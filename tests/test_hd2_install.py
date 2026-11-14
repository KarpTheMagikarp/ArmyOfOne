"""
FF XIII-2 HD installed without its .bat, against a small real-format XIII-2.

The plan tests pin every tool invocation to the argument order their .bat
uses. The install tests run the whole pipeline through stub tools that behave
like the real ones (see hd2_world): an encrypted index is refused, so these
only pass if every index ff13tool sees was opened first, and the stubs leave
the checksum at zero, so the game only reads right if we sealed it again.
"""

from pathlib import Path

import pytest

from patcher.mods import hd2_install, hd_install
from patcher.mods.hd2_install import chr_steps, model_steps, resolve, texture_steps
from patcher.mods.hd2_recipe import detect
from patcher.mods.hd_install import InstallError
from patcher.mods.hd_runner import DISCARD, OK_COUNT, SEAL, SEED
from tests.conftest import Log
from tests.hd2_world import (MODELS, categories, entry_bytes, filler, make_game, make_mod,
                             pin_vanilla, zone_entry_bytes)

GAME_FILES = ("alba_data/sys/filelistu.win32.bin", "alba_data/sys/white_imgu.win32.bin",
              "alba_data/zone/white_z0010u_img.win32.bin")


@pytest.fixture
def world(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    root = make_mod(tmp_path, monkeypatch)
    pin_vanilla(monkeypatch, game)
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    return game, root, detect(root)


def snapshot(game: Path) -> dict:
    return {rel: (game / rel).read_bytes() for rel in GAME_FILES}


def install(game, found, selected, **kw):
    kw.setdefault("on_line", Log())
    return hd2_install.install(game, found, categories(), set(selected), game / ".patcher-undo", **kw)


def game_entries(game):
    from patcher.archive.filelist import load
    return list(load(game / "alba_data" / "sys" / "filelistu.win32.bin").entries())


ALL = {"zones", "weapons", "npcs", "enemies", "misc", "pcs"}


def test_texture_steps_are_their_modtype_in_their_order(world):
    game, root, found = world

    steps = texture_steps(game_entries(game), found, ["fa", "npc", "mon", "pc", "weapon"], "u")

    assert [s.label for s in steps] == ["f001 textures", "n012 textures", "n910 textures",
                                        "m001 textures", "m002 textures", "c108 textures",
                                        "c159 textures", "w101 textures"]
    c108 = steps[5]
    # /s first on the chr call, as FFXIII-2 HD.bat's MODTYPE writes it
    assert c108.args == ["/s", "c108.win32", "models\\orig\\chr\\pc\\c108\\bin\\",
                         "models\\mod\\chr\\pc\\c108\\bin\\", "pc\\"]
    assert c108.tool == "inject2" and c108.success == OK_COUNT and c108.optional
    assert c108.copies == [
        ("models\\mod\\chr\\pc\\c108\\bin\\c108.win32.trb",
         "white_imgu\\chr\\pc\\c108\\bin\\c108.win32.trb"),
        ("models\\mod\\chr\\pc\\c108\\bin\\c108.win32.imgb",
         "white_imgu\\chr\\pc\\c108\\bin\\c108.win32.imgb")]


def test_a_placeholder_and_an_unticked_group_get_no_steps(world):
    game, root, found = world

    labels = [s.label for s in texture_steps(game_entries(game), found, ["fa", "mon"], "u")]

    assert "f002 textures" not in labels          # in the index, holding nothing
    assert "s001 textures" not in labels          # summon: the mod ships no art for it
    assert "c108 textures" not in labels


def test_model_steps_come_from_the_lists_and_their_recipes(world):
    game, root, found = world

    steps = model_steps(found, [("pc", "PCModels.txt"), ("npc", "NPCModels.txt")], "u")

    # 23 and 43 ids listed, and only these four have a recipe in the fixture
    assert [s.label for s in steps] == ["c108 models", "c159 models", "n012 models",
                                        "n910 models"]
    assert steps[0].tool == "models2"
    assert steps[0].args == ["-s", "data\\c108.txt"]
    assert all(s.limit == hd2_install.MODEL_LIMIT for s in steps)
    assert steps[0].copies == [
        ("models\\mod\\chr\\pc\\c108\\bin\\c108.win32.trb",
         "white_imgu\\chr\\pc\\c108\\bin\\c108.win32.trb"),
        ("models\\mod\\chr\\pc\\c108\\bin\\c108.win32.imgb",
         "white_imgu\\chr\\pc\\c108\\bin\\c108.win32.imgb")]


def test_the_character_pass_is_bookended_like_their_bat(world, tmp_path):
    game, root, found = world

    steps = chr_steps(game_entries(game), found, ["pc"], [("pc", "PCModels.txt")], "u",
                      source=tmp_path / "backup")

    assert steps[0].args == ["-x", "filelistu.win32.bin", "white_imgu.win32.bin", "-ff132"]
    assert steps[1].action == DISCARD and steps[1].args == ["white_imgu.win32.bin"]
    assert steps[2].action == SEED and steps[2].args == ["white_imgu", "pc"]
    assert steps[2].source == str(tmp_path / "backup")
    assert steps[-2].args == ["-c", "-ff132", "filelistu.win32.bin", "white_imgu"]
    assert steps[-2].needs_change == "white_imgu"
    assert steps[-1].action == SEAL and steps[-1].args == ["filelistu.win32.bin"]
    # models after textures, the order their MODEL loop runs in
    labels = [s.label for s in steps]
    assert labels.index("c108 models") > labels.index("c159 textures")


def test_resolve_follows_their_order_whatever_the_tick_order(world):
    game, root, found = world

    groups, lists, zones = resolve(found, categories(), ["weapons", "pcs", "npcs", "zones", "misc"])

    assert groups == ["fa", "npc", "pc", "weapon"]
    assert lists == [("pc", "PCModels.txt"), ("npc", "NPCModels.txt")]
    assert zones == ["z0010"]


def test_nothing_ticked_and_a_category_with_nothing_in_it_are_refused(world):
    game, root, found = world

    with pytest.raises(InstallError, match="nothing selected"):
        resolve(found, categories(), [])
    found = dict(found, groups=["pc"], zones=[])
    with pytest.raises(InstallError, match="Weapons would install nothing"):
        resolve(found, categories(), ["weapons"])


def test_a_full_install_puts_the_art_in_and_leaves_the_rest_alone(world):
    game, root, found = world
    seen = Log()

    result = install(game, found, ALL, on_line=seen)

    assert result.ok, (result.errors, result.lines)
    assert result.landed == ["the character archives", "zone z0010"]
    # textures, then the model tool over the top for the ids it remodels
    assert b"HD" in entry_bytes(game, "chr/fa/f001/bin/f001.win32.imgb")
    assert b"HD" in entry_bytes(game, "chr/weapon/w101/bin/w101.win32.imgb")
    assert b"HDMODEL" in entry_bytes(game, "chr/pc/c108/bin/c108.win32.trb")
    # no art for these, so what the game had
    for path in ("chr/mon/m002/bin/m002.win32.imgb", "vfx/field/rain.win32.imgb",
                 "chr/summon/s001/bin/s001.win32.trb"):
        assert entry_bytes(game, path) == filler(path, MODELS[path]), path
    # the zone: both halves of the block with art, nothing else
    assert b"HD" in zone_entry_bytes(game, "z0010", "bg/loc001/a001/bin/block001.win32.trb")
    assert b"HD" in zone_entry_bytes(game, "z0010", "bg/loc001/a001/bin/block001.win32.imgb")
    assert b"HD" in zone_entry_bytes(game, "z0010", "bg/deep/x/y/bin/rock.win32.imgb")
    assert b"HD" not in zone_entry_bytes(game, "z0010", "bg/loc001/a002/bin/block002.win32.imgb")


def test_a_model_the_tool_freezes_on_keeps_its_textures_and_the_rest_go_in(tmp_path,
                                                                          monkeypatch):
    """a live run sat on n111 models, 1288 steps in, with nothing to say why"""
    from tests.hd2_world import MODELS_TOOL

    frozen = MODELS_TOOL.replace(
        "recipe = here(sys.argv[-1])\n",
        "recipe = here(sys.argv[-1])\n"
        "if 'c159' in sys.argv[-1]:\n"
        "    import time; time.sleep(20)\n", 1)
    assert frozen != MODELS_TOOL, "the stub changed under this test"
    game = make_game(tmp_path)
    root = make_mod(tmp_path, monkeypatch, tools={"models2": frozen})
    pin_vanilla(monkeypatch, game)
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr(hd2_install, "MODEL_LIMIT", 1)
    seen = Log()

    result = install(game, detect(root), {"pcs", "npcs"}, on_line=seen)

    assert result.ok, (result.errors, result.lines)
    assert [label for label, _ in result.skipped] == ["c159 models"]
    assert any("c159 models was still running after" in line for line in seen), seen
    c159 = entry_bytes(game, "chr/pc/c159/bin/c159.win32.trb")
    assert b"HD" in c159 and b"HDMODEL" not in c159
    assert b"HDMODEL" in entry_bytes(game, "chr/pc/c108/bin/c108.win32.trb")
    assert b"HDMODEL" in entry_bytes(game, "chr/npc/n012/bin/n012.win32.trb")


def test_the_models_run_through_the_fixed_copy_when_there_is_one(tmp_path, monkeypatch):
    """theirs is never run once a fixed copy is in place, and never changed"""
    from patcher.mods import hd_runner
    from tests.hd2_world import MODELS_TOOL
    from tests.test_hd_runner import stub

    theirs = "import sys\nprint('theirs ran', flush=True)\nsys.exit(9)\n"
    game = make_game(tmp_path)
    root = make_mod(tmp_path, monkeypatch, tools={"models2": theirs})
    pin_vanilla(monkeypatch, game)
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    support = root / "supportfiles"
    before = (support / hd_runner.TOOLS["models2"]).read_bytes()

    def fixed(folder, name, fixed_name):
        return stub(Path(folder), fixed_name, MODELS_TOOL)
    monkeypatch.setattr("patcher.mods.hd2_models_fix.fixed_copy", fixed)
    seen = Log()

    result = install(game, detect(root), {"pcs", "npcs"}, on_line=seen)

    assert result.ok and not result.skipped, (result.errors, result.skipped)
    assert "theirs ran" not in seen
    assert any("models run through" in line for line in seen), seen
    assert b"HDMODEL" in entry_bytes(game, "chr/pc/c108/bin/c108.win32.trb")
    assert (support / hd_runner.TOOLS["models2"]).read_bytes() == before
    # ours, and the next run writes it again
    assert not (support / hd_runner.TOOLS["models2fixed"]).exists()


def test_a_run_that_worked_leaves_only_what_a_later_run_reads(world):
    """
    the zone originals under zone\\ are copies of our backup, read out of it
    again by every zone run, and models\\mod is in the game already. Their
    .bat empties models\\mod itself
    """
    game, root, found = world
    theirs = root / "zone" / "notes.txt"
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_text("somebody's")

    result = install(game, found, ALL)

    assert result.ok, (result.errors, result.lines)
    assert sorted(p.name for p in (root / "zone").iterdir()) == ["notes.txt"]
    assert not (root / "models" / "mod").exists()
    assert any((root / "models" / "orig" / "chr").rglob("*.trb"))


def test_a_zone_run_that_failed_keeps_its_originals(tmp_path, monkeypatch):
    """what the next attempt starts from, and what anybody looks at first"""
    from tests.hd2_world import FF13TOOL

    broken = FF13TOOL.replace(
        "    entries = read_index(index)\n    out = blob.parent",
        "    if '_img_orig' in blob.name:\n"
        "        print('Error: Unable to read the zone.', flush=True)\n"
        "        sys.exit(1)\n"
        "    entries = read_index(index)\n    out = blob.parent", 1)
    assert broken != FF13TOOL, "the stub changed under this test"
    game = make_game(tmp_path)
    root = make_mod(tmp_path, monkeypatch, tools={"pack": broken})
    pin_vanilla(monkeypatch, game)
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)

    result = install(game, detect(root), {"zones"})

    assert not result.ok
    assert (root / "zone" / "white_z0010u_img_orig.win32.bin").is_file()


