"""Audio devices and recording"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave
from array import array
from dataclasses import dataclass, field
from typing import Optional

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Backends.
_pa = None
_sd = None

if IS_WINDOWS:
    try:
        import pyaudiowpatch as _pa
    except Exception:
        _pa = None
try:
    import sounddevice as _sd
except Exception:
    _sd = None


def available() -> bool:
    return _pa is not None or _sd is not None


def backend_name() -> str:
    if _pa is not None:
        return "WASAPI"
    if _sd is not None:
        return "PortAudio"
    return "none"


@dataclass(frozen=True)
class AudioDevice:
    key: str
    name: str
    # Backend is "pa" or "sd".
    backend: str
    index: int
    channels: int
    samplerate: int
    is_system: bool = False
    is_default: bool = False
    env: dict = field(default_factory=dict, compare=False, hash=False)

    def label(self) -> str:
        return self.name


def _clean_name(name: str) -> str:
    return name.replace(" [Loopback]", "").strip()


def list_devices() -> tuple[list[AudioDevice], list[AudioDevice]]:
    """
    Return (microphones, system audio sources)
    """
    mics: list[AudioDevice] = []
    systems: list[AudioDevice] = []
    if _pa is not None:
        try:
            mics, systems = _list_pa()
        except Exception:
            mics, systems = [], []
    if not mics and not systems and _sd is not None:
        try:
            mics, systems = _list_sd()
        except Exception:
            mics, systems = [], []
    return mics, systems


def _list_pa() -> tuple[list[AudioDevice], list[AudioDevice]]:
    p = _pa.PyAudio()
    try:
        api = p.get_host_api_info_by_type(_pa.paWASAPI)
        default_in = api.get("defaultInputDevice", -1)
        try:
            default_loop = p.get_default_wasapi_loopback().get("index", -1)
        except Exception:
            default_loop = -1
        mics, systems = [], []
        for info in p.get_device_info_generator_by_host_api(host_api_index=api["index"]):
            if info.get("maxInputChannels", 0) <= 0:
                continue
            idx = int(info["index"])
            dev = AudioDevice(
                key=f"pa:{_clean_name(info['name'])}", name=_clean_name(info["name"]), backend="pa", index=idx,
                channels=min(2, int(info["maxInputChannels"])), samplerate=int(info.get("defaultSampleRate") or 48000),
                is_system=bool(info.get("isLoopbackDevice")), is_default=idx in (default_in, default_loop))
            (systems if dev.is_system else mics).append(dev)
        return mics, systems
    finally:
        p.terminate()


def _list_sd() -> tuple[list[AudioDevice], list[AudioDevice]]:
    devices = _sd.query_devices()
    apis = _sd.query_hostapis()
    try:
        default_in = _sd.default.device[0]
    except Exception:
        default_in = -1
    preferred_api = None
    if IS_WINDOWS:
        for i, api in enumerate(apis):
            if "WASAPI" in api["name"]:
                preferred_api = i
    mics: list[AudioDevice] = []
    systems: list[AudioDevice] = []
    seen: set[str] = set()
    for idx, info in enumerate(devices):
        if info["max_input_channels"] <= 0:
            continue
        if preferred_api is not None and info["hostapi"] != preferred_api:
            continue
        name = info["name"].strip()
        if name in seen:
            continue
        seen.add(name)
        dev = AudioDevice(key=f"sd:{name}", name=name, backend="sd", index=idx,
                          channels=min(2, int(info["max_input_channels"])),
                          samplerate=int(info["default_samplerate"] or 48000),
                          is_system=_looks_like_system_source(name), is_default=idx == default_in)
        (systems if dev.is_system else mics).append(dev)
    if sys.platform.startswith("linux"):
        systems.extend(_pulse_monitors(devices))
    return mics, systems


def _looks_like_system_source(name: str) -> bool:
    lowered = name.lower()
    return any(k in lowered for k in ("loopback", "monitor", "stereo mix", "what u hear", "blackhole",
                                      "soundflower", "ishowu", "virtual cable"))


def _pulse_monitors(devices) -> list[AudioDevice]:
    """
    PulseAudio / PipeWire monitor sources (opened through the 'pulse' device)
    """
    if not shutil.which("pactl"):
        return []
    pulse_index = None
    for idx, info in enumerate(devices):
        if info["name"] in ("pulse", "default") and info["max_input_channels"] > 0:
            pulse_index = idx
            break
    if pulse_index is None:
        return []
    try:
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].endswith(".monitor"):
            source = parts[1]
            pretty = source[:-len(".monitor")].replace("alsa_output.", "").replace("_", " ")
            found.append(AudioDevice(key=f"pulse:{source}", name=f"System audio ({pretty})", backend="sd",
                                     index=pulse_index, channels=2, samplerate=48000, is_system=True,
                                     env={"PULSE_SOURCE": source}))
    return found


def _peak(data: bytes) -> float:
    if not data:
        return 0.0
    samples = array("h")
    samples.frombytes(data[: len(data) - len(data) % 2])
    if not samples:
        return 0.0
    return min(1.0, max(max(samples), -min(samples)) / 32768.0)


class AudioRecorder:
    """
    Record one device to a WAV file (or only monitor its level when path is None)
    """

    def __init__(self, device: AudioDevice, path: Optional[str] = None):
        self.device = device
        self.path = path
        self.level = 0.0
        self.error: Optional[str] = None
        self._queue: "queue.Queue[bytes]" = queue.Queue()
        self._paused = False
        self._pause_started = 0.0
        self._paused_total = 0.0
        self._t0 = 0.0
        self._running = False
        self._writer: Optional[threading.Thread] = None
        self._stream = None
        self._pa_instance = None
        self._channels = device.channels
        self._rate = device.samplerate
        self._lock = threading.Lock()

    # Lifecycle.
    def start(self) -> None:
        for key, value in self.device.env.items():
            os.environ[key] = value
        if self.device.backend == "pa":
            self._open_pa()
        else:
            self._open_sd()
        self._t0 = time.monotonic()
        self._running = True
        if self.path:
            self._writer = threading.Thread(target=self._write_loop, name="lightrec-audio-writer", daemon=True)
            self._writer.start()

    def _candidates(self) -> list[tuple[int, int]]:
        ch, rate = self.device.channels, self.device.samplerate
        tried = []
        for cand in ((ch, rate), (2, rate), (1, rate), (ch, 48000), (ch, 44100), (1, 48000), (1, 44100)):
            if cand not in tried and cand[0] > 0:
                tried.append(cand)
        return tried

    def _open_pa(self) -> None:
        self._pa_instance = _pa.PyAudio()
        last_error = None
        for channels, rate in self._candidates():
            try:
                stream = self._pa_instance.open(format=_pa.paInt16, channels=channels, rate=rate, input=True,
                                                input_device_index=self.device.index, frames_per_buffer=1024,
                                                stream_callback=self._pa_callback)
                stream.start_stream()
                self._stream, self._channels, self._rate = stream, channels, rate
                return
            except Exception as exc:
                last_error = exc
        self._pa_instance.terminate()
        self._pa_instance = None
        raise RuntimeError(f"Could not open '{self.device.name}': {last_error}")

    def _open_sd(self) -> None:
        last_error = None
        for channels, rate in self._candidates():
            try:
                stream = _sd.RawInputStream(device=self.device.index, channels=channels, samplerate=rate,
                                            dtype="int16", blocksize=1024, callback=self._sd_callback)
                stream.start()
                self._stream, self._channels, self._rate = stream, channels, rate
                return
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"Could not open '{self.device.name}': {last_error}")

    def _pa_callback(self, in_data, frame_count, time_info, status):
        self._on_data(bytes(in_data))
        return (None, _pa.paContinue)

    def _sd_callback(self, indata, frames, time_info, status):
        self._on_data(bytes(indata))

    def _on_data(self, data: bytes) -> None:
        peak = _peak(data)
        self.level = max(peak, self.level * 0.75)
        if self._running and self.path and not self._paused:
            self._queue.put(data)

    def pause(self) -> None:
        with self._lock:
            if not self._paused:
                self._paused = True
                self._pause_started = time.monotonic()

    def resume(self) -> None:
        with self._lock:
            if self._paused:
                self._paused = False
                self._paused_total += time.monotonic() - self._pause_started

    def stop(self) -> Optional[str]:
        """
        Stop capturing and return the WAV path (None when only monitoring)
        """
        self._running = False
        self._close_stream()
        if self._writer is not None:
            self._writer.join(timeout=10)
            self._writer = None
        self.level = 0.0
        return self.path if self.path and self.error is None else None

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            if self.device.backend == "pa":
                stream.stop_stream()
                stream.close()
            else:
                stream.stop()
                stream.close()
        except Exception:
            pass
        if self._pa_instance is not None:
            try:
                self._pa_instance.terminate()
            except Exception:
                pass
            self._pa_instance = None

    # Writer.
    def _expected_frames(self) -> int:
        with self._lock:
            now = self._pause_started if self._paused else time.monotonic()
            elapsed = now - self._t0 - self._paused_total
        return max(0, int(elapsed * self._rate))

    def _write_loop(self) -> None:
        frame_bytes = 2 * self._channels
        written = 0
        try:
            with wave.open(self.path, "wb") as wav:
                wav.setnchannels(self._channels)
                wav.setsampwidth(2)
                wav.setframerate(self._rate)
                while True:
                    try:
                        chunk = self._queue.get(timeout=0.05)
                    except queue.Empty:
                        chunk = None
                    if chunk is None:
                        if not self._running:
                            break
                    else:
                        wav.writeframes(chunk)
                        written += len(chunk) // frame_bytes
                    # Fill gaps with silence so the track never falls behind the clock.
                    gap = self._expected_frames() - written
                    if gap > self._rate // 4:
                        pad = gap - self._rate // 20
                        wav.writeframes(bytes(pad * frame_bytes))
                        written += pad
                # Drain what arrived while stopping.
                while True:
                    try:
                        chunk = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    wav.writeframes(chunk)
                    written += len(chunk) // frame_bytes
                gap = self._expected_frames() - written
                if gap > 0:
                    wav.writeframes(bytes(gap * frame_bytes))
        except Exception as exc:
            self.error = str(exc)
