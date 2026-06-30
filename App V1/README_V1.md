# EPUB → Audiobook

Turn any **`.epub`** book into a folder of **MP3 files** that read it aloud,
word-for-word, in a natural voice. Chapters are detected automatically and
named nicely:

```
Man's Search For Meaning/
   Ch.1 - Foreword.mp3
   Ch.3 - Experiences in a Concentration Camp - Part 1.mp3
   Ch.3 - Experiences in a Concentration Camp - Part 2.mp3
   ...
```

Short chapters become one file; long chapters are split into ~12-minute
**Parts**. The audio comes from Microsoft Edge's free text-to-speech — natural
voices, no account, no API key.

There are **two ways** to use it:

| | Best for |
|---|---|
| 🖱️ **The app** (`epub_to_audiobook_gui.py`) | Almost everyone — a simple window with buttons and menus |
| ⌨️ **The command line** (`epub_to_audiobook.py`) | People who like typing commands / automation |

Both do exactly the same thing. **Start with the app.**

---

## 1. One-time setup

Do this **once per computer**.

1. **Install Python** (3.9 or newer) from <https://www.python.org/downloads/>.
   On the first install screen, tick **“Add Python to PATH”**.

2. **Install the three required packages.** Open a terminal
   (in VS Code: menu `Terminal → New Terminal`) and paste:

   ```
   pip install edge-tts ebooklib beautifulsoup4
   ```

> An internet connection is needed every time you convert a book, because the
> audio is generated online.

---

## 2. Using the app (the easy way)

1. **Start it.** Open `epub_to_audiobook_gui.py` in VS Code (or any editor) and
   press the **▶ Run** button. *(Or, in a terminal:
   `python epub_to_audiobook_gui.py`.)* A window opens.

2. **Pick your book.** Click **Browse…** next to *EPUB file* and choose your
   `.epub`. The book’s title, author, and chapters appear.

3. **Set it up** (all optional — the defaults are fine):
   - **Voice** – choose a narrator from the menu
   - **Speed** – drag the slider to read faster or slower
   - **Chapters** – untick anything you don’t want (e.g. the copyright page)
   - **Output folder** – where the MP3s are saved (filled in for you)

4. **Click “Convert.”** A progress bar fills as each file is made. When it’s
   done, click **Open output folder** to find your MP3s.

That’s it. **Safe to stop anytime** — if you close the window or lose Wi-Fi,
just open it and click Convert again; finished files are kept and it picks up
where it left off.

### What the controls do

| Control | What it does |
|---|---|
| **Voice** | The narrator. Pick from the menu, or type any Edge voice name. |
| **Speed** | Faster / slower reading (−50% to +50%). |
| **Output folder** | Where the MP3s go. |
| **Part length (min)** | How long each Part of a long chapter is (default 12). |
| **Parallel workers** | How many chapters are made at once (default 4). |
| **Min chars / chapter** | Ignore tiny sections below this many characters. |
| **Announce chapter titles** | Speak “Chapter N. Title.” at the start of each file. |
| **Zero-pad numbers** | Name files `Ch.01` instead of `Ch.1`. |
| **Select all / Clear all** | Tick or untick every chapter at once. |

---

## 3. Good to know

- **Stopping is safe.** Finished files are kept and skipped next time, and a
  file is only saved once it’s fully made — so you’ll never get a broken
  half-file. To resume, just run it again.

- **What counts as a chapter?** Anything in the book’s table of contents with
  real text. Tiny front-matter (cover, blank pages) is skipped automatically. A
  copyright page may show up — just untick it.

- **Includes everything by design.** Every real section is converted so nothing
  is lost. You decide what to leave out by unticking chapters.

---

## 4. Troubleshooting

| Problem | Fix |
|---|---|
| A pop-up says **packages are missing** | Run `pip install edge-tts ebooklib beautifulsoup4`, then start the app again. |
| **`python` is not recognized** | Python isn’t on PATH. Reinstall it and tick **“Add Python to PATH.”** |
| A chapter shows a red **✗ failed** | Usually a network hiccup. Click **Convert** again — it retries and resumes. |
| **No chapters found** | Lower **Min chars / chapter** (e.g. to 100) and reload. |
| Wrong things included / excluded | Untick the chapters you don’t want before converting. |

---

## 5. Command-line version (advanced)

Prefer typing commands? `epub_to_audiobook.py` does the same job from a terminal.

```powershell
# preview the chapters (no audio made)
python epub_to_audiobook.py "YourBook.epub" --list

# convert
python epub_to_audiobook.py "YourBook.epub"

# convert, skipping chapters 1 and 21, speaking titles, with a UK voice
python epub_to_audiobook.py "YourBook.epub" --skip 1,21 --announce --voice en-GB-SoniaNeural
```

Common options: `--list`, `--out`, `--voice`, `--rate ±N%`, `--slow`,
`--announce`, `--part-minutes N`, `--workers N`, `--skip N,N`, `--min-chars N`,
`--pad`. Run `python epub_to_audiobook.py --help` for the full list.

### Picking a voice

See every available voice with:

```
edge-tts --list-voices
```

Popular English ones:

| Voice | Description |
|---|---|
| `en-US-GuyNeural` | Male, US **(default)** |
| `en-US-AriaNeural` | Female, US |
| `en-US-JennyNeural` | Female, US |
| `en-GB-RyanNeural` | Male, UK |
| `en-GB-SoniaNeural` | Female, UK |
| `en-AU-NatashaNeural` | Female, Australian |
