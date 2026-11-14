"""
Every command line flag, invoked the way a user invokes it.

Written after --doctor and --revert both shipped importing
detect_steam_library_paths from the wrong module. Both functions were tested;
neither COMMAND was, so the broken import sat there until someone typed it.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def run(*args, expect_zero=True):
    result = subprocess.run([sys.executable, "main.py", *args],
                            cwd=REPO, capture_output=True, text=True, timeout=120)
    if expect_zero:
        assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "ImportError" not in result.stderr, result.stderr
    return result


def test_help_works():
    assert "--doctor" in run("--help").stdout


@pytest.mark.parametrize("game", ["ffxiii", "ffxiii2", "ffxiii3"])
def test_doctor_runs_for_every_game(game):
    """the flag that shipped with a broken import"""
    result = run(game, "--doctor")
    assert result.stdout.strip(), "the doctor printed nothing"


def test_doctor_with_no_game_covers_all_of_them():
    output = run("--doctor").stdout
    for name in ("Final Fantasy XIII", "Lightning Returns"):
        assert name in output


def test_doctor_pointed_at_one_game_reports_just_that_game(tmp_path):
    """
    the README and every "couldn't find" message say --path, without a game,
    and that used to print two BAD "couldn't find the game" blocks for the
    games that were never going to be in that folder
    """
    from tests.test_premodded_installs import make_game

    root = make_game(tmp_path / "Steam", "lr", {})

    output = run("--doctor", "--path", str(root)).stdout

    assert "Lightning Returns" in output and "LRFF13.exe" in output, output
    assert "couldn't find" not in output and "Final Fantasy XIII-2" not in output, output


@pytest.mark.parametrize("game", ["ffxiii", "ffxiii2", "ffxiii3"])
def test_dry_run_runs_for_every_game(game):
    """
    a non-zero exit is correct where the game is not installed, which is the
    case on any build machine. what must not happen is a traceback.
    """
    result = run(game, "--dry-run", expect_zero=False)
    assert result.returncode == 0 or "not found" in result.stdout


def test_a_patch_that_doesnt_ship_any_more_isnt_on_every_run(tmp_path):
    """XIII-2 said "Leviathan's Tears: skipped" on every run after it retired"""
    import json
    import struct

    game = tmp_path / "FFXIII2"
    manifest = json.loads((REPO / "manifests" / "ffxiii2.json").read_text(encoding="utf-8"))
    exe = game / manifest["executable_relative_path"]
    exe.parent.mkdir(parents=True)
    header = bytearray(0x80)
    struct.pack_into("<I", header, 0x3C, 0x80)
    exe.write_bytes(bytes(header) + b"PE\x00\x00" + bytes(20))

    out = run("ffxiii2", "--dry-run", "--skip-update-check", "--path", str(game)).stdout

    assert "found Final Fantasy XIII-2" in out and "NovaFix" in out, out
    assert "Leviathan" not in out, out


def test_revert_runs_without_a_game_installed():
    """also imported from the wrong module, and also never exercised"""
    run("ffxiii", "--revert")


def test_revert_without_a_game_argument_explains_itself():
    result = run("--revert", expect_zero=False)
    assert "say which game" in result.stdout


def test_no_command_path_imports_from_the_wrong_module():
    """
    the actual defect: a module-level name that exists somewhere else. every
    deferred import inside main() has to name a module that really exports it.
    """
    import ast
    import importlib

    tree = ast.parse((REPO / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("patcher"):
            continue
        module = importlib.import_module(node.module)
        for alias in node.names:
            assert hasattr(module, alias.name), (
                f"main.py imports {alias.name} from {node.module}, which does not have it")


def test_the_release_exe_takes_a_typed_command_instead_of_opening_the_window():
    """
    the exe is shell.py, and it used to hand everything but nxm:// to the
    window's own parser, so the --revert every recovery message names did
    nothing at all
    """
    result = subprocess.run([sys.executable, "shell.py", "ffxiii", "--doctor"],
                            cwd=REPO, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "unrecognized arguments" not in result.stderr
    assert result.stdout.strip(), "the doctor printed nothing"
