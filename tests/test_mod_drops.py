"""
The Mods drawer's one door: whatever gets dropped, through install_mod, onto a
real-format XIII-2 and XIII sat in a Steam library the way the app finds them.
"""

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from patcher.mods import game_files as gf
from tests.hd2_world import MODELS, make_game
from tests.helpers import make_fake_pe, pack_plain

MANIFESTS = Path(__file__).resolve().parent.parent / "manifests"
C108 = "chr/pc/c108/bin/c108.win32.imgb"
C201 = "chr/pc/c201/bin/c201.win32.imgb"


def library_with(tmp_path, xiii=False):
    library = tmp_path / "steam"
    common = library / "steamapps" / "common"
    game = make_game(common, "u", name="FFXIII2")
    make_game(common, "c", name="FFXIII2")
    (library / "steamapps" / "appmanifest_292140.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"292140"\n\t"installdir"\t\t"FFXIII2"\n}\n')
    paths = {"ffxiii2": MANIFESTS / "ffxiii2.json"}
    if xiii:
        first = common / "FFXIII"
        sys_dir = first / "white_data" / "sys"
        sys_dir.mkdir(parents=True)
        (first / "white_data" / "prog" / "win" / "bin").mkdir(parents=True)
        (first / "white_data" / "prog" / "win" / "bin" / "ffxiiiimg.exe").write_bytes(
            make_fake_pe())
        pack_plain(sys_dir / "white_scru.win32.bin", sys_dir / "filelist_scru.win32.bin",
                   [(C201, b"lightning" * 300), ("chr/pc/c201/bin/c201.win32.trb", b"t" * 900)])
        pack_plain(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
                   [("gui/resident/menu.win32.imgb", b"menu" * 300)])
        (library / "steamapps" / "appmanifest_292120.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"292120"\n\t"installdir"\t\t"FFXIII"\n}\n')
        paths["ffxiii"] = MANIFESTS / "ffxiii.json"
    return library, game, paths


@pytest.fixture
def world(tmp_path, monkeypatch):
    library, game, paths = library_with(tmp_path)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    return UiBridge(manifest_paths=paths, steam_library_paths=[library]), game, tmp_path


def zipped(tmp_path, name, files: dict) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in files.items():
            archive.writestr(member, data)
    return path


def folder(tmp_path, name, files: dict) -> Path:
    root = tmp_path / name
    for member, data in files.items():
        (root / member).parent.mkdir(parents=True, exist_ok=True)
        (root / member).write_bytes(data)
    return root


def vanilla(game) -> dict:
    sys_dir = game / "alba_data" / "sys"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sys_dir.iterdir())}


def test_a_zip_of_game_files_goes_in_lists_and_comes_off(world):
    bridge, game, tmp = world
    before = vanilla(game)
    drop = zipped(tmp, "Serah Dress-55-1-2-1699999999.zip",
                  {f"Serah Dress/{C108}": b"dress" * 2000, "Serah Dress/readme.txt": b"hi"})

    phases = []
    done = bridge.install_mod("ff13-2", str(drop), on_phase=lambda **said: phases.append(said))
    assert done["ok"] and done["kind"] == "files" and done["modName"] == "Serah Dress"
    assert "1 game file into both voice sets" in done["message"]
    # the line the drawer shows, with the whole account still beside it
    assert done["summary"] == "Installed Serah Dress." and done["title"] == "Serah Dress"
    # what it was doing as it went, by name, and how far through installing it was,
    # a percent that only climbs and only says a hundred once it's in
    assert [p["phase"] for p in phases[:2]] == ["read", "install"]
    shares = [p["done"] for p in phases[2:]]
    assert all(p["phase"] == "install" and p["name"] == "Serah Dress" and p["of"] == 100
               for p in phases[2:])
    assert shares[0] == 0 and shares[-1] == 100 and shares == sorted(set(shares))
    assert 99 in shares, "every pass, the read back too, is through before it says a hundred"

    listed = bridge.list_addons("ff13-2")
    mine = [m for m in listed["mods"] if m["kind"] == "files"]
    assert [m["name"] for m in mine] == ["Serah Dress"] and mine[0]["managed"]
    assert mine[0]["state"] == "in" and "1 game file in both voice sets" in mine[0]["detail"]

    gone = bridge.remove_addon("ff13-2", "Serah Dress", "files")
    assert gone["ok"] and "back the way it was" in gone["message"]
    assert vanilla(game) == before
    assert not [m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "files"]


def test_dropping_the_new_version_updates_the_old_one(world):
    bridge, game, tmp = world
    bridge.install_mod("ff13-2", str(folder(tmp, "Serah Dress v1.0", {C108: b"one" * 2000})))
    done = bridge.install_mod("ff13-2", str(folder(tmp, "Serah Dress v1.1", {C108: b"two" * 2000})))

    assert done["ok"] and done["matchedExisting"] and done["message"].startswith("Updated")
    assert [m["name"] for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "files"] == \
        ["Serah Dress"]


def test_the_hd_project_is_handed_to_its_options(world, monkeypatch):
    from tests.hd2_world import make_mod
    bridge, game, tmp = world
    mod = make_mod(tmp, monkeypatch)

    done = bridge.install_mod("ff13-2", str(mod))
    assert done["kind"] == "hdproject" and done["jobId"] == "hdproject2"
    assert gf.records(game / ".patcher-undo") == []


def test_hd_models_plus_on_another_game_is_sent_to_xiii_whole(world):
    """
    its models\\mod from a run of its own .bat is built for XIII, and a
    file in it that XIII-2's archives also list doesn't make it XIII-2's
    """
    bridge, game, tmp = world
    drop = folder(tmp, "HD Models Plus", {"_FF XIII HD Models+ Install.bat": b"@echo off",
                                          "SupportFiles/FFXIIItrbSetup.bat": b"@echo off",
                                          "SupportFiles/FF XIII HD Models.exe": b"MZ",
                                          f"models/mod/{C108}": b"hd" * 2000})
    before = vanilla(game)
    done = bridge.install_mod("ff13-2", str(drop))
    assert not done["ok"] and "is for Final Fantasy XIII." in done["error"], done
    assert "Install.bat" not in done["error"], "nobody's sent to run it"
    assert vanilla(game) == before


def test_a_mod_laid_out_for_another_game_is_sent_there(world):
    bridge, game, tmp = world
    drop = folder(tmp, "Lightning Outfit", {f"white_data/{C108}": b"x" * 100})
    done = bridge.install_mod("ff13-2", str(drop))
    assert not done["ok"] and "FINAL FANTASY XIII." in done["error"]


def test_files_only_the_other_installed_game_has_say_which_game(tmp_path, monkeypatch):
    library, game, paths = library_with(tmp_path, xiii=True)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    drop = folder(tmp_path, "Menu Recolor", {"gui/resident/menu.win32.imgb": b"blue" * 300})

    done = bridge.install_mod("ff13-2", str(drop))
    assert not done["ok"] and "is for FINAL FANTASY XIII." in done["error"]
    assert bridge.install_mod("ff13", str(drop))["ok"]


def test_versions_are_asked_about_then_installed(world):
    bridge, game, tmp = world
    drop = zipped(tmp, "Hair.zip", {f"Hair/Short/{C108}": b"s" * 3000,
                                    f"Hair/Long/{C108}": b"l" * 3000})
    asked = bridge.install_mod("ff13-2", str(drop))
    assert asked["kind"] == "versions" and asked["versions"] == ["Hair/Long", "Hair/Short"]

    done = bridge.install_mod("ff13-2", str(drop), version="Hair/Long")
    assert done["ok"]
    from tests.test_game_files import read
    assert read(game, C108) == b"l" * 3000


