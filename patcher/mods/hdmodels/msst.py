"""
The msst block: a copy of every mesh's third vertex stream, kept apart
from the wrb (the game swaps it in for an alternate look).

Building one, the exe takes a model's wrb block, writes the msst's streams
over that wrb's third streams, runs the recipe's mesh edits on the result
as if it were a wrb of its own, then cuts the third streams back out into
a new msst. So edits made for the wrb land on its msst too.

Which model's wrb: the one the recipe marked with a 9 in front, or the
model the block's parameter names (1 based), remembered for the msst
blocks after it; which block of it: the parameter once the model's
known, or its block tagged "brw" when that's 0.
"""

from patcher.mods.hdmodels import state, wrb
from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import i16, i32, u32

THIRD = 2 * 0x38   # the third stream's entry in a mesh's stream list


def edit(b, n: int, block: bytearray, length: int, param: int):
    """main's msst branch, then FUN_0042c280"""
    t, k = b.msst_trb, param
    if t == -1:
        if param < 1 or len(b.trbs) < param:
            raise ModelError("its recipe has an msst block with no model to take it from")
        t = b.msst_trb = param - 1
        k = _find(b, n, t)
    elif param == 0:
        k = _find(b, n, t)
    src = b.trbs[t]
    if not 0 <= k < src.count - 1:
        raise ModelError("its msst block points at a block its model hasn't got")
    size = src.size(k)
    data = src.read(src.start + src.offset(k), size)
    if bytes(data[:7]) != b"SEDBwrb":
        raise ModelError("its msst block doesn't point at a mesh")
    w = wrb.Edit(b, 999)
    try:
        out = _rebuild(w, block, length, data, size)
    except (IndexError, OverflowError) as e:
        raise ModelError("its msst block doesn't line up with its mesh") from e
    state.pull(b)
    return out, len(out)


def _find(b, n: int, t: int) -> int:
    k = i16(state.find_block(b, t, b"brw"))
    b.params[n] = k
    if k == 0:
        raise ModelError("its recipe's msst block has no mesh to go with it")
    return k


def _streams(w, ps):
    """the third stream entry of every mesh, MDL by MDL, as the exe walks them"""
    mem = w.mem
    out = []
    for i in range(mem.u32(ps + wrb.MDL_COUNT)):
        m = mem.u64(ps + wrb.MDLS) + i * 0x40
        for j in range(mem.u32(m + 0x30)):
            me = mem.u64(m + 0x18) + j * 0x30
            if mem.u32(me + 0x20) < 3:
                raise ModelError("its msst block's mesh has no third stream")
            out.append(mem.u64(me + 0x08) + THIRD)
    return out


def _rebuild(w, block, length: int, data, size: int) -> bytes:
    mem, heap = w.mem, w.heap
    msst = heap.new(length)
    mem.write(msst, bytes(block[:length]))
    mesh = heap.new(size)
    mem.write(mesh, bytes(data[:size]))
    ps = mem.malloc(wrb.PARSE)
    mem.set_u32(ps + wrb.SIZE, size)
    wrb.parse(w, ps, mesh)
    start = mem.be32(msst + 0x50)
    for k, s in enumerate(_streams(w, ps)):
        bytes_in = mem.be32(mem.u64(s) + 8)
        at = mem.be32(msst + k * 0x20 + 0x50)
        heap.copy(1, mesh, i32(mem.u32(s + 0x24) + 0x10), msst, i32(at + 0x30), i32(bytes_in - 0x10))
    wrb.release(mem, ps)

    cur, size = wrb.run(w, mesh, size)

    mem.set_u32(ps + wrb.SIZE, size)
    wrb.parse(w, ps, cur)
    end = start
    streams = _streams(w, ps)
    for k, s in enumerate(streams):
        mem.set_be32(msst + k * 0x20 + 0x50, end)
        mem.set_be32(msst + k * 0x20 + 0x54, mem.be32(mem.u64(s) + 8) - 0x10)
        end = u32(end - 0x10 + mem.u32(s + 0x28) - mem.u32(s + 0x24))
    mem.set_u32(msst + 0x10, end)
    total = i32(end + 0x30 + (-end & 0xF))
    out = heap.new(total)
    at = i32(mem.be32(msst + 0x50) + 0x30)
    heap.copy(1, out, 0, msst, 0, at)
    for s in streams:
        heap.copy(1, out, at, cur, i32(mem.u32(s + 0x24) + 0x10), i32(mem.be32(mem.u64(s) + 8) - 0x10))
        at = i32(at - 0x10 + mem.u32(s + 0x28) - mem.u32(s + 0x24))
    heap.delete(msst)
    heap.delete(cur)
    result = mem.get(out, total)
    # main frees the new block once it's written
    heap.delete(out)
    return result
