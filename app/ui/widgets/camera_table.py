from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from app.database.models import CameraModel
from app.ui.widgets.status_badge import status_item
from app.utils.datetime_utils import iso_to_human
from app.utils.validators import mask_rtsp_url


class CameraTable(QTableWidget):
    open_requested = Signal(int)
    check_requested = Signal(int)
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    coordinates_copied = Signal(str)
    sort_changed = Signal(int, Qt.SortOrder)

    COLUMNS = [
        "№",
        "Объект",
        "УИН",
        "ID камеры",
        "Имя камеры",
        "Тип",
        "Координаты",
        "Статус",
        "Последняя проверка",
        "Ошибка",
        "RTSP",
        "Действия",
    ]
    COL_NUM, COL_OBJECT, COL_UIN, COL_ID, COL_NAME = 0, 1, 2, 3, 4
    COL_TYPE, COL_GPS = 5, 6
    COL_STATUS, COL_CHECKED, COL_ERR = 7, 8, 9
    COL_RTSP, COL_ACTIONS = 10, 11
    COL_SEEN = -1
    SETTINGS_HIDDEN_KEY = "camera_table/hidden_columns_v3"

    def __init__(self):
        super().__init__(0, len(self.COLUMNS))
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setStretchLastSection(True)
        self.cellClicked.connect(self._on_cell_clicked)

        header = self.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_menu)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_section_clicked)
        self._sort_column = self.COL_OBJECT
        self._sort_order = Qt.SortOrder.AscendingOrder

        self._restore_visibility()

    def _on_section_clicked(self, column: int) -> None:
        if column == self.COL_ACTIONS:
            return
        if column == self._sort_column:
            self._sort_order = (
                Qt.SortOrder.DescendingOrder
                if self._sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self._sort_column = column
            self._sort_order = Qt.SortOrder.AscendingOrder
        self.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)
        self.sort_changed.emit(self._sort_column, self._sort_order)

    def current_sort(self) -> tuple[int, Qt.SortOrder]:
        return self._sort_column, self._sort_order

    # --- selection / keys ---------------------------------------------

    def selected_camera_id(self) -> int | None:
        row = self.currentRow()
        if row < 0 or row >= self.rowCount():
            return None
        for col in (self.COL_NUM, self.COL_ID, self.COL_OBJECT):
            item = self.item(row, col)
            if not item:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            try:
                if data is not None:
                    return int(data)
            except (TypeError, ValueError):
                continue
        return None

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            cam_id = self.selected_camera_id()
            if cam_id is not None:
                self.delete_requested.emit(cam_id)
                event.accept()
                return
        if (
            event.key() == Qt.Key.Key_C
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            if self._copy_selected_gps():
                event.accept()
                return
        super().keyPressEvent(event)

    def _selected_gps(self) -> str:
        row = self.currentRow()
        if row < 0 or row >= self.rowCount():
            return ""
        item = self.item(row, self.COL_GPS)
        if not item:
            return ""
        text = item.data(Qt.ItemDataRole.UserRole) or item.text()
        return str(text or "").strip()

    def _copy_selected_gps(self) -> bool:
        text = self._selected_gps()
        if not text:
            return False
        QGuiApplication.clipboard().setText(text)
        self.coordinates_copied.emit(text)
        return True

    # --- populate ------------------------------------------------------

    def populate(self, cameras: list[CameraModel]) -> None:
        self.setRowCount(len(cameras))
        for row, cam in enumerate(cameras):
            self._set_row(row, cam)
        self.resizeColumnsToContents()

    def _set_row(self, row: int, cam: CameraModel) -> None:
        num_item = QTableWidgetItem(str(row + 1))
        num_item.setData(Qt.ItemDataRole.UserRole, cam.id)
        num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setItem(row, self.COL_NUM, num_item)

        obj_item = QTableWidgetItem(cam.object_name)
        obj_item.setData(Qt.ItemDataRole.UserRole, cam.id)
        self.setItem(row, self.COL_OBJECT, obj_item)

        self.setItem(row, self.COL_UIN, QTableWidgetItem(cam.uin or ""))

        id_item = QTableWidgetItem(cam.camera_identifier)
        id_item.setData(Qt.ItemDataRole.UserRole, cam.id)
        self.setItem(row, self.COL_ID, id_item)

        self.setItem(row, self.COL_NAME, QTableWidgetItem(cam.camera_name))
        self.setItem(row, self.COL_TYPE, QTableWidgetItem(cam.group_name))

        gps_item = QTableWidgetItem(cam.gps_coords or "")
        if cam.gps_coords:
            gps_item.setToolTip(
                f"Кликните, чтобы скопировать координаты:\n{cam.gps_coords}"
            )
            gps_item.setData(Qt.ItemDataRole.UserRole, cam.gps_coords)
        self.setItem(row, self.COL_GPS, gps_item)

        status_cell = status_item(cam.status)
        if cam.last_seen_online_at:
            status_cell.setToolTip(
                f"Последний online: {iso_to_human(cam.last_seen_online_at)}"
            )
        self.setItem(row, self.COL_STATUS, status_cell)
        self.setItem(row, self.COL_CHECKED, QTableWidgetItem(iso_to_human(cam.last_checked_at)))
        self.setItem(row, self.COL_ERR, QTableWidgetItem(cam.last_error or ""))
        self.setItem(row, self.COL_RTSP, QTableWidgetItem(mask_rtsp_url(cam.rtsp_url)))

        action_widget = QWidget()
        layout = QHBoxLayout(action_widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        open_btn = QPushButton("Открыть")
        check_btn = QPushButton("Проверить")
        edit_btn = QPushButton("Изм.")
        delete_btn = QPushButton("Удалить")

        open_btn.clicked.connect(partial(self.open_requested.emit, cam.id))
        check_btn.clicked.connect(partial(self.check_requested.emit, cam.id))
        edit_btn.clicked.connect(partial(self.edit_requested.emit, cam.id))
        delete_btn.clicked.connect(partial(self.delete_requested.emit, cam.id))

        for btn in (open_btn, check_btn, edit_btn, delete_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(btn)

        self.setCellWidget(row, self.COL_ACTIONS, action_widget)

    # --- copy gps ------------------------------------------------------

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column != self.COL_GPS:
            return
        item = self.item(row, column)
        if not item:
            return
        text = item.data(Qt.ItemDataRole.UserRole) or item.text()
        if not text:
            return
        QGuiApplication.clipboard().setText(str(text))
        item.setToolTip(f"Скопировано: {text}\n(кликните ещё раз чтобы скопировать снова)")

    # --- column visibility --------------------------------------------

    def _show_header_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.setTitle("Колонки")
        for idx, label in enumerate(self.COLUMNS):
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(not self.isColumnHidden(idx))
            act.toggled.connect(lambda checked, i=idx: self._toggle_column(i, checked))
            menu.addAction(act)
        menu.addSeparator()
        reset_act = QAction("Показать все", menu)
        reset_act.triggered.connect(self._show_all_columns)
        menu.addAction(reset_act)
        menu.exec(self.horizontalHeader().mapToGlobal(pos))

    def _toggle_column(self, idx: int, visible: bool) -> None:
        self.setColumnHidden(idx, not visible)
        self._save_visibility()

    def _show_all_columns(self) -> None:
        for idx in range(self.columnCount()):
            self.setColumnHidden(idx, False)
        self._save_visibility()

    def _save_visibility(self) -> None:
        hidden = [idx for idx in range(self.columnCount()) if self.isColumnHidden(idx)]
        QSettings().setValue(self.SETTINGS_HIDDEN_KEY, hidden)

    def _restore_visibility(self) -> None:
        raw = QSettings().value(self.SETTINGS_HIDDEN_KEY, [])
        if raw is None:
            return
        try:
            indices = [int(x) for x in raw]
        except (TypeError, ValueError):
            return
        for idx in indices:
            if 0 <= idx < self.columnCount():
                self.setColumnHidden(idx, True)
