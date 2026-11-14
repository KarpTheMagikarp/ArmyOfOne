"""
The whole HD Project path through the real shell.Api, not the modules alone.

Every other test in this area exercises one layer. This one presses the button:
a fake Steam install, a real mod folder layout with stubbed tools, and the same
Api object pywebview hands the page. It catches the joins, which is where all
of this has actually broken so far.

The tools are stubs because the real ones are Windows PE. Everything else is
the shipping code.
"""

import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from patcher.mods import hd_runner

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data" / "hd"

# The three tools, shaped like the real pipeline: ff13tool -x builds the tree
# from the mod's own LoadHD lists, the other two read models\\orig and write
# models\\mod, and the runner's copies put the result in the tree. Shared with
# test_hd_install so both files exercise the same shape.
from tests.test_hd_install import FF13TOOL, INJECT, MODELS

STUBS = {"pack": FF13TOOL, "models": MODELS, "inject": INJECT}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """a game, a mod folder and a shell Api wired to both"""
    return make_world(tmp_path, monkeypatch)


def make_world(tmp_path, monkeypatch):
    import shell as shell_mod
    from tests.test_hd_runner import stub

    # a library Steam would actually recognise: the acf manifest, the folder it
    # names, and the executable the game manifest resolves against
    from tests.helpers import make_fake_pe

    library = tmp_path / "steam"
    install = library / "steamapps" / "common" / "FINAL FANTASY XIII"
    (install / "white_data" / "prog" / "win" / "bin").mkdir(parents=True)
    (install / "white_data" / "prog" / "win" / "bin" / "ffxiiiimg.exe").write_bytes(make_fake_pe())
    (library / "steamapps" / "appmanifest_292120.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"292120"\n\t"installdir"\t\t"FINAL FANTASY XIII"\n}\n')

    sys_dir = install / "white_data" / "sys"
    sys_dir.mkdir(parents=True)

    mod = tmp_path / "FFXIII HD^Project!"      # the characters their .bat chokes on
    data, support = mod / "Data", mod / "SupportFiles"
    data.mkdir(parents=True)
    support.mkdir(parents=True)
    for name in ("LoadHDEidolons.txt", "n910.txt", "models_sample.bin"):
        shutil.copy(DATA / name, data / name)
    (data / "LoadHDNPCs.txt").write_text("#:NPCs\nn n910\n", encoding="utf-8")
    (data / "LoadHDWeapons.txt").write_text("#:Weapons\nw w001\n", encoding="utf-8")
    (data / "FFXIII_General_Models.bin").write_bytes((DATA / "models_sample.bin").read_bytes())

    # a real pair, not b"VANILLA:...". Everything interesting now reads it:
    # the size survey, the applied check, the free space estimate, the restore
    # decision and the backup's own check that the two belong together.
    from tests.helpers import index_entries, write_scr_pair
    write_scr_pair(sys_dir / "filelist_scru.win32.bin", sys_dir / "white_scru.win32.bin",
                   index_entries(data), size=2048)

    if sys.platform != "win32":
        monkeypatch.setitem(hd_runner.TOOLS, "pack", "ff13tool")
        monkeypatch.setitem(hd_runner.TOOLS, "models", "FFXIII_ModelsHD")
        monkeypatch.setitem(hd_runner.TOOLS, "inject", "FFXIII_HDProject")
    for key, body in STUBS.items():
        stub(support, hd_runner.TOOLS[key], body)

    state_dir = tmp_path / "state"
    monkeypatch.setattr("patcher.bridge.state_path_for", lambda gid: state_dir / f"{gid}.json")
    monkeypatch.setattr("patcher.paths.undo_store_for", lambda d: Path(d) / ".patcher-undo")
    # this world's own, or a voice switch finds another test's mod in a shared log's header
    monkeypatch.setattr("patcher.paths.run_log_path", lambda stem: tmp_path / "logs" / f"{stem}.log")
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)

    api = shell_mod.Api()
    api._window = None
    events = []
    api._emit = lambda ev: events.append(ev)
    monkeypatch.setattr(api, "_patcher", lambda: _bridge(library))

    return api, events, install, mod


def _bridge(library):
    from patcher.bridge import UiBridge
    paths = {p.stem: p for p in sorted((REPO / "manifests").glob("*.json"))}
    return UiBridge(manifest_paths=paths, steam_library_paths=[library])


