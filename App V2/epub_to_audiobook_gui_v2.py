"""
epub_to_audiobook_gui_v2.py — EPUB → MP3 Audiobook Converter (GUI, V2)
======================================================================
A point-and-click window around the shared engine in `audiobook_engine.py`
(the same engine the V2 command-line tool uses, so there's only one copy of the
logic). It turns any .epub into a folder of nicely-named MP3 chapters read aloud
by Microsoft Edge's free text-to-speech voices.

What's new in V2 (everything V1 did, plus):

    • READ-ALONG PLAYER — play any produced MP3 inside the app and watch the
      book's text highlight SENTENCE-BY-SENTENCE in time with the narration.
      Click a sentence to jump playback there.
    • Preview voice button — hear a short sample before converting a whole book.
    • Smarter chapter list — likely front/back matter (cover, copyright, index)
      is auto-flagged and starts unticked.
    • Optional output: write ID3 tags + cover, and/or combine everything into a
      single .m4b audiobook with chapter markers.

SETUP (one-time)
----------------
    pip install edge-tts ebooklib beautifulsoup4 pygame
    pip install mutagen            # optional: tags / .m4b cover
    # ffmpeg on PATH               # optional: single-file .m4b

Just run this file (press ▶ Run in your editor, or  python epub_to_audiobook_gui_v2.py).
Finished files are cached, so stopping and re-running resumes where it left off.
"""

import os
import sys
import time
import queue
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ── Friendly dependency check (pop-up instead of a console crash) ─────────────

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

import audiobook_engine as eng

# Optional audio backend for the read-along player.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    import pygame
    pygame.mixer.init()
    HAVE_PYGAME = True
except Exception:
    HAVE_PYGAME = False


# ──────────────────────────────────────────────────────────────────────────────
#  Theme
# ──────────────────────────────────────────────────────────────────────────────

BG       = "#f4f5f7"
CARD     = "#ffffff"
TEXT     = "#1f2430"
MUTED    = "#6b7280"
ACCENT   = "#2d6cdf"
ACCENT2  = "#1f57c3"
BORDER   = "#e3e6ea"
OK_CLR   = "#137a3f"
ERR_CLR  = "#b42318"
HL_BG    = "#fff3c4"        # current-sentence highlight
HL_FG    = "#1f2430"


