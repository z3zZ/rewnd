"""Turn a session's frames into a finished MP4: pick frames, render each one, pipe to ffmpeg."""

import subprocess
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from rewnd import imaging
from rewnd.platform_utils import NO_WINDOW, ffmpeg_exe
from rewnd.session import FRAME_RE

CRF = {"high": 18, "balanced": 23, "small": 28}


class ExportCancelled(Exception):
    pass


def plan(n_frames, exp):
    """Source frame index (0-based, into the sorted frame list) for every output frame."""
    if n_frames <= 0:
        return []
    fps = max(1, int(exp.get("fps", 30)))
    if exp.get("length_mode", "target") == "target":
        wanted = max(1, int(round(float(exp.get("target_seconds", 30)) * fps)))
        return [min(n_frames - 1, int(i)) for i in np.floor(np.linspace(0, n_frames, wanted, endpoint=False))]
    return list(range(n_frames))


def describe(n_frames, exp):
    """Human summary of what an export will produce, for the UI."""
    fps = max(1, int(exp.get("fps", 30)))
    seq = plan(n_frames, exp)
    secs = len(seq) / fps + float(exp.get("hold_end", 0) or 0)
    used = len(set(seq))
    if not n_frames:
        return "No frames yet"
    if used < n_frames:
        how = f"uses {used} of {n_frames} frames"
    elif len(seq) > n_frames:
        how = f"each frame held {len(seq) / n_frames:.1f}x"
    else:
        how = f"all {n_frames} frames"
    return f"{secs:.1f}s at {fps} fps · {how}"


def output_name(exp):
    aspect = exp.get("aspect", "original").replace(":", "x")
    if exp.get("length_mode", "target") == "target":
        return f"rewnd_{aspect}_{int(round(float(exp.get('target_seconds', 30))))}s.mp4"
    return f"rewnd_{aspect}_{int(exp.get('fps', 30))}fps.mp4"


def export(session, look, exp, ov, out_path=None, progress=None, cancel: threading.Event = None):
    """Render session -> MP4. Returns the output path. Raises on failure or ExportCancelled."""
    frames = session.frames()
    if not frames:
        raise RuntimeError("This session has no frames yet.")
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg wasn't found. Reinstall the requirements (pip install -r requirements.txt).")

    stamps = session.timestamps()

    def ts_of(path):
        m = FRAME_RE.search(path.name)
        return stamps.get(int(m.group(1))) if m else None

    t0 = min((t for t in (ts_of(p) for p in frames) if t is not None), default=None)
    fps = max(1, int(exp.get("fps", 30)))
    seq = plan(len(frames), exp)
    hold = int(round(float(exp.get("hold_end", 0) or 0) * fps))
    total = len(seq) + hold

    first = cv2.imread(str(frames[seq[0]]))
    if first is None:
        raise RuntimeError(f"Couldn't read {frames[seq[0]].name}.")
    probe = imaging.render(first, look, exp, ov, overlays=False)
    H, W = probe.shape[:2]

    out_path = Path(out_path) if out_path else session.path / output_name(exp)
    cmd = [exe, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
    if exp.get("deflicker", True):
        cmd += ["-vf", "deflicker=size=5:mode=pm"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(CRF.get(exp.get("quality", "high"), 18)),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, creationflags=NO_WINDOW)

    def render_one(i):
        path = frames[i]
        raw = cv2.imread(str(path))
        if raw is None:
            raw = first
        ts = ts_of(path)
        elapsed = (ts - t0) if (ts is not None and t0 is not None) else 0.0
        img = imaging.render(raw, look, exp, ov, elapsed=elapsed, wall=imaging.wall_time(ts))
        if img.shape[0] != H or img.shape[1] != W:
            img = cv2.resize(img, (W, H))
        return np.ascontiguousarray(img).tobytes()

    # Consecutive repeats of the same source frame are rendered once and written several times.
    runs = []
    for idx in seq:
        if runs and runs[-1][0] == idx:
            runs[-1][1] += 1
        else:
            runs.append([idx, 1])

    written = 0
    last = None
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = deque()
            it = iter(runs)
            for _ in range(8):
                nxt = next(it, None)
                if nxt is None:
                    break
                pending.append((pool.submit(render_one, nxt[0]), nxt[1]))
            while pending:
                if cancel is not None and cancel.is_set():
                    raise ExportCancelled()
                fut, count = pending.popleft()
                last = fut.result()
                for _ in range(count):
                    proc.stdin.write(last)
                written += count
                if progress:
                    progress(written, total)
                nxt = next(it, None)
                if nxt is not None:
                    pending.append((pool.submit(render_one, nxt[0]), nxt[1]))
        for _ in range(hold):
            if cancel is not None and cancel.is_set():
                raise ExportCancelled()
            proc.stdin.write(last)
            written += 1
            if progress and written % 5 == 0:
                progress(written, total)
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait() != 0:
            raise RuntimeError(err.strip().splitlines()[-1] if err.strip() else "ffmpeg failed")
    except (ExportCancelled, BrokenPipeError, OSError) as e:
        try:
            proc.kill()
        except Exception:
            pass
        out_path.unlink(missing_ok=True)
        if isinstance(e, ExportCancelled):
            raise
        err = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
        raise RuntimeError(err.strip().splitlines()[-1] if err.strip() else str(e))
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        out_path.unlink(missing_ok=True)
        raise
    if progress:
        progress(total, total)
    return out_path
