"""Secondary windows (area selector, countdown, control bar, notes, dialogs)"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, ttk
from typing import Callable, Optional

from .. import encoder
from . import theme
from .widgets import IconButton, round_rect

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
Rect = tuple[int, int, int, int]
ExcludeFn = Optional[Callable[[tk.Toplevel], None]]


def _exclude(top: tk.Toplevel, exclude: ExcludeFn) -> None:
    if exclude is not None:
        top.update_idletasks()
        try:
            exclude(top)
        except Exception:
            pass


def _keep_on_top(top: tk.Toplevel) -> None:
    # Borderless games keep their own window topmost too, so popups have to climb back above it.
    if IS_WINDOWS:
        try:
            from .. import winapi
            winapi.keep_on_top(winapi.tk_hwnd(top))
        except Exception:
            pass


class RegionSelector:
    """
    Dimmed full-screen overlay, drag to pick a rectangle
    """

    MIN_SIZE = 16

    def __init__(self, root: tk.Misc, bounds: Rect, on_done: Callable[[Optional[Rect]], None], scale: float = 1.0):
        self.bounds, self.on_done, self.scale = bounds, on_done, scale
        self._start: Optional[tuple[int, int]] = None
        self._done = False
        top = self.top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.geometry(f"{bounds[2]}x{bounds[3]}+{bounds[0]}+{bounds[1]}")
        self.hole = IS_WINDOWS
        try:
            if self.hole:
                top.attributes("-transparentcolor", theme.KEY_COLOR)
                top.attributes("-alpha", 0.45)
            else:
                top.attributes("-alpha", 0.35)
        except tk.TclError:
            self.hole = False
        self.canvas = tk.Canvas(top, bg="#000000", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self._rect = None
        self._size_bg = None
        self._size_text = None
        self._hint_items: list[int] = []
        self._draw_hint("Drag to select the area to record   ·   Esc to cancel")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        top.bind("<Escape>", lambda e: self._finish(None))
        top.bind("<FocusOut>", self._on_focus_out)
        top.after(20, self._focus)

    def _focus(self):
        try:
            self.top.focus_force()
            self.canvas.focus_set()
        except tk.TclError:
            pass

    def _on_focus_out(self, _e):
        # Keep keyboard focus so Esc keeps working.
        if not self._done and self._start is None:
            self.top.after(50, self._focus)

    def _draw_hint(self, text: str):
        for item in self._hint_items:
            self.canvas.delete(item)
        self._hint_items.clear()
        font = (theme.font_family(self.top), 11)
        w = self.bounds[2]
        tid = self.canvas.create_text(w / 2, int(46 * self.scale), text=text, fill=theme.TEXT, font=font)
        x1, y1, x2, y2 = self.canvas.bbox(tid)
        pad = int(14 * self.scale)
        bid = round_rect(self.canvas, x1 - pad, y1 - pad * 0.6, x2 + pad, y2 + pad * 0.6, 10, fill=theme.PANEL2, outline="")
        self.canvas.tag_raise(tid, bid)
        self._hint_items += [bid, tid]

    def _press(self, e):
        self._start = (e.x, e.y)
        if self._rect:
            self.canvas.delete(self._rect, self._size_bg, self._size_text)
        fill = theme.KEY_COLOR if self.hole else ""
        self._rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=theme.ACCENT, width=2, fill=fill)
        self._size_bg = round_rect(self.canvas, 0, 0, 0, 0, 6, fill=theme.PANEL2, outline="")
        self._size_text = self.canvas.create_text(0, 0, text="", fill=theme.TEXT,
                                                  font=(theme.font_family(self.top), 10, "bold"))

    def _motion(self, e):
        if self._start is None:
            return
        x1, y1 = self._start
        x2, y2 = max(0, min(e.x, self.bounds[2])), max(0, min(e.y, self.bounds[3]))
        self.canvas.coords(self._rect, x1, y1, x2, y2)
        w, h = abs(x2 - x1), abs(y2 - y1)
        tx, ty = max(x1, x2) + 8, max(y1, y2) + 8
        label = f"{w - w % 2} × {h - h % 2}"
        self.canvas.itemconfigure(self._size_text, text=label)
        bbox = self.canvas.bbox(self._size_text)
        tw, th = (bbox[2] - bbox[0]) + 16, (bbox[3] - bbox[1]) + 10
        if tx + tw > self.bounds[2]:
            tx = max(x1, x2) - tw - 8
        if ty + th > self.bounds[3]:
            ty = min(y1, y2) - th - 8
        self.canvas.coords(self._size_text, tx + tw / 2, ty + th / 2)
        self.canvas.delete(self._size_bg)
        self._size_bg = round_rect(self.canvas, tx, ty, tx + tw, ty + th, 6, fill=theme.PANEL2, outline="")
        self.canvas.tag_raise(self._size_text, self._size_bg)

    def _release(self, e):
        if self._start is None:
            return
        x1, y1 = self._start
        x2, y2 = max(0, min(e.x, self.bounds[2])), max(0, min(e.y, self.bounds[3]))
        self._start = None
        left, top = min(x1, x2), min(y1, y2)
        w, h = abs(x2 - x1), abs(y2 - y1)
        w, h = w - w % 2, h - h % 2
        if w < self.MIN_SIZE or h < self.MIN_SIZE:
            self.canvas.delete(self._rect, self._size_bg, self._size_text)
            self._rect = None
            self._draw_hint("That area is too small - drag a larger rectangle   ·   Esc to cancel")
            return
        self._finish((left + self.bounds[0], top + self.bounds[1], w, h))

    def _finish(self, rect: Optional[Rect]):
        if self._done:
            return
        self._done = True
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        self.on_done(rect)


class Countdown:
    """
    Centered 3-2-1 countdown before recording (click to cancel)
    """

    def __init__(self, root: tk.Misc, seconds: int, center: Rect, on_done: Callable[[], None],
                 on_cancel: Callable[[], None], exclude: ExcludeFn = None, scale: float = 1.0):
        self.remaining = seconds
        self.on_done, self.on_cancel = on_done, on_cancel
        self._finished = False
        size = int(150 * scale)
        top = self.top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        x = center[0] + (center[2] - size) // 2
        y = center[1] + (center[3] - size) // 2
        top.geometry(f"{size}x{size}+{x}+{y}")
        bg = theme.KEY_COLOR if IS_WINDOWS else theme.BG
        if IS_WINDOWS:
            try:
                top.attributes("-transparentcolor", theme.KEY_COLOR)
            except tk.TclError:
                bg = theme.BG
        self.canvas = tk.Canvas(top, bg=bg, highlightthickness=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_oval(2, 2, size - 2, size - 2, fill=theme.PANEL, outline=theme.BORDER, width=2)
        self.number = self.canvas.create_text(size / 2, size / 2 - 8, text=str(seconds), fill=theme.TEXT,
                                              font=(theme.font_family(top), int(54 * scale), "bold"))
        self.canvas.create_text(size / 2, size - int(30 * scale), text="click to cancel", fill=theme.MUTED,
                                font=(theme.font_family(top), 9))
        self.canvas.bind("<Button-1>", lambda e: self._cancel())
        _exclude(top, exclude)
        top.after(1000, self._tick)

    def _tick(self):
        if self._finished:
            return
        self.remaining -= 1
        if self.remaining <= 0:
            self._finished = True
            self.top.destroy()
            self.on_done()
            return
        self.canvas.itemconfigure(self.number, text=str(self.remaining))
        self.top.after(1000, self._tick)

    def _cancel(self):
        if self._finished:
            return
        self._finished = True
        self.top.destroy()
        self.on_cancel()


class MiniBar:
    """
    Floating control bar shown while recording
    """

    def __init__(self, root: tk.Misc, on_pause: Callable[[], None], on_stop: Callable[[], None],
                 position: Optional[tuple[int, int]], screen: Rect, on_moved: Callable[[int, int], None],
                 exclude: ExcludeFn = None, scale: float = 1.0, show_pause: bool = True, visible: bool = True):
        self.on_moved = on_moved
        top = self.top = tk.Toplevel(root)
        # Hidden until it is in place.
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        frame = tk.Frame(top, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1,
                         padx=int(10 * scale), pady=int(5 * scale))
        frame.pack()
        self.dot = IconButton(frame, "dot", lambda: None, theme.DANGER, size=int(22 * scale))
        self.dot.configure(cursor="fleur")
        self.dot.pack(side="left")
        self.time = tk.Label(frame, text="00:00", bg=theme.PANEL, fg=theme.TEXT, width=6, cursor="fleur",
                             font=(theme.font_family(top), 11, "bold"))
        self.time.pack(side="left", padx=(2, 8))
        self.pause_btn = IconButton(frame, "pause", on_pause, theme.TEXT, size=int(26 * scale))
        # Gamer mode has no pause, it just records.
        if show_pause:
            self.pause_btn.pack(side="left", padx=2)
        self.stop_btn = IconButton(frame, "stop", on_stop, theme.DANGER, size=int(26 * scale))
        self.stop_btn.pack(side="left", padx=2)
        self.status = tk.Label(frame, text="", bg=theme.PANEL, fg=theme.MUTED, font=(theme.font_family(top), 9))
        for widget in (frame, self.dot, self.time):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        if position and screen[0] <= position[0] <= screen[0] + screen[2] - w and screen[1] <= position[1] <= screen[1] + screen[3] - h:
            x, y = position
        else:
            x, y = screen[0] + (screen[2] - w) // 2, screen[1] + int(14 * scale)
        top.geometry(f"+{x}+{y}")
        _exclude(top, exclude)
        self._drag = None
        self._blink = True
        if visible:
            top.deiconify()

    def _drag_start(self, e):
        self._drag = (e.x_root - self.top.winfo_x(), e.y_root - self.top.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self.top.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, _e):
        if self._drag:
            self._drag = None
            self.on_moved(self.top.winfo_x(), self.top.winfo_y())

    def set_time(self, text: str) -> None:
        self.time.configure(text=text)

    def set_paused(self, paused: bool) -> None:
        self.pause_btn.set_glyph("play" if paused else "pause")
        self.dot._color = theme.WARN if paused else theme.DANGER
        self.dot._draw()

    def blink(self, paused: bool) -> None:
        if paused:
            return
        self._blink = not self._blink
        self.dot._color = theme.DANGER if self._blink else theme.PANEL4
        self.dot._draw()

    def set_saving(self) -> None:
        self.pause_btn.pack_forget()
        self.stop_btn.pack_forget()
        self.status.configure(text="Saving…")
        self.status.pack(side="left", padx=(0, 4))
        self.dot._color = theme.ACCENT
        self.dot._draw()

    def set_visible(self, visible: bool) -> None:
        """
        Show or hide the bar; showing also puts it back on top
        """
        try:
            hidden = self.top.state() == "withdrawn"
            if visible and hidden:
                self.top.deiconify()
            elif not visible and not hidden:
                self.top.withdraw()
        except tk.TclError:
            return
        if visible:
            _keep_on_top(self.top)

    def destroy(self) -> None:
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class Toast:
    """
    Small notice in the corner of the game display that fades away on its own
    """

    def __init__(self, root: tk.Misc, text: str, screen: Rect, exclude: ExcludeFn = None, scale: float = 1.0,
                 duration_ms: int = 2600, color: str = theme.OK):
        top = self.top = tk.Toplevel(root)
        # Hidden until it can't take the focus, so it never pulls a game out of the foreground.
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        frame = tk.Frame(top, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1,
                         padx=int(14 * scale), pady=int(9 * scale))
        frame.pack()
        dot = tk.Canvas(frame, width=12, height=12, bg=theme.PANEL, highlightthickness=0)
        dot.create_oval(1, 1, 11, 11, fill=color, outline="")
        dot.pack(side="left", padx=(0, 9))
        tk.Label(frame, text=text, bg=theme.PANEL, fg=theme.TEXT, font=(theme.font_family(top), 10, "bold")).pack(side="left")
        top.update_idletasks()
        w = top.winfo_reqwidth()
        margin = int(18 * scale)
        top.geometry(f"+{screen[0] + screen[2] - w - margin}+{screen[1] + margin}")
        _exclude(top, exclude)
        if IS_WINDOWS:
            try:
                from .. import winapi
                winapi.make_passive(winapi.tk_hwnd(top))
            except Exception:
                pass
        top.deiconify()
        self._close_job = top.after(duration_ms, self.destroy)
        self._raise_job = ""
        self._keep_raised()

    def _keep_raised(self) -> None:
        _keep_on_top(self.top)
        self._raise_job = self.top.after(250, self._keep_raised)

    def destroy(self) -> None:
        try:
            # A pending callback on a destroyed window would end in a Tcl error.
            for job in (self._close_job, self._raise_job):
                if job:
                    self.top.after_cancel(job)
            self._close_job = self._raise_job = ""
            self.top.destroy()
        except tk.TclError:
            pass


class NotesPanel:
    """
    Floating notepad (hidden from recordings on Windows)
    """

    def __init__(self, root: tk.Misc, text: str, geometry: str, on_change: Callable[[str, str], None],
                 exclude: ExcludeFn = None, scale: float = 1.0):
        self.on_change = on_change
        top = self.top = tk.Toplevel(root)
        hidden_note = "hidden from recordings" if IS_WINDOWS else "keep it outside the recorded area"
        top.title(f"Notes  ·  {hidden_note}")
        top.configure(bg=theme.BG)
        top.minsize(int(240 * scale), int(160 * scale))
        if geometry:
            top.geometry(geometry)
        else:
            top.geometry(f"{int(360 * scale)}x{int(300 * scale)}")
        bar = tk.Frame(top, bg=theme.BG)
        bar.pack(fill="x", padx=8, pady=(6, 0))
        self.topmost = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Always on top", variable=self.topmost, command=self._apply_topmost,
                        style="TCheckbutton").pack(side="left")
        tk.Label(bar, text="Your notes are saved automatically", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.font_family(top), 9)).pack(side="right")
        self.text = tk.Text(top, bg=theme.PANEL, fg=theme.TEXT, insertbackground=theme.TEXT, wrap="word",
                            relief="flat", bd=0, padx=12, pady=10, undo=True, selectbackground=theme.ACCENT,
                            font=(theme.font_family(top), 11), highlightthickness=1,
                            highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
        self.text.pack(fill="both", expand=True, padx=8, pady=8)
        self.text.insert("1.0", text)
        self.text.edit_modified(False)
        self.text.bind("<<Modified>>", self._modified)
        top.protocol("WM_DELETE_WINDOW", self.hide)
        top.bind("<Configure>", lambda e: self._save(), add="+")
        self._apply_topmost()
        _exclude(top, exclude)
        self._save_after = None

    def _apply_topmost(self):
        self.top.attributes("-topmost", bool(self.topmost.get()))

    def _modified(self, _e=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self._save()

    def _save(self):
        if self._save_after:
            self.top.after_cancel(self._save_after)
        self._save_after = self.top.after(400, self._do_save)

    def _do_save(self):
        self._save_after = None
        try:
            self.on_change(self.text.get("1.0", "end-1c"), self.top.geometry())
        except tk.TclError:
            pass

    def show(self):
        self.top.deiconify()
        self.top.lift()
        self.text.focus_set()

    def hide(self):
        self._do_save()
        self.top.withdraw()

    def visible(self) -> bool:
        try:
            return self.top.state() == "normal"
        except tk.TclError:
            return False

    def toggle(self):
        self.hide() if self.visible() else self.show()


class FfmpegDialog:
    """
    Dialog to download or locate ffmpeg
    """

    def __init__(self, root: tk.Misc, on_done: Callable[[Optional[str]], None], scale: float = 1.0):
        self.on_done = on_done
        self._cancel = False
        self._result: Optional[str] = None
        self._error: Optional[str] = None
        self._progress = (0.0, "")
        top = self.top = tk.Toplevel(root)
        top.title("Set up ffmpeg")
        top.configure(bg=theme.BG)
        top.resizable(False, False)
        top.transient(root)
        pad = int(18 * scale)
        body = tk.Frame(top, bg=theme.BG, padx=pad, pady=pad)
        body.pack()
        tk.Label(body, text="One more thing: ffmpeg", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.font_family(top), 13, "bold"), anchor="w").pack(fill="x")
        size_hint = "about 30 MB" if IS_WINDOWS else "about 40–80 MB"
        msg = ("LightRec uses ffmpeg, a free open-source tool, to encode video.\n\n"
               f"It can download a portable copy ({size_hint}) into its own folder - nothing is installed "
               "system-wide - or you can point it to an ffmpeg you already have.")
        tk.Label(body, text=msg, bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=int(400 * scale),
                 anchor="w").pack(fill="x", pady=(8, 14))
        self.bar = ttk.Progressbar(body, mode="determinate", maximum=1000, length=int(400 * scale))
        self.status = tk.Label(body, text="", bg=theme.BG, fg=theme.MUTED, anchor="w")
        buttons = tk.Frame(body, bg=theme.BG)
        buttons.pack(fill="x")
        self.download_btn = ttk.Button(buttons, text="Download automatically", style="Accent.TButton",
                                       command=self._download)
        self.download_btn.pack(side="left")
        self.locate_btn = ttk.Button(buttons, text="Locate ffmpeg…", command=self._locate)
        self.locate_btn.pack(side="left", padx=8)
        ttk.Button(buttons, text="Cancel", style="Ghost.TButton", command=self._close).pack(side="right")
        if encoder.download_url() is None:
            self.download_btn.state(["disabled"])
        top.protocol("WM_DELETE_WINDOW", self._close)
        top.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - top.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - top.winfo_reqheight()) // 2
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.grab_set()

    def _locate(self):
        types = [("ffmpeg", "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"), ("All files", "*")]
        path = filedialog.askopenfilename(parent=self.top, title="Select the ffmpeg executable", filetypes=types)
        if not path:
            return
        if encoder._usable(path):
            self._finish(path)
        else:
            self.status.configure(text="That file does not look like a working ffmpeg.", fg=theme.DANGER)
            self.status.pack(fill="x", pady=(8, 0))

    def _download(self):
        self.download_btn.state(["disabled"])
        self.locate_btn.state(["disabled"])
        self.bar.pack(fill="x", pady=(0, 6))
        self.status.configure(fg=theme.MUTED, text="Starting…")
        self.status.pack(fill="x", pady=(0, 10))
        self.status.pack_configure(before=self.download_btn.master)
        self.bar.pack_configure(before=self.status)
        threading.Thread(target=self._worker, daemon=True).start()
        self.top.after(100, self._poll)

    def _worker(self):
        try:
            self._result = encoder.download_ffmpeg(lambda f, m: self._set_progress(f, m), lambda: self._cancel)
        except Exception as exc:
            self._error = str(exc)

    def _set_progress(self, fraction: float, message: str):
        self._progress = (fraction, message)

    def _poll(self):
        if self._cancel:
            return
        if self._result:
            self._finish(self._result)
            return
        if self._error:
            self.status.configure(text=f"Download failed: {self._error}", fg=theme.DANGER)
            self.download_btn.state(["!disabled"])
            self.locate_btn.state(["!disabled"])
            self._error = None
            return
        fraction, message = self._progress
        if fraction < 0:
            self.bar.configure(mode="indeterminate")
            self.bar.step(10)
        else:
            self.bar.configure(mode="determinate", value=int(fraction * 1000))
        self.status.configure(text=message)
        self.top.after(100, self._poll)

    def _finish(self, path: str):
        try:
            self.top.grab_release()
            self.top.destroy()
        except tk.TclError:
            pass
        self.on_done(path)

    def _close(self):
        self._cancel = True
        try:
            self.top.grab_release()
            self.top.destroy()
        except tk.TclError:
            pass
        self.on_done(None)


class AboutDialog:
    """
    About window
    """

    GITHUB = "https://github.com/Hodaifa98"

    def __init__(self, root: tk.Misc, version: str, details: list[str], scale: float = 1.0):
        top = self.top = tk.Toplevel(root)
        top.title("About LightRec")
        top.configure(bg=theme.BG)
        top.resizable(False, False)
        top.transient(root)
        fam = theme.font_family(top)
        pad = int(18 * scale)
        body = tk.Frame(top, bg=theme.BG, padx=pad, pady=pad)
        body.pack()
        tk.Label(body, text="LightRec", bg=theme.BG, fg=theme.TEXT, font=(fam, 16, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=f"Version {version}  ·  a lightweight, portable screen recorder", bg=theme.BG,
                 fg=theme.MUTED, anchor="w").pack(fill="x", pady=(0, 10))
        steps = ("1. Pick what to record: the full screen, an area you drag out, or one window.\n"
                 "2. Optionally hide windows you don't want in the video (notes, chats…).\n"
                 "3. Choose a microphone and/or system audio, then press Start recording.")
        tk.Label(body, text=steps, bg=theme.BG, fg=theme.TEXT, justify="left", anchor="w").pack(fill="x")
        tk.Label(body, text="\n".join(details), bg=theme.BG, fg=theme.MUTED, justify="left", anchor="w",
                 wraplength=int(440 * scale), font=(fam, 9)).pack(fill="x", pady=(10, 12))
        ttk.Separator(body).pack(fill="x")
        credit = tk.Frame(body, bg=theme.BG)
        credit.pack(fill="x", pady=(10, 0))
        tk.Label(credit, text="Developed by Hodaifa98", bg=theme.BG, fg=theme.TEXT,
                 font=(fam, 10, "bold")).pack(side="left")
        link = tk.Label(credit, text="github.com/Hodaifa98", bg=theme.BG, fg=theme.ACCENT, cursor="hand2",
                        font=(fam, 10, "underline"))
        link.pack(side="left", padx=(10, 0))
        link.bind("<Button-1>", lambda e: webbrowser.open(self.GITHUB))
        link.bind("<Enter>", lambda e: link.configure(fg=theme.ACCENT_HOVER))
        link.bind("<Leave>", lambda e: link.configure(fg=theme.ACCENT))
        ttk.Button(credit, text="Close", style="Small.TButton", command=top.destroy).pack(side="right")
        top.bind("<Escape>", lambda e: top.destroy())
        top.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - top.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - top.winfo_reqheight()) // 2
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.focus_set()
