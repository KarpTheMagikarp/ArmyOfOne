"""
XIII's Save Anywhere: our own NovaFix add-on, an optional row that's off until
it's been played through, and an ini that's the player's the moment they touch it.
"""
import json
import subprocess
from pathlib import Path

import pytest

from patcher.engine import RUNTIME_LOGS, PatchStatus, run_patches
from patcher.manifest import load_manifest
from patcher.revert import revert_patch
from patcher.state import applied_recorder, record_reader

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets" / "mods" / "xiii-save-anywhere"
SOURCE = ROOT / "addon-src" / "xiii-save-anywhere" / "xiii_save_anywhere.cpp"


def xiii():
    return load_manifest(ROOT / "manifests" / "ffxiii.json")


def save(m):
    return next(p for p in m.patches if p.id == "install_save_anywhere")


def apply(root, m, state):
    results = run_patches(root, m, {"install_save_anywhere"},
                          already_applied_check=lambda pid: pid == "install_novafix",
                          on_applied=applied_recorder(root, m, state),
                          recorded=record_reader(state))
    return next(r for r in results if r.patch_id == "install_save_anywhere")


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "FINAL FANTASY XIII"
    exe = root / "white_data" / "prog" / "win" / "bin" / "ffxiiiimg.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    return root


def bin_dir(root):
    return root / "white_data" / "prog" / "win" / "bin"


def test_it_is_an_extra_called_save_anywhere_and_off_until_its_been_played():
    jobs = json.loads((ROOT / "ui" / "jobs.json").read_text(encoding="utf-8"))
    job = next(j for g in jobs["games"] if g["id"] == "ff13" for j in g["jobs"] if j["id"] == "saveanywhere")
    assert job["name"] == "Save Anywhere"
    assert job["kind"] == "optional" and job["default"] is False

    patch = save(xiii())
    assert patch.label == "Save Anywhere"
    assert not patch.default_enabled
    assert patch.ui_job_id == "saveanywhere"
    assert patch.requires == ["install_novafix"]


def test_the_ui_row_is_the_same_in_both_copies():
    """index.html carries its own copy of jobs.json, so the two drifting means two different apps"""
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    jobs = (ROOT / "ui" / "jobs.json").read_text(encoding="utf-8")
    for line in ('"id": "saveanywhere"', '"name": "Save Anywhere"', '"kind": "optional"'):
        assert line in html and line in jobs


def test_it_goes_into_whichever_addon_folder_novafix_is_reading(game, tmp_path):
    (bin_dir(game) / "mods").mkdir()
    result = apply(game, xiii(), tmp_path / "state.json")

    assert result.status == PatchStatus.APPLIED, result.detail
    home = bin_dir(game) / "mods" / "xiii-save-anywhere"
    assert (home / "xiii_save_anywhere.asi").is_file()
    assert (home / "xiii-save-anywhere.ini").is_file()
    assert (home / "LICENSE-MinHook.txt").is_file()
    assert not (bin_dir(game) / "addons").exists()


def test_the_players_settings_survive_another_apply_and_a_revert(game, tmp_path):
    m, state = xiii(), tmp_path / "state.json"
    assert apply(game, m, state).status == PatchStatus.APPLIED
    home = bin_dir(game) / "addons" / "xiii-save-anywhere"
    ini = home / "xiii-save-anywhere.ini"
    ini.write_text(ini.read_text(encoding="utf-8").replace("SaveButton=4", "SaveButton=2"))
    (home / "xiii-save-anywhere.log").write_text("ready\n")
    (home / "xiii-save-anywhere.log.1").write_text("older\n")
    (home / "pause-01.bin").write_bytes(b"\0" * 16)
    (home / "debug").mkdir()
    (home / "debug" / "pause-02-hops.bin").write_bytes(b"\0" * 16)

    assert apply(game, m, state).status == PatchStatus.APPLIED
    assert "SaveButton=2" in ini.read_text(encoding="utf-8")

    undone = revert_patch(game, m, save(m), state)

    assert undone.ok, undone.detail
    assert not (home / "xiii_save_anywhere.asi").exists()
    assert not (home / "xiii-save-anywhere.log").exists()
    assert not (home / "xiii-save-anywhere.log.1").exists()
    assert not (home / "pause-01.bin").exists()
    # the debug folder goes with its dumps
    assert not (home / "debug").exists()
    assert "SaveButton=2" in ini.read_text(encoding="utf-8")


