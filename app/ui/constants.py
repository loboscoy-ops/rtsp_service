"""UI-константы: цвета, размеры, палитра.

Хранится отдельно от виджетов, чтобы не плодить «магические числа» по всему UI.
"""
from __future__ import annotations


# --- цветовая палитра (тёмный дашборд) ---------------------------------------

# Фон и текст
THEME_BG_WINDOW = "#12141a"
THEME_BG_PANEL = "#1a1e28"
THEME_BG_INPUT = "#222831"
THEME_BG_ROW_ALT = "#161a22"
THEME_FG = "#e8eaed"
THEME_FG_MUTED = "#8b929e"
THEME_BORDER = "#2d3544"
THEME_ACCENT = "#3d8bfd"
THEME_ACCENT_HOVER = "#5a9dff"

# Статусы камер / сеть (согласованы с маркерами карты)
PING_OK_COLOR = "#3ecf8e"
PING_BLOCKED_COLOR = "#e3a008"
PING_DEAD_COLOR = "#f85149"

STATUS_ONLINE_FG = "#3ecf8e"
STATUS_OFFLINE_FG = "#f85149"
STATUS_UNKNOWN_FG = "#8b949e"

ERROR_PANE_BG = "#2a1214"
ERROR_PANE_FG = "#ff8b8b"
ERROR_PANE_QSS = (
    f"QTextEdit {{ background-color: {ERROR_PANE_BG}; color: {ERROR_PANE_FG}; "
    f"border: 1px solid {THEME_BORDER}; border-radius: 6px; padding: 6px; }}"
)

# Глобальная таблица стилей Qt (Fusion-подобный тёмный UI в духе мониторинга)
APP_GLOBAL_QSS = f"""
QWidget {{
  background-color: {THEME_BG_WINDOW};
  color: {THEME_FG};
  font-size: 13px;
}}
QMainWindow::separator {{
  background: {THEME_BORDER};
  width: 1px; height: 1px;
}}
QMenuBar {{
  background-color: {THEME_BG_PANEL};
  color: {THEME_FG};
  border-bottom: 1px solid {THEME_BORDER};
  padding: 2px 4px;
}}
QMenuBar::item {{
  padding: 6px 10px;
  border-radius: 4px;
}}
QMenuBar::item:selected {{
  background-color: {THEME_BG_INPUT};
}}
QMenu {{
  background-color: {THEME_BG_PANEL};
  color: {THEME_FG};
  border: 1px solid {THEME_BORDER};
  padding: 4px;
}}
QMenu::item {{
  padding: 8px 28px 8px 12px;
  border-radius: 4px;
}}
QMenu::item:selected {{
  background-color: rgba(61, 139, 253, 0.35);
}}
QMenu::separator {{
  height: 1px;
  background: {THEME_BORDER};
  margin: 4px 8px;
}}
QToolBar {{
  background-color: {THEME_BG_PANEL};
  border: none;
  border-bottom: 1px solid {THEME_BORDER};
  padding: 4px 6px;
  spacing: 6px;
}}
QToolBar QToolButton,
QToolBar QPushButton {{
  background-color: {THEME_BG_INPUT};
  color: {THEME_FG};
  border: 1px solid {THEME_BORDER};
  border-radius: 6px;
  padding: 6px 12px;
  min-height: 22px;
}}
QToolBar QPushButton:hover {{
  background-color: #2a3140;
  border-color: {THEME_ACCENT};
}}
QToolBar QPushButton:pressed {{
  background-color: #323a4a;
}}
QToolBar QPushButton:disabled {{
  color: {THEME_FG_MUTED};
  background-color: {THEME_BG_PANEL};
}}
QToolBar QPushButton#ViewSwitch {{
  background-color: transparent;
  border: 1px solid {THEME_BORDER};
  color: {THEME_FG_MUTED};
  font-weight: 600;
  padding: 6px 16px;
}}
QToolBar QPushButton#ViewSwitch:hover {{
  color: {THEME_FG};
  border-color: {THEME_ACCENT};
}}
QToolBar QPushButton#ViewSwitch:checked {{
  background-color: rgba(61, 139, 253, 0.18);
  border-color: {THEME_ACCENT};
  color: {THEME_FG};
}}
QToolBar QLabel {{
  color: {THEME_FG_MUTED};
  background: transparent;
}}
QLineEdit, QComboBox, QSpinBox {{
  background-color: {THEME_BG_INPUT};
  color: {THEME_FG};
  border: 1px solid {THEME_BORDER};
  border-radius: 6px;
  padding: 5px 8px;
  min-height: 22px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
  border-color: {THEME_ACCENT};
}}
QComboBox::drop-down {{
  border: none;
  width: 22px;
}}
QComboBox QAbstractItemView {{
  background-color: {THEME_BG_INPUT};
  color: {THEME_FG};
  selection-background-color: {THEME_ACCENT};
  selection-color: #ffffff;
  border: 1px solid {THEME_BORDER};
}}
QTableWidget {{
  background-color: {THEME_BG_PANEL};
  alternate-background-color: {THEME_BG_ROW_ALT};
  color: {THEME_FG};
  gridline-color: {THEME_BORDER};
  border: 1px solid {THEME_BORDER};
  border-radius: 8px;
}}
QTableWidget::item {{
  padding: 4px;
}}
QTableWidget::item:selected {{
  background-color: rgba(61, 139, 253, 0.35);
  color: {THEME_FG};
}}
QHeaderView::section {{
  background-color: {THEME_BG_INPUT};
  color: {THEME_FG_MUTED};
  padding: 8px 6px;
  border: none;
  border-bottom: 2px solid {THEME_BORDER};
  border-right: 1px solid {THEME_BORDER};
  font-weight: 600;
}}
QListWidget {{
  background-color: {THEME_BG_PANEL};
  color: {THEME_FG};
  border: 1px solid {THEME_BORDER};
  border-radius: 8px;
  padding: 4px;
}}
QListWidget::item {{
  padding: 10px 8px;
  border-radius: 6px;
}}
QListWidget::item:selected {{
  background-color: rgba(61, 139, 253, 0.25);
  color: {THEME_FG};
}}
QListWidget::item:hover:!selected {{
  background-color: {THEME_BG_INPUT};
}}
QSplitter::handle {{
  background-color: {THEME_BORDER};
}}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QStatusBar {{
  background-color: {THEME_BG_PANEL};
  color: {THEME_FG_MUTED};
  border-top: 1px solid {THEME_BORDER};
}}
QPushButton {{
  background-color: {THEME_BG_INPUT};
  color: {THEME_FG};
  border: 1px solid {THEME_BORDER};
  border-radius: 6px;
  padding: 6px 14px;
  min-height: 22px;
}}
QPushButton:hover {{
  background-color: #2a3140;
  border-color: {THEME_ACCENT};
}}
QPushButton:pressed {{
  background-color: #323a4a;
}}
QPushButton#ErrorsClearBtn {{
  background-color: rgba(248, 81, 73, 0.18);
  color: #ffd3d3;
  border: 1px solid rgba(248, 81, 73, 0.55);
  padding: 4px 10px;
  min-height: 18px;
  font-weight: 600;
}}
QPushButton#ErrorsClearBtn:hover {{
  background-color: rgba(248, 81, 73, 0.32);
  border-color: #f85149;
}}
QPushButton#ErrorsClearBtn:pressed {{
  background-color: rgba(248, 81, 73, 0.45);
}}
QPushButton:disabled {{
  color: {THEME_FG_MUTED};
  background-color: {THEME_BG_PANEL};
}}
QGroupBox {{
  font-weight: 600;
  color: {THEME_FG};
  border: 1px solid {THEME_BORDER};
  border-radius: 8px;
  margin-top: 12px;
  padding-top: 8px;
}}
QGroupBox::title {{
  subcontrol-origin: margin;
  left: 12px;
  padding: 0 6px;
  color: {THEME_FG_MUTED};
}}
QDialog {{
  background-color: {THEME_BG_WINDOW};
}}
QDialogButtonBox QPushButton {{
  min-width: 88px;
}}
QScrollBar:vertical {{
  background: {THEME_BG_PANEL};
  width: 10px;
  margin: 0;
  border-radius: 5px;
}}
QScrollBar::handle:vertical {{
  background: #3d4555;
  min-height: 28px;
  border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: #4a5366; }}
QScrollBar:horizontal {{
  background: {THEME_BG_PANEL};
  height: 10px;
  margin: 0;
  border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
  background: #3d4555;
  min-width: 28px;
  border-radius: 5px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QMessageBox {{
  background-color: {THEME_BG_WINDOW};
}}
QMessageBox QLabel {{
  color: {THEME_FG};
}}
QProgressDialog {{
  background-color: {THEME_BG_WINDOW};
}}
QFormLayout QLabel {{
  color: {THEME_FG_MUTED};
}}
"""


