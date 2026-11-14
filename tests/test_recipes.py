"""
A .bat mod read instead of run (patcher/mods/recipes.py), against a XIII-2
built the way Steam's is where it matters: six costume codes pointing at
records named after c201 and c204, which the Console Content Patch's xdelta
turns back into the 1.0 release's c160 and c163, one of them a placeholder.
"""

import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from patcher.archive import crypt
from patcher.archive.entry import ChunkEntry, format_entry
from patcher.archive.filelist import Filelist, by_code, load, rebuild
from patcher.mods import game_files as gf
from patcher.mods import recipes
from patcher.mods.hd_zones import read_record
from patcher.paths import undo_store_for
from tests.hd2_world import header
from tests.helpers import vcdiff_patch
from tests.test_mod_drops import world  # noqa: F401  (the fixture)

SECTOR = 2048
C201, C204 = "chr/pc/c201/bin/c201.win32", "chr/pc/c204/bin/c204.win32"


def blob(seed: str, size: int) -> bytes:
    """compressible, and different for every seed"""
    return (hashlib.sha256(seed.encode()).hexdigest().encode() * (size // 64 + 1))[:size]


# what both releases hold under the real c201 and c204, which Steam's update
# also copied under the costumes' codes
REAL = {
    0x1180000: (f"{C201}.imgb", blob("c201i", 9000)),
    0x1180100: (f"{C201}.trb", blob("c201t", 4000)),
    0x1188000: (f"{C204}.imgb", blob("c204i", 7000)),
    0x1188100: (f"{C204}.trb", blob("c204t", 3000)),
    0x10F0000: ("chr/pc/c107/bin/c107.win32.imgb", blob("c107i", 6000)),
    0x10F0100: ("chr/pc/c107/bin/c107.win32.trb", blob("c107t", 5000)),
    0x2000001: ("btscene/pack/wdb/x000.bin", blob("x000", 2900)),
    0x3000001: ("vfx/field/rain.win32.imgb", blob("rain", 4000)),
}
# code -> (Steam's record, 1.0's record and what it holds, b"" a placeholder)
CUT = {
    0x1140000: (REAL[0x1180000][0], "chr/pc/c160/bin/c160.win32.imgb", blob("c160i", 8000)),
    0x1140100: (REAL[0x1180100][0], "chr/pc/c160/bin/c160.win32.trb", blob("c160t", 3500)),
    0x1146000: (REAL[0x1188000][0], "chr/pc/c163/bin/c163.win32.imgb", b""),
    # 1.0's is a copy of another file Steam has, which the patch copies rather
    # than adds, and which is still a change
    0x1146100: (REAL[0x1188100][0], "chr/pc/c163/bin/c163.win32.trb", REAL[0x10F0100][1]),
}
VERSION = (0x1001, "sys/debug/version.txt")
X000_NEW = blob("x000-unlocked", 2900)
C107_NEW = blob("c107-classic", 5100)


def pack(rows):
    """(archive bytes, [(code, entry)]) with every payload at its sector"""
    body, out = bytearray(), []
    for code, path, payload in rows:
        if payload:
            stored = zlib.compress(payload, 6)
            stored = payload if len(stored) >= len(payload) else stored
        else:
            stored = zlib.compress(b"")
        out.append((code, ChunkEntry(path=path, sector_offset=len(body) // SECTOR,
                                     uncompressed=len(payload), stored=len(stored))))
        body += stored
        body += b"\x00" * (-len(body) % SECTOR)
    return bytes(body), out


def index_of(rows) -> bytes:
    """a sealed filelist with these codes, records in chunk order, lookups by code"""
    chunk, lookups = bytearray(), []
    for code, entry in rows:
        lookups.append((code, len(chunk)))
        chunk += format_entry(entry).encode() + b"\x00"
    chunk += b"end\x00"
    records = b"".join(struct.pack("<I", code) + at.to_bytes(3, "little") + bytes([16])
                       for code, at in sorted(lookups))
    book = Filelist(header=header(), entry_records=records, chunks=[bytes(chunk)],
                    original_blobs=[None], edited={0}, entry_count=len(rows),
                    was_encrypted=False, remainder=b"\x00" * 8)
    return crypt.encrypt(rebuild(book, encrypt=False))


def steam_rows():
    rows = [(VERSION[0], VERSION[1], b"1.0.1.0 steam")]
    rows += [(code, steam, REAL[next(c for c, (p, _) in REAL.items() if p == steam)][1])
             for code, (steam, _, _) in CUT.items()]
    rows += [(code, path, payload) for code, (path, payload) in REAL.items()]
    return rows


def release_rows():
    rows = [(VERSION[0], VERSION[1], b"1.0.0.0 first")]
    rows += [(code, path, payload) for code, (_, path, payload) in CUT.items()]
    rows += [(code, path, payload) for code, (path, payload) in REAL.items()]
    return rows


def patches(source_index: bytes, source_archive: bytes, split: bool = False) -> tuple:
    """(index patch, archive patch) from Steam's pair to 1.0's, the way xdelta3 lays them out"""
    steam_body, steam = pack(steam_rows())
    assert steam_body == source_archive
    body, release = pack(release_rows())
    where = {code: e for code, e in steam}
    # what Steam has anywhere, so 1.0's c163.trb copies from c107's
    anywhere = {}
    for code, e in steam:
        anywhere.setdefault(source_archive[e.byte_offset:e.byte_offset + e.stored], e)
    pieces = []
    for code, e in release:
        stored = body[e.byte_offset:e.byte_offset + e.stored]
        same = anywhere.get(stored)
        if same is not None and not e.is_stub:
            pieces.append(("copy", same.byte_offset, e.stored))
        else:
            pieces.append(("add", stored))
        pad = -e.stored % SECTOR
        if pad:
            pieces.append(("run", 0, pad))
    assert where
    if split:
        # a window boundary through the middle of the first costume
        first = next(e for code, e in release if code == 0x1140000)
        cut = first.byte_offset + 100
        windows, built = [[]], 0
        for piece in pieces:
            size = len(piece[1]) if piece[0] == "add" else piece[2]
            if built < cut < built + size and piece[0] == "add":
                windows[-1].append(("add", piece[1][:cut - built]))
                windows.append([("add", piece[1][cut - built:])])
            else:
                windows[-1].append(piece)
            built += size
        archive_patch = vcdiff_patch(source_archive, windows)
    else:
        archive_patch = vcdiff_patch(source_archive, [pieces])
    target = index_of(release)
    assert target[20:32] == source_index[20:32]
    index_patch = vcdiff_patch(source_index, [[("add", target[:20]), ("copy", 20, 12),
                                               ("add", target[32:])]])
    return index_patch, archive_patch, body, release


BAT = r"""@echo off
cls
:start
echo Console Content Patcher
echo 1. Revert Game Version to 1.0
echo 2. Unlock Console-Exclusive DLC
echo Type "Update" to download the latest build.
set /p choice=Input Selection:
if '%choice%'=='1' goto :choice1
if '%choice%'=='2' goto :choice2
if '%choice%'=='Update' goto :update
goto start

:choice1
{revert}
echo ...Finished!
pause
goto start

:choice2
copy .\mod\Misc\x000.ccp .\mod\Data\btscene\pack\wdb\x000.bin
{unlock}
del .\mod\Data\btscene\pack\wdb\x000.bin
goto end

:update
powershell -Command "(New-Object Net.WebClient).DownloadFile('https://example.com/x', 'mod.rar')"
"mod/7z.exe" x -aoa mod.rar
goto start

:end
pause
exit
"""


def revert_lines(voice: str) -> str:
    return "\n".join([
        rf"copy .\mod\Misc\filelist{voice}.win32.xdelta .\mod\filelist{voice}.win32.xdelta",
        rf"copy .\mod\Misc\white_img{voice}.win32.xdelta .\mod\white_img{voice}.win32.xdelta",
        rf"move .\sys\filelist{voice}.win32.bin .\mod\filelist{voice}.win32.org",
        rf"move .\sys\white_img{voice}.win32.bin .\mod\white_img{voice}.win32.org",
        r"cd .\mod",
        rf"xdelta -dfs filelist{voice}.win32.org filelist{voice}.win32.xdelta filelist{voice}.win32.bin",
        rf"xdelta -dfs white_img{voice}.win32.org white_img{voice}.win32.xdelta white_img{voice}.win32.bin",
        r"cd ..\ ",
        rf"move .\mod\filelist{voice}.win32.bin .\sys\filelist{voice}.win32.bin",
        rf"move .\mod\white_img{voice}.win32.bin .\sys\white_img{voice}.win32.bin",
        rf"del .\mod\filelist{voice}.win32.xdelta",
        rf"del .\mod\white_img{voice}.win32.org",
        rf"del .\mod\filelist{voice}.win32.org",
    ])


def unlock_lines(voice: str) -> str:
    return "\n".join([
        rf"move .\sys\filelist{voice}.win32.bin .\mod\filelist{voice}.win32.bin",
        rf"move .\sys\white_img{voice}.win32.bin .\mod\white_img{voice}.win32.bin",
        r"cd .\mod",
        rf'Cocoon.exe inject2 filelist{voice}.win32.bin white_img{voice}.win32.bin ".\Data" '
        r"btscene\pack\wdb\x000.bin",
        rf'Cocoon.exe inject2 filelist{voice}.win32.bin white_img{voice}.win32.bin ".\Data" '
        r"chr\pc\c107\bin\c107.win32.trb",
        r"cd ..\ ",
        rf"move .\mod\filelist{voice}.win32.bin .\sys\filelist{voice}.win32.bin",
        rf"move .\mod\white_img{voice}.win32.bin .\sys\white_img{voice}.win32.bin",
        rf"rd .\mod\filelist{voice}.win32_chunks /q /s",
    ])


def steamify(game: Path) -> dict:
    """the game's archives swapped for Steam-shaped ones, both voice sets"""
    sys_dir = game / "alba_data" / "sys"
    body, steam = pack(steam_rows())
    index = index_of(steam)
    for voice in "uc":
        (sys_dir / f"white_img{voice}.win32.bin").write_bytes(body)
        (sys_dir / f"filelist{voice}.win32.bin").write_bytes(index)
    return {"index": index, "archive": body}


def drop(tmp_path: Path, game_files: dict, split: bool = False, voices="uc") -> Path:
    root = tmp_path / "FFXIII-2 - Console Content Patch v2.3"
    misc = root / "mod" / "Misc"
    misc.mkdir(parents=True)
    index_patch, archive_patch, _, _ = patches(game_files["index"], game_files["archive"], split)
    for voice in voices:
        (misc / f"filelist{voice}.win32.xdelta").write_bytes(index_patch)
        (misc / f"white_img{voice}.win32.xdelta").write_bytes(archive_patch)
    (misc / "x000.ccp").write_bytes(X000_NEW)
    for member, data in {"mod/Data/btscene/pack/wdb/x000.bin": X000_NEW,
                         "mod/Data/chr/pc/c107/bin/c107.win32.trb": C107_NEW,
                         "mod/Cocoon.exe": b"MZ not run", "mod/xdelta.exe": b"MZ not run",
                         "mod/EntityFramework.dll": b"MZ not loaded",
                         "Readme.txt": b"Reverting to 1.0 is required the first time."}.items():
        (root / member).parent.mkdir(parents=True, exist_ok=True)
        (root / member).write_bytes(data)
    bat = BAT.replace("{revert}", "\n".join(revert_lines(v) for v in voices)).replace(
        "{unlock}", "\n".join(unlock_lines(v) for v in voices))
    (root / "Install Console Content Patch.bat").write_bytes(bat.replace("\n", "\r\n").encode())
    return root


def held(game: Path, voice: str) -> dict:
    """code -> (path, what it holds plainly, or "stub")"""
    sys_dir = game / "alba_data" / "sys"
    archive = sys_dir / f"white_img{voice}.win32.bin"
    out = {}
    for code, records in by_code(load(sys_dir / f"filelist{voice}.win32.bin")).items():
        (e,) = records
        out[code] = (e.path, "stub" if e.is_stub else read_record(archive, e))
    return out


def sys_bytes(game: Path) -> dict:
    sys_dir = game / "alba_data" / "sys"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sys_dir.iterdir())
            if p.name.startswith(("filelist", "white_img"))}


