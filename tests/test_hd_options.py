"""
The category toggles, and the promise underneath them.

The mod's installer asks what to install with a menu tree, because cmd can only
ask one question at a time. Underneath, PROCESSLINE dispatches install and
uninstall through the same table with one letter swapped, so what it is really
asking is seven independent on/off choices.

The load bearing test in here is the last one. We cannot do this mod's geometry
work, so nothing in this path may touch the game, and a row that looks like it
installs when it cannot is the one outcome worth failing the build over.
"""

import json
import sys
import re
from pathlib import Path

import pytest

from patcher.bridge import UiBridge
from patcher.manifest import load_manifest
from patcher.mods.hd_recipe import detect, installer_steps

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data" / "hd"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    import patcher.bridge as module
    monkeypatch.setattr(module, "state_path_for", lambda gid: tmp_path / f"{gid}.json")
    paths = {p.stem: p for p in sorted((REPO / "manifests").glob("*.json"))}
    return UiBridge(manifest_paths=paths, steam_library_paths=[])


def categories():
    manifest = load_manifest(REPO / "manifests" / "ffxiii.json")
    return next(p.categories for p in manifest.patches if p.ui_job_id == "hdproject")


def test_the_seven_categories_match_the_mods_own_menu():
    """
    the installer's category menu is A through G in this order. A letter that
    drifts sends the user to the wrong item, which is worse than no guidance.
    """
    letters = [c["menu"] for c in categories()]
    assert letters == ["A", "B", "C", "D", "E", "F", "G"]
    assert [c["id"] for c in categories()] == ["zones", "weapons", "eidolons",
                                               "npcs", "enemies", "misc", "pcs"]


def test_characters_are_off_by_default_and_flagged():
    """
    the mod's own Install All prints a warning about the character models and
    then installs them anyway with no prompt. That is the behaviour worth not
    copying: it is the mesh surgery half and it overwrites other mods.
    """
    pcs = next(c for c in categories() if c["id"] == "pcs")

    assert pcs["default"] is False
    assert pcs["mesh"] is True
    assert pcs["warn"]
    # everything else is on by default EXCEPT zones, which is off for a
    # different reason: it cannot be installed from here at all yet
    assert all(c["default"] for c in categories()
               if c["id"] not in {"pcs", "zones"})


def test_first_look_offers_everything_it_can_install(bridge):
    """
    seven categories offered, five ticked. Characters are off because they
    overwrite other mods, zones because the pipeline for them is not here.
    """
    state = bridge.hd_categories("ff13", "hdproject")

    assert len(state["categories"]) == 7
    assert "pcs" not in state["selected"]
    assert "zones" not in state["selected"]
    assert len(state["selected"]) == 5


def test_a_choice_survives_reopening_the_drawer(bridge):
    bridge.set_hd_categories("ff13", "hdproject", ["weapons", "pcs"])

    assert bridge.hd_categories("ff13", "hdproject")["selected"] == ["weapons", "pcs"]


def test_a_job_with_no_options_answers_calmly(bridge):
    """every other row is a plain on/off and the UI asks all of them"""
    state = bridge.hd_categories("ff13", "laa")

    assert state == {"categories": [], "selected": [], "installed": []}
    assert bridge.set_hd_categories("ff13", "laa", ["nonsense"])["ok"] is False


def test_nonsense_selections_are_dropped_rather_than_stored(bridge):
    result = bridge.set_hd_categories("ff13", "hdproject", ["weapons", "made_up", ""])

    assert result["selected"] == ["weapons"]


def test_install_all_is_only_offered_when_characters_are_wanted_too():
    """
    the mod's A includes the character models. Offering it for "everything
    except characters" would quietly install the one thing just declined.
    """
    every = {c["id"] for c in categories()}
    assert installer_steps(categories(), every) == ["A, to install everything"]

    without = every - {"pcs"}
    steps = installer_steps(categories(), without)
    assert not any(s.startswith("A,") for s in steps)
    assert len(steps) == 6


def test_steps_name_the_key_for_each_category():
    steps = installer_steps(categories(), {"zones", "npcs"})

    assert steps == ["C then A, to add Environmental Zones",
                     "C then D, to add NPCs"]


def test_unticking_something_installed_becomes_an_uninstall_step():
    """
    the whole reason for modelling state instead of verbs: the mod asks
    "install or uninstall", and the answer falls out of what changed
    """
    steps = installer_steps(categories(), {"zones"}, installed={"zones", "weapons"})

    assert steps == ["D then B, to remove Weapons"]


def test_no_change_means_no_steps():
    assert installer_steps(categories(), {"zones"}, installed={"zones"}) == []


