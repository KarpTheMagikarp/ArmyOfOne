"""
The kinds of mod the Nexus pages for the three games ship that the drawer
didn't take before: a Nova pack's records inside wdbpack.bin, LayeredFS's
mod_data, an index and archive dragged over the game's whole, a zone's
archive swapped whole, a ReShade add-on, and the ones nothing goes in for,
said for what they are.
"""

import hashlib

import pytest

from patcher.mods import game_files as gf
from patcher.mods import wpd
from patcher.mods.hd_zones import read_record
from patcher.paths import undo_store_for
from tests.hd2_world import write_pair
from tests.test_loader_mods import pe32
from tests.test_mod_drops import C108, world, zipped  # noqa: F401
from tests.test_patchdata import (DLC, WDBPACK, aerith, blob, live, lr, sums,  # noqa: F401
                                  wdbpack, zip_bytes)

NOVA = "[ModPackConfig]\r\nName=LR Seed 42\r\nVersion=1.0\r\nGameEntry=3\r\n"


def nova_pack(tmp, name="LRRando_Seed42-1-0.ncmp", records=None, whole=None, config=NOVA):
    files = {"modconfig.ini": config.encode("utf-16")}
    for record, data in (records or {"r_dlc.wdb": b"seeded dlc" * 40}).items():
        files[f"Data/db/resident/_wdbpack.bin/{record}"] = data
    for path, data in (whole or {}).items():
        files[f"Data/{path}"] = data
    path = tmp / name
    path.write_bytes(zip_bytes(files))
    return path


def other(game, letter: str) -> bytes:
    return live(game, letter, "db/resident/other.wdb")


def test_a_nova_packs_utf16_modconfig_names_it_and_its_game(lr):
    """Nova writes modconfig.ini UTF-16, which read as UTF-8 named the pack after its file"""
    bridge, game, tmp = lr
    done = bridge.install_mod("lr", str(nova_pack(tmp)))
    assert done["ok"], done
    assert done["modName"] == "LR Seed 42"
    wrong = nova_pack(tmp, "Other.ncmp", config=NOVA.replace("GameEntry=3", "GameEntry=2"))
    said = bridge.install_mod("lr", str(wrong))
    assert not said["ok"] and "FINAL FANTASY XIII-2" in said["error"]


def test_records_inside_wdbpack_go_in_with_the_packs_whole_files_as_one_mod(lr):
    bridge, game, tmp = lr
    before = sums(game)
    vanilla = live(game, "a")
    pack = nova_pack(tmp, records={"r_dlc.wdb": b"seeded dlc" * 40},
                     whole={"db/resident/other.wdb": blob("seeded other", 900)})
    done = bridge.install_mod("lr", str(pack))
    assert done["ok"], done
    assert "Its 1 record inside wdbpack.bin went into the game's copy" in done["message"]
    for letter in "av":
        assert live(game, letter) == wpd.patch(vanilla, {("r_dlc.wdb",): b"seeded dlc" * 40})
        assert other(game, letter) == blob("seeded other", 900)
    rows = [m for m in bridge.list_addons("lr")["mods"] if m.get("kind") == "files"]
    assert [m["name"] for m in rows] == ["LR Seed 42"]
    assert rows[0]["about"] == "2 game files in both voice sets"
    assert bridge.remove_addon("lr", "LR Seed 42", "files")["ok"]
    assert sums(game) == before


def test_records_for_one_voice_set_go_into_that_sets_copy_only(lr):
    bridge, game, tmp = lr
    vanilla = live(game, "v")
    path = tmp / "Voices.ncmp"
    path.write_bytes(zip_bytes({
        "modconfig.ini": NOVA.encode("utf-16"),
        "EN-Data/db/resident/_wdbpack.bin/r_dlc.wdb": b"english" * 30}))
    assert bridge.install_mod("lr", str(path))["ok"]
    assert wpd.read(live(game, "a"), ("r_dlc.wdb",)) == b"english" * 30
    assert live(game, "v") == vanilla


def test_two_different_files_for_one_record_say_so_and_change_nothing(lr):
    bridge, game, tmp = lr
    before = sums(game)
    path = tmp / "Twice.zip"
    path.write_bytes(zip_bytes({"One/db/resident/_wdbpack.bin/r_dlc.wdb": b"one" * 20,
                                "Two/db/resident/_wdbpack.bin/r_dlc.wdb": b"two" * 20}))
    said = bridge.install_mod("lr", str(path))
    assert not said["ok"] and "2 different files for r_dlc.wdb inside" in said["error"]
    assert sums(game) == before


