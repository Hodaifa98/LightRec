"""Dark theme for ttk"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

BG = "#13151a"
PANEL = "#1b1e26"
PANEL2 = "#242833"
PANEL3 = "#2d3240"
PANEL4 = "#363c4d"
BORDER = "#2e3342"
TEXT = "#e9ebf1"
MUTED = "#8b91a3"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6b9dff"
ACCENT_DOWN = "#3f74d9"
DANGER = "#ff4d5e"
DANGER_HOVER = "#ff6675"
DANGER_DOWN = "#d93e4d"
OK = "#36d274"
WARN = "#ffb547"
# Color key for transparent holes on Windows.
KEY_COLOR = "#ff00fe"

_FAMILY = None


def font_family(root: tk.Misc) -> str:
    global _FAMILY
    if _FAMILY:
        return _FAMILY
    try:
        families = set(tkfont.families(root))
    except tk.TclError:
        families = set()
    if sys.platform == "win32":
        prefs = ["Segoe UI", "Tahoma", "Arial"]
    elif sys.platform == "darwin":
        prefs = ["SF Pro Text", "Helvetica Neue", "Helvetica"]
    else:
        prefs = ["Inter", "Noto Sans", "Ubuntu", "Cantarell", "DejaVu Sans", "Liberation Sans"]
    _FAMILY = next((f for f in prefs if f in families), tkfont.nametofont("TkDefaultFont").actual("family"))
    return _FAMILY


def ui_scale(root: tk.Misc) -> float:
    try:
        return max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        return 1.0


def apply(root: tk.Tk) -> None:
    fam = font_family(root)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont", "TkCaptionFont"):
        try:
            tkfont.nametofont(name).configure(family=fam, size=10)
        except tk.TclError:
            pass
    root.configure(bg=BG)
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL2, bordercolor=BORDER,
                lightcolor=PANEL2, darkcolor=PANEL2, troughcolor=PANEL2, focuscolor=BG,
                selectbackground=ACCENT, selectforeground="#ffffff", font=(fam, 10))
    s.configure("TFrame", background=BG)
    s.configure("Panel.TFrame", background=PANEL)
    s.configure("TLabel", background=BG, foreground=TEXT)
    s.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=(fam, 9))
    s.configure("MutedBg.TLabel", background=BG, foreground=MUTED, font=(fam, 9))
    s.configure("Warn.TLabel", background=BG, foreground=WARN, font=(fam, 9))
    s.configure("Section.TLabel", background=PANEL, foreground=MUTED, font=(fam, 8, "bold"))
    s.configure("Title.TLabel", background=BG, foreground=TEXT, font=(fam, 15, "bold"))

    s.configure("TButton", background=PANEL3, foreground=TEXT, borderwidth=0, focusthickness=0,
                padding=(12, 6), anchor="center")
    s.map("TButton", background=[("disabled", PANEL2), ("pressed", PANEL2), ("active", PANEL4)],
          foreground=[("disabled", MUTED)])
    s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff")
    s.map("Accent.TButton", background=[("disabled", PANEL3), ("pressed", ACCENT_DOWN), ("active", ACCENT_HOVER)])
    s.configure("Ghost.TButton", background=BG, foreground=MUTED, padding=(10, 5))
    s.map("Ghost.TButton", background=[("pressed", PANEL2), ("active", PANEL2)],
          foreground=[("active", TEXT), ("disabled", BORDER)])
    s.configure("Small.TButton", background=PANEL3, foreground=TEXT, padding=(8, 4), font=(fam, 9))
    s.map("Small.TButton", background=[("disabled", PANEL2), ("pressed", PANEL2), ("active", PANEL4)],
          foreground=[("disabled", MUTED)])
    s.configure("Link.TButton", background=BG, foreground=ACCENT, padding=(4, 2), font=(fam, 9))
    s.map("Link.TButton", background=[("active", BG)], foreground=[("active", ACCENT_HOVER)])

    s.configure("Seg.Toolbutton", background=PANEL2, foreground=MUTED, borderwidth=0, focusthickness=0,
                padding=(14, 7), anchor="center", font=(fam, 10))
    s.map("Seg.Toolbutton", background=[("disabled", PANEL2), ("selected", ACCENT), ("active", PANEL3)],
          foreground=[("disabled", "#4b5162"), ("selected", "#ffffff"), ("active", TEXT)])

    s.configure("TCheckbutton", background=PANEL, foreground=TEXT, focuscolor=PANEL, indicatorbackground=PANEL3,
                indicatorforeground=TEXT, indicatormargin=(0, 0, 8, 0), padding=(2, 3))
    s.map("TCheckbutton", background=[("active", PANEL)], foreground=[("disabled", MUTED)],
          indicatorbackground=[("disabled", PANEL2), ("selected", ACCENT), ("active", PANEL4)],
          indicatorforeground=[("selected", "#ffffff")])

    s.configure("TCombobox", fieldbackground=PANEL2, background=PANEL3, foreground=TEXT, arrowcolor=TEXT,
                bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2, padding=(8, 5), arrowsize=14,
                insertcolor=TEXT)
    s.map("TCombobox", fieldbackground=[("readonly", PANEL2), ("disabled", PANEL)],
          foreground=[("disabled", MUTED)], selectbackground=[("readonly", PANEL2)],
          selectforeground=[("readonly", TEXT)], background=[("active", PANEL4)],
          arrowcolor=[("disabled", MUTED)], bordercolor=[("focus", ACCENT)])
    root.option_add("*TCombobox*Listbox.background", PANEL2)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.relief", "flat")
    root.option_add("*TCombobox*Listbox.font", f"{{{fam}}} 10")

    s.configure("TEntry", fieldbackground=PANEL2, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER,
                lightcolor=PANEL2, darkcolor=PANEL2, padding=(8, 5))
    s.map("TEntry", fieldbackground=[("readonly", PANEL2), ("disabled", PANEL)], foreground=[("disabled", MUTED)],
          bordercolor=[("focus", ACCENT)])
    s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL2, bordercolor=PANEL2,
                lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
    s.configure("TSeparator", background=BORDER)
    s.configure("Vertical.TScrollbar", background=PANEL3, troughcolor=PANEL, arrowcolor=TEXT, bordercolor=PANEL,
                lightcolor=PANEL3, darkcolor=PANEL3)
    s.map("Vertical.TScrollbar", background=[("active", PANEL4)])
