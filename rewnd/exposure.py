"""Exposure handling: a common exposure control per backend, highlight protection, and HDR merging.

Webcams pick one exposure for the whole scene, so in a dark room a monitor or window blows out to
flat white. Phones avoid that by merging several exposures and tone mapping every frame. rewnd does
the equivalent itself:

* protect: before each shot, lower the exposure until bright things stop clipping
* manual: a fixed exposure from a slider
* hdr: take 2-3 exposures per timelapse frame and fuse them (Mertens exposure fusion)

Exposure is handled in stops (EV, log2 of exposure time) so every backend behaves the same.
"""

import math
import time

import cv2
import numpy as np

from rewnd import v4l2

MODES = ("auto", "protect", "manual")


# ---------- exposure controls ----------

class ExposureControl:
    """Reads and writes exposure in stops. Subclasses fill in the backend details."""
    ev_min = 0.0
    ev_max = 10.0

    def read(self):
        return None

    def write(self, ev):
        """Switch to manual exposure and set it. Returns the clamped ev that was asked for."""
        raise NotImplementedError

    def set_auto(self):
        pass

    def clamp(self, ev):
        return min(self.ev_max, max(self.ev_min, float(ev)))

    def to_pos(self, ev):
        """Slider position 0..100 for an ev."""
        span = self.ev_max - self.ev_min
        return 50.0 if span <= 0 else 100.0 * (self.clamp(ev) - self.ev_min) / span

    def from_pos(self, pos):
        return self.ev_min + (self.ev_max - self.ev_min) * min(100.0, max(0.0, float(pos))) / 100.0


class V4l2Exposure(ExposureControl):
    """exposure_time_absolute is in 100 µs units on UVC cameras; ev = log2 of that."""

    def __init__(self, ctrls, info):
        self.ctrls = ctrls
        e = info["exposure_time_absolute"]
        self.lo, self.hi = max(1, e.get("min", 1)), max(2, e.get("max", 2047))
        self.ev_min, self.ev_max = math.log2(self.lo), math.log2(self.hi)
        self.has_dynamic = "exposure_dynamic_framerate" in info

    def read(self):
        try:
            v = self.ctrls.controls().get("exposure_time_absolute", {}).get("value")
        except Exception:
            return None
        return math.log2(v) if v and v > 0 else None

    def write(self, ev):
        ev = self.clamp(ev)
        raw = int(round(2 ** ev))
        self.ctrls.set({"auto_exposure": v4l2.AE_MANUAL,
                        "exposure_time_absolute": min(self.hi, max(self.lo, raw))})
        return ev

    def set_auto(self):
        self.ctrls.set({"auto_exposure": v4l2.AE_APERTURE_PRIORITY})


class CvExposure(ExposureControl):
    """OpenCV's CAP_PROP_EXPOSURE. DirectShow and Media Foundation use log2 seconds in whole steps;
    V4L2 uses the raw 100 µs units. Ranges can't be queried, so common UVC limits are assumed and
    the value is read back after writing."""

    def __init__(self, cap, backend):
        self.cap, self.backend = cap, backend
        self.log_units = backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF)
        if self.log_units:
            self.ev_min, self.ev_max = -13.0, -1.0
        else:
            self.ev_min, self.ev_max = math.log2(3), math.log2(2047)

    def read(self):
        try:
            v = self.cap.get(cv2.CAP_PROP_EXPOSURE)
        except Exception:
            return None
        if self.log_units:
            return float(v) if v else None
        return math.log2(v) if v and v > 0 else None

    def write(self, ev):
        ev = self.clamp(ev)
        try:
            if self.backend == cv2.CAP_V4L2:
                self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
            elif self.backend == cv2.CAP_DSHOW:
                self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, round(ev) if self.log_units else int(round(2 ** ev)))
        except Exception:
            pass
        return ev

    def set_auto(self):
        try:
            if self.backend == cv2.CAP_V4L2:
                self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
            elif self.backend == cv2.CAP_DSHOW:
                self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
        except Exception:
            pass


class FakeExposure(ExposureControl):
    """Drives the demo camera, which renders brighter or darker with its ev."""
    ev_min, ev_max = -6.0, 4.0

    def __init__(self, fake):
        self.fake = fake

    def read(self):
        return self.fake.ev

    def write(self, ev):
        ev = self.clamp(ev)
        self.fake.ev = ev
        self.fake.auto = False
        return ev

    def set_auto(self):
        self.fake.auto = True
        self.fake.ev = 0.0


def open_exposure(cap, backend, path=None, fake=False):
    """Best exposure control for an open camera, or None if it can't be controlled."""
    if fake:
        return FakeExposure(cap)
    if path:
        ctrls = v4l2.open_controls(path)
        if ctrls is not None:
            info = ctrls.controls()
            if "exposure_time_absolute" in info and "auto_exposure" in info:
                return V4l2Exposure(ctrls, info)
    if backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_V4L2):
        return CvExposure(cap, backend)
    return None


# ---------- metering ----------

CLIP_LEVEL = 250


