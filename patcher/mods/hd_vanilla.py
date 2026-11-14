"""
What a vanilla Steam copy's character models weigh, for when there's no backup
of ours to ask or the one there isn't vanilla.

The backup answers "is it already in the game" exactly, and only for a game
this app saw clean. A copy moved over from another PC, the mod's own .bat run
before this app was found, an undo folder somebody deleted, a backup an older
build took over a hand install: each leaves no reference or a modded one, and
the badges went on the record and the first install on nothing.

manifests/vanilla holds the sizes, read off the real indexes in tests/data by
tools/vanilla_models.py. Both are clean where it counts. XIII-2's white_imgu has
never been through ff13tool -c, which stores sys/debug/version.txt raw where
Square Enix compressed it, and every character install repacks with it. XIII's
playable characters are the sizes their Setup.bat checks for, and its other
groups match XIII-2's clean copies wherever the two games share a model.

Bigger, never just different, and per id. HD art is bigger art, so a table
that's wrong about a model can only miss it, and the Japanese set can't read
as modded for differing from the English one this was read off. The .trb and
.imgb of an id decide together, the way the seed reads them.
"""

import hashlib
import re
import threading
from pathlib import Path

HALVES = ("trb", "imgb")
MODEL = re.compile(r"^chr/([a-z0-9]+)/([a-z0-9_]+)/bin/\2\.win32\.(trb|imgb)$")


def parse_path(path: str):
    """(group, id, half) for chr/<group>/<id>/bin/<id>.win32.<half>, else None"""
    hit = MODEL.match(path.replace("\\", "/").lower())
    return hit.groups() if hit else None


def model_path(group: str, member: str, half: str) -> str:
    return f"chr/{group}/{member}/bin/{member}.win32.{half}"


def table(layout) -> dict:
    """archive path -> vanilla size, or {} without a table to go on"""
    from patcher.paths import bundled_data_root
    return _read(bundled_data_root() / "manifests" / "vanilla" / f"{layout.game}.txt")


_tables, _sizes, _lock = {}, {}, threading.Lock()


def _read(path: Path) -> dict:
    with _lock:
        known = _tables.get(str(path))
    if known is not None:
        return known
    found = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        parts = line.split()
        if line.startswith("#") or len(parts) != 2 + len(HALVES):
            continue
        for half, size in zip(HALVES, parts[2:]):
            if size.isdigit():
                found[model_path(parts[0], parts[1], half)] = int(size)
    with _lock:
        _tables[str(path)] = found
    return found


def index_sizes(index: Path) -> dict:
    """
    the index's model files, lower cased, as path -> uncompressed. Kept against
    the file's bytes, since the survey, the install and Revert all ask of an
    index that only changes when something is installed
    """
    from patcher.archive.filelist import load

    raw = Path(index).read_bytes()
    key = hashlib.blake2b(raw, digest_size=16).digest()
    with _lock:
        known = _sizes.get(key)
    if known is None:
        known = {}
        for entry in load(Path(index)).entries():
            path = entry.path.replace("\\", "/").lower()
            if parse_path(path) is not None:
                # a path listed twice keeps its first, and the copies agree anyway
                known.setdefault(path, entry.uncompressed)
        with _lock:
            _sizes[key] = known
            while len(_sizes) > 8:
                _sizes.pop(next(iter(_sizes)))
    return known


def _stem(path: str) -> str:
    return path.rsplit(".win32.", 1)[0]


def _grown_stems(sizes: dict, vanilla: dict) -> set:
    return {_stem(path) for path, size in sizes.items() if size > vanilla.get(path, size)}


def grown(sizes: dict, layout) -> frozenset:
    """both halves of every id in `sizes` that has a half bigger than vanilla"""
    stems = _grown_stems(sizes, table(layout))
    return frozenset(path for path in sizes if _stem(path) in stems)


def moved(live: dict, backup: dict | None, layout) -> frozenset:
    """
    both halves of every model in `live` something has replaced: bigger than
    vanilla, or not the backup's copy where that copy isn't bigger itself.
    The backup still catches what the table can't size, and a backup taken
    over a hand install can't make a verified game read as modded.
    """
    vanilla = table(layout)
    stems = _grown_stems(live, vanilla)
    if backup is not None:
        stems |= ({_stem(path) for path, size in live.items()
                   if path in backup and backup[path] != size}
                  - _grown_stems(backup, vanilla))
    return frozenset(path for path in live if _stem(path) in stems)


def grown_in(index: Path, layout) -> frozenset:
    """grown() off an index on disk"""
    return grown(index_sizes(index), layout)


def stuck(paths, live: dict, backup: dict | None, cache: Path, layout) -> set:
    """
    the ids among model `paths` with nothing clean to build them from: bigger
    than vanilla in the game, and in the backup too where the seed would take
    the backup's copy, and not cached clean in models\\orig. The seed takes
    the same view of it (hd_runner), so everything else gets a clean original
    there, a cached copy bigger than vanilla included.
    """
    vanilla = table(layout)
    out = set()
    for path in paths:
        want, size = vanilla.get(path), live.get(path)
        if want is None or size is None:
            continue
        try:
            if (Path(cache) / path).stat().st_size <= want:
                continue
        except OSError:
            pass
        was = None if backup is None else backup.get(path)
        if (was if was is not None and size > was else size) > want:
            out.add(_stem(path))
    return out


def planned(steps) -> set:
    """the model paths a plan's tool steps put into the tree, off where each copy lands"""
    found = set()
    for step in steps:
        for _, landing in getattr(step, "copies", ()) or ():
            # white_scr<v>\chr\..., or white_img<v>\chr\... on XIII-2
            path = landing.replace("\\", "/").split("/", 1)[-1].lower()
            if step.tool is not None and parse_path(path) is not None:
                found.add(path)
    return found


def zone_size(layout, name: str) -> int | None:
    """a zone blob's Steam size, both voice sets, off the tables the mods' own setups check"""
    from patcher.mods.hd_layout import XIII2
    from patcher.mods.hd_preflight import VANILLA2_ZONES, VANILLA_ZONES
    return (VANILLA2_ZONES if layout is XIII2 else VANILLA_ZONES).get(name)


def known(layout) -> bool:
    return bool(table(layout))


def bigger(path: str, size: int, layout) -> bool:
    """one file's own size against vanilla, false for anything the table doesn't list"""
    return size > table(layout).get(path.replace("\\", "/").lower(), size)
