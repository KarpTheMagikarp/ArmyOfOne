"""
Moving gigabytes without holding them in memory, blocking the UI, or leaving a
half-written game folder behind.

Everything streams: response -> chunk -> disk and archive member -> chunk ->
disk, so peak memory is one chunk whether the file's 200KB or 60GB. On top of
that:

  resume      partial output lands in .part and a retry asks for the rest by Range
  space       checked before writing anything, not found out at 80%
  progress    throttled, since 1MB chunks of a 6GB file is 6000 callbacks
  cancel      checked between chunks, not between files
"""

import hashlib
import os
import shutil
import tarfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from patcher import zipnames
from patcher.zipnames import safe_target

CHUNK = 1024 * 1024

# room to spare on top of what an install strictly needs. windows gets
# unhappy near a full volume, and the game itself needs somewhere to write
# saves afterwards.
SPACE_HEADROOM = 512 * 1024 * 1024

USER_AGENT = "ArmyOfOne"


class Cancelled(Exception):
    """the user stopped it. not an error, and not something to log as one."""


class IncompleteDownload(Exception):
    """
    the connection ended before the server's declared length was reached.

    Kept separate from a generic failure because the .part file is still good
    and a retry resumes from it, which is not true of most download errors.
    """

    def __init__(self, got: int, expected: int, name: str):
        self.got, self.expected = got, expected
        super().__init__(
            f"{name} stopped at {gb(got)} of {gb(expected)}. "
            "The connection dropped partway. Running this again picks up where it left off"
        )


class NotEnoughSpace(Exception):
    def __init__(self, needed: int, available: int, where: Path):
        self.needed, self.available, self.where = needed, available, where
        super().__init__(
            f"needs {gb(needed)} free on {where}, only {gb(available)} available"
        )


def gb(byte_count: int) -> str:
    return f"{byte_count / (1024 ** 3):.1f} GB"


@dataclass
class Progress:
    stage: str          # "download" or "extract"
    done: int
    total: int          # 0 when the server would not say
    label: str = ""

    @property
    def fraction(self) -> float:
        # unknown total reports 0 rather than guessing, so the UI can show an
        # indeterminate state instead of a bar that lies
        return (self.done / self.total) if self.total else 0.0


def free_space(path: Path) -> int:
    """free bytes on the volume holding path, walking up to one that exists"""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def ensure_space(path: Path, needed: int) -> None:
    available = free_space(path)
    if available < needed + SPACE_HEADROOM:
        raise NotEnoughSpace(needed + SPACE_HEADROOM, available, path)


class Throttle:
    """
    collapses a flood of progress calls down to something a UI can render.
    emits on a time interval, and always emits the final call so a bar cannot
    finish stuck at 97%.
    """

    def __init__(self, sink: Callable[[Progress], None] | None, interval: float = 0.2,
                 clock: Callable[[], float] = time.monotonic):
        self._sink = sink
        self._interval = interval
        self._clock = clock
        self._last = 0.0

    def __call__(self, progress: Progress, final: bool = False) -> None:
        if self._sink is None:
            return
        now = self._clock()
        if final or now - self._last >= self._interval:
            self._last = now
            self._sink(progress)


