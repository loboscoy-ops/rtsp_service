from __future__ import annotations

import re
from urllib.parse import urlparse


_RTSP_RE = re.compile(r"^rtsps?://", re.IGNORECASE)


def is_valid_rtsp_url(url: str) -> bool:
    text = (url or "").strip()
    if not _RTSP_RE.match(text):
        return False
    try:
        parsed = urlparse(text)
        return bool(parsed.scheme and parsed.hostname)
    except Exception:
        return False


def parse_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if text in {"0", "false", "no", "n", "off", "нет"}:
        return False
    return True


def mask_rtsp_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    # Hide credentials in rtsp://user:pass@host/...
    return re.sub(r"//[^:/@]+:[^@]+@", "//***:***@", text, count=1)

