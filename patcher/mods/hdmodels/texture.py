"""
A model's textures: the txb blocks, their pixels in the .imgb, and the
replacements a recipe can bring.

A txb block is 0x50 bytes of header, big endian like everything Square Enix
wrote for the PS3 first, then its mip table: a pair of u32 (offset into the
.imgb, size) for each mip level, after two words the game keeps.

    0x10  u32   the texture's size in bytes, all mips
    0x46  u8    format: 3 RGBA, 0x18 DXT1, 0x19 DXT3, 0x1A DXT5
    0x47  u8    mip levels
    0x4a  u16   width
    0x4c  u16   height

Copying a model, each texture's mips are read out of the source .imgb and
written one after another into the new one, and its table rewritten to point
there. Unless the mod has a replacement for it, looked for by the name the
last block's record gives the texture:

    textures\\<name>#.dds                  a DDS of the same compression
    textures\\pc\\<name>_<model>.bin       HD Models Plus's own, scrambled
    textures\\pc\\<name>.bin               against the name they're under
"""

import functools
import struct

from patcher.mods.hdmodels.build import LARGEST, ModelError
from patcher.mods.hdmodels.cnum import bswap16, bswap32, i16, u32

DXT1, DXT3, DXT5, RGBA = 0x18, 0x19, 0x1A, 3
FOURCC = {DXT1: b"DXT1", DXT5: b"DXT5"}


def mip_sizes(fmt: int, width: int, height: int, mips: int) -> list:
    """FUN_00406520's walk: each level's bytes, halving as it goes"""
    out = []
    w, h = width, height
    for _ in range(mips):
        if fmt == RGBA:
            size = 0 if (w | h) == 0 else max(w, 1) * max(h, 1) * 4
        else:
            aw, ah = w + (-w & 3), h + (-h & 3)
            if (aw | ah) == 0:
                size = 0
            else:
                size = max(aw, 4) * max(ah, 4)
                if fmt == DXT1:
                    size >>= 1
        out.append(size)
        w >>= 1
        h >>= 1
    return out


def total(header: bytes) -> int:
    fmt, mips = header[0x46], header[0x47]
    width = bswap16(struct.unpack_from("<H", header, 0x4C)[0])
    height = bswap16(struct.unpack_from("<H", header, 0x4A)[0])
    return sum(max(s, 0x10) if s else 0 for s in mip_sizes(fmt, width, height, mips))


def txb(b, trb: int, block: int, data: bytearray, length: int, param: int):
    """FUN_00408e90: one texture copied across, its pixels into the new .imgb"""
    header = bytearray(data[:0x50].ljust(0x50, b"\0"))
    path, source = _replacement(b, trb, block, header)
    if path is None:
        # its own pixels, where its mip table says they start
        words = (length - 0x50) >> 2
        table = list(struct.unpack_from(f"<{words}I", data, 0x50)) if words > 0 else []
        start = bswap32(table[2]) if len(table) > 2 else 0
        out_block = data
        reading = b.trbs[trb].imgb
    else:
        levels = header[0x47] + 1
        levels += levels & 1
        words = levels * 2
        length = levels * 8 + 0x50
        out_block = bytearray(length)
        out_block[:0x50] = header
        table = [0] * words
        table[0] = bswap32(0x18)
        start = 0x80
        reading = path
    fmt, mips = header[0x46], header[0x47]
    height = bswap16(struct.unpack_from("<H", header, 0x4C)[0])
    width = bswap16(struct.unpack_from("<H", header, 0x4A)[0])
    count = 0
    slot = 2
    h, w = height, width
    for _ in range(mips):
        if fmt == RGBA:
            size = 0 if (w | h) == 0 else max(w, 1) * max(h, 1) * 4
        else:
            ah, aw = h + (-h & 3), w + (-w & 3)
            if (ah | aw) == 0:
                size = 0
            else:
                size = max(aw, 4) * max(ah, 4)
                if fmt == DXT1:
                    size >>= 1
        if slot < (words & 0xFFFF):
            table[slot] = bswap32(b.imgb_at)
            table[slot + 1] = bswap32(size)
        if size:
            step = max(size, 0x10)
            b.imgb_at = u32(b.imgb_at + step)
            count += step
        h >>= 1
        w >>= 1
        slot += 2
    if count > LARGEST:
        raise ModelError("one of its textures is too big to be real")
    if count:
        if source is None:
            raw = b.files.read(reading)
            if raw is None:
                b.say(f"Error opening imgb file {reading}\n")
                pixels = None
            else:
                pixels = bytearray(raw[start:start + count].ljust(count, b"\0"))
        else:
            # the header's sizes, whatever the .bin held
            pixels = bytearray(source[:count].ljust(count, b"\0"))
        if pixels is not None:
            if param > 0:
                from patcher.mods.hdmodels import texedit
                texedit.run(b, pixels, bytes(header), count)
            b.imgb.write(pixels)
    elif source is not None and param > 0:
        from patcher.mods.hdmodels import texedit
        texedit.run(b, source, bytes(header), 0)
    words_out = bytearray(b"".join(struct.pack("<I", w) for w in table))
    out_block[0x50:0x50 + len(words_out)] = words_out
    return out_block, length


