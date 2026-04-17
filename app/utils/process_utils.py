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


def ensure_binary_exists(binary: str) -> bool:
    candidate = (binary or "").strip()
    if not candidate:
        return False
    if "/" in candidate:
        path = Path(candidate).expanduser()
        return path.is_file() and path.stat().st_mode & 0o111 != 0
    return bool(shutil.which(candidate))


def run_command(cmd: list[str], timeout_sec: int) -> ProcessResult:
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

