"""Custom widgets"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from . import theme


def round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float, **kw) -> int:
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
              x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kw)


class Card(tk.Frame):
    """
    Bordered panel with a section title
    """

    def __init__(self, master, title: str, scale: float = 1.0, **kw):
        super().__init__(master, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1, **kw)
        pad = int(11 * scale)
        self.body = tk.Frame(self, bg=theme.PANEL)
        ttk.Label(self, text=title.upper(), style="Section.TLabel").pack(anchor="w", padx=pad, pady=(int(7 * scale), 0))
        self.body.pack(fill="both", expand=True, padx=pad, pady=(int(5 * scale), int(9 * scale)))


class RoundButton(tk.Canvas):
    """
    Large rounded button drawn on a canvas
    """

    def __init__(self, master, text: str, command: Callable[[], None], color: str, hover: str, down: str,
                 fg: str = "#ffffff", height: int = 44, radius: int = 12, font=None, bg: str = theme.BG):
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._text, self._command = text, command
        self._colors = (color, hover, down)
        self._fg, self._radius, self._font = fg, radius, font
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def configure_button(self, text: Optional[str] = None, colors: Optional[tuple[str, str, str]] = None,
                         enabled: Optional[bool] = None) -> None:
        if text is not None:
            self._text = text
        if colors is not None:
            self._colors = colors
        if enabled is not None:
            self._state = "normal" if enabled else "disabled"
            self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = self._pressed = False
        self._draw()

    def _on_press(self, _e):
        if self._state == "normal":
            self._pressed = True
            self._draw()

    def _on_release(self, e):
        was = self._pressed
        self._pressed = False
        self._draw()
        if was and self._state == "normal" and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._command()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        color, hover, down = self._colors
        fill = theme.PANEL3 if self._state == "disabled" else down if self._pressed else hover if self._hover else color
        fg = theme.MUTED if self._state == "disabled" else self._fg
        round_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


class LevelMeter(tk.Canvas):
    """
    Audio level meter with a falling peak marker
    """

    def __init__(self, master, width: int = 90, height: int = 6, bg: str = theme.PANEL):
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
        self._mw, self._mh = width, height
        self._peak = 0.0
        self._bg = self.create_rectangle(0, 0, width, height, fill=theme.PANEL3, outline="")
        self._bar = self.create_rectangle(0, 0, 0, height, fill=theme.OK, outline="")
        self._mark = self.create_rectangle(0, 0, 0, 0, fill=theme.TEXT, outline="")

    def set(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        self._peak = max(level, self._peak - 0.03)
        x = int(level * self._mw)
        color = theme.OK if level < 0.6 else theme.WARN if level < 0.9 else theme.DANGER
        self.coords(self._bar, 0, 0, x, self._mh)
        self.itemconfigure(self._bar, fill=color)
        px = int(self._peak * self._mw)
        self.coords(self._mark, max(0, px - 2), 0, px, self._mh) if px > 0 else self.coords(self._mark, 0, 0, 0, 0)

    def set_enabled(self, enabled: bool) -> None:
        if not enabled:
            self._peak = 0.0
            self.set(0.0)
        self.itemconfigure(self._bg, fill=theme.PANEL3 if enabled else theme.PANEL2)


class Chip(tk.Frame):
    """
    Removable tag for the hidden windows list
    """

    def __init__(self, master, text: str, on_remove: Callable[[], None], scale: float = 1.0):
        super().__init__(master, bg=theme.PANEL3, padx=int(8 * scale), pady=int(3 * scale))
        label = tk.Label(self, text=text, bg=theme.PANEL3, fg=theme.TEXT, anchor="w")
        label.pack(side="left", fill="x", expand=True)
        close = tk.Label(self, text="✕", bg=theme.PANEL3, fg=theme.MUTED, cursor="hand2", padx=int(4 * scale))
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: on_remove())
        close.bind("<Enter>", lambda e: close.configure(fg=theme.DANGER))
        close.bind("<Leave>", lambda e: close.configure(fg=theme.MUTED))


class IconButton(tk.Canvas):
    """
    Small canvas button with a pause, play, stop or dot glyph
    """

    def __init__(self, master, glyph: str, command: Callable[[], None], color: str, size: int = 26,
                 bg: str = theme.PANEL):
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._glyph, self._command, self._color, self._size, self._bg = glyph, command, color, size, bg
        self._hover = False
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self._command())
        self._draw()

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self._draw()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        s = self._size
        if self._hover:
            round_rect(self, 0, 0, s, s, 6, fill=theme.PANEL3, outline="")
        c, m = self._color, s / 2
        if self._glyph == "pause":
            self.create_rectangle(m - 6, m - 6, m - 2, m + 6, fill=c, outline="")
            self.create_rectangle(m + 2, m - 6, m + 6, m + 6, fill=c, outline="")
        elif self._glyph == "play":
            self.create_polygon(m - 5, m - 7, m - 5, m + 7, m + 7, m, fill=c, outline="")
        elif self._glyph == "stop":
            round_rect(self, m - 6, m - 6, m + 6, m + 6, 2, fill=c, outline="")
        elif self._glyph == "dot":
            self.create_oval(m - 5, m - 5, m + 5, m + 5, fill=c, outline="")


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str):
        self.widget, self.text, self._tip, self._after = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._after = self.widget.after(600, self._show)

    def _show(self):
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.overrideredirect(True)
        self._tip.attributes("-topmost", True)
        tk.Label(self._tip, text=self.text, bg=theme.PANEL4, fg=theme.TEXT, padx=8, pady=5, wraplength=320,
                 justify="left").pack()
        self._tip.geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._tip:
            self._tip.destroy()
            self._tip = None
