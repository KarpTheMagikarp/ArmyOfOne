"""
Mods that aren't files for the archives: what the game reads loose off the
disk (patcher/mods/disk_files.py) and xdelta patches on their own
(patcher/mods/deltas.py), through the drawer on a stand-in XIII-2.
"""

import hashlib

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from patcher.mods import disk_files
from patcher.paths import undo_store_for
from tests.helpers import vcdiff_patch
from tests.test_mod_drops import library_with, vanilla, zipped

MOVIE = b"the game's own opening, " * 400


@pytest.fixture
def world(tmp_path, monkeypatch):
    library, game, paths = library_with(tmp_path)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    movies = game / "alba_data" / "movie"
    movies.mkdir(parents=True)
    (movies / "op_movie.win32.bik").write_bytes(MOVIE)
    (game / "alba_data" / "dlc" / "0000001").mkdir(parents=True)
    return UiBridge(manifest_paths=paths, steam_library_paths=[library]), game, tmp_path


def everything(game) -> dict:
    return {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(game.rglob("*")) if p.is_file() and ".patcher-undo" not in p.parts}


def test_a_movie_goes_in_over_the_games_own_and_comes_off_putting_it_back(world):
    bridge, game, tmp = world
    before = everything(game)
    drop = zipped(tmp, "HD Opening-31-1-0-1760000000.zip",
                  {"HD Opening/alba_data/movie/op_movie.win32.bik": b"in HD" * 900,
                   "HD Opening/readme.txt": b"hi"})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"] and done["kind"] == "disk", done
    assert done["message"] == ("Installed HD Opening: 1 file in the game's folder, over the "
                               "game's own, which is kept to put back.")
    movie = game / "alba_data" / "movie" / "op_movie.win32.bik"
    assert movie.read_bytes() == b"in HD" * 900
    rows = [m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "disk"]
    assert [(m["name"], m["warn"]) for m in rows] == [("HD Opening", "")]
    gone = bridge.remove_addon("ff13-2", "HD Opening", "disk")
    assert gone["ok"], gone
    assert everything(game) == before


def test_one_laid_out_from_its_own_folder_finds_the_file_by_its_path(world):
    bridge, game, tmp = world
    drop = zipped(tmp, "Opening.zip", {"movie/op_movie.win32.bik": b"x" * 900})
    assert bridge.install_mod("ff13-2", str(drop))["ok"]


def test_a_file_the_game_hasnt_got_goes_in_a_folder_it_has_and_comes_off(world):
    bridge, game, tmp = world
    before = everything(game)
    drop = zipped(tmp, "More DLC.zip", {"alba_data/dlc/0000001/extra.win32.bin": b"dlc"})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"] and "1 file the game didn't have" in done["message"], done
    assert bridge.remove_addon("ff13-2", "More DLC")["ok"]
    assert everything(game) == before


@pytest.mark.parametrize("member", [
    # nowhere the game reads: the data folder's top, a folder it hasn't got
    "alba_data/stray.bin", "alba_data/nothing/here.bin",
    # its archives, which only go in through their indexes
    "alba_data/sys/white_imgu.win32.bin",
])
def test_anywhere_else_is_turned_away(world, member):
    bridge, game, tmp = world
    before = everything(game)
    said = bridge.install_mod("ff13-2", str(zipped(tmp, "Stray.zip", {member: b"x" * 64})))
    assert not said["ok"], said
    assert everything(game) == before


def test_two_mods_cant_both_be_one_file(world):
    bridge, game, tmp = world
    assert bridge.install_mod("ff13-2", str(zipped(
        tmp, "First.zip", {"alba_data/movie/op_movie.win32.bik": b"first" * 99})))["ok"]
    said = bridge.install_mod("ff13-2", str(zipped(
        tmp, "Second.zip", {"alba_data/movie/op_movie.win32.bik": b"second" * 99})))
    assert not said["ok"] and "First's" in said["error"], said
    assert (game / "alba_data" / "movie" / "op_movie.win32.bik").read_bytes() == b"first" * 99


