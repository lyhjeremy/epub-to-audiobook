"""
audiobook_engine.py — Shared EPUB → Audiobook engine (V2)
=========================================================
All the non-UI logic for turning an .epub into a folder of nicely-named MP3
chapters, shared by both V2 entry points:

    epub_to_audiobook_v2.py        (command line)
    epub_to_audiobook_gui_v2.py    (graphical, with read-along player)

This is the V1 engine (text extraction, chapter detection, edge-tts synthesis,
crash-safe atomic writes, resume-on-rerun caching) factored into one module and
extended with:

    • Sentence-level timing  — capture edge-tts WordBoundary events during
      synthesis and write a sidecar  "<mp3-stem>.subs.json"  next to each MP3,
      so the GUI can highlight the text sentence-by-sentence in sync with audio.
    • Smarter chapter detection — heading-based title fallback and a "junk"
      flag for front/back matter (cover, copyright, index …).
    • Output packaging — optional ID3 tags + embedded cover (mutagen) and an
      optional single .m4b audiobook with chapter markers (ffmpeg).

SETUP (one-time)
----------------
    pip install edge-tts ebooklib beautifulsoup4 pygame
    # optional extras:
    pip install mutagen           # ID3 tags + cover art on each MP3
    # ffmpeg on PATH              # required only for the single-file .m4b

Nothing here imports a GUI toolkit, so it is safe to import from anywhere.
"""

import os
import re
import sys
import json
import asyncio
import shutil
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path


# ── Dependency check ──────────────────────────────────────────────────────────

REQUIRED_DEPS = [("edge-tts", "edge_tts"),
                 ("ebooklib", "ebooklib"),
                 ("beautifulsoup4", "bs4")]


def missing_deps():
    """Return the list of pip names for any required package that won't import."""
    missing = []
    for pkg, imp in REQUIRED_DEPS:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    return missing


# Import the required libraries lazily-but-eagerly so importing this module fails
# loudly only when a *core* dependency is absent. Callers (CLI/GUI) check
# missing_deps() first to show a friendly message.
import ebooklib                       # noqa: E402
from ebooklib import epub             # noqa: E402
from bs4 import BeautifulSoup         # noqa: E402
import edge_tts                       # noqa: E402

try:
    import mutagen                    # noqa: F401
    HAVE_MUTAGEN = True
except ImportError:
    HAVE_MUTAGEN = False


# ── Constants ─────────────────────────────────────────────────────────────────

CHARS_PER_MIN     = 900   # English: ~150 wpm * ~6 chars/word
CJK_CHARS_PER_MIN = 300   # Mandarin/Cantonese: ~1 char/syllable, ~5 chars/sec
DEFAULT_VOICE = "en-US-GuyNeural"
RETRY_LIMIT   = 4
RETRY_DELAY   = 3       # seconds between retries (multiplied by attempt #)

DROP_TAGS = {"script", "style", "head", "figure", "figcaption",
             "img", "svg", "aside", "nav", "footer"}
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
              "li", "tr", "br", "blockquote", "section", "article"}

# Sections whose title/filename look like front/back matter rather than the book
# itself. These are *flagged* (junk=True), never silently dropped — the user
# decides. (Lower-cased substring match.)
JUNK_PATTERNS = [
    "cover", "title page", "half title", "copyright", "colophon", "imprint",
    "index", "acknowledg", "about the author", "about the publisher",
    "also by", "praise for", "front matter", "back matter", "dedication",
    "table of contents", "contents",
    # Chinese front/back matter (Simplified + Traditional)
    "目录", "目錄", "版权", "版權", "封面", "扉页", "扉頁", "致谢", "致謝",
    "索引", "关于作者", "關於作者", "版权页", "版權頁",
]