def test_revert_takes_the_log_the_addon_writes():
    assert RUNTIME_LOGS["install_save_anywhere"] == ("xiii-save-anywhere.log", "xiii-save-anywhere.log.1",
                                                     "debug/pause-*.bin", "pause-*.bin")
    source = SOURCE.read_text(encoding="utf-8")
    assert '"\\\\pause-%02d.bin"' in source and 'const std::string dir = Folder("debug");' in source
    assert '"\\\\xiii-save-anywhere.log"' in SOURCE.read_text(encoding="utf-8")


def test_the_shipped_ini_says_what_the_addon_reads():
    text = (ASSETS / "xiii-save-anywhere.ini").read_text(encoding="utf-8")
    source = SOURCE.read_text(encoding="utf-8")
    for key, default in (("Enabled", "1"), ("SaveButton", "4"), ("Prompt", "1"), ("Debug", "0")):
        assert f"\n{key}={default}\n" in text
        assert f'L"{key}", {default},' in source
    assert "\nSaveKey=E\n" in text and 'L"SaveKey", L"E",' in source
    assert "[SaveAnywhere]" in text and 'L"SaveAnywhere"' in source


def test_it_only_ever_fires_the_games_own_save_trigger():
    """
    the whole safety story is that the add-on adds no game logic: the pause
    closes through its own Resume (a frame of Start), and the one thing fired
    at the zone state machine is SAVE_REQ, which it only takes in free roam.
    Anything else showing up here is a change to that story and deserves a look
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr int kTrgSaveReq = 41;" in source
    fired = [line.strip() for line in source.splitlines() if "g_zoneFire(Zone()" in line]
    assert fired == ["g_zoneFire(Zone(), nullptr, kTrgSaveReq);"]


def test_the_addon_is_32_bit_and_exports_the_novafix_query():
    data = (ASSETS / "xiii_save_anywhere.asi").read_bytes()
    pe = int.from_bytes(data[0x3C:0x40], "little")
    assert data[:2] == b"MZ" and data[pe:pe + 4] == b"PE\0\0"
    assert int.from_bytes(data[pe + 4:pe + 6], "little") == 0x14C

    try:
        dump = subprocess.run(["i686-w64-mingw32-objdump", "-p", str(ASSETS / "xiii_save_anywhere.asi")],
                              capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no mingw objdump to read the export table with")
    # NovaFix finds the plugin API by this exact, undecorated name
    assert "NovaFixPlugin_Query" in dump
    imports = {line.split(":", 1)[1].strip().lower() for line in dump.splitlines() if "DLL Name:" in line}
    assert imports <= {"kernel32.dll", "user32.dll", "msvcrt.dll"}


def test_it_stays_off_novafixs_input_tick():
    """
    NovaFix detours InputManager::Update (0x41FBE0) for its XIII dynamic
    prompts and refuses to install over a prologue it doesn't recognise, so a
    hook of ours there quietly switches its prompts off. The tick lives on the
    pad update below it instead
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr DWORD kPadUpdateRva = 0x4206C0;" in source
    code = [line.split("//")[0] for line in source.splitlines()]
    assert not any("0x41FBE0" in line.upper().replace("0X", "0x") for line in code)


def test_the_pause_labels_stay_off_novafixs_prompt_layout():
    """
    NovaFix checks Label::SetText's prologue (0x63B920) and detours the text
    rebuild (0x680090) for its prompts, so the labels change one step earlier,
    at the text id lookup, and neither address shows up in the code at all
    """
    source = SOURCE.read_text(encoding="utf-8")
    code = " ".join(line.split("//")[0] for line in source.splitlines()).upper()
    assert "0X63B920" not in code and "0X680090" not in code
    hooked = [line for line in source.splitlines() if "Hook(reinterpret_cast<LPVOID>(g_base + k" in line]
    assert any("kPauseModeRva" in line for line in hooked)
    assert any("kTextSetIdRva" in line for line in hooked)


