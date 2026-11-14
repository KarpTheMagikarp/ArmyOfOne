import struct
import zlib
from pathlib import Path

import pytest

from patcher.archive import crypt, filelist
from patcher.archive.filelist import FilelistError, VerificationFailed

SAMPLE = Path(__file__).resolve().parent / "data" / "filelistu_sample.bin"
WEATHER07 = "vfx/field/weather/weather07/veffs.jp.win32.imgb"
WEATHER08 = "vfx/field/weather/weather08/veffs.jp.win32.imgb"


def raw():
    if not SAMPLE.is_file():
        pytest.skip("no sample filelist")
    return SAMPLE.read_bytes()


def test_parse_matches_the_headers_own_entry_count():
    fl = filelist.parse(raw())
    assert len(fl.entries()) == fl.entry_count == 14381
    assert len(fl.chunks) == 94


def test_the_preflight_reproduces_the_users_own_file():
    """
    the licence to modify. it compares against THIS file, not a vanilla one,
    so a heavily modded index verifies exactly as readily as a fresh install.
    """
    filelist.verify_roundtrip(raw())


def test_rebuild_is_byte_identical_with_no_edits():
    data = raw()
    assert filelist.rebuild(filelist.parse(data)) == data


def test_the_unencrypted_remainder_is_preserved():
    """
    the cipher covers body_size bytes from offset 32; a real file carries a
    short plain remainder past that. dropping it left the rebuild 8 bytes short.
    """
    fl = filelist.parse(raw())
    assert fl.remainder, "this sample is only a useful witness if it has one"
    assert filelist.rebuild(fl).endswith(fl.remainder)


def test_a_corrupted_index_fails_the_preflight_rather_than_being_rewritten():
    data = bytearray(raw())
    data[crypt.HEADER_SIZE + 500] ^= 0xFF          # damage the ciphertext
    with pytest.raises((VerificationFailed, FilelistError)):
        filelist.verify_roundtrip(bytes(data))


def test_injecting_leviathans_tears_changes_only_its_two_entries():
    data = raw()
    original = filelist.parse(data)
    fl = filelist.parse(data)

    for path in (WEATHER07, WEATHER08):
        filelist.replace_entry(fl, path, uncompressed=90512, stored=32029)

    edited = filelist.rebuild(fl)
    back = filelist.parse(edited)

    for path in (WEATHER07, WEATHER08):
        _, entry = back.locate(path)
        assert entry.stored == 32029
        assert entry.uncompressed == 90512
        # an index edit must never move the payload
        assert entry.sector_offset == original.locate(path)[1].sector_offset

    untouched = lambda f: [e for e in f.entries() if "weather0" not in e.path]
    assert untouched(back) == untouched(original)
    assert back.entry_count == original.entry_count


def test_an_edited_index_still_verifies_against_itself():
    """once written, the next run's pre-flight has to pass on the new state"""
    fl = filelist.parse(raw())
    filelist.replace_entry(fl, WEATHER07, uncompressed=90512, stored=32029)
    filelist.verify_roundtrip(filelist.rebuild(fl))


def test_an_edit_that_would_change_a_records_length_is_refused():
    """
    the 8-byte entry records hold a running text offset, so a longer record
    silently invalidates every one after it. the rest of those records is not
    understood, so this refuses rather than guesses.
    """
    fl = filelist.parse(raw())
    with pytest.raises(FilelistError, match="characters"):
        filelist.replace_entry(fl, WEATHER07, uncompressed=0xFFFFFFF, stored=0xFFFFFFF)


def test_an_unknown_path_is_refused():
    fl = filelist.parse(raw())
    with pytest.raises(FilelistError, match="no entry for"):
        filelist.replace_entry(fl, "not/a/real/path.imgb", uncompressed=1, stored=1)


