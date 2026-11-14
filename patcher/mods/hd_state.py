"""
What's actually in the game right now, asked of the game, not the record.

The state file goes stale after a Steam verify, a manual restore, a game copied
from another PC or a .bat install, and both the toggles and install()'s
restore-first decision depend on getting it right. So it's a diff: an entry
whose uncompressed size differs between our pre-install backup's index and the
game's is one something replaced. Works the same for a hand-modded game.
"""

import hashlib
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Applied:
    """which categories are in the game, and whether we could tell"""
    categories: set = field(default_factory=set)
    changed: int = 0          # entries differing from vanilla
    verified: bool = False    # False means there was nothing to compare against

    def __bool__(self) -> bool:
        return bool(self.categories)


def changed_paths(live_index: Path, vanilla_index: Path) -> frozenset:
    """
    the archive paths whose contents differ between the two indexes.

    By size, since that's two parses of a 63KB index instead of a pass over
    4GB. A same-length replacement would be missed, which doesn't happen when
    the art is bigger. Cached against the index bytes (hashed, not stat'ed,
    since exFAT timestamps are 10ms and there's no stable inode), because every
    drawer open and survey asks again.
    """
    live, vanilla = Path(live_index).read_bytes(), Path(vanilla_index).read_bytes()
    key = tuple(hashlib.blake2b(raw, digest_size=16).digest() for raw in (live, vanilla))
    with _seen_lock:
        known = _seen.get(key)
    if known is None:
        known = _diff(live, vanilla)
        with _seen_lock:
            _seen[key] = known
            # insertion ordered, so the oldest answer is the first to go
            while len(_seen) > 16:
                _seen.pop(next(iter(_seen)))
    return known


# the drawer, the survey and an install's own thread all ask
_seen, _seen_lock = {}, threading.Lock()


def models_moved(live_index: Path, vanilla_index: Path | None) -> frozenset | None:
    """
    hd_vanilla.moved() for an index pair on disk, the backup's half optional.
    None when there's nothing to tell by: no backup, no table, or an index that
    won't read
    """
    from patcher.mods import hd_vanilla
    from patcher.mods.hd_layout import layout_of

    # <game>/<data>/sys/<index>, which is how it says which game it is
    layout = layout_of(Path(live_index).parents[2])
    try:
        live = without_file_mods(live_index)
    except Exception:
        return None
    backup = None
    if vanilla_index is not None and Path(vanilla_index).is_file():
        try:
            backup = hd_vanilla.index_sizes(vanilla_index)
        except Exception:
            pass
    if backup is None and not hd_vanilla.known(layout):
        return None
    return hd_vanilla.moved(live, backup, layout)


def without_file_mods(index: Path) -> dict:
    """
    hd_vanilla.index_sizes() with what the Mods drawer put in read as what's
    under it. A costume dropped in there is its own mod, and counting it would
    light up an HD category nobody installed
    """
    from patcher.mods import hd_vanilla
    from patcher.mods.game_files import originals

    sizes = hd_vanilla.index_sizes(index)
    under = {p: s for p, s in originals(index).items() if p in sizes}
    return {**sizes, **under} if under else sizes


def _diff(live: bytes, vanilla: bytes) -> frozenset:
    from patcher.archive.filelist import parse

    now = {entry.path.replace("\\", "/").lower(): entry.uncompressed
           for entry in parse(live).entries()}
    was = {entry.path.replace("\\", "/").lower(): entry.uncompressed
           for entry in parse(vanilla).entries()}

    return frozenset(path for path, size in now.items()
                     if path in was and was[path] != size)


def applied_categories(live_index: Path, vanilla_index: Path | None, data_dir: Path,
                       categories: list, mod_root: Path = None) -> Applied:
    """
    which of these categories the game already carries.

    Counts when most of a category's ids changed, not any, since the lists
    overlap and one install would otherwise light up several. Changed is
    against the backup and the shipped vanilla sizes (hd_vanilla.moved), so a
    game with no clean backup still gets an answer. With neither it comes back
    unverified instead of empty, since "couldn't tell" and "nothing installed"
    want opposite handling.
    """
    changed = models_moved(live_index, vanilla_index)
    if changed is None:
        return Applied()

    # what the mod can actually change, when the mod folder is at hand. Without
    # it this falls back to counting everything a category lists, which is the
    # behaviour that made Eidolons permanently undetectable.
    art = shipped_art(mod_root, data_dir) if mod_root else {}

    found = Applied(changed=len(changed), verified=True)
    for category in categories:
        wanted = _model_paths(Path(data_dir), category, art)
        if not wanted:
            continue
        hits = len(wanted & changed)
        # still most rather than any, because the point of the threshold is
        # that one shared id cannot light up a category nobody installed
        if hits * 2 > len(wanted):
            found.categories.add(category["id"])
    return found


# the party, by the two ids Playable Characters lists each of them under: c0NN
# in the field and c2NN in battle. HD Models Plus comes one file per person
PARTY = tuple((f"c{n:03d}", f"c{200 + n}") for n in range(1, 7))


