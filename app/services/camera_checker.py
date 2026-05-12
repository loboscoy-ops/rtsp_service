from __future__ import annotations

import subprocess
from collections import Counter, deque
from dataclasses import dataclass
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


# --- worker ----------------------------------------------------------------


class CameraCheckWorker(QRunnable):
    """Запускает ping + ffprobe и эмитит CheckResult через долгоживущий сигнал."""

    def __init__(self, camera: CameraModel, result_signal, started_signal):
        super().__init__()
        self.camera = camera
        self._result_signal = result_signal
        self._started_signal = started_signal
        self._ping_result: Optional[tuple[bool, Optional[int]]] = None
        self.setAutoDelete(True)

    # -- public entry point --------------------------------------------------

    def run(self) -> None:
        checked_at = now_iso()
        last_seen = self.camera.last_seen_online_at
        self._emit_started()

        self._ping_result = self._run_ping()

        if not self.camera.enabled:
            self._emit(self._offline_result(checked_at, last_seen, error="Камера выключена"))
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

        self._probe_and_emit(url, checked_at, last_seen)

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
        checked_at: str,
        last_seen: Optional[str],
    ) -> None:
        fast_timeout_sec = max(
            1,
            int(getattr(config, "CHECK_FAST_TIMEOUT_SEC", config.CHECK_TIMEOUT_SEC)),
        )
        deep_timeout_sec = max(
            1,
            int(getattr(config, "CHECK_DEEP_TIMEOUT_SEC", config.CHECK_TIMEOUT_SEC)),
        )

        # Этап 1: быстрый проход по TCP для всех камер.
        fast_outcome = self._probe_once(url, "tcp", fast_timeout_sec)
        if fast_outcome.kind == "online":
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
        if fast_outcome.kind == "error":
            self._emit(self._offline_result(checked_at, last_seen, error=fast_outcome.error[:500]))
            return
        if fast_outcome.kind == "exception":
            self._emit(self._offline_result(checked_at, last_seen, error=fast_outcome.error))
            return

        # Часть камер (особенно Dahua/Hikvision со стримом cam/realmonitor) отвечает
        # на TCP медленно или только по UDP. ffplay сам пробует оба транспорта,
        # ffprobe — нет, поэтому в углубленном этапе пробуем TCP, затем UDP.
        for transport in ("tcp", "udp"):
            outcome = self._probe_once(url, transport, deep_timeout_sec)
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

        # Оба транспорта в timeout — считаем камеру offline.
        self._emit(
            self._offline_result(
                checked_at,
                last_seen,
                error=config.CHECK_TIMEOUT_FAIL_MESSAGE,
            )
        )

    def _probe_once(
        self,
        url: str,
        transport: str,
        timeout_sec: int,
    ) -> "_ProbeOutcome":
        cmd = self._build_ffprobe_cmd(url, transport, timeout_sec)
        try:
            proc = run_command(cmd, timeout_sec=max(2, timeout_sec + 2))
        except subprocess.TimeoutExpired:
            return _ProbeOutcome(kind="timeout", error="")
        except Exception as exc:
            return _ProbeOutcome(kind="exception", error=str(exc))

        if proc.returncode == 0:
            raw = (proc.stdout or "").strip().lower()
            if not raw:
                return _ProbeOutcome(
                    kind="error",
                    error=config.REQUIRED_H264_ERROR_TEXT,
                )
            codec = raw.splitlines()[0].strip()
            if codec in config.REQUIRED_VIDEO_CODECS:
                return _ProbeOutcome(kind="online", error="")
            return _ProbeOutcome(
                kind="error",
                error=config.REQUIRED_H264_ERROR_TEXT,
            )

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
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=nw=1:nk=1",
            url,
        ]

    # -- factories ----------------------------------------------------------

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

    # -- emit ---------------------------------------------------------------

    def _emit_started(self) -> None:
        try:
            self._started_signal.emit(
                int(self.camera.id),
                int(self.camera.object_id),
                self.camera.object_name or "",
                self.camera.camera_name or "",
            )
        except RuntimeError:
            pass

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
    camera_check_started = Signal(int, int, str, str)

    def __init__(self) -> None:
        super().__init__()
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(max(1, config.MAX_CONCURRENT_CHECKS))
        self._pending: deque[CameraModel] = deque()
        self._active_by_object: Counter[int] = Counter()
        self._active_by_host: Counter[str] = Counter()
        self._active_ids: set[int] = set()
        self._inflight_meta: dict[int, tuple[int, str]] = {}
        self._max_per_object = max(1, config.MAX_CONCURRENT_CHECKS_PER_OBJECT)
        self._max_per_host = max(1, config.MAX_CONCURRENT_CHECKS_PER_HOST)
        self.camera_checked.connect(self._on_camera_finished)

    def check_camera(self, camera: CameraModel) -> None:
        self._pending.append(camera)
        self._dispatch_pending()

    def check_many(self, cameras: list[CameraModel]) -> None:
        for cam in cameras:
            self._pending.append(cam)
        self._dispatch_pending()

    def clear_pending(self) -> None:
        self._pending.clear()

    def _host_key(self, camera: CameraModel) -> str:
        return (host_from_rtsp_url(camera.rtsp_url) or "").strip().lower()

    def _can_start(self, camera: CameraModel) -> bool:
        object_id = int(camera.object_id)
        if self._active_by_object[object_id] >= self._max_per_object:
            return False
        host = self._host_key(camera)
        if host and self._active_by_host[host] >= self._max_per_host:
            return False
        return True

    def _mark_started(self, camera: CameraModel) -> None:
        cam_id = int(camera.id)
        object_id = int(camera.object_id)
        host = self._host_key(camera)
        self._active_ids.add(cam_id)
        self._active_by_object[object_id] += 1
        if host:
            self._active_by_host[host] += 1
        self._inflight_meta[cam_id] = (object_id, host)

    def _dispatch_pending(self) -> None:
        if not self._pending:
            return
        retries_left = len(self._pending)
        while self._pending and self.pool.activeThreadCount() < self.pool.maxThreadCount():
            camera = self._pending.popleft()
            cam_id = int(camera.id)
            if cam_id in self._active_ids:
                retries_left -= 1
                if retries_left <= 0:
                    break
                continue
            if not self._can_start(camera):
                self._pending.append(camera)
                retries_left -= 1
                if retries_left <= 0:
                    break
                continue
            self._mark_started(camera)
            worker = CameraCheckWorker(camera, self.camera_checked, self.camera_check_started)
            self.pool.start(worker)
            retries_left = len(self._pending)

    def _on_camera_finished(self, result: CheckResult) -> None:
        cam_id = int(result.camera_id)
        meta = self._inflight_meta.pop(cam_id, None)
        if meta is not None:
            object_id, host = meta
            if self._active_by_object[object_id] > 0:
                self._active_by_object[object_id] -= 1
            if host and self._active_by_host[host] > 0:
                self._active_by_host[host] -= 1
        self._active_ids.discard(cam_id)
        self._dispatch_pending()
