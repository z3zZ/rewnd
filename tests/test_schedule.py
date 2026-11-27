"""The "scheduled" exposure mode: profile ranges, night fallback, sunrise/sunset, easing, the engine,
unit migration from v1.1.0, failure reporting, location gating."""
import json
import math
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from rewnd import camera, config, exposure, imaging, schedule
from rewnd.session import Session

FIXTURES = Path(__file__).parent / "fixtures"


def sched(**kw):
    cfg = config.defaults()["exposure_schedule"]
    cfg.update(**kw)
    return schedule.clean(cfg)


def at(h, m=0, d=(2026, 11, 27)):
    return datetime(*d, h, m).timestamp()


# ---------- schedule logic ----------

def test_fixed_ranges_and_night_fallback():
    cfg = sched(profiles=[{"name": "day", "start": "08:00", "end": "16:00", "value": -7},
                          {"name": "late", "start": "22:00", "end": "02:00", "value": -3}],
                night_value=-4)
    assert schedule.fixed_slot_at(cfg, at(8)).name == "day"
    assert schedule.fixed_slot_at(cfg, at(15, 59)).name == "day"
    assert schedule.fixed_slot_at(cfg, at(16)).name == "night"       # end is exclusive
    assert schedule.fixed_slot_at(cfg, at(23)).name == "late"        # wraps past midnight
    assert schedule.fixed_slot_at(cfg, at(1, 30)).name == "late"
    assert schedule.fixed_slot_at(cfg, at(3)).value == -4
    p = schedule.plan_at(cfg, at(12))
    assert p.slot.name == "day" and p.next_at == at(16) and p.next_slot.name == "night"
    p = schedule.plan_at(cfg, at(17))
    assert p.next_at == at(22) and p.next_slot.name == "late"
    p = schedule.plan_at(cfg, at(23))
    assert p.next_at == at(2, d=(2026, 11, 28)) and p.next_slot.name == "night"


def test_no_profiles_means_always_night():
    p = schedule.plan_at(sched(profiles=[]), at(12))
    assert p.slot.key == "night" and p.next_at is None


def test_clean_repairs_bad_settings():
    cfg = schedule.clean({"mode": "bogus", "ease_minutes": "x", "night_value": 99,
                          "profiles": [{"name": "", "start": "25:00", "end": "9:30", "value": "nope"}, "junk"],
                          "location": {"lat": "51.5", "lon": 500}})
    assert cfg["mode"] == "fixed" and cfg["ease_minutes"] == 25 and cfg["night_value"] == schedule.MAX_STOPS
    (p,) = cfg["profiles"]
    assert p == {"name": "profile 1", "start": "08:00", "end": "09:30", "value": schedule.DEFAULT_DAY}
    assert cfg["location"]["lat"] == 51.5 and cfg["location"]["lon"] is None


def test_sunrise_sunset_matches_published_times():
    # London, 21 June 2025: sunrise 03:43 UTC, sunset 20:21 UTC
    rise, sset, polar = schedule.sun_times(51.5074, -0.1278, date(2025, 6, 21))
    r, s = (datetime.fromtimestamp(t, timezone.utc) for t in (rise, sset))
    assert polar is None
    assert abs((r.hour * 60 + r.minute) - (3 * 60 + 43)) <= 2
    assert abs((s.hour * 60 + s.minute) - (20 * 60 + 21)) <= 2
    # Sydney, 21 Dec 2025: sunrise 18:41 UTC the day before (05:41 AEDT), sunset 09:05 UTC (20:05 AEDT)
    rise, sset, _ = schedule.sun_times(-33.8688, 151.2093, date(2025, 12, 21))
    r, s = (datetime.fromtimestamp(t, timezone.utc) for t in (rise, sset))
    assert abs((r.hour * 60 + r.minute) - (18 * 60 + 41)) <= 3 and r.day == 20
    assert abs((s.hour * 60 + s.minute) - (9 * 60 + 5)) <= 3
    # Tromsø: no sunrise in mid December, no sunset in mid June
    assert schedule.sun_times(69.65, 18.96, date(2025, 12, 15))[2] == "night"
    assert schedule.sun_times(69.65, 18.96, date(2025, 6, 15))[2] == "day"


