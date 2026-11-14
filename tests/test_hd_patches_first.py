"""
Installing HD before pressing Apply.

XIII-2 refuses HD without the 4GB Patch, so a mod dropped in first used to end
at a refusal telling the user to go press Apply. The install now puts the
game's required fixes on itself, ahead of the mod, and these pin the guard
rails: nothing already on goes in twice, a fix that won't go in leaves the
mod's own refusal to speak, Stop covers both, and a rebuild whose restore took
a fix off puts it back before the mod goes on.
"""

import json
import time
from pathlib import Path

import pytest

from tests.hd2_world import entry_bytes
from tests.test_hd2_end_to_end import build_world, snapshot
from tests.rain import PAYLOAD
from tests.test_hd2_injections import WEATHER, weather

REPO = Path(__file__).resolve().parent.parent
EXE = Path("alba_data") / "prog" / "win" / "bin" / "ffxiii2img.exe"
GAME_ZONE = "alba_data/zone/white_z0010u_img.win32.bin"


def required():
    """what the page sends: the required rows out of ui/jobs.json, not a copy of them"""
    games = json.loads((REPO / "ui" / "jobs.json").read_text(encoding="utf-8"))["games"]
    jobs = next(g for g in games if g["id"] == "ff13-2")["jobs"]
    return [j["id"] for j in jobs if j.get("kind", "required") == "required"]


def run(api, events, mod, selected, mode="add", first=None, timeout=60, game="ff13-2"):
    events.clear()
    started = api.install_hd_project(game, str(mod), selected, mode, first)
    assert started.get("ok"), started
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ended = [e for e in events if e.get("stage") in ("done", "failed", "cancelled")]
        if ended:
            return ended[0]
        time.sleep(0.05)
    pytest.fail(f"the run never finished: {[e.get('stage') for e in events]}")


def lines(events):
    return [e["text"] for e in events if e.get("stage") == "line"]


def has_laa(game):
    from patcher.patch_ops import is_laa_enabled
    return is_laa_enabled((game / EXE).read_bytes())


def test_an_install_before_apply_puts_the_fixes_on_first(tmp_path, monkeypatch):
    api, events, game, mod = build_world(tmp_path, monkeypatch, laa=False)

    ending = run(api, events, mod, ["weapons"], first=required())

    assert ending["stage"] == "done", ending
    assert has_laa(game)
    assert api._patcher().missing_jobs("ff13-2", required()) == {}
    patched = [e for e in events if e.get("stage") == "patched"]
    assert len(patched) == 1 and set(patched[0]["applied"]) == set(required()), patched
    said = lines(events)
    first_fix = next(i for i, text in enumerate(said) if text.startswith("[patches]"))
    first_backup = next(i for i, text in enumerate(said) if text.startswith("[backup]"))
    assert first_fix < first_backup, "the mod's backup was taken before the fixes went in"


def test_the_backup_taken_after_the_fixes_keeps_them_through_revert(tmp_path, monkeypatch):
    """the order Apply-then-install always gave, so Revert of HD leaves the fixes alone"""
    api, events, game, mod = build_world(tmp_path, monkeypatch, laa=False)
    assert run(api, events, mod, ["weapons"], first=required())["stage"] == "done"

    put_back = api.restore_hd_project("ff13-2")

    assert put_back["ok"], put_back
    assert not put_back["cameOff"], put_back
    assert api._patcher().missing_jobs("ff13-2", required()) == {}


def test_fixes_already_on_the_game_are_left_alone(tmp_path, monkeypatch):
    api, events, game, mod = build_world(tmp_path, monkeypatch)
    exe = (game / EXE).read_bytes()

    ending = run(api, events, mod, ["weapons"], first=["laa"])

    assert ending["stage"] == "done", ending
    assert (game / EXE).read_bytes() == exe, "the 4GB Patch went on a second time"
    assert not [e for e in events if e.get("stage") == "patched"]
    assert not [text for text in lines(events) if text.startswith("[patches]")]


def test_without_a_list_nothing_is_put_on(tmp_path, monkeypatch):
    """the old call, from anything that isn't the window, still refuses as it did"""
    api, events, game, mod = build_world(tmp_path, monkeypatch, laa=False)

    ending = run(api, events, mod, ["weapons"])

    assert ending["stage"] == "failed" and "4GB Patch" in ending["error"], ending
    assert not has_laa(game)


