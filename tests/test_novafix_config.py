"""
NovaFix.ini handling. The file belongs to NovaFix; we only ever touch the keys
the user changed in our UI.
"""

import re
from pathlib import Path

import pytest

from patcher import novafix_config as cfg

REAL_SHAPE = """\
[Performance]

; OUTPUT
; Unlimited | 15..1000
FrameRateLimit=Unlimited
; GameDefault | Off | On
VSyncMode=GameDefault
; Automatic | 24..1000
RefreshRate=Automatic

; SYSTEM
ShowFpsCounter=Off

[Graphics]

; DISPLAY
; Windowed | Borderless | BorderlessFullscreen
DisplayMode=Borderless
"""


def test_a_missing_file_reads_as_empty(tmp_path):
    """NovaFix only writes it on first run, so absent is the normal state"""
    assert cfg.read(tmp_path / "NovaFix.ini") == {}


def test_reading_their_real_file_shape(tmp_path):
    path = tmp_path / "NovaFix.ini"
    path.write_text(REAL_SHAPE)

    values = cfg.read(path)

    assert values["Performance"]["FrameRateLimit"] == "Unlimited"
    assert values["Performance"]["VSyncMode"] == "GameDefault"
    assert values["Graphics"]["DisplayMode"] == "Borderless"


def test_a_fresh_file_holds_only_what_changed(tmp_path):
    """
    not a generated full config. NovaFix fills in the rest on boot, and a file
    we authored from guessed defaults would pin settings nobody chose.
    """
    path = tmp_path / "NovaFix.ini"
    cfg.write(path, {"Performance": {"FrameRateLimit": "60"}})

    text = path.read_text(encoding="utf-8")
    assert "FrameRateLimit=60" in text
    assert "[Performance]" in text
    assert "VSyncMode" not in text, "we must not invent values the user never set"
    assert "[Graphics]" not in text
    assert cfg.read(path) == {"Performance": {"FrameRateLimit": "60"}}


def test_an_existing_file_keeps_its_comments_and_order(tmp_path):
    """
    a user can open this in Notepad. reserialising it would drop NovaFix's
    comments and reorder their sections, which looks like corruption.
    """
    path = tmp_path / "NovaFix.ini"
    path.write_text(REAL_SHAPE)

    cfg.write(path, {"Performance": {"FrameRateLimit": "120"}})

    text = path.read_text(encoding="utf-8")
    assert "FrameRateLimit=120" in text
    assert "; Unlimited | 15..1000" in text, "their comment was dropped"
    assert "; SYSTEM" in text
    assert text.index("[Performance]") < text.index("[Graphics]")
    # nothing else moved
    assert cfg.read(path)["Performance"]["VSyncMode"] == "GameDefault"
    assert cfg.read(path)["Graphics"]["DisplayMode"] == "Borderless"


def test_a_new_key_is_appended_to_its_existing_section(tmp_path):
    path = tmp_path / "NovaFix.ini"
    path.write_text(REAL_SHAPE)

    cfg.write(path, {"Graphics": {"MSAA": "4"}})

    values = cfg.read(path)
    assert values["Graphics"]["MSAA"] == "4"
    assert values["Graphics"]["DisplayMode"] == "Borderless"


def test_a_new_section_is_appended(tmp_path):
    path = tmp_path / "NovaFix.ini"
    path.write_text("[Performance]\nFrameRateLimit=60\n")

    cfg.write(path, {"Input": {"ControllerVibration": "Off"}})

    assert cfg.read(path)["Input"]["ControllerVibration"] == "Off"
    assert cfg.read(path)["Performance"]["FrameRateLimit"] == "60"


def test_crlf_is_preserved(tmp_path):
    """their writer uses CRLF; rewriting with LF makes every line look changed"""
    path = tmp_path / "NovaFix.ini"
    path.write_bytes(b"[Performance]\r\nFrameRateLimit=60\r\n")

    cfg.write(path, {"Performance": {"FrameRateLimit": "120"}})

    assert path.read_bytes() == b"[Performance]\r\nFrameRateLimit=120\r\n"


def test_the_config_sits_next_to_the_proxy_not_the_exe(tmp_path):
    path = cfg.config_path(tmp_path, "alba_data/prog/win/bin")
    assert path == tmp_path / "alba_data" / "prog" / "win" / "bin" / "NovaFix.ini"


def test_an_unknown_key_is_refused(tmp_path):
    """their parser reads this file; a bad value there is our fault"""
    result = cfg.apply_changes(tmp_path, ".", "ffxiii2",
                               {"Performance": {"MadeUpSetting": "1"}})

    assert result["ok"] is False
    assert "isn't a setting the patcher changes" in result["error"]
    assert not (tmp_path / "NovaFix.ini").exists()


def test_a_value_outside_the_documented_choices_is_refused(tmp_path):
    result = cfg.apply_changes(tmp_path, ".", "ffxiii2",
                               {"Performance": {"VSync": "Maybe"}})

    assert result["ok"] is False
    assert "On, Double Buffered" in result["error"]
    assert not (tmp_path / "NovaFix.ini").exists()


def test_a_free_numeric_value_is_allowed_where_they_document_a_range(tmp_path):
    """FrameRateLimit is 'Unlimited | 15..1000', not a fixed list"""
    result = cfg.apply_changes(tmp_path, ".", "ffxiii2",
                               {"Performance": {"FrameRateLimit": "75"}})

    assert result["ok"] is True
    assert cfg.read(tmp_path / "NovaFix.ini")["Performance"]["FrameRateLimit"] == "75"


def test_a_note_is_for_the_player_and_lets_no_number_through(tmp_path):
    """
    a note used to double as the switch for free numbers, so the range had to
    be spelled out in it ("Unlimited, or 15 to 1000") for a player to read. The
    range is the row's own now, and a row with a note and no range takes names
    only, the many-key rows above all, which have nothing to write for one.
    And the note is one line in the help bar beside an open drawer, which
    fits about ninety characters
    """
    def refused(game, section, key, value):
        result = cfg.apply_changes(tmp_path, ".", game, {section: {key: value}})
        return result["ok"] is False and not (tmp_path / "NovaFix.ini").exists() \
            and not (tmp_path / "SaviorsPatch.ini").exists()

    assert refused("ffxiii2", "Performance", "FrameRateLimit", "5")
    assert refused("ffxiii2", "Performance", "FrameRateLimit", "2000")
    assert refused("ffxiii2", "Quality", "AnisotropicFiltering", "3")
    assert refused("ffxiii3", "Quality", "Shadows", "123")
    assert refused("ffxiii2", "Performance", "FrameRateLimit", "\u00b2")
    said = cfg.apply_changes(tmp_path, ".", "ffxiii2", {"Performance": {"FrameRateLimit": "5"}})
    assert "or a number from 15 to 1000" in said["error"], said
    for game in ("ffxiii", "ffxiii2", "ffxiii3"):
        for setting in cfg.settings_for(game):
            assert not re.search(r"\d+ to \d+", setting.note), setting.note
            assert len(setting.note) <= 90, setting.note
    from patcher import voices
    for note in (voices.NOTE, voices.NOT_YET[1], voices.NOT_THERE[1]):
        assert len(note) <= 90, note


