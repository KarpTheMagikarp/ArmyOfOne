"""
Every write the HD Project makes into the game, pulled at the plug.

Each test breaks one thing at the moment it would do the most damage and then
checks the game: either byte for byte what it was, or all the way to what it
was going to be, and never something in between. The stub tools are the same
ones the rest of the suite drives; only the failure is new.
"""

import shutil
import threading
from pathlib import Path

import pytest

from patcher import fileops
from patcher.mods import hd_install, hd_runner
from patcher.mods.hd_install import (InstallError, RestoreIncomplete, archive_pair,
                                     back_up, install, prepare, restore)
from patcher.mods.hd_runner import Cancelled
from tests.conftest import Log
from tests.helpers import carries_hd, pack_plain
from tests.test_hd_install import FF13TOOL
from tests.test_hd_runner import stub
from tests.test_hd_zones import DATA, ZONES, world  # noqa: F401  (fixture)

NPCS = {"id": "npcs", "label": "NPCs", "list": "LoadHDNPCs.txt"}


def patched(old: str, new: str) -> str:
    body = FF13TOOL.replace(old, new, 1)
    assert body != FF13TOOL, "the stub changed under this test"
    return body


# the first line of the -i branch that knows what it is importing, and the
# line that writes the pair: the two places a failure can be slipped in
BEFORE_IMPORT = "    key = wanted.as_posix()\n"
AFTER_IMPORT = "    pack(blob, index, rows)\n"


def game_files(game: Path) -> dict:
    sys_dir, zone_dir = game / "white_data" / "sys", game / "white_data" / "zone"
    return {p: p.read_bytes() for p in (*sys_dir.iterdir(), *zone_dir.iterdir()) if p.is_file()}


def assert_untouched(game: Path, before: dict):
    for path, was in before.items():
        assert path.read_bytes() == was, f"{path.name} changed"
    stray = [p.name for p in (game / "white_data").rglob("*")
             if p.name.endswith((".part", ".prev"))]
    assert not stray, f"staging files left in the game: {stray}"


def with_npcs(data: Path) -> list:
    shutil.copy(DATA / "n910.txt", data / "n910.txt")
    (data / "LoadHDNPCs.txt").write_text("#:NPCs\nn n910\n", encoding="utf-8")
    return ZONES + [NPCS]


def test_a_block_whose_pixels_did_not_import_keeps_its_zone_out(world):
    """HD headers over vanilla pixels is the coloured static. The zone stays vanilla instead."""
    game, root, data, support = world
    stub(support, hd_runner.TOOLS["pack"], patched(
        BEFORE_IMPORT,
        "    if wanted.name.endswith('.imgb'):\n"
        "        print('Unable to write ' + wanted.name, file=sys.stderr, flush=True)\n"
        "        sys.exit(1)\n" + BEFORE_IMPORT))
    before = game_files(game)

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", skip_space_check=True)

    assert result.ok
    assert "z010 into the game" in [label for label, _ in result.skipped], \
        "a zone left out has to show up as a gap"
    assert result.landed == []
    assert_untouched(game, {p: b for p, b in before.items() if ".undo" not in str(p)})


def test_the_pixels_are_never_imported_after_the_headers_failed(world):
    game, root, data, support = world
    stub(support, hd_runner.TOOLS["pack"], patched(
        BEFORE_IMPORT,
        "    if wanted.name.endswith('.trb'):\n"
        "        sys.exit(1)\n" + BEFORE_IMPORT))
    seen = Log()

    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", on_line=seen, skip_space_check=True)

    commands = [text for text, stream in seen.tagged if stream == "cmd"]
    assert not any(" -i " in c and c.endswith(".imgb") for c in commands), \
        "the second half went in after the first had failed"


def test_stop_during_the_import_into_the_game_lets_it_finish(world):
    """
    Killing ff13tool halfway through white_img is the one thing with no way
    back. Stop waits for it, and the zone lands whole before the run stops.
    """
    game, root, data, support = world
    stub(support, hd_runner.TOOLS["pack"], patched(
        BEFORE_IMPORT,
        "    if 'white_data' in str(blob):\n"
        "        import time\n"
        "        time.sleep(2)\n" + BEFORE_IMPORT))
    cancel, landed = threading.Event(), []

    def on_step(step, index, total):
        if step.label == "z010 into the game":
            threading.Timer(0.5, cancel.set).start()

    with pytest.raises(Cancelled):
        install(game, root, data, support, ZONES, {"zones"}, store=game / ".undo",
                skip_space_check=True, on_step=on_step, cancel=cancel, landed=landed)

    assert landed == ["zone z010"], "the zone did not finish going in"
    from patcher.mods.hd_state import _zone_index
    inside = _zone_index(game / "white_data" / "sys", "u", "z010")
    assert inside["bg/loc014/m022/bin/block022.win32.imgb"] > 900, \
        "white_imgu does not carry the zone the blob does"


def test_stop_while_the_blob_is_staging_leaves_the_game_alone(world):
    game, root, data, support = world
    before = game_files(game)
    cancel = threading.Event()

    def on_step(step, index, total):
        if step.label == "z010 into the game":
            cancel.set()

    with pytest.raises(Cancelled):
        install(game, root, data, support, ZONES, {"zones"}, store=game / ".undo",
                skip_space_check=True, on_step=on_step, cancel=cancel)

    assert_untouched(game, before)


def test_an_import_into_the_game_that_fails_is_rolled_back(world):
    """dies the way an appending tool would: data on the end, index half written"""
    game, root, data, support = world
    stub(support, hd_runner.TOOLS["pack"], patched(
        BEFORE_IMPORT,
        "    if 'white_data' in str(blob):\n"
        "        with open(blob, 'ab') as handle:\n"
        "            handle.write(b'half an import' * 999)\n"
        "        index.write_bytes(index.read_bytes()[:40])\n"
        "        sys.exit(1)\n" + BEFORE_IMPORT))
    before = game_files(game)

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", skip_space_check=True)

    assert not result.ok
    assert any("left as it was" in line for line in result.errors), result.errors
    assert_untouched(game, before)
    undo = game / ".undo" / "hdproject-commit"
    assert not [p for p in undo.iterdir() if p.is_file()], "the undo for a clean rollback was kept"


def test_a_tool_that_moved_everything_is_not_called_rolled_back(world):
    """a whole relayout cannot be put back from one span, so it has to say so"""
    game, root, data, support = world
    stub(support, hd_runner.TOOLS["pack"], patched(
        AFTER_IMPORT,
        AFTER_IMPORT +
        "    if 'white_data' in str(blob):\n"
        "        sys.exit(1)\n"))

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", skip_space_check=True)

    said = " ".join(result.errors)
    assert "moved" in said and "--revert" in said, said


