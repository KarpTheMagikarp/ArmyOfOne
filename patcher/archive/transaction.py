"""
Installing a mod across every language archive as one operation.

XIII-2 ships its data twice (white_imgu/filelistu English, white_imgc/filelistc
Japanese), and a mod in one but not the other is a bug nobody can see. So:

    1  validate everything, write nothing: pre-flight each index against
       itself, check every target entry reads back and every replacement fits
    2  write, recording undo as we go
    3  read back what we wrote
    rollback on any failure in 2 or 3, every archive, in reverse

Nearly everything that can go wrong is caught in 1, so rollback is rare.
Filelists (a few hundred KB) are backed up as plain copies; white_img (8 to
20GB) goes through spanbackup for a few KB.
"""

import hashlib
import os
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from patcher.archive import filelist
from patcher.archive.entry import SECTOR
from patcher.archive.filelist import FilelistError, VerificationFailed
from patcher.archive.inject import InjectError, Replacement, encode, plan
from patcher.archive.spanbackup import SpanBackup, discard, restore
from patcher.fileops import copy_atomic, write_atomic

CHUNK = 1024 * 1024


class TransactionError(Exception):
    pass


@dataclass
class ArchivePair:
    """one language's index and data file"""
    variant: str                 # "u" for English, "c" for Japanese
    filelist_path: Path
    whitebin_path: Path

    def exists(self) -> bool:
        return self.filelist_path.is_file() and self.whitebin_path.is_file()


@dataclass
class ArchivePlan:
    pair: ArchivePair
    index: object                                  # a parsed Filelist
    writes: list = field(default_factory=list)     # (ChunkEntry, encoded bytes, raw size)

    def as_dict(self) -> dict:
        return {"variant": self.pair.variant,
                "writes": [{"path": e.path, "offset": e.byte_offset,
                            "stored": len(blob), "span": e.sector_span * SECTOR}
                           for e, blob, _ in self.writes]}


def discover(install_dir: Path, archive_dir: str, variants=("u", "c")) -> list:
    """
    the archive pairs present in this install.

    a variant with no files is skipped rather than failed: not every release
    ships both, and refusing to patch an English-only install because there is
    no Japanese archive would be absurd.
    """
    root = Path(install_dir) / archive_dir
    found = []
    for variant in variants:
        pair = ArchivePair(
            variant=variant,
            filelist_path=root / f"filelist{variant}.win32.bin",
            whitebin_path=root / f"white_img{variant}.win32.bin",
        )
        if pair.exists():
            found.append(pair)
    return found


def prepare(pairs: list, replacements: list) -> list:
    """
    PHASE 1. validates every archive and writes nothing.

    raises on the first problem, because a partial plan is not useful: if the
    Japanese archive cannot take this mod, installing it into the English one
    produces exactly the inconsistent state this module exists to prevent.
    """
    if not pairs:
        raise TransactionError("no language archives found in this install")

    plans = []
    for pair in pairs:
        raw = pair.filelist_path.read_bytes()
        try:
            # the pre-flight: prove we can reproduce THIS index, whatever
            # state it is in, before earning the right to rewrite it
            filelist.verify_roundtrip(raw)
        except (VerificationFailed, FilelistError) as e:
            raise TransactionError(
                f"{pair.filelist_path.name}: {e}. nothing has been changed"
            ) from e

        index = filelist.parse(raw)
        resolved = plan(index.entries(), replacements)
        if resolved.rejected:
            reasons = "; ".join(f"{p}: {r}" for p, r in resolved.rejected)
            raise TransactionError(f"{pair.filelist_path.name}: {reasons}")
        if not resolved.replacements:
            raise TransactionError(f"{pair.filelist_path.name}: nothing to inject")

        # the undo is taken from what's there, so a pair that already
        # disagrees with itself would have that put back as the original. A
        # Revert stopped between its two halves leaves exactly that, with the
        # row reading not installed
        size = pair.whitebin_path.stat().st_size
        with pair.whitebin_path.open("rb") as handle:
            for entry, blob in resolved.replacements:
                if entry.byte_offset + len(blob) > size:
                    raise TransactionError(f"{pair.whitebin_path.name}: {entry.path} would run "
                                           "past the end of it. nothing has been changed")
                if not _reads_back(handle, entry):
                    from patcher.mods.hd_layout import layout_of

                    revert = layout_of(pair.filelist_path.parents[2]).revert
                    raise TransactionError(
                        f"{entry.path} in {pair.whitebin_path.name} doesn't read back as "
                        f"{pair.filelist_path.name} says it should, so nothing has been "
                        f"changed. If a {revert} stopped partway, run it again to finish it. "
                        "Otherwise verify the game files through Steam.")

        sizes = {item.game_path: len(item.payload) for item in replacements}
        plans.append(ArchivePlan(
            pair=pair, index=index,
            writes=[(entry, blob, sizes[entry.path]) for entry, blob in resolved.replacements],
        ))
    return plans