# Voices grouped by language for the UI. Mandarin = zh-CN / zh-TW, Cantonese = zh-HK.
VOICES_BY_LANG = {
    "English (US)": [
        "en-US-GuyNeural", "en-US-AndrewMultilingualNeural",
        "en-US-BrianMultilingualNeural", "en-US-AriaNeural", "en-US-JennyNeural",
        "en-US-EmmaMultilingualNeural", "en-US-AvaMultilingualNeural",
    ],
    "English (UK)": ["en-GB-RyanNeural", "en-GB-SoniaNeural", "en-GB-ThomasNeural"],
    "English (AU/CA/IE/IN)": [
        "en-AU-NatashaNeural", "en-AU-WilliamNeural", "en-CA-LiamNeural",
        "en-IE-EmilyNeural", "en-IN-NeerjaNeural", "en-IN-PrabhatNeural",
    ],
    "Mandarin (Mainland)": [
        "zh-CN-XiaoxiaoNeural",   # female, warm (good default)
        "zh-CN-YunxiNeural",      # male
        "zh-CN-YunyangNeural",    # male, news
        "zh-CN-XiaoyiNeural",     # female
        "zh-CN-YunjianNeural",    # male
        "zh-CN-liaoning-XiaobeiNeural",  # female, NE dialect
    ],
    "Mandarin (Taiwan)": [
        "zh-TW-HsiaoChenNeural",  # female
        "zh-TW-YunJheNeural",     # male
        "zh-TW-HsiaoYuNeural",    # female
    ],
    "Cantonese (Hong Kong)": [
        "zh-HK-HiuMaanNeural",    # female (good default)
        "zh-HK-WanLungNeural",    # male
        "zh-HK-HiuGaaiNeural",    # female
    ],
}

# Sensible default voice per language group (used when auto-suggesting).
DEFAULT_VOICE_FOR_LANG = {
    "Mandarin (Mainland)": "zh-CN-XiaoxiaoNeural",
    "Mandarin (Taiwan)": "zh-TW-HsiaoChenNeural",
    "Cantonese (Hong Kong)": "zh-HK-HiuMaanNeural",
}

POPULAR_VOICES = [v for group in VOICES_BY_LANG.values() for v in group]

SUBS_SUFFIX = ".subs.json"            # sidecar timing file: "<mp3-stem>.subs.json"


# ── Text extraction ───────────────────────────────────────────────────────────

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


def first_heading(html_bytes: bytes) -> str:
    """First h1..h3 heading text in a document, or '' if none — title fallback."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup.find_all(["h1", "h2", "h3"]):
        txt = re.sub(r"\s+", " ", tag.get_text(" ")).strip()
        if txt:
            return txt
    return ""


# ── Language detection (CJK = Chinese for Mandarin/Cantonese) ──────────────────

def _is_cjk_char(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or      # CJK Unified Ideographs
            0x3400 <= o <= 0x4DBF or      # Extension A
            0xF900 <= o <= 0xFAFF or      # Compatibility Ideographs
            0x3000 <= o <= 0x303F)        # CJK symbols & punctuation (。！？「」…)


def cjk_fraction(text: str) -> float:
    """Fraction of the 'meaningful' characters that are CJK ideographs."""
    cjk = letters = 0
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF:
            cjk += 1; letters += 1
        elif ch.isalpha():
            letters += 1
    return cjk / letters if letters else 0.0


def is_cjk_text(text: str) -> bool:
    """True when the text is predominantly Chinese (Mandarin/Cantonese)."""
    return cjk_fraction(text) > 0.2


def chars_per_min(text: str) -> int:
    """Speech-rate heuristic (chars/min) appropriate to the text's language."""
    return CJK_CHARS_PER_MIN if is_cjk_text(text) else CHARS_PER_MIN


# ── Sentence / chunk splitting (Latin + CJK) ──────────────────────────────────

# Split AFTER full-width CJK terminators (no following space needed) OR after
# Latin .!? that is followed by whitespace.
_SENTENCE_RE = re.compile(r"(?<=[。！？；…])|(?<=[.!?])\s+")


def split_sentences(text: str) -> list:
    """Split text into sentences. Handles English (.!? + space) and Chinese
    (。！？；… with no spaces between sentences)."""
    return [s for s in (p.strip() for p in _SENTENCE_RE.split(text)) if s and s.strip()]


