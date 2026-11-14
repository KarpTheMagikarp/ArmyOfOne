"""
The name src_38 gave mods' dlls for NovaFix's popup, and the way back off
(patcher/pe_name.py). XIII-2 and LR stopped starting with HD Fonts and GUI's
renamed version.dll in, so nothing renames a mod's dll now and the list puts
every renamed one back to its author's bytes. named() is kept as the
definition of what's undone, so its own checks stay.
"""

import hashlib
import struct
from pathlib import Path

import pytest

from patcher import pe_name

REPO = Path(__file__).resolve().parent.parent

# what mingw's windres writes for a VERSIONINFO with these four strings, which
# is the layout rc.exe has always used
WINDRES = bytes.fromhex(
    "d00134000000560053005f00560045005200530049004f004e005f0049004e0046004f00000000"
    "00bd04effe00000100000001000000000000000100000000003f000000000000000400040002"
    "00000000000000000000000000000030010000010053007400720069006e006700460069006c"
    "00650049006e0066006f0000000c01000001003000340030003900300034006200300000004a"
    "0011000100460069006c0065004400650073006300720069007000740069006f006e00000000"
    "0048004400200046006f006e0074007300200061006e00640020004700550049000000000030"
    "0008000100460069006c006500560065007200730069006f006e000000000031002e0030002e"
    "0030002e0030000000420011000100500072006f0064007500630074004e0061006d00650000"
    "00000048004400200046006f006e0074007300200061006e0064002000470055004900000000"
    "00340008000100500072006f006400750063007400560065007200730069006f006e00000031"
    "002e0030002e0030002e0030000000440000000100560061007200460069006c00650049006e"
    "0066006f00000000002400040000005400720061006e0073006c006100740069006f006e0000"
    "0000000904b004")


def dll(resources=None, sections=1, signed=False, headers=0x400, section_align=0x1000,
        rsrc=None, vsize=None) -> bytes:
    """
    a PE32 laid out the way a linker lays one out: headers padded to the file
    alignment, a code section, and a resource section when `resources` is a
    tree of type -> name -> language -> bytes, or `rsrc(rva)` its bytes as
    they come. `vsize` is the last section's size in memory, when it says
    less than its size in the file
    """
    file_align = 0x200
    code = b"\x55\x8b\xec" + b"\x90" * 500
    bodies = [(b".text\x00\x00\x00", code, 0x60000020)]
    for n in range(1, sections):
        bodies.append((f".d{n}".encode().ljust(8, b"\x00"), bytes([n]) * 300, 0x40000040))
    rsrc_rva = section_align * (len(bodies) + 1)
    if resources is not None or rsrc is not None:
        body = rsrc(rsrc_rva) if rsrc else pe_name._build_tree(resources, rsrc_rva)
        bodies.append((b".rsrc\x00\x00\x00", body, 0x40000040))
    head = bytearray(headers)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HHIIIHH", head, 0x84, 0x14C, len(bodies), 0, 0, 0, 0xE0, 0x2102)
    optional = 0x98
    struct.pack_into("<H", head, optional, 0x10B)
    struct.pack_into("<II", head, optional + 32, section_align, file_align)
    struct.pack_into("<I", head, optional + 56, section_align * (len(bodies) + 1))
    struct.pack_into("<I", head, optional + 60, headers)
    struct.pack_into("<I", head, optional + 92, 16)
    out = bytearray(head)
    table = optional + 0xE0
    for n, (name, body, flags) in enumerate(bodies):
        va, raw_at = section_align * (n + 1), len(out)
        head_at = table + 40 * n
        out[head_at:head_at + 8] = name
        last = n == len(bodies) - 1 and vsize is not None
        struct.pack_into("<IIII", out, head_at + 8, vsize if last else len(body), va,
                         pe_name._align(len(body), file_align), raw_at)
        struct.pack_into("<I", out, head_at + 36, flags)
        out += body + bytes(-len(body) % file_align)
        if name.startswith(b".rsrc"):
            struct.pack_into("<II", out, optional + 96 + 16, va, len(body))
    if signed:
        struct.pack_into("<II", out, optional + 96 + 32, len(out), 8)
        out += b"WIN_CERT"
    return bytes(out)


def sections_of(raw: bytes) -> list:
    return [(s["name"].rstrip(b"\x00"), s["va"], s["vsize"]) for s in pe_name._layout(raw)["sections"]]


def test_the_version_resource_is_the_one_windres_writes():
    assert pe_name.version_info("HD Fonts and GUI") == WINDRES


