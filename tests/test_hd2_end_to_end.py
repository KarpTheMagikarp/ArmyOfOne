"""
FF XIII-2 HD through the real shell.Api and the real XIII-2 manifest.

The same buttons the XIII test presses, pressed on the other game: a drop, the
categories, a survey, Install, Restore. What this catches is the joins, which
is where XIII-2 could still be handed XIII's pipeline.
"""

import time
from pathlib import Path

import pytest

from tests.hd2_world import (MODELS, entry_bytes, filler, make_game, make_mod, pin_vanilla,
                             zone_entry_bytes)

REPO = Path(__file__).resolve().parent.parent
GAME_FILES = ("alba_data/sys/filelistu.win32.bin", "alba_data/sys/white_imgu.win32.bin",
              "alba_data/zone/white_z0010u_img.win32.bin")


@pytest.fixture
def world(tmp_path, monkeypatch):
    return build_world(tmp_path, monkeypatch)


def build_world(tmp_path, monkeypatch, extra=(), laa=True, rain=False):
    """
    (api, events, game, mod): the real shell and bridge over a fixture game in
    a Steam library. `rain` puts Leviathan's Tears back on Apply, on the
    stand-in in tests.rain
    """
    import shell as shell_mod
    from patcher.bridge import UiBridge

    library = tmp_path / "steam"
    game = make_game(library / "steamapps" / "common", extra=extra, laa=laa)
    (library / "steamapps" / "appmanifest_292140.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"292140"\n\t"installdir"\t\t"FINAL FANTASY XIII-2"\n}\n')
    mod = make_mod(tmp_path, monkeypatch)

    pin_vanilla(monkeypatch, game)
    monkeypatch.setattr("patcher.bridge.state_path_for", lambda gid: tmp_path / "state" / f"{gid}.json")
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.paths.run_log_path", lambda stem: tmp_path / "logs" / f"{stem}.log")

    paths = {p.stem: p for p in sorted((REPO / "manifests").glob("*.json"))}
    if rain:
        from tests.rain import manifest_file
        paths["ffxiii2"] = manifest_file(tmp_path)
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    api = shell_mod.Api()
    api._window = None
    events = []
    api._emit = lambda ev: events.append(ev)
    monkeypatch.setattr(api, "_patcher", lambda: bridge)
    return api, events, game, mod


def run_and_wait(api, events, mod, selected, mode="add", timeout=60, voices=False):
    events.clear()
    started = api.install_hd_project("ff13-2", str(mod), selected, mode, voices=voices)
    assert started.get("ok"), started
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ended = [e for e in events if e.get("stage") in ("done", "failed", "cancelled")]
        if ended:
            return ended[0], [e for e in events if e.get("type") == "hd"]
        time.sleep(0.05)
    pytest.fail(f"the run never finished: {[e.get('stage') for e in events]}")


def snapshot(game):
    return {rel: (game / rel).read_bytes() for rel in GAME_FILES}


def test_a_drop_is_identified_as_xiii2_hd(world):
    api, _, _, mod = world

    what = api.identify_drop(str(mod))

    assert what["kind"] == "hdproject" and what["jobId"] == "hdproject2"


def test_the_categories_are_xiii2_s(world):
    api, _, _, _ = world

    state = api.hd_categories("ff13-2", "hdproject2")

    assert [c["id"] for c in state["categories"]] == ["zones", "weapons", "npcs", "enemies",
                                                      "misc", "pcs"]
    assert set(state["selected"]) == {"weapons", "npcs", "enemies", "misc"}


def test_install_writes_the_game_and_the_record(world):
    api, events, game, mod = world

    ending, hd = run_and_wait(api, events, mod, ["weapons", "pcs", "zones"])

    assert ending["stage"] == "done", ending
    assert b"HDMODEL" in entry_bytes(game, "chr/pc/c108/bin/c108.win32.trb")
    assert b"HD" in zone_entry_bytes(game, "z0010", "bg/loc001/a001/bin/block001.win32.imgb")
    assert entry_bytes(game, "chr/mon/m001/bin/m001.win32.imgb") == \
        filler("chr/mon/m001/bin/m001.win32.imgb", MODELS["chr/mon/m001/bin/m001.win32.imgb"])
    state = api.hd_categories("ff13-2", "hdproject2")
    assert set(state["installed"]) == {"weapons", "pcs", "zones"}
    # XIII-2's own log, not XIII's
    assert Path(ending["log"]).name.startswith("hdproject2-")
    lines = [e["text"] for e in hd if e.get("stage") == "line"]
    assert any("-ff132" in line for line in lines)


def test_the_survey_reads_sizes_and_what_is_in_from_the_game(world):
    api, events, game, mod = world

    before = api.hd_survey("ff13-2", "hdproject2", str(mod))
    assert before["verified"] is False and not before["applied"]
    assert set(before["sizes"]) == {"zones", "weapons", "npcs", "enemies", "misc", "pcs"}

    run_and_wait(api, events, mod, ["weapons", "zones"])
    after = api.hd_survey("ff13-2", "hdproject2", str(mod))

    assert after["verified"] is True
    assert set(after["applied"]) == {"weapons", "zones"}


def test_restore_puts_xiii2_back(world):
    api, events, game, mod = world
    before = snapshot(game)
    run_and_wait(api, events, mod, ["weapons", "zones"])
    assert snapshot(game) != before

    result = api.restore_hd_project("ff13-2")

    assert result["ok"], result
    assert snapshot(game) == before
    assert api.hd_categories("ff13-2", "hdproject2")["installed"] == []


def test_set_mode_takes_a_category_off(world):
    api, events, game, mod = world
    run_and_wait(api, events, mod, ["weapons", "enemies"])

    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set")

    assert ending["stage"] == "done", ending
    assert b"HD" in entry_bytes(game, "chr/weapon/w101/bin/w101.win32.imgb")
    assert b"HD" not in entry_bytes(game, "chr/mon/m001/bin/m001.win32.imgb")
    assert api.hd_categories("ff13-2", "hdproject2")["installed"] == ["weapons"]


def test_xiii_s_mod_is_refused_for_xiii2(world, tmp_path, monkeypatch):
    from tests.test_hd_install import make_mod as make_xiii_mod

    api, events, game, _ = world
    xiii, data, _ = make_xiii_mod(tmp_path / "xiii", monkeypatch)
    before = snapshot(game)

    ending, _ = run_and_wait(api, events, xiii, ["weapons"])

    assert ending["stage"] == "failed"
    assert "not FFXIII-2 HD" in ending["error"]
    assert snapshot(game) == before


def test_a_rebuild_that_would_be_refused_is_refused_before_the_restore(world):
    """the restore goes first, and a refusal after it arrives with the install already off"""
    from tests.helpers import make_fake_pe

    api, events, game, mod = world
    assert run_and_wait(api, events, mod, ["weapons", "zones"])[0]["stage"] == "done"
    installed = snapshot(game)
    # a Steam update of the exe takes the 4GB flag with it
    exe = game / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe"
    exe.write_bytes(make_fake_pe(characteristics=0x0002, machine=0x14c))

    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set")

    assert ending["stage"] == "failed" and "4GB Patch" in ending["error"], ending
    assert snapshot(game) == installed


def test_a_refusal_after_a_rebuild_s_restore_says_the_restore_happened(world, monkeypatch):
    from patcher.mods import hd2_install

    api, events, game, mod = world
    before = snapshot(game)
    assert run_and_wait(api, events, mod, ["weapons"])[0]["stage"] == "done"
    # something only the install itself can find out, after the restore
    monkeypatch.setattr(hd2_install, "chr_steps", lambda *a, **k: [])

    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set")

    assert ending["stage"] == "failed", ending
    assert snapshot(game) == before
    assert "nothing has been changed" not in ending["error"].lower(), ending["error"]
    assert "vanilla archives already went into the game" in (ending.get("note") or ""), ending


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail("it never happened")
        time.sleep(0.02)


def test_the_hd_install_waits_for_an_apply(world, monkeypatch):
    """on XIII-2 both rewrite white_img, and one landing inside the other is lost"""
    import threading

    api, events, game, mod = world
    going, release = threading.Event(), threading.Event()

    def applying(*args, **kwargs):
        going.set()
        release.wait(10)

    monkeypatch.setattr(api._patcher(), "apply", applying)
    assert api.apply("ff13-2", ["laa"])["started"]
    assert going.wait(5)
    before = snapshot(game)

    refused = api.install_hd_project("ff13-2", str(mod), ["weapons"], "add")

    assert not refused["ok"] and "patch run" in refused["error"], refused
    assert snapshot(game) == before
    release.set()
    wait_for(lambda: not api._applying)
    assert run_and_wait(api, events, mod, ["weapons"])[0]["stage"] == "done"


def test_apply_waits_for_the_hd_install(world, monkeypatch):
    import threading

    from patcher.mods import hd2_install

    api, events, game, mod = world
    real, inside, release = hd2_install.install, threading.Event(), threading.Event()

    def installing(*args, **kwargs):
        inside.set()
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(hd2_install, "install", installing)
    events.clear()
    assert api.install_hd_project("ff13-2", str(mod), ["weapons"], "add")["ok"]
    assert inside.wait(5)

    refused = api.apply("ff13-2", ["laa"])

    assert not refused["ok"] and "HD install" in refused["error"], refused
    release.set()
    wait_for(lambda: any(e.get("stage") in ("done", "failed") for e in events))
    assert api._busy.acquire(blocking=False), "a refused Apply kept the lock"
    api._busy.release()


def test_a_rebuild_puts_back_a_zone_blob_before_asking_for_it(world):
    """a swap that could not be taken back leaves the blob beside itself, and the restore fixes that"""
    api, events, game, mod = world
    assert run_and_wait(api, events, mod, ["weapons", "zones"])[0]["stage"] == "done"
    blob = game / "alba_data" / "zone" / "white_z0010u_img.win32.bin"
    blob.rename(blob.with_name(blob.name + ".prev"))

    ending, _ = run_and_wait(api, events, mod, ["weapons", "zones"], mode="set")

    assert ending["stage"] == "done", ending
    assert blob.is_file()


def test_an_hd_run_on_one_game_leaves_apply_on_another_alone(world, monkeypatch):
    import threading

    from patcher.mods import hd2_install

    api, events, game, mod = world
    real, inside, release = hd2_install.install, threading.Event(), threading.Event()

    def installing(*args, **kwargs):
        inside.set()
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(hd2_install, "install", installing)
    monkeypatch.setattr(api._patcher(), "apply", lambda *a, **k: None)
    events.clear()
    assert api.install_hd_project("ff13-2", str(mod), ["weapons"], "add")["ok"]
    assert inside.wait(5)

    started = api.apply("lr", ["some-job"])

    release.set()
    assert started["started"], started
    wait_for(lambda: any(e.get("stage") in ("done", "failed") for e in events))
    wait_for(lambda: api._applying is None)
