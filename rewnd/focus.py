"""Manual focus: one small interface per backend, with positions as a 0..100 slider.

Webcam autofocus hunts. Every time something moves (you, your hands, a window behind you) it
racks focus in and out, and in a timelapse that turns into frames that pop between sharp and soft.
Turning autofocus off and setting the focus by hand fixes it.

* Linux: V4L2 focus_absolute, with its real range from the driver.
* Windows: DirectShow IAMCameraControl, which also reports the real range. If that can't be
  reached, OpenCV's CAP_PROP_FOCUS is used with a common UVC range assumed.
"""

import cv2

from rewnd import v4l2

FOCUS = 6                 # CameraControl_Focus in IAMCameraControl
FLAG_AUTO, FLAG_MANUAL = 1, 2


class FocusControl:
    lo, hi = 0, 255

    def read(self):
        """Current focus in device units, or None if it can't be read."""
        return None

    def write(self, raw):
        """Switch autofocus off and set the focus."""
        raise NotImplementedError

    def set_auto(self, on):
        pass

    def clamp(self, raw):
        return int(round(min(self.hi, max(self.lo, float(raw)))))

    def to_pos(self, raw):
        span = self.hi - self.lo
        return 50.0 if span <= 0 else 100.0 * (self.clamp(raw) - self.lo) / span

    def from_pos(self, pos):
        return self.clamp(self.lo + (self.hi - self.lo) * min(100.0, max(0.0, float(pos))) / 100.0)


class V4l2Focus(FocusControl):
    def __init__(self, ctrls, info):
        self.ctrls = ctrls
        f = info["focus_absolute"]
        self.lo, self.hi = f.get("min", 0), f.get("max", 255)
        self.step = max(1, f.get("step", 1))
        self.has_auto = "focus_automatic_continuous" in info

    def read(self):
        try:
            return self.ctrls.controls().get("focus_absolute", {}).get("value")
        except Exception:
            return None

    def write(self, raw):
        raw = self.clamp(raw)
        raw = self.lo + ((raw - self.lo) // self.step) * self.step
        sets = {}
        if self.has_auto:
            sets["focus_automatic_continuous"] = 0      # must go first: focus_absolute is inactive in auto
        sets["focus_absolute"] = raw
        self.ctrls.set(sets)
        return raw

    def set_auto(self, on):
        if self.has_auto:
            self.ctrls.set({"focus_automatic_continuous": 1 if on else 0})


class DshowFocus(FocusControl):
    """IAMCameraControl on the same device OpenCV is streaming from. Camera controls are per
    device in the driver, so a second filter instance controls the open stream."""

    def __init__(self, control):
        self.ctl = control
        lo, hi, step, default, caps = control.GetRange(FOCUS)
        if hi <= lo:
            raise ValueError("no focus range")
        self.lo, self.hi, self.step = int(lo), int(hi), max(1, int(step))

    def read(self):
        try:
            value, _flags = self.ctl.Get(FOCUS)
            return int(value)
        except Exception:
            return None

    def write(self, raw):
        raw = self.clamp(raw)
        raw = self.lo + ((raw - self.lo) // self.step) * self.step
        self.ctl.Set(FOCUS, raw, FLAG_MANUAL)
        return raw

    def set_auto(self, on):
        cur = self.read()
        self.ctl.Set(FOCUS, cur if cur is not None else self.lo, FLAG_AUTO if on else FLAG_MANUAL)


def _dshow_camera_control(index):
    """IAMCameraControl for the DirectShow video device at index (Windows only)."""
    from ctypes import HRESULT, POINTER, c_long
    from comtypes import COMMETHOD, GUID, IUnknown

    class IAMCameraControl(IUnknown):
        _case_insensitive_ = True
        _iid_ = GUID("{C6E13370-30AC-11d0-A18C-00A0C9118956}")
        _idlflags_ = []
        _methods_ = [
            COMMETHOD([], HRESULT, "GetRange",
                      (["in"], c_long, "Property"),
                      (["out"], POINTER(c_long), "pMin"),
                      (["out"], POINTER(c_long), "pMax"),
                      (["out"], POINTER(c_long), "pSteppingDelta"),
                      (["out"], POINTER(c_long), "pDefault"),
                      (["out"], POINTER(c_long), "pCapsFlags")),
            COMMETHOD([], HRESULT, "Set",
                      (["in"], c_long, "Property"),
                      (["in"], c_long, "lValue"),
                      (["in"], c_long, "Flags")),
            COMMETHOD([], HRESULT, "Get",
                      (["in"], c_long, "Property"),
                      (["out"], POINTER(c_long), "lValue"),
                      (["out"], POINTER(c_long), "Flags")),
        ]

    import comtypes
    try:   # COM has to be set up on the capture thread too, not just the UI thread
        comtypes.CoInitialize()
    except OSError:
        pass   # already set up on this thread (OpenCV's DirectShow backend does it)
    from pygrabber.dshow_graph import SystemDeviceEnum
    from pygrabber.dshow_ids import DeviceCategories
    flt, _name = SystemDeviceEnum().get_filter_by_index(DeviceCategories.VideoInputDevice, index)
    return flt.QueryInterface(IAMCameraControl)


class CvFocus(FocusControl):
    """OpenCV's CAP_PROP_FOCUS. The range can't be queried, so the common UVC 0..255 is assumed."""

    def __init__(self, cap):
        self.cap = cap

    def read(self):
        try:
            v = self.cap.get(cv2.CAP_PROP_FOCUS)
            return int(v) if v is not None and v >= 0 else None
        except Exception:
            return None

    def write(self, raw):
        raw = self.clamp(raw)
        try:
            self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
            self.cap.set(cv2.CAP_PROP_FOCUS, raw)
        except Exception:
            pass
        return raw

    def set_auto(self, on):
        try:
            self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1 if on else 0)
        except Exception:
            pass


class FakeFocus(FocusControl):
    """Drives the demo camera, which blurs as focus moves away from its sharp point."""
    lo, hi = 0, 255

    def __init__(self, fake):
        self.fake = fake

    def read(self):
        return self.fake.focus

    def write(self, raw):
        self.fake.focus = self.clamp(raw)
        self.fake.autofocus = False
        return self.fake.focus

    def set_auto(self, on):
        self.fake.autofocus = bool(on)


def open_focus(cap, backend, path=None, index=None, fake=False):
    """Best focus control for an open camera, or None if it can't be focused by hand."""
    if fake:
        return FakeFocus(cap)
    if path:
        ctrls = v4l2.open_controls(path)
        if ctrls is not None:
            info = ctrls.controls()
            if "focus_absolute" in info:
                return V4l2Focus(ctrls, info)
            return None   # V4L2 told us the truth: this camera has no adjustable focus
    if backend == cv2.CAP_DSHOW and index is not None:
        try:
            return DshowFocus(_dshow_camera_control(index))
        except Exception:
            pass
    if backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_V4L2):
        return CvFocus(cap)
    return None
