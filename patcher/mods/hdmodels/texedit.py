"""
The texture commands a txb block's recipe line brings when its third field's
above 0 (FUN_004068b0 and the editors under it). They work on DXT1 pixels in
place, a block at a time: two RGB565 colours then sixteen 2 bit indices, so
format 0x18 is the only one touched.

    :e,ch,scale,add,lo,hi          a channel scaled and shifted, where it's in lo..hi
    :E,ch,scale,add                the same, everywhere
    :F,ch,scale,add,x0,x1,y0,y1    the same, in a window
    :A                             the alpha mask drawn: opaque green, holes black
    :C,from,to                     one channel copied onto another
    :c,from,to,x0,x1,y0,y1         the same, in a window
    :S,a,b                         two channels swapped
    :T,a,b,x0,x1,y0,y1             the same, in a window
    :D,file                        a DDS's pixels over the lot
    :I,trb,block                   another model's texture over the lot
    :J,trb,block,x0,x1,y0,y1,sx,sy blocks of it, into a window
    :R,x,y,mip,w0,w1,w2,w3         one block's four raw words
    :X                             done

Channels are 1 red, 2 green, 3 blue; the scale ones take 0 as red and 4 as
grey, the copies take 0 as the whole block. Windows are in pixels, X down the
rows and Y across, made blocks by C's divide; an end of -1 means the edge.
Arguments are read with plain atoi/atof, so a missing one is just 0.
"""

import struct

from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import atoi, bswap32, f32, i16, i32, u16, u32
from patcher.mods.hdmodels.texture import DXT1, total

NOT_DXT1 = "Edit Texture error.  Texture not type 24.\n"


def run(b, pixels: bytearray, header: bytes, size: int) -> None:
    """FUN_004068b0: commands off the recipe until :X, each one editing `pixels`"""
    if header[0x46] != DXT1:
        # and the commands stay on the recipe for whoever reads next
        if b.verbose:
            b.say(NOT_DXT1)
        return
    r = b.r

    def num() -> int:
        return atoi(r.str(optional=True))

    while True:
        head = r.command()
        if head is None:
            return
        if b.verbose:
            b.say(r.line + "\n")
        if head[:1] != ":":
            continue
        op = head[1:2] or "\0"
        if op == "X":
            return
        if op == "e":
            mode, scale, add, lo, hi = num(), r.float(optional=True), num(), num(), num()
            edit(b, pixels, header, u16(mode), scale, i16(add), 0, -1, 0, -1, i16(lo), i16(hi))
        elif op == "E":
            mode, scale, add = num(), r.float(optional=True), num()
            edit(b, pixels, header, u16(mode), scale, i16(add), 0, -1, 0, -1, -1, -1)
        elif op == "F":
            mode, scale = num(), r.float(optional=True)
            add, x0, x1, y0, y1 = (i16(num()) for _ in range(5))
            edit(b, pixels, header, u16(mode), scale, add, x0, x1, y0, y1, -1, -1)
        elif op == "A":
            alpha(b, pixels, header)
        elif op == "C":
            src, dst = num(), num()
            copy(b, pixels, header, u16(dst), pixels, header, u16(src))
        elif op == "c":
            src, dst, x0, x1, y0, y1 = (num() for _ in range(6))
            copy(b, pixels, header, u16(dst), pixels, header, u16(src),
                 i16(x0), i16(x1), i16(y0), i16(y1))
        elif op in "ST":
            one, two = u16(num()), u16(num())
            window = tuple(i16(num()) for _ in range(4)) if op == "T" else (0, -1, 0, -1)
            # both directions read a snapshot, or the second would see the first's work
            was = bytearray(pixels[:size])
            copy(b, pixels, header, one, was, header, two, *window)
            copy(b, pixels, header, two, was, header, one, *window)
        elif op == "D":
            dds(b, pixels, header, r.str(optional=True))
        elif op in "IJ":
            _borrow(b, pixels, header, op, num)
        elif op == "R":
            x, y, mip = i16(num()), i16(num()), i16(num())
            raw(b, pixels, header, x, y, mip, *(u16(num()) for _ in range(4)))
        else:
            b.say(f"Invalid Texture Command: {op}\n")


