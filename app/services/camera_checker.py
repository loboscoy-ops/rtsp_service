from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app import config
from app.database.models import CameraModel
from app.utils.datetime_utils import now_iso
from app.utils.ping_utils import host_from_rtsp_url, ping_host
from app.utils.process_utils import ensure_binary_exists, run_command
from app.utils.validators import is_valid_rtsp_url


# --- результаты ------------------------------------------------------------


@dataclass
class CheckResult:
    camera_id: int
    status: str
    checked_at: str
    error: Optional[str]
    seen_online_at: Optional[str]
    ping_ok: Optional[bool] = None
    ping_ms: Optional[int] = None


class CheckLevel(Enum):
    """Два уровня глубины проверки RTSP-потока через ffprobe.

    NORMAL — быстрая (5 c) проверка для всех камер.
    DEEP   — длинная (120 c) проверка для камер, которые предыдущим тиком
             ушли в unknown. После DEEP результат всегда детерминирован:
             либо online, либо offline. Никаких «висящих» unknown.
    """

    NORMAL = "normal"
    DEEP = "deep"

    @property
    def timeout_sec(self) -> int:
        if self is CheckLevel.DEEP:
            return max(1, config.CHECK_TIMEOUT_DEEP_SEC)
        return max(1, config.CHECK_TIMEOUT_SEC)


# --- worker ----------------------------------------------------------------


