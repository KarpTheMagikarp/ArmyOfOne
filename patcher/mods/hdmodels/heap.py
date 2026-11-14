"""
The exe's own little allocator on top of malloc: ten slots, each a pointer
and a size, so a copy can check it isn't writing past the end of a block.

It matters for output because the checks don't just complain. A copy into a
block the table doesn't know is dropped, and one that runs past the end of
its block is cut short, by a length worked out with a bug in it (the slot's
index plus slot 0's size, not the slot's own size). Mesh edits lean on these
copies for every resize, so they go through here exactly as the exe has them.
"""

SLOTS = 10


class Heap:
    def __init__(self, mem, say=None):
        self.mem = mem
        self.say = say or (lambda text: None)
        self.ptrs = [0] * SLOTS
        self.sizes = [0] * SLOTS

    def _slot(self, at: int) -> int:
        for n, ptr in enumerate(self.ptrs):
            if ptr == at:
                return n
        return -1

    def new(self, size: int) -> int:
        """FUN_0040a7a0: zeroed, in the first free slot; 0 when there's none"""
        if size < 1:
            self.say("Internal Error: Mem_New: Zero bytes requested.\n")
            return 0
        for n in range(SLOTS):
            if self.sizes[n] == 0:
                at = self.mem.malloc(size)
                self.ptrs[n], self.sizes[n] = at, size
                return at
        self.say("Internal Error: Out of memory blocks.\n")
        return 0

    def delete(self, at: int) -> None:
        """FUN_0040a890"""
        if not at:
            self.say("Internal Error: Mem_Del: Null block passed.\n")
            return
        n = self._slot(at)
        if n < 0:
            self.say("Internal Error: Mem_Del: Memory block not found.\n")
        else:
            self.ptrs[n], self.sizes[n] = 0, 0
        self.mem.free(at)

    def fill(self, at: int, value: int, offset: int, length: int) -> None:
        """FUN_0040a970: memset, held inside the block"""
        if not at:
            self.say("Internal Error: Mem_Set: Null block passed.\n")
        elif offset < 0:
            self.say("Internal Error: Mem_Set: Offset < 0\n")
        elif length < 0:
            self.say("Internal Error: Mem_Set: Length < 0\n")
        else:
            n = self._slot(at)
            if n < 0:
                self.say("Internal Error: Mem_Set: Memory block not found.\n")
                return
            if self.sizes[n] < offset + length:
                self.say("Internal Error: Mem_Set: Buffer Overrun.\n")
                length = self.sizes[n] - offset
            self.mem.memset(at + offset, value, length)

    def copy(self, check: int, dst: int, dst_at: int, src: int, src_at: int, length: int) -> None:
        """FUN_0040aa60: memmove into a block the table knows, cut short at its end"""
        if not dst:
            return self.say("Internal Error: Mem_Copy: Null destination block passed.\n")
        if not src:
            return self.say("Internal Error: Mem_Copy: Null source block passed.\n")
        if dst_at < 0:
            return self.say("Internal Error: Mem_Copy: Destination offset < 0\n")
        if src_at < 0:
            return self.say("Internal Error: Mem_Copy: Source offset < 0\n")
        if length < 0:
            return self.say("Internal Error: Mem_Copy: Length < 0\n")
        if length == 0:
            return None
        n = self._slot(dst)
        if n < 0:
            return self.say("Internal Error: Mem_Copy: Destination memory block not found.\n")
        count = length
        moved = True
        if _i32(length + dst_at) > self.sizes[n]:
            self.say("Internal Error: Mem_Copy: Buffer Overrun.\n")
            count = _i32(n + self.sizes[0] - dst_at)
            moved = self.sizes[n] >= dst_at and count > 0
        if moved:
            self.mem.memmove(dst + dst_at, src + src_at, count)
        if check & 1:
            for k in range(SLOTS):
                if self.ptrs[k] == src and self.sizes[k] < _i32(src_at + length):
                    return self.say("Internal Error: Mem_Copy: Src Overrun.\n")
        return None


def _i32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value
