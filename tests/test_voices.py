"""
The voice language, in the Settings drawer now that Square Enix's launcher
stays hidden, and the HD panel's box for the other voice set.

voice_mode in setup.xml is the one thing that window still owned once NovaFix
took the graphics, and it isn't cosmetic: it picks which copy of the character
archives the game loads, which is also the copy an HD install went into. The
other copy only gets HD when its box is ticked, since that's a second backup
and a second copy of the art, and a switch is only ever the launcher's write.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from patcher import voices
from patcher.mods import hd_install
from patcher.paths import undo_store_for

XIII, XIII2, LR = "292120", "292140", "345350"
SETUP = (b'<?xml version="1.0" encoding="utf-8"?>\r\n<SaveData>\r\n'
         b"<window_mode>1</window_mode>\r\n<voice_mode>1</voice_mode>\r\n"
         b"<confirm_mode>true</confirm_mode>\r\n</SaveData>\r\n")
JAPANESE = SETUP.replace(b">1</voice", b">0</voice")


def game(tmp_path, sets="uc", setup=SETUP):
    """XIII-2 with the voice sets asked for, and the launcher's file unless it's None"""
    from tests.hd2_world import make_game

    for voice in sets:
        root = make_game(tmp_path, variant=voice)
    if setup is not None:
        (root / "setup.xml").write_bytes(setup)
    return root


def hd_in(root, *sets):
    """the HD Project's backup beside the game, and the sets named grown past it"""
    backup = undo_store_for(root) / "hdproject"
    backup.mkdir(parents=True, exist_ok=True)
    sys_dir = root / "alba_data" / "sys"
    for name in [p.name for p in sys_dir.iterdir()]:
        if not (backup / name).exists():
            shutil.copyfile(sys_dir / name, backup / name)
    for voice in sets:
        with (sys_dir / f"white_img{voice}.win32.bin").open("ab") as archive:
            archive.write(b"HD" * 512)


def test_the_row_reads_what_the_launcher_set(tmp_path):
    root = game(tmp_path)

    row = voices.row(root, XIII2)
    assert (row["section"], row["label"]) == ("Language", "Voices")
    assert row["choices"] == ["English", "Japanese"] and row["value"] == "English"
    assert not row["unavailable"]

    (root / "setup.xml").write_bytes(JAPANESE)
    assert voices.row(root, XIII2)["value"] == "Japanese"


def xiii(tmp_path, scr="uc", img="uc"):
    """XIII with the character pair in the sets `scr` names, the image pair in `img`'s"""
    from tests.test_hd_install import make_game

    root = make_game(tmp_path, "u")
    sys_dir = root / "white_data" / "sys"
    for voice in "uc":
        names = (["filelist_scr{}.win32.bin", "white_scr{}.win32.bin"] if voice in scr else []) \
              + (["filelist{}.win32.bin", "white_img{}.win32.bin"] if voice in img else [])
        for name in names:
            if not (sys_dir / name.format(voice)).exists():
                (sys_dir / name.format(voice)).write_bytes(b"x")
    if "u" not in scr:
        for name in ("filelist_scru.win32.bin", "white_scru.win32.bin"):
            (sys_dir / name).unlink()
    (root / "setup.xml").write_bytes(SETUP)
    return root


def test_xiii_counts_its_own_archives(tmp_path):
    """XIII rebuilds its scr pair, and each language has a white_img pair beside it too"""
    assert voices.row(xiii(tmp_path), XIII)["choices"] == ["English", "Japanese"]


def test_a_set_missing_its_image_files_isnt_offered(tmp_path):
    """the game loads the whole set, so half of one is nothing to switch to"""
    root = xiii(tmp_path, img="u")

    assert voices.row(root, XIII)["unavailable"] == "English Only"
    assert not voices.save(root, XIII, "Japanese")["ok"]


def test_a_launcher_pointing_at_missing_voices_can_be_put_right(tmp_path):
    """
    one set on disk and voice_mode naming the other leaves the game nothing to
    load, and the language that is there is the way out, so it stays offered
    """
    root = game(tmp_path, sets="u", setup=JAPANESE)

    row = voices.row(root, XIII2)
    assert not row["unavailable"] and row["choices"] == ["English"], row
    assert "nothing to load" in row["note"]

    said = voices.save(root, XIII2, "English")
    assert said["ok"] and said["changed"], said
    assert voices.row(root, XIII2)["unavailable"] == "English Only"


def test_lightning_returns_has_no_voice_row(tmp_path):
    """it starts straight into the game: no launcher, so no setup.xml to keep voices in"""
    assert voices.row(game(tmp_path), LR) is None


def test_a_switch_changes_voice_mode_and_nothing_else(tmp_path):
    root = game(tmp_path)

    said = voices.save(root, XIII2, "Japanese")

    assert said["ok"] and said["changed"], said
    # the rest of the file is the user's, CRLF and all
    assert (root / "setup.xml").read_bytes() == JAPANESE
    # and the archives the game loads follow it, which is what an HD install asks
    assert hd_install.find_variant(root) == "c"

    again = voices.save(root, XIII2, "Japanese")
    assert again["ok"] and not again["changed"]


def test_a_switch_waits_out_the_launcher_check(tmp_path, monkeypatch):
    """
    both rewrite setup.xml whole, so a game switch's check landing mid-save
    would write the old voice back over the new one
    """
    from patcher import launcher_skip

    root = game(tmp_path)
    held, real = [], launcher_skip._rewrite

    def watched(*args):
        held.append(launcher_skip._FOLLOWING.locked())
        return real(*args)

    monkeypatch.setattr(launcher_skip, "_rewrite", watched)
    voices.save(root, XIII2, "Japanese")

    assert held == [True]


def test_before_the_first_launch_there_is_nothing_to_set(tmp_path):
    """
    the launcher writes setup.xml the first time it comes up, and asks for the
    voices itself then. Writing one from scratch means guessing a schema whose
    other values are undocumented enum indices.
    """
    root = game(tmp_path, setup=None)

    row = voices.row(root, XIII2)
    assert row["unavailable"] and row["note"]

    said = voices.save(root, XIII2, "Japanese")
    assert not said["ok"] and "first time" in said["error"], said
    assert not (root / "setup.xml").exists()


def test_a_setup_xml_without_the_setting_is_left_to_the_launcher(tmp_path):
    root = game(tmp_path, setup=b"<SaveData>\r\n<window_mode>1</window_mode>\r\n</SaveData>\r\n")

    assert voices.row(root, XIII2)["unavailable"]
    said = voices.save(root, XIII2, "Japanese")
    assert not said["ok"]
    assert b"voice_mode" not in (root / "setup.xml").read_bytes()


def test_only_the_voices_on_disk_are_offered(tmp_path):
    """
    some installs carry one set, the Asian release among them, and pointing the
    game at a set that isn't there leaves it nothing to load
    """
    root = game(tmp_path, sets="u")

    assert voices.row(root, XIII2)["unavailable"] == "English Only"
    said = voices.save(root, XIII2, "Japanese")
    assert not said["ok"] and "Japanese" in said["error"], said
    assert (root / "setup.xml").read_bytes() == SETUP


def test_an_unknown_language_is_refused(tmp_path):
    root = game(tmp_path)

    said = voices.save(root, XIII2, "Klingon")

    assert not said["ok"] and (root / "setup.xml").read_bytes() == SETUP


def test_the_row_says_what_a_switch_leaves_behind(tmp_path):
    """
    the switch is the launcher's write either way, so all the row can do is
    say, before Apply, what the set it lands on is short of
    """
    root = game(tmp_path)
    hd_in(root, "u")

    def warned(gap):
        return voices.row(root, XIII2, gaps={"Japanese": gap})["warn"]

    assert warned(None) == {}
    none = warned({"from": "u", "mod": "FFXIII-2 HD", "none": True})["Japanese"]
    assert none.startswith("FFXIII-2 HD is only in the English voice files, and Japanese loads "
                           "its own"), none
    assert none.endswith("install it with Japanese Voices Too ticked."), none
    short = warned({"from": "u", "mod": "FFXIII-2 HD", "missing": ["NPCs"],
                    "older": ["Weapons"]})["Japanese"]
    assert short.startswith("The Japanese voice files are behind the English ones on FFXIII-2 "
                            "HD, without NPCs and with an older build of Weapons."), short

    said = voices.save(root, XIII2, "Japanese", gap={"from": "u", "none": True})
    assert said["ok"] and said["changed"] and said["notice"].startswith("The HD Project is only"), said
    assert (root / "setup.xml").read_bytes() == JAPANESE


def test_staying_put_is_never_a_warning(tmp_path):
    """already on Japanese with HD in English: that's today, not a switch"""
    root = game(tmp_path, setup=JAPANESE)
    hd_in(root, "u")

    row = voices.row(root, XIII2, gaps={"Japanese": {"from": "u", "none": True}})
    assert "Japanese" not in row["warn"]
    assert not voices.save(root, XIII2, "Japanese", gap={"from": "u", "none": True})["notice"]


def drawer_world(tmp_path, monkeypatch, japanese=None):
    """
    the real shell and bridge over XIII-2 with both voice sets on disk.
    `japanese(root)` changes the Japanese set before anything is installed
    """
    from tests.hd2_world import make_game
    from tests.test_hd2_end_to_end import build_world

    api, events, root, mod = build_world(tmp_path, monkeypatch)
    make_game(root.parent, variant="c")
    if japanese:
        japanese(root)
    (root / "setup.xml").write_bytes(SETUP)
    return api, root, mod, events


def novafix_ini(root):
    return root / "alba_data" / "prog" / "win" / "bin" / "NovaFix.ini"


def test_the_drawer_ends_on_the_voices(tmp_path, monkeypatch):
    api, _, _, _ = drawer_world(tmp_path, monkeypatch)

    loaded = api._patcher().novafix_settings("ff13-2")

    assert loaded["ok"], loaded
    *rest, last = loaded["settings"]
    assert (last["section"], last["key"], last["value"]) == ("Language", "Voices", "English")
    assert all(row["section"] != "Language" for row in rest)


def test_one_apply_writes_the_voices_and_the_graphics(tmp_path, monkeypatch):
    api, root, _, _ = drawer_world(tmp_path, monkeypatch)

    went = api.save_novafix_settings("ff13-2", {"Language": {"Voices": "Japanese"},
                                                "Quality": {"MSAA": "8"}})

    assert went["ok"] and went["written"], went
    assert (root / "setup.xml").read_bytes() == JAPANESE
    assert "MSAA=8" in novafix_ini(root).read_text(encoding="utf-8")
    assert went["changed"]["Language"] == ["Voices"]


