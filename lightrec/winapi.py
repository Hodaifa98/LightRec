"""Windows capture helpers (ctypes only)"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes as wt
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32
    try:
        dwmapi = ctypes.windll.dwmapi
    except OSError:
        dwmapi = None
else:
    user32 = gdi32 = kernel32 = dwmapi = None

# Constants.
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DI_NORMAL = 0x0003
CURSOR_SHOWING = 0x0001
GA_ROOT = 2
GW_HWNDNEXT = 2
GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
WDA_EXCLUDEFROMCAPTURE = 0x00000011
PW_RENDERFULLCONTENT = 0x00000002
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
MONITORINFOF_PRIMARY = 1
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000


# Structures.
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HANDLE), ("ptScreenPos", wt.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)


def _setup_prototypes() -> None:
    """
    Declare argument and return types (keeps 64-bit handles intact)
    """
    u, g, k = user32, gdi32, kernel32
    u.GetDC.restype, u.GetDC.argtypes = wt.HDC, [wt.HWND]
    u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = wt.HDC, [wt.HDC]
    g.DeleteDC.argtypes = [wt.HDC]
    g.CreateDIBSection.restype = wt.HBITMAP
    g.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
    g.SelectObject.restype, g.SelectObject.argtypes = wt.HGDIOBJ, [wt.HDC, wt.HGDIOBJ]
    g.DeleteObject.argtypes = [wt.HGDIOBJ]
    g.BitBlt.restype = wt.BOOL
    g.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
    g.CreateSolidBrush.restype, g.CreateSolidBrush.argtypes = wt.HBRUSH, [wt.COLORREF]
    u.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
    u.PrintWindow.restype, u.PrintWindow.argtypes = wt.BOOL, [wt.HWND, wt.HDC, wt.UINT]
    u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    for name in ("IsWindowVisible", "IsIconic", "IsWindow"):
        fn = getattr(u, name)
        fn.restype, fn.argtypes = wt.BOOL, [wt.HWND]
    u.GetWindow.restype, u.GetWindow.argtypes = wt.HWND, [wt.HWND, wt.UINT]
    u.GetAncestor.restype, u.GetAncestor.argtypes = wt.HWND, [wt.HWND, wt.UINT]
    u.GetWindowTextLengthW.argtypes = [wt.HWND]
    u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    u.GetWindowLongW.restype, u.GetWindowLongW.argtypes = wt.LONG, [wt.HWND, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    u.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    u.GetIconInfo.argtypes = [wt.HICON, ctypes.POINTER(ICONINFO)]
    u.DrawIconEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.HICON, ctypes.c_int, ctypes.c_int,
                             wt.UINT, wt.HBRUSH, wt.UINT]
    u.SetWindowDisplayAffinity.restype, u.SetWindowDisplayAffinity.argtypes = wt.BOOL, [wt.HWND, wt.DWORD]
    u.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
    u.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), MONITORENUMPROC, wt.LPARAM]
    u.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    u.GetSystemMetrics.argtypes = [ctypes.c_int]
    u.RegisterHotKey.restype, u.RegisterHotKey.argtypes = wt.BOOL, [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    u.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    u.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
    k.GetCurrentThreadId.restype = wt.DWORD
    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]


if IS_WINDOWS:
    _setup_prototypes()

_OWN_PID = os.getpid()


# Helpers.
def set_dpi_aware() -> None:
    """
    Make coordinates physical pixels (call before creating Tk)
    """
    try:
        # Per monitor aware v2.
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def tk_hwnd(widget) -> int:
    """
    Get the top-level HWND of a Tk widget
    """
    return int(user32.GetAncestor(wt.HWND(widget.winfo_id()), GA_ROOT) or widget.winfo_id())


def exclude_from_capture(hwnd: int) -> bool:
    """
    Hide one of our own windows from screen capture (Windows 10 2004+)
    """
    try:
        return bool(user32.SetWindowDisplayAffinity(wt.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE))
    except OSError:
        return False


# Rect is (x, y, w, h).
Rect = tuple[int, int, int, int]


def intersect(a: Rect, b: Rect) -> Optional[Rect]:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


def virtual_screen() -> Rect:
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def window_rect(hwnd: int) -> Rect:
    """
    Window rect as used by PrintWindow (includes invisible borders)
    """
    rc = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rc))
    return (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def window_bounds(hwnd: int) -> Rect:
    """
    Visible frame of a window (no shadow or invisible borders)
    """
    if dwmapi is not None:
        rc = wt.RECT()
        hr = dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                                          ctypes.byref(rc), ctypes.sizeof(rc))
        if hr == 0 and rc.right > rc.left and rc.bottom > rc.top:
            return (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
    return window_rect(hwnd)


def is_cloaked(hwnd: int) -> bool:
    if dwmapi is None:
        return False
    value = wt.DWORD(0)
    hr = dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0 and value.value != 0


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return pid.value


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(wt.HWND(hwnd))
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(wt.HWND(hwnd), buf, length + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wt.HWND(hwnd), buf, 256)
    return buf.value


def window_alive(hwnd: int) -> bool:
    return bool(user32.IsWindow(wt.HWND(hwnd)))


def window_visible_on_screen(hwnd: int) -> bool:
    h = wt.HWND(hwnd)
    return bool(user32.IsWindow(h) and user32.IsWindowVisible(h) and not user32.IsIconic(h) and not is_cloaked(hwnd))


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    rect: Rect
    pid: int

    def label(self) -> str:
        return f"{self.title}  ({self.rect[2]}×{self.rect[3]})"


_SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "DV2ControlHost"}


def list_windows() -> list[WindowInfo]:
    """
    List visible top-level windows of other processes, top-most first
    """
    result: list[WindowInfo] = []

    def callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or is_cloaked(hwnd):
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            if window_pid(hwnd) == _OWN_PID or window_class(hwnd) in _SKIP_CLASSES:
                return True
            title = window_title(hwnd).strip()
            if not title:
                return True
            rect = window_bounds(hwnd)
            if rect[2] < 8 or rect[3] < 8:
                return True
            result.append(WindowInfo(int(hwnd), title, rect, window_pid(hwnd)))
        except OSError:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return result


@dataclass(frozen=True)
class MonitorInfo:
    index: int
    name: str
    rect: Rect
    primary: bool


def list_monitors() -> list[MonitorInfo]:
    found: list[MonitorInfo] = []

    def callback(hmon, _hdc, _rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append(MonitorInfo(len(found) + 1, info.szDevice,
                                     (r.left, r.top, r.right - r.left, r.bottom - r.top),
                                     bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    found.sort(key=lambda m: (not m.primary, m.rect[0], m.rect[1]))
    return [MonitorInfo(i + 1, m.name, m.rect, m.primary) for i, m in enumerate(found)]


# GDI bitmaps.
class Dib:
    """
    Top-down 32-bit BGRA bitmap with its own memory DC
    """

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.size = width * height * 4
        self.hdc = gdi32.CreateCompatibleDC(None)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        self.bits = ctypes.c_void_p()
        self.hbm = gdi32.CreateDIBSection(None, ctypes.byref(info), 0, ctypes.byref(self.bits), None, 0)
        if not self.hbm or not self.bits:
            gdi32.DeleteDC(self.hdc)
            self.hdc = None
            raise OSError(f"Could not allocate a {width}x{height} capture bitmap")
        self.old = gdi32.SelectObject(self.hdc, self.hbm)

    def data(self) -> bytes:
        return ctypes.string_at(self.bits, self.size)

    def clear(self) -> None:
        ctypes.memset(self.bits, 0, self.size)

    def close(self) -> None:
        if self.hdc:
            gdi32.SelectObject(self.hdc, self.old)
            gdi32.DeleteObject(self.hbm)
            gdi32.DeleteDC(self.hdc)
            self.hdc = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class _CursorPainter:
    def __init__(self):
        self._hotspots: dict[int, tuple[int, int]] = {}

    def draw(self, hdc, offset_x: int, offset_y: int) -> None:
        info = CURSORINFO()
        info.cbSize = ctypes.sizeof(CURSORINFO)
        if not user32.GetCursorInfo(ctypes.byref(info)) or not (info.flags & CURSOR_SHOWING) or not info.hCursor:
            return
        hot = self._hotspots.get(info.hCursor)
        if hot is None:
            icon = ICONINFO()
            hot = (0, 0)
            if user32.GetIconInfo(info.hCursor, ctypes.byref(icon)):
                hot = (int(icon.xHotspot), int(icon.yHotspot))
                if icon.hbmMask:
                    gdi32.DeleteObject(icon.hbmMask)
                if icon.hbmColor:
                    gdi32.DeleteObject(icon.hbmColor)
            if len(self._hotspots) > 64:
                self._hotspots.clear()
            self._hotspots[info.hCursor] = hot
        user32.DrawIconEx(hdc, info.ptScreenPos.x - hot[0] + offset_x, info.ptScreenPos.y - hot[1] + offset_y,
                          info.hCursor, 0, 0, 0, None, DI_NORMAL)


class _PrintCache:
    """
    Scratch bitmaps for PrintWindow, one per window
    """

    def __init__(self):
        self._items: dict[int, Dib] = {}

    def get(self, hwnd: int, width: int, height: int) -> Dib:
        dib = self._items.get(hwnd)
        if dib is None or dib.width != width or dib.height != height:
            if dib is not None:
                dib.close()
                self._items.pop(hwnd, None)
            if len(self._items) > 16:
                self.close()
            dib = Dib(width, height)
            self._items[hwnd] = dib
        return dib

    def close(self) -> None:
        for dib in self._items.values():
            dib.close()
        self._items.clear()


class _Patcher:
    """
    Repaint the area of hidden windows with what sits beneath them
    """

    def __init__(self, hidden: Iterable[int]):
        self.hidden = [int(h) for h in hidden]
        self._hidden_set = set(self.hidden)
        self._cache = _PrintCache()
        self._brush = gdi32.CreateSolidBrush(0x000000)
        self._original: Optional[Dib] = None
        self._first_seen: dict[int, float] = {}

    def _z_ordered(self) -> list[int]:
        """
        Hidden windows that need patching, bottom-most first
        """
        active = [h for h in self.hidden if window_visible_on_screen(h)]
        if len(active) < 2:
            return active
        rank: dict[int, int] = {}
        order: list[int] = []

        def callback(hwnd, _lparam):
            order.append(int(hwnd))
            return True

        # Top-most first.
        user32.EnumWindows(WNDENUMPROC(callback), 0)
        for i, h in enumerate(order):
            rank[h] = i
        return sorted(active, key=lambda h: -rank.get(h, -1))

    def apply(self, target: Dib, capture: Rect) -> None:
        active = self._z_ordered()
        if not active:
            return
        # Keep the untouched capture, windows above a hidden window are restored from it.
        if self._original is None or self._original.width != target.width or self._original.height != target.height:
            if self._original is not None:
                self._original.close()
            self._original = Dib(target.width, target.height)
        gdi32.BitBlt(self._original.hdc, 0, 0, target.width, target.height, target.hdc, 0, 0, SRCCOPY)
        for hwnd in active:
            self._patch(target, capture, hwnd)

    def _restore_above(self, target: Dib, capture: Rect, hwnd: int, area: Rect) -> None:
        """
        Paint back the windows that sit above the hidden one
        """
        # Their pixels come from the untouched screen capture, except windows that just
        # appeared (still fading in) or layered windows, which get rendered with PrintWindow.
        now = time.monotonic()
        layers: list[tuple[int, Rect]] = []
        above = user32.GetWindow(wt.HWND(hwnd), GW_HWNDPREV)
        guard = 0
        while above and guard < 4000:
            guard += 1
            h = int(above)
            if h not in self._hidden_set and window_visible_on_screen(h) and window_pid(h) != _OWN_PID:
                part = intersect(window_bounds(h), area)
                if part is not None:
                    layers.append((h, part))
            above = user32.GetWindow(wt.HWND(h), GW_HWNDPREV)
        if len(self._first_seen) > 512:
            self._first_seen.clear()
        for h, part in layers:
            first = self._first_seen.setdefault(h, now)
            animating = now - first < 0.6
            layered = bool(user32.GetWindowLongW(wt.HWND(h), GWL_EXSTYLE) & WS_EX_LAYERED)
            if (animating or layered) and self._print_part(target, capture, h, part):
                continue
            gdi32.BitBlt(target.hdc, part[0] - capture[0], part[1] - capture[1], part[2], part[3],
                         self._original.hdc, part[0] - capture[0], part[1] - capture[1], SRCCOPY)

    def _print_part(self, target: Dib, capture: Rect, h: int, part: Rect) -> bool:
        wr = window_rect(h)
        if wr[2] <= 0 or wr[3] <= 0:
            return False
        try:
            scratch = self._cache.get(h, wr[2], wr[3])
        except OSError:
            return False
        if not user32.PrintWindow(wt.HWND(h), scratch.hdc, PW_RENDERFULLCONTENT):
            return False
        gdi32.BitBlt(target.hdc, part[0] - capture[0], part[1] - capture[1], part[2], part[3],
                     scratch.hdc, part[0] - wr[0], part[1] - wr[1], SRCCOPY)
        return True

    def _patch(self, target: Dib, capture: Rect, hwnd: int) -> None:
        area = intersect(window_bounds(hwnd), capture)
        if area is None:
            return
        layers: list[tuple[int, Rect]] = []
        below = user32.GetWindow(wt.HWND(hwnd), GW_HWNDNEXT)
        guard = 0
        while below and guard < 4000:
            guard += 1
            h = int(below)
            if h not in self._hidden_set and window_visible_on_screen(h) and window_pid(h) != _OWN_PID:
                part = intersect(window_bounds(h), area)
                if part is not None:
                    layers.append((h, part))
                    if part == area:
                        # Fully covered, nothing below can be seen.
                        break
            below = user32.GetWindow(wt.HWND(h), GW_HWNDNEXT)
        rc = wt.RECT(area[0] - capture[0], area[1] - capture[1],
                     area[0] - capture[0] + area[2], area[1] - capture[1] + area[3])
        user32.FillRect(target.hdc, ctypes.byref(rc), self._brush)
        # Paint bottom-most first.
        for h, part in reversed(layers):
            self._print_part(target, capture, h, part)
        self._restore_above(target, capture, hwnd, area)

    def close(self) -> None:
        self._cache.close()
        if self._original is not None:
            self._original.close()
            self._original = None
        if self._brush:
            gdi32.DeleteObject(self._brush)
            self._brush = None


class SourceLost(Exception):
    """
    Raised when the recorded window no longer exists
    """


class ScreenGrabber:
    """
    Capture a fixed rectangle of the virtual screen
    """

    def __init__(self, rect: Rect, hidden: Iterable[int] = (), cursor: bool = True):
        self.rect = rect
        self.width, self.height = rect[2], rect[3]
        self._dib = Dib(self.width, self.height)
        self._patcher = _Patcher(hidden)
        self._cursor = _CursorPainter() if cursor else None

    def grab(self) -> bytes:
        screen = user32.GetDC(None)
        try:
            gdi32.BitBlt(self._dib.hdc, 0, 0, self.width, self.height, screen,
                         self.rect[0], self.rect[1], SRCCOPY | CAPTUREBLT)
        finally:
            user32.ReleaseDC(None, screen)
        self._patcher.apply(self._dib, self.rect)
        if self._cursor:
            self._cursor.draw(self._dib.hdc, -self.rect[0], -self.rect[1])
        return self._dib.data()

    def close(self) -> None:
        self._patcher.close()
        self._dib.close()


def _all_black(data: bytes) -> bool:
    return not data.strip(b"\x00")


class WindowGrabber:
    """
    Capture one window directly (falls back to its screen area)
    """

    def __init__(self, hwnd: int, hidden: Iterable[int] = (), cursor: bool = True, direct: bool = True):
        if not window_alive(hwnd):
            raise SourceLost("The selected window is no longer open")
        self.hwnd = hwnd
        bounds = window_bounds(hwnd)
        self.width = max(2, bounds[2] - bounds[2] % 2)
        self.height = max(2, bounds[3] - bounds[3] % 2)
        self.direct = direct
        self.direct_failed = False
        self._checked_first = False
        self._dib = Dib(self.width, self.height)
        self._scratch: Optional[Dib] = None
        self._patcher = _Patcher(hidden)
        self._cursor = _CursorPainter() if cursor else None

    def grab(self) -> Optional[bytes]:
        h = wt.HWND(self.hwnd)
        if not user32.IsWindow(h):
            raise SourceLost("The recorded window was closed")
        if user32.IsIconic(h):
            # Minimized, keep the last frame.
            return None
        bounds = window_bounds(self.hwnd)
        if bounds[2] <= 0 or bounds[3] <= 0:
            return None
        if bounds[2] != self.width or bounds[3] != self.height:
            self._dib.clear()
        copy_w, copy_h = min(bounds[2], self.width), min(bounds[3], self.height)
        if self.direct and self._grab_direct(bounds, copy_w, copy_h):
            data = self._dib.data()
            if not self._checked_first:
                self._checked_first = True
                # Fullscreen apps render black, use the screen path instead.
                if _all_black(data):
                    self.direct = False
                    self.direct_failed = True
                    return self.grab()
            return data
        screen = user32.GetDC(None)
        try:
            gdi32.BitBlt(self._dib.hdc, 0, 0, copy_w, copy_h, screen, bounds[0], bounds[1], SRCCOPY | CAPTUREBLT)
        finally:
            user32.ReleaseDC(None, screen)
        self._patcher.apply(self._dib, (bounds[0], bounds[1], copy_w, copy_h))
        if self._cursor:
            self._cursor.draw(self._dib.hdc, -bounds[0], -bounds[1])
        return self._dib.data()

    def _grab_direct(self, bounds: Rect, copy_w: int, copy_h: int) -> bool:
        wr = window_rect(self.hwnd)
        if wr[2] <= 0 or wr[3] <= 0:
            return False
        if self._scratch is None or self._scratch.width != wr[2] or self._scratch.height != wr[3]:
            if self._scratch is not None:
                self._scratch.close()
                self._scratch = None
            try:
                self._scratch = Dib(wr[2], wr[3])
            except OSError:
                return False
        if not user32.PrintWindow(wt.HWND(self.hwnd), self._scratch.hdc, PW_RENDERFULLCONTENT):
            self.direct = False
            self.direct_failed = True
            return False
        gdi32.BitBlt(self._dib.hdc, 0, 0, copy_w, copy_h, self._scratch.hdc,
                     bounds[0] - wr[0], bounds[1] - wr[1], SRCCOPY)
        if self._cursor:
            self._cursor.draw(self._dib.hdc, -bounds[0], -bounds[1])
        return True

    def close(self) -> None:
        self._patcher.close()
        if self._scratch is not None:
            self._scratch.close()
        self._dib.close()


# Global hotkeys.
class HotkeyListener(threading.Thread):
    """
    Register global hotkeys on a message loop thread
    """

    def __init__(self, bindings: dict[int, tuple[int, int]], callback: Callable[[int], None]):
        super().__init__(name="lightrec-hotkeys", daemon=True)
        # id -> (modifiers, virtual key).
        self._bindings = bindings
        self._callback = callback
        self._thread_id = 0
        self._ready = threading.Event()
        self.failed: list[int] = []

    def run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        for hid, (mods, vk) in self._bindings.items():
            if not user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self.failed.append(hid)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self._callback(int(msg.wParam))
                except Exception:
                    pass
        for hid in self._bindings:
            user32.UnregisterHotKey(None, hid)

    def wait_ready(self, timeout: float = 1.0) -> None:
        self._ready.wait(timeout)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