def test_every_index_the_game_holds_is_sealed_with_a_real_checksum(world):
    from patcher.archive import crypt

    game, root, found = world
    assert install(game, found, ALL).ok

    main = (game / "alba_data" / "sys" / "filelistu.win32.bin").read_bytes()
    zone = entry_bytes(game, "zone/filelist_z0010u.win32.bin")
    for raw in (main, zone):
        assert crypt.is_encrypted(raw)
        plain = crypt.decrypt(raw)
        # the stub leaves zero like the real tool does, so this is ours
        assert crypt.checksum(plain) != 0
        assert crypt.apply_checksum(plain) == plain


def test_the_backup_is_every_file_the_run_writes_to(world):
    game, root, found = world
    before = snapshot(game)

    assert install(game, found, ALL).ok

    backup = game / ".patcher-undo" / "hdproject"
    for rel, data in before.items():
        assert (backup / Path(rel).name).read_bytes() == data, rel


def test_revert_puts_every_byte_back(world):
    from patcher.mods.hd_install import restore, restore_prep

    game, root, found = world
    before = snapshot(game)
    assert install(game, found, ALL).ok
    assert snapshot(game) != before

    put_back = restore(restore_prep(game, game / ".patcher-undo"))

    assert sorted(p.name for p in put_back) == sorted(Path(r).name for r in GAME_FILES)
    assert snapshot(game) == before
    assert not (game / "white_data").exists(), "it restored into XIII's folder"


