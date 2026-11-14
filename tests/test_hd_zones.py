"""
The environmental zones, which are the one category that is not just more ids.

Everything here was read off FFXIII HD Setup.bat and FFXIII HD Installer.bat
and then checked against real game files: the 23 zone indexes really are inside
filelistu.win32.bin, and the block path really is the four character directory
their own z010block022u.txt spells out.

The fixture builds a small but real version of the whole nest, because the
shape IS the thing being tested: an index inside an archive, a blob beside it
on disk, and a second archive pair two levels further in.
"""

import shutil
import sys
import zlib
from pathlib import Path

import pytest

from patcher.mods import hd_install, hd_runner, hd_zones
from patcher.mods.hd_install import InstallError, install, prepare, restore
from patcher.mods.hd_zones import blocks, plan_for, read_entry, zone_ids
from tests.conftest import Log
from tests.helpers import pack

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data" / "hd"

ZONES = [{"id": "zones", "label": "Environmental Zones",
          "list": "LoadHDEnvironments.txt", "supported": True}]

# one area, two blocks, and one of them has a recipe while the other does not,
# which is the fork GENERATEZONE takes per block
BLOCK_FILES = [
    "bg/loc014/m022/bin/block022.win32.trb",
    "bg/loc014/m022/bin/block022.win32.imgb",
    "bg/loc014/m041/bin/block041.win32.trb",
    "bg/loc014/m041/bin/block041.win32.imgb",
    # not a block: a four character rule keeps this out rather than handing
    # the tool a directory it cannot make sense of
    "bg/loc014/shared/bin/notes.win32.trb",
]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """a game with one zone in it, and a mod folder wired to work on it"""
    from tests.test_hd_install import DEFAULT_TOOLS
    from tests.test_hd_runner import stub

    game = tmp_path / "FINAL FANTASY XIII"
    sys_dir, zone_dir = game / "white_data" / "sys", game / "white_data" / "zone"
    sys_dir.mkdir(parents=True)
    zone_dir.mkdir(parents=True)

    # the scr pair, so find_variant recognises this as XIII at all
    from tests.helpers import index_entries, write_scr_pair
    write_scr_pair(sys_dir / "filelist_scru.win32.bin", sys_dir / "white_scru.win32.bin",
                   index_entries(DATA), size=4096)

    # the zone's own pair, which lives loose on disk
    zone_index = pack(zone_dir / "white_z010u_img.win32.bin",
                      zone_dir / "filelist_z010u.win32.bin",
                      [(p.replace("/", "\\"), b"Z" * 900) for p in BLOCK_FILES])

    # and the main image archive, which is where the zone's INDEX lives. This
    # nesting is the whole point: the blob is on disk and its index is inside
    # something else.
    pack(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
         [("zone\\filelist_z010u.win32.bin", zone_index.read_bytes()),
          ("zone\\z010\\z010.wdb", b"w" * 768)])
    zone_index.unlink()

    root = tmp_path / "HDProject"
    data, support = root / "Data", root / "SupportFiles"
    data.mkdir(parents=True)
    support.mkdir(parents=True)
    (data / "LoadHDEnvironments.txt").write_text("#:Sunleth\nZ10\n", encoding="utf-8")
    shutil.copy(DATA / "z010block022u.txt", data / "z010block022u.txt")

    if sys.platform != "win32":
        for key, name in (("pack", "ff13tool"), ("models", "FFXIII_ModelsHD"),
                          ("inject", "FFXIII_HDProject")):
            monkeypatch.setitem(hd_runner.TOOLS, key, name)
    for key in ("pack", "models", "inject"):
        stub(support, hd_runner.TOOLS[key], DEFAULT_TOOLS[key])

    monkeypatch.setattr(hd_install, "free_space", lambda p: 500 * 1024 ** 3)
    return game, root, data, support


def test_the_twenty_zone_ids_come_out_of_their_own_list():
    """
    a bare Z8 widened to z008, which is what the archive is called. Their
    installer has the same twenty in the same order and hardcodes them; this
    reads them, so a list that gains a zone gains one here too.
    """
    from patcher.mods.hd_recipe import read_category

    found = zone_ids(read_category(DATA / "LoadHDEnvironments.txt").members)
    assert found == ["z002", "z003", "z004", "z006", "z008", "z010", "z015", "z016",
                     "z017", "z018", "z019", "z020", "z021", "z022", "z023", "z024",
                     "z026", "z027", "z029", "z030"]


