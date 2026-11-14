"""
Any mod that's the game's own files, put straight into the archives they come
out of.

Most mods for these games are a folder of files laid out the way the archives
lay them out, chr/pc/c201/bin/c201.win32.imgb and the like: loose, in a zip or
a 7z, unpacked by ff13tool into a white_imgu folder, or under a data folder for
Nova Chrysalia's unpacked mode. Instead of unpacking and repacking the whole
game, each file is matched to the entry it replaces
by the longest tail of its path that an index lists, in every voice set that
lists it, put on the end of that archive, and the entry pointed at it.

Nothing is written over. The original stays where it was, under the mod, so
taking the mod off is the index put back the way it was and the archive cut
back to its old length when nothing else has gone on the end since. The record
goes down before an index is swapped, so a crash in between leaves something to
finish with rather than a mod nothing can take off.

Mods stack in the order they went in. Taking one off from under another hands
the one on top what was under it, so nothing ever points back at a mod that's
gone.
"""

import hashlib
import io
import json
import os
import re
import struct
import tarfile
import threading
import time
import unicodedata
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from patcher.access import is_threat_block, threat_message, write_failure

RECORDS = "files"
DATA_DIRS = {"white_data": "FINAL FANTASY XIII", "alba_data": "FINAL FANTASY XIII-2",
             "weiss_data": "LIGHTNING RETURNS"}

# what Python's zipfile inflates: stored, deflate, bzip2, LZMA. Deflate64,
# PPMd and AES are 7-Zip's
INFLATES = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}


def _sealed(info) -> bool:
    """a member zipfile lists and can't read: locked with a password, or one only 7-Zip inflates"""
    return bool(info.flag_bits & 1) or info.compress_type not in INFLATES


def _all_sealed(archive) -> bool:
    files = [info for info in archive.infolist() if not info.is_dir()]
    return bool(files) and all(_sealed(info) for info in files)


def spanned(handle) -> bool:
    """
    whether a zip is the last of several parts, the way Info-ZIP's -s and
    WinZip split one: its end record is on a disk past the first. zipfile
    lists one fine and fails on the first member that's in another part
    """
    at = handle.tell()
    try:
        size = handle.seek(0, io.SEEK_END)
        handle.seek(max(0, size - (1 << 16) - 22))
        tail = handle.read()
    finally:
        handle.seek(at)
    end = tail.rfind(b"PK\x05\x06")
    if end < 0 or len(tail) < end + 8:
        return False
    disks = struct.unpack_from("<HH", tail, end + 4)
    if 0xFFFF in disks:
        # zip64's, which says how many disks in the locator just before it
        locator = end - 20
        return locator >= 0 and tail[locator:locator + 4] == b"PK\x06\x07" and \
            struct.unpack_from("<I", tail, locator + 16)[0] > 1
    return any(disks)


# a zone's index rides inside white_img and only means anything with the blob
# it describes, which a file on its own can't bring along
ZONE_INDEX = re.compile(r"^zone/filelist_z\d{3,4}[ucav]?\.win32\.bin$")
# a zone's own art, which lives in a blob this doesn't open
ZONE_ART = re.compile(r"(^|/)bg/.+\.win32\.(trb|imgb)$")
WHOLE = re.compile(r"^(white_(img2?|scr)[ucav]?|filelist2?(_scr)?[ucav]?|white_z\d{3,4}[ucav]?_img)"
                   r"\.win32\.bin$")
# the folder ff13tool and WhiteBinTools unpack an archive into, which says
# which voice set its files came out of
UNPACKED = re.compile(r"^_?white_(img2?|scr)([ucav]?)(\.win32\.bin)?$")
# LR spells its voice sets a and v where the other two say u and c, and its
# Japanese release has no letter at all. Pairs carry XIII's letter, so a
# mod's voice set means the same thing in all three games
LR_SETS = (("a", "u"), ("v", "c"), ("", "c"))
SPOKEN = {"u": "u", "c": "c", "a": "u", "v": "c"}
# Nova Chrysalia's modpacks say it the same way, one folder per language
NOVA_VOICES = {"en-data": "u", "jp-data": "c"}
NOVA_GAMES = {"1": "white_data", "2": "alba_data", "3": "weiss_data"}
# a zip inside the drop, the way a patcher carries its payload: Red Rose's
# PatchData.zip next to the exe that unpacks it. One level, and only zips,
# which list themselves without unpacking
NESTED = (".zip", ".ncmp")
PAYLOADS = ("patchdata.bin",)

