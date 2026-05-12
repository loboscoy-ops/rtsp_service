from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidgetItem

from app.ui.constants import STATUS_OFFLINE_FG, STATUS_ONLINE_FG, STATUS_UNKNOWN_FG


def status_item(status: str) -> QTableWidgetItem:
    text = status or "unknown"
    item = QTableWidgetItem(text)
    if text == "online":
        item.setForeground(QColor(STATUS_ONLINE_FG))
    elif text == "offline":
        item.setForeground(QColor(STATUS_OFFLINE_FG))
    else:
        item.setForeground(QColor(STATUS_UNKNOWN_FG))
    return item
