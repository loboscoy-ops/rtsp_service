from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.services.import_service import (
    ALL_FIELDS,
    REQUIRED_FIELDS,
    ImportPreview,
    ImportService,
    SheetData,
)
from app.services.template_service import TemplateService

FIELD_LABELS = {
    "object_name": "Объект (object_name) *",
    "camera_identifier": "ID камеры (camera_identifier) *",
    "rtsp_url": "RTSP URL (rtsp_url) *",
    "camera_name": "Имя камеры (camera_name)",
    "group_name": "Группа / тип (group_name)",
    "gps_coords": "GPS координаты (gps_coords)",
    "enabled": "Активна (enabled)",
}
NOT_USED_LABEL = "(не использовать)"


class _Job(QRunnable):
    def __init__(self, fn, success_signal, error_signal):
        super().__init__()
        self.fn = fn
        self._success = success_signal
        self._error = error_signal
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            data = self.fn()
        except Exception as exc:
            try:
                self._error.emit(str(exc))
            except RuntimeError:
                pass
            return
        try:
            self._success.emit(data)
        except RuntimeError:
            pass


class ImportDialog(QDialog):
    import_completed = Signal(int, int)
    _sheets_loaded = Signal(object)
    _preview_ready = Signal(object)
    _import_done = Signal(object)
    _job_failed = Signal(str)

    def __init__(self, import_service: ImportService, template_service: TemplateService, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Импорт формы")
        self.resize(1100, 720)
        self.import_service = import_service
        self.template_service = template_service
        self.file_path: Path | None = None
        self.sheets: list[SheetData] = []
        self.current_sheet: SheetData | None = None
        self.preview: ImportPreview | None = None
        self.pool = QThreadPool.globalInstance()
        self._summary_cache = ""
        self._mapping_combos: dict[str, QComboBox] = {}

        self._sheets_loaded.connect(self._on_sheets_loaded)
        self._preview_ready.connect(self._on_preview_ready)
        self._import_done.connect(self._on_import_done)
        self._job_failed.connect(self._on_job_error)

        self._build_ui()
        self._set_busy(False)

    def _build_ui(self) -> None:
        choose_btn = QPushButton("Выбрать файл (.xlsx / .xls)")
        choose_btn.clicked.connect(self.choose_file)
        template_btn = QPushButton("Скачать шаблон XLSX")
        template_btn.clicked.connect(self.download_template)
        preview_btn = QPushButton("Предпросмотр")
        preview_btn.clicked.connect(self.build_preview)
        import_btn = QPushButton("Импорт формы")
        import_btn.setToolTip("Применить выбранную форму в БД")
        import_btn.clicked.connect(self.run_import)
        self.import_btn = import_btn

        top = QHBoxLayout()
        top.addWidget(choose_btn)
        top.addWidget(template_btn)
        top.addWidget(preview_btn)
        top.addWidget(import_btn)
        top.addStretch(1)

        self.file_label = QLabel("Файл не выбран")

        self.sheet_combo = QComboBox()
        self.sheet_combo.setMinimumWidth(360)
        self.sheet_combo.currentIndexChanged.connect(self._on_sheet_changed)

        self.header_spin = QSpinBox()
        self.header_spin.setMinimum(1)
        self.header_spin.setMaximum(50)
        self.header_spin.setValue(1)
        self.header_spin.valueChanged.connect(self._on_header_row_changed)

        sheet_form = QFormLayout()
        sheet_form.addRow("Лист:", self.sheet_combo)
        sheet_form.addRow("Строка заголовков (1-based):", self.header_spin)
        sheet_box = QGroupBox("Источник")
        sheet_box.setLayout(sheet_form)

        self.mapping_form = QFormLayout()
        self.mapping_box = QGroupBox(
            "Соответствие колонок (звёздочкой отмечены обязательные)"
        )
        self.mapping_box.setLayout(self.mapping_form)
        for field in ALL_FIELDS:
            combo = QComboBox()
            combo.setMinimumWidth(360)
            combo.addItem(NOT_USED_LABEL, -1)
            self._mapping_combos[field] = combo
            self.mapping_form.addRow(FIELD_LABELS[field], combo)

        self.summary_label = QLabel("")

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            [
                "object_name",
                "camera_identifier",
                "camera_name",
                "rtsp_url",
                "group_name",
                "enabled",
                "ошибка",
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.file_label)
        root.addWidget(sheet_box)
        root.addWidget(self.mapping_box)
        root.addWidget(self.summary_label)
        root.addWidget(self.table, 1)

    # --- file / sheets -------------------------------------------------

    def choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите Excel-файл",
            "",
            "Excel files (*.xlsx *.XLSX *.xls *.XLS);;Все файлы (*)",
        )
        if not path:
            return
        self.file_path = Path(path)
        self.file_label.setText(str(self.file_path))
        self.preview = None
        self.table.setRowCount(0)
        self.summary_label.setText("Чтение листов...")
        self._summary_cache = ""
        self._set_busy(True)

        target = self.file_path

        def _task():
            return self.import_service.list_sheets(target)

        self.pool.start(_Job(_task, self._sheets_loaded, self._job_failed))

    def _on_sheets_loaded(self, sheets_obj: object) -> None:
        self._set_busy(False)
        self.sheets = list(sheets_obj)  # type: ignore[arg-type]
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for s in self.sheets:
            rows = len(s.rows)
            cols = max((len(r) for r in s.rows), default=0)
            self.sheet_combo.addItem(f"{s.name}  ({rows}×{cols})", s.name)
        self.sheet_combo.blockSignals(False)
        if self.sheets:
            self.sheet_combo.setCurrentIndex(0)
            self._on_sheet_changed(0)
        else:
            self.summary_label.setText("В файле нет листов")

    def _on_sheet_changed(self, _idx: int) -> None:
        name = self.sheet_combo.currentData()
        self.current_sheet = next((s for s in self.sheets if s.name == name), None)
        if not self.current_sheet:
            return
        max_header = max(1, len(self.current_sheet.rows))
        detected_idx = self.import_service.detect_header_row(self.current_sheet.rows)
        self.header_spin.blockSignals(True)
        self.header_spin.setMaximum(max_header)
        self.header_spin.setValue(min(detected_idx + 1, max_header))
        self.header_spin.blockSignals(False)
        self._refresh_mapping_combos()

    def _on_header_row_changed(self, _value: int) -> None:
        self._refresh_mapping_combos()

    def _refresh_mapping_combos(self) -> None:
        if not self.current_sheet:
            return
        header_idx = self.header_spin.value() - 1
        if header_idx < 0 or header_idx >= len(self.current_sheet.rows):
            return
        header = self.current_sheet.rows[header_idx]
        max_cols = max((len(r) for r in self.current_sheet.rows), default=len(header))
        labels: list[str] = []
        for col in range(max_cols):
            head = header[col] if col < len(header) else ""
            letter = self._col_letter(col)
            label = f"{letter} · {head}" if head else f"{letter} · (пусто)"
            labels.append(label)

        auto = self.import_service.auto_detect_mapping(header)
        auto = self.import_service.refine_identifier_mapping(
            self.current_sheet, header_idx, auto
        )

        for field, combo in self._mapping_combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(NOT_USED_LABEL, -1)
            for col_idx, label in enumerate(labels):
                combo.addItem(label, col_idx)
            target = auto.get(field)
            if target is not None and 0 <= target < max_cols:
                combo.setCurrentIndex(target + 1)
            combo.blockSignals(False)
            try:
                combo.currentIndexChanged.disconnect(self._schedule_auto_preview)
            except (RuntimeError, TypeError):
                pass
            combo.currentIndexChanged.connect(self._schedule_auto_preview)
        self._schedule_auto_preview()

    def _schedule_auto_preview(self) -> None:
        if not self.current_sheet:
            return
        mapping = self._current_mapping()
        if any(mapping.get(f) is None for f in REQUIRED_FIELDS):
            return
        self.build_preview()

    @staticmethod
    def _col_letter(idx: int) -> str:
        n = idx + 1
        out = ""
        while n:
            n, r = divmod(n - 1, 26)
            out = chr(65 + r) + out
        return out

    # --- preview / import ----------------------------------------------

    def _current_mapping(self) -> dict[str, int | None]:
        mapping: dict[str, int | None] = {}
        for field, combo in self._mapping_combos.items():
            data = combo.currentData()
            mapping[field] = None if data is None or int(data) < 0 else int(data)
        return mapping

    def build_preview(self) -> None:
        if not self.current_sheet:
            QMessageBox.warning(self, "Импорт", "Сначала выберите файл и лист")
            return
        mapping = self._current_mapping()
        missing = [f for f in REQUIRED_FIELDS if mapping.get(f) is None]
        if missing:
            QMessageBox.warning(
                self,
                "Импорт",
                "Не выбраны колонки для обязательных полей: " + ", ".join(missing),
            )
            return
        header_row_index = self.header_spin.value() - 1
        sheet = self.current_sheet
        self._set_busy(True)

        def _task():
            return self.import_service.build_preview_from_mapping(sheet, header_row_index, mapping)

        self.pool.start(_Job(_task, self._preview_ready, self._job_failed))

    def _on_preview_ready(self, preview_obj: object) -> None:
        self._set_busy(False)
        self.preview = preview_obj  # type: ignore[assignment]
        self._render_preview(self.preview)

    def _render_preview(self, preview: ImportPreview) -> None:
        self.table.setRowCount(len(preview.rows))
        for idx, row in enumerate(preview.rows):
            self.table.setItem(idx, 0, QTableWidgetItem(row.object_name))
            self.table.setItem(idx, 1, QTableWidgetItem(row.camera_identifier))
            self.table.setItem(idx, 2, QTableWidgetItem(row.camera_name))
            self.table.setItem(idx, 3, QTableWidgetItem(row.rtsp_url))
            self.table.setItem(idx, 4, QTableWidgetItem(row.group_name))
            self.table.setItem(idx, 5, QTableWidgetItem("1" if row.enabled else "0"))
            self.table.setItem(idx, 6, QTableWidgetItem(row.error))
        valid_count = len(preview.valid_rows)
        total = len(preview.rows)
        text = f"Строк данных: {total}. Валидных: {valid_count}. Заметок: {len(preview.issues)}."
        self.summary_label.setText(text)
        self._summary_cache = self.summary_label.text()

    def run_import(self) -> None:
        if not self.current_sheet:
            QMessageBox.warning(self, "Импорт", "Сначала выберите файл и лист")
            return
        if not self.preview:
            self.build_preview()
            QMessageBox.information(
                self,
                "Импорт",
                "Подготовлен предпросмотр. Нажмите ещё раз «Импорт формы», чтобы загрузить.",
            )
            return
        if not self.preview.valid_rows:
            QMessageBox.warning(
                self,
                "Импорт",
                "Нет валидных строк для импорта. Проверьте сопоставление колонок и текст ошибок в таблице.",
            )
            return

        unique_keys = {
            (r.object_name.lower(), r.camera_identifier.lower())
            for r in self.preview.valid_rows
        }
        total = len(self.preview.valid_rows)
        if len(unique_keys) < total:
            resp = QMessageBox.warning(
                self,
                "Импорт",
                (
                    f"В превью {total} строк, но уникальных ID камер только {len(unique_keys)}.\n"
                    "Каждая повторная строка ПЕРЕЗАПИШЕТ предыдущую.\n\n"
                    "Скорее всего, в качестве «ID камеры (camera_identifier)» выбрана колонка, "
                    "повторяющаяся между камерами (например, «Описание зоны обзора» или «УИН»).\n"
                    "Поменяйте её на колонку с уникальным значением (например «№ п/п»).\n\n"
                    "Всё равно импортировать?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return

        preview = self.preview
        self._set_busy(True)

        def _task():
            return self.import_service.import_valid_rows(preview)

        self.pool.start(_Job(_task, self._import_done, self._job_failed))

    def _on_import_done(self, result_obj: object) -> None:
        self._set_busy(False)
        created, updated = result_obj  # type: ignore[misc]
        QMessageBox.information(
            self,
            "Импорт завершен",
            f"Создано: {created}\nОбновлено: {updated}",
        )
        self.import_completed.emit(created, updated)
        self.accept()

    def _on_job_error(self, text: str) -> None:
        self._set_busy(False)
        QMessageBox.critical(self, "Ошибка", text)

    def _set_busy(self, busy: bool) -> None:
        self.import_btn.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.summary_label.setText("Обработка..." if busy else self._summary_cache)

    # --- template ------------------------------------------------------

    def download_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить шаблон",
            "camera_import_template.xlsx",
            "Excel (*.xlsx)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".xlsx":
            target = target.with_suffix(".xlsx")
        self.template_service.generate(target)
        QMessageBox.information(self, "Шаблон", f"Шаблон сохранен: {target}")
