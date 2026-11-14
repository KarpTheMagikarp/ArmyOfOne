"""
The shipped vanilla sizes, and the two rules that make a wrong one harmless:
bigger only, and the backup can't overrule them into calling vanilla modded.
"""

from pathlib import Path

import pytest

from patcher.archive.filelist import load
from patcher.mods import hd_vanilla
from patcher.mods.hd_layout import XIII, XIII2

REPO = Path(__file__).resolve().parent.parent
SAMPLES = {XIII: REPO / "tests" / "data" / "filelist_scru_ff13_sample.bin",
           XIII2: REPO / "tests" / "data" / "filelistu_sample.bin"}
TRB, IMGB = "chr/mon/m001/bin/m001.win32.trb", "chr/mon/m001/bin/m001.win32.imgb"


@pytest.fixture
def shipped(monkeypatch):
    """the real tables, which every other test swaps out for its own game's"""
    monkeypatch.undo()


def test_the_shipped_tables_hold_what_the_mods_own_setups_check(shipped):
    from patcher.mods.hd_preflight import VANILLA2_CHR, VANILLA_PCS

    for layout, checked in ((XIII, VANILLA_PCS), (XIII2, VANILLA2_CHR)):
        table = hd_vanilla.table(layout)
        assert table, f"manifests/vanilla/{layout.game}.txt didn't load"
        assert {p: table.get(p) for p in checked} == checked


def test_the_shipped_tables_read_their_own_clean_copies_as_untouched(shipped):
    for layout, sample in SAMPLES.items():
        sizes = hd_vanilla.index_sizes(sample)
        assert len(sizes) > 1000
        assert hd_vanilla.grown(sizes, layout) == frozenset()


def test_xiii2s_copy_was_never_through_ff13tools_repack():
    """
    why its sizes can be trusted for every group: every HD character install
    repacks with ff13tool -c, which stores this raw where Square Enix
    compressed it, and the real run before and after said so
    """
    version = next(e for e in load(SAMPLES[XIII2]).entries() if e.path == "sys/debug/version.txt")

    assert version.stored > version.uncompressed


def test_a_placeholder_isnt_a_size(shipped):
    stubs = [e.path for e in load(SAMPLES[XIII]).entries()
             if e.is_stub and hd_vanilla.parse_path(e.path)]
    assert stubs, "the sample has placeholders, or this proves nothing"

    assert not set(stubs) & set(hd_vanilla.table(XIII))


def test_bigger_counts_and_smaller_doesnt(monkeypatch):
    monkeypatch.setattr(hd_vanilla, "table", lambda layout: {TRB: 100, IMGB: 200})

    assert hd_vanilla.grown({TRB: 100, IMGB: 201}, XIII) == {TRB, IMGB}, \
        "the .trb and .imgb of a model go together"
    assert hd_vanilla.grown({TRB: 90, IMGB: 150}, XIII) == frozenset(), \
        "smaller is a table that's wrong about it, not a mod"


def test_a_backup_taken_over_a_hand_install_cant_make_a_verified_game_read_modded(monkeypatch):
    monkeypatch.setattr(hd_vanilla, "table", lambda layout: {TRB: 100, IMGB: 200})
    hand = {TRB: 140, IMGB: 900}
    verified = {TRB: 100, IMGB: 200}

    assert hd_vanilla.moved(verified, hand, XIII) == frozenset()
    assert hd_vanilla.moved(hand, hand, XIII) == {TRB, IMGB}, "and the hand install still shows"


def test_the_backup_still_catches_what_the_table_cant_size(monkeypatch):
    monkeypatch.setattr(hd_vanilla, "table", lambda layout: {})
    clean = {TRB: 100, IMGB: 200}

    assert hd_vanilla.moved({TRB: 100, IMGB: 199}, clean, XIII) == {TRB, IMGB}
    assert hd_vanilla.moved(clean, None, XIII) == frozenset()


def test_stuck_is_a_modded_model_with_nothing_clean_to_build_from(tmp_path, monkeypatch):
    monkeypatch.setattr(hd_vanilla, "table", lambda layout: {TRB: 100, IMGB: 200})
    cache = tmp_path / "models" / "orig"
    hd = {TRB: 140, IMGB: 900}

    assert hd_vanilla.stuck([TRB, IMGB], hd, None, cache, XIII) == {TRB.rsplit(".win32.", 1)[0]}
    # the seed takes the backup's copy where the game's outgrew it
    assert hd_vanilla.stuck([TRB, IMGB], hd, {TRB: 100, IMGB: 200}, cache, XIII) == set()
    # and a clean cached copy wins over both
    for path, size in ((TRB, 100), (IMGB, 200)):
        (cache / path).parent.mkdir(parents=True, exist_ok=True)
        (cache / path).write_bytes(b"V" * size)
    assert hd_vanilla.stuck([TRB, IMGB], hd, None, cache, XIII) == set()


def test_a_cached_copy_bigger_than_vanilla_isnt_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(hd_vanilla, "table", lambda layout: {TRB: 100, IMGB: 200})
    cache = tmp_path / "models" / "orig"
    (cache / TRB).parent.mkdir(parents=True)
    (cache / TRB).write_bytes(b"V" * 140)

    # the game's own copy is clean, so the seed caches that again
    assert hd_vanilla.stuck([TRB, IMGB], {TRB: 100, IMGB: 200}, None, cache, XIII) == set()
    assert hd_vanilla.stuck([TRB, IMGB], {TRB: 140, IMGB: 200}, None, cache, XIII) != set()
