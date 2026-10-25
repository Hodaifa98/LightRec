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
import threading
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
# GPU encoders in the order we prefer them, with the name shown in the UI.
HW_ENCODERS = (
    ("h264_nvenc", "NVIDIA NVENC"),
    ("h264_amf", "AMD AMF"),
    ("h264_qsv", "Intel Quick Sync"),
)
ENCODER_LABELS = {"libx264": "CPU (x264)", **dict(HW_ENCODERS)}
# Constant-quality values for the GPU encoders (lower is better, like crf).
HW_QUALITY = {"Low": 28, "Medium": 23, "High": 19}


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


_probe_cache: dict[str, list[str]] = {}
_probe_lock = threading.Lock()


def _test_encode(ffmpeg: str, name: str) -> bool:
    """
    Encode three tiny black frames, because a listed GPU encoder still fails without the GPU
    """
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", "bgra",
            "-video_size", "256x256", "-framerate", "30", "-i", "pipe:0", "-frames:v", "3", "-c:v", name,
            "-pix_fmt", "yuv420p", "-f", "null", "-"]
    try:
        proc = subprocess.run(args, input=bytes(256 * 256 * 4 * 3), capture_output=True, timeout=20, **_popen_kwargs())
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def probe_encoders(ffmpeg: str) -> list[str]:
    """
    Encoders that actually work on this machine, GPU ones first (takes a second or two, cached)
    """
    with _probe_lock:
        if ffmpeg not in _probe_cache:
            working = [name for name, _label in HW_ENCODERS if has_encoder(ffmpeg, name) and _test_encode(ffmpeg, name)]
            if has_encoder(ffmpeg, "libx264"):
                working.append("libx264")
            _probe_cache[ffmpeg] = working
        return list(_probe_cache[ffmpeg])


def pick_encoder(ffmpeg: str, wanted: str = "auto") -> str:
    """
    Resolve "auto" (or an encoder that isn't available anymore) to something that works
    """
    working = probe_encoders(ffmpeg)
    if wanted != "auto" and wanted in working:
        return wanted
    return working[0] if working else "mpeg4"


def codec_args(encoder: str, quality: str, fps: int, low_latency: bool = False) -> list[str]:
    """
    Codec part of the ffmpeg command line for one encoder
    """
    # Keyframe every second for the replay buffer (clips are cut there), every two seconds otherwise.
    gop = fps if low_latency else fps * 2
    if encoder == "libx264":
        preset, crf = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["Medium"])
        args = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-g", str(gop), "-keyint_min", str(gop),
                "-sc_threshold", "0", "-pix_fmt", "yuv420p"]
        if low_latency:
            args += ["-tune", "zerolatency"]
        return args
    q = HW_QUALITY.get(quality, 23)
    if encoder == "h264_nvenc":
        args = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(q), "-b:v", "0",
                "-g", str(gop), "-forced-idr", "1", "-pix_fmt", "yuv420p"]
        if low_latency:
            args += ["-tune", "ll", "-delay", "0", "-rc-lookahead", "0", "-bf", "0"]
        return args
    if encoder == "h264_amf":
        args = ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q),
                "-g", str(gop), "-pix_fmt", "yuv420p"]
        if low_latency:
            args += ["-usage", "lowlatency", "-bf", "0"]
        return args
    if encoder == "h264_qsv":
        args = ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", str(q), "-g", str(gop),
                "-pix_fmt", "nv12"]
        if low_latency:
            args += ["-look_ahead", "0", "-bf", "0", "-async_depth", "1"]
        return args
    # Builds without x264 fall back to MPEG-4.
    return ["-c:v", "mpeg4", "-q:v", {"Low": "8", "Medium": "5", "High": "3"}.get(quality, "5"),
            "-g", str(gop), "-pix_fmt", "yuv420p"]


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
    Pipe raw BGRA frames into ffmpeg to produce an H.264 MP4 (a file, or a fragmented stream on stdout)
    """

    def __init__(self, ffmpeg: str, path: str, width: int, height: int, fps: int, quality: str, log_path: str,
                 encoder: Optional[str] = None, fragmented: bool = False, stream: bool = False,
                 low_latency: bool = False):
        if encoder is None:
            encoder = "libx264" if has_encoder(ffmpeg, "libx264") else "mpeg4"
        codec = codec_args(encoder, quality, fps, low_latency)
        if fragmented or stream:
            # Every fragment is playable on its own, so a crash (or a cut) never loses the whole file.
            container = ["-f", "mp4", "-movflags", "+frag_keyframe+empty_moov+default_base_moof"]
        else:
            container = ["-movflags", "+faststart"]
        target = "pipe:1" if stream else path
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgra", "-video_size", f"{width}x{height}",
                "-framerate", str(fps), "-i", "pipe:0", "-an", *codec, *container, target]
        self.path = path
        self.encoder = encoder
        self.log_path = log_path
        self.frames = 0
        self._log = open(log_path, "w", encoding="utf-8")
        try:
            self._proc = subprocess.Popen(args, stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE if stream else subprocess.DEVNULL,
                                          stderr=self._log, bufsize=0, **_popen_kwargs())
        except OSError as exc:
            self._log.close()
            raise EncodeError(f"Could not start ffmpeg: {exc}") from exc
        # Only set when streaming; the replay buffer reads the fragments from here.
        self.stdout = self._proc.stdout if stream else None

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


def remux(ffmpeg: str, video: str, output: str, log_path: str) -> None:
    """
    Rewrite a (fragmented) video as a normal fast-start MP4 without re-encoding
    """
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", video, "-c", "copy",
            "-movflags", "+faststart", output]
    with open(log_path, "w", encoding="utf-8") as log:
        try:
            code = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=log, timeout=3600,
                                  **_popen_kwargs()).returncode
        except (OSError, subprocess.SubprocessError) as exc:
            raise EncodeError(f"Could not run ffmpeg: {exc}") from exc
    if code != 0 or not os.path.isfile(output) or os.path.getsize(output) == 0:
        raise EncodeError(f"Finishing the video failed.\n{_tail(log_path)}")


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
