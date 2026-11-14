"""
The install pipeline, end to end, with stubbed tools.

The order is the design. The backup goes in before a single byte of the game is
touched, so every later step has something to fall back to, and the tests below
pull the plug at each stage to check that holds.
"""

import shutil
import sys
import threading
from pathlib import Path

import pytest

from tests.conftest import Log

from patcher.mods import hd_install, hd_runner
from patcher.mods.hd_install import (InstallError, archive_pair, back_up,
                                     build_plan, find_variant, install,
                                     mark_written, members_for, prepare, restore)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data" / "hd"

CATEGORIES = [
    {"id": "npcs", "label": "NPCs", "list": "LoadHDNPCs.txt"},
    {"id": "eidolons", "label": "Eidolons", "list": "LoadHDEidolons.txt"},
    {"id": "pcs", "label": "Playable Characters", "list": "LoadHDPCs.tx1"},
]


def make_game(tmp_path, variant="u", size=4096):
    """
    a game with a real index in it.

    The placeholder it used to write was fine while nothing read the file and
    useless the moment something did, which is now the size survey, the applied
    check, the free space estimate and the extraction stub. Built from the
    fixture's own lists, so whatever a test selects, the index holds it.
    """
    from tests.helpers import index_entries, write_scr_pair

    install_dir = tmp_path / "FINAL FANTASY XIII"
    sys_dir = install_dir / "white_data" / "sys"
    sys_dir.mkdir(parents=True)
    write_scr_pair(sys_dir / f"filelist_scr{variant}.win32.bin",
                   sys_dir / f"white_scr{variant}.win32.bin", index_entries(DATA), size=size)
    return install_dir


def make_mod(tmp_path, monkeypatch, tools=None):
    root = tmp_path / "HDProject"
    support = root / "SupportFiles"
    data = root / "Data"
    data.mkdir(parents=True)
    support.mkdir(parents=True)
    for name in ("LoadHDEidolons.txt", "LoadHDPCs.tx1", "n910.txt", "c001.txt"):
        shutil.copy(DATA / name, data / name)
    (data / "LoadHDNPCs.txt").write_text("#:NPCs\nn n910\n", encoding="utf-8")

    if sys.platform != "win32":
        monkeypatch.setitem(hd_runner.TOOLS, "pack", "ff13tool")
        monkeypatch.setitem(hd_runner.TOOLS, "models", "FFXIII_ModelsHD")
        monkeypatch.setitem(hd_runner.TOOLS, "inject", "FFXIII_HDProject")

    from tests.test_hd_runner import stub
    bodies = tools or {}
    for key in ("pack", "models", "inject"):
        name = hd_runner.TOOLS[key]
        stub(support, name, bodies.get(key, DEFAULT_TOOLS[key]))
    return root, data, support


# Stand-ins for the three tools, shaped like the real pipeline now that the
# batch files have been read.
#
# ff13tool -x builds the extracted tree from the mod's own LoadHD lists, so
# whatever categories a test selects, the tree holds the files those ids need.
# That keeps the fixture honest without hardcoding a set of ids beside every
# test.
FF13TOOL = 'REPO = ' + repr(str(REPO)) + r"""
import pathlib, sys, zlib
sys.path.insert(0, REPO)
from patcher.archive.filelist import load
from tests.helpers import pack, write_index

def here(text):
    return pathlib.Path(str(text).replace(chr(92), '/'))

mode = sys.argv[1]
print('Game set to FF13.', flush=True)

if mode == '-x':
    # every entry the index lists, at the size it claims, into a folder named
    # after the blob. That is what the real tool does and it is the only thing
    # that makes an untouched entry still look untouched afterwards.
    index, blob = here(sys.argv[3]), here(sys.argv[4])
    out = blob.parent / blob.name.split('.')[0]
    made = 0
    for entry in load(index).entries():
        target = out / here(entry.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'V' * max(entry.uncompressed, 1))
        made += 1
        print('Extracted ' + target.as_posix() + ' (uncompressed)', flush=True)
    print('File list processed. ' + str(made) + ' files listed in 2 file chunks.', flush=True)
    print('Extracted ' + str(made) + ' files in total.', flush=True)

elif mode == '-c':
    # a REAL pair, rebuilt from the tree, because the size survey, the applied
    # check and the read-back before the copy all look inside it. Entries the
    # tools touched are bigger, which is what installing an HD texture does.
    from tests.helpers import pack_plain
    index, tree = here(sys.argv[2]), here(sys.argv[3])
    rows = [(path.relative_to(tree).as_posix(), path.read_bytes())
            for path in sorted(tree.rglob('*')) if path.is_file()]
    pack_plain(pathlib.Path(tree.name + '.win32.bin'), index, rows)

elif mode == '-i':
    # one file into an existing pair, laid out for real: every payload read
    # back out, the named one replaced, the whole pair written again.
    #
    # It used to synthesise the index instead, appending to the blob and
    # writing offsets that pointed nowhere near the data. Everything that only
    # reads SIZES out of an index was happy with that, which is most of this
    # suite, and anything that reads an entry's CONTENT could not parse the
    # result at all. That hid the half written pair check completely.
    index, blob, wanted = here(sys.argv[2]), here(sys.argv[3]), here(sys.argv[4])
    if not wanted.is_file():
        print('Could not open ' + wanted.as_posix(), file=sys.stderr, flush=True)
        sys.exit(2)
    key = wanted.as_posix()
    raw = blob.read_bytes()
    rows = []
    for e in load(index).entries():
        if e.path.replace(chr(92), '/') == key or e.is_stub:
            continue
        payload = raw[e.byte_offset:e.byte_offset + e.stored]
        if e.is_compressed:
            payload = zlib.decompress(payload)
        rows.append((e.path, payload))
    rows.append((key.replace('/', chr(92)), wanted.read_bytes()))
    pack(blob, index, rows)
    # with their arguments on them, which is what the real tool prints and
    # what made a bare-word check pass here and fail on a live run
    print('Added ' + wanted.name + ' (copied)', flush=True)
    print('Updated ' + blob.as_posix(), flush=True)
    print('Imported one file to ' + blob.as_posix(), flush=True)
    print('Wrote ' + index.as_posix(), flush=True)
sys.exit(1)
"""

