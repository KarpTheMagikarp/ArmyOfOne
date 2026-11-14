"""
FFXIIIwdbMod.exe's table edits done natively (wdbmod.py), and FFXIIIwdbLoad
taken over in the sandbox so a mod calling it installs whether or not it
ships the tool. The golden hashes are what the real exe wrote for these
exact inputs, run under Wine: the engine has to match it byte for byte
"""

import hashlib
import struct

import pytest

from patcher.mods import sandbox, wdbmod
from tests.helpers import pack_plain
from tests.test_sandbox import drawer, drop, laid, play, sums, xiii

WDB = "db/resident/bt_auto_ability.wdb"


def table(rows, cols, strings=b"\x00", pad=True, salt=0):
    """an XIII style .wdb: four of its own entries, then rows of words that are easy to spot"""
    names = [b"!!string", b"!!strtypelist", b"!!typelist", b"!!version"] + [r.encode() for r in rows]
    pool = strings + (b"\x00" * (-len(strings) % 4) if pad else b"")
    types = b"".join(struct.pack(">I", c % 4) for c in range(cols))
    rows_data = [b"".join(struct.pack(">I", (r * 2654435761 + c * 40503 + salt) & 0xFFFFFFFF)
                          for c in range(cols)) for r in range(len(rows))]
    blobs = [pool, types, b"\x00" * 4, b"\x00\x00\x00\x02"] + rows_data
    sizes = [len(strings), len(types), 4, 4] + [4 * cols] * len(rows)
    at, out = 16 + 32 * len(names), b""
    for name, blob, size in zip(names, blobs, sizes):
        out += name.ljust(16, b"\x00") + struct.pack(">II", at, size) + b"\x00" * 8
        at += len(blob)
    return b"WPD\x00" + struct.pack(">I", len(names)) + b"\x00" * 8 + out + b"".join(blobs)


def word(data: bytes, rows: int, row: int, col: int, cols: int) -> int:
    rows_at = struct.unpack_from(">I", data, 16 + 32 * 3 + 16)[0] + 4
    return struct.unpack_from(">I", data, rows_at + (row * cols + col) * 4)[0]


ROWS = ["drop_a", "drop_b", "drop_c", "spec_x", "spec_y"]
GOLDEN = {
    "fields": ({"db\\resident\\fields.wdb": table(ROWS, 8)}, """:\\every way a column's written
db\\resident\\fields.wdb
drop_a,0,w,10000
drop_a,1,w,-7
drop_a,2,w,0.5
drop_a,3,f,1.25
drop_a,4,f,-2.5e3
drop_b,0,bs,10000
drop_b,1,ib,3
drop_b,2,ii,258
drop_b,3,i,26
drop_b,4,bbbb,110
drop_b,5,b,255
drop_b,6,c,70000
drop_b,7,s,-1
drop_c,0,p347,125
drop_c,1,p477,55
drop_c,2,p47,75
drop_c,3,p47E7,100
drop_c,4,t0001,1
drop_c,5,t1,1
drop_c,6,p0,65535
drop_c,7,pF1,1
spec_x,0,bt01,1
spec_x,1,w,99999999999
spec_x,2,bs,
""", {"db\\resident\\fields.wdb":
      "3a4c6d80c9f28306db35b8de8d37bd10c4fc2dd11a746624492df1a4d8c41bb3"}),
    "strings": ({"db\\resident\\strings.wdb": table(
        ROWS, 4, strings=b"\x00material_o001\x00me020_00\x00ab\x00", pad=False)},
        """db\\resident\\strings.wdb
drop_a,0,l,me020_00
drop_a,1,l,material_o007
drop_b,1,l,material_o007
drop_c,2,l,0
drop_c,3,l,ab
spec_x,0,l,a longer string of its own
spec_y,1,l,b
""", {"db\\resident\\strings.wdb":
      "805bea521b3343091bfd4ea6fdae61c7a9e4f16def5f33f79e49d228c3c9b67a"}),
    "rows": ({"db\\resident\\rows.wdb": table(ROWS, 6)}, """:\\rows copied and renamed
db\\resident\\rows.wdb
:NOUNINSTALL
:COPY,drop_b,drop_new,:BEGIN
:COPY,spec_x,spec_new,spec_y
:RENAME,0,drop_c,drop_cc
:RENAME,1,nothere,nobody
drop_new,0,w,1
spec_new,5,ib,9
drop_cc,2,f,0.3
""", {"db\\resident\\rows.wdb":
      "7e90f6d664aa6938fde94f5c978ba44a142d7bbf430ba50612867c29bf32aa27"}),
    "two": ({"db\\crystal\\one.wdb": table(ROWS, 3),
             "db\\crystal\\two.wdb": table(ROWS[:2], 5, salt=7)},
            """:\\two tables, and back to the first
db\\crystal\\one.wdb
drop_a,0,w,36000
:NEWFILE
:\\second
db\\crystal\\two.wdb
drop_b,4,i,280
:NEWFILE
db\\crystal\\one.wdb
drop_a,1,ib,1
""", {"db\\crystal\\one.wdb": "51564bbde8fc0eab2b6e3649ff368eb36506191cbd12bac454eec74ef4788399",
      "db\\crystal\\two.wdb": "b47d0bfb33e9de6127716430cb9050ec903461ebf1fdd7ff7a18d7a8dfcff0ba"}),
}


