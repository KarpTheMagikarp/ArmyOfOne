"""
An index and the archive it describes only ever go into the backup, and back
into the game, as a pair taken together.

A live XIII-2 had filelistu renamed out of its HD backup so it could be sent
somewhere. The next install took the game's filelistu, which an earlier run
had repacked, and kept white_imgu from before that run. Revert put the two
back and the game crashed right after the Square Enix logo: none of the
index's entries sat where it said they did, and 783 pointed past the end.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from patcher.mods import hd2_install, hd_install
from patcher.mods.hd2_recipe import detect
from patcher.mods.hd_install import InstallError, pair_key
from patcher.mods.hd_zones import pair_problem
from patcher.state import load_state
from tests.conftest import Log
from tests.hd2_world import (categories, entry_bytes, filler, make_game, make_mod, pin_vanilla,
                             write_pair)
from tests.test_hd2_injections import (LT_ID, WEATHER, game_files, hd, lt, revert, voice,
                                       weather, world)  # noqa: F401


def install(game, root, selected):
    return hd2_install.install(game, detect(root), categories(), set(selected),
                               game / ".patcher-undo", on_line=Log())


def backup_of(game):
    return game / ".patcher-undo" / "hdproject"


def records(folder):
    return json.loads((folder / hd_install.MANIFEST).read_text(encoding="utf-8"))["files"]


def as_an_older_build_left_it(game, names=("filelistu.win32.bin",)):
    """
    the live backup: the game's copy of `names` beside the rest of the backup
    from before, each recorded on its own and nothing naming a partner
    """
    folder = backup_of(game)
    data = json.loads((folder / hd_install.MANIFEST).read_text(encoding="utf-8"))
    for record in data["files"].values():
        record.pop("partner", None)
    for name in names:
        saved = folder / name
        shutil.copyfile(game / "alba_data" / "sys" / name, saved)
        data["files"][name].update(size=saved.stat().st_size,
                                   sha256=hashlib.sha256(saved.read_bytes()).hexdigest())
    (folder / hd_install.MANIFEST).write_text(json.dumps(data), encoding="utf-8")


def archives_in(folder):
    return sorted(p.name for p in folder.iterdir() if pair_key(p.name)) if folder.exists() else []


def leftovers(game):
    return [p.name for p in game.rglob("*") if p.name.endswith((".part", ".prev"))]


@pytest.fixture
def zoned(tmp_path, monkeypatch):
    """XIII-2 with z0010 in it, for the white_img and blob pair"""
    game = make_game(tmp_path)
    root = make_mod(tmp_path, monkeypatch)
    pin_vanilla(monkeypatch, game)
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    return game, root


def everything_in(game):
    return {p: p.read_bytes() for p in (game / "alba_data").rglob("*.win32.bin")}


@pytest.mark.parametrize("sealed", [True, False])
def test_an_index_and_an_archive_from_either_side_of_a_repack_are_told_apart(tmp_path, sealed):
    rows = [(f"chr/npc/n{i:03d}/bin/n{i:03d}.win32.trb", bytes([i]) * 5000) for i in range(30)]
    # HD art doesn't deflate to nothing, so everything after it moves down
    art = b"".join(hashlib.sha256(bytes([n % 256, n // 256])).digest() for n in range(640))
    grown = [(path, data + art if i == 3 else data) for i, (path, data) in enumerate(rows)]
    pairs = []
    for name, content in (("before", rows), ("after", grown)):
        blob = tmp_path / name / "white_imgu.win32.bin"
        index = tmp_path / name / "filelistu.win32.bin"
        write_pair(blob, index, content, sealed=sealed)
        pairs.append((index, blob))
    (before_index, before_blob), (after_index, after_blob) = pairs

    assert pair_problem(before_index, before_blob) == ""
    assert pair_problem(after_index, after_blob) == ""
    assert "past the end" in pair_problem(after_index, before_blob), "the live crash"
    assert "is not where" in pair_problem(before_index, after_blob)


def test_a_backup_missing_its_index_is_not_finished_from_a_game_rewritten_since(world):
    game, root, state = world(variants=("u", "c"))
    vanilla = game_files(game, "uc")
    lt(game, state)
    hd(game, root, {"weapons"})
    folder = backup_of(game)
    (folder / "filelistu.win32.bin").rename(folder / "filelistu.win32patcher-undo.bin")
    before = game_files(game, "uc")

    with pytest.raises(InstallError, match="filelistu.win32.bin was in the backup.*isn't any "
                                           "more.*put it back"):
        install(game, root, {"enemies"})

    assert game_files(game, "uc") == before
    assert not (folder / "filelistu.win32.bin").exists(), "the game's index went in as the backup"

    # put back, everything carries on and comes off cleanly
    (folder / "filelistu.win32patcher-undo.bin").rename(folder / "filelistu.win32.bin")
    hd(game, root, {"enemies"})
    results = revert(game, state)
    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game, "uc") == vanilla


def test_an_older_builds_backup_that_was_finished_that_way_never_goes_back(world):
    game, root, state = world()
    lt(game, state)
    hd(game, root, {"weapons"})
    as_an_older_build_left_it(game)
    before = game_files(game)

    with pytest.raises(InstallError, match="were not taken together"):
        hd_install.restore(hd_install.restore_prep(game, game / ".patcher-undo"))
    assert game_files(game) == before

    results = {r.patch_id: r for r in revert(game, state)}

    assert not results["hd_texture_pack"].ok
    assert "were not taken together" in results["hd_texture_pack"].detail
    # its undo is kept for the day the archives can go back, not judged on a scrambled pair
    assert not results[LT_ID].ok and "left in for now" in results[LT_ID].detail
    assert LT_ID in load_state(state)["applied_patches"]
    assert game_files(game) == before


def test_and_no_install_is_built_on_top_of_it(world):
    game, root, state = world()
    hd(game, root, {"weapons"})
    as_an_older_build_left_it(game)
    before = game_files(game)

    with pytest.raises(InstallError, match="were not taken together.*delete"):
        install(game, root, {"enemies"})
    assert game_files(game) == before


def test_half_a_backup_left_by_a_cut_copy_is_finished_from_the_untouched_game(world):
    """the one way half a pair gets there on its own, and the game hasn't moved since"""
    game, root, state = world()
    prep = hd_install.prepare(game, root, game / ".patcher-undo")
    prep.backup_dir.mkdir(parents=True)
    shutil.copyfile(prep.archives[1], prep.backup_dir / prep.archives[1].name)

    hd_install.back_up(prep)

    assert (prep.backup_dir / "filelistu.win32.bin").read_bytes() == prep.archives[0].read_bytes()
    assert hd_install.partnered(records(prep.backup_dir), "filelistu.win32.bin",
                                "white_imgu.win32.bin")


