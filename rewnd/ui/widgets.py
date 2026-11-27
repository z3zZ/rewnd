"""Custom-painted widgets: toggles, segmented controls, the record button, the preview."""

from PySide6.QtCore import (Property, QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect,
                            QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout,
                               QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from rewnd.ui import theme


class GlassBackdrop(QWidget):
    """Window background. Flat for black and light; for aero, a sky gradient with soft bubbles
    and a green glow, which is what the translucent glass panels sit on."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._cache = None

    def set_ambient(self, _):
        pass

    def retheme(self):
        self._cache = None
        self.update()

    def _aero(self, w, h):
        from PySide6.QtGui import QLinearGradient, QPixmap, QRadialGradient
        pm = QPixmap(w, h)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        sky = QLinearGradient(0, 0, 0, h)
        sky.setColorAt(0.0, QColor("#7FC8F8"))
        sky.setColorAt(0.45, QColor("#C9E9FF"))
        sky.setColorAt(0.78, QColor("#EEF9FF"))
        sky.setColorAt(1.0, QColor("#D6F3DA"))
        p.fillRect(0, 0, w, h, sky)
        acc = theme.qcolor(theme.ACCENT)
        for cx, cy, rad, col, al in ((0.12, 1.05, 0.55, acc, 120), (0.95, -0.1, 0.45, QColor("#FFFFFF"), 200),
                                    (0.75, 0.95, 0.35, QColor("#7FE0C8"), 110)):
            g = QRadialGradient(QPointF(w * cx, h * cy), max(w, h) * rad)
            c0 = QColor(col)
            c0.setAlpha(al)
            c1 = QColor(col)
            c1.setAlpha(0)
            g.setColorAt(0, c0)
            g.setColorAt(1, c1)
            p.fillRect(0, 0, w, h, g)
        # bubbles: soft rim, bright highlight top-left
        for fx, fy, fr in ((0.07, 0.30, 0.050), (0.18, 0.80, 0.028), (0.42, 0.12, 0.020), (0.63, 0.88, 0.040),
                           (0.86, 0.42, 0.026), (0.93, 0.78, 0.060), (0.30, 0.55, 0.016), (0.55, 0.40, 0.012)):
            r = min(w, h) * fr * 2.2
            c = QPointF(w * fx, h * fy)
            body = QRadialGradient(c, r)
            body.setColorAt(0.0, QColor(255, 255, 255, 18))
            body.setColorAt(0.82, QColor(255, 255, 255, 40))
            body.setColorAt(0.97, QColor(255, 255, 255, 150))
            body.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(body)
            p.drawEllipse(c, r, r)
            hl = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.4), r * 0.45)
            hl.setColorAt(0.0, QColor(255, 255, 255, 210))
            hl.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(hl)
            p.drawEllipse(QPointF(c.x() - r * 0.35, c.y() - r * 0.4), r * 0.45, r * 0.32)
        p.end()
        return pm

    def paintEvent(self, e):
        p = QPainter(self)
        if theme.NAME != "aero":
            p.fillRect(e.rect(), theme.qcolor(theme.BASE))
            return
        size = (self.width(), self.height())
        if self._cache is None or self._cache[0] != size:
            self._cache = (size, self._aero(*size))
        p.drawPixmap(e.rect(), self._cache[1], e.rect())


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
    """The rewnd mark: two round-stroke rewind chevrons, drawn like the errnd icon."""

    def __init__(self, size=22, accent=theme.ACCENT, parent=None):
        super().__init__(parent)
        self.accent = accent
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pen = QPen(theme.qcolor(theme.ACCENT if theme.DARK else theme.ACCENT_TEXT), max(2.0, h * 0.13))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        cy, s = h / 2, h * 0.28
        for i in range(2):
            x = w * 0.22 + i * w * 0.32
            p.drawPolyline(QPolygonF([QPointF(x + s, cy - s), QPointF(x, cy), QPointF(x + s, cy + s)]))


class Wordmark(QLabel):
    def __init__(self, size=15, parent=None):
        super().__init__(theme.wordmark_html(size), parent)
        self.size_px = size
        self.setTextFormat(Qt.RichText)

    def retheme(self):
        self.setText(theme.wordmark_html(self.size_px))


class Toggle(QAbstractButton):
    def __init__(self, checked=False, accent=theme.ACCENT, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(32, 22)
        self.accent = accent
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_knob(self):
        return self._pos

    def _set_knob(self, v):
        self._pos = v
        self.update()

    # Not "pos": that name is QWidget's position, and animating it moved the toggle around.
    knob = Property(float, _get_knob, _set_knob)

    def sizeHint(self):
        return QSize(32, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.35)
        t = self._pos
        border = theme.qcolor(theme.ACCENT) if t > 0.5 else theme.qcolor(theme.TOGGLE_OFF)
        if not self.isEnabled():
            border.setAlpha(80)
        p.setPen(QPen(border, 1))
        filled = not theme.DARK and t > 0.5   # light/aero: iOS-style filled track, white knob
        p.setBrush(theme.qcolor(theme.ACCENT) if filled else theme.qcolor(theme.FIELD))
        p.drawRoundedRect(QRectF(0.5, 2.5, 31, 17), 8.5, 8.5)
        p.setPen(Qt.NoPen)
        if filled:
            knob = QColor("#FFFFFF")
        else:
            knob = theme.qcolor(theme.ACCENT) if t > 0.5 else theme.qcolor(theme.TEXT_2)
        p.setBrush(knob)
        p.drawEllipse(QRectF(4 + t * 14, 6, 10, 10))


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
        self.form.setContentsMargins(2, 0, 2, 16)
        self.form.setSpacing(10)
        outer.addWidget(self.body)
        self.set_expanded(expanded)

    def set_expanded(self, on):
        self.body.setVisible(on)
        self.header.setText(("▾  " if on else "▸  ") + self.title.upper())

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
        self.accent = theme.ACCENT
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
        r = QRectF(3, 3, 70, 70)
        p.setPen(QPen(theme.qcolor(theme.RING), 2))
        p.setBrush(theme.qcolor(theme.BASE))
        p.drawEllipse(r)
        lime = theme.qcolor(theme.ACCENT)
        if self.recording:
            pen = QPen(lime, 2.5)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(r, 90 * 16, int(-360 * 16 * max(0.0, min(1.0, self.progress))))
        p.setPen(Qt.NoPen)
        if self._hover:
            lime = lime.lighter(110)
        p.setBrush(lime)
        if self.recording:
            p.drawRoundedRect(QRectF(29, 29, 18, 18), 4, 4)
        else:
            inset = 15 if self._hover else 16
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
            p.setPen(QPen(theme.qcolor(theme.BORDER), 1))
            p.setBrush(theme.qcolor(theme.SURFACE))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(theme.qcolor(theme.TEXT_2))
            f = QFont(theme.MONO, 12)
            p.setFont(f)
            p.drawText(r.adjusted(0, -12, 0, -12), Qt.AlignCenter, self.message)
            if self.sub:
                p.setPen(theme.qcolor(theme.TEXT_3))
                p.setFont(QFont(theme.UI_FONT, 10))
                p.drawText(r.adjusted(40, 30, -40, 30), Qt.AlignCenter | Qt.TextWordWrap, self.sub)
            return
        r = self.image_rect()
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.setClipPath(path)
        p.drawImage(r, self.image)
        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        if self.rec:
            p.setPen(QPen(theme.qcolor(theme.ACCENT, 210), 1.5))
        else:
            p.setPen(QPen(theme.qcolor(theme.BORDER), 1))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        if self.message and self.sub == "overlay":
            p.fillRect(r, QColor(0, 0, 0, 140))
            p.setPen(theme.qcolor(theme.TEXT))
            p.setFont(QFont(theme.MONO, 12))
            p.drawText(r, Qt.AlignCenter, self.message)


class StatusPill(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusPill")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 3, 11, 3)
        lay.setSpacing(6)
        self.dot = QLabel("●")
        self.text = QLabel("")
        self.text.setStyleSheet(f"font-family: '{theme.MONO}'; font-size: 11px; color: {theme.TEXT_2};")
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self._blink = QTimer(self)
        self._blink.timeout.connect(self._tick)
        self._on = True
        self._colour = theme.TEXT_2

    def set(self, text, colour, blink=False):
        self.text.setStyleSheet(f"font-family: '{theme.MONO}'; font-size: 11px; color: {theme.TEXT_2};")
        self.text.setText(text.lower())
        self._colour = colour
        self.dot.setStyleSheet(f"color: {colour}; font-size: 9px;")
        if blink and not self._blink.isActive():
            self._blink.start(600)
        elif not blink:
            self._blink.stop()
            self._on = True
            self.dot.setStyleSheet(f"color: {colour}; font-size: 9px;")

    def _tick(self):
        self._on = not self._on
        c = self._colour if self._on else "transparent"
        self.dot.setStyleSheet(f"color: {c}; font-size: 9px;")


class Toast(QFrame):
    """Bottom-right notification card with an optional progress bar and action buttons."""

    def __init__(self, parent):
        super().__init__(parent)
        from PySide6.QtWidgets import QProgressBar
        self.setObjectName("Toast")
        self.setFixedWidth(340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        self.title = label("", "H2")
        self.body = label("", "Muted")
        self.body.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(4)
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
            close = QPushButton("dismiss")
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
