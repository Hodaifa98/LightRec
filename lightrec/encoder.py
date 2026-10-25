"""ffmpeg: find, download, encode and mux"""
from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from typing import Callable, Optional

from . import paths

IS_WINDOWS = sys.platform == "win32"
EXE = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"

QUALITY_PRESETS = {
    "Low": ("ultrafast", 30),
    "Medium": ("veryfast", 23),
    "High": ("faster", 18),
}


class EncodeError(RuntimeError):
    pass


def _popen_kwargs() -> dict:
    kwargs: dict = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return kwargs


def _usable(path: Optional[str]) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        proc = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10, **_popen_kwargs())
        return proc.returncode == 0 and "ffmpeg" in (proc.stdout or "").lower()
    except (OSError, subprocess.SubprocessError):
        return False


def find_ffmpeg() -> Optional[str]:
    """
    Find ffmpeg (env var, app folder, PATH, imageio-ffmpeg, common install paths)
    """
    candidates: list[Optional[str]] = [os.environ.get("LIGHTREC_FFMPEG")]
    for base in (paths.bin_dir(), paths.app_dir(), os.path.join(paths.app_dir(), "ffmpeg", "bin")):
        candidates.append(os.path.join(base, EXE))
    candidates.append(shutil.which("ffmpeg"))
    try:
        import imageio_ffmpeg
        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    if IS_WINDOWS:
        candidates += [r"C:\ffmpeg\bin\ffmpeg.exe", os.path.expandvars(r"%ProgramData%\chocolatey\bin\ffmpeg.exe"),
                       os.path.expandvars(r"%LOCALAPPDATA%\Programs\ffmpeg\bin\ffmpeg.exe")]
    else:
        candidates += ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "/snap/bin/ffmpeg"]
    for cand in candidates:
        if _usable(cand):
            return os.path.abspath(cand)
    return None


_encoder_cache: dict[tuple[str, str], bool] = {}


def has_encoder(ffmpeg: str, name: str) -> bool:
    key = (ffmpeg, name)
    if key not in _encoder_cache:
        try:
            proc = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                  timeout=15, **_popen_kwargs())
            _encoder_cache[key] = any(line.split()[1:2] == [name] for line in proc.stdout.splitlines())
        except (OSError, subprocess.SubprocessError):
            _encoder_cache[key] = False
    return _encoder_cache[key]