def _reads_back(handle, entry) -> bool:
    """whether an entry's bytes are what its index line says they are"""
    handle.seek(entry.byte_offset)
    data = handle.read(entry.stored)
    if len(data) != entry.stored:
        return False
    try:
        return not entry.is_compressed or len(zlib.decompress(data)) == entry.uncompressed
    except zlib.error:
        return False


@dataclass
class Undo:
    """everything needed to put one archive back"""
    pair: ArchivePair
    journal: Path | None = None          # white_img span journal
    filelist_backup: Path | None = None  # a plain copy, it is small
    # sha256 of the copy and of the index we wrote over it, which is how a
    # revert knows the copy is the one taken and nothing has rewritten the index
    taken: str = ""
    written: str = ""

    def roll_back(self) -> None:
        if self.journal is not None:
            restore(self.journal)
        if self.filelist_backup is not None and self.filelist_backup.is_file():
            copy_atomic(self.filelist_backup, self.pair.filelist_path)

    def release(self) -> None:
        if self.journal is not None:
            discard(self.journal)
        if self.filelist_backup is not None:
            self.filelist_backup.unlink(missing_ok=True)


def _write_payloads(undo: Undo, writes: list, store: Path, on_progress=None,
                    cancel=None) -> None:
    """injects into white_img, journalling each span first"""
    pair = undo.pair
    backup = SpanBackup(
        pair.whitebin_path, store,
        metadata={"variant": pair.variant,
                  "entries": [{"path": e.path, "sector_offset": e.sector_offset,
                               "uncompressed": e.uncompressed, "stored": e.stored}
                              for e, _, _ in writes]},
    )
    total_size = pair.whitebin_path.stat().st_size
    for entry, blob, _ in writes:
        # the whole span, not just the bytes we overwrite. a shorter
        # replacement leaves the tail of the original in place, and restoring
        # only the written part would leave a hybrid of both files.
        span = min(entry.sector_span * SECTOR, total_size - entry.byte_offset)
        backup.record(entry.byte_offset, span, label=entry.path)
    # on the undo before the first write, so Stop or a failed write after it
    # rolls back what did land
    undo.journal = backup.commit()

    written, total = 0, sum(len(b) for _, b, _ in writes)
    with pair.whitebin_path.open("rb+") as handle:
        for entry, blob, _ in writes:
            if cancel is not None and cancel.is_set():
                raise TransactionError("cancelled")
            handle.seek(entry.byte_offset)
            handle.write(blob)
            padding = min(entry.sector_span * SECTOR - len(blob),
                          total_size - entry.byte_offset - len(blob))
            while padding > 0:
                block = min(CHUNK, padding)
                handle.write(b"\x00" * block)
                padding -= block
            written += len(blob)
            if on_progress:
                from patcher.download import Progress
                on_progress(Progress("inject", written, total,
                                     f"{pair.variant}: {Path(entry.path).name}"))
        # on disk before the index that describes it is
        handle.flush()
        os.fsync(handle.fileno())
    # written in place at the same size, which a drive with coarse timestamps
    # may not even date, so the Mods drawer's hashes of it can't be trusted
    from patcher.mods.game_files import forget_contents
    forget_contents(pair.whitebin_path)


def _write_index(undo: Undo, plan_for_pair: ArchivePlan, store: Path) -> None:
    """rewrites the index to match what we just put in white_img"""
    store.mkdir(parents=True, exist_ok=True)
    backup = store / f"{plan_for_pair.pair.filelist_path.name}.backup"
    taken = copy_atomic(plan_for_pair.pair.filelist_path, backup)

    for entry, blob, raw_size in plan_for_pair.writes:
        filelist.replace_entry(plan_for_pair.index, entry.path,
                               uncompressed=raw_size, stored=len(blob))

    rebuilt = filelist.rebuild(plan_for_pair.index)
    # durable, so neither a crash nor a power cut leaves a truncated index,
    # which is the one thing that stops the game booting at all
    write_atomic(plan_for_pair.pair.filelist_path, rebuilt, durable=True)
    undo.filelist_backup, undo.taken = backup, taken
    undo.written = hashlib.sha256(rebuilt).hexdigest()