def covered_categories(live_index: Path, vanilla_index: Path | None, data_dir: Path,
                       categories: list, mod_root: Path = None, applied=()) -> dict:
    """
    category id -> the Mods drawer mods it's in from, for the party category,
    when all six are modded (HD Models Plus and Playable Characters are two
    takes on the same six). Counted by person, not by ids (34 on XIII), and
    all six since with five the HD Project still has one to build. Modded
    outside the drawer counts too, with no names. Separate from
    applied_categories, which is only what the HD Project put in.
    """
    from patcher.mods import hd_vanilla
    from patcher.mods.game_files import held, holders

    party = {member for pair in PARTY for member in pair}
    changed = models_moved(live_index, vanilla_index) or frozenset()
    theirs = held(live_index)
    out = {}
    for category in categories:
        if not category.get("mesh") or category["id"] in applied:
            continue
        wanted = {p for p in _model_paths(Path(data_dir), category)
                  if (hd_vanilla.parse_path(p) or ("", ""))[1] in party}
        modded = {hd_vanilla.parse_path(p)[1] for p in wanted & (changed | theirs)}
        listed = {hd_vanilla.parse_path(p)[1] for p in wanted}
        if not all(set(pair) & listed and set(pair) & modded for pair in PARTY):
            continue
        install_dir = Path(live_index).parents[2]
        out[category["id"]] = sorted(holders(
            install_dir, wanted & theirs,
            index=Path(live_index).relative_to(install_dir).as_posix()) if theirs else ())
    return out


def zones_installed(game_dir: Path, variant: str, backup_dir: Path,
                    data_dir: Path, category: dict) -> bool:
    """
    whether a zone category is already in the game.

    The scr index can't say: a zone's blob sits loose under white_data\\zone
    and gets replaced whole, so it's compared against our backup's copy. Only
    a zone install backs one up, so being in the backup at all means we
    installed it; the compare covers a restore that left the backup behind.
    blob_moved checks VANILLA_ZONES sizes first.
    """
    from patcher.mods import hd_vanilla
    from patcher.mods.hd_layout import XIII
    from patcher.mods.hd_recipe import read_category
    from patcher.mods.hd_zones import zone_ids

    listing = Path(data_dir) / category.get("list", "")
    if not listing.is_file():
        return False

    zone_dir, changed, seen = Path(game_dir) / "white_data" / "zone", 0, 0
    for zone in zone_ids(read_category(listing).members):
        name = f"white_{zone}{variant}_img.win32.bin"
        moved = blob_moved(zone_dir / name, Path(backup_dir) / name,
                           hd_vanilla.zone_size(XIII, name))
        if moved is None:
            continue
        seen += 1
        changed += moved
    # most, not any. The category is all twenty or nothing, so one changed
    # blob is a run that stopped partway rather than a category that is in.
    # Reading it as "in" locks the row and leaves somebody unable to install
    # the other nineteen, which is exactly the state a live run left behind
    # when it died on zone one of twenty.
    return changed * 2 > seen if seen else False


def blob_moved(live: Path, backup: Path, vanilla: int | None) -> bool | None:
    """
    whether a zone blob isn't what the game shipped, or None with nothing to
    tell by. The Steam size decides when there is one: it's exact for each
    voice set, and a backup taken over a hand install would call a verified
    blob modded
    """
    if not Path(live).is_file():
        return None
    size = Path(live).stat().st_size
    if vanilla is not None:
        return size != vanilla
    if Path(backup).is_file():
        return size != Path(backup).stat().st_size
    return None


# what each category's install pipeline is on. Bump one when a fix changes the
# bytes a run produces, and give the bump a line saying what was wrong: an
# install recorded below the current number was built by code we now know got
# it wrong, and that is worth saying without waiting for somebody to find it in
# game. Anything not named here is on 1 and has never needed a correction.
PIPELINE = {"zones": 2}
PIPELINE_FROM = 1
PIPELINE_WHY = {
    ("zones", 1): "installed before the .imgb half of each block was being "
                  "imported, so these zones carry HD texture headers over "
                  "standard pixel data. That is the coloured static.",
}


def stale_pipelines(recorded: dict, installed) -> dict:
    """
    installed categories whose pipeline has been fixed since, as id -> why.
    Covers fixes that leave nothing on disk to detect. No stamp at all reads
    as the oldest, since that's who a first bump is aimed at.
    """
    stamps = (recorded or {}).get("pipeline") or {}
    stale = {}
    for category in installed or []:
        current = PIPELINE.get(category, PIPELINE_FROM)
        try:
            was = int(stamps.get(category, PIPELINE_FROM))
        except (TypeError, ValueError):
            was = PIPELINE_FROM
        if was >= current:
            continue
        why = PIPELINE_WHY.get((category, was))
        stale[category] = why or ("installed by an older build whose pipeline "
                                  "has been corrected since.")
    return stale