def test_what_a_choice_costs_is_said_when_its_picked():
    """
    DXVK needs a dll the user adds and both other renderers leave the shader
    off. That's for whoever picks one, so it's said then, beside Apply, rather
    than in the note everyone hovering the row reads
    """
    for game in ("ffxiii", "ffxiii2", "ffxiii3"):
        rows = {s.key: s.as_dict() for s in cfg.settings_for(game)}
        renderer = rows["D3D9Backend"]
        assert set(renderer["warn"]) == {"System", "LocalDXVK"}
        assert "dxvk.dll" in renderer["warn"]["LocalDXVK"]
        assert "dxvk" not in renderer["note"].lower(), "the note is for everyone"
        for row in rows.values():
            assert set(row["warn"]) <= set(row["choices"]), row["key"]


def test_lightning_returns_has_no_frame_pacing_settings():
    """
    NovaFix's default_config skips the whole OUTPUT block and the [Graphics]
    section for LR. offering them would write keys their parser ignores.
    """
    written = {k for s in cfg.settings_for("ffxiii3") for k in s.real_keys}
    assert "FrameRateLimit" not in written
    assert "VSyncMode" not in written
    assert "TripleBuffering" not in written
    # nothing LR offers may land in NovaFix's [Graphics], which it never writes
    assert not any(s.target_section == "Graphics"
                   for s in cfg.settings_for("ffxiii3"))
    assert "ShowFpsCounter" in written


def test_xiii2_gets_the_full_set():
    written = {k for s in cfg.settings_for("ffxiii2") for k in s.real_keys}
    assert {"FrameRateLimit", "VSyncMode", "TripleBuffering", "DisplayMode",
            "Width", "Height", "MSAA", "AnisotropicFiltering",
            "ShadowResolution"} <= written


def test_current_reports_unset_values_as_none(tmp_path):
    """the UI shows 'NovaFix default' rather than inventing something"""
    state = cfg.current(tmp_path, ".", "ffxiii2")

    assert state["exists"] is False
    frame = next(s for s in state["settings"] if s["key"] == "FrameRateLimit")
    assert frame["value"] is None
    assert "Unlimited" in frame["choices"]


def test_current_reflects_what_is_on_disk(tmp_path):
    (tmp_path / "NovaFix.ini").write_text(REAL_SHAPE)

    state = cfg.current(tmp_path, ".", "ffxiii2")

    assert state["exists"] is True
    display = next(s for s in state["settings"] if s["key"] == "DisplayMode")
    assert display["value"] == "Borderless"


def test_picking_a_resolution_writes_the_pair_novafix_reads(tmp_path):
    """
    Resolution is ours, not NovaFix's. It expands into the Width/Height keys it
    does have, so the user picks one thing instead of two numbers that can
    disagree with each other.
    """
    result = cfg.apply_changes(tmp_path, ".", "ffxiii2",
                               {"Display": {"Resolution": "2560x1440"}})

    assert result["ok"] is True
    values = cfg.read(tmp_path / "NovaFix.ini")["Graphics"]
    assert values["Width"] == "2560" and values["Height"] == "1440"
    assert "Resolution" not in values, "NovaFix has no such key"


def test_an_existing_pair_shows_back_as_a_resolution(tmp_path):
    (tmp_path / "NovaFix.ini").write_text("[Graphics]\nWidth=1920\nHeight=1080\n")

    state = cfg.current(tmp_path, ".", "ffxiii2")

    resolution = next(s for s in state["settings"] if s["key"] == "Resolution")
    assert resolution["value"] == "1920x1080"


def test_a_resolution_we_do_not_offer_is_refused(tmp_path):
    """
    these games render 16:9 internally, so an arbitrary size is a stretched HUD
    rather than a feature.
    """
    result = cfg.apply_changes(tmp_path, ".", "ffxiii2",
                               {"Graphics": {"Resolution": "1234x567"}})

    assert result["ok"] is False
    assert not (tmp_path / "NovaFix.ini").exists()


def test_shadow_quality_shows_the_same_labels_as_the_overlay():
    """
    the in-game menu says "Very high (4096)". showing a bare 4096 makes someone
    who has seen both work out that they are the same setting.
    """
    shadow = next(s for s in cfg.settings_for("ffxiii2") if s.key == "ShadowResolution")
    labels = shadow.as_dict()["choiceLabels"]
    assert "Very High (4096)" in labels
    assert "Low (512)" in labels


def test_the_launcher_override_is_set_for_you_and_is_not_a_row(tmp_path):
    """
    It used to be a row with exactly one correct answer. Square Enix's launcher
    owns resolution, shadows and anti-aliasing, and NovaFix ignores every
    Graphics key while the override is off, so leaving it to the user meant a
    panel where changing Shadow quality did nothing until you also found a
    switch three rows up.

    Now changing any Graphics setting turns it on. The row is gone and the
    behaviour is not.
    """
    assert not any(s.key == "OverrideLauncherGraphics"
                   for s in cfg.settings_for("ffxiii2")), "it came back as a row"

    cfg.apply_changes(tmp_path, ".", "ffxiii2", {"Quality": {"ShadowResolution": "4096"}})
    values = cfg.read(tmp_path / "NovaFix.ini")["Graphics"]

    assert values["ShadowResolution"] == "4096"
    assert values["OverrideLauncherGraphics"] == "On"


def test_every_setting_we_write_exists_in_the_shipped_binary():
    """
    The one thing that IS version-coupled to NovaFix.

    Our writer is partial and line-based, so a key they renamed cannot corrupt
    their file. What it does instead is worse to diagnose: we append a key
    nothing reads, the UI reports the setting saved, and nothing changes in
    game. That is the same silent-success shape as a patch run that crashes and
    says "All patches applied".

    Checking the strings in the binary we actually ship turns that into a
    build-time failure. RedirectExecutableReads is the proof this works: it is
    absent from stock NovaFix, which is exactly why it is not in SETTINGS.
    """
    from patcher.novafix_config import SETTINGS
    from patcher.paths import bundled_data_root

    proxy = bundled_data_root() / "assets" / "mods" / "novafix" / "d3d9.dll"
    if not proxy.is_file():
        pytest.skip("NovaFix is not committed in this checkout")

    blob = proxy.read_bytes()

    def present(token: str) -> bool:
        # their config code uses wide strings, the diagnostics counters narrow
        return token.encode() in blob or token.encode("utf-16-le") in blob

    # real_keys, not key. A merged row like VSync or Graphics settings from is
    # named for the question it asks, and that name is never written anywhere:
    # what has to exist in their build is the keys underneath it.
    # and a preset row writes nothing of its own, only the rows it sets
    wanted = {k for s in SETTINGS if s.kind != "preset" for k in s.real_keys}
    missing = sorted({k for k in wanted if not present(k)})
    assert not missing, (
        f"these keys are not in the shipped NovaFix build: {missing}. "
        "Writing them would be a no-op that the settings panel reports as saved.")


