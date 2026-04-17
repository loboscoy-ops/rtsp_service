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

        cmd = [
            config.FFPROBE_BIN,
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-rw_timeout",
            str(max(1, config.CHECK_TIMEOUT_SEC) * 1_000_000),
            "-timeout",
            str(max(1, config.CHECK_TIMEOUT_SEC) * 1_000_000),
            "-show_entries",
            "format=format_name",
            "-of",
            "default=nw=1:nk=1",
            url,
        ]
        try:
            proc = run_command(cmd, timeout_sec=max(2, config.CHECK_TIMEOUT_SEC + 2))
        except subprocess.TimeoutExpired:
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
            return
        self._emit(
            CheckResult(
                camera_id=self.camera.id,
                status="offline",
                checked_at=checked_at,
                error=err[:500],
                seen_online_at=last_seen,
            )
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
