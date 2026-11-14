"""
Reading and rewriting a filelist index. Decoded off real XIII-2 and XIII
indexes, every field tested against them. Full notes in docs/archive-format.md.

    +0    uint32   offset of the chunk table
    +4    uint32   offset of the chunk data
    +8    uint32   entry count
    +12            entry records, 8 bytes each
    table          12 bytes per chunk: (plain length, deflated length, offset)
    chunks         contiguous zlib streams, level 9, no gaps
    padding        to an 8 byte boundary, because the cipher works in blocks
    tail -8        uint32 body length
    tail -4        uint32 checksum

Untouched chunks keep their original deflated bytes, since deflate output
differs between zlib builds and an unedited rebuild has to be byte identical on
any machine. Offsets and the chunk table are always rebuilt from scratch.
verify_roundtrip() rebuilds the user's own file unedited and compares before
anything is written, so a modded index is as fine as a vanilla one.

Entry records are (file code, uint24 position, flags), where position is
chunk_index * 0x8000 + the text's offset in that chunk. XIII uses a plain
uint16 chunk and uint16 offset instead.
"""

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from patcher.archive import crypt
from patcher.archive.entry import END_SENTINEL, ChunkEntry, format_chunk, parse_chunk

DEFLATE_LEVEL = 9      # reproduces all 94 sample chunks byte-identically
TABLE_RECORD = 12
ENTRY_RECORD = 8
ALIGN = 8
# each chunk owns this much of the lookup table's address space, whatever its
# text actually weighs. see the module docstring.
CHUNK_STRIDE = 0x8000


class FilelistError(Exception):
    pass


class VerificationFailed(FilelistError):
    """our rebuild did not reproduce the user's own file"""


@dataclass
class Filelist:
    header: bytes                      # the 32 byte file header, untouched
    entry_records: bytes               # preserved verbatim, see module docstring
    chunks: list = field(default_factory=list)     # plain (inflated) bytes per chunk
    # the deflated bytes exactly as they were read, one per chunk. reused
    # verbatim unless that chunk was edited, so a rebuild never depends on
    # this machine's zlib producing the same output as the game's did.
    original_blobs: list = field(default_factory=list)
    edited: set = field(default_factory=set)       # indexes needing re-deflation
    entry_count: int = 0
    was_encrypted: bool = True
    # bytes after the encrypted body. the cipher only covers body_size bytes
    # from offset 32, and a real file carries a short unencrypted remainder
    # past that. dropping it produced a file 8 bytes short of the original.
    remainder: bytes = b""
    # which shape this was read as. SEALED is the XIII-2 and LR container and
    # PLAIN_0 is XIII's, the two rebuild() knows how to write back, and
    # everything that modifies an index goes through rebuild(). See _layouts().
    layout: str = "sealed"

    @property
    def writable(self) -> bool:
        return self.layout in (SEALED, PLAIN_0)

    def entries(self) -> list:
        """every record across every chunk, sentinel excluded"""
        out = []
        for plain in self.chunks:
            out.extend(parse_chunk(plain))
        return out

    def locate(self, game_path: str):
        """(chunk index, entry) for a path, or (None, None)"""
        wanted = game_path.replace("\\", "/").lower()
        for index, plain in enumerate(self.chunks):
            for entry in parse_chunk(plain):
                if entry.path.replace("\\", "/").lower() == wanted:
                    return index, entry
        return None, None


def _split_chunk(plain: bytes):
    """
    records plus whatever trailer the chunk carries.

    the final chunk ends with a literal "end" that is not an entry. parsing it
    as one puts every downstream count off by one, and dropping it on rebuild
    would produce an index the game does not recognise as complete.
    """
    parts = plain.split(b"\x00")
    trailing = b""
    if parts and parts[-1] == b"":
        parts = parts[:-1]
    if parts and parts[-1] == END_SENTINEL:
        trailing = END_SENTINEL
        parts = parts[:-1]
    return parts, trailing


SEALED = "sealed"        # XIII-2 and LR: 32 byte header, length field, checksum
PLAIN_32 = "plain32"     # a header but no sealed body, everything after it
PLAIN_0 = "plain0"       # no header at all, the body starts at byte zero


