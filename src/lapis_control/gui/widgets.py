"""Shared visual components for the LAPIS desktop workspace."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import IntFlag

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)


STYLE = """
QWidget { color:#192945; font-size:13px; }
QMainWindow, QScrollArea, QWidget#canvas { background:#f3f6fa; }
QScrollArea { border:0; }
QFrame#rail { background:#14243b; border:0; }
QLabel#brand { color:white; font-size:25px; font-weight:700; letter-spacing:1px; }
QLabel#railText { color:#becbde; }
QLabel#railValue { color:#f3f7ff; font-size:19px; font-weight:600; }
QLabel#section { color:#9aafc8; font-size:11px; font-weight:600; }
QPushButton#nav { text-align:left; padding:9px 12px; border:0; background:transparent; color:#d1dceb; }
QPushButton#nav:hover { background:#233851; }
QPushButton#nav:checked { background:#2d486a; color:white; font-weight:600; }
QFrame#card { background:white; border:1px solid #dce3ed; border-radius:12px; }
QFrame#pressure { background:#172c43; border:0; border-radius:12px; }
QLabel#light { color:#c4d4e5; }
QLabel#pressureValue { color:#f2fbff; font-size:32px; font-weight:600; }
QLabel#pageTitle { font-size:29px; font-weight:700; color:#14243b; }
QLabel#title { font-size:16px; font-weight:600; }
QLabel#value { font-size:21px; font-weight:600; }
QLabel#muted { color:#566980; }
QLabel#message { padding:7px 12px; border-top:1px solid #dce3ed; background:white; color:#43556b; }
QLabel#problem { padding:7px 12px; background:#ffebdf; color:#802c11; }
QPushButton { padding:7px 12px; background:white; border:1px solid #c8d3e0; border-radius:6px; font-weight:500; }
QPushButton:hover { background:#edf2fa; border-color:#9cacc3; }
QPushButton:pressed { background:#dbe6f6; }
QPushButton#primary { background:#325ee3; color:white; border-color:#325ee3; font-weight:600; }
QPushButton#primary:hover { background:#264ec4; }
QPushButton#stop { color:#9c382a; border-color:#dab7af; }
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#stop:disabled {
    color:#8896a9; background:#eef2f6; border-color:#dce3ed; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
    padding:5px 6px; background:#fbfcfe; border:1px solid #c7d2df; border-radius:5px; min-height:18px; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus { border-color:#446be0; }
QTreeWidget, QListWidget { border:0; background:white; alternate-background-color:#f6f8fb; }
QTreeWidget::item, QListWidget::item { padding:3px; }
QHeaderView::section { background:#f4f7fb; color:#4f647c; padding:5px; border:0; }
QToolTip { background:#192b42; color:white; border:0; padding:4px; }
"""


def set_style(app: QApplication) -> None:
    app.setStyle('Fusion')
    families = QFontDatabase.families()
    font = next((f for f in ('Inter', 'Segoe UI') if f in families), app.font().family())
    app.setFont(QFont(font, 10))
    app.setStyleSheet(STYLE)


def icon(name: str, color: str = '#91aed1', size: int = 24) -> QIcon:
    """Draw instrument symbols at native display resolution."""
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    p.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    if name == 'brand':
        path = QPainterPath(QPointF(12, 1))
        for point in [(23, 7), (23, 17), (12, 23), (1, 17), (1, 7)]:
            path.lineTo(*point)
        path.closeSubpath()
        p.fillPath(path, QColor('#6ce1ce'))
        p.setPen(QPen(QColor('#17324b'), 1.8))
        p.drawPolyline([QPointF(5, 14), QPointF(10, 8), QPointF(14, 15), QPointF(19, 9)])
    elif name == 'overview':
        for x, y in [(3, 3), (13, 3), (3, 13), (13, 13)]:
            p.drawRoundedRect(QRectF(x, y, 7, 7), 1.5, 1.5)
    elif name == 'vacuum':
        p.drawEllipse(QRectF(3, 3, 18, 18))
        p.drawLine(12, 13, 16, 8)
        p.drawLine(8, 17, 16, 17)
    elif name == 'bender':
        for x, y in [(5, 8), (12, 14), (19, 6)]:
            p.drawLine(x, 3, x, 21)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x-2, y, 4, 4), 1, 1)
    elif name == 'laser':
        p.drawEllipse(QRectF(8, 8, 8, 8))
        for x, y, u, v in [(12,2,12,6),(12,18,12,22),(2,12,6,12),(18,12,22,12),
                            (4,4,7,7),(17,17,20,20),(4,20,7,17),(17,7,20,4)]:
            p.drawLine(x,y,u,v)
    elif name == 'oscilloscope':
        p.drawRoundedRect(QRectF(2, 4, 20, 16), 3, 3)
        p.drawPolyline([QPointF(4,13), QPointF(8,13), QPointF(10,8), QPointF(13,16), QPointF(16,11), QPointF(20,11)])
    else:
        for y, shift in [(5, 0), (10, 3), (15, 6)]:
            p.drawLine(4, y, 20-shift, y)
        p.drawLine(4, 20, 20, 20)
    p.end()
    return QIcon(pix)


def label(text: str, style: str = '', *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(style)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, style: str = '') -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(style)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


class _NumberInput(QDoubleSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class _ChoiceInput(QComboBox):
    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


def number(value: float, low: float, high: float, digits: int = 2, step: float = 1.0) -> QDoubleSpinBox:
    widget = _NumberInput()
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.setDecimals(digits)
    widget.setRange(low, high)
    widget.setValue(value)
    widget.setSingleStep(step)
    widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
    widget.setKeyboardTracking(False)
    return widget


def combo(values: list[str]) -> QComboBox:
    widget = _ChoiceInput()
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.addItems(values)
    return widget


def row(*widgets: QWidget, stretch: bool = True) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(8)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch()
    return layout


class Card(QFrame):
    def __init__(self, title: str = '') -> None:
        super().__init__()
        self.setObjectName('card')
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(18, 14, 18, 16)
        self.box.setSpacing(10)
        if title:
            self.box.addWidget(label(title, 'title'))


def fields(items: list[tuple[str, QWidget]], columns: int = 3) -> QGridLayout:
    layout = QGridLayout()
    layout.setHorizontalSpacing(14)
    for i, (title, widget) in enumerate(items):
        y, x = divmod(i, columns)
        caption = label(title, 'muted')
        caption.setBuddy(widget)
        widget.setAccessibleName(title)
        layout.addWidget(caption, 2*y, x)
        layout.addWidget(widget, 2*y+1, x)
    return layout


class Details(QTreeWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setColumnCount(2)
        self.setHeaderLabels(['Parameter', 'Gerätewert'])
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(175)
        self.setColumnWidth(0, 240)
        self.header().setStretchLastSection(True)

    def display(self, data: dict) -> None:
        expanded = {self.topLevelItem(i).text(0) for i in range(self.topLevelItemCount())
                    if self.topLevelItem(i).isExpanded()}
        self.clear()

        def add(parent: QTreeWidget | QTreeWidgetItem, key: object, value: object) -> None:
            if is_dataclass(value):
                value = asdict(value)
            item = QTreeWidgetItem(parent, [str(key), ''])
            if isinstance(value, dict):
                for name, field in value.items():
                    add(item, name, field)
                item.setExpanded(str(key) in expanded)
            else:
                if isinstance(value, IntFlag):
                    value = f'0x{value:04X}  {value.name or ""}'
                elif isinstance(value, bool):
                    value = 'Ja' if value else 'Nein'
                item.setText(1, '—' if value is None else str(value))
                item.setToolTip(1, item.text(1))

        for key, value in data.items():
            add(self, key, value)
