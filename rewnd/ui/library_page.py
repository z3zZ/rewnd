"""Library: every past session as a card, with export, play, rename and delete."""

import cv2
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QInputDialog, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from rewnd import imaging
from rewnd.platform_utils import open_path
from rewnd.session import list_sessions
from rewnd.ui import theme
from rewnd.ui.widgets import FlowLayout, cv_to_qimage, label


class Thumb(QWidget):
    def __init__(self, qimg=None):
        super().__init__()
        self.img = qimg
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int(w * 9 / 16)

    def resizeEvent(self, e):
        self.setFixedHeight(self.heightForWidth(self.width()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.setClipPath(path)
        p.fillRect(r, theme.qcolor(theme.SURFACE_2))
        if self.img is not None:
            iw, ih = self.img.width(), self.img.height()
            k = max(r.width() / iw, r.height() / ih)
            w, h = iw * k, ih * k
            p.drawImage(QRectF((r.width() - w) / 2, (r.height() - h) / 2, w, h), self.img)
        else:
            p.setPen(theme.qcolor(theme.FAINT))
            p.drawText(r, Qt.AlignCenter, "no frames")


def human_duration(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m = rem // 60
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m"
    return f"{sec}s"


class SessionCard(QFrame):
    export_clicked = Signal(object)
    changed = Signal()

    def __init__(self, session):
        super().__init__()
        self.setObjectName("Card")
        self.session = session
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        frames = session.frames()
        qimg = None
        if frames:
            raw = cv2.imread(str(frames[-1]))
            if raw is not None:
                h, w = raw.shape[:2]
                k = 360 / min(w, h)
                if k < 1:
                    raw = cv2.resize(raw, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
                look = session.meta.get("look") or {}
                raw = imaging.adjust(imaging.orient(raw, look), look)
                qimg = cv_to_qimage(raw)
        self.thumb = Thumb(qimg)
        v.addWidget(self.thumb)

        title_row = QHBoxLayout()
        self.title = label(session.title.lower(), "H2")
        title_row.addWidget(self.title, 1)
        videos = session.videos()
        if videos:
            badge = label(f"{len(videos)} video{'s' if len(videos) > 1 else ''}", "Mono")
            title_row.addWidget(badge)
        v.addLayout(title_row)

        interval = session.meta.get("interval")
        bits = [f"{len(frames)} frames", human_duration(session.duration())]
        if interval:
            bits.append(f"every {interval:g}s")
        v.addWidget(label(" · ".join(bits), "Mono"))

        actions = QHBoxLayout()
        actions.setSpacing(6)
        exp = QPushButton("export…")
        exp.setObjectName("Primary")
        exp.setEnabled(bool(frames))
        exp.clicked.connect(lambda: self.export_clicked.emit(self.session))
        actions.addWidget(exp)
        if videos:
            play = QPushButton("play")
            play.clicked.connect(lambda: open_path(videos[0]))
            actions.addWidget(play)
        actions.addStretch(1)
        more = QPushButton("•••")
        more.setObjectName("Ghost")
        menu = QMenu(more)
        menu.addAction("open folder", lambda: open_path(session.path))
        menu.addAction("rename…", self._rename)
        menu.addSeparator()
        menu.addAction("delete session…", self._delete)
        more.setMenu(menu)
        more.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0; }")
        actions.addWidget(more)
        v.addLayout(actions)

    def sizeHint(self):
        return QSize(300, 300)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int((w - 24) * 9 / 16) + 128

    def _rename(self):
        text, ok = QInputDialog.getText(self, "Rename session", "Name", text=self.title.text())
        if ok and text.strip():
            self.session.meta["title"] = text.strip()
            self.session.save_meta()
            self.title.setText(text.strip())

    def _delete(self):
        n = len(self.session.frames())
        r = QMessageBox.question(self, "Delete session",
                                 f"Delete this session and its {n} frames and videos? This can't be undone.")
        if r == QMessageBox.Yes:
            self.session.delete()
            self.changed.emit()


class LibraryPage(QWidget):
    export_requested = Signal(object)

    def __init__(self, cfg):
        super().__init__()
        self.setObjectName("Page")
        self.cfg = cfg
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label("library", "H1"))
        self.sub = label("", "Muted")
        col.addWidget(self.sub)
        head.addLayout(col, 1)
        open_btn = QPushButton("open folder")
        open_btn.clicked.connect(lambda: open_path(self.cfg["capture"]["output_root"]))
        head.addWidget(open_btn)
        refresh = QPushButton("refresh")
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("Panel")
        self.flow = FlowLayout(self.grid_host, spacing=16)
        self.scroll.setWidget(self.grid_host)
        root.addWidget(self.scroll, 1)
        self.empty = label("no sessions yet. hit record and they'll show up here.", "Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty)

    def refresh(self):
        while self.flow.count():
            item = self.flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        sessions = list_sessions(self.cfg["capture"]["output_root"])
        for s in sessions:
            card = SessionCard(s)
            card.export_clicked.connect(self.export_requested)
            card.changed.connect(self.refresh)
            self.flow.addWidget(card)
        n = len(sessions)
        self.sub.setText(f"{n} session{'s' if n != 1 else ''} · {self.cfg['capture']['output_root']}")
        self.empty.setVisible(n == 0)
        self.scroll.setVisible(n > 0)
        self.grid_host.updateGeometry()
