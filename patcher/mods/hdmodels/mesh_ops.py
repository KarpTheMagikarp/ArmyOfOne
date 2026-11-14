"""
The structural mesh edits: vertices cut out or copied, meshes deleted or
spliced in from another model, vertex components (DECLs) dropped or added,
plus the two odd ones out, a vertex nudge and a mesh diff to a text file.

All of them work through the parse struct (wrb.py) and resize the block in
place or into a new one, then fix every size above the mesh with
wrb.shift and parse again. Ported line for line from the exe's assembly
where Ghidra's types were off, so the in-place moves, the padding they zero
(and the padding they forget to) come out on the same bytes.

The parse structs' layout, as wrb.parse fills them:

    MDL   0x40: +0 header, +0x10 mesh list header, +0x18 meshes, +0x30 count
    mesh  0x30: +0 MESH, +0x08 streams, +0x10 ENVDs, +0x18 the 0x50 trailer,
                +0x20/+0x24 counts, +0x28/+0x2c where it starts and ends
    STMS  0x38: +0 header, +0x08 component table, +0x10 vertices, +0x18 pad,
                +0x1c components, +0x20 table at, +0x24 start, +0x28 end,
                +0x2c vertices at
    ENVD  0x38: +0 header, +0x08 table, +0x18 vertex list (u16), +0x20 weights
"""

from patcher.mods.hdmodels import cfile, volume, wrb
from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import f32, i16, i32, u16, u32
from patcher.mods.hdmodels.meshnum import NORM, comp_read, valid, vertex_range

STMS = ENVD = 0x38
BLEND_INDEX, BLEND_WEIGHT = 0xF, 0xE


def _mesh_at(mem, ps: int, mdl: int, mesh: int) -> int:
    return mem.u64(mem.u64(ps + wrb.MDLS) + mdl * 0x40 + 0x18) + mesh * 0x30


def _crem(x: int, m: int) -> int:
    # C's %, which keeps the dividend's sign
    r = abs(x) % m
    return -r if x < 0 else r


def _zero(mem, at: int, count: int) -> None:
    for q in range(max(count, 0)):
        mem.set_u8(at + q, 0)


@wrb.handler(0x413C10)
def remove_selected(w, mdl, mesh, first, last, cur, ps=None):
    """FUN_00413c10 (:O, :r): every vertex in first..last the selection holds
    cut out one at a time, walking down so the ones still to test keep their
    numbers"""
    mem = w.mem
    ps = w.ps if ps is None else ps
    verts = mem.be16(mem.u64(w.mesh(mdl, mesh, ps)) + 0x38)
    ok, first, last = vertex_range(w, first, last, "Remove Vertex 1", verts)
    if not ok or not valid(w, mdl, mesh, "Remove Vertex 1", ps):
        return
    # stream 1 holds the positions; its format is read once, its data and
    # stride every pass since each cut moves them
    pos = mem.u64(_mesh_at(mem, ps, mdl, mesh) + 0x08) + STMS
    kind = mem.be32(mem.u64(pos + 0x08) + 4)
    v = i32(last)
    while i32(first) <= v:
        pos = mem.u64(_mesh_at(mem, ps, mdl, mesh) + 0x08) + STMS
        at = mem.u64(pos + 0x10) + i32(mem.be16(mem.u64(pos) + 0x1A) * v)
        x, y, z = (comp_read(kind, n, mem, at) for n in range(3))
        if volume.selected(w, x, y, z, 7):
            remove_vertices(w, mdl, mesh, v, v, cur, ps=ps)
        v -= 1