def _borrow(b, pixels: bytearray, header: bytes, op: str, num) -> None:
    """:I and :J, another model's texture read straight out of its .trb and .imgb"""
    t, block = i16(num()), i16(num())
    if not 0 <= t < len(b.trbs):
        raise ModelError(f"a texture command reads model {t}, which the recipe doesn't name")
    src = b.trbs[t]
    if not 0 <= block < src.count - 1:
        raise ModelError(f"a texture command reads block {block} of a model without one")
    length = u32(src.offset(block + 1) - src.offset(block))
    if length < 0x5C:
        raise ModelError(f"a texture command reads block {block} as a texture, too short for one")
    data = src.read(u32(src.start + src.offset(block)), 0x5C)
    theirs = bytes(data[:0x50])
    at = bswap32(struct.unpack_from("<I", data, 0x58)[0])
    need = u32(total(theirs))
    imgb = b.files.read(src.imgb)
    if imgb is None:
        b.say(f"Error opening imgb file {src.imgb}\n")
        return
    # fseek takes a 32 bit long: past 2GB it fails and the read starts at 0
    at = 0 if at >= 0x80000000 else at
    found = _Short(imgb[at:at + need], need)
    if op == "I":
        if need > len(pixels):
            raise ModelError("a texture command copies a bigger texture over a smaller one")
        pixels[:need] = found[0:need]
        return
    x0, x1, y0, y1, sx, sy = (i16(num()) for _ in range(6))
    copy(b, pixels, header, 0, found, theirs, 0, x0, x1, y0, y1, sx, sy)


class _Short:
    """
    a read that came up short of the buffer it went into: zeros past what was
    there, like texture.py has it, without building the whole thing up front
    """

    def __init__(self, data: bytes, size: int):
        self.data, self.size = data, size

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, span: slice) -> bytes:
        return self.data[span].ljust(min(span.stop, self.size) - span.start, b"\0")


def _dims(header) -> tuple:
    """(rows, cols) of blocks: the height's at 0x4c, the width's at 0x4a"""
    height, width = struct.unpack_from(">H", header, 0x4C)[0], struct.unpack_from(">H", header, 0x4A)[0]
    return height >> 2, width >> 2


def _quarter(v: int) -> int:
    # C's v / 4 on a short, which rounds toward 0
    return (v + 3) >> 2 if v < 0 else v >> 2


def _half(v: int) -> int:
    return -(-v >> 1) if v < 0 else v >> 1


def _shrink(n: int) -> int:
    return 1 if n < 2 else n >> 1


def _window(rows, cols, x0, x1, y0, y1):
    """the block window a command's pixel window makes, None where the exe refuses it"""
    end_r = rows if x1 == -1 else _quarter(x1)
    end_c = cols if y1 == -1 else _quarter(y1)
    start_r, start_c = _quarter(x0), _quarter(y0)
    if start_r <= rows and end_r <= rows and start_c <= cols and end_c <= cols \
            and x0 > -4 and end_r >= 0 and y0 > -4 and end_c >= 0:
        return start_r, end_r, start_c, end_c
    return None


def _block(buf, i: int) -> int:
    """the byte offset of block i, refusing what the exe would run off its buffer to reach"""
    if i < 0 or i * 8 + 8 > len(buf):
        raise ModelError("a texture command reaches past the texture's pixels")
    return i * 8


