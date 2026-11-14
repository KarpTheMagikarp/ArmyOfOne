"""
The HD Project copied into the other voice set, which is what makes the voices
a plain setting again.

Against the small real-format XIII-2 from hd2_world, with HD installed into the
English set by the stub tools the way a real run installs it, then copied
across. The Japanese set starts as the same files the English one does, the
way the two languages ship, except where a test makes one differ.
"""

import hashlib
import zlib
from pathlib import Path

import pytest

from patcher.mods import hd2_install, hd_install, hd_mirror
from patcher.mods.hd2_recipe import detect
from patcher.mods.hd_install import InstallError
from tests.conftest import Log
from tests.hd2_world import (MODELS, categories, entry_bytes, make_game, make_mod, pin_vanilla,
                             zone_entry_bytes)

ALL = {"zones", "weapons", "npcs", "enemies", "misc", "pcs"}
ZONED = ["bg/loc001/a001/bin/block001.win32.trb", "bg/loc001/a001/bin/block001.win32.imgb",
         "bg/deep/x/y/bin/rock.win32.trb", "bg/deep/x/y/bin/rock.win32.imgb",
         "bg/loc001/a002/bin/block002.win32.imgb", "etc/notes.txt"]


def store(game):
    return game / ".patcher-undo"


def replace_in(game, variant, path, payload):
    """one file in a voice set's white_img made its own, the way a language's copy would be"""
    from patcher.archive import grow
    from patcher.archive.inject import Replacement

    sys_dir = game / "alba_data" / "sys"
    grow.grow(sys_dir / f"white_img{variant}.win32.bin", sys_dir / f"filelist{variant}.win32.bin",
              [Replacement(path, payload)], store=game.parent / "grown")


