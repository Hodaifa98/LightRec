"""Short sounds that stand in for on-screen notices over exclusive fullscreen games"""
from __future__ import annotations

import io
import math
import struct
import sys
import threading
import wave

RATE = 22050
NOTE_SECONDS = 0.07
# Quiet (about -24 dBFS) so it stays in the background of the game, and of a recording that picks it up.
VOLUME = 2000
# Two notes per cue (Hz): rising for start, higher for saved, falling for errors.
NOTES = {"start": (523, 659), "saved": (659, 880), "error": (440, 349)}

_rendered: dict[str, bytes] = {}


def _render(notes: tuple[int, ...]) -> bytes:
    count = int(RATE * NOTE_SECONDS)
    fade = count // 4
    samples = bytearray()
    for freq in notes:
        for i in range(count):
            # Fade each note in and out so it's soft rather than clicky.
            env = min(1.0, i / fade, (count - i) / fade)
            samples += struct.pack("<h", int(VOLUME * env * math.sin(2 * math.pi * freq * i / RATE)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(bytes(samples))
    return buf.getvalue()


def play(kind: str) -> None:
    """
    Play a cue without blocking (Windows only)
    """
    if sys.platform != "win32" or kind not in NOTES:
        return
    import winsound
    if kind not in _rendered:
        _rendered[kind] = _render(NOTES[kind])
    data = _rendered[kind]

    def run():
        try:
            winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        except RuntimeError:
            pass

    # winsound can't play from memory asynchronously, so a short-lived thread waits for it instead.
    threading.Thread(target=run, name="lightrec-cue", daemon=True).start()
