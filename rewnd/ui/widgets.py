"""Custom-painted widgets: toggles, segmented controls, the record button, the preview."""

from PySide6.QtCore import (Property, QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect,
                            QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout,
                               QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from rewnd.ui import theme


def hline():
    f = QFrame()
    f.setObjectName("Divider")
    return f


def label(text, name=None):
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    return lab


class Logo(QWidget):
    """The rewnd mark: two rewind chevrons."""

    def __init__(self, size=22, accent="#5CF2B4", parent=None):
        super().__init__(parent)
        self.accent = accent
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cy = h / 2
        s = h * 0.36
        p.setPen(Qt.NoPen)
        for i, alpha in ((0, 255), (1, 150)):
            ox = w * 0.08 + i * s * 1.2
            p.setBrush(theme.qcolor(self.accent, alpha))
            p.drawPolygon(QPolygonF([QPointF(ox, cy), QPointF(ox + s * 1.25, cy - s), QPointF(ox + s * 1.25, cy + s)]))


class Toggle(QAbstractButton):
    def __init__(self, checked=False, accent="#5CF2B4", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(36, 20)
        self.accent = accent
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_pos(self):
        return self._pos

    def _set_pos(self, v):
        self._pos = v
        self.update()

    pos = Property(float, _get_pos, _set_pos)

    def sizeHint(self):
        return QSize(36, 20)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off, on = QColor(theme.SURFACE_3), QColor(self.accent)
        t = self._pos
        track = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            track.setAlpha(90)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)
        p.setBrush(QColor("#0A0C0F") if t > 0.5 else QColor(theme.TEXT))
        x = 3 + t * 16
        p.drawEllipse(QRectF(x, 3, 14, 14))


class Segmented(QFrame):
    changed = Signal(object)

    def __init__(self, options, value=None, parent=None):
        super().__init__(parent)
        self.setObjectName("SegBox")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.values = []
        for text, val in options:
            b = QPushButton(text)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.group.addButton(b, len(self.values))
            self.values.append(val)
            lay.addWidget(b)
        self.set_value(value if value is not None else self.values[0])
        self.group.idClicked.connect(lambda i: self.changed.emit(self.values[i]))

    def value(self):
        i = self.group.checkedId()
        return self.values[i] if i >= 0 else None

    def set_value(self, v):
        if v in self.values:
            self.group.button(self.values.index(v)).setChecked(True)


class Chips(QWidget):
    """Quick-pick chips; checked when the value matches one of them."""
    picked = Signal(object)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.buttons = []
        for text, val in options:
            b = QPushButton(text)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=val: self.picked.emit(v))
            self.buttons.append((b, val))
            lay.addWidget(b)
        lay.addStretch(1)

    def sync(self, value):
        for b, v in self.buttons:
            b.setChecked(abs(float(v) - float(value)) < 1e-6 if value is not None else False)


class Section(QFrame):
    """Collapsible settings card."""

    def __init__(self, title, subtitle="", expanded=True, parent=None):
        super().__init__(parent)
        self.setObjectName("Section")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = QPushButton()
        self.header.setObjectName("SectionHeader")
        self.header.setCursor(Qt.PointingHandCursor)
        self.title = title
        self.subtitle = subtitle
        self.header.clicked.connect(self.toggle)
        outer.addWidget(self.header)
        self.body = QWidget()
        self.form = QVBoxLayout(self.body)
        self.form.setContentsMargins(14, 0, 14, 14)
        self.form.setSpacing(10)
        outer.addWidget(self.body)
        self.set_expanded(expanded)

    def set_expanded(self, on):
        self.body.setVisible(on)
        self.header.setText(("▾  " if on else "▸  ") + self.title)

    def toggle(self):
        self.set_expanded(not self.body.isVisible())

    def row(self, text, widget, hint=None):
        r = QHBoxLayout()
        r.setSpacing(10)
        lab = label(text, "FieldLabel")
        if hint:
            lab.setToolTip(hint)
            widget.setToolTip(hint)
        r.addWidget(lab)
        r.addStretch(1)
        r.addWidget(widget)
        self.form.addLayout(r)
        return lab

    def stacked(self, text, widget, hint=None):
        lab = label(text, "FieldLabel")
        if hint:
            lab.setToolTip(hint)
            widget.setToolTip(hint)
        self.form.addWidget(lab)
        self.form.addWidget(widget)
        return lab

    def add(self, w):
        if isinstance(w, QLayout):
            self.form.addLayout(w)
        else:
            self.form.addWidget(w)