def test_the_addon_folder_names_match_the_shipped_binary():
    """
    NovaFix picks the first of these that exists and creates the first one if
    none do. Getting the list or its ORDER wrong means installing into a folder
    it is not reading, or creating one that steals priority from the folder a
    user's existing mods live in.
    """
    from patcher.novafix_config import ADDON_DIR_NAMES
    from patcher.paths import bundled_data_root

    proxy = bundled_data_root() / "assets" / "mods" / "novafix" / "d3d9.dll"
    if not proxy.is_file():
        pytest.skip("NovaFix is not committed in this checkout")

    blob = proxy.read_bytes()

    # one contiguous run, in order, with the padding between entries left
    # loose. the linker aligns those strings, so pinning the exact byte layout
    # would fail on a rebuild that changed nothing we care about. anchoring as
    # a run is also what keeps "dll" from matching inside "d3d9.dll".
    run = b"\x00+".join(
        re.escape(name.encode("utf-16-le")) for name in ADDON_DIR_NAMES)

    assert re.search(run, blob), (
        f"{list(ADDON_DIR_NAMES)} is not the probe run in this NovaFix build, in "
        "that order. The order decides which folder wins when two exist, and "
        "installing into one it does not read is an add-on that never loads.")


def test_nothing_in_the_shipped_build_lets_an_outside_process_reload_the_config():
    """
    This is the constraint the Apply button is designed around, so it is worth
    pinning rather than remembering.

    NovaFix reads NovaFix.ini at startup. It imports GetPrivateProfileStringW
    and WritePrivateProfileStringW but no directory-change API, and the only
    non-D3D9 exports are NovaFix_IsOverlayOpen and NovaFix_RestartHelper. No
    watcher and no reload entry point means a running instance cannot be told
    about a config change from outside the game process, whatever we write.

    If a future build gains one, this fails and the panel can stop saying
    "restart it".
    """
    from patcher.paths import bundled_data_root

    proxy = bundled_data_root() / "assets" / "mods" / "novafix" / "d3d9.dll"
    if not proxy.is_file():
        pytest.skip("NovaFix is not committed in this checkout")

    blob = proxy.read_bytes()

    def imported(name):
        return name.encode() in blob

    assert imported("GetPrivateProfileStringW"), "the ini reader moved"
    for watcher in ("ReadDirectoryChangesW", "FindFirstChangeNotificationW",
                    "FindFirstChangeNotificationA"):
        assert not imported(watcher), (
            f"this build imports {watcher}, so it may now notice a config change "
            "on its own. Apply could stop telling people to restart.")


def test_the_graphics_drawer_offers_no_ambient_occlusion():
    """
    Asked for explicitly, and worth a test rather than a careful list, because
    that mod carries 28 Ao* keys and adding "just one" later is a one-line
    change nobody would think to question.

    It is also the right call on its own: ambient occlusion is a look, and this
    drawer is for the settings that decide whether the game runs well.
    """
    from patcher.novafix_config import settings_for

    offered = [s.key for s in settings_for("ffxiii3")]
    assert not [k for k in offered if k.lower().startswith("ao")], offered


def saviors_in(folder: Path) -> None:
    """Savior's Patch's dll as far as the drawer can tell: one naming the ini it reads"""
    (folder / "dinput8.dll").write_bytes(b"MZ\x90\x00 ... SaviorsPatch.ini ...")


def test_saviors_rows_wait_for_its_dll(tmp_path):
    """
    it's the user's own download now, so an LR without it gets NovaFix's rows
    and nothing else, and a dinput8.dll of somebody else's, LayeredFS's say,
    doesn't count as it
    """
    from patcher.novafix_config import SAVIORS_SETTINGS, current

    theirs = {s.label for s in SAVIORS_SETTINGS}

    def shown():
        state = current(tmp_path, ".", "ffxiii3")
        return ({s["label"] for s in state["settings"]} & theirs,
                [f["name"] for f in state["files"]])

    assert shown() == (set(), ["NovaFix.ini"])
    (tmp_path / "dinput8.dll").write_bytes(b"MZ layeredLS.ini mod_data")
    assert shown() == (set(), ["NovaFix.ini"])
    saviors_in(tmp_path)
    assert shown() == (theirs, ["NovaFix.ini", "SaviorsPatch.ini"])


def test_a_saviors_setting_is_refused_with_it_gone(tmp_path):
    """a drawer opened before Remove, saved after: nothing reads that file now"""
    from patcher.novafix_config import apply_changes

    result = apply_changes(tmp_path, ".", "ffxiii3", {"Quality": {"Shadows": "Ultra"}})

    assert not result["ok"]
    assert result["error"] == ("Shadow Quality is a Savior's Patch setting, "
                               "and Savior's Patch isn't in")
    assert not (tmp_path / "SaviorsPatch.ini").exists()


def test_lr_settings_land_in_the_right_file_and_shape(tmp_path):
    """
    Two mods, two files, in the same folder, and they disagree about
    everything: NovaFix.ini has [Sections], named values and ";" comments;
    SaviorsPatch.ini is flat, integers only, "#" comments. A Savior's key
    written under a [Section] header is a key its parser never finds.
    """
    from patcher.novafix_config import apply_changes

    saviors_in(tmp_path)

    result = apply_changes(tmp_path, ".", "ffxiii3", {
        "Performance": {"D3D9Backend": "LocalDXVK", "Stutter": "Aggressive"},
        "Quality": {"Shadows": "Very High"},
    })
    assert result["ok"], result.get("error")

    novafix = (tmp_path / "NovaFix.ini").read_text(encoding="utf-8")
    saviors = (tmp_path / "SaviorsPatch.ini").read_text(encoding="utf-8")

    assert "[Performance]" in novafix and "D3D9Backend=LocalDXVK" in novafix
    assert "ShadowMapRes" not in novafix, "a Savior's key leaked into NovaFix.ini"

    assert "ShadowMapRes=4096" in saviors and "LoaderThrottle=1" in saviors
    # the row names are ours and must never reach the file
    assert "Shadows=" not in saviors and "Stutter=" not in saviors
    assert "[" not in saviors, "SaviorsPatch.ini has no sections and must not gain one"
    assert ";" not in saviors, "its comment character is #, not ;"
    assert "D3D9Backend" not in saviors, "a NovaFix key leaked into SaviorsPatch.ini"


