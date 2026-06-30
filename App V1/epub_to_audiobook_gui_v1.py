"""
epub_to_audiobook_gui.py — EPUB → MP3 Audiobook Converter (graphical version)
=============================================================================
A simple, point-and-click window around the exact same engine as
`epub_to_audiobook.py`: it turns any .epub into a folder of nicely-named MP3
files that read the book aloud word-for-word, using Microsoft Edge's free
text-to-speech voices.

Just run this file — no terminal commands to memorise:

    • In VS Code / any IDE:  open this file and press the ▶ Run button
    • Or from a terminal:     python epub_to_audiobook_gui.py

SETUP (one-time)
----------------
    pip install edge-tts ebooklib beautifulsoup4

Everything the original command-line tool does is here as a control you can
click: choose the .epub, pick a voice, drag the speed slider, tick which
chapters to include, set part length / workers / output folder, then Convert.
Finished files are cached, so if you stop and run again it resumes where it
left off and never leaves a broken half-file behind.
"""

import os
import re
import sys
import time
import queue
import asyncio
import threading
import unicodedata
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ── Dependency check (friendly pop-up instead of a console crash) ──────────────

def _check_deps():
    missing = []
    for pkg, imp in [("edge-tts", "edge_tts"),
                     ("ebooklib", "ebooklib"),
                     ("beautifulsoup4", "bs4")]:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    if missing:
        msg = ("Missing required package(s):\n\n    " + ", ".join(missing) +
               "\n\nInstall them once by running:\n\n"
               "    pip install " + " ".join(missing))
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Setup needed", msg)
            root.destroy()
        except Exception:
            print("\n[X] " + msg + "\n")
        sys.exit(1)

_check_deps()

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import edge_tts


# ── Engine constants (identical to the command-line tool) ─────────────────────

CHARS_PER_MIN = 900      # rough chars/min of speech (~150 wpm * ~6 chars/word)
RETRY_LIMIT   = 4
RETRY_DELAY   = 3        # seconds between retries (multiplied by attempt #)

DROP_TAGS = {"script", "style", "head", "figure", "figcaption",
             "img", "svg", "aside", "nav", "footer"}
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
              "li", "tr", "br", "blockquote", "section", "article"}

POPULAR_VOICES = [
    "en-US-GuyNeural",                 # male, US (classic default)
    "en-US-AriaNeural",                # female, US
    "en-US-JennyNeural",               # female, US
    "en-US-EmmaMultilingualNeural",    # female, US (very natural)
    "en-US-AndrewMultilingualNeural",  # male, US (very natural)
    "en-GB-RyanNeural",                # male, UK
    "en-GB-SoniaNeural",               # female, UK
    "en-AU-NatashaNeural",             # female, Australia
    "en-CA-LiamNeural",                # male, Canada
    "en-IE-EmilyNeural",               # female, Ireland
    "en-IN-NeerjaNeural",              # female, India
]
DEFAULT_VOICE = "en-US-GuyNeural"


# ── Engine: text extraction / splitting / naming (identical logic) ────────────

def extract_text(html_bytes: bytes) -> str:
    """Parse one EPUB HTML document into clean, speakable plain text."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(BLOCK_TAGS):
        tag.append("\n")
    text = soup.get_text(separator=" ")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "")    # soft hyphen
    text = text.replace("�", "")    # decode-failure replacement char
    text = re.sub(r"https?://\S+", "", text)        # URLs
    text = re.sub(r"(?m)^\s*\d+\s*$", "", text)     # lone page/footnote numbers
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(text: str, max_chars: int) -> list:
    """Split text into <=max_chars pieces, always at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces, current = [], ""
    for sentence in sentences:
        if len(sentence) > max_chars:               # one very long sentence
            for word in sentence.split():
                if len(current) + len(word) + 1 > max_chars:
                    if current:
                        pieces.append(current.strip())
                    current = word + " "
                else:
                    current += word + " "
            continue
        if len(current) + len(sentence) + 1 > max_chars:
            pieces.append(current.strip())
            current = sentence + " "
        else:
            current += sentence + " "
    if current.strip():
        pieces.append(current.strip())
    return [p for p in pieces if p]


