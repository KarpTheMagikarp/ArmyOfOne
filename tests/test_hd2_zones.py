"""
XIII-2's zones: what gets planned, and what reaches the game when part of it fails.
"""

from pathlib import Path

import pytest

from patcher.mods import hd2_install, hd2_zones
from patcher.mods.hd2_recipe import detect
from patcher.mods.hd_runner import COMMIT2, DISCARD, OK_IMPORTED, OPEN, SEED_PAIR
from tests.conftest import Log
from tests.hd2_world import FF13TOOL, INJECT, categories, make_game, make_mod, pin_vanilla

GAME_FILES = ("alba_data/sys/filelistu.win32.bin", "alba_data/sys/white_imgu.win32.bin",
              "alba_data/zone/white_z0010u_img.win32.bin")


def zone_tree(root: Path, files):
    orig = root / "zone" / "z0010" / "orig"
    for rel, size in files.items():
        (orig / rel).parent.mkdir(parents=True, exist_ok=True)
        (orig / rel).write_bytes(b"z" * size)
    return orig


def test_a_block_is_any_imgb_under_the_zone_with_its_trb(tmp_path):
    zone_tree(tmp_path, {
        "bg/loc001/a001/bin/block001.win32.trb": 10,
        "bg/loc001/a001/bin/block001.win32.imgb": 10,
        "bg/far/down/the/tree/bin/rock.win32.trb": 10,
        "bg/far/down/the/tree/bin/rock.win32.imgb": 10,
        "bg/loc001/a002/bin/lonely.win32.imgb": 10,
        "bg/loc001/a003/bin/empty.win32.trb": 10,
        "bg/loc001/a003/bin/empty.win32.imgb": 0,
    })

    assert hd2_zones.blocks(tmp_path, "z0010") == [
        ("bg\\far\\down\\the\\tree\\bin\\", "rock.win32"),
        ("bg\\loc001\\a001\\bin\\", "block001.win32"),
    ]


def test_block_steps_are_their_updateimgb(tmp_path):
    zone_tree(tmp_path, {"bg/loc001/a001/bin/block001.win32.trb": 10,
                         "bg/loc001/a001/bin/block001.win32.imgb": 10})

    steps = hd2_zones.block_steps(tmp_path, "z0010", "u")

    assert [s.action for s in steps[:2]] == [SEED_PAIR, OPEN]
    assert steps[0].args == ["zone\\filelist_z0010u_orig.win32.bin", "zone\\filelist_z0010u.win32.bin",
                             "zone\\white_z0010u_img_orig.win32.bin", "zone\\white_z0010u_img.win32.bin",
                             "zone\\z0010\\orig", "zone\\z0010\\mod"]
    inject, trb, imgb = steps[2:]
    # /s last on the zone call, as their UPDATEIMGB writes it
    assert inject.args == ["block001.win32", "zone\\z0010\\orig\\bg\\loc001\\a001\\bin\\",
                           "zone\\z0010\\mod\\bg\\loc001\\a001\\bin\\", "z0010\\", "/s"]
    assert trb.args == ["-i", "-ff132", "..\\..\\filelist_z0010u.win32.bin",
                        "..\\..\\white_z0010u_img.win32.bin",
                        "bg\\loc001\\a001\\bin\\block001.win32.trb"]
    assert trb.cwd == "zone\\z0010\\mod" and trb.success == OK_IMPORTED
    assert imgb.args[-1].endswith("block001.win32.imgb")
    # the .imgb waits on the .trb, which waits on the injector
    assert trb.after == inject.label and imgb.after == trb.label