def test_a_new_version_comes_in_over_the_old_and_the_games_own_stays_kept(world):
    bridge, game, tmp = world
    movie = game / "alba_data" / "movie" / "op_movie.win32.bik"
    for n in (1, 2):
        assert bridge.install_mod("ff13-2", str(zipped(
            tmp, f"Opening v{n}.zip", {"alba_data/movie/op_movie.win32.bik": b"v%d" % n * 99})))["ok"]
    assert movie.read_bytes() == b"v2" * 99
    assert bridge.remove_addon("ff13-2", "Opening")["ok"]
    assert movie.read_bytes() == MOVIE


def test_one_changed_since_stays_and_its_original_is_still_kept(world):
    bridge, game, tmp = world
    movie = game / "alba_data" / "movie" / "op_movie.win32.bik"
    assert bridge.install_mod("ff13-2", str(zipped(
        tmp, "Opening.zip", {"alba_data/movie/op_movie.win32.bik": b"mod" * 99})))["ok"]
    movie.write_bytes(b"steam verified it back")
    rows = [m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "disk"]
    assert rows[0]["warn"] == "alba_data/movie/op_movie.win32.bik has changed since it went in"
    gone = bridge.remove_addon("ff13-2", "Opening", "disk")
    assert gone["ok"] and "changed since" in gone["message"]
    assert movie.read_bytes() == b"steam verified it back"


@pytest.mark.parametrize("member", ["alba_data/movie/op_movie.win32.bik",
                                    "alba_data/dlc/0000001/new.win32.bin"])
def test_an_install_cut_short_is_undone_from_its_record(world, monkeypatch, member):
    """a crash between the game's file going aside and the mod's going in"""
    bridge, game, tmp = world
    before = everything(game)
    real = disk_files.os.replace

    def cut(source, target):
        # the mod's copy going in, after the game's has gone aside
        if str(source).endswith(".patcher-new"):
            raise KeyboardInterrupt
        return real(source, target)

    monkeypatch.setattr(disk_files.os, "replace", cut)
    with pytest.raises(KeyboardInterrupt):
        disk_files.install(game, undo_store_for(game), "Opening", {"m": member},
                           lambda m: b"new" * 50)
    monkeypatch.setattr(disk_files.os, "replace", real)
    assert everything(game) == before
    assert disk_files.records(undo_store_for(game)) == []


def test_a_loose_files_xdelta_is_decoded_against_the_games_copy(world):
    bridge, game, tmp = world
    before = everything(game)
    patched = MOVIE[:4000] + b"a new ending"
    patch = vcdiff_patch(MOVIE, [[("copy", 0, 4000), ("add", b"a new ending")]])
    done = bridge.install_mod("ff13-2", str(zipped(
        tmp, "New Ending-40-1-0.zip", {"op_movie.win32.bik.xdelta": patch, "readme.txt": b"x"})))
    assert done["ok"], done
    assert (game / "alba_data" / "movie" / "op_movie.win32.bik").read_bytes() == patched
    assert bridge.remove_addon("ff13-2", "New Ending")["ok"]
    assert everything(game) == before


def test_one_made_from_another_version_of_the_file_says_so_and_changes_nothing(world):
    bridge, game, tmp = world
    before = everything(game)
    patch = vcdiff_patch(b"another build entirely" * 300, [[("copy", 0, 4000)]])
    said = bridge.install_mod("ff13-2", str(zipped(
        tmp, "Old Ending.zip", {"op_movie.win32.bik.xdelta": patch})))
    assert not said["ok"] and "made from another version of op_movie.win32.bik" in said["error"]
    assert everything(game) == before


