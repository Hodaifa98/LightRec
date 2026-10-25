"""Gamer mode: one capture loop feeding the instant replay buffer and long session recordings"""
from __future__ import annotations

import os
import queue
import struct
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from . import encoder, paths
from .audio import AudioDevice, AudioRecorder
from .capture import FrameSource, SourceLost
from .session import RecordingResult

EventCallback = Callable[[str, dict], None]

# How long a save waits for the fragment that contains the moment you pressed the key.
SAVE_WAIT = 3.0


@dataclass
class GameConfig:
    source: FrameSource
    ffmpeg: str
    output_dir: str
    fps: int = 30
    quality: str = "Medium"
    encoder: str = "libx264"
    audio: list[AudioDevice] = field(default_factory=list)


# Audio.
class PcmWriter:
    """
    Write one audio track to a WAV file, starting at a moment chosen later (when the first video frame goes out)
    """

    def __init__(self, path: str, channels: int, rate: int):
        self.path = path
        self.channels, self.rate = channels, rate
        self.error: Optional[str] = None
        self.t0: Optional[float] = None
        self._queue: "queue.Queue[Optional[tuple[float, bytes]]]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="lightrec-game-audio", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def begin_at(self, t0: float) -> None:
        self.t0 = t0

    def push(self, start: float, data: bytes) -> None:
        self._queue.put((start, data))

    def stop(self) -> Optional[str]:
        self._queue.put(None)
        self._thread.join(timeout=10)
        return self.path if self.error is None else None

    def _loop(self) -> None:
        fb = 2 * self.channels
        written = 0
        try:
            with wave.open(self.path, "wb") as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(2)
                wav.setframerate(self.rate)
                while True:
                    item = self._queue.get()
                    if item is None:
                        break
                    start, data = item
                    t0 = self.t0
                    if t0 is None:
                        continue
                    frames = len(data) // fb
                    if start + frames / self.rate <= t0:
                        continue
                    # Where this chunk belongs; pad a dropout with silence, trim an overlap.
                    pos = int((start - t0) * self.rate)
                    if pos > written:
                        wav.writeframes(bytes((pos - written) * fb))
                        written = pos
                    elif pos < written:
                        skip = written - pos
                        if skip >= frames:
                            continue
                        data = data[skip * fb:]
                        frames -= skip
                    wav.writeframes(data)
                    written += frames
        except Exception as exc:
            self.error = str(exc)


class AudioTrack:
    """
    One device: the last seconds kept in memory for replays, plus a copy to any open WAV writers
    """

    def __init__(self, device: AudioDevice, keep_seconds: float):
        self.device = device
        self.keep = keep_seconds
        self.recorder = AudioRecorder(device, None, on_chunk=self._on_chunk)
        self.rate = self.channels = 0
        self._ring: deque[tuple[float, bytes]] = deque()
        self._writers: list[PcmWriter] = []
        self._lock = threading.Lock()
        self._anchor_time = 0.0
        self._anchor_frames = 0

    def start(self) -> None:
        self.recorder.start()
        self.rate, self.channels = self.recorder.rate, self.recorder.channels

    def stop(self) -> None:
        self.recorder.stop()

    @property
    def level(self) -> float:
        return self.recorder.level

    @property
    def frame_bytes(self) -> int:
        return 2 * self.channels

    def add_writer(self, writer: PcmWriter) -> None:
        with self._lock:
            self._writers.append(writer)

    def remove_writer(self, writer: PcmWriter) -> None:
        with self._lock:
            self._writers = [w for w in self._writers if w is not writer]

    def _on_chunk(self, data: bytes, now: float) -> None:
        frames = len(data) // self.frame_bytes
        if frames == 0:
            return
        # Timestamps come from the device's own sample count (callback timing is jittery);
        # if the device clock and ours drift apart, jump back onto the wall clock.
        measured = now - frames / self.rate
        expected = self._anchor_time + self._anchor_frames / self.rate
        if abs(measured - expected) > 0.1:
            self._anchor_time, self._anchor_frames = measured, 0
            start = measured
        else:
            start = expected
        self._anchor_frames += frames
        with self._lock:
            self._ring.append((start, data))
            limit = start - self.keep
            while self._ring and self._ring[0][0] + len(self._ring[0][1]) / self.frame_bytes / self.rate < limit:
                self._ring.popleft()
            writers = list(self._writers)
        for writer in writers:
            writer.push(start, data)

    def extract(self, t0: float, t1: float) -> bytes:
        """
        PCM between two moments, silence where nothing was captured
        """
        fb, rate = self.frame_bytes, self.rate
        total = max(0, int((t1 - t0) * rate))
        out = bytearray()
        with self._lock:
            chunks = list(self._ring)
        for start, data in chunks:
            frames = len(data) // fb
            if start + frames / rate <= t0:
                continue
            if start >= t1:
                break
            pos = int((start - t0) * rate)
            written = len(out) // fb
            if pos > written:
                out += bytes((pos - written) * fb)
            elif pos < written:
                skip = written - pos
                if skip >= frames:
                    continue
                data = data[skip * fb:]
            out += data
        out = out[:total * fb]
        if len(out) < total * fb:
            out += bytes(total * fb - len(out))
        return bytes(out)