def test_a_dll_with_no_name_gets_one_and_nothing_it_had_moves():
    raw = dll()
    named = pe_name.named(raw, "HD Fonts and GUI")
    assert pe_name.display_name(raw) == ""
    assert pe_name.display_name(named) == "HD Fonts and GUI"
    layout = pe_name._layout(named)
    assert sections_of(named)[:1] == sections_of(raw)
    new = layout["sections"][-1]
    assert new["va"] % 0x1000 == 0 and new["roff"] % 0x200 == 0 and new["roff"] >= len(raw)
    assert struct.unpack_from("<I", named, layout["image_size_at"])[0] == \
        pe_name._align(new["va"] + new["vsize"], 0x1000)
    # every byte of the code and everything after the headers is where it was
    assert named[0x400:len(raw)] == raw[0x400:]
    checksum = struct.unpack_from("<I", named, layout["checksum_at"])[0]
    assert checksum == pe_name._checksum(named, layout["checksum_at"]) and checksum


def test_the_resources_it_had_are_still_there_at_their_addresses():
    tree = {"HELLO": {"README": {0: b"hello from the old table"}},
            10: {"HELLO": {0x409: b"rcdata"}},
            24: {1: {0x409: b"<assembly/>"}}}
    raw = dll(tree, sections=2)
    before = pe_name._tree(raw, pe_name._layout(raw))
    named = pe_name.named(raw, "Some Mod")
    after = pe_name._tree(named, pe_name._layout(named))
    assert {k: v for k, v in after.items() if k != pe_name.RT_VERSION} == before
    assert list(after) == ["HELLO", 10, 16, 24], "named first, then ids in order"
    assert pe_name.display_name(named) == "Some Mod"


def test_a_dll_that_has_a_name_keeps_it():
    named = pe_name.named(dll(), "First")
    assert pe_name.named(named, "Second") is None


def test_a_version_resource_without_a_name_gives_its_numbers_to_the_one_that_has():
    unnamed = struct.pack("<13I", pe_name.SIGNATURE, 0x10000, 3 << 16 | 2, 7, 0, 0, 0x3F, 0,
                          0x40004, 2, 0, 0, 0)
    tree = {16: {1: {0x409: pe_name._block("VS_VERSION_INFO", unnamed)}}}
    named = pe_name.named(dll(tree), "Numbered")
    assert pe_name.display_name(named) == "Numbered"
    layout = pe_name._layout(named)
    (data, size, _), = pe_name._tree(named, layout)[16][1].values()
    at = pe_name._offset(layout, data)
    fixed = pe_name._blocks(named[at:at + size])[0][1]
    assert struct.unpack_from("<II", fixed, 8) == (3 << 16 | 2, 7)


@pytest.mark.parametrize("raw", [
    b"not a dll at all",
    dll(signed=True),
    # headers with no room for one more section's header
    dll(headers=0x200, sections=5),
], ids=["not a PE", "signed", "no room"])
def test_one_it_cant_name_without_moving_something_is_left_alone(raw):
    assert pe_name.named(raw, "Anything") is None


def test_the_dlls_the_patcher_puts_in_read_the_way_novafix_reads_them():
    """
    what the popup calls each, read off the real files. The exe redirect add-on
    has no name, and no Apply puts it in any more: it loaded too late to cover
    the DRM's check, which the dinput8 shim does now
    """
    mods = REPO / "assets" / "mods"
    said = {path.relative_to(mods).as_posix(): pe_name.display_name(path.read_bytes())
            for path in sorted(mods.rglob("*")) if path.suffix in (".dll", ".asi")
            and path.is_file()}
    assert said == {"ff13-code-patches/01_ff13_code_patches.asi": "Nova Chrysalia Code Patches",
                    "ff13-dinput8-shim/dinput8.dll": "FFXIII 4GB Compatibility",
                    "ff13-exe-redirect/00_ff13_exe_redirect.asi": "",
                    "lr-mouse/lr_mouse.asi": "LR Mouse Support",
                    "xiii-save-anywhere/xiii_save_anywhere.asi": "XIII Save Anywhere",
                    "xiii2-audio/xiii2_audio.asi": "XIII-2 Audio Fix",
                    "novafix/d3d9.dll": "NovaFix",
                    "reshade/d3d9.dll": "ReShade"}


