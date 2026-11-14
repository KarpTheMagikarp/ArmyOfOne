"""
Any mod made of the game's own files, matched to the entries it replaces and
put on the end of the archives, against the small real-format games the HD
tests use: XIII-2's sealed pair in both voice sets, and XIII's plain scr and
img pairs.
"""

import hashlib
import zipfile
import zlib
from pathlib import Path

import pytest

from patcher.archive.filelist import load
from patcher.mods import game_files as gf
from patcher.mods.hd_zones import read_entry
from tests.hd2_world import MODELS, REPEATED, filler, make_game
from tests.helpers import pack_plain

C108 = "chr/pc/c108/bin/c108.win32.imgb"
C108_TRB = "chr/pc/c108/bin/c108.win32.trb"
N012 = "chr/npc/n012/bin/n012.win32.imgb"


@pytest.fixture
def game(tmp_path):
    made = make_game(tmp_path, "u")
    make_game(tmp_path, "c")
    return made


def sys_of(game):
    return game / "alba_data" / "sys"


def store(game):
    return game / ".patcher-undo"


def fingerprint(game) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(sys_of(game).iterdir())}


def read(game, path, voice="u"):
    sys_dir = sys_of(game)
    return read_entry(sys_dir / f"white_img{voice}.win32.bin",
                      list(load(sys_dir / f"filelist{voice}.win32.bin").entries()), path)


def mod(tmp_path, name, files: dict) -> Path:
    root = tmp_path / name
    for member, data in files.items():
        target = root / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


def put(game, source_path, name=None, version=None, **kw):
    name = name or gf.mod_name(source_path)
    with gf.Source(source_path) as source:
        found = gf.look_in(source, gf.read_game(game), version=version, root=name)
        return gf.install(game, store(game), name, found, source, **kw)


def states(game):
    everything = gf.records(store(game))
    return {r["name"]: gf.state(game, r, everything)["state"] for r in everything}


def test_a_mod_goes_into_both_voice_sets_and_comes_back_off_byte_for_byte(game, tmp_path):
    vanilla = fingerprint(game)
    source = mod(tmp_path, "Serah Outfit v1.2", {f"Data/{C108}": b"NEW" * 4000,
                                                 "readme.txt": b"read me", "shot.png": b"png"})
    done = put(game, source)

    assert done["ok"] and done["files"] == 1 and done["sets"] == ["c", "u"]
    assert read(game, C108, "u") == read(game, C108, "c") == b"NEW" * 4000
    assert states(game) == {"Serah Outfit": "in"}

    gf.remove(game, store(game), "Serah Outfit")
    assert fingerprint(game) == vanilla
    assert gf.records(store(game)) == []


def test_the_original_is_left_where_it_was_under_the_mod(game, tmp_path):
    before = read(game, C108)
    put(game, mod(tmp_path, "m", {C108: b"x" * 9000}))

    record = gf.records(store(game))[0]
    item = record["pairs"][0]["entries"][0]
    sys_dir = sys_of(game)
    with (sys_dir / "white_imgu.win32.bin").open("rb") as handle:
        handle.seek(item["was"][0] * 2048)
        stored = handle.read(item["was"][2])
    assert (zlib.decompress(stored) if item["was"][1] != item["was"][2] else stored) == before


def test_a_file_compressed_in_the_game_goes_in_compressed(game, tmp_path):
    payload = b"compressible " * 5000
    put(game, mod(tmp_path, "m", {C108_TRB: payload}))

    entry = gf.entries(sys_of(game) / "filelistu.win32.bin")[C108_TRB]
    assert entry.is_compressed and entry.uncompressed == len(payload)
    assert read(game, C108_TRB) == payload


def test_a_file_that_wont_compress_goes_in_raw_whatever_the_original_was(game, tmp_path):
    import os
    payload = os.urandom(6000)
    put(game, mod(tmp_path, "m", {C108_TRB: payload}))

    entry = gf.entries(sys_of(game) / "filelistu.win32.bin")[C108_TRB]
    assert entry.uncompressed == entry.stored == len(payload)
    assert read(game, C108_TRB) == payload


def test_the_folder_unpacked_from_one_voice_set_only_goes_into_that_one(game, tmp_path):
    japanese = read(game, C108, "c")
    put(game, mod(tmp_path, "m", {f"_white_imgu.win32.bin/{C108}": b"EN" * 3000}))

    assert read(game, C108, "u") == b"EN" * 3000
    assert read(game, C108, "c") == japanese


def test_a_file_meant_for_one_set_beats_one_meant_for_both(game, tmp_path):
    put(game, mod(tmp_path, "m", {C108: b"BOTH" * 1000, f"white_imgc/{C108}": b"JP" * 3000}))

    assert read(game, C108, "u") == b"BOTH" * 1000
    assert read(game, C108, "c") == b"JP" * 3000


def test_a_bare_file_goes_by_its_name_when_the_game_has_it_once(game, tmp_path):
    loose = tmp_path / "c108.win32.imgb"
    loose.write_bytes(b"L" * 5000)
    done = put(game, loose, name="loose")

    assert done["files"] == 1
    assert read(game, C108) == b"L" * 5000


def test_a_file_in_the_games_own_folders_that_dont_match_isnt_placed_by_name(game):
    found = gf.look([("chr/pc/c999/bin/c108.win32.trb", 10)], gf.read_game(game))
    assert found.unmatched == ["chr/pc/c999/bin/c108.win32.trb"] and not found.picks


def test_a_file_in_the_mods_own_folder_goes_by_its_name(game, tmp_path):
    """a zip with the mod's name as its one folder, which is how most costumes come"""
    import zipfile
    dress = tmp_path / "Serah Dress.zip"
    with zipfile.ZipFile(dress, "w") as archive:
        archive.writestr("Serah Dress/c108.win32.imgb", b"I" * 4000)
        archive.writestr("Serah Dress/c108.win32.trb", b"T" * 6000)
    done = put(game, dress)

    assert done["files"] == 2
    assert read(game, C108) == b"I" * 4000 and read(game, C108_TRB) == b"T" * 6000


def test_a_bare_file_the_game_has_twice_is_left_for_the_folders_to_say(tmp_path):
    shared = make_game(tmp_path, "u", extra=[("chr/other/bin/c108.win32.trb", b"o" * 50)])
    found = gf.look([("c108.win32.trb", 10)], gf.read_game(shared))
    assert found.loose == ["c108.win32.trb"] and not found.picks
    assert "no folders around it" in gf.explain(found, gf.read_game(shared), "m")


def test_two_versions_of_a_file_are_offered_rather_than_guessed(game, tmp_path):
    source = mod(tmp_path, "Hair Pack", {
        f"Hair Pack/Short Hair/{C108}": b"S" * 4000,
        f"Hair Pack/Long Hair/{C108}": b"L" * 4000,
        f"Hair Pack/Long Hair/{N012}": b"N" * 4000,
        f"Hair Pack/common/{C108_TRB}": b"T" * 4000,
    })
    with gf.Source(source) as src:
        found = gf.look(src.members(), gf.read_game(game), src.read)
    assert found.versions == ["Hair Pack/Long Hair", "Hair Pack/Short Hair"]
    assert C108 not in {p for p, _ in found.picks}

    put(game, source, version="Hair Pack/Short Hair")
    assert read(game, C108) == b"S" * 4000
    assert read(game, C108_TRB) == b"T" * 4000
    # the other version's own files stay out too
    assert read(game, N012) != b"N" * 4000


def test_the_same_file_twice_in_a_mod_is_one_file(game, tmp_path):
    source = mod(tmp_path, "m", {f"a/{C108}": b"same" * 900, f"b/{C108}": b"same" * 900})
    with gf.Source(source) as src:
        found = gf.look(src.members(), gf.read_game(game), src.read)
    assert not found.clashes and len(found.picks) == 1


def test_two_files_for_one_place_with_nothing_to_split_them_is_explained(game, tmp_path):
    found = gf.look([(f"x/{C108}", 10), ("x/c108.win32.imgb", 11)], gf.read_game(game))
    assert found.clashes and not found.versions
    assert "different files" in gf.explain(found, gf.read_game(game), "m")


def test_a_file_the_game_already_has_isnt_put_in_again(game, tmp_path):
    vanilla = fingerprint(game)
    done = put(game, mod(tmp_path, "m", {C108: read(game, C108)}))

    assert done["files"] == 0 and done["same"] == 1
    assert fingerprint(game) == vanilla
    assert gf.records(store(game)) == []


def test_a_mod_on_top_of_another_comes_off_in_either_order(game, tmp_path):
    vanilla = read(game, C108)
    put(game, mod(tmp_path, "first", {C108: b"1" * 5000, N012: b"n" * 5000}), name="first")
    put(game, mod(tmp_path, "second", {C108: b"2" * 5000}), name="second")
    assert read(game, C108) == b"2" * 5000
    assert states(game) == {"first": "in", "second": "in"}

    # the one underneath goes first, and the one on top now sits on vanilla
    gf.remove(game, store(game), "first")
    assert read(game, C108) == b"2" * 5000
    assert read(game, N012) == filler(N012, MODELS[N012])
    gf.remove(game, store(game), "second")
    assert read(game, C108) == vanilla
    assert gf.records(store(game)) == []


