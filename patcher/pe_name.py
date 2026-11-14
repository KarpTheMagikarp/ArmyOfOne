"""
The name src_38 gave mods' dlls for NovaFix's popup, and the way back off.

NovaFix's "loaded add-ons" popup lists every dll beside the game by its version
resource, and without one by the name it was loaded as, so HD Fonts and GUI's
proxy shows as VERSION. src_38's named() gave a dll with no name a version
resource: a section on the end holding the resource table copied over with a
VS_VERSIONINFO added, and the table's directory entry pointed at it.

XIII-2 and LR both stopped starting the round that shipped, and HD Fonts and
GUI's renamed version.dll was the one change it made to XIII-2's folder. A dll
that isn't its author's bytes any more is one Windows' reputation checks have
never seen, whatever the loader makes of it, so nothing renames a mod's dll
now, and unnamed() gives back the exact one it replaced. named() stays as the
definition of what's being undone.
"""

import hashlib
import struct
import sys
from array import array

RT_VERSION = 16
SIGNATURE = 0xFEEF04BD
# what NovaFix asks for first when the file says nothing, en-US in UTF-16
LANGUAGE, CODEPAGE = 0x0409, 0x04B0
SECTION = b".rsrcn\x00\x00"
READABLE = 0x40000040     # initialized data, read


class PeError(ValueError):
    pass


def _align(value: int, to: int) -> int:
    return (value + to - 1) // to * to


def _layout(raw: bytes) -> dict:
    if raw[:2] != b"MZ" or len(raw) < 0x40:
        raise PeError("not a PE")
    pe = struct.unpack_from("<I", raw, 0x3C)[0]
    if raw[pe:pe + 4] != b"PE\x00\x00":
        raise PeError("not a PE")
    count = struct.unpack_from("<H", raw, pe + 6)[0]
    optional = struct.unpack_from("<H", raw, pe + 20)[0]
    head = pe + 24
    magic = struct.unpack_from("<H", raw, head)[0]
    if magic not in (0x10B, 0x20B):
        raise PeError("an optional header this doesn't know")
    wide = magic == 0x20B
    dirs_at = head + (112 if wide else 96)
    rvas = struct.unpack_from("<I", raw, dirs_at - 4)[0]
    sections = []
    table = head + optional
    for n in range(count):
        at = table + 40 * n
        name = raw[at:at + 8]
        vsize, va, rsize, roff = struct.unpack_from("<IIII", raw, at + 8)
        sections.append({"at": at, "name": name, "vsize": vsize, "va": va, "rsize": rsize,
                         "roff": roff})
    section_align, file_align = struct.unpack_from("<II", raw, head + 32)
    # a linker writes powers of two, and anything else is a file that isn't
    # what its header says
    for align in (section_align, file_align):
        if align <= 0 or align & (align - 1):
            raise PeError("an alignment no linker writes")
    return {"pe": pe, "head": head, "count": count, "table": table, "wide": wide,
            "section_align": section_align, "file_align": file_align,
            "image_size_at": head + 56, "headers": struct.unpack_from("<I", raw, head + 60)[0],
            "checksum_at": head + 64, "dirs_at": dirs_at, "rvas": rvas, "sections": sections}


def _directory(raw: bytes, layout: dict, n: int) -> tuple:
    if n >= layout["rvas"]:
        return 0, 0
    return struct.unpack_from("<II", raw, layout["dirs_at"] + 8 * n)


def _offset(layout: dict, rva: int) -> int:
    for s in layout["sections"]:
        if s["va"] <= rva < s["va"] + max(s["vsize"], s["rsize"]):
            if rva - s["va"] >= s["rsize"]:
                raise PeError("a resource sits in a part of its section the file doesn't hold")
            return s["roff"] + rva - s["va"]
    if rva < layout["headers"]:
        return rva
    raise PeError("a resource points outside every section")


