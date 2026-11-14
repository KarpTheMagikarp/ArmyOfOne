import threading
import zlib
from pathlib import Path

import pytest

from patcher.archive.entry import SECTOR, ChunkEntry, chain_is_consistent, find, parse_chunk
from patcher.archive.inject import (
    DoesNotFit,
    InjectError,
    Replacement,
    encode,
    inject,
    plan,
    verify,
)
from patcher.archive.spanbackup import restore


def build_archive(tmp_path, payloads):
    """a white_img plus matching index, laid out exactly like the real one"""
    blob = bytearray()
    entries = []
    sector = 0
    for path, data, compress in payloads:
        stored = encode(data, compress)
        blob.extend(b"\x00" * (sector * SECTOR - len(blob)))
        blob.extend(stored)
        entries.append(ChunkEntry(path=path, sector_offset=sector,
                                  uncompressed=len(data), stored=len(stored)))
        sector += -(-len(stored) // SECTOR)
    blob.extend(b"\x00" * (sector * SECTOR - len(blob)))
    white = tmp_path / "white_imgu.win32.bin"
    white.write_bytes(bytes(blob))
    return white, entries


SAMPLE = [
    ("vfx/field/weather/weather07/veffs.jp.win32.imgb", b"rain07" * 3000, True),
    ("vfx/field/weather/weather08/veffs.jp.win32.imgb", b"rain08" * 3000, True),
    ("chr/pc/c001/bin/c001.win32.trb", b"model" * 8000, True),
]


def test_the_fixture_matches_the_real_layout(tmp_path):
    _, entries = build_archive(tmp_path, SAMPLE)
    assert chain_is_consistent(entries)


def test_a_replacement_that_fits_goes_in_without_moving_anything(tmp_path):
    white, entries = build_archive(tmp_path, SAMPLE)
    before = white.stat().st_size
    new_rain = b"NEWRAIN" * 2000

    updated, journal = inject(
        white, entries,
        [Replacement("vfx/field/weather/weather07/veffs.jp.win32.imgb", new_rain)],
        store=tmp_path / "spans")

    assert white.stat().st_size == before, "an in-place write must not resize the container"
    # offsets untouched, sizes updated
    original = find(entries, "vfx/field/weather/weather07/veffs.jp.win32.imgb")
    changed = find(updated, "vfx/field/weather/weather07/veffs.jp.win32.imgb")
    assert changed.sector_offset == original.sector_offset
    assert changed.uncompressed == len(new_rain)
    assert chain_is_consistent(updated)

    assert verify(white, updated, [changed.path])[changed.path] == "ok"


def test_neighbouring_entries_are_untouched(tmp_path):
    """the failure that would be invisible until the game loaded something else"""
    white, entries = build_archive(tmp_path, SAMPLE)
    neighbour = find(entries, "vfx/field/weather/weather08/veffs.jp.win32.imgb")
    with white.open("rb") as h:
        h.seek(neighbour.byte_offset)
        before = h.read(neighbour.stored)

    inject(white, entries,
           [Replacement("vfx/field/weather/weather07/veffs.jp.win32.imgb", b"x" * 500)],
           store=tmp_path / "spans")

    with white.open("rb") as h:
        h.seek(neighbour.byte_offset)
        assert h.read(neighbour.stored) == before


def test_a_shorter_replacement_leaves_no_fragment_of_the_old_asset(tmp_path):
    """
    restoring only the overwritten bytes would leave a hybrid file, so the
    whole span is zeroed and the whole span is journalled.
    """
    white, entries = build_archive(tmp_path, SAMPLE)
    entry = find(entries, "chr/pc/c001/bin/c001.win32.trb")

    inject(white, entries, [Replacement(entry.path, b"tiny")], store=tmp_path / "spans")

    with white.open("rb") as h:
        h.seek(entry.byte_offset)
        span = h.read(entry.sector_span * SECTOR)
    assert b"model" not in span


def test_a_replacement_too_big_for_its_span_is_refused(tmp_path):
    white, entries = build_archive(tmp_path, SAMPLE)
    entry = find(entries, "vfx/field/weather/weather07/veffs.jp.win32.imgb")
    huge = bytes(range(256)) * 4000          # incompressible and far too large
    before = white.read_bytes()

    with pytest.raises(DoesNotFit, match="full repack"):
        inject(white, entries, [Replacement(entry.path, huge)], store=tmp_path / "spans")

    assert white.read_bytes() == before, "a refusal must not have written anything"


def test_a_replacement_may_use_the_sector_padding(tmp_path):
    """the slack up to the next boundary is real, usable room"""
    white, entries = build_archive(tmp_path, [("a/b.imgb", b"x" * 100, False)])
    entry = entries[0]
    assert entry.stored == 100
    assert entry.fits(SECTOR)

    updated, _ = inject(white, entries,
                        [Replacement("a/b.imgb", b"y" * (SECTOR - 8), compress=False)],
                        store=tmp_path / "spans")
    assert verify(white, updated, ["a/b.imgb"])["a/b.imgb"] == "ok"


def test_an_unknown_path_is_rejected_with_a_reason(tmp_path):
    white, entries = build_archive(tmp_path, SAMPLE)
    with pytest.raises(InjectError, match="no entry with that path"):
        inject(white, entries, [Replacement("not/real.imgb", b"x")], store=tmp_path / "spans")


def test_a_stub_entry_is_refused(tmp_path):
    """uncompressed=0 placeholders cannot hold a real asset"""
    entries = [ChunkEntry(path="chr/pc/c110/bin/c110.win32.imgb",
                          sector_offset=0, uncompressed=0, stored=8)]
    white = tmp_path / "w.bin"
    white.write_bytes(b"\x00" * SECTOR)

    result = plan(entries, [Replacement(entries[0].path, b"data")])
    assert not result.ok
    assert "empty stub" in result.rejected[0][1]


def test_the_journal_restores_the_container_exactly(tmp_path):
    white, entries = build_archive(tmp_path, SAMPLE)
    original = white.read_bytes()

    _, journal = inject(
        white, entries,
        [Replacement("vfx/field/weather/weather07/veffs.jp.win32.imgb", b"NEW" * 900),
         Replacement("chr/pc/c001/bin/c001.win32.trb", b"ALSONEW" * 400)],
        store=tmp_path / "spans")
    assert white.read_bytes() != original

    restore(journal)
    assert white.read_bytes() == original


def test_the_backup_stays_tiny_next_to_the_container(tmp_path):
    white, entries = build_archive(tmp_path, SAMPLE)
    _, journal = inject(
        white, entries,
        [Replacement("vfx/field/weather/weather07/veffs.jp.win32.imgb", b"x" * 100)],
        store=tmp_path / "spans")

    span_bytes = sum(p.stat().st_size for p in (tmp_path / "spans").iterdir())
    assert span_bytes < white.stat().st_size / 2


def test_a_failure_mid_write_rolls_back(tmp_path):
    """a partially written container is the one outcome that costs a reinstall"""
    white, entries = build_archive(tmp_path, SAMPLE)
    original = white.read_bytes()
    cancel = threading.Event()

    calls = []

    def on_progress(progress):
        calls.append(progress.label)
        cancel.set()          # abort after the first payload lands

    with pytest.raises(InjectError):
        inject(white, entries,
               [Replacement("vfx/field/weather/weather07/veffs.jp.win32.imgb", b"a" * 900),
                Replacement("vfx/field/weather/weather08/veffs.jp.win32.imgb", b"b" * 900)],
               store=tmp_path / "spans", on_progress=on_progress, cancel=cancel)

    assert white.read_bytes() == original, "rolled back to exactly the original"


def test_dry_run_touches_nothing(tmp_path):
    white, entries = build_archive(tmp_path, SAMPLE)
    original = white.read_bytes()

    updated, journal = inject(
        white, entries,
        [Replacement("vfx/field/weather/weather07/veffs.jp.win32.imgb", b"x" * 100)],
        store=tmp_path / "spans", dry_run=True)

    assert journal is None
    assert white.read_bytes() == original
    assert not (tmp_path / "spans").exists()


def test_plan_reports_everything_before_anything_is_written(tmp_path):
    _, entries = build_archive(tmp_path, SAMPLE)
    result = plan(entries, [
        Replacement("vfx/field/weather/weather07/veffs.jp.win32.imgb", b"ok" * 100),
        Replacement("nope/missing.imgb", b"x"),
    ])
    payload = result.as_dict()
    assert len(payload["willWrite"]) == 1
    assert len(payload["rejected"]) == 1
    assert payload["willWrite"][0]["spanBytes"] % SECTOR == 0
