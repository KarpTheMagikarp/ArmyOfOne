import io
import struct
import tarfile
import time
import zlib
import zipfile
from pathlib import Path


def make_fake_pe(characteristics: int = 0x0000, machine: int = 0x8664) -> bytes:
    """
    builds a minimal fake PE-shaped byte string, just enough structure for
    our functions to find the Characteristics field. not a real runnable
    exe, just a fixture.
    """
    pe_offset = 0x80

    dos_header = bytearray(pe_offset)
    # the DOS magic was missing, which nothing noticed while the only reader
    # was patch_ops following e_lfanew. unusable_binary() checks it, so a
    # fixture without it is not a Windows binary as far as the app is concerned.
    dos_header[0:2] = b"MZ"
    struct.pack_into("<I", dos_header, 0x3C, pe_offset)  # e_lfanew

    coff_header = bytearray(20)
    # machine defaults to x64 so unusable_binary() has something to reject.
    # pass 0x014C when the test needs a file that passes shape validation.
    struct.pack_into("<H", coff_header, 0, machine)
    struct.pack_into("<H", coff_header, 18, characteristics)  # Characteristics

    return bytes(dos_header) + b"PE\x00\x00" + bytes(coff_header) + b"trailing junk goes here"


def make_export_pe(exports=(), machine: int = 0x014C, version: tuple | None = None,
                   managed: bool = False, filler: bytes = b"") -> bytes:
    """
    a PE32 dll real enough to read exports out of: one section holding the
    export directory and its names, and a VS_FIXEDFILEINFO when `version` is
    (major, minor) the way a version resource carries it. `filler` makes two
    builds of the same exports different files
    """
    names = sorted(exports)
    section_rva, raw_at = 0x1000, 0x200
    directory = 40
    functions = directory
    name_table = functions + 4 * len(names)
    ordinals = name_table + 4 * len(names)
    strings = ordinals + 2 * len(names)
    blob = bytearray(strings)
    text = b""
    for n, name in enumerate(names):
        struct.pack_into("<I", blob, functions + 4 * n, section_rva)
        struct.pack_into("<I", blob, name_table + 4 * n, section_rva + strings + len(text))
        struct.pack_into("<H", blob, ordinals + 2 * n, n)
        text += name.encode("ascii") + b"\0"
    struct.pack_into("<IIHHIIIIIII", blob, 0, 0, 0, 0, 0, 0, 1, len(names), len(names),
                     section_rva + functions, section_rva + name_table, section_rva + ordinals)
    blob += text
    if version is not None:
        blob += b"\xbd\x04\xef\xfe" + struct.pack("<III", 0x10000, version[0] << 16 | version[1], 0)
    blob += filler

    head = bytearray(raw_at)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, 0x84, machine, 1, 0, 0, 0, 0xE0, 0x2102)
    optional = 0x98
    struct.pack_into("<H", head, optional, 0x10B)
    struct.pack_into("<I", head, optional + 92, 16)
    if names:
        struct.pack_into("<II", head, optional + 96, section_rva, len(blob))
    if managed:
        struct.pack_into("<II", head, optional + 96 + 14 * 8, section_rva, 72)
    section = optional + 0xE0
    head[section:section + 8] = b".edata\0\0"
    struct.pack_into("<IIII", head, section + 8, len(blob), section_rva, len(blob), raw_at)
    return bytes(head) + bytes(blob)


def make_import_pe(dlls=(), machine: int = 0x014C) -> bytes:
    """a PE32 exe importing from `dlls`, real enough to read its import directory off"""
    section_rva, raw_at = 0x1000, 0x200
    table = 20 * (len(dlls) + 1)
    blob = bytearray(table)
    text = b""
    for n, dll in enumerate(dlls):
        struct.pack_into("<IIIII", blob, 20 * n, 0, 0, 0, section_rva + table + len(text), 0)
        text += dll.encode("ascii") + b"\0"
    blob += text
    head = bytearray(raw_at)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, 0x84, machine, 1, 0, 0, 0, 0xE0, 0x0102)
    optional = 0x98
    struct.pack_into("<H", head, optional, 0x10B)
    struct.pack_into("<I", head, optional + 92, 16)
    struct.pack_into("<II", head, optional + 104, section_rva, table)
    section = optional + 0xE0
    head[section:section + 8] = b".idata\0\0"
    struct.pack_into("<IIII", head, section + 8, len(blob), section_rva, len(blob), raw_at)
    return bytes(head) + bytes(blob)