def _remap(byte: int, three: bool) -> int:
    """FUN_00405130: four indices renumbered for the endpoints being swapped"""
    out = 0
    for shift in range(0, 8, 2):
        f = byte & 3 << shift
        if not three and f >> shift >= 2:
            # 2 and 3 trade places as (5 << shift) - f, except the top pair,
            # where the exe has 0x100: its 2 stays 2 and its 3 comes out 0
            f = (0x100 if shift == 6 else 5 << shift) - f
        if f >> shift < 2:
            f = (1 << shift) - f
        out |= f
    return out


REMAP = ([_remap(n, False) for n in range(256)], [_remap(n, True) for n in range(256)])


def _settle(buf: bytearray, o: int, c0: int, c1: int, three: bool) -> None:
    """
    the colours written back, and if their order flipped the block's mode, the
    block put back the way it was: endpoints swapped (FUN_004051f0), or nudged
    apart when they came out equal
    """
    if (c0 <= c1) != three:
        if c0 == c1:
            if c1 & 0x1F == 0x1F:
                c1 = c1 & 0xFFE0 | 0x1E
                buf[o + 4:o + 8] = bytes(4)
            else:
                c0 = c0 & 0xFFE0 | (c0 + 1) & 0x1F
                buf[o + 4:o + 8] = b"\x55\x55\x55\x55"
        else:
            c0, c1 = c1, c0
            # the remap only knows the old mode, so a 3 colour block keeps its 2s and 3s
            table = REMAP[three]
            for k in range(o + 4, o + 8):
                buf[k] = table[buf[k]]
    struct.pack_into("<HH", buf, o, c0, c1)


def _truncate(value: float) -> int:
    """cvttss2si: toward 0, and the 0x80000000 it gives for anything it can't hold"""
    if value != value or not -2147483648.0 <= value < 2147483648.0:
        return -0x80000000
    return int(value)


