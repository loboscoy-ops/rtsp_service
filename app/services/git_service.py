from __future__ import annotations

import shutil
import subprocess

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app import config


class _PullJob(QRunnable):
    def __init__(self, finished_signal):
        super().__init__()
        self._finished = finished_signal
        self.setAutoDelete(True)

    def run(self) -> None:
        ok, message = _do_pull()
        try:
            self._finished.emit(ok, message)
        except RuntimeError:
            pass


def _do_pull() -> tuple[bool, str]:
    git_bin = shutil.which("git")
    if not git_bin:
        return False, "git не найден в PATH. Установите Xcode Command Line Tools."

    git_dir = config.ROOT_DIR / ".git"
    if not git_dir.exists():
        return False, f"Директория {config.ROOT_DIR} не является git-репозиторием."

    try:
        proc = subprocess.run(
            [git_bin, "-C", str(config.ROOT_DIR), "pull", "--ff-only", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "git pull: таймаут"
    except Exception as exc:
        return False, f"git pull: {exc}"

    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return (proc.returncode == 0), output.strip() or f"код {proc.returncode}"


class GitPullService(QObject):
    finished = Signal(bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()

    def start(self) -> None:
        self._pool.start(_PullJob(self.finished))