def test_it_watches_the_games_save_slots_not_the_file_system():
    """
    with Steam Cloud on, XIII writes its slots through Steam's storage object
    and CreateFileA never sees a save, so the add-on hooks the game's own slot
    writer and reader, which every save and load goes through either way
    """
    source = SOURCE.read_text(encoding="utf-8")
    hooked = [line for line in source.splitlines() if "Hook(reinterpret_cast<LPVOID>(g_base + k" in line]
    assert any("kSaveWriteRva" in line for line in hooked)
    assert any("kSaveReadRva" in line for line in hooked)
    assert '"CreateFileA"' not in source


def test_where_you_stand_is_read_off_the_actor_not_queued_through_the_script_vm():
    """
    Chara::getPos (0x6C1F20) only queues script command 0xB8 to fill a script
    variable later, which is how v0.6 kept a spot of all zeros. The position
    comes off the actor's live world matrix at +0x78, under the registry lock
    setPosFlash takes. +0x150 is only the request block setters drop values into
    """
    source = SOURCE.read_text(encoding="utf-8")
    code = " ".join(line.split("//")[0] for line in source.splitlines()).upper()
    assert "0X2C1F20" not in code
    assert "constexpr DWORD kActorMatrix = 0x78, kActorTranslation = 0xA8;" in source


def test_a_load_lands_through_the_games_own_spawn():
    """
    a warp after the field starts loses to the game's own station placement,
    which runs later, and the spawn, the camera and the party placement each
    look the start marker up for themselves, so it's the marker lookup that
    answers with the save's matrix
    """
    source = SOURCE.read_text(encoding="utf-8")
    hooked = [line for line in source.splitlines() if "Hook(reinterpret_cast<LPVOID>(g_base + k" in line]
    assert any("kMarkerLookupRva" in line for line in hooked)
    assert "constexpr DWORD kMarkerLookupRva = 0x304A80;" in source


def test_the_patchers_list_of_hook_sites_is_the_addons():
    """the patcher keeps code mods off these, so one missing here is one it lets through"""
    import re

    from patcher.save_anywhere import SITES

    source = SOURCE.read_text(encoding="utf-8")
    values = {}
    for decl in re.findall(r"constexpr DWORD ([^;]+);", source):
        for name, value in re.findall(r"(k\w+) = (0x[0-9A-Fa-f]+)\b", decl):
            values[name] = int(value, 16)
    heads = {name: len(body.split(",")) for name, body in
             re.findall(r"constexpr unsigned char (k\w+)\[\] = \{([^}]*)\};", source)}
    table = re.search(r"const Site sites\[\] = \{(.*?)\};", source, re.S).group(1)
    theirs = [(values[rva], heads[head]) for rva, head in
              re.findall(r"\{(k\w+), (k\w+), sizeof k\w+\}", table)]
    assert theirs and [(rva, size) for _, rva, size in SITES] == theirs


def code_mod(root, name, *pairs):
    folder = bin_dir(root) / "addons" / "ff13-code-patches"
    folder.mkdir(parents=True, exist_ok=True)
    body = "".join(f'\t"{n}"\t"{v}"\n' for n, v in enumerate(pairs, 1))
    (folder / f"{name}.nccp").write_text(f'"{name}"\n{{\n{body}}}\n')


def test_a_code_mod_on_its_hooks_keeps_it_out_and_says_whose(game, tmp_path):
    # one byte into the pad update hook
    code_mod(game, "Turbo", "4206C4|90")
    result = apply(game, xiii(), tmp_path / "state.json")

    assert result.status == PatchStatus.BLOCKED
    # not one the patcher put in, so it goes by its file name
    assert "Turbo.nccp's code patch at 4206C4" in result.detail
    assert "Remove Turbo.nccp first" in result.detail
    assert not (bin_dir(game) / "addons" / "xiii-save-anywhere" / "xiii_save_anywhere.asi").exists()


def test_a_code_mod_anywhere_else_is_no_bother(game, tmp_path):
    # right up against the pad update hook, and right after the zone pointer
    code_mod(game, "Neighbour", "4206BC|90909090", "7EB3A6|EB")
    assert apply(game, xiii(), tmp_path / "state.json").status == PatchStatus.APPLIED


def test_the_overlap_check_is_any_write_not_just_a_different_one():
    from patcher.mods.nccp import Patch
    from patcher.save_anywhere import clash

    assert clash([Patch(0x7EB3A5, b"\x8b")])[1] == "zone pointer"
    assert clash([Patch(0x7EB3A6, b"\x90"), Patch(0x4206B0, b"\x90" * 0x10)]) is None


