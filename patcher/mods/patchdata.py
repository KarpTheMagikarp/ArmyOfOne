"""
Surihix's patchers for LR, read rather than run.

LRAerithGrabRestoration.exe and LROuterworldAddons.exe are one program around
two PatchData.bin files. Run, it unpacks db/resident/wdbpack.bin out of one
voice set's white_img2, drops PatchData's files over it, repacks in place, and
copies the DLC files into weiss_data/dlc, keeping nothing of what was there.

PatchData.bin is a zip laid out the way WPDtool unpacks (wpd.py):

    db/resident/_wdbpack.bin/r_dlc.wdb             a record in wdbpack.bin
    db/resident/_wdbpack.bin/_r_reward.wdb/rw_102  a record in a record in it
    a/dlc/0000003/white_p0000003img_a.win32.bin    a file for weiss_data (a is
                                                   English voices, v Japanese
                                                   voices, j the Japanese release)

So the container comes out of each voice set's archive as it is now, gets
those records swapped, and goes in as a drawer mod; the DLC files go beside
the game. Both patchers change the same wdbpack.bin, so taking the first off
under the second means rebuilding the second without it (captured() and
rebuild()). lay() does the same for any drop shaped like this, like a Nova
Chrysalia modpack's Data\\db\\resident\\_wdbpack.bin\\r_treasurebox.wdb.
"""

import hashlib
import re
import time
import zlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from patcher.mods import wpd

# a DLC file under the letter of the voice set it's for, which is the letter
# that set's white_img2 ends in, j for the Japanese release's, which has none
LOOSE = re.compile(r"^([avj])/(dlc/.+)$")


class BuildError(Exception):
    pass


@dataclass
class Plan:
    data: str                   # PatchData.bin in the drop
    exe: str                    # the patcher beside it, what the drawer knows it by
    # game path -> {(record, record in it...): the drop's member}
    records: dict = field(default_factory=dict)
    # Surihix's letter -> {path under weiss_data: the drop's member}
    loose: dict = field(default_factory=dict)


def find(source, members):
    """the patcher in a drop, or None for anything that isn't one"""
    for outer in source.nested:
        if PurePosixPath(outer).name.lower() != "patchdata.bin":
            continue
        folder = outer.rpartition("/")[0]
        plan = Plan(data=outer, exe=next(
            (PurePosixPath(m).name for m, _ in members
             if m.lower().endswith(".exe") and m.rpartition("/")[0] == folder), "PatchData.bin"))
        for name in source.listing(outer):
            member = f"{outer}/{name}"
            loose = LOOSE.match(name.replace("\\", "/"))
            inside = wpd.split(name)
            if loose:
                plan.loose.setdefault(loose.group(1), {})[loose.group(2)] = member
            elif inside is not None:
                path, records = inside
                plan.records.setdefault(path.lower(), {})[records] = member
            else:
                plan = None
                break
        if plan is not None and (plan.records or plan.loose):
            return plan
    return None


def touches(install_dir, plan: Plan) -> set:
    """(index, game path) for every container it changes, the way the drawer's install sees them"""
    from pathlib import Path

    from patcher.mods import game_files

    install_dir = Path(install_dir)
    game = game_files.read_game(install_dir)
    return {(pair.name(install_dir), path) for path in plan.records for pair in _wanted(game, path)}


def _wanted(game, path: str) -> list:
    """the pairs a container goes into: white_img2's, which is the one the patcher opens"""
    from patcher.mods import game_files

    listing = [p for p in game.pairs if path in game_files.entries(p.index)]
    second = [p for p in listing if p.kind.startswith("img2")]
    return second or listing


def _work(install_dir):
    import tempfile
    from pathlib import Path

    from patcher.paths import undo_store_for

    store = undo_store_for(install_dir) / "work"
    store.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="patchdata-", dir=store))


def sweep(install_dir) -> None:
    """what a build cut short left, a crash or a power cut part way through one"""
    from patcher.fileops import clear_tree
    from patcher.paths import undo_store_for

    work = undo_store_for(install_dir) / "work"
    for leftover in work.glob("patchdata-*") if work.is_dir() else ():
        try:
            # a Remove's going on beside this has been written to in the last minute
            stale = time.time() - leftover.stat().st_mtime > 3600
        except OSError:
            continue
        if stale:
            clear_tree(leftover)


