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
    toward where the spawn had put you, so there isn't one any more. Nothing
    else moves her either: her body wrote back every move tried after the
    spawn (Ch 9, Nov 23)
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr float kSpawnStep = 4.0f;" in source
    # the spawn lifts her 1.45 off the marker, so ours answers that much under
    # the floor she stood on, less 5 cm, the way a station's marker sits
    assert "constexpr float kSpawnRise = 1.40f;" in source
    assert "out.m[13] = saved.m[13] - kSpawnRise;" in source
    assert "g_spawn.matrix = SpawnMatrix(g_spawn.matrix, out0);" in source
    assert "SetPos" not in source and "0x2C3450" not in source
    body = source[source.index("void TickLoad(int state) {"):]
    body = body[:body.index("\n}\n")]
    assert "LoseSpot(" in body


def test_after_a_story_beat_it_saves_only_what_a_load_can_put_back():
    """
    a save mid-sequence (after a beat, before the station that follows it)
    reloaded into a fight that never led anywhere, so for a while Save waited
    for the next station after any beat. With the world journal a load puts
    back what the station's rebuild misses (every difference a terminal's own
    reload left in Vile Peaks was one of those, Nov 15), so after a beat it's
    only held back by what can't be put back yet: a chara made or let go, a
    talk change, a path edge, or a zone flag
    """
    source = SOURCE.read_text(encoding="utf-8")
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    # a sequence still running holds it back whatever came before
    assert withheld.index("now.ui != g_sanctioned.ui") < withheld.index("g_sceneSince >= 0")
    assert "now.menu, g_sanctioned.menu" in withheld
    beat = withheld[withheld.index("if (g_sceneSince >= 0 || now.story != g_sanctioned.story) {"):]
    assert "CharasMoved()" in beat and "g_lockedNotes.load() != g_lockedAtSanction" in beat
    assert "now.zone, g_sanctioned.zone" in beat
    assert "Steady(rise)" in withheld and "Carried()" in withheld
    note = source[source.index("void Note(Op op,"):]
    note = note[:note.index("\n}\n")]
    assert "if (!Replayable(op) && op != Op::Chara) ++g_lockedNotes;" in note
    # a chara made and let go inside a scene nets out, by name
    native = source[source.index("void NoteNative(Op op, void* vm, bool on) {"):]
    native = native[:native.index("\n}\n")]
    assert "const char* target = op == Op::Chara && on ? texts[1] : texts[0];" in native
    # the bar and the press go by the same answer
    assert "&& !Withheld();" in source
    assert "if (const char* why = Withheld()) Log(\"save pressed, but %s\", why);" in source
    # station saves sanction: any slot write that isn't ours, since a terminal
    # saves from its own menu and never passes through the save screen's state
    hook = source[source.index("int __fastcall SaveWriteHook("):]
    hook = hook[:hook.index("g_sanction = kStation;")]
    assert "!g_ourSave.load() && slot >= 0 && slot < kSlots && data && size >= kSaveMinBytes" in hook