# --- размеры главного окна --------------------------------------------------

WINDOW_DEFAULT_SIZE = (1450, 880)

SIDEBAR_DEFAULT_WIDTH = 260
SIDEBAR_MIN_WIDTH = 220
RIGHT_PANE_DEFAULT_WIDTH = 1190

# Стартовые размеры вертикального сплиттера: верх — таблица, низ — карта+ошибки.
CAMERAS_SPLITTER_DEFAULT_SIZES = (520, 360)
# После импорта формы: высота зоны таблицы ≈ столько видимых строк (остальное — скролл).
IMPORT_TABLE_VISIBLE_ROWS = 6
# Стартовые размеры нижнего горизонтального сплиттера: слева карта, справа ошибки.
BOTTOM_SPLITTER_DEFAULT_SIZES = (820, 380)

LOGO_HEIGHT_PX = 84
STATUSBAR_PADDING_PX = 8
# Высота лого, прижатого в правый угол тулбара. Должна совпадать с
# фактической высотой кнопок тулбара (≈ 28 px), иначе логотип будет либо
# выступать за тулбар, либо «висеть» сильно меньше его.
TOOLBAR_LOGO_HEIGHT_PX = 28

# --- интервалы --------------------------------------------------------------

CHECK_TIMER_MIN_INTERVAL_SEC = 15
REFRESH_DEBOUNCE_MS = 250
STATUS_BAR_MESSAGE_MS = 5000
FFPLAY_FOCUS_DELAY_MS = 300

# Ожидание уже запущенных QRunnable при закрытии окна (секунды).
# Очередь проверок при выходе снимается через pool.clear(), активные ffprobe
# завершаются через terminate_ffprobe_children(); это окно — добежать коротким задачам.
THREADPOOL_SHUTDOWN_WAIT_MS = 8_000


# --- прочее ----------------------------------------------------------------

CELL_PREVIEW_LIMIT = 40           # сколько символов вмещает превью «Копировать <колонка>: …»
ERROR_LOG_LIMIT = 500             # обрезка длинных stderr из ffprobe
