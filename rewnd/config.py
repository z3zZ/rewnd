"""Settings: one nested dict with defaults, saved as JSON in the platform's config folder."""

import copy
import json
import os
import sys
from pathlib import Path


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "rewnd"


CONFIG_PATH = config_dir() / "config.json"
LEGACY_CONFIG_PATH = Path.home() / ".config" / "timelapse-app" / "config.json"

ASPECTS = {
    "original": None,
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:5": (4, 5),
}
POSITIONS = ["top-left", "top-right", "bottom-left", "bottom-right", "top-center", "bottom-center"]

DEFAULTS = {
    "capture": {
        "camera": None,              # camera id string, see camera.list_cameras()
        "resolution": None,          # "1920x1080"; None = best available
        "interval": 10.0,            # seconds between shots
        "output_root": str(Path.home() / "Timelapses"),
        "jpeg_quality": 92,
        "auto_stop_minutes": 0,      # 0 = never
        "keep_awake": True,
        "auto_export": True,         # export a video as soon as you stop
    },
    "camera": {
        "autofocus": True,
        "auto_exposure": True,
        "low_light": False,
        "mjpeg": True,               # ask the camera for MJPG: less USB bandwidth, more stable on hubs/extenders
    },
    "look": {
        "mirror": False,
        "flip": False,
        "rotate": 0,                 # 0/90/180/270 clockwise
        "brightness": 0,             # -100..100
        "contrast": 0,               # -100..100
        "saturation": 0,             # -100..100
        "warmth": 0,                 # -100..100
    },
    "overlay": {
        "timer": True,
        "timer_position": "top-left",
        "clock": False,
        "clock_position": "top-right",
        "text": "",
        "text_position": "bottom-left",
        "watermark": True,
        "watermark_position": "bottom-right",
        "scale": 1.0,                # overlay size multiplier
        "accent": "#C8F135",
        "pill_opacity": 0.55,
    },
    "export": {
        "aspect": "original",
        "fit": "crop",               # crop | blur (fit inside, blurred fill behind)
        "crop_offset": 0.0,          # -1..1, slides the crop window along the long axis
        "length_mode": "target",     # target | fps
        "target_seconds": 30.0,
        "fps": 30,                   # output video frame rate
        "size": 1080,                # short side in pixels (1080 = 1080p); 0 = keep source size
        "quality": "high",           # high | balanced | small
        "deflicker": True,
        "hold_end": 1.5,             # seconds to hold the final frame
    },
    "ui": {
        "accent": "#C8F135",
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def defaults() -> dict:
    return copy.deepcopy(DEFAULTS)


def load(path: Path = None) -> dict:
    path = path or CONFIG_PATH
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    elif path == CONFIG_PATH and LEGACY_CONFIG_PATH.exists():
        try:
            old = json.loads(LEGACY_CONFIG_PATH.read_text(encoding="utf-8"))
            data = {
                "capture": {
                    "interval": old.get("interval", 10.0),
                    "output_root": old.get("output_root", DEFAULTS["capture"]["output_root"]),
                    "resolution": old.get("resolution"),
                },
                "camera": {"low_light": old.get("low_light_boost", False)},
                "export": {"fps": int(old.get("fps", 30)), "length_mode": "fps"},
            }
        except Exception:
            data = {}
    return _merge(DEFAULTS, data)


def save(cfg: dict, path: Path = None) -> None:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, path)
