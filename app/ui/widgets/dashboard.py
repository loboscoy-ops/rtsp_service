"""Дашборд: светлая карта (по площадкам) + список «Площадки» с пончиками.

Маркеры на карте — по одной точке на объект, в кружке количество камер.
Клик по маркеру или карточке открывает таблицу нужного объекта.
"""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.database.models import CameraModel, ObjectModel
from app.database.repository import Repository
from app.ui.constants import (
    PING_BLOCKED_COLOR,
    STATUS_OFFLINE_FG,
    STATUS_ONLINE_FG,
    THEME_BG_INPUT,
    THEME_BG_PANEL,
    THEME_BG_WINDOW,
    THEME_BORDER,
    THEME_FG,
    THEME_FG_MUTED,
)
from app.ui.widgets.camera_map import CameraMapView


_CARD_BG = THEME_BG_PANEL
_CARD_BORDER = THEME_BORDER
_DONUT_TRACK = "#2d3544"


def _ratio_color(online: int, offline: int, unknown: int) -> str:
    total = online + offline + unknown
    if total == 0:
        return THEME_FG_MUTED
    if offline == 0 and unknown == 0:
        return STATUS_ONLINE_FG
    if offline / total >= 0.4:
        return STATUS_OFFLINE_FG
    return PING_BLOCKED_COLOR


def _object_uin(cameras: Iterable[CameraModel]) -> str:
    for c in cameras:
        if (c.uin or "").strip():
            return c.uin.strip()
    return ""


class _Donut(QWidget):
    """Круговая диаграмма online/offline/unknown с центральной подписью."""

    def __init__(self, size: int = 52, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._online = 0
        self._offline = 0
        self._unknown = 0
        self._size = size
        self._pen_w = max(5, size // 9)
        self.setFixedSize(size, size)

    def set_values(self, online: int, offline: int, unknown: int) -> None:
        self._online = max(0, online)
        self._offline = max(0, offline)
        self._unknown = max(0, unknown)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        total = self._online + self._offline + self._unknown
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen_track = QPen(QColor(_DONUT_TRACK))
            pen_track.setWidth(self._pen_w)
            pen_track.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen_track)
            painter.drawArc(rect, 0, 360 * 16)

            if total > 0:
                start = 90 * 16
                segments = (
                    (self._online, STATUS_ONLINE_FG),
                    (self._unknown, PING_BLOCKED_COLOR),
                    (self._offline, STATUS_OFFLINE_FG),
                )
                for value, color in segments:
                    if value <= 0:
                        continue
                    span = -int(round(360 * 16 * value / total))
                    pen = QPen(QColor(color))
                    pen.setWidth(self._pen_w)
                    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                    painter.setPen(pen)
                    painter.drawArc(rect, start, span)
                    start += span

            painter.setPen(QColor(THEME_FG))
            font = painter.font()
            font.setBold(True)
            font.setPointSize(8 if self._size <= 56 else 10)
            painter.setFont(font)
            if total == 0:
                text = "—"
            elif self._offline == 0 and self._unknown == 0:
                text = "100%\nOK"
            else:
                ok_pct = round(self._online / total * 100)
                bad_pct = 100 - ok_pct
                text = f"{ok_pct}%\n{bad_pct}%"
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        finally:
            painter.end()


class _SiteCard(QFrame):
    """Карточка одной площадки: имя + УИН + пончик + счётчик камер."""

    clicked = Signal(int)

    def __init__(self, obj: ObjectModel, cameras_for_obj: list[CameraModel]) -> None:
        super().__init__()
        self.setObjectName("SiteCard")
        self._object_id = int(obj.id)

        online = sum(1 for c in cameras_for_obj if c.status == "online")
        offline = sum(1 for c in cameras_for_obj if c.status == "offline")
        unknown = len(cameras_for_obj) - online - offline
        accent = _ratio_color(online, offline, unknown)
        uin = _object_uin(cameras_for_obj)

        self.setStyleSheet(
            "QFrame#SiteCard {"
            f" background-color: {_CARD_BG};"
            f" border: 1px solid {_CARD_BORDER};"
            f" border-left: 3px solid {accent};"
            " border-radius: 8px;"
            "}"
            "QFrame#SiteCard:hover {"
            f" background-color: {THEME_BG_INPUT};"
            "}"
        )

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)
        name = QLabel(obj.name or "Объект")
        name.setStyleSheet(
            f"color: {THEME_FG}; font-size: 12px; font-weight: 700;"
        )
        name.setWordWrap(True)
        uin_label = QLabel(f"УИН: {uin}" if uin else "УИН: —")
        uin_label.setStyleSheet(f"color: {THEME_FG_MUTED}; font-size: 10px;")
        text_col.addWidget(name)
        text_col.addWidget(uin_label)
        text_col.addStretch(1)
        outer.addLayout(text_col, 1)

        donut_col = QVBoxLayout()
        donut_col.setSpacing(1)
        donut_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        donut = _Donut(size=52)
        donut.set_values(online, offline, unknown)
        donut_col.addWidget(donut, 0, Qt.AlignmentFlag.AlignHCenter)
        cams_lab = QLabel(f"Камер  {len(cameras_for_obj)}")
        cams_lab.setStyleSheet(f"color: {THEME_FG_MUTED}; font-size: 10px;")
        cams_lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        donut_col.addWidget(cams_lab)
        outer.addLayout(donut_col)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._object_id)
        super().mouseReleaseEvent(event)


