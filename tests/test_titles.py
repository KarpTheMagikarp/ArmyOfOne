"""
What the drawer calls a mod, off what its download was called. The two the
user actually had in the list are the first two cases.
"""

import pytest

from patcher.mods.titles import title


@pytest.mark.parametrize("raw, shown", [
    ("(STANDALONE VERSION) HD Fonts and GUI - Base mod", "HD Fonts and GUI"),
    ("Red Rose Beachwear 2.0 (Patcher)", "Red Rose Beachwear"),
    ("Red_Rose_Beachwear_2.0_Patcher", "Red Rose Beachwear"),
    ("FF XIII HD Models Plus", "HD Models Plus"),
    ("Better Lighting-217-1-2-0-1699999999", "Better Lighting"),
    ("Some Mod v1.4 FINAL", "Some Mod"),
    ("Hair Color 2.0 - Standalone", "Hair Color"),
    ("Mod (v1.2) [Nexus]", "Mod"),
    ("Lightning Returns: Better UI", "Better UI"),
])
def test_packaging_comes_off_the_name(raw, shown):
    assert title(raw) == shown


@pytest.mark.parametrize("raw", [
    # a variant is part of what it is
    "Serah Dress (Red)",
    # the number a namesake gets, which is the only thing telling two apart
    "HD Fonts and GUI (2)",
    "Serah - Red Dress",
    # a Pack that's the thing itself, rather than how it was packed
    "HD Texture Pack",
    # the game in front of one word leaves too little to read
    "FFXIII-2 AudioFix",
    "Final Fantasy XIII-2 HD",
])
def test_what_says_what_it_is_stays(raw):
    assert title(raw) == raw


@pytest.mark.parametrize("raw", ["1.2", "v2", "mod", "(Patcher)"])
def test_never_nothing(raw):
    assert title(raw)


def test_a_name_in_another_script_is_left_as_it_is():
    assert title("Улучшенные шрифты") == "Улучшенные шрифты"


@pytest.mark.parametrize("raw, shown", [
    # a translation is what it is, in whatever script
    ("HD Fonts and GUI - Русская версия 1.1", "HD Fonts and GUI - Русская версия"),
    # a number that's part of a word isn't its version
    ("Battle Speed x1.5", "Battle Speed x1.5"),
    # Nexus's id, version and upload time, the time being what gives it away
    ("Less Grinding 73 1.3 2026-08-27T12-00-00", "Less Grinding"),
    ("Centered Camera 74 1 2026-08-27T08:15:31.123Z", "Centered Camera"),
    ("Centered_Camera-74-1-2026-08-27", "Centered Camera"),
    ("Chapter 13 2026-08-27", "Chapter 13"),
    # however the time's spelled: a .NET round trip, compact, or with the
    # colon lookalike Windows lets a folder name keep
    ("Less Grinding 73 1.3 2026-08-27T17:13:00.1234567-05:00", "Less Grinding"),
    ("Less_Grinding_73_1.3_2026-08-27T171300", "Less Grinding"),
    ("Centered Camera 74 1 2026-08-27T17-13-00.123+0000", "Centered Camera"),
    ("Centered Camera 74 1 2026-08-27T17\ua78913\ua78900", "Centered Camera"),
    # a tweak picked out of a pack lands after the stamp, the user's real two
    ("Less Grinding 73 1.3 2026-08-27T17-13-00Z (x2 EXP)", "Less Grinding (x2 EXP)"),
    ("Centered Camera 74 1 2026-08-27T17-13-00Z (Centered_Camera)", "Centered Camera"),
    ("Centered Camera 74 1 2026-08-27T17-13-00Z (Centered Camera) (2)",
     "Centered Camera (Centered Camera) (2)"),
    # whatever a manager or a rename left past the date
    ("Less Grinding 73 1.3 2026-08-27T17-13-00Z-0500 x86", "Less Grinding"),
    ("Centered Camera 74 1 2026-08-27 at 5.13 PM", "Centered Camera"),
    ("Less Grinding 73 1.3 2026-08-27T17h13m (x2 EXP) final", "Less Grinding (x2 EXP)"),
    ("Chapter 13 2026-08-27 notes", "Chapter 13 2026-08-27 notes"),
    # Nexus since June 2026, and once its slug's gone
    ("SkyUI_6.11_trg5df", "SkyUI"),
    ("Less Grinding_1.3_trg5df (Faster)", "Less Grinding (Faster)"),
    ("Less_Grinding_1.3", "Less Grinding"),
    # six digits there is a number, not a slug
    ("Mod_1.0_123456", "Mod 1.0 123456"),
    # a date that's the mod's own version, with no time after it, stays put
    ("Mod 2026-08-27 v2", "Mod 2026-08-27"),
    # the game's name off the front only when words are left, not a dash and a number
    ("Lightning Returns HD - 1-2-3-1700000000", "Lightning Returns HD - 1"),
])
def test_what_the_first_cut_got_wrong(raw, shown):
    assert title(raw) == shown