def make_zip_bytes(files: dict) -> bytes:
    """builds an in-memory zip from a {filename: content_bytes} dict, for fake release downloads"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def make_locked_zip_bytes(files: dict, password: bytes) -> bytes:
    """
    a zip stored under PKWARE's own password scheme, the one 7-Zip calls
    ZipCrypto: zipfile reads it with the password but has no way to write one
    """
    table = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    out, central = io.BytesIO(), []
    for name, data in files.items():
        keys = [0x12345678, 0x23456789, 0x34567890]

        def update(c, keys=keys):
            keys[0] = (keys[0] >> 8) ^ table[(keys[0] ^ c) & 0xFF]
            keys[1] = ((keys[1] + (keys[0] & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
            keys[2] = (keys[2] >> 8) ^ table[(keys[2] ^ (keys[1] >> 24)) & 0xFF]

        for c in password:
            update(c)
        crc = zlib.crc32(data) & 0xFFFFFFFF
        locked = bytearray()
        # twelve bytes ahead of the data, the last the CRC's top byte, which
        # is how a reader tells a wrong password
        for c in bytes(11) + bytes([crc >> 24]) + data:
            t = (keys[2] | 2) & 0xFFFF
            locked.append(c ^ (((t * (t ^ 1)) >> 8) & 0xFF))
            update(c)
        at, raw = out.tell(), name.encode()
        out.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 1, 0, 0, 0x21, crc, len(locked),
                              len(data), len(raw), 0) + raw + locked)
        central.append(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 1, 0, 0, 0x21, crc,
                                   len(locked), len(data), len(raw), 0, 0, 0, 0, 0, at) + raw)
    start = out.tell()
    out.write(b"".join(central))
    out.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central),
                          out.tell() - start, start, 0))
    return out.getvalue()


def seal_member(data: bytes, name: str, method=None, locked=False) -> bytes:
    """
    a zip with one member said to be locked with a password, or packed with a
    method only 7-Zip reads (9 is Deflate64), in its local header and its
    central record both. zipfile lists it fine and only fails reading it
    """
    data = bytearray(data)
    at = data.index(b"PK\x01\x02")
    while at >= 0:
        size = struct.unpack_from("<H", data, at + 28)[0]
        if bytes(data[at + 46:at + 46 + size]) == name.encode():
            local = struct.unpack_from("<I", data, at + 42)[0]
            for flags in (at + 8, local + 6):
                if locked:
                    struct.pack_into("<H", data, flags, struct.unpack_from("<H", data, flags)[0] | 1)
                if method is not None:
                    struct.pack_into("<H", data, flags + 2, method)
            return bytes(data)
        at = data.find(b"PK\x01\x02", at + 46)
    raise KeyError(name)


def make_split_zip(files: dict, stem: str = "pack") -> dict:
    """
    a zip split in two the way Info-ZIP's -s does it: the members in .z01
    behind the spanning mark, the central record in .zip, which says it's
    the second disk. zipfile lists the .zip and can't read a member of it
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    data = buf.getvalue()
    end = data.rindex(b"PK\x05\x06")
    count, size, start = struct.unpack_from("<HII", data, end + 10)
    central, at = bytearray(data[start:start + size]), 0
    while at < len(central):
        # every member starts on the first disk, four bytes in past the mark
        struct.pack_into("<H", central, at + 34, 0)
        struct.pack_into("<I", central, at + 42, struct.unpack_from("<I", central, at + 42)[0] + 4)
        name, extra, comment = struct.unpack_from("<HHH", central, at + 28)
        at += 46 + name + extra + comment
    record = struct.pack("<IHHHHIIH", 0x06054B50, 1, 1, count, count, size, 0, 0)
    return {f"{stem}.z01": b"PK\x07\x08" + data[:start], f"{stem}.zip": bytes(central) + record}