def test_the_voices_alone_leave_novafix_ini_alone(tmp_path, monkeypatch):
    """setup.xml's, not an ini's: a voice switch is no reason to write NovaFix a file"""
    api, root, _, _ = drawer_world(tmp_path, monkeypatch)

    went = api.save_novafix_settings("ff13-2", {"Language": {"Voices": "Japanese"}})

    assert went["ok"] and went["written"], went
    assert not novafix_ini(root).exists()


def test_a_refused_voice_writes_nothing_at_all(tmp_path, monkeypatch):
    """a half applied panel is one the user has to work out the state of"""
    api, root, _, _ = drawer_world(tmp_path, monkeypatch)
    for name in ("filelistc.win32.bin", "white_imgc.win32.bin"):
        (root / "alba_data" / "sys" / name).unlink()

    went = api.save_novafix_settings("ff13-2", {"Language": {"Voices": "Japanese"},
                                                "Quality": {"MSAA": "8"}})

    assert not went["ok"] and "Japanese" in went["error"], went
    assert not novafix_ini(root).exists()
    assert (root / "setup.xml").read_bytes() == SETUP


def sys_files(root, voice):
    return {p.name: p.read_bytes() for p in (root / "alba_data" / "sys").glob(f"*{voice}.win32.bin")}


def install(api, events, mod, categories, voices=False):
    from tests.test_hd2_end_to_end import run_and_wait

    ending, hd = run_and_wait(api, events, mod, categories, voices=voices)
    assert ending["stage"] == "done", ending
    return hd


def copy_over(api, events, mod, expect="done"):
    """the voices box ticked with nothing else: what this set has, into the other one"""
    from tests.test_hd2_end_to_end import run_and_wait

    ending, hd = run_and_wait(api, events, mod, [], voices=True)
    assert ending["stage"] == expect, ending
    return ending, hd


def ended(events, timeout=60):
    """the HD run's last event, waited for"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        last = [e for e in events if e.get("stage") in ("done", "failed", "cancelled")]
        if last:
            return last[0]
        time.sleep(0.05)
    pytest.fail(f"the run never finished: {[e.get('stage') for e in events]}")


def lines_of(hd):
    return [e.get("text", "") for e in hd if e.get("stage") == "line"]


def instant(api, root, events, language, game="ff13-2"):
    """a switch is the launcher's write, there and then, and never starts a run"""
    events.clear()
    went = api.save_novafix_settings(game, {"Language": {"Voices": language}})
    assert went["ok"], went
    assert not [e for e in events if e.get("type") == "hd"], "a switch started a run"
    assert (root / "setup.xml").read_bytes() == (JAPANESE if language == "Japanese" else SETUP)
    return went


def test_a_voice_switch_waits_for_the_hd_install_writing_this_game(tmp_path, monkeypatch):
    """
    an install reads its voice set out of setup.xml when it starts and writes
    that set for the next hour. A switch mid-run leaves HD in the set the game
    stops loading, and nothing has grown yet for the row to warn about
    """
    import threading

    from patcher.mods import hd2_install

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    at_copy_back, release = threading.Event(), threading.Event()
    real = hd2_install._copy_back

    def held(*args, **kwargs):
        at_copy_back.set()
        release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(hd2_install, "_copy_back", held)
    assert api.install_hd_project("ff13-2", str(mod), ["weapons"], "add").get("ok")
    try:
        assert at_copy_back.wait(60), "the install never got to its copy back"

        row = api.novafix_settings("ff13-2")["settings"][-1]
        assert row["unavailable"] == "After the Install" and not row["warn"], row
        went = api.save_novafix_settings("ff13-2", {"Language": {"Voices": "Japanese"},
                                                    "Quality": {"MSAA": "8"}})
        assert not went["ok"] and "install" in went["error"], went
        assert (root / "setup.xml").read_bytes() == SETUP and not novafix_ini(root).exists()
        # the rest of the drawer isn't the install's business
        assert api.save_novafix_settings("ff13-2", {"Quality": {"MSAA": "8"}})["ok"]
    finally:
        release.set()
    assert ended(events)["stage"] == "done"

    # and once it's done the switch is the write, with a word on what it leaves
    assert "only in the English voice files" in instant(api, root, events, "Japanese")["notice"]


def test_without_the_box_the_other_voice_set_is_left_alone(tmp_path, monkeypatch):
    """
    the point of the box: an install goes into the voices the game is on and
    nowhere else, so the Japanese archives aren't backed up or grown, and the
    switch to them says what it leaves behind instead of copying anything
    """
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    japanese = sys_files(root, "c")
    zone = root / "alba_data" / "zone" / "white_z0010c_img.win32.bin"
    japanese_zone = zone.read_bytes()

    hd = install(api, events, mod, ["weapons", "zones"])

    assert hd_install.voices_with_hd(root) == {"u"}
    assert sys_files(root, "c") == japanese and zone.read_bytes() == japanese_zone
    backup = undo_store_for(root) / "hdproject"
    assert not [p.name for p in backup.iterdir() if "c.win32" in p.name or "c_img" in p.name]
    assert not [line for line in lines_of(hd) if line.startswith("[voices]")], lines_of(hd)

    row = api.novafix_settings("ff13-2")["settings"][-1]
    assert row["warn"]["Japanese"].startswith("FFXIII-2 HD is only in the English voice files")
    went = instant(api, root, events, "Japanese")
    said = row["warn"]["Japanese"].split(". To have it")[0]
    assert went["notice"].startswith(said) and said, (went, row)
    assert sys_files(root, "c") == japanese
    # and English is where it was, with nothing to say the other way
    assert not instant(api, root, events, "English").get("notice")


def test_the_box_puts_hd_in_both_voice_sets(tmp_path, monkeypatch):
    """
    ticked, the install's own run copies what it built into the Japanese
    files, so the switch afterwards is the launcher's one write either way,
    and Square Enix's own launcher switching it finds HD too
    """
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)

    hd = install(api, events, mod, ["weapons", "zones"], voices=True)

    assert any(line.startswith("[voices]") and "Japanese voice files have the HD Project too"
               in line for line in lines_of(hd)), lines_of(hd)
    assert hd_install.voices_with_hd(root) == {"u", "c"}
    from tests.hd2_world import entry_bytes, zone_entry_bytes
    weapon = "chr/weapon/w101/bin/w101.win32.imgb"
    assert entry_bytes(root, weapon, "c") == entry_bytes(root, weapon, "u")
    block = "bg/loc001/a001/bin/block001.win32.imgb"
    assert b"HD" in zone_entry_bytes(root, "z0010", block, "c")
    assert api.novafix_settings("ff13-2")["settings"][-1]["warn"] == {}

    english = sys_files(root, "u")
    assert not instant(api, root, events, "Japanese").get("notice")
    assert not instant(api, root, events, "English").get("notice")
    assert sys_files(root, "u") == english


def test_hd_in_both_sets_or_neither_leaves_nothing_behind(tmp_path, monkeypatch):
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    bridge = api._patcher()
    assert bridge.voice_gap("ff13-2", "Japanese") is None, "no HD anywhere"

    install(api, events, mod, ["weapons"], voices=True)

    assert bridge.voice_gap("ff13-2", "Japanese") is None
    instant(api, root, events, "Japanese")
    assert bridge.voice_gap("ff13-2", "English") is None


def test_the_box_alone_copies_what_these_voices_have(tmp_path, monkeypatch):
    """
    HD went in without it, and ticking it later with nothing else ticked is
    that copy on its own: no tools, English untouched, the voices left alone
    """
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "zones"])
    english = sys_files(root, "u")

    ending, hd = copy_over(api, events, mod)

    assert ending["copied"]["zones"] == 1 and ending["copied"]["files"], ending
    assert ending["copied"]["language"] == "Japanese" and not ending["copied"]["built"], ending
    assert hd_install.voices_with_hd(root) == {"u", "c"}
    assert sys_files(root, "u") == english, "the English set was touched"
    assert (root / "setup.xml").read_bytes() == SETUP, "the voices moved"
    assert not [line for line in lines_of(hd) if "mod's own tools" in line], lines_of(hd)
    record = api._patcher().hd_categories("ff13-2", "hdproject2")
    assert record["sets"]["c"] == ["weapons", "zones"], record
    assert not instant(api, root, events, "Japanese").get("notice")

    # and again, with nothing left to carry
    ending, _ = copy_over(api, events, mod)
    assert not ending["copied"]["files"] and not ending["copied"]["zones"], ending


def test_a_copy_that_fails_leaves_the_other_set_as_it_was(tmp_path, monkeypatch):
    from patcher.mods import hd_mirror

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"])
    japanese = sys_files(root, "c")
    monkeypatch.setattr(hd_mirror, "_read_back", lambda *a, **k: "the drive filled up")

    ending, _ = copy_over(api, events, mod, expect="failed")

    assert "drive filled up" in ending["error"], ending
    assert sys_files(root, "c") == japanese
    assert (root / "setup.xml").read_bytes() == SETUP


def test_a_copy_stopped_is_a_stop(tmp_path, monkeypatch):
    from patcher.mods import hd_mirror
    from patcher.mods.hd_runner import Cancelled

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"])
    japanese = sys_files(root, "c")

    def stopped(*args, **kwargs):
        raise Cancelled("stopped")

    monkeypatch.setattr(hd_mirror, "plan", stopped)
    copy_over(api, events, mod, expect="cancelled")

    assert sys_files(root, "c") == japanese


def test_a_copy_with_no_room_says_so(tmp_path, monkeypatch):
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"])
    japanese = sys_files(root, "c")
    monkeypatch.setattr("patcher.download.free_space", lambda p: 4096)

    ending, _ = copy_over(api, events, mod, expect="failed")

    assert "isn't room" in ending["error"], ending
    assert sys_files(root, "c") == japanese


def jp_weapon(root):
    """the Japanese w101 isn't the English one, the way a language's own file wouldn't be"""
    from tests.test_hd_mirror import replace_in

    replace_in(root, "c", "chr/weapon/w101/bin/w101.win32.imgb", b"a Japanese w101" * 300)