DROPS = [
    "(STANDALONE VERSION) HD Fonts and GUI - Base mod-40-2-2-1779722868.zip",
    "Lightning Returns HD - 1-2-3-1700000000.zip", "FFXIII-2 HD Project v1.2.zip",
    "Mod (2).zip", "Yeul (1).zip", "Some Mod - copy.zip", "[Nova] Serah Outfit (Patcher).zip",
    "Better Lighting v1.2 Final.zip", "FF13 Lightning Outfit.zip",
    "Red_Rose_Beachwear_2.0_Patcher.zip", "Battle Speed x1.5.zip",
    "HD Fonts and GUI - Русская версия 1.1.zip", "Serah Dress (Red).zip",
    "Some Mod v1.4 FINAL.7z", "Hair Color 2.0 - Standalone.rar",
    "Lightning Returns - Better UI-12-1-0-1700000000.zip", "FFXIII HD Project.zip",
    "Final Fantasy XIII-2 HD.zip", "Улучшенные шрифты.zip", "HD Models Plus - Lightning.ncmp",
    "Serah Dress", "HD Models Plus - Lightning", "c108.win32.imgb",
    # digits and letters past ASCII, which Python's \d and \w take and plain JS doesn't
    "Serah Outfit v\uff11.\uff12.zip", "Mod (\uff12).zip", "FF13 \u216b Remix.zip",
    "FF13 HD\u00b2Mod.zip", "Battle Speed x2.0 Final.zip",
    # LR's DLC restoration's two files, one with the game in front
    "FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip",
    "Aerith_Garb_Restauration-23-1-1-1759690588.zip",
    "FFXIII-2_-_Console_Content_Patch_v2.3.7z",
    # Nexus's newer names end in the upload time as well, and a hand-renamed
    # folder has spaces for its dashes
    "Less Grinding-73-1-3-2026-08-27T12-00-00Z.zip", "Less Grinding 73 1.3 2026-08-27T12-00-00",
    "Centered_Camera-74-1-2026-08-27.7z", "Mod 2024-05-01.zip", "Chapter 13 2026-08-27.zip",
    "Less Grinding 73 1.3 2026-08-27T17:13:00.1234567-05:00", "Less_Grinding_73_1.3_2026-08-27T171300.zip",
    "Centered Camera 74 1 2026-08-27T17-13-00.123+0000.7z", "Centered Camera 74 1 2026-08-27T17\ua78913\ua78900",
    "Mod 2026-08-27 v2.zip",
    "Less Grinding 73 1.3 2026-08-27T17-13-00Z-0500 x86.zip", "Centered Camera 74 1 2026-08-27 at 5.13 PM",
    "Less Grinding 73 1.3 2026-08-27T17h13m (x2 EXP) final", "Chapter 13 2026-08-27 notes.zip",
    "SkyUI_6.11_trg5df.zip", "Less Grinding_1.3_trg5df.7z", "Less_Grinding_1.3.zip", "Mod_1.0_123456.zip",
    "Less Grinding 73 1.3 2026-08-27T17-13-00Z (x2 EXP)", "Centered Camera 74 1 2026-08-27T17-13-00Z (Centered_Camera)",
]