def test_a_rollback_that_cannot_be_proven_says_to_revert(world):
    """an image the tool shrank cannot be grown back, and nobody gets told it was fine"""
    game, root, data, support = world
    stub(support, hd_runner.TOOLS["pack"], patched(
        BEFORE_IMPORT,
        "    if 'white_data' in str(blob):\n"
        "        blob.write_bytes(blob.read_bytes()[:100])\n"
        "        sys.exit(1)\n" + BEFORE_IMPORT))

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", skip_space_check=True)

    assert not result.ok
    said = " ".join(result.errors)
    assert "could not be fully undone" in said and "--revert" in said, said
    assert [p for p in (game / ".undo" / "hdproject-commit").iterdir()], \
        "the only record of what was there was thrown away"


def test_a_working_pair_cut_short_never_reaches_the_game(world):
    """four words printed over a disk that filled: the read-back is what notices"""
    game, root, data, support = world
    # the last import of the zone, so nothing after it trips over the damage
    stub(support, hd_runner.TOOLS["pack"], patched(
        AFTER_IMPORT,
        AFTER_IMPORT +
        "    if wanted.name == 'block041.win32.imgb':\n"
        "        raw = blob.read_bytes()\n"
        "        blob.write_bytes(raw[:len(raw) - 3000])\n"))
    before = game_files(game)

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", skip_space_check=True)

    assert not result.ok
    assert any("past the end" in line for line in result.errors), result.errors
    assert_untouched(game, before)


def test_the_characters_stay_in_when_a_zone_fails_after_them(world):
    game, root, data, support = world
    categories = with_npcs(data)
    stub(support, hd_runner.TOOLS["pack"], patched(
        BEFORE_IMPORT,
        "    if 'white_data' in str(blob):\n"
        "        sys.exit(1)\n" + BEFORE_IMPORT))
    zone_blob = game / "white_data" / "zone" / "white_z010u_img.win32.bin"
    was = zone_blob.read_bytes()

    result = install(game, root, data, support, categories, {"zones", "npcs"},
                     store=game / ".undo", skip_space_check=True)

    assert not result.ok
    assert result.landed == ["the character archives"]
    sys_dir = game / "white_data" / "sys"
    assert carries_hd(sys_dir / "filelist_scru.win32.bin", sys_dir / "white_scru.win32.bin",
                      "chr/npc/n910/bin/n910.win32.trb"), "the finished characters were thrown away"
    assert zone_blob.read_bytes() == was


def test_a_repack_cut_short_never_reaches_the_game(tmp_path, monkeypatch):
    from tests.test_hd_install import CATEGORIES, make_game, make_mod

    game = make_game(tmp_path)
    short = patched("    pack_plain(pathlib.Path(tree.name + '.win32.bin'), index, rows)\n",
                    "    pack_plain(pathlib.Path(tree.name + '.win32.bin'), index, rows)\n"
                    "    out = pathlib.Path(tree.name + '.win32.bin')\n"
                    "    out.write_bytes(out.read_bytes()[:-4096])\n")
    root, data, support = make_mod(tmp_path, monkeypatch, tools={"pack": short})
    vanilla = {p: p.read_bytes() for p in archive_pair(game, "u")}

    with pytest.raises(InstallError, match="read-back"):
        install(game, root, data, support, CATEGORIES, {"npcs"},
                store=tmp_path / "store", skip_space_check=True)

    for path, was in vanilla.items():
        assert path.read_bytes() == was


def test_no_room_to_swap_is_refused_before_the_game_is_touched(tmp_path, monkeypatch):
    from tests.test_hd_install import CATEGORIES, make_game, make_mod

    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    vanilla = {p: p.read_bytes() for p in archive_pair(game, "u")}
    real = hd_install.free_space
    monkeypatch.setattr(hd_install, "free_space",
                        lambda p: 1024 if "white_data" in str(p) else real(p))

    with pytest.raises(InstallError, match="game's drive") as caught:
        install(game, root, data, support, CATEGORIES, {"npcs"},
                store=tmp_path / "store", skip_space_check=True)

    assert "Nothing in the game has been changed" in str(caught.value)
    for path, was in vanilla.items():
        assert path.read_bytes() == was


def test_the_zone_originals_come_from_the_backup_not_the_game(world):
    """a cache refilled from a game that already carries the zone is HD over HD"""
    from tests.test_hd_zones import built_from

    game, root, data, support = world
    vanilla = (game / "white_data" / "zone" / "white_z010u_img.win32.bin").read_bytes()
    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", skip_space_check=True)
    assert (game / "white_data" / "zone" / "white_z010u_img.win32.bin").read_bytes() != vanilla
    # gone with the run, so the next one fills it again
    assert not (root / "models" / "orig" / "zoneu").exists()

    seen, watch = built_from(root / "models" / "orig" / "zoneu" / "white_z010u_img.win32.bin")
    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", on_step=watch, skip_space_check=True)

    assert seen == [vanilla], "the originals cache was refilled from the modded game"


def test_a_cached_zone_that_isnt_the_backups_size_is_taken_again(world):
    """their .bat fills the cache from whatever the game holds, an HD zone included"""
    from tests.test_hd_zones import built_from

    game, root, data, support = world
    blob = game / "white_data" / "zone" / "white_z010u_img.win32.bin"
    vanilla = blob.read_bytes()
    cached = root / "models" / "orig" / "zoneu" / "white_z010u_img.win32.bin"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(vanilla + b"HD" * 4096)

    seen, watch = built_from(cached)
    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", on_step=watch, skip_space_check=True)

    assert seen == [vanilla], "the zone was built from the HD copy in the cache"


