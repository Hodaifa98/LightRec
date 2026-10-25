# LightRec

A lightweight, portable screen recorder. One small window, no account, no installer.

- **Record the full screen, an area you drag out, or a single window.**
- **Hide windows from the recording** (your notes, a chat, a password manager…). On Windows the
  hidden window is replaced by whatever is behind it, so viewers never know it was there.
- **Built-in Notes panel** for your script. On Windows it is invisible to the recording even when it
  floats on top of what you're recording.
- **Microphone and system audio**, each with a live level meter, mixed into the video.
- Pause / resume, a floating control bar, a 3-second countdown, global shortcuts
  (`Ctrl+Shift+R` start/stop, `Ctrl+Shift+P` pause), the real mouse cursor, 15–60 fps, three
  quality levels. Output is a standard H.264 / AAC `.mp4`.

## Run it

Double-click `run.bat` (Windows) or run `./run.sh` (Linux / macOS). The first start creates a
private `.venv` folder next to the app and installs the few packages LightRec needs; later starts
are instant. Or do it by hand:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt    # Linux/macOS: .venv/bin/python
.venv\Scripts\python LightRec.py
```

If you start `LightRec.py` with a Python that lacks the packages, the window still opens but the
audio section explains what to install.

Python 3.9+ with Tk (included in the standard Windows and macOS installers; on Linux install
`python3-tk`). On first recording LightRec looks for `ffmpeg`; if it isn't on your system it offers
to download a portable copy into its own `bin/` folder, or you can point it at an existing one
(`ffmpeg` on the PATH, `bin/ffmpeg(.exe)` next to the app, or the `LIGHTREC_FFMPEG` env var).

Settings are stored in `lightrec_settings.json` next to the app (or in `~/.lightrec` when the app
folder is read-only), so a copy on a USB stick keeps its preferences.

## Build a portable executable

```
python -m pip install pyinstaller
python build.py
```

`dist/LightRec.exe` (or `dist/LightRec` on other systems) is self-contained apart from ffmpeg,
which it downloads on demand or finds next to itself.

## How it works

| Piece | Windows | Linux / macOS |
| --- | --- | --- |
| Screen capture | GDI via `ctypes` (no extra packages) | `mss` |
| Window capture | `PrintWindow` renders the window itself, so windows covering it are not recorded (falls back to its screen area for exclusive-fullscreen apps) | The window's screen area; overlapping windows stay visible |
| Hide a window | Repainted with the windows beneath it (painter's algorithm over the z-order) | Covered with a solid block |
| LightRec's own windows / Notes | Excluded from capture by the OS (`WDA_EXCLUDEFROMCAPTURE`, Windows 10 2004+) | Visible if inside the recorded area; "Hide LightRec while recording" is on by default |
| Microphone | WASAPI via `pyaudiowpatch` (falls back to `sounddevice`) | `sounddevice` |
| System audio | WASAPI loopback via `pyaudiowpatch` | PulseAudio / PipeWire monitor sources (Linux, needs `pactl`); a virtual device such as BlackHole on macOS |
| Window list | built in | `wmctrl` on X11, `pyobjc-framework-Quartz` on macOS |
| Encoding | `ffmpeg` (libx264 + AAC), frames piped as raw BGRA | same |

Frames are paced against the wall clock (late frames are duplicated, never dropped silently) and
the audio writer pads silence whenever a device goes quiet, so audio and video stay in sync even
on long recordings and across pauses.

Wayland sessions are not supported for capture (neither `mss` nor X11 window listing work there).

## Layout

```
LightRec.py              entry point
lightrec/
  capture.py             frame sources (Windows GDI / mss), monitor + window listing
  winapi.py              Windows: grabbers, hidden-window reconstruction, cursor, hotkeys
  audio.py               device discovery, WAV recorder with clock alignment
  encoder.py             ffmpeg discovery/download, raw-frame encoder, audio mux
  session.py             a recording: pacing, pause, finalize
  settings.py            persisted preferences
  ui/                    Tk interface: theme, widgets, overlays, main window
build.py                 PyInstaller one-file build
```
