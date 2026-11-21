"""Frame pipeline shared by the live preview and the exporter.

Frames are saved raw. Orientation, colour, framing and overlays are applied here, so a
session can be re-exported with different settings without recapturing anything.
"""

from datetime import datetime
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rewnd.config import ASPECTS

FONT_DIR = Path(__file__).parent / "assets" / "fonts"
FONT_FILES = {
    "mono": "JetBrainsMono-Bold.ttf",
    "sans": "Inter-SemiBold.ttf",
    "sans-bold": "Inter-Bold.ttf",
}


# ---------- orientation and colour ----------

def orient(img, look):
    rot = int(look.get("rotate", 0)) % 360
    if rot == 90:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    elif rot == 180:
        img = cv2.rotate(img, cv2.ROTATE_180)
    elif rot == 270:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if look.get("mirror"):
        img = cv2.flip(img, 1)
    if look.get("flip"):
        img = cv2.flip(img, 0)
    return img


@lru_cache(maxsize=64)
def _tone_lut(brightness, contrast):
    x = np.arange(256, dtype=np.float32)
    c = 1.0 + contrast / 100.0          # 0..2
    x = (x - 128.0) * c + 128.0 + brightness * 1.28
    return np.clip(x, 0, 255).astype(np.uint8)


def adjust(img, look):
    b = int(look.get("brightness", 0))
    c = int(look.get("contrast", 0))
    s = int(look.get("saturation", 0))
    w = int(look.get("warmth", 0))
    if b or c:
        img = cv2.LUT(img, _tone_lut(b, c))
    if s:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * (1.0 + s / 100.0), 0, 255)
        img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    if w:
        shift = w * 0.4
        img = img.astype(np.int16)
        img[..., 2] += int(shift)       # red
        img[..., 0] -= int(shift)       # blue
        img = np.clip(img, 0, 255).astype(np.uint8)
    return img


# ---------- framing ----------

def _even(n):
    n = int(round(n))
    return max(2, n - (n % 2))


def output_size(src_w, src_h, aspect="original", size=1080):
    """Final (w, h) of the exported video for a source frame (after orientation)."""
    ratio = ASPECTS.get(aspect)
    if ratio is None:
        w, h = src_w, src_h
    else:
        r = ratio[0] / ratio[1]
        if src_w / src_h > r:
            h = src_h
            w = h * r
        else:
            w = src_w
            h = w / r
    if size:
        short = min(w, h)
        k = size / short
        if k < 1 or ratio is not None:
            w, h = w * k, h * k
    return _even(w), _even(h)