ARCHIVES = (".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".tar", ".zip", ".7z", ".rar", ".ncmp")
INSTALLERS = (".bat", ".cmd", ".exe", ".ps1", ".sh")
CODE = (".dll", ".asi")
# Nova Chrysalia's patches to the exe, which only it applies
CODE_PATCHES = (".nccp",)
# art that a tool still has to pack into the game's own .imgb
LOOSE_ART = (".dds", ".tga")
# the games' own programs, which Apply keeps for the 4GB patch and Steam checks
MAIN_EXES = {"ffxiiiimg.exe", "ffxiii2img.exe", "lrff13.exe"}
GAME_EXES = {"ffxiiiimg.exe", "ffxiii2img.exe", "lrff13.exe", "ffxiiilauncher.exe",
             "ffxiii2launcher.exe", "lrff13launcher.exe"}
# patches to a file rather than files, xdelta's and the like
DELTAS = (".xdelta", ".vcdiff", ".xd3", ".delta")
# the folders beside an exe that NovaFix's add-ons and ReShade keep
PROGRAM_FOLDERS = {"reshade", "addons", "extra", "extras", "mods", "dll"}
# what a save is called, ff13-00.dat and lrff13-50.dat
SAVE = re.compile(r"^(ff13|ff13-2|ff132|lrff13|lr)[-_]?\d+\.dat$")
# each game's Steam app, whose userdata folder holds its saves under Steam Cloud
STEAM_APPS = {"white_data": "292120", "alba_data": "292140", "weiss_data": "345350"}
# a zone's archive, loose in the zone folder with its index inside white_img
ZONE_BLOB = re.compile(r"^white_z\d{3,4}[ucav]?_img2?\.win32(\.bin)?$")
# the games whose HD Project builds its art into those. A whole one swapped in
# under it would become what its backup calls vanilla, so there they're its
HD_ZONES = {"white_data", "alba_data"}
# what runs beside the game rather than going into it: a Cheat Engine table, a
# TexMod package, a spreadsheet for working out edits by hand
TOOLS = {".ct": "a Cheat Engine table, which Cheat Engine opens while the game runs",
         ".tpf": "a TexMod package, which TexMod puts into the game as it runs",
         ".xlsx": "a spreadsheet for working out edits by hand",
         ".xls": "a spreadsheet for working out edits by hand",
         ".ods": "a spreadsheet for working out edits by hand"}
# and settings for what this app runs itself, by name
SETTINGS = {"novafix.ini": "NovaFix's settings, which the Settings drawer writes for you",
            "ff13fix.ini": "FF13Fix's settings, and this app runs NovaFix in its place"}
RESHADE_ADDONS = (".addon32", ".addon", ".addon64")
# the 4GB patch on its own, NTCore's and the ones made from it
LARGE_ADDRESS = re.compile(r"4\s*gb|large\s*address|(^|[^a-z])laa([^a-z]|$)")
# what a WPD is called: wdbpack.bin, and the .wdb tables inside it and beside it
WPDS = (".wdb", ".wpd", ".bin")
# LayeredFS, a dinput8.dll for LR that reads files out of mod_data beside the
# exe instead of the archives
LAYERED = ("layeredls.ini", "layeredfs.ini")
LAYERED_DATA = "mod_data"

# what a placeholder the build left empty holds, which has nothing to hash
STUB = "stub"
# and how one is stored: no bytes, deflated, which is the 0:8 every placeholder
# in the index points at
STUB_BLOB = zlib.compress(b"")


class FilesError(Exception):
    pass


@dataclass(frozen=True)
class Pair:
    index: Path
    archive: Path
    kind: str      # img or scr
    voice: str     # u or c

    def name(self, install_dir) -> str:
        return self.index.relative_to(install_dir).as_posix()


def data_dir(install_dir):
    """white_data, alba_data or weiss_data, whichever this game keeps"""
    return next((Path(install_dir) / name for name in DATA_DIRS
                 if (Path(install_dir) / name).is_dir()), None)


def pairs(install_dir) -> list:
    """every index and archive pair under the game's sys folder, both voice sets"""
    data = data_dir(install_dir)
    if data is None:
        return []
    sys_dir, found = data / "sys", []
    if data.name == "weiss_data":
        for letter, voice in LR_SETS:
            # white_img2 is LR's second archive per voice set, wdbpack.bin's.
            # The letter goes in the kind so two of them never share a name
            for kind, index, archive in (("scr", f"filelist_scr{letter}", f"white_scr{letter}"),
                                         ("img", f"filelist{letter}", f"white_img{letter}"),
                                         ("img2", f"filelist2{letter}", f"white_img2{letter}")):
                index, archive = sys_dir / f"{index}.win32.bin", sys_dir / f"{archive}.win32.bin"
                if index.is_file() and archive.is_file():
                    found.append(Pair(index, archive, kind + (letter or "j"), voice))
        return found
    for voice in "uc":
        for kind, index, archive in (("scr", f"filelist_scr{voice}", f"white_scr{voice}"),
                                     ("img", f"filelist{voice}", f"white_img{voice}")):
            index, archive = sys_dir / f"{index}.win32.bin", sys_dir / f"{archive}.win32.bin"
            if index.is_file() and archive.is_file():
                found.append(Pair(index, archive, kind, voice))
    return found


def _key(path: str) -> str:
    return path.replace("\\", "/").lower().strip("/")


def _fields(entry) -> list:
    return [entry.sector_offset, entry.uncompressed, entry.stored]


# the drawer, the HD survey and an install all ask after the same few indexes
_books, _books_lock = {}, threading.Lock()


def entries(index) -> dict:
    """
    path -> the first record listing it. Kept against the file's bytes rather
    than its mtime, which exFAT keeps to 10ms: two writes inside one tick at
    the same size would read as the first
    """
    from patcher.archive.filelist import parse

    raw = Path(index).read_bytes()
    mark = hashlib.blake2b(raw, digest_size=16).digest()
    with _books_lock:
        known = _books.get(mark)
    if known is None:
        known = {}
        for entry in parse(raw).entries():
            known.setdefault(_key(entry.path), entry)
        with _books_lock:
            _books[mark] = known
            while len(_books) > 8:
                _books.pop(next(iter(_books)))
    return known


_codes = {}


def coded(index) -> dict:
    """
    file code -> its record, for every code with one record, kept the way
    entries() keeps paths. What a rename goes by, since the path it starts
    from can be on two other records as well
    """
    from patcher.archive.filelist import by_code, parse

    raw = Path(index).read_bytes()
    mark = hashlib.blake2b(raw, digest_size=16).digest()
    with _books_lock:
        known = _codes.get(mark)
    if known is None:
        known = {code: records[0] for code, records in by_code(parse(raw)).items()
                 if len(records) == 1}
        with _books_lock:
            _codes[mark] = known
            while len(_codes) > 8:
                _codes.pop(next(iter(_codes)))
    return known


@dataclass
class Game:
    install_dir: Path
    pairs: list
    listed: dict      # path -> the voice sets listing it
    leaves: dict      # file name -> every path ending in it
    dirs: set = field(default_factory=set)      # every folder name the game's paths use
    stems: dict = field(default_factory=dict)   # c108.win32.trb's stem -> its .imgb and so on
    _disk: tuple | None = None
    _held: set | None = None

    def loader_held(self) -> set:
        """
        every file a loader mod holds beside the exe, lowercased from the
        game's folder: HD Fonts and GUI's hd_textures is on the disk once it's
        in, and dropped again it's that mod's data, not loose files
        """
        if self._held is None:
            from patcher.mods import loader_mods
            from patcher.paths import undo_store_for

            exe = next((k for k in self.disk()[0] if k.rpartition("/")[2] in MAIN_EXES), None)
            held = set()
            if exe is not None:
                beside = exe.rpartition("/")[0]
                try:
                    for record in loader_mods.records(undo_store_for(self.install_dir)):
                        for rel in loader_mods._held(record, "files", "was", "kept"):
                            path = f"{beside}/{rel}" if beside else rel
                            held.add(path.replace("\\", "/").lower())
                except OSError:
                    pass
            self._held = held
        return self._held

    @property
    def data(self) -> str:
        found = data_dir(self.install_dir)
        return found.name if found else ""

    def disk(self) -> tuple:
        """
        (every file in the game's folder, lowercased path -> as it's spelled,
        every folder the same way, the folders holding an exe), walked once
        and only when a drop has a file the archives don't list
        """
        if self._disk is None:
            files, folders, programs = {}, {}, set()
            for path in Path(self.install_dir).rglob("*"):
                rel = path.relative_to(self.install_dir).as_posix()
                key = rel.lower()
                if key.startswith(".patcher-undo"):
                    continue
                if path.is_dir():
                    folders[key] = rel
                elif path.is_file():
                    files[key] = rel
                    if key.endswith(".exe"):
                        programs.add(key.rpartition("/")[0])
            leaves = {}
            for key in files:
                leaves.setdefault(key.rpartition("/")[2], []).append(key)
            self._disk = (files, folders, programs, leaves)
        return self._disk


def _stem(path: str) -> str:
    """a model's files share all of their path but the .trb, .imgb and so on"""
    return re.sub(r"\.win32\.[^./]+$", "", path)


def read_game(install_dir) -> Game:
    install_dir = Path(install_dir)
    found = pairs(install_dir)
    game = Game(install_dir, found, {}, {})
    for pair in found:
        for path in entries(pair.index):
            game.listed.setdefault(path, set()).add(pair.voice)
    for path in game.listed:
        folders, _, leaf = path.rpartition("/")
        game.leaves.setdefault(leaf, set()).add(path)
        game.dirs.update(folders.split("/"))
        game.stems.setdefault(_stem(path), set()).add(path)
    return game


class Source:
    """
    a drop's files, read where they sit. A folder and a zip are read in place;
    a tar has no index to seek by, so it's unpacked beside itself first and
    read as the folder that makes. Anything else is one loose file.

    `context` is the folders a dropped folder or loose file sits in, its own
    name included, which can say which voice set or game it's for. `doubled`
    is every name a zip lists twice with different bytes, which can't be told
    apart and so goes nowhere. A zip inside it has its files listed as its
    own, under the zip's name (`nested`), and one that won't open, or a .7z
    or .rar that would need unpacking, is in `unopened`. One Windows Security
    won't hand over is in `blocked`, since that isn't a damaged download.
    Without `open_nested` a zip inside it is just a file of it
    """

    def __init__(self, path, open_nested: bool = True):
        self.path = Path(path)
        self.root, self._zip, self._names, self._staging = None, None, {}, None
        self.context, self.doubled, self.nested, self.unopened = [], [], [], []
        self.blocked = []
        self._inner = {}     # a nested zip's name in here -> (ZipFile, its names)
        self._folders = {}   # a nested .7z or .rar's name in here -> where it unpacked to
        self._stretches = []
        self._walked = None
        self._copies = 0
        # a compressed member copied out whole, by its name, for every open after the first
        self._whole = {}
        name = self.path.name.lower()
        if self.path.is_file():
            try:
                with self.path.open("rb") as handle:
                    handle.read(4)
            except OSError as e:
                # is_zipfile() swallows this and the drop read as half downloaded
                if is_threat_block(e):
                    raise FilesError(threat_message(self.path.name)) from e
                raise
        if self.path.is_dir():
            self.root = self.path
            self.context = _parts(str(self.path))
        elif zipfile.is_zipfile(self.path):
            self._open_zip()
        elif tarfile.is_tarfile(self.path):
            self.root = self._untar()
        elif name.endswith((".zip", ".ncmp", ".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".tar")):
            raise FilesError(f"{self.path.name} won't open. It may not have finished downloading, "
                             "or the download is damaged.")
        else:
            self.context = _parts(str(self.path.parent))
        if open_nested and (self._zip is not None or self.root is not None):
            try:
                self._open_nested()
            except BaseException:
                self.close()
                raise

    @staticmethod
    def _listed(archive) -> tuple:
        """(name -> its record, the names listed twice with different bytes)"""
        from patcher import zipnames

        copies, names, doubled = {}, {}, []
        for info in archive.infolist():
            if not info.is_dir():
                copies.setdefault(zipnames.repair(info), []).append(info)
        for name, infos in copies.items():
            # the same bytes twice is one file; different bytes under one name
            # is a packer's mistake with no way to tell which was meant
            if len({(i.CRC, i.file_size) for i in infos}) == 1:
                names[name] = infos[0]
            else:
                doubled.append(name)
        return names, doubled

    def _open_zip(self) -> None:
        self._zip = zipfile.ZipFile(self.path)
        if spanned(self._zip.fp):
            self._zip.close()
            self._zip = None
            raise FilesError(f"{self.path.name} is the last part of an archive split into "
                             "pieces. Unpack it with 7-Zip, with every part beside it, and drop "
                             "the folder that comes out.")
        if _all_sealed(self._zip):
            # it lists fine and never reads. One that only locks its readme or
            # packs a preview with Deflate64 goes in, and says so if a file it
            # needs is one of those when it's read
            self._zip.close()
            self._zip = None
            raise FilesError(f"{self.path.name} is locked with a password, or packed so only "
                             "7-Zip opens it. Unpack it with 7-Zip (with the password its mod "
                             "page gives, if it asks) and drop the folder that comes out.")
        self._names, self.doubled = self._listed(self._zip)

    def _open_nested(self) -> None:
        """
        each zip in it opened where it sits: in place out of a folder, and out
        of a zip in place too when it's stored there whole, the way a zip of
        a zip usually is, since its bytes are a stretch of the drop. One
        that's compressed is copied onto the disk beside the drop first,
        since a member of a zip can't be sought through without inflating it
        from the start every time
        """
        for n, (member, size) in enumerate(list(self._own())):
            leaf = member.rsplit("/", 1)[-1].lower()
            if leaf.endswith((".7z", ".rar")):
                # one locked in the drop's own zip says so, not as zipfile's error
                self._readable(member)
                folder = self._unpack_inner(member, size, n)
                if folder is None:
                    self.unopened.append(member)
                else:
                    self._folders[member] = folder
                    self.nested.append(member)
                continue
            # Surihix's LR patchers carry their payload as a zip called
            # PatchData.bin, and a .bin that isn't one is nobody's business
            named = leaf in PAYLOADS
            if not leaf.endswith(NESTED) and not named:
                continue
            self._readable(member)
            where, inner, copied = None, None, self._zip is None
            try:
                if copied:
                    where = self.root / member
                else:
                    spot = self._stored(self._names[member])
                    if spot is not None:
                        where = _Stretch(self.path, *spot)
                        self._stretches.append(where)
                    else:
                        where = self._copy_out(member, n)
                    copied = True
                if isinstance(where, Path):
                    # is_zipfile() takes Windows refusing the read for a zip
                    # that isn't one, and that was said as a damaged download
                    with where.open("rb") as handle:
                        handle.read(4)
                inner = zipfile.ZipFile(where)
            except zipfile.BadZipFile:
                pass
            except OSError as e:
                if is_threat_block(e):
                    self.blocked.append(member)
                    continue
                if not copied:
                    raise
            if inner is not None and (_all_sealed(inner) or spanned(inner.fp)):
                # locked with a password, packed so only 7-Zip reads it, or one
                # part of several: listed fine and never read, so one to unpack,
                # not the mod's files
                inner.close()
                inner = None
            if inner is None:
                if not named:
                    self.unopened.append(member)
                continue
            names, doubled = self._listed(inner)
            self._inner[member] = (inner, names)
            self.nested.append(member)
            self.doubled += [f"{member}/{name}" for name in doubled]

    def _unpack_inner(self, member: str, size: int, n: int):
        """
        a .7z or .rar inside the drop, unpacked beside it the way a dropped
        one is, or None when Windows' tar can't, which then asks for it to be
        unpacked by hand as before. What's in it is listed as the drop's own,
        under its name, the way a zip in it is
        """
        import tempfile

        from patcher.download import free_space
        from patcher.fileops import scratch_near
        from patcher.ingest import try_system_extract

        if self._staging is None:
            self._staging = Path(tempfile.mkdtemp(prefix="ffxiii-mod-",
                                                  dir=scratch_near(self.path.parent)))
        # what it holds isn't known short of unpacking it, and a mod's packs
        # rarely come out at more than four times their size
        if size * 5 + (64 << 20) > free_space(self._staging):
            return None
        archive = self.root / member if self._zip is None else None
        if archive is None:
            archive = self._copy_out(member, n)
            archive = archive.rename(archive.with_suffix(Path(member).suffix.lower()))
        return try_system_extract(archive, self._staging / f"{n}-unpacked")

    def _copy_out(self, member: str, n, opener=None, size: int | None = None) -> Path:
        """
        a compressed zip in the drop, onto the disk beside it, once there's
        room, or whatever `opener` streams. A drive that fills on the way is
        the drop's, said as that rather than as whatever the caller would blame
        """
        import tempfile

        from patcher.download import free_space
        from patcher.fileops import scratch_near

        leaf = member.rsplit("/", 1)[-1]
        if opener is None:
            info = self._names[member]
            opener, size = (lambda: self._zip.open(info)), info.file_size
        if self._staging is None:
            self._staging = Path(tempfile.mkdtemp(prefix="ffxiii-mod-",
                                                  dir=scratch_near(self.path.parent)))
        room = free_space(self._staging)
        if size + (16 << 20) > room:
            raise FilesError(f"{leaf} inside {self.path.name} is "
                             f"{size / 1024 ** 3:.1f} GB unpacked, and the drive holding "
                             f"{self._staging.parent} has {room / 1024 ** 3:.1f} GB free to open "
                             "it in. Free some up, or unpack it yourself and drop what's inside.")
        where = self._staging / (f"{n}.zip" if isinstance(n, int) else str(n))
        try:
            with opener() as inside, where.open("wb") as out:
                while True:
                    chunk = inside.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
        except OSError as e:
            if is_threat_block(e):
                raise
            raise FilesError(f"{leaf} couldn't be copied out of {self.path.name} to be read: "
                             f"{write_failure(e, self._staging)}") from e
        return where

    def _stored(self, info):
        """(where in the drop, how long) for a member stored whole, or None"""
        if info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 0x1:
            return None
        with self.path.open("rb") as handle:
            handle.seek(info.header_offset)
            head = handle.read(30)
        if len(head) < 30 or head[:4] != b"PK\x03\x04":
            return None
        name, extra = struct.unpack_from("<HH", head, 26)
        return info.header_offset + 30 + name + extra, info.file_size

    def _own(self) -> list:
        if self._zip is not None:
            return [(name, info.file_size) for name, info in self._names.items()]
        if self.root is not None:
            # walked once: a big folder walked twice per look was most of one
            if self._walked is None:
                self._walked = [(p.relative_to(self.root).as_posix(), p.stat().st_size)
                                for p in sorted(self.root.rglob("*"))
                                if p.is_file() and not p.is_symlink()]
            return self._walked
        return [(self.path.name, self.path.stat().st_size)]

    def listing(self, outer: str) -> list:
        """every name in one of the zips or unpacked archives inside it"""
        if outer in self._folders:
            return [name for name, _ in self._unpacked(outer)]
        return list(self._inner[outer][1])

    def _unpacked(self, outer: str) -> list:
        folder = self._folders[outer]
        return [(p.relative_to(folder).as_posix(), p.stat().st_size)
                for p in sorted(folder.rglob("*")) if p.is_file() and not p.is_symlink()]

    def _readable(self, member: str) -> None:
        """a member read out of a zip it's locked in says so, rather than as zipfile's error"""
        nested, name = self._inside(member)
        if nested is not None:
            info, held = nested[1][name], member[:-len(name) - 1].rsplit("/", 1)[-1]
        elif self._zip is not None and member in self._names:
            info, held = self._names[member], self.path.name
        else:
            return
        if _sealed(info):
            raise FilesError(f"{member.rsplit('/', 1)[-1]} in {held} is locked with a password, "
                             f"or packed so only 7-Zip opens it. Unpack {held} with 7-Zip (with "
                             "the password its mod page gives, if it asks) and drop the folder "
                             "that comes out.")

    def _inside(self, member: str):
        """(the nested zip's record, the name in it) for a member of one, or (None, None)"""
        for outer, (inner, names) in self._inner.items():
            if member.startswith(outer + "/") and member[len(outer) + 1:] in names:
                return (inner, names), member[len(outer) + 1:]
        return None, None

    def _unpacked_file(self, member: str):
        """where a member of a nested .7z or .rar is on disk, or None"""
        for outer, folder in self._folders.items():
            if member.startswith(outer + "/"):
                path = folder / member[len(outer) + 1:]
                if path.is_file():
                    return path
        return None

    def opened(self, member: str):
        """
        a member as a file to seek through, the caller's to close: a whole
        archive is gigabytes, more than read() should hand over at once. Where
        it sits when that's on the disk, its stretch of the zip when it's
        stored there whole, else copied out beside the drop first
        """
        unpacked = self._unpacked_file(member) if self._folders else None
        if unpacked is not None:
            return unpacked.open("rb")
        self._readable(member)
        nested = self._inside(member)
        if nested[0] is None and self.root is not None:
            return (self.root / member).open("rb")
        if nested[0] is None and self._zip is not None:
            spot = self._stored(self._names[member])
            if spot is not None:
                return io.BufferedReader(_Stretch(self.path, *spot), 1 << 20)
        elif nested[0] is None:
            return self.path.open("rb")
        copied = self._whole.get(member)
        if copied is None or not copied.is_file():
            self._copies += 1
            if nested[0] is None:
                copied = self._copy_out(member, f"whole-{self._copies}")
            else:
                (inner, names), name = nested
                info = names[name]
                copied = self._copy_out(member, f"whole-{self._copies}",
                                        opener=lambda: inner.open(info), size=info.file_size)
            self._whole[member] = copied
        return copied.open("rb")

    def head(self, member: str, size: int = 8) -> bytes:
        """a member's first bytes, read where it is without copying it anywhere"""
        self._readable(member)
        nested, name = self._inside(member)
        try:
            if nested is not None:
                inner, names = nested
                with inner.open(names[name]) as handle:
                    return handle.read(size)
            unpacked = self._unpacked_file(member) if self._folders else None
            where = unpacked if unpacked is not None else \
                self.root / member if self.root is not None else None
            if where is None and self._zip is not None:
                with self._zip.open(self._names[member]) as handle:
                    return handle.read(size)
            with (where or self.path).open("rb") as handle:
                return handle.read(size)
        except OSError as e:
            if is_threat_block(e):
                raise FilesError(threat_message(member.rsplit("/", 1)[-1])) from e
            raise

    def _untar(self) -> Path:
        import tempfile

        from patcher.fileops import scratch_near

        if not hasattr(tarfile, "data_filter"):
            raise FilesError("the patcher can't open a .tar safely. Unpack it yourself and drop "
                             "the folder instead.")
        self._staging = Path(tempfile.mkdtemp(prefix="ffxiii-mod-",
                                              dir=scratch_near(self.path.parent)))
        try:
            with tarfile.open(self.path, mode="r:*") as archive:
                archive.extractall(self._staging, filter="data")
        except BaseException:
            self.close()
            raise
        return self._staging

    def members(self) -> list:
        """(name, size) for every file in it, a nested zip's in place of the zip"""
        out = [(name, size) for name, size in self._own()
               if name not in self._inner and name not in self._folders]
        for outer, (_, names) in self._inner.items():
            out += [(f"{outer}/{name}", info.file_size) for name, info in names.items()]
        for outer in self._folders:
            out += [(f"{outer}/{name}", size) for name, size in self._unpacked(outer)]
        return out

    def folders(self) -> list:
        """
        every folder in it, empty ones included: an installer can check for a
        folder its zip ships empty for it to copy into (BareLight's chr/pc/c201/bin)
        """
        if self._zip is not None:
            names = [n.rstrip("/") for n in self._zip.namelist()]
            out = {n for n in names if n and n + "/" in self._zip.NameToInfo}
        elif self.root is not None:
            out = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                   if p.is_dir() and not p.is_symlink()}
        else:
            out = set()
        # the parents of every file too, which a zip needn't list
        for name, _ in self.members():
            parts = name.split("/")[:-1]
            out.update("/".join(parts[:n]) for n in range(1, len(parts) + 1))
        return sorted(out)

    def size(self, member: str) -> int:
        """the bytes in one of its own files, a zip or .7z inside it included"""
        return dict(self._own()).get(member, 0)

    def has(self, member: str) -> bool:
        if self._inside(member)[0] is not None or self._unpacked_file(member) is not None:
            return True
        if member in self._inner or member in self._folders:
            return False
        if self._zip is not None:
            return member in self._names
        if self.root is not None:
            return (self.root / member).is_file()
        # one loose file, so not the whole of a .7z read in for every lookup
        return member == self.path.name

    def read(self, member: str) -> bytes:
        self._readable(member)
        nested, name = self._inside(member)
        try:
            if nested is not None:
                inner, names = nested
                return inner.read(names[name])
            unpacked = self._unpacked_file(member) if self._folders else None
            if unpacked is not None:
                return unpacked.read_bytes()
            if self._zip is not None:
                return self._zip.read(self._names[member])
            if self.root is not None:
                return (self.root / member).read_bytes()
            return self.path.read_bytes()
        except OSError as e:
            if is_threat_block(e):
                raise FilesError(threat_message(member.rsplit("/", 1)[-1])) from e
            raise

    def close(self) -> None:
        from patcher.fileops import clear_tree

        for inner, _ in self._inner.values():
            inner.close()
        self._inner, self._folders = {}, {}
        for stretch in self._stretches:
            stretch.close()
        self._stretches = []
        if self._zip is not None:
            self._zip.close()
            self._zip = None
        if self._staging is not None:
            clear_tree(self._staging)
            self._staging = None
        self._whole = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _Stretch(io.RawIOBase):
    """part of a file read as a file of its own: a zip stored whole inside another"""

    def __init__(self, path, start: int, size: int):
        super().__init__()
        self._handle = Path(path).open("rb")
        self._start, self._size, self._at = start, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._at

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._at, io.SEEK_END: self._size}[whence]
        self._at = max(0, base + offset)
        return self._at

    def readinto(self, buffer) -> int:
        want = min(len(buffer), self._size - self._at)
        if want <= 0:
            return 0
        self._handle.seek(self._start + self._at)
        got = self._handle.readinto(memoryview(buffer)[:want]) or 0
        self._at += got
        return got

    def close(self) -> None:
        if not self.closed:
            self._handle.close()
        super().close()


@dataclass
class Look:
    """what a drop holds, as far as one game's archives go"""
    picks: dict = field(default_factory=dict)       # (path, voice) -> member, "" for every set
    sizes: dict = field(default_factory=dict)       # member -> bytes
    by_name: list = field(default_factory=list)     # placed by a file name the game has once
    clashes: dict = field(default_factory=dict)     # (path, voice) -> different files for it
    versions: list = field(default_factory=list)    # the folders those clashes split into
    keys: dict = field(default_factory=dict)        # version -> the folder it means, "" for the top
    unmatched: list = field(default_factory=list)   # looked like the game's, listed nowhere
    loose: list = field(default_factory=list)       # a bare name the game has more than once
    zones: list = field(default_factory=list)
    code: list = field(default_factory=list)
    installers: list = field(default_factory=list)
    art: list = field(default_factory=list)
    whole: list = field(default_factory=list)       # entire archives, not files for one
    whole_why: dict = field(default_factory=dict)   # why one of those couldn't go in
    elsewhere: list = field(default_factory=list)   # under another game's data folder
    homes: Counter = field(default_factory=Counter)  # which, for those
    empty: list = field(default_factory=list)       # 0 bytes, an unpack that never finished
    doubled: list = field(default_factory=list)     # one name twice in a zip, different bytes
    matched: set = field(default_factory=set)       # every game path something in it is for
    members_at: dict = field(default_factory=dict)  # game path -> every member that's for it
    voices: dict = field(default_factory=dict)      # member -> the voice set it's for, "" for any
    disk: dict = field(default_factory=dict)        # member -> the file it is loose on disk
    exes: list = field(default_factory=list)        # the game's own exe, which stays Steam's
    deltas: list = field(default_factory=list)      # xdelta patches rather than files
    delta_why: dict = field(default_factory=dict)   # why one of those couldn't be done
    saves: list = field(default_factory=list)       # saved games, which live with Steam's
    # (container path, voice) -> {(record, ...): [members]}, through WPDtool's
    # unpack folders: db/resident/_wdbpack.bin/r_dlc.wdb
    records: dict = field(default_factory=dict)
    tools: list = field(default_factory=list)       # Cheat Engine, TexMod, spreadsheets
    shade_addons: list = field(default_factory=list)  # ReShade add-ons on their own
    layered: list = field(default_factory=list)     # LayeredFS, which the archives make unneeded
    loaders: list = field(default_factory=list)     # named after a Windows dll, beside the exe
    loader_files: dict = field(default_factory=dict)  # member -> its path beside the exe
    x64: list = field(default_factory=list)         # one of those built 64-bit, which can't load
    x64_files: list = field(default_factory=list)   # what's with one, its own data
    blocked_files: list = field(default_factory=list)  # what's with a proxy Windows blocked
    managed: list = field(default_factory=list)     # .NET, the mod's own installer's
    blocked: list = field(default_factory=list)     # Windows wouldn't hand it over
    patches: list = field(default_factory=list)     # Nova Chrysalia's patches to the exe
    nested: list = field(default_factory=list)      # the zips inside it, opened
    unopened: list = field(default_factory=list)    # a zip in it that won't open, a .7z or .rar
    reshade: list = field(default_factory=list)     # ReShade builds, 32-bit, by its export
    reshade_files: dict = field(default_factory=dict)  # member -> its path under ReShade/
    reshade_info: dict = field(default_factory=dict)   # its preset and menu key, for the list
    reshade_rewrites: dict = field(default_factory=dict)  # its settings, paths made to fit
    reshade_clash: list = field(default_factory=list)  # different builds, nothing to say which
    reshade_spare: list = field(default_factory=list)  # its other settings, which stay out
    # index name -> {path: (code, the path its record has now)}, for a record a
    # mod gives its name back: Steam's XIII-2 lists the cut costumes under
    # c201's and c204's names, so their own paths aren't in the index to match
    renames: dict = field(default_factory=dict)
    # the only indexes it goes into, when what it holds was made from one
    # archive's copy of a file another archive of the same voice set has too
    only: set = field(default_factory=set)
    # the same for one path: a container built out of white_img2's copy
    pairs_for: dict = field(default_factory=dict)


def _parts(member: str) -> list:
    return [p for p in _key(member).split("/") if p not in ("", ".")]


def _junk(parts) -> bool:
    return any(p in ("__macosx", ".ds_store", "thumbs.db") or p.startswith("._") for p in parts)


def _place(parts: list, game: Game):
    """(entry path, how many leading parts it skipped) for the longest tail the game lists"""
    for n in range(len(parts) - 1):
        tail = "/".join(parts[n:])
        if tail in game.listed:
            return tail, n
    return None, 0


def _spot(parts: list, own: list, game: Game):
    """
    (game path or None, the drop's own folders in front of it, whether it went
    by name). The longest tail of the path the game lists, or else the file's
    name when the game has that name once and the file's folders, from the
    first one named like one of the game's on, are all on that path:
    Serah Dress/c108.win32.trb and c108/c108.win32.trb go in by name,
    chr/pc/c999/bin/c108.win32.trb doesn't
    """
    path, skipped = _place(parts, game)
    if path is not None:
        return path, own[:max(0, skipped - (len(parts) - len(own)))], False
    folders, there = own[:-1], game.leaves.get(own[-1], ())
    if len(there) == 1:
        path = next(iter(there))
        theirs = set(path.split("/")[:-1])
        start = next((i for i, p in enumerate(folders) if p in game.dirs), len(folders))
        if all(p in theirs for p in folders[start:]):
            return path, folders, True
    return None, folders, False


def _disk_spot(parts: list, game: Game):
    """
    the file in the game's folder a member is when it's one the game reads
    loose rather than out of an archive: the longest tail of its path, a
    folder and the file at least, that's a file there, or a file the game
    hasn't got yet laid out from its data folder into a folder it has. Never
    one of sys's archives or indexes, which go in record by record, or
    anything in a folder holding an exe, which is the program's and the
    loaders' to fill
    """
    leaf = parts[-1]
    # a zone's archive is loose on disk and nothing of the drawer's goes into
    # it, so one swapped whole is a file like any other where no HD Project
    # builds into it
    blob = ZONE_BLOB.match(leaf)
    if (WHOLE.match(leaf) and not blob) or leaf.endswith(CODE + (".exe",)) \
            or (blob and game.data in HD_ZONES):
        return None
    files, folders, programs, leaves = game.disk()
    # the file by the longest tail of the member's path it ends in, a folder
    # and the file at least, and only when no other file there ends the same
    best, rel = 1, None
    for key in leaves.get(leaf, ()):
        theirs = key.split("/")
        same = 0
        while same < min(len(parts), len(theirs)) and parts[-1 - same] == theirs[-1 - same]:
            same += 1
        if same > best:
            best, rel = same, files[key]
        elif same == best:
            rel = None
    if rel is None and game.data and game.data in parts[:-1]:
        start = len(parts) - 1 - parts[::-1].index(game.data)
        parent = "/".join(parts[start:-1])
        # its own folder has to be the game's, and not the data folder's top,
        # where a stray file would sit beside sys and nothing reads it
        if parent in folders and "/" in parent:
            rel = f"{folders[parent]}/{leaf}"
    if rel is None:
        return None
    parent = rel.lower().rpartition("/")[0]
    if parent.rsplit("/", 1)[-1] == "sys" or parent in programs:
        return None
    # NovaFix's add-ons and ReShade, beside the exe, are theirs
    for program in programs:
        below = parent[len(program) + 1:] if program else parent
        if (not program or parent.startswith(program + "/")) \
                and below.split("/", 1)[0] in PROGRAM_FOLDERS:
            return None
    return rel


def _in_container(parts: list, own: list, game: Game):
    """
    (the game path of the container, the records inside it) for a member
    WPDtool's unpack folders put inside one of the game's files, matched the
    way any member is, or None
    """
    from patcher.mods import wpd

    held = wpd.split("/".join(own))
    # a model's _c108.win32.trb folder is a texture tool's unpack, not records
    if held is None or not held[0].endswith(WPDS):
        return None
    container = [p for p in held[0].split("/") if p]
    path = _spot(parts[:len(parts) - len(own)] + container, container, game)[0]
    return None if path is None or ZONE_INDEX.match(path) else (path, held[1])


def _unindexed(found: Look) -> None:
    """
    a zone's archive that came with its own index out of white_img, which this
    doesn't put in, out of found.disk: the index is what says where everything
    in the archive is, so one without the other is a crash
    """
    indexes = {_leaf(m).lower() for m in found.zones}
    for member, rel in list(found.disk.items()):
        leaf = _leaf(rel).lower()
        if ZONE_BLOB.match(leaf) and f"filelist_{leaf.split('_')[1]}.win32.bin" in indexes:
            del found.disk[member]
            found.whole.append(member)
            found.whole_why[member] = ("it comes with its own index for that zone, which goes "
                                       "inside white_img where this can't put it, and the two "
                                       "only work together")


def _layered(found: Look) -> None:
    """
    LayeredFS out of what goes beside the exe: a dinput8.dll with a
    layeredLS.ini or a mod_data folder beside it. What it reads out of
    mod_data is the game's own files, which go into the archives here, so the
    loader would only be one more dll in the way, of Savior's Patch's on an LR
    that has it
    """
    for dll in list(found.loaders):
        home = _parts(dll)[:-1]
        near = [_parts(m)[len(home):] for m in found.sizes
                if _parts(m)[:len(home)] == home and len(_parts(m)) > len(home)]
        if _parts(dll)[-1] != "dinput8.dll" or not any(
                (len(rest) == 1 and rest[0] in LAYERED) or (len(rest) > 1 and rest[0] == LAYERED_DATA)
                for rest in near):
            continue
        found.loaders.remove(dll)
        own = [m for m in found.loader_files if _parts(m)[:len(home)] == home]
        for member in own:
            found.loader_files.pop(member)
        found.layered += sorted(set(own) | {dll}, key=lambda m: (m != dll, m))


def look(members, game: Game, read=None, version: str | None = None, within=(),
         root: str = "", doubled=(), nested=(), unopened=(), blocked=()) -> Look:
    """
    sorts a drop's members against what the game's indexes list.

    A member matches by the longest tail of its path that's a whole entry path,
    so a Data\\ or a white_imgu\\ in front of it doesn't matter and two files
    with the same name in different folders never swap. The folders the drop
    sits in (`within`) count too, for that and for which voice set or game it's
    for. A file none of whose own folders are the game's goes by its name, and
    only when the game has that name once.

    Two different files for one place is a mod that comes in versions, one
    folder each, or a folder of options inside the mod's own files. `version`
    names the one wanted, laid over what the versions share; without it
    they're listed for the caller to ask about. `root` names a version that's
    the drop's own top-level files, `read` settles two files the same size
    and says what each .dll is, and `doubled` is names a zip lists twice with
    different bytes. `nested`, `unopened` and `blocked` are what Source made
    of the zips inside it.

    Every member is listed against the game path it's for in `members_at`,
    the ones that aren't going in too, which is what install() keeps a model
    whole by
    """
    from patcher.ingest import is_asset

    members, context = list(members), list(within)
    chosen, excluded, offered = None, (), None
    if version is not None:
        offered = look(members, game, read, within=within, root=root, doubled=doubled)
        mine = picked(version, offered.keys)
        if mine is None:
            raise FilesError(f"{version} isn't one of its versions. Drop it again to pick one.")
        chosen = offered.keys[mine]
        # every other version's own files, but not a folder the chosen one is
        # inside of, which is what it's laid over
        excluded = tuple(k + "/" for k in offered.keys.values()
                         if k and k != chosen and not (chosen + "/").startswith(k + "/"))
    found, candidates, homes, here = Look(), {}, {}, game.data
    found.nested, found.unopened, found.blocked = list(nested), list(unopened), list(blocked)
    for member, size in members:
        own = _parts(member)
        if not own or _junk(own) or (excluded and "/".join(own).startswith(excluded)):
            continue
        found.sizes[member] = size
        parts, leaf, folders = context + own, own[-1], own[:-1]
        voice = _voice(parts)
        # a pack for the whole series keeps each game's files under its own
        # data folder, and the other games' paths can be this one's too. The
        # nearest one says, wherever the drop itself sits
        home = next((p for p in reversed(parts[:-1]) if p in DATA_DIRS), here)
        if home != here:
            found.elsewhere.append(member)
            found.homes[home] += 1
            continue
        path, homes[member], by_name = _spot(parts, own, game)
        if by_name:
            found.by_name.append(member)
        if path is not None and not ZONE_INDEX.match(path):
            found.matched.add(path)
            found.members_at.setdefault(path, []).append(member)
            found.voices[member] = voice
        if path is not None and size == 0:
            # an unpack or a download that stopped short. In the index 0
            # bytes is what marks a placeholder, so in, it could never be
            # told from one or come back off. Still the mod's, for keeping
            # a model whole
            found.empty.append(member)
        elif path is not None and ZONE_INDEX.match(path):
            found.zones.append(member)
        elif path is not None:
            candidates.setdefault((path, voice), []).append(member)
        elif (held := _in_container(parts, own, game)) is not None:
            found.records.setdefault((held[0], voice), {}).setdefault(held[1], []).append(member)
        elif leaf in GAME_EXES:
            found.exes.append(member)
        elif leaf.endswith(DELTAS):
            found.deltas.append(member)
        elif SAVE.match(leaf):
            found.saves.append(member)
        elif (spot := _disk_spot(parts, game)) is not None:
            found.disk[member] = spot
        elif game.leaves.get(leaf) and not any(p in game.dirs for p in folders):
            found.loose.append(member)
        elif WHOLE.match(leaf):
            found.whole.append(member)
            if ZONE_BLOB.match(leaf) and game.data in HD_ZONES:
                found.whole_why[member] = ("in this game a zone's archive is the HD Project's to "
                                           "build into, so it isn't swapped for another")
        elif ZONE_ART.search("/".join(parts)):
            found.zones.append(member)
        elif leaf.endswith(CODE):
            found.code.append(member)
        elif leaf.endswith(CODE_PATCHES):
            found.patches.append(member)
        elif leaf.endswith(tuple(TOOLS)) or leaf in SETTINGS:
            found.tools.append(member)
        elif leaf.endswith(RESHADE_ADDONS):
            found.shade_addons.append(member)
        elif leaf.endswith(INSTALLERS):
            found.installers.append(member)
        elif leaf.endswith(LOOSE_ART):
            found.art.append(member)
        elif is_asset(member):
            found.unmatched.append(member)
    for member in doubled:
        own = _parts(member)
        path = _spot(context + own, own, game)[0] if own else None
        if path is not None:
            found.doubled.append(member)
            found.matched.add(path)
            found.members_at.setdefault(path, []).append(member)
            found.voices[member] = _voice(context + own)

    for place, those in candidates.items():
        kept = _distinct(those, found.sizes, read)
        if len(kept) > 1 and chosen:
            # the chosen version over what it's laid on: its own file wins
            mine = [m for m in kept if "/".join(_parts(m)).startswith(chosen + "/")]
            kept = mine if len(mine) == 1 else kept
        if len(kept) == 1:
            found.picks[place] = kept[0]
        else:
            found.clashes[place] = kept
    if found.clashes and chosen is None:
        found.versions, found.keys = _versions(found.clashes, homes, root)
    if read is not None and found.code:
        _sort_code(found, read, homes, root, chosen)
    if found.disk and found.loaders:
        # what an earlier version of a loader put beside the exe is on the
        # disk now, and it's that loader's to claim again
        held = game.loader_held()
        for member, rel in list(found.disk.items()):
            if rel.lower() in held:
                del found.disk[member]
    if found.loaders or found.x64 or found.blocked:
        _claim(found)
    if found.loaders:
        _layered(found)
    if found.disk and found.zones:
        _unindexed(found)
    if found.shade_addons:
        # one a ReShade build or a loader brought along is theirs
        found.shade_addons = [m for m in found.shade_addons
                              if m not in found.reshade_files and m not in found.loader_files
                              and m not in found.layered]
    if offered is not None:
        # what the other versions have still counts for keeping a model whole
        found.matched, found.members_at = offered.matched, offered.members_at
        found.voices = offered.voices
    return found


def look_in(source: "Source", game: Game, version: str | None = None, root: str = "") -> Look:
    """look() at a Source, with where it sits and what its zip lists twice"""
    return look(source.members(), game, source.read, version=version, within=source.context,
                root=root, doubled=source.doubled, nested=source.nested,
                unopened=source.unopened, blocked=source.blocked)


def _voice(parts: list) -> str:
    """the voice set a file's folders say it's for, or "" for every one"""
    for part in parts[:-1]:
        matched = UNPACKED.match(part)
        if matched:
            return SPOKEN.get(matched.group(2), "")
        if part in NOVA_VOICES:
            return NOVA_VOICES[part]
    return ""


def _sort_code(found: Look, read, homes=None, root: str = "", chosen: str | None = None) -> None:
    """
    each .dll by what it is, off its own headers: one named after a Windows
    DLL goes beside the exe, a .NET library is an installer's and goes
    nowhere, and the rest are NovaFix's add-ons, as before. A .dll antivirus
    won't hand over is said, not guessed at. A ReShade .asi is read too, the
    build renamed for an ASI loader. Not every .asi: the plugin a loader
    brings goes in with it or not at all, and its own read says which
    """
    from patcher.addon_sieve import IMAGE_FILE_MACHINE_I386, pe_kind, reshade_build
    from patcher.mods.loader_mods import PROXY_NAMES

    code, builds = [], {}
    for member in found.code:
        leaf = _parts(member)[-1]
        if not (leaf.endswith(".dll") or (leaf.endswith(".asi") and "reshade" in leaf)):
            code.append(member)
            continue
        try:
            # whole, since a long DOS stub puts the headers past any fixed read
            data = read(member)
            kind = pe_kind(data)
        except FilesError:
            found.blocked.append(member)
            continue
        major = reshade_build(data) if kind is not None else None
        if major is not None:
            # ReShade, under whatever name its download gave it. NovaFix only
            # takes it as ReShade\d3d9.dll, and as a d3d9.dll beside the exe it
            # would be over NovaFix itself
            if kind["machine"] == IMAGE_FILE_MACHINE_I386:
                found.reshade.append(member)
                builds[member] = (major, hashlib.sha256(data).digest())
            else:
                found.x64.append(member)
        elif leaf.endswith(".asi"):
            code.append(member)
        elif kind is not None and kind["managed"]:
            found.managed.append(member)
        elif kind is not None and leaf in PROXY_NAMES:
            # these games are 32-bit, and Windows loading a 64-bit one in
            # place of its own stops the exe starting at all. The sieve says
            # so for NovaFix's add-ons, and beside the exe it matters more
            (found.loaders if kind["machine"] == IMAGE_FILE_MACHINE_I386
             else found.x64).append(member)
        else:
            code.append(member)
    found.code = code
    if found.reshade:
        _claim_reshade(found, read, builds, homes or {}, root, chosen)


def _spelled(member: str) -> list:
    return [p for p in member.replace("\\", "/").split("/") if p not in ("", ".")]


# ReShade's menu key in its settings, as a virtual key code: KeyMenu up to 4.x,
# KeyOverlay since
VK_NAMES = {36: "Home", 45: "Insert", 35: "End", 33: "Page Up", 34: "Page Down", 19: "Pause",
            145: "Scroll Lock", 46: "Delete", **{111 + n: f"F{n}" for n in range(1, 13)}}


def _busy(found: Look, *more) -> tuple:
    """
    (the members that are something else's, every folder holding one of
    them). `more` is other members to count as something else's
    """
    taken = set(found.picks.values()) | set(found.empty) | set(found.zones) \
        | set(found.whole) | set(found.elsewhere) | set(found.installers) \
        | set(found.managed) | set(found.doubled) | set(found.patches) | set(found.blocked) \
        | set(found.unmatched) | set(found.loose) | set(found.x64) | set(found.disk) \
        | set(found.exes) | set(found.deltas) | set(found.saves) | set(found.tools) \
        | {m for held in found.records.values() for those in held.values() for m in those}
    taken |= {m for those in found.clashes.values() for m in those}
    taken |= {m for those in more for m in those}
    busy = set()
    for member in taken | {m for those in found.members_at.values() for m in those}:
        parts = [p.lower() for p in _spelled(member)]
        busy.update("/".join(parts[:n]) for n in range(1, len(parts)))
    return taken, busy


# ReShade's own folders by name, and what's only ever ReShade's in one
RESHADE_DIRS = {"reshade-shaders", "shaders", "textures", "presets", "reshade-presets"}
SHADERS = (".fx", ".fxh", ".addon", ".addon32")
PICTURES = (".png", ".jpg", ".jpeg", ".dds", ".bmp", ".tga")
NOTES = (".txt", ".md", ".pdf", ".url", ".html")
# the settings that point at its files, each a comma separated list, and what
# each one goes looking for there
SEARCH_KEYS = {"EffectSearchPaths": SHADERS, "TextureSearchPaths": PICTURES,
               "PresetSearchPaths": (".ini", ".txt"), "PresetFiles": (".ini", ".txt"),
               "CurrentPresetPath": (".ini", ".txt"), "PresetPath": (".ini", ".txt")}
SEARCH_LINE = re.compile(r"(?im)^(\s*(" + "|".join(SEARCH_KEYS) + r")\s*=)(.*?)(\r?)$")
# what makes an .ini or a .txt a preset rather than a readme
PRESET_LINE = re.compile(rb"(?im)^\s*Techniques\s*=")


def _shade_inis(found: Look, read, home: list) -> tuple:
    """(its settings files as (member, text), its presets) beside a build in `home`"""
    configs, presets = [], []
    for member in found.sizes:
        parts = [p.lower() for p in _spelled(member)]
        if parts[:-1] != home or not parts[-1].endswith((".ini", ".txt")) \
                or not found.sizes[member]:
            continue
        text = read(member).decode("utf-8", "replace")
        if re.search(r"(?im)^\s*Techniques\s*=", text):
            presets.append(member)
        # [General] alone is half the .ini files there are: ReShade's says
        # where its shaders are, or which key opens it
        elif re.search(r"(?im)^\s*\[GENERAL\]", text) and (
                SEARCH_LINE.search(text) or re.search(r"(?im)^\s*Key(?:Menu|Overlay)\s*=", text)):
            configs.append((member, text))
    return configs, presets


def presets_in(members, read) -> tuple:
    """
    a ReShade preset with no ReShade, the most common way one is shared: (its
    preset .ini files, {path under its Shaders folder: member}, the same for
    Textures), or nothing for a drop that isn't one. A preset is an .ini
    saying which techniques it runs
    """
    presets, shaders, textures = [], {}, {}
    for member, size in members:
        parts = _spelled(member)
        if not parts or _junk([p.lower() for p in parts]):
            continue
        leaf = parts[-1].lower()
        if leaf.endswith(".ini"):
            try:
                if size and size < 1 << 20 and PRESET_LINE.search(read(member)):
                    presets.append(member)
            except (FilesError, OSError):
                pass
            continue
        lowered = [p.lower() for p in parts[:-1]]
        if "textures" in lowered and leaf.endswith(PICTURES):
            textures["/".join(parts[lowered.index("textures") + 1:])] = member
        elif leaf.endswith(SHADERS):
            below = parts[lowered.index("shaders") + 1:] if "shaders" in lowered else parts[-1:]
            shaders["/".join(below)] = member
    return (presets, shaders, textures) if presets else ([], {}, {})


def _claim_reshade(found: Look, read, builds: dict, homes: dict, root: str = "",
                   chosen: str | None = None) -> None:
    """
    what goes in with a ReShade build, under ReShade\\ where NovaFix loads it
    from: the build as d3d9.dll, its settings under the name that build reads,
    its presets, its shaders, and the folders that are ReShade's own. ReShade
    resolves a relative path in its settings from its own folder, so what's
    under the build keeps where it sat, and a path that reached outside it or
    was written for someone else's PC is pointed at where its files went. A
    copy of the build under another name, a readme, or a folder that's another
    mod's or the drop's own isn't ReShade's to have. Two different builds are
    a choice: versions to pick from when they're in folders of their own and
    the drop isn't already asking about others, or left out and said. `chosen`
    is the version picked, whose own build wins over one it's laid over
    """
    from patcher.mods.loader_mods import NOT_DATA

    named = lambda m: (_parts(m)[-1] != "d3d9.dll", _parts(m)[-1] != "reshade32.dll", len(m), m)
    distinct = {}
    for member in sorted(found.reshade, key=named):
        distinct.setdefault(builds[member][1], member)
    if len(distinct) > 1 and chosen is not None:
        mine = {d: m for d, m in distinct.items() if "/".join(_parts(m)).startswith(chosen + "/")}
        distinct = mine if len(mine) == 1 else distinct
    if len(distinct) > 1:
        # one picker for the drop: a version of its game files picked would
        # leave both ReShades out without a word
        versions, keys = _versions({("reshade", ""): list(distinct.values())}, homes, root) \
            if chosen is None and not found.versions else ([], {})
        if versions:
            found.versions, found.keys = versions, keys
        else:
            found.reshade_clash = sorted(distinct.values())
            for member in found.reshade_clash:
                configs, presets = _shade_inis(found, read, [p.lower() for p in
                                                             _spelled(member)[:-1]])
                found.reshade_spare += [m for m, _ in configs] + presets
        return
    build = next(iter(distinct.values()))
    major = builds[build][0]
    home = [p.lower() for p in _spelled(build)[:-1]]
    taken, busy = _busy(found, found.code, found.loaders)
    # another mod's code beside the build: its folder is shared, and what's
    # only pictures in it could as well be that mod's
    shared = any([p.lower() for p in _spelled(m)[:-1]] == home for m in found.code + found.loaders)

    beside, below = {}, {}
    for member in found.sizes:
        parts = _spelled(member)
        if member in found.reshade or member in taken or len(parts) <= len(home) \
                or [p.lower() for p in parts[:len(home)]] != home:
            continue
        rest = parts[len(home):]
        if len(rest) == 1:
            beside[member] = rest[0]
        else:
            below.setdefault(rest[0], []).append(member)

    configs, presets = _shade_inis(found, read, home)
    config = text = None
    if configs:
        # 4.x reads its own name's settings first and 5 on only ReShade.ini, so
        # a stale one beside a newer build loses to the one it reads
        stem = _parts(build)[-1].rsplit(".", 1)[0]
        own = lambda c: _parts(c[0])[-1] != f"{stem}.ini"
        common = lambda c: _parts(c[0])[-1] != "reshade.ini"
        config, text = min(configs, key=lambda c: ((common(c), own(c)) if major >= 5
                                                   else (own(c), common(c)), c[0]))
        found.reshade_spare += [m for m, _ in configs if m != config]

    files = {build: "ReShade/d3d9.dll"}
    wanted, rewritten = set(), {}
    for key, entry in _search_entries(text or ""):
        place = _resolve(entry, home, found.sizes, taken)
        if place is None:
            continue
        parts, tail, leaf, moved = place
        recursive = entry.rstrip().endswith("**")
        if parts[:len(home)] == home:
            # the build's folder or under it, which goes with it as it is
            if len(parts) > len(home):
                wanted.add(parts[len(home)])
            if not moved:
                continue
        else:
            # outside it: what this setting looks for there goes under
            # ReShade\ by the path's own last folders. Not another build, a
            # mod's folder, a readme's, nor all of a folder the path only
            # passes. Busy anywhere from where the two part ways, so a
            # "Textures" inside another mod's folder stays that mod's
            common = next((n for n, (a, b) in enumerate(zip(parts, home)) if a != b),
                          min(len(parts), len(home)))
            for member in found.sizes:
                spelled = [p.lower() for p in _spelled(member)]
                inner = spelled[len(parts):]
                if spelled[:len(parts)] != parts or member in taken or member in found.reshade \
                        or (len(inner) > 1 and not recursive) \
                        or any(f in NOT_DATA for f in inner[:-1]) \
                        or any("/".join(spelled[:n]) in busy
                               for n in range(common + 1, len(spelled))) \
                        or not inner[-1].endswith(SEARCH_KEYS[key]) \
                        or (leaf and inner != [leaf.lower()]) \
                        or (key.startswith(("Preset", "CurrentPreset")) and not (
                            found.sizes[member] and PRESET_LINE.search(read(member)))):
                    continue
                files.setdefault(member, "ReShade/" + "/".join(tail + _spelled(member)[len(parts):]))
        rewritten[entry] = "\\".join(["."] + tail + ([leaf] if leaf else
                                                     ["**"] if recursive else []))

    flat = any(leaf.lower().endswith((".fx", ".fxh")) for leaf in beside.values())
    for member, leaf in beside.items():
        low = leaf.lower()
        if member in presets or low.endswith(SHADERS) or (flat and low.endswith(PICTURES)):
            files[member] = "ReShade/" + leaf
    if config is not None:
        files[config] = "ReShade/ReShade.ini" if major >= 5 else "ReShade/d3d9.ini"
    for first, members in below.items():
        low = first.lower()
        if low in NOT_DATA or "/".join(home + [low]) in busy:
            continue
        leaves = [_parts(m)[-1] for m in members]
        if low in RESHADE_DIRS or low in wanted or any(f.endswith(SHADERS) for f in leaves) \
                or any(f.endswith((".ini", ".txt")) and found.sizes[m] and PRESET_LINE.search(read(m))
                       for m, f in zip(members, leaves)) \
                or (not shared and all(f.endswith(PICTURES + NOTES) and not f.endswith(".dds")
                                       for f in leaves)):
            for member in members:
                files.setdefault(member, "ReShade/" + "/".join(_spelled(member)[len(home):]))

    info = {}
    if text is not None:
        if rewritten:
            found.reshade_rewrites = {config: _rewrite_paths(read(config), rewritten)}
        info = reshade_said(text)
    if "preset" not in info and len(presets) == 1:
        info["preset"] = Path(_parts(presets[0])[-1]).stem
    found.reshade_files, found.reshade_info = files, info
    claimed = set(files) | set(found.reshade)
    for part in ("art", "code"):
        setattr(found, part, [m for m in getattr(found, part) if m not in claimed])


def reshade_said(text: str) -> dict:
    """the key that opens its menu and the preset it starts on, off its .ini"""
    info = {}
    key = re.search(r"(?im)^\s*Key(?:Menu|Overlay)\s*=\s*(\d+)", text)
    info["menu"] = VK_NAMES.get(int(key.group(1)), "") if key else "Home"
    preset = re.search(r"(?im)^\s*(?:CurrentPresetPath|PresetPath)\s*=\s*(.+?)\s*$", text)
    if preset:
        info["preset"] = Path(preset.group(1).strip('"').replace("\\", "/")).stem
    return info


def reshade_side(found: Look) -> set:
    """
    what in a drop is ReShade's, whether it went in or not: never an add-on's,
    which copies every .ini near its code
    """
    return set(found.reshade_files) | set(found.reshade) | set(found.reshade_clash) \
        | set(found.reshade_spare)


def _search_entries(text: str) -> list:
    """(setting, path) for every path ReShade's settings point at, as written"""
    out = []
    for match in SEARCH_LINE.finditer(text):
        key = next(k for k in SEARCH_KEYS if k.lower() == match.group(2).lower())
        for entry in match.group(3).split(","):
            entry = entry.strip().strip('"')
            if entry and (key, entry) not in out:
                out.append((key, entry))
    return out


def _resolve(entry: str, home: list, sizes: dict, taken) -> tuple | None:
    """
    (the folder in the drop a settings path means as lowercased parts, the
    folders to keep it under from ReShade's, the file it names or "", whether
    it has to be written again to mean that from there), or None when it
    means nothing in the drop. A relative one from the build's folder, ".."
    and all. A whole one, written on somebody else's PC, by the build's own
    folder when it names a file that's there, else the longest run of its last
    folders the drop has, nearest the build first
    """
    spelled = [p for p in entry.replace("\\", "/").split("/") if p not in ("", ".", "**", "*")]
    leaf = ""
    if spelled and Path(spelled[-1]).suffix.lower() in (".ini", ".txt"):
        # a preset's own file: where it sits is what's wanted
        leaf, spelled = spelled[-1], spelled[:-1]
    members = [[p.lower() for p in _spelled(m)] for m in sizes if m not in taken]
    folders = {tuple(parts[:n]) for parts in members for n in range(1, len(parts))}
    holds = lambda folder: not leaf or list(folder) + [leaf.lower()] in members
    if not re.match(r"^([a-zA-Z]:|[\\/]{2})", entry.strip()):
        parts = list(home)
        for part in spelled:
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            else:
                parts.append(part.lower())
        if (tuple(parts) not in folders and parts != home) or not holds(parts):
            return None
        inside = parts[:len(home)] == home
        # out and back in again still means the build's folder, which
        # ReShade's own folder is from now on, so it's written from there
        tail = spelled[len(spelled) - (len(parts) - len(home)):] if inside else \
            [p for p in spelled if p != ".."]
        return parts, tail, leaf, ".." in spelled
    if leaf and holds(home):
        return list(home), [], leaf, True
    nearest = lambda f: (list(f[:len(home)]) != home, len(f), f)
    for n in range(len(spelled) - 1, 0, -1):
        tail = spelled[-n:]
        for folder in sorted(folders, key=nearest):
            if list(folder[-n:]) == [p.lower() for p in tail] and holds(folder):
                if list(folder[:len(home)]) == home:
                    # from the build, as the drop spells it, rather than as the path did
                    tail = next(_spelled(m)[len(home):len(folder)] for m in sizes
                                if [p.lower() for p in _spelled(m)[:len(folder)]] == list(folder))
                return list(folder), tail, leaf, True
    return None


def _rewrite_paths(raw: bytes, rewritten: dict) -> bytes:
    """ReShade's settings with the paths that moved pointed where they went, nothing else touched"""
    text = raw.decode("utf-8", "surrogateescape")

    def line(match):
        entries = match.group(3).split(",")
        for i, entry in enumerate(entries):
            bare = entry.strip().strip('"')
            if bare in rewritten:
                entries[i] = rewritten[bare]
        return match.group(1) + ",".join(entries) + match.group(4)

    return SEARCH_LINE.sub(line, text).encode("utf-8", "surrogateescape")


def _claim(found: Look) -> None:
    """
    what goes beside the exe with each loader: everything it sits among, as it
    is around it in the drop, which is how these mods say to install them. HD
    Fonts and GUI is version.dll with hd_textures and hd_textures_shaders
    beside it. Not the readme, and never a game file, an installer, or a
    folder holding either: those are the mod's other parts. A 64-bit build's
    own files are that build's by the same rule, and stay out with it
    """
    from patcher.mods.loader_mods import CONFIG, NOT_DATA, PROXY_NAMES

    # a game file the archives don't list is still a game file: another
    # game's, or one this can't place, and beside the exe it does nothing.
    # ReShade's are its own
    shade = reshade_side(found)
    taken, busy = _busy(found)
    taken |= shade
    # ReShade's folders aren't a loader's data, but for the one a build sits
    # in among other mods' code, scripts\ beside an ASI loader, which is that
    # loader's still: ReShade's own files in it are all that stay out
    homes = {tuple(p.lower() for p in _spelled(m)[:-1]) for m in found.reshade}
    shared = {tuple(p.lower() for p in _spelled(m)[:-1]) for m in found.code + found.loaders}
    for member in shade:
        parts = [p.lower() for p in _spelled(member)]
        busy.update("/".join(parts[:n]) for n in range(1, len(parts))
                    if not (tuple(parts[:n]) in homes and tuple(parts[:n]) in shared))

    def beside(dll):
        """(member, its path beside the exe) for what goes with `dll`, itself included"""
        home = [p.lower() for p in _spelled(dll)[:-1]]
        for member in found.sizes:
            if member != dll and (member in taken or member in found.loaders):
                continue
            parts = _spelled(member)
            if [p.lower() for p in parts[:len(home)]] != home or len(parts) <= len(home):
                continue
            rest = parts[len(home):]
            if len(rest) == 1:
                # beside it: its config, and a .dll or .asi it brought along
                if not rest[0].lower().endswith(CONFIG + CODE):
                    continue
            elif "/".join(p.lower() for p in parts[:len(home) + 1]) in busy \
                    or rest[0].lower() in NOT_DATA:
                continue
            yield member, "/".join(rest)

    for dll in found.loaders:
        for member, rest in beside(dll):
            found.loader_files.setdefault(member, rest)
    homes = [[p.lower() for p in _spelled(dll)[:-1]] for dll in found.loaders]

    def stay_out(dlls, into):
        """what goes with builds that don't go in, so it doesn't go anywhere else either"""
        for dll in dlls:
            depth = len(_spelled(dll)) - 1
            for member, _ in beside(dll):
                parts = [p.lower() for p in _spelled(member)]
                # an x86 folder inside this one's is the x86 build's
                if member == dll or member in found.loader_files \
                        or any(len(h) > depth and parts[:len(h)] == h for h in homes):
                    continue
                if member not in into:
                    into.append(member)

    stay_out(found.x64, found.x64_files)
    # a proxy Windows Security took is still a loader, and its plugins and data
    # are as much its own as they'd have been
    stay_out([m for m in found.blocked if _spelled(m)[-1].lower() in PROXY_NAMES],
             found.blocked_files)
    claimed = set(found.loader_files) | set(found.x64_files) | set(found.blocked_files)
    for part in ("art", "code"):
        setattr(found, part, [m for m in getattr(found, part) if m not in claimed])


def _distinct(members: list, sizes: dict, read) -> list:
    """the members that aren't a byte for byte copy of one before them"""
    kept, digests = [], {}

    def digest(member):
        if member not in digests:
            digests[member] = hashlib.sha256(read(member)).digest()
        return digests[member]

    for member in members:
        same = [m for m in kept if sizes[m] == sizes[member]]
        if same and read is not None and any(digest(m) == digest(member) for m in same):
            continue
        kept.append(member)
    return kept


def _versions(clashes: dict, homes: dict, root: str = "") -> tuple:
    """
    (the versions a mod's clashes split it into, version -> the folder it
    means). Every clash has to be one file from each: "Option A/chr/..."
    against "Option B/chr/...", or the mod's own files against a folder of
    options inside it, which then is a version laid over those. Two files for
    one place in one folder doesn't split, and there's nothing to offer
    """
    clashing = [m for those in clashes.values() for m in those]
    prefixes = [homes[m] for m in clashing]
    common = 0
    while all(len(p) > common for p in prefixes) and len({p[common] for p in prefixes}) == 1:
        common += 1
    roots = {m: tuple(p[:common + 1]) for m, p in zip(clashing, prefixes)}
    if any(len({roots[m] for m in those}) != len(those) for those in clashes.values()):
        return [], {}
    spelled = {}
    for member in clashing:
        original = [p for p in member.replace("\\", "/").split("/") if p not in ("", ".")]
        spelled.setdefault(roots[member], "/".join(original[:len(roots[member])]))
    keys = {}
    for folder, name in spelled.items():
        if not name:
            # the drop's own top-level files, named after it
            name = root or "Main files"
            if any(_key(name) == _key(other) for other in spelled.values()):
                name += " (main files)"
        keys[name] = "/".join(folder)
    return sorted(keys, key=str.lower), keys


# a menu's bullet or a quote round a label is how it was printed, not its name
LABEL_EDGES = re.compile(r"^[\s\-\u2010-\u2015\u2022*.:>\"']+|[\s\-\u2010-\u2015\u2022*.:\"']+$")


def label_key(text) -> str:
    """a version's name the way two of them are compared: no bullets, \\r, case or doubled spaces"""
    text = unicodedata.normalize("NFKC", str(text or ""))
    return LABEL_EDGES.sub("", re.sub(r"\s+", " ", text)).casefold()


def picked(version, names):
    """
    which of `names` a version picked off the drawer means, or None. Exact
    first, then the one name that reads the same tidied, so "- LG Catalog Drop
    Rates" from a menu still finds itself after a trip through the UI and a
    second read. Two that tidy the same are a real choice and never guessed
    """
    if version is None:
        return None
    names = list(names)
    if version in names:
        return version
    want = label_key(version)
    same = [n for n in names if label_key(n) == want]
    return same[0] if len(same) == 1 else None


def nova_config(source: "Source") -> dict:
    """
    a Nova Chrysalia modpack's modconfig.ini, at its top or one folder down:
    {"name", "game", "root", "external"} with game as the data folder it's
    for, root the folder the pack's in ("" at the top) and external whether
    Nova runs its External installer. {} for any other drop. Its folders say
    the rest, EN-Data and JP-Data for one voice set each
    """
    import configparser

    for member, size in source.members():
        parts = _parts(member)
        if not parts or parts[-1] != "modconfig.ini" or len(parts) > 2 or size > 1 << 16:
            continue
        config = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            raw = source.read(member)
            # Nova writes it UTF-16 with a BOM, which read as UTF-8 has no
            # [ModPackConfig] in it at all
            text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
                else raw.decode("utf-8-sig", errors="replace")
            config.read_string(text)
        except (configparser.Error, FilesError, OSError, UnicodeDecodeError):
            return {}
        if not config.has_section("ModPackConfig"):
            return {}
        pack = config["ModPackConfig"]
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", pack.get("Name", "")).strip()[:80]
        flags = config["NovaChysaliaConfig"] if config.has_section("NovaChysaliaConfig") else {}
        return {"name": "" if name.lower() == "default" else name,
                "game": NOVA_GAMES.get(pack.get("GameEntry", "").strip(), ""),
                "root": member.replace("\\", "/").rsplit("/", 1)[0] if len(parts) > 1 else "",
                # Nova runs it on ExtPatch = true alone, but a pack made by hand
                # often leaves the flags out, and then its Data goes in here
                # anyway. Only one that says false is taken at its word
                "external": flags.get("ExtPatch", "").strip().lower() != "false"}
    return {}


def wrong_game(found: Look, game: Game) -> str:
    """the other game a drop's data folders say it's for, when nothing in it is for this one"""
    if not found.homes or found.picks or found.clashes:
        return ""
    return DATA_DIRS[found.homes.most_common(1)[0][0]]


def _records(store) -> Path:
    return Path(store) / RECORDS


def records(store) -> list:
    """every file mod on record for this game, oldest first"""
    folder = _records(store)
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("name") and isinstance(record.get("pairs"),
                                                                           list):
            record["_file"] = path
            found.append(record)
    return sorted(found, key=lambda r: (r.get("at", 0), r["_file"].name))


def _rid(record: dict) -> str:
    return record.get("id") or Path(record["_file"]).stem


def _fresh(store, record: dict):
    """the record as it is on disk now, or None when it's gone"""
    path = Path(record["_file"])
    try:
        fresh = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(fresh, dict) or not fresh.get("name") or not isinstance(
            fresh.get("pairs"), list):
        return None
    fresh["_file"] = path
    return fresh


def _write_record(record: dict) -> None:
    from patcher.fileops import write_atomic

    body = {k: v for k, v in record.items() if not k.startswith("_")}
    write_atomic(record["_file"], json.dumps(body, indent=1).encode("utf-8"), durable=True)


def _forget(record: dict) -> None:
    from patcher.fileops import unlink

    folder = Path(record["_file"]).parent
    for spec in record.get("pairs", []):
        if spec.get("before"):
            unlink(folder / spec["before"])
    unlink(Path(record["_file"]))


def _settled_records(touched: dict) -> None:
    """records changed on another's behalf: written, or gone once they hold nothing"""
    for record in touched.values():
        if any(spec["entries"] for spec in record["pairs"]):
            _write_record(record)
        else:
            _forget(record)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")[:48] or "mod"


def _stored(plain: bytes, original) -> bytes:
    """
    compressed when the original was, so the game reads it the way it read
    what was there. Raw when deflate doesn't win, since equal sizes is how an
    index says raw
    """
    if not original.is_compressed:
        return plain
    packed = zlib.compress(plain, 6)
    return packed if len(packed) < len(plain) else plain


def _sha_at(handle, entry) -> str:
    from patcher.mods.hd_zones import _record

    try:
        return hashlib.sha256(_record(handle, entry)).hexdigest()
    except (OSError, ValueError, zlib.error):
        return ""


# what entries hold, kept against the archive as it was when read
_contents, _contents_lock = {}, threading.Lock()
# and beside the records, so the first look after the app starts doesn't read
# every drawer mod's files again: seconds on an SSD for a couple of big packs,
# far more on a hard drive. `_generation` moves with every change to
# _contents, which is how a write knows there's anything new to keep
CONTENTS = "contents.cache"
_generation = 0
_recalled, _kept = set(), {}


def _where(archive) -> str:
    """one spelling per file, however the caller came by its path"""
    return os.path.normcase(os.path.abspath(archive))


def _stamp(archive: Path) -> tuple:
    """
    which file an archive is and how it was last left. A rename over it is a
    new file, so a restore's copy with the backup's mtime doesn't pass for it
    """
    seen = Path(archive).stat()
    return seen.st_dev, seen.st_ino, seen.st_size, seen.st_mtime_ns


def forget_contents(archive) -> None:
    """
    for anything that writes inside an archive without changing its size,
    which a drive with 2 second timestamps may not even date. Off the disk too,
    or the next start would read them back under the same stamp
    """
    global _generation
    with _contents_lock:
        for key in [k for k in _contents if k[0] == _where(archive)]:
            del _contents[key]
        _generation += 1
    from patcher.paths import undo_store_for

    for parent in list(Path(_where(archive)).parents)[:6]:
        kept = _records(undo_store_for(parent)) / CONTENTS
        if kept.is_file():
            try:
                kept.unlink()
            except OSError:
                pass
            with _contents_lock:
                _kept.pop(str(kept), None)


def _recall(install_dir: Path, store: Path) -> None:
    """
    what an earlier run hashed in this game, once per run, for each archive
    that's still the file it was and as it was left: the same _stamp(). A
    file any write has reached since reads as new, which is the same rule the
    hashes in memory go by
    """
    kept = _records(store) / CONTENTS
    with _contents_lock:
        if str(kept) in _recalled:
            return
        _recalled.add(str(kept))
    try:
        saved = json.loads(kept.read_text(encoding="utf-8"))
        archives = saved["archives"] if saved.get("version") == 1 else {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return
    found = {}
    for rel, part in archives.items() if isinstance(archives, dict) else ():
        try:
            archive = Path(install_dir) / rel
            stamp = _stamp(archive)
            if list(stamp) != part["stamp"]:
                continue
            where = _where(archive)
            for fields, sha in part["shas"].items():
                if isinstance(sha, str):
                    found[(where, stamp, tuple(int(n) for n in fields.split(",")))] = sha
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    with _contents_lock:
        for key, sha in found.items():
            _contents.setdefault(key, sha)


def keep_contents(install_dir, store) -> None:
    """
    the hashes of this game's archives, written beside its records for the
    next run when there's anything new. Only archives as they are now, and
    only for a game with drawer mods. Best effort: losing it costs one slow
    look, not a wrong one
    """
    from patcher.fileops import write_atomic

    folder = _records(store)
    kept = folder / CONTENTS
    with _contents_lock:
        if _kept.get(str(kept)) == _generation:
            return
        generation, items = _generation, list(_contents.items())
    if not folder.is_dir() or not any(folder.glob("*.json")):
        return
    root = _where(install_dir)
    stamps, archives = {}, {}
    for (where, stamp, fields), sha in items:
        if not where.startswith(root + os.sep) or not sha:
            continue
        if where not in stamps:
            try:
                stamps[where] = _stamp(Path(where))
            except OSError:
                stamps[where] = None
        if stamps[where] != stamp:
            continue
        part = archives.setdefault(os.path.relpath(where, root),
                                   {"stamp": list(stamp), "shas": {}})
        part["shas"][",".join(str(n) for n in fields)] = sha
    try:
        write_atomic(kept, json.dumps({"version": 1, "archives": archives}).encode("utf-8"))
    except OSError:
        return
    with _contents_lock:
        _kept[str(kept)] = generation


def _content(archive: Path, handle, entry) -> str:
    """
    sha256 of what an entry holds, plain. STUB for a placeholder the build
    left empty, "" when it won't read. Remembered against the archive's
    _stamp(), which any write to it changes
    """
    if entry.is_stub:
        return STUB
    global _generation
    key = (_where(archive), _stamp(archive), entry.raw_fields)
    with _contents_lock:
        known = _contents.get(key)
    if known is None:
        known = _sha_at(handle, entry)
        with _contents_lock:
            _contents[key] = known
            _generation += 1
            while len(_contents) > 65536:
                _contents.pop(next(iter(_contents)))
    return known


def _carry(archive: Path, before, keep: int) -> None:
    """
    what entries hold, carried over a write of our own that left the archive
    as it was up to `keep`: an append, or a cut back. Otherwise every install
    has the next look hash every mod in the drawer all over again. What's past
    `keep` is dropped rather than left under the old stamp, which a drive with
    coarse timestamps can hand out again
    """
    global _generation
    try:
        after = _stamp(archive)
    except OSError:
        return
    with _contents_lock:
        _generation += 1
        for key in [k for k in _contents if k[:2] == (_where(archive), before)]:
            sha = _contents.pop(key)
            if key[2][0] * 2048 + key[2][2] <= keep:
                _contents[(key[0], after, key[2])] = sha


def _stacks(everything: list) -> dict:
    """
    (index, path) -> [(record, spec, entry)] bottom to top, each entry on the
    one its `below` names, from the game's own up. Which mod is on which is
    kept as names rather than places, since a repack moves every place. Two on
    one is something that died between two writes: an update, both versions
    listing the file it took over, where the newest counts and what sat on the
    other sits on it, or a Remove, where what sat on the one coming off was
    handed down first and says so in `from`
    """
    on = [r for r in everything if not r.get("off")]
    ids = {_rid(r) for r in on}
    by = {}
    for record in on:
        for spec in record["pairs"]:
            for item in spec["entries"]:
                if not item.get("lost"):
                    by.setdefault((spec["index"], item["path"]), []).append((record, spec, item))
    out = {}
    for key, rest in by.items():
        chain, below = [], {""}
        while rest:
            # a bottom whose own bottom was taken off by hand starts it too
            nxt = [t for t in rest if (t[2].get("below") or "") in below
                   or (not chain and (t[2].get("below") or "") not in ids)]
            if not nxt:
                break
            handed = {t[2].get("from") for t in nxt}
            chain.append(([t for t in nxt if _rid(t[0]) not in handed] or nxt)[-1])
            rest = [t for t in rest if not any(t is n for n in nxt)]
            below = {_rid(t[0]) for t in nxt}
        out[key] = chain
    return out


class _View:
    """the records and the game as they are, read once for one decision"""

    def __init__(self, install_dir, store, everything=None):
        self.install_dir = Path(install_dir)
        self.store = Path(store)
        _recall(self.install_dir, self.store)
        self.everything = records(store) if everything is None else everything
        self.stacks = _stacks(self.everything)
        self._books, self._handles, self._sources, self._at = {}, {}, {}, None
        self.models = {}

    def at(self, spec: dict, path: str) -> list:
        """(record, entry) for every entry on record for one path, kept-off ones included"""
        if self._at is None:
            # once per view, not once per file: a texture pack coming off
            # entry by entry asks for thousands of paths
            self._at = {}
            for record in self.everything:
                for theirs in record["pairs"]:
                    for item in theirs["entries"]:
                        self._at.setdefault((theirs["index"], item["path"]), []).append(
                            (record, item))
        return self._at.get((spec["index"], path), [])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for handle in self._handles.values():
            if handle is not None:
                handle.close()
        for source in self._sources.values():
            if source is not None:
                source.close()
        self._handles.clear()
        self._sources.clear()

    def source(self, where: str):
        """a mod's own download, opened once per view, or None once it's gone"""
        if where not in self._sources:
            try:
                self._sources[where] = Source(where) if where else None
            except Exception:
                self._sources[where] = None
        return self._sources[where]

    def entries(self, index_name: str):
        if index_name not in self._books:
            try:
                self._books[index_name] = entries(self.install_dir / index_name)
            except Exception:
                self._books[index_name] = None
        return self._books[index_name]

    def handle(self, archive: Path):
        archive = Path(archive)
        if archive not in self._handles:
            try:
                self._handles[archive] = archive.open("rb")
            except OSError:
                self._handles[archive] = None
        return self._handles[archive]

    def forget(self, archive: Path) -> None:
        """an archive about to be written to, so the next read sees it as it is then"""
        handle = self._handles.pop(Path(archive), None)
        if handle is not None:
            handle.close()

    def content(self, archive: Path, entry) -> str:
        handle = self.handle(archive)
        return "" if handle is None else _content(Path(archive), handle, entry)

    def live(self, spec: dict, path: str):
        """(entry, what it holds) for a path in the game now, (None, "") without one"""
        listed = self.entries(spec["index"])
        entry = None if listed is None else listed.get(path)
        if entry is None:
            return None, ""
        return entry, self.content(self.install_dir / spec["archive"], entry)

    def chain(self, spec: dict, path: str) -> list:
        return self.stacks.get((spec["index"], path), [])

    def top(self, spec: dict, path: str):
        """
        where in its stack the file the game holds is, highest first, or None
        when it's nobody's. Anything above that is a mod no longer in there: a
        restore took it, or taking it off stopped half way through
        """
        chain = self.chain(spec, path)
        if not chain:
            return None
        held = self.live(spec, path)[1]
        return next((i for i in range(len(chain) - 1, -1, -1) if chain[i][2]["sha"] == held),
                    None)

    def holding(self, index_name: str) -> dict:
        """
        path -> (record, spec, entry) for every path in one index holding a
        drawer mod's file now, wherever that sits
        """
        out = {}
        for (name, path), chain in self.stacks.items():
            if name != index_name or not chain:
                continue
            at = self.top(chain[-1][1], path)
            if at is not None:
                out[path] = chain[at]
        return out


def _entry_at(path: str, fields):
    from patcher.archive.entry import ChunkEntry

    return ChunkEntry(path=path, sector_offset=fields[0], uncompressed=fields[1],
                      stored=fields[2])


def _went_under(item: dict, sha: str, was) -> None:
    """
    what an entry covers handed down, with what it covered before kept as a
    place to look: a restore can bring back an archive with that still in it
    """
    if sha != item["under"]:
        item["past"] = [[item["under"], item["was"]]] + [
            p for p in item.get("past", []) if p[0] != item["under"]][:3]
    item["under"], item["was"] = sha, was


def _moved(item: dict, fields) -> None:
    """
    a file's new place, with a few of the old ones kept: a restore puts back
    an archive laid out the way it was when the backup was taken
    """
    fields = list(fields)
    if fields == item["now"]:
        return
    item["seen"] = [item["now"]] + [s for s in item.get("seen", [])
                                    if s not in (item["now"], fields)][:5]
    item["now"] = fields


def install(install_dir, store, name: str, found: Look, source: Source, origin: str = "",
            on_line=None, cancel=None, progress=None, extra: dict | None = None,
            others=()) -> dict:
    """
    puts what `found` picked into the game under one record called `name`. A
    mod by that name already in goes under the new one and comes off after,
    so an update that fails leaves the old version as it was. `progress(done,
    of)` hears how far it's got, out of a hundred, and `extra` goes on the
    record as it is: the version HD Models Plus was built as, for its line
    """
    from patcher.mods.hd_install import InstallError, exclusive
    from patcher.mods.meter import Meter

    meter = Meter(progress)
    install_dir, store = Path(install_dir), Path(store)
    paths = {(pair.name(install_dir), path)
             for pair, mine in _plans(read_game(install_dir), found) for path in mine}
    try:
        with exclusive(store):
            earlier, name = _going(store, name, paths, origin or str(source.path), others)
            done = _install(install_dir, store, name, found, source, origin, earlier,
                            on_line, cancel, meter, extra)
            problems, rest = [], {"files": 0, "handed": 0, "crossed": [], "left": []}
            def came_off(spec):
                meter.add("off", _off(install_dir, spec))

            if done.pop("recorded"):
                done["updated"] = bool(earlier)
                # newest first and read again each time, since taking one off
                # rewrites the records it hands things to
                for stale in reversed(earlier):
                    record = _fresh(store, stale)
                    try:
                        if record is not None:
                            gone = _take_off(install_dir, store, record, tick=came_off)
                            rest["left"] += gone["left"]
                            rest["crossed"] = sorted(set(rest["crossed"]) | set(gone["crossed"]))
                    except (FilesError, OSError) as e:
                        problems.append(str(e))
            # nothing came off, or all that will has: the rest of its share is done
            meter.at("off", 1, 1)
            # a run cut short can have marked one as missing a file and not
            # got to the rest of it, which this finishes whatever went in
            _whole(install_dir, store, rest, on_line=on_line)
            rest["left"] = _still_left(store, rest["left"])
            done["crossed"] = sorted(set(rest["crossed"]) - {name})
            if problems:
                done["note"] = f"The old version didn't all come off: {problems[0]}"
            elif rest["left"]:
                done["note"] = lost_said(*rest["left"][0])
            keep_contents(install_dir, store)
            meter.end()
            return done
    except InstallError as e:
        raise FilesError(str(e)) from None


def _going(store, name: str, paths: set, where: str, others=()) -> tuple:
    """
    (the mods on record a drop is a new version of, the name it goes in
    under). One taken off already is only kept for a restore to find, and one
    that only shares the name is another mod: two dropped as their chr folder
    are two, the second "Name (2)", which a later drop by that name sharing
    its files then updates. Sharing files with both, it's the one dropped from
    the same place, or the one it has more of. Every one by the name it picks
    goes, since two by one name is an update whose old version didn't all
    come off, and what's left of it may share nothing with this one
    """
    # a name off a Windows file, where Pack.zip and pack.zip are one file
    from patcher.addon_sieve import stamped

    kin = re.compile(re.escape(name) + r"( \(\d+\))?", re.I)
    # one that went in before names lost Nexus's id, version and time is
    # still this mod, and its update takes the plain name
    live = [r for r in records(store) if not r.get("off") and (
        kin.fullmatch(r["name"]) or kin.fullmatch(stamped(r["name"])[0].strip(" .-")))]
    shared = {}
    for record in live:
        mine = paths & {(spec["index"], item["path"])
                        for spec in record["pairs"] for item in spec["entries"]}
        if mine:
            shared.setdefault(record["name"], []).append((record, mine))
    if not shared:
        # `others` is names another kind of mod has, lowercased, which isn't this one
        taken = any(r["name"].lower() == name.lower() for r in live) or name.lower() in others
        return [], _free_name(store, name, others) if taken else name

    def likely(group):
        those = shared[group]
        return (any(r.get("source") == where for r, _ in those),
                len(set().union(*(mine for _, mine in those))),
                max(r.get("at", 0) for r, _ in those))
    group = max(shared, key=likely)
    return [r for r in live if r["name"] == group], name if stamped(group)[0] != group else group


def _free_name(store, name: str, others=()) -> str:
    """the name with a number after it, for another mod that happens to share it"""
    taken = {r["name"].lower() for r in records(store)} | set(others)
    n = 2
    while f"{name} ({n})".lower() in taken:
        n += 1
    return f"{name} ({n})"


def _carried(view: "_View", install_dir: Path, plans: list, going: set, game: Game,
             found: Look, source: Source) -> dict:
    """
    pair -> [(record, spec, entry, whether the game holds it)] of the version
    going out to keep in the new one: the rest of a model the new one only
    ships part of, the way unpacking an update over the old one would leave
    it. Under another mod's file too, so taking that one off later finds this
    one's. A file of the model the drop has somewhere it isn't going in from
    (another version, the other voice set, two copies, empty) is half a model
    unless the version going out has that very file, and that's refused
    before anything moves
    """
    carried = {}
    for pair, mine in plans:
        spec = {"index": pair.name(install_dir),
                "archive": pair.archive.relative_to(install_dir).as_posix()}
        listed = view.entries(spec["index"]) or {}
        for path in sorted(mine):
            for other in sorted(game.stems.get(_stem(path), ())):
                if other in mine or other not in listed:
                    continue
                chain, at = view.chain(spec, other), view.top(spec, other)
                # anything above the file the game holds isn't in there any more
                old = next((chain[i] + (i == at,) for i in range(at, -1, -1)
                            if _rid(chain[i][0]) in going), None) if at is not None else None
                if other in found.matched and (old is None or not _drop_has(
                        source, found.members_at.get(other, ()), old[2]["sha"])):
                    raise FilesError(_half_said(found, path, other, pair.voice))
                if old is not None and not any(t[2] is old[2] for t in carried.get(pair, [])):
                    carried.setdefault(pair, []).append(old)
    return carried


def _also(ways: list) -> list:
    """
    up to three other (download, member) a file can be read back out of,
    newest first, leaving out a .7z or .rar, which would mean unpacking again
    """
    kept = []
    for way in ways:
        if way[0] and list(way) not in kept and not str(way[0]).lower().endswith((".7z", ".rar")):
            kept.append(list(way))
    return kept[:3]


def _drop_has(source: Source, members, sha: str) -> bool:
    """whether one of these members of the drop is the file hashing to sha"""
    for member in members:
        try:
            if source.has(member) and hashlib.sha256(source.read(member)).hexdigest() == sha:
                return True
        except (OSError, KeyError, zipfile.BadZipFile):
            continue
    return False


def _half_said(found: Look, path: str, other: str, voice: str) -> str:
    """why half a model doesn't go in, and what does"""
    from patcher.mods.hd_mirror import LANGUAGE

    leaf, missing = path.rsplit("/", 1)[-1], other.rsplit("/", 1)[-1]
    there = found.members_at.get(other, [])
    # the copies meant for this voice set, or for either
    mine = [m for m in there if found.voices.get(m, "") in ("", voice)]
    if mine and all(found.sizes.get(m) == 0 for m in mine):
        return (f"its {missing} is empty, 0 bytes, so {leaf} can't go in without it. That "
                "usually means the download or the unpack didn't finish. Nothing has been "
                "changed.")
    if any(m in found.doubled for m in mine):
        return (f"its zip lists {missing} twice with different contents, and {leaf} can't go "
                "in without knowing which. Nothing has been changed.")
    if there and not mine:
        return (f"its {missing} is only for the {LANGUAGE[found.voices[there[0]]]} voice set, so "
                f"{leaf} can't go into the {LANGUAGE[voice]} one without it: a model's files "
                "have to come from the same place. Nothing has been changed.")
    return (f"{leaf} would go in without the {missing} it has, which is in another version. A "
            "model's files have to come from the same place, so nothing has been changed. If "
            "this version goes on top of another one, install that one first, then drop it "
            "again and pick this one.")


def _plans(game: Game, found: Look) -> list:
    """
    (pair, path -> member) for every pair something goes into, the file meant
    for a pair's own voice set over one meant for any
    """
    plans = []
    for pair in game.pairs:
        if found.only and pair.name(game.install_dir) not in found.only:
            continue
        listed = entries(pair.index)
        name = pair.name(game.install_dir)
        renamed = found.renames.get(name, {})
        mine = {path: member for (path, voice), member in found.picks.items()
                if (path in listed or path in renamed)
                and (voice == pair.voice or (not voice and (path, pair.voice) not in found.picks))
                and name in found.pairs_for.get(path, (name,))}
        if mine:
            plans.append((pair, mine))
    return plans


def _entry_for(pair: Pair, path: str, found: Look, install_dir=None):
    """the record a path goes onto: the one a rename gives it to, or its own"""
    renamed = found.renames.get(pair.name(install_dir), {}) \
        if found.renames and install_dir is not None else {}
    if path in renamed:
        return coded(pair.index)[renamed[path][0]]
    return entries(pair.index)[path]


def _ready(pairs_in: list) -> None:
    """refused before anything moves: a folder that won't take writes, or an archive held open"""
    from patcher.access import writable_or_reason
    from patcher.mods.hd_install import in_use

    if not pairs_in:
        return
    refusal = writable_or_reason(pairs_in[0].index.parent)
    if refusal:
        raise FilesError(refusal)
    held = in_use([p for pair in pairs_in for p in (pair.index, pair.archive)])
    if held:
        raise FilesError(f"{held[0][0]} can't be opened for writing, which usually means the game "
                         "is running or Steam is verifying it. Close both and try again. Nothing "
                         "has been changed.")


def _preflight(install_dir: Path, plans: list, found: Look, tick=None) -> dict:
    """
    what refuses before a byte is written, and pair -> (raw, book) when
    nothing does. `tick(pair)` once each index has been read and checked
    """
    from patcher.archive.filelist import parse
    from patcher.download import free_space
    from patcher.mods.hd_mirror import SLACK, _index_problem

    if not plans:
        raise FilesError("none of its files are in this game's archives, so nothing has been "
                         "changed.")
    _ready([pair for pair, _ in plans])
    # every file, unchanged ones too: a same-size recolor pack looks
    # unchanged by size, and finding out by filling the drive is worse than
    # an update asking for room it ends up not using
    need = sum(found.sizes[m] + 4096 for _, mine in plans for m in mine.values()) + SLACK
    free = free_space(install_dir)
    if free < need:
        raise FilesError(f"it needs {need / 1024 ** 2:.0f} MB free on the game's drive and there's "
                         f"{free / 1024 ** 2:.0f} MB. Nothing has been changed.")
    books = {}
    for pair, _ in plans:
        raw = pair.index.read_bytes()
        book = parse(raw)
        problem = _index_problem(book, raw)
        if problem:
            raise FilesError(f"{pair.index.name} can't be written back exactly ({problem}), so "
                             "nothing has been changed.")
        books[pair] = (raw, book)
        if tick is not None:
            tick(pair)
    return books


def _what_is_there(view: _View, plans: list, found: Look, stop, tick) -> dict:
    """
    (pair, path) -> (entry, what it holds) for every file about to be covered,
    read in archive order: a texture pack is thousands of reads scattered over
    gigabytes, which in the mod's order is a hard drive seeking for minutes.
    `tick((pair, path))` after each
    """
    out = {}
    for pair, mine in plans:
        at = {path: _entry_for(pair, path, found, view.install_dir) for path in mine}
        for path in sorted(mine, key=lambda p: at[p].byte_offset):
            stop()
            out[(pair, path)] = (at[path], view.content(pair.archive, at[path]))
            tick((pair, path))
    return out


# what a byte costs in each pass of an install, against reading one off the
# disk and hashing it. Writing one costs about the same, most of it when the
# archive is flushed after the last file, and reading back one that was just
# written, still in memory, about half. Whatever the game had compressed is
# deflated going in, thirty times slower than sha256, and the disk takes some
# of that back
DEFLATED, WRITTEN, CHECKED = 8, 1, 0.5
# and what a file costs before its bytes, as bytes: the lookups and records
# each one gets, which is most of a pack of small files. Twice that going in,
# and as much again on the record beside the index, an eighth to look up one
# that's hashed already, a quarter to read one back, and twice for each file
# an update takes off
FILE = 1 << 16
TAKEN = 2 * FILE
# each of an index's entries, parsed and checked before anything's written and
# again read back after, half that rebuilt: XIII-2's 14,000 are a fifth of a
# second each way here, which is most of a small mod's install
INDEX = 3 << 10


def weigh(install_dir, found: Look) -> float:
    """what install() costs, in _costs' units, for a caller putting more in after it"""
    parts = _costs(_plans(read_game(install_dir), found), found, install_dir)
    return sum(sum(each.values()) for each in parts.values())


def _off(install_dir, spec: dict) -> float:
    """what taking one voice set of a version off costs: its files, and its index rewritten"""
    index = Path(install_dir) / spec["index"]
    return TAKEN * len(spec["entries"]) + (INDEX * len(entries(index)) if index.is_file() else 0)


def _costs(plans: list, found: Look, install_dir=None) -> dict:
    """
    part -> what each piece of it costs: by pair for an index, by (pair,
    path) for a file but for putting it in, which is by member, and the read
    back of a whole index by (pair, None). Reading what's there costs next to
    nothing for anything hashed already, which is every file under a mod the
    drawer has looked at, or an update's line would leap at the start and crawl
    the rest of the way
    """
    parts = {part: {} for part in ("setup", "look", "write", "flush", "commit", "check")}
    for pair, mine in plans:
        listed = entries(pair.index)
        where, stamp = _where(pair.archive), _stamp(pair.archive)
        parts["setup"][pair] = INDEX * len(listed)
        parts["commit"][pair] = INDEX / 2 * len(listed) + FILE * len(mine)
        parts["check"][(pair, None)] = INDEX * len(listed)
        for path, member in mine.items():
            entry, size, key = (_entry_for(pair, path, found, install_dir), found.sizes[member],
                                (pair, path))
            with _contents_lock:
                known = entry.is_stub or (where, stamp, entry.raw_fields) in _contents
            parts["look"][key] = FILE / 8 if known else entry.uncompressed + FILE
            # read out of the drop and hashed once, whichever voice sets it goes into
            parts["write"][member] = (parts["write"].get(member, size)
                                      + size * DEFLATED * entry.is_compressed + 2 * FILE)
            parts["flush"][key] = size * WRITTEN
            parts["check"][key] = size * CHECKED + FILE / 4
    return parts


def _install(install_dir: Path, store: Path, name: str, found: Look, source: Source,
             origin: str, earlier: list, on_line, cancel, meter, extra=None) -> dict:
    from patcher.archive.filelist import rebuild, relocate_many, rename_records
    from patcher.fileops import write_atomic
    from patcher.mods.hd_mirror import _Tail
    from patcher.mods.hd_runner import Cancelled

    def stop():
        if cancel is not None and cancel.is_set():
            raise Cancelled("stopped before the indexes changed")

    game = read_game(install_dir)
    plans = _plans(game, found)
    # weighed with what an earlier run hashed in hand, or the first install
    # after a start weighs a read of the game it won't make
    _recall(install_dir, store)
    costs = _costs(plans, found, install_dir)
    meter.split(**{part: sum(each.values()) for part, each in costs.items()},
                off=sum(_off(install_dir, spec) for r in earlier for spec in r["pairs"]))

    def did(part, key):
        meter.add(part, costs[part].get(key, 0))

    books = _preflight(install_dir, plans, found, lambda pair: did("setup", pair))
    stamps = {pair: _stamp(pair.archive) for pair, _ in plans}
    going = {_rid(r) for r in earlier}
    by_member = {}
    for pair, mine in plans:
        for path, member in mine.items():
            by_member.setdefault(member, []).append((pair, path))

    moved = {pair: {} for pair, _ in plans}
    items = {pair: [] for pair, _ in plans}
    # code -> path, for each record given its name back on the way in
    renaming = {pair: {} for pair, _ in plans}
    tails, swapped, wrote, same, adopted, gave, stacked = {}, [], False, set(), [], [], 0
    crossed, repoint, leaving = {}, [], []
    stamp = time.time()
    stem = f"{int(stamp * 1000)}-{_slug(name)}"
    folder = _records(store)
    record = {**(extra or {}), "name": name, "id": stem, "source": origin or str(source.path),
              "at": stamp, "pairs": [], "_file": folder / f"{stem}.json"}
    # a FOMOD's pick reads under where it goes; the download has it under its own name
    kept_as = getattr(source, "real", lambda member: member)
    try:
        with _View(install_dir, store) as view:
            everything = view.everything
            there = _what_is_there(view, plans, found, stop, lambda key: did("look", key))
            carried = _carried(view, install_dir, plans, going, game, found, source)
            for n, member in enumerate(sorted(by_member, key=str.lower), start=1):
                stop()
                plain = source.read(member)
                # nothing at all is a placeholder, which is what the index
                # says of one: a mod putting back a cut model's empty .imgb
                digest = hashlib.sha256(plain).hexdigest() if plain else STUB
                for pair, path in by_member[member]:
                    entry, held = there[(pair, path)]
                    renamed = found.renames.get(pair.name(install_dir), {}).get(path)
                    chain = view.chain({"index": pair.name(install_dir)}, path)
                    at = next((i for i in range(len(chain) - 1, -1, -1)
                               if chain[i][2]["sha"] == held), None)
                    # a mod on record above whose file the game holds isn't in
                    # here any more, left by a restore or a Remove cut short.
                    # Crossed off, so this one goes on what's really there
                    # and the stack stays one stack
                    for gone, gone_spec, gone_item in reversed(chain[0 if at is None else at + 1:]):
                        more, _ = _splice(view, gone, gone_spec, [gone_item])
                        crossed.update(more)
                        gone_item["lost"] = True
                        crossed[_rid(gone)] = gone
                    top = None if at is None else chain[at]
                    item = {"path": path, "member": kept_as(member), "sha": digest,
                            "size": len(plain),
                            "below": _rid(top[0]) if top else "", "under": held,
                            "was": _fields(entry), "now": _fields(entry)}
                    if renamed:
                        # what it was called, so taking it off calls it that again
                        item["renamed"] = [renamed[0], renamed[1]]
                    # the version going out has this very file in, held or
                    # under another mod: it changes hands where it sits rather
                    # than going on top again
                    old = None if at is None else next(
                        (chain[i] for i in range(at, -1, -1)
                         if _rid(chain[i][0]) in going and chain[i][2]["sha"] == digest), None)
                    if old is not None:
                        mine = dict(old[2], member=kept_as(member))
                        # out of this drop now, and out of the one it came from
                        # before, which may be the only one left that reads
                        was = [mine.pop("source", None) or old[0].get("source", ""),
                               old[2].get("member", "")]
                        mine["also"] = _also([was] + old[2].get("also", []))
                        if old is top:
                            _moved(mine, _fields(entry))
                        items[pair].append(mine)
                        adopted.append((old[1], old[2]))
                        # and whatever sat on it sits on this one, or taking
                        # the old one off would hand it what was under
                        repoint += [(other, theirs, theirs["below"]) for other, theirs in _sitting(
                            view, {"index": pair.name(install_dir)}, path, old[0], old[2])]
                        continue
                    if held == digest and top is None and not renamed:
                        # the game's own already, and nobody's mod
                        same.add(path)
                        continue
                    if renamed:
                        renaming[pair][renamed[0]] = path
                    if held != digest:
                        blob = _stored(plain, entry) if plain else STUB_BLOB
                        if pair not in tails:
                            tails[pair] = _Tail(pair.archive, digests=False)
                        tail = tails[pair]
                        item["now"] = [tail.put(blob), len(plain), len(blob)]
                        moved[pair][path] = tuple(item["now"])
                    else:
                        # another mod has this very file in: this one goes on
                        # top of it, so taking that one off leaves this one's
                        stacked += 1
                    items[pair].append(item)
                if n % 200 == 0 and on_line:
                    on_line(f"[files] {n} of {len(by_member)} files in")
                # what didn't move has nothing to flush or read back, said now
                # rather than as a leap once the rest has been
                for pair, path in by_member[member]:
                    if path not in moved[pair]:
                        did("flush", (pair, path))
                        did("check", (pair, path))
                did("write", member)
            for pair, those in carried.items():
                spec = {"index": pair.name(install_dir),
                        "archive": pair.archive.relative_to(install_dir).as_posix()}
                for owner, theirs, item, held in those:
                    # its bytes came out of the old version's download, which
                    # is where to look if a repack drops them from under a mod
                    kept = dict(item, source=item.get("source") or owner.get("source", ""))
                    if held:
                        _moved(kept, _fields(view.live(spec, item["path"])[0]))
                    items[pair].append(kept)
                    adopted.append((theirs, item))
                    # and what sits on it, as for a file taken over above
                    repoint += [(other, it, it["below"])
                                for other, it in _sitting(view, spec, item["path"], owner, item)]
        for pair, tail in tails.items():
            tail.close()
            for path in moved[pair]:
                did("flush", (pair, path))
            _carry(pair.archive, stamps[pair], tail.size)
        # an index nothing moved in is neither rewritten nor read back
        for pair, _ in plans:
            if not moved[pair] and not renaming[pair]:
                did("commit", pair)
                did("check", (pair, None))
        whole = sum(len(spec["entries"]) for r in earlier for spec in r["pairs"])
        if not any(moved.values()) and not stacked and len(adopted) in (0, whole) \
                and not any(renaming.values()):
            # every file is what the game has already, this version's own included
            return {"ok": True, "name": name, "files": 0, "adopted": 0,
                    "same": len(same | {i["path"] for _, i in adopted}),
                    "already": bool(adopted), "sets": [], "archives": [], "recorded": False}

        folder.mkdir(parents=True, exist_ok=True)
        swaps = []
        for pair, _ in plans:
            if not items[pair]:
                continue
            raw, book = books[pair]
            size = pair.archive.stat().st_size
            spec = {"index": pair.name(install_dir),
                    "archive": pair.archive.relative_to(install_dir).as_posix(),
                    "start": tails[pair].size if pair in tails else size,
                    "end": tails[pair].at if pair in tails else size,
                    "before": "", "before_sha": "", "after_sha": "", "entries": items[pair]}
            if moved[pair] or renaming[pair]:
                # named first, so a record given its name back moves by it
                if renaming[pair]:
                    rename_records(book, renaming[pair])
                if moved[pair]:
                    relocate_many(book, moved[pair])
                new = rebuild(book)
                spec["before"] = f"{stem}.{pair.voice}{pair.kind}.bin"
                write_atomic(folder / spec["before"], raw, durable=True)
                spec["before_sha"] = hashlib.sha256(raw).hexdigest()
                # with a version going out, "before" has that one in, so it's
                # never the way back
                if not earlier:
                    spec["after_sha"] = hashlib.sha256(new).hexdigest()
                swaps.append((pair, raw, new))
            record["pairs"].append(spec)
        stop()
        _settled_records(crossed)
        # down before the swap: a crash after it leaves a mod with a way off
        _write_record(record)
        wrote = True
        # what sits on a file changing hands sits on this one from here, which
        # reads the same while the version going out still lists it too
        for _, theirs, _ in repoint:
            theirs["below"] = stem
        gave = [r for r in everything if any(r is other for other, _, _ in repoint)]
        for touched in gave:
            _write_record(touched)
        for pair, raw, new in swaps:
            write_atomic(pair.index, new, durable=True)
            swapped.append((pair, raw))
            did("commit", pair)
        for pair, raw, new in swaps:
            problem = _read_back(pair, raw, moved[pair],
                                 {i["path"]: i["sha"] for i in items[pair]},
                                 lambda key: did("check", key), renaming[pair])
            if problem:
                raise FilesError(f"{pair.index.name} didn't read back as written ({problem})")
        # and only then off the version going out, marked replaced in the same
        # write: what's left of it is half a model, so whatever runs next
        # takes the rest off if the take-off after this doesn't. Until then
        # both list what changed hands, the same file in the same place, and
        # whichever comes off first leaves it to the other
        for spec, given in _by_spec(adopted):
            spec["entries"] = [i for i in spec["entries"] if id(i) not in given]
            # its saved index would put back what it no longer owns
            spec["after_sha"] = ""
        leaving = [r for r in everything if _rid(r) in going]
        for old in leaving:
            old["replaced"] = stem
        gave += [r for r in leaving if not any(r is o for o in gave)]
        for touched in leaving:
            _write_record(touched)
    except BaseException as e:
        left = []
        for pair, raw in reversed(swapped):
            try:
                write_atomic(pair.index, raw, durable=True)
            except OSError as failed:
                left.append(f"{pair.index.name} could not be put back ({failed})")
        if not left:
            # one that won't cut back leaves bytes nothing points at, which
            # cost disk space and nothing else
            for tail in tails.values():
                tail.roll_back()
            for spec, given in _by_spec(adopted) if gave else ():
                there = {id(i) for i in spec["entries"]}
                spec["entries"] += [i for i in given.values() if id(i) not in there]
            for _, theirs, was in repoint if gave else ():
                theirs["below"] = was
            for old in leaving:
                old.pop("replaced", None)
            for touched in reversed(gave):
                _write_record(touched)
            if wrote:
                _forget(record)
            else:
                for spec in record["pairs"]:
                    if spec["before"]:
                        (folder / spec["before"]).unlink(missing_ok=True)
        if left:
            raise FilesError(f"{name} didn't go in ({e}) and couldn't be fully undone: "
                             f"{'; '.join(left)}. Its record is kept, so Remove in the Mods "
                             "drawer can finish taking it off.") from e
        if isinstance(e, (FilesError, Cancelled)) or not isinstance(e, Exception):
            raise
        raise FilesError(f"{name} didn't go in ({e}). The game is as it was.") from e

    landed = [pair for pair, _ in plans if moved[pair]]
    count = len({p for pair in landed for p in moved[pair]})
    if landed and on_line:
        on_line(f"[files] {name}: {count} files into "
                f"{', '.join(p.archive.name for p in landed)}")
    return {"ok": True, "name": name, "files": count,
            "adopted": len({i["path"] for _, i in adopted}),
            "kept": sorted({t[2]["path"].rsplit("/", 1)[-1]
                            for those in carried.values() for t in those}),
            "same": len(same - {p for m in moved.values() for p in m}),
            "sets": sorted({pair.voice for pair in landed}),
            "archives": sorted(p.archive.name for p in landed), "recorded": True}


def _by_spec(adopted: list) -> list:
    """(spec, id -> entry) for each spec entries were taken over from, in one go each"""
    out = {}
    for spec, item in adopted:
        out.setdefault(id(spec), (spec, {}))[1][id(item)] = item
    return list(out.values())


def _read_back(pair: Pair, before: bytes, moves: dict, shas: dict, tick=None,
               renames=None) -> str:
    """
    every moved record where it was put, every renamed one under its new name,
    nothing else moved, and what each one points at hashing to what was
    written. `tick((pair, None))` once the index has read back whole, then
    `tick((pair, path))` after each file
    """
    from patcher.archive.filelist import by_code, load, parse
    from patcher.mods.hd_zones import _record

    try:
        book = load(pair.index)
        now = list(book.entries())
    except Exception as e:
        return f"it can't be read ({e})"
    size = pair.archive.stat().st_size
    for entry in now:
        spot = moves.get(_key(entry.path))
        if spot is not None and tuple(_fields(entry)) != tuple(spot):
            return f"{entry.path} isn't where it was put"
        if not entry.is_stub and entry.byte_offset + entry.stored > size:
            return f"{entry.path} points past the end of {pair.archive.name}"
    if renames:
        # by code, since a renamed record's path was somebody else's before
        def planned(code, entry):
            path = _key(renames.get(code, entry.path))
            return path, tuple(moves.get(path, _fields(entry)))

        then = {code: sorted(planned(code, e) for e in records)
                for code, records in by_code(parse(before)).items()}
        if then != {code: sorted((_key(e.path), tuple(_fields(e))) for e in records)
                    for code, records in by_code(book).items()}:
            return "entries it didn't touch moved"
    else:
        rest = Counter((_key(e.path), tuple(_fields(e))) for e in parse(before).entries()
                       if _key(e.path) not in moves)
        if rest != Counter((_key(e.path), tuple(_fields(e))) for e in now
                           if _key(e.path) not in moves):
            return "entries it didn't touch moved"
    if tick is not None:
        tick((pair, None))
    first = {}
    for entry in now:
        first.setdefault(_key(entry.path), entry)
    stamp, read = _stamp(pair.archive), {}
    with pair.archive.open("rb") as handle:
        for path in sorted(moves, key=lambda p: moves[p][0]):
            try:
                if shas[path] == STUB:
                    if not first[path].is_stub:
                        return f"{path} reads back as something else"
                elif hashlib.sha256(_record(handle, first[path])).hexdigest() != shas[path]:
                    return f"{path} reads back as something else"
            except (ValueError, zlib.error) as e:
                return f"{path} won't read back ({e})"
            read[(_where(pair.archive), stamp, first[path].raw_fields)] = shas[path]
            if tick is not None:
                tick((pair, path))
    # hashed already, so the drawer's next look doesn't do it again
    global _generation
    with _contents_lock:
        _contents.update(read)
        _generation += 1
    return ""


def _live(install_dir: Path, spec: dict):
    index, archive = install_dir / spec["index"], install_dir / spec["archive"]
    try:
        return index, archive, entries(index)
    except Exception:
        return index, archive, None


def state(install_dir, record: dict, everything: list | None = None) -> dict:
    """
    where a mod stands, as {"state", "files"} and a count of each: in, under
    another mod, out (something else is there now: an HD build, a restore, a
    verify), or unknown when an index won't read. A mod taken off and kept for
    a restore reads as in only where its file is back and no mod on record
    accounts for it. `everything` is records() when the caller already has it
    """
    from patcher.paths import undo_store_for

    install_dir = Path(install_dir)
    rid, count, over = _rid(record), Counter(), set()
    with _View(install_dir, undo_store_for(install_dir), everything) as view:
        for spec in record.get("pairs", []):
            if view.entries(spec["index"]) is None:
                return {"state": "unknown", "files": len(spec.get("entries", [])),
                        "in": 0, "covered": 0, "out": 0, "under": []}
            for item in spec.get("entries", []):
                if item.get("lost"):
                    count["out"] += 1
                    continue
                chain = view.chain(spec, item["path"])
                held = view.live(spec, item["path"])[1]
                at = view.top(spec, item["path"])
                where = next((i for i, t in enumerate(chain) if _rid(t[0]) == rid), None)
                if record.get("off"):
                    count["in" if held == item["sha"] and at is None else "out"] += 1
                elif where is None or (at is not None and where > at):
                    count["out"] += 1
                elif where == at:
                    count["in"] += 1
                elif at is None and where == len(chain) - 1:
                    # what's there may be one taken off and kept for a
                    # restore that went on this one's file, back with one and
                    # not off again yet: this one is still under that, not out
                    ghost = next((r for r, i in view.at(spec, item["path"]) if r.get("off")
                                  and i["sha"] == held and i["under"] == item["sha"]), None)
                    count["out" if ghost is None else "under"] += 1
                    if ghost is not None:
                        over.add(ghost["name"])
                else:
                    # under whoever the game holds, or the top of the stack
                    # when that's nobody's and settle() has it next
                    count["under"] += 1
                    over.add(chain[-1 if at is None else at][0]["name"])
    total = sum(count.values())
    if count["out"] == total:
        word = "out"
    elif count["out"]:
        word = "partly"
    else:
        word = "in"
    return {"state": word, "files": total, "in": count["in"], "covered": count["under"],
            "out": count["out"], "under": sorted(over)}


def settle(install_dir, stuck: list | None = None) -> tuple:
    """
    (came out, taken off again), after something put older archives back or
    repacked them. A mod a restore took out with it is forgotten, since a
    record of something no longer in the game is a row Remove has nothing to
    do for, and one it took half of goes the rest of the way. One taken off
    after the HD backup was taken comes back with it, and goes again. Where a
    repack carried each mod is put on record, so finding its bytes later
    starts in the right place. What it couldn't do goes in `stuck`, as
    (name, why)
    """
    from patcher.mods.hd_install import InstallError, exclusive
    from patcher.paths import undo_store_for

    install_dir, store = Path(install_dir), undo_store_for(install_dir)
    gone, again, said = [], [], []
    try:
        with exclusive(store):
            # again while a pass changes something: taking one off can put
            # back a file of one the same pass already looked at. What's
            # stuck is what the last pass couldn't do
            for _ in range(4):
                said = []
                if not _settle_pass(install_dir, store, gone, again, said):
                    break
    except InstallError as e:
        # something else has the archives; the next settle catches up
        said = [("", str(e))]
    if stuck is not None:
        stuck += list({name: (name, why) for name, why in said}.values())
    keep_contents(install_dir, store)
    # an update cut short leaves two by one name, and one of them coming off
    # isn't the mod coming out
    there = {r["name"] for r in records(store) if not r.get("off")}
    return list(dict.fromkeys(n for n in gone if n not in there)), list(dict.fromkeys(again))


def _settle_pass(install_dir: Path, store: Path, gone: list, again: list, stuck: list) -> bool:
    """
    one go over every record, newest first, the way a stack comes off, and the
    ones kept for a restore before the rest: what they cover is only there to
    judge once they're off again. Whether it changed anything
    """
    changed, blocked = False, set()
    everything = records(store)
    for stale in [r for r in reversed(everything) if r.get("off")] + \
            [r for r in reversed(everything) if not r.get("off")]:
        record = _fresh(store, stale)
        if record is None:
            continue
        mine = {(spec["index"], i["path"]) for spec in record["pairs"] for i in spec["entries"]}
        if not record.get("off") and mine & blocked:
            # a kept-off one that wouldn't come off again still covers some of
            # this one, which can't be judged until it does
            continue
        try:
            seen, done = state(install_dir, record), {"left": []}
            if record.get("off") and seen["state"] in ("in", "partly"):
                done = _take_off(install_dir, store, record)
                gone += done["crossed"]
                again.append(record["name"])
            elif record.get("off"):
                if _backup_holds(install_dir, record):
                    continue
                _forget(record)
            elif record.get("replaced") or seen["state"] == "partly" or (
                    seen["state"] == "out" and not _backup_holds(install_dir, record, every=True)) \
                    or (seen["covered"] and _hollow(install_dir, store, record)):
                # what's left of one a restore took half of is a model split
                # between two versions, so the rest goes too, as does an old
                # version an update stopped taking off. One out altogether
                # that the backup has whole is a Verify's doing, and the next
                # restore brings it back
                done = _take_off(install_dir, store, _fresh(store, record) or record)
                gone += [record["name"]] + done["crossed"]
            else:
                _reanchor(install_dir, store, record)
                continue
            changed = True
            stuck += done["left"]
        except (OSError, FilesError) as e:
            # the rest still get their turn; the next settle, or Remove in the
            # drawer, has another go at this one
            stuck.append((record["name"], str(e)))
            if record.get("off"):
                blocked |= mine
    return changed


def _hollow(install_dir: Path, store: Path, record: dict) -> bool:
    """
    whether a file of this one's under another mod is nowhere any more: on
    record under a mod a restore put back, which had something else under it
    when the backup was taken. Marked lost, so the stack closes over it and the
    rest of the mod comes off
    """
    hollow = {}
    with _View(install_dir, store) as view:
        mine = next((r for r in view.everything if _rid(r) == _rid(record)), None)
        for spec in mine["pairs"] if mine else ():
            for item in spec["entries"]:
                chain, at = view.chain(spec, item["path"]), view.top(spec, item["path"])
                where = next((i for i, t in enumerate(chain) if t[2] is item), None)
                if where is None or where >= (len(chain) - 1 if at is None else at):
                    continue
                hints = _hints(view, spec, item["path"], item["sha"], item["now"])
                if _find(view, spec, item["path"], item["sha"], hints) is not None:
                    continue
                more, _ = _splice(view, mine, spec, [item])
                hollow.update(more)
                item["lost"] = True
                hollow[_rid(mine)] = mine
    _settled_records(hollow)
    return bool(hollow)


def settled(install_dir, keep=()) -> list:
    """settle(), as lines for a run's log. `keep` are lifted mods going back on after"""
    from patcher.mods.titles import title

    stuck = []
    gone, again = settle(install_dir, stuck)
    gone = [title(name) for name in gone if name not in keep]
    again = [title(name) for name in again]
    stuck = [(name and title(name), why) for name, why in stuck]
    said = []
    if gone:
        it = "it" if len(gone) == 1 else "them"
        said.append(f"{' and '.join(gone)} went in after the HD backup was taken, so "
                    f"{'it' if len(gone) == 1 else 'they'} came out with it. Drop {it} on the "
                    f"Mods drawer again to put {it} back.")
    if again:
        said.append(f"{' and '.join(again)} came back with the HD backup, which was taken with "
                    f"{'it' if len(again) == 1 else 'them'} in, and "
                    f"{'was' if len(again) == 1 else 'were'} taken off again.")
    for name, why in stuck:
        said.append(f"{name} couldn't be put right after that ({why}). Remove in the Mods drawer "
                    "has another go at it." if name else
                    f"The Mods drawer's mods weren't looked at after that ({why}). The next HD "
                    "install or restore does it.")
    return said


def lift(install_dir) -> dict | None:
    """
    every drawer mod's own files, read out of the game as it is now and kept
    under the undo store, oldest first. An HD build repacks its archives from
    the backup, which has none of what went in after it, so what it drops goes
    back on top from here afterwards (lay_back) rather than being lost. None
    when the drawer has nothing in
    """
    import tempfile

    from patcher.fileops import clear_tree
    from patcher.mods.hd_install import exclusive
    from patcher.mods.hd_zones import read_record
    from patcher.paths import undo_store_for

    install_dir = Path(install_dir)
    store = undo_store_for(install_dir)
    kept = [r for r in records(store) if not r.get("off")]
    if not kept:
        return None
    (store / "work").mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="lifted-", dir=store / "work"))
    mods = []
    try:
        with exclusive(store), _View(install_dir, store, kept) as view:
            for n, record in enumerate(kept):
                root, whole = folder / f"{n:03d}", True
                for spec in record["pairs"]:
                    # white_scru\chr\... is how look() hears which voice set
                    into = root / Path(spec["archive"]).name.split(".")[0]
                    for item in spec["entries"]:
                        if item.get("lost") or item["sha"] in (STUB, ""):
                            continue
                        hints = _hints(view, spec, item["path"], item["sha"], item.get("now"))
                        found = _find(view, spec, item["path"], item["sha"], hints)
                        if found is None:
                            whole = False
                            break
                        if found[0] == "plain":
                            plain = found[1]
                        elif found[0] == "at":
                            plain = read_record(install_dir / spec["archive"],
                                                _entry_at(item["path"], found[1]))
                        else:
                            _, stored, size, _ = found
                            plain = zlib.decompress(stored) if len(stored) != size else stored
                        target = into.joinpath(*item["path"].split("/"))
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(plain)
                    if not whole:
                        break
                # half a mod back on top is worse than the message saying to
                # drop it again
                if whole and root.is_dir():
                    mods.append({"name": record["name"], "source": record.get("source", ""),
                                 "extra": {k: v for k, v in record.items() if not
                                           k.startswith("_") and k not in
                                           ("name", "id", "source", "at", "pairs")},
                                 "folder": str(root)})
    except Exception:
        clear_tree(folder)
        return None
    return {"folder": str(folder), "mods": mods}


