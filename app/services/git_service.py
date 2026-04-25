from __future__ import annotations

import shutil
import subprocess

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app import config


def _git_bin() -> str | None:
    return shutil.which("git") or ("/usr/bin/git" if shutil.which("/usr/bin/git") else None)


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


class _CheckJob(QRunnable):
    """Тихий фоновый job: только смотрит, есть ли новые коммиты в origin/main."""

    def __init__(self, finished_signal):
        super().__init__()
        self._finished = finished_signal
        self.setAutoDelete(True)

    def run(self) -> None:
        count, message = _do_check()
        try:
            self._finished.emit(int(count), str(message))
        except RuntimeError:
            pass


def _do_pull() -> tuple[bool, str]:
    git_bin = _git_bin()
    if not git_bin:
        return False, "git не найден в PATH. Установите Xcode Command Line Tools."

    project_dir = config.PROJECT_GIT_DIR
    if project_dir is None:
        return False, (
            "Не найден git-репозиторий проекта.\n"
            f"Сделайте: git clone {config.GITHUB_REPO_URL} ~/rtsp-camera-service\n"
            "или задайте RTSP_PROJECT_DIR в окружении."
        )

    try:
        subprocess.run(
            [git_bin, "-C", str(project_dir), "fetch", "--prune", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        proc = subprocess.run(
            [git_bin, "-C", str(project_dir), "merge", "--ff-only", "origin/main"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "git: таймаут"
    except subprocess.CalledProcessError as exc:
        return False, f"git fetch упал: {exc.stderr or exc.stdout or exc}"
    except Exception as exc:
        return False, f"git: {exc}"

    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    output = f"{project_dir}\n{output}".strip()
    return (proc.returncode == 0), output or f"код {proc.returncode}"


def _do_check() -> tuple[int, str]:
    """Возвращает (количество_коммитов_впереди, сообщение_или_ошибка).

    -1 как количество означает «не удалось проверить» (сетевая ошибка / нет
    git / нет репозитория) — UI в этом случае ничего не подсвечивает.
    """
    git_bin = _git_bin()
    if not git_bin:
        return -1, "git не найден"
    project_dir = config.PROJECT_GIT_DIR
    if project_dir is None:
        return -1, "нет .git"
    try:
        subprocess.run(
            [git_bin, "-C", str(project_dir), "fetch", "--prune", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        proc = subprocess.run(
            [git_bin, "-C", str(project_dir), "rev-list", "--count", "HEAD..origin/main"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        count = int((proc.stdout or "0").strip() or "0")
        return count, "ok"
    except subprocess.TimeoutExpired:
        return -1, "git fetch: таймаут"
    except subprocess.CalledProcessError as exc:
        return -1, f"git: {exc.stderr or exc.stdout or exc}"
    except Exception as exc:
        return -1, f"git: {exc}"


class GitPullService(QObject):
    finished = Signal(bool, str)
    updates_checked = Signal(int, str)  # (count, message); count == -1 → ошибка

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()

    def start(self) -> None:
        self._pool.start(_PullJob(self.finished))

    def check_updates(self) -> None:
        self._pool.start(_CheckJob(self.updates_checked))