def _hard_chunks(s: str, max_chars: int) -> list:
    """Break an over-long sentence: at spaces if it has them (Latin), otherwise
    at fixed character boundaries (CJK has no spaces)."""
    if " " in s:
        out, cur = [], ""
        for word in s.split():
            if len(cur) + len(word) + 1 > max_chars and cur:
                out.append(cur.strip()); cur = word + " "
            else:
                cur += word + " "
        if cur.strip():
            out.append(cur.strip())
        return out
    return [s[i:i + max_chars] for i in range(0, len(s), max_chars)]


def split_text(text: str, max_chars: int) -> list:
    """Split text into <=max_chars pieces, always at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    sentences = split_sentences(text)
    pieces, current = [], ""
    # CJK sentences run together with no separators; Latin gets a joining space.
    join = "" if is_cjk_text(text) else " "

    for sentence in sentences:
        if len(sentence) > max_chars:           # one very long sentence
            if current.strip():
                pieces.append(current.strip()); current = ""
            pieces.extend(_hard_chunks(sentence, max_chars))
            continue

        if len(current) + len(sentence) + 1 > max_chars:
            pieces.append(current.strip())
            current = sentence + join
        else:
            current += sentence + join

    if current.strip():
        pieces.append(current.strip())
    return [p for p in pieces if p]


# ── Table-of-contents helpers ─────────────────────────────────────────────────

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
    """basename -> title; first occurrence wins (the primary chapter title)."""
    seen = {}
    for title, href in toc_entries:
        if href not in seen:
            seen[href] = title
    return seen


# ── Filename helpers ──────────────────────────────────────────────────────────

def safe_filename(s: str, max_len: int = 120) -> str:
    """
    Make a readable, cross-platform-safe filename: keeps spaces and ordinary
    punctuation, only stripping characters that are actually illegal.
    """
    s = unicodedata.normalize("NFC", s)
    s = s.replace("�", "").replace("­", "")
    s = s.replace(":", " -").replace("/", "-").replace("\\", "-")
    s = re.sub(r'[<>"|?*\x00-\x1f]', "", s)        # remaining illegal chars
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ")                              # Windows: no trailing dot/space
    return s[:max_len].strip() or "Untitled"


def _looks_like_junk(title: str, basename: str, char_count: int,
                     min_chars: int) -> bool:
    """Heuristic: front/back matter the user probably doesn't want narrated."""
    hay = f"{title} {basename}".lower()
    if any(pat in hay for pat in JUNK_PATTERNS):
        return True
    # Very short sections just over the min-chars bar are usually matter pages.
    if char_count < max(min_chars * 2, 600):
        return True
    return False


# ── Chapter assembly ──────────────────────────────────────────────────────────

def gather_chapters(book, min_chars: int) -> list:
    """
    Walk the spine in reading order ->
        [{index, title, text, file_name, junk}]

    Title resolution order: TOC title -> first in-document heading -> "Section N".
    `junk` flags likely front/back matter (cover, copyright, index, tiny pages).
    """
    title_map = build_title_map(flatten_toc(book.toc))

    chapters, chapter_num = [], 0
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if not item or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue

        content = item.get_content()
        text = extract_text(content)
        if len(text) < min_chars:
            continue

        chapter_num += 1
        basename = Path(item.file_name).name
        title = (title_map.get(basename)
                 or first_heading(content)
                 or f"Section {chapter_num}")
        chapters.append({
            "index": chapter_num,
            "title": title,
            "text": text,
            "file_name": basename,
            "junk": _looks_like_junk(title, basename, len(text), min_chars),
        })
    return chapters


# ── Rate normalisation ────────────────────────────────────────────────────────

def normalize_rate(rate) -> str:
    """
    Accept an int slider value (-50..50) or a string ('-15%', '15', '+20%')
    and return a valid edge-tts rate string like '+0%'.
    """
    if isinstance(rate, (int, float)):
        return f"{int(rate):+d}%"
    rate = str(rate).strip()
    if not rate:
        return "+0%"
    if not rate.endswith("%"):
        rate += "%"
    if rate[0] not in "+-":
        rate = "+" + rate
    return rate


# ── edge-tts synthesis ────────────────────────────────────────────────────────

async def _synth_save(text: str, path: str, voice: str, rate: str):
    """Plain synthesis (no timing) — used by the no-subs path and previews."""
    await edge_tts.Communicate(text, voice=voice, rate=rate).save(path)