@wrb.handler(0x40D870)
def remove_vertices(w, mdl, mesh, first, last, cur, ps=None):
    """FUN_0040d870 (:R): vertices first..last cut out of a mesh, with every
    triangle that touched them and their bone bindings"""
    mem = w.mem
    ps = w.ps if ps is None else ps
    me = w.mesh(mdl, mesh, ps)
    verts = mem.be16(mem.u64(me) + 0x38)
    ok, first, last = vertex_range(w, first, last, "Remove Vertex", verts)
    if not ok:
        return
    if not valid(w, mdl, mesh, "Remove Vertex", ps):
        return
    first, last = i32(first), i32(last)
    gone = u16((last & 0xFFFF) + 1 - first)
    # what every index past the cut moves by, negative, in 16 bits
    step = u16(first - ((last & 0xFFFF) + 1))
    head = mem.u64(me)
    mem.set_be16(head + 0x38, step + verts)
    stms = mem.u64(me + 0x08)

    # the vertex streams close up in place; the gap they leave is only
    # squeezed out at the very end, stream by stream
    streams_cut = 0
    for k in range(1, mem.u8(head + 0x32)):
        s = stms + k * STMS
        sh = mem.u64(s)
        stride = mem.be16(sh + 0x1A)
        count = mem.be32(sh + 0x14)
        cut = u32(gone * stride)
        data = mem.u64(s + 0x10)
        dst = data + u32(stride * first)
        pad = mem.u32(s + 0x18)
        # not the alignment the stream needs, just the old pad plus the cut, mod 16
        newpad = (cut + pad) & 0xF
        mem.set_u32(s + 0x18, newpad)
        mem.set_be32(sh + 0x14, count - gone)
        mem.memmove(dst, dst + cut, u32((count - (first + gone)) * stride))
        _zero(mem, data + u32(stride * u32(count - gone)), newpad)
        mem.set_be32(sh + 0x08, mem.be32(sh + 0x08) - cut)
        cut = u32(cut + i16(pad) - newpad)
        mem.set_be32(sh + 0x0C, mem.be32(sh + 0x0C) - cut)
        streams_cut = u32(streams_cut + cut)

    # triangles: any corner in the cut drops the whole face, the rest renumber
    s0 = stms
    total = mem.be32(mem.u64(s0) + 0x14) & 0xFFFF
    polys = mem.u64(s0 + 0x10)
    dropped = at = 0
    while True:
        left = u32(total - 3 * dropped)
        if at >= i32(left):
            break
        p = polys + at * 2
        a, b, c = mem.be16(p), mem.be16(p + 2), mem.be16(p + 4)
        if any(first <= v <= last for v in (a, b, c)):
            dropped = u16(dropped + 1)
            left = i32(total - 3 * dropped)
            if at < left:
                mem.memmove(p, p + 6, (left - at) * 2)
            continue
        for q, v in enumerate((a, b, c)):
            if v > last:
                mem.set_be16(p + q * 2, step + v)
        at = u16(at + 3)
    sh0 = mem.u64(s0)
    mem.set_be32(sh0 + 0x14, left)
    mem.set_be16(head + 0x3A, u16(total // 3 - dropped))
    cut6 = dropped * 6
    pad0 = i16(mem.u32(s0 + 0x18))
    rem = _crem(pad0 + cut6, 16)
    if rem > 0:
        _zero(mem, polys + i32(left * 2), rem)
    faces_cut = u32(pad0 - rem + cut6)
    mem.set_be32(sh0 + 0x08, mem.be32(sh0 + 0x08) - cut6)
    mem.set_be32(sh0 + 0x0C, mem.be32(sh0 + 0x0C) - faces_cut)

    # each ENVD's vertex list loses the cut vertices, renumbers the rest, and
    # gets its weights and padding laid out again behind it
    freed = 0
    cursor = polys
    envds = mem.u32(me + 0x24)
    for k in range(envds):
        e = mem.u64(me + 0x10) + k * ENVD
        table = mem.u64(e + 0x08)
        bones = mem.be16(table + 2)
        ids = mem.u64(e + 0x18)
        weights = cursor = mem.u64(e + 0x20)
        lost = n = 0
        while True:
            left = u32(bones - lost)
            if n >= left:
                break
            p = ids + n * 2
            v = mem.be16(p)
            if v > last:
                mem.set_be16(p, v + step)
            elif v >= first:
                lost = u16(lost + 1)
                rest = u32(bones - lost)
                if n < rest:
                    rest = u32(rest - n)
                    mem.memmove(p, p + 2, rest * 2)
                    mem.memmove(weights + n, weights + n + 1, rest)
                continue
            n = u16(n + 1)
        a = u32(left * 2)
        pad1 = -a & 3
        _zero(mem, ids + a, pad1)
        at = u32(a + pad1)
        mem.memmove(ids + at, weights, left)
        tail = u32(left + at)
        pad2 = -tail & 3
        _zero(mem, ids + tail, pad2)
        tail = u32(tail + pad2)
        pad3 = u32(-(mem.be16(table + 4) + tail)) & 0xF
        _zero(mem, ids + tail, pad3)
        mem.set_be16(table + 2, bones - lost)
        mem.set_u16(table + 8, mem.u16(table + 2))
        mem.set_be16(table + 6, mem.be16(table + 4) + at)
        mem.set_u16(table + 0x0C, mem.u16(table + 6))
        start = u32(tail + mem.be16(table + 4) + 0x10)
        end = u32(start + pad3)
        eh = mem.u64(e)
        mem.set_be32(eh + 0x08, start)
        freed = u32(freed + mem.be32(eh + 0x0C) - end)
        mem.set_be32(eh + 0x0C, end)

    # an ENVD left with no bones gets dropped below, so every blend index
    # past it steps down one. Only the first stream with blend indices counts
    emptied = False
    streams = mem.u8(head + 0x32)
    for k in range(1, streams):
        s = stms + k * STMS
        count = mem.be32(mem.u64(s) + 0x14)
        stride = mem.be16(mem.u64(s) + 0x1A)
        table = mem.u64(s + 0x08)
        hit = None
        for j in range(mem.u32(s + 0x1C)):
            if mem.be16(table + j * 16 + 0x0C) == BLEND_INDEX:
                hit = mem.be16(table + j * 16 + 2)
                break
        if hit is None:
            continue
        i = i16(u16(mem.u32(me + 0x24)) - 1)
        while i >= 0:
            e = mem.u64(me + 0x10) + i * ENVD
            if mem.be16(mem.u64(e + 0x08) + 2) == 0 and count:
                data = mem.u64(s + 0x10)
                for v in range(count):
                    at = data + u32(v * stride) + hit
                    for q in range(4):
                        bone = mem.u8(at + q)
                        if bone != 0xFF and bone > i:
                            mem.set_u8(at + q, bone - 1)
            i -= 1
        emptied = True
        break

    # now squeeze the gaps out: streams back to back, then the ENVDs kept,
    # the trailer, and the rest of the block
    if streams > 1:
        for k in range(streams - 1):
            sh = mem.u64(stms + k * STMS)
            dst = sh + mem.be32(sh + 0x0C)
            nxt = mem.u64(stms + (k + 1) * STMS)
            size = mem.be32(nxt + 0x0C)
            mem.memmove(dst, nxt, size)
            mem.set_u64(stms + (k + 1) * STMS, dst)
        cursor = dst + size
    for k in range(envds):
        e = mem.u64(me + 0x10) + k * ENVD
        eh = mem.u64(e)
        if mem.be16(mem.u64(e + 0x08) + 2) == 0 and emptied:
            mem.set_be32(head + 0x10, mem.be32(head + 0x10) - 1)
            mem.set_u8(head + 0x33, mem.u8(head + 0x33) - 1)
            freed = u32(freed + mem.be32(eh + 0x0C))
        else:
            size = mem.be32(eh + 0x0C)
            mem.memmove(cursor, eh, size)
            cursor += size
    mem.memmove(cursor, mem.u64(me + 0x18), 0x50)
    cursor += 0x50
    end = mem.u32(me + 0x2C)
    mem.memmove(cursor, cur + end, u32(mem.u32(ps + wrb.SIZE) - end))
    wrb.shift(w, mdl, mesh, ps, u32(faces_cut + streams_cut + freed), True)
    wrb.parse(w, ps, cur, True)


@wrb.handler(0x40C980)
def copy_vertex(w, mdl, mesh, src, dst, cur, ps=None):
    """FUN_0040c980 (:C): vertex src copied over vertex dst (a negative dst
    appends one), and bound to the bones src's weights name"""
    mem, heap = w.mem, w.heap
    ps = w.ps if ps is None else ps
    at = dst
    if i16(dst) < 0:
        cur = wrb.grow_verts(w, mdl, mesh, 1, ps, cur)
        at = u16(mem.be16(mem.u64(w.mesh(mdl, mesh, ps)) + 0x38) - 1)
    me = w.mesh(mdl, mesh, ps)
    if mem.u8(mem.u64(me) + 0x32) <= 1:
        return cur
    # the index stream is left alone: the copy only lands in the vertex streams
    for k in range(1, mem.u8(mem.u64(me) + 0x32)):
        s = mem.u64(me + 0x08) + k * STMS
        stride = mem.be16(mem.u64(s) + 0x1A)
        body = mem.u32(s + 0x2C)
        heap.copy(1, cur, i32(stride * i16(at) + body), cur, i32(stride * i16(src) + body), stride)

    for k in range(1, mem.u8(mem.u64(me) + 0x32)):
        s = mem.u64(me + 0x08) + k * STMS
        stride = mem.be16(mem.u64(s) + 0x1A)
        parts = mem.u32(s + 0x1C)
        index = weight = 0
        for j in range(1, parts):
            row = mem.u64(s + 0x08) + j * 16
            kind, off = mem.be16(row + 0x0C), mem.be16(row + 2)
            if kind == BLEND_INDEX:
                index = off
            elif kind == BLEND_WEIGHT:
                weight = off
            if not (index and weight):
                continue
            base = u32(stride * i16(at))
            data = mem.u64(s + 0x10)
            bones = [mem.u8(data + index + base + q) for q in range(4)]
            weights = [mem.u8(data + weight + base + q) for q in range(4)]
            for bone, wt in zip(bones, weights):
                if not wt:
                    continue
                cur, had = wrb.grow_envd(w, mdl, mesh, bone, 1, ps, cur)
                e = mem.u64(w.mesh(mdl, mesh, ps) + 0x10) + bone * ENVD
                mem.set_u8(mem.u64(e + 0x20) + had, wt)
                mem.set_be16(mem.u64(e + 0x18) + had * 2, at)
            return cur
    return cur


@wrb.handler(0x40D330)
def delete_mesh(w, cur, mdl, mesh, ps=None):
    """FUN_0040d330 (:D): a mesh cut out of its MDL; the meshes after it get
    their streams renumbered. Returns the MDL's last mesh index, -1 if invalid"""
    mem = w.mem
    ps = w.ps if ps is None else ps
    if not valid(w, mdl, mesh, "Delete Mesh", ps):
        return -1
    if w.verbose:
        w.say(f"Delete Mesh {mdl}:{mesh}\n")
    m = mem.u64(ps + wrb.MDLS) + mdl * 0x40
    me = mem.u64(m + 0x18) + mesh * 0x30
    start, end = mem.u32(me + 0x28), mem.u32(me + 0x2C)
    w.heap.copy(1, cur, i32(start), cur, i32(end), i32(mem.u32(ps + wrb.SIZE) - end))
    mem.set_be32(mem.u64(m + 0x10) + 0x24, mem.u32(m + 0x30) - 1)
    head = mem.u64(m)
    mem.set_be32(head + 0x10, mem.be32(head + 0x10) - 1)
    wrb.shift(w, mdl, -1, ps, u32(end - start), True)
    wrb.parse(w, ps, cur, True)

    # a stream's id is (mdl, mesh, stream) packed 5 bits apiece, plus one
    m = mem.u64(ps + wrb.MDLS) + mdl * 0x40
    count = mem.u32(m + 0x30)
    tag = ((mdl << 5) + mesh) << 5
    j = mesh
    while j < count:
        me = mem.u64(m + 0x18) + j * 0x30
        for k in range(mem.u32(me + 0x20)):
            sh = mem.u64(mem.u64(me + 0x08) + k * STMS)
            if mem.u16(sh + 0x18):
                mem.set_be16(sh + 0x18, tag + 1 + k)
        j += 1
        tag += 0x20
    return i32(count - 1)


@wrb.handler(0x40E7A0)
def drop_component(w, mdl, mesh, stream, comp, cur, ps=None):
    """FUN_0040e7a0 (:B, "Delete DECL"): one component cut out of a stream's
    vertex layout, and out of every vertex"""
    mem, heap = w.mem, w.heap
    ps = w.ps if ps is None else ps
    if not valid(w, mdl, mesh, "Delete DECL", ps):
        return
    size = mem.u32(ps + wrb.SIZE)
    me = w.mesh(mdl, mesh, ps)
    s = mem.u64(me + 0x08) + stream * STMS
    sh = mem.u64(s)
    parts = mem.be32(sh + 0x10)
    stride = mem.be16(sh + 0x1A)
    count = mem.be32(sh + 0x14)
    table = mem.u64(s + 0x08)
    head_bytes = mem.be16(table + comp * 16 + 2)
    write = i32(mem.u32(s + 0x24) + 0x20)
    read = i32(mem.u32(s + 0x2C))
    old_end = mem.u64(s + 0x10)
    after = new_stride = stride
    width = 0
    if parts == 0:
        parts = 0xFFFFFFFF
    else:
        parts -= 1
        for j in range(parts + 1):
            if j == comp:
                # the last component's width is never worked out, so cutting
                # it shrinks the vertices without shrinking the stride
                if comp < parts:
                    width = u16(mem.be16(table + j * 16 + 0x12) - mem.be16(table + j * 16 + 2))
                continue
            if j > comp:
                raw = mem.be16(table + j * 16 + 2)
                if after == stride:
                    after = raw
                mem.set_be16(table + j * 16 + 2, raw - width)
                heap.copy(1, cur, write, cur, i32(j * 16 + mem.u32(s + 0x20)), 0x10)
            write = i32(write + 0x10)
        new_stride = u16(stride - width)
    tail = u32(stride - after)
    for _ in range(count):
        if head_bytes:
            heap.copy(1, cur, write, cur, read, head_bytes)
            write, read = i32(write + head_bytes), i32(read + head_bytes)
            old_end += head_bytes
        old_end += width
        read = i32(read + width)
        if tail:
            heap.copy(1, cur, write, cur, read, tail)
            write, read = i32(write + tail), i32(read + tail)
            old_end += tail
    pad = u32(mem.u32(s + 0x24) - write) & 0xF
    # the pad it zeroes is where the old vertices ended, not where the new
    # ones do; what's between keeps stale vertex bytes until the tail lands
    _zero(mem, old_end, pad)
    end = i32(write + pad)
    mem.set_be32(sh + 0x08, write - mem.u32(s + 0x24))
    mem.set_be32(sh + 0x0C, end - mem.u32(s + 0x24))
    mem.set_be16(sh + 0x1A, new_stride)
    mem.set_u32(s + 0x1C, parts)
    mem.set_be32(sh + 0x10, parts)
    rest = mem.u32(s + 0x28)
    heap.copy(1, cur, end, cur, i32(rest), i32(size - rest))
    wrb.shift(w, mdl, mesh, ps, u32(rest - end), True)
    wrb.parse(w, ps, cur, True)


@wrb.handler(0x40E5E0)
def move_vertices(w, mdl, mesh, first, last, dx, dy, dz, ps=None):
    """FUN_0040e5e0 (:M): an int16 position nudged on vertices first..last"""
    mem = w.mem
    ps = w.ps if ps is None else ps
    me = w.mesh(mdl, mesh, ps)
    ok, first, last = vertex_range(w, first, last, "Move Vertex", mem.be16(mem.u64(me) + 0x38))
    if not ok or not valid(w, mdl, mesh, "Move Vertex", ps):
        return
    if (dx | dy | dz) & 0xFFFF == 0:
        w.say("Error: Move Vertex: Move values all zero.\n")
        return
    s = mem.u64(me + 0x08) + STMS
    if mem.be32(mem.u64(s + 0x08) + 4) == 1:
        w.say("MoveVertex does not work for environment variables.\n")
        return
    v = i32(first)
    while v <= i32(last):
        p = mem.u64(s + 0x10) + i32(mem.be16(mem.u64(s) + 0x1A) * v)
        for q, d in enumerate((dx, dy, dz)):
            mem.set_be16(p + q * 2, mem.be16(p + q * 2) + d)
        v += 1


@wrb.handler(0x40D4F0)
def insert_mesh(w, mdl, mesh, mdl2, mesh2, shader, cur, other, name, ps=None, ps2=None):
    """
    FUN_0040d4f0 (:I): another model's mesh spliced in before mesh `mesh`
    (or after the last when it equals the count), into a new block. Frees
    both the old block and the other model's
    """
    mem, heap = w.mem, w.heap
    ps = w.ps if ps is None else ps
    ps2 = w.other if ps2 is None else ps2
    m = mem.u64(ps + wrb.MDLS) + mdl * 0x40
    appending = mdl < mem.u32(ps + wrb.MDL_COUNT) and mesh == mem.u32(m + 0x30)
    if not appending and not valid(w, mdl, mesh, "Insert Mesh 1", ps):
        return cur
    if not valid(w, mdl2, mesh2, "Insert Mesh 2", ps2):
        return cur
    theirs = _mesh_at(mem, ps2, mdl2, mesh2)
    start = mem.u32(theirs + 0x28)
    span = u32(mem.u32(theirs + 0x2C) - start)
    count = mem.u32(m + 0x30)
    if mesh == count:
        if not count:
            raise ModelError("its recipe appends a mesh to an MDL that has none")
        # the end of the mesh before
        at = mem.u32(mem.u64(m + 0x18) + mesh * 0x30 - 4)
    else:
        at = mem.u32(mem.u64(m + 0x18) + mesh * 0x30 + 0x28)
    mem.set_be32(mem.u64(m + 0x10) + 0x24, count + 1)
    head = mem.u64(m)
    mem.set_be32(head + 0x10, mem.be32(head + 0x10) + 1)
    mem.set_be32(head + 0x08, span + mem.be32(head + 0x08))
    if mdl < u32(mem.u32(ps + wrb.MDL_COUNT) - 1):
        mem.set_be32(head + 0x0C, span + mem.be32(head + 0x0C))
    blk = mem.u64(ps)
    for field in (0x38, 0x5C, 0x58):
        mem.set_be32(blk + field, span + mem.be32(blk + field))
    mem.set_u32(blk + 0x10, mem.u32(blk + 0x10) + span)
    size = mem.u32(ps + wrb.SIZE)
    new = heap.new(i32(size + span))
    if not new:
        raise ModelError("its recipe inserts more meshes than the original tool has room for")
    heap.copy(1, new, 0, cur, 0, i32(at))
    heap.copy(1, new, i32(at), other, i32(start), i32(span))
    heap.copy(1, new, i32(at + span), cur, i32(at), i32(size - at))
    wrb.release(mem, ps2)
    heap.delete(other)
    heap.delete(cur)
    mem.set_u32(ps + wrb.SIZE, size + span)
    wrb.parse(w, ps, new, True)
    me = mem.u64(_mesh_at(mem, ps, mdl, mesh))
    mem.set_be32(me + 0x34, shader & 0xFFFF)
    text = name.encode("latin-1")
    length = len(text) & 0xFFFF
    # only an empty name leaves the old one; either way its length is redone
    chars = 0
    for k in range(0x10):
        if text[:1] not in (b"", b"\0"):
            mem.set_u8(me + 0x50 + k, text[k] if k < length else 0)
        chars += mem.u8(me + 0x50 + k) != 0
    mem.set_be32(me + 0x48, u16(chars) + 0x11)
    return new


# a new component's 16 byte table row by its type: (two words it carries, bytes it takes)
LAYOUTS = {0: ((4, 4), 8), 1: ((1, 3), 0xC), 2: ((3, 4), 4), 3: ((3, 4), 4), 8: ((2, 2), 4),
           9: ((2, 2), 4), 10: ((2, 2), 4), 11: ((2, 2), 4), 12: ((3, 4), 4), 13: ((3, 4), 4),
           14: ((3, 4), 4), 15: ((6, 4), 4)}


@wrb.handler(0x40ED40)
def add_component(w, mdl, mesh, stream, kind, pos, offset, cur, ps=None):
    """FUN_0040ed40 (:A): a component of type `kind` added to a stream's
    layout at row `pos` and byte `offset`, zeroed in every vertex"""
    mem, heap = w.mem, w.heap
    ps = w.ps if ps is None else ps
    if not valid(w, mdl, mesh, "Delete DECL", ps):
        return cur
    if kind not in LAYOUTS:
        # the exe fills the row from uninitialised stack for these
        raise ModelError("its recipe adds a vertex component of a type the original tool can't lay out")
    (lo, hi), width = LAYOUTS[kind]
    size = mem.u32(ps + wrb.SIZE)
    me = w.mesh(mdl, mesh, ps)
    s = mem.u64(me + 0x08) + stream * STMS
    sh = mem.u64(s)
    parts = mem.be32(sh + 0x10)
    stride = mem.be16(sh + 0x1A)
    count = mem.be32(sh + 0x14)
    span = mem.be32(sh + 0x0C)
    # type 1 rows go in with a zero type word
    row = (bytes(2) + (offset & 0xFFFF).to_bytes(2, "big") + lo.to_bytes(4, "big")
           + hi.to_bytes(4, "big") + (0 if kind == 1 else kind).to_bytes(2, "big") + bytes(2))
    record = mem.put(row)

    wide = u32(width + stride)
    tables = u32((parts + 3) * 16)
    used = u32(count * wide + tables)
    pad = u32(-used) & 0xF
    grown = u32(used + pad)
    mem.set_be32(sh + 0x08, used)
    mem.set_be32(sh + 0x0C, grown)
    mem.set_be16(sh + 0x1A, wide)
    mem.set_u32(s + 0x1C, parts + 1)
    mem.set_be32(sh + 0x10, parts + 1)
    new = heap.new(i32(size - span + grown + 1))
    if not new:
        raise ModelError("its recipe adds more components than the original tool has room for")
    first = mem.u32(s + 0x24)
    wrb.shift(w, mdl, mesh, ps, u32(grown - span), False)
    read = write = u32(first + 0x20)
    heap.copy(1, new, 0, cur, 0, i32(read))
    for j in range(parts):
        if j >= pos:
            mem.set_be16(cur + read + 2, mem.be16(cur + read + 2) + width)
            if j == pos:
                heap.copy(0, new, i32(write), record, 0, 0x10)
                write = u32(write + 0x10)
        heap.copy(1, new, i32(write), cur, i32(read), 0x10)
        read, write = u32(read + 0x10), u32(write + 0x10)
    read = u32(first + tables - 0x10)
    if parts == pos:
        heap.copy(0, new, i32(write), record, 0, 0x10)
        write = u32(write + 0x10)
    mem.free(record)
    for _ in range(count):
        heap.copy(1, new, i32(write), cur, i32(read), i32(offset))
        write = u32(write + width + offset)
        if wide > u32(width + offset):
            heap.copy(1, new, i32(write), cur, i32(read + offset), i32(stride - offset))
            write = u32(write + stride - offset)
            read = u32(read + stride)
        else:
            read = u32(read + offset)
    rest = mem.u32(s + 0x28)
    heap.copy(1, new, i32(pad + write), cur, i32(rest), i32(size - rest))
    wrb.parse(w, ps, new, True)
    heap.delete(cur)
    return new


@wrb.handler(0x425CA0)
def compare_meshes(w, mdl, mesh, mdl2, mesh2, path, ps=None, ps2=None):
    """FUN_00425ca0 (:c): every vertex whose position differs from the other
    model's mesh, appended to a text file as theirs then ours (x, z, -y)"""
    mem = w.mem
    ps = w.ps if ps is None else ps
    ps2 = w.other if ps2 is None else ps2
    if not valid(w, mdl, mesh, "Compare Mesh", ps) or not valid(w, mdl2, mesh2, "Compare Mesh 2", ps2):
        return
    ours, theirs = _mesh_at(mem, ps, mdl, mesh), _mesh_at(mem, ps2, mdl2, mesh2)
    verts = mem.be16(mem.u64(ours) + 0x38)
    s, t = mem.u64(ours + 0x08) + STMS, mem.u64(theirs + 0x08) + STMS
    stride, stride2 = mem.be16(mem.u64(s) + 0x1A), mem.be16(mem.u64(t) + 0x1A)
    if verts != mem.be16(mem.u64(theirs) + 0x38):
        w.say("CompareMesh: Mesh sizes are different.\n")
        return
    out = _append(w.b, path)
    lines = []
    for v in range(verts):
        p, q = mem.u64(s + 0x10) + v * stride, mem.u64(t + 0x10) + v * stride2
        if mem.get(p, 6) == mem.get(q, 6):
            continue
        a = [f32(i16(mem.be16(p + k * 2)) / NORM) for k in range(3)]
        b = [f32(i16(mem.be16(q + k * 2)) / NORM) for k in range(3)]
        lines.append("%f,%f,%f,%f,%f,%f\r\n" % (b[0], b[2], -b[1], a[0], a[2], -a[1]))
    out.write("".join(lines).encode("latin-1"))


def _append(b, path: str):
    """fopen(path, "a") in text mode: made if it isn't there, kept with the build's other writes"""
    written = b.__dict__.setdefault("written", {})
    f = written.get(path)
    if f is None:
        f = written[path] = cfile.CFile(path, b.files.read(path) or b"", writable=True)
    f.seek(0, cfile.SEEK_END)
    return f
