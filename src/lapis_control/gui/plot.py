"""Qt trace display with min/max decimation and time-range selection."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class TracePlot(QWidget):
    range_changed = Signal(object)

    def __init__(self, *, pressure: bool = False) -> None:
        super().__init__()
        self.pressure = pressure
        self.x = np.array([], dtype=float)
        self.y = self.x
        self.selection = slice(None)
        self.drag: float | None = None
        self.pointer = 0.0
        self.bounds = (0., 1.)
        self.setMinimumHeight(137 if pressure else 245)
        self.setMouseTracking(True)
        self.setAccessibleName('Druckverlauf' if pressure else 'Messkurve')
        if not pressure:
            self.setToolTip('Zeitbereich mit der Maus aufziehen. Doppelklick zeigt die gesamte Kurve.')

    def set_data(self, x: np.ndarray, y: np.ndarray) -> None:
        self.x, self.y = x, y
        self.reset()

    def reset(self) -> None:
        self.selection = slice(None)
        self.drag = None
        self.range_changed.emit(self.selection)
        self.update()

    def clear(self) -> None:
        self.set_data(np.array([]), np.array([]))

    def area(self) -> QRectF:
        return QRectF(69, 19, max(1, self.width()-86), max(1, self.height()-54))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.area()
        p.fillRect(self.rect(), QColor('#172c43'))
        x, y = self.x[self.selection], self.y[self.selection]
        valid = np.isfinite(y)
        data = bool(valid.any())
        ymin, ymax = (float(y[valid].min()), float(y[valid].max())) if data else (0., 1.)
        pad = (ymax - ymin) * .08 or max(abs(ymax) * .02, 1e-16)
        ymin, ymax = ymin - pad, ymax + pad
        xmin, xmax = (float(x[0]), float(x[-1])) if x.size else (0., 1.)
        if xmax <= xmin:
            xmin, xmax = (xmin-1, xmax) if self.pressure else (xmin-.5, xmax+.5)
        self.bounds = xmin, xmax
        scale, unit = 1., 's'
        if not self.pressure:
            for limit, factor, symbol in [(1e-6, 1e9, 'ns'), (1e-3, 1e6, 'µs'), (1., 1e3, 'ms')]:
                if xmax - xmin < limit:
                    scale, unit = factor, symbol
                    break
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        for i in range(5):
            frac = i / 4
            px = area.left() + frac * area.width()
            py = area.bottom() - frac * area.height()
            p.setPen(QPen(QColor('#31465a'), 1))
            p.drawLine(QPointF(px, area.top()), QPointF(px, area.bottom()))
            p.drawLine(QPointF(area.left(), py), QPointF(area.right(), py))
            if data:
                p.setPen(QColor('#b9cbd9'))
                val = ymin + frac * (ymax-ymin)
                p.drawText(QRectF(0, py-9, 63, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           f'{val:.4g}')
                val = (xmin + frac * (xmax-xmin)) * scale
                p.drawText(QRectF(px-36, area.bottom()+4, 72, 18), Qt.AlignmentFlag.AlignCenter, f'{val:.3g}')
        p.setPen(QColor('#c5d6e1'))
        p.drawText(5, 13, 'mbar' if self.pressure else 'V')
        p.drawText(int(area.right()-12), self.height()-3, unit)
        if data:
            # Each display bin contributes its two extremes in temporal order.
            # A narrow pulse is retained even on a multi-million-sample trace.
            width = max(1, int(area.width()))
            stride = max(1, (y.size + width - 1) // width)
            if stride > 1:
                padded = np.full(((y.size + stride-1)//stride)*stride, np.nan)
                padded[:y.size] = y
                bins = padded.reshape(-1, stride)
                start = np.arange(len(bins))*stride
                low = start + np.where(np.isfinite(bins), bins, np.inf).argmin(axis=1)
                high = start + np.where(np.isfinite(bins), bins, -np.inf).argmax(axis=1)
                indices = np.sort(np.column_stack([low, high]), axis=1).ravel()
                # Preserve visible gaps even when a missing sample shares a bin with valid data.
                gaps = np.flatnonzero(~valid)
                if gaps.size:
                    indices = np.union1d(indices, gaps[np.r_[True, np.diff(gaps)//stride > 0]])
            else:
                indices = range(y.size)
            path = QPainterPath()
            pen_down = False
            p.setPen(QPen(QColor('#5de3d0'), 1.7))
            for i in indices:
                if not valid[i]:
                    pen_down = False
                    continue
                px = area.left() + (x[i]-xmin)/(xmax-xmin)*area.width()
                py = area.bottom() - (y[i]-ymin)/(ymax-ymin)*area.height()
                if pen_down:
                    path.lineTo(px, py)
                else:
                    path.moveTo(px, py)
                    p.drawPoint(QPointF(px, py))
                pen_down = True
            p.drawPath(path)
        else:
            message = 'Warte auf Druckwerte' if self.pressure else 'Keine Messkurve geladen'
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, message if not x.size else 'Keine gültigen Messwerte')
        if self.drag is not None:
            left, right = sorted((self.drag, self.pointer))
            p.fillRect(QRectF(left, area.top(), right-left, area.height()), QColor(93, 227, 208, 45))
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (not self.pressure and self.x.size and event.button() == Qt.MouseButton.LeftButton
                and self.area().contains(event.position())):
            self.drag = self.pointer = event.position().x()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag is not None:
            self.pointer = max(self.area().left(), min(self.area().right(), event.position().x()))
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.drag is None:
            return
        left, right = sorted((self.drag, self.pointer))
        self.drag = None
        if right - left > 5:
            area = self.area()
            xmin, xmax = self.bounds
            span = (xmax-xmin) / area.width()
            lo = self.x.searchsorted(xmin+(left-area.left())*span)
            hi = self.x.searchsorted(xmin+(right-area.left())*span, side='right')
            if hi - lo >= 2:
                self.selection = slice(lo, hi)
                self.range_changed.emit(self.selection)
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.reset()
