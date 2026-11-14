"""
Nova Chrysalia's code patches (.nccp), put in the way Nova put them in.

A .nccp is Valve KeyValues text: one root object, each child with an all-digit
name an "RVA|bytes" pair in hex. Nova's launcher starts the exe itself,
suspends it, and writes each one at the image base plus the RVA (MemPatch.cs).
We can't start the exe ourselves, Steam refuses that (launch.py), so the
writing is done from inside by our NovaFix add-on, addon-src/ff13-code-patches.
NovaFix loads add-ons at the game's first Direct3DCreate9 (render init), later
than Nova writes, so a patch to startup code that already ran won't take. The
exe on disk is never touched, so XIII's CEG self-check never sees it.

This side reads them the way that add-on will (the parse is the same rules as
its nccp.h), checks each lands inside this game's exe, and turns away two mods
whose patches overlap, so the order the add-on reads them in never decides.
"""

import re
import struct
from dataclasses import dataclass
from pathlib import Path

FOLDER = "ff13-code-patches"
ADDON = "01_ff13_code_patches.asi"


# isspace() in the add-on's C locale. Python's own also takes a no-break
# space and \x1c-\x1f, and a file that reads differently here than there gets
# checked as one thing and written as another
SPACE = " \t\n\v\f\r"
BARE = re.compile(r'[^ \t\n\v\f\r{}"]+')


class NccpError(ValueError):
    pass


@dataclass(frozen=True)
class Patch:
    rva: int
    data: bytes

    @property
    def end(self) -> int:
        return self.rva + len(self.data)


def _tokens(text: str):
    """KeyValues tokens: quoted or bare text, braces. // comments and [$WIN32] conditions go"""
    at, n = 0, len(text)
    while at < n:
        c = text[at]
        if c in SPACE:
            at += 1
        elif text.startswith("//", at):
            at = text.find("\n", at)
            at = n if at < 0 else at
        elif c == "[":
            close = text.find("]", at)
            at = n if close < 0 else close + 1
        elif c in "{}":
            yield c, None
            at += 1
        elif c == '"':
            out, at = [], at + 1
            while at < n and text[at] != '"':
                if text[at] == "\\" and at + 1 < n:
                    at += 1
                    out.append({"n": "\n", "t": "\t"}.get(text[at], text[at]))
                else:
                    out.append(text[at])
                at += 1
            yield "text", "".join(out)
            at += 1
        else:
            hit = BARE.match(text, at)
            yield "text", hit.group()
            at = hit.end()


def _entry(value: str):
    """RVA|bytes the way Nova splits it, spaces forgiven where Nova threw mid-launch"""
    if "|" not in value:
        return None
    rva, rest = value.split("|", 1)
    rva = re.sub(f"[{SPACE}]", "", rva)
    data = re.sub(f"[{SPACE}]", "", rest.split("|", 1)[0])
    if rva[:2].lower() == "0x":
        rva = rva[2:]
    if not rva or len(rva) > 16 or not data or len(data) % 2 or \
            not re.fullmatch(r"[0-9A-Fa-f]+", rva + data):
        return None
    return Patch(int(rva, 16), bytes.fromhex(data))


def parse(raw: bytes) -> tuple:
    """([Patch] in file order, [keys that didn't read as RVA|bytes])"""
    # byte for byte, the way the add-on reads it
    text = (raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw).decode("latin-1")
    tokens = list(_tokens(text))
    patches, skipped = [], []
    if len(tokens) < 2 or tokens[0][0] != "text" or tokens[1][0] != "{":
        return patches, skipped
    n = 2
    while n + 1 < len(tokens) and tokens[n][0] == "text":
        key, (kind, value) = tokens[n][1], tokens[n + 1]
        n += 2
        if kind == "{":
            # an object as a value is no patch at any depth
            depth = 1
            while n < len(tokens) and depth:
                depth += {"{": 1, "}": -1}.get(tokens[n][0], 0)
                n += 1
            continue
        if kind != "text":
            break
        if not re.fullmatch(r"[0-9]*", key):
            continue
        patch = _entry(value)
        if patch is None:
            skipped.append(key)
        else:
            patches.append(patch)
    return patches, skipped


def image_size(exe: bytes) -> int:
    """the exe's SizeOfImage, what the add-on bounds every write by, or 0"""
    try:
        at = struct.unpack_from("<I", exe, 0x3C)[0]
        if exe[:2] != b"MZ" or exe[at:at + 4] != b"PE\x00\x00":
            return 0
        return struct.unpack_from("<I", exe, at + 24 + 56)[0]
    except struct.error:
        return 0


def read_for(raw: bytes, exe: bytes, name: str) -> list:
    """
    the patches a .nccp holds, every one checked to land inside `exe`'s
    image. None at all, or one outside it, and the whole file's turned away:
    it was made for another game or another version of this one's exe
    """
    patches, skipped = parse(raw)
    if not patches:
        raise NccpError(f"{name} has no code patch in it that reads as Nova Chrysalia writes them")
    if skipped:
        raise NccpError(f"{name}'s patch {skipped[0]} doesn't read as an address and its bytes")
    size = image_size(exe)
    outside = next((p for p in patches if not size or p.end > size), None)
    if outside is not None:
        raise NccpError(f"{name} patches the game's code at {outside.rva:X}, past the end of this "
                        "game's exe, so it's for another game or another version of it")
    return patches


def overlap(mine: list, theirs: list):
    """the first of `mine` that writes where one of `theirs` does, differently, or None"""
    for a in mine:
        for b in theirs:
            lo, hi = max(a.rva, b.rva), min(a.end, b.end)
            if lo < hi and a.data[lo - a.rva:hi - a.rva] != b.data[lo - b.rva:hi - b.rva]:
                return a
    return None


def bundled() -> Path:
    from patcher.paths import bundled_data_root

    return bundled_data_root() / "assets" / "mods" / FOLDER / ADDON


def ensure_addon(folder: Path) -> bool:
    """
    our add-on in `folder`, when the build in the bundle isn't there already.
    It belongs to no mod: it does nothing with no .nccp beside it, and two mods
    can't both own one file. False when the bundle hasn't got it
    """
    from patcher.fileops import write_atomic

    source = bundled()
    if not source.is_file():
        return False
    data = source.read_bytes()
    target = Path(folder) / ADDON
    try:
        if target.is_file() and target.read_bytes() == data:
            return True
    except OSError:
        pass
    folder.mkdir(parents=True, exist_ok=True)
    write_atomic(target, data)
    return True
