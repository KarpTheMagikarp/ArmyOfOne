"""
Turning a dropped mod archive into a folder NovaFix will load.

Nothing is extracted into the game folder. The archive is staged, walked, and
only files passing every check move into their own folder under addons/; the
staging dir goes either way. Each check matches something NovaFix does:

  extensions   it loads .dll and .asi. .ini comes along because the dll reads it
  bitness      it skips anything not I386, so a 64-bit dll "installs" and never runs
  reserved     d3d9.dll, d3d9_chain.dll, dxvk.dll and ReShade32.dll are renderer
               backends to it, so one of those is a ReShade or DXVK download in
               the wrong place

One folder per mod, named after the archive, so uninstall is a folder delete
and two mods with the same filename stay apart.
"""

import hashlib
import json
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from patcher import zipnames
from patcher.access import is_threat_block, threat_message, writable_or_reason, write_failure
from patcher.download import NotEnoughSpace
from patcher.fileops import scratch_near, write_atomic

PAYLOAD_EXTENSIONS = {".dll", ".asi", ".ini"}
CODE_EXTENSIONS = {".dll", ".asi"}

# NovaFix treats these as renderer backends rather than add-ons
RESERVED_NAMES = {"d3d9.dll", "d3d9_chain.dll", "dxvk.dll", "reshade32.dll"}

# archive noise that is never a payload
JUNK_PARTS = {"__macosx", ".ds_store", "thumbs.db"}

IMAGE_FILE_MACHINE_I386 = 0x014C

# what we installed, kept inside the addon folder rather than in global state:
# delete the folder and the record goes with it, which is the behaviour a user
# expects from something that looks like just a folder of files.
RECORD_NAME = ".patcher-addon.json"

# Nexus filenames end in id and version hyphen groups, e.g.
# "Better Lighting-217-1-2-0-1699999999.zip". Three or more groups, or
# "FFXIII-2" gets read as "FFXIII" plus a version. Only affects the folder
# name; whether it's an update is decided by what's inside.
VERSION_TAIL = re.compile(r"(?:[-_]\d+){3,}$")

# A second, safer version shape: a v-prefixed token, or a dotted decimal.
# "Better Lighting v1.2" and "Some Mod 2.0" are versions; "FFXIII-2",
# "Chapter-13" and "Mod_2024" are not, and neither has a 'v' or a dot to
# confuse this. Bare trailing digits are deliberately NOT matched here, which
# is what keeps those three intact.
VERSION_TOKEN = re.compile(
    r"[\s._-]*(?:"
    r"v\.?\d+(?:[._]\d+)*"          # v1, v1.2, v_1_2
    r"|\d+\.\d+(?:\.\d+)*"          # 1.2, 2.0.1
    r")"
    r"(?:[\s._-]*(?:final|fixed|release|beta|alpha|re-?upload))*$",
    re.IGNORECASE)

# dll stems too generic to name a folder after
GENERIC_STEMS = {"hook", "main", "mod", "plugin", "core", "patch", "loader",
                 "asi", "dll", "script", "init", "setup", "install"}

# what Windows actually forbids in a name, rather than what ASCII happens to
# contain. The old allowlist ([^A-Za-z0-9._-]) collapsed any fully non-Latin
# archive name to a single underscore, which .strip() then ate, so every
# Russian and Chinese mod installed into addons/mod and overwrote the last
# one's record. remove_mod("mod") then deleted all of them at once.
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\s]+')

# names the filesystem refuses outright, with or without an extension
DEVICE_NAMES = {"con", "prn", "aux", "nul",
                *(f"com{n}" for n in range(1, 10)),
                *(f"lpt{n}" for n in range(1, 10))}


class SieveError(Exception):
    pass


def _ensure_room(archive: Path, staging: Path) -> None:
    """
    checks the destination has room before a byte is written.

    The extracted size comes out of the zip central directory, so this costs
    one seek rather than a trial extraction. An archive we cannot measure
    (tar, mostly) falls back to assuming it triples, which is generous for
    the dlls and inis this deals with and cheap to be wrong about.
    """
    from patcher.download import ensure_space, extracted_size

    try:
        needed = extracted_size(archive)
    except Exception:
        needed = archive.stat().st_size * 3
    ensure_space(staging, needed)