def test_a_zone_goes_in_only_if_something_worked_and_nothing_failed(tmp_path):
    zone_tree(tmp_path, {"bg/a/b/bin/one.win32.trb": 10, "bg/a/b/bin/one.win32.imgb": 10,
                         "bg/c/d/bin/two.win32.trb": 10, "bg/c/d/bin/two.win32.imgb": 10})

    steps = hd2_zones.plan_for(tmp_path, tmp_path / "game", "u", ["z0010"],
                               undo=tmp_path / "undo", backup=tmp_path / "backup")

    commit, discard = steps[-2:]
    assert commit.action == COMMIT2 and discard.action == DISCARD
    assert commit.after_any == [s.label for s in steps if s.payload]
    assert commit.unless_failed == [s.label for s in steps if s.success == OK_IMPORTED]
    assert commit.args[:5] == ["zone\\white_z0010u_img.win32.bin", "zone\\filelist_z0010u.win32.bin",
                               str(tmp_path / "game"), "u", "z0010"]
    assert commit.args[-1] == "zone\\filelist_z0010u_orig.win32.bin"
    assert commit.lands == "zone z0010"


def test_the_originals_come_out_of_the_backup_not_the_game(tmp_path, monkeypatch):
    """a cached blob the wrong size is replaced, one the right size is kept"""
    game = make_game(tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    for name in ("filelistu.win32.bin", "white_imgu.win32.bin"):
        (backup / name).write_bytes((game / "alba_data" / "sys" / name).read_bytes())
    blob = (game / "alba_data" / "zone" / "white_z0010u_img.win32.bin").read_bytes()
    (backup / "white_z0010u_img.win32.bin").write_bytes(blob)
    mod = tmp_path / "mod"
    (mod / "zone").mkdir(parents=True)
    (mod / "zone" / "white_z0010u_img_orig.win32.bin").write_bytes(blob + b"their .bat, modded")
    (mod / "zone" / "filelist_z0010u_orig.win32.bin").write_bytes(b"stale")

    step = hd2_zones.harvest_steps(backup, "u", ["z0010"])[0]
    assert hd2_zones.harvest(step, mod, on_line=Log()) == 0

    assert (mod / "zone" / "white_z0010u_img_orig.win32.bin").read_bytes() == blob
    from tests.hd2_world import entry_bytes
    assert (mod / "zone" / "filelist_z0010u_orig.win32.bin").read_bytes() == \
        entry_bytes(game, "zone/filelist_z0010u.win32.bin")


@pytest.fixture
def ready(tmp_path, monkeypatch):
    def build(tools=None):
        game = make_game(tmp_path)
        root = make_mod(tmp_path, monkeypatch, tools=tools)
        pin_vanilla(monkeypatch, game)
        monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
        monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
        return game, detect(root)
    return build


def snapshot(game):
    return {rel: (game / rel).read_bytes() for rel in GAME_FILES}


def test_a_zone_with_no_art_is_left_alone_and_is_not_a_gap(ready):
    game, found = ready()
    for art in (Path(found["textures"]) / "z0010").iterdir():
        art.unlink()
    before = snapshot(game)

    result = hd2_install.install(game, found, categories(), {"zones"}, game / ".patcher-undo",
                                 on_line=Log())

    # every block said "nothing", which on its own is refused like any run
    # where the tools found no art at all
    assert not result.ok
    assert snapshot(game) == before
    assert result.gaps == []


def test_a_zone_where_one_half_failed_to_import_never_reaches_the_game(ready):
    """half a block is HD headers over vanilla pixels: coloured static"""
    failing = FF13TOOL.replace(
        "    matches = [",
        "    if key.endswith('rock.win32.imgb'):\n"
        "        print('Error: disk full', flush=True)\n"
        "        sys.exit(1)\n"
        "    matches = [")
    assert failing != FF13TOOL, "the stub changed under this test"
    game, found = ready(tools={"pack": failing})
    before = snapshot(game)
    seen = Log()

    result = hd2_install.install(game, found, categories(), {"zones"}, game / ".patcher-undo",
                                 on_line=seen)

    assert result.gaps == ["zone z0010"]
    assert "zone z0010" not in result.landed
    assert snapshot(game) == before
    assert any("[left out] z0010 into the game" in line for line in seen)


def test_a_failed_injector_skips_its_imports(ready):
    # on Windows a failure is a negative count, which a POSIX exit status
    # cannot carry, so this says it the other way the runner reads as failed
    broken = INJECT.replace("if not hits:", "if 'rock' in stem:\n"
                                            "    print('Texture file processing error', "
                                            "file=sys.stderr, flush=True)\n"
                                            "    sys.exit(0)\n"
                                            "if not hits:")
    game, found = ready(tools={"inject2": broken})
    # two of three working, since exactly half is refused as a broken setup
    (Path(found["textures"]) / "z0010" / "block002_hmaa.bin").write_bytes(b"art" * 100)
    seen = Log()

    result = hd2_install.install(game, found, categories(), {"zones"}, game / ".patcher-undo",
                                 on_line=seen)

    # block001 still went in, rock's imports never ran, so nothing half written
    assert result.ok
    commands = seen.on("cmd")
    assert any("block001.win32.trb" in line for line in commands)
    assert not any("rock.win32.trb" in line for line in commands)
    from tests.hd2_world import zone_entry_bytes
    assert b"HD" in zone_entry_bytes(game, "z0010", "bg/loc001/a001/bin/block001.win32.imgb")
    assert b"HD" not in zone_entry_bytes(game, "z0010", "bg/deep/x/y/bin/rock.win32.trb")


def import_into_the_game(body: str) -> str:
    """the stub's -i, with `body` run first when the target is the game's own white_img"""
    stub = FF13TOOL.replace(
        "    payload = wanted.read_bytes()\n",
        "    payload = wanted.read_bytes()\n"
        "    if 'alba_data' in args[2]:\n" + body, 1)
    assert stub != FF13TOOL, "the stub changed under this test"
    return stub


def test_a_failed_import_is_undone_on_an_index_that_lists_a_path_twice(ready):
    """the real filelistu does, 274 times, and a path-keyed check read that as moved"""
    game, found = ready(tools={"pack": import_into_the_game(
        "        with blob.open('r+b') as h:\n"
        "            h.seek(0, 2); h.write(b'half an import')\n"
        "        print('Error: disk full', flush=True)\n"
        "        sys.exit(1)\n")})
    before = snapshot(game)
    seen = Log()

    result = hd2_install.install(game, found, categories(), {"zones"}, game / ".patcher-undo",
                                 on_line=seen)

    assert snapshot(game) == before
    assert not result.landed, result.landed
    assert any("z0010 was left as it was" in line for line in seen), seen.on("err")


def test_a_relayout_in_the_copy_ff13tool_edited_is_not_called_undone(ready):
    """
    XIII-2's import edits a decrypted copy of the index, not the game's, so
    that copy is what says whether anything else moved
    """
    game, found = ready(tools={"pack": import_into_the_game(
        "        after = min((e for e in entries if e.sector_offset > matches[0].sector_offset),\n"
        "                    key=lambda e: e.sector_offset)\n"
        "        data = blob.read_bytes()\n"
        "        end = -(-len(data) // 2048) * 2048\n"
        "        with blob.open('r+b') as h:\n"
        "            h.seek(end)\n"
        "            h.write(data[after.byte_offset:after.byte_offset + after.stored])\n"
        "            h.seek(matches[0].byte_offset); h.write(payload)\n"
        "        grown = [ChunkEntry(path=e.path, sector_offset=end // 2048,\n"
        "                            uncompressed=e.uncompressed, stored=e.stored) if e is after\n"
        "                 else ChunkEntry(path=e.path, sector_offset=e.sector_offset,\n"
        "                                 uncompressed=len(payload), stored=len(payload))\n"
        "                 if e is matches[0] else e for e in entries]\n"
        "        index.write_bytes(index_bytes(grown, sealed=False, zero_checksum=True))\n"
        "        print('Error: could not finish', flush=True)\n"
        "        sys.exit(1)\n")})
    seen = Log()

    result = hd2_install.install(game, found, categories(), {"zones"}, game / ".patcher-undo",
                                 on_line=seen)

    assert not result.ok
    assert not any("z0010 was left as it was" in line for line in seen)
    assert any("moved 1 other files" in line for line in seen.on("err")), seen.on("err")
    assert [str(x) for x in result.landed] == ["zone z0010"]


def test_an_index_that_will_not_open_leaves_no_undo_behind(ready, monkeypatch):
    from patcher.mods import hd2_archive

    game, found = ready()
    real = hd2_archive.open_index

    def will_not_open(source, target=None):
        if Path(source).name == "filelistu.win32.bin":
            raise hd2_archive.SealError(f"{Path(source).name} does not decrypt")
        return real(source, target)

    monkeypatch.setattr(hd2_archive, "open_index", will_not_open)
    before = snapshot(game)
    seen = Log()

    result = hd2_install.install(game, found, categories(), {"zones"}, game / ".patcher-undo",
                                 on_line=seen)

    assert snapshot(game) == before
    assert any("z0010 was left as it was" in line for line in seen), seen.on("err")
    undo = game / ".patcher-undo" / "hdproject-commit"
    assert not [p.name for p in undo.iterdir()] if undo.is_dir() else True
    assert not result.landed


def test_the_read_back_reads_the_copy_that_moved_not_the_first_one_listed(tmp_path):
    """an import rewrites every copy of a repeated path, and any one of them can be the bad one"""
    from patcher.archive.entry import ChunkEntry
    from patcher.mods.hd_zones import _entries, check_archive
    from tests.hd2_world import index_bytes, pack_rows

    rows = [("chr/a.trb", b"A" * 3000), ("vfx/d.imgb", b"D" * 2000), ("sys/t.bin", b"T" * 100),
            ("vfx/d.imgb", b"D" * 2000)]
    data, entries = pack_rows(rows)
    blob, index = tmp_path / "white_imgu.win32.bin", tmp_path / "filelistu.win32.bin"
    index.write_bytes(index_bytes(entries, sealed=False))
    since = _entries(index)

    # the second copy moved to the end, and what landed there does not inflate
    end = len(data)
    blob.write_bytes(data + b"\x78\x9c" + b"\xff" * 40)
    moved = entries[:3] + [ChunkEntry(path="vfx/d.imgb", sector_offset=end // 2048,
                                      uncompressed=2000, stored=42)]
    index.write_bytes(index_bytes(moved, sealed=False))

    problem = check_archive(index, blob, since=since)

    assert "vfx/d.imgb does not read back" in problem, problem


def test_the_zone_cache_follows_the_voice_set_it_was_filled_from(tmp_path):
    """
    XIII-2 keeps one zone\\<z>\\orig for both voice sets. Each set's
    extraction comes out of its own vanilla blob in the backup, so a cache
    filled from English is checked against Japanese's before the tools read it
    """
    from patcher.mods.hd_runner import VOICES_MARK, Step, ZONE_TREE, run_local

    def extracted(variant, data):
        block = tmp_path / "zone" / f"white_z0010{variant}_img_orig" / "bg" / "a" / "bin"
        block.mkdir(parents=True, exist_ok=True)
        (block / "block001.win32.trb").write_bytes(data)
        (block / "block001.win32.imgb").write_bytes(data * 2)

    def tree(variant):
        return Step(tool=None, action=ZONE_TREE,
                    args=[f"zone\\white_z0010{variant}_img_orig", "zone\\z0010\\orig", "bg",
                          "models"], label="caching z0010 backgrounds")

    cache = tmp_path / "zone" / "z0010" / "orig"
    extracted("u", b"English bg")
    assert run_local(tree("u"), tmp_path) == 0
    assert (cache / "bg" / "a" / "bin" / "block001.win32.trb").read_bytes() == b"English bg"

    extracted("c", b"Japanese bg")
    assert run_local(tree("c"), tmp_path) == 0

    assert (cache / "bg" / "a" / "bin" / "block001.win32.trb").read_bytes() == b"Japanese bg"
    assert (cache / VOICES_MARK).read_text(encoding="utf-8") == "c"

    # and a cache already of this set is left as it is, the way it always was
    extracted("c", b"something else")
    run_local(tree("c"), tmp_path)
    assert (cache / "bg" / "a" / "bin" / "block001.win32.trb").read_bytes() == b"Japanese bg"