def test_the_backup_carries_a_manifest(tmp_path):
    from tests.test_hd_install import make_game

    game = make_game(tmp_path)
    prep = prepare(game, tmp_path, tmp_path / "store")
    back_up(prep)

    import hashlib
    import json
    files = json.loads((prep.backup_dir / hd_install.MANIFEST).read_text(encoding="utf-8"))["files"]
    for path in prep.archives:
        saved = prep.backup_dir / path.name
        assert files[path.name]["size"] == saved.stat().st_size
        assert files[path.name]["sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()


def test_a_backup_that_shrank_stops_the_next_install(tmp_path):
    from tests.test_hd_install import make_game

    game = make_game(tmp_path)
    prep = prepare(game, tmp_path, tmp_path / "store")
    back_up(prep)
    hd_install.mark_written(prep.backup_dir)
    saved = prep.backup_dir / prep.archives[1].name
    saved.write_bytes(saved.read_bytes()[:100])

    with pytest.raises(InstallError, match="damaged"):
        back_up(prep)
    with pytest.raises(InstallError, match="damaged"):
        restore(prep)


def test_a_restore_is_checked_against_the_hash_before_anything_moves(tmp_path):
    """same size, different bytes: the size check passes and the hash does not"""
    from tests.test_hd_install import make_game

    game = make_game(tmp_path)
    prep = prepare(game, tmp_path, tmp_path / "store")
    back_up(prep)
    hd_install.mark_written(prep.backup_dir)
    for path in prep.archives:
        path.write_bytes(b"MODDED" * 900)
    modded = {p: p.read_bytes() for p in prep.archives}

    saved = prep.backup_dir / prep.archives[1].name
    raw = bytearray(saved.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    saved.write_bytes(bytes(raw))

    with pytest.raises(InstallError):
        restore(prep)
    for path, was in modded.items():
        assert path.read_bytes() == was, "a pair went back half verified"
    assert not [p for p in prep.archives[0].parent.iterdir()
                if p.name.endswith((".part", ".prev"))]


def test_an_older_backup_is_adopted_when_it_holds_together(tmp_path):
    """no manifest, so it is judged on whether its index fits inside its archive"""
    from tests.test_hd_install import make_game

    game = make_game(tmp_path)
    sys_dir = game / "white_data" / "sys"
    pack_plain(sys_dir / "white_scru.win32.bin", sys_dir / "filelist_scru.win32.bin",
               [("chr/npc/n910/bin/n910.win32.trb", b"t" * 5000),
                ("chr/npc/n910/bin/n910.win32.imgb", b"i" * 9000)])
    prep = prepare(game, tmp_path, tmp_path / "store")
    prep.backup_dir.mkdir(parents=True)
    for path in prep.archives:
        shutil.copyfile(path, prep.backup_dir / path.name)

    back_up(prep)
    import json
    files = json.loads((prep.backup_dir / hd_install.MANIFEST).read_text(encoding="utf-8"))["files"]
    assert {files[p.name]["how"] for p in prep.archives} == {"adopted"}

    # and one that died halfway is not
    (prep.backup_dir / hd_install.MANIFEST).unlink()
    saved = prep.backup_dir / "white_scru.win32.bin"
    saved.write_bytes(saved.read_bytes()[:2048])
    with pytest.raises(InstallError, match="damaged"):
        back_up(prep)


def test_a_restore_that_stops_partway_says_what_went_back(world, monkeypatch):
    game, root, data, support = world
    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", skip_space_check=True)
    blob = game / "white_data" / "zone" / "white_z010u_img.win32.bin"
    installed = blob.read_bytes()

    real = fileops.os.replace

    def held(src, dst, *a, **k):
        if Path(src) == blob:
            raise PermissionError(13, "in use")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(fileops.os, "replace", held)
    prep = prepare(game, root, game / ".undo")

    with pytest.raises(RestoreIncomplete) as caught:
        restore(prep)

    names = [p.name for p in caught.value.restored]
    assert "white_imgu.win32.bin" in names and "white_scru.win32.bin" in names
    assert blob.name in str(caught.value)
    assert blob.read_bytes() == installed


@pytest.mark.parametrize("setting, expected", [(b"0", "c"), (b"1", "u"), (None, "u")])
def test_the_variant_follows_the_launchers_voice_setting(tmp_path, setting, expected):
    from tests.test_hd_install import make_game

    game = make_game(tmp_path, "u")
    sys_dir = game / "white_data" / "sys"
    for name in ("filelist_scrc.win32.bin", "white_scrc.win32.bin"):
        shutil.copyfile(sys_dir / name.replace("scrc", "scru"), sys_dir / name)
    if setting is not None:
        (game / "setup.xml").write_bytes(
            b'<?xml version="1.0" encoding="utf-8"?>\r\n<SaveData>\r\n<window_mode>1</window_mode>\r\n'
            b"<voice_mode>" + setting + b"</voice_mode>\r\n<confirm_mode>false</confirm_mode>\r\n"
            b"</SaveData>\r\n")

    assert hd_install.find_variant(game) == expected


def test_last_runs_output_is_not_copied_as_this_runs(tmp_path, monkeypatch):
    """a tool that says it worked without writing anything leaves nothing stale to pick up"""
    from tests.test_hd_install import CATEGORIES, make_game, make_mod

    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch, tools={
        "models": "import sys\nprint('done', flush=True)\nsys.exit(0)\n"})
    stale = root / "models" / "mod" / "chr" / "npc" / "n910" / "bin"
    stale.mkdir(parents=True)
    for suffix in (".win32.trb", ".win32.imgb"):
        (stale / f"n910{suffix}").write_bytes(b"LAST RUN")

    result = install(game, root, data, support, CATEGORIES, {"npcs"},
                     store=tmp_path / "store", skip_space_check=True)

    assert not result.ok, "a run whose only tool wrote nothing installed something"
    assert not (stale / "n910.win32.trb").exists()


def test_a_zones_only_run_is_not_charged_for_the_scr_pair(world, monkeypatch):
    game, root, data, support = world
    prep = prepare(game, root, game / ".undo")

    hd_install.measure(prep, root, unpacks=False)

    assert prep.needed_bytes == 0


def test_a_mod_folder_too_deep_for_the_zones_is_refused_first(world, monkeypatch):
    game, root, data, support = world
    from patcher.mods import hd_preflight
    monkeypatch.setattr(hd_preflight, "MAX_PATH", len(str(root)) + 40)
    before = game_files(game)

    with pytest.raises(InstallError, match="longer than Windows allows"):
        install(game, root, data, support, ZONES, {"zones"},
                store=game / ".undo", skip_space_check=True)

    assert_untouched(game, before)
    assert not (game / ".undo" / "hdproject").exists(), "it got as far as the backup"


@pytest.fixture
def shell_world(tmp_path, monkeypatch):
    """the shell Api over a game that has the scr pair and one zone"""
    from tests.helpers import pack
    from tests.test_hd_end_to_end import make_world
    from tests.test_hd_zones import BLOCK_FILES

    api, events, install_dir, mod = make_world(tmp_path, monkeypatch)
    sys_dir, zone_dir = install_dir / "white_data" / "sys", install_dir / "white_data" / "zone"
    zone_dir.mkdir(parents=True)
    zone_index = pack(zone_dir / "white_z010u_img.win32.bin", zone_dir / "filelist_z010u.win32.bin",
                      [(p.replace("/", "\\"), b"Z" * 900) for p in BLOCK_FILES])
    pack(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
         [("zone\\filelist_z010u.win32.bin", zone_index.read_bytes()),
          ("zone\\z010\\z010.wdb", b"w" * 768)])
    zone_index.unlink()
    (mod / "Data" / "LoadHDEnvironments.txt").write_text("#:Sunleth\nZ10\n", encoding="utf-8")
    shutil.copy(DATA / "z010block022u.txt", mod / "Data" / "z010block022u.txt")
    return api, events, install_dir, mod


def test_a_zone_failing_after_the_characters_is_reported_as_it_is(shell_world):
    from tests.test_hd_end_to_end import run_and_wait, stage_of

    api, events, install_dir, mod = shell_world
    stub(mod / "SupportFiles", hd_runner.TOOLS["pack"], patched(
        BEFORE_IMPORT,
        "    if 'white_data' in str(blob):\n"
        "        sys.exit(1)\n" + BEFORE_IMPORT))

    hd = run_and_wait(api, events, install_dir, mod, ["zones", "npcs"])

    failed = stage_of(hd, "failed")
    assert failed, [e.get("stage") for e in hd]
    assert "character archives already went into the game" in failed.get("note", ""), failed
    # the record follows the disk rather than the failure
    assert api.hd_categories("ff13", "hdproject")["installed"] == ["npcs"]


def test_stop_before_anything_lands_says_so(shell_world):
    from tests.test_hd_end_to_end import run_and_wait, stage_of

    api, events, install_dir, mod = shell_world
    stub(mod / "SupportFiles", hd_runner.TOOLS["pack"],
         "import time\nprint('unpacking', flush=True)\ntime.sleep(30)\n")
    threading.Timer(1.0, api.cancel_hd_project).start()

    hd = run_and_wait(api, events, install_dir, mod, ["npcs"], timeout=40)

    assert stage_of(hd, "cancelled")["note"] == "Stopped. Nothing in the game has been changed."


def test_every_side_operation_gets_its_own_stop(shell_world):
    api = shell_world[0]
    one, two = api._arm_side(), api._arm_side()
    assert one is not two, "starting a second one clears the first one's stop"

    api.cancel_hd_project()
    assert not one.is_set() and not two.is_set(), "the HD Stop reached a download watcher"

    api.cancel()
    assert one.is_set() and two.is_set()
    assert api._hd_cancel.is_set() is True  # set above, and nothing cleared it
    fresh = api._arm_side()
    assert not fresh.is_set(), "a new operation started out already stopped"


def test_the_main_stop_does_not_reach_the_hd_run(shell_world):
    api = shell_world[0]
    api._hd_cancel = threading.Event()
    api.cancel()
    assert not api._hd_cancel.is_set()


def test_only_what_a_run_built_gets_its_pipeline_stamp(tmp_path, monkeypatch):
    from patcher.bridge import UiBridge
    from patcher.mods.hd_state import PIPELINE
    from tests.test_hd_end_to_end import REPO

    state = tmp_path / "state"
    monkeypatch.setattr("patcher.bridge.state_path_for", lambda gid: state / f"{gid}.json")
    bridge = UiBridge(manifest_paths={p.stem: p for p in (REPO / "manifests").glob("*.json")},
                      steam_library_paths=[])

    # zones installed by the build that wrote only half of each block
    bridge.record_hd_installed("ff13", "hdproject", ["zones"], built=[])
    bridge.record_hd_installed("ff13", "hdproject", ["zones", "weapons"], built=["weapons"])

    from patcher.state import load_state
    stamps = load_state(state / "ffxiii.json")["categories:hd_texture_pack"]["pipeline"]
    assert "zones" not in stamps, "adding weapons vouched for zones it never rebuilt"
    assert stamps["weapons"] == PIPELINE.get("weapons", 1)


def test_the_nexus_download_never_offers_the_mods_own_batch_file(tmp_path, monkeypatch):
    """the app's install takes a backup first; their .bat is what it replaces"""
    import main

    unpacked = tmp_path / "HDProject"
    for name in ("Data", "SupportFiles"):
        (unpacked / name).mkdir(parents=True)
    (unpacked / "Data" / "LoadHDNPCs.txt").write_text("#:NPCs\nn n910\n", encoding="utf-8")
    (unpacked / "Data" / "FFXIII_General_Models.bin").write_bytes(b"\x00" * 64)
    (unpacked / "FFXIII HD Installer.bat").write_text("@echo off\n")

    told, launched = [], []
    monkeypatch.setattr(main, "setup_logging", lambda p: __import__("logging").getLogger("t"))
    monkeypatch.setattr(main, "load_api_key", lambda p: "key")
    monkeypatch.setattr(main, "parse_nxm_url",
                        lambda u: type("N", (), {"mod_id": 17, "file_id": 1,
                                                 "game_domain": "finalfantasy13"})())
    monkeypatch.setattr(main, "get_download_url", lambda k, n: "https://example.invalid/x.zip")
    monkeypatch.setattr(main, "hd_pack_staging_dir", lambda d: tmp_path)
    monkeypatch.setattr(main, "download_and_stage", lambda *a, **k: tmp_path / "x.zip")
    monkeypatch.setattr(main, "extract_archive", lambda p: unpacked)
    monkeypatch.setattr(main, "notify", told.append)
    monkeypatch.setattr(main, "ask", lambda prompt: "1")
    monkeypatch.setattr(main.subprocess, "Popen", lambda *a, **k: launched.append(a))

    main.handle_nxm_callback("nxm://finalfantasy13/mods/17/files/1")

    assert not launched, "the mod's own installer was launched"
    assert told and "Choose a folder" in told[0]


def test_setting_up_downloads_asks_before_taking_nxm_from_another_app(monkeypatch):
    """the CLI route used to overwrite Vortex's handler without a word or a backup"""
    import main
    from patcher import nxm_registry

    theirs = nxm_registry.Registration(registered=True, ours=False,
                                       owner='"C:\\\\Vortex\\\\Vortex.exe" -d "%1"')
    calls, opened = [], []
    monkeypatch.setattr(main, "load_api_key", lambda p: "key")
    monkeypatch.setattr(main.webbrowser, "open", lambda url, *a, **k: opened.append(url))
    monkeypatch.setattr(nxm_registry, "current", lambda: theirs)
    monkeypatch.setattr(nxm_registry, "register",
                        lambda force=False: calls.append(force) or
                        nxm_registry.Registration(registered=True, ours=True))

    monkeypatch.setattr(main, "ask", lambda prompt: "")
    main.setup_hd_pack("ffxiii")
    assert calls == [], "it took the protocol without asking"
    assert opened == []

    monkeypatch.setattr(main, "ask", lambda prompt: "y")
    main.setup_hd_pack("ffxiii")
    assert calls == [True]
    # a nexus patch has no source_url, which used to end this at "no link found"
    assert opened == ["https://www.nexusmods.com/finalfantasy13/mods/17?tab=files"]


def held_when(monkeypatch, blocked):
    """a rename that fails the way Windows does while something holds the file"""
    real = fileops.os.replace

    def replace(src, dst, *a, **k):
        if blocked(Path(src), Path(dst)):
            raise PermissionError(32, "being used by another process")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(fileops.os, "replace", replace)


def test_a_failure_that_could_not_undo_itself_never_reads_as_nothing_changed():
    from patcher.mods.hd_install import so_far
    from patcher.mods.hd_runner import Unsure

    alone = so_far([Unsure("zone z010")])
    assert "Nothing" not in alone and "--revert" in alone, alone
    both = so_far(["the character archives", Unsure("zone z010")])
    assert "character archives already went into the game" in both
    assert "may be half in the game" in both and "Nothing else" not in both, both


def test_a_zone_swap_that_cannot_be_undone_says_so_and_revert_finishes_it(world, monkeypatch):
    """the new blob would not go in and the old one would not go back: the zone is missing"""
    game, root, data, support = world
    blob = game / "white_data" / "zone" / "white_z010u_img.win32.bin"
    vanilla = blob.read_bytes()
    held_when(monkeypatch, lambda src, dst: dst == blob)
    landed = []

    result = install(game, root, data, support, ZONES, {"zones"}, store=game / ".undo",
                     skip_space_check=True, landed=landed)

    assert not result.ok
    said = " ".join(result.errors)
    assert "could not be undone" in said and "--revert" in said, said
    assert landed == ["zone z010"] and isinstance(landed[0], hd_runner.Unsure)
    assert "Nothing in the game" not in hd_install.so_far(landed)
    assert not blob.exists(), "this test is meant to leave the zone stranded"

    monkeypatch.undo()
    restore(hd_install.restore_prep(game, game / ".undo"))
    assert blob.read_bytes() == vanilla
    assert not [p for p in blob.parent.iterdir() if p.name.endswith(".prev")]


def test_a_character_swap_that_cannot_be_undone_says_where_things_are(world, monkeypatch):
    game, root, data, support = world
    categories = with_npcs(data)
    sys_dir = game / "white_data" / "sys"
    index, archive = sys_dir / "filelist_scru.win32.bin", sys_dir / "white_scru.win32.bin"
    held_when(monkeypatch, lambda src, dst: (dst == archive and src.name.endswith(".part"))
              or (dst == index and src.name.endswith(".prev")))
    landed = []

    with pytest.raises(InstallError) as caught:
        install(game, root, data, support, categories, {"npcs"}, store=game / ".undo",
                skip_space_check=True, landed=landed)

    said = str(caught.value)
    assert ".prev" in said and "--revert" in said, said
    assert "Nothing in the game has been changed" not in said
    assert [str(x) for x in landed] == ["the character archives"]
    assert isinstance(landed[0], hd_runner.Unsure)


def test_an_import_that_never_opened_the_image_rolls_back_without_writing(world, monkeypatch):
    """
    the game started mid run and holds white_imgu. The import could not touch
    it, and an undo that writes anyway meets the same lock and cries wolf.
    """
    game, root, data, support = world
    stub(support, hd_runner.TOOLS["pack"], patched(
        BEFORE_IMPORT,
        "    if 'white_data' in str(blob):\n"
        "        print('Unable to open ' + str(blob), file=sys.stderr, flush=True)\n"
        "        sys.exit(1)\n" + BEFORE_IMPORT))
    from patcher.archive import spanbackup
    listing = game / "white_data" / "sys" / "filelistu.win32.bin"
    real_write = fileops.write_atomic

    def locked(*a, **k):
        raise PermissionError(32, "white_imgu.win32.bin is in use")

    def write(target, data, **k):
        if Path(target) == listing:
            raise PermissionError(32, "filelistu.win32.bin is in use")
        return real_write(target, data, **k)

    monkeypatch.setattr(spanbackup, "restore", locked)
    monkeypatch.setattr(fileops, "write_atomic", write)
    before = game_files(game)
    landed = []

    result = install(game, root, data, support, ZONES, {"zones"}, store=game / ".undo",
                     skip_space_check=True, landed=landed)

    assert not result.ok
    said = " ".join(result.errors)
    assert "left as it was" in said and "could not be fully undone" not in said, said
    assert landed == []
    assert_untouched(game, {p: b for p, b in before.items() if ".undo" not in str(p)})


def test_the_payload_verdict_never_says_nothing_went_in_after_the_characters_did():
    from patcher.mods.hd_runner import Result, _nothing_applied

    result = Result(applied=1, skipped=[("block 1", 1), ("block 2", 1)],
                    landed=["the character archives"])
    headline = _nothing_applied(result)[0]
    assert "nothing has been copied back" not in headline, headline
    assert "nothing more has gone into the game" in headline


def test_a_zone_left_out_keeps_an_older_builds_zones_marked_stale(shell_world):
    """a stamp over a zone this run never wrote hid an older broken one for good"""
    from patcher.state import load_state
    from tests.test_hd_end_to_end import run_and_wait, stage_of

    api, events, install_dir, mod = shell_world
    api._patcher().record_hd_installed("ff13", "hdproject", ["zones"], built=[])
    stub(mod / "SupportFiles", hd_runner.TOOLS["pack"], patched(
        BEFORE_IMPORT,
        "    if wanted.name.endswith('.imgb'):\n"
        "        print('Unable to write ' + wanted.name, file=sys.stderr, flush=True)\n"
        "        sys.exit(1)\n" + BEFORE_IMPORT))

    hd = run_and_wait(api, events, install_dir, mod, ["zones"])

    done = stage_of(hd, "done")
    assert done and done["skipped"], [e.get("stage") for e in hd]
    state = load_state(install_dir.parents[3] / "state" / "ffxiii.json")
    stamps = state["categories:hd_texture_pack"].get("pipeline") or {}
    assert "zones" not in stamps, stamps


def two_voices(tmp_path, voice=None):
    """a game with the English and the Japanese scr pairs, and a launcher setting"""
    from tests.test_hd_install import make_game

    game = make_game(tmp_path, "u")
    sys_dir = game / "white_data" / "sys"
    for name in ("filelist_scrc.win32.bin", "white_scrc.win32.bin"):
        shutil.copyfile(sys_dir / name.replace("scrc", "scru"), sys_dir / name)
    if voice is not None:
        (game / "setup.xml").write_bytes(
            b'<?xml version="1.0" encoding="utf-8"?>\r\n<SaveData>\r\n'
            b"<voice_mode>" + voice + b"</voice_mode>\r\n</SaveData>\r\n")
    return game


def test_revert_puts_back_every_voice_the_backup_holds(tmp_path):
    """English installed, then Japanese: today's setting says nothing about which is modded"""
    game = two_voices(tmp_path, b"1")
    store = tmp_path / "store"
    vanilla = {p: p.read_bytes() for p in (game / "white_data" / "sys").iterdir()}
    for voice in (b"1", b"0"):
        (game / "setup.xml").write_bytes(
            b'<?xml version="1.0" encoding="utf-8"?>\r\n<SaveData>\r\n'
            b"<voice_mode>" + voice + b"</voice_mode>\r\n</SaveData>\r\n")
        prep = prepare(game, tmp_path, store)
        back_up(prep)
        hd_install.mark_written(prep.backup_dir)
        for path in prep.archives:
            path.write_bytes(b"MODDED" * 900)

    put_back = restore(hd_install.restore_prep(game, store))

    assert sorted(p.name for p in put_back) == sorted(p.name for p in vanilla)
    for path, was in vanilla.items():
        assert path.read_bytes() == was, f"{path.name} is still modded"


def test_a_voice_the_backup_never_saw_does_not_block_the_one_it_did(tmp_path):
    """installed with English voices, launcher switched to Japanese since"""
    game = two_voices(tmp_path, b"1")
    store = tmp_path / "store"
    prep = prepare(game, tmp_path, store)
    was = {p: p.read_bytes() for p in prep.archives}
    back_up(prep)
    hd_install.mark_written(prep.backup_dir)
    for path in prep.archives:
        path.write_bytes(b"MODDED" * 900)
    (game / "setup.xml").write_bytes(
        b'<?xml version="1.0" encoding="utf-8"?>\r\n<SaveData>\r\n'
        b"<voice_mode>0</voice_mode>\r\n</SaveData>\r\n")

    restore(hd_install.restore_prep(game, store))

    for path, before in was.items():
        assert path.read_bytes() == before


def test_a_corrupt_zone_backup_is_caught_before_anything_moves(world):
    """same size, one byte off: found by the hash before the scr pair goes back, not after"""
    game, root, data, support = world
    install(game, root, data, support, ZONES, {"zones"}, store=game / ".undo",
            skip_space_check=True)
    installed = game_files(game)
    saved = game / ".undo" / "hdproject" / "white_z010u_img.win32.bin"
    raw = bytearray(saved.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    saved.write_bytes(bytes(raw))

    with pytest.raises(InstallError, match="damaged"):
        restore(hd_install.restore_prep(game, game / ".undo"))

    assert_untouched(game, installed)


def test_the_seed_takes_models_the_game_already_carries_modded_out_of_the_backup(tmp_path):
    """a new mod folder has no models\\orig, and the game's tree is HD by now"""
    from patcher.mods.hd_runner import SEED, Step, run_local

    npc, weapon = "chr/npc/n910/bin/n910", "chr/weapon/w001/bin/w001"
    vanilla = {f"{npc}.win32.trb": b"t" * 400, f"{npc}.win32.imgb": b"v" * 900,
               f"{weapon}.win32.trb": b"T" * 300, f"{weapon}.win32.imgb": b"W" * 700}
    # the npc went in HD: pixels four times the size, header rewritten at its old length
    live = dict(vanilla, **{f"{npc}.win32.trb": b"H" * 400, f"{npc}.win32.imgb": b"HD" * 1800})

    backup, root = tmp_path / "backup", tmp_path / "mod"
    backup.mkdir()
    root.mkdir()
    pack_plain(backup / "white_scru.win32.bin", backup / "filelist_scru.win32.bin",
               sorted(vanilla.items()))
    pack_plain(root / "white_scru.win32.bin", root / "filelist_scru.win32.bin",
               sorted(live.items()))
    for path, body in live.items():
        (root / "white_scru" / path).parent.mkdir(parents=True, exist_ok=True)
        (root / "white_scru" / path).write_bytes(body)

    step = Step(tool=None, action=SEED, args=["white_scru", "npc", "weapon"],
                label="caching the original models", source=str(backup))
    assert run_local(step, root) == 0

    orig = root / "models" / "orig"
    for path, body in vanilla.items():
        assert (orig / path).read_bytes() == body, f"{path} was cached from the modded tree"


def test_a_same_volume_check_does_not_go_by_the_letter(tmp_path):
    later = tmp_path / "not" / "there" / "yet"
    assert hd_install._same_drive(tmp_path, later)
    assert hd_install._same_drive(later, tmp_path / "mod")


def modded_both_voices(tmp_path, in_game=False):
    """HD in the English and the Japanese pair, from two installs, launcher on English"""
    game = two_voices(tmp_path, b"1")
    store = game / ".patcher-undo" if in_game else tmp_path / "store"
    vanilla = {p: p.read_bytes() for p in (game / "white_data" / "sys").iterdir()}
    for voice in (b"1", b"0"):
        (game / "setup.xml").write_bytes(
            b'<?xml version="1.0" encoding="utf-8"?>\r\n<SaveData>\r\n'
            b"<voice_mode>" + voice + b"</voice_mode>\r\n</SaveData>\r\n")
        prep = prepare(game, tmp_path, store)
        back_up(prep)
        hd_install.mark_written(prep.backup_dir)
        for path in prep.archives:
            path.write_bytes(b"HD " + path.name.encode() + b"." * 900)
    (game / "setup.xml").write_bytes(
        b'<?xml version="1.0" encoding="utf-8"?>\r\n<SaveData>\r\n'
        b"<voice_mode>1</voice_mode>\r\n</SaveData>\r\n")
    return game, store, vanilla


def test_a_rebuild_puts_back_its_own_voice_and_leaves_the_other(tmp_path):
    """set mode rebuilds the voice being played, so that is the only one it may take back"""
    game, store, vanilla = modded_both_voices(tmp_path)
    sys_dir = game / "white_data" / "sys"
    prep = prepare(game, tmp_path, store)

    put_back = restore(prep, variant=prep.variant)

    assert sorted(p.name for p in put_back) == ["filelist_scru.win32.bin", "white_scru.win32.bin"]
    assert (sys_dir / "white_scrc.win32.bin").read_bytes().startswith(b"HD ")
    # and Revert still knows the Japanese pair is ours to take back
    restore(hd_install.restore_prep(game, store))
    for path, was in vanilla.items():
        assert path.read_bytes() == was, f"{path.name} is still modded"


def test_a_rebuild_starts_from_vanilla_whoever_modded_the_game(tmp_path):
    """reverted, then the mod's own installer run by hand: a rebuild still needs a clean base"""
    game, store, vanilla = modded_both_voices(tmp_path)
    restore(hd_install.restore_prep(game, store))
    sys_dir = game / "white_data" / "sys"
    (sys_dir / "white_scru.win32.bin").write_bytes(b"HD BY HAND" * 90)
    prep = prepare(game, tmp_path, store)

    assert restore(hd_install.restore_prep(game, store)) == [], "Revert took back what it never wrote"
    restore(prep, variant=prep.variant)

    assert (sys_dir / "white_scru.win32.bin").read_bytes() == vanilla[sys_dir / "white_scru.win32.bin"]


def test_the_seed_never_trusts_a_backup_of_a_game_that_was_already_modded(tmp_path):
    """
    suspect_backup warned, the backup was taken anyway, the game was verified
    through Steam since. The tree is the vanilla one now, and the backup is not.
    """
    from patcher.mods.hd_runner import SEED, Step, run_local

    npc = "chr/npc/n910/bin/n910"
    vanilla = {f"{npc}.win32.trb": b"t" * 400, f"{npc}.win32.imgb": b"v" * 900}
    hd = {f"{npc}.win32.trb": b"H" * 400, f"{npc}.win32.imgb": b"HD" * 1800}
    backup, root = tmp_path / "backup", tmp_path / "mod"
    backup.mkdir()
    root.mkdir()
    pack_plain(backup / "white_scru.win32.bin", backup / "filelist_scru.win32.bin",
               sorted(hd.items()))
    pack_plain(root / "white_scru.win32.bin", root / "filelist_scru.win32.bin",
               sorted(vanilla.items()))
    for path, body in vanilla.items():
        (root / "white_scru" / path).parent.mkdir(parents=True, exist_ok=True)
        (root / "white_scru" / path).write_bytes(body)

    step = Step(tool=None, action=SEED, args=["white_scru", "npc"],
                label="caching the original models", source=str(backup))
    assert run_local(step, root) == 0

    for path, body in vanilla.items():
        assert (root / "models" / "orig" / path).read_bytes() == body, path


def test_a_placeholder_in_the_backup_seeds_as_the_empty_file_it_is(tmp_path):
    """
    XIII's filelist_scru lists 390 entries with no bytes behind them, 11 of
    the 22 Eidolon .imgb among them. HD for one of those used to stop the run
    at "caching the original models".
    """
    from patcher.mods.hd_runner import SEED, Step, run_local

    summon = "chr/summon/s150/bin/s150"
    vanilla = {f"{summon}.win32.trb": b"t" * 400, f"{summon}.win32.imgb": b""}
    hd = {f"{summon}.win32.trb": b"H" * 400, f"{summon}.win32.imgb": b"HD" * 1800}
    backup, root = tmp_path / "backup", tmp_path / "mod"
    backup.mkdir()
    root.mkdir()
    pack_plain(backup / "white_scru.win32.bin", backup / "filelist_scru.win32.bin",
               sorted(vanilla.items()))
    pack_plain(root / "white_scru.win32.bin", root / "filelist_scru.win32.bin",
               sorted(hd.items()))
    for path, body in hd.items():
        (root / "white_scru" / path).parent.mkdir(parents=True, exist_ok=True)
        (root / "white_scru" / path).write_bytes(body)

    step = Step(tool=None, action=SEED, args=["white_scru", "summon"],
                label="caching the original models", source=str(backup))
    assert run_local(step, root) == 0

    for path, body in vanilla.items():
        assert (root / "models" / "orig" / path).read_bytes() == body, path


def test_a_restore_that_finished_is_not_reported_as_failed_by_its_bookkeeping(world, monkeypatch):
    game, root, data, support = world
    install(game, root, data, support, ZONES, {"zones"}, store=game / ".undo",
            skip_space_check=True)
    blob = game / "white_data" / "zone" / "white_z010u_img.win32.bin"
    vanilla = (game / ".undo" / "hdproject" / blob.name).read_bytes()
    real = hd_install.write_atomic

    def full(target, data, **k):
        if Path(target).name == hd_install.MANIFEST:
            raise PermissionError(32, "backup.json is in use")
        return real(target, data, **k)

    monkeypatch.setattr(hd_install, "write_atomic", full)
    put_back = restore(hd_install.restore_prep(game, game / ".undo"))

    assert blob.name in [p.name for p in put_back]
    assert blob.read_bytes() == vanilla


def test_a_stranded_swap_only_mentions_a_prev_that_exists():
    from patcher.fileops import Stranded

    said = hd_install.stranded(Stranded("held", [(Path("sys/filelist_scru.win32.bin"), None)]))
    assert ".prev" not in said and "was not there before" in said, said


def test_a_backup_that_cannot_be_marked_stops_before_the_copy_back(world, monkeypatch):
    game, root, data, support = world
    categories = with_npcs(data)
    before = game_files(game)

    def unwritable(backup_dir):
        raise PermissionError(13, "access denied")

    monkeypatch.setattr(hd_install, "mark_written", unwritable)
    with pytest.raises(InstallError) as caught:
        install(game, root, data, support, categories, {"npcs"}, store=game / ".undo",
                skip_space_check=True)

    said = str(caught.value)
    assert "record" in said and "game is running" not in said, said
    assert_untouched(game, {p: b for p, b in before.items() if ".undo" not in str(p)})


def test_one_run_at_a_time_on_a_games_archives(tmp_path):
    """the window's install and a --revert typed into a terminal, at once"""
    store = tmp_path / ".patcher-undo"
    with hd_install.exclusive(store):
        with pytest.raises(InstallError, match="another copy"):
            with hd_install.exclusive(store):
                pass
    with hd_install.exclusive(store):
        pass


def test_a_backup_folder_with_nothing_in_it_was_never_written_from(tmp_path):
    empty = tmp_path / "hdproject"
    empty.mkdir()
    assert not hd_install.game_written(empty)
    (empty / "white_scru.win32.bin").write_bytes(b"an older build's copy")
    assert hd_install.game_written(empty)


def test_the_exe_hands_over_actions_and_never_a_bare_game_name():
    """a bare game name is the headless apply, which the exe never ran"""
    import shell

    assert shell.is_cli_invocation(["ffxiii", "--revert"])
    assert shell.is_cli_invocation(["--doctor"])
    assert shell.is_cli_invocation(["nxm://finalfantasy13/mods/17/files/1"])
    assert not shell.is_cli_invocation(["ffxiii"])
    assert not shell.is_cli_invocation(["ffxiii", "--path", "D:/Games/FFXIII"])
    assert not shell.is_cli_invocation(["--debug"])


def test_stop_while_the_characters_stage_leaves_the_backup_unmarked(world, monkeypatch):
    """the game is untouched, so a Revert later must not treat it as ours to take back"""
    game, root, data, support = world
    categories = with_npcs(data)
    sys_dir = game / "white_data" / "sys"
    real = hd_install.stage_copy

    def stopped(source, target, **k):
        if Path(target).parent == sys_dir:
            raise fileops.CopyCancelled("stopped while copying")
        return real(source, target, **k)

    monkeypatch.setattr(hd_install, "stage_copy", stopped)
    # the copy, which is what a mod folder on another drive gets
    monkeypatch.setattr(hd_install, "_same_drive", lambda one, other: False)
    with pytest.raises(Cancelled):
        install(game, root, data, support, categories, {"npcs"}, store=game / ".undo",
                skip_space_check=True)

    assert not hd_install.game_written(game / ".undo" / "hdproject")


def test_stop_before_the_rebuilt_pair_is_moved_in_leaves_the_game_alone(world, monkeypatch):
    """the same drive, where it's a rename with no chunks for a Stop to land between"""
    import threading

    game, root, data, support = world
    categories = with_npcs(data)
    before = {p.name: p.read_bytes() for p in (game / "white_data" / "sys").glob("*.bin")}
    cancel = threading.Event()
    real = hd_install.verify_rebuilt

    def checked_then_stopped(*a, **k):
        problem = real(*a, **k)
        cancel.set()
        return problem

    monkeypatch.setattr(hd_install, "verify_rebuilt", checked_then_stopped)
    with pytest.raises(Cancelled):
        install(game, root, data, support, categories, {"npcs"}, store=game / ".undo",
                skip_space_check=True, cancel=cancel)

    assert not hd_install.game_written(game / ".undo" / "hdproject")
    assert {p.name: p.read_bytes() for p in (game / "white_data" / "sys").glob("*.bin")} == before
    assert not list((game / "white_data" / "sys").glob("*.part*")), "a moved half was left behind"


def test_a_rebuilt_pair_on_the_games_drive_is_moved_not_copied(world, monkeypatch):
    game, root, data, support = world
    categories = with_npcs(data)
    sys_dir = game / "white_data" / "sys"
    real = hd_install.stage_copy

    def no_copies(source, target, **k):
        assert Path(target).parent != sys_dir, f"{Path(source).name} was copied into the game"
        return real(source, target, **k)

    monkeypatch.setattr(hd_install, "stage_copy", no_copies)
    install(game, root, data, support, categories, {"npcs"}, store=game / ".undo",
            skip_space_check=True)

    assert not (root / "white_scru.win32.bin").exists()
    assert hd_install.game_written(game / ".undo" / "hdproject")


def test_a_rebuild_that_covers_the_whole_backup_settles_it(tmp_path):
    """nothing ticked on a game with one voice set is as full a restore as Revert's"""
    from tests.test_hd_install import make_game

    game = make_game(tmp_path)
    prep = prepare(game, tmp_path, tmp_path / "store")
    back_up(prep)
    hd_install.mark_written(prep.backup_dir)
    for path in prep.archives:
        path.write_bytes(b"MODDED" * 900)

    restore(prep, variant=prep.variant)

    assert not hd_install.game_written(prep.backup_dir)


def test_no_unpack_while_an_install_may_be_working_out_of_the_folder(shell_world):
    api, events, install_dir, mod = shell_world
    api._hd_running = True

    answer = api.unpack_drop(str(install_dir.parent / "FFXIII HD Project.zip"))

    assert not answer["ok"]
    dropped = [e for e in events if e.get("type") == "dropped"]
    assert dropped and "running" in dropped[-1]["error"], events


def test_an_old_folder_that_will_not_delete_is_never_unpacked_into(tmp_path, monkeypatch):
    """locked leftovers of an older version would be found first and installed"""
    import zipfile

    from tests.test_drop_paths import bridge

    def build(top, tool):
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr(f"{top}/Data/LoadHDNPCs.txt", "#:NPCs\nn n910\n")
            out.writestr(f"{top}/Data/FFXIII_General_Models.bin", b"\x00" * 64)
            out.writestr(f"{top}/SupportFiles/ff13tool.exe", tool)

    archive = tmp_path / "FFXIII HD Project.zip"
    build("FFXIII HD Project v3.1", b"MZ old tools")
    bridge().identify_drop(str(archive))
    monkeypatch.setattr("patcher.bridge.shutil.rmtree", lambda path, **k: None)
    archive.unlink()
    build("FFXIII HD Project v3.2", b"MZ new tools")

    second = bridge().identify_drop(str(archive))

    assert Path(second["detail"]["root"]).name == "FFXIII HD Project v3.2"
    # and found again next time rather than unpacked from scratch on every drop
    third = bridge().identify_drop(str(archive))
    assert third["unpackedTo"] == second["unpackedTo"]


def test_a_revert_that_fails_after_letting_go_of_the_terminal_still_says_so(monkeypatch):
    """the note promised a message box, and a print goes nowhere once the terminal is gone"""
    import logging

    import main

    told = []
    monkeypatch.setattr(main, "notify", told.append)
    monkeypatch.setattr(main, "setup_logging", lambda p: logging.getLogger("t"))

    def locked(game, path):
        raise PermissionError(13, "access denied", "state.json")

    monkeypatch.setattr(main, "revert_game", locked)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "ffxiii", "--revert"])

    with pytest.raises(SystemExit):
        main.main()

    assert told, "nothing reached the user"


