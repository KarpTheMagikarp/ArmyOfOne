import struct

import pytest

from patcher.patch_ops import (
    flip_laa_flag,
    is_laa_enabled,
    sha256_of,
    verify_checksum,
)
from tests.helpers import make_fake_pe


def test_is_laa_enabled_false_by_default():
    exe = make_fake_pe(characteristics=0x0000)
    assert is_laa_enabled(exe) is False


def test_is_laa_enabled_true_when_flag_set():
    exe = make_fake_pe(characteristics=0x0020)
    assert is_laa_enabled(exe) is True


def test_flip_laa_flag_sets_the_bit():
    exe = make_fake_pe(characteristics=0x0000)
    patched = flip_laa_flag(exe)
    assert is_laa_enabled(patched) is True


def test_flip_laa_flag_is_idempotent():
    exe = make_fake_pe(characteristics=0x0000)
    once = flip_laa_flag(exe)
    twice = flip_laa_flag(once)
    assert once == twice


def test_flip_laa_flag_preserves_other_bits():
    # some other random characteristic bit set, should survive the patch
    exe = make_fake_pe(characteristics=0x0100)
    patched = flip_laa_flag(exe)
    assert is_laa_enabled(patched) is True
    coff_offset = 0x80 + 4
    characteristics = struct.unpack_from("<H", patched, coff_offset + 18)[0]
    assert characteristics & 0x0100  # original bit still there


def test_flip_laa_flag_doesnt_change_file_length():
    exe = make_fake_pe(characteristics=0x0000)
    patched = flip_laa_flag(exe)
    assert len(patched) == len(exe)


def test_flip_laa_flag_rejects_bad_signature():
    exe = bytearray(200)
    struct.pack_into("<I", exe, 0x3C, 0x80)
    exe[0x80:0x84] = b"NOPE"
    with pytest.raises(ValueError):
        flip_laa_flag(bytes(exe))


def test_checksum_roundtrip():
    data = b"some fake exe bytes"
    digest = sha256_of(data)
    assert verify_checksum(data, digest) is True
    assert verify_checksum(data, "0" * 64) is False


def test_static_imports_are_read_off_a_real_pe():
    """
    The list that decides whether a shim can exist at all. The CEG check runs
    in the entry point, and the loader's only free opening in front of it is
    the DllMain of a statically imported DLL.
    """
    from pathlib import Path

    from patcher.patch_ops import read_static_imports

    asi = (Path(__file__).resolve().parent.parent
           / "assets" / "mods" / "ff13-exe-redirect" / "00_ff13_exe_redirect.asi")
    assert read_static_imports(asi.read_bytes()) == ["kernel32.dll", "msvcrt.dll"]


def test_static_imports_degrade_rather_than_raise():
    """
    it feeds a diagnostic, so "could not tell" has to be something it can
    print. a doctor run that dies on a truncated header helps nobody.
    """
    from patcher.patch_ops import read_static_imports

    assert read_static_imports(b"") == []
    assert read_static_imports(b"MZ" + b"\0" * 200) == []
    assert read_static_imports(b"not a pe at all") == []


def test_an_executable_with_no_imports_is_not_an_error():
    """distinct from an unreadable one, and both are normal answers"""
    import struct

    from patcher.patch_ops import read_static_imports

    # a PE32 header whose import data directory entry is zero
    data = bytearray(0x400)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x80 + 4 + 16, 224)      # SizeOfOptionalHeader
    struct.pack_into("<H", data, 0x80 + 24, 0x10B)        # PE32 magic

    assert read_static_imports(bytes(data)) == []
