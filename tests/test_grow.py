"""
Growing an entry, which is what an HD texture pack needs and what inject.py
refuses to do.

The interesting tests here run against tests/data/filelistu_sample.bin, a real
14381 entry XIII-2 index, rather than a synthetic one. The lookup table is the
whole difficulty and a hand-built fixture would have whatever structure made
the test pass.
"""

import struct
from pathlib import Path

import pytest

from patcher.archive import filelist as F
from patcher.archive import grow
from patcher.archive.entry import SECTOR
from patcher.archive.inject import Replacement

SAMPLE = Path(__file__).resolve().parent / "data" / "filelistu_sample.bin"
pytestmark = pytest.mark.skipif(not SAMPLE.is_file(), reason="no sample index")


def sample():
    return F.parse(SAMPLE.read_bytes())


def positions_by_chunk(index):
    out = {}
    for chunk, offset in F.lookup_positions(index.entry_records):
        out.setdefault(chunk, []).append(offset)
    return out


def record_offsets(plain: bytes) -> list[int]:
    """where each record's text actually starts in a chunk"""
    out, offset = [], 0
    for part in plain.split(b"\x00")[:-1]:
        if part == b"end":
            break
        out.append(offset)
        offset += len(part) + 1
    return out


def test_every_lookup_entry_points_at_a_real_record():
    """
    the claim the whole module rests on. 14381 values in a 24 bit space, each
    landing exactly on a record boundary, one per record with none left over.
    """
    index = sample()
    actual = {chunk: set(record_offsets(plain)) for chunk, plain in enumerate(index.chunks)}

    seen = set()
    for chunk, offset in F.lookup_positions(index.entry_records):
        assert chunk < len(index.chunks), f"lookup points at chunk {chunk}, there are {len(index.chunks)}"
        assert offset in actual[chunk], f"chunk {chunk} offset {offset} is not a record start"
        seen.add((chunk, offset))

    total = sum(len(v) for v in actual.values())
    assert len(seen) == total == index.entry_count, "not a bijection onto the real records"


def test_an_unedited_index_still_rebuilds_byte_identically():
    """the licence to write, and the new code must not have cost us it"""
    F.verify_roundtrip(SAMPLE.read_bytes())


def test_relocate_shifts_only_what_comes_after_it_in_that_chunk():
    index = sample()
    target = index.entries()[0]
    chunk_index, _ = index.locate(target.path)
    before = positions_by_chunk(index)

    # a much bigger payload, much further into the file: both fields grow
    F.relocate_entry(index, target.path, sector_offset=0xABCDEF,
                     uncompressed=9_000_000, stored=8_000_000)

    after = positions_by_chunk(index)
    for chunk in before:
        if chunk != chunk_index:
            assert before[chunk] == after[chunk], f"chunk {chunk} moved and should not have"

    # and the edited chunk's lookup still describes its own records exactly
    assert sorted(after[chunk_index]) == record_offsets(index.chunks[chunk_index])


def test_a_grown_index_survives_a_rebuild_and_reparse():
    index = sample()
    target = index.entries()[0]

    F.relocate_entry(index, target.path, sector_offset=0xABCDEF,
                     uncompressed=9_000_000, stored=8_000_000)
    reparsed = F.parse(F.rebuild(index))

    found = next(e for e in reparsed.entries() if e.path == target.path)
    assert (found.sector_offset, found.uncompressed, found.stored) == (0xABCDEF, 9_000_000, 8_000_000)

    # every other entry came through untouched. compared as ordered lists,
    # because 274 paths in this index appear more than once and a dict keyed on
    # path silently collapses them into whichever came last.
    before = [e.raw_fields for e in sample().entries() if e.path != target.path]
    after = [e.raw_fields for e in reparsed.entries() if e.path != target.path]
    assert after == before


