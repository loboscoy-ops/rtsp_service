"""Встраиваемый виджет карты камер (Leaflet + OSM)."""
from __future__ import annotations

import html
import json
import logging
import math
from collections import defaultdict
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


def _jitter_duplicate_marker_positions(markers: list[dict]) -> None:
    """Сдвигаем маркеры с одинаковыми координатами по маленькому кругу.

    Иначе десяток камер с одним GPS лежит одним «бубликом», на карте видно
    меньше точек, чем строк в таблице — пользователь воспринимает это как
    расхождение количества.
    """
    if len(markers) <= 1:
        return
    buckets: dict[tuple[float, float], list[dict]] = defaultdict(list)
    for m in markers:
        key = (round(m["lat"], 6), round(m["lon"], 6))
        buckets[key].append(m)
    # ~3 м шаг по экватору; достаточно, чтобы кружки перестали совпадать.
    radius_deg = 2.8e-5
    for items in buckets.values():
        if len(items) <= 1:
            continue
        base_lat = items[0]["lat"]
        base_lon = items[0]["lon"]
        n = len(items)
        for i, m in enumerate(items):
            ang = (2 * math.pi * i) / n
            m["lat"] = base_lat + radius_deg * math.sin(ang)
            m["lon"] = base_lon + radius_deg * math.cos(ang)