def _zone_index(sys_dir: Path, variant: str, zone: str) -> dict:
    """
    one zone's index, out of the white_img it lives inside, as path -> size.

    Nested, which is the whole reason this is its own function: a zone's blob
    is loose on disk but its index is an entry inside white_img<v>, so getting
    at it means parsing that archive's index to find the offset of another
    index and inflating what is there.
    """
    from patcher.archive.filelist import load, parse
    from patcher.mods.hd_zones import read_entry

    image = sys_dir / f"white_img{variant}.win32.bin"
    listing = sys_dir / f"filelist{variant}.win32.bin"
    if not image.is_file() or not listing.is_file():
        return {}
    entries = list(load(listing).entries())
    raw = read_entry(image, entries, f"zone\\filelist_{zone}{variant}.win32.bin")
    return {e.path.replace("\\", "/").lower(): e.uncompressed
            for e in parse(raw).entries()}


def half_written_zones(game_dir: Path, variant: str, backup_dir: Path,
                       data_dir: Path, category: dict) -> list:
    """
    zones carrying HD texture headers over standard pixel data.

    A block's .trb holds texture headers and its .imgb the pixels they point
    at; one without the other renders as coloured static. No correct run
    changes the .trb and not the .imgb, so that's the test, per block, and
    most of a zone doing it (not any) is a pipeline that skipped a step.
    """
    from patcher.mods.hd_recipe import read_category
    from patcher.mods.hd_zones import zone_ids

    listing = Path(data_dir) / category.get("list", "")
    backup_dir = Path(backup_dir)
    if not listing.is_file() or not backup_dir.is_dir():
        return []

    sys_dir = Path(game_dir) / "white_data" / "sys"
    found = []
    for zone in zone_ids(read_category(listing).members):
        try:
            live = _zone_index(sys_dir, variant, zone)
            was = _zone_index(backup_dir, variant, zone)
        except Exception:
            # an index that will not parse is a different problem and not one
            # this can speak to. Saying nothing beats claiming damage.
            continue
        if not live or not was:
            continue

        touched = split = 0
        for path, size in live.items():
            if not path.endswith(".win32.trb") or path not in was:
                continue
            pixels = path[:-len(".trb")] + ".imgb"
            if pixels not in live or pixels not in was:
                continue
            moved = was[path] != size
            pixels_moved = was[pixels] != live[pixels]
            if moved or pixels_moved:
                touched += 1
            # only this direction. The reverse, pixels replaced at the same
            # dimensions so the headers did not need to move, is something a
            # texture pack can legitimately do.
            if moved and not pixels_moved:
                split += 1
        if touched and split * 2 > touched:
            found.append(zone)
    return found


ART_ID = re.compile(r"^([A-Za-z]\d{3})")


def shipped_art(mod_root: Path, data_dir: Path) -> dict:
    """
    the ids the mod can actually change, by group, or {} if it can't be told.
    A Data recipe names one outright; the rest go by path against the art in
    textures\\<group>. A third of the listed ids ship no art, and Eidolons is
    mostly those, so counting by everything listed would never read it as
    installed.
    """
    root = Path(mod_root) / "textures"
    if not root.is_dir():
        return {}

    found = {}
    for group in sorted(p for p in root.iterdir() if p.is_dir()):
        ids = set()
        for art in group.iterdir():
            hit = ART_ID.match(art.name)
            if hit:
                ids.add(hit.group(1).lower())
        if ids:
            found[group.name.lower()] = ids

    # a recipe is the other half of the fork and is art by another route
    data_dir = Path(data_dir)
    if data_dir.is_dir():
        recipes = {p.stem.lower() for p in data_dir.glob("*.txt")}
        for ids in found.values():
            ids |= {i for i in recipes if ART_ID.match(i)}
    return found


def _model_paths(data_dir: Path, category: dict, art: dict = None) -> set:
    """
    every archive path a category's ids own, lower cased.

    With `art`, only the ids the mod ships something for. That is the
    denominator applied_categories should be measuring against: an id with no
    HD art cannot change, so counting it is asking a category to reach a bar
    that moves further away the more of its ids the mod skips.
    """
    from patcher.mods.hd_preflight import MODEL_SUFFIXES
    from patcher.mods.hd_recipe import read_category
    from patcher.mods.hd_runner import GROUPS, usable_id

    listing = data_dir / category.get("list", "")
    if not listing.is_file():
        return set()

    paths = set()
    for kind, member in read_category(listing).members:
        group = GROUPS.get((kind or "").upper())
        if group is None or not usable_id(member):
            continue
        if art and member.lower() not in art.get(group, set()):
            continue
        for suffix in MODEL_SUFFIXES:
            paths.add(f"chr/{group}/{member}/bin/{member}{suffix}".lower())
    return paths


def looks_modded(live_index: Path, vanilla_index: Path) -> bool:
    """
    whether the game differs from the backup at all.

    Deliberately weaker than applied_categories: install() only needs to know
    whether to put the vanilla archives back first, and one changed entry is
    reason enough. Working out WHICH mod is in there is a question for the
    panel, not for the thing about to overwrite it.
    """
    try:
        return bool(changed_paths(live_index, vanilla_index))
    except Exception:
        return False