def test_stacked_mods_taken_off_newest_first_leave_the_game_byte_for_byte(game, tmp_path):
    vanilla = fingerprint(game)
    put(game, mod(tmp_path, "first", {C108: b"1" * 5000}), name="first")
    put(game, mod(tmp_path, "second", {C108: b"2" * 5000}), name="second")

    gf.remove(game, store(game), "second")
    assert read(game, C108) == b"1" * 5000
    gf.remove(game, store(game), "first")
    assert fingerprint(game) == vanilla


def test_the_one_underneath_reads_as_under_the_other(game, tmp_path):
    put(game, mod(tmp_path, "first", {C108: b"1" * 5000}), name="first")
    put(game, mod(tmp_path, "second", {C108: b"2" * 5000}), name="second")

    everything = gf.records(store(game))
    first = next(r for r in everything if r["name"] == "first")
    seen = gf.state(game, first, everything)
    assert seen["covered"] == 2 and seen["under"] == ["second"]


def test_an_update_replaces_the_old_version_and_keeps_what_didnt_change(game, tmp_path):
    vanilla = fingerprint(game)
    put(game, mod(tmp_path, "Outfit v1", {C108: b"v1" * 3000, N012: b"keep" * 1000}))
    archive = sys_of(game) / "white_imgu.win32.bin"
    grown = archive.stat().st_size

    done = put(game, mod(tmp_path, "Outfit v2", {C108: b"v2" * 3000, N012: b"keep" * 1000}))
    assert done["updated"] and done["files"] == 1 and done["adopted"] == 1
    assert [r["name"] for r in gf.records(store(game))] == ["Outfit"]
    assert read(game, C108) == b"v2" * 3000 and read(game, N012) == b"keep" * 1000
    # the unchanged one wasn't put on the end a second time
    assert archive.stat().st_size - grown < 3 * 4096 + 6000 * 2

    gf.remove(game, store(game), "Outfit")
    for voice in "uc":
        assert read(game, C108, voice) == filler(C108, MODELS[C108])
        assert read(game, N012, voice) == filler(N012, MODELS[N012])
    index_names = {k: v for k, v in fingerprint(game).items() if k.startswith("filelist")}
    assert index_names == {k: v for k, v in vanilla.items() if k.startswith("filelist")}


def costs(game, source_path, name):
    with gf.Source(source_path) as source:
        found = gf.look_in(source, gf.read_game(game), root=name)
        return gf._costs(gf._plans(gf.read_game(game), found), found)


def test_an_install_says_how_far_it_has_got_and_an_update_skips_what_it_knows(game, tmp_path,
                                                                             monkeypatch):
    """
    out of a hundred, only climbing, and a hundred once it's all in, with the
    line moving well before the read back. What an update covers was hashed
    as the version going out went in, so reading it again weighs next to
    nothing, where a first install's read of the game is real work
    """
    first = mod(tmp_path, "Outfit v1", {C108: b"v1" * 3000, N012: b"keep" * 1000})
    parts = costs(game, first, "Outfit")
    # each index, each file in each voice set, each file once going in, and
    # each index read back whole
    assert {part: len(each) for part, each in parts.items()} == {
        "setup": 2, "look": 4, "write": 2, "flush": 4, "commit": 2, "check": 6}
    assert all(cost > 0 for each in parts.values() for cost in each.values())
    assert all(cost > gf.FILE for cost in parts["look"].values())

    heard, at_read_back = [], []
    real = gf._read_back

    def read_back(*args):
        at_read_back.append(len(set(heard)))
        return real(*args)

    monkeypatch.setattr(gf, "_read_back", read_back)
    put(game, first, progress=lambda done, of: heard.append((done, of)))
    shares = [done for done, _ in heard]
    assert shares[0] == 0 and shares[-1] == 100 and shares == sorted(set(shares)), shares
    assert {of for _, of in heard} == {100}
    assert at_read_back and at_read_back[0] >= 4, (at_read_back, shares)

    looked = costs(game, mod(tmp_path, "Outfit v2", {C108: b"v2" * 3000, N012: b"keep" * 1000}),
                   "Outfit")["look"]
    assert len(looked) == 4 and set(looked.values()) == {gf.FILE / 8}, looked


def test_each_pass_of_an_install_moves_the_line(game, tmp_path, monkeypatch):
    """reading what's there, putting it in, flushing it and reading it back"""
    from patcher.mods import hd_mirror

    heard, at = [0], {}

    def mark(owner, attr, before, after=None):
        real = getattr(owner, attr)

        def marked(*args, **kwargs):
            at.setdefault(before, heard[-1])
            try:
                return real(*args, **kwargs)
            finally:
                if after:
                    at[after] = heard[-1]
        monkeypatch.setattr(owner, attr, marked)

    mark(gf, "_what_is_there", "looking", "looked")
    mark(hd_mirror._Tail, "close", "flushing")
    mark(gf, "_carry", "-", "flushed")
    mark(gf, "_read_back", "checking", "checked")
    # big enough that their bytes, not a file's bookkeeping, are most of each pass
    put(game, mod(tmp_path, "Outfit", {C108: b"v1" * 100000, N012: b"keep" * 30000}),
        progress=lambda done, of: heard.append(done))

    assert at["looking"] < at["looked"] < at["flushing"] < at["flushed"], at
    # the indexes rebuilt and written between the flush and the read back
    assert at["flushed"] < at["checking"] < at["checked"] < 100 == heard[-1], at
    # and every part all said by the end, nothing left for a leap to 100
    assert heard[-2] == 99, heard


def test_an_update_says_a_hundred_once_the_old_version_is_off(game, tmp_path, monkeypatch):
    """
    the version going out comes off after the new one is in, record by
    record, which is a part of its own: it used to sit on 99 through all of it.
    What doesn't change hands is credited as it's found, not in a leap at the end
    """
    from patcher.mods import hd_mirror, meter

    put(game, mod(tmp_path, "Outfit v1", {C108: b"v1" * 3000, N012: b"keep" * 1000}))
    heard, at_take_off, adds = [], [], []
    real_take_off, real_add = gf._take_off, meter.Meter.add

    def take_off(*args, **kwargs):
        at_take_off.append(heard[-1][0])
        try:
            return real_take_off(*args, **kwargs)
        finally:
            at_take_off.append(heard[-1][0])

    def add(self, name, cost):
        adds.append(name)
        return real_add(self, name, cost)

    monkeypatch.setattr(gf, "_take_off", take_off)
    monkeypatch.setattr(meter.Meter, "add", add)
    real_read_back, real_close = gf._read_back, hd_mirror._Tail.close
    monkeypatch.setattr(gf, "_read_back",
                        lambda *args: adds.append("read back") or real_read_back(*args))
    monkeypatch.setattr(hd_mirror._Tail, "close",
                        lambda self: adds.append("close") or real_close(self))

    done = put(game, mod(tmp_path, "Outfit v2", {C108: b"v2" * 3000, N012: b"keep" * 1000}),
               progress=lambda done, of: heard.append((done, of)))

    assert done["updated"] and done["adopted"] == 1, done
    shares = [done for done, _ in heard]
    assert shares[-1] == 100 and shares == sorted(set(shares)), shares
    # moving while it comes off, a voice set at a time
    assert at_take_off[0] < at_take_off[1] <= shares[-2] < 100, (at_take_off, shares)
    # N012 changed hands where it sits: nothing of it to flush or read back,
    # said before either happens
    assert "flush" in adds[:adds.index("close")], adds
    assert "check" in adds[:adds.index("read back")], adds


def test_the_same_version_again_changes_nothing(game, tmp_path):
    put(game, mod(tmp_path, "Outfit v1", {C108: b"v1" * 3000}))
    now = fingerprint(game)
    heard = []
    done = put(game, mod(tmp_path, "Outfit v1", {C108: b"v1" * 3000}),
               progress=lambda done, of: heard.append(done))
    # nothing to write or take off is as good as done, not a leap at the end
    assert heard[-2:] == [99, 100], heard

    assert done["files"] == 0
    assert fingerprint(game) == now
    assert states(game) == {"Outfit": "in"}


def test_a_path_the_index_lists_twice_moves_every_copy_and_goes_back(game, tmp_path):
    vanilla = fingerprint(game)
    put(game, mod(tmp_path, "m", {REPEATED: b"R" * 3000}))
    copies = [e for e in load(sys_of(game) / "filelistu.win32.bin").entries()
              if e.path == REPEATED]
    assert len(copies) == 2 and copies[0].raw_fields == copies[1].raw_fields

    gf.remove(game, store(game), "m")
    assert fingerprint(game) == vanilla


def test_a_placeholder_the_build_left_empty_can_be_filled(game, tmp_path):
    stub = "chr/fa/f002/bin/f002.win32.trb"
    put(game, mod(tmp_path, "m", {stub: b"filled" * 100}))
    assert read(game, stub) == b"filled" * 100


def test_a_zip_is_read_where_it_is(game, tmp_path):
    archive = tmp_path / "Zip Mod-123-1-0-1699999999.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"Zip Mod/Data/{C108}", b"Z" * 7000)
        zf.writestr("__MACOSX/._junk", b"x")
    done = put(game, archive)
    assert done["name"] == "Zip Mod" and read(game, C108) == b"Z" * 7000


def test_a_failed_swap_leaves_the_game_and_the_records_as_they_were(game, tmp_path, monkeypatch):
    import patcher.fileops as fileops
    vanilla = fingerprint(game)
    real, seen = fileops.write_atomic, []

    def second_index_fails(target, data, durable=False):
        if Path(target).name.startswith("filelist"):
            seen.append(target)
            if len(seen) == 2:
                raise OSError(28, "No space left on device")
        return real(target, data, durable=durable)

    monkeypatch.setattr(fileops, "write_atomic", second_index_fails)
    with pytest.raises(gf.FilesError, match="game is as it was"):
        put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    monkeypatch.setattr(fileops, "write_atomic", real)
    assert fingerprint(game) == vanilla
    assert gf.records(store(game)) == []
    assert not any((store(game) / "files").glob("*.bin"))


