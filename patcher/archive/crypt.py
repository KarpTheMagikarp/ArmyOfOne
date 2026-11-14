"""
Filelist decryption for XIII-2 and Lightning Returns (XIII's are in the clear).

A port of Surihix's WhiteCryptTool (GPL-3.0), which is why this project is
GPL-3.0. See LICENSING.md.

    [0..32)                 header, never encrypted
    [32..32+body_size)      encrypted body, 8-byte blocks
    [32+body_size..end)     trailing data, never encrypted

body_size is a big-endian uint32 at offset 16, plus 8. The key schedule's seed
is four scattered header bytes, so no external key. A 264-byte table comes
from the seed, each block XOR-chains against its predecessor byte, goes
through a substitution, then gets offset by a value from the block index. It's
there to stop casual editing, not to be strong.
"""

import functools
import struct
from dataclasses import dataclass
from pathlib import Path

HEADER_SIZE = 32
BLOCK = 8
TABLE_SIZE = 264
MASK64 = (1 << 64) - 1
# a block keys off table[(index % 32) * 8], so the key comes round every 256 bytes
CYCLE = 32 * BLOCK
# what a block's first byte chains against, having no byte before it
_LEADS = bytes((index ^ 69) & 0xFF for index in range(256))


class DecryptError(Exception):
    pass


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _rotl32(value: int, count: int) -> int:
    value &= 0xFFFFFFFF
    return ((value << count) | (value >> (32 - count))) & 0xFFFFFFFF


def body_size(raw: bytes) -> int:
    """
    length of the encrypted region, from the big-endian uint32 at offset 16.

    the +8 and the multiple-of-8 requirement are the format's, not ours: the
    cipher works in 8 byte blocks and a body that is not a whole number of
    them means the header has been misread.
    """
    if len(raw) < HEADER_SIZE:
        raise DecryptError(f"file is {len(raw)} bytes, too short to be a filelist")
    size = struct.unpack_from(">I", raw, 16)[0] + 8
    if size % BLOCK:
        raise DecryptError(f"body size {size} is not a multiple of {BLOCK}")
    if HEADER_SIZE + size > len(raw):
        raise DecryptError(
            f"header claims a {size} byte body but only {len(raw) - HEADER_SIZE} bytes follow"
        )
    return size


def generate_table(raw: bytes) -> bytes:
    """
    the 264-byte keystream table, derived entirely from the header.

    the seed is four bytes plucked from scattered header positions (9, 12, 2,
    0). that looks arbitrary because it is: it is obfuscation, and the only
    thing that matters is reproducing it exactly.
    """
    header = raw[:16]
    seed = (header[9] << 24) | (header[12] << 16) | (header[2] << 8) | header[0]

    # C# evaluates those byte shifts as int, so a header byte >= 0x80 at
    # position 9 makes the result NEGATIVE, and the (ulong) cast then
    # sign-extends it: the top four bytes become FF rather than 00. masking to
    # 32 bits here produces a completely different key schedule and output
    # that is still indistinguishable from noise, which is a bug with no
    # symptom other than "it does not work".
    if seed & 0x80000000:
        seed -= 1 << 32
    seed_bytes = struct.pack("<q", seed)

    reversed_seed = seed_bytes[::-1]
    half_a, half_b = struct.unpack("<II", reversed_seed)

    half_a = _rotl32(half_a, 8)
    half_b = _rotl32(half_b, 16)

    block = bytearray(struct.pack("<I", half_b) + struct.pack("<I", half_a))
    block[0] = (block[0] + 0x45) & 0xFF

    for i in range(1, 8):
        tmp = block[i] + 0xD4 + block[i - 1]
        tmp ^= (block[i - 1] << 2)
        tmp ^= 0x45
        block[i] = tmp & 0xFF

    table = bytearray(TABLE_SIZE)
    table[0:8] = block

    previous = struct.unpack("<Q", bytes(block))[0]
    at = 8
    for _ in range(1, 0x21):
        low = previous & 0xFFFFFFFF
        high = (previous >> 32) & 0xFFFFFFFF

        a = (5 * previous) & 0xFFFFFFFFFFFFFFFF
        a ^= (high << 32) & 0xFFFFFFFFFFFFFFFF

        tmp_low = _u32(low ^ a)
        tmp_high = _u32(a >> 32)

        a = (low | (a & 0xFFFFFFFF00000000)) & 0xFFFFFFFFFFFFFFFF

        out_low = _u32(a ^ tmp_low)
        out_high = _u32(tmp_high ^ high)

        block = bytearray(struct.pack("<I", out_low) + struct.pack("<I", out_high))
        table[at:at + 8] = block
        previous = struct.unpack("<Q", bytes(block))[0]
        at += 8

    return bytes(table)


def _shifts(table: bytes) -> list:
    """
    the byte substitution at each of the 32 table offsets. The original runs
    eight rounds of it per byte, through a 256 entry table that is just +120,
    minus one table byte per round, all mod 256. Adds all the way down, so the
    eight rounds are one add.
    """
    return [(8 * 120 - sum(table[at:at + BLOCK])) & 0xFF for at in range(0, CYCLE, BLOCK)]


def _counter_key(counter: int) -> int:
    """the per-block value the original builds out of 32-bit halves and a carry"""
    return ((counter | counter << 10 | counter << 20 | counter << 30) + 0xA1652347) & MASK64


def _cycled(pattern: bytes, length: int) -> bytes:
    whole, part = divmod(length, len(pattern))
    return pattern * whole + pattern[:part]


def _xor(a, b) -> bytes:
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(len(a), "little")


