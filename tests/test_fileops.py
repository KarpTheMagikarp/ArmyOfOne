from pathlib import Path

import pytest

from patcher import fileops
from patcher.fileops import write_atomic


def test_write_atomic_writes_the_file(tmp_path: Path):
    target = tmp_path / "game.exe"

    write_atomic(target, b"new content")

    assert target.read_bytes() == b"new content"


def test_write_atomic_leaves_no_tmp_file_behind_on_success(tmp_path: Path):
    target = tmp_path / "game.exe"

    write_atomic(target, b"new content")

    assert not (tmp_path / "game.exe.tmp").exists()


def test_write_atomic_never_corrupts_existing_file_on_failure(tmp_path: Path, monkeypatch):
    """
    the actual scenario this protects against: a write gets interrupted
    partway through (disk full, permission revoked mid-write, whatever).
    the real target file must come out of that exactly as it was before,
    not truncated or half-overwritten.
    """
    target = tmp_path / "game.exe"
    target.write_bytes(b"original content")

    def raise_partway(self, data):
        raise OSError("No space left on device")

    monkeypatch.setattr(Path, "write_bytes", raise_partway)

    try:
        write_atomic(target, b"new content that never lands")
        assert False, "expected an OSError"
    except OSError:
        pass

    # the mock replaced Path.write_bytes globally, so read via a plain
    # open() call instead of target.read_bytes() to check the real file
    with open(target, "rb") as f:
        assert f.read() == b"original content"


def test_write_atomic_cleans_up_tmp_file_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "game.exe"
    target.write_bytes(b"original content")

    real_write_bytes = Path.write_bytes

    def raise_only_for_tmp(self, data):
        if self.name.endswith(".tmp"):
            raise OSError("No space left on device")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", raise_only_for_tmp)

    try:
        write_atomic(target, b"new content")
    except OSError:
        pass

    assert not (tmp_path / "game.exe.tmp").exists()


def _held_when(monkeypatch, blocked):
    """os.replace that fails with a sharing violation whenever blocked(src, dst)"""
    real = fileops.os.replace

    def replace(src, dst, *a, **k):
        if blocked(Path(src), Path(dst)):
            raise PermissionError(32, "being used by another process")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(fileops.os, "replace", replace)


def test_a_swap_that_cannot_be_put_back_says_where_the_old_file_went(tmp_path, monkeypatch):
    """the lock that failed the swap fails the way back too, and the target is simply gone"""
    target = tmp_path / "white_scru.win32.bin"
    target.write_bytes(b"GAME")
    temp = tmp_path / "white_scru.win32.bin.part"
    temp.write_bytes(b"NEW")
    _held_when(monkeypatch, lambda src, dst: dst == target)

    with pytest.raises(fileops.Stranded) as caught:
        fileops.swap(temp, target)

    [(where, prev)] = caught.value.left
    assert where == target and not target.exists()
    assert prev.read_bytes() == b"GAME"


def test_a_pair_whose_rollback_fails_is_never_passed_off_as_untouched(tmp_path, monkeypatch):
    """the index went in, the archive did not, and the index could not come back out"""
    index, archive = tmp_path / "filelist_scru.win32.bin", tmp_path / "white_scru.win32.bin"
    index.write_bytes(b"OLD INDEX")
    archive.write_bytes(b"OLD ARCHIVE")
    staged = []
    for target, body in ((index, b"NEW INDEX"), (archive, b"NEW ARCHIVE")):
        temp = tmp_path / f"{target.name}.part"
        temp.write_bytes(body)
        staged.append((temp, target))
    _held_when(monkeypatch,
               lambda src, dst: (dst == archive and src.name.endswith(".part"))
               or (dst == index and src.name.endswith(".prev")))

    with pytest.raises(fileops.Stranded) as caught:
        fileops.replace_together(staged)

    [(where, prev)] = caught.value.left
    assert where == index and prev.read_bytes() == b"OLD INDEX"
    assert archive.read_bytes() == b"OLD ARCHIVE"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".part")]


def test_a_rename_held_for_a_moment_is_waited_out(tmp_path, monkeypatch):
    """antivirus scanning the file that was just written holds it for a second or two"""
    monkeypatch.setattr(fileops, "RENAME_WAITS", (0, 0, 0))
    source, target = tmp_path / "built.bin", tmp_path / "white_scru.win32.bin"
    source.write_bytes(b"NEW")
    target.write_bytes(b"OLD")
    refusals = [1, 1]
    _held_when(monkeypatch,
               lambda src, dst: dst == target and bool(refusals) and refusals.pop())

    fileops.copy_atomic(source, target)

    assert target.read_bytes() == b"NEW"


def test_a_rename_held_for_good_still_fails_and_leaves_the_target(tmp_path, monkeypatch):
    monkeypatch.setattr(fileops, "RENAME_WAITS", (0, 0))
    source, target = tmp_path / "built.bin", tmp_path / "white_scru.win32.bin"
    source.write_bytes(b"NEW")
    target.write_bytes(b"OLD")
    _held_when(monkeypatch, lambda src, dst: dst == target and src.name.endswith(".part"))

    with pytest.raises(PermissionError):
        fileops.copy_atomic(source, target)

    assert target.read_bytes() == b"OLD"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["built.bin", "white_scru.win32.bin"]


def test_a_durable_write_lands_the_same_bytes(tmp_path):
    target = tmp_path / "backup.json"
    write_atomic(target, b"{}", durable=True)
    assert target.read_bytes() == b"{}"


def test_a_write_waits_out_a_scan_holding_the_new_file(tmp_path, monkeypatch):
    """antivirus opens a file the moment it's written, and Windows refuses the rename meanwhile"""
    monkeypatch.setattr(fileops, "RENAME_WAITS", (0, 0))
    target = tmp_path / "filelistu.win32.bin"
    target.write_bytes(b"OLD")
    refusals = [1]
    _held_when(monkeypatch, lambda src, dst: dst == target and bool(refusals) and refusals.pop())

    write_atomic(target, b"NEW")

    assert target.read_bytes() == b"NEW"


def test_a_read_only_target_is_written_over_without_waiting(tmp_path, monkeypatch):
    """taken for a scan, a read-only file cost every bundled dll the whole wait"""
    import stat

    monkeypatch.setattr(fileops, "RENAME_WAITS", (0, 0, 0))
    target = tmp_path / "d3d9.dll"
    target.write_bytes(b"OLD")
    target.chmod(stat.S_IREAD)
    tries = []

    def windows(src, dst):
        tries.append(dst)
        return Path(dst) == target and not target.stat().st_mode & stat.S_IWRITE
    _held_when(monkeypatch, windows)

    write_atomic(target, b"NEW")

    assert target.read_bytes() == b"NEW"
    assert len(tries) == 1
