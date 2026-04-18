from __future__ import annotations

import re
import subprocess
import sys
from urllib.parse import urlparse


_TIME_RE = re.compile(r"time[=<]([\d.]+)\s*ms", re.IGNORECASE)


def host_from_rtsp_url(url: str) -> str:
    """Извлекает хост из RTSP URL без логина/пароля и порта."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return ""
    return (parsed.hostname or "").strip()


def ping_host(host: str, timeout_sec: float = 2.0) -> tuple[bool, int | None]:
    """Один ICMP-пинг.

    Возвращает (ok, latency_ms). На macOS/Linux вызывает системный `ping`.
    Не требует root: использует /sbin/ping (DGRAM сокет на macOS).
    """
    host = (host or "").strip()
    if not host:
        return False, None

    timeout_int = max(1, int(timeout_sec))
    if sys.platform == "darwin":
        cmd = ["ping", "-c", "1", "-t", str(timeout_int), host]
    elif sys.platform.startswith("linux"):
        cmd = ["ping", "-c", "1", "-W", str(timeout_int), host]
    else:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout_sec * 1000)), host]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec + 1.5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, None

    if proc.returncode != 0:
        return False, None

    m = _TIME_RE.search(proc.stdout or "")
    if m:
        try:
            return True, int(round(float(m.group(1))))
        except ValueError:
            return True, None
    return True, None