def test_a_mixed_pack_over_a_patcher_is_built_again_whole_when_that_one_comes_off(lr):
    """
    captured() kept only a mod's records, so one with whole files besides went
    back on without them after the patcher under it came off
    """
    bridge, game, tmp = lr
    vanilla = live(game, "a")
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    pack = nova_pack(tmp, records={"r_item.wdb": b"seeded items" * 9},
                     whole={"db/resident/other.wdb": blob("seeded other", 900)})
    assert bridge.install_mod("lr", str(pack))["ok"]
    gone = bridge.remove_addon("lr", "Aerith Garb Restauration", "files")
    assert gone["ok"] and "LR Seed 42 went back on over it" in gone["message"], gone
    for letter in "av":
        assert live(game, letter) == wpd.patch(vanilla, {("r_item.wdb",): b"seeded items" * 9})
        assert wpd.read(live(game, letter), ("r_dlc.wdb",)) == DLC
        assert other(game, letter) == blob("seeded other", 900)
    assert bridge.remove_addon("lr", "LR Seed 42", "files")["ok"]
    for letter in "av":
        assert live(game, letter) == vanilla


def test_layeredfs_goes_in_by_its_mod_data_without_its_loader(lr):
    """
    LR's dinput8.dll is Savior's Patch's, so LayeredFS's took the whole mod
    down with a refusal. What it reads out of mod_data goes in the archives
    """
    bridge, game, tmp = lr
    path = tmp / "LR Gameplay Tweaks-30-1-0.zip"
    path.write_bytes(zip_bytes({
        "dinput8.dll": pe32(), "layeredLS.ini": b"[LayeredFS]\nEnabled=1\n",
        "mod_data/db/resident/other.wdb": blob("tweaked", 900)}))
    done = bridge.install_mod("lr", str(path))
    assert done["ok"] and done["summary"] == "Installed LR Gameplay Tweaks.", done
    assert "dinput8.dll and layeredLS.ini, LayeredFS's loader, which isn't needed" in done["message"]
    assert not (game / "dinput8.dll").exists()
    assert other(game, "a") == blob("tweaked", 900)
    alone = tmp / "LayeredFS-1-0.zip"
    alone.write_bytes(zip_bytes({"dinput8.dll": pe32(), "layeredLS.ini": b"[x]",
                                 "mod_data/readme.txt": b"put mods here"}))
    said = bridge.install_mod("lr", str(alone))
    assert not said["ok"] and "is LayeredFS" in said["error"]


def mod_pair(tmp, rows, letter="2a"):
    where = tmp / f"pair-{letter}-{len(list(tmp.iterdir()))}"
    write_pair(where / f"white_img{letter}.win32.bin", where / f"filelist{letter}.win32.bin", rows)
    return {f"weiss_data/sys/white_img{letter}.win32.bin":
            (where / f"white_img{letter}.win32.bin").read_bytes(),
            f"weiss_data/sys/filelist{letter}.win32.bin":
            (where / f"filelist{letter}.win32.bin").read_bytes()}


def test_an_index_and_archive_shipped_whole_put_in_only_what_differs_from_steams(lr):
    """
    before, any mod made of whole archives was turned away. Now what differs
    goes in record by record, so a drawer mod already in the same archive
    keeps its file
    """
    bridge, game, tmp = lr
    before = sums(game)
    vanilla = live(game, "a")
    records = tmp / "Records.zip"
    records.write_bytes(zip_bytes({"db/resident/_wdbpack.bin/r_item.wdb": b"mine" * 30}))
    assert bridge.install_mod("lr", str(records))["ok"]
    # Steam's wdbpack.bin and a new other.wdb, the way a mod packed from a
    # clean game ships them
    drop = tmp / "Rebalanced-14-1-0.zip"
    drop.write_bytes(zip_bytes({**mod_pair(tmp, [("db/resident/other.wdb", blob("rebal", 950)),
                                                 (WDBPACK, wdbpack())]),
                                "readme.txt": b"drag into weiss_data\\sys"}))
    done = bridge.install_mod("lr", str(drop))
    assert done["ok"], done
    assert done["message"].startswith("Put in only what its white_img2a.win32.bin changes "
                                      "from Steam's, record by record")
    assert other(game, "a") == blob("rebal", 950)
    assert wpd.read(live(game, "a"), ("r_item.wdb",)) == b"mine" * 30
    assert bridge.remove_addon("lr", "Rebalanced", "files")["ok"]
    assert bridge.remove_addon("lr", "Records", "files")["ok"]
    assert live(game, "a") == vanilla and sums(game) == before


