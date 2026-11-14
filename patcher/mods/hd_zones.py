"""
The environmental half of the HD Project.

Characters live in one archive pair and repack once. Each zone is its own pair,
split across two places:

    white_data\\sys\\white_img<v>.win32.bin     holds zone\\filelist_z010u.win32.bin
    white_data\\zone\\white_z010u_img.win32.bin sits loose on disk

So a zone install nests: unpack the zone, run the tools per block, import back
into the zone pair, then put the blob on disk and import the new zone index into
white_img<v>. That last import is why zones touch the game mid-run.

Setup.bat extracts all of white_img<v> (2GB) for about 600KB of indexes; we read
just those entries. It also hardcodes a bg folder per zone and gets z029 wrong
(bg\\loc019, which is z019's), so blocks are found by walking what the zone
actually extracted to instead.
"""

import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from patcher.mods.hd_runner import (CMD, COMMIT, DISCARD, ERR, HARVEST, MODEL_FILES,
                                    OK_COUNT, OK_IMPORTED, OK_PRODUCED, OK_ZERO, OUT,
                                    SEED_PAIR, ZONE_TREE, Step)

# where the mod keeps its zone originals, and where it assembles the result.
# Their names, so a folder their installer has used reads the same to ours.
# A run that works clears both (hd_install.clear_caches)
ZONE_CACHE = "models\\orig\\zone{variant}"
ZONE_WORK = "models\\mod\\zone"

ZONE_ID = re.compile(r"^z\d{3}$")
BLOCK_DIR = re.compile(r"^[A-Za-z]\d{3}$")


def zone_ids(members) -> list:
    """
    the zone ids a category's members name, in the order they were listed.

    LoadHDEnvironments.txt is `Z8` under a `#:Palumpolum` heading, and the
    archive is z008: a bare number widened to three digits. read_category
    yields these with kind None, because the one letter type in every other
    list is a group and a zone has no group.
    """
    found, seen = [], set()
    for kind, member in members:
        if kind is not None:
            continue
        text = str(member).strip()
        if not text or text[0] not in "zZ" or not text[1:].isdigit():
            continue
        name = f"z{int(text[1:]):03d}"
        if name not in seen:
            seen.add(name)
            found.append(name)
    return found


def read_entry(image: Path, entries: list, game_path: str) -> bytes:
    """
    one file out of a white_img, without unpacking the other fourteen thousand.

    The index holds a sector offset and both lengths per entry, so this is a
    seek and a read. Raises rather than returning empty: a caller that gets
    b"" back writes a zero byte index and the failure surfaces four steps later
    as something unrelated.
    """
    from patcher.archive.entry import find

    entry = find(entries, game_path)
    if entry is None:
        raise KeyError(f"{game_path} is not in the index")
    return read_record(image, entry)


def read_record(image: Path, entry) -> bytes:
    """read_entry() for one particular record, which is not the same thing when a path repeats"""
    if entry.is_stub:
        raise ValueError(f"{entry.path} is a placeholder, not a file")
    with Path(image).open("rb") as handle:
        return _record(handle, entry)


def _record(handle, entry) -> bytes:
    """read_record() through a handle that's already open"""
    handle.seek(entry.byte_offset)
    blob = handle.read(entry.stored)
    if len(blob) != entry.stored:
        raise ValueError(f"{entry.path} is short: {len(blob)} of {entry.stored} bytes")
    if entry.is_compressed:
        import zlib
        blob = zlib.decompress(blob)
        if len(blob) != entry.uncompressed:
            raise ValueError(f"{entry.path} inflated to {len(blob)}, index says "
                             f"{entry.uncompressed}")
    return blob


def harvest_steps(sys_dir: Path, zone_dir: Path, variant: str, zones) -> list:
    """each zone's vanilla pair, into the mod's cache"""
    wanted = [z for z in zones if ZONE_ID.match(z)]
    if not wanted:
        return []
    return [Step(tool=None, action=HARVEST,
                 args=[str(sys_dir), str(zone_dir), variant, *wanted],
                 label=f"reading {len(wanted)} zone archives out of the game")]


