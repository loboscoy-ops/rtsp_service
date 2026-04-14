from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

# Лист с таблицей камер (колонки проект / имя / тип / URL).
# CAMERAS_SHEET_GID — число из URL (...#gid=1450282054); если задано, имеет приоритет над именем листа.
def _optional_int(name: str) -> int | None:
    v = os.getenv(name, "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


CAMERAS_SHEET_GID = _optional_int("CAMERAS_SHEET_GID")
CAMERAS_SHEET = os.getenv("CAMERAS_SHEET", "Камеры").strip()
IGNORE_SHEETS = frozenset(
    s.strip()
    for s in os.getenv("IGNORE_SHEETS", "Template,Шаблон,README").split(",")
    if s.strip()
)
SHEETS_POLL_INTERVAL_SEC = int(os.getenv("SHEETS_POLL_INTERVAL_SEC", "120"))

# Автоопрос доступности: ping хоста из RTSP (секунды). По умолчанию 30 минут.
PING_INTERVAL_SEC = int(os.getenv("PING_INTERVAL_SEC", str(30 * 60)))
PING_CONCURRENCY = max(1, int(os.getenv("PING_CONCURRENCY", "8")))

# Ручная «глубокая» проверка потока ffprobe (секунды), если понадобится отдельно
RTSP_PROBE_INTERVAL_SEC = int(os.getenv("RTSP_PROBE_INTERVAL_SEC", "300"))
RTSP_FFPROBE_TIMEOUT_US = int(os.getenv("RTSP_FFPROBE_TIMEOUT_US", "8000000"))

FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
FFPLAY_BIN = os.getenv("FFPLAY_BIN", "ffplay")

RTSP_PROBE_CONCURRENCY = max(1, int(os.getenv("RTSP_PROBE_CONCURRENCY", "4")))

# Журнал событий (ping и смена online/offline), не более N записей в памяти
STATUS_LOG_MAX = max(50, int(os.getenv("STATUS_LOG_MAX", "500")))

# CORS: через запятую, например http://127.0.0.1:3000 (пусто = отключено)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").strip()
