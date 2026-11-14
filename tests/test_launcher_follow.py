"""
Steam's own Play button, after the patcher has been at the game.

XIII and XIII-2 start behind Square Enix's launcher whichever way they're
launched. Only the patcher's Launch used to tick its "don't show" box, so
pressing Apply and then starting from Steam put the launcher's resolution and
shadow settings in front of a game whose graphics NovaFix overrides: two places
to set the same thing, and the one on screen losing. Now the box follows who
owns the graphics, from Apply, a game switch, the Settings drawer and Revert,
and a window the user brings back by hand stays back.
"""

import threading
from pathlib import Path

import pytest

from patcher import launcher_skip
from patcher.manifest import load_manifest
from tests.test_hd2_end_to_end import build_world

REPO = Path(__file__).resolve().parent.parent
SETUP = (b'<?xml version="1.0" encoding="utf-8"?>\r\n<SaveData>\r\n'
         b"<window_mode>1</window_mode>\r\n<voice_mode>1</voice_mode>\r\n"
         b"<confirm_mode>false</confirm_mode>\r\n</SaveData>\r\n")
TICKED = SETUP.replace(b">false<", b">true<")


@pytest.fixture
def world(tmp_path, monkeypatch):
    api, _, game, _ = build_world(tmp_path, monkeypatch)
    (game / "setup.xml").write_bytes(SETUP)
    return api, game, tmp_path / "state" / "ffxiii2.json"


def hidden(game) -> bool:
    return b"<confirm_mode>true</confirm_mode>" in (game / "setup.xml").read_bytes()


def ours(game) -> bool:
    """the marker saying Apply hid it, which is what Revert goes by"""
    return (game / ".patcher-undo" / launcher_skip.HIDDEN).is_file()


def novafix(api):
    went = api._patcher().apply("ff13-2", ["ff132fix"])
    assert went["ok"], went


def switch_to(api):
    """a game switch, waiting out the launcher check it starts off the UI thread"""
    api.scan("ff13-2")
    if api._settling is not None:
        api._settling.join(10)


def revert(api, game, state):
    from patcher.revert import revert_all

    results = revert_all(game, api._patcher().manifest_for("ff13-2"), state)
    return results, [r for r in results if r.label == "Square Enix launcher"]


def test_apply_hides_the_launcher_so_steams_play_skips_it(world):
    api, game, _ = world

    novafix(api)

    assert hidden(game) and ours(game)
    # the rest of the file is the user's and isn't reformatted
    assert (game / "setup.xml").read_bytes() == TICKED


def test_a_game_switch_catches_a_game_patched_before_this(world):
    """Apply greys out on a game that's done, so opening it is the next chance"""
    api, game, _ = world
    novafix(api)
    (game / "setup.xml").write_bytes(SETUP)
    (game / ".patcher-undo" / launcher_skip.HIDDEN).unlink()

    switch_to(api)

    assert hidden(game)


def test_nothing_is_hidden_without_our_novafix_on_the_game(world):
    """with no override the launcher's window is the only place graphics change"""
    api, game, _ = world

    switch_to(api)

    assert not hidden(game) and not ours(game)


def test_a_launcher_brought_back_by_hand_stays_back(world):
    """
    the voice language used to only live in that window, and editing setup.xml
    is what every guide online says. Hiding it again on the next game switch is
    a fight
    """
    api, game, _ = world
    novafix(api)
    (game / "setup.xml").write_bytes(SETUP)

    switch_to(api)
    api._silence_launcher("ff13-2")
    novafix(api)
    assert not hidden(game)

    # until they ask the patcher for the graphics again
    api.save_novafix_settings("ff13-2", {"Display": {"GraphicsSource": "This Patcher"}})
    assert hidden(game) and ours(game)


def another_novafix(game):
    """
    a NovaFix, just not the build this version pins: the one an earlier release
    installed, or a newer one dropped in by hand. Same ini, same override
    """
    dll = game / "alba_data" / "prog" / "win" / "bin" / "d3d9.dll"
    dll.write_bytes(dll.read_bytes() + b"\0another build")


def test_a_novafix_that_isnt_our_build_still_overrides(world):
    """read as NovaFix gone, a game switch or Launch put the second settings window back"""
    api, game, _ = world
    novafix(api)
    another_novafix(game)

    switch_to(api)
    api._silence_launcher("ff13-2")

    assert hidden(game) and ours(game)


def test_an_update_that_pins_a_newer_novafix_leaves_it_hidden(world):
    """nothing on disk changes, the next release just pins a different build"""
    api, game, _ = world
    novafix(api)
    proxy = next(p for p in api._patcher().manifest_for("ff13-2").patches
                 if p.id == "install_novafix")
    proxy.bundled_files[0]["sha256"] = "0" * 64

    switch_to(api)

    assert hidden(game) and ours(game)


def test_a_launcher_brought_back_stays_back_through_a_novafix_update(world):
    api, game, _ = world
    novafix(api)
    (game / "setup.xml").write_bytes(SETUP)
    switch_to(api)
    another_novafix(game)
    switch_to(api)

    novafix(api)

    assert not hidden(game)