def unpack_steps(variant: str, zone: str) -> list:
    """
    a zone's own archive apart, then everything but bg thrown away.

    The extraction lands in a folder named after the blob, which is where
    ff13tool puts it and not something we choose. produces is that folder: the
    exit code is meaningless here for the reason the runner's docstring gives
    at length.
    """
    cache = ZONE_CACHE.format(variant=variant)
    index = f"{cache}\\filelist_{zone}{variant}.win32.bin"
    blob = f"{cache}\\white_{zone}{variant}_img.win32.bin"
    out = f"{cache}\\white_{zone}{variant}_img"
    return [
        Step(tool="pack", args=["-x", "-ff13", index, blob],
             label=f"unpacking {zone}", success=OK_PRODUCED, produces=out),
        Step(tool=None, action=ZONE_TREE, args=[out, f"{cache}\\{zone}", "bg"],
             label=f"caching {zone} backgrounds"),
    ]


def blocks(mod_root: Path, variant: str, zone: str) -> list:
    """
    every block in an extracted zone, as (relative path, block number). The
    mod's recipes fix the shape:

        zoneu\\z010\\bg\\loc014\\m022\\bin\\block022.win32.trb

    a four character folder whose last three are the number, two under bg.
    Anything else is left alone.
    """
    root = Path(mod_root) / Path(ZONE_CACHE.format(variant=variant).replace("\\", "/")) / zone / "bg"
    if not root.is_dir():
        return []

    found = []
    for area in sorted(p for p in root.iterdir() if p.is_dir()):
        for block in sorted(p for p in area.iterdir() if p.is_dir()):
            if not BLOCK_DIR.match(block.name) or not (block / "bin").is_dir():
                continue
            found.append((f"bg\\{area.name}\\{block.name}", block.name[-3:]))
    return found


def block_steps(mod_root: Path, data_dir: Path, variant: str, zone: str) -> list:
    """
    the per block half, one pair of steps each: make the model, import it.

    Same recipe-or-path fork as PROCESSLINE. Both halves get imported, .trb
    then .imgb, like LOADMODELFILE does; importing only the .trb put HD headers
    over vanilla pixels and turned every surface to noise. Runs from inside
    models\\mod\\zone<v>\\<zone>, which all of ff13tool's paths resolve
    against.
    """
    cache = ZONE_CACHE.format(variant=variant)
    work = f"{ZONE_WORK}\\filelist_{zone}{variant}.win32.bin"
    blob = f"{ZONE_WORK}\\white_{zone}{variant}_img.win32.bin"
    steps = []

    for relative, number in blocks(mod_root, variant, zone):
        orig = f"{cache}\\{zone}\\{relative}\\bin\\"
        mod = f"models\\mod\\zone{variant}\\{zone}\\{relative}\\bin\\"
        recipe = Path(data_dir) / f"{zone}block{number}{variant}.txt"
        if recipe.is_file():
            made = Step(tool="models", args=[f"Data\\{recipe.name}"],
                        label=f"{zone} block{number} models", success=OK_ZERO,
                        optional=True, payload=True)
        else:
            # /s is on their zone call and on neither of their two chr ones.
            # What it switches is documented nowhere we have, but it is the
            # configuration their zones ship working under, so it is ours too.
            made = Step(tool="inject",
                        args=[f"block{number}.win32", orig, mod,
                              f"zone{variant}\\{zone}\\", "/s"],
                        label=f"{zone} block{number} textures", success=OK_COUNT,
                        optional=True, payload=True)
        steps.append(made)
        # relative to the cwd below, which is two levels under models\\mod.
        # after=, because their GENERATEZONE only imports a block whose tools
        # reported doing something: run unconditionally, this reached a block
        # the injector had just failed on and asked to run in a folder the
        # injector therefore never created. The .imgb waits on the .trb, so a
        # failed first half never gets a second one to go with it.
        previous = made.label
        for suffix in MODEL_FILES:
            steps.append(Step(
                tool="pack",
                args=["-i", f"..\\..\\zone\\filelist_{zone}{variant}.win32.bin",
                      f"..\\..\\zone\\white_{zone}{variant}_img.win32.bin",
                      f"{relative}\\bin\\block{number}{suffix}"],
                label=f"{zone} block{number}{suffix[6:]} into the zone archive",
                # their LOADMODELFILE counts the same four words on both halves.
                # The blob changing is not enough evidence here: a half written
                # import moves its mtime too, and a silent half import is the
                # exact failure this pipeline already shipped once.
                success=OK_IMPORTED, optional=True, after=previous,
                cwd=f"models\\mod\\zone{variant}\\{zone}"))
            previous = steps[-1].label
    if steps:
        # the working pair the imports above write into, copied from the cache
        # rather than edited in place, so a failed zone leaves the original
        # where the next attempt can find it
        # the two trailing args are the trees to mirror as empty folders. The
        # seed step cannot do it: it runs before a zone has been unpacked, so
        # models\\orig\\zone<v> is not there yet when it looks.
        steps.insert(0, Step(
            tool=None, action=SEED_PAIR,
            args=[f"{cache}\\filelist_{zone}{variant}.win32.bin", work,
                  f"{cache}\\white_{zone}{variant}_img.win32.bin", blob,
                  f"{cache}\\{zone}", f"models\\mod\\zone{variant}\\{zone}"],
            label=f"{zone} archive ready to write into"))
    return steps