# every step starts a stub tool, and starting a process on Windows costs ten
# times what it does on Linux, so the limit is there for a hang, not a pace
def run_and_wait(api, events, install, mod, selected, timeout=180, mode="add", voices=False):
    started = api.install_hd_project("ff13", str(mod), selected, mode, voices=voices)
    assert started.get("ok"), started
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(e.get("stage") in ("done", "failed", "cancelled") for e in events):
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"the run never finished. events: {[e.get('stage') for e in events]}")
    return [e for e in events if e.get("type") == "hd"]


def stage_of(events, name):
    return next((e for e in events if e.get("stage") == name), None)


def test_a_drop_is_identified_through_the_shell(world):
    api, _, _, mod = world

    what = api.identify_drop(str(mod))

    assert what["kind"] == "hdproject"
    assert what["jobId"] == "hdproject"
    assert Path(what["detail"]["data"]).name == "Data"


def test_the_categories_come_back_through_the_shell(world):
    api, _, _, _ = world

    state = api.hd_categories("ff13", "hdproject")

    assert len(state["categories"]) == 7
    assert "pcs" not in state["selected"]


def test_a_whole_install_runs_and_writes_the_game(world):
    api, events, install, mod = world
    sys_dir = install / "white_data" / "sys"

    hd = run_and_wait(api, events, install, mod, ["npcs", "weapons"])

    assert stage_of(hd, "done"), [e for e in hd if e.get("stage") == "failed"]
    from tests.helpers import carries_hd
    assert carries_hd(sys_dir / "filelist_scru.win32.bin", sys_dir / "white_scru.win32.bin",
                      "chr/npc/n910/bin/n910.win32.trb"), "the pair was not written back"

    lines = [e["text"] for e in hd if e.get("stage") == "line"]
    assert any("[backup]" in l for l in lines), "nothing was backed up"
    # the line names the file it ran, which is ff13tool.exe on Windows
    assert any(re.search(r"ff13tool(\.exe)? -x", l) for l in lines), "the archive was never unpacked"
    assert any(re.search(r"ff13tool(\.exe)? -c", l) for l in lines), "the archive was never repacked"
    assert any("cached" in l and "original model files" in l for l in lines), \
        "the originals were never cached, so the tools had nothing to read"

    # the result of each id has to land in the tree, or the repack rebuilds
    # exactly what came out. This is the half that was missing.
    tree = mod / "white_scru" / "chr" / "npc" / "n910" / "bin"
    assert not tree.exists(), "the working tree should have been cleaned up"
    steps = [e["label"] for e in hd if e.get("stage") == "step"]
    assert any("n910" in s for s in steps), f"no recipe was run: {steps}"


def test_the_backup_holds_the_vanilla_archives(world):
    api, events, install, mod = world
    vanilla = {p.name: p.read_bytes()
               for p in (install / "white_data" / "sys").iterdir()}

    run_and_wait(api, events, install, mod, ["npcs"])

    saved = install / ".patcher-undo" / "hdproject"
    for name, content in vanilla.items():
        assert (saved / name).read_bytes() == content, f"{name} backup is not vanilla"


def test_restore_puts_the_game_back_after_a_real_install(world):
    api, events, install, mod = world
    sys_dir = install / "white_data" / "sys"
    vanilla = {p.name: p.read_bytes() for p in sys_dir.iterdir()}

    run_and_wait(api, events, install, mod, ["npcs"])
    assert (sys_dir / "white_scru.win32.bin").read_bytes() != vanilla["white_scru.win32.bin"]

    result = api.restore_hd_project("ff13")

    assert result["ok"], result
    for name, content in vanilla.items():
        assert (sys_dir / name).read_bytes() == content, f"{name} did not come back"


def test_what_was_installed_is_recorded_so_unticking_means_something(world):
    api, events, install, mod = world

    run_and_wait(api, events, install, mod, ["npcs", "weapons"])

    state = api.hd_categories("ff13", "hdproject")
    assert set(state["installed"]) == {"npcs", "weapons"}

    api.restore_hd_project("ff13")
    assert api.hd_categories("ff13", "hdproject")["installed"] == []


