"""
Zips the game's own copies of the character models HD Models Plus builds
from, so the native model builder can be checked byte for byte against
FF XIII HD Models.exe on real data. Development tool, not shipped.

    py -3 tools/export_models.py "E:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII"
    py -3 tools/export_models.py "<game>" c005 c595      (just those)

Writes ff13-models.zip here (ff13-models-part.zip for a list). Each half
is the game's original, found the way the drawer's HD Models Plus install
finds it (under any mod of ours, then the HD backup), so a game with HD
Models Plus already in still exports clean.
"""

import hashlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# every model a recipe in HD Models Plus 4.1.7 reads, and c595 for the
# Vanille Nude Mod's own recipe
MODELS = [f"chr/pc/{m}/bin/{m}.win32" for m in (
    "c001", "c002", "c003", "c004", "c005", "c006",
    "c201", "c202", "c203", "c204", "c205", "c206", "c592", "c595", "c604", "c605")] + \
    [f"chr/npc/{m}/bin/{m}.win32" for m in (
        "n900", "n901", "n903", "n905", "n906", "n907", "n909", "n910", "n911", "n912",
        "n913", "n914", "n916")]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    from patcher.mods import models_plus

    game = Path(sys.argv[1])
    wanted = {m.lower() for m in sys.argv[2:]}
    models = [m for m in MODELS if not wanted or m.split("/")[2] in wanted]
    if not models:
        print("none of those are models this exports")
        return 1
    if not (game / "white_data" / "sys" / "filelist_scru.win32.bin").is_file() and \
            not (game / "white_data" / "sys" / "filelist_scrc.win32.bin").is_file():
        print(f"no FINAL FANTASY XIII at {game}")
        return 1
    print("reading the original models out of the game...")
    try:
        found = models_plus.originals(game, models, tick=lambda: print(".", end="", flush=True))
    except models_plus.ModelsError as e:
        print(f"\n{e}")
        return 1
    out = Path("ff13-models-part.zip" if wanted else "ff13-models.zip")
    lines = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_LZMA) as z:
        for path, data in sorted(found.items()):
            z.writestr(path, data)
            lines.append(f"{hashlib.sha256(data).hexdigest()}  {len(data):>9}  {path}")
        z.writestr("SHA256SUMS.txt", "\n".join(lines) + "\n")
    print(f"\nwrote {out.resolve()} ({out.stat().st_size / 1e6:.1f} MB, {len(found)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
