"""
cuts each game's still frames out of its clip: the last frame (cutRest), which
a clip that can't play falls back to, and the first (cutPoster) if it's missing.

    python tools/cut_stills.py [--force]

goes off ui/jobs.json, so the names always match what the page asks for, and
takes the biggest cut that's actually in assets/ so the stills are as sharp as
the clip. an existing still is left alone unless --force.

needs: ffmpeg on PATH
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def on_disk(ref):
    # jobs.json paths are relative to ui/, the way the page sees them
    return (UI / ref).resolve() if ref else None


def grab(clip, out, last):
    # -update 1 keeps overwriting one image, so reading the last quarter second
    # leaves the final frame behind. Seeking to the exact end lands past it
    seek = ["-sseof", "-0.25"] if last else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", *seek, "-i", str(clip),
                    "-update", "1", "-q:v", "2", *([] if last else ["-frames:v", "1"]), str(out)],
                   check=True)


def main(force):
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg isn't on PATH")
    games = json.loads((UI / "jobs.json").read_text(encoding="utf-8"))["games"]
    for g in games:
        clip = next((p for p in map(on_disk, (g.get("cut"), g.get("cutMp4"), g.get("cut720")))
                     if p and p.is_file()), None)
        if not clip:
            print(f"{g['id']}: no clip in assets, skipped")
            continue
        for key, last in (("cutRest", True), ("cutPoster", False)):
            out = on_disk(g.get(key))
            if not out or (out.exists() and not force):
                continue
            grab(clip, out, last)
            print(f"{g['id']}: {out.name} from {clip.name}")


if __name__ == "__main__":
    main("--force" in sys.argv[1:])
