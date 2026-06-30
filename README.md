# EPUB → Audiobook

Turn any `.epub` book into a folder of clean, chapter-named audio files — and
**read along** while it plays, with the current sentence highlighted in sync.
Free, runs on your own computer, and supports **English and Chinese**
(Mandarin & Cantonese).

> 🌐 **Non-technical overview:** see the showcase page →
> https://lyhjeremy.github.io/epub-to-audiobook/

---

## What it does

- 📖 **Reads EPUBs** — extracts the text, detects chapters, and skips front/back
  matter (cover, copyright, index).
- 🗣️ **Natural neural voices** via Microsoft `edge-tts` (no API key, no cost).
- 🀄 **English + Chinese** — Mandarin (Mainland / Taiwan) and Cantonese (Hong
  Kong), with correct sentence splitting (。！？；) and Chinese fonts.
- 🎯 **Read-along player** — highlights each sentence in time with the audio,
  using `edge-tts`'s own word-timing data.
- 💾 **Crash-safe & resumable** — writes each chapter atomically and skips
  chapters already done if you re-run.
- 🎛️ **Modern GUI** — light/dark themes, adjustable speed/font/size, auto-advance,
  resume, sleep timer, and keyboard shortcuts.
- 📦 **Export options** — per-chapter `.mp3` (with optional ID3 tags + embedded
  cover) or a single `.m4b` audiobook with chapter markers (via `ffmpeg`).

## Repository layout

This repo contains **code only**, and ships just the **latest version (V6)**.
The `.epub` source books and the generated `.mp3` / `.m4b` audio are intentionally
**not** included (third-party copyright and file size) — point the app at your own EPUBs.

```
App V6/                          The app (latest version — self-contained)
  epub_to_audiobook_gui_v6.py    🖱️  The app: converter + read-along player
  epub_to_audiobook_v6.py        ⌨️  Command-line version
  audiobook_engine_v6.py         🧠  Engine: accurate timing + speed rendering
  audiobook_engine.py            🧩  Base engine (CJK-aware + ffmpeg locator)
  README_V6.md                   Notes for this version
  Launch Audiobook App.bat       Windows launcher
  Launch Audiobook App.command   macOS launcher
  Make Mac App.command           Builds a double-clickable macOS .app
```

## Quick start (V6)

```bash
# 1. Install dependencies
pip install edge-tts ebooklib beautifulsoup4 pygame
pip install mutagen            # optional: ID3 tags + cover art on each MP3

# 2. (Optional) install ffmpeg — needed only for speed control and single-file .m4b
#    Windows:  winget install Gyan.FFmpeg
#    macOS:    brew install ffmpeg
#    Linux:    sudo apt install ffmpeg
#    V6 auto-discovers ffmpeg even when it isn't on your PATH.

# 3. Run the app
python "App V6/epub_to_audiobook_gui_v6.py"      # graphical, with read-along
python "App V6/epub_to_audiobook_v6.py" book.epub  # command line
```

On Windows you can also double-click **`App V6/Launch Audiobook App.bat`**; on
macOS, **`Launch Audiobook App.command`** (or build a real app with
**`Make Mac App.command`**).

## How it evolved

Built over six iterations; this repo ships the latest, **V6**, which includes
everything below:

- **V1** — core pipeline: text extraction, chapter detection, `edge-tts`
  synthesis, crash-safe atomic writes, resume-on-rerun.
- **V2** — engine factored into a shared module; sentence-level timing sidecars
  for the read-along player; smarter chapter detection; ID3 tags + `.m4b` export.
- **V3 / V4** — modern UI: light/dark, text-first read-along, real line spacing,
  page-turn scrolling, speed/font/size/highlight, auto-advance, resume, sleep
  timer, keyboard shortcuts, and an **Open folder…** button.
- **V5** — **Chinese support**: Mandarin (Mainland/Taiwan) and Cantonese (Hong
  Kong) voices, Chinese-aware sentence splitting, character-based estimates,
  accurate Chinese read-along, and auto font/voice selection.
- **V6** — **robust ffmpeg discovery** so speed control and `.m4b` work on a
  fresh machine without manual PATH setup, with actionable install errors and a
  "ship ffmpeg next to the app" option.

## A note on content

This tool is for converting books **you own or that are in the public domain**
into audio for your **personal use**. The repository ships no copyrighted books
or audio; you supply your own EPUB files.

## License

[MIT](LICENSE) © 2026 Jeremy Lee
