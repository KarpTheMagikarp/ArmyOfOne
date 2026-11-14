"""
Mods that load themselves through a .dll named after one of Windows', beside
the game's exe with the folders they read from: HD Fonts and GUI is
version.dll, hd_textures and hd_textures_shaders.
"""

import hashlib
import json
import os
import struct
import zipfile
from pathlib import Path

import pytest

from patcher.mods import loader_mods
from patcher.mods.loader_mods import LoaderError


def pe32(managed: bool = False, dll: bool = True, machine: int = 0x014C) -> bytes:
    """a PE32 with an optional header, its CLR directory set for a .NET assembly"""
    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)
    coff = struct.pack("<HHIIIHH", machine, 0, 0, 0, 0, 224, 0x0102 | (0x2000 if dll else 0))
    optional = bytearray(224)
    struct.pack_into("<H", optional, 0, 0x10B)
    struct.pack_into("<I", optional, 92, 16)
    if managed:
        struct.pack_into("<II", optional, 96 + 14 * 8, 0x2008, 0x48)
    return bytes(dos) + b"PE\0\0" + coff + bytes(optional) + b"\0" * 512


def test_the_headers_say_what_a_dll_is():
    from patcher.addon_sieve import pe_kind

    assert pe_kind(pe32()) == {"machine": 0x014C, "dll": True, "managed": False}
    assert pe_kind(pe32(managed=True))["managed"]
    assert pe_kind(pe32(dll=False, managed=True)) == {"machine": 0x014C, "dll": False,
                                                       "managed": True}
    assert pe_kind(b"MZ") is None and pe_kind(b"not a pe at all" * 10) is None


def exe_dir(tmp_path) -> Path:
    where = tmp_path / "game" / "alba_data" / "prog" / "win" / "bin"
    where.mkdir(parents=True)
    (where / "ffxiii2img.exe").write_bytes(b"MZ the game")
    return where


FONTS = {"version.dll": pe32(), "hd_textures/hash_database.txt": b"hashes",
         "hd_textures/gui/font.dds": b"DDS " + b"f" * 100,
         "hd_textures_shaders/a.cso": b"shader"}


def put(where, store, name="HD Fonts", files=None, taken=None):
    files = dict(FONTS if files is None else files)
    return loader_mods.install(where, store, name, {m: m for m in files}, files.__getitem__,
                               taken=taken)


def test_it_goes_beside_the_exe_and_comes_off_whole(tmp_path):
    where, store = exe_dir(tmp_path), tmp_path / "undo"

    done = put(where, store)

    assert done["ok"] and done["files"] == 4 and done["dlls"] == ["version.dll"]
    assert done["folders"] == ["hd_textures", "hd_textures_shaders"]
    for member, data in FONTS.items():
        assert (where / member).read_bytes() == data
    assert not [p for p in where.rglob("*") if p.name.endswith((".patcher-new", ".patcher-old"))]
    record = loader_mods.records(store)[0]
    assert record["files"]["version.dll"] == hashlib.sha256(FONTS["version.dll"]).hexdigest()
    assert "pending" not in record
    assert loader_mods.listed(store) == [{"name": "HD Fonts", "kind": "loader", "managed": True,
                                          "files": ["hd_textures/", "hd_textures_shaders/",
                                                    "version.dll"],
                                          # what NovaFix's popup calls it, VERSION
                                          "about": "Loads as version.dll", "warn": ""}]

    gone = loader_mods.remove(where, store, "HD Fonts")

    assert gone["ok"] and not gone["left"]
    assert sorted(p.name for p in where.iterdir()) == ["ffxiii2img.exe"]
    assert loader_mods.records(store) == []


def test_a_file_already_there_that_isnt_its_own_is_never_written_over(tmp_path):
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    (where / "version.dll").write_bytes(b"somebody else's proxy")

    with pytest.raises(LoaderError, match="already a version.dll .* isn't from this app"):
        put(where, store)

    assert (where / "version.dll").read_bytes() == b"somebody else's proxy"
    assert not (where / "hd_textures").exists() and loader_mods.records(store) == []