def _replacement(b, trb: int, block: int, header: bytearray):
    """
    FUN_004086d0: a replacement for this texture, as (path, pixels) where
    pixels are already in hand for a .bin, (path, None) for a .dds read later,
    (None, None) for none. The header's taken on to the replacement's size
    """
    src = b.trbs[trb]
    name = bytes(src.record(block))
    name = name.split(b"\0", 1)[0]
    path = b"textures\\" + name
    if b.verbose:
        b.say(f"txb: {path.decode('latin-1')}\n")
    dds = (path + b"#.dds").decode("latin-1")
    raw = b.files.read(dds)
    pixels = None
    if raw is not None:
        info = raw[:0x80].ljust(0x80, b"\0")
        if b.verbose:
            b.say(f"{dds} replacement\n")
        if struct.unpack_from("<I", info, 4)[0] != 0x7C:
            b.say(f"Invalid DDS Header Size ({struct.unpack_from('<I', info, 4)[0]}) for {dds}\n")
            return None, None
        found = dds
    else:
        ident = src.ident[:4]
        key = name + b"_" + ident
        found = (b"textures\\" + b.texture_dir + b"\\" + key + b".bin").decode("latin-1")
        raw = b.files.read(found)
        if raw is None:
            key = name
            found = (b"textures\\" + b.texture_dir + b"\\" + name + b".bin").decode("latin-1")
            raw = b.files.read(found)
            if raw is None:
                if b.verbose:
                    b.say("file not found.\n")
                return None, None
        if b.verbose:
            b.say(f"{found} replacement\n")
        info, pixels = _unscramble(key, bytes(raw))
    fmt = header[0x46]
    if fmt not in FOURCC or info[0x54:0x58] != FOURCC[fmt]:
        b.say(f"Internal texture compression format not consistent with {found}\n")
        return None, None
    mips = struct.unpack_from("<I", info, 0x1C)[0] or 1
    height = struct.unpack_from("<H", info, 0x0C)[0]
    width = struct.unpack_from("<H", info, 0x10)[0]
    struct.pack_into("<H", header, 0x4C, bswap16(height))
    struct.pack_into("<H", header, 0x4A, bswap16(width))
    header[0x47] = mips & 0xFF
    struct.pack_into("<I", header, 0x10, u32(total(header)))
    b.textures_replaced += 1
    return found, pixels


class _Rng:
    """FUN_00408280: the generator HD Models Plus scrambles its .bin textures with"""

    def __init__(self, a, b_, c, d):
        self.carry = 0
        self.a, self.b, self.c, self.d = a, b_, c, d

    def next(self) -> int:
        old = self.c
        x = (self.b << 5 ^ self.b) & 0xFFFFFFFF
        x ^= _sar(x, 7)
        self.b = (x << 22 ^ x) & 0xFFFFFFFF
        self.c = self.d
        mix = (old + self.d + self.carry) & 0xFFFFFFFF
        self.d = mix & 0x7FFFFFFF
        self.carry = mix >> 31
        self.a = (self.a + 0x542023AB) & 0xFFFFFFFF
        return (self.d + self.b + self.a) & 0xFFFFFFFF


def _sar(value: int, n: int) -> int:
    value &= 0xFFFFFFFF
    if value & 0x80000000:
        value -= 1 << 32
    return (value >> n) & 0xFFFFFFFF