def test_the_bat_reads_as_two_steps_and_its_update_option_is_left_out():
    has = {"mod/misc/x000.ccp": "mod/Misc/x000.ccp",
           "mod/data/chr/pc/c107/bin/c107.win32.trb": "mod/Data/chr/pc/c107/bin/c107.win32.trb"}
    for voice in "uc":
        has[f"mod/misc/filelist{voice}.win32.xdelta"] = f"mod/Misc/filelist{voice}.win32.xdelta"
        has[f"mod/misc/white_img{voice}.win32.xdelta"] = f"mod/Misc/white_img{voice}.win32.xdelta"
    text = BAT.replace("{revert}", "\n".join(revert_lines(v) for v in "uc")).replace(
        "{unlock}", "\n".join(unlock_lines(v) for v in "uc"))
    recipe = recipes.read(text, "Install Console Content Patch.bat", has.get, {"mod"})
    revert, unlock = recipe.options
    assert revert.label == "Revert Game Version to 1.0" and not revert.problem
    assert unlock.label == "Unlock Console-Exclusive DLC" and not unlock.problem
    plan = revert.pairs["sys/filelistu.win32.bin"]
    assert (plan.index_patch, plan.archive_patch) == ("mod/Misc/filelistu.win32.xdelta",
                                                     "mod/Misc/white_imgu.win32.xdelta")
    # what x000.bin is when it's injected is the .ccp copied over it first
    assert unlock.pairs["sys/filelistc.win32.bin"].injects == [
        ("btscene/pack/wdb/x000.bin", "mod/Misc/x000.ccp"),
        ("chr/pc/c107/bin/c107.win32.trb", "mod/Data/chr/pc/c107/bin/c107.win32.trb")]
    chosen, versions = recipes.steps(recipe)
    assert [o.label for o in chosen] == [revert.label, unlock.label] and versions == []