def test_a_group_letter_is_not_a_zone():
    """the other lists are `n n910`, and a group is not a marker"""
    assert zone_ids([("N", "n910"), ("W", "w001")]) == []
    assert zone_ids([(None, "Z10"), (None, "Z10")]) == ["z010"], "duplicates survived"
    assert zone_ids([(None, "banana"), (None, "Z")]) == []


def test_one_file_comes_out_of_an_archive_without_unpacking_it(tmp_path):
    """
    the reason this does not copy their Setup.bat. Theirs extracts the whole
    of white_img<v>, two gigabytes of writes, to get at 659KB of zone indexes.
    """
    from patcher.archive.filelist import load

    blob = tmp_path / "white_imgu.win32.bin"
    index = pack(blob, tmp_path / "filelistu.win32.bin",
                 [("zone\\filelist_z010u.win32.bin", b"INDEX" * 400),
                  ("zone\\z010\\z010.wdb", b"w" * 768)])
    entries = list(load(index).entries())

    assert read_entry(blob, entries, "zone/filelist_z010u.win32.bin") == b"INDEX" * 400
    with pytest.raises(KeyError):
        read_entry(blob, entries, "zone/filelist_z999u.win32.bin")


def test_the_blocks_are_walked_rather_than_named(tmp_path):
    """
    their installer hardcodes a bg folder per zone and gives z029 z019's, which
    is either a typo or a quirk and cannot be settled from the script. A walk
    needs neither answer.
    """
    root = tmp_path / "mod"
    base = root / "models" / "orig" / "zoneu" / "z010" / "bg"
    for path in BLOCK_FILES:
        target = base.parent / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    (base / "loc020" / "m003" / "bin").mkdir(parents=True)

    found = blocks(root, "u", "z010")

    assert found == [("bg\\loc014\\m022", "022"), ("bg\\loc014\\m041", "041"),
                     ("bg\\loc020\\m003", "003")], \
        "a second area under bg was missed, which is what a hardcoded loc does"
    assert not any("shared" in rel for rel, _ in found), \
        "a directory that is not a four character block was handed to the tool"


def test_the_plan_matches_what_their_installer_runs(world):
    """
    every argument, because this is a reimplementation of GENERATEZONE and a
    wrong path is a silently skipped block rather than an error
    """
    game, root, data, _ = world
    cache = root / "models" / "orig" / "zoneu" / "z010" / "bg" / "loc014" / "m022" / "bin"
    cache.mkdir(parents=True)
    (cache / "block022.win32.trb").write_bytes(b"x")

    steps = plan_for(root, game, data, "u", ["z010"])
    by_label = {s.label: s for s in steps}

    # the recipe exists for this block, so it is the ModelsHD branch
    assert by_label["z010 block022 models"].args == ["Data\\z010block022u.txt"]

    # both halves of the pair, in their order. A block is a .trb of texture
    # headers and a .imgb of the pixels those headers address, and their
    # LOADMODELFILE imports them one after the other. Shipping the .trb alone
    # put HD headers over vanilla pixel data: 2536 steps of reported success
    # and every surface in the game rendered as noise.
    for suffix in (".trb", ".imgb"):
        into = by_label[f"z010 block022{suffix} into the zone archive"]
        assert into.args == ["-i", "..\\..\\zone\\filelist_z010u.win32.bin",
                             "..\\..\\zone\\white_z010u_img.win32.bin",
                             f"bg\\loc014\\m022\\bin\\block022.win32{suffix}"]
        assert into.cwd == "models\\mod\\zoneu\\z010", \
            "ff13tool resolves all three of those against the current directory"

    # the .trb waits on the tool, and the .imgb waits on the .trb, so half a
    # block never goes in without the other half having gone first
    assert by_label["z010 block022.trb into the zone archive"].after == "z010 block022 models", \
        "an import for a block the tools skipped asks for a folder never made"
    assert by_label["z010 block022.imgb into the zone archive"].after == \
        "z010 block022.trb into the zone archive"

    # one step into the game, holding off if any import in the zone failed
    commit = by_label["z010 into the game"]
    assert commit.action == hd_runner.COMMIT
    assert commit.args[:5] == ["models\\mod\\zone\\white_z010u_img.win32.bin",
                               "models\\mod\\zone\\filelist_z010u.win32.bin",
                               str(game), "u", "z010"]
    assert set(commit.unless_failed) == {s.label for s in steps if s.success == hd_runner.OK_IMPORTED}
    assert commit.after_any == ["z010 block022 models"]


