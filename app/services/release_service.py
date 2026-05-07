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
import socket
import subprocess
import sys
import tempfile
import time
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
RELEASES_TAG_URL_TMPL = f"https://api.github.com/repos/{REPO_SLUG}/releases/tags/{{tag}}"
RELEASES_PAGE_URL = f"https://github.com/{REPO_SLUG}/releases"

USER_AGENT = f"rtsp-camera-monitor/{config.APP_VERSION}"
# GitHub по HTTPS на медленных/фильтруемых сетях часто не успевает за 15 с (SSL handshake).
HTTP_TIMEOUT_SEC = int(os.getenv("RTSP_HTTP_TIMEOUT_SEC", "60"))
HTTP_CONNECT_RETRIES = max(1, int(os.getenv("RTSP_HTTP_CONNECT_RETRIES", "3")))
HTTP_RETRY_BASE_DELAY_SEC = float(os.getenv("RTSP_HTTP_RETRY_DELAY_SEC", "2.0"))
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


def _format_fetch_error(exc: Exception) -> str:
    """Краткое сообщение для UI; техподробности урезаем."""
    raw = str(exc).strip()
    low = raw.lower()
    if "handshake" in low or "_ssl.c" in low:
        return (
            "Не удалось установить защищённое соединение с GitHub (таймаут SSL). "
            "Проверьте интернет, VPN, прокси или файрвол; позже повторите проверку обновлений.\n\n"
            f"Подробности: {raw[:280]}"
        )
    if "timed out" in low or "timeout" in low:
        return (
            "Превышено время ожидания ответа от GitHub. Сеть может быть перегружена или недоступна.\n\n"
            f"Подробности: {raw[:280]}"
        )
    if "certificate" in low or "ssl" in low:
        return (
            "Ошибка проверки SSL-сертификата при обращении к GitHub.\n\n"
            f"Подробности: {raw[:280]}"
        )
    if "name or service not known" in low or "getaddrinfo failed" in low:
        return "Не удалось разрешить имя сервера GitHub (DNS). Проверьте доступ в интернет."
    return raw[:500] if raw else repr(exc)


