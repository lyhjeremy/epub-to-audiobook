"""
epub_to_audiobook_gui_v4.py — EPUB → MP3 Audiobook Converter (GUI, V4)
======================================================================
A modernised V3. Same engine and features, with a refreshed, less cluttered
interface and three read-along fixes:

  • IMMERSIVE READ-ALONG — the text now fills the page. The book picker moved to
    the Convert tab and the read-along settings collapsed into one slim toolbar,
    so the reader gets the large majority of the window.
  • REAL LINE SPACING — the spacing control now sets the gap BETWEEN wrapped
    lines (Text `spacing2`), not just above the first line.
  • MODERN LOOK — refreshed indigo palette, flat inputs/sliders/scrollbars,
    roomier spacing, native system fonts, and a Light/Dark toggle.

Carried over from V3: accurate edge-tts sentence timing (no late-track drift),
page-turn auto-scroll, reading-speed / font / size / spacing / highlight
controls, auto-advance, resume, sleep timer, and keyboard shortcuts
(Space = play/pause, ←/→ = sentence, ↑/↓ = track). Built on `audiobook_engine_v4`.

SETUP (one-time)
----------------
    pip install edge-tts ebooklib beautifulsoup4 pygame
    pip install mutagen            # optional: tags / .m4b cover
    # ffmpeg on PATH               # needed for the speed control and .m4b

Run it:  python epub_to_audiobook_gui_v4.py
"""

import os
import sys
import json
import time
import queue
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser, font as tkfont


# ── Friendly dependency check ─────────────────────────────────────────────────

def _check_deps():
    import importlib.util as ilu
    missing = [pkg for pkg, imp in [("edge-tts", "edge_tts"),
                                    ("ebooklib", "ebooklib"),
                                    ("beautifulsoup4", "bs4")]
               if ilu.find_spec(imp) is None]
    if missing:
        msg = ("Missing required package(s):\n\n    " + ", ".join(missing) +
               "\n\nInstall them once by running:\n\n"
               "    pip install " + " ".join(missing))
        try:
            root = tk.Tk(); root.withdraw()
            messagebox.showerror("Setup needed", msg)
            root.destroy()
        except Exception:
            print("\n[X] " + msg + "\n")
        sys.exit(1)

_check_deps()

import audiobook_engine_v4 as eng

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    import pygame
    pygame.mixer.init()
    HAVE_PYGAME = True
except Exception:
    HAVE_PYGAME = False


# ── Native-feeling UI fonts (so it doesn't look like 2005 Segoe-on-everything) ─

if sys.platform == "darwin":
    UI_FAMILY, MONO_FAMILY = "Helvetica Neue", "Menlo"
elif sys.platform.startswith("win"):
    UI_FAMILY, MONO_FAMILY = "Segoe UI", "Consolas"
else:
    UI_FAMILY, MONO_FAMILY = "DejaVu Sans", "DejaVu Sans Mono"


# ──────────────────────────────────────────────────────────────────────────────
#  Themes + persistence
# ──────────────────────────────────────────────────────────────────────────────

THEMES = {
    "light": {
        "BG": "#f7f8fa", "CARD": "#ffffff", "TEXT": "#14161c", "MUTED": "#71757f",
        "ACCENT": "#4f46e5", "ACCENT2": "#4338ca", "BORDER": "#e7e9ee",
        "OK": "#0f9d58", "ERR": "#d23b3b", "FIELD": "#f1f2f5",
        "BTN": "#eef0f3", "BTN_HOVER": "#e2e5ea",
        "HL_BG": "#fff1bd", "HL_FG": "#1a1d24",
    },
    "dark": {
        "BG": "#0f1117", "CARD": "#171a21", "TEXT": "#e8eaf0", "MUTED": "#8b909c",
        "ACCENT": "#7c83ff", "ACCENT2": "#6b72f0", "BORDER": "#242833",
        "OK": "#3ecf8e", "ERR": "#ef6b6b", "FIELD": "#1d212b",
        "BTN": "#232834", "BTN_HOVER": "#2c3340",
        "HL_BG": "#574a1f", "HL_FG": "#ffe9a8",
    },
}

HL_PRESETS = {
    "light": {"Amber": "#fff3c4", "Yellow": "#ffe98a", "Green": "#c8f1d4",
              "Blue": "#cfe2ff", "Pink": "#ffd6e7"},
    "dark":  {"Amber": "#5a4d20", "Yellow": "#6b5a17", "Green": "#22503a",
              "Blue": "#243b63", "Pink": "#5a2740"},
}

FONT_CHOICES = ["Georgia", "Palatino", "Iowan Old Style", "Charter",
                "Times New Roman", "Helvetica", "Arial", "Verdana",
                "Avenir Next", "Menlo", "Courier New"]

SPEED_CHOICES = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]

SLEEP_CHOICES = ["Off", "5 min", "10 min", "15 min", "30 min", "45 min",
                 "60 min", "End of track"]

SETTINGS_FILE = Path.home() / ".epub_audiobook_v3.json"

DEFAULT_SETTINGS = {
    "theme": "light",
    "voice": eng.DEFAULT_VOICE,
    "font_family": "Georgia",
    "font_size": 14,
    "line_spacing": 6,
    "speed": 1.0,
    "auto_advance": True,
    "highlight": "",          # "" => theme default preset
    "last_epub": "",
    "last_out": "",
    "positions": {},          # abspath -> ms
}


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    s["positions"] = dict(s.get("positions") or {})
    return s


def _natkey(p: Path):
    import re
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", p.name)]