class SliderRow(QWidget):
    changed = Signal(int)

    def __init__(self, text, lo, hi, value, fmt="{:+d}", reset=0, parent=None):
        super().__init__(parent)
        self.fmt = fmt
        self.reset_value = reset
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(label(text, "FieldLabel"))
        top.addStretch(1)
        self.val = label("", "Mono")
        top.addWidget(self.val)
        v.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(int(value))
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.setToolTip("Double-click to reset")
        v.addWidget(self.slider)
        self.slider.valueChanged.connect(self._on)
        self._on(self.slider.value(), emit=False)

    def mouseDoubleClickEvent(self, e):
        self.slider.setValue(self.reset_value)

    def _on(self, v, emit=True):
        self.val.setText(self.fmt.format(v))
        if emit:
            self.changed.emit(v)

    def set_value(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(v))
        self.slider.blockSignals(False)
        self._on(int(v), emit=False)


class RecordButton(QAbstractButton):
    """Big round record button. While recording it shows a ring counting down to the next shot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(76, 76)
        self.recording = False
        self.progress = 0.0
        self.accent = "#5CF2B4"
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def set_state(self, recording, progress=0.0):
        self.recording, self.progress = recording, progress
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(4, 4, 68, 68)
        p.setPen(QPen(QColor(theme.SURFACE_3), 4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r)
        if self.recording:
            pen = QPen(QColor(self.accent), 4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(r, 90 * 16, int(-360 * 16 * max(0.0, min(1.0, self.progress))))
        p.setPen(Qt.NoPen)
        red = QColor(theme.REC)
        if self._hover:
            red = red.lighter(112)
        p.setBrush(red)
        if self.recording:
            p.drawRoundedRect(QRectF(27, 27, 22, 22), 5, 5)
        else:
            inset = 13 if self._hover else 14
            p.drawEllipse(QRectF(inset, inset, 76 - 2 * inset, 76 - 2 * inset))


class PreviewView(QWidget):
    """Shows the rendered preview frame, letterboxed with rounded corners."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 220)
        self.image = None
        self.message = "Looking for a camera…"
        self.sub = ""
        self.rec = False

    def set_image(self, qimg):
        self.image = qimg
        self.update()

    def set_message(self, msg, sub=""):
        self.message, self.sub = msg, sub
        self.update()

    def image_rect(self):
        if self.image is None or self.image.isNull():
            return QRectF(self.rect())
        iw, ih = self.image.width(), self.image.height()
        k = min(self.width() / iw, self.height() / ih)
        w, h = iw * k, ih * k
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.image is None or self.image.isNull():
            r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            p.setPen(QPen(QColor(theme.BORDER), 1, Qt.DashLine))
            p.setBrush(QColor(theme.SURFACE))
            p.drawRoundedRect(r, 16, 16)
            p.setPen(QColor(theme.MUTED))
            f = QFont("Inter", 14)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.drawText(r.adjusted(0, -12, 0, -12), Qt.AlignCenter, self.message)
            if self.sub:
                p.setPen(QColor(theme.FAINT))
                p.setFont(QFont("Inter", 11))
                p.drawText(r.adjusted(40, 30, -40, 30), Qt.AlignCenter | Qt.TextWordWrap, self.sub)
            return
        r = self.image_rect()
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.setClipPath(path)
        p.drawImage(r, self.image)
        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        if self.rec:
            p.setPen(QPen(theme.qcolor(theme.REC, 200), 2))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        if self.message and self.sub == "overlay":
            p.fillRect(r, QColor(0, 0, 0, 120))
            p.setPen(QColor(theme.TEXT))
            f = QFont("Inter", 14)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, self.message)


