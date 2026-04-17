from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidgetItem


def status_item(status: str) -> QTableWidgetItem:
    text = status or "unknown"
    item = QTableWidgetItem(text)
    if text == "online":
        item.setForeground(QColor("#2ea043"))
    elif text == "offline":
        item.setForeground(QColor("#d1242f"))
    else:
        item.setForeground(QColor("#6e7781"))
    return item