def test_revert_leaves_a_box_ticked_after_the_launcher_was_brought_back(world):
    """back on screen, then the user ticks the box themselves: that tick is theirs"""
    api, game, state = world
    novafix(api)
    (game / "setup.xml").write_bytes(SETUP)
    switch_to(api)
    (game / "setup.xml").write_bytes(TICKED)

    _, line = revert(api, game, state)

    assert hidden(game) and not line, line


def test_a_drawer_pick_waits_for_a_game_switch_already_writing(world, monkeypatch):
    """
    the switch's write held by the launcher or antivirus, and the user picks
    The Game's Launcher meanwhile. Interleaved, the switch's late write hid the
    window right after they asked for it
    """
    api, game, _ = world
    novafix(api)
    (game / "setup.xml").write_bytes(SETUP)
    (game / ".patcher-undo" / launcher_skip.HIDDEN).unlink()
    held, reached = threading.Event(), threading.Event()
    real = launcher_skip.write_atomic

    def slow(path, data, *args, **kwargs):
        if Path(path).name == "setup.xml" and threading.current_thread() is api._settling:
            reached.set()
            held.wait(10)
        return real(path, data, *args, **kwargs)

    monkeypatch.setattr(launcher_skip, "write_atomic", slow)
    api.scan("ff13-2")
    assert reached.wait(10)
    pick = threading.Thread(target=api.save_novafix_settings, args=(
        "ff13-2", {"Display": {"GraphicsSource": "The Game's Launcher"}}))
    pick.start()
    pick.join(0.5)
    assert pick.is_alive(), "the pick didn't wait for the write already under way"

    held.set()
    pick.join(10)
    api._settling.join(10)

    assert not hidden(game)


def test_handing_the_graphics_to_the_launcher_brings_it_back(world):
    api, game, _ = world
    novafix(api)

    api.save_novafix_settings("ff13-2", {"Display": {"GraphicsSource": "The Game's Launcher"}})
    assert not hidden(game) and not ours(game)
    # and nothing that runs later hides it under them again
    switch_to(api)
    api._silence_launcher("ff13-2")
    assert not hidden(game)

    api.save_novafix_settings("ff13-2", {"Display": {"GraphicsSource": "This Patcher"}})
    assert hidden(game) and ours(game)


def test_a_graphics_change_takes_them_back_from_the_launcher(world):
    """changing a quality row turns the override on, and that hides it again"""
    api, game, _ = world
    novafix(api)
    api.save_novafix_settings("ff13-2", {"Display": {"GraphicsSource": "The Game's Launcher"}})

    api.save_novafix_settings("ff13-2", {"Quality": {"ShadowResolution": "4096"}})

    assert hidden(game)


def test_picking_the_launcher_shows_it_even_if_the_user_hid_it(world):
    """the one moment they've asked for the window, and without it the choice does nothing"""
    api, game, _ = world
    (game / "setup.xml").write_bytes(TICKED)

    api.save_novafix_settings("ff13-2", {"Display": {"GraphicsSource": "The Game's Launcher"}})

    assert not hidden(game)


def test_an_unrelated_save_leaves_a_box_the_user_ticked(world):
    """only picking a Graphics Source is a request about its window"""
    api, game, _ = world
    api.save_novafix_settings("ff13-2", {"Display": {"GraphicsSource": "The Game's Launcher"}})
    (game / "setup.xml").write_bytes(TICKED)

    api.save_novafix_settings("ff13-2", {"Performance": {"ShowFpsCounter": "On"}})

    assert hidden(game)


def test_revert_shows_the_launcher_apply_hid(world):
    api, game, state = world
    novafix(api)

    _, line = revert(api, game, state)

    assert not hidden(game) and not ours(game)
    assert line and line[0].ok and "shows again" in line[0].detail, line


def test_revert_leaves_a_box_the_user_ticked_themselves(world):
    """the same flag on disk, and nothing of ours says we set it"""
    api, game, state = world
    (game / "setup.xml").write_bytes(TICKED)
    novafix(api)
    assert not ours(game), "a flag that was already set was claimed"

    _, line = revert(api, game, state)

    assert hidden(game) and not line


