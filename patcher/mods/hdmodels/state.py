"""
The exe's globals, for the editors ported line for line onto cmem.Mem.

build.py keeps main()'s state as ordinary Python. The skeleton and mesh
editors were ported closer to the metal, reading and writing the exe's
globals by address, so before one runs main()'s state is pushed into the
address space where they expect it, and anything they changed (the output
table, the header) is pulled back after. The addresses are the exe's own.
"""

import struct

from patcher.mods.hdmodels.cmem import Mem
from patcher.mods.hdmodels.heap import Heap

# main()'s globals the editors read
OUT_COUNT = 0x4590AA        # u16
OUT_SRC_TRB = 0x459098      # -> u16[]
OUT_SRC_BLOCK = 0x459090    # -> u16[]
OUT_TABLE = 0x45A4A8        # -> 16 bytes a block
OUT_HEADER = 0x45A4C0       # 16 u32, in place
TRB_COUNT = 0x4590A8        # u16
TRB_TABLE = 0x45A500        # -> (-> 16 bytes a block)[]
TRB_HEADER = 0x45A508       # -> 16 u32 a model
TRB_START = 0x4590A0        # -> u32[]
TRB_KIND = 0x459088         # -> u16[]
TRB_TAGS = 0x459AD8         # -> (-> u32[])[]
TRB_ID = 0x4590D8           # -> 4 bytes a model
RECIPE_VERSION = 0x459ACC   # u16
FLAGS = 0x459AC8            # u32
ANSWERS = 0x459080          # u32
SILENT = 0x459084           # u32
REPL2_FLAG = 0x4590E0       # u8
TEXTURES = 0x4590AC         # u16
VOLUME = 0x44A19C          # 100 of them, 0x28 bytes each: type, size, rotation, centre
VOLUMES = 100
VOLUME_SIZE = 0x28
VOLUME_COUNT = 0x44A182     # i16
SKL_SIZE = 0x45A52C         # u32, the skeleton block's size in its own model's table


def mem_for(b) -> Mem:
    """the build's address space, made the first time an editor wants it"""
    mem = getattr(b, "mem", None)
    if mem is not None:
        return mem
    mem = b.mem = Mem()
    # .data's non-zero words: a couple of defaults, and the hundred selection
    # volumes the mesh edits test points against, each scaled 1 to start
    mem.set_u32(0x44A000, 10)
    mem.write(0x44A040, b"pc\0")
    mem.set_u32(0x44A020, 1)
    for n in range(VOLUMES):
        for k in range(3):
            mem.set_f32(VOLUME + n * VOLUME_SIZE + 4 + k * 4, 1.0)
    b.heap = Heap(mem, b.say)
    count = len(b.trbs)
    tables, headers, starts, kinds, tags = (mem.malloc(count * 8), mem.malloc(count * 64),
                                           mem.malloc(count * 4), mem.malloc(count * 2),
                                           mem.malloc(count * 8))
    ids = mem.malloc(count * 4)
    for n, trb in enumerate(b.trbs):
        mem.set_u64(tables + n * 8, mem.put(trb.table))
        mem.write(headers + n * 64, struct.pack("<16I", *trb.header))
        mem.set_u32(starts + n * 4, trb.start)
        mem.set_u16(kinds + n * 2, trb.kind)
        tag_block = trb.read(trb.start + trb.offset(trb.count - 2), trb.count * 4)
        mem.set_u64(tags + n * 8, mem.put(tag_block))
        mem.write(ids + n * 4, trb.ident[:4].ljust(4, b"\0"))
    mem.set_u64(TRB_TABLE, tables)
    mem.set_u64(TRB_HEADER, headers)
    mem.set_u64(TRB_START, starts)
    mem.set_u64(TRB_KIND, kinds)
    mem.set_u64(TRB_TAGS, tags)
    mem.set_u64(TRB_ID, ids)
    mem.set_u16(TRB_COUNT, count)
    return mem


def push(b) -> Mem:
    """main()'s current state where the editors look for it"""
    mem = mem_for(b)
    m = b.count
    table = mem.u64(OUT_TABLE)
    if not table or mem.sizes.get(table, 0) < m * 16:
        table = mem.malloc(m * 16)
        mem.set_u64(OUT_TABLE, table)
        mem.set_u64(OUT_SRC_TRB, mem.malloc(m * 2))
        mem.set_u64(OUT_SRC_BLOCK, mem.malloc(m * 2))
    mem.write(table, b.table[:m * 16])
    for n in range(m):
        mem.set_u16(mem.u64(OUT_SRC_TRB) + n * 2, b.r.out_trb[n])
        mem.set_u16(mem.u64(OUT_SRC_BLOCK) + n * 2, b.r.out_block[n])
    mem.write(OUT_HEADER, struct.pack("<16I", *b.header))
    mem.set_u16(OUT_COUNT, m)
    mem.set_u16(RECIPE_VERSION, b.version)
    mem.set_u32(FLAGS, b.r.flags)
    mem.set_u32(ANSWERS, b.r.answers)
    mem.set_u8(REPL2_FLAG, 1 if b.swap_first else 0)
    mem.set_u32(SKL_SIZE, b.skl_size)
    return mem


def pull(b) -> None:
    """what an editor changed of main()'s state, back into it"""
    mem = b.mem
    m = b.count
    b.table[:m * 16] = mem.get(mem.u64(OUT_TABLE), m * 16)
    b.header = list(struct.unpack("<16I", mem.get(OUT_HEADER, 64)))
    # flags and answers live on the recipe, where ::F and ::A change them;
    # the copies in memory are only for the ported code to read


def find_block(b, trb: int, tag: bytes) -> int:
    """FUN_0040a660: a model's first block whose 4 byte tag is `tag`, -1 if none"""
    src = b.trbs[trb]
    needle = tag[:4].ljust(4, b"\0")
    for n in range(src.count):
        if same_tag(needle, bytes(src.tag(n))):
            return n
    b.say(f"Error: Block {tag.decode('latin-1')} not found in file {trb}\n")
    return -1


def same_tag(needle: bytes, have: bytes) -> bool:
    """FUN_00409990 over 4 bytes: ~ reads as a NUL on either side, the needle's NUL ends it"""
    for k in range(4):
        x, y = needle[k], have[k]
        if (0 if x == 0x7E else x) != (0 if y == 0x7E else y):
            return False
        if x == 0:
            return True
    return True
