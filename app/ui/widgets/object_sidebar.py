from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMenu

from app.database.models import ObjectModel


class ObjectSidebar(QListWidget):
    object_selected = Signal(int)
    delete_requested = Signal(int)
    rename_requested = Signal(int)

    def __init__(self):
        super().__init__()
        self.currentItemChanged.connect(self._on_current_changed)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def populate(self, objects: list[ObjectModel]) -> None:
        self.blockSignals(True)
        self.clear()
        for obj in objects:
            text = (
                f"{obj.name}\n"
                f"Камер: {obj.camera_count}  "
                f"Online: {obj.online_count}  Offline: {obj.offline_count}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, obj.id)
            item.setToolTip(obj.name)
            self.addItem(item)
        if self.count() > 0 and self.currentRow() < 0:
            self.setCurrentRow(0)
        self.blockSignals(False)

    def select_object(self, object_id: int | None) -> None:
        if object_id is None:
            self.setCurrentRow(0 if self.count() > 0 else -1)
            return
        for idx in range(self.count()):
            item = self.item(idx)
            if int(item.data(Qt.ItemDataRole.UserRole)) == object_id:
                self.setCurrentRow(idx)
                return

    def current_object_id(self) -> int | None:
        item = self.currentItem()
        if not item:
            return None
        return int(item.data(Qt.ItemDataRole.UserRole))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            obj_id = self.current_object_id()
            if obj_id is not None:
                self.delete_requested.emit(obj_id)
                event.accept()
                return
        if event.key() == Qt.Key.Key_F2:
            obj_id = self.current_object_id()
            if obj_id is not None:
                self.rename_requested.emit(obj_id)
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_current_changed(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if not current:
            return
        self.object_selected.emit(int(current.data(Qt.ItemDataRole.UserRole)))

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        if not item:
            return
        self.rename_requested.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self.itemAt(pos)
        if not item:
            return
        obj_id = int(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        rename_act = QAction("Переименовать (F2)", menu)
        rename_act.triggered.connect(lambda: self.rename_requested.emit(obj_id))
        menu.addAction(rename_act)
        delete_act = QAction("Удалить (Backspace)", menu)
        delete_act.triggered.connect(lambda: self.delete_requested.emit(obj_id))
        menu.addAction(delete_act)
        menu.exec(self.viewport().mapToGlobal(pos))