@pytest.mark.parametrize("name, why", [
    ("ffxiii2img.exe.xdelta", "the game's own exe"),
    ("white_imgu.win32.bin.xdelta", "without the other"),
    ("nothing.dat.xdelta", "hasn't got a nothing.dat"),
])
def test_a_patch_for_the_exe_half_a_pair_or_nothing_says_why(world, name, why):
    bridge, game, tmp = world
    patch = vcdiff_patch(b"x" * 100, [[("add", b"y")]])
    said = bridge.install_mod("ff13-2", str(zipped(tmp, "Patch.zip", {name: patch})))
    assert not said["ok"] and why in said["error"], said


def test_its_header_names_the_file_when_its_own_name_doesnt():
    from patcher.mods import deltas

    head = b"\xd6\xc3\xc4\x00\x04" + bytes([len(b"new.bik//D:\\Game\\movie\\Op_Movie.win32.bik/")]) \
        + b"new.bik//D:\\Game\\movie\\Op_Movie.win32.bik/"
    assert deltas.named("patch.xdelta", head) == "op_movie.win32.bik"
    assert deltas.named("white_imgu.win32.xdelta", b"") == "white_imgu.win32"


def test_a_loaders_own_files_and_the_folders_beside_the_exe_are_never_a_loose_files(world):
    """HD Fonts and GUI's data folder is its record's, and ReShade's folder is ReShade's"""
    from patcher.mods import loader_mods

    bridge, game, tmp = world
    beside = game / "alba_data" / "prog" / "win" / "bin"
    loader_mods.install(beside, undo_store_for(game), "HD Fonts and GUI",
                        {"version.dll": "version.dll", "a.dds": "hd_textures/a.dds"},
                        lambda m: b"MZ" if m.endswith(".dll") else b"DDS")
    said = bridge.install_mod("ff13-2", str(zipped(
        tmp, "Fonts Fix.zip", {"alba_data/prog/win/bin/hd_textures/a.dds": b"better"})))
    assert not said["ok"] and "HD Fonts and GUI's" in said["error"], said
    assert (beside / "hd_textures" / "a.dds").read_bytes() == b"DDS"
    (beside / "ReShade").mkdir()
    (beside / "ReShade" / "ReShade.ini").write_bytes(b"[GENERAL]")
    said = bridge.install_mod("ff13-2", str(zipped(
        tmp, "Preset.zip", {"alba_data/prog/win/bin/ReShade/ReShade.ini": b"[mine]"})))
    assert not said["ok"]
    assert (beside / "ReShade" / "ReShade.ini").read_bytes() == b"[GENERAL]"


def test_a_list_drawn_while_one_goes_in_leaves_it_be(world):
    """its record is pending while it goes in, which is what a crash leaves too"""
    import threading

    bridge, game, tmp = world
    going, done = threading.Event(), threading.Event()
    seen = {}

    def slow(member):
        going.set()
        done.wait(5)
        return b"new" * 50

    def install():
        seen["done"] = disk_files.install(game, undo_store_for(game), "Opening",
                                          {"m": "alba_data/movie/op_movie.win32.bik"}, slow)

    worker = threading.Thread(target=install)
    worker.start()
    going.wait(5)
    rows = [m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "disk"]
    done.set()
    worker.join(5)
    assert seen["done"]["ok"]
    assert (game / "alba_data" / "movie" / "op_movie.win32.bik").read_bytes() == b"new" * 50
    assert rows == [], "listed as in while it was still going in"
    assert disk_files.listed(undo_store_for(game), game) == [{
        "name": "Opening", "kind": "disk", "files": ["alba_data/movie/op_movie.win32.bik"],
        "about": "1 file in the game's folder", "warn": "", "managed": True, "with": True}]


