from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Urus Camera Monitor"
APP_VERSION = "0.1.75"

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
# Legacy общий timeout (оставлен для обратной совместимости и как fallback).
CHECK_TIMEOUT_SEC = int(os.getenv("RTSP_CHECK_TIMEOUT_SEC", "30"))
# Двухэтапный опрос: быстрый проход по всем + углубленный только для проблемных.
CHECK_FAST_TIMEOUT_SEC = int(os.getenv("RTSP_CHECK_FAST_TIMEOUT_SEC", "4"))
CHECK_DEEP_TIMEOUT_SEC = int(os.getenv("RTSP_CHECK_DEEP_TIMEOUT_SEC", "12"))
CHECK_TIMEOUT_FAIL_MESSAGE = os.getenv(
    "RTSP_CHECK_TIMEOUT_FAIL_MESSAGE",
    "RTSP не подключается > 30 сек",
)
# Допустимые кодеки первого видеопотока (ffprobe stream=codec_name), через запятую.
_raw_codecs = os.getenv("RTSP_REQUIRED_VIDEO_CODECS", "h264").strip()
REQUIRED_VIDEO_CODECS = frozenset(
    c.strip().lower()
    for c in (_raw_codecs.split(",") if _raw_codecs else ["h264"])
    if c.strip()
) or frozenset({"h264"})
# Текст в колонке «Ошибка» при отсутствии H.264 или неопределённом видеокодеке (без детализации кодека).
REQUIRED_H264_ERROR_TEXT = os.getenv("RTSP_REQUIRED_H264_ERROR_TEXT", "Требуется H.264")
# Префикс-код в колонке «Ошибка» (пусто = не добавлять). Раньше по умолчанию был «0x00».
OFFLINE_ERROR_CODE = os.getenv("RTSP_OFFLINE_ERROR_CODE", "").strip()
# 24 потока на ~5000 камер — около ~3,5 минут на полный цикл при NORMAL=5с.
# Можно поднять переменной окружения RTSP_MAX_CONCURRENT_CHECKS.
MAX_CONCURRENT_CHECKS = int(os.getenv("RTSP_MAX_CONCURRENT_CHECKS", "24"))
# Дополнительные лимиты, чтобы не перегружать один объект/регистратор.
# Важно: при дефолте «1 на host» все камеры с одного IP (типичный NVR)
# проверяются строго по очереди — полный опрос площадки заметно растягивается.
# Для слабых регистраторов можно понизить через переменные окружения.
MAX_CONCURRENT_CHECKS_PER_OBJECT = int(
    os.getenv("RTSP_MAX_CONCURRENT_CHECKS_PER_OBJECT", "4")
)
MAX_CONCURRENT_CHECKS_PER_HOST = int(
    os.getenv("RTSP_MAX_CONCURRENT_CHECKS_PER_HOST", "4")
)

# Параллельная ICMP-проверка хоста камеры (отделяет «сеть упала» от «RTSP сломан»).
PING_ENABLED = os.getenv("RTSP_PING_ENABLED", "1") == "1"
PING_TIMEOUT_SEC = int(os.getenv("RTSP_PING_TIMEOUT_SEC", "2"))

FFPROBE_BIN = os.getenv("RTSP_FFPROBE_BIN", "ffprobe")
FFPLAY_BIN = os.getenv("RTSP_FFPLAY_BIN", "ffplay")

# Seed demo data on first launch if DB is empty.
SEED_ON_EMPTY = os.getenv("RTSP_SEED_ON_EMPTY", "1") == "1"