def test_a_dll_mod_still_goes_to_the_add_ons(world, monkeypatch):
    bridge, game, tmp = world
    seen = []
    monkeypatch.setattr(UiBridge, "install_addon",
                        lambda self, gid, path: seen.append(path) or
                        {"ok": True, "modName": "Hook", "matchedExisting": False})
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Hook.zip", {"Hook/hook.dll": b"MZ"})))
    assert done["ok"] and done["kind"] == "addon" and "add-ons" in done["message"]
    assert seen


def test_a_loose_dll_is_handed_over_in_a_folder_of_its_own(world, monkeypatch):
    bridge, game, tmp = world
    seen = []

    def taken(self, gid, path):
        seen.append(sorted(p.name for p in Path(path).iterdir()))
        return {"ok": True, "modName": Path(path).name}

    monkeypatch.setattr(UiBridge, "install_addon", taken)
    loose = tmp / "Better Hook.asi"
    loose.write_bytes(b"MZ")
    done = bridge.install_mod("ff13-2", str(loose))
    assert done["ok"] and seen == [["Better Hook.asi"]]


def test_game_files_and_a_dll_together_both_go_in(world, monkeypatch):
    bridge, game, tmp = world
    monkeypatch.setattr(UiBridge, "install_addon",
                        lambda self, gid, path: {"ok": True, "modName": "Both"})
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Both.zip", {
        f"Both/{C108}": b"b" * 3000, "Both/fix.dll": b"MZ"})))
    assert done["ok"] and "Installed Both" in done["message"] and "add-ons" in done["message"]


def test_a_dll_beside_game_files_moves_the_line_once_its_in(world, monkeypatch):
    bridge, game, tmp = world
    heard, at_addon = [], []

    def install_addon(self, gid, path):
        at_addon.append(max(said.get("done", 0) for said in heard))
        return {"ok": True, "modName": "Both"}

    monkeypatch.setattr(UiBridge, "install_addon", install_addon)
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Both.zip", {
        f"Both/{C108}": b"b" * 3000, "Both/fix.dll": b"MZ"})),
        on_phase=lambda **said: heard.append(said))

    assert done["ok"], done
    shares = [said["done"] for said in heard if "done" in said]
    assert at_addon and any(at_addon[0] < share < 100 for share in shares), (at_addon, shares)


def test_nothing_usable_says_what_it_found(world):
    bridge, game, tmp = world
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Photos.zip", {"a.jpg": b"x"})))
    assert not done["ok"] and "nothing in Photos" in done["error"]

    # its installer's played on a copy of the game, and what that came to is said
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Setup.zip", {"Setup/install.bat": b"x"})))
    assert done["error"] == ("Setup wasn't installed: its install.bat runs x, which isn't anywhere "
                             "in the download.")


def test_what_a_mod_left_out_is_said_with_what_went_in(world):
    bridge, game, tmp = world
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Mixed.zip", {
        f"Mixed/{C108}": b"m" * 3000, "Mixed/Install.bat": b"x",
        "Mixed/bg/loc001/a001/bin/block001.win32.imgb": b"z"})))
    assert done["ok"]
    assert "1 zone file, which sits" in done["message"]
    # its .bat was played first, and what that came to is the reason it's out
    assert ("Install.bat, its installer, which runs x, which isn't anywhere in the "
            "download") in done["message"]
    # part of the mod is out, and the short line doesn't hide it
    assert done["summary"] == "Installed Mixed. Some of it didn't go in."


def test_a_7z_that_turns_out_to_be_a_mod_is_read_from_its_unpack(world, monkeypatch):
    bridge, game, tmp = world
    unpacked = folder(tmp, ".Dress-unpacked", {f"Dress/{C108}": b"7z" * 2000})
    archive = tmp / "Dress.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c")

    def unpack(self, path, progress=None):
        progress(3 << 20, 8 << 20)
        return unpacked, ""

    monkeypatch.setattr(UiBridge, "_unpack_for_project", unpack)
    phases = []
    done = bridge.install_mod("ff13-2", str(archive), on_phase=lambda **said: phases.append(said))
    assert done["ok"] and done["modName"] == "Dress"
    # a mod's unpack isn't kept, only the HD Project's is
    assert not unpacked.exists()
    # and it said how far it had got, in bytes, which the drawer shows as a percent
    assert {"phase": "unpack", "name": "Dress", "done": 3 << 20, "of": 8 << 20,
            "bytes": True} in phases


def test_an_unpack_that_isnt_the_hd_project_goes_once_thats_known(world, monkeypatch):
    """an HD-only drop of some other 7z: its archive is what any install reads"""
    bridge, game, tmp = world
    unpacked = folder(tmp, ".Dress-unpacked", {f"Dress/{C108}": b"7z" * 2000})
    archive = tmp / "Dress.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c")
    monkeypatch.setattr(UiBridge, "_unpack_for_project",
                        lambda self, path, progress=None: (unpacked, ""))

    assert bridge.identify_drop(str(archive))["kind"] == "archive"
    assert not unpacked.exists()


def test_reading_the_drawer_makes_no_folder_for_novafix(world):
    """opening the folder makes it, the list only says what's in it"""
    bridge, game, tmp = world
    addons = game / "alba_data" / "prog" / "win" / "bin" / "addons"

    assert bridge.list_addons("ff13-2")["ok"]
    assert not addons.exists()
    assert bridge.addons_dir("ff13-2")["ok"] and addons.is_dir()


def test_a_7z_that_is_the_hd_project_keeps_its_unpack_for_the_install(world, monkeypatch):
    from tests.hd2_world import make_mod
    bridge, game, tmp = world
    unpacked = make_mod(tmp, monkeypatch, name=".HD-unpacked")
    archive = tmp / "HD.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c")
    monkeypatch.setattr(UiBridge, "_unpack_for_project",
                        lambda self, path, progress=None: (unpacked, ""))

    done = bridge.install_mod("ff13-2", str(archive))
    assert done["kind"] == "hdproject" and done["unpackedTo"] == str(unpacked)
    assert unpacked.is_dir()


def test_removing_by_name_finds_a_file_mod_without_being_told_which_kind(world):
    bridge, game, tmp = world
    bridge.install_mod("ff13-2", str(folder(tmp, "Thing", {C108: b"t" * 3000})))
    assert bridge.remove_addon("ff13-2", "Thing")["ok"]
    assert gf.records(game / ".patcher-undo") == []


def test_revert_takes_the_drawers_mods_off_too(world):
    from patcher.revert import revert_all
    bridge, game, tmp = world
    before = vanilla(game)
    bridge.install_mod("ff13-2", str(folder(tmp, "Thing", {C108: b"t" * 3000})))

    manifest = bridge._manifests["ff13-2"]
    results = revert_all(game, manifest, tmp / ".state" / "ffxiii2.json")
    mine = [r for r in results if r.patch_id == "files:Thing"]
    assert mine and mine[0].ok
    assert vanilla(game) == before


def test_the_hd_checks_dont_count_a_dropped_costume_as_hd(world):
    """a file mod is its own mod, so the HD survey reads the game as if it weren't there"""
    from patcher.mods import hd_state
    bridge, game, tmp = world
    index = game / "alba_data" / "sys" / "filelistu.win32.bin"
    plain = hd_state.without_file_mods(index)
    bridge.install_mod("ff13-2", str(folder(tmp, "Big", {C108: b"B" * (MODELS[C108] * 3)})))

    assert hd_state.without_file_mods(index) == plain
    from patcher.mods import hd_vanilla
    assert hd_vanilla.index_sizes(index)[C108] == MODELS[C108] * 3