def markers_payload(cameras: list["CameraModel"]) -> list[dict]:
    """Маркеры по камерам (для основного раздела «Карта» и дашборда)."""
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
                "object_id": int(cam.object_id),
                "num": idx,
                "lat": lat,
                "lon": lon,
                "name": cam.camera_name or "",
                "status": cam.status or "unknown",
                "object": cam.object_name or "",
                "uin": (cam.uin or "").strip(),
            }
        )
    _jitter_duplicate_marker_positions(out)
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
    dashboard_hover: bool = False,
    map_overlay: dict | None = None,
) -> str:
    """HTML страницы с интерактивной картой Leaflet.

    Скрипт регистрирует две глобальные функции:
      - ``updateCameraStatus(id, status)`` — поменять цвет маркера без перерисовки;
      - ``fitAllMarkers()`` — снова уместить все маркеры в видимую область.

    При ``cluster=True`` маркеры группируются через Leaflet.markercluster.

    ``dashboard_hover=True`` — на карте дашборда при наведении показывается
    плашка с именем объекта и УИН (без кнопок); кластеры окрашиваются по доле
    online; по клику на кластер карта масштабируется под его границы.

    ``map_overlay`` — словарь {{mapped, total, no_gps}} для подписи
    «На карте точек: … из …».
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
    dash_hover_flag = "true" if dashboard_hover else "false"
    overlay_json = json.dumps(map_overlay, ensure_ascii=False) if map_overlay is not None else "null"

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
  margin-right: 6px;
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
.leaflet-tooltip.cam-hover-tip {{
  padding: 0;
  border: 1px solid {popup_border};
  border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0,0,0,.22);
  background: {popup_bg};
  color: {popup_fg};
  font-family: system-ui, sans-serif;
}}
.leaflet-tooltip.cam-hover-tip .leaflet-tooltip-content {{
  margin: 0;
}}
.cam-hover {{
  padding: 10px 12px;
  min-width: 200px;
  max-width: 280px;
}}
.cam-hover .hover-title {{
  font-weight: 700;
  font-size: 14px;
  margin-bottom: 4px;
}}
.cam-hover .hover-uin {{
  font-size: 12px;
  color: {hint_fg};
  margin-bottom: 8px;
}}
.cam-hover .hover-uin.muted {{
  font-style: italic;
}}
.cam-hover-stack {{
  padding: 10px 12px;
  min-width: 200px;
  max-width: 300px;
}}
.cam-hover-stack .cam-hover {{
  padding: 8px 0;
  min-width: auto;
  max-width: none;
}}
.cam-hover-stack .cam-hover:not(:first-child) {{
  border-top: 1px solid {popup_border};
}}
/* Кластеры карты дашборда: цвет по доле online среди маркеров */
.marker-cluster.dash-cluster-green {{ background-color: rgba(45, 157, 95, 0.42); }}
.marker-cluster.dash-cluster-green div {{
  background-color: #2d9d5f;
  color: #ffffff;
}}
.marker-cluster.dash-cluster-yellow {{ background-color: rgba(234, 179, 8, 0.42); }}
.marker-cluster.dash-cluster-yellow div {{
  background-color: #ca8a04;
  color: #111111;
}}
.marker-cluster.dash-cluster-red {{ background-color: rgba(196, 60, 60, 0.42); }}
.marker-cluster.dash-cluster-red div {{
  background-color: #c43c3c;
  color: #ffffff;
}}
.marker-cluster.dash-cluster-grey {{ background-color: rgba(107, 114, 128, 0.42); }}
.marker-cluster.dash-cluster-grey div {{
  background-color: #6b7280;
  color: #ffffff;
}}
.map-stats-overlay {{
  position: absolute;
  top: 10px;
  right: 52px;
  z-index: 1000;
  max-width: 280px;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.35;
  pointer-events: none;
  border: 1px solid {popup_border};
  background: {popup_bg};
  color: {popup_fg};
  opacity: 0.96;
  box-shadow: 0 1px 6px rgba(0,0,0,.12);
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
const dashHover = {dash_hover_flag};

function buildHoverPanel(objName, uin, _objectId) {{
  const title = '<div class="hover-title">' + escHtml(objName || 'Объект') + '</div>';
  const uinDiv = uin
    ? '<div class="hover-uin">УИН: ' + escHtml(uin) + '</div>'
    : '<div class="hover-uin muted">УИН не указан</div>';
  return '<div class="cam-hover">' + title + uinDiv + '</div>';
}}

function attachDashboardHover(marker, m) {{
  if (!dashHover || m.kind !== 'camera' || typeof m.object_id !== 'number') return;
  marker.bindTooltip(
    buildHoverPanel(m.object, m.uin || '', m.object_id),
    {{
      sticky: true,
      direction: 'auto',
      opacity: 1,
      interactive: false,
      className: 'cam-hover-tip'
    }}
  );
}}

function bindClusterDashboardHover(layer) {{
  if (!dashHover || !useCluster) return;
  layer.on('clustermouseover', function (ev) {{
    const cluster = ev.layer;
    const kids = cluster.getAllChildMarkers();
    const seen = {{}};
    let html = '';
    kids.forEach(function (mk) {{
      const meta = mk._meta;
      if (!meta || typeof meta.object_id !== 'number') return;
      const oid = meta.object_id;
      if (seen[oid]) return;
      seen[oid] = true;
      html += buildHoverPanel(meta.object || '', meta.uin || '', oid);
    }});
    if (!html) {{
      html = '<div class="cam-hover"><small>Нет данных об объекте</small></div>';
    }} else {{
      html = '<div class="cam-hover-stack">' + html + '</div>';
    }}
    cluster.bindTooltip(html, {{
      sticky: true,
      direction: 'auto',
      opacity: 1,
      interactive: false,
      className: 'cam-hover-tip'
    }}).openTooltip();
  }});
  layer.on('clustermouseout', function (ev) {{
    const cluster = ev.layer;
    cluster.closeTooltip();
    cluster.unbindTooltip();
  }});
}}

/** Клик по кластеру на дашборде — приблизить карту к границам площадки. */
function bindClusterDashboardClick(layer) {{
  if (!dashHover || !useCluster) return;
  layer.on('clusterclick', function (ev) {{
    const cluster = ev.layer;
    const b = cluster.getBounds();
    if (b.isValid()) {{
      map.fitBounds(b.pad(0.38));
    }}
  }});
}}

/** Доля online в кластере (только маркеры с _meta.status). */
function dashClusterOnlineFraction(cluster) {{
  const kids = cluster.getAllChildMarkers();
  let total = 0;
  let online = 0;
  kids.forEach(function (mk) {{
    const meta = mk._meta;
    if (!meta || meta.kind !== 'camera') return;
    total++;
    if (meta.status === 'online') online++;
  }});
  return {{ total: total, online: online }};
}}

/**
 * Цвет кружка кластера на дашборде:
 * - зелёный — все камеры online (100%);
 * - жёлтый — от 30% до <100% online;
 * - красный — строго менее 30% online.
 */
function dashClusterTier(cluster) {{
  const h = dashClusterOnlineFraction(cluster);
  if (h.total <= 0) return 'grey';
  const frac = h.online / h.total;
  if (frac >= 1 - 1e-9) return 'green';
  if (frac < 0.3) return 'red';
  return 'yellow';
}}

function dashClusterIconCreate(cluster) {{
  const tier = dashClusterTier(cluster);
  const count = cluster.getChildCount();
  let sizeCls = ' marker-cluster-large';
  if (count < 10) sizeCls = ' marker-cluster-small';
  else if (count < 100) sizeCls = ' marker-cluster-medium';
  return new L.DivIcon({{
    html: '<div><span>' + count + '</span></div>',
    className: 'marker-cluster dash-cluster-' + tier + sizeCls,
    iconSize: new L.Point(40, 40)
  }});
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
const clusterOpts = {{
  chunkedLoading: true,
  showCoverageOnHover: false,
  spiderfyOnMaxZoom: true,
  disableClusteringAtZoom: 17,
  maxClusterRadius: {cluster_radius},
  zoomToBoundsOnClick: !(useCluster && dashHover)
}};
if (useCluster && dashHover) {{
  clusterOpts.iconCreateFunction = dashClusterIconCreate;
}}
const featureGroup = useCluster
  ? L.markerClusterGroup(clusterOpts).addTo(map)
  : L.featureGroup().addTo(map);

if (useCluster && dashHover) {{
  bindClusterDashboardHover(featureGroup);
  bindClusterDashboardClick(featureGroup);
}}

(function installMapOverlay() {{
  const data = {overlay_json};
  if (!data || typeof data.total !== 'number' || data.total <= 0) return;
  const wrap = document.createElement('div');
  wrap.className = 'map-stats-overlay';
  let text = 'На карте точек: ' + data.mapped + ' из ' + data.total;
  if (data.no_gps > 0) {{
    text += ' (' + data.no_gps + ' без координат)';
  }}
  wrap.textContent = text;
  map.getContainer().appendChild(wrap);
}})();
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
         + '<small>Камер: ' + m.num + '</small>'
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
  attachDashboardHover(marker, m);
  marker.on('dblclick', function (ev) {{
    L.DomEvent.stopPropagation(ev);
    if (m.kind === 'object') {{ return; }}
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

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        dark: bool = False,
        cluster: bool = False,
        dashboard_hover: bool = False,
    ):
        super().__init__(parent)
        self._dark = dark
        # Кластеризация маркеров (Leaflet.markercluster). По умолчанию
        # выключена — на карте «Камеры» точки всегда индивидуальные.
        # Включаем только для дашбордной мини-карты, чтобы при общем виде
        # на регион камеры собирались в стопки с количеством.
        self._cluster = cluster
        # Плашка при наведении на дашборде — только объект и УИН.
        self._dashboard_hover = dashboard_hover
        self._map_html_generation = 0
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
        # Кластер — только если CameraMapView создан с cluster=True
        # (сейчас это только дашбордная мини-карта). Радиус — стандартный
        # Leaflet (80 px), чтобы стопки образовывались уже на уровне города.
        self._map_html_generation += 1
        html = leaflet_html(
            markers,
            dark=self._dark,
            cluster=self._cluster,
            cluster_radius=80,
            dashboard_hover=self._dashboard_hover,
            map_overlay={
                "mapped": len(markers),
                "total": total,
                "no_gps": max(0, total - len(markers)),
            },
        )
        self._view.setHtml(
            html,
            QUrl(f"https://local.map/g{self._map_html_generation}/"),
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
