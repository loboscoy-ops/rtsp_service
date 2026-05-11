"""Встраиваемый виджет карты камер (Leaflet + OSM)."""
from __future__ import annotations

import html
import json
import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QLabel, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from app.database.models import CameraModel

# WebEngine-импорт максимально устойчив: на разных сборках PySide6/macOS
# может падать не только ImportError, но и OSError/RuntimeError.
try:
    from PySide6.QtWebEngineCore import QWebEnginePage  # type: ignore
    from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore

    _WEBENGINE = True
except Exception:
    QWebEnginePage = None  # type: ignore[misc, assignment]
    QWebEngineView = None  # type: ignore[misc, assignment]
    _WEBENGINE = False


_log = logging.getLogger(__name__)


# Кастомная схема, которой мы помечаем «открой камеру id=N».
# Перехватывается в MapPage.acceptNavigationRequest, поэтому браузер
# никуда не уходит — мы только эмитим сигнал в Python.
OPEN_SCHEME = "rtsp-app"
OPEN_HOST = "open"


def markers_payload(cameras: list["CameraModel"]) -> list[dict]:
    """Список словарей с координатами/статусами для отрисовки на карте."""
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


def fallback_html(markers: list[dict], skipped: int) -> str:
    """HTML-фолбэк, когда Qt WebEngine недоступен."""
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