def test_the_page_names_a_drop_as_the_backend_will(tmp_path):
    """
    the drawer says "Reading" by its own name for it before the backend's
    comes back, and two different ones read as two different mods
    """
    import json
    import shutil
    import subprocess
    from pathlib import Path

    from patcher.mods.game_files import mod_name

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node here")
    page = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text(
        encoding="utf-8")
    code = page[page.index("  const FILLER = new Set("):page.index(
        "  /* The mods Apply cannot finish")]
    script = code + ("\nconst names = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
                     "\nprocess.stdout.write(JSON.stringify(names.map(titled)));")
    shown = json.loads(subprocess.run([node, "-e", script], input=json.dumps(DROPS),
                                      capture_output=True, text=True, encoding="utf-8",
                                      check=True).stdout)
    for name in ("Serah Dress", "HD Models Plus - Lightning"):
        (tmp_path / name).mkdir()
    expected = [title(mod_name(tmp_path / name)) for name in DROPS]
    assert shown == expected


def test_the_name_a_mod_goes_in_under_matches_its_next_version():
    """
    updates find a mod by this name, so the version comes off glued on or not.
    Kept as it was: a mod already in under it would otherwise not be found
    """
    from pathlib import Path

    from patcher.addon_sieve import normalise_mod_name
    from patcher.mods.game_files import mod_name

    assert mod_name(Path("CoolModv1.2.zip")) == mod_name(Path("CoolModv1.3.zip")) == "CoolMod"
    assert normalise_mod_name("CoolModv1.2.zip") == normalise_mod_name("CoolModv1.3.zip")
    assert mod_name(Path("Battle Speed x1.5.zip")) == "Battle Speed x"


@pytest.mark.parametrize("raw, said", [
    ("Less Grinding 73 1.3 2026-08-27T12-00-00", "1.3"),
    ("Less Grinding-73-1-3-1724745600", "1.3"),
    ("Less Grinding 73 1.3 2026-08-27T17:13:00.1234567-05:00", "1.3"),
    ("Centered Camera 74 1 2026-08-27T171300", "1"),
    ("Less Grinding 73 1.3 2026-08-27T17-13-00Z (x2 EXP)", "1.3"),
    ("SkyUI_6.11_trg5df", "6.11"),
    ("Better Lighting v1.2", "1.2"),
    ("Red Rose Beachwear 2.0 (Patcher)", "2.0"),
    # a number that's part of the name, or none at all, isn't a version
    ("Battle Speed x1.5", ""),
    ("Chapter 13", ""),
    ("Gameplay Tweaks", ""),
    ("Name (2)", ""),
])
def test_the_version_is_kept_to_show_beside_the_title(raw, said):
    from patcher.mods.titles import version
    assert version(raw) == said


def test_rows_only_keep_their_whole_name_when_nothing_else_tells_them_apart():
    """
    mods already in when the app first opens come in as whatever they were
    called, often more than one row a mod, and none of that's a reason for a
    stamped name on screen
    """
    from patcher.bridge import UiBridge

    api = UiBridge.__new__(UiBridge)
    rows = [
        # one mod's archives and its loose files, from before they were one row
        {"name": "Less Grinding 73 1.3 2026-08-27T17:13:00.1234567-05:00", "kind": "files"},
        {"name": "Less Grinding 73 1.3 2026-08-27T17:13:00.1234567-05:00", "kind": "disk"},
        # two versions in at once: the version beside each tells them apart
        {"name": "Centered Camera 74 1 2026-08-27T171300 (Centered Camera)", "kind": "files"},
        {"name": "Centered Camera 74 2 2026-09-02T090000", "kind": "files"},
        # two downloads nothing on screen would tell apart
        {"name": "Mod (2)", "kind": "files"}, {"name": "Mod - copy", "kind": "files"},
    ]
    api._manifests = {}
    api.addons_dir = lambda game, create=True: {"ok": False}
    api.hd_rows = api.loader_mods = api.extra_rows = api.disk_mods = lambda game: []
    api.file_mods = lambda game: [dict(r) for r in rows]
    shown = [(m["title"], m["tag"]) for m in api.list_addons("ff13", rename=False)["mods"]]
    assert shown == [("Less Grinding", "v1.3"), ("Less Grinding", "v1.3"),
                     ("Centered Camera", "v1"), ("Centered Camera", "v2"),
                     ("Mod (2)", ""), ("Mod - copy", "")]