def test_editing_a_saviors_file_leaves_the_keys_we_do_not_manage_alone(tmp_path):
    """
    That file holds around 140 keys and we offer 17 of them. The other 123,
    ambient occlusion tuning very much included, belong to the user and the
    mod's own menu. Rewriting the file rather than editing it in place would
    silently reset every one of them.
    """
    from patcher.novafix_config import apply_changes

    saviors_in(tmp_path)

    path = tmp_path / "SaviorsPatch.ini"
    path.write_bytes(b"# Savior's Patch - Performance & graphics\r\n"
                     b"DiscardFix=1\r\nAoEnable=2\r\nAoRadius100=250\r\n"
                     b"ShadowMapRes=0\r\n")

    apply_changes(tmp_path, ".", "ffxiii3", {"Quality": {"Shadows": "Ultra"}})

    after = path.read_bytes()
    assert b"ShadowMapRes=8192" in after
    assert b"AoEnable=2" in after and b"AoRadius100=250" in after
    assert b"DiscardFix=1" in after
    assert after.startswith(b"# Savior's Patch"), "its own header survived"
    assert b"\r\n" in after, "CRLF preserved"


def test_picking_a_frame_rate_unlocks_the_limiter_for_you(tmp_path):
    """
    The reason these rows got merged. The mod ships three separate switches and
    picking 60fps without the other two does literally nothing: TargetFpsX100
    is only read while UnlockFramerate is on, and above 30fps the game's
    interact prompts and stamina drain are wrong until SimDeltaFix is too.

    Three switches where two of them exist to make the third work is not
    customisation, it is a puzzle. One row, and the answer is correct.
    """
    from patcher.novafix_config import apply_changes

    saviors_in(tmp_path)

    apply_changes(tmp_path, ".", "ffxiii3", {"Performance": {"FrameRate": "120 FPS"}})
    written = (tmp_path / "SaviorsPatch.ini").read_text(encoding="utf-8")

    assert "TargetFpsX100=12000" in written
    assert "UnlockFramerate=1" in written, "picked a frame rate that cannot apply"
    assert "SimDeltaFix=1" in written


def test_a_merged_row_reads_back_as_the_option_that_set_it(tmp_path):
    """
    A row standing for several keys has to recognise its own handiwork, or the
    panel reopens showing Default over settings it just wrote and the next save
    reverts them.
    """
    from patcher.novafix_config import apply_changes, current

    saviors_in(tmp_path)

    apply_changes(tmp_path, ".", "ffxiii3", {
        "Quality": {"AntiAliasing": "MSAA 4x", "Shadows": "Ultra, Softened"},
        "Display": {"Resolution": "1440p"},
    })
    shown = {s["label"]: s["value"] for s in current(tmp_path, ".", "ffxiii3")["settings"]}

    assert shown["Anti-Aliasing"] == "MSAA 4x"
    assert shown["Shadow Quality"] == "Ultra, Softened"
    assert shown["Render Resolution"] == "1440p"
    # nothing was touched here, and Default is the honest answer for that
    assert shown["Texture Filtering"] is None


def test_a_hand_edited_combination_reads_as_default_not_the_nearest_label(tmp_path):
    """
    Someone who set MsaaSamples by hand in the mod's own menu is in a state no
    preset describes. Showing the closest one would be the panel claiming a
    state the file is not in, and the next save would quietly overwrite a choice
    they made deliberately.
    """
    from patcher.novafix_config import current

    saviors_in(tmp_path)

    (tmp_path / "SaviorsPatch.ini").write_text(
        "MsaaSamples=2\nSsaaScale=150\nFxaaOff=0\n")
    shown = {s["label"]: s["value"] for s in current(tmp_path, ".", "ffxiii3")["settings"]}

    assert shown["Anti-Aliasing"] is None


def test_no_two_lr_rows_write_the_same_key():
    """
    The overlap check. Two rows owning one key means whichever saves last wins
    and the other row starts lying about what is on disk, which is exactly the
    mess the merge was meant to end.
    """
    from patcher.novafix_config import SAVIORS_SETTINGS

    seen = {}
    for setting in SAVIORS_SETTINGS:
        for key in setting.real_keys:
            assert key not in seen, f"{key} is written by both {seen[key]} and {setting.label}"
            seen[key] = setting.label


def test_every_lr_option_writes_the_same_keys_as_its_siblings():
    """
    Options within a row have to agree on which keys they set. One that leaves
    a key out cannot undo what a sibling wrote, so switching from MSAA 4x to
    Off would turn FXAA on and leave MsaaSamples=4 sitting underneath it.
    """
    from patcher.novafix_config import SAVIORS_SETTINGS

    for setting in SAVIORS_SETTINGS:
        if not setting.expand:
            continue
        shapes = {choice: tuple(sorted(k for k, _ in pairs))
                  for choice, pairs in setting.expand}
        assert len(set(shapes.values())) == 1, (
            f"{setting.label} options disagree about which keys they set: {shapes}")


def _untitled(text: str) -> list:
    """words in `text` that do not open with a capital or a digit"""
    import re

    words = re.split(r"[ /\-]+", text)
    bad = []
    for word in words:
        stripped = word.lstrip("(\"'")
        if stripped and not (stripped[0].isupper() or stripped[0].isdigit()):
            bad.append(word)
    return bad


def test_every_label_and_option_is_title_case():
    """
    The convention every game settings menu uses, ours included now. It was
    sentence case: "Frame rate", "Anti-aliasing", "NPC spawn distance", "Very
    high (4096)", "This patcher".

    Checked as a rule rather than a list, because the failure is one row added
    later in the old style, which nobody notices next to eleven that are right.

    Hyphens split, so Anti-Aliasing needs both halves. A word opening with a
    digit passes, which covers 16x, 4K, 1440p and 60 FPS, and an acronym passes
    on its first letter, which covers FXAA, MSAA, VSync and LocalDXVK.
    """
    from patcher.novafix_config import settings_for

    wrong = {}
    for game in ("ffxiii", "ffxiii2", "ffxiii3"):
        for setting in settings_for(game):
            shown = setting.as_dict()
            for text in [shown["label"], *shown["choiceLabels"]]:
                bad = _untitled(text)
                if bad:
                    wrong[text] = bad

    assert not wrong, f"not title case: {wrong}"


INPUT_AS_NOVAFIX_WRITES_IT = """\
[Input]

; CONTROLLER
; Automatic | Controller | Keyboard
PromptMode=Automatic
ControllerVibration=On

; OVERLAY
; Disabled | F8 | F9 | F10 | F11 | F12 | Grave | Insert | Home
OverlayHotkey=F10
AlternateOverlayHotkey=Grave
; Disabled | Both
OverlayGamepadHotkey=Both
"""
POPUP_AS_NOVAFIX_WRITES_IT = """\
[Performance]

; NOTIFICATIONS
StartupSound=On
MenuSounds=On
; Never | WhenChanged | EveryLaunch
LoadedModulesNotificationMode=EveryLaunch

; RENDERER
; Automatic | System | LocalDXVK
D3D9Backend=Automatic
"""


def menu_only(store):
    """the popup looked at already, so a test of the menu keys sees just them"""
    store.mkdir(parents=True, exist_ok=True)
    (store / cfg.POPPED).write_text('{"wrote": false}')
    return store


