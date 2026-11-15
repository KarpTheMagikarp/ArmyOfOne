"""
Pulls XIII's compiled scripts (.clb) out of the script archive into one zip.

Read only: both archive files are opened "rb" and nothing in the game changes.
It's ~235 files and a few MB, the zone scripts plus sys/script, which is where
stations, restart points and every story sequence actually live. Save Anywhere's
safety rules get checked against these instead of guessed from the exe.

    python tools/dump_scripts.py "<XIII folder>"            -> ff13-scripts.zip
    python tools/dump_scripts.py "<XIII folder>" --out x.zip
"""

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from patcher.archive.filelist import load            # noqa: E402
from patcher.mods.hd_zones import read_record        # noqa: E402


def archives(game: Path):
    # u is the English voice set, c the Japanese one. Either can be the only one
    # installed, and their scripts can differ, so take whatever's there
    sys_dir = game / "white_data" / "sys"
    for voice in "uc":
        index, image = sys_dir / f"filelist_scr{voice}.win32.bin", sys_dir / f"white_scr{voice}.win32.bin"
        if index.is_file() and image.is_file():
            yield voice, index, image


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game", type=Path, help="the FINAL FANTASY XIII folder")
    ap.add_argument("--out", type=Path, default=Path("ff13-scripts.zip"))
    args = ap.parse_args()

    found = list(archives(args.game))
    if not found:
        raise SystemExit(f"no filelist_scr*/white_scr* pair under {args.game / 'white_data' / 'sys'}")
    count = total = 0
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as out:
        for voice, index, image in found:
            for entry in load(index).entries():
                if entry.is_stub or not entry.path.lower().endswith(".clb"):
                    continue
                data = read_record(image, entry)
                out.writestr(f"{voice}/{entry.path}", data)
                count += 1
                total += len(data)
    print(f"{count} scripts, {total / 1e6:.1f} MB, from {', '.join(v for v, _, _ in found)} -> {args.out}")


if __name__ == "__main__":
    main()