def test_a_floor_thats_gone_on_load_is_caught_whatever_moved_it():
    """
    lifts, sideways platforms, a save in mid air: if the floor isn't where it
    was once the field's rebuilt she drops straight through, and the spot's
    marked so the next load starts somewhere she walked. Stairs and a hop off
    a step cover more ground than height, so they aren't a drop (0.25.13 read
    one as a fall)
    """
    source = SOURCE.read_text(encoding="utf-8")
    body = source[source.index("void TickLoad(int state) {"):]
    body = body[:body.index("\n}\n")]
    drop = body.index("if (flat < 3.0f && drop > 1.5f && flat < 0.5f * drop) {")
    after = body[drop:drop + 500]
    assert "LoseSpot(g_loadedSlot, g_landTarget);" in after and "g_lostLanding = true;" in after
    assert body.index("if (g_worldCheckWanted) return;") < drop


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
    assert finish.index("Drifted(after, g_atSave)") < finish.index("WriteWhere(written);")


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
    3 FIELD_IDLE, 23 FIELD_PAUSE, 27 FIELD_PAUSE_WAIT, 31 FIELD_PAUSE_END_WAIT,
    60 SAVE. A battle pauses into 24 and an event or live event into states of
    their own, so none of them shows Save or takes the press
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr int kStateFieldIdle = 3, kStateFieldPause = 23, kStateFieldPauseWait = 27, kStateFieldPauseEnd = 31,\n" \
           "              kStateSave = 60;" in source
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
    own walking rather than assumed. And if the floor was gone on load, the
    next load of that save starts on the ground she walked on before, off
    that stretch, and the station only when there's none
    """
    source = SOURCE.read_text(encoding="utf-8")
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert "Carried()" in withheld and "Steady(rise)" in withheld
    carried = source[source.index("bool Carried() {"):]
    carried = carried[:carried.index("\n}\n")]
    assert "FacingAxis()" in carried and "if (axis < 0) return false;" in carried
    avoid = source[source.index("bool AvoidLostSpots(int slot, Matrix& saved) {"):]
    avoid = avoid[:avoid.index("\n}\n")]
    assert "for (int i = 0; i < g_fallbackCount; ++i) {" in avoid and "return false;" in avoid
    arm = source[source.index("void TryArm(int marker) {"):]
    arm = arm[:arm.index("\n}\n")]
    assert "} else if (!AvoidLostSpots(load.slot, saved)) {" in arm
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
    assert "if (!g_idleSolid && !(g_nearFloor && g_trailCount > 0))" in withheld and "ReadFooting" not in withheld
    footing = source[source.index("bool ReadFooting(DWORD handle, Footing& out) {"):]
    footing = footing[:footing.index("\n}\n")]
    assert "if (got < sizeof block) return false;" in footing
    assert "if (now - g_idleSince >= 100) {\n                const bool read = ReadFooting(PlayerHandle(), footing);\n" \
           "                g_idleSolid = read && footing.solid;" in source
    assert "if (Steady(rise) && !carried && g_idleSolid && offRide) {" in source
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
    assert "Keep them with your saves." in source


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
    assert "Drifted(now, g_restartAt)" in taken and 'Sanctify("a restart point", false);' in taken
    assert "g_runMs = 0;\n        // a field change after the point means it's not this field's any more\n" \
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
    assert "Missing(g_pending, have, &other, boxes, g_realFrom)" in check and "Replay(e)" in check
    replay = source[source.index("bool Replay(const Entry& e) {"):]
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
    15), so it waits for the player and quiet calls, and only replays a
    journal from this zone
    """
    source = SOURCE.read_text(encoding="utf-8")
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    wait = tick[tick.index("if (g_worldCheckWanted"):]
    wait = wait[:wait.index("CheckWorld()")]
    assert "!StandIn(at)" in wait and "g_lastNoteAt" in wait
    check = source[source.index("void CheckWorld() {"):]
    assert check.index("g_pendingZone != g_worldZone") < check.index("Replay(e)")


def test_only_what_a_script_asked_for_goes_in_the_journal():
    """
    the engine toggles areas around fights and refreshes enemy groups by itself
    and keeps its own tally. Replaying one of its offs left a save station
    invisible until the next load (Nov 15), so the hooks keep a call only when
    a native made it, or for areas an area box, and a 5's journal, made before
    that, is never replayed
    """
    source = SOURCE.read_text(encoding="utf-8")
    for hook in ("AreaFadeHook", "ObjectHook", "EncountHook"):
        body = source[source.index(f"int __fastcall {hook}(") if "Encount" not in hook else source.index(f"int __cdecl {hook}("):]
        body = body[:body.index("\n}\n")]
        assert "FromScript(__builtin_return_address(0))" in body
    for hook in ("AreaOnHook", "AreaOffHook"):
        body = source[source.index(f"int __fastcall {hook}("):]
        body = body[:body.index("\n}\n")]
        assert "const bool kept = (FromScript(from) || box) && Named(name);" in body
    assert "version >= 6 && !ReadWorld(" in source


