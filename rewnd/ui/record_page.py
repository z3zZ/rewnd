"""Record page: live WYSIWYG preview, the record button and every capture setting."""

import time
from pathlib import Path

import cv2
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from rewnd import imaging, schedule
from rewnd.platform_utils import IS_WINDOWS, free_space
from rewnd.ui import forms, theme
from rewnd.ui.schedule_panel import SchedulePanel, clock, restyle
from rewnd.ui.widgets import (Chips, PreviewView, RecordButton, Section, Segmented, SliderRow, StatusPill,
                              cv_to_qimage, label)



def fmt_duration(sec):
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_bytes(n):
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.0f} PB"


class Stat(QWidget):
    def __init__(self, caption, value="—", key=False):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(label(caption, "Caps"))
        self.value = label(value, "StatKey" if key else "Stat")
        v.addWidget(self.value)

    def set(self, text):
        self.value.setText(text)


class ExposureStat(QWidget):
    """Bottom-bar readout in the scheduled exposure mode: the active profile and what happens next."""

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(label("EXPOSURE", "Caps"), 0, Qt.AlignRight)
        self.value = label("\u2014", "StatSmall")
        v.addWidget(self.value, 0, Qt.AlignRight)
        self.sub = label("", "Faint")
        v.addWidget(self.sub, 0, Qt.AlignRight)
        self.hide()

    def set_state(self, st):
        if not st.get("active"):
            self.hide()
            return
        self.show()
        if st.get("supported") is False:
            self.value.setText("auto")
            self.sub.setText("camera can't do manual")
            restyle(self.sub, "Warn")
            self.setToolTip(st.get("reason", ""))
            return
        restyle(self.sub, "Faint")
        self.value.setText(f"{st['name']}  {schedule.fmt_stops(st['value'])}")
        if st.get("easing") and st.get("ease_end"):
            left = max(1, round((st["ease_end"] - time.time()) / 60))
            self.sub.setText(f"easing in from {st.get('ease_from') or 'last'} \u00b7 {left} min")
        elif st.get("next_at"):
            self.sub.setText(f"{st['next_name']} at {clock(st['next_at'])}")
        else:
            self.sub.setText(st.get("note") or "no switch coming up")
        tip = (f"Exposure {st['value']:.2f} stops ({schedule.shutter_text(st['value'])}), "
               f"profile \"{st['name']}\".")
        if st.get("next_at"):
            tip += (f" Switches to \"{st['next_name']}\" ({schedule.fmt_stops(st['next_value'])}) "
                    f"at {clock(st['next_at'])}.")
        if st.get("note"):
            tip += f" Note: {st['note']}."
        self.setToolTip(tip)


