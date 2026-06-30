# EPUB → Audiobook **V4**

| File | What it is |
|---|---|
| `epub_to_audiobook_gui_v4.py` | 🖱️ The app — converter **+ modern, text-first read-along** |
| `epub_to_audiobook_v4.py` | ⌨️ Command-line version (uses the V4 engine) |
| `audiobook_engine_v4.py` | 🧠 Engine: accurate timing + speed rendering (reuses the base engine for the rest) |
| `audiobook_engine.py` | 🧩 Base engine it builds on |

---

## Setup

```
pip install edge-tts ebooklib beautifulsoup4 pygame
pip install mutagen          # optional: tags + cover art / .m4b cover
```

`ffmpeg` on your PATH is needed for the **reading-speed** control and the
single-file `.m4b` (`brew install ffmpeg` on macOS).

---

## Run it

```bash
cd "V4 - Modern Read-Along"
python3 epub_to_audiobook_gui_v4.py
```

1. **Convert tab** → Browse to an `.epub`, pick a voice (try **Preview**), keep
   *“Generate read-along subtitles”* ticked, then **Convert**.
2. **Read-along tab** → choose a track, tune speed / font / size / spacing /
   highlight in the toolbar, press **▶ Play**. The text follows along
   sentence-by-sentence; click any sentence to jump.
3. Top-right **☾ Dark / ☀ Light** switches the theme anytime.

> Settings live in `~/.epub_audiobook_v3.json` (shared with V3 so your
> preferences carry over). Delete it to reset to defaults.

---

## Command line

Same flags as before (`--list`, `--skip-junk`, `--tag`, `--m4b`,
`--preview-voice`, `--voice`, `--rate`, `--part-minutes`, …):

```bash
python3 epub_to_audiobook_v4.py "Man's Search For Meaning.epub" --list
python3 epub_to_audiobook_v4.py "Man's Search For Meaning.epub" --skip-junk
```

---

## Notes / troubleshooting

| Thing | Detail |
|---|---|
| First speed change is slow | It renders that part at the new tempo with ffmpeg, then caches it; later it's instant. |
| Speed needs ffmpeg | Without `ffmpeg` on PATH, leave speed at 1×. |
| Highlight does nothing on a track | That track has no timing (made with subtitles off). Re-convert with subtitles on. |
| Reset everything | Delete `~/.epub_audiobook_v3.json`. |
