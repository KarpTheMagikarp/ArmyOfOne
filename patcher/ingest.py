"""
Takes a file the user downloaded themselves and works out what to do with it,
for mods with no Mod Manager Download button or hosts that only serve
browsers. From there it behaves like an automated download.

Mod archives are laid out however the author felt like, so the scan finds the
game's own asset types by extension and matches their paths against the
filelist index.
"""

import os
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from patcher import zipnames
from patcher.fileops import scratch_near

# extensions the FFXIII engine actually loads. an archive member that is not
# one of these is documentation, a screenshot or an installer script, and none
# of those belong in a container.
GAME_ASSETS = {
    ".imgb",    # image/texture blobs
    ".trb",     # model and resource bundles
    ".mpk",     # packed model data
    ".scd",     # audio
    ".xwb",     # audio banks
    ".wdb",     # database records
    ".win32",   # generic engine payloads
    ".ztr",     # text
    ".clb",     # binary tables
}

# things that are definitely not assets, listed so a diagnostic can say WHY a
# member was ignored rather than silently dropping it
IGNORED = {".txt", ".md", ".pdf", ".jpg", ".jpeg", ".png", ".bat", ".exe",
           ".dll", ".url", ".nfo", ".ini", ".doc", ".rtf"}

UNSUPPORTED = {".rar", ".7z"}


class IngestError(Exception):
    pass


@dataclass
class Candidate:
    """one archive member that looks like a game asset"""
    member: str                 # path inside the archive
    size: int
    game_path: str | None = None   # matched path in the filelist index
    reason: str = ""

    @property
    def name(self) -> str:
        return PurePosixPath(self.member.replace("\\", "/")).name

    @property
    def matched(self) -> bool:
        return self.game_path is not None


@dataclass
class Ingest:
    source: Path
    candidates: list[Candidate] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    # set when scan() had to unpack the archive itself to read it. the caller
    # owns deleting this, and every one of them does it in a finally. before
    # this existed a dropped .rar left a full copy of itself in %TEMP% on
    # every scan, three times per install, forever.
    staging: Path | None = None

    def cleanup(self) -> None:
        if self.staging is None:
            return
        import shutil
        shutil.rmtree(self.staging, ignore_errors=True)
        self.staging = None

    @property
    def matched(self) -> list[Candidate]:
        return [c for c in self.candidates if c.matched]

    def as_dict(self) -> dict:
        return {
            "source": str(self.source),
            "assets": len(self.candidates),
            "matched": len(self.matched),
            "files": [
                {"member": c.member, "size": c.size,
                 "gamePath": c.game_path, "reason": c.reason}
                for c in self.candidates
            ],
        }


def system_tar() -> str | None:
    """
    Windows' own bsdtar first, then whatever PATH has.

    PATH alone found the wrong one on a modder's machine: Git for Windows'
    Unix tools, MSYS and Cygwin all put GNU tar ahead of System32, and GNU tar
    can't read a .7z or a .rar and takes a C:\\ path for a host called C. The
    one in System32 is the libarchive build this was written against
    """
    import shutil as _sh

    if sys.platform == "win32":
        own = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
        if own.is_file():
            return str(own)
    return _sh.which("bsdtar") or _sh.which("tar")


