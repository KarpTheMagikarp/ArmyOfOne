"""
NovaFix's own menu, which ` and F10 opened mid game. Everything a player needs
out of it is in the Settings drawer now, so its keys go off from Apply, a game
switch and the drawer's Apply, the way Square Enix's launcher flag does, and a
key somebody set themselves stays theirs. Its add-on popup goes to every launch
the same way.
"""

from patcher import novafix_config as cfg
from tests.test_launcher_follow import novafix, switch_to, world  # noqa: F401


def ini(game):
    return game / "alba_data" / "prog" / "win" / "bin" / "NovaFix.ini"


def keys(game) -> tuple:
    found = cfg.read(ini(game)).get("Input", {})
    return tuple(found.get(k) for k in ("OverlayHotkey", "AlternateOverlayHotkey",
                                        "OverlayGamepadHotkey"))


QUIET = ("F8", "Disabled", "Disabled")
DEFAULTS = ("[Input]\r\nOverlayHotkey=F10\r\nAlternateOverlayHotkey=Grave\r\n"
            "OverlayGamepadHotkey=Both\r\n")


def test_apply_turns_novafixs_menu_keys_off(world):
    api, game, _ = world
    novafix(api)
    assert keys(game) == QUIET


def test_a_game_switch_catches_a_novafix_from_before(world):
    """the user's own case: NovaFix in long before this, nothing left to Apply"""
    api, game, _ = world
    novafix(api)
    ini(game).write_text(DEFAULTS)
    (game / ".patcher-undo" / cfg.QUIETED).unlink()
    switch_to(api)
    assert keys(game) == QUIET


def test_keys_put_back_by_hand_stay_put_back(world):
    api, game, _ = world
    novafix(api)
    ini(game).write_text(DEFAULTS)
    for _ in range(3):
        switch_to(api)
    novafix(api)
    assert keys(game) == ("F10", "Grave", "Both")


def test_a_d3d9_dll_that_isnt_novafix_gets_no_novafix_ini(world):
    """FF13Fix, DXVK and ReShade all go in as d3d9.dll"""
    api, game, _ = world
    proxy = ini(game).parent / "d3d9.dll"
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(b"MZ" + b"\0" * 4096)
    switch_to(api)
    assert not ini(game).exists()


def test_revert_puts_them_back(world):
    from tests.test_launcher_follow import revert

    api, game, state = world
    novafix(api)
    # NovaFix's first launch, filling in the rest
    cfg.write(ini(game), {"Graphics": {"DisplayMode": "Borderless"}})
    revert(api, game, state)
    assert keys(game) == ("F10", "Grave", "Both")
    assert not (game / ".patcher-undo" / cfg.QUIETED).exists()


def test_revert_before_novafix_ever_ran_leaves_no_ini(world):
    from tests.test_launcher_follow import revert

    api, game, state = world
    novafix(api)
    revert(api, game, state)
    assert not ini(game).exists()


def test_nothing_is_written_without_novafix_beside_the_game(world):
    api, game, _ = world
    switch_to(api)
    assert not ini(game).exists()


def test_a_key_somebody_chose_stays(world):
    api, game, _ = world
    novafix(api)
    ini(game).write_text("[Input]\r\nOverlayHotkey=Insert\r\nAlternateOverlayHotkey=Grave\r\n")
    switch_to(api)
    novafix(api)
    api.save_novafix_settings("ff13-2", {"Performance": {"FrameRateLimit": "60"}})
    assert keys(game)[:2] == ("Insert", "Grave")



def test_our_novafix_still_on_after_its_revert_failed_keeps_its_keys_off(world):
    """
    the running game holding it, most often, and its record still says it's
    on: putting ` and F10 back would be for nobody
    """
    from patcher import revert as revert_mod

    api, game, state = world
    novafix(api)
    cfg.write(ini(game), {"Graphics": {"DisplayMode": "Borderless"}})
    assert revert_mod._ini_back(game, api._patcher().manifest_for("ff13-2"), None, state) == []
    assert keys(game) == QUIET
    assert (game / ".patcher-undo" / cfg.QUIETED).exists()


