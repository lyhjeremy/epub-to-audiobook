# EPUB to Audiobook

A Python desktop app that turns any `.epub` into chapter-named audio with free neural voices, and highlights each sentence on screen as it plays. English, Mandarin and Cantonese.

[Live site](https://lyhjeremy.github.io/epub-to-audiobook/) · [Write-up](https://lyhjeremy.github.io/epub-to-audiobook/overview/)

Design and copy follow [these standards](https://github.com/lyhjeremy/lyhjeremy/blob/main/DESIGN_STANDARDS.md).

## Sample output

A `--list` dry run of the V6 command-line tool on an EPUB of Haruki Murakami's *What I Talk About When I Talk About Running* (the book is not in the repo):

```
[*] 14 chapters  -  ~45,646 words  -  ~5h 4m estimated  -  ~12 min/part

     1  Contents                          ~    95 w  (1 file) [junk?]
     2  Foreword                          ~   751 w  (1 file)
     3  Chapter One                       ~ 5,326 w  (3 parts)
     4  Chapter Two                       ~ 6,294 w  (4 parts)
     ...
    12  Afterword                         ~ 1,221 w  (1 file)
    13  Also by Haruki Murakami           ~    62 w  (1 file) [junk?]
    14  Copyright                         ~   163 w  (1 file) [junk?]
```

There are no screenshots of the GUI yet: capturing the window was blocked by a macOS screen-recording permission prompt.

## What it does

- Extracts the text with ebooklib and BeautifulSoup, detects chapters, and flags front and back matter (contents, copyright, "Also by") for you to untick. `--skip-junk` drops them on the command line.
- Renders each chapter with Microsoft edge-tts neural voices (no API key), split into parts of about 12 minutes. Runtime estimates use about 900 characters a minute for English and 300 for Chinese.
- Writes each chapter to a `.part` file and renames it into place with `os.replace`, so a crash leaves no half-file and a re-run skips finished chapters.
- Plays the book in a read-along player that highlights each sentence. The highlight units are edge-tts's own boundary events, stored in a `.subs.json` sidecar beside each MP3.
- Supports Mandarin (zh-CN, zh-TW) and Cantonese (zh-HK) voices, with Chinese sentence splitting (。！？；) and PingFang fonts.
- Exports per-chapter MP3s with ID3 tags and cover art (mutagen), or one `.m4b` with chapter markers (ffmpeg). Playback speed uses ffmpeg's pitch-preserving `atempo`.

## The read-along drift fix

V2 split each chapter into sentences with its own splitter and matched them to edge-tts's boundary events by greedy text matching. The counts rarely agreed (86 timed segments against 77 sentences in one part), so the highlight drifted further out of sync through a track. Since V3 the player uses the boundary events directly as the highlight units, made contiguous so the highlight never flickers, and stores the real audio duration for an exact seek bar.

## Version history

| Version | Change |
|---|---|
| V1 | Text extraction, chapter detection, edge-tts synthesis, atomic writes, resume |
| V2 | Shared engine, timing sidecars, smarter chapter detection, ID3 tags and `.m4b` export |
| V3–V4 | Read-along timed from edge-tts boundary events, speed control, modern UI (themes, font and size, auto-advance, resume, sleep timer, shortcuts) |
| V5 | Mandarin and Cantonese voices, Chinese-aware splitting, estimates and fonts |
| V6 | ffmpeg discovery off PATH (winget, Chocolatey, Scoop, Homebrew, a bundled folder or `FFMPEG_DIR`) with OS-specific install hints |

This repo ships only V6.

## Limitations

- Desktop only (Tkinter); nothing runs in a browser.
- Synthesis needs an internet connection, since edge-tts is a hosted Microsoft service. Keep `edge-tts` upgraded: it is an unofficial endpoint and older versions stop working.
- Code only. The repo ships no books or audio; convert books you own or public-domain titles, for personal use.

## How to run

```bash
pip install edge-tts ebooklib beautifulsoup4 pygame
pip install mutagen            # optional: ID3 tags and cover art

# optional, for speed control and .m4b:
#   macOS:   brew install ffmpeg
#   Windows: winget install Gyan.FFmpeg
#   Linux:   sudo apt install ffmpeg

python "App V6/epub_to_audiobook_gui_v6.py"                 # GUI with read-along
python "App V6/epub_to_audiobook_v6.py" book.epub           # command line
python "App V6/epub_to_audiobook_v6.py" --list book.epub    # dry run: chapters and estimates
```

On Windows you can double-click `App V6/Launch Audiobook App.bat`; on macOS, `Launch Audiobook App.command`, or build a real app with `Make Mac App.command`.

## Repo layout

| Path | What it is |
|---|---|
| `App V6/epub_to_audiobook_gui_v6.py` | GUI: converter and read-along player |
| `App V6/epub_to_audiobook_v6.py` | Command-line version |
| `App V6/audiobook_engine_v6.py` | Boundary-event timing and speed rendering |
| `App V6/audiobook_engine.py` | Base engine: extraction, chapters, voices, tags, `.m4b`, ffmpeg locator |
| `App V6/README_V6.md` | Notes for this version |
| `App V6/*.command`, `*.bat` | Launchers and the macOS app builder |

Stack: Python, edge-tts, ebooklib, BeautifulSoup, Tkinter, pygame, mutagen, ffmpeg.

## License

[MIT](LICENSE) © 2026 Jeremy Lee
