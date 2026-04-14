from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import subprocess

from . import config
from .rtsp_probe import mask_rtsp_url, probe_rtsp_reachable
from .sheets_sync import CameraRecord, SheetsState, fetch_cameras_from_spreadsheet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

_sheets_state: SheetsState = SheetsState()
_rtsp_status: dict[int, dict] = {}
_lock = asyncio.Lock()


def _camera_by_sheet_id(sheet_id: int) -> CameraRecord | None:
    for c in _sheets_state.cameras:
        if c.sheet_id == sheet_id:
            return c
    return None


async def _sync_sheets_once() -> None:
    global _sheets_state
    async with _lock:
        state = await asyncio.to_thread(fetch_cameras_from_spreadsheet)
        _sheets_state = state
        known = {c.sheet_id for c in state.cameras}
        for sid in list(_rtsp_status.keys()):
            if sid not in known:
                _rtsp_status.pop(sid, None)
    if state.last_error:
        log.warning("синхронизация таблицы: %s", state.last_error)
    else:
        log.info("камер из таблицы: %s", len(state.cameras))


async def _probe_camera(sheet_id: int) -> None:
    cam = _camera_by_sheet_id(sheet_id)
    if not cam:
        return
    ok, err = await probe_rtsp_reachable(cam.rtsp_url)
    _rtsp_status[sheet_id] = {
        "online": ok,
        "error": err,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def _probe_all_loop() -> None:
    while True:
        cameras = list(_sheets_state.cameras)
        for c in cameras:
            await _probe_camera(c.sheet_id)
        await asyncio.sleep(max(15, config.RTSP_PROBE_INTERVAL_SEC))


async def _sheets_poll_loop() -> None:
    while True:
        await _sync_sheets_once()
        await asyncio.sleep(max(30, config.SHEETS_POLL_INTERVAL_SEC))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _sync_sheets_once()
    t1 = asyncio.create_task(_sheets_poll_loop())
    t2 = asyncio.create_task(_probe_all_loop())
    yield
    t1.cancel()
    t2.cancel()
    for t in (t1, t2):
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="RTSP Camera Service", lifespan=lifespan)
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    index = static_dir / "index.html"
    if index.is_file():
        return FileResponse(index)
    return HTMLResponse("<p>Добавьте static/index.html</p>", status_code=500)


def _camera_payload(c: CameraRecord) -> dict:
    st = _rtsp_status.get(c.sheet_id, {})
    return {
        "sheet_id": c.sheet_id,
        "name": c.sheet_title,
        "cell": c.cell_a1,
        "rtsp_masked": mask_rtsp_url(c.rtsp_url),
        "online": st.get("online"),
        "last_error": st.get("error"),
        "checked_at": st.get("checked_at"),
    }


@app.get("/api/cameras")
async def list_cameras():
    async with _lock:
        cams = list(_sheets_state.cameras)
        err = _sheets_state.last_error
        updated = _sheets_state.updated_at_iso
    return {
        "spreadsheet_id": config.SPREADSHEET_ID,
        "sheets_updated_at": updated,
        "sheets_error": err,
        "cameras": [_camera_payload(c) for c in cams],
    }


@app.post("/api/cameras/refresh-sheets")
async def refresh_sheets():
    await _sync_sheets_once()
    return await list_cameras()


@app.post("/api/cameras/{sheet_id}/probe")
async def probe_one(sheet_id: int):
    cam = _camera_by_sheet_id(sheet_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Камера не найдена")
    await _probe_camera(sheet_id)
    return _camera_payload(cam)


@app.post("/api/cameras/{sheet_id}/ffplay")
async def launch_ffplay(sheet_id: int):
    cam = _camera_by_sheet_id(sheet_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Камера не найдена")
    cmd = [
        config.FFPLAY_BIN,
        "-rtsp_transport",
        "tcp",
        "-window_title",
        f"RTSP: {cam.sheet_title}",
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
    return {"ok": True, "message": "ffplay запущен", "command": " ".join(cmd[:6]) + " <url>"}
