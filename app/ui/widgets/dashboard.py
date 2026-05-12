"""Дашборд: KPI-карточки + сводки по объектам/проверкам.

Перерисовывается из MainWindow при переключении на раздел и после автопроверок.
"""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.database.models import CameraModel, ObjectModel
from app.database.repository import Repository
from app.ui.constants import (
    PING_OK_COLOR,
    STATUS_OFFLINE_FG,
    STATUS_ONLINE_FG,
    STATUS_UNKNOWN_FG,
    THEME_ACCENT,
    THEME_BG_PANEL,
    THEME_BORDER,
    THEME_FG,
    THEME_FG_MUTED,
)


_CARD_QSS = (
    f"#StatCard {{ background-color: {THEME_BG_PANEL}; "
    f"border: 1px solid {THEME_BORDER}; border-radius: 10px; }}"
)


class _StatCard(QFrame):
    def __init__(self, title: str, value_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setStyleSheet(_CARD_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(100)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(6)

        self._title = QLabel(title)
        self._title.setStyleSheet(
            f"color: {THEME_FG_MUTED}; font-size: 12px; font-weight: 600;"
            " letter-spacing: 0.5px;"
        )
        self._value = QLabel("—")
        self._value.setStyleSheet(
            f"color: {value_color}; font-size: 30px; font-weight: 700;"
        )
        self._sub = QLabel("")
        self._sub.setStyleSheet(f"color: {THEME_FG_MUTED}; font-size: 11px;")
        self._sub.setVisible(False)

        v.addWidget(self._title)
        v.addWidget(self._value)
        v.addWidget(self._sub)
        v.addStretch(1)

    def set_value(self, text: str, sub: str = "") -> None:
        self._value.setText(text)
        if sub:
            self._sub.setText(sub)
            self._sub.setVisible(True)
        else:
            self._sub.setVisible(False)


def _list_card(title: str) -> tuple[QFrame, QListWidget]:
    wrap = QFrame()
    wrap.setObjectName("StatCard")
    wrap.setStyleSheet(_CARD_QSS)
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)

    header = QLabel(title)
    header.setStyleSheet(f"color: {THEME_FG_MUTED}; font-weight: 600;")
    layout.addWidget(header)

    lst = QListWidget()
    lst.setStyleSheet(
        "QListWidget { background-color: transparent; border: none; }"
        " QListWidget::item { padding: 6px 4px; }"
    )
    lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
    layout.addWidget(lst, 1)
    return wrap, lst


class DashboardView(QWidget):
    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        title = QLabel("Дашборд")
        title.setStyleSheet(
            f"font-size: 22px; font-weight: 700; color: {THEME_FG};"
        )
        root.addWidget(title)

        cards = QGridLayout()
        cards.setHorizontalSpacing(14)
        cards.setVerticalSpacing(14)
        root.addLayout(cards)

        self.card_objects = _StatCard("Объектов", THEME_ACCENT)
        self.card_total = _StatCard("Камер всего", THEME_FG)
        self.card_online = _StatCard("Online", STATUS_ONLINE_FG)
        self.card_offline = _StatCard("Offline", STATUS_OFFLINE_FG)
        self.card_unknown = _StatCard("Unknown", STATUS_UNKNOWN_FG)
        self.card_ping = _StatCard("Сеть OK (ping)", PING_OK_COLOR)

        for i, card in enumerate(
            [
                self.card_objects,
                self.card_total,
                self.card_online,
                self.card_offline,
                self.card_unknown,
                self.card_ping,
            ]
        ):
            cards.addWidget(card, i // 3, i % 3)

        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        root.addLayout(bottom, 1)

        self._offline_card, self._offline_list = _list_card(
            "Объекты с offline-камерами (топ 8)"
        )
        self._recent_card, self._recent_list = _list_card(
            "Недавно проверенные камеры (топ 8)"
        )
        bottom.addWidget(self._offline_card, 1)
        bottom.addWidget(self._recent_card, 1)

    def refresh(self) -> None:
        try:
            objects = self.repo.list_objects()
            cameras = self.repo.list_cameras()
        except Exception:
            return

        self._update_cards(objects, cameras)
        self._update_offline_list(cameras)
        self._update_recent_list(cameras)

    def _update_cards(
        self,
        objects: list[ObjectModel],
        cameras: list[CameraModel],
    ) -> None:
        total = len(cameras)
        online = sum(1 for c in cameras if c.status == "online")
        offline = sum(1 for c in cameras if c.status == "offline")
        unknown = total - online - offline
        ping_ok = sum(1 for c in cameras if c.last_ping_ok)

        def pct(part: int) -> str:
            return f"{(part / total * 100):.0f}% от {total}" if total else "—"

        self.card_objects.set_value(str(len(objects)))
        self.card_total.set_value(str(total))
        self.card_online.set_value(str(online), pct(online))
        self.card_offline.set_value(str(offline), pct(offline))
        self.card_unknown.set_value(str(unknown), pct(unknown))
        self.card_ping.set_value(str(ping_ok), pct(ping_ok))

    def _update_offline_list(self, cameras: Iterable[CameraModel]) -> None:
        per_object: dict[str, int] = {}
        for c in cameras:
            if c.status == "offline":
                per_object[c.object_name] = per_object.get(c.object_name, 0) + 1

        self._offline_list.clear()
        if not per_object:
            item = QListWidgetItem("Все камеры в порядке")
            item.setForeground(Qt.GlobalColor.gray)
            self._offline_list.addItem(item)
            return
        for name, cnt in sorted(per_object.items(), key=lambda kv: (-kv[1], kv[0]))[:8]:
            self._offline_list.addItem(QListWidgetItem(f"{name}  —  {cnt}"))

    def _update_recent_list(self, cameras: Iterable[CameraModel]) -> None:
        recent = sorted(
            (c for c in cameras if c.last_checked_at),
            key=lambda c: c.last_checked_at or "",
            reverse=True,
        )[:8]
        self._recent_list.clear()
        if not recent:
            item = QListWidgetItem("Ещё нет проверок")
            item.setForeground(Qt.GlobalColor.gray)
            self._recent_list.addItem(item)
            return
        for c in recent:
            ts = (c.last_checked_at or "").replace("T", " ")[:19]
            name = c.camera_name or c.camera_identifier
            self._recent_list.addItem(
                QListWidgetItem(f"{c.object_name}: {name} — {c.status}   ({ts})")
            )