async def _synth_stream(text: str, path: str, voice: str, rate: str):
    """
    Stream synthesis, writing audio to `path` and collecting boundary events.
    Returns [(offset_ms, duration_ms, text), ...] in spoken order.

    edge-tts emits either "SentenceBoundary" (the default in recent versions)
    or "WordBoundary" chunks; we capture both. Either granularity aggregates up
    to sentence timing in build_sentence_timing(). Offsets/durations are in
    100-nanosecond "ticks" (1 ms = 10_000 ticks).
    """
    events = []
    with open(path, "wb") as fh:
        async for chunk in edge_tts.Communicate(text, voice=voice, rate=rate).stream():
            ctype = chunk.get("type")
            if ctype == "audio":
                fh.write(chunk["data"])
            elif ctype in ("SentenceBoundary", "WordBoundary"):
                events.append((chunk["offset"] // 10_000,
                               chunk["duration"] // 10_000,
                               chunk.get("text", "")))
    return events


def _retrying(coro_factory, path):
    """Run an async synth coroutine with the V1 retry + empty-file contract."""
    last = None
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            result = asyncio.run(coro_factory())
            if os.path.getsize(path) == 0:
                raise RuntimeError("edge-tts wrote an empty file")
            return result
        except Exception as exc:                 # transient network errors, etc.
            last = exc
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY * attempt)
    raise RuntimeError(f"edge-tts failed after {RETRY_LIMIT} attempts: {last}")


def tts_to_file(text: str, path: str, voice: str, rate: str):
    """Synthesise `text` to one MP3 (no timing), retrying on transient errors."""
    _retrying(lambda: _synth_save(text, path, voice, rate), path)


def tts_to_file_timed(text: str, path: str, voice: str, rate: str) -> list:
    """Synthesise `text` to one MP3 and return WordBoundary events (with retry)."""
    return _retrying(lambda: _synth_stream(text, path, voice, rate), path)


# ── Sentence timing ───────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Lower-case, keep only alphanumerics — for tolerant word matching."""
    return re.sub(r"[^0-9a-z]+", "", s.lower())


def build_sentence_timing(sentences: list, word_events: list) -> list:
    """
    Aggregate per-word boundary events up to per-sentence spans.

    Greedy text alignment: for each sentence, consume word events (in order),
    accumulating their normalised text, until it covers the sentence's
    normalised text. start_ms = first consumed event's offset; end_ms = last
    consumed event's offset+duration. Tolerant of minor word/punct mismatches,
    which are invisible at sentence granularity.

    Returns [{"start_ms", "end_ms", "text"}] aligned 1:1 with `sentences`.
    """
    out = []
    ei = 0
    n = len(word_events)
    last_end = 0
    for sent in sentences:
        target = _norm(sent)
        if not target:
            out.append({"start_ms": last_end, "end_ms": last_end, "text": sent})
            continue

        start_ms = None
        acc = ""
        consumed_end = last_end
        while ei < n and len(acc) < len(target):
            off, dur, wtext = word_events[ei]
            if start_ms is None:
                start_ms = off
            acc += _norm(wtext)
            consumed_end = max(consumed_end, off + dur)
            ei += 1

        if start_ms is None:                     # ran out of events — estimate
            start_ms = last_end
        out.append({"start_ms": int(start_ms),
                    "end_ms": int(consumed_end),
                    "text": sent})
        last_end = consumed_end
    return out


def subs_path_for(mp3_path: Path) -> Path:
    """'Ch.1 - Foo.mp3' -> 'Ch.1 - Foo.subs.json' (sidecar next to the MP3)."""
    return mp3_path.with_suffix("").with_name(mp3_path.stem + SUBS_SUFFIX)


def _write_subs(subs_file: Path, mp3_name: str, voice: str, rate: str,
                sentence_timings: list):
    """Atomically write the sidecar timing JSON."""
    payload = {
        "version": 2,
        "mp3": mp3_name,
        "voice": voice,
        "rate": rate,
        "sentences": sentence_timings,
    }
    staging = subs_file.with_suffix(subs_file.suffix + ".tmp")
    staging.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    os.replace(staging, subs_file)