def test_what_the_copy_cant_do_the_install_builds_with_the_tools(tmp_path, monkeypatch):
    """
    a file HD changed that isn't the same file in Japanese can't be copied, so
    the install builds that pass for Japanese from its own originals with the
    mod's tools, in the same run, and the switch afterwards is still the write
    """
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    jp = entry_bytes(root, "chr/weapon/w101/bin/w101.win32.imgb", "c")

    hd = install(api, events, mod, ["weapons", "zones"], voices=True)

    lines = lines_of(hd)
    assert any("stay out of the Japanese files" in line for line in lines), lines
    assert any(line.startswith("[voices] Weapons for the Japanese voice files with the mod's "
                               "own tools") for line in lines), lines
    built = entry_bytes(root, "chr/weapon/w101/bin/w101.win32.imgb", "c")
    assert built.startswith(jp) and b"HD" in built, "not built from the Japanese original"
    assert api._patcher().hd_categories("ff13-2", "hdproject2")["sets"]["c"] == \
        ["weapons", "zones"]
    assert not instant(api, root, events, "Japanese").get("notice")
    # and back: Japanese's own build isn't something English is missing
    assert not instant(api, root, events, "English").get("notice")


def test_a_repair_the_copy_cant_carry_is_built_for_the_other_set_too(tmp_path, monkeypatch):
    """
    Japanese has Weapons already, so nothing's missing by the record, but this
    run's build is newer and the copy can't take it across
    """
    from patcher.mods import hd_runner
    from tests.hd2_world import INJECT, entry_bytes
    from tests.test_hd_runner import stub

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    install(api, events, mod, ["weapons"], voices=True)
    stub(mod / "supportfiles", hd_runner.TOOLS["inject2"], INJECT.replace("b'HD'", "b'HD v2'"))

    hd = install(api, events, mod, ["weapons"], voices=True)

    weapon = "chr/weapon/w101/bin/w101.win32.imgb"
    for voice in "uc":
        assert b"HD v2" in entry_bytes(root, weapon, voice), voice
    assert any(line.startswith("[voices] Weapons for the Japanese voice files")
               for line in lines_of(hd))
    instant(api, root, events, "Japanese")
    instant(api, root, events, "English")


def test_the_box_alone_builds_what_the_copy_cant_do_in_the_same_run(tmp_path, monkeypatch):
    """
    the copy on its own over a Japanese w101 that's its own file: the zones go
    across and Weapons is built from the Japanese original with the mod's
    tools, the required fixes first, all in the one run the box started
    """
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    install(api, events, mod, ["weapons", "zones"])
    jp = entry_bytes(root, "chr/weapon/w101/bin/w101.win32.imgb", "c")

    ending, hd = copy_over(api, events, mod)

    assert ending["copied"]["zones"] == 1, ending
    assert ending["copied"]["built"] == "built with the mod's tools.", ending
    built = entry_bytes(root, "chr/weapon/w101/bin/w101.win32.imgb", "c")
    assert built.startswith(jp) and b"HD" in built, "not built from the Japanese original"
    assert (root / "setup.xml").read_bytes() == SETUP
    assert not instant(api, root, events, "Japanese").get("notice")


def test_a_category_added_on_the_other_language_reaches_both(tmp_path, monkeypatch):
    """NPCs installed while on Japanese, box ticked, are in English as well"""
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"], voices=True)
    instant(api, root, events, "Japanese")
    install(api, events, mod, ["npcs"], voices=True)

    npc = "chr/npc/n012/bin/n012.win32.imgb"
    assert b"HD" in entry_bytes(root, npc, "u") and \
        entry_bytes(root, npc, "u") == entry_bytes(root, npc, "c")
    instant(api, root, events, "English")
    record = api._patcher().hd_categories("ff13-2", "hdproject2")
    assert record["sets"] == {"u": ["npcs", "weapons"], "c": ["npcs", "weapons"]}, record


def test_a_category_added_on_the_other_language_without_the_box_stays_there(tmp_path,
                                                                            monkeypatch):
    """and unticked, English is left short, and says so on the way back"""
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"], voices=True)
    instant(api, root, events, "Japanese")
    install(api, events, mod, ["npcs"])

    npc = "chr/npc/n012/bin/n012.win32.imgb"
    assert b"HD" in entry_bytes(root, npc, "c") and b"HD" not in entry_bytes(root, npc, "u")
    went = instant(api, root, events, "English")
    assert "behind the Japanese ones on FFXIII-2 HD, without NPCs" in went["notice"], went


def test_an_install_can_go_into_a_set_the_launcher_isnt_on(tmp_path):
    """and refuses one that isn't all there, before anything is written"""
    from patcher.mods.hd_install import InstallError, prepare

    root = game(tmp_path, sets="u")
    with pytest.raises(InstallError, match="the c voice files"):
        prepare(root, tmp_path / "mod", tmp_path / "store", "c")


def test_a_failed_voice_write_still_settles_the_launcher(tmp_path, monkeypatch):
    """NovaFix.ini already said who owns the graphics, and Steam's Play goes by it"""
    from patcher import launcher_skip
    from tests.test_launcher_follow import hidden, novafix

    api, root, _, _ = drawer_world(tmp_path, monkeypatch)
    novafix(api)
    assert hidden(root)
    monkeypatch.setattr(launcher_skip, "set_voice", lambda install_dir, mode: launcher_skip.Result(
        False, "could not write setup.xml: in use", failed=True))

    went = api.save_novafix_settings("ff13-2", {"Display": {"GraphicsSource": "The Game's Launcher"},
                                                "Language": {"Voices": "Japanese"}})

    assert not went["ok"] and went["error"].startswith("The rest is saved."), went
    assert "OverrideLauncherGraphics=Off" in novafix_ini(root).read_text(encoding="utf-8")
    assert not hidden(root), "the graphics went to the launcher and its window stayed hidden"


def test_the_drawer_renders_it_in_a_real_dom():
    """
    tools/settings_probe.js, which draws the drawer from the real schema plus
    this row: the note when a set short of HD is picked, the greyed row before
    a first launch, and the tab reading Settings. Skips without node and jsdom.
    """
    repo = Path(__file__).resolve().parent.parent
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    env = {**os.environ}
    for candidate in (repo / "node_modules", Path.home() / "node_modules"):
        if candidate.is_dir():
            env["NODE_PATH"] = str(candidate)
            break

    result = subprocess.run(["node", str(repo / "tools" / "settings_probe.js")],
                            capture_output=True, text=True, cwd=repo, env=env, timeout=180)
    if "Cannot find module 'jsdom'" in result.stderr:
        pytest.skip("jsdom is not installed (npm install jsdom)")

    assert result.returncode == 0, "\n".join(
        line for line in result.stdout.splitlines() if "FAIL" in line) or result.stderr


def test_the_labels_read_like_the_rest_of_the_drawer(tmp_path):
    """Title Case, by the same rule test_novafix_config holds every other row to"""
    from tests.test_novafix_config import _untitled

    row = voices.row(game(tmp_path), XIII2)
    shown = [row["section"], row["label"], *row["choiceLabels"],
             voices.row(game(tmp_path / "one", sets="u"), XIII2)["unavailable"],
             voices.row(game(tmp_path / "none", setup=None), XIII2)["unavailable"],
             voices.row(game(tmp_path / "bare", setup=b"<SaveData></SaveData>"), XIII2)["unavailable"]]

    assert not {text: _untitled(text) for text in shown if _untitled(text)}


def xiii_world(tmp_path, monkeypatch, zones=False):
    """XIII with both voice sets, each with its scr pair and an image pair holding a zone"""
    from tests.helpers import pack
    from tests.test_hd_end_to_end import make_world
    from tests.test_hd_zones import BLOCK_FILES, DATA as ZONE_DATA

    api, events, install_dir, mod = make_world(tmp_path, monkeypatch)
    sys_dir = install_dir / "white_data" / "sys"
    for name in ("filelist_scrc.win32.bin", "white_scrc.win32.bin"):
        shutil.copyfile(sys_dir / name.replace("scrc", "scru"), sys_dir / name)
    zone_dir = install_dir / "white_data" / "zone"
    zone_dir.mkdir(parents=True)
    for voice in "uc":
        index = pack(zone_dir / f"white_z010{voice}_img.win32.bin",
                     zone_dir / f"filelist_z010{voice}.win32.bin",
                     [(p.replace("/", "\\"), b"Z" * 900) for p in BLOCK_FILES])
        pack(sys_dir / f"white_img{voice}.win32.bin", sys_dir / f"filelist{voice}.win32.bin",
             [(f"zone\\filelist_z010{voice}.win32.bin", index.read_bytes()),
              ("zone\\z010\\z010.wdb", b"w" * 768)])
        index.unlink()
    if zones:
        (mod / "Data" / "LoadHDEnvironments.txt").write_text("#:Sunleth\nZ10\n", encoding="utf-8")
        shutil.copy(ZONE_DATA / "z010block022u.txt", mod / "Data" / "z010block022u.txt")
    (install_dir / "setup.xml").write_bytes(SETUP)
    return api, events, install_dir, mod


def xiii_install(api, events, install_dir, mod, categories, voices=False):
    from tests.test_hd_end_to_end import run_and_wait

    events.clear()
    hd = run_and_wait(api, events, install_dir, mod, categories, voices=voices)
    assert any(e.get("stage") == "done" for e in hd), hd
    return hd


@pytest.mark.parametrize("categories", [["npcs"], ["npcs", "zones"], ["zones"]])
def test_xiii_puts_hd_in_both_voice_sets_too(tmp_path, monkeypatch, categories):
    """
    XIII keeps its characters in white_scr and each zone's index in white_img,
    one of each per voice set, and a zone's art in its own blob per set
    """
    from tests.helpers import carries_hd

    api, events, install_dir, mod = xiii_world(tmp_path, monkeypatch, zones=True)
    sys_dir, zone_dir = install_dir / "white_data" / "sys", install_dir / "white_data" / "zone"
    japanese_zone = (zone_dir / "white_z010c_img.win32.bin").read_bytes()

    xiii_install(api, events, install_dir, mod, categories, voices=True)

    assert hd_install.voices_with_hd(install_dir) == {"u", "c"}
    if "npcs" in categories:
        assert carries_hd(sys_dir / "filelist_scrc.win32.bin", sys_dir / "white_scrc.win32.bin",
                          "chr/npc/n910/bin/n910.win32.trb"), "the Japanese pair has no HD in it"
        from patcher.archive import filelist
        assert filelist.records_problem(filelist.load(sys_dir / "filelist_scrc.win32.bin")) == ""
    if "zones" in categories:
        assert (zone_dir / "white_z010c_img.win32.bin").read_bytes() != japanese_zone
        from patcher.mods import hd_mirror
        assert "z010" in hd_mirror.installed_zones(install_dir, "c")
    record = api._patcher().hd_categories("ff13", "hdproject")
    assert record["sets"]["c"] == record["sets"]["u"] == sorted(categories), record
    instant(api, install_dir, events, "Japanese", game="ff13")
    assert sorted(api._patcher().hd_categories("ff13", "hdproject")["installed"]) == \
        sorted(categories)