def _lay(install_dir, work, found, pair, path: str, changes: dict, prefix: str = "") -> None:
    """
    one pair's copy of a container, as the game has it now with `changes` in,
    into `work`, or a whole file a mod with records has besides, for changes
    of {(): its bytes}. Listed under `prefix` and the path, for a drop read
    beside it (Joined)
    """
    from patcher.archive.entry import EntryFormatError
    from patcher.mods import game_files
    from patcher.mods.hd_zones import read_record

    entry = game_files.entries(pair.index).get(path)
    if entry is None:
        raise BuildError(f"{path} isn't in {pair.index.name} any more")
    if (path, pair.voice) in found.picks:
        # LR's v and unlettered sets are both Japanese, and a copy built out of
        # each would go in under one name, both archives getting the last
        raise BuildError(f"{path} is in two of this game's archives for one voice set, "
                         f"{pair.archive.name} the second, which can't be told apart")
    try:
        if () in changes:
            built = bytes(changes[()])
        else:
            live = b"" if entry.is_stub else read_record(pair.archive, entry)
            if live[:len(wpd.MAGIC)] != wpd.MAGIC:
                raise BuildError(f"{path} isn't a container in this game, so the "
                                 f"_{path.rsplit('/', 1)[-1]} folder isn't records to put in it")
            built = wpd.patch(live, changes)
    except wpd.WpdError as e:
        raise BuildError(f"{path} in {pair.index.name} {e}, so it isn't the version this "
                         "was made for") from None
    except (OSError, ValueError, EntryFormatError, zlib.error) as e:
        raise BuildError(f"{path} couldn't be read out of {pair.archive.name} ({e})") from None
    where = f"{pair.voice}/{path}"
    (work / where).parent.mkdir(parents=True, exist_ok=True)
    (work / where).write_bytes(built)
    member = prefix + where
    found.picks[(path, pair.voice)] = member
    found.sizes[member] = len(built)
    found.matched.add(path)
    found.members_at.setdefault(path, []).append(member)
    found.voices[member] = pair.voice
    # into the archive it was built out of, and not another of the set
    # that lists the same path
    found.pairs_for.setdefault(path, set()).add(pair.name(install_dir))


def build(install_dir, source, plan: Plan, progress=None) -> tuple:
    """
    (a folder laid out the way the drawer reads a drop, its look(), the DLC
    files as drop member -> path under the game's folder, and the records it
    changes as game path -> [[record, ...]]). The caller installs the first
    two as one mod, the DLC files beside it, and clears the folder either way
    """
    from pathlib import Path

    from patcher.fileops import clear_tree
    from patcher.mods import game_files

    install_dir = Path(install_dir)
    data = game_files.data_dir(install_dir)
    if data is None or data.name != "weiss_data":
        raise BuildError("it's a patcher for Lightning Returns, so it only goes into that game")
    game = game_files.read_game(install_dir)
    sweep(install_dir)
    targets = {}
    for path in plan.records:
        pairs = _wanted(game, path)
        if not pairs:
            raise BuildError(f"{path} isn't in this game's archives")
        targets[path] = pairs
    # the DLC for every voice set this game has, as the patcher would have
    # copied for whichever one it was asked about
    letters = {p.kind[-1] for p in game.pairs if p.kind.startswith("img2")}
    loose = {member: f"{data.name}/{rel}" for letter, files in plan.loose.items()
             if letter in letters for rel, member in files.items()}
    if plan.loose and not loose:
        raise BuildError("its DLC files are for a voice set this game doesn't have")
    work, found = _work(install_dir), game_files.Look()
    try:
        steps, done = sum(len(p) for p in targets.values()), 0
        for path, pairs in sorted(targets.items()):
            changes = {inside: source.read(member)
                       for inside, member in plan.records[path].items()}
            for pair in pairs:
                _lay(install_dir, work, found, pair, path, changes)
                done += 1
                if progress is not None:
                    progress(done, steps)
    except BaseException:
        clear_tree(work)
        raise
    changed = {path: sorted([list(inside) for inside in records])
               for path, records in plan.records.items()}
    return work, found, loose, changed


BUILT = ".patcher-built/"


