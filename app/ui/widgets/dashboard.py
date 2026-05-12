"""Дашборд: карта камер + список «Площадки» с пончиками статусов.

На карте дашборда каждая камера — свой маркер (при необходимости кластеры).
При наведении — всплывающая плашка с объектом, УИН и переходом в таблицу.
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

            font = painter.font()
            font.setBold(True)
            font.setPointSize(9 if self._size <= 56 else 11)
            painter.setFont(font)
            if total == 0:
                painter.setPen(QColor(THEME_FG))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "—")
            elif self._offline == 0 and self._unknown == 0:
                painter.setPen(QColor(STATUS_ONLINE_FG))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "OK")
            elif self._offline > 0:
                painter.setPen(QColor(STATUS_OFFLINE_FG))
                painter.drawText(
                    rect,
                    Qt.AlignmentFlag.AlignCenter,
                    str(self._offline),
                )
            else:
                # Есть только «unknown» — без красного сегмента, считаем проблемными.
                painter.setPen(QColor(PING_BLOCKED_COLOR))
                painter.drawText(
                    rect,
                    Qt.AlignmentFlag.AlignCenter,
                    str(self._unknown),
                )
        finally:
            painter.end()


class _SiteCard(QFrame):
    """Карточка одной площадки: имя + УИН + пончик + счётчик камер.

    Карточка переиспользуется между обновлениями дашборда: вместо
    пересоздания всего набора виджетов мы только заново заполняем
    данные через :meth:`set_data`.
    """

    clicked = Signal(int)

    def __init__(self, obj: ObjectModel, cameras_for_obj: list[CameraModel]) -> None:
        super().__init__()
        self.setObjectName("SiteCard")
        self._object_id = int(obj.id)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)
        self._name_lab = QLabel("")
        self._name_lab.setStyleSheet(
            f"color: {THEME_FG}; font-size: 12px; font-weight: 700;"
        )
        self._name_lab.setWordWrap(True)
        self._uin_lab = QLabel("")
        self._uin_lab.setStyleSheet(f"color: {THEME_FG_MUTED}; font-size: 10px;")
        text_col.addWidget(self._name_lab)
        text_col.addWidget(self._uin_lab)
        text_col.addStretch(1)
        outer.addLayout(text_col, 1)

        donut_col = QVBoxLayout()
        donut_col.setSpacing(1)
        donut_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._donut = _Donut(size=52)
        donut_col.addWidget(self._donut, 0, Qt.AlignmentFlag.AlignHCenter)
        self._cams_lab = QLabel("")
        self._cams_lab.setStyleSheet(f"color: {THEME_FG_MUTED}; font-size: 10px;")
        self._cams_lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        donut_col.addWidget(self._cams_lab)
        outer.addLayout(donut_col)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._accent = ""
        self.set_data(obj, cameras_for_obj)

    @property
    def object_id(self) -> int:
        return self._object_id

    def set_data(self, obj: ObjectModel, cameras_for_obj: list[CameraModel]) -> None:
        """Перепривязать карточку к (возможно, другому) объекту и обновить данные."""
        self._object_id = int(obj.id)

        online = sum(1 for c in cameras_for_obj if c.status == "online")
        offline = sum(1 for c in cameras_for_obj if c.status == "offline")
        unknown = len(cameras_for_obj) - online - offline
        accent = _ratio_color(online, offline, unknown)
        uin = _object_uin(cameras_for_obj)

        if accent != self._accent:
            self._accent = accent
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

        self._name_lab.setText(obj.name or "Объект")
        self._uin_lab.setText(f"УИН: {uin}" if uin else "УИН: —")
        self._donut.set_values(online, offline, unknown)
        self._cams_lab.setText(f"Камер  {len(cameras_for_obj)}")

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
        # cluster=True — на мини-карте дашборда камеры одного района
        # собираются в стопки с количеством (Leaflet.markercluster). На
        # основной карте «Камеры» кластеризация по-прежнему выключена.
        self.map_view = CameraMapView(
            self,
            dark=False,
            cluster=True,
            dashboard_hover=True,
            per_object_marker_numbers=True,
        )
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

        # Пул переиспользуемых карточек (индекс → виджет).
        # Приходящий поток обновлений только перепривязывает данные
        # и при необходимости меняет порядок — это в десятки раз дешевле
        # пересоздания QFrame/QLabel/QPainter на каждый цикл проверки.
        self._cards: list[_SiteCard] = []

        root.addWidget(side, 0)

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        try:
            objects = self.repo.list_objects()
            cameras = self.repo.list_cameras()
        except Exception:
            return

        # Раньше дашборд показывал по одному маркеру на площадку (с цифрой
        # «сколько камер»). Пользователь попросил видеть именно камеры, а не
        # агрегат «9» — поэтому теперь рисуем те же маркеры, что и в разделе
        # «Камеры»: один маркер на камеру, склеиваются только реально
        # перекрывающиеся друг другом точки.
        self.map_view.set_cameras(cameras)
        self._update_cards(objects, cameras)

    @staticmethod
    def _severity_key(obj: ObjectModel, cams: list[CameraModel]):
        total = len(cams)
        offline = sum(1 for c in cams if c.status == "offline")
        unknown = sum(1 for c in cams if c.status == "unknown")
        online = total - offline - unknown
        if total == 0:
            tier = 3  # пустые площадки — в самый низ
            ratio = 0.0
        elif offline == total:
            tier = 0  # всё лежит — наверху
            ratio = 1.0
        elif offline > 0:
            tier = 1  # частично offline
            ratio = offline / total
        elif unknown > 0:
            tier = 2  # есть unknown, нет offline
            ratio = unknown / total
        else:
            tier = 2  # всё ок
            ratio = 0.0
        return (tier, -ratio, -offline, -unknown, -online, (obj.name or "").lower())

    def _update_cards(
        self,
        objects: Iterable[ObjectModel],
        cameras: Iterable[CameraModel],
    ) -> None:
        by_object: dict[int, list[CameraModel]] = {}
        for c in cameras:
            by_object.setdefault(int(c.object_id), []).append(c)

        ordered: list[tuple[ObjectModel, list[CameraModel]]] = []
        for obj in objects:
            cams = by_object.get(int(obj.id), [])
            ordered.append((obj, cams))
        ordered.sort(key=lambda pair: self._severity_key(pair[0], pair[1]))

        # 1. Дотягиваем пул до нужного размера, добавляя в layout перед stretch.
        while len(self._cards) < len(ordered):
            obj, cams = ordered[len(self._cards)]
            card = _SiteCard(obj, cams)
            card.clicked.connect(self.object_selected)
            self._cards.append(card)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

        # 2. Обновляем данные на нужном количестве карточек и показываем их.
        for idx, (obj, cams) in enumerate(ordered):
            card = self._cards[idx]
            card.set_data(obj, cams)
            card.setVisible(True)

        # 3. Лишние карточки прячем (не удаляем — пригодятся в следующий раз).
        for idx in range(len(ordered), len(self._cards)):
            self._cards[idx].setVisible(False)