def test_the_chunk_table_is_recalculated_not_reused():
    """
    an edit changes a chunk's deflated length, so offsets must be rebuilt.
    forcing a chunk to compress differently proves the table follows.
    """
    fl = filelist.parse(raw())
    fl.chunks[0] = fl.chunks[0] + b"0:1:1:padding/entry.imgb\x00"
    # editing a chunk directly means saying so; replace_entry does this for you
    fl.edited.add(0)

    rebuilt = filelist.rebuild(fl)
    data = crypt.decrypt(rebuilt)
    body = data[crypt.HEADER_SIZE:crypt.HEADER_SIZE + crypt.body_size(data)]
    table_at, chunk_at, _ = struct.unpack_from("<III", body, 0)

    running = 0
    for i in range(len(fl.chunks)):
        plain_len, deflated_len, rel = struct.unpack_from("<III", body, table_at + i * 12)
        assert rel == running, f"chunk {i} offset was not recalculated"
        blob = body[chunk_at + rel: chunk_at + rel + deflated_len]
        assert len(zlib.decompress(blob)) == plain_len
        running += deflated_len


def test_the_end_sentinel_survives_a_rebuild():
    """an index without it is not recognised as complete"""
    fl = filelist.parse(raw())
    assert fl.chunks[-1].rstrip(b"\x00").endswith(b"end")
    back = filelist.parse(filelist.rebuild(fl))
    assert back.chunks[-1].rstrip(b"\x00").endswith(b"end")


def test_the_sentinel_is_not_counted_as_an_entry():
    fl = filelist.parse(raw())
    assert all(e.path != "end" for e in fl.entries())


def test_a_plaintext_index_is_rebuilt_without_encrypting(tmp_path):
    """XIII does not encrypt, so a rebuild must not introduce encryption"""
    payload = zlib.compress(b"0:10:10:a/b.imgb\x00end\x00", 9)
    entry_records = b"\x00" * 8
    table_at = 12 + len(entry_records)
    chunk_at = table_at + 12
    body = bytearray(struct.pack("<III", table_at, chunk_at, 1))
    body += entry_records
    body += struct.pack("<III", len(zlib.decompress(payload)), len(payload), 0)
    body += payload
    body += b"\x00" * ((-len(body)) % 8)
    length = len(body)
    body += struct.pack("<I", length) + b"\x00" * 4
    out = bytearray(32)
    struct.pack_into(">I", out, 16, length)
    out += body
    data = crypt.apply_checksum(bytes(out))

    fl = filelist.parse(data)
    assert fl.was_encrypted is False
    assert filelist.rebuild(fl) == data


def test_an_untouched_chunk_keeps_its_original_deflated_bytes():
    """
    deflate output is not stable across zlib builds. the first real Windows
    run rebuilt 928 bytes larger than the file it read, and the pre-flight
    correctly refused to write. reusing the bytes we read removes the
    dependency entirely.
    """
    import zlib as _z
    real = _z.compress
    from unittest import mock

    data = raw()
    with mock.patch.object(filelist.zlib, "compress",
                           lambda payload, level=9: real(payload, 6)):
        assert filelist.rebuild(filelist.parse(data)) == data
        filelist.verify_roundtrip(data)


def test_only_the_edited_chunk_is_recompressed():
    fl = filelist.parse(raw())
    assert fl.edited == set()
    filelist.replace_entry(fl, WEATHER07, uncompressed=90512, stored=32029)
    assert len(fl.edited) == 1, "one chunk changed, so one chunk recompresses"


def test_an_edit_survives_even_under_a_different_deflate():
    import zlib as _z
    real = _z.compress
    from unittest import mock

    with mock.patch.object(filelist.zlib, "compress",
                           lambda payload, level=9: real(payload, 6)):
        fl = filelist.parse(raw())
        filelist.replace_entry(fl, WEATHER07, uncompressed=90512, stored=32029)
        edited = filelist.rebuild(fl)
        _, entry = filelist.parse(edited).locate(WEATHER07)
        assert entry.stored == 32029
        filelist.verify_roundtrip(edited)


def body_of(entries=(("a/b.imgb", 10, 10),)):
    """the part every layout has in common: header triple, table, chunks"""
    text = "".join(f"{i}:{u}:{s}:{p}\x00" for i, (p, u, s) in enumerate(entries)) + "end\x00"
    payload = zlib.compress(text.encode(), 9)
    entry_records = b"\x00" * (8 * len(entries))
    table_at = 12 + len(entry_records)
    chunk_at = table_at + 12
    body = bytearray(struct.pack("<III", table_at, chunk_at, len(entries)))
    body += entry_records
    body += struct.pack("<III", len(text.encode()), len(payload), 0)
    body += payload
    return bytes(body)


