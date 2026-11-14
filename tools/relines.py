"""
swaps the scanlines baked into each game's cut for the ones XIII's own status
screen draws, so the menu text sits on a texture instead of on stripes.

    python tools/relines.py [--strength 0.7] [--fresh]

goes off ui/jobs.json like cut_stills.py, and does every cut it finds in
assets/ (cut, cutMp4, cut720). The first run copies each untouched clip into
.cut-originals/ and every run works from that copy, so a rerun with another
strength starts clean instead of stacking. Rerun cut_stills.py --force after,
so the stills match.

--fresh is for clips exported without any lines (delete .cut-originals/ and
drop them in first). Nothing gets taken out, so nothing gets amplified: each
game's biggest clip is the master, and all three sizes are scaled from it with
the lines added. .cut-originals/ remembers it, so later runs stay fresh.

needs: ffmpeg on PATH and numpy. A media tool for whoever cuts the clips, so
numpy stays out of requirements.txt and the exe
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
ORIGINALS = ROOT / ".cut-originals"

# measured off XIII's status screen at 720p: a darker row, a lighter one and
# one about level, every 3 rows. The lighter row is why it doesn't dim the art
STATUS = np.array([0.819, 1.266, 0.987])


def line_gain(rows, height):
    """
    how much each row is darkened (or lifted) against its neighbours. The old
    lines sit on fixed rows, so a whole clip's row means average the picture
    out and leave just them. The window grows with the clip so a 4K master's
    thicker lines still sit inside it
    """
    window = int(9 * height / 720) | 1
    padded = np.pad(rows, window // 2, mode="edge")
    level = np.median(np.lib.stride_tricks.sliding_window_view(padded, window), axis=1)
    return np.clip(rows / np.maximum(level, 1e-3), 0.2, 1.5)


def status_lines(height, strength):
    # 3 rows a period at 720p, so a 1440p master gets 6 and its 720p cut,
    # scaled down from it, lands back on 3
    period = 3 * height / 720
    phase = np.floor((np.arange(height) % period) / period * 3).astype(int)
    return 1 - strength * (1 - STATUS / STATUS.mean())[phase]


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip().split(",")
    return int(out[0]), int(out[1]), out[2]


def frames(path, w, h, scale=False):
    # scale: decoded straight to w x h, how a master becomes its smaller cuts
    resize = ["-vf", f"scale={w}:{h}:flags=lanczos"] if scale else []
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), *resize, "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-"], stdout=subprocess.PIPE)
    size = w * h * 3
    while (buf := p.stdout.read(size)) and len(buf) == size:
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    p.stdout.close()
    p.wait()


def encoders(dst, log):
    # the webms get docs/ui.md's 720p recipe, two passes. The mp4 is the clip a
    # GPU decodes, so it stays H.264: Chromium won't play HEVC
    if dst.suffix == ".webm":
        vp9 = ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "30", "-row-mt", "1", "-an", "-pix_fmt", "yuv420p",
               "-passlogfile", str(log)]
        return [vp9 + ["-pass", "1", "-f", "null", "-"],
                vp9 + ["-pass", "2", "-cpu-used", "1", str(dst)]]
    return [["-c:v", "libx264", "-crf", "16", "-preset", "slow", "-an", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(dst)]]


def encode(source, dst, w, h, rate):
    """
    source() yields float frames, written through docs/ui.md's recipe for dst's
    type. A callable, since there are two passes and a 4K clip doesn't fit in
    memory twice over
    """
    log = dst.with_suffix(".passlog")
    raw = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", rate, "-i", "-"]
    try:
        for tail in encoders(dst, log):
            enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", *raw, *tail], stdin=subprocess.PIPE)
            for f in source():
                enc.stdin.write(np.clip(f + 0.5, 0, 255).astype(np.uint8).tobytes())
            enc.stdin.close()
            if enc.wait():
                raise RuntimeError(f"ffmpeg couldn't encode {dst.name}")
    finally:
        for p in dst.parent.glob(log.name + "*"):
            p.unlink()


def reline(src, dst, strength):
    w, h, rate = probe(src)
    rows, n = np.zeros(h), 0
    for f in frames(src, w, h):
        rows += f.mean((1, 2))
        n += 1
    gain = (status_lines(h, strength) / line_gain(rows / max(n, 1), h)).astype(np.float32)[:, None, None]
    encode(lambda: (f * gain for f in frames(src, w, h)), dst, w, h, rate)
    return n


def lined(master, dst, w, h, strength):
    """a clean master scaled to w x h with the status screen lines on it, nothing taken out"""
    rate = probe(master)[2]
    gain = status_lines(h, strength).astype(np.float32)[:, None, None]
    encode(lambda: (f * gain for f in frames(master, w, h, scale=True)), dst, w, h, rate)
    return sum(1 for _ in frames(master, 16, 16, scale=True))


# what each cut is when a fresh master has to make it from nothing
SIZES = {"cutMp4": (3840, 2160), "cut": (2560, 1440), "cut720": (1280, 720)}
CLEAN = ORIGINALS / "clean"


def main(strength, fresh=False):
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg isn't on PATH")
    games = json.loads((UI / "jobs.json").read_text(encoding="utf-8"))["games"]
    ORIGINALS.mkdir(exist_ok=True)
    if fresh and not CLEAN.exists():
        # these were copied from lined clips, and adding lines on top of lines
        # is the one way --fresh can go wrong
        if any(p.suffix in (".mp4", ".webm") for p in ORIGINALS.iterdir()):
            sys.exit("delete .cut-originals/ first: it holds the lined clips, and --fresh "
                     "would use them as the clean masters")
        CLEAN.touch()
    fresh = CLEAN.exists()
    for g in games:
        clips = {key: (UI / g[key]).resolve() for key in SIZES if g.get(key)}
        for clip in clips.values():
            original = ORIGINALS / clip.name
            if clip.is_file() and not original.exists():
                shutil.copy2(clip, original)
        if fresh:
            # the tallest clean export is the one every size comes from
            have = [ORIGINALS / c.name for c in clips.values() if (ORIGINALS / c.name).is_file()]
            if not have:
                print(f"{g['id']}: no clip in assets, skipped")
                continue
            master = max(have, key=lambda p: probe(p)[1])
        for key, clip in clips.items():
            original = ORIGINALS / clip.name
            if not fresh and not original.is_file():
                continue
            # written beside it and swapped in, so a failed encode leaves the
            # clip the page plays as it was
            tmp = clip.with_name(clip.stem + ".relining" + clip.suffix)
            if fresh:
                w, h = SIZES[key]
                n = lined(master, tmp, w, h, strength)
            else:
                n = reline(original, tmp, strength)
            tmp.replace(clip)
            print(f"{g['id']}: {clip.name}, {n} frames at strength {strength}"
                  + (f" from {master.name}" if fresh else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="swap the cuts' scanlines for XIII's status screen ones")
    ap.add_argument("--strength", type=float, default=0.7, help="0 takes the lines off, 1 is the status screen's full depth")
    ap.add_argument("--fresh", action="store_true", help="the clips have no lines of their own: only add them")
    args = ap.parse_args()
    main(args.strength, args.fresh)
