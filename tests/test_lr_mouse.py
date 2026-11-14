"""
Lightning Returns' mouse support: our own NovaFix add-on, a required row, and
an ini that's the player's the moment they touch it.
"""
import json
import subprocess
from pathlib import Path

import pytest

from patcher.engine import PatchStatus, run_patches
from patcher.manifest import load_manifest
from patcher.revert import revert_patch
from patcher.state import applied_recorder, record_reader

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets" / "mods" / "lr-mouse"


def lr():
    return load_manifest(ROOT / "manifests" / "ffxiii3.json")


def mouse(m):
    return next(p for p in m.patches if p.id == "install_lr_mouse")


def apply(root, m, state):
    # NovaFix counts as in already: this is about where the add-on goes, and
    # NovaFix's own install has tests of its own
    results = run_patches(root, m, {"install_lr_mouse"},
                          already_applied_check=lambda pid: pid == "install_novafix",
                          on_applied=applied_recorder(root, m, state),
                          recorded=record_reader(state))
    return next(r for r in results if r.patch_id == "install_lr_mouse")


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "LIGHTNING RETURNS FINAL FANTASY XIII"
    root.mkdir()
    (root / "LRFF13.exe").write_bytes(b"MZ")
    return root


def test_it_is_a_patch_to_be_applied_called_mouse_support():
    jobs = json.loads((ROOT / "ui" / "jobs.json").read_text(encoding="utf-8"))
    job = next(j for g in jobs["games"] if g["id"] == "lr" for j in g["jobs"] if j["id"] == "lrmouse")
    assert job["name"] == "Mouse Support"
    # required is what puts it under Patches to be applied rather than Extras
    assert job["kind"] == "required"

    patch = mouse(lr())
    assert patch.label == "Mouse Support"
    assert patch.default_enabled
    assert patch.ui_job_id == "lrmouse"
    assert patch.requires == ["install_novafix"]


def test_it_goes_into_whichever_addon_folder_novafix_is_reading(game, tmp_path):
    """
    NovaFix scans the first of addons/extra/extras/mods/dll that exists and only
    that one, so an install that already has mods/ has to get it there
    """
    (game / "mods").mkdir()
    result = apply(game, lr(), tmp_path / "state.json")

    assert result.status == PatchStatus.APPLIED, result.detail
    assert (game / "mods" / "lr-mouse" / "lr_mouse.asi").is_file()
    assert (game / "mods" / "lr-mouse" / "lr-mouse.ini").is_file()
    assert (game / "mods" / "lr-mouse" / "LICENSE-MinHook.txt").is_file()
    assert not (game / "addons").exists()


def test_the_players_settings_survive_another_apply_and_a_revert(game, tmp_path):
    m, state = lr(), tmp_path / "state.json"
    assert apply(game, m, state).status == PatchStatus.APPLIED
    home = game / "addons" / "lr-mouse"
    ini = home / "lr-mouse.ini"
    ini.write_text(ini.read_text(encoding="utf-8").replace("Sensitivity=1.0", "Sensitivity=2.5"))
    (home / "lr-mouse.log").write_text("ready\n")

    assert apply(game, m, state).status == PatchStatus.APPLIED
    assert "Sensitivity=2.5" in ini.read_text(encoding="utf-8")

    undone = revert_patch(game, m, mouse(m), state)

    assert undone.ok, undone.detail
    assert not (home / "lr_mouse.asi").exists()
    assert not (home / "lr-mouse.log").exists()
    assert "Sensitivity=2.5" in ini.read_text(encoding="utf-8")


def test_the_shipped_ini_says_what_the_addon_reads():
    """
    the add-on reads these exact keys with these defaults. An ini that drifts
    from it still loads, it just quietly does nothing when somebody edits it
    """
    text = (ASSETS / "lr-mouse.ini").read_text(encoding="utf-8")
    source = (ROOT / "addon-src" / "lr-mouse" / "lr_mouse.cpp").read_text(encoding="utf-8")
    for key, default in (("Enabled", "1"), ("Sensitivity", "1.0"), ("InvertX", "0"), ("InvertY", "0"),
                         ("Menus", "1")):
        assert f"\n{key}={default}\n" in text
        assert f'L"{key}"' in source
    assert "[Mouse]" in text and 'L"Mouse"' in source


def test_the_addon_is_32_bit_and_borrows_the_games_dinput8():
    """
    NovaFix skips anything that isn't i386 without a word, and importing
    dinput8 would make a missing or odd one stop the add-on loading at all
    """
    data = (ASSETS / "lr_mouse.asi").read_bytes()
    pe = int.from_bytes(data[0x3C:0x40], "little")
    assert data[:2] == b"MZ" and data[pe:pe + 4] == b"PE\0\0"
    assert int.from_bytes(data[pe + 4:pe + 6], "little") == 0x14C

    try:
        dump = subprocess.run(["i686-w64-mingw32-objdump", "-p", str(ASSETS / "lr_mouse.asi")],
                              capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no mingw objdump to read the import table with")
    imports = {line.split(":", 1)[1].strip().lower() for line in dump.splitlines() if "DLL Name:" in line}
    assert "dinput8.dll" not in imports
    assert imports <= {"kernel32.dll", "user32.dll", "msvcrt.dll"}
