"""
small shared file-writing helper used by both the binary patcher and
the file installer, so both get the same crash-safety guarantee
without duplicating the logic.
"""

import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

from patcher.access import clear_readonly

COPY_CHUNK = 4 * 1024 * 1024

# antivirus opens a freshly written file to scan it, and on Windows that
# handle fails a rename for as long as the scan takes. A few seconds of
# patience beats throwing away a forty minute build over it.
RENAME_WAITS = (0.1, 0.25, 0.5, 1.0, 2.0) if sys.platform == "win32" else ()


class CopyCancelled(Exception):
    pass


class CopyRefused(OSError):
    """the bytes that came through are not the ones we were promised"""


class Stranded(OSError):
    """
    a swap that failed and then could not be taken back either. `left` is
    [(target, prev)] for each target whose own copy is still sitting at prev,
    which is the one fact whoever catches this has to pass on.
    """

    def __init__(self, message: str, left: list):
        super().__init__(message)
        self.left = left


def scratch_near(anchor: Path) -> str | None:
    """
    a directory on the same volume as anchor, for tempfile's dir argument.
    None means "no better idea than %TEMP%". %TEMP% is on C: for nearly
    everyone, and a 4GB mod for a game on D: shouldn't fill the system drive.
    Same volume also makes the final move a rename.
    """
    probe = Path(anchor)
    try:
        probe = probe.resolve()
        while not probe.is_dir() and probe.parent != probe:
            probe = probe.parent
        return str(probe) if probe.is_dir() else None
    except OSError:
        return None