def test_all_copies_of_a_duplicated_path_move_together():
    """
    274 paths in this index appear more than once, several of them three times:
    same sizes, different offsets. Repointing only the first would leave the
    others describing vanilla data, and the game would load the HD texture or
    the original depending on which copy it reached.
    """
    index = sample()
    from collections import Counter
    counts = Counter(e.path for e in index.entries())
    path, copies = next((p, c) for p, c in counts.items() if c > 2)

    F.relocate_entry(index, path, sector_offset=0xABCDEF,
                     uncompressed=9_000_000, stored=8_000_000)
    reparsed = F.parse(F.rebuild(index))

    moved = [e for e in reparsed.entries() if e.path == path]
    assert len(moved) == copies, "an occurrence went missing"
    assert all(e.sector_offset == 0xABCDEF for e in moved), \
        f"only {sum(e.sector_offset == 0xABCDEF for e in moved)} of {copies} copies moved"


def test_the_lookup_still_finds_every_record_after_a_grow():
    """
    the failure this guards is silent: the index parses, the game starts, and
    one asset loads whatever text happens to sit at a stale offset
    """
    index = sample()
    F.relocate_entry(index, index.entries()[0].path, sector_offset=0xABCDEF,
                     uncompressed=9_000_000, stored=8_000_000)
    reparsed = F.parse(F.rebuild(index))

    actual = {c: set(record_offsets(p)) for c, p in enumerate(reparsed.chunks)}
    for chunk, offset in F.lookup_positions(reparsed.entry_records):
        assert offset in actual[chunk], f"stale lookup: chunk {chunk} offset {offset}"


def test_a_chunk_cannot_be_grown_past_its_slice():
    """
    24KB of headroom on the sample, so the guard needs a chunk built to sit
    right under the limit. It still has to refuse rather than write a lookup
    table pointing into the next chunk's space.
    """
    index = sample()
    chunk = 0
    records, _ = F._split_chunk(index.chunks[chunk])
    old = F._offsets(records)
    # every record still in place, except the last one lands past the slice
    new = list(old)
    new[-1] = F.CHUNK_STRIDE + 8

    with pytest.raises(F.FilelistError, match="collide|slice"):
        F._remap_lookup(index, chunk, old, new)


def test_a_lookup_pointing_at_nothing_is_refused_rather_than_guessed():
    """
    a table that disagrees with the chunk it indexes means one of the two is
    not what we think it is, and repointing it anyway would write a plausible
    index over a real one
    """
    index = sample()
    records, _ = F._split_chunk(index.chunks[0])
    old = F._offsets(records)
    shifted = [o + 1 for o in old]        # nothing lines up any more

    with pytest.raises(F.FilelistError, match="not the start of any record"):
        F._remap_lookup(index, 0, shifted, [o + 4 for o in shifted])