def test_a_read_back_that_disagrees_puts_everything_back(game, tmp_path, monkeypatch):
    vanilla = fingerprint(game)
    monkeypatch.setattr(gf, "_read_back", lambda *a: "it reads back as something else")
    with pytest.raises(gf.FilesError, match="didn't read back"):
        put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    assert fingerprint(game) == vanilla and gf.records(store(game)) == []


def test_stop_before_the_indexes_change_leaves_nothing(game, tmp_path):
    import threading

    from patcher.mods.hd_runner import Cancelled
    vanilla = fingerprint(game)
    stop = threading.Event()
    stop.set()
    with pytest.raises(Cancelled):
        put(game, mod(tmp_path, "m", {C108: b"x" * 5000}), cancel=stop)
    assert fingerprint(game) == vanilla


def test_an_archive_held_open_is_refused_before_anything_is_written(game, tmp_path, monkeypatch):
    from patcher.mods import hd_install
    vanilla = fingerprint(game)
    monkeypatch.setattr(hd_install, "in_use",
                        lambda paths: [("white_imgu.win32.bin", "sharing violation")])
    with pytest.raises(gf.FilesError, match="game is running"):
        put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    assert fingerprint(game) == vanilla


def test_no_room_is_refused_before_anything_is_written(game, tmp_path, monkeypatch):
    import patcher.download as download
    vanilla = fingerprint(game)
    monkeypatch.setattr(download, "free_space", lambda path: 1024)
    with pytest.raises(gf.FilesError, match="MB free"):
        put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    assert fingerprint(game) == vanilla


def test_an_index_that_wont_write_back_exactly_is_never_written(game, tmp_path, monkeypatch):
    from patcher.mods import hd_mirror
    vanilla = fingerprint(game)
    monkeypatch.setattr(hd_mirror, "_index_problem", lambda book, raw: "it doesn't rebuild")
    with pytest.raises(gf.FilesError, match="written back exactly"):
        put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    assert fingerprint(game) == vanilla


def test_someone_elses_change_to_the_index_survives_taking_the_mod_off(game, tmp_path):
    from patcher.archive import grow
    from patcher.archive.inject import Replacement

    put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    sys_dir = sys_of(game)
    grow.grow(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
              [Replacement(N012, b"theirs" * 1000)], store=tmp_path / "grown")

    gf.remove(game, store(game), "m")
    assert read(game, C108) == filler(C108, MODELS[C108])
    assert read(game, N012) == b"theirs" * 1000


def repack(game, voice="u"):
    """the pair rebuilt from what its index points at now, every file somewhere new"""
    from tests.hd2_world import write_pair

    sys_dir = sys_of(game)
    index, archive = sys_dir / f"filelist{voice}.win32.bin", sys_dir / f"white_img{voice}.win32.bin"
    rows = [(e.path, read_entry(archive, [e], e.path) if not e.is_stub else b"")
            for e in load(index).entries()]
    # in reverse, so nothing lands where it was
    write_pair(archive, index, list(reversed(rows)))


def test_a_mod_a_repack_carried_with_nothing_to_go_back_to_still_comes_off(game, tmp_path):
    put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    repack(game, "u")
    repack(game, "c")

    # in, wherever the repack put it: what the game holds is what decides
    assert gf.state(game, gf.records(store(game))[0])["state"] == "in"
    # the original's nowhere, either voice set or a backup, so the file stays
    # the mod's and says so, rather than a Remove that fails every time
    done = gf.remove(game, store(game), "m")
    assert done["stuck"] == [C108], done
    assert gf.records(store(game)) == []
    assert read(game, C108, "u") == read(game, C108, "c") == b"x" * 5000


def test_an_original_a_repack_dropped_comes_back_from_the_other_voice_set(game, tmp_path):
    vanilla = read(game, C108, "c")
    put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    repack(game, "u")

    done = gf.remove(game, store(game), "m")
    assert not done["stuck"], done
    assert read(game, C108, "u") == read(game, C108, "c") == vanilla


def test_an_original_a_repack_dropped_comes_back_out_of_the_hd_backup(game, tmp_path):
    """the HD install backs the pair up before it repacks, original and all"""
    vanilla = read(game, C108, "u")
    put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    spares = store(game) / "hdproject"
    spares.mkdir(parents=True)
    for name in ("white_imgu.win32.bin", "filelistu.win32.bin"):
        (spares / name).write_bytes((sys_of(game) / name).read_bytes())
    repack(game, "u")

    gf.remove(game, store(game), "m")
    assert read(game, C108, "u") == read(game, C108, "c") == vanilla
    # the backup still has it in, so a restore of it would put it back
    assert [r.get("off") for r in gf.records(store(game))] == [True]


def test_a_mod_a_restore_took_out_comes_off_the_list_without_touching_anything(game, tmp_path):
    vanilla = fingerprint(game)
    put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    folder = store(game) / "files"
    for spec in gf.records(store(game))[0]["pairs"]:
        (game / spec["index"]).write_bytes((folder / spec["before"]).read_bytes())

    assert states(game) == {"m": "out"}
    gf.remove(game, store(game), "m")
    assert gf.records(store(game)) == []
    assert {k: v for k, v in fingerprint(game).items() if k.startswith("filelist")} == \
        {k: v for k, v in vanilla.items() if k.startswith("filelist")}


def test_an_original_a_repack_moved_isnt_pointed_back_at(game, tmp_path):
    vanilla = read(game, C108, "c")
    put(game, mod(tmp_path, "m", {C108: b"x" * 5000}))
    item = gf.records(store(game))[0]["pairs"][0]["entries"][0]
    archive = sys_of(game) / "white_imgu.win32.bin"
    with archive.open("r+b") as handle:
        handle.seek(item["was"][0] * 2048)
        handle.write(b"\xff" * 64)

    # never the bytes written over where it was: the other voice set's copy
    assert not gf.remove(game, store(game), "m")["stuck"]
    assert read(game, C108, "u") == vanilla


def test_held_names_what_the_file_mods_point_at_now(game, tmp_path):
    put(game, mod(tmp_path, "m", {C108: b"x" * 5000, N012: b"y" * 5000}))
    index = sys_of(game) / "filelistu.win32.bin"
    assert gf.held(index) == {C108, N012}
    assert gf.holders(game, [C108, "chr/pc/c159/bin/c159.win32.imgb"]) == {"m": {C108}}

    gf.remove(game, store(game), "m")
    assert gf.held(index) == frozenset()


def test_holders_can_be_asked_about_the_voice_set_being_read(game, tmp_path):
    """the same path is in both sets, and a costume only in the other isn't this one's"""
    put(game, mod(tmp_path, "English", {f"_white_imgu.win32.bin/{C108}": b"EN" * 3000}))
    put(game, mod(tmp_path, "Japanese", {f"_white_imgc.win32.bin/{C108}": b"JP" * 3000}))

    assert gf.holders(game, [C108]) == {"English": {C108}, "Japanese": {C108}}
    assert gf.holders(game, [C108], index="alba_data/sys/filelistu.win32.bin") == \
        {"English": {C108}}


def test_hd_models_plus_main_download_is_told_from_its_nova_versions():
    from patcher.mods import models_plus

    main = [("HD Models Plus/_FF XIII HD Models+ Install.bat", 10),
            ("HD Models Plus/SupportFiles/FFXIIItrbSetup.bat", 10),
            (f"HD Models Plus/SupportFiles/{models_plus.TOOL}", 10)]
    assert models_plus.is_main_download(main)
    assert models_plus.root_of(main) == "HD Models Plus/"
    nova = [("modconfig.ini", 10), ("EN-Data/chr/pc/c201/bin/c201.win32.imgb", 10)]
    assert not models_plus.is_main_download(nova)


def test_files_laid_out_for_another_game_are_sent_there(game):
    found = gf.look([(f"white_data/{C108}", 10), (f"white_data/{N012}", 10)], gf.read_game(game))
    assert gf.wrong_game(found, gf.read_game(game)) == "FINAL FANTASY XIII"
    assert "Switch to that game" in gf.explain(found, gf.read_game(game), "m")


def test_files_under_this_games_own_data_folder_go_in(game):
    found = gf.look([(f"alba_data/{C108}", 10)], gf.read_game(game))
    assert gf.wrong_game(found, gf.read_game(game)) == ""
    assert found.picks


def test_what_cant_go_in_is_sorted_and_said(game):
    g = gf.read_game(game)
    found = gf.look([
        ("pack/white_imgu.win32.bin", 10),
        ("pack/zone/filelist_z0010u.win32.bin", 10),
        ("pack/bg/loc001/a001/bin/block001.win32.imgb", 10),
        ("pack/textures/c108.dds", 10),
        ("pack/hook.dll", 10),
        ("pack/install.bat", 10),
        ("pack/sound/pack/9999/nothing.win32.scd", 10),
    ], g)
    assert found.whole == ["pack/white_imgu.win32.bin"]
    assert len(found.zones) == 2
    assert found.art == ["pack/textures/c108.dds"]
    assert found.code == ["pack/hook.dll"] and found.installers == ["pack/install.bat"]
    assert found.unmatched == ["pack/sound/pack/9999/nothing.win32.scd"]
    assert not found.picks
    assert "whole archives" in gf.explain(found, g, "m")


