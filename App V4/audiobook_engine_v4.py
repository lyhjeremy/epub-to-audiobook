"""
audiobook_engine_v3.py — Engine extensions for V3
=================================================
V3 reuses the stable V2 engine (`audiobook_engine.py`) for everything that
didn't change — text extraction, chapter detection, filenames, voices, tags,
cover art, the .m4b builder — and changes only two things:

1. ACCURATE READ-ALONG TIMING.  V2 re-segmented each chapter with its own
   sentence splitter and then *aligned* that to edge-tts's boundary events with
   greedy text-matching. edge-tts emits a different number of segments than the
   splitter (e.g. 86 vs 77 for one part), so the alignment drifts further and
   further through a track — the late-part de-sync you noticed.

   V3 stores edge-tts's boundary events **directly** as the highlight units, so
   each highlighted unit is exactly the span edge-tts timed. No alignment, no
   drift. Units are made contiguous (each ends where the next begins) so the
   highlight never flickers between sentences, and the real audio duration is
   recorded for an exact seek bar.

2. VARIABLE PLAYBACK SPEED.  `render_at_speed()` uses ffmpeg's `atempo`
   (pitch-preserving time-stretch) to produce a sped-up/slowed copy of a track,
   cached, for the read-along speed control.

The sidecar file name and JSON shape stay compatible with V2 (same
`<stem>.subs.json`, same `sentences` list) — V3 just fills it with accurate
data and adds a `duration_ms` field — so a V3-made book also plays correctly in
the V2 app, and vice-versa.
"""

import os
import json
import hashlib
import subprocess
import tempfile
from pathlib import Path

# Reuse every stable piece of the V2 engine unchanged.
from audiobook_engine import (                      # noqa: F401
    CHARS_PER_MIN, DEFAULT_VOICE, POPULAR_VOICES, SUBS_SUFFIX,
    HAVE_MUTAGEN, missing_deps,
    extract_text, split_text, split_sentences,
    flatten_toc, build_title_map, safe_filename,
    gather_chapters, normalize_rate,
    tts_to_file, tts_to_file_timed,
    subs_path_for, load_subs,
    extract_cover, write_tags, have_ffmpeg, combine_to_m4b,
    load_book, estimate_words_minutes, chapter_part_paths,
)

SUBS_VERSION = 3


# ── Accurate units straight from edge-tts boundaries ──────────────────────────

def boundaries_to_units(events: list, duration_ms: int = 0) -> list:
    """
    Turn raw edge-tts boundary events [(offset_ms, dur_ms, text), ...] into
    contiguous highlight units [{start_ms, end_ms, text}].

    Each unit starts at its own offset and ends where the NEXT unit starts (so
    the highlight is gap-free); the final unit ends at the true audio duration
    (or its own offset+duration if that's later).
    """
    cleaned = [(int(off), int(dur), (txt or "").strip())
               for off, dur, txt in events if (txt or "").strip()]
    cleaned.sort(key=lambda e: e[0])
    units = []
    for i, (off, dur, txt) in enumerate(cleaned):
        if i + 1 < len(cleaned):
            end = cleaned[i + 1][0]
        else:
            end = max(off + dur, duration_ms)
        units.append({"start_ms": off, "end_ms": max(end, off), "text": txt})
    return units


def _measure_duration_ms(mp3_path: Path) -> int:
    """Real audio length in ms (mutagen preferred, ffprobe fallback, else 0)."""
    if HAVE_MUTAGEN:
        try:
            from mutagen.mp3 import MP3
            return int(MP3(str(mp3_path)).info.length * 1000)
        except Exception:
            pass
    if have_ffmpeg():
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(mp3_path)],
                capture_output=True, text=True)
            return int(float(out.stdout.strip()) * 1000)
        except Exception:
            pass
    return 0


def _write_subs(subs_file: Path, mp3_name, voice, rate, units, duration_ms):
    payload = {
        "version": SUBS_VERSION,
        "mp3": mp3_name,
        "voice": voice,
        "rate": rate,
        "duration_ms": duration_ms,
        "sentences": units,        # same key as V2 → cross-compatible
    }
    staging = subs_file.with_suffix(subs_file.suffix + ".tmp")
    staging.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    os.replace(staging, subs_file)


