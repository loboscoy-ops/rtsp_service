from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from app.database.models import CameraModel, ObjectModel
from app.utils.validators import is_valid_rtsp_url


@dataclass
class CameraFormData:
    object_id: int
    camera_identifier: str
    camera_name: str
    group_name: str
    gps_coords: str
    uin: str
    rtsp_url: str
    enabled: bool


def _field_text(camera: CameraModel | None, attr: str) -> str:
    if camera is None:
        return ""
    raw = getattr(camera, attr, None)
    return raw if isinstance(raw, str) else ""


class CameraDialog(QDialog):
    def __init__(
        self,
        objects: list[ObjectModel],
        parent=None,
        camera: CameraModel | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Камера")
        self.resize(460, 280)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._objects = objects

        self.object_combo = QComboBox()
        for obj in objects:
            self.object_combo.addItem(obj.name, obj.id)

        self.identifier_edit = QLineEdit(_field_text(camera, "camera_identifier"))
        self.name_edit = QLineEdit(_field_text(camera, "camera_name"))
        self.group_edit = QLineEdit(_field_text(camera, "group_name"))
        self.gps_edit = QLineEdit(_field_text(camera, "gps_coords"))
        self.gps_edit.setPlaceholderText("например: 55.7522, 37.6156")
        self.uin_edit = QLineEdit(_field_text(camera, "uin"))
        self.uin_edit.setPlaceholderText("УИН объекта (опционально)")
        self.rtsp_edit = QLineEdit(_field_text(camera, "rtsp_url"))
        self.enabled_check = QCheckBox("Камера активна")
        self.enabled_check.setChecked(camera.enabled if camera else True)

        if camera:
            for idx in range(self.object_combo.count()):
                if int(self.object_combo.itemData(idx)) == camera.object_id:
                    self.object_combo.setCurrentIndex(idx)
                    break

        form = QFormLayout()
        form.addRow("Объект", self.object_combo)
        form.addRow("ID камеры", self.identifier_edit)
        form.addRow("Имя камеры", self.name_edit)
        form.addRow("Группа/зона", self.group_edit)
        form.addRow("GPS координаты", self.gps_edit)
        form.addRow("УИН", self.uin_edit)
        form.addRow("RTSP URL", self.rtsp_edit)
        form.addRow("", self.enabled_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._after_show_focus)

    def _after_show_focus(self) -> None:
        self.activateWindow()
        self.raise_()
        self.identifier_edit.setFocus(Qt.FocusReason.PopupFocusReason)

    def _on_accept(self) -> None:
        if not self.identifier_edit.text().strip():
            QMessageBox.warning(self, "Валидация", "Введите идентификатор камеры")
            return
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Валидация", "Введите имя камеры")
            return
        if not is_valid_rtsp_url(self.rtsp_edit.text().strip()):
            QMessageBox.warning(self, "Валидация", "Введите корректный RTSP URL")
            return
        self.accept()

    def form_data(self) -> CameraFormData:
        return CameraFormData(
            object_id=int(self.object_combo.currentData()),
            camera_identifier=self.identifier_edit.text().strip(),
            camera_name=self.name_edit.text().strip(),
            group_name=self.group_edit.text().strip(),
            gps_coords=self.gps_edit.text().strip(),
            uin=self.uin_edit.text().strip(),
            rtsp_url=self.rtsp_edit.text().strip(),
            enabled=self.enabled_check.isChecked(),
        )

