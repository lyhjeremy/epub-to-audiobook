# EPUB → Audiobook **V2**

Same idea as V1 — turn any **`.epub`** into a folder of **MP3 chapters** read
aloud in a natural voice — now with a built-in **read-along player** and a few
extras. **V1 is untouched**; V2 lives in new files next to it.

> ✨ **The headline feature:** play any chapter inside the app and watch the
> book’s text **highlight sentence-by-sentence in time with the narration**.
> Click any sentence to jump there.

| File | What it is |
|---|---|
| `epub_to_audiobook_gui_v2.py` | 🖱️ The app — converter **+ read-along player** |
| `epub_to_audiobook_v2.py` | ⌨️ The command-line version (same engine) |
| `audiobook_engine.py` | 🧠 Shared engine both of the above import (no UI) |

The V1 files (`epub_to_audiobook.py`, `epub_to_audiobook_gui.py`) still work
exactly as before.

---

## What’s new in V2

- **Read-along player** — sentence-synced highlighting while it plays, with
  click-to-jump. Powered by sentence-timing sidecar files written during
  conversion (`<chapter>.subs.json`, sitting next to each MP3).
- **Preview voice** — hear a short sample before committing to a whole book.
- **More voices** — including the very natural *Multilingual* narrators.
- **Smarter chapter list** — likely front/back matter (cover, copyright, index,
  tiny pages) is auto-flagged ⚑ and starts **unticked**.
- **Optional output formats**
  - **Tags + cover** embedded in each MP3 (needs `mutagen`).
  - **One `.m4b` audiobook** with chapter markers + cover (needs `ffmpeg`).
- **One engine** — the logic is no longer copy-pasted between the app and the
  CLI; both import `audiobook_engine.py`.

---

## 1. One-time setup

```
pip install edge-tts ebooklib beautifulsoup4 pygame
```

Optional extras:

```
pip install mutagen     # tags + cover art, and cover inside the .m4b
```

…and **`ffmpeg`** on your PATH if you want the single-file `.m4b`
(`brew install ffmpeg` on macOS).

> `pygame` is what plays audio inside the app. Without it the converter still
> works and you can read the text, but in-app playback is disabled.

---

## 2. Using the app

1. **Run** `epub_to_audiobook_gui_v2.py` (press ▶ in your editor, or
   `python epub_to_audiobook_gui_v2.py`).
2. **Convert tab:** Browse to your `.epub`, pick a voice (try **Preview**),
   adjust speed/chapters, then **Convert**. Keep *“Generate read-along
   subtitles”* ticked to enable syncing.
3. **Read-along tab:** choose a track, press **▶ Play**, and follow along — the
   current sentence highlights and scrolls into view. Click any sentence to jump.

Stopping is safe: finished files are cached and skipped, so re-running resumes
where it left off.

---

## 3. Command line

```bash
# preview the chapters (no audio made); junk is flagged
python epub_to_audiobook_v2.py "YourBook.epub" --list

# convert (writes MP3s + .subs.json sidecars by default)
python epub_to_audiobook_v2.py "YourBook.epub"

# skip flagged front/back matter, tag the files, and build one .m4b
python epub_to_audiobook_v2.py "YourBook.epub" --skip-junk --tag --m4b

# hear a voice before converting
python epub_to_audiobook_v2.py --voice en-US-AvaMultilingualNeural --preview-voice
```

New flags on top of V1’s: `--subs/--no-subs` (default on), `--skip-junk`,
`--tag`, `--m4b`, `--preview-voice [TEXT]`. Everything else
(`--voice`, `--rate`, `--announce`, `--part-minutes`, `--workers`, `--skip`,
`--min-chars`, `--pad`, `--out`, `--list`) behaves as in V1.
Run `python epub_to_audiobook_v2.py --help` for the full list.

---

## 4. How the sync works (for the curious)

Microsoft Edge TTS emits a **word-boundary** event for every spoken word. During
conversion V2 streams the audio and these events together, then groups the words
into sentences to get a `start`/`end` time per sentence. That table is saved as
`<chapter>.subs.json` next to the MP3. The player just loads it, tracks the
playback position, and highlights whichever sentence’s time-span contains it.

MP3s made **without** subtitles (or by V1) still play — you simply won’t get the
highlighting for those tracks.

---

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| “packages are missing” pop-up | Run the `pip install` line in Setup. |
| Read-along tab says playback needs pygame | `pip install pygame`. |
| A chapter shows ✗ failed | Usually a network hiccup — click **Convert** again; it resumes. |
| “Tags / .m4b” checkboxes greyed out | Install `mutagen` / put `ffmpeg` on PATH, then restart the app. |
| No highlight for a track | It was made with subtitles off, or by V1. Re-convert with subtitles on. |
| No chapters found | Lower **Min chars / chapter** and reload. |