def load_subs(mp3_path) -> dict:
    """Read a sidecar timing file, or {} if missing/unreadable."""
    p = subs_path_for(Path(mp3_path))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ── Chapter synthesis (crash-safe, atomic, resumable; optional subs) ──────────

def chapter_part_paths(chapter: dict, out_dir: Path, fmt: str,
                       part_chars: int) -> list:
    """The MP3 paths this chapter will produce (without synthesising)."""
    idx = chapter["index"]
    safe_ttl = safe_filename(chapter["title"])
    parts = split_text(chapter["text"], part_chars)
    multi = len(parts) > 1
    paths = []
    for pi in range(1, len(parts) + 1):
        suffix = f" - Part {pi}" if multi else ""
        paths.append(out_dir / (fmt.format(idx=idx) + f" - {safe_ttl}{suffix}.mp3"))
    return paths


def synthesize_chapter(chapter: dict, out_dir: Path, fmt: str, part_chars: int,
                       voice: str, rate: str, announce: bool, subs: bool = True,
                       on_part=None, should_cancel=None) -> list:
    """
    Convert one chapter into one or more MP3s (+ optional .subs.json sidecars):
      * short chapter  -> 'Ch.N - Title.mp3'
      * long chapter   -> 'Ch.N - Title - Part 1.mp3', ' - Part 2.mp3', ...

    Crash-safe: each file is synthesised to a '.part' staging file then
    atomically renamed, and finished (non-empty) files are reused on re-run.

    `on_part(mp3_path, cached: bool)` is called after each part (for progress).
    `should_cancel()` -> True aborts early (returns what's done so far).
    """
    idx      = chapter["index"]
    safe_ttl = safe_filename(chapter["title"])
    parts    = split_text(chapter["text"], part_chars)
    multi    = len(parts) > 1

    out_paths = []
    for pi, part_text in enumerate(parts, start=1):
        if should_cancel and should_cancel():
            break

        suffix  = f" - Part {pi}" if multi else ""
        out_mp3 = out_dir / (fmt.format(idx=idx) + f" - {safe_ttl}{suffix}.mp3")
        out_paths.append(out_mp3)
        subs_file = subs_path_for(out_mp3)

        if out_mp3.exists() and out_mp3.stat().st_size > 0:
            # Cached MP3. Backfill a missing sidecar only if cheap is impossible
            # (it needs a re-synth), so just leave it; the player falls back.
            if on_part:
                on_part(out_mp3, True)
            continue

        # Build the spoken text (optional chapter announcement) and the matching
        # sentence list so timing aligns with exactly what is read aloud.
        spoken = part_text
        if announce:
            head = f"Chapter {idx}. {chapter['title']}."
            if multi:
                head = f"Chapter {idx}. {chapter['title']}. Part {pi}."
            spoken = head + "\n" + part_text

        staging = out_mp3.with_suffix(".part")
        if subs:
            sentences = split_sentences(spoken)
            events = tts_to_file_timed(spoken, str(staging), voice, rate)
            timings = build_sentence_timing(sentences, events)
        else:
            tts_to_file(spoken, str(staging), voice, rate)
            timings = None

        os.replace(staging, out_mp3)            # atomic — never a half file
        if timings is not None:
            _write_subs(subs_file, out_mp3.name, voice, rate, timings)

        if on_part:
            on_part(out_mp3, False)

    return out_paths


# ── Output packaging: cover, ID3 tags, single .m4b ────────────────────────────

def extract_cover(book) -> bytes:
    """Return the EPUB cover image bytes, or b'' if none can be found."""
    # 1) explicit cover metadata -> item id
    try:
        meta = book.get_metadata("OPF", "cover")
        if meta:
            cid = meta[0][1].get("content")
            item = book.get_item_with_id(cid)
            if item:
                return item.get_content()
    except Exception:
        pass
    # 2) any image item whose name/id mentions "cover"
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        name = (item.get_name() or "").lower()
        if "cover" in name or "cover" in (item.get_id() or "").lower():
            return item.get_content()
    # 3) fall back to the first image
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        return item.get_content()
    return b""


