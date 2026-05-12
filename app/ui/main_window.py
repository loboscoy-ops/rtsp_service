from __future__ import annotations

import logging
import shutil
import subprocess
import traceback
from collections import deque
from datetime import datetime
from typing import Callable, Optional

from PySide6.QtCore import QByteArray, QSettings, Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.database.models import CameraModel, ObjectModel
from app.database.repository import Repository
from app.services.camera_checker import CameraChecker, CheckResult
from app.services.ffplay_service import FFPlayService
from app.services.import_service import ImportService
from app.services.template_service import TemplateService
from app.ui.constants import (
    ERROR_PANE_BG,
    ERROR_PANE_FG,
    FFPLAY_FOCUS_DELAY_MS,
    BOTTOM_SPLITTER_DEFAULT_SIZES,
    CAMERAS_SPLITTER_DEFAULT_SIZES,
    IMPORT_TABLE_VISIBLE_ROWS,
    REFRESH_DEBOUNCE_MS,
    RIGHT_PANE_DEFAULT_WIDTH,
    SIDEBAR_DEFAULT_WIDTH,
    SIDEBAR_MIN_WIDTH,
    THEME_BG_PANEL,
    THEME_BORDER,
    THREADPOOL_SHUTDOWN_WAIT_MS,
    TOOLBAR_LOGO_HEIGHT_PX,
    WINDOW_DEFAULT_SIZE,
)
from app.ui.dialogs.camera_dialog import CameraDialog
from app.ui.dialogs.import_dialog import ImportDialog
from app.ui.dialogs.object_dialog import ObjectDialog
from app.ui.widgets.camera_map import CameraMapView
from app.ui.widgets.camera_table import CameraTable
from app.ui.widgets.dashboard import DashboardView
from app.ui.widgets.object_sidebar import ObjectSidebar
from app.utils.process_utils import terminate_ffprobe_children
from app.utils.validators import mask_rtsp_url

