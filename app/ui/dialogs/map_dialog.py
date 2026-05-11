from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from app.ui.widgets.camera_map import CameraMapView, markers_payload

if TYPE_CHECKING:
    from app.database.models import CameraModel


class MapDialog(QDialog):
    """Окно с картой OSM и маркерами камер по gps_coords.

    Сигналы:
      open_camera_requested(int) — пользователь хочет открыть стрим камеры.
    """

    open_camera_requested = Signal(int)

    def __init__(self, cameras: list["CameraModel"], *, object_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Карта — {object_label}")
        self.resize(720, 520)

        markers = markers_payload(cameras)
        skipped = len(cameras) - len(markers)

        layout = QVBoxLayout(self)
        hint = QLabel(
            f"Камер на карте: {len(markers)} из {len(cameras)}"
            + (f" (без координат: {skipped})" if skipped else "")
        )
        layout.addWidget(hint)

        self._map_view = CameraMapView(self)
        self._map_view.open_camera_requested.connect(self.open_camera_requested)
        self._map_view.set_cameras(list(cameras))
        layout.addWidget(self._map_view)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._install_escape_shortcut()

    def _install_escape_shortcut(self) -> None:
        # QWebEngineView перехватывает фокус и Esc туда не доходит как QKeyEvent в QDialog.
        # WindowShortcut гарантирует доставку именно в это окно.
        sc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(self.reject)