def test_an_installer_with_nothing_else_is_said_plainly(game):
    # a .bat gets played on a copy of the game first, so this is what that came to
    found = gf.look([("Mod/Install.bat", 10), ("Mod/tool.exe", 10)], gf.read_game(game))
    assert gf.explain(found, gf.read_game(game), "Mod") == (
        "Mod's installer, Install.bat, put nothing in when it was played on a copy of the game.")
    # a program on its own can't be played on a copy: what it does instead is said
    found = gf.look([("Patch/Patcher.exe", 10)], gf.read_game(game))
    said = gf.explain(found, gf.read_game(game), "Patch")
    assert "doesn't run other installers" not in said
    assert said.startswith("Patch is Patcher.exe, a program that changes the game by itself")
    assert "plain files too, or as a Nova Chrysalia modpack" in said


def xiii(tmp_path):
    """XIII's plain scr pair for the characters and plain img pair, English only"""
    game = tmp_path / "FINAL FANTASY XIII"
    sys_dir = game / "white_data" / "sys"
    sys_dir.mkdir(parents=True)
    pack_plain(sys_dir / "white_scru.win32.bin", sys_dir / "filelist_scru.win32.bin",
               [("chr/pc/c201/bin/c201.win32.imgb", b"lightning" * 400),
                ("chr/pc/c201/bin/c201.win32.trb", b"trb" * 400)])
    pack_plain(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
               [("gui/resident/menu.win32.imgb", b"menu" * 400)])
    return game


def test_xiii_gets_each_file_in_whichever_archive_lists_it(tmp_path):
    game = xiii(tmp_path)
    sys_dir = game / "white_data" / "sys"
    vanilla = {p.name: p.read_bytes() for p in sys_dir.iterdir()}
    source = mod(tmp_path, "m", {"chr/pc/c201/bin/c201.win32.imgb": b"HD" * 900,
                                 "gui/resident/menu.win32.imgb": b"MENU" * 300})
    done = put(game, source)

    assert done["archives"] == ["white_imgu.win32.bin", "white_scru.win32.bin"]
    assert read_entry(sys_dir / "white_scru.win32.bin",
                      list(load(sys_dir / "filelist_scru.win32.bin").entries()),
                      "chr/pc/c201/bin/c201.win32.imgb") == b"HD" * 900
    gf.remove(game, store(game), "m")
    assert {p.name: p.read_bytes() for p in sys_dir.iterdir()} == vanilla


def test_a_game_with_no_data_folder_has_nothing_to_match(tmp_path):
    assert gf.pairs(tmp_path) == [] and gf.read_game(tmp_path).listed == {}


def test_a_pack_for_the_whole_series_puts_in_this_games_part(game):
    g = gf.read_game(game)
    found = gf.look([(f"Pack/alba_data/{C108}", 10), (f"Pack/white_data/{C108}", 10),
                     (f"Pack/white_data/{N012}", 10)], g)
    assert set(found.picks.values()) == {f"Pack/alba_data/{C108}"}
    assert gf.wrong_game(found, g) == ""
    assert "2 files for FINAL FANTASY XIII" in gf.left_out(found)


def backup(game):
    """the HD install's backup of the pairs, taken now"""
    import shutil
    spares = store(game) / "hdproject"
    spares.mkdir(parents=True, exist_ok=True)
    for p in sys_of(game).iterdir():
        if p.name.startswith(("filelist", "white_img")):
            shutil.copy2(p, spares / p.name)


def restore(game):
    """the HD restore, which puts the backup's pairs back"""
    import shutil
    for p in (store(game) / "hdproject").iterdir():
        shutil.copy2(p, sys_of(game) / p.name)


def test_a_mod_waits_to_be_judged_while_one_kept_off_over_it_is_half_off_again(game, tmp_path,
                                                                              monkeypatch):
    import patcher.fileops as fileops
    put(game, mod(tmp_path, "a1", {C108_TRB: b"X" * 6000}), name="Hair")
    put(game, mod(tmp_path, "b", {C108_TRB: b"Y" * 6000}), name="Other")
    backup(game)
    gf.remove(game, store(game), "Other")
    gf.remove(game, store(game), "Hair")
    put(game, mod(tmp_path, "a2", {C108_TRB: b"X" * 6000}), name="Hair")
    restore(game)

    # Other back with the restore, and taken off again in one voice set only
    real, failed = fileops.write_atomic, []

    def write(target, data, durable=False):
        if Path(target).name == "filelistc.win32.bin" and not failed:
            failed.append(target)
            raise OSError(28, "No space left on device")
        return real(target, data, durable=durable)
    monkeypatch.setattr(fileops, "write_atomic", write)
    gf.settle(game)
    monkeypatch.setattr(fileops, "write_atomic", real)
    assert failed

    gf.settle(game)
    assert [r["name"] for r in gf.records(store(game)) if not r.get("off")] == ["Hair"]
    assert {read(game, C108_TRB, v) for v in "uc"} == {b"X" * 6000}


def test_a_mod_taken_off_while_the_backup_has_it_keeps_its_place(game, tmp_path):
    """the sectors it sat on stay taken, so nothing else can land there and pass for it"""
    import os
    a, b = os.urandom(6000), os.urandom(6000)
    put(game, mod(tmp_path, "Hair A", {C108: a}), name="Hair A")
    backup(game)
    gf.remove(game, store(game), "Hair A")
    put(game, mod(tmp_path, "Hair B", {C108: b}), name="Hair B")

    assert gf.settle(game) == ([], [])
    assert read(game, C108) == b
    assert states(game)["Hair B"] == "in"


def test_dropping_a_mod_again_after_taking_it_off_keeps_it_in(game, tmp_path):
    payload = b"X" * 5000
    put(game, mod(tmp_path, "Outfit", {C108: payload}), name="Outfit")
    backup(game)
    gf.remove(game, store(game), "Outfit")
    done = put(game, mod(tmp_path, "Outfit again", {C108: payload}), name="Outfit")

    assert done["files"] == 1 and not done.get("updated")
    gf.settle(game)
    assert read(game, C108, "u") == read(game, C108, "c") == payload


@pytest.mark.parametrize("order", [("top", "bottom"), ("bottom", "top")])
def test_stacked_mods_taken_off_then_restored_leave_nothing_behind(game, tmp_path, order):
    vanilla = read(game, C108)
    put(game, mod(tmp_path, "bottom", {C108: b"A" * 5000}), name="bottom")
    put(game, mod(tmp_path, "top", {C108: b"B" * 5000}), name="top")
    backup(game)
    for name in order:
        gf.remove(game, store(game), name)
    assert read(game, C108) == vanilla

    restore(game)
    gf.settle(game)
    assert read(game, C108) == vanilla


def test_a_third_version_over_two_on_record_comes_off_clean(game, tmp_path, monkeypatch):
    vanilla = read(game, C108)
    put(game, mod(tmp_path, "v1", {C108: b"1" * 5000}), name="X")
    real = gf._remove

    def fails(*a, **k):
        raise gf.FilesError("the old version didn't come off")

    monkeypatch.setattr(gf, "_remove", fails)
    assert "didn't all come off" in put(game, mod(tmp_path, "v2", {C108: b"2" * 5000}),
                                        name="X")["note"]
    monkeypatch.setattr(gf, "_remove", real)
    put(game, mod(tmp_path, "v3", {C108: b"3" * 5000}), name="X")
    assert read(game, C108) == b"3" * 5000

    gf.remove(game, store(game), "X")
    assert read(game, C108) == vanilla


def test_an_update_over_a_repacked_version_keeps_the_model_whole(game, tmp_path):
    put(game, mod(tmp_path, "v1", {C108: b"I1" * 3000, C108_TRB: b"T" * 6000}), name="Outfit")
    backup(game)
    repack(game, "u")
    repack(game, "c")

    done = put(game, mod(tmp_path, "v2", {C108: b"I2" * 3100, C108_TRB: b"T" * 6000}),
               name="Outfit")
    assert done["updated"]
    assert read(game, C108) == b"I2" * 3100
    assert read(game, C108_TRB) == b"T" * 6000


def test_a_mod_a_repack_moved_still_reads_as_the_drawers(game, tmp_path):
    from patcher.mods.hd_state import without_file_mods
    index = sys_of(game) / "filelistu.win32.bin"
    plain = without_file_mods(index)[C108]
    put(game, mod(tmp_path, "Big", {C108: b"B" * (plain + 9000)}), name="Big")
    repack(game, "u")

    assert gf.held(index) == {C108}
    assert without_file_mods(index)[C108] == plain
    gf.settle(game)
    item = gf.records(store(game))[0]["pairs"][0]["entries"][0]
    assert item["now"] == list(gf.entries(index)[C108].raw_fields)


def test_names_keep_what_tells_two_drops_apart(tmp_path):
    folder = tmp_path / "Better.Hair"
    folder.mkdir()
    assert gf.mod_name(tmp_path / "c108.win32.imgb") == "c108.win32.imgb"
    assert gf.mod_name(tmp_path / "c108.win32.trb") == "c108.win32.trb"
    assert gf.mod_name(folder) == "Better.Hair"
    assert gf.mod_name(tmp_path / "Serah Dress-55-1-2-1699999999.zip") == "Serah Dress"
    assert gf.mod_name(tmp_path / "Hair Pack v1.2.7z") == "Hair Pack"
    assert gf.mod_name(tmp_path / "Pack.tar.gz") == "Pack"