def write_tags(mp3_path: Path, album: str, author: str, track: int,
               title: str, cover_bytes: bytes = b"") -> bool:
    """Write ID3 tags (+ optional cover) onto one MP3. No-op without mutagen."""
    if not HAVE_MUTAGEN:
        return False
    from mutagen.id3 import ID3, TIT2, TALB, TPE1, TRCK, APIC, error
    try:
        try:
            tags = ID3(str(mp3_path))
        except error:
            tags = ID3()
        tags.setall("TIT2", [TIT2(encoding=3, text=title)])
        tags.setall("TALB", [TALB(encoding=3, text=album)])
        tags.setall("TPE1", [TPE1(encoding=3, text=author)])
        tags.setall("TRCK", [TRCK(encoding=3, text=str(track))])
        if cover_bytes:
            mime = "image/png" if cover_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            tags.setall("APIC", [APIC(encoding=3, mime=mime, type=3,
                                      desc="Cover", data=cover_bytes)])
        tags.save(str(mp3_path), v2_version=3)
        return True
    except Exception:
        return False


# ── ffmpeg discovery (robust against ffmpeg not being on PATH) ─────────────────
#
# A just-installed ffmpeg (e.g. `winget install Gyan.FFmpeg`) is invisible to an
# already-running process until it restarts, and some installers never touch
# PATH at all. So that the app works out-of-the-box on other computers we look
# in the usual install locations as well as on PATH and — when ffmpeg is found
# off-PATH — prepend its directory to *this* process's PATH, so the bare
# "ffmpeg"/"ffprobe" subprocess calls elsewhere keep working unchanged.

_FFMPEG_DIR = None          # cached: directory holding ffmpeg+ffprobe, or ""


def _candidate_ffmpeg_dirs():
    """Yield directories that may contain ffmpeg/ffprobe, best guess first."""
    # 1. Explicit override — set FFMPEG_DIR to force a specific install.
    env_dir = os.environ.get("FFMPEG_DIR") or os.environ.get("FFMPEG_HOME")
    if env_dir:
        yield Path(env_dir)
        yield Path(env_dir) / "bin"

    # 2. Bundled alongside the app (drop ffmpeg here to ship it with the app).
    here = Path(__file__).resolve().parent
    yield here
    yield here / "bin"
    yield here / "ffmpeg"
    yield here / "ffmpeg" / "bin"

    # 3. Common per-OS install locations.
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        programdata = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        userprofile = Path(os.environ.get("USERPROFILE", ""))
        yield local / "Microsoft" / "WinGet" / "Links"   # winget shim dir
        wg = local / "Microsoft" / "WinGet" / "Packages"  # winget payloads
        if wg.is_dir():
            try:
                yield from wg.glob("*FFmpeg*/**/bin")
            except OSError:
                pass
        yield programdata / "chocolatey" / "bin"
        yield userprofile / "scoop" / "shims"
        yield pf / "ffmpeg" / "bin"
        yield Path(r"C:\ffmpeg\bin")
    else:
        for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/snap/bin"):
            yield Path(d)


def _locate_ffmpeg() -> str:
    """Directory containing both ffmpeg & ffprobe ("" if not found); cached.

    On success, guarantees that directory is on this process's PATH.
    """
    global _FFMPEG_DIR
    if _FFMPEG_DIR is not None:
        return _FFMPEG_DIR

    # PATH first — cheapest, and respects an explicit user setup.
    on_path = shutil.which("ffmpeg")
    if on_path and shutil.which("ffprobe"):
        _FFMPEG_DIR = str(Path(on_path).resolve().parent)
        return _FFMPEG_DIR

    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    probe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    for d in _candidate_ffmpeg_dirs():
        try:
            if (d / exe).is_file() and (d / probe).is_file():
                resolved = str(d.resolve())
                os.environ["PATH"] = resolved + os.pathsep + os.environ.get("PATH", "")
                _FFMPEG_DIR = resolved
                return _FFMPEG_DIR
        except OSError:
            continue

    _FFMPEG_DIR = ""
    return _FFMPEG_DIR


def have_ffmpeg() -> bool:
    return bool(_locate_ffmpeg())