def flatten_toc(toc) -> list:
    """Flatten an ebooklib TOC into [(title, href_basename), ...]."""
    entries = []
    for item in toc:
        if isinstance(item, tuple):
            section, children = item
            if hasattr(section, "title") and hasattr(section, "href"):
                href = section.href.split("#")[0].split("/")[-1]
                entries.append((section.title.strip(), href))
            entries.extend(flatten_toc(children))
        elif hasattr(item, "title") and hasattr(item, "href"):
            href = item.href.split("#")[0].split("/")[-1]
            entries.append((item.title.strip(), href))
    return entries


def build_title_map(toc_entries: list) -> dict:
    seen = {}
    for title, href in toc_entries:
        if href not in seen:
            seen[href] = title
    return seen


def safe_filename(s: str, max_len: int = 120) -> str:
    """Readable, cross-platform-safe filename; only strips illegal characters."""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("�", "").replace("­", "")
    s = s.replace(":", " -").replace("/", "-").replace("\\", "-")
    s = re.sub(r'[<>"|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ")
    return s[:max_len].strip() or "Untitled"


def gather_chapters(book, min_chars: int) -> list:
    """Walk the spine in reading order -> [{index, title, text, file_name}]."""
    title_map = build_title_map(flatten_toc(book.toc))
    chapters, chapter_num = [], 0
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if not item or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        text = extract_text(item.get_content())
        if len(text) < min_chars:
            continue
        chapter_num += 1
        basename = Path(item.file_name).name
        title = title_map.get(basename, f"Section {chapter_num}")
        chapters.append({"index": chapter_num, "title": title,
                         "text": text, "file_name": basename})
    return chapters


# ── Engine: edge-tts synthesis (identical, crash-safe + atomic) ───────────────

async def _synth(text: str, path: str, voice: str, rate: str):
    await edge_tts.Communicate(text, voice=voice, rate=rate).save(path)


def tts_to_file(text: str, path: str, voice: str, rate: str):
    """Synthesise `text` to one MP3, retrying on transient network errors."""
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            asyncio.run(_synth(text, path, voice, rate))
            if os.path.getsize(path) == 0:
                raise RuntimeError("edge-tts wrote an empty file")
            return
        except Exception as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY * attempt)
            else:
                raise RuntimeError(
                    f"edge-tts failed after {RETRY_LIMIT} attempts: {exc}")


def normalize_rate(value: int) -> str:
    """Turn a slider integer (-50..50) into an edge-tts rate string ('+0%')."""
    return f"{int(value):+d}%"


# ──────────────────────────────────────────────────────────────────────────────
#  GUI
# ──────────────────────────────────────────────────────────────────────────────

