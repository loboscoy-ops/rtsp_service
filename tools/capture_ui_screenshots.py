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

    # Расширенный демо-набор объектов и камер для дашборда.
    demo_sites = [
        ("ЖК Диус", "U-DIUS-001", "55.8990, 37.4290", "online"),
        ("ЖК УЛИЦА АКАДЕМИКА ФРАНКА", "U-FRAN-014", "55.7522, 37.4733", "online"),
        ("ТРК Эс Ай Эс", "U-SIS-021", "55.6740, 37.2710", "online"),
        ("Школа Эс Ай Эс", "U-SIS-022", "55.4080, 37.7530", "online"),
        ("Циолковского", "U-COSM-009", "55.9120, 37.8270", "offline"),
        ("Балашиха", "U-BAL-033", "55.7960, 37.9380", "offline"),
    ]
    for idx, (site_name, uin, coords, status) in enumerate(demo_sites, start=1):
        for cam_idx in range(1, 4 if idx % 2 else 3):
            cam_id, _ = repo.upsert_camera_for_object_name(
                object_name=site_name,
                camera_identifier=f"{site_name.lower()[:5]}-cam-{cam_idx:02d}",
                camera_name=f"Камера {cam_idx}",
                group_name="Двор" if cam_idx % 2 else "Периметр",
                rtsp_url=f"rtsp://demo:demo@10.0.{idx}.{cam_idx}/stream",
                enabled=True,
                gps_coords=coords,
                uin=uin,
            )
            override = status if cam_idx == 1 else "online"
            repo.update_camera_status(
                camera_id=cam_id,
                status=override,
                last_checked_at="2026-05-12T13:55:00",
                last_error=None if override == "online" else "demo offline",
                last_seen_online_at="2026-05-12T13:55:00" if override == "online" else None,
                last_ping_ok=override == "online",
                last_ping_ms=120 if override == "online" else None,
            )

    cams = repo.list_cameras()
    if cams:
        cam = cams[0]
        if not (cam.gps_coords or "").strip():
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
