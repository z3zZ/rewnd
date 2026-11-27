import json
import time

import cv2
import numpy as np
import pytest

from rewnd import camera, config, exposure, v4l2


def scene(ev, w=320, h=180):
    """Dark room with a bright monitor, like a desk at night."""
    room = np.full((h, w, 3), 60.0, np.float32)
    room[20:80, 180:300] = 700.0
    text = np.zeros((h, w), np.uint8)
    cv2.putText(text, "txt", (200, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, 255, 2)
    room[text > 0] = 400.0
    return np.clip(room * 2.0 ** ev, 0, 255).astype(np.uint8)


def test_measure_reports_clipping():
    s = exposure.measure(scene(0))
    assert s["clip"] > 0.1
    assert exposure.measure(scene(-3))["clip"] < 0.01


def test_guard_steps_down_holds_and_steps_up():
    g = exposure.HighlightGuard()
    assert g.step({"clip": 0.12, "p99": 255, "median": 60}) < 0
    assert g.step({"clip": 0.01, "p99": 250, "median": 60}) == 0          # in the dead band
    assert g.step({"clip": 0.0, "p99": 120, "median": 30}) > 0
    # already near black: don't darken further to save a tiny highlight
    assert g.step({"clip": 0.05, "p99": 255, "median": 8}) == 0
    # smaller steps while recording, so the timelapse doesn't flicker
    assert abs(g.step({"clip": 0.12, "p99": 255, "median": 60}, recording=True)) < \
        abs(g.step({"clip": 0.12, "p99": 255, "median": 60}))


def test_guard_converges_on_scene():
    g = exposure.HighlightGuard()
    ev = 0.0
    for _ in range(40):
        ev += g.step(exposure.measure(scene(ev)))
    assert exposure.measure(scene(ev))["clip"] <= g.CLIP_HIGH
    assert ev < -1


def test_fuse_keeps_screen_detail_and_room():
    dark, mid, bright = scene(-3), scene(0), scene(1.5)
    out = exposure.fuse([mid, dark, bright])
    assert out.shape == mid.shape and out.dtype == np.uint8
    screen = out[25:75, 185:295]
    assert (screen >= 250).mean() < (mid[25:75, 185:295] >= 250).mean()   # screen recovered
    assert out[120:170, 10:150].mean() > dark[120:170, 10:150].mean()     # room not crushed


def test_bracket_offsets_and_clamping():
    c = exposure.FakeExposure(camera.FakeCapture(64, 36))   # -13..-3 stops, like a Windows webcam
    assert exposure.bracket(-7.0, "protect", c) == [-7.0, -5.5, -4.0]
    assert exposure.bracket(-7.0, "manual", c) == [-9.0, -7.0, -5.5]
    # scheduled brackets around the schedule's value, like manual (mid-ease values included)
    assert exposure.bracket(-6.3, "scheduled", c) == pytest.approx([-8.3, -6.3, -4.8])
    # near the top of the range, duplicates collapse
    assert exposure.bracket(-3.2, "protect", c) == [-3.2]


def test_slider_position_round_trip():
    c = exposure.FakeExposure(camera.FakeCapture(64, 36))
    for ev in (-13, -8.5, -7, -3):
        assert c.from_pos(c.to_pos(ev)) == pytest.approx(ev)
    assert c.to_pos(99) == 100


def test_config_migrates_old_auto_exposure(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"camera": {"auto_exposure": False}}))
    cfg = config.load(p)
    assert cfg["camera"]["exposure_mode"] == "manual" and "auto_exposure" not in cfg["camera"]
    p.write_text(json.dumps({"camera": {"auto_exposure": True}}))
    assert config.load(p)["camera"]["exposure_mode"] == "protect"
    assert config.defaults()["camera"]["exposure_mode"] == "protect"


