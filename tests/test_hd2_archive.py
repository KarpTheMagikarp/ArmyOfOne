"""
XIII-2's encrypted indexes, against what the real tools do to them.

data/hd2/real was recorded by running ff13tool v1.4 and ffxiiicrypt, the builds
FF XIII-2 HD ships, under wine on a small pair in the game's own container.
These tests hold our open, seal and read-back to those outputs, so a change
that drifts from the tools the game's files have always gone through fails
here rather than in somebody's game.
"""

import hashlib
import struct
from pathlib import Path

import pytest

from patcher.archive import crypt
from patcher.archive.filelist import load, parse
from patcher.mods.hd2_archive import SealError, open_index, seal, seal_index
from patcher.mods.hd_zones import check_archive, read_entry

REPO = Path(__file__).resolve().parent.parent
REAL = REPO / "tests" / "data" / "hd2" / "real"
SAMPLE = REPO / "tests" / "data" / "filelistu_sample.bin"


def stored_checksum(plain: bytes) -> int:
    return struct.unpack_from("<I", plain, 32 + crypt.body_size(plain) - 4)[0]


def test_opening_matches_ffxiiicrypt(tmp_path):
    open_index(REAL / "pair.idx", tmp_path / "opened")

    assert (tmp_path / "opened").read_bytes() == (REAL / "pair.idx.opened_by_ffxiiicrypt").read_bytes()


def test_opening_the_real_filelistu_matches_ffxiiicrypt(tmp_path):
    # ffxiiicrypt -d on the user's own filelistu, hashed when it was run
    open_index(SAMPLE, tmp_path / "opened")

    digest = hashlib.sha256((tmp_path / "opened").read_bytes()).hexdigest()
    assert digest == "03ee34818402bf6b46f817694a6100323adcfbc23772f14429b82697002b72bf"


def test_ff13tool_leaves_the_checksum_at_zero():
    """
    why sealing writes one. Their .bat encrypts this as it is, and ffxiiicrypt
    -e does not fix it either
    """
    plain = (REAL / "repacked.idx.plain").read_bytes()

    assert crypt.looks_decrypted(plain)
    assert stored_checksum(plain) == 0
    assert crypt.checksum(plain) != 0


def test_sealing_matches_their_tools_when_they_get_it_right():
    """
    the reference is ff13tool writing the checksum itself, which it only does
    when ffxiiicrypt sits in its working directory, then ffxiiicrypt -e
    """
    plain = (REAL / "repacked.idx.plain").read_bytes()

    assert seal(plain) == (REAL / "repacked.idx.sealed_by_their_tools").read_bytes()


def test_the_real_repack_reads_back_once_sealed(tmp_path):
    sealed = tmp_path / "filelistu.win32.bin"
    sealed.write_bytes((REAL / "repacked.idx.plain").read_bytes())
    seal_index(sealed)

    before = list(load(REAL / "pair.idx").entries())
    assert check_archive(sealed, REAL / "repacked.img", since=before) == ""
    grown = read_entry(REAL / "repacked.img", list(load(sealed).entries()),
                       "chr/pc/c108/bin/c108.win32.imgb")
    assert grown.startswith(b"HD")


def test_an_import_appends_and_leaves_everything_else_where_it_was(tmp_path):
    """
    what ImageGuard's undo assumes, now measured rather than guessed: the file
    goes on the end, the index points there, and not a byte before it moves
    """
    sealed = tmp_path / "filelistu.win32.bin"
    sealed.write_bytes(seal((REAL / "imported.idx.plain").read_bytes()))
    before = {e.path: e for e in load(REAL / "pair.idx").entries()}
    after = {e.path: e for e in load(sealed).entries()}
    vanilla, imported = (REAL / "pair.img").read_bytes(), (REAL / "imported.img").read_bytes()

    assert imported[:len(vanilla)] == vanilla
    moved = [p for p in after if (after[p].sector_offset, after[p].stored)
             != (before[p].sector_offset, before[p].stored)]
    assert moved == ["zone/filelist_z0010u.win32.bin"]
    assert after[moved[0]].byte_offset >= len(vanilla)
    assert check_archive(sealed, REAL / "imported.img", since=list(before.values())) == ""
    assert read_entry(REAL / "imported.img", list(after.values()), moved[0]) == \
        (REAL / "imported.payload").read_bytes()


def test_opening_something_already_open_changes_nothing(tmp_path):
    target = tmp_path / "opened"
    open_index(REAL / "pair.idx", target)
    first = target.read_bytes()

    open_index(target)

    assert target.read_bytes() == first


def test_sealing_is_refused_for_what_is_not_an_open_index(tmp_path):
    with pytest.raises(SealError):
        seal((REAL / "pair.idx").read_bytes())        # already sealed
    with pytest.raises(SealError):
        seal(b"\x00" * 256)


def test_a_file_that_is_not_an_index_will_not_open(tmp_path):
    junk = tmp_path / "filelistu.win32.bin"
    junk.write_bytes(b"not a filelist at all" * 20)

    with pytest.raises(SealError):
        open_index(junk, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_sealed_index_reads_the_same_as_the_open_one(tmp_path):
    plain = (REAL / "imported.idx.plain").read_bytes()
    sealed = seal(plain)

    assert crypt.is_encrypted(sealed)
    assert [(e.path, e.sector_offset, e.uncompressed, e.stored) for e in parse(sealed).entries()] \
        == [(e.path, e.sector_offset, e.uncompressed, e.stored) for e in parse(plain).entries()]