# Nexus's newer download names: id, version and upload time, like
# Less Grinding-73-1-3-2026-08-27T12-00-00Z, often with spaces after a rename.
# Only a name ending in a time loses its id and version (an epoch after dashes
# is the older spelling VERSION_TAIL reads). Anything time-shaped past the date
# goes too: .NET's seven digit fraction and offset, a compact T171300, and the
# colon lookalikes Windows folder names use.
STAMP = re.compile(r"[\s._-]+\d{4}([-_‐-―−])\d{2}\1\d{2}"
                   r"(?:[T\s_]\d[\d\s.,:+_\-TZ\u02d0\u2236\ua789\ufe55\uff1a]*)?$"
                   r"|\s[\s._-]*\d{9,11}$", re.I)
NEXUS_TAIL = re.compile(r"[\s_-]+\d{1,6}((?:[\s_-]+\d+(?:\.\d+)*)+)$")
# and since June 2026, {FileName}_{Version}_{slug}: SkyUI_6.11_trg5df. The slug's
# a stopgap Nexus says goes within the year, leaving SkyUI_6.11, which
# VERSION_TOKEN already reads
SLUG_TAIL = re.compile(r"_(v?\d+(?:\.\d+)*[A-Za-z]{0,3})_([a-z\d]{6})$")
# a tweak picked out of a pack goes in as "Pack (Tweak)", a second by one name
# as "Pack (2)", and both land after whatever the download's name ended in
BRACKETED = re.compile(r"(?:\s*\([^()]*\))+$")
# id, version and a date anywhere past the name, for whatever a mod manager or
# a rename left after the date. The date's the tell, so nothing else needs it,
# and it's read with any dash Unicode has, since not every tool writes "-"
LOOSE = re.compile(r"[\s_-]+\d{1,6}((?:[\s_-]+\d+(?:\.\d+)*)+)[\s._-]+\d{4}([-_‐-―−])\d{2}\2\d{2}(?!\d)")


def stamped(text: str) -> tuple:
    """(the name, its version) off a name ending in Nexus's id, version and time"""
    after = BRACKETED.search(text)
    if after and after.start():
        head = text[:after.start()]
        name, said = stamped(head)
        return (name + after.group(0) if name != head else text), said
    stamp = STAMP.search(text)
    loose = None if stamp else LOOSE.search(text)
    if loose and loose.start():
        # past the date only a tweak's bracket is worth keeping
        kept = "".join(f" {b}" for b in re.findall(r"\([^()]*\)", text[loose.end():]))
        return text[:loose.start()] + kept, ".".join(re.split(r"[\s_-]+", loose.group(1).strip(" _-")))
    if not stamp:
        slug = SLUG_TAIL.search(text)
        if slug and not slug.group(2).isdecimal():
            return text[:slug.start()], slug.group(1).lstrip("vV")
        return text, ""
    rest = text[:stamp.start()]
    tail = NEXUS_TAIL.search(rest)
    if not tail:
        return rest, ""
    return rest[:tail.start()], ".".join(re.split(r"[\s_-]+", tail.group(1).strip(" _-")))


def strip_version(stem: str) -> str:
    """
    the Nexus tail and then the version off a name, each only when something
    recognisable is left. Glued on or not: this is the name a mod's updates
    are matched by, so "CoolModv1.2" and "CoolModv1.3" have to be one, and
    the price is "Battle Speed x1.5" going in, and showing, as "Battle Speed x"
    """
    bare = stamped(stem)[0]
    if bare.strip(" ._-"):
        stem = bare
    for pattern in (VERSION_TAIL, VERSION_TOKEN):
        trimmed = pattern.sub("", stem)
        if trimmed.strip(" ._-"):
            stem = trimmed
    return stem