class CameraCheckWorker(QRunnable):
    """Запускает ping + ffprobe и эмитит CheckResult через долгоживущий сигнал."""

    def __init__(self, camera: CameraModel, result_signal):
        super().__init__()
        self.camera = camera
        self._result_signal = result_signal
        self._ping_result: Optional[tuple[bool, Optional[int]]] = None
        self.setAutoDelete(True)

    # -- public entry point --------------------------------------------------

    def run(self) -> None:
        checked_at = now_iso()
        last_seen = self.camera.last_seen_online_at

        self._ping_result = self._run_ping()

        if not self.camera.enabled:
            self._emit(self._unknown_result(checked_at, last_seen, error="Камера выключена"))
            return

        url = self.camera.rtsp_url.strip()
        if not is_valid_rtsp_url(url):
            self._emit(self._offline_result(checked_at, last_seen, error="Некорректный RTSP URL"))
            return

        if not ensure_binary_exists(config.FFPROBE_BIN):
            self._emit(
                self._offline_result(
                    checked_at,
                    last_seen,
                    error=f"ffprobe не найден: {config.FFPROBE_BIN}",
                )
            )
            return

        level = self._resolve_check_level()
        self._probe_and_emit(url, level, checked_at, last_seen)

    # -- ping ---------------------------------------------------------------

    def _run_ping(self) -> Optional[tuple[bool, Optional[int]]]:
        if not config.PING_ENABLED:
            return None
        host = host_from_rtsp_url(self.camera.rtsp_url)
        if not host:
            return None
        try:
            return ping_host(host, config.PING_TIMEOUT_SEC)
        except Exception:
            return (False, None)

    # -- ffprobe ------------------------------------------------------------

    def _probe_and_emit(
        self,
        url: str,
        level: CheckLevel,
        checked_at: str,
        last_seen: Optional[str],
    ) -> None:
        timeout_sec = level.timeout_sec

        # Часть камер (особенно Dahua/Hikvision со стримом cam/realmonitor) отвечает
        # на TCP медленно или только по UDP. ffplay сам пробует оба транспорта,
        # ffprobe — нет, поэтому делаем это сами: сначала TCP, потом UDP.
        for transport in ("tcp", "udp"):
            outcome = self._probe_once(url, transport, timeout_sec)
            if outcome.kind == "online":
                self._emit(
                    CheckResult(
                        camera_id=self.camera.id,
                        status="online",
                        checked_at=checked_at,
                        error=None,
                        seen_online_at=checked_at,
                    )
                )
                return
            if outcome.kind == "error":
                # Реальная ошибка от ffprobe (401/404/Connection refused) — UDP
                # пробовать смысла нет, камера действительно недоступна.
                self._emit(
                    self._offline_result(checked_at, last_seen, error=outcome.error[:500])
                )
                return
            if outcome.kind == "exception":
                self._emit(self._offline_result(checked_at, last_seen, error=outcome.error))
                return
            # outcome.kind == "timeout" → пробуем следующий транспорт.

        # Оба транспорта в timeout — это уже наш «unknown»-сценарий.
        self._emit(self._timeout_result(checked_at, last_seen, level))

    def _probe_once(self, url: str, transport: str, timeout_sec: int) -> "_ProbeOutcome":
        cmd = self._build_ffprobe_cmd(url, transport, timeout_sec)
        try:
            proc = run_command(cmd, timeout_sec=max(2, timeout_sec + 2))
        except subprocess.TimeoutExpired:
            return _ProbeOutcome(kind="timeout", error="")
        except Exception as exc:
            return _ProbeOutcome(kind="exception", error=str(exc))

        if proc.returncode == 0 and proc.stdout.strip():
            return _ProbeOutcome(kind="online", error="")

        err = (proc.stderr or proc.stdout or f"Код {proc.returncode}").strip()
        if _looks_like_timeout(err):
            return _ProbeOutcome(kind="timeout", error=err)
        return _ProbeOutcome(kind="error", error=err)

    @staticmethod
    def _build_ffprobe_cmd(url: str, transport: str, timeout_sec: int) -> list[str]:
        timeout_us = str(timeout_sec * 1_000_000)
        # ВАЖНО: не добавляем -stimeout/-probesize/-analyzeduration.
        # На некоторых сборках ffmpeg -stimeout вызывает «Unrecognized option»,
        # а слишком агрессивный probesize не даёт SDP распарситься —
        # из-за этого камеры начинают казаться «timeout», хотя проблема в флагах.
        return [
            config.FFPROBE_BIN,
            "-v", "error",
            "-rtsp_transport", transport,
            "-rw_timeout", timeout_us,
            "-timeout", timeout_us,
            "-show_entries", "format=format_name",
            "-of", "default=nw=1:nk=1",
            url,
        ]

    def _resolve_check_level(self) -> CheckLevel:
        # Камера, которая в прошлый раз не ответила за 5 c, получает один
        # длинный шанс на ~2 минуты. По его итогу — online или offline.
        if self.camera.status == "unknown":
            return CheckLevel.DEEP
        return CheckLevel.NORMAL

    # -- factories ----------------------------------------------------------

    def _unknown_result(
        self,
        checked_at: str,
        last_seen: Optional[str],
        error: Optional[str],
    ) -> CheckResult:
        return CheckResult(
            camera_id=self.camera.id,
            status="unknown",
            checked_at=checked_at,
            error=error,
            seen_online_at=last_seen,
        )

    def _offline_result(
        self,
        checked_at: str,
        last_seen: Optional[str],
        error: Optional[str],
    ) -> CheckResult:
        return CheckResult(
            camera_id=self.camera.id,
            status="offline",
            checked_at=checked_at,
            error=error,
            seen_online_at=last_seen,
        )

    def _timeout_result(
        self,
        checked_at: str,
        last_seen: Optional[str],
        level: CheckLevel,
    ) -> CheckResult:
        # После длинной (DEEP) проверки тайм-аут означает «не подключается»,
        # камера уходит в offline. После короткой (NORMAL) проверки тайм-аут
        # ещё не приговор — оставляем unknown и даём шанс длинной проверке
        # на следующем тике.
        if level is CheckLevel.DEEP:
            return self._offline_result(
                checked_at,
                last_seen,
                error=config.UNKNOWN_OFFLINE_FAIL_MESSAGE,
            )
        return self._unknown_result(checked_at, last_seen, error=None)

    # -- emit ---------------------------------------------------------------

    def _emit(self, result: CheckResult) -> None:
        self._stamp_offline_code(result)
        self._stamp_ping(result)
        try:
            self._result_signal.emit(result)
        except RuntimeError:
            # Владелец сигнала уже уничтожен (приложение закрывается) — молча игнорируем.
            pass

    @staticmethod
    def _stamp_offline_code(result: CheckResult) -> None:
        if result.status != "offline":
            return
        code = config.OFFLINE_ERROR_CODE
        if not code:
            return
        text = (result.error or "").strip()
        if text.startswith(code):
            return
        result.error = f"{code} {text}".strip()

    def _stamp_ping(self, result: CheckResult) -> None:
        if self._ping_result is None:
            return
        ok, ms = self._ping_result
        result.ping_ok = ok
        result.ping_ms = ms


def _looks_like_timeout(err: str) -> bool:
    """True если stderr ffprobe реально про сетевой таймаут.

    Раньше тут было `"timeout" in text`, но это ложно срабатывало на
    «Unrecognized option 'stimeout'» и подобных строках, в результате
    мы засчитывали ошибку парсинга аргументов как таймаут.
    """
    text = err.lower()
    if "unrecognized option" in text or "invalid argument" in text:
        return False
    return "timed out" in text or "operation timed out" in text or "i/o timeout" in text


@dataclass
class _ProbeOutcome:
    """Внутренний результат одной попытки ffprobe (TCP или UDP)."""
    kind: str   # "online" | "timeout" | "error" | "exception"
    error: str


# --- pool wrapper ----------------------------------------------------------


class CameraChecker(QObject):
    camera_checked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(max(1, config.MAX_CONCURRENT_CHECKS))

    def check_camera(self, camera: CameraModel) -> None:
        worker = CameraCheckWorker(camera, self.camera_checked)
        self.pool.start(worker)

    def check_many(self, cameras: list[CameraModel]) -> None:
        for cam in cameras:
            self.check_camera(cam)
