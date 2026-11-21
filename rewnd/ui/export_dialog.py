"""Export dialog: scrub through a session with the final look, tweak, render."""

import copy
import threading

import cv2
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QProgressBar, QPushButton, QScrollArea,
                               QSlider, QVBoxLayout, QWidget)

from rewnd import exporter, imaging
from rewnd.platform_utils import open_path, reveal
from rewnd.session import FRAME_RE
from rewnd.ui import forms
from rewnd.ui.widgets import GlassBackdrop, PreviewView, cv_to_qimage, label


class ExportJob(QObject):
    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, session, look, exp, ov):
        super().__init__()
        self.session = session
        self.look, self.exp, self.ov = copy.deepcopy(look), copy.deepcopy(exp), copy.deepcopy(ov)
        self.cancel = threading.Event()
        self.thread = QThread()
        self.moveToThread(self.thread)
        self.thread.started.connect(self.run)

    def start(self):
        self.thread.start()

    def run(self):
        try:
            out = exporter.export(self.session, self.look, self.exp, self.ov,
                                  progress=lambda d, t: self.progress.emit(d, t), cancel=self.cancel)
            self.done.emit(str(out))
        except exporter.ExportCancelled:
            self.failed.emit("cancelled")
        except Exception as e:  # surfaced to the user
            self.failed.emit(str(e))
        finally:
            self.thread.quit()


class Relay(QObject):
    """Lives on the UI thread. Job signals connected to its slots are queued onto the UI thread,
    so handlers can safely touch widgets (plain lambdas would run on the worker thread)."""

    def __init__(self, job, on_progress=None, on_done=None, on_failed=None, on_finished=None, parent=None):
        super().__init__(parent)
        self._p, self._d, self._f, self._fin = on_progress, on_done, on_failed, on_finished
        job.progress.connect(self.progress)
        job.done.connect(self.done)
        job.failed.connect(self.failed)
        job.thread.finished.connect(self.finished)

    @Slot(int, int)
    def progress(self, d, t):
        if self._p:
            self._p(d, t)

    @Slot(str)
    def done(self, path):
        if self._d:
            self._d(path)

    @Slot(str)
    def failed(self, msg):
        if self._f:
            self._f(msg)

    @Slot()
    def finished(self):
        if self._fin:
            self._fin()