def _layouts(raw: bytes):
    """
    the shapes an index might be in, best understood first.

    SEALED is only offered when crypt agrees, so anything that parses today
    takes the same path. The others exist for XIII's filelist_scr pair, which
    is in the clear and also lacks the length and checksum looks_decrypted()
    keys on, so it used to get "decrypted" into noise. Guessing is safe: a
    candidate only counts once every chunk inflates to the length its table
    says.
    """
    yield SEALED, crypt.HEADER_SIZE, True
    yield PLAIN_32, crypt.HEADER_SIZE, False
    yield PLAIN_0, 0, False


def parse(raw: bytes) -> Filelist:
    """
    reads an index in whichever shape it is in, decrypting if it needs it.

    Tries the sealed container first and falls through to the plain ones, so
    nothing that already parsed changes behaviour. Only the sealed shape can be
    written back; see Filelist.writable and rebuild().
    """
    tried = []
    for layout, header_len, sealed in _layouts(raw):
        try:
            return _read(raw, header_len, layout, sealed)
        except (FilelistError, crypt.DecryptError, struct.error, zlib.error, ValueError) as e:
            tried.append(f"{layout}: {e}")

    raise FilelistError("this does not look like a filelist after decryption "
                        f"({'; '.join(tried)})")


def _read(raw: bytes, header_len: int, layout: str, sealed: bool) -> Filelist:
    # the decryption happens here rather than while choosing candidates so a
    # file whose header decrypts to nonsense is one rejected candidate and not
    # an exception out of the loop
    if sealed:
        was_encrypted = crypt.is_encrypted(raw)
        data = crypt.decrypt(raw) if was_encrypted else raw
        if not crypt.looks_decrypted(data):
            raise FilelistError("no length field after decryption")
        size = crypt.body_size(data)
        body = data[header_len:header_len + size]
        remainder = data[header_len + size:]
    else:
        data, was_encrypted = raw, False
        body = data[header_len:]
        remainder = b""

    if len(body) < 12:
        raise FilelistError(f"body is {len(body)} bytes, too short to hold a header")
    table_at, chunk_at, entry_count = struct.unpack_from("<III", body, 0)

    if not (12 <= table_at <= chunk_at <= len(body)):
        raise FilelistError(f"implausible offsets: table {table_at}, chunks {chunk_at}")
    chunk_count, over = divmod(chunk_at - table_at, TABLE_RECORD)
    if over:
        raise FilelistError("the chunk table is not a whole number of records")
    # an empty index is not a thing, and without this a candidate layout whose
    # offsets happen to be equal parses zero chunks and calls itself valid.
    # Inflating every chunk is the real test and there has to be one to inflate.
    if chunk_count < 1 or entry_count < 1:
        raise FilelistError(f"{chunk_count} chunks and {entry_count} entries is not an index")

    chunks, blobs = [], []
    for i in range(chunk_count):
        plain_len, deflated_len, rel = struct.unpack_from("<III", body, table_at + i * TABLE_RECORD)
        blob = body[chunk_at + rel: chunk_at + rel + deflated_len]
        try:
            plain = zlib.decompress(blob)
        except zlib.error as e:
            raise FilelistError(f"chunk {i} would not inflate: {e}") from e
        if len(plain) != plain_len:
            raise FilelistError(
                f"chunk {i} inflated to {len(plain)}, the table says {plain_len}")
        chunks.append(plain)
        blobs.append(blob)

    return Filelist(
        header=data[:header_len],
        entry_records=body[12:table_at],
        chunks=chunks,
        original_blobs=blobs,
        entry_count=entry_count,
        was_encrypted=was_encrypted,
        remainder=remainder,
        layout=layout,
    )