def test_what_this_app_put_there_itself_is_named(tmp_path):
    """NovaFix is d3d9.dll, and a mod that's its own would take NovaFix out"""
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    (where / "d3d9.dll").write_bytes(b"NovaFix")

    with pytest.raises(LoaderError, match="d3d9.dll beside the game's exe is NovaFix"):
        put(where, store, "ReShade", {"d3d9.dll": pe32()}, taken={"d3d9.dll": "NovaFix"})

    assert (where / "d3d9.dll").read_bytes() == b"NovaFix"


def test_two_mods_cant_both_be_version_dll(tmp_path):
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store)

    with pytest.raises(LoaderError, match="version.dll beside the game's exe is HD Fonts's"):
        put(where, store, "Other Hook", {"version.dll": pe32() + b"other"})

    assert (where / "version.dll").read_bytes() == FONTS["version.dll"]


def test_an_update_replaces_its_own_files_and_drops_the_ones_it_doesnt_ship(tmp_path):
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store, files={**FONTS, "hd_textures/old/gone.dds": b"old", "fonts.ini": b"a=1"})
    (where / "fonts.ini").write_bytes(b"a=2, mine")

    done = put(where, store, files={**FONTS, "version.dll": pe32() + b"v2", "fonts.ini": b"a=1"})

    assert done["updated"] and done["kept"] == ["fonts.ini"]
    assert (where / "version.dll").read_bytes() == pe32() + b"v2"
    assert not (where / "hd_textures" / "old").exists(), "the old version's folder stayed"
    assert (where / "fonts.ini").read_bytes() == b"a=2, mine"
    assert (where / "fonts.ini.new").read_bytes() == b"a=1"
    assert len(loader_mods.records(store)) == 1

    # and again: the kept config is still the user's, not somebody else's file
    assert put(where, store, files={**FONTS, "fonts.ini": b"a=1"})["kept"] == ["fonts.ini"]
    gone = loader_mods.remove(where, store, "HD Fonts")
    assert gone["left"] == ["fonts.ini"] and (where / "fonts.ini").read_bytes() == b"a=2, mine"
    assert not (where / "fonts.ini.new").exists() and not (where / "version.dll").exists()


def test_a_dll_changed_since_isnt_replaced(tmp_path):
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store)
    (where / "version.dll").write_bytes(b"patched by hand")

    with pytest.raises(LoaderError, match="changed since HD Fonts put it there"):
        put(where, store, files={**FONTS, "version.dll": pe32() + b"v2"})

    assert (where / "version.dll").read_bytes() == b"patched by hand"
    gone = loader_mods.remove(where, store, "HD Fonts")
    assert gone["left"] == ["version.dll"] and (where / "version.dll").exists()


def test_antivirus_taking_a_file_as_it_lands_leaves_nothing_behind(tmp_path, monkeypatch):
    """
    Defender quarantines a flagged .dll as it's closed, so the read back is
    where that shows: the whole set backs out, and it says why in words that
    don't send anybody looking for a corrupt download
    """
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    real = loader_mods._digest

    def quarantined(path):
        if path.name == "version.dll" + loader_mods.NEW:
            path.unlink()
        return real(path)

    monkeypatch.setattr(loader_mods, "_digest", quarantined)
    with pytest.raises(LoaderError, match="Windows Security blocked version.dll"):
        put(where, store)

    assert sorted(p.name for p in where.iterdir()) == ["ffxiii2img.exe"]
    assert loader_mods.records(store) == []


def test_a_read_windows_refuses_is_said_as_a_threat_block(tmp_path):
    where, store = exe_dir(tmp_path), tmp_path / "undo"

    def blocked(member):
        error = OSError(22, "Operation did not complete successfully because the file "
                            "contains a virus")
        error.winerror = 225
        raise error

    with pytest.raises(LoaderError, match="Windows Security blocked version.dll"):
        loader_mods.install(where, store, "HD Fonts", {"version.dll": "version.dll"}, blocked)
    assert loader_mods.records(store) == []