# Video.
def _boxes(buf: bytearray):
    """
    Yield complete MP4 boxes (type, bytes) from the front of the buffer
    """
    while len(buf) >= 8:
        size, typ = struct.unpack(">I4s", buf[:8])
        if size == 1:
            if len(buf) < 16:
                return
            size = struct.unpack(">Q", buf[8:16])[0]
        elif size == 0:
            # "To end of file" never happens on a live stream, treat as what we have.
            size = len(buf)
        if size < 8 or len(buf) < size:
            return
        yield typ.decode("latin-1"), bytes(buf[:size])
        del buf[:size]


def _sample_count(moof: bytes) -> int:
    """
    Frames in a fragment, read from its trun box(es)
    """
    total = 0

    def walk(data: bytes) -> None:
        nonlocal total
        pos = 0
        while pos + 8 <= len(data):
            size, typ = struct.unpack(">I4s", data[pos:pos + 8])
            if size < 8:
                return
            body = data[pos + 8:pos + size]
            if typ in (b"moof", b"traf"):
                walk(body)
            elif typ == b"trun" and len(body) >= 8:
                total += struct.unpack(">I", body[4:8])[0]
            pos += size

    walk(moof)
    return total


class ReplayBuffer:
    """
    The last N seconds of encoded video, kept in memory as MP4 fragments read straight from ffmpeg
    """

    def __init__(self, writer: encoder.VideoWriter, fps: int, seconds: int, frame_time: Callable[[int], float]):
        self.writer = writer
        self.fps, self.seconds = fps, seconds
        self._frame_time = frame_time
        self.init = b""
        self.bytes = 0
        self.error: Optional[str] = None
        # (start time, duration, moof + mdat).
        self._frags: deque[tuple[float, float, bytes]] = deque()
        self._frames = 0
        self._pending: Optional[bytes] = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read, name="lightrec-replay", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _read(self) -> None:
        buf = bytearray()
        stream = self.writer.stdout
        try:
            while True:
                chunk = stream.read(1 << 16)
                if not chunk:
                    break
                buf += chunk
                for typ, data in _boxes(buf):
                    self._on_box(typ, data)
        except Exception as exc:
            self.error = str(exc)

    def _on_box(self, typ: str, data: bytes) -> None:
        if typ in ("ftyp", "moov"):
            self.init += data
        elif typ == "moof":
            self._pending = data
        elif typ == "mdat" and self._pending is not None:
            moof, self._pending = self._pending, None
            samples = _sample_count(moof)
            start = self._frame_time(self._frames)
            self._frames += samples
            duration = samples / float(self.fps)
            with self._lock:
                self._frags.append((start, duration, moof + data))
                self.bytes += len(moof) + len(data)
                # Keep a little more than asked so a clip always has its full length.
                limit = start + duration - self.seconds - 1.5
                while self._frags and self._frags[0][0] + self._frags[0][1] < limit:
                    self.bytes -= len(self._frags[0][2])
                    self._frags.popleft()

    @property
    def buffered(self) -> float:
        """
        Seconds of video currently in memory
        """
        with self._lock:
            if not self._frags:
                return 0.0
            return self._frags[-1][0] + self._frags[-1][1] - self._frags[0][0]

    def end_time(self) -> Optional[float]:
        with self._lock:
            if not self._frags:
                return None
            return self._frags[-1][0] + self._frags[-1][1]

    def clip(self, seconds: float, until: float) -> Optional[tuple[bytes, float, float]]:
        """
        The video for the last `seconds` before `until`: (mp4 bytes, start, end)
        """
        with self._lock:
            frags = [f for f in self._frags if f[0] < until]
            init = self.init
        if not frags or not init:
            return None
        chosen = frags
        target = until - seconds
        for i in range(len(frags) - 1, -1, -1):
            if frags[i][0] <= target:
                chosen = frags[i:]
                break
        data = init + b"".join(f[2] for f in chosen)
        return data, chosen[0][0], chosen[-1][0] + chosen[-1][1]

    def close(self) -> None:
        self.writer.abort()