def _tree(raw: bytes, layout: dict) -> dict:
    """type -> name -> language -> (data rva, size, codepage), names as ints or strings"""
    rva, size = _directory(raw, layout, 2)
    if not rva or not size:
        return {}
    base = _offset(layout, rva)

    def name_of(entry: int):
        if entry & 0x80000000:
            at = base + (entry & 0x7FFFFFFF)
            length = struct.unpack_from("<H", raw, at)[0]
            return raw[at + 2:at + 2 + 2 * length].decode("utf-16-le")
        return entry & 0xFFFF

    seen = set()

    def entries(at: int) -> list:
        # a real tree never lists one directory twice, and one that does can
        # fan a few kilobytes out to billions of visits
        if at in seen:
            raise PeError("a resource directory that's listed twice")
        seen.add(at)
        named, ids = struct.unpack_from("<HH", raw, base + at + 12)
        if named + ids > 4096:
            raise PeError("a resource directory says it has more entries than any does")
        return [struct.unpack_from("<II", raw, base + at + 16 + 8 * n) for n in range(named + ids)]

    out = {}
    for kind, below in entries(0):
        if not below & 0x80000000:
            raise PeError("a resource type that isn't a directory")
        for name, deeper in entries(below & 0x7FFFFFFF):
            if not deeper & 0x80000000:
                raise PeError("a resource name that isn't a directory")
            for language, leaf in entries(deeper & 0x7FFFFFFF):
                if leaf & 0x80000000:
                    raise PeError("a resource language that's a directory")
                data, length, codepage, _ = struct.unpack_from("<IIII", raw, base + leaf)
                out.setdefault(name_of(kind), {}).setdefault(name_of(name), {})[language] = (
                    data, length, codepage)
    return out


def _blocks(data: bytes, at: int = 0, end: int | None = None, depth: int = 0) -> list:
    """the children of a VS_VERSIONINFO block as (key, value bytes, is text, children)"""
    end = len(data) if end is None else end
    out = []
    # a string is four blocks down, and nothing NovaFix reads is deeper
    if depth > 6:
        return out
    while at + 6 <= end:
        length, value_length, text = struct.unpack_from("<HHH", data, at)
        if length < 6 or at + length > end:
            break
        key_end = at + 6
        while key_end + 1 < at + length and data[key_end:key_end + 2] != b"\x00\x00":
            key_end += 2
        key = data[at + 6:key_end].decode("utf-16-le", errors="replace")
        value_at = _align(key_end + 2, 4)
        value_size = value_length * 2 if text else value_length
        value = data[value_at:value_at + value_size]
        children = _blocks(data, _align(value_at + value_size, 4), at + length, depth + 1)
        out.append((key, value, bool(text), children))
        at = _align(at + length, 4)
    return out


def _version(raw: bytes, layout: dict, versions: dict) -> tuple:
    """the four numbers of a version resource's file version, 1.0.0.0 without one"""
    for languages in versions.values():
        for data, size, _ in languages.values():
            at = _offset(layout, data)
            top = _blocks(raw[at:at + size])
            fixed = top[0][1] if top else b""
            if len(fixed) >= 16 and struct.unpack_from("<I", fixed)[0] == SIGNATURE:
                ms, ls = struct.unpack_from("<II", fixed, 8)
                return ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF
    return 1, 0, 0, 0


def display_name(raw: bytes) -> str:
    """what NovaFix's popup calls this dll, "" when it'd go by the file's name"""
    try:
        layout = _layout(raw)
        # VS_VERSION_INFO is id 1, the one GetFileVersionInfo looks up, and one
        # under a name or another id is a resource nothing reads
        chosen = _tree(raw, layout).get(RT_VERSION, {}).get(1)
        if not chosen:
            return ""
        data, size, _ = next(iter(chosen.values()))
        at = _offset(layout, data)
        top = _blocks(raw[at:at + size])
    except (PeError, struct.error, IndexError, MemoryError):
        return ""
    found = {}
    for _, _, _, children in top[:1]:
        for key, _, _, tables in children:
            if key != "StringFileInfo":
                continue
            for _, _, _, strings in tables:
                for name, value, _, _ in strings:
                    text = value.decode("utf-16-le", errors="replace").rstrip("\x00").strip()
                    if text:
                        found.setdefault(name, text)
    return found.get("ProductName") or found.get("FileDescription") or ""