@pytest.mark.parametrize("case", sorted(GOLDEN))
def test_every_table_comes_out_as_the_exe_wrote_it(case):
    tables, txt, want = GOLDEN[case]
    done = wdbmod.run(txt.replace("\n", "\r\n").encode(), lambda path: tables[path])
    assert {path: hashlib.sha256(data).hexdigest() for path, data in done} == want


@pytest.mark.parametrize("code, value, want", [
    ("bs", 10000, 10000 << 8), ("ib", 3, 3 << 8), ("ii", 258, 258), ("i", 26, 26 << 16),
    ("bbbb", 110, 110), ("b", 255, 255 << 24), ("p347", 125, 125 << 18), ("p47E7", 100, 100),
    ("t0001", 1, 1 << 28), ("p0", 65535, 65535 << 16), ("s", -1, 0xFFFF << 16),
])
def test_a_field_is_the_bits_its_code_walks_to(code, value, want):
    assert wdbmod.bits(0, code, value) == want
    # and nothing else in the word moves
    assert wdbmod.bits(0xFFFFFFFF, code, 0) == 0xFFFFFFFF & ~(
        wdbmod.bits(0, code, -1))


@pytest.mark.parametrize("code", ["", "w", "x", "bbbbb", "t2", "pz", "q8"])
def test_a_code_it_cant_walk_is_said_not_guessed(code):
    with pytest.raises(wdbmod.WdbModError):
        wdbmod.bits(0, code, 1)


@pytest.mark.parametrize("text, want", [
    ("99999999999", 1215752191), ("-99999999999", -1215752191), ("4294967296", 0),
    ("  17", 17), ("+42", 42), ("0x10", 0), ("1e3", 1), ("", 0)])
def test_numbers_read_the_way_the_exe_reads_them(text, want):
    assert wdbmod.atoi(text) == want


def test_a_string_already_there_is_pointed_at_and_a_new_one_added_once():
    data = table(["a", "b"], 2, strings=b"\x00one\x00two\x00", pad=False)
    txt = b"db\\t.wdb\na,0,l,two\na,1,l,three\nb,0,l,three\nb,1,l,0\n"
    (_, out), = wdbmod.run(txt, lambda _: data)
    assert word(out, 2, 0, 0, 2) == 5
    assert word(out, 2, 0, 1, 2) == word(out, 2, 1, 0, 2) == 9
    assert word(out, 2, 1, 1, 2) == 0
    pool_size = struct.unpack_from(">I", out, 16 + 20)[0]
    assert pool_size % 4 == 0 and out.count(b"three\x00") == 1


def test_a_copy_at_the_end_lands_where_the_exe_meant_it_to():
    # the exe reads this one's offset from past its table and writes junk
    data = table(["a", "b"], 3)
    (_, out), = wdbmod.run(b"db\\t.wdb\n:COPY,a,a2,:END\na2,2,w,7\n", lambda _: data)
    again = wdbmod.Table(out)
    assert again.count == 7 and again.row_of("a2") == 6
    assert word(out, 3, 2, 0, 3) == word(out, 3, 0, 0, 3) and word(out, 3, 2, 2, 3) == 7


