import json, subprocess, threading, time

import cv2
import numpy as np
import pytest

from rewnd import config, exporter, imaging
from rewnd.platform_utils import ffmpeg_exe
from rewnd.session import Session, list_sessions


def make_session(tmp_path, n=40, w=640, h=360, interval=22.0):
    cfg = config.defaults()
    cfg["capture"]["interval"] = interval
    s = Session.create(tmp_path, cfg)
    t0 = 1_700_000_000.0
    for i in range(1, n + 1):
        img = np.full((h, w, 3), (i * 5) % 255, np.uint8)
        cv2.putText(img, str(i), (20, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
        cv2.imwrite(str(s.frames_dir / f"frame_{i:06d}.jpg"), img)
        s.log_frame(i, t0 + (i - 1) * interval)
    return s, cfg


def probe(path):
    out = subprocess.run([ffmpeg_exe(), "-i", str(path)], stderr=subprocess.PIPE, text=True).stderr
    import re
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    size = re.search(r", (\d{2,5})x(\d{2,5})", out)
    return dur, (int(size.group(1)), int(size.group(2)))


def test_plan_subsamples_and_repeats():
    assert len(exporter.plan(1000, {"length_mode": "target", "target_seconds": 10, "fps": 30})) == 300
    seq = exporter.plan(10, {"length_mode": "target", "target_seconds": 2, "fps": 30})
    assert len(seq) == 60 and seq[0] == 0 and seq[-1] == 9 and seq == sorted(seq)
    assert exporter.plan(50, {"length_mode": "fps", "fps": 30}) == list(range(50))


def test_output_sizes():
    assert imaging.output_size(1920, 1080, "original", 1080) == (1920, 1080)
    assert imaging.output_size(1920, 1080, "original", 720) == (1280, 720)
    assert imaging.output_size(640, 360, "original", 1080) == (640, 360)  # never upscale original
    assert imaging.output_size(1920, 1080, "9:16", 1080) == (1080, 1920)
    assert imaging.output_size(1920, 1080, "1:1", 1080) == (1080, 1080)
    w, h = imaging.output_size(1920, 1080, "4:5", 1080)
    assert (w, h) == (1080, 1350)


def test_orient_and_fit():
    img = np.zeros((100, 200, 3), np.uint8)
    img[:, :100] = 255  # left half white
    assert imaging.orient(img, {"mirror": True})[:, -1].mean() == 255
    assert imaging.orient(img, {"rotate": 90}).shape[:2] == (200, 100)
    out = imaging.frame_to(img, 90, 160, "blur")
    assert out.shape == (160, 90, 3)
    out = imaging.frame_to(img, 90, 160, "crop", offset=-1.0)
    assert out.mean() > 250  # crop slid fully left: all white


def test_overlays_use_chosen_colour_everywhere():
    """Every overlay (stopwatch, clock, caption, tag) is drawn in the chosen overlay colour."""
    base = dict(config.DEFAULTS["overlay"], timer=False, clock=False, watermark=False, text="",
                accent="#FF0000", pill_opacity=0.0)
    for one in ({"timer": True}, {"clock": True}, {"text": "building inkd"}, {"watermark": True}):
        img = np.zeros((720, 1280, 3), np.uint8)
        imaging.draw_overlays(img, dict(base, **one), elapsed=3725, wall=imaging.wall_time(1_700_000_000))
        red = (img[..., 2] > 200) & (img[..., 1] < 60) & (img[..., 0] < 60)
        assert red.sum() > 50, one
    assert imaging.format_elapsed(3725) == "01:02:05"


def test_toggle_animation_does_not_move_widget():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    from rewnd.ui.widgets import Toggle
    host = QWidget()
    t = Toggle(True)
    t.setParent(host)
    t.move(40, 30)
    host.show()
    t.setChecked(False)
    end = time.time() + 0.4
    while time.time() < end:
        app.processEvents()
    assert (t.x(), t.y()) == (40, 30)


def test_export_target_vertical(tmp_path):
    s, cfg = make_session(tmp_path, n=40)
    exp = dict(cfg["export"], aspect="9:16", target_seconds=3, fps=30, hold_end=1.0, size=640)
    seen = []
    out = exporter.export(s, cfg["look"], exp, cfg["overlay"], progress=lambda d, t: seen.append((d, t)))
    dur, (w, h) = probe(out)
    assert out.name == "rewnd_9x16_3s.mp4"
    assert (w, h) == (640, 1138)
    assert 3.8 < dur < 4.3
    assert seen[-1][0] == seen[-1][1]


def test_export_fps_mode_blur_fit(tmp_path):
    s, cfg = make_session(tmp_path, n=30)
    exp = dict(cfg["export"], aspect="1:1", fit="blur", length_mode="fps", fps=15, hold_end=0, deflicker=False)
    out = exporter.export(s, dict(cfg["look"], mirror=True, saturation=20), exp, cfg["overlay"])
    dur, (w, h) = probe(out)
    assert w == h == 1080 and 1.8 < dur < 2.2


def test_export_cancel(tmp_path):
    s, cfg = make_session(tmp_path, n=60)
    ev = threading.Event()
    ev.set()
    with pytest.raises(exporter.ExportCancelled):
        exporter.export(s, cfg["look"], cfg["export"], cfg["overlay"], cancel=ev)
    assert not list(s.path.glob("*.mp4"))


def test_legacy_session_without_metadata(tmp_path):
    d = tmp_path / "2026-11-01_10-00-00" / "frames"
    d.mkdir(parents=True)
    for i in range(1, 6):
        cv2.imwrite(str(d / f"frame_{i:06d}.jpg"), np.zeros((120, 160, 3), np.uint8))
    (ss,) = list_sessions(tmp_path)
    assert len(ss.frames()) == 5 and ss.timestamps()


def test_config_roundtrip(tmp_path):
    p = tmp_path / "c.json"
    cfg = config.load(p)
    cfg["look"]["mirror"] = True
    config.save(cfg, p)
    assert config.load(p)["look"]["mirror"] is True
    p.write_text(json.dumps({"look": {"rotate": 90}, "junk": 1}))
    c2 = config.load(p)
    assert c2["look"]["rotate"] == 90 and "junk" not in c2 and c2["export"]["fps"] == 30


def test_engine_records_with_fake_camera(tmp_path, monkeypatch):
    from rewnd import camera
    monkeypatch.setattr(camera, "FAKE", True)
    shots = []
    eng = camera.CaptureEngine(on_shot=lambda i, p: shots.append(i))
    eng.WARMUP_SECONDS = 0.1
    eng.start()
    cams = camera.list_cameras()
    cfg = config.defaults()
    eng.configure(cams[0], "1280x720", cfg["camera"])
    t = time.time()
    while eng.status != "live" and time.time() - t < 5:
        time.sleep(0.05)
    s = Session.create(tmp_path, cfg)
    eng.start_recording(s, 0.3)
    time.sleep(1.6)
    eng.stop_recording()
    eng.shutdown()
    assert len(shots) >= 3
    assert len(s.frames()) == len(shots) and len(s.timestamps()) == len(shots)
    assert cv2.imread(str(s.frames()[0])).shape == (720, 1280, 3)