@pytest.mark.parametrize("line, why", [
    ("del .\\sys\\filelistu.win32.bin", "deletes the game's"),
    ("copy .\\mod\\x.bin .\\sys\\white_imgu.win32.bin", "something else in place"),
    ("ff13tool.exe -i -ff132 filelistu.win32.bin white_imgu.win32.bin x", "ff13tool"),
    ("powershell -Command Invoke-WebRequest x", "powershell"),
    ("copy .\\mod\\*.bin .\\sys\\", "can't follow"),
    ("if exist .\\sys\\x goto :a", "decides something"),
])
def test_anything_else_a_bat_does_to_the_game_leaves_that_option_out(line, why):
    text = f"@echo off\n{line}\n"
    recipe = recipes.read(text, "go.bat", {"mod/x.bin": "mod/x.bin"}.get, {"mod"})
    (option,) = recipe.options
    assert why in option.problem and not recipe.usable()


def test_options_that_put_different_files_in_one_place_are_versions():
    text = "\n".join([
        "@echo off", ":start", "echo 1. Short hair", "echo 2. Long hair", "set /p c=?",
        "if '%c%'=='1' goto :a", "if '%c%'=='2' goto :b", "goto start", ":a",
        'Cocoon.exe inject2 sys\\filelistu.win32.bin sys\\white_imgu.win32.bin ".\\Short" '
        "chr\\pc\\c108\\bin\\c108.win32.trb", "goto start", ":b",
        'Cocoon.exe inject2 sys\\filelistu.win32.bin sys\\white_imgu.win32.bin ".\\Long" '
        "chr\\pc\\c108\\bin\\c108.win32.trb", "goto start"])
    has = {"short/chr/pc/c108/bin/c108.win32.trb": "Short/chr/pc/c108/bin/c108.win32.trb",
           "long/chr/pc/c108/bin/c108.win32.trb": "Long/chr/pc/c108/bin/c108.win32.trb"}
    chosen, versions = recipes.steps(recipes.read(text, "go.bat", has.get))
    assert chosen == [] and versions == ["Short hair", "Long hair"]