@pytest.mark.parametrize("txt, said", [
    (b"db\\t.wdb\nnope,0,w,1\n", "has no nope in it"),
    (b"db\\t.wdb\na,9,w,1\n", "has 2 columns, not a column 9"),
    (b"db\\t.wdb\na,0,w\n", "isn't record,column,type,value"),
    (b"db\\t.wdb\na,0,zz,1\n", "isn't one FFXIIIwdbMod reads"),
    (b"db\\t.wdb\n:RENAME,0,nope,x\n", "can't find nope"),
    (b"db\\t.wdb\n:COPY,nope,x,:END\n", "can't find nope"),
    (b"db\\t.wdb\n:COPY,a,much_too_long_a_name,:END\n", "16 characters"),
])
def test_a_line_it_cant_do_stops_it_whole(txt, said):
    with pytest.raises(wdbmod.WdbModError, match=said):
        wdbmod.run(txt, lambda _: table(["a", "b"], 2))


@pytest.mark.parametrize("damage", ["magic", "short", "offset"])
def test_a_table_not_laid_out_as_the_exe_reads_one_is_turned_away(damage):
    data = bytearray(table(["a", "b"], 2))
    if damage == "magic":
        data[:4] = b"RIFF"
    elif damage == "short":
        data = data[:-4]
    else:
        struct.pack_into(">I", data, 16 + 32 * 5 + 16, 9999)
    with pytest.raises(wdbmod.WdbModError):
        wdbmod.Table(bytes(data))


def test_descriptions_blank_lines_and_crlf_are_read_past():
    data = table(["a"], 1)
    txt = b":\\what it does\r\n:\\\r\n\r\ndb\\t.wdb\r\n:\\more\r\n\r\na,0,w,5\r\n\x1aa,0,w,6\r\n"
    (_, out), = wdbmod.run(txt, lambda _: data)
    assert word(out, 1, 0, 0, 1) == 5


def game_with(tmp_path, data=None):
    """XIII with a .wdb in both voice sets' white_img"""
    game = xiii(tmp_path / "steam")
    sys_dir = game / "white_data" / "sys"
    for v in "uc":
        pack_plain(sys_dir / f"white_img{v}.win32.bin", sys_dir / f"filelist{v}.win32.bin",
                   [("gui/resident/menu.win32.imgb", b"menu" * 300),
                    (WDB, data or table(["auto_drop_0", "auto_drop_1"], 8))])
    return game


CATALOG = ":\\Collector's Catalog to +100%\r\n" \
          "db\\resident\\bt_auto_ability.wdb\r\nauto_drop_0,7,bs,10000\r\nauto_drop_1,7,bs,5000\r\n"
CALLS = '@echo off\ncd /d "%~dp0"\ncall FFXIIIwdbLoad {}\n'


def want(txt: str, data=None) -> bytes:
    return wdbmod.run(txt.encode(), lambda _: data or table(["auto_drop_0", "auto_drop_1"], 8))[0][1]


def test_a_mod_that_leaves_ffxiiiwdbload_out_still_goes_in_both_voice_sets(tmp_path):
    game = game_with(tmp_path)
    before = sums(game)
    folder = drop(tmp_path, "Less Grinding", {
        "- LG Catalog Drop Rates.bat": CALLS.format("Catalog_Rates.txt"),
        "Catalog_Rates.txt": CATALOG})
    p = play(game, folder)
    assert laid(p) == {f"white_imgu/{WDB}": want(CATALOG), f"white_imgc/{WDB}": want(CATALOG)}, \
        (p.problem, p.detail)
    assert sums(game) == before


def test_its_own_ffxiiiwdbload_and_tools_are_never_run(tmp_path):
    # theirs would search the drives, unpack white_imgu whole and run an exe
    game = game_with(tmp_path)
    folder = drop(tmp_path, "Gameplay Tweaks", {
        "Mod_CatalogDropRate.bat": "@echo OFF\ncall FFXIIIwdbLoad Catalog.txt\n",
        "FFXIIIwdbLoad.bat": "@echo off\necho theirs ran> theirs.txt\nexit /b 1\n",
        "FFXIIIwdbSetup.bat": "@echo off\nexit /b 1\n",
        "FFXIIIwdbMod.exe": b"MZ", "ff13tool.exe": b"MZ", "Catalog.txt": CATALOG})
    p = play(game, folder)
    assert laid(p) == {f"white_imgu/{WDB}": want(CATALOG), f"white_imgc/{WDB}": want(CATALOG)}, \
        (p.problem, p.detail)
    assert p.ran == []