def test_whole_archives_that_change_nothing_or_come_without_their_index_say_so(lr):
    bridge, game, tmp = lr
    before = sums(game)
    same = tmp / "Same.zip"
    same.write_bytes(zip_bytes(mod_pair(tmp, [("db/resident/other.wdb", blob("othera", 900)),
                                              (WDBPACK, wdbpack())])))
    said = bridge.install_mod("lr", str(same))
    assert not said["ok"] and "nothing Steam's don't" in said["error"]
    half = tmp / "Half.zip"
    half.write_bytes(zip_bytes({k: v for k, v in mod_pair(
        tmp, [("db/resident/other.wdb", blob("x", 950)), (WDBPACK, wdbpack())]).items()
        if "white_img" in k}))
    said = bridge.install_mod("lr", str(half))
    assert not said["ok"]
    assert "the filelist2a.win32.bin that says what's in it isn't with it" in said["error"]
    more = tmp / "More.zip"
    more.write_bytes(zip_bytes(mod_pair(tmp, [("db/resident/other.wdb", blob("othera", 900)),
                                              (WDBPACK, wdbpack()),
                                              ("db/resident/brand_new.wdb", b"new" * 10)])))
    said = bridge.install_mod("lr", str(more))
    assert not said["ok"] and "lists 1 file this game's doesn't" in said["error"]
    assert sums(game) == before


def test_a_zones_archive_is_the_hd_projects_on_the_games_it_builds_into(world):
    """
    swapped in under the HD Project, it'd be what its backup calls vanilla, so
    on XIII and XIII-2 a whole zone archive stays out
    """
    bridge, game, tmp = world
    blob_path = game / "alba_data" / "zone" / "white_z0010u_img.win32.bin"
    steam = blob_path.read_bytes()
    drop = zipped(tmp, "BGM Volume-2-1-0.zip",
                  {"alba_data/zone/white_z0010u_img.win32.bin": steam + b"louder" * 50})
    said = bridge.install_mod("ff13-2", str(drop))
    assert not said["ok"] and "the HD Project's to build into" in said["error"], said
    assert blob_path.read_bytes() == steam


def lr_zone(game):
    """a zone for the stand-in LR: its archive in weiss_data/zone, its index inside white_imga"""
    from tests.hd2_world import index_bytes, pack_rows

    rows = [(f"bg/loc{n}/bin/loc{n}.win32.imgb", blob(f"zone art {n}", 2500)) for n in range(4)]
    data, entries = pack_rows(rows)
    blob_path = game / "weiss_data" / "zone" / "white_z0120a_img.win32.bin"
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(data)
    sys_dir = game / "weiss_data" / "sys"
    write_pair(sys_dir / "white_imga.win32.bin", sys_dir / "filelista.win32.bin",
               [("chr/pc/c000/bin/c000.win32.trb", blob("c000a", 1200)),
                ("zone/filelist_z0120a.win32.bin", index_bytes(entries, sealed=True))])
    return blob_path


def test_lrs_zone_archive_swaps_whole_when_it_fits_the_index_the_game_keeps(lr):
    bridge, game, tmp = lr
    blob_path = lr_zone(game)
    steam = blob_path.read_bytes()
    before = sums(game)
    # where the index says everything is, with more on the end, the way a
    # music volume mod leaves it
    louder = tmp / "BGM Volume-2-1-0.zip"
    louder.write_bytes(zip_bytes({"weiss_data/zone/white_z0120a_img.win32.bin":
                                  steam + b"louder" * 400}))
    done = bridge.install_mod("lr", str(louder))
    assert done["ok"] and done["kind"] == "disk", done
    assert blob_path.read_bytes() == steam + b"louder" * 400
    assert bridge.remove_addon("lr", "BGM Volume", "disk")["ok"]
    assert sums(game) == before
    short = tmp / "Cut Short-3-1-0.zip"
    short.write_bytes(zip_bytes({"weiss_data/zone/white_z0120a_img.win32.bin": steam[:3000]}))
    said = bridge.install_mod("lr", str(short))
    assert not said["ok"] and "doesn't fit the index the game keeps for that zone" in \
        said["error"], said
    assert sums(game) == before
    both = tmp / "Zone Remix-4-1-0.zip"
    both.write_bytes(zip_bytes({"weiss_data/zone/white_z0120a_img.win32.bin": steam + b"x",
                                "zone/filelist_z0120a.win32.bin": b"their own index" * 20}))
    said = bridge.install_mod("lr", str(both))
    assert not said["ok"] and "comes with its own index for that zone" in said["error"], said
    assert sums(game) == before


def test_a_big_loose_file_streams_in_rather_than_being_read_whole(lr, monkeypatch):
    bridge, game, tmp = lr
    steam = lr_zone(game).read_bytes()
    drop = tmp / "BGM Volume-2-1-0.zip"
    drop.write_bytes(zip_bytes({"weiss_data/zone/white_z0120a_img.win32.bin":
                                steam + b"louder" * 400}))
    real = gf.Source.read

    def read(self, member):
        assert not member.endswith("_img.win32.bin"), "read whole"
        return real(self, member)

    monkeypatch.setattr(gf.Source, "read", read)
    assert bridge.install_mod("lr", str(drop))["ok"]