def _check(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


class _BytesResponse:
    """
    wraps a bytes-returning fetch so it goes down the streaming path, so
    tests (which inject `fetch(url) -> bytes`) exercise the same code as
    production.
    """

    def __init__(self, body: bytes):
        self._body = body
        self._offset = 0
        self.status = 200
        self.headers = {"Content-Length": str(len(body))}

    def read(self, size=-1):
        if size is None or size < 0:
            block, self._offset = self._body[self._offset:], len(self._body)
            return block
        block = self._body[self._offset:self._offset + size]
        self._offset += len(block)
        return block

    def close(self):
        pass


class _FileResponse:
    """
    a local file dressed as an HTTP response.

    lets a dropped archive go down the exact same install path as a download:
    streamed, zip-slip guarded, collision checked, flattened identically. a
    second extractor for local files would be the one nobody exercises.
    """

    def __init__(self, path: Path):
        self._handle = Path(path).open("rb")
        self.status = 200
        self.headers = {"Content-Length": str(Path(path).stat().st_size)}

    def read(self, size=-1):
        return self._handle.read(size if size and size > 0 else -1)

    def close(self):
        self._handle.close()


def opener_from_file(path: Path):
    """an opener that serves one local file, ignoring Range"""
    def opener(url: str, headers: dict):
        return _FileResponse(path)
    return opener


def opener_from_fetch(fetch: Callable[[str], bytes]):
    """adapts an injected fetch into an opener stream_download can drive"""
    def opener(url: str, headers: dict):
        # a bytes fetch has no concept of Range, so never claim a partial
        # response: stream_download sees 200 and correctly restarts.
        return _BytesResponse(fetch(url))
    return opener


class _NoDowngrade(HTTPRedirectHandler):
    """
    follows redirects, but never from https down to http. CPython's default
    doesn't care, and what comes down is a dll loaded into the game with no
    pinned checksum, so the connection is all the trust there is.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_full_url().lower().startswith("https://") and \
                not newurl.lower().startswith("https://"):
            raise URLError(f"refused a redirect from https to {newurl.split(':', 1)[0]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SAFE_OPENER = build_opener(_NoDowngrade())


def default_opener(url: str, headers: dict):
    return _SAFE_OPENER.open(Request(url, headers=headers), timeout=30)


def opener_for(fetch: Callable[[str], bytes]):
    """
    the streaming opener for real downloads, the adapter for injected ones.
    Keyed on identity against default_fetch, not a flag, since a caller that
    injected something wants it used. (Every call site used to pass the
    default straight through, so resume, cancel and progress never ran.)
    """
    from patcher.updater import default_fetch
    return default_opener if fetch is default_fetch else opener_from_fetch(fetch)


def stream_download(url: str, dest: Path, *, opener=default_opener,
                    on_progress: Callable[[Progress], None] | None = None,
                    cancel=None, label: str = "", expected_size: int | None = None) -> Path:
    """
    downloads url to dest without ever holding it in memory, resuming a
    partial .part file if one is there.

    the .part rename at the end is what makes a killed download safe: a file
    only appears at dest once it is whole, so a later run can tell "finished"
    from "interrupted" by name alone rather than by trusting a size.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    already = part.stat().st_size if part.exists() else 0

    headers = {"User-Agent": USER_AGENT}
    if already:
        # ask only for the rest. a server that ignores this answers 200 with
        # the whole file, which is handled below rather than assumed away.
        headers["Range"] = f"bytes={already}-"

    if expected_size:
        ensure_space(dest.parent, max(expected_size - already, 0))

    response = opener(url, headers)
    try:
        status = getattr(response, "status", None) or response.getcode()
        length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
        remaining = int(length) if length and str(length).isdigit() else 0

        if already and status != 206:
            # range refused, so everything downloaded so far is worthless
            already = 0
            part.unlink(missing_ok=True)

        total = remaining + already
        # the server just told us how big this is, which is better than
        # anything a caller could have guessed. no call site ever passed
        # expected_size, so before this the check above never ran and a
        # download simply filled the drive and died on errno 28.
        if remaining:
            ensure_space(dest.parent, remaining)

        mode = "ab" if already else "wb"
        emit = Throttle(on_progress)
        done = already

        with part.open(mode) as handle:
            while True:
                _check(cancel)
                block = response.read(CHUNK)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                emit(Progress("download", done, total, label))
        emit(Progress("download", done, total or done, label), final=True)

        # a body that stopped short of its declared length is not a file, and
        # promoting it to the final name records it as installed forever.
        # the realistic shape is a lengthless response over a dropped
        # connection, where urllib raises nothing at all and read() simply
        # returns what arrived.
        if total and done < total:
            raise IncompleteDownload(done, total, dest.name)
    finally:
        close = getattr(response, "close", None)
        if close:
            close()

    # replace rather than rename: a leftover dest from an earlier attempt
    # would otherwise make this fail on windows
    os.replace(part, dest)
    return dest


def sha256_of_file(path: Path, cancel=None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            _check(cancel)
            digest.update(block)
    return digest.hexdigest()


def _zip_plan(archive: Path, source_subdir: str | None):
    with zipfile.ZipFile(archive) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
    if source_subdir:
        wanted = source_subdir.strip("/").lower()
        members = [m for m in members
                   if wanted in [p.lower() for p in
                                 PurePosixPath(zipnames.repair(m).replace("\\", "/")).parts[:-1]]]
    # file_size comes from the central directory, so the extracted size is
    # known before a single byte is decompressed. that is what makes the
    # space check possible up front instead of halfway through.
    return members, sum(m.file_size for m in members)


def extracted_size(archive: Path, source_subdir: str | None = None) -> int:
    """how much disk the contents will need, read from the archive index"""
    name = archive.name.lower()
    if name.endswith(".zip"):
        return _zip_plan(archive, source_subdir)[1]
    with tarfile.open(archive, mode="r:*") as tf:
        return sum(m.size for m in tf.getmembers() if m.isfile())


def _pour(source, target: Path, cancel, emit, progress) -> int:
    """
    writes one archive member without ever leaving a partial file at its real
    name: temp sibling, then rename, and a cancel removes the temp. A
    truncated proxy dll beside the exe gets loaded on the next launch.
    progress(done_so_far) is called as it goes.
    """
    from patcher.fileops import temp_sibling

    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = temp_sibling(target)
    moved = 0
    try:
        with source, scratch.open("wb") as sink:
            while True:
                _check(cancel)
                block = source.read(CHUNK)
                if not block:
                    break
                sink.write(block)
                moved += len(block)
                progress(moved)
        scratch.replace(target)
        return moved
    except BaseException:
        try:
            scratch.unlink()
        except OSError:
            pass
        raise


def stream_extract(archive: Path, dest_dir: Path, *,
                   on_progress: Callable[[Progress], None] | None = None,
                   cancel=None, flatten: bool = False,
                   source_subdir: str | None = None,
                   label: str = "") -> list[str]:
    """
    unpacks archive into dest_dir a chunk at a time, checking free space first
    and reporting progress in bytes (a pack is often a few huge members, so
    "3 of 9 files" sits still for minutes).

    Nothing survives a failure: each member lands atomically (_pour) and an
    abandoned extract removes what did land, since half a mod with no state
    record is something nobody can see or undo.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    emit = Throttle(on_progress)
    written: list[str] = []
    name = archive.name.lower()
    try:
        return _extract_into(archive, dest_dir, name, written, emit, cancel,
                             flatten, source_subdir, label)
    except BaseException:
        for relative in written:
            try:
                (dest_dir / relative).unlink()
            except OSError:
                pass
        raise


def _extract_into(archive: Path, dest_dir: Path, name: str, written: list,
                  emit, cancel, flatten, source_subdir, label) -> list[str]:

    if name.endswith(".zip"):
        members, total = _zip_plan(archive, source_subdir)
        ensure_space(dest_dir, total)
        done = 0
        with zipfile.ZipFile(archive) as zf:
            for member in members:
                _check(cancel)
                target = safe_target(dest_dir, zipnames.repair(member), flatten)
                if target is None:
                    continue
                before = done
                done += _pour(
                    zf.open(member), target, cancel, emit,
                    lambda moved: emit(Progress("extract", before + moved, total,
                                                label or target.name)))
                written.append(target.name if flatten else target.relative_to(dest_dir).as_posix())
        emit(Progress("extract", done, total or done, label), final=True)
        return written

    with tarfile.open(archive, mode="r:*") as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        if source_subdir:
            wanted = source_subdir.strip("/").lower()
            members = [m for m in members
                       if wanted in [p.lower() for p in PurePosixPath(m.name).parts[:-1]]]
        total = sum(m.size for m in members)
        ensure_space(dest_dir, total)
        done = 0
        for member in members:
            _check(cancel)
            target = safe_target(dest_dir, member.name, flatten)
            if target is None:
                continue
            source = tf.extractfile(member)
            if source is None:
                continue
            before = done
            done += _pour(
                source, target, cancel, emit,
                lambda moved: emit(Progress("extract", before + moved, total,
                                            label or target.name)))
            written.append(target.name if flatten else target.relative_to(dest_dir).as_posix())
    emit(Progress("extract", done, total or done, label), final=True)
    return written