def test_a_half_whose_partner_the_game_still_holds_is_taken_again(world):
    game, root, state = world()
    prep = hd_install.prepare(game, root, game / ".patcher-undo")
    hd_install.back_up(prep)
    (prep.backup_dir / "filelistu.win32.bin").unlink()

    hd_install.back_up(prep)

    assert (prep.backup_dir / "filelistu.win32.bin").read_bytes() == prep.archives[0].read_bytes()


def test_every_pair_the_backup_takes_names_its_other_half(world):
    game, root, state = world(variants=("u", "c"))
    hd(game, root, {"weapons"})

    saved = records(backup_of(game))

    assert hd_install.partnered(saved, "filelistu.win32.bin", "white_imgu.win32.bin")


def test_a_game_already_left_scrambled_is_not_backed_up_as_whole(world):
    """the live game after the old Revert, its backup deleted before Steam had put it right"""
    game, root, state = world()
    hd(game, root, {"weapons"})
    folder = backup_of(game)
    shutil.copyfile(folder / "white_imgu.win32.bin", game / "alba_data/sys/white_imgu.win32.bin")
    shutil.rmtree(folder)
    before = game_files(game)

    with pytest.raises(InstallError, match="the game's filelistu.win32.bin and white_imgu.win32.bin "
                                           "don't belong together.*through Steam"):
        install(game, root, {"enemies"})

    assert game_files(game) == before
    assert archives_in(folder) == [] and leftovers(game) == []


