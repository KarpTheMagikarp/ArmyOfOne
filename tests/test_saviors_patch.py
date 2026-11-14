"""
Savior's Patch on Lightning Returns, which is the user's own download now.

Worth getting points at its page, the download goes in through the drawer like
any mod that loads itself, and the row, the Settings drawer and Remove all go
by what's beside LRFF13.exe. An install an earlier version's Apply put in is
still listed and still comes off.
"""

import json
from pathlib import Path

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from patcher.manifest import load_manifest
from patcher.novafix_config import SAVIORS_SETTINGS
from patcher.state import record_applied
from tests.helpers import make_export_pe
from tests.test_mod_drops import zipped
from tests.test_patchdata import make_lr

REPO = Path(__file__).resolve().parent.parent
MANIFESTS = REPO / "manifests"
# a dinput8.dll that reads SaviorsPatch.ini, which is what the drawer knows it by
SAVIORS = make_export_pe(("DirectInput8Create",), machine=0x14C,
                         filler=b"\0SaviorsPatch.ini\0SaviorsPatch.log\0")
THEIRS = {s.label for s in SAVIORS_SETTINGS}


@pytest.fixture
def lr(tmp_path, monkeypatch):
    library = tmp_path / "steam"
    game = make_lr(library / "steamapps" / "common")
    (library / "steamapps" / "appmanifest_345350.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"345350"\n\t"installdir"\t\t"LR"\n}\n')
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths={"ffxiii3": MANIFESTS / "ffxiii3.json"},
                      steam_library_paths=[library])
    return bridge, game, tmp_path


def settings_shown(bridge) -> set:
    return {s["label"] for s in bridge.novafix_settings("lr", gaps=False)["settings"]} & THEIRS


def test_it_is_worth_getting_and_nothing_of_it_ships():
    games = json.loads((REPO / "ui" / "jobs.json").read_text(encoding="utf-8"))["games"]
    row = next(j for g in games if g["id"] == "lr" for j in g["jobs"] if j["id"] == "saviors")
    assert row["kind"] == "extra"
    assert row["page"].startswith(
        "https://www.nexusmods.com/lightningreturnsfinalfantasy13/mods/36")
    patch = next(p for p in load_manifest(MANIFESTS / "ffxiii3.json").patches
                 if p.ui_job_id == "saviors")
    assert patch.type == "nexus" and not patch.default_enabled and patch.in_mods
    assert patch.expects == ["dinput8.dll"] and not patch.bundled_files
    assert not (REPO / "assets" / "mods" / "saviors-patch").exists()


@pytest.mark.parametrize("name", ["Savior's Patch v1.1.3-36-1-1-3-1717171717.zip",
                                  "SaviorsPatch_v1.1.3-36-1-1-3-1717171717.zip",
                                  "Saviour's Patch-36-1-1-3-1717171717.zip",
                                  "dinput8-36-1-1-3-1717171717.zip"],
                         ids=["named as on its page", "named the way files often are",
                              "spelled another way", "named for its dll"])
def test_its_download_goes_in_beside_the_exe_and_its_row_says_so(lr, name):
    bridge, game, tmp_path = lr
    drop = zipped(tmp_path, name, {"dinput8.dll": SAVIORS,
                                   "Readme.txt": b"put it beside LRFF13.exe"})
    assert settings_shown(bridge) == set(), "its rows before it's in"

    done = bridge.install_mod("lr", str(drop))

    assert done["ok"], done
    assert (game / "dinput8.dll").read_bytes() == SAVIORS
    row = next(m for m in bridge.loader_mods("lr") if m["files"] == ["dinput8.dll"])
    assert row["jobId"] == "saviors", row
    assert settings_shown(bridge) == THEIRS

    assert bridge.remove_addon("lr", row["name"])["ok"]
    assert not (game / "dinput8.dll").exists()
    assert settings_shown(bridge) == set()


@pytest.mark.parametrize("name", ["LayeredFS-12-1-0-1717171717.zip", "dinput8.zip"],
                         ids=["another mod's download", "no name to go by"])
def test_another_dinput8_isnt_taken_for_it(lr, name):
    bridge, game, tmp_path = lr
    layered = make_export_pe(("DirectInput8Create",), machine=0x14C,
                             filler=b"\0layeredLS.ini\0mod_data\0")
    assert bridge.install_mod("lr", str(zipped(tmp_path, name, {"dinput8.dll": layered})))["ok"]
    row = next(m for m in bridge.loader_mods("lr") if m["files"] == ["dinput8.dll"])
    assert "jobId" not in row, row
    assert settings_shown(bridge) == set()


def test_an_install_an_earlier_apply_put_in_is_listed_and_comes_off(lr):
    bridge, game, tmp_path = lr
    (game / "dinput8.dll").write_bytes(SAVIORS)
    (game / "SaviorsPatch.log").write_text("streaming: ok\n")
    record_applied(tmp_path / ".state" / "ffxiii3.json", "install_saviors_patch", ["dinput8.dll"],
                   {"digests": {}})

    rows = bridge.extra_rows("lr")
    assert [(r["name"], r["about"], r["managed"]) for r in rows] == \
        [("Savior's Patch", "Went in with Apply", True)], rows

    done = bridge.remove_extra("lr", "Savior's Patch")

    assert done["ok"], done
    assert not (game / "dinput8.dll").exists()
    assert not (game / "SaviorsPatch.log").exists(), "its log outlived it"
    assert bridge.extra_rows("lr") == []


def test_a_newer_one_over_an_earlier_apply_says_to_remove_that_first(lr):
    """it was refused as a dinput8.dll that isn't from this app, which it is"""
    bridge, game, tmp_path = lr
    (game / "dinput8.dll").write_bytes(SAVIORS)
    record_applied(tmp_path / ".state" / "ffxiii3.json", "install_saviors_patch", ["dinput8.dll"],
                   {"digests": {}})
    newer = make_export_pe(("DirectInput8Create",), machine=0x14C,
                           filler=b"\0SaviorsPatch.ini\0SaviorsPatch.log\0v1.2.0\0")
    drop = zipped(tmp_path, "SaviorsPatch_v1.2.0-36-1-2-0-1727171717.zip", {"dinput8.dll": newer})

    done = bridge.install_mod("lr", str(drop))

    assert not done["ok"]
    assert "Remove Savior's Patch first" in done["error"], done["error"]
    assert (game / "dinput8.dll").read_bytes() == SAVIORS

    assert bridge.remove_extra("lr", "Savior's Patch")["ok"]
    assert bridge.install_mod("lr", str(drop))["ok"]
    assert (game / "dinput8.dll").read_bytes() == newer
