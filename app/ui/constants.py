"""UI-константы: цвета, размеры, палитра.

Хранится отдельно от виджетов, чтобы не плодить «магические числа» по всему UI.
"""
from __future__ import annotations


# --- цветовая палитра -------------------------------------------------------

PING_OK_COLOR = "#3ecf8e"          # зелёный — хост отвечает на ICMP
PING_BLOCKED_COLOR = "#d4a017"     # жёлтый — RTSP online, но ICMP режется
PING_DEAD_COLOR = "#ff8b8b"        # красный — хост не отвечает

ERROR_PANE_BG = "#2a1414"
ERROR_PANE_FG = "#ff8b8b"
ERROR_PANE_QSS = (
    f"QTextEdit {{ background-color: {ERROR_PANE_BG}; color: {ERROR_PANE_FG}; }}"
)


# --- размеры главного окна --------------------------------------------------

WINDOW_DEFAULT_SIZE = (1450, 880)

SIDEBAR_DEFAULT_WIDTH = 260
SIDEBAR_MIN_WIDTH = 220
RIGHT_PANE_DEFAULT_WIDTH = 1190

# Стартовые размеры вертикального сплиттера: верх — таблица, низ — карта+ошибки.
CAMERAS_SPLITTER_DEFAULT_SIZES = (520, 360)
# Стартовые размеры нижнего горизонтального сплиттера: слева карта, справа ошибки.
BOTTOM_SPLITTER_DEFAULT_SIZES = (820, 380)

LOGO_HEIGHT_PX = 84
STATUSBAR_PADDING_PX = 8

# --- интервалы --------------------------------------------------------------

CHECK_TIMER_MIN_INTERVAL_SEC = 15
REFRESH_DEBOUNCE_MS = 250
STATUS_BAR_MESSAGE_MS = 5000
FFPLAY_FOCUS_DELAY_MS = 300

# Как часто фоном проверяем наличие новых коммитов в origin/main.
GIT_UPDATE_CHECK_INTERVAL_MS = 10 * 60 * 1000   # 10 минут
GIT_UPDATE_FIRST_CHECK_DELAY_MS = 3000          # первая проверка через 3 секунды после старта

# Ожидание уже запущенных QRunnable при закрытии окна (секунды).
# Очередь проверок при выходе снимается через pool.clear(), активные ffprobe
# завершаются через terminate_ffprobe_children(); это окно — добежать коротким задачам.
THREADPOOL_SHUTDOWN_WAIT_MS = 8_000

# QSS для кнопки «Обновить из GitHub», когда есть новые коммиты.
GIT_BTN_HAS_UPDATES_QSS = (
    "QPushButton {"
    "  background-color: #1f9d55;"
    "  color: white;"
    "  font-weight: bold;"
    "  border: 1px solid #157145;"
    "  border-radius: 4px;"
    "  padding: 4px 10px;"
    "}"
    "QPushButton:hover { background-color: #2bb573; }"
    "QPushButton:disabled { background-color: #6a6a6a; color: #ddd; }"
)


# --- прочее ----------------------------------------------------------------

CELL_PREVIEW_LIMIT = 40           # сколько символов вмещает превью «Копировать <колонка>: …»
ERROR_LOG_LIMIT = 500             # обрезка длинных stderr из ffprobe
GIT_PULL_DIALOG_LIMIT = 1500      # сколько вывода git показываем в диалоге
GIT_PULL_LOG_LIMIT = 200          # сколько вывода git кладём в панель ошибок