def test_a_block_without_a_recipe_goes_through_the_injector(world):
    """
    the other half of GENERATEZONE's fork, and it reads a count rather than a
    status, same as every other use of that executable
    """
    game, root, data, _ = world
    cache = root / "models" / "orig" / "zoneu" / "z010" / "bg" / "loc014" / "m041" / "bin"
    cache.mkdir(parents=True)
    (cache / "block041.win32.trb").write_bytes(b"x")

    step = {s.label: s for s in plan_for(root, game, data, "u", ["z010"])}[
        "z010 block041 textures"]

    # /s included: it is on their zone call and on neither of their two chr
    # calls, so it is not noise they left lying around
    assert step.args == ["block041.win32",
                         "models\\orig\\zoneu\\z010\\bg\\loc014\\m041\\bin\\",
                         "models\\mod\\zoneu\\z010\\bg\\loc014\\m041\\bin\\",
                         "zoneu\\z010\\", "/s"]
    assert step.success == hd_runner.OK_COUNT
    assert step.optional and step.payload


def test_a_zone_installs_end_to_end(world):
    """
    the whole nest, driven through install(): the index out of white_imgu, the
    zone archive unpacked, both blocks built, both imported, the blob back on
    disk and the rewritten index back into white_imgu.
    """
    game, root, data, support = world
    seen = Log()

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", on_line=seen, skip_space_check=True)

    assert result.ok, (result.failed and result.failed.label, result.errors)
    assert result.applied == 2, f"{result.applied} blocks built, expected both"

    # the blob really was replaced, read back through its own index rather than
    # by searching the file for a marker. Payloads are stored compressed, so a
    # marker is only findable in a blob nothing has properly packed.
    from patcher.archive.filelist import load
    from patcher.mods.hd_state import _zone_index

    sys_dir = game / "white_data" / "sys"
    inside = _zone_index(sys_dir, "u", "z010")
    for suffix in (".trb", ".imgb"):
        path = f"bg/loc014/m022/bin/block022.win32{suffix}"
        assert inside.get(path, 0) > 900, \
            f"{suffix} in the game is still the size the fixture packed it at"

    # and the index really went back into the archive it lives in
    entries = list(load(sys_dir / "filelistu.win32.bin").entries())
    assert any(e.path.replace("\\", "/") == "zone/filelist_z010u.win32.bin"
               for e in entries), "the zone index was dropped from white_imgu"


def test_both_halves_of_a_block_reach_the_zone_archive(world):
    """
    the one that shipped. A block is a .trb of texture headers and a .imgb of
    the pixels those headers address; we imported the first and left the
    second, so the game read HD headers against vanilla pixel data and drew
    every wall as coloured noise while the log reported 2536 successful steps.

    Asserted on what ff13tool was actually handed during a real install, which
    is the boundary the bug lived on. The stub's -i synthesises index offsets
    rather than laying the blob out for real, so reading the entry back would
    be testing the stub.
    """
    game, root, data, support = world
    seen = Log()

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", on_line=seen, skip_space_check=True)
    assert result.ok, (result.failed and result.failed.label, result.errors)

    imports = [text for text in seen.on("cmd") if " -i " in text]
    for number in ("022", "041"):
        for suffix in (".trb", ".imgb"):
            wanted = f"bg\\loc014\\m{number}\\bin\\block{number}.win32{suffix}"
            assert any(text.endswith(wanted) or f"{wanted} " in text
                       for text in imports), \
                f"block{number}{suffix} was never imported into the zone archive"

    # and in their order, because the pair is written into one blob in sequence
    order = [text.rsplit("win32", 1)[-1].split()[0] for text in imports
             if "block022" in text]
    assert order == [".trb", ".imgb"], f"the pair went in as {order}"