def test_novafixs_menu_keys_go_off_where_it_wrote_them(tmp_path):
    """
    the Settings drawer has everything a player needs out of NovaFix's menu,
    and ` and F10 open it mid game. NovaFix won't take every key off, so F8
    stays, which nothing else answers to
    """
    path = tmp_path / "NovaFix.ini"
    path.write_bytes(INPUT_AS_NOVAFIX_WRITES_IT.replace("\n", "\r\n").encode())

    assert cfg.first_look(tmp_path, ".", tmp_path / "undo")
    keys = cfg.read(path)["Input"]
    assert (keys["OverlayHotkey"], keys["AlternateOverlayHotkey"],
            keys["OverlayGamepadHotkey"]) == ("F8", "Disabled", "Disabled")
    # their comments and the rest of the section as they were
    text = path.read_bytes().decode()
    assert "; Disabled | F8 | F9 | F10 | F11 | F12 | Grave | Insert | Home\r\n" in text
    assert "PromptMode=Automatic\r\n" in text
    # and once is all: what's there now is ours, which is a choice like any
    assert not cfg.first_look(tmp_path, ".", tmp_path / "undo")


def test_the_one_key_left_isnt_one_novafix_would_put_back(tmp_path):
    """Normalize() only puts F10 back when both keyboard keys are Disabled"""
    assert cfg.MENU_KEYS["OverlayHotkey"] not in ("Disabled", "F10", "Grave", "Home", "F12")
    assert cfg.MENU_KEYS["AlternateOverlayHotkey"] == "Disabled"


def test_keys_somebody_set_are_left_alone(tmp_path):
    path = tmp_path / "NovaFix.ini"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT.replace("OverlayHotkey=F10", "OverlayHotkey=F11"))
    before = path.read_bytes()
    assert not cfg.first_look(tmp_path, ".", menu_only(tmp_path / "undo"))
    assert path.read_bytes() == before


def test_a_file_novafix_hasnt_written_yet_gets_just_the_keys(tmp_path):
    """NovaFix fills in the rest itself on launch, as with any key we write"""
    assert cfg.first_look(tmp_path, ".", tmp_path / "undo")
    assert cfg.read(tmp_path / "NovaFix.ini") == {"Input": dict(cfg.MENU_KEYS),
                                                  "Performance": dict(cfg.POPUP_KEYS)}


def test_an_older_builds_gamepad_names_count_as_its_default(tmp_path):
    path = tmp_path / "NovaFix.ini"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT.replace("OverlayGamepadHotkey=Both",
                                                       "OverlayGamepadHotkey=StickClicks"))
    assert cfg.first_look(tmp_path, ".", tmp_path / "undo")
    assert cfg.read(path)["Input"]["OverlayGamepadHotkey"] == "Disabled"


def test_once_means_once_even_when_they_go_back_to_novafixs_keys(tmp_path):
    """
    every game switch asks, so without a note of having asked, a player who
    put F10 back got it taken off again the next time they opened the game
    """
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT)
    assert cfg.first_look(tmp_path, ".", store)

    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT)
    assert not cfg.first_look(tmp_path, ".", store)
    assert path.read_text(encoding="utf-8") == INPUT_AS_NOVAFIX_WRITES_IT


def test_keys_that_were_somebodys_choice_are_never_looked_at_again(tmp_path):
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT.replace("OverlayHotkey=F10", "OverlayHotkey=F11"))
    assert not cfg.first_look(tmp_path, ".", menu_only(store))
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT)
    assert not cfg.first_look(tmp_path, ".", store)
    assert path.read_text(encoding="utf-8") == INPUT_AS_NOVAFIX_WRITES_IT


def test_the_keys_are_read_the_way_novafix_reads_them(tmp_path):
    """
    GetPrivateProfileString: any case, the first section and the first key.
    Read any other way, a key the player set looked like a default
    """
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text("[input]\noverlayhotkey=Insert\nAlternateOverlayHotkey=Grave\n"
                    "OverlayHotkey=F10\n")
    before = path.read_bytes()
    assert not cfg.first_look(tmp_path, ".", menu_only(store))
    assert path.read_bytes() == before


def test_the_keys_go_where_novafix_reads_them(tmp_path):
    """a lowercase [input] used to get a second [Input] after it that NovaFix never reads"""
    path = tmp_path / "NovaFix.ini"
    path.write_text("[input]\noverlayhotkey=F10\n\n[Graphics]\nA=1\n\n[Input]\nOverlayHotkey=F10\n")
    assert cfg.first_look(tmp_path, ".", menu_only(tmp_path / "undo"))
    assert path.read_text(encoding="utf-8") == ("[input]\nOverlayHotkey=F8\nAlternateOverlayHotkey=Disabled\n"
                                "OverlayGamepadHotkey=Disabled\n\n[Graphics]\nA=1\n\n"
                                "[Input]\nOverlayHotkey=F10\n")
    assert cfg.profile(path)["input"]["overlayhotkey"] == "F8"


def test_keys_added_to_two_sections_each_land_in_their_own(tmp_path):
    """the first insert used to shift the lines the second was aimed at"""
    path = tmp_path / "NovaFix.ini"
    path.write_text("[A]\nx=1\ny=2\nz=3\n[B]\nw=1\n")
    cfg.write(path, {"A": {"New": "a"}, "B": {"Other": "b"}})
    assert path.read_text(encoding="utf-8") == "[A]\nx=1\ny=2\nz=3\nNew=a\n[B]\nw=1\nOther=b\n"


def test_revert_puts_the_keys_back_as_they_were(tmp_path):
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT.replace("OverlayGamepadHotkey=Both",
                                                       "OverlayGamepadHotkey=StickClicks"))
    assert cfg.first_look(tmp_path, ".", store)
    # one the player changed since is theirs
    cfg.write(path, {"Input": {"OverlayHotkey": "Insert"}})

    assert cfg.put_back(tmp_path, ".", store)
    keys = cfg.read(path)["Input"]
    assert (keys["OverlayHotkey"], keys["AlternateOverlayHotkey"],
            keys["OverlayGamepadHotkey"]) == ("Insert", "Grave", "StickClicks")
    assert not (store / cfg.QUIETED).exists()
    # and a NovaFix put on after starts over: the popup's NovaFix's own again,
    # and Insert is still somebody's choice
    assert cfg.first_look(tmp_path, ".", store)
    assert cfg.read(path)["Input"]["OverlayHotkey"] == "Insert"
    assert cfg.read(path)["Performance"] == cfg.POPUP_KEYS


def test_revert_puts_back_a_key_that_wasnt_there_as_novafixs_own(tmp_path):
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text("[Graphics]\nDisplayMode=Borderless\n")
    assert cfg.first_look(tmp_path, ".", store)
    assert cfg.put_back(tmp_path, ".", store)
    assert cfg.read(path)["Input"] == {"OverlayHotkey": "F10", "AlternateOverlayHotkey": "Grave",
                                       "OverlayGamepadHotkey": "Both"}
    assert cfg.read(path)["Performance"] == {"LoadedModulesNotificationMode": "EveryLaunch"}


