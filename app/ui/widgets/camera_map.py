"""Встраиваемый виджет карты камер (Leaflet + OSM)."""
from __future__ import annotations

import html
import json
import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QLabel, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from app.database.models import CameraModel, ObjectModel

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


# Кастомная схема для коммуникации Leaflet → Python:
#   rtsp-app://open/<camera_id>    — попросить открыть стрим камеры
#   rtsp-app://object/<object_id>  — попросить перейти к объекту в таблице
OPEN_SCHEME = "rtsp-app"
OPEN_HOST = "open"
OBJECT_HOST = "object"


def markers_payload(cameras: list["CameraModel"]) -> list[dict]:
    """Маркеры по камерам (для основного раздела «Карта»)."""
    from app.utils.gps_parse import parse_lat_lon

    out: list[dict] = []
    for idx, cam in enumerate(cameras, start=1):
        ll = parse_lat_lon(cam.gps_coords)
        if ll is None:
            continue
        lat, lon = ll
        out.append(
            {
                "kind": "camera",
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


def markers_payload_objects(
    objects: list["ObjectModel"],
    cameras: list["CameraModel"],
) -> list[dict]:
    """Маркеры по объектам: один маркер на площадку, в кружке — кол-во камер.

    Координаты площадки = координаты первой камеры с валидным GPS.
    Статус площадки = «online», если все камеры online; «offline», если
    среди offline их большинство; иначе «unknown».
    УИН площадки = первый непустой UIN среди её камер.
    """
    from app.utils.gps_parse import parse_lat_lon

    by_object: dict[int, list["CameraModel"]] = {}
    for c in cameras:
        by_object.setdefault(int(c.object_id), []).append(c)

    out: list[dict] = []
    for obj in objects:
        cams = by_object.get(int(obj.id), [])
        coords: tuple[float, float] | None = None
        for c in cams:
            ll = parse_lat_lon(c.gps_coords)
            if ll is not None:
                coords = ll
                break
        if coords is None:
            continue

        online = sum(1 for c in cams if c.status == "online")
        offline = sum(1 for c in cams if c.status == "offline")
        unknown = len(cams) - online - offline
        if offline == 0 and unknown == 0:
            status = "online"
        elif offline >= max(online, unknown):
            status = "offline"
        else:
            status = "unknown"

        uin = ""
        for c in cams:
            if (c.uin or "").strip():
                uin = c.uin.strip()
                break

        out.append(
            {
                "kind": "object",
                "id": int(obj.id),
                "num": len(cams),
                "lat": coords[0],
                "lon": coords[1],
                "name": obj.name or "",
                "uin": uin,
                "status": status,
                "online": online,
                "offline": offline,
                "unknown": unknown,
            }
        )
    return out


def fallback_html(markers: list[dict], skipped: int) -> str:
    """HTML-фолбэк, когда Qt WebEngine недоступен."""
    lines = [
        "<div style='background:#ffffff;color:#1f2330;padding:16px;font-family:system-ui,sans-serif;'>",
        "<h3 style='margin-top:0;'>Карта недоступна (нет Qt WebEngine)</h3>",
        "<p style='color:#6b7280;'>Установите полный PySide6 с WebEngine или откройте координаты по ссылкам:</p>",
        "<ul style='line-height:1.6;'>",
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
        lines.append(f"<p style='color:#6b7280;'>Камер без координат: {skipped}</p>")
    lines.append("</div>")
    return "\n".join(lines)


def leaflet_html(
    markers: list[dict],
    *,
    dark: bool = False,
    cluster: bool = False,
    cluster_radius: int = 60,
) -> str:
    """HTML страницы с интерактивной картой Leaflet.

    Скрипт регистрирует две глобальные функции:
      - ``updateCameraStatus(id, status)`` — поменять цвет маркера без перерисовки;
      - ``fitAllMarkers()`` — снова уместить все маркеры в видимую область.

    При ``cluster=True`` маркеры группируются через Leaflet.markercluster.
    ``cluster_radius`` задаёт расстояние слипания в пикселях: для основной
    карты (5к камер) ставим ~60, для дашборда — маленькое значение, чтобы
    группировались только реально соприкасающиеся маркеры.
    """
    # Вставляем как JS-литерал; \u003c — чтобы случайный "</" в данных не закрыл <script>.
    markers_json = json.dumps(markers, ensure_ascii=False)
    markers_json = markers_json.replace("<", "\\u003c")
    open_link = f"{OPEN_SCHEME}://{OPEN_HOST}/"
    object_link = f"{OPEN_SCHEME}://{OBJECT_HOST}/"

    # Leaflet ожидает шаблон URL вида {z}/{x}/{y}. Здесь — обычные строки,
    # они подставляются в f-string как значения «как есть», поэтому
    # фигурные скобки нельзя экранировать (никакого {{...}}).
    if dark:
        body_bg = "#12141a"
        container_bg = "#0f1218"
        marker_border = "#0f1218"
        popup_bg = "#1a1e28"
        popup_fg = "#e8eaed"
        popup_border = "#2d3544"
        hint_fg = "#8b929e"
        tile_url = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        tile_attr = (
            "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a>"
            " &copy; <a href=\"https://carto.com/attributions\">CARTO</a>"
        )
        tile_subdomains = "abcd"
    else:
        body_bg = "#ffffff"
        container_bg = "#f5f5f5"
        marker_border = "#ffffff"
        popup_bg = "#ffffff"
        popup_fg = "#1f2330"
        popup_border = "#d6d8dc"
        hint_fg = "#6b7280"
        tile_url = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        tile_attr = "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a>"
        tile_subdomains = "abc"

    cluster_css = (
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>\n'
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>'
        if cluster else ""
    )
    cluster_js = (
        '<script src="https://cdn.jsdelivr.net/npm/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>'
        if cluster else ""
    )
    cluster_flag = "true" if cluster else "false"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"/>
{cluster_css}
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
{cluster_js}
<style>
html, body, #map {{ height: 100%; margin: 0; padding: 0; background: {body_bg}; }}
.leaflet-container {{ background: {container_bg}; }}
.cam-num {{
  width: 26px; height: 26px; border-radius: 50%;
  border: 2px solid {marker_border}; color: #ffffff; font: bold 12px/22px system-ui, sans-serif;
  text-align: center; line-height: 22px;
  box-shadow: 0 1px 4px rgba(0,0,0,.45);
  cursor: pointer;
}}
.cam-online {{ background: #2d9d5f; }}
.cam-offline {{ background: #c43c3c; }}
.cam-unknown {{ background: #6b7280; }}
.cam-object {{ font-size: 13px; box-shadow: 0 2px 6px rgba(0,0,0,.35); }}
.cam-popup .leaflet-popup-content-wrapper {{
  background: {popup_bg};
  color: {popup_fg};
  border-radius: 8px;
  border: 1px solid {popup_border};
}}
.cam-popup .leaflet-popup-tip {{ background: {popup_bg}; }}
.cam-popup .open-link {{
  display: inline-block;
  margin-top: 6px;
  padding: 6px 12px;
  background: #1f9d55;
  color: #ffffff;
  border-radius: 6px;
  text-decoration: none;
  font-weight: 600;
}}
.cam-popup .open-link:hover {{ background: #2bb573; }}
.cam-popup .hint {{
  display: block;
  margin-top: 4px;
  color: {hint_fg};
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
function objectLink(id) {{
  return {json.dumps(object_link)} + encodeURIComponent(id);
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
L.tileLayer('{tile_url}', {{
  maxZoom: 19,
  subdomains: '{tile_subdomains}',
  attribution: '{tile_attr}'
}}).addTo(map);

const markerById = {{}};
const useCluster = {cluster_flag};
const featureGroup = useCluster
  ? L.markerClusterGroup({{
      chunkedLoading: true,
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: true,
      disableClusteringAtZoom: 17,
      maxClusterRadius: {cluster_radius}
    }}).addTo(map)
  : L.featureGroup().addTo(map);

function buildIcon(num, status, kind) {{
  const size = (kind === 'object') ? 36 : 26;
  const inner = (kind === 'object') ? 32 : 22;
  const cls = (kind === 'object') ? 'cam-num cam-object' : 'cam-num';
  const tip = (kind === 'object')
    ? 'Площадка: ' + num + ' камер'
    : 'Клик: попап. Двойной клик: запустить RTSP';
  return L.divIcon({{
    className: '',
    html: '<div class="' + cls + ' ' + statusClass(status)
        + '" style="width:' + size + 'px;height:' + size + 'px;line-height:'
        + inner + 'px;" title="' + tip + '">' + num + '</div>',
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2]
  }});
}}

function buildPopup(m) {{
  if (m.kind === 'object') {{
    const uin = m.uin
      ? '<small>УИН: ' + escHtml(m.uin) + '</small><br/>'
      : '<small style="color:#9aa1ab;">УИН не указан</small><br/>';
    return '<div class="cam-popup">'
         + '<b>' + escHtml(m.name) + '</b><br/>'
         + uin
         + '<small>Камер: ' + m.num + '</small><br/>'
         + '<a class="open-link" href="' + objectLink(m.id) + '">'
         + 'Перейти в таблицу объекта</a>'
         + '</div>';
  }}
  return '<div class="cam-popup">'
       + '<b>№' + m.num + '</b> — ' + escHtml(m.name) + '<br/>'
       + escHtml(m.object) + '<br/>'
       + '<small>' + escHtml(m.status) + '</small><br/>'
       + '<a class="open-link" href="' + openLink(m.id) + '">Запустить RTSP</a>'
       + '<span class="hint">Двойной клик по маркеру — то же самое</span>'
       + '</div>';
}}

function addMarker(m) {{
  const marker = L.marker([m.lat, m.lon], {{
    icon: buildIcon(m.num, m.status, m.kind),
    title: (m.kind === 'object')
      ? m.name + ' — камер: ' + m.num
      : '№' + m.num + ' — ' + m.name
  }});
  marker._meta = m;
  marker.bindPopup(buildPopup(m), {{ className: 'cam-popup' }});
  marker.on('dblclick', function (ev) {{
    L.DomEvent.stopPropagation(ev);
    window.location.href = (m.kind === 'object') ? objectLink(m.id) : openLink(m.id);
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
  marker.setIcon(buildIcon(marker._meta.num, status, marker._meta.kind));
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
        """Перехватывает rtsp-app:// и роутит:
        rtsp-app://open/<cam_id>     → camera_open_requested(cam_id)
        rtsp-app://object/<obj_id>   → object_open_requested(obj_id)
        """

        camera_open_requested = Signal(int)
        object_open_requested = Signal(int)

        def acceptNavigationRequest(self, url: QUrl, _type, _is_main_frame) -> bool:  # noqa: D401
            if url.scheme() == OPEN_SCHEME:
                raw = (url.path() or "").strip("/").split("/")[-1]
                try:
                    target_id = int(raw)
                except (TypeError, ValueError):
                    return False
                if url.host() == OBJECT_HOST:
                    self.object_open_requested.emit(target_id)
                else:
                    self.camera_open_requested.emit(target_id)
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
    """Встраиваемая карта.

    Поддерживает два набора маркеров:
      * `set_cameras(...)` — по одной точке на камеру (раздел «Камеры»);
      * `set_objects(...)` — по одной точке на площадку, в кружке —
        количество камер (раздел «Дашборд»).

    Сигналы:
      open_camera_requested(int) — пользователь хочет открыть стрим камеры.
      open_object_requested(int) — пользователь хочет перейти к площадке.
    """

    open_camera_requested = Signal(int)
    open_object_requested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None, *, dark: bool = False):
        super().__init__(parent)
        self._dark = dark
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
        self._mode: str = "cameras"  # "cameras" | "objects"

        self._info_label = QLabel("Нет камер с распознаваемыми координатами.")
        self._info_label.setWordWrap(True)
        self._stack.addWidget(self._info_label)

        if _WEBENGINE and QWebEngineView is not None and MapPage is not None:
            self._view = QWebEngineView(self)
            self._page = MapPage(self._view)
            self._page.camera_open_requested.connect(self.open_camera_requested)
            self._page.object_open_requested.connect(self.open_object_requested)
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
        """Полное обновление набора камер (раздел «Карта»).

        Если поменялся только статус (структура списка не изменилась),
        точечно патчим иконки — зум/панорама сохраняются.
        """
        markers = markers_payload(cameras)
        new_fp = ("cameras", _fingerprint(cameras))

        if (
            self._mode == "cameras"
            and new_fp == self._last_fingerprint
            and self._known_ids
        ):
            for m in markers:
                self.update_camera_status(m["id"], m["status"])
            return

        self._mode = "cameras"
        self._last_fingerprint = new_fp
        self._known_ids = {m["id"] for m in markers}
        self._render(markers, total=len(cameras))

    def set_objects(
        self,
        objects: list["ObjectModel"],
        cameras: list["CameraModel"],
    ) -> None:
        """Маркеры по площадкам (раздел «Дашборд»)."""
        markers = markers_payload_objects(objects, cameras)
        new_fp = (
            "objects",
            tuple(
                (m["id"], m["lat"], m["lon"], m["num"], m["name"], m.get("uin", ""))
                for m in markers
            ),
        )
        if (
            self._mode == "objects"
            and new_fp == self._last_fingerprint
            and self._known_ids
        ):
            for m in markers:
                self.update_camera_status(m["id"], m["status"])
            return

        self._mode = "objects"
        self._last_fingerprint = new_fp
        self._known_ids = {m["id"] for m in markers}
        self._render(markers, total=len(objects))

    def _render(self, markers: list[dict], *, total: int) -> None:
        if not markers:
            self._info_label.setText(
                "Нет точек с распознаваемыми координатами"
                + (f" (всего: {total})." if total else ".")
                + "\nФормат GPS: широта, долгота (например 55.7522, 37.6156)."
            )
            self._stack.setCurrentWidget(self._info_label)
            self._loaded = False
            self._pending_status_updates.clear()
            return

        if self._view is None:
            skipped = max(0, total - len(markers))
            if hasattr(self, "_fallback"):
                self._fallback.setHtml(fallback_html(markers, skipped))
                self._stack.setCurrentWidget(self._fallback)
            return

        self._loaded = False
        self._pending_status_updates.clear()
        # «Камеры»: радиус 60 px — на 5к точках клики попадают в кластеры.
        # «Дашборд» (объекты): радиус 18 px — кластер только когда маркеры
        # реально соприкасаются краями (одна площадка ровно над другой).
        cluster_radius = 60 if self._mode == "cameras" else 18
        self._view.setHtml(
            leaflet_html(
                markers,
                dark=self._dark,
                cluster=True,
                cluster_radius=cluster_radius,
            ),
            QUrl("https://local.map/"),
        )
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

    def prepare_shutdown(self) -> None:
        """Уменьшить краши Qt WebEngine при закрытии главного окна."""
        for sig in (self.open_camera_requested, self.open_object_requested):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        if self._page is not None:
            for sig_name in ("camera_open_requested", "object_open_requested"):
                try:
                    getattr(self._page, sig_name).disconnect()
                except (RuntimeError, TypeError, AttributeError):
                    pass
        if self._view is None:
            self._loaded = False
            return
        try:
            self._view.stop()
            self._view.setHtml("", QUrl("about:blank"))
        except Exception:
            pass
        self._loaded = False

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
