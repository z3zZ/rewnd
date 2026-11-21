"""Colour wheel picker: a hue/saturation disc, a brightness slider, a hex field and presets.

The disc is drawn once per size into a small cached image, so it costs almost nothing.
"""

import math

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from rewnd.ui import theme


def _disc_image(size):
    """Hue around the circle, saturation from the centre out, full brightness."""
    r = size / 2
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    dx, dy = x - r + 0.5, y - r + 0.5
    dist = np.sqrt(dx * dx + dy * dy) / r
    hue = (np.degrees(np.arctan2(-dy, dx)) % 360) / 60.0
    sat = np.clip(dist, 0, 1)
    c = sat
    xx = c * (1 - np.abs(hue % 2 - 1))
    zero = np.zeros_like(c)
    i = np.floor(hue).astype(int) % 6
    rgb = np.select(
        [i[..., None] == k for k in range(6)],
        [np.dstack(v) for v in ((c, xx, zero), (xx, c, zero), (zero, c, xx), (zero, xx, c), (xx, zero, c), (c, zero, xx))],
    )
    rgb = rgb + (1 - c)[..., None]
    alpha = np.clip((1.0 - dist) * r + 0.5, 0, 1)   # anti-aliased edge
    rgba = np.dstack([rgb, alpha[..., None]]) * 255
    arr = np.ascontiguousarray(rgba.astype(np.uint8))
    img = QImage(arr.data, size, size, size * 4, QImage.Format_RGBA8888)
    return img.copy()


class ColourWheel(QWidget):
    changed = Signal(QColor)

    def __init__(self, size=176, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CrossCursor)
        self._img = _disc_image(size)
        self.hue, self.sat, self.val = 0.0, 1.0, 1.0

    def set_colour(self, c: QColor):
        h, s, v, _ = c.getHsvF()
        self.hue = max(0.0, h) * 360
        self.sat, self.val = s, v
        self.update()

    def colour(self):
        return QColor.fromHsvF((self.hue % 360) / 360, self.sat, self.val)

    def _pick(self, pos):
        r = self.width() / 2
        dx, dy = pos.x() - r, pos.y() - r
        self.sat = min(1.0, math.hypot(dx, dy) / r)
        self.hue = math.degrees(math.atan2(-dy, dx)) % 360
        self.update()
        self.changed.emit(self.colour())

    def mousePressEvent(self, e):
        self._pick(e.position())

    def mouseMoveEvent(self, e):
        self._pick(e.position())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawImage(0, 0, self._img)
        if self.val < 1:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, int(255 * (1 - self.val))))
            p.drawEllipse(QRectF(0, 0, self.width(), self.height()))
        r = self.width() / 2
        ang = math.radians(self.hue)
        x = r + math.cos(ang) * self.sat * (r - 1)
        y = r - math.sin(ang) * self.sat * (r - 1)
        p.setBrush(self.colour())
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.drawEllipse(QPointF(x, y), 7, 7)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(x, y), 8.5, 8.5)


class ColourPopup(QFrame):
    changed = Signal(str)

    def __init__(self, colour, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("ColourPopup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        self.wheel = ColourWheel()
        v.addWidget(self.wheel, 0, Qt.AlignHCenter)
        self.bright = QSlider(Qt.Horizontal)
        self.bright.setRange(5, 100)
        self.bright.setToolTip("brightness")
        v.addWidget(self.bright)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.hex = QLineEdit()
        self.hex.setValidator(QRegularExpressionValidator(QRegularExpression(r"#?[0-9A-Fa-f]{0,6}")))
        self.hex.setFixedWidth(96)
        row.addWidget(self.hex)
        row.addStretch(1)
        v.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (name, hx) in enumerate(theme.PRESETS.items()):
            b = QPushButton()
            b.setToolTip(name)
            b.setFixedSize(20, 20)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ background: {hx}; border: 1px solid rgba(0,0,0,0.25); border-radius: 10px; padding: 0; }}"
                            f"QPushButton:hover {{ border: 2px solid {theme.TEXT}; }}")
            b.clicked.connect(lambda _=False, h=hx: self._set(QColor(h), emit=True))
            grid.addWidget(b, i // 8, i % 8)
        v.addLayout(grid)
        self.wheel.changed.connect(lambda c: self._set(c, emit=True, from_wheel=True))
        self.bright.valueChanged.connect(self._bright)
        self.hex.editingFinished.connect(self._hex)
        self._set(QColor(colour), emit=False)

    def _set(self, c, emit=True, from_wheel=False):
        if not c.isValid():
            return
        if from_wheel:
            c = QColor.fromHsvF((self.wheel.hue % 360) / 360, self.wheel.sat, self.bright.value() / 100)
        else:
            self.wheel.set_colour(c)
            self.bright.blockSignals(True)
            self.bright.setValue(max(5, int(round(c.valueF() * 100))))
            self.bright.blockSignals(False)
        self.wheel.val = self.bright.value() / 100
        self.wheel.update()
        self.hex.setText(c.name().upper())
        if emit:
            self.changed.emit(c.name())

    def _bright(self, v):
        self.wheel.val = v / 100
        self.wheel.update()
        c = self.wheel.colour()
        self.hex.setText(c.name().upper())
        self.changed.emit(c.name())

    def _hex(self):
        t = self.hex.text().strip()
        if not t.startswith("#"):
            t = "#" + t
        c = QColor(t)
        if len(t) == 7 and c.isValid():
            self._set(c, emit=True)


class ColourButton(QAbstractButton):
    """A round swatch that opens the colour wheel."""
    changed = Signal(str)

    def __init__(self, colour, parent=None):
        super().__init__(parent)
        self.colour = QColor(colour).name()
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(26, 26)
        self.clicked.connect(self.open)
        self._popup = None

    def sizeHint(self):
        return QSize(26, 26)

    def set_colour(self, hx):
        self.colour = QColor(hx).name()
        self.update()

    def open(self):
        self._popup = ColourPopup(self.colour, self)
        self._popup.changed.connect(self._picked)
        self._popup.adjustSize()
        pos = self.mapToGlobal(QPoint(self.width() - self._popup.width(), self.height() + 6))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            pos.setX(max(screen.left() + 8, min(pos.x(), screen.right() - self._popup.width() - 8)))
            if pos.y() + self._popup.height() > screen.bottom():
                pos.setY(self.mapToGlobal(QPoint(0, 0)).y() - self._popup.height() - 6)
        self._popup.move(pos)
        self._popup.show()

    def _picked(self, hx):
        self.colour = hx
        self.update()
        self.changed.emit(hx)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(theme.qcolor(theme.HOVER_EDGE if self.underMouse() else theme.BORDER), 1))
        p.setBrush(theme.qcolor(theme.FIELD))
        p.drawEllipse(QRectF(0.5, 0.5, 25, 25))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.colour))
        p.drawEllipse(QRectF(4, 4, 18, 18))