def test_both_halves_moved_out_are_not_taken_again_from_the_modded_game(world):
    """the live mistake with both files: each was on record, so only those files will do"""
    game, root, state = world()
    hd(game, root, {"weapons"})
    folder = backup_of(game)
    for name in ("filelistu.win32.bin", "white_imgu.win32.bin"):
        (folder / name).rename(folder / (name + ".sent"))
    before = game_files(game)

    with pytest.raises(InstallError, match="was in the backup.*isn't any more.*put it back"):
        install(game, root, {"enemies"})
    with pytest.raises(InstallError, match="isn't in .* any more.*put it back"):
        hd_install.restore(hd_install.restore_prep(game, game / ".patcher-undo"))

    assert game_files(game) == before
    assert archives_in(folder) == [] and leftovers(game) == []


def forget(game, names):
    """files gone from the backup with no record they were ever there, as an older build left it"""
    folder = backup_of(game)
    data = json.loads((folder / hd_install.MANIFEST).read_text(encoding="utf-8"))
    for name in names:
        (folder / name).unlink()
        data["files"].pop(name, None)
    (folder / hd_install.MANIFEST).write_text(json.dumps(data), encoding="utf-8")


def test_a_zone_blob_is_never_kept_beside_the_index_from_after_its_install(zoned):
    """white_img and the blob are a pair too: the blob's index lives inside white_img"""
    game, root = zoned
    assert install(game, root, {"zones"}).ok
    forget(game, ("filelistu.win32.bin", "white_imgu.win32.bin"))
    folder = backup_of(game)
    before = everything_in(game)

    with pytest.raises(InstallError, match="white_z0010u_img.win32.bin that the index for it in "
                                           "white_imgu.win32.bin doesn't fit.*put them back"):
        install(game, root, {"zones"})

    assert everything_in(game) == before
    assert archives_in(folder) == [] and leftovers(game) == []


def test_with_no_white_img_of_its_own_a_blob_is_held_to_the_games(zoned):
    """it goes back under the game's index, which a zone install has moved past its end"""
    game, root = zoned
    assert install(game, root, {"zones"}).ok
    forget(game, ("filelistu.win32.bin", "white_imgu.win32.bin"))
    before = everything_in(game)

    with pytest.raises(InstallError, match="white_z0010u_img.win32.bin doesn't fit the index"):
        hd_install.restore(hd_install.restore_prep(game, game / ".patcher-undo"))
    assert everything_in(game) == before


def test_a_first_install_over_a_game_whose_own_zone_is_broken_says_so(zoned):
    game, root = zoned
    assert install(game, root, {"zones"}).ok
    blob = "white_z0010u_img.win32.bin"
    shutil.copyfile(backup_of(game) / blob, game / "alba_data" / "zone" / blob)
    shutil.rmtree(backup_of(game))
    before = everything_in(game)

    with pytest.raises(InstallError, match=f"the game's {blob} doesn't fit the index for it in "
                                           "white_imgu.win32.bin.*through Steam"):
        install(game, root, {"zones"})
    assert everything_in(game) == before


def test_restore_refuses_a_zone_blob_the_index_beside_it_has_moved_past(zoned):
    game, root = zoned
    assert install(game, root, {"zones"}).ok
    as_an_older_build_left_it(game, ("filelistu.win32.bin", "white_imgu.win32.bin"))
    before = everything_in(game)

    with pytest.raises(InstallError, match="white_z0010u_img.win32.bin doesn't fit the index for "
                                           "it in white_imgu.win32.bin"):
        hd_install.restore(hd_install.restore_prep(game, game / ".patcher-undo"))
    assert everything_in(game) == before