def rebuild(filelist: Filelist, encrypt: bool | None = None) -> bytes:
    """
    assembles an index from its parts, recomputing every offset.

    The sealed container gets a 32 byte header, a big-endian length at 16 and
    a trailing length and checksum. XIII's bare body gets nothing around it.
    PLAIN_32 is only a shape parse() guesses at, no real file has it, so
    writing one refuses.
    """
    if not filelist.writable:
        raise FilelistError(
            f"this index was read as {filelist.layout}, a shape that can be measured "
            "but not written back. Rebuilding it would invent a container the game "
            "does not use.")

    table_at = 12 + len(filelist.entry_records)
    chunk_at = table_at + TABLE_RECORD * len(filelist.chunks)

    table = bytearray()
    blobs = bytearray()
    running = 0
    for index, plain in enumerate(filelist.chunks):
        original = (filelist.original_blobs[index]
                    if index < len(filelist.original_blobs) else None)
        if original is not None and index not in filelist.edited:
            # untouched: reuse the bytes we read. this is what keeps a rebuild
            # byte-identical regardless of the local zlib build.
            blob = original
        else:
            blob = zlib.compress(plain, DEFLATE_LEVEL)
        table += struct.pack("<III", len(plain), len(blob), running)
        blobs += blob
        running += len(blob)

    body = bytearray(struct.pack("<III", table_at, chunk_at, filelist.entry_count))
    body += filelist.entry_records
    body += table
    body += blobs
    if filelist.layout == PLAIN_0:
        return bytes(body)

    # the cipher works on 8 byte blocks, and the last 8 bytes of the body are
    # the length and checksum, so the content ahead of them has to land on a
    # boundary
    body += b"\x00" * ((-len(body)) % ALIGN)
    body_length = len(body)
    body += struct.pack("<I", body_length) + b"\x00" * 4

    out = bytearray(filelist.header)
    # the header records the body length excluding its own trailing 8 bytes,
    # big-endian, which is the one big-endian field in the whole format
    struct.pack_into(">I", out, 16, body_length)
    out += body

    stamped = crypt.apply_checksum(bytes(out))
    should_encrypt = filelist.was_encrypted if encrypt is None else encrypt
    result = crypt.encrypt(stamped) if should_encrypt else stamped
    # the remainder is appended after encryption because the cipher never
    # covered it in the first place
    return result + filelist.remainder


def verify_roundtrip(raw: bytes) -> None:
    """
    parses the index and rebuilds it unchanged, asserting it's byte identical
    to what came in. Compared against this file, whatever state it's in,
    since reproducing their current state is the licence to modify it.
    """
    rebuilt = rebuild(parse(raw))
    if rebuilt == raw:
        return

    detail = f"rebuilt {len(rebuilt)} bytes against {len(raw)}"
    if len(rebuilt) == len(raw):
        first = next((i for i, (a, b) in enumerate(zip(raw, rebuilt)) if a != b), None)
        differing = sum(1 for a, b in zip(raw, rebuilt) if a != b)
        detail = f"{differing} bytes differ, first at offset {first}"
    raise VerificationFailed(
        "could not reproduce this filelist exactly, so it is not safe to rewrite it "
        f"({detail})"
    )


def replace_entry(filelist: Filelist, game_path: str, uncompressed: int, stored: int) -> None:
    """
    updates one entry's sizes in place.

    refuses an edit that changes the serialised length of the record. the
    8-byte entry records hold a running text offset, so a length change would
    silently invalidate every one of them after this point, and the rest of
    those records is not understood well enough to rebuild.
    """
    index, existing = filelist.locate(game_path)
    if existing is None:
        raise FilelistError(f"no entry for {game_path}")

    updated = ChunkEntry(path=existing.path, sector_offset=existing.sector_offset,
                         uncompressed=uncompressed, stored=stored)
    from patcher.archive.entry import format_entry
    before, after = format_entry(existing), format_entry(updated)
    if len(before) != len(after):
        raise FilelistError(
            f"this edit changes the record from {len(before)} to {len(after)} characters, "
            "which would shift every entry record after it. not supported"
        )

    records, trailing = _split_chunk(filelist.chunks[index])
    target = before.encode()
    for i, record in enumerate(records):
        if record == target:
            records[i] = after.encode()
            break
    else:
        raise FilelistError(f"{game_path} was located but its record could not be matched")

    rebuilt = b"\x00".join(records) + b"\x00"
    if trailing:
        rebuilt += trailing + b"\x00"
    filelist.chunks[index] = rebuilt
    # this one has to be recompressed; every other chunk keeps its original bytes
    filelist.edited.add(index)


def lookup_positions(entry_records: bytes) -> list[tuple[int, int]]:
    """every entry record as (chunk index, byte offset of its text in that chunk)"""
    out = []
    for i in range(0, len(entry_records), ENTRY_RECORD):
        position = int.from_bytes(entry_records[i + 4:i + 7], "little")
        out.append(divmod(position, CHUNK_STRIDE))
    return out


def lookup_slots(filelist: Filelist) -> list[tuple[int, int]]:
    """lookup_positions() for whichever game's records these are"""
    if filelist.layout == SEALED:
        return lookup_positions(filelist.entry_records)
    return [struct.unpack_from("<HH", filelist.entry_records, i + 4)
            for i in range(0, len(filelist.entry_records), ENTRY_RECORD)]