# ── Chapter synthesis (crash-safe + atomic + resumable; accurate subs) ────────

def synthesize_chapter(chapter: dict, out_dir: Path, fmt: str, part_chars: int,
                       voice: str, rate: str, announce: bool, subs: bool = True,
                       on_part=None, should_cancel=None) -> list:
    """
    Same crash-safe contract as V2's synthesize_chapter (atomic '.part' rename,
    cached files reused), but writes ACCURATE sentence timing taken straight
    from edge-tts boundaries plus the measured audio duration.
    """
    idx      = chapter["index"]
    safe_ttl = safe_filename(chapter["title"])
    parts    = split_text(chapter["text"], part_chars)
    multi    = len(parts) > 1

    out_paths = []
    for pi, part_text in enumerate(parts, start=1):
        if should_cancel and should_cancel():
            break

        suffix  = f" - Part {pi}" if multi else ""
        out_mp3 = out_dir / (fmt.format(idx=idx) + f" - {safe_ttl}{suffix}.mp3")
        out_paths.append(out_mp3)
        subs_file = subs_path_for(out_mp3)

        if out_mp3.exists() and out_mp3.stat().st_size > 0:
            if on_part:
                on_part(out_mp3, True)
            continue

        spoken = part_text
        if announce:
            head = f"Chapter {idx}. {chapter['title']}."
            if multi:
                head = f"Chapter {idx}. {chapter['title']}. Part {pi}."
            spoken = head + "\n" + part_text

        staging = out_mp3.with_suffix(".part")
        if subs:
            events = tts_to_file_timed(spoken, str(staging), voice, rate)
            dur = _measure_duration_ms(staging)
            units = boundaries_to_units(events, dur)
        else:
            tts_to_file(spoken, str(staging), voice, rate)
            units, dur = None, 0

        os.replace(staging, out_mp3)
        if units is not None:
            _write_subs(subs_file, out_mp3.name, voice, rate, units, dur)

        if on_part:
            on_part(out_mp3, False)

    return out_paths


# ── Variable playback speed (ffmpeg atempo, pitch-preserving, cached) ─────────

def _speed_cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "epub_audiobook_speed_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_at_speed(mp3_path, speed: float) -> Path:
    """
    Return a path to `mp3_path` played at `speed`× (pitch preserved), rendering
    it with ffmpeg the first time and caching the result. speed==1.0 returns the
    original file unchanged. Raises RuntimeError if ffmpeg is unavailable.

    The cache key includes the source path, its mtime/size and the speed, so an
    edited/re-synthesised source is re-rendered automatically.
    """
    mp3_path = Path(mp3_path)
    if abs(speed - 1.0) < 1e-3:
        return mp3_path
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg not found on PATH — needed for speed change")

    st = mp3_path.stat()
    key = f"{mp3_path.resolve()}|{st.st_mtime_ns}|{st.st_size}|{speed:.3f}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    out = _speed_cache_dir() / f"{mp3_path.stem}@{speed:.2f}x_{digest}.mp3"
    if out.exists() and out.stat().st_size > 0:
        return out

    # atempo handles 0.5–2.0 in one filter; chain for anything outside.
    factors = _atempo_chain(speed)
    flt = ",".join(f"atempo={f:.4f}" for f in factors)
    staging = out.with_suffix(".part.mp3")
    cmd = ["ffmpeg", "-y", "-i", str(mp3_path), "-filter:a", flt,
           "-vn", str(staging)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not staging.exists() or staging.stat().st_size == 0:
        try:
            staging.unlink()
        except OSError:
            pass
        raise RuntimeError(f"ffmpeg atempo failed:\n{res.stderr[-800:]}")
    os.replace(staging, out)
    return out


def _atempo_chain(speed: float) -> list:
    """Decompose `speed` into atempo factors each within ffmpeg's 0.5–2.0 range."""
    factors, remaining = [], speed
    while remaining > 2.0:
        factors.append(2.0); remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5); remaining /= 0.5
    factors.append(remaining)
    return factors
