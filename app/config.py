from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "RTSP Camera Monitor"
APP_VERSION = "0.1.18"

ROOT_DIR = Path(__file__).resolve().parent.parent
IS_FROZEN = bool(getattr(sys, "frozen", False))


def _user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "RTSPCameraMonitor"
    return Path.home() / ".rtsp-camera-monitor"


# Куда пишем БД и логи: внутри репозитория при разработке, в Application Support при сборке.
DATA_DIR = Path(os.getenv("RTSP_DATA_DIR") or (_user_data_dir() if IS_FROZEN else ROOT_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("RTSP_APP_DB_PATH") or (DATA_DIR / "rtsp_monitor.db"))
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _find_project_git_dir() -> Path | None:
    """Где брать .git для кнопки 'Обновить из GitHub'."""
    candidates = []
    env_dir = os.getenv("RTSP_PROJECT_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.append(Path.home() / "rtsp-camera-service")
    candidates.append(ROOT_DIR)
    for c in candidates:
        if (c / ".git").exists():
            return c
    return None


PROJECT_GIT_DIR = _find_project_git_dir()
GITHUB_REPO_URL = os.getenv(
    "RTSP_GITHUB_URL",
    "https://github.com/loboscoy-ops/rtsp_service",
)

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

CHECK_INTERVAL_SEC = int(os.getenv("RTSP_CHECK_INTERVAL_SEC", "60"))
CHECK_TIMEOUT_SEC = int(os.getenv("RTSP_CHECK_TIMEOUT_SEC", "5"))
# Глубокая проверка для камер со статусом unknown — даём им больше времени.
CHECK_TIMEOUT_DEEP_SEC = int(os.getenv("RTSP_CHECK_TIMEOUT_DEEP_SEC", "10"))
# Код в колонке «Ошибка», если глубокая проверка всё равно не достучалась.
UNKNOWN_DEEP_FAIL_CODE = os.getenv("RTSP_UNKNOWN_DEEP_FAIL_CODE", "0x03")
# «Финальная» проверка для unknown + 0x03: длинный таймаут, после которого камера уходит в offline.
CHECK_TIMEOUT_ULTRA_SEC = int(os.getenv("RTSP_CHECK_TIMEOUT_ULTRA_SEC", "120"))
# Текст ошибки, который попадает в колонку «Ошибка» при offline после финальной проверки.
UNKNOWN_OFFLINE_FAIL_MESSAGE = os.getenv(
    "RTSP_UNKNOWN_OFFLINE_FAIL_MESSAGE",
    "RTSP не запускается (0x03)",
)
MAX_CONCURRENT_CHECKS = int(os.getenv("RTSP_MAX_CONCURRENT_CHECKS", "6"))

FFPROBE_BIN = os.getenv("RTSP_FFPROBE_BIN", "ffprobe")
FFPLAY_BIN = os.getenv("RTSP_FFPLAY_BIN", "ffplay")

# Seed demo data on first launch if DB is empty.
SEED_ON_EMPTY = os.getenv("RTSP_SEED_ON_EMPTY", "1") == "1"