def install_steps(game_dir: Path, variant: str, zone: str, after_any=(),
                  unless_failed=(), undo: Path | None = None,
                  backup: Path | None = None) -> list:
    """
    the rewritten zone, back into the game, as one step. See commit_zone().

    after_any is their `if %updated% EQU 0 GOTO:EOF`: a zone the mod ships no
    art for anywhere is left alone rather than having its own untouched
    archive copied over itself. unless_failed is ours: a zone where any import
    failed is left alone too, because half a block is worse than none.
    """
    game = Path(game_dir)
    if undo is None or backup is None:
        from patcher.paths import undo_store_for
        undo = undo if undo is not None else undo_store_for(game) / "hdproject-commit"
        backup = backup if backup is not None else undo_store_for(game) / "hdproject"
    return [Step(tool=None, action=COMMIT, after_any=list(after_any),
                 unless_failed=list(unless_failed),
                 args=[f"{ZONE_WORK}\\white_{zone}{variant}_img.win32.bin",
                       f"{ZONE_WORK}\\filelist_{zone}{variant}.win32.bin",
                       str(game), variant, zone, str(undo), str(backup)],
                 label=f"{zone} into the game", lands=f"zone {zone}")]


def discard_steps(variant: str, zone: str) -> list:
    """
    the working copies, once the game has the result.

    The last three lines of their GENERATEZONE, and they matter more for us
    than for them now that the .imgb goes in too: a zone blob grows while it is
    being written into, and twenty of them kept to the end of a run is tens of
    gigabytes of a drive that only ever needed one at a time.
    """
    return [Step(tool=None, action=DISCARD,
                 args=[f"{ZONE_WORK}\\white_{zone}{variant}_img.win32.bin",
                       f"{ZONE_WORK}\\filelist_{zone}{variant}.win32.bin",
                       f"models\\mod\\zone{variant}\\{zone}"],
                 label=f"{zone} working copies released")]


def plan_for(mod_root: Path, game_dir: Path, data_dir: Path, variant: str, zones,
             undo: Path | None = None, backup: Path | None = None) -> list:
    """
    every step for a set of zones, in the order a failure survives.

    Built in two passes on purpose. The blocks in a zone are not knowable until
    its archive has been unpacked, so the caller runs the harvest and unpack
    steps first and comes back here for the rest: see hd_install.install().
    """
    steps = []
    for zone in zones:
        made = block_steps(mod_root, data_dir, variant, zone)
        if not made:
            continue
        # the payload steps are the ones their `set updated=1` sits behind
        worked = [s.label for s in made if s.payload]
        imports = [s.label for s in made if s.success == OK_IMPORTED]
        steps += (made
                  + install_steps(game_dir, variant, zone, after_any=worked,
                                  unless_failed=imports, undo=undo, backup=backup)
                  + discard_steps(variant, zone))
    return steps


def prepare_steps(game_dir: Path, variant: str, zones, source: Path | None = None) -> list:
    """
    the half that has to run before the blocks are knowable.

    source is where the vanilla pairs are read from. install() hands it the
    backup, which is vanilla by construction; the game is only the fallback.
    """
    game = Path(game_dir)
    if source is None:
        steps = harvest_steps(game / "white_data" / "sys", game / "white_data" / "zone",
                              variant, zones)
    else:
        steps = harvest_steps(Path(source), Path(source), variant, zones)
    for zone in zones:
        steps += unpack_steps(variant, zone)
    return steps