def test_the_revert_command_takes_it_off(world, tmp_path):
    """the CLI's route, --revert, through the real XIII-2 manifest"""
    from patcher.manifest import load_manifest
    from patcher.revert import revert_all
    from tests.hd2_world import REPO

    game, root, found = world
    before = snapshot(game)
    assert install(game, found, ALL).ok

    results = revert_all(game, load_manifest(REPO / "manifests" / "ffxiii2.json"),
                         tmp_path / "state.json")

    assert [r.ok for r in results] == [True], [r.message for r in results]
    assert snapshot(game) == before


def test_without_the_4gb_patch_nothing_is_touched(tmp_path, monkeypatch):
    game = make_game(tmp_path, laa=False)
    root = make_mod(tmp_path, monkeypatch)
    before = snapshot(game)

    with pytest.raises(InstallError, match="4GB Patch"):
        install(game, detect(root), ALL)

    assert snapshot(game) == before
    assert not (game / ".patcher-undo" / "hdproject").exists()


def test_a_zones_only_install_still_backs_up_white_img(world):
    """the zone commit imports into white_img, so it has to be in the backup"""
    game, root, found = world
    before = snapshot(game)

    result = install(game, found, {"zones"})

    assert result.ok and result.landed == ["zone z0010"]
    backup = game / ".patcher-undo" / "hdproject"
    assert (backup / "white_imgu.win32.bin").read_bytes() == \
        before["alba_data/sys/white_imgu.win32.bin"]
    # no character pass, so every character file is what it was
    assert entry_bytes(game, "chr/pc/c108/bin/c108.win32.trb") == \
        filler("chr/pc/c108/bin/c108.win32.trb", MODELS["chr/pc/c108/bin/c108.win32.trb"])


