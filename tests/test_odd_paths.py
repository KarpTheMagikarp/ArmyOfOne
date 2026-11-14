"""
Steam libraries live wherever people put them: under a username with an
umlaut, in a folder called Игры or ゲーム, behind an apostrophe, an ampersand
or a percent sign, in Program Files (x86). The whole Apply and Revert goes
through each, on CI's Windows runner as well, where the ANSI codepage is the
one any missing encoding falls back to.
"""
import hashlib
from pathlib import Path

import pytest

from patcher.revert import revert_all
from tests.test_premodded_installs import GAMES, REPO, defaults, make_game, someone_elses

PLACES = [
    "Jürgen's Spiele & Mods",
    "Игры",
    "ゲーム ライブラリ",
    "100% Games #1",
    "Program Files (x86)",
    "a;b,c=d [e] {f} ~g",
]


@pytest.mark.parametrize("place", PLACES)
@pytest.mark.parametrize("game", list(GAMES))
def test_apply_and_revert_wherever_the_library_is(tmp_path, monkeypatch, place, game):
    from patcher.bridge import UiBridge

    monkeypatch.setattr("patcher.bridge.state_path_for", lambda gid: tmp_path / place / "state" / f"{gid}.json")
    monkeypatch.setattr("patcher.paths.persistent_data_root", lambda: tmp_path / place / "data")
    library = tmp_path / place / "Steam"
    library.mkdir(parents=True)
    root = make_game(library, game, {"d3d9.dll": someone_elses(b"theirs")})
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    paths = {p.stem: p for p in sorted((REPO / "manifests").glob("*.json"))}
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])

    found = bridge.scan(game)
    assert found.get("found"), found
    assert place in found.get("path", "")
    went = bridge.apply(game, defaults(bridge, game))
    assert went["ok"], went
    assert bridge.novafix_settings(game).get("ok") is not False

    revert_all(root, bridge.manifest_for(game), tmp_path / place / "state" / f"{GAMES[game][0]}.json")
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before