def test_a_swap_that_fails_part_way_puts_the_last_version_back(tmp_path, monkeypatch):
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store)
    before = {p.relative_to(where).as_posix(): p.read_bytes() for p in where.rglob("*")
              if p.is_file()}
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if str(dst).replace("\\", "/").endswith("hd_textures_shaders/a.cso") and str(src).endswith(".patcher-new"):
            raise PermissionError(13, "held by the game", str(dst))
        return real(src, dst)

    monkeypatch.setattr(loader_mods.os, "replace", flaky)
    with pytest.raises(LoaderError, match="wasn't installed"):
        put(where, store, files={**FONTS, "version.dll": pe32() + b"v2",
                                 "hd_textures_shaders/a.cso": b"shader v2"})
    monkeypatch.setattr(loader_mods.os, "replace", real)

    after = {p.relative_to(where).as_posix(): p.read_bytes() for p in where.rglob("*")
             if p.is_file()}
    assert after == before
    record = loader_mods.records(store)[0]
    assert "pending" not in record and record["files"]["version.dll"] == \
        hashlib.sha256(FONTS["version.dll"]).hexdigest()


def test_a_crash_mid_install_leaves_a_record_remove_finishes_with(tmp_path):
    """the record goes down first, saying what each file may hold, leftovers included"""
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store)
    record = loader_mods.records(store)[0]
    new = pe32() + b"v2"
    # what the power going out between two swaps of an update leaves
    os.replace(where / "version.dll", where / ("version.dll" + loader_mods.OLD))
    (where / "version.dll").write_bytes(new)
    (where / ("hd_textures_shaders/a.cso" + loader_mods.NEW)).write_bytes(b"shader v2")
    path = Path(record.pop("_path"))
    record.update(pending=True, was=dict(record["files"]),
                  files=dict(record["files"], **{
                      "version.dll": hashlib.sha256(new).hexdigest(),
                      "hd_textures_shaders/a.cso": hashlib.sha256(b"shader v2").hexdigest()}))
    path.write_text(json.dumps(record), encoding="utf-8")

    gone = loader_mods.remove(where, store, "HD Fonts")

    assert gone["ok"] and not gone["left"], gone
    assert sorted(p.name for p in where.iterdir()) == ["ffxiii2img.exe"]


def test_nothing_goes_outside_the_exes_folder(tmp_path):
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    with pytest.raises(LoaderError, match="doesn't stay inside"):
        loader_mods.install(where, store, "Evil", {"x": "../evil.dll"}, lambda m: b"MZ")
    assert not (where.parent / "evil.dll").exists()


def test_a_folder_where_it_puts_a_file_stops_it(tmp_path):
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    (where / "version.dll").mkdir()
    with pytest.raises(LoaderError, match="folder called version.dll"):
        put(where, store)