def test_removing_a_mod_another_sits_on_says_so(world):
    bridge, game, tmp = world
    bridge.install_mod("ff13-2", str(folder(tmp, "First", {C108: b"1" * 3000})))
    bridge.install_mod("ff13-2", str(folder(tmp, "Second", {C108: b"2" * 3000})))

    listed = {m["name"]: m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "files"}
    assert "all of them covered by Second" in listed["First"]["detail"], listed
    gone = bridge.remove_addon("ff13-2", "First", "files")
    assert gone["ok"] and "Another mod had replaced its files" in gone["message"], gone


def test_removing_one_that_took_another_with_it_says_so(world):
    import shutil
    from tests.test_game_files import backup, repack
    bridge, game, tmp = world
    backup(game)
    outfit = folder(tmp, "Outfit", {C108: b"O" * 3000,
                                    "chr/pc/c108/bin/c108.win32.trb": b"T" * 4000})
    bridge.install_mod("ff13-2", str(outfit))
    shutil.rmtree(outfit)
    bridge.install_mod("ff13-2", str(folder(tmp, "Hair", {C108: b"H" * 3000})))
    repack(game, "u")
    repack(game, "c")

    gone = bridge.remove_addon("ff13-2", "Hair", "files")
    assert gone["ok"] and "Outfit was under it" in gone["message"], gone
    assert gf.records(game / ".patcher-undo") == []


