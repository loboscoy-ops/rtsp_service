from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path

# До любого импорта PySide6 / QtWebEngine: в собранном .app на macOS Chromium
# helper часто завершается из‑за sandbox, окно не появляется и traceback не виден.
if sys.platform == "darwin":
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")


def _ensure_qtwebengine_helper_path() -> None:
    """В собранном .app PySide6 кладёт QtWebEngineProcess.app внутрь
    `QtWebEngineCore.framework/Versions/Resources/Helpers/...`, а Qt ищет его
    в `QtWebEngineCore.framework/Helpers/...`. Прописываем явный путь через
    `QTWEBENGINEPROCESS_PATH`, иначе на macOS приложение падает с SIGABRT
    при создании первого QWebEnginePage.
    """
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return
    if os.environ.get("QTWEBENGINEPROCESS_PATH"):
        return
    try:
        bundle = Path(sys.executable).resolve().parents[1]  # .../Contents
    except IndexError:
        return
    fw = bundle / "Frameworks" / "PySide6" / "Qt" / "lib" / "QtWebEngineCore.framework"
    candidates = [
        fw / "Helpers" / "QtWebEngineProcess.app" / "Contents" / "MacOS" / "QtWebEngineProcess",
        fw / "Versions" / "A" / "Helpers" / "QtWebEngineProcess.app" / "Contents" / "MacOS" / "QtWebEngineProcess",
        fw / "Versions" / "Resources" / "Helpers" / "QtWebEngineProcess.app" / "Contents" / "MacOS" / "QtWebEngineProcess",
    ]
    for path in candidates:
        if path.exists():
            os.environ["QTWEBENGINEPROCESS_PATH"] = str(path)
            return


_ensure_qtwebengine_helper_path()

from PySide6.QtWidgets import QApplication

from app import config
from app.database.db import initialize_database
from app.database.repository import Repository
from app.ui.constants import APP_GLOBAL_QSS
from app.ui.main_window import MainWindow


def _append_crash_log(text: str) -> None:
    """Если GUI падает до окна логов — смотрите этот файл."""
    try:
        log_dir = Path.home() / "Library/Application Support/RTSPCameraMonitor/logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "crash.log"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
    except OSError:
        pass


def configure_logging() -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if getattr(sys, "frozen", False):
        try:
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(config.LOG_DIR / "app.log", encoding="utf-8")
            fh.setFormatter(logging.Formatter(fmt))
            handlers.append(fh)
        except OSError:
            pass
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)


def run() -> int:
    configure_logging()
    initialize_database(config.DB_PATH)
    repo = Repository()
    if config.SEED_ON_EMPTY:
        repo.seed_demo_data()

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    app.setStyleSheet(APP_GLOBAL_QSS)

    window = MainWindow(repo)
    app.aboutToQuit.connect(lambda: window.ffplay.terminate_all())
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except SystemExit:
        raise
    except BaseException:
        _append_crash_log(traceback.format_exc())
        raise