def test_the_users_own_novafix_the_same_as_ours_gets_its_keys_back(world):
    """
    one they put in themselves, byte for byte ours, so Apply had nothing to
    record: it read as ours and Revert left its keys off for good
    """
    from tests.test_launcher_follow import REPO, revert

    api, game, state = world
    dll = ini(game).parent / "d3d9.dll"
    dll.parent.mkdir(parents=True, exist_ok=True)
    dll.write_bytes((REPO / "assets" / "mods" / "novafix" / "d3d9.dll").read_bytes())
    ini(game).write_text(DEFAULTS)
    switch_to(api)
    assert keys(game) == QUIET
    revert(api, game, state)
    assert keys(game) == ("F10", "Grave", "Both")
    switch_to(api)
    assert keys(game) == ("F10", "Grave", "Both")


def test_the_users_own_novafix_after_revert_keeps_its_keys_and_ours_starts_over(world):
    """
    one Revert left beside the game is theirs, keys and all; ours put back on
    later is a different dll, and turns them off again
    """
    from patcher.state import digest_of

    api, game, _ = world
    novafix(api)
    dll = ini(game).parent / "d3d9.dll"
    ini(game).write_text(DEFAULTS)
    store = game / ".patcher-undo"
    theirs = dll.read_bytes() + b"theirs"
    dll.write_bytes(theirs)
    cfg.put_back(game, "alba_data/prog/win/bin", store, stays=digest_of(dll))
    switch_to(api)
    assert keys(game) == ("F10", "Grave", "Both")

    dll.write_bytes(theirs + b" and ours since")
    switch_to(api)
    assert keys(game) == QUIET


def popup(game):
    return cfg.read(ini(game)).get("Performance", {}).get("LoadedModulesNotificationMode")


def test_apply_has_novafix_list_its_add_ons_only_when_they_change(world):
    api, game, _ = world
    novafix(api)
    assert popup(game) == "WhenChanged"


def test_a_game_switch_catches_one_whose_menu_was_looked_at_before_the_popup(world):
    """the user's own case: XIII-2 patched by the last version, with NovaFix's first launch long done"""
    api, game, _ = world
    novafix(api)
    ini(game).write_text(DEFAULTS + "\r\n[Performance]\r\nLoadedModulesNotificationMode=EveryLaunch\r\n")
    (game / ".patcher-undo" / cfg.POPPED).unlink()
    switch_to(api)
    assert popup(game) == "WhenChanged"
    # the menu had its look, and F10 put back by hand since is theirs
    assert keys(game) == ("F10", "Grave", "Both")


def test_revert_puts_the_popup_back(world):
    from tests.test_launcher_follow import revert

    api, game, state = world
    novafix(api)
    cfg.write(ini(game), {"Graphics": {"DisplayMode": "Borderless"}})
    revert(api, game, state)
    # it was missing, which NovaFix 1.0.2 reads as its default
    assert popup(game) == "EveryLaunch"
    assert not (game / ".patcher-undo" / cfg.POPPED).exists()


def test_the_drawers_save_catches_one_whose_menu_was_looked_at_before_the_popup(world):
    api, game, _ = world
    novafix(api)
    (game / ".patcher-undo" / cfg.POPPED).unlink()
    cfg.write(ini(game), {"Performance": {"LoadedModulesNotificationMode": "EveryLaunch"}})
    api.save_novafix_settings("ff13-2", {"Performance": {"FrameRateLimit": "60"}})
    assert popup(game) == "WhenChanged"


def test_lrs_novafix_ini_in_its_root_gets_the_look_and_revert_puts_it_back(tmp_path, monkeypatch):
    """LR's NovaFix sits beside LRFF13.exe, in the game's root"""
    from patcher import revert as revert_mod
    from tests.test_reshade_bundle import build

    bridge, home, tmp = build(tmp_path, monkeypatch, "lr")
    root = home.parent
    (root / "NovaFix.ini").write_text("[Input]\r\nOverlayHotkey=F10\r\n\r\n"
                                       "[Performance]\r\nLoadedModulesNotificationMode=WhenChanged\r\n")
    assert bridge.settle_novafix("lr")
    values = cfg.read(root / "NovaFix.ini")
    assert values["Performance"] == cfg.POPUP_KEYS and values["Input"]["OverlayHotkey"] == "F8"

    # Revert with no NovaFix of ours on record: theirs stays, and so do its keys' undo
    assert revert_mod._ini_back(root, bridge.manifest_for("lr"), None,
                                tmp / ".state" / "ffxiii3.json") == []
    values = cfg.read(root / "NovaFix.ini")
    assert values["Performance"]["LoadedModulesNotificationMode"] == "WhenChanged"
    assert values["Input"]["OverlayHotkey"] == "F10"