# both tools read models\orig and write models\mod, and neither puts the
# result anywhere the repack would look. The runner's copies do that, which is
# GENERATESINGLE's loadcopy=1 branch and the half that was missing.
#
# Driven by the arguments rather than by a guess at the shape, because the
# same two executables do the chr work and the zone work and the only thing
# that differs is the paths they are handed.
WRITES_TO_MOD = r"""
import hashlib, pathlib, sys

# an HD file is bigger than the one it replaces, and the archive stores
# payloads compressed, so the bulk has to be incompressible or the entry comes
# out the same size it went in and nothing downstream can tell an installed
# zone from a vanilla one. sha256 output is the cheapest way to get that
# deterministically.
def bulk(seed, want=6000):
    out, block = bytearray(), hashlib.sha256(seed).digest()
    while len(out) < want:
        out += block
        block = hashlib.sha256(block).digest()
    return bytes(out[:want])

def here(text):
    return pathlib.Path(text.replace('\\', '/').strip('/'))
def emit(src, dst):
    if not src.is_dir():
        print('Unable to open the .trb file.  Exiting.', flush=True)
        return 0
    if not dst.is_dir():
        # the real one writes into this folder and does not create it, which is
        # what killed a live run on its first block. A stub that mkdir's here
        # is a stub that cannot reproduce the bug the seed step exists for.
        print('Unable to open output files ' + dst.as_posix() + '.  Exiting.', flush=True)
        raise SystemExit(4294967292)
    made = 0
    for path in sorted(src.iterdir()):
        if path.is_file():
            (dst / path.name).write_bytes(path.read_bytes() + b'HD' + bulk(path.name.encode()))
            made += 1
    return made
"""

INJECT = WRITES_TO_MOD + r"""
# argv is (<id>.win32, models\orig\...\bin\, models\mod\...\bin\, <group>\)
sys.exit(3 if emit(here(sys.argv[2]), here(sys.argv[3])) else 0)
"""

MODELS = WRITES_TO_MOD + r"""
# the recipe names the three paths it works on, seven header lines down, and
# they are relative to models\orig and models\mod. That is true of a chr
# recipe and a zone block recipe alike.
lines = here(sys.argv[1]).read_text(encoding="utf-8").splitlines()
base = lines[7] if len(lines) > 8 else ''
if not base:
    sys.exit(2)
src = pathlib.Path('models/orig') / here(base).parent
dst = pathlib.Path('models/mod') / here(base).parent
sys.exit(0 if emit(src, dst) else 2)
"""

DEFAULT_TOOLS = {"pack": FF13TOOL, "models": MODELS, "inject": INJECT}


def test_the_variant_comes_from_what_is_on_disk(tmp_path):
    assert find_variant(make_game(tmp_path, "u")) == "u"
    assert find_variant(make_game(tmp_path / "jp", "c")) == "c"

    bare = tmp_path / "empty"
    (bare / "white_data" / "sys").mkdir(parents=True)
    with pytest.raises(InstallError, match="does not look like"):
        find_variant(bare)


def test_a_half_present_pair_is_not_a_variant(tmp_path):
    """one of the two is not an install, it is a broken one"""
    install_dir = tmp_path / "game"
    sys_dir = install_dir / "white_data" / "sys"
    sys_dir.mkdir(parents=True)
    (sys_dir / "filelist_scru.win32.bin").write_bytes(b"x")

    with pytest.raises(InstallError):
        find_variant(install_dir)


def test_prepare_writes_nothing(tmp_path):
    game = make_game(tmp_path)
    before = {p: p.read_bytes() for p in (game / "white_data" / "sys").iterdir()}

    prep = prepare(game, tmp_path, tmp_path / "store")

    assert prep.variant == "u"
    assert prep.needed_bytes > 20 * 1024 ** 3, "the mod's own 20GB warning is not accounted for"
    for path, content in before.items():
        assert path.read_bytes() == content
    assert not (tmp_path / "store").exists()


