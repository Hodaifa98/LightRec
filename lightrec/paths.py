"""Folders used by LightRec"""
from __future__ import annotations

import os
import sys
import tempfile


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """
    Folder that holds the app (the exe when frozen, else the project root)
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".lightrec_write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def data_dir() -> str:
    """
    Folder for settings and ffmpeg (app folder if writable, else the user profile)
    """
    candidate = app_dir()
    if _writable(candidate):
        return candidate
    fallback = os.path.join(os.path.expanduser("~"), ".lightrec")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def settings_path() -> str:
    return os.path.join(data_dir(), "lightrec_settings.json")


def bin_dir() -> str:
    return os.path.join(data_dir(), "bin")


def log_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "lightrec")
    os.makedirs(path, exist_ok=True)
    return path


def default_output_dir() -> str:
    home = os.path.expanduser("~")
    for name in ("Videos", "Movies"):
        candidate = os.path.join(home, name)
        if os.path.isdir(candidate):
            return os.path.join(candidate, "LightRec")
    return os.path.join(home, "LightRec")
