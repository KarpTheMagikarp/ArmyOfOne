"""
Nova Chrysalia's code patches without Nova: read the way the NovaFix add-on
reads them, checked against the game's exe and each other, and put where the
add-on finds them.
"""

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from patcher.mods import loader_mods, nccp
from tests.test_mod_drops import C108, library_with, zipped

SOURCE = Path(__file__).resolve().parent.parent / "addon-src" / "ff13-code-patches"


def pe(size: int = 0x200000) -> bytes:
    """an x86 exe with an optional header, which is where SizeOfImage lives"""
    head = bytearray(0x200)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HH", head, 0x84, 0x14C, 0)
    struct.pack_into("<H", head, 0x84 + 16, 0xE0)
    struct.pack_into("<H", head, 0x98, 0x10B)
    struct.pack_into("<I", head, 0x98 + 56, size)
    return bytes(head)


def vdf(*pairs, name="Mod") -> bytes:
    body = "".join(f'\t"{key}"\t"{value}"\n' for key, value in pairs)
    return f'"{name}"\n{{\n{body}}}\n'.encode()


def test_it_reads_what_nova_reads():
    raw = (b'\xef\xbb\xbf// made in Notepad\n"Mod"\n{\n'
           b'  "1" "2B54F|9090"   // nop the check\n'
           b'  2 0x1A2B30|EB05\n'
           b'  "Author" "someone"\n'
           b'  "3" "10 | 90 90 90"\n'
           b'  "nested" { "4" "10|90" }\n'
           b'  "5" "20|ABC" [$WIN32]\n'
           b'}\n')
    patches, skipped = nccp.parse(raw)
    assert patches == [nccp.Patch(0x2B54F, b"\x90\x90"), nccp.Patch(0x1A2B30, b"\xeb\x05"),
                       nccp.Patch(0x10, b"\x90\x90\x90")]
    # an odd count of hex digits is a key Nova would have thrown on mid-launch
    assert skipped == ["5"]


def test_a_patch_past_the_exe_or_one_that_wont_read_turns_the_file_away():
    exe = pe(0x1000)
    assert nccp.read_for(vdf(("1", "FFE|9090")), exe, "a.nccp") == [nccp.Patch(0xFFE, b"\x90\x90")]
    with pytest.raises(nccp.NccpError, match="past the end of this game's exe"):
        nccp.read_for(vdf(("1", "FFF|9090")), exe, "a.nccp")
    with pytest.raises(nccp.NccpError, match="doesn't read as an address"):
        nccp.read_for(vdf(("1", "10|90"), ("2", "zz|90")), exe, "a.nccp")
    with pytest.raises(nccp.NccpError, match="has no code patch"):
        nccp.read_for(b"patch", exe, "a.nccp")
    # no optional header to bound it by, so nothing can be said to fit
    with pytest.raises(nccp.NccpError):
        nccp.read_for(vdf(("1", "10|90")), b"MZ" + bytes(100), "a.nccp")


def test_overlap_is_only_two_writing_different_bytes_to_one_spot():
    a = [nccp.Patch(0x100, b"\x90\x90\x90")]
    assert nccp.overlap(a, [nccp.Patch(0x103, b"\xcc")]) is None
    assert nccp.overlap(a, [nccp.Patch(0x101, b"\x90")]) is None
    assert nccp.overlap(a, [nccp.Patch(0x0FF, b"\xcc\xcc")]) == a[0]


@pytest.fixture(scope="module")
def compiler():
    found = shutil.which("g++") or shutil.which("clang++")
    if found is None:
        pytest.skip("no host C++ compiler")
    return found


