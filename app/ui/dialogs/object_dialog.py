from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)


class ObjectDialog(QDialog):
    def __init__(self, parent=None, initial_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Объект")
        self.resize(380, 120)

        self.name_edit = QLineEdit(initial_name)
        self.name_edit.setPlaceholderText("Название объекта")

        form = QFormLayout()
        form.addRow("Название", self.name_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.name.strip():
            QMessageBox.warning(self, "Валидация", "Название объекта не может быть пустым")
            return
        self.accept()

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