def lay_back(install_dir, lifted: dict | None, on_line=None) -> list:
    """
    what lift() kept that an HD build dropped, put back on top through the
    drawer's own door in the order it first went in, so the stack is the one
    the person built. The names it put back. The kept copies go either way
    """
    from patcher.fileops import clear_tree
    from patcher.paths import undo_store_for

    if not lifted:
        return []
    install_dir = Path(install_dir)
    store = undo_store_for(install_dir)
    back = []
    try:
        there = {r["name"] for r in records(store) if not r.get("off")}
        game = read_game(install_dir)
        for mod in lifted["mods"]:
            if mod["name"] in there:
                continue
            try:
                with Source(Path(mod["folder"])) as source:
                    found = look_in(source, game)
                    install(install_dir, store, mod["name"], found, source,
                            origin=mod["source"], extra=mod["extra"] or None)
                back.append(mod["name"])
            except Exception as e:
                if on_line:
                    on_line(f"{mod['name']} couldn't go back on top ({e}). Drop it on the Mods "
                            "drawer again to put it back.")
    finally:
        clear_tree(Path(lifted["folder"]))
    return back


def _reanchor(install_dir: Path, store: Path, record: dict) -> None:
    """where the game holds each of a record's files now, for finding them by later"""
    moved, rid = False, _rid(record)
    with _View(install_dir, store) as view:
        for spec in record["pairs"]:
            for item in spec["entries"]:
                chain, at = view.chain(spec, item["path"]), view.top(spec, item["path"])
                if at is None or _rid(chain[at][0]) != rid:
                    continue
                entry = view.live(spec, item["path"])[0]
                if _fields(entry) != item["now"]:
                    _moved(item, _fields(entry))
                    moved = True
    if moved:
        _write_record(record)


