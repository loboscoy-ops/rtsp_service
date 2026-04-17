from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "RTSP Camera Monitor"
APP_VERSION = "0.1.0"

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("RTSP_APP_DB_PATH", DATA_DIR / "rtsp_monitor.db"))
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Excel source for MVP workflow.
EXCEL_TEMPLATE_HEADERS = [
    "object_name",
    "camera_identifier",
    "camera_name",
    "rtsp_url",
    "group_name",
    "gps_coords",
    "enabled",
]

CHECK_INTERVAL_SEC = int(os.getenv("RTSP_CHECK_INTERVAL_SEC", "120"))
CHECK_TIMEOUT_SEC = int(os.getenv("RTSP_CHECK_TIMEOUT_SEC", "10"))
MAX_CONCURRENT_CHECKS = int(os.getenv("RTSP_MAX_CONCURRENT_CHECKS", "6"))

FFPROBE_BIN = os.getenv("RTSP_FFPROBE_BIN", "ffprobe")
FFPLAY_BIN = os.getenv("RTSP_FFPLAY_BIN", "ffplay")

# Seed demo data on first launch if DB is empty.
SEED_ON_EMPTY = os.getenv("RTSP_SEED_ON_EMPTY", "1") == "1"

