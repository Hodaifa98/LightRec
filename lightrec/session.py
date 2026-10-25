"""A recording session"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from . import encoder, paths
from .audio import AudioDevice, AudioRecorder
from .capture import FrameSource, SourceLost

EventCallback = Callable[[str, dict], None]


@dataclass
class SessionConfig:
    source: FrameSource
    ffmpeg: str
    output_dir: str
    fps: int = 30
    quality: str = "Medium"
    audio: list[AudioDevice] = field(default_factory=list)


@dataclass
class RecordingResult:
    path: str
    duration: float
    size: int
    warnings: list[str]


class RecordingSession:
    """
    One recording (starting, recording, paused, finalizing, done or error)
    """

    def __init__(self, config: SessionConfig, on_event: EventCallback):
        self.config = config
        self._emit = on_event
        self.state = "starting"
        self.warnings: list[str] = []
        # Set while not paused.
        self._run = threading.Event()
        self._run.set()
        self._stopping = False
        self._lock = threading.Lock()
        self._frames_written = 0
        self._video_thread: Optional[threading.Thread] = None
        self._writer: Optional[encoder.VideoWriter] = None
        self._audio: list[AudioRecorder] = []
        self._audio_paths: list[str] = []
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        os.makedirs(config.output_dir, exist_ok=True)
        self.final_path = os.path.join(config.output_dir, f"LightRec_{stamp}.mp4")
        self._tmp_base = os.path.join(config.output_dir, f".lightrec_{stamp}")
        self._video_tmp = self._tmp_base + ".video.mp4"
        self._video_error: Optional[str] = None

    # Public API.
    def start(self) -> None:
        cfg = self.config
        log = os.path.join(paths.log_dir(), "ffmpeg_encode.log")
        self._writer = encoder.VideoWriter(cfg.ffmpeg, self._video_tmp, cfg.source.width, cfg.source.height,
                                           cfg.fps, cfg.quality, log)
        for i, device in enumerate(cfg.audio):
            path = f"{self._tmp_base}.audio{i}.wav"
            rec = AudioRecorder(device, path)
            try:
                rec.start()
            except Exception as exc:
                self.warnings.append(f"{device.name}: {exc}")
                continue
            self._audio.append(rec)
        self.state = "recording"
        self._video_thread = threading.Thread(target=self._video_loop, name="lightrec-video", daemon=True)
        self._video_thread.start()
        self._emit("started", {})

    def pause(self) -> None:
        if self.state == "recording":
            self.state = "paused"
            self._run.clear()
            for rec in self._audio:
                rec.pause()
            self._emit("paused", {})

    def resume(self) -> None:
        if self.state == "paused":
            self.state = "recording"
            for rec in self._audio:
                rec.resume()
            self._run.set()
            self._emit("resumed", {})

    def toggle_pause(self) -> None:
        if self.state == "paused":
            self.resume()
        else:
            self.pause()

    def stop(self) -> None:
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
        self.state = "finalizing"
        self._run.set()
        self._emit("finalizing", {})
        threading.Thread(target=self._finalize, name="lightrec-finalize", daemon=True).start()

    @property
    def elapsed(self) -> float:
        return self._frames_written / float(self.config.fps)

    def levels(self) -> dict[str, float]:
        return {rec.device.key: rec.level for rec in self._audio}

    # Threads.
    def _video_loop(self) -> None:
        source, writer = self.config.source, self._writer
        interval = 1.0 / self.config.fps
        last_frame: Optional[bytes] = None
        next_time = time.perf_counter()
        note_sent = False
        try:
            while not self._stopping:
                if not self._run.is_set():
                    paused_at = time.perf_counter()
                    while not self._run.wait(0.1):
                        if self._stopping:
                            break
                    next_time += time.perf_counter() - paused_at
                    continue
                now = time.perf_counter()
                if now < next_time:
                    time.sleep(min(next_time - now, 0.02))
                    continue
                frame = source.grab()
                if frame is None:
                    frame = last_frame
                else:
                    last_frame = frame
                if source.note and not note_sent:
                    note_sent = True
                    self.warnings.append(source.note)
                    self._emit("info", {"message": source.note})
                now = time.perf_counter()
                if frame is None:
                    next_time = max(next_time + interval, now)
                    continue
                # Write this frame, plus duplicates if capture fell behind.
                while next_time <= now and not self._stopping:
                    writer.write(frame)
                    self._frames_written += 1
                    next_time += interval
                    # Never catch up more than two seconds at once.
                    if now - next_time > 2.0:
                        next_time = now
        except SourceLost as exc:
            self._video_error = None
            self.warnings.append(str(exc))
            self._emit("info", {"message": f"{exc}. Recording stopped."})
        except Exception as exc:
            self._video_error = str(exc)
        finally:
            if not self._stopping:
                self.stop()

    def _finalize(self) -> None:
        if self._video_thread is not None and self._video_thread is not threading.current_thread():
            self._video_thread.join(timeout=15)
        error = self._video_error
        try:
            if self._writer is not None:
                if error:
                    self._writer.abort()
                else:
                    self._writer.close()
        except encoder.EncodeError as exc:
            error = error or str(exc)
        for rec in self._audio:
            path = rec.stop()
            if path and os.path.isfile(path) and os.path.getsize(path) > 44:
                self._audio_paths.append(path)
            elif rec.error:
                self.warnings.append(f"{rec.device.name}: {rec.error}")
        try:
            self.config.source.close()
        except Exception:
            pass
        if error or self._frames_written == 0:
            self._cleanup(keep_video=False)
            self.state = "error"
            self._emit("error", {"message": error or "No frames were captured."})
            return
        try:
            if self._audio_paths:
                encoder.mux(self.config.ffmpeg, self._video_tmp, self._audio_paths, self.final_path,
                            os.path.join(paths.log_dir(), "ffmpeg_mux.log"))
                self._cleanup(keep_video=False)
            else:
                os.replace(self._video_tmp, self.final_path)
                self._cleanup(keep_video=False)
        except encoder.EncodeError as exc:
            # Keep the silent video instead of losing the recording.
            try:
                os.replace(self._video_tmp, self.final_path)
                self.warnings.append(f"Audio could not be added: {exc}")
            except OSError:
                self.state = "error"
                self._emit("error", {"message": str(exc)})
                return
            self._cleanup(keep_video=True)
        self.state = "done"
        size = os.path.getsize(self.final_path) if os.path.isfile(self.final_path) else 0
        result = RecordingResult(self.final_path, self.elapsed, size, list(self.warnings))
        self._emit("finished", {"result": result})

    def _cleanup(self, keep_video: bool) -> None:
        targets = list(self._audio_paths)
        if not keep_video:
            targets.append(self._video_tmp)
        for i in range(len(self.config.audio)):
            targets.append(f"{self._tmp_base}.audio{i}.wav")
        for path in set(targets):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
