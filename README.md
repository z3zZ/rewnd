# rewnd

A lightweight GUI for turning any UVC webcam into a timelapse camera on Linux. Point it at a webcam, set an interval, and it captures frames in the background and stitches them into an MP4 when you stop.

## Features

- **Start/Stop capture** on any interval, with a live preview of the latest frame
- **Auto-builds the video** (H.264 MP4) from captured frames as soon as you stop
- **Multi-camera support** — auto-detects every real capture-capable camera on the system (filters out the metadata-only device nodes UVC cameras also expose) and lists each one's actual supported resolutions, rather than a fixed list that may not match the hardware
- **Low-light boost** — many webcams leave auto-exposure fooled by backlighting, producing near-black frames. rewnd queries each camera's actual v4l2 controls at runtime and boosts whichever of `backlight_compensation`/`gain`/`brightness`/`gamma` it has, so this works across different camera models rather than being tuned to one
- **Build Video Now** — re-render a video from any previous session's captured frames at a different framerate, without recapturing
- Settings (device, resolution, interval, output FPS, save folder, low-light boost) persist between runs

## Requirements

- Python 3 with `tkinter` and `Pillow` (`python3-pil.imagetk` on Debian/Ubuntu)
- `ffmpeg` — does the actual frame capture and video encoding
- `v4l-utils` — used for camera capability/resolution detection and low-light tuning (optional: the app still works without it, just with less accurate device/resolution/tuning info)

On Debian/Ubuntu:

```
sudo apt install ffmpeg v4l-utils python3-pil.imagetk
```

## Usage

```
./run.sh
```

or launch `timelapse_app.py` directly with `python3`.

1. Pick your camera and resolution (populated automatically from what the hardware actually supports)
2. Set a capture interval in seconds and an output video framerate
3. Hit **Test Camera** to confirm framing/exposure before a long run
4. Hit **Start** — frames save to `~/Timelapses/<timestamp>/frames/`
5. Hit **Stop** whenever — the MP4 builds automatically in the same session folder

For multi-hour or multi-day timelapses: keep the laptop lid open (closing it will suspend the system on most distros, which stops capture), and make sure the save folder has enough free space for the frame count you expect.

## How frames are captured

Each tick, rewnd runs a single-frame `ffmpeg` capture against the selected `/dev/videoN` device at the chosen resolution. On stop, it runs `ffmpeg` once more over the saved JPEG sequence to produce `timelapse.mp4` with `libx264`.