def test_a_dll_that_wont_copy_doesnt_say_the_files_didnt_go_in(world, monkeypatch):
    bridge, game, tmp = world

    def no_room(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(bridge, "_addon_from", no_room)
    done = bridge.install_mod("ff13-2", str(folder(tmp, "Both", {C108: b"b" * 3000,
                                                                "fix.dll": make_fake_pe()})))
    assert done["ok"] and done["kind"] == "files", done
    assert "Installed Both" in done["message"] and ".dll didn't go in" in done["message"]


def test_remove_waits_for_the_game_to_let_go_of_its_archives(world, monkeypatch):
    import patcher.mods.hd_install as hd_install
    bridge, game, tmp = world
    bridge.install_mod("ff13-2", str(folder(tmp, "Thing", {C108: b"t" * 3000})))
    monkeypatch.setattr(hd_install, "in_use", lambda paths: [("white_imgu.win32.bin", "busy")])

    gone = bridge.remove_addon("ff13-2", "Thing", "files")
    assert not gone["ok"] and "game is running" in gone["error"], gone
    assert [r["name"] for r in gf.records(game / ".patcher-undo")] == ["Thing"]


def test_a_zip_that_never_finished_downloading_is_said_to_be(world):
    bridge, game, tmp = world
    broken = tmp / "Dress.zip"
    broken.write_bytes(b"PK\x03\x04 and then nothing")
    done = bridge.install_mod("ff13-2", str(broken))
    assert not done["ok"] and "finished downloading" in done["error"], done


def test_an_unpack_that_fills_the_drive_says_so(world, monkeypatch):
    import patcher.download as download
    import patcher.ingest as ingest
    bridge, game, tmp = world
    pack = tmp / "Big Pack.7z"
    pack.write_bytes(b"7z" + b"\0" * 4000)
    monkeypatch.setattr(ingest, "try_system_extract", lambda *a, **k: None)
    monkeypatch.setattr(download, "free_space", lambda path: 10 * 1024 * 1024)

    done = bridge.install_mod("ff13-2", str(pack))
    assert not done["ok"] and "ran out of room" in done["error"], done
    assert not any(p.name.startswith(".Big Pack-unpacked") for p in tmp.iterdir())


def test_a_dll_named_after_windows_goes_beside_the_exe_with_its_folders(world):
    """
    HD Fonts and GUI's layout: version.dll, the two folders it reads from and a
    readme. Its readme says all of it into the exe's folder, and the .dds in
    hd_textures are its data, not art that needs packing
    """
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    drop = zipped(tmp, "HD Fonts and GUI-40-2-2-1779722868.zip", {
        "version.dll": pe32(), "README.txt": b"copy these into alba_data/prog/win/bin",
        "hd_textures/hash_database.txt": b"h", "hd_textures/face000/a.dds": b"DDS a",
        "hd_textures_shaders/x.cso": b"cso", "hd_textures_shaders/hash_table.txt": b"t"})
    beside = game / "alba_data" / "prog" / "win" / "bin"
    before = vanilla(game)

    done = bridge.install_mod("ff13-2", str(drop))

    assert done["ok"] and done["kind"] == "loader", done
    assert done["message"] == ("Installed HD Fonts and GUI beside ffxiii2img.exe: version.dll and "
                               "2 folders, 5 files in all."), done["message"]
    assert done["summary"] == "Installed HD Fonts and GUI.", done["summary"]
    assert (beside / "version.dll").read_bytes() == pe32()
    assert (beside / "hd_textures" / "face000" / "a.dds").read_bytes() == b"DDS a"
    assert not (beside / "README.txt").exists()
    assert vanilla(game) == before, "the archives were touched"
    assert not (beside / "addons" / "HD Fonts and GUI").exists(), "it went to NovaFix's add-ons too"
    listed = [m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "loader"]
    assert listed and listed[0]["name"] == "HD Fonts and GUI"
    # the name NovaFix's popup gives it, VERSION, said where the mod is
    assert listed[0]["about"] == "Loads as version.dll"

    gone = bridge.remove_addon("ff13-2", "HD Fonts and GUI")
    assert gone["ok"] and "Everything it put beside ffxiii2img.exe is gone" in gone["message"]
    assert not (beside / "version.dll").exists() and not (beside / "hd_textures").exists()


def test_hd_fonts_in_from_the_drawer_is_its_worth_getting_rows_mod(world):
    """
    Worth getting's HD Fonts and GUI row says it's in once its download has gone
    in as a mod beside the exe, by the name the download carries, and stops
    saying so once its dll has gone
    """
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    drop = zipped(tmp, "4K HD Fonts and GUI plus Shader Fixes-40-2-2-1779722868.zip", {
        "version.dll": pe32(), "hd_textures/face000/a.dds": b"DDS a",
        "hd_textures_shaders/hash_table.txt": b"t"})
    other = zipped(tmp, "Faster Menus-90-1-0-1779722868.zip", {"winmm.dll": pe32()})
    assert bridge.install_mod("ff13-2", str(drop))["ok"]
    assert bridge.install_mod("ff13-2", str(other))["ok"]

    rows = {m["name"]: m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "loader"}
    assert rows["4K HD Fonts and GUI plus Shader Fixes"]["jobId"] == "hdfonts"
    assert "jobId" not in rows["Faster Menus"], "a mod that isn't the row's"
    # the watch for it takes any archive, with no floor: its size isn't known
    assert bridge.download_filter("ff13-2", "hdfonts")["minBytes"] is None

    (game / "alba_data" / "prog" / "win" / "bin" / "version.dll").unlink()
    rows = {m["name"]: m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "loader"}
    assert "jobId" not in rows["4K HD Fonts and GUI plus Shader Fixes"]


def test_hd_fonts_rows_go_by_the_name_and_the_dll_its_entry_expects(world):
    """
    the Nexus files spell it HD Fonts And GUI. A fix made for it carries the
    name without its version.dll, and a settings file gone from beside the exe
    is no reason to say it's out
    """
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    nexus = zipped(tmp, "(STANDALONE VERSION) HD Fonts And GUI - Base mod-40-2-2-1779722868.zip", {
        "version.dll": pe32(), "version.ini": b"[HD]\r\n",
        "hd_textures/face000/a.dds": b"DDS a"})
    fix = zipped(tmp, "HD Fonts and GUI Korean Fix-77-1-0-1779722868.zip", {"dsound.dll": pe32()})
    assert bridge.install_mod("ff13-2", str(nexus))["ok"]
    assert bridge.install_mod("ff13-2", str(fix))["ok"]

    def tags():
        return {m["name"]: m.get("jobId") for m in bridge.list_addons("ff13-2")["mods"]
                if m["kind"] == "loader"}

    assert tags()["(STANDALONE VERSION) HD Fonts And GUI - Base mod"] == "hdfonts"
    assert tags()["HD Fonts and GUI Korean Fix"] is None
    (game / "alba_data" / "prog" / "win" / "bin" / "version.ini").unlink()
    assert tags()["(STANDALONE VERSION) HD Fonts And GUI - Base mod"] == "hdfonts"


def test_a_nexus_row_that_expects_nothing_tags_nothing(tmp_path, monkeypatch):
    """XIII's HD Models Plus expects no file, so by name alone a fix made for it would read as it"""
    from tests.test_loader_mods import pe32

    library, _, paths = library_with(tmp_path, xiii=True)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    drop = zipped(tmp_path, "HD Models Plus Hair Fix-99-1-0-1779722868.zip", {"dsound.dll": pe32()})
    assert bridge.install_mod("ff13", str(drop))["ok"]
    rows = [m for m in bridge.list_addons("ff13")["mods"] if m["kind"] == "loader"]
    assert rows and all(m.get("jobId") is None for m in rows), rows


def test_a_dll_in_its_archives_spelling_still_tags_the_row(world):
    """Version.dll, as an archive can have it, is the same file to Windows"""
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    drop = zipped(tmp, "4K HD Fonts and GUI plus Shader Fixes-40-2-2-1779722868.zip", {
        "Version.dll": pe32(), "hd_textures/face000/a.dds": b"DDS a"})
    assert bridge.install_mod("ff13-2", str(drop))["ok"]
    rows = {m["name"]: m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "loader"}
    assert rows["4K HD Fonts and GUI plus Shader Fixes"]["jobId"] == "hdfonts"


def test_only_a_nexus_rows_name_tags_a_mod_beside_the_exe(world, monkeypatch):
    """a bundled patch's label, the shader's say, is never a Worth getting row's mod"""
    from patcher.manifest import Patch
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    manifest = bridge.manifest_for("ff13-2")
    shader = next(p for p in manifest.patches if p.id == "install_reshade")
    monkeypatch.setattr(shader, "expects", ["version.dll"])
    drop = zipped(tmp, "Fabula-Lous Shader extras-90-1-0-1779722868.zip", {"version.dll": pe32()})
    assert bridge.install_mod("ff13-2", str(drop))["ok"]
    rows = [m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "loader"]
    assert [m.get("jobId") for m in rows] == [None]


def test_a_loader_mod_says_how_far_it_has_got(world, monkeypatch):
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    drop = zipped(tmp, "HD Fonts and GUI-40-2-2-1779722868.zip", {
        "version.dll": pe32(), **{f"hd_textures/face{n:03d}/a.dds": b"DDS" * 9000
                                  for n in range(12)}})
    heard = []

    from patcher.mods import loader_mods

    real_write, real_replace, marks = loader_mods._write, loader_mods.os.replace, {}

    def write(record):
        # the record goes down between reading the drop and writing its files
        marks.setdefault("read", len(heard))
        return real_write(record)

    def replace(src, dst, *args, **kwargs):
        # and the files go into place once they're all written
        if str(src).endswith(loader_mods.NEW):
            marks.setdefault("written", len(heard))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(loader_mods, "_write", write)
    monkeypatch.setattr(loader_mods.os, "replace", replace)
    done = bridge.install_mod("ff13-2", str(drop), on_phase=lambda **said: heard.append(said))

    assert done["ok"] and done["kind"] == "loader", done
    shares = [said["done"] for said in heard if said["phase"] == "install" and "done" in said]
    assert shares[0] == 0 and shares[-1] == 100 and shares == sorted(set(shares)), shares
    reading = [said for said in heard[:marks["read"]] if "done" in said]
    writing = [said for said in heard[marks["read"]:marks["written"]] if "done" in said]
    assert len(reading) > 3 and len(writing) > 3, (len(reading), len(writing), shares)


def test_a_drop_of_game_files_and_a_loader_says_a_hundred_once_both_are_in(world, monkeypatch):
    """the game's files said a hundred on their own while the loader was still to go in"""
    from patcher.mods import loader_mods
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    drop = zipped(tmp, "Mixed.zip", {f"Mixed/{C108}": b"dress" * 2000, "version.dll": pe32(),
                                     **{f"hd_textures/face{n:03d}/a.dds": b"DDS" * 9000
                                        for n in range(12)}})
    heard, before_loader = [], []
    real = loader_mods.install

    def install(*args, **kwargs):
        before_loader.append(max(said.get("done", 0) for said in heard))
        return real(*args, **kwargs)

    monkeypatch.setattr(loader_mods, "install", install)
    done = bridge.install_mod("ff13-2", str(drop), on_phase=lambda **said: heard.append(said))

    assert done["ok"] and "version.dll" in done["message"], done
    shares = [said["done"] for said in heard if said["phase"] == "install" and "done" in said]
    assert shares[0] == 0 and shares[-1] == 100 and shares == sorted(set(shares)), shares
    assert before_loader and before_loader[0] < 100, before_loader
    assert any(before_loader[0] < share < 100 for share in shares), shares


def test_a_proxy_dll_this_app_owns_isnt_replaced(world):
    """a ReShade or DXVK download is d3d9.dll, which is NovaFix"""
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    beside = game / "alba_data" / "prog" / "win" / "bin"
    (beside / "d3d9.dll").write_bytes(b"NovaFix")

    done = bridge.install_mod("ff13-2", str(zipped(tmp, "ReShade.zip", {"d3d9.dll": pe32()})))

    assert not done["ok"] and "d3d9.dll beside the game's exe is NovaFix" in done["error"], done
    assert (beside / "d3d9.dll").read_bytes() == b"NovaFix"


def red_rose(tmp, name="Red Rose Beachwear 2.0 Patcher-7-2-0-1742241455.zip"):
    """Red Rose's layout: a .NET patcher, the libraries it runs on, and its payload as a zip"""
    import io

    from tests.test_loader_mods import pe32

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr(f"PatchData/{C108}", b"r" * 3000)
        archive.writestr("PatchData/chr/pc/c108/bin/c108.win32.trb", b"t" * 2000)
    return zipped(tmp, name, {"FFXIII2WhiteIMGPatcher.exe": pe32(dll=False, managed=True),
                              "LocateFile.dll": pe32(managed=True),
                              "WhiteBinTools.dll": pe32(managed=True),
                              "PatchData.zip": inner.getvalue()})


def test_a_patchers_payload_zip_goes_in_and_its_net_code_stays_out(world, monkeypatch):
    """
    the files it carries went straight in, and its .dlls are .NET libraries
    for its own exe, which NovaFix's add-ons were never the place for
    """
    from tests.test_game_files import read

    bridge, game, tmp = world
    addons = []
    monkeypatch.setattr(UiBridge, "install_addon",
                        lambda self, gid, path: addons.append(path) or {"ok": True})

    done = bridge.install_mod("ff13-2", str(red_rose(tmp)))

    assert done["ok"] and done["kind"] == "files", done
    assert read(game, C108) == b"r" * 3000
    assert not addons, "its .NET libraries went to NovaFix"
    assert ("FFXIII2WhiteIMGPatcher.exe and the .NET libraries it runs on, its own installer, "
            "which wasn't needed since the files it carries went in directly") in done["message"]
    # nothing of the mod stayed out, so the line doesn't say anything did
    assert done["summary"] == "Installed Red Rose Beachwear.", done["summary"]
    assert not [p for p in tmp.iterdir() if p.name.startswith("ffxiii-mod-")], "staging stayed"


def test_a_zip_inside_a_dropped_folder_is_read_in_place(world):
    import shutil

    from tests.test_game_files import read

    bridge, game, tmp = world
    unpacked = tmp / "Red Rose"
    with zipfile.ZipFile(red_rose(tmp)) as archive:
        archive.extractall(unpacked)
    done = bridge.install_mod("ff13-2", str(unpacked))
    assert done["ok"] and read(game, C108) == b"r" * 3000, done
    shutil.rmtree(unpacked)


def test_a_mod_whose_only_files_are_in_a_7z_inside_it_says_to_unpack_that(world):
    bridge, game, tmp = world
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Wrapped.zip", {
        "Wrapped/Data.7z": b"7z\xbc\xaf'\x1c", "Wrapped/readme.txt": b"r"})))
    assert not done["ok"] and "Data.7z inside it, which needs unpacking first" in done["error"]


