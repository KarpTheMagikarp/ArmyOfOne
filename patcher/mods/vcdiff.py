"""
VCDIFF (RFC 3284) the way xdelta3 writes it, read a window at a time.

Older mods ship their archive changes as xdelta patches of the game's own
files: the Console Content Patch turns Steam's white_imgu into the 1.0
release's with a 4MB patch over a 1.5GB archive. Running that over the game
writes the whole archive again and throws away everything the drawer or the
HD Project appended, so nothing here ever produces a patched file whole.
`ops()` says which target bytes are new and which are copies of the source,
which is how a caller finds the handful of files a patch really changes, and
`decode()` builds one window, checked against the adler32 xdelta3 stores
beside it, so a source that isn't the file the patch was made from fails
loudly instead of turning into a costume made of somebody else's bytes.

Secondary compression (xdelta3's -S) isn't read. Every patch seen so far is
without it, and one with it says so rather than decoding to noise.
"""

import zlib
from dataclasses import dataclass

MAGIC = b"\xd6\xc3\xc4\x00"
VCD_DECOMPRESS, VCD_CODETABLE, VCD_APPHEADER = 1, 2, 4
VCD_SOURCE, VCD_TARGET, VCD_ADLER32 = 1, 2, 4
NOOP, ADD, RUN, COPY = 0, 1, 2, 3
NEAR, SAME = 4, 3


class PatchError(ValueError):
    pass


def _code_table() -> list:
    """RFC 3284's default table, built the way section 5.6 lays it out"""
    table = [((RUN, 0, 0), (NOOP, 0, 0))]
    table += [((ADD, size, 0), (NOOP, 0, 0)) for size in range(18)]
    for mode in range(9):
        table += [((COPY, size, mode), (NOOP, 0, 0)) for size in (0, *range(4, 19))]
    for mode in range(6):
        table += [((ADD, add, 0), (COPY, copy, mode)) for add in range(1, 5) for copy in range(4, 7)]
    for mode in range(6, 9):
        table += [((ADD, add, 0), (COPY, 4, mode)) for add in range(1, 5)]
    table += [((COPY, 4, mode), (ADD, 1, 0)) for mode in range(9)]
    assert len(table) == 256
    return table


TABLE = _code_table()