def test_xiii_without_the_box_leaves_the_japanese_pairs_alone(tmp_path, monkeypatch):
    api, events, install_dir, mod = xiii_world(tmp_path, monkeypatch, zones=True)
    sys_dir, zone_dir = install_dir / "white_data" / "sys", install_dir / "white_data" / "zone"
    before = {p.name: p.read_bytes() for d in (sys_dir, zone_dir) for p in d.iterdir()
              if p.name.endswith(("c.win32.bin", "c_img.win32.bin"))}

    xiii_install(api, events, install_dir, mod, ["npcs", "zones"])

    assert hd_install.voices_with_hd(install_dir) == {"u"}
    after = {name: (sys_dir / name if (sys_dir / name).exists() else zone_dir / name).read_bytes()
             for name in before}
    assert after == before
    went = instant(api, install_dir, events, "Japanese", game="ff13")
    assert went["notice"].startswith("The HD Project is only in the English voice files"), went


def test_not_knowing_what_a_set_is_short_of_doesnt_stop_the_switch(tmp_path, monkeypatch):
    """the note is a note: the voices are the player's to pick either way"""
    from patcher.bridge import UiBridge

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"])

    def broken(self, ui_game_id, language):
        raise RuntimeError("the index wouldn't read")

    monkeypatch.setattr(UiBridge, "voice_gap", broken)
    went = instant(api, root, events, "Japanese")

    assert not went.get("notice"), went
    # and the drawer still opens, without a note on the row
    assert api.novafix_settings("ff13-2")["settings"][-1]["warn"] == {}


def record_of(api, game="ff13-2", job="hdproject2"):
    from patcher import bridge
    from patcher.state import load_state

    manifest = {"ff13": "ffxiii", "ff13-2": "ffxiii2"}[game]
    state = load_state(bridge.state_path_for(manifest))
    return next(r for r in state.values() if isinstance(r, dict) and "installed" in r)


def test_a_reverted_game_switches_with_nothing_recorded(tmp_path, monkeypatch):
    """Revert puts both sets back, and the per-set lines it leaves are stale"""
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"], voices=True)
    english, japanese = sys_files(root, "u"), sys_files(root, "c")
    assert api.restore_hd_project("ff13-2")["ok"]
    assert hd_install.voices_with_hd(root) == set()
    assert sys_files(root, "u") != english and sys_files(root, "c") != japanese

    assert not instant(api, root, events, "Japanese").get("notice")
    assert record_of(api)["installed"] == []


def stamp(api, game, number, voice=None):
    """what a build from before the zone fix wrote, in one voice set or every one"""
    from patcher import bridge
    from patcher.state import load_state, save_state

    path = bridge.state_path_for({"ff13": "ffxiii", "ff13-2": "ffxiii2"}[game])
    state = load_state(path)
    for record in state.values():
        if not isinstance(record, dict) or "pipeline" not in record:
            continue
        if voice is None:
            record["pipeline"]["zones"] = number
        for each, stamps in (record.get("pipelines") or {}).items():
            if "zones" in stamps and voice in (None, each):
                stamps["zones"] = number
        live = record.get("pipelines", {}).get("u", {})
        if voice == "u" and "zones" in live:
            record["pipeline"]["zones"] = number
    save_state(path, state)


def test_a_copy_carries_the_stamp_of_the_build_it_came_from(tmp_path, monkeypatch):
    """
    the Japanese zones are the English zones' bytes, built by the same run, so
    they carry its stamp: a fix since then makes both suspect, and a set only
    one older build wrote to is the only one that says so
    """
    api, events, install_dir, mod = xiii_world(tmp_path, monkeypatch, zones=True)
    xiii_install(api, events, install_dir, mod, ["npcs", "zones"], voices=True)
    record = record_of(api, "ff13", "hdproject")
    assert record["pipelines"]["c"] == record["pipelines"]["u"], record
    survey = lambda: api._patcher().hd_survey("ff13", "hdproject", str(mod))["suspect"]
    assert "zones" not in survey()

    stamp(api, "ff13", 1, voice="u")
    assert "zones" in survey()
    instant(api, install_dir, events, "Japanese", game="ff13")
    assert "zones" not in survey()


def xiii_copy_over(api, events, install_dir, mod):
    from tests.test_hd_end_to_end import run_and_wait

    events.clear()
    hd = run_and_wait(api, events, install_dir, mod, [], voices=True)
    ending = next(e for e in hd if e.get("stage") in ("done", "failed", "cancelled"))
    assert ending["stage"] == "done", ending
    return ending


def test_an_older_builds_zones_copied_across_stay_suspect(tmp_path, monkeypatch):
    api, events, install_dir, mod = xiii_world(tmp_path, monkeypatch, zones=True)
    xiii_install(api, events, install_dir, mod, ["npcs", "zones"])
    stamp(api, "ff13", 1)

    xiii_copy_over(api, events, install_dir, mod)
    instant(api, install_dir, events, "Japanese", game="ff13")

    assert "zones" in api._patcher().hd_survey("ff13", "hdproject", str(mod))["suspect"]


def strip_to(api, game, keep_pipeline):
    """the record as a build from before the per-set lines left it"""
    from patcher import bridge
    from patcher.state import load_state, save_state

    path = bridge.state_path_for({"ff13": "ffxiii", "ff13-2": "ffxiii2"}[game])
    state = load_state(path)
    for record in state.values():
        if isinstance(record, dict) and "installed" in record:
            record.pop("sets", None)
            record.pop("pipelines", None)
            record["pipeline"] = dict(keep_pipeline)
    save_state(path, state)


def test_a_record_from_before_the_sets_is_the_set_the_game_loaded(tmp_path, monkeypatch):
    """
    an install from before either: the old game-wide line is the set that was
    loading, and a copy from it carries it, old zones and all
    """
    api, events, install_dir, mod = xiii_world(tmp_path, monkeypatch, zones=True)
    xiii_install(api, events, install_dir, mod, ["npcs", "zones"])
    strip_to(api, "ff13", {"npcs": 1, "zones": 1})
    survey = lambda: api._patcher().hd_survey("ff13", "hdproject", str(mod))["suspect"]
    assert "zones" in survey()

    xiii_copy_over(api, events, install_dir, mod)
    instant(api, install_dir, events, "Japanese", game="ff13")

    assert "zones" in survey(), "the copy of the old zones lost their warning"
    record = record_of(api, "ff13", "hdproject")
    assert record["sets"] == {"u": ["npcs", "zones"], "c": ["npcs", "zones"]}, record
    assert record["pipelines"]["c"] == {"npcs": 1, "zones": 1}, record


def test_the_launcher_moving_the_voices_moves_the_record_too(tmp_path, monkeypatch):
    """
    with the graphics handed to Square Enix's launcher its window is back, and
    its voice setting writes voice_mode without the app knowing
    """
    api, events, install_dir, mod = xiii_world(tmp_path, monkeypatch, zones=True)
    xiii_install(api, events, install_dir, mod, ["npcs", "zones"], voices=True)
    stamp(api, "ff13", 1, voice="u")
    assert "zones" in api._patcher().hd_survey("ff13", "hdproject", str(mod))["suspect"]

    (install_dir / "setup.xml").write_bytes(JAPANESE)

    assert "zones" not in api._patcher().hd_survey("ff13", "hdproject", str(mod))["suspect"]
    assert sorted(api._patcher().hd_categories("ff13", "hdproject")["installed"]) == \
        ["npcs", "zones"]


def test_revert_clears_what_each_set_had(tmp_path, monkeypatch):
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"], voices=True)
    assert set(record_of(api)["sets"]) == {"u", "c"}

    assert api.restore_hd_project("ff13-2")["ok"]

    record = record_of(api)
    assert record["installed"] == [] and "sets" not in record and "pipelines" not in record


def test_a_hand_edited_source_doesnt_take_the_drawer_down(tmp_path, monkeypatch):
    """the state file is a user's to edit"""
    from patcher import bridge
    from patcher.state import load_state, save_state

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"])
    path = bridge.state_path_for("ffxiii2")
    state = load_state(path)
    assert any(isinstance(r, dict) and r.get("source") for r in state.values()), state
    for record in state.values():
        if isinstance(record, dict) and "source" in record:
            record["source"] = ["D:/mods/FFXIII-2 HD"]
    save_state(path, state)

    drawer = api.novafix_settings("ff13-2")
    assert drawer["ok"] and drawer["settings"][-1]["key"] == "Voices", drawer
    instant(api, root, events, "Japanese")


def test_an_install_older_than_the_record_is_found_by_its_run_log(tmp_path, monkeypatch):
    """
    every install before the record kept a folder named none, the user's own
    included, and what a set is short of reads better off the mod's lists
    """
    from patcher import bridge
    from patcher.state import load_state, save_state

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"])
    path = bridge.state_path_for("ffxiii2")
    state = load_state(path)
    for record in state.values():
        if isinstance(record, dict):
            record.pop("source", None)
    save_state(path, state)

    _, found = api._patcher().hd_source("ff13-2")
    assert found is not None and Path(found["root"]) == mod


def test_a_rebuild_with_the_box_takes_what_it_leaves_out_off_both_sets(tmp_path, monkeypatch):
    """
    the copy after a rebuild only adds, so a rebuild that put back only its
    own set would leave the other holding what it took off
    """
    from tests.hd2_world import entry_bytes
    from tests.test_hd2_end_to_end import run_and_wait

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "npcs"], voices=True)

    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set", voices=True)

    assert ending["stage"] == "done", ending
    npc, weapon = "chr/npc/n012/bin/n012.win32.imgb", "chr/weapon/w101/bin/w101.win32.imgb"
    for voice in "uc":
        assert b"HD" not in entry_bytes(root, npc, voice), voice
        assert b"HD" in entry_bytes(root, weapon, voice), voice
    record = api._patcher().hd_categories("ff13-2", "hdproject2")
    assert record["sets"] == {"u": ["weapons"], "c": ["weapons"]}, record

    instant(api, root, events, "Japanese")
    install(api, events, mod, ["pcs"], voices=True)
    assert b"HD" not in entry_bytes(root, npc, "u"), "the NPCs it took off came back"


def test_a_rebuild_without_the_box_leaves_the_other_set_be(tmp_path, monkeypatch):
    from tests.hd2_world import entry_bytes
    from tests.test_hd2_end_to_end import run_and_wait

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "npcs"], voices=True)
    japanese = sys_files(root, "c")

    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set")

    assert ending["stage"] == "done", ending
    assert b"HD" not in entry_bytes(root, "chr/npc/n012/bin/n012.win32.imgb", "u")
    assert sys_files(root, "c") == japanese