def replace_in_zone(game, variant, zone, path, payload):
    from patcher.archive import filelist

    blob = game / "alba_data" / "zone" / f"white_{zone}{variant}_img.win32.bin"
    name = f"zone/filelist_{zone}{variant}.win32.bin"
    book = filelist.parse(entry_bytes(game, name, variant))
    data = zlib.compress(payload, 9)
    sector = -(-blob.stat().st_size // 2048)
    with blob.open("r+b") as handle:
        handle.seek(sector * 2048)
        handle.write(data)
    filelist.relocate_entry(book, path, sector, len(payload), len(data))
    replace_in(game, variant, name, filelist.rebuild(book))


def build(tmp_path, monkeypatch, selected=ALL, before=None):
    """both voice sets, the mod, and HD installed into English. `before` changes the game first"""
    game = make_game(tmp_path, "u")
    make_game(tmp_path, "c")
    if before:
        before(game)
    root = make_mod(tmp_path, monkeypatch)
    pin_vanilla(monkeypatch, game)
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    result = hd2_install.install(game, detect(root), categories(), set(selected), store(game),
                                 on_line=Log())
    assert result.ok, (result.errors, result.lines)
    return game


@pytest.fixture
def world(tmp_path, monkeypatch):
    return build(tmp_path, monkeypatch)


def files(game, variant):
    """every file a voice set is, by name"""
    found = [*(game / "alba_data" / "sys").glob(f"*{variant}.win32.bin"),
             *(game / "alba_data" / "zone").glob(f"white_*{variant}_img.win32.bin")]
    return {p.name: p.read_bytes() for p in found}


def copy(game, **kw):
    kw.setdefault("on_line", Log())
    return hd_mirror.copy(game, store(game), "u", "c", **kw)


def records(game, variant):
    from patcher.archive.filelist import load
    return [e.raw_fields + (e.path,) for e in
            load(game / "alba_data" / "sys" / f"filelist{variant}.win32.bin").entries()]


def test_what_an_install_built_goes_into_the_other_voice_set(world):
    game = world
    assert hd_install.voices_with_hd(game) == {"u"}

    outcome = copy(game)

    assert outcome.whole and outcome.files == 14 and outcome.zones == ["z0010"], outcome
    for path, size in MODELS.items():
        if size:
            assert entry_bytes(game, path, "c") == entry_bytes(game, path, "u"), path
    for path in ZONED:
        assert zone_entry_bytes(game, "z0010", path, "c") == \
            zone_entry_bytes(game, "z0010", path, "u"), path
    assert b"HD" in zone_entry_bytes(game, "z0010", ZONED[0], "c")
    assert hd_install.voices_with_hd(game) == {"u", "c"}
    assert not hd_mirror.lacking(game, "u", "c")


def test_the_other_set_only_grows(world):
    """
    nothing it had moves: every record HD didn't touch reads the same, the
    bytes the archives held are still the first bytes they hold, and the set
    the files came from isn't written at all
    """
    from collections import Counter

    game = world
    english, japanese = files(game, "u"), files(game, "c")
    hd = {p for p, size in MODELS.items() if size and b"HD" in entry_bytes(game, p, "u")}
    hd.add("zone/filelist_z0010c.win32.bin")
    untouched = Counter(r for r in records(game, "c") if r[3] not in hd)
    was = {r[3]: r[:3] for r in records(game, "c") if r[3] in hd}

    copy(game)

    assert files(game, "u") == english
    after = files(game, "c")
    for name in ("white_imgc.win32.bin", "white_z0010c_img.win32.bin"):
        assert after[name].startswith(japanese[name]) and len(after[name]) > len(japanese[name])
    assert Counter(r for r in records(game, "c") if r[3] not in hd) == untouched
    assert all(r[:3] != was[r[3]] for r in records(game, "c") if r[3] in hd)


def test_the_other_set_is_backed_up_first_and_revert_puts_both_back(world):
    game = world
    vanilla_japanese = files(game, "c")
    backup = store(game) / "hdproject"
    vanilla_english = {name: (backup / name).read_bytes() for name in files(game, "u")}

    copy(game)

    for name, data in vanilla_japanese.items():
        assert (backup / name).read_bytes() == data, f"{name} wasn't backed up as it was"
    taken = hd_install._manifest(backup)["files"]
    assert taken["white_imgc.win32.bin"]["sha256"] == hashlib.sha256(
        vanilla_japanese["white_imgc.win32.bin"]).hexdigest()
    assert hd_install.partnered(taken, "filelistc.win32.bin", "white_imgc.win32.bin")

    hd_install.restore(hd_install.restore_prep(game, store(game)))

    assert files(game, "c") == vanilla_japanese
    assert files(game, "u") == vanilla_english


def test_a_second_copy_has_nothing_to_do_and_writes_nothing(world):
    game = world
    copy(game)
    japanese = files(game, "c")
    manifest = (store(game) / "hdproject" / "backup.json").read_bytes()
    said = Log()

    outcome = copy(game, on_line=said)

    assert outcome.already and not outcome.files and not outcome.zones
    assert files(game, "c") == japanese
    assert (store(game) / "hdproject" / "backup.json").read_bytes() == manifest
    assert any("already have everything" in line for line in said), said


def test_a_file_that_differs_between_the_sets_keeps_its_pass_out(tmp_path, monkeypatch):
    """
    the Japanese copy of f001's textures isn't the English one, so HD built from
    the English one isn't HD for it. The characters stay out whole, the zones
    still go, and the line says which file and why
    """
    path = "chr/fa/f001/bin/f001.win32.imgb"
    game = build(tmp_path, monkeypatch,
                 before=lambda g: replace_in(g, "c", path, b"a Japanese f001" * 300))
    japanese = entry_bytes(game, path, "c")
    said = Log()

    outcome = copy(game, on_line=said)

    assert "characters" in outcome.blocked and outcome.zones == ["z0010"], outcome
    assert path in outcome.blocked["characters"]
    assert entry_bytes(game, path, "c") == japanese
    assert b"HD" not in entry_bytes(game, "chr/weapon/w101/bin/w101.win32.imgb", "c")
    assert b"HD" in zone_entry_bytes(game, "z0010", ZONED[0], "c")
    assert any(path in line and "isn't the same file" in line for line in said), said


def test_a_zone_file_that_differs_keeps_the_zones_out(tmp_path, monkeypatch):
    path = "bg/loc001/a001/bin/block001.win32.imgb"
    game = build(tmp_path, monkeypatch,
                 before=lambda g: replace_in_zone(g, "c", "z0010", path, b"jp block" * 200))
    zone = files(game, "c")["white_z0010c_img.win32.bin"]

    outcome = copy(game)

    assert "zones" in outcome.blocked and outcome.files == 14 and not outcome.zones, outcome
    assert files(game, "c")["white_z0010c_img.win32.bin"] == zone
    assert b"HD" in entry_bytes(game, "chr/weapon/w101/bin/w101.win32.imgb", "c")


def prep_for(game, variant, zones=()):
    """the voice set backed up the way an install into it would back it up"""
    sys_dir, zone_dir = game / "alba_data" / "sys", game / "alba_data" / "zone"
    return hd_install.Prep(variant=variant, archives=[sys_dir / f"filelist{variant}.win32.bin",
                                                      sys_dir / f"white_img{variant}.win32.bin"],
                           extra=[zone_dir / f"white_{z}{variant}_img.win32.bin" for z in zones],
                           backup_dir=store(game) / "hdproject")


def test_the_last_install_wins_in_both_sets(world):
    """
    a pair the Japanese set got some other way, one half of it here, comes out
    as the English pair whole: the last install's files are in both sets, and
    an .imgb never ends up beside a .trb from another build
    """
    game = world
    hd_install.back_up(prep_for(game, "c"))
    path = "chr/weapon/w101/bin/w101.win32.imgb"
    replace_in(game, "c", path, b"Japanese HD from somewhere else" * 400)

    outcome = copy(game)

    assert outcome.whole and outcome.files == 14, outcome
    for half in (path, path[:-4] + "trb"):
        assert entry_bytes(game, half, "c") == entry_bytes(game, half, "u"), half
    assert not hd_mirror.lacking(game, "u", "c")
    assert copy(game).already


def test_a_newer_build_in_one_set_reaches_the_other(world):
    """
    a repair, or the mod's next release, rebuilds files the other set already
    has a copy of. At the same length too, which the indexes alone can't see
    """
    game = world
    copy(game)
    path = "chr/weapon/w101/bin/w101.win32.imgb"
    first = entry_bytes(game, path, "u")
    replace_in(game, "u", path, bytes(reversed(first)))
    assert entry_bytes(game, path, "u") != entry_bytes(game, path, "c")

    outcome = copy(game)

    assert outcome.files == 1 and not outcome.already, outcome
    assert entry_bytes(game, path, "c") == entry_bytes(game, path, "u")


def test_a_pass_turned_down_isnt_work_to_do_until_the_files_change(tmp_path, monkeypatch):
    """
    the drawer asks on every open, and a pass copy() refuses with these vanilla
    files would only be refused again: every switch to that language ran it.
    What it found is about f001's records in the two sets, so a different
    Japanese f001 is asked about afresh
    """
    from patcher.archive import filelist

    path = "chr/fa/f001/bin/f001.win32.imgb"
    game = build(tmp_path, monkeypatch,
                 before=lambda g: replace_in(g, "c", path, b"a Japanese f001" * 300))
    assert len(hd_mirror.lacking(game, "u", "c").files) == 14

    copy(game)

    lack = hd_mirror.lacking(game, "u", "c")
    assert not lack, (lack.files, lack.zones, lack.owed)
    kept = store(game) / "hdproject" / "filelistc.win32.bin"
    book = filelist.parse(kept.read_bytes())
    entry = next(e for e in book.entries() if e.path.replace("\\", "/").lower() == path)
    filelist.relocate_entry(book, entry.path, entry.sector_offset + 1, entry.uncompressed,
                            entry.stored)
    kept.write_bytes(filelist.rebuild(book))
    assert len(hd_mirror.lacking(game, "u", "c").files) == 14


def test_what_a_copy_found_holds_either_way_round(tmp_path, monkeypatch):
    """
    f001 differs between the two vanilla sets whichever one HD went into
    first, so Japanese's own build of it isn't something English lacks
    """
    path = "chr/fa/f001/bin/f001.win32.imgb"
    game = build(tmp_path, monkeypatch,
                 before=lambda g: replace_in(g, "c", path, b"a Japanese f001" * 300))
    copy(game)
    hd_install.back_up(prep_for(game, "c"))
    replace_in(game, "c", path, b"Japanese HD built from its own f001" * 400)

    assert not hd_mirror.lacking(game, "c", "u").files


def test_a_copy_that_never_finished_is_owed_whatever_the_sizes_say(world):
    import threading

    from patcher.mods.hd_runner import Cancelled

    game = world
    copy(game)
    path = "chr/weapon/w101/bin/w101.win32.imgb"
    replace_in(game, "u", path, bytes(reversed(entry_bytes(game, path, "u"))))
    assert not hd_mirror.lacking(game, "u", "c"), "meant to be a rebuild the sizes can't see"

    stop = threading.Event()
    stop.set()
    with pytest.raises(Cancelled):
        copy(game, cancel=stop)

    assert hd_mirror.lacking(game, "u", "c").owed
    assert copy(game).files == 1
    assert not hd_mirror.lacking(game, "u", "c")


def test_an_index_that_wont_go_back_keeps_what_it_points_at(world, monkeypatch):
    """
    the read back fails and so does putting the old index back. Cutting the
    archive back anyway left the index the game reads pointing past its end
    """
    from patcher import fileops
    from patcher.archive.filelist import load

    game = world
    monkeypatch.setattr(hd_mirror, "_read_back", lambda *a, **k: "it came back wrong")
    real, swaps = fileops.write_atomic, []

    def second_fails(target, data, durable=False):
        if Path(target).name == "filelistc.win32.bin":
            swaps.append(target)
            if len(swaps) > 1:
                raise PermissionError("held by a scanner")
        return real(target, data, durable=durable)

    monkeypatch.setattr(fileops, "write_atomic", second_fails)

    with pytest.raises(InstallError, match="Revert"):
        copy(game)

    sys_dir = game / "alba_data" / "sys"
    size = (sys_dir / "white_imgc.win32.bin").stat().st_size
    assert all(e.is_stub or e.byte_offset + e.stored <= size
               for e in load(sys_dir / "filelistc.win32.bin").entries())


def test_bytes_a_killed_copy_left_on_the_end_are_cut_off_by_the_next(world, monkeypatch):
    """the app closed between the appends and the index swap: no undo ran"""
    from patcher import fileops

    game = world
    japanese = files(game, "c")
    real = fileops.write_atomic

    def closed(target, data, durable=False):
        if Path(target).name == "filelistc.win32.bin":
            raise SystemExit("the window closed")
        return real(target, data, durable=durable)

    monkeypatch.setattr(fileops, "write_atomic", closed)
    monkeypatch.setattr(hd_mirror._Tail, "roll_back", lambda self: "")
    with pytest.raises(SystemExit):
        copy(game)
    monkeypatch.undo()
    monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
    monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
    assert len(files(game, "c")["white_imgc.win32.bin"]) > len(japanese["white_imgc.win32.bin"])
    said = Log()

    assert copy(game, on_line=said).whole

    assert any("never pointed anything at, cut off" in line for line in said), said
    for name in ("white_imgc.win32.bin", "white_z0010c_img.win32.bin"):
        assert files(game, "c")[name].startswith(japanese[name])
    once = len(files(game, "c")["white_imgc.win32.bin"])
    fresh = build(Path(str(game.parent) + "-again"), monkeypatch)
    copy(fresh)
    assert once == len(files(fresh, "c")["white_imgc.win32.bin"]), \
        "the copy after a killed one is bigger than a copy that ran once"


def test_a_swap_that_fails_leaves_the_other_set_as_it_was(world, monkeypatch):
    from patcher import fileops

    game = world
    japanese = files(game, "c")
    real = fileops.write_atomic

    def refuse(target, data, durable=False):
        if Path(target).name == "filelistc.win32.bin":
            raise PermissionError("held by something")
        return real(target, data, durable=durable)

    monkeypatch.setattr(fileops, "write_atomic", refuse)

    with pytest.raises(InstallError, match="as they were"):
        copy(game)

    assert files(game, "c") == japanese
    assert hd_install.voices_with_hd(game) == {"u"}
    assert hd_mirror.lacking(game, "u", "c")


def test_a_read_back_that_disagrees_puts_the_old_index_back(world, monkeypatch):
    game = world
    japanese = files(game, "c")
    monkeypatch.setattr(hd_mirror, "_read_back", lambda *a, **k: "it came back wrong")

    with pytest.raises(InstallError, match="came back wrong"):
        copy(game)

    assert files(game, "c") == japanese


def test_stop_while_copying_leaves_the_other_set_as_it_was(world):
    from patcher.mods.hd_runner import Cancelled

    game = world
    japanese = files(game, "c")

    import threading

    stop = threading.Event()

    def stage(label, index, total):
        # through the comparison and the backup, and Stop as the copy starts
        if label.startswith("copying"):
            stop.set()

    with pytest.raises(Cancelled):
        copy(game, cancel=stop, on_step=stage)

    assert {n: d for n, d in files(game, "c").items()} == japanese


def test_no_room_is_refused_before_anything_is_written(world, monkeypatch):
    game = world
    japanese = files(game, "c")
    taken = sorted(p.name for p in (store(game) / "hdproject").iterdir())
    monkeypatch.setattr("patcher.download.free_space", lambda p: 1024)

    with pytest.raises(InstallError, match="isn't room"):
        copy(game)

    assert files(game, "c") == japanese
    # what the copy found out goes beside the backup either way, and it's no backup
    assert sorted(p.name for p in (store(game) / "hdproject").iterdir()
                  if p.name != hd_mirror.MEMO) == taken


def test_files_the_game_holds_are_refused_before_anything_is_written(world, monkeypatch):
    game = world
    japanese = files(game, "c")
    monkeypatch.setattr(hd_install, "in_use",
                        lambda paths: [(Path(p).name, "in use") for p in paths][:1])

    with pytest.raises(InstallError, match="game is running"):
        copy(game)

    assert files(game, "c") == japanese


def test_asking_what_a_set_lacks_reads_no_payloads(world, monkeypatch):
    """the drawer asks on every open, so it's the indexes or nothing"""
    game = world

    def payload(*args, **kwargs):
        raise AssertionError("a payload was read")

    monkeypatch.setattr(hd_mirror._Reader, "read", payload)
    lack = hd_mirror.lacking(game, "u", "c")

    assert len(lack.files) == 14 and lack.zones == ["z0010"] and lack.bytes > 0
    assert not hd_mirror.lacking(game, "c", "u"), "Japanese has nothing English lacks"


def test_a_path_listed_twice_moves_every_copy(tmp_path, monkeypatch):
    """the real filelistu lists ten of c201's files twice, and the game may reach either"""
    from patcher.archive.filelist import load
    from tests import hd2_world

    twice = "chr/pc/c108/bin/c108.win32.trb"
    original = hd2_world.REPEATED
    monkeypatch.setattr(hd2_world, "REPEATED", twice)
    game = build(tmp_path, monkeypatch)
    monkeypatch.setattr(hd2_world, "REPEATED", original)

    copy(game)

    listed = [e for e in load(game / "alba_data" / "sys" / "filelistc.win32.bin").entries()
              if e.path == twice]
    assert len(listed) == 3 and len({e.raw_fields for e in listed}) == 1, listed
    assert b"HD" in entry_bytes(game, twice, "c")


def test_an_index_that_wont_write_back_exactly_is_never_written(world):
    """
    the copy's licence to edit an index is rebuilding it unedited to the same
    bytes, and lookup records that don't land one each on a record mean they're
    not what this thinks they are
    """
    from patcher.archive import filelist

    game = world
    listing = game / "alba_data" / "sys" / "filelistc.win32.bin"
    book = filelist.parse(listing.read_bytes())
    book.entry_records = b"\x00" * len(book.entry_records)
    listing.write_bytes(filelist.rebuild(book))
    japanese = files(game, "c")

    with pytest.raises(InstallError, match="written back"):
        copy(game)

    assert files(game, "c") == japanese


def test_bytes_left_on_the_end_by_a_crash_dont_read_as_hd(world):
    """an append whose index never went in grew the files and changed nothing the game reads"""
    game = world
    for name in ("white_imgc.win32.bin",):
        with (game / "alba_data" / "sys" / name).open("ab") as handle:
            handle.write(b"half a copy" * 5000)
    with (game / "alba_data" / "zone" / "white_z0010c_img.win32.bin").open("ab") as handle:
        handle.write(b"half a zone" * 5000)
    hd_install.back_up(prep_for(game, "c", zones=["z0010"]))

    assert hd_mirror.installed_zones(game, "c") == []
    lack = hd_mirror.lacking(game, "u", "c")
    assert len(lack.files) == 14 and lack.zones == ["z0010"]

    assert copy(game).whole
    assert not hd_mirror.lacking(game, "u", "c")


def test_a_set_that_isnt_all_there_gets_nothing(world):
    game = world
    (game / "alba_data" / "sys" / "white_imgc.win32.bin").unlink()

    assert not hd_mirror.lacking(game, "u", "c")