def make_tar_bytes(files: dict, compression: str = "gz") -> bytes:
    """
    builds an in-memory tarball, for release archives shaped like DXVK's.
    compression is the tarfile mode suffix: "gz", "xz", "bz2" or "" for plain.
    """
    buf = io.BytesIO()
    mode = f"w:{compression}" if compression else "w"
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = int(time.time())
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def make_tar_with_symlink(real_files: dict, link_name: str, link_target: str) -> bytes:
    """a tarball carrying a symlink, to prove only regular files get installed"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in real_files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        link = tarfile.TarInfo(link_name)
        link.type = tarfile.SYMTYPE
        link.linkname = link_target
        tf.addfile(link)
    return buf.getvalue()


def ff13_records(records) -> bytes:
    """
    XIII's lookup records for an index of one chunk: a file code, then that
    chunk and the offset each record's text starts at, as uint16s. The real
    filelist_scru reads that way, and an index with zeroes there is one the
    copy into the other voice set refuses to edit.
    """
    from patcher.archive.entry import format_entry

    out, at = bytearray(), 0
    for n, record in enumerate(records):
        out += struct.pack("<IHH", 0x11000000 + n * 0x100, 0, at)
        at += len(format_entry(record).encode("utf-8")) + 1
    return bytes(out)


def write_index(target, entries):
    """
    a real filelist_scr in the shape XIII actually ships: no header, no length
    field, body from byte zero.

    entries is (path, uncompressed) pairs. The fixtures used to write
    b"VANILLA:..." here, which is fine for a test that only copies the file
    around and useless the moment anything reads it, which is now most of the
    interesting code.
    """
    import struct
    import zlib

    from patcher.archive.entry import ChunkEntry, format_chunk

    records = [ChunkEntry(path=path, sector_offset=i, uncompressed=size, stored=size)
               for i, (path, size) in enumerate(entries)]
    plain = format_chunk(records)
    blob = zlib.compress(plain, 9)

    table_at = 12 + 8 * len(records)
    chunk_at = table_at + 12
    body = bytearray(struct.pack("<III", table_at, chunk_at, len(records)))
    body += ff13_records(records)
    body += struct.pack("<III", len(plain), len(blob), 0)
    body += blob

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(body))
    return target


def index_entries(data_dir, grow=(), extra=()):
    """
    what the game's index holds for the ids the mod's own lists name, with the
    given paths grown as an install would grow them.

    Two sources, because the lists are not the only place an id appears. A
    recipe file names the archive paths it works on outright, seven header
    lines down, and that is the authoritative spelling: it is what the tool
    reads and it covers a zone block, whose path has no group in it at all.
    `extra` takes directories to sweep for recipes beyond data_dir.
    """
    from patcher.mods.hd_recipe import read_category
    from patcher.mods.hd_runner import GROUPS

    bigger = {p.lower() for p in grow}
    out = []

    # the six main character models at the byte sizes Setup.bat checks for.
    # Anything else here would have already_modded declare every fixture game
    # modded, which is correct of it and useless as a starting point.
    from patcher.mods.hd_preflight import VANILLA_PCS

    def add(path):
        if any(path == p for p, _ in out):
            return
        size = VANILLA_PCS.get(path.lower(), 200_000)
        out.append((path, size + (4096 if path.lower() in bigger else 0)))

    for folder in (Path(data_dir), *(Path(p) for p in extra)):
        if not folder.is_dir():
            continue
        for listing in sorted(folder.glob("LoadHD*.tx*")):
            for kind, member in read_category(listing).members:
                group = GROUPS.get((kind or "").upper())
                if group is None:
                    continue
                for suffix in (".win32.trb", ".win32.imgb"):
                    add(f"chr/{group}/{member}/bin/{member}{suffix}")
        for recipe in sorted(folder.glob("*.txt")):
            if recipe.name.startswith("LoadHD"):
                continue
            lines = recipe.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[8:10]:
                text = line.strip().replace("\\", "/")
                # chr only: a zone block's path is in its own zone archive, not
                # in white_scr, and putting it here would have the index claim
                # something the game does not keep there
                if (text.startswith("chr/") and ".." not in text
                        and text.endswith((".win32.trb", ".win32.imgb"))):
                    add(text)
    return out


def write_scr_pair(index, archive, entries, size=0):
    """
    a filelist_scr and the archive it describes, each entry at its own sectors
    holding what the stub ff13tool extracts for it. The backup only takes and
    restores a pair whose index fits its archive, which every real game's does
    and the old placeholder archive never did. `size` pads the archive out, for
    the tests about free space.
    """
    pack_plain(Path(archive), Path(index), [(path, b"V" * n) for path, n in entries])
    short = size - Path(archive).stat().st_size
    if short > 0:
        with Path(archive).open("ab") as handle:
            handle.write(b"." * short)
    return Path(index)


def pack_plain(blob: Path, index: Path, rows) -> Path:
    """
    pack(), but with the index in the plain shape XIII ships its scr pair in,
    which is what ff13tool -c writes back. A repack has to read back as a real
    archive now, so the stub has to make one.
    """
    from patcher.archive.entry import ChunkEntry, format_chunk

    sector, body, records = 2048, bytearray(), []
    for path, payload in rows:
        stored = zlib.compress(payload, 6)
        records.append(ChunkEntry(path=path, sector_offset=len(body) // sector,
                                  uncompressed=len(payload), stored=len(stored)))
        body += stored
        body += b"\x00" * (-len(body) % sector)
    Path(blob).write_bytes(bytes(body))

    plain = format_chunk(records)
    deflated = zlib.compress(plain, 9)
    table_at = 12 + 8 * len(records)
    chunk_at = table_at + 12
    head = bytearray(struct.pack("<III", table_at, chunk_at, len(records)))
    head += ff13_records(records)
    head += struct.pack("<III", len(plain), len(deflated), 0)
    head += deflated
    Path(index).write_bytes(bytes(head))
    return Path(index)


def pack(blob: Path, index: Path, rows) -> Path:
    """
    a white_img shaped file: payloads on 2048 byte boundaries, and an index
    beside it saying where each one is.

    The index is named rather than derived, because the game does not derive
    it either: white_imgu's index is filelistu, a zone's is filelist_z010u,
    and the scr pair's is filelist_scru. Three spellings, no rule.

    Real enough for read_entry, which is the only thing that reads one of
    these for content rather than for sizes.
    """
    from patcher.archive.entry import ChunkEntry, format_chunk
    from patcher.archive.filelist import Filelist, save

    sector, body, entries = 2048, bytearray(), []
    for path, payload in rows:
        stored = zlib.compress(payload, 6)
        entries.append(ChunkEntry(path=path, sector_offset=len(body) // sector,
                                  uncompressed=len(payload), stored=len(stored)))
        body += stored
        body += b"\x00" * (-len(body) % sector)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(bytes(body))

    index = Path(index)
    save(index, Filelist(header=b"\x00" * 32, entry_records=b"",
                         chunks=[format_chunk(entries)], original_blobs=[None],
                         edited={0}, entry_count=len(entries)))
    return index


def carries_hd(index, archive, path) -> bool:
    """whether an entry in a real pair holds what the stub tools write for HD"""
    from patcher.archive.filelist import load
    from patcher.mods.hd_zones import read_entry

    return b"HD" in read_entry(Path(archive), list(load(Path(index)).entries()), path)


def vcdiff_patch(source: bytes, windows) -> bytes:
    """
    a VCDIFF patch laid out the way xdelta3 writes one: an adler32 per window,
    each window's copies reading the whole source. `windows` is a list of
    pieces lists, each piece ("copy", source offset, length), ("add", bytes)
    or ("run", byte, length), so a test can put a file across two windows
    """
    import zlib

    def varint(n: int) -> bytes:
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.append(0x80 | (n & 0x7F))
            n >>= 7
        return bytes(reversed(out))

    out = bytearray(b"\xd6\xc3\xc4\x00\x00")
    for pieces in windows:
        built, data, inst, addr = bytearray(), bytearray(), bytearray(), bytearray()
        for piece in pieces:
            if piece[0] == "add":
                built += piece[1]
                inst += bytes([1]) + varint(len(piece[1]))
                data += piece[1]
            elif piece[0] == "run":
                built += bytes([piece[1]]) * piece[2]
                inst += bytes([0]) + varint(piece[2])
                data += bytes([piece[1]])
            else:
                built += source[piece[1]:piece[1] + piece[2]]
                inst += bytes([19]) + varint(piece[2])
                addr += varint(piece[1])
        body = (varint(len(built)) + b"\x00" + varint(len(data)) + varint(len(inst))
                + varint(len(addr)) + (zlib.adler32(bytes(built)) & 0xFFFFFFFF).to_bytes(4, "big")
                + bytes(data) + bytes(inst) + bytes(addr))
        out += bytes([0x05]) + varint(len(source)) + varint(0) + varint(len(body)) + body
    return bytes(out)