class DashboardView(QWidget):
    """Главный виджет дашборда: карта + список площадок."""

    object_selected = Signal(int)

    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo

        self.setStyleSheet(f"DashboardView {{ background-color: {THEME_BG_WINDOW}; }}")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- карта слева (светлая, маркеры по площадкам)
        map_wrap = QFrame()
        map_wrap.setStyleSheet(f"QFrame {{ background-color: {THEME_BG_WINDOW}; }}")
        map_layout = QVBoxLayout(map_wrap)
        map_layout.setContentsMargins(0, 0, 0, 0)
        self.map_view = CameraMapView(self, dark=False)
        map_layout.addWidget(self.map_view)
        root.addWidget(map_wrap, 2)

        # --- правая колонка
        side = QFrame()
        side.setObjectName("SidePane")
        side.setStyleSheet(
            "QFrame#SidePane {"
            f" background-color: {THEME_BG_WINDOW};"
            f" border-left: 1px solid {THEME_BORDER};"
            "}"
        )
        side.setFixedWidth(300)

        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 10, 12, 10)
        side_layout.setSpacing(8)

        title = QLabel("Площадки")
        title.setStyleSheet(
            f"color: {THEME_FG}; font-size: 16px; font-weight: 700; padding-left: 2px;"
        )
        side_layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        side_layout.addWidget(scroll, 1)

        self._cards_host = QWidget()
        self._cards_host.setStyleSheet("background: transparent;")
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)
        scroll.setWidget(self._cards_host)

        root.addWidget(side, 0)

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        try:
            objects = self.repo.list_objects()
            cameras = self.repo.list_cameras()
        except Exception:
            return

        self.map_view.set_objects(objects, cameras)
        self._rebuild_cards(objects, cameras)

    def _rebuild_cards(
        self,
        objects: Iterable[ObjectModel],
        cameras: Iterable[CameraModel],
    ) -> None:
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        by_object: dict[int, list[CameraModel]] = {}
        for c in cameras:
            by_object.setdefault(int(c.object_id), []).append(c)

        ordered = sorted(
            objects,
            key=lambda o: (
                -sum(1 for c in by_object.get(int(o.id), []) if c.status == "offline"),
                o.name.lower(),
            ),
        )

        for obj in ordered:
            cams = by_object.get(int(obj.id), [])
            card = _SiteCard(obj, cams)
            card.clicked.connect(self.object_selected)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