def test_hd_fonts_goes_in_as_its_authors_bytes_and_comes_off(tmp_path, monkeypatch):
    from tests.test_mod_drops import library_with, zipped

    import patcher.bridge as bridge_mod
    from patcher.bridge import UiBridge

    library, game, paths = library_with(tmp_path)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    raw = dll()
    drop = zipped(tmp_path, "(STANDALONE VERSION) HD Fonts and GUI - Base mod-40-2-2-1779722868.zip",
                  {"version.dll": raw, "hd_textures/face000/a.dds": b"DDS a"})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    beside = game / "alba_data" / "prog" / "win" / "bin"
    assert (beside / "version.dll").read_bytes() == raw
    bridge.list_addons("ff13-2")
    assert (beside / "version.dll").read_bytes() == raw
    assert bridge.remove_addon("ff13-2", "(STANDALONE VERSION) HD Fonts and GUI - Base mod")["ok"]
    assert not (beside / "version.dll").exists()


def renamed_the_src38_way(beside, store, name: str, raw: bytes, original_on_record=True):
    """what src_38's install or its list draw left: the named copy beside the exe"""
    from patcher.mods import loader_mods

    loader_mods.install(beside, store, name, {"version.dll": "version.dll"}, lambda member: raw)
    named = pe_name.named(raw, "HD Fonts and GUI")
    record = loader_mods.records(store)[0]
    record["files"]["version.dll"] = hashlib.sha256(named).hexdigest()
    if original_on_record:
        record["was"] = {"version.dll": sorted({hashlib.sha256(raw).hexdigest(),
                                                hashlib.sha256(named).hexdigest()})}
    loader_mods._write(record)
    (beside / "version.dll").write_bytes(named)
    return named


@pytest.mark.parametrize("on_record", [True, False], ids=["renamed later", "went in named"])
def test_one_src38_renamed_goes_back_to_its_authors_bytes_when_the_list_is_drawn(
        tmp_path, monkeypatch, on_record):
    from tests.test_mod_drops import library_with

    import patcher.bridge as bridge_mod
    from patcher.bridge import UiBridge
    from patcher.mods import loader_mods
    from patcher.paths import undo_store_for

    library, game, paths = library_with(tmp_path)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    raw = dll({10: {"HELLO": {0x409: b"rcdata"}}})
    beside = game / "alba_data" / "prog" / "win" / "bin"
    named = renamed_the_src38_way(beside, undo_store_for(game), "HD Fonts and GUI", raw, on_record)
    bridge.list_addons("ff13-2")
    assert (beside / "version.dll").read_bytes() == raw
    record = loader_mods.records(undo_store_for(game))[0]
    assert record["files"]["version.dll"] == hashlib.sha256(raw).hexdigest()
    # the named copy stays its own, so a Remove takes either off
    (beside / "version.dll").write_bytes(named)
    assert bridge.remove_addon("ff13-2", "HD Fonts and GUI")["ok"]
    assert not (beside / "version.dll").exists()


def test_one_edited_since_or_with_the_game_running_is_left_be(tmp_path, monkeypatch):
    from tests.test_mod_drops import library_with

    import patcher.bridge as bridge_mod
    from patcher.bridge import UiBridge
    from patcher.paths import undo_store_for

    library, game, paths = library_with(tmp_path)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    raw = dll()
    beside = game / "alba_data" / "prog" / "win" / "bin"
    named = renamed_the_src38_way(beside, undo_store_for(game), "HD Fonts and GUI", raw)
    monkeypatch.setattr(bridge, "game_running", lambda game_id: "XIII-2 is running")
    bridge.list_addons("ff13-2")
    assert (beside / "version.dll").read_bytes() == named
    monkeypatch.setattr(bridge, "game_running", lambda game_id: "")
    (beside / "version.dll").write_bytes(named + b"edited")
    bridge.list_addons("ff13-2")
    assert (beside / "version.dll").read_bytes() == named + b"edited"


def overlaid(checksum: bool) -> bytes:
    """
    the way GNU ld leaves one: symbols past the sections, so the file ends off
    the alignment and on a zero, and a checksum it worked out itself
    """
    raw = bytearray(dll())
    pe = struct.unpack_from("<I", raw, 0x3C)[0]
    symbols = len(raw)
    struct.pack_into("<II", raw, pe + 12, symbols, 2)
    raw += bytes(36) + struct.pack("<I", 4 + 14) + b"_imp__fwrite\x00\x00"
    if checksum:
        at = 0x98 + 64
        struct.pack_into("<I", raw, at, pe_name._checksum(bytes(raw), at))
    return bytes(raw)