class ExportDialog(QDialog):
    saved_defaults = Signal(dict, dict, dict)

    def __init__(self, session, defaults, accent, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"rewnd · export · {session.title.lower()}")
        self.resize(1180, 760)
        self.session = session
        self.accent = accent
        m = session.meta
        self.look = copy.deepcopy(m.get("look") or defaults["look"])
        self.exp = copy.deepcopy(m.get("export") or defaults["export"])
        self.ov = copy.deepcopy(m.get("overlay") or defaults["overlay"])
        self.frames = session.frames()
        self.stamps = session.timestamps()
        self.t0 = min(self.stamps.values()) if self.stamps else None
        self.job = None
        self._raw_cache = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.backdrop = GlassBackdrop()
        outer.addWidget(self.backdrop)
        root = QHBoxLayout(self.backdrop)
        root.setContentsMargins(22, 20, 18, 20)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(label(session.title.lower(), "H1"))
        self.meta_label = label("", "Mono")
        left.addWidget(self.meta_label)
        self.preview = PreviewView()
        self.preview.set_message("no frames in this session")
        left.addWidget(self.preview, 1)
        scrub = QHBoxLayout()
        self.scrub = QSlider(Qt.Horizontal)
        self.scrub.setRange(0, max(0, len(self.frames) - 1))
        self.scrub.setValue(max(0, len(self.frames) - 1))
        self.scrub.valueChanged.connect(lambda _: self._render())
        self.frame_label = label("", "Mono")
        scrub.addWidget(self.scrub, 1)
        scrub.addWidget(self.frame_label)
        left.addLayout(scrub)

        foot = QFrame()
        foot.setObjectName("Glass")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(10)
        row = QHBoxLayout()
        self.summary = label("", "Mono")
        row.addWidget(self.summary, 1)
        self.defaults_btn = QPushButton("use as my defaults")
        self.defaults_btn.setObjectName("Ghost")
        self.defaults_btn.clicked.connect(self._save_defaults)
        row.addWidget(self.defaults_btn)
        self.export_btn = QPushButton("export video")
        self.export_btn.setObjectName("Primary")
        self.export_btn.clicked.connect(self._export)
        row.addWidget(self.export_btn)
        fl.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(6)
        self.bar.hide()
        fl.addWidget(self.bar)
        self.result_row = QHBoxLayout()
        self.result_label = label("", "Mono")
        self.result_label.hide()
        self.result_row.addWidget(self.result_label, 1)
        self.play_btn = QPushButton("play")
        self.show_btn = QPushButton("show in folder")
        self.play_btn.hide()
        self.show_btn.hide()
        self.result_row.addWidget(self.play_btn)
        self.result_row.addWidget(self.show_btn)
        fl.addLayout(self.result_row)
        left.addWidget(foot)
        root.addLayout(left, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        panel = QWidget()
        panel.setObjectName("Panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(18, 2, 14, 18)
        pl.setSpacing(0)
        pl.addWidget(forms.framing_section(self.exp, self._changed, accent))
        pl.addWidget(forms.overlay_section(self.ov, self._changed, accent, expanded=False))
        pl.addWidget(forms.look_section(self.look, self._changed, accent, expanded=False))
        pl.addStretch(1)
        scroll.setWidget(panel)
        side = QFrame()
        side.setObjectName("SidePanel")
        side.setFixedWidth(368)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(scroll)
        root.addWidget(side)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._render)
        self._changed()
        from rewnd.ui.library_page import human_duration
        self.meta_label.setText(f"{len(self.frames)} frames · {human_duration(session.duration())} recorded")
        self.meta_label.setToolTip(str(session.path))

    def _changed(self):
        self.summary.setText(exporter.describe(len(self.frames), self.exp))
        self._debounce.start(60)

    def _raw(self, i):
        if i not in self._raw_cache:
            img = cv2.imread(str(self.frames[i]))
            if img is not None:
                h, w = img.shape[:2]
                k = 900 / min(w, h)
                if k < 1:
                    img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
            if len(self._raw_cache) > 24:
                self._raw_cache.pop(next(iter(self._raw_cache)))
            self._raw_cache[i] = img
        return self._raw_cache[i]

    def _render(self):
        if not self.frames:
            return
        i = self.scrub.value()
        raw = self._raw(i)
        if raw is None:
            return
        m = FRAME_RE.search(self.frames[i].name)
        ts = self.stamps.get(int(m.group(1))) if m else None
        elapsed = (ts - self.t0) if (ts and self.t0) else 0.0
        img = imaging.render(raw, self.look, self.exp, self.ov, elapsed=elapsed,
                             wall=imaging.wall_time(ts), size=720)
        self.preview.set_image(cv_to_qimage(img))
        self.frame_label.setText(f"{i + 1} / {len(self.frames)}")

    def _save_defaults(self):
        self.saved_defaults.emit(copy.deepcopy(self.look), copy.deepcopy(self.exp), copy.deepcopy(self.ov))
        self.defaults_btn.setText("saved as defaults ✓")
        QTimer.singleShot(1800, lambda: self.defaults_btn.setText("use as my defaults"))

    def _export(self):
        if self.job is not None:
            self.job.cancel.set()
            return
        self.session.meta.update(look=copy.deepcopy(self.look), export=copy.deepcopy(self.exp),
                                 overlay=copy.deepcopy(self.ov))
        try:
            self.session.save_meta()
        except Exception:
            pass
        self.job = ExportJob(self.session, self.look, self.exp, self.ov)
        self.relay = Relay(self.job, on_progress=lambda d, t: self.bar.setValue(int(1000 * d / max(1, t))),
                           on_done=self._done, on_failed=self._failed, parent=self)
        self.bar.setValue(0)
        self.bar.show()
        self.play_btn.hide()
        self.show_btn.hide()
        self.result_label.setText("rendering…")
        self.result_label.show()
        self.export_btn.setText("cancel")
        self.export_btn.setObjectName("")
        self.export_btn.setStyle(self.export_btn.style())
        self.job.start()

    def _reset_button(self):
        self.job = None
        self.export_btn.setText("export video")
        self.export_btn.setObjectName("Primary")
        self.export_btn.setStyle(self.export_btn.style())

    def _done(self, path):
        self._reset_button()
        self.bar.setValue(1000)
        self.result_label.setText(f"saved {path.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]}")
        for b in (self.play_btn, self.show_btn):
            try:
                b.clicked.disconnect()
            except Exception:
                pass
        self.play_btn.clicked.connect(lambda: open_path(path))
        self.show_btn.clicked.connect(lambda: reveal(path))
        self.play_btn.show()
        self.show_btn.show()

    def _failed(self, msg):
        self._reset_button()
        self.bar.hide()
        self.result_label.setText("export cancelled." if msg == "cancelled" else f"export failed: {msg}")

    def closeEvent(self, e):
        if self.job is not None:
            self.job.cancel.set()
            self.job.thread.wait(3000)
        super().closeEvent(e)
