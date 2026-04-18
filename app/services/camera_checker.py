from __future__ import annotations

import subprocess
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app import config
from app.database.models import CameraModel
from app.utils.datetime_utils import now_iso
from app.utils.process_utils import ensure_binary_exists, run_command
from app.utils.validators import is_valid_rtsp_url


@dataclass
class CheckResult:
    camera_id: int
    status: str
    checked_at: str
    error: str | None
    seen_online_at: str | None


class CameraCheckWorker(QRunnable):
    """Эмитит результат через внешний bound-signal долгоживущего владельца."""

    def __init__(self, camera: CameraModel, result_signal):
        super().__init__()
        self.camera = camera
        self._result_signal = result_signal
        self.setAutoDelete(True)

    def _emit(self, result: CheckResult) -> None:
        try:
            self._result_signal.emit(result)
        except RuntimeError:
            # Владелец сигнала уже уничтожен (приложение закрывается) — молча игнорируем.
            pass

    def run(self) -> None:
        checked_at = now_iso()
        last_seen = self.camera.last_seen_online_at

        if not self.camera.enabled:
            self._emit(
                CheckResult(
                    camera_id=self.camera.id,
                    status="unknown",
                    checked_at=checked_at,
                    error="Камера выключена",
                    seen_online_at=last_seen,
                )
            )
            return

        url = self.camera.rtsp_url.strip()
        if not is_valid_rtsp_url(url):
            self._emit(
                CheckResult(
                    camera_id=self.camera.id,
                    status="offline",
                    checked_at=checked_at,
                    error="Некорректный RTSP URL",
                    seen_online_at=last_seen,
                )
            )
            return

        if not ensure_binary_exists(config.FFPROBE_BIN):
            self._emit(
                CheckResult(
                    camera_id=self.camera.id,
                    status="offline",
                    checked_at=checked_at,
                    error=f"ffprobe не найден: {config.FFPROBE_BIN}",
                    seen_online_at=last_seen,
                )
            )
            return

        # Три уровня проверки:
        #   normal — здоровые/offline камеры: короткий таймаут.
        #   deep   — статус unknown без кода: даём больше времени, при тайм-ауте пишем 0x03.
        #   ultra  — статус unknown + 0x03: длинный таймаут, при тайм-ауте уходим в offline.
        prev_status = self.camera.status
        prev_error = (self.camera.last_error or "").strip()
        is_ultra = prev_status == "unknown" and prev_error == config.UNKNOWN_DEEP_FAIL_CODE
        is_deep = (not is_ultra) and prev_status == "unknown"

        if is_ultra:
            timeout_sec = max(1, config.CHECK_TIMEOUT_ULTRA_SEC)
        elif is_deep:
            timeout_sec = max(1, config.CHECK_TIMEOUT_DEEP_SEC)
        else:
            timeout_sec = max(1, config.CHECK_TIMEOUT_SEC)

        cmd = [
            config.FFPROBE_BIN,
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-rw_timeout",
            str(timeout_sec * 1_000_000),
            "-timeout",
            str(timeout_sec * 1_000_000),
            "-show_entries",
            "format=format_name",
            "-of",
            "default=nw=1:nk=1",
            url,
        ]
        try:
            proc = run_command(cmd, timeout_sec=max(2, timeout_sec + 2))
        except subprocess.TimeoutExpired:
            self._emit(self._timeout_result(checked_at, last_seen, is_deep, is_ultra))
            return
        except Exception as exc:
            self._emit(
                CheckResult(
                    camera_id=self.camera.id,
                    status="offline",
                    checked_at=checked_at,
                    error=str(exc),
                    seen_online_at=last_seen,
                )
            )
            return

        if proc.returncode == 0 and proc.stdout.strip():
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

        err = (proc.stderr or proc.stdout or f"Код {proc.returncode}").strip()
        if "timed out" in err.lower() or "timeout" in err.lower():
            self._emit(self._timeout_result(checked_at, last_seen, is_deep, is_ultra))
            return
        # Любая другая ошибка от ffprobe (отказ соединения, 401, 404, и т. п.) → offline.
        self._emit(
            CheckResult(
                camera_id=self.camera.id,
                status="offline",
                checked_at=checked_at,
                error=err[:500],
                seen_online_at=last_seen,
            )
        )

    def _timeout_result(
        self,
        checked_at: str,
        last_seen: str | None,
        is_deep: bool,
        is_ultra: bool,
    ) -> CheckResult:
        if is_ultra:
            # Финальная 2-минутная проверка не достучалась — уводим камеру в offline с понятной ошибкой.
            return CheckResult(
                camera_id=self.camera.id,
                status="offline",
                checked_at=checked_at,
                error=config.UNKNOWN_OFFLINE_FAIL_MESSAGE,
                seen_online_at=last_seen,
            )
        return CheckResult(
            camera_id=self.camera.id,
            status="unknown",
            checked_at=checked_at,
            error=config.UNKNOWN_DEEP_FAIL_CODE if is_deep else None,
            seen_online_at=last_seen,
        )


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
