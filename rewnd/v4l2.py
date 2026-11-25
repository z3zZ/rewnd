"""Linux camera controls without needing v4l2-ctl installed.

Controls are read and written straight through the V4L2 ioctls on a second file handle, which works
while OpenCV is streaming from the same device. If that fails for any reason, v4l2-ctl is used when
it's installed. Both expose the same small interface: controls() and set().
"""

import os
import shutil
import struct
import subprocess

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

from rewnd.platform_utils import NO_WINDOW


def _iowr(nr, size):
    return (3 << 30) | (size << 16) | (ord("V") << 8) | nr


_QUERYCTRL = struct.Struct("=II32siiiiI2I")   # struct v4l2_queryctrl, 68 bytes
_CONTROL = struct.Struct("=Ii")               # struct v4l2_control, 8 bytes
VIDIOC_QUERYCTRL = _iowr(36, _QUERYCTRL.size)
VIDIOC_G_CTRL = _iowr(27, _CONTROL.size)
VIDIOC_S_CTRL = _iowr(28, _CONTROL.size)
CTRL_FLAG_DISABLED = 0x0001

_USER = 0x00980900
_CAMERA = 0x009A0900
# Names follow current v4l2-ctl so both backends agree.
CONTROL_IDS = {
    "brightness": _USER + 0,
    "contrast": _USER + 1,
    "saturation": _USER + 2,
    "gamma": _USER + 16,
    "gain": _USER + 19,
    "backlight_compensation": _USER + 28,
    "auto_exposure": _CAMERA + 1,
    "exposure_time_absolute": _CAMERA + 2,
    "exposure_dynamic_framerate": _CAMERA + 3,
    "focus_absolute": _CAMERA + 10,
    "focus_automatic_continuous": _CAMERA + 12,
}
# Older v4l2-ctl releases print these names instead.
LEGACY_NAMES = {
    "exposure_auto": "auto_exposure",
    "exposure_absolute": "exposure_time_absolute",
    "exposure_auto_priority": "exposure_dynamic_framerate",
    "focus_auto": "focus_automatic_continuous",
}

# auto_exposure menu values
AE_MANUAL = 1
AE_APERTURE_PRIORITY = 3


class IoctlControls:
    def __init__(self, path):
        self.path = path

    def _fd(self):
        return os.open(self.path, os.O_RDWR | os.O_NONBLOCK)

    def controls(self):
        out = {}
        fd = self._fd()
        try:
            for name, cid in CONTROL_IDS.items():
                buf = bytearray(_QUERYCTRL.pack(cid, 0, b"", 0, 0, 0, 0, 0, 0, 0))
                try:
                    fcntl.ioctl(fd, VIDIOC_QUERYCTRL, buf)
                except OSError:
                    continue
                _, _, _, lo, hi, step, default, flags, _, _ = _QUERYCTRL.unpack(buf)
                if flags & CTRL_FLAG_DISABLED:
                    continue
                ctl = {"min": lo, "max": hi, "step": step, "default": default}
                cbuf = bytearray(_CONTROL.pack(cid, 0))
                try:
                    fcntl.ioctl(fd, VIDIOC_G_CTRL, cbuf)
                    ctl["value"] = _CONTROL.unpack(cbuf)[1]
                except OSError:
                    pass
                out[name] = ctl
        finally:
            os.close(fd)
        return out

    def set(self, values):
        fd = self._fd()
        try:
            for name, v in values.items():
                cid = CONTROL_IDS.get(name)
                if cid is None:
                    continue
                try:
                    fcntl.ioctl(fd, VIDIOC_S_CTRL, bytearray(_CONTROL.pack(cid, int(v))))
                except OSError:
                    pass   # inactive (e.g. exposure while in auto) or unsupported
        finally:
            os.close(fd)


class V4l2CtlControls:
    def __init__(self, path):
        self.path = path
        self._names = {}   # canonical name -> the name this v4l2-ctl uses

    def _run(self, args):
        try:
            return subprocess.run(["v4l2-ctl", "--device", self.path, *args], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, timeout=5,
                                  creationflags=NO_WINDOW).stdout
        except Exception:
            return ""

    def controls(self):
        out = {}
        for line in self._run(["--list-ctrls"]).splitlines():
            line = line.strip()
            if "0x" not in line or ":" not in line:
                continue
            raw = line.split()[0]
            name = LEGACY_NAMES.get(raw, raw)
            if name not in CONTROL_IDS:
                continue
            kv = {}
            for part in line.split(":", 1)[1].split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    try:
                        kv[k] = int(v)
                    except ValueError:
                        pass
            if "flags" in line and "inactive" in line:
                kv["inactive"] = True
            self._names[name] = raw
            out[name] = kv
        return out

    def set(self, values):
        if not self._names:
            self.controls()
        sets = [f"{self._names.get(k, k)}={int(v)}" for k, v in values.items()]
        # one call per control, so an inactive one doesn't stop the rest
        for s in sets:
            self._run(["--set-ctrl", s])


def open_controls(path):
    """Best available control backend for a /dev/videoN path, or None."""
    if not path:
        return None
    if fcntl is not None and os.path.exists(path):
        c = IoctlControls(path)
        try:
            if c.controls():
                return c
        except OSError:
            pass
    if shutil.which("v4l2-ctl"):
        c = V4l2CtlControls(path)
        if c.controls():
            return c
    return None