def _substitute(data: bytes, table: bytes, sign: int) -> bytearray:
    """every byte shifted by its block's offset, one translate per offset and byte position"""
    out = bytearray(len(data))
    for slot, shift in enumerate(_shifts(table)):
        lut = bytes((value + sign * shift) & 0xFF for value in range(256))
        for byte in range(BLOCK):
            at = slot * BLOCK + byte
            out[at::CYCLE] = data[at::CYCLE].translate(lut)
    return out


def decrypt(raw: bytes) -> bytes:
    """
    returns the whole file with its body decrypted, header and tail intact.

    operates on bytes rather than streaming because a filelist is a few
    hundred KB even for XIII-2. white_img is the multi-gigabyte one, and it is
    not encrypted.

    The byte steps run over the whole body at once and only the 64-bit math is
    a loop. The page reads XIII-2's index on every game switch and an HD
    install decrypts it a couple of dozen times, and the straight port spent
    430 ms on the real one doing eight rounds per byte in Python.
    """
    return _decrypted(bytes(raw))


# keyed on the bytes, so a changed file is just a miss. The same few indexes
# get asked about all session and only change when something is installed
@functools.lru_cache(maxsize=8)
def _decrypted(raw: bytes) -> bytes:
    size = body_size(raw)
    table = generate_table(raw)
    body = raw[HEADER_SIZE:HEADER_SIZE + size]

    # each byte is chained against the previous CIPHERTEXT byte, so a
    # block decrypts independently of the plaintext around it
    before = bytearray(1) + body[:-1]
    before[::BLOCK] = _cycled(_LEADS, size // BLOCK)
    mixed = _substitute(_xor(body, before), table, 1)

    keys = struct.unpack_from(f"<{CYCLE // BLOCK}Q", table)
    plain = []
    for index, (lane,) in enumerate(struct.iter_unpack("<Q", mixed)):
        key = keys[index % len(keys)]
        lane = ((lane - key) & MASK64) ^ _counter_key(index * BLOCK) ^ key
        # the halves are written swapped relative to how they were read
        plain.append((lane & 0xFFFFFFFF) << 32 | lane >> 32)
    return raw[:HEADER_SIZE] + struct.pack(f"<{len(plain)}Q", *plain) + raw[HEADER_SIZE + size:]


def checksum(data: bytes) -> int:
    """
    the filelist's own integrity value: every fourth byte of the body, summed
    as a wrapping uint32.

    sampled rather than complete, which is cheap and weak, but it is the
    game's rule and not ours to improve. it lives in the last four bytes of
    the body, immediately after the body length.
    """
    size = body_size(data)
    return sum(data[HEADER_SIZE:HEADER_SIZE + size - 8:4]) & 0xFFFFFFFF


def apply_checksum(data: bytes) -> bytes:
    """
    stamps a freshly computed checksum into a decrypted filelist.

    has to happen BEFORE encryption, because the value covers plaintext. get
    that order wrong and the game rejects an index that is otherwise perfect.
    """
    size = body_size(data)
    out = bytearray(data)
    struct.pack_into("<I", out, HEADER_SIZE + size - 4, checksum(data))
    return bytes(out)


def encrypt(raw: bytes) -> bytes:
    """
    the inverse of decrypt(), block for block.

    the caller is expected to have run apply_checksum() first. this does not
    do it silently, because a function that rewrites two different things
    depending on its mood is how a subtle corruption gets shipped.
    """
    raw = bytes(raw)
    size = body_size(raw)
    table = generate_table(raw)

    keys = struct.unpack_from(f"<{CYCLE // BLOCK}Q", table)
    lanes = []
    for index, (lane,) in enumerate(struct.iter_unpack("<Q", raw[HEADER_SIZE:HEADER_SIZE + size])):
        key = keys[index % len(keys)]
        lane = (lane & 0xFFFFFFFF) << 32 | lane >> 32
        lanes.append(((lane ^ key ^ _counter_key(index * BLOCK)) + key) & MASK64)
    mixed = _substitute(struct.pack(f"<{len(lanes)}Q", *lanes), table, -1)

    # decrypt xors each byte with the ciphertext byte before it, so going the
    # other way a block is a running xor that starts from its lead byte
    body = bytearray(size)
    chain = _cycled(_LEADS, size // BLOCK)
    for byte in range(BLOCK):
        chain = _xor(chain, mixed[byte::BLOCK])
        body[byte::BLOCK] = chain
    return raw[:HEADER_SIZE] + bytes(body) + raw[HEADER_SIZE + size:]


def looks_decrypted(data: bytes) -> bool:
    """
    the format's own self-check: the last four bytes of the body hold the
    body's length. a wrong key produces noise there, so this is a reliable
    oracle with no need to parse anything downstream.
    """
    try:
        size = body_size(data) - 8
    except DecryptError:
        return False
    at = HEADER_SIZE + size
    if at + 4 > len(data):
        return False
    return struct.unpack_from("<I", data, at)[0] == size


def is_encrypted(raw: bytes) -> bool:
    """XIII filelists are plaintext, so decryption must be conditional"""
    return not looks_decrypted(raw)


@dataclass
class Filelist:
    raw: bytes
    was_encrypted: bool

    @property
    def body(self) -> bytes:
        return self.raw[HEADER_SIZE:HEADER_SIZE + body_size(self.raw)]


def load(path: Path) -> Filelist:
    """reads a filelist and decrypts it only if it needs it"""
    raw = Path(path).read_bytes()
    if not is_encrypted(raw):
        return Filelist(raw=raw, was_encrypted=False)

    decrypted = decrypt(raw)
    if not looks_decrypted(decrypted):
        raise DecryptError(
            "decryption did not produce a valid filelist. the header may be from a "
            "different game revision than this implementation was written against"
        )
    return Filelist(raw=decrypted, was_encrypted=True)