def zipped(path: Path, files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in files.items():
            archive.writestr(member, data)
    return path


def leftovers(where):
    return sorted(p.relative_to(where).as_posix() for p in where.rglob("*")
                  if p.name.endswith((loader_mods.NEW, loader_mods.OLD)))


def test_a_disk_that_fills_while_staging_leaves_no_half_file(tmp_path, monkeypatch):
    """the side file is the undo's from before it's opened, not once it's written"""
    import errno

    where, store = exe_dir(tmp_path), tmp_path / "undo"
    real = Path.open

    class Full:
        def __init__(self, handle):
            self.handle = handle

        def write(self, data):
            self.handle.write(data[:10])
            self.handle.flush()
            raise OSError(errno.ENOSPC, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    def opener(self, mode="r", *args, **kwargs):
        handle = real(self, mode, *args, **kwargs)
        return Full(handle) if "w" in mode and self.name == "font.dds" + loader_mods.NEW \
            else handle

    monkeypatch.setattr(Path, "open", opener)
    with pytest.raises(LoaderError, match="wasn't installed"):
        put(where, store)
    monkeypatch.setattr(Path, "open", real)

    assert leftovers(where) == [] and loader_mods.records(store) == []
    assert sorted(p.name for p in where.iterdir()) == ["ffxiii2img.exe"]


def test_an_old_copy_that_wont_delete_stays_on_the_record(tmp_path, monkeypatch):
    """antivirus holding the old version.dll open: Remove takes it once it can"""
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store)
    real = Path.unlink

    def held(self, missing_ok=False):
        if self.name == "version.dll" + loader_mods.OLD:
            raise PermissionError(13, "Access is denied", str(self))
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held)
    done = put(where, store, files={**FONTS, "version.dll": pe32() + b"v2"})
    monkeypatch.setattr(Path, "unlink", real)

    assert done["ok"] and leftovers(where) == ["version.dll" + loader_mods.OLD]
    left = loader_mods.records(store)[0]["leftover"]
    assert list(left) == ["version.dll" + loader_mods.OLD], left
    assert hashlib.sha256(FONTS["version.dll"]).hexdigest() in left["version.dll" +
                                                                    loader_mods.OLD]
    gone = loader_mods.remove(where, store, "HD Fonts")
    assert gone["ok"] and not gone["left"] and leftovers(where) == []
    assert sorted(p.name for p in where.iterdir()) == ["ffxiii2img.exe"]


def test_an_update_again_after_one_was_cut_short_drops_what_that_was_dropping(tmp_path):
    """
    the power went out while v2 staged, so the record is v2's plan with v1 as
    what was there. Dropping v2 again still takes v1's extra file off, and the
    side files the cut left go with it
    """
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store, files={**FONTS, "hd_textures/old/gone.dds": b"old"})
    record = loader_mods.records(store)[0]
    path = Path(record.pop("_path"))
    v2 = {**FONTS, "version.dll": pe32() + b"v2"}
    record.update(pending=True, was=dict(record["files"]),
                  files={m: hashlib.sha256(d).hexdigest() for m, d in v2.items()})
    path.write_text(json.dumps(record), encoding="utf-8")
    (where / ("version.dll" + loader_mods.NEW)).write_bytes(v2["version.dll"])

    done = put(where, store, files=v2)

    assert done["ok"] and done["updated"], done
    assert not (where / "hd_textures" / "old").exists(), "v1's file is nobody's now"
    assert leftovers(where) == []
    assert (where / "version.dll").read_bytes() == v2["version.dll"]
    assert "was" not in loader_mods.records(store)[0]


def test_another_mod_that_only_shares_the_name_goes_in_beside_it(tmp_path):
    """
    two downloads both called Release.zip are two mods. A third drop by the
    name updates the one it shares files with
    """
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store, "Release", {"version.dll": pe32(), "fonts/a.dds": b"a"})

    other = put(where, store, "Release", {"winmm.dll": pe32() + b"other", "audio/b.ini": b"b"})

    assert other["ok"] and other["name"] == "Release (2)" and not other["updated"], other
    assert (where / "version.dll").exists() and (where / "fonts" / "a.dds").exists()
    again = put(where, store, "Release", {"winmm.dll": pe32() + b"v2", "audio/b.ini": b"b"})
    assert again["name"] == "Release (2)" and again["updated"], again
    assert (where / "winmm.dll").read_bytes() == pe32() + b"v2"
    assert sorted(r["name"] for r in loader_mods.records(store)) == ["Release", "Release (2)"]


