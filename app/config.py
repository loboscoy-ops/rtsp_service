import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
IGNORE_SHEETS = frozenset(
    s.strip()
    for s in os.getenv("IGNORE_SHEETS", "Template,Шаблон,README").split(",")
    if s.strip()
)
SHEETS_POLL_INTERVAL_SEC = int(os.getenv("SHEETS_POLL_INTERVAL_SEC", "120"))
RTSP_PROBE_INTERVAL_SEC = int(os.getenv("RTSP_PROBE_INTERVAL_SEC", "60"))
RTSP_FFPROBE_TIMEOUT_US = int(os.getenv("RTSP_FFPROBE_TIMEOUT_US", "8000000"))

FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
FFPLAY_BIN = os.getenv("FFPLAY_BIN", "ffplay")

# Сколько камер одновременно проверять ffprobe (остальные ждут в очереди)
RTSP_PROBE_CONCURRENCY = max(1, int(os.getenv("RTSP_PROBE_CONCURRENCY", "4")))

# CORS: через запятую, например http://127.0.0.1:3000 (пусто = отключено)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").strip()