def test_an_index_with_a_header_but_no_length_field_is_still_readable():
    """
    XIII's own filelist_scr pair. "Ships them in the clear" turned out to mean
    without the trailing length and checksum too, so looks_decrypted() said no,
    detection concluded it must be encrypted, decrypted it into noise and
    reported that the noise was not a filelist. A user's run log said "could
    not read the index ahead of time" and the preflight silently did nothing.
    """
    data = bytes(32) + body_of()

    fl = filelist.parse(data)

    assert fl.layout == filelist.PLAIN_32
    assert fl.entry_count == 1
    assert [e.path for e in fl.entries()] == ["a/b.imgb"]


def test_an_index_with_no_header_at_all_is_still_readable():
    fl = filelist.parse(body_of())

    assert fl.layout == filelist.PLAIN_0
    assert [e.path for e in fl.entries()] == ["a/b.imgb"]


def test_the_sealed_container_still_wins_when_it_fits():
    """
    the alternatives must never change what a file that already parsed does,
    because everything that rewrites an index goes through that path
    """
    fl = filelist.parse(raw())

    assert fl.layout == filelist.SEALED
    assert fl.writable


def test_a_shape_that_cannot_be_written_back_refuses_to_be():
    """
    a header with no sealed body is a shape parse() guesses at and no real file
    has turned up in. Writing one would invent a container the game has never
    seen, so the read privilege stops at reading.
    """
    fl = filelist.parse(bytes(32) + body_of())

    assert fl.layout == filelist.PLAIN_32
    assert not fl.writable
    with pytest.raises(FilelistError, match="not written back"):
        filelist.rebuild(fl)


def test_a_bare_body_writes_back_as_a_bare_body():
    """XIII's shape: nothing before the body and nothing after the last chunk"""
    data = body_of((("a/b.imgb", 10, 10), ("c/d.trb", 0x20, 0x18)))

    fl = filelist.parse(data)

    assert fl.writable
    assert filelist.rebuild(fl) == data


def test_noise_is_still_refused():
    """
    three candidate layouts is three chances to accept rubbish, so the verdict
    is not a heuristic: every chunk a candidate declares has to inflate to
    exactly the length its own table gives. Noise does not do that.
    """
    import os
    for _ in range(50):
        with pytest.raises(FilelistError):
            filelist.parse(os.urandom(4096))


def test_an_empty_index_is_not_a_valid_layout():
    """
    without this, a candidate whose two offsets land equal parses zero chunks,
    inflates nothing, and passes a check that never ran
    """
    body = struct.pack("<III", 12, 12, 0)

    with pytest.raises(FilelistError):
        filelist.parse(bytes(32) + body)


def test_the_failure_says_what_was_tried():
    """
    "this does not look like a filelist" with three layouts behind it is not
    something anyone can act on
    """
    with pytest.raises(FilelistError) as caught:
        filelist.parse(b"\x00" * 200)

    for layout in (filelist.SEALED, filelist.PLAIN_32, filelist.PLAIN_0):
        assert layout in str(caught.value)


FF13_SAMPLE = Path(__file__).resolve().parent / "data" / "filelist_scru_ff13_sample.bin"


def ff13_raw():
    if not FF13_SAMPLE.is_file():
        pytest.skip("no XIII sample filelist")
    return FF13_SAMPLE.read_bytes()


def test_xiii_ships_a_bare_body_with_no_header_at_all():
    """
    The shape the plain layouts were guessed at, now measured. crypt's "XIII
    ships them in the clear" was right about the cipher and wrong about the
    container: there is no 32 byte header, no length field and no checksum,
    just the body from byte zero.
    """
    book = filelist.parse(ff13_raw())

    assert book.layout == filelist.PLAIN_0
    assert book.header == b""
    assert book.remainder == b""
    assert book.was_encrypted is False


