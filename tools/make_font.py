"""
turn a desktop font (.otf/.ttf) into the subset .woff2 the UI loads.

    python tools/make_font.py "Saira[wdth,wght].ttf" saira --wdth 118 --wght 480

writes assets/<name>.woff2 containing only the glyphs this UI can actually
display, around 10 KB. that's the difference between the text appearing
instantly and after a visible pause, since every label waits on it
(font-display:block). check the licence lets you ship it first; Saira is OFL,
so its OFL.txt ships beside it.

--wdth and --wght pin a variable font to one instance. 118 and 480 are where
Saira's widths and stems land on NewRodin Pro DB's, the face the layout was
first drawn in: within 0.5% per cap height on a line of upper or mixed case,
and within a percent on the stems of l and I.

needs: pip install fonttools brotli
"""

from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

# everything the interface can put on screen. latin, the punctuation the help
# strings use, and the few symbols in the markup.
CHARS = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " .,:;!?'\"()[]{}/\\|-_+=*&%#@$<>~^`"
    "\u2019\u2018\u201c\u201d"   # curly quotes, Leviathan's Tears uses one
    "\u2013\u2014"               # en dash, em dash
    "\u2026"                     # ellipsis, the help bar truncates with one
    "\u00d7\u2212\u25a1"         # x, minus, square
    "\u00a9\u00ae\u2122"
    "\u00e0\u00e1\u00e8\u00e9\u00ed\u00f3\u00fa\u00f1\u00fc\u00f6\u00e4"
)


def build(src: Path, out_name: str, axes: dict | None = None) -> Path:
    if not src.is_file():
        raise SystemExit(f"no such font: {src}")

    root = Path(__file__).resolve().parent.parent
    out = root / "assets" / f"{out_name}.woff2"
    out.parent.mkdir(parents=True, exist_ok=True)

    font = TTFont(str(src), fontNumber=0)
    if axes:
        from fontTools.varLib.instancer import instantiateVariableFont

        if "fvar" not in font:
            raise SystemExit(f"{src.name} isn't a variable font, so there's no axis to pin")
        # one static instance, so the @font-face covering 400-900 never has
        # Chromium synthesising bold on top of it
        font = instantiateVariableFont(font, axes)
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.desubroutinize = True          # smaller for CFF/otf sources
    opts.layout_features = ["kern", "liga", "calt"]
    opts.name_IDs = ["*"]               # keep the name table, licences ask for it
    opts.notdef_outline = True
    opts.recalc_bounds = True

    subsetter = subset.Subsetter(options=opts)
    subsetter.populate(text=CHARS)
    subsetter.subset(font)
    font.flavor = "woff2"
    font.save(str(out))

    before = src.stat().st_size / 1024
    after = out.stat().st_size / 1024
    print(f"{src.name}  {before:,.0f} KB  ->  assets/{out.name}  {after:,.1f} KB")
    print(f"kept {len(set(CHARS))} characters")
    return out


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("name", nargs="?")
    ap.add_argument("--wght", type=float)
    ap.add_argument("--wdth", type=float)
    args = ap.parse_args()
    axes = {tag: v for tag, v in (("wght", args.wght), ("wdth", args.wdth)) if v is not None}
    build(args.src, args.name or args.src.stem.lower().replace(" ", "-"), axes)


if __name__ == "__main__":
    main()