def _varint(buf, pos: int) -> tuple:
    value = 0
    while True:
        if pos >= len(buf):
            raise PatchError("a number runs off the end of the patch")
        byte = buf[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos


@dataclass(frozen=True)
class Window:
    start: int              # where its bytes go in the target
    length: int
    # (position, length) of the segment its copies read: in the source, or in
    # the target written so far for a VCD_TARGET window
    segment: tuple
    from_target: bool
    adler: int | None
    data: tuple             # (offset, length) of each section in the patch
    inst: tuple
    addr: tuple


class Patch:
    def __init__(self, raw: bytes):
        self.raw = bytes(raw)
        if self.raw[:4] != MAGIC:
            raise PatchError("not a VCDIFF patch")
        indicator, pos = self.raw[4], 5
        if indicator & VCD_DECOMPRESS:
            raise PatchError("its sections are compressed (xdelta3 -S), which this doesn't read")
        if indicator & VCD_CODETABLE:
            raise PatchError("it brings its own code table, which this doesn't read")
        self.app = b""
        if indicator & VCD_APPHEADER:
            size, pos = _varint(self.raw, pos)
            self.app = self.raw[pos:pos + size]
            pos += size
        self.windows, written = [], 0
        while pos < len(self.raw):
            window, pos = self._window(pos, written)
            self.windows.append(window)
            written += window.length

    @property
    def size(self) -> int:
        """the whole target's length"""
        return sum(w.length for w in self.windows)

    def _window(self, pos: int, start: int) -> tuple:
        raw = self.raw
        indicator = raw[pos]
        pos += 1
        if indicator & VCD_SOURCE and indicator & VCD_TARGET:
            raise PatchError("a window says it copies from both the source and the target")
        segment = (0, 0)
        if indicator & (VCD_SOURCE | VCD_TARGET):
            length, pos = _varint(raw, pos)
            where, pos = _varint(raw, pos)
            segment = (where, length)
        size, pos = _varint(raw, pos)
        end = pos + size
        length, pos = _varint(raw, pos)
        compressed = raw[pos]
        pos += 1
        if compressed:
            raise PatchError("a window's sections are compressed, which this doesn't read")
        data, pos = _varint(raw, pos)
        inst, pos = _varint(raw, pos)
        addr, pos = _varint(raw, pos)
        adler = None
        if indicator & VCD_ADLER32:
            adler = int.from_bytes(raw[pos:pos + 4], "big")
            pos += 4
        sections = ((pos, data), (pos + data, inst), (pos + data + inst, addr))
        if sections[2][0] + addr != end or end > len(raw):
            raise PatchError("a window's sections don't add up to its length")
        return Window(start, length, segment, bool(indicator & VCD_TARGET), adler,
                      *sections), end

    def window_at(self, offset: int) -> Window:
        for window in self.windows:
            if window.start <= offset < window.start + window.length:
                return window
        raise PatchError(f"no window holds byte {offset} of the target")

    def ops(self, window: Window) -> list:
        """
        (target offset, length, kind, where) for every instruction in the
        window. `where` is the source offset for a copy out of the source, the
        target offset for one out of the target, the data offset in the patch
        for an add, and the byte for a run
        """
        raw, out = self.raw, []
        data_at, data_end = window.data[0], window.data[0] + window.data[1]
        inst_at, inst_end = window.inst[0], window.inst[0] + window.inst[1]
        addr_at, addr_end = window.addr[0], window.addr[0] + window.addr[1]
        segment_pos, segment_len = window.segment
        near, slot, same = [0] * NEAR, 0, [0] * (SAME * 256)
        here = 0
        while inst_at < inst_end:
            pair = TABLE[raw[inst_at]]
            inst_at += 1
            for kind, size, mode in pair:
                if kind == NOOP:
                    continue
                if size == 0:
                    size, inst_at = _varint(raw, inst_at)
                    if inst_at > inst_end:
                        raise PatchError("an instruction runs past its section")
                if kind == ADD:
                    if data_at + size > data_end:
                        raise PatchError("an add runs past the data section")
                    out.append((window.start + here, size, "add", data_at))
                    data_at += size
                elif kind == RUN:
                    if data_at >= data_end:
                        raise PatchError("a run has no byte left to repeat")
                    out.append((window.start + here, size, "run", raw[data_at]))
                    data_at += 1
                else:
                    position = segment_len + here
                    if mode == 0:
                        address, addr_at = _varint(raw, addr_at)
                    elif mode == 1:
                        back, addr_at = _varint(raw, addr_at)
                        address = position - back
                    elif mode < 2 + NEAR:
                        step, addr_at = _varint(raw, addr_at)
                        address = near[mode - 2] + step
                    else:
                        address = same[(mode - 2 - NEAR) * 256 + raw[addr_at]]
                        addr_at += 1
                    if addr_at > addr_end:
                        raise PatchError("an address runs past its section")
                    near[slot], slot = address, (slot + 1) % NEAR
                    same[address % (SAME * 256)] = address
                    if address < 0 or address >= position:
                        raise PatchError("a copy reaches past what's been written")
                    if address < segment_len:
                        if address + size > segment_len:
                            raise PatchError("a copy runs off the end of its segment")
                        kind_said = "target" if window.from_target else "source"
                        out.append((window.start + here, size, kind_said, segment_pos + address))
                    else:
                        out.append((window.start + here, size, "self",
                                    window.start + address - segment_len))
                here += size
        if here != window.length:
            raise PatchError(f"a window builds {here} bytes and says it's {window.length}")
        return out

    def decode(self, window: Window, read_source, read_target=None) -> bytes:
        """
        one window's bytes. `read_source(position, size)` reads the file the
        patch was made from, and `read_target` the target before this window,
        for the rare VCD_TARGET one. Checked against the window's adler32
        """
        out = bytearray()
        for at, size, kind, where in self.ops(window):
            if kind == "add":
                out += self.raw[where:where + size]
            elif kind == "run":
                out += bytes([where]) * size
            elif kind in ("source", "target"):
                reader = read_source if kind == "source" else read_target
                if reader is None:
                    raise PatchError("a window copies from the target, which wasn't given")
                chunk = reader(where, size)
                if len(chunk) != size:
                    raise PatchError("the source ran out before the patch did")
                out += chunk
            else:
                start = where - window.start
                if start + size <= len(out):
                    out += out[start:start + size]
                else:
                    # an overlapping copy repeats what it's still writing
                    for i in range(size):
                        out.append(out[start + i])
        if window.adler is not None and zlib.adler32(bytes(out)) & 0xFFFFFFFF != window.adler:
            raise PatchError("a window doesn't check out, so the file it was patched against "
                             "isn't the one here")
        return bytes(out)

    def read(self, offset: int, size: int, read_source, read_target=None,
             cache: dict | None = None) -> bytes:
        """
        bytes [offset, offset + size) of the target, decoding only the windows
        they're in. `cache` keeps decoded windows for the next read
        """
        out, end = bytearray(), offset + size
        while offset < end:
            window = self.window_at(offset)
            built = cache.get(window.start) if cache is not None else None
            if built is None:
                built = self.decode(window, read_source, read_target)
                if cache is not None:
                    cache[window.start] = built
            take = min(end, window.start + window.length) - offset
            out += built[offset - window.start:offset - window.start + take]
            offset += take
        return bytes(out)
