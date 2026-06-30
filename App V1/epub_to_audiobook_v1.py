"""
epub_to_audiobook.py — Generic EPUB → MP3 Audiobook Converter
=============================================================
Turns ANY .epub into a single folder of MP3 files that read the book's
content word-for-word. Chapters are auto-detected from the table of
contents and named nicely:

    Ch.1 - Foreword by John B. Collison.mp3
    Ch.6 - Chapter 1 - A Portrait of Charles T. Munger - Part 1.mp3
    Ch.6 - Chapter 1 - A Portrait of Charles T. Munger - Part 2.mp3
    ...

Short chapters become one file. Long chapters are split into evenly sized
"Part" files (default ~12 minutes each) so they're easy to navigate.

Uses Microsoft Edge's text-to-speech (edge-tts) — free, no API key, natural
voices, and (unlike gTTS) no aggressive rate-limiting, so it handles full
13-hour books in one go.

SETUP (one-time)
----------------
    pip install edge-tts ebooklib beautifulsoup4

USAGE
-----
    python epub_to_audiobook.py path/to/book.epub

COMMON OPTIONS
--------------
    --list             Show detected chapters and exit (dry run)
    --skip 1,21,22     Skip chapters by number (e.g. copyright page, endnotes)
    --part-minutes 12  Target length of each Part file (default: 12 min)
    --voice NAME       Edge voice (default: en-US-GuyNeural). See voices below.
    --rate +0%         Speech rate, e.g. -15% slower, +20% faster
    --slow             Shortcut for --rate -25%
    --announce         Speak "Chapter N. Title. Part K." at the start of each file
    --workers 4        Parallel chapter workers (default: 4)
    --min-chars 300    Min characters for a spine item to count as a chapter
    --pad              Zero-pad chapter numbers (Ch.01) instead of (Ch.1)

PICK A VOICE
------------
    edge-tts --list-voices            # full list
Popular English voices:
    en-US-GuyNeural      (male, US, default)     en-US-AriaNeural   (female, US)
    en-US-JennyNeural    (female, US)            en-GB-RyanNeural   (male, UK)
    en-GB-SoniaNeural    (female, UK)            en-AU-NatashaNeural(female, AU)

EXAMPLES
--------
    python epub_to_audiobook.py book.epub --list
    python epub_to_audiobook.py book.epub --voice en-GB-SoniaNeural --announce
    python epub_to_audiobook.py book.epub --skip 1,21 --part-minutes 20

CRASH-SAFE
----------
Every finished file is written atomically and reused on the next run, so if
the process is interrupted (Ctrl+C, network drop, machine sleep) just run the
same command again — it resumes exactly where it left off and never leaves a
0-byte file behind.
"""

import argparse
import asyncio
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


# ── Dependency check ──────────────────────────────────────────────────────────

def check_deps():
    missing = []
    for pkg, imp in [("edge-tts", "edge_tts"),
                     ("ebooklib", "ebooklib"),
                     ("beautifulsoup4", "bs4")]:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"\n[X] Missing packages: {', '.join(missing)}")
        print(f"    Run:  pip install {' '.join(missing)}\n")
        sys.exit(1)

check_deps()

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import edge_tts


# ── Constants ─────────────────────────────────────────────────────────────────

CHARS_PER_MIN  = 900    # rough chars/min of speech (~150 wpm * ~6 chars/word)
DEFAULT_VOICE  = "en-US-GuyNeural"
RETRY_LIMIT    = 4
RETRY_DELAY    = 3      # seconds between retries (multiplied by attempt #)