def _verify(pair: ArchivePair, writes: list) -> None:
    """PHASE 3. reads back what we wrote rather than assuming it took."""
    index = filelist.parse(pair.filelist_path.read_bytes())
    with pair.whitebin_path.open("rb") as handle:
        for entry, blob, raw_size in writes:
            _, updated = index.locate(entry.path)
            if updated is None:
                raise TransactionError(f"{entry.path} vanished from {pair.filelist_path.name}")
            if updated.stored != len(blob) or updated.uncompressed != raw_size:
                raise TransactionError(
                    f"{entry.path}: index says {updated.stored}/{updated.uncompressed}, "
                    f"we wrote {len(blob)}/{raw_size}")
            handle.seek(updated.byte_offset)
            data = handle.read(updated.stored)
            if len(data) != updated.stored:
                raise TransactionError(f"{entry.path}: short read from {pair.whitebin_path.name}")
            if updated.is_compressed:
                try:
                    plain = zlib.decompress(data)
                except zlib.error as e:
                    raise TransactionError(f"{entry.path}: written data will not inflate ({e})")
                if len(plain) != raw_size:
                    raise TransactionError(
                        f"{entry.path}: inflated to {len(plain)}, expected {raw_size}")


def apply_injection(install_dir: Path, archive_dir: str, replacements: list,
                    store: Path, variants=("u", "c"), on_progress=None,
                    cancel=None, keep_undo: bool = True) -> dict:
    """
    installs a set of replacements into every language archive, atomically.
    Returns a summary; on any failure everything's rolled back and the
    original exception raised. Claims the game's archives first, since an HD
    install can be rewriting the same white_img and index for an hour.
    """
    from patcher.fileops import Held, claim

    try:
        held = claim(store)
    except Held:
        raise TransactionError(
            "another run is working on this game's archives right now, most likely the HD "
            "install. Let it finish, then try again. Nothing has been changed.") from None
    except OSError as e:
        raise TransactionError(
            f"couldn't create the undo folder at {store}: {e.strerror or e}. "
            "nothing has been changed") from e
    with held:
        return _apply_injection(install_dir, archive_dir, replacements, store, variants,
                                on_progress, cancel, keep_undo)


def _apply_injection(install_dir, archive_dir, replacements, store, variants, on_progress,
                     cancel, keep_undo) -> dict:
    pairs = discover(install_dir, archive_dir, variants)
    prepared = prepare(pairs, replacements)       # phase 1, writes nothing
    # a pair that already holds every payload is left alone. Written again,
    # its journal would keep the payload as its own original, and nothing
    # could take it off that pair afterwards. carry_undo() keeps its old undo.
    wanted = {_key(r.game_path): r.payload for r in replacements}
    kept = [p.pair for p in prepared if _already_there(p, wanted)]
    plans = [p for p in prepared if p.pair not in kept]

    # every run gets its own folder in the store, since _write_index names its
    # backup after the archive. Shared, the second run's already injected copy
    # replaced the first's vanilla one. mkdtemp so same-second runs can't
    # collide.
    root = Path(store)
    try:
        root.mkdir(parents=True, exist_ok=True)
        store = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    except OSError as e:
        # this runs after prepare() proved the archives are rewritable, so
        # failing here means the undo store specifically is unreachable. a
        # raw OSError is not a TransactionError, so it escaped both the
        # caller's handler and the rollback below: the one place in this
        # module where an exception could leave phase 2 unguarded.
        raise TransactionError(
            f"couldn't create the undo folder at {store}: {e.strerror or e}. "
            "nothing has been changed") from e
    undos: list = []

    try:
        for archive_plan in plans:                # phase 2
            undo = Undo(pair=archive_plan.pair)
            undos.append(undo)
            _write_payloads(undo, archive_plan.writes, store,
                            on_progress=on_progress, cancel=cancel)
            _write_index(undo, archive_plan, store)

        for archive_plan in plans:                # phase 3
            _verify(archive_plan.pair, archive_plan.writes)
    except Exception as e:
        # reverse order, so an archive touched later is undone first, and one
        # that won't go back doesn't stop the others
        stuck = []
        for undo in reversed(undos):
            try:
                undo.roll_back()
            except Exception:
                stuck.append(undo.pair.whitebin_path.name)
        if stuck:
            # nothing gets recorded for a run that failed, so nothing could
            # finish this later either
            raise TransactionError(
                f"{e}, and {' and '.join(stuck)} couldn't be put back after it, so it may "
                "not agree with its index. Verify the game files through Steam before "
                "playing") from e
        raise

    if not undos:
        try:
            store.rmdir()
        except OSError:
            pass
    summary = {
        "ok": True,
        "archives": [p.pair.variant for p in plans],
        "kept": [{"variant": pair.variant, "image": str(pair.whitebin_path),
                  "index": str(pair.filelist_path)} for pair in kept],
        "files": sorted({e.path for p in prepared for e, _, _ in p.writes}),
        "undo": [str(u.journal) for u in undos if u.journal],
        # the index backup has to be handed back along with the journals, or
        # revert restores the data under an index that still describes the
        # injected layout. The destination travels with it since the backup's
        # name doesn't say which folder it came from.
        "index_backups": [{"backup": str(u.filelist_backup),
                           "target": str(u.pair.filelist_path),
                           "sha256": u.taken, "written": u.written}
                          for u in undos if u.filelist_backup],
        # the same undo as data: the two numbers per entry the injection
        # changed. Losing the copy above then isn't a Steam verify (tens of GB
        # on XIII) to undo a 90KB mod. Safe to replay since replace_entry
        # refuses length changes, and revert checks the offset and payload
        # first, since the numbers only mean anything while the entry is ours.
        "index_entries": [{"target": str(plan.pair.filelist_path),
                           "entries": [{"path": entry.path,
                                        "sector_offset": entry.sector_offset,
                                        "uncompressed": entry.uncompressed,
                                        "stored": entry.stored,
                                        "injected": [raw_size, len(blob)]}
                                       for entry, blob, raw_size in plan.writes]}
                          for plan in plans],
    }
    if not keep_undo:
        for undo in undos:
            undo.release()
        summary["undo"] = []
        summary["index_backups"] = []
        # cleared with the rest of it. without the journals there is nothing to
        # put these numbers back on top of, so keeping them would advertise an
        # undo that cannot happen
        summary["index_entries"] = []
        # the run folder is ours and is now empty, so take it with us rather
        # than leaving a directory per injection behind in the store
        try:
            store.rmdir()
        except OSError:
            pass
    return summary


