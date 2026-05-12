from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtCore import QPoint, Qt, QSettings, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
)

from app import config
from app.database.models import CameraModel
from app.ui.constants import (
    CELL_PREVIEW_LIMIT,
    PING_BLOCKED_COLOR,
    PING_DEAD_COLOR,
    PING_OK_COLOR,
)
from app.ui.widgets.status_badge import status_item
from app.utils.datetime_utils import iso_to_human
from app.utils.validators import mask_rtsp_url


def _display_camera_error_text(raw: Optional[str]) -> str:
    """Текст ошибки для UI: без префикса OFFLINE_ERROR_CODE и без устаревшего «0x00 » из БД."""
    t = (raw or "").strip()
    if not t:
        return ""
    code = (config.OFFLINE_ERROR_CODE or "").strip()
    if code and t.startswith(code):
        t = t[len(code) :].lstrip()
    if len(t) >= 4 and t[:4].lower() == "0x00" and (len(t) == 4 or t[4] in " \t:"):
        t = t[4:].lstrip(" \t:")
    return t


def _is_required_h264_error_text(text: Optional[str]) -> bool:
    """Сообщение про обязательный H.264 (после снятия префиксов для отображения)."""
    return _display_camera_error_text(text) == config.REQUIRED_H264_ERROR_TEXT