def test_a_repair_with_a_newer_build_reaches_the_other_set(tmp_path, monkeypatch):
    """the copy that's already there isn't the last install any more"""
    from patcher.mods import hd_runner
    from tests.hd2_world import INJECT, entry_bytes
    from tests.test_hd_runner import stub

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"], voices=True)
    stub(mod / "supportfiles", hd_runner.TOOLS["inject2"], INJECT.replace("b'HD'", "b'HD v2'"))

    install(api, events, mod, ["weapons"], voices=True)

    weapon = "chr/weapon/w101/bin/w101.win32.imgb"
    assert b"HD v2" in entry_bytes(root, weapon, "u")
    assert entry_bytes(root, weapon, "c") == entry_bytes(root, weapon, "u")
    instant(api, root, events, "Japanese")


def second_release(mod):
    """the mod's injector as its next release: every file it writes rebuilt at the same length"""
    from patcher.mods import hd_runner
    from tests.hd2_world import INJECT
    from tests.test_hd_runner import stub

    stub(mod / "supportfiles", hd_runner.TOOLS["inject2"],
         INJECT.replace("bulk(half.name.encode())", "bulk(half.name.encode() + b' v2')"))


def test_a_repair_without_the_box_is_still_owed_to_the_other_set(tmp_path, monkeypatch):
    """
    same length, so the sizes can't see it, and the box wasn't ticked for the
    repair: the copy asked for later still has to read it rather than take
    Japanese's older build as the same file
    """
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"], voices=True)
    weapon = "chr/weapon/w101/bin/w101.win32.imgb"
    older = entry_bytes(root, weapon, "c")
    second_release(mod)
    install(api, events, mod, ["weapons"])
    assert entry_bytes(root, weapon, "u") != older and entry_bytes(root, weapon, "c") == older
    assert "older build of Weapons" in api.novafix_settings("ff13-2")["settings"][-1]["warn"][
        "Japanese"]

    copy_over(api, events, mod)

    assert entry_bytes(root, weapon, "c") == entry_bytes(root, weapon, "u")


def stop_in(monkeypatch, where):
    """Stop pressed while the install's copy compares the sets, or while the tools build Japanese"""
    from patcher.mods import hd2_install, hd_mirror
    from patcher.mods.hd_runner import Cancelled

    if where == "copy":
        def plan(*args, **kwargs):
            raise Cancelled("stopped while comparing the voice sets")

        monkeypatch.setattr(hd_mirror, "plan", plan)
        return
    real = hd2_install.install

    def install_(install_dir, found, categories, selected, store, **kwargs):
        if kwargs.get("variant") == "c":
            raise Cancelled("stopped")
        return real(install_dir, found, categories, selected, store, **kwargs)

    monkeypatch.setattr(hd2_install, "install", install_)


def test_a_newer_zone_build_reaches_the_other_set(tmp_path, monkeypatch):
    """the zones go by the characters' rule: the last install's files are in both sets"""
    from tests.hd2_world import zone_entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["zones"], voices=True)
    block = "bg/loc001/a001/bin/block001.win32.imgb"
    first = zone_entry_bytes(root, "z0010", block, "u")
    second_release(mod)

    install(api, events, mod, ["zones"], voices=True)

    assert zone_entry_bytes(root, "z0010", block, "u") != first, "the repair didn't rebuild it"
    assert zone_entry_bytes(root, "z0010", block, "c") == zone_entry_bytes(root, "z0010", block, "u")
    instant(api, root, events, "Japanese")
    instant(api, root, events, "English")


@pytest.mark.parametrize("where", ["copy", "build"])
def test_a_repair_stopped_short_of_the_other_set_is_finished_by_the_next_copy(
        tmp_path, monkeypatch, where):
    """
    the run says ticking the box again finishes it, and it has to: a rebuild
    at the same length is past the sizes, and Japanese counts as having
    Weapons already, the build before this one's
    """
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch,
                                          japanese=jp_weapon if where == "build" else None)
    install(api, events, mod, ["weapons"], voices=True)
    weapon = "chr/weapon/w101/bin/w101.win32.imgb"
    older = {v: entry_bytes(root, weapon, v) for v in "uc"}
    second_release(mod)
    with monkeypatch.context() as patched:
        stop_in(patched, where)
        hd = install(api, events, mod, ["weapons"], voices=True)
    assert any("Japanese Voices Too ticked to finish it" in line for line in lines_of(hd)), \
        lines_of(hd)
    assert entry_bytes(root, weapon, "u") != older["u"]
    assert entry_bytes(root, weapon, "c") == older["c"]

    copy_over(api, events, mod)

    now = entry_bytes(root, weapon, "c")
    assert now != older["c"], "Japanese kept the build before this one"
    if where == "copy":
        assert now == entry_bytes(root, weapon, "u")
    assert not instant(api, root, events, "Japanese").get("notice")


def test_a_file_that_differs_only_turns_down_the_installs_that_change_it(tmp_path, monkeypatch):
    """
    Weapons can't be copied, NPCs can. What was found about w101 outlives a
    Revert, which leaves the vanilla files as they were, and stays about w101:
    NPCs installed after it still go across without the tools
    """
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    install(api, events, mod, ["weapons"], voices=True)
    assert api.restore_hd_project("ff13-2")["ok"]
    assert hd_install.voices_with_hd(root) == set()
    install(api, events, mod, ["npcs"])

    _, hd = copy_over(api, events, mod)

    npc = "chr/npc/n012/bin/n012.win32.imgb"
    assert b"HD" in entry_bytes(root, npc, "c") and \
        entry_bytes(root, npc, "c") == entry_bytes(root, npc, "u")
    assert not [line for line in lines_of(hd) if "mod's own tools" in line], lines_of(hd)


def test_a_crash_in_the_other_sets_build_leaves_the_install_counted(tmp_path, monkeypatch):
    """
    English is in and recorded. Japanese goes without Weapons and the switch
    says so, and installing them again with the box ticked is what retries it
    """
    from patcher import fileops
    from tests.hd2_world import entry_bytes
    from tests.test_hd2_end_to_end import run_and_wait

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    real = fileops.copy_atomic

    def held(source, target, *args, **kwargs):
        if "white_imgc" in str(source):
            raise PermissionError(13, "held by the indexer", str(source))
        return real(source, target, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(fileops, "copy_atomic", held)
        ending, hd = run_and_wait(api, events, mod, ["weapons"], voices=True)

    assert ending["stage"] == "done", ending
    weapon = "chr/weapon/w101/bin/w101.win32.imgb"
    assert b"HD" in entry_bytes(root, weapon, "u") and b"HD" not in entry_bytes(root, weapon, "c")
    assert any("held by the indexer" in line for line in lines_of(hd)), lines_of(hd)

    went = instant(api, root, events, "Japanese")
    assert "only in the English voice files" in went["notice"], went
    install(api, events, mod, ["weapons"], voices=True)
    assert b"HD" in entry_bytes(root, weapon, "c")
    instant(api, root, events, "English")
    assert not instant(api, root, events, "Japanese").get("notice")


def test_a_verify_that_put_one_sets_archive_back_is_told_from_the_record(tmp_path, monkeypatch):
    """
    Steam put Japanese's character archive back and the mod folder is gone, so
    the record is all there is to ask, and it still says Weapons. The archive
    has the last word, and the switch says what Japanese is short of
    """
    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    sys_dir = root / "alba_data" / "sys"
    vanilla = {n: (sys_dir / n).read_bytes() for n in ("white_imgc.win32.bin", "filelistc.win32.bin")}
    install(api, events, mod, ["weapons", "zones"], voices=True)
    shutil.rmtree(mod)
    for name, data in vanilla.items():
        (sys_dir / name).write_bytes(data)

    went = instant(api, root, events, "Japanese")

    # standard, not an older build: the archive went back, Weapons with it
    assert "without Weapons" in went["notice"], went


def test_leviathans_tears_going_on_doesnt_make_a_refusal_new_again(tmp_path, monkeypatch):
    """
    a copy that refuses every pass writes nothing, so the Japanese files were
    never backed up and their vanilla is the game's own, which Leviathan's
    Tears then changes. What was refused is about w101, which it doesn't touch
    """
    from patcher.mods import hd_mirror
    from tests.hd2_world import entry_bytes, make_game
    from tests.test_hd2_end_to_end import build_world
    from tests.rain import PAYLOAD
    from tests.test_hd2_injections import WEATHER, weather

    api, events, root, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    make_game(root.parent, variant="c", zones=(), extra=weather())
    (root / "setup.xml").write_bytes(SETUP)
    jp_weapon(root)
    install(api, events, mod, ["weapons"])

    outcome = hd_mirror.copy(root, undo_store_for(root), "u", "c")
    assert "characters" in outcome.blocked and not outcome.files, outcome
    assert not hd_mirror.lacking(root, "u", "c").files
    said = api._patcher().apply("ff13-2", ["leviathanstears"])
    assert entry_bytes(root, WEATHER[0], "c") == PAYLOAD, said

    assert not hd_mirror.lacking(root, "u", "c").files, "LT made the w101 refusal new again"


def test_a_rebuild_on_a_game_with_one_voice_set_leaves_the_record_one_set(tmp_path, monkeypatch):
    from tests.test_hd2_end_to_end import build_world, run_and_wait

    api, events, root, mod = build_world(tmp_path, monkeypatch)
    (root / "setup.xml").write_bytes(SETUP)
    assert run_and_wait(api, events, mod, ["weapons", "npcs"], voices=True)[0]["stage"] == "done"

    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set", voices=True)

    assert ending["stage"] == "done", ending
    record = api._patcher().hd_categories("ff13-2", "hdproject2")
    assert set(record.get("sets") or {}) <= {"u"}, record


def test_an_install_reads_what_it_built_and_nothing_else_of_either_set(tmp_path, monkeypatch):
    """
    the copy after an install of PCs compares PCs. Every other category went
    across with the install that built it, and reading all of them out of both
    sets again was gigabytes every time
    """
    from patcher.mods import hd_mirror

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "npcs", "enemies", "misc"], voices=True)
    read, real = [], hd_mirror._Reader.read

    def counted(self, path, entry):
        read.append(entry.path.replace("\\", "/").lower())
        return real(self, path, entry)

    monkeypatch.setattr(hd_mirror._Reader, "read", counted)
    install(api, events, mod, ["pcs"], voices=True)

    assert any(p.startswith("chr/pc/") for p in read), read
    assert not [p for p in read if p.startswith("chr/") and not p.startswith("chr/pc/")], read
    from tests.hd2_world import entry_bytes
    pc = "chr/pc/c108/bin/c108.win32.imgb"
    assert b"HD" in entry_bytes(root, pc, "c") and entry_bytes(root, pc, "c") == entry_bytes(root, pc, "u")


