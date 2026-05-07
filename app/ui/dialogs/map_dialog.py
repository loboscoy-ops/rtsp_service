from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from app.database.models import CameraModel

# WebEngine ловим максимально широко: на разных сборках PySide6/macOS
# импорт может падать не только ImportError, но и OSError/RuntimeError
# (отсутствуют фреймворки, нет sandbox-helper'а и т.п.).
try:
    from PySide6.QtWebEngineCore import QWebEnginePage  # type: ignore
    from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore

    _WEBENGINE = True
except Exception:
    QWebEnginePage = None  # type: ignore[misc, assignment]
    QWebEngineView = None  # type: ignore[misc, assignment]
    _WEBENGINE = False


# Кастомная схема, которой мы помечаем «открой камеру id=N».
# Перехватывается в _MapPage.acceptNavigationRequest, поэтому браузер
# никуда не уходит — мы только эмитим сигнал в Python.
_OPEN_SCHEME = "rtsp-app"
_OPEN_HOST = "open"


def _markers_payload(cameras: list["CameraModel"]) -> list[dict]:
    from app.utils.gps_parse import parse_lat_lon

    out: list[dict] = []
    for idx, cam in enumerate(cameras, start=1):
        ll = parse_lat_lon(cam.gps_coords)
        if ll is None:
            continue
        lat, lon = ll
        out.append(
            {
                "id": int(cam.id),
                "num": idx,
                "lat": lat,
                "lon": lon,
                "name": cam.camera_name or "",
                "status": cam.status or "unknown",
                "object": cam.object_name or "",
            }
        )
    return out


def _fallback_html(markers: list[dict], skipped: int) -> str:
    lines = [
        "<h3>Карта недоступна (нет Qt WebEngine)</h3>",
        "<p>Установите полный PySide6 с WebEngine или откройте координаты по ссылкам:</p>",
        "<ul>",
    ]
    for m in markers:
        lat, lon = m["lat"], m["lon"]
        name = html.escape(m["name"])
        url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}"
        lines.append(
            f'<li>№{m["num"]} — {name}: '
            f'<a href="{url}">{lat:.6f}, {lon:.6f}</a></li>'
        )
    lines.append("</ul>")
    if skipped:
        lines.append(f"<p>Камер без координат: {skipped}</p>")
    return "\n".join(lines)


def _leaflet_html(markers: list[dict]) -> str:
    # Вставляем как JS-литерал; \u003c — чтобы случайный "</" в данных не закрыл <script>
    markers_json = json.dumps(markers, ensure_ascii=False)
    markers_json = markers_json.replace("<", "\\u003c")
    open_link = f"{_OPEN_SCHEME}://{_OPEN_HOST}/"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html, body, #map {{ height: 100%; margin: 0; padding: 0; }}
