"""
what happens to a game between Apply and the next time the app opens: Steam's
Verify putting the original exe back, an update replacing it with a newer
build, a reinstall, a move to another library. The app finds it, a second
Apply settles it, and Revert never puts an older exe over a newer one.
"""
import shutil

import pytest

from patcher.revert import revert_all
from tests.test_premodded_installs import GAMES, REPO, defaults, exe, make_game


def newer_exe() -> bytes:
    return exe() + b"a steam update"


@pytest.mark.parametrize("game", list(GAMES))
@pytest.mark.parametrize("event", ["verify", "update", "reinstall", "moved"])
def test_the_game_is_found_settled_and_reverted_to_what_steam_last_put_there(tmp_path, monkeypatch,
                                                                              game, event):
    from patcher.bridge import UiBridge

    monkeypatch.setattr("patcher.bridge.state_path_for", lambda gid: tmp_path / "state" / f"{gid}.json")
    monkeypatch.setattr("patcher.paths.persistent_data_root", lambda: tmp_path / "data")
    first, second = tmp_path / "SteamA", tmp_path / "SteamB"
    root = make_game(first, game, {})
    (second / "steamapps" / "common").mkdir(parents=True)
    paths = {p.stem: p for p in sorted((REPO / "manifests").glob("*.json"))}
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[first, second])
    assert bridge.apply(game, defaults(bridge, game))["ok"]

    if event == "verify":
        (root / GAMES[game][3]).write_bytes(exe())
    elif event == "update":
        (root / GAMES[game][3]).write_bytes(newer_exe())
    elif event == "reinstall":
        shutil.rmtree(root)
        root = make_game(first, game, {})
    else:
        acf = f"appmanifest_{GAMES[game][1]}.acf"
        shutil.move(str(first / "steamapps" / acf), str(second / "steamapps" / acf))
        root = shutil.move(str(root), str(second / "steamapps" / "common" / root.name))
    root = type(first)(root)

    later = UiBridge(manifest_paths=paths, steam_library_paths=[first, second])
    found = later.scan(game)
    assert found.get("found") and str(root) in found.get("path", ""), found
    assert later.apply(game, defaults(later, game))["ok"]

    reverted = revert_all(root, later.manifest_for(game), tmp_path / "state" / f"{GAMES[game][0]}.json")

    assert all(r.ok for r in reverted), [(r.label, r.detail) for r in reverted]
    assert (root / GAMES[game][3]).read_bytes() == (newer_exe() if event == "update" else exe())