def _block(key: str, value: bytes = b"", children=(), text: bool = False,
           value_length: int | None = None) -> bytes:
    body = bytearray(bytes(6) + (key + "\x00").encode("utf-16-le"))
    body += bytes(-len(body) % 4)
    body += value
    for child in children:
        body += bytes(-len(body) % 4)
        body += child
    if value_length is None:
        value_length = len(value) // 2 if text else len(value)
    struct.pack_into("<HHH", body, 0, len(body), value_length, 1 if text else 0)
    return bytes(body)


def version_info(name: str, version=(1, 0, 0, 0)) -> bytes:
    """a VS_VERSIONINFO naming a dll, laid out the way rc.exe writes one"""
    ms, ls = (version[0] << 16) | version[1], (version[2] << 16) | version[3]
    fixed = struct.pack("<13I", SIGNATURE, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x40004, 2, 0, 0, 0)
    shown = ".".join(str(v) for v in version)
    strings = [_block(key, (text + "\x00").encode("utf-16-le"), text=True)
               for key, text in (("FileDescription", name), ("FileVersion", shown),
                                 ("ProductName", name), ("ProductVersion", shown))]
    table = _block(f"{LANGUAGE:04x}{CODEPAGE:04x}", children=strings, text=True)
    translation = _block("Translation", struct.pack("<HH", LANGUAGE, CODEPAGE))
    return _block("VS_VERSION_INFO", fixed, children=[
        _block("StringFileInfo", children=[table], text=True),
        _block("VarFileInfo", children=[translation], text=True)])


