#!/usr/bin/env python3
"""
Scroll-caption video maker.

Standalone, manual-run tool: no automation, no queues. Drop three files
into a folder and run:

    python3 render.py /path/to/folder

Folder must contain exactly:
  - one image  (.jpg/.jpeg/.png/.webp)   -> background
  - one audio  (.mp3/.wav/.m4a/.aac)     -> music
  - one text   (.txt)                   -> the script

Text file format: one sentence/beat per line. Blank lines are ignored.
Every SENTENCE becomes its own beat (a line holding three sentences renders
as three separate paragraphs), so there is a double break after every
sentence or thought instead of large chunks. Pass --keep-lines to go back to
one beat per line. A beat that's too wide for the screen auto-wraps onto
multiple tight lines; each NEW beat gets one blank line's worth of space
before it, so wrapped continuation lines read as one thought and separate
beats read as separate paragraphs — matching the reference caption style.

Music: any silence at the very start of the track is cut off so the sound
begins at 0:00 (messaging-app audio exports often open with dead air).

Output: <folder>/output.mp4
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# A long script legitimately produces a very tall text canvas (a 2,400-word
# script is ~126,000px). That is not a decompression bomb — it is our own
# canvas — so lift Pillow's guard rather than have it warn on every run.
Image.MAX_IMAGE_PIXELS = None

# ---- tunables (safe to edit) ----------------------------------------------
OUT_W, OUT_H = 1080, 1920
FPS = 30

# Font resolution order (first match wins), so this runs identically on any
# machine instead of depending on what's installed:
#   1. SCROLL_VIDEO_FONT env var, if set (escape hatch for a different face)
#   2. bundled Poppins weight shipped alongside this script (fonts/), picked
#      by --weight (default DEFAULT_WEIGHT)
#   3. a search list of bold/heavy system fonts, by platform
_FONT_DIR = Path(__file__).resolve().parent / "fonts"
BUNDLED_FONTS = {                # all OFL, from google/fonts
    "black": _FONT_DIR / "Poppins-Black.ttf",        # 900 — the original look
    "bold": _FONT_DIR / "Poppins-Bold.ttf",          # 700
    "semibold": _FONT_DIR / "Poppins-SemiBold.ttf",  # 600
    "medium": _FONT_DIR / "Poppins-Medium.ttf",      # 500 — closest to the reference
}
DEFAULT_WEIGHT = "bold"          # Black is heavier than the usual scrolling-
                                 # caption look, Medium is lighter; Bold sits in
                                 # the middle. Override: --weight.
BUNDLED_FONT_FACE_INDEX = 0

FALLBACK_FONTS = [
    # (path, face_index) — checked in order, first that exists wins
    ("/System/Library/Fonts/Avenir Next.ttc", 8),          # macOS, "Heavy"
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),  # macOS
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),  # Linux (Debian/Ubuntu)
    ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 0),        # Linux (Arch)
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 0),  # Linux
    ("C:/Windows/Fonts/arialbd.ttf", 0),                    # Windows
]

FONT_SIZE_RATIO = 0.065        # font size = OUT_W * this (~70px at 1080w).
                               # Override per-render with --font-size PX.
TEXT_WIDTH_RATIO = 0.90        # max text width as a fraction of frame width
STROKE_RATIO = 0.045           # black outline width, as a fraction of font size

LINE_SPACING_MULT = 1.15       # spacing between wrapped lines of the SAME beat
PARAGRAPH_GAP_MULT = 1.15      # EXTRA spacing added before each NEW beat — 1.15 =
                               # exactly one blank line (the "double break")

SCRIM_OPACITY = 0.65           # 0..1, uniform BLACK darkening over the background
                               # photo. Override per-render with --scrim.
                               # 0.40 is barely visible on a bright photo.
                               # Useful range 0.55-0.75.

SPLIT_SENTENCES = True         # each sentence = its own beat (own paragraph gap)
MAX_CHUNK_WORDS = 18           # SOFT cap: blocks break at spoken pauses (see
                               # chunk_sentence), never by length — except a run
                               # with no pause point at all longer than this,
                               # which is cut once at a preposition. Override
                               # with --max-words; 0 = never.
MIN_CHUNK_WORDS = 3            # smallest side a soft-cap cut may leave

LEADING_SILENCE_DB = -50       # quieter than this at the very start = "silence"
LEADING_SILENCE_MIN = 0.3      # seconds — anything shorter is left alone

READING_WPS = 2.2              # words/sec scroll pace (~132 wpm) — slower = more dramatic
MIN_DURATION = 6.0             # seconds, floor for very short scripts
START_Y_RATIO = 0.55           # text block's starting top position (fraction of frame height)

VIDEO_CRF = "23"               # x264 quality (lower = bigger file). 18 made a 12-min
                               # render 589MB; 23 is visually the same for text on a
                               # still photo at roughly half the size.
                               # Override with SCROLL_VIDEO_CRF=18.

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac",
              # messaging-app / voice-memo exports land with these extensions
              ".mpeg", ".mpga", ".ogg", ".opus", ".aiff", ".aif"}


def find_one(folder, exts, kind):
    matches = [p for p in folder.iterdir() if p.suffix.lower() in exts]
    if len(matches) == 0:
        sys.exit(f"No {kind} file found in {folder} (looked for {sorted(exts)})")
    if len(matches) > 1:
        sys.exit(f"Found {len(matches)} {kind} files in {folder}, expected exactly 1: "
                  f"{[p.name for p in matches]}")
    return matches[0]


def cover_resize(img, w, h):
    src_ratio = img.width / img.height
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        new_h = h
        new_w = round(h * src_ratio)
    else:
        new_w = w
        new_h = round(w / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def build_bg_scrim(image_path, tmp_dir, scrim=SCRIM_OPACITY):
    img = Image.open(image_path).convert("RGB")
    img = cover_resize(img, OUT_W, OUT_H)
    black = Image.new("RGB", img.size, (0, 0, 0))
    img = Image.blend(img, black, scrim)
    out_path = tmp_dir / "bg.png"
    img.save(out_path)
    return out_path


def resolve_font_path(weight=DEFAULT_WEIGHT):
    env_override = os.environ.get("SCROLL_VIDEO_FONT")
    if env_override and Path(env_override).exists():
        return env_override, int(os.environ.get("SCROLL_VIDEO_FONT_INDEX", "0"))
    bundled = BUNDLED_FONTS[weight]
    if bundled.exists():
        return str(bundled), BUNDLED_FONT_FACE_INDEX
    for path, index in FALLBACK_FONTS:
        if Path(path).exists():
            return path, index
    sys.exit(
        f"No usable font found. Either restore {bundled.relative_to(_FONT_DIR.parent)} "
        "next to render.py, or set SCROLL_VIDEO_FONT=/path/to/font.ttf"
    )


def load_font(size=None, weight=DEFAULT_WEIGHT):
    size = size or round(OUT_W * FONT_SIZE_RATIO)
    path, index = resolve_font_path(weight)
    return ImageFont.truetype(path, size, index=index), size


def wrap_beat(draw, font, text, max_width):
    words = text.split()
    lines = []
    current = []
    for word in words:
        trial = " ".join(current + [word])
        if draw.textlength(trial, font=font) <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


# Split "Windlass. Strap. Twist. Lock." into four beats, but never split
# "Dr. Smith" or "J. Smith". The lookbehinds are fixed-width because Python's
# re demands it: end-punctuation, or end-punctuation + a closing quote/bracket.
_SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+|(?<=[.!?]["\')\]])\s+')
_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "st.", "jr.", "sr.", "vs.",
                  "etc.", "no.", "u.s.", "a.m.", "p.m.", "e.g.", "i.e."}


def _ends_with_abbreviation(text):
    last = text.split()[-1].lower() if text.split() else ""
    return last in _ABBREVIATIONS or re.fullmatch(r"[a-z]\.", last) is not None


def split_sentences(text):
    parts = [p.strip() for p in _SENTENCE_BREAK.split(text) if p.strip()]
    out = []
    for part in parts:
        if out and _ends_with_abbreviation(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return out


# Spoken-pause splitting, by example:
#
#     I know it was twenty six feet
#     because I have measured it since,
#     more than once,
#     because I have not been able to stop thinking about that number
#
# A break goes wherever a person reading aloud would breathe — after a
# comma/semicolon/colon/dash, and before a word that starts a new clause
# ("because", "and", "until"). Never mid-phrase: no cutting at prepositions
# ("to stop thinking" stays whole), no word cap forcing a break. Words that
# often start a RESTRICTIVE clause ("that", "who", "which", "so", "as",
# "than", "like", "or") are left out — "the doctor who stitched me up" must
# not break. The only fallback is MAX_CHUNK_WORDS: a run with no pause point
# at all past that length is cut once, at the preposition nearest its middle.
_PAUSE_WORDS = {"because", "and", "but", "yet", "until", "while", "when",
                "where", "if", "unless", "after", "before", "then", "though",
                "although", "since", "once", "whether"}
_PREP_WORDS = {"from", "to", "with", "without", "in", "on", "at", "for", "into",
               "through", "over", "under", "by", "about", "across", "toward",
               "towards", "against", "between", "during", "inside", "outside"}
_CLAUSE_PUNCT = re.compile(r'(?<=[,;:\u2014\u2013])\s+|\s+-\s+')
_WORD_STRIP = "\"'()"


def _split_at_pauses(words):
    """One block per spoken clause: break before a pause word, provided the
    block so far has at least 3 words and at least 2 words follow."""
    blocks, current = [], []
    for i, word in enumerate(words):
        if (len(current) >= 3 and len(words) - i >= 2
                and word.lower().strip(_WORD_STRIP) in _PAUSE_WORDS):
            blocks.append(current)
            current = []
        current.append(word)
    if current:
        blocks.append(current)
    return blocks


def _best_cut(words, vocab):
    """Index to cut BEFORE, choosing the vocab word closest to the middle that
    leaves at least MIN_CHUNK_WORDS on each side; None if there is none."""
    n = len(words)
    best = None
    for i in range(MIN_CHUNK_WORDS, n - MIN_CHUNK_WORDS + 1):
        if words[i].lower().strip(_WORD_STRIP) in vocab:
            if best is None or abs(i - n / 2) < abs(best - n / 2):
                best = i
    return best


def _soft_cap(words, max_words):
    if max_words <= 0 or len(words) <= max_words:
        return [words]
    cut = _best_cut(words, _PREP_WORDS)
    if cut is None:
        return [words]
    return _soft_cap(words[:cut], max_words) + _soft_cap(words[cut:], max_words)


def chunk_sentence(sentence, max_words=MAX_CHUNK_WORDS):
    """Break one sentence into blocks at spoken pauses: punctuation first,
    then clause-starting words. Short sentences with no pause pass through."""
    pieces = [p.strip() for p in _CLAUSE_PUNCT.split(sentence) if p and p.strip()]
    out = []
    for piece in pieces:
        for block in _split_at_pauses(piece.split()):
            for capped in _soft_cap(block, max_words):
                out.append(" ".join(capped))
    return out


def split_beats(text, max_words=MAX_CHUNK_WORDS):
    return [chunk for sentence in split_sentences(text)
            for chunk in chunk_sentence(sentence, max_words)]


def render_text_image(beats, font, font_size, tmp_dir):
    scratch = Image.new("RGBA", (OUT_W, 10))
    draw = ImageDraw.Draw(scratch)
    max_width = round(OUT_W * TEXT_WIDTH_RATIO)

    line_h = round(font_size * LINE_SPACING_MULT)
    gap = round(font_size * PARAGRAPH_GAP_MULT)

    rows = []  # (text, extra_gap_before)
    for beat_idx, beat in enumerate(beats):
        wrapped = wrap_beat(draw, font, beat, max_width)
        for line_idx, line in enumerate(wrapped):
            extra = gap if (line_idx == 0 and beat_idx > 0) else 0
            rows.append((line, extra))

    total_height = sum(line_h + extra for _, extra in rows) + line_h
    canvas = Image.new("RGBA", (OUT_W, total_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    stroke_w = max(1, round(font_size * STROKE_RATIO))
    y = 0
    for line, extra in rows:
        y += extra
        draw.text(
            (OUT_W // 2, y), line, font=font,
            fill="white", stroke_width=stroke_w, stroke_fill="black",
            anchor="ma",
        )
        y += line_h

    return canvas, total_height


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def leading_silence(audio_path):
    """Seconds of silence at the very start of the track (0.0 if it opens
    with sound, or if the whole file is silent)."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio_path),
         "-af", f"silencedetect=noise={LEADING_SILENCE_DB}dB:d={LEADING_SILENCE_MIN}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    first_start = re.search(r"silence_start:\s*(-?[\d.]+)", out.stderr)
    first_end = re.search(r"silence_end:\s*([\d.]+)", out.stderr)
    if not first_start or float(first_start.group(1)) > 0.05 or not first_end:
        return 0.0
    return float(first_end.group(1))