@pytest.mark.parametrize("files, said", [
    ({"FFXIII-2.CT": b"<CheatTable/>"}, "is a Cheat Engine table, which Cheat Engine opens"),
    ({"Hair.tpf": b"tpf", "readme.txt": b"use texmod"}, "is a TexMod package"),
    ({"Crystarium.xlsx": b"PK\x03\x04sheet"}, "is a spreadsheet for working out edits by hand"),
    ({"4gb_patch.exe": pe32(dll=False)}, "is a 4GB patcher, 4gb_patch.exe. Apply's 4GB patch"),
    ({"FFXIII2FanfareMod.exe": pe32(managed=True, dll=False)},
     "or as a Nova Chrysalia modpack (a .ncmp, or the download marked Nova)"),
    ({"ShaderToggler.addon64": pe32(machine=0x8664)}, "built for 64-bit"),
    ({"alba_data/prog/win/bin/NovaFix.ini": b"[Graphics]\n"},
     "is NovaFix's settings, which the Settings drawer writes for you"),
])
def test_what_runs_beside_the_game_or_isnt_for_it_is_said_for_what_it_is(world, files, said):
    bridge, game, tmp = world
    done = bridge.install_mod("ff13-2", str(zipped(tmp, "Thing-1-0.zip", files)))
    assert not done["ok"] and said in done["error"], done


def test_a_save_says_where_saves_go(world):
    bridge, game, tmp = world
    said = bridge.install_mod("ff13-2", str(zipped(tmp, "Save-1-0.zip",
                                                   {"ff13-2-00.dat": b"s" * 99})))
    assert "Steam\\userdata\\<your id>\\292140\\remote" in said["error"]
    assert "Documents" not in said["error"]


def addon_drop(tmp):
    return zipped(tmp, "ShaderToggler-36-1-0.zip", {
        "ShaderToggler/ShaderToggler.addon32": pe32(),
        "ShaderToggler/ShaderToggler.ini": b"[Group0]\nName=HUD\n",
        "ShaderToggler/ReShade.ini": b"[GENERAL]\n", "readme.txt": b"needs addon reshade"})


def test_a_reshade_add_on_goes_beside_the_reshade_thats_in_and_comes_off(world):
    bridge, game, tmp = world
    said = bridge.install_mod("ff13-2", str(addon_drop(tmp)))
    assert not said["ok"] and "no ReShade for it yet" in said["error"]
    home = game / "alba_data" / "prog" / "win" / "bin" / "ReShade"
    home.mkdir()
    (home / "d3d9.dll").write_bytes(pe32())
    (home / "ReShade.ini").write_bytes(b"[GENERAL]\nPresetPath=.\\Mine.ini\n")
    done = bridge.install_mod("ff13-2", str(addon_drop(tmp)))
    assert done["ok"] and "full add-on support" in done["message"], done
    assert sorted(p.name for p in home.iterdir()) == [
        "ReShade.ini", "ShaderToggler.addon32", "ShaderToggler.ini", "d3d9.dll"]
    assert (home / "ReShade.ini").read_bytes() == b"[GENERAL]\nPresetPath=.\\Mine.ini\n"
    assert bridge.remove_addon("ff13-2", "ShaderToggler", "loader")["ok"]
    assert sorted(p.name for p in home.iterdir()) == ["ReShade.ini", "d3d9.dll"]


def test_the_patchers_own_reshade_is_the_standard_build_and_says_so(world, monkeypatch):
    bridge, game, tmp = world

    class Ours:
        label = "Fabula-Lous Shader"
        bundled_files = [{"target": "ReShade/d3d9.dll"}]

    home = game / "alba_data" / "prog" / "win" / "bin" / "ReShade"
    home.mkdir()
    (home / "d3d9.dll").write_bytes(pe32())
    monkeypatch.setattr(type(bridge), "extras_in", lambda self, gid: [Ours()])
    said = bridge.install_mod("ff13-2", str(addon_drop(tmp)))
    assert not said["ok"] and "Fabula-Lous Shader's ReShade is the standard build" in said["error"]
    assert not (home / "ShaderToggler.addon32").exists()


def test_a_zip_with_a_whole_archive_beside_game_files_says_what_stayed_out(world):
    """whole archives weren't in left_out() at all, so a mod with both said nothing of them"""
    bridge, game, tmp = world
    drop = zipped(tmp, "Both-3-1-0.zip", {C108: b"dress" * 900,
                                         "extra/white_scru.win32.bin": b"scr" * 100})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"] and "white_scru.win32.bin, a whole archive this can't put in" in \
        done["message"], done
    assert done["summary"] == "Installed Both. Some of it didn't go in."