def _natkey(p: Path):
    """Natural sort: Ch.2 before Ch.10."""
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
        self.events = queue.Queue()          # worker -> GUI messages
        self.cancel = threading.Event()
        self.book = None
        self.book_title = ""
        self.author = "Unknown"
        self.chapters = []
        self.chapter_vars = []
        self.running = False
        self.total_parts = 0
        self.done_parts = 0

        # ── read-along player state ──
        self.tracks = []                     # list[Path] of produced MP3s
        self.cur_sentences = []              # [{start_ms,end_ms,text}]
        self.cur_ranges = []                 # [(text_index_start, end)]
        self.cur_track = None                # Path
        self.duration_ms = 0
        self.playing = False
        self.paused = False
        self.anchor_ms = 0                   # file position at last play/seek
        self.t0 = 0.0                        # monotonic when that segment began
        self.user_seeking = False
        self.hl_index = -1                   # currently highlighted sentence

        root.title("EPUB → Audiobook  ·  V2")
        root.configure(bg=BG)
        root.geometry("860x980")
        root.minsize(720, 720)

        self._init_style()
        self._build_ui()
        self.root.after(100, self._poll)

    # ── styling ──────────────────────────────────────────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base_font = ("Segoe UI", 10)
        style.configure(".", background=BG, foreground=TEXT, font=base_font)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
        style.configure("Header.TLabel", background=BG, foreground=TEXT,
                        font=("Segoe UI Semibold", 17))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED,
                        font=("Segoe UI", 10))
        style.configure("Section.TLabelframe", background=CARD,
                        bordercolor=BORDER, relief="solid", borderwidth=1)
        style.configure("Section.TLabelframe.Label", background=CARD,
                        foreground=ACCENT, font=("Segoe UI Semibold", 10))
        style.configure("TCheckbutton", background=CARD, foreground=TEXT)
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10),
                        foreground="#ffffff", background=ACCENT,
                        bordercolor=ACCENT, padding=8)
        style.map("Accent.TButton",
                  background=[("active", ACCENT2), ("disabled", "#9db8ec")],
                  foreground=[("disabled", "#eef2fb")])
        style.configure("Horizontal.TProgressbar", troughcolor=BORDER,
                        background=ACCENT, bordercolor=BORDER, thickness=14)
        style.configure("TCombobox", padding=4)
        style.configure("TSpinbox", padding=3)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 7),
                        font=("Segoe UI", 10))

    # ── layout ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=(18, 16, 18, 14))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="EPUB → Audiobook", style="Header.TLabel").pack(anchor="w")
        ttk.Label(head, text="Convert any .epub to MP3 chapters — then read along, "
                  "sentence-by-sentence, as it plays.",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        # Book picker (always visible)
        src = ttk.Labelframe(outer, text=" Book ", style="Section.TLabelframe",
                             padding=12)
        src.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        src.columnconfigure(1, weight=1)
        ttk.Label(src, text="EPUB file", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.epub_var = tk.StringVar()
        self.epub_entry = ttk.Entry(src, textvariable=self.epub_var)
        self.epub_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(src, text="Browse…", command=self.choose_epub).grid(
            row=0, column=2, padx=(8, 0))
        self.info_var = tk.StringVar(value="No book loaded yet.")
        ttk.Label(src, textvariable=self.info_var, style="CardMuted.TLabel",
                  wraplength=760, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))

        # Notebook: Convert tab + Read-along tab
        nb = ttk.Notebook(outer)
        nb.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        self.tab_convert = ttk.Frame(nb, padding=2)
        self.tab_read = ttk.Frame(nb, padding=2)
        nb.add(self.tab_convert, text="  Convert  ")
        nb.add(self.tab_read, text="  Read-along  ")
        self.notebook = nb

        self._build_convert_tab(self.tab_convert)
        self._build_read_tab(self.tab_read)

    # ── Convert tab ──────────────────────────────────────────────────────────
    def _build_convert_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        opts = ttk.Labelframe(parent, text=" Options ", style="Section.TLabelframe",
                              padding=12)
        opts.grid(row=0, column=0, sticky="ew")
        for c in (1, 3):
            opts.columnconfigure(c, weight=1)

        # Voice + preview
        ttk.Label(opts, text="Voice", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        voice_wrap = ttk.Frame(opts, style="Card.TFrame")
        voice_wrap.grid(row=0, column=1, sticky="ew", pady=4)
        voice_wrap.columnconfigure(0, weight=1)
        self.voice_var = tk.StringVar(value=eng.DEFAULT_VOICE)
        self.voice_box = ttk.Combobox(voice_wrap, textvariable=self.voice_var,
                                      values=eng.POPULAR_VOICES, width=28)
        self.voice_box.grid(row=0, column=0, sticky="ew")
        self.preview_btn = ttk.Button(voice_wrap, text="Preview",
                                      command=self.preview_voice, width=8)
        self.preview_btn.grid(row=0, column=1, padx=(6, 0))

        # Output folder
        ttk.Label(opts, text="Output folder", style="Card.TLabel").grid(
            row=0, column=2, sticky="w", pady=4, padx=(16, 8))
        self.out_var = tk.StringVar()
        out_wrap = ttk.Frame(opts, style="Card.TFrame")
        out_wrap.grid(row=0, column=3, sticky="ew", pady=4)
        out_wrap.columnconfigure(0, weight=1)
        ttk.Entry(out_wrap, textvariable=self.out_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(out_wrap, text="…", width=3,
                   command=self.choose_outdir).grid(row=0, column=1, padx=(6, 0))

        # Speed slider
        ttk.Label(opts, text="Speed", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=4, padx=(0, 8))
        speed_wrap = ttk.Frame(opts, style="Card.TFrame")
        speed_wrap.grid(row=1, column=1, sticky="ew", pady=4)
        speed_wrap.columnconfigure(0, weight=1)
        self.rate_val = tk.IntVar(value=0)
        self.speed = ttk.Scale(speed_wrap, from_=-50, to=50, orient="horizontal",
                               command=self._on_speed)
        self.speed.grid(row=0, column=0, sticky="ew")
        self.speed_lbl = ttk.Label(speed_wrap, text="+0%", style="CardMuted.TLabel",
                                   width=6)
        self.speed_lbl.grid(row=0, column=1, padx=(8, 0))
        self.speed.set(0)

        # Part length
        ttk.Label(opts, text="Part length (min)", style="Card.TLabel").grid(
            row=1, column=2, sticky="w", pady=4, padx=(16, 8))
        self.part_var = tk.IntVar(value=12)
        self.part_spin = ttk.Spinbox(opts, from_=1, to=60, textvariable=self.part_var,
                                     width=8, command=self._refresh_chapter_parts)
        self.part_spin.grid(row=1, column=3, sticky="w", pady=4)
        self.part_var.trace_add("write", lambda *_: self._refresh_chapter_parts())

        # Workers
        ttk.Label(opts, text="Parallel workers", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", pady=4, padx=(0, 8))
        self.workers_var = tk.IntVar(value=4)
        ttk.Spinbox(opts, from_=1, to=8, textvariable=self.workers_var,
                    width=8).grid(row=2, column=1, sticky="w", pady=4)

        # Min chars
        ttk.Label(opts, text="Min chars / chapter", style="Card.TLabel").grid(
            row=2, column=2, sticky="w", pady=4, padx=(16, 8))
        self.minchars_var = tk.IntVar(value=300)
        ttk.Spinbox(opts, from_=50, to=5000, increment=50,
                    textvariable=self.minchars_var, width=8).grid(
            row=2, column=3, sticky="w", pady=4)

        # Checkboxes
        self.subs_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Generate read-along subtitles (sentence timing)",
                        variable=self.subs_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.announce_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Announce chapter titles at the start of each file",
                        variable=self.announce_var).grid(
            row=3, column=2, columnspan=2, sticky="w", pady=(8, 0))
        self.pad_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Zero-pad chapter numbers (Ch.01 vs Ch.1)",
                        variable=self.pad_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.tag_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Write tags + cover" +
                        ("" if eng.HAVE_MUTAGEN else "  (needs mutagen)"),
                        variable=self.tag_var,
                        state="normal" if eng.HAVE_MUTAGEN else "disabled").grid(
            row=4, column=2, sticky="w", pady=(2, 0))
        self.m4b_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Combine into one .m4b" +
                        ("" if eng.have_ffmpeg() else "  (needs ffmpeg)"),
                        variable=self.m4b_var,
                        state="normal" if eng.have_ffmpeg() else "disabled").grid(
            row=4, column=3, sticky="w", pady=(2, 0))

        ttk.Button(opts, text="Reload chapters with these settings",
                   command=self.reload_chapters).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))

        # Chapters
        chap = ttk.Labelframe(parent, text=" Chapters ", style="Section.TLabelframe",
                              padding=10)
        chap.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        chap.columnconfigure(0, weight=1)
        chap.rowconfigure(1, weight=1)

        bar = ttk.Frame(chap, style="Card.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(3, weight=1)
        ttk.Button(bar, text="Select all", width=10,
                   command=lambda: self._set_all(True)).grid(row=0, column=0)
        ttk.Button(bar, text="Clear all", width=10,
                   command=lambda: self._set_all(False)).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(bar, text="Unflag junk", width=12,
                   command=self._untick_junk).grid(row=0, column=2, padx=(6, 0))
        self.sel_info = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.sel_info, style="CardMuted.TLabel").grid(
            row=0, column=3, sticky="e")

        list_wrap = tk.Frame(chap, bg=CARD, highlightthickness=1,
                             highlightbackground=BORDER)
        list_wrap.grid(row=1, column=0, sticky="nsew")
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(list_wrap, bg=CARD, highlightthickness=0, height=150)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(list_wrap, orient="vertical", command=self.canvas.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=vsb.set)
        self.list_frame = tk.Frame(self.canvas, bg=CARD)
        self.canvas_win = self.canvas.create_window((0, 0), window=self.list_frame,
                                                    anchor="nw")
        self.list_frame.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(
            self.canvas_win, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        tk.Label(self.list_frame, text="Load an .epub to see its chapters.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 10), pady=18).pack()

        # Progress + log
        prog = ttk.Frame(parent)
        prog.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        prog.columnconfigure(0, weight=1)
        self.pbar = ttk.Progressbar(prog, style="Horizontal.TProgressbar",
                                    mode="determinate")
        self.pbar.grid(row=0, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(prog, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0))

        log_wrap = tk.Frame(parent, bg=CARD, highlightthickness=1,
                           highlightbackground=BORDER)
        log_wrap.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        log_wrap.columnconfigure(0, weight=1)
        self.log = tk.Text(log_wrap, height=5, wrap="word", bg=CARD, fg=TEXT,
                          relief="flat", font=("Consolas", 9), padx=8, pady=6,
                          state="disabled")
        self.log.grid(row=0, column=0, sticky="ew")
        logsb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log.yview)
        logsb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=logsb.set)
        self.log.tag_configure("ok", foreground=OK_CLR)
        self.log.tag_configure("err", foreground=ERR_CLR)
        self.log.tag_configure("muted", foreground=MUTED)

        # Actions
        actions = ttk.Frame(parent)
        actions.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        self.open_btn = ttk.Button(actions, text="Open output folder",
                                   command=self.open_output, state="disabled")
        self.open_btn.grid(row=0, column=0, sticky="w")
        self.stop_btn = ttk.Button(actions, text="Stop", command=self.stop,
                                   state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=(0, 8))
        self.convert_btn = ttk.Button(actions, text="Convert", style="Accent.TButton",
                                      command=self.start_convert, state="disabled")
        self.convert_btn.grid(row=0, column=2)

    # ── Read-along tab ───────────────────────────────────────────────────────
    def _build_read_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        if not HAVE_PYGAME:
            ttk.Label(parent, style="Muted.TLabel", wraplength=760, justify="left",
                      text="In-app playback needs the 'pygame' package "
                           "(pip install pygame). You can still pick a track to "
                           "read its text and open it in your media player.").grid(
                row=0, column=0, sticky="w", pady=(6, 8))

        # Track picker
        pick = ttk.Frame(parent)
        pick.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        pick.columnconfigure(1, weight=1)
        ttk.Label(pick, text="Track", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.track_var = tk.StringVar()
        self.track_box = ttk.Combobox(pick, textvariable=self.track_var,
                                      state="readonly", values=[])
        self.track_box.grid(row=0, column=1, sticky="ew")
        self.track_box.bind("<<ComboboxSelected>>", self._on_track_selected)
        ttk.Button(pick, text="Refresh", command=self._refresh_tracks,
                   width=9).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(pick, text="Open file", command=self._open_current_track,
                   width=10).grid(row=0, column=3, padx=(6, 0))

        # Text pane
        text_wrap = tk.Frame(parent, bg=CARD, highlightthickness=1,
                             highlightbackground=BORDER)
        text_wrap.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        text_wrap.columnconfigure(0, weight=1)
        text_wrap.rowconfigure(0, weight=1)
        self.reader = tk.Text(text_wrap, wrap="word", bg=CARD, fg=TEXT,
                              relief="flat", font=("Georgia", 13),
                              padx=16, pady=14, spacing2=4, spacing3=8,
                              cursor="hand2", state="disabled")
        self.reader.grid(row=0, column=0, sticky="nsew")
        rsb = ttk.Scrollbar(text_wrap, orient="vertical", command=self.reader.yview)
        rsb.grid(row=0, column=1, sticky="ns")
        self.reader.configure(yscrollcommand=rsb.set)
        self.reader.tag_configure("hl", background=HL_BG, foreground=HL_FG)
        self._reader_placeholder("Convert a book (or pick a track) to read along here.")

        # Transport
        trans = ttk.Frame(parent)
        trans.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        trans.columnconfigure(4, weight=1)
        self.play_btn = ttk.Button(trans, text="▶ Play", width=9,
                                   command=self.toggle_play, state="disabled")
        self.play_btn.grid(row=0, column=0)
        self.stopp_btn = ttk.Button(trans, text="■ Stop", width=8,
                                    command=self.stop_play, state="disabled")
        self.stopp_btn.grid(row=0, column=1, padx=(6, 0))
        self.time_lbl = ttk.Label(trans, text="0:00 / 0:00", style="Muted.TLabel",
                                  width=14)
        self.time_lbl.grid(row=0, column=2, padx=(10, 8))
        self.seek = ttk.Scale(trans, from_=0, to=1000, orient="horizontal")
        self.seek.grid(row=0, column=4, sticky="ew")
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "user_seeking", True))
        self.seek.bind("<ButtonRelease-1>", self._on_seek_commit)
        if not HAVE_PYGAME:
            for w in (self.play_btn, self.stopp_btn):
                w.config(state="disabled")

    def _reader_placeholder(self, text):
        self.reader.configure(state="normal")
        self.reader.delete("1.0", "end")
        self.reader.insert("1.0", text)
        self.reader.tag_add("ph", "1.0", "end")
        self.reader.tag_configure("ph", foreground=MUTED, font=("Segoe UI", 11))
        self.reader.configure(state="disabled")

    # ── small helpers ────────────────────────────────────────────────────────
    def _on_speed(self, _val):
        v = int(round(float(self.speed.get()) / 5.0) * 5)
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
        path = filedialog.askopenfilename(
            title="Choose an EPUB file",
            filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")])
        if path:
            self.epub_var.set(path)
            self.reload_chapters()

    def choose_outdir(self):
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.out_var.set(d)
            self._refresh_tracks()

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
        self.book = book
        self.book_title = title
        self.author = author
        self.chapters = chapters
        if not self.out_var.get().strip():
            self.out_var.set(str(Path(path).parent / eng.safe_filename(title)))
        words, est = eng.estimate_words_minutes(chapters)
        njunk = sum(1 for c in chapters if c["junk"])
        self.info_var.set(
            f"📖  {title}\n👤  {author}\n"
            f"{len(chapters)} chapters · ~{words:,} words · "
            f"~{est // 60}h {est % 60}m of audio"
            + (f" · {njunk} flagged as front/back matter" if njunk else ""))
        self._populate_chapters()
        self._refresh_tracks()
        self.status_var.set("Ready. Tick the chapters you want, then Convert.")
        self.convert_btn.config(state="normal" if chapters else "disabled")

    def _populate_chapters(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.chapter_vars = []
        part_chars = self._part_chars()
        if not self.chapters:
            tk.Label(self.list_frame, text="No chapters found. Try lowering "
                     "“Min chars / chapter”.", bg=CARD, fg=MUTED,
                     font=("Segoe UI", 10), pady=18).pack()
            return
        for ch in self.chapters:
            var = tk.BooleanVar(value=not ch["junk"])     # junk starts unticked
            self.chapter_vars.append(var)
            row = tk.Frame(self.list_frame, bg=CARD)
            row.pack(fill="x", padx=6, pady=1)
            wc = len(ch["text"].split())
            n = len(eng.split_text(ch["text"], part_chars))
            tag = f"{n} parts" if n > 1 else "1 file"
            flag = "  ⚑" if ch["junk"] else ""
            label = f"{ch['index']:>2}.  {eng.safe_filename(ch['title'])[:60]}{flag}"
            cb = tk.Checkbutton(row, text=label, variable=var, bg=CARD,
                                fg=(MUTED if ch["junk"] else TEXT),
                                activebackground=CARD, selectcolor=CARD,
                                anchor="w", font=("Segoe UI", 10),
                                command=self._update_sel_info)
            cb.pack(side="left", fill="x", expand=True)
            tk.Label(row, text=f"~{wc:,} w · {tag}", bg=CARD, fg=MUTED,
                     font=("Segoe UI", 9)).pack(side="right")
        self._update_sel_info()

    def _refresh_chapter_parts(self):
        if self.chapters:
            states = [v.get() for v in self.chapter_vars] if self.chapter_vars else None
            self._populate_chapters()
            if states and len(states) == len(self.chapter_vars):
                for v, s in zip(self.chapter_vars, states):
                    v.set(s)
            self._update_sel_info()

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
        self.sel_info.set(f"{len(sel)} of {len(self.chapters)} selected · "
                          f"~{est // 60}h {est % 60}m")

    # ── voice preview ────────────────────────────────────────────────────────
    def preview_voice(self):
        if not HAVE_PYGAME:
            messagebox.showinfo("Preview needs pygame",
                                "Install pygame to hear voice previews:\n\n"
                                "    pip install pygame")
            return
        voice = self.voice_var.get().strip() or eng.DEFAULT_VOICE
        rate = eng.normalize_rate(self.rate_val.get())
        self.preview_btn.config(state="disabled")
        self.status_var.set(f"Synthesising preview ({voice})…")
        threading.Thread(target=self._preview_worker, args=(voice, rate),
                         daemon=True).start()

    def _preview_worker(self, voice, rate):
        import tempfile
        sample = ("Hello. This is a short sample of how this voice will read "
                  "your book. You can change the speed with the slider.")
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
        announce = self.announce_var.get()
        subs = self.subs_var.get()
        workers = max(1, int(self.workers_var.get()))

        self.total_parts = sum(len(eng.split_text(c["text"], part_chars))
                               for c in selected)
        self.done_parts = 0
        self.pbar.config(maximum=max(1, self.total_parts), value=0)
        self.cancel.clear()
        self.running = True
        self._set_running_ui(True)
        self.log.configure(state="normal"); self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.log_line(f"Converting {len(selected)} chapter(s) → {out_dir}", "muted")
        self.status_var.set("Synthesising…")
        self.last_out_dir = out_dir

        args = (selected, out_dir, fmt, part_chars, voice, rate, announce, subs,
                workers, self.tag_var.get(), self.m4b_var.get())
        threading.Thread(target=self._convert_worker, args=args, daemon=True).start()

    def _convert_worker(self, selected, out_dir, fmt, part_chars, voice, rate,
                        announce, subs, workers, do_tag, do_m4b):
        failed = []
        produced = {}

        def on_part(mp3_path, cached):
            self.events.put(("part", (mp3_path.name, cached)))

        def do_chapter(ch):
            if self.cancel.is_set():
                return ch["index"], "skipped", []
            title = eng.safe_filename(ch["title"])
            try:
                paths = eng.synthesize_chapter(
                    ch, out_dir, fmt, part_chars, voice, rate, announce,
                    subs=subs, on_part=on_part,
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

        # Optional post-processing (only on a clean, non-cancelled run).
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
                ordered_paths.append(p)
                ordered_titles.append(eng.safe_filename(ch["title"]))
        if not ordered_paths:
            return
        cover = eng.extract_cover(self.book) if self.book else b""

        if do_tag and eng.HAVE_MUTAGEN:
            for n, (p, ctitle) in enumerate(zip(ordered_paths, ordered_titles), 1):
                eng.write_tags(p, album=self.book_title, author=self.author,
                               track=n, title=ctitle, cover_bytes=cover)
            self.events.put(("log", (f"  ✓  tagged {len(ordered_paths)} MP3(s)", "ok")))

        if do_m4b and eng.have_ffmpeg():
            out_m4b = out_dir / f"{eng.safe_filename(self.book_title)}.m4b"
            self.events.put(("log", (f"  building {out_m4b.name} …", "muted")))
            eng.combine_to_m4b(ordered_paths, out_m4b, title=self.book_title,
                               author=self.author, chapter_titles=ordered_titles,
                               cover_bytes=cover)
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
        if names and not self.track_var.get():
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

    def _load_track(self, path: Path):
        """Load a track's text + timing into the reader and reset the player."""
        self.stop_play()
        self.cur_track = path
        data = eng.load_subs(path)
        sents = data.get("sentences", [])
        self.cur_sentences = sents

        self.reader.configure(state="normal")
        self.reader.delete("1.0", "end")
        self.cur_ranges = []
        if sents:
            for s in sents:
                start = self.reader.index("end-1c")
                self.reader.insert("end", s["text"] + "  ")
                end = self.reader.index("end-1c")
                self.cur_ranges.append((start, end))
            self.duration_ms = max((s["end_ms"] for s in sents), default=0)
        else:
            self.reader.insert("end",
                               "No read-along timing for this track.\n\n"
                               "(It was made without subtitles, or by V1.) "
                               "You can still play it with your media player.")
            self.duration_ms = self._probe_duration(path)
        self.reader.configure(state="disabled")
        self.reader.tag_remove("hl", "1.0", "end")
        self.hl_index = -1

        # Make each sentence clickable -> jump.
        self.reader.tag_remove("sent", "1.0", "end")
        for i, (a, b) in enumerate(self.cur_ranges):
            tagn = f"s{i}"
            self.reader.tag_add(tagn, a, b)
            self.reader.tag_bind(tagn, "<Button-1>",
                                 lambda e, idx=i: self._jump_to_sentence(idx))

        have = HAVE_PYGAME and self.cur_track is not None
        self.play_btn.config(state="normal" if have else "disabled",
                             text="▶ Play")
        self.stopp_btn.config(state="disabled")
        self.seek.set(0)
        self._update_time_label(0)

    def _probe_duration(self, path):
        if eng.HAVE_MUTAGEN:
            try:
                from mutagen.mp3 import MP3
                return int(MP3(str(path)).info.length * 1000)
            except Exception:
                pass
        return 0

    def _elapsed_ms(self):
        if self.playing and not self.paused:
            return self.anchor_ms + (time.monotonic() - self.t0) * 1000
        return self.anchor_ms

    def toggle_play(self):
        if not HAVE_PYGAME or self.cur_track is None:
            return
        if not self.playing:
            self._start_playback(self.anchor_ms)
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

    def _start_playback(self, from_ms):
        try:
            pygame.mixer.music.load(str(self.cur_track))
            pygame.mixer.music.play(start=max(0, from_ms) / 1000.0)
        except Exception as exc:
            messagebox.showerror("Playback failed", str(exc))
            return
        self.anchor_ms = max(0, from_ms)
        self.t0 = time.monotonic()
        self.playing = True
        self.paused = False
        self.play_btn.config(text="❚❚ Pause", state="normal")
        self.stopp_btn.config(state="normal")

    def stop_play(self):
        if HAVE_PYGAME:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self.playing = False
        self.paused = False
        self.anchor_ms = 0
        if hasattr(self, "play_btn"):
            self.play_btn.config(text="▶ Play")
            self.stopp_btn.config(state="disabled")
            self.seek.set(0)
            self._update_time_label(0)
        self.reader.tag_remove("hl", "1.0", "end")
        self.hl_index = -1

    def _jump_to_sentence(self, idx):
        if not (0 <= idx < len(self.cur_sentences)):
            return
        start_ms = self.cur_sentences[idx]["start_ms"]
        if HAVE_PYGAME and self.cur_track is not None:
            self._start_playback(start_ms)
        self._highlight_sentence(idx)

    def _on_seek_commit(self, _evt):
        self.user_seeking = False
        if self.duration_ms <= 0:
            return
        frac = float(self.seek.get()) / 1000.0
        target = int(frac * self.duration_ms)
        if HAVE_PYGAME and self.cur_track is not None and self.playing:
            self._start_playback(target)
        else:
            self.anchor_ms = target
        self._update_time_label(target)

    def _highlight_sentence(self, idx):
        if idx is None or idx == self.hl_index or not (0 <= idx < len(self.cur_ranges)):
            return
        self.reader.tag_remove("hl", "1.0", "end")
        a, b = self.cur_ranges[idx]
        self.reader.tag_add("hl", a, b)
        self.reader.see(a)
        self.hl_index = idx

    def _update_time_label(self, ms):
        self.time_lbl.config(text=f"{_fmt_time(ms)} / {_fmt_time(self.duration_ms)}")

    def _tick_player(self):
        """Called from the poll loop: advance highlight + slider while playing."""
        if not (self.playing and not self.paused):
            return
        # Detect natural end of playback.
        if HAVE_PYGAME and not pygame.mixer.music.get_busy():
            self.stop_play()
            return
        elapsed = self._elapsed_ms()
        self._update_time_label(elapsed)
        if self.duration_ms > 0 and not self.user_seeking:
            self.seek.set(min(1000, 1000 * elapsed / self.duration_ms))
        # Find the current sentence (linear scan from current index is plenty).
        for i, s in enumerate(self.cur_sentences):
            if s["start_ms"] <= elapsed < s["end_ms"]:
                self._highlight_sentence(i)
                break

    def _open_current_track(self):
        p = self.cur_track
        if not p or not Path(p).exists():
            messagebox.showinfo("No track", "Pick a track first.")
            return
        self._open_path(p)

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
                    self.log_line(*((payload[0], payload[1])
                                    if isinstance(payload, tuple) else (payload,)))
                elif kind == "part":
                    name, cached = payload
                    self.done_parts += 1
                    self.pbar.config(value=self.done_parts)
                    suffix = " (already done)" if cached else ""
                    self.status_var.set(
                        f"{self.done_parts}/{self.total_parts} files · {name}{suffix}")
                elif kind == "preview_play":
                    self.preview_btn.config(state="normal")
                    self.status_var.set("Preview ready.")
                    try:
                        pygame.mixer.music.load(payload)
                        pygame.mixer.music.play()
                    except Exception:
                        pass
                elif kind == "preview_err":
                    self.preview_btn.config(state="normal")
                    self.status_var.set("Ready.")
                    messagebox.showerror("Preview failed", payload)
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

    # ── misc ─────────────────────────────────────────────────────────────────
    def open_output(self):
        d = getattr(self, "last_out_dir", None) or self.out_var.get().strip()
        if not d or not Path(d).exists():
            messagebox.showinfo("No folder yet", "Nothing has been generated yet.")
            return
        self._open_path(d)

    def _open_path(self, d):
        try:
            os.startfile(d)                      # Windows
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