def test_a_locked_zip_inside_the_drop_is_one_to_unpack_not_files_to_read(world):
    """zipfile lists one locked with a password happily, and only reading it fails"""
    from tests.helpers import make_locked_zip_bytes

    bridge, game, tmp = world
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Wrapped.zip", {
        "Wrapped/Data.zip": make_locked_zip_bytes({C108: b"r" * 3000}, b"SECRET"),
        "Wrapped/readme.txt": b"r"})))
    assert not done["ok"]
    assert "Data.zip inside it, which won't open here: it's damaged, or locked with a password" \
        in done["error"], done


def test_a_dropped_zip_locked_with_a_password_says_to_unpack_it_with_7zip(world):
    from tests.helpers import make_locked_zip_bytes

    bridge, game, tmp = world
    locked = tmp / "Locked.zip"
    locked.write_bytes(make_locked_zip_bytes({C108: b"r" * 3000}, b"SECRET"))
    done = bridge.install_mod("ff13-2", str(locked))
    assert not done["ok"]
    assert "Locked.zip is locked with a password, or packed so only 7-Zip opens it" \
        in done["error"], done


def _with_extra(tmp_path, name, extra, seal):
    from tests.helpers import seal_member

    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        if extra.endswith("/"):
            archive.writestr(zipfile.ZipInfo(extra), b"")
        archive.writestr(C108, b"r" * 3000)
        if not extra.endswith("/"):
            archive.writestr(extra, b"thanks for downloading " * 20)
    path.write_bytes(seal_member(path.read_bytes(), extra, **seal))
    return path


@pytest.mark.parametrize("extra, seal", [
    ("readme.txt", {"locked": True}), ("preview.png", {"method": 9}), ("chr/", {"locked": True}),
], ids=["locked readme", "Deflate64 preview", "flagged folder entry"])
def test_a_zip_that_only_seals_something_it_doesnt_need_still_goes_in(world, extra, seal):
    """7-Zip users lock a readme or pack a picture with Deflate64; the game file reads fine"""
    from tests.test_game_files import read

    bridge, game, tmp = world
    done = bridge.install_mod("ff13-2", str(_with_extra(tmp, "Outfit.zip", extra, seal)))
    assert done["ok"], done
    assert read(game, C108) == b"r" * 3000


@pytest.mark.parametrize("extra", ["credits.txt", "chr/"], ids=["its credits", "a folder entry"])
def test_a_zip_inside_the_drop_that_only_locks_what_it_doesnt_need_still_opens(world, extra):
    from tests.test_game_files import read

    bridge, game, tmp = world
    inner = _with_extra(tmp, "Data.zip", extra, {"locked": True}).read_bytes()
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Wrapped.zip", {
        "Wrapped/Data.zip": inner, "Wrapped/readme.txt": b"r"})))
    assert done["ok"], done
    assert read(game, C108) == b"r" * 3000


def test_a_game_file_locked_in_its_zip_says_to_unpack_that_zip(world):
    from tests.helpers import seal_member

    bridge, game, tmp = world
    path = zipped(tmp, "Outfit.zip", {C108: b"r" * 3000, "readme.txt": b"the password is 1234"})
    path.write_bytes(seal_member(path.read_bytes(), C108, locked=True))
    done = bridge.install_mod("ff13-2", str(path))
    assert not done["ok"]
    assert "c108.win32.imgb in Outfit.zip is locked with a password, or packed so only 7-Zip " \
           "opens it. Unpack Outfit.zip with 7-Zip" in done["error"], done


@pytest.mark.parametrize("inner", ["Data.zip", "Data.7z", "PatchData.bin"])
def test_an_archive_locked_inside_the_dropped_zip_says_which_and_where(world, inner):
    from tests.helpers import seal_member

    bridge, game, tmp = world
    path = zipped(tmp, "Outfit.zip", {inner: b"inner archive bytes " * 50, "readme.txt": b"r"})
    path.write_bytes(seal_member(path.read_bytes(), inner, locked=True))
    done = bridge.install_mod("ff13-2", str(path))
    assert not done["ok"]
    assert f"{inner} in Outfit.zip is locked with a password, or packed so only 7-Zip opens " \
           "it. Unpack Outfit.zip with 7-Zip" in done["error"], done


def test_a_dropped_zip_thats_one_part_of_several_says_to_unpack_it_with_its_parts(world):
    from tests.helpers import make_split_zip

    bridge, game, tmp = world
    for name, data in make_split_zip({C108: b"r" * 3000}).items():
        (tmp / name).write_bytes(data)
    done = bridge.install_mod("ff13-2", str(tmp / "pack.zip"))
    assert not done["ok"]
    assert "pack.zip is the last part of an archive split into pieces. Unpack it with 7-Zip, " \
           "with every part beside it" in done["error"], done


def test_a_nova_chrysalia_modpack_names_itself_and_splits_by_language(world):
    """
    modconfig.ini's Name is the mod's, EN-Data is the English voice set's and
    JP-Data the Japanese one's, so two files for one place aren't versions to
    pick between. Its External installer is played the way Nova runs it
    (test_nova_external.py), and its code patch read (test_code_patches.py)
    """
    from tests.test_game_files import read

    bridge, game, tmp = world
    pack = zipped(tmp, "serah_pack.ncmp", {
        "modconfig.ini": b"[ModPackConfig]\r\nName = Serah's Summer\r\nGameEntry = 2\r\n"
                         b"[NovaChysaliaConfig]\r\nDataPatch = true\r\n",
        f"EN-Data/{C108}": b"e" * 3000, f"JP-Data/{C108}": b"j" * 3000,
        "External/Install.bat": b"@echo off", "Code/ffxiii2img.nccp": b"patch"})

    done = bridge.install_mod("ff13-2", str(pack))

    assert done["ok"] and done["modName"] == "Serah's Summer", done
    assert read(game, C108, "u") == b"e" * 3000 and read(game, C108, "c") == b"j" * 3000
    assert "Serah's Summer's code patches didn't go in: ffxiii2img.nccp has no code patch" \
        in done["message"]
    assert ("Install.bat, its installer, which played to the end on a copy of the game without "
            "changing it") in done["message"]


