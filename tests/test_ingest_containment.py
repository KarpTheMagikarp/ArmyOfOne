"""
The one extraction in this project that is not ours to control.

Everything else routes through zipnames.safe_target, which picks the
destination itself and can refuse a member before a byte is written.
try_system_extract cannot: it hands the archive to whatever binary answers to
`tar` on this machine and is told where to put it. Two implementations answer
to that name on the platforms this ships to, and only one of them is the one
anybody tested.

So these attack the folder afterwards rather than trusting the extractor, and
they build the hostile archives for real rather than asserting on a mock.
"""

import os
import shutil
import tarfile
from pathlib import Path

import pytest

from patcher.ingest import _escapes, scan, try_system_extract

pytestmark = pytest.mark.skipif(shutil.which("tar") is None and shutil.which("bsdtar") is None,
                                reason="no system tar to extract with")


def tar_with(tmp_path: Path, name: str, build) -> Path:
    """a .tar the archive readers here refuse, so scan() falls to the system one"""
    staging = tmp_path / "build"
    staging.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / name
    with tarfile.open(archive, "w") as tf:
        build(tf, staging)
    return archive


def test_a_plain_archive_still_extracts(tmp_path):
    """the guard has to let the normal case through or it is just an outage"""
    def build(tf, staging):
        good = staging / "veffs.jp.win32.imgb"
        good.write_bytes(b"payload" * 100)
        tf.add(good, arcname="mod/veffs.jp.win32.imgb")

    archive = tar_with(tmp_path, "clean.tar", build)
    out = try_system_extract(archive, tmp_path / "out")

    assert out is not None
    assert (out / "mod" / "veffs.jp.win32.imgb").is_file()
    assert _escapes(out) is None


def test_a_member_that_climbs_out_is_refused(tmp_path):
    """
    the classic. ../../ in a member name, aimed at the folder above the one we
    asked for. bsdtar usually strips it, GNU tar usually refuses it, and
    "usually" is the entire reason this test exists.
    """
    def build(tf, staging):
        payload = staging / "evil.imgb"
        payload.write_bytes(b"owned")
        tf.add(payload, arcname="../../../escaped.imgb")

    archive = tar_with(tmp_path, "climb.tar", build)
    target = tmp_path / "out"
    result = try_system_extract(archive, target)

    # either the extractor refused it, or we did. what must not happen is a
    # file landing above the folder and being reported as a member.
    outside = tmp_path / "escaped.imgb"
    assert not outside.exists(), "a member was written outside the extraction folder"
    if result is not None:
        assert _escapes(result) is None


def test_an_absolute_member_is_refused(tmp_path):
    """the other half of the same trick, without any dots in it"""
    def build(tf, staging):
        payload = staging / "abs.imgb"
        payload.write_bytes(b"owned")
        info = tf.gettarinfo(payload, arcname=str(tmp_path / "planted.imgb"))
        with payload.open("rb") as handle:
            tf.addfile(info, handle)

    archive = tar_with(tmp_path, "absolute.tar", build)
    try_system_extract(archive, tmp_path / "out")

    assert not (tmp_path / "planted.imgb").exists(), "an absolute member was honoured"


def test_a_symlinked_file_is_refused_rather_than_followed(tmp_path):
    """
    Nothing is written outside the folder here, so containment alone says this
    is fine. It is not: the link is READ, and its bytes go into white_img at a
    real offset under a real entry. An archive cannot be allowed to nominate a
    file it does not contain.
    """
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"not the archive's to give")

    def build(tf, staging):
        link = staging / "veffs.jp.win32.imgb"
        os.symlink(secret, link)
        tf.add(link, arcname="mod/veffs.jp.win32.imgb")

    archive = tar_with(tmp_path, "linked.tar", build)
    out = tmp_path / "out"
    result = try_system_extract(archive, out)

    assert result is None, "a link in the tree was accepted"
    # and the reason is the link, not some unrelated failure
    if out.exists() and any(out.rglob("*")):
        assert "link" in (_escapes(out) or "")


def test_a_symlinked_directory_is_refused_before_it_is_walked(tmp_path):
    """
    the one rglob gets wrong. A link named as a directory makes rglob walk
    straight through it and report whatever is on the other side as members of
    the archive, so the walk has to refuse the link rather than follow it.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "borrowed.imgb").write_bytes(b"not ours")

    out = tmp_path / "out"
    (out / "mod").mkdir(parents=True)
    (out / "mod" / "real.imgb").write_bytes(b"ours")
    os.symlink(elsewhere, out / "mod" / "extra")

    reason = _escapes(out)

    assert reason and "link" in reason, reason


def test_scan_does_not_hand_back_an_escaping_extraction(tmp_path):
    """
    the guard has to sit under scan(), not only under the helper, because scan
    is what the drop path calls and what turns members into injection targets
    """
    from patcher.ingest import IngestError

    def build(tf, staging):
        payload = staging / "evil.imgb"
        payload.write_bytes(b"owned")
        tf.add(payload, arcname="../../../escaped.imgb")
        good = staging / "veffs.jp.win32.imgb"
        good.write_bytes(b"payload")
        tf.add(good, arcname="mod/veffs.jp.win32.imgb")

    # .rar so _members refuses it outright and the system extractor is reached
    archive = tar_with(tmp_path, "mixed.rar", build)

    try:
        found = scan(archive)
    except IngestError:
        pass          # refused, which is the other acceptable answer
    else:
        root = Path(found.source).resolve(strict=False)
        for candidate in found.candidates:
            landed = (Path(found.source) / candidate.member).resolve(strict=False)
            assert root in landed.parents, f"{candidate.member} resolves outside"

    assert not (tmp_path / "escaped.imgb").exists()