def test_two_different_files_for_one_place_are_refused(tmp_path):
    """an x86 and an x64 folder each with its own hook.ini, or two options: no telling which"""
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    data = {"A/hook.ini": b"a=1", "B/hook.ini": b"a=2", "A/version.dll": pe32()}

    with pytest.raises(LoaderError, match="two different hook.ini files"):
        loader_mods.install(where, store, "Hook", {"A/hook.ini": "hook.ini",
                                                   "B/hook.ini": "hook.ini",
                                                   "A/version.dll": "version.dll"},
                            data.__getitem__)
    assert sorted(p.name for p in where.iterdir()) == ["ffxiii2img.exe"]

    data["B/hook.ini"] = b"a=1"
    done = loader_mods.install(where, store, "Hook", {"A/hook.ini": "hook.ini",
                                                      "B/hook.ini": "hook.ini",
                                                      "A/version.dll": "version.dll"},
                               data.__getitem__)
    assert done["ok"] and done["files"] == 2


def test_a_dll_whose_headers_start_past_the_first_page_is_still_one(tmp_path):
    """a long DOS stub is still a DLL, and the add-on sieve took these before"""
    import io

    from patcher import addon_sieve

    head = pe32()
    stub = bytearray(head[:0x80]) + bytearray(0x2000 - 0x80)
    struct.pack_into("<I", stub, 0x3C, 0x2000)
    far = bytes(stub) + head[0x80:]

    assert addon_sieve.pe_kind(addon_sieve.pe_head(io.BytesIO(far)))["machine"] == 0x014C
    staged = tmp_path / "stage"
    staged.mkdir()
    (staged / "Far.dll").write_bytes(far)
    assert [p.name for p in addon_sieve.sift(staged, "Far.zip").payloads] == ["Far.dll"]
    # and a stub pointing a long way off is no PE, read in a few bytes
    nowhere = bytearray(head[:0x80])
    struct.pack_into("<I", nowhere, 0x3C, 0x7FFFFFF0)
    assert addon_sieve.pe_kind(addon_sieve.pe_head(io.BytesIO(bytes(nowhere)))) is None


def test_a_side_file_held_too_hard_to_read_doesnt_fail_an_update_already_in(tmp_path,
                                                                            monkeypatch):
    """a sharing violation refuses the read as well as the delete, and v2 is in by then"""
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store, files={**FONTS, "hd_textures/old/gone.dds": b"old"})
    (where / ("hd_textures/old/gone.dds" + loader_mods.NEW)).write_bytes(b"what a cut left")
    held = {"version.dll" + loader_mods.OLD, "gone.dds" + loader_mods.NEW}
    real_read, real_unlink = Path.read_bytes, Path.unlink

    def read(self):
        if self.name in held:
            raise PermissionError(13, "being used by another process", str(self))
        return real_read(self)

    def unlink(self, missing_ok=False):
        if self.name in held:
            raise PermissionError(13, "Access is denied", str(self))
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "unlink", unlink)
    done = put(where, store, files={**FONTS, "version.dll": pe32() + b"v2"})
    monkeypatch.setattr(Path, "read_bytes", real_read)
    monkeypatch.setattr(Path, "unlink", real_unlink)

    record = loader_mods.records(store)[0]
    assert done["ok"] and "pending" not in record, record
    assert sorted(record["leftover"]) == ["hd_textures/old/gone.dds" + loader_mods.NEW,
                                          "version.dll" + loader_mods.OLD], record
    gone = loader_mods.remove(where, store, "HD Fonts")
    # the old dll goes once it can be read; what the cut left isn't any of its versions
    assert gone["ok"] and leftovers(where) == ["hd_textures/old/gone.dds" + loader_mods.NEW]


def test_an_update_after_a_cut_short_one_leaves_the_users_config_where_it_is(tmp_path):
    """
    v1's fonts.ini was edited, and the v2 update that kept it lost power. A v3
    that doesn't ship fonts.ini still leaves the user's own
    """
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store, files={**FONTS, "fonts.ini": b"a=1"})
    (where / "fonts.ini").write_bytes(b"a=2, mine")
    record = loader_mods.records(store)[0]
    path = Path(record.pop("_path"))
    sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
    v2 = {**FONTS, "version.dll": pe32() + b"v2"}
    record.update(pending=True, was={rel: [digest] for rel, digest in record["files"].items()},
                  files={**{m: sha(d) for m, d in v2.items()}, "fonts.ini.new": sha(b"a=1")},
                  kept={"fonts.ini": sha(b"a=2, mine")})
    path.write_text(json.dumps(record), encoding="utf-8")

    done = put(where, store, files={**FONTS, "version.dll": pe32() + b"v3"})

    assert done["ok"] and done["updated"], done
    assert (where / "fonts.ini").read_bytes() == b"a=2, mine"
    assert leftovers(where) == []