def test_the_backup_is_taken_before_anything_else(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    vanilla = {p.name: p.read_bytes() for p in archive_pair(game, "u")}

    order = []
    real_copy = shutil.copyfile
    def watched(src, dst, *a, **k):
        order.append(("copy", Path(dst).name))
        return real_copy(src, dst, *a, **k)
    monkeypatch.setattr(hd_install.shutil, "copyfile", watched)
    monkeypatch.setattr(hd_runner, "run_step", lambda *a, **k: order.append(("tool", a[0].label)) or 0)

    install(game, root, data, support, CATEGORIES, {"npcs"},
            store=tmp_path / "store", skip_space_check=True)

    first_tool = next(i for i, e in enumerate(order) if e[0] == "tool")
    backed_up = [i for i, e in enumerate(order) if e[0] == "copy"][:2]
    assert max(backed_up) < first_tool, "a tool ran before the backup finished"

    saved = tmp_path / "store" / "hdproject"
    for name, content in vanilla.items():
        assert (saved / name).read_bytes() == content


def test_a_second_run_does_not_overwrite_the_vanilla_backup(tmp_path):
    """
    the mistake the mod's own orig cache makes: repopulating from the live
    archive after an install caches the modded file as the original
    """
    game = make_game(tmp_path)
    prep = prepare(game, tmp_path, tmp_path / "store")
    was = {p.name: p.read_bytes() for p in prep.archives}
    back_up(prep)

    for path in prep.archives:
        path.write_bytes(b"MODDED" + b"." * 4096)
    back_up(prep)

    for path in prep.archives:
        assert (prep.backup_dir / path.name).read_bytes() == was[path.name], \
            f"{path.name} was re-saved from the modded copy"


def test_restore_puts_the_game_back_byte_for_byte(tmp_path):
    game = make_game(tmp_path)
    prep = prepare(game, tmp_path, tmp_path / "store")
    vanilla = {p.name: p.read_bytes() for p in prep.archives}
    back_up(prep)

    mark_written(prep.backup_dir)
    for path in prep.archives:
        path.write_bytes(b"MODDED" * 900)
    restore(prep)

    for path in prep.archives:
        assert path.read_bytes() == vanilla[path.name]


def test_restoring_without_a_backup_refuses(tmp_path):
    game = make_game(tmp_path)
    prep = prepare(game, tmp_path, tmp_path / "store")

    with pytest.raises(InstallError, match="no backup"):
        restore(prep)


def test_not_enough_room_refuses_before_touching_anything(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    vanilla = {p.name: p.read_bytes() for p in archive_pair(game, "u")}
    # a megabyte. The index in this fixture is a real one and measure() reads
    # the unpacked size out of it, so the 20GB guess is gone and a gigabyte is
    # now plenty for what it actually says.
    monkeypatch.setattr(hd_install, "free_space", lambda p: 1024 ** 2)

    with pytest.raises(InstallError, match="Nothing has been changed"):
        install(game, root, data, support, CATEGORIES, {"npcs"}, store=tmp_path / "store")

    for path in archive_pair(game, "u"):
        assert path.read_bytes() == vanilla[path.name]
    assert not (tmp_path / "store" / "hdproject").exists()


def test_the_backup_drive_is_checked_as_well_as_the_mod_drive(tmp_path, monkeypatch):
    """
    The backup goes to the undo store under the app's data folder, which on a
    normal Windows box is C: while the game and the mod are on whatever big
    drive they were installed to. Checking one and writing to two is how "I
    have loads of space" and "it ran out of space" are both true at once.
    """
    game = make_game(tmp_path, size=4 * 1024 ** 2)
    root, data, support = make_mod(tmp_path, monkeypatch)
    store = tmp_path / "store"

    room = {str(root): 500 * 1024 ** 3}          # the mod's drive, plenty
    monkeypatch.setattr(hd_install, "free_space",
                        lambda p: room.get(str(p), 1024 ** 2))   # everywhere else, 1MB
    monkeypatch.setattr(hd_install, "_same_drive", lambda a, b: False)

    prep = prepare(game, root, store)

    assert not prep.enough_room
    assert "backup" in prep.shortfall(), prep.shortfall()
    assert str(prep.backup_dir) in prep.shortfall(), \
        "it does not say where the backup was going, which is the surprising half"


def test_one_drive_means_one_budget(tmp_path, monkeypatch):
    """
    the common case. Mod and store on the same volume means the two costs come
    out of the same free space and adding them is the only correct check.
    """
    game = make_game(tmp_path, size=4 * 1024 ** 2)
    root, data, support = make_mod(tmp_path, monkeypatch)

    prep = prepare(game, root, tmp_path / "store")
    assert prep.same_drive, "tmp_path is one volume, so this should have noticed"

    payload = sum(p.stat().st_size for p in prep.archives)
    prep.needed_bytes, prep.backup_bytes = payload, payload
    prep.free_bytes = payload * 2 - 1

    assert not prep.enough_room, "the two costs were not added together"
    prep.free_bytes = payload * 2
    assert prep.enough_room


def test_the_space_estimate_is_replaced_by_what_the_index_says(tmp_path, monkeypatch):
    """
    the flat 20GB came off the mod's own README. The filelist knows the actual
    total, so the check stops being a guess in both directions.
    """
    game = make_game(tmp_path)
    root, _, _ = make_mod(tmp_path, monkeypatch)
    prep = prepare(game, root, tmp_path / "store")
    guessed = prep.needed_bytes

    monkeypatch.setattr(
        "patcher.mods.hd_preflight.survey_extraction",
        lambda book, where: __import__("patcher.mods.hd_preflight", fromlist=["Survey"])
        .Survey(entries=14381, bytes_needed=11 * 1024 ** 3, longest=188))

    seen = Log()
    hd_install.measure(prep, root, on_line=seen)

    assert prep.needed_bytes != guessed
    assert prep.needed_bytes > 11 * 1024 ** 3
    assert any("14381 files" in line for line in seen), seen


def test_a_path_that_cannot_be_written_refuses_before_the_backup(tmp_path, monkeypatch):
    """
    the live failure this came from: code 1 partway through an extraction. If
    it is going to fail on the 3077th file it can fail on the zeroth instead,
    with a sentence saying to move the folder.
    """
    game = make_game(tmp_path)
    root, _, _ = make_mod(tmp_path, monkeypatch)
    prep = prepare(game, root, tmp_path / "store")

    from patcher.mods import hd_preflight
    monkeypatch.setattr(hd_preflight, "survey_extraction",
                        lambda book, where: hd_preflight.Survey(
                            entries=2, longest=402,
                            too_long=[("chr/pc/pc001/model/pc001_00_a.win32.trb", 402)]))

    with pytest.raises(InstallError, match="longer than Windows allows"):
        hd_install.measure(prep, root)


def test_an_unreadable_index_does_not_block_the_install(tmp_path, monkeypatch):
    """
    a filelist this cannot parse is a reason to fall back to the estimate and
    let the tools try, not a new way to refuse before attempting anything
    """
    game = make_game(tmp_path)
    root, _, _ = make_mod(tmp_path, monkeypatch)
    prep = prepare(game, root, tmp_path / "store")
    guessed = prep.needed_bytes

    from patcher.mods import hd_preflight
    monkeypatch.setattr(hd_preflight, "survey_extraction",
                        lambda *a: (_ for _ in ()).throw(ValueError("not a filelist")))

    seen = Log()
    hd_install.measure(prep, root, on_line=seen)

    assert prep.needed_bytes == guessed
    assert any("is an estimate" in line for line in seen), seen
    # and names the file, since that is the thing worth sending on if it keeps
    # happening, plus the actual error underneath it
    assert any("filelist_scru.win32.bin" in line for line in seen), seen
    assert any("not a filelist" in line for line in seen), seen


def test_members_come_back_deduplicated_and_in_order(tmp_path, monkeypatch):
    _, data, _ = make_mod(tmp_path, monkeypatch)

    members = members_for(data, CATEGORIES, {"npcs", "eidolons"})

    assert ("n", "n910") in members
    assert ("s", "s101") in members
    assert len(members) == len(set(members)), "an id listed twice would be installed twice"


def test_selecting_nothing_is_refused_rather_than_run(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    _, data, _ = make_mod(tmp_path, monkeypatch)

    with pytest.raises(InstallError, match="nothing selected"):
        build_plan(game, tmp_path, data, CATEGORIES, set(), "u")


def test_the_archive_is_repacked_once_not_once_per_category(tmp_path, monkeypatch):
    _, data, _ = make_mod(tmp_path, monkeypatch)

    steps = build_plan(tmp_path, tmp_path, data, CATEGORIES, {"npcs", "eidolons", "pcs"}, "u")

    packs = [s for s in steps if s.tool == "pack"]
    assert len(packs) == 2, "unpack and repack, once each"
    assert packs[0].args[0] == "-x" and packs[-1].args[0] == "-c"
    assert steps[0] is packs[0] and steps[-1] is packs[-1]


def test_a_full_run_copies_the_rebuilt_archive_back(tmp_path, monkeypatch):
    """
    the default pack stub exits 1 from both modes, as the real one does, so
    this passing at all is the fix for the live failure
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)

    seen = Log()
    result = install(game, root, data, support, CATEGORIES, {"npcs"},
                     store=tmp_path / "store", on_line=seen, skip_space_check=True)

    assert result.ok
    from tests.helpers import carries_hd
    index, rebuilt = archive_pair(game, "u")
    assert carries_hd(index, rebuilt, "chr/npc/n910/bin/n910.win32.trb"), \
        "the rebuilt pair was not copied back"
    assert any("into the game" in line for line in seen)
    assert result.landed == ["the character archives"]


def test_a_failed_repack_leaves_the_game_alone(tmp_path, monkeypatch):
    """
    the game is only written at the very end, so anything that goes wrong
    before that point cannot have half-installed it
    """
    game = make_game(tmp_path)
    # unpacks for real, then does nothing at all on -c, which is what a broken
    # repack looks like now that the exit code is not the verdict
    root, data, support = make_mod(tmp_path, monkeypatch, tools={
        "pack": FF13TOOL.replace(
            "elif mode == '-c':", "elif mode == '-c' and False:").replace(
            "sys.exit(1)", "sys.exit(9)"),
    })
    vanilla = {p.name: p.read_bytes() for p in archive_pair(game, "u")}

    result = install(game, root, data, support, CATEGORIES, {"npcs"},
                     store=tmp_path / "store", skip_space_check=True)

    assert not result.ok and result.code == 9
    for path in archive_pair(game, "u"):
        assert path.read_bytes() == vanilla[path.name], "the game was written despite a failure"


def test_cancelling_mid_run_leaves_the_game_alone(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch, tools={
        "pack": "import time\nprint('unpacking', flush=True)\ntime.sleep(30)\n",
    })
    vanilla = {p.name: p.read_bytes() for p in archive_pair(game, "u")}

    cancel = threading.Event()
    threading.Timer(0.6, cancel.set).start()

    with pytest.raises(hd_runner.Cancelled):
        install(game, root, data, support, CATEGORIES, {"npcs"},
                store=tmp_path / "store", cancel=cancel, skip_space_check=True)

    for path in archive_pair(game, "u"):
        assert path.read_bytes() == vanilla[path.name]
    # and the backup survives a cancel, so a retry still has the vanilla copy
    assert (tmp_path / "store" / "hdproject" / "white_scru.win32.bin").is_file()


def test_a_repack_that_produced_nothing_is_caught_by_the_runner(tmp_path, monkeypatch):
    """
    it used to get all the way to install()'s own check. The runner names the
    step and stops there now, which is both earlier and more specific.
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch, tools={
        "pack": FF13TOOL.replace("elif mode == '-c':", "elif mode == '-c' and False:"),
    })

    result = install(game, root, data, support, CATEGORIES, {"npcs"},
                     store=tmp_path / "store", skip_space_check=True)

    assert not result.ok
    assert "repacking" in result.failed.label
    assert "white_scru.win32.bin" in result.reason, result.errors


def test_the_backstop_still_catches_an_archive_the_step_check_cannot_see(tmp_path, monkeypatch):
    """
    `produces` names one file and install() copies back two, so a repack that
    rewrites white_scr and loses filelist_scr satisfies the step and still
    cannot be copied back. That is what install()'s own check is for.
    """
    game = make_game(tmp_path)
    # rewrites the archive and loses the index beside it, which the step
    # check cannot see because `produces` names only one of the two
    loses_index = FF13TOOL.replace(
        "    pack_plain(pathlib.Path(tree.name + '.win32.bin'), index, rows)",
        "    pack_plain(pathlib.Path(tree.name + '.win32.bin'), index, rows)\n"
        "    index.unlink()")
    assert loses_index != FF13TOOL, "the stub changed and this test stopped testing anything"
    root, data, support = make_mod(tmp_path, monkeypatch, tools={"pack": loses_index})
    vanilla = {p.name: p.read_bytes() for p in archive_pair(game, "u")}

    with pytest.raises(InstallError, match="was not rebuilt"):
        install(game, root, data, support, CATEGORIES, {"npcs"},
                store=tmp_path / "store", skip_space_check=True)

    for path in archive_pair(game, "u"):
        assert path.read_bytes() == vanilla[path.name], \
            "it copied one archive back before noticing the other was missing"


REAL_CATEGORIES = None


def real_categories():
    global REAL_CATEGORIES
    if REAL_CATEGORIES is None:
        from patcher.manifest import load_manifest
        REAL_CATEGORIES = next(p.categories for p in
                               load_manifest(REPO / "manifests" / "ffxiii.json").patches
                               if p.ui_job_id == "hdproject")
    return REAL_CATEGORIES


def test_zones_are_installable_and_say_what_they_cost():
    """
    A zone is a marker like Z8 that expands to a whole archive of its own, and
    for a long time the honest thing was to say so rather than tick a box that
    did nothing. It runs now, and what it should say instead is the price:
    twenty archives unpacked and written back is the longest job in here.
    """
    zones = next(c for c in real_categories() if c["id"] == "zones")

    assert zones["supported"] is True
    assert zones["default"] is False, "the longest job in here is not a default"
    assert "own installer" not in zones["warn"], "it still sends people away"
    # a line, not a paragraph: these render in a tooltip beside the name
    for cat in real_categories():
        assert len(cat["warn"]) < 90, f'{cat["id"]} still has a paragraph on it'
    assert "Longest" in zones["warn"]

    assert all(c.get("supported") for c in real_categories() if c["id"] != "zones")


def test_an_unsupported_category_is_refused_by_name(tmp_path, monkeypatch):
    _, data, _ = make_mod(tmp_path, monkeypatch)

    with pytest.raises(InstallError, match="Environmental Zones"):
        build_plan(tmp_path, tmp_path, data, real_categories(), {"zones"}, "u")

    # and it poisons a mixed selection rather than silently doing the rest,
    # because a user who ticked it is expecting it
    with pytest.raises(InstallError, match="nothing has been changed"):
        build_plan(tmp_path, tmp_path, data, real_categories(), {"zones", "npcs"}, "u")


def test_a_category_that_would_install_nothing_is_refused(tmp_path, monkeypatch):
    """
    members that resolve to no step at all: a type letter the dispatch table
    does not have, an id with a separator in it. Running for a while and
    touching none of what a category named is worse than refusing.
    """
    _, data, _ = make_mod(tmp_path, monkeypatch)
    hollow = [{"id": "hollow", "label": "Hollow", "list": "LoadHDHollow.txt", "supported": True}]
    (data / "LoadHDHollow.txt").write_text("#:Hollow\nQ q001\nn ../etc\n", encoding="utf-8")

    with pytest.raises(InstallError, match="would install nothing"):
        build_plan(tmp_path, tmp_path, data, hollow, {"hollow"}, "u")


def test_a_zone_category_plans_its_groundwork_rather_than_being_refused(tmp_path, monkeypatch):
    """
    zones used to be the live example of the refusal above, because a zone
    marker produced no step. It produces the groundwork now: the blocks come
    out of per zone archives that have to be unpacked before anything can say
    how many there are, so the plan carries the unpacking and the rest is
    planned off what it produced.
    """
    _, data, _ = make_mod(tmp_path, monkeypatch)
    cats = [{"id": "zones", "label": "Zones", "list": "LoadHDZ.txt", "supported": True}]
    (data / "LoadHDZ.txt").write_text("#:Sunleth\nZ10\n#:Eden\nZ19\n", encoding="utf-8")

    steps = build_plan(tmp_path, tmp_path, data, cats, {"zones"}, "u")

    labels = [s.label for s in steps]
    assert "reading 2 zone archives out of the game" in labels
    assert "unpacking z010" in labels and "unpacking z019" in labels
    # and nothing from the scr pipeline, which a zones only install never touches
    assert not any("white_scru" in s.label for s in steps), \
        "a zones only install is unpacking and repacking a four gigabyte archive for nothing"


def test_an_id_in_two_categories_is_only_run_once(tmp_path, monkeypatch):
    """
    the mod's lists overlap. running a recipe twice is wasted minutes rather
    than a wrong result, but on 900 steps the minutes are the point.
    """
    _, data, _ = make_mod(tmp_path, monkeypatch)
    (data / "LoadHDOne.txt").write_text("#:One\nn n910\n", encoding="utf-8")
    (data / "LoadHDTwo.txt").write_text("#:Two\nn n910\nw w001\n", encoding="utf-8")
    cats = [{"id": "one", "label": "One", "list": "LoadHDOne.txt", "supported": True},
            {"id": "two", "label": "Two", "list": "LoadHDTwo.txt", "supported": True}]

    steps = build_plan(tmp_path, tmp_path, data, cats, {"one", "two"}, "u")

    # unpack, seed, then the work, then repack
    inner = [s for s in steps if s.payload]
    assert len(inner) == 2, [s.label for s in inner]
    assert len({(s.tool, tuple(s.args)) for s in inner}) == 2


def test_the_real_manifest_plans_a_sane_number_of_steps(tmp_path, monkeypatch):
    """
    against the mod's own Data listing, not a fixture: every supported category
    has to produce steps, and the total has to look like the 10 to 45 minutes
    the mod documents rather than three steps or a hundred thousand.
    """
    _, data, _ = make_mod(tmp_path, monkeypatch)
    shipped = REPO / "tests" / "data" / "hd"
    for name in ("LoadHDEidolons.txt",):
        shutil.copy(shipped / name, data / name)

    cats = [c for c in real_categories() if c["id"] in {"eidolons", "npcs"}]
    steps = build_plan(tmp_path, tmp_path, data, cats, {"eidolons", "npcs"}, "u")

    assert steps[0].args[0] == "-x" and steps[-1].args[0] == "-c"
    assert len(steps) > 4, "too few steps to be doing anything"
    assert all(s.tool in {"pack", "models", "inject", None} for s in steps)
    # exactly one local step, and it is the seed that caches the originals
    local = [s for s in steps if s.tool is None]
    assert [s.action for s in local] == ["seed"], [s.label for s in local]


def test_a_read_only_game_folder_is_caught_before_anything_runs(tmp_path, monkeypatch):
    """
    Finding out the game folder cannot be written after forty minutes of
    repacking is the worst possible moment to find out.
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    monkeypatch.setattr(hd_install, "writable_or_reason",
                        lambda p: "it is read-only" if "FANTASY" in str(p) else None)

    with pytest.raises(InstallError, match="game folder cannot be written"):
        install(game, root, data, support, CATEGORIES, {"npcs"},
                store=tmp_path / "store", skip_space_check=True)

    assert not (tmp_path / "store" / "hdproject").exists(), "it got as far as the backup"


def test_a_read_only_mod_folder_is_caught_too(tmp_path, monkeypatch):
    """the tools unpack a multi-gigabyte archive into it, so it has to be writable"""
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    monkeypatch.setattr(hd_install, "writable_or_reason",
                        lambda p: "it is read-only" if "HDProject" in str(p) else None)

    with pytest.raises(InstallError, match="mod folder cannot be written"):
        install(game, root, data, support, CATEGORIES, {"npcs"},
                store=tmp_path / "store", skip_space_check=True)


def test_a_half_modified_tree_from_a_cancelled_run_is_cleared_first(tmp_path, monkeypatch):
    """
    ff13tool -x writes over the files it extracts rather than emptying the
    folder first, so anything a cancelled run left behind that this one does
    not touch would survive into the repack and go into the game.
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    stale = root / "white_scru" / "chr" / "pc" / "leftover.trb"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"from a run that was cancelled")

    install(game, root, data, support, CATEGORIES, {"npcs"},
            store=tmp_path / "store", skip_space_check=True)

    assert not stale.exists(), "a file from the previous run made it into the repack"


def test_the_working_files_are_gone_once_the_game_has_the_result(tmp_path, monkeypatch):
    """
    1.3GB of extracted game data and half a gigabyte of archive copies, per
    install, in somebody's Downloads folder. Their own script cleans up and so
    should this.
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)

    seen = Log()
    install(game, root, data, support, CATEGORIES, {"npcs"},
            store=tmp_path / "store", on_line=seen, skip_space_check=True)

    assert not (root / "white_scru").exists()
    assert not (root / "white_scru.win32.bin").exists()
    assert not (root / "filelist_scru.win32.bin").exists()
    assert any("[clean]" in line for line in seen), seen


def test_a_failed_run_keeps_the_evidence(tmp_path, monkeypatch):
    """
    the extracted tree is what anybody would want to look at, and deleting it
    on failure is the last thing to do while working out what went wrong
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch, tools={
        "pack": FF13TOOL.replace("elif mode == '-c':", "elif mode == '-c' and False:"),
    })

    zone = root / "models" / "orig" / "zoneu" / "white_z999u_img.win32.bin"
    zone.parent.mkdir(parents=True)
    zone.write_bytes(b"zone")

    result = install(game, root, data, support, CATEGORIES, {"npcs"},
                     store=tmp_path / "store", skip_space_check=True)

    assert not result.ok
    assert (root / "white_scru").is_dir(), "it swept away the evidence"
    assert zone.is_file() and (root / "models" / "mod").is_dir()