def test_the_addons_own_parser_tests_pass(compiler, tmp_path):
    binary = tmp_path / "nccp_test"
    build = subprocess.run([compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-o",
                            str(binary), str(SOURCE / "nccp_test.cpp")],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0 and "all good" in run.stdout, run.stdout


CORPUS = [
    vdf(("1", "2B54F|9090")),
    b'\xef\xbb\xbf"Mod" { "1" "0x10|EB 05" "x" "y" "2" "20|90|ignored" }',
    b'Mod { 1 10|90 2 "bad" 3 "|90" 4 "10|" 5 "0x|90" }',
    b'"Mod" { "1" "10|90" "sub" { "2" "20|90" { } } "3" "30|CC" }',
    b'"Mod" { "" "10|90" "1a" "20|90" "\xc2\xb2" "30|90" }',
    b'"Mod" { "1" "11112222333344445|90" "2" "1111222233334444|90" }',
    b'"Mod" { "1" "10|90" // comment "2" "20|90"\n "3" "30|90" [$X] "4" "40|90" }',
    b'"Mod" { "1" "10|90" } "Other" { "2" "20|90" }',
    b'"Mod" { "1" "10|9',
    # whitespace only to Python's isspace(), never to the add-on's
    b'"Mod" { "1" "1000|90\xa090" 2\x1f"20|90" "3" "30|\xc2\xa090" }',
    b'"Mod" { "1" }',
    b'',
]


def test_python_and_the_addon_read_every_file_the_same(compiler, tmp_path):
    """the check before install has to see exactly what the add-on will write"""
    driver = tmp_path / "dump.cpp"
    driver.write_text(f'#include "{(SOURCE / "nccp.h").as_posix()}"\n' + r'''
#include <cstdio>
#include <fstream>
#include <iterator>
int main(int, char** argv) {
    std::ifstream in(argv[1], std::ios::binary);
    std::string text((std::istreambuf_iterator<char>(in)), {});
    auto parsed = ff13_nccp::Parse(text);
    for (auto& p : parsed.patches) {
        std::printf("%llx:", static_cast<unsigned long long>(p.rva));
        for (auto b : p.bytes) std::printf("%02x", b);
        std::printf("\n");
    }
    for (auto& k : parsed.skipped) std::printf("skip:%s\n", k.c_str());
}
''')
    binary = tmp_path / "dump"
    build = subprocess.run([compiler, "-std=c++20", "-o", str(binary), str(driver)],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    for n, raw in enumerate(CORPUS):
        path = tmp_path / f"{n}.nccp"
        path.write_bytes(raw)
        theirs = subprocess.run([str(binary), str(path)], capture_output=True).stdout.decode()
        patches, skipped = nccp.parse(raw)
        ours = "".join(f"{p.rva:x}:{p.data.hex()}\n" for p in patches) + \
            "".join(f"skip:{k}\n" for k in skipped)
        assert ours == theirs, (raw, ours, theirs)


@pytest.fixture
def world(tmp_path, monkeypatch):
    library, game, paths = library_with(tmp_path)
    (game / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe").write_bytes(pe())
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    # the bundled build, or a stand-in where the checkout hasn't got one
    if not nccp.bundled().is_file():
        stand_in = tmp_path / "bundle" / "assets" / "mods" / nccp.FOLDER / nccp.ADDON
        stand_in.parent.mkdir(parents=True)
        stand_in.write_bytes(b"MZ addon")
        monkeypatch.setattr(nccp, "bundled", lambda: stand_in)
    return UiBridge(manifest_paths=paths, steam_library_paths=[library]), game, tmp_path


def folder_of(game) -> Path:
    return game / "alba_data" / "prog" / "win" / "bin" / "addons" / nccp.FOLDER


def test_a_code_patch_goes_in_beside_the_addon_and_comes_off(world):
    bridge, game, tmp = world
    drop = zipped(tmp, "Faster Battles.zip", {"Faster Battles/Code/Faster Battles.nccp":
                                              vdf(("1", "1234|9090"), ("2", "2000|EB05"))})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"] and done["kind"] == "loader", done
    assert "its 2 Nova Chrysalia code patches, which NovaFix applies in memory" \
        in done["message"], done
    # nothing loads it until NovaFix is on, and the line says so
    assert "Apply puts on" in done["summary"]
    folder = folder_of(game)
    assert (folder / "Faster Battles.nccp").read_bytes() == vdf(("1", "1234|9090"),
                                                                ("2", "2000|EB05"))
    assert (folder / nccp.ADDON).read_bytes() == nccp.bundled().read_bytes()
    row = next(r for r in bridge.loader_mods("ff13-2") if r["name"] == "Faster Battles")
    assert row["about"] == "Code patches, applied by NovaFix"
    # the add-on's folder is the patcher's, not a mod for the add-ons list
    assert nccp.FOLDER in bridge.our_addon_folders("ff13-2")

    assert bridge.remove_loader_mod("ff13-2", "Faster Battles")["ok"]
    assert not (folder / "Faster Battles.nccp").exists()


def test_one_made_for_another_exe_is_turned_away(world):
    bridge, game, tmp = world
    drop = zipped(tmp, "LR Patch.zip", {"Code/LR Patch.nccp": vdf(("1", "7FFFFF|90"))})
    done = bridge.install_mod("ff13-2", str(drop))
    assert not done["ok"]
    assert "past the end of this game's exe, so it's for another game" in done["error"], done
    assert not (folder_of(game) / "LR Patch.nccp").exists()


def test_two_mods_writing_the_same_code_differently_cant_both_be_in(world):
    bridge, game, tmp = world
    first = zipped(tmp, "One.zip", {"Code/One.nccp": vdf(("1", "1000|90909090"))})
    assert bridge.install_mod("ff13-2", str(first))["ok"]
    second = zipped(tmp, "Two.zip", {"Code/Two.nccp": vdf(("1", "1002|CC"))})
    done = bridge.install_mod("ff13-2", str(second))
    assert not done["ok"]
    assert "changes the same code as One's" in done["error"], done
    # one that only agrees with it is fine
    third = zipped(tmp, "Three.zip", {"Code/Three.nccp": vdf(("1", "1002|90"))})
    assert bridge.install_mod("ff13-2", str(third))["ok"]


def test_an_update_replaces_its_own_patches_rather_than_clashing_with_them(world):
    bridge, game, tmp = world
    old = zipped(tmp, "Mod.zip", {"Code/Mod.nccp": vdf(("1", "1000|90"))})
    assert bridge.install_mod("ff13-2", str(old))["ok"]
    new = zipped(tmp, "Mod v2.zip", {"Mod/Code/Mod.nccp": vdf(("1", "1000|CC"))})
    done = bridge.install_mod("ff13-2", str(new))
    assert done["ok"] and done["matchedExisting"], done
    assert (folder_of(game) / "Mod.nccp").read_bytes() == vdf(("1", "1000|CC"))


def test_a_nova_pack_with_data_and_code_is_one_mod_with_one_remove(world):
    bridge, game, tmp = world
    pack = zipped(tmp, "pack.ncmp", {
        "modconfig.ini": b"[ModPackConfig]\r\nName = Quick Menus\r\nGameEntry = 2\r\n"
                         b"[NovaChysaliaConfig]\r\nDataPatch = true\r\nCodePatch = true\r\n",
        f"Data/{C108}": b"d" * 3000, "Code/Quick Menus.nccp": vdf(("1", "4000|EB"))})
    done = bridge.install_mod("ff13-2", str(pack))
    assert done["ok"] and done["kind"] == "files" and done["modName"] == "Quick Menus", done
    assert (folder_of(game) / "Quick Menus.nccp").is_file()
    # its code patches are a part of the files row, not a row of their own
    assert not any(r["name"] == "Quick Menus" for r in bridge.loader_mods("ff13-2"))
    assert bridge.remove_file_mod("ff13-2", "Quick Menus")["ok"]
    assert not (folder_of(game) / "Quick Menus.nccp").exists()
    assert not loader_mods.records(bridge_mod.undo_store_for(game))


def test_a_version_shipping_its_patches_under_other_names_replaces_the_old_ones(world):
    bridge, game, tmp = world
    v1 = zipped(tmp, "Mod.zip", {"Code/Mod.nccp": vdf(("1", "1000|90"))})
    assert bridge.install_mod("ff13-2", str(v1))["ok"]
    v2 = zipped(tmp, "Mod v2.zip", {"Code/A.nccp": vdf(("1", "1000|CC")),
                                    "Code/B.nccp": vdf(("1", "2000|CC"))})
    done = bridge.install_mod("ff13-2", str(v2))
    assert done["ok"] and done["matchedExisting"], done
    assert sorted(p.name for p in folder_of(game).glob("*.nccp")) == ["Mod.A.nccp", "Mod.B.nccp"]


def test_two_of_its_own_files_clashing_is_turned_away(world):
    bridge, game, tmp = world
    drop = zipped(tmp, "Mod.zip", {"Code/A.nccp": vdf(("1", "1000|90")),
                                   "Code/B.nccp": vdf(("1", "1000|CC"))})
    done = bridge.install_mod("ff13-2", str(drop))
    assert not done["ok"] and "two of its files change the code at 1000" in done["error"], done


QUICK = b"[ModPackConfig]\r\nName = Quick\r\nGameEntry = 2\r\n"


def test_code_alone_then_a_version_with_game_files_is_still_an_update(world):
    bridge, game, tmp = world
    v1 = zipped(tmp, "Quick.ncmp", {"modconfig.ini": QUICK,
                                    "Code/Quick.nccp": vdf(("1", "4000|EB"))})
    assert bridge.install_mod("ff13-2", str(v1))["ok"]
    v2 = zipped(tmp, "Quick2.ncmp", {"modconfig.ini": QUICK, f"Data/{C108}": b"d" * 3000,
                                     "Code/Quick.nccp": vdf(("1", "4000|CC"))})
    done = bridge.install_mod("ff13-2", str(v2))
    assert done["ok"] and done["whole"], done
    assert (folder_of(game) / "Quick.nccp").read_bytes() == vdf(("1", "4000|CC"))
    # one mod now, under its files' row
    assert not any(r["name"].startswith("Quick") for r in bridge.loader_mods("ff13-2"))


def test_game_files_and_code_then_code_alone_is_still_an_update(world):
    bridge, game, tmp = world
    v1 = zipped(tmp, "Quick.ncmp", {"modconfig.ini": QUICK, f"Data/{C108}": b"d" * 3000,
                                    "Code/Quick.nccp": vdf(("1", "4000|EB"))})
    assert bridge.install_mod("ff13-2", str(v1))["ok"]
    v2 = zipped(tmp, "Quick2.ncmp", {"modconfig.ini": QUICK,
                                     "Code/Quick.nccp": vdf(("1", "4000|CC"))})
    done = bridge.install_mod("ff13-2", str(v2))
    assert done["ok"], done
    assert (folder_of(game) / "Quick.nccp").read_bytes() == vdf(("1", "4000|CC"))
    assert bridge.remove_loader_mod("ff13-2", "Quick")["ok"]
    assert not (folder_of(game) / "Quick.nccp").exists()


def test_one_writing_over_save_anywhere_waits_until_its_gone(tmp_path, monkeypatch):
    library, _, paths = library_with(tmp_path, xiii=True)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    if not nccp.bundled().is_file():
        stand_in = tmp_path / "bundle" / nccp.ADDON
        stand_in.parent.mkdir(parents=True)
        stand_in.write_bytes(b"MZ addon")
        monkeypatch.setattr(nccp, "bundled", lambda: stand_in)
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    bin_dir = library / "steamapps" / "common" / "FFXIII" / "white_data" / "prog" / "win" / "bin"
    (bin_dir / "ffxiiiimg.exe").write_bytes(pe(0x2400000))

    # without Save Anywhere in, the pad update hook is anyone's
    first = zipped(tmp_path, "Pad.zip", {"Code/Pad.nccp": vdf(("1", "4206C0|90"))})
    assert bridge.install_mod("ff13", str(first))["ok"]
    assert bridge.remove_loader_mod("ff13", "Pad")["ok"]

    home = bin_dir / "addons" / "xiii-save-anywhere"
    home.mkdir(parents=True)
    (home / "xiii_save_anywhere.asi").write_bytes(b"MZ")
    done = bridge.install_mod("ff13", str(first))
    assert not done["ok"]
    assert "Pad's code patch at 4206C0 changes the game code Save Anywhere hooks (pad update)" \
        in done["error"], done
    assert not (bin_dir / "addons" / nccp.FOLDER / "Pad.nccp").exists()

    elsewhere = zipped(tmp_path, "Calm.zip", {"Code/Calm.nccp": vdf(("1", "1000|90"))})
    assert bridge.install_mod("ff13", str(elsewhere))["ok"]