def measure(frame):
    """Clipped fraction, 99th percentile and median brightness, on a small copy for speed."""
    h, w = frame.shape[:2]
    if w > 240:
        frame = cv2.resize(frame, (240, max(1, round(h * 240 / w))), interpolation=cv2.INTER_AREA)
    peak = frame.max(axis=2) if frame.ndim == 3 else frame
    luma = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return {
        "clip": float((peak >= CLIP_LEVEL).mean()),
        "p99": float(np.percentile(luma, 99)),
        "median": float(np.median(luma)),
    }


class HighlightGuard:
    """Steps exposure down while highlights clip and back up once there's headroom.

    The gap between the two thresholds stops it hunting, steps are small while recording so the
    timelapse doesn't flicker, and it won't darken a scene that's already close to black.
    """

    CLIP_HIGH = 0.02      # more than 2% of pixels at white: too bright
    CLIP_LOW = 0.005      # under 0.5% clipped ...
    HEADROOM_P99 = 215    # ... and the brightest 1% well below white: room to brighten
    MIN_MEDIAN = 10       # stop before the room goes fully black (HDR is the answer past this point)

    def step(self, stats, recording=False):
        """Stops to change the exposure by (0 means hold)."""
        scale = 0.5 if recording else 1.0
        if stats["clip"] > self.CLIP_HIGH and stats["median"] > self.MIN_MEDIAN:
            return -(0.5 if stats["clip"] > 0.08 else 0.25) * scale
        if stats["clip"] < self.CLIP_LOW and stats["p99"] < self.HEADROOM_P99:
            return (0.5 if stats["p99"] < 150 else 0.25) * scale
        return 0.0


# ---------- HDR ----------

def bracket(base, mode, control):
    """Exposures (stops) to take for one HDR frame. Protect already exposes for the highlights, so
    it brackets upward for the shadows; manual brackets around the chosen exposure."""
    offsets = (0.0, 1.5, 3.0) if mode == "protect" else (-2.0, 0.0, 1.5)
    evs = []
    for o in offsets:
        ev = control.clamp(base + o)
        if all(abs(ev - e) > 0.4 for e in evs):
            evs.append(ev)
    return evs


def _weights(img):
    """How well exposed each pixel is: highest at mid-grey, zero where a channel is clipped or crushed."""
    f = img.astype(np.float32) / 255.0
    luma = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    w = np.exp(-((luma - 0.5) ** 2) / (2 * 0.2 ** 2))
    peak, low = f.max(axis=2), f.min(axis=2)
    w[peak >= CLIP_LEVEL / 255.0] = 0.0
    w[low <= 2 / 255.0] *= 0.1
    return w


def _levels(h, w):
    return max(1, min(7, int(math.log2(max(2, min(h, w)))) - 3))


def fuse(frames):
    """Merge differently exposed frames into one 8-bit image.

    This is Mertens-style exposure fusion: every frame gets a weight per pixel for how well exposed
    it is, and the frames are blended across a Laplacian pyramid so the joins don't show. Clipped
    pixels get no weight at all, which keeps flat white screens from washing back in (OpenCV's
    MergeMertens averages them back in because its contrast term is zero on flat areas).
    The camera is fixed for a timelapse, so there's no alignment step.
    """
    frames = [f for f in frames if f is not None]
    if not frames:
        return None
    if len(frames) == 1:
        return frames[0]
    h, w = frames[0].shape[:2]
    frames = [f if f.shape[:2] == (h, w) else cv2.resize(f, (w, h)) for f in frames]

    weights = [_weights(f) for f in frames]
    total = sum(weights)
    # where every frame is clipped (or crushed), fall back to plain averaging
    none = total < 1e-6
    weights = [np.where(none, 1.0 / len(frames), wt / np.maximum(total, 1e-6)) for wt in weights]

    n = _levels(h, w)
    out = None
    for img, wt in zip(frames, weights):
        g = [wt]
        for _ in range(n):
            g.append(cv2.pyrDown(g[-1]))
        gi = [img.astype(np.float32)]
        for _ in range(n):
            gi.append(cv2.pyrDown(gi[-1]))
        lap = [gi[i] - cv2.pyrUp(gi[i + 1], dstsize=(gi[i].shape[1], gi[i].shape[0])) for i in range(n)]
        lap.append(gi[n])
        blended = [lap[i] * g[i][..., None] for i in range(n + 1)]
        out = blended if out is None else [a + b for a, b in zip(out, blended)]

    img = out[-1]
    for i in range(n - 1, -1, -1):
        img = cv2.pyrUp(img, dstsize=(out[i].shape[1], out[i].shape[0])) + out[i]
    return np.clip(img, 0, 255).astype(np.uint8)


HDR_MIN_INTERVAL = 2.5    # seconds between shots needed to fit a bracket in
SETTLE_SECONDS = 0.35     # time for a webcam to apply a new exposure
SETTLE_FRAMES = 3


def grab_settled(read, deadline=2.0):
    """Read past frames taken before an exposure change landed, then return a fresh one."""
    t0 = time.time()
    frames = 0
    frame = None
    while time.time() - t0 < deadline:
        ok, f = read()
        if ok and f is not None:
            frames += 1
            frame = f
            if frames > SETTLE_FRAMES and time.time() - t0 >= SETTLE_SECONDS:
                return frame
    return frame
