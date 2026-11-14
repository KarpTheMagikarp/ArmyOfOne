"""
Noticing when the file the user just downloaded finishes landing.

The mod page opens in the user's real browser, where login, Cloudflare and the
countdown behave like always, and we watch Downloads for the file. No
credentials in our window and nothing of pywebview's to hook. Since that means
looking at a folder, it only looks while a run is waiting, only at files
matching what that patch expects and created after the wait began, and only
at names and sizes until one matches.
"""

import fnmatch
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# a browser writes to a temp name and renames on completion, so these are
# never a finished download
PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload")

# a browser's name for one it hasn't named yet: Chrome's "Unconfirmed
# 123456.crdownload" with the word in whatever language Chrome is in, and
# Firefox's random "AbC1xYz9.part" while its save dialog is up. Never a .tmp,
# which every program writes
UNNAMED = re.compile(r"(.+ \d{3,}\.crdownload|[^.]+\.(part|crdownload|opdownload|partial))",
                     re.I)

ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar.gz", ".tgz")

# a browser's own mark on a second copy of a name: "Mod.zip" then "Mod (1).zip"
COPY = re.compile(r"\s*\(\d+\)$")

# .exe is deliberately not in the list above. Downloads is where a browser puts
# everything, so a watch that accepts any .exe accepts the GPU driver and the
# Discord updater that happened to finish while the user was reading a mod
# page. Only a patch that says runs_installer has any business matching one.
INSTALLER_SUFFIXES = (".exe", ".msi")


def original(name: str) -> str:
    """the name a browser's second copy is a copy of, any case: "Mod (1).zip" is "mod.zip" """
    lowered = name.lower()
    for suffix in ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES:
        if lowered.endswith(suffix):
            return COPY.sub("", lowered[:-len(suffix)]) + suffix
    return COPY.sub("", lowered)


def downloads_folder() -> Path:
    """
    the user's downloads folder.

    asks Windows for the real one rather than assuming ~/Downloads, because it
    is routinely moved to another drive and a hardcoded guess would simply
    never see anything.
    """
    import os
    import sys

    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            # FOLDERID_Downloads
            guid = "{374DE290-123F-4565-9164-39C4925E467B}"
            buf = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(_guid_to_struct(guid)), 0, None, ctypes.byref(buf)
            ) == 0:
                return Path(buf.value)
        except Exception:
            pass
    env = os.environ.get("XDG_DOWNLOAD_DIR")
    if env:
        return Path(env)
    return Path.home() / "Downloads"


def _guid_to_struct(guid: str):
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]

    hexed = guid.strip("{}").replace("-", "")
    raw = bytes.fromhex(hexed)
    out = GUID()
    out.Data1 = int.from_bytes(raw[0:4], "big")
    out.Data2 = int.from_bytes(raw[4:6], "big")
    out.Data3 = int.from_bytes(raw[6:8], "big")
    for i in range(8):
        out.Data4[i] = raw[8 + i]
    return out


def nexus_named(name: str, mod_id: str) -> bool:
    """
    whether a file is named the way Nexus names a download of this mod:
    the file's own name, the mod's id, its version and when it went up, all
    joined by dashes, Aerith_Garb_Restauration-23-1-1-1759690588.zip. The
    name and the version can have dashes of their own, so it's the id with a
    version after it and the upload time last
    """
    lowered = name.lower()
    for suffix in PARTIAL_SUFFIXES + ARCHIVE_SUFFIXES + (".exe", ".msi"):
        if lowered.endswith(suffix):
            name, lowered = name[:-len(suffix)], lowered[:-len(suffix)]
    words = COPY.sub("", name).split("-")
    if len(words) < 4 or not (words[-1].isdigit() and 9 <= len(words[-1]) <= 11):
        return False
    return any(words[n] == str(mod_id) for n in range(1, len(words) - 2))


@dataclass
class Writing:
    """a download a browser is still writing: its bytes so far and the name it'll have"""
    size: int
    name: str = ""      # "" while the browser hasn't said, Chrome's "Unconfirmed 123.crdownload"


def _stamp(path: Path) -> tuple | None:
    """(size, mtime), or None for one that's gone"""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


@dataclass
class Sighting:
    path: Path
    size: int
    seen_at: float

    def as_dict(self) -> dict:
        return {"path": str(self.path), "name": self.path.name, "size": self.size}