def test_an_update_after_the_backup_doesnt_leave_half_a_model_after_a_restore(game, tmp_path):
    """the shared mesh changed hands to the new version, which the backup never saw"""
    vanilla = (read(game, C108), read(game, C108_TRB))
    mesh = b"mesh v1 and v2 " * 500
    put(game, mod(tmp_path, "v1", {C108_TRB: mesh, C108: b"tex1" * 1500}), name="Outfit")
    backup(game)
    assert put(game, mod(tmp_path, "v2", {C108_TRB: mesh, C108: b"tex2" * 1500}),
               name="Outfit")["updated"]
    restore(game)

    said = gf.settled(game)
    assert (read(game, C108), read(game, C108_TRB)) == vanilla
    assert any("Outfit went in after the HD backup" in s for s in said), said


def test_a_mod_a_repack_left_half_of_comes_off_whole_with_the_one_over_it(game, tmp_path):
    """
    the repack dropped the .imgb under Hair and the folder it came from is
    gone, which leaves a .trb pointing at textures that aren't its own
    """
    import shutil
    vanilla = {v: (read(game, C108, v), read(game, C108_TRB, v)) for v in "uc"}
    backup(game)
    outfit = mod(tmp_path, "Outfit", {C108: b"O" * 5000, C108_TRB: b"T" * 6000})
    put(game, outfit, name="Outfit")
    shutil.rmtree(outfit)
    put(game, mod(tmp_path, "Hair", {C108: b"H" * 5000}), name="Hair")
    repack(game, "u")
    repack(game, "c")
    # no settle, the way a run that died before it leaves things: the
    # way down the stack is what finds out

    assert gf.remove(game, store(game), "Hair")["crossed"] == ["Outfit"]
    assert gf.records(store(game)) == []
    assert {v: (read(game, C108, v), read(game, C108_TRB, v)) for v in "uc"} == vanilla


def test_a_mod_a_repack_dropped_comes_back_out_of_its_own_download(game, tmp_path):
    backup(game)
    put(game, mod(tmp_path, "Outfit", {C108: b"O" * 5000, C108_TRB: b"T" * 6000}), name="Outfit")
    put(game, mod(tmp_path, "Hair", {C108: b"H" * 5000}), name="Hair")
    repack(game, "u")
    gf.settle(game)

    assert gf.remove(game, store(game), "Hair")["crossed"] == []
    assert read(game, C108) == b"O" * 5000 and read(game, C108_TRB) == b"T" * 6000
    assert states(game) == {"Outfit": "in"}


def test_one_a_restore_took_half_of_comes_off_with_another_still_over_it(game, tmp_path):
    """
    the update kept a file the backup has, so it's still in after the
    restore, sitting on a mod the restore took the rest of
    """
    shared = b"kept by the update " * 400
    vanilla = {p: read(game, p) for p in (C108, C108_TRB, N012)}
    put(game, mod(tmp_path, "v1", {N012: shared, C108: b"old" * 2000}), name="A")
    backup(game)
    put(game, mod(tmp_path, "C", {N012: b"C" * 5000, C108_TRB: b"T" * 6000}), name="C")
    put(game, mod(tmp_path, "v2", {N012: shared}), name="A")
    restore(game)

    assert gf.settle(game) == (["C"], ["A"])
    assert [r["name"] for r in gf.records(store(game)) if not r.get("off")] == ["A"]
    assert read(game, N012) == shared
    assert (read(game, C108), read(game, C108_TRB)) == (vanilla[C108], vanilla[C108_TRB])

    gf.remove(game, store(game), "A")
    assert {p: read(game, p) for p in vanilla} == vanilla


def test_revert_counts_a_mod_that_came_off_with_the_one_over_it(game, tmp_path):
    import shutil
    from patcher.revert import _files_off
    backup(game)
    outfit = mod(tmp_path, "Outfit", {C108: b"O" * 5000, C108_TRB: b"T" * 6000})
    put(game, outfit, name="Outfit")
    shutil.rmtree(outfit)
    put(game, mod(tmp_path, "Hair", {C108: b"H" * 5000}), name="Hair")
    repack(game, "u")
    # no settle, the way a run that died before it leaves things: the
    # way down the stack is what finds out

    results = {r.label: r for r in _files_off(game)}
    assert results["Hair"].ok and results["Outfit"].ok, results
    assert "came off with" in results["Outfit"].detail


def test_what_settle_couldnt_do_is_said(game, tmp_path, monkeypatch):
    put(game, mod(tmp_path, "Outfit", {C108: b"O" * 5000}), name="Outfit")
    backup(game)
    gf.remove(game, store(game), "Outfit")
    put(game, mod(tmp_path, "Ghost", {N012: b"G" * 5000}), name="Ghost")
    gf.remove(game, store(game), "Ghost")
    restore(game)

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(gf, "_remove", full)
    said = gf.settled(game)
    assert any("Outfit couldn't be put right" in s for s in said), said


def test_a_remove_cut_short_after_the_way_down_was_decided_is_finished_next_time(game, tmp_path,
                                                                               monkeypatch):
    """the mods passed over are on record before the index moves, so a second go knows them"""
    import shutil
    import patcher.fileops as fileops
    vanilla = {v: (read(game, C108, v), read(game, C108_TRB, v)) for v in "uc"}
    backup(game)
    outfit = mod(tmp_path, "Outfit", {C108: b"O" * 5000, C108_TRB: b"T" * 6000})
    put(game, outfit, name="Outfit")
    shutil.rmtree(outfit)
    put(game, mod(tmp_path, "Hair", {C108: b"H" * 5000}), name="Hair")
    repack(game, "u")
    repack(game, "c")
    # no settle, the way a run that died before it leaves things: the
    # way down the stack is what finds out

    real = fileops.write_atomic

    def no_index(target, data, durable=False):
        if Path(target).name.startswith("filelist"):
            raise OSError(28, "No space left on device")
        return real(target, data, durable=durable)

    monkeypatch.setattr(fileops, "write_atomic", no_index)
    with pytest.raises(OSError):
        gf.remove(game, store(game), "Hair")
    monkeypatch.setattr(fileops, "write_atomic", real)

    assert gf.remove(game, store(game), "Hair")["crossed"] == ["Outfit"]
    assert gf.records(store(game)) == []
    assert {v: (read(game, C108, v), read(game, C108_TRB, v)) for v in "uc"} == vanilla


def test_the_one_under_a_mod_half_taken_off_reads_as_in_and_comes_off(game, tmp_path,
                                                                      monkeypatch):
    """
    the top came off one voice set before the other failed: under it, where
    the game holds its file again, is what's in, whatever the record above says
    """
    import patcher.fileops as fileops
    vanilla = {v: read(game, C108, v) for v in "uc"}
    put(game, mod(tmp_path, "Under", {C108: b"U" * 5000}), name="Under")
    put(game, mod(tmp_path, "Over", {C108: b"V" * 5000}), name="Over")
    real, calls = fileops.write_atomic, []

    def second_index_fails(target, data, durable=False):
        if Path(target).name.startswith("filelist"):
            calls.append(target)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
        return real(target, data, durable=durable)

    monkeypatch.setattr(fileops, "write_atomic", second_index_fails)
    with pytest.raises(OSError):
        gf.remove(game, store(game), "Over")
    monkeypatch.setattr(fileops, "write_atomic", real)
    assert sorted(read(game, C108, v) for v in "uc") == [b"U" * 5000, b"V" * 5000]

    gf.remove(game, store(game), "Under")
    gf.remove(game, store(game), "Over")
    assert {v: read(game, C108, v) for v in "uc"} == vanilla
    assert gf.records(store(game)) == []


def test_one_kept_for_a_restore_on_an_updated_file_follows_it_to_the_new_version(game,
                                                                               tmp_path):
    """
    the update took the file over from the old version, so the one sitting on
    it has to sit on the new version, or a restore puts the old one's file back
    with nothing on record for it
    """
    vanilla = (read(game, C108), read(game, N012))
    put(game, mod(tmp_path, "v1", {C108: b"A" * 5000}), name="A")
    put(game, mod(tmp_path, "B", {C108: b"B" * 5000}), name="B")
    backup(game)
    gf.remove(game, store(game), "B")
    put(game, mod(tmp_path, "v2", {C108: b"A" * 5000, N012: b"X" * 4000}), name="A")
    gf.remove(game, store(game), "A")
    restore(game)

    gf.settle(game)
    assert (read(game, C108), read(game, N012)) == vanilla
    assert [r for r in gf.records(store(game)) if not r.get("off")] == []


def test_one_a_restore_left_under_another_with_its_file_nowhere_comes_off(game, tmp_path):
    """
    the restore put back the version the backup had, so what's under it now is
    what was under that one, and the mod on record in between is nowhere
    """
    import shutil
    vanilla = read(game, C108)
    put(game, mod(tmp_path, "v1", {C108: b"B" * 5000}), name="B")
    backup(game)
    hair = mod(tmp_path, "Hair", {C108: b"H" * 5000})
    put(game, hair, name="Hair")
    shutil.rmtree(hair)
    put(game, mod(tmp_path, "v2", {C108: b"B" * 5000}), name="B")
    restore(game)

    assert gf.settle(game)[0] == ["Hair"]
    assert read(game, C108) == b"B" * 5000
    gf.remove(game, store(game), "B")
    assert read(game, C108) == vanilla
    assert [r for r in gf.records(store(game)) if not r.get("off")] == []