.cam-num {{
  width: 26px; height: 26px; border-radius: 50%;
  border: 2px solid #fff; color: #fff; font: bold 12px/22px system-ui, sans-serif;
  text-align: center; line-height: 22px;
  box-shadow: 0 1px 4px rgba(0,0,0,.45);
  cursor: pointer;
}}
.cam-online {{ background: #2d9d5f; }}
.cam-offline {{ background: #c43c3c; }}
.cam-unknown {{ background: #6b6b6b; }}
.cam-popup .open-link {{
  display: inline-block;
  margin-top: 6px;
  padding: 4px 10px;
  background: #1f9d55;
  color: #fff;
  border-radius: 4px;
  text-decoration: none;
  font-weight: 600;
}}
.cam-popup .open-link:hover {{ background: #2bb573; }}
.cam-popup .hint {{
  display: block;
  margin-top: 4px;
  color: #666;
  font-size: 11px;
}}
</style>
</head><body>
<div id="map"></div>
<script>
function escHtml(s) {{
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}}
function openLink(id) {{
  return {json.dumps(open_link)} + encodeURIComponent(id);
}}
const markers = {markers_json};
const map = L.map('map', {{ zoomControl: true }});
map.attributionControl.setPrefix(
  '<span aria-hidden="true">\\uD83C\\uDDF7\\uD83C\\uDDFA</span> '
  + '<a href="https://leafletjs.com" target="_blank">Leaflet</a>'
);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap'
}}).addTo(map);
if (markers.length === 0) {{
  map.setView([55.75, 37.62], 10);
}} else {{
  const layers = [];
  for (const m of markers) {{
    const cls = m.status === 'online' ? 'cam-online'
      : (m.status === 'offline' ? 'cam-offline' : 'cam-unknown');
    const icon = L.divIcon({{
      className: '',
      html: '<div class="cam-num ' + cls + '" title="Клик: попап. Двойной клик: открыть в ffplay">' + m.num + '</div>',
      iconSize: [26, 26],
      iconAnchor: [13, 13]
    }});
    const marker = L.marker([m.lat, m.lon], {{ icon: icon, title: '№' + m.num + ' — ' + m.name }});
    marker.bindPopup(
      '<div class="cam-popup">'
      + '<b>№' + m.num + '</b> — ' + escHtml(m.name) + '<br/>'
      + escHtml(m.object) + '<br/>'
      + '<small>' + escHtml(m.status) + '</small><br/>'
      + '<a class="open-link" href="' + openLink(m.id) + '">▶ Открыть в ffplay</a>'
      + '<span class="hint">Двойной клик по маркеру — то же самое</span>'
      + '</div>'
    );
    marker.on('dblclick', function (ev) {{
      L.DomEvent.stopPropagation(ev);
      window.location.href = openLink(m.id);
    }});
    marker.addTo(map);
    layers.push(marker);
  }}
  const g = L.featureGroup(layers);
  map.fitBounds(g.getBounds().pad(0.15));
}}
</script>
</body></html>
"""


if _WEBENGINE and QWebEnginePage is not None:

    class _MapPage(QWebEnginePage):  # type: ignore[misc]
        """Перехватывает переходы на rtsp-app://open/<id> и эмитит сигнал в Python."""

        camera_open_requested = Signal(int)

        def acceptNavigationRequest(self, url: QUrl, _type, _is_main_frame) -> bool:  # noqa: D401
            if url.scheme() == _OPEN_SCHEME:
                # Берём последний непустой сегмент пути — это id камеры.
                raw = (url.path() or "").strip("/").split("/")[-1]
                try:
                    cam_id = int(raw)
                except (TypeError, ValueError):
                    return False
                self.camera_open_requested.emit(cam_id)
                return False
            return super().acceptNavigationRequest(url, _type, _is_main_frame)
else:
    _MapPage = None  # type: ignore[misc, assignment]


class MapDialog(QDialog):
    """Окно с картой OSM и маркерами камер по gps_coords.

    Сигналы:
      open_camera_requested(int) — пользователь хочет открыть стрим камеры.
    """

    open_camera_requested = Signal(int)

    def __init__(self, cameras: list["CameraModel"], *, object_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Карта — {object_label}")
        self.resize(720, 520)

        markers = _markers_payload(cameras)
        skipped = len(cameras) - len(markers)

        layout = QVBoxLayout(self)
        hint = QLabel(
            f"Камер на карте: {len(markers)} из {len(cameras)}"
            + (f" (без координат: {skipped})" if skipped else "")
        )
        layout.addWidget(hint)

        if not markers:
            layout.addWidget(
                QLabel(
                    "Ни у одной камеры в текущем списке нет распознаваемых координат.\n"
                    "Формат: широта, долгота (например 55.7522, 37.6156)."
                )
            )
            close_btn = QPushButton("Закрыть")
            close_btn.clicked.connect(self.reject)
            layout.addWidget(close_btn)
            self._install_escape_shortcut()
            return

        if _WEBENGINE and QWebEngineView is not None and _MapPage is not None:
            view = QWebEngineView()
            page = _MapPage(view)
            page.camera_open_requested.connect(self.open_camera_requested)
            view.setPage(page)
            view.setHtml(_leaflet_html(markers), QUrl("https://local.map/"))
            layout.addWidget(view)
            self._view = view  # держим ссылку, иначе сборщик может прибить
        else:
            QMessageBox.information(
                self,
                "Карта",
                "Компонент Qt WebEngine недоступен в этой сборке PySide6.\n"
                "Ниже — ссылки на карту в браузере.",
            )
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHtml(_fallback_html(markers, skipped))
            layout.addWidget(browser)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._install_escape_shortcut()

    def _install_escape_shortcut(self) -> None:
        # QWebEngineView перехватывает фокус и Esc туда не доходит как QKeyEvent в QDialog.
        # WindowShortcut гарантирует доставку именно в это окно.
        sc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(self.reject)
