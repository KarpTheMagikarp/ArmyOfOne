"""
Reading the index to find out what the extraction will do, before it does it.

This exists because of one live run: ff13tool stopped with code 1 after 3076 of
14381 files and said nothing useful. Both of the things that ordinarily causes
are answerable from filelist_scr* alone, which holds every path the tool will
write and the size of each, so none of it has to be guessed at from an exit
code.
"""

from pathlib import Path

import pytest

from patcher.archive import filelist as fl
from patcher.archive.entry import ChunkEntry, format_chunk
from patcher.mods.hd_preflight import (MAX_PATH, explain, headroom,
                                       survey_extraction)

REPO = Path(__file__).resolve().parent.parent


def index(tmp_path, paths, size=1024):
    """a real filelist, built and saved the way the game's own is"""
    entries = [ChunkEntry(path=p, sector_offset=i, uncompressed=size, stored=size)
               for i, p in enumerate(paths)]
    made = fl.Filelist(header=b"\x00" * 32, entry_records=b"", chunks=[format_chunk(entries)],
                       original_blobs=[None], edited={0}, entry_count=len(entries))
    target = tmp_path / "filelist_scru.win32.bin"
    fl.save(target, made)
    return target


def test_it_counts_what_the_extraction_will_actually_weigh(tmp_path):
    """
    prepare() estimated twice the packed archive plus a flat 20GB, which is the
    number the mod's own script quotes at people. The index knows.
    """
    book = index(tmp_path, ["chr/pc/pc001.trb", "zone/z110.xgr", "sound/bgm01.scd"],
                 size=3 * 1024 ** 2)

    found = survey_extraction(book, tmp_path / "mod")

    assert found.entries == 3
    assert found.bytes_needed == 9 * 1024 ** 2


def test_a_path_over_the_windows_limit_is_caught_before_the_tool_hits_it(tmp_path):
    # sized so the deep entry goes over and the shallow one does not, which is
    # what the real thing looks like: a few hundred of fourteen thousand fail
    # and the extraction dies partway through rather than refusing up front
    deep = Path("C:/" + "x" * 227)
    book = index(tmp_path, ["chr/pc/pc001/model/pc001_00_a.win32.trb", "a.bin"])

    found = survey_extraction(book, deep)

    assert not found.ok
    assert len(found.too_long) == 1, found.too_long
    assert found.too_long[0][0].endswith("pc001_00_a.win32.trb")
    assert found.longest > MAX_PATH


def test_the_same_archive_is_fine_somewhere_short(tmp_path):
    book = index(tmp_path, ["chr/pc/pc001/model/pc001_00_a.win32.trb"])

    found = survey_extraction(book, Path("C:/FF13HD"))

    assert found.ok
    assert explain(found, Path("C:/FF13HD")) == ""
    assert headroom(found) > 100, "a short root should leave plenty of room"


def test_the_explanation_says_how_much_shorter_rather_than_just_too_long(tmp_path):
    """
    "path too long" reads as a reason to delete files. The fix is to move the
    folder up a few levels and the message has to say so, with a number.
    """
    root = tmp_path / ("deep" * 60)
    book = index(tmp_path, ["chr/pc/pc001/model/pc001_00_a.win32.trb"])

    words = explain(survey_extraction(book, root), root)

    assert str(root) in words, "it does not show which folder is the problem"
    assert "characters or less" in words
    assert str(MAX_PATH) in words


def test_a_path_windows_will_not_take_is_reported_as_a_bad_download(tmp_path):
    """
    a colon or a pipe in an entry means the archive is damaged, not that the
    folder is too deep, and those want completely different advice
    """
    book = index(tmp_path, ["chr/pc/bad:name.trb"])

    found = survey_extraction(book, Path("C:/m"))

    assert not found.ok
    assert found.illegal == ["chr/pc/bad:name.trb"]
    assert "download it again" in explain(found, Path("C:/m"))


def test_the_limit_counts_the_mod_folder_too(tmp_path):
    """
    the entry path alone is nowhere near the limit. Where the user put the mod
    is the other half of it, and the half they can do something about.
    """
    short = survey_extraction(index(tmp_path, ["a/b/c.trb"]), Path("C:/m"))
    long = survey_extraction(index(tmp_path, ["a/b/c.trb"]), Path("C:/" + "x" * 150))

    assert long.longest - short.longest == 150 - 1


@pytest.mark.parametrize("extra", [0, 1])
def test_the_boundary_is_where_windows_puts_it(tmp_path, extra):
    """
    260 counts the terminating null, so 259 is the longest usable path. Off by
    one here means either refusing an install that would have worked or letting
    one fail three thousand files in.
    """
    root = Path("C:/m")
    name = "a" * (MAX_PATH - len(str(root)) - 1 + extra)

    found = survey_extraction(index(tmp_path, [name]), root)

    assert found.longest == MAX_PATH + extra
    assert bool(found.too_long) is bool(extra)


FF13_SAMPLE = REPO / "tests" / "data" / "filelist_scru_ff13_sample.bin"


def test_the_vanilla_table_matches_a_real_untouched_game():
    """
    The twelve sizes come from the mod's own Setup.bat, where CHECKFILESIZE
    uses them to spot a game whose characters have been swapped. This checks
    them against a real filelist_scru from an unmodded install.

    Two independent sources agreeing to the byte is what makes the table
    trustworthy, and it checks the index parser at the same time: a number
    wrong in either direction shows up here.
    """
    from patcher.archive import filelist as fl
    from patcher.mods.hd_preflight import VANILLA_PCS, already_modded

    if not FF13_SAMPLE.is_file():
        pytest.skip("no XIII sample filelist")

    sizes = {e.path.lower(): e.uncompressed
             for e in fl.parse(FF13_SAMPLE.read_bytes()).entries()}
    for path, expected in VANILLA_PCS.items():
        assert sizes.get(path.lower()) == expected, path

    assert already_modded(FF13_SAMPLE) == [], "a vanilla game read as modded"


def test_a_swapped_character_model_is_spotted(tmp_path):
    """
    the check that makes "is the backup I am about to take actually vanilla" a
    question with an answer rather than a guess about what a folder implies
    """
    from patcher.mods.hd_preflight import VANILLA_PCS, already_modded

    paths = list(VANILLA_PCS)
    entries = [ChunkEntry(path=p, sector_offset=i, uncompressed=VANILLA_PCS[p],
                          stored=VANILLA_PCS[p])
               for i, p in enumerate(paths)]
    entries[0] = ChunkEntry(path=paths[0], sector_offset=0,
                            uncompressed=VANILLA_PCS[paths[0]] + 4096, stored=100)
    made = fl.Filelist(header=b"\x00" * 32, entry_records=b"", chunks=[format_chunk(entries)],
                       original_blobs=[None], edited={0}, entry_count=len(entries))
    target = tmp_path / "filelist_scru.win32.bin"
    fl.save(target, made)

    changed = already_modded(target)

    assert len(changed) == 1
    assert changed[0][0] == paths[0]
    assert changed[0][2] == VANILLA_PCS[paths[0]] + 4096


def test_an_entry_the_archive_does_not_have_is_not_a_modded_one(tmp_path):
    """
    a missing model is a different and stranger problem than a swapped one,
    and calling it modded would refuse a backup for the wrong reason
    """
    from patcher.mods.hd_preflight import already_modded

    book = index(tmp_path, ["chr/pc/c999/bin/c999.win32.trb"])

    assert already_modded(book) == []