def test_a_zone_backed_up_for_the_first_time_off_a_modded_game_is_warned_about(world):
    """the scr pair was settled on an earlier run; this zone's copy is being taken now"""
    game, root, data, support = world
    prep = prepare(game, root, game / ".undo")
    back_up(prep)
    from patcher.mods.hd_zones import zone_archives
    prep.extra = zone_archives(game, prep.variant, ["z010"])

    said = hd_install.suspect_backup(root, prep)

    assert "z010" in said and "for the first time" in said, said


def test_a_character_swap_that_went_back_cleanly_lifts_the_mark(world, monkeypatch):
    """the game was never written, so a hand install after it is not Revert's to take"""
    game, root, data, support = world
    categories = with_npcs(data)
    index = game / "white_data" / "sys" / "filelist_scru.win32.bin"
    held_when(monkeypatch, lambda src, dst: src == index and dst.name.endswith(".prev"))

    with pytest.raises(InstallError, match="could not be written into the game"):
        install(game, root, data, support, categories, {"npcs"}, store=game / ".undo",
                skip_space_check=True)

    assert not hd_install.game_written(game / ".undo" / "hdproject")


def test_a_zone_swap_that_went_back_cleanly_lifts_the_mark(world, monkeypatch):
    game, root, data, support = world
    blob = game / "white_data" / "zone" / "white_z010u_img.win32.bin"
    held_when(monkeypatch, lambda src, dst: src == blob and dst.name.endswith(".prev"))

    result = install(game, root, data, support, ZONES, {"zones"}, store=game / ".undo",
                     skip_space_check=True)

    assert not result.ok and any("left as it was" in line for line in result.errors)
    assert not hd_install.game_written(game / ".undo" / "hdproject")