_log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    _SETTINGS_SPLITTER_MAIN = "main_window/splitter_main"
    _SETTINGS_SPLITTER_CAMERAS = "main_window/splitter_cameras"
    _SETTINGS_SPLITTER_BOTTOM = "main_window/splitter_bottom"

    def __init__(self, repository: Repository):
        super().__init__()
        self.repo = repository
        self.ffplay = FFPlayService()
        self.checker = CameraChecker()
        self.import_service = ImportService(self.repo)
        self.template_service = TemplateService()
        self._closing = False

        self.current_object_id: Optional[int] = None
        self.objects_cache: list[ObjectModel] = []
        self.cameras_cache: list[CameraModel] = []
        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder

        # Текущий «снимок» ошибок камер (id → отрисованная строка).
        # Дедуплицируется автоматически: повторные провалы той же камеры
        # не дублируются, а уход в online — убирает запись.
        self._camera_errors: dict[int, str] = {}
        # Прочие разовые ошибки (FFPLAY/IMPORT…) — список с временем.
        self._misc_errors: list[str] = []
        self._active_checks: dict[int, str] = {}
        self._recent_poll_events: deque[str] = deque(maxlen=5)
        self._priority_object_id: int | None = None
        # Текст «Опрос:» при активных проверках: full | local | auto
        self._poll_ui: str | None = None

        self.setWindowTitle(f"{config.APP_NAME} v.{config.APP_VERSION}")
        self.resize(*WINDOW_DEFAULT_SIZE)

        self._setup_ui()
        self._bind_signals()
        self._refresh_objects()
        self._refresh_cameras()

        self._offline_timer = QTimer(self)
        self._offline_timer.setInterval(max(30, config.CHECK_INTERVAL_OFFLINE_SEC) * 1000)
        self._offline_timer.timeout.connect(self._auto_check_offline_objects)
        self._offline_timer.start()

        self._online_timer = QTimer(self)
        self._online_timer.setInterval(max(60, config.CHECK_INTERVAL_ONLINE_SEC) * 1000)
        self._online_timer.timeout.connect(self._auto_check_online_objects)
        self._online_timer.start()

        self._poll_activity_timer = QTimer(self)
        self._poll_activity_timer.setInterval(1000)
        self._poll_activity_timer.timeout.connect(self._render_poll_activity)
        self._poll_activity_timer.start()

        self._refresh_debounce = QTimer(self)
        self._refresh_debounce.setSingleShot(True)
        self._refresh_debounce.setInterval(REFRESH_DEBOUNCE_MS)
        self._refresh_debounce.timeout.connect(self._refresh_views_after_checks)

        self._setup_shortcuts()

    # ==================================================================
    # UI assembly
    # ==================================================================

    def _setup_ui(self) -> None:
        self._build_toolbar()
        self.setCentralWidget(self._build_central_stack())
        self._restore_splitter_states()
        self._enforce_cameras_bottom_height_cap()
        # Status bar убран целиком — лог пишется только в системный логгер,
        # лого живёт в правом углу тулбара (см. _build_toolbar).

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.cameras_view_btn = self._add_view_button(
            toolbar, "Камеры", checked=True, slot=lambda: self._switch_view(0)
        )
        self.dashboard_view_btn = self._add_view_button(
            toolbar, "Дашборд", checked=False, slot=lambda: self._switch_view(1)
        )
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_group.addButton(self.cameras_view_btn, 0)
        self._view_group.addButton(self.dashboard_view_btn, 1)

        toolbar.addSeparator()

        self.add_object_btn = self._add_toolbar_button(
            toolbar, "Добавить объект", self._add_object
        )
        self.add_camera_btn = self._add_toolbar_button(
            toolbar, "Добавить камеру", self._add_camera
        )
        self.import_btn = self._add_toolbar_button(
            toolbar, "Импорт формы", self._open_import_dialog,
            tooltip="Загрузить .xls / .xlsx и сопоставить колонки",
        )
        self.check_all_btn = self._add_toolbar_button(
            toolbar, "Проверить все (⌘R)", self._manual_check_all,
            tooltip="Проверить все камеры всех объектов (⌘R)",
        )

        toolbar.addSeparator()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Поиск: УИН, ID камеры, имя, группа, объект"
        )
        self.search_input.textChanged.connect(self._refresh_cameras)
        toolbar.addWidget(self.search_input)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["all", "online", "offline"])
        self.status_filter.currentIndexChanged.connect(self._refresh_cameras)
        toolbar.addWidget(QLabel("Статус:"))
        toolbar.addWidget(self.status_filter)
        toolbar.addWidget(QLabel("Опрос:"))
        self.poll_activity = QLineEdit()
        self.poll_activity.setReadOnly(True)
        self.poll_activity.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.poll_activity.setFixedWidth(max(180, self.status_filter.sizeHint().width() * 2))
        self.poll_activity.setToolTip("Текущие процессы опроса камер")
        toolbar.addWidget(self.poll_activity)
        self._render_poll_activity()

        # Растяжка прижимает лого «Урус» в правый угол тулбара.
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        toolbar.addWidget(spacer)
        self._setup_logo(toolbar)

    @staticmethod
    def _add_toolbar_button(
        toolbar: QToolBar,
        label: str,
        slot: Callable[[], None],
        *,
        tooltip: str = "",
    ) -> QPushButton:
        btn = QPushButton(label)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        toolbar.addWidget(btn)
        return btn

    @staticmethod
    def _add_view_button(
        toolbar: QToolBar,
        label: str,
        *,
        checked: bool,
        slot: Callable[[], None],
    ) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setObjectName("ViewSwitch")
        btn.clicked.connect(slot)
        toolbar.addWidget(btn)
        return btn

    def _build_central_stack(self) -> QStackedWidget:
        stack = QStackedWidget()
        stack.addWidget(self._build_central_splitter())
        self.dashboard = DashboardView(self.repo)
        self.dashboard.object_selected.connect(self._on_dashboard_object_selected)
        self.dashboard.map_view.open_object_requested.connect(
            self._on_dashboard_object_selected
        )
        self.dashboard.map_view.open_camera_requested.connect(self._open_camera_stream)
        stack.addWidget(self.dashboard)
        self._view_stack = stack
        return stack

    def _switch_view(self, index: int) -> None:
        if not hasattr(self, "_view_stack"):
            return
        self._view_stack.setCurrentIndex(index)
        if index == 1:
            self.dashboard.refresh(status_filter=self.status_filter.currentText())

    def _on_dashboard_object_selected(self, object_id: int) -> None:
        """Клик по карточке площадки на дашборде → переходим в раздел
        «Камеры» и выбираем этот объект в сайдбаре.
        """
        self.cameras_view_btn.setChecked(True)
        self._switch_view(0)
        self.sidebar.select_object(object_id)

    def _build_central_splitter(self) -> QSplitter:
        splitter = QSplitter()
        splitter.addWidget(self._build_objects_pane())
        splitter.addWidget(self._build_cameras_pane())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([SIDEBAR_DEFAULT_WIDTH, RIGHT_PANE_DEFAULT_WIDTH])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.splitterMoved.connect(lambda *_: self._save_splitter_states())
        self._main_splitter = splitter
        return splitter

    def _build_objects_pane(self) -> QWidget:
        # Заголовок «Объекты» убран по просьбе — список говорит сам за себя.
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar = ObjectSidebar()
        self.sidebar.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        layout.addWidget(self.sidebar)
        return wrapper

    def _build_cameras_pane(self) -> QWidget:
        # Вертикальный сплиттер: сверху таблица камер, снизу карта + ошибки.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_table_pane())
        self._cameras_bottom_pane = self._build_bottom_pane()
        splitter.addWidget(self._cameras_bottom_pane)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes(list(CAMERAS_SPLITTER_DEFAULT_SIZES))
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.splitterMoved.connect(lambda *_: self._save_splitter_states())
        self._cameras_splitter = splitter
        return splitter

    def _build_table_pane(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = CameraTable()
        layout.addWidget(self.table)
        return wrapper

    def _build_bottom_pane(self) -> QSplitter:
        # Карта и панель «Ошибки» делят нижнюю строку поровну (высоты
        # выровнены, ширины задаются BOTTOM_SPLITTER_DEFAULT_SIZES).
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_map_panel())
        splitter.addWidget(self._build_errors_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes(list(BOTTOM_SPLITTER_DEFAULT_SIZES))
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.splitterMoved.connect(lambda *_: self._save_splitter_states())
        self._bottom_splitter = splitter
        return splitter

    def _resize_cameras_splitter_for_visible_table_rows(self, rows: int) -> None:
        """Задать высоту верхней части сплиттера под ~`rows` видимых строк таблицы (остальное — скролл)."""
        if not hasattr(self, "_cameras_splitter"):
            return
        sp = self._cameras_splitter
        table = self.table
        QApplication.processEvents()
        header_h = max(table.horizontalHeader().height(), 24)
        if table.rowCount() >= 1:
            row_h = max(
                table.verticalHeader().sectionSize(0),
                table.verticalHeader().defaultSectionSize(),
                18,
            )
        else:
            row_h = max(table.verticalHeader().defaultSectionSize(), 24)
        max_bottom = self._cameras_bottom_area_max_height()
        bottom_min = min(160, max_bottom)
        total = sum(sp.sizes())
        if total <= 0:
            total = max(sp.height(), 520)
        want_top = header_h + rows * row_h + 10
        top_h = min(want_top, total - bottom_min)
        top_h = max(top_h, header_h + row_h + 6)
        bottom_h = total - top_h
        if bottom_h > max_bottom:
            bottom_h = max_bottom
            top_h = total - bottom_h
        sp.setSizes([top_h, bottom_h])

    def _cameras_bottom_area_max_height(self) -> int:
        """Максимальная высота нижнего блока (миникарта + ошибки) в разделе «Камеры».

        Не больше трети высоты доступной области экрана, на котором окно показано.
        """
        scr = self.screen()
        if scr is None:
            scr = QApplication.primaryScreen()
        if scr is None:
            return 400
        return max(120, scr.availableGeometry().height() // 3)

    def _enforce_cameras_bottom_height_cap(self) -> None:
        if not hasattr(self, "_cameras_splitter") or not hasattr(self, "_cameras_bottom_pane"):
            return
        max_bottom = self._cameras_bottom_area_max_height()
        self._cameras_bottom_pane.setMaximumHeight(max_bottom)

        sp = self._cameras_splitter
        sizes = sp.sizes()
        if len(sizes) < 2:
            return
        top, bot = sizes[0], sizes[1]
        total = top + bot
        if total <= 0:
            return
        min_table = 120
        if bot <= max_bottom:
            return
        new_bot = max_bottom
        new_top = total - new_bot
        if new_top < min_table:
            new_top = min(min_table, max(0, total - 80))
            new_bot = total - new_top
            if new_bot > max_bottom:
                new_bot = max_bottom
                new_top = total - new_bot
        sp.setSizes([new_top, new_bot])

    def _save_splitter_states(self) -> None:
        settings = QSettings()
        if hasattr(self, "_main_splitter"):
            settings.setValue(
                self._SETTINGS_SPLITTER_MAIN,
                self._main_splitter.saveState(),
            )
        if hasattr(self, "_cameras_splitter"):
            settings.setValue(
                self._SETTINGS_SPLITTER_CAMERAS,
                self._cameras_splitter.saveState(),
            )
        if hasattr(self, "_bottom_splitter"):
            settings.setValue(
                self._SETTINGS_SPLITTER_BOTTOM,
                self._bottom_splitter.saveState(),
            )

    def _restore_splitter_states(self) -> None:
        settings = QSettings()
        self._restore_splitter(
            settings.value(self._SETTINGS_SPLITTER_MAIN),
            getattr(self, "_main_splitter", None),
        )
        self._restore_splitter(
            settings.value(self._SETTINGS_SPLITTER_CAMERAS),
            getattr(self, "_cameras_splitter", None),
        )
        self._restore_splitter(
            settings.value(self._SETTINGS_SPLITTER_BOTTOM),
            getattr(self, "_bottom_splitter", None),
        )

    @staticmethod
    def _restore_splitter(raw_state, splitter: Optional[QSplitter]) -> None:
        if splitter is None or raw_state is None:
            return
        if isinstance(raw_state, QByteArray):
            splitter.restoreState(raw_state)
            return
        if isinstance(raw_state, bytes):
            splitter.restoreState(QByteArray(raw_state))
            return
        if isinstance(raw_state, str):
            splitter.restoreState(QByteArray.fromBase64(raw_state.encode("ascii")))

    def _build_map_panel(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        # Нумерация маркеров на миникарте «Камеры» — как на дашборде:
        # внутри каждой площадки 1..N.
        self.map_view = CameraMapView(self, per_object_marker_numbers=True)
        self.map_view.open_camera_requested.connect(self._open_camera_stream)
        layout.addWidget(self.map_view)
        return wrapper

    def _build_errors_panel(self) -> QWidget:
        # Кнопка «Очистить» теперь живёт ВНУТРИ единого блока ошибок:
        # снаружи — общая рамка как у QTextEdit, внутри слева → справа
        # узкая шапка с кнопкой, ниже — сам текст ошибок.
        wrapper = QFrame()
        wrapper.setObjectName("ErrorsPanel")
        wrapper.setStyleSheet(
            "QFrame#ErrorsPanel {"
            f" background-color: {ERROR_PANE_BG};"
            f" border: 1px solid {THEME_BORDER};"
            " border-radius: 6px;"
            "}"
        )
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addStretch(1)
        self.errors_clear_btn = QPushButton("Очистить")
        self.errors_clear_btn.setObjectName("ErrorsClearBtn")
        self.errors_clear_btn.setToolTip(
            "Скрыть все текущие записи в панели «Ошибки». "
            "При следующей проверке камер актуальные ошибки появятся снова."
        )
        self.errors_clear_btn.clicked.connect(self._clear_errors_pane)
        header.addWidget(self.errors_clear_btn)
        layout.addLayout(header)

        self.error_text = QTextEdit()
        self.error_text.setReadOnly(True)
        # У текстового поля убираем собственную рамку — рамка уже у обёртки.
        self.error_text.setStyleSheet(
            "QTextEdit {"
            f" background-color: {ERROR_PANE_BG};"
            f" color: {ERROR_PANE_FG};"
            " border: none;"
            " padding: 0px;"
            "}"
        )
        layout.addWidget(self.error_text, 1)
        return wrapper

    def _clear_errors_pane(self) -> None:
        """Сбросить текущее содержимое панели «Ошибки» вручную.

        Чистим оба источника: дедуп камер (заполняется по результатам
        проверок) и журнал прочих событий (импорт/ffplay/etc.).
        """
        had_anything = bool(self._camera_errors) or bool(self._misc_errors)
        self._camera_errors.clear()
        self._misc_errors.clear()
        self._render_errors_pane()
        if had_anything:
            self._log("Панель «Ошибки» очищена")

    def _setup_logo(self, toolbar: QToolBar) -> None:
        """Положить логотип «Урус» в правый угол тулбара.

        Высота тулбара ограничена кнопками, поэтому логотип масштабируем
        под высоту тулбара (TOOLBAR_LOGO_HEIGHT_PX), не под старую панель
        статуса. Если файла лого нет — просто ничего не добавляем.
        """
        if not config.LOGO_PATH.exists():
            return
        pix = QPixmap(str(config.LOGO_PATH))
        if pix.isNull():
            return
        pix = pix.scaledToHeight(
            TOOLBAR_LOGO_HEIGHT_PX, Qt.TransformationMode.SmoothTransformation
        )
        pix = self._composite_on_theme(pix)
        self.logo_label = QLabel()
        self.logo_label.setPixmap(pix)
        self.logo_label.setToolTip("УРУС")
        self.logo_label.setContentsMargins(12, 0, 4, 0)
        self.logo_label.setFixedHeight(TOOLBAR_LOGO_HEIGHT_PX)
        self.logo_label.setStyleSheet(
            f"background-color: {THEME_BG_PANEL}; border: none;"
        )
        toolbar.addWidget(self.logo_label)

    @staticmethod
    def _composite_on_theme(src: QPixmap) -> QPixmap:
        """Подложить под лого фон темы — чтобы он не «горел» белым/прозрачным
        пятном на тёмной строке состояния.
        """
        out = QPixmap(src.size())
        out.fill(QColor(THEME_BG_PANEL))
        painter = QPainter(out)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(0, 0, src)
        finally:
            painter.end()
        return out

    # ==================================================================
    # signals / shortcuts
    # ==================================================================

    def _setup_shortcuts(self) -> None:
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(self._open_selected_camera)
        sc_r = QShortcut(QKeySequence("Ctrl+R"), self)
        sc_r.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc_r.activated.connect(self._manual_check_all)

    def _bind_signals(self) -> None:
        self.sidebar.object_selected.connect(self._on_object_selected)
        self.sidebar.check_status_requested.connect(self._on_priority_object_check_requested)
        self.sidebar.delete_requested.connect(self._delete_object)
        self.sidebar.rename_requested.connect(self._rename_object)
        self.table.open_requested.connect(self._open_camera_stream)
        self.table.check_requested.connect(self._check_single_camera)
        self.table.edit_requested.connect(self._edit_camera)
        self.table.delete_requested.connect(self._delete_camera)
        self.table.coordinates_copied.connect(self._on_coords_copied)
        self.table.rtsp_copied.connect(self._on_rtsp_copied)
        self.table.sort_changed.connect(self._on_sort_changed)
        self.table.bulk_check_requested.connect(self._bulk_check)
        self.table.bulk_delete_requested.connect(self._bulk_delete)
        self.table.bulk_edit_requested.connect(self._bulk_edit)
        self.checker.camera_checked.connect(self._on_camera_checked)
        self.checker.camera_check_started.connect(self._on_camera_check_started)

    # ==================================================================
    # logging helpers
    # ==================================================================

    def _log(self, message: str) -> None:
        # Status bar убран — пишем сообщения только в системный логгер.
        # При необходимости их можно увидеть через Console.app (macOS) или
        # ~/Library/Logs/...
        _log.info("%s", message)

    def _render_poll_activity(self) -> None:
        if not hasattr(self, "poll_activity"):
            return
        if not self._active_checks:
            self._poll_ui = None
            self.poll_activity.setText("Жду опроса")
            self.poll_activity.setStyleSheet(
                "QLineEdit { color: #2d9d5f; font-weight: 700; }"
            )
            return
        if self._poll_ui == "full":
            self.poll_activity.setText("Общая проверка")
            self.poll_activity.setStyleSheet(
                "QLineEdit { color: #eab308; font-weight: 600; }"
            )
            return
        if self._poll_ui == "local":
            self.poll_activity.setText("Локальный опрос")
            self.poll_activity.setStyleSheet(
                "QLineEdit { color: #eab308; font-weight: 600; }"
            )
            return
        self.poll_activity.setText("Проверяю камеры")
        self.poll_activity.setStyleSheet(
            "QLineEdit { color: #eab308; font-weight: 600; }"
        )

    def _log_error(self, message: str) -> None:
        """Разовая ошибка не от проверки камер (FFPLAY/IMPORT…)."""
        _log.warning("%s", message)
        if self._closing:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self._misc_errors.append(f"[{ts}] {message}")
        self._render_errors_pane()

    def _render_errors_pane(self) -> None:
        """Перерисовать панель ошибок: дедуп по камерам + хронологические события."""
        if self._closing or not hasattr(self, "error_text"):
            return
        lines: list[str] = []
        for cam_id in sorted(
            self._camera_errors,
            key=lambda cid: (
                0,
                (self._camera_table_row_num(cid) or 10**9),
                cid,
            ),
        ):
            lines.append(self._camera_errors[cam_id])
        if self._misc_errors:
            if lines:
                lines.append("")
                lines.append("— События —")
            lines.extend(self._misc_errors)
        self.error_text.setPlainText("\n".join(lines))

    def _camera_table_row_num(self, camera_id: int) -> Optional[int]:
        """Позиция камеры в текущей таблице (колонка «№»), 1-based."""
        for idx, c in enumerate(self.cameras_cache):
            if c.id == camera_id:
                return idx + 1
        return None

    def _camera_row_label(self, camera_id: int) -> str:
        """Подпись для логов: табличный №, иначе внутренний id БД."""
        n = self._camera_table_row_num(camera_id)
        return f"№ {n}" if n is not None else f"id {camera_id}"

    def _format_camera_error(self, cam: Optional[CameraModel], camera_id: int) -> str:
        label = self._camera_row_label(camera_id)
        if cam is None:
            return f"— - камера ({label})"
        obj = cam.object_name or "—"
        name = cam.camera_name or "—"
        return f"{obj} - {name} ({label})"

    def _set_camera_error(self, camera_id: int, line: str) -> None:
        """Обновить запись об ошибке для камеры (без дублей)."""
        if self._camera_errors.get(camera_id) == line:
            return
        self._camera_errors[camera_id] = line
        self._render_errors_pane()

    def _clear_camera_error(self, camera_id: int) -> None:
        if self._camera_errors.pop(camera_id, None) is not None:
            self._render_errors_pane()

    # ==================================================================
    # data refresh
    # ==================================================================

    def _refresh_objects(self) -> None:
        current_id = self.sidebar.current_object_id()
        objects = self.repo.list_objects()
        self.objects_cache = objects
        self.sidebar.populate(objects)
        if current_id is not None:
            self.sidebar.select_object(current_id)
        elif objects:
            self.current_object_id = objects[0].id

    def _refresh_cameras(self) -> None:
        cameras = self.repo.list_cameras(
            object_id=self.current_object_id,
            search=self.search_input.text(),
            status_filter=self.status_filter.currentText(),
        )
        self.cameras_cache = self._apply_sort(cameras)
        self.table.populate(self.cameras_cache)
        if hasattr(self, "map_view"):
            self.map_view.set_cameras(self.cameras_cache)
        if hasattr(self, "_view_stack") and self._view_stack.currentIndex() == 1:
            self.dashboard.refresh(status_filter=self.status_filter.currentText())

    def _refresh_views_after_checks(self) -> None:
        if self._closing:
            return
        self._refresh_objects()
        self._refresh_cameras()

    # ==================================================================
    # sorting
    # ==================================================================

    def _apply_sort(self, cameras: list[CameraModel]) -> list[CameraModel]:
        col = self._sort_column
        order = self._sort_order
        T = self.table
        status_rank = {"online": 0, "offline": 1, "unknown": 2}

        def key(cam: CameraModel):
            if col == T.COL_NUM:
                return cam.id
            if col == T.COL_OBJECT:
                return (cam.object_name or "").lower()
            if col == T.COL_UIN:
                return (cam.uin or "").lower()
            if col == T.COL_NAME:
                return (cam.camera_name or "").lower()
            if col == T.COL_TYPE:
                return (cam.group_name or "").lower()
            if col == T.COL_GPS:
                return (cam.gps_coords or "").lower()
            if col == T.COL_STATUS:
                return status_rank.get(cam.status, 9)
            if col == T.COL_PING:
                if cam.last_ping_ok is None:
                    return (3, 0)
                if not cam.last_ping_ok:
                    # ICMP-блок (RTSP online) сортируем выше «реально мёртвых».
                    return (1 if cam.status == "online" else 2, 0)
                return (0, cam.last_ping_ms if cam.last_ping_ms is not None else 0)
            if col == T.COL_CHECKED:
                return cam.last_checked_at or ""
            if col == T.COL_ERR:
                return cam.last_error or ""
            if col == T.COL_RTSP:
                return (cam.rtsp_url or "").lower()
            return cam.camera_name or ""

        return sorted(cameras, key=key, reverse=(order == Qt.SortOrder.DescendingOrder))

    def _on_sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        self._sort_column = column
        self._sort_order = order
        self._refresh_cameras()

    # ==================================================================
    # objects: select / add / rename / delete
    # ==================================================================

    def _on_object_selected(self, object_id: int) -> None:
        self.current_object_id = object_id
        self._refresh_cameras()

    def _selected_object(self) -> Optional[ObjectModel]:
        for obj in self.objects_cache:
            if obj.id == self.current_object_id:
                return obj
        return None

    def _add_object(self) -> None:
        dlg = ObjectDialog(self)
        if not dlg.exec():
            return
        try:
            self.repo.add_object(dlg.name)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать объект:\n{exc}")
            return
        self._refresh_objects()
        self._log(f"Создан объект: {dlg.name}")

    def _rename_object(self, object_id: int) -> None:
        obj = next((o for o in self.objects_cache if o.id == object_id), None)
        if not obj:
            return
        new_name, ok = QInputDialog.getText(
            self, "Переименовать объект", "Новое название объекта:",
            QLineEdit.EchoMode.Normal, obj.name,
        )
        if not ok:
            return
        new_name = (new_name or "").strip()
        if not new_name:
            QMessageBox.warning(self, "Переименование", "Название не может быть пустым")
            return
        if new_name == obj.name:
            return
        clash = next(
            (o for o in self.objects_cache
             if o.id != object_id and o.name.lower() == new_name.lower()),
            None,
        )
        if clash:
            QMessageBox.warning(
                self, "Переименование",
                f"Объект с названием «{new_name}» уже существует.",
            )
            return
        try:
            self.repo.update_object(object_id, new_name)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось переименовать объект:\n{exc}")
            return
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Объект переименован: «{obj.name}» → «{new_name}»")

    def _delete_object(self, object_id: int) -> None:
        obj = next((o for o in self.objects_cache if o.id == object_id), None)
        if not obj:
            return
        resp = QMessageBox.question(
            self, "Удаление",
            f"Удалить объект «{obj.name}» и все его камеры ({obj.camera_count})?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.repo.delete_object(object_id)
        if self.current_object_id == object_id:
            self.current_object_id = None
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Удален объект: {obj.name}")

    # ==================================================================
    # cameras: add / edit / delete
    # ==================================================================

    def _add_camera(self) -> None:
        if not self.objects_cache:
            QMessageBox.information(self, "Камера", "Сначала добавьте хотя бы один объект")
            return
        self.map_view.prepare_for_modal_dialog()
        self.table.setFocus(Qt.FocusReason.OtherFocusReason)
        QApplication.processEvents()
        dlg = CameraDialog(self.objects_cache, parent=self)
        if not dlg.exec():
            return
        d = dlg.form_data()
        try:
            self.repo.add_camera(
                object_id=d.object_id,
                camera_identifier=d.camera_identifier,
                camera_name=d.camera_name,
                group_name=d.group_name,
                gps_coords=d.gps_coords,
                uin=d.uin,
                rtsp_url=d.rtsp_url,
                enabled=d.enabled,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось добавить камеру:\n{exc}")
            return
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Добавлена камера: {d.camera_name}")

    def _edit_camera(self, camera_id: int) -> None:
        cam = self.repo.get_camera(camera_id)
        if not cam:
            return
        self.map_view.prepare_for_modal_dialog()
        self.table.setFocus(Qt.FocusReason.OtherFocusReason)
        QApplication.processEvents()
        dlg = CameraDialog(self.objects_cache, parent=self, camera=cam)
        if not dlg.exec():
            return
        d = dlg.form_data()
        try:
            self.repo.update_camera(
                camera_id=cam.id,
                object_id=d.object_id,
                camera_identifier=d.camera_identifier,
                camera_name=d.camera_name,
                group_name=d.group_name,
                gps_coords=d.gps_coords,
                uin=d.uin,
                rtsp_url=d.rtsp_url,
                enabled=d.enabled,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось обновить камеру:\n{exc}")
            return
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Обновлена камера: {d.camera_name}")

    def _delete_camera(self, camera_id: int) -> None:
        cam = self.repo.get_camera(camera_id)
        if not cam:
            return
        resp = QMessageBox.question(
            self, "Удаление", f"Удалить камеру «{cam.camera_name}»?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.repo.delete_camera(camera_id)
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Удалена камера: {cam.camera_name}")

    # ==================================================================
    # cameras: bulk operations
    # ==================================================================

    def _bulk_check(self, camera_ids: list[int]) -> None:
        cams = [c for c in (self.repo.get_camera(cid) for cid in camera_ids) if c]
        self._start_checks(cams, f"Локальная проверка выделенных ({len(cams)})", poll_ui="local")

    def _bulk_delete(self, camera_ids: list[int]) -> None:
        if not camera_ids:
            return
        resp = QMessageBox.question(
            self, "Удаление", f"Удалить выделенные камеры ({len(camera_ids)})?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = self.repo.bulk_delete_cameras(camera_ids)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить камеры:\n{exc}")
            return
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Удалено камер: {removed}")

    # Простые текстовые поля: общий обработчик через QInputDialog.
    _BULK_TEXT_FIELDS: dict[str, tuple[str, str]] = {
        "group_name": ("Тип (группа)", "Новый тип/группа для выделенных камер:"),
        "gps_coords": ("Координаты (GPS)", "Новые координаты для выделенных камер (пусто = очистить):"),
        "uin": ("УИН", "Новый УИН для выделенных камер (пусто = очистить):"),
    }

    def _bulk_edit(self, camera_ids: list[int], field: str) -> None:
        if not camera_ids:
            return
        try:
            if field in self._BULK_TEXT_FIELDS:
                self._bulk_edit_text_field(camera_ids, field)
            elif field == "object_id":
                self._bulk_edit_move_to_object(camera_ids)
            elif field in ("enable", "disable"):
                self._bulk_edit_enabled(camera_ids, enable=(field == "enable"))
            else:
                return
        except Exception as exc:
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось применить массовое изменение:\n{exc}",
            )
            return

        self._refresh_objects()
        self._refresh_cameras()

    def _bulk_edit_text_field(self, camera_ids: list[int], field: str) -> None:
        title, prompt = self._BULK_TEXT_FIELDS[field]
        value, ok = QInputDialog.getText(
            self, f"Массовое изменение: {title}",
            f"{prompt}\nЗатронет камер: {len(camera_ids)}",
            QLineEdit.EchoMode.Normal, "",
        )
        if not ok:
            return
        affected = self.repo.bulk_update_field(camera_ids, field, value.strip())
        self._log(f"Массово обновлено «{title}» у {affected} камер")

    def _bulk_edit_move_to_object(self, camera_ids: list[int]) -> None:
        if not self.objects_cache:
            QMessageBox.information(self, "Перенос", "Нет доступных объектов")
            return
        names = [o.name for o in self.objects_cache]
        name, ok = QInputDialog.getItem(
            self, "Перенести в объект",
            f"Выберите объект для {len(camera_ids)} камер:",
            names, 0, False,
        )
        if not ok:
            return
        target = next((o for o in self.objects_cache if o.name == name), None)
        if not target:
            return
        affected = self.repo.bulk_update_field(camera_ids, "object_id", target.id)
        self._log(f"Перенесено камер в «{target.name}»: {affected}")

    def _bulk_edit_enabled(self, camera_ids: list[int], *, enable: bool) -> None:
        affected = self.repo.bulk_update_field(camera_ids, "enabled", 1 if enable else 0)
        state = "включено" if enable else "выключено"
        self._log(f"Массово {state} камер: {affected}")

    # ==================================================================
    # ffplay
    # ==================================================================

    def _open_selected_camera(self) -> None:
        cam_id = self.table.selected_camera_id()
        if cam_id is None:
            self._log("Открыть камеру: не выбрана строка в таблице")
            return
        self._open_camera_stream(cam_id)

    def _open_camera_stream(self, camera_id: int) -> None:
        cam = self.repo.get_camera(camera_id)
        if not cam:
            return
        result = self.ffplay.launch(cam.rtsp_url, f"{cam.object_name} / {cam.camera_name}")
        if not result.ok:
            err = result.error or "Не удалось открыть поток"
            QMessageBox.warning(self, "FFplay", err)
            self._log_error(
                f"FFPLAY: {cam.object_name} - {cam.camera_name} "
                f"({self._camera_row_label(cam.id)}) — {err}"
            )
            return
        self._log(f"Открыт поток: {cam.camera_name}")
        self.lower()
        self.clearFocus()
        QTimer.singleShot(FFPLAY_FOCUS_DELAY_MS, self._activate_ffplay_window)

    def _activate_ffplay_window(self) -> None:
        osa = shutil.which("osascript")
        if not osa:
            return
        try:
            subprocess.Popen(
                [
                    osa, "-e",
                    'tell application "System Events" to set frontmost of '
                    '(first process whose name is "ffplay") to true',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # ==================================================================
    # checker
    # ==================================================================

    def _check_single_camera(self, camera_id: int) -> None:
        cam = self.repo.get_camera(camera_id)
        if not cam:
            return
        self._start_checks([cam], "Локальная проверка одной камеры", poll_ui="local")

    def _manual_check_all(self) -> None:
        cameras = self.repo.list_cameras(object_id=None, search="", status_filter="all")
        enabled = [c for c in cameras if c.enabled]
        self._start_checks(enabled, "Ручная проверка всех объектов", poll_ui="full")

    def _auto_check_offline_objects(self) -> None:
        if self._priority_object_id is not None:
            return
        if self._active_checks:
            self._log("Автоопрос проблемных объектов: предыдущий цикл ещё идёт, пропускаем")
            return
        all_cameras = self.repo.list_cameras(object_id=None, search="", status_filter="all")
        bad_object_ids = {
            int(c.object_id) for c in all_cameras if (c.status or "").lower() != "online"
        }
        target = [c for c in all_cameras if c.enabled and int(c.object_id) in bad_object_ids]
        self._start_checks(target, "Автоопрос объектов с offline (каждые 3 мин)", poll_ui="auto")

    def _auto_check_online_objects(self) -> None:
        if self._priority_object_id is not None:
            return
        if self._active_checks:
            self._log("Автоопрос online-объектов: предыдущий цикл ещё идёт, пропускаем")
            return
        all_cameras = self.repo.list_cameras(object_id=None, search="", status_filter="all")
        bad_object_ids = {
            int(c.object_id) for c in all_cameras if (c.status or "").lower() != "online"
        }
        target = [
            c
            for c in all_cameras
            if c.enabled and int(c.object_id) not in bad_object_ids
        ]
        self._start_checks(target, "Автоопрос объектов без offline (каждые 10 мин)", poll_ui="auto")

    def _on_priority_object_check_requested(self, object_id: int) -> None:
        cameras = self.repo.list_cameras(object_id=object_id, search="", status_filter="all")
        enabled = [c for c in cameras if c.enabled]
        self._priority_object_id = object_id
        self._offline_timer.stop()
        self._online_timer.stop()
        self.checker.clear_pending()
        QThreadPool.globalInstance().clear()
        self._active_checks.clear()
        self._start_checks(enabled, f"Приоритетный опрос объекта {object_id}", poll_ui="local")

    def _start_checks(self, cameras: list[CameraModel], reason: str, *, poll_ui: str = "auto") -> None:
        if not cameras:
            self._log(f"{reason}: камер для опроса нет")
            self._render_poll_activity()
            if self._priority_object_id is not None:
                self._finish_priority_mode()
            return
        self._poll_ui = poll_ui
        for cam in cameras:
            self._active_checks[int(cam.id)] = f"{cam.object_name} / {cam.camera_name}"
        self._render_poll_activity()
        self._log(f"{reason}: {len(cameras)} камер")
        self.checker.check_many(cameras)

    def _on_camera_check_started(
        self,
        camera_id: int,
        _object_id: int,
        object_name: str,
        camera_name: str,
    ) -> None:
        self._active_checks[int(camera_id)] = f"{object_name} / {camera_name}"
        self._recent_poll_events.appendleft(f"{object_name} / {camera_name}")
        self._render_poll_activity()

    def _finish_priority_mode(self) -> None:
        self._priority_object_id = None
        self._offline_timer.start()
        self._online_timer.start()
        self._log("Приоритетный опрос завершён, плановые циклы возобновлены")

    def _on_camera_checked(self, result: CheckResult) -> None:
        if self._closing:
            return
        self.repo.update_camera_status(
            camera_id=result.camera_id,
            status=result.status,
            last_checked_at=result.checked_at,
            last_error=result.error,
            last_seen_online_at=result.seen_online_at,
            last_ping_ok=result.ping_ok,
            last_ping_ms=result.ping_ms,
        )
        cam = self.repo.get_camera(result.camera_id)
        ping_part = self._format_ping_part(result.ping_ok, result.ping_ms)
        row_lbl = self._camera_row_label(result.camera_id)
        self._log(
            f"Проверка завершена {row_lbl}: {result.status}"
            + (f" ({result.error})" if result.error else "")
            + ping_part
        )
        # Обновляем точечный маркер на карте без перерисовки.
        if hasattr(self, "map_view"):
            self.map_view.update_camera_status(result.camera_id, result.status)

        # Дедупликация: запись по камере добавляется/убирается только тут,
        # повторные проверки той же камеры не плодят дубли в панели «Ошибки».
        if result.status == "online":
            self._clear_camera_error(result.camera_id)
        else:
            self._set_camera_error(
                result.camera_id, self._format_camera_error(cam, result.camera_id)
            )
        if not self._refresh_debounce.isActive():
            self._refresh_debounce.start()
        self._active_checks.pop(int(result.camera_id), None)
        self._render_poll_activity()
        if self._priority_object_id is not None and not self._active_checks:
            self._finish_priority_mode()

    @staticmethod
    def _format_ping_part(ping_ok: Optional[bool], ping_ms: Optional[int]) -> str:
        if ping_ok is None:
            return ""
        if ping_ok:
            return f" [ping {ping_ms} ms]" if ping_ms is not None else " [ping OK]"
        return " [ping fail]"

    # ==================================================================
    # clipboard callbacks (от таблицы)
    # ==================================================================

    def _on_coords_copied(self, coords: str) -> None:
        self._log(f"Координаты скопированы: {coords}")

    def _on_rtsp_copied(self, url: str) -> None:
        self._log(f"RTSP-ссылка скопирована: {mask_rtsp_url(url)}")

    # ==================================================================
    # import dialog
    # ==================================================================

    def _open_import_dialog(self) -> None:
        try:
            dlg = ImportDialog(self.import_service, self.template_service, self)
        except Exception as exc:
            tb = traceback.format_exc()
            self._log(f"Ошибка открытия импорта: {exc}")
            self._log_error(f"IMPORT: {exc}")
            QMessageBox.critical(
                self, "Импорт", f"Не удалось открыть окно импорта:\n{exc}\n\n{tb}",
            )
            return
        dlg.import_completed.connect(self._on_import_completed)
        dlg.exec()

    def _on_import_completed(self, created: int, updated: int) -> None:
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Импорт завершен. Создано={created}, обновлено={updated}")
        if created + updated > 0:
            QTimer.singleShot(
                50,
                lambda: self._resize_cameras_splitter_for_visible_table_rows(IMPORT_TABLE_VISIBLE_ROWS),
            )
        # По запросу: сразу после загрузки формы запускать опрос камер.
        if created + updated <= 0:
            return
        if self._active_checks:
            # Останавливаем текущую очередь, чтобы приоритетно проверить
            # актуально загруженные данные.
            self.checker.clear_pending()
            QThreadPool.globalInstance().clear()
            self._active_checks.clear()
            self._render_poll_activity()
        cameras = self.repo.list_cameras(object_id=None, search="", status_filter="all")
        enabled = [c for c in cameras if c.enabled]
        self._start_checks(enabled, "Опрос после импорта формы", poll_ui="local")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._enforce_cameras_bottom_height_cap()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._enforce_cameras_bottom_height_cap()

    # ==================================================================
    # close
    # ==================================================================

    def closeEvent(self, event) -> None:
        self._closing = True
        self._save_splitter_states()
        self._offline_timer.stop()
        self._online_timer.stop()
        self._poll_activity_timer.stop()
        self._refresh_debounce.stop()

        self._disconnect_background_slots()

        pool = QThreadPool.globalInstance()
        # Не ждать десятки зависших ffprobe: снимаем очередь иронов и рвём блокирующие probes.
        self.checker.clear_pending()
        pool.clear()

        if hasattr(self, "map_view"):
            self.map_view.prepare_shutdown()
            QApplication.processEvents()

        n_probe = terminate_ffprobe_children()
        if n_probe:
            _log.info("При выходе завершено процессов ffprobe: %s", n_probe)

        if not pool.waitForDone(THREADPOOL_SHUTDOWN_WAIT_MS):
            _log.warning(
                "При закрытии QThreadPool не освободился за %s мс — принудительно очищаем",
                THREADPOOL_SHUTDOWN_WAIT_MS,
            )
            self.checker.clear_pending()
            pool.clear()

        try:
            killed = self.ffplay.terminate_all()
            if killed:
                _log.info("Закрыто окон ffplay: %s", killed)
        except Exception as exc:
            _log.warning("Не удалось закрыть ffplay: %s", exc)
        super().closeEvent(event)

    def _disconnect_background_slots(self) -> None:
        for sig, slot in (
            (self.checker.camera_checked, self._on_camera_checked),
            (self.checker.camera_check_started, self._on_camera_check_started),
        ):
            try:
                sig.disconnect(slot)
            except TypeError:
                pass