def test_the_long_traces_only_go_in_the_log_with_debug_on():
    source = SOURCE.read_text(encoding="utf-8")
    for line in ('"pause screen: button', '"  marker %d asked from', '"  text id', '"pause labels:',
                 '"  relabelled %d', ' slot +%#x'):
        uses = [l.strip() for l in source.splitlines() if line in l]
        assert uses and all(l.startswith("Trace(") for l in uses), (line, uses)
    assert "const bool logging = g_debug && g_pauseLogLines < 8;" in source
    assert "if (g_debug && g_lastState == kStateFieldPause" in source


def test_a_load_of_ours_lands_through_the_spawn_alone_and_still_checks_for_a_fall():
    """
    the spawn sets the leader a step behind the start marker, so ours answers
    a step ahead of the spot, upright. A move after the spawn got pulled back
    toward where the spawn had put you, so there isn't one any more
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr float kSpawnStep = 4.0f;" in source
    assert "g_spawn.matrix = SpawnMatrix(g_spawn.matrix, out0);" in source
    body = source[source.index("void TickLoad(int state) {"):]
    body = body[:body.index("\n}\n")]
    # the spot's never moved onto after the spawn: only a fallback moves you
    assert "NextFallback(" in body


def test_it_only_saves_a_story_state_a_station_could_have():
    """
    a save mid-sequence (after a beat, before the station that follows it)
    reloaded into a fight that never led anywhere. So Save stays off the bar
    once the story's moved past where the last load or station save had it
    """
    source = SOURCE.read_text(encoding="utf-8")
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert "Moved(now)" in withheld and "Steady(rise)" in withheld
    # a scene since the last station is what holds it back first
    assert withheld.index("g_sceneSince >= 0") < withheld.index("Moved(now)")
    moved = source[source.index("const char* Moved(const Progress& now) {"):]
    moved = moved[:moved.index("\n}\n")]
    for check in ("now.story != g_sanctioned.story", "now.ui != g_sanctioned.ui", "now.menu, g_sanctioned.menu"):
        assert check in moved, check
    # a chest or a jump up a ledge flips event flags, so those are logged, not gated
    for logged in ("now.flags", "now.work", "now.zone"):
        assert logged not in moved, logged
    # the bar and the press go by the same answer
    assert "&& !Withheld();" in source
    assert "if (const char* why = Withheld()) Log(\"save pressed, but %s\", why);" in source
    # station saves sanction: any slot write that isn't ours, since a terminal
    # saves from its own menu and never passes through the save screen's state
    hook = source[source.index("int __fastcall SaveWriteHook("):]
    hook = hook[:hook.index("g_sanction = kStation;")]
    assert "!g_ourSave.load() && slot >= 0 && SlotOf(reinterpret_cast<const char*>(self + 0x172)) == slot" in hook


def test_a_floor_thats_gone_on_load_is_caught_whatever_moved_it():
    """
    lifts, sideways platforms, a save in mid air: if the floor isn't where it
    was once the field's rebuilt you drop, and a straight drop of more than a
    step sends you to the station. Stairs cover more ground than height, so
    walking off down them isn't a drop
    """
    source = SOURCE.read_text(encoding="utf-8")
    body = source[source.index("void TickLoad(int state) {"):]
    body = body[:body.index("\n}\n")]
    assert "if (flat < 3.0f && drop > 1.0f && flat < drop) {" in body
    assert "NextFallback(handle, now, why);" in body
    assert "g_rescues < 3" in body


def test_a_station_is_what_makes_a_state_safe_to_save_in():
    """
    the game makes a station the current save point the moment you walk into
    its area, so that's a state it would save itself. So is a station save, a
    load that's seen starting you at its station, and a save of ours made under
    this guard. A slot read on its own isn't: the load screen reads them all,
    and the saves don't carry the station in the clear (seen in game)
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr DWORD kAreaEnterRva = 0x1BD1C0;" in source
    hooked = [line for line in source.splitlines() if "Hook(reinterpret_cast<LPVOID>(g_base + k" in line]
    assert any("kAreaEnterRva" in line for line in hooked)
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    assert 'Sanctify("a station")' in tick and "state == kStateFieldIdle" in tick
    assert 'Sanctify("the station this load started at")' in tick
    assert "if (format >= 4) g_sanction = kOurs;" in source
    assert '"xiii-save-anywhere 6\\nhash' in source
    assert "g_readBytes" not in source
    # any read wipes the old state until the new field's settled
    assert "g_sanctioned.story = -1;" in tick


