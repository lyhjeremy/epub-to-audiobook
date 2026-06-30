# EPUB → Audiobook **V6**

Everything V5 does, **plus robust ffmpeg discovery** so the speed control and
`.m4b` work on a fresh computer without manual PATH setup. V1–V5 are untouched —
V6 lives in its own folder.

| File | What it is |
|---|---|
| `epub_to_audiobook_gui_v6.py` | 🖱️ The app — converter + read-along (English + Chinese) |
| `epub_to_audiobook_v6.py` | ⌨️ Command-line version |
| `audiobook_engine_v6.py` | 🧠 Engine: accurate timing + speed rendering |
| `audiobook_engine.py` | 🧩 Base engine (CJK-aware + ffmpeg locator) |

---

## What's new in V6

- **🔎 Finds ffmpeg even when it isn't on PATH.** The app now looks in the usual
  install locations (winget, Chocolatey, Scoop, Homebrew, `/usr/bin`, a bundled
  `ffmpeg/` folder, or a `FFMPEG_DIR` you set) in addition to PATH. When it finds
  ffmpeg off-PATH it adds it to the running process automatically — so the
  *"ffmpeg not found on PATH — needed for speed change"* error no longer appears
  just because the install hasn't propagated to PATH yet.
- **🧰 Actionable errors.** If ffmpeg genuinely isn't installed, the speed/`.m4b`
  errors now tell you the exact command to install it for your OS.
- **📦 Ship-it option.** Drop `ffmpeg`/`ffprobe` (or a `bin/` folder containing
  them) next to the app and it'll use those — no system install required.

See [Setup](#setup) for the one-line ffmpeg install.

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

### ffmpeg (for the reading-speed control and `.m4b`)

ffmpeg is needed for the speed slider and for building a single `.m4b`. Install
it once:

```
winget install Gyan.FFmpeg     # Windows
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Debian/Ubuntu
```

> **After installing, fully quit and reopen the app.** A program only reads PATH
> when it starts, so an app left running won't see a brand-new install until it
> restarts. (V6 also scans the common install locations, so in most cases a
> restart alone is enough even if the installer didn't update PATH.)

**Alternatives if you can't change PATH:**
- Set `FFMPEG_DIR` to the folder that holds `ffmpeg`/`ffprobe`, or
- Drop `ffmpeg`/`ffprobe` (or a `bin/` folder with them) **next to the app's
  `.py` files** — V6 will find and use them automatically.

No extra packages are needed for Chinese — the `zh-*` voices and PingFang fonts
are built into macOS / edge-tts.

---

## Launching the app (no command line)

You don't need to open VS Code or type anything — use the launcher for your OS.

### Windows
- Double-click **`Launch Audiobook App.bat`** in this folder, **or**
- Double-click the **`EPUB to Audiobook`** shortcut on the Desktop.

Both start the GUI with no console window. To get the Desktop shortcut on
another Windows PC, run this once in PowerShell from inside this folder:

```powershell
$pyw = (Get-Command pythonw).Source
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) "EPUB to Audiobook.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $pyw; $s.Arguments = '"epub_to_audiobook_gui_v6.py"'
$s.WorkingDirectory = (Get-Location).Path; $s.IconLocation = "$pyw,0"; $s.Save()
```

### macOS — recommended: build a real `.app` (one time)

This gives you a normal double-click app with **no Terminal window** and no
"unidentified developer" warning.

1. Open this folder in **Finder** and open **Terminal**.
2. In Terminal type `bash` and a space, then **drag `Make Mac App.command` into
   the Terminal window** (this fills in the path) and press **Enter**.
3. It creates **`EPUB to Audiobook.app`** in this folder.

From then on, just **double-click `EPUB to Audiobook.app`** (and drag it to the
Dock to keep it handy). Re-run the builder only if you move the folder to a
different Mac.

> Build it **on the Mac** — a `.app` made on Windows / synced via Google Drive
> loses the bundle structure and Unix permissions it needs.

### macOS — simple alternative

- Double-click **`Launch Audiobook App.command`** in this folder.

**First time only:** macOS marks downloaded/synced scripts as non-executable, so
double-clicking would just open it in a text editor. Fix it once in Terminal
(type `chmod +x ` then drag the file in to fill the path, then Enter):

```bash
chmod +x "Launch Audiobook App.command"
```

After that, double-clicking launches the app, though a Terminal window stays open
behind it. (If macOS warns it's from an unidentified developer, right-click the
file → **Open** → **Open** the first time.)

> The `.bat`/Desktop shortcut are Windows-only; the `.command`/`.app` are
> macOS-only. All of them just run `epub_to_audiobook_gui_v6.py`, so the command
> line below always works too on either OS.

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