def version_string(ffmpeg: str) -> str:
    try:
        proc = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10, **_popen_kwargs())
        first = (proc.stdout or "").splitlines()[0]
        return first.replace("ffmpeg version", "ffmpeg").split(" Copyright")[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return "ffmpeg"


# Download.
def download_url() -> Optional[str]:
    machine = platform.machine().lower()
    if IS_WINDOWS:
        return "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    if sys.platform == "darwin":
        return "https://evermeet.cx/ffmpeg/getrelease/zip"
    if sys.platform.startswith("linux"):
        arch = "arm64" if machine in ("aarch64", "arm64") else "amd64"
        return f"https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{arch}-static.tar.xz"
    return None


def download_ffmpeg(progress: Callable[[float, str], None], cancelled: Callable[[], bool]) -> str:
    """
    Download a static ffmpeg into the bin folder and return its path
    """
    url = download_url()
    if not url:
        raise EncodeError("Automatic download is not available for this system.")
    dest_dir = paths.bin_dir()
    os.makedirs(dest_dir, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.basename(url)[-8:], dir=dest_dir)
    tmp.close()
    try:
        progress(0.0, "Connecting…")
        req = urllib.request.Request(url, headers={"User-Agent": "LightRec/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(tmp.name, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancelled():
                    raise EncodeError("Download cancelled.")
                block = resp.read(256 * 1024)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if total:
                    progress(done / total, f"Downloading… {done / 1e6:.1f} / {total / 1e6:.1f} MB")
                else:
                    progress(-1.0, f"Downloading… {done / 1e6:.1f} MB")
        progress(1.0, "Extracting…")
        target = os.path.join(dest_dir, EXE)
        _extract_binary(tmp.name, target)
        if not IS_WINDOWS:
            os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if not _usable(target):
            raise EncodeError("The downloaded ffmpeg could not be started.")
        return target
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def _extract_binary(archive: str, target: str) -> None:
    wanted = {"ffmpeg", "ffmpeg.exe"}
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                if os.path.basename(member.filename) in wanted and not member.is_dir():
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    return
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if member.isfile() and os.path.basename(member.name) in wanted:
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    with src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    return
    raise EncodeError("The downloaded archive does not contain an ffmpeg binary.")


# Encoding.
def _tail(log_path: Optional[str], lines: int = 6) -> str:
    if not log_path or not os.path.isfile(log_path):
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            return "\n".join(fh.read().splitlines()[-lines:]).strip()
    except OSError:
        return ""


class VideoWriter:
    """
    Pipe raw BGRA frames into ffmpeg to produce an H.264 MP4
    """

    def __init__(self, ffmpeg: str, path: str, width: int, height: int, fps: int, quality: str, log_path: str):
        preset, crf = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["Medium"])
        if has_encoder(ffmpeg, "libx264"):
            codec = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
        # Builds without x264 fall back to MPEG-4.
        else:
            codec = ["-c:v", "mpeg4", "-q:v", {"Low": "8", "Medium": "5", "High": "3"}.get(quality, "5"),
                     "-pix_fmt", "yuv420p"]
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgra", "-video_size", f"{width}x{height}",
                "-framerate", str(fps), "-i", "pipe:0", "-an", *codec, "-movflags", "+faststart", path]
        self.path = path
        self.log_path = log_path
        self.frames = 0
        self._log = open(log_path, "w", encoding="utf-8")
        try:
            self._proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=self._log, bufsize=0, **_popen_kwargs())
        except OSError as exc:
            self._log.close()
            raise EncodeError(f"Could not start ffmpeg: {exc}") from exc

    def write(self, frame: bytes) -> None:
        try:
            self._proc.stdin.write(frame)
            self.frames += 1
        except (BrokenPipeError, OSError) as exc:
            raise EncodeError(f"ffmpeg stopped accepting frames: {_tail(self.log_path) or exc}") from exc

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            code = self._proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            code = -1
        self._log.close()
        if code != 0:
            raise EncodeError(f"ffmpeg failed while finishing the video (code {code}).\n{_tail(self.log_path)}")

    def abort(self) -> None:
        try:
            self._proc.kill()
        except OSError:
            pass
        try:
            self._log.close()
        except OSError:
            pass


def mux(ffmpeg: str, video: str, audio: list[str], output: str, log_path: str) -> None:
    """
    Combine the video with one or more WAV tracks into the output file
    """
    inputs = ["-i", video]
    for track in audio:
        inputs += ["-i", track]
    mapping: list[str]
    if len(audio) == 1:
        mapping = ["-map", "0:v:0", "-map", "1:a:0"]
        filters: list[list[str]] = [[]]
    else:
        chains = "".join(f"[{i + 1}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}];" for i in range(len(audio)))
        joined = "".join(f"[a{i}]" for i in range(len(audio)))
        with_norm = chains + joined + f"amix=inputs={len(audio)}:duration=longest:dropout_transition=0:normalize=0[aout]"
        without = chains + joined + f"amix=inputs={len(audio)}:duration=longest:dropout_transition=0,volume={len(audio)}[aout]"
        filters = [["-filter_complex", with_norm], ["-filter_complex", without]]
        mapping = ["-map", "0:v:0", "-map", "[aout]"]
    last_error = ""
    for flt in filters:
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *inputs, *flt, *mapping,
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-shortest", "-movflags", "+faststart", output]
        with open(log_path, "w", encoding="utf-8") as log:
            try:
                code = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=log, timeout=3600,
                                      **_popen_kwargs()).returncode
            except (OSError, subprocess.SubprocessError) as exc:
                raise EncodeError(f"Could not run ffmpeg: {exc}") from exc
        if code == 0 and os.path.isfile(output) and os.path.getsize(output) > 0:
            return
        last_error = _tail(log_path)
    raise EncodeError(f"Combining audio and video failed.\n{last_error}")