def test_revert_puts_the_zone_archives_back_without_being_told_which(world):
    """
    the one that would have left somebody stuck. restore_hd_project builds a
    bare prepare(), which knows the scr pair and nothing else, so prep.extra is
    empty and Revert put back two archives and left white_imgu and all twenty
    zone blobs modded. A corrupted zone install could not be undone from the
    app at all.

    Revert has no category list behind it, so the backup folder has to be what
    decides.
    """
    game, root, data, support = world
    sys_dir, zone_dir = game / "white_data" / "sys", game / "white_data" / "zone"
    vanilla = {p.name: p.read_bytes()
               for p in (*sys_dir.iterdir(), *zone_dir.iterdir())}

    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", skip_space_check=True)

    blob = zone_dir / "white_z010u_img.win32.bin"
    assert blob.read_bytes() != vanilla[blob.name], "nothing was installed to undo"

    # exactly what the shell does: a bare prepare, no categories, no zones
    fresh = prepare(game, root, game / ".undo")
    restore(fresh)

    for name, before in vanilla.items():
        where = zone_dir if name.startswith("white_z") else sys_dir
        assert (where / name).read_bytes() == before, f"{name} was left modded"


def built_from(cache_file: Path):
    """
    the cache's bytes as the zone's own archive is unpacked, which is what the
    zone is built out of. A run that works clears the cache after, so this is
    the only moment to look
    """
    seen = []

    def on_step(step, *_):
        if step.label.startswith("unpacking ") and not seen and cache_file.is_file():
            seen.append(cache_file.read_bytes())
    return seen, on_step


def test_a_zone_rebuilds_over_a_broken_install_without_going_back_to_vanilla(world):
    """
    Repair, on the case it was built for. A zone install that went out wrong
    has a blob in the game that does not match the index describing it, and the
    fix has to be reachable without restoring six categories that were fine.

    It works because nothing here reads the game to decide what to write. The
    working pair is seeded out of the backup every run, the blob goes back as a
    whole file copy and the index entry is replaced inside white_img<v>, so the
    second run writes exactly what the first one should have.
    """
    game, root, data, support = world
    cache = root / "models" / "orig" / "zoneu" / "white_z010u_img.win32.bin"
    blob = game / "white_data" / "zone" / "white_z010u_img.win32.bin"
    vanilla = blob.read_bytes()

    first, watch = built_from(cache)
    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", on_step=watch, skip_space_check=True)
    good = blob.read_bytes()
    assert good != vanilla
    assert first == [vanilla], "the cache is not holding the original"

    # however it got broken, the shape is the same: what is in the game is not
    # what the pipeline would have produced
    blob.write_bytes(b"CORRUPT" * 4096)

    seen = Log()
    again, watch = built_from(cache)
    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", on_line=seen, on_step=watch, skip_space_check=True)

    assert result.ok, (result.failed and result.failed.label, result.errors)
    assert blob.read_bytes() == good, "the repair did not reproduce the good install"

    # and it never read the broken game to decide what to write. If the harvest
    # refreshed the cache from disk, the rebuild would be built out of whatever
    # the last bad run left behind.
    assert again == [vanilla], \
        "the originals cache was refilled from a modded game, so a repair makes it worse"

    imports = [text for text in seen.on("cmd") if " -i " in text]
    for suffix in (".trb", ".imgb"):
        assert any(text.rstrip().endswith(f"block022.win32{suffix}") or
                   f"block022.win32{suffix} " in text for text in imports), \
            f"the repair skipped the {suffix} half"


def test_a_half_written_zone_is_found_without_anybody_launching_the_game(world, monkeypatch):
    """
    the answer to "how would a user know". They knew by playing it, seeing
    coloured static, quitting and coming back, which is not a flow anybody
    should have to run.

    A block is a pair, so a .trb that differs from vanilla beside a .imgb that
    does not is a state no correct run can produce: the tools either touch a
    block and write both halves, or skip it and write neither. That is
    readable off the two indexes, and this drives the real pipeline with the
    exact defect that shipped rather than writing the signature by hand.
    """
    from patcher.mods.hd_state import half_written_zones

    game, root, data, support = world
    store = game / ".undo" / "hdproject"

    # what the shipped build did: import the .trb half and stop
    monkeypatch.setattr(hd_zones, "MODEL_FILES", (".win32.trb",))
    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", skip_space_check=True)

    assert half_written_zones(game, "u", store, data, ZONES[0]) == ["z010"], \
        "the exact defect that shipped went unnoticed"


