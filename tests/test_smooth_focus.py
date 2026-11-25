import time

import cv2
import numpy as np
import pytest

from rewnd import camera, config, exporter, focus, v4l2
from tests.test_core import make_session, probe


# ---------- smooth motion ----------

def test_smooth_off_matches_plan():
    exp = dict(config.defaults()["export"], smooth="off", target_seconds=2, fps=30)
    assert [s[0][0] for s in exporter.plan_samples(200, exp)] == exporter.plan(200, exp)
    assert all(len(s) == 1 and s[0][1] == 1.0 for s in exporter.plan_samples(200, exp))


@pytest.mark.parametrize("n", [20, 60, 61, 500])
@pytest.mark.parametrize("smooth", ["light", "strong"])
def test_smooth_weights_are_normalised_and_local(n, smooth):
    exp = dict(config.defaults()["export"], smooth=smooth, target_seconds=2, fps=30)
    samples = exporter.plan_samples(n, exp)
    assert len(samples) == 60
    step = n / 60
    for k, smp in enumerate(samples):
        assert sum(w for _, w in smp) == pytest.approx(1.0, abs=1e-3)
        assert len(smp) <= exporter.MAX_BLEND
        centre = max(smp, key=lambda p: p[1])[0]
        assert abs(centre - k * step) <= 1.0
        assert all(0 <= i < n for i, _ in smp)


def test_smooth_blends_neighbours_in_every_frame_mode():
    exp = dict(config.defaults()["export"], smooth="strong", length_mode="fps")
    smp = exporter.plan_samples(10, exp)
    assert len(smp) == 10
    mid = dict(smp[5])
    assert set(mid) == {4, 5, 6} and mid[5] > mid[4]


def test_slow_video_crossfades_instead_of_holding():
    # 10 shots stretched to 60 frames: frames between two shots mix both
    exp = dict(config.defaults()["export"], smooth="light", target_seconds=2, fps=30)
    smp = exporter.plan_samples(10, exp)
    mixed = [s for s in smp if len(s) > 1]
    assert len(mixed) > 20


def test_blend_averages():
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.full((4, 4, 3), 200, np.uint8)
    assert int(exporter.blend([a, b], [0.5, 0.5])[0, 0, 0]) == 100


def test_export_with_smooth_motion(tmp_path):
    s, cfg = make_session(tmp_path, n=90)
    exp = dict(cfg["export"], smooth="strong", target_seconds=1, fps=30, hold_end=0, size=360)
    out = exporter.export(s, cfg["look"], exp, cfg["overlay"])
    dur, (w, h) = probe(out)
    assert (w, h) == (640, 360) and 0.9 < dur < 1.2
    assert "smooth motion" in exporter.describe(90, exp)


# ---------- focus ----------

def test_focus_positions_round_trip():
    f = focus.FakeFocus(camera.FakeCapture(64, 36))
    for pos in (0, 25, 50, 100):
        assert f.to_pos(f.from_pos(pos)) == pytest.approx(pos, abs=0.5)


def test_v4l2_focus_turns_autofocus_off_first():
    class Ctrls:
        def __init__(self):
            self.sets = []

        def set(self, values):
            self.sets.append(list(values.items()))

        def controls(self):
            return {"focus_absolute": {"value": 30}}

    c = Ctrls()
    f = focus.V4l2Focus(c, {"focus_absolute": {"min": 0, "max": 250, "step": 5},
                            "focus_automatic_continuous": {"min": 0, "max": 1}})
    assert f.write(133) == 130
    assert c.sets[-1] == [("focus_automatic_continuous", 0), ("focus_absolute", 130)]
    assert f.read() == 30
    assert "focus_absolute" in v4l2.CONTROL_IDS


def _run(monkeypatch, cam_cfg, body):
    monkeypatch.setattr(camera, "FAKE", True)
    infos, errors = [], []
    eng = camera.CaptureEngine(on_info=infos.append, on_error=errors.append)
    cfg = dict(config.defaults()["camera"], **cam_cfg)
    eng.configure(camera.CameraInfo(id="fake:0", index=0, name="Demo"), "640x360", cfg)
    eng.start()
    try:
        time.sleep(1.2)
        body(eng, infos)
        assert not errors
    finally:
        eng.shutdown()
        eng.join(timeout=3)


def test_engine_autofocus_off_locks_current_focus(monkeypatch):
    def body(eng, infos):
        assert eng._cap.autofocus is False
        assert eng._cap.focus == 140            # locked where it was, nothing jumped
        f = [i["focus"] for i in infos if "focus" in i][-1]
        assert f["available"] and f["pos"] == pytest.approx(eng._focus.to_pos(140))
    _run(monkeypatch, {"autofocus": False, "focus": None}, body)


def test_engine_manual_focus_slider(monkeypatch):
    def sharpness(img):
        return cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()

    def body(eng, infos):
        sharp = sharpness(eng.latest()[0])
        eng.set_focus_pos(0)
        time.sleep(0.5)
        assert eng._cap.focus == 0
        assert sharpness(eng.latest()[0]) < sharp * 0.5
    _run(monkeypatch, {"autofocus": False, "focus": eng_pos_for(140)}, body)


def eng_pos_for(raw):
    return focus.FakeFocus(camera.FakeCapture(8, 8)).to_pos(raw)


def test_engine_autofocus_on(monkeypatch):
    def body(eng, infos):
        assert eng._cap.autofocus is True
    _run(monkeypatch, {"autofocus": True}, body)


def test_config_bumps_old_jpeg_quality(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"capture": {"jpeg_quality": 92}}')
    cfg = config.load(p)
    assert cfg["capture"]["jpeg_quality"] == 95
    assert cfg["export"]["smooth"] == "off" and cfg["camera"]["focus"] is None