def test_the_model_caches_are_not_ours_to_delete(tmp_path, monkeypatch):
    """
    their script keeps models\\orig between runs as a cache, and keeps
    models\\mod\\chr\\pc and \\npc on purpose because other mods read them
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    for kept in (root / "models" / "orig" / "chr" / "pc" / "c001.trb",
                 root / "models" / "mod" / "chr" / "npc" / "n910.trb"):
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(b"cached")

    install(game, root, data, support, CATEGORIES, {"npcs"},
            store=tmp_path / "store", skip_space_check=True)

    assert (root / "models" / "orig" / "chr" / "pc" / "c001.trb").exists()
    assert (root / "models" / "mod" / "chr" / "npc" / "n910.trb").exists()


def test_what_no_later_run_reads_goes_once_the_game_has_it(tmp_path, monkeypatch):
    """
    the tools' output is in the game already, and the zone copies come out of
    our backup on every zone run. Kept, they were gigabytes for nobody
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    for rel in ("models/mod/chr/weapon/w001/bin/w001.win32.trb",
                "models/mod/chr/pc/c001/bin/c001.win32.trb",
                "models/orig/zoneu/white_z999u_img.win32.bin",
                "models/orig/zoneu/z999/bg/loc001/a001/bin/block001.win32.trb",
                "models/orig/zonec/filelist_z999c.win32.bin"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x" * 64)
    seen = Log()

    result = install(game, root, data, support, CATEGORIES, {"npcs"},
                     store=tmp_path / "store", on_line=seen, skip_space_check=True)

    assert result.ok
    assert not list((root / "models" / "orig").glob("zone*"))
    assert not (root / "models" / "mod" / "chr" / "weapon").exists()
    # the two their own cleanup keeps for other mods, and the originals
    assert (root / "models" / "mod" / "chr" / "pc" / "c001" / "bin" / "c001.win32.trb").is_file()
    assert any((root / "models" / "mod" / "chr" / "npc").rglob("*.trb"))
    assert any((root / "models" / "orig" / "chr").rglob("*.trb"))
    assert any("working copies in the mod folder" in line for line in seen), seen


def test_nothing_is_emptied_through_a_link_in_the_mod_folder(tmp_path):
    """a folder linked in under one of the names a run clears is somebody's"""
    from patcher.mods.hd_layout import XIII2

    theirs = tmp_path / "theirs"
    (theirs / "weapon" / "w001").mkdir(parents=True)
    (theirs / "weapon" / "w001" / "precious.trb").write_bytes(b"mine")
    (theirs / "orig" / "bg").mkdir(parents=True)
    (theirs / "orig" / "bg" / "precious.imgb").write_bytes(b"mine")
    xiii, xiii2 = tmp_path / "xiii", tmp_path / "xiii2"
    (xiii / "models" / "mod").mkdir(parents=True)
    (xiii / "models" / "orig").mkdir(parents=True)
    try:
        (xiii / "models" / "mod" / "chr").symlink_to(theirs, target_is_directory=True)
    except OSError:
        pytest.skip("no symlinks here")
    (xiii / "models" / "orig" / "zoneu").symlink_to(theirs, target_is_directory=True)
    (xiii2 / "zone").mkdir(parents=True)
    (xiii2 / "zone" / "z0010").symlink_to(theirs, target_is_directory=True)
    (xiii2 / "models").mkdir()
    (xiii2 / "models" / "mod").symlink_to(theirs, target_is_directory=True)

    hd_install.clear_caches(xiii)
    hd_install.clear_caches(xiii2, layout=XIII2)

    assert (theirs / "weapon" / "w001" / "precious.trb").read_bytes() == b"mine"
    assert (theirs / "orig" / "bg" / "precious.imgb").read_bytes() == b"mine"


def test_a_locked_leftover_does_not_fail_an_install_that_already_worked(tmp_path, monkeypatch):
    """
    the game has the archives by this point. Refusing to report success over a
    file somebody has open in another window would be absurd.
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)

    real = hd_install.shutil.rmtree
    def stubborn(path, *a, **k):
        raise OSError(32, "being used by another process")
    monkeypatch.setattr(hd_install.shutil, "rmtree", stubborn)

    seen = Log()
    result = install(game, root, data, support, CATEGORIES, {"npcs"},
                     store=tmp_path / "store", on_line=seen, skip_space_check=True)

    assert result.ok
    assert any("could not remove" in line for line in seen), seen


def test_a_mod_folder_that_has_installed_before_makes_the_backup_suspect(tmp_path, monkeypatch):
    """
    the mistake worth inheriting a guard against. If somebody installed this by
    hand and then found this app, the first backup captures their already
    modded archives and becomes what Revert puts back, permanently.

    models\\orig is the mod's own signal: their batch file probes it to decide
    whether this is a first install.
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    cached = root / "models" / "orig" / "chr" / "pc" / "c001" / "bin" / "c001.win32.trb"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached by their installer")
    prep = prepare(game, root, tmp_path / "store")

    doubt = hd_install.suspect_backup(root, prep)

    assert "installed from this folder before" in doubt
    assert "verify the game files through Steam" in doubt, "no way out is offered"


def test_a_clean_mod_folder_says_nothing(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    root, _, _ = make_mod(tmp_path, monkeypatch)

    assert hd_install.suspect_backup(root, prepare(game, root, tmp_path / "store")) == ""


def test_an_empty_orig_cache_is_not_a_previous_install(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    root, _, _ = make_mod(tmp_path, monkeypatch)
    (root / "models" / "orig").mkdir(parents=True)

    assert hd_install.suspect_backup(root, prepare(game, root, tmp_path / "store")) == ""


def test_once_we_have_a_backup_the_question_is_settled(tmp_path, monkeypatch):
    """
    saying it on the second run is too late to be useful and is just noise on
    every run thereafter
    """
    game = make_game(tmp_path)
    root, _, _ = make_mod(tmp_path, monkeypatch)
    (root / "models" / "orig" / "chr").mkdir(parents=True)
    (root / "models" / "orig" / "chr" / "x.trb").write_bytes(b"cached")
    prep = prepare(game, root, tmp_path / "store")
    back_up(prep)

    assert hd_install.suspect_backup(root, prep) == ""


def test_the_warning_reaches_the_terminal_as_an_error(tmp_path, monkeypatch):
    """
    it goes out on the stderr stream so it renders red, because it is the only
    moment this can be said and a grey line in three thousand is not saying it
    """
    game = make_game(tmp_path)
    root, data, support = make_mod(tmp_path, monkeypatch)
    (root / "models" / "orig" / "chr").mkdir(parents=True)
    (root / "models" / "orig" / "chr" / "x.trb").write_bytes(b"cached")

    seen = Log()
    install(game, root, data, support, CATEGORIES, {"npcs"},
            store=tmp_path / "store", on_line=seen, skip_space_check=True)

    assert any("installed from this folder before" in line for line in seen.on("err")), seen.tagged


def test_a_modded_game_is_detected_from_the_archive_not_the_folder(tmp_path, monkeypatch):
    """
    stronger than the models\\orig signal, because it measures the game rather
    than inferring from what some folder implies. The sizes come from their own
    Setup.bat and the index carries the same numbers.
    """
    game = make_game(tmp_path)
    root, _, _ = make_mod(tmp_path, monkeypatch)
    prep = prepare(game, root, tmp_path / "store")

    from patcher.mods import hd_preflight
    monkeypatch.setattr(hd_preflight, "already_modded",
                        lambda book: [("chr/pc/c201/bin/c201.win32.trb", 794339, 999999)])

    doubt = hd_install.suspect_backup(root, prep)

    assert "not the size a vanilla copy" in doubt
    assert "c201" in doubt, "it does not say which character"
    assert "verify the game files through Steam" in doubt


def test_an_index_that_cannot_be_read_falls_back_to_the_folder_signal(tmp_path, monkeypatch):
    """
    a filelist this cannot parse is a reason to fall back, not a reason to
    refuse or to claim the game is fine
    """
    game = make_game(tmp_path)
    root, _, _ = make_mod(tmp_path, monkeypatch)
    (root / "models" / "orig" / "chr").mkdir(parents=True)
    (root / "models" / "orig" / "chr" / "x.trb").write_bytes(b"cached")
    prep = prepare(game, root, tmp_path / "store")

    from patcher.mods import hd_preflight
    monkeypatch.setattr(hd_preflight, "already_modded",
                        lambda book: (_ for _ in ()).throw(ValueError("not a filelist")))

    assert "installed from this folder before" in hd_install.suspect_backup(root, prep)


def test_a_locked_game_file_is_refused_before_the_work_not_after(tmp_path, monkeypatch):
    """
    a live run repacked for forty minutes, copied the result back and died on
    an unhandled PermissionError, which is the worst moment to find out and
    the least useful way to say it. The folder probe cannot see this: a
    running game holds its archives open, and on Windows that is a share mode
    rather than a permission bit, so the folder probes writable and the file
    inside it does not.
    """
    from patcher.mods.hd_install import in_use, prepare

    game = make_game(tmp_path)
    locked = game / "white_data" / "sys" / "white_scru.win32.bin"

    # what the OS does when something else holds it, without needing an OS
    # that does it: the check is "can this be opened for update", and that is
    # the question either way
    real = Path.open

    def refuse(self, *a, **kw):
        if self == locked and a and "+" in str(a[0]):
            raise PermissionError(13, "Permission denied")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "open", refuse)

    assert [n for n, _ in in_use([locked])] == ["white_scru.win32.bin"]
    with pytest.raises(InstallError, match="game is running"):
        prepare(game, tmp_path, tmp_path / "store")


def test_a_lock_that_appears_mid_run_does_not_crash_the_shell(tmp_path, monkeypatch):
    """
    the preflight cannot cover this one: somebody launches the game to look at
    the last install while this one is still repacking. It has to come back as
    a failure that says what to do, not a traceback, and it has to say the
    work is still there.
    """
    from patcher import fileops
    from patcher.mods import hd_install

    root, data, support = make_mod(tmp_path, monkeypatch)
    game = make_game(tmp_path)
    vanilla = {p.name: p.read_bytes() for p in archive_pair(game, "u")}

    # a running game holds its archive without share-delete, so moving it
    # aside for the swap is the step Windows refuses
    real_replace = fileops.os.replace

    def refuse(src, dst, *a, **kw):
        if str(src).endswith("white_scru.win32.bin") and "white_data" in str(src):
            raise PermissionError(13, "The process cannot access the file")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(fileops.os, "replace", refuse)

    with pytest.raises(InstallError) as caught:
        hd_install.install(game, root, data, support, CATEGORIES, {"npcs"},
                           store=tmp_path / "store", skip_space_check=True)

    said = str(caught.value)
    assert "game is running" in said
    assert "Nothing in the game has been changed" in said, "nothing said the game was still safe"
    # and it is true: the index that did swap went back when the archive would not
    for path in archive_pair(game, "u"):
        assert path.read_bytes() == vanilla[path.name], f"{path.name} was left half swapped"
    leftovers = [p.name for p in (game / "white_data" / "sys").iterdir()
                 if p.name.endswith((".part", ".prev"))]
    assert not leftovers, f"staging files left in the game folder: {leftovers}"
