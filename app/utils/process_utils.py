from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from dataclasses import dataclass


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

