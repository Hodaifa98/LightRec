"""Frame sources (Windows GDI or mss)"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

Rect = tuple[int, int, int, int]

if IS_WINDOWS:
    from . import winapi
    from .winapi import SourceLost, WindowInfo, MonitorInfo
else:
    class SourceLost(Exception):
        """
        Raised when the recorded window no longer exists
        """

    @dataclass(frozen=True)
    class WindowInfo:
        handle: int
        title: str
        rect: Rect
        pid: int

        def label(self) -> str:
            return f"{self.title}  ({self.rect[2]}×{self.rect[3]})"

    @dataclass(frozen=True)
    class MonitorInfo:
        index: int
        name: str
        rect: Rect
        primary: bool


def even(value: int) -> int:
    return max(2, value - value % 2)


def even_rect(rect: Rect) -> Rect:
    return (rect[0], rect[1], even(rect[2]), even(rect[3]))


def intersect(a: Rect, b: Rect) -> Optional[Rect]:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


# Monitors and windows.
def _mss_instance():
    # Optional on Windows.
    import mss
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


def list_monitors() -> list[MonitorInfo]:
    if IS_WINDOWS:
        return winapi.list_monitors()
    with _mss_instance() as sct:
        mons = sct.monitors[1:] or sct.monitors[:1]
    return [MonitorInfo(i + 1, f"Display {i + 1}", (m["left"], m["top"], m["width"], m["height"]), i == 0)
            for i, m in enumerate(mons)]


def virtual_screen() -> Rect:
    if IS_WINDOWS:
        return winapi.virtual_screen()
    with _mss_instance() as sct:
        m = sct.monitors[0]
    return (m["left"], m["top"], m["width"], m["height"])


def list_windows() -> list[WindowInfo]:
    if IS_WINDOWS:
        return winapi.list_windows()
    if IS_MAC:
        return _mac_windows()
    return _x11_windows()


def window_supported() -> bool:
    """
    Check if window listing works on this system
    """
    if IS_WINDOWS:
        return True
    if IS_MAC:
        import importlib.util
        return importlib.util.find_spec("Quartz") is not None
    return shutil.which("wmctrl") is not None


def _x11_windows() -> list[WindowInfo]:
    if not shutil.which("wmctrl"):
        return []
    try:
        out = subprocess.run(["wmctrl", "-lGp"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    result = []
    for line in out.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        try:
            wid = int(parts[0], 16)
            desktop = int(parts[1])
            pid = int(parts[2])
            x, y, w, h = (int(p) for p in parts[3:7])
        except ValueError:
            continue
        title = parts[8].strip()
        if desktop < 0 or not title or w < 8 or h < 8:
            continue
        result.append(WindowInfo(wid, title, (x, y, w, h), pid))
    return result


def _mac_windows() -> list[WindowInfo]:
    try:
        import Quartz
    except ImportError:
        return []
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    result = []
    for info in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
        if info.get("kCGWindowLayer", 0) != 0:
            continue
        title = info.get("kCGWindowName") or ""
        owner = info.get("kCGWindowOwnerName") or ""
        label = f"{owner} - {title}" if title else owner
        b = info.get("kCGWindowBounds") or {}
        rect = (int(b.get("X", 0)), int(b.get("Y", 0)), int(b.get("Width", 0)), int(b.get("Height", 0)))
        if not label or rect[2] < 8 or rect[3] < 8:
            continue
        result.append(WindowInfo(int(info.get("kCGWindowNumber", 0)), label, rect, int(info.get("kCGWindowOwnerPID", 0))))
    return result


def window_rect_now(handle: int) -> Optional[Rect]:
    """
    Current rectangle of a window (None if it is gone)
    """
    if IS_WINDOWS:
        if not winapi.window_alive(handle):
            return None
        return winapi.window_bounds(handle)
    for w in list_windows():
        if w.handle == handle:
            return w.rect
    return None


# Sources.
class FrameSource:
    width: int = 0
    height: int = 0
    # Remark set by the source (fallbacks).
    note: str = ""

    def grab(self) -> Optional[bytes]:
        raise NotImplementedError

    def close(self) -> None:
        pass


PointerProvider = Callable[[], Optional[tuple[int, int]]]

# Arrow cursor ('#' black, '.' white, ' ' transparent).
_ARROW = [
    "#           ", "##          ", "#.#         ", "#..#        ", "#...#       ", "#....#      ",
    "#.....#     ", "#......#    ", "#.......#   ", "#........#  ", "#.........# ", "#......#####",
    "#...#..#    ", "#..# #..#   ", "#.#  #..#   ", "##    #..#  ", "#     #..#  ", "       ##   ",
]


def _draw_arrow(frame: bytearray, width: int, height: int, x: int, y: int) -> None:
    black, white = b"\x00\x00\x00\xff", b"\xff\xff\xff\xff"
    for row, line in enumerate(_ARROW):
        py = y + row
        if py < 0 or py >= height:
            continue
        for col, ch in enumerate(line):
            if ch == " ":
                continue
            px = x + col
            if 0 <= px < width:
                i = (py * width + px) * 4
                frame[i:i + 4] = black if ch == "#" else white


def _fill_rect(frame: bytearray, width: int, height: int, rect: Rect, color: bytes) -> None:
    area = intersect(rect, (0, 0, width, height))
    if area is None:
        return
    row = color * area[2]
    for py in range(area[1], area[1] + area[3]):
        start = (py * width + area[0]) * 4
        frame[start:start + len(row)] = row


class MssScreenSource(FrameSource):
    """
    Grab a screen rectangle with mss (hidden windows are masked)
    """

    MASK = b"\x1c\x1e\x24\xff"

    def __init__(self, rect: Rect, hidden: Iterable[int] = (), cursor: bool = True,
                 pointer: Optional[PointerProvider] = None):
        self._sct = _mss_instance()
        self.rect = rect
        self._monitor = {"left": rect[0], "top": rect[1], "width": rect[2], "height": rect[3]}
        probe = self._sct.grab(self._monitor)
        # Retina returns 2x pixels.
        self._scale = probe.size.width / rect[2] if rect[2] else 1.0
        self.width, self.height = even(probe.size.width), even(probe.size.height)
        self._crop = (probe.size.width, probe.size.height) != (self.width, self.height)
        self._src_w = probe.size.width
        self.hidden = list(hidden)
        self._hidden_rects: list[Rect] = []
        self._hidden_checked = 0.0
        self._cursor = cursor
        self._pointer = pointer
        if not self.hidden:
            self.note = ""
        else:
            self.note = "Hidden windows are masked on this system"

    def _refresh_hidden(self) -> None:
        now = time.monotonic()
        if now - self._hidden_checked < 0.5:
            return
        self._hidden_checked = now
        rects = []
        for handle in self.hidden:
            r = window_rect_now(handle)
            if r is not None:
                rects.append(r)
        self._hidden_rects = rects

    def _to_local(self, x: int, y: int) -> tuple[int, int]:
        return (int((x - self.rect[0]) * self._scale), int((y - self.rect[1]) * self._scale))

    def grab(self) -> Optional[bytes]:
        shot = self._sct.grab(self._monitor)
        data = shot.bgra
        if self._crop:
            row = self.width * 4
            src_row = self._src_w * 4
            data = b"".join(data[i * src_row:i * src_row + row] for i in range(self.height))
        needs_edit = bool(self.hidden) or (self._cursor and self._pointer is not None)
        if not needs_edit:
            return data
        frame = bytearray(data)
        if self.hidden:
            self._refresh_hidden()
            for r in self._hidden_rects:
                lx, ly = self._to_local(r[0], r[1])
                _fill_rect(frame, self.width, self.height,
                           (lx, ly, int(r[2] * self._scale), int(r[3] * self._scale)), self.MASK)
        if self._cursor and self._pointer is not None:
            pos = self._pointer()
            if pos is not None:
                lx, ly = self._to_local(*pos)
                _draw_arrow(frame, self.width, self.height, lx, ly)
        return bytes(frame)

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass


class MssWindowSource(FrameSource):
    """
    Record a window by following its screen rectangle
    """

    def __init__(self, handle: int, hidden: Iterable[int] = (), cursor: bool = True,
                 pointer: Optional[PointerProvider] = None):
        rect = window_rect_now(handle)
        if rect is None:
            raise SourceLost("The selected window is no longer open")
        self.handle = handle
        self._inner = MssScreenSource(even_rect(rect), hidden, cursor, pointer)
        self.width, self.height = self._inner.width, self._inner.height
        self._last_check = time.monotonic()
        self.note = "Overlapping windows stay visible when recording a window on this system"

    def grab(self) -> Optional[bytes]:
        now = time.monotonic()
        if now - self._last_check > 1.0:
            self._last_check = now
            rect = window_rect_now(self.handle)
            if rect is None:
                raise SourceLost("The recorded window was closed")
            inner = self._inner
            inner.rect = (rect[0], rect[1], inner.rect[2], inner.rect[3])
            inner._monitor.update(left=rect[0], top=rect[1])
        return self._inner.grab()

    def close(self) -> None:
        self._inner.close()


if IS_WINDOWS:
    class WinScreenSource(FrameSource):
        def __init__(self, rect: Rect, hidden: Iterable[int] = (), cursor: bool = True):
            rect = even_rect(rect)
            self._grabber = winapi.ScreenGrabber(rect, hidden, cursor)
            self.width, self.height = rect[2], rect[3]

        def grab(self) -> Optional[bytes]:
            return self._grabber.grab()

        def close(self) -> None:
            self._grabber.close()

    class WinWindowSource(FrameSource):
        def __init__(self, handle: int, hidden: Iterable[int] = (), cursor: bool = True, direct: bool = True):
            self._grabber = winapi.WindowGrabber(handle, hidden, cursor, direct)
            self.width, self.height = self._grabber.width, self._grabber.height

        def grab(self) -> Optional[bytes]:
            data = self._grabber.grab()
            if self._grabber.direct_failed and not self.note:
                self.note = "Direct window capture is unavailable for this window; recording its screen area instead"
            return data

        def close(self) -> None:
            self._grabber.close()


def make_screen_source(rect: Rect, hidden: Iterable[int] = (), cursor: bool = True,
                       pointer: Optional[PointerProvider] = None) -> FrameSource:
    if IS_WINDOWS:
        return WinScreenSource(rect, hidden, cursor)
    return MssScreenSource(even_rect(rect), hidden, cursor, pointer)


def make_window_source(handle: int, hidden: Iterable[int] = (), cursor: bool = True, direct: bool = True,
                       pointer: Optional[PointerProvider] = None) -> FrameSource:
    if IS_WINDOWS:
        return WinWindowSource(handle, hidden, cursor, direct)
    return MssWindowSource(handle, hidden, cursor, pointer)