class StatusPill(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusPill")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(7)
        self.dot = QLabel("●")
        self.text = QLabel("")
        self.text.setStyleSheet("font-weight: 700; font-size: 11px; letter-spacing: 1px;")
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self._blink = QTimer(self)
        self._blink.timeout.connect(self._tick)
        self._on = True
        self._colour = theme.MUTED

    def set(self, text, colour, blink=False):
        self.text.setText(text)
        self._colour = colour
        self.dot.setStyleSheet(f"color: {colour}; font-size: 10px;")
        if blink and not self._blink.isActive():
            self._blink.start(600)
        elif not blink:
            self._blink.stop()
            self._on = True
            self.dot.setStyleSheet(f"color: {colour}; font-size: 10px;")

    def _tick(self):
        self._on = not self._on
        c = self._colour if self._on else "transparent"
        self.dot.setStyleSheet(f"color: {c}; font-size: 10px;")


class Toast(QFrame):
    """Bottom-right notification card with an optional progress bar and action buttons."""

    def __init__(self, parent):
        super().__init__(parent)
        from PySide6.QtWidgets import QProgressBar
        self.setObjectName("Card")
        self.setFixedWidth(340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        self.title = label("", "H2")
        self.body = label("", "Muted")
        self.body.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(6)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        lay.addWidget(self.title)
        lay.addWidget(self.body)
        lay.addWidget(self.bar)
        lay.addLayout(self.actions)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, title, body="", progress=None, actions=(), timeout=0):
        self.show()
        self.title.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))
        self.bar.setVisible(progress is not None)
        if progress is not None:
            self.bar.setValue(int(progress * 1000))
        while self.actions.count():
            item = self.actions.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, (text, fn) in enumerate(actions):
            b = QPushButton(text)
            if i == 0:
                b.setObjectName("Primary")
            b.clicked.connect(fn)
            self.actions.addWidget(b)
            b.show()
        if actions:
            close = QPushButton("Dismiss")
            close.setObjectName("Ghost")
            close.clicked.connect(self.hide)
            self.actions.addStretch(1)
            self.actions.addWidget(close)
            close.show()
        lay = self.layout()
        lay.activate()
        h = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.setFixedHeight(max(h, lay.totalMinimumSize().height()))
        self.reposition()
        self.show()
        self.raise_()
        if timeout:
            self._timer.start(timeout)
        else:
            self._timer.stop()

    def reposition(self):
        par = self.parentWidget()
        if par:
            self.move(par.width() - self.width() - 20, par.height() - self.height() - 20)


class FlowLayout(QLayout):
    """Wraps child widgets into rows; used for the library grid."""

    def __init__(self, parent=None, spacing=16):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(QRect(0, 0, w, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _layout(self, rect, dry=False):
        sp = self.spacing()
        if not self.items:
            return 0
        item_w = self.items[0].sizeHint().width()
        cols = max(1, (rect.width() + sp) // (item_w + sp))
        w = (rect.width() - sp * (cols - 1)) // cols
        x, y, row_h, col = rect.x(), rect.y(), 0, 0
        for it in self.items:
            h = it.widget().heightForWidth(w) if it.widget() and it.widget().hasHeightForWidth() else it.sizeHint().height()
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            row_h = max(row_h, h)
            col += 1
            if col >= cols:
                col = 0
                x = rect.x()
                y += row_h + sp
                row_h = 0
            else:
                x += w + sp
        return y + row_h - rect.y()


def cv_to_qimage(bgr):
    import cv2
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
