from __future__ import annotations

import asyncio
import logging
import platform
import re
import subprocess
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_HOST_SAFE = re.compile(r"^[a-zA-Z0-9.\-:]+$")


def host_from_rtsp_url(url: str) -> str | None:
    if not url or not str(url).strip().lower().startswith("rtsp"):
        return None
    try:
        p = urlparse(str(url).strip())
        h = p.hostname
        if not h:
            return None
        if not _HOST_SAFE.match(h):
            log.warning("подозрительный hostname из RTSP, ping пропущен: %r", h[:80])
            return None
        return h
    except Exception:
        return None


def _ping_cmd(host: str) -> list[str]:
    system = platform.system()
    if system == "Darwin":
        return ["ping", "-c", "1", "-W", "3000", host]
    if system == "Windows":
        return ["ping", "-n", "1", "-w", "3000", host]
    return ["ping", "-c", "1", "-W", "2", host]


async def ping_host(host: str) -> tuple[bool, str | None]:
    if not host:
        return False, "нет хоста"
    cmd = _ping_cmd(host)

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=12,
        )

    try:
        proc = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        return False, "таймаут ping"
    except FileNotFoundError:
        return False, "команда ping не найдена"
    except Exception as e:
        return False, str(e)

    if proc.returncode == 0:
        return True, None
    err = (proc.stderr or proc.stdout or "").strip() or f"код {proc.returncode}"
    return False, err[:400]