def test_an_injection_written_in_place_is_found_on_an_index_the_size_of_a_real_one(
        tmp_path, monkeypatch):
    """
    Leviathan's Tears moves two entries out of thousands, so a sample walks
    past it. An index from after it over the archive from before it, the way
    an older build could finish half a pair, has to be read all the way through
    """
    rows = [(f"pad/p{i:05d}.bin", filler(f"pad/p{i:05d}.bin", 120)) for i in range(1500)]
    game = make_game(tmp_path, zones=(), extra=rows + weather())
    root = make_mod(tmp_path, monkeypatch)
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    sys_dir = game / "alba_data" / "sys"
    vanilla_image = (sys_dir / "white_imgu.win32.bin").read_bytes()
    lt(game, tmp_path / "state.json")

    folder = backup_of(game)
    folder.mkdir(parents=True)
    (folder / "white_imgu.win32.bin").write_bytes(vanilla_image)
    shutil.copyfile(sys_dir / "filelistu.win32.bin", folder / "filelistu.win32.bin")
    files = {p.name: {"size": p.stat().st_size, "how": "taken",
                      "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in folder.iterdir()}
    (folder / hd_install.MANIFEST).write_text(json.dumps({"version": 1, "files": files,
                                                          "written": 1.0}))
    assert pair_problem(folder / "filelistu.win32.bin", folder / "white_imgu.win32.bin") == "", \
        "the sample was meant to miss this"
    before = everything_in(game)

    with pytest.raises(InstallError, match="were not taken together"):
        hd_install.restore(hd_install.restore_prep(game, game / ".patcher-undo"))
    with pytest.raises(InstallError, match="were not taken together"):
        install(game, root, {"enemies"})
    assert everything_in(game) == before
    assert entry_bytes(game, WEATHER[0]), "the game's own pair stopped reading"


def test_the_whole_backup_is_asked_not_just_the_voice_set_being_installed(world):
    game, root, state = world(variants=("u", "c"))
    voice(game, 1)
    hd(game, root, {"weapons"})
    voice(game, 0)
    hd(game, root, {"weapons"})
    as_an_older_build_left_it(game, ("filelistc.win32.bin",))
    voice(game, 1)
    before = game_files(game, "uc")

    with pytest.raises(InstallError, match="filelistc.win32.bin and a white_imgc.win32.bin that "
                                           "were not taken together"):
        install(game, root, {"enemies"})
    assert game_files(game, "uc") == before


def test_following_the_restore_refusal_gets_back_to_a_clean_game(world):
    game, root, state = world()
    vanilla = game_files(game)
    lt(game, state)
    hd(game, root, {"weapons"})
    as_an_older_build_left_it(game)

    first = {r.patch_id: r for r in revert(game, state)}
    advice = first["hd_texture_pack"].detail
    assert "verify the game files through Steam" in advice and str(backup_of(game)) in advice

    # what it says: Steam puts the files back, the backup goes, Revert again
    for path, data in vanilla.items():
        path.write_bytes(data)
    shutil.rmtree(backup_of(game))
    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game) == vanilla
    assert LT_ID not in load_state(state)["applied_patches"]


def test_a_partner_that_changes_while_it_is_copied_is_not_kept(world, monkeypatch):
    """the bytes checked are the bytes kept: Steam rewriting it mid-copy is caught"""
    game, root, state = world()
    prep = hd_install.prepare(game, root, game / ".patcher-undo")
    hd_install.back_up(prep)
    (prep.backup_dir / "filelistu.win32.bin").unlink()
    real = hd_install.stage_copy

    def rewritten_midway(source, target, **kw):
        if source.name == "filelistu.win32.bin":
            data = bytearray(source.read_bytes())
            data[len(data) // 2] ^= 0xFF
            source.write_bytes(bytes(data))
        return real(source, target, **kw)
    monkeypatch.setattr(hd_install, "stage_copy", rewritten_midway)

    with pytest.raises(InstallError, match="filelistu.win32.bin was in the backup.*the game's "
                                           "copy is not that file"):
        hd_install.back_up(prep)

    assert archives_in(prep.backup_dir) == ["white_imgu.win32.bin"]
    assert leftovers(game) == []


def test_a_partner_on_record_is_never_talked_round_by_the_bytes(world):
    """two halves that would read back fine together, but the record says otherwise"""
    game, root, state = world()
    prep = hd_install.prepare(game, root, game / ".patcher-undo")
    hd_install.back_up(prep)
    folder = prep.backup_dir
    saved = records(folder)
    saved["white_imgu.win32.bin"]["partner"] = "0" * 64
    view = {name: folder / name for name in ("filelistu.win32.bin", "white_imgu.win32.bin")}

    kind, names, why = hd_install._apart(view, saved, game)

    assert kind == "pair" and "is not the one white_imgu.win32.bin was taken with" in why


def test_the_other_voice_sets_half_pair_stops_an_install_and_a_revert(world):
    game, root, state = world(variants=("u", "c"))
    voice(game, 1)
    hd(game, root, {"weapons"})
    voice(game, 0)
    hd(game, root, {"weapons"})
    forget(game, ("white_imgc.win32.bin",))
    voice(game, 1)
    before = game_files(game, "uc")

    with pytest.raises(InstallError, match="has filelistc.win32.bin without white_imgc.win32.bin"):
        install(game, root, {"enemies"})
    with pytest.raises(InstallError, match="filelistc.win32.bin without white_imgc.win32.bin"):
        hd_install.restore(hd_install.restore_prep(game, game / ".patcher-undo"))
    assert game_files(game, "uc") == before


def test_the_listing_is_read_once_however_many_zones_share_it(tmp_path, monkeypatch):
    """half a second each on a real filelistu, and fifteen zones"""
    from patcher.mods import hd_zones

    game = make_game(tmp_path, zones=("z0010", "z0020", "z0030"))
    sys_dir = game / "alba_data" / "sys"
    view = {p.name: p for p in (*sys_dir.iterdir(), *(game / "alba_data" / "zone").iterdir())
            if p.name.endswith(".win32.bin")}
    real, reads = hd_zones._entries, []
    monkeypatch.setattr(hd_zones, "_entries", lambda path: reads.append(Path(path).name) or real(path))

    assert hd_install._apart(view, {}, game) == ("", (), "")
    assert reads.count("filelistu.win32.bin") == 2, reads    # the pair's own read, then the zones'


def test_an_older_builds_pairs_are_all_adopted_not_just_this_runs(tmp_path):
    """or every characters-only run reads the zones' white_img through again"""
    from tests.helpers import pack
    from tests.test_hd_install import make_game as xiii_game

    game = xiii_game(tmp_path)
    sys_dir = game / "white_data" / "sys"
    pack(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
         [("zone\\filelist_z010u.win32.bin", b"no zone in this one"), ("zone\\x.wdb", b"w" * 99)])
    folder = tmp_path / "store" / "hdproject"
    folder.mkdir(parents=True)
    for path in sys_dir.iterdir():
        shutil.copyfile(path, folder / path.name)              # src_9: copies, no backup.json

    hd_install.back_up(hd_install.prepare(game, tmp_path, tmp_path / "store"))

    saved = records(folder)
    assert hd_install.partnered(saved, "filelist_scru.win32.bin", "white_scru.win32.bin")
    assert hd_install.partnered(saved, "filelistu.win32.bin", "white_imgu.win32.bin")


def test_placeholders_shaped_like_the_real_ones_are_not_read(tmp_path):
    """
    a real stub is 0 inflated and 8 stored, the deflate of nothing, and reading
    one raises. 506 in filelistu and 390 in filelist_scru: a check that read them
    would refuse every real pair
    """
    import zlib

    from patcher.archive.entry import ChunkEntry
    from patcher.mods.hd_zones import zone_problem
    from tests.hd2_world import index_bytes, pack_rows

    def with_stub(rows, stub_path):
        body, entries = pack_rows(rows)
        body = bytearray(body)
        entries.append(ChunkEntry(path=stub_path, sector_offset=len(body) // 2048,
                                  uncompressed=0, stored=8))
        body += zlib.compress(b"")
        body += b"\x00" * (-len(body) % 2048)
        return bytes(body), entries

    blob, inner = with_stub([("bg/a/bin/block.win32.imgb", b"Z" * 3000)], "bg/a/bin/none.win32.trb")
    (tmp_path / "white_z0010u_img.win32.bin").write_bytes(blob)
    image, outer = with_stub([("zone/filelist_z0010u.win32.bin", index_bytes(inner)),
                              ("chr/pc/c108/bin/c108.win32.trb", b"T" * 5000)],
                             "chr/pc/c999/bin/c999.win32.trb")
    (tmp_path / "white_imgu.win32.bin").write_bytes(image)
    (tmp_path / "filelistu.win32.bin").write_bytes(index_bytes(outer))
    assert outer[-1].is_stub and inner[-1].is_stub

    assert pair_problem(tmp_path / "filelistu.win32.bin", tmp_path / "white_imgu.win32.bin",
                        samples=None) == ""
    assert zone_problem(tmp_path / "white_imgu.win32.bin", tmp_path / "filelistu.win32.bin",
                        tmp_path / "white_z0010u_img.win32.bin") == ""


def test_a_vanilla_zone_index_over_an_hd_blob_is_fine_and_a_missing_one_too(zoned):
    """imports only ever append, so the vanilla data is still where it was"""
    from patcher.mods.hd_zones import zone_problem

    game, root = zoned
    sys_dir, zone_dir = game / "alba_data" / "sys", game / "alba_data" / "zone"
    vanilla = tmp = game.parent / "vanilla"
    tmp.mkdir()
    for name in ("filelistu.win32.bin", "white_imgu.win32.bin"):
        shutil.copyfile(sys_dir / name, vanilla / name)
    assert install(game, root, {"zones"}).ok
    hd_blob = zone_dir / "white_z0010u_img.win32.bin"

    assert zone_problem(vanilla / "white_imgu.win32.bin", vanilla / "filelistu.win32.bin",
                        hd_blob) == ""
    shutil.copyfile(hd_blob, tmp / "white_z9999u_img.win32.bin")
    assert zone_problem(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
                        tmp / "white_z9999u_img.win32.bin") == "", "no index for it is nothing to fit"


def test_a_pair_retaken_after_revert_names_its_partner(world):
    """the backup held Leviathan's Tears, so Revert takes the clean pair into it again"""
    game, root, state = world()
    lt(game, state)
    hd(game, root, {"weapons"})

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    saved = records(backup_of(game))
    assert saved["white_imgu.win32.bin"]["how"] == "retaken"
    assert hd_install.partnered(saved, "filelistu.win32.bin", "white_imgu.win32.bin")


def test_a_rebuild_refuses_before_putting_its_own_voice_back(world):
    """
    it restores and then backs up again, and the back up asks the whole backup,
    so the restore has to ask it first or the install is gone before the refusal
    """
    game, root, state = world(variants=("u", "c"))
    voice(game, 1)
    hd(game, root, {"weapons"})
    voice(game, 0)
    hd(game, root, {"weapons"})
    forget(game, ("white_imgc.win32.bin",))
    voice(game, 1)
    before = game_files(game, "uc")
    prep = hd_install.prepare(game, root, game / ".patcher-undo")

    with pytest.raises(InstallError, match="filelistc.win32.bin without white_imgc.win32.bin"):
        hd_install.restore(prep, variant="u")
    assert game_files(game, "uc") == before


def test_reading_every_entry_in_lanes_still_names_the_first_bad_one(tmp_path, monkeypatch):
    """
    the first backup of a pair with no partner on record reads all of it, a
    few runs of entries at once now. Two damaged spots, so the one named has
    to be the earliest, the way reading them in order would have found it
    """
    import os

    from tests.hd2_world import index_bytes, pack_rows

    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    rows = [(f"chr/mon/m{n:03d}/bin/m{n:03d}.win32.imgb", (f"m{n:03d} " * 400).encode())
            for n in range(1200)]
    body, entries = pack_rows(rows)
    body = bytearray(body)
    for n in (900, 300):
        body[entries[n].byte_offset + 4] ^= 0xFF
    (tmp_path / "white_imgu.win32.bin").write_bytes(bytes(body))
    (tmp_path / "filelistu.win32.bin").write_bytes(index_bytes(entries))

    said = pair_problem(tmp_path / "filelistu.win32.bin", tmp_path / "white_imgu.win32.bin",
                        samples=None)

    assert said.startswith("chr/mon/m300/"), said