def test_a_fix_that_will_not_go_in_leaves_the_refusal_to_the_mod(tmp_path, monkeypatch):
    """the game running is the usual reason, and the line above the refusal says so"""
    import patcher.engine as engine

    api, events, game, mod = build_world(tmp_path, monkeypatch, laa=False)
    before = snapshot(game)
    real = engine.write_atomic

    def held(target, *args, **kwargs):
        if Path(target).name == "ffxiii2img.exe":
            raise PermissionError(13, "in use", str(target))
        return real(target, *args, **kwargs)

    monkeypatch.setattr(engine, "write_atomic", held)

    ending = run(api, events, mod, ["weapons"], first=required())

    assert ending["stage"] == "failed", ending
    assert "4GB Patch isn't on ffxiii2img.exe" in ending["error"], ending
    assert "nothing has been changed" not in ending["error"].lower(), ending
    assert ending["note"] == ("NovaFix already went into the game, complete. "
                              "Nothing else was changed."), ending
    refused = [e for e in events if e.get("stage") == "line"
               and e["text"].startswith("[patches] 4GB Patch didn't go in")]
    assert refused and refused[0]["stream"] == "err", lines(events)
    assert (game / GAME_ZONE).read_bytes() == before[GAME_ZONE]



def test_a_fix_the_engine_keeps_off_this_exe_is_not_an_error(tmp_path, monkeypatch):
    """
    XIII's DRM refused the 4GB patch once, so the engine keeps it and the shim
    off from then on. That's its decision about the exe, and saying it in red
    on every install lit the minimized terminal's alert for nothing.
    """
    from patcher.launch_watch import DRM_ERROR
    from patcher.state import record_launch
    from tests.test_hd_end_to_end import make_world

    api, events, install, mod = make_world(tmp_path, monkeypatch)
    state = tmp_path / "state"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda gid: state / f"{gid}.json")
    monkeypatch.setattr("patcher.paths.run_log_path",
                        lambda stem: tmp_path / "logs" / f"{stem}.log")
    record_launch(state / "ffxiii.json", "flip_laa_flag", DRM_ERROR, cover="dinput8_shim")

    ending = run(api, events, mod, ["npcs"], game="ff13", first=["ff13fix", "dinput8shim", "laa"])

    assert ending["stage"] == "done", ending
    tried = [e for e in events if e.get("stage") == "line" and e["text"].startswith("[patches]")]
    kept_off = [e["text"] for e in tried if " stays off: " in e["text"]]
    assert len(kept_off) == 2, [e["text"] for e in tried]
    assert not [e for e in tried if e["stream"] == "err"], [e["text"] for e in tried]


def test_stop_during_the_fixes_stops_before_the_mod(tmp_path, monkeypatch):
    api, events, game, mod = build_world(tmp_path, monkeypatch, laa=False)
    before = snapshot(game)
    bridge = api._patcher()
    real = bridge.apply

    def then_stop(*args, **kwargs):
        answer = real(*args, **kwargs)
        api.cancel_hd_project()
        return answer

    monkeypatch.setattr(bridge, "apply", then_stop)

    ending = run(api, events, mod, ["weapons"], first=["laa"])

    assert ending["stage"] == "cancelled", ending
    assert ending["note"] == ("Stopped. 4GB Patch already went into the game, complete. "
                              "Nothing else was changed."), ending
    assert has_laa(game)
    assert snapshot(game) == before, "the mod went in after Stop"


def test_a_rebuild_puts_back_on_what_its_restore_took_off(tmp_path, monkeypatch):
    """
    HD first, Leviathan's Tears after, so the backup a rebuild restores is
    without it. On the stand-in, since it's off Apply now: an injection Apply
    puts in is what this is about
    """
    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    assert run(api, events, mod, ["weapons"])["stage"] == "done"
    went = api._patcher().apply("ff13-2", ["leviathanstears"])
    assert went["ok"], went

    ending = run(api, events, mod, ["weapons"], mode="set",
                 first=[*required(), "leviathanstears"])

    assert ending["stage"] == "done", ending
    said = lines(events)
    off = [text for text in said if text.startswith("[restore] Leviathan's Tears came off")]
    assert off and off[0].endswith("It goes back on before the mod."), said
    assert entry_bytes(game, WEATHER[0]) == PAYLOAD
    assert api._patcher().missing_jobs("ff13-2", ["leviathanstears"]) == {}
    patched = [e["applied"] for e in events if e.get("stage") == "patched"]
    assert ["leviathanstears"] in patched, patched


