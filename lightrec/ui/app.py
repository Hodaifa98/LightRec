"""Main window for LightRec"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .. import __version__, audio, capture, encoder, paths
from ..audio import AudioDevice, AudioRecorder
from ..capture import WindowInfo
from ..game import GameConfig, GameEngine
from ..session import RecordingResult, RecordingSession, SessionConfig
from ..settings import FPS_CHOICES, QUALITY_CHOICES, REPLAY_CHOICES, Settings
from . import cues, theme
from .overlays import AboutDialog, Countdown, FfmpegDialog, MiniBar, NotesPanel, RegionSelector, Toast
from .widgets import Card, Chip, LevelMeter, RoundButton, Tooltip

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WINDOWS:
    from .. import winapi

HOTKEY_RECORD, HOTKEY_PAUSE, HOTKEY_CLIP = 1, 2, 3
TRAY_OPEN, TRAY_CLIP, TRAY_RECORD, TRAY_REPLAY, TRAY_QUIT = 1, 2, 3, 4, 5
HOTKEY_TEXT = "Ctrl+Shift+R  start / stop      Ctrl+Shift+P  pause"
GAME_HOTKEY_TEXT = "Ctrl+Shift+R  start / stop      Ctrl+Shift+S  save clip"


def replay_label(seconds: int) -> str:
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min"


def format_time(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_size(size: int) -> str:
    if size >= 1 << 30:
        return f"{size / (1 << 30):.2f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    return f"{max(1, size // 1024)} KB"


def open_path(path: str) -> None:
    try:
        if IS_WINDOWS:
            os.startfile(path)
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


def reveal_file(path: str) -> None:
    try:
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif IS_MAC:
            subprocess.Popen(["open", "-R", path])
        else:
            open_path(os.path.dirname(path))
    except OSError:
        open_path(os.path.dirname(path))


class App:
    def __init__(self, start_hidden: bool = False) -> None:
        if IS_WINDOWS:
            winapi.set_dpi_aware()
        self._start_hidden = start_hidden
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("LightRec")
        theme.apply(self.root)
        self.scale = theme.ui_scale(self.root)
        self.settings = Settings.load()
        self.ffmpeg = encoder.find_ffmpeg()
        self.events: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self.session: Optional[RecordingSession] = None
        self.result: Optional[RecordingResult] = None
        # Gamer mode engine (runs while instant replay is on or a session is being recorded).
        self.game: Optional[GameEngine] = None
        self.encoders: Optional[list[str]] = None
        self._toast: Optional[Toast] = None
        # Tray icon (Windows), shown while gamer mode runs or the window is tucked away.
        self.tray = None
        self._in_tray = False
        self._tray_state: tuple = ()
        self.monitors_audio: list[AudioRecorder] = []
        self.hidden_windows: list[WindowInfo] = []
        self.windows_cache: list[WindowInfo] = []
        self.monitors = capture.list_monitors()
        self.mics, self.systems = audio.list_devices()
        self.notes: Optional[NotesPanel] = None
        self.minibar: Optional[MiniBar] = None
        self.hotkeys = None
        self.pointer: Optional[tuple[int, int]] = None
        self._countdown: Optional[Countdown] = None
        self._quit_after_finish = False
        self._tick_count = 0
        self._set_icon()
        self._build()
        self._apply_settings()
        self._place_window()
        self.root.deiconify()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._post_init)
        self.root.after(100, self._tick)

    # Build.
    def _set_icon(self) -> None:
        size = 32
        img = tk.PhotoImage(width=size, height=size)
        rows = []
        for y in range(size):
            row = []
            for x in range(size):
                dx, dy = x - 15.5, y - 15.5
                d = (dx * dx + dy * dy) ** 0.5
                row.append(theme.DANGER if d < 9 else theme.PANEL3 if d < 14.5 else theme.BG)
            rows.append("{" + " ".join(row) + "}")
        img.put(" ".join(rows))
        self._icon = img
        try:
            self.root.iconphoto(True, img)
        except tk.TclError:
            pass

    def _build(self) -> None:
        s = self.scale
        px = int(16 * s)
        outer = tk.Frame(self.root, bg=theme.BG)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg=theme.BG, highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        main = self.main = tk.Frame(self.canvas, bg=theme.BG, padx=px, pady=0)
        self._main_item = self.canvas.create_window((0, 0), window=main, anchor="nw")
        main.columnconfigure(0, weight=1, minsize=int(430 * s))
        main.bind("<Configure>", self._fit_height)
        self.root.after_idle(lambda: self.footer.bind("<Configure>", self._fit_height))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._main_item, width=e.width))
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.root.bind_all("<Button-4>", self._on_wheel)
        self.root.bind_all("<Button-5>", self._on_wheel)
        self._scrollable = False
        self._lockable: list[tk.Widget] = []

        # Header.
        header = tk.Frame(main, bg=theme.BG)
        header.grid(row=0, column=0, sticky="ew", pady=(int(8 * s), int(6 * s)))
        self.dot = tk.Canvas(header, width=14, height=14, bg=theme.BG, highlightthickness=0)
        self._dot_item = self.dot.create_oval(2, 2, 12, 12, fill=theme.MUTED, outline="")
        self.dot.pack(side="left", padx=(0, 8))
        ttk.Label(header, text="LightRec", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="?", style="Ghost.TButton", width=2, command=self._show_help).pack(side="right")
        self.notes_btn = ttk.Button(header, text="Notes", style="Ghost.TButton", command=self._toggle_notes)
        self.notes_btn.pack(side="right", padx=(0, 4))
        self.tray_btn = ttk.Button(header, text="Run in background", style="Ghost.TButton", command=self._go_background)
        Tooltip(self.tray_btn, "Hides the window. Instant replay and the shortcuts keep working; LightRec sits in the "
                               "tray next to the clock (it may be under the ^ hidden icons).")
        Tooltip(self.notes_btn, "A floating notepad for your script. On Windows it is invisible to the recording."
                if IS_WINDOWS else "A floating notepad for your script. Keep it outside the recorded area.")

        # Recorder / Gamer switch.
        switch = tk.Frame(main, bg=theme.PANEL2)
        switch.grid(row=1, column=0, sticky="ew", pady=(0, int(8 * s)))
        self.view_var = tk.StringVar(value="record")
        for i, (value, text) in enumerate((("record", "Recorder"), ("game", "Gamer"))):
            rb = ttk.Radiobutton(switch, text=text, value=value, variable=self.view_var, style="Seg.Toolbutton",
                                 command=self._view_changed)
            rb.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 1, 0))
            self._lockable.append(rb)
        rec = self.rec_view = tk.Frame(main, bg=theme.BG)
        rec.grid(row=2, column=0, sticky="ew")
        rec.columnconfigure(0, weight=1)
        gv = self.game_view = tk.Frame(main, bg=theme.BG)
        gv.grid(row=2, column=0, sticky="ew")
        gv.columnconfigure(0, weight=1)
        gv.grid_remove()

        # Source.
        card = Card(rec, "What to record", s)
        card.grid(row=0, column=0, sticky="ew", pady=(0, int(6 * s)))
        seg = tk.Frame(card.body, bg=theme.PANEL2)
        seg.pack(fill="x")
        self.mode_var = tk.StringVar(value="screen")
        for i, (value, text) in enumerate((("screen", "Full screen"), ("region", "Area"), ("window", "Window"))):
            rb = ttk.Radiobutton(seg, text=text, value=value, variable=self.mode_var, style="Seg.Toolbutton",
                                 command=self._mode_changed)
            rb.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 1, 0))
            self._lockable.append(rb)
        detail = self.detail = tk.Frame(card.body, bg=theme.PANEL)
        detail.pack(fill="x", pady=(int(8 * s), 0))

        self.screen_frame = tk.Frame(detail, bg=theme.PANEL)
        ttk.Label(self.screen_frame, text="Display", style="Panel.TLabel").pack(side="left", padx=(0, 10))
        self.monitor_box = ttk.Combobox(self.screen_frame, state="readonly", width=34)
        self.monitor_box.pack(side="left", fill="x", expand=True)
        self.monitor_box.bind("<<ComboboxSelected>>", lambda e: self._sync_settings())
        self._lockable.append(self.monitor_box)

        self.region_frame = tk.Frame(detail, bg=theme.PANEL)
        self.region_btn = ttk.Button(self.region_frame, text="Select area…", command=self._select_region)
        self.region_btn.pack(side="left")
        self.region_label = ttk.Label(self.region_frame, text="No area selected", style="Muted.TLabel")
        self.region_label.pack(side="left", padx=10)
        self._lockable.append(self.region_btn)

        self.window_frame = tk.Frame(detail, bg=theme.PANEL)
        row = tk.Frame(self.window_frame, bg=theme.PANEL)
        row.pack(fill="x")
        self.window_box = ttk.Combobox(row, state="readonly", width=40, postcommand=self._refresh_window_box)
        self.window_box.pack(side="left", fill="x", expand=True)
        self.window_box.bind("<<ComboboxSelected>>", lambda e: self._sync_settings())
        self._lockable.append(self.window_box)
        self.direct_var = tk.BooleanVar(value=True)
        if IS_WINDOWS:
            cb = ttk.Checkbutton(self.window_frame, text="Record the window itself, even when other windows cover it",
                                 variable=self.direct_var, command=self._sync_settings)
            cb.pack(anchor="w", pady=(6, 0))
            self._lockable.append(cb)
        else:
            ttk.Label(self.window_frame, text="The window's screen area is recorded; overlapping windows stay visible.",
                      style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        # Hidden windows.
        card = Card(rec, "Hide from the recording", s)
        card.grid(row=1, column=0, sticky="ew", pady=(0, int(6 * s)))
        self.hide_box = ttk.Combobox(card.body, state="readonly", postcommand=self._refresh_hide_box)
        self.hide_box.set("Add a window to hide…")
        self.hide_box.pack(fill="x")
        self.hide_box.bind("<<ComboboxSelected>>", self._hide_selected)
        self._lockable.append(self.hide_box)
        self.chips = tk.Frame(card.body, bg=theme.PANEL)
        self.chips.pack(fill="x")
        hint = ("Replaced with what's behind them. LightRec's own windows and Notes are never recorded."
                if IS_WINDOWS else "Hidden windows are covered with a solid block in the recording.")
        self.hide_hint = ttk.Label(card.body, text=hint, style="Muted.TLabel", wraplength=int(410 * s), justify="left")
        self.hide_hint.pack(anchor="w", pady=(5, 0))

        # Audio.
        card = Card(main, "Audio", s)
        card.grid(row=3, column=0, sticky="ew", pady=(0, int(6 * s)))
        card.body.columnconfigure(1, weight=1)
        self.mic_box, self.mic_meter = self._audio_row(card.body, 0, "Microphone")
        self.sys_box, self.sys_meter = self._audio_row(card.body, 1, "System audio")
        if not audio.available():
            tip = ("Audio packages are not installed for this Python. Start LightRec with run.bat (Windows) or "
                   "run.sh, which sets everything up, or run:  python -m pip install -r requirements.txt")
            ttk.Label(card.body, text=tip, style="Muted.TLabel", wraplength=int(400 * s), justify="left").grid(
                row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
            for box in (self.mic_box, self.sys_box):
                box.state(["disabled"])
        elif not self.systems:
            tip = ("No system-audio device was found. On Windows install the 'pyaudiowpatch' package; "
                   "on macOS install a virtual audio device such as BlackHole.")
            ttk.Label(card.body, text=tip, style="Muted.TLabel", wraplength=int(400 * s), justify="left").grid(
                row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # Options.
        card = Card(rec, "Options", s)
        card.grid(row=2, column=0, sticky="ew", pady=(0, int(6 * s)))
        row = tk.Frame(card.body, bg=theme.PANEL)
        row.pack(fill="x")
        ttk.Label(row, text="Frame rate", style="Panel.TLabel").pack(side="left")
        self.fps_box = ttk.Combobox(row, state="readonly", width=5, values=[str(f) for f in FPS_CHOICES])
        self.fps_box.pack(side="left", padx=(8, 18))
        self.fps_box.bind("<<ComboboxSelected>>", lambda e: self._sync_settings())
        ttk.Label(row, text="Quality", style="Panel.TLabel").pack(side="left")
        self.quality_box = ttk.Combobox(row, state="readonly", width=8, values=list(QUALITY_CHOICES))
        self.quality_box.pack(side="left", padx=(8, 0))
        self.quality_box.bind("<<ComboboxSelected>>", lambda e: self._sync_settings())
        self._lockable += [self.fps_box, self.quality_box]
        checks = tk.Frame(card.body, bg=theme.PANEL)
        checks.pack(fill="x", pady=(6, 0))
        self.cursor_var = tk.BooleanVar(value=True)
        self.countdown_var = tk.BooleanVar(value=True)
        self.hide_var = tk.BooleanVar(value=True)
        self.hotkeys_var = tk.BooleanVar(value=True)
        for col, (text, var) in enumerate((("Show cursor", self.cursor_var), ("3-second countdown", self.countdown_var),
                                           ("Hide LightRec while recording", self.hide_var))):
            cb = ttk.Checkbutton(checks, text=text, variable=var, command=self._sync_settings)
            cb.grid(row=col // 2, column=col % 2, sticky="w", padx=(0, 16), pady=0)
            self._lockable.append(cb)
        if IS_WINDOWS:
            cb = ttk.Checkbutton(checks, text="Global shortcuts", variable=self.hotkeys_var, command=self._hotkeys_changed)
            cb.grid(row=1, column=1, sticky="w", pady=0)
            self._lockable.append(cb)
            Tooltip(cb, HOTKEY_TEXT)

        # Gamer: the display the game is on.
        card = Card(gv, "Game", s)
        card.grid(row=0, column=0, sticky="ew", pady=(0, int(6 * s)))
        row = tk.Frame(card.body, bg=theme.PANEL)
        row.pack(fill="x")
        ttk.Label(row, text="Display", style="Panel.TLabel").pack(side="left", padx=(0, 10))
        self.game_monitor_box = ttk.Combobox(row, state="readonly", width=34)
        self.game_monitor_box.pack(side="left", fill="x", expand=True)
        self.game_monitor_box.bind("<<ComboboxSelected>>", lambda e: self._game_settings_changed())
        self._lockable.append(self.game_monitor_box)
        ttk.Label(card.body, text="The whole display is recorded, so alt-tabbing to Chrome or Discord never "
                                  "interrupts it. Black video? Set the game to borderless window mode.",
                  style="Muted.TLabel", wraplength=int(410 * s), justify="left").pack(anchor="w", pady=(6, 0))

        # Gamer: instant replay.
        card = Card(gv, "Instant replay", s)
        card.grid(row=1, column=0, sticky="ew", pady=(0, int(6 * s)))
        row = tk.Frame(card.body, bg=theme.PANEL)
        row.pack(fill="x")
        self.replay_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Keep the last", variable=self.replay_var, command=self._replay_toggled).pack(side="left")
        self.replay_box = ttk.Combobox(row, state="readonly", width=6, values=[replay_label(v) for v in REPLAY_CHOICES])
        self.replay_box.pack(side="left", padx=(2, 8))
        self.replay_box.bind("<<ComboboxSelected>>", lambda e: self._replay_seconds_changed())
        ttk.Label(row, text="of gameplay ready to save", style="Panel.TLabel").pack(side="left")
        self.replay_status = ttk.Label(card.body, text="", style="Muted.TLabel", wraplength=int(410 * s), justify="left")
        self.replay_status.pack(anchor="w", pady=(6, 0))

        # Gamer: quality.
        card = Card(gv, "Quality", s)
        card.grid(row=2, column=0, sticky="ew", pady=(0, int(6 * s)))
        row = tk.Frame(card.body, bg=theme.PANEL)
        row.pack(fill="x")
        ttk.Label(row, text="Frame rate", style="Panel.TLabel").pack(side="left")
        self.game_fps_box = ttk.Combobox(row, state="readonly", width=5, values=[str(f) for f in FPS_CHOICES])
        self.game_fps_box.pack(side="left", padx=(8, 14))
        self.game_fps_box.bind("<<ComboboxSelected>>", lambda e: self._game_settings_changed())
        ttk.Label(row, text="Quality", style="Panel.TLabel").pack(side="left")
        self.game_quality_box = ttk.Combobox(row, state="readonly", width=8, values=list(QUALITY_CHOICES))
        self.game_quality_box.pack(side="left", padx=(8, 0))
        self.game_quality_box.bind("<<ComboboxSelected>>", lambda e: self._game_settings_changed())
        # Encoder gets its own row so the window stays the same width as the Recorder view.
        row = tk.Frame(card.body, bg=theme.PANEL)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Encoder", style="Panel.TLabel").pack(side="left")
        self.encoder_box = ttk.Combobox(row, state="readonly", width=18, values=["Auto"])
        self.encoder_box.pack(side="left", padx=(8, 0))
        self.encoder_box.bind("<<ComboboxSelected>>", lambda e: self._game_settings_changed())
        self._lockable += [self.game_fps_box, self.game_quality_box, self.encoder_box]
        self.encoder_note = ttk.Label(card.body, text="", style="Muted.TLabel", wraplength=int(410 * s), justify="left")
        self.encoder_note.pack(anchor="w", pady=(6, 0))
        self.game_timer_var = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(card.body, text="Floating timer while recording", variable=self.game_timer_var,
                             command=self._sync_settings)
        cb.pack(anchor="w", pady=(4, 0))
        Tooltip(cb, "A small timer with a stop button over the game. It is never part of the video.")
        self._lockable.append(cb)
        if IS_WINDOWS:
            self.fullscreen_sound_var = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(card.body, text="Beep in exclusive fullscreen games", variable=self.fullscreen_sound_var,
                                 command=self._sync_settings)
            cb.pack(anchor="w", pady=(2, 0))
            Tooltip(cb, "Nothing can be shown over a game in exclusive fullscreen, so a quiet beep confirms the "
                        "shortcuts instead. With system sound on, the recording can pick it up.")

        # Output folder.
        card = Card(main, "Save to", s)
        card.grid(row=4, column=0, sticky="ew", pady=(0, int(8 * s)))
        self.out_var = tk.StringVar()
        entry = ttk.Entry(card.body, textvariable=self.out_var, state="readonly")
        entry.pack(side="left", fill="x", expand=True)
        b1 = ttk.Button(card.body, text="Browse", style="Small.TButton", command=self._browse)
        b1.pack(side="left", padx=(8, 4))
        ttk.Button(card.body, text="Open", style="Small.TButton",
                   command=lambda: self._open_folder()).pack(side="left")
        self._lockable.append(b1)

        # Record button and status (footer, never scrolls).
        footer = self.footer = tk.Frame(self.root, bg=theme.BG, padx=px, pady=int(8 * s))
        footer.pack(side="bottom", fill="x", before=outer)
        footer.columnconfigure(0, weight=1)
        self.record_row = tk.Frame(footer, bg=theme.BG)
        self.record_row.grid(row=0, column=0, sticky="ew")
        self.record_btn = RoundButton(self.record_row, "●  Start recording", self._toggle_record, theme.DANGER,
                                      theme.DANGER_HOVER, theme.DANGER_DOWN, height=int(46 * s),
                                      font=(theme.font_family(self.root), 12, "bold"))
        # A canvas asks for 378 px by default; keep it small so "Save clip" next to it doesn't widen the window.
        self.record_btn.configure(width=int(200 * s))
        self.record_btn.pack(side="left", fill="x", expand=True)
        self.pause_btn = ttk.Button(self.record_row, text="Pause", command=self._toggle_pause)
        self.clip_btn = RoundButton(self.record_row, "Save clip", self._save_clip, theme.ACCENT, theme.ACCENT_HOVER,
                                    theme.ACCENT_DOWN, height=int(46 * s), font=(theme.font_family(self.root), 11, "bold"))
        self.clip_btn.configure(width=int(118 * s))
        status_row = tk.Frame(footer, bg=theme.BG)
        status_row.grid(row=1, column=0, sticky="ew", pady=(int(6 * s), 0))
        self.status = ttk.Label(status_row, text="", style="MutedBg.TLabel", wraplength=int(430 * s), justify="left")
        self.status.pack(side="left", fill="x", expand=True)
        self.result_row = tk.Frame(footer, bg=theme.BG)
        self.open_btn = ttk.Button(self.result_row, text="Open video", style="Link.TButton",
                                   command=lambda: self.result and open_path(self.result.path))
        self.open_btn.pack(side="left")
        self.reveal_btn = ttk.Button(self.result_row, text="Show in folder", style="Link.TButton",
                                     command=lambda: self.result and reveal_file(self.result.path))
        self.reveal_btn.pack(side="left", padx=(6, 0))
        self.warn_label = ttk.Label(footer, text="", style="Warn.TLabel", wraplength=int(430 * s), justify="left")

        for box in (self.monitor_box, self.window_box, self.hide_box, self.mic_box, self.sys_box, self.fps_box,
                    self.quality_box, self.game_monitor_box, self.replay_box, self.game_fps_box,
                    self.game_quality_box, self.encoder_box):
            box.bind("<MouseWheel>", self._on_wheel_combobox)
            box.bind("<Button-4>", self._on_wheel_combobox)
            box.bind("<Button-5>", self._on_wheel_combobox)
        self.root.bind("<Control-Shift-KeyPress-R>", lambda e: self._toggle_record())
        self.root.bind("<Control-Shift-KeyPress-r>", lambda e: self._toggle_record())
        self.root.bind("<Control-Shift-KeyPress-P>", lambda e: self._toggle_pause())
        self.root.bind("<Control-Shift-KeyPress-p>", lambda e: self._toggle_pause())
        self.root.bind("<Control-Shift-KeyPress-S>", lambda e: self._save_clip())
        self.root.bind("<Control-Shift-KeyPress-s>", lambda e: self._save_clip())
        self.root.resizable(False, False)

    def _fit_height(self, _e=None) -> None:
        """
        Size the window to its content, capped to the screen height (then it scrolls)
        """
        req_w, req_h = self.main.winfo_reqwidth(), self.main.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, req_w, req_h))
        footer_h = self.footer.winfo_reqheight() if hasattr(self, "footer") else 0
        limit = max(int(360 * self.scale), self.root.winfo_screenheight() - int(90 * self.scale) - footer_h)
        target = min(req_h, limit)
        self._scrollable = req_h > limit
        if self._scrollable:
            self.vbar.pack(side="right", fill="y")
        else:
            self.vbar.pack_forget()
            self.canvas.yview_moveto(0)
        self.canvas.configure(width=req_w, height=target)

    def _on_wheel_combobox(self, e) -> str:
        self._on_wheel(e)
        return "break"

    def _on_wheel(self, e) -> None:
        if not self._scrollable:
            return
        widget = e.widget
        # Ignore wheel events outside the main window.
        try:
            if widget.winfo_toplevel() is not self.root:
                return
        except (AttributeError, tk.TclError):
            return
        step = -1 if getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(step, "units")

    def _audio_row(self, parent, row: int, label: str) -> tuple[ttk.Combobox, LevelMeter]:
        ttk.Label(parent, text=label, style="Panel.TLabel", width=12).grid(row=row, column=0, sticky="w", pady=3)
        box = ttk.Combobox(parent, state="readonly", width=30)
        box.grid(row=row, column=1, sticky="ew", pady=3)
        box.bind("<<ComboboxSelected>>", lambda e: self._audio_changed())
        meter = LevelMeter(parent, width=int(80 * self.scale), height=int(6 * self.scale))
        meter.grid(row=row, column=2, padx=(10, 0))
        self._lockable.append(box)
        return box, meter

    # Settings.
    def _apply_settings(self) -> None:
        st = self.settings
        self.mode_var.set(st.mode)
        labels = []
        if len(self.monitors) > 1:
            vs = capture.virtual_screen()
            labels.append(f"All displays  ({vs[2]}×{vs[3]})")
        for m in self.monitors:
            labels.append(f"Display {m.index}{' (primary)' if m.primary else ''}  ({m.rect[2]}×{m.rect[3]})")
        self.monitor_box.configure(values=labels)
        index = 0
        if len(self.monitors) > 1:
            index = st.monitor if 0 <= st.monitor <= len(self.monitors) else 1
        else:
            index = 0
        self.monitor_box.current(min(index, len(labels) - 1))
        self._update_region_label()
        self._refresh_window_box()
        if st.window_title:
            for i, w in enumerate(self.windows_cache):
                if w.title == st.window_title:
                    self.window_box.current(i)
                    break
        self.direct_var.set(st.direct_window_capture)
        self.fps_box.set(str(st.fps))
        self.quality_box.set(st.quality)
        self.cursor_var.set(st.cursor)
        self.countdown_var.set(st.countdown)
        self.hide_var.set(st.hide_while_recording)
        self.hotkeys_var.set(st.hotkeys)
        self.out_var.set(st.output_dir)
        self._fill_audio_box(self.mic_box, self.mics, st.mic_key, prefer_default=st.mic_key == "auto")
        self._fill_audio_box(self.sys_box, self.systems, st.system_key, prefer_default=False)
        self._mode_changed(initial=True)
        labels = [f"Display {m.index}{' (primary)' if m.primary else ''}  ({m.rect[2]}×{m.rect[3]})" for m in self.monitors]
        self.game_monitor_box.configure(values=labels or ["No display found"])
        self.game_monitor_box.current(min(max(st.game_monitor - 1, 0), max(len(labels) - 1, 0)))
        self.game_fps_box.set(str(st.game_fps))
        self.game_quality_box.set(st.game_quality)
        self.replay_var.set(st.replay_enabled)
        self.replay_box.current(REPLAY_CHOICES.index(st.replay_seconds))
        self.game_timer_var.set(st.game_timer)
        if IS_WINDOWS:
            self.fullscreen_sound_var.set(st.fullscreen_sound)
        self._fill_encoder_box()
        self.view_var.set(st.view)
        self._view_changed(initial=True)
        self._restart_monitors()

    def _fill_audio_box(self, box: ttk.Combobox, devices: list[AudioDevice], key: str, prefer_default: bool) -> None:
        values = ["None"] + [d.label() for d in devices]
        box.configure(values=values)
        box.current(0)
        if key and key != "auto":
            for i, d in enumerate(devices):
                if d.key == key:
                    box.current(i + 1)
                    return
        if prefer_default:
            for i, d in enumerate(devices):
                if d.is_default:
                    box.current(i + 1)
                    return

    def _selected_device(self, box: ttk.Combobox, devices: list[AudioDevice]) -> Optional[AudioDevice]:
        idx = box.current() - 1
        return devices[idx] if 0 <= idx < len(devices) else None

    def _sync_settings(self) -> None:
        st = self.settings
        st.mode = self.mode_var.get()
        if len(self.monitors) > 1:
            st.monitor = self.monitor_box.current()
        win = self._selected_window()
        st.window_title = win.title if win else st.window_title
        st.direct_window_capture = bool(self.direct_var.get())
        try:
            st.fps = int(self.fps_box.get())
        except ValueError:
            st.fps = 30
        st.quality = self.quality_box.get() or "Medium"
        st.cursor = bool(self.cursor_var.get())
        st.countdown = bool(self.countdown_var.get())
        st.hide_while_recording = bool(self.hide_var.get())
        st.hotkeys = bool(self.hotkeys_var.get())
        mic = self._selected_device(self.mic_box, self.mics)
        sysdev = self._selected_device(self.sys_box, self.systems)
        st.mic_key = mic.key if mic else ""
        st.system_key = sysdev.key if sysdev else ""
        st.output_dir = self.out_var.get()
        st.view = self.view_var.get()
        st.game_monitor = max(1, self.game_monitor_box.current() + 1)
        try:
            st.game_fps = int(self.game_fps_box.get())
        except ValueError:
            st.game_fps = 30
        st.game_quality = self.game_quality_box.get() or "Medium"
        st.game_encoder = self._selected_encoder()
        st.replay_enabled = bool(self.replay_var.get())
        idx = self.replay_box.current()
        st.replay_seconds = REPLAY_CHOICES[idx] if 0 <= idx < len(REPLAY_CHOICES) else 30
        st.game_timer = bool(self.game_timer_var.get())
        if IS_WINDOWS:
            st.fullscreen_sound = bool(self.fullscreen_sound_var.get())
        st.save()

    def _place_window(self) -> None:
        self.root.update_idletasks()
        self._fit_height()
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        vs = capture.virtual_screen()
        x = y = None
        geo = self.settings.window_geometry
        if geo and "+" in geo:
            try:
                parts = geo.split("+")
                x, y = int(parts[1]), int(parts[2])
            except (ValueError, IndexError):
                x = y = None
        if x is None or not (vs[0] <= x < vs[0] + vs[2] - 100 and vs[1] <= y < vs[1] + vs[3] - 100):
            primary = next((m for m in self.monitors if m.primary), self.monitors[0] if self.monitors else None)
            rect = primary.rect if primary else vs
            x = rect[0] + (rect[2] - w) // 2
            y = rect[1] + max(0, (rect[3] - h) // 2 - 20)
        self.root.geometry(f"+{x}+{y}")

    def _idle_text(self) -> str:
        if not self.ffmpeg:
            return "ffmpeg is needed to encode video - LightRec will set it up when you start recording."
        note = getattr(self, "_shortcut_note", "" if IS_WINDOWS else "Ctrl+Shift+R starts / stops while LightRec is focused")
        if self._in_game_view():
            if self.game and self.game.replay:
                return f"Instant replay is on  ·  {note}" if note else "Instant replay is on"
            return f"Ready  ·  {note}" if note else "Ready"
        return f"Ready  ·  {note}" if note else "Ready"

    def _post_init(self) -> None:
        if IS_WINDOWS:
            self._exclude(self.root)
            self._hotkeys_changed()
        self._set_status(self._idle_text())
        self._probe_encoders()
        if self._start_hidden:
            # Started with --tray: straight to gamer mode, out of the way.
            self.view_var.set("game")
            self._view_changed()
            self.root.after(200, self._go_background)

    def _exclude(self, top) -> None:
        if IS_WINDOWS:
            try:
                top.update_idletasks()
                winapi.exclude_from_capture(winapi.tk_hwnd(top))
            except Exception:
                pass

    # Handlers.
    def _mode_changed(self, initial: bool = False) -> None:
        mode = self.mode_var.get()
        for frame in (self.screen_frame, self.region_frame, self.window_frame):
            frame.pack_forget()
        {"screen": self.screen_frame, "region": self.region_frame, "window": self.window_frame}[mode].pack(fill="x")
        if mode == "window" and not self.windows_cache:
            self._refresh_window_box()
        direct_hint = mode == "window" and IS_WINDOWS and self.direct_var.get()
        self.hide_hint.configure(text=(
            "When recording the window itself, anything covering it is already left out." if direct_hint else
            "Replaced with what's behind them. LightRec's own windows and Notes are never recorded." if IS_WINDOWS
            else "Hidden windows are covered with a solid block in the recording."))
        if not initial:
            self._sync_settings()

    def _update_region_label(self) -> None:
        r = self.settings.region
        if r:
            self.region_label.configure(text=f"{r[2]} × {r[3]}  at  {r[0]}, {r[1]}")
        else:
            self.region_label.configure(text="No area selected")

    def _select_region(self) -> None:
        bounds = capture.virtual_screen()
        # Overlays spanning monitors are unreliable on macOS.
        if IS_MAC and self.monitors:
            bounds = next((m.rect for m in self.monitors if m.primary), self.monitors[0].rect)
        self.root.withdraw()
        if self.notes and self.notes.visible():
            self.notes.top.withdraw()
            notes_was_visible = True
        else:
            notes_was_visible = False

        def done(rect):
            self.root.deiconify()
            self.root.lift()
            if notes_was_visible and self.notes:
                self.notes.top.deiconify()
            if rect:
                self.settings.region = list(rect)
                self._update_region_label()
                self._sync_settings()

        self.root.after(150, lambda: RegionSelector(self.root, bounds, done, self.scale))

    def _refresh_window_box(self) -> None:
        current = self._selected_window()
        self.windows_cache = capture.list_windows()
        labels = [self._short(w.label()) for w in self.windows_cache]
        if not labels:
            labels = ["No windows found" if capture.window_supported() else "Window listing needs wmctrl (Linux) or pyobjc (macOS)"]
        self.window_box.configure(values=labels)
        if current:
            for i, w in enumerate(self.windows_cache):
                if w.handle == current.handle:
                    self.window_box.current(i)
                    return
        if self.windows_cache and self.window_box.current() < 0:
            self.window_box.current(0)

    @staticmethod
    def _short(text: str, limit: int = 64) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _selected_window(self) -> Optional[WindowInfo]:
        idx = self.window_box.current()
        return self.windows_cache[idx] if 0 <= idx < len(self.windows_cache) else None

    def _refresh_hide_box(self) -> None:
        hidden = {w.handle for w in self.hidden_windows}
        self._hide_candidates = [w for w in capture.list_windows() if w.handle not in hidden]
        values = [self._short(w.label()) for w in self._hide_candidates] or ["No other windows found"]
        self.hide_box.configure(values=values)

    def _hide_selected(self, _e=None) -> None:
        idx = self.hide_box.current()
        candidates = getattr(self, "_hide_candidates", [])
        if 0 <= idx < len(candidates):
            self.hidden_windows.append(candidates[idx])
            self._render_chips()
        self.hide_box.set("Add a window to hide…")
        self.root.focus_set()

    def _render_chips(self) -> None:
        for child in self.chips.winfo_children():
            child.destroy()
        for w in self.hidden_windows:
            chip = Chip(self.chips, self._short(w.title, 56), lambda w=w: self._remove_hidden(w), self.scale)
            chip.pack(fill="x", pady=(6, 0))

    def _remove_hidden(self, win: WindowInfo) -> None:
        self.hidden_windows = [w for w in self.hidden_windows if w.handle != win.handle]
        self._render_chips()

    def _audio_changed(self) -> None:
        self._sync_settings()
        self._restart_monitors()
        self._restart_engine()

    def _restart_monitors(self) -> None:
        self._stop_monitors()
        # The engine already has the devices open and reports their levels.
        if self.session or self.game:
            return
        for box, devices, meter in ((self.mic_box, self.mics, self.mic_meter), (self.sys_box, self.systems, self.sys_meter)):
            dev = self._selected_device(box, devices)
            meter.set_enabled(dev is not None)
            if dev is None:
                continue
            rec = AudioRecorder(dev, None)
            try:
                rec.start()
                self.monitors_audio.append(rec)
            except Exception as exc:
                self._set_status(f"{dev.name}: {exc}", warn=True)

    def _stop_monitors(self) -> None:
        for rec in self.monitors_audio:
            try:
                rec.stop()
            except Exception:
                pass
        self.monitors_audio = []

    def _hotkeys_changed(self) -> None:
        if not IS_WINDOWS:
            return
        self._sync_settings()
        if self.hotkeys:
            self.hotkeys.stop()
            self.hotkeys = None
        if self.hotkeys_var.get():
            bindings = {HOTKEY_RECORD: (winapi.MOD_CONTROL | winapi.MOD_SHIFT, 0x52),
                        HOTKEY_PAUSE: (winapi.MOD_CONTROL | winapi.MOD_SHIFT, 0x50)}
            # Ctrl+Shift+S is "Save as" in a lot of apps, so only grab it while a replay buffer exists.
            if self.game and self.game.replay:
                bindings[HOTKEY_CLIP] = (winapi.MOD_CONTROL | winapi.MOD_SHIFT, 0x53)
            listener = winapi.HotkeyListener(bindings, lambda hid: self.events.put(("hotkey", {"id": hid})))
            listener.start()
            listener.wait_ready()
            self.hotkeys = listener
            text = GAME_HOTKEY_TEXT if self._in_game_view() else HOTKEY_TEXT
            self._shortcut_note = "Shortcuts are in use by another app - use the buttons instead" if listener.failed else text
        else:
            self._shortcut_note = "Global shortcuts are off"
        if not self.session and not self._game_recording():
            self._set_status(self._idle_text())

    def _browse(self) -> None:
        path = filedialog.askdirectory(parent=self.root, initialdir=self.out_var.get() or os.path.expanduser("~"),
                                       title="Choose where recordings are saved")
        if path:
            self.out_var.set(os.path.normpath(path))
            self._sync_settings()

    def _open_folder(self) -> None:
        path = self.out_var.get()
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        open_path(path)

    def _toggle_notes(self) -> None:
        if self.notes is None:
            self.notes = NotesPanel(self.root, self.settings.notes, self.settings.notes_geometry,
                                    self._notes_changed, self._exclude if IS_WINDOWS else None, self.scale)
            self._exclude(self.notes.top)
        else:
            self.notes.toggle()

    def _notes_changed(self, text: str, geometry: str) -> None:
        self.settings.notes = text
        self.settings.notes_geometry = geometry
        self.settings.save()

    def _show_help(self) -> None:
        details = [
            HOTKEY_TEXT if IS_WINDOWS else "Ctrl+Shift+R starts/stops while LightRec is focused.",
            "Gamer mode: Ctrl+Shift+S saves the last seconds while instant replay is on.",
            f"ffmpeg: {self.ffmpeg or 'not found'}",
            f"Audio backend: {audio.backend_name()}",
            f"Settings: {paths.settings_path()}",
            f"Logs: {paths.log_dir()}",
        ]
        dialog = AboutDialog(self.root, __version__, details, self.scale)
        self._exclude(dialog.top)

    # Gamer mode.
    def _in_game_view(self) -> bool:
        return self.view_var.get() == "game"

    def _game_recording(self) -> bool:
        return bool(self.game and self.game.recording)

    def _view_changed(self, initial: bool = False) -> None:
        game = self._in_game_view()
        if game:
            self.rec_view.grid_remove()
            self.game_view.grid()
            self.clip_btn.pack(side="left", padx=(8, 0), fill="y")
            if IS_WINDOWS:
                self.tray_btn.pack(side="right", padx=(0, 4), after=self.notes_btn)
        else:
            self.game_view.grid_remove()
            self.rec_view.grid()
            self.clip_btn.pack_forget()
            self.tray_btn.pack_forget()
        self._update_replay_status()
        if not initial:
            self._sync_settings()
            if IS_WINDOWS:
                self._hotkeys_changed()
            else:
                self._set_status(self._idle_text())

    def _game_rect(self):
        idx = self.game_monitor_box.current()
        if 0 <= idx < len(self.monitors):
            return self.monitors[idx].rect
        return self._primary_rect()

    def _probe_encoders(self) -> None:
        """
        Find out in the background which GPU encoders really work here
        """
        if not self.ffmpeg:
            self.encoder_note.configure(text="ffmpeg is set up on the first recording.")
            return
        self.encoder_note.configure(text="Checking for a GPU encoder…")

        def work(ffmpeg=self.ffmpeg):
            names = encoder.probe_encoders(ffmpeg)
            self.events.put(("encoders", {"names": names}))

        threading.Thread(target=work, name="lightrec-probe", daemon=True).start()

    def _fill_encoder_box(self) -> None:
        names = self.encoders or []
        values = ["Auto"] + [encoder.ENCODER_LABELS.get(n, n) for n in names]
        self.encoder_box.configure(values=values)
        self.encoder_box.current(0)
        wanted = self.settings.game_encoder
        if wanted in names:
            self.encoder_box.current(names.index(wanted) + 1)
        if self.encoders is None:
            return
        chosen = encoder.pick_encoder(self.ffmpeg or "", wanted) if self.ffmpeg else "libx264"
        label = encoder.ENCODER_LABELS.get(chosen, chosen)
        if chosen in dict(encoder.HW_ENCODERS):
            self.encoder_note.configure(text=f"Encoding on the GPU with {label} - the CPU stays free for the game.")
        else:
            self.encoder_note.configure(text="No GPU encoder found, encoding on the CPU. Medium quality at 30 fps "
                                             "is the safe choice while gaming.")

    def _selected_encoder(self) -> str:
        # Until the probe is done the box only says "Auto"; don't lose a pinned choice over that.
        if self.encoders is None:
            return self.settings.game_encoder
        idx = self.encoder_box.current() - 1
        return self.encoders[idx] if 0 <= idx < len(self.encoders) else "auto"

    def _selected_audio(self) -> list[AudioDevice]:
        return [d for d in (self._selected_device(self.mic_box, self.mics),
                            self._selected_device(self.sys_box, self.systems)) if d]

    def _need_ffmpeg(self, then) -> bool:
        """
        True when ffmpeg is ready; otherwise sets it up and calls `then` afterwards
        """
        if self.ffmpeg:
            return True

        def done(path):
            if path:
                self.ffmpeg = path
                self._set_status(self._idle_text())
                self._probe_encoders()
                then()

        FfmpegDialog(self.root, done, self.scale)
        return False

    def _start_engine(self) -> bool:
        if self.game:
            return True
        self._sync_settings()
        st = self.settings
        try:
            os.makedirs(st.output_dir, exist_ok=True)
            source = capture.make_screen_source(self._game_rect(), [], bool(self.cursor_var.get()),
                                                None if IS_WINDOWS else (lambda: self.pointer))
        except Exception as exc:
            messagebox.showerror("Can't start gamer mode", str(exc), parent=self.root)
            return False
        chosen = encoder.pick_encoder(self.ffmpeg or "", st.game_encoder)
        cfg = GameConfig(source=source, ffmpeg=self.ffmpeg or "", output_dir=st.output_dir, fps=st.game_fps,
                         quality=st.game_quality, encoder=chosen, audio=self._selected_audio())
        engine = GameEngine(cfg, lambda name, data: self.events.put((name, data)))
        try:
            engine.start()
        except Exception as exc:
            source.close()
            messagebox.showerror("Can't start gamer mode", str(exc), parent=self.root)
            return False
        self._stop_monitors()
        self.game = engine
        self.mic_meter.set_enabled(any(not d.is_system for d in cfg.audio))
        self.sys_meter.set_enabled(any(d.is_system for d in cfg.audio))
        if engine.warnings:
            self._set_status("; ".join(engine.warnings), warn=True)
        return True

    def _stop_engine_if_idle(self) -> None:
        game = self.game
        if game and game.replay is None and game.recording is None:
            self.game = None
            game.stop()
            self._restart_monitors()
            self.dot.itemconfigure(self._dot_item, fill=theme.MUTED)
            if IS_WINDOWS:
                self._hotkeys_changed()
            self._update_replay_status()
            self.clip_btn.configure_button(enabled=False)
            self._sync_tray()

    def _restart_engine(self) -> None:
        """
        Apply changed settings to a running replay buffer (recordings lock the settings instead)
        """
        if not self.game or self._game_recording():
            return
        self.game.disarm_replay()
        self._stop_engine_if_idle()
        if self.replay_var.get():
            self._arm_replay()

    def _game_settings_changed(self) -> None:
        self._sync_settings()
        self._restart_engine()

    def _replay_toggled(self) -> None:
        self._sync_settings()
        if self.replay_var.get():
            self._arm_replay()
        else:
            if self.game:
                self.game.disarm_replay()
            self._stop_engine_if_idle()
            self._set_status(self._idle_text())

    def _replay_seconds_changed(self) -> None:
        self._sync_settings()
        if self.game and self.game.replay:
            self.game.arm_replay(self.settings.replay_seconds)

    def _arm_replay(self) -> None:
        if not self._need_ffmpeg(self._arm_replay):
            return
        if not self._start_engine():
            self.replay_var.set(False)
            self._sync_settings()
            return
        self.game.arm_replay(self.settings.replay_seconds)

    def _save_clip(self) -> None:
        if not self.game or not self.game.replay:
            if self._in_game_view():
                self._set_status("Turn on instant replay first, then Ctrl+Shift+S saves a clip.", warn=True)
            return
        # No "buffer is empty" check here: the save waits up to a few seconds for the next fragment.
        self.game.save_replay()
        self._set_status("Saving the clip…")

    def _toggle_game_record(self) -> None:
        if self._game_recording():
            self.game.stop_recording()
            return
        if not self._need_ffmpeg(self._toggle_game_record):
            return
        if not self._start_engine():
            return
        self.result = None
        self.result_row.grid_forget()
        self.warn_label.grid_forget()
        self.game.start_recording()

    def _update_replay_status(self) -> None:
        game = self.game
        if not game or not game.replay:
            text = "Off" if not self.replay_var.get() else "Starting…"
            if not self.replay_var.get():
                text += "  ·  Turn it on before you play; LightRec remembers it."
            self.replay_status.configure(text=text)
            return
        replay = game.replay
        buffered = min(replay.buffered, replay.seconds)
        if buffered < replay.seconds - 0.5:
            text = f"On  ·  buffering {int(buffered)} / {replay.seconds} s"
        else:
            text = f"On  ·  {replay_label(replay.seconds)} ready"
        text += f"  ·  {format_size(replay.bytes)} in memory  ·  Ctrl+Shift+S saves it"
        self.replay_status.configure(text=text)

    @staticmethod
    def _fullscreen_game() -> bool:
        return IS_WINDOWS and winapi.exclusive_fullscreen()

    def _cue(self, kind: str) -> None:
        if self.settings.fullscreen_sound:
            cues.play(kind)

    def _show_toast(self, text: str, color: str = theme.OK, cue: Optional[str] = None) -> None:
        if self._toast:
            self._toast.destroy()
            self._toast = None
        if self._fullscreen_game():
            # A popup can't be seen over an exclusive fullscreen game and can knock it out of fullscreen,
            # so a short sound stands in for it.
            if cue:
                self._cue(cue)
            return
        try:
            self._toast = Toast(self.root, text, self._game_rect(), self._exclude if IS_WINDOWS else None,
                                self.scale, color=color)
        except tk.TclError:
            self._toast = None

    # Background / tray.
    def _tray_menu(self) -> list:
        # Runs on the tray thread, so it only reads state, and reads the engine once since it can go away meanwhile.
        game = self.game
        armed = bool(game and game.replay)
        recording = bool(game and game.recording)
        return [
            (TRAY_CLIP, f"Save the last {replay_label(self.settings.replay_seconds)}\tCtrl+Shift+S", armed, False),
            (TRAY_RECORD, ("Stop recording" if recording else "Start recording") + "\tCtrl+Shift+R", True, False),
            (TRAY_REPLAY, "Instant replay", True, armed),
            None,
            (TRAY_OPEN, "Open LightRec", True, False),
            (TRAY_QUIT, "Quit LightRec", True, False),
        ]

    def _sync_tray(self) -> None:
        """
        Keep the tray icon around while gamer mode runs or the window is hidden, colored by state
        """
        if not IS_WINDOWS:
            return
        if not self.game and not self._in_tray:
            if self.tray:
                tray, self.tray = self.tray, None
                tray.stop()
            self._tray_state = ()
            return
        if self.tray is None:
            tray = winapi.TrayIcon("LightRec", lambda: self.events.put(("tray", {"id": TRAY_OPEN})),
                                   lambda mid: self.events.put(("tray", {"id": mid})), self._tray_menu)
            tray.update(icon=winapi.make_dot_icon(theme.MUTED, theme.PANEL3))
            tray.start()
            tray.wait_ready()
            if not tray.ok:
                tray.stop()
                return
            self.tray = tray
        if self._game_recording():
            state = (theme.DANGER, "Recording")
        elif self.game and self.game.replay:
            state = (theme.ACCENT, f"Instant replay on  ·  the last {replay_label(self.game.replay.seconds)} are ready")
        else:
            state = (theme.MUTED, "Idle")
        if state != self._tray_state:
            self._tray_state = state
            self.tray.update(f"LightRec  ·  {state[1]}", winapi.make_dot_icon(state[0], theme.PANEL3))

    def _go_background(self) -> None:
        if not IS_WINDOWS:
            self.root.iconify()
            return
        self._in_tray = True
        self._sync_tray()
        if self.tray is None:
            # No tray icon available, minimizing is the next best thing.
            self._in_tray = False
            self.root.iconify()
            return
        if self.notes and self.notes.visible():
            self.notes.hide()
        self.root.withdraw()
        self._show_toast("LightRec keeps running in the tray, next to the clock", theme.ACCENT)

    def _show_from_tray(self) -> None:
        self._in_tray = False
        self._restore_main()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        self._sync_tray()

    def _tray_event(self, mid: int) -> None:
        if mid == TRAY_OPEN:
            self._show_from_tray()
        elif mid == TRAY_CLIP:
            self._save_clip()
        elif mid == TRAY_RECORD:
            if not self._in_game_view():
                self.view_var.set("game")
                self._view_changed()
            self._toggle_game_record()
        elif mid == TRAY_REPLAY:
            self.replay_var.set(not self.replay_var.get())
            self._replay_toggled()
        elif mid == TRAY_QUIT:
            if self._game_recording():
                self._quit_after_finish = True
                self.game.stop_recording()
                self._show_toast("Saving the recording, then quitting…", theme.ACCENT)
            else:
                self._quit()

    def _game_event(self, name: str, data: dict) -> None:
        self._dispatch_game_event(name, data)
        self._sync_tray()

    def _dispatch_game_event(self, name: str, data: dict) -> None:
        if name == "encoders":
            self.encoders = data.get("names", [])
            self._fill_encoder_box()
            # Instant replay was on last time, bring it back.
            if self.settings.replay_enabled and self.replay_var.get() and not self.game and self.ffmpeg:
                self._arm_replay()
        elif name == "replay_armed":
            self.dot.itemconfigure(self._dot_item, fill=theme.ACCENT if not self._game_recording() else theme.DANGER)
            self.clip_btn.configure_button(enabled=True)
            if IS_WINDOWS:
                self._hotkeys_changed()
            else:
                self._set_status(self._idle_text())
            self._update_replay_status()
            self._show_toast(f"Instant replay on  ·  Ctrl+Shift+S saves the last {replay_label(data.get('seconds', 30))}",
                             theme.ACCENT)
        elif name == "replay_off":
            self._update_replay_status()
        elif name == "replay_error":
            self.replay_var.set(False)
            self._sync_settings()
            self._stop_engine_if_idle()
            self._set_status(f"Instant replay stopped: {data.get('message', '')}", warn=True)
        elif name == "clip_saved":
            result: RecordingResult = data["result"]
            self.result = result
            prefix = "Recording…  ·  " if self._game_recording() else ""
            self._set_status(f"{prefix}Saved clip {os.path.basename(result.path)}  ·  {format_size(result.size)}  ·  "
                             f"{format_time(result.duration)}")
            self.result_row.grid(row=2, column=0, sticky="w", pady=(2, 0))
            self._show_toast(f"Clip saved  ·  {format_time(result.duration)}", cue="saved")
        elif name == "clip_error":
            self._set_status(f"Clip not saved: {data.get('message', '')}", warn=True)
            self._show_toast("Clip not saved", theme.DANGER, cue="error")
        elif name == "rec_started":
            self._set_recording_ui(True, pause=False)
            if self.game_timer_var.get():
                pos = tuple(self.settings.minibar_position) if self.settings.minibar_position else None
                self.minibar = MiniBar(self.root, self._toggle_pause, self._toggle_record, pos, capture.virtual_screen(),
                                       self._minibar_moved, self._exclude if IS_WINDOWS else None, self.scale,
                                       show_pause=False, visible=not self._fullscreen_game())
            self._set_status("Recording…")
            self._show_toast("Recording", theme.DANGER, cue="start")
        elif name == "rec_finalizing":
            if self.minibar:
                self.minibar.set_saving()
            self.record_btn.configure_button(text="Saving…", enabled=False)
            self._set_status("Saving the recording…")
        elif name in ("rec_finished", "rec_error"):
            if self.minibar:
                self.minibar.destroy()
                self.minibar = None
            self._set_recording_ui(False)
            self._stop_engine_if_idle()
            self.dot.itemconfigure(self._dot_item, fill=theme.ACCENT if self.game and self.game.replay else theme.MUTED)
            if name == "rec_finished":
                result = data["result"]
                self.result = result
                self._set_status(f"Saved {os.path.basename(result.path)}  ·  {format_size(result.size)}  ·  "
                                 f"{format_time(result.duration)}")
                self.result_row.grid(row=2, column=0, sticky="w", pady=(2, 0))
                if result.warnings:
                    self.warn_label.configure(text="Note: " + "  ".join(result.warnings))
                    self.warn_label.grid(row=3, column=0, sticky="w", pady=(4, 0))
                self._show_toast(f"Recording saved  ·  {format_time(result.duration)}", cue="saved")
            else:
                self._set_status("Recording failed", warn=True)
                messagebox.showerror("Recording failed", data.get("message", "Unknown error"), parent=self.root)
            if self._quit_after_finish:
                self.root.after(100, self._quit)
        elif name == "engine_error":
            game, self.game = self.game, None
            if game:
                game.stop()
            self.replay_var.set(False)
            self._sync_settings()
            self._set_recording_ui(False)
            self._restart_monitors()
            self._set_status("Gamer mode stopped", warn=True)
            messagebox.showerror("Gamer mode stopped", data.get("message", "Unknown error"), parent=self.root)

    # Recording.
    def _toggle_record(self) -> None:
        if self.session:
            if self.session.state in ("recording", "paused"):
                self.session.stop()
            return
        if self._in_game_view() or self._game_recording():
            self._toggle_game_record()
            return
        if self._countdown:
            return
        self._start_flow()

    def _toggle_pause(self) -> None:
        if self.session and self.session.state in ("recording", "paused"):
            self.session.toggle_pause()

    def _start_flow(self) -> None:
        if not self.ffmpeg:
            def done(path):
                if path:
                    self.ffmpeg = path
                    self._set_status(self._idle_text())
                    self._start_flow()
            FfmpegDialog(self.root, done, self.scale)
            return
        self._sync_settings()
        try:
            os.makedirs(self.settings.output_dir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Can't save there", f"The folder could not be created:\n{exc}", parent=self.root)
            return
        try:
            source = self._build_source()
        except Exception as exc:
            messagebox.showerror("Can't start recording", str(exc), parent=self.root)
            return
        devices = [d for d in (self._selected_device(self.mic_box, self.mics),
                               self._selected_device(self.sys_box, self.systems)) if d]
        self._stop_monitors()
        self.result = None
        self.result_row.grid_forget()
        self.warn_label.grid_forget()
        hide = self.hide_var.get()
        if hide:
            self.root.withdraw()

        def begin():
            self._countdown = None
            self._begin(source, devices)

        def cancel():
            self._countdown = None
            source.close()
            self._restore_main()
            self._restart_monitors()

        # No countdown over an exclusive fullscreen game: it couldn't be seen, and it could knock the game out.
        if self.countdown_var.get() and not self._fullscreen_game():
            center = self._primary_rect()
            self._countdown = Countdown(self.root, 3, center, begin, cancel,
                                        self._exclude if IS_WINDOWS else None, self.scale)
        else:
            begin()

    def _primary_rect(self):
        primary = next((m for m in self.monitors if m.primary), self.monitors[0] if self.monitors else None)
        return primary.rect if primary else capture.virtual_screen()

    def _build_source(self) -> capture.FrameSource:
        mode = self.mode_var.get()
        hidden = [w.handle for w in self.hidden_windows]
        cursor = bool(self.cursor_var.get())
        pointer = None if IS_WINDOWS else (lambda: self.pointer)
        vs = capture.virtual_screen()
        if mode == "screen":
            idx = self.monitor_box.current()
            if len(self.monitors) > 1:
                rect = vs if idx == 0 else self.monitors[idx - 1].rect
            else:
                rect = self.monitors[0].rect if self.monitors else vs
            return capture.make_screen_source(rect, hidden, cursor, pointer)
        if mode == "region":
            if not self.settings.region:
                raise ValueError("Select the area you want to record first.")
            rect = tuple(int(v) for v in self.settings.region)
            clipped = capture.intersect(rect, vs)
            if clipped is None or clipped[2] < 16 or clipped[3] < 16:
                raise ValueError("The saved area is off-screen. Please select it again.")
            return capture.make_screen_source(clipped, hidden, cursor, pointer)
        win = self._selected_window()
        if win is None:
            raise ValueError("Choose the window you want to record.")
        hidden = [h for h in hidden if h != win.handle]
        return capture.make_window_source(win.handle, hidden, cursor, bool(self.direct_var.get()), pointer)

    def _begin(self, source: capture.FrameSource, devices: list[AudioDevice]) -> None:
        cfg = SessionConfig(source=source, ffmpeg=self.ffmpeg or "", output_dir=self.settings.output_dir,
                            fps=self.settings.fps, quality=self.settings.quality, audio=devices)
        try:
            session = RecordingSession(cfg, lambda name, data: self.events.put((name, data)))
            session.start()
        except Exception as exc:
            source.close()
            self._restore_main()
            self._restart_monitors()
            messagebox.showerror("Can't start recording", str(exc), parent=self.root)
            return
        self.session = session
        pos = tuple(self.settings.minibar_position) if self.settings.minibar_position else None
        fullscreen = self._fullscreen_game()
        self.minibar = MiniBar(self.root, self._toggle_pause, self._toggle_record, pos, capture.virtual_screen(),
                               self._minibar_moved, self._exclude if IS_WINDOWS else None, self.scale,
                               visible=not fullscreen)
        if fullscreen:
            self._cue("start")
        self._set_recording_ui(True)
        if session.warnings:
            self._set_status("Recording  ·  " + "; ".join(session.warnings), warn=True)
        else:
            self._set_status("Recording…")

    def _minibar_moved(self, x: int, y: int) -> None:
        self.settings.minibar_position = [x, y]
        self.settings.save()

    def _set_recording_ui(self, recording: bool, pause: bool = True) -> None:
        for widget in self._lockable:
            try:
                widget.state(["disabled"] if recording else ["!disabled"])
            except (AttributeError, tk.TclError):
                try:
                    widget.configure(state="disabled" if recording else "normal")
                except tk.TclError:
                    pass
        if recording:
            self.record_btn.configure_button(text="■  Stop recording", colors=(theme.PANEL3, theme.PANEL4, theme.PANEL2))
            self.pause_btn.configure(text="Pause")
            if pause:
                self.pause_btn.pack(side="left", padx=(8, 0), fill="y")
            self.dot.itemconfigure(self._dot_item, fill=theme.DANGER)
        else:
            self.record_btn.configure_button(text="●  Start recording",
                                             colors=(theme.DANGER, theme.DANGER_HOVER, theme.DANGER_DOWN), enabled=True)
            self.pause_btn.pack_forget()
            self.dot.itemconfigure(self._dot_item, fill=theme.MUTED)

    def _restore_main(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            pass

    def _handle_event(self, name: str, data: dict) -> None:
        if name == "hotkey":
            if data.get("id") == HOTKEY_RECORD:
                self._toggle_record()
            elif data.get("id") == HOTKEY_PAUSE:
                self._toggle_pause()
            elif data.get("id") == HOTKEY_CLIP:
                self._save_clip()
            return
        if name == "tray":
            self._tray_event(int(data.get("id", 0)))
            return
        if name.startswith(("replay_", "clip_", "rec_", "engine_")) or name == "encoders":
            self._game_event(name, data)
            return
        if name == "paused":
            if self.minibar:
                self.minibar.set_paused(True)
            self.pause_btn.configure(text="Resume")
            self._set_status("Paused")
        elif name == "resumed":
            if self.minibar:
                self.minibar.set_paused(False)
            self.pause_btn.configure(text="Pause")
            self._set_status("Recording…")
        elif name == "finalizing":
            if self.minibar:
                self.minibar.set_saving()
            self.record_btn.configure_button(text="Saving…", enabled=False)
            self.pause_btn.pack_forget()
            self._set_status("Saving the recording…")
        elif name == "info":
            self._set_status(data.get("message", ""), warn=True)
        elif name == "finished":
            result: RecordingResult = data["result"]
            self.result = result
            self._finish_session()
            if self._fullscreen_game():
                self._cue("saved")
            self._set_status(f"Saved {os.path.basename(result.path)}  ·  {format_size(result.size)}  ·  "
                             f"{format_time(result.duration)}")
            self.result_row.grid(row=2, column=0, sticky="w", pady=(2, 0))
            if result.warnings:
                self.warn_label.configure(text="Note: " + "  ".join(result.warnings))
                self.warn_label.grid(row=3, column=0, sticky="w", pady=(4, 0))
        elif name == "error":
            self._finish_session()
            self._set_status("Recording failed", warn=True)
            messagebox.showerror("Recording failed", data.get("message", "Unknown error"), parent=self.root)
        if name in ("finished", "error") and self._quit_after_finish:
            self.root.after(100, self._quit)

    def _finish_session(self) -> None:
        self.session = None
        # Checked before the bar goes away, since clicking its stop button makes it the foreground window.
        elsewhere = IS_WINDOWS and not winapi.foreground_is_ours()
        if self.minibar:
            self.minibar.destroy()
            self.minibar = None
        self._set_recording_ui(False)
        if elsewhere:
            # Stopped with the shortcut from a game or another app: come back on the taskbar, not on top of it.
            if self.root.state() == "withdrawn":
                self.root.iconify()
        else:
            self._restore_main()
        self._restart_monitors()

    def _set_status(self, text: str, warn: bool = False) -> None:
        self.status.configure(text=text, style="Warn.TLabel" if warn else "MutedBg.TLabel")

    # Main loop.
    def _tick(self) -> None:
        self._tick_count += 1
        try:
            while True:
                name, data = self.events.get_nowait()
                self._handle_event(name, data)
        except queue.Empty:
            pass
        if IS_WINDOWS and self.minibar and self._tick_count % 5 == 0:
            # Keep the bar above borderless games, and off exclusive fullscreen ones.
            self.minibar.set_visible(not winapi.exclusive_fullscreen())
        session = self.session
        if session:
            levels = session.levels()
            mic = self._selected_device(self.mic_box, self.mics)
            sysdev = self._selected_device(self.sys_box, self.systems)
            self.mic_meter.set(levels.get(mic.key, 0.0) if mic else 0.0)
            self.sys_meter.set(levels.get(sysdev.key, 0.0) if sysdev else 0.0)
            if self.minibar and session.state in ("recording", "paused"):
                self.minibar.set_time(format_time(session.elapsed))
                if self._tick_count % 5 == 0:
                    self.minibar.blink(session.state == "paused")
            if not IS_WINDOWS:
                try:
                    self.pointer = self.root.winfo_pointerxy()
                except tk.TclError:
                    pass
        elif self.game:
            levels = self.game.levels()
            mic = self._selected_device(self.mic_box, self.mics)
            sysdev = self._selected_device(self.sys_box, self.systems)
            self.mic_meter.set(levels.get(mic.key, 0.0) if mic else 0.0)
            self.sys_meter.set(levels.get(sysdev.key, 0.0) if sysdev else 0.0)
            if self.minibar and self.game.recording:
                self.minibar.set_time(format_time(self.game.elapsed))
                if self._tick_count % 5 == 0:
                    self.minibar.blink(False)
            if self._tick_count % 10 == 0:
                self._update_replay_status()
            if not IS_WINDOWS:
                try:
                    self.pointer = self.root.winfo_pointerxy()
                except tk.TclError:
                    pass
        else:
            mic = self._selected_device(self.mic_box, self.mics)
            sysdev = self._selected_device(self.sys_box, self.systems)
            for rec in self.monitors_audio:
                if mic and rec.device.key == mic.key:
                    self.mic_meter.set(rec.level)
                if sysdev and rec.device.key == sysdev.key:
                    self.sys_meter.set(rec.level)
        self.root.after(100, self._tick)

    # Exit.
    def on_close(self) -> None:
        if IS_WINDOWS and self.game and (self.game.replay or self.game.recording):
            # Gamer mode is busy, so the X only tucks LightRec away; Quit lives in the tray menu.
            self._go_background()
            return
        if self._game_recording():
            if self.game.recording.stopping:
                self._quit_after_finish = True
                return
            if messagebox.askyesno("Recording in progress", "Stop and save the recording before quitting?",
                                   parent=self.root):
                self._quit_after_finish = True
                self.game.stop_recording()
            return
        if self.session and self.session.state in ("recording", "paused", "finalizing"):
            if self.session.state == "finalizing":
                self._quit_after_finish = True
                return
            if messagebox.askyesno("Recording in progress", "Stop and save the recording before quitting?",
                                   parent=self.root):
                self._quit_after_finish = True
                self.session.stop()
            return
        self._quit()

    def _quit(self) -> None:
        try:
            self.settings.window_geometry = self.root.geometry()
            self._sync_settings()
        except tk.TclError:
            pass
        self._stop_monitors()
        if self.game:
            game, self.game = self.game, None
            game.stop()
        if self.tray:
            tray, self.tray = self.tray, None
            tray.stop()
        if self.hotkeys:
            self.hotkeys.stop()
        if self.notes:
            self.notes.hide()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    App(start_hidden="--tray" in sys.argv[1:]).run()