def build_archive(tmp_path):
    """a white_img holding the payload of every entry the sample describes, cheaply"""
    index = sample()
    entries = index.entries()[:3]
    white = tmp_path / "white_imgu.win32.bin"
    size = max(e.byte_offset + e.stored for e in entries)
    with white.open("wb") as handle:
        handle.truncate(-(-size // SECTOR) * SECTOR)
    listing = tmp_path / "filelistu.win32.bin"
    listing.write_bytes(SAMPLE.read_bytes())
    return white, listing, entries


def test_grow_appends_and_repoints(tmp_path):
    white, listing, entries = build_archive(tmp_path)
    original_size = white.stat().st_size
    target = entries[0]
    payload = b"HD" * 4_000_000        # far bigger than anything in the sample

    undo = grow.grow(white, listing, [Replacement(game_path=target.path, payload=payload)],
                     store=tmp_path / "undo")

    assert undo is not None
    moved = next(e for e in F.load(listing).entries() if e.path == target.path)
    assert moved.sector_offset * SECTOR >= original_size, "payload did not land past the old end"
    assert moved.uncompressed == len(payload)
    assert white.stat().st_size > original_size

    # and the bytes really are there
    with white.open("rb") as handle:
        handle.seek(moved.byte_offset)
        stored = handle.read(moved.stored)
    import zlib
    assert zlib.decompress(stored) == payload


def test_rolling_back_a_grow_restores_both_files_exactly(tmp_path):
    white, listing, entries = build_archive(tmp_path)
    before_white = white.read_bytes()
    before_index = listing.read_bytes()

    undo = grow.grow(white, listing,
                     [Replacement(game_path=entries[0].path, payload=b"X" * 3_000_000)],
                     store=tmp_path / "undo")
    undo.roll_back()

    assert white.read_bytes() == before_white, "white_img did not come back byte for byte"
    assert listing.read_bytes() == before_index, "the index did not come back byte for byte"


def test_a_failure_midway_leaves_nothing_behind(tmp_path, monkeypatch):
    """
    appending never overwrites, so a crash after the payload is written still
    has to leave the archive exactly as it was
    """
    white, listing, entries = build_archive(tmp_path)
    before_white = white.read_bytes()
    before_index = listing.read_bytes()

    def explode(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(grow.filelist_mod, "save", explode)

    with pytest.raises(RuntimeError):
        grow.grow(white, listing,
                  [Replacement(game_path=entries[0].path, payload=b"Y" * 2_000_000)],
                  store=tmp_path / "undo")

    assert white.read_bytes() == before_white
    assert listing.read_bytes() == before_index


def test_an_unknown_path_is_refused_before_anything_is_written(tmp_path):
    white, listing, _ = build_archive(tmp_path)
    before = white.read_bytes()

    with pytest.raises(grow.GrowError, match="no entry with that path"):
        grow.grow(white, listing, [Replacement(game_path="chr/nope/nope.imgb", payload=b"x")],
                  store=tmp_path / "undo")

    assert white.read_bytes() == before


def test_everything_lands_on_a_sector_boundary(tmp_path):
    """the index addresses payloads in sectors and cannot describe anything else"""
    white, listing, entries = build_archive(tmp_path)
    targets = [Replacement(game_path=e.path, payload=bytes([i]) * (1_000_000 + i))
               for i, e in enumerate(entries)]

    grow.grow(white, listing, targets, store=tmp_path / "undo")

    reread = {e.path: e for e in F.load(listing).entries()}
    for item in targets:
        entry = reread[item.game_path]
        assert entry.byte_offset % SECTOR == 0, f"{item.game_path} is not sector aligned"


def test_a_whole_voice_sets_worth_of_moves_goes_in_one_pass():
    """
    an HD copy into the other voice set is thousands of moves. One pass over
    the chunks for all of them comes out the same as one relocate_entry each,
    keeps every lookup record on a record, and leaves the rest alone
    """
    import time
    from collections import Counter

    index = sample()
    entries = index.entries()
    counts = Counter(e.path for e in entries)
    chosen = sorted({e.path for e in entries if e.path.startswith("chr/")})
    assert len(chosen) > 8000
    # sizes past a hex digit boundary in every field, the way HD art lands
    moves = {path: (0x100000 + n * 3, 0x1000000 + n, 0x800000 + n)
             for n, path in enumerate(chosen)}

    began = time.monotonic()
    F.relocate_many(index, moves)
    took = time.monotonic() - began
    again = F.parse(F.rebuild(index))

    assert F.records_problem(again) == ""
    assert took < 5, f"{took:.1f}s for {len(moves)} moves"
    now = Counter((e.path, e.raw_fields) for e in again.entries())
    for path, spot in moves.items():
        assert now[(path, spot)] == counts[path], f"{path} didn't move every copy"
    rest = [e.raw_fields for e in entries if not e.path.startswith("chr/")]
    assert [e.raw_fields for e in again.entries() if not e.path.startswith("chr/")] == rest

    one_by_one = sample()
    for path, spot in list(moves.items())[:40]:
        F.relocate_entry(one_by_one, path, *spot)
    batch = sample()
    F.relocate_many(batch, dict(list(moves.items())[:40]))
    assert F.rebuild(batch) == F.rebuild(one_by_one)


def test_a_batch_with_a_path_the_index_lacks_changes_nothing():
    index = sample()
    chunks, records = list(index.chunks), index.entry_records
    first = index.entries()[0].path

    with pytest.raises(F.FilelistError, match="no entry for"):
        F.relocate_many(index, {first: (0xABCDEF, 9, 9), "chr/nope/nope.imgb": (1, 1, 1)})

    assert index.chunks == chunks and index.entry_records == records and not index.edited