def _view_for(index):
    from patcher.paths import undo_store_for

    index = Path(index)
    install_dir = index.parents[2]
    return _View(install_dir, undo_store_for(install_dir)), index.relative_to(
        install_dir).as_posix()


def held(index) -> frozenset:
    """the paths in this index holding a drawer mod's file right now"""
    try:
        view, name = _view_for(index)
        with view:
            return frozenset(view.holding(name))
    except Exception:
        return frozenset()


def originals(index) -> dict:
    """
    path -> the size of what's under the drawer's mods holding it, the bottom
    of each stack. What the HD Project's checks read the index as, so a
    costume from the Mods drawer never reads as an HD category being in
    """
    try:
        view, name = _view_for(index)
        with view:
            return {path: view.chain(spec, path)[0][2]["was"][1]
                    for path, (_, spec, _) in view.holding(name).items()}
    except Exception:
        return {}


def bottoms(index) -> dict:
    """
    path -> where what's under the drawer's mods sat, for every path one of
    them holds: the index as it was before the drawer, as far as its own
    records go. What a patch made against the game's own files compares with
    """
    try:
        view, name = _view_for(index)
        with view:
            return {path: list(view.chain(spec, path)[0][2]["was"])
                    for path, (_, spec, _) in view.holding(name).items()}
    except Exception:
        return {}