# A calm, modern-ish palette layered on top of ttk's 'clam' theme.
BG      = "#f4f5f7"
CARD    = "#ffffff"
TEXT    = "#1f2430"
MUTED   = "#6b7280"
ACCENT  = "#2d6cdf"
ACCENT2 = "#1f57c3"
BORDER  = "#e3e6ea"
OK_CLR  = "#137a3f"
ERR_CLR = "#b42318"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events = queue.Queue()          # worker -> GUI messages
        self.cancel = threading.Event()
        self.book = None
        self.chapters = []                   # list of chapter dicts
        self.chapter_vars = []               # BooleanVar per chapter (include?)
        self.running = False
        self.total_parts = 0
        self.done_parts = 0

        root.title("EPUB → Audiobook")
        root.configure(bg=BG)
        root.geometry("780x860")
        root.minsize(680, 640)

        self._init_style()
        self._build_ui()
        self.root.after(100, self._poll)     # start the GUI event pump

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
        style.configure("Row.TCheckbutton", background=CARD, foreground=TEXT)
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

    # ── layout ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=(18, 16, 18, 14))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        # Header ----------------------------------------------------------------
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="EPUB → Audiobook", style="Header.TLabel").pack(anchor="w")
        ttk.Label(head, text="Turn any .epub into a folder of MP3 chapters read "
                  "aloud by a natural voice.", style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        # Source ----------------------------------------------------------------
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
                  wraplength=680, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))

        # Options ---------------------------------------------------------------
        opts = ttk.Labelframe(outer, text=" Options ", style="Section.TLabelframe",
                              padding=12)
        opts.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        for c in (1, 3):
            opts.columnconfigure(c, weight=1)

        # Voice
        ttk.Label(opts, text="Voice", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        self.voice_var = tk.StringVar(value=DEFAULT_VOICE)
        self.voice_box = ttk.Combobox(opts, textvariable=self.voice_var,
                                      values=POPULAR_VOICES, width=30)
        self.voice_box.grid(row=0, column=1, sticky="ew", pady=4)

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
        self.speed.set(0)        # set after the label exists (fires _on_speed)

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
        self.minchars_spin = ttk.Spinbox(opts, from_=50, to=5000, increment=50,
                                         textvariable=self.minchars_var, width=8)
        self.minchars_spin.grid(row=2, column=3, sticky="w", pady=4)

        # Checkboxes
        self.announce_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Announce chapter titles at the start of each file",
                        variable=self.announce_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.pad_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Zero-pad chapter numbers (Ch.01 vs Ch.1)",
                        variable=self.pad_var).grid(
            row=3, column=2, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Button(opts, text="Reload chapters with these settings",
                   command=self.reload_chapters).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))

        # Chapters --------------------------------------------------------------
        chap = ttk.Labelframe(outer, text=" Chapters ", style="Section.TLabelframe",
                              padding=10)
        chap.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        outer.rowconfigure(3, weight=1)
        chap.columnconfigure(0, weight=1)
        chap.rowconfigure(1, weight=1)

        bar = ttk.Frame(chap, style="Card.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(2, weight=1)
        ttk.Button(bar, text="Select all", width=10,
                   command=lambda: self._set_all(True)).grid(row=0, column=0)
        ttk.Button(bar, text="Clear all", width=10,
                   command=lambda: self._set_all(False)).grid(row=0, column=1, padx=(6, 0))
        self.sel_info = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.sel_info, style="CardMuted.TLabel").grid(
            row=0, column=2, sticky="e")

        # scrollable list of chapter check-rows
        list_wrap = tk.Frame(chap, bg=CARD, highlightthickness=1,
                             highlightbackground=BORDER)
        list_wrap.grid(row=1, column=0, sticky="nsew")
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(list_wrap, bg=CARD, highlightthickness=0, height=170)
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
        self._empty_lbl = tk.Label(self.list_frame, text="Load an .epub to see its chapters.",
                                   bg=CARD, fg=MUTED, font=("Segoe UI", 10), pady=18)
        self._empty_lbl.pack()

        # Progress + log --------------------------------------------------------
        prog = ttk.Frame(outer)
        prog.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        prog.columnconfigure(0, weight=1)
        self.pbar = ttk.Progressbar(prog, style="Horizontal.TProgressbar",
                                    mode="determinate")
        self.pbar.grid(row=0, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(prog, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0))

        log_wrap = tk.Frame(outer, bg=CARD, highlightthickness=1,
                           highlightbackground=BORDER)
        log_wrap.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        log_wrap.columnconfigure(0, weight=1)
        self.log = tk.Text(log_wrap, height=7, wrap="word", bg=CARD, fg=TEXT,
                          relief="flat", font=("Consolas", 9), padx=8, pady=6,
                          state="disabled")
        self.log.grid(row=0, column=0, sticky="ew")
        logsb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log.yview)
        logsb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=logsb.set)
        self.log.tag_configure("ok", foreground=OK_CLR)
        self.log.tag_configure("err", foreground=ERR_CLR)
        self.log.tag_configure("muted", foreground=MUTED)

        # Action bar ------------------------------------------------------------
        actions = ttk.Frame(outer)
        actions.grid(row=6, column=0, sticky="ew", pady=(12, 0))
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

    # ── small helpers ──────────────────────────────────────────────────────────
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
        return max(1000, minutes * CHARS_PER_MIN)

    def log_line(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", (tag,) if tag else ())
        self.log.see("end")
        self.log.configure(state="disabled")

    # ── loading the book ───────────────────────────────────────────────────────
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
            book = epub.read_epub(path)
            chapters = gather_chapters(book, min_chars=int(self.minchars_var.get()))
            title = book.title or Path(path).stem
            authors = book.get_metadata("DC", "creator")
            author = authors[0][0] if authors else "Unknown"
            self.events.put(("loaded", (book, chapters, title, author, path)))
        except Exception as exc:
            self.events.put(("load_error", str(exc)))

    def _on_loaded(self, payload):
        book, chapters, title, author, path = payload
        self.book = book
        self.chapters = chapters
        if not self.out_var.get().strip():
            base = Path(path).parent / safe_filename(title)
            self.out_var.set(str(base))
        total_words = sum(len(c["text"].split()) for c in chapters)
        est = total_words // 150
        self.info_var.set(
            f"📖  {title}\n👤  {author}\n"
            f"{len(chapters)} chapters · ~{total_words:,} words · "
            f"~{est // 60}h {est % 60}m of audio")
        self._populate_chapters()
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
            var = tk.BooleanVar(value=True)
            self.chapter_vars.append(var)
            row = tk.Frame(self.list_frame, bg=CARD)
            row.pack(fill="x", padx=6, pady=1)
            wc = len(ch["text"].split())
            n = len(split_text(ch["text"], part_chars))
            tag = f"{n} parts" if n > 1 else "1 file"
            label = f"{ch['index']:>2}.  {safe_filename(ch['title'])[:62]}"
            cb = tk.Checkbutton(row, text=label, variable=var, bg=CARD, fg=TEXT,
                                activebackground=CARD, selectcolor=CARD,
                                anchor="w", font=("Segoe UI", 10),
                                command=self._update_sel_info)
            cb.pack(side="left", fill="x", expand=True)
            tk.Label(row, text=f"~{wc:,} w · {tag}", bg=CARD, fg=MUTED,
                     font=("Segoe UI", 9)).pack(side="right")
        self._update_sel_info()

    def _refresh_chapter_parts(self):
        # Re-render the part counts when part-length changes (cheap, no reload).
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

    def _selected_chapters(self):
        return [ch for ch, v in zip(self.chapters, self.chapter_vars) if v.get()]

    def _update_sel_info(self):
        sel = self._selected_chapters()
        words = sum(len(c["text"].split()) for c in sel)
        est = words // 150
        self.sel_info.set(f"{len(sel)} of {len(self.chapters)} selected · "
                          f"~{est // 60}h {est % 60}m")

    # ── conversion ─────────────────────────────────────────────────────────────
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

        voice = self.voice_var.get().strip() or DEFAULT_VOICE
        rate = normalize_rate(self.rate_val.get())
        part_chars = self._part_chars()
        fmt = "Ch.{idx:02d}" if self.pad_var.get() else "Ch.{idx}"
        announce = self.announce_var.get()
        workers = max(1, int(self.workers_var.get()))

        self.total_parts = sum(len(split_text(c["text"], part_chars)) for c in selected)
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

        args = (selected, out_dir, fmt, part_chars, voice, rate, announce, workers)
        threading.Thread(target=self._convert_worker, args=args, daemon=True).start()

    def _convert_worker(self, selected, out_dir, fmt, part_chars,
                        voice, rate, announce, workers):
        failed = []

        def do_chapter(ch):
            if self.cancel.is_set():
                return ch["index"], "skipped"
            title = safe_filename(ch["title"])
            try:
                self._synth_chapter(ch, out_dir, fmt, part_chars, voice, rate, announce)
                if self.cancel.is_set():
                    return ch["index"], "skipped"
                self.events.put(("log", (f"  ✓  [{ch['index']:>2}] {title[:48]}", "ok")))
                return ch["index"], None
            except Exception as exc:
                self.events.put(("log", (f"  ✗  [{ch['index']:>2}] {title[:48]} — {exc}", "err")))
                return ch["index"], str(exc)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(do_chapter, ch): ch for ch in selected}
            for fut in as_completed(futures):
                idx, err = fut.result()
                if err and err != "skipped":
                    failed.append(idx)
        self.events.put(("done", failed))

    def _synth_chapter(self, chapter, out_dir, fmt, part_chars, voice, rate, announce):
        """One chapter -> one or more MP3s, with per-part progress + caching."""
        idx = chapter["index"]
        safe_ttl = safe_filename(chapter["title"])
        parts = split_text(chapter["text"], part_chars)
        multi = len(parts) > 1
        for pi, part_text in enumerate(parts, start=1):
            if self.cancel.is_set():
                return
            suffix = f" - Part {pi}" if multi else ""
            out_mp3 = out_dir / (fmt.format(idx=idx) + f" - {safe_ttl}{suffix}.mp3")
            if out_mp3.exists() and out_mp3.stat().st_size > 0:
                self.events.put(("part", (out_mp3.name, True)))
                continue
            spoken = part_text
            if announce:
                head = f"Chapter {idx}. {chapter['title']}."
                if multi:
                    head = f"Chapter {idx}. {chapter['title']}. Part {pi}."
                spoken = head + "\n" + part_text
            staging = out_mp3.with_suffix(".part")
            tts_to_file(spoken, str(staging), voice=voice, rate=rate)
            os.replace(staging, out_mp3)        # atomic — never a half file
            self.events.put(("part", (out_mp3.name, False)))

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
        self.open_btn.config(state="normal" if (not running and
                             getattr(self, "last_out_dir", None)) else self.open_btn["state"])

    # ── GUI event pump (handles all worker -> GUI messages) ────────────────────
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
                    text, tag = payload
                    self.log_line(text, tag)
                elif kind == "part":
                    name, cached = payload
                    self.done_parts += 1
                    self.pbar.config(value=self.done_parts)
                    suffix = " (already done)" if cached else ""
                    self.status_var.set(
                        f"{self.done_parts}/{self.total_parts} files · {name}{suffix}")
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _on_done(self, failed):
        self.running = False
        self._set_running_ui(False)
        self.open_btn.config(state="normal")
        if self.cancel.is_set():
            self.status_var.set("Stopped. Re-run Convert to resume — finished files are kept.")
            self.log_line("Stopped (finished files are cached; Convert again to resume).", "muted")
        elif failed:
            self.status_var.set(f"Done with {len(failed)} failed chapter(s).")
            self.log_line(f"Failed chapters: {', '.join(map(str, sorted(failed)))}. "
                          "Click Convert again to retry — finished files are cached.", "err")
        else:
            self.status_var.set("All done! 🎧")
            self.log_line("All chapters complete.", "ok")
            self.pbar.config(value=self.pbar["maximum"])

    def open_output(self):
        d = getattr(self, "last_out_dir", None) or self.out_var.get().strip()
        if not d or not Path(d).exists():
            messagebox.showinfo("No folder yet", "Nothing has been generated yet.")
            return
        try:
            os.startfile(d)                      # Windows
        except AttributeError:
            import subprocess
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(d)])


def main():
    root = tk.Tk()
    app = App(root)
    # If launched with a path argument, preload it.
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        app.epub_var.set(sys.argv[1])
        root.after(300, app.reload_chapters)
    root.mainloop()


if __name__ == "__main__":
    main()