def test_the_console_content_patch_goes_in_by_its_bat_and_comes_off_whole(world, tmp_path):
    bridge, game, _ = world
    files = steamify(game)
    before = sys_bytes(game)
    done = bridge.install_mod("ff13-2", str(drop(tmp_path, files)))
    assert done["ok"], done
    assert done["title"] == "Console Content Patch"
    assert "Revert Game Version to 1.0, then Unlock Console-Exclusive DLC" in done["message"]
    assert "update" not in done["message"].lower()
    for voice in "uc":
        now = held(game, voice)
        for code, (_, path, payload) in CUT.items():
            assert now[code] == (path, payload or "stub")
        assert now[VERSION[0]] == (VERSION[1], b"1.0.0.0 first")
        assert now[0x2000001][1] == X000_NEW and now[0x10F0100][1] == C107_NEW
        # the real c201 and c204, and everything the patch left alone, as they were
        for code in (0x1180000, 0x1180100, 0x1188000, 0x1188100, 0x10F0000, 0x3000001):
            assert now[code] == REAL[code]
    (record,) = gf.records(undo_store_for(game))
    assert record["recipe"] == "Install Console Content Patch.bat"
    assert record["steps"] == ["Revert Game Version to 1.0", "Unlock Console-Exclusive DLC"]
    renamed = [i for spec in record["pairs"] for i in spec["entries"] if i.get("renamed")]
    assert len(renamed) == 2 * len(CUT)
    # nothing of the mod's own tools went anywhere
    assert not (game / "alba_data" / "prog" / "win" / "bin" / "addons").exists()
    # and Worth getting's DLC Restoration row, whose page it's from, knows it's in
    rows = {m["name"]: m for m in bridge.list_addons("ff13-2")["mods"]}
    assert rows[record["name"]].get("jobId") == "dlcrestore"

    gone = bridge.remove_addon("ff13-2", record["name"])
    assert gone["ok"], gone
    assert sys_bytes(game) == before
    assert not list((undo_store_for(game) / "work").glob("recipe-*"))