def test_the_guard_is_asked_again_as_the_save_fires_and_after_it():
    source = SOURCE.read_text(encoding="utf-8")
    fire = source.index("g_zoneFire(Zone(), nullptr, kTrgSaveReq);")
    assert "if (const char* why = Withheld()) {" in source[fire - 400:fire]
    finish = source[source.index("void FinishSave() {"):]
    finish = finish[:finish.index("\n}\n")]
    assert finish.index("Moved(after)") < finish.index("WriteWhere(written);")


def test_it_never_writes_to_a_save():
    """the slot hooks only read what passes through, then hand it on untouched"""
    source = SOURCE.read_text(encoding="utf-8")
    for hook in ("int __fastcall SaveWriteHook(", "int __fastcall SaveReadHook("):
        body = source[source.index(hook):]
        body = body[:body.index("\n}\n")]
        assert "memcpy(" not in body and "memset(" not in body
    assert "return g_saveWrite(self, edx, a, b, data, size);" in source


def test_it_only_acts_in_free_roam_and_its_pause_never_in_a_fight():
    """
    the numbers are the zone state table's (0x26A1728, states from entry 60):
    3 FIELD_IDLE, 23 FIELD_PAUSE, 27 FIELD_PAUSE_WAIT, 60 SAVE. A battle pauses
    into 24 and an event or live event into states of their own, so none of
    them shows Save or takes the press
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr int kStateFieldIdle = 3, kStateFieldPause = 23, kStateFieldPauseWait = 27, kStateSave = 60;" \
        in source
    fire = source.index("g_zoneFire(Zone(), nullptr, kTrgSaveReq);")
    assert "if (state == kStateFieldIdle) {" in source[fire - 600:fire]
    assert "if (state == kStateFieldPause && g_phase == Phase::Idle && g_enabled && (g_decorated || !g_prompt)" \
        in source


def test_a_scene_is_any_event_state_and_a_plain_fight_isnt():
    """
    the zone state table (0x26A1728): every name with EVENT in it is a scene,
    a cutscene, a scene you walk through, a cutscene inside a battle, or one of
    their pauses and skips. 5-8 and 24 are an ordinary fight and its pause
    """
    import re as _re

    source = SOURCE.read_text(encoding="utf-8")
    body = source[source.index("bool InScene(int state) {"):]
    body = body[:body.index("\n}\n")]
    scene = set()
    for lo, hi in _re.findall(r"state >= (\d+) && state <= (\d+)", body):
        scene |= set(range(int(lo), int(hi) + 1))
    scene |= {int(n) for n in _re.findall(r"state == (\d+)", body)}
    events = {13, 14, 15, 16, 17, 18, 21, 22, 25, 26, 29, 30, 33, 34, 35, 36, 37, 42, 43, 44, 45, 47, 49,
              51, 52, 53, 54, 55, 56, 57, 58, 59, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73}
    assert scene == events
    assert not scene & {3, 5, 6, 7, 8, 23, 24, 27, 28, 60}


def test_platforms_any_way_they_move_and_where_a_missing_floor_sends_you():
    """
    up and down is the lift guard. Sideways or spinning is being carried
    where she isn't facing, step by step, with the facing axis learnt from her
    own walking rather than assumed. And if the floor's gone on load, the
    ground she walked on just before comes first, the station last
    """
    source = SOURCE.read_text(encoding="utf-8")
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert "Carried()" in withheld and "Steady(rise)" in withheld
    carried = source[source.index("bool Carried() {"):]
    carried = carried[:carried.index("\n}\n")]
    assert "FacingAxis()" in carried and "if (axis < 0) return false;" in carried
    fallback = source[source.index("void NextFallback("):]
    fallback = fallback[:fallback.index("\n}\n")]
    assert "g_fallbackAt == g_fallbackCount" in fallback and "g_stationPos" in fallback
    assert 'fprintf(file, "\\ntrail %d", g_savedTrailCount);' in source
    # a station starts a fresh trail, and a new field throws the old one away
    assert "g_trailCount = 0;" in source[source.index("void Sanctify("):]
    assert "if (state == 1 || state == 2) {\n        g_trailCount = 0;" in source


def test_save_waits_for_the_levels_own_floor():
    """
    the game's own footing flag (bit 22 at [actor+0x10]+0x380, read off two
    labelled test runs) is off on lifts, platforms and in the air, so Save
    needs it on, and the trail of fallback spots only keeps floor she stood on
    while it was. Anything unreadable counts as off.

    The pause screen clears that bit a frame or two in, so Withheld goes by the
    last free roam sample, not a live read. A live read there turned every
    press into a refusal (chapter 2, Nov 14)
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr DWORD kActorFooting = 0x10, kFootingFlags = 0x380, kFootingGround = 0x398;" in source
    assert "constexpr DWORD kOnLevelFloor = 1u << 22;" in source
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert "if (!g_idleSolid)" in withheld and "ReadFooting" not in withheld
    footing = source[source.index("bool ReadFooting(DWORD handle, Footing& out) {"):]
    footing = footing[:footing.index("\n}\n")]
    assert "if (got < sizeof block) return false;" in footing
    assert "if (now - g_idleSince >= 100) g_idleSolid = ReadFooting(PlayerHandle(), footing) && footing.solid;" in source
    assert "!Carried() && g_idleSolid)\n                Remember(next.pos);" in source
    # a new field starts her off the floor until she's sampled standing on it
    assert "g_trailCount = 0;\n        g_idleSolid = false;" in source