def test_a_spot_loads_into_the_map_set_it_was_saved_in():
    """a station stays the save point past where its own map set ends, and its load always asks for its own, so a spot out in another one loaded into sky (Nov 15)"""
    source = SOURCE.read_text(encoding="utf-8")
    hook = source[source.index("int __fastcall MapSetHook("):]
    hook = hook[:hook.index("\n}\n")]
    assert "g_pendingZone == ZoneNumber()" in hook and "g_mapSet(zone, edx, want)" in hook


def test_a_scene_that_changed_nothing_doesnt_hold_save_back():
    """
    a terminal's own menu is an event state, so just using one turned Save
    off until you walked back into its area (Nov 15). Only a scene that left
    the story, flags, script variables, zone flags and the field untouched
    """
    source = SOURCE.read_text(encoding="utf-8")
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    assert "g_notes.load() == g_notesAtScene && !Moved(after) && !Drifted(after, g_sceneStart)" in tick


def test_plain_ground_after_a_hop_still_counts_as_floor():
    """
    after one of the game's hops the floor bit stayed off on plain ground
    until she was well away (Vile Peaks, Nov 15), so level with and near the
    last floor spot counts for the save. The fallback trail stays strict
    """
    source = SOURCE.read_text(encoding="utf-8")
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert "if (!g_idleSolid && !(g_nearFloor && g_trailCount > 0))" in withheld
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    assert "if (Steady(rise) && !carried && g_idleSolid && offRide) {" in tick


def test_a_ride_holds_save_back_till_shes_walked_off_it():
    """
    Ch 9 on an older build: a save on a lift going up after a scene, no lift on
    load, a fall to the fight floor below, and that fight no longer starts the
    scene. Steady only sees half a second and the game may call a stopped lift
    floor, so a ride latches until she's stood on floor a few metres off it,
    and after a scene there has to be walked floor for a missing one to land on
    """
    source = SOURCE.read_text(encoding="utf-8")
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    # sustained and steeper than 45, so stairs and a hop never latch
    assert "std::hypot(dx, dz) < 0.5f * std::fabs(stepDy)" in tick
    # longer than any jump or climb, and being carried isn't one: judged every
    # step it caught plain running round corners, and at 1.2 s and 1 m a hop
    # up onto a junk pile counted (Vile Peaks, Nov 16)
    assert "if (g_runMs >= 2000 && std::fabs(g_runDy) >= 2.0f) {" in tick and "|| carried" not in tick
    # a load settling her onto the spot isn't a ride
    assert "if (g_landCheckAt) {\n                g_runDy = 0;" in tick
    # pauses, fights, scenes and teleports break a run but never clear the latch
    assert "dt <= 1000" in tick and "< 2.0f;" in tick
    # nothing on the ride goes on the trail
    assert tick.index("offRide") < tick.index("Remember(next.pos)")
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert "if (g_rode)\n        return g_rodeByScene ?" in withheld
    assert "if (g_trailCount == 0 && (g_sceneSince >= 0 || now.story != g_sanctioned.story))" in withheld
    sanctify = source[source.index("void Sanctify("):]
    sanctify = sanctify[:sanctify.index("\n}\n")]
    assert "g_rode = false;" in sanctify


def test_a_load_of_ours_counts_once_the_field_is_put_back():
    """
    sanctioned off the stand-in's idle, the station's own load scene came
    after and held Save off until the next station after every load of ours
    (Vile Peaks, Nov 15)
    """
    source = SOURCE.read_text(encoding="utf-8")
    ours = source[source.index("} else if (g_sanction == kOurs) {"):]
    ours = ours[:ours.index("} else {")]
    assert ours.index("if (!g_worldCheckWanted) {") < ours.index('Sanctify("a save anywhere load");')


