"""
The HD Project in the other voice set too, when the box for it is ticked, so
the voices change the way Square Enix's launcher changes them: one setting,
nothing to wait on. Off by default, since that set gets its own backup and a
second copy of the art, gigabytes nobody should pay for without asking.

voice_mode picks which copy of the archives the game loads, u for English and
c for Japanese, and an HD install rebuilds the copy it's pointed at. The other
copy is the same game with the cast speaking another language, so the files HD
replaces are the same files in both, and what the install built for one set is
what the other one needs. This copies the finished files across instead of
running the tools again: a few minutes of appends where a second build took
forty.

"The same files in both" is checked, never assumed. A file HD changed only
goes across when the vanilla copies in the two sets match byte for byte, and a
pass holding any that don't is left whole for a build from that set's own
originals.

Nothing is written over. The files go on the end of the other set's archives,
the indexes pointing at them are swapped in last, and every file that changes
was copied into the HD backup first, so Revert puts both sets back the way it
always has.
"""

import hashlib
import os
import threading
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from patcher.archive.entry import SECTOR
from patcher.mods.game_files import held

LANGUAGE = {"u": "English", "c": "Japanese"}
# on top of what goes on the end, for the indexes swapped in beside the old ones
SLACK = 64 * 1024 * 1024


class MirrorError(Exception):
    pass


def _key(path: str) -> str:
    return path.replace("\\", "/").lower()