def test_whole_archive_reading_hands_over_a_file_to_seek_through(tmp_path):
    """a stored member is its stretch of the zip, a deflated one is copied out once"""
    import zipfile

    path = tmp_path / "Drop.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("stored.bin"), b"0123456789" * 1000)
        archive.writestr("deflated.bin", b"abcdefghij" * 1000, zipfile.ZIP_DEFLATED)
    with gf.Source(path) as source:
        for member, unit in (("stored.bin", b"0123456789"), ("deflated.bin", b"abcdefghij")):
            with source.opened(member) as handle:
                handle.seek(5005)
                assert handle.read(5) == (unit * 1000)[5005:5010]
    assert hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_container_another_archive_of_the_set_lists_too_only_goes_back_where_it_came_from(lr):
    """
    white_img2 holds the wdbpack.bin LR reads, and one the white_img of the
    same voice set lists as well isn't the copy the records were built from
    """
    bridge, game, tmp = lr
    sys_dir = game / "weiss_data" / "sys"
    write_pair(sys_dir / "white_imga.win32.bin", sys_dir / "filelista.win32.bin",
               [("chr/pc/c000/bin/c000.win32.trb", blob("c000a", 1200)), (WDBPACK, wdbpack())])
    first = read_record(sys_dir / "white_imga.win32.bin",
                        gf.entries(sys_dir / "filelista.win32.bin")[WDBPACK])
    assert bridge.install_mod("lr", str(nova_pack(tmp)))["ok"]
    assert wpd.read(live(game, "a"), ("r_dlc.wdb",)) == b"seeded dlc" * 40
    assert read_record(sys_dir / "white_imga.win32.bin",
                       gf.entries(sys_dir / "filelista.win32.bin")[WDBPACK]) == first


def test_a_record_their_tool_packed_again_the_same_underneath_isnt_a_change(lr, monkeypatch):
    """
    Steam's other.wdb deflated another way is still Steam's, and put in it
    would go over the drawer mod that has that file
    """
    import zlib

    bridge, game, tmp = lr
    mine = tmp / "Other Tweak.zip"
    mine.write_bytes(zip_bytes({"db/resident/other.wdb": blob("tweak", 900)}))
    assert bridge.install_mod("lr", str(mine))["ok"]
    real = zlib.compress
    with monkeypatch.context() as patched:
        # another deflate level: other bytes in the archive, the same file
        patched.setattr(zlib, "compress", lambda data, level=6: real(data, 1))
        files = mod_pair(tmp, [("db/resident/other.wdb", blob("othera", 900)),
                               (WDBPACK, wdbpack(dlc=b"rebalanced" * 30))])
    drop = tmp / "Repacked.zip"
    drop.write_bytes(zip_bytes(files))
    done = bridge.install_mod("lr", str(drop))
    assert done["ok"] and "1 game file into the English voice set" in done["message"], done
    record = next(r for r in gf.records(undo_store_for(game)) if r["name"] == "Repacked")
    assert [i["path"] for spec in record["pairs"] for i in spec["entries"]] == [WDBPACK]
    assert other(game, "a") == blob("tweak", 900)


def test_a_texture_tools_unpack_folder_isnt_taken_for_records(world):
    """a _c108.win32.trb folder of .dds is art to pack, as it always was"""
    bridge, game, tmp = world
    drop = zipped(tmp, "Skin-5-1-0.zip",
                  {"chr/pc/c108/bin/_c108.win32.trb/skin.dds": b"DDS " + b"x" * 400})
    said = bridge.install_mod("ff13-2", str(drop))
    assert not said["ok"] and "ships .dds images" in said["error"], said


def test_records_for_a_file_that_isnt_a_container_say_so(lr):
    bridge, game, tmp = lr
    before = sums(game)
    drop = tmp / "Rows.zip"
    drop.write_bytes(zip_bytes({"db/resident/_other.wdb/row_1": b"row" * 8}))
    said = bridge.install_mod("lr", str(drop))
    assert not said["ok"] and "isn't a container in this game" in said["error"], said
    assert sums(game) == before


def test_a_new_version_of_a_pack_with_records_doesnt_keep_the_old_ones(lr):
    """
    its container is built over the game as it is, so building it over the
    version going out would keep whatever records the new one dropped
    """
    bridge, game, tmp = lr
    vanilla = live(game, "a")
    first = nova_pack(tmp, "Seed-1.ncmp", records={"r_dlc.wdb": b"first dlc" * 20,
                                                   "r_item.wdb": b"first items" * 20})
    assert bridge.install_mod("lr", str(first))["ok"]
    second = nova_pack(tmp, "Seed-2.ncmp", records={"r_item.wdb": b"second items" * 20})
    done = bridge.install_mod("lr", str(second))
    assert done["ok"], done
    for letter in "av":
        assert live(game, letter) == wpd.patch(vanilla, {("r_item.wdb",): b"second items" * 20})
    assert [m["name"] for m in bridge.list_addons("lr")["mods"]
            if m.get("kind") == "files"] == ["LR Seed 42"]
    assert bridge.remove_addon("lr", "LR Seed 42", "files")["ok"]
    assert live(game, "a") == vanilla


