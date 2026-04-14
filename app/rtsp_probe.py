from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from urllib.parse import urlparse

from . import config

log = logging.getLogger(__name__)

RTSP_SCHEME = re.compile(r"^rtsps?://", re.I)


def looks_like_rtsp_url(text: str) -> bool:
    t = (text or "").strip()
    if not t or " " in t:
        return False
    return bool(RTSP_SCHEME.match(t))


def mask_rtsp_url(url: str) -> str:
    try:
        p = urlparse(url)
        if p.username:
            return re.sub(
                r"//[^:]+:[^@]+@",
                "//***:***@",
                url,
                count=1,
            )
        return url
    except Exception:
        return "<invalid>"


async def probe_rtsp_reachable(url: str) -> tuple[bool, str | None]:
    if not shutil.which(config.FFPROBE_BIN.split("/")[-1]) and "/" not in config.FFPROBE_BIN:
        return False, "ffprobe не найден в PATH"

    cmd = [
        config.FFPROBE_BIN,
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        str(config.RTSP_FFPROBE_TIMEOUT_US),
        "-show_entries",
        "format=format_name",
        "-of",
        "default=nw=1:nk=1",
        url,
    ]

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(3, config.RTSP_FFPROBE_TIMEOUT_US / 1_000_000 + 2),
        )

    try:
        proc = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        return False, "таймаут ffprobe"
    except Exception as e:
        return False, str(e)

    if proc.returncode == 0 and (proc.stdout or "").strip():
        return True, None
    err = (proc.stderr or proc.stdout or "").strip() or f"код {proc.returncode}"
    return False, err[:500]
