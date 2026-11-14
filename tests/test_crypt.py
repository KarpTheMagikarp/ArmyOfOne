import random
import struct
import time
import zlib
from pathlib import Path

import pytest

from patcher.archive import crypt

REAL = Path("/mnt/user-data/uploads/filelistu_win32.bin")
FIXTURE = Path(__file__).resolve().parent / "data" / "filelistu_sample.bin"


def real_filelist():
    for candidate in (FIXTURE, REAL):
        if candidate.is_file():
            return candidate.read_bytes()
    pytest.skip("no real filelist available")


def test_a_real_xiii2_filelist_is_detected_as_encrypted():
    raw = real_filelist()
    assert crypt.is_encrypted(raw)
    assert not crypt.looks_decrypted(raw)


def test_decrypt_passes_the_formats_own_self_check():
    """the last four bytes of the body hold the body length"""
    assert crypt.looks_decrypted(crypt.decrypt(real_filelist()))


def test_decrypt_then_encrypt_is_byte_identical():
    """
    the gate on everything else. bricking the index is the one failure that
    costs a 40GB reinstall, so the writer is not allowed near a real install
    until this holds on a real file.
    """
    raw = real_filelist()
    assert crypt.encrypt(crypt.decrypt(raw)) == raw


def test_our_checksum_matches_the_one_already_in_the_file():
    """computed independently, so a coincidental round trip cannot hide a bug"""
    dec = crypt.decrypt(real_filelist())
    size = crypt.body_size(dec)
    stored = struct.unpack_from("<I", dec, crypt.HEADER_SIZE + size - 4)[0]
    assert crypt.checksum(dec) == stored


def test_apply_checksum_leaves_an_untouched_file_alone():
    dec = crypt.decrypt(real_filelist())
    assert crypt.apply_checksum(dec) == dec


def test_editing_the_body_changes_the_checksum():
    dec = bytearray(crypt.decrypt(real_filelist()))
    before = crypt.checksum(bytes(dec))
    dec[crypt.HEADER_SIZE] = (dec[crypt.HEADER_SIZE] + 1) & 0xFF
    assert crypt.checksum(bytes(dec)) != before


def test_an_edited_index_survives_the_full_cycle():
    """
    edit, re-stamp the checksum, encrypt, decrypt again. the edit has to come
    back and the self-check has to pass, or a rewritten index would be
    rejected by the game.
    """
    dec = bytearray(crypt.decrypt(real_filelist()))
    at = crypt.HEADER_SIZE + 64
    dec[at] = (dec[at] + 7) & 0xFF

    stamped = crypt.apply_checksum(bytes(dec))
    round_tripped = crypt.decrypt(crypt.encrypt(stamped))

    assert crypt.looks_decrypted(round_tripped)
    assert round_tripped[at] == dec[at]
    assert round_tripped == stamped


def test_the_checksum_must_be_stamped_before_encrypting():
    """
    it covers plaintext. stamping after encryption writes it into ciphertext
    and corrupts a block, which is a subtle enough mistake to be worth a test.
    """
    dec = crypt.decrypt(real_filelist())
    right = crypt.decrypt(crypt.encrypt(crypt.apply_checksum(dec)))
    assert crypt.looks_decrypted(right)


def test_the_seed_sign_extension_matters():
    """
    the seed is built from four header bytes with C# int semantics, so a byte
    >= 0x80 at position 9 makes it negative and the widening sign-extends.
    masking to 32 bits produces a different key schedule and output that still
    looks like noise, which is a bug with no symptom.
    """
    raw = real_filelist()
    assert raw[9] >= 0x80, "this file is only a useful witness if the top bit is set"
    assert crypt.looks_decrypted(crypt.decrypt(raw))


def test_a_plaintext_xiii_style_filelist_is_left_alone(tmp_path):
    """XIII does not encrypt, so load() must not decrypt what is already clear"""
    payload = zlib.compress(b"0:10:10:a/b.imgb\x00" * 8, 9)
    body = bytearray(payload) + bytearray(16)
    body.extend(b"\x00" * ((-len(body)) % 8))
    out = bytearray(32)
    struct.pack_into(">I", out, 16, len(body) - 8)
    out.extend(body)
    struct.pack_into("<I", out, 32 + len(body) - 8, len(body) - 8)
    path = tmp_path / "filelistu.win32.bin"
    path.write_bytes(bytes(out))

    loaded = crypt.load(path)
    assert loaded.was_encrypted is False
    assert loaded.raw == bytes(out)