def test_one_thats_left_the_patcher_stays_off_after_a_rebuild(tmp_path, monkeypatch):
    """
    an earlier version's Leviathan's Tears, which a rebuild's restore takes off
    like any, has nothing to go back on with now, so the line neither says it
    will nor sends anyone to Apply
    """
    from patcher.bridge import UiBridge
    from patcher.state import load_state

    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    assert run(api, events, mod, ["weapons"])["stage"] == "done"
    assert api._patcher().apply("ff13-2", ["leviathanstears"])["ok"]
    # and then this version, whose entry for it is retired
    now = UiBridge(manifest_paths={p.stem: p for p in sorted((REPO / "manifests").glob("*.json"))},
                   steam_library_paths=[tmp_path / "steam"])
    monkeypatch.setattr(api, "_patcher", lambda: now)

    ending = run(api, events, mod, ["weapons"], mode="set", first=required())

    assert ending["stage"] == "done", ending
    said = lines(events)
    off = [text for text in said if text.startswith("[restore] Leviathan's Tears came off")]
    assert off and off[0].endswith("the copies that went back were without it."), said
    assert entry_bytes(game, WEATHER[0]) != PAYLOAD
    state = load_state(tmp_path / "state" / "ffxiii2.json")
    assert "install_leviathans_tears" not in state["applied_patches"]


def test_a_fix_put_back_after_a_restore_is_not_news_when_the_rebuild_fails(tmp_path, monkeypatch):
    """off with the restore and straight back on is the same game, so the note says neither"""
    from patcher.mods import hd_games
    from patcher.mods.hd_install import InstallError

    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    assert run(api, events, mod, ["weapons"])["stage"] == "done"
    assert api._patcher().apply("ff13-2", ["leviathanstears"])["ok"]

    def refused(self, *args, **kwargs):
        raise InstallError("the tools went missing. Nothing has been changed.")

    monkeypatch.setattr(hd_games.Xiii2, "install", refused)

    ending = run(api, events, mod, ["weapons"], mode="set", first=["leviathanstears"])

    assert ending["stage"] == "failed", ending
    assert ending["note"] == ("The vanilla archives already went into the game, complete. "
                              "Nothing else was changed."), ending
    assert entry_bytes(game, WEATHER[0]) == PAYLOAD


def test_a_refused_repair_still_says_the_fixes_went_in(tmp_path, monkeypatch):
    """the cache refusal ends the run before so_far ever saw it, so it said nothing"""
    api, events, game, mod = build_world(tmp_path, monkeypatch)
    assert run(api, events, mod, ["pcs"])["stage"] == "done"
    cached = mod / "models" / "orig" / "chr" / "pc" / "c108" / "bin" / "c108.win32.trb"
    cached.write_bytes(b"x" * 1000)

    ending = run(api, events, mod, ["pcs"], first=required())

    assert ending["stage"] == "failed" and "c108.win32.trb" in ending["error"], ending
    assert ending["note"] == ("NovaFix already went into the game, complete. "
                              "Nothing else was changed."), ending


def test_taking_hd_off_puts_nothing_on(tmp_path, monkeypatch):
    """a rebuild down to nothing is a removal, and NovaFix turning up with it is a surprise"""
    api, events, game, mod = build_world(tmp_path, monkeypatch)
    assert run(api, events, mod, ["weapons"])["stage"] == "done"

    ending = run(api, events, mod, [], mode="set", first=required())

    assert ending["stage"] == "done", ending
    assert not [text for text in lines(events) if text.startswith("[patches]")], lines(events)
    assert "ff132fix" in api._patcher().missing_jobs("ff13-2", required())


def test_only_what_can_go_in_unattended_is_put_on(tmp_path, monkeypatch):
    """a download, a sign-in or somebody's .bat can't happen halfway through an install"""
    api, _, _, _ = build_world(tmp_path, monkeypatch, laa=False)

    missing = api._patcher().missing_jobs("ff13-2", ["laa", "dlcrestore", "hdproject2"])

    assert missing == {"laa": "4GB Patch"}


def test_launch_waits_for_the_install_on_that_game(tmp_path, monkeypatch):
    """the row can offer Launch mid-install now, and the game holding the archives fails the swap"""
    api, _, _, _ = build_world(tmp_path, monkeypatch)
    api._hd_running, api._hd_game = True, "ff13-2"

    answer = api.launch_game("ff13-2")

    assert not answer["ok"] and "Launch once it has finished" in answer["error"], answer
