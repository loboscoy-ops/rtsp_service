"""Проверка/скачивание новой версии для собранного .app.

Работает по двум независимым каналам:
  • Канал «версия»  — читаем `APP_VERSION` из `app/config.py` через
    raw.githubusercontent.com. Это даёт нам факт «есть свежая сборка»
    даже если релиз ещё не выкладывался — UI просто подсветит кнопку.
  • Канал «релиз»   — GitHub Releases API. Если для этой версии есть
    релиз с прикреплённым .dmg, мы можем скачать и установить его
    в /Applications автоматически.

Сетевые операции — через urllib (без сторонних зависимостей), все вызовы
обёрнуты в QRunnable и эмитят результат через сигналы.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app import config


REPO_SLUG = os.getenv("RTSP_REPO_SLUG", "loboscoy-ops/rtsp_service")
RAW_CONFIG_URL = (
    f"https://raw.githubusercontent.com/{REPO_SLUG}/main/app/config.py"
)
RELEASES_LATEST_URL = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPO_SLUG}/releases"

USER_AGENT = f"rtsp-camera-monitor/{config.APP_VERSION}"
HTTP_TIMEOUT_SEC = 15
DOWNLOAD_TIMEOUT_SEC = 600

_VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]', re.MULTILINE)


# --- структуры -------------------------------------------------------------


@dataclass
class RemoteVersion:
    version: str          # «голая» версия из remote config.py
    is_newer: bool        # remote > local
    local: str
    error: Optional[str] = None  # текст ошибки сети (если is_newer = False по причине ошибки)


@dataclass
class ReleaseAsset:
    tag: str
    dmg_url: Optional[str]
    page_url: str
    body: str


# --- сравнение версий ------------------------------------------------------


def _version_tuple(s: str) -> tuple[int, ...]:
    """`0.1.37` → (0,1,37). Нечисловые куски игнорируем (rc/beta — отбрасываем)."""
    parts: list[int] = []
    for chunk in re.split(r"[.\-+]", s.strip().lstrip("vV")):
        m = re.match(r"^(\d+)", chunk)
        if m:
            parts.append(int(m.group(1)))
        else:
            break
    return tuple(parts) if parts else (0,)


def is_newer(remote: str, local: str) -> bool:
    return _version_tuple(remote) > _version_tuple(local)


# --- сетевые вызовы --------------------------------------------------------


def _http_get(url: str, accept: Optional[str] = None, timeout: int = HTTP_TIMEOUT_SEC) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_remote_version() -> RemoteVersion:
    """Парсит APP_VERSION из remote app/config.py."""
    local = config.APP_VERSION
    try:
        body = _http_get(RAW_CONFIG_URL).decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return RemoteVersion(version="", is_newer=False, local=local, error=str(exc))
    m = _VERSION_RE.search(body)
    if not m:
        return RemoteVersion(
            version="", is_newer=False, local=local,
            error="не нашли APP_VERSION в remote config.py",
        )
    remote = m.group(1).strip()
    return RemoteVersion(version=remote, is_newer=is_newer(remote, local), local=local)


def fetch_latest_release() -> Optional[ReleaseAsset]:
    """Достаёт последний релиз и ссылку на .dmg-asset (если есть)."""
    try:
        raw = _http_get(RELEASES_LATEST_URL, accept="application/vnd.github+json")
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    tag = (data.get("tag_name") or "").lstrip("vV")
    page = data.get("html_url") or RELEASES_PAGE_URL
    body = data.get("body") or ""
    dmg_url: Optional[str] = None
    for asset in data.get("assets") or []:
        name = (asset.get("name") or "").lower()
        if name.endswith(".dmg"):
            dmg_url = asset.get("browser_download_url")
            break
    if not tag:
        return None
    return ReleaseAsset(tag=tag, dmg_url=dmg_url, page_url=page, body=body)


# --- установка .dmg --------------------------------------------------------


def _bundle_path_from_executable() -> Optional[Path]:
    exe = Path(sys.executable)
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def install_dmg(dmg_path: Path) -> tuple[bool, str]:
    """Монтирует .dmg, копирует .app поверх текущего bundle и размонтирует.

    Возвращает (ok, message). Если .app сейчас лежит вне `/Applications`
    (например, в Downloads), мы заменим именно тот bundle, из которого
    приложение запустилось.
    """
    if sys.platform != "darwin":
        return False, "Автоустановка .dmg доступна только на macOS"

    bundle = _bundle_path_from_executable()
    if bundle is None:
        return False, "Не удалось определить путь к текущему .app bundle"

    mount_root = Path(tempfile.mkdtemp(prefix="rtsp-update-"))
    try:
        attach = subprocess.run(
            ["hdiutil", "attach", str(dmg_path), "-mountpoint", str(mount_root),
             "-nobrowse", "-quiet"],
            capture_output=True, text=True, timeout=120,
        )
        if attach.returncode != 0:
            return False, f"hdiutil attach: {attach.stderr or attach.stdout}".strip()

        # Ищем единственный *.app внутри образа.
        candidates = list(mount_root.glob("*.app"))
        if not candidates:
            return False, "В .dmg не нашли .app"
        new_app = candidates[0]

        # Копируем во временный путь, потом атомарно меняем местами,
        # чтобы текущий процесс не дёрнулся на полпути.
        staging = bundle.parent / f".{bundle.name}.new-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(new_app, staging, symlinks=True)

        backup = bundle.parent / f".{bundle.name}.old-{os.getpid()}"
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if bundle.exists():
            os.rename(bundle, backup)
        os.rename(staging, bundle)
        # Снимаем карантин с нового .app, чтобы Gatekeeper не ругался.
        subprocess.run(
            ["xattr", "-dr", "com.apple.quarantine", str(bundle)],
            capture_output=True, text=True,
        )
        # Старую копию удаляем в фоне (после следующего запуска тоже не критично).
        try:
            shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            pass

        return True, str(bundle)
    finally:
        subprocess.run(
            ["hdiutil", "detach", str(mount_root), "-quiet"],
            capture_output=True, text=True,
        )


def download_to_temp(url: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=".dmg", prefix="rtsp-update-")
    os.close(fd)
    target = Path(name)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=DOWNLOAD_TIMEOUT_SEC) as resp, open(target, "wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)
    return target


# --- Qt-обёртка ------------------------------------------------------------


class _CheckJob(QRunnable):
    def __init__(self, finished_signal):
        super().__init__()
        self._finished = finished_signal
        self.setAutoDelete(True)

    def run(self) -> None:
        rv = fetch_remote_version()
        try:
            self._finished.emit(rv.version, bool(rv.is_newer), rv.error or "")
        except RuntimeError:
            pass


class _InstallJob(QRunnable):
    def __init__(self, dmg_url: str, finished_signal):
        super().__init__()
        self._url = dmg_url
        self._finished = finished_signal
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            dmg = download_to_temp(self._url)
        except Exception as exc:
            self._emit(False, f"download: {exc}")
            return
        try:
            ok, msg = install_dmg(dmg)
        finally:
            try:
                dmg.unlink(missing_ok=True)
            except Exception:
                pass
        self._emit(ok, msg)

    def _emit(self, ok: bool, msg: str) -> None:
        try:
            self._finished.emit(bool(ok), str(msg))
        except RuntimeError:
            pass


class ReleaseService(QObject):
    """Фоновая проверка версии и установка .dmg для собранного .app."""

    version_checked = Signal(str, bool, str)   # (remote_version, is_newer, error_message)
    install_finished = Signal(bool, str)       # (ok, message)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()

    def check(self) -> None:
        self._pool.start(_CheckJob(self.version_checked))

    def install(self, dmg_url: str) -> None:
        self._pool.start(_InstallJob(dmg_url, self.install_finished))
