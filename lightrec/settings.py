"""Saved user settings"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

from . import paths

FPS_CHOICES = (15, 24, 30, 60)
QUALITY_CHOICES = ("Low", "Medium", "High")
REPLAY_CHOICES = (15, 30, 60, 120)


@dataclass
class Settings:
    # Mode is screen, region or window.
    mode: str = "screen"
    # 0 means all displays.
    monitor: int = 1
    # [x, y, w, h].
    region: Optional[list] = None
    # Last recorded window, matched by title.
    window_title: str = ""
    direct_window_capture: bool = True
    fps: int = 30
    quality: str = "Medium"
    cursor: bool = True
    countdown: bool = True
    hide_while_recording: bool = True
    hotkeys: bool = True
    # "" is none, "auto" picks the default mic.
    mic_key: str = "auto"
    # "" is none.
    system_key: str = ""
    output_dir: str = field(default_factory=paths.default_output_dir)
    notes: str = ""
    notes_geometry: str = ""
    window_geometry: str = ""
    minibar_position: Optional[list] = None
    # Gamer mode. View is "record" or "game".
    view: str = "record"
    # 1-based display index.
    game_monitor: int = 1
    game_fps: int = 30
    game_quality: str = "Medium"
    # "auto" or an ffmpeg encoder name such as h264_nvenc.
    game_encoder: str = "auto"
    replay_enabled: bool = False
    replay_seconds: int = 30
    game_timer: bool = True
    # A quiet beep in place of on-screen notices over exclusive fullscreen games.
    fullscreen_sound: bool = True

    @classmethod
    def load(cls) -> "Settings":
        data = {}
        try:
            with open(paths.settings_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        settings = cls(**clean)
        if settings.fps not in FPS_CHOICES:
            settings.fps = 30
        if settings.quality not in QUALITY_CHOICES:
            settings.quality = "Medium"
        if settings.mode not in ("screen", "region", "window"):
            settings.mode = "screen"
        if settings.region is not None and (not isinstance(settings.region, list) or len(settings.region) != 4):
            settings.region = None
        if not settings.output_dir:
            settings.output_dir = paths.default_output_dir()
        if settings.view not in ("record", "game"):
            settings.view = "record"
        if settings.game_fps not in FPS_CHOICES:
            settings.game_fps = 30
        if settings.game_quality not in QUALITY_CHOICES:
            settings.game_quality = "Medium"
        if settings.replay_seconds not in REPLAY_CHOICES:
            settings.replay_seconds = 30
        return settings

    def save(self) -> None:
        try:
            path = paths.settings_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2)
            os.replace(tmp, path)
        except OSError:
            pass
