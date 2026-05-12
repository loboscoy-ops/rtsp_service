from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Urus Camera Monitor"
APP_VERSION = "0.1.0"

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
    """Где искать .git репозитория проекта (для служебных сценариев)."""
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

RESOURCES_DIR = ROOT_DIR / "resources"
LOGO_PATH = RESOURCES_DIR / "logo_urus.png"

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

CHECK_INTERVAL_OFFLINE_SEC = int(os.getenv("RTSP_CHECK_INTERVAL_OFFLINE_SEC", "180"))
CHECK_INTERVAL_ONLINE_SEC = int(os.getenv("RTSP_CHECK_INTERVAL_ONLINE_SEC", "600"))
CHECK_TIMEOUT_SEC = int(os.getenv("RTSP_CHECK_TIMEOUT_SEC", "30"))
CHECK_TIMEOUT_FAIL_MESSAGE = os.getenv(
    "RTSP_CHECK_TIMEOUT_FAIL_MESSAGE",
    "RTSP не подключается > 30 сек",
)
# Префикс-код, который ставится в колонку «Ошибка» для любой offline-камеры.
OFFLINE_ERROR_CODE = os.getenv("RTSP_OFFLINE_ERROR_CODE", "0x00")
# 24 потока на ~5000 камер — около ~3,5 минут на полный цикл при NORMAL=5с.
# Можно поднять переменной окружения RTSP_MAX_CONCURRENT_CHECKS.
MAX_CONCURRENT_CHECKS = int(os.getenv("RTSP_MAX_CONCURRENT_CHECKS", "24"))

# Параллельная ICMP-проверка хоста камеры (отделяет «сеть упала» от «RTSP сломан»).
PING_ENABLED = os.getenv("RTSP_PING_ENABLED", "1") == "1"
PING_TIMEOUT_SEC = int(os.getenv("RTSP_PING_TIMEOUT_SEC", "2"))

FFPROBE_BIN = os.getenv("RTSP_FFPROBE_BIN", "ffprobe")
FFPLAY_BIN = os.getenv("RTSP_FFPLAY_BIN", "ffplay")

# Seed demo data on first launch if DB is empty.
SEED_ON_EMPTY = os.getenv("RTSP_SEED_ON_EMPTY", "1") == "1"

# Тестовый режим ветки test_design: синтетический дашборд на 400 площадок.
TEST_DESIGN_EMULATION = os.getenv("RTSP_TEST_DESIGN_EMULATION", "1") == "1"
TEST_DESIGN_OBJECTS = int(os.getenv("RTSP_TEST_DESIGN_OBJECTS", "400"))
TEST_DESIGN_CAMERAS_MIN = int(os.getenv("RTSP_TEST_DESIGN_CAMERAS_MIN", "6"))
TEST_DESIGN_CAMERAS_MAX = int(os.getenv("RTSP_TEST_DESIGN_CAMERAS_MAX", "10"))

