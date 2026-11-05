#!/usr/bin/env python3
"""Timelapse capture GUI for UVC webcams, using ffmpeg to grab frames and build the final video."""

import json
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

CONFIG_PATH = Path.home() / ".config" / "timelapse-app" / "config.json"
DEFAULT_OUTPUT_ROOT = Path.home() / "Timelapses"
FALLBACK_RESOLUTIONS = ["1920x1080", "1280x720", "640x480"]


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_config(data):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2))


def apply_camera_controls(device, controls):
    """Best-effort v4l2-ctl tuning; silently no-ops if v4l2-ctl or the control isn't available."""
    if not controls or shutil.which("v4l2-ctl") is None:
        return
    subprocess.run(
        ["v4l2-ctl", "--device", device, "--set-ctrl", controls],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def query_int_controls(device):
    """Return {control_name: {'min':, 'max':, 'default':, ...}} for whatever this camera
    actually exposes — different UVC cameras have very different control sets/ranges, so
    tuning must be computed per-device rather than hardcoded."""
    if shutil.which("v4l2-ctl") is None:
        return {}
    result = subprocess.run(
        ["v4l2-ctl", "--device", device, "--list-ctrls"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    controls = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or "0x" not in line or ":" not in line:
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
        if "min" in kv and "max" in kv:
            controls[name] = kv
    return controls


def build_low_light_controls(device):
    """Boost whichever brightening controls this specific camera actually has."""
    controls = query_int_controls(device)
    parts = []
    if "backlight_compensation" in controls:
        c = controls["backlight_compensation"]
        parts.append(f"backlight_compensation={c['max']}")
    if "gain" in controls:
        c = controls["gain"]
        target = round(c["min"] + 0.7 * (c["max"] - c["min"]))
        parts.append(f"gain={target}")
    if "brightness" in controls:
        c = controls["brightness"]
        base = c.get("default", (c["min"] + c["max"]) // 2)
        target = min(c["max"], round(base + 0.35 * (c["max"] - base)))
        parts.append(f"brightness={target}")
    if "gamma" in controls:
        c = controls["gamma"]
        base = c.get("default", (c["min"] + c["max"]) // 2)
        target = min(c["max"], round(base + 0.25 * (c["max"] - base)))
        parts.append(f"gamma={target}")
    return ",".join(parts)


def build_auto_controls(device):
    """Reset the same controls build_low_light_controls touches back to their defaults."""
    controls = query_int_controls(device)
    parts = []
    for name in ("backlight_compensation", "gain", "brightness", "gamma"):
        if name in controls and "default" in controls[name]:
            parts.append(f"{name}={controls[name]['default']}")
    return ",".join(parts)


def list_device_resolutions(device):
    if shutil.which("v4l2-ctl") is None:
        return list(FALLBACK_RESOLUTIONS)
    result = subprocess.run(
        ["v4l2-ctl", "--device", device, "--list-formats-ext"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    sizes = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("Size: Discrete"):
            sizes.add(line.split()[-1])
    if not sizes:
        return list(FALLBACK_RESOLUTIONS)

    def area(s):
        w, h = s.split("x")
        return int(w) * int(h)

    return sorted(sizes, key=area, reverse=True)


class TimelapseApp:
    def __init__(self, root):
        self.root = root
        root.title("Timelapse")

        self.cfg = load_config()
        self.capturing = False
        self.stop_event = threading.Event()
        self.capture_thread = None
        self.msg_queue = queue.Queue()
        self.frame_count = 0
        self.session_dir = None
        self.start_time = None
        self.settings_snapshot = None
        self.preview_image = None  # keep a reference so Tk doesn't garbage-collect it

        self._build_ui()
        root.update_idletasks()
        req_w = max(root.winfo_reqwidth(), 600)
        req_h = max(root.winfo_reqheight(), 480)
        root.geometry(f"{req_w}x{req_h}")
        root.minsize(req_w, req_h)
        self._poll_queue()

        if shutil.which("ffmpeg") is None:
            messagebox.showwarning(
                "ffmpeg not found",
                "ffmpeg isn't installed yet. Install it with:\n\n"
                "sudo apt install ffmpeg\n\n"
                "The app will open, but capture and video building will fail until it's installed.",
            )

    # ---------- UI ----------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        frm_settings = ttk.LabelFrame(self.root, text="Settings")
        frm_settings.pack(fill="x", **pad)
        frm_settings.columnconfigure(1, weight=1)

        row = 0

        ttk.Label(frm_settings, text="Camera device:").grid(row=row, column=0, sticky="w", **pad)
        devices = self._detect_devices() or ["/dev/video0"]
        default_device = self.cfg.get("device", devices[0])
        if default_device not in devices:
            default_device = devices[0]
        self.device_var = tk.StringVar(value=default_device)
        self.device_combo = ttk.Combobox(
            frm_settings, textvariable=self.device_var, values=devices, width=14, state="readonly"
        )
        self.device_combo.grid(row=row, column=1, sticky="w", **pad)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_changed)
        row += 1

        ttk.Label(frm_settings, text="Resolution:").grid(row=row, column=0, sticky="w", **pad)
        resolutions = list_device_resolutions(default_device)
        default_res = self.cfg.get("resolution")
        if default_res not in resolutions:
            default_res = resolutions[0]  # highest resolution this device supports
        self.res_var = tk.StringVar(value=default_res)
        self.res_combo = ttk.Combobox(
            frm_settings, textvariable=self.res_var, values=resolutions, width=14, state="readonly"
        )
        self.res_combo.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Label(frm_settings, text="Interval (seconds):").grid(row=row, column=0, sticky="w", **pad)
        self.interval_var = tk.StringVar(value=str(self.cfg.get("interval", 10)))
        ttk.Entry(frm_settings, textvariable=self.interval_var, width=10).grid(
            row=row, column=1, sticky="w", **pad
        )
        row += 1

        ttk.Label(frm_settings, text="Output video FPS:").grid(row=row, column=0, sticky="w", **pad)
        self.fps_var = tk.StringVar(value=str(self.cfg.get("fps", 24)))
        ttk.Entry(frm_settings, textvariable=self.fps_var, width=10).grid(
            row=row, column=1, sticky="w", **pad
        )
        row += 1

        ttk.Label(frm_settings, text="Save to:").grid(row=row, column=0, sticky="w", **pad)
        self.outdir_var = tk.StringVar(value=self.cfg.get("output_root", str(DEFAULT_OUTPUT_ROOT)))
        ttk.Entry(frm_settings, textvariable=self.outdir_var).grid(
            row=row, column=1, sticky="ew", **pad
        )
        ttk.Button(frm_settings, text="Browse...", command=self._choose_dir).grid(
            row=row, column=2, sticky="w", padx=(0, 8), pady=6
        )
        row += 1

        self.lowlight_var = tk.BooleanVar(value=self.cfg.get("low_light_boost", True))
        ttk.Checkbutton(
            frm_settings,
            text="Boost low light (recommended — fixes backlit/dim shots)",
            variable=self.lowlight_var,
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))
        row += 1

        # Controls and status are packed before the preview (and the preview is capped in size)
        # so they can never be pushed off an unresizable window once a captured photo enlarges
        # the preview area.
        frm_controls = ttk.Frame(self.root)
        frm_controls.pack(fill="x", **pad)
        self.test_btn = ttk.Button(frm_controls, text="Test Camera", command=self._test_camera)
        self.test_btn.pack(side="left", padx=4)
        self.start_btn = ttk.Button(frm_controls, text="Start", command=self._start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(frm_controls, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.build_btn = ttk.Button(
            frm_controls, text="Build Video Now...", command=self._build_video_manual
        )
        self.build_btn.pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", wraplength=580).pack(
            fill="x", padx=12, pady=(0, 8)
        )

        frm_preview = ttk.LabelFrame(self.root, text="Preview")
        frm_preview.pack(fill="both", expand=True, **pad)
        self.preview_label = ttk.Label(frm_preview, text="No preview yet", anchor="center")
        self.preview_label.pack(fill="both", expand=True, padx=8, pady=8)

    def _detect_devices(self):
        candidates = sorted(str(p) for p in Path("/dev").glob("video*"))
        if shutil.which("v4l2-ctl") is None:
            return candidates
        capture_devices = [d for d in candidates if self._supports_video_capture(d)]
        return capture_devices or candidates

    def _supports_video_capture(self, device):
        # "Capabilities" lists what the whole USB device can do; "Device Caps" lists what this
        # specific /dev/videoN node exposes (UVC splits capture and metadata onto separate nodes).
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, "--all"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        in_device_caps = False
        for line in result.stdout.splitlines():
            if "Device Caps" in line:
                in_device_caps = True
                continue
            if in_device_caps:
                if not line.startswith("\t\t"):
                    break
                if "Video Capture" in line:
                    return True
        return False

    def _on_device_changed(self, _event=None):
        resolutions = list_device_resolutions(self.device_var.get())
        self.res_combo.config(values=resolutions)
        if self.res_var.get() not in resolutions:
            self.res_var.set(resolutions[0])

    def _choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.outdir_var.get() or str(Path.home()))
        if d:
            self.outdir_var.set(d)

    # ---------- settings ----------

    def _current_settings(self):
        try:
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError
        except ValueError:
            raise ValueError("Interval must be a positive number of seconds.")
        try:
            fps = float(self.fps_var.get())
            if fps <= 0:
                raise ValueError
        except ValueError:
            raise ValueError("Output video FPS must be a positive number.")
        return {
            "device": self.device_var.get(),
            "resolution": self.res_var.get(),
            "interval": interval,
            "fps": fps,
            "output_root": self.outdir_var.get(),
            "low_light_boost": self.lowlight_var.get(),
        }

    # ---------- capture ----------

    def _capture_frame(self, device, resolution, dest_path):
        cmd = [
            "ffmpeg", "-y",
            "-f", "v4l2",
            "-video_size", resolution,
            "-i", device,
            "-frames:v", "1",
            str(dest_path),
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return result.returncode == 0, result.stderr.decode(errors="ignore")

    def _test_camera(self):
        if shutil.which("ffmpeg") is None:
            messagebox.showerror("ffmpeg missing", "Install ffmpeg first:\nsudo apt install ffmpeg")
            return
        try:
            settings = self._current_settings()
        except ValueError as e:
            messagebox.showerror("Invalid setting", str(e))
            return
        self.test_btn.config(state="disabled")
        self.status_var.set("Testing camera...")

        def worker():
            apply_camera_controls(
                settings["device"],
                build_low_light_controls(settings["device"])
                if settings["low_light_boost"]
                else build_auto_controls(settings["device"]),
            )
            tmp = Path("/tmp/timelapse_test_frame.jpg")
            ok, err = self._capture_frame(settings["device"], settings["resolution"], tmp)
            self.msg_queue.put(("test_result", ok, err, tmp))

        threading.Thread(target=worker, daemon=True).start()

    def _start(self):
        if shutil.which("ffmpeg") is None:
            messagebox.showerror("ffmpeg missing", "Install ffmpeg first:\nsudo apt install ffmpeg")
            return
        try:
            settings = self._current_settings()
        except ValueError as e:
            messagebox.showerror("Invalid setting", str(e))
            return

        save_config(settings)
        apply_camera_controls(
            settings["device"],
            build_low_light_controls(settings["device"])
            if settings["low_light_boost"]
            else build_auto_controls(settings["device"]),
        )

        session_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_dir = Path(settings["output_root"]).expanduser() / session_name
        (self.session_dir / "frames").mkdir(parents=True, exist_ok=True)

        self.frame_count = 0
        self.start_time = time.time()
        self.stop_event.clear()
        self.settings_snapshot = settings
        self.capturing = True

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.test_btn.config(state="disabled")
        self.status_var.set(f"Capturing to {self.session_dir} ...")

        self.capture_thread = threading.Thread(target=self._capture_loop, args=(settings,), daemon=True)
        self.capture_thread.start()

    def _capture_loop(self, settings):
        device = settings["device"]
        resolution = settings["resolution"]
        interval = settings["interval"]
        frames_dir = self.session_dir / "frames"

        while not self.stop_event.is_set():
            tick_start = time.time()
            idx = self.frame_count + 1
            dest = frames_dir / f"frame_{idx:06d}.jpg"
            ok, err = self._capture_frame(device, resolution, dest)
            if ok:
                self.frame_count = idx
                self.msg_queue.put(("frame", idx, dest))
            else:
                self.msg_queue.put(("error", err))

            elapsed = time.time() - tick_start
            remaining = interval - elapsed
            if remaining > 0:
                self.stop_event.wait(remaining)

        self.msg_queue.put(("stopped",))

    def _stop(self):
        if not self.capturing:
            return
        self.status_var.set("Stopping...")
        self.stop_event.set()
        self.capturing = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.test_btn.config(state="normal")

    # ---------- video building ----------

    def _build_video_async(self, frames_dir, out_dir, fps):
        def worker():
            frames = sorted(Path(frames_dir).glob("frame_*.jpg"))
            if not frames:
                self.msg_queue.put(("video_done", False, None, "No frames found to build a video from."))
                return
            out_path = Path(out_dir) / "timelapse.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-framerate", str(fps),
                "-i", str(Path(frames_dir) / "frame_%06d.jpg"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(out_path),
            ]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            ok = result.returncode == 0 and out_path.exists()
            self.msg_queue.put(("video_done", ok, out_path, result.stderr.decode(errors="ignore")))

        threading.Thread(target=worker, daemon=True).start()

    def _build_video_manual(self):
        if shutil.which("ffmpeg") is None:
            messagebox.showerror("ffmpeg missing", "Install ffmpeg first:\nsudo apt install ffmpeg")
            return
        d = filedialog.askdirectory(
            title="Select a timelapse session folder (containing a frames/ subfolder)",
            initialdir=self.outdir_var.get(),
        )
        if not d:
            return
        session_dir = Path(d)
        frames_dir = session_dir / "frames"
        if not frames_dir.exists():
            frames_dir = session_dir  # allow pointing directly at a frames folder
        try:
            fps = float(self.fps_var.get())
        except ValueError:
            fps = 24
        self.status_var.set("Building video...")
        self._build_video_async(frames_dir, session_dir, fps)

    # ---------- queue / preview ----------

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "frame":
                    _, idx, dest = msg
                    elapsed = time.time() - self.start_time
                    self.status_var.set(
                        f"Captured frame {idx} ({elapsed / 60:.1f} min elapsed) -> {self.session_dir}"
                    )
                    self._show_preview(dest)
                elif kind == "error":
                    last_line = msg[1].strip().splitlines()[-1] if msg[1] else "unknown error"
                    self.status_var.set(f"Capture error: {last_line}")
                elif kind == "stopped":
                    if self.frame_count > 0:
                        self.status_var.set(f"Stopped. {self.frame_count} frames captured. Building video...")
                        self._build_video_async(
                            self.session_dir / "frames", self.session_dir, self.settings_snapshot["fps"]
                        )
                    else:
                        self.status_var.set("Stopped. No frames were captured — check the camera settings.")
                elif kind == "test_result":
                    _, ok, err, tmp = msg
                    self.test_btn.config(state="normal")
                    if ok:
                        self.status_var.set("Camera OK.")
                        self._show_preview(tmp)
                    else:
                        self.status_var.set("Camera test failed.")
                        messagebox.showerror("Camera test failed", err[-800:] if err else "Unknown error")
                elif kind == "video_done":
                    _, ok, out_path, err = msg
                    if ok:
                        self.status_var.set(f"Video ready: {out_path}")
                        messagebox.showinfo("Video built", f"Saved timelapse video to:\n{out_path}")
                    else:
                        self.status_var.set("Video build failed.")
                        messagebox.showerror("Video build failed", err[-800:] if err else "Unknown error")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _show_preview(self, image_path):
        try:
            img = Image.open(image_path)
            img.thumbnail((440, 260))
            self.preview_image = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self.preview_image, text="")
        except Exception:
            pass

    def on_close(self):
        if self.capturing:
            if not messagebox.askyesno("Capture running", "A capture is in progress. Stop and exit?"):
                return
            self._stop()
        self.root.destroy()


def main():
    root = tk.Tk(className="Timelapse")
    app = TimelapseApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