def _build_tree(tree: dict, base_rva: int) -> bytes:
    """
    the resource section's bytes, laid out from `base_rva`, for a tree whose
    leaves are (rva, size, codepage) or bytes still to place
    """
    def ordered(level: dict) -> list:
        # named entries first, in the order the file had them, which is the
        # order its own compiler sorted them in for the loader's binary
        # search, then ids ascending
        items = list(level.items())
        return [kv for kv in items if not isinstance(kv[0], int)] + sorted(
            (kv for kv in items if isinstance(kv[0], int)), key=lambda kv: kv[0])

    dirs, strings, leaves = bytearray(), [], []
    # ("name", where an entry's name goes, which string) or ("leaf", where its
    # data entry's offset goes, which leaf), filled in once those are placed
    patches = []

    def directory(level: dict, depth: int) -> int:
        at = len(dirs)
        items = ordered(level)
        named = sum(not isinstance(k, int) for k, _ in items)
        dirs.extend(struct.pack("<IIHHHH", 0, 0, 0, 0, named, len(items) - named))
        slots = len(dirs)
        dirs.extend(bytes(8 * len(items)))
        for n, (key, below) in enumerate(items):
            slot = slots + 8 * n
            if isinstance(key, int):
                struct.pack_into("<I", dirs, slot, key)
            else:
                patches.append(("name", slot, len(strings)))
                strings.append(key)
            if depth < 2:
                child = directory(below, depth + 1)
                struct.pack_into("<I", dirs, slot + 4, 0x80000000 | child)
            else:
                patches.append(("leaf", slot + 4, len(leaves)))
                leaves.append(below)
        return at

    directory(tree, 0)
    out = bytearray(dirs)
    string_at = []
    for text in strings:
        string_at.append(len(out))
        encoded = text.encode("utf-16-le")
        out += struct.pack("<H", len(encoded) // 2) + encoded
    out += bytes(-len(out) % 4)
    leaf_at = []
    for _ in leaves:
        leaf_at.append(len(out))
        out += bytes(16)
    for n, leaf in enumerate(leaves):
        if isinstance(leaf, (bytes, bytearray)):
            out += bytes(-len(out) % 8)
            struct.pack_into("<IIII", out, leaf_at[n], base_rva + len(out), len(leaf), 0, 0)
            out += leaf
        else:
            rva, size, codepage = leaf
            struct.pack_into("<IIII", out, leaf_at[n], rva, size, codepage, 0)
    for kind, slot, index in patches:
        if kind == "name":
            struct.pack_into("<I", out, slot, 0x80000000 | string_at[index])
        else:
            struct.pack_into("<I", out, slot, leaf_at[index])
    return bytes(out)


def _checksum(raw: bytes, at: int) -> int:
    """
    the PE checksum, the way imagehlp's CheckSumMappedFile adds it up: the
    file's 16-bit words with their carries folded back in, less the checksum's
    own, plus its length. Folding once at the end comes to the same
    """
    data = raw + bytes(len(raw) % 2)
    words = array("H")
    words.frombytes(data)
    if sys.byteorder != "little":
        words.byteswap()
    # summed where they are: a tuple of every word as an int is 24 times the file
    total = sum(words) - sum(struct.unpack_from("<HH", data, at))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total + len(raw)


def named(raw: bytes, name: str):
    """
    a copy of a dll with a version resource calling it `name`, or None when it
    has a name of its own already or this can't add one without moving
    anything that's there
    """
    name = " ".join(str(name or "").split())
    if not name:
        return None
    try:
        return _named(bytes(raw), name)
    except Exception:
        # a file this can't make sense of goes in as it came
        return None


def _named(raw: bytes, name: str):
    if display_name(raw):
        return None
    layout = _layout(raw)
    # signed, or no slot for the resource table in its directory
    if _directory(raw, layout, 4)[1] or layout["rvas"] < 3:
        return None
    # under a page, the loader wants every section's address to be its place in
    # the file, which nothing after the last one would be. Past these, it's no
    # file a linker wrote
    if not 0x1000 <= layout["section_align"] <= 1 << 28 or layout["file_align"] > 0x10000:
        return None
    sections = layout["sections"]
    if not sections:
        return None
    table_end = layout["table"] + 40 * (layout["count"] + 1)
    first = min([s["roff"] for s in sections if s["rsize"]] or [len(raw)])
    # the slot for one more header has to be inside the headers and empty:
    # a linker's bound imports are what most often sits right after them
    if table_end > min(layout["headers"], first) or any(raw[table_end - 40:table_end]):
        return None
    tree = {kind: {k: dict(v) for k, v in names.items()}
            for kind, names in _tree(raw, layout).items()}
    version = _version(raw, layout, tree.get(RT_VERSION, {}))
    # whatever version resource it had says no name, so ours takes its place,
    # with its version numbers when it had some
    tree.setdefault(RT_VERSION, {})[1] = {LANGUAGE: version_info(name, version)}
    # the loader steps each section on by its size in memory, its size in the
    # file only standing in for one that doesn't say
    last = max(sections, key=lambda s: s["va"])
    va = _align(last["va"] + (last["vsize"] or last["rsize"]), layout["section_align"])
    body = _build_tree(tree, va)
    if va + len(body) >= 1 << 32:
        return None
    start = _align(len(raw), layout["file_align"])
    out = bytearray(raw + bytes(start - len(raw)))
    out += body + bytes(-len(body) % layout["file_align"])
    header = bytearray(SECTION + struct.pack("<IIII", len(body), va,
                                             _align(len(body), layout["file_align"]), start)
                       + bytes(12) + struct.pack("<I", READABLE))
    out[table_end - 40:table_end] = header
    struct.pack_into("<H", out, layout["pe"] + 6, layout["count"] + 1)
    struct.pack_into("<I", out, layout["image_size_at"],
                     _align(va + len(body), layout["section_align"]))
    struct.pack_into("<II", out, layout["dirs_at"] + 16, va, len(body))
    struct.pack_into("<I", out, layout["checksum_at"], 0)
    struct.pack_into("<I", out, layout["checksum_at"], _checksum(bytes(out), layout["checksum_at"]))
    return bytes(out)


def unnamed(raw: bytes, originals=()):
    """
    the dll named() was handed, from what it made of it, or None when this
    isn't one of its copies or which bytes it came from can't be told.
    `originals` are digests the file is on record as having had, and the copy
    that hashes to one is the answer. With none on record, the copy that names
    back to exactly `raw` is, as a linker would have left it
    """
    try:
        return _unnamed(bytes(raw), {str(d) for d in originals or ()})
    except Exception:
        return None


def _unnamed(raw: bytes, originals: set):
    layout = _layout(raw)
    *before, ours = layout["sections"]
    # named() adds one section, last in the table, the file and memory, and
    # points the resource table at it, so anything else isn't its copy
    if not before or ours["name"] != SECTION or ours["roff"] + ours["rsize"] != len(raw) \
            or _directory(raw, layout, 2) != (ours["va"], ours["vsize"]):
        return None
    last = max(before, key=lambda s: s["va"])
    reach = last["va"] + (last["vsize"] or last["rsize"])
    if ours["va"] != _align(reach, layout["section_align"]):
        return None
    name = display_name(raw)
    if not name:
        return None
    # the copy it is now is on the record too, and it can't be its own original
    originals.discard(hashlib.sha256(raw).hexdigest())
    start = ours["roff"]
    base = bytearray(raw[:start])
    at = layout["table"] + 40 * (layout["count"] - 1)
    base[at:at + 40] = bytes(40)
    struct.pack_into("<H", base, layout["pe"] + 6, layout["count"] - 1)
    # what it wrote over rather than added: the image size, set from the last
    # section on, the resource table's entry and the checksum
    images = list(dict.fromkeys((ours["va"], reach)))
    tables = [(s["va"], s["vsize"]) for s in before
              if s["name"].rstrip(b"\x00") == b".rsrc"] + [(0, 0)]
    # the file was padded out to the alignment with zeros, never into the
    # file's own sections
    end = max([s["roff"] + s["rsize"] for s in before if s["rsize"]] + [layout["headers"]])
    lengths = [start]
    while lengths[-1] > max(end, start - layout["file_align"] + 1) and not raw[lengths[-1] - 1]:
        lengths.append(lengths[-1] - 1)

    def made(image: int, table: tuple, length: int, checksum: bool) -> bytes:
        out = bytearray(base[:length])
        struct.pack_into("<I", out, layout["image_size_at"], image)
        struct.pack_into("<II", out, layout["dirs_at"] + 16, *table)
        struct.pack_into("<I", out, layout["checksum_at"], 0)
        if checksum:
            struct.pack_into("<I", out, layout["checksum_at"],
                             _checksum(bytes(out), layout["checksum_at"]))
        return bytes(out)

    if originals:
        shortest, tail = lengths[-1], memoryview(base)
        # a checksum is in the headers and moves with the length, so it's only
        # worth a whole pass for the lengths a file could have ended at: on
        # the alignment, or after the symbols GNU ld leaves past the sections
        likely = [start, shortest]
        symbols, count = struct.unpack_from("<II", raw, layout["pe"] + 12)
        if symbols and symbols + 18 * count + 4 <= start:
            strings = symbols + 18 * count
            likely.append(strings + struct.unpack_from("<I", raw, strings)[0])
        likely = [n for n in dict.fromkeys(likely) if shortest <= n <= start]
        for image in images:
            for table in tables:
                # every length shares the headers and all but the last few
                # zeros, so those are hashed once
                shared = hashlib.sha256(made(image, table, layout["headers"], False))
                shared.update(tail[layout["headers"]:shortest])
                for length in reversed(lengths):
                    digest = shared.copy()
                    digest.update(tail[shortest:length])
                    if digest.hexdigest() in originals:
                        return made(image, table, length, False)
                for length in likely:
                    summed = made(image, table, length, True)
                    if hashlib.sha256(summed).hexdigest() in originals:
                        return summed
    # where the file ended is a guess past the first length, and a guess
    # isn't the author's dll
    if len(lengths) > 1:
        return None
    for table in tables:
        candidate = made(images[0], table, start, False)
        if named(candidate, name) == raw:
            return candidate
    return None