def test_a_save_of_ours_gets_its_field_back_even_when_its_spot_cant_be_used():
    """
    a save made after a story beat needs the field put back whether or not it
    starts at the spot, so the journal is taken whenever the save is ours, and
    the replay goes by the zone, not the map set: a step across a swap box
    before the replay ran used to skip it whole
    """
    source = SOURCE.read_text(encoding="utf-8")
    arm = source[source.index("void TryArm(int marker) {"):]
    arm = arm[:arm.index("\n}\n")]
    assert arm.index("if (ours && load.hash == stored && format >= 6) {") < arm.index("if (!ours) {")
    check = source[source.index("void CheckWorld() {"):]
    check = check[:check.index("\n}\n")]
    assert "if (g_pendingZone != g_worldZone) {" in check and "MapSetName()" not in check
    # only the area boxes' groups go by the map set, the one she came up on
    assert "g_realSet == g_pendingMapSet" in check
    # and only the rebuild's own map set ask is ever swapped
    hook = source[source.index("int __fastcall MapSetHook("):]
    hook = hook[:hook.index("\n}\n")]
    assert "g_mapSetSwapLeft = false;" in hook


def test_the_spots_folder_says_to_keep_the_files():
    """since 0.25 a .where carries the field too, so the note that called them safe to delete had to go"""
    source = SOURCE.read_text(encoding="utf-8")
    explain = source[source.index("void ExplainLocations() {"):]
    explain = explain[:explain.index("\n}\n")]
    assert "Safe to delete" not in explain and "Keep them with your saves." in explain
    assert "GetFileAttributesA" not in explain


def test_charas_and_talk_reset_with_the_field_but_not_at_a_restart_point():
    source = SOURCE.read_text(encoding="utf-8")
    forget = source[source.index("void ForgetWorld(const char* why) {"):]
    forget = forget[:forget.index("\n}\n")]
    assert "g_charaNet.clear();" in forget and "g_lockedAtSanction = g_lockedNotes.load();" in forget
    sanctify = source[source.index("void Sanctify(const char* where, bool baseline = true) {"):]
    sanctify = sanctify[:sanctify.index("\n}\n")]
    assert sanctify.index("if (baseline) {") < sanctify.index("g_charaNet.clear();")


