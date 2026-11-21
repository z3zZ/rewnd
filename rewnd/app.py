"""Entry point."""

import sys

from PySide6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor, QPolygonF
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from rewnd import APP_NAME, config
from rewnd.ui import theme


def app_icon(accent):
    """Dark rounded tile with the lime round-stroke chevrons (same mark as the top bar)."""
    from PySide6.QtGui import QPen
    pm = QPixmap(256, 256)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#000000"))
    p.drawRoundedRect(0, 0, 256, 256, 56, 56)
    pen = QPen(QColor(accent), 26)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s, cy = 46, 128
    for i in range(2):
        x = 62 + i * 70
        p.drawPolyline(QPolygonF([QPointF(x + s, cy - s), QPointF(x, cy), QPointF(x + s, cy + s)]))
    p.end()
    return QIcon(pm)


def install_crash_handler():
    """pythonw has no console, so uncaught errors are written to a log and shown in a dialog."""
    import traceback
    log = config.config_dir() / "crash.log"

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass
        sys.__stderr__ and sys.__stderr__.write(text)
        try:
            from PySide6.QtWidgets import QMessageBox
            if QApplication.instance():
                QMessageBox.critical(None, "rewnd hit a problem",
                                     f"{value}\n\nDetails were saved to:\n{log}")
        except Exception:
            pass
    sys.excepthook = hook


def main():
    install_crash_handler()
    if sys.platform.startswith("win"):
        try:  # own taskbar icon instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("z3zZ.rewnd")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("rewnd")
    theme.load_fonts()
    app.setFont(QFont("Inter", 10))
    cfg = config.load()
    theme.apply(cfg["ui"].get("theme", "black"), cfg["ui"].get("accent", theme.DEFAULT_ACCENT))
    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(app_icon(cfg["ui"]["accent"]))

    from rewnd.ui.main_window import MainWindow
    win = MainWindow(cfg)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
