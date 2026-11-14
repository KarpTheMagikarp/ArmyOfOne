"""
What is in the game, asked of the game.

There is a state file recording what was installed and it is right until it is
not: Steam verifying the files, a manual restore, the file being lost, a game
copied from another machine, an install done with the mod's own batch before
this app existed. Every one of those leaves the record and the archive
disagreeing, and a panel that badges a category APPLIED on the record's word
is lying in exactly those cases.
"""

from pathlib import Path

import pytest

from patcher.archive import filelist as fl
from patcher.archive.entry import ChunkEntry, format_chunk
from patcher.mods.hd_state import (applied_categories, changed_paths,
                                   looks_modded)

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "tests" / "data" / "filelist_scru_ff13_sample.bin"
DATA = REPO / "tests" / "data" / "hd"


def vanilla():
    if not SAMPLE.is_file():
        pytest.skip("no XIII sample filelist")
    return SAMPLE


def build_plain(tmp_path, entries, name="filelist_scru.win32.bin"):
    """a PLAIN_0 index, which is the shape XIII actually ships"""
    import struct
    import zlib

    plain = format_chunk(entries)
    blob = zlib.compress(plain, 9)
    records = b"\x00" * (8 * len(entries))
    table_at = 12 + len(records)
    chunk_at = table_at + 12
    body = bytearray(struct.pack("<III", table_at, chunk_at, len(entries)))
    body += records
    body += struct.pack("<III", len(plain), len(blob), 0)
    body += blob
    target = tmp_path / name
    target.write_bytes(bytes(body))
    return target


def sample_entries():
    return fl.parse(vanilla().read_bytes()).entries()


def grown(entries, paths, by=4096):
    wanted = {p.lower() for p in paths}
    return [ChunkEntry(path=e.path, sector_offset=e.sector_offset,
                       uncompressed=e.uncompressed + (by if e.path.lower() in wanted else 0),
                       stored=e.stored)
            for e in entries]


def test_an_untouched_game_shows_nothing_changed(tmp_path):
    same = build_plain(tmp_path, sample_entries())

    assert changed_paths(same, vanilla()) == set()
    assert not looks_modded(same, vanilla())


def test_a_replaced_model_is_spotted(tmp_path):
    live = build_plain(tmp_path, grown(sample_entries(), ["chr/weapon/w001/bin/w001.win32.imgb"]))

    changed = changed_paths(live, vanilla())

    assert changed == {"chr/weapon/w001/bin/w001.win32.imgb"}
    assert looks_modded(live, vanilla())


def test_installing_one_category_lights_up_only_that_one(tmp_path):
    """
    the real data. Grow every eidolon the mod's own list names and nothing
    else, then ask which categories are in the game.
    """
    from patcher.mods.hd_recipe import read_category
    from patcher.mods.hd_runner import GROUPS

    members = read_category(DATA / "LoadHDEidolons.txt").members
    paths = [f"chr/{GROUPS[k.upper()]}/{m}/bin/{m}{s}"
             for k, m in members for s in (".win32.trb", ".win32.imgb")]
    live = build_plain(tmp_path, grown(sample_entries(), paths))

    found = applied_categories(live, vanilla(), DATA, [
        {"id": "eidolons", "list": "LoadHDEidolons.txt"},
        {"id": "pcs", "list": "LoadHDPCs.tx1"},
    ])

    assert found.verified
    assert found.categories == {"eidolons"}, found.categories


def test_a_handful_of_shared_ids_does_not_count_as_installed(tmp_path):
    """
    the lists overlap: an id sits in Weapons and in Miscellaneous. Any single
    hit counting would light up several categories for one install, so it
    takes most of them.
    """
    from patcher.mods.hd_recipe import read_category
    from patcher.mods.hd_runner import GROUPS

    members = read_category(DATA / "LoadHDEidolons.txt").members[:2]
    paths = [f"chr/{GROUPS[k.upper()]}/{m}/bin/{m}.win32.trb" for k, m in members]
    live = build_plain(tmp_path, grown(sample_entries(), paths))

    found = applied_categories(live, vanilla(), DATA,
                               [{"id": "eidolons", "list": "LoadHDEidolons.txt"}])

    assert found.categories == set(), "two ids out of dozens read as a full install"
    assert found.verified, "it could tell, it just said no"


