# SETUP — for Claude Code

You've been handed this folder (unzipped or cloned) on a machine that has
never run it. Get it working, end to end, then confirm with a real render.
Don't just check that dependencies exist — prove the tool produces a valid
video.

## 1. Check Python

```
python3 --version
```

Need 3.9+. If missing: macOS → `brew install python3`; Debian/Ubuntu →
`sudo apt install python3 python3-pip`; Windows → point the user to
python.org (winget/choco also work: `winget install Python.Python.3.12`).

## 2. Check ffmpeg + ffprobe

```
ffmpeg -version && ffprobe -version
```

Both ship in the same package. If missing:
- macOS: `brew install ffmpeg`
- Debian/Ubuntu: `sudo apt install ffmpeg`
- Fedora: `sudo dnf install ffmpeg`
- Windows: `winget install Gyan.FFmpeg` (or `choco install ffmpeg`), then
  make sure the install added `ffmpeg`/`ffprobe` to PATH — open a new shell
  and re-run the version check before moving on.

## 3. Install the Python dependency

```
pip3 install -r requirements.txt
```

(Just Pillow. If the machine enforces externally-managed Python envs, use a
venv: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.)

## 4. Confirm the bundled font survived the transfer

```
ls -la fonts/Poppins-Black.ttf
```

This file must exist and be non-trivial in size (~150KB). If it's missing
(e.g. a zip tool skipped a subfolder, or only `render.py` got copied over),
the script will fall back to hunting for a system font and may fail
outright on Windows/Linux — get the `fonts/` folder from the original
zip/repo rather than patching around it.

## 5. Prove it renders — don't just check exit codes

Build a throwaway job folder and run a real render:

```bash
mkdir -p /tmp/scroll-video-smoketest
cd /tmp/scroll-video-smoketest
python3 -c "from PIL import Image; Image.new('RGB', (1200, 1600), (60, 90, 140)).save('bg.jpg')"
ffmpeg -y -f lavfi -i "sine=frequency=220:duration=6" -c:a aac test.m4a
cat > script.txt << 'EOF'
This is a setup smoke test.
If this rendered, the tool is working end to end.
EOF
python3 /path/to/scroll-video/render.py /tmp/scroll-video-smoketest
```

Then verify the OUTPUT, not just that the command exited 0:

```
ffprobe -v error -show_entries stream=codec_name,width,height,codec_type \
  -show_entries format=duration -of default=noprint_wrappers=0 \
  /tmp/scroll-video-smoketest/output.mp4
```

Expect: a `video` stream (h264, 1080x1920) and an `audio` stream (aac), and
a duration roughly matching the script length at ~2.2 words/sec (floor 6s).
If any of that is missing or the file is 0 bytes, the render silently
failed upstream — go find why before telling the user it's ready.

## Done

Report back: dependency versions found/installed, and the smoke-test
render's resolution/duration from ffprobe. Then clean up
`/tmp/scroll-video-smoketest`.