def test_the_full_revert_puts_the_games_own_loose_files_back(world):
    from pathlib import Path

    from patcher.manifest import load_manifest
    from patcher.revert import revert_all

    bridge, game, tmp = world
    before = everything(game)
    assert bridge.install_mod("ff13-2", str(zipped(
        tmp, "Opening.zip", {"alba_data/movie/op_movie.win32.bik": b"mod" * 99,
                             "alba_data/dlc/0000001/extra.win32.bin": b"dlc"})))["ok"]
    manifests = Path(__file__).resolve().parent.parent / "manifests"
    results = revert_all(game, load_manifest(manifests / "ffxiii2.json"),
                         tmp / ".state" / "ffxiii2.json")
    assert any(r.patch_id == "disk:Opening" and r.ok for r in results)
    assert everything(game) == before
    assert disk_files.records(undo_store_for(game)) == []


SETTINGS = b"[GENERAL]\r\nEffectSearchPaths=.\\reshade-shaders\\Shaders\\**\r\n" \
    b"PresetPath=.\\Fabula-Lous Shader.ini\r\nPerformanceMode=1\r\n"


def reshade_in(game):
    home = game / "alba_data" / "prog" / "win" / "bin" / "ReShade"
    (home / "reshade-shaders" / "Shaders").mkdir(parents=True)
    (home / "d3d9.dll").write_bytes(b"MZ reshade")
    (home / "ReShade.ini").write_bytes(SETTINGS)
    (home / "reshade-shaders" / "Shaders" / "Vibrance.fx").write_bytes(b"// ours")
    return home


def test_a_preset_on_its_own_goes_into_the_reshade_thats_in_and_comes_back_off(world):
    bridge, game, tmp = world
    home = reshade_in(game)
    before = everything(game)
    drop = zipped(tmp, "Moody Preset-55-1-0-1760000000.zip", {
        "Moody/Moody.ini": b"Techniques=Vibrance@Vibrance.fx,Lightroom@qUINT_lightroom.fx\r\n",
        "Moody/reshade-shaders/Shaders/Vibrance.fx": b"// theirs",
        "Moody/reshade-shaders/Shaders/qUINT_lightroom.fx": b"// lightroom",
        "Moody/reshade-shaders/Textures/lut.png": b"PNG",
        "Moody/readme.txt": b"enjoy"})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    assert done["message"] == ("Installed Moody Preset into ReShade: 1 preset and 2 shader and "
                               "texture files, keeping the 1 ReShade had already. ReShade starts "
                               "on it now, and its menu switches back.")
    assert (home / "Moody.ini").is_file()
    assert (home / "reshade-shaders" / "Shaders" / "Moody Preset" / "qUINT_lightroom.fx").is_file()
    assert (home / "reshade-shaders" / "Textures" / "Moody Preset" / "lut.png").is_file()
    assert (home / "reshade-shaders" / "Shaders" / "Vibrance.fx").read_bytes() == b"// ours"
    assert b"PresetPath=.\\Moody.ini\r\n" in (home / "ReShade.ini").read_bytes()
    assert bridge.remove_addon("ff13-2", done["modName"], "loader")["ok"]
    assert everything(game) == before


def test_a_preset_with_no_reshade_to_go_in_says_how_to_get_one(world):
    bridge, game, tmp = world
    said = bridge.install_mod("ff13-2", str(zipped(
        tmp, "Moody.zip", {"Moody.ini": b"Techniques=Vibrance@Vibrance.fx\n"})))
    assert not said["ok"] and "no ReShade for it yet" in said["error"], said


def test_reshade_moved_on_to_another_preset_since_is_left_on_it(world):
    bridge, game, tmp = world
    home = reshade_in(game)
    done = bridge.install_mod("ff13-2", str(zipped(
        tmp, "Moody.zip", {"Moody.ini": b"Techniques=Vibrance@Vibrance.fx\n"})))
    assert done["ok"], done
    settings = (home / "ReShade.ini").read_bytes().replace(b".\\Moody.ini", b".\\Mine.ini")
    (home / "ReShade.ini").write_bytes(settings)
    assert bridge.remove_addon("ff13-2", done["modName"], "loader")["ok"]
    assert b"PresetPath=.\\Mine.ini" in (home / "ReShade.ini").read_bytes()
