"""
Reading FF XIII-2 HD (MJB, nexusmods.com/finalfantasyxiii2/mods/1).

Same author as XIII's HD Project and a smaller contract. There are no category
lists: the .bat hardcodes five texture folders and fifteen zones, and data/
holds only the model recipes and the two lists naming which ones run.

    data/PCModels.txt, NPCModels.txt   ids, one per line
    data/<id>.txt                      six header lines, three game paths, opcodes
    data/FFXIII_Ultimate_Models.bin    the blob container, same shape as XIII's
    textures/<group>/, textures/z####  the art, looked up by name inside a .trb
    supportfiles/                      ff13tool, FFXIII2_HDProject, FF XIII HD Models

Folder names are matched without case. The zip ships them lower case and the
.bat spells them Data and SupportFiles, which only works because NTFS lets it.
"""

import re
from pathlib import Path

from patcher.mods.hd_runner import usable_id

NAME = "FFXIII-2 HD"
JOB_ID = "hdproject2"
INJECTOR = "FFXIII2_HDProject.exe"
CONTAINER = "FFXIII_Ultimate_Models.bin"
LISTS = ("PCModels.txt", "NPCModels.txt")

# MODTYPE order in their .bat. Exte is seeded and then commented out, and
# the mod ships no textures for it.
GROUPS = ("fa", "npc", "mon", "pc", "weapon")
ZONE = re.compile(r"^z\d{4}$")


class RecipeError(Exception):
    pass


def child(folder: Path, name: str) -> Path | None:
    """a direct child of folder by name, whatever its case"""
    folder = Path(folder)
    exact = folder / name
    if exact.exists():
        return exact
    try:
        for path in folder.iterdir():
            if path.name.lower() == name.lower():
                return path
    except OSError:
        pass
    return None


def _is_home(folder: Path) -> bool:
    data, support = child(folder, "data"), child(folder, "supportfiles")
    if data is None or support is None or not data.is_dir() or not support.is_dir():
        return False
    has_list = any(child(data, name) for name in LISTS)
    return has_list and child(data, CONTAINER) is not None and child(support, INJECTOR) is not None


def find_home(root: Path) -> Path | None:
    """
    the folder the mod unpacked into: the drop itself, or one level down for
    an extractor that wrapped it. Deeper is never searched.
    """
    root = Path(root)
    if not root.is_dir():
        return None
    if _is_home(root):
        return root
    for candidate in sorted(p for p in root.iterdir() if p.is_dir()):
        if _is_home(candidate):
            return candidate
    return None


def detect(root: Path) -> dict | None:
    """what bridge.identify_drop and the install need, or None if this is not the mod"""
    home = find_home(root)
    if home is None:
        return None
    data, support = child(home, "data"), child(home, "supportfiles")
    textures = child(home, "textures")
    return {
        "name": NAME,
        "root": str(home),
        "data": str(data),
        "support": str(support),
        "textures": str(textures) if textures is not None and textures.is_dir() else "",
        "lists": [name for name in LISTS if child(data, name)],
        "containers": [CONTAINER],
        "tools": sorted(p.name for p in support.iterdir() if p.is_file()),
        "has_textures": textures is not None and textures.is_dir(),
        "zones": zones(textures) if textures is not None else [],
        "groups": [g for g in GROUPS if textures is not None and child(textures, g)],
    }


def archive_holds(names: list) -> bool:
    """a zip listing that is this mod, read off its central directory"""
    # at the zip's root, or under the one folder somebody zipped it up in.
    # PowerShell's Compress-Archive writes backslashes, hence the replace
    flat = {n.replace("\\", "/").lower() for n in names}

    def anywhere(tail):
        return any(n == tail or n.endswith("/" + tail) for n in flat)

    return (anywhere("supportfiles/" + INJECTOR.lower())
            and anywhere("data/" + CONTAINER.lower())
            and any(anywhere("data/" + name.lower()) for name in LISTS))


def zones(textures: Path) -> list:
    """
    the zones the mod ships art for, which is exactly the fifteen its .bat
    calls MODZONE on. Read off the folders so a newer release that adds one
    is not silently cut short.
    """
    if textures is None or not Path(textures).is_dir():
        return []
    return sorted(p.name.lower() for p in Path(textures).iterdir()
                  if p.is_dir() and ZONE.match(p.name.lower()))


def model_ids(listing: Path) -> list:
    """
    ids off PCModels.txt or NPCModels.txt, the way `for /f "eol=;"` reads them:
    blank lines and ; comments skipped. Anything that is not a plain id is
    dropped, because it becomes a filename two steps later.
    """
    if listing is None or not Path(listing).is_file():
        return []
    found = []
    for raw in Path(listing).read_text(encoding="latin-1").splitlines():
        text = raw.strip()
        if not text or text.startswith(";"):
            continue
        if usable_id(text) and text.lower() not in found:
            found.append(text.lower())
    return found


def recipe_paths(recipe: Path, group: str, member: str) -> tuple:
    """
    (trb, imgb) a recipe rewrites, as it spells them.

    Found by shape rather than by line number: XIII's recipes have seven header
    lines and these have six, and a count that moves once can move again. The
    paths have to be this id's own model, because the tool writes them under
    models\\mod and the copy puts them in the archive.
    """
    lines = [l.strip() for l in Path(recipe).read_text(encoding="latin-1").splitlines()]
    for at, line in enumerate(lines[:-1]):
        if not line.lower().endswith(".win32.trb"):
            continue
        trb, imgb = line, lines[at + 1]
        expected = "\\".join(["chr", group, member, "bin", member + ".win32"]).lower()
        if (trb.lower() == expected + ".trb" and imgb.lower() == expected + ".imgb"):
            return trb, imgb
        raise RecipeError(f"{Path(recipe).name} rewrites {trb}, which is not {member}'s model")
    raise RecipeError(f"{Path(recipe).name} names no model to rewrite")