class RecordPage(QWidget):
    record_clicked = Signal()
    ambient_changed = Signal(object)
    settings_changed = Signal()
    camera_changed = Signal()
    controls_changed = Signal()
    refresh_cameras = Signal()
    driver_settings = Signal()
    schedule_changed = Signal()

    def __init__(self, cfg, engine):
        super().__init__()
        self.setObjectName("Page")
        self.cfg = cfg
        self.engine = engine
        self.accent = cfg["ui"]["accent"]
        self.session = None
        self.rec_started = None
        self._last_ts = 0.0
        self._last_ambient = 0.0
        self._last_state_ui = 0.0
        self.cameras = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ----- left: preview + controls -----
        left = QVBoxLayout()
        left.setContentsMargins(24, 18, 18, 20)
        left.setSpacing(12)
        info = QHBoxLayout()
        info.setSpacing(10)
        self.status = StatusPill()
        self.status.set("starting", theme.TEXT_3)
        info.addWidget(self.status)
        self.cam_label = label("", "Mono")
        info.addWidget(self.cam_label)
        info.addStretch(1)
        self.disk_label = label("", "Mono")
        info.addWidget(self.disk_label)
        left.addLayout(info)

        self.preview = PreviewView()
        left.addWidget(self.preview, 1)

        bar = QFrame()
        bar.setObjectName("Glass")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(22, 12, 22, 12)
        bl.setSpacing(28)
        self.stat_frames = Stat("FRAMES", "0", key=True)
        self.stat_elapsed = Stat("ELAPSED", "00:00:00")
        self.stat_video = Stat("VIDEO", "0.0s")
        for s in (self.stat_frames, self.stat_elapsed, self.stat_video):
            bl.addWidget(s)
        bl.addStretch(1)
        self.rec_btn = RecordButton()
        self.rec_btn.accent = self.accent
        self.rec_btn.clicked.connect(self.record_clicked)
        self.rec_btn.setToolTip("Start recording (Space)")
        bl.addWidget(self.rec_btn)
        bl.addStretch(1)
        self.exp_stat = ExposureStat()
        bl.addWidget(self.exp_stat)
        right_stats = QVBoxLayout()
        right_stats.setSpacing(2)
        right_stats.addWidget(label("NEXT SHOT", "Caps"), 0, Qt.AlignRight)
        self.next_label = label("—", "Stat")
        right_stats.addWidget(self.next_label, 0, Qt.AlignRight)
        bl.addLayout(right_stats)
        left.addWidget(bar)
        root.addLayout(left, 1)

        # ----- right: settings -----
        side = QFrame()
        side.setObjectName("SidePanel")
        side.setFixedWidth(368)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        panel = QWidget()
        panel.setObjectName("Panel")
        self.panel = QVBoxLayout(panel)
        self.panel.setContentsMargins(18, 2, 14, 18)
        self.panel.setSpacing(0)
        scroll.setWidget(panel)
        sl.addWidget(scroll)
        side_wrap = QVBoxLayout()
        side_wrap.setContentsMargins(0, 18, 18, 20)
        side_wrap.addWidget(side)
        root.addLayout(side_wrap)

        self._build_camera_section()
        self._build_capture_section()
        self.panel.addWidget(forms.look_section(cfg["look"], self._changed, self.accent, expanded=False))
        self.panel.addWidget(forms.framing_section(cfg["export"], self._changed, self.accent, expanded=False))
        self.panel.addWidget(forms.overlay_section(cfg["overlay"], self._changed, self.accent, expanded=True))
        self.panel.addStretch(1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(66)
        self._force = True
        self._update_estimate()

    # ---------- sections ----------

    def _build_camera_section(self):
        sec = Section("camera")
        cam_row = QHBoxLayout()
        cam_row.setSpacing(6)
        self.cam_combo = QComboBox()
        self.cam_combo.currentIndexChanged.connect(self._on_camera_pick)
        refresh = QPushButton()
        refresh.setIcon(theme.icon("refresh"))
        self.refresh_btn = refresh
        refresh.setToolTip("Look for cameras again")
        refresh.setFixedWidth(36)
        refresh.clicked.connect(self.refresh_cameras)
        cam_row.addWidget(self.cam_combo, 1)
        cam_row.addWidget(refresh)
        sec.form.addWidget(label("camera", "FieldLabel"))
        sec.add(cam_row)

        self.res_combo = QComboBox()
        self.res_combo.setFixedWidth(150)
        self.res_combo.currentIndexChanged.connect(self._on_res_pick)
        sec.row("resolution", self.res_combo)

        cam = self.cfg["camera"]

        def ctl():
            self.controls_changed.emit()
            self.settings_changed.emit()
        self._build_focus(sec, cam, ctl)
        self._build_exposure(sec, cam, ctl)
        forms.toggle(sec, "low-light boost", cam, "low_light", ctl, self.accent,
                     "Raises gain and backlight compensation for dim or backlit rooms.")

        def mj():
            self.camera_changed.emit()
            self.settings_changed.emit()
        forms.toggle(sec, "stable USB mode", cam, "mjpeg", mj, self.accent,
                     "Asks the camera for compressed MJPEG. Uses far less USB bandwidth, which helps on hubs and long extension cables.")
        if IS_WINDOWS:
            drv = QPushButton("camera driver settings…")
            drv.setToolTip("Opens the camera's own settings window for focus, exposure, white balance and more.")
            drv.clicked.connect(self.driver_settings)
            sec.add(drv)
        self.panel.addWidget(sec)
        self.camera_section = sec

    def _build_focus(self, sec, cam, ctl):
        def af():
            self._sync_focus_ui()
            ctl()
        self.af_toggle = forms.toggle(
            sec, "autofocus", cam, "autofocus", af, self.accent,
            "On: the camera keeps refocusing, which can make a timelapse pop between sharp and soft. "
            "Off: focus stays put and you set it with the slider.")
        pos = cam.get("focus")
        self.focus_slider = SliderRow("focus", 0, 100, 50 if pos is None else round(pos), fmt="{:d}", reset=50)
        self.focus_slider.setToolTip("Slide until your desk (or whatever matters) is sharp in the preview. "
                                     "On most webcams lower is further away.")

        def level(v):
            cam["focus"] = float(v)
            self.engine.set_focus_pos(v)
            self.settings_changed.emit()
        self.focus_slider.changed.connect(level)
        sec.add(self.focus_slider)
        self.focus_note = label("", "Faint")
        self.focus_note.setWordWrap(True)
        sec.add(self.focus_note)
        self._focus_available = True
        self._sync_focus_ui()

    def _sync_focus_ui(self):
        manual = not self.cfg["camera"].get("autofocus", True)
        self.focus_slider.setVisible(manual and self._focus_available)
        note = ""
        if manual and not self._focus_available:
            note = "This camera doesn't let apps set its focus. It may be fixed-focus already."
        self.focus_note.setText(note)
        self.focus_note.setVisible(bool(note))

    def _build_exposure(self, sec, cam, ctl):
        self.exp_mode = Segmented([("auto", "auto"), ("protect", "protect"), ("manual", "manual"),
                                   ("scheduled", "scheduled")], cam.get("exposure_mode", "protect"))

        def mode(v):
            cam["exposure_mode"] = v
            self._sync_exposure_ui()
            ctl()
            if v == "scheduled":
                self.schedule_panel.activated()
        self.exp_mode.changed.connect(mode)
        self.exp_mode_label = sec.stacked(
            "exposure", self.exp_mode,
            "auto: the camera decides, and bright screens or windows can blow out to white.\n"
            "protect: rewnd lowers the exposure until bright things keep their detail.\n"
            "manual: you set it with the slider, and it stays put.\n"
            "scheduled: different exposures for different times of day, or sunrise and sunset, "
            "eased in gradually so the video doesn't flash.")

        pos = cam.get("exposure")
        self.exp_slider = SliderRow("exposure level", 0, 100, 50 if pos is None else round(pos), fmt="{:d}",
                                    reset=50)

        def level(v):
            cam["exposure"] = float(v)
            self.engine.set_exposure_pos(v)
            self.settings_changed.emit()
        self.exp_slider.changed.connect(level)
        sec.add(self.exp_slider)

        self.schedule_panel = SchedulePanel(self.cfg["exposure_schedule"], cam)

        def sched():
            self.schedule_changed.emit()
            self.settings_changed.emit()
        self.schedule_panel.changed.connect(sched)
        sec.add(self.schedule_panel)

        self.hdr_toggle = forms.toggle(
            sec, "HDR", cam, "hdr", lambda: (self._sync_exposure_ui(), ctl()), self.accent,
            "Takes 2–3 exposures for every frame and blends them, like a phone does, so screens and the "
            "room both keep their detail. Needs at least 2.5 s between shots.")
        self.exp_note = label("", "Faint")
        self.exp_note.setWordWrap(True)
        sec.add(self.exp_note)
        self._exp_available = True
        self._sync_exposure_ui()

    def _sync_exposure_ui(self):
        cam = self.cfg["camera"]
        avail = self._exp_available
        self.exp_mode.setEnabled(avail)
        self.exp_slider.setVisible(avail and cam.get("exposure_mode") == "manual")
        self.schedule_panel.setVisible(avail and cam.get("exposure_mode") == "scheduled")
        self.hdr_toggle.setEnabled(avail)
        note = ""
        if not avail:
            note = "This camera doesn't let apps set its exposure, so it stays on auto."
        elif cam.get("hdr"):
            if float(self.cfg["capture"]["interval"]) < 2.5:
                note = "HDR is paused: it needs at least 2.5 s between shots."
            elif cam.get("exposure_mode") == "auto":
                note = "HDR uses protect for its base exposure."
        self.exp_note.setText(note)
        self.exp_note.setVisible(bool(note))

    def _build_capture_section(self):
        sec = Section("capture")
        cap = self.cfg["capture"]
        sec.form.addWidget(label("take a shot every", "FieldLabel"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.int_chips = Chips([("5s", 5), ("10s", 10), ("22s", 22), ("40s", 40), ("60s", 60)])
        self.int_spin = QDoubleSpinBox()
        self.int_spin.setRange(0.5, 3600)
        self.int_spin.setDecimals(1)
        self.int_spin.setSuffix(" s")
        self.int_spin.setFixedWidth(80)
        self.int_spin.setValue(float(cap["interval"]))
        row.addWidget(self.int_chips, 1)
        row.addWidget(self.int_spin)
        sec.add(row)

        def set_interval(v):
            cap["interval"] = float(v)
            self.int_chips.sync(v)
            if abs(self.int_spin.value() - float(v)) > 1e-6:
                self.int_spin.blockSignals(True)
                self.int_spin.setValue(float(v))
                self.int_spin.blockSignals(False)
            self._changed()
        self.int_chips.picked.connect(set_interval)
        self.int_spin.valueChanged.connect(set_interval)
        self.int_chips.sync(cap["interval"])

        self.estimate = label("", "Faint")
        self.estimate.setWordWrap(True)
        sec.add(self.estimate)

        stop = Segmented([("never", 0), ("1h", 60), ("2h", 120), ("4h", 240), ("8h", 480)],
                         int(cap.get("auto_stop_minutes", 0)))

        def st(v):
            cap["auto_stop_minutes"] = v
            self._changed()
        stop.changed.connect(st)
        sec.stacked("stop automatically after", stop)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        self.folder_label = label(self._short_path(cap["output_root"]), "Mono")
        self.folder_label.setToolTip(cap["output_root"])
        browse = QPushButton("change…")
        browse.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.folder_label, 1)
        folder_row.addWidget(browse)
        sec.form.addWidget(label("save sessions to", "FieldLabel"))
        sec.add(folder_row)

        forms.toggle(sec, "keep computer awake", cap, "keep_awake", self._changed, self.accent,
                     "Stops the computer sleeping mid-recording. The screen can still turn off.")
        forms.toggle(sec, "make the video when I stop", cap, "auto_export", self._changed, self.accent,
                     "Exports straight away using your Framing and Overlay settings. You can re-export later from the Library.")
        self.capture_section = sec
        self.panel.addWidget(sec)

    @staticmethod
    def _short_path(p):
        p = str(p)
        home = str(Path.home())
        return "~" + p[len(home):] if p.startswith(home) else p

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "save sessions to", self.cfg["capture"]["output_root"])
        if d:
            self.cfg["capture"]["output_root"] = d
            self.folder_label.setText(self._short_path(d))
            self.folder_label.setToolTip(d)
            self._changed()

    # ---------- camera list ----------

    def set_cameras(self, cameras, selected_id):
        self.cameras = cameras
        self.cam_combo.blockSignals(True)
        self.cam_combo.clear()
        for c in cameras:
            self.cam_combo.addItem(c.label, c.id)
        idx = next((i for i, c in enumerate(cameras) if c.id == selected_id), 0)
        if cameras:
            self.cam_combo.setCurrentIndex(idx)
        else:
            self.cam_combo.addItem("no camera found")
        self.cam_combo.setEnabled(bool(cameras) and not self.engine.recording())
        self.cam_combo.blockSignals(False)

    def selected_camera(self):
        i = self.cam_combo.currentIndex()
        return self.cameras[i] if 0 <= i < len(self.cameras) else None

    def set_resolutions(self, options, actual):
        self.res_combo.blockSignals(True)
        self.res_combo.clear()
        for r in options:
            self.res_combo.addItem(r.replace("x", " × "), r)
        want = self.cfg["capture"].get("resolution")
        pick = want if want in options else actual
        if pick in options:
            self.res_combo.setCurrentIndex(options.index(pick))
        self.res_combo.blockSignals(False)

    def _on_camera_pick(self, _):
        cam = self.selected_camera()
        if cam:
            self.cfg["capture"]["camera"] = cam.id
            self.cfg["capture"]["resolution"] = None
            self._force = True
            self.camera_changed.emit()
            self.settings_changed.emit()

    def _on_res_pick(self, _):
        r = self.res_combo.currentData()
        if r:
            self.cfg["capture"]["resolution"] = r
            self.camera_changed.emit()
            self.settings_changed.emit()

    # ---------- state ----------

    def _changed(self):
        if hasattr(self, "exp_note"):
            self._sync_exposure_ui()
        self._force = True
        self._update_estimate()
        self.settings_changed.emit()

    def _update_estimate(self):
        cap, exp = self.cfg["capture"], self.cfg["export"]
        per_hour = int(3600 / max(0.1, cap["interval"]))
        fps = int(exp.get("fps", 30))
        if exp.get("length_mode") == "target":
            txt = (f"{per_hour} shots an hour. Videos are fitted to {exp['target_seconds']:.0f}s, "
                   f"so longer sessions just play faster.")
        else:
            txt = f"{per_hour} shots an hour, about {per_hour / fps:.1f}s of video per hour at {fps} fps."
        self.estimate.setText(txt)

    def set_status(self, state, msg=""):
        rec = self.engine.recording()
        if state == "live":
            if rec:
                self.status.set("rec", theme.ACCENT, blink=True)
            else:
                self.status.set("live", theme.ACCENT)
            self.preview.set_message("", "")
        elif state in ("connecting",):
            self.status.set("connecting", theme.TEXT_2)
        elif state == "reconnecting":
            self.status.set("reconnecting", theme.WARN, blink=True)
            if rec:
                self.preview.set_message("camera dropped, reconnecting…", "overlay")
            else:
                self.preview.set_message("reconnecting to the camera…",
                                         "If it's on a USB hub or extension cable, try Stable USB mode or a different port.")
                self.preview.set_image(None)
        elif state == "no-camera":
            self.status.set("no camera", theme.TEXT_3)
            self.preview.set_image(None)
            self.preview.set_message("no camera found", "Plug one in, then press the refresh button next to the camera list.")

    def set_info(self, info):
        if "focus" in info:
            f = info["focus"]
            self._focus_available = f.get("available", True)
            pos = f.get("pos")
            if pos is not None:
                self.cfg["camera"]["focus"] = pos
                self.focus_slider.set_value(round(pos))
            self._sync_focus_ui()
            return
        if "exposure" in info:
            e = info["exposure"]
            self._exp_available = e.get("available", True)
            pos = e.get("pos")
            if pos is not None and e.get("mode") == "manual":
                self.cfg["camera"]["exposure"] = pos
                self.exp_slider.set_value(round(pos))
            self._sync_exposure_ui()
            if "camera" not in info:
                return
        cam = info.get("camera")
        self.cam_label.setText(f"{cam.label if cam else ''}  ·  {info.get('actual', '').replace('x', '×')}")
        self.set_resolutions(info.get("resolutions", []), info.get("actual"))

    def set_recording(self, session):
        self.session = session
        self.rec_started = time.time() if session else None
        rec = session is not None
        self.cam_combo.setEnabled(not rec and bool(self.cameras))
        self.res_combo.setEnabled(not rec)
        self.int_spin.setEnabled(not rec)
        for b, _ in self.int_chips.buttons:
            b.setEnabled(not rec)
        self.preview.rec = rec
        self.rec_btn.setToolTip("Stop recording (Space)" if rec else "Start recording (Space)")
        if rec:
            self.status.set("rec", theme.ACCENT, blink=True)
        elif self.engine.status == "live":
            self.status.set("live", theme.ACCENT)
        self._force = True

    def on_shot(self, idx):
        self.stat_frames.set(str(idx))
        n = idx
        exp = self.cfg["export"]
        if exp.get("length_mode") == "target":
            secs = float(exp["target_seconds"])
        else:
            secs = n / max(1, int(exp.get("fps", 30)))
        self.stat_video.set(f"{secs:.1f}s")

    # ---------- frame loop ----------

    def _tick(self):
        rec = self.engine.recording()
        if rec and self.rec_started:
            elapsed = time.time() - self.rec_started
            self.stat_elapsed.set(fmt_duration(elapsed))
            nxt = self.engine.seconds_to_next()
            interval = max(0.1, self.cfg["capture"]["interval"])
            if nxt is not None:
                self.next_label.setText(f"{nxt:0.0f}s" if nxt >= 1 else "now")
                self.rec_btn.set_state(True, 1.0 - min(1.0, nxt / interval))
        else:
            self.rec_btn.set_state(False)
            self.next_label.setText("—")

        exp_state = self.engine.exposure_state()
        now = time.time()
        if now - self._last_state_ui >= 0.5:
            self._last_state_ui = now
            self.exp_stat.set_state(exp_state)
            self.schedule_panel.set_state(exp_state)

        frame, ts = self.engine.latest()
        if frame is None or (ts == self._last_ts and not self._force):
            return
        self._last_ts = ts
        self._force = False
        h, w = frame.shape[:2]
        k = 1080 / min(w, h)
        if k < 1:
            frame = cv2.resize(frame, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
        elapsed = (time.time() - self.rec_started) if (rec and self.rec_started) else 0.0
        try:
            img = imaging.render(frame, self.cfg["look"], self.cfg["export"], self.cfg["overlay"],
                                 elapsed=elapsed, wall=imaging.wall_time(ts), size=self._preview_size(),
                                 ev=exp_state.get("comp", 0.0))
        except Exception:
            return
        self.preview.set_image(cv_to_qimage(img))

    def _preview_size(self):
        """Short side to render at: what the preview box can show on this screen, capped at 1080."""
        dpr = self.preview.devicePixelRatioF() or 1.0
        short = min(self.preview.width(), self.preview.height()) * dpr
        return int(max(360, min(1080, short)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._force = True

    def retheme(self):
        self.refresh_btn.setIcon(theme.icon("refresh"))
        self._force = True

    def refresh_disk(self):
        free = free_space(self.cfg["capture"]["output_root"])
        self.disk_label.setText(f"{fmt_bytes(free).lower()} free")