def test_a_whole_install_is_not_mistaken_for_a_half_written_one(world):
    """
    the half that decides whether this is usable. A check that cries wolf on a
    good install is worse than no check, because the badge it raises is the
    one telling somebody to spend forty minutes.
    """
    from patcher.mods.hd_state import half_written_zones

    game, root, data, support = world
    store = game / ".undo" / "hdproject"

    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", skip_space_check=True)

    assert half_written_zones(game, "u", store, data, ZONES[0]) == []
    # and a game nothing has been done to has nothing to say either
    assert half_written_zones(game, "u", game / "nope", data, ZONES[0]) == []


def test_the_extraction_is_thrown_away_once_it_has_been_harvested(world):
    """
    twenty zones at up to half a gigabyte each unpack to more than the game
    takes. Their own cleanup tries to delete these and its glob matches none
    of the twenty, which is why the mod folder ends up so large.
    """
    game, root, data, support = world
    cache = root / "models" / "orig" / "zoneu"
    during = []

    def watch(step, *_):
        if step.label == "z010 archive ready to write into":
            during.append(((cache / "white_z010u_img").exists(), (cache / "z010" / "bg").is_dir()))

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", on_step=watch, skip_space_check=True)

    assert result.ok
    # while the blocks go in: the unpack gone, the part they're built from kept
    assert during == [(False, True)]
    # and once the game has them, the whole cache, which the next run takes
    # out of the backup again
    assert not cache.exists()


def test_the_backup_covers_what_a_zone_install_overwrites(world):
    """
    a zone writes to the game where it lies: the blob is replaced and the index
    is imported into white_img<v> in place. Neither is the scr pair, so without
    this Revert puts back a game still carrying half a zone install.
    """
    game, root, data, support = world
    before = {p.name: p.read_bytes() for p in
              [game / "white_data" / "sys" / "white_imgu.win32.bin",
               game / "white_data" / "sys" / "filelistu.win32.bin",
               game / "white_data" / "zone" / "white_z010u_img.win32.bin"]}

    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", skip_space_check=True)

    for name, was in before.items():
        assert (game / ".undo" / "hdproject" / name).read_bytes() == was, \
            f"{name} is not in the backup, so Revert cannot undo this"

    prep = prepare(game, root, game / ".undo")
    prep.extra = hd_zones.zone_archives(game, "u", ["z010"])
    restore(prep)

    for name, was in before.items():
        found = next(p for p in [*prep.archives, *prep.extra] if p.name == name)
        assert found.read_bytes() == was, f"{name} did not come back"


def test_a_zones_only_install_never_touches_the_scr_pair(world):
    """
    unpacking and repacking a four gigabyte archive to install something that
    is not in it is forty minutes for nothing
    """
    game, root, data, support = world
    scr = game / "white_data" / "sys" / "white_scru.win32.bin"
    was = scr.read_bytes()
    seen = Log()

    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", on_line=seen, skip_space_check=True)

    assert scr.read_bytes() == was
    assert not (root / "white_scru").exists(), "it unpacked the archive anyway"


def test_a_zone_with_no_backgrounds_in_it_is_refused_rather_than_called_done(world):
    """
    the same trap the chr pipeline fell into: every step succeeded and nothing
    was installed. An archive that unpacks to no bg at all is a broken setup,
    not a run of bad luck.
    """
    game, root, data, support = world
    # an archive with the right name and nothing in it worth having
    index = game / "white_data" / "zone" / "filelist_z010u.win32.bin"
    pack(game / "white_data" / "zone" / "white_z010u_img.win32.bin", index,
         [("sound\\loc014\\hum.win32.scd", b"s" * 64)])
    pack(game / "white_data" / "sys" / "white_imgu.win32.bin",
         game / "white_data" / "sys" / "filelistu.win32.bin",
         [("zone\\filelist_z010u.win32.bin", index.read_bytes())])
    index.unlink()

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", skip_space_check=True)

    assert not result.ok
    assert "no backgrounds" in " ".join(result.errors).lower()