def zone_archives(game_dir: Path, variant: str, zones) -> list:
    """
    what a zone install overwrites in the game, so it can be backed up.

    white_img<v> is on the list once however many zones are picked, because
    every zone's index goes back into it. Nothing here is a guess: these are
    the exact two paths install_steps writes to.
    """
    from patcher.mods.hd_layout import layout_of

    layout = layout_of(game_dir)
    sys_dir, zone_dir = layout.sys_dir(game_dir), layout.zone_dir(game_dir)
    found = [sys_dir / f"white_img{variant}.win32.bin", sys_dir / f"filelist{variant}.win32.bin"]
    for zone in zones:
        found.append(zone_dir / f"white_{zone}{variant}_img.win32.bin")
    return [p for p in found if p.is_file()]


# room on top of the blob itself: the image archive grows by one zone index
# per commit, and a drive filled to the last megabyte is not one to swap on
COMMIT_SLACK = 64 * 1024 * 1024


def _entries(path: Path) -> list:
    from patcher.archive.filelist import load
    return list(load(Path(path)).entries())


def _overruns(entries: list, size: int) -> str:
    for entry in entries:
        if not entry.is_stub and entry.byte_offset + entry.stored > size:
            return entry.path
    return ""


def _key(path: str) -> str:
    return path.replace("\\", "/").lower()


def _unmatched(records: list, against: list) -> list:
    """
    the records with no counterpart at the same place in `against`.

    Counted rather than keyed by path, because a path can be listed twice at
    two places, and a dict keeps the last one and reads the first as moved.
    The real filelistu does that 470 times.
    """
    left = Counter((_key(e.path), e.sector_offset, e.stored) for e in against)
    alone = []
    for record in records:
        spot = (_key(record.path), record.sector_offset, record.stored)
        if left[spot]:
            left[spot] -= 1
        else:
            alone.append(record)
    return alone


def check_archive(index: Path, blob: Path, since: list | None = None, recent: int = 4) -> str:
    """
    why an index and the archive it describes disagree, or "".

    Read back the way the game reads them, since a tool printing success over
    a full disk looks just like one that worked: the index parses, nothing
    points past the end, and the newest data inflates to the claimed size.
    `since` is the index this was made from: every path in it must still be
    listed, and "newest" means what moved. By path, since one path can be
    listed twice.
    """
    try:
        entries = _entries(index)
    except Exception as e:
        return f"{Path(index).name} will not parse ({e})"

    if since is not None:
        listed = {_key(e.path) for e in entries}
        lost = sorted({e.path for e in since if _key(e.path) not in listed})
        if lost:
            return f"{len(lost)} files are missing from {Path(index).name}, starting with {lost[0]}"

    overrun = _overruns(entries, Path(blob).stat().st_size)
    if overrun:
        return f"{overrun} points past the end of {Path(blob).name}, so a write stopped partway"

    moved = [e for e in _unmatched(entries, since or []) if not e.is_stub]
    for entry in sorted(moved, key=lambda e: e.byte_offset)[-recent:]:
        try:
            read_record(blob, entry)
        except Exception as e:
            return f"{entry.path} does not read back ({e})"
    return ""