def test_a_modpack_for_another_game_says_which(world):
    bridge, game, tmp = world
    pack = zipped(tmp, "lr.ncmp", {"Pack/modconfig.ini": b"[ModPackConfig]\nName=Lumina\n"
                                                         b"GameEntry=3\n",
                                   f"Pack/Data/{C108}": b"x" * 3000})
    done = bridge.install_mod("ff13-2", str(pack))
    assert not done["ok"] and "Lumina is a Nova Chrysalia modpack for LIGHTNING RETURNS" \
        in done["error"], done


def test_a_drop_windows_wont_hand_over_is_said_to_be_blocked(world, monkeypatch):
    """
    Defender quarantines a flagged file, and reading it then fails with its own
    error, which used to come out as "isn't a Windows binary"
    """
    bridge, game, tmp = world
    drop = folder(tmp, "Hook", {"version.dll": make_fake_pe(machine=0x014C)})
    real = Path.read_bytes

    def flagged(self):
        if self.name == "version.dll":
            error = OSError(22, "Operation did not complete successfully because the file "
                                "contains a virus")
            error.winerror = 225
            raise error
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flagged)
    done = bridge.install_mod("ff13-2", str(drop))

    assert not done["ok"] and "Windows Security blocked version.dll as a threat" in done["error"]
    assert "isn't a Windows binary" not in done["error"]


def test_the_add_on_sieve_tells_net_and_blocked_dlls_from_broken_ones(tmp_path, monkeypatch):
    from patcher import addon_sieve
    from tests.test_loader_mods import pe32

    staged = tmp_path / "stage"
    staged.mkdir()
    (staged / "WhiteBinTools.dll").write_bytes(pe32(managed=True))
    (staged / "Hook.dll").write_bytes(pe32())
    result = addon_sieve.sift(staged, "Mixed.zip")
    assert [p.name for p in result.payloads] == ["Hook.dll"]
    assert ".NET library" in dict(result.rejected)["WhiteBinTools.dll"]

    real = Path.open

    def flagged(self, *a, **k):
        if self.name == "Hook.dll":
            error = OSError(22, "virus")
            error.winerror = 226
            raise error
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "open", flagged)
    result = addon_sieve.sift(staged, "Mixed.zip")
    assert dict(result.rejected)["Hook.dll"].startswith("Windows Security blocked Hook.dll")


def test_a_64_bit_proxy_never_goes_beside_the_32_bit_exe(world):
    """
    Windows loading a 64-bit version.dll in place of its own stops the game
    starting at all. With an x86 build in the drop that's the one, and on its
    own it's said
    """
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    beside = game / "alba_data" / "prog" / "win" / "bin"
    x64 = pe32(machine=0x8664)

    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Hook-1-0.zip", {
        "x64/version.dll": x64, "x64/hook.ini": b"a=64",
        "x86/version.dll": pe32(), "x86/hook.ini": b"a=32"})))

    assert done["ok"] and (beside / "version.dll").read_bytes() == pe32(), done
    assert (beside / "hook.ini").read_bytes() == b"a=32"
    assert "x64/version.dll and the 1 file with it, built for 64-bit, which these 32-bit games " \
        "can't load" in done["message"], done["message"]

    alone = bridge.install_mod("ff13-2", str(zipped(tmp, "Hook64.zip", {"dinput8.dll": x64})))
    assert not alone["ok"] and "dinput8.dll is built for 64-bit" in alone["error"], alone
    assert not (beside / "dinput8.dll").exists()


def test_game_files_beside_a_hook_arent_its_data(world):
    """files the archives don't list are still game files, and beside the exe they do nothing"""
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    beside = game / "alba_data" / "prog" / "win" / "bin"

    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Hooked Costume.zip", {
        "version.dll": pe32(), "Costume/chr/pc/c999/bin/c999.win32.imgb": b"x" * 3000,
        "Costume/chr/pc/c999/bin/c999.win32.trb": b"t" * 900})))

    assert not (beside / "Costume").exists(), done
    assert "2 files the game doesn't have" in done["message"], done


def test_a_hook_with_another_games_files_is_sent_to_that_game(tmp_path, monkeypatch):
    """without the .dll it was; with it, the .dll went into XIII-2 on its own"""
    from tests.test_loader_mods import pe32

    library, game, paths = library_with(tmp_path, xiii=True)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    beside = game / "alba_data" / "prog" / "win" / "bin"

    done = bridge.install_mod("ff13-2", str(zipped(tmp_path, "Light Coat Hook.zip", {
        "version.dll": pe32(), f"Light Coat/{C201}": b"l" * 3000})))

    assert not done["ok"] and "is for FINAL FANTASY XIII. Switch" in done["error"], done
    assert not (beside / "version.dll").exists()


def test_a_loader_that_cant_be_read_doesnt_unsay_what_went_in(world):
    """one bad byte in its .dds: the game file is in, recorded, and the message says so"""
    from tests.test_game_files import read
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    drop = zipped(tmp, "Hooked Dress.zip", {f"Dress/{C108}": b"dress" * 2000,
                                            "version.dll": pe32(),
                                            "hd_textures/a.dds": b"DDS " + b"Q" * 200})
    raw = bytearray(drop.read_bytes())
    raw[raw.find(b"DDS " + b"Q" * 200) + 10] = ord("Z")
    drop.write_bytes(bytes(raw))

    done = bridge.install_mod("ff13-2", str(drop))

    assert done["ok"] and read(game, C108) == b"dress" * 2000, done
    assert "a.dds couldn't be read out of Hooked Dress" in done["message"], done
    assert [r["name"] for r in gf.records(game / ".patcher-undo")] == ["Hooked Dress"]
    assert not (game / "alba_data" / "prog" / "win" / "bin" / "version.dll").exists()


def test_a_zip_in_a_folder_that_windows_blocks_is_said_to_be_blocked(world, monkeypatch):
    """is_zipfile() took the refusal for a zip that isn't one: 'the download may be damaged'"""
    bridge, game, tmp = world
    drop = folder(tmp, "Red Rose", {"PatchData.zip": b"PK\x03\x04", "readme.txt": b"r"})
    real = Path.open

    def flagged(self, *args, **kwargs):
        if self.name == "PatchData.zip":
            error = OSError(22, "Operation did not complete successfully because the file "
                                "contains a virus")
            error.winerror = 225
            raise error
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flagged)
    done = bridge.install_mod("ff13-2", str(drop))

    assert not done["ok"] and "Windows Security blocked PatchData.zip" in done["error"], done
    assert "damaged" not in done["error"]


def test_loader_mods_are_listed_when_novafixs_folder_isnt_usable(world, monkeypatch):
    """or there'd be no seeing one to take it off"""
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    assert bridge.install_mod("ff13-2", str(zipped(tmp, "HD Fonts and GUI.zip", {
        "version.dll": pe32(), "hd_textures/a.dds": b"d"})))["ok"]
    monkeypatch.setattr(UiBridge, "addons_dir",
                        lambda self, gid, create=True: {"ok": False,
                                                        "error": "addons isn't writable"})

    listed = bridge.list_addons("ff13-2")

    assert listed["ok"] and [m["name"] for m in listed["mods"]] == ["HD Fonts and GUI"], listed