def _seed(key: bytes, skip: int) -> _Rng:
    """FUN_004084f0: the key cased alternately, folded into four words"""
    cased = bytearray(key)
    for n in range(len(cased)):
        c = chr(cased[n])
        cased[n] = ord(c.upper() if n % 2 == 0 else c.lower()) if c.isascii() else cased[n]
    words = [0x75BCD15, 0xDFB38D3, 0x149AA440, 0x1B3A0C83]
    carry = 0x75BCD15
    for n in range(16):
        c = cased[n % len(cased)]
        if c >= 0x80:
            c -= 0x100
        shift = ((n & 0xFFFF) // 3 % 3) * 7
        words[n & 3] = ((c << shift) + carry) & 0xFFFFFFFF
        if n == 15:
            break
        carry = words[(n + 1) & 3]
    rng = _Rng(words[0], (words[1] + 1) & 0xFFFFFFFF, words[2], words[3])
    first = rng.next()
    turns = i16((first & 0xF) + 2 + skip)
    for _ in range(turns & 0xFFFF):
        rng.next()
    return rng


# HD Models Plus's runs share their textures, and unscrambling one is a
# second or two of pure Python, so the last few are kept
@functools.lru_cache(maxsize=8)
def _unscramble(key: bytes, raw: bytes):
    """a .bin's 8 byte header made a DDS header, and its pixels unscrambled"""
    rng = _seed(key, 3)
    head = raw[:8].ljust(8, b"\0")
    info = bytearray(0x80)
    info[0:4] = b"DDS "
    struct.pack_into("<I", info, 4, 0x7C)
    struct.pack_into("<I", info, 8, 0xA1007)
    struct.pack_into("<I", info, 0x4C, 0x20)
    struct.pack_into("<I", info, 0x50, 4)
    struct.pack_into("<Q", info, 0x68, 0x40100800000000)
    p = int.from_bytes(head, "little")
    r1 = rng.next()
    r2 = rng.next()
    kind = ((p >> 8) - r2) & 0xFF
    r3 = rng.next()
    r4 = rng.next()
    r5 = rng.next()
    r6 = rng.next()
    r7 = rng.next()
    r8 = rng.next()
    check1 = (p >> 24) & 0xFF
    check2 = (p >> 48) & 0xFF
    if check1 != r4 & 0xFF or check2 != r7 & 0xFF:
        pass
    high = (p >> 32) & 0xFFFFFFFF
    height = (((high >> 8) - r6) & 0xFF) + ((((p >> 16) & 0xFFFFFFFF) - r3) & 0xFF) * 0x100
    width = (((p & 0xFFFFFFFF) - r1) & 0xFF) + (((p >> 56) - r8) & 0xFF) * 0x100
    struct.pack_into("<I", info, 0x10, u32(height))
    struct.pack_into("<I", info, 0x1C, (high - r5) & 0xFF)
    struct.pack_into("<I", info, 0x0C, u32(width))
    area = u32(height * width)
    if kind == 0x18:
        info[0x54:0x58] = b"DXT1"
        struct.pack_into("<I", info, 0x14, u32(area >> 1 if area < 0x80000000 else (area - (1 << 32)) >> 1))
    elif kind == 0x19:
        info[0x54:0x58] = b"DXT3"
        struct.pack_into("<I", info, 0x14, area)
    else:
        info[0x54:0x58] = b"DXT5"
        struct.pack_into("<I", info, 0x14, area)
    size = _bin_size(info)
    if size > LARGEST:
        raise ModelError("one of its replacement textures is too big to be real")
    pixels = bytearray(raw[8:8 + size].ljust(size, b"\0"))
    _descramble(pixels, rng)
    return bytes(info), bytes(pixels)


def _bin_size(info: bytes) -> int:
    """FUN_00408640 on the DDS header the .bin's was made into"""
    w = struct.unpack_from("<I", info, 0x0C)[0]
    h = struct.unpack_from("<I", info, 0x10)[0]
    mips = struct.unpack_from("<i", info, 0x1C)[0]
    dxt1 = info[0x54:0x58] == b"DXT1"
    total_ = 0
    n = 0
    while n < mips:
        n += 1
        if (h | w) != 0:
            size = max(w, 4) * max(h, 4)
            if dxt1:
                size >>= 1
            if size:
                total_ += max(size, 0x10)
        w >>= 1
        h >>= 1
        if (n & 0xFF) >= mips:
            break
    return u32(total_)


def _descramble(buf: bytearray, rng: _Rng) -> None:
    """FUN_004082f0: each byte less the next number, and every 5323rd a reshuffle"""
    # _Rng.next inlined: this runs once a byte over megabytes of texture
    a, b, c, d, carry = rng.a, rng.b, rng.c, rng.d, rng.carry
    every = 3
    for n in range(len(buf)):
        x = (b << 5 ^ b) & 0xFFFFFFFF
        x ^= (x >> 7) | (0xFE000000 if x & 0x80000000 else 0)
        b = (x << 22 ^ x) & 0xFFFFFFFF
        mix = (c + d + carry) & 0xFFFFFFFF
        c, d, carry = d, mix & 0x7FFFFFFF, mix >> 31
        a = (a + 0x542023AB) & 0xFFFFFFFF
        buf[n] = (buf[n] - d - b - a) & 0xFF
        if n % 0x14CB == 0:
            rng.a, rng.b, rng.c, rng.d, rng.carry = (a + 0xB) & 0xFFFFFFFF, (b + 0x1F) & 0xFFFFFFFF, \
                (c + 0x15B) & 0xFFFFFFFF, d, carry
            hi = rng.next()
            for _ in range(every & 0xFFFF):
                rng.next()
            lo = rng.next()
            every = (((lo >> 17) + (hi >> 3)) & 7) + 3
            a, b, c, d, carry = rng.a, rng.b, rng.c, rng.d, rng.carry
    rng.a, rng.b, rng.c, rng.d, rng.carry = a, b, c, d, carry