def test_the_box_alone_reads_what_this_set_has_hd_in_and_nothing_else(tmp_path, monkeypatch):
    """Weapons only, installed without the box: the copy has no business in npc/ or pc/"""
    from patcher.mods import hd_mirror

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"])
    read, real = [], hd_mirror._Reader.read

    def counted(self, path, entry):
        read.append(entry.path.replace("\\", "/").lower())
        return real(self, path, entry)

    monkeypatch.setattr(hd_mirror._Reader, "read", counted)
    copy_over(api, events, mod)

    assert any(p.startswith("chr/weapon/") for p in read), read
    assert not [p for p in read if p.startswith("chr/") and not p.startswith("chr/weapon/")], read


def test_a_model_edited_at_its_own_length_reaches_the_other_set(tmp_path, monkeypatch):
    """
    the size can't see it, so what an install built is read: n910 has a model
    recipe and no texture art, and a tool that edits it in place leaves both
    halves the length they were
    """
    from patcher.mods import hd_runner
    from tests.hd2_world import MODELS_TOOL, entry_bytes
    from tests.test_hd_runner import stub

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    in_place = MODELS_TOOL.replace(
        "target.write_bytes(source.read_bytes() + b'HDMODEL' + bulk(rel.encode()))",
        "target.write_bytes(bytes(b ^ 0x5A for b in source.read_bytes()))")
    assert in_place != MODELS_TOOL
    stub(mod / "supportfiles", hd_runner.TOOLS["models2"], in_place)
    model = "chr/npc/n910/bin/n910.win32.trb"
    vanilla = entry_bytes(root, model, "c")

    install(api, events, mod, ["npcs"], voices=True)

    english = entry_bytes(root, model, "u")
    assert english != vanilla and len(english) == len(vanilla), "the stub didn't edit in place"
    assert entry_bytes(root, model, "c") == english
    instant(api, root, events, "Japanese")


def refusing_pack():
    """ff13tool refusing the Japanese repack, the way a file in that set it chokes on would"""
    from tests.hd2_world import FF13TOOL

    body = FF13TOOL.replace("elif mode == '-c':\n",
                            "elif mode == '-c' and 'filelistc' in str(args):\n"
                            "    print('Error: chunk 3 of filelistc is corrupt', flush=True)\n"
                            "    sys.exit(1)\n"
                            "elif mode == '-c':\n")
    assert body != FF13TOOL
    return body


def tool(mod, key, body):
    from patcher.mods import hd_runner
    from tests.test_hd_runner import stub

    stub(mod / "supportfiles", hd_runner.TOOLS[key], body)


def test_a_build_the_tools_refuse_for_the_other_set_says_so_and_the_voices_still_switch(
        tmp_path, monkeypatch):
    """
    a repair whose Japanese build ff13tool refuses: English is in, Japanese
    keeps its older Weapons, the switch says so, and installing them again
    with the box ticked retries it
    """
    from tests.hd2_world import FF13TOOL, entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    install(api, events, mod, ["weapons"], voices=True)
    weapon = "chr/weapon/w101/bin/w101.win32.imgb"
    older = entry_bytes(root, weapon, "c")
    tool(mod, "pack", refusing_pack())
    second_release(mod)

    hd = install(api, events, mod, ["weapons"], voices=True)

    assert any("Japanese Voices Too ticked to finish it" in line for line in lines_of(hd)), \
        lines_of(hd)
    went = instant(api, root, events, "Japanese")
    assert "with an older build of Weapons" in went["notice"], went
    assert entry_bytes(root, weapon, "c") == older
    instant(api, root, events, "English")

    tool(mod, "pack", FF13TOOL)
    install(api, events, mod, ["weapons"], voices=True)
    assert entry_bytes(root, weapon, "c") != older
    assert not instant(api, root, events, "Japanese").get("notice")


def test_a_category_a_copy_carries_later_stops_being_short(tmp_path, monkeypatch):
    """
    NPCs were only in Japanese's tools build because Weapons turned the pass
    down. Installed again, they copy, which is them in Japanese at English's
    build, so the note stops naming them
    """
    from tests.hd2_world import FF13TOOL, entry_bytes
    from tests.test_hd_mirror import replace_in

    weapon, npc = "chr/weapon/w101/bin/w101.win32.imgb", "chr/npc/n012/bin/n012.win32.imgb"

    def same_size(root):
        replace_in(root, "c", weapon, b"J" * len(entry_bytes(root, weapon, "c")))

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=same_size)
    install(api, events, mod, ["weapons"], voices=True)
    tool(mod, "pack", refusing_pack())
    install(api, events, mod, ["npcs", "weapons"], voices=True)
    assert api._patcher().hd_owed("ff13-2", "c") == {"npcs", "weapons"}

    tool(mod, "pack", FF13TOOL)
    install(api, events, mod, ["npcs"], voices=True)

    assert entry_bytes(root, npc, "c") == entry_bytes(root, npc, "u")
    gap = api._patcher().voice_gap("ff13-2", "Japanese")
    assert not gap or "NPCs" not in (gap.get("missing") or []) + (gap.get("older") or []), gap


def test_what_failed_and_what_stopped_go_in_one_note(tmp_path, monkeypatch):
    """
    Japanese has the zones, and lacks Weapons, which failed, and NPCs, which
    stopped: the note names both
    """
    from patcher.mods import hd2_install
    from patcher.mods.hd_runner import Cancelled
    from tests.hd2_world import FF13TOOL, entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    tool(mod, "pack", refusing_pack())
    install(api, events, mod, ["weapons", "zones"], voices=True)
    assert hd_install.voices_with_hd(root) == {"u", "c"}
    tool(mod, "pack", FF13TOOL)
    real = hd2_install.install

    def stopped(install_dir, found, categories, selected, store, **kwargs):
        if kwargs.get("variant") == "c":
            raise Cancelled("stopped")
        return real(install_dir, found, categories, selected, store, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(hd2_install, "install", stopped)
        install(api, events, mod, ["npcs"], voices=True)
    shutil.rmtree(mod)

    went = instant(api, root, events, "Japanese")

    assert "without NPCs and Weapons" in went["notice"], went
    weapon, npc = "chr/weapon/w101/bin/w101.win32.imgb", "chr/npc/n012/bin/n012.win32.imgb"
    assert b"HD" not in entry_bytes(root, weapon, "c") and b"HD" not in entry_bytes(root, npc, "c")


def test_a_set_the_copy_cant_reach_says_so_and_the_voices_switch(tmp_path, monkeypatch):
    """Japanese's zone blob is gone, so neither the copy nor the tools can put the zone there"""
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    (root / "alba_data" / "zone" / "white_z0010c_img.win32.bin").unlink()

    hd = install(api, events, mod, ["zones"], voices=True)

    assert any("white_z0010c_img.win32.bin" in line for line in lines_of(hd)), lines_of(hd)
    went = instant(api, root, events, "Japanese")
    assert "without Environments" in went["notice"] or "only in the English" in went["notice"], went


def test_the_note_on_a_set_short_of_it_says_how_to_put_it_there(tmp_path, monkeypatch):
    """
    installing it again starts with the mod back on the window. Before the
    switch that's with the box ticked, and after it a plain install goes into
    these voices, the box beside it being English's by then
    """
    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    tool(mod, "pack", refusing_pack())
    install(api, events, mod, ["weapons"], voices=True)
    shutil.rmtree(mod)
    before = api.novafix_settings("ff13-2")["settings"][-1]["warn"]["Japanese"]

    went = instant(api, root, events, "Japanese")

    assert before.endswith("drop the mod on the window and install it with Japanese Voices Too "
                           "ticked."), before
    assert "drop the mod on the window and install it, which now goes into these voices. " \
           "Switching back to English first and ticking Japanese Voices Too" in went["notice"], went


def test_the_notice_after_a_switch_names_the_box_the_panel_shows_then(tmp_path, monkeypatch):
    """
    on Japanese the panel's box is English Voices Too and copies the other
    way, so the notice can't send anyone to a Japanese one there
    """
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"])
    survey = lambda: api._patcher().hd_survey("ff13-2", "hdproject2", str(mod))["voices"]

    went = instant(api, root, events, "Japanese")

    assert survey()["language"] == "English"
    assert "install it with Japanese Voices Too ticked" not in went["notice"], went
    assert "Switching back to English first and ticking Japanese Voices Too" in went["notice"]
    instant(api, root, events, "English")
    assert survey()["language"] == "Japanese", "the box the notice sends them to isn't there"


def test_the_survey_prices_the_box(tmp_path, monkeypatch):
    """
    what the panel adds up beside the box: the other set's backup the first
    time a copy writes it, and each category's art, read off the indexes once
    this set has it. Nothing to back up once the backup holds the file
    """
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    sys_dir, zones = root / "alba_data" / "sys", root / "alba_data" / "zone"
    survey = lambda: api._patcher().hd_survey("ff13-2", "hdproject2", str(mod))["voices"]

    box = survey()
    assert box["language"] == "Japanese" and box["has"] == [] and box["owed"] == [], box
    assert box["parts"]["weapons"] == ["filelistc.win32.bin", "white_imgc.win32.bin"], box
    assert "white_z0010c_img.win32.bin" in box["parts"]["zones"], box
    assert box["backup"]["white_imgc.win32.bin"] == (sys_dir / "white_imgc.win32.bin").stat().st_size
    assert box["backup"]["white_z0010c_img.win32.bin"] == \
        (zones / "white_z0010c_img.win32.bin").stat().st_size
    sizes = api._patcher().hd_survey("ff13-2", "hdproject2", str(mod))["sizes"]
    assert box["art"]["weapons"] == sizes["weapons"], "unbuilt, the panel's own size stands in"

    install(api, events, mod, ["weapons"])
    box = survey()
    assert box["owed"] == ["weapons"] and box["has"] == [], box
    built = len(entry_bytes(root, "chr/weapon/w101/bin/w101.win32.imgb", "u"))
    assert box["art"]["weapons"] >= built, box

    install(api, events, mod, ["npcs"], voices=True)
    box = survey()
    assert set(box["has"]) == {"npcs", "weapons"} and box["owed"] == [], box
    assert box["backup"]["white_imgc.win32.bin"] == 0, box


def test_the_box_is_kept_like_the_ticks(tmp_path, monkeypatch):
    api, _, _, _ = drawer_world(tmp_path, monkeypatch)
    bridge = api._patcher()
    assert bridge.hd_categories("ff13-2", "hdproject2")["voices"] is False

    bridge.set_hd_categories("ff13-2", "hdproject2", ["weapons"], True)
    assert bridge.hd_categories("ff13-2", "hdproject2")["voices"] is True
    # the ticks alone leave it be
    bridge.set_hd_categories("ff13-2", "hdproject2", ["npcs"])
    assert bridge.hd_categories("ff13-2", "hdproject2")["voices"] is True
    bridge.set_hd_categories("ff13-2", "hdproject2", ["npcs"], False)
    assert bridge.hd_categories("ff13-2", "hdproject2")["voices"] is False


WEAPON = "chr/weapon/w101/bin/w101.win32.imgb"


def warned(api, language="Japanese"):
    return api.novafix_settings("ff13-2")["settings"][-1]["warn"].get(language, "")


def test_the_box_alone_builds_a_repair_it_couldnt_copy(tmp_path, monkeypatch):
    """
    Japanese's w101 is its own file, so a copy can't carry Weapons and the
    tools build it. The repair went in without the box, which used to be owed
    only in the copy's memo, and the copy wiped that on the way to the tools
    """
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    install(api, events, mod, ["weapons"], voices=True)
    older = entry_bytes(root, WEAPON, "c")
    second_release(mod)
    install(api, events, mod, ["weapons"])
    assert entry_bytes(root, WEAPON, "c") == older
    assert "older build of Weapons" in warned(api)

    ending, hd = copy_over(api, events, mod)

    assert entry_bytes(root, WEAPON, "c") != older, \
        f"Japanese kept the build before the repair and the run said {ending}"
    assert ending["copied"]["built"], ending
    assert not warned(api)
    box = api._patcher().hd_survey("ff13-2", "hdproject2", str(mod))["voices"]
    assert box["owed"] == [], box


def test_a_ticked_install_of_something_else_builds_an_unticked_repair_too(tmp_path, monkeypatch):
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    install(api, events, mod, ["weapons"], voices=True)
    older = entry_bytes(root, WEAPON, "c")
    second_release(mod)
    install(api, events, mod, ["weapons"])

    install(api, events, mod, ["npcs"], voices=True)

    assert entry_bytes(root, WEAPON, "c") != older, "Japanese kept the older Weapons"
    assert not warned(api)


def test_the_same_build_in_each_set_isnt_older_both_ways(tmp_path, monkeypatch):
    """
    Weapons built into English, then into Japanese from the same release,
    neither with the box. Japanese building it itself settles what it owed
    English's, so at most the cautious way round says older
    """
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"])
    instant(api, root, events, "Japanese")
    install(api, events, mod, ["weapons"])
    assert entry_bytes(root, WEAPON, "u") == entry_bytes(root, WEAPON, "c")

    to_english = instant(api, root, events, "English").get("notice")
    to_japanese = instant(api, root, events, "Japanese").get("notice")

    assert not to_japanese, to_japanese
    assert not (to_english and to_japanese)


def test_a_copy_refused_for_room_owes_what_it_did_before(tmp_path, monkeypatch):
    """it wrote nothing, so Japanese's Weapons is still byte for byte English's"""
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"], voices=True)
    assert not warned(api)

    with monkeypatch.context() as patched:
        patched.setattr("patcher.download.free_space", lambda p: 4096)
        hd = install(api, events, mod, ["npcs"], voices=True)

    assert any("there isn't room" in line for line in lines_of(hd)), lines_of(hd)
    assert entry_bytes(root, WEAPON, "u") == entry_bytes(root, WEAPON, "c")
    warn = warned(api)
    assert "without NPCs" in warn and "Weapons" not in warn, warn


def test_a_rebuild_without_the_box_isnt_owed_what_it_took_off(tmp_path, monkeypatch):
    """English dropped NPCs, so Japanese can't be behind it on them"""
    from tests.test_hd2_end_to_end import run_and_wait

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "npcs"], voices=True)
    install(api, events, mod, ["npcs"])

    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set")

    assert ending["stage"] == "done", ending
    assert "NPCs" not in warned(api), warned(api)