# Поля для массового редактирования: (label, key)
BULK_EDIT_FIELDS: tuple[tuple[str, str], ...] = (
    ("Тип (группа)", "group_name"),
    ("Координаты (GPS)", "gps_coords"),
    ("УИН", "uin"),
    ("Перенести в объект…", "object_id"),
)


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
        "Сеть",
        "Последняя проверка",
        "Ошибка",
        "RTSP",
    ]
    COL_NUM, COL_OBJECT, COL_UIN, COL_NAME = 0, 1, 2, 3
    COL_TYPE, COL_GPS = 4, 5
    COL_STATUS, COL_PING, COL_CHECKED, COL_ERR = 6, 7, 8, 9
    COL_RTSP = 10
    COL_ID = -1     # колонки больше нет, оставлено для обратной совместимости в _apply_sort
    COL_SEEN = -1
    SETTINGS_HIDDEN_KEY = "camera_table/hidden_columns_v6"

    # ------------------------------------------------------------------
    # init / setup
    # ------------------------------------------------------------------

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

        self._setup_header()
        self._setup_context_menu()

        self._sort_column = self.COL_OBJECT
        self._sort_order = Qt.SortOrder.AscendingOrder

        self._restore_visibility()

    def _setup_header(self) -> None:
        header = self.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_menu)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_section_clicked)

    def _setup_context_menu(self) -> None:
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_row_menu)

    # ------------------------------------------------------------------
    # sort / selection
    # ------------------------------------------------------------------

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

    def selected_camera_id(self) -> Optional[int]:
        row = self.currentRow()
        if row < 0 or row >= self.rowCount():
            return None
        return self._row_camera_id(row)

    def selected_camera_ids(self) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()
        for row in self._iter_selected_rows():
            cam_id = self._row_camera_id(row)
            if cam_id is not None and cam_id not in seen:
                seen.add(cam_id)
                ids.append(cam_id)
        return ids

    def _iter_selected_rows(self) -> Iterable[int]:
        model = self.selectionModel()
        if not model:
            return ()
        seen: set[int] = set()
        rows: list[int] = []
        for index in model.selectedRows():
            r = index.row()
            if r not in seen:
                seen.add(r)
                rows.append(r)
        return rows

    def _selected_rows(self) -> list[int]:
        return list(self._iter_selected_rows())

    # ------------------------------------------------------------------
    # keyboard
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cam_id = self.selected_camera_id()
            if cam_id is not None:
                self.open_requested.emit(cam_id)
                event.accept()
                return
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

    # ------------------------------------------------------------------
    # populate
    # ------------------------------------------------------------------

    def populate(self, cameras: list[CameraModel]) -> None:
        self.setRowCount(len(cameras))
        for row, cam in enumerate(cameras):
            self._set_row(row, cam)
        self.resizeColumnsToContents()

    def _set_row(self, row: int, cam: CameraModel) -> None:
        self._fill_identity(row, cam)
        self._fill_info(row, cam)
        self._fill_health(row, cam)
        self._fill_traceability(row, cam)

    def _fill_identity(self, row: int, cam: CameraModel) -> None:
        num_item = QTableWidgetItem(str(row + 1))
        num_item.setData(Qt.ItemDataRole.UserRole, cam.id)
        num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setItem(row, self.COL_NUM, num_item)

        obj_item = QTableWidgetItem(cam.object_name)
        obj_item.setData(Qt.ItemDataRole.UserRole, cam.id)
        self._set_hover_tooltip(obj_item, cam.object_name)
        self.setItem(row, self.COL_OBJECT, obj_item)

    def _fill_info(self, row: int, cam: CameraModel) -> None:
        uin_item = QTableWidgetItem(cam.uin or "")
        self._set_hover_tooltip(uin_item, cam.uin)
        self.setItem(row, self.COL_UIN, uin_item)

        name_item = QTableWidgetItem(cam.camera_name)
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

    def _fill_health(self, row: int, cam: CameraModel) -> None:
        status_cell = status_item(cam.status)
        if cam.last_seen_online_at:
            status_cell.setToolTip(
                f"Последний online: {iso_to_human(cam.last_seen_online_at)}"
            )
        self.setItem(row, self.COL_STATUS, status_cell)

        self.setItem(row, self.COL_PING, self._build_ping_item(cam))

    def _fill_traceability(self, row: int, cam: CameraModel) -> None:
        checked_text = iso_to_human(cam.last_checked_at)
        checked_item = QTableWidgetItem(checked_text)
        self._set_hover_tooltip(checked_item, checked_text)
        self.setItem(row, self.COL_CHECKED, checked_item)

        err_raw = cam.last_error or ""
        err_disp = _display_camera_error_text(err_raw)
        err_item = QTableWidgetItem(err_disp)
        self._set_hover_tooltip(err_item, err_disp or None)
        if _is_required_h264_error_text(err_raw):
            err_item.setForeground(QBrush(QColor(PING_BLOCKED_COLOR)))
        self.setItem(row, self.COL_ERR, err_item)

        rtsp_item = QTableWidgetItem(mask_rtsp_url(cam.rtsp_url))
        rtsp_item.setData(Qt.ItemDataRole.UserRole, cam.rtsp_url)
        rtsp_item.setToolTip(
            f"{cam.rtsp_url}\n\nПКМ → копировать / открыть. Двойной клик запустит RTSP."
        )
        self.setItem(row, self.COL_RTSP, rtsp_item)

    # ------------------------------------------------------------------
    # cell builders
    # ------------------------------------------------------------------

    def _set_hover_tooltip(self, item: QTableWidgetItem, value: Optional[str]) -> None:
        text = (value or "").strip()
        if text:
            item.setToolTip(f"{text}\n\nПКМ → «Копировать»")

    def _build_ping_item(self, cam: CameraModel) -> QTableWidgetItem:
        text, tooltip, color = self._ping_cell_payload(cam)
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if color is not None:
            item.setForeground(QBrush(QColor(color)))
        item.setToolTip(tooltip)
        return item

    @staticmethod
    def _ping_cell_payload(cam: CameraModel) -> tuple[str, str, Optional[str]]:
        if cam.last_ping_ok is None:
            return "—", "ICMP-пинг ещё не выполнялся", None
        if cam.last_ping_ok:
            if cam.last_ping_ms is not None:
                return (
                    f"{cam.last_ping_ms} ms",
                    f"Хост отвечает на ICMP, RTT {cam.last_ping_ms} ms",
                    PING_OK_COLOR,
                )
            return "OK", "Хост отвечает на ICMP", PING_OK_COLOR
        # ping failed
        if cam.status == "online":
            return (
                "ICMP блок.",
                (
                    "RTSP отвечает (online), а ICMP пакеты не доходят.\n"
                    "Скорее всего, файрвол на камере или маршрутизаторе режет ping."
                ),
                PING_BLOCKED_COLOR,
            )
        return (
            "нет ответа",
            "Хост не отвечает на ICMP — возможно, сеть до камеры лежит",
            PING_DEAD_COLOR,
        )

    # ------------------------------------------------------------------
    # row context menu
    # ------------------------------------------------------------------

    def _row_camera_id(self, row: int) -> Optional[int]:
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

        # ПКМ по строке вне выделения → переключаемся на эту строку.
        selected_ids = self.selected_camera_ids()
        if cam_id not in selected_ids:
            self.clearSelection()
            self.selectRow(row)
            selected_ids = [cam_id]

        if len(selected_ids) > 1:
            menu = self._build_bulk_menu(selected_ids, column)
        else:
            menu = self._build_single_menu(row, column, cam_id)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _build_single_menu(self, row: int, column: int, cam_id: int) -> QMenu:
        menu = QMenu(self)

        cell_text = self._cell_text(row, column)
        col_label = self._column_label(column)
        copy_label = self._copy_cell_label(col_label, cell_text)
        self._add_action(
            menu, copy_label,
            lambda: self._copy_to_clipboard(cell_text),
            enabled=bool(cell_text),
        )

        menu.addSeparator()
        self._add_action(menu, "Открыть поток (⌘⏎)",
                         lambda: self.open_requested.emit(cam_id))
        self._add_action(menu, "Проверить",
                         lambda: self.check_requested.emit(cam_id))

        menu.addSeparator()
        self._add_action(menu, "Изменить…",
                         lambda: self.edit_requested.emit(cam_id))

        gps = self._gps_for_row(row)
        self._add_action(
            menu, "Копировать координаты",
            lambda: self._copy_gps_text(gps),
            enabled=bool(gps),
        )

        rtsp = self._rtsp_for_row(row)
        self._add_action(
            menu, "Копировать RTSP-ссылку",
            lambda: self._copy_rtsp_text(rtsp),
            enabled=bool(rtsp),
        )
        self._add_action(menu, "Копировать всю строку (TSV)",
                         lambda: self._copy_rows_tsv([row]))

        menu.addSeparator()
        self._add_action(menu, "Удалить (Backspace)",
                         lambda: self.delete_requested.emit(cam_id))
        return menu

    def _build_bulk_menu(self, ids: list[int], column: int) -> QMenu:
        menu = QMenu(self)
        title = QAction(f"Выделено камер: {len(ids)}", menu)
        title.setEnabled(False)
        menu.addAction(title)
        menu.addSeparator()

        rows = self._selected_rows()
        col_label = self._column_label(column)
        self._add_action(
            menu, f"Копировать столбец «{col_label}» ({len(rows)})",
            lambda: self._copy_column_values(rows, column),
        )
        self._add_action(
            menu, f"Копировать выделенные строки (TSV, {len(rows)})",
            lambda: self._copy_rows_tsv(rows),
        )

        menu.addSeparator()
        self._add_action(menu, "Проверить выделенные",
                         lambda: self.bulk_check_requested.emit(ids))

        menu.addSeparator()
        edit_menu = menu.addMenu("Изменить поле…")
        for label, key in BULK_EDIT_FIELDS:
            self._add_action(
                edit_menu, label,
                lambda k=key: self.bulk_edit_requested.emit(ids, k),
            )

        self._add_action(menu, "Включить (enabled = да)",
                         lambda: self.bulk_edit_requested.emit(ids, "enable"))
        self._add_action(menu, "Выключить (enabled = нет)",
                         lambda: self.bulk_edit_requested.emit(ids, "disable"))

        menu.addSeparator()
        self._add_action(
            menu, f"Удалить выделенные ({len(ids)}) — Backspace",
            lambda: self.bulk_delete_requested.emit(ids),
        )
        return menu

    @staticmethod
    def _add_action(
        menu: QMenu,
        text: str,
        slot: Callable[[], None],
        *,
        enabled: bool = True,
    ) -> QAction:
        act = QAction(text, menu)
        act.setEnabled(enabled)
        act.triggered.connect(lambda _checked=False, s=slot: s())
        menu.addAction(act)
        return act

    # ------------------------------------------------------------------
    # cell text / clipboard helpers
    # ------------------------------------------------------------------

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

    @staticmethod
    def _copy_cell_label(col_label: str, cell_text: str) -> str:
        if not cell_text:
            return f"Копировать «{col_label}» (пусто)"
        if len(cell_text) <= CELL_PREVIEW_LIMIT:
            preview = cell_text
        else:
            preview = cell_text[: CELL_PREVIEW_LIMIT - 3] + "…"
        return f"Копировать «{col_label}»: {preview}"

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        if not text:
            return
        QGuiApplication.clipboard().setText(text)

    def _copy_column_values(self, rows: list[int], column: int) -> None:
        values = [self._cell_text(r, column) for r in rows]
        text = "\n".join(values)
        if text:
            QGuiApplication.clipboard().setText(text)

    def _copy_rows_tsv(self, rows: list[int]) -> None:
        visible_cols = [c for c in range(self.columnCount()) if not self.isColumnHidden(c)]
        if not visible_cols or not rows:
            return
        lines = ["\t".join(self._column_label(c) for c in visible_cols)]
        for r in rows:
            lines.append("\t".join(self._cell_text(r, c) for c in visible_cols))
        QGuiApplication.clipboard().setText("\n".join(lines))

    def _gps_for_row(self, row: int) -> str:
        return self._user_role_or_text(row, self.COL_GPS)

    def _rtsp_for_row(self, row: int) -> str:
        return self._user_role_or_text(row, self.COL_RTSP)

    def _user_role_or_text(self, row: int, column: int) -> str:
        item = self.item(row, column)
        if not item:
            return ""
        text = item.data(Qt.ItemDataRole.UserRole) or item.text()
        return str(text or "").strip()

    def _selected_gps(self) -> str:
        row = self.currentRow()
        if row < 0 or row >= self.rowCount():
            return ""
        return self._gps_for_row(row)

    def _copy_selected_gps(self) -> bool:
        text = self._selected_gps()
        if not text:
            return False
        self._copy_gps_text(text)
        return True

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

    # ------------------------------------------------------------------
    # cell click handlers
    # ------------------------------------------------------------------

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column != self.COL_GPS:
            return
        text = self._gps_for_row(row)
        if not text:
            return
        self._copy_gps_text(text)
        item = self.item(row, column)
        if item:
            item.setToolTip(
                f"Скопировано: {text}\n(кликните ещё раз чтобы скопировать снова)"
            )

    def _on_cell_double_clicked(self, row: int, _column: int) -> None:
        cam_id = self._row_camera_id(row)
        if cam_id is None:
            return
        self.open_requested.emit(cam_id)

    # ------------------------------------------------------------------
    # column visibility
    # ------------------------------------------------------------------

    def _show_header_menu(self, pos: QPoint) -> None:
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