def test_a_step_cannot_ask_to_run_outside_the_mod_folder(tmp_path):
    """
    cwd exists for the zone imports and takes a path built from a plan. The
    plan comes from data that came out of an archive somebody downloaded.
    """
    from tests.test_hd_runner import stub

    support = tmp_path / "SupportFiles"
    support.mkdir()
    stub(support, hd_runner.TOOLS["pack"], "import sys\nsys.exit(0)\n")

    step = hd_runner.Step(tool="pack", args=["-i"], label="escape",
                          cwd="..\\..\\somewhere else")
    with pytest.raises(hd_runner.RunnerError, match="outside the mod folder"):
        hd_runner.run_step(step, tmp_path, support)


def test_what_a_zone_category_costs_comes_off_disk(world):
    """
    not out of the scr index, where none of it lives. The blob is the thing
    that gets copied, unpacked and written back, so its size is the real one.
    """
    from patcher.mods.hd_preflight import zone_sizes

    game, root, data, _ = world
    blob = game / "white_data" / "zone" / "white_z010u_img.win32.bin"

    sizes = zone_sizes(game, "u", data, ZONES)

    assert sizes["zones"] == blob.stat().st_size
    # and a game with none of them says nothing rather than zero, because a
    # zero beside a category reads as free
    blob.unlink()
    assert "zones" not in zone_sizes(game, "u", data, ZONES)


def test_an_installed_zone_shows_up_as_applied(world):
    """
    the badge, and it cannot come from the scr index either. The blob against
    the backup's copy of it is the same diff one archive over.
    """
    from patcher.mods.hd_state import zones_installed

    game, root, data, support = world
    store = game / ".undo" / "hdproject"

    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", skip_space_check=True)

    assert zones_installed(game, "u", store, data, ZONES[0])

    # and once it has been put back, it stops claiming to be there
    prep = prepare(game, root, game / ".undo")
    prep.extra = hd_zones.zone_archives(game, "u", ["z010"])
    restore(prep)
    assert not zones_installed(game, "u", store, data, ZONES[0])


def test_a_game_with_no_backup_of_a_zone_is_not_called_modded(world):
    """
    nothing to compare against is not the same as nothing installed, and
    guessing here puts an APPLIED badge on a vanilla game
    """
    from patcher.mods.hd_state import zones_installed

    game, root, data, _ = world
    empty = game / ".undo" / "hdproject"
    empty.mkdir(parents=True)

    assert not zones_installed(game, "u", empty, data, ZONES[0])


def test_a_zone_archive_that_is_the_wrong_size_makes_the_backup_suspect(world):
    """
    the same evidence VANILLA_PCS is, one archive over. Their Setup.bat carries
    the byte size of all twenty and this reads the same numbers, so it is
    measured rather than inferred from what some folder implies.
    """
    from patcher.mods.hd_preflight import VANILLA_ZONES, zones_already_modded

    game, root, _, _ = world
    zone_dir = game / "white_data" / "zone"

    # vanilla: the file is the size the table says
    name = "white_z010u_img.win32.bin"
    (zone_dir / name).write_bytes(b"\x00" * VANILLA_ZONES[name])
    assert zones_already_modded(game, "u") == []

    (zone_dir / name).write_bytes(b"\x00" * (VANILLA_ZONES[name] + 4096))
    found = zones_already_modded(game, "u")
    assert [n for n, _, _ in found] == [name]

    doubt = hd_install.suspect_backup(root, prepare(game, root, game / ".undo"))
    assert "zone archives" in doubt and "z010u" in doubt


def test_the_japanese_zones_are_not_measured_against_the_english_ones(world):
    """
    both languages are in the table and their sizes differ by tens of
    megabytes, so mixing them would call every Japanese install modded
    """
    from patcher.mods.hd_preflight import VANILLA_ZONES, zones_already_modded

    game, _, _, _ = world
    zone_dir = game / "white_data" / "zone"
    (zone_dir / "white_z010u_img.win32.bin").write_bytes(
        b"\x00" * VANILLA_ZONES["white_z010u_img.win32.bin"])
    (zone_dir / "white_z010c_img.win32.bin").write_bytes(
        b"\x00" * VANILLA_ZONES["white_z010c_img.win32.bin"])

    assert VANILLA_ZONES["white_z010u_img.win32.bin"] != \
        VANILLA_ZONES["white_z010c_img.win32.bin"]
    assert zones_already_modded(game, "u") == []
    assert zones_already_modded(game, "c") == []