def test_the_box_offers_to_finish_a_build_that_was_stopped(tmp_path, monkeypatch):
    """
    the tools build of Weapons into Japanese was stopped after the copy
    finished. The drawer says Japanese is behind, so the box can't say APPLIED
    with Copy greyed out
    """
    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    install(api, events, mod, ["weapons"], voices=True)
    second_release(mod)
    with monkeypatch.context() as patched:
        stop_in(patched, "build")
        install(api, events, mod, ["weapons"], voices=True)
    assert "older build of Weapons" in warned(api)

    box = api._patcher().hd_survey("ff13-2", "hdproject2", str(mod))["voices"]

    assert box["owed"] == ["weapons"], box


def test_the_box_alone_with_one_voice_set_refuses_before_the_fixes(tmp_path, monkeypatch):
    """there's nowhere to copy to, and a refusal that had put fixes on wouldn't say so"""
    from tests.test_hd2_end_to_end import build_world
    from tests.test_hd2_injections import weather
    from tests.test_hd_patches_first import has_laa, required

    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), laa=False)
    (game / "setup.xml").write_bytes(SETUP)
    events.clear()

    assert api.install_hd_project("ff13-2", str(mod), [], "add", required(), True)["ok"]
    ending = ended(events)

    assert ending["stage"] == "failed" and "Japanese voice files aren't all there" in \
        ending["error"], ending
    assert not has_laa(game), "the 4GB Patch went on for a run that changed nothing else"
    assert not [e for e in events if e.get("stage") == "line"
                and e["text"].startswith("[patches]")], events


def test_the_box_prices_the_zones_a_copy_appends(tmp_path, monkeypatch):
    """repairs don't grow the zone blob, so its price isn't every build added up"""
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    blob_c = root / "alba_data" / "zone" / "white_z0010c_img.win32.bin"
    install(api, events, mod, ["zones"])
    for _ in range(3):
        install(api, events, mod, ["zones"])
    priced = api._patcher().hd_survey("ff13-2", "hdproject2", str(mod))["voices"]["art"]["zones"]

    before = blob_c.stat().st_size
    copy_over(api, events, mod)

    assert 0 < priced <= 2 * (blob_c.stat().st_size - before), priced


def box_of(api, mod):
    return api._patcher().hd_survey("ff13-2", "hdproject2", str(mod))["voices"]


def test_npcs_english_got_back_from_japanese_arent_older_in_japanese(tmp_path, monkeypatch):
    """
    NPCs went out of English with a rebuild that left the box alone, while the
    record still had Japanese owing them from a repair before it. English
    then takes Japanese's own NPCs by a copy, so the two are the same bytes
    """
    from tests.hd2_world import entry_bytes
    from tests.test_hd2_end_to_end import run_and_wait

    npc = "chr/npc/n012/bin/n012.win32.imgb"
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "npcs"], voices=True)
    install(api, events, mod, ["npcs"])
    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set")
    assert ending["stage"] == "done", ending
    copy_over(api, events, mod)
    assert not warned(api)

    instant(api, root, events, "Japanese")
    copy_over(api, events, mod)
    instant(api, root, events, "English")

    assert entry_bytes(root, npc, "u") == entry_bytes(root, npc, "c")
    assert not warned(api), warned(api)
    assert box_of(api, mod)["owed"] == []


def test_a_copy_back_the_other_way_is_settled_by_a_copy_that_writes_nothing(tmp_path,
                                                                            monkeypatch):
    """
    the same, with no copy into Japanese between the rebuild and the one back.
    A copy only reads what its source changed, so it can't say the other way
    is even too, and the row stays careful. The box from English settles it,
    reading both and writing nothing
    """
    from tests.hd2_world import entry_bytes
    from tests.test_hd2_end_to_end import run_and_wait

    npc = "chr/npc/n012/bin/n012.win32.imgb"
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "npcs"], voices=True)
    install(api, events, mod, ["npcs"])
    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set")
    assert ending["stage"] == "done", ending
    instant(api, root, events, "Japanese")
    copy_over(api, events, mod)
    instant(api, root, events, "English")
    assert entry_bytes(root, npc, "u") == entry_bytes(root, npc, "c")
    assert entry_bytes(root, WEAPON, "u") == entry_bytes(root, WEAPON, "c")
    japanese = sys_files(root, "c")

    ending, _ = copy_over(api, events, mod)

    assert ending["copied"]["files"] == 0 and sys_files(root, "c") == japanese, ending
    assert not warned(api), warned(api)
    assert box_of(api, mod)["owed"] == []


def test_xiii_zones_english_got_back_from_japanese_arent_older_in_japanese(tmp_path,
                                                                          monkeypatch):
    from patcher.mods import hd_mirror
    from tests.test_hd_end_to_end import run_and_wait

    api, events, root, mod = xiii_world(tmp_path, monkeypatch, zones=True)
    xiii_install(api, events, root, mod, ["npcs", "zones"], voices=True)
    xiii_install(api, events, root, mod, ["zones"])
    events.clear()
    hd = run_and_wait(api, events, root, mod, ["npcs"], mode="set")
    assert any(e.get("stage") == "done" for e in hd), hd
    xiii_copy_over(api, events, root, mod)

    instant(api, root, events, "Japanese", game="ff13")
    xiii_copy_over(api, events, root, mod)
    instant(api, root, events, "English", game="ff13")

    assert hd_mirror.installed_zones(root, "u") == ["z010"]
    assert not api.novafix_settings("ff13")["settings"][-1]["warn"].get("Japanese")
    assert api._patcher().hd_survey("ff13", "hdproject", str(mod))["voices"]["owed"] == []


ZONES = ("z0010", "z0020", "z0030")


def zones_world(tmp_path, monkeypatch):
    """drawer_world with three zones in each voice set, and the mod's art for all three"""
    import functools

    import tests.hd2_world as world
    import tests.test_hd2_end_to_end as e2e

    real = world.make_game
    monkeypatch.setattr(world, "make_game", functools.partial(real, zones=ZONES))
    monkeypatch.setattr(e2e, "make_game", functools.partial(real, zones=ZONES))
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    for zone in ZONES[1:]:
        shutil.copytree(mod / "textures" / "z0010", mod / "textures" / zone)
    return api, root, mod, events


