"""A session is one recording: a folder with frames/, a frames.jsonl log and session.json."""

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FRAME_GLOB = "frame_*.jpg"
FRAME_RE = re.compile(r"frame_(\d+)\.jpg$")


def frame_name(idx):
    return f"frame_{idx:06d}.jpg"


@dataclass
class Session:
    path: Path
    meta: dict = field(default_factory=dict)

    # ---------- creation ----------

    @classmethod
    def create(cls, root, settings):
        root = Path(root).expanduser()
        name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = root / name
        n = 2
        while path.exists():
            path = root / f"{name}_{n}"
            n += 1
        (path / "frames").mkdir(parents=True)
        s = cls(path, {
            "app": "rewnd",
            "version": 1,
            "title": "",
            "started": time.time(),
            "ended": None,
            "interval": settings["capture"]["interval"],
            "camera": settings["capture"].get("camera"),
            "resolution": settings["capture"].get("resolution"),
            "look": settings["look"],
            "overlay": settings["overlay"],
            "export": settings["export"],
        })
        s.save_meta()
        return s

    @classmethod
    def open(cls, path):
        path = Path(path)
        meta = {}
        mp = path / "session.json"
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        return cls(path, meta)

    def save_meta(self):
        tmp = self.path / "session.json.tmp"
        tmp.write_text(json.dumps(self.meta, indent=2), encoding="utf-8")
        os.replace(tmp, self.path / "session.json")

    # ---------- frames ----------

    @property
    def frames_dir(self):
        d = self.path / "frames"
        return d if d.is_dir() else self.path

    def frames(self):
        return sorted(self.frames_dir.glob(FRAME_GLOB))

    def next_index(self):
        last = 0
        for p in self.frames_dir.glob(FRAME_GLOB):
            m = FRAME_RE.search(p.name)
            if m:
                last = max(last, int(m.group(1)))
        return last + 1

    def log_frame(self, idx, ts):
        with open(self.path / "frames.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"i": idx, "t": round(ts, 3)}) + "\n")

    def timestamps(self):
        """{frame index: capture time}. Old sessions without a log fall back to file times."""
        out = {}
        log = self.path / "frames.jsonl"
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    out[int(rec["i"])] = float(rec["t"])
                except Exception:
                    continue
        if not out:
            for p in self.frames():
                m = FRAME_RE.search(p.name)
                if m:
                    out[int(m.group(1))] = p.stat().st_mtime
        return out

    # ---------- info ----------

    @property
    def title(self):
        if self.meta.get("title"):
            return self.meta["title"]
        try:
            return datetime.fromtimestamp(self.started()).strftime("%a %d %b, %H:%M")
        except Exception:
            return self.path.name

    def started(self):
        ts = self.meta.get("started")
        if ts:
            return ts
        fr = self.frames()
        return fr[0].stat().st_mtime if fr else self.path.stat().st_mtime

    def duration(self):
        stamps = self.timestamps()
        if len(stamps) < 2:
            return 0.0
        return max(stamps.values()) - min(stamps.values())

    def videos(self):
        return sorted(self.path.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)

    def size_bytes(self):
        return sum(p.stat().st_size for p in self.path.rglob("*") if p.is_file())

    def delete(self):
        shutil.rmtree(self.path)


def list_sessions(root):
    root = Path(root).expanduser()
    if not root.is_dir():
        return []
    out = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        if (d / "session.json").exists() or any((d / "frames").glob(FRAME_GLOB)) or any(d.glob(FRAME_GLOB)):
            out.append(Session.open(d))
    out.sort(key=lambda s: s.started(), reverse=True)
    return out