def test_its_pick_between_txts_is_offered_as_versions(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    sys_dir = game / "white_data" / "sys"
    pack_plain(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
               [(WDB, table(["auto_drop_0", "auto_drop_1"], 8))])
    double = CATALOG.replace("10000", "20000")
    folder = drop(tmp_path, "Strike Chain", {
        "Mod_Catalog.bat": "@echo OFF\nset filename1=Plus100.txt\nset filename2=Plus200.txt\n"
                           "set select=2\nset selecttext=\"Select (A) +100%% or (B) +200%%: \"\n"
                           "set selectchoice=\"AB\"\ncall FFXIIIwdbLoad Plus100.txt\n",
        "Plus100.txt": CATALOG, "Plus200.txt": double})
    asked = bridge.install_mod("ff13", str(folder))
    assert asked.get("versions") == ["+100%", "+200%"], asked
    done = bridge.install_mod("ff13", str(folder), version="+200%")
    assert done["ok"], done
    from patcher.mods import game_files
    from patcher.mods.hd_zones import read_record
    entry = game_files.entries(sys_dir / "filelistu.win32.bin")[WDB]
    assert read_record(sys_dir / "white_imgu.win32.bin", entry) == want(double)


@pytest.mark.parametrize("keys, text, labels", [
    ("ABO", "Select (A) 98% or (B) 88% Chain increment, or (O)riginal rate: ",
     {"A": "98%", "B": "88% Chain increment", "O": "Original rate"}),
    ("35O", "Select (3)0% or (5)0% Increase in ATB rate, or (O)riginal rate: ",
     {"3": "30%", "5": "50% Increase in ATB rate", "O": "Original rate"}),
])
def test_its_select_text_reads_as_the_versions_it_offers(keys, text, labels):
    assert sandbox.select_labels(keys, text) == labels


def test_the_drawer_installs_one_of_its_versions(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    sys_dir = game / "white_data" / "sys"
    pack_plain(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
               [(WDB, table(["auto_drop_0", "auto_drop_1"], 8))])
    folder = drop(tmp_path, "Less Grinding 73 1.3", {
        "- LG Catalog Drop Rates.bat": CALLS.format("Catalog_Rates.txt"),
        "- LG Half CP Cost.bat": CALLS.format("Half_CP_Cost.txt"),
        "Catalog_Rates.txt": CATALOG,
        "Half_CP_Cost.txt": CATALOG.replace("5000", "2500")})
    asked = bridge.install_mod("ff13", str(folder))
    assert asked["versions"] == ["- LG Catalog Drop Rates", "- LG Half CP Cost"]
    done = bridge.install_mod("ff13", str(folder), version="- LG Catalog Drop Rates")
    assert done["ok"], done
    from patcher.mods import game_files
    index = sys_dir / "filelistu.win32.bin"
    entry = game_files.entries(index)[WDB]
    from patcher.mods.hd_zones import read_record
    assert read_record(sys_dir / "white_imgu.win32.bin", entry) == want(CATALOG)


def test_a_record_its_table_hasnt_got_is_said_plainly(tmp_path):
    game = game_with(tmp_path)
    folder = drop(tmp_path, "Less Grinding", {
        "- LG Catalog Drop Rates.bat": CALLS.format("Catalog_Rates.txt"),
        "Catalog_Rates.txt": CATALOG.replace("auto_drop_1", "auto_drop_9")})
    p = play(game, folder)
    assert laid(p) == {}
    assert p.problem == ("loads Catalog_Rates.txt into the game's tables, and "
                         "db\\resident\\bt_auto_ability.wdb has no auto_drop_9 in it")
    assert p.detail.startswith("The .txt was made against a different version")


def test_a_txt_its_download_hasnt_got_is_said_plainly(tmp_path):
    game = game_with(tmp_path)
    folder = drop(tmp_path, "Less Grinding", {
        "- LG Catalog Drop Rates.bat": CALLS.format("Catalog_Rates.txt")})
    p = play(game, folder)
    assert p.problem == ("loads Catalog_Rates.txt into the game's tables, and that isn't in "
                         "its download")


def test_another_games_drop_is_told_which_game_its_for(tmp_path):
    game = tmp_path / "steam" / "steamapps" / "common" / "FINAL FANTASY XIII-2"
    sys_dir = game / "alba_data" / "sys"
    sys_dir.mkdir(parents=True)
    (game / "alba_data" / "prog" / "win" / "bin").mkdir(parents=True)
    from tests.helpers import make_fake_pe
    (game / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe").write_bytes(make_fake_pe())
    pack_plain(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
               [("db/x.wdb", table(["a"], 1))])
    folder = drop(tmp_path, "Less Grinding", {
        "- LG Catalog Drop Rates.bat": CALLS.format("Catalog_Rates.txt"),
        "Catalog_Rates.txt": CATALOG})
    p = play(game, folder)
    assert p.problem == "is a FINAL FANTASY XIII mod, and edits tables only XIII has"


def test_its_exe_called_by_a_bat_of_its_own_edits_wdb_files_the_way_the_exe_does(tmp_path):
    from tests.test_sandbox import GAME
    original = table(["auto_drop_0", "auto_drop_1"], 8)
    game = game_with(tmp_path, original)
    folder = drop(tmp_path, "Own Loader", {
        "install.bat": rf"""@echo off
cd /d "%~dp0"
FFXIIIwdbMod.exe Catalog.txt
if errorlevel 1 exit /b 1
cd wdb_files\mod
..\..\ff13tool -i "{GAME}\white_data\sys\filelistu.win32.bin" "{GAME}\white_data\sys\white_imgu.win32.bin" db\resident\bt_auto_ability.wdb
""",
        "FFXIIIwdbMod.exe": b"MZ", "ff13tool.exe": b"MZ", "Catalog.txt": CATALOG,
        "wdb_files/orig/db/resident/bt_auto_ability.wdb": original,
        "wdb_files/mod/db/resident/.keep": b""})
    p = play(game, folder)
    assert laid(p) == {f"white_imgu/{WDB}": want(CATALOG, original)}, (p.problem, p.detail)
    assert "FFXIIIwdbMod.exe" in p.ran


def test_the_stand_in_is_found_by_any_name_cmd_would_find_it_by():
    world = sandbox.World.__new__(sandbox.World)
    for name in ("FFXIIIwdbLoad", "ffxiiiwdbload.bat", "C:\\Mods\\FFXIIIwdbLoad.cmd"):
        assert world.builtin(name) == sandbox.WDBLOAD
    assert world.builtin("FFXIIIwdbSetup") is None


SPEC = "db/resident/bt_chara_spec.wdb"
CHAIN = "db/resident/damagesrc.wdb"
# Gameplay Tweaks' shape: a Mod_ .bat per tweak, a few a pick between .txts,
# one of them the game's own values back
OWN = "db\\resident\\damagesrc.wdb\r\nat010,5,w,1000\r\nat020,5,w,1000\r\n"
TWEAKS = {
    "Mod_StrikeChain.bat": "@echo OFF\nset filename1=Strike98.txt\nset filename2=Strike88.txt\n"
                           "set filename3=Strike100.txt\nset select=3\n"
                           "set selecttext=\"Select (A) 98%% or (B) 88%% Chain increment, or "
                           "(O)riginal rate: \"\nset selectchoice=\"ABO\"\n"
                           "call FFXIIIwdbLoad Strike100.txt\n",
    "Mod_RunSpeed.bat": "@echo OFF\nset filename1=Run_0.txt\nset filename2=Run_15.txt\n"
                        "set select=2\nset selecttext=\"Select (A) +15%% Run Speed or (O)riginal: \"\n"
                        "set selectchoice=\"OA\"\ncall FFXIIIwdbLoad Run_0.txt\n",
    "Mod_Lightning.bat": "@echo OFF\ncall FFXIIIwdbLoad Lightning.txt\n",
    "Mod_AllCrystals.bat": "@echo OFF\ncall FFXIIIwdbLoad Crystals.txt\n",
    "Strike98.txt": OWN.replace("1000", "980"), "Strike88.txt": OWN.replace("1000", "880"),
    "Strike100.txt": OWN,
    "Run_0.txt": "db\\resident\\bt_chara_spec.wdb\r\nfam_light,5,bbbb,96\r\n",
    "Run_15.txt": "db\\resident\\bt_chara_spec.wdb\r\nfam_light,5,bbbb,110\r\n",
    # two that share Lightning's row of one table, the second put in over the first
    "Crystals.txt": "db\\resident\\bt_auto_ability.wdb\r\nauto_drop_0,7,bs,10000\r\n"
                    "auto_drop_1,7,bs,5000\r\n",
    "Lightning.txt": "db\\resident\\bt_auto_ability.wdb\r\nauto_drop_0,3,w,77\r\n",
}


def tweaked(tmp_path, monkeypatch):
    """a game whose tables have the values the packs' "(O)riginal" .txts write"""
    game, bridge = drawer(tmp_path, monkeypatch)
    sys_dir = game / "white_data" / "sys"
    tables = {WDB: table(["auto_drop_0", "auto_drop_1"], 8),
              CHAIN: want(OWN, table(["at010", "at020"], 8)),
              SPEC: want(TWEAKS["Run_0.txt"], table(["fam_light"], 8))}
    pack_plain(sys_dir / "white_imgu.win32.bin", sys_dir / "filelistu.win32.bin",
               list(tables.items()))
    return bridge, drop(tmp_path, "Gameplay_Tweaks-5-1-1-16-1742857788", TWEAKS), sys_dir, tables


def now(sys_dir, path):
    from patcher.mods import game_files
    from patcher.mods.hd_zones import read_record
    return read_record(sys_dir / "white_imgu.win32.bin",
                       game_files.entries(sys_dir / "filelistu.win32.bin")[path])


def test_a_tweaks_original_is_never_offered_as_a_version_of_it(tmp_path, monkeypatch):
    bridge, folder, sys_dir, tables = tweaked(tmp_path, monkeypatch)
    asked = bridge.install_mod("ff13", str(folder), version="Mod_StrikeChain")
    assert asked["versions"] == ["Mod_StrikeChain / 98%",
                                 "Mod_StrikeChain / 88% Chain increment"], asked
    # one real choice left isn't a question
    done = bridge.install_mod("ff13", str(folder), version="Mod_RunSpeed")
    assert done["ok"] and done["summary"] == "Installed Gameplay Tweaks (RunSpeed).", done
    assert now(sys_dir, SPEC) == want(TWEAKS["Run_15.txt"], tables[SPEC])


def test_its_original_picked_anyway_says_what_it_is(tmp_path, monkeypatch):
    bridge, folder, sys_dir, tables = tweaked(tmp_path, monkeypatch)
    said = bridge.install_mod("ff13", str(folder), version="Mod_StrikeChain / Original rate")
    assert said["summary"] == "Original rate is how the game already is, so nothing changed.", said
    assert bridge.install_mod("ff13", str(folder), version="Mod_StrikeChain / 98%")["ok"]
    # in, the version it is is marked, and its original is Remove's job
    again = bridge.install_mod("ff13", str(folder), version="Mod_StrikeChain")
    assert again["done"] == ["Mod_StrikeChain / 98%"], again
    said = bridge.install_mod("ff13", str(folder), version="Mod_StrikeChain / Original rate")
    assert said["summary"] == ("Original rate puts the game's own back. That's Remove on "
                               "Gameplay Tweaks (StrikeChain) under Installed."), said
    assert now(sys_dir, CHAIN) == want(TWEAKS["Strike98.txt"], tables[CHAIN])


def test_a_tweak_removed_from_under_another_takes_only_its_own_edits(tmp_path, monkeypatch):
    bridge, folder, sys_dir, tables = tweaked(tmp_path, monkeypatch)
    for pick in ("Mod_AllCrystals", "Mod_Lightning"):
        assert bridge.install_mod("ff13", str(folder), version=pick)["ok"]
    both = want(TWEAKS["Lightning.txt"], want(TWEAKS["Crystals.txt"], tables[WDB]))
    assert now(sys_dir, WDB) == both
    done = bridge.remove_addon("ff13", "Gameplay Tweaks (AllCrystals)", "files")
    assert done["ok"], done
    assert now(sys_dir, WDB) == want(TWEAKS["Lightning.txt"], tables[WDB])
    assert bridge.remove_addon("ff13", "Gameplay Tweaks (Lightning)", "files")["ok"]
    assert now(sys_dir, WDB) == tables[WDB]
