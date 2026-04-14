from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import subprocess

from . import config
from .excel_cameras import load_cameras_from_excel
from .host_ping import host_from_rtsp_url, ping_host
from .models import CameraRecord
from .rtsp_probe import mask_rtsp_url
from .sheets_sync import SheetsState, check_spreadsheet_access, fetch_cameras_from_spreadsheet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass
class AppCameraState:
    cameras: list[CameraRecord] = field(default_factory=list)
    load_error: str | None = None
    updated_at_iso: str | None = None
    data_source: str = "excel"
    table_mode: bool = False
    table_sheet_title: str | None = None


_state: AppCameraState = AppCameraState()
_ping_state: dict[str, dict] = {}
_status_logs: deque[dict] = deque(maxlen=config.STATUS_LOG_MAX)
_lock = asyncio.Lock()


def _append_log(
    camera_id: str,
    project: str,
    name: str,
    ok: bool | None,
    message: str,
) -> None:
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "camera_id": camera_id,
        "project": project,
        "name": name,
        "ok": ok,
        "message": message[:800],
    }
    _status_logs.append(entry)


def _recent_logs(limit: int) -> list[dict]:
    n = max(1, min(limit, config.STATUS_LOG_MAX))
    return list(_status_logs)[-n:][::-1]


def _camera_by_id(camera_id: str) -> CameraRecord | None:
    for c in _state.cameras:
        if c.camera_id == camera_id:
            return c
    return None


def _display_title(cam: CameraRecord) -> str:
    if cam.project and cam.name:
        return f"{cam.project} — {cam.name}"
    if cam.name:
        return cam.name
    return cam.legacy_sheet_title or cam.camera_id


async def _reload_data() -> None:
    global _state
    now_iso = datetime.now(timezone.utc).isoformat()
    async with _lock:
        if config.DATA_SOURCE == "excel":
            cams, err = await asyncio.to_thread(
                load_cameras_from_excel, config.EXCEL_CAMERAS_PATH
            )
            _state = AppCameraState(
                cameras=cams,
                load_error=err,
                updated_at_iso=now_iso,
                data_source="excel",
            )
        else:
            ss: SheetsState = await asyncio.to_thread(fetch_cameras_from_spreadsheet)
            _state = AppCameraState(
                cameras=list(ss.cameras),
                load_error=ss.last_error,
                updated_at_iso=ss.updated_at_iso or now_iso,
                data_source="sheets",
                table_mode=ss.table_mode,
                table_sheet_title=ss.table_sheet_title,
            )
        known = {c.camera_id for c in _state.cameras}
        for cid in list(_ping_state.keys()):
            if cid not in known:
                _ping_state.pop(cid, None)
    if _state.load_error:
        log.warning("загрузка данных: %s", _state.load_error)
    else:
        log.info("камер: %s (источник=%s)", len(_state.cameras), _state.data_source)


async def _ping_camera(camera_id: str) -> None:
    cam = _camera_by_id(camera_id)
    if not cam:
        return
    host = host_from_rtsp_url(cam.rtsp_url)
    prev = _ping_state.get(camera_id, {})
    now_iso = datetime.now(timezone.utc).isoformat()

    if not host:
        ok = False
        err = "не удалось извлечь хост из RTSP URL"
        st = {
            "online": False,
            "last_check_at": now_iso,
            "last_seen_online": prev.get("last_seen_online"),
            "ping_host": None,
            "last_error": err,
        }
        _ping_state[camera_id] = st
        _append_log(camera_id, cam.project, cam.name, False, err)
        return

    ok, err = await ping_host(host)
    st = {
        "online": ok,
        "last_check_at": now_iso,
        "last_seen_online": now_iso if ok else prev.get("last_seen_online"),
        "ping_host": host,
        "last_error": err,
    }
    _ping_state[camera_id] = st
    msg = f"ping {host} OK" if ok else f"ping {host} FAIL: {err or 'нет ответа'}"
    _append_log(camera_id, cam.project, cam.name, ok, msg)


async def _ping_many(camera_ids: list[str]) -> None:
    if not camera_ids:
        return
    sem = asyncio.Semaphore(config.PING_CONCURRENCY)

    async def one(cid: str) -> None:
        async with sem:
            await _ping_camera(cid)

    results = await asyncio.gather(
        *(one(cid) for cid in camera_ids),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            log.error("ошибка в пакетном ping", exc_info=r)


async def _ping_all_loop() -> None:
    while True:
        cameras = list(_state.cameras)
        await _ping_many([c.camera_id for c in cameras])
        await asyncio.sleep(max(60, config.PING_INTERVAL_SEC))


async def _sheets_poll_loop() -> None:
    while True:
        await asyncio.sleep(max(30, config.SHEETS_POLL_INTERVAL_SEC))
        await _reload_data()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _reload_data()
    await _ping_many([c.camera_id for c in _state.cameras])
    tasks = [asyncio.create_task(_ping_all_loop())]
    if config.DATA_SOURCE == "sheets":
        tasks.append(asyncio.create_task(_sheets_poll_loop()))
    yield
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="RTSP Camera Service", lifespan=lifespan)