def test_a_patcher_under_a_pack_wont_come_off_while_another_mod_is_over_the_packs_files(lr):
    """
    taking the patcher off builds the pack again on top, which would put its
    whole other.wdb back over the mod that went in over it
    """
    bridge, game, tmp = lr
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    pack = nova_pack(tmp, records={"r_item.wdb": b"seeded items" * 9},
                     whole={"db/resident/other.wdb": blob("pack other", 900)})
    assert bridge.install_mod("lr", str(pack))["ok"]
    later = tmp / "Other Tweak.zip"
    later.write_bytes(zip_bytes({"db/resident/other.wdb": blob("tweak on top", 900)}))
    assert bridge.install_mod("lr", str(later))["ok"]
    before = sums(game)
    said = bridge.remove_addon("lr", "Aerith Garb Restauration", "files")
    assert not said["ok"] and "Remove Other Tweak first" in said["error"], said
    assert sums(game) == before
    assert bridge.remove_addon("lr", "Other Tweak", "files")["ok"]
    gone = bridge.remove_addon("lr", "Aerith Garb Restauration", "files")
    assert gone["ok"] and "LR Seed 42 went back on over it" in gone["message"], gone
    assert other(game, "a") == blob("pack other", 900)


def test_a_new_version_when_the_archive_is_held_says_nothing_came_off(lr, monkeypatch):
    import patcher.mods.hd_install as hd_install

    bridge, game, tmp = lr
    first = nova_pack(tmp, "Seed-1.ncmp", records={"r_dlc.wdb": b"first dlc" * 20})
    assert bridge.install_mod("lr", str(first))["ok"]
    before = sums(game)
    monkeypatch.setattr(hd_install, "in_use", lambda paths: [("white_img2a.win32.bin", "busy")])
    said = bridge.install_mod("lr", str(nova_pack(tmp, "Seed-2.ncmp",
                                                  records={"r_item.wdb": b"second" * 20})))
    assert not said["ok"] and "came off first" not in said["error"], said
    assert sums(game) == before


def test_loose_files_patched_from_an_xdelta_leave_nothing_behind_in_the_undo_store(tmp_path):
    from tests.helpers import vcdiff_patch
    from patcher.mods import deltas

    original = tmp_path / "op_movie.win32.bik"
    original.write_bytes(b"the game's own opening, " * 400)
    # two windows, so the second's written after the first's on the disk
    patch = vcdiff_patch(original.read_bytes(), [[("copy", 0, 4800)],
                                                 [("add", b"recut " * 300)]])
    wanted = original.read_bytes()[:4800] + b"recut " * 300
    with (tmp_path / "out").open("w+b") as out:
        assert deltas.whole(patch, original, out) == len(wanted)
    assert (tmp_path / "out").read_bytes() == wanted
    spill = deltas.spill(tmp_path / "game")
    assert spill.parent == undo_store_for(tmp_path / "game") / "work"
    assert spill.name.startswith("delta-")


def test_a_second_mod_by_the_same_name_keeps_its_loose_files_to_itself(world):
    """its archive files went in as Pack (2) and its loose ones as Pack, the other mod's"""
    bridge, game, tmp = world
    movies = game / "alba_data" / "movie"
    movies.mkdir(parents=True)
    (movies / "op_movie.win32.bik").write_bytes(b"opening" * 300)
    (game / "alba_data" / "dlc" / "0000001").mkdir(parents=True)
    first = zipped(tmp, "Pack.zip", {C108: b"dress" * 900,
                                     "alba_data/movie/op_movie.win32.bik": b"first movie" * 99})
    assert bridge.install_mod("ff13-2", str(first))["ok"]
    (tmp / "b").mkdir()
    second = zipped(tmp / "b", "Pack.zip", {"chr/mon/m001/bin/m001.win32.imgb": b"other" * 900,
                                            "alba_data/dlc/0000001/extra.win32.bin": b"dlc"})
    done = bridge.install_mod("ff13-2", str(second))
    assert done["ok"] and done["modName"] == "Pack (2)", done
    assert (movies / "op_movie.win32.bik").read_bytes() == b"first movie" * 99
    assert bridge.remove_addon("ff13-2", "Pack (2)")["ok"]
    assert not (game / "alba_data" / "dlc" / "0000001" / "extra.win32.bin").exists()
    assert (movies / "op_movie.win32.bik").read_bytes() == b"first movie" * 99
    # and one with nothing but loose files is another mod too, not the first's update
    (tmp / "c").mkdir()
    third = zipped(tmp / "c", "Pack.zip", {"alba_data/dlc/0000001/extra.win32.bin": b"dlc"})
    done = bridge.install_mod("ff13-2", str(third))
    assert done["ok"] and done["modName"] == "Pack (2)", done
    assert (movies / "op_movie.win32.bik").read_bytes() == b"first movie" * 99
    assert bridge.remove_addon("ff13-2", "Pack")["ok"]
    assert (game / "alba_data" / "dlc" / "0000001" / "extra.win32.bin").exists()