def test_the_area_boxes_groups_come_back_with_the_spot():
    """
    the map data's area boxes (0x5C88D0, four groups, all on or all off) turn
    on the next stretch of level's gr_fa group as you walk into it, and its
    save station's terminal lives in that group. A load past the box starts at
    a station before it, so with those calls left out the terminal ahead stood
    invisible, solid and dead (Vile Peaks, Nov 16). They go in the journal
    marked as a box's, and come back only at the spot, on the map set it was
    saved on, and not over a box she's walked through since
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr DWORD kAreaBoxFrom = 0x1C88D0, kAreaBoxTo = 0x1C9AA5;" in source
    on = source[source.index("int __fastcall AreaOnHook("):]
    on = on[:on.index("\n}\n")]
    assert "Note(Op::AreaOn, name, nullptr, flags, box);" in on
    assert "bool FromBox(const Entry& e) { return Kind(e.op) == 0 && e.j == 1; }" in source
    # who toggled it isn't the effect
    assert "(Kind(x.op) == 0 || x.j == y.j)" in source
    missing = source[source.index("std::vector<Entry> Missing("):]
    missing = missing[:missing.index("\n}\n")]
    assert "(FromBox(w) && !boxes)" in missing
    assert "FromBox(w) && it != have.end() && FromBox(*it) && it->seq > walked" in missing
    check = source[source.index("void CheckWorld() {"):]
    check = check[:check.index("\n}\n")]
    assert "g_spotUsed && g_realSet == g_pendingMapSet && read &&" in check and "< kBoxReach" in check
    # the stand-in's boxes aren't hers
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    assert "if (Position(PlayerHandle(), at) && !StandIn(at)) {\n            g_realFrom = g_notes.load();" in tick


def test_a_pause_holds_the_load_check_rather_than_restarting_it():
    """
    the field check after a load wanted a second of unbroken free roam, so
    pausing to check Save straight after loading kept putting it off, and Save
    stayed off for eight pauses in a row (Vile Peaks, Nov 16). Free roam since
    the load now adds up across pauses, and the reason says what it's waiting on
    """
    source = SOURCE.read_text(encoding="utf-8")
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    assert "if (state != kStateFieldPause && state != kStateFieldPauseWait && state != kStateFieldPauseEnd) g_roamMs = 0;" in tick
    assert "if (g_worldCheckWanted && state == kStateFieldIdle && g_roamMs > 1000 &&" in tick
    # and so does the sanction after it, which kept starting its 2 s over
    assert "if (g_sanction != kNone && state == kStateFieldIdle && g_roamMs > 2000) {" in tick
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert withheld.index("if (g_worldCheckWanted)") < withheld.index("if (g_sanctioned.story < 0)")


def test_a_cutscene_a_callback_loaded_for_later_comes_back_with_the_save():
    """
    ac_hiku_011's callback loads ac_hiku_013's schedule for the lift button
    and no station's load script does, so a save between them loaded with the
    button live and nothing to play: pressing it waited on a cutscene that
    never came, a softlock (Ch 9, Nov 19). Cutscene data in and out goes in the
    journal, scripts' and the event-load boxes' loads and anyone's unloads,
    and a load only ever puts data back in
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr DWORD kCutsceneRva = 0x47E80, kCutsceneHolderRva = 0x23FCE04;" in source
    assert "{kCutsceneRva, kCutsceneHead, sizeof kCutsceneHead}," in source
    assert "HookAt(kCutsceneRva, &CutsceneHook, g_cutscene)" in source
    hook = source[source.index("int __fastcall CutsceneHook("):]
    hook = hook[:hook.index("\n}\n")]
    assert "(!load || FromScript(from) || FromAreaBox(from))" in hook
    assert "case Op::Cutscene: return 5;" in source and "bool Replayable(Op op) { return Kind(op) <= 5; }" in source
    missing = source[source.index("std::vector<Entry> Missing("):]
    missing = missing[:missing.index("\n}\n")]
    assert "if (w.op == Op::Cutscene && !w.i) continue;" in missing
    replay = source[source.index("bool Replay(const Entry& e) {"):]
    replay = replay[:replay.index("\n}\n")]
    assert "case Op::Cutscene: {" in replay and "g_base + kCutsceneRva" in replay
    # a newer op in an older reader is a bad file, not a crash
    assert "op < 0 || op > static_cast<int>(Op::Cutscene) ||" in source