def test_spots_and_dumps_keep_to_their_own_folders():
    """
    a hundred slots is a hundred spot files, so they live in save-locations,
    debug dumps in debug, and a station save over a slot takes its stale spot
    with it rather than leaving it to pile up
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert 'return Folder("save-locations") + name;' in source
    assert "Tidy();" in source and '{"ff13-*.where", "pause-*.bin"}' in source
    writer = source[source.index("int __fastcall SaveWriteHook("):]
    writer = writer[:writer.index("\n}\n")]
    assert "DeleteFileA(WherePath(slot).c_str())" in writer
    # never one of ours: that slot gets a fresh spot instead
    assert writer.index("!g_ourSave.load()") < writer.index("DeleteFileA")


def test_the_log_rotates_and_the_spots_folder_explains_itself():
    source = SOURCE.read_text(encoding="utf-8")
    worker = source[source.index("DWORD WINAPI Worker(LPVOID) {"):]
    # rotated before the session's first line, so that line starts the fresh file
    assert worker.index("RotateLog();") < worker.index("Log(")
    assert "1024 * 1024" in source and 'L".1"' in source
    assert "ExplainLocations();" in source[source.index("void WriteWhere("):]
    assert "Safe to delete" in source


def test_restart_points_count_like_a_station():
    """
    White.doRestartSave is where the scripts write the snapshot game over's
    Retry rebuilds the field from, so the designers already vouch for it the
    way they do for a station. Chapter 3 has long stretches with a scene behind
    you and no station ahead (Nov 14), so these let Save back on. Only if
    nothing that persists moved between the point and free roam, and only in
    the field it was set in
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr DWORD kRestartSaveRva = 0x7E63D0;" in source
    assert "{kRestartSaveRva, kRestartSaveHead, sizeof kRestartSaveHead}," in source
    assert "reinterpret_cast<LPVOID>(&RestartSaveHook)" in source
    hook = source[source.index("int __cdecl RestartSaveHook(void* vm) {"):]
    hook = hook[:hook.index("\n}\n")]
    # the original runs first, so the snapshot is written before we look
    assert hook.index("g_restartSave(vm)") < hook.index("ReadProgress(g_restartAt)")
    drifted = source[source.index("const char* Drifted("):]
    drifted = drifted[:drifted.index("\n}\n")]
    for field in ("story", "flags", "work", "zone"):
        assert f"now.{field}" in drifted
    # mid-sequence locks are on when a script sets the point, so they can't count
    assert "now.ui" not in drifted and "now.menu" not in drifted
    taken = source[source.index("if (state == kStateFieldIdle && g_restartPoint.exchange(false)) {"):]
    taken = taken[:taken.index("\n    }\n")]
    assert "Drifted(now, g_restartAt)" in taken and 'Sanctify("a restart point");' in taken
    assert "g_idleSolid = false;\n        // a field change after the point means it's not this field's any more\n" \
           "        g_restartPoint = false;" in source