def befores(index) -> list:
    """
    the index as each of the drawer's mods on it found it, the first of them
    first, which is what the game had before the drawer as far as a record
    kept it. Each checked against the digest taken when it was saved
    """
    from patcher.paths import undo_store_for

    index = Path(index)
    install_dir = index.parents[2]
    name, store = index.relative_to(install_dir).as_posix(), undo_store_for(install_dir)
    out = []
    for record in records(store):
        for spec in record["pairs"]:
            if spec["index"] != name or not spec.get("before"):
                continue
            try:
                raw = (_records(store) / spec["before"]).read_bytes()
            except OSError:
                continue
            if hashlib.sha256(raw).hexdigest() == spec.get("before_sha"):
                out.append(raw)
    return out


def underneath(index, path: str) -> bytes | None:
    """
    the bytes under every drawer mod on one path, what the game had before the
    first of them went in, found the way Remove finds them: where they sat,
    where a repack carried them, the HD backup. None when the drawer holds
    nothing there or those bytes are gone
    """
    import zlib

    from patcher.mods.hd_zones import read_record

    view, name = _view_for(index)
    key = _key(path)
    with view:
        chain = view.chain({"index": name}, key)
        if not chain:
            return None
        _, spec, bottom = chain[0]
        sha = bottom["under"]
        found = _find(view, spec, key, sha, _hints(view, spec, key, sha, bottom["was"]),
                      downloads=False)
        if found is None or sha in (STUB, ""):
            return None
        if found[0] == "at":
            plain = read_record(view.install_dir / spec["archive"], _entry_at(key, found[1]))
        else:
            _, blob, uncompressed, stored = found
            plain = zlib.decompress(blob) if uncompressed != stored else blob
    return plain if hashlib.sha256(plain).hexdigest() == sha else None