def test_the_field_goes_back_after_she_lands_not_before():
    """
    0.25.17 put the lift and its apron back before she spawned, and a load
    onto plain walkway near the lift went through as well as the apron, where
    with the replay after the landing (0.25.10) walkway loads there held. So
    the replay waits for her, and a refused bg anim stays out of the journal
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "EarlyReplay" not in source
    replay = source[source.index("bool Replay(const Entry& e) {"):]
    replay = replay[:replay.index("\n}\n")]
    assert "if (took) Note(" in replay
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    real = tick.index("g_realFrom = g_notes.load();")
    assert "if (g_landCheckAt) g_landCheckAt = now;" in tick[real:real + 400]


def test_a_scene_that_moves_her_holds_save_till_she_walks_off():
    """
    the button cutscene stood her on the lift it brought down and the game
    calls a parked lift floor, so the footing check passed a save on it. A
    scene that moves her over 2 m latches the ride guard where it left her,
    and nothing closer goes on the trail. A lift needs 6 m off it, a scene's
    spot only a metre: the Ch 9 jump's landing has less than 6 m of walkway
    before the button, and the slab it lands her on is the moving-piece check's
    """
    source = SOURCE.read_text(encoding="utf-8")
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    assert "g_sceneFromRead = Position(PlayerHandle(), g_sceneFrom);" in tick
    end = tick.index('Moment("back in free roam after a scene");')
    after = tick[end:end + 1600]
    # a scene that changed nothing doesn't latch: the no-op check comes first
    assert after.index("g_sceneSince = -1;") < after.index("g_rodeByScene = true;")
    assert "std::hypot(at.v[0] - g_sceneFrom.v[0], at.v[2] - g_sceneFrom.v[2]) > 2.0f" in after
    # and a load's own scene never latches it (Ch 9, Nov 22: every load logged one)
    assert "g_sceneSince >= 0 && !g_worldCheckWanted && g_sanctioned.story >= 0" in after
    assert "constexpr float kOffRide = 6.0f;" in source and "constexpr float kOffScene = 1.0f;" in source
    assert "(g_rodeByScene ? kOffScene : kOffRide);" in tick
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert withheld.index("if (g_rode)") < withheld.index("if (!g_idleSolid")


def test_a_spot_that_dropped_her_is_skipped_on_the_next_load_and_save_waits():
    """
    nothing moves her body once it's spawned, so a spot that dropped her is
    written beside its .where and the next load of that save starts on the
    newest ground she walked that's 5 m or more off it: the Ch 9 apron's
    three newest trail spots were all on the apron. Save stays off till she's
    been at a station, since she fell somewhere a save would softlock, and a
    new save over the slot drops the list
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr float kSamePiece = 5.0f;" in source
    assert 'std::string LostPath(int slot) { return WherePath(slot) + ".lost"; }' in source
    write = source[source.index("void WriteWhere(const SlotHash& save) {"):]
    assert "DeleteFileA(LostPath(save.slot).c_str());" in write[:200]
    assert "if (DeleteFileA(WherePath(slot).c_str())) g_staleSlot = slot;\n        DeleteFileA(LostPath(slot).c_str());" in source
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert withheld.index("if (g_lostLanding)") < withheld.index("g_sanctioned.story < 0")
    assert "g_lostLanding = false;" in source[source.index("void Sanctify("):]
    arm = source[source.index("void TryArm(int marker) {"):]
    arm = arm[:arm.index("\n}\n")]
    assert "g_lostLanding = false;" in arm


def test_the_layers_under_her_are_surveyed_at_each_debug_pause_and_nothing_hooks_threads():
    """
    layer 11, the one the controller's ray uses, came back empty under every
    floor (0.25.18), so with Debug on each pause logs what the same ray finds
    under her on every layer, to map against which spots load and which drop
    her. The hardware watch that found the ray is gone: setting debug
    registers on every thread is no thing to leave in
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "SetThreadContext" not in source and "AddVectoredExceptionHandler" not in source
    survey = source[source.index("void SurveyLayers(const Vec4& at) {"):]
    survey = survey[:survey.index("\n}\n")]
    assert "bit < 0 ? -1 : 1 << bit" in survey
    tick = source[source.index("void Tick() {"):]
    tick = tick[:tick.index("\n}\n")]
    dump = tick.index("DumpActor();")
    assert "SurveyLayers(at);" in tick[dump:dump + 200]
    assert tick.rfind("if (g_debug) {", 0, dump) > 0


def test_save_stays_off_on_a_moving_piece_of_the_level():
    """
    the level's own floor isn't in the engine's ray world, only the stuff it
    moves: every spot that dropped her on load read layer 16 at her feet, the
    Ch 9 slab reads it before the button too, and plain floor reads nothing.
    So Save asks the same ray for anything but her body and the plane under
    the field, through the half metre either side of her feet. It's the last
    check, being a call into the engine, and no world means no answer rather
    than a no
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr int kNotFloorLayers = ~((1 << 1) | (1 << 14));" in source
    piece = source[source.index("bool OnMovingPiece(const Vec4& at) {"):]
    piece = piece[:piece.index("\n}\n")]
    assert "if (!world) return false;" in piece
    # only in a field a script or cutscene has posed a bg anim in: Palumpolum's
    # plaza reads 16 nearly everywhere and has none
    assert piece.index("if (!g_fieldAnimates) return false;") < piece.index("void* world")
    forget = source[source.index("void ForgetWorld(const char* why) {"):]
    assert "g_fieldAnimates = false;" in forget[:forget.index("\n}\n")]
    bg = source[source.index("int __fastcall BgAnimHook("):]
    bg = bg[:bg.index("\n}\n")]
    assert bg.index("if (kept) {") < bg.index("g_fieldAnimates = true;")
    assert "at.v[1] + 0.5f" in piece and "at.v[1] - 0.5f" in piece
    assert "kNotFloorLayers) & 0xFF" in piece
    withheld = source[source.index("const char* Withheld() {"):]
    withheld = withheld[:withheld.index("\n}\n")]
    assert withheld.index("if (Carried())") < withheld.index("OnMovingPiece(at)")
    assert withheld.index("OnMovingPiece(at)") < withheld.rindex("return nullptr;")
    # and the trail a lost load falls back on only keeps what Save would take
    remember = source[source.index("void Remember(const Vec4& at) {"):]
    remember = remember[:remember.index("\n}\n")]
    assert "if (OnMovingPiece(at)) return;" in remember