def _fmt_time(ms):
    s = max(0, int(ms // 1000))
    return f"{s // 60}:{s % 60:02d}"


# ──────────────────────────────────────────────────────────────────────────────
#  App
# ──────────────────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.settings = load_settings()
        self.theme = self.settings.get("theme", "light")
        self.pal = dict(THEMES[self.theme])

        self.book = None
        self.book_title = ""
        self.author = "Unknown"
        self.chapters = []
        self.chapter_vars = []
        self.running = False
        self.total_parts = 0
        self.done_parts = 0
        self._themed = []                     # [(widget, {opt: palkey})]

        # ── player state (all positions tracked in ORIGINAL-audio ms) ──
        self.tracks = []
        self.cur_track = None
        self.cur_units = []                   # [{start_ms,end_ms,text}]
        self.cur_ranges = []                  # [(text_idx_a, text_idx_b)]
        self.duration_ms = 0
        self.playing = False
        self.paused = False
        self.anchor_ms = 0                    # original-ms at last play/seek
        self.t0 = 0.0
        self.speed = float(self.settings.get("speed", 1.0))
        self.play_file = None                 # possibly speed-rendered file
        self.user_seeking = False
        self.hl_index = -1
        self._play_token = 0                  # guards stale async renders
        self._started_at = 0.0
        self._last_pos_save = 0.0
        self.sleep_deadline = None            # monotonic time, or "track"

        root.title("EPUB → Audiobook  ·  V4")
        root.geometry("980x1040")
        root.minsize(760, 760)

        self._init_style()
        self._build_ui()
        self._apply_palette()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll)

        # Restore last session
        if self.settings.get("last_out"):
            self.out_var.set(self.settings["last_out"])
        if self.settings.get("last_epub") and Path(self.settings["last_epub"]).exists():
            self.epub_var.set(self.settings["last_epub"])
            self.root.after(300, self.reload_chapters)
        else:
            self._refresh_tracks()

    # ── ttk styling for the current palette ──────────────────────────────────
    def _init_style(self):
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

    def _apply_ttk_styles(self):
        p, st = self.pal, self.style
        base = (UI_FAMILY, 12)
        st.configure(".", background=p["BG"], foreground=p["TEXT"], font=base)
        st.configure("TFrame", background=p["BG"])
        st.configure("Card.TFrame", background=p["CARD"])
        st.configure("TLabel", background=p["BG"], foreground=p["TEXT"])
        st.configure("Card.TLabel", background=p["CARD"], foreground=p["TEXT"])
        st.configure("Muted.TLabel", background=p["BG"], foreground=p["MUTED"])
        st.configure("CardMuted.TLabel", background=p["CARD"], foreground=p["MUTED"])
        st.configure("Header.TLabel", background=p["BG"], foreground=p["TEXT"],
                     font=(UI_FAMILY, 21, "bold"))
        st.configure("Sub.TLabel", background=p["BG"], foreground=p["MUTED"],
                     font=(UI_FAMILY, 11))
        st.configure("Section.TLabelframe", background=p["CARD"],
                     bordercolor=p["BORDER"], relief="solid", borderwidth=1)
        st.configure("Section.TLabelframe.Label", background=p["CARD"],
                     foreground=p["MUTED"], font=(UI_FAMILY, 9, "bold"))
        st.configure("TCheckbutton", background=p["CARD"], foreground=p["TEXT"],
                     font=(UI_FAMILY, 11), focuscolor=p["CARD"])
        st.map("TCheckbutton", background=[("active", p["CARD"])],
               foreground=[("disabled", p["MUTED"])])
        st.configure("Bg.TCheckbutton", background=p["BG"], foreground=p["TEXT"],
                     font=(UI_FAMILY, 11), focuscolor=p["BG"])
        st.map("Bg.TCheckbutton", background=[("active", p["BG"])])

        # Buttons: flat, filled, roomy hit-areas (no 3D bevel / focus dotting).
        st.configure("TButton", font=(UI_FAMILY, 11), padding=(16, 10),
                     relief="flat", borderwidth=0, background=p["BTN"],
                     foreground=p["TEXT"], focuscolor=p["BTN"])
        st.map("TButton",
               background=[("pressed", p["BTN_HOVER"]), ("active", p["BTN_HOVER"]),
                           ("disabled", p["CARD"])],
               foreground=[("disabled", p["MUTED"])])
        st.configure("Accent.TButton", font=(UI_FAMILY, 12, "bold"),
                     foreground="#ffffff", background=p["ACCENT"],
                     bordercolor=p["ACCENT"], relief="flat", borderwidth=0,
                     padding=(24, 13), focuscolor=p["ACCENT"])
        st.map("Accent.TButton",
               background=[("pressed", p["ACCENT2"]), ("active", p["ACCENT2"]),
                           ("disabled", p["BORDER"])],
               foreground=[("disabled", p["MUTED"])])
        # Larger transport controls for the read-along bar.
        st.configure("Transport.TButton", font=(UI_FAMILY, 13), padding=(18, 13),
                     relief="flat", borderwidth=0, background=p["BTN"],
                     foreground=p["TEXT"], focuscolor=p["BTN"])
        st.map("Transport.TButton",
               background=[("pressed", p["BTN_HOVER"]), ("active", p["BTN_HOVER"]),
                           ("disabled", p["CARD"])],
               foreground=[("disabled", p["MUTED"])])
        st.configure("Play.TButton", font=(UI_FAMILY, 13, "bold"),
                     foreground="#ffffff", background=p["ACCENT"], relief="flat",
                     borderwidth=0, padding=(26, 13), focuscolor=p["ACCENT"])
        st.map("Play.TButton",
               background=[("pressed", p["ACCENT2"]), ("active", p["ACCENT2"]),
                           ("disabled", p["BORDER"])],
               foreground=[("disabled", p["MUTED"])])

        st.configure("Horizontal.TProgressbar", troughcolor=p["BORDER"],
                     background=p["ACCENT"], bordercolor=p["BORDER"], thickness=8)
        # Inputs: flat chips (kill clam's 3D bevel via light/dark/border = field).
        st.configure("TCombobox", padding=7, arrowsize=14, arrowcolor=p["MUTED"],
                     bordercolor=p["BORDER"], lightcolor=p["FIELD"], darkcolor=p["FIELD"],
                     fieldbackground=p["FIELD"], background=p["FIELD"], foreground=p["TEXT"])
        st.map("TCombobox",
               fieldbackground=[("readonly", p["FIELD"]), ("!readonly", p["FIELD"])],
               foreground=[("readonly", p["TEXT"])],
               bordercolor=[("focus", p["ACCENT"])],
               selectbackground=[("readonly", p["FIELD"])],
               selectforeground=[("readonly", p["TEXT"])])
        st.configure("TSpinbox", padding=7, arrowsize=14, fieldbackground=p["FIELD"],
                     foreground=p["TEXT"], background=p["FIELD"], arrowcolor=p["MUTED"],
                     bordercolor=p["BORDER"], lightcolor=p["FIELD"], darkcolor=p["FIELD"])
        st.map("TSpinbox", bordercolor=[("focus", p["ACCENT"])])
        st.configure("TEntry", padding=7, fieldbackground=p["FIELD"], foreground=p["TEXT"],
                     insertcolor=p["TEXT"], bordercolor=p["BORDER"],
                     lightcolor=p["FIELD"], darkcolor=p["FIELD"])
        st.map("TEntry", bordercolor=[("focus", p["ACCENT"])])
        st.configure("TNotebook", background=p["BG"], borderwidth=0)
        st.configure("TNotebook.Tab", padding=(20, 11), font=(UI_FAMILY, 12),
                     background=p["BG"], foreground=p["MUTED"], borderwidth=0)
        st.map("TNotebook.Tab",
               background=[("selected", p["CARD"])],
               foreground=[("selected", p["TEXT"]), ("active", p["TEXT"])])
        # Sliders + scrollbars: flat, blended, accent thumb.
        st.configure("TScale", background=p["BG"], troughcolor=p["BORDER"],
                     bordercolor=p["BG"])
        st.configure("Card.Horizontal.TScale", background=p["CARD"],
                     troughcolor=p["BORDER"], bordercolor=p["CARD"])
        st.configure("Vertical.TScrollbar", troughcolor=p["CARD"], background=p["BORDER"],
                     bordercolor=p["CARD"], arrowcolor=p["MUTED"], relief="flat")
        st.map("Vertical.TScrollbar", background=[("active", p["MUTED"])])
        st.configure("Horizontal.TScrollbar", troughcolor=p["CARD"], background=p["BORDER"],
                     bordercolor=p["CARD"], arrowcolor=p["MUTED"], relief="flat")
        # combobox dropdown list colours
        self.root.option_add("*TCombobox*Listbox.background", p["FIELD"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["TEXT"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["ACCENT"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # ── theme registry helpers (for plain tk widgets) ────────────────────────
    def _reg(self, w, **roles):
        self._themed.append((w, roles))
        self._apply_widget(w, roles)
        return w

    def _apply_widget(self, w, roles):
        for opt, key in roles.items():
            try:
                w.configure(**{opt: self.pal[key]})
            except tk.TclError:
                pass

    def _apply_palette(self):
        self.pal = dict(THEMES[self.theme])
        self.root.configure(bg=self.pal["BG"])
        self._apply_ttk_styles()
        for w, roles in list(self._themed):
            if w.winfo_exists():
                self._apply_widget(w, roles)
        if self.chapters:
            self._repopulate_keeping_state()
        self._restyle_reader()

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self.theme_btn.config(text="☾ Dark" if self.theme == "light" else "☀ Light")
        self._apply_palette()
        self._save_settings()

    # ── layout ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._apply_ttk_styles()
        outer = ttk.Frame(self.root, padding=(22, 14, 22, 10))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)        # notebook expands

        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        title_box = ttk.Frame(head)
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text="EPUB → Audiobook", style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="Convert any .epub to MP3 chapters — then read "
                  "along, in sync, as it plays.", style="Sub.TLabel").pack(anchor="w", pady=(3, 0))
        self.theme_btn = ttk.Button(
            head, text="☾  Dark" if self.theme == "light" else "☀  Light",
            command=self.toggle_theme)
        self.theme_btn.grid(row=0, column=1, sticky="ne")

        # The Book picker now lives inside the Convert tab so the Read-along tab
        # can give almost all of its height to the text.
        self.epub_var = tk.StringVar()
        self.info_var = tk.StringVar(value="No book loaded yet.")

        nb = ttk.Notebook(outer)
        nb.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self.tab_convert = ttk.Frame(nb, padding=(2, 12, 2, 2))
        self.tab_read = ttk.Frame(nb, padding=(2, 12, 2, 2))
        nb.add(self.tab_convert, text="   Convert   ")
        nb.add(self.tab_read, text="   Read-along   ")
        self.notebook = nb

        self._build_convert_tab(self.tab_convert)
        self._build_read_tab(self.tab_read)

    # ── Convert tab ──────────────────────────────────────────────────────────
    def _build_convert_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        # Book picker
        src = ttk.Labelframe(parent, text="BOOK", style="Section.TLabelframe", padding=14)
        src.grid(row=0, column=0, sticky="ew")
        src.columnconfigure(1, weight=1)
        ttk.Label(src, text="EPUB file", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.epub_entry = ttk.Entry(src, textvariable=self.epub_var)
        self.epub_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(src, text="Browse…", command=self.choose_epub).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(src, textvariable=self.info_var, style="CardMuted.TLabel",
                  wraplength=820, justify="left").grid(row=1, column=0, columnspan=3,
                                                       sticky="w", pady=(10, 0))

        opts = ttk.Labelframe(parent, text="OPTIONS", style="Section.TLabelframe", padding=14)
        opts.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        for c in (1, 3):
            opts.columnconfigure(c, weight=1)

        ttk.Label(opts, text="Voice", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        voice_wrap = ttk.Frame(opts, style="Card.TFrame")
        voice_wrap.grid(row=0, column=1, sticky="ew", pady=4)
        voice_wrap.columnconfigure(0, weight=1)
        self.voice_var = tk.StringVar(value=self.settings.get("voice", eng.DEFAULT_VOICE))
        self.voice_box = ttk.Combobox(voice_wrap, textvariable=self.voice_var,
                                      values=eng.POPULAR_VOICES, width=26)
        self.voice_box.grid(row=0, column=0, sticky="ew")
        self.preview_btn = ttk.Button(voice_wrap, text="Preview", command=self.preview_voice, width=8)
        self.preview_btn.grid(row=0, column=1, padx=(6, 0))


        ttk.Label(opts, text="Voice", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        voice_wrap = ttk.Frame(opts, style="Card.TFrame")
        voice_wrap.grid(row=0, column=1, sticky="ew", pady=4)
        voice_wrap.columnconfigure(0, weight=1)
        self.voice_var = tk.StringVar(value=self.settings.get("voice", eng.DEFAULT_VOICE))
        self.voice_box = ttk.Combobox(voice_wrap, textvariable=self.voice_var,
                                      values=eng.POPULAR_VOICES, width=26)
        self.voice_box.grid(row=0, column=0, sticky="ew")
        self.preview_btn = ttk.Button(voice_wrap, text="Preview", command=self.preview_voice, width=8)
        self.preview_btn.grid(row=0, column=1, padx=(6, 0))

        ttk.Label(opts, text="Output folder", style="Card.TLabel").grid(row=0, column=2, sticky="w", pady=4, padx=(16, 8))
        self.out_var = tk.StringVar()
        out_wrap = ttk.Frame(opts, style="Card.TFrame")
        out_wrap.grid(row=0, column=3, sticky="ew", pady=4)
        out_wrap.columnconfigure(0, weight=1)
        ttk.Entry(out_wrap, textvariable=self.out_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(out_wrap, text="…", width=3, command=self.choose_outdir).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(opts, text="Speed", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4, padx=(0, 8))
        speed_wrap = ttk.Frame(opts, style="Card.TFrame")
        speed_wrap.grid(row=1, column=1, sticky="ew", pady=4)
        speed_wrap.columnconfigure(0, weight=1)
        self.rate_val = tk.IntVar(value=0)
        self.cspeed = ttk.Scale(speed_wrap, from_=-50, to=50, orient="horizontal",
                                style="Card.Horizontal.TScale", command=self._on_speed)
        self.cspeed.grid(row=0, column=0, sticky="ew")
        self.speed_lbl = ttk.Label(speed_wrap, text="+0%", style="CardMuted.TLabel", width=6)
        self.speed_lbl.grid(row=0, column=1, padx=(8, 0))
        self.cspeed.set(0)

        ttk.Label(opts, text="Part length (min)", style="Card.TLabel").grid(row=1, column=2, sticky="w", pady=4, padx=(16, 8))
        self.part_var = tk.IntVar(value=12)
        self.part_spin = ttk.Spinbox(opts, from_=1, to=60, textvariable=self.part_var,
                                     width=8, command=self._refresh_chapter_parts)
        self.part_spin.grid(row=1, column=3, sticky="w", pady=4)
        self.part_var.trace_add("write", lambda *_: self._refresh_chapter_parts())

        ttk.Label(opts, text="Parallel workers", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4, padx=(0, 8))
        self.workers_var = tk.IntVar(value=4)
        ttk.Spinbox(opts, from_=1, to=8, textvariable=self.workers_var, width=8).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(opts, text="Min chars / chapter", style="Card.TLabel").grid(row=2, column=2, sticky="w", pady=4, padx=(16, 8))
        self.minchars_var = tk.IntVar(value=300)
        ttk.Spinbox(opts, from_=50, to=5000, increment=50, textvariable=self.minchars_var, width=8).grid(row=2, column=3, sticky="w", pady=4)

        self.subs_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Generate read-along subtitles (sentence timing)",
                        variable=self.subs_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.announce_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Announce chapter titles at the start of each file",
                        variable=self.announce_var).grid(row=3, column=2, columnspan=2, sticky="w", pady=(8, 0))
        self.pad_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Zero-pad chapter numbers (Ch.01 vs Ch.1)",
                        variable=self.pad_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.tag_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Write tags + cover" + ("" if eng.HAVE_MUTAGEN else "  (needs mutagen)"),
                        variable=self.tag_var, state="normal" if eng.HAVE_MUTAGEN else "disabled").grid(row=4, column=2, sticky="w", pady=(2, 0))
        self.m4b_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Combine into one .m4b" + ("" if eng.have_ffmpeg() else "  (needs ffmpeg)"),
                        variable=self.m4b_var, state="normal" if eng.have_ffmpeg() else "disabled").grid(row=4, column=3, sticky="w", pady=(2, 0))

        ttk.Button(opts, text="Reload chapters with these settings",
                   command=self.reload_chapters).grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))

        # Chapters
        chap = ttk.Labelframe(parent, text="CHAPTERS", style="Section.TLabelframe", padding=12)
        chap.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        chap.columnconfigure(0, weight=1)
        chap.rowconfigure(1, weight=1)
        bar = ttk.Frame(chap, style="Card.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(3, weight=1)
        ttk.Button(bar, text="Select all", width=10, command=lambda: self._set_all(True)).grid(row=0, column=0)
        ttk.Button(bar, text="Clear all", width=10, command=lambda: self._set_all(False)).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(bar, text="Unflag junk", width=12, command=self._untick_junk).grid(row=0, column=2, padx=(6, 0))
        self.sel_info = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.sel_info, style="CardMuted.TLabel").grid(row=0, column=3, sticky="e")

        list_wrap = self._reg(tk.Frame(chap, highlightthickness=1), bg="CARD", highlightbackground="BORDER")
        list_wrap.grid(row=1, column=0, sticky="nsew")
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)
        self.canvas = self._reg(tk.Canvas(list_wrap, highlightthickness=0, height=140), bg="CARD")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(list_wrap, orient="vertical", command=self.canvas.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=vsb.set)
        self.list_frame = self._reg(tk.Frame(self.canvas), bg="CARD")
        self.canvas_win = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self.canvas_win, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self._reg(tk.Label(self.list_frame, text="Load an .epub to see its chapters.",
                           font=(UI_FAMILY, 11), pady=18), bg="CARD", fg="MUTED").pack()

        prog = ttk.Frame(parent)
        prog.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        prog.columnconfigure(0, weight=1)
        self.pbar = ttk.Progressbar(prog, style="Horizontal.TProgressbar", mode="determinate")
        self.pbar.grid(row=0, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(prog, textvariable=self.status_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

        log_wrap = self._reg(tk.Frame(parent, highlightthickness=1), bg="CARD", highlightbackground="BORDER")
        log_wrap.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        log_wrap.columnconfigure(0, weight=1)
        self.log = self._reg(tk.Text(log_wrap, height=5, wrap="word", relief="flat",
                                     font=(MONO_FAMILY, 10), padx=8, pady=6, state="disabled"),
                             bg="CARD", fg="TEXT", insertbackground="TEXT")
        self.log.grid(row=0, column=0, sticky="ew")
        logsb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log.yview)
        logsb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=logsb.set)
        self.log.tag_configure("ok", foreground=self.pal["OK"])
        self.log.tag_configure("err", foreground=self.pal["ERR"])
        self.log.tag_configure("muted", foreground=self.pal["MUTED"])

        actions = ttk.Frame(parent)
        actions.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        actions.columnconfigure(0, weight=1)
        self.open_btn = ttk.Button(actions, text="Open output folder", command=self.open_output, state="disabled")
        self.open_btn.grid(row=0, column=0, sticky="w")
        self.stop_btn = ttk.Button(actions, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=(0, 8))
        self.convert_btn = ttk.Button(actions, text="Convert", style="Accent.TButton",
                                      command=self.start_convert, state="disabled")
        self.convert_btn.grid(row=0, column=2)

    # ── Read-along tab ───────────────────────────────────────────────────────
    def _build_read_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)            # the reader gets (almost) all the height

        # ── track selector ──
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Track", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 10))
        self.track_var = tk.StringVar()
        self.track_box = ttk.Combobox(top, textvariable=self.track_var, state="readonly", values=[])
        self.track_box.grid(row=0, column=1, sticky="ew")
        self.track_box.bind("<<ComboboxSelected>>", self._on_track_selected)
        ttk.Button(top, text="📁  Open folder…", command=self.choose_read_folder).grid(row=0, column=2, padx=(10, 0))
        ttk.Button(top, text="Refresh", command=self._refresh_tracks).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(top, text="Open file", command=self._open_current_track).grid(row=0, column=4, padx=(8, 0))

        # ── one-line controls toolbar (keeps the page mostly text) ──
        bar = ttk.Frame(parent)
        bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        def cell(label, w):
            ttk.Label(bar, text=label, style="Muted.TLabel").pack(side="left", padx=(0, 7))
            w.pack(side="left", padx=(0, 18))
            return w

        self.speed_var = tk.StringVar(value=f"{self.speed:g}×")
        self.rspeed_box = ttk.Combobox(bar, textvariable=self.speed_var, width=6, state="readonly",
                                       values=[f"{s:g}×" for s in SPEED_CHOICES])
        self.rspeed_box.bind("<<ComboboxSelected>>", self._on_read_speed)
        cell("Speed", self.rspeed_box)

        self.font_var = tk.StringVar(value=self.settings.get("font_family", "Georgia"))
        self.font_box = ttk.Combobox(bar, textvariable=self.font_var, width=15, state="readonly",
                                     values=FONT_CHOICES)
        self.font_box.bind("<<ComboboxSelected>>", lambda e: self._restyle_reader(save=True))
        cell("Font", self.font_box)

        self.size_var = tk.IntVar(value=int(self.settings.get("font_size", 14)))
        sz = ttk.Spinbox(bar, from_=10, to=40, width=4, textvariable=self.size_var,
                         command=lambda: self._restyle_reader(save=True))
        self.size_var.trace_add("write", lambda *_: self._restyle_reader(save=True))
        cell("Size", sz)

        self.ls_var = tk.IntVar(value=int(self.settings.get("line_spacing", 6)))
        lsb = ttk.Spinbox(bar, from_=0, to=50, width=4, textvariable=self.ls_var,
                          command=lambda: self._restyle_reader(save=True))
        self.ls_var.trace_add("write", lambda *_: self._restyle_reader(save=True))
        cell("Spacing", lsb)

        self.hl_var = tk.StringVar(value=self.settings.get("highlight") or "Amber")
        self.hl_box = ttk.Combobox(bar, textvariable=self.hl_var, width=9, state="readonly",
                                   values=list(HL_PRESETS[self.theme].keys()) + ["Custom…"])
        self.hl_box.bind("<<ComboboxSelected>>", self._on_highlight_pick)
        cell("Highlight", self.hl_box)

        self.adv_var = tk.BooleanVar(value=bool(self.settings.get("auto_advance", True)))
        ttk.Checkbutton(bar, text="Auto-advance", variable=self.adv_var,
                        style="Bg.TCheckbutton", command=self._save_settings).pack(side="left", padx=(0, 18))

        self.sleep_var = tk.StringVar(value="Off")
        self.sleep_box = ttk.Combobox(bar, textvariable=self.sleep_var, width=11, state="readonly",
                                      values=SLEEP_CHOICES)
        self.sleep_box.bind("<<ComboboxSelected>>", self._on_sleep_pick)
        cell("Sleep", self.sleep_box)

        hint = ("Space play/pause · ←/→ sentence · ↑/↓ track" if HAVE_PYGAME
                else "In-app playback needs pygame  (pip install pygame)")
        self.hint_lbl = ttk.Label(bar, style="Muted.TLabel", text=hint)
        self.hint_lbl.pack(side="right")

        # ── reader (the star of the show — fills the rest of the tab) ──
        text_wrap = self._reg(tk.Frame(parent, highlightthickness=1), bg="CARD", highlightbackground="BORDER")
        text_wrap.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        text_wrap.columnconfigure(0, weight=1)
        text_wrap.rowconfigure(0, weight=1)
        self.reader = self._reg(
            tk.Text(text_wrap, wrap="word", relief="flat", borderwidth=0,
                    padx=30, pady=24, cursor="arrow", state="disabled"),
            bg="CARD", fg="TEXT", insertbackground="TEXT")
        self.reader.grid(row=0, column=0, sticky="nsew")
        rsb = ttk.Scrollbar(text_wrap, orient="vertical", command=self.reader.yview)
        rsb.grid(row=0, column=1, sticky="ns")
        self.reader.configure(yscrollcommand=rsb.set)
        self._reader_placeholder("Convert a book (or pick a track) to read along here.")

        # ── transport ──
        trans = ttk.Frame(parent)
        trans.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        trans.columnconfigure(6, weight=1)
        self.play_btn = ttk.Button(trans, text="▶  Play", width=9, style="Play.TButton",
                                   command=self.toggle_play, state="disabled")
        self.play_btn.grid(row=0, column=0)
        self.stopp_btn = ttk.Button(trans, text="■", width=3, style="Transport.TButton",
                                    command=self.stop_play, state="disabled")
        self.stopp_btn.grid(row=0, column=1, padx=(8, 0))
        self.prev_btn = ttk.Button(trans, text="⏮", width=3, style="Transport.TButton",
                                   command=lambda: self._step_sentence(-1), state="disabled")
        self.prev_btn.grid(row=0, column=2, padx=(8, 0))
        self.next_btn = ttk.Button(trans, text="⏭", width=3, style="Transport.TButton",
                                   command=lambda: self._step_sentence(1), state="disabled")
        self.next_btn.grid(row=0, column=3, padx=(8, 0))
        self.time_lbl = ttk.Label(trans, text="0:00 / 0:00", style="Muted.TLabel", width=14)
        self.time_lbl.grid(row=0, column=4, padx=(14, 8))
        self.seek = ttk.Scale(trans, from_=0, to=1000, orient="horizontal")
        self.seek.grid(row=0, column=6, sticky="ew")
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "user_seeking", True))
        self.seek.bind("<ButtonRelease-1>", self._on_seek_commit)
        if not HAVE_PYGAME:
            for w in (self.play_btn, self.stopp_btn, self.prev_btn, self.next_btn):
                w.config(state="disabled")

    # ── reader styling ───────────────────────────────────────────────────────
    def _reader_font(self):
        return (self.font_var.get(), max(10, int(self.size_var.get())))

    def _highlight_color(self):
        name = self.hl_var.get()
        if name.startswith("#"):
            return name
        return HL_PRESETS[self.theme].get(name, self.pal["HL_BG"])

    def _restyle_reader(self, save=False):
        try:
            ls = max(0, int(self.ls_var.get()))
        except (tk.TclError, ValueError):
            ls = 6
        # Sentences are inserted as one flowing paragraph, so the gap the user
        # actually sees BETWEEN lines is `spacing2` (space between wrapped display
        # lines). spacing1/spacing3 only pad a paragraph's first/last line — which
        # is why earlier the control "only worked before the first line".
        self.reader.configure(bg=self.pal["CARD"], fg=self.pal["TEXT"],
                              font=self._reader_font(),
                              spacing1=ls, spacing2=ls, spacing3=ls)
        self.reader.tag_configure("hl", background=self._highlight_color(), foreground=self.pal["HL_FG"])
        self.reader.tag_configure("ph", foreground=self.pal["MUTED"], font=(UI_FAMILY, 12))
        if save:
            self._save_settings()

    def _reader_placeholder(self, text):
        self.reader.configure(state="normal")
        self.reader.delete("1.0", "end")
        self.reader.insert("1.0", text)
        self.reader.tag_add("ph", "1.0", "end")
        self.reader.configure(state="disabled")
        self._restyle_reader()

    # ── small helpers ────────────────────────────────────────────────────────
    def _on_speed(self, _val):
        v = int(round(float(self.cspeed.get()) / 5.0) * 5)
        self.rate_val.set(v)
        if hasattr(self, "speed_lbl"):
            self.speed_lbl.config(text=f"{v:+d}%")

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _part_chars(self):
        try:
            minutes = max(1, int(self.part_var.get()))
        except (tk.TclError, ValueError):
            minutes = 12
        return max(1000, minutes * eng.CHARS_PER_MIN)

    def log_line(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", (tag,) if tag else ())
        self.log.see("end")
        self.log.configure(state="disabled")

    # ── loading the book ─────────────────────────────────────────────────────
    def choose_epub(self):
        path = filedialog.askopenfilename(title="Choose an EPUB file",
                                          filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")])
        if path:
            self.epub_var.set(path)
            self.reload_chapters()

    def choose_outdir(self):
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.out_var.set(d)
            self._refresh_tracks()
            self._save_settings()

    def choose_read_folder(self):
        """Read-along: pick any previously-generated audiobook folder to play."""
        start = self.out_var.get().strip() or str(Path.home())
        d = filedialog.askdirectory(title="Open an audiobook folder", initialdir=start)
        if not d:
            return
        self.stop_play()
        self.out_var.set(d)
        self.track_var.set("")             # let _refresh_tracks select the first track
        self._refresh_tracks()
        self._save_settings()
        if not self.tracks:
            messagebox.showinfo("No tracks here",
                                "That folder has no .mp3 files.\n\n"
                                "Pick the folder that contains an audiobook's "
                                "Ch.* .mp3 files.")

    def reload_chapters(self):
        path = self.epub_var.get().strip()
        if not path:
            messagebox.showinfo("Choose a book", "Pick an .epub file first.")
            return
        if not Path(path).exists():
            messagebox.showerror("Not found", f"File not found:\n{path}")
            return
        self.status_var.set("Loading book…")
        self.convert_btn.config(state="disabled")
        threading.Thread(target=self._load_worker, args=(path,), daemon=True).start()

    def _load_worker(self, path):
        try:
            book, title, author = eng.load_book(path)
            chapters = eng.gather_chapters(book, min_chars=int(self.minchars_var.get()))
            self.events.put(("loaded", (book, chapters, title, author, path)))
        except Exception as exc:
            self.events.put(("load_error", str(exc)))

    def _on_loaded(self, payload):
        book, chapters, title, author, path = payload
        self.book, self.book_title, self.author, self.chapters = book, title, author, chapters
        if not self.out_var.get().strip():
            self.out_var.set(str(Path(path).parent / eng.safe_filename(title)))
        words, est = eng.estimate_words_minutes(chapters)
        njunk = sum(1 for c in chapters if c["junk"])
        self.info_var.set(f"📖  {title}\n👤  {author}\n"
                          f"{len(chapters)} chapters · ~{words:,} words · "
                          f"~{est // 60}h {est % 60}m of audio"
                          + (f" · {njunk} flagged as front/back matter" if njunk else ""))
        self._populate_chapters()
        self._refresh_tracks()
        self.status_var.set("Ready. Tick the chapters you want, then Convert.")
        self.convert_btn.config(state="normal" if chapters else "disabled")
        self.settings["last_epub"] = path
        self._save_settings()

    def _populate_chapters(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.chapter_vars = []
        part_chars = self._part_chars()
        if not self.chapters:
            self._reg(tk.Label(self.list_frame, text="No chapters found. Try lowering "
                      "“Min chars / chapter”.", font=(UI_FAMILY, 11), pady=18),
                      bg="CARD", fg="MUTED").pack()
            return
        for ch in self.chapters:
            var = tk.BooleanVar(value=not ch["junk"])
            self.chapter_vars.append(var)
            row = tk.Frame(self.list_frame, bg=self.pal["CARD"])
            row.pack(fill="x", padx=6, pady=1)
            wc = len(ch["text"].split())
            n = len(eng.split_text(ch["text"], part_chars))
            tag = f"{n} parts" if n > 1 else "1 file"
            flag = "  ⚑" if ch["junk"] else ""
            label = f"{ch['index']:>2}.  {eng.safe_filename(ch['title'])[:60]}{flag}"
            cb = tk.Checkbutton(row, text=label, variable=var, bg=self.pal["CARD"],
                                fg=(self.pal["MUTED"] if ch["junk"] else self.pal["TEXT"]),
                                activebackground=self.pal["CARD"], selectcolor=self.pal["CARD"],
                                anchor="w", font=(UI_FAMILY, 11), command=self._update_sel_info)
            cb.pack(side="left", fill="x", expand=True)
            tk.Label(row, text=f"~{wc:,} w · {tag}", bg=self.pal["CARD"], fg=self.pal["MUTED"],
                     font=(UI_FAMILY, 10)).pack(side="right")
        self._update_sel_info()

    def _repopulate_keeping_state(self):
        states = [v.get() for v in self.chapter_vars] if self.chapter_vars else None
        self._populate_chapters()
        if states and len(states) == len(self.chapter_vars):
            for v, s in zip(self.chapter_vars, states):
                v.set(s)
        self._update_sel_info()

    def _refresh_chapter_parts(self):
        if self.chapters:
            self._repopulate_keeping_state()

    def _set_all(self, value):
        for v in self.chapter_vars:
            v.set(value)
        self._update_sel_info()

    def _untick_junk(self):
        for v, ch in zip(self.chapter_vars, self.chapters):
            if ch["junk"]:
                v.set(False)
        self._update_sel_info()

    def _selected_chapters(self):
        return [ch for ch, v in zip(self.chapters, self.chapter_vars) if v.get()]

    def _update_sel_info(self):
        sel = self._selected_chapters()
        words, est = eng.estimate_words_minutes(sel) if sel else (0, 0)
        self.sel_info.set(f"{len(sel)} of {len(self.chapters)} selected · ~{est // 60}h {est % 60}m")

    # ── voice preview ────────────────────────────────────────────────────────
    def preview_voice(self):
        if not HAVE_PYGAME:
            messagebox.showinfo("Preview needs pygame", "Install pygame to hear voice previews:\n\n    pip install pygame")
            return
        voice = self.voice_var.get().strip() or eng.DEFAULT_VOICE
        rate = eng.normalize_rate(self.rate_val.get())
        self.preview_btn.config(state="disabled")
        self.status_var.set(f"Synthesising preview ({voice})…")
        threading.Thread(target=self._preview_worker, args=(voice, rate), daemon=True).start()

    def _preview_worker(self, voice, rate):
        import tempfile
        sample = ("Hello. This is a short sample of how this voice will read your book. "
                  "You can change the speed with the slider.")
        tmp = Path(tempfile.gettempdir()) / "epub_voice_preview.mp3"
        try:
            eng.tts_to_file(sample, str(tmp), voice=voice, rate=rate)
            self.events.put(("preview_play", str(tmp)))
        except Exception as exc:
            self.events.put(("preview_err", str(exc)))

    # ── conversion ───────────────────────────────────────────────────────────
    def start_convert(self):
        if self.running:
            return
        selected = self._selected_chapters()
        if not selected:
            messagebox.showinfo("Nothing selected", "Tick at least one chapter.")
            return
        out_dir = Path(self.out_var.get().strip())
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Output folder", f"Could not create folder:\n{exc}")
            return

        voice = self.voice_var.get().strip() or eng.DEFAULT_VOICE
        rate = eng.normalize_rate(self.rate_val.get())
        part_chars = self._part_chars()
        fmt = "Ch.{idx:02d}" if self.pad_var.get() else "Ch.{idx}"
        announce, subs = self.announce_var.get(), self.subs_var.get()
        workers = max(1, int(self.workers_var.get()))

        self.total_parts = sum(len(eng.split_text(c["text"], part_chars)) for c in selected)
        self.done_parts = 0
        self.pbar.config(maximum=max(1, self.total_parts), value=0)
        self.cancel.clear()
        self.running = True
        self._set_running_ui(True)
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")
        self.log_line(f"Converting {len(selected)} chapter(s) → {out_dir}", "muted")
        self.status_var.set("Synthesising…")
        self.last_out_dir = out_dir
        self.settings["voice"] = voice
        self._save_settings()

        args = (selected, out_dir, fmt, part_chars, voice, rate, announce, subs,
                workers, self.tag_var.get(), self.m4b_var.get())
        threading.Thread(target=self._convert_worker, args=args, daemon=True).start()

    def _convert_worker(self, selected, out_dir, fmt, part_chars, voice, rate,
                        announce, subs, workers, do_tag, do_m4b):
        failed, produced = [], {}

        def on_part(mp3_path, cached):
            self.events.put(("part", (mp3_path.name, cached)))

        def do_chapter(ch):
            if self.cancel.is_set():
                return ch["index"], "skipped", []
            title = eng.safe_filename(ch["title"])
            try:
                paths = eng.synthesize_chapter(ch, out_dir, fmt, part_chars, voice, rate,
                                               announce, subs=subs, on_part=on_part,
                                               should_cancel=self.cancel.is_set)
                if self.cancel.is_set():
                    return ch["index"], "skipped", paths
                self.events.put(("log", (f"  ✓  [{ch['index']:>2}] {title[:48]}", "ok")))
                return ch["index"], None, paths
            except Exception as exc:
                self.events.put(("log", (f"  ✗  [{ch['index']:>2}] {title[:48]} — {exc}", "err")))
                return ch["index"], str(exc), []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(do_chapter, ch): ch for ch in selected}
            for fut in as_completed(futures):
                idx, err, paths = fut.result()
                if err and err != "skipped":
                    failed.append(idx)
                else:
                    produced[idx] = paths

        if not failed and not self.cancel.is_set() and (do_tag or do_m4b):
            try:
                self._post_process(selected, produced, out_dir, do_tag, do_m4b)
            except Exception as exc:
                self.events.put(("log", (f"  post-processing error: {exc}", "err")))
        self.events.put(("done", failed))

    def _post_process(self, selected, produced, out_dir, do_tag, do_m4b):
        ordered_paths, ordered_titles = [], []
        for ch in selected:
            for p in produced.get(ch["index"], []):
                ordered_paths.append(p); ordered_titles.append(eng.safe_filename(ch["title"]))
        if not ordered_paths:
            return
        cover = eng.extract_cover(self.book) if self.book else b""
        if do_tag and eng.HAVE_MUTAGEN:
            for n, (p, ct) in enumerate(zip(ordered_paths, ordered_titles), 1):
                eng.write_tags(p, album=self.book_title, author=self.author, track=n, title=ct, cover_bytes=cover)
            self.events.put(("log", (f"  ✓  tagged {len(ordered_paths)} MP3(s)", "ok")))
        if do_m4b and eng.have_ffmpeg():
            out_m4b = out_dir / f"{eng.safe_filename(self.book_title)}.m4b"
            self.events.put(("log", (f"  building {out_m4b.name} …", "muted")))
            eng.combine_to_m4b(ordered_paths, out_m4b, title=self.book_title, author=self.author,
                               chapter_titles=ordered_titles, cover_bytes=cover)
            self.events.put(("log", (f"  ✓  wrote {out_m4b.name}", "ok")))

    def stop(self):
        if self.running:
            self.cancel.set()
            self.status_var.set("Stopping after current files…")
            self.stop_btn.config(state="disabled")

    def _set_running_ui(self, running):
        state = "disabled" if running else "normal"
        self.convert_btn.config(state=state)
        self.epub_entry.config(state=state)
        self.stop_btn.config(state="normal" if running else "disabled")

    # ─────────────────────────────────────────────────────────────────────────
    #  Read-along player
    # ─────────────────────────────────────────────────────────────────────────
    def _refresh_tracks(self):
        d = self.out_var.get().strip()
        self.tracks = []
        if d and Path(d).exists():
            self.tracks = sorted(Path(d).glob("*.mp3"), key=_natkey)
        names = [p.name for p in self.tracks]
        self.track_box.config(values=names)
        if names and self.track_var.get() not in names:
            self.track_var.set(names[0])
            self._load_track(self.tracks[0])
        elif not names:
            self.track_var.set("")

    def _on_track_selected(self, _evt=None):
        name = self.track_var.get()
        for p in self.tracks:
            if p.name == name:
                self._load_track(p)
                break

    def _track_index(self):
        for i, p in enumerate(self.tracks):
            if p == self.cur_track:
                return i
        return -1

    def _load_track(self, path: Path):
        self.stop_play(save_pos=True)
        self.cur_track = path
        self.play_file = None
        data = eng.load_subs(path)
        units = data.get("sentences", [])
        self.cur_units = units

        self.reader.configure(state="normal")
        self.reader.delete("1.0", "end")
        self.cur_ranges = []
        if units:
            for u in units:
                a = self.reader.index("end-1c")
                self.reader.insert("end", u["text"] + " ")
                b = self.reader.index("end-1c")
                self.cur_ranges.append((a, b))
            self.duration_ms = data.get("duration_ms") or max((u["end_ms"] for u in units), default=0)
        else:
            self.reader.insert("end", "No read-along timing for this track.\n\n"
                               "(It was made without subtitles.) You can still play it "
                               "in your media player.")
            self.duration_ms = self._probe_duration(path)
        self.reader.configure(state="disabled")
        self.hl_index = -1
        self._restyle_reader()

        for i, (a, b) in enumerate(self.cur_ranges):
            tagn = f"s{i}"
            self.reader.tag_add(tagn, a, b)
            self.reader.tag_bind(tagn, "<Button-1>", lambda e, idx=i: self._jump_to_unit(idx))

        # resume position
        saved = int(self.settings.get("positions", {}).get(str(path.resolve()), 0))
        if 3000 < saved < self.duration_ms - 3000:
            self.anchor_ms = saved
            self.status_var.set(f"Resume at {_fmt_time(saved)} — press Play.")
        else:
            self.anchor_ms = 0

        have = HAVE_PYGAME and self.cur_track is not None
        for w in (self.play_btn,):
            w.config(state="normal" if have else "disabled", text="▶ Play")
        for w in (self.prev_btn, self.next_btn):
            w.config(state="normal" if (have and units) else "disabled")
        self.stopp_btn.config(state="disabled")
        self.seek.set(1000 * self.anchor_ms / self.duration_ms if self.duration_ms else 0)
        self._update_time_label(self.anchor_ms)

    def _probe_duration(self, path):
        return eng._measure_duration_ms(Path(path))

    def _elapsed_ms(self):
        """Current position in ORIGINAL-audio ms (speed-independent)."""
        if self.playing and not self.paused:
            return self.anchor_ms + (time.monotonic() - self.t0) * 1000 * self.speed
        return self.anchor_ms

    # playback control ---------------------------------------------------------
    def toggle_play(self):
        if not HAVE_PYGAME or self.cur_track is None:
            return
        if not self.playing:
            self._request_play(self.anchor_ms)
        elif self.paused:
            pygame.mixer.music.unpause()
            self.t0 = time.monotonic()
            self.paused = False
            self.play_btn.config(text="❚❚ Pause")
        else:
            pygame.mixer.music.pause()
            self.anchor_ms = self._elapsed_ms()
            self.paused = True
            self.play_btn.config(text="▶ Play")
            self._save_position()

    def _request_play(self, from_ms):
        """Begin (or resume) playback at original-ms `from_ms`, honouring speed."""
        if not HAVE_PYGAME or self.cur_track is None:
            return
        from_ms = max(0, min(from_ms, max(0, self.duration_ms - 200)))
        self._play_token += 1
        token = self._play_token
        if abs(self.speed - 1.0) < 1e-3:
            self._do_play(self.cur_track, from_ms)
            return
        # need a speed-rendered file
        cached = eng._speed_cache_dir()
        self.status_var.set(f"Preparing {self.speed:g}× audio…")
        self.play_btn.config(state="disabled")
        threading.Thread(target=self._render_speed_worker,
                         args=(self.cur_track, self.speed, from_ms, token),
                         daemon=True).start()

    def _render_speed_worker(self, track, speed, from_ms, token):
        try:
            path = eng.render_at_speed(track, speed)
            self.events.put(("play_ready", (str(path), from_ms, token)))
        except Exception as exc:
            self.events.put(("play_err", str(exc)))

    def _do_play(self, file_path, from_ms):
        try:
            pygame.mixer.music.load(str(file_path))
            pygame.mixer.music.play(start=(from_ms / 1000.0) / self.speed)
        except Exception as exc:
            messagebox.showerror("Playback failed", str(exc))
            self.play_btn.config(state="normal")
            return
        self.play_file = Path(file_path)
        self.anchor_ms = from_ms
        self.t0 = time.monotonic()
        self.playing = True
        self.paused = False
        self._started_at = time.monotonic()
        self.play_btn.config(text="❚❚ Pause", state="normal")
        self.stopp_btn.config(state="normal")
        self.status_var.set(f"Playing {self.cur_track.name}  ({self.speed:g}×)")

    def stop_play(self, save_pos=False):
        if save_pos:
            self._save_position()
        if HAVE_PYGAME:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self.playing = self.paused = False
        self.anchor_ms = 0
        if hasattr(self, "play_btn"):
            self.play_btn.config(text="▶ Play")
            self.stopp_btn.config(state="disabled")
            self.seek.set(0)
            self._update_time_label(0)
        if hasattr(self, "reader"):
            self.reader.tag_remove("hl", "1.0", "end")
        self.hl_index = -1

    def _jump_to_unit(self, idx):
        if not (0 <= idx < len(self.cur_units)):
            return
        self._highlight_unit(idx)
        if HAVE_PYGAME and self.cur_track is not None:
            self._request_play(self.cur_units[idx]["start_ms"])
        else:
            self.anchor_ms = self.cur_units[idx]["start_ms"]

    def _step_sentence(self, delta):
        if not self.cur_units:
            return
        cur = self._unit_at(self._elapsed_ms())
        if cur is None:
            cur = self.hl_index if self.hl_index >= 0 else 0
        self._jump_to_unit(max(0, min(len(self.cur_units) - 1, cur + delta)))

    def _on_seek_commit(self, _evt):
        self.user_seeking = False
        if self.duration_ms <= 0:
            return
        target = int(float(self.seek.get()) / 1000.0 * self.duration_ms)
        if HAVE_PYGAME and self.cur_track is not None and self.playing:
            self._request_play(target)
        else:
            self.anchor_ms = target
        self._update_time_label(target)

    def _unit_at(self, ms):
        for i, u in enumerate(self.cur_units):
            if u["start_ms"] <= ms < u["end_ms"]:
                return i
        return None

    def _highlight_unit(self, idx):
        if idx is None or idx == self.hl_index or not (0 <= idx < len(self.cur_ranges)):
            return
        self.reader.tag_remove("hl", "1.0", "end")
        a, b = self.cur_ranges[idx]
        self.reader.tag_add("hl", a, b)
        self.hl_index = idx
        self._scroll_into_view(a)

    def _scroll_into_view(self, index):
        """
        Page-turn scrolling: leave the highlight alone until it reaches the
        bottom of the page (its line is at/near the second-to-last visible
        line), then jump it up so it becomes the SECOND line — revealing a fresh
        page of upcoming text. Also scrolls if the highlight is off-screen.
        """
        try:
            self.reader.update_idletasks()
            view_h = self.reader.winfo_height()
            if view_h <= 1:
                return
            dline = self.reader.dlineinfo(index)        # (x, y, w, h, baseline)
            line_h = dline[3] if dline else 0
            if not line_h:                              # estimate from the font
                f = tkfont.Font(font=self.reader.cget("font"))
                line_h = f.metrics("linespace") + max(0, int(self.ls_var.get()))
            total = self.reader.count("1.0", "end", "ypixels")
            total = total[0] if total else 0
            if total <= view_h:                         # whole text fits — never scroll
                return
            # Trigger only when the line is off-screen, or within the last two
            # lines of the page (i.e. it has reached the second-to-last line).
            if dline is not None and dline[1] < view_h - 2 * line_h:
                return
            top = self.reader.count("1.0", index, "ypixels")
            top = top[0] if top else 0
            target = max(0, top - line_h)               # put this line second from top
            self.reader.yview_moveto(min(1.0, target / total))
        except Exception:
            try:
                self.reader.see(index)
            except Exception:
                pass

    def _update_time_label(self, ms):
        self.time_lbl.config(text=f"{_fmt_time(ms)} / {_fmt_time(self.duration_ms)}")

    def _tick_player(self):
        if not (self.playing and not self.paused):
            return
        if HAVE_PYGAME and not pygame.mixer.music.get_busy() and \
                time.monotonic() - self._started_at > 0.4:
            self._on_track_end()
            return
        elapsed = self._elapsed_ms()
        self._update_time_label(elapsed)
        if self.duration_ms > 0 and not self.user_seeking:
            self.seek.set(min(1000, 1000 * elapsed / self.duration_ms))
        i = self._unit_at(elapsed)
        if i is not None:
            self._highlight_unit(i)
        if time.monotonic() - self._last_pos_save > 5:
            self._save_position()
        if self.sleep_deadline and self.sleep_deadline != "track" and \
                time.monotonic() >= self.sleep_deadline:
            self.sleep_deadline = None
            self.sleep_var.set("Off")
            self.stop_play(save_pos=True)
            self.status_var.set("Sleep timer: stopped.")

    def _on_track_end(self):
        # clear saved position for a finished track
        self.settings.get("positions", {}).pop(str(self.cur_track.resolve()), None)
        end_of_track_sleep = (self.sleep_deadline == "track")
        self.stop_play()
        if end_of_track_sleep:
            self.sleep_deadline = None
            self.sleep_var.set("Off")
            self.status_var.set("Sleep timer: stopped at end of track.")
            return
        idx = self._track_index()
        if self.adv_var.get() and 0 <= idx < len(self.tracks) - 1:
            nxt = self.tracks[idx + 1]
            self.track_var.set(nxt.name)
            self._load_track(nxt)
            self._request_play(0)
        else:
            self.status_var.set("Finished.")

    # read-along option handlers ----------------------------------------------
    def _on_read_speed(self, _evt=None):
        try:
            self.speed = float(self.speed_var.get().rstrip("×"))
        except ValueError:
            self.speed = 1.0
        self.settings["speed"] = self.speed
        self._save_settings()
        if self.playing:
            pos = self._elapsed_ms()
            was_paused = self.paused
            self._request_play(pos)
            if was_paused:
                self.root.after(50, self.toggle_play)   # re-pause

    def _on_highlight_pick(self, _evt=None):
        if self.hl_var.get() == "Custom…":
            init = self._highlight_color()
            rgb, hexv = colorchooser.askcolor(color=init, title="Highlight colour")
            if hexv:
                self.hl_var.set(hexv)
        self.settings["highlight"] = self.hl_var.get()
        self._save_settings()
        self._restyle_reader()
        if 0 <= self.hl_index < len(self.cur_ranges):
            a, b = self.cur_ranges[self.hl_index]
            self.reader.tag_remove("hl", "1.0", "end")
            self.reader.tag_add("hl", a, b)

    def _on_sleep_pick(self, _evt=None):
        choice = self.sleep_var.get()
        if choice == "Off":
            self.sleep_deadline = None
        elif choice == "End of track":
            self.sleep_deadline = "track"
        else:
            mins = int(choice.split()[0])
            self.sleep_deadline = time.monotonic() + mins * 60
        if choice != "Off":
            self.status_var.set(f"Sleep timer set: {choice}.")

    def _open_current_track(self):
        p = self.cur_track
        if not p or not Path(p).exists():
            messagebox.showinfo("No track", "Pick a track first.")
            return
        self._open_path(p)

    # ── keyboard shortcuts ───────────────────────────────────────────────────
    def _bind_keys(self):
        def guard(fn):
            def handler(e):
                cls = e.widget.winfo_class()
                if cls in ("TEntry", "Entry", "TCombobox", "TSpinbox", "Spinbox"):
                    return
                fn()
                return "break"
            return handler
        self.root.bind("<space>", guard(self.toggle_play))
        self.root.bind("<Left>", guard(lambda: self._step_sentence(-1)))
        self.root.bind("<Right>", guard(lambda: self._step_sentence(1)))
        self.root.bind("<Up>", guard(lambda: self._step_track(-1)))
        self.root.bind("<Down>", guard(lambda: self._step_track(1)))

    def _step_track(self, delta):
        if not self.tracks:
            return
        idx = self._track_index()
        if idx < 0:
            idx = 0
        new = max(0, min(len(self.tracks) - 1, idx + delta))
        if new != idx:
            self.track_var.set(self.tracks[new].name)
            self._load_track(self.tracks[new])

    # ── settings persistence ─────────────────────────────────────────────────
    def _save_position(self):
        self._last_pos_save = time.monotonic()
        if self.cur_track is None or self.duration_ms <= 0:
            return
        pos = int(self._elapsed_ms())
        self.settings.setdefault("positions", {})[str(self.cur_track.resolve())] = pos
        self._save_settings()

    def _save_settings(self):
        try:
            self.settings.update({
                "theme": self.theme,
                "voice": self.voice_var.get() if hasattr(self, "voice_var") else self.settings.get("voice"),
                "font_family": self.font_var.get() if hasattr(self, "font_var") else self.settings.get("font_family"),
                "font_size": int(self.size_var.get()) if hasattr(self, "size_var") else self.settings.get("font_size"),
                "line_spacing": int(self.ls_var.get()) if hasattr(self, "ls_var") else self.settings.get("line_spacing"),
                "speed": self.speed,
                "auto_advance": bool(self.adv_var.get()) if hasattr(self, "adv_var") else True,
                "highlight": self.hl_var.get() if hasattr(self, "hl_var") else "",
                "last_out": self.out_var.get() if hasattr(self, "out_var") else "",
            })
            SETTINGS_FILE.write_text(json.dumps(self.settings, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, tk.TclError, ValueError):
            pass

    def _on_close(self):
        try:
            self._save_position()
            self._save_settings()
        finally:
            self.root.destroy()

    # ── GUI event pump ───────────────────────────────────────────────────────
    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "loaded":
                    self._on_loaded(payload)
                elif kind == "load_error":
                    self.status_var.set("Ready.")
                    messagebox.showerror("Could not read EPUB", payload)
                elif kind == "log":
                    self.log_line(payload[0], payload[1])
                elif kind == "part":
                    name, cached = payload
                    self.done_parts += 1
                    self.pbar.config(value=self.done_parts)
                    suffix = " (already done)" if cached else ""
                    self.status_var.set(f"{self.done_parts}/{self.total_parts} files · {name}{suffix}")
                elif kind == "preview_play":
                    self.preview_btn.config(state="normal")
                    self.status_var.set("Preview ready.")
                    try:
                        pygame.mixer.music.load(payload); pygame.mixer.music.play()
                    except Exception:
                        pass
                elif kind == "preview_err":
                    self.preview_btn.config(state="normal")
                    self.status_var.set("Ready.")
                    messagebox.showerror("Preview failed", payload)
                elif kind == "play_ready":
                    path, from_ms, token = payload
                    if token == self._play_token:
                        self._do_play(path, from_ms)
                elif kind == "play_err":
                    self.play_btn.config(state="normal")
                    self.status_var.set("Ready.")
                    messagebox.showerror("Speed render failed", payload)
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self._tick_player()
        self.root.after(100, self._poll)

    def _on_done(self, failed):
        self.running = False
        self._set_running_ui(False)
        self.open_btn.config(state="normal")
        self._refresh_tracks()
        if self.cancel.is_set():
            self.status_var.set("Stopped. Re-run Convert to resume — finished files are kept.")
            self.log_line("Stopped (finished files are cached; Convert again to resume).", "muted")
        elif failed:
            self.status_var.set(f"Done with {len(failed)} failed chapter(s).")
            self.log_line(f"Failed chapters: {', '.join(map(str, sorted(failed)))}. "
                          "Click Convert again to retry — finished files are cached.", "err")
        else:
            self.status_var.set("All done! 🎧  Open the Read-along tab to listen.")
            self.log_line("All chapters complete.", "ok")
            self.pbar.config(value=self.pbar["maximum"])

    def open_output(self):
        d = getattr(self, "last_out_dir", None) or self.out_var.get().strip()
        if not d or not Path(d).exists():
            messagebox.showinfo("No folder yet", "Nothing has been generated yet.")
            return
        self._open_path(d)

    def _open_path(self, d):
        try:
            os.startfile(d)
        except AttributeError:
            import subprocess
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(d)])


def main():
    root = tk.Tk()
    app = App(root)
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        app.epub_var.set(sys.argv[1])
        root.after(300, app.reload_chapters)
    root.mainloop()


if __name__ == "__main__":
    main()