@pytest.mark.parametrize("raw", [
    dll(), dll({10: {"HELLO": {0x409: b"rcdata"}}}, sections=3), overlaid(False), overlaid(True),
    dll({16: {1: {0x409: pe_name._block("VS_VERSION_INFO", struct.pack(
        "<13I", pe_name.SIGNATURE, 0x10000, 3 << 16 | 2, 7, 0, 0, 0x3F, 0, 0x40004, 2, 0, 0, 0))}}}),
], ids=["plain", "resources", "symbols past the end", "and a checksum", "numbers, no name"])
def test_what_named_made_goes_back_to_exactly_what_it_was_handed(raw):
    named = pe_name.named(raw, "HD Fonts and GUI")
    assert named is not None and len(named) > len(raw)
    on_record = {hashlib.sha256(raw).hexdigest(), hashlib.sha256(named).hexdigest()}
    assert pe_name.unnamed(named, on_record) == raw


def test_with_nothing_on_record_only_a_file_with_one_way_back_goes_back():
    """
    a linker's file ends on the alignment with no checksum, so naming it back
    gives exactly what's there. One that ended past it could have ended on any
    of the zeros
    """
    raw = dll({10: {"HELLO": {0x409: b"rcdata"}}})
    assert pe_name.unnamed(pe_name.named(raw, "Mod")) == raw
    assert pe_name.unnamed(pe_name.named(raw, "Mod"), {"0" * 64}) == raw
    assert pe_name.unnamed(pe_name.named(overlaid(True), "Mod")) is None


def test_a_real_dll_named_the_src38_way_comes_back_byte_for_byte():
    """the exe redirect add-on is the one dll in assets with no name, so it stands in"""
    raw = (REPO / "assets" / "mods" / "ff13-exe-redirect" / "00_ff13_exe_redirect.asi").read_bytes()
    named = pe_name.named(raw, "FFXIII Exe Redirect")
    assert pe_name.unnamed(named, {hashlib.sha256(raw).hexdigest()}) == raw


@pytest.mark.parametrize("raw", [
    b"not a dll at all", dll(), dll(signed=True),
    pe_name.named(dll(), "Mod")[:-0x200],
], ids=["not a PE", "never named", "signed", "cut short"])
def test_a_file_named_didnt_make_is_left_as_it_is(raw):
    assert pe_name.unnamed(raw, {hashlib.sha256(raw).hexdigest()}) is None
    assert pe_name.unnamed(raw) is None


def test_a_section_called_what_named_calls_its_own_isnt_enough():
    """one more section than the file had, anywhere but straight after the last, isn't ours"""
    named = bytearray(pe_name.named(dll(), "Mod"))
    layout = pe_name._layout(bytes(named))
    ours = layout["sections"][-1]
    struct.pack_into("<I", named, ours["at"] + 12, ours["va"] + 0x1000)
    struct.pack_into("<I", named, layout["dirs_at"] + 16, ours["va"] + 0x1000)
    assert pe_name.unnamed(bytes(named)) is None


def shared(rva: int) -> bytes:
    """a resource tree whose three types all list one names directory, which fans out"""
    out = bytearray(struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 3))
    names_at = 16 + 8 * 3
    for kind in (3, 10, 24):
        out += struct.pack("<II", kind, 0x80000000 | names_at)
    out += struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 1, 0x80000000 | (names_at + 24))
    out += struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 0x409, names_at + 48)
    out += struct.pack("<IIII", rva, 4, 0, 0)
    return bytes(out)


def test_a_tree_listing_one_directory_twice_is_left_alone():
    """four thousand types on one directory on one directory is billions of visits off kilobytes"""
    raw = dll(rsrc=shared)
    assert pe_name.named(raw, "Fanned") is None
    assert pe_name.display_name(raw) == ""


def test_a_version_block_nested_past_any_real_one_is_read_without_recursing_into_it():
    block = b""
    for _ in range(3000):
        body = bytearray(struct.pack("<HHH", 0, 0, 0) + b"\x00\x00" + block)
        struct.pack_into("<H", body, 0, len(body) & 0xFFFF)
        block = bytes(body)
    raw = dll({16: {1: {0x409: block}}})
    assert pe_name.display_name(raw) == ""
    assert pe_name.display_name(pe_name.named(raw, "Deep")) == "Deep"


@pytest.mark.parametrize("at, value", [
    (0x98 + 36, 0x10000000),     # a file alignment no loader takes, a 512MB copy
    (0x98 + 32, 0x80000000),     # a section alignment that puts the new one past 4GB
    (0x98 + 32, 0x200),          # under a page, where an address has to be a place in the file
], ids=["file alignment", "section alignment", "low alignment"])
def test_alignments_a_linker_wouldnt_write_are_left_alone(at, value):
    raw = bytearray(dll())
    struct.pack_into("<I", raw, at, value)
    assert pe_name.named(bytes(raw), "Aligned") is None


