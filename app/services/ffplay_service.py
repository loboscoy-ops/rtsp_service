from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass

from app import config
from app.utils.process_utils import ensure_binary_exists, resolve_binary

log = logging.getLogger(__name__)


@dataclass
class FFPlayLaunchResult:
    ok: bool
    error: str | None = None


class FFPlayService:
    def __init__(self) -> None:
        self._procs: list[subprocess.Popen] = []

    def launch(self, rtsp_url: str, title: str = "RTSP Camera") -> FFPlayLaunchResult:
        url = rtsp_url.strip()
        if not url:
            return FFPlayLaunchResult(ok=False, error="RTSP URL пустой")
        if not ensure_binary_exists(config.FFPLAY_BIN):
            return FFPlayLaunchResult(
                ok=False,
                error=f"ffplay не найден: {config.FFPLAY_BIN}. Установите ffmpeg.",
            )
        cmd = [
            resolve_binary(config.FFPLAY_BIN),
            "-rtsp_transport",
            "tcp",
            "-window_title",
            title,
            "-loglevel",
            "warning",
            url,
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            return FFPlayLaunchResult(ok=False, error=f"Не удалось запустить ffplay: {exc}")
        self._procs = [p for p in self._procs if p.poll() is None]
        self._procs.append(proc)
        return FFPlayLaunchResult(ok=True)

    def terminate_all(self) -> int:
        alive = [p for p in self._procs if p.poll() is None]
        if not alive:
            self._procs = []
            return 0
        for p in alive:
            try:
                p.terminate()
            except Exception as exc:
                log.warning("ffplay terminate(pid=%s) ошибка: %s", getattr(p, "pid", "?"), exc)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and any(p.poll() is None for p in alive):
            time.sleep(0.05)
        still_alive = [p for p in alive if p.poll() is None]
        for p in still_alive:
            try:
                p.kill()
            except Exception as exc:
                log.warning("ffplay kill(pid=%s) ошибка: %s", getattr(p, "pid", "?"), exc)
        self._procs = []
        return len(alive)