def failing_import(blob):
    """ff13tool refusing to import into one zone blob, so that zone is left out"""
    from tests.hd2_world import FF13TOOL

    body = FF13TOOL.replace("    matches = [", f"    if {blob!r} in str(blob):\n"
                                              "        print('Error: disk full', flush=True)\n"
                                              "        sys.exit(1)\n"
                                              "    matches = [")
    assert body != FF13TOOL
    return body


def test_a_zone_left_out_of_a_sets_own_build_is_still_owed(tmp_path, monkeypatch):
    """
    two of three zones is enough for the category to read as in, so its own
    build mustn't settle what it owed English's
    """
    from patcher.mods import hd_mirror
    from tests.hd2_world import FF13TOOL

    api, root, mod, events = zones_world(tmp_path, monkeypatch)
    install(api, events, mod, ["zones"])
    instant(api, root, events, "Japanese")
    tool(mod, "pack", failing_import("z0030c"))
    hd = install(api, events, mod, ["zones"])
    assert any("[left out] z0030" in line for line in lines_of(hd)), lines_of(hd)
    tool(mod, "pack", FF13TOOL)
    instant(api, root, events, "English")
    assert hd_mirror.installed_zones(root, "c") == ["z0010", "z0020"]
    assert warned(api).startswith("The Japanese voice files are behind the English ones")

    shown = box_of(api, mod)

    assert "zones" not in shown["has"] or "zones" in shown["owed"], shown


def skipping_inject(stem):
    """the injector failing on one id, a bad id a run reports and carries on past"""
    from tests.hd2_world import INJECT

    body = INJECT.replace("bulk(half.name.encode())", "bulk(half.name.encode() + b' v2')")
    body = body.replace("if not hits:", f"if {stem!r} in stem:\n"
                                        "    print('Texture file processing error', "
                                        "file=sys.stderr, flush=True)\n"
                                        "    sys.exit(0)\n"
                                        "if not hits:")
    assert body.count("v2") == 1 and stem in body
    return body


def test_an_id_skipped_in_a_sets_own_build_is_still_owed(tmp_path, monkeypatch):
    """
    Japanese builds Weapons itself and w101 is skipped, so its w101 is still
    the build before English's repair, at the same length
    """
    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons"], voices=True)
    older = entry_bytes(root, WEAPON, "c")
    second_release(mod)
    install(api, events, mod, ["weapons"])
    instant(api, root, events, "Japanese")
    tool(mod, "inject2", skipping_inject("w101"))
    hd = install(api, events, mod, ["weapons", "npcs"])
    assert any("[skipped]" in line and "w101" in line for line in lines_of(hd)), lines_of(hd)
    second_release(mod)
    instant(api, root, events, "English")
    assert entry_bytes(root, WEAPON, "c") == older != entry_bytes(root, WEAPON, "u")

    shown = box_of(api, mod)

    assert "weapons" in shown["owed"], shown
    assert "older build of Weapons" in warned(api), warned(api)


def jp_zone(root):
    """a block in the Japanese z0010 that isn't the English one, so the zones can't be copied"""
    from tests.test_hd_mirror import replace_in_zone

    replace_in_zone(root, "c", "z0010", "bg/loc001/a001/bin/block001.win32.imgb",
                    b"a Japanese block" * 60)


def test_a_zone_repair_a_sets_own_build_left_out_is_built_by_the_box(tmp_path, monkeypatch):
    """
    the zones can't be copied, English's repair went in without the box and
    Japanese's own repair left z0010 out. The record wasn't to be paid by a
    repair that didn't go in, or the copy that leaves zones to the tools
    builds nothing
    """
    from tests.hd2_world import FF13TOOL, zone_entry_bytes

    block = "bg/loc001/a001/bin/block001.win32.imgb"
    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_zone)
    install(api, events, mod, ["zones"], voices=True)
    older = zone_entry_bytes(root, "z0010", block, "c")
    second_release(mod)
    install(api, events, mod, ["zones"])
    instant(api, root, events, "Japanese")
    tool(mod, "pack", failing_import("z0010c"))
    hd = install(api, events, mod, ["zones"])
    assert any("[left out] z0010" in line for line in lines_of(hd)), lines_of(hd)
    tool(mod, "pack", FF13TOOL)
    instant(api, root, events, "English")
    assert "older build of Environments" in warned(api), warned(api)

    ending, hd = copy_over(api, events, mod)

    assert any("stay out of the Japanese files" in line for line in lines_of(hd)), lines_of(hd)
    assert zone_entry_bytes(root, "z0010", block, "c") != older, ending


def skip_only(stem):
    """the injector failing on one id every time, the mod's own bad file, and nothing else new"""
    from tests.hd2_world import INJECT

    body = INJECT.replace("if not hits:", f"if {stem!r} in stem:\n"
                                          "    print('Texture file processing error', "
                                          "file=sys.stderr, flush=True)\n"
                                          "    sys.exit(0)\n"
                                          "if not hits:")
    assert body != INJECT
    return body


def test_a_zone_left_out_owes_the_zones_and_nothing_else(tmp_path, monkeypatch):
    """
    Japanese builds Weapons and Environments itself and z0030 is left out, then
    Environments is repaired whole. Weapons went in whole the first time
    """
    from tests.hd2_world import FF13TOOL, entry_bytes, zone_entry_bytes

    block = "bg/loc001/a001/bin/block001.win32.imgb"
    api, root, mod, events = zones_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "zones"])
    instant(api, root, events, "Japanese")
    tool(mod, "pack", failing_import("z0030c"))
    hd = install(api, events, mod, ["weapons", "zones"])
    assert any("[left out] z0030" in line for line in lines_of(hd)), lines_of(hd)
    tool(mod, "pack", FF13TOOL)
    install(api, events, mod, ["zones"])
    instant(api, root, events, "English")

    assert entry_bytes(root, WEAPON, "u") == entry_bytes(root, WEAPON, "c")
    for zone in ZONES:
        assert zone_entry_bytes(root, zone, block, "u") == zone_entry_bytes(root, zone, block, "c")
    assert "Weapons" not in warned(api) and "weapons" not in box_of(api, mod)["owed"]


def test_a_skip_owes_the_category_it_was_in_and_nothing_else(tmp_path, monkeypatch):
    """n012 fails in Japanese's build of Weapons and NPCs, and a rebuild of NPCs puts it in"""
    from tests.hd2_world import INJECT, entry_bytes

    npc = "chr/npc/n012/bin/n012.win32.imgb"
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "npcs"])
    instant(api, root, events, "Japanese")
    tool(mod, "inject2", skip_only("n012"))
    hd = install(api, events, mod, ["weapons", "npcs"])
    assert any("[skipped]" in line and "n012" in line for line in lines_of(hd)), lines_of(hd)
    tool(mod, "inject2", INJECT)
    install(api, events, mod, ["npcs"])
    instant(api, root, events, "English")

    assert entry_bytes(root, npc, "u") == entry_bytes(root, npc, "c")
    assert "Weapons" not in warned(api) and "weapons" not in box_of(api, mod)["owed"]


def test_a_skip_the_mod_always_makes_owes_only_its_own_category(tmp_path, monkeypatch):
    """
    n012 fails in every build, and Japanese's own w101 sends the characters to
    the tools every time. Weapons goes in whole every time, so it's paid
    """
    api, root, mod, events = drawer_world(tmp_path, monkeypatch, japanese=jp_weapon)
    tool(mod, "inject2", skip_only("n012"))
    hd = install(api, events, mod, ["weapons", "npcs"], voices=True)
    assert any("mod's own tools" in line for line in lines_of(hd)), lines_of(hd)

    copy_over(api, events, mod)

    assert "Weapons" not in warned(api) and "weapons" not in box_of(api, mod)["owed"]


def n910_in_place():
    """the next release of the models tool: n910 remodelled at its own length, the rest as before"""
    from tests.hd2_world import MODELS_TOOL

    old = "    target.write_bytes(source.read_bytes() + b'HDMODEL' + bulk(rel.encode()))"
    body = MODELS_TOOL.replace(old, "    if 'n910' in rel:\n"
                                    "        target.write_bytes(bytes(b ^ 0x5A for b in "
                                    "source.read_bytes()))\n"
                                    "    else:\n" + "    " + old)
    assert body != MODELS_TOOL
    return body


def test_a_copy_doesnt_even_a_model_only_the_target_changed(tmp_path, monkeypatch):
    """
    English's NPCs came from a release that didn't touch n910, Japanese's from
    the next, which remodels it at its own length. A copy from English reads
    what English changed, so Japanese keeps its n910 and English is still behind
    """
    from tests.hd2_world import entry_bytes

    model = "chr/npc/n910/bin/n910.win32.trb"
    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    vanilla = entry_bytes(root, model, "u")
    recipe = mod / "data" / "n910.txt"
    held = recipe.read_bytes()
    recipe.unlink()
    install(api, events, mod, ["npcs"])
    recipe.write_bytes(held)
    tool(mod, "models2", n910_in_place())
    instant(api, root, events, "Japanese")
    install(api, events, mod, ["npcs"])
    newer = entry_bytes(root, model, "c")
    assert newer != vanilla and len(newer) == len(vanilla)
    assert "older build of NPCs" in warned(api, "English"), warned(api, "English")

    instant(api, root, events, "English")
    copy_over(api, events, mod)
    instant(api, root, events, "Japanese")

    assert entry_bytes(root, model, "u") == vanilla and entry_bytes(root, model, "c") == newer
    assert "NPCs" in warned(api, "English") and "npcs" in box_of(api, mod)["owed"]


def test_a_drawer_mod_a_build_leaves_alone_doesnt_owe_its_category(tmp_path, monkeypatch):
    """
    a costume from the Mods drawer sits on w101 in both voice sets, so
    Japanese's own build of Weapons leaves it and is still the whole of
    Weapons: the same file is in English
    """
    import zipfile

    from tests.hd2_world import entry_bytes

    api, root, mod, events = drawer_world(tmp_path, monkeypatch)
    install(api, events, mod, ["weapons", "npcs"])
    costume = tmp_path / "Blade.zip"
    with zipfile.ZipFile(costume, "w") as archive:
        archive.writestr(WEAPON, b"a costume" * 400)
        archive.writestr(WEAPON.replace(".imgb", ".trb"), b"its trb" * 300)
    assert api._patcher().install_mod("ff13-2", str(costume))["ok"]
    instant(api, root, events, "Japanese")

    hd = install(api, events, mod, ["weapons", "npcs"])

    assert any("w101" in line and "left" in line for line in lines_of(hd)), lines_of(hd)
    assert entry_bytes(root, WEAPON, "u") == entry_bytes(root, WEAPON, "c")
    instant(api, root, events, "English")
    assert "Weapons" not in warned(api), warned(api)