def records_problem(filelist: Filelist) -> str:
    """
    why the lookup records don't point one each at the records in the chunks,
    or "". Asked before an edit moves anything, because a remap through records
    we've misread writes a plausible index over a real one. An index with no
    lookup records at all has nothing to keep in step.
    """
    if not filelist.entry_records:
        return ""
    if len(filelist.entry_records) != ENTRY_RECORD * filelist.entry_count:
        return (f"{len(filelist.entry_records)} bytes of lookup records for "
                f"{filelist.entry_count} entries")
    starts = []
    for plain in filelist.chunks:
        records, _ = _split_chunk(plain)
        starts.append(set(_offsets(records)))
    seen = set()
    for chunk, offset in lookup_slots(filelist):
        if chunk >= len(starts) or offset not in starts[chunk]:
            return (f"a lookup record points at offset {offset} in chunk {chunk}, which is "
                    "no record's start")
        if (chunk, offset) in seen:
            return f"two lookup records point at the record at offset {offset} in chunk {chunk}"
        seen.add((chunk, offset))
    if len(seen) != sum(len(s) for s in starts):
        return f"{len(seen)} lookup records for {sum(len(s) for s in starts)} records"
    return ""


def _offsets(records: list) -> list[int]:
    """where each record's text starts, given the records in order"""
    out, running = [], 0
    for record in records:
        out.append(running)
        running += len(record) + 1
    return out


def _remap_lookup(filelist: Filelist, chunk_index: int,
                  old: list[int], new: list[int]) -> None:
    """
    repoints this chunk's lookup entries from the old record offsets to the
    new. A remap, not a delta, since records move by different amounts. Order
    and count survive an edit, so the nth old is the nth new, and a value not
    in `old` means the table disagrees with its chunk, which raises.
    """
    _remap(filelist, {chunk_index: dict(zip(old, new))})


def _remap(filelist: Filelist, moves: dict) -> None:
    """_remap_lookup for any number of chunks in one pass, chunk -> {old: new}"""
    moves = {chunk: m for chunk, m in moves.items() if any(a != b for a, b in m.items())}
    if not moves:
        return
    sealed = filelist.layout == SEALED
    # XIII's offsets are a plain uint16, XIII-2's live in a 0x8000 slice each
    ceiling = CHUNK_STRIDE if sealed else 0x10000
    records = bytearray(filelist.entry_records)
    for i in range(0, len(records), ENTRY_RECORD):
        if sealed:
            chunk, offset = divmod(int.from_bytes(records[i + 4:i + 7], "little"), CHUNK_STRIDE)
        else:
            chunk, offset = struct.unpack_from("<HH", records, i + 4)
        shifts = moves.get(chunk)
        if shifts is None:
            continue
        moved = shifts.get(offset)
        if moved is None:
            raise FilelistError(
                f"a lookup entry points at offset {offset} in chunk {chunk}, "
                "which is not the start of any record there"
            )
        if moved >= ceiling:
            raise FilelistError(
                f"chunk {chunk} would grow past the {ceiling:#x} byte slice "
                "the lookup table gives it, which would collide with the next chunk"
            )
        if sealed:
            records[i + 4:i + 7] = (chunk * CHUNK_STRIDE + moved).to_bytes(3, "little")
        else:
            struct.pack_into("<H", records, i + 6, moved)
    filelist.entry_records = bytes(records)


def relocate_entry(filelist: Filelist, game_path: str, sector_offset: int,
                   uncompressed: int, stored: int) -> None:
    """
    points an entry somewhere else in white_img, at any size.

    replace_entry can't change a record's text length; this rewrites the
    lookup table so it can. Every occurrence moves: 274 paths in the XIII-2
    sample appear more than once, and repointing only the first would load HD
    or vanilla depending on which copy the game reached.
    """
    relocate_many(filelist, {game_path: (sector_offset, uncompressed, stored)})