_cors_origins = [o.strip() for o in config.CORS_ORIGINS.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _spreadsheet_edit_url() -> str | None:
    if not config.SPREADSHEET_ID:
        return None
    return f"https://docs.google.com/spreadsheets/d/{config.SPREADSHEET_ID}/edit"


@app.get("/", response_class=HTMLResponse)
async def root():
    index = static_dir / "index.html"
    if index.is_file():
        return FileResponse(index)
    return HTMLResponse("<p>Добавьте static/index.html</p>", status_code=500)


def _camera_payload(c: CameraRecord) -> dict:
    st = _ping_state.get(c.camera_id, {})
    return {
        "camera_id": c.camera_id,
        "row_no": c.row_no,
        "uin": c.uin,
        "project": c.project,
        "name": c.name,
        "address": c.address,
        "camera_type": c.camera_type,
        "cell": c.cell_a1,
        "rtsp_masked": mask_rtsp_url(c.rtsp_url),
        "lat": c.lat,
        "lon": c.lon,
        "ping_host": st.get("ping_host"),
        "online": st.get("online"),
        "last_error": st.get("last_error"),
        "last_check_at": st.get("last_check_at"),
        "last_seen_online": st.get("last_seen_online"),
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "rtsp-camera-service"}


@app.get("/api/sheets-access")
async def sheets_access():
    if config.DATA_SOURCE != "sheets":
        return {
            "ok": True,
            "skipped": True,
            "message": "Активен режим Excel; Google Sheets не используется",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    return await asyncio.to_thread(check_spreadsheet_access)


@app.get("/api/logs")
async def get_logs(limit: int = 200):
    return {"logs": _recent_logs(limit)}


@app.get("/api/cameras")
async def list_cameras():
    async with _lock:
        cams = list(_state.cameras)
        err = _state.load_error
        updated = _state.updated_at_iso
        ds = _state.data_source
        table_mode = _state.table_mode
        table_sheet_title = _state.table_sheet_title
    return {
        "data_source": ds,
        "excel_path": str(config.EXCEL_CAMERAS_PATH) if ds == "excel" else None,
        "load_error": err,
        "spreadsheet_id": config.SPREADSHEET_ID if ds == "sheets" else None,
        "spreadsheet_url": _spreadsheet_edit_url() if ds == "sheets" else None,
        "sheets_updated_at": updated,
        "sheets_error": err if ds == "sheets" else None,
        "table_mode": table_mode,
        "cameras_sheet": config.CAMERAS_SHEET or None,
        "cameras_sheet_gid": config.CAMERAS_SHEET_GID if ds == "sheets" else None,
        "cameras_sheet_title": table_sheet_title,
        "sheets_auth_mode": config.SHEETS_AUTH_MODE if ds == "sheets" else None,
        "ping_interval_sec": config.PING_INTERVAL_SEC,
        "cameras": [_camera_payload(c) for c in cams],
        "logs": _recent_logs(150),
    }


@app.post("/api/cameras/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    if config.DATA_SOURCE != "excel":
        raise HTTPException(
            status_code=400,
            detail="Загрузка Excel только при DATA_SOURCE=excel в .env",
        )
    name = (file.filename or "").lower()
    if not name.endswith(".xlsx"):
        raise HTTPException(
            status_code=400,
            detail="Нужен файл .xlsx (Excel 2007+)",
        )
    raw = await file.read()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 15 МБ)")
    path = config.EXCEL_CAMERAS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    log.info("сохранён Excel: %s (%s байт)", path, len(raw))
    await _reload_data()
    return await list_cameras()


@app.post("/api/cameras/reload-excel")
async def reload_excel():
    if config.DATA_SOURCE != "excel":
        raise HTTPException(status_code=400, detail="Только для режима excel")
    await _reload_data()
    return await list_cameras()


@app.post("/api/cameras/refresh-sheets")
async def refresh_sheets():
    if config.DATA_SOURCE != "sheets":
        raise HTTPException(status_code=400, detail="Только для режима sheets")
    await _reload_data()
    return await list_cameras()


@app.post("/api/cameras/ping-all")
async def ping_all():
    async with _lock:
        ids = [c.camera_id for c in _state.cameras]
    await _ping_many(ids)
    return await list_cameras()


@app.post("/api/cameras/{camera_id}/ping")
async def ping_one(camera_id: str):
    cam = _camera_by_id(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Камера не найдена")
    await _ping_camera(camera_id)
    return _camera_payload(cam)


@app.post("/api/cameras/{camera_id}/ffplay")
async def launch_ffplay(camera_id: str):
    cam = _camera_by_id(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Камера не найдена")
    title = _display_title(cam)
    cmd = [
        config.FFPLAY_BIN,
        "-rtsp_transport",
        "tcp",
        "-window_title",
        f"RTSP: {title}",
        "-loglevel",
        "warning",
        cam.rtsp_url,
    ]
    try:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.exception("ffplay")
        raise HTTPException(status_code=500, detail=str(e)) from e
    _append_log(camera_id, cam.project, cam.name, None, "запуск ffplay")
    return {"ok": True, "message": "ffplay запущен"}
