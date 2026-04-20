from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
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

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView

    _WEBENGINE = True
except ImportError:
    QWebEngineView = None  # type: ignore[misc, assignment]
    _WEBENGINE = False


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
}}
.cam-online {{ background: #2d9d5f; }}
.cam-offline {{ background: #c43c3c; }}
.cam-unknown {{ background: #6b6b6b; }}
</style>
</head><body>
<div id="map"></div>
<script>
function escHtml(s) {{
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
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
      html: '<div class="cam-num ' + cls + '">' + m.num + '</div>',
      iconSize: [26, 26],
      iconAnchor: [13, 13]
    }});
    const marker = L.marker([m.lat, m.lon], {{ icon: icon }});
    marker.bindPopup(
      '<b>№' + m.num + '</b> — ' + escHtml(m.name) + '<br/>'
      + escHtml(m.object) + '<br/><small>' + escHtml(m.status) + '</small>'
    );
    marker.addTo(map);
    layers.push(marker);
  }}
  const g = L.featureGroup(layers);
  map.fitBounds(g.getBounds().pad(0.15));
}}
</script>
</body></html>
"""


class MapDialog(QDialog):
    """Небольшое окно с картой OSM и маркерами камер по gps_coords."""

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
            close_btn.clicked.connect(self.accept)
            layout.addWidget(close_btn)
            return

        if _WEBENGINE and QWebEngineView is not None:
            view = QWebEngineView()
            view.setHtml(_leaflet_html(markers), QUrl("https://local.map/"))
            layout.addWidget(view)
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
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)
