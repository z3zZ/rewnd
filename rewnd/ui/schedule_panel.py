"""Settings for the "scheduled" exposure mode: profiles, the night value, sunrise/sunset, easing.

Shown under the exposure mode switch in the camera section while "scheduled" is picked.
"""

import threading
import time
from datetime import datetime

from PySide6.QtCore import QLocale, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLineEdit, QPushButton,
                               QTimeEdit, QVBoxLayout, QWidget)

from rewnd import schedule
from rewnd.ui.widgets import Segmented, label


def restyle(w, name):
    if w.objectName() != name:
        w.setObjectName(name)
        w.style().unpolish(w)
        w.style().polish(w)


def clock(ts):
    """'16:00', or 'tomorrow 08:00' / 'Sat 08:00' when it isn't today."""
    if ts is None:
        return ""
    dt = datetime.fromtimestamp(ts)
    days = (dt.date() - datetime.now().date()).days
    hm = dt.strftime("%H:%M")
    if days == 0:
        return hm
    if days == 1:
        return f"tomorrow {hm}"
    return f"{dt.strftime('%a')} {hm}"


class ValueField(QLineEdit):
    """Exposure typed in directly, in stops. Emits only valid values."""
    value_changed = Signal(float)

    def __init__(self, value):
        super().__init__(f"{float(value):g}")
        v = QDoubleValidator(schedule.MIN_STOPS, schedule.MAX_STOPS, 2, self)
        v.setNotation(QDoubleValidator.StandardNotation)
        v.setLocale(QLocale.c())  # always "." for decimals, whatever the system language
        self.setValidator(v)
        self.setFixedWidth(62)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setToolTip(f"Exposure in stops, from {schedule.MIN_STOPS:g} to {schedule.MAX_STOPS:g}. "
                        "The shutter is open 2^value seconds: -6 is 1/64 s, -7 is half as bright, "
                        "-5 twice as bright. Decimals are fine. Same meaning on Windows and Linux.")
        self.textEdited.connect(self._edited)

    def _edited(self, text):
        if self.hasAcceptableInput():
            self.value_changed.emit(float(text))