def test_debug_watches_who_moves_her_through_the_seconds_after_a_load():
    """
    every load ends 1.6 to 1.8 m forward of where it set her down, a station's
    own too, so with Debug on setMatrix gets watched for the leader through
    the after-the-load trace, and those lines say if the stick or the movement
    keys were held. It's on a function every actor calls every frame, so it's
    never hooked without Debug and a failure to hook it doesn't stop the rest
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr DWORD kSetMatrixRva = 0x3C1970;" in source
    assert "if (g_debug && !HookAt(kSetMatrixRva, &SetMatrixHook, g_setMatrix)) {" in source
    hook = source[source.index("int __fastcall SetMatrixHook("):]
    hook = hook[:hook.index("\n}\n")]
    assert "self == g_watchActor.load()" in hook and "g_watchLines++ < 120" in hook
    assert hook.rstrip().endswith("return g_setMatrix(self, edx, m, warp, lock);")
    assert 'Log("  after the load: %.2f %.2f %.2f (%s, controller %08lx)"' in source
    # and the field player controller's flags each line, then what in it
    # changed across the trace
    assert "constexpr DWORD kFieldPlayerRva = 0x23FF554;" in source
    assert 'Log("  controller through the trace: %d of %u dwords changed"' in source


def test_a_load_of_ours_holds_the_controllers_ease_in():
    """
    a load sets the leader 4.06 m off the start marker and the field
    controller eases her in to 2.25 m, which is walking up to the terminal
    on a station's load and a 1.75 m slide off the spot on ours. Ours moves
    the anchor to 2.25 m off the spot on the same line and takes the
    distance as eased, once the anchor's filled in, and only on its own loads
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "constexpr size_t kArrivalAnchor = 0x8C, kArrivalAnchorToo = 0xAC, kArrivalTo = 0x26C, kArrivalAt = 0x34C;" in source
    arm = source[source.index("void TryArm(int marker) {"):]
    arm = arm[:arm.index("\n}\n")]
    assert "g_holdArrivalUntil = GetTickCount64() + 45000;" in arm
    hold = source[source.index("void HoldArrival(int state) {"):]
    hold = hold[:hold.index("\n}\n")]
    assert "if (std::fabs(at - to) < 0.01f || to < 0.5f || to > 10.0f) return;" in hold
    assert "if (len < 1.0f || len > 8.0f) return;" in hold
    # and only while she's still on the spot, so it can't pull her back to it
    assert hold.index("if (off > 0.75f) {") < hold.index("std::memcpy(ctrl + kArrivalAnchor")
    assert "g_landTarget.v[0] - to * dx" in hold and "g_landTarget.v[2] - to * dz" in hold
    assert hold.index("std::memcpy(ctrl + kArrivalAnchor") < hold.index("std::memcpy(ctrl + kArrivalAt")
    assert "if (g_holdArrivalUntil) HoldArrival(state);" in source