def edit(b, buf: bytearray, header: bytes, mode: int, scale: float, add: int,
         x0: int, x1: int, y0: int, y1: int, lo: int, hi: int) -> None:
    """FUN_00405250: a channel times scale plus add, clamped, where it's in lo..hi"""
    if header[0x46] != DXT1:
        if b.verbose:
            b.say(NOT_DXT1)
        return
    rows, cols = _dims(header)
    window = _window(rows, cols, x0, x1, y0, y1)
    if window is None:
        b.say(f"Invalid texture edit (X,Y) parameters (h,w) ({rows},{cols})\n")
        return
    if mode > 4:
        # bails at the first block it reads, having changed nothing
        return
    top = 0x1F if mode in (0, 1, 3) else 0x3F

    def scaled(v):
        if (lo != -1 and v < lo) or (hi != -1 and v > hi):
            return None
        # single precision multiply, then the int truncated to a short
        n = i16(_truncate(f32(v * scale))) + add
        return min(max(n, 0), top)

    lut = [scaled(v) for v in range(0x40)]
    start_r, end_r, start_c, end_c = window
    base = 0
    for _ in range(header[0x47]):
        for r in range(start_r, end_r):
            for i in range(base + r * cols + start_c, base + r * cols + end_c):
                o = _block(buf, i)
                c0, c1 = struct.unpack_from("<HH", buf, o)
                three = c0 <= c1
                n0, n1 = c0, c1
                if mode < 2:
                    v = lut[c0 >> 11]
                    if v is not None:
                        n0 = c0 & 0x7FF | v << 11
                    v = lut[c1 >> 11]
                    if v is not None:
                        n1 = c1 & 0x7FF | v << 11
                elif mode == 2:
                    v = lut[c0 >> 5 & 0x3F]
                    if v is not None:
                        n0 = c0 & 0xF81F | v << 5
                    v = lut[c1 >> 5 & 0x3F]
                    if v is not None:
                        n1 = c1 & 0xF81F | v << 5
                elif mode == 3:
                    v = lut[c0 & 0x1F]
                    if v is not None:
                        n0 = c0 & 0xFFE0 | v
                    v = lut[c1 & 0x1F]
                    if v is not None:
                        n1 = c1 & 0xFFE0 | v
                else:
                    # grey: green plus twice red and blue, a third of it into all three
                    v = lut[((c0 >> 5 & 0x3F) + (c0 >> 11) * 2 + 2 + (c0 & 0x1F) * 2) // 3]
                    if v is not None:
                        n0 = (v >> 1) << 11 | v << 5 | v >> 1
                    v = lut[((c1 >> 5 & 0x3F) + (c1 >> 11) * 2 + 2 + (c1 & 0x1F) * 2) // 3]
                    if v is not None:
                        n1 = (v >> 1) << 11 | v << 5 | v >> 1
                if (n0, n1) != (c0, c1):
                    _settle(buf, o, n0, n1, three)
        base += max(rows * cols, 2)
        rows, cols = _shrink(rows), _shrink(cols)
        start_r, start_c = start_r >> 1, start_c >> 1
        end_r, end_c = end_r >> 1 or 1, end_c >> 1 or 1


def alpha(b, buf: bytearray, header: bytes) -> None:
    """FUN_00405bb0: every block made green where it's opaque and black where it's punched out"""
    if header[0x46] != DXT1:
        if b.verbose:
            b.say(NOT_DXT1)
        return
    mips = header[0x47]
    if not mips:
        return
    rows, cols = _dims(header)
    base = mip = 0
    if rows == 0:
        # a texture under 4 high skips its top mip, two blocks along
        base, rows, cols, mip = 2, 1, _shrink(cols), 1
        if mip == mips:
            return
    while True:
        for i in range(base, base + rows * cols):
            o = _block(buf, i)
            c0, c1, idx = struct.unpack_from("<HHI", buf, o)
            mask = 0
            if c0 <= c1:
                # 3 colour mode: index 3 is the transparent one
                for bit in range(0, 32, 2):
                    if idx >> bit & 3 == 3:
                        mask |= 1 << bit
            struct.pack_into("<HHI", buf, o, 0x7E0, 0, mask)
        base += max(rows * cols, 2)
        rows, cols = _shrink(rows), _shrink(cols)
        mip += 1
        if mip == mips:
            return


def copy(b, dst: bytearray, dst_head: bytes, dst_ch: int, src, src_head: bytes, src_ch: int,
         x0: int = 0, x1: int = -1, y0: int = 0, y1: int = -1, sx: int = 0, sy: int = 0,
         src_mip: int = 0) -> None:
    """
    FUN_00405d50: a channel of src's blocks into a channel of dst's (channel 0
    to 0 is the whole block), over a window, src read from (sx, sy) on
    """
    if dst_head[0x46] != DXT1 or src_head[0x46] != DXT1:
        if b.verbose:
            b.say(NOT_DXT1)
        return
    if dst_ch >= 4 or src_ch >= 4:
        if b.verbose:
            b.say("Copy Channel error.  Invalid channel number.\n")
        return
    rows, cols = _dims(dst_head)
    src_rows, src_cols = _dims(src_head)
    window = _window(rows, cols, x0, x1, y0, y1)
    if window is None:
        b.say("Invalid texture import (X,Y) parameters\n")
        return
    start_r, end_r, start_c, end_c = window
    src_base = 0
    for _ in range(src_mip):
        src_base += max(src_rows * src_cols, 2)
        src_rows, src_cols = _shrink(src_rows), _shrink(src_cols)
    off_r, off_c = _quarter(sx), _quarter(sy)
    # a channel 0 source copies whatever the last block gave, which is always 0
    a = z = 0
    base = 0
    for _ in range(dst_head[0x47]):
        for r in range(start_r, end_r):
            for c in range(start_c, end_c):
                o = _block(dst, base + r * cols + c)
                so = _block(src, src_base + (off_r + r) * src_cols + off_c + c)
                if dst_ch == 0:
                    dst[o:o + 8] = src[so:so + 8]
                    continue
                d0, d1 = struct.unpack_from("<HH", dst, o)
                s0, s1 = struct.unpack("<HH", src[so:so + 4])
                three = d0 <= d1
                if (s0 <= s1) != three:
                    a, z = z, a
                if src_ch == 1:
                    a, z = s0 >> 11, s1 >> 11
                elif src_ch == 2:
                    a, z = s0 >> 5 & 0x3F, s1 >> 5 & 0x3F
                elif src_ch == 3:
                    a, z = s0 & 0x1F, s1 & 0x1F
                # green's six bits lose or gain the bottom one going to or from five
                if dst_ch == 1:
                    v0, v1 = (a >> 1, z >> 1) if src_ch == 2 else (a, z)
                    d0 = d0 & 0x7FF | (v0 << 3 & 0xFF) << 8
                    d1 = d1 & 0x7FF | (v1 << 3 & 0xFF) << 8
                elif dst_ch == 2:
                    v0, v1 = (a, z) if src_ch == 2 else (a * 2 & 0x3E, z * 2 & 0x3E)
                    d0 = d0 & 0xF81F | v0 << 5 & 0xFFFF
                    d1 = d1 & 0xF81F | v1 << 5 & 0xFFFF
                else:
                    v0, v1 = (a >> 1, z >> 1) if src_ch == 2 else (a, z)
                    d0 = d0 & 0xFFE0 | v0 & 0x1F
                    d1 = d1 & 0xFFE0 | v1 & 0x1F
                _settle(dst, o, d0, d1, three)
        base += max(rows * cols, 2)
        rows, cols = _shrink(rows), _shrink(cols)
        src_base += max(src_rows * src_cols, 2)
        src_rows, src_cols = _shrink(src_rows), _shrink(src_cols)
        # unlike edit(), the window's ends can halve to nothing here
        end_r, end_c = end_r >> 1, end_c >> 1
        off_r, off_c = _half(off_r), _half(off_c)
        start_r, start_c = start_r >> 1, start_c >> 1


def dds(b, buf: bytearray, header: bytes, path: str) -> None:
    """FUN_00406600: a DDS's pixels read over the texture's, as many bytes as its own header says"""
    data = b.files.read(path) if path else None
    if data is None:
        b.say(f"Unable to open DDS file {path}\n")
        return
    head = data[:0x80].ljust(8, b"\0")
    size = struct.unpack_from("<i", head, 4)[0]
    if size != 0x7C:
        b.say(f"Invalid DDS Header Size ({size})\n")
        return
    got = data[0x80:0x80 + u32(total(header))]
    if len(got) > len(buf):
        raise ModelError(f"{path} has more pixels than the texture it's read over")
    buf[:len(got)] = got


def raw(b, buf: bytearray, header: bytes, x: int, y: int, mip: int,
        w0: int, w1: int, w2: int, w3: int) -> None:
    """FUN_00406720: one block's words written as they are, found by pixel and mip"""
    if header[0x47] <= mip:
        return
    height = struct.unpack_from(">H", header, 0x4C)[0]
    width = struct.unpack_from(">H", header, 0x4A)[0]
    if not (x >= 0 and y >= 0 and x < height and y < width):
        b.say("DXT Raw Data: Invalid Height/Width.\n")
        return
    base = 0
    if not ((width < 4 and height < 4) or mip < 1):
        h, w, n = height, width, 0
        while True:
            h, w = max(h, 4), max(w, 4)
            n += 1
            base = u32(base + (i32(h * w) >> 4))
            h, w = h >> 1, w >> 1
            if not ((h > 3 or w > 3) and n < mip):
                break
        width = w
    # x is a pixel row but gets multiplied as if it were a block row, so off the
    # top mip's multiples of 4 it lands somewhere else; kept, it's what the exe does
    o = _block(buf, base + ((y + (i32(x * width) >> 2)) >> 2))
    struct.pack_into("<4H", buf, o, w0, w1, w2, w3)