def test_adding_a_category_leaves_the_one_already_there_alone(world):
    """
    the whole reason adding stopped restoring first. A game with NPCs in it
    that wants Weapons should cost Weapons, not Weapons plus rebuilding every
    NPC texture already sitting in the archive.
    """
    api, events, install, mod = world

    run_and_wait(api, events, install, mod, ["npcs"])
    events.clear()
    hd = run_and_wait(api, events, install, mod, ["weapons"])

    assert stage_of(hd, "done")
    lines = [e["text"] for e in hd if e.get("stage") == "line"]
    assert not any("[restore]" in l for l in lines), \
        "the archive was thrown away and rebuilt, which is the hour this avoids"

    steps = [e["label"] for e in hd if e.get("stage") == "step"]
    assert any("w001" in s for s in steps), "the new category was not built"
    assert not any("n910" in s for s in steps), \
        "the category already in the game was built again"


def test_adding_keeps_what_the_earlier_run_put_in(world):
    """
    skipping the restore is only correct if the archive really does keep the
    earlier install through a second extract and repack
    """
    api, events, install, mod = world

    run_and_wait(api, events, install, mod, ["npcs"])
    events.clear()
    run_and_wait(api, events, install, mod, ["weapons"])

    assert set(api.hd_categories("ff13", "hdproject")["installed"]) == {"npcs", "weapons"}, \
        "the record replaced what was there instead of adding to it"

    answer = api.hd_survey("ff13", "hdproject", str(mod))
    assert answer["verified"]
    assert set(answer["applied"]) == {"npcs", "weapons"}, \
        "the archive lost the first install when the second one repacked it"


def test_ticking_something_already_in_the_game_rebuilds_it(world):
    """
    the repair path. This used to refuse, and refusing is what made a bad
    install a dead end: a category that came out corrupt could not be built
    again, so the only way forward was Revert and an hour spent rebuilding
    every category that was already fine.

    Safe because nothing here is copied out of the mod. Every HD file is
    derived from the vanilla one in models\\orig, so building a category twice
    writes the same bytes the first run wrote.
    """
    api, events, install, mod = world

    run_and_wait(api, events, install, mod, ["npcs"])
    events.clear()
    hd = run_and_wait(api, events, install, mod, ["npcs"])

    assert stage_of(hd, "done"), [e.get("stage") for e in hd]
    lines = [e["text"] for e in hd if e.get("stage") == "line"]
    assert any("[repair]" in l and "NPCs" in l for l in lines), \
        "it rebuilt without saying that is what it was doing"
    steps = [e["label"] for e in hd if e.get("stage") == "step"]
    assert any("n910" in s for s in steps), "the category was not actually built again"


def test_a_repair_refuses_when_the_originals_cache_is_not_vanilla(world):
    """
    the one case where building over the top is worse than doing nothing. The
    tools read models\\orig and write HD art from it, so a cache that was first
    filled from an already modded game means a repair reads HD art and writes
    HD art over it. Their own installer walks into exactly this when its cache
    goes missing and it refills it from the live archive.
    """
    api, events, install, mod = world
    from patcher.mods.hd_preflight import VANILLA_PCS

    run_and_wait(api, events, install, mod, ["npcs"])

    # one cached original at a size vanilla never ships, which is what a cache
    # filled from a modded game looks like
    path, size = next(iter(VANILLA_PCS.items()))
    poisoned = mod / "models" / "orig" / Path(path)
    poisoned.parent.mkdir(parents=True, exist_ok=True)
    poisoned.write_bytes(b"\x00" * (size + 4096))
    events.clear()

    hd = run_and_wait(api, events, install, mod, ["npcs"])

    failed = stage_of(hd, "failed")
    assert failed and "already been modded" in failed["error"], failed
    assert not any(e.get("stage") == "step" for e in hd), "it rebuilt from HD art anyway"


def test_adding_and_repairing_in_one_run_does_both(world):
    api, events, install, mod = world

    run_and_wait(api, events, install, mod, ["npcs"])
    events.clear()
    hd = run_and_wait(api, events, install, mod, ["npcs", "weapons"])

    assert stage_of(hd, "done")
    lines = [e["text"] for e in hd if e.get("stage") == "line"]
    assert any("[repair]" in l and "NPCs" in l for l in lines), \
        "nothing said the applied category was being rebuilt rather than added"
    steps = [e["label"] for e in hd if e.get("stage") == "step"]
    assert any("w001" in s for s in steps), "the new category was not built"
    assert any("n910" in s for s in steps), "the repaired category was not built"