def test_detect_finds_the_mod_however_it_was_unpacked(tmp_path):
    """the folder is called whatever the user unzipped it to"""
    home = tmp_path / "whatever they called it"
    (home / "Data").mkdir(parents=True)
    (home / "SupportFiles").mkdir()
    for name in ("LoadHDEidolons.txt", "models_sample.bin"):
        (home / "Data" / name).write_bytes((DATA / name).read_bytes())
    (home / "Data" / "FFXIII_General_Models.bin").write_bytes(b"x" * 8)
    (home / "SupportFiles" / "ff13tool.exe").write_bytes(b"MZ")

    assert detect(home) is not None
    assert detect(tmp_path) is not None, "one wrapper level should still match"
    assert Path(detect(home)["data"]).name == "Data"


@pytest.mark.parametrize("missing", ["Data", "SupportFiles", "container"])
def test_detect_needs_all_three_signs(tmp_path, missing):
    """
    any one of these alone is a folder that happens to look a bit like a mod,
    and a false positive here sends a drop somewhere it was not meant to go
    """
    home = tmp_path / "mod"
    (home / "Data").mkdir(parents=True)
    (home / "SupportFiles").mkdir()
    (home / "Data" / "LoadHDEidolons.txt").write_text("#:Eidolons\ns s101\n")
    (home / "Data" / "FFXIII_General_Models.bin").write_bytes(b"x" * 8)

    if missing == "Data":
        (home / "Data" / "LoadHDEidolons.txt").unlink()
    elif missing == "SupportFiles":
        (home / "SupportFiles").rmdir()
    else:
        (home / "Data" / "FFXIII_General_Models.bin").unlink()

    assert detect(home) is None


def test_identify_drop_does_not_claim_an_ordinary_archive(bridge, tmp_path):
    archive = tmp_path / "LeviathansTears.rar"
    archive.write_bytes(b"Rar!")

    assert bridge.identify_drop(str(archive))["kind"] == "archive"
    assert bridge.identify_drop(str(tmp_path / "gone.zip"))["kind"] == "missing"


def test_nothing_in_this_path_can_touch_the_game(bridge, tmp_path):
    """
    The one that matters. We cannot do this mod's geometry work, so recording a
    selection must stay a recording. If this ever starts writing, the row is
    lying about what pressing it does.
    """
    install = tmp_path / "FINAL FANTASY XIII"
    (install / "white_data" / "sys").mkdir(parents=True)
    archives = {}
    for name in ("filelist_scru.win32.bin", "white_scru.win32.bin"):
        target = install / "white_data" / "sys" / name
        target.write_bytes(b"vanilla " + name.encode())
        archives[name] = target.read_bytes()

    for selection in ([], ["pcs"], [c["id"] for c in categories()]):
        bridge.set_hd_categories("ff13", "hdproject", selection)
        bridge.hd_categories("ff13", "hdproject")
    bridge.identify_drop(str(install))

    for name, before in archives.items():
        assert (install / "white_data" / "sys" / name).read_bytes() == before


def test_the_hd_row_is_never_in_the_apply_queue():
    """
    hdproject is kind "extra", and the UI's jobs() filters those out, so Apply
    cannot reach it however the toggles are set. That is what makes the whole
    panel safe rather than careful.
    """
    jobs = json.loads((REPO / "ui" / "jobs.json").read_text(encoding="utf-8"))
    job = next(j for g in jobs["games"] for j in g["jobs"] if j["id"] == "hdproject")
    assert job["kind"] == "extra"

    ui = (REPO / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'filter(j => j.kind !== "extra")' in ui, \
        "the queue stopped excluding extras, so Apply can now reach this row"


def test_the_ui_manifest_carries_the_categories():
    """
    the UI renders them and the backend decides them, so they are stamped
    across rather than typed twice. A second copy in jobs.json is the drift
    build_ui_manifest.py exists to stop.
    """
    generated = REPO / "assets" / "manifest.json"
    if not generated.is_file():
        # generated and gitignored. CI builds it before pytest for the same
        # reason test_bridge.py needs it, but a bare pytest on a clean clone
        # should fail on something real rather than on a missing artifact.
        import subprocess
        subprocess.run([sys.executable, str(REPO / "tools" / "build_ui_manifest.py")],
                       check=True, capture_output=True)

    built = json.loads(generated.read_text(encoding="utf-8"))
    job = next(j for g in built["games"] for j in g["jobs"] if j["id"] == "hdproject")
    assert [c["id"] for c in job["categories"]] == [c["id"] for c in categories()]

    ui = (REPO / "ui" / "index.html").read_text(encoding="utf-8")
    assert "Environmental Zones" in ui, "the baked fallback missed the categories"


def test_jobs_json_does_not_keep_its_own_copy():
    jobs = json.loads((REPO / "ui" / "jobs.json").read_text(encoding="utf-8"))
    job = next(j for g in jobs["games"] for j in g["jobs"] if j["id"] == "hdproject")

    assert "categories" not in job, \
        "categories belong in manifests/, stamped across at build time"
