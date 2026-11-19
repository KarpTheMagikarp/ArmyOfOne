"""
Where Save Anywhere hooks and reads XIII's code, so code mods can be kept off it.

The add-on checks every one of these against the bytes it expects before it
hooks anything, and stays off whole if one is different. A Nova code patch
landing on one passes every check of its own and just leaves Save Anywhere
quietly dead, so the patcher turns the pair away up front and says which.
test_save_anywhere keeps this in step with the sites[] table in the add-on.
"""

from pathlib import Path

PATCH_ID = "install_save_anywhere"
ASI = "xiii_save_anywhere.asi"

# (what it is, RVA, bytes the add-on checks there)
SITES = (
    ("pad update", 0x4206C0, 12),
    ("zone state", 0x417850, 11),
    ("zone trigger", 0x40D070, 12),
    ("save point lookup", 0x728EE0, 8),
    ("save point row", 0x43D9C0, 7),
    ("actor lookup", 0x2A18B0, 12),
    ("actor lock", 0x09D1A0, 12),
    ("actor unlock", 0x09D220, 12),
    ("warp", 0x2C3450, 10),
    ("player handle", 0x2C0810, 5),
    ("zone pointer", 0x7EB3A4, 2),
    ("pause bar", 0x546630, 12),
    ("text set", 0x4FC870, 12),
    ("text ctor", 0x4FC7E0, 12),
    ("text dtor", 0x4FC820, 12),
    ("save write", 0x439810, 12),
    ("save read", 0x439F30, 12),
    ("spawn marker", 0x304A80, 12),
    ("station area", 0x1BD1C0, 12),
    ("restart point", 0x7E63D0, 14),
    ("start marker", 0x1D30D0, 12),
    ("story number", 0x76C750, 36),
    ("event flags", 0x768196, 13),
    ("field UI", 0x7D1AC9, 12),
    ("menu mask", 0x75D8CD, 5),
    ("menu mask bits", 0x75D901, 7),
    ("script variables", 0x76807E, 16),
    ("zone flags", 0x7EB290, 47),
    ("area on", 0x3036E0, 9),
    ("area fade in", 0x302950, 11),
    ("area off", 0x303F40, 9),
    ("boxes", 0x2FBC70, 9),
    ("bg anims", 0x2F9750, 9),
    ("layout pieces", 0x2F8DD0, 11),
    ("encounters", 0x1B0210, 7),
    ("map set load", 0x40BC20, 9),
    ("retry", 0x40BA30, 9),
    ("chara create", 0x7CC290, 9),
    ("chara release", 0x7CC4F0, 11),
    ("talk set", 0x7CC580, 9),
    ("talk clear", 0x7CC690, 11),
    ("path edge", 0x7E1050, 9),
    ("cutscene data", 0x47E80, 9),
)


def clash(patches):
    """(patch, site) for the first code patch that writes over one of ours, or None"""
    # any write at all, not just a different one: the add-on wants the
    # game's own bytes there, and a code mod never writes those
    for patch in patches:
        for site, rva, size in SITES:
            if patch.rva < rva + size and rva < patch.end:
                return patch, site
    return None


def installed(install_dir: Path, manifest) -> bool:
    from patcher.engine import install_target_dir

    patch = next((p for p in manifest.patches if p.id == PATCH_ID), None)
    return patch is not None and (install_target_dir(install_dir, patch) / ASI).is_file()


def code_patches(install_dir: Path, manifest) -> Path | None:
    """the folder our code-patch add-on reads .nccp files from, if NovaFix is in the manifest"""
    from patcher.mods import nccp
    from patcher.novafix_config import resolve_addons_dir

    proxy = next((p for p in manifest.patches if p.id == "install_novafix"), None)
    if proxy is None:
        return None
    return resolve_addons_dir(install_dir / (proxy.install_relative_path or "")) / nccp.FOLDER


def code_owner(install_dir: Path, path: Path) -> str:
    """the mod on record for a .nccp in our add-on's folder, or its file name"""
    from patcher.mods import loader_mods
    from patcher.paths import undo_store_for

    for record in loader_mods.records(undo_store_for(install_dir)):
        if record.get("nccp") and any(rel.replace("\\", "/").rsplit("/", 1)[-1].lower()
                                      == path.name.lower() for rel in record["files"]):
            return str(record.get("part") or record.get("name"))
    return path.name


def blocking(install_dir: Path, manifest):
    """(owner, patch, site) for the first .nccp already in that overlaps us, or None"""
    from patcher.mods import nccp

    folder = code_patches(install_dir, manifest)
    if folder is None or not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.nccp")):
        try:
            hit = clash(nccp.parse(path.read_bytes())[0])
        except OSError:
            continue
        if hit is not None:
            return code_owner(install_dir, path), *hit
    return None