def test_no_reference_is_not_the_same_as_nothing_installed(tmp_path):
    """
    the distinction a caller must not collapse. A missing backup means we
    cannot tell, and badging every category as clean on that basis is how a
    modded game gets installed over.
    """
    found = applied_categories(vanilla(), tmp_path / "not-there.bin", DATA,
                               [{"id": "eidolons", "list": "LoadHDEidolons.txt"}])

    assert not found.verified
    assert found.categories == set()
    assert not looks_modded(vanilla(), tmp_path / "not-there.bin")


def test_a_category_whose_list_is_missing_is_skipped_not_guessed(tmp_path):
    live = build_plain(tmp_path, grown(sample_entries(), ["chr/weapon/w001/bin/w001.win32.trb"]))

    found = applied_categories(live, vanilla(), DATA,
                               [{"id": "weapons", "list": "LoadHDWeapons.txt"}])

    assert found.verified
    assert found.categories == set()


def test_a_category_installed_by_a_corrected_build_is_flagged():
    """
    the half that needs no evidence. Detection can only find damage that left a
    signature on disk; a stamp says outright that the code which wrote this has
    been fixed since, which covers the corrections that leave nothing to find.
    """
    from patcher.mods.hd_state import PIPELINE, stale_pipelines

    assert PIPELINE["zones"] > 1, "the .imgb fix never bumped anything"

    # an install from before stamping existed has no number at all, and that is
    # exactly the population the first bump is aimed at
    stale = stale_pipelines({"installed": ["zones", "npcs"]}, ["zones", "npcs"])
    assert set(stale) == {"zones"}, stale
    assert "imgb" in stale["zones"], "it flags without saying what was wrong"

    # one written by the current build is not
    now = {"pipeline": {"zones": PIPELINE["zones"]}}
    assert stale_pipelines(now, ["zones"]) == {}

    # and only about what is installed
    assert stale_pipelines({}, []) == {}
    assert stale_pipelines({"pipeline": {"zones": 1}}, ["npcs"]) == {}


def test_a_junk_stamp_reads_as_the_oldest_rather_than_crashing():
    """
    the state file is on disk and editable, and a survey that raises takes the
    whole panel's badges with it
    """
    from patcher.mods.hd_state import stale_pipelines

    for junk in ("banana", None, {}, []):
        assert set(stale_pipelines({"pipeline": {"zones": junk}}, ["zones"])) == {"zones"}


def test_a_category_mostly_without_art_can_still_read_as_installed(tmp_path):
    """
    the live one, and it could never have worked. Eidolons lists twenty two
    ids and the mod ships art for ten, so a perfect install moves twenty of
    forty four paths against a bar of twenty three. It read as not installed
    on every machine, forever, which is why somebody kept reinstalling it.

    The denominator was the bug: an id with no HD art cannot change, so
    counting it asks a category to clear a bar that gets further away the more
    ids the mod skips.
    """
    from patcher.mods.hd_state import _model_paths, shipped_art

    data = tmp_path / "Data"
    data.mkdir()
    (data / "LoadHDEidolons.txt").write_text(
        "#:Eidolons\n" + "".join(f"s s{n}\n" for n in
                                 ("101", "102", "150", "201", "203", "250")),
        encoding="utf-8")
    # the ten-of-twenty-two shape, in miniature: art for two of six
    art_dir = tmp_path / "textures" / "summon"
    art_dir.mkdir(parents=True)
    for shipped in ("s101A_01.bin", "s102C_01.bin"):
        (art_dir / shipped).write_bytes(b"x")

    category = {"id": "eidolons", "list": "LoadHDEidolons.txt"}
    art = shipped_art(tmp_path, data)
    assert art["summon"] == {"s101", "s102"}

    listed = _model_paths(data, category)
    real = _model_paths(data, category, art)
    assert len(listed) == 12, "six ids, two files each"
    assert len(real) == 4, "only what the mod can actually change"

    # the whole point: a complete install clears the bar now and could not before
    installed = set(real)
    assert not (len(installed) * 2 > len(listed)), "this used to be unreachable"
    assert len(installed & real) * 2 > len(real)


def test_a_mod_with_no_textures_folder_falls_back_rather_than_going_blank():
    """
    every id would be filtered out by an empty answer, so a category with a
    perfectly good install would read as not installed. Unknown has to mean
    "count everything" and not "count nothing".
    """
    from pathlib import Path

    from patcher.mods.hd_state import shipped_art

    assert shipped_art(Path("/nope/not/here"), Path("/nope/not/here")) == {}