def test_one_kept_for_a_restore_comes_off_onto_what_is_on_record_under_it_now(game, tmp_path):
    """
    the hair sat on the outfit's first texture; the outfit dropped the texture
    and an update brought the same one back. A restore puts the hair back on
    top, and taking it off again has to land on the texture, not the game's own
    """
    vanilla = (read(game, C108), read(game, N012))
    texture, mesh = b"T" * 5000, b"N" * 4000
    put(game, mod(tmp_path, "v1", {C108: texture, N012: mesh}), name="Outfit")
    put(game, mod(tmp_path, "Hair", {C108: b"H" * 5000}), name="Hair")
    backup(game)
    gf.remove(game, store(game), "Hair")
    put(game, mod(tmp_path, "v2", {N012: mesh}), name="Outfit")
    put(game, mod(tmp_path, "v3", {N012: mesh, C108: texture}), name="Outfit")
    restore(game)

    gf.settle(game)
    assert (read(game, C108), read(game, N012)) == (texture, mesh)
    assert [r["name"] for r in gf.records(store(game)) if not r.get("off")] == ["Outfit"]
    gf.remove(game, store(game), "Outfit")
    assert (read(game, C108), read(game, N012)) == vanilla



def test_an_empty_file_is_left_out_and_said(game, tmp_path):
    """0 bytes is how the index marks a placeholder, so in it would never come back off"""
    source = mod(tmp_path, "Half", {C108: b"", C108_TRB: b"T" * 6000})
    with gf.Source(source) as src:
        found = gf.look_in(src, gf.read_game(game))
    assert found.empty == [C108] and (C108, "") not in found.picks
    assert "1 empty file, which" in gf.left_out(found)
    with gf.Source(mod(tmp_path, "Empty", {C108: b""})) as src:
        found = gf.look_in(src, gf.read_game(game))
    assert "0 bytes" in gf.explain(found, gf.read_game(game), "Empty")


def test_an_optional_folder_inside_a_mod_is_a_version_laid_over_it(game, tmp_path):
    source = mod(tmp_path, "Outfit", {C108_TRB: b"T" * 6000, C108: b"I" * 4000,
                                      f"Optional - no cape/{C108}": b"N" * 4000})
    with gf.Source(source) as src:
        found = gf.look_in(src, gf.read_game(game), root="Outfit")
    assert found.versions == ["Optional - no cape", "Outfit"]

    put(game, source, version="Optional - no cape")
    assert (read(game, C108_TRB), read(game, C108)) == (b"T" * 6000, b"N" * 4000)
    put(game, source, version="Outfit")
    assert (read(game, C108_TRB), read(game, C108)) == (b"T" * 6000, b"I" * 4000)


def test_a_version_missing_half_of_a_model_another_one_has_is_refused(game, tmp_path):
    vanilla = (read(game, C108_TRB), read(game, C108))
    source = mod(tmp_path, "Outfit", {f"Main/{C108_TRB}": b"T" * 6000, f"Main/{C108}": b"I" * 4000,
                                      f"Optional/{C108}": b"N" * 4000})
    with pytest.raises(gf.FilesError, match="install that one first"):
        put(game, source, version="Optional")
    assert (read(game, C108_TRB), read(game, C108)) == vanilla
    put(game, source, version="Main")
    assert (read(game, C108_TRB), read(game, C108)) == (b"T" * 6000, b"I" * 4000)


def test_a_zip_listing_a_file_twice_takes_one_copy_or_says_so(game, tmp_path):
    import zipfile
    twice = tmp_path / "Twice.zip"
    with zipfile.ZipFile(twice, "w") as archive:
        archive.writestr(C108_TRB, b"T" * 6000)
        archive.writestr(C108, b"I" * 4000)
        archive.writestr(C108, b"I" * 4000)
    assert put(game, twice)["files"] == 2

    differ = tmp_path / "Differ.zip"
    with zipfile.ZipFile(differ, "w") as archive:
        archive.writestr(C108_TRB, b"U" * 6000)
        archive.writestr(C108, b"A" * 4000)
        archive.writestr(C108, b"B" * 4000)
    with gf.Source(differ) as src:
        found = gf.look_in(src, gf.read_game(game))
    assert found.doubled == [C108] and "twice" in gf.left_out(found)
    with pytest.raises(gf.FilesError, match="lists c108.win32.imgb twice"):
        put(game, differ)


def test_the_folder_dropped_says_which_voice_set_and_which_game(game, tmp_path):
    japanese = mod(tmp_path / "Serah", "white_imgc", {C108: b"J" * 4000})
    english = read(game, C108, "u")
    put(game, japanese)
    assert read(game, C108, "c") == b"J" * 4000 and read(game, C108, "u") == english
    assert gf.records(store(game))[0]["name"] == "Serah"

    xiii = mod(tmp_path, "white_data", {C108: b"X" * 4000})
    with gf.Source(xiii) as src:
        found = gf.look_in(src, gf.read_game(game))
    assert not found.picks and gf.wrong_game(found, gf.read_game(game)) == "FINAL FANTASY XIII"


def test_a_zip_that_never_finished_downloading_says_so(tmp_path):
    broken = tmp_path / "Dress.zip"
    broken.write_bytes(b"PK\x03\x04 cut off here")
    with pytest.raises(gf.FilesError, match="finished downloading"):
        gf.Source(broken)