def pair_problem(index: Path, archive: Path, samples: int | None = 64) -> str:
    """
    why an index and an archive weren't made together, or "".

    For two files that might be from different days, unlike check_archive().
    After a repack nothing sits where it did, so `samples` spread entries catch
    a mismatched pair (a backup like that crashed a game after the logo). An
    in-place injection only moves two entries in thousands, which only
    samples=None (read them all) is sure to find.
    """
    try:
        entries = [e for e in _entries(index) if not e.is_stub]
        size = Path(archive).stat().st_size
    except Exception as e:
        return f"{Path(index).name} will not read ({e})"
    overrun = _overruns(entries, size)
    if overrun:
        return f"{Path(index).name} puts {overrun} past the end of {Path(archive).name}"
    packed = sorted((e for e in entries if e.is_compressed), key=lambda e: e.byte_offset)
    if samples:
        packed = packed[::max(1, len(packed) // samples)]
    wrong = _first_unread(archive, packed)
    if wrong is not None:
        entry, e = wrong
        return f"{entry.path} is not where {Path(index).name} says it is ({e})"
    return ""


def _first_unread(archive: Path, entries: list):
    """
    (entry, why) for the first of `entries` that doesn't read back, or None.
    Every entry is a whole archive's inflate, seconds of it, and zlib lets go
    of the GIL while it works, so a few contiguous runs of them go at once,
    each through its own handle rather than an open per entry
    """
    from concurrent.futures import ThreadPoolExecutor

    def run(part):
        with Path(archive).open("rb") as handle:
            for entry in part:
                try:
                    _record(handle, entry)
                except Exception as e:
                    return entry, e
        return None

    import os
    lanes = max(1, min(4, os.cpu_count() or 1, len(entries) // 256))
    size = -(-len(entries) // lanes) if entries else 0
    parts = [entries[n:n + size] for n in range(0, len(entries), size)] if size else []
    if len(parts) < 2:
        return run(entries)
    with ThreadPoolExecutor(max_workers=len(parts)) as pool:
        found = [w for w in pool.map(run, parts) if w is not None]
    # the earliest, the way reading them in order would have said it
    return min(found, key=lambda w: w[0].byte_offset) if found else None


def zone_problem(image: Path, listing: Path, blob: Path, samples: int = 32,
                 name: str = "", entries: list | None = None) -> str:
    """
    why a zone blob and white_img's index for it don't fit, or "". `name` is
    the blob's real name when `blob` is a staged copy, `entries` the listing
    already read (half a second each, fifteen zones share it).

    An HD index over a vanilla blob points past its end. The other way round
    is fine, imports only append. No index for the zone is fine too.
    """
    from patcher.archive.filelist import parse

    name = name or Path(blob).name
    zone, variant = name.split("_")[1][:-1], name.split("_")[1][-1]
    try:
        raw = read_entry(image, entries if entries is not None else _entries(listing),
                         f"zone/filelist_{zone}{variant}.win32.bin")
    except KeyError:
        return ""
    except Exception as e:
        return f"{zone}'s index will not read out of {Path(image).name} ({e})"
    try:
        inner = [e for e in parse(raw).entries() if not e.is_stub]
        size = Path(blob).stat().st_size
    except Exception as e:
        return f"{zone}'s index will not read ({e})"
    overrun = _overruns(inner, size)
    if overrun:
        return f"{zone}'s index puts {overrun} past the end of {name}"
    packed = sorted((e for e in inner if e.is_compressed), key=lambda e: e.byte_offset)
    for entry in packed[::max(1, len(packed) // samples)]:
        try:
            read_record(blob, entry)
        except Exception as e:
            return f"{entry.path} is not where {zone}'s index says it is in {name} ({e})"
    return ""


def check_pair(index: Path, blob: Path, original: Path) -> str:
    """why a finished working zone pair is not fit to go into the game, or "" """
    try:
        before = _entries(original) if Path(original).is_file() else None
    except Exception:
        before = None
    return check_archive(index, blob, since=before)


@dataclass
class ImageGuard:
    """
    what it takes to undo one ff13tool -i against the game's own image pair.

    The tool edits white_img<v> in place, so this takes the three things it can
    touch: the index (small, copied whole), the image's length (an import
    appends) and the span the zone's old index occupied (in case it wrote over
    it instead). Truncate, put the span back, put the index back.
    """
    image: Path
    listing: Path
    entry: str
    size: int
    entries: list
    listing_copy: Path
    journal: Path
    before: bytes

    @classmethod
    def take(cls, image: Path, listing: Path, entry_path: str, store: Path) -> "ImageGuard":
        from patcher.archive.entry import SECTOR
        from patcher.archive.spanbackup import SpanBackup
        from patcher.fileops import write_atomic

        image, listing, store = Path(image), Path(listing), Path(store)
        store.mkdir(parents=True, exist_ok=True)
        entries = _entries(listing)
        # the tool rewrites every copy of a path listed twice, so every copy's span is kept
        copies = [e for e in entries if _key(e.path) == _key(entry_path)]
        if not copies:
            raise KeyError(f"{entry_path} is not in {listing.name}")
        before = read_record(image, copies[0])
        size = image.stat().st_size

        copy = store / f"{listing.name}.{int(time.time() * 1000):x}.before"
        write_atomic(copy, listing.read_bytes())
        spans = SpanBackup(image, store, metadata={"entry": entry_path})
        for entry in copies:
            spans.record(entry.byte_offset,
                         min(entry.sector_span * SECTOR, size - entry.byte_offset),
                         label=entry_path)
        return cls(image=image, listing=listing, entry=entry_path, size=size,
                   entries=entries, listing_copy=copy, journal=spans.commit(),
                   before=before)

    def check(self, expected: bytes) -> str:
        """why the import did not land as intended, or an empty string"""
        problem = check_archive(self.listing, self.image, since=self.entries)
        if problem:
            return problem
        try:
            got = read_entry(self.image, _entries(self.listing), self.entry)
        except Exception as e:
            return f"{self.entry} does not read back ({e})"
        return "" if got == expected else f"{self.entry} reads back as something else"

    def untouched(self) -> bool:
        """whether the pair is still byte for byte what take() found"""
        from patcher.archive.spanbackup import matches

        try:
            return (self.image.stat().st_size == self.size
                    and self.listing.read_bytes() == self.listing_copy.read_bytes()
                    and matches(self.journal, self.image))
        except Exception:
            return False

    def roll_back(self, edited: Path | None = None) -> str:
        """
        the pair as take() found it, checked. "" or what is still wrong.

        `edited` is the listing ff13tool was handed, when that was a copy
        rather than the game's own. XIII-2's is decrypted into the mod folder
        first, so that copy is the only place a relayout would show.
        """
        from patcher.archive.spanbackup import restore
        from patcher.fileops import write_atomic

        # an import that never got the file open changed nothing, and writing
        # the undo anyway only meets the same lock and calls it a failure
        if self.untouched():
            return ""

        # an import that moved anything else is not one a length and a span
        # can undo, so say so rather than put back a pair that only looks right
        def others(entries):
            return [e for e in entries if _key(e.path) != _key(self.entry)]

        try:
            now = others(_entries(edited or self.listing))
        except Exception:
            now = None
        if now is not None:
            moved = _unmatched(others(self.entries), now)
            if moved:
                return (f"ff13tool moved {len(moved)} other files inside {self.image.name}, "
                        "which the undo taken before it cannot put back")

        try:
            if self.image.stat().st_size > self.size:
                with self.image.open("rb+") as handle:
                    handle.truncate(self.size)
            restore(self.journal)
            write_atomic(self.listing, self.listing_copy.read_bytes())
            if self.listing.read_bytes() != self.listing_copy.read_bytes():
                return f"{self.listing.name} did not go back"
            if read_entry(self.image, _entries(self.listing), self.entry) != self.before:
                return f"{self.entry} did not go back"
        except Exception as e:
            return str(e)
        return ""

    def release(self) -> None:
        from patcher.archive.spanbackup import discard

        # runs after the zone is in, so a scanner holding the undo is a stray
        # file in the store, never a reason to report a finished zone as a crash
        try:
            discard(self.journal)
            self.listing_copy.unlink(missing_ok=True)
        except OSError:
            pass


def commit_zone(step: Step, mod_root: Path, support: Path, on_line=None,
                on_share=None, cancel=None) -> int:
    """
    one finished zone into the game: the blob swapped on disk and its index
    imported into white_img<v>, as a unit. Returns 0 or 1 like a tool, or
    NOT_UNDONE when it failed and couldn't show the game was put back.

    Pair checked first, new blob written in full beside the old, swap is two
    renames, and the import runs under a guard that can put the image pair
    back, so a zone is all in or not at all. Stop works while staging, not
    after: killing ff13tool halfway through the game's archive has no way back.

    args are (working blob, working index, game dir, variant, zone, undo dir,
    backup dir).
    """
    from patcher.download import free_space
    from patcher.fileops import (CopyCancelled, Stranded, discard, drop, stage_copy, swap,
                                 unswap)
    from patcher.mods.hd_install import mark_written, unmark_written
    from patcher.mods.hd_runner import (NOT_UNDONE, REVERT, Cancelled, _spawn, at,
                                        succeeded, tool_path)

    def say(text, stream=OUT):
        if on_line:
            on_line(text, stream)

    blob_rel, index_rel, game, variant, zone, undo, backup = step.args
    blob, index = at(mod_root, blob_rel), at(mod_root, index_rel)
    game = Path(game)
    live = game / "white_data" / "zone" / blob.name
    sys_dir = game / "white_data" / "sys"
    image, listing = sys_dir / f"white_img{variant}.win32.bin", sys_dir / f"filelist{variant}.win32.bin"
    original = at(mod_root, f"{ZONE_CACHE.format(variant=variant)}\\{index.name}")
    tool = tool_path(support, "pack")

    for needed in (blob, index):
        if not needed.is_file():
            say(f"{zone} was left as it was: {needed.name} was never written", ERR)
            return 1
    problem = check_pair(index, blob, original)
    if problem:
        say(f"{zone} was left as it was: {problem}", ERR)
        return 1

    need = blob.stat().st_size + COMMIT_SLACK
    free = free_space(live.parent)
    if free < need:
        say(f"{zone} was left as it was: going in safely needs {need / 1024 ** 3:.1f} GB "
            f"free on the game's drive and there is {free / 1024 ** 3:.1f} GB", ERR)
        return 1

    try:
        temp, _ = stage_copy(blob, live, cancel=cancel, on_share=on_share)
    except CopyCancelled as e:
        raise Cancelled(str(e)) from None
    except OSError as e:
        say(f"{zone} was left as it was: the new copy could not be written beside the "
            f"game's ({e})", ERR)
        return 1

    try:
        marked = mark_written(Path(backup))
    except OSError as e:
        discard(temp)
        say(f"{zone} was left as it was: the backup's record could not be updated ({e}), "
            "and Revert goes by it", ERR)
        return 1

    def left_as_it_was(text) -> int:
        # the mark was only ever for this zone, and nothing of it went in
        if marked:
            unmark_written(Path(backup))
        say(f"{zone} was left as it was: {text}", ERR)
        return 1

    try:
        prev = swap(temp, live)
    except Stranded as e:
        discard(temp)
        say(f"{zone} failed and could not be undone: {e}. Close whatever is holding the "
            f"game's files, then put the game back with {REVERT} before playing.", ERR)
        return NOT_UNDONE
    except OSError as e:
        discard(temp)
        return left_as_it_was(f"{live.name} could not be replaced ({e}). This usually "
                              "means the game is running.")

    def put_back_blob() -> str:
        try:
            unswap(live, prev)
        except OSError as e:
            where = f", the game's own copy is beside it as {prev.name}" if prev else ""
            return f"{live.name} could not be put back ({e}){where}"
        return ""

    try:
        guard = ImageGuard.take(image, listing, f"zone/{index.name}", Path(undo))
    except Exception as e:
        left = put_back_blob()
        if left:
            say(f"{zone} failed and could not be undone: {left}. Put the game back with "
                f"{REVERT} before playing.", ERR)
            return NOT_UNDONE
        return left_as_it_was(f"no way to undo an import into {image.name} could be "
                              f"taken ({e})")

    said = []

    def hear(text, stream=OUT):
        if stream != ERR:
            said.append(text)
        say(text, stream)

    say(f"> {tool.name} -i {listing} {image} zone\\{index.name}   (in models\\mod)", CMD)
    try:
        code = _spawn([str(tool), "-i", str(listing), str(image), f"zone\\{index.name}"],
                      at(mod_root, "models\\mod"), on_line=hear, label=step.label)
    except OSError as e:
        code, said = None, [str(e)]

    judge = Step(tool="pack", args=[], label=step.label, success=OK_IMPORTED)
    problem = ("ff13tool did not report the import"
               if code is None or not succeeded(judge, code, None, said)
               else guard.check(index.read_bytes()))
    if problem:
        left = "; ".join(x for x in (guard.roll_back(), put_back_blob()) if x)
        if left:
            say(f"{zone} failed and could not be fully undone: {left}. Put the game back "
                f"with {REVERT} before playing.", ERR)
            return NOT_UNDONE
        guard.release()
        return left_as_it_was(problem)

    drop(prev)
    guard.release()
    say(f"{zone} is in the game")
    return 0
