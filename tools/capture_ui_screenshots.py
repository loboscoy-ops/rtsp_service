#!/usr/bin/env python3
"""Сохранить PNG скриншоты разделов UI (для документации / превью).

Запуск из корня репозитория:
  .venv/bin/python tools/capture_ui_screenshots.py
  .venv/bin/python tools/capture_ui_screenshots.py --out docs/ui-screenshots

Использует отдельную временную БД (docs/.screenshot_db), не трогает data/rtsp_monitor.db.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _prepare_env() -> Path:
    db_dir = ROOT / "docs" / ".screenshot_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "capture.db"
    if db_path.exists():
        db_path.unlink()
    os.environ["RTSP_APP_DB_PATH"] = str(db_path)
    os.environ["RTSP_SEED_ON_EMPTY"] = "1"
    return db_path


def _save_grab(widget, path: Path) -> None:
    from PySide6.QtWidgets import QWidget

    if not isinstance(widget, QWidget):
        return
    widget.repaint()
    pm = widget.grab()
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = pm.save(str(path), "PNG")
    if not ok:
        raise RuntimeError(f"Не удалось сохранить {path}")


def main() -> int:
    sys.path.insert(0, str(ROOT))
    _prepare_env()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QToolBar

    from app import config
    from app.database.db import initialize_database
    from app.database.repository import Repository
    from app.main import configure_logging
    from app.services.import_service import ImportService
    from app.services.template_service import TemplateService
    from app.ui.constants import APP_GLOBAL_QSS
    from app.ui.dialogs.camera_dialog import CameraDialog
    from app.ui.dialogs.import_dialog import ImportDialog
    from app.ui.dialogs.object_dialog import ObjectDialog
    from app.ui.main_window import MainWindow

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "docs" / "ui-screenshots",
        help="Каталог для PNG",
    )
    args = parser.parse_args()
    out: Path = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    configure_logging()
    initialize_database(config.DB_PATH)
    repo = Repository()
    repo.seed_demo_data()

    # Точка на карте для демо-маркера (Москва).
    cams = repo.list_cameras()
    if cams:
        cam = cams[0]
        repo.update_camera(
            cam.id,
            cam.object_id,
            cam.camera_identifier,
            cam.camera_name,
            cam.group_name,
            cam.rtsp_url,
            cam.enabled,
            gps_coords="55.751244, 37.618423",
            uin=cam.uin or "",
        )

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setStyleSheet(APP_GLOBAL_QSS)

    win = MainWindow(repo)
    win.timer.stop()
    win.error_text.setPlainText(
        "Демо: «КПП вход» — offline (0x00)\n"
        "Демо: проверка RTSP — таймаут\n"
        "(текст панели ошибок для скриншота)"
    )
    win.show()
    win.raise_()
    win.activateWindow()

    import_service = ImportService(repo)
    template_service = TemplateService()

    def capture() -> None:
        QApplication.processEvents()

        _save_grab(win, out / "01-glavnoe-okno.png")

        tb = win.findChild(QToolBar)
        if tb:
            _save_grab(tb, out / "02-panel-instrumentov.png")

        objects_wrap = win.sidebar.parentWidget()
        if objects_wrap:
            _save_grab(objects_wrap, out / "03-obekty.png")

        table_wrap = win.table.parentWidget()
        if table_wrap:
            _save_grab(table_wrap, out / "04-kamery.png")

        map_wrap = win.map_view.parentWidget()
        if map_wrap:
            _save_grab(map_wrap, out / "05-karta.png")

        err_wrap = win.error_text.parentWidget()
        if err_wrap:
            _save_grab(err_wrap, out / "06-oshibki.png")

        if win.statusBar():
            _save_grab(win.statusBar(), out / "07-statusnaya-stroka.png")

        win.dashboard_view_btn.click()
        QApplication.processEvents()
        _save_grab(win, out / "11-dashboard.png")
        win.cameras_view_btn.click()
        QApplication.processEvents()

        # Диалоги (по одному кадру каждый).
        od = ObjectDialog(win, initial_name="Новый объект")
        od.show()
        QApplication.processEvents()
        _save_grab(od, out / "08-dialog-obekt.png")
        od.close()

        objs = repo.list_objects()
        cd = CameraDialog(objects=objs, parent=win, camera=None)
        cd.show()
        QApplication.processEvents()
        _save_grab(cd, out / "09-dialog-kamera.png")
        cd.close()

        imp = ImportDialog(import_service, template_service, parent=win)
        imp.show()
        QApplication.processEvents()
        _save_grab(imp, out / "10-dialog-import-formy.png")
        imp.close()

        print(out)
        app.quit()

    QTimer.singleShot(1600, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
