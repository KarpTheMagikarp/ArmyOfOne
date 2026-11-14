"""
A flat little-endian address space for the parts of the exe ported line for
line: the mesh and skeleton editors walk their data through raw pointers,
add offsets to them, and keep pointers into the middle of a block in their
own structs. Giving them addresses here keeps that arithmetic exactly as the
exe does it, so a port reads like the code it came from and can be checked
against it a function at a time.

Every float stored is rounded to float32, which is all the exe's float math
needs to come out on the same bits.
"""

import bisect
import struct

_U8, _I8 = struct.Struct("<B"), struct.Struct("<b")
_U16, _I16 = struct.Struct("<H"), struct.Struct("<h")
_U32, _I32 = struct.Struct("<I"), struct.Struct("<i")
_U64, _I64 = struct.Struct("<Q"), struct.Struct("<q")
_F32, _F64 = struct.Struct("<f"), struct.Struct("<d")
_BEF = struct.Struct(">f")

# the exe's own globals live where the exe keeps them, so a ported line that
# reads DAT_0045a6c0 reads 0x45a6c0 here too. The heap starts past them, and
# address 0 stays unmapped, so a NULL is still a NULL
GLOBALS = 0x449000
BASE = 0x460000

_ZEROS = memoryview(bytes(1 << 20))


def _zeros(n: int):
    # bytes(n) costs more than copying n bytes does; a held block of zeros
    # sliced to size doesn't
    global _ZEROS
    if n > len(_ZEROS):
        _ZEROS = memoryview(bytes(max(n, len(_ZEROS) * 2)))
    return _ZEROS[:n]


class Mem:
    def __init__(self):
        self.buf = bytearray(BASE)
        self.sizes = {}
        # freed spans, start -> end, merged with their neighbours. A mesh edit
        # that resizes its block allocates a whole new copy every time, so
        # without reuse a few thousand of them run the machine out of memory
        self.holes = {}
        self.hole_starts = []

    def _inside(self, at: int, size: int) -> None:
        # a slice past the end would quietly resize the whole address space;
        # where the exe would fault, so does this
        if at <= 0 or at + size > len(self.buf):
            raise IndexError(f"{size} bytes at {at:#x} fall outside memory")

    def malloc(self, size: int, zero: bool = True) -> int:
        size = max(int(size), 1)
        span = size + (-size & 0xF)
        at = self._reuse(span)
        if at is None:
            at = len(self.buf)
            at += -at & 0xF
            self.buf.extend(_zeros(at + span - len(self.buf)))
        else:
            self.buf[at:at + span] = _zeros(span)
        self.sizes[at] = size
        return at

    def _reuse(self, span: int):
        for start in self.hole_starts:
            end = self.holes[start]
            if end - start >= span:
                self._forget(start)
                if end > start + span:
                    self._remember(start + span, end)
                return start
        return None

    def _remember(self, start: int, end: int) -> None:
        bisect.insort(self.hole_starts, start)
        self.holes[start] = end

    def _forget(self, start: int) -> None:
        del self.holes[start]
        self.hole_starts.pop(bisect.bisect_left(self.hole_starts, start))

    def free(self, at: int) -> None:
        size = self.sizes.pop(at, None)
        if size is None:
            return
        start, end = at, at + size + (-size & 0xF)
        k = bisect.bisect_left(self.hole_starts, start)
        if k and self.holes[self.hole_starts[k - 1]] == start:
            start = self.hole_starts[k - 1]
            self._forget(start)
        if end in self.holes:
            nxt = self.holes[end]
            self._forget(end)
            end = nxt
        self._remember(start, end)

    def put(self, data) -> int:
        at = self.malloc(len(data))
        self.buf[at:at + len(data)] = data
        return at

    def get(self, at: int, size: int) -> bytes:
        return bytes(self.buf[at:at + size])

    def write(self, at: int, data) -> None:
        self._inside(at, len(data))
        self.buf[at:at + len(data)] = data

    def memmove(self, dst: int, src: int, size: int) -> None:
        if size > 0:
            self._inside(dst, size)
            self._inside(src, size)
            if dst + size <= src or src + size <= dst:
                # apart, so a view saves the temporary copy; it can't stay
                # open, a bytearray with a view on it can't grow
                with memoryview(self.buf) as view:
                    self.buf[dst:dst + size] = view[src:src + size]
            else:
                self.buf[dst:dst + size] = self.buf[src:src + size]

    memcpy = memmove

    def memset(self, at: int, value: int, size: int) -> None:
        if size > 0:
            self._inside(at, size)
            self.buf[at:at + size] = bytes([value & 0xFF]) * size

    def u8(self, at): return self.buf[at]
    def i8(self, at): return _I8.unpack_from(self.buf, at)[0]
    def u16(self, at): return _U16.unpack_from(self.buf, at)[0]
    def i16(self, at): return _I16.unpack_from(self.buf, at)[0]
    def u32(self, at): return _U32.unpack_from(self.buf, at)[0]
    def i32(self, at): return _I32.unpack_from(self.buf, at)[0]
    def u64(self, at): return _U64.unpack_from(self.buf, at)[0]
    def i64(self, at): return _I64.unpack_from(self.buf, at)[0]
    def f32(self, at): return _F32.unpack_from(self.buf, at)[0]
    def f64(self, at): return _F64.unpack_from(self.buf, at)[0]

    def set_u8(self, at, v): self.buf[at] = v & 0xFF
    def set_i16(self, at, v): _U16.pack_into(self.buf, at, v & 0xFFFF)
    def set_i32(self, at, v): _U32.pack_into(self.buf, at, v & 0xFFFFFFFF)
    def set_u16(self, at, v): _U16.pack_into(self.buf, at, v & 0xFFFF)
    def set_u32(self, at, v): _U32.pack_into(self.buf, at, v & 0xFFFFFFFF)
    def set_u64(self, at, v): _U64.pack_into(self.buf, at, v & 0xFFFFFFFFFFFFFFFF)
    def set_f64(self, at, v): _F64.pack_into(self.buf, at, v)

    def set_f32(self, at, v):
        try:
            _F32.pack_into(self.buf, at, v)
        except OverflowError:
            _F32.pack_into(self.buf, at, float("inf") if v > 0 else float("-inf"))

    # big endian, the way the game's own structures are
    def be16(self, at): return int.from_bytes(self.buf[at:at + 2], "big")
    def be32(self, at): return int.from_bytes(self.buf[at:at + 4], "big")
    def be_f32(self, at): return _BEF.unpack_from(self.buf, at)[0]

    def set_be_f32(self, at, v):
        try:
            _BEF.pack_into(self.buf, at, v)
        except OverflowError:
            _BEF.pack_into(self.buf, at, float("inf") if v > 0 else float("-inf"))

    def set_be16(self, at, v): self.buf[at:at + 2] = (v & 0xFFFF).to_bytes(2, "big")
    def set_be32(self, at, v): self.buf[at:at + 4] = (v & 0xFFFFFFFF).to_bytes(4, "big")

    def strlen(self, at: int) -> int:
        return self.buf.index(0, at) - at

    def cstr(self, at: int) -> bytes:
        return bytes(self.buf[at:self.buf.index(0, at)])

    def strcpy(self, dst: int, src) -> None:
        data = (self.cstr(src) if isinstance(src, int) else bytes(src)) + b"\0"
        self._inside(dst, len(data))
        self.buf[dst:dst + len(data)] = data

    def strcat(self, dst: int, src) -> None:
        self.strcpy(dst + self.strlen(dst), src)