def test_the_mod_side_folders_exist_before_the_injector_writes_into_them(world):
    """
    the live one. FFXIII_HDProject writes into models\\mod\\...\\bin and does
    not create it, and the seed that builds that skeleton runs before any zone
    has been unpacked, so models\\orig\\zoneu is not there for it to mirror.
    A real run died on its first block:

        Unable to open output files models\\mod\\zoneu\\z002\\bg\\loc006\\
        m000\\bin\\block000.win32.imgb.  Exiting.

    The stub refuses the same way, so this is the crash and not a paraphrase of
    it. The folders themselves are gone by the end of a run now that each zone
    releases its working copies, so the evidence is that nothing reported
    having nowhere to write.
    """
    game, root, data, support = world

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", skip_space_check=True)

    assert result.ok, result.reason
    assert not result.skipped, f"a block could not be written: {result.skipped}"
    assert result.applied, "nothing was installed at all, so this proves nothing"


def test_a_block_whose_tools_failed_is_not_imported(world):
    """
    their GENERATEZONE wraps the import in `IF !success! EQU 1`. Run
    unconditionally it reached a block the injector had just failed on, asked
    to run in the folder the injector therefore never made, and took the whole
    2536 step run down with it.
    """
    from tests.test_hd_install import DEFAULT_TOOLS
    from tests.test_hd_runner import stub

    game, root, data, support = world
    # an injector that refuses everything, the way it does for a block with no
    # HD art and a mod folder it cannot write into
    stub(support, hd_runner.TOOLS["inject"],
         "import sys\nprint('Unable to open output files.  Exiting.', flush=True)\n"
         "sys.exit(4294967292 - (1 << 32))\n")
    stub(support, hd_runner.TOOLS["models"],
         "import sys\nprint('nope', flush=True)\nsys.exit(2)\n")

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", skip_space_check=True)

    assert not result.ok, "every block failed and the run called itself a success"
    ran = [label for label, _ in result.skipped]
    assert not any("into the zone archive" in label for label in ran), \
        "a block that produced nothing was still imported"


def test_a_zone_the_mod_ships_nothing_for_is_not_written_to_the_game(world):
    """
    their `if %updated% EQU 0 GOTO:EOF`. A zone where no block had HD art still
    had its seeded copy placed over the game's own file and its untouched index
    imported back into white_img<v>: twenty pointless rewrites of a two
    gigabyte archive, each one a window in which a crash leaves it half written.
    """
    from tests.test_hd_runner import stub

    game, root, data, support = world
    sys_dir, zone_dir = game / "white_data" / "sys", game / "white_data" / "zone"
    before = {p: p.read_bytes() for p in (*sys_dir.iterdir(), *zone_dir.iterdir())}

    # exit 0 with nothing on stderr is the injector saying it looked and there
    # was no HD texture, which is true of 102 of the 891 ids in their own lists
    stub(support, hd_runner.TOOLS["inject"], "import sys\nsys.exit(0)\n")
    stub(support, hd_runner.TOOLS["models"],
         "import sys\nprint('no art', flush=True)\nsys.exit(2)\n")

    install(game, root, data, support, ZONES, {"zones"},
            store=game / ".undo", skip_space_check=True)

    for path, was in before.items():
        assert path.read_bytes() == was, f"{path.name} was rewritten for nothing"


def test_a_step_that_cannot_start_does_not_end_an_optional_run(tmp_path):
    """
    run_step raises before any of the optional handling, so a missing working
    directory escaped as a RunnerError and ended the run at step 69 of 2536
    """
    from tests.test_hd_runner import stub

    support = tmp_path / "SupportFiles"
    support.mkdir()
    stub(support, hd_runner.TOOLS["pack"], "import sys\nsys.exit(0)\n")

    said = []
    steps = [hd_runner.Step(tool="pack", args=["-i"], label="nowhere",
                            cwd="models\\mod\\gone", optional=True, payload=True),
             hd_runner.Step(tool="pack", args=["-x"], label="after it",
                            optional=True, payload=True)]
    result = hd_runner.run(steps, tmp_path, support,
                           on_line=lambda text, stream="out": said.append((stream, text)))

    assert result.steps_run == 2, "the run stopped at the step that could not start"
    assert any("nowhere" in label for label, _ in result.skipped)
    assert any("not there" in text for stream, text in said if stream == hd_runner.ERR), \
        "it carried on without saying why the step never ran"

    # and a required step still takes the run down, because that is what
    # required means
    steps[0].optional = False
    with pytest.raises(hd_runner.RunnerError, match="not there"):
        hd_runner.run(steps, tmp_path, support)