def lay(install_dir, records: dict, read, progress=None) -> tuple:
    """
    (a folder of every container a drop's records go into, each built over
    what the game has now, their look() under BUILT, and the records as game
    path -> [[record, ...]]) for look()'s `records`: (container, voice) ->
    {(record, ...): [members]}. Any game's, since a container is any of its
    files WPDtool can open. A record for every voice set goes into each set's
    copy, under the set's own
    """
    from pathlib import Path

    from patcher.fileops import clear_tree
    from patcher.mods import game_files

    install_dir = Path(install_dir)
    game = game_files.read_game(install_dir)
    plan = {}
    for (path, voice), held in sorted(records.items(), key=lambda kv: (kv[0][0], kv[0][1] != "")):
        pairs = [p for p in _wanted(game, path) if not voice or p.voice == voice]
        if not pairs:
            raise BuildError(f"{path} isn't in this game's archives for the voice set it's for")
        for inside, members in held.items():
            if len({read(m) for m in members}) > 1:
                raise BuildError(f"it has {len(members)} different files for {'/'.join(inside)} "
                                 f"inside {path}, with nothing to say which goes in. Drop just "
                                 "the folder you want")
            for pair in pairs:
                plan.setdefault((path, pair.name(install_dir)), (pair, {}))[1][inside] = members[0]
    sweep(install_dir)
    work, found = _work(install_dir), game_files.Look()
    try:
        for n, ((path, _), (pair, changes)) in enumerate(sorted(plan.items(),
                                                                key=lambda kv: kv[0])):
            _lay(install_dir, work, found, pair, path,
                 {inside: read(member) for inside, member in changes.items()}, prefix=BUILT)
            if progress is not None:
                progress(n + 1, len(plan))
    except BaseException:
        clear_tree(work)
        raise
    changed = {}
    for (path, _), held in records.items():
        changed.setdefault(path, set()).update(held)
    return work, found, {path: sorted(list(inside) for inside in held)
                         for path, held in changed.items()}


class Joined:
    """a drop read with the containers lay() built for it, theirs under BUILT"""

    def __init__(self, source, work):
        from pathlib import Path

        self._source, self._work = source, Path(work)
        self.path, self.root, self.context = source.path, source.root, list(source.context)
        self.doubled, self.nested = list(source.doubled), list(source.nested)
        self.unopened, self.blocked = list(source.unopened), list(source.blocked)

    def _built(self, member: str):
        return self._work / member[len(BUILT):] if member.startswith(BUILT) else None

    def members(self) -> list:
        return self._source.members() + [
            (BUILT + p.relative_to(self._work).as_posix(), p.stat().st_size)
            for p in sorted(self._work.rglob("*")) if p.is_file()]

    def has(self, member: str) -> bool:
        built = self._built(member)
        return built.is_file() if built is not None else self._source.has(member)

    def read(self, member: str) -> bytes:
        built = self._built(member)
        return built.read_bytes() if built is not None else self._source.read(member)

    def opened(self, member: str):
        built = self._built(member)
        return built.open("rb") if built is not None else self._source.opened(member)

    def real(self, member: str) -> str:
        return member if member.startswith(BUILT) \
            else getattr(self._source, "real", lambda m: m)(member)

    def listing(self, outer: str) -> list:
        return self._source.listing(outer)

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def captured(install_dir, record: dict) -> dict:
    """
    what a mod's records hold in its own copy of each container, as index name
    -> game path -> {(record, ...): bytes}, and any file it has whole
    besides as {(): bytes}. Read before anything under it comes off, it's
    what that mod is, whatever it was built on. Its own copy and not the
    game's: one over it changing the same record would hand it that one's
    """
    from pathlib import Path

    install_dir = Path(install_dir)
    out = {}
    for spec in record.get("pairs", []):
        for item in spec["entries"]:
            if item.get("lost"):
                continue
            inside = (record.get("records") or {}).get(item["path"])
            own = _own(install_dir / spec["archive"], item)
            out.setdefault(spec["index"], {})[item["path"]] = (
                {tuple(r): wpd.read(own, tuple(r)) for r in inside} if inside else {(): own})
    return out


def _own(archive, item: dict) -> bytes:
    """a mod's copy of a file where it went in, or where a repack carried it, by its digest"""
    from patcher.mods import game_files
    from patcher.mods.hd_zones import read_record

    if item.get("sha") == game_files.STUB:
        return b""
    for fields in [item["now"]] + list(item.get("seen") or []):
        try:
            raw = read_record(archive, game_files._entry_at(item["path"], fields))
        except (OSError, ValueError, zlib.error):
            continue
        if hashlib.sha256(raw).hexdigest() == item["sha"]:
            return raw
    raise BuildError(f"its copy of {item['path']} isn't in {archive.name} any more")


def rebuild(install_dir, held: dict) -> tuple:
    """
    (a folder and its look()) putting captured() records back over what the
    game has now, and the files it held whole as they were
    """
    from pathlib import Path

    from patcher.fileops import clear_tree
    from patcher.mods import game_files

    install_dir = Path(install_dir)
    by_name = {p.name(install_dir): p for p in game_files.pairs(install_dir)}
    work, found = _work(install_dir), game_files.Look()
    try:
        for index, paths in sorted(held.items()):
            pair = by_name.get(index)
            if pair is None:
                raise BuildError(f"{index} isn't in the game any more")
            for path, changes in sorted(paths.items()):
                _lay(install_dir, work, found, pair, path, changes)
    except BaseException:
        clear_tree(work)
        raise
    return work, found
