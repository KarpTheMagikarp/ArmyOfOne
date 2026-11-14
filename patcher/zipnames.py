"""
Recovering member names from zips that lied about their encoding.

Without the UTF-8 flag, the spec says CP437 and Python obeys, so a Russian mod
comes out as box-drawing glyphs. Explorer's Send to > Compressed folder wrote
unflagged UTF-8 for years. CP437 maps all 256 bytes, so encoding the mangled
name back gives the original bytes, and if those are valid UTF-8 we have the
real name.
"""

import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

UTF8_FLAG = 0x800


def repair(info: zipfile.ZipInfo) -> str:
    """
    The member's real name, undoing a wrong CP437 decode where there was one.

    Returns the name unchanged whenever there is any doubt. A wrong guess here
    renames a file the caller then cannot open, which is worse than a member
    with an ugly name.
    """
    name = info.filename
    if info.flag_bits & UTF8_FLAG or name.isascii():
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        # not a CP437 decode after all: something already handed us a real
        # unicode name, so leave it alone
        return name
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        # genuinely CP437, or some other codepage we have no way to identify.
        # the stdlib's answer stands.
        return name
    # a pure-ASCII result means the bytes were ASCII all along and the cp437
    # decode was a no-op, so there is nothing to correct
    return decoded if not decoded.isascii() else name


def names(zf: zipfile.ZipFile) -> dict[str, str]:
    """
    repaired name -> the name zf.open() still expects. A repaired name that
    isn't unique is left out, not last-write-wins, since the wrong member's
    bytes at the right offset in white_img fails with no error anywhere.
    """
    counts: dict[str, int] = {}
    for info in zf.infolist():
        fixed = repair(info)
        counts[fixed] = counts.get(fixed, 0) + 1
    return {repair(info): info.filename
            for info in zf.infolist() if counts[repair(info)] == 1}


_ILLEGAL = re.compile(r'[<>:"|?*\x00-\x1f]')

# names the filesystem refuses outright, with or without an extension. a file
# written as CON.zip cannot be opened afterwards by any name.
_DEVICE_NAMES = {"con", "prn", "aux", "nul",
                 *(f"com{n}" for n in range(1, 10)),
                 *(f"lpt{n}" for n in range(1, 10))}


def windows_safe(part: str) -> str:
    """
    a single path component Windows will accept: characters NTFS refuses get
    replaced, and trailing dots and spaces go, since Windows drops those
    silently and the file wouldn't be where we recorded it.
    """
    cleaned = _ILLEGAL.sub("_", part).rstrip(". ")
    if not cleaned:
        return "_"
    # DOS device names, which the two sibling helpers in this module already
    # escape and this one did not. Windows resolves a reserved stem as a device
    # in any directory and with any extension, so a member called NUL.asi opens
    # for writing, succeeds, and sends every byte nowhere: the extract reports
    # success, the record lists a file, and nothing is on disk. CON and PRN do
    # the same, and COM1 with no such port aborts the extract mid-run instead.
    if cleaned.split(".")[0].lower() in _DEVICE_NAMES:
        cleaned = "_" + cleaned
    return cleaned


def safe_target(dest_dir: Path, member_name: str, flatten: bool) -> Path | None:
    """
    resolves an archive member to a path inside dest_dir, or None if it tries
    to escape (..\\..\\Windows\\System32\\x.dll). Archives are untrusted even
    from a mod you trust.
    """
    name = member_name.replace("\\", "/")
    if flatten:
        leaf = PurePosixPath(name).name
        return (dest_dir / windows_safe(leaf)) if leaf else None

    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        return None
    parts = [p for p in pure.parts if p not in ("", ".")]
    if not parts:
        # a member named "" or "." resolves to dest_dir itself, and the caller
        # then opens a directory for writing. ZipInfo.is_dir() does not catch
        # it either, because that only looks for a trailing slash.
        return None
    safe = "/".join(windows_safe(p) for p in parts)
    target = (dest_dir / safe).resolve()
    try:
        target.relative_to(dest_dir.resolve())
    except ValueError:
        return None
    return target


def extract_all(zf: zipfile.ZipFile, destination: Path) -> None:
    """
    extractall, but member by member so each name goes through repair() and
    safe_target(). Not a security fix (CPython already sanitises), a
    correctness one: extractall writes the mojibake name and lookups by the
    real name miss.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for info in zf.infolist():
        if info.is_dir():
            continue
        target = safe_target(destination, repair(info), flatten=False)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as source, target.open("wb") as sink:
            shutil.copyfileobj(source, sink, length=1024 * 1024)


def filename_from_url(url: str, fallback: str = "download") -> str:
    """
    the filename a URL is offering, percent-decoded and safe to write.

    The separator strip after decoding is the important part: %2F..%2Fevil.exe
    decodes to an absolute path, and `staging / filename` with an absolute
    right side throws staging away. The url is remote input.
    """
    from urllib.parse import unquote

    tail = url.split("?")[0].split("#")[0].rsplit("/", 1)[-1]
    try:
        tail = unquote(tail, errors="strict")
    except UnicodeDecodeError:
        # percent escapes that are not utf-8. keep the raw form rather than
        # inventing replacement characters in something we will write to disk.
        pass

    # one component, whatever the decode produced. PurePosixPath handles a
    # leading slash and a trailing one; the backslash swap catches the windows
    # spelling, which PurePosixPath would treat as an ordinary character.
    leaf = PurePosixPath(tail.replace("\\", "/")).name
    if leaf in ("", ".", ".."):
        return fallback

    safe = windows_safe(leaf)
    stem, dot, suffix = safe.partition(".")
    if stem.lower() in _DEVICE_NAMES:
        # into the stem, not onto the end. the suffix decides whether this gets
        # treated as an archive later, so "con.zip" must not become
        # "con.zip_file" and stop looking like a zip.
        safe = f"{stem}_file{dot}{suffix}"
    return safe or fallback
