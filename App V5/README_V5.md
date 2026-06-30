# EPUB → Audiobook **V5**

Everything V4 does, **plus Mandarin and Cantonese** read-along. V1–V4 are
untouched — V5 lives in its own folder.

| File | What it is |
|---|---|
| `epub_to_audiobook_gui_v5.py` | 🖱️ The app — converter + read-along (English + Chinese) |
| `epub_to_audiobook_v5.py` | ⌨️ Command-line version |
| `audiobook_engine_v5.py` | 🧠 Engine: accurate timing + speed rendering |
| `audiobook_engine.py` | 🧩 Base engine (now CJK-aware) |

---

## What's new in V5

- **🗣️ Chinese voices.** A **Language** selector next to the voice menu offers:
  - **Mandarin (Mainland)** — `zh-CN` voices (Xiaoxiao, Yunxi, …)
  - **Mandarin (Taiwan)** — `zh-TW` voices
  - **Cantonese (Hong Kong)** — `zh-HK` voices (HiuMaan, WanLung, …)
- **🀄 Understands Chinese text.** Sentence splitting now handles 。！？；…
  (Chinese has no spaces between sentences), part lengths use the Chinese speech
  rate (~300 chars/min vs ~900 for English), and counts/estimates switch to
  **characters**.
- **🎯 Accurate Chinese read-along.** The sentence highlighting comes straight
  from edge-tts's own Chinese sentence timing (verified to match the audio), so
  it stays in sync for Mandarin and Cantonese just like English.
- **🈶 Chinese font.** When a Chinese book is opened, the reader auto-switches to
  a Chinese font (PingFang SC for Mainland, PingFang HK/TC for HK/Taiwan).
- **🤖 Auto setup.** Opening a Chinese book auto-selects a Mandarin voice. Since
  Mandarin and Cantonese share the same characters, just switch the Language to
  **Cantonese** (and pick a `zh-HK` voice) if you want it read in Cantonese.

Everything from V4 is unchanged: modern UI, Light/Dark, text-first read-along,
real line spacing, page-turn scroll, speed/font/size/highlight, auto-advance,
resume, sleep timer, keyboard shortcuts, and the **📁 Open folder…** button.

---

## Setup

```
pip install edge-tts ebooklib beautifulsoup4 pygame
pip install mutagen          # optional: tags + cover art / .m4b cover
```

`ffmpeg` on PATH is needed for the reading-speed control and `.m4b`.
No extra packages are needed for Chinese — the `zh-*` voices and PingFang fonts
are built into macOS / edge-tts.

---

## Reading a Chinese book

1. **Convert tab** → Browse to a Chinese `.epub`. The app detects Chinese,
   sets the voice to Mandarin, and shows character counts.
   - Want **Cantonese**? Set **Language → Cantonese (Hong Kong)** and pick a
     `zh-HK` voice. (Want a different Mandarin? Use the Language menu too.)
   - Try **Preview** to hear the voice.
2. **Convert.**
3. **Read-along tab** → pick a track and press **▶ Play**. The Chinese text
   highlights sentence-by-sentence in a Chinese font, in sync with the voice.

Mandarin and Cantonese both read the **same** Chinese characters — the voice
decides the pronunciation, so one Chinese `.epub` works for either.

---

## Command line

```bash
# English (unchanged)
python3 epub_to_audiobook_v5.py "book.epub" --list

# Chinese — pick a Mandarin or Cantonese voice
python3 epub_to_audiobook_v5.py "中文书.epub" --voice zh-CN-XiaoxiaoNeural
python3 epub_to_audiobook_v5.py "中文书.epub" --voice zh-HK-HiuMaanNeural   # Cantonese
```

`--list` shows character counts and `[Chinese]` for Chinese books; part lengths
and time estimates adjust to the Chinese speech rate automatically.

---

## Notes

| Thing | Detail |
|---|---|
| Mandarin vs Cantonese | Same characters; the **voice** sets the language. Pick a `zh-CN`/`zh-TW` voice for Mandarin, `zh-HK` for Cantonese. |
| Simplified vs Traditional | Displayed exactly as written in the EPUB — no conversion. HK/TW voices pair with a Traditional-friendly font. |
| Reset everything | Delete `~/.epub_audiobook_v3.json` (settings file shared across V3–V5). |