def test_v4l2ctl_parses_new_and_legacy_names(monkeypatch):
    out = """
User Controls

                     brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=0
                           gain 0x00980913 (int)    : min=0 max=100 step=1 default=0 value=0

Camera Controls

                  exposure_auto 0x009a0901 (menu)   : min=0 max=3 default=3 value=3
              exposure_absolute 0x009a0902 (int)    : min=3 max=2047 step=1 default=250 value=250 flags=inactive
"""
    c = v4l2.V4l2CtlControls("/dev/video0")
    calls = []

    def fake_run(args):   # a camera that takes what it's given
        nonlocal out
        calls.append(args)
        if args[0] == "--set-ctrl" and args[1].startswith("exposure_absolute="):
            out = out.replace("value=250", "value=" + args[1].split("=")[1])
        return out if args == ["--list-ctrls"] else ""
    monkeypatch.setattr(c, "_run", fake_run)
    ctrls = c.controls()
    assert ctrls["exposure_time_absolute"]["max"] == 2047
    assert ctrls["auto_exposure"]["value"] == 3
    e = exposure.V4l2Exposure(c, ctrls)
    e.write(e.ev_max + 5)
    assert ["--set-ctrl", "exposure_auto=1"] in calls
    assert ["--set-ctrl", "exposure_absolute=2047"] in calls


def run_engine(monkeypatch, cam_cfg, seconds, record=None):
    monkeypatch.setattr(camera, "FAKE", True)
    errors = []
    eng = camera.CaptureEngine(on_error=errors.append)
    cfg = dict(config.defaults()["camera"], **cam_cfg)
    eng.configure(camera.CameraInfo(id="fake:0", index=0, name="Demo"), "1280x720", cfg)
    eng.start()
    try:
        if record:
            time.sleep(3)    # let protect settle before the first shot
            record(eng)
        time.sleep(seconds)
        frame, _ = eng.latest()
        eng.final_ev = eng._ev
        return eng, frame, errors
    finally:
        eng.shutdown()
        eng.join(timeout=3)


def test_engine_protect_stops_screen_clipping(monkeypatch):
    eng, frame, errors = run_engine(monkeypatch, {"exposure_mode": "protect"}, 6)
    assert not errors
    assert eng.final_ev is not None and eng.final_ev < exposure.FakeExposure.NEUTRAL   # came down
    assert exposure.measure(frame)["clip"] <= exposure.HighlightGuard.CLIP_HIGH * 1.5


def test_engine_auto_leaves_screen_clipped(monkeypatch):
    eng, frame, _ = run_engine(monkeypatch, {"exposure_mode": "auto"}, 1.5)
    assert eng.final_ev is None
    assert exposure.measure(frame)["clip"] > 0.05


def test_engine_manual_sets_and_moves_exposure(monkeypatch):
    infos = []
    monkeypatch.setattr(camera, "FAKE", True)
    eng = camera.CaptureEngine(on_info=infos.append)
    cfg = dict(config.defaults()["camera"], exposure_mode="manual", exposure=20.0)
    eng.configure(camera.CameraInfo(id="fake:0", index=0, name="Demo"), "640x360", cfg)
    eng.start()
    try:
        time.sleep(1.2)
        assert eng._ev == pytest.approx(eng._exp.from_pos(20))
        eng.set_exposure_pos(80)
        time.sleep(0.5)
        assert eng._ev == pytest.approx(eng._exp.from_pos(80))
        assert any(i.get("exposure", {}).get("mode") == "manual" for i in infos)
    finally:
        eng.shutdown()
        eng.join(timeout=3)


def test_engine_hdr_frames_keep_screen(monkeypatch, tmp_path):
    from rewnd.session import Session
    cfg = config.defaults()
    cfg["capture"]["output_root"] = str(tmp_path)
    sess = Session.create(tmp_path, cfg)
    shots = []

    def start(eng):
        eng.on_shot = lambda i, p: shots.append(p)
        eng.start_recording(sess, 3.0)
    eng, _, errors = run_engine(monkeypatch, {"exposure_mode": "protect", "hdr": True}, 7, record=start)
    eng.stop_recording()
    assert not errors and shots
    img = cv2.imread(str(shots[-1]))
    assert exposure.measure(img)["clip"] < 0.05