def time_edit(hhmm):
    t = QTimeEdit()
    t.setDisplayFormat("HH:mm")
    m = schedule.parse_time(hhmm) or 0
    t.setTime(QTime(m // 60, m % 60))
    t.setFixedWidth(62)
    t.setAlignment(Qt.AlignCenter)
    t.setWrapping(True)
    return t


class SchedulePanel(QWidget):
    changed = Signal()
    _located = Signal(object)   # (lat, lon) or an error message, from the lookup thread

    def __init__(self, sched_cfg, cam_cfg):
        super().__init__()
        self.cfg = sched_cfg
        self.cam = cam_cfg
        self.state = {}
        self._locating = False
        self._quiet = True
        self._located.connect(self._on_located)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        self.mode = Segmented([("fixed times", "fixed"), ("sunrise / sunset", "sun")], sched_cfg.get("mode"))
        self.mode.changed.connect(self._set_mode)
        box.addWidget(self.mode)
        self.mode_hint = label("", "Faint")
        self.mode_hint.setWordWrap(True)
        box.addWidget(self.mode_hint)

        # sunrise/sunset: which profile, and where
        self.sun_box = QWidget()
        sb = QVBoxLayout(self.sun_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(8)
        r = QHBoxLayout()
        r.addWidget(label("sunrise to sunset", "FieldLabel"))
        r.addStretch(1)
        self.day_combo = QComboBox()
        self.day_combo.setFixedWidth(140)
        self.day_combo.setToolTip("The profile used while the sun is up. Night is used the rest of the time.")
        self.day_combo.currentIndexChanged.connect(self._set_day_profile)
        r.addWidget(self.day_combo)
        sb.addLayout(r)
        sb.addWidget(label("location", "FieldLabel"))
        self.place = QLineEdit(sched_cfg["location"].get("query", ""))
        self.place.setPlaceholderText("city, or 51.5, -0.12")
        self.place.setToolTip("A city from an offline list, or latitude and longitude. Nothing is looked up online.")
        self.place.returnPressed.connect(self._set_place)
        set_btn = QPushButton("set")
        set_btn.clicked.connect(self._set_place)
        r = QHBoxLayout()
        r.setSpacing(6)
        r.addWidget(self.place, 1)
        r.addWidget(set_btn)
        sb.addLayout(r)
        self.loc_label = label("", "Mono")
        self.loc_label.setWordWrap(True)
        sb.addWidget(self.loc_label)
        self.sys_btn = QPushButton("use this computer's location")
        self.sys_btn.setObjectName("Ghost")
        self.sys_btn.clicked.connect(lambda: self.locate(quiet=False))
        sb.addWidget(self.sys_btn, 0, Qt.AlignLeft)
        self.sun_label = label("", "Faint")
        self.sun_label.setWordWrap(True)
        sb.addWidget(self.sun_label)
        box.addWidget(self.sun_box)

        box.addWidget(label("profiles", "FieldLabel"))
        self.cards = QVBoxLayout()
        self.cards.setSpacing(6)
        box.addLayout(self.cards)
        add = QPushButton("+ add profile")
        add.setObjectName("Ghost")
        add.clicked.connect(self._add_profile)
        box.addWidget(add, 0, Qt.AlignLeft)
        self.overlap = label("", "Faint")
        self.overlap.setWordWrap(True)
        box.addWidget(self.overlap)

        r = QHBoxLayout()
        r.setSpacing(8)
        night_lab = label("night", "FieldLabel")
        night_lab.setToolTip("Used whenever no profile's time range applies.")
        r.addWidget(night_lab)
        r.addWidget(label("outside every range", "Faint"))
        r.addStretch(1)
        self.night = ValueField(sched_cfg.get("night_value", schedule.DEFAULT_NIGHT))
        self.night_shutter = label("", "Mono")
        self.night_shutter.setFixedWidth(52)
        self.night.value_changed.connect(self._set_night)
        r.addWidget(self.night)
        r.addWidget(self.night_shutter)
        box.addLayout(r)

        self.ease = QDoubleSpinBox()
        self.ease.setRange(0, 240)
        self.ease.setDecimals(0)
        self.ease.setSuffix(" min")
        self.ease.setFixedWidth(78)
        self.ease.setValue(float(sched_cfg.get("ease_minutes", 25)))
        self.ease.valueChanged.connect(self._set_ease)
        self.ease.setToolTip("A switch starts at its time and glides to the new exposure over this long, so "
                             "the video doesn't flash. 0 switches instantly.")
        r = QHBoxLayout()
        r.addWidget(label("ease each switch over", "FieldLabel"))
        r.addStretch(1)
        r.addWidget(self.ease)
        box.addLayout(r)

        self.cam_hint = label("", "Faint")
        self.cam_hint.setWordWrap(True)
        box.addWidget(self.cam_hint)

        self._build_cards()
        self._sync()
        self._sun_timer = QTimer(self)
        self._sun_timer.timeout.connect(self._refresh_sun)
        self._sun_timer.start(60_000)
        QTimer.singleShot(1200, self.activated)

    # ---------- profile cards ----------

    def _build_cards(self):
        while self.cards.count():
            item = self.cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.time_rows = []
        self.card_warns = []
        for i, p in enumerate(self.cfg["profiles"]):
            self.cards.addWidget(self._card(i, p))
        self._sync_day_combo()
        self._check_ranges()

    def _card(self, i, p):
        card = QFrame()
        card.setObjectName("ProfileCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 6, 6, 8)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLineEdit(p["name"])
        name.setPlaceholderText(f"profile {i + 1}")
        name.textEdited.connect(lambda t, i=i: self._edit(i, "name", t.strip() or f"profile {i + 1}"))
        top.addWidget(name, 1)
        rm = QPushButton("✕")
        rm.setObjectName("Ghost")
        rm.setToolTip("Remove this profile")
        rm.clicked.connect(lambda _=False, i=i: self._remove(i))
        top.addWidget(rm)
        v.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(6)
        times = QWidget()
        tl = QHBoxLayout(times)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        start, end = time_edit(p["start"]), time_edit(p["end"])
        start.setToolTip("Starts at")
        end.setToolTip("Ends at. An end before the start runs past midnight.")
        start.timeChanged.connect(lambda t, i=i: self._edit(i, "start", t.toString("HH:mm")))
        end.timeChanged.connect(lambda t, i=i: self._edit(i, "end", t.toString("HH:mm")))
        tl.addWidget(start)
        tl.addWidget(label("–", "Faint"))
        tl.addWidget(end)
        row.addWidget(times)
        self.time_rows.append(times)
        row.addStretch(1)
        val = ValueField(p["value"])
        shutter = label(schedule.shutter_text(p["value"]), "Mono")
        shutter.setFixedWidth(52)

        def set_val(x, i=i, shutter=shutter):
            shutter.setText(schedule.shutter_text(x))
            self._edit(i, "value", x)
        val.value_changed.connect(set_val)
        row.addWidget(val)
        row.addWidget(shutter)
        v.addLayout(row)
        warn = label("", "Warn")
        warn.hide()
        v.addWidget(warn)
        self.card_warns.append(warn)
        return card

    def _edit(self, i, key, value):
        if i < len(self.cfg["profiles"]):
            self.cfg["profiles"][i][key] = value
            if key == "name":
                self._sync_day_combo()
            if key in ("start", "end"):
                self._check_ranges()
            self._changed()

    def _add_profile(self):
        ps = self.cfg["profiles"]
        start = ps[-1]["end"] if ps else "08:00"
        m = schedule.parse_time(start) or 0
        ps.append({"name": f"profile {len(ps) + 1}", "start": start,
                   "end": schedule.fmt_time(m + 120), "value": schedule.DEFAULT_DAY})
        self._build_cards()
        self._sync()
        self._changed()

    def _remove(self, i):
        del self.cfg["profiles"][i]
        if self.cfg.get("sun_profile", 0) >= len(self.cfg["profiles"]):
            self.cfg["sun_profile"] = 0
        self._build_cards()
        self._sync()
        self._changed()

    def _check_ranges(self):
        """Flag empty ranges, and say who wins where ranges overlap."""
        covered = {}
        overlaps = set()
        sun = self.cfg.get("mode") == "sun"
        for i, p in enumerate(self.cfg["profiles"]):
            s, e = schedule.parse_time(p["start"]), schedule.parse_time(p["end"])
            if i < len(self.card_warns):
                self.card_warns[i].setText("start and end are the same, so this never runs")
                self.card_warns[i].setVisible(s == e and not sun)
            if s is None or e is None or s == e:
                continue
            mins = range(s, e) if s < e else list(range(s, 1440)) + list(range(0, e))
            for m in mins[::5]:
                if m in covered and covered[m] != i:
                    overlaps.add((covered[m], i))
                covered.setdefault(m, i)
        if overlaps and not sun:
            names = sorted({self.cfg["profiles"][a]["name"] for a, _ in overlaps})
            self.overlap.setText("Some ranges overlap. Where they do, the profile higher in the list "
                                 f"wins ({', '.join(names)}).")
            self.overlap.show()
        else:
            self.overlap.hide()

    # ---------- settings ----------

    def _changed(self):
        self._refresh_sun()
        self.changed.emit()

    def _set_mode(self, mode):
        self.cfg["mode"] = mode
        self._sync()
        self._check_ranges()
        if mode == "sun" and self.cfg["location"].get("lat") is None:
            self.locate(quiet=False)   # refused unless the scheduled mode is on (see _may_locate)
        self._changed()

    def _set_day_profile(self, idx):
        if idx >= 0:
            self.cfg["sun_profile"] = idx
            self._changed()

    def _set_night(self, v):
        self.cfg["night_value"] = v
        self.night_shutter.setText(schedule.shutter_text(v))
        self._changed()

    def _set_ease(self, v):
        self.cfg["ease_minutes"] = float(v)
        self._changed()

    def _sync_day_combo(self):
        self.day_combo.blockSignals(True)
        self.day_combo.clear()
        for p in self.cfg["profiles"]:
            self.day_combo.addItem(p["name"])
        if not self.cfg["profiles"]:
            self.day_combo.addItem("add a profile first")
        self.day_combo.setEnabled(bool(self.cfg["profiles"]))
        self.day_combo.setCurrentIndex(min(self.cfg.get("sun_profile", 0), max(0, self.day_combo.count() - 1)))
        self.day_combo.blockSignals(False)

    def _sync(self):
        sun = self.cfg.get("mode") == "sun"
        self.sun_box.setVisible(sun)
        for w in self.time_rows:
            w.setVisible(not sun)
        self.night_shutter.setText(schedule.shutter_text(self.cfg.get("night_value", schedule.DEFAULT_NIGHT)))
        if sun:
            self.mode_hint.setText("The profile you pick runs from sunrise to sunset; night runs the rest. "
                                   "Each profile's own times are ignored.")
        else:
            self.mode_hint.setText("Each profile runs in its time range. Night covers the rest of the day.")
        self._show_location()
        self._refresh_sun()
        self._show_state()

    # ---------- location ----------

    def _may_locate(self):
        """The only time rewnd asks the OS for the location: the scheduled exposure mode is on and
        set to sunrise / sunset. Every lookup, automatic or from the button, goes through this."""
        return self.cam.get("exposure_mode") == "scheduled" and self.cfg.get("mode") == "sun"

    def _wants_system_location(self):
        """Look up automatically only if the user hasn't typed a place."""
        return self._may_locate() and self.cfg["location"].get("source") == "system" and not self._locating

    def activated(self):
        """Called when the scheduled mode is picked (and once at launch)."""
        if self._wants_system_location():
            self.locate(quiet=True)

    def locate(self, quiet=False):
        """Ask the OS where we are, off the UI thread. Refuses outside sunrise / sunset mode."""
        if self._locating or not self._may_locate():
            return
        self._locating = True
        self._quiet = quiet
        self.sys_btn.setEnabled(False)
        if not quiet or self.cfg["location"].get("lat") is None:
            self.loc_label.setText("finding this computer…")

        def work():
            from rewnd.location import LocationUnavailable, system_location
            try:
                self._located.emit(system_location())
            except LocationUnavailable as e:
                self._located.emit(str(e))
            except Exception as e:  # never let a lookup crash the app
                self._located.emit(f"Couldn't get the system location ({e}). Type a city instead.")
        threading.Thread(target=work, daemon=True, name="rewnd-locate").start()

    def _on_located(self, result):
        self._locating = False
        self.sys_btn.setEnabled(True)
        if isinstance(result, tuple):
            lat, lon = result
            near = schedule.nearest_city(lat, lon)
            self.cfg["location"].update(source="system", lat=lat, lon=lon,
                                        label=f"this computer, near {near.split(',')[0]}" if near else
                                        "this computer")
            self._show_location()
            self._changed()
        else:
            quiet_ok = self._quiet and self.cfg["location"].get("lat") is not None
            self._show_location(error=None if quiet_ok else result)

    def _set_place(self):
        text = self.place.text().strip()
        found = schedule.parse_place(text)
        if not found:
            self._show_location(error="Couldn't find that place. Try a bigger city nearby, or type "
                                      "coordinates like 51.5, -0.12.")
            return
        lat, lon, name = found
        self.cfg["location"].update(source="manual", query=text, lat=lat, lon=lon, label=name)
        self._show_location()
        self._changed()

    def _show_location(self, error=None):
        loc = self.cfg["location"]
        if error:
            self.loc_label.setText(error)
            restyle(self.loc_label, "Warn")
            return
        restyle(self.loc_label, "Mono")
        if loc.get("lat") is None:
            self.loc_label.setText("no location yet")
        else:
            self.loc_label.setText(f"{loc.get('label') or 'set'}  ·  {loc['lat']:.2f}, {loc['lon']:.2f}")

    def _refresh_sun(self):
        if self.cfg.get("mode") != "sun" or self.cfg["location"].get("lat") is None:
            self.sun_label.setText("")
            return
        plan = schedule.plan_at(schedule.clean(self.cfg), time.time())
        parts = []
        if plan.sunrise:
            parts.append(f"sunrise {clock(plan.sunrise)}")
        if plan.sunset:
            parts.append(f"sunset {clock(plan.sunset)}")
        text = "today: " + " · ".join(parts) if parts else ""
        if plan.note:
            text = (text + ". " if text else "") + plan.note[0].upper() + plan.note[1:] + "."
        self.sun_label.setText(text)

    # ---------- live state from the engine ----------

    def set_state(self, state):
        self.state = state or {}
        self._show_state()

    def _show_state(self):
        st = self.state
        bits = []
        rng = st.get("range")
        if rng:
            bits.append(f"This camera goes from {rng[0]:.1f} ({schedule.shutter_text(rng[0])}) "
                        f"to {rng[1]:.1f} ({schedule.shutter_text(rng[1])}).")
        if st.get("supported") and st.get("applied") is not None:
            now = f"Now {st['value']:.2f}"
            if abs(st.get("comp", 0)) >= 0.01:
                now += (f": the camera is at {st['applied']:.1f} and rewnd makes up the "
                        f"{st['comp']:+.2f} in the picture")
            bits.append(now + ".")
        self.cam_hint.setText(" ".join(bits))
        self.cam_hint.setVisible(bool(bits))