def test_revert_takes_away_a_file_it_made_for_the_keys(tmp_path):
    """one made before NovaFix ever ran, with nothing in it since but ours"""
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    assert cfg.first_look(tmp_path, ".", store)
    assert cfg.put_back(tmp_path, ".", store)
    assert not path.exists()

    # one NovaFix has filled in since is its own, and only the keys go back
    assert cfg.first_look(tmp_path, ".", store)
    cfg.write(path, {"Graphics": {"DisplayMode": "Borderless"}})
    assert cfg.put_back(tmp_path, ".", store)
    assert cfg.read(path)["Input"]["OverlayHotkey"] == "F10"
    assert cfg.read(path)["Graphics"] == {"DisplayMode": "Borderless"}


def test_a_novafix_still_there_after_revert_keeps_the_keys_it_was_given(tmp_path):
    """the user's own, which Revert put back: turning its keys off again on the next switch undid Revert"""
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT)
    assert cfg.first_look(tmp_path, ".", store)
    assert cfg.put_back(tmp_path, ".", store, stays="ab" * 32)
    assert not cfg.first_look(tmp_path, ".", store)
    assert cfg.looked_at(store, cfg.QUIETED) == "ab" * 32
    keys = cfg.read(path)["Input"]
    assert (keys["OverlayHotkey"], keys["AlternateOverlayHotkey"]) == ("F10", "Grave")


def test_a_marker_that_cant_be_read_stays_for_the_next_revert(tmp_path, monkeypatch):
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT)
    assert cfg.first_look(tmp_path, ".", store)
    real = Path.read_bytes

    def held(self):
        if self.name == cfg.QUIETED:
            raise PermissionError(13, "in use", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", held)
    with pytest.raises(OSError):
        cfg.put_back(tmp_path, ".", store)
    assert (store / cfg.QUIETED).exists()


def test_novafixs_add_on_popup_goes_to_every_launch(tmp_path):
    """
    NovaFix 1.0.2 lists the add-ons it loaded on every launch, and once is
    enough: the first time it finds them, and again only when they change
    """
    path = tmp_path / "NovaFix.ini"
    path.write_bytes((INPUT_AS_NOVAFIX_WRITES_IT + "\n" + POPUP_AS_NOVAFIX_WRITES_IT)
                     .replace("\n", "\r\n").encode())
    assert cfg.first_look(tmp_path, ".", tmp_path / "undo")
    # in place, under the line saying what it takes
    assert ("; Never | WhenChanged | EveryLaunch\r\nLoadedModulesNotificationMode=WhenChanged\r\n"
            in path.read_bytes().decode())
    assert not cfg.first_look(tmp_path, ".", tmp_path / "undo")


@pytest.mark.parametrize("theirs, after", [
    ("Never", "Never"), ("WhenChanged", "WhenChanged"), ("1", "1"),
    # an older build's number for EveryLaunch, and any case, read as ReadChoice reads them
    ("2", "WhenChanged"), ("everylaunch", "WhenChanged"),
])
def test_a_popup_somebody_set_stays_theirs(tmp_path, theirs, after):
    path = tmp_path / "NovaFix.ini"
    path.write_text(POPUP_AS_NOVAFIX_WRITES_IT.replace("=EveryLaunch", f"={theirs}"))
    cfg.first_look(tmp_path, ".", tmp_path / "undo")
    assert cfg.profile(path)["performance"]["loadedmodulesnotificationmode"] == after


def test_a_game_patched_before_the_popup_still_gets_its_look(tmp_path):
    """its menu keys looked at by an older patcher and F10 put back by hand since, which stays"""
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT + "\n" + POPUP_AS_NOVAFIX_WRITES_IT)
    store.mkdir()
    (store / cfg.QUIETED).write_text('{"wrote": true, "was": {}, "made": false}')
    assert cfg.first_look(tmp_path, ".", store)
    values = cfg.read(path)
    assert values["Input"]["OverlayHotkey"] == "F10"
    assert values["Performance"]["LoadedModulesNotificationMode"] == "WhenChanged"


def test_revert_puts_the_popup_back_unless_somebody_changed_it(tmp_path):
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(POPUP_AS_NOVAFIX_WRITES_IT)
    assert cfg.first_look(tmp_path, ".", store)
    assert cfg.put_back(tmp_path, ".", store)
    assert cfg.read(path)["Performance"]["LoadedModulesNotificationMode"] == "EveryLaunch"
    assert not (store / cfg.POPPED).exists()

    assert cfg.first_look(tmp_path, ".", store)
    cfg.write(path, {"Performance": {"LoadedModulesNotificationMode": "Never"}})
    cfg.put_back(tmp_path, ".", store)
    assert cfg.read(path)["Performance"]["LoadedModulesNotificationMode"] == "Never"


def test_revert_takes_away_a_file_an_older_look_made_and_the_popup_went_in_since(tmp_path):
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    # an older patcher's look, the menu alone, making the file for it
    assert cfg.first_look(tmp_path, ".", menu_only(store))
    (store / cfg.POPPED).unlink()
    assert cfg.first_look(tmp_path, ".", store)
    assert cfg.put_back(tmp_path, ".", store)
    assert not path.exists()


def test_the_users_own_novafix_after_an_older_revert_keeps_its_popup(tmp_path):
    """that Revert named their dll in the menu's marker alone, and the dll is theirs, popup and all"""
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(POPUP_AS_NOVAFIX_WRITES_IT)
    before = path.read_bytes()
    store.mkdir()
    (store / cfg.QUIETED).write_text('{"wrote": false, "dll": "%s"}' % ("ab" * 32))
    assert not cfg.first_look(tmp_path, ".", store, current_dll=lambda: "ab" * 32)
    assert path.read_bytes() == before
    assert cfg.looked_at(store, cfg.POPPED) is None
    # ours put on since starts over, both
    assert cfg.first_look(tmp_path, ".", store, current_dll=lambda: "cd" * 32)
    assert cfg.read(path)["Performance"]["LoadedModulesNotificationMode"] == "WhenChanged"
    assert cfg.read(path)["Input"]["OverlayHotkey"] == "F8"


@pytest.mark.parametrize("written, read", [
    ("LocalDXVK", "LocalDXVK"), ("localdxvk", "LocalDXVK"), ("2", "LocalDXVK"), ("7", "LocalDXVK"),
    (" 1 ", "System"), ("002", "LocalDXVK"), ("4294967295", "LocalDXVK"),
    # ParseUnsigned refuses these, and NovaFix keeps its default
    ("4294967296", "Automatic"), ("²", "Automatic"), ("+2", "Automatic"), ("-1", "Automatic"),
    ("DXVK", "Automatic"), ("", "Automatic"),
    # quotes keep their blanks in, which only a number gets past, and ReadText
    # gives up at 63 characters
    ('" System "', "Automatic"), ('" 2 "', "LocalDXVK"),
    ("0" * 61 + "2", "LocalDXVK"), ("0" * 62 + "2", "Automatic"),
])
def test_the_renderer_reads_the_way_novafix_reads_it(tmp_path, written, read):
    path = tmp_path / "NovaFix.ini"
    path.write_bytes(f"[Performance]\r\nD3D9Backend={written}\r\n".encode())
    assert cfg.renderer(cfg.profile(path)) == read