def test_a_fomod_whose_files_come_in_versions_asks_once_and_installs(world):
    bridge, game, tmp = world
    xml = (b'<config><moduleName>Dress</moduleName><requiredInstallFiles>'
           b'<folder source="Base" destination=""/><folder source="Alt" destination="extra"/>'
           b'</requiredInstallFiles></config>')
    drop = zipped(tmp, "Dress-9-1-0.zip", {"fomod/ModuleConfig.xml": xml,
                                         f"Base/{C108}": b"base" * 900,
                                         f"Alt/{C108}": b"alt" * 900})
    said = bridge.install_mod("ff13-2", str(drop))
    assert said["kind"] == "versions" and len(said["versions"]) == 2, said
    done = bridge.install_mod("ff13-2", str(drop), version=said["versions"][-1])
    assert done["ok"], done


def test_a_fomod_pick_is_kept_under_the_name_its_download_has_it_by(world):
    bridge, game, tmp = world
    xml = (b'<config><moduleName>Dress</moduleName><requiredInstallFiles>'
           b'<folder source="Option A" destination="alba_data"/>'
           b'</requiredInstallFiles></config>')
    drop = zipped(tmp, "Dress-9-1-0.zip", {"fomod/ModuleConfig.xml": xml,
                                         f"Option A/{C108}": b"red dress" * 900})
    assert bridge.install_mod("ff13-2", str(drop))["ok"]
    record = next(r for r in gf.records(undo_store_for(game)) if r["name"] == "Dress")
    members = {i["member"] for spec in record["pairs"] for i in spec["entries"]}
    with gf.Source(record["source"]) as download:
        assert members and all(download.has(m) for m in members), members


def test_the_full_revert_says_which_part_of_a_mod_came_off(world, monkeypatch):
    """a mod's archive files and its loose ones share its name, and a retry of one read as both"""
    from patcher import revert
    from patcher.manifest import load_manifest
    from patcher.mods import disk_files
    from tests.test_mod_drops import MANIFESTS

    bridge, game, tmp = world
    movies = game / "alba_data" / "movie"
    movies.mkdir(parents=True)
    (movies / "op_movie.win32.bik").write_bytes(b"opening" * 300)
    drop = zipped(tmp, "Pack.zip", {C108: b"dress" * 900,
                                    "alba_data/movie/op_movie.win32.bik": b"first movie" * 99})
    assert bridge.install_mod("ff13-2", str(drop))["ok"]
    real, calls = disk_files.remove, []

    def flaky(*args, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise disk_files.DiskError("op_movie.win32.bik is open in another program")
        return real(*args, **kw)

    def stuck(*args, **kw):
        raise gf.FilesError("white_imgu.win32.bin can't be opened for writing")

    monkeypatch.setattr(disk_files, "remove", flaky)
    monkeypatch.setattr(gf, "remove", stuck)
    results = revert.revert_all(game, load_manifest(MANIFESTS / "ffxiii2.json"),
                                tmp / ".state" / "ffxiii2.json")
    mine = {r.patch_id: r.ok for r in results if r.label == "Pack"}
    assert mine == {"files:Pack": False, "disk:Pack": True}, mine


def test_a_loose_file_from_an_xdelta_goes_in_and_its_decoded_copy_is_cleared(world):
    from tests.helpers import vcdiff_patch

    bridge, game, tmp = world
    movie = game / "alba_data" / "movie" / "op_movie.win32.bik"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"the game's own opening, " * 400)
    patch = vcdiff_patch(movie.read_bytes(), [[("copy", 0, 4000), ("add", b"a new ending")]])
    drop = zipped(tmp, "Recut-1-0.zip", {"op_movie.win32.bik.xdelta": patch})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    assert movie.read_bytes() == (b"the game's own opening, " * 400)[:4000] + b"a new ending"
    work = undo_store_for(game) / "work"
    assert not list(work.glob("delta-*")) if work.is_dir() else True


def test_a_patcher_dropped_again_under_a_refusal_keeps_its_dlc_files(lr):
    """
    the old version was counted as off before the Remove that refused, and a
    failed update then took its DLC files with its records still in
    """
    bridge, game, tmp = lr
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    pack = nova_pack(tmp, records={"r_item.wdb": b"seeded items" * 9},
                     whole={"db/resident/other.wdb": blob("pack other", 900)})
    assert bridge.install_mod("lr", str(pack))["ok"]
    later = tmp / "Other Tweak.zip"
    later.write_bytes(zip_bytes({"db/resident/other.wdb": blob("tweak on top", 900)}))
    assert bridge.install_mod("lr", str(later))["ok"]
    before = sums(game)
    said = bridge.install_mod("lr", str(aerith(tmp)))
    assert not said["ok"] and "came off first" not in said["error"], said
    assert sums(game) == before
    for letter in "av":
        assert (game / "weiss_data" / "dlc" / "0000003" /
                f"white_p0000003img_{letter}.win32.bin").is_file()