def test_an_import_is_judged_on_the_first_word_of_each_line():
    """
    ff13tool says "Added filelist_z002u.win32.bin (copied)", never a bare
    "Added". Their LOADMODELFILE compares the first token because `for /f`
    with default delims hands %%a exactly that; matching whole lines meant
    every import failed, and it killed a live run at step 189 of 2536 on one
    that had just done all four things correctly.
    """
    step = hd_runner.Step(tool="pack", args=["-i"], label="z002 index",
                          success=hd_runner.OK_IMPORTED)
    real = [
        "Game automatically set to FF13.",
        "File list processed. 14107 files (16 duplicates) listed in 99 file chunks.",
        "Added filelist_z002u.win32.bin (copied)",
        r"Updated E:\SteamLibrary\steamapps\common\FINAL FANTASY XIII\white_data\sys\white_imgu.win32.bin",
        r"Imported one file to E:\SteamLibrary\steamapps\common\FINAL FANTASY XIII\white_data\sys\white_imgu.win32.bin",
        r"Wrote E:\SteamLibrary\steamapps\common\FINAL FANTASY XIII\white_data\sys\filelistu.win32.bin",
    ]
    # exit 1, which is what it returns whatever happens
    assert hd_runner.succeeded(step, 1, lines=real)

    # and it still refuses a run that only did some of it
    assert not hd_runner.succeeded(step, 1, lines=real[:-1])
    assert not hd_runner.succeeded(step, 0, lines=["Game automatically set to FF13."])
    assert not hd_runner.succeeded(step, 0, lines=[])
    # a word inside a line is not the word starting it
    assert not hd_runner.succeeded(step, 1, lines=[
        "Nothing Added", "Nothing Updated", "Nothing Imported", "Nothing Wrote"])


def test_the_zone_index_import_reaches_the_game(world):
    """
    the step that failed. It is the last one of a zone and the only write that
    lands in the game's own archive, so a spurious refusal here is a run that
    stops after changing the game.
    """
    game, root, data, support = world

    result = install(game, root, data, support, ZONES, {"zones"},
                     store=game / ".undo", skip_space_check=True)

    assert result.ok, (result.failed and result.failed.label, result.errors)
    assert result.failed is None


def test_one_zone_of_twenty_is_a_stopped_run_not_an_installed_category(world, tmp_path):
    """
    the state a live run actually left: it died importing zone one of twenty,
    after that zone's blob was already in the game. Reading a single changed
    blob as "installed" locks the row and leaves nobody able to install the
    other nineteen.
    """
    from patcher.mods.hd_state import zones_installed

    game, root, data, _ = world
    zone_dir = game / "white_data" / "zone"
    store = game / ".undo" / "hdproject"
    store.mkdir(parents=True)

    names = [f"z{n:03d}" for n in (2, 3, 4, 6, 8, 10, 15, 16, 17, 18)]
    (data / "LoadHDEnvironments.txt").write_text(
        "".join(f"#:Zone{n}\nZ{int(n[1:])}\n" for n in names), encoding="utf-8")
    for zone in names:
        blob = f"white_{zone}u_img.win32.bin"
        (zone_dir / blob).write_bytes(b"v" * 1000)
        (store / blob).write_bytes(b"v" * 1000)

    category = {"id": "zones", "list": "LoadHDEnvironments.txt"}
    assert not zones_installed(game, "u", store, data, category), "vanilla reads as installed"

    # one done, nineteen to go
    (zone_dir / f"white_{names[0]}u_img.win32.bin").write_bytes(b"v" * 2000)
    assert not zones_installed(game, "u", store, data, category), \
        "a run that stopped on the first zone locked the whole category"

    # and a real install still reads as one
    for zone in names:
        (zone_dir / f"white_{zone}u_img.win32.bin").write_bytes(b"v" * 2000)
    assert zones_installed(game, "u", store, data, category)