def holding(install_dir, ids) -> dict:
    """mod name -> the character ids among these that one of the file mods holds"""
    from patcher.mods import hd_vanilla

    wanted, out = {i.lower() for i in ids}, {}
    for name, paths in holders(install_dir, None).items():
        hit = {p[1] for p in map(hd_vanilla.parse_path, paths) if p and p[1] in wanted}
        if hit:
            out[name] = hit
    return out


def holders(install_dir, paths, index: str | None = None) -> dict:
    """
    mod name -> the paths among these that one of the file mods holds right
    now, or every path one holds when `paths` is None. In every index, or
    just `index`, the one a voice set reads: the same path is in both
    """
    from patcher.paths import undo_store_for

    install_dir, out = Path(install_dir), {}
    wanted = None if paths is None else {_key(p) for p in paths}
    with _View(install_dir, undo_store_for(install_dir)) as view:
        for name in sorted({key[0] for key in view.stacks}):
            if index is not None and name != index:
                continue
            for path, (record, _, _) in view.holding(name).items():
                if wanted is None or path in wanted:
                    out.setdefault(record["name"], set()).add(path)
    return out


def stacked(install_dir, index: str, path: str) -> list:
    """the records holding one path in one index, bottom to top"""
    from patcher.paths import undo_store_for

    with _View(Path(install_dir), undo_store_for(install_dir)) as view:
        return [record for record, _, _ in view.stacks.get((index, _key(path)), [])]