def test_revert_never_hides_it(world, monkeypatch):
    """
    NovaFix still on after its own revert failed, the game holding d3d9.dll,
    used to send Revert down the hide path and then report the launcher shows
    again
    """
    api, game, state = world
    (game / "setup.xml").unlink()
    novafix(api)
    (game / "setup.xml").write_bytes(SETUP)
    real = Path.unlink

    def held(self, *args, **kwargs):
        if self.name.lower() == "d3d9.dll":
            raise PermissionError(13, "in use by another process", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", held)

    _, line = revert(api, game, state)

    assert not hidden(game) and not ours(game) and not line, line


def test_revert_with_nothing_recorded_says_nothing_about_it(world):
    """our NovaFix still there and no record of it reads as nothing to undo, not a hide"""
    api, game, state = world
    (game / "setup.xml").unlink()
    novafix(api)
    (game / "setup.xml").write_bytes(SETUP)
    state.unlink()

    results, _ = revert(api, game, state)

    assert results == [] and not hidden(game)


def test_a_setup_xml_revert_cannot_read_keeps_the_record(world, monkeypatch):
    """a read error isn't nothing to do: the marker stays so the next Revert can finish"""
    api, game, state = world
    novafix(api)
    real = Path.read_bytes

    def busy(self):
        if self.name == "setup.xml":
            raise PermissionError(13, "sharing violation", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", busy)
    _, line = revert(api, game, state)
    monkeypatch.setattr(Path, "read_bytes", real)

    assert line and not line[0].ok and "could not read setup.xml" in line[0].detail, line
    assert ours(game) and hidden(game)
    assert launcher_skip.follow(game, api._patcher().manifest_for("ff13-2"),
                                may_hide=False).changed
    assert not hidden(game) and not ours(game)


def test_an_unreadable_novafix_ini_leaves_it_alone(world, monkeypatch):
    """can't tell who owns the graphics, so nothing is decided for them"""
    api, game, _ = world
    real = Path.read_text

    def busy(self, *args, **kwargs):
        if self.name == "NovaFix.ini":
            raise PermissionError(13, "sharing violation", str(self))
        return real(self, *args, **kwargs)

    novafix(api)
    (game / "setup.xml").write_bytes(SETUP)
    (game / ".patcher-undo" / launcher_skip.HIDDEN).unlink()
    ini = game / "alba_data" / "prog" / "win" / "bin" / "NovaFix.ini"
    ini.write_text("[Graphics]\nOverrideLauncherGraphics=Off\n")
    monkeypatch.setattr(Path, "read_text", busy)

    result = launcher_skip.follow(game, api._patcher().manifest_for("ff13-2"))

    assert not result.changed and not result.failed and not hidden(game)


def test_a_game_never_started_waits_for_its_launcher_to_write_the_file(world):
    """setup.xml is the launcher's to create, so the first start still shows it once"""
    api, game, _ = world
    (game / "setup.xml").unlink()

    novafix(api)
    assert not (game / "setup.xml").exists() and not ours(game)

    (game / "setup.xml").write_bytes(SETUP)
    switch_to(api)
    assert hidden(game)


def test_the_command_line_apply_hides_it_too(tmp_path, monkeypatch):
    from patcher.app import run_full_automated_patch

    api, _, game, _ = build_world(tmp_path, monkeypatch)
    (game / "setup.xml").write_bytes(SETUP)

    run_full_automated_patch(api._patcher().manifest_for("ff13-2"),
                             state_path=tmp_path / "cli-state.json", manual_path=game,
                             steam_library_paths=[], enabled_patch_ids={"install_novafix"})

    assert hidden(game)


def test_lightning_returns_is_never_touched(tmp_path):
    """it starts straight into the game, so there's no window and no file to edit"""
    lr = load_manifest(REPO / "manifests" / "ffxiii3.json")
    (tmp_path / "setup.xml").write_bytes(SETUP)

    result = launcher_skip.follow(tmp_path, lr)

    assert not result.changed and (tmp_path / "setup.xml").read_bytes() == SETUP


@pytest.mark.parametrize("ini, owns", [
    ("[Graphics]\nOverrideLauncherGraphics=Off\n", True),
    ("[Graphics]\nOverrideLauncherGraphics=0\n", True),
    ("[Graphics]\nOverrideLauncherGraphics=On\n", False),
    ("[Graphics]\nOverrideLauncherGraphics=1\n", False),
    ("[Graphics]\nOverrideLauncherGraphics=false\n", False),
    ("[Graphics]\nDisplayMode=Borderless\n", False),
    # what GetPrivateProfileString does with a hand edit
    ("[graphics]\nOverrideLauncherGraphics=Off\n", True),
    ("[Graphics]\noverridelaunchergraphics=off\n", True),
    ('[Graphics]\nOverrideLauncherGraphics="Off"\n', True),
    ("[Graphics]\nOverrideLauncherGraphics=Off\nOverrideLauncherGraphics=On\n", True),
    ("[Graphics]\nOverrideLauncherGraphics=00\n", True),
    ("[Graphics]\nOverrideLauncherGraphics=2\n", False),
    ("[Performance]\nOverrideLauncherGraphics=Off\n[Graphics]\nMSAA=4\n", False),
])
def test_who_owns_the_graphics_is_read_the_way_novafix_reads_it(tmp_path, ini, owns):
    """Off, or a 0, and anything else falls back to its default of overriding"""
    manifest = load_manifest(REPO / "manifests" / "ffxiii2.json")
    proxy = next(p for p in manifest.patches if p.id == "install_novafix")
    path = tmp_path / proxy.install_relative_path / "NovaFix.ini"
    path.parent.mkdir(parents=True)
    path.write_text(ini)

    assert launcher_skip.owns_graphics(tmp_path, proxy) is owns