def test_the_numbers_match_what_ff13tool_reported_about_the_same_file():
    """
    the tool's own account of it, from a run log:

        File list processed. 3076 files listed in 19 file chunks.
        Extracted 3076 files in total.

    Two independent readers agreeing on both numbers is the strongest check
    available without the game.
    """
    book = filelist.parse(ff13_raw())

    assert book.entry_count == 3076
    assert len(book.chunks) == 19
    assert len(book.entries()) == 3076


def test_the_eight_byte_lookup_record_holds_for_xiii_too():
    """
    the format was decoded against a XIII-2 sample. One record per entry, and
    nothing left over, says the same layout is in use here.
    """
    book = filelist.parse(ff13_raw())

    assert len(book.entry_records) == 8 * book.entry_count


def test_the_survey_agrees_with_the_run_it_was_written_for():
    """
    [prep] 3076 files, 1.3 GB unpacked, longest path 93 of 259, off a real
    install. The size is what the free space check now spends instead of the
    flat 20GB guess.
    """
    from patcher.mods.hd_preflight import survey_extraction

    found = survey_extraction(FF13_SAMPLE, Path("C:/Users/Someone/Downloads/FF XIII HD"))

    assert found.entries == 3076
    assert round(found.bytes_needed / 1024 ** 3, 1) == 1.3
    assert found.ok and not found.too_long


def test_a_real_xiii_index_writes_back_byte_for_byte():
    """
    the licence to edit one, same as XIII-2's: no header invented, nothing
    after the chunks, and every byte where the game put it
    """
    book = filelist.parse(ff13_raw())

    assert book.writable
    assert filelist.rebuild(book) == ff13_raw()
    filelist.verify_roundtrip(ff13_raw())


def test_xiiis_lookup_records_are_a_chunk_and_an_offset():
    """
    uint16 chunk at +4, uint16 offset at +6, where XIII-2 packs a 24 bit
    position. 3076 records onto 3076 record starts, none twice, is the format
    and not a coincidence
    """
    book = filelist.parse(ff13_raw())

    slots = filelist.lookup_slots(book)
    assert slots[:3] == [(0, 0), (0, 48), (0, 96)]
    assert len(set(slots)) == 3076
    assert filelist.records_problem(book) == ""


def test_xiii_grows_an_entry_and_keeps_its_lookup_in_step():
    """relocate_entry on XIII's records, the way an HD copy into the other voice set needs it"""
    book = filelist.parse(ff13_raw())
    first = book.entries()[0]

    filelist.relocate_entry(book, first.path, 0xABCDEF, 9_000_000, 8_000_000)
    again = filelist.parse(filelist.rebuild(book))

    assert filelist.records_problem(again) == ""
    moved = next(e for e in again.entries() if e.path == first.path)
    assert moved.raw_fields == (0xABCDEF, 9_000_000, 8_000_000)
    assert [e.raw_fields for e in again.entries()][1:] == \
        [e.raw_fields for e in filelist.parse(ff13_raw()).entries()][1:]


def test_records_that_dont_line_up_are_named_before_anything_moves():
    """the fixtures used to write zeroes there, which a remap would read as every record at 0"""
    book = filelist.parse(ff13_raw())
    book.entry_records = b"\x00" * len(book.entry_records)
    assert "two lookup records" in filelist.records_problem(book)

    book = filelist.parse(ff13_raw())
    book.entry_records = book.entry_records[:6] + b"\x01\x00" + book.entry_records[8:]
    assert "no record's start" in filelist.records_problem(book)


def test_the_paths_are_the_ones_the_tool_extracted():
    """spot checks against the first and last lines of that run's log"""
    entries = filelist.parse(ff13_raw()).entries()
    paths = [e.path for e in entries]

    assert paths[0] == "chr/pc/c001/bin/c001.win32.imgb"
    assert paths[-1] == "zone/z106/scr255.clb"
    # the one the injector wanted and could not find, so this is the path the
    # missing models/orig step has to produce
    assert "chr/weapon/w001/bin/w001.win32.trb" in paths