def remove(install_dir, store, name: str, on_line=None) -> dict:
    from patcher.mods.hd_install import InstallError, exclusive

    try:
        with exclusive(Path(store)):
            found = [r for r in records(store) if r["name"] == name]
            if not found:
                raise FilesError(f"there's no record of {name} in this game")
            _ready(pairs(install_dir))
            done = {"ok": True, "name": name, "files": 0, "handed": 0, "crossed": [], "left": [],
                    "stuck": []}
            for stale in reversed(found):
                record = _fresh(store, stale)
                if record is None:
                    continue
                gone = _take_off(Path(install_dir), Path(store), record, on_line=on_line)
                for key in ("files", "handed", "left"):
                    done[key] += gone[key]
                done["stuck"] += gone.get("stuck", [])
                done["crossed"] = sorted((set(done["crossed"]) | set(gone["crossed"])) - {name})
            done["left"] = _still_left(store, done["left"])
            keep_contents(install_dir, store)
            return done
    except InstallError as e:
        raise FilesError(str(e)) from None


def _splice(view: _View, record: dict, spec: dict, items=None, skip=(), mates=None) -> tuple:
    """
    every entry, a kept-off mod's too, that names this record as what's under
    it, handed what's under this one instead: (records to write, how many).
    `items` narrows it to some of the record's entries, and `mates` is path ->
    another record with the same file in the same place, which takes it over
    """
    rid, touched, count = _rid(record), {}, 0
    mine = {item["path"]: item for item in (spec["entries"] if items is None else items)}
    for other in view.everything:
        if _rid(other) == rid or _rid(other) in skip:
            continue
        for theirs in other["pairs"]:
            if theirs["index"] != spec["index"]:
                continue
            for item in theirs["entries"]:
                below = mine.get(item["path"])
                if below is None or (item.get("below") != rid
                                     and not _stray(view, spec, item, other, rid, below)):
                    continue
                mate = (mates or {}).get(item["path"])
                if mate is not None and mate is not other:
                    item["below"] = _rid(mate)
                else:
                    # what it covered is only this one's file if this one was
                    # in when it went on; otherwise it keeps what it found
                    if item["under"] == below["sha"]:
                        _went_under(item, below["under"], below["was"])
                        # and the name the record goes back to with it
                        if below.get("renamed") and not item.get("renamed"):
                            item["renamed"] = list(below["renamed"])
                    item["below"] = below.get("below") or ""
                    # until this one's record is gone both sit on the same
                    # thing, and this says which is on top
                    item["from"] = rid
                # what that one saved as "before" still has this mod in it
                theirs["after_sha"] = ""
                touched[_rid(other)] = other
                count += 1
    return touched, count


def _mates(view: _View, spec: dict, record: dict, item: dict) -> list:
    """
    the other records listing this very file in this very place, which an
    update cut short between its writes leaves: one of them holds the file
    once this one's off. Not the one this was handed down from, a Remove
    that stopped, once the game holds nothing else of that model of its
    """
    rid, below, handed = _rid(record), item.get("below") or "", item.get("from")
    return [other for other, theirs in view.at(spec, item["path"])
            if _rid(other) != rid and not other.get("off") and not theirs.get("lost")
            and theirs["sha"] == item["sha"] and (theirs.get("below") or "") == below
            and (_rid(other) != handed or _holds_model(view, spec["index"], other, item["path"]))]


def _holds_model(view: _View, index_name: str, record: dict, path: str) -> bool:
    """whether the game holds any of a record's files of the model a path is part of"""
    rid = _rid(record)
    if (rid, index_name) not in view.models:
        # once per view: a pack coming off asks this for every file it has
        models = view.models[(rid, index_name)] = {}
        for theirs in record["pairs"]:
            for item in theirs["entries"] if theirs["index"] == index_name else ():
                models.setdefault(_stem(item["path"]), []).append((theirs, item))
    for theirs, item in view.models[(rid, index_name)].get(_stem(path), ()):
        at = view.top(theirs, item["path"])
        if at is not None and _rid(view.chain(theirs, item["path"])[at][0]) == rid:
            return True
    return False


def _mate(view: _View, spec: dict, record: dict, item: dict):
    return next(iter(_mates(view, spec, record, item)), None)


def _sitting(view: _View, spec: dict, path: str, owner: dict, item: dict) -> list:
    """
    (record, entry) for everything on an entry about to change hands, a
    kept-off mod's too, whether it names the entry's record or a mate of it
    """
    spot = {_rid(r) for r in [owner] + _mates(view, spec, owner, item)}
    return [(other, theirs) for other, theirs in view.at(spec, path)
            if other is not owner and theirs.get("below") in spot]


def _stray(view: _View, spec: dict, item: dict, owner: dict, rid: str, below: dict) -> bool:
    """
    whether an entry naming something with nothing at its path any more (a
    crash between an update's writes can leave that) sat on this one's file
    instead: it covered that file, and no other mod still has it there
    """
    named = item.get("below") or ""
    if not named or item["under"] != below["sha"]:
        return False
    there = view.at(spec, item["path"])
    if any(_rid(r) == named for r, _ in there):
        return False
    return not any(_rid(r) not in (rid, _rid(owner)) and i["sha"] == item["under"]
                   for r, i in there)


def _find(view: _View, spec: dict, path: str, sha: str, hints: list, downloads: bool = True):
    """
    the bytes that hash to `sha` for a path, as ("at", fields) where the live
    archive already has them, ("bytes", stored, uncompressed, stored size) from
    the HD backup's copy, or ("plain", bytes) out of a mod's own download when
    `downloads`. Places are where to look, never what decides
    """
    from patcher.paths import undo_store_for

    if sha in (STUB, ""):
        # nothing to hash: a placeholder, or what was there wouldn't read
        # either, and the way back is where it pointed
        return ("at", tuple(hints[0])) if hints else None
    archive = view.install_dir / spec["archive"]
    places = list(dict.fromkeys(tuple(h) for h in hints if h))
    for spot in places:
        if view.content(archive, _entry_at(path, spot)) == sha:
            return "at", spot
    spares = undo_store_for(view.install_dir) / "hdproject"
    spare, spare_index = spares / archive.name, spares / Path(spec["index"]).name
    if spare.is_file():
        try:
            theirs = entries(spare_index).get(path) if spare_index.is_file() else None
        except Exception:
            theirs = None
        tries = ([tuple(_fields(theirs))] if theirs is not None else []) + places
        for spot in dict.fromkeys(tries):
            entry = _entry_at(path, spot)
            if view.content(spare, entry) == sha:
                handle = view.handle(spare)
                handle.seek(entry.byte_offset)
                return "bytes", handle.read(entry.stored), spot[1], spot[2]
    # the other voice set's copy: a model's the same file in both, and one
    # side's can outlive a repack of the other
    for pair in pairs(view.install_dir):
        if pair.archive == archive or not pair.archive.is_file():
            continue
        try:
            theirs = entries(pair.index).get(path)
        except Exception:
            continue
        if theirs is not None and view.content(pair.archive, theirs) == sha:
            handle = view.handle(pair.archive)
            handle.seek(theirs.byte_offset)
            fields = _fields(theirs)
            return "bytes", handle.read(theirs.stored), fields[1], fields[2]
    for record, item in view.at(spec, path) if downloads else ():
        if item["sha"] != sha or not item.get("member"):
            continue
        ways = [(item.get("source") or record.get("source", ""), item["member"])]
        for where, member in ways + [tuple(a) for a in item.get("also", [])]:
            source = view.source(where)
            if source is None or not source.has(member):
                continue
            try:
                plain = source.read(member)
            except Exception:
                continue
            if hashlib.sha256(plain).hexdigest() == sha:
                return "plain", plain
    return None


def _hints(view: _View, spec: dict, path: str, sha: str, first) -> list:
    """
    everywhere anything on record says bytes hashing to `sha` sat for this
    path, and where the game's entry points now: a mod on top of the same
    bytes is holding them, wherever a repack put them
    """
    live = view.live(spec, path)[0]
    out = [first] + ([_fields(live)] if live is not None else [])
    for _, item in view.at(spec, path):
        if item["sha"] == sha:
            out += [item["now"]] + item.get("seen", [])
        if item["under"] == sha:
            out.append(item["was"])
        out += [place for under, place in item.get("past", []) if under == sha]
    return out


def _back_under(view: _View, spec: dict, item: dict, lower: list):
    """
    for one kept for a restore and back with one: the highest mod on record
    under it whose file can be had, which is what the game ought to hold with
    this one off, even if it went in after this one came off and never sat on
    it. None when there's none, and it goes the way it came instead
    """
    for owner in reversed(lower):
        sha = owner[2]["sha"]
        # not out of a download: that would be putting back a mod the restore
        # took out, on the paths that happen to have one of these on them
        found = _find(view, spec, item["path"], sha,
                      _hints(view, spec, item["path"], sha, owner[2]["now"]), downloads=False)
        if found is not None:
            return found, owner, [], sha
    return None


def _stem(path: str) -> str:
    """chr/pc/c205/bin/c205.win32 for its .trb and its .imgb both"""
    return path.lower().rsplit(".", 1)[0]


def _way_back(view: _View, spec: dict, item: dict, lower: list):
    """
    (what to point the entry at, the mod that is, the mods passed over, what
    it hashes to) for the nearest thing under an entry that can still be had,
    or None. Down the stack one mod at a time: a repack drops whatever nothing
    pointed at, so a mod under another can be gone from the archive and the
    backup both, and is passed over for what was under it
    """
    sha, first, below = item["under"], item["was"], item.get("below") or ""
    passed = []
    while True:
        found = _find(view, spec, item["path"], sha, _hints(view, spec, item["path"], sha, first))
        if found is not None:
            # on top again: the highest one under it whose file that is. Not
            # always the one it went on, which may have been out of the game
            # already, left behind by a restore
            owner = next((t for t in reversed(lower)
                          if t[2]["sha"] == sha and not any(t is p for p in passed)), None)
            return found, owner, passed, sha
        nxt = next((t for t in reversed(lower) if _rid(t[0]) == below), None) if below else None
        if nxt is None:
            return None
        passed.append(nxt)
        sha, first, below = nxt[2]["under"], nxt[2]["was"], nxt[2].get("below") or ""


def _remove(install_dir: Path, store: Path, record: dict, on_line=None, tick=None) -> dict:
    """
    every entry still holding the mod pointed back at what was under it.
    `tick(spec)` as each voice set's index is through

    Whatever names this mod as what's under it, a later mod or one taken off
    and kept for a restore, is handed what was under this one first, so the
    stack closes over the gap whichever order things come off in. Then the
    whole index goes back byte for byte when nothing has touched it since, or
    entry by entry: only where this mod is the top of its stack and the game
    holds its file, onto the nearest thing under it that can still be had, out
    of the live archive, the HD backup's copy or a mod's own download, each
    told by what it hashes to
    """
    from patcher.archive.filelist import parse, rebuild, relocate_many, rename_records
    from patcher.fileops import write_atomic
    from patcher.mods.hd_mirror import _index_problem, _Tail

    folder, rid = Path(record["_file"]).parent, _rid(record)
    # decided before anything moves: what the backup has is what a restore
    # brings back, and this mod's sectors stay taken while it can
    keep = _backup_holds(install_dir, record)
    # what an earlier go passed over before it was cut short, still to come off
    stuck, crossing, back, handed = [], dict(record.get("crossing") or {}), 0, 0
    # a voice set with a file that has nothing to go back to waits for the
    # others once: the original can be in the other set's archive, which
    # only points at it again once this mod's off there
    queue, waited = list(record.get("pairs", [])), set()
    while queue:
        spec = queue.pop(0)
        index, archive, live = _live(install_dir, spec)
        if live is None and index.is_file():
            raise FilesError(f"{index.name} won't read, so {record['name']} can't come off from "
                             "here")
        if live is None:
            continue
        with _View(install_dir, store) as view:
            # a file another version lists in the same place stays, theirs now
            mates = {item["path"]: mate for item in spec["entries"]
                     for mate in [_mate(view, spec, record, item)] if mate is not None}
            touched, count = _splice(view, record, spec, mates=mates)
            handed += count
            _settled_records(touched)
            tops = [(item, lower) for item in spec["entries"] if item["path"] not in mates
                    for lower in [_in_game(view, record, spec, item)] if lower is not None]
            raw = index.read_bytes()
            kept = folder / spec["before"] if spec.get("before") else None
            if (spec.get("after_sha") and hashlib.sha256(raw).hexdigest() == spec["after_sha"]
                    and kept is not None and kept.is_file()
                    and hashlib.sha256(kept.read_bytes()).hexdigest() == spec.get("before_sha")
                    and len(tops) == len(spec["entries"])
                    and all(view.content(archive, _entry_at(i["path"], i["was"])) == i["under"]
                            for i in sorted(spec["entries"], key=lambda i: i["was"][0]))):
                write_atomic(index, kept.read_bytes(), durable=True)
                back += len(spec["entries"])
                if not keep:
                    _cut_back(index, archive, spec)
                if tick is not None:
                    tick(spec)
                continue

            moves, fetch, owners, touched, rebased = {}, [], {}, {}, {}
            ways = {item["path"]: (_back_under(view, spec, item, lower) if record.get("off")
                                   else None) or _way_back(view, spec, item, lower)
                    for item, lower in tops}
            # a model's .trb and .imgb only load as a pair, so one that can't go
            # back keeps the other as the mod left it too, rather than half of
            # each in the game
            lost = {_stem(path) for path, way in ways.items() if way is None}
            if lost and queue and spec["index"] not in waited:
                waited.add(spec["index"])
                queue.append(spec)
                continue
            stuck += [item["path"] for item, _ in tops if _stem(item["path"]) in lost]
            for item, lower in tops:
                if _stem(item["path"]) in lost:
                    continue
                entry = live[item["path"]]
                found, owner, passed, under = ways[item["path"]]
                if found[0] == "at":
                    moves[item["path"]] = found[1]
                elif found[0] == "bytes":
                    fetch.append((item["path"], found[1], found[2], found[3]))
                else:
                    blob = _stored(found[1], entry)
                    fetch.append((item["path"], blob, len(found[1]), len(blob)))
                if owner is not None:
                    owners[item["path"]] = owner
                # a mod further down whose bytes a repack dropped has lost this
                # file for good, and is marked to come off whole. Whatever
                # sat on it, a kept-off mod included, sits on what's under it
                for gone, gone_spec, gone_item in passed:
                    more, _ = _splice(view, gone, gone_spec, [gone_item], skip={rid})
                    touched.update(more)
                    gone_item["lost"] = True
                    touched[_rid(gone)] = gone
                    crossing[_rid(gone)] = gone["name"]
                if passed:
                    # this one's own entry too, so another go after a failed
                    # write goes straight to what's under it now: onto what
                    # the last one crossed off sat on, the way _splice() hands
                    # it down, which keeps the stack one stack
                    item["below"] = passed[-1][2].get("below") or ""
                    _went_under(item, under, item["was"])
                    rebased[item["path"]] = item
            view.forget(archive)
        # down before the index moves: after that, nothing in the game says
        # which mods were passed over on the way. This one's own record
        # first, in one write, so a crash anywhere after still knows them
        if rebased:
            record["crossing"] = crossing
            _write_record(record)
        _settled_records(touched)
        touched = {}
        tail = _Tail(archive, digests=False) if fetch else None
        before = _stamp(archive)
        try:
            for path, blob, size, stored in fetch:
                moves[path] = (tail.put(blob), size, stored)
            if tail is not None:
                tail.close()
                _carry(archive, before, tail.size)
            if moves:
                book = parse(raw)
                problem = _index_problem(book, raw)
                if problem:
                    raise FilesError(f"{index.name} can't be written back exactly ({problem}), "
                                     f"so {record['name']} is still in")
                relocate_many(book, moves)
                # a record given a name on the way in has the old one back
                # along with what was under it
                named = {item["renamed"][0]: item["renamed"][1] for item, _ in tops
                         if item.get("renamed") and item["path"] in moves}
                if named:
                    rename_records(book, named)
                write_atomic(index, rebuild(book), durable=True)
                back += len(moves)
        except BaseException:
            if tail is not None:
                tail.roll_back()
            raise
        # the mod under it is the top again, and on record where it sits
        for path, (lower, lower_spec, lower_item) in owners.items():
            if path in moves:
                _moved(lower_item, moves[path])
                touched[_rid(lower)] = lower
        for path, item in rebased.items():
            if path in moves:
                item["was"] = list(moves[path])
        _settled_records(touched)
        if not keep:
            _cut_back(index, archive, spec)
        if tick is not None:
            tick(spec)
    # what was under those is nowhere any more, not in the archive, the HD
    # backup or a mod's own download, so they stay as the mod left them: the
    # mod still comes off the list, rather than a Remove that fails every time
    if keep:
        # the HD backup was taken with it in, so a restore puts it back. Kept,
        # marked off, for settle() to take off again when one does
        record["off"] = True
        record.pop("crossing", None)
        _write_record(record)
    else:
        _forget(record)
    if on_line:
        on_line(f"[files] {record['name']} taken off, {back} files back as they were")
    return {"ok": True, "name": record["name"], "files": back, "handed": handed,
            "crossed": sorted(set(crossing.values())), "crossing": list(crossing),
            "stuck": sorted(set(stuck))}