def test_a_popup_marker_that_isnt_json_doesnt_hold_revert_up(tmp_path):
    """it can't say what was there, so the popup stays as it is and the menu keys still go back"""
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text("[Input]\nOverlayHotkey=F10\n\n" + POPUP_AS_NOVAFIX_WRITES_IT)
    assert cfg.first_look(tmp_path, ".", store)
    (store / cfg.POPPED).write_bytes(b"{not json")
    assert cfg.put_back(tmp_path, ".", store)
    assert cfg.read(path)["Input"]["OverlayHotkey"] == "F10"
    assert cfg.read(path)["Performance"]["LoadedModulesNotificationMode"] == "WhenChanged"
    assert not (store / cfg.POPPED).exists() and not (store / cfg.QUIETED).exists()


def test_a_popup_the_look_left_isnt_put_back_over_a_later_choice(tmp_path):
    """Never when it looked, Every Launch picked in NovaFix's menu since"""
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(POPUP_AS_NOVAFIX_WRITES_IT.replace("=EveryLaunch", "=Never"))
    cfg.first_look(tmp_path, ".", store)
    cfg.write(path, {"Performance": {"LoadedModulesNotificationMode": "EveryLaunch"}})
    cfg.put_back(tmp_path, ".", store)
    assert cfg.read(path)["Performance"]["LoadedModulesNotificationMode"] == "EveryLaunch"


def test_a_file_the_look_made_with_somebodys_line_above_its_sections_stays(tmp_path):
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    assert cfg.first_look(tmp_path, ".", store)
    path.write_text("Note=mine\r\n" + path.read_text(encoding="utf-8"))
    assert cfg.put_back(tmp_path, ".", store)
    assert path.exists()


def test_a_utf16_novafix_ini_gets_its_look_in_utf16(tmp_path):
    """Windows reads it by its BOM and writes back the same; UTF-8 added to it was never read"""
    path = tmp_path / "NovaFix.ini"
    path.write_bytes(b"\xff\xfe" + (INPUT_AS_NOVAFIX_WRITES_IT + "\n" + POPUP_AS_NOVAFIX_WRITES_IT)
                     .replace("\n", "\r\n").encode("utf-16-le"))
    assert cfg.first_look(tmp_path, ".", tmp_path / "undo")
    raw = path.read_bytes()
    assert raw.startswith(b"\xff\xfe")
    text = raw[2:].decode("utf-16-le")
    assert "OverlayHotkey=F8\r\n" in text and "LoadedModulesNotificationMode=WhenChanged\r\n" in text
    assert cfg.profile(path)["performance"]["loadedmodulesnotificationmode"] == "WhenChanged"


def test_a_renderer_picked_while_dxvk_comes_off_stays_picked(tmp_path, monkeypatch):
    """
    the drawer saving on its thread between off_dxvk's read and its write, with
    a game switch's first look on a third: whichever goes first, nothing hangs
    and the drawer's pick is what's left
    """
    import threading
    import time

    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    real = cfg.profile

    def slow(p):
        found = real(p)
        time.sleep(0.05)
        return found

    monkeypatch.setattr(cfg, "profile", slow)
    for _ in range(3):
        path.write_text("[Performance]\r\nD3D9Backend=LocalDXVK\r\n"
                        "LoadedModulesNotificationMode=EveryLaunch\r\n")
        for marker in (cfg.QUIETED, cfg.POPPED):
            (store / marker).unlink(missing_ok=True)
        jobs = [threading.Thread(target=cfg.off_dxvk, args=(tmp_path, ".")),
                threading.Thread(target=cfg.apply_changes, args=(
                    tmp_path, ".", "ffxiii", {"Performance": {"D3D9Backend": "System"}})),
                threading.Thread(target=cfg.first_look, args=(tmp_path, ".", store))]
        jobs[0].start()
        time.sleep(0.01)
        for job in jobs[1:]:
            job.start()
        for job in jobs:
            job.join(timeout=10)
            assert not job.is_alive()
        assert real(path)["performance"]["d3d9backend"] == "System"
        assert real(path)["performance"]["loadedmodulesnotificationmode"] == "WhenChanged"


def test_what_the_first_look_writes_is_what_the_shipped_novafix_reads():
    """a name it doesn't know reads as its default, so the look would do nothing and say it did"""
    from patcher.paths import bundled_data_root

    proxy = bundled_data_root() / "assets" / "mods" / "novafix" / "d3d9.dll"
    if not proxy.is_file():
        pytest.skip("NovaFix is not committed in this checkout")
    blob = proxy.read_bytes()
    unknown = [text for section, ours, defaults in cfg.ONCE.values()
               for text in (section, *ours, *ours.values(), *(d[0] for d in defaults.values()))
               if text.encode("utf-16-le") not in blob]
    assert not unknown


def test_a_header_with_a_comment_after_it_is_still_its_section(tmp_path):
    """GetPrivateProfileString reads "[Graphics] ; display" as [Graphics]"""
    path = tmp_path / "NovaFix.ini"
    path.write_text("[Graphics] ; display\nDisplayMode=Windowed\n")
    cfg.write(path, {"Graphics": {"DisplayMode": "Borderless"}})
    assert path.read_text(encoding="utf-8") == "[Graphics] ; display\nDisplayMode=Borderless\n"


def test_a_bom_stays_and_doesnt_hide_the_first_section(tmp_path):
    path = tmp_path / "NovaFix.ini"
    path.write_bytes(b"\xef\xbb\xbf[Input]\r\nOverlayHotkey=F10\r\n")
    assert cfg.first_look(tmp_path, ".", menu_only(tmp_path / "undo"))
    assert path.read_bytes() == (b"\xef\xbb\xbf[Input]\r\nOverlayHotkey=F8\r\n"
                                 b"AlternateOverlayHotkey=Disabled\r\nOverlayGamepadHotkey=Disabled\r\n")


def test_the_drawer_shows_what_novafix_reads(tmp_path):
    """the section the old writer appended a second copy of, read first the way NovaFix reads"""
    (tmp_path / "NovaFix.ini").write_text("[performance]\nFrameRateLimit=60\n"
                                          "[Performance]\nFrameRateLimit=120\n")
    rows = {s["key"]: s["value"] for s in cfg.current(tmp_path, ".", "ffxiii2")["settings"]}
    assert rows["FrameRateLimit"] == "60"