def test_the_hd_project_zipped_inside_a_zip_says_so(world, monkeypatch):
    import shutil

    from tests.hd2_world import make_mod

    bridge, game, tmp = world
    mod = make_mod(tmp, monkeypatch)
    inner = shutil.make_archive(str(tmp / "FF XIII-2 HD"), "zip", root_dir=mod.parent,
                                base_dir=mod.name)
    outer = tmp / "FF XIII-2 HD (wrapped).zip"
    with zipfile.ZipFile(outer, "w") as archive:
        archive.write(inner, "FF XIII-2 HD.zip")

    done = bridge.install_mod("ff13-2", str(outer))

    assert not done["ok"] and "FF XIII-2 HD.zip inside FF XIII-2 HD (wrapped).zip is the HD " \
        "Project" in done["error"], done
    assert not gf.records(game / ".patcher-undo")


def threat(name):
    error = OSError(22, "Operation did not complete successfully because the file contains "
                        "a virus")
    error.winerror, error.filename = 225, name
    return error


def test_a_blocked_file_beside_a_loader_is_said_to_be_blocked(world, monkeypatch):
    """the drop's reader says so itself, and that isn't a damaged download"""
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    drop = folder(tmp, "ASI Hook", {"version.dll": pe32(), "plugin.asi": b"MZ plugin",
                                    "plugin.ini": b"a=1"})
    real = Path.read_bytes

    def flagged(self):
        if self.name == "plugin.asi" and self.parent == drop:
            raise threat(str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flagged)
    done = bridge.install_mod("ff13-2", str(drop))

    assert not done["ok"] and "Windows Security blocked plugin.asi" in done["error"], done
    assert "damaged" not in done["error"]


def test_an_x86_build_windows_blocked_isnt_said_to_be_missing(world, monkeypatch):
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    drop = folder(tmp, "Hook", {"x86/version.dll": pe32(),
                                "x64/version.dll": pe32(machine=0x8664)})
    real = Path.read_bytes

    def flagged(self):
        if self.name == "version.dll" and self.parent == drop / "x86":
            raise threat(str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flagged)
    done = bridge.install_mod("ff13-2", str(drop))

    assert not done["ok"] and "Windows Security blocked version.dll" in done["error"], done
    assert "Get its 32-bit (x86) build" not in done["error"]


def test_a_64_bit_builds_own_files_stay_out_with_it(world):
    """
    Ultimate ASI Loader's own layout, an x86 and an x64 folder each with its
    plugins and data: the x64 one's aren't NovaFix add-ons or art to pack
    """
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    beside = game / "alba_data" / "prog" / "win" / "bin"
    x64 = pe32(machine=0x8664)

    done = bridge.install_mod("ff13-2", str(zipped(tmp, "UAL Plugin.zip", {
        "x86/dinput8.dll": pe32(), "x86/scripts/plugin.asi": pe32() + b"asi32",
        "x86/scripts/plugin.ini": b"a=32", "x86/hd_textures/font.dds": b"DDS 32",
        "x64/dinput8.dll": x64, "x64/scripts/plugin.asi": x64 + b"asi64",
        "x64/scripts/plugin.ini": b"a=64", "x64/hd_textures/font.dds": b"DDS 64"})))

    assert done["ok"] and (beside / "scripts" / "plugin.asi").read_bytes() == pe32() + b"asi32"
    assert (beside / "hd_textures" / "font.dds").read_bytes() == b"DDS 32"
    assert not done["rejected"] and not (beside / "addons").exists(), done
    assert "x64/dinput8.dll and the 3 files with it, built for 64-bit" in done["message"], done
    assert "need packing" not in done["message"] and "No compatible" not in done["message"]


def test_the_hd_project_zip_in_a_dropped_folder_is_opened_as_if_dropped(world, monkeypatch):
    """it's a file on disk, so it goes the way it would have dropped on its own"""
    import shutil

    from tests.hd2_world import make_mod

    bridge, game, tmp = world
    mod = make_mod(tmp, monkeypatch)
    holder = tmp / "Downloaded mods"
    holder.mkdir()
    inner = shutil.make_archive(str(holder / "FF XIII-2 HD"), "zip", root_dir=mod.parent,
                                base_dir=mod.name)

    done = bridge.install_mod("ff13-2", str(holder))

    assert done == {"ok": False, "kind": "maybe-hdproject", "path": inner}, done


def test_a_zip_stored_whole_in_a_zip_is_read_where_it_sits(world, monkeypatch):
    """the usual zip of a zip: nothing is copied out, so the HD Project inside says so at once"""
    import shutil

    from tests.hd2_world import make_mod

    bridge, game, tmp = world
    real = Path.open

    def no_copies(self, mode="r", *args, **kwargs):
        assert not ("w" in mode and self.parent.name.startswith("ffxiii-mod-")), self
        return real(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_copies)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(f"PatchData/{C108}", b"r" * 3000)
    stored = zipped(tmp, "Red Rose.zip", {"PatchData.zip": payload.getvalue()})
    assert bridge.install_mod("ff13-2", str(stored))["ok"]

    mod = make_mod(tmp, monkeypatch)
    inner = shutil.make_archive(str(tmp / "FF XIII-2 HD"), "zip", root_dir=mod.parent,
                                base_dir=mod.name)
    outer = tmp / "FF XIII-2 HD (wrapped).zip"
    with zipfile.ZipFile(outer, "w") as archive:
        archive.write(inner, "FF XIII-2 HD.zip")
    done = bridge.install_mod("ff13-2", str(outer))
    assert not done["ok"] and "FF XIII-2 HD.zip inside FF XIII-2 HD (wrapped).zip is the HD " \
        "Project" in done["error"], done


def deflated(tmp, name, inner_name, inner: bytes) -> Path:
    path = tmp / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(inner_name, inner)
    return path


