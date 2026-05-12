from __future__ import annotations

import html

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QAction,
    QKeyEvent,
    QPalette,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from app.database.models import ObjectModel
from app.ui.constants import (
    STATUS_OFFLINE_FG,
    STATUS_ONLINE_FG,
    THEME_FG,
    THEME_FG_MUTED,
)


class _RichTextItemDelegate(QStyledItemDelegate):
    """Рисует HTML из DisplayRole, чтобы Online/Offline были цветными."""

    def paint(self, painter, option, index):  # noqa: D401
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()

        text_html = opt.text or ""
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setHtml(text_html)
        doc.setTextWidth(opt.rect.width() - 12)

        painter.save()
        painter.translate(opt.rect.x() + 6, opt.rect.y() + 4)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        if opt.state & QStyle.StateFlag.State_Selected:
            ctx.palette.setColor(
                QPalette.ColorRole.Text,
                opt.palette.highlightedText().color(),
            )
        clip = QRectF(0, 0, opt.rect.width() - 12, opt.rect.height() - 8)
        ctx.clip = clip
        painter.setClipRect(clip)
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index):  # noqa: D401
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setHtml(opt.text or "")
        width = opt.rect.width() if opt.rect.width() > 0 else 240
        doc.setTextWidth(width - 12)
        return QSize(int(doc.idealWidth()) + 12, int(doc.size().height()) + 8)


class ObjectSidebar(QListWidget):
    object_selected = Signal(int)
    delete_requested = Signal(int)
    rename_requested = Signal(int)

    def __init__(self):
        super().__init__()
        self.setItemDelegate(_RichTextItemDelegate(self))
        self.currentItemChanged.connect(self._on_current_changed)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def populate(self, objects: list[ObjectModel]) -> None:
        self.blockSignals(True)
        self.clear()
        for obj in objects:
            name = html.escape(obj.name or "")
            text = (
                f'<div style="color:{THEME_FG};font-weight:600;">{name}</div>'
                f'<div style="margin-top:2px;font-weight:400;">'
                f'<span style="color:{THEME_FG_MUTED};">Камер: {obj.camera_count}  </span>'
                f'<span style="color:{STATUS_ONLINE_FG};">'
                f'Online: {obj.online_count}</span>'
                f'<span style="color:{THEME_FG_MUTED};">  </span>'
                f'<span style="color:{STATUS_OFFLINE_FG};">'
                f'Offline: {obj.offline_count}</span>'
                f'</div>'
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