def prepare_audio(audio_path, tmp_dir):
    """Cut any leading silence so the music starts at 0:00. Returns the path
    to use from here on (the original if there was nothing to cut). Done as
    a separate file — not an -ss on the looped input — because a looped input
    seeks back to the real start of the file on every pass, silence included."""
    skip = leading_silence(audio_path)
    if skip <= 0:
        return audio_path
    trimmed = tmp_dir / "audio_trimmed.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{skip:.3f}",
         "-i", str(audio_path), "-vn", "-c:a", "pcm_s16le", str(trimmed)],
        check=True, capture_output=True,
    )
    print(f"  cut {skip:.1f}s of silence off the start of the music", flush=True)
    return trimmed


LOOP_CROSSFADE = 2.0           # seconds; when the script outlasts the music the
                               # track repeats with this crossfade at the seam
                               # instead of a hard cut (2026-09-04, 10-ad batch)


def build_fitted_audio(audio_path, duration, tmp_dir):
    src_dur = ffprobe_duration(audio_path)
    out_path = tmp_dir / "audio_fit.m4a"
    fade_start = max(0.0, duration - 1.0)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if src_dur >= duration:
        cmd += ["-i", str(audio_path), "-filter_complex",
                f"[0:a]afade=t=out:st={fade_start:.3f}:d=1[a]"]
    else:
        # chain N copies with a crossfade at every seam, then trim
        xf = min(LOOP_CROSSFADE, src_dur / 4)
        copies = int(duration // (src_dur - xf)) + 2
        for _ in range(copies):
            cmd += ["-i", str(audio_path)]
        chain, prev = [], "[0:a]"
        for k in range(1, copies):
            out = f"[x{k}]"
            chain.append(f"{prev}[{k}:a]acrossfade=d={xf:.3f}:c1=tri:c2=tri{out}")
            prev = out
        chain.append(f"{prev}afade=t=out:st={fade_start:.3f}:d=1[a]")
        cmd += ["-filter_complex", ";".join(chain)]
        print(f"  music ({src_dur:.0f}s) is shorter than the video ({duration:.0f}s) "
              f"— repeating with a {xf:.0f}s crossfade", flush=True)
    cmd += ["-map", "[a]", "-t", f"{duration:.3f}",
            "-c:a", "aac", "-b:a", "192k", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


# Above this text-image height, ffmpeg's overlay filter re-reads the whole
# (very tall) PNG on every output frame and slows to a crawl — a 3-second clip
# off a 126,000px-tall text image took over 2 minutes. Past the threshold we
# pump frames from Pillow instead, which only ever touches the visible slice.
TALL_TEXT_PX = 8000


def render_via_frame_pump(bg_path, txt, text_h, duration, audio_fit, out_path):
    """Scroll by cropping a window out of the tall text canvas, frame by frame,
    and piping raw frames straight into ffmpeg. Cost per frame is O(screen),
    not O(whole text image).

    `txt` is the in-memory RGBA canvas. It is deliberately NOT round-tripped
    through a PNG: at these heights the canvas is ~0.5GB, and open+convert
    would hold a second copy and get the process OOM-killed on a small box.
    """
    bg = Image.open(bg_path).convert("RGB")

    start_y = OUT_H * START_Y_RATIO
    speed = (start_y + text_h) / duration       # px/sec
    n_frames = max(1, round(duration * FPS))

    # Encode to a sidecar file and only move it into place on success, so an
    # interrupted long render can never leave a half-written output.mp4 that
    # looks finished.
    # keep the real extension — ffmpeg picks the muxer from it
    partial = out_path.with_name(f"{out_path.stem}.partial{out_path.suffix}")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{OUT_W}x{OUT_H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio_fit),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264",
        "-crf", os.environ.get("SCROLL_VIDEO_CRF", VIDEO_CRF),
        "-preset", os.environ.get("SCROLL_VIDEO_PRESET", "medium"),
        "-threads", os.environ.get("SCROLL_VIDEO_THREADS", "0"),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(partial),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n_frames):
            y = round(start_y - (i / FPS) * speed)
            frame = bg.copy()
            src_top = max(0, -y)
            dst_top = max(0, y)
            visible = min(text_h - src_top, OUT_H - dst_top)
            if visible > 0:
                slice_ = txt.crop((0, src_top, OUT_W, src_top + visible))
                frame.paste(slice_, (0, dst_top), slice_)
            proc.stdin.write(frame.tobytes())
            if i and i % (FPS * 30) == 0:
                pct = 100 * i / n_frames
                print(f"  ...{pct:.0f}%  ({i//FPS}s of {n_frames//FPS}s)", flush=True)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    rc = proc.wait()
    if rc != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed during frame-pump render (exit {rc})")
    os.replace(partial, out_path)


def has_job_files(folder):
    def count(exts):
        return sum(1 for p in folder.iterdir() if p.suffix.lower() in exts)
    return count(IMAGE_EXTS) >= 1 and count(AUDIO_EXTS) >= 1 and count({".txt"}) >= 1


def write_preview(bg_path, txt, text_h, duration, at_sec, out_png):
    """One frame at `at_sec`, as a PNG — for eyeballing font size / scrim
    without waiting for a full render."""
    bg = Image.open(bg_path).convert("RGB")
    start_y = OUT_H * START_Y_RATIO
    speed = (start_y + text_h) / duration
    y = round(start_y - at_sec * speed)
    frame = bg.copy()
    src_top = max(0, -y)
    dst_top = max(0, y)
    visible = min(text_h - src_top, OUT_H - dst_top)
    if visible > 0:
        slice_ = txt.crop((0, src_top, OUT_W, src_top + visible))
        frame.paste(slice_, (0, dst_top), slice_)
    frame.save(out_png)


def process_folder(folder, wps, out_path=None, scrim=SCRIM_OPACITY,
                   sync_audio=False, split_sentences_flag=SPLIT_SENTENCES,
                   font_size_px=None, preview_at=None,
                   max_words=MAX_CHUNK_WORDS, weight=DEFAULT_WEIGHT):
    image_path = find_one(folder, IMAGE_EXTS, "image")
    audio_path = find_one(folder, AUDIO_EXTS, "audio")
    text_path = find_one(folder, {".txt"}, "text")

    beats = [line.strip() for line in text_path.read_text().splitlines() if line.strip()]
    if not beats:
        sys.exit(f"{text_path} has no non-blank lines")
    if split_sentences_flag:
        beats = [chunk for beat in beats for chunk in split_beats(beat, max_words)]

    out_path = out_path or (folder / "output.mp4")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        font, font_size = load_font(font_size_px, weight)
        bg_path = build_bg_scrim(image_path, tmp_dir, scrim)
        text_canvas, text_h = render_text_image(beats, font, font_size, tmp_dir)

        # trim leading silence BEFORE measuring, so --sync-audio paces the
        # scroll to the music that is actually heard
        audio_path = prepare_audio(audio_path, tmp_dir)

        total_words = sum(len(b.split()) for b in beats)
        if sync_audio:
            duration = ffprobe_duration(audio_path)
        else:
            duration = max(total_words / wps, MIN_DURATION)

        start_y = OUT_H * START_Y_RATIO
        end_y = -text_h
        distance = start_y - end_y
        speed = distance / duration  # px/sec

        if preview_at is not None:
            png = out_path.with_name(f"{out_path.stem}.preview-{preview_at:g}s.png")
            write_preview(bg_path, text_canvas, text_h, duration, preview_at, png)
            print(f"wrote {png}  (frame at {preview_at:g}s of {duration:.1f}s, "
                  f"{len(beats)} beats, font {font_size}px {weight}, scrim {scrim:.0%})")
            return

        audio_fit = build_fitted_audio(audio_path, duration, tmp_dir)

        if text_h > TALL_TEXT_PX:
            print(f"  tall text ({text_h}px) — using frame-pump renderer", flush=True)
            render_via_frame_pump(bg_path, text_canvas, text_h, duration,
                                  audio_fit, out_path)
            print(f"wrote {out_path}  ({duration:.1f}s, {len(beats)} beats, "
                  f"font {font_size}px {weight}, scrim {scrim:.0%})")
            return

        # short-text path: hand the canvas to ffmpeg as a PNG and let the
        # overlay filter do the scroll (proven, and fast at these sizes)
        text_png = tmp_dir / "text.png"
        text_canvas.save(text_png)
        text_canvas.close()

        filter_complex = (
            f"[0:v]scale={OUT_W}:{OUT_H}[bg];"
            f"[1:v]scale={OUT_W}:-1[txt];"
            f"[bg][txt]overlay=x=0:y='{start_y:.2f}-t*{speed:.4f}':eval=frame:format=auto[v]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(FPS), "-loop", "1", "-i", str(bg_path),
            "-framerate", str(FPS), "-loop", "1", "-i", str(text_png),
            "-i", str(audio_fit),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "2:a",
            "-t", f"{duration:.3f}",
            "-r", str(FPS),
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-crf", os.environ.get("SCROLL_VIDEO_CRF", VIDEO_CRF),
            "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    print(f"wrote {out_path}  ({duration:.1f}s, {len(beats)} beats, "
          f"font {font_size}px {weight}, scrim {scrim:.0%})")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", type=Path,
                     help="a single job folder (bg+audio+txt), OR a parent folder "
                          "full of job subfolders for bulk mode")
    ap.add_argument("--wps", type=float, default=READING_WPS,
                     help="scroll pace in words/sec (default %(default)s)")
    ap.add_argument("--out", type=Path, default=None,
                     help="output path, single-job mode only (default: <folder>/output.mp4)")
    ap.add_argument("--scrim", type=float, default=SCRIM_OPACITY,
                     help="black overlay strength over the photo, 0..1 "
                          "(default %(default)s; useful range 0.55-0.75)")
    ap.add_argument("--sync-audio", action="store_true",
                     help="set the scroll length from the audio track's own "
                          "duration (after leading silence is cut) instead of "
                          "the word count — the music then plays exactly once")
    ap.add_argument("--font-size", type=int, default=None, metavar="PX",
                     help=f"caption font size in pixels (default "
                          f"{round(OUT_W * FONT_SIZE_RATIO)})")
    ap.add_argument("--weight", choices=sorted(BUNDLED_FONTS), default=DEFAULT_WEIGHT,
                     help="bundled Poppins weight for the captions (default "
                          "%(default)s)")
    ap.add_argument("--keep-lines", action="store_true",
                     help="one beat per line of the text file, instead of "
                          "splitting every sentence into its own beat")
    ap.add_argument("--max-words", type=int, default=MAX_CHUNK_WORDS, metavar="N",
                     help="soft cap: a run with no spoken pause point longer "
                          "than N words is cut once at a preposition (default "
                          "%(default)s; 0 = never)")
    ap.add_argument("--preview", type=float, default=None, metavar="SEC",
                     help="don't render the video; write one PNG frame at SEC "
                          "seconds next to where output.mp4 would go")
    args = ap.parse_args()

    if args.font_size is not None and args.font_size < 8:
        sys.exit(f"--font-size must be at least 8px, got {args.font_size}")

    if not 0.0 <= args.scrim <= 1.0:
        sys.exit(f"--scrim must be between 0 and 1, got {args.scrim}")

    root = args.folder.expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    if has_job_files(root):
        process_folder(root, args.wps, args.out, args.scrim, args.sync_audio,
                       not args.keep_lines, args.font_size, args.preview,
                       args.max_words, args.weight)
        return

    jobs = sorted(p for p in root.iterdir() if p.is_dir() and has_job_files(p))
    if not jobs:
        sys.exit(f"{root} is neither a job folder (needs 1 image + 1 audio + 1 .txt) "
                  f"nor a parent of job subfolders")

    print(f"bulk mode: {len(jobs)} job folder(s) found in {root}")
    ok, failed = [], []
    for job in jobs:
        try:
            process_folder(job, args.wps, None, args.scrim, args.sync_audio,
                           not args.keep_lines, args.font_size, args.preview,
                           args.max_words, args.weight)
            ok.append(job)
        except Exception as e:
            print(f"FAILED {job.name}: {e}")
            failed.append(job)

    print(f"\ndone: {len(ok)} succeeded, {len(failed)} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