def temp_sibling(target_path: Path, tag: str = "tmp") -> Path:
    """
    a scratch name beside target_path that won't blow the path limit.

    A Steam library under a OneDrive Documents folder can already be near 255,
    and +".tmp" then fails as path-not-found on a folder that exists. Out of
    room, the name is replaced by a digest of it, not truncated: truncation
    collides (ffxiiiimg.exe and ffxiiiimg_dx11.exe), and since backup_path_for
    uses this, the second exe would reuse the first one's backup.
    """
    suffixed = target_path.with_name(f"{target_path.name}.{tag}")
    if len(str(suffixed)) < 255 and suffixed != target_path:
        return suffixed

    digest = hashlib.sha1(
        target_path.name.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    short = target_path.with_name(f"{digest}.{tag}")
    # a target already named exactly like a digest would make write_atomic
    # write onto the file it is protecting, and then delete it on failure
    return short if short != target_path else target_path.with_name(f"{digest}_.{tag}")


def write_atomic(target_path: Path, data: bytes, durable: bool = False) -> None:
    """
    writes data to target_path without ever leaving it half written: temp file
    first, then Path.replace, which is atomic on Windows and POSIX. On failure
    the temp goes and the target is whatever it was before. durable fsyncs
    before the rename, for a file trusted by name afterwards, since a power cut
    can keep the rename and lose the data.
    """
    tmp_path = temp_sibling(target_path)
    try:
        if durable:
            with tmp_path.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        else:
            tmp_path.write_bytes(data)
        # windows refuses a replace over a read-only file outright, and mods
        # unpacked by tools that keep archive attributes arrive that way. Then
        # patient, since antivirus scanning the temp just written holds the
        # rename for as long as the scan takes
        clear_readonly(target_path)
        _replace(tmp_path, target_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            # cleanup failing must never replace the exception that explains
            # why we are here. a stray .tmp is cosmetic; losing the real
            # error is not.
            pass
        raise


def discard(path: Path) -> None:
    """a scratch file gone, if it can be. Never raises: it runs inside error
    handlers, where the exception already in flight is the one worth keeping."""
    try:
        path = Path(path)
        clear_readonly(path)
        path.unlink(missing_ok=True)
    except OSError:
        pass


def clear_tree(folder: Path) -> None:
    """
    a folder of ours gone, read-only files included. Extractors that keep the
    archive's attributes leave read-only files, and rmtree on Windows stops at
    the first one of those. Best effort: what is left over is checked for.
    """
    def writable(func, path, exc):
        clear_readonly(Path(path))
        func(path)

    try:
        try:
            shutil.rmtree(folder, onexc=writable)
        except TypeError:
            # 3.11 and older call it onerror, with the same three arguments
            shutil.rmtree(folder, onerror=writable)
    except OSError:
        pass


def unlink(path: Path) -> None:
    """a file gone, waiting out something on Windows that has it open for a moment"""
    for wait in RENAME_WAITS:
        try:
            Path(path).unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(wait)
    Path(path).unlink(missing_ok=True)


def _replace(source: Path, target: Path) -> None:
    for wait in RENAME_WAITS:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(wait)
    os.replace(source, target)


def stage_copy(source: Path, target: Path, *, expect: str | None = None,
               durable: bool = True, cancel=None, on_share=None) -> tuple[Path, str]:
    """
    source copied into a temp sibling of target, ready to swap in. Returns
    (temp, sha256); target isn't touched. Hashed on the way through so a
    multi-GB file isn't read twice, and `expect` refuses a mismatch before it
    gets near the real name. durable=False skips the fsync, only safe for a
    copy nobody will trust by name.
    """
    source, target = Path(source), Path(target)
    temp = temp_sibling(target, "part")
    digest = hashlib.sha256()
    total = source.stat().st_size
    done = shown = 0
    try:
        with source.open("rb") as src, temp.open("wb") as dst:
            while block := src.read(COPY_CHUNK):
                if cancel is not None and cancel.is_set():
                    raise CopyCancelled(f"stopped while copying {source.name}")
                dst.write(block)
                digest.update(block)
                done += len(block)
                # whole percents only, a 4GB archive is a thousand chunks
                if on_share and total and done * 100 // total > shown:
                    shown = done * 100 // total
                    on_share(done / total)
            if durable:
                dst.flush()
                os.fsync(dst.fileno())
        if done != total:
            raise OSError(f"{source.name} changed size while it was being copied")
        got = digest.hexdigest()
        if expect is not None and got != expect:
            raise CopyRefused(f"{source.name} is not the file that was recorded "
                              f"(sha256 {got[:12]}, expected {expect[:12]})")
    except BaseException:
        discard(temp)
        raise
    return temp, got


def swap(temp: Path, target: Path) -> Path | None:
    """
    temp over target, keeping the old target aside. Returns where the old one
    went, or None. Two renames; a failure between them is put right here, and
    if that fails too it's Stranded, since the caller has to know the target
    is missing. unswap() puts the old one back, drop() lets it go.
    """
    temp, target = Path(temp), Path(target)
    prev = None
    if target.exists():
        prev = temp_sibling(target, "prev")
        discard(prev)
        _replace(target, prev)
    try:
        _replace(temp, target)
    except BaseException as e:
        if prev is not None:
            try:
                _replace(prev, target)
            except OSError:
                raise Stranded(f"{target.name} could not be put back, its old copy is "
                               f"beside it as {prev.name}", [(target, prev)]) from e
        raise
    return prev


def unswap(target: Path, prev: Path | None) -> None:
    """undoes swap(): the old file back, or no file if there wasn't one"""
    target = Path(target)
    if prev is None:
        clear_readonly(target)
        target.unlink(missing_ok=True)
    else:
        _replace(prev, target)


def drop(prev: Path | None) -> None:
    if prev is not None:
        discard(prev)


def replace_together(staged) -> None:
    """
    [(temp, target), ...] swapped in as a set, all or none, for files that
    only mean anything together (an index and its archive). Temps are already
    complete, so it's just renames, and a failure sends the done ones back.
    If that fails too it's Stranded, naming everything out of place, never a
    quiet mismatched pair.
    """
    done = []
    try:
        for temp, target in staged:
            done.append((target, swap(temp, target)))
    except BaseException as e:
        stuck = list(e.left) if isinstance(e, Stranded) else []
        for target, prev in reversed(done):
            try:
                unswap(target, prev)
            except OSError:
                stuck.append((target, prev))
        for temp, _ in staged:
            discard(temp)
        if stuck and not (isinstance(e, Stranded) and stuck == e.left):
            names = " and ".join(Path(t).name for t, _ in stuck)
            raise Stranded(f"{names} could not be put back after the swap failed ({e})",
                           stuck) from e
        raise
    for _, prev in done:
        drop(prev)


def copy_atomic(source: Path, target: Path, **options) -> str:
    """stage_copy() and swap it in. target is the old file or all of the new one."""
    temp, digest = stage_copy(source, target, **options)
    replace_together([(temp, target)])
    return digest


class Held(OSError):
    """another run already has this game's archives"""


class Claim:
    """a held claim(). Release it, or use it in a with block."""

    def __init__(self, handle):
        self._handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()

    def release(self) -> None:
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        # Windows frees a lock on close when it gets round to it, and a rebuild
        # claims twice in a row
        if sys.platform == "win32":
            try:
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()


def claim(store: Path) -> Claim:
    """
    one writer at a time on a game's archives, across processes and threads.
    An HD install, an injection and a terminal --revert all rewrite the same
    white_img. A lock on a file in the undo store dies with the process, so a
    crash never leaves one behind. Not reentrant. Raises Held when taken.
    """
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    handle = open(store / "archives.lock", "a+b")
    try:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        handle.close()
        raise Held(f"{store} is claimed by another run") from e
    return Claim(handle)
