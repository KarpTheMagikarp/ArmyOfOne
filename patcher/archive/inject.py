"""
Replacing a file inside white_img without rewriting the container.

Each entry owns its stored bytes plus padding up to the next 2048-byte sector,
so a replacement that fits that span goes in without moving anything. One that
doesn't is refused here; grow.py appends those instead.

    1. resolve and validate everything, touching nothing
    2. record the original spans, fsync the journal
    3. write the payload into white_img
    4. write the rewritten index
    5. verify by reading back
    6. drop the journal only once 5 passes

After 2, any failure is recoverable from the journal; before it, nothing's
changed.
"""

import zlib
from dataclasses import dataclass, field
from pathlib import Path

from patcher.archive.entry import SECTOR, ChunkEntry, find
from patcher.archive.spanbackup import SpanBackup, restore

CHUNK = 1024 * 1024


class InjectError(Exception):
    pass


class DoesNotFit(InjectError):
    """the replacement is larger than the span it has to live in"""


@dataclass
class Replacement:
    game_path: str
    payload: bytes          # the raw, uncompressed asset
    compress: bool = True


@dataclass
class Plan:
    """what an injection will do, worked out before anything is written"""
    replacements: list = field(default_factory=list)   # (ChunkEntry, bytes stored)
    rejected: list = field(default_factory=list)       # (game_path, reason)

    @property
    def ok(self) -> bool:
        return bool(self.replacements) and not self.rejected

    def as_dict(self) -> dict:
        return {
            "willWrite": [
                {"path": e.path, "offset": e.byte_offset,
                 "originalStored": e.stored, "newStored": len(blob),
                 "spanBytes": e.sector_span * SECTOR}
                for e, blob in self.replacements
            ],
            "rejected": [{"path": p, "reason": r} for p, r in self.rejected],
        }


def encode(payload: bytes, compress: bool) -> bytes:
    """
    the bytes that will actually sit in white_img.

    zlib level 9 on purpose: this runs once on a file the user will keep, and
    a smaller result is more likely to fit the span it has to live in. the
    seconds it costs are irrelevant next to the injection failing for the sake
    of a few hundred bytes.
    """
    return zlib.compress(payload, 9) if compress else payload


def plan(entries: list[ChunkEntry], replacements: list[Replacement]) -> Plan:
    """
    works out whether an injection can be done in place. writes nothing.

    every rejection carries a reason, because "it didn't work" on a mod
    install is the least useful thing a tool can say.
    """
    result = Plan()
    for item in replacements:
        entry = find(entries, item.game_path)
        if entry is None:
            result.rejected.append((item.game_path, "no entry with that path in the index"))
            continue
        if entry.is_stub:
            # uncompressed=0 with 8 bytes stored: a placeholder for an asset
            # the build left out. writing real data over it would need the
            # entry to grow, which is a repack.
            result.rejected.append((item.game_path, "this entry is an empty stub in the index"))
            continue

        blob = encode(item.payload, item.compress)
        if not entry.fits(len(blob)):
            result.rejected.append((
                item.game_path,
                f"needs {len(blob):,} bytes but the span holds "
                f"{entry.sector_span * SECTOR:,}; this one needs a full repack",
            ))
            continue
        result.replacements.append((entry, blob))
    return result


def inject(white_bin: Path, entries: list[ChunkEntry], replacements: list[Replacement],
           store: Path, on_progress=None, cancel=None, dry_run: bool = False):
    """
    performs the in-place replacement and returns (updated_entries, journal).

    journal is the SpanBackup journal path; hold onto it until the game has
    been confirmed working, then discard() it. restore() puts everything back.
    """
    white_bin = Path(white_bin)
    if not white_bin.is_file():
        raise InjectError(f"{white_bin} does not exist")

    resolved = plan(entries, replacements)
    if resolved.rejected:
        reasons = "; ".join(f"{p}: {r}" for p, r in resolved.rejected)
        raise DoesNotFit(reasons) if any("repack" in r for _, r in resolved.rejected) \
            else InjectError(reasons)
    if not resolved.replacements:
        raise InjectError("nothing to inject")

    if dry_run:
        return entries, None

    # step 2: record before touching anything
    sizes = {id(entry): len(item.payload)
             for (entry, _), item in zip(resolved.replacements, replacements)}
    backup = SpanBackup(
        white_bin, store,
        metadata={
            "entries": [
                {"path": e.path, "sector_offset": e.sector_offset,
                 "uncompressed": e.uncompressed, "stored": e.stored}
                for e, _ in resolved.replacements
            ]
        },
    )
    for entry, blob in resolved.replacements:
        # the whole span, not just the stored bytes. a shorter replacement
        # leaves the tail of the original span in place, and restoring only
        # the overwritten part would leave a hybrid of both files.
        span = entry.sector_span * SECTOR
        span = min(span, backup._journal.target_size - entry.byte_offset)
        backup.record(entry.byte_offset, span, label=entry.path)
    journal = backup.commit()

    # step 3: write payloads
    try:
        written = 0
        total = sum(len(b) for _, b in resolved.replacements)
        with white_bin.open("rb+") as handle:
            for entry, blob in resolved.replacements:
                if cancel is not None and cancel.is_set():
                    raise InjectError("cancelled")
                handle.seek(entry.byte_offset)
                handle.write(blob)
                # zero the rest of the span so no fragment of the old asset
                # survives past the new one's end
                padding = entry.sector_span * SECTOR - len(blob)
                remaining = min(padding, white_bin.stat().st_size - entry.byte_offset - len(blob))
                while remaining > 0:
                    block = min(CHUNK, remaining)
                    handle.write(b"\x00" * block)
                    remaining -= block
                written += len(blob)
                if on_progress:
                    from patcher.download import Progress
                    on_progress(Progress("inject", written, total, entry.path))
            handle.flush()
    except Exception:
        # put it back before the exception escapes: leaving a partially
        # written container is the one outcome that costs a reinstall
        restore(journal)
        raise

    updated = []
    replaced = {id(e): (b, sizes[id(e)]) for e, b in resolved.replacements}
    for entry in entries:
        hit = replaced.get(id(entry))
        if hit is None:
            updated.append(entry)
        else:
            blob, raw_size = hit
            updated.append(ChunkEntry(
                path=entry.path,
                sector_offset=entry.sector_offset,
                uncompressed=raw_size,
                stored=len(blob),
            ))
    return updated, journal


def verify(white_bin: Path, entries: list[ChunkEntry], paths: list[str]) -> dict:
    """
    reads the written entries back and confirms they inflate to what the index
    now claims. this is step 5, and it is not optional: a write that reports
    success without being read back is a guess.
    """
    results = {}
    with Path(white_bin).open("rb") as handle:
        for path in paths:
            entry = find(entries, path)
            if entry is None:
                results[path] = "missing from index"
                continue
            handle.seek(entry.byte_offset)
            blob = handle.read(entry.stored)
            if len(blob) != entry.stored:
                results[path] = "short read"
                continue
            if entry.is_compressed:
                try:
                    plain = zlib.decompress(blob)
                except zlib.error as e:
                    results[path] = f"inflate failed: {e}"
                    continue
                results[path] = ("ok" if len(plain) == entry.uncompressed
                                 else f"inflated to {len(plain)}, index says {entry.uncompressed}")
            else:
                results[path] = "ok" if len(blob) == entry.uncompressed else "size mismatch"
    return results