def test_a_rebuild_never_settles_a_backup_missing_half_of_the_other_voice(tmp_path):
    """
    nor starts one: the install after the restore backs up again, which refuses
    a backup that can't put the other voice back, by which time this one's gone
    """
    game, store, _ = modded_both_voices(tmp_path)
    (store / "hdproject" / "white_scrc.win32.bin").unlink()
    prep = prepare(game, tmp_path, store)
    before = {p: p.read_bytes() for p in (game / "white_data" / "sys").iterdir()}

    with pytest.raises(InstallError, match="white_scrc.win32.bin.*put it back"):
        restore(prep, variant=prep.variant)

    assert {p: p.read_bytes() for p in (game / "white_data" / "sys").iterdir()} == before
    assert hd_install.game_written(store / "hdproject"), "the Japanese pair is still modded"


def test_no_install_starts_while_an_archive_is_still_unpacking(shell_world):
    api, events, install_dir, mod = shell_world
    api._unpacking = True

    answer = api.install_hd_project("ff13", str(mod), ["npcs"], "add")

    assert not answer["ok"] and "unpacking" in answer["error"], answer


def test_an_archive_in_a_folder_it_cannot_write_to_unpacks_to_temp(tmp_path, monkeypatch):
    """Controlled Folder Access on Downloads, or a read-only share"""
    import tempfile
    import zipfile

    from tests.test_drop_paths import bridge

    home = tmp_path / "locked"
    home.mkdir()
    archive = home / "FFXIII HD Project.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("FFXIII HD Project/Data/LoadHDNPCs.txt", "#:NPCs\nn n910\n")
        out.writestr("FFXIII HD Project/Data/FFXIII_General_Models.bin", b"\x00" * 64)
        out.writestr("FFXIII HD Project/SupportFiles/ff13tool.exe", b"MZ")

    real_mkdir, real_mkdtemp = Path.mkdir, tempfile.mkdtemp

    def mkdir(self, *a, **k):
        if self.parent == home:
            raise PermissionError(13, "access denied", str(self))
        return real_mkdir(self, *a, **k)

    def mkdtemp(*a, dir=None, **k):
        if dir is not None and Path(dir) == home:
            raise PermissionError(13, "access denied", str(dir))
        return real_mkdtemp(*a, dir=tmp_path / "temp", **k)

    (tmp_path / "temp").mkdir()
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)

    found = bridge().identify_drop(str(archive))

    assert found["kind"] == "hdproject", found
    assert Path(found["unpackedTo"]).parent == tmp_path / "temp"


def test_revert_only_ever_claims_what_it_applied(tmp_path, monkeypatch):
    """a mod installed by hand since is still there, whatever the footer says"""
    import main
    from types import SimpleNamespace

    told = []
    monkeypatch.setattr(main, "notify", told.append)
    monkeypatch.setattr("patcher.install_resolver.resolve_install_dir",
                        lambda *a, **k: SimpleNamespace(found=True, install_dir=tmp_path))
    monkeypatch.setattr("patcher.revert.revert_all",
                        lambda *a, **k: [SimpleNamespace(label="HD", ok=True, detail="x")])

    main.revert_game("ffxiii")

    assert told and "back to how it was" not in told[-1], told
