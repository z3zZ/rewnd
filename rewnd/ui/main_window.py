"""Main window: top bar, Record/Library pages, the capture engine and background exports."""

import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from rewnd import APP_NAME, REPO, __version__, config, updates
from rewnd.camera import CaptureEngine, find_camera, list_cameras
from rewnd.platform_utils import KeepAwake, open_path, reveal
from rewnd.session import Session
from rewnd.ui.export_dialog import ExportDialog, ExportJob, Relay
from rewnd.ui.library_page import LibraryPage
from rewnd.ui.record_page import RecordPage
from rewnd.ui import theme
from rewnd.ui.colour import ColourButton
from rewnd.ui.widgets import GlassBackdrop, Logo, Segmented, Toast, Wordmark, label


class EngineBridge(QObject):
    """Engine callbacks fire on the capture thread; these signals hop them onto the UI thread."""
    status = Signal(str, str)
    shot = Signal(int, str)
    info = Signal(dict)
    error = Signal(str)
    update = Signal(object)   # updates.Release, from the update-check thread


class MainWindow(QMainWindow):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.accent = cfg["ui"]["accent"]
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 860)
        self.setMinimumSize(1080, 680)
        self.keep_awake = KeepAwake()
        self.session = None
        self.jobs = []

        self.bridge = EngineBridge()
        self.engine = CaptureEngine(
            on_status=lambda s, m: self.bridge.status.emit(s, m),
            on_shot=lambda i, p: self.bridge.shot.emit(i, str(p)),
            on_info=lambda d: self.bridge.info.emit(d),
            on_error=lambda m: self.bridge.error.emit(m),
        )

        root = GlassBackdrop()
        self.backdrop = root
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._top_bar())

        self.record = RecordPage(cfg, self.engine)
        self.library = LibraryPage(cfg)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.record)
        self.stack.addWidget(self.library)
        v.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.toast = Toast(root)

        self.bridge.status.connect(self._on_status)
        self.bridge.shot.connect(self._on_shot)
        self.bridge.info.connect(self.record.set_info)
        self.bridge.error.connect(self._on_error)
        self.record.record_clicked.connect(self.toggle_recording)
        self.record.settings_changed.connect(self._schedule_save)
        self.record.camera_changed.connect(self._configure_engine)
        self.record.controls_changed.connect(lambda: self.engine.apply_controls(self.cfg["camera"]))
        self.record.refresh_cameras.connect(self._load_cameras)
        self.record.driver_settings.connect(self.engine.open_driver_settings)
        self.record.schedule_changed.connect(
            lambda: self.engine.set_exposure_schedule(self.cfg["exposure_schedule"]))
        self.library.export_requested.connect(self.open_export)
        self.record.ambient_changed.connect(self.backdrop.set_ambient)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(lambda: config.save(self.cfg))
        self._housekeeping = QTimer(self)
        self._housekeeping.timeout.connect(self._tick)
        self._housekeeping.start(1000)

        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._space)
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.go(0))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self.go(1))

        self.release = None
        self._announced = None
        self.bridge.update.connect(self._on_update)
        if self.cfg["ui"].get("check_updates", True):
            QTimer.singleShot(4000, self.check_for_updates)
            self._update_timer = QTimer(self)
            self._update_timer.timeout.connect(self.check_for_updates)
            self._update_timer.start(6 * 60 * 60 * 1000)   # long sessions still hear about releases

        self.engine.set_exposure_schedule(self.cfg["exposure_schedule"])
        self.engine.start()
        self._load_cameras()
        self.record.refresh_disk()

    # ---------- chrome ----------

    def _top_bar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(52)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(10)
        self.logo = Logo(18)
        h.addWidget(self.logo)
        self.wordmark = Wordmark(15)
        h.addWidget(self.wordmark)
        h.addSpacing(10)
        self.tagline = label(theme.tagline_html(), "Tagline")
        self.tagline.setTextFormat(Qt.RichText)
        h.addWidget(self.tagline)
        h.addStretch(1)
        self.tabs = QButtonGroup(self)
        for i, text in enumerate(("record", "library")):
            b = QPushButton(text)
            b.setObjectName("Tab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.tabs.addButton(b, i)
            h.addWidget(b)
        self.tabs.button(0).setChecked(True)
        self.tabs.idClicked.connect(self.go)
        h.addStretch(1)
        self.theme_switch = Segmented([("black", "black"), ("light", "light"), ("aero", "aero")],
                                      self.cfg["ui"].get("theme", "black"))
        self.theme_switch.setFixedSize(180, 30)
        self.theme_switch.setToolTip("App theme")
        self.theme_switch.changed.connect(self.set_theme)
        h.addWidget(self.theme_switch)
        self.accent_btn = ColourButton(self.cfg["ui"].get("accent", theme.DEFAULT_ACCENT))
        self.accent_btn.setToolTip("App accent colour")
        self.accent_btn.changed.connect(self.set_accent)
        h.addWidget(self.accent_btn)
        h.addSpacing(10)
        self.clock = label("", "Mono")
        h.addWidget(self.clock)
        h.addSpacing(8)
        h.addWidget(label(f"v{__version__}", "Faint"))
        self.update_btn = QPushButton()
        self.update_btn.setObjectName("Primary")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.clicked.connect(self._open_update)
        self.update_btn.hide()
        h.addWidget(self.update_btn)
        self._update_clock()
        return bar

    def set_theme(self, name):
        self.cfg["ui"]["theme"] = name
        self.retheme()

    def set_accent(self, hx):
        self.cfg["ui"]["accent"] = hx
        self.retheme()

    def retheme(self):
        """Apply the theme/accent live: new stylesheet, then refresh the few hand-styled pieces."""
        from PySide6.QtWidgets import QApplication
        theme.apply(self.cfg["ui"].get("theme", "black"), self.cfg["ui"].get("accent", theme.DEFAULT_ACCENT))
        QApplication.instance().setStyleSheet(theme.stylesheet())
        self.wordmark.retheme()
        self.logo.update()
        self.record.retheme()
        self.tagline.setText(theme.tagline_html())
        self.backdrop.retheme()
        self.record.set_status(self.engine.status)
        for w in self.findChildren(QWidget):
            w.update()
        self._schedule_save()

    def _update_clock(self):
        self.clock.setText(time.strftime("%H:%M"))

    def go(self, i):
        self.tabs.button(i).setChecked(True)
        self.stack.setCurrentIndex(i)
        if i == 1:
            self.library.refresh()

    def _space(self):
        if self.stack.currentIndex() == 0:
            self.toggle_recording()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.reposition()

    # ---------- camera ----------

    def _load_cameras(self):
        cams = list_cameras()
        saved = self.cfg["capture"].get("camera")
        cam = find_camera(cams, saved) or (cams[0] if cams else None)
        self.record.set_cameras(cams, cam.id if cam else None)
        if cam:
            self.cfg["capture"]["camera"] = cam.id
        self._configure_engine()

    def _configure_engine(self):
        cam = self.record.selected_camera()
        self.engine.configure(cam, self.cfg["capture"].get("resolution"), self.cfg["camera"])

    def _on_error(self, msg):
        self.toast.show_message("heads up", msg, timeout=6000)

    def _on_status(self, state, msg):
        self.record.set_status(state, msg)
        if state == "reconnecting" and self.session is not None and msg:
            self.toast.show_message("camera dropped", "Still recording. Shots resume as soon as it's back.",
                                    timeout=5000)

    # ---------- updates ----------

    def check_for_updates(self):
        skip = self.cfg["ui"].get("skip_version", "")

        def run():
            rel = updates.check(REPO, __version__, skip)
            if rel is not None:
                self.bridge.update.emit(rel)
        threading.Thread(target=run, daemon=True, name="rewnd-update-check").start()

    def _on_update(self, rel):
        self.release = rel
        self.update_btn.setText(f"update to {rel.version}")
        self.update_btn.setToolTip(f"{rel.name} is out. Opens the download page.")
        self.update_btn.show()
        if rel.version != self._announced:
            self._announce(rel, 0)

    def _announce(self, rel, tries):
        if self.release is not rel:
            return
        if self.toast.isVisible() and tries < 20:
            # don't cover an export or camera message; the top-bar button is already showing
            QTimer.singleShot(15000, lambda: self._announce(rel, tries + 1))
            return
        self._announced = rel.version
        self.toast.show_message(
            f"rewnd {rel.version} is out",
            rel.summary or "A new version is ready to download.",
            actions=[("get it", self._open_update), ("skip this version", lambda: self._skip_update(rel))],
        )

    def _open_update(self):
        if self.release is not None:
            QDesktopServices.openUrl(QUrl(self.release.url))
        self.toast.hide()

    def _skip_update(self, rel):
        self.cfg["ui"]["skip_version"] = rel.version
        self._schedule_save()
        self.update_btn.hide()
        self.toast.hide()

    # ---------- recording ----------

    def toggle_recording(self):
        if self.session is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        if self.engine.status != "live":
            self.toast.show_message("no camera yet", "Wait for the preview to show up, then hit record.",
                                    timeout=4000)
            return
        try:
            self.session = Session.create(self.cfg["capture"]["output_root"], self.cfg)
        except OSError as e:
            QMessageBox.critical(self, "Can't create session", f"Couldn't create the session folder:\n{e}")
            return
        config.save(self.cfg)
        self.engine.start_recording(self.session, self.cfg["capture"]["interval"],
                                    self.cfg["capture"].get("jpeg_quality", 95))
        if self.cfg["capture"].get("keep_awake", True):
            self.keep_awake.start()
        self.record.set_recording(self.session)
        self.record.on_shot(0)
        self.toast.hide()

    def stop_recording(self):
        s = self.engine.stop_recording()
        self.keep_awake.stop()
        self.record.set_recording(None)
        self.session = None
        if s is None:
            return
        s.meta["ended"] = time.time()
        # Framing and overlay choices made during the recording belong to this session.
        s.meta.update(look=self.cfg["look"], overlay=self.cfg["overlay"], export=self.cfg["export"])
        s.save_meta()
        n = len(s.frames())
        if n == 0:
            self.toast.show_message("nothing captured", "The session stopped before the first shot.", timeout=5000)
            return
        if self.cfg["capture"].get("auto_export", True):
            self.run_export(s)
        else:
            self.toast.show_message(f"saved {n} frames", "Export it whenever you like from the library.",
                                    actions=[("export…", lambda: self.open_export(s))])

    def _on_shot(self, idx, _path):
        self.record.on_shot(idx)

    def _tick(self):
        self._update_clock()
        if self.session is not None:
            mins = int(self.cfg["capture"].get("auto_stop_minutes", 0) or 0)
            if mins and time.time() - self.session.meta.get("started", time.time()) >= mins * 60:
                self.stop_recording()
        if int(time.time()) % 30 == 0:
            self.record.refresh_disk()

    # ---------- exporting ----------

    def run_export(self, session):
        job = ExportJob(session, session.meta.get("look", self.cfg["look"]),
                        session.meta.get("export", self.cfg["export"]),
                        session.meta.get("overlay", self.cfg["overlay"]))
        n = len(session.frames())
        self.toast.show_message("making your video…", f"{n} frames", progress=0.0)

        def finished():
            if job in self.jobs:
                self.jobs.remove(job)
        job.relay = Relay(
            job,
            on_progress=lambda d, t: self.toast.show_message("making your video…", f"{n} frames",
                                                             progress=d / max(1, t)),
            on_done=self._export_done,
            on_failed=lambda m: self.toast.show_message(
                "export failed", m, actions=[("try again…", lambda: self.open_export(session))]),
            on_finished=finished, parent=self)
        self.jobs.append(job)
        job.start()

    def _export_done(self, path):
        self.toast.show_message("video ready", path.replace("\\", "/").rsplit("/", 1)[-1],
                                actions=[("play", lambda: open_path(path)), ("show in folder", lambda: reveal(path))],
                                timeout=15000)
        if self.stack.currentIndex() == 1:
            self.library.refresh()

    def open_export(self, session):
        dlg = ExportDialog(session, self.cfg, self.accent, self)
        dlg.saved_defaults.connect(self._save_defaults)
        dlg.exec()
        self.library.refresh()

    def _save_defaults(self, look, exp, ov):
        self.cfg["look"].update(look)
        self.cfg["export"].update(exp)
        self.cfg["overlay"].update(ov)
        config.save(self.cfg)

    # ---------- persistence ----------

    def _schedule_save(self):
        self._save_timer.start(500)

    def closeEvent(self, e):
        if self.session is not None:
            r = QMessageBox.question(self, "Recording in progress",
                                     "Stop recording and quit? The frames so far are kept.")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            s = self.engine.stop_recording()
            if s:
                s.meta["ended"] = time.time()
                s.save_meta()
        for job in list(self.jobs):
            job.cancel.set()
            job.thread.wait(3000)
        self.keep_awake.stop()
        self.engine.shutdown()
        self.engine.join(timeout=2)
        config.save(self.cfg)
        super().closeEvent(e)
