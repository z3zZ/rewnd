import os
import time

import pytest

from rewnd import updates

REL = updates.Release(version="1.0.2", tag="v1.0.2", url="https://github.com/z3zZ/rewnd/releases/tag/v1.0.2",
                      name="rewnd v1.0.2", summary="Faster exports.")


@pytest.fixture
def online(monkeypatch):
    monkeypatch.delenv(updates.DISABLE_ENV, raising=False)


def test_version_ordering():
    assert updates.is_newer("v1.0.1", "1.0.0")
    assert updates.is_newer("1.0.10", "1.0.9")          # numeric, not alphabetical
    assert updates.is_newer("2.0", "1.9.9")
    assert not updates.is_newer("1.0", "1.0.0")         # same version, different spelling
    assert not updates.is_newer("1.0.0", "1.0.1")
    assert not updates.is_newer("1.1.0-beta", "1.1.0")
    assert not updates.is_newer("nonsense", "1.0.0")


def test_check_finds_newer_and_respects_skip(online):
    assert updates.check("z3zZ/rewnd", "1.0.1", fetch=lambda r: REL) is REL
    assert updates.check("z3zZ/rewnd", "1.0.2", fetch=lambda r: REL) is None
    assert updates.check("z3zZ/rewnd", "1.0.1", skip="1.0.2", fetch=lambda r: REL) is None
    assert updates.check("z3zZ/rewnd", "1.0.1", skip="1.0.1", fetch=lambda r: REL) is REL
    assert updates.check("z3zZ/rewnd", "1.0.1", fetch=lambda r: None) is None   # offline


def test_check_can_be_disabled(monkeypatch):
    monkeypatch.setenv(updates.DISABLE_ENV, "1")
    assert updates.check("z3zZ/rewnd", "0.1", fetch=lambda r: REL) is None


def test_fetch_never_raises_offline(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    assert updates.fetch_latest("z3zZ/rewnd") is None


def test_summary_from_release_notes():
    notes = "**Hotfix: bright screens no longer blow out.**\n\n## Download\n- Windows: zip"
    assert updates._plain(notes) == "Hotfix: bright screens no longer blow out."
    assert updates._plain("## Only headings\n- and lists") == ""


def test_window_shows_notice_and_skip(tmp_path, monkeypatch, online):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from rewnd import camera, config
    monkeypatch.setattr(camera, "FAKE", True)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(updates, "fetch_latest", lambda repo, timeout=6.0: REL)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rewnd.ui.main_window import MainWindow
    opened = []
    monkeypatch.setattr("rewnd.ui.main_window.QDesktopServices.openUrl", lambda u: opened.append(u.toString()))

    cfg = config.defaults()
    cfg["capture"]["output_root"] = str(tmp_path / "out")
    w = MainWindow(cfg)
    w.show()
    try:
        w.check_for_updates()
        t = time.time()
        while w.release is None and time.time() - t < 5:
            app.processEvents()
            time.sleep(0.02)
        assert w.release is REL
        assert w.update_btn.isVisible() and "1.0.2" in w.update_btn.text()
        assert w.toast.isVisible() and "1.0.2" in w.toast.title.text()

        w.update_btn.click()
        assert opened == [REL.url]

        w._skip_update(REL)
        assert cfg["ui"]["skip_version"] == "1.0.2" and not w.update_btn.isVisible()
    finally:
        w.engine.shutdown()
        w.close()
