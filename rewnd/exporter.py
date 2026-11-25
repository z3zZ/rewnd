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

# Smooth motion, like Skyflow's: each video frame blends the shots around it instead of showing one.
# The width is in output frames: 1.0 means each frame's blend just reaches its neighbours' centres.
SMOOTH_WIDTH = {"off": 0.0, "light": 1.25, "strong": 2.0}
MAX_BLEND = 16            # most shots blended into one frame; wider windows are sampled evenly


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


def _positions(n_frames, exp):
    """Fractional source position of every output frame, and source frames per output frame."""
    fps = max(1, int(exp.get("fps", 30)))
    if exp.get("length_mode", "target") == "target":
        wanted = max(1, int(round(float(exp.get("target_seconds", 30)) * fps)))
        step = n_frames / wanted
        return [min(n_frames - 1.0, i * step) for i in range(wanted)], step
    return [float(i) for i in range(n_frames)], 1.0


def plan_samples(n_frames, exp):
    """For every output frame, the source frames to blend and their weights, as ((idx, w), ...).

    With smooth motion off this is the same as plan(): one frame each. With it on, a triangular
    window centred on the output frame's position in the session blends the neighbouring shots.
    When the video speeds a session up, that's motion blur, so movement flows instead of
    stuttering. When it slows down (each shot shown for several frames), it crossfades between
    shots instead of holding and jumping. Either way it also averages away sensor noise.
    """
    if n_frames <= 0:
        return []
    width = SMOOTH_WIDTH.get(exp.get("smooth", "off"), 0.0)
    if width <= 0:
        return [((i, 1.0),) for i in plan(n_frames, exp)]
    positions, step = _positions(n_frames, exp)
    half = width * max(1.0, step)
    out = []
    for pos in positions:
        lo = max(0, int(np.ceil(pos - half + 1e-9)))
        hi = min(n_frames - 1, int(np.floor(pos + half - 1e-9)))
        idxs = range(lo, hi + 1)
        if len(idxs) > MAX_BLEND:
            idxs = set(int(round(x)) for x in np.linspace(lo, hi, MAX_BLEND - 1))
            idxs.add(min(n_frames - 1, int(round(pos))))
            idxs = sorted(idxs)
        pairs = [(i, 1.0 - abs(i - pos) / half) for i in idxs]
        pairs = [(i, w) for i, w in pairs if w > 1e-3]
        if not pairs:
            pairs = [(min(n_frames - 1, int(round(pos))), 1.0)]
        total = sum(w for _, w in pairs)
        out.append(tuple((i, round(w / total, 4)) for i, w in pairs))
    return out


def blend(images, weights):
    """Weighted average of same-sized uint8 frames."""
    if len(images) == 1:
        return images[0]
    acc = np.zeros(images[0].shape, np.float32)
    for img, w in zip(images, weights):
        acc = cv2.scaleAdd(img.astype(np.float32), float(w), acc)
    return np.clip(acc + 0.5, 0, 255).astype(np.uint8)


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
    if SMOOTH_WIDTH.get(exp.get("smooth", "off"), 0) > 0:
        how += " · smooth motion"
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
    samples = plan_samples(len(frames), exp)
    seq = [max(smp, key=lambda p: p[1])[0] for smp in samples]
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

    # Each shot is rendered (orientation, colour, framing) once and kept for a while, because
    # smooth motion blends every shot into several neighbouring video frames.
    cache, cache_lock, cache_size = {}, threading.Lock(), 48

    def base(i):
        with cache_lock:
            img = cache.get(i)
        if img is not None:
            return img
        raw = cv2.imread(str(frames[i]))
        if raw is None:
            raw = first
        img = imaging.render(raw, look, exp, ov, overlays=False)
        if img.shape[0] != H or img.shape[1] != W:
            img = cv2.resize(img, (W, H))
        with cache_lock:
            cache[i] = img
            while len(cache) > cache_size:
                cache.pop(next(iter(cache)))
        return img

    def render_one(smp):
        img = blend([base(i) for i, _ in smp], [w for _, w in smp]).copy()
        stamps_w = [(ts_of(frames[i]), w) for i, w in smp]
        stamps_w = [(t, w) for t, w in stamps_w if t is not None]
        if stamps_w and t0 is not None:
            ts = sum(t * w for t, w in stamps_w) / sum(w for _, w in stamps_w)
            elapsed = ts - t0
        else:
            ts, elapsed = None, 0.0
        imaging.draw_overlays(img, ov, elapsed, imaging.wall_time(ts))
        return np.ascontiguousarray(img).tobytes()

    # Consecutive identical frames are rendered once and written several times.
    runs = []
    for smp in samples:
        if runs and runs[-1][0] == smp:
            runs[-1][1] += 1
        else:
            runs.append([smp, 1])

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
