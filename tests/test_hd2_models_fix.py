"""
The fixed copy of FF XIII HD Models: made only from the build it was worked out
on, only ever as a copy, and checked by hash on the way out as well as in.

The last test runs against the real exe when HD2MODELS names it:

    set HD2MODELS=C:\\FF13-2HD\\supportfiles\\FF XIII HD Models.exe
    py -3 -m pytest tests/test_hd2_models_fix.py
"""

import hashlib
import os
from pathlib import Path

import pytest

from patcher.mods import hd2_models_fix
from patcher.mods.hd2_models_fix import fixed_copy

THEIRS, OURS = "FF XIII HD Models.exe", "FF XIII HD Models.fixed.exe"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def build(tmp_path, monkeypatch):
    """a stand-in exe with the jump at 0x40, and FIXES taught its hash"""
    support = tmp_path / "supportfiles"
    support.mkdir()
    original = bytes(range(256)) * 4
    fixed = bytearray(original)
    fixed[0x40] = 0xE9
    (support / THEIRS).write_bytes(original)
    monkeypatch.setattr(hd2_models_fix, "FIXES",
                        {sha(original): (0x40, original[0x40], 0xE9, sha(bytes(fixed)))})
    return support, original, bytes(fixed)


def test_the_known_build_gets_a_fixed_copy_and_keeps_its_own_exe(build):
    support, original, fixed = build

    made = fixed_copy(support, THEIRS, OURS)

    assert made == support / OURS
    assert made.read_bytes() == fixed
    assert (support / THEIRS).read_bytes() == original


def test_a_good_copy_already_there_is_used_as_it_is(build, monkeypatch):
    support, original, fixed = build
    (support / OURS).write_bytes(fixed)
    monkeypatch.setattr("patcher.fileops.write_atomic",
                        lambda *a, **k: pytest.fail("rewrote a copy that was already right"))

    assert fixed_copy(support, THEIRS, OURS) == support / OURS


def test_a_copy_that_is_not_the_fix_is_made_again(build):
    support, original, fixed = build
    (support / OURS).write_bytes(b"MZ something else")

    assert fixed_copy(support, THEIRS, OURS) == support / OURS
    assert (support / OURS).read_bytes() == fixed


def test_any_other_build_runs_as_it_is(build):
    support, original, fixed = build
    (support / THEIRS).write_bytes(original + b"\x00")

    assert fixed_copy(support, THEIRS, OURS) is None
    assert not (support / OURS).exists()


def test_a_folder_it_cannot_write_to_falls_back_to_theirs(build, monkeypatch):
    support, original, fixed = build

    def refuse(*a, **k):
        raise PermissionError("read only")
    monkeypatch.setattr("patcher.fileops.write_atomic", refuse)

    assert fixed_copy(support, THEIRS, OURS) is None


def test_no_tool_at_all_is_no_copy(tmp_path):
    assert fixed_copy(tmp_path, THEIRS, OURS) is None


REAL = Path(os.environ.get("HD2MODELS", ""))


@pytest.mark.skipif(not REAL.name or not REAL.is_file(),
                    reason="HD2MODELS does not name FF XIII HD Models.exe")
def test_the_real_v4_build_comes_out_as_the_known_fix(tmp_path):
    (tmp_path / THEIRS).write_bytes(REAL.read_bytes())

    made = fixed_copy(tmp_path, THEIRS, OURS)

    assert made is not None, "not the v4.0.0 build the fix was made for"
    theirs, ours = REAL.read_bytes(), made.read_bytes()
    assert [i for i in range(len(theirs)) if theirs[i] != ours[i]] == [0xDFDA]
    # je rel32 at 0x40ebd8: the empty table used to step to 0x40eca5, now it returns
    assert theirs[0xDFD8:0xDFDE] == bytes.fromhex("0f84c7000000")
    assert ours[0xDFD8:0xDFDE] == bytes.fromhex("0f84e9000000")