def test_chunks_re_deflate_byte_identically_at_level_nine():
    """
    the original was produced with zlib level 9, so an unmodified rebuild
    reproduces the file exactly. that is what lets a rewrite be verified
    against the original rather than merely hoped about.
    """
    dec = crypt.decrypt(real_filelist())
    body = dec[crypt.HEADER_SIZE:crypt.HEADER_SIZE + crypt.body_size(dec)]

    table_at, chunk_at, _ = struct.unpack_from("<III", body, 0)
    count = (chunk_at - table_at) // 12
    exact = 0
    for i in range(count):
        _, deflated_len, rel = struct.unpack_from("<III", body, table_at + i * 12)
        blob = body[chunk_at + rel: chunk_at + rel + deflated_len]
        plain = zlib.decompress(blob)
        rebuilt = zlib.compress(plain, 9)
        if rebuilt == blob:
            exact += 1
    assert exact == count, f"only {exact}/{count} chunks reproduced"


def test_the_chunk_table_describes_every_chunk():
    """12 byte records of (plain length, deflated length, relative offset)"""
    dec = crypt.decrypt(real_filelist())
    body = dec[crypt.HEADER_SIZE:crypt.HEADER_SIZE + crypt.body_size(dec)]
    table_at, chunk_at, entry_count = struct.unpack_from("<III", body, 0)
    count = (chunk_at - table_at) // 12

    running = 0
    total_entries = 0
    for i in range(count):
        plain_len, deflated_len, rel = struct.unpack_from("<III", body, table_at + i * 12)
        assert rel == running, "chunks are contiguous, so offsets accumulate exactly"
        blob = body[chunk_at + rel: chunk_at + rel + deflated_len]
        plain = zlib.decompress(blob)
        assert len(plain) == plain_len
        records = [p for p in plain.split(b"\x00") if p]
        # the final chunk closes with a literal "end" sentinel that is not an
        # entry. counting it would put every downstream index off by one.
        total_entries += len([r for r in records if r != b"end"])
        running += deflated_len

    assert total_entries == entry_count, "the header's entry count must be the real one"


def test_the_index_ends_with_an_end_sentinel():
    """not an entry, and a rebuild has to preserve it"""
    import zlib as _z
    dec = crypt.decrypt(real_filelist())
    body = dec[crypt.HEADER_SIZE:crypt.HEADER_SIZE + crypt.body_size(dec)]
    table_at, chunk_at, _ = struct.unpack_from("<III", body, 0)
    count = (chunk_at - table_at) // 12
    _, deflated_len, rel = struct.unpack_from("<III", body, table_at + (count - 1) * 12)
    last = _z.decompress(body[chunk_at + rel: chunk_at + rel + deflated_len])
    assert last.rstrip(b"\x00").endswith(b"end")


def _straight_parts(counter: int):
    ab, cd, ef = ((counter << n) & crypt.MASK64 for n in (10, 20, 30))
    e = (ef & 0xFFFFFFFF) | counter | (cd & 0xFFFFFFFF) | (ab & 0xFFFFFFFF)
    f = (ef >> 32) | (cd >> 32) | (ab >> 32)
    return (counter & 0xFFFF) & 0xF8, e, f


def _straight_rounds(value: int, table: bytes, offset: int, reverse: bool) -> int:
    for i in (range(7, -1, -1) if reverse else range(8)):
        value = (value + table[offset + i] - 120 if reverse
                 else ((value + 120) & 0xFF) - table[offset + i]) & 0xFF
    return value