def test_removing_one_rebuilds_from_vanilla_and_puts_the_others_back(world):
    """
    what Uninstall does. There is no per category undo inside a repacked
    archive, so the set that survives is what gets named and the rest of the
    work is the price.
    """
    api, events, install, mod = world

    run_and_wait(api, events, install, mod, ["npcs", "weapons"])
    events.clear()
    hd = run_and_wait(api, events, install, mod, ["weapons"], mode="set")

    assert stage_of(hd, "done")
    lines = [e["text"] for e in hd if e.get("stage") == "line"]
    assert any("[restore]" in l for l in lines), "it did not go back to vanilla first"

    steps = [e["label"] for e in hd if e.get("stage") == "step"]
    assert any("w001" in s for s in steps), "the survivor was not put back"
    assert not any("n910" in s for s in steps), "the one being removed was built again"

    assert api.hd_categories("ff13", "hdproject")["installed"] == ["weapons"]
    answer = api.hd_survey("ff13", "hdproject", str(mod))
    assert answer["verified"] and set(answer["applied"]) == {"weapons"}


def test_removing_everything_is_a_restore_and_nothing_else(world):
    api, events, install, mod = world
    sys_dir = install / "white_data" / "sys"
    vanilla = {p.name: p.read_bytes() for p in sys_dir.iterdir()}

    run_and_wait(api, events, install, mod, ["npcs", "weapons"])
    events.clear()
    hd = run_and_wait(api, events, install, mod, [], mode="set")

    done = stage_of(hd, "done")
    assert done and done["removed"] == 2 and done["steps"] == 0, done
    assert not any(e.get("stage") == "step" for e in hd), \
        "it rebuilt an archive it was about to have nothing to put in"
    for name, content in vanilla.items():
        assert (sys_dir / name).read_bytes() == content, f"{name} did not come back"
    assert api.hd_categories("ff13", "hdproject")["installed"] == []


def test_an_install_mode_nobody_defined_is_refused(world):
    api, events, install, mod = world

    result = api.install_hd_project("ff13", str(mod), ["npcs"], "wipe")

    assert not result["ok"] and "wipe" in result["error"]
    assert not api._hd_running


def test_two_installs_at_once_are_refused(world):
    api, events, install, mod = world
    api._hd_running = True

    result = api.install_hd_project("ff13", str(mod), ["npcs"])

    assert not result["ok"] and "already running" in result["error"]


def test_a_folder_that_is_not_the_mod_fails_without_touching_the_game(world, tmp_path):
    api, events, install, mod = world
    vanilla = {p.name: p.read_bytes() for p in (install / "white_data" / "sys").iterdir()}
    empty = tmp_path / "some other folder"
    empty.mkdir()

    hd = run_and_wait(api, events, install, empty, ["npcs"])

    assert stage_of(hd, "failed")
    for name, content in vanilla.items():
        assert (install / "white_data" / "sys" / name).read_bytes() == content


def test_a_failing_tool_reports_which_one_and_leaves_the_game_alone(world, monkeypatch):
    api, events, install, mod = world
    from tests.test_hd_runner import stub
    stub(mod / "SupportFiles", hd_runner.TOOLS["pack"],
         "import sys\nprint('cannot read archive', flush=True)\nsys.exit(5)\n")
    vanilla = {p.name: p.read_bytes() for p in (install / "white_data" / "sys").iterdir()}

    hd = run_and_wait(api, events, install, mod, ["npcs"])

    failed = stage_of(hd, "failed")
    assert failed and failed["code"] == 5
    assert "unpacking" in failed["label"]
    for name, content in vanilla.items():
        assert (install / "white_data" / "sys" / name).read_bytes() == content


def test_cancelling_through_the_shell_stops_it(world):
    api, events, install, mod = world
    from tests.test_hd_runner import stub
    stub(mod / "SupportFiles", hd_runner.TOOLS["pack"],
         "import time\nprint('unpacking', flush=True)\ntime.sleep(30)\n")
    vanilla = {p.name: p.read_bytes() for p in (install / "white_data" / "sys").iterdir()}

    threading.Timer(1.0, lambda: api.cancel_hd_project()).start()
    hd = run_and_wait(api, events, install, mod, ["npcs"], timeout=40)

    assert stage_of(hd, "cancelled"), [e.get("stage") for e in hd]
    for name, content in vanilla.items():
        assert (install / "white_data" / "sys" / name).read_bytes() == content
    assert (install / ".patcher-undo" / "hdproject" / "white_scru.win32.bin").is_file(), \
        "a cancel threw the backup away, so a retry has nothing to fall back on"