def test_two_writers_at_once_dont_undo_each_other(tmp_path, monkeypatch):
    """
    the game switch's keys and a Settings save, each reading the file and
    writing it back: the one that read first used to put back what the other
    had just changed
    """
    import threading

    path = tmp_path / "NovaFix.ini"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT)
    real = Path.read_bytes
    read = threading.Event()

    def slow(self):
        data = real(self)
        if self == path and not read.is_set():
            read.set()
            # the other writer goes now
            threading.Event().wait(0.3)
        return data

    monkeypatch.setattr(Path, "read_bytes", slow)
    first = threading.Thread(target=cfg.write, args=(path, {"Input": {"OverlayHotkey": "F8"}}))
    first.start()
    read.wait(2)
    cfg.write(path, {"Performance": {"FrameRateLimit": "60"}})
    first.join(5)
    values = cfg.read(path)
    assert values["Input"]["OverlayHotkey"] == "F8"
    assert values["Performance"]["FrameRateLimit"] == "60"



def test_a_read_only_file_is_left_read_only(tmp_path):
    """somebody's lock on their settings: replacing it would have lifted the lock"""
    import stat

    path = tmp_path / "NovaFix.ini"
    path.write_text(INPUT_AS_NOVAFIX_WRITES_IT)
    path.chmod(stat.S_IREAD)
    try:
        with pytest.raises(OSError, match="read-only"):
            cfg.write(path, {"Performance": {"FrameRateLimit": "60"}})
        assert path.read_text(encoding="utf-8") == INPUT_AS_NOVAFIX_WRITES_IT
        assert not path.stat().st_mode & stat.S_IWRITE
    finally:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.parametrize("game", ["ffxiii", "ffxiii2", "ffxiii3"])
def test_every_tier_sets_rows_that_exist_to_values_they_take(game):
    """a tier is pending edits on its sibling rows, so one naming a row or a
    choice that isn't there would be refused at Apply with no way to see why"""
    settings = settings_with_saviors(game)
    rows = {(s.section, s.key): s for s in settings}
    for preset in (s for s in settings if s.kind == "preset"):
        assert [t for t, _ in preset.tiers] == ["Low", "Medium", "High"]
        for tier, picks in preset.tiers:
            for key, value in picks:
                row = rows[(preset.section, key)]
                assert row.file == preset.file, (tier, key)
                assert value in row.choices, (tier, key, value)


def test_a_preset_is_never_written_to_the_ini(tmp_path):
    result = cfg.apply_changes(tmp_path, ".", "ffxiii2", {"Quality": {
        "GraphicsPreset": "Low", "ShadowResolution": "1024"}})

    assert result["ok"], result
    text = (tmp_path / "NovaFix.ini").read_text(encoding="utf-8")
    assert "ShadowResolution=1024" in text
    assert "Preset" not in text


def test_a_preset_outside_its_choices_is_refused(tmp_path):
    result = cfg.apply_changes(tmp_path, ".", "ffxiii2", {"Quality": {"GraphicsPreset": "Ultra"}})

    assert not result["ok"]
    assert not (tmp_path / "NovaFix.ini").exists()


def test_auto_is_remembered_until_a_row_is_changed_by_hand(tmp_path):
    def shown():
        state = cfg.current(tmp_path, ".", "ffxiii2")
        return next(s for s in state["settings"] if s["kind"] == "preset")["value"]

    assert shown() is None
    cfg.apply_changes(tmp_path, ".", "ffxiii2", {"Quality": {"GraphicsPreset": "Auto"}})
    assert shown() == "Auto"
    # another game's pick is its own
    assert next(s for s in cfg.current(tmp_path, ".", "ffxiii")["settings"]
                if s["kind"] == "preset")["value"] is None
    cfg.apply_changes(tmp_path, ".", "ffxiii2", {"Performance": {"FrameRateLimit": "60"}})
    assert shown() == "Auto"
    cfg.apply_changes(tmp_path, ".", "ffxiii2", {"Quality": {"MSAA": "2"}})
    assert shown() is None


def test_auto_carries_the_tier_this_pc_gets(tmp_path, monkeypatch):
    import patcher.hardware as hardware

    monkeypatch.setattr(hardware, "auto_tier", lambda: "Low")
    preset = next(s for s in cfg.current(tmp_path, ".", "ffxiii2")["settings"]
                  if s["kind"] == "preset")

    assert preset["autoTier"] == "Low"
    assert preset["presets"]["Auto"] == preset["presets"]["Low"]
    assert preset["defaults"]


def settings_with_saviors(game):
    from patcher.novafix_config import SAVIORS_SETTINGS

    settings = cfg.settings_for(game)
    return settings + [s for s in SAVIORS_SETTINGS if s not in settings] if game == "ffxiii3" else settings


OLD_POPPED = "novafix-popup-every"


def test_a_game_the_old_popup_group_set_to_every_launch_comes_down_to_when_changed(tmp_path):
    """
    the group used to set EveryLaunch under its own marker. Its note of what
    the key was before is kept, so Revert lands on that, not on our EveryLaunch
    """
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(POPUP_AS_NOVAFIX_WRITES_IT)
    store.mkdir()
    (store / OLD_POPPED).write_text(
        '{"wrote": true, "was": {"LoadedModulesNotificationMode": "WhenChanged"}, "made": false}')
    assert cfg.first_look(tmp_path, ".", store)
    assert cfg.read(path)["Performance"]["LoadedModulesNotificationMode"] == "WhenChanged"
    assert not (store / OLD_POPPED).exists()
    # back to what it was before the old group, on a 1.0.1 that wrote WhenChanged
    cfg.write(path, {"Performance": {"LoadedModulesNotificationMode": "WhenChanged"}})
    cfg.put_back(tmp_path, ".", store)
    assert cfg.read(path)["Performance"]["LoadedModulesNotificationMode"] == "WhenChanged"
    assert not (store / cfg.POPPED).exists()


def test_revert_still_undoes_the_old_popup_group_nobody_looked_past(tmp_path):
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(POPUP_AS_NOVAFIX_WRITES_IT)
    store.mkdir()
    (store / OLD_POPPED).write_text(
        '{"wrote": true, "was": {"LoadedModulesNotificationMode": "WhenChanged"}, "made": false}')
    assert cfg.put_back(tmp_path, ".", store)
    assert cfg.read(path)["Performance"]["LoadedModulesNotificationMode"] == "WhenChanged"
    assert not (store / OLD_POPPED).exists()


def test_a_popup_the_old_group_left_alone_stays_theirs(tmp_path):
    path, store = tmp_path / "NovaFix.ini", tmp_path / "undo"
    path.write_text(POPUP_AS_NOVAFIX_WRITES_IT)
    store.mkdir()
    (store / OLD_POPPED).write_text('{"wrote": false, "was": {"LoadedModulesNotificationMode": "EveryLaunch"}}')
    cfg.first_look(tmp_path, ".", store)
    assert cfg.read(path)["Performance"]["LoadedModulesNotificationMode"] == "EveryLaunch"
    assert (store / cfg.POPPED).exists() and not (store / OLD_POPPED).exists()