DROP_TAGS = {"script", "style", "head", "figure", "figcaption",
             "img", "svg", "aside", "nav", "footer"}
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
              "li", "tr", "br", "blockquote", "section", "article"}


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
    text = text.replace("­", "")     # soft hyphen
    text = text.replace("�", "")     # decode-failure replacement char (the "?" boxes)
    text = re.sub(r"https?://\S+", "", text)       # URLs
    text = re.sub(r"(?m)^\s*\d+\s*$", "", text)    # lone page/footnote numbers
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
        if len(sentence) > max_chars:           # one very long sentence
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
    'Chapter 1: A Portrait' -> 'Chapter 1 - A Portrait'; drops a trailing '?'.
    """
    s = unicodedata.normalize("NFC", s)
    s = s.replace("�", "").replace("­", "")
    s = s.replace(":", " -").replace("/", "-").replace("\\", "-")
    s = re.sub(r'[<>"|?*\x00-\x1f]', "", s)        # remaining illegal chars
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ")                              # Windows: no trailing dot/space
    return s[:max_len].strip() or "Untitled"


# ── Chapter assembly ──────────────────────────────────────────────────────────

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
        chapters.append({
            "index": chapter_num,
            "title": title,
            "text": text,
            "file_name": basename,
        })
    return chapters


# ── edge-tts synthesis ────────────────────────────────────────────────────────

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


def synthesize_chapter(chapter: dict, out_dir: Path, fmt: str, part_chars: int,
                       voice: str, rate: str, announce: bool) -> list:
    """
    Convert one chapter into one or more MP3s:
      * short chapter  -> 'Ch.N - Title.mp3'
      * long chapter   -> 'Ch.N - Title - Part 1.mp3', ' - Part 2.mp3', ...
    Each Part is a single edge-tts call. Finished files are cached/resumed.
    """
    idx      = chapter["index"]
    safe_ttl = safe_filename(chapter["title"])
    parts    = split_text(chapter["text"], part_chars)
    multi    = len(parts) > 1

    out_paths = []
    for pi, part_text in enumerate(parts, start=1):
        suffix  = f" - Part {pi}" if multi else ""
        out_mp3 = out_dir / (fmt.format(idx=idx) + f" - {safe_ttl}{suffix}.mp3")
        out_paths.append(out_mp3)

        if out_mp3.exists() and out_mp3.stat().st_size > 0:
            continue                            # cached / resumed

        spoken = part_text
        if announce:
            head = f"Chapter {idx}. {chapter['title']}."
            if multi:
                head = f"Chapter {idx}. {chapter['title']}. Part {pi}."
            spoken = head + "\n" + part_text

        staging = out_mp3.with_suffix(".part")
        tts_to_file(spoken, str(staging), voice=voice, rate=rate)
        os.replace(staging, out_mp3)            # atomic — never a half file

    return out_paths


# ── CLI ───────────────────────────────────────────────────────────────────────

def normalize_rate(rate: str) -> str:
    """Accept '-15%', '15', '+20%' etc. and return a valid edge-tts rate."""
    rate = rate.strip()
    if not rate:
        return "+0%"
    if not rate.endswith("%"):
        rate += "%"
    if rate[0] not in "+-":
        rate = "+" + rate
    return rate


def main():
    parser = argparse.ArgumentParser(
        description="Convert any EPUB into a folder of nicely-named MP3 chapters.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("epub", help="Path to the EPUB file")
    parser.add_argument("--out", default=None,
                        help="Output folder (default: named after the book title)")
    parser.add_argument("--voice", default=DEFAULT_VOICE,
                        help=f"Edge TTS voice (default: {DEFAULT_VOICE})")
    parser.add_argument("--rate", default="+0%",
                        help="Speech rate, e.g. -15%% slower, +20%% faster")
    parser.add_argument("--slow", action="store_true",
                        help="Shortcut for --rate -25%%")
    parser.add_argument("--announce", action="store_true",
                        help="Speak the chapter title at the start of each file")
    parser.add_argument("--part-minutes", type=float, default=12.0,
                        help="Target minutes per Part file (default: 12)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel chapter workers (default: 4)")
    parser.add_argument("--skip", default="",
                        help="Chapter numbers to skip, e.g. --skip 1,21,22")
    parser.add_argument("--min-chars", type=int, default=300,
                        help="Min characters to count a spine item as a chapter")
    parser.add_argument("--pad", action="store_true",
                        help="Zero-pad chapter numbers (Ch.01 vs Ch.1)")
    parser.add_argument("--list", action="store_true",
                        help="List detected chapters and exit (dry run)")
    args = parser.parse_args()

    epub_path = Path(args.epub)
    if not epub_path.exists():
        print(f"\n[X] File not found: {epub_path}\n")
        sys.exit(1)

    skip_set = set()
    if args.skip:
        try:
            skip_set = {int(x.strip()) for x in args.skip.split(",")}
        except ValueError:
            print("[X] --skip must be comma-separated integers, e.g. --skip 1,2")
            sys.exit(1)

    rate = "-25%" if args.slow else normalize_rate(args.rate)
    part_chars = max(1000, int(args.part_minutes * CHARS_PER_MIN))
    fmt = "Ch.{idx:02d}" if args.pad else "Ch.{idx}"

    print(f"\n[*] Loading: {epub_path.name}")
    book = epub.read_epub(str(epub_path))
    book_title = book.title or epub_path.stem
    authors = book.get_metadata("DC", "creator")
    author_str = authors[0][0] if authors else "Unknown"
    print(f"    Title : {book_title}")
    print(f"    Author: {author_str}")

    out_dir = Path(args.out) if args.out else Path(safe_filename(book_title))
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"    Output: {out_dir}/")
    print(f"    Voice : {args.voice}   Rate: {rate}\n")

    chapters = gather_chapters(book, min_chars=args.min_chars)
    if skip_set:
        chapters = [c for c in chapters if c["index"] not in skip_set]
    if not chapters:
        print("[X] No chapters found. Try --min-chars 100 to lower the threshold.")
        sys.exit(1)

    total_words = sum(len(c["text"].split()) for c in chapters)
    est_minutes = total_words // 150
    print(f"[*] {len(chapters)} chapters  -  ~{total_words:,} words  "
          f"-  ~{est_minutes // 60}h {est_minutes % 60}m estimated"
          f"  -  ~{args.part_minutes:g} min/part\n")
    for ch in chapters:
        wc = len(ch["text"].split())
        n_parts = len(split_text(ch["text"], part_chars))
        tag = f"{n_parts} parts" if n_parts > 1 else "1 file"
        ttl = safe_filename(ch["title"])
        print(f"    {ch['index']:>2}  {ttl[:58]:<58}  ~{wc:>6,} w  ({tag})")
    print()

    if args.list:
        print("(dry run — nothing synthesised)\n")
        return

    print("[*] Synthesising...  (safe to Ctrl+C and re-run; it resumes)\n")
    failed = []

    def do_chapter(chapter):
        label = f"[{chapter['index']:>2}] {safe_filename(chapter['title'])[:46]}"
        try:
            t0 = time.time()
            paths = synthesize_chapter(chapter, out_dir, fmt, part_chars,
                                       voice=args.voice, rate=rate,
                                       announce=args.announce)
            cached = " (cached)" if time.time() - t0 < 0.5 else ""
            print(f"  OK  {label}  ->  {len(paths)} file(s){cached}")
            return chapter["index"], paths, None
        except Exception as exc:
            print(f"  XX  {label}  FAILED: {exc}")
            return chapter["index"], [], str(exc)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(do_chapter, ch): ch for ch in chapters}
        for future in as_completed(futures):
            idx, paths, err = future.result()
            if err:
                failed.append(idx)

    print(f"\n{'-' * 60}")
    done = len(chapters) - len(failed)
    print(f"[OK] {done}/{len(chapters)} chapters complete.")
    print(f"     Folder: {out_dir.resolve()}/")
    if failed:
        print("\n[!] Failed chapters (re-run the SAME command to retry — "
              "finished files are cached):")
        for idx in sorted(failed):
            print(f"     - chapter {idx}")
    print()


if __name__ == "__main__":
    main()
