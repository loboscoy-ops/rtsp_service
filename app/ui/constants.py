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

LOG_PANE_MAX_HEIGHT = 220
LOG_SPLITTER_DEFAULT_SIZES = (600, 600)

LOGO_HEIGHT_PX = 84
STATUSBAR_PADDING_PX = 8

# --- интервалы --------------------------------------------------------------

CHECK_TIMER_MIN_INTERVAL_SEC = 15
REFRESH_DEBOUNCE_MS = 250
STATUS_BAR_MESSAGE_MS = 5000
FFPLAY_FOCUS_DELAY_MS = 300


# --- прочее ----------------------------------------------------------------

CELL_PREVIEW_LIMIT = 40           # сколько символов вмещает превью «Копировать <колонка>: …»
ERROR_LOG_LIMIT = 500             # обрезка длинных stderr из ffprobe
GIT_PULL_DIALOG_LIMIT = 1500      # сколько вывода git показываем в диалоге
GIT_PULL_LOG_LIMIT = 200          # сколько вывода git кладём в панель ошибок