def test_the_run_flag_clears_so_a_retry_is_possible(world):
    api, events, install, mod = world
    from tests.test_hd_runner import stub
    stub(mod / "SupportFiles", hd_runner.TOOLS["pack"],
         "import sys\nprint('boom', flush=True)\nsys.exit(5)\n")

    run_and_wait(api, events, install, mod, ["npcs"])

    assert api._hd_running is False, "a failed run left the flag set, so nothing can run again"


def test_every_event_is_json_serialisable(world):
    """
    _emit does json.dumps before handing it to the page. A Path or an exception
    in there raises inside the emit, which the shell swallows, so the terminal
    just stops with no clue why.
    """
    api, events, install, mod = world

    run_and_wait(api, events, install, mod, ["npcs", "weapons"])

    for event in events:
        json.dumps(event)


def test_progress_arrives_before_the_run_finishes(world):
    """
    the whole reason for the terminal. events collected only at the end would
    pass every other test in this file and still show a frozen window.
    """
    api, events, install, mod = world
    from tests.test_hd_runner import stub
    stub(mod / "SupportFiles", hd_runner.TOOLS["models"],
         "import time\nprint('working', flush=True)\ntime.sleep(1.2)\nprint('done', flush=True)\n")

    api.install_hd_project("ff13", str(mod), ["npcs"])
    time.sleep(0.9)
    early = len([e for e in events if e.get("stage") == "line"])

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not any(
            e.get("stage") in ("done", "failed") for e in events):
        time.sleep(0.05)

    assert early > 0, "nothing was emitted while the run was still going"


# what the tools said on the run that reported success. Two stubs, not one,
# because the two tools disagree about what zero means: for the injector it is
# "processed nothing" and therefore a failure, and for ModelsHD it is the
# ordinary success. Writing one stub for both is how this test first passed
# while asserting the wrong headline.
NO_ORIG_INJECT = ("import sys\n"
                  "print('The HD Project.  Version 3.0.0', flush=True)\n"
                  "print('Unable to open the .trb file.  Exiting.', flush=True)\n"
                  "sys.exit(0)\n")

# the real one returned 2, straight off the log
NO_ORIG_MODELS = ("import sys\n"
                  "print('HD Models+  v4.1.4', flush=True)\n"
                  "print('Error: Unable to open TRB model file "
                  "models\\\\orig\\\\chr\\\\npc\\\\n910\\\\bin\\\\n910.win32.trb', flush=True)\n"
                  "sys.exit(2)\n")


def test_a_run_that_installed_nothing_does_not_touch_the_game(world):
    """
    The worst outcome this app has produced: 857 of 859 steps skipped, "Done.
    859 steps." on screen, five categories recorded as installed, and a
    recompressed copy of the vanilla archive written over the game. Nothing was
    broken and nothing was installed, and the app said otherwise.
    """
    api, events, install, mod = world
    from tests.test_hd_runner import stub
    stub(mod / "SupportFiles", hd_runner.TOOLS["inject"], NO_ORIG_INJECT)
    stub(mod / "SupportFiles", hd_runner.TOOLS["models"], NO_ORIG_MODELS)
    vanilla = {p.name: p.read_bytes() for p in (install / "white_data" / "sys").iterdir()
               if p.is_file()}

    hd = run_and_wait(api, events, install, mod, ["npcs", "weapons"])

    assert not stage_of(hd, "done"), "it reported success having installed nothing"
    failed = stage_of(hd, "failed")
    assert failed, [e.get("stage") for e in hd]
    assert "None of the" in (failed.get("reason") or "") + (failed.get("error") or "") \
        or any("None of the" in t for t in failed.get("tail", [])), failed

    for name, content in vanilla.items():
        assert (install / "white_data" / "sys" / name).read_bytes() == content, \
            f"{name} was overwritten by an install that applied nothing"


def test_nothing_is_recorded_as_installed_when_nothing_was(world):
    """
    the state record drives what the panel shows and whether the next run
    restores first, so recording a phantom install is not cosmetic
    """
    api, events, install, mod = world
    from tests.test_hd_runner import stub
    stub(mod / "SupportFiles", hd_runner.TOOLS["inject"], NO_ORIG_INJECT)
    stub(mod / "SupportFiles", hd_runner.TOOLS["models"], NO_ORIG_MODELS)

    run_and_wait(api, events, install, mod, ["npcs", "weapons"])

    after = api.hd_categories("ff13", "hdproject")
    assert not after.get("installed"), after