def try_system_extract(archive: Path, destination: Path, timeout: float = 300,
                       progress=None) -> Path | None:
    """
    best effort extraction of a format Python can't open, using bsdtar.
    Windows has shipped tar.exe since 10 1803 and recent libarchive reads RAR,
    which covers Leviathan's Tears' .rar with nothing bundled.

    `progress(done, of)` gets bytes out against what a .7z lists, 0 for
    anything else (a solid .rar only lists by unpacking). Returns the folder,
    or None on any failure, never raises: asking the user is still there.
    """
    import subprocess
    import time

    tar = system_tar()
    if tar is None:
        return None
    destination.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    total = _listed_size(tar, archive, flags) \
        if progress is not None and archive.suffix.lower() == ".7z" else 0
    try:
        unpack = subprocess.Popen([tar, "-xf", str(archive), "-C", str(destination)],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    deadline, wait, said, code = time.monotonic() + timeout, 0.5, 0, None
    try:
        while code is None and time.monotonic() < deadline:
            try:
                code = unpack.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                if progress is None:
                    continue
                began = time.monotonic()
                out = _size_of(destination)
                # walking a big tree isn't free, so it gets at most a fifth of the time
                wait = max(0.5, 4 * (time.monotonic() - began))
                if out > said:
                    said = out
                    try:
                        progress(min(out, total) if total else out, total)
                    except Exception:
                        # a line that can't be said isn't worth the unpack
                        progress = None
    finally:
        # out of time, or anything here that raised: nothing's left writing behind us
        if unpack.poll() is None:
            unpack.kill()
            unpack.wait()
    if code != 0:
        return None
    if not any(destination.iterdir()):
        return None

    # bsdtar picked the paths, not us, so the escape check runs after, on what
    # actually landed. "libarchive strips .. by default" is a promise about
    # someone else's binary. Any escape refuses the whole extraction.
    escaped = _escapes(destination)
    if escaped:
        return None
    return destination


def _listed_size(tar: str, archive: Path, flags: int) -> int:
    """
    what an archive holds unpacked, off `tar -tv`, or 0 when it won't say. A
    .7z keeps every size in its one header, so this is quick for one. The size
    is bsdtar's fifth column and GNU tar's third, the one after owner/group
    """
    import subprocess

    try:
        # it's a header read, milliseconds for a 7z of any size, so a listing
        # that takes longer than this isn't worth holding the unpack back for
        listed = subprocess.run([tar, "-tvf", str(archive)], capture_output=True, timeout=10,
                                stdin=subprocess.DEVNULL, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return 0
    if listed.returncode != 0:
        return 0
    total = 0
    for line in listed.stdout.decode("utf-8", "replace").splitlines():
        cols = line.split()
        if len(cols) < 2:
            continue
        at = 2 if "/" in cols[1] else 4
        # a device, or an owner with a space in it: a listing this can't read
        if len(cols) <= at or not cols[at].isdigit():
            return 0
        total += int(cols[at])
    return total


def _size_of(folder: Path) -> int:
    """
    bytes under a folder while it's being written, links not followed, the
    way _escapes() won't. A stat a file: on Windows the size scandir lists
    can lag a file that's still open, and a big one is most of an unpack
    """
    import os

    total, todo = 0, [str(folder)]
    while todo:
        try:
            with os.scandir(todo.pop()) as found:
                for entry in found:
                    if entry.is_dir(follow_symlinks=False):
                        todo.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        total += os.lstat(entry.path).st_size
        except OSError:
            continue
    return total


def _escapes(folder: Path) -> str | None:
    """
    what in this extracted tree isn't really inside it, or None. os.walk with
    followlinks off, since rglob walks right through a symlinked dir. Links
    are refused, not resolved: no FFXIII mod ships one.
    """
    import os

    root = folder.resolve(strict=False)
    for parent, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            entry = Path(parent) / name
            # is_symlink misses a Windows junction, which is the same hole
            # wearing a different reparse tag. Path.is_junction arrived in
            # 3.12, which is what CI builds with; on anything older the
            # containment check below still catches one that points outside.
            if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                return f"{name} is a link"
            try:
                resolved = entry.resolve(strict=False)
            except OSError:
                return f"{name} could not be resolved"
            if resolved != root and root not in resolved.parents:
                return f"{name} landed outside the folder it was extracted into"
    return None


def _members(archive: Path) -> list[tuple[str, int]]:
    name = archive.name.lower()
    if any(name.endswith(ext) for ext in UNSUPPORTED):
        raise IngestError(
            f"Windows can't open {archive.suffix} on its own. Extract it "
            "yourself, then use Choose folder to point at the extracted files."
        )
    if name.endswith(".zip"):
        try:
            with zipfile.ZipFile(archive) as zf:
                # repaired names, so everything downstream matches and writes
                # the name the author actually used. extract_member translates
                # back to whatever zf.open still wants.
                return [(zipnames.repair(i), i.file_size)
                        for i in zf.infolist() if not i.is_dir()]
        except zipfile.BadZipFile:
            # the stdlib says "File is not a zip file" about a file plainly
            # named .zip, with no hint that a half finished download is the
            # usual cause and that redownloading is the fix
            raise IngestError(
                f"{archive.name} isn't a readable archive. It may have "
                "downloaded only partway: delete it and try again") from None
    try:
        with tarfile.open(archive, mode="r:*") as tf:
            return [(m.name, m.size) for m in tf.getmembers() if m.isfile()]
    except tarfile.TarError as e:
        raise IngestError(f"couldn't read {archive.name}: {e}") from e


def _folder_members(folder: Path) -> list[tuple[str, int]]:
    return [(str(p.relative_to(folder)).replace("\\", "/"), p.stat().st_size)
            for p in sorted(folder.rglob("*")) if p.is_file()]


def is_asset(member: str) -> bool:
    """
    whether a member looks like something the game loads.

    checks every suffix, not just the last one: the engine's filenames stack
    them (c001.win32.imgb, veffs.jp.win32.imgb), so a plain Path.suffix test
    sees ".imgb" on one file and ".win32" on another and treats them
    differently for no reason.
    """
    suffixes = {s.lower() for s in PurePosixPath(member.replace("\\", "/")).suffixes}
    if suffixes & IGNORED:
        return False
    return bool(suffixes & GAME_ASSETS)


def match_to_index(candidate: Candidate, index_paths: list[str]) -> None:
    """
    finds where a dropped file belongs in the game's archive.

    matches on the deepest shared path tail rather than on the filename alone,
    because the engine is full of identically named files. There are dozens of
    veffs.jp.win32.imgb, one per weather and effect folder, and picking the
    wrong one writes rain data over something else entirely.
    """
    member = candidate.member.replace("\\", "/").lower().lstrip("./")
    best, best_depth = None, 0

    for path in index_paths:
        lowered = path.lower()
        if not member.endswith(PurePosixPath(lowered).name):
            continue
        # walk back through path components counting how many agree
        wanted = lowered.split("/")
        have = member.split("/")
        depth = 0
        for a, b in zip(reversed(wanted), reversed(have)):
            if a != b:
                break
            depth += 1
        if depth > best_depth:
            best, best_depth = path, depth

    if best is None:
        candidate.reason = "no entry in the game's index has this name"
        return
    if best_depth < 2:
        # a bare filename match is not enough to act on
        candidate.reason = (
            f"only the filename matched ({PurePosixPath(best).name}); "
            "keep the mod's folder structure so the right one can be picked"
        )
        return
    candidate.game_path = best
    candidate.reason = f"matched {best_depth} path segments"


def scan(source: Path, index_paths: list[str] | None = None,
         allow_system_extract: bool = True) -> Ingest:
    """
    inspects a dropped archive or folder and reports what is usable.

    read-only. nothing is extracted or written here, so a user can drop the
    wrong file and get told so without anything having happened to their game.
    """
    source = Path(source)
    if not source.exists():
        raise IngestError(f"{source} does not exist")

    unpacked_to = None
    if source.is_dir():
        members = _folder_members(source)
    else:
        try:
            members = _members(source)
        except IngestError:
            # one attempt with the system extractor before giving up. a .rar
            # we can open is worth far more than a clear error about a .rar we
            # cannot.
            if not allow_system_extract:
                raise
            import shutil
            import tempfile
            # beside the archive rather than in %TEMP%, so a 4GB rar sitting on
            # a roomy D: does not try to unpack itself onto a full C:
            staging = Path(tempfile.mkdtemp(prefix="ffxiii-unpack-",
                                            dir=scratch_near(source.parent)))
            try:
                extracted = try_system_extract(source, staging)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            if extracted is None:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            source, unpacked_to = extracted, staging
            members = _folder_members(source)

    result = Ingest(source=source, staging=unpacked_to)

    for member, size in members:
        if is_asset(member):
            result.candidates.append(Candidate(member=member, size=size))
        else:
            result.ignored.append(member)

    if index_paths:
        for candidate in result.candidates:
            match_to_index(candidate, index_paths)

    return result


def extract_member(source: Path, member: str, destination: Path) -> Path:
    """pulls one member out to a real file, streamed"""
    import shutil

    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copyfile(source / member, destination)
        return destination

    if source.name.lower().endswith(".zip"):
        with zipfile.ZipFile(source) as zf:
            # _members handed the caller a repaired name; zf.open only knows
            # the one in the central directory
            table = zipnames.names(zf)
            inner = table.get(member)
            if inner is None:
                # either not in this archive, or the name is ambiguous, which
                # names() deliberately refuses to guess at. extracting the
                # wrong member here would write real bytes into white_img at
                # the right offset and report success.
                raise IngestError(
                    f"{member} could not be read from {source.name} unambiguously. "
                    "Extract the archive yourself and use Choose folder.")
            with zf.open(inner) as src, destination.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)
        return destination

    with tarfile.open(source, mode="r:*") as tf:
        handle = tf.extractfile(member)
        if handle is None:
            raise IngestError(f"{member} is not a regular file")
        with handle, destination.open("wb") as out:
            shutil.copyfileobj(handle, out, length=1024 * 1024)
    return destination