def _aligned(size: int) -> int:
    return -(-size // SECTOR) * SECTOR


@dataclass(frozen=True)
class Side:
    """one voice set's archives, in the game or as the HD backup keeps them"""
    letter: str
    chr_index: Path
    chr_archive: Path
    img_index: Path
    img_archive: Path
    blobs: Path

    def blob(self, zone: str) -> Path:
        return self.blobs / f"white_{zone}{self.letter}_img.win32.bin"

    def zone_index(self, zone: str) -> str:
        return f"zone/filelist_{zone}{self.letter}.win32.bin"

    @property
    def shared(self) -> bool:
        """XIII-2 keeps its characters and its zone indexes in one archive, XIII in two"""
        return self.chr_archive == self.img_archive

    def has_chr(self) -> bool:
        return self.chr_index.is_file() and self.chr_archive.is_file()

    def has_img(self) -> bool:
        return self.img_index.is_file() and self.img_archive.is_file()


def live(install_dir, letter: str) -> Side:
    from patcher.mods.hd_layout import layout_of

    layout = layout_of(install_dir)
    chr_index, chr_archive = layout.pair(install_dir, letter)
    sys_dir = layout.sys_dir(install_dir)
    return Side(letter, chr_index, chr_archive, sys_dir / f"filelist{letter}.win32.bin",
                sys_dir / f"white_img{letter}.win32.bin", layout.zone_dir(install_dir))


def kept(install_dir, letter: str) -> Side:
    """the same files as the HD backup holds them, from before HD first went into this set"""
    from patcher.paths import undo_store_for

    game = live(install_dir, letter)
    backup = undo_store_for(Path(install_dir)) / "hdproject"
    return Side(letter, backup / game.chr_index.name, backup / game.chr_archive.name,
                backup / game.img_index.name, backup / game.img_archive.name, backup)


@dataclass(frozen=True)
class Listing:
    entries: tuple       # every record, in order, repeats included
    first: dict          # normalised path -> its first record


# the drawer asks lacking() on every open, of indexes that only change when
# something is installed, so a parse is kept against the bytes it came from.
# Read only: anything that edits an index parses its own copy
_listings, _listings_lock = {}, threading.Lock()


def listing(raw: bytes) -> Listing:
    from patcher.archive.filelist import parse

    key = hashlib.blake2b(raw, digest_size=16).digest()
    with _listings_lock:
        known = _listings.get(key)
    if known is None:
        entries = tuple(parse(raw).entries())
        first = {}
        for entry in entries:
            first.setdefault(_key(entry.path), entry)
        known = Listing(entries, first)
        with _listings_lock:
            _listings[key] = known
            while len(_listings) > 12:
                _listings.pop(next(iter(_listings)))
    return known


def _zone_raw(side: Side, zone: str) -> bytes:
    """a zone's own index, read out of the image archive that carries it"""
    from patcher.mods.hd_zones import read_entry

    return read_entry(side.img_archive, list(listing(side.img_index.read_bytes()).entries),
                      side.zone_index(zone))


def installed_zones(install_dir, letter: str) -> list:
    """
    the zones HD is in for this voice set: its zone index in the game no longer
    reads the same as the one in the backup. The index rather than the blob's
    size, because an append that never got its index committed grows the blob
    and changes nothing the game can reach.
    """
    from patcher.mods.hd_install import ZONE_BLOB

    have, was = live(install_dir, letter), kept(install_dir, letter)
    if not (was.has_img() and have.has_img()):
        return []
    found = []
    for blob in sorted(was.blobs.glob(f"white_z*{letter}_img.win32.bin")):
        if not ZONE_BLOB.match(blob.name):
            continue
        zone = blob.name.split("_")[1][:-1]
        try:
            if _zone_raw(have, zone) != _zone_raw(was, zone):
                found.append(zone)
        except Exception:
            continue
    return found


def hd_left(install_dir, letter: str) -> tuple:
    """
    (characters, zones): whether a set's indexes still show HD of each, against
    the backup's. What a record is checked by when there's no mod folder to ask,
    since a verify or a redownload can put one set's archives back under it
    """
    from patcher.mods.hd_state import changed_paths

    have, was = live(install_dir, letter), kept(install_dir, letter)
    try:
        chars = was.has_chr() and have.has_chr() and any(
            p.startswith("chr/") for p in changed_paths(have.chr_index, was.chr_index)
            - held(have.chr_index))
    except Exception:
        # unreadable isn't gone, and the record is what's left to go by
        chars = True
    return chars, bool(installed_zones(install_dir, letter))


@dataclass
class Lack:
    """what one voice set has of the HD Project and the other doesn't, by the indexes alone"""
    source: str
    target: str
    files: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    bytes: int = 0
    owed: bool = False      # a copy between them started and never finished

    def __bool__(self) -> bool:
        return bool(self.files or self.zones or self.owed)


def lacking(install_dir, source: str, target: str) -> Lack:
    """
    cheap enough for every drawer open: two index diffs and a few zone indexes,
    no payloads. Sizes stand in for contents here, which is fine for asking
    whether there's work to do; copy() reads the bytes before it does any, and
    a copy that never finished is owed whatever the sizes say. A pass copy()
    would turn down again isn't work for it: the tools build those, and the
    record says which categories that leaves the set short of.
    """
    from patcher.mods.hd_install import voice_sets, voices_with_hd
    from patcher.mods.hd_state import changed_paths

    lack = Lack(source, target)
    sets = voice_sets(Path(install_dir), whole=True)
    if source == target or source not in sets or target not in sets \
            or source not in voices_with_hd(Path(install_dir)):
        return lack
    have, was = live(install_dir, source), kept(install_dir, source)
    there = live(install_dir, target)
    memo, views = _memo(install_dir), {source: _Shipped(install_dir, source),
                                        target: _Shipped(install_dir, target)}
    lack.owed = bool(memo["owed"].get(source + target))
    if was.has_chr():
        grown = sorted(p for p in changed_paths(have.chr_index, was.chr_index) - held(
            have.chr_index) if p.startswith("chr/"))
        if grown and not _stuck(memo, views, source, target, "", {_block(p) for p in grown}):
            mine = listing(have.chr_index.read_bytes()).first
            theirs = listing(there.chr_index.read_bytes()).first
            for path in grown:
                other = theirs.get(path)
                if other is None or other.uncompressed != mine[path].uncompressed:
                    lack.files.append(path)
                    lack.bytes += _aligned(mine[path].stored)

    done, behind = set(installed_zones(install_dir, target)), {}
    for zone in installed_zones(install_dir, source):
        try:
            mine = listing(_zone_raw(have, zone)).first
            vanilla = listing(_zone_raw(was, zone)).first
        except Exception:
            continue
        # a zone only grows, so a record that moved is a file HD rewrote
        grown = [k for k, e in mine.items() if not e.is_stub and k in vanilla
                 and vanilla[k].raw_fields != e.raw_fields]
        if _stuck(memo, views, source, target, zone, {_block(k) for k in grown}):
            behind = {}
            break
        if zone not in done:
            try:
                behind[zone] = max(0, have.blob(zone).stat().st_size
                                   - was.blob(zone).stat().st_size)
            except OSError:
                behind[zone] = 0
            continue
        # one the other set has, from a build this one has replaced since
        try:
            theirs = listing(_zone_raw(there, zone)).first
        except Exception:
            continue
        newer = [k for k in grown
                 if k not in theirs or theirs[k].uncompressed != mine[k].uncompressed]
        if newer:
            behind[zone] = sum(_aligned(mine[k].stored) for k in newer)
    lack.zones = sorted(behind)
    lack.bytes += sum(behind.values())
    return lack


def weigh(install_dir, source: str, fresh: "Fresh", zones) -> int:
    """
    what a copy of `fresh`'s files and `zones` out of `source` puts on the end
    of a set that has none of them: every file HD changed under its paths, and
    each zone's growth past the backup. Off the indexes and the blob sizes, so
    it's what lacking() would say about a set that's never had any of it
    """
    from patcher.mods.hd_state import changed_paths

    have, was = live(install_dir, source), kept(install_dir, source)
    total = 0
    if fresh is not None and fresh.paths and was.has_chr() and have.has_chr():
        mine = listing(have.chr_index.read_bytes()).first
        for path in changed_paths(have.chr_index, was.chr_index) - held(have.chr_index):
            if fresh.has(path) and path in mine and not mine[path].is_stub:
                total += _aligned(mine[path].stored)
    done = set(installed_zones(install_dir, source))
    for zone in zones or ():
        if zone in done:
            try:
                total += max(0, have.blob(zone).stat().st_size - was.blob(zone).stat().st_size)
            except OSError:
                continue
    return total


def writes(install_dir, target: str, characters: bool, zones) -> list:
    """the files of `target`'s a copy writes: the character pair, and for zones the pair
    holding their indexes and each blob"""
    there = live(install_dir, target)
    out = [there.chr_index, there.chr_archive] if characters else []
    if zones:
        out += [p for p in (there.img_index, there.img_archive) if p not in out]
        out += [there.blob(zone) for zone in zones]
    return out


def unbacked(install_dir, files) -> dict:
    """
    name -> bytes, what backing each of `files` up still costs: its size the
    first time a write reaches it, nothing once the HD backup holds a copy,
    the way copy()'s own room check counts it
    """
    from patcher.paths import undo_store_for

    backup = undo_store_for(Path(install_dir)) / "hdproject"
    out = {}
    for path in files:
        try:
            out[Path(path).name] = 0 if (backup / Path(path).name).exists() \
                else Path(path).stat().st_size
        except OSError:
            continue
    return out


# what copy() has found out about the two sets, kept beside the backup: files
# the two languages ship differently, things one set hasn't got, and copies
# that started and never finished. Each finding is checked against the records
# it was made from before it's believed, so Revert, a verify or another mod
# writing an archive can't leave one standing that's no longer true
MEMO = "voices.json"
EVERYTHING = "*"


def _memo(install_dir) -> dict:
    import json
    from patcher.paths import undo_store_for

    try:
        data = json.loads((undo_store_for(Path(install_dir)) / "hdproject" / MEMO)
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("version") != 2:
        data = {}
    return {"version": 2, **{part: data[part] if isinstance(data.get(part), dict) else {}
                             for part in ("differ", "gone", "owed")}}


def _keep(install_dir, memo: dict) -> None:
    """best effort: losing it costs one copy that finds it all out again"""
    import json
    from patcher.fileops import write_atomic
    from patcher.paths import undo_store_for

    path = undo_store_for(Path(install_dir)) / "hdproject" / MEMO
    if not path.parent.is_dir():
        return
    try:
        write_atomic(path, json.dumps(memo, indent=1, sort_keys=True).encode("utf-8"),
                     durable=True)
    except OSError:
        pass


class _Shipped:
    """one set's listings the way copy() compares them, each read once per question"""

    def __init__(self, install_dir, letter: str):
        self.live, self.kept = live(install_dir, letter), kept(install_dir, letter)
        self._read = {}

    def first(self, area: str, vanilla: bool) -> dict:
        """area "" for the characters, or a zone's name for its own index"""
        if (area, vanilla) not in self._read:
            try:
                if not area:
                    side = self.kept if vanilla and self.kept.has_chr() else self.live
                    raw = side.chr_index.read_bytes()
                else:
                    side = (self.kept if vanilla and self.kept.has_img()
                            and self.kept.blob(area).is_file() else self.live)
                    raw = _zone_raw(side, area)
                self._read[area, vanilla] = listing(raw).first
            except Exception:
                self._read[area, vanilla] = None
        return self._read[area, vanilla]


def _witness(first: dict, keys) -> str:
    """the records `keys` have in one listing, as one short digest. Missing counts too"""
    digest = hashlib.blake2b(digest_size=12)
    for key in sorted(keys):
        entry = first.get(key)
        digest.update(f"{key}={entry.raw_fields if entry is not None else '-'};".encode())
    return digest.hexdigest()


def _holds(kind: str, fact: dict, views: dict, target: str) -> bool:
    area = fact.get("area", "")
    if kind == "differ":
        # either way round: the two vanilla copies are what differ
        for letter, view in views.items():
            first = view.first(area, vanilla=True)
            if first is None or _witness(first, fact.get("keys") or []) != fact.get(letter):
                return False
        return True
    there = views[target]
    if "blob" in fact:
        return not there.live.blob(area).is_file()
    if "index" in fact:
        try:
            return _key(there.live.zone_index(area)) not in listing(
                there.live.img_index.read_bytes()).first
        except OSError:
            return False
    if "raw" in fact:
        try:
            return hashlib.blake2b(_zone_raw(there.live, area), digest_size=12).hexdigest() \
                == fact["raw"]
        except Exception:
            return False
    first = there.first(area, vanilla=False)
    return first is not None and bool(fact.get("keys")) \
        and not any(k in first for k in fact["keys"])


def _stuck(memo: dict, views: dict, source: str, target: str, area: str, blocks) -> bool:
    """whether copy() would turn down the pass `blocks` are in, by what it found before"""
    gone = memo["gone"].get(source + target) or {}
    keys = [f"{area}|{b}" for b in blocks]
    if area:
        keys += [f"{area}|{what}" for what in ("blob", "index", "unwritable")]
    for key in keys:
        fact = memo["differ"].get(key)
        if isinstance(fact, dict) and _holds("differ", fact, views, target):
            return True
        fact = gone.get(key)
        if isinstance(fact, dict) and _holds("gone", fact, views, target):
            return True
    return False


def _learn(install_dir, source: str, target: str, facts: list) -> None:
    """what a plan() found, in with the rest, and whatever no longer holds out"""
    memo = _memo(install_dir)
    for kind, key, fact in facts:
        if kind == "differ":
            memo["differ"][key] = fact
        else:
            memo["gone"].setdefault(source + target, {})[key] = fact
    views = {v: _Shipped(install_dir, v) for v in (source, target)}
    memo["differ"] = {k: f for k, f in memo["differ"].items()
                      if isinstance(f, dict) and _holds("differ", f, views, target)}
    memo["gone"][source + target] = {
        k: f for k, f in (memo["gone"].get(source + target) or {}).items()
        if isinstance(f, dict) and _holds("gone", f, views, target)}
    _keep(install_dir, memo)


def owe(install_dir, source: str, target: str, what=None) -> None:
    """
    `target` may be behind `source` until a copy between them finishes: on
    `what`, the categories an install just built into `source`, or on
    everything when None
    """
    memo = _memo(install_dir)
    held = memo["owed"].get(source + target)
    if what is None or held == EVERYTHING:
        memo["owed"][source + target] = EVERYTHING
    else:
        memo["owed"][source + target] = sorted(set(held or []) | {str(c) for c in what})
    _keep(install_dir, memo)


def owed(install_dir, source: str, target: str):
    """None, the categories `target` is still owed from `source`, or EVERYTHING"""
    held = _memo(install_dir)["owed"].get(source + target)
    if held == EVERYTHING:
        return EVERYTHING
    return frozenset(held) if isinstance(held, list) and held else None


def settle(install_dir, source: str, target: str, what) -> None:
    """`target` built `what` itself, from the same mod, so it isn't behind `source` on them"""
    memo = _memo(install_dir)
    held = memo["owed"].get(source + target)
    if not isinstance(held, list):
        # EVERYTHING is a copy that never finished, and a build of a few
        # categories doesn't say the rest got there
        return
    left = sorted(set(held) - {str(c) for c in what})
    if left == sorted(held):
        return
    if left:
        memo["owed"][source + target] = left
    else:
        memo["owed"].pop(source + target)
    _keep(install_dir, memo)


def _paid(install_dir, source: str, target: str, was=None) -> None:
    """
    the copy finished, or it refused before writing anything and `was` is
    what was owed when it started, which goes back as it was
    """
    memo = _memo(install_dir)
    if was is not None:
        if memo["owed"].get(source + target) != was:
            memo["owed"][source + target] = was
            _keep(install_dir, memo)
        return
    if memo["owed"].pop(source + target, None) is not None:
        _keep(install_dir, memo)


def reverted(install_dir) -> None:
    """both sets back to vanilla, so nothing is owed either way"""
    memo = _memo(install_dir)
    if memo["owed"]:
        memo["owed"] = {}
        _keep(install_dir, memo)


class _Reader:
    """payloads out of whichever archives a comparison needs, one handle each"""

    def __init__(self):
        self._open = {}

    def read(self, path: Path, entry) -> bytes:
        handle = self._open.get(path)
        if handle is None:
            handle = self._open[path] = Path(path).open("rb")
        handle.seek(entry.byte_offset)
        data = handle.read(entry.stored)
        if len(data) != entry.stored:
            raise MirrorError(f"{entry.path} is short in {Path(path).name}: {len(data)} of "
                              f"{entry.stored} bytes")
        return data

    def close(self) -> None:
        for handle in self._open.values():
            handle.close()
        self._open.clear()


def _plain(data: bytes, entry) -> bytes:
    return zlib.decompress(data) if entry.is_compressed else data


def _same(reader: _Reader, a_file: Path, a, b_file: Path, b, spots: bool = False) -> bool:
    """
    whether two records hold the same file. `spots` takes the same place and
    sizes as the same bytes across two files, which holds where one file is the
    other with things appended and nowhere a repack has been
    """
    if a.is_stub or b.is_stub:
        return a.is_stub and b.is_stub
    if a.uncompressed != b.uncompressed:
        return False
    if (a.sector_offset, a.stored) == (b.sector_offset, b.stored) and (spots or a_file == b_file):
        return True
    x, y = reader.read(a_file, a), reader.read(b_file, b)
    if a.stored == b.stored and x == y:
        return True
    try:
        # the same file stored two ways: ff13tool -c keeps a few raw that the
        # game's build deflated
        return _plain(x, a) == _plain(y, b)
    except zlib.error:
        return False


@dataclass
class Copy:
    path: str            # spelled as the source index spells it
    entry: object        # the source's record
    file: Path           # the archive its bytes are in


@dataclass
class Plan:
    source: str
    target: str
    files: list = field(default_factory=list)       # Copy, into the target's character archive
    zones: dict = field(default_factory=dict)       # zone -> [Copy], into the target's blob
    blocked: dict = field(default_factory=dict)     # "characters" or "zones" -> why, said plainly
    same: int = 0                                   # changed files the target already holds

    @property
    def bytes(self) -> int:
        copies = self.files + [c for group in self.zones.values() for c in group]
        return sum(_aligned(c.entry.stored) for c in copies)

    def __bool__(self) -> bool:
        return bool(self.files or self.zones)


@dataclass(frozen=True)
class Fresh:
    """
    what an install just built: archive path prefixes, and zones (None for
    every zone). A changed file outside it that the other set already has its
    own copy of, at the same size, is taken as copied by the copy after the run
    that built it rather than read out of both sets again, which was gigabytes
    on every install
    """
    paths: tuple = ()
    zones: frozenset | None = frozenset()

    def has(self, block: str) -> bool:
        return block.startswith(self.paths)

    def has_zone(self, zone: str) -> bool:
        return self.zones is None or zone in self.zones


def _compare(reader, source, was, target, before, plan, letters, area="", prefix="",
             spots=False, fresh=None, cancel=None, on_share=None, skip=frozenset()):
    """
    (copies, refusals) for one archive. Each side is (Listing, file). Every
    pair the source changed, its .trb and .imgb or a file on its own, comes out
    the same in the target as in the source, so the last install's files are in
    both sets whichever one it went into: a repair or a newer build of the mod
    reaches the other set too. Only where each file's vanilla copy in the
    target is the source's, since a file the two languages ship differently
    can't take the other's HD. A refusal is (what, why, kind, memo key, fact).
    `skip` is what the Mods drawer put in, which is its own mod and not HD's.
    """
    (now_list, now_file), (was_list, was_file) = source, was
    (there_list, there_file), (before_list, before_file) = target, before
    changed, count = [], max(1, len(now_list.first))
    for n, (key, now) in enumerate(now_list.first.items()):
        if n % 256 == 0:
            if cancel is not None and cancel.is_set():
                from patcher.mods.hd_runner import Cancelled
                raise Cancelled("stopped while comparing the voice sets")
            if on_share:
                on_share(n / count)
        if not key.startswith(prefix) or now.is_stub or key in skip:
            continue
        old = was_list.first.get(key)
        # nothing an HD install does adds a path, so one with no vanilla copy
        # is somebody else's and not ours to carry
        if old is None:
            continue
        block = _block(key)
        if spots:
            # a zone only ever has things appended, so a record in its old
            # place at its old sizes is its old bytes, and anything else is read
            same = _same(reader, now_file, now, was_file, old, spots=True)
        elif fresh is not None and not fresh(block):
            # outside what this run built, by size the way changed_paths goes:
            # a repack moves every record behind the first file that grew, so
            # places say nothing, and reading every file HD left alone out of
            # both copies was most of the time this took
            same = old.is_stub == now.is_stub and old.uncompressed == now.uncompressed
        else:
            # what it built is read, since a model edit can keep its length
            same = _same(reader, now_file, now, was_file, old)
        if not same:
            changed.append(block)
    # a pair goes as a pair, the half HD left as it was included: headers from
    # one build over pixels from another is what draws as static
    halves = {}
    wanted = set(changed)
    for key in now_list.first:
        if key.startswith(prefix) and _block(key) in wanted:
            halves.setdefault(_block(key), []).append(key)

    copies, refused = [], []
    for block in dict.fromkeys(changed):
        keys = [k for k in halves[block] if k in was_list.first]
        gone = [k for k in keys if k not in there_list.first]
        if gone:
            refused.append((now_list.first[gone[0]].path, "isn't in the other voice set at all",
                            "gone", f"{area}|{block}", {"area": area, "keys": gone}))
            continue
        pairs = [(k, there_list.first[k], now_list.first[k]) for k in keys]
        # a pair this run didn't build, which the target got from the copy
        # after the run that did: the same sizes in both are the same files.
        # Both halves or neither, or a .trb HD left the size of goes unread
        if fresh is not None and not fresh(block) and all(
                not (theirs.is_stub or mine.is_stub) and theirs.uncompressed == mine.uncompressed
                and before_list.first.get(k) is not None
                and before_list.first[k].raw_fields != theirs.raw_fields
                for k, theirs, mine in pairs):
            plan.same += len(keys)
            continue
        moving = [k for k, theirs, mine in pairs
                  if not _same(reader, there_file, theirs, now_file, mine)]
        if not moving:
            plan.same += len(keys)
            continue
        differ = [k for k in keys if k not in before_list.first
                  or not _same(reader, before_file, before_list.first[k],
                               was_file, was_list.first[k])]
        if differ:
            mine_letter, their_letter = letters
            refused.append((now_list.first[differ[0]].path,
                            "isn't the same file in the two voice sets", "differ",
                            f"{area}|{block}",
                            {"area": area, "keys": keys,
                             mine_letter: _witness(was_list.first, keys),
                             their_letter: _witness(before_list.first, keys)}))
            continue
        copies += [Copy(now_list.first[k].path, now_list.first[k], now_file) for k in moving]
    return copies, refused


def _block(key: str) -> str:
    """the pair a file belongs to, its .trb and .imgb by one name"""
    for suffix in (".trb", ".imgb"):
        if key.endswith(suffix):
            return key[:-len(suffix)]
    return key


def plan(install_dir, source: str, target: str, cancel=None, on_share=None,
         fresh: Fresh | None = None) -> Plan:
    """
    what copy() would carry, read off the payloads themselves. Writes nothing
    to the game. `fresh` is what the install before it built, None for
    everything there is.
    """
    from patcher.archive.filelist import parse

    out = Plan(source, target)
    have, was = live(install_dir, source), kept(install_dir, source)
    there, before = live(install_dir, target), kept(install_dir, target)
    reader, facts, letters = _Reader(), [], (source, target)
    try:
        if was.has_chr():
            # a target nobody has written to is its own vanilla copy
            prior = before if before.has_chr() else there
            copies, refused = _compare(
                reader,
                (listing(have.chr_index.read_bytes()), have.chr_archive),
                (listing(was.chr_index.read_bytes()), was.chr_archive),
                (listing(there.chr_index.read_bytes()), there.chr_archive),
                (listing(prior.chr_index.read_bytes()), prior.chr_archive),
                out, letters, prefix="chr/", fresh=fresh.has if fresh else None,
                cancel=cancel, on_share=on_share, skip=held(have.chr_index))
            facts += [fact[2:] for fact in refused]
            if refused:
                out.blocked["characters"] = _refusal(refused, target)
            else:
                out.files = copies

        refused, zones = [], {}
        # every zone this set has, the other set's included: a newer build of
        # one goes across like a newer build of a character does
        for zone in installed_zones(install_dir, source):
            if not there.blob(zone).is_file():
                refused.append((zone, f"has no {there.blob(zone).name}", "gone", f"{zone}|blob",
                                {"area": zone, "blob": True}))
                continue
            try:
                theirs = _zone_raw(there, zone)
            except Exception:
                refused.append((zone, f"has no {zone} index in {there.img_archive.name}", "gone",
                                f"{zone}|index", {"area": zone, "index": True}))
                continue
            prior = before if before.has_img() and before.blob(zone).is_file() else there
            # a zone only ever has things appended to it, so the same place and
            # sizes in the game and in the backup is the same bytes, and the
            # few hundred MB of every zone that HD never touched go unread
            copies, wrong = _compare(
                reader,
                (listing(_zone_raw(have, zone)), have.blob(zone)),
                (listing(_zone_raw(was, zone)), was.blob(zone)),
                (listing(theirs), there.blob(zone)),
                (listing(_zone_raw(prior, zone)), prior.blob(zone)),
                out, letters, area=zone, spots=True,
                fresh=None if fresh is None else (lambda block, z=zone: fresh.has_zone(z)),
                cancel=cancel)
            refused += [(f"{zone} {path}", why, *rest) for path, why, *rest in wrong]
            if not copies:
                continue
            zones[zone] = copies
            # its index is rewritten to point at what goes on the end, so it
            # has to come back out of a rebuild as the bytes that went in
            try:
                problem = _index_problem(parse(theirs), theirs)
            except Exception as e:
                problem = str(e)
            if problem:
                refused.append((zone, f"has a {zone} index this can't write back ({problem})",
                                "gone", f"{zone}|unwritable",
                                {"area": zone,
                                 "raw": hashlib.blake2b(theirs, digest_size=12).hexdigest()}))
        facts += [fact[2:] for fact in refused]
        if refused:
            out.blocked["zones"] = _refusal(refused, target)
        else:
            out.zones = zones
    finally:
        reader.close()
    _learn(install_dir, source, target, facts)
    return out


def _refusal(refused: list, target: str) -> str:
    what, why = refused[0][:2]
    more = f" and {len(refused) - 1} more" if len(refused) > 1 else ""
    return f"{what}{more}: {why}, so those are left to be built from the {LANGUAGE[target]} files"


def _index_problem(book, raw: bytes) -> str:
    """why an index can't be edited and written back exactly, or "" """
    from patcher.archive.filelist import FilelistError, records_problem, rebuild

    if book is None:
        return "it won't read"
    if not book.writable:
        return f"it's the {book.layout} shape, which only gets read"
    try:
        if rebuild(book) != raw:
            return "it doesn't rebuild to the same bytes"
    except FilelistError as e:
        return str(e)
    return records_problem(book)


@dataclass
class Outcome:
    """what copy() did, which is what the run says and the record keeps"""
    source: str
    target: str
    files: int = 0
    zones: list = field(default_factory=list)
    bytes: int = 0
    blocked: dict = field(default_factory=dict)
    already: bool = False      # the target had all of it

    @property
    def whole(self) -> bool:
        return not self.blocked


class _Tail:
    """the end of one archive, written to and able to go back to where it was"""

    def __init__(self, path: Path, digests: bool = True):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        self.handle = None
        self.at = self.size
        # off for a caller that hashes what it reads back itself: blake2b
        # over a gigabyte of mod is seconds for nothing
        self.digests = {} if digests else None

    def put(self, data: bytes) -> int:
        """data at the next sector boundary, returning that sector"""
        if self.handle is None:
            self.handle = self.path.open("r+b")
            self.handle.seek(self.size)
        pad = _aligned(self.at) - self.at
        if pad:
            self.handle.write(b"\x00" * pad)
            self.at += pad
        sector = self.at // SECTOR
        self.handle.write(data)
        self.at += len(data)
        if self.digests is not None:
            self.digests[sector] = hashlib.blake2b(data, digest_size=16).digest()
        return sector

    def close(self) -> None:
        """padded out, flushed and synced, so the next append starts on a boundary too"""
        if self.handle is None:
            return
        pad = _aligned(self.at) - self.at
        if pad:
            self.handle.write(b"\x00" * pad)
            self.at += pad
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        self.handle = None

    def roll_back(self) -> str:
        try:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            if self.path.is_file() and self.path.stat().st_size > self.size:
                with self.path.open("r+b") as handle:
                    handle.truncate(self.size)
        except OSError as e:
            return f"{self.path.name} could not be cut back to its old length ({e})"
        return ""


def copy(install_dir, store: Path, source: str, target: str, on_line=None, on_step=None,
         on_share=None, cancel=None, fresh: Fresh | None = None) -> Outcome:
    """
    the HD Project's files from `source`'s archives into `target`'s, and the
    target's indexes pointed at them. Under the same lock as an install, which
    is what keeps an Apply out of these archives meanwhile.
    `fresh` is what the install just before it built, None for everything.
    """
    from patcher.mods.hd_install import exclusive

    with exclusive(Path(store)):
        return _copy(Path(install_dir), Path(store), source, target, on_line=on_line,
                     on_step=on_step, on_share=on_share, cancel=cancel, fresh=fresh)


def _copy(install_dir: Path, store: Path, source: str, target: str, on_line=None,
          on_step=None, on_share=None, cancel=None, fresh=None) -> Outcome:
    from patcher.mods.hd_install import InstallError, back_up, in_use, suspect_backup

    def say(text, stream="out"):
        if on_line:
            on_line(text, stream)

    going, leaving = LANGUAGE[target], LANGUAGE[source]
    # the steps the indexes say there'll be, so the bar has a length from the
    # start. A pass the payloads turn down is skipped over rather than renumbered
    ahead = lacking(install_dir, source, target)
    labels = [f"comparing the {leaving} and {going} voice files",
              f"backing up the {going} voice files"]
    if ahead.files:
        labels.append(f"character files into the {going} voice files")
    labels += [f"{zone} into the {going} voice files" for zone in sorted(ahead.zones)]
    labels.append(f"swapping in the {going} indexes")

    def stage(label, text=None):
        if on_step:
            on_step(text or label, labels.index(label), len(labels))

    outcome = Outcome(source, target)
    # owed in full until this finishes, so a Stop or the app going down
    # partway leaves the next copy reading everything rather than the sizes,
    # which a same-length rebuild gets past. The two refusals below write
    # nothing, so they put back what was owed before
    was = _memo(install_dir)["owed"].get(source + target)
    owe(install_dir, source, target)
    stage(labels[0])
    work = plan(install_dir, source, target, cancel=cancel, on_share=on_share, fresh=fresh)
    outcome.blocked = dict(work.blocked)
    for what, why in work.blocked.items():
        say(f"[voices] the {what} stay out of the {going} files for now: {why}", "err")
    if not work:
        outcome.already = not work.blocked
        if outcome.already:
            say(f"[voices] the {going} voice files already have everything the {leaving} ones do")
        _paid(install_dir, source, target)
        return outcome

    there = live(install_dir, target)
    zones = sorted(work.zones)
    writes = []
    if work.files:
        writes += [there.chr_index, there.chr_archive]
    if zones:
        if not (there.shared and work.files):
            writes += [there.img_index, there.img_archive]
        writes += [there.blob(z) for z in zones]
    held = in_use(writes)
    if held:
        _paid(install_dir, source, target, was=was)
        raise InstallError(
            f"{', '.join(name for name, _ in held)} cannot be opened for writing, which usually "
            "means the game is running or Steam is verifying it. Close both and try again. "
            "Nothing has been changed.")

    prep = _room(install_dir, store, target, writes, work.bytes + SECTOR * (len(zones) + 1))
    if not prep.enough_room:
        _paid(install_dir, source, target, was=was)
        raise InstallError(
            f"there isn't room to put the HD Project in the {going} voice files: "
            f"{prep.shortfall()}. Free some up and try again. Nothing has been changed.")

    said = [f"{len(work.files):,} character files"] if work.files else []
    if zones:
        said.append(f"{len(zones)} {'zone' if len(zones) == 1 else 'zones'}")
    say(f"[voices] {' and '.join(said)} go into the {going} voice files, "
        f"{work.bytes / 1024 ** 3:.1f} GB" + (f", {work.same:,} were there already"
                                              if work.same else ""))
    stage(labels[1])
    for path, keep in _leftovers(there, store, writes):
        size = path.stat().st_size
        with path.open("r+b") as handle:
            handle.truncate(keep)
        say(f"[voices] {path.name}: {(size - keep) / 1024 ** 2:.1f} MB an earlier copy put on the "
            "end and never pointed anything at, cut off")
    doubt = suspect_backup(None, prep)
    if doubt:
        say(f"[backup] {doubt}", "err")
    back_up(prep, on_line=on_line, cancel=cancel)

    def step(name, text=None):
        if name in labels:
            stage(name, text)

    _write(store, there, work, zones, outcome, step, labels, on_share=on_share, cancel=cancel)
    _paid(install_dir, source, target)
    say(f"[voices] the {going} voice files have the HD Project too, so the voices switch "
        "without waiting on it")
    return outcome


def _leftovers(there: Side, store: Path, writes: list) -> list:
    """
    (file, length to cut it to) where a copy that never got to its index swap,
    the app closed or the power gone, left bytes on the end of an archive.
    Nothing points at them, but a size check reads them as HD. Never below
    the backup's copy of the file, and only for a file the backup holds.
    """
    out = []
    for path in writes:
        kept_copy = store / "hdproject" / path.name
        if path in (there.chr_index, there.img_index) or not (kept_copy.is_file()
                                                              and path.is_file()):
            continue
        try:
            if path in (there.chr_archive, there.img_archive):
                index = there.chr_index if path == there.chr_archive else there.img_index
                entries = listing(index.read_bytes()).entries
            else:
                entries = listing(_zone_raw(there, path.name.split("_")[1][:-1])).entries
        except Exception:
            continue
        end = max((e.byte_offset + e.stored for e in entries if not e.is_stub), default=0)
        keep = max(kept_copy.stat().st_size, _aligned(end))
        if path.stat().st_size > keep:
            out.append((path, keep))
    return out


def _room(install_dir: Path, store: Path, target: str, writes: list, grows: int):
    """a Prep for the backup the target set needs, whose budgets say whether it all fits"""
    from patcher.download import free_space
    from patcher.mods.hd_install import Prep, _same_drive

    backup = store / "hdproject"
    anchor = backup
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    taking = [p for p in writes if not (backup / p.name).exists()]
    return Prep(variant=target, archives=writes[:2], extra=writes[2:], backup_dir=backup,
                needed_bytes=0, free_bytes=0,
                backup_bytes=sum(p.stat().st_size for p in taking),
                backup_free=free_space(anchor),
                swap_bytes=grows + SLACK,
                game_free=free_space(install_dir),
                game_with_backup=_same_drive(install_dir, anchor))


def _write(store: Path, there: Side, work: Plan, zones: list, outcome: Outcome, step,
           labels: list, on_share=None, cancel=None) -> None:
    """
    appends, then the new indexes in place of the old, then a read back. Up to
    the swap a failure cuts every archive back to its old length; after it the
    old index goes back too, and anything that can't be put back says to Revert.
    """
    from patcher.archive.filelist import parse, rebuild, relocate_many
    from patcher.fileops import write_atomic
    from patcher.mods.hd_install import InstallError, mark_written, unmark_written
    from patcher.mods.hd_runner import Cancelled

    going = LANGUAGE[there.letter]
    tails, reader = {}, _Reader()
    # one index on XIII-2, where the zone indexes share the characters' archive
    moves = {there.chr_index: {}, there.img_index: {}}
    archive_of = {there.chr_index: there.chr_archive, there.img_index: there.img_archive}
    swapped, marked = [], False
    done, size = 0, max(1, work.bytes)

    def tail(path):
        if path not in tails:
            tails[path] = _Tail(path)
        return tails[path]

    def carry(copies, into):
        nonlocal done
        placed = {}
        for each in copies:
            if cancel is not None and cancel.is_set():
                raise Cancelled(f"stopped before the {going} voice files changed")
            data = reader.read(each.file, each.entry)
            placed[each.path] = (tail(into).put(data), each.entry.uncompressed, len(data))
            done += len(data)
            if on_share:
                on_share(min(done / size, 1.0))
        return placed

    def undo() -> list:
        left = []
        for index, raw in reversed(swapped):
            try:
                write_atomic(index, raw, durable=True)
            except OSError as e:
                left.append(f"{index.name} could not be put back ({e})")
        if left:
            # an index the game reads still points at what went on the end, so
            # that stays. Cut back, it would point past the end of its archive
            return left
        left += [x for x in (t.roll_back() for t in tails.values()) if x]
        if marked and not left:
            unmark_written(store / "hdproject")
        return left

    try:
        if work.files:
            step(f"character files into the {going} voice files",
                 f"copying {len(work.files):,} character files into {there.chr_archive.name}")
            moves[there.chr_index].update(carry(work.files, there.chr_archive))
        for zone in zones:
            step(f"{zone} into the {going} voice files")
            book = parse(_zone_raw(there, zone))
            relocate_many(book, carry(work.zones[zone], there.blob(zone)))
            index = rebuild(book)
            # stored as is, the way ff13tool -i puts a zone's index back
            moves[there.img_index][there.zone_index(zone)] = (
                tail(there.img_archive).put(index), len(index), len(index))
        for each in tails.values():
            each.close()

        rewritten = []
        for index, wanted in moves.items():
            if not wanted:
                continue
            raw = index.read_bytes()
            book = parse(raw)
            problem = _index_problem(book, raw)
            if problem:
                raise MirrorError(f"{index.name} can't be written back exactly ({problem})")
            relocate_many(book, wanted)
            rewritten.append((index, raw, rebuild(book), wanted))

        step(labels[-1])
        try:
            marked = mark_written(store / "hdproject")
        except OSError as e:
            raise MirrorError(f"the backup's record could not be updated ({e}), and Revert "
                              "goes by it") from e
        for index, raw, new, _ in rewritten:
            write_atomic(index, new, durable=True)
            swapped.append((index, raw))
        for index, raw, _, wanted in rewritten:
            problem = _read_back(index, archive_of[index], raw, wanted, tails, there)
            if problem:
                raise MirrorError(f"{index.name} didn't read back as written ({problem})")
        outcome.files = len(work.files)
        outcome.zones = list(zones)
        outcome.bytes = sum(t.at - t.size for t in tails.values())
    except BaseException as e:
        reader.close()
        left = undo()
        if left:
            raise InstallError(
                f"putting the HD Project in the {going} voice files failed ({e}) and couldn't "
                f"be fully undone: {'; '.join(left)}. Put the game back with Revert before "
                "playing.") from e
        if isinstance(e, (Cancelled, InstallError)) or not isinstance(e, Exception):
            raise
        raise InstallError(f"the HD Project didn't go into the {going} voice files ({e}). "
                           "They're as they were.") from e
    reader.close()


def _read_back(index: Path, archive: Path, before: bytes, moves: dict, tails: dict,
               there: Side) -> str:
    """
    the swapped in index off the disk: every move where it was put, every
    other record where it was, nothing past the end, and the newest bytes
    reading back as what was written
    """
    from patcher.archive.filelist import load, parse

    try:
        now = list(load(index).entries())
    except Exception as e:
        return f"it won't parse ({e})"
    size = archive.stat().st_size
    wanted = {_key(p): spot for p, spot in moves.items()}
    for entry in now:
        spot = wanted.get(_key(entry.path))
        if spot is not None and entry.raw_fields != spot:
            return f"{entry.path} isn't where it was put"
        if not entry.is_stub and entry.byte_offset + entry.stored > size:
            return f"{entry.path} points past the end of {archive.name}"
    rest = Counter((_key(e.path), e.raw_fields) for e in parse(before).entries()
                   if _key(e.path) not in wanted)
    if rest != Counter((_key(e.path), e.raw_fields) for e in now if _key(e.path) not in wanted):
        return "records it wasn't asked to move moved"

    reader = _Reader()
    try:
        by_key = {_key(e.path): e for e in now}
        tail = tails.get(archive)
        newest = sorted((k for k in wanted if k in by_key),
                        key=lambda k: by_key[k].sector_offset)[-4:]
        for key in newest:
            entry = by_key[key]
            digest = hashlib.blake2b(reader.read(archive, entry), digest_size=16).digest()
            if tail is None or tail.digests.get(entry.sector_offset) != digest:
                return f"{entry.path} reads back as something else"
        # and each zone's own index, down to its newest entries in its blob
        for key in wanted:
            if not key.startswith("zone/filelist_"):
                continue
            zone = key.split("_")[1].split(".")[0][:-1]
            inner = parse(reader.read(archive, by_key[key])).entries()
            blob, blob_tail = there.blob(zone), tails.get(there.blob(zone))
            blob_size = blob.stat().st_size
            if any(not e.is_stub and e.byte_offset + e.stored > blob_size for e in inner):
                return f"{zone}'s index points past the end of {blob.name}"
            fresh = sorted((e for e in inner if blob_tail and e.byte_offset >= blob_tail.size),
                           key=lambda e: e.sector_offset)[-2:]
            for entry in fresh:
                digest = hashlib.blake2b(reader.read(blob, entry), digest_size=16).digest()
                if blob_tail.digests.get(entry.sector_offset) != digest:
                    return f"{zone} {entry.path} reads back as something else"
    except Exception as e:
        return str(e)
    finally:
        reader.close()
    return ""