def test_a_zone_whose_import_into_the_game_fails_is_put_back_whole(tmp_path, monkeypatch):
    """
    the commit is the one step that writes into the game's own white_img, so
    it is the one a failure has to fully undo: blob, image and index
    """
    from tests.hd2_world import FF13TOOL

    failing = FF13TOOL.replace(
        "    payload = wanted.read_bytes()\n",
        "    payload = wanted.read_bytes()\n"
        "    if 'alba_data' in args[2]:\n"
        "        with blob.open('r+b') as h:\n"
        "            h.seek(0, 2); h.write(b'half an import')\n"
        "        print('Error: disk full', flush=True)\n"
        "        sys.exit(1)\n")
    assert failing != FF13TOOL, "the stub changed under this test"
    game = make_game(tmp_path)
    root = make_mod(tmp_path, monkeypatch, tools={"pack": failing})
    pin_vanilla(monkeypatch, game)
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    before = snapshot(game)
    seen = Log()

    result = install(game, detect(root), {"zones"}, on_line=seen)

    assert not result.ok
    assert result.failed.label == "z0010 into the game"
    assert snapshot(game) == before
    assert any("z0010 was left as it was" in line for line in seen), seen


def test_a_run_whose_textures_all_come_back_empty_writes_nothing(world):
    game, root, found = world
    for folder in (root / "textures").iterdir():
        for art in folder.iterdir():
            art.unlink()
    before = snapshot(game)

    result = install(game, found, {"weapons", "enemies", "misc"})

    assert not result.ok
    assert "came back with nothing to install" in result.errors[0]
    assert snapshot(game) == before