def _key(game_path: str) -> str:
    return game_path.replace("\\", "/").lower()


def _already_there(archive_plan: ArchivePlan, wanted: dict) -> bool:
    """whether every entry this plan would write already holds exactly its payload"""
    try:
        with archive_plan.pair.whitebin_path.open("rb") as handle:
            for entry, _, _ in archive_plan.writes:
                handle.seek(entry.byte_offset)
                held = handle.read(entry.stored)
                if entry.is_compressed:
                    held = zlib.decompress(held)
                if held != wanted.get(_key(entry.path)):
                    return False
    except (OSError, zlib.error):
        return False
    return True


def carry_undo(result: dict, earlier: dict | None) -> dict:
    """
    the undo to record: what this run wrote, plus an earlier record's undo for
    pairs this run found already holding the payload (it's the only undo they
    have). A pair's index goes with its journal or not at all.
    """
    import os

    from patcher.archive.spanbackup import load_journal

    def spot(path) -> str:
        return os.path.normcase(os.path.realpath(str(path)))

    kept = result.get("kept", [])
    images = {spot(k["image"]): k for k in kept}

    def pair_of(journal):
        # a journal that will not load still names its archive, since it is
        # "<archive>.<stamp>.journal", and carried like that the revert's own
        # check refuses it as incomplete rather than half undoing the pair
        try:
            return images.get(spot(load_journal(Path(journal)).target))
        except Exception:
            name = Path(journal).name.lower()
            return next((k for k in kept
                         if name.startswith(Path(k["image"]).name.lower() + ".")), None)

    earlier = earlier if isinstance(earlier, dict) else {}
    carried = [(j, pair_of(j)) for j in earlier.get("journals", [])]
    carried = [(j, k) for j, k in carried if k is not None]
    indexes = {spot(k["index"]) for _, k in carried}
    return {
        "journals": list(result.get("undo", [])) + [j for j, _ in carried],
        "index_backups": list(result.get("index_backups", [])) + [
            spec for spec in earlier.get("index_backups", [])
            if spec.get("target") and spot(spec["target"]) in indexes],
        "index_entries": list(result.get("index_entries", [])) + [
            spec for spec in earlier.get("index_entries", [])
            if spec.get("target") and spot(spec["target"]) in indexes],
        "archives": sorted(set(result.get("archives", [])) | {k["variant"] for _, k in carried}),
    }


def roll_back_all(undos: list) -> None:
    for undo in reversed(undos):
        undo.roll_back()