def test_an_update_with_only_the_texture_keeps_the_last_versions_mesh(game, tmp_path):
    """v1.1 ships the one file that changed, the way unpacking it over v1.0 would go"""
    vanilla = (read(game, C108_TRB), read(game, C108))
    put(game, mod(tmp_path, "v1.0", {C108_TRB: b"T" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")
    done = put(game, mod(tmp_path, "v1.1", {C108: b"J" * 4000}), name="Serah Dress")

    assert done["updated"] and done["kept"] == ["c108.win32.trb"]
    assert (read(game, C108_TRB), read(game, C108)) == (b"T" * 6000, b"J" * 4000)
    assert [r["name"] for r in gf.records(store(game))] == ["Serah Dress"]
    gf.remove(game, store(game), "Serah Dress")
    assert (read(game, C108_TRB), read(game, C108)) == vanilla


def test_half_a_model_is_refused_per_voice_set(game, tmp_path):
    vanilla = read(game, C108_TRB, "c")
    source = mod(tmp_path, "Both Sets", {f"_white_imgu.win32.bin/{C108_TRB}": b"T" * 6000,
                                         f"_white_imgu.win32.bin/{C108}": b"I" * 4000,
                                         f"_white_imgc.win32.bin/{C108_TRB}": b"J" * 6000})
    with pytest.raises(gf.FilesError, match="only for the English voice set"):
        put(game, source)
    assert read(game, C108_TRB, "c") == vanilla

    (source / f"_white_imgc.win32.bin/{C108}").parent.mkdir(parents=True, exist_ok=True)
    (source / f"_white_imgc.win32.bin/{C108}").write_bytes(b"")
    with pytest.raises(gf.FilesError, match="is empty"):
        put(game, source)


def test_an_update_keeps_the_last_versions_texture_under_another_mod(game, tmp_path):
    """a recolor over the costume, then a costume update with only the mesh"""
    vanilla = (read(game, C108_TRB), read(game, C108))
    put(game, mod(tmp_path, "v1.0", {C108_TRB: b"T" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")
    put(game, mod(tmp_path, "Recolor", {C108: b"R" * 4000}))
    done = put(game, mod(tmp_path, "v1.1", {C108_TRB: b"U" * 6000}), name="Serah Dress")

    assert done["kept"] == ["c108.win32.imgb"]
    assert (read(game, C108_TRB), read(game, C108)) == (b"U" * 6000, b"R" * 4000)
    gf.remove(game, store(game), "Recolor")
    assert (read(game, C108_TRB), read(game, C108)) == (b"U" * 6000, b"I" * 4000)
    gf.remove(game, store(game), "Serah Dress")
    assert (read(game, C108_TRB), read(game, C108)) == vanilla
    assert not gf.records(store(game))


def test_a_kept_texture_a_repack_dropped_comes_back_out_of_the_old_download(game, tmp_path):
    put(game, mod(tmp_path, "v1.0", {C108_TRB: b"T" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")
    put(game, mod(tmp_path, "Recolor", {C108: b"R" * 4000}))
    put(game, mod(tmp_path, "v1.1", {C108_TRB: b"U" * 6000}), name="Serah Dress")
    repack(game, "u")

    # an HD run's settle: still findable, so it stays
    assert gf.settle(game) == ([], [])
    gf.remove(game, store(game), "Recolor")
    assert (read(game, C108_TRB), read(game, C108)) == (b"U" * 6000, b"I" * 4000)


def test_an_update_leaves_a_recolor_on_a_file_it_didnt_change(game, tmp_path):
    put(game, mod(tmp_path, "v1.0", {C108_TRB: b"T" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")
    put(game, mod(tmp_path, "Recolor", {C108: b"R" * 4000}))
    put(game, mod(tmp_path, "v1.1", {C108_TRB: b"U" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")

    assert (read(game, C108_TRB), read(game, C108)) == (b"U" * 6000, b"R" * 4000)
    gf.remove(game, store(game), "Recolor")
    assert (read(game, C108_TRB), read(game, C108)) == (b"U" * 6000, b"I" * 4000)


def test_a_file_an_update_took_over_comes_back_out_of_its_own_download(game, tmp_path):
    import shutil
    v10 = mod(tmp_path, "v1.0", {C108_TRB: b"T" * 6000, C108: b"I" * 4000})
    put(game, v10, name="Serah Dress")
    put(game, mod(tmp_path, "v1.1", {C108_TRB: b"U" * 6000}), name="Serah Dress")
    put(game, mod(tmp_path, "v1.2", {C108_TRB: b"V" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")
    shutil.rmtree(v10)
    put(game, mod(tmp_path, "Recolor", {C108: b"R" * 4000}))
    repack(game, "u")

    gf.remove(game, store(game), "Recolor")
    assert (read(game, C108_TRB), read(game, C108)) == (b"V" * 6000, b"I" * 4000)


class Cut(BaseException):
    """a power cut, which nothing gets to clean up after"""


def cut_at(game, n, fn) -> list:
    """fn() with its n-th write never landing, and the game left as the disk had it then"""
    import shutil
    import patcher.fileops as fileops

    real, seen, snap = fileops.write_atomic, [], game.parent / "at-the-cut"

    def write(target, data, durable=False):
        seen.append(Path(target).name)
        if len(seen) == n:
            shutil.copytree(game, snap)
            raise Cut()
        return real(target, data, durable=durable)

    fileops.write_atomic = write
    try:
        fn()
    except Cut:
        pass
    finally:
        fileops.write_atomic = real
    assert len(seen) >= n, seen
    shutil.rmtree(game)
    shutil.copytree(snap, game)
    shutil.rmtree(snap)
    gf._contents.clear()
    return seen[:n]


def recolored(game, tmp_path) -> tuple:
    vanilla = (read(game, C108_TRB), read(game, C108))
    put(game, mod(tmp_path, "v1.0", {C108_TRB: b"T" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")
    put(game, mod(tmp_path, "Recolor", {C108: b"R" * 4000}))
    return vanilla, mod(tmp_path, "v1.1", {C108_TRB: b"U" * 6000})


def test_an_update_cut_short_then_dropped_again_keeps_the_recolor_on_top(game, tmp_path):
    vanilla, v11 = recolored(game, tmp_path)
    # the Recolor moving onto the new version is the write that never lands
    assert cut_at(game, 4, lambda: put(game, v11, name="Serah Dress"))[-1].endswith(
        "-Recolor.json")
    assert states(game)["Recolor"] == "in"

    put(game, v11, name="Serah Dress")
    assert states(game) == {"Recolor": "in", "Serah Dress": "in"}
    gf.remove(game, store(game), "Recolor")
    assert (read(game, C108_TRB), read(game, C108)) == (b"U" * 6000, b"I" * 4000)
    gf.remove(game, store(game), "Serah Dress")
    assert (read(game, C108_TRB), read(game, C108)) == vanilla


def test_a_remove_cut_short_keeps_what_sat_on_it_on_top(game, tmp_path):
    vanilla, v11 = recolored(game, tmp_path)
    put(game, v11, name="Serah Dress")
    # the Recolor handed down, then the index that never lands
    seen = cut_at(game, 2, lambda: gf.remove(game, store(game), "Serah Dress"))
    assert seen[0].endswith("-Recolor.json") and seen[1] == "filelistu.win32.bin"

    assert read(game, C108) == b"R" * 4000 and states(game)["Recolor"] == "in"
    gf.remove(game, store(game), "Recolor")
    gf.remove(game, store(game), "Serah Dress")
    assert (read(game, C108_TRB), read(game, C108)) == vanilla
    assert not gf.records(store(game))


def twinned(game, tmp_path):
    """a costume, and another mod with the very same texture on top of it"""
    put(game, mod(tmp_path, "Dress", {C108_TRB: b"T" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")
    put(game, mod(tmp_path, "Twin", {C108: b"I" * 4000}))


def test_a_twin_on_a_mod_whose_remove_got_stuck_comes_off_onto_what_was_under(game, tmp_path):
    vanilla = {v: (read(game, C108_TRB, v), read(game, C108, v)) for v in "uc"}
    twinned(game, tmp_path)
    # an English set repacked with no backup has lost its original trb, which
    # the Japanese set still has
    repack(game, "u")
    assert not gf.remove(game, store(game), "Serah Dress")["stuck"]
    gf.remove(game, store(game), "Twin")
    assert {v: (read(game, C108_TRB, v), read(game, C108, v)) for v in "uc"} == vanilla


def test_a_twin_on_a_mod_whose_remove_was_cut_short_comes_off_onto_what_was_under(game,
                                                                                  tmp_path):
    vanilla = read(game, C108)
    twinned(game, tmp_path)
    # cut short once the English set is done
    seen = cut_at(game, 3, lambda: gf.remove(game, store(game), "Serah Dress"))
    assert seen[1] == "filelistu.win32.bin" and seen[2].endswith("-Twin.json")
    gf.remove(game, store(game), "Twin")
    assert read(game, C108) == vanilla


def test_a_mod_over_the_same_file_comes_off_and_leaves_it_after_a_repack(game, tmp_path):
    """a repack from outside the app, so nothing has put down where it moved them"""
    import shutil
    twinned(game, tmp_path)
    # and nothing to fall back on but the game itself
    shutil.rmtree(tmp_path / "Dress")
    shutil.rmtree(tmp_path / "Twin")
    repack(game, "u")

    gf.remove(game, store(game), "Twin")
    assert (read(game, C108_TRB), read(game, C108)) == (b"T" * 6000, b"I" * 4000)
    assert states(game) == {"Serah Dress": "in"}


def test_a_twin_comes_off_whole_from_a_pack_stuck_on_another_model(game, tmp_path):
    vanilla = {v: (read(game, C108_TRB, v), read(game, C108, v)) for v in "uc"}
    put(game, mod(tmp_path, "Pack", {C108_TRB: b"T" * 6000, C108: b"I" * 4000,
                                     N012: b"K" * 3000}))
    put(game, mod(tmp_path, "Twin", {C108: b"I" * 4000}))
    # the English set's original n012 written over, so Remove Pack ends stuck
    # on that one, with its c108 already off
    pack = gf.records(store(game))[0]
    spot = next(i["was"] for s in pack["pairs"] if s["index"].endswith("filelistu.win32.bin")
                for i in s["entries"] if i["path"] == N012)
    with (sys_of(game) / "white_imgu.win32.bin").open("r+b") as handle:
        handle.seek(spot[0] * 2048)
        handle.write(b"\xff" * spot[2])
    gf._contents.clear()
    # which the Japanese set still has
    assert not gf.remove(game, store(game), "Pack")["stuck"]

    gf.remove(game, store(game), "Twin")
    assert {v: (read(game, C108_TRB, v), read(game, C108, v)) for v in "uc"} == vanilla


def stuck_update(game, tmp_path, monkeypatch) -> None:
    """an update whose old version didn't come off, a full disk say"""
    put(game, mod(tmp_path, "v1.0", {C108_TRB: b"T" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")
    real = gf._remove

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(gf, "_remove", full)
    done = put(game, mod(tmp_path, "v1.1", {C108_TRB: b"U" * 6000}), name="Serah Dress")
    assert "didn't all come off" in done["note"]
    monkeypatch.setattr(gf, "_remove", real)


def test_an_old_version_finished_off_later_isnt_said_to_have_come_off(game, tmp_path,
                                                                      monkeypatch):
    from patcher.bridge import _files_said
    stuck_update(game, tmp_path, monkeypatch)

    # whatever runs next finishes it, and has nothing to say about it
    said = _files_said(put(game, mod(tmp_path, "Hair", {N012: b"H" * 3000})), "Hair")
    assert "Serah Dress" not in said
    assert [r["name"] for r in gf.records(store(game))] == ["Serah Dress", "Hair"]
    assert (read(game, C108_TRB), read(game, C108)) == (b"U" * 6000, b"I" * 4000)


def test_an_old_version_finished_off_by_a_remove_isnt_counted_in_it(game, tmp_path,
                                                                     monkeypatch):
    put(game, mod(tmp_path, "Hair", {N012: b"H" * 3000}))
    stuck_update(game, tmp_path, monkeypatch)

    done = gf.remove(game, store(game), "Hair")
    assert (done["files"], done["handed"], done["crossed"]) == (2, 0, [])
    assert [r["name"] for r in gf.records(store(game))] == ["Serah Dress"]


@pytest.mark.parametrize("n", [7, 8, 9])
def test_an_old_version_left_half_off_still_comes_off_after_a_restore(game, tmp_path, n):
    vanilla = (read(game, C108_TRB), read(game, C108))
    put(game, mod(tmp_path, "v1.0", {C108_TRB: b"T" * 6000, C108: b"I" * 4000}),
        name="Serah Dress")
    backup(game)
    # the update's in, and taking the old version off is what's cut short
    cut_at(game, n, lambda: put(game, mod(tmp_path, "v1.1", {C108_TRB: b"U" * 6000}),
                                name="Serah Dress"))
    restore(game)
    gf.settle(game)
    assert {(read(game, C108_TRB, v), read(game, C108, v)) for v in "uc"} == {vanilla}


def test_a_file_taken_over_where_it_sits_keeps_the_download_it_came_out_of(game, tmp_path):
    import shutil
    backup(game)
    v10 = tmp_path / "Serah Dress v1.0.zip"
    with zipfile.ZipFile(v10, "w") as archive:
        archive.writestr(C108_TRB, b"T" * 6000)
        archive.writestr(C108, b"I" * 4000)
    put(game, v10, name="Serah Dress")
    put(game, mod(tmp_path, "Recolor", {C108: b"R" * 4000}))
    # v1.1 came as a .7z, unpacked to a folder that's gone again
    unpacked = mod(tmp_path / "unpacked", "Serah Dress",
                   {C108_TRB: b"U" * 6000, C108: b"I" * 4000})
    put(game, unpacked, name="Serah Dress", origin=str(tmp_path / "Serah Dress v1.1.7z"))
    shutil.rmtree(unpacked)
    repack(game, "u")

    assert gf.settled(game) == []
    assert states(game) == {"Recolor": "in", "Serah Dress": "in"}


@pytest.mark.parametrize("n", [4, 5, 6])
def test_an_update_cut_short_then_settled_leaves_one_whole_version(game, tmp_path, n):
    """before either index, between the two, and before the old version lets go"""
    old, new = (b"T" * 6000, b"I" * 4000), (b"U" * 6000, b"I" * 4000)
    put(game, mod(tmp_path, "v1.0", {C108_TRB: old[0], C108: old[1]}), name="Serah Dress")
    cut_at(game, n, lambda: put(game, mod(tmp_path, "v1.1", {C108_TRB: new[0]}),
                                name="Serah Dress"))

    assert gf.settled(game) == []
    assert {(read(game, C108_TRB, v), read(game, C108, v)) for v in "uc"} in ({old}, {new})
    assert states(game) == {"Serah Dress": "in"}


def test_a_file_listed_twice_in_a_folder_of_its_own_is_still_seen(game, tmp_path):
    import zipfile
    dup = tmp_path / "Dup.zip"
    with zipfile.ZipFile(dup, "w") as archive:
        archive.writestr("Dup/c108.win32.trb", b"T" * 6000)
        archive.writestr("Dup/c108.win32.imgb", b"A" * 4000)
        archive.writestr("Dup/c108.win32.imgb", b"B" * 4000)
    with pytest.raises(gf.FilesError, match="twice"):
        put(game, dup)


def test_two_mods_with_one_name_and_nothing_in_common_are_two_mods(game, tmp_path):
    put(game, mod(tmp_path / "one", "Pack", {C108: b"A" * 4000}))
    done = put(game, mod(tmp_path / "two", "Pack", {N012: b"B" * 4000}))

    assert done["name"] == "Pack (2)" and not done.get("updated")
    assert read(game, C108) == b"A" * 4000 and read(game, N012) == b"B" * 4000
    assert gf.mod_name(mod(tmp_path / "three", "Serah Dress", {f"chr/{C108}": b"x"}) / "chr") \
        == "Serah Dress"


def test_the_second_of_two_namesakes_gets_its_own_updates(game, tmp_path):
    put(game, mod(tmp_path / "one", "Pack", {C108_TRB: b"T" * 6000, C108: b"A" * 4000}))
    put(game, mod(tmp_path / "two", "Pack", {N012: b"B" * 4000}))
    # pack.zip is Pack.zip on Windows
    done = put(game, mod(tmp_path / "three", "Pack", {N012: b"C" * 4000}), name="pack")

    assert done["name"] == "Pack (2)" and done["updated"]
    assert sorted(r["name"] for r in gf.records(store(game))) == ["Pack", "Pack (2)"]
    assert (read(game, C108), read(game, N012)) == (b"A" * 4000, b"C" * 4000)

    # sharing files with both, it's the one it has more of
    done = put(game, mod(tmp_path / "four", "Pack", {C108_TRB: b"U" * 6000, C108: b"D" * 4000,
                                                     N012: b"E" * 4000}))
    assert done["name"] == "Pack" and done["updated"]
    assert sorted(r["name"] for r in gf.records(store(game))) == ["Pack", "Pack (2)"]
    gf.remove(game, store(game), "Pack")
    assert read(game, N012) == b"C" * 4000


def test_a_second_download_of_the_same_zip_is_the_same_mod(tmp_path):
    assert gf.mod_name(tmp_path / "Serah Dress-55-1-2-1699999999 (1).zip") == "Serah Dress"
    assert gf.mod_name(mod(tmp_path, "Pack - Copy", {C108: b"x"})) == "Pack"
    assert gf.mod_name(tmp_path / "Pack (1) - Copy.zip") == "Pack"
    assert gf.mod_name(tmp_path / "Outfit (2013).zip") == "Outfit (2013)"
    assert gf.mod_name(tmp_path / "Carbon-Copy.zip") == "Carbon-Copy"


def test_a_folder_named_after_the_model_goes_in_by_name(game, tmp_path):
    import zipfile
    costume = tmp_path / "Serah Costume.zip"
    with zipfile.ZipFile(costume, "w") as archive:
        archive.writestr("c108/c108.win32.trb", b"T" * 6000)
        archive.writestr("c108/c108.win32.imgb", b"I" * 4000)
    assert put(game, costume)["files"] == 2
    assert read(game, C108_TRB) == b"T" * 6000


def test_main_then_optional_is_the_way_an_optional_over_main_goes_in(game, tmp_path):
    source = mod(tmp_path, "Outfit", {f"Main/{C108_TRB}": b"T" * 6000, f"Main/{C108}": b"I" * 4000,
                                      f"Optional/{C108}": b"N" * 4000})
    put(game, source, version="Main")
    done = put(game, source, version="Optional")

    assert done["kept"] == ["c108.win32.trb"]
    assert (read(game, C108_TRB), read(game, C108)) == (b"T" * 6000, b"N" * 4000)


def test_an_empty_half_of_a_model_is_said_to_be_empty(game, tmp_path):
    with pytest.raises(gf.FilesError, match="is empty"):
        put(game, mod(tmp_path, "Half", {C108_TRB: b"T" * 6000, C108: b""}))


def restarted():
    """what a fresh run of the app has: nothing hashed in memory"""
    gf._contents.clear()
    gf._recalled.clear()
    gf._kept.clear()


def hashing(monkeypatch) -> list:
    """every entry read and hashed, as it happens"""
    seen, real = [], gf._sha_at
    monkeypatch.setattr(gf, "_sha_at", lambda handle, entry: seen.append(entry.path)
                        or real(handle, entry))
    return seen


def test_the_first_look_after_a_start_reads_what_the_last_run_hashed(game, tmp_path,
                                                                   monkeypatch):
    """
    the hashes go beside the records once an install has them, so a restart
    doesn't read every drawer mod's files again to say where each stands
    """
    put(game, mod(tmp_path, "Serah Dress", {C108_TRB: b"T" * 6000, C108: b"I" * 4000}))
    put(game, mod(tmp_path, "Recolor", {C108: b"R" * 4000}))
    assert (store(game) / gf.RECORDS / gf.CONTENTS).is_file()
    before = states(game)
    restarted()
    seen = hashing(monkeypatch)

    assert states(game) == before == {"Serah Dress": "in", "Recolor": "in"}
    assert seen == [], f"read again after a start: {seen}"


def test_an_archive_written_since_is_hashed_again(game, tmp_path, monkeypatch):
    """the same rule as in memory: a write anywhere in it changes its stamp"""
    put(game, mod(tmp_path, "Serah Dress", {C108: b"I" * 4000}))
    restarted()
    archive = sys_of(game) / "white_imgu.win32.bin"
    with archive.open("ab") as handle:
        handle.write(b"\0" * 2048)
    seen = hashing(monkeypatch)

    assert states(game) == {"Serah Dress": "in"}
    assert seen, "took an archive that's been written to at its old word"


def test_a_write_inside_an_archive_takes_its_hashes_off_the_disk(game, tmp_path):
    """Leviathan's Tears writes in place, at the size the archive had"""
    put(game, mod(tmp_path, "Serah Dress", {C108: b"I" * 4000}))
    kept = store(game) / gf.RECORDS / gf.CONTENTS
    assert kept.is_file()

    gf.forget_contents(sys_of(game) / "white_imgu.win32.bin")

    assert not kept.exists()


def test_a_damaged_cache_costs_a_look_not_a_wrong_answer(game, tmp_path, monkeypatch):
    put(game, mod(tmp_path, "Serah Dress", {C108: b"I" * 4000}))
    kept = store(game) / gf.RECORDS / gf.CONTENTS
    for junk in (b"{not json", b'{"version": 1, "archives": {"x": {"stamp": 3}}}',
                 b'{"version": 1, "archives": []}'):
        kept.write_bytes(junk)
        restarted()
        assert states(game) == {"Serah Dress": "in"}



def test_only_a_zip_whose_end_record_names_another_disk_is_one_part_of_several():
    import io
    import struct

    from tests.helpers import make_split_zip, make_zip_bytes

    plain = make_zip_bytes({"a.txt": b"a" * 100})
    end = plain.rindex(b"PK\x05\x06")
    count, size, start = struct.unpack_from("<HII", plain, end + 10)
    # zip64's end record and locator, the plain record's disk fields left at 0xFFFF the
    # way APPNOTE says a field too small for its value is
    wide = struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, count, count, size, start)
    locator = struct.pack("<IIQI", 0x07064B50, 0, end, 1)
    zip64 = plain[:end] + wide + locator + struct.pack(
        "<IHHHHIIH", 0x06054B50, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    assert not gf.spanned(io.BytesIO(plain)) and not gf.spanned(io.BytesIO(zip64))
    assert gf.spanned(io.BytesIO(make_split_zip({"a.txt": b"a" * 100})["pack.zip"]))
