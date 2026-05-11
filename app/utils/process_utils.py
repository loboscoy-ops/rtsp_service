from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass

_log = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


# Запущенная через Finder .app получает PATH=/usr/bin:/bin:/usr/sbin:/sbin
# и не видит brew-бинарей. Подкладываем сами оба обычных префикса.
_EXTRA_PATHS = (
    "/opt/homebrew/bin",   # Apple Silicon Homebrew
    "/usr/local/bin",      # Intel Homebrew
    "/usr/local/sbin",
)


def _resolve_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for prefix in _EXTRA_PATHS:
        candidate = Path(prefix) / name
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return str(candidate)
    return None


def ensure_binary_exists(binary: str) -> bool:
    candidate = (binary or "").strip()
    if not candidate:
        return False
    if "/" in candidate:
        path = Path(candidate).expanduser()
        return path.is_file() and path.stat().st_mode & 0o111 != 0
    return _resolve_binary(candidate) is not None


def resolve_binary(binary: str) -> str:
    """Возвращает абсолютный путь к бинарю (через PATH + brew-префиксы)
    или исходное имя — пусть subprocess сам падает с понятной ошибкой."""
    candidate = (binary or "").strip()
    if not candidate:
        return candidate
    if "/" in candidate:
        return str(Path(candidate).expanduser())
    return _resolve_binary(candidate) or candidate


def run_command(cmd: list[str], timeout_sec: int) -> ProcessResult:
    if cmd:
        cmd = [resolve_binary(cmd[0]), *cmd[1:]]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    return ProcessResult(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def terminate_ffprobe_children() -> int:
    """Отправить SIGTERM прямым дочерним процессам ffprobe текущего процесса.

    Проверки камер блокируют поток QThreadPool внутри subprocess.run; при выходе
    из приложения это иначе держит окно десятки секунд или провоцирует «не отвечает».
    Затрагиваем только процессы, в командной строке которых есть ffprobe (не ffplay,
    не QtWebEngineProcess).
    """
    if sys.platform == "win32":
        return 0
    ppid = str(os.getpid())
    try:
        proc = subprocess.run(
            ["pgrep", "-P", ppid],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        _log.debug("terminate_ffprobe_children: pgrep не выполнен: %s", exc)
        return 0
    out = (proc.stdout or "").strip()
    if not out:
        return 0
    killed = 0
    for pid_str in out.splitlines():
        pid_str = pid_str.strip()
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        try:
            ps = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        args = (ps.stdout or "").strip().lower()
        if "ffprobe" not in args:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            _log.debug("terminate_ffprobe_children: pid=%s %s", pid, exc)
    return killed