def _take_off(install_dir: Path, store: Path, record: dict, on_line=None, tick=None) -> dict:
    """_remove(), then the rest of every mod it passed over on the way down"""
    done = _remove(install_dir, store, record, on_line=on_line, tick=tick)
    done["left"] = []
    return _whole(install_dir, store, done, done.pop("crossing"), on_line)


def _whole(install_dir: Path, store: Path, done: dict, queue=(), on_line=None) -> dict:
    """
    the rest of every mod in `queue`, passed over on the way down a stack, and
    of any the records mark as missing a file: one missing a file is a model
    split between two versions, a .trb that doesn't match its .imgb, which is
    worse than not having the mod at all. Counted into `done`
    """
    on = [r for r in records(store) if not r.get("off")]
    queue = list(queue) + [_rid(r) for r in on if any(
        i.get("lost") for spec in r["pairs"] for i in spec["entries"])]
    # and an old version an update gave part of its model to and didn't get
    # the rest of off, which is no repack's doing and nothing to say: the new
    # version's in, and the next go tries again
    quiet = {_rid(r) for r in on if r.get("replaced")} - set(queue)
    queue += sorted(quiet)
    seen = set()
    while queue:
        rid = queue.pop(0)
        rest = None if rid in seen else next((r for r in records(store) if _rid(r) == rid), None)
        seen.add(rid)
        if rest is None:
            continue
        try:
            more = _remove(install_dir, store, rest, on_line=on_line)
        except (FilesError, OSError) as e:
            if rid not in quiet:
                done["left"].append((rest["name"], str(e)))
            continue
        if rid not in quiet:
            done["files"] += more["files"]
            done["handed"] += more["handed"]
            done["crossed"] = sorted(set(done["crossed"]) | {rest["name"]})
        done["crossed"] = sorted(set(done["crossed"]) | set(more["crossed"]))
        queue += more["crossing"]
    return done


def _still_left(store, left: list) -> list:
    """the ones that didn't come off whole and still haven't, a later go having got some"""
    there = {r["name"] for r in records(store) if not r.get("off")}
    return list({name: (name, why) for name, why in left if name in there}.values())


def lost_said(name: str, why: str) -> str:
    """for a mod passed over on the way down that then wouldn't come off whole"""
    return (f"{name} lost a file to the HD Project's repack and the rest of it didn't come off: "
            f"{why}")


def _in_game(view: _View, record: dict, spec: dict, item: dict):
    """
    the stack under an entry when the game holds its file, it being the
    highest on the stack that does, else None. One taken off and kept for a
    restore holds it when its file is back and no mod on record accounts for it
    """
    if view.live(spec, item["path"])[1] != item["sha"]:
        return None
    chain, at = view.chain(spec, item["path"]), view.top(spec, item["path"])
    if record.get("off"):
        return chain if at is None else None
    if at is not None and _rid(chain[at][0]) == _rid(record):
        return chain[:at]
    return None


def _backup_holds(install_dir: Path, record: dict, every: bool = False) -> bool:
    """
    whether the HD backup holds any of this mod's files, which a restore puts
    back, or with `every`, all of them: the whole mod coming back with one
    """
    from patcher.paths import undo_store_for

    spares, found, total = undo_store_for(install_dir) / "hdproject", 0, 0
    for spec in record.get("pairs", []):
        total += len(spec.get("entries", []))
        spare_index = spares / Path(spec["index"]).name
        spare = spares / Path(spec["archive"]).name
        if not spare_index.is_file() or not spare.is_file():
            continue
        try:
            listed = entries(spare_index)
            with spare.open("rb") as handle:
                for item in spec.get("entries", []):
                    entry = listed.get(item["path"])
                    # sizes first: a mod that went in after the backup is
                    # nearly always a different size, and that's no hashing
                    if entry is not None and entry.uncompressed == item.get("size",
                                                                            entry.uncompressed) \
                            and _content(spare, handle, entry) == item["sha"]:
                        found += 1
                        if not every:
                            return True
        except Exception:
            continue
    return bool(total) and found == total


def _cut_back(index: Path, archive: Path, spec: dict) -> None:
    """the archive back to its length from before the mod, when nothing reaches past that now"""
    from patcher.archive.filelist import load

    start, end = int(spec.get("start", 0)), int(spec.get("end", 0))
    try:
        if not archive.is_file() or archive.stat().st_size != end or start >= end:
            return
        reach = max((e.byte_offset + e.stored for e in load(index).entries() if not e.is_stub),
                    default=0)
        if reach <= start:
            before = _stamp(archive)
            with archive.open("r+b") as handle:
                handle.truncate(start)
            _carry(archive, before, start)
    except (OSError, ValueError):
        # dead bytes past the last record cost disk space and nothing else
        pass


# where mods get unpacked to, which names none of them
CONTAINERS = {"", "downloads", "desktop", "documents", "temp", "tmp", "mods", "new folder"}


def _named_by_tools(name: str) -> bool:
    """a folder named by an unpacker or after the game's own: white_imgu, alba_data, chr, c108"""
    name = name.lower()
    return bool(UNPACKED.match(name) or name in (*DATA_DIRS, "sys", "data")
                or name in ("chr", "bg", "gui", "vfx", "sound", "zone", "db", "event", "txtres",
                            "btscene", "movie", "bin", "pc", "npc", "mon", "wea", "exte", "fa")
                or re.fullmatch(r"[a-z]{1,4}\d{3,4}", name))


def mod_name(origin) -> str:
    """
    what a mod is called: an archive's name without the archive or the
    version, a folder's as it is, a loose file's with its extensions, since
    c108.win32.imgb and c108.win32.trb are two mods' worth of name otherwise
    """
    from patcher.addon_sieve import strip_version

    path = Path(origin)
    # a folder an unpacker or the game itself named says nothing about the
    # mod, so the folder it's in does: Serah Dress/white_imgu and Serah
    # Dress/chr are Serah Dress. Not past Downloads, which says even less
    while path.is_dir() and path.parent != path and _named_by_tools(path.name) \
            and path.parent.name.lower() not in CONTAINERS:
        path = path.parent
    name = path.name
    suffix = next((s for s in ARCHIVES if name.lower().endswith(s)), None)
    if suffix is None and not path.is_dir():
        return name
    stem = name[:-len(suffix)] if suffix else name
    # downloaded twice, or the folder copied, it's still the same mod. Not a
    # year in brackets, which is a name
    stem = re.sub(r"(\s?\(\d{1,2}\)| - copy)+$", "", stem, flags=re.I) or stem
    return re.sub(r"[\s_]+", " ", strip_version(stem)).strip(" .-") or "mod"


def explain(found: Look, game: Game, name: str) -> str:
    """why nothing in a drop could go in, said so somebody knows what to do next"""
    other = wrong_game(found, game)
    if other:
        return f"{name}'s files are laid out for {other}. Switch to that game and drop it again."
    if found.clashes:
        (path, _), those = next(iter(found.clashes.items()))
        return (f"{name} has {len(those)} different files for {path}, with nothing to say which "
                "one goes in. Drop just the folder you want.")
    if found.doubled:
        leaf = found.doubled[0].replace("\\", "/").rsplit("/", 1)[-1]
        return (f"{name}'s zip lists {leaf} twice with different contents, so there's no telling "
                "which one is meant. Unpack it, keep the one you want and drop the folder.")
    if found.empty:
        return (f"{name}'s game files are empty, 0 bytes each, which usually means the download "
                "or the unpack didn't finish. Get it again and drop that.")
    if found.whole:
        leaf = _leaf(found.whole[0])
        why = found.whole_why.get(found.whole[0])
        if why:
            return f"{name} replaces {leaf} whole, and {why}."
        return (f"{name} replaces whole archives ({leaf}) rather "
                "than files inside them. Its own instructions are the way to install it.")
    if found.loose:
        leaf = found.loose[0].replace("\\", "/").rsplit("/", 1)[-1]
        return (f"{leaf} has no folders around it, and the game has "
                f"{len(game.leaves[_key(leaf)])} "
                "files by that name. Keep the folders the mod came with so the right one gets "
                "replaced.")
    if found.zones:
        return (f"{name} is zone art, which sits in each zone's own archive. Those aren't "
                "something this can put files into yet.")
    if found.exes:
        return (f"{name} replaces the game's own {_leaf(found.exes[0])}, which stays the one "
                "Steam ships: Steam checks it, and the 4GB patch is made for that one.")
    if found.deltas:
        why = found.delta_why.get(found.deltas[0])
        if why:
            return f"{name} is an xdelta patch, {_leaf(found.deltas[0])}, and {why}."
        return (f"{name} is an xdelta patch, {_leaf(found.deltas[0])}, and there's nothing to "
                "say which of the game's files it's for. Its readme names the file, and "
                "renaming the patch after it lets this find it.")
    if found.saves:
        app = STEAM_APPS.get(game.data)
        return (f"{name} is a saved game, {_leaf(found.saves[0])}. Saves don't go in the game's "
                "folder: copy it into the one yours are in, with the game closed."
                + (f" With Steam Cloud on that's Steam\\userdata\\<your id>\\{app}\\remote."
                   if app else ""))
    if found.layered and not found.picks:
        return (f"{name} is LayeredFS, a loader that reads mods out of a mod_data folder beside "
                "the exe. Mods made for it go into the game's archives here, which needs no "
                "loader, so drop those instead.")
    if found.tools:
        member = found.tools[0]
        return f"{name}'s {_leaf(member)} is {_tool(member)}. Nothing of it goes into the game."
    # ahead of the art, which can be a hook's own data
    if found.blocked:
        return threat_message(_leaf(found.blocked[0]), name)
    if found.x64:
        # after a block: an x86 build Windows Security took is still in the drop
        return (f"{name}'s {_leaf(found.x64[0])} is built for 64-bit, and these games are 32-bit: "
                "beside the exe it would stop the game starting. Get its 32-bit (x86) build and "
                "drop that.")
    if found.reshade_clash:
        return (f"{name} has {len(found.reshade_clash)} different ReShade builds, "
                f"{' and '.join('/'.join(_spelled(m)) for m in found.reshade_clash)}, with nothing "
                "to say which one goes in. Unpack it and drop the one you want with its files.")
    if found.art:
        return (f"{name} ships .dds images, which a tool has to pack into the game's own .imgb "
                "first. Its own instructions say how.")
    if found.unmatched:
        return (f"none of {name}'s {len(found.unmatched)} game files are in this game's "
                "archives. It may be for another game in the series.")
    if found.patches:
        return (f"{name}'s changes are Nova Chrysalia code patches (.nccp), which go in "
                "through NovaFix's add-ons, and this game's NovaFix folder wasn't found.")
    if found.shade_addons:
        leaf = _leaf(found.shade_addons[0])
        if leaf.lower().endswith(".addon64"):
            return (f"{name}'s {leaf} is a ReShade add-on built for 64-bit, and these games are "
                    "32-bit. Get its .addon32 and drop that.")
        return f"{name}'s {leaf} is a ReShade add-on, which goes in once this game has ReShade."
    if found.installers:
        # a .bat is what installs a drop with one, the programs beside it its
        # tools, and one that takes a mod back off is why nothing was played
        bats = [m for m in found.installers if m.lower().endswith((".bat", ".cmd"))]
        leaf = _leaf(next((m for m in bats if _undoes(m, found)), bats[0] if bats else
                          found.installers[0]))
        patcher = next((_leaf(m) for m in found.installers if LARGE_ADDRESS.search(_leaf(m).lower())),
                       None)
        if patcher is not None:
            return (f"{name} is a 4GB patcher, {patcher}. Apply's 4GB patch does the same, so "
                    "there's nothing here to put in.")
        runs = " and the .NET libraries it runs on" if found.managed else ""
        if leaf.lower().endswith((".bat", ".cmd")):
            if _undoes(leaf, found):
                return f"{name}'s {leaf} is for taking a mod back off, so it wasn't played."
            # the drawer plays the rest on a copy of the game first, so
            # getting here is that play coming to nothing
            return f"{name}'s installer, {leaf}, put nothing in when it was played on a copy of " \
                   "the game."
        kind = {".ps1": "a PowerShell script", ".sh": "a shell script"}.get(
            "." + leaf.lower().rsplit(".", 1)[-1], "a program")
        # a program finds the game on its own and changes it where it sits, with
        # nothing kept of what was under, which Remove can't take back out
        said = (f"{name} is {leaf}{runs}, {kind} that changes the game by itself wherever it "
                "finds it, keeping nothing of what was there, so it can't go in from here and "
                "come back out.")
        if leaf.lower().endswith(".exe"):
            # the Packed downloads are a patcher around the same mod as a Nova
            # Chrysalia modpack, which goes in like any other
            said += (" Its page usually has the mod as plain files too, or as a Nova Chrysalia "
                     "modpack (a .ncmp, or the download marked Nova), and either goes in here.")
        return said
    if found.unopened:
        return (f"{name}'s files are in {_leaf(found.unopened[0])} inside it, which "
                + ("needs unpacking first. Unpack it and drop the folder."
                   if found.unopened[0].lower().endswith((".7z", ".rar"))
                   else "won't open here: it's damaged, or locked with a password. Unpack it "
                        "with 7-Zip and drop the folder, or get the mod again."))
    return f"nothing in {name} is a game file or an add-on."


def _leaf(member: str) -> str:
    return member.replace("\\", "/").rsplit("/", 1)[-1]


def _tool(member: str) -> str:
    leaf = _leaf(member).lower()
    return SETTINGS.get(leaf) or next(said for suffix, said in TOOLS.items()
                                      if leaf.endswith(suffix))


def placed(found: Look, members) -> None:
    """
    for a drop laid out by playing its .bat: every file nothing else took goes
    in loose, where the play left it. The folder it was laid out in is the
    game's, so a file's path there is its place, new folders and all, and
    what look() could only guess at, art and names the archives don't list,
    is no guess
    """
    # a program or a .bat it put beside the game is one more file of the
    # mod's there, not an installer of its own still to run
    kept = [m for m in found.tools if _leaf(m).lower() in SETTINGS]
    sure = {*found.unmatched, *found.art, *found.loose, *found.installers,
            *(m for m in found.tools if m not in kept)}
    found.unmatched, found.art, found.loose, found.installers = [], [], [], []
    found.tools = kept
    taken, _ = _busy(found, found.code, found.loaders, list(found.loader_files), found.reshade,
                     list(found.reshade_files), found.layered, found.shade_addons,
                     found.x64_files, found.blocked_files)
    for member, size in members:
        if member in sure or member not in taken:
            found.disk[member] = member
            found.sizes[member] = size


def lost_any(found: Look) -> bool:
    """
    whether left_out() is about part of the mod. A patcher whose files went in
    directly and the .NET it runs on aren't: nothing was meant to go in there
    """
    carried = any(member.startswith(tuple(f"{n}/" for n in found.nested))
                  for member in found.picks.values())
    return bool(found.elsewhere or found.clashes or found.doubled or found.empty
                or found.zones or found.unmatched or found.loose or found.art
                or found.blocked or found.x64 or found.patches or found.unopened
                or found.reshade_clash or found.exes or found.deltas or found.saves
                or found.shade_addons or found.whole or (found.installers and not carried))


def _many(count: int, thing: str) -> str:
    return f"{count} {thing}" + ("" if count == 1 else "s")


def _bats(found: Look) -> list:
    return [m for m in found.installers if m.lower().endswith((".bat", ".cmd"))]


def _undoes(member: str, found: Look) -> bool:
    """whether a drop's .bat is named for taking a mod back off, and so went unplayed"""
    from patcher.mods import sandbox

    return sandbox.undoes(_leaf(member).rsplit(".", 1)[0])


def left_out(found: Look, played: str = "", bat: str = "") -> str:
    """
    what an install that went in didn't take, or "". `played` is what its
    .bat came to when it was played on a copy of the game, when it was
    """
    said = []
    if found.elsewhere:
        other = DATA_DIRS[found.homes.most_common(1)[0][0]]
        one = len(found.elsewhere) == 1
        said.append(f"{_many(len(found.elsewhere), 'file')} for {other}, which "
                    f"{'goes' if one else 'go'} in from that game")
    if found.clashes:
        said.append(f"{_many(len(found.clashes), 'file')} it has more than one version of, "
                    "with nothing to say which")
    if found.doubled:
        said.append(f"{_many(len(found.doubled), 'file')} its zip lists twice with different "
                    "contents")
    if found.empty:
        said.append(f"{_many(len(found.empty), 'empty file')}, which usually means an unpack "
                    "that didn't finish")
    if found.zones:
        said.append(f"{_many(len(found.zones), 'zone file')}, which "
                    f"{'sits' if len(found.zones) == 1 else 'sit'} in archives this doesn't fill "
                    "yet")
    if found.unmatched:
        said.append(f"{_many(len(found.unmatched), 'file')} the game doesn't have")
    for member in found.whole:
        said.append(f"{_leaf(member)}, a whole archive"
                    + (f": {found.whole_why[member]}" if member in found.whole_why else
                       " this can't put in"))
    if found.loose:
        said.append(f"{_many(len(found.loose), 'file')} with no folders to say where "
                    f"{'it goes' if len(found.loose) == 1 else 'they go'}")
    if found.art:
        said.append(f"{_many(len(found.art), '.dds image')} that "
                    f"{'needs' if len(found.art) == 1 else 'need'} packing first")
    if found.blocked:
        more = len(found.blocked_files)
        said.append(f"{_leaf(found.blocked[0])}, which Windows Security blocked as a threat"
                    + (f", and the {_many(more, 'file')} that "
                       f"{'goes' if more == 1 else 'go'} with it" if more else ""))
    if found.x64:
        more = len(found.x64_files)
        said.append(f"{', '.join('/'.join(_spelled(m)) for m in found.x64)}"
                    + (f" and the {more} file{'' if more == 1 else 's'} with it" if more else "")
                    + ", built for 64-bit, which these 32-bit games can't load")
    if found.patches:
        said.append(f"{len(found.patches)} Nova Chrysalia code patch"
                    f"{'' if len(found.patches) == 1 else 'es'}, which go in through NovaFix")
    if found.exes:
        said.append(f"{_leaf(found.exes[0])}, the game's own exe, which stays the one Steam ships")
    for member in found.deltas:
        said.append(f"{_leaf(member)}, an xdelta patch: "
                    + (found.delta_why.get(member) or "nothing says which file it's for"))
    if found.saves:
        said.append(f"{_many(len(found.saves), 'saved game')}, which "
                    f"{'goes' if len(found.saves) == 1 else 'go'} in the folder your own saves "
                    "are in")
    for member in found.tools:
        said.append(f"{_leaf(member)}, {_tool(member)}")
    if found.layered:
        said.append(f"{' and '.join(_leaf(m) for m in found.layered[:2])}, LayeredFS's loader, "
                    "which isn't needed with what it reads out of mod_data in the game's "
                    "archives")
    if found.shade_addons:
        said.append(f"{_leaf(found.shade_addons[0])}, a ReShade add-on, "
                    + ("built for 64-bit" if found.shade_addons[0].lower().endswith(".addon64")
                       else "with no ReShade in for it"))
    if found.reshade_clash:
        said.append(f"{len(found.reshade_clash)} different ReShade builds, "
                    f"{' and '.join('/'.join(_spelled(m)) for m in found.reshade_clash)}, with "
                    "nothing to say which")
    carried = any(member.startswith(tuple(f"{n}/" for n in found.nested))
                  for member in found.picks.values())
    runs = (f" and the .NET {'library' if len(found.managed) == 1 else 'libraries'} it runs on"
            if found.managed and found.installers else "")
    if found.installers and carried:
        # a patcher and the zip it unpacks into the game: the zip went in
        said.append(f"{_leaf(found.installers[0])}{runs}, its own installer, which wasn't needed "
                    "since the files it carries went in directly")
    elif found.installers and played:
        from patcher.mods import sandbox

        bat = bat or next((m for m in found.installers if m.lower().endswith((".bat", ".cmd"))),
                          found.installers[0])
        said.append(f"{_leaf(bat)}, which {played}" if played == sandbox.UNDID else
                    f"{_leaf(bat)}{runs}, its installer, which {played}")
    elif _bats(found) and all(_undoes(m, found) for m in _bats(found)):
        from patcher.mods import sandbox

        # its uninstaller, with ff13tool or 7-Zip beside it to run, but an
        # installer of another kind is said as one
        said.append(f"{' and '.join(_leaf(m) for m in _bats(found)[:2])}, for taking it back "
                    "off, which Remove does here")
        other = next((m for m in found.installers if not m.lower().endswith((".bat", ".cmd"))
                      and not sandbox.known_tool(_leaf(m))), None)
        if other is not None:
            said.append(f"{_leaf(other)}{runs}, which wasn't run. If its readme says it does "
                        "more than put files in, that part isn't done")
    elif found.installers:
        said.append(f"{_leaf(found.installers[0])}{runs}, which wasn't run. If its readme says it "
                    "does more than put files in, that part isn't done")
    elif found.managed:
        said.append(f"{', '.join(_leaf(m) for m in found.managed)}, .NET code the game never "
                    "loads")
    for member in found.unopened:
        said.append(f"{_leaf(member)}, which "
                    + ("needs unpacking first" if member.lower().endswith((".7z", ".rar"))
                       else "won't open"))
    if not said:
        return ""
    return "Left out: " + "; ".join(said) + "."