def test_a_loader_dropped_again_keeps_its_data_rather_than_handing_it_to_loose_files(world):
    from patcher.mods import disk_files, loader_mods

    bridge, game, tmp = world
    drop = zipped(tmp, "HD Fonts-40-1-0.zip", {"version.dll": pe32(),
                                               "hd_textures/gui/font.dds": b"DDS " + b"f" * 99,
                                               "hd_textures/hash_database.txt": b"hashes"})
    assert bridge.install_mod("ff13-2", str(drop))["ok"]
    again = bridge.install_mod("ff13-2", str(drop))
    assert again["ok"] and again["kind"] == "loader", again
    store = undo_store_for(game)
    record = next(r for r in loader_mods.records(store) if r["name"] == "HD Fonts")
    assert sorted(record["files"]) == ["hd_textures/gui/font.dds",
                                       "hd_textures/hash_database.txt", "version.dll"]
    assert not disk_files.records(store)


def test_an_xdelta_for_a_file_apply_put_in_isnt_decoded_over_it(world):
    from tests.helpers import vcdiff_patch

    bridge, game, tmp = world
    shade = game / "alba_data" / "prog" / "win" / "bin" / "ReShade"
    shade.mkdir()
    (shade / "ReShade.ini").write_bytes(b"[GENERAL]\nPresetPath=.\\Fabula.ini\n" * 20)
    patch = vcdiff_patch((shade / "ReShade.ini").read_bytes(), [[("add", b"[GENERAL]\n")]])
    said = bridge.install_mod("ff13-2", str(zipped(tmp, "Tweak-1-0.zip",
                                                   {"ReShade.ini.xdelta": patch})))
    assert not said["ok"] and "which is Apply's or another mod's" in said["error"], said
    assert (shade / "ReShade.ini").read_bytes().startswith(b"[GENERAL]\nPresetPath")


def test_a_mod_of_other_files_by_a_loose_file_mods_name_is_another_mod(world):
    bridge, game, tmp = world
    movie = game / "alba_data" / "movie" / "op_movie.win32.bik"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"opening" * 300)
    assert bridge.install_mod("ff13-2", str(zipped(
        tmp, "Opening.zip", {"alba_data/movie/op_movie.win32.bik": b"recut" * 300})))["ok"]
    (tmp / "b").mkdir()
    done = bridge.install_mod("ff13-2", str(zipped(tmp / "b", "Opening.zip",
                                                   {C108: b"dress" * 900})))
    assert done["ok"] and done["modName"] == "Opening (2)", done
    rows = sorted((m["name"], m["kind"]) for m in bridge.list_addons("ff13-2")["mods"]
                  if m["kind"] in ("files", "disk"))
    assert rows == [("Opening", "disk"), ("Opening (2)", "files")]
    assert bridge.remove_addon("ff13-2", "Opening (2)", "files")["ok"]
    assert movie.read_bytes() == b"recut" * 300


def test_no_room_to_decode_or_a_full_drive_is_said_as_that(world, monkeypatch):
    import errno

    import patcher.download as download
    from patcher.mods import deltas
    from tests.helpers import vcdiff_patch

    bridge, game, tmp = world
    movie = game / "alba_data" / "movie" / "op_movie.win32.bik"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"the game's own opening, " * 400)
    drop = zipped(tmp, "Recut-1-0.zip", {"op_movie.win32.bik.xdelta": vcdiff_patch(
        movie.read_bytes(), [[("copy", 0, 4000), ("add", b"a new ending")]])})
    with monkeypatch.context() as patched:
        patched.setattr(download, "free_space", lambda path: 1 << 20)
        said = bridge.install_mod("ff13-2", str(drop))
    assert not said["ok"] and "GB free on the game's drive" in said["error"], said

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(deltas, "whole", full)
    said = bridge.install_mod("ff13-2", str(drop))
    assert not said["ok"] and "another version" not in said["error"], said


def test_a_patchers_dlc_over_a_loose_file_mod_names_that_mod(lr):
    bridge, game, tmp = lr
    dlc = "weiss_data/dlc/0000003/white_p0000003img_a.win32.bin"
    (game / dlc).write_bytes(blob("steam's", 200))
    assert bridge.install_mod("lr", str(zipped(tmp, "DLC Fix.zip", {dlc: b"fixed" * 40})))["ok"]
    said = bridge.install_mod("lr", str(aerith(tmp)))
    assert not said["ok"] and "DLC Fix" in said["error"], said
