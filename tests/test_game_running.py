"""
Nothing is installed or patched under a running game. Windows won't let a file
the game has open be replaced, so a run that started anyway would stop on the
first one it couldn't write, which is a worse place to stop than the click.
"""

import hashlib
from pathlib import Path

import pytest

from patcher import running


def running_as(monkeypatch, *found):
    """the processes Windows would list, each (pid, exe name, path or None)"""
    monkeypatch.setattr(running, "processes", lambda: [(pid, name) for pid, name, _ in found])
    paths = {pid: path for pid, _, path in found}
    monkeypatch.setattr(running, "image_path", lambda pid: paths.get(pid))


def test_the_game_or_its_launcher_out_of_its_folder_counts(tmp_path, monkeypatch):
    game = tmp_path / "FINAL FANTASY XIII-2"
    exe = game / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe"

    running_as(monkeypatch, (12, "explorer.exe", "C:\\Windows\\explorer.exe"),
               (40, "ffxiii2img.exe", str(exe)))
    assert running.running_from(game, exe) == "ffxiii2img.exe"

    running_as(monkeypatch, (41, "ffxiiilauncher.exe", str(game / "FFXiiiLauncher.exe")))
    assert running.running_from(game, exe) == "ffxiiilauncher.exe"


def test_a_copy_of_the_game_somewhere_else_doesnt(tmp_path, monkeypatch):
    game = tmp_path / "FINAL FANTASY XIII-2"
    exe = game / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe"
    running_as(monkeypatch, (40, "ffxiii2img.exe", str(tmp_path / "Backup" / "ffxiii2img.exe")),
               (41, "ffxiii2imgx.exe", str(tmp_path / "FINAL FANTASY XIII-2 copy" / "x.exe")))

    assert running.running_from(game, exe) == ""


def test_one_windows_wont_name_the_path_of_counts_by_its_name(tmp_path, monkeypatch):
    """an elevated game hides its path from an app that isn't, and closing it is cheap"""
    game = tmp_path / "FINAL FANTASY XIII-2"
    exe = game / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe"

    running_as(monkeypatch, (40, "ffxiii2img.exe", None), (41, "svchost.exe", None))
    assert running.running_from(game, exe) == "ffxiii2img.exe"
    running_as(monkeypatch, (41, "svchost.exe", None))
    assert running.running_from(game, exe) == ""


def test_off_windows_nothing_is_running(tmp_path):
    assert running.running_from(tmp_path, tmp_path / "game.exe") == ""


@pytest.fixture
def world(tmp_path, monkeypatch):
    from tests.test_hd2_end_to_end import build_world

    return build_world(tmp_path, monkeypatch)


def snapshot(game: Path) -> dict:
    return {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(game.rglob("*")) if p.is_file()}


def test_every_write_is_refused_while_the_game_runs(world, monkeypatch, tmp_path):
    from tests.test_mod_drops import C108, zipped

    api, events, game, mod = world
    exe = game / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe"
    before = snapshot(game)
    running_as(monkeypatch, (40, "ffxiii2img.exe", str(exe)))
    drop = zipped(tmp_path, "Serah Dress.zip", {C108: b"s" * 3000})

    said = [api.apply("ff13-2", ["laa", "ff132fix"]),
            api.install_hd_project("ff13-2", str(mod), ["weapons"]),
            api.install_hd_project("ff13-2", str(mod), [], "add", None, True),
            api.install_mod("ff13-2", str(drop)),
            api.install_addon("ff13-2", str(drop)),
            api.remove_addon("ff13-2", "Serah Dress", "files"),
            api.restore_hd_project("ff13-2")]

    for answer in said:
        assert not answer["ok"], answer
        assert answer["error"].startswith("Final Fantasy XIII-2 is running (ffxiii2img.exe). "
                                          "Close it first"), answer
    assert snapshot(game) == before
    assert not [e for e in events if e.get("type") == "hd"], "a run started anyway"


def test_the_settings_still_save_with_it_running(world, monkeypatch):
    """NovaFix.ini and the voices are read at the next start, so writing them is fine"""
    api, events, game, mod = world
    exe = game / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe"
    running_as(monkeypatch, (40, "ffxiii2img.exe", str(exe)))

    went = api.save_novafix_settings("ff13-2", {"Quality": {"MSAA": "8"}})

    assert went["ok"], went


def test_a_command_line_revert_waits_for_the_game(tmp_path, monkeypatch):
    import main

    game = tmp_path / "FINAL FANTASY XIII-2"
    exe = game / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    running_as(monkeypatch, (40, "ffxiii2img.exe", str(exe)))
    told, reverted = [], []
    monkeypatch.setattr(main, "notify", told.append)
    monkeypatch.setattr("patcher.revert.revert_all", lambda *a, **k: reverted.append(a) or [])

    main.revert_game("ffxiii2", game)

    assert not reverted and told and "is running (ffxiii2img.exe)" in told[0], told