def straight_decrypt(raw: bytes) -> bytes:
    """the per-byte port of WhiteCryptTool that decrypt() replaced, kept as the yardstick"""
    size, table, out = crypt.body_size(raw), crypt.generate_table(raw), bytearray(raw)
    for index in range(size // 8):
        at, counter = crypt.HEADER_SIZE + index * 8, index * 8
        current = raw[at:at + 8]
        offset, e, f = _straight_parts(counter)
        d = [_straight_rounds((((counter >> 3) ^ 69) & 0xFF) ^ current[0], table, offset, False)]
        d += [_straight_rounds(current[i - 1] ^ current[i], table, offset, False) for i in range(1, 8)]
        low, high = struct.unpack("<II", bytes(d))
        key_low, key_high = struct.unpack_from("<II", table, offset)
        borrow = 1 if low < key_low else 0
        low = ((low - key_low) ^ (e + 0xA1652347) ^ key_low) & 0xFFFFFFFF
        high = ((high - key_high - borrow) ^ (f + (1 if e > 0x5E9ADCB8 else 0)) ^ key_high) & 0xFFFFFFFF
        out[at:at + 8] = struct.pack("<II", high, low)
    return bytes(out)


def straight_encrypt(raw: bytes) -> bytes:
    size, table, out = crypt.body_size(raw), crypt.generate_table(raw), bytearray(raw)
    for index in range(size // 8):
        at, counter = crypt.HEADER_SIZE + index * 8, index * 8
        offset, e, f = _straight_parts(counter)
        high, low = struct.unpack_from("<II", raw, at)
        key_low, key_high = struct.unpack_from("<II", table, offset)
        low ^= key_low ^ ((e + 0xA1652347) & 0xFFFFFFFF)
        high ^= key_high ^ ((f + (1 if e > 0x5E9ADCB8 else 0)) & 0xFFFFFFFF)
        low = (low + key_low) & 0xFFFFFFFF
        high = (high + key_high + (1 if low < key_low else 0)) & 0xFFFFFFFF
        computed = struct.pack("<II", low, high)
        cipher = [(((counter >> 3) ^ 69) & 0xFF) ^ _straight_rounds(computed[0], table, offset, True)]
        for i in range(1, 8):
            cipher.append(cipher[-1] ^ _straight_rounds(computed[i], table, offset, True))
        out[at:at + 8] = bytes(cipher)
    return bytes(out)


def test_the_whole_body_passes_match_the_straight_port():
    """
    decrypt and encrypt run over the whole body at once, and a slip in that
    is noise that only the game would notice. Both seed signs, every table
    offset and block index wrap, and a tail past the body.
    """
    crypt._decrypted.cache_clear()
    rng = random.Random(13)
    for case in range(160):
        blocks = rng.choice([1, 2, 31, 32, 33, 255, 256, 257, rng.randrange(1, 700)])
        header = bytearray(rng.randbytes(crypt.HEADER_SIZE))
        struct.pack_into(">I", header, 16, blocks * 8 - 8)
        header[9] = header[9] | 0x80 if case % 2 else header[9] & 0x7F
        raw = bytes(header) + rng.randbytes(blocks * 8) + rng.randbytes(rng.randrange(0, 24))
        assert crypt.decrypt(raw) == straight_decrypt(raw), case
        assert crypt.encrypt(raw) == straight_encrypt(raw), case


def test_the_straight_port_agrees_on_the_real_index():
    """so the yardstick above is the algorithm and not a copy of a mistake"""
    raw = real_filelist()
    assert straight_decrypt(raw) == crypt.decrypt(raw)
    assert straight_encrypt(crypt.decrypt(raw)) == raw


def test_the_real_index_decrypts_in_a_game_switch_not_half_a_second():
    """
    the page asks for XIII-2's state on every game switch, which reads this
    index, and the straight port took 430 ms of it. Loose on purpose, a slow
    runner shouldn't fail it, a per-byte loop coming back should.
    """
    raw = real_filelist()
    took = []
    for _ in range(3):
        # the cache would make every run after the first free
        crypt._decrypted.cache_clear()
        started = time.perf_counter()
        crypt.encrypt(crypt.decrypt(raw))
        took.append(time.perf_counter() - started)
    assert min(took) < 0.2, f"{min(took) * 1000:.0f} ms to decrypt and encrypt the real index"


def test_a_changed_index_is_never_answered_from_the_cache():
    """keyed on the bytes, so one flipped byte is a different file"""
    raw = real_filelist()
    first = crypt.decrypt(raw)
    edited = bytearray(raw)
    edited[crypt.HEADER_SIZE + 100] ^= 0xFF
    assert crypt.decrypt(bytes(edited)) == straight_decrypt(bytes(edited)) != first
    assert crypt.decrypt(bytearray(raw)) == first, "a bytearray is the same file"
