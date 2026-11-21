"""Main window: top bar, Record/Library pages, the capture engine and background exports."""

import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from rewnd import APP_NAME, TAGLINE, __version__, config
from rewnd.camera import CaptureEngine, find_camera, list_cameras
from rewnd.platform_utils import KeepAwake, open_path, reveal
from rewnd.session import Session
from rewnd.ui.export_dialog import ExportDialog, ExportJob, Relay
from rewnd.ui.library_page import LibraryPage
from rewnd.ui.record_page import RecordPage
from rewnd.ui.widgets import Logo, Toast, label


class EngineBridge(QObject):
    """Engine callbacks fire on the capture thread; these signals hop them onto the UI thread."""
    status = Signal(str, str)
    shot = Signal(int, str)
    info = Signal(dict)
    error = Signal(str)


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

        root = QWidget()
        root.setObjectName("Root")
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
        self.library.export_requested.connect(self.open_export)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(lambda: config.save(self.cfg))
        self._housekeeping = QTimer(self)
        self._housekeeping.timeout.connect(self._tick)
        self._housekeeping.start(1000)

        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._space)
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.go(0))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self.go(1))

        self.engine.start()
        self._load_cameras()
        self.record.refresh_disk()

    # ---------- chrome ----------

    def _top_bar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(60)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(10)
        h.addWidget(Logo(22, self.accent))
        h.addWidget(label("rewnd", "Wordmark"))
        tag = label(TAGLINE, "Tagline")
        h.addSpacing(6)
        h.addWidget(tag)
        h.addStretch(1)
        self.tabs = QButtonGroup(self)
        for i, text in enumerate(("Record", "Library")):
            b = QPushButton(text)
            b.setObjectName("Tab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.tabs.addButton(b, i)
            h.addWidget(b)
        self.tabs.button(0).setChecked(True)
        self.tabs.idClicked.connect(self.go)
        h.addStretch(1)
        h.addWidget(label(f"v{__version__} · free & local", "Faint"))
        return bar

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
        self.toast.show_message("Heads up", msg, timeout=6000)

    def _on_status(self, state, msg):
        self.record.set_status(state, msg)
        if state == "reconnecting" and self.session is not None and msg:
            self.toast.show_message("Camera dropped", "Still recording. Shots resume as soon as it's back.",
                                    timeout=5000)

    # ---------- recording ----------

    def toggle_recording(self):
        if self.session is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        if self.engine.status != "live":
            self.toast.show_message("No camera yet", "Wait for the preview to show up, then hit record.",
                                    timeout=4000)
            return
        try:
            self.session = Session.create(self.cfg["capture"]["output_root"], self.cfg)
        except OSError as e:
            QMessageBox.critical(self, "Can't create session", f"Couldn't create the session folder:\n{e}")
            return
        config.save(self.cfg)
        self.engine.start_recording(self.session, self.cfg["capture"]["interval"],
                                    self.cfg["capture"].get("jpeg_quality", 92))
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
            self.toast.show_message("Nothing captured", "The session stopped before the first shot.", timeout=5000)
            return
        if self.cfg["capture"].get("auto_export", True):
            self.run_export(s)
        else:
            self.toast.show_message(f"Saved {n} frames", "Export it whenever you like from the Library.",
                                    actions=[("Export…", lambda: self.open_export(s))])

    def _on_shot(self, idx, _path):
        self.record.on_shot(idx)

    def _tick(self):
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
        self.toast.show_message("Making your video…", f"{n} frames", progress=0.0)

        def finished():
            if job in self.jobs:
                self.jobs.remove(job)
        job.relay = Relay(
            job,
            on_progress=lambda d, t: self.toast.show_message("Making your video…", f"{n} frames",
                                                             progress=d / max(1, t)),
            on_done=self._export_done,
            on_failed=lambda m: self.toast.show_message(
                "Export failed", m, actions=[("Try again…", lambda: self.open_export(session))]),
            on_finished=finished, parent=self)
        self.jobs.append(job)
        job.start()

    def _export_done(self, path):
        self.toast.show_message("Video ready", path.replace("\\", "/").rsplit("/", 1)[-1],
                                actions=[("Play", lambda: open_path(path)), ("Show in folder", lambda: reveal(path))],
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