def ffmpeg_install_hint() -> str:
    """OS-appropriate instructions for installing ffmpeg."""
    if os.name == "nt":
        return ("Install it, then fully quit and reopen this app:\n"
                "    winget install Gyan.FFmpeg\n"
                "(or download from https://www.gyan.dev/ffmpeg/builds/ and add "
                "its bin\\ folder to PATH).")
    if sys.platform == "darwin":
        return ("Install it, then restart this app:\n"
                "    brew install ffmpeg")
    return ("Install it, then restart this app:\n"
            "    sudo apt install ffmpeg     # Debian/Ubuntu\n"
            "    sudo dnf install ffmpeg     # Fedora")


def _ffprobe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def combine_to_m4b(mp3_paths: list, out_m4b: Path, title: str, author: str,
                   chapter_titles: list = None, cover_bytes: bytes = b"") -> Path:
    """
    Concatenate MP3s into a single .m4b with chapter markers + metadata + cover.
    Requires ffmpeg/ffprobe on PATH; raises RuntimeError otherwise.
    """
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg/ffprobe not found — cannot build .m4b.\n"
                           + ffmpeg_install_hint())
    mp3_paths = [Path(p) for p in mp3_paths if Path(p).exists()]
    if not mp3_paths:
        raise RuntimeError("no MP3 files to combine")
    if chapter_titles is None:
        chapter_titles = [p.stem for p in mp3_paths]

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        # concat list
        concat = td / "concat.txt"
        concat.write_text(
            "".join(f"file '{p.resolve()}'\n" for p in mp3_paths),
            encoding="utf-8")

        # FFMETADATA with chapter markers (offsets in ms)
        lines = [";FFMETADATA1", f"title={title}", f"artist={author}",
                 f"album={title}"]
        start_ms = 0
        for p, ctitle in zip(mp3_paths, chapter_titles):
            dur_ms = int(_ffprobe_seconds(p) * 1000)
            end_ms = start_ms + max(dur_ms, 1)
            lines += ["[CHAPTER]", "TIMEBASE=1/1000",
                      f"START={start_ms}", f"END={end_ms}",
                      f"title={ctitle}"]
            start_ms = end_ms
        meta = td / "meta.txt"
        meta.write_text("\n".join(lines) + "\n", encoding="utf-8")

        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
               "-i", str(meta)]
        cover = b""
        if cover_bytes:
            cover_file = td / "cover.img"
            cover_file.write_bytes(cover_bytes)
            cmd += ["-i", str(cover_file)]
            cover = cover_file

        cmd += ["-map_metadata", "1"]
        if cover:
            # Embed the cover as an mjpeg "attached_pic"; the .m4b (ipod/mov)
            # container rejects the default h264 re-encode ffmpeg would pick.
            cmd += ["-map", "0:a", "-map", "2:v",
                    "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
        else:
            cmd += ["-map", "0:a"]
        cmd += ["-c:a", "aac", "-b:a", "96k", str(out_m4b)]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            try:
                out_m4b.unlink()                 # drop the 0-byte failed output
            except OSError:
                pass
            raise RuntimeError(f"ffmpeg failed:\n{res.stderr[-1500:]}")
    return out_m4b


# ── Book loading convenience ──────────────────────────────────────────────────

def load_book(path):
    """Read an EPUB and return (book, title, author)."""
    book = epub.read_epub(str(path))
    title = book.title or Path(path).stem
    authors = book.get_metadata("DC", "creator")
    author = authors[0][0] if authors else "Unknown"
    return book, title, author


def estimate_words_minutes(chapters: list):
    """
    (count, minutes, unit) for a chapter list.
      * English: count = words,      minutes ≈ words / 150,  unit = "words"
      * Chinese: count = characters, minutes ≈ chars / 300,  unit = "characters"
    (Returns a 3-tuple; older callers that unpack two values should take [:2].)
    """
    all_text = "".join(c["text"] for c in chapters)
    if is_cjk_text(all_text):
        chars = sum(len(re.sub(r"\s+", "", c["text"])) for c in chapters)
        return chars, chars // CJK_CHARS_PER_MIN, "characters"
    words = sum(len(c["text"].split()) for c in chapters)
    return words, words // 150, "words"