def test_adding_a_category_keeps_the_one_already_in(world):
    game, root, found = world

    assert install(game, found, {"weapons"}).ok
    assert install(game, found, {"enemies"}).ok

    assert b"HD" in entry_bytes(game, "chr/weapon/w101/bin/w101.win32.imgb")
    assert b"HD" in entry_bytes(game, "chr/mon/m001/bin/m001.win32.imgb")
    applied = hd2_install.applied(game, found, categories())
    assert {"weapons", "enemies"} <= applied.categories and applied.verified


def test_applied_follows_the_disk_not_the_record(world):
    from patcher.mods.hd_install import restore, restore_prep

    game, root, found = world
    assert not hd2_install.applied(game, found, categories()).categories

    assert install(game, found, {"weapons", "zones"}).ok
    assert hd2_install.applied(game, found, categories()).categories == {"weapons", "zones"}

    restore(restore_prep(game, game / ".patcher-undo"))
    assert not hd2_install.applied(game, found, categories()).categories


def test_sizes_are_what_each_category_rewrites(world):
    game, root, found = world

    sizes = hd2_install.sizes(game, found, categories())

    assert sizes["pcs"] == sum(v for k, v in MODELS.items()
                               if k.startswith("chr/pc/") and k.endswith((".trb", ".imgb")))
    assert sizes["zones"] == (game / "alba_data/zone/white_z0010u_img.win32.bin").stat().st_size
    assert "npcs" in sizes and "weapons" in sizes


def test_a_game_already_modded_by_hand_is_called_out_before_the_backup(world):
    """HD2Setup.bat's own three sizes, read out of the index"""
    from tests.hd2_world import index_bytes, pack_rows

    game, root, found = world
    sys_dir = game / "alba_data" / "sys"
    rows = [(e.path, b"" if e.is_stub else entry_bytes(game, e.path)) for e in game_entries(game)]
    rows = [(p, d + b"modded" if p == "chr/pc/c108/bin/c108.win32.trb" else d) for p, d in rows]
    data, entries = pack_rows(rows)
    (sys_dir / "white_imgu.win32.bin").write_bytes(data)
    (sys_dir / "filelistu.win32.bin").write_bytes(index_bytes(entries))

    prep = hd_install.prepare(game, root, game / ".patcher-undo")
    doubt = hd_install.suspect_backup(root, prep)

    assert "not the size a vanilla copy ships them at (c108)" in doubt


def test_a_zone_already_installed_by_hand_is_called_out(world):
    from patcher.mods import hd_preflight

    game, root, found = world
    hd_preflight.VANILLA2_ZONE_INDEXES["u"]["z0010"] += 8

    prep = hd_install.prepare(game, root, game / ".patcher-undo")
    prep.extra = [game / "alba_data" / "zone" / "white_z0010u_img.win32.bin"]

    assert "z0010u" in hd_install.suspect_backup(root, prep)


def test_a_zone_blob_that_has_grown_is_called_out(world):
    """the index still reads vanilla here, so this is the blob's size alone"""
    game, root, found = world
    blob = game / "alba_data" / "zone" / "white_z0010u_img.win32.bin"
    with blob.open("ab") as f:
        f.write(b"\x00" * 2048)

    prep = hd_install.prepare(game, root, game / ".patcher-undo")
    prep.extra = [blob]

    assert "z0010u" in hd_install.suspect_backup(root, prep)


def test_our_own_cache_is_not_their_installer_after_the_backup_goes(world):
    """
    The recovery steps say to verify and delete .patcher-undo\\hdproject. The
    originals cache in the mod folder is still there, filled by our own seed,
    and the next first backup warned that the mod had been installed by hand.
    """
    import shutil

    game, root, found = world
    assert install(game, found, {"weapons"}).ok
    assert any((root / "models" / "orig").rglob("*.trb")), "nothing was cached to begin with"
    shutil.rmtree(game / ".patcher-undo" / "hdproject")

    prep = hd_install.prepare(game, root, game / ".patcher-undo")

    assert hd_install.suspect_backup(root, prep) == ""