def frame_to(img, out_w, out_h, fit="crop", offset=0.0):
    """Fill an out_w x out_h canvas from img, either by cropping or by fitting with a blurred fill."""
    sh, sw = img.shape[:2]
    target = out_w / out_h
    src = sw / sh

    def cover(image):
        ih, iw = image.shape[:2]
        if iw / ih > target:
            cw = int(round(ih * target))
            slack = iw - cw
            x0 = int(round(slack / 2 + offset * slack / 2))
            x0 = max(0, min(slack, x0))
            image = image[:, x0:x0 + cw]
        else:
            ch = int(round(iw / target))
            slack = ih - ch
            y0 = int(round(slack / 2 + offset * slack / 2))
            y0 = max(0, min(slack, y0))
            image = image[y0:y0 + ch, :]
        interp = cv2.INTER_AREA if image.shape[1] > out_w else cv2.INTER_LINEAR
        return cv2.resize(image, (out_w, out_h), interpolation=interp)

    if fit != "blur" or abs(src - target) < 0.01:
        return cover(img)

    small = cv2.resize(img, (max(8, sw // 8), max(8, sh // 8)), interpolation=cv2.INTER_AREA)
    bg = cover(small)
    bg = cv2.GaussianBlur(bg, (0, 0), sigmaX=max(out_w, out_h) / 40)
    bg = (bg.astype(np.float32) * 0.55).astype(np.uint8)

    k = min(out_w / sw, out_h / sh)
    fw, fh = max(2, int(round(sw * k))), max(2, int(round(sh * k)))
    interp = cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR
    fg = cv2.resize(img, (fw, fh), interpolation=interp)
    x0, y0 = (out_w - fw) // 2, (out_h - fh) // 2
    bg[y0:y0 + fh, x0:x0 + fw] = fg
    return bg


# ---------- overlays ----------

@lru_cache(maxsize=32)
def font(kind, px):
    path = FONT_DIR / FONT_FILES[kind]
    try:
        return ImageFont.truetype(str(path), max(6, int(px)))
    except OSError:
        return ImageFont.load_default()


def hex_rgb(h, fallback=(200, 241, 53)):
    try:
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def format_elapsed(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _pill(text, kind, px, accent, opacity, dot=False, rewind_icon=False, wordmark=False, alpha=255):
    f = font(kind, px)
    pad_x, pad_y = px * 0.62, px * 0.38
    left, top, right, bottom = f.getbbox(text)
    tw = right - left
    icon_w = 0
    if dot:
        icon_w = px * 0.95
    if rewind_icon:
        icon_w = px * 1.15
    w = int(tw + pad_x * 2 + icon_w)
    h = int(px + pad_y * 2)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h / 2, fill=(13, 13, 13, int(255 * opacity)),
                        outline=(255, 255, 255, int(40 * min(1.0, opacity * 1.6))), width=max(1, int(px * 0.04)))
    x = pad_x
    cy = h / 2
    if dot:
        r = px * 0.22
        d.ellipse([x, cy - r, x + 2 * r, cy + r], fill=accent + (alpha,))
        x += icon_w
    if rewind_icon:  # round-stroke chevrons, same drawing as the app logo
        s = px * 0.28
        lw = max(2, int(px * 0.13))
        for i in range(2):
            ox = x + px * 0.08 + i * px * 0.42
            pts = [(ox + s, cy - s), (ox, cy), (ox + s, cy + s)]
            d.line(pts, fill=accent + (alpha,), width=lw, joint="curve")
            for (px_, py_) in (pts[0], pts[2]):
                d.ellipse([px_ - lw / 2, py_ - lw / 2, px_ + lw / 2, py_ + lw / 2], fill=accent + (alpha,))
        x += icon_w
    ty = (h - (bottom + top)) / 2
    if wordmark:  # "rewnd" with the vowel in lime, like the klokd/errnd wordmarks
        for part, col in (("r", (240, 240, 240)), ("e", accent), ("wnd", (240, 240, 240))):
            d.text((x - left, ty), part, font=f, fill=col + (alpha,))
            x += f.getlength(part)
        return im
    d.text((x - left, ty), text, font=f, fill=(240, 240, 240, alpha))
    return im


def _anchor(pos, w, h, W, H, margin):
    vert, horiz = pos.split("-")
    if horiz == "left":
        x = margin
    elif horiz == "right":
        x = W - w - margin
    else:
        x = (W - w) // 2
    y = margin if vert == "top" else H - h - margin
    return int(x), int(y)


def overlay_items(ov, elapsed, wall):
    """List of (position, pill-kwargs) for the enabled overlay elements, in stacking order."""
    items = []
    if ov.get("timer"):
        items.append((ov.get("timer_position", "top-left"),
                      dict(text=format_elapsed(elapsed), kind="mono", dot=True)))
    if ov.get("clock"):
        stamp = wall.strftime("%a %d %b · %H:%M") if wall else ""
        items.append((ov.get("clock_position", "top-right"), dict(text=stamp, kind="mono")))
    if ov.get("text"):
        items.append((ov.get("text_position", "bottom-left"), dict(text=ov["text"], kind="sans")))
    if ov.get("watermark"):
        items.append((ov.get("watermark_position", "bottom-right"),
                      dict(text="rewnd", kind="mono", rewind_icon=True, wordmark=True, small=True)))
    return items


def draw_overlays(img, ov, elapsed=0.0, wall=None):
    """Composite overlay pills onto a BGR frame (in place) and return it."""
    items = overlay_items(ov, elapsed, wall)
    if not items:
        return img
    H, W = img.shape[:2]
    short = min(W, H)
    base = short * 0.042 * float(ov.get("scale", 1.0))
    margin = int(short * 0.035)
    gap = int(base * 0.35)
    accent = hex_rgb(ov.get("accent", "#C8F135"))
    opacity = float(ov.get("pill_opacity", 0.55))

    stacks = {}
    for pos, kw in items:
        small = kw.pop("small", False)
        px = base * (0.72 if small else 1.0)
        pill = _pill(kw.pop("text"), kw.pop("kind"), px, accent, opacity, **kw)
        stacks.setdefault(pos, []).append(pill)

    canvas = None
    for pos, pills in stacks.items():
        offset = 0
        ordered = pills if pos.startswith("top") else list(reversed(pills))
        for pill in ordered:
            w, h = pill.size
            x, y = _anchor(pos, w, h, W, H, margin)
            y = y + offset if pos.startswith("top") else y - offset
            offset += h + gap
            if canvas is None:
                canvas = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).convert("RGBA")
            canvas.alpha_composite(pill, (max(0, x), max(0, y)))
    if canvas is not None:
        img[:] = cv2.cvtColor(np.asarray(canvas.convert("RGB")), cv2.COLOR_RGB2BGR)
    return img


# ---------- full pipeline ----------

def render(raw, look, exp, ov, elapsed=0.0, wall=None, size=None, overlays=True):
    """Raw camera frame -> finished output frame, exactly as it will appear in the video."""
    img = orient(raw, look)
    img = adjust(img, look)
    h, w = img.shape[:2]
    out_w, out_h = output_size(w, h, exp.get("aspect", "original"),
                               exp.get("size", 1080) if size is None else size)
    img = frame_to(img, out_w, out_h, exp.get("fit", "crop"), float(exp.get("crop_offset", 0.0)))
    if overlays:
        draw_overlays(img, ov, elapsed, wall)
    return img


def wall_time(ts):
    return datetime.fromtimestamp(ts) if ts else None
