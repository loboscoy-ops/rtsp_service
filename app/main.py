from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import subprocess

from . import config
from .host_ping import host_from_rtsp_url, ping_host
from .rtsp_probe import mask_rtsp_url
from .sheets_sync import (
    CameraRecord,
    SheetsState,
    check_spreadsheet_access,
    fetch_cameras_from_spreadsheet,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

_sheets_state: SheetsState = SheetsState()
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
    for c in _sheets_state.cameras:
        if c.camera_id == camera_id:
            return c
    return None


def _display_title(cam: CameraRecord) -> str:
    if cam.project and cam.name:
        return f"{cam.project} — {cam.name}"
    if cam.name:
        return cam.name
    return cam.legacy_sheet_title or cam.camera_id


async def _sync_sheets_once() -> None:
    global _sheets_state
    async with _lock:
        state = await asyncio.to_thread(fetch_cameras_from_spreadsheet)
        _sheets_state = state
        known = {c.camera_id for c in state.cameras}
        for cid in list(_ping_state.keys()):
            if cid not in known:
                _ping_state.pop(cid, None)
    if state.last_error:
        log.warning("синхронизация таблицы: %s", state.last_error)
    else:
        log.info(
            "камер: %s (%s)",
            len(state.cameras),
            "таблица" if state.table_mode else "по вкладкам",
        )


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
        _append_log(
            camera_id,
            cam.project,
            cam.name,
            False,
            err,
        )
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
        cameras = list(_sheets_state.cameras)
        await _ping_many([c.camera_id for c in cameras])
        await asyncio.sleep(max(60, config.PING_INTERVAL_SEC))


async def _sheets_poll_loop() -> None:
    while True:
        await _sync_sheets_once()
        await asyncio.sleep(max(30, config.SHEETS_POLL_INTERVAL_SEC))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _sync_sheets_once()
    await _ping_many([c.camera_id for c in _sheets_state.cameras])
    t1 = asyncio.create_task(_sheets_poll_loop())
    t2 = asyncio.create_task(_ping_all_loop())
    yield
    t1.cancel()
    t2.cancel()
    for t in (t1, t2):
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
        "project": c.project,
        "name": c.name,
        "camera_type": c.camera_type,
        "cell": c.cell_a1,
        "rtsp_masked": mask_rtsp_url(c.rtsp_url),
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
    """Проверка ключа и прав: метаданные книги + чтение A1 целевого листа."""
    return await asyncio.to_thread(check_spreadsheet_access)


@app.get("/api/logs")
async def get_logs(limit: int = 200):
    return {"logs": _recent_logs(limit)}


@app.get("/api/cameras")
async def list_cameras():
    async with _lock:
        cams = list(_sheets_state.cameras)
        err = _sheets_state.last_error
        updated = _sheets_state.updated_at_iso
        table_mode = _sheets_state.table_mode
        table_sheet_title = _sheets_state.table_sheet_title
    return {
        "spreadsheet_id": config.SPREADSHEET_ID,
        "spreadsheet_url": _spreadsheet_edit_url(),
        "sheets_updated_at": updated,
        "sheets_error": err,
        "table_mode": table_mode,
        "cameras_sheet": config.CAMERAS_SHEET or None,
        "cameras_sheet_gid": config.CAMERAS_SHEET_GID,
        "cameras_sheet_title": table_sheet_title,
        "ping_interval_sec": config.PING_INTERVAL_SEC,
        "cameras": [_camera_payload(c) for c in cams],
        "logs": _recent_logs(150),
    }


@app.post("/api/cameras/refresh-sheets")
async def refresh_sheets():
    await _sync_sheets_once()
    return await list_cameras()


@app.post("/api/cameras/ping-all")
async def ping_all():
    async with _lock:
        ids = [c.camera_id for c in _sheets_state.cameras]
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
    _append_log(
        camera_id,
        cam.project,
        cam.name,
        None,
        "запуск ffplay",
    )
    return {"ok": True, "message": "ffplay запущен"}