def normalise_mod_name(archive_name: str) -> str:
    """
    the folder name for a mod, derived from its archive filename.

    strips Nexus's trailing id/version groups, collapses whitespace to
    underscores and drops anything that would be awkward in a path. never
    returns empty: an archive named entirely of digits still needs somewhere
    to live.
    """
    stem = Path(archive_name).name
    for suffix in (".tar.gz", ".tar.xz", ".tar.bz2"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    else:
        stem = Path(stem).stem

    # only a trim that leaves something recognisable, or a mod called "2013"
    # would normalise away to nothing
    stem = strip_version(stem)

    stem = UNSAFE.sub("_", stem.strip())
    # windows silently drops trailing dots and spaces, so a folder created as
    # "Mod." is opened as "Mod" and every later lookup misses it
    stem = stem.strip().strip("._-").rstrip(". ")
    if stem.split(".")[0].lower() in DEVICE_NAMES:
        stem = f"{stem}_mod"
    return stem or "mod"


def folder_name_for(result: "SiftResult") -> str:
    """
    the folder a new mod gets, decided without asking. The archive name first,
    since that's what the author called it, and the main dll's stem when the
    name's junk ("betterlighting_FINAL_reupload.zip" with BetterLighting.dll).
    Cosmetic only: updates are matched by payload.
    """
    from_archive = result.mod_name
    code = [p.name for p in result.payloads if p.is_code]
    if not code:
        return from_archive

    stem = normalise_mod_name(sorted(code)[0])
    if stem.lower() in GENERIC_STEMS or len(stem) < 4:
        return from_archive

    def bare(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    # the archive name is the dll's name plus junk, so take the dll's. Only
    # when it's strictly tidier: same name means the archive has the author's
    # own capitalisation.
    if bare(stem) and bare(stem) in bare(from_archive) and bare(stem) != bare(from_archive):
        return stem

    # judged against the filename AS DROPPED, not the cleaned form: the
    # cleaning is what removes the evidence. "Mod v1.4 FINAL.zip" normalises to
    # "Mod", which looks like a perfectly good title until you see the original.
    raw = result.archive_name or from_archive
    noisy = re.search(r"\d", raw) or re.search(
        r"final|reupload|re_upload|download|copy|new|latest", raw, re.IGNORECASE)
    return stem if noisy else from_archive


def is_junk(member: Path) -> bool:
    return any(part.lower() in JUNK_PARTS or part.startswith("._")
               for part in member.parts)


# past this a stub pointing at the headers is a file that isn't a PE
PE_REACH = 1 << 20


def pe_head(handle) -> bytes:
    """
    a file's bytes from the start to the end of its PE headers, wherever
    e_lfanew puts them. They're usually in the first page, but a long DOS
    stub pushes them past it, and a fixed read called those dlls not Windows
    binaries at all
    """
    head = handle.read(0x40)
    if len(head) < 0x40 or head[:2] != b"MZ":
        return head
    # the signature, the file header and a PE32+'s optional header up to the
    # end of its data directories, which is the furthest pe_kind reads
    end = struct.unpack_from("<I", head, 0x3C)[0] + 24 + 240
    if end > PE_REACH:
        return head
    return head + handle.read(max(0, end - len(head)))


def pe_kind(data: bytes) -> dict | None:
    """
    {"machine", "dll", "managed"} off a PE's headers, or None for anything that
    isn't one. Managed is a .NET assembly: it carries a CLR header and does
    nothing loaded the way Windows' loader or NovaFix loads a DLL, so one in a
    mod is the mod's own installer and not something for the game
    """
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset + 24 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        return None
    machine, _, _, _, _, _, flags = struct.unpack_from("<HHIIIHH", data, offset + 4)
    optional, managed = offset + 24, False
    if optional + 2 <= len(data):
        # the data directories start 96 bytes into a PE32's optional header and
        # 112 into a PE32+'s, with their count in the 4 bytes before them
        start = {0x10B: 96, 0x20B: 112}.get(struct.unpack_from("<H", data, optional)[0])
        if start is not None and optional + start <= len(data):
            count = struct.unpack_from("<I", data, optional + start - 4)[0]
            clr = optional + start + 14 * 8
            managed = count > 14 and clr + 8 <= len(data) \
                and any(struct.unpack_from("<II", data, clr))
    return {"machine": machine, "dll": bool(flags & 0x2000), "managed": managed}


def pe_exports(data: bytes) -> set:
    """
    the names a whole PE exports, off its export directory, or an empty set
    for anything that isn't one or that the headers don't add up for
    """
    try:
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[offset:offset + 4] != b"PE\0\0":
            return set()
        sections, optional_size = struct.unpack_from("<H12xH", data, offset + 6)
        optional = offset + 24
        start = {0x10B: 96, 0x20B: 112}.get(struct.unpack_from("<H", data, optional)[0])
        if start is None:
            return set()
        rva = struct.unpack_from("<I", data, optional + start)[0]
        table = [struct.unpack_from("<8xIIII", data, optional + optional_size + 40 * n)
                 for n in range(sections)]

        def at(address):
            for size, base, raw_size, raw in table:
                if base <= address < base + max(size, raw_size):
                    return raw + address - base
            raise ValueError(address)

        if not rva:
            return set()
        # NumberOfNames, then past AddressOfFunctions to AddressOfNames
        count, names = struct.unpack_from("<24xI4xI", data, at(rva))
        names = at(names)
        out = set()
        for n in range(min(count, 1 << 16)):
            name = at(struct.unpack_from("<I", data, names + 4 * n)[0])
            out.add(data[name:data.index(b"\0", name)].decode("ascii", "replace"))
        return out
    except (struct.error, ValueError):
        return set()


def pe_imports(data: bytes) -> list:
    """
    the DLLs a whole PE loads, off its import directory (delay-loaded ones
    too), in the order it lists them, or [] for anything that isn't one
    """
    try:
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[offset:offset + 4] != b"PE\0\0":
            return []
        sections, optional_size = struct.unpack_from("<H12xH", data, offset + 6)
        optional = offset + 24
        start = {0x10B: 96, 0x20B: 112}.get(struct.unpack_from("<H", data, optional)[0])
        if start is None:
            return []
        table = [struct.unpack_from("<8xIIII", data, optional + optional_size + 40 * n)
                 for n in range(sections)]

        def at(address):
            for size, base, raw_size, raw in table:
                if base <= address < base + max(size, raw_size):
                    return raw + address - base
            raise ValueError(address)

        out = []
        # the import directory's descriptors are 20 bytes with the name 12 in,
        # the delay-load directory's 32 with it 4 in
        for entry, width, name_at in ((1, 20, 12), (13, 32, 4)):
            rva = struct.unpack_from("<I", data, optional + start + 8 * entry)[0]
            if not rva:
                continue
            spot = at(rva)
            for _ in range(1024):
                name = struct.unpack_from("<I", data, spot + name_at)[0]
                if not name:
                    break
                text = at(name)
                dll = data[text:data.index(b"\0", text)].decode("ascii", "replace")
                if dll and dll not in out:
                    out.append(dll)
                spot += width
        return out
    except (struct.error, ValueError, IndexError):
        return []


def reshade_build(data: bytes) -> int | None:
    """
    the major version of a ReShade dll, 0 when it doesn't say, or None for
    any other dll. Every build exports ReShadeVersion, which is how NovaFix
    tells it apart from a plain d3d9.dll too
    """
    if b"ReShadeVersion" not in data or "ReShadeVersion" not in pe_exports(data):
        return None
    # VS_FIXEDFILEINFO, found by its signature: the file version's top word
    at = data.find(b"\xbd\x04\xef\xfe")
    if at < 0 or at + 12 > len(data):
        return 0
    return struct.unpack_from("<I", data, at + 8)[0] >> 16


def pe_machine(path: Path) -> int | None:
    """
    the PE machine field, or None if this is not a PE at all, or can't be read.

    read from the file rather than trusted from a folder name: archives ship
    x86 and x64 directories with the same filenames inside, and plenty label
    them wrongly.
    """
    try:
        with path.open("rb") as handle:
            kind = pe_kind(pe_head(handle))
    except OSError:
        return None
    return kind["machine"] if kind else None


@dataclass
class Payload:
    source: Path                 # inside staging
    name: str                    # the filename it will land as
    kind: str                    # "code" or "config"
    machine: int | None = None

    @property
    def is_code(self) -> bool:
        return self.kind == "code"


@dataclass
class SiftResult:
    mod_name: str
    # the filename as dropped. mod_name has already had version tails and
    # upload noise stripped, so it cannot be used to judge how noisy the
    # original was.
    archive_name: str = ""
    payloads: list = field(default_factory=list)
    rejected: list = field(default_factory=list)   # (name, reason)
    ignored: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.payloads)

    def as_dict(self) -> dict:
        return {
            "modName": self.mod_name,
            "files": [{"name": p.name, "kind": p.kind} for p in self.payloads],
            "rejected": [{"name": n, "reason": r} for n, r in self.rejected],
            "ignored": len(self.ignored),
        }


def _extract(archive: Path, destination: Path) -> None:
    """unpacks into staging, or raises with something a person can act on"""
    import tarfile
    import zipfile

    from patcher.ingest import try_system_extract

    name = archive.name.lower()
    destination.mkdir(parents=True, exist_ok=True)

    if archive.is_dir():
        shutil.copytree(archive, destination, dirs_exist_ok=True)
        return
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zipnames.extract_all(zf, destination)
        return
    if any(name.endswith(ext) for ext in (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")):
        # the filter is the only thing stopping a tar writing outside the
        # folder or planting a link, so no filter means no tar, not a tar
        # extracted without one
        if not hasattr(tarfile, "data_filter"):
            raise SieveError("the patcher can't open .tar archives safely. Extract it "
                             "yourself, then use Choose a folder.")
        with tarfile.open(archive, mode="r:*") as tf:
            tf.extractall(destination, filter="data")
        return

    # .rar and .7z: one attempt with the system extractor before giving up
    if try_system_extract(archive, destination) is None:
        raise SieveError(
            f"Windows can't open {archive.suffix} on its own. Extract it yourself, "
            "then drop the folder in instead."
        )


def sift(staging: Path, archive_name: str) -> SiftResult:
    """
    walks an extracted archive and decides what is installable.

    read-only over staging. every rejection carries a reason, because "that
    didn't work" on a mod install tells the user nothing about what to try.
    """
    result = SiftResult(mod_name=normalise_mod_name(archive_name), archive_name=archive_name)
    by_name: dict = {}

    for path in sorted(staging.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(staging)
        if is_junk(relative):
            result.ignored.append(str(relative))
            continue

        extension = path.suffix.lower()
        if extension not in PAYLOAD_EXTENSIONS:
            result.ignored.append(str(relative))
            continue

        leaf = path.name
        if leaf.lower() in RESERVED_NAMES:
            result.rejected.append((
                leaf,
                f"{leaf} is a renderer, not an add-on. NovaFix loads it from the game "
                "folder rather than addons, so this archive needs installing a different way"
            ))
            continue

        if extension in CODE_EXTENSIONS:
            try:
                with path.open("rb") as handle:
                    kind = pe_kind(pe_head(handle))
            except OSError as e:
                # a file antivirus took while it was being staged reads as
                # anything but what it is, and "isn't a Windows binary" sent
                # somebody looking for a corrupt download
                result.rejected.append((leaf, threat_message(leaf) if is_threat_block(e)
                                        else f"{leaf} couldn't be read ({e.strerror or e})"))
                continue
            if kind is None:
                result.rejected.append((leaf, f"{leaf} isn't a Windows binary"))
                continue
            if kind["managed"]:
                result.rejected.append((leaf, f"{leaf} is a .NET library, which is part of a "
                                              "mod's own installer rather than anything NovaFix "
                                              "loads"))
                continue
            payload = Payload(source=path, name=leaf, kind="code", machine=kind["machine"])
        else:
            payload = Payload(source=path, name=leaf, kind="config")

        # archives ship x86 and x64 copies of the same filename. keep the
        # 32-bit one rather than whichever the walk happened to reach last.
        existing = by_name.get(leaf.lower())
        if existing is None:
            by_name[leaf.lower()] = payload
            continue
        if existing.machine != IMAGE_FILE_MACHINE_I386 and payload.machine == IMAGE_FILE_MACHINE_I386:
            by_name[leaf.lower()] = payload
            result.ignored.append(str(existing.source.relative_to(staging)))
        else:
            result.ignored.append(str(relative))

    for payload in by_name.values():
        if payload.is_code and payload.machine != IMAGE_FILE_MACHINE_I386:
            result.rejected.append((
                payload.name,
                f"{payload.name} is 64-bit. These games are 32-bit, so NovaFix would skip it"
            ))
            continue
        result.payloads.append(payload)

    # a config with no code beside it is not a mod, it is a stray settings file
    if result.payloads and not any(p.is_code for p in result.payloads):
        for payload in result.payloads:
            result.rejected.append((payload.name, "no .dll or .asi in the archive to go with it"))
        result.payloads = []

    return result


def find_existing(addons_dir: Path, result: "SiftResult") -> Path | None:
    """
    the folder this mod already lives in, if it does. Matched on the code
    files inside, since "Mod v1.2.zip" and "Mod v1.3.zip" ship the same dll
    and two folders would load the hook twice. Only folders we installed, not
    one someone built by hand.
    """
    root = Path(addons_dir)
    if not root.is_dir():
        return None

    wanted = {p.name.lower() for p in result.payloads if p.is_code}
    if not wanted:
        return None

    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        record = read_record(folder)
        if not record:
            continue
        installed = {name.lower() for name in record.get("files", {})
                     if Path(name).suffix.lower() in CODE_EXTENSIONS}
        if installed & wanted:
            return folder
    return None


def read_record(folder: Path) -> dict:
    path = Path(folder) / RECORD_NAME
    if not path.is_file():
        return {}
    try:
        record = json.loads(path.read_bytes())
    # ValueError rather than JSONDecodeError: json.loads on bytes raises
    # UnicodeDecodeError for a file that is not valid utf-8, and that is a
    # SIBLING of JSONDecodeError under ValueError, not a subclass. the
    # narrower catch let a hand-edited config take the whole app down.
    except (ValueError, OSError):
        return {}
    # hand-edited into some other shape, it records nothing. Everything that
    # reads one goes straight to .get("files"), and a null there blanked the
    # whole drawer
    if not isinstance(record, dict):
        return {}
    if not isinstance(record.get("files"), dict):
        record["files"] = {}
    return record


def _copy_atomic(source: Path, destination: Path) -> None:
    """
    copy that never leaves a half written file where a whole one was.
    copyfile truncates then fills, so a failed update leaves a short dll for
    NovaFix to load. write_atomic writes a sibling and renames.
    """
    write_atomic(destination, source.read_bytes())


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(result: SiftResult, addons_dir: Path) -> dict:
    """
    moves the payloads into addons/<mod name>/. Code is replaced outright. An
    .ini only if it's byte identical to what we installed, since the user may
    have edited it; otherwise the new one lands beside it as .ini.new.
    """
    if not result.ok:
        raise SieveError("nothing installable in that archive")

    existing = find_existing(addons_dir, result)
    target = existing if existing is not None else Path(addons_dir) / folder_name_for(result)

    # probe before the first byte: a mod half copied with no
    # .patcher-addon.json reads as unmanaged and can't be removed from the
    # drawer.
    refusal = writable_or_reason(target if target.is_dir() else Path(addons_dir))
    if refusal:
        raise SieveError(refusal)

    # whether WE created the folder, which is not the same as whether
    # find_existing recognised one. A folder with an empty or unreadable record
    # is not "existing" to the matcher but is very much already on the user's
    # disk, and rolling back by deleting it would take their files with it.
    fresh = not target.exists()

    target.mkdir(parents=True, exist_ok=True)
    record = read_record(target)
    installed_hashes = record.get("files", {})

    installed, preserved = [], []
    try:
        for payload in result.payloads:
            destination = target / payload.name

            if payload.kind == "config" and destination.is_file():
                previous = installed_hashes.get(payload.name)
                current = _digest(destination)
                if previous is None or current != previous:
                    # either the user edited it, or they put it there themselves.
                    # both mean it is not ours to overwrite.
                    _copy_atomic(payload.source,
                                 destination.with_suffix(destination.suffix + ".new"))
                    preserved.append(payload.name)
                    continue

            _copy_atomic(payload.source, destination)
            installed.append(payload.name)
    except OSError as e:
        # take back what landed. _copy_atomic means an existing file is the
        # old one or the new one, never short, and a brand new folder goes
        # entirely.
        for name in installed:
            try:
                (target / name).unlink()
            except OSError:
                pass
        if fresh:
            shutil.rmtree(target, ignore_errors=True)
        raise SieveError(write_failure(e, target)) from e

    updated = dict(installed_hashes)
    for payload in result.payloads:
        landed = target / payload.name
        if payload.name in installed and landed.is_file():
            updated[payload.name] = _digest(landed)

    record = json.dumps({"mod": result.mod_name, "files": updated}, indent=2)
    (target / RECORD_NAME).write_text(record, encoding="utf-8")

    return {
        "ok": True,
        "modName": target.name,
        "matchedExisting": existing is not None,
        "path": str(target),
        "installed": installed,
        "preserved": preserved,
        "updated": bool(installed_hashes),
    }


def ingest_archive(archive: Path, addons_dir: Path) -> dict:
    """
    the whole flow: stage, sift, install, and destroy the staging directory
    whether or not any of it worked.
    """
    archive = Path(archive)
    if not archive.exists():
        return {"ok": False, "error": f"couldn't find {archive.name}"}

    # beside the archive, not on C:. a texture pack dropped from a D: drive
    # has no business filling the system drive to unpack itself.
    staging = Path(tempfile.mkdtemp(prefix="temp_mod_staging_",
                                    dir=scratch_near(archive.parent)))
    try:
        try:
            _ensure_room(archive, staging)
            _extract(archive, staging)
        except NotEnoughSpace as e:
            return {"ok": False, "error": str(e)}
        except SieveError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"couldn't open {archive.name}: {e}"}

        result = sift(staging, archive.name)
        if not result.ok:
            return {
                "ok": False,
                "error": "No compatible mod files found in archive.",
                **result.as_dict(),
            }
        return dict(install(result, Path(addons_dir)), **{"rejected": result.as_dict()["rejected"]})
    finally:
        # readmes, screenshots and __MACOSX folders never reach the game folder
        # and never linger on disk
        shutil.rmtree(staging, ignore_errors=True)


def _linked(path: Path) -> bool:
    # a junction is a link wearing another reparse tag, and is_junction is 3.12's
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _tidy_plan(folder: Path) -> list | None:
    """
    what tidying a leftover takes out, .dlls last, or None when the folder
    isn't one. Hiding and tidying both go by this, so a folder is never off
    the list while still on disk: anything else in it, an edited file, an
    .ini.new beside one, or a link, and it's listed as it always was
    """
    folder = Path(folder)
    if _linked(folder) or not folder.is_dir():
        return None
    files = read_record(folder).get("files", {})
    try:
        there = [p for p in folder.iterdir() if p.name != RECORD_NAME]
        if not files or any(_linked(p) or not p.is_file() or p.name not in files for p in there):
            return None
        code = [p for p in there if p.suffix.lower() in CODE_EXTENSIONS]
        if not code:
            return None
        # the cheap read first, so a real add-on is never hashed to find out
        for path in code:
            with path.open("rb") as handle:
                kind = pe_kind(pe_head(handle))
            if kind is None or not kind["managed"]:
                return None
        if any(files[p.name] != _digest(p) for p in there):
            return None
    except OSError:
        return None
    # one the running game has open stays, and keeps the folder a leftover for next time
    return sorted(there, key=lambda p: p.suffix.lower() in CODE_EXTENSIONS)


def leftover(folder: Path) -> bool:
    """
    a folder this app filled with nothing but .NET libraries. An older drawer
    took a patcher's own code for add-ons (Red Rose's LocateFile.dll and
    WhiteBinTools.dll), and NovaFix maps them and names them in its popup
    without them doing anything, so they're nobody's mod
    """
    return _tidy_plan(folder) is not None


def tidy_leftovers(addons_dir: Path) -> list:
    """takes those folders out, and says which went"""
    root, gone = Path(addons_dir), []
    if not root.is_dir():
        return gone
    for folder in sorted(root.iterdir()):
        plan = _tidy_plan(folder)
        if plan is None:
            continue
        try:
            for path in plan:
                path.unlink()
            (folder / RECORD_NAME).unlink()
            folder.rmdir()
        except OSError:
            continue
        gone.append(folder.name)
    return gone


def installed_mods(addons_dir: Path, ours: set | None = None) -> list:
    """
    what's currently in addons/, for the UI to list. ours names folders the
    patcher installs itself, which are left out so nobody's offered a Remove
    button that quietly breaks their game.
    """
    root = Path(addons_dir)
    if not root.is_dir():
        return []
    skip = ours or set()
    mods = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if folder.name in skip or leftover(folder):
            continue
        record = read_record(folder)
        files = sorted(p.name for p in folder.iterdir()
                       if p.is_file() and p.name != RECORD_NAME)
        mods.append({"name": folder.name, "path": str(folder), "files": files,
                     "managed": bool(record)})
    return mods


def remove_mod(addons_dir: Path, mod_name: str) -> dict:
    """
    deletes one mod's folder.

    only folders we installed, identified by their record. a directory someone
    created by hand is theirs, and a mod manager that deletes those is a mod
    manager people uninstall.
    """
    target = Path(addons_dir) / mod_name
    if not target.is_dir():
        return {"ok": False, "error": f"{mod_name} isn't installed"}
    if not read_record(target):
        return {"ok": False,
                "error": f"{mod_name} wasn't installed by this patcher, so it won't be removed"}
    shutil.rmtree(target)
    return {"ok": True, "removed": mod_name}