def test_coming_off_under_another_mod_names_the_records_back(world, tmp_path):
    """the index has moved on since, so it goes record by record, names and all"""
    bridge, game, _ = world
    files = steamify(game)
    before = {voice: held(game, voice) for voice in "uc"}
    assert bridge.install_mod("ff13-2", str(drop(tmp_path, files)))["ok"]
    rain = tmp_path / "Rain" / "vfx" / "field" / "rain.win32.imgb"
    rain.parent.mkdir(parents=True)
    rain.write_bytes(blob("hd rain", 4100))
    assert bridge.install_mod("ff13-2", str(tmp_path / "Rain"))["ok"]

    assert bridge.remove_addon("ff13-2", "FFXIII-2 - Console Content Patch")["ok"]
    for voice in "uc":
        now = held(game, voice)
        for code, (steam, _, _) in CUT.items():
            assert now[code] == before[voice][code] and now[code][0] == steam
        assert now[0x3000001][1] == blob("hd rain", 4100)
    assert bridge.remove_addon("ff13-2", "Rain")["ok"]
    assert {voice: held(game, voice) for voice in "uc"} == before


def test_its_patch_is_decoded_against_the_hd_backup_when_the_archive_was_repacked(
        world, tmp_path):
    """the HD Project repacks white_img, so only its backup is what the patch was made from"""
    bridge, game, _ = world
    files = steamify(game)
    spares = undo_store_for(game) / "hdproject"
    spares.mkdir(parents=True)
    sys_dir = game / "alba_data" / "sys"
    rows = steam_rows()
    for voice in "uc":
        (spares / f"filelist{voice}.win32.bin").write_bytes(files["index"])
        (spares / f"white_img{voice}.win32.bin").write_bytes(files["archive"])
        # repacked: the same files in another order, every place moved
        body, entries = pack(list(reversed(rows)))
        order = {code: e for code, e in entries}
        (sys_dir / f"white_img{voice}.win32.bin").write_bytes(body)
        (sys_dir / f"filelist{voice}.win32.bin").write_bytes(
            index_of([(code, order[code]) for code, _, _ in rows]))
    done = bridge.install_mod("ff13-2", str(drop(tmp_path, files, split=True)))
    assert done["ok"], done
    for voice in "uc":
        now = held(game, voice)
        assert all(now[code] == (path, payload or "stub")
                   for code, (_, path, payload) in CUT.items())


def test_a_game_that_isnt_what_its_patch_was_made_from_says_so_and_stays(world, tmp_path):
    bridge, game, _ = world
    files = steamify(game)
    sys_dir = game / "alba_data" / "sys"
    archive = sys_dir / "white_imgu.win32.bin"
    data = bytearray(archive.read_bytes())
    real = next(e for code, e in pack(steam_rows())[1] if code == 0x10F0100)
    data[real.byte_offset + 3] ^= 0xFF
    archive.write_bytes(bytes(data))
    before = sys_bytes(game)
    done = bridge.install_mod("ff13-2", str(drop(tmp_path, files)))
    assert not done["ok"]
    assert "isn't the Steam file its patch was made from" in done["error"]
    assert sys_bytes(game) == before
    assert gf.records(undo_store_for(game)) == []