def leaflet_html(markers: list[dict]) -> str:
    """HTML страницы с интерактивной картой Leaflet.

    Скрипт регистрирует две глобальные функции:
      - ``updateCameraStatus(id, status)`` — поменять цвет маркера без перерисовки;
      - ``fitAllMarkers()`` — снова уместить все маркеры в видимую область.
    """
    # Вставляем как JS-литерал; \u003c — чтобы случайный "</" в данных не закрыл <script>.
    markers_json = json.dumps(markers, ensure_ascii=False)
    markers_json = markers_json.replace("<", "\\u003c")
    open_link = f"{OPEN_SCHEME}://{OPEN_HOST}/"

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
function statusClass(s) {{
  if (s === 'online') return 'cam-online';
  if (s === 'offline') return 'cam-offline';
  return 'cam-unknown';
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

const markerById = {{}};
const featureGroup = L.featureGroup().addTo(map);

function buildIcon(num, status) {{
  return L.divIcon({{
    className: '',
    html: '<div class="cam-num ' + statusClass(status)
        + '" title="Клик: попап. Двойной клик: открыть в ffplay">' + num + '</div>',
    iconSize: [26, 26],
    iconAnchor: [13, 13]
  }});
}}

function buildPopup(m) {{
  return '<div class="cam-popup">'
       + '<b>№' + m.num + '</b> — ' + escHtml(m.name) + '<br/>'
       + escHtml(m.object) + '<br/>'
       + '<small>' + escHtml(m.status) + '</small><br/>'
       + '<a class="open-link" href="' + openLink(m.id) + '">▶ Открыть в ffplay</a>'
       + '<span class="hint">Двойной клик по маркеру — то же самое</span>'
       + '</div>';
}}

function addMarker(m) {{
  const marker = L.marker([m.lat, m.lon], {{
    icon: buildIcon(m.num, m.status),
    title: '№' + m.num + ' — ' + m.name
  }});
  marker._meta = m;
  marker.bindPopup(buildPopup(m));
  marker.on('dblclick', function (ev) {{
    L.DomEvent.stopPropagation(ev);
    window.location.href = openLink(m.id);
  }});
  marker.addTo(featureGroup);
  markerById[m.id] = marker;
  return marker;
}}

window.fitAllMarkers = function () {{
  if (!markers.length) {{
    map.setView([55.75, 37.62], 10);
    return;
  }}
  const bounds = featureGroup.getBounds();
  if (bounds.isValid()) {{
    map.fitBounds(bounds.pad(0.15));
  }}
}};

window.updateCameraStatus = function (id, status) {{
  const marker = markerById[id];
  if (!marker) return;
  marker._meta.status = status;
  marker.setIcon(buildIcon(marker._meta.num, status));
  marker.setPopupContent(buildPopup(marker._meta));
}};

for (const m of markers) {{
  addMarker(m);
}}
window.fitAllMarkers();
</script>
</body></html>
"""


if _WEBENGINE and QWebEnginePage is not None:

    class MapPage(QWebEnginePage):  # type: ignore[misc]
        """Перехватывает переходы на rtsp-app://open/<id> и эмитит сигнал в Python."""

        camera_open_requested = Signal(int)

        def acceptNavigationRequest(self, url: QUrl, _type, _is_main_frame) -> bool:  # noqa: D401
            if url.scheme() == OPEN_SCHEME:
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
    MapPage = None  # type: ignore[misc, assignment]


def _fingerprint(cameras: list["CameraModel"]) -> tuple:
    """Хеш по «структуре» (id+координаты+имя+объект).

    Если изменился только статус — fingerprint совпадает и мы можем
    просто обновить иконки через JS, не теряя текущий зум/панораму.
    """
    return tuple(
        (
            int(cam.id),
            (cam.gps_coords or "").strip(),
            cam.camera_name or "",
            cam.object_name or "",
        )
        for cam in cameras
    )


class CameraMapView(QWidget):
    """Встраиваемая карта камер с тем же UX, что и :class:`MapDialog`.

    Сигналы:
      open_camera_requested(int) — пользователь хочет открыть стрим камеры.
    """

    open_camera_requested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._view: Optional[QWebEngineView] = None
        self._page: Optional[MapPage] = None  # type: ignore[assignment]
        self._loaded = False
        self._pending_status_updates: list[tuple[int, str]] = []
        self._last_fingerprint: tuple = ()
        self._known_ids: set[int] = set()

        self._info_label = QLabel("Нет камер с распознаваемыми координатами.")
        self._info_label.setWordWrap(True)
        self._stack.addWidget(self._info_label)

        if _WEBENGINE and QWebEngineView is not None and MapPage is not None:
            self._view = QWebEngineView(self)
            self._page = MapPage(self._view)
            self._page.camera_open_requested.connect(self.open_camera_requested)
            self._view.setPage(self._page)
            self._view.loadFinished.connect(self._on_load_finished)
            self._stack.addWidget(self._view)
        else:
            self._fallback = QTextBrowser(self)
            self._fallback.setOpenExternalLinks(True)
            self._fallback.setHtml(
                "<p>Карта недоступна: Qt WebEngine не установлен.</p>"
            )
            self._stack.addWidget(self._fallback)

        # Стартовый экран — заглушка.
        self._stack.setCurrentWidget(self._info_label)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cameras(self, cameras: list["CameraModel"]) -> None:
        """Полное обновление набора камер.

        Если поменялся только статус (структура списка не изменилась),
        точечно патчим иконки — зум/панорама сохраняются.
        """
        markers = markers_payload(cameras)
        new_fp = _fingerprint(cameras)

        if new_fp == self._last_fingerprint and self._known_ids:
            for m in markers:
                self.update_camera_status(m["id"], m["status"])
            return

        self._last_fingerprint = new_fp
        self._known_ids = {m["id"] for m in markers}

        if not markers:
            skipped = len(cameras)
            self._info_label.setText(
                "Нет камер с распознаваемыми координатами"
                + (f" (всего камер: {skipped})." if skipped else ".")
                + "\nФормат: широта, долгота (например 55.7522, 37.6156)."
            )
            self._stack.setCurrentWidget(self._info_label)
            self._loaded = False
            self._pending_status_updates.clear()
            return

        if self._view is None:
            # Фолбэк без WebEngine.
            skipped = len(cameras) - len(markers)
            if hasattr(self, "_fallback"):
                self._fallback.setHtml(fallback_html(markers, skipped))
                self._stack.setCurrentWidget(self._fallback)
            return

        self._loaded = False
        self._pending_status_updates.clear()
        self._view.setHtml(leaflet_html(markers), QUrl("https://local.map/"))
        self._stack.setCurrentWidget(self._view)

    def update_camera_status(self, camera_id: int, status: str) -> None:
        """Поменять цвет маркера одной камеры без перерисовки карты."""
        if self._view is None or self._page is None:
            return
        if camera_id not in self._known_ids:
            return
        if not self._loaded:
            self._pending_status_updates.append((camera_id, status))
            return
        self._page.runJavaScript(
            f"window.updateCameraStatus && updateCameraStatus("
            f"{int(camera_id)}, {json.dumps(status)});"
        )

    def fit_all(self) -> None:
        """Заново уместить все маркеры в видимую область."""
        if self._view is None or self._page is None or not self._loaded:
            return
        self._page.runJavaScript("window.fitAllMarkers && fitAllMarkers();")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _on_load_finished(self, ok: bool) -> None:
        self._loaded = bool(ok)
        if not ok:
            _log.warning("CameraMapView: страница карты загрузилась с ошибкой")
            return
        # Пробросим накопленные обновления статусов.
        pending = self._pending_status_updates
        self._pending_status_updates = []
        for cam_id, status in pending:
            self.update_camera_status(cam_id, status)
