"""
The XIII-2 install against the real ff13tool, when there is one to run.

Every archive step goes through the ff13tool.exe FF XIII-2 HD ships: unpacking
white_img, repacking it, unpacking a zone, importing both halves of each block
and importing the zone's index into the game's white_img. The injector and the
model tool stay stubs, because they need the game's real art to do anything.

Skipped unless FF13TOOL names that exe. On Windows it runs as it is; anywhere
else it needs wine. From a checkout, with the mod unpacked to C:\\FF132HD:

    set FF13TOOL=C:\\FF132HD\\supportfiles\\ff13tool.exe
    py -3 -m pytest tests/test_hd2_real_tool.py
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

from tests.conftest import Log
from tests.hd2_world import (categories, entry_bytes, make_game, make_mod, pin_vanilla,
                             zone_entry_bytes)

REAL = Path(os.environ.get("FF13TOOL", ""))
WINE = shutil.which("wine")

pytestmark = pytest.mark.skipif(
    not REAL.name or not REAL.is_file() or (sys.platform != "win32" and not WINE),
    reason="FF13TOOL does not name an ff13tool.exe that can run here")


def use_the_real_tool(support: Path):
    from patcher.mods import hd_runner

    target = support / hd_runner.TOOLS["pack"]
    target.unlink(missing_ok=True)
    if sys.platform == "win32":
        shutil.copyfile(REAL, target)
        return
    target.write_text(f'#!/bin/sh\nexec "{WINE}" "{REAL}" "$@"\n', encoding="utf-8")
    target.chmod(0o755)


def test_the_whole_install_with_the_real_ff13tool(tmp_path, monkeypatch):
    from patcher.archive import crypt
    from patcher.mods import hd2_install
    from patcher.mods.hd2_recipe import detect
    from patcher.mods.hd_install import restore, restore_prep

    monkeypatch.setenv("WINEDEBUG", "-all")
    game = make_game(tmp_path)
    root = make_mod(tmp_path, monkeypatch)
    use_the_real_tool(root / "supportfiles")
    pin_vanilla(monkeypatch, game)
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    files = ("alba_data/sys/filelistu.win32.bin", "alba_data/sys/white_imgu.win32.bin",
             "alba_data/zone/white_z0010u_img.win32.bin")
    before = {rel: (game / rel).read_bytes() for rel in files}
    seen = Log()

    result = hd2_install.install(game, detect(root), categories(),
                                 {c["id"] for c in categories()}, game / ".patcher-undo",
                                 on_line=seen)

    assert result.ok, (result.errors, result.lines, list(seen)[-20:])
    assert any("Game set to FF13-2" in line for line in seen), "that was not the real tool"
    assert b"HDMODEL" in entry_bytes(game, "chr/pc/c108/bin/c108.win32.trb")
    assert b"HD" in zone_entry_bytes(game, "z0010", "bg/loc001/a001/bin/block001.win32.imgb")
    assert b"HD" in zone_entry_bytes(game, "z0010", "bg/deep/x/y/bin/rock.win32.trb")
    raw = (game / "alba_data" / "sys" / "filelistu.win32.bin").read_bytes()
    assert crypt.is_encrypted(raw) and crypt.checksum(crypt.decrypt(raw)) != 0

    restore(restore_prep(game, game / ".patcher-undo"))
    assert {rel: (game / rel).read_bytes() for rel in files} == before