def zone_blob(game: Path, name: str, size: int) -> None:
    # sparse, so a full size zone costs no disk
    zone_dir = game / "alba_data" / "zone"
    zone_dir.mkdir(parents=True, exist_ok=True)
    with (zone_dir / name).open("wb") as f:
        f.truncate(size)


def test_the_japanese_zones_are_measured_by_their_blobs(tmp_path):
    """there is no index table for c, so before these sizes its zones went unchecked"""
    from patcher.mods.hd_preflight import VANILLA2_ZONES, zones_already_modded

    name = "white_z0070c_img.win32.bin"
    zone_blob(tmp_path, name, VANILLA2_ZONES[name])
    assert zones_already_modded(tmp_path, "c") == []

    zone_blob(tmp_path, name, VANILLA2_ZONES[name] + 2048)
    assert [n for n, _, _ in zones_already_modded(tmp_path, "c")] == [name]


def test_each_voice_set_is_measured_against_its_own_sizes(tmp_path):
    from patcher.mods.hd_preflight import VANILLA2_ZONES, zones_already_modded

    for name in ("white_z0070u_img.win32.bin", "white_z0070c_img.win32.bin"):
        zone_blob(tmp_path, name, VANILLA2_ZONES[name])

    assert VANILLA2_ZONES["white_z0070u_img.win32.bin"] != \
        VANILLA2_ZONES["white_z0070c_img.win32.bin"]
    assert zones_already_modded(tmp_path, "u") == []
    assert zones_already_modded(tmp_path, "c") == []


def test_the_zone_table_has_every_zone_the_mod_ships_in_both_sets():
    from patcher.mods.hd_preflight import VANILLA2_ZONE_INDEXES, VANILLA2_ZONES

    zones = VANILLA2_ZONE_INDEXES["u"]
    assert len(zones) == 15
    for variant in "uc":
        assert {n for n in VANILLA2_ZONES if f"{variant}_img" in n} == \
            {f"white_{zone}{variant}_img.win32.bin" for zone in zones}
    # the archives are whole 2048 byte sectors, so any other size is a typo
    assert all(size % 2048 == 0 for size in VANILLA2_ZONES.values())


def test_a_modded_originals_cache_is_not_rebuilt_from(world):
    game, root, found = world
    cached = root / "models" / "orig" / "chr" / "pc" / "c108" / "bin" / "c108.win32.trb"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"x" * 1000)

    assert "c108.win32.trb" in hd2_install.cache_doubt(root, "u")
    cached.write_bytes(b"x" * 677433)
    assert hd2_install.cache_doubt(root, "u") == ""


def test_the_shared_layer_reads_which_game_it_is_from_the_folder(world):
    game, root, found = world

    assert hd_install.find_variant(game) == "u"
    assert [p.name for p in hd_install.archive_pair(game, "u")] == \
        ["filelistu.win32.bin", "white_imgu.win32.bin"]
    assert all("alba_data" in p.parts for p in hd_install.archive_pair(game, "u"))


def test_leftovers_are_cleared_by_this_game_s_names(tmp_path):
    from patcher.mods.hd_layout import XIII2

    for rel in ("white_imgu/chr/x.trb", "white_imgu.win32.bin", "filelistu.win32.bin",
                "white_scru.win32.bin"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")

    hd_install.clear_leftovers(tmp_path, "u", layout=XIII2)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["white_scru.win32.bin"]


def test_the_stub_refuses_an_encrypted_index_like_the_real_tool(world):
    """so the install passing above means every index was opened first"""
    import subprocess
    from patcher.mods import hd_runner

    game, root, found = world
    tool = root / "supportfiles" / hd_runner.TOOLS["pack"]
    sys_dir = game / "alba_data" / "sys"

    done = subprocess.run([str(tool), "-x", str(sys_dir / "filelistu.win32.bin"),
                           str(sys_dir / "white_imgu.win32.bin"), "-ff132"],
                          capture_output=True, text=True)

    assert "Unable to handle encrypted" in done.stdout
    assert not (sys_dir / "white_imgu").exists()
