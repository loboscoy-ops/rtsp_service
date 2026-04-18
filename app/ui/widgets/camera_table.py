from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QSettings, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
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
    rtsp_copied = Signal(str)
    sort_changed = Signal(int, Qt.SortOrder)
    bulk_check_requested = Signal(list)
    bulk_delete_requested = Signal(list)
    bulk_edit_requested = Signal(list, str)

    COLUMNS = [
        "№",
        "Объект",
        "УИН",
        "Имя камеры",
        "Тип",
        "Координаты",
        "Статус",
        "Последняя проверка",
        "Ошибка",
        "RTSP",
    ]
    COL_NUM, COL_OBJECT, COL_UIN, COL_NAME = 0, 1, 2, 3
    COL_TYPE, COL_GPS = 4, 5
    COL_STATUS, COL_CHECKED, COL_ERR = 6, 7, 8
    COL_RTSP = 9
    COL_ID = -1
    COL_SEEN = -1
    SETTINGS_HIDDEN_KEY = "camera_table/hidden_columns_v5"

    def __init__(self):
        super().__init__(0, len(self.COLUMNS))
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setStretchLastSection(True)
        self.cellClicked.connect(self._on_cell_clicked)
        self.cellDoubleClicked.connect(self._on_cell_double_clicked)

        header = self.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_menu)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_section_clicked)
        self._sort_column = self.COL_OBJECT
        self._sort_order = Qt.SortOrder.AscendingOrder

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_row_menu)

        self._restore_visibility()

    def _on_section_clicked(self, column: int) -> None:
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
        return self._row_camera_id(row)

    def selected_camera_ids(self) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()
        for index in self.selectionModel().selectedRows() if self.selectionModel() else []:
            cam_id = self._row_camera_id(index.row())
            if cam_id is not None and cam_id not in seen:
                seen.add(cam_id)
                ids.append(cam_id)
        return ids

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            ids = self.selected_camera_ids()
            if len(ids) > 1:
                self.bulk_delete_requested.emit(ids)
                event.accept()
                return
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
        self._set_hover_tooltip(obj_item, cam.object_name)
        self.setItem(row, self.COL_OBJECT, obj_item)

        uin_item = QTableWidgetItem(cam.uin or "")
        self._set_hover_tooltip(uin_item, cam.uin)
        self.setItem(row, self.COL_UIN, uin_item)

        name_item = QTableWidgetItem(cam.camera_name)
        # camera_identifier теперь не имеет своей колонки — оставляем доступ через tooltip / поиск.
        tooltip_text = cam.camera_name
        if cam.camera_identifier:
            tooltip_text = f"{cam.camera_name}\nID камеры: {cam.camera_identifier}"
        self._set_hover_tooltip(name_item, tooltip_text)
        self.setItem(row, self.COL_NAME, name_item)

        type_item = QTableWidgetItem(cam.group_name)
        self._set_hover_tooltip(type_item, cam.group_name)
        self.setItem(row, self.COL_TYPE, type_item)

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

        checked_item = QTableWidgetItem(iso_to_human(cam.last_checked_at))
        self._set_hover_tooltip(checked_item, iso_to_human(cam.last_checked_at))
        self.setItem(row, self.COL_CHECKED, checked_item)

        err_item = QTableWidgetItem(cam.last_error or "")
        self._set_hover_tooltip(err_item, cam.last_error)
        self.setItem(row, self.COL_ERR, err_item)

        rtsp_item = QTableWidgetItem(mask_rtsp_url(cam.rtsp_url))
        rtsp_item.setData(Qt.ItemDataRole.UserRole, cam.rtsp_url)
        rtsp_item.setToolTip(
            f"{cam.rtsp_url}\n\nПКМ → копировать / открыть. Двойной клик откроет поток в ffplay."
        )
        self.setItem(row, self.COL_RTSP, rtsp_item)

    def _set_hover_tooltip(self, item: QTableWidgetItem, value: str | None) -> None:
        text = (value or "").strip()
        if text:
            item.setToolTip(f"{text}\n\nПКМ → «Копировать»")

    # --- row context menu / actions -----------------------------------

    def _row_camera_id(self, row: int) -> int | None:
        if row < 0 or row >= self.rowCount():
            return None
        for col in (self.COL_NUM, self.COL_OBJECT):
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

    def _show_row_menu(self, pos: QPoint) -> None:
        index = self.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        column = index.column()
        cam_id = self._row_camera_id(row)
        if cam_id is None:
            return

        # Если ПКМ по строке вне выделения — переключаемся на эту одну строку.
        selected_ids = self.selected_camera_ids()
        if cam_id not in selected_ids:
            self.clearSelection()
            self.selectRow(row)
            selected_ids = [cam_id]

        if len(selected_ids) > 1:
            self._exec_bulk_menu(pos, selected_ids, column)
        else:
            self._exec_single_menu(pos, row, column, cam_id)

    def _cell_text(self, row: int, column: int) -> str:
        """Текст ячейки (для RTSP/координат — оригинал из UserRole, иначе видимый текст)."""
        if column == self.COL_RTSP:
            return self._rtsp_for_row(row)
        if column == self.COL_GPS:
            return self._gps_for_row(row)
        item = self.item(row, column)
        if not item:
            return ""
        return (item.text() or "").strip()

    def _column_label(self, column: int) -> str:
        if 0 <= column < len(self.COLUMNS):
            return self.COLUMNS[column]
        return ""

    def _copy_to_clipboard(self, text: str) -> None:
        if not text:
            return
        QGuiApplication.clipboard().setText(text)

    def _exec_single_menu(self, pos: QPoint, row: int, column: int, cam_id: int) -> None:
        menu = QMenu(self)

        cell_text = self._cell_text(row, column)
        col_label = self._column_label(column)
        preview = cell_text if len(cell_text) <= 40 else cell_text[:37] + "…"
        copy_label = (
            f"Копировать «{col_label}»: {preview}" if cell_text
            else f"Копировать «{col_label}» (пусто)"
        )
        copy_cell_act = QAction(copy_label, menu)
        copy_cell_act.setEnabled(bool(cell_text))
        copy_cell_act.triggered.connect(lambda: self._copy_to_clipboard(cell_text))
        menu.addAction(copy_cell_act)

        menu.addSeparator()

        open_act = QAction("Открыть поток (⌘⏎)", menu)
        open_act.triggered.connect(lambda: self.open_requested.emit(cam_id))
        menu.addAction(open_act)

        check_act = QAction("Проверить", menu)
        check_act.triggered.connect(lambda: self.check_requested.emit(cam_id))
        menu.addAction(check_act)

        menu.addSeparator()

        edit_act = QAction("Изменить…", menu)
        edit_act.triggered.connect(lambda: self.edit_requested.emit(cam_id))
        menu.addAction(edit_act)

        gps = self._gps_for_row(row)
        copy_gps_act = QAction("Копировать координаты", menu)
        copy_gps_act.setEnabled(bool(gps))
        copy_gps_act.triggered.connect(lambda: self._copy_gps_text(gps))
        menu.addAction(copy_gps_act)

        rtsp = self._rtsp_for_row(row)
        copy_rtsp_act = QAction("Копировать RTSP-ссылку", menu)
        copy_rtsp_act.setEnabled(bool(rtsp))
        copy_rtsp_act.triggered.connect(lambda: self._copy_rtsp_text(rtsp))
        menu.addAction(copy_rtsp_act)

        copy_row_act = QAction("Копировать всю строку (TSV)", menu)
        copy_row_act.triggered.connect(lambda: self._copy_rows_tsv([row]))
        menu.addAction(copy_row_act)

        menu.addSeparator()
        delete_act = QAction("Удалить (Backspace)", menu)
        delete_act.triggered.connect(lambda: self.delete_requested.emit(cam_id))
        menu.addAction(delete_act)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _selected_rows(self) -> list[int]:
        rows: list[int] = []
        seen: set[int] = set()
        if self.selectionModel():
            for index in self.selectionModel().selectedRows():
                r = index.row()
                if r not in seen:
                    seen.add(r)
                    rows.append(r)
        return rows

    def _copy_column_values(self, rows: list[int], column: int) -> None:
        values = [self._cell_text(r, column) for r in rows]
        text = "\n".join(values)
        if text:
            QGuiApplication.clipboard().setText(text)

    def _copy_rows_tsv(self, rows: list[int]) -> None:
        visible_cols = [c for c in range(self.columnCount()) if not self.isColumnHidden(c)]
        lines = []
        header = "\t".join(self._column_label(c) for c in visible_cols)
        lines.append(header)
        for r in rows:
            cells = [self._cell_text(r, c) for c in visible_cols]
            lines.append("\t".join(cells))
        text = "\n".join(lines)
        if text:
            QGuiApplication.clipboard().setText(text)

    def _exec_bulk_menu(self, pos: QPoint, ids: list[int], column: int) -> None:
        menu = QMenu(self)
        title = QAction(f"Выделено камер: {len(ids)}", menu)
        title.setEnabled(False)
        menu.addAction(title)
        menu.addSeparator()

        rows = self._selected_rows()
        col_label = self._column_label(column)
        copy_col_act = QAction(f"Копировать столбец «{col_label}» ({len(rows)})", menu)
        copy_col_act.triggered.connect(lambda: self._copy_column_values(rows, column))
        menu.addAction(copy_col_act)

        copy_rows_act = QAction(f"Копировать выделенные строки (TSV, {len(rows)})", menu)
        copy_rows_act.triggered.connect(lambda: self._copy_rows_tsv(rows))
        menu.addAction(copy_rows_act)

        menu.addSeparator()

        check_act = QAction("Проверить выделенные", menu)
        check_act.triggered.connect(lambda: self.bulk_check_requested.emit(ids))
        menu.addAction(check_act)

        menu.addSeparator()

        edit_menu = menu.addMenu("Изменить поле…")
        for label, key in (
            ("Тип (группа)", "group_name"),
            ("Координаты (GPS)", "gps_coords"),
            ("УИН", "uin"),
            ("Перенести в объект…", "object_id"),
        ):
            act = QAction(label, edit_menu)
            act.triggered.connect(
                lambda _checked=False, k=key: self.bulk_edit_requested.emit(ids, k)
            )
            edit_menu.addAction(act)

        enable_act = QAction("Включить (enabled = да)", menu)
        enable_act.triggered.connect(
            lambda: self.bulk_edit_requested.emit(ids, "enable")
        )
        menu.addAction(enable_act)

        disable_act = QAction("Выключить (enabled = нет)", menu)
        disable_act.triggered.connect(
            lambda: self.bulk_edit_requested.emit(ids, "disable")
        )
        menu.addAction(disable_act)

        menu.addSeparator()
        delete_act = QAction(f"Удалить выделенные ({len(ids)}) — Backspace", menu)
        delete_act.triggered.connect(lambda: self.bulk_delete_requested.emit(ids))
        menu.addAction(delete_act)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _gps_for_row(self, row: int) -> str:
        item = self.item(row, self.COL_GPS)
        if not item:
            return ""
        text = item.data(Qt.ItemDataRole.UserRole) or item.text()
        return str(text or "").strip()

    def _rtsp_for_row(self, row: int) -> str:
        item = self.item(row, self.COL_RTSP)
        if not item:
            return ""
        text = item.data(Qt.ItemDataRole.UserRole) or item.text()
        return str(text or "").strip()

    def _copy_gps_text(self, text: str) -> None:
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.coordinates_copied.emit(text)

    def _copy_rtsp_text(self, text: str) -> None:
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.rtsp_copied.emit(text)

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column != self.COL_GPS:
            return
        text = self._gps_for_row(row)
        if not text:
            return
        self._copy_gps_text(text)
        item = self.item(row, column)
        if item:
            item.setToolTip(f"Скопировано: {text}\n(кликните ещё раз чтобы скопировать снова)")

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        cam_id = self._row_camera_id(row)
        if cam_id is None:
            return
        self.open_requested.emit(cam_id)

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
