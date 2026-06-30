"""
epub_to_audiobook_v3.py — EPUB → MP3 Audiobook Converter (command line, V3)
===========================================================================
Same command-line tool as V2, but built on the V3 engine so the read-along
sentence timing it writes is taken straight from edge-tts's own boundaries
(accurate; no late-track drift). All V2 flags are unchanged:

    --subs / --no-subs   Write sidecar sentence-timing JSON (default ON)
    --skip-junk          Drop chapters auto-flagged as front/back matter
    --tag                Write ID3 tags + cover onto each MP3 (mutagen)
    --m4b                Combine into one <Book>.m4b (needs ffmpeg)
    --preview-voice TXT  Speak a short sample with --voice and exit
    ...plus --voice/--rate/--announce/--part-minutes/--workers/--skip/
            --min-chars/--pad/--out/--list

SETUP
-----
    pip install edge-tts ebooklib beautifulsoup4 pygame
    pip install mutagen            # optional: --tag
    # ffmpeg on PATH               # optional: --m4b and read-along speed control
"""

import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import importlib.util as _ilu

_missing = [pkg for pkg, imp in [("edge-tts", "edge_tts"),
                                 ("ebooklib", "ebooklib"),
                                 ("beautifulsoup4", "bs4")]
            if _ilu.find_spec(imp) is None]
if _missing:
    print(f"\n[X] Missing packages: {', '.join(_missing)}")
    print(f"    Run:  pip install {' '.join(_missing)}\n")
    sys.exit(1)

import argparse
import re
import time

import audiobook_engine_v5 as eng


def _preview_voice(text, voice, rate):
    import tempfile
    import os
    tmp = Path(tempfile.gettempdir()) / "epub_voice_preview.mp3"
    print(f"[*] Synthesising sample with {voice} ({rate}) …")
    eng.tts_to_file(text, str(tmp), voice=voice, rate=rate)
    try:
        import pygame
        pygame.mixer.init()
        pygame.mixer.music.load(str(tmp))
        pygame.mixer.music.play()
        print("[*] Playing… (Ctrl+C to stop)")
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    except Exception:
        print(f"[i] Saved sample to {tmp} (install pygame to auto-play).")
        try:
            os.startfile(tmp)
        except AttributeError:
            import subprocess
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(tmp)])


