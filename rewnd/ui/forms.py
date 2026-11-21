"""Settings sections bound directly to the config dict. Each control writes its key and calls on_change."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QHBoxLayout, QLineEdit, QPushButton, QWidget)

from rewnd.config import POSITIONS
from rewnd.ui import theme
from rewnd.ui.widgets import Chips, Section, Segmented, SliderRow, Toggle, label

POSITION_LABELS = {
    "top-left": "Top left", "top-center": "Top centre", "top-right": "Top right",
    "bottom-left": "Bottom left", "bottom-center": "Bottom centre", "bottom-right": "Bottom right",
}


def toggle(sec, text, d, key, on_change, accent, hint=None):
    t = Toggle(bool(d.get(key)), accent)

    def f(v):
        d[key] = bool(v)
        on_change()
    t.toggled.connect(f)
    sec.row(text, t, hint)
    return t


def position_combo(d, key, on_change):
    c = QComboBox()
    c.setFixedWidth(130)
    for p in POSITIONS:
        c.addItem(POSITION_LABELS[p], p)
    c.setCurrentIndex(max(0, POSITIONS.index(d.get(key, "top-left")) if d.get(key) in POSITIONS else 0))

    def f(_):
        d[key] = c.currentData()
        on_change()
    c.currentIndexChanged.connect(f)
    return c


def look_section(look, on_change, accent, expanded=True):
    sec = Section("Look", expanded=expanded)
    toggle(sec, "Mirror", look, "mirror", on_change, accent,
           "Flip left to right. Turn on if your webcam shows you mirrored.")
    toggle(sec, "Upside down", look, "flip", on_change, accent, "Flip top to bottom, for cameras mounted upside down.")
    rot = Segmented([("0°", 0), ("90°", 90), ("180°", 180), ("270°", 270)], look.get("rotate", 0))
    rot.setFixedWidth(200)

    def r(v):
        look["rotate"] = v
        on_change()
    rot.changed.connect(r)
    sec.row("Rotate", rot)

    sliders = []
    for key, text in (("brightness", "Brightness"), ("contrast", "Contrast"),
                      ("saturation", "Saturation"), ("warmth", "Warmth")):
        s = SliderRow(text, -100, 100, look.get(key, 0))

        def f(v, key=key):
            look[key] = v
            on_change()
        s.changed.connect(f)
        sec.add(s)
        sliders.append((key, s))

    reset = QPushButton("Reset colour")
    reset.setObjectName("Ghost")

    def do_reset():
        for key, s in sliders:
            look[key] = 0
            s.set_value(0)
        on_change()
    reset.clicked.connect(do_reset)
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(reset)
    sec.add(row)
    return sec


def framing_section(exp, on_change, accent, expanded=True):
    sec = Section("Framing && length", expanded=expanded)

    aspect = Segmented([("Original", "original"), ("16:9", "16:9"), ("9:16", "9:16"),
                        ("1:1", "1:1"), ("4:5", "4:5")], exp.get("aspect"))
    sec.stacked("Aspect ratio", aspect, "9:16 is TikTok, Reels and Shorts. 4:5 suits Instagram posts.")

    fit = Segmented([("Crop to fill", "crop"), ("Fit + blur", "blur")], exp.get("fit"))
    sec.stacked("When the shape changes", fit,
                "Crop fills the frame. Fit + blur shows the whole shot over a blurred copy of itself.")

    offset = SliderRow("Crop position", -100, 100, int(float(exp.get("crop_offset", 0)) * 100), fmt="{:+d}")

    def off(v):
        exp["crop_offset"] = v / 100.0
        on_change()
    offset.changed.connect(off)
    sec.add(offset)

    def sync_offset():
        offset.setVisible(exp.get("aspect") != "original" and exp.get("fit") == "crop")

    def a(v):
        exp["aspect"] = v
        sync_offset()
        on_change()

    def fi(v):
        exp["fit"] = v
        sync_offset()
        on_change()
    aspect.changed.connect(a)
    fit.changed.connect(fi)
    sync_offset()

    mode = Segmented([("Fit to length", "target"), ("Every frame", "fps")], exp.get("length_mode"))
    sec.stacked("Video length", mode,
                "Fit to length speeds up or slows down to hit a duration. Every frame uses each shot once.")

    target_row = QWidget()
    tr = QHBoxLayout(target_row)
    tr.setContentsMargins(0, 0, 0, 0)
    tr.setSpacing(8)
    chips = Chips([("15s", 15), ("30s", 30), ("60s", 60), ("90s", 90)])
    spin = QDoubleSpinBox()
    spin.setRange(1, 3600)
    spin.setDecimals(0)
    spin.setSuffix(" s")
    spin.setFixedWidth(78)
    spin.setValue(float(exp.get("target_seconds", 30)))
    tr.addWidget(chips, 1)
    tr.addWidget(spin)
    sec.add(target_row)

    def set_target(v):
        exp["target_seconds"] = float(v)
        chips.sync(v)
        if spin.value() != float(v):
            spin.blockSignals(True)
            spin.setValue(float(v))
            spin.blockSignals(False)
        on_change()
    chips.picked.connect(set_target)
    spin.valueChanged.connect(set_target)
    chips.sync(exp.get("target_seconds", 30))

    def m(v):
        exp["length_mode"] = v
        target_row.setVisible(v == "target")
        on_change()
    mode.changed.connect(m)
    target_row.setVisible(exp.get("length_mode") == "target")

    fps = Segmented([("24", 24), ("30", 30), ("60", 60)], int(exp.get("fps", 30)))
    fps.setFixedWidth(170)

    def fp(v):
        exp["fps"] = v
        on_change()
    fps.changed.connect(fp)
    sec.row("Frame rate", fps)

    size = Segmented([("720p", 720), ("1080p", 1080), ("1440p", 1440), ("Source", 0)], int(exp.get("size", 1080)))

    def sz(v):
        exp["size"] = v
        on_change()
    size.changed.connect(sz)
    sec.stacked("Resolution", size)

    quality = Segmented([("High", "high"), ("Balanced", "balanced"), ("Small file", "small")], exp.get("quality"))

    def q(v):
        exp["quality"] = v
        on_change()
    quality.changed.connect(q)
    sec.stacked("Quality", quality)

    toggle(sec, "Smooth out flicker", exp, "deflicker", on_change, accent,
           "Evens out brightness jumps between shots, which timelapses are prone to.")

    hold = QDoubleSpinBox()
    hold.setRange(0, 10)
    hold.setSingleStep(0.5)
    hold.setDecimals(1)
    hold.setSuffix(" s")
    hold.setFixedWidth(78)
    hold.setValue(float(exp.get("hold_end", 1.5)))

    def h(v):
        exp["hold_end"] = float(v)
        on_change()
    hold.valueChanged.connect(h)
    sec.row("Hold the last frame", hold, "Freeze on the finished result at the end of the video.")
    return sec


def overlay_section(ov, on_change, accent, expanded=True):
    sec = Section("Overlays", expanded=expanded)

    def line(text, key, pos_key, hint):
        row = QHBoxLayout()
        row.setSpacing(10)
        lab = label(text, "FieldLabel")
        lab.setToolTip(hint)
        t = Toggle(bool(ov.get(key)), accent)
        combo = position_combo(ov, pos_key, on_change)
        combo.setEnabled(t.isChecked())

        def f(v):
            ov[key] = bool(v)
            combo.setEnabled(v)
            on_change()
        t.toggled.connect(f)
        row.addWidget(t)
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(combo)
        sec.add(row)

    line("Stopwatch", "timer", "timer_position", "Real time elapsed since the session started.")
    line("Clock", "clock", "clock_position", "The time of day each shot was taken.")
    line("rewnd tag", "watermark", "watermark_position", "A small rewnd mark. Turn it off any time, it's your video.")

    text = QLineEdit(ov.get("text", ""))
    text.setPlaceholderText("Caption, e.g. building inkd")
    pos = position_combo(ov, "text_position", on_change)

    def t(v):
        ov["text"] = v.strip()
        on_change()
    text.textChanged.connect(t)
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(text, 1)
    row.addWidget(pos)
    sec.form.addWidget(label("Caption", "FieldLabel"))
    sec.add(row)

    scale = SliderRow("Size", 50, 200, int(float(ov.get("scale", 1.0)) * 100), fmt="{}%", reset=100)

    def s(v):
        ov["scale"] = v / 100.0
        on_change()
    scale.changed.connect(s)
    sec.add(scale)

    opacity = SliderRow("Background", 0, 100, int(float(ov.get("pill_opacity", 0.55)) * 100), fmt="{}%", reset=55)

    def o(v):
        ov["pill_opacity"] = v / 100.0
        on_change()
    opacity.changed.connect(o)
    sec.add(opacity)

    swatches = QHBoxLayout()
    swatches.setSpacing(8)
    buttons = []

    def pick(hex_):
        ov["accent"] = hex_
        for b, h in buttons:
            b.setChecked(h == hex_)
        on_change()
    for name, hex_ in theme.ACCENTS.items():
        b = QPushButton()
        b.setCheckable(True)
        b.setFixedSize(22, 22)
        b.setToolTip(name)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton {{ background: {hex_}; border-radius: 11px; border: 2px solid transparent; padding: 0; }}"
            f"QPushButton:checked {{ border: 2px solid {theme.TEXT}; }}")
        b.setChecked(hex_.lower() == str(ov.get("accent", "")).lower())
        b.clicked.connect(lambda _=False, h=hex_: pick(h))
        buttons.append((b, hex_))
        swatches.addWidget(b)
    swatches.addStretch(1)
    sec.form.addWidget(label("Accent colour", "FieldLabel"))
    sec.add(swatches)
    return sec
