"""
Surihix's LR patchers read instead of run (patcher/mods/patchdata.py), against
an LR built the way the Steam release is where it matters: a white_img2 per
voice set holding db/resident/wdbpack.bin, a WPD whose r_reward.wdb is one
too, LRFF13.exe in the game's root, and an empty weiss_data/dlc/0000003.
"""

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from patcher.mods import game_files as gf
from patcher.mods import loader_mods, wpd
from patcher.mods.hd_zones import read_record
from patcher.mods.wpd import Record
from patcher.paths import undo_store_for
from tests.hd2_world import write_pair
from tests.helpers import make_fake_pe

MANIFESTS = Path(__file__).resolve().parent.parent / "manifests"
WDBPACK = "db/resident/wdbpack.bin"


def blob(seed: str, size: int) -> bytes:
    return (hashlib.sha256(seed.encode()).hexdigest().encode() * (size // 64 + 1))[:size]


REWARDS = {f"rw_{n}": bytes([n % 256]) * 8 for n in range(100, 121)}
DLC = blob("r_dlc vanilla", 3001)


def reward_wdb(rows=None) -> bytes:
    rows = {**REWARDS, **(rows or {})}
    return wpd.build([Record(b"!!sheetname", b"", b"r_reward\x00")]
                     + [Record(name.encode(), b"", data) for name, data in rows.items()])


def wdbpack(dlc=DLC, rewards=None) -> bytes:
    return wpd.build([Record(b"r_dlc", b"wdb", dlc), Record(b"r_reward", b"wdb", reward_wdb(rewards)),
                      Record(b"r_item", b"wdb", blob("items", 777))])


def make_lr(common: Path, sets="av") -> Path:
    game = common / "LR"
    sys_dir = game / "weiss_data" / "sys"
    for letter in sets:
        write_pair(sys_dir / f"white_img2{letter}.win32.bin",
                   sys_dir / f"filelist2{letter}.win32.bin",
                   [("db/resident/other.wdb", blob("other" + letter, 900)), (WDBPACK, wdbpack())])
        write_pair(sys_dir / f"white_img{letter}.win32.bin", sys_dir / f"filelist{letter}.win32.bin",
                   [("chr/pc/c000/bin/c000.win32.trb", blob("c000" + letter, 1200))])
    (game / "weiss_data" / "dlc" / "0000003").mkdir(parents=True)
    (game / "LRFF13.exe").write_bytes(make_fake_pe(machine=0x14C))
    return game


@pytest.fixture
def lr(tmp_path, monkeypatch):
    library = tmp_path / "steam"
    game = make_lr(library / "steamapps" / "common")
    (library / "steamapps" / "appmanifest_345350.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"345350"\n\t"installdir"\t\t"LR"\n}\n')
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths={"ffxiii3": MANIFESTS / "ffxiii3.json"},
                      steam_library_paths=[library])
    return bridge, game, tmp_path


def zip_bytes(files: dict, method=zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", method) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


AERITH_DLC = blob("r_dlc with aerith", 3400)
DLC_FILES = {letter: {f"dlc/0000003/filelist_p0000003img_{letter}.win32.bin": blob("fl" + letter, 300),
                      f"dlc/0000003/white_p0000003img_{letter}.win32.bin": blob("wh" + letter, 5000)}
             for letter in "avj"}
OUTER = {"rw_102": b"\x66" * 8, "rw_117": b"\x77" * 8}


def aerith(tmp_path, name="Aerith_Garb_Restauration-23-1-1-1759690588.zip") -> Path:
    data = {f"{letter}/{rel}": raw for letter, files in DLC_FILES.items()
            for rel, raw in files.items()}
    data["db/resident/_wdbpack.bin/r_dlc.wdb"] = AERITH_DLC
    path = tmp_path / name
    path.write_bytes(zip_bytes({
        "LRAerithGrabRestoration.exe": make_fake_pe(machine=0x14C),
        "WPDtool.dll": b"MZ not really", "WhiteBinTools.dll": b"MZ not really",
        "PatchData.bin": zip_bytes(data)}))
    return path


def outerworld(tmp_path, name="FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip") -> Path:
    data = {f"db/resident/_wdbpack.bin/_r_reward.wdb/{row}": raw for row, raw in OUTER.items()}
    path = tmp_path / name
    path.write_bytes(zip_bytes({"LROuterworldAddons.exe": make_fake_pe(machine=0x14C),
                                "WPDtool.dll": b"MZ not really",
                                "PatchData.bin": zip_bytes(data, zipfile.ZIP_STORED)}))
    return path


def live(game: Path, letter: str, path: str = WDBPACK) -> bytes:
    sys_dir = game / "weiss_data" / "sys"
    entry = gf.entries(sys_dir / f"filelist2{letter}.win32.bin")[path]
    return read_record(sys_dir / f"white_img2{letter}.win32.bin", entry)


def sums(game: Path) -> dict:
    """the game's own files, and not the records the drawer keeps beside it"""
    return {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(game.rglob("*"))
            if p.is_file() and ".patcher-undo" not in p.relative_to(game).parts}


def test_lrs_archives_are_pairs_with_xiiis_voice_letters(lr):
    _, game, _ = lr
    found = {(p.index.name, p.kind, p.voice) for p in gf.pairs(game)}
    assert found == {("filelist2a.win32.bin", "img2a", "u"), ("filelista.win32.bin", "imga", "u"),
                     ("filelist2v.win32.bin", "img2v", "c"), ("filelistv.win32.bin", "imgv", "c")}


def test_a_container_record_swapped_is_what_wpdtool_packs():
    """the layout WPDtool repacks to, checked against its own output under mono"""
    raw = wdbpack()
    assert wpd.build(wpd.parse(raw)) == raw
    patched = wpd.patch(raw, {("r_reward.wdb", "rw_102"): b"\x09" * 8, ("r_dlc.wdb",): b"new"})
    records = wpd.parse(patched)
    assert [r.file for r in records] == ["r_dlc.wdb", "r_reward.wdb", "r_item.wdb"]
    assert records[0].data == b"new"
    assert wpd.read(patched, ("r_reward.wdb", "rw_102")) == b"\x09" * 8
    assert wpd.read(patched, ("r_reward.wdb", "rw_103")) == REWARDS["rw_103"]
    assert records[2].data == blob("items", 777)
    # every record starts on 4 bytes, right after the last
    starts = [int.from_bytes(patched[16 + 32 * n + 16:16 + 32 * n + 20], "big") for n in range(3)]
    assert starts[0] == 16 + 32 * 3 and all(s % 4 == 0 for s in starts)
    with pytest.raises(wpd.WpdError):
        wpd.patch(raw, {("r_nothing.wdb",): b"x"})


@pytest.mark.parametrize("path,split", [
    ("db/resident/_wdbpack.bin/r_dlc.wdb", ("db/resident/wdbpack.bin", ("r_dlc.wdb",))),
    ("db/resident/_wdbpack.bin/_r_reward.wdb/rw_102",
     ("db/resident/wdbpack.bin", ("r_reward.wdb", "rw_102"))),
    ("_white_img2a.win32.bin/db/resident/_wdbpack.bin/r_dlc.wdb",
     ("_white_img2a.win32.bin/db/resident/wdbpack.bin", ("r_dlc.wdb",))),
    ("chr/pc/c000/bin/c000.win32.trb", None),
    ("db/_folder/plain/file.bin", None),
])
def test_a_path_through_wpdtools_folders_names_the_container_and_its_records(path, split):
    assert wpd.split(path) == split


def test_aerith_goes_in_both_voice_sets_with_its_dlc_and_comes_off_whole(lr):
    bridge, game, tmp = lr
    before = sums(game)
    done = bridge.install_mod("lr", str(aerith(tmp)))
    assert done["ok"], done
    assert done["title"] == "Aerith Garb Restauration"
    assert done["summary"] == "Installed Aerith Garb Restauration."
    assert "LRAerithGrabRestoration.exe" in done["message"]
    assert "wdbpack.bin in both voice sets" in done["message"]
    for letter in "av":
        assert wpd.read(live(game, letter), ("r_dlc.wdb",)) == AERITH_DLC
        assert wpd.read(live(game, letter), ("r_reward.wdb",)) == reward_wdb()
        for rel, raw in DLC_FILES[letter].items():
            assert (game / "weiss_data" / rel).read_bytes() == raw
    # the Japanese release's set isn't in this game, so its files aren't either
    assert not any(p.name.endswith("_j.win32.bin") for p in game.rglob("*"))
    # one mod in the list, its DLC files under it rather than a mod of their own
    listed = [m for m in bridge.list_addons("lr")["mods"] if m.get("kind") in ("files", "loader")]
    assert [(m["name"], m["kind"]) for m in listed] == [
        ("Aerith Garb Restauration", "files")]
    assert listed[0]["detail"] == "wdbpack.bin in both voice sets, and 4 DLC files"
    assert listed[0]["warn"] == "", "in as it went in, so Installed shows its name alone"

    gone = bridge.remove_addon("lr", "Aerith Garb Restauration", "files")
    assert gone["ok"], gone
    assert sums(game) == before
    assert not loader_mods.records(undo_store_for(game))


def test_its_records_go_into_what_the_game_has_now_and_both_stack(lr):
    bridge, game, tmp = lr
    vanilla = live(game, "a")
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    done = bridge.install_mod("lr", str(outerworld(tmp)))
    assert done["ok"], done
    assert done["title"] == "Outerworld Addons"
    both = wpd.patch(vanilla, {("r_dlc.wdb",): AERITH_DLC, ("r_reward.wdb", "rw_102"): OUTER["rw_102"],
                               ("r_reward.wdb", "rw_117"): OUTER["rw_117"]})
    for letter in "av":
        assert live(game, letter) == both
    names = [m["name"] for m in bridge.list_addons("lr")["mods"] if m.get("kind") == "files"]
    assert len(names) == 2


def test_the_one_underneath_comes_off_and_the_one_on_top_is_built_again_without_it(lr):
    bridge, game, tmp = lr
    vanilla = live(game, "v")
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    assert bridge.install_mod("lr", str(outerworld(tmp)))["ok"]
    gone = bridge.remove_addon("lr", "Aerith Garb Restauration", "files")
    assert gone["ok"], gone
    assert "Outerworld" in gone["message"] and "went back on over it" in gone["message"]
    only = wpd.patch(vanilla, {("r_reward.wdb", "rw_102"): OUTER["rw_102"],
                               ("r_reward.wdb", "rw_117"): OUTER["rw_117"]})
    for letter in "av":
        assert live(game, letter) == only
        assert wpd.read(live(game, letter), ("r_dlc.wdb",)) == DLC
        assert not (game / "weiss_data" / "dlc" / "0000003" /
                    f"white_p0000003img_{letter}.win32.bin").exists()
    names = [m["name"] for m in bridge.list_addons("lr")["mods"] if m.get("kind") == "files"]
    assert names == ["FFXIII-LR - Outerworld Addons"]
    # and that one still comes off back to the game as it was
    assert bridge.remove_addon("lr", names[0], "files")["ok"]
    for letter in "av":
        assert live(game, letter) == vanilla


def test_one_for_a_game_whose_container_is_another_version_says_so_and_stays(lr):
    bridge, game, tmp = lr
    sys_dir = game / "weiss_data" / "sys"
    for letter in "av":
        write_pair(sys_dir / f"white_img2{letter}.win32.bin",
                   sys_dir / f"filelist2{letter}.win32.bin",
                   [(WDBPACK, wpd.build([Record(b"r_item", b"wdb", b"x")]))])
    before = sums(game)
    done = bridge.install_mod("lr", str(outerworld(tmp)))
    assert not done["ok"]
    assert "has no record called r_reward.wdb" in done["error"]
    assert "isn't the version this was made for" in done["error"]
    assert sums(game) == before


def test_dropped_on_another_game_it_says_which_one_it_is_for(tmp_path, monkeypatch):
    from tests.test_mod_drops import library_with

    library, game, paths = library_with(tmp_path)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    done = bridge.install_mod("ff13-2", str(outerworld(tmp_path)))
    assert not done["ok"]
    assert "Lightning Returns" in done["error"]


def test_dropped_again_it_is_already_in_and_its_dlc_stays(lr):
    bridge, game, tmp = lr
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    again = bridge.install_mod("lr", str(aerith(tmp)))
    assert again["ok"], again
    for letter in "av":
        assert (game / "weiss_data" / "dlc" / "0000003" /
                f"white_p0000003img_{letter}.win32.bin").is_file()
    assert len([r for r in loader_mods.records(undo_store_for(game)) if r.get("part")]) == 1


def test_a_dlc_file_already_there_that_isnt_ours_stops_it_before_the_archives_change(lr):
    bridge, game, tmp = lr
    (game / "weiss_data" / "dlc" / "0000003" / "white_p0000003img_a.win32.bin").write_bytes(b"x")
    before = sums(game)
    done = bridge.install_mod("lr", str(aerith(tmp)))
    assert not done["ok"]
    assert sums(game) == before
    assert not gf.records(undo_store_for(game))


def test_the_full_revert_takes_its_dlc_files_off_with_it(lr):
    """
    its DLC files are a part of it that the list doesn't show on its own, so
    one left behind by the command line's --revert could never come off
    """
    from patcher.manifest import load_manifest
    from patcher.revert import revert_all

    bridge, game, tmp = lr
    before = sums(game)
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    assert bridge.install_mod("lr", str(outerworld(tmp)))["ok"]
    revert_all(game, load_manifest(MANIFESTS / "ffxiii3.json"), tmp / ".state" / "ffxiii3.json")
    assert sums(game) == before
    assert not loader_mods.records(undo_store_for(game))
    assert not gf.records(undo_store_for(game))


def test_dropped_again_under_another_it_goes_on_top_and_the_other_loses_its_records(lr):
    """
    an update goes in over the one on top of the old version and takes the old
    one off from under it, which the one between had built its copy on, so its
    records stayed in the game through that one's container
    """
    bridge, game, tmp = lr
    vanilla = live(game, "a")
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    assert bridge.install_mod("lr", str(outerworld(tmp)))["ok"]
    again = bridge.install_mod("lr", str(aerith(tmp)))
    assert again["ok"], again
    assert "Outerworld Addons went back on under it" in again["message"]
    both = wpd.patch(vanilla, {("r_dlc.wdb",): AERITH_DLC, ("r_reward.wdb", "rw_102"): OUTER["rw_102"],
                               ("r_reward.wdb", "rw_117"): OUTER["rw_117"]})
    assert live(game, "a") == both
    # now on top, so taking it off is the usual way, and leaves the other's alone
    assert bridge.remove_addon("lr", "Aerith Garb Restauration", "files")["ok"]
    only = wpd.patch(vanilla, {("r_reward.wdb", "rw_102"): OUTER["rw_102"],
                               ("r_reward.wdb", "rw_117"): OUTER["rw_117"]})
    for letter in "av":
        assert live(game, letter) == only
    assert not any(p.name.startswith("white_p0000003") for p in game.rglob("*"))


@pytest.fixture
def held_open(monkeypatch):
    """the next Remove can't delete one DLC file, the way antivirus or a preview pane holds one"""
    real = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == "white_p0000003img_a.win32.bin":
            raise PermissionError(13, "being used by another process", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    return lambda: monkeypatch.setattr(Path, "unlink", real)


def test_dropped_again_with_a_dlc_file_changed_it_says_so_and_stays_in(lr):
    bridge, game, tmp = lr
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    changed = game / "weiss_data" / "dlc" / "0000003" / "white_p0000003img_a.win32.bin"
    changed.write_bytes(b"x")
    again = bridge.install_mod("lr", str(aerith(tmp)))
    assert not again["ok"] and "has changed since" in again["error"]
    # what was in is in as it was
    assert [r["name"] for r in gf.records(undo_store_for(game))] == ["Aerith Garb Restauration"]
    for letter in "av":
        assert wpd.read(live(game, letter), ("r_dlc.wdb",)) == AERITH_DLC
    assert changed.read_bytes() == b"x"


def test_a_first_install_whose_archives_wont_take_it_leaves_no_dlc_files(lr, monkeypatch):
    bridge, game, tmp = lr
    before = sums(game)

    def held(pairs_in):
        raise gf.FilesError("white_img2a.win32.bin is open in another program")

    monkeypatch.setattr(gf, "_preflight", lambda *a, **k: held(a))
    done = bridge.install_mod("lr", str(aerith(tmp)))
    assert not done["ok"] and "open in another program" in done["error"]
    assert sums(game) == before
    assert not loader_mods.records(undo_store_for(game))


def test_dlc_files_a_remove_couldnt_take_are_listed_until_they_come_off(lr, held_open):
    bridge, game, tmp = lr
    before = sums(game)
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    gone = bridge.remove_addon("lr", "Aerith Garb Restauration", "files")
    held_open()
    assert gone["ok"], gone
    assert "Its DLC files couldn't all come off" in gone["message"]
    assert "couldn't come off:" not in gone["message"]
    # the archives are back, and what's left is a row that can still be taken off
    for letter in "av":
        assert wpd.read(live(game, letter), ("r_dlc.wdb",)) == DLC
    rows = [m for m in bridge.list_addons("lr")["mods"] if m.get("kind") == "files"]
    left = sorted(p.name for p in (game / "weiss_data" / "dlc" / "0000003").iterdir())
    assert "white_p0000003img_a.win32.bin" in left
    assert [(m["name"], m["about"]) for m in rows] == [
        ("Aerith Garb Restauration", f"{len(left)} DLC files left in weiss_data/dlc/0000003")]
    # names alone under Installed, and this is the one thing a bare name can't say
    assert rows[0]["warn"] == rows[0]["about"]
    again = bridge.remove_addon("lr", "Aerith Garb Restauration", "files")
    assert again["ok"] and "all that was left of it" in again["message"]
    assert sums(game) == before
    assert not loader_mods.records(undo_store_for(game))


def test_the_full_revert_takes_dlc_files_a_remove_left(lr, held_open):
    from patcher.manifest import load_manifest
    from patcher.revert import revert_all

    bridge, game, tmp = lr
    before = sums(game)
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    assert bridge.remove_addon("lr", "Aerith Garb Restauration", "files")["ok"]
    held_open()
    out = revert_all(game, load_manifest(MANIFESTS / "ffxiii3.json"), tmp / ".state" / "ffxiii3.json")
    assert [(r.label, r.ok) for r in out if r.patch_id.startswith("files:")] == [
        ("Aerith Garb Restauration", True)]
    assert sums(game) == before
    assert not loader_mods.records(undo_store_for(game))


def test_a_remove_cut_short_part_way_puts_back_what_came_off(lr, monkeypatch):
    bridge, game, tmp = lr
    assert bridge.install_mod("lr", str(outerworld(tmp)))["ok"]
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    both = live(game, "a")
    real, calls = gf._ready, []

    def ready(pairs_in):
        # Aerith on top comes off, then the game's started before Outerworld can
        calls.append(1)
        if len(calls) == 2:
            raise gf.FilesError("white_img2a.win32.bin is open in another program")
        return real(pairs_in)

    monkeypatch.setattr(gf, "_ready", ready)
    gone = bridge.remove_addon("lr", "FFXIII-LR - Outerworld Addons", "files")
    monkeypatch.setattr(gf, "_ready", real)
    assert not gone["ok"] and "open in another program" in gone["error"]
    assert sorted(r["name"] for r in gf.records(undo_store_for(game))) == [
        "Aerith Garb Restauration", "FFXIII-LR - Outerworld Addons"]
    for letter in "av":
        assert live(game, letter) == both
        assert (game / "weiss_data" / "dlc" / "0000003" /
                f"white_p0000003img_{letter}.win32.bin").is_file()


def outer_remix(tmp_path, rows) -> Path:
    data = {f"db/resident/_wdbpack.bin/_r_reward.wdb/{row}": raw for row, raw in rows.items()}
    path = tmp_path / "FFXIII-LR_-_Outerworld_Addons_Remix-23-2-1-1759699999.zip"
    path.write_bytes(zip_bytes({"LROuterworldAddons.exe": make_fake_pe(machine=0x14C),
                                "PatchData.bin": zip_bytes(data, zipfile.ZIP_STORED)}))
    return path


def test_the_one_between_goes_back_on_with_its_own_records_not_the_top_ones(lr):
    bridge, game, tmp = lr
    vanilla = live(game, "a")
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    assert bridge.install_mod("lr", str(outerworld(tmp)))["ok"]
    assert bridge.install_mod("lr", str(outer_remix(tmp, {"rw_102": b"\x99" * 8})))["ok"]
    gone = bridge.remove_addon("lr", "Aerith Garb Restauration", "files")
    assert gone["ok"], gone
    assert gone["message"].count("went back on over it") == 2
    assert bridge.remove_addon("lr", "FFXIII-LR - Outerworld Addons Remix", "files")["ok"]
    only = wpd.patch(vanilla, {("r_reward.wdb", "rw_102"): OUTER["rw_102"],
                               ("r_reward.wdb", "rw_117"): OUTER["rw_117"]})
    for letter in "av":
        assert live(game, letter) == only
    # and where each came from is kept for the next drop to match it by
    (record,) = gf.records(undo_store_for(game))
    assert record["source"] == str(tmp / "FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip")


def test_dropped_again_under_another_name_case_it_still_comes_out_from_under(lr):
    bridge, game, tmp = lr
    vanilla = live(game, "a")
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    assert bridge.install_mod("lr", str(outerworld(tmp)))["ok"]
    folder = tmp / "drops" / "aerith garb restauration"
    folder.mkdir(parents=True)
    data = {f"{letter}/{rel}": raw for letter, files in DLC_FILES.items()
            for rel, raw in files.items()}
    data["db/resident/_wdbpack.bin/r_dlc.wdb"] = AERITH_DLC
    (folder / "LRAerithGrabRestoration.exe").write_bytes(make_fake_pe(machine=0x14C))
    (folder / "PatchData.bin").write_bytes(zip_bytes(data))
    again = bridge.install_mod("lr", str(folder))
    assert again["ok"], again
    assert "went back on under it" in again["message"]
    names = [r["name"] for r in gf.records(undo_store_for(game))]
    assert names == ["FFXIII-LR - Outerworld Addons", "Aerith Garb Restauration"]
    assert bridge.remove_addon("lr", "Aerith Garb Restauration", "files")["ok"]
    only = wpd.patch(vanilla, {("r_reward.wdb", "rw_102"): OUTER["rw_102"],
                               ("r_reward.wdb", "rw_117"): OUTER["rw_117"]})
    for letter in "av":
        assert live(game, letter) == only


def test_a_game_with_two_archives_for_the_japanese_voices_is_left_alone(lr):
    bridge, game, tmp = lr
    sys_dir = game / "weiss_data" / "sys"
    write_pair(sys_dir / "white_img2.win32.bin", sys_dir / "filelist2.win32.bin",
               [(WDBPACK, wdbpack(dlc=blob("the japanese release's", 3001)))])
    before = sums(game)
    done = bridge.install_mod("lr", str(outerworld(tmp)))
    assert not done["ok"] and "can't be told apart" in done["error"]
    assert sums(game) == before


def test_a_build_a_crash_cut_short_is_cleared_by_the_next(lr):
    import os

    bridge, game, tmp = lr
    work = undo_store_for(game) / "work"
    stale, going = work / "patchdata-old", work / "patchdata-now"
    for folder in (stale, going):
        (folder / "a" / "db").mkdir(parents=True)
    os.utime(stale, (1, 1))
    assert bridge.install_mod("lr", str(outerworld(tmp)))["ok"]
    assert not stale.exists() and going.exists()


def test_open_page_with_one_of_its_two_files_in_waits_for_the_other_only(lr):
    """it waited for two, and took anything else of mod 23's for the second meanwhile"""
    bridge, game, tmp = lr
    limits = bridge.download_filter("lr", "dlcrestore")
    assert (limits["want"], limits["skip"]) == (2, [])
    assert bridge.install_mod("lr", str(aerith(tmp)))["ok"]
    assert bridge.list_addons("lr")["parts"]["dlcrestore"] == [1, 2]
    # and "download both" gets a fresh Aerith from someone with it in, which isn't the other
    limits = bridge.download_filter("lr", "dlcrestore")
    assert (limits["want"], limits["skip"]) == (1, ["Aerith_Garb_Restauration-23-1-1-1759690588.zip"])
    assert bridge.install_mod("lr", str(outerworld(tmp)))["ok"]
    assert bridge.list_addons("lr")["parts"]["dlcrestore"] == [2, 2]