def _http_get(url: str, accept: Optional[str] = None, timeout: Optional[int] = None) -> bytes:
    """GET с несколькими попытками при обрыве соединения / таймауте."""
    deadline = timeout if timeout is not None else HTTP_TIMEOUT_SEC
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = Request(url, headers=headers)

    last_exc: Exception | None = None
    for attempt in range(HTTP_CONNECT_RETRIES):
        try:
            with urlopen(req, timeout=deadline) as resp:
                return resp.read()
        except HTTPError:
            raise
        except (URLError, TimeoutError, OSError, socket.timeout) as exc:
            last_exc = exc
            if attempt < HTTP_CONNECT_RETRIES - 1:
                time.sleep(HTTP_RETRY_BASE_DELAY_SEC * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def fetch_remote_version() -> RemoteVersion:
    """Парсит APP_VERSION из remote app/config.py."""
    local = config.APP_VERSION
    try:
        body = _http_get(RAW_CONFIG_URL).decode("utf-8", errors="replace")
    except HTTPError as exc:
        return RemoteVersion(
            version="", is_newer=False, local=local,
            error=_format_fetch_error(exc),
        )
    except (URLError, TimeoutError, OSError, socket.timeout) as exc:
        return RemoteVersion(
            version="", is_newer=False, local=local,
            error=_format_fetch_error(exc),
        )
    except Exception as exc:
        return RemoteVersion(
            version="", is_newer=False, local=local,
            error=_format_fetch_error(exc),
        )
    m = _VERSION_RE.search(body)
    if not m:
        return RemoteVersion(
            version="", is_newer=False, local=local,
            error="не нашли APP_VERSION в remote config.py",
        )
    remote = m.group(1).strip()
    return RemoteVersion(version=remote, is_newer=is_newer(remote, local), local=local)


def _parse_release_json(data: dict) -> Optional[ReleaseAsset]:
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


def fetch_release_by_tag(version_or_tag: str) -> Optional[ReleaseAsset]:
    """Релиз по тегу, например «0.1.39» или «v0.1.39». 404 → None."""
    raw_tag = (version_or_tag or "").strip().lstrip("vV")
    if not raw_tag:
        return None
    url = RELEASES_TAG_URL_TMPL.format(tag=f"v{raw_tag}")
    try:
        body = _http_get(url, accept="application/vnd.github+json")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        return None
    except (URLError, TimeoutError, OSError, socket.timeout):
        return None
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return _parse_release_json(data)


def fetch_latest_release() -> Optional[ReleaseAsset]:
    """Достаёт последний релиз и ссылку на .dmg-asset (если есть)."""
    try:
        raw = _http_get(RELEASES_LATEST_URL, accept="application/vnd.github+json")
    except (HTTPError, URLError, TimeoutError, OSError, socket.timeout):
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return _parse_release_json(data)


def pick_release_for_upgrade(remote_version: str, local_version: str) -> Optional[ReleaseAsset]:
    """Выбирает .dmg для обновления с local_version до remote_version.

    1) Точный релиз v{remote_version} с ассетом .dmg.
    2) Иначе «latest», но только если он не старее remote_version
       (код в main уже 0.1.40, а latest ещё 0.1.39 — не предлагаем старый .dmg).
    """
    if not remote_version:
        return None
    exact = fetch_release_by_tag(remote_version)
    if exact and exact.dmg_url and is_newer(exact.tag, local_version):
        return exact
    latest = fetch_latest_release()
    if (
        latest
        and latest.dmg_url
        and is_newer(latest.tag, local_version)
        and not is_newer(remote_version, latest.tag)
    ):
        return latest
    return None


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


_PROGRESS_CHUNK = 256 * 1024  # 256 KB — достаточно частые тики UI без лишней нагрузки


def download_to_temp(url: str, progress_cb=None) -> Path:
    """Скачивает url во временный файл, опционально отдавая прогресс.

    progress_cb(bytes_downloaded, total_bytes_or_minus_one) вызывается
    из этого же потока. Если сервер не отдал Content-Length, total = -1.
    """
    fd, name = tempfile.mkstemp(suffix=".dmg", prefix="rtsp-update-")
    os.close(fd)
    target = Path(name)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Exception | None = None
    for attempt in range(HTTP_CONNECT_RETRIES):
        try:
            with urlopen(req, timeout=DOWNLOAD_TIMEOUT_SEC) as resp, open(target, "wb") as out:
                length_header = resp.headers.get("Content-Length") if hasattr(resp, "headers") else None
                try:
                    total = int(length_header) if length_header else -1
                except (TypeError, ValueError):
                    total = -1
                downloaded = 0
                if progress_cb is not None:
                    try:
                        progress_cb(0, total)
                    except Exception:
                        pass
                while True:
                    chunk = resp.read(_PROGRESS_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb is not None:
                        try:
                            progress_cb(downloaded, total)
                        except Exception:
                            pass
            return target
        except HTTPError:
            raise
        except (URLError, TimeoutError, OSError, socket.timeout) as exc:
            last_exc = exc
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < HTTP_CONNECT_RETRIES - 1:
                time.sleep(HTTP_RETRY_BASE_DELAY_SEC * (attempt + 1))
    assert last_exc is not None
    raise last_exc


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
    def __init__(self, dmg_url: str, progress_signal, stage_signal, finished_signal):
        super().__init__()
        self._url = dmg_url
        self._progress = progress_signal
        self._stage = stage_signal
        self._finished = finished_signal
        self.setAutoDelete(True)

    def run(self) -> None:
        self._safe_emit(self._stage, "download")
        try:
            dmg = download_to_temp(self._url, progress_cb=self._on_progress)
        except Exception as exc:
            self._safe_emit(self._finished, False, f"download: {exc}")
            return
        self._safe_emit(self._stage, "install")
        try:
            ok, msg = install_dmg(dmg)
        finally:
            try:
                dmg.unlink(missing_ok=True)
            except Exception:
                pass
        self._safe_emit(self._finished, bool(ok), str(msg))

    def _on_progress(self, downloaded: int, total: int) -> None:
        self._safe_emit(self._progress, int(downloaded), int(total))

    @staticmethod
    def _safe_emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            # Получатель уничтожен (закрытие приложения) — молча.
            pass


class ReleaseService(QObject):
    """Фоновая проверка версии и установка .dmg для собранного .app."""

    version_checked = Signal(str, bool, str)   # (remote_version, is_newer, error_message)
    download_progress = Signal(int, int)       # (bytes_downloaded, total_or_-1)
    install_stage = Signal(str)                # "download" | "install"
    install_finished = Signal(bool, str)       # (ok, message)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()

    def check(self) -> None:
        self._pool.start(_CheckJob(self.version_checked))

    def install(self, dmg_url: str) -> None:
        self._pool.start(
            _InstallJob(
                dmg_url,
                self.download_progress,
                self.install_stage,
                self.install_finished,
            )
        )
