"""
level the glyph sheet: work out how far each letter sits off the line and
write it into assets/glyphs.json as "dy", which ff13() moves the letter by
with a translate (a background offset would be rounded to whole pixels).

    python tools/level_glyphs.py [assets/ff13_glyphs.png] [assets/glyphs.json]

the letters were cut out of btl_number01.dds by hand and they don't share a
baseline: ink tops run from row 1 to row 5 of the 124px cell. at 13px that's
most of a pixel between neighbours, which is what made a word like ATB look
like its letters were bouncing. the sheet itself stays untouched.

needs: pip install pillow
"""

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SHEET = ROOT / "assets" / "ff13_glyphs.png"
MAP = ROOT / "assets" / "glyphs.json"

# cap height glyphs, so their ink box is the thing to centre. a hyphen or a
# full stop sits where it sits on purpose and only follows the letters' drift
CAPS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!?%+:;")
ALPHA = 128


def ink_rows(alpha, x, w):
    rows = [y for y in range(alpha.height)
            if any(alpha.getpixel((c, y)) >= ALPHA for c in range(x, x + w))]
    return (rows[0], rows[-1]) if rows else None


def half(v):
    return round(v * 2) / 2


def level(sheet, glyphs):
    alpha = Image.open(sheet).convert("RGBA").getchannel("A")
    cell = glyphs["h"]
    drift = {}
    for ch, m in glyphs["g"].items():
        rows = ink_rows(alpha, m["x"], m["w"])
        if rows:
            top, bottom = rows
            # rows are inclusive, so the ink spans [top, bottom + 1)
            drift[ch] = cell / 2 - (top + bottom + 1) / 2
    caps = [d for ch, d in drift.items() if ch in CAPS]
    follow = sum(caps) / len(caps) if caps else 0.0
    for ch, m in glyphs["g"].items():
        dy = half(drift.get(ch, 0.0) if ch in CAPS else follow)
        if dy:
            m["dy"] = dy
        else:
            m.pop("dy", None)
    return glyphs


def main(argv):
    sheet = Path(argv[1]) if len(argv) > 1 else SHEET
    target = Path(argv[2]) if len(argv) > 2 else MAP
    glyphs = json.loads(target.read_text(encoding="utf-8"))
    level(sheet, glyphs)
    target.write_text(json.dumps(glyphs), encoding="utf-8")
    moved = {ch: m["dy"] for ch, m in glyphs["g"].items() if "dy" in m}
    print(f"{target.name}: {len(moved)} of {len(glyphs['g'])} glyphs moved, "
          f"{min(moved.values(), default=0)} to {max(moved.values(), default=0)} px of {glyphs['h']}")


if __name__ == "__main__":
    main(sys.argv)
