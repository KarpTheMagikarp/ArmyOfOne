"""
WPD, the container LR keeps its little files in. db/resident/wdbpack.bin is
one, and every .wdb inside it is one too, its rows as records.

    0      "WPD" and a NUL
    4      u32 BE   how many records
    16     32 bytes a record: its name (16, NUL padded), u32 BE offset,
                    u32 BE size, its extension (8, NUL padded)
    then   each record's bytes, in table order, each padded to 4

That last line is WPDtool's repack and not something the game asks for: it
reads the offsets. The mods for it are made with WPDtool, though, and the
patchers that put them in repack the whole thing that way, so building it the
same way gives the game exactly what the mod's own patcher would have.

WPDtool unpacks a record to a file called `name.ext`, and a container's
records to a folder called `_` and its name, which is how a mod ships changes
to one: `db/resident/_wdbpack.bin/_r_reward.wdb/rw_102` is the record rw_102
of r_reward.wdb, inside the game's db/resident/wdbpack.bin.
"""

import struct
from dataclasses import dataclass

MAGIC = b"WPD\x00"
HEAD = 16
RECORD = 32
ALIGN = 4


class WpdError(ValueError):
    pass


@dataclass
class Record:
    name: bytes
    ext: bytes
    data: bytes

    @property
    def file(self) -> str:
        """the name WPDtool unpacks it to, which is what a mod calls it"""
        name = self.name.decode("utf-8", errors="replace")
        name = "".join(c for c in name if c not in '\\/:*?"<>|')
        ext = self.ext.decode("utf-8", errors="replace")
        return f"{name}.{ext}" if ext else name


def parse(raw: bytes) -> list:
    raw = bytes(raw)
    if raw[:4] != MAGIC:
        raise WpdError("not a WPD")
    count = struct.unpack_from(">I", raw, 4)[0]
    if HEAD + count * RECORD > len(raw):
        raise WpdError(f"says it has {count} records, more than fit in it")
    out = []
    for n in range(count):
        at = HEAD + n * RECORD
        name = raw[at:at + 16].split(b"\x00", 1)[0]
        offset, size = struct.unpack_from(">II", raw, at + 16)
        ext = raw[at + 24:at + 32].split(b"\x00", 1)[0]
        if offset + size > len(raw):
            raise WpdError(f"its record {name.decode('utf-8', 'replace')} runs past its end")
        out.append(Record(name, ext, raw[offset:offset + size]))
    return out


def build(records: list) -> bytes:
    table, body = bytearray(MAGIC + struct.pack(">I", len(records)) + bytes(8)), bytearray()
    start = HEAD + RECORD * len(records)
    for record in records:
        if len(record.name) > 16 or len(record.ext) > 8:
            raise WpdError(f"{record.file} has a name longer than a record holds")
        table += record.name.ljust(16, b"\x00")
        table += struct.pack(">II", start + len(body), len(record.data))
        table += record.ext.ljust(8, b"\x00")
        body += record.data
        body += bytes(-len(body) % ALIGN)
    return bytes(table + body)


def _find(records: list, name: str) -> Record:
    hits = [r for r in records if r.file.lower() == name.lower()]
    if not hits:
        raise WpdError(f"has no record called {name}")
    if len(hits) > 1:
        # WPDtool would unpack both to one file and pack that file back as
        # both, which no mod means to do
        raise WpdError(f"has {len(hits)} records called {name}")
    return hits[0]


def read(raw: bytes, inside: tuple) -> bytes:
    """the bytes of a record, a record of one of its records for a longer `inside`"""
    for name in inside:
        raw = _find(parse(raw), name).data
    return raw


def patch(raw: bytes, changes: dict) -> bytes:
    """
    the container with records swapped: `changes` is (record, record in that,
    ...) -> its new bytes. Every other record keeps its bytes and its place
    in the table
    """
    records = parse(raw)
    deeper = {}
    for inside, data in changes.items():
        if not inside:
            raise WpdError("a change names no record")
        target = _find(records, inside[0])
        if len(inside) == 1:
            target.data = bytes(data)
        else:
            deeper.setdefault(target.file.lower(), (target, {}))[1][tuple(inside[1:])] = data
    for target, nested in deeper.values():
        target.data = patch(target.data, nested)
    return build(records)


def split(path: str):
    """
    (the game's file, the records inside it) for a path through WPDtool's
    unpack folders, or None for a path that doesn't go through one
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    for n, part in enumerate(parts[:-1]):
        # a container holds records and nothing else, so everything between
        # its folder and the file is a container's folder too. ff13tool's
        # _white_img2a.win32.bin, with the game's folders in it, isn't one
        if len(part) > 1 and part.startswith("_") \
                and all(p.startswith("_") and len(p) > 1 for p in parts[n + 1:-1]):
            inside = tuple(p[1:] for p in parts[n + 1:-1]) + (parts[-1],)
            return "/".join(parts[:n] + [part[1:]]), inside
    return None