def test_sun_mode_switches_at_sunrise_and_sunset():
    cfg = sched(mode="sun", location={"source": "manual", "lat": 51.5074, "lon": -0.1278, "label": "London"})
    p = schedule.plan_at(cfg, datetime(2025, 6, 21, 12, tzinfo=timezone.utc).timestamp())
    assert p.slot.name == "day" and p.next_slot.name == "night"
    s = datetime.fromtimestamp(p.next_at, timezone.utc)
    assert (s.hour, s.minute // 10) == (20, 2)
    p = schedule.plan_at(cfg, datetime(2025, 6, 21, 23, 30, tzinfo=timezone.utc).timestamp())
    assert p.slot.name == "night" and p.next_slot.name == "day"
    cfg["location"].update(lat=78.22, lon=15.65)                      # polar night
    p = schedule.plan_at(cfg, datetime(2025, 12, 21, 12, tzinfo=timezone.utc).timestamp())
    assert p.slot.name == "night" and p.next_at is None


def test_sun_mode_without_location_falls_back_to_fixed_times():
    p = schedule.plan_at(sched(mode="sun"), at(12))
    assert p.slot.name == "day" and "no location" in p.note


def test_easing_is_gradual_and_restarts_from_where_it_is():
    e = schedule.Easer()
    assert e.update(-7, 0, 1500) == -7
    assert e.update(-4, 100, 1500) == -7
    assert e.update(-4, 850, 1500) == pytest.approx(-5.5)          # halfway in time is halfway in stops
    assert -7 < e.update(-4, 475, 1500) < -6                       # S-curve: slow at the start
    vals = [e.update(-4, 100 + t, 1500) for t in range(0, 1501, 10)]
    assert max(abs(a - b) for a, b in zip(vals, vals[1:])) < 0.03  # no visible step between shots
    assert e.update(-4, 1601, 1500) == -4 and not e.easing
    e.update(-7, 2000, 1500)
    v = e.update(-7, 2300, 1500)
    e.update(-2, 2300, 1500)                                       # switch again mid-ramp
    assert e.update(-2, 2300, 1500) == pytest.approx(v)
    assert e.update(-5, 5000, 1500, ease=False) == -5              # not recording: straight there


def test_schedule_eases_only_while_recording():
    cfg = sched(profiles=[{"name": "day", "start": "08:00", "end": "16:00", "value": -7}], night_value=-4)
    s = schedule.Schedule(cfg)
    st = s.update(at(15, 59), recording=True)
    assert st["name"] == "day" and st["value"] == -7 and st["next_name"] == "night"
    st = s.update(at(16), recording=True)
    assert st["name"] == "night" and st["easing"] and st["ease_from"] == "day" and st["value"] == -7
    assert -6 < s.update(at(16, 12) + 30, recording=True)["value"] < -5
    assert s.easer.ends_at() == pytest.approx(at(16, 25))
    st = s.update(at(16, 26), recording=True)
    assert st["value"] == -4 and not st["easing"]
    idle = schedule.Schedule(cfg)
    idle.update(at(15, 59), recording=False)
    assert idle.update(at(16), recording=False)["value"] == -4


def test_places():
    assert schedule.parse_place("51.5, -0.12")[:2] == (51.5, -0.12)
    assert schedule.parse_place("33.87S 151.21E")[:2] == (-33.87, 151.21)
    lat, lon, label = schedule.parse_place("london")
    assert abs(lat - 51.5) < 0.1 and abs(lon + 0.12) < 0.1 and "UK" in label
    assert schedule.parse_place("New York, USA")[2].startswith("New York")
    assert schedule.parse_place("São Paulo")[0] < -23
    assert schedule.parse_place("Manchester")[0] > 53
    assert schedule.parse_place("nowhere-ville") is None
    assert schedule.parse_place("95, 10") is None
    assert schedule.shutter_text(-6) == "1/64 s" and schedule.shutter_text(0) == "1 s"


def test_works_without_tzdata(monkeypatch):
    """If an offline update left tzdata uninstalled, places still work from the built-in list."""
    import importlib.resources

    def missing(name):
        raise ModuleNotFoundError(name)
    monkeypatch.setattr(importlib.resources, "files", missing)
    schedule.cities.cache_clear()
    try:
        assert schedule.parse_place("Manchester")[0] > 53
        assert schedule.parse_place("Reykjavik") is None
    finally:
        schedule.cities.cache_clear()


# ---------- units: stops on every platform ----------

class FakeCtrls:
    """A V4L2 control backend that takes what it's given (or ignores exposure, if refuse=True)."""

    def __init__(self, lo, hi, refuse=False):
        self.vals = {"auto_exposure": 3, "exposure_time_absolute": (lo + hi) // 2}
        self.lo, self.hi, self.refuse, self.sent = lo, hi, refuse, []

    def controls(self):
        return {"auto_exposure": {"min": 0, "max": 3, "value": self.vals["auto_exposure"]},
                "exposure_time_absolute": {"min": self.lo, "max": self.hi,
                                           "value": self.vals["exposure_time_absolute"]}}

    def set(self, values):
        self.sent.append(dict(values))
        for k, v in values.items():
            if not (self.refuse and k == "exposure_time_absolute"):
                self.vals[k] = v


def test_linux_exposure_is_in_real_stops():
    c = exposure.V4l2Exposure(FakeCtrls(3, 2047), FakeCtrls(3, 2047).controls())
    assert c.ev_min == pytest.approx(math.log2(3e-4)) and c.ev_max == pytest.approx(math.log2(0.2047))
    ctl = FakeCtrls(1, 10000)
    c = exposure.V4l2Exposure(ctl, ctl.controls())
    assert c.write(-6) == pytest.approx(math.log2(156e-4))        # 1/64 s = 15.6 ms = 156 steps
    assert ctl.sent[-1]["exposure_time_absolute"] == 156
    assert c.write(-6) == pytest.approx(-6, abs=0.01)             # same number as a Windows camera


# Raw exposure_time_absolute values that v1.1.0 (origin/main a939d65) sent for a saved slider
# position, recorded by running its exposure.V4l2Exposure before this change.
V1_1_RAW = {(3, 2047): {0: 3, 37.5: 35, 100: 2047},
            (1, 5000): {0: 1, 37.5: 24, 100: 5000},
            (50, 10000): {0: 50, 37.5: 365, 100: 10000}}


def test_v1_1_linux_saved_exposure_lands_on_the_same_camera_value():
    """v1.1.0 saved manual exposure as a 0-100 position along the camera's own range, never in
    stops, so it needs no rewriting: the same position gives the camera the same raw value."""
    for (lo, hi), expect in V1_1_RAW.items():
        for pos, raw in expect.items():
            ctl = FakeCtrls(lo, hi)
            c = exposure.V4l2Exposure(ctl, ctl.controls())
            c.write(c.from_pos(pos))
            assert ctl.sent[-1]["exposure_time_absolute"] == raw, (lo, hi, pos)


def test_v1_1_0_config_loads_with_nothing_lost(tmp_path):
    """A config.json written by v1.1.0 (tests/fixtures, made with v1.1.0's own config.save on a
    Linux-style setup) loads cleanly: every setting survives, and the schedule arrives at defaults."""
    import copy
    src = FIXTURES / "config_v1.1.0_linux.json"
    v11 = json.loads(src.read_text(encoding="utf-8"))
    p = tmp_path / "config.json"
    p.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = config.load(p)
    for section, values in v11.items():
        assert cfg[section] == values, section          # nothing dropped, reset or retyped
    assert set(cfg) == set(v11) | {"exposure_schedule"}
    assert cfg["exposure_schedule"] == config.DEFAULTS["exposure_schedule"]
    assert cfg["camera"]["exposure_mode"] == "manual" and cfg["camera"]["exposure"] == 37.5
    assert schedule.clean(cfg["exposure_schedule"]) == cfg["exposure_schedule"]
    before = copy.deepcopy(cfg)
    config.save(cfg, p)
    assert config.load(p) == before


def test_v1_1_0_config_runs_in_the_app_unchanged(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(camera, "FAKE", True)
    v11 = json.loads((FIXTURES / "config_v1.1.0_linux.json").read_text(encoding="utf-8"))
    v11["capture"]["output_root"] = str(tmp_path / "out")
    p = tmp_path / "config.json"
    p.write_text(json.dumps(v11), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rewnd.ui.main_window import MainWindow
    w = MainWindow(config.load(p))
    end = time.time() + 4
    while time.time() < end and w.engine.status != "live":
        app.processEvents()
        time.sleep(0.02)
    assert w.engine.status == "live"
    end = time.time() + 1
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert w.engine.exposure_mode() == "manual"
    assert w.engine._ev == pytest.approx(w.engine._exp.from_pos(37.5))   # the saved position, as before
    assert w.engine.exposure_state() == {"active": False}
    assert not w.record.schedule_panel.isVisible()
    w.close()
    saved = json.loads(p.read_text(encoding="utf-8"))
    v11["capture"]["camera"] = saved["capture"]["camera"]   # /dev/video0 isn't here; v1.1 picks one too
    for section, values in v11.items():
        assert saved[section] == values, section


# ---------- compensation ----------

def test_compensation_and_log(tmp_path):
    img = np.full((10, 10, 3), 100, np.uint8)
    assert imaging.compensate(img, 0) is img
    assert 135 <= imaging.compensate(img, 1.0)[0, 0, 0] <= 140     # +1 stop: x2 linear, ~x1.37 in JPEG
    for bad in (float("nan"), float("inf"), "x", None):
        assert imaging.compensate(img, bad) is img
    s = Session.create(tmp_path, config.defaults())
    s.log_frame(1, 1.0)
    s.log_frame(2, 2.0, ev=0.4)
    assert s.frame_ev() == {2: 0.4} and s.timestamps() == {1: 1.0, 2: 2.0}


def test_damaged_compensation_log_never_breaks_export(tmp_path):
    from rewnd import exporter
    s = Session.create(tmp_path, config.defaults())
    for i in range(1, 9):
        cv2.imwrite(str(s.frames_dir / f"frame_{i:06d}.jpg"), np.full((90, 160, 3), 100, np.uint8))
    lines = ['{"i": 1, "t": 1.0, "ev": 0.4}', '{"i": 2, "t": 2.0, "ev": NaN}', '{"i": 3, "t": 3.0, "ev": "abc"}',
             '{"i": 4, "t": 4.0, "ev": 1e308}', '{"i": 5, "t": 5.0, "ev": -Infinity}', 'not json at all',
             '{"i": 6, "t": 6.0, "ev": -7}', '{"t": 7.0, "ev": 0.2}', '{"i": 8, "t": 8.0}']
    (s.path / "frames.jsonl").write_text("\n".join(lines) + "\n\x00garbage", encoding="utf-8")
    assert s.frame_ev() == {1: 0.4, 4: 1.0, 6: -1.0}
    exp = dict(config.defaults()["export"], length_mode="fps", fps=8, hold_end=0, size=0)
    assert exporter.export(s, config.defaults()["look"], exp, config.defaults()["overlay"]).exists()
    (s.path / "frames.jsonl").unlink()
    assert s.frame_ev() == {}
    assert exporter.export(s, config.defaults()["look"], exp, config.defaults()["overlay"]).exists()


# ---------- the engine in scheduled mode ----------

def whole_stops(monkeypatch):
    """Make the demo camera behave like a Windows driver: whole stops only."""
    real = exposure.FakeExposure.write
    monkeypatch.setattr(exposure.FakeExposure, "write", lambda self, ev: real(self, round(ev)))


def start_engine(monkeypatch, sched_cfg, cam_cfg=None, no_exposure=False):
    monkeypatch.setattr(camera, "FAKE", True)
    monkeypatch.setattr(camera, "FAKE_NO_EXPOSURE", no_exposure)
    errors = []
    eng = camera.CaptureEngine(on_error=errors.append)
    eng.WARMUP_SECONDS = 0.1
    eng.set_exposure_schedule(sched_cfg)
    cfg = dict(config.defaults()["camera"], exposure_mode="scheduled")
    cfg.update(cam_cfg or {})
    eng.configure(camera.CameraInfo(id="fake:0", index=0, name="Demo"), "640x360", cfg)
    eng.start()
    end = time.time() + 5
    while eng.status != "live" and time.time() < end:
        time.sleep(0.05)
    return eng, errors


def wait_for(cond, secs=3):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


def all_day(value):
    s = config.defaults()["exposure_schedule"]
    s.update(ease_minutes=0, night_value=value,
             profiles=[{"name": "all day", "start": "00:00", "end": "23:59", "value": value}])
    return s


def test_engine_scheduled_sets_exposure_and_fills_in_fractions(monkeypatch, tmp_path):
    whole_stops(monkeypatch)
    eng, errors = start_engine(monkeypatch, all_day(-6.6))
    try:
        assert wait_for(lambda: eng.exposure_state().get("applied") is not None)
        st = eng.exposure_state()
        assert eng.exposure_mode() == "scheduled" and eng._cap.auto is False
        assert st["applied"] == -7.0 and st["comp"] == pytest.approx(0.4) and st["supported"]
        s = Session.create(tmp_path, config.defaults())
        eng.start_recording(s, 0.3)
        assert wait_for(lambda: len(s.frames()) >= 2)
        eng.stop_recording()
        assert set(s.frame_ev().values()) == {0.4}
        eng.apply_controls(dict(eng.cam_cfg, exposure_mode="auto"))     # leaving the mode
        assert wait_for(lambda: eng._cap.auto is True)
        assert wait_for(lambda: eng.exposure_state() == {"active": False})
        assert not errors
    finally:
        eng.shutdown()


def test_engine_hdr_brackets_around_the_schedule_value(monkeypatch, tmp_path):
    eng, errors = start_engine(monkeypatch, all_day(-8.0), {"hdr": True})
    written = []
    real = eng._exp.write

    def spy(ev):
        written.append(ev)
        return real(ev)
    eng._exp.write = spy
    try:
        assert wait_for(lambda: eng.exposure_state().get("applied") == -8.0)
        s = Session.create(tmp_path, config.defaults())
        shots = []
        eng.on_shot = lambda i, p: shots.append(p)
        eng.start_recording(s, 3.0)
        assert wait_for(lambda: shots, 6)
        eng.stop_recording()
        assert -10.0 in written and -6.5 in written        # manual-style bracket: -2 and +1.5 around -8
        assert eng._ev == -8.0 and not errors              # and back on the schedule's value
    finally:
        eng.shutdown()


@pytest.mark.parametrize("interval", [0.4, 1.0])
def test_schedule_changes_never_delay_shots(monkeypatch, tmp_path, interval):
    """Shots keep their interval while the exposure changes; with room, changes avoid shot time."""
    eng, _ = start_engine(monkeypatch, all_day(-7))
    changes = []
    real = eng._exp.write

    def spy(ev):
        changes.append(time.time())
        return real(ev)
    eng._exp.write = spy
    shots = []
    try:
        assert wait_for(lambda: eng.exposure_state().get("applied") is not None)
        eng.on_shot = lambda i, p: shots.append(time.time())
        eng.start_recording(Session.create(tmp_path, config.defaults()), interval)
        end = time.time() + 4.2
        v = -7
        while time.time() < end:                        # flip the exposure every 0.15 s
            v = -5 if v == -7 else -7
            eng.set_exposure_schedule(all_day(v))
            time.sleep(0.15)
        eng.stop_recording()
    finally:
        eng.shutdown()
    gaps = [b - a for a, b in zip(shots, shots[1:])]
    assert len(shots) >= int(4.2 / interval) - 1
    assert all(abs(g - interval) < 0.12 for g in gaps), gaps          # never pushed back
    if interval > exposure.SETTLE_SECONDS + 0.1:
        inside = [c for c in changes if shots[0] < c < shots[-1]]
        assert inside, "the schedule should still have changed the exposure"
        for c in inside:                                               # each had time to settle
            assert min(t for t in shots if t > c) - c >= exposure.SETTLE_SECONDS - 0.05


# ---------- cameras that won't take a manual exposure ----------

@pytest.mark.parametrize("mode", ["scheduled", "manual", "protect"])
def test_camera_without_manual_exposure_is_reported_not_ignored(monkeypatch, mode):
    infos = []
    eng, errors = start_engine(monkeypatch, all_day(-7), {"exposure_mode": mode}, no_exposure=True)
    eng.on_info = infos.append
    try:
        assert wait_for(lambda: eng._exp is None)
        assert eng.exposure_mode() == "auto" and eng._cap.auto is True     # left on auto, not stuck
        assert errors == [exposure.UNSUPPORTED]                            # told once
        eng.apply_controls(dict(eng.cam_cfg))                              # nudge it again
        time.sleep(0.4)
        assert errors == [exposure.UNSUPPORTED]                            # still only once
        if mode == "scheduled":
            st = eng.exposure_state()
            assert st["active"] and st["supported"] is False and st["reason"] == exposure.UNSUPPORTED
    finally:
        eng.shutdown()


def test_v4l2_camera_that_ignores_exposure_is_reported():
    ctl = FakeCtrls(3, 2047, refuse=True)
    c = exposure.V4l2Exposure(ctl, ctl.controls())
    with pytest.raises(exposure.ExposureUnsupported):
        c.write(-8)


def test_opencv_backend_refusal_is_reported():
    class Cap:
        def set(self, prop, val):
            return prop != cv2.CAP_PROP_EXPOSURE
    c = exposure.CvExposure(Cap(), cv2.CAP_DSHOW)
    with pytest.raises(exposure.ExposureUnsupported):
        c.write(-6)


# ---------- location only in sunrise/sunset mode ----------

def test_location_is_only_requested_in_sunrise_sunset_mode(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rewnd import location
    from rewnd.ui.schedule_panel import SchedulePanel
    calls = []

    def fake_lookup(timeout=12.0):
        calls.append(time.time())
        return 51.5, -0.12
    monkeypatch.setattr(location, "system_location", fake_lookup)

    def pump(secs):
        end = time.time() + secs
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def panel(cam_mode, **sched_kw):
        s = dict(config.defaults()["exposure_schedule"], **sched_kw)
        p = SchedulePanel(s, {"exposure_mode": cam_mode})
        pump(1.6)                                    # past the panel's delayed lookup at launch
        return p

    p = panel("protect", mode="sun")                 # sun picked earlier, but another exposure mode now
    p.locate(quiet=False)
    p.sys_btn.click()
    p.activated()
    pump(0.3)
    assert calls == []

    p = panel("scheduled", mode="fixed")             # scheduled, fixed times: never
    p.locate(quiet=False)
    p.activated()
    pump(0.3)
    assert calls == []

    p.mode.changed.emit("sun")                       # the user picks sunrise / sunset
    pump(0.4)
    assert len(calls) == 1 and p.cfg["location"]["lat"] == 51.5

    p.cam["exposure_mode"] = "manual"                # leaves the scheduled mode
    calls.clear()
    p.locate(quiet=False)
    pump(0.3)
    assert calls == []

    panel("scheduled", mode="sun", location={"source": "manual", "query": "Paris", "lat": 48.86,
                                             "lon": 2.35, "label": "Paris"})
    assert calls == []                               # a typed place is never overridden

    panel("scheduled", mode="sun")                   # fresh: no location yet
    assert len(calls) == 1