class _Recording:
    """
    One session recording in progress
    """

    def __init__(self, writer: encoder.VideoWriter, writers: list[PcmWriter], final_path: str, tmp_base: str):
        self.writer = writer
        self.writers = writers
        self.final_path = final_path
        self.tmp_base = tmp_base
        self.frames = 0
        self.started: Optional[float] = None
        self.stopping = False
        self.error: Optional[str] = None
        self.warnings: list[str] = []


# Engine.
class GameEngine:
    """
    Captures the display once; the frames go to the replay buffer and/or the session recording
    """

    def __init__(self, config: GameConfig, on_event: EventCallback):
        self.config = config
        self._emit = on_event
        self.tracks: list[AudioTrack] = []
        self.warnings: list[str] = []
        self.replay: Optional[ReplayBuffer] = None
        self.recording: Optional[_Recording] = None
        self.running = False
        self._frames = 0
        # (first frame index, its scheduled time) - a new segment after a stall.
        self._clock: list[tuple[int, float]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._saving = False
        self._black_warned = False
        self._thread = threading.Thread(target=self._loop, name="lightrec-game", daemon=True)

    # Lifecycle.
    def start(self) -> None:
        for device in self.config.audio:
            track = AudioTrack(device, keep_seconds=130.0)
            try:
                track.start()
            except Exception as exc:
                self.warnings.append(f"{device.name}: {exc}")
                continue
            self.tracks.append(track)
        self.running = True
        self._thread.start()

    def stop(self) -> None:
        """
        Stop everything (a session recording must be finished first)
        """
        self._stop.set()
        self._thread.join(timeout=5)
        self.disarm_replay()
        for track in self.tracks:
            try:
                track.stop()
            except Exception:
                pass
        self.tracks = []
        try:
            self.config.source.close()
        except Exception:
            pass
        self.running = False

    def levels(self) -> dict[str, float]:
        return {t.device.key: t.level for t in self.tracks}

    def frame_time(self, index: int) -> float:
        """
        Scheduled time of a frame (frames are written on a fixed grid)
        """
        first, base = self._clock[0] if self._clock else (0, time.perf_counter())
        for seg in self._clock:
            if seg[0] <= index:
                first, base = seg
        return base + (index - first) / float(self.config.fps)

    # Instant replay.
    def arm_replay(self, seconds: int) -> None:
        if self.replay is not None:
            if self.replay.seconds == seconds:
                return
            self.disarm_replay()
        cfg = self.config
        log = os.path.join(paths.log_dir(), "ffmpeg_replay.log")
        writer = encoder.VideoWriter(cfg.ffmpeg, "", cfg.source.width, cfg.source.height, cfg.fps, cfg.quality, log,
                                     encoder=cfg.encoder, stream=True, low_latency=True)
        with self._lock:
            first = self._frames
            replay = ReplayBuffer(writer, cfg.fps, seconds, lambda k: self.frame_time(first + k))
            self.replay = replay
        replay.start()
        self._emit("replay_armed", {"seconds": seconds})

    def disarm_replay(self) -> None:
        with self._lock:
            replay, self.replay = self.replay, None
        if replay is not None:
            replay.close()
            self._emit("replay_off", {})

    def save_replay(self) -> None:
        """
        Save the last N seconds as a clip (runs in the background)
        """
        if self.replay is None:
            return
        if self._saving:
            self._emit("info", {"message": "Still saving the previous clip…"})
            return
        self._saving = True
        pressed = time.perf_counter()
        threading.Thread(target=self._save_worker, args=(pressed,), name="lightrec-clip", daemon=True).start()

    def _save_worker(self, pressed: float) -> None:
        try:
            replay = self.replay
            if replay is None:
                return
            # The fragment holding this very moment shows up at the next keyframe, give it a second.
            deadline = pressed + SAVE_WAIT
            while time.perf_counter() < deadline:
                end = replay.end_time()
                if end is not None and end >= pressed:
                    break
                time.sleep(0.05)
            piece = replay.clip(replay.seconds, pressed)
            if piece is None:
                raise encoder.EncodeError("Nothing is buffered yet - give it a second and try again.")
            data, start, end = piece
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            os.makedirs(self.config.output_dir, exist_ok=True)
            final = os.path.join(self.config.output_dir, f"LightRec_Clip_{stamp}.mp4")
            tmp_base = os.path.join(self.config.output_dir, f".lightrec_clip_{stamp}")
            video = tmp_base + ".video.mp4"
            temps = [video]
            with open(video, "wb") as fh:
                fh.write(data)
            wavs = []
            for i, track in enumerate(self.tracks):
                path = f"{tmp_base}.audio{i}.wav"
                temps.append(path)
                with wave.open(path, "wb") as wav:
                    wav.setnchannels(track.channels)
                    wav.setsampwidth(2)
                    wav.setframerate(track.rate)
                    wav.writeframes(track.extract(start, end))
                wavs.append(path)
            log = os.path.join(paths.log_dir(), "ffmpeg_clip.log")
            try:
                if wavs:
                    encoder.mux(self.config.ffmpeg, video, wavs, final, log)
                else:
                    encoder.remux(self.config.ffmpeg, video, final, log)
            finally:
                for path in temps:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            size = os.path.getsize(final) if os.path.isfile(final) else 0
            self._emit("clip_saved", {"result": RecordingResult(final, end - start, size, [])})
        except Exception as exc:
            self._emit("clip_error", {"message": str(exc)})
        finally:
            self._saving = False

    # Session recording.
    def start_recording(self) -> None:
        if self.recording is not None:
            return
        cfg = self.config
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        os.makedirs(cfg.output_dir, exist_ok=True)
        final = os.path.join(cfg.output_dir, f"LightRec_Game_{stamp}.mp4")
        tmp_base = os.path.join(cfg.output_dir, f".lightrec_game_{stamp}")
        log = os.path.join(paths.log_dir(), "ffmpeg_game.log")
        writer = encoder.VideoWriter(cfg.ffmpeg, tmp_base + ".video.mp4", cfg.source.width, cfg.source.height,
                                     cfg.fps, cfg.quality, log, encoder=cfg.encoder, fragmented=True)
        writers = []
        for i, track in enumerate(self.tracks):
            pcm = PcmWriter(f"{tmp_base}.audio{i}.wav", track.channels, track.rate)
            pcm.start()
            track.add_writer(pcm)
            writers.append(pcm)
        with self._lock:
            self.recording = _Recording(writer, writers, final, tmp_base)
        self._emit("rec_started", {})

    def stop_recording(self) -> None:
        with self._lock:
            rec = self.recording
            if rec is None or rec.stopping:
                return
            rec.stopping = True
        self._emit("rec_finalizing", {})
        threading.Thread(target=self._finalize, args=(rec,), name="lightrec-game-finalize", daemon=True).start()

    @property
    def elapsed(self) -> float:
        rec = self.recording
        return rec.frames / float(self.config.fps) if rec else 0.0

    def _finalize(self, rec: _Recording) -> None:
        # Let the capture loop drop the recording before closing the pipe.
        for _ in range(50):
            if self.recording is not rec:
                break
            time.sleep(0.02)
        error = rec.error
        try:
            if error:
                rec.writer.abort()
            else:
                rec.writer.close()
        except encoder.EncodeError as exc:
            error = error or str(exc)
        wavs = []
        for track, pcm in zip(self.tracks, rec.writers):
            track.remove_writer(pcm)
            path = pcm.stop()
            if path and os.path.isfile(path) and os.path.getsize(path) > 44:
                wavs.append(path)
            elif pcm.error:
                rec.warnings.append(f"{track.device.name}: {pcm.error}")
        video = rec.tmp_base + ".video.mp4"
        temps = [video] + [f"{rec.tmp_base}.audio{i}.wav" for i in range(len(rec.writers))]
        if error or rec.frames == 0:
            self._cleanup(temps)
            self._emit("rec_error", {"message": error or "No frames were captured."})
            return
        log = os.path.join(paths.log_dir(), "ffmpeg_mux.log")
        try:
            if wavs:
                encoder.mux(self.config.ffmpeg, video, wavs, rec.final_path, log)
            else:
                encoder.remux(self.config.ffmpeg, video, rec.final_path, log)
        except encoder.EncodeError as exc:
            # Keep the raw video rather than lose the whole session.
            try:
                os.replace(video, rec.final_path)
                rec.warnings.append(f"Audio could not be added: {exc}")
            except OSError:
                self._cleanup(temps)
                self._emit("rec_error", {"message": str(exc)})
                return
        self._cleanup(temps)
        size = os.path.getsize(rec.final_path) if os.path.isfile(rec.final_path) else 0
        result = RecordingResult(rec.final_path, rec.frames / float(self.config.fps), size, list(rec.warnings))
        self._emit("rec_finished", {"result": result})

    @staticmethod
    def _cleanup(temps: list[str]) -> None:
        for path in temps:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    # Capture loop.
    def _loop(self) -> None:
        source = self.config.source
        interval = 1.0 / self.config.fps
        last_frame: Optional[bytes] = None
        next_time = time.perf_counter()
        self._clock = [(0, next_time)]
        black_streak = 0
        try:
            while not self._stop.is_set():
                now = time.perf_counter()
                if now < next_time:
                    time.sleep(min(next_time - now, 0.02))
                    continue
                frame = source.grab()
                if frame is None:
                    frame = last_frame
                else:
                    last_frame = frame
                now = time.perf_counter()
                if frame is None:
                    next_time = max(next_time + interval, now)
                    continue
                if self._frames % (self.config.fps * 2) == 0:
                    black_streak = self._check_black(frame, black_streak)
                # Write this frame, plus duplicates if capture fell behind.
                while next_time <= now and not self._stop.is_set():
                    self._write(frame)
                    next_time += interval
                    if now - next_time > 2.0:
                        # The machine stalled; skip ahead instead of spamming duplicates.
                        next_time = now
                        self._clock.append((self._frames, now))
        except SourceLost as exc:
            self.warnings.append(str(exc))
            self._emit("info", {"message": f"{exc}. Gamer mode stopped."})
        except Exception as exc:
            self._emit("engine_error", {"message": str(exc)})

    def _write(self, frame: bytes) -> None:
        with self._lock:
            replay, rec = self.replay, self.recording
            index = self._frames
            self._frames += 1
            if rec is not None and rec.stopping:
                self.recording = None
                rec = None
        if replay is not None:
            try:
                replay.writer.write(frame)
            except encoder.EncodeError as exc:
                with self._lock:
                    if self.replay is replay:
                        self.replay = None
                replay.close()
                self._emit("replay_error", {"message": str(exc)})
        if rec is not None:
            if rec.started is None:
                rec.started = self.frame_time(index)
                for pcm in rec.writers:
                    pcm.begin_at(rec.started)
            try:
                rec.writer.write(frame)
                rec.frames += 1
            except encoder.EncodeError as exc:
                rec.error = str(exc)
                self.stop_recording()

    def _check_black(self, frame: bytes, streak: int) -> int:
        if self._black_warned:
            return 0
        if frame.strip(b"\x00"):
            return 0
        streak += 1
        # Three checks in a row (about six seconds) of pure black is not a loading screen.
        if streak >= 3:
            self._black_warned = True
            self._emit("info", {"message": "The capture is all black. If the game runs in exclusive fullscreen, "
                                           "switch it to borderless window mode."})
        return streak
