import pytest

from patcher.archive.entry import (
    ChunkEntry,
    EntryFormatError,
    find,
    format_chunk,
    format_entry,
    parse_chunk,
    parse_entry,
)

# verbatim records from a real XIII-2 filelistu.win32.bin, after decryption
# and zlib inflation. NOT the pipe-prefixed shape in the WhiteBinTools README,
# which is that tool's text dump format rather than what the archive stores.
REAL = [
    "0:4e9a68:27052f:chr/pc/c001/bin/c001.win32.imgb",
    "4e1:1524a3:96de8:chr/pc/c001/bin/c001.win32.trb",
    "60f:3d7f5:c0ed:chr/pc/c001/bin/c001_def.win32.mpk",
    "628:3f515:cfc8:chr/pc/c001/bin/c001_rain.win32.mpk",
]


def test_parses_a_real_record():
    entry = parse_entry(REAL[0])
    assert entry.path == "chr/pc/c001/bin/c001.win32.imgb"
    assert entry.raw_fields == (0, 0x4E9A68, 0x27052F)
    assert entry.byte_offset == 0
    assert entry.is_compressed


def test_the_sector_chain_holds_on_real_consecutive_entries():
    """
    the invariant that identified the format: each entry starts at the sector
    boundary after the previous one ends. it held for 14,352 of 14,352 pairs
    in a real index.
    """
    from patcher.archive.entry import chain_is_consistent
    assert chain_is_consistent([parse_entry(l) for l in REAL])


def test_sector_offsets_are_scaled_not_raw_bytes():
    """788,337 sectors is 1.5GB, which is why it looked too small to be one"""
    entry = parse_entry("c0771:100:100:some/file.imgb")
    assert entry.sector_offset == 0xC0771
    assert entry.byte_offset == 0xC0771 * 2048
    assert entry.byte_offset / 1024 ** 3 == pytest.approx(1.50, abs=0.01)


def test_a_replacement_may_grow_into_the_sector_padding():
    """the slack between a file's end and the next boundary is usable space"""
    entry = parse_entry("0:1000:801:a.imgb")   # 0x801 = 2049, spans 2 sectors
    assert entry.sector_span == 2
    assert entry.fits(2049)
    assert entry.fits(4096)
    assert not entry.fits(4097)


def test_stub_entries_are_recognised():
    """uncompressed=0 with stored=8 is a placeholder, not a replaceable file"""
    entry = parse_entry("100:0:8:chr/pc/c110/bin/c110.win32.imgb")
    assert entry.is_stub


@pytest.mark.parametrize("line", REAL)
def test_records_round_trip_byte_for_byte(line):
    """this property is what proved the documented format was the wrong one"""
    assert format_entry(parse_entry(line)) == line


def test_a_chunk_round_trips_including_its_trailing_nul():
    """
    a rebuilt chunk that drops the trailing NUL is a different length, which
    shifts everything downstream of it.
    """
    blob = b"\x00".join(line.encode() for line in REAL) + b"\x00"
    entries = parse_chunk(blob)
    assert len(entries) == 4
    assert format_chunk(entries) == blob


def test_the_old_pipe_prefixed_format_is_rejected():
    """
    guards the correction: WhiteBinTools' dump format must not be mistaken for
    the on-disk format again.
    """
    with pytest.raises(EntryFormatError):
        parse_entry("2826572288|631a7:1b4867:1b4867:sound/pack/music.win32.scd")


def test_a_path_containing_a_colon_survives():
    line = "a:b:c:some/odd:name.win32.imgb"
    assert parse_entry(line).path == "some/odd:name.win32.imgb"
    assert format_entry(parse_entry(line)) == line


@pytest.mark.parametrize("bad", ["", "a:b:c", "zz:b:c:path", "a:b:c:"])
def test_malformed_records_raise_rather_than_guess(bad):
    with pytest.raises(EntryFormatError):
        parse_entry(bad)


def test_lookup_ignores_separator_and_case():
    entries = parse_chunk(b"\x00".join(line.encode() for line in REAL) + b"\x00")
    assert find(entries, "chr\\pc\\c001\\bin\\c001.win32.trb") is not None
    assert find(entries, "CHR/PC/C001/BIN/C001.WIN32.IMGB") is not None
    assert find(entries, "vfx/nope.imgb") is None


def test_empty_chunk_is_empty_not_a_lone_nul():
    assert parse_chunk(b"") == []
    assert format_chunk([]) == b""