def test_one_hand_edited_record_doesnt_stop_the_rest_coming_off(tmp_path):
    """and it comes off itself, by the name the drawer shows it under"""
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store, "Aaa", {"winmm.dll": pe32() + b"a"})
    put(where, store)
    first = loader_mods.records(store)[0]
    path = Path(first.pop("_path"))
    first.update(name=None, was=["not", "a", "dict"], leftover=7, dirs="hd")
    path.write_text(json.dumps(first), encoding="utf-8")

    names = [row["name"] for row in loader_mods.listed(store, where)]
    assert loader_mods.remove(where, store, "HD Fonts")["ok"]
    assert not (where / "version.dll").exists()
    assert loader_mods.remove(where, store, names[0])["ok"]
    assert not (where / "winmm.dll").exists() and loader_mods.records(store) == []


def test_a_new_config_the_user_merged_their_settings_into_isnt_replaced(tmp_path):
    """
    v2 kept the user's fonts.ini and put its own beside it as fonts.ini.new,
    and the user merged into that. v3 doesn't write over it, and says so
    """
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store, files={**FONTS, "fonts.ini": b"a=1"})
    (where / "fonts.ini").write_bytes(b"a=2, mine")
    put(where, store, files={**FONTS, "version.dll": pe32() + b"v2", "fonts.ini": b"a=1\nb=1"})
    assert (where / "fonts.ini.new").read_bytes() == b"a=1\nb=1"
    (where / "fonts.ini.new").write_bytes(b"a=2\nb=1, merged by hand")

    with pytest.raises(LoaderError, match="fonts.ini.new beside the game's exe has changed"):
        put(where, store, files={**FONTS, "version.dll": pe32() + b"v3",
                                 "fonts.ini": b"a=1\nb=2"})

    assert (where / "fonts.ini.new").read_bytes() == b"a=2\nb=1, merged by hand"
    assert (where / "fonts.ini").read_bytes() == b"a=2, mine"
    assert (where / "version.dll").read_bytes() == pe32() + b"v2" and leftovers(where) == []


def test_settings_beside_the_exe_on_no_record_are_still_refused(tmp_path):
    """another mod's, put in by hand: only ReShade's own folder takes unrecorded settings as the user's"""
    exe_dir, store = tmp_path / "bin", tmp_path / "undo"
    (exe_dir / "scripts").mkdir(parents=True)
    (exe_dir / "scripts" / "global.ini").write_bytes(b"theirs=1")
    files = {"m/scripts/global.ini": "scripts/global.ini", "m/scripts/fix.asi": "scripts/fix.asi"}
    data = {"m/scripts/global.ini": b"ours=1", "m/scripts/fix.asi": b"MZ fix"}
    with pytest.raises(loader_mods.LoaderError, match="isn't from this app"):
        loader_mods.install(exe_dir, store, "Fix", files, data.__getitem__)
    assert (exe_dir / "scripts" / "global.ini").read_bytes() == b"theirs=1"


def test_installed_says_something_under_a_mod_only_when_its_dll_has_gone(tmp_path):
    """the line under every row said how it loads, which wasn't always so: names alone now"""
    where, store = exe_dir(tmp_path), tmp_path / "undo"
    put(where, store)
    assert loader_mods.listed(store, where)[0]["warn"] == ""
    (where / "version.dll").unlink()
    assert loader_mods.listed(store, where)[0]["warn"] == "version.dll is missing"
