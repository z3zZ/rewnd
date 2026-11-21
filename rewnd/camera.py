"""Cross-platform camera discovery and the background capture engine.

The old app ran a fresh ffmpeg process per shot, which reopened the camera every tick. That is
slow, lets auto-exposure reset each time, and is fragile on long USB extenders and hubs, where the
device can briefly drop and re-enumerate. The engine here keeps one camera open, streams it for
the live preview, saves the newest frame on each tick, and reconnects automatically when the
camera disappears.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from rewnd.platform_utils import IS_MAC, IS_WINDOWS, NO_WINDOW
from rewnd.session import frame_name

IS_LINUX = sys.platform.startswith("linux")
COMMON_RESOLUTIONS = ["3840x2160", "2560x1440", "1920x1080", "1600x900", "1280x720", "1024x576",
                      "800x600", "640x480"]
FAKE = os.environ.get("REWND_FAKE_CAMERA") == "1"


@dataclass
class CameraInfo:
    id: str          # stable-ish identifier saved in settings
    index: int       # OpenCV index
    name: str
    path: str = ""   # /dev/videoN on Linux

    @property
    def label(self):
        return self.name


def _run(cmd):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                              timeout=5, creationflags=NO_WINDOW).stdout
    except Exception:
        return ""


# ---------- discovery ----------

def _linux_cameras():
    out = []
    sysfs = Path("/sys/class/video4linux")
    nodes = sorted(Path("/dev").glob("video*"), key=lambda p: int(re.sub(r"\D", "", p.name) or 0))
    have_v4l2 = shutil.which("v4l2-ctl") is not None
    for dev in nodes:
        n = int(re.sub(r"\D", "", dev.name) or 0)
        name = ""
        try:
            name = (sysfs / dev.name / "name").read_text().strip()
        except Exception:
            pass
        if have_v4l2:
            caps = _run(["v4l2-ctl", "--device", str(dev), "--all"])
            in_dev = False
            capture = False
            for line in caps.splitlines():
                if "Device Caps" in line:
                    in_dev = True
                    continue
                if in_dev:
                    if not line.startswith("\t\t"):
                        break
                    if "Video Capture" in line:
                        capture = True
            if caps and not capture:
                continue
        else:
            try:  # UVC exposes a metadata node next to each capture node; its index is 1
                if (sysfs / dev.name / "index").read_text().strip() != "0":
                    continue
            except Exception:
                pass
        out.append(CameraInfo(id=str(dev), index=n, name=name or dev.name, path=str(dev)))
    return out


def _windows_cameras():
    names = []
    try:
        from pygrabber.dshow_graph import FilterGraph  # DirectShow names, same order as CAP_DSHOW
        names = FilterGraph().get_input_devices()
    except Exception:
        names = []
    if names:
        seen = {}
        out = []
        for i, nm in enumerate(names):
            seen[nm] = seen.get(nm, 0) + 1
            label = nm if seen[nm] == 1 else f"{nm} ({seen[nm]})"
            out.append(CameraInfo(id=f"dshow:{label}", index=i, name=label))
        return out
    return _probe_indices(cv2.CAP_DSHOW, "Camera")


def _probe_indices(api, prefix, limit=6):
    out = []
    for i in range(limit):
        cap = cv2.VideoCapture(i, api)
        ok = cap.isOpened()
        cap.release()
        if ok:
            out.append(CameraInfo(id=f"{prefix.lower()}:{i}", index=i, name=f"{prefix} {i + 1}"))
    return out


def list_cameras():
    if FAKE:
        return [CameraInfo(id="fake:0", index=0, name="Demo camera")]
    if IS_WINDOWS:
        return _windows_cameras()
    if IS_LINUX:
        return _linux_cameras()
    if IS_MAC:
        return _probe_indices(cv2.CAP_AVFOUNDATION, "Camera", limit=4)
    return _probe_indices(cv2.CAP_ANY, "Camera")


def find_camera(cameras, cam_id):
    """Match a saved id, falling back to the same name at a different index (USB re-plugs reorder)."""
    if not cameras:
        return None
    for c in cameras:
        if c.id == cam_id:
            return c
    if cam_id and ":" in cam_id:
        tail = cam_id.split(":", 1)[1]
        for c in cameras:
            if c.name == tail:
                return c
    return cameras[0] if cam_id is None else None


def _area(res):
    w, h = res.split("x")
    return int(w) * int(h)


def linux_resolutions(path, prefer_mjpeg=True):
    if not path or shutil.which("v4l2-ctl") is None:
        return []
    out = _run(["v4l2-ctl", "--device", path, "--list-formats-ext"])
    by_fmt = {}
    fmt = None
    for line in out.splitlines():
        line = line.strip()
        m = re.match(r"\[\d+\]: '(\w+)'", line)
        if m:
            fmt = m.group(1)
            continue
        if line.startswith("Size: Discrete") and fmt:
            by_fmt.setdefault(fmt, set()).add(line.split()[-1])
    if prefer_mjpeg and by_fmt.get("MJPG"):
        sizes = set(by_fmt["MJPG"])
    else:
        sizes = set().union(*by_fmt.values()) if by_fmt else set()
    return sorted(sizes, key=_area, reverse=True)


# ---------- camera controls ----------

def _v4l2_ctrls(path):
    out = _run(["v4l2-ctl", "--device", path, "--list-ctrls"])
    ctrls = {}
    for line in out.splitlines():
        line = line.strip()
        if "0x" not in line or ":" not in line:
            continue
        name = line.split()[0]
        kv = {}
        for part in line.split(":", 1)[1].split():
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    kv[k] = int(v)
                except ValueError:
                    pass
        ctrls[name] = kv
    return ctrls


def _v4l2_set(path, assignments):
    if assignments:
        _run(["v4l2-ctl", "--device", path, "--set-ctrl", ",".join(assignments)])


def v4l2_apply(path, cam_cfg):
    """Precise controls on Linux via v4l2-ctl. Returns True if v4l2-ctl handled it."""
    if not path or shutil.which("v4l2-ctl") is None:
        return False
    c = _v4l2_ctrls(path)
    sets = []
    for name in ("focus_automatic_continuous", "focus_auto"):
        if name in c:
            sets.append(f"{name}={1 if cam_cfg.get('autofocus', True) else 0}")
            break
    for name in ("auto_exposure", "exposure_auto"):
        if name in c:
            # 3 = aperture priority (auto), 1 = manual (locks the current exposure)
            sets.append(f"{name}={3 if cam_cfg.get('auto_exposure', True) else 1}")
            break
    _v4l2_set(path, sets)

    sets = []
    boost = cam_cfg.get("low_light", False)
    if "backlight_compensation" in c and "max" in c["backlight_compensation"]:
        v = c["backlight_compensation"]
        sets.append(f"backlight_compensation={v['max'] if boost else v.get('default', v['min'])}")
    for name, k in (("gain", 0.7), ("brightness", 0.35), ("gamma", 0.25)):
        if name not in c or "min" not in c[name] or "max" not in c[name]:
            continue
        v = c[name]
        base = v.get("default", (v["min"] + v["max"]) // 2)
        if boost:
            if name == "gain":
                target = round(v["min"] + k * (v["max"] - v["min"]))
            else:
                target = min(v["max"], round(base + k * (v["max"] - base)))
        else:
            target = base
        sets.append(f"{name}={target}")
    _v4l2_set(path, sets)
    return True


def cv_apply(cap, backend, cam_cfg):
    """Best-effort controls through OpenCV (Windows/macOS, or Linux without v4l2-ctl)."""
    try:
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 1 if cam_cfg.get("autofocus", True) else 0)
        auto = cam_cfg.get("auto_exposure", True)
        if backend == cv2.CAP_V4L2:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3 if auto else 1)
        elif backend == cv2.CAP_DSHOW:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1 if auto else 0)
        if cam_cfg.get("low_light"):
            cap.set(cv2.CAP_PROP_BACKLIGHT, 1)
    except Exception:
        pass


# ---------- fake camera for demos and tests ----------

class FakeCapture:
    def __init__(self, w=1920, h=1080):
        self.w, self.h = w, h
        self.t0 = time.time()
        self.alive = True

    def isOpened(self):
        return self.alive

    def set(self, prop, val):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            self.w = int(val)
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            self.h = int(val)
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FRAME_WIDTH: self.w, cv2.CAP_PROP_FRAME_HEIGHT: self.h}.get(prop, 0)

    def read(self):
        time.sleep(1 / 30)
        t = time.time() - self.t0
        w, h = self.w, self.h
        y, x = np.mgrid[0:h:4, 0:w:4].astype(np.float32)
        r = 40 + 30 * np.sin(x / w * 3 + t * 0.3)
        g = 30 + 25 * np.sin(y / h * 4 + t * 0.2)
        b = 70 + 50 * np.cos((x + y) / (w + h) * 5 - t * 0.25)
        small = np.dstack([b, g, r]).clip(0, 255).astype(np.uint8)
        img = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        cx = int(w * (0.5 + 0.3 * np.sin(t * 0.5)))
        cv2.circle(img, (cx, int(h * 0.55)), int(h * 0.12), (90, 200, 250), -1, cv2.LINE_AA)
        cv2.rectangle(img, (int(w * 0.1), int(h * 0.78)), (int(w * 0.9), int(h * 0.82)), (40, 40, 50), -1)
        return True, img

    def release(self):
        self.alive = False


# ---------- engine ----------

class CaptureEngine(threading.Thread):
    """Owns the camera. UI threads talk to it through methods; it reports through callbacks."""

    RETRY_SECONDS = 2.0
    STALL_SECONDS = 3.0
    WARMUP_SECONDS = 1.2

    def __init__(self, on_status=None, on_shot=None, on_info=None, on_error=None):
        super().__init__(daemon=True, name="rewnd-capture")
        self.on_status = on_status or (lambda *a: None)
        self.on_shot = on_shot or (lambda *a: None)
        self.on_info = on_info or (lambda *a: None)
        self.on_error = on_error or (lambda *a: None)

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cmds = []
        self._latest = None
        self._latest_ts = 0.0
        self.status = "idle"

        self.camera = None        # CameraInfo
        self.resolution = None
        self.cam_cfg = {}
        self.actual_size = None
        self.res_cache = {}

        self.session = None
        self.interval = 10.0
        self.jpeg_quality = 92
        self.next_shot = 0.0
        self.shot_count = 0
        self.missed = 0
        self._cap = None
        self._backend = None
        self._opened_at = 0.0

    # ----- public API (thread-safe) -----

    def latest(self):
        with self._lock:
            return self._latest, self._latest_ts

    def configure(self, camera, resolution, cam_cfg):
        with self._lock:
            self._cmds.append(("configure", camera, resolution, dict(cam_cfg)))

    def apply_controls(self, cam_cfg):
        with self._lock:
            self._cmds.append(("controls", dict(cam_cfg)))

    def open_driver_settings(self):
        with self._lock:
            self._cmds.append(("driver",))

    def start_recording(self, session, interval, jpeg_quality=92):
        with self._lock:
            self.session = session
            self.interval = max(0.2, float(interval))
            self.jpeg_quality = int(jpeg_quality)
            self.shot_count = session.next_index() - 1
            self.missed = 0
            self.next_shot = time.time()

    def stop_recording(self):
        with self._lock:
            s = self.session
            self.session = None
        return s

    def recording(self):
        return self.session is not None

    def seconds_to_next(self):
        if not self.session:
            return None
        return max(0.0, self.next_shot - time.time())

    def shutdown(self):
        self._stop.set()

    # ----- thread -----

    def _set_status(self, s, msg=""):
        if s != self.status or msg:
            self.status = s
            self.on_status(s, msg)

    def _open(self):
        cam = self.camera
        if cam is None:
            return False
        if FAKE:
            cap, backend = FakeCapture(), None
        else:
            if IS_WINDOWS:
                apis = [cv2.CAP_DSHOW, cv2.CAP_MSMF]
            elif IS_LINUX:
                apis = [cv2.CAP_V4L2]
            elif IS_MAC:
                apis = [cv2.CAP_AVFOUNDATION]
            else:
                apis = [cv2.CAP_ANY]
            cap = backend = None
            for api in apis:
                target = cam.path if (IS_LINUX and cam.path) else cam.index
                c = cv2.VideoCapture(target, api)
                if c.isOpened():
                    cap, backend = c, api
                    break
                c.release()
            if cap is None:
                return False

        if self.cam_cfg.get("mjpeg", True) and backend is not None:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if cam.id not in self.res_cache:
            self.res_cache[cam.id] = self._discover_resolutions(cap, cam)
        options = self.res_cache[cam.id]
        res = self.resolution if self.resolution in options else (options[0] if options else None)
        if res:
            w, h = res.split("x")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(w))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(h))

        self._cap, self._backend = cap, backend
        self._apply_controls()
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            self._cap = None
            return False
        self.actual_size = (frame.shape[1], frame.shape[0])
        self._opened_at = time.time()
        self.on_info({"camera": cam, "resolutions": options,
                      "actual": f"{self.actual_size[0]}x{self.actual_size[1]}"})
        return True

    def _discover_resolutions(self, cap, cam):
        if FAKE:
            return ["1920x1080", "1280x720"]
        found = linux_resolutions(cam.path, self.cam_cfg.get("mjpeg", True)) if IS_LINUX else []
        if found:
            return found
        seen = set()
        for res in COMMON_RESOLUTIONS:
            w, h = (int(x) for x in res.split("x"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            aw, ah = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if aw and ah:
                seen.add(f"{aw}x{ah}")
        return sorted(seen, key=_area, reverse=True) or ["1280x720", "640x480"]

    def _apply_controls(self):
        if self._cap is None or FAKE:
            return
        if not (IS_LINUX and v4l2_apply(self.camera.path, self.cam_cfg)):
            cv_apply(self._cap, self._backend, self.cam_cfg)

    def _close(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    def _handle_cmds(self):
        with self._lock:
            cmds, self._cmds = self._cmds, []
        for cmd in cmds:
            if cmd[0] == "configure":
                _, camera, resolution, cam_cfg = cmd
                mjpeg_changed = cam_cfg.get("mjpeg") != self.cam_cfg.get("mjpeg")
                changed = (camera is None or self.camera is None or camera.id != self.camera.id
                           or resolution != self.resolution or mjpeg_changed)
                self.camera, self.resolution, self.cam_cfg = camera, resolution, cam_cfg
                if mjpeg_changed and camera is not None:
                    self.res_cache.pop(camera.id, None)
                if changed:
                    self._close()
            elif cmd[0] == "controls":
                self.cam_cfg.update(cmd[1])
                self._apply_controls()
            elif cmd[0] == "driver":
                if self._cap is not None and self._backend == cv2.CAP_DSHOW:
                    self._cap.set(cv2.CAP_PROP_SETTINGS, 1)
                else:
                    self.on_error("Camera driver settings are only available on Windows. "
                                  "On Linux, install v4l-utils for full control.")

    def _save_shot(self, frame, now):
        s = self.session
        if s is None:
            return
        idx = self.shot_count + 1
        dest = s.frames_dir / frame_name(idx)
        ok = cv2.imwrite(str(dest), frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if ok:
            self.shot_count = idx
            s.log_frame(idx, now)
            self.on_shot(idx, dest)
        else:
            self.on_error(f"Couldn't write {dest}. Is the drive full or disconnected?")

    def run(self):
        last_good = 0.0
        last_attempt = 0.0
        while not self._stop.is_set():
            self._handle_cmds()

            if self._cap is None:
                if self.camera is None:
                    self._set_status("no-camera")
                    time.sleep(0.3)
                    continue
                now = time.time()
                if now - last_attempt < self.RETRY_SECONDS and self.status in ("reconnecting", "no-camera"):
                    time.sleep(0.1)
                    self._miss_due_shots(now)
                    continue
                last_attempt = now
                self._set_status("connecting")
                if self._open():
                    last_good = time.time()
                    self._set_status("live")
                else:
                    self._set_status("reconnecting")
                    # A re-plugged USB camera can come back at a different index; look again by name.
                    if not FAKE:
                        cams = list_cameras()
                        again = find_camera(cams, self.camera.id) if self.camera else None
                        if again is not None:
                            self.camera = again
                continue

            ok, frame = self._cap.read()
            now = time.time()
            if ok and frame is not None:
                last_good = now
                with self._lock:
                    self._latest, self._latest_ts = frame, now
                warmed = now - self._opened_at >= self.WARMUP_SECONDS
                if self.session is not None and warmed and now >= self.next_shot:
                    self._save_shot(frame, now)
                    self.next_shot += self.interval
                    if self.next_shot < now:   # fell behind (sleep, stall): don't burst, just resync
                        self.next_shot = now + self.interval
            elif now - last_good > self.STALL_SECONDS:
                self._close()
                self._set_status("reconnecting", "Camera stopped responding. Reconnecting…")
            else:
                time.sleep(0.02)
        self._close()

    def _miss_due_shots(self, now):
        if self.session is not None and now >= self.next_shot:
            self.missed += 1
            self.next_shot = now + self.interval
