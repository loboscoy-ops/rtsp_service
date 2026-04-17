from __future__ import annotations

import subprocess
from dataclasses import dataclass

from app import config
from app.utils.process_utils import ensure_binary_exists


@dataclass
class FFPlayLaunchResult:
    ok: bool
    error: str | None = None


class FFPlayService:
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
            config.FFPLAY_BIN,
            "-rtsp_transport",
            "tcp",
            "-window_title",
            title,
            "-loglevel",
            "warning",
            url,
        ]
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return FFPlayLaunchResult(ok=True)
        except Exception as exc:
            return FFPlayLaunchResult(ok=False, error=f"Не удалось запустить ffplay: {exc}")

