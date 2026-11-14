"""
A download of separate tweaks, one .bat each, Gameplay Tweaks' Mod_RunSpeed.bat
beside its Mod_Adamantite.bat: each goes in as a mod of its own, so the second
one picked goes in beside the first instead of reading as it in already
"""

from patcher.mods import sandbox
from tests.helpers import pack_plain
from tests.test_sandbox import drawer, drop
from tests.test_wdbmod import table

RUN, DROP = "db/resident/charafamily.wdb", "db/resident/bt_chara_spec.wdb"
CALL = "@echo OFF\ncall FFXIIIwdbLoad {}\n"


def pack(tmp_path):
    return drop(tmp_path, "Gameplay Tweaks", {
        "Mod_RunSpeed.bat": CALL.format("Run.txt"),
        "Mod_Adamantite.bat": CALL.format("Ada.txt"),
        "Run.txt": "db\\resident\\charafamily.wdb\r\nrun_a,1,i,115\r\n",
        "Ada.txt": "db\\resident\\bt_chara_spec.wdb\r\nm186a,2,i,300\r\n"})


def world(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    sys_dir = game / "white_data" / "sys"
    pack_plain(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
               [(RUN, table(["run_a"], 4)), (DROP, table(["m186a"], 4))])
    return game, bridge


def test_each_tweak_goes_in_as_its_own_mod_beside_the_others(tmp_path, monkeypatch):
    game, bridge = world(tmp_path, monkeypatch)
    folder = pack(tmp_path)
    asked = bridge.install_mod("ff13", str(folder))
    assert asked["kind"] == "versions" and asked["tweaks"], asked
    first = bridge.install_mod("ff13", str(folder), version="Mod_RunSpeed")
    assert first["ok"] and first["modName"] == "Gameplay Tweaks (RunSpeed)", first
    # the list comes back for the next pick, the one in ticked
    assert first["again"]["done"] == ["Mod_RunSpeed"], first
    second = bridge.install_mod("ff13", str(folder), version="Mod_Adamantite")
    assert second["ok"] and not second["matchedExisting"], second
    assert sorted(second["again"]["done"]) == ["Mod_Adamantite", "Mod_RunSpeed"]
    assert sorted(m["name"] for m in bridge.file_mods("ff13")) == [
        "Gameplay Tweaks (Adamantite)", "Gameplay Tweaks (RunSpeed)"]


def test_in_already_is_only_said_of_the_tweak_thats_in(tmp_path, monkeypatch):
    game, bridge = world(tmp_path, monkeypatch)
    folder = pack(tmp_path)
    assert bridge.install_mod("ff13", str(folder), version="Mod_RunSpeed")["ok"]
    again = bridge.install_mod("ff13", str(folder), version="Mod_RunSpeed")
    assert again["summary"] == "Gameplay Tweaks (RunSpeed) is in already.", again


def test_one_installer_per_folder_is_still_versions_of_one_mod(tmp_path):
    folder = drop(tmp_path, "Outfit", {"English/Install.bat": "@echo off\n",
                                       "Japanese/Install.bat": "@echo off\n"})
    from patcher.mods import game_files
    with game_files.Source(folder) as source:
        assert sandbox.tweaks(source, source.members(), "white_data") == []


def test_a_tweaks_name_is_what_it_does():
    assert sandbox.tweak_name("Mod_Tier3Weapon125") == "Tier3Weapon125"
    assert sandbox.tweak_name("- LG Catalog Drop Rates") == "LG Catalog Drop Rates"
    assert sandbox.tweak_name("Mod_") == "Tweak"