def test_what_the_scripts_did_to_the_field_goes_with_the_save():
    """
    a save keeps the party, not the field: which areas are on, which boxes are
    live, where a door's animation stopped. The station's load script rebuilds
    those from its own side of the story, and the zone scripts show most of
    what an event turns on afterwards is never in one (Nov 15). So the engine
    end of every one of those calls is journalled, the journal goes in the
    .where, and a load of ours puts back whatever the rebuild left different
    """
    source = SOURCE.read_text(encoding="utf-8")
    for site in ("kAreaOnRva", "kAreaFadeRva", "kAreaOffRva", "kObjectRva", "kBgAnimRva", "kLayoutRva",
                 "kEncountRva"):
        assert f"{{{site}, " in source
    assert "WriteWorld(file, g_savedWorld, g_savedZone, g_savedMapSet);" in source
    check = source[source.index("void CheckWorld() {"):]
    check = check[:check.index("\n}\n")]
    assert "Missing(g_pending, have, &other)" in check and "Replay(e)" in check
    replay = source[source.index("void Replay(const Entry& e) {"):]
    replay = replay[:replay.index("\n}\n")]
    # back through the hooked entries, so the journal sees the replay too
    assert "g_base + kAreaOnRva" in replay and "g_areaOn(" not in replay
    # a door that was opening comes back open, not opening again
    assert "int ReplayMode(int mode) { return mode == 1 ? 5 : mode; }" in source


def test_the_journal_only_ever_holds_the_field_youre_in():
    """a load, a retry and a new zone each start a field from nothing, and a journal from before would put back a world that's gone"""
    source = SOURCE.read_text(encoding="utf-8")
    read = source[source.index("int __fastcall SaveReadHook("):]
    read = read[:read.index("\n}\n")]
    assert 'ForgetWorld("a load");' in read
    retry = source[source.index("int __fastcall RetryHook("):]
    assert retry.index('ForgetWorld("retry");') < retry.index("g_retry(self, edx)")
    note = source[source.index("void Note(Op op,"):]
    note = note[:note.index("\n}\n")]
    assert 'if (zone != g_worldZone) ForgetWorld("a new zone");' in note


def test_a_load_waits_for_the_real_rebuild_before_putting_anything_back():
    """
    the first free roam after a load is the stand-in at 0 1.5 -10, before the
    station's load script has run. A replay there hung the load for good (Nov
    15), so it waits for the player, quiet calls, and the same map set
    """
    source = SOURCE.read_text(encoding="utf-8")
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    wait = tick[tick.index("if (g_worldCheckWanted"):]
    wait = wait[:wait.index("CheckWorld();")]
    assert "!StandIn(at)" in wait and "g_lastNoteAt" in wait
    check = source[source.index("void CheckWorld() {"):]
    assert check.index("g_pendingMapSet != MapSetName()") < check.index("Replay(e)")


def test_only_what_a_script_asked_for_goes_in_the_journal():
    """
    the engine streams enemy groups in and out by itself and keeps its own
    tally. Replaying one of its offs left a save station invisible until the
    next load (Nov 15), so the hooks keep a call only when a native made it,
    and a 5's journal, made before that, is never replayed
    """
    source = SOURCE.read_text(encoding="utf-8")
    for hook in ("AreaOnHook", "AreaFadeHook", "AreaOffHook", "ObjectHook", "EncountHook"):
        body = source[source.index(f"int __fastcall {hook}(") if "Encount" not in hook else source.index(f"int __cdecl {hook}("):]
        body = body[:body.index("\n}\n")]
        assert "FromScript(__builtin_return_address(0))" in body
    assert "version >= 6 && !ReadWorld(" in source


def test_a_spot_saved_in_a_streamed_in_map_set_loads_into_that_set():
    """0304a is 0304 with the far end streamed in, and a station's load always asks for its own, so a spot out in the other one loaded into sky (Nov 15)"""
    source = SOURCE.read_text(encoding="utf-8")
    hook = source[source.index("int __fastcall MapSetHook("):]
    hook = hook[:hook.index("\n}\n")]
    assert "Family(asked) == Family(g_pendingMapSet)" in hook and "g_mapSet(zone, edx, want)" in hook
