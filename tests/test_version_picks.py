"""
A version picked off the drawer has to find itself again on the way back in,
whatever its menu printed round it, and a pick a .bat used up never gets
asked of the drop's own files, which only buried why the .bat put nothing in
"""

import pytest

from patcher.mods import game_files
from tests.test_sandbox import GAME, MENU, MOVIE, drawer, drop

COPY = rf"""@echo off
set "game={GAME}"
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""
BUILDS = r"""@echo off
"%~dp0SupportFiles\FFXIII_ModelsHD.exe" Data\recipe.txt
"""
LG = ["- LG Catalog Drop Rates", "- LG Chocobo Speed", "- LG Half CP Cost"]


def grinding(tmp_path, script, nested=True):
    """Less Grinding's shape: a folder per version with its own installer in it, or a .bat each"""
    if nested:
        files = {f"{n}/Install.bat": script for n in LG}
        files.update({f"{n}/op.win32.wmp": n.encode() * 200 for n in LG})
    else:
        files = {f"{n}.bat": script.replace("%~dp0op", f"%~dp0{i}\\op") for i, n in enumerate(LG)}
        files.update({f"{i}/op.win32.wmp": n.encode() * 200 for i, n in enumerate(LG)})
    return drop(tmp_path, "Less Grinding 73 1.3", files)


@pytest.mark.parametrize("nested", [True, False])
def test_a_picked_bat_that_fails_says_why_not_that_its_no_version(tmp_path, monkeypatch, nested):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = grinding(tmp_path, BUILDS, nested)
    asked = bridge.install_mod("ff13", str(folder))
    assert asked["versions"] == LG
    done = bridge.install_mod("ff13", str(folder), version=LG[0])
    assert not done["ok"]
    assert "isn't one of its versions" not in done["error"]
    assert "SupportFiles" in done["error"] and done["detail"], done


@pytest.mark.parametrize("pick", [LG[0], LG[0] + "\r", f"  {LG[0]}\r\n", "- lg catalog  drop rates",
                                  "LG Catalog Drop Rates", "– LG Catalog Drop Rates"])
def test_a_pick_that_came_back_scuffed_still_installs(tmp_path, monkeypatch, pick):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = grinding(tmp_path, COPY)
    done = bridge.install_mod("ff13", str(folder), version=pick)
    assert done["ok"], done
    assert (game / MOVIE).read_bytes() == LG[0].encode() * 200


def test_a_menus_label_is_matched_tidied(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Vanille Outfits", {
        "install.bat": MENU.replace("FINAL FANTASY XIII", "FFXIII"), "ff13tool.exe": b"MZ",
        "naturey/chr/pc/c004/bin/c004.win32.trb": b"naturey " * 100,
        "robe/chr/pc/c004/bin/c004.win32.trb": b"robe " * 100})
    done = bridge.install_mod("ff13", str(folder), version=" the naturey OUTFIT.\r")
    assert done["ok"], done


def test_picked_only_matches_one_name():
    names = ["- LG Catalog Drop Rates", "- LG Chocobo Speed"]
    assert game_files.picked("- LG Chocobo Speed", names) == "- LG Chocobo Speed"
    assert game_files.picked("lg chocobo speed\r", names) == "- LG Chocobo Speed"
    assert game_files.picked("LG Shroud Drop Rate", names) is None
    assert game_files.picked(None, names) is None
    # two that read the same tidied are a real choice
    assert game_files.picked("Option A", ["- Option A", "Option A."]) is None
