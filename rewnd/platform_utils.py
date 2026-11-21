"""OS-specific bits: ffmpeg location, opening folders, keeping the machine awake."""

import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW: no console flashing on Windows


def ffmpeg_exe():
    """Bundled ffmpeg from imageio-ffmpeg first (works out of the box on Windows), then PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def open_path(path):
    path = str(Path(path))
    try:
        if IS_WINDOWS:
            import os
            os.startfile(path)  # noqa: S606
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def reveal(path):
    path = Path(path)
    if IS_WINDOWS and path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        open_path(path if path.is_dir() else path.parent)


class KeepAwake:
    """Stop the system sleeping while a capture runs (the screen may still turn off)."""

    def __init__(self):
        self._proc = None
        self._active = False

    def start(self, reason="Recording a timelapse"):
        if self._active:
            return
        self._active = True
        try:
            if IS_WINDOWS:
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            elif IS_MAC:
                self._proc = subprocess.Popen(["caffeinate", "-i"])
            elif shutil.which("systemd-inhibit"):
                self._proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep", "--who=rewnd", f"--why={reason}",
                     "--mode=block", "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass

    def stop(self):
        if not self._active:
            return
        self._active = False
        try:
            if IS_WINDOWS:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            if self._proc:
                self._proc.terminate()
                self._proc = None
        except Exception:
            pass


def free_space(path):
    try:
        p = Path(path).expanduser()
        while not p.exists() and p != p.parent:
            p = p.parent
        return shutil.disk_usage(p).free
    except Exception:
        return None