def test_the_checksum_comes_to_what_adding_up_every_word_does():
    import os

    for size in (4096, 4097, 99999):
        raw = os.urandom(size)
        data = raw + bytes(size % 2)
        total = sum(struct.unpack(f"<{len(data) // 2}H", data)) - sum(struct.unpack_from("<HH", data, 64))
        while total >> 16:
            total = (total & 0xFFFF) + (total >> 16)
        assert pe_name._checksum(raw, 64) == total + size


def test_the_new_section_follows_the_last_ones_size_in_memory():
    """the loader steps on by that, and a section with a gap before it doesn't load"""
    raw = dll({24: {1: {0x409: b"<assembly/>" * 500}}}, vsize=0x800)
    last = pe_name._layout(raw)["sections"][-1]
    assert last["rsize"] > 0x1000 and last["vsize"] == 0x800
    new = pe_name._layout(pe_name.named(raw, "Packed"))["sections"][-1]
    assert new["va"] == last["va"] + 0x1000


def test_a_version_resource_under_a_name_or_another_id_isnt_a_name():
    """GetFileVersionInfo looks up id 1, so one compiled as "VS_VERSION_INFO" is never read"""
    for where in ("VS_VERSION_INFO", 2):
        raw = dll({16: {where: {0x409: pe_name.version_info("Hidden")}}})
        assert pe_name.display_name(raw) == ""
        named = pe_name.named(raw, "Shown")
        assert pe_name.display_name(named) == "Shown"
        assert where in pe_name._tree(named, pe_name._layout(named))[16]


def test_putting_back_waits_for_no_drop_and_a_drop_never_waits_for_it(tmp_path):
    """the drawer's list rewrites dlls on its own thread while a drop can be going in"""
    import threading

    from patcher.mods import loader_mods

    exe_dir, store = tmp_path / "bin", tmp_path / "store"
    exe_dir.mkdir()
    raw = dll()
    renamed_the_src38_way(exe_dir, store, "HD Fonts and GUI", raw)
    back = lambda data, digests: pe_name.unnamed(data, digests)
    going, done = threading.Event(), threading.Event()

    def drop():
        with loader_mods._lock:
            going.set()
            done.wait(5)

    other = threading.Thread(target=drop)
    other.start()
    going.wait(5)
    assert loader_mods.rewrite(exe_dir, store, back) == []
    assert pe_name.display_name((exe_dir / "version.dll").read_bytes()) == "HD Fonts and GUI"
    done.set()
    other.join()
    assert loader_mods.rewrite(exe_dir, store, back) == ["HD Fonts and GUI"]
    assert (exe_dir / "version.dll").read_bytes() == raw


def test_whose_dll_the_game_crashed_in_is_said_by_the_mod_or_its_own_name(tmp_path, monkeypatch):
    """for the launch check's sentence, which is the one thing a player can act on"""
    from tests.test_mod_drops import library_with

    import patcher.bridge as bridge_mod
    from patcher.bridge import UiBridge
    from patcher.mods import loader_mods
    from patcher.paths import undo_store_for

    library, game, paths = library_with(tmp_path)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    beside = game / "alba_data" / "prog" / "win" / "bin"
    loader_mods.install(beside, undo_store_for(game), "(STANDALONE VERSION) HD Fonts and GUI - Base mod",
                        {"version.dll": "version.dll"}, lambda member: dll())
    (beside / "d3d9.dll").write_bytes(pe_name.named(dll(), "NovaFix"))
    assert bridge.module_owner("ff13-2", "version.dll") == {"name": "HD Fonts and GUI",
                                                             "drawer": True}
    assert bridge.module_owner("ff13-2", str(beside / "VERSION.dll")) == {
        "name": "HD Fonts and GUI", "drawer": True}
    assert bridge.module_owner("ff13-2", "d3d9.dll") == {"name": "NovaFix", "drawer": False}
    # Windows' own, or one nothing here knows, has no one to name
    elsewhere = tmp_path / "Windows" / "System32" / "ntdll.dll"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes(pe_name.named(dll(), "Microsoft Windows"))
    assert bridge.module_owner("ff13-2", str(elsewhere)) == {}
    assert bridge.module_owner("ff13-2", "dsound.dll") == {}
