from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_ROOT = Path(__file__).resolve().parent.parent

# Источник списка камер: excel (файл .xlsx) или sheets (Google Таблица)
DATA_SOURCE = os.getenv("DATA_SOURCE", "excel").strip().lower()
if DATA_SOURCE not in ("excel", "sheets"):
    DATA_SOURCE = "excel"

_excel_path_raw = os.getenv("EXCEL_CAMERAS_PATH", "").strip()
EXCEL_CAMERAS_PATH = (
    Path(_excel_path_raw) if _excel_path_raw else (_ROOT / "data" / "cameras.xlsx")
)

# Ваша книга по умолчанию (можно переопределить в .env)
_DEFAULT_SPREADSHEET_ID = "1sYMmVZTjE136vruKJx1hh-gkwax_AtFWHrnO8x2xWkw"
_DEFAULT_CAMERAS_SHEET_GID = 1450282054

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip() or _DEFAULT_SPREADSHEET_ID
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
# Альтернатива JSON: ключ API Google Cloud (Sheets API включён). Таблица должна быть доступна
# «Все в интернете могут просматривать» — иначе 403.
GOOGLE_SHEETS_API_KEY = os.getenv("GOOGLE_SHEETS_API_KEY", "").strip()

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


if "CAMERAS_SHEET_GID" in os.environ:
    CAMERAS_SHEET_GID = _optional_int("CAMERAS_SHEET_GID")
else:
    CAMERAS_SHEET_GID = _DEFAULT_CAMERAS_SHEET_GID

CAMERAS_SHEET = os.getenv("CAMERAS_SHEET", "Камеры").strip()

SHEETS_AUTH_MODE = (
    "service_account"
    if GOOGLE_APPLICATION_CREDENTIALS
    else ("api_key" if GOOGLE_SHEETS_API_KEY else "none")
)
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
