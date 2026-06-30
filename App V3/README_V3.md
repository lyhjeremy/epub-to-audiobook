# EPUB → Audiobook **V3**

Everything V2 does, with a better read-along experience and a dark mode.
**V1 and V2 are untouched** — V3 lives in new files beside them.

| File | What it is |
|---|---|
| `epub_to_audiobook_gui_v3.py` | 🖱️ The app — converter **+ dark theme + upgraded read-along player** |
| `epub_to_audiobook_v3.py` | ⌨️ Command-line version (uses the V3 engine) |
| `audiobook_engine_v3.py` | 🧠 V3 engine: accurate timing + speed rendering (reuses the V2 engine for the rest) |

---

## What's new in V3

- **🌙 Dark theme** — a **Light/Dark** toggle in the top-right. Your choice is
  remembered, along with all your read-along preferences.
- **🎯 Accurate sync (fixed)** — V2 highlighted using its own sentence splitter
  aligned to edge-tts's segments; the two disagree (e.g. 86 vs 77 segments), so
  the highlight drifted further off as a part played. V3 uses **edge-tts's own
  segments directly**, so the highlight stays locked to the audio to the very
  end, and the last sentence ends exactly with the audio.
- **📜 Page-turn auto-scroll** — the highlight stays put until it reaches the
  bottom of the page, then the view turns so that line becomes the **second line
  from the top** — a fresh page of upcoming text, instead of nudging on every
  sentence.
- **🎛️ Read-along controls**
  - **Reading speed** — 0.75× to 2× with natural pitch (rendered by ffmpeg and
    cached; the first switch on a long part takes a second or two).
  - **Font family**, **font size**, **line spacing** (up to 50), and
    **highlight colour** (presets or a custom colour).
- **Extras**
  - **Auto-advance** through a chapter's parts and on to the next chapter.
  - **Resume** — it remembers where you stopped in each track and offers to pick
    up there.
  - **Sleep timer** — stop after N minutes or at the end of the current track.
  - **Keyboard shortcuts** — `Space` play/pause, `←/→` previous/next sentence,
    `↑/↓` previous/next track.

The sidecar timing files stay compatible: a book made in V3 also reads correctly
in the V2 app, and V3 can play books you already made in V2.

---

## Setup

```
pip install edge-tts ebooklib beautifulsoup4 pygame
pip install mutagen          # optional: tags + cover art / .m4b cover
```

`ffmpeg` on your PATH is needed for the **reading-speed** control and for the
single-file `.m4b` (`brew install ffmpeg` on macOS).

---

## Run it

```bash
cd "/Users/jeremylee/My Drive/1. Personal/16. Reading/Audibook Generation"
python3 epub_to_audiobook_gui_v3.py
```

1. **Convert tab** → Browse to an `.epub`, pick a voice (try **Preview**), keep
   *“Generate read-along subtitles”* ticked, then **Convert**.
2. **Read-along tab** → choose a track, set speed/font/highlight to taste, press
   **▶ Play**. The text follows along, sentence-by-sentence, scrolling to keep
   the current line near the top. Click any sentence to jump there.
3. Top-right **☾ Dark / ☀ Light** switches the theme anytime.

> Settings live in `~/.epub_audiobook_v3.json`. Delete that file to reset to
> defaults.

---

## Command line

Same flags as V2 (`--list`, `--skip-junk`, `--tag`, `--m4b`,
`--preview-voice`, `--voice`, `--rate`, `--part-minutes`, …), now writing the
accurate V3 timing:

```bash
python3 epub_to_audiobook_v3.py "Man's Search For Meaning.epub" --list
python3 epub_to_audiobook_v3.py "Man's Search For Meaning.epub" --skip-junk
```

---

## Notes / troubleshooting

| Thing | Detail |
|---|---|
| First speed change is slow | It renders that part at the new tempo with ffmpeg, then caches it; later it's instant. |
| Speed control greyed-out behaviour | Needs `ffmpeg` on PATH. Without it, leave speed at 1×. |
| Highlight on a track does nothing | That track has no timing (made with subtitles off). Re-convert with subtitles on. |
| Reset everything | Delete `~/.epub_audiobook_v3.json`. |
