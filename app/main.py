from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from app import config
from app.database.db import initialize_database
from app.database.repository import Repository
from app.ui.main_window import MainWindow


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run() -> int:
    configure_logging()
    initialize_database(config.DB_PATH)
    repo = Repository()
    if config.SEED_ON_EMPTY:
        repo.seed_demo_data()

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)

    window = MainWindow(repo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())

