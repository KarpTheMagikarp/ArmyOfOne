"""
Chunk entries from a filelist index. No file IO; every offset the rest of the
code trusts comes from here.

On disk, after decryption and inflation, a chunk is NUL-separated records:

    <sector_offset>:<uncompressed_size>:<stored_size>:<path>
    0:4e9a68:27052f:chr/pc/c001/bin/c001.win32.imgb

Lowercase hex, no 0x. (WhiteBinTools' dumps add a `code|` prefix, but that's
its text format, not the archive's.) sector_offset is in 2048-byte units, and
every next entry starts at ceil((offset * 2048 + stored) / 2048), which holds
for all 14,352 pairs in the XIII-2 index.
"""

SECTOR = 2048

from dataclasses import dataclass

SEPARATOR = b"\x00"


class EntryFormatError(ValueError):
    """a record that does not match the expected shape"""


@dataclass(frozen=True)
class ChunkEntry:
    """
    one record from a filelist chunk.

    sector_offset is in 2048-byte units. uncompressed == stored means the
    file is held raw; otherwise it is zlib deflated.

    a handful of entries carry uncompressed=0 with stored=8. those are stubs
    for assets the build left out, not a contradiction, and they must not be
    treated as replaceable.
    """

    path: str
    sector_offset: int
    uncompressed: int
    stored: int

    @property
    def byte_offset(self) -> int:
        """where the payload actually starts in white_img"""
        return self.sector_offset * SECTOR

    @property
    def is_compressed(self) -> bool:
        return self.uncompressed != self.stored

    @property
    def is_stub(self) -> bool:
        """an empty placeholder, present in the index but holding nothing"""
        return self.uncompressed == 0

    @property
    def sector_span(self) -> int:
        """
        sectors this entry occupies, including the padding to the next
        boundary. a replacement may use the whole span, not just `stored`.
        """
        return -(-self.stored // SECTOR)

    def fits(self, replacement_stored_size: int) -> bool:
        """
        whether a replacement can go in without moving anything after it.

        the test is against the SECTOR SPAN, not the stored size: the padding
        between the end of a file and the next sector boundary is dead space
        that a slightly larger replacement can legitimately grow into. that
        buys real room, since the average slack is about a kilobyte.
        """
        return replacement_stored_size <= self.sector_span * SECTOR

    @property
    def raw_fields(self) -> tuple[int, int, int]:
        return (self.sector_offset, self.uncompressed, self.stored)


def parse_entry(text: str) -> ChunkEntry:
    bits = text.split(":", 3)
    if len(bits) != 4:
        raise EntryFormatError(f"expected a:b:c:path, got {text!r}")
    a, b, c, path = bits
    if not path:
        raise EntryFormatError(f"entry has no path: {text!r}")
    try:
        return ChunkEntry(path=path, sector_offset=int(a, 16),
                          uncompressed=int(b, 16), stored=int(c, 16))
    except ValueError as e:
        raise EntryFormatError(f"bad hex in {text!r}: {e}") from e


def format_entry(entry: ChunkEntry) -> str:
    """
    the exact inverse of parse_entry.

    round-tripping is the cheapest proof the format is understood. it already
    earned its keep once: it is what showed the documented pipe format was not
    the on-disk format, before a single byte was written anywhere.
    """
    return f"{entry.sector_offset:x}:{entry.uncompressed:x}:{entry.stored:x}:{entry.path}"


# the final chunk of an index closes with a literal "end". it is a structural
# marker, not a record, and counting it puts every downstream total off by one.
END_SENTINEL = b"end"


def parse_chunk(data: bytes) -> list[ChunkEntry]:
    """every record in one inflated chunk. records are NUL separated."""
    return [parse_entry(part.decode("utf-8"))
            for part in data.split(SEPARATOR) if part and part != END_SENTINEL]


def format_chunk(entries: list[ChunkEntry]) -> bytes:
    """
    rebuilds a chunk byte-for-byte.

    the trailing NUL matters: real chunks end with one, and a rebuilt chunk
    that drops it is a different length, which shifts everything downstream.
    """
    if not entries:
        return b""
    return SEPARATOR.join(format_entry(e).encode("utf-8") for e in entries) + SEPARATOR


def chain_is_consistent(entries: list[ChunkEntry]) -> bool:
    """
    every entry should begin at the sector boundary after the previous one
    ends. this is the invariant that identified the format, and it is also the
    cheapest possible sanity check on an index we are about to rewrite.
    """
    for current, following in zip(entries, entries[1:]):
        end = current.byte_offset + current.stored
        if -(-end // SECTOR) != following.sector_offset:
            return False
    return True


def find(entries: list[ChunkEntry], game_path: str) -> ChunkEntry | None:
    """
    locates a record by its in-game path.

    separator and case insensitive: mod readmes and BAT files write these with
    backslashes (vfx\\field\\weather\\weather07\\veffs.jp.win32.imgb) while the
    index uses forward slashes, and a lookup that misses is indistinguishable
    from the file genuinely being absent.
    """
    wanted = game_path.replace("\\", "/").lower()
    return next((e for e in entries if e.path.replace("\\", "/").lower() == wanted), None)