def test_the_running_flag_clears_before_the_page_is_told_it_finished(world):
    """
    pressing Install the moment Done appears was answered with "an install is
    already running", because the flag cleared a few microseconds after the
    event went out
    """
    api, events, install, mod = world

    run_and_wait(api, events, install, mod, ["npcs"])
    again = api.install_hd_project("ff13", str(mod), ["npcs"])

    assert again.get("ok"), again
    api.cancel_hd_project()


def test_tools_that_report_success_and_change_nothing_do_not_reach_the_game(world):
    """
    the guarantee that survives not understanding the tools. If the pipeline is
    driven wrongly, every tool can report a healthy count and the tree still
    comes out identical to what went in, and repacking that writes a copy of
    what the game already has over the top of the game.
    """
    api, events, install, mod = world
    from tests.test_hd_runner import stub
    stub(mod / "SupportFiles", hd_runner.TOOLS["inject"],
         "import sys\nprint('HDProject ok', flush=True)\nsys.exit(3)\n")
    stub(mod / "SupportFiles", hd_runner.TOOLS["models"], "print('ModelsHD ok', flush=True)\n")
    vanilla = {p.name: p.read_bytes() for p in (install / "white_data" / "sys").iterdir()
               if p.is_file()}

    hd = run_and_wait(api, events, install, mod, ["npcs", "weapons"])

    assert not stage_of(hd, "done"), "every tool said yes and nothing was installed"
    for name, content in vanilla.items():
        assert (install / "white_data" / "sys" / name).read_bytes() == content


def test_the_archive_stops_a_double_install_when_the_record_has_forgotten(world, monkeypatch):
    """
    the record and the archive disagree after Steam verifies the files, after
    a manual restore, and on a game copied from another machine. When the
    record is the thing that lost it, the archive is what is left to ask.

    A second install is no longer refused, so what this pins now is that the
    archive still decides: the run knows this is a rebuild of something already
    there rather than a fresh add, on the evidence of the archive alone.
    """
    api, events, install, mod = world

    run_and_wait(api, events, install, mod, ["npcs"])
    # the record loses its memory, which is the whole scenario
    monkeypatch.setattr(api, "_patcher", lambda lib=None: _forgetful(install.parent.parent.parent))
    events.clear()

    hd = run_and_wait(api, events, install, mod, ["npcs"])

    assert stage_of(hd, "done"), [e.get("stage") for e in hd]
    lines = [e["text"] for e in hd if e.get("stage") == "line"]
    assert any("[repair]" in l for l in lines), \
        "it read a modded archive as vanilla because the state file had forgotten"


def test_the_archive_is_asked_before_the_record_is_believed(world, monkeypatch):
    """
    the record can also be wrong the other way: it claims something the game
    does not have, after a manual restore or a Steam verify. Believing it
    there would refuse an install the user is entitled to.
    """
    api, events, install, mod = world
    api._patcher().record_hd_installed("ff13", "hdproject", ["npcs", "weapons"])
    events.clear()

    hd = run_and_wait(api, events, install, mod, ["npcs"])

    assert stage_of(hd, "done"), "a record with no archive behind it blocked a first install"
    steps = [e["label"] for e in hd if e.get("stage") == "step"]
    assert any("n910" in s for s in steps)


def _forgetful(library):
    """a bridge whose state file remembers nothing, everything else real"""
    bridge = _bridge(library)
    real = bridge.hd_categories

    def blank(game, job):
        answer = dict(real(game, job))
        answer["installed"] = []
        return answer

    bridge.hd_categories = blank
    return bridge


def test_an_unmodded_game_is_not_restored_for_no_reason(world):
    """
    a first install has nothing to put back, and restore() raises when there
    is no backup, so a false positive here refuses every first run
    """
    api, events, install, mod = world

    hd = run_and_wait(api, events, install, mod, ["npcs"])

    assert stage_of(hd, "done"), [e for e in hd if e.get("stage") == "failed"]
    lines = [e["text"] for e in hd if e.get("stage") == "line"]
    assert not any("[restore]" in l for l in lines)


def test_the_survey_reports_what_the_archive_carries(world):
    api, events, install, mod = world

    before = api.hd_survey("ff13", "hdproject", str(mod))
    assert before["applied"] == [], before
    assert before["sizes"], "no sizes at all, so the labels stay bare"

    run_and_wait(api, events, install, mod, ["npcs", "weapons"])
    after = api.hd_survey("ff13", "hdproject", str(mod))

    assert after["verified"], "there is a backup now, so it could have told"
    assert set(after["applied"]) == {"npcs", "weapons"}, after