def test_dropped_again_it_is_already_in(world, tmp_path):
    bridge, game, _ = world
    files = steamify(game)
    assert bridge.install_mod("ff13-2", str(drop(tmp_path, files)))["ok"]
    after = sys_bytes(game)
    again = bridge.install_mod("ff13-2", str(drop(tmp_path / "again", files)))
    assert again["ok"], again
    assert again["summary"] == "Console Content Patch is already in."
    assert sys_bytes(game) == after


def test_its_row_watches_for_the_download_from_pcgamingwiki():
    """
    PCGamingWiki's files carry no mod id the way Nexus's do, so its watch goes
    by the name, and only for the Console Content Patch
    """
    import fnmatch

    from patcher.bridge import UiBridge
    from tests.test_mod_drops import MANIFESTS

    bridge = UiBridge(manifest_paths={"ffxiii2": MANIFESTS / "ffxiii2.json"},
                      steam_library_paths=[])
    limits = bridge.download_filter("ff13-2", "dlcrestore")
    assert limits["nexus"] is None and limits["want"] == 1
    named = lambda name: any(fnmatch.fnmatch(name.lower(), g) for g in limits["names"])
    assert named("FFXIII-2_-_Console_Content_Patch_v2.3.7z")
    assert not named("FFXIII-2 HD - Main-1-1-0-1700000000.zip")
    assert limits["sizes"]["FFXIII-2_-_Console_Content_Patch_v2.3.7z"] == 7994027


SECOND = set(CUT) | {VERSION[0], 0x1180000, 0x1180100, 0x1188000, 0x1188100}


def two_chunk_index(rows) -> bytes:
    """
    the costumes' records in a second chunk. The cipher goes block by block by
    place, so an xdelta between two of these copies everything that didn't
    change, and the drawer rewriting the index changes what it copies
    """
    from patcher.archive.filelist import CHUNK_STRIDE

    groups = [[r for r in rows if r[0] not in SECOND], [r for r in rows if r[0] in SECOND]]
    chunks, lookups = [], []
    for n, group in enumerate(groups):
        chunk = bytearray()
        for code, entry in group:
            lookups.append((code, n * CHUNK_STRIDE + len(chunk)))
            chunk += format_entry(entry).encode() + b"\x00"
        if n == len(groups) - 1:
            chunk += b"end\x00"
        chunks.append(bytes(chunk))
    records = b"".join(struct.pack("<I", code) + at.to_bytes(3, "little") + bytes([16])
                       for code, at in sorted(lookups))
    book = Filelist(header=header(), entry_records=records, chunks=chunks,
                    original_blobs=[None, None], edited={0, 1}, entry_count=len(rows),
                    was_encrypted=False, remainder=b"\x00" * 8)
    return crypt.encrypt(rebuild(book, encrypt=False))


def copying_patch(source: bytes, target: bytes) -> bytes:
    """the way xdelta3 does it: every 8 bytes the two share at one place copied, the rest added"""
    same = lambda at: len(target[at:at + 8]) == 8 and source[at:at + 8] == target[at:at + 8]
    pieces, at = [], 0
    while at < len(target):
        end, copy = at, same(at)
        while end < len(target) and same(end) == copy:
            end += 8
        pieces.append(("copy", at, end - at) if copy else ("add", target[at:end]))
        at = end
    return vcdiff_patch(source, [pieces])


def test_its_index_patch_is_read_against_the_index_from_under_the_drawers_mods(world, tmp_path):
    bridge, game, _ = world
    sys_dir = game / "alba_data" / "sys"
    body, steam = pack(steam_rows())
    index = two_chunk_index(steam)
    for voice in "uc":
        (sys_dir / f"white_img{voice}.win32.bin").write_bytes(body)
        (sys_dir / f"filelist{voice}.win32.bin").write_bytes(index)
    files = {"index": index, "archive": body}
    rain = tmp_path / "Rain" / "vfx" / "field" / "rain.win32.imgb"
    rain.parent.mkdir(parents=True)
    rain.write_bytes(blob("hd rain", 4100))
    assert bridge.install_mod("ff13-2", str(tmp_path / "Rain"))["ok"]

    root = drop(tmp_path, files)
    _, _, _, release = patches(index, body)
    patch = copying_patch(index, two_chunk_index(release))
    for voice in "uc":
        (root / "mod" / "Misc" / f"filelist{voice}.win32.xdelta").write_bytes(patch)
    done = bridge.install_mod("ff13-2", str(root))
    assert done["ok"], done
    for voice in "uc":
        now = held(game, voice)
        assert all(now[code] == (path, payload or "stub")
                   for code, (_, path, payload) in CUT.items())
        assert now[0x3000001][1] == blob("hd rain", 4100)
    # and again, over its own rewrite of the index
    again = bridge.install_mod("ff13-2", str(root))
    assert again["ok"] and again["summary"] == "Console Content Patch is already in."


