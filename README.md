# scroll-video

Standalone scroll-caption video maker. Turns a background image, a music
track, and a text file of "beats" into a 9:16 MP4 with the text scrolling
up over the image, karaoke-style — each beat wrapped and spaced like the
long-form scroll-caption ads you see on TikTok/IG.

No automation, no queues. Manual, one-shot tool.

A ready-made sample lives in `example/` (gradient image, 60s instrumental,
short script): `python3 render.py example` renders it to `example/output.mp4`.

## How to use this — no coding required

You don't need to run any commands yourself. Just:

1. **Gather 3 files into one folder:** a photo, a music track, and a text
   file with your script (one line per beat — see the example below).
2. **Open Claude Code in this folder** and say: *"render the video in
   [folder name]"* — Claude Code runs the tool for you.
3. Your finished video (`output.mp4`) shows up in that same folder.

**Want a batch of videos at once?** Put each video's 3 files in its own
folder, put all those folders inside one bigger folder, and tell Claude
Code: *"bulk-render [bigger folder name]"* — it does them all in one pass
and tells you if any failed.

First time on a brand-new computer? Tell Claude Code: *"read SETUP.md and
set this up"* first — one-time only, then the above just works.

Everything below is the technical reference, for Claude Code (or you, if
you ever want to run it by hand from a terminal).

## First-time setup (new machine)

Unzip this folder anywhere (or `git clone` it). Then open a terminal in it
and hand it to Claude Code: **"read SETUP.md and set this up."** That file
has the full dependency checklist (Python, ffmpeg, Pillow) and has Claude
prove it works with a real test render before calling it done. Nothing
else needs installing beyond what SETUP.md walks through.

## Folder setup — how a "job" is laid out

Every render needs exactly 3 files sitting together in one folder:

```
my-video/
├── background.jpg     ← ONE image  (.jpg / .jpeg / .png / .webp)
├── music.mp3           ← ONE audio (.mp3 / .wav / .m4a / .aac / .flac)
└── script.txt          ← ONE text file — the captions
```

Filenames don't matter — the script finds them by extension. There must be
**exactly one of each type** in the folder (it errors out, naming the
problem, if it finds zero or more than one of any type).

`script.txt` is one beat (sentence/line) per line. Blank lines are ignored.
**Every sentence becomes its own beat, and a break goes wherever a person
reading aloud would breathe**: after a comma, semicolon, colon or dash, and
before a word that starts a new clause ("because", "and", "but", "until",
"when", "before"). Never mid-phrase — "to stop thinking about that number"
stays whole no matter how long, and words that usually start a restrictive
clause ("that", "who", "which", "so", "as", "than", "like", "or") never break.
The only length rule is a soft cap: a run with no pause point at all past
`--max-words` (default 18) is cut once at the preposition nearest its middle
(`0` = never). Pass `--keep-lines` to go back to one beat per line exactly as
typed. Anything still too wide auto-wraps:

```
This is the first beat, it can wrap onto multiple lines automatically.
This is a second beat. And this third one renders as its own block too.
Short beats work fine too.
```

**Music starts at 0:00.** Any silence at the very start of the audio file is
cut off automatically before the render (messaging-app audio exports can open
with 30s+ of dead air). With `--sync-audio` the video length is the length of the music
that is actually heard. Without it, a script that outlasts the track repeats
the music with a 2-second crossfade at the seam, then fades out with the video.

## Usage

**Single job** — point it straight at a job folder like the one above:

```
python3 render.py /path/to/my-video
```

Output: `my-video/output.mp4`

**Bulk mode** — point it at a parent folder full of job subfolders (each
one laid out exactly like the example above) and it renders all of them
in one run:

```
parent-folder/
├── video-1/   (image + audio + script.txt)
├── video-2/   (image + audio + script.txt)
└── video-3/   (image + audio + script.txt)
```

```
python3 render.py /path/to/parent-folder
```

Each subfolder gets its own `output.mp4`. Failures in one job don't stop
the rest — it reports a per-job pass/fail summary at the end.

**Options:**

```
python3 render.py <folder> --wps 2.5        # faster/slower scroll (words/sec)
python3 render.py <folder> --sync-audio     # video length = music length, plays once
python3 render.py <folder> --scrim 0.45     # black overlay strength, 0..1
python3 render.py <folder> --font-size 80   # caption size in px (default 70)
python3 render.py <folder> --weight medium  # Poppins weight: black | bold | semibold | medium (default bold)
python3 render.py <folder> --max-words 0    # soft cap for pause-less runs (default 18; 0 = never cut)
python3 render.py <folder> --keep-lines     # one beat per LINE exactly as typed, no splitting
python3 render.py <folder> --preview 120    # one PNG frame at 120s, no video
python3 render.py <job-folder> --out /custom/path.mp4   # single-job only
```

`--scrim` darkens the background photo so the white captions stay readable.
Default is `0.65` (a clear semi-opaque black overlay); the useful range is
`0.55` (photo stays punchy) to `0.75` (maximum legibility). It works in bulk
mode too and applies to every job in the run. On photos that are already
near-black, the scrim mostly dims your subject rather than helping
legibility — reach for the low end there.

`--preview SEC` is the fast way to judge font size and scrim: it writes
`output.preview-120s.png` next to where the video would go, in seconds instead
of minutes. Tune with `--font-size` / `--scrim`, then drop `--preview` to
render for real.

## Tuning the look

All the knobs (font size, scroll speed, stroke width, scrim darkness, output
resolution) are constants at the top of `render.py` under
`# ---- tunables ----`, with comments. Edit and re-run — no config file.

## Font

Ships with four bundled weights of Poppins (OFL-licensed, in `fonts/`) so the
output looks identical on every machine — it does not depend on fonts already
installed on your system. Default is **Bold** (700): the original Black (900)
was too heavy next to the scrolling reference, and Medium (500) is the
reference's own weight, so Bold sits in the middle. Pick another with:

```
python3 render.py <folder> --weight semibold     # black | bold | semibold | medium
```

To use a different font altogether, set:

```
SCROLL_VIDEO_FONT=/path/to/font.ttf python3 render.py <folder>
```

## Changelog (from the first version)

- Caption font 108px → 70px (`FONT_SIZE_RATIO` 0.10 → 0.065; `--font-size` added).
- Caption weight Black → Bold, with SemiBold and Medium bundled too (`--weight`).
- Every sentence is its own beat with a full blank line before it (`--keep-lines` restores per-line beats).
- Blocks break at spoken pauses (punctuation + clause-starting words), never mid-phrase; soft cap 18 for pause-less runs (`--max-words`).
- Leading silence is cut from the music so sound starts at 0:00; `--sync-audio` measures the trimmed track.
- Scrim default 0.40 → 0.55 → 0.65 so the black overlay is clearly visible over a bright photo.
- `--preview SEC` writes a single frame for fast tuning.
- Music that is shorter than the video repeats with a 2s crossfade instead of a hard cut (`LOOP_CROSSFADE`).
- Encode quality CRF 18 → 23 (`VIDEO_CRF`): about half the file size, no visible loss on text over a still photo.