def main():
    parser = argparse.ArgumentParser(
        description="Convert any EPUB into nicely-named MP3 chapters (V3 engine).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("epub", nargs="?", help="Path to the EPUB file")
    parser.add_argument("--out", default=None)
    parser.add_argument("--voice", default=eng.DEFAULT_VOICE)
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--slow", action="store_true")
    parser.add_argument("--announce", action="store_true")
    parser.add_argument("--part-minutes", type=float, default=12.0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip", default="")
    parser.add_argument("--skip-junk", action="store_true")
    parser.add_argument("--min-chars", type=int, default=300)
    parser.add_argument("--pad", action="store_true")
    parser.add_argument("--no-subs", dest="subs", action="store_false")
    parser.add_argument("--tag", action="store_true")
    parser.add_argument("--m4b", action="store_true")
    parser.add_argument("--preview-voice", nargs="?", const=
                        "Hello. This is a sample of how this voice will read your book.",
                        default=None, metavar="TEXT")
    parser.add_argument("--list", action="store_true")
    parser.set_defaults(subs=True)
    args = parser.parse_args()

    rate = "-25%" if args.slow else eng.normalize_rate(args.rate)

    if args.preview_voice is not None:
        _preview_voice(args.preview_voice, args.voice, rate)
        return
    if not args.epub:
        parser.error("the EPUB path is required (or use --preview-voice)")

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

    fmt = "Ch.{idx:02d}" if args.pad else "Ch.{idx}"

    print(f"\n[*] Loading: {epub_path.name}")
    book, book_title, author_str = eng.load_book(epub_path)
    print(f"    Title : {book_title}")
    print(f"    Author: {author_str}")

    out_dir = Path(args.out) if args.out else (
        epub_path.parent / eng.safe_filename(book_title))
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"    Output: {out_dir}/")
    print(f"    Voice : {args.voice}   Rate: {rate}   "
          f"Subs: {'on' if args.subs else 'off'}\n")

    chapters = eng.gather_chapters(book, min_chars=args.min_chars)
    if skip_set:
        chapters = [c for c in chapters if c["index"] not in skip_set]
    if args.skip_junk:
        dropped = [c["index"] for c in chapters if c["junk"]]
        chapters = [c for c in chapters if not c["junk"]]
        if dropped:
            print(f"[*] --skip-junk dropped chapters: {', '.join(map(str, dropped))}\n")
    if not chapters:
        print("[X] No chapters found. Try --min-chars 100 to lower the threshold.")
        sys.exit(1)

    # Part length depends on the language's speech rate (Chinese ≈ 300 chars/min,
    # English ≈ 900), so size parts from the book's detected language.
    all_text = "".join(c["text"] for c in chapters)
    cjk = eng.is_cjk_text(all_text)
    cpm = eng.chars_per_min(all_text)
    part_chars = max(1000, int(args.part_minutes * cpm))

    total, est_minutes, unit = eng.estimate_words_minutes(chapters)
    u = "chars" if cjk else "w"
    print(f"[*] {len(chapters)} chapters  -  ~{total:,} {unit}  "
          f"-  ~{est_minutes // 60}h {est_minutes % 60}m estimated"
          f"  -  ~{args.part_minutes:g} min/part" + ("  [Chinese]" if cjk else "") + "\n")
    for ch in chapters:
        wc = len(re.sub(r"\s+", "", ch["text"])) if cjk else len(ch["text"].split())
        n_parts = len(eng.split_text(ch["text"], part_chars))
        tag = f"{n_parts} parts" if n_parts > 1 else "1 file"
        flag = " [junk?]" if ch["junk"] else ""
        ttl = eng.safe_filename(ch["title"])
        print(f"    {ch['index']:>2}  {ttl[:52]:<52}  ~{wc:>6,} {u}  ({tag}){flag}")
    print()

    if args.list:
        print("(dry run — nothing synthesised)\n")
        return

    print("[*] Synthesising...  (safe to Ctrl+C and re-run; it resumes)\n")
    failed, produced = [], {}

    def do_chapter(chapter):
        label = f"[{chapter['index']:>2}] {eng.safe_filename(chapter['title'])[:46]}"
        try:
            t0 = time.time()
            paths = eng.synthesize_chapter(
                chapter, out_dir, fmt, part_chars, voice=args.voice, rate=rate,
                announce=args.announce, subs=args.subs)
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
            else:
                produced[idx] = paths

    print(f"\n{'-' * 60}")
    done = len(chapters) - len(failed)
    print(f"[OK] {done}/{len(chapters)} chapters complete.")
    print(f"     Folder: {out_dir.resolve()}/")

    ordered_paths, ordered_titles, cover = [], [], b""
    if (args.tag or args.m4b) and not failed:
        cover = eng.extract_cover(book)
    for ch in chapters:
        for p in produced.get(ch["index"], []):
            ordered_paths.append(p)
            ordered_titles.append(eng.safe_filename(ch["title"]))

    if args.tag and ordered_paths:
        if not eng.HAVE_MUTAGEN:
            print("\n[!] --tag needs mutagen:  pip install mutagen  (skipped)")
        else:
            for n, (p, ctitle) in enumerate(zip(ordered_paths, ordered_titles), 1):
                eng.write_tags(p, album=book_title, author=author_str,
                               track=n, title=ctitle, cover_bytes=cover)
            print(f"[OK] Tagged {len(ordered_paths)} MP3(s).")

    if args.m4b and ordered_paths:
        if not eng.have_ffmpeg():
            print("\n[!] --m4b needs ffmpeg on PATH (skipped).")
        else:
            out_m4b = out_dir / f"{eng.safe_filename(book_title)}.m4b"
            print(f"[*] Building {out_m4b.name} …")
            try:
                eng.combine_to_m4b(ordered_paths, out_m4b, title=book_title,
                                   author=author_str, chapter_titles=ordered_titles,
                                   cover_bytes=cover)
                print(f"[OK] Wrote {out_m4b.resolve()}")
            except Exception as exc:
                print(f"[X] .m4b build failed: {exc}")

    if failed:
        print("\n[!] Failed chapters (re-run the SAME command to retry — "
              "finished files are cached):")
        for idx in sorted(failed):
            print(f"     - chapter {idx}")
    print()


if __name__ == "__main__":
    main()