def test_a_record_it_copies_from_steams_duplicate_isnt_put_over_a_costume(world, tmp_path):
    """
    Steam's XIII-2 holds c201's files twice, and a patch can copy them from
    either place: from the other one it's the same bytes, and no change to put in
    """
    bridge, game, _ = world
    files = steamify(game)
    spares = undo_store_for(game) / "hdproject"
    spares.mkdir(parents=True)
    for voice in "uc":
        (spares / f"filelist{voice}.win32.bin").write_bytes(files["index"])
        (spares / f"white_img{voice}.win32.bin").write_bytes(files["archive"])
    costume = tmp_path / "Serah Costume" / "chr" / "pc" / "c201" / "bin" / "c201.win32.imgb"
    costume.parent.mkdir(parents=True)
    costume.write_bytes(blob("serah costume", 9100))
    assert bridge.install_mod("ff13-2", str(tmp_path / "Serah Costume"))["ok"]
    done = bridge.install_mod("ff13-2", str(drop(tmp_path, files)))
    assert done["ok"], done
    for voice in "uc":
        assert held(game, voice)[0x1180000][1] == blob("serah costume", 9100)
    (record,) = [r for r in gf.records(undo_store_for(game)) if r.get("recipe")]
    assert C201 + ".imgb" not in {i["path"] for s in record["pairs"] for i in s["entries"]}


def test_it_only_goes_into_the_indexes_its_bat_changes(world, tmp_path):
    """x000.bin listed in the script archive as well stays the game's there"""
    bridge, game, _ = world
    files = steamify(game)
    sys_dir = game / "alba_data" / "sys"
    body, rows = pack([(0x2000001, "btscene/pack/wdb/x000.bin", blob("x000", 2900))])
    for voice in "uc":
        (sys_dir / f"white_scr{voice}.win32.bin").write_bytes(body)
        (sys_dir / f"filelist_scr{voice}.win32.bin").write_bytes(index_of(rows))
    scr = {p.name: p.read_bytes() for p in sys_dir.glob("*scr*")}
    assert bridge.install_mod("ff13-2", str(drop(tmp_path, files)))["ok"]
    assert {p.name: p.read_bytes() for p in sys_dir.glob("*scr*")} == scr


def test_xdelta_patches_on_their_own_go_in_like_the_bats_do(world, tmp_path):
    """
    an index and archive patched together with no .bat round them, named the
    Console Content Patch's way. Read record by record, so the archives
    aren't written out again over whatever else is in them
    """
    import zipfile

    bridge, game, _ = world
    files = steamify(game)
    before = sys_bytes(game)
    index_patch, archive_patch, _, _ = patches(files["index"], files["archive"])
    drop = tmp_path / "Steam to Release-12-1-0-1760000000.zip"
    with zipfile.ZipFile(drop, "w") as archive:
        for voice in "uc":
            archive.writestr(f"patches/filelist{voice}.win32.xdelta", index_patch)
            archive.writestr(f"patches/white_img{voice}.win32.xdelta", archive_patch)
        archive.writestr("readme.txt", b"apply with xdelta")
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    assert done["message"].startswith("Patched white_imgc.win32.bin and white_imgu.win32.bin "
                                      "the way its xdelta patches say")
    for voice in "uc":
        now = held(game, voice)
        for code, (_, path, payload) in CUT.items():
            assert now[code] == (path, payload or "stub")
        assert now[VERSION[0]] == (VERSION[1], b"1.0.0.0 first")
    assert bridge.remove_addon("ff13-2", done["modName"])["ok"]
    assert sys_bytes(game) == before