@dataclass
class DownloadWatcher:
    """
    waits for one specific download to finish, then hands it over.

    deliberately not a background service. it is created when a patch starts
    waiting, and it stops the moment that patch is satisfied or abandoned.
    """

    folder: Path
    patterns: Iterable[str] = field(default_factory=lambda: ARCHIVE_SUFFIXES)
    # a floor, never a ceiling, and a loose one. The two failures are not
    # symmetric: adopting the wrong file fails loudly and Choose file is still
    # right there, while rejecting the right one means the watch sits quiet
    # forever and the user never learns why. So this only has to be tight
    # enough to tell a 6GB texture pack from a browser installer.
    min_bytes: int | None = None
    clock: Callable[[], float] = time.monotonic
    settle_seconds: float = 1.0
    # what a download's name has to look like to be this job's: globs, and a
    # Nexus mod's id, which every download from its page carries. Nothing
    # given is any archive, as it always was
    names: Iterable[str] = ()
    nexus: str | None = None
    # downloads already in, which a fresh copy of is never the one waited on
    skip: Iterable[str] = ()
    _baseline: dict = field(default_factory=dict)
    _started: float = 0.0
    _sizes: dict = field(default_factory=dict)
    _unfinished: dict = field(default_factory=dict)
    _following: str | None = None
    # handed over already, by the name a copy is of: pressing Download again
    # over a browser's warning saves "Mod (1).zip", which is the same file and
    # not the next one a row of two is waiting on
    _taken: set = field(default_factory=set)

    def start(self) -> None:
        """
        snapshots what is already there, by name and size.

        this is what stops an old copy of the same mod, sitting in Downloads
        from last week, being picked up and reported as a fresh install. Size
        too, since Firefox makes the finished name as an empty file the
        moment a download starts, so one begun just before this would
        otherwise be old news by the time it's done.
        """
        self._started = self.clock()
        self._baseline = {p.name: _stamp(p) for p in self._candidates()}
        self._unfinished = {p.name: _stamp(p) for p in self._partials()}
        self._sizes = {}
        self._following = None
        self._taken = {original(name) for name in self.skip}

    def wanted(self, name: str) -> bool:
        """whether a file by this name could be the download this watch is for"""
        globs = [g.lower() for g in self.names or ()]
        if not globs and not self.nexus:
            return True
        return any(fnmatch.fnmatchcase(original(name), g) for g in globs) or \
            bool(self.nexus and nexus_named(name, self.nexus))

    def _partials(self) -> list:
        if not self.folder.is_dir():
            return []
        try:
            return [entry for entry in self.folder.iterdir()
                    if entry.name.lower().endswith(PARTIAL_SUFFIXES) and entry.is_file()]
        except OSError:
            return []

    def partial(self) -> int | None:
        """the bytes so far of the download a browser is writing for this, or None"""
        found = self.writing()
        return found.size if found is not None else None

    def writing(self) -> Writing | None:
        """
        the download a browser is writing for this, or None: one named like
        what's watched for, or one Chrome hasn't named yet. New since
        start(), or grown since, which is a paused one picked up again. The
        one it settles on is followed while it's there, so two going at once
        don't take turns at the count. Names and sizes only, like the rest
        of this
        """
        fresh, resumed = {}, {}
        for entry in self._partials():
            name = entry.name
            lowered = name.lower()
            stem = lowered[:lowered.rindex(".")]
            unnamed = bool(UNNAMED.fullmatch(name)) and not stem.endswith(tuple(self.patterns))
            if not (unnamed or (stem.endswith(tuple(self.patterns))
                                and self.wanted(name[:len(stem)])
                                and original(stem) not in self._taken)):
                continue
            stamp = _stamp(entry)
            if stamp is None:
                continue
            if name not in self._unfinished:
                fresh[name] = stamp
            elif stamp[0] != (self._unfinished[name] or (None,))[0]:
                resumed[name] = stamp
        found = {**resumed, **fresh}
        if self._following not in found:
            # what this run started over one that was going before it
            pool = fresh or resumed
            self._following = max(pool, key=lambda n: pool[n][1]) if pool else None
        if not self._following:
            return None
        stem = self._following[:self._following.rindex(".")]
        named = stem.lower().endswith(tuple(self.patterns))
        return Writing(found[self._following][0], stem if named else "")

    def _candidates(self) -> list:
        if not self.folder.is_dir():
            return []
        out = []
        try:
            entries = list(self.folder.iterdir())
        except OSError:
            return []
        for entry in entries:
            if not entry.is_file():
                continue
            lowered = entry.name.lower()
            if any(lowered.endswith(s) for s in PARTIAL_SUFFIXES):
                continue
            if not any(lowered.endswith(s) for s in self.patterns):
                continue
            if original(entry.name) in self._taken or not self.wanted(entry.name):
                continue
            out.append(entry)
        return out

    def poll(self) -> Sighting | None:
        """
        returns a finished download that appeared since start(), or None.

        a file is only reported once its size has stopped changing between two
        polls. a browser creates the final name and then fills it, so acting
        on first sight would hand over a truncated archive and produce a
        confusing "that zip is corrupt" a few seconds later.
        """
        now = self.clock()
        for entry in self._candidates():
            stamp = _stamp(entry)
            if stamp is None or self._baseline.get(entry.name) == stamp:
                continue
            size = stamp[0]
            if size == 0:
                continue
            if self.min_bytes is not None and size < self.min_bytes:
                # too small to be what we are waiting for. not recorded in
                # _sizes either, so a download that starts below the floor and
                # grows past it is still caught on a later poll.
                continue

            previous = self._sizes.get(entry.name)
            if previous is None:
                # first sight: record the size and when we saw it, nothing more
                self._sizes[entry.name] = (size, now)
                continue

            last_size, first_seen_at_this_size = previous
            if size != last_size:
                # still growing, so restart the clock at the new size
                self._sizes[entry.name] = (size, now)
                continue

            # unchanged, and crucially the timestamp is NOT refreshed here: it
            # has to keep pointing at when this size was FIRST seen, or the
            # settle window resets on every poll and never elapses
            if now - first_seen_at_this_size < self.settle_seconds:
                continue
            self._taken.add(original(entry.name))
            return Sighting(path=entry, size=size, seen_at=now)
        return None

    def wait(self, timeout: float = 600, interval: float = 0.5,
             cancel=None, sleep=time.sleep) -> Sighting | None:
        """
        blocks until something lands or the wait is given up on.

        the caller runs this on a worker thread, same as every other long
        operation here. cancel is checked every interval so abandoning it is
        immediate rather than waiting out the timeout.
        """
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            if cancel is not None and cancel.is_set():
                return None
            found = self.poll()
            if found is not None:
                return found
            sleep(interval)
        return None