def relocate_many(filelist: Filelist, moves: dict) -> None:
    """
    relocate_entry() for every path in `moves`, path -> (sector_offset,
    uncompressed, stored), in one pass over the chunks. An HD voice set is
    thousands of moves, and a pass per path read the whole index each time.
    Every path has to be in here or nothing changes.
    """
    from patcher.archive.entry import format_entry, parse_entry

    wanted = {path.replace("\\", "/").lower(): spot for path, spot in moves.items()}
    found, edits, shifts = set(), {}, {}
    for index, plain in enumerate(filelist.chunks):
        records, trailing = _split_chunk(plain)
        old, hit = _offsets(records), False
        for i, record in enumerate(records):
            entry = parse_entry(record.decode("utf-8"))
            key = entry.path.replace("\\", "/").lower()
            if key not in wanted:
                continue
            sector_offset, uncompressed, stored = wanted[key]
            # each occurrence keeps its own spelling of the path, which is the
            # text the game looks it up by
            records[i] = format_entry(ChunkEntry(path=entry.path, sector_offset=sector_offset,
                                                 uncompressed=uncompressed,
                                                 stored=stored)).encode("utf-8")
            found.add(key)
            hit = True
        if hit:
            rebuilt = b"\x00".join(records) + b"\x00"
            if trailing:
                rebuilt += trailing + b"\x00"
            edits[index] = rebuilt
            shifts[index] = dict(zip(old, _offsets(records)))

    missing = sorted(set(wanted) - found)
    if missing:
        raise FilelistError(f"no entry for {missing[0]}")
    _remap(filelist, shifts)
    for index, rebuilt in edits.items():
        filelist.chunks[index] = rebuilt
        filelist.edited.add(index)


def by_code(filelist: Filelist) -> dict:
    """
    file code -> every record its lookup entries point at, in lookup order.
    The code is what the game asks for a file by and the text is what every
    tool goes by, and the two can disagree: Steam's XIII-2 points six
    costumes' codes at records named after c201 and c204, which is how an
    update cut the console outfits without renumbering anything
    """
    from patcher.archive.entry import parse_entry

    texts = []
    for plain in filelist.chunks:
        records, _ = _split_chunk(plain)
        texts.append(dict(zip(_offsets(records), records)))
    out = {}
    for i, (chunk, offset) in enumerate(lookup_slots(filelist)):
        record = texts[chunk].get(offset) if chunk < len(texts) else None
        if record is None:
            raise FilelistError(f"lookup entry {i} points at no record")
        code = int.from_bytes(filelist.entry_records[i * ENTRY_RECORD:i * ENTRY_RECORD + 4],
                              "little")
        out.setdefault(code, []).append(parse_entry(record.decode("utf-8")))
    return out


def rename_records(filelist: Filelist, renames: dict) -> None:
    """
    the record each code points at given a new path, its sizes and place
    kept. Only for a code with one record: two would leave no telling which
    was meant. Every code has to be there or nothing changes
    """
    from patcher.archive.entry import format_entry, parse_entry

    slots = lookup_slots(filelist)
    wanted, where = dict(renames), {}
    for i, (chunk, offset) in enumerate(slots):
        code = int.from_bytes(filelist.entry_records[i * ENTRY_RECORD:i * ENTRY_RECORD + 4],
                              "little")
        if code in wanted:
            if code in where:
                raise FilelistError(f"code {code:#x} has more than one record")
            where[code] = (chunk, offset)
    missing = sorted(set(wanted) - set(where))
    if missing:
        raise FilelistError(f"no record for code {missing[0]:#x}")
    by_chunk = {}
    for code, (chunk, offset) in where.items():
        by_chunk.setdefault(chunk, {})[offset] = wanted[code]
    shifts, edits = {}, {}
    for chunk, named in by_chunk.items():
        records, trailing = _split_chunk(filelist.chunks[chunk])
        old = _offsets(records)
        for i, start in enumerate(old):
            if start in named:
                entry = parse_entry(records[i].decode("utf-8"))
                records[i] = format_entry(ChunkEntry(
                    path=named[start], sector_offset=entry.sector_offset,
                    uncompressed=entry.uncompressed, stored=entry.stored)).encode("utf-8")
        rebuilt = b"\x00".join(records) + b"\x00"
        if trailing:
            rebuilt += trailing + b"\x00"
        edits[chunk] = rebuilt
        shifts[chunk] = dict(zip(old, _offsets(records)))
    _remap(filelist, shifts)
    for chunk, rebuilt in edits.items():
        filelist.chunks[chunk] = rebuilt
        filelist.edited.add(chunk)


def load(path: Path) -> Filelist:
    return parse(Path(path).read_bytes())


def save(path: Path, filelist: Filelist) -> None:
    Path(path).write_bytes(rebuild(filelist))
