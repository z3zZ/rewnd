"""Drives the real window offscreen with the demo camera: record, stop, auto-export, library, export dialog."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def test_record_export_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from rewnd import camera, config
    monkeypatch.setattr(camera, "FAKE", True)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rewnd.ui import theme
    theme.load_fonts()
    app.setStyleSheet(theme.stylesheet())
    from rewnd.ui.main_window import MainWindow
    from rewnd.ui.export_dialog import ExportDialog
    from rewnd.session import list_sessions

    cfg = config.defaults()
    cfg["capture"]["output_root"] = str(tmp_path / "out")
    cfg["capture"]["interval"] = 0.4
    cfg["export"].update(target_seconds=2, aspect="9:16", size=480)
    w = MainWindow(cfg)
    w.show()

    def wait(cond, secs):
        end = time.time() + secs
        while time.time() < end and not cond():
            app.processEvents()
            time.sleep(0.02)
        return cond()

    assert wait(lambda: w.engine.status == "live", 10)
    w.start_recording()
    assert w.session is not None
    assert wait(lambda: len(w.engine.session.frames()) >= 3 if w.engine.session else False, 10)
    w.stop_recording()
    assert wait(lambda: not w.jobs, 60)
    (s,) = list_sessions(cfg["capture"]["output_root"])
    assert [p.name for p in s.videos()] == ["rewnd_9x16_2s.mp4"]
    assert s.meta["ended"] and s.meta["export"]["aspect"] == "9:16"

    w.go(1)
    app.processEvents()
    assert w.library.flow.count() == 1
    d = ExportDialog(s, cfg, cfg["ui"]["accent"], w)
    d.show()
    app.processEvents()
    assert wait(lambda: d.preview.image is not None, 3)
    assert d.summary.text().startswith("3.5s")  # 2s fitted length + 1.5s hold on the last frame
    d.close()
    w.close()
    assert config.CONFIG_PATH.exists()
