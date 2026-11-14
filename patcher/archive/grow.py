"""
Putting a replacement that doesn't fit at the end of white_img instead.

inject.py refuses anything bigger than its span, which suits same-size swaps
like Leviathan's Tears but not an HD pack where every replacement is bigger.
white_img is just sector aligned payloads addressed by the index, so the new
bytes go on the end, on a sector boundary, and only that entry's offset and
sizes change. The hole stays, same trade the mod's own installer makes.

The old bytes are left alone on purpose: nothing's ever overwritten, so undo
is truncate to the original length and put the index back. Reinstalling
appends again; revert's truncate reclaims all of it. Growing a record's text
needs filelist.relocate_entry to fix up the lookup records.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from patcher.archive import filelist as filelist_mod
from patcher.archive.entry import SECTOR
from patcher.archive.inject import InjectError, encode

CHUNK = 1024 * 1024


class GrowError(InjectError):
    pass


@dataclass
class GrowUndo:
    """
    what it takes to put one archive back.

    an original length and a copy of the index, and that is the whole of it.
    """
    white_bin: Path
    filelist_path: Path
    original_size: int
    filelist_backup: Path | None = None

    def roll_back(self) -> None:
        if self.filelist_backup is not None and self.filelist_backup.is_file():
            shutil.copyfile(self.filelist_backup, self.filelist_path)
        if self.white_bin.is_file() and self.white_bin.stat().st_size > self.original_size:
            with self.white_bin.open("rb+") as handle:
                handle.truncate(self.original_size)

    def release(self) -> None:
        if self.filelist_backup is not None:
            self.filelist_backup.unlink(missing_ok=True)

    def as_dict(self) -> dict:
        return {"white_bin": str(self.white_bin), "filelist": str(self.filelist_path),
                "original_size": self.original_size,
                "filelist_backup": str(self.filelist_backup) if self.filelist_backup else None}


@dataclass
class GrowPlan:
    appends: list = field(default_factory=list)   # (game_path, blob, uncompressed)
    rejected: list = field(default_factory=list)  # (game_path, reason)

    @property
    def added_bytes(self) -> int:
        return sum(_aligned(len(blob)) for _, blob, _ in self.appends)


def _aligned(size: int) -> int:
    """size rounded up to the next sector boundary"""
    return -(-size // SECTOR) * SECTOR


def plan(index, replacements: list) -> GrowPlan:
    """
    works out what would be appended. writes nothing, reads no payload twice.

    an entry that is not in this index is rejected rather than added, because
    the index is the only thing that makes a payload reachable: appending bytes
    nothing points at would grow the file and change nothing about the game.
    """
    result = GrowPlan()
    known = {e.path.replace("\\", "/").lower() for e in index.entries()}

    for item in replacements:
        wanted = item.game_path.replace("\\", "/").lower()
        if wanted not in known:
            result.rejected.append((item.game_path, "no entry with that path in the index"))
            continue
        blob = encode(item.payload, item.compress)
        result.appends.append((item.game_path, blob, len(item.payload)))
    return result


def grow(white_bin: Path, filelist_path: Path, replacements: list, store: Path,
         on_progress=None, cancel=None, dry_run: bool = False) -> GrowUndo | None:
    """
    appends every replacement and repoints the index at it.

        1. prove we can reproduce this index, and resolve every replacement,
           touching nothing
        2. copy the index aside and record how long white_img is
        3. append the payloads
        4. write the rewritten index
        5. read it back and check every entry lands where we said
        6. on any failure, truncate and put the index back

    5 isn't optional: a mismatched index looks like a working game until it
    loads that asset.
    """
    white_bin, filelist_path, store = Path(white_bin), Path(filelist_path), Path(store)
    if not white_bin.is_file():
        raise GrowError(f"{white_bin} does not exist")

    raw = filelist_path.read_bytes()
    try:
        filelist_mod.verify_roundtrip(raw)
    except (filelist_mod.VerificationFailed, filelist_mod.FilelistError) as e:
        raise GrowError(f"{filelist_path.name}: {e}. nothing has been changed") from e

    index = filelist_mod.parse(raw)
    resolved = plan(index, replacements)
    if resolved.rejected:
        raise GrowError("; ".join(f"{p}: {r}" for p, r in resolved.rejected))
    if not resolved.appends:
        raise GrowError("nothing to append")

    if dry_run:
        return None

    original_size = white_bin.stat().st_size
    store.mkdir(parents=True, exist_ok=True)
    backup = store / f"{filelist_path.name}.backup"
    shutil.copyfile(filelist_path, backup)
    undo = GrowUndo(white_bin=white_bin, filelist_path=filelist_path,
                    original_size=original_size, filelist_backup=backup)

    try:
        placements = []
        with white_bin.open("rb+") as handle:
            handle.seek(0, 2)
            cursor = handle.tell()
            written, total = 0, sum(len(b) for _, b, _ in resolved.appends)

            for game_path, blob, uncompressed in resolved.appends:
                if cancel is not None and cancel.is_set():
                    raise GrowError("cancelled")
                # pad to the next sector boundary before writing, because the
                # index addresses payloads in sectors and cannot describe one
                # that starts anywhere else
                pad = _aligned(cursor) - cursor
                if pad:
                    handle.write(b"\x00" * pad)
                    cursor += pad

                handle.write(blob)
                placements.append((game_path, cursor // SECTOR, uncompressed, len(blob)))
                cursor += len(blob)
                written += len(blob)
                if on_progress:
                    from patcher.download import Progress
                    on_progress(Progress("append", written, total, game_path))

            # the last payload gets padding too, so the next install to run
            # starts aligned and does not have to care what came before it
            pad = _aligned(cursor) - cursor
            if pad:
                handle.write(b"\x00" * pad)
            handle.flush()
            import os
            os.fsync(handle.fileno())

        for game_path, sector, uncompressed, stored in placements:
            filelist_mod.relocate_entry(index, game_path, sector, uncompressed, stored)
        filelist_mod.save(filelist_path, index)

        _verify(white_bin, filelist_path, placements)
    except BaseException:
        undo.roll_back()
        raise

    return undo


def _verify(white_bin: Path, filelist_path: Path, placements: list) -> None:
    """
    re-reads the index off disk and checks every entry points where we put it.

    off disk, not from the object in memory: the point is to catch a rebuild
    that did not survive the round trip, and asking the same object we just
    edited would agree with itself no matter what went wrong.
    """
    reread = filelist_mod.load(filelist_path)
    by_path = {e.path.replace("\\", "/").lower(): e for e in reread.entries()}
    size = white_bin.stat().st_size

    for game_path, sector, uncompressed, stored in placements:
        entry = by_path.get(game_path.replace("\\", "/").lower())
        if entry is None:
            raise GrowError(f"{game_path} vanished from the index after the rewrite")
        if entry.sector_offset != sector:
            raise GrowError(
                f"{game_path} should point at sector {sector} and points at "
                f"{entry.sector_offset}")
        if entry.stored != stored or entry.uncompressed != uncompressed:
            raise GrowError(f"{game_path} came back with the wrong sizes")
        if entry.byte_offset + entry.stored > size:
            raise GrowError(f"{game_path} points past the end of {white_bin.name}")
