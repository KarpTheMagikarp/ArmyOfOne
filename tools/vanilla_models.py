"""
Writes manifests/vanilla/<game>.txt, the character model sizes the HD checks
hold a game up against when there's no backup of ours to diff, off an index
from a clean copy.

    py -3 tools/vanilla_models.py ffxiii  "<game>/white_data/sys/filelist_scru.win32.bin"
    py -3 tools/vanilla_models.py ffxiii2 "<game>/alba_data/sys/filelistu.win32.bin"

Only from a copy Steam has just verified. The checks only ever ask whether a
model is bigger than this says, so a table taken off a modded game can't make
anything read as installed, but it does go blind to whatever it was taken over.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from patcher.archive.filelist import load  # noqa: E402
from patcher.mods.hd_layout import XIII, XIII2  # noqa: E402
from patcher.mods.hd_vanilla import HALVES, parse_path  # noqa: E402

LAYOUTS = {XIII.game: XIII, XIII2.game: XIII2}


def rows(index: Path) -> dict:
    found = {}
    for entry in load(index).entries():
        hit = parse_path(entry.path)
        # a placeholder says nothing about what a real file there weighs, and
        # the Japanese set can ship one where the English has a placeholder
        if hit is None or entry.is_stub:
            continue
        group, member, half = hit
        found.setdefault((group, member), {})[half] = entry.uncompressed
    return found


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in LAYOUTS:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    layout, index = LAYOUTS[sys.argv[1]], Path(sys.argv[2])
    found = rows(index)
    # named the way the game names it, whatever the copy it was read off is called
    variant = "c" if re.search(r"filelist(?:_scr)?c", index.name.lower()) else "u"
    lines = [f"# {layout.title}, the character models in a vanilla Steam copy, read off "
             f"{layout.chr_index.format(variant)}",
             "# group id .trb .imgb, uncompressed bytes, - where vanilla has a placeholder "
             "or nothing"]
    for (group, member), sizes in sorted(found.items()):
        lines.append(" ".join([group, member, *(str(sizes.get(h, "-")) for h in HALVES)]))
    out = Path(__file__).resolve().parent.parent / "manifests" / "vanilla" / f"{layout.game}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{out}: {len(found)} models")
    return 0


if __name__ == "__main__":
    sys.exit(main())