def test_a_compressed_zip_in_a_zip_with_no_room_to_open_names_the_drive(world, monkeypatch):
    """it's copied out beside the drop, so that's the drive to free, not the game's"""
    import errno

    bridge, game, tmp = world
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(f"PatchData/{C108}", b"r" * 3000)
    downloads = tmp / "Downloads"
    downloads.mkdir()
    drop = deflated(downloads, "Red Rose.zip", "PatchData.zip", payload.getvalue())

    monkeypatch.setattr("patcher.download.free_space", lambda path: 1024)
    tight = bridge.install_mod("ff13-2", str(drop))
    assert not tight["ok"] and "PatchData.zip inside Red Rose.zip" in tight["error"], tight
    assert "free to open it in" in tight["error"] and str(game) not in tight["error"]

    monkeypatch.setattr("patcher.download.free_space", lambda path: 1 << 40)
    real = Path.open

    class Full:
        def __init__(self, handle):
            self.handle = handle

        def write(self, data):
            raise OSError(errno.ENOSPC, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    def opener(self, mode="r", *args, **kwargs):
        handle = real(self, mode, *args, **kwargs)
        return Full(handle) if "w" in mode and self.parent.name.startswith("ffxiii-mod-") \
            else handle

    monkeypatch.setattr(Path, "open", opener)
    full = bridge.install_mod("ff13-2", str(drop))
    assert not full["ok"] and "is full" in full["error"] and str(downloads) in full["error"], full
    assert str(game) not in full["error"]


def blocking(monkeypatch, path):
    real = Path.read_bytes

    def flagged(self):
        if self == path:
            raise threat(str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flagged)


UAL = {"x86/dinput8.dll": None, "x86/scripts/plugin.asi": b"asi32",
       "x86/scripts/plugin.ini": b"a=32", "x86/hd_textures/font.dds": b"DDS 32",
       "x64/dinput8.dll": None, "x64/scripts/plugin.asi": b"asi64",
       "x64/scripts/plugin.ini": b"a=64", "x64/hd_textures/font.dds": b"DDS 64"}


def ual(tmp):
    """Ultimate ASI Loader's layout as a folder, each build with its plugin and data"""
    from tests.test_loader_mods import pe32

    x86, x64 = pe32(), pe32(machine=0x8664)
    files = {member: (x86 if member.startswith("x86") else x64) + (data or b"")
             if member.endswith((".dll", ".asi")) else data for member, data in UAL.items()}
    return folder(tmp, "UAL Plugin", files)


def test_a_blocked_x86_build_is_said_as_a_block_and_its_files_stay_out(world, monkeypatch):
    """its plugin isn't NovaFix's and its textures aren't art to pack, blocked or not"""
    bridge, game, tmp = world
    addons = []
    monkeypatch.setattr(UiBridge, "install_addon",
                        lambda self, gid, path: addons.append(path) or {"ok": True})
    drop = ual(tmp)
    blocking(monkeypatch, drop / "x86" / "dinput8.dll")

    done = bridge.install_mod("ff13-2", str(drop))

    assert not done["ok"] and "Windows Security blocked dinput8.dll" in done["error"], done
    assert not addons, addons


def test_a_blocked_x64_builds_files_stay_out_with_it(world, monkeypatch):
    """antivirus flags proxies whatever they're built for"""
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    beside = game / "alba_data" / "prog" / "win" / "bin"
    drop = ual(tmp)
    blocking(monkeypatch, drop / "x64" / "dinput8.dll")

    done = bridge.install_mod("ff13-2", str(drop))

    assert done["ok"] and (beside / "scripts" / "plugin.asi").read_bytes() == pe32() + b"asi32"
    assert not done["rejected"] and not (beside / "addons").exists(), done
    assert "need packing" not in done["message"], done["message"]
    assert "dinput8.dll, which Windows Security blocked as a threat, and the 3 files that go " \
        "with it" in done["message"], done["message"]


def test_a_small_zip_in_a_zip_opens_on_a_nearly_full_drive(world, monkeypatch):
    """the room asked for is what it unpacks to and a little, not a fixed slab"""
    from tests.test_game_files import read

    bridge, game, tmp = world
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(f"PatchData/{C108}", b"r" * 3000)
    drop = deflated(tmp, "Red Rose.zip", "PatchData.zip", payload.getvalue())
    # tight only where it's opened, beside the drop
    monkeypatch.setattr("patcher.download.free_space",
                        lambda path: 20 << 20 if "ffxiii-mod-" in str(path) else 1 << 40)

    done = bridge.install_mod("ff13-2", str(drop))

    assert done["ok"] and read(game, C108) == b"r" * 3000, done


def test_two_mods_that_would_read_the_same_keep_their_whole_names(world):
    """one HD Fonts for NovaFix and one standalone, side by side, looked like one listed twice"""
    bridge, game, tmp = world
    for name, path in (("(STANDALONE VERSION) HD Fonts and GUI - Base mod", C108),
                       ("(NOVA VERSION) HD Fonts and GUI - Base mod",
                        "chr/pc/c159/bin/c159.win32.imgb")):
        assert bridge.install_mod("ff13-2", str(folder(tmp, name, {path: b"a" * 4000})))["ok"]
    shown = sorted(m["title"] for m in bridge.list_addons("ff13-2")["mods"])
    assert shown == ["(NOVA VERSION) HD Fonts and GUI - Base mod",
                     "(STANDALONE VERSION) HD Fonts and GUI - Base mod"]


def test_a_drop_is_named_as_soon_as_its_read(world):
    bridge, game, tmp = world
    answer = bridge.identify_drop(str(zipped(tmp, "Serah Dress-55-1-2-1699999999.zip",
                                             {f"Serah Dress/{C108}": b"x"})), unpack=False)
    assert answer["kind"] == "archive" and answer["title"] == "Serah Dress"


def test_a_7z_inside_the_drop_is_unpacked_and_its_files_go_in(world, monkeypatch, tmp_path):
    """
    one the drop carries inside it used to be "unpack it yourself first". It's
    unpacked beside the drop the way a dropped one is, by Windows' own tar
    """
    import shutil
    import subprocess

    tar, seven = shutil.which("bsdtar"), shutil.which("7z") or shutil.which("7za")
    if not tar or not seven:
        pytest.skip("needs bsdtar and 7-Zip to make and read a .7z")
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: tar if name == "tar" else real(name))
    bridge, game, tmp = world
    before = vanilla(game)
    inner = tmp / "inner"
    (inner / C108).parent.mkdir(parents=True)
    (inner / C108).write_bytes(b"seven" * 2000)
    subprocess.run([seven, "a", str(tmp / "Dress.7z"), "."], cwd=inner, check=True,
                   capture_output=True)
    drop = zipped(tmp, "Dress Pack-91-1-0-1760000000.zip",
                  {"Dress.7z": (tmp / "Dress.7z").read_bytes(), "readme.txt": b"hi"})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    assert "1 game file into both voice sets" in done["message"]
    # nothing of the unpack is left beside the drop
    assert sorted(p.name for p in tmp.iterdir() if p.name.startswith("ffxiii-mod-")) == []
    assert bridge.remove_addon("ff13-2", done["modName"], "files")["ok"]
    assert vanilla(game) == before


@pytest.mark.parametrize("markers, off", [
    ((b"FF13Fix.ini", b"Final Fantasy XIII"), True),
    (("FF13Fix.ini".encode("utf-16-le"), b"FF13Fix.log"), True),
    ((b"OneTweakNG", b"FF13Fix.log", b"ffxiiiimg.exe"), True),
    # one marker alone is too easy to hit by chance, NovaFix's rule says
    ((b"FF13Fix.ini",), False),
    ((b"made with love, not FF13Fix",), False)])
def test_a_dll_built_on_ff13fix_says_novafix_switches_itself_off_for_it(world, markers, off):
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    dll = pe32() + b"\0".join(markers) + b"\0"
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Old Fix.zip", {"winmm.dll": dll})))
    assert done["ok"], done
    said = "NovaFix turns its own fixes off while winmm.dll is in"
    row = next(m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "loader")
    assert (said in done["message"], said in row["warn"]) == (off, off), (done, row)
    assert done.get("whole", True) is not off


def test_ff13fix_dropped_is_turned_away_for_novafix(world):
    """the two can't run together: NovaFix switches itself off while FF13Fix is loaded"""
    from tests.test_loader_mods import pe32

    bridge, game, tmp = world
    dll = pe32() + b"\0FF13Fix.ini\0FF13Fix.log\0OneTweakNG\0"
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "FF13Fix 1.6.4.zip", {
        "d3d9.dll": dll, "FF13Fix.ini": b"[Options]\nPresentationInterval = 1\n"})))
    assert not done["ok"] and "it's FF13Fix, which NovaFix replaces" in done["error"], done
    assert not [m for m in bridge.list_addons("ff13-2")["mods"] if m["kind"] == "loader"]
