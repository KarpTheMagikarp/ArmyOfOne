"""
The mesh edits that don't fit anywhere else: bone weights set by hand or
copied off another mesh (:v), a rig file's weights snapped onto the nearest
vertices (:f), a Wavefront .obj appended (:F), a vertex merge (:i), and
components out of a catalog file, placed one at a time (:S) or scattered
at random over a picked surface (:k).

Bone weights live twice in a mesh. Each vertex carries four bone numbers and
four weight bytes in a vertex stream (components 0xf and 0xe), and each
ENVD (one per bone the mesh binds to) carries the list of vertices it
moves, a u16 each, with a weight byte each after them. The weight edits
keep both in step: a vertex dropped off a bone comes off that ENVD's list,
a new one goes on, growing it, and a bone the mesh never bound gets an
ENVD of its own.

Ported line for line from the assembly, onto the parse struct wrb.py
keeps, so the exe's off-by-ones in those list shuffles come out the same.
"""

import math
import struct
import time

from patcher.mods.hdmodels import cfile, skeleton, volume, wrb
from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import atof, atoi, f32, i16, i32, u16, u32
from patcher.mods.hdmodels.meshnum import (comp_read, comp_write, cvtt, float_to_byte, float_to_half,
                                           valid, vertex_range)
from patcher.mods.hdmodels.skeleton import compare

STMS = ENVD = 0x38
# HeapAlloc serves blocks from about here up with VirtualAlloc, and frees them
# back to the OS, so a read through a stale pointer into one faults (Wine's
# threshold; Windows' is 0xfe000)
LARGE_BLOCK = 0xFF000
BLEND_INDEX, BLEND_WEIGHT, POSITION = 0xF, 0xE, 0x0
NONE = 0xFF


def _mesh(mem, ps: int, mdl: int, mesh: int) -> int:
    return mem.u64(mem.u64(ps + wrb.MDLS) + mdl * 0x40 + 0x18) + mesh * 0x30


def _envd(mem, ps: int, mdl: int, mesh: int, n: int) -> int:
    me = _mesh(mem, ps, mdl, mesh)
    if n >= mem.u32(me + 0x24):
        # past the parse's array the exe follows heap garbage as pointers
        raise ModelError("its recipe binds a vertex to a bone its mesh hasn't got")
    return mem.u64(me + 0x10) + n * ENVD


def _named(mem, name: bytes, at: int) -> bool:
    """FUN_00409aa0 with no length: the C string at `at` reads as `name`"""
    return compare(name, mem.get(at, len(name) + 1), 0) & 0xFFFF == 0


def _text(f) -> None:
    # fopen(.., "r"): msvcrt's text mode, CRLF in as LF and ^Z the end
    f.data = bytearray(bytes(f.data).split(b"\x1a", 1)[0].replace(b"\r\n", b"\n"))


def _fields(w, f) -> None:
    """fgets a line of `f` into the buffer the recipe's argument reads walk"""
    w.r.rest = (f.gets(0xFF) or b"").decode("latin-1")


@wrb.handler(0x40CD00)
def unbind(w, mdl, mesh, envd, vertex, drop, cur, ps=None):
    """FUN_0040cd00: a vertex taken off one ENVD's list, and the ENVD shrunk to fit"""
    mem = w.mem
    ps = w.ps if ps is None else ps
    e = _envd(mem, ps, mdl, mesh, envd)
    table = mem.u64(e + 0x08)
    count = mem.be16(table + 2)
    head, verts, weights = mem.u64(e), mem.u64(e + 0x18), mem.u64(e + 0x20)
    end = mem.u32(e + 0x30)
    vertex &= 0xFFFF
    removed = k = 0
    while u32(count - removed) > k:
        if mem.be16(verts + k * 2) != vertex:
            k += 1
            continue
        removed = (removed + 1) & 0xFFFF
        rest = u32(count - removed)
        if rest == 0:
            # rest - 1 wraps, so the exe memmoves 4GB and dies
            raise ModelError("its recipe unbinds the last vertex a bone holds, "
                             "which crashes the original tool")
        # one short on both moves: the list's last entry is lost (and doubled
        # into the slot before it), and with one entry after the match none
        # move, so the match is found again and takes its neighbour with it
        if u32(rest - 1) > k:
            left = u32(rest - k)
            mem.memmove(verts + k * 2, verts + k * 2 + 2, u32((left + 0x7FFFFFFF) * 2))
            mem.memmove(weights + k, weights + k + 1, u32(left - 1))
    if removed == 0:
        return
    # with nothing left the exe's "isn't done" branch would drop the whole
    # ENVD, but the move above has crashed long before it gets there
    left = u32(count - removed)
    at = u32(left * 2)
    if left & 1:
        mem.set_u8(verts + at, 0)
        mem.set_u8(verts + at + 1, 0)
        at = u32(at + 2)
    mem.memmove(verts + at, weights, left)
    tail = u32(left + at)
    pad = -tail & 3
    for q in range(pad):
        mem.set_u8(verts + tail + q, 0)
    tail = u32(tail + pad)
    names_at = mem.be16(table + 4)
    pad = -(names_at + tail) & 0xF
    for q in range(pad):
        mem.set_u8(verts + tail + q, 0)
    mem.set_be16(table + 2, left)
    mem.set_u16(table + 8, mem.u16(table + 2))
    mem.set_be16(table + 6, names_at + at)
    mem.set_u16(table + 0x0C, mem.u16(table + 6))
    size = u32(tail + names_at + 0x10)
    mem.set_be32(head + 0x08, size)
    size = u32(size + pad)
    gone = u32(mem.be32(head + 0x0C) - size)
    mem.set_be32(head + 0x0C, size)
    mem.memmove(head + size, cur + end, u32(mem.u32(ps + wrb.SIZE) - end))
    wrb.shift(w, mdl, i16(mesh), ps, gone, True)
    wrb.parse(w, ps, cur, True)


@wrb.handler(0x40F8B0)
def bone_envd(w, mdl, mesh, name: bytes, add, cur, ps=None):
    """
    FUN_0040f8b0: which of a mesh's ENVDs binds to the bone `name`, as
    (block, index). With `add`, a bone it doesn't bind yet gets a new empty
    ENVD on the end (and the block moves); without, the index is -1
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    me = _mesh(mem, ps, mdl, mesh)
    k = 0
    while k < mem.u32(me + 0x24):
        if _named(mem, name, mem.u64(mem.u64(me + 0x10) + k * ENVD + 0x10)):
            return cur, k
        k += 1
    if not add:
        return cur, -1
    index = i16(mem.u32(me + 0x24))
    if index <= 0:
        # it copies up to the end of the ENVD before the new one, read from
        # in front of the array when there's none
        raise ModelError("its recipe binds bones on a mesh that has no bone bindings")
    head = mem.u64(me)
    mem.set_u8(head + 0x33, mem.u8(head + 0x33) + 1)
    mem.set_be32(head + 0x10, mem.be32(head + 0x10) + 1)
    name = name.split(b"\0", 1)[0]
    words = len(name) & ~3
    room = (words + 4) & 0xFFFF
    pad = -(words + 4) & 0xF
    size = (words + 0x24) & 0xFFFF
    total = mem.u32(ps + wrb.SIZE)
    new = w.heap.new(i32(size + total + pad))
    if not new:
        raise ModelError("its mesh edits run the original tool out of memory blocks")
    at = mem.u32(mem.u64(me + 0x10) + index * ENVD - 8)
    wrb.shift(w, mdl, i16(mesh), ps, u32(pad + size), False)
    w.heap.copy(1, new, 0, cur, 0, i32(at))
    p = new + at
    mem.write(p, b"ENVD")
    mem.set_be32(p + 0x08, size)
    mem.set_be32(p + 0x0C, pad + size)
    mem.set_be16(p + 0x10, 0x10)
    for off in (0x14, 0x1A, 0x16, 0x1C):
        mem.set_be16(p + off, words + 0x14)
    mem.write(p + 0x20, name[:room].ljust(room, b"\0"))
    w.heap.copy(1, new, i32(size + at + pad), cur, i32(at), i32(total - at))
    wrb.parse(w, ps, new, True)
    w.heap.delete(cur)
    return new, index


@wrb.handler(0x411C40)
def rebind(w, mdl, mesh, vertex, bones, weights, old, cur, ps=None):
    """
    FUN_00411c40: a vertex's ENVD entries moved over to its new bones. `old`
    is where its current four bone numbers sit in the block; any of them
    not among `bones` lets go of it, and each of `bones` takes it on with
    its weight (an ENVD grown by one when the vertex is new to it)
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    vertex &= 0xFFFF
    for k in range(4):
        bone = mem.u8(old + k)
        if bone != NONE and bone not in bones:
            unbind(w, mdl, mesh, bone, vertex, 1, cur, ps)
    for k, bone in enumerate(bones):
        if bone == NONE:
            continue
        e = _envd(mem, ps, mdl, mesh, bone)
        listed = mem.be16(mem.u64(e + 0x08) + 2)
        for q in range(listed):
            if mem.be16(mem.u64(e + 0x18) + q * 2) == vertex:
                mem.set_u8(mem.u64(e + 0x20) + q, weights[k])
                break
        else:
            cur, q = wrb.grow_envd(w, mdl, mesh, bone, 1, ps, cur)
            e = _envd(mem, ps, mdl, mesh, bone)
            mem.set_u8(mem.u64(e + 0x20) + q, weights[k])
            mem.set_be16(mem.u64(e + 0x18) + q * 2, vertex)
    return cur


NO_SKIN = {"idx": 0, "stride": 0, "stream": 1, "wt": 0}


def _skin(mem, me: int):
    """the bone number and weight components past stream 0, as the :v scan finds them; the last wins"""
    found = dict(NO_SKIN)
    stms = mem.u64(me + 0x08)
    s = 1
    while s < mem.u32(me + 0x20):
        st = stms + s * STMS
        for c in range(mem.u32(st + 0x1C)):
            comp = mem.u64(st + 0x08) + c * 0x10
            kind = mem.be16(comp + 0x0C)
            if kind == BLEND_INDEX:
                found.update(idx=mem.be16(comp + 2), stride=mem.be16(mem.u64(st) + 0x1A), stream=s)
            elif kind == BLEND_WEIGHT:
                found["wt"] = mem.be16(comp + 2)
        s += 1
    return found


def _pos_stream(mem, me: int) -> int:
    if mem.u32(me + 0x20) < 2:
        # stream 1 is taken on faith, read from past the parse's array
        raise ModelError("its recipe edits a mesh with no vertex positions")
    return mem.u64(me + 0x08) + STMS


@wrb.handler(0x4191A0)
def bone_weights(w, mdl, mesh, kind, first, last, picks, w0, w1, w2, w3, cur, ps=None):
    """
    FUN_004191a0 (:v): bone weights for a run of vertices. Kind 0 sets the
    four bones `picks` with weights w0..w3 on each; kind 2 copies them off
    whichever vertex of mesh (picks[0], picks[1]) sits on the very same
    spot, binding its bones by name. 0x10 keeps it to the selection
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    name = "Edit Bone Weighting"
    if not valid(w, mdl, mesh, name, ps):
        return cur
    low = kind & 0xF
    me = _mesh(mem, ps, mdl, mesh)
    if kind & 0xD == 0:
        ok, first, last = vertex_range(w, first, last, name, mem.be16(mem.u64(me) + 0x38))
        if not ok:
            return cur
    first, last = i32(first), i32(last)
    pos = _pos_stream(mem, me)
    pos_kind = mem.be32(mem.u64(pos + 0x08) + 4)
    pos_stride = mem.be16(mem.u64(pos) + 0x1A)
    mdl2 = mesh2 = 0
    if low in (1, 2):
        if not valid(w, picks[0], picks[1], name, ps):
            return cur
        mdl2, mesh2 = picks[0], picks[1]
    if mem.u32(me + 0x20) <= 1:
        w.say("Error: Edit Bone Weighting: Bone and/or Weight data location not found.\n")
        return cur
    skin = _skin(mem, me)
    if skin["stride"] == 0 or skin["wt"] == 0:
        # an offset of 0 counts as missing too
        w.say("Error: Edit Bone Weighting: Bone and/or Weight data location not found.\n")
        return cur
    select = kind & 0x10

    def outside(p):
        if not select:
            return False
        x, y, z = (comp_read(pos_kind, n, mem, p) for n in range(3))
        return mem.u16(volume.ACTIVE) != 0 and not volume.inside(w, x, y, z, 7)

    def idx_data():
        return mem.u64(mem.u64(_mesh(mem, ps, mdl, mesh) + 0x08) + skin["stream"] * STMS + 0x10)

    if low == 0:
        count = mem.u32(me + 0x24)
        p0, p1, p2, p3 = picks
        if (p1 != NONE and p1 >= count) or not (p2 < count or p2 == NONE) or \
                not (p3 == NONE or p3 < count) or not (p0 < count or p0 == NONE):
            w.say("Bone/ENVD number out of range.\n")
            return cur
        bones = [p & 0xFF for p in picks]
        weights = [v & 0xFF for v in (w0, w1, w2, w3)]
        stride, at = skin["stride"], skin["idx"]
        v = first
        while v <= last:
            vert = mem.u64(_pos_stream(mem, _mesh(mem, ps, mdl, mesh)) + 0x10) + i32(pos_stride * v)
            if not outside(vert):
                old = idx_data() + i32(stride * v) + at
                cur = rebind(w, mdl, mesh, v, bones, weights, old, cur, ps)
                row = idx_data() + i32(stride * v)
                for k in range(4):
                    mem.set_u8(row + at + k, bones[k])
                    mem.set_u8(row + skin["wt"] + k, weights[k])
            v += 1
        return cur
    if kind & 0xD:
        return cur
    return _copy_weights(w, mdl, mesh, mdl2, mesh2, first, last, select, outside, skin,
                         pos_stride, idx_data, cur, ps)


def _copy_weights(w, mdl, mesh, mdl2, mesh2, first, last, select, outside, skin,
                  pos_stride, idx_data, cur, ps):
    """kind 2 of :v, its second half"""
    mem = w.mem
    me2 = _mesh(mem, ps, mdl2, mesh2)
    stride2 = mem.be16(mem.u64(_pos_stream(mem, me2)) + 0x1A)
    verts2 = mem.be16(mem.u64(me2) + 0x38)
    theirs = _skin(mem, me2) if mem.u32(me2 + 0x20) > 1 else dict(NO_SKIN)

    def their_data():
        return mem.u64(mem.u64(_mesh(mem, ps, mdl2, mesh2) + 0x08) + theirs["stream"] * STMS + 0x10)

    wt = skin["wt"]
    v = first
    # strictly below last, unlike kind 0
    while v < last:
        vert = mem.u64(_pos_stream(mem, _mesh(mem, ps, mdl, mesh)) + 0x10) + i32(pos_stride * v)
        # the check reads the weights' offset from the position stream, as the exe does
        if sum(mem.u8(vert + wt + k) for k in range(4)) != 0xFF:
            w.say(f"{v} bone weighting issue\n")
        if (select and outside(vert)) or verts2 == 0:
            v += 1
            continue
        key, key2 = mem.u32(vert), mem.u16(vert + 4)
        other = mem.u64(_pos_stream(mem, _mesh(mem, ps, mdl2, mesh2)) + 0x10)
        for k in range(verts2):
            p = other + k * stride2
            if mem.u32(p) == key and mem.u16(p + 4) == key2:
                cur = _take(w, mdl, mesh, mdl2, mesh2, v, k, skin, theirs, their_data, idx_data,
                            cur, ps)
                break
        v += 1
    return cur


def _take(w, mdl, mesh, mdl2, mesh2, v, k, skin, theirs, their_data, idx_data, cur, ps):
    """one vertex's bones and weights off its twin in the other mesh"""
    mem = w.mem
    row = i32(k * theirs["stride"])
    bones, weights = [0] * 4, [0] * 4
    for j in range(4):
        data = their_data()
        bone = mem.u8(data + row + theirs["idx"] + j)
        bones[j] = bone
        weights[j] = mem.u8(data + row + theirs["wt"] + j)
        if bone == NONE:
            continue
        e = _envd(mem, ps, mdl2, mesh2, bone)
        table = mem.u64(e + 0x08)
        size = i32(mem.be16(table + 4) - mem.be16(table))
        text = mem.get(mem.u64(e + 0x10), max(size, 0)).split(b"\0", 1)[0]
        # strncpy into a zeroed 32 byte stack buffer
        if size > 0x20 or size < 0 or (size == 0x20 and len(text) == 0x20):
            raise ModelError("its recipe copies a bone whose name overruns the original tool's buffer")
        cur, index = bone_envd(w, mdl, mesh, text, True, cur, ps)
        bones[j] = index & 0xFF
    row = i32(skin["stride"] * v)
    cur = rebind(w, mdl, mesh, v, bones, weights, idx_data() + row + skin["idx"], cur, ps)
    row += idx_data()
    for j in range(4):
        mem.set_u8(row + skin["idx"] + j, bones[j])
        mem.set_u8(row + skin["wt"] + j, weights[j])
    return cur


GROUP, RIG = 0x1E, 0x20
FAR = f32(13.0)


@wrb.handler(0x419EC0)
def rig(w, mdl, mesh, first, last, unused, path, cur, ps=None):
    """
    FUN_00419ec0 (:f): bone weights off a rig file. Its first line counts
    the bone groups, one line each (group, bone number, bone name); then a
    count of rig points, one line each (skipped, group, x, y, z, four bone
    numbers, four weights). Each vertex in first..last takes the nearest rig
    point within sqrt(13), its bones named through the groups and bound
    with :v's kind 0
    """
    mem, heap = w.mem, w.heap
    ps = w.ps if ps is None else ps
    me = w.mesh(mdl, mesh, ps)
    ok, first, last = vertex_range(w, first, last, "Rigging", mem.be16(mem.u64(me) + 0x38))
    if not ok or not valid(w, mdl, mesh, "Rigging", ps):
        return cur
    f = cfile.fopen(w.b, path, "r")
    if f is None:
        w.say(f"Unable to open .obj file {path}\n")
        return cur
    _text(f)

    # the last position component found in any stream wins
    pos_off, pos_stream, pos_kind, pos_stride = 0, 1, 0, 0
    s = 0
    while s < mem.u32(me + 0x20):
        st = mem.u64(me + 0x08) + s * STMS
        stride = mem.be16(mem.u64(st) + 0x1A)
        c = 0
        while c < i32(mem.u32(st + 0x1C)):
            comp = mem.u64(st + 0x08) + c * 0x10
            if mem.be16(comp + 0x0C) == POSITION:
                pos_stream, pos_kind = s, mem.be32(comp + 4) & 0xFFFF
                pos_off, pos_stride = mem.be16(comp + 2), stride
            c += 1
        s += 1

    _fields(w, f)
    groups = i16(w.S())
    table = heap.new(i32(groups * GROUP))
    if groups > 0 and not table:
        raise ModelError("its mesh edits run the original tool out of memory blocks")
    for n in range(max(groups, 0)):
        g = table + n * GROUP
        _fields(w, f)
        mem.set_u16(g + 2, w.S())
        mem.set_u16(g + 4, NONE)
        mem.set_u16(g, w.S())
        name = w.T().encode("latin-1")
        mem.write(g + 6, name.split(b"\0", 1)[0][:0x18].ljust(0x18, b"\0"))

    _fields(w, f)
    points = i16(w.S())
    rigs = heap.new(i32(points << 5))
    if points > 0 and not rigs:
        raise ModelError("its mesh edits run the original tool out of memory blocks")
    if points > 0 and not table:
        raise ModelError("its rig file has points but no bone groups, which crashes the original tool")
    # which group matched last; neither is reset between points, and the
    # first starts each point at the last one's bone count, all as the exe
    a, b = 0, 0
    for n in range(max(points, 0)):
        e = rigs + n * RIG
        _fields(w, f)
        w.S()
        mem.set_u16(e, w.S())
        for k in range(3):
            mem.set_f32(e + 4 + k * 4, w.F())
        for k in range(8):
            mem.set_u16(e + 0x10 + k * 2, w.S())
        group = mem.i16(e)
        for i in range(1, 4):
            for gi in range(max(groups, 0)):
                g = table + gi * GROUP
                if mem.i16(g + 2) == group and mem.u16(e + 0x0E + i * 2) == mem.u16(g):
                    a = gi
            for j in range(i, 4):
                for gi in range(max(groups, 0)):
                    g = table + gi * GROUP
                    if mem.i16(g + 2) == group and mem.u16(e + 0x10 + j * 2) == mem.u16(g):
                        b = gi
                if not (0 <= a < groups and 0 <= b < groups):
                    # a stale match points past the groups into the heap; it
                    # only folds an unused bone (-1, weight 0) into nothing
                    if mem.u16(e + 0x10 + j * 2) != 0xFFFF or mem.u16(e + 0x18 + j * 2):
                        raise ModelError("its rig file names a bone with no group, "
                                         "which the original tool reads garbage for")
                    continue
                # two bones of the same name fold into the first
                x, y = table + a * GROUP + 6, table + b * GROUP + 6
                if compare(mem.cstr(x), mem.cstr(y), 0, True) & 0xFFFF == 0:
                    mem.set_u16(e + 0x10 + j * 2, 0xFFFF)
                    mem.set_u16(e + 0x16 + i * 2, mem.u16(e + 0x16 + i * 2) + mem.u16(e + 0x18 + j * 2))
                    mem.set_u16(e + 0x18 + j * 2, 0)
        kept = 0
        for k in range(4):
            bone = mem.u16(e + 0x10 + k * 2)
            if bone != 0xFFFF:
                mem.set_u16(e + 0x10 + kept * 2, bone)
                mem.set_u16(e + 0x18 + kept * 2, mem.u16(e + 0x18 + k * 2))
                kept += 1
        for k in range(kept, 4):
            mem.set_u16(e + 0x10 + k * 2, 0xFFFF)
            mem.set_u16(e + 0x18 + k * 2, 0)
        a = kept

    first, last = i32(first), i32(last)
    at = i32(pos_stride * first) + pos_off
    v = first
    while v <= last:
        p = at + mem.u64(mem.u64(_mesh(mem, ps, mdl, mesh) + 0x08) + pos_stream * STMS + 0x10)
        x, y, z = (comp_read(pos_kind, n, mem, p) for n in range(3))
        near, best = 0, FAR
        for n in range(max(points, 0)):
            e = rigs + n * RIG
            dx = f32(x - mem.f32(e + 4))
            dy = f32(y - mem.f32(e + 8))
            dz = f32(z - mem.f32(e + 12))
            d = f32(f32(dz * dz) + f32(f32(dx * dx) + f32(dy * dy)))
            if best > d:
                near = n
            best = d if d < best else best
        near = i16(near)
        if points <= 0:
            raise ModelError("its rig file has no points to weigh a vertex by")
        e = rigs + near * RIG
        bones, weights = [NONE] * 4, [0] * 4
        for k in range(4):
            bone = mem.i16(e + 0x10 + k * 2)
            if bone == -1:
                continue
            for gi in range(max(groups, 0)):
                g = table + gi * GROUP
                if mem.i16(g) == bone and mem.i16(g + 2) == mem.i16(e):
                    cur, index = bone_envd(w, mdl, mesh, mem.cstr(g + 6), True, cur, ps)
                    bones[k] = index & 0xFFFF
                    weights[k] = mem.u16(e + 0x18 + k * 2)
                    break
            else:
                w.say(f"Matching bone not found of vertex {v}, rig {near}, group {mem.i16(e)}, "
                      f"bone {bone}\n")
        cur = bone_weights(w, mdl, mesh, 0, v, v, tuple(bones), *(i16(x) for x in weights), cur, ps)
        at += pos_stride
        v += 1
    heap.delete(rigs)
    heap.delete(table)
    return cur


@wrb.handler(0x417300)
def merge(w, mdl, mesh, mdl2, mesh2, flags, first, last, cur, ps=None):
    """
    FUN_00417300 (:i): vertices first..last of mesh A (mdl, mesh) merged
    into mesh B (mdl2, mesh2), then A's triangles redrawn over them, as far
    as the exe gets. Flags: 1 reuses a vertex already in place, 2 deletes A
    after, 4 then drops A's MDL too if that leaves it a single mesh.

    It's riddled with crossed wires, all kept: the reuse test compares B's
    vertex v against A's first verts(B), a merged vertex's component
    offsets are read off B's table at row v, without flag 1 only the first
    vertex goes over, and the triangles are A's, read at three consecutive
    table entries from one index, appended to A. A merged vertex with a
    bone bound hands the ENVD lookup a NULL name and crashes it
    """
    mem, heap = w.mem, w.heap
    ps = w.ps if ps is None else ps
    name = "Merge Mesh"
    if not valid(w, mdl, mesh, name, ps) or not valid(w, mdl2, mesh2, name, ps):
        return cur
    me = _mesh(mem, ps, mdl, mesh)
    ok, first, last = vertex_range(w, first, last, name, mem.be16(mem.u64(me) + 0x38))
    if not ok:
        return cur
    verts = mem.be16(mem.u64(me) + 0x38)
    me2 = _mesh(mem, ps, mdl2, mesh2)
    verts2, faces2 = mem.be16(mem.u64(me2) + 0x38), mem.be16(mem.u64(me2) + 0x3A)
    stride = mem.be16(mem.u64(_pos_stream(mem, me)) + 0x1A)
    stride2 = mem.be16(mem.u64(_pos_stream(mem, me2)) + 0x1A)
    kind = mem.be32(mem.u64(_pos_stream(mem, me) + 0x08) + 4)
    # taken once, so after a resize it points into the freed block
    polys = mem.u64(mem.u64(me + 0x08) + 0x10)
    origin, origin_size = cur, mem.sizes.get(cur, 0)

    def a_stms():
        return mem.u64(_mesh(mem, ps, mdl, mesh) + 0x08)

    def b_stms():
        return mem.u64(_mesh(mem, ps, mdl2, mesh2) + 0x08)

    def has(me_, kind_):
        for s in range(1, mem.u32(me_ + 0x20)):
            st = mem.u64(me_ + 0x08) + s * STMS
            for c in range(mem.u32(st + 0x1C)):
                if mem.be16(mem.u64(st + 0x08) + c * 0x10 + 0x0C) == kind_:
                    return True
        return False

    for s in range(1, mem.u32(me2 + 0x20)):
        st = mem.u64(me2 + 0x08) + s * STMS
        for c in range(mem.u32(st + 0x1C)):
            t = i16(mem.be16(mem.u64(st + 0x08) + c * 0x10 + 0x0C))
            if not has(me, t):
                w.say(f"Error: Merge Mesh Failed.  Decl {t} not found in source ({mdl}, {mesh}).\n")
                return cur

    span = i32(i16(verts) * 2)
    table = heap.new(span)
    heap.fill(table, 0xFF, 0, span)
    if not table:
        raise ModelError("its recipe merges from a mesh too big for the original tool")
    v = first & 0xFFFF
    last = i32(last)
    seen = False
    added = 0
    count2 = i16(verts2)
    while v <= last:
        at = mem.u64(a_stms() + STMS + 0x10) + i32(stride * v)
        x, y, z = (comp_read(kind, n, mem, at) for n in range(3))
        if mem.u16(volume.ACTIVE) != 0 and not volume.inside(w, x, y, z, 7):
            v = (v + 1) & 0xFFFF
            continue
        reuse = None
        if flags & 1:
            mine = mem.u64(b_stms() + STMS + 0x10) + i32(stride2 * v)
            theirs = mem.u64(a_stms() + STMS + 0x10)
            for k in range(max(count2, 0)):
                p = theirs + i32(k * stride)
                if mem.u32(p) == mem.u32(mine) and mem.u16(p + 4) == mem.u16(mine + 4):
                    reuse = k
                    break
        elif seen:
            v = (v + 1) & 0xFFFF
            continue
        if reuse is not None:
            mem.set_u16(table + v * 2, reuse)
            seen = True
        else:
            cur, seen = _merge_vertex(w, mdl, mesh, mdl2, mesh2, v, added, verts2, stride, stride2,
                                      table, cur, ps)
            added = (added + 1) & 0xFFFF
        v = (v + 1) & 0xFFFF

    at = polys
    for k in range(max(i16(faces2), 0)):
        if cur != origin and origin_size >= LARGE_BLOCK:
            # past a resize this reads the freed block: a big one is handed
            # straight back to the OS and the read faults; a smaller one
            # still holds what it held, which is what's read here too
            raise ModelError("its recipe merges triangles out of a freed mesh block, "
                             "which crashes the original tool")
        i0 = mem.be16(at)
        if i0 + 2 >= i16(verts):
            raise ModelError("its recipe merges triangles that read past the original tool's table")
        a, b, c = (mem.i16(table + (i0 + q) * 2) for q in range(3))
        if a != -1 and b != -1 and c != -1:
            # the read only moves on once a triangle goes in
            at += 6
            cur = wrb.grow_faces(w, mdl, mesh, 1, cur, ps)
            wrb.edit_poly(w, mdl, mesh, 0, 0, 0, u16(a), u16(b), u16(c))
    if flags & 2 and wrb.call(w, 0x40D330, cur, mdl, mesh) & 0xFFFF == 0 and flags & 4:
        wrb.drop_mdl(w, mdl, cur, True)
    heap.delete(table)
    return cur


def _merge_vertex(w, mdl, mesh, mdl2, mesh2, v, added, verts2, stride, stride2, table, cur, ps):
    """one vertex of A appended to B, component by component; (block, anything copied)"""
    mem = w.mem
    index = (verts2 + added) & 0xFFFF
    mem.set_u16(table + v * 2, index)
    cur = wrb.grow_verts(w, mdl2, mesh2, 1, ps, cur)
    count2 = i16(verts2)
    old_row = i32(i32(stride2 * count2) * i16(added))
    new_row = i32((i16(added) + count2) * stride2)
    src_row = i32(v * stride)
    bones = 0
    copied = False
    me2 = _mesh(mem, ps, mdl2, mesh2)
    s = 1
    while s < mem.u32(me2 + 0x20):
        st = mem.u64(me2 + 0x08) + s * STMS
        c = 0
        while c < mem.u32(st + 0x1C):
            comps = mem.u64(st + 0x08)
            kind = mem.be16(comps + c * 0x10 + 0x0C)
            # the offset comes off row v of the table, not row c
            off = i16(mem.be16(comps + v * 0x10 + 2))
            if kind == BLEND_INDEX:
                bones = off + old_row + mem.u64(st + 0x10)
            me = _mesh(mem, ps, mdl, mesh)
            s2 = 1
            while s2 < mem.u32(me + 0x20):
                st2 = mem.u64(me + 0x08) + s2 * STMS
                hit = None
                for c2 in range(mem.u32(st2 + 0x1C)):
                    if mem.be16(mem.u64(st2 + 0x08) + c2 * 0x10 + 0x0C) == i16(kind):
                        hit = c2
                        break
                if hit is not None:
                    src = i16(mem.be16(mem.u64(st2 + 0x08) + hit * 0x10 + 2)) + src_row + \
                        mem.u64(st2 + 0x10)
                    dst = off + new_row + mem.u64(st + 0x10)
                    size = 8 if kind == 0 else 4
                    if not cur <= dst <= cur + mem.u32(ps + wrb.SIZE) - size:
                        raise ModelError("its recipe merges a vertex the original tool writes "
                                         "outside its mesh")
                    mem.memmove(dst, src, size)
                    copied = True
                    break
                if copied:
                    break
                s2 += 1
            c = (c + 1) & 0xFFFF
        s = (s + 1) & 0xFFFF
    if not bones:
        raise ModelError("its recipe merges into a mesh with no bone numbers, "
                         "which crashes the original tool")
    if any(mem.u8(bones + j) != NONE for j in range(4)):
        raise ModelError("its recipe merges a bone-weighted vertex, which crashes the original tool")
    return cur, copied


class _Line:
    """
    fgets(buf, 0xff, f) into one stack buffer that keeps whatever the last
    line left past its NUL, split in place the way FUN_00409ab0 does it
    """

    def __init__(self, f):
        self.f = f
        self.buf = bytearray(0x140)
        self.eof = False
        self.at = 0

    def read(self) -> None:
        self.buf[0] = 0
        got = self.f.gets(0xFF)
        # feof only once a read ran into the end, not when a newline stopped it
        self.eof = got is None or (not got.endswith(b"\n") and len(got) < 0xFE)
        if got is not None:
            self.buf[:len(got) + 1] = got + b"\0"
        self.at = 0

    def cut(self, at: int, sep: int) -> int:
        """FUN_00409ab0: NUL over the first `sep` from `at`, and where the rest starts"""
        buf = self.buf
        if buf[at] == 0:
            return at
        while buf[at] != sep:
            at += 1
            if buf[at] == 0:
                return at
        buf[at] = 0
        return at + 1

    def token(self) -> bytes:
        start = self.at
        self.at = self.cut(start, 0x20)
        return bytes(self.buf[start:self.buf.index(0, start)])

    def number(self) -> int:
        start = self.at
        self.at = self.cut(start, 0x20)
        self.cut(start, 0x2F)
        return atoi(bytes(self.buf[start:self.buf.index(0, start)]).decode("latin-1"))


def _axes(kind: int):
    """which of x, y, z each output axis takes, off the low 3 bits; 3 is past the floats"""
    k = kind & 7
    return k >> 1, ((k + 3) % 6) >> 1, (k // 3) * 3 - k + 2


def _cdiv(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


@wrb.handler(0x415D00)
def import_obj(w, mdl, mesh, kind, sx, ox, sy, oy, sz, oz, unused, path, cur, ps=None):
    """
    FUN_00415d00 (:F): a Wavefront .obj appended to a mesh. Read twice:
    once to count `v ` and `f ` lines and grow the mesh to fit, then for
    real, each v scaled and shifted per axis (x * sx + ox ...), its axes
    swizzled by kind & 7, and vn, vb, vt and f filling in the components
    the mesh has. Triangles go in turned over (a, c, b) unless kind has 8.
    New vertices get bone 0 at full weight, then the normals are rebuilt
    with :G
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    if not valid(w, mdl, mesh, "Import .obj", ps):
        return cur
    f = cfile.fopen(w.b, path, "r")
    if f is None:
        w.say(f"Unable to open .obj file {path}\n")
        return cur
    _text(f)
    line = _Line(f)
    verts = faces = 0
    while True:
        line.read()
        head = bytes(line.buf[:2])
        if head == b"v ":
            verts = (verts + 1) & 0xFFFF
        elif head == b"f ":
            faces = (faces + 1) & 0xFFFF
            # parsed and thrown away, but the cuts land in the buffer the
            # second pass reads its lines into
            line.at = line.cut(0, 0x20)
            for _ in range(3):
                line.number()
        if line.eof:
            break
    if w.verbose:
        w.say(f"Import OBJ: Vertex Add: {verts}   Polygon Add: {faces}\n")
    if verts == 0:
        w.say(f"Import {path} failed.  Vertex count = 0")
        return cur

    me = _mesh(mem, ps, mdl, mesh)
    verts0, faces0 = mem.be16(mem.u64(me) + 0x38), mem.be16(mem.u64(me) + 0x3A)
    cur = wrb.grow_verts(w, mdl, mesh, verts, ps, cur)
    cur = wrb.grow_faces(w, mdl, mesh, faces, cur, ps)

    # (stream, offset, stride) of each component it fills; the last one of
    # a kind wins, and stream 0 counts as not there
    comp = {k: (0, 0, 0) for k in (0, 2, 3, 8, 0xD, 0xE, 0xF)}
    pos_kind = 0
    me = _mesh(mem, ps, mdl, mesh)
    s = 0
    while s < mem.u32(me + 0x20):
        st = mem.u64(me + 0x08) + s * STMS
        stride = mem.be16(mem.u64(st) + 0x1A)
        c = 0
        while c < mem.u32(st + 0x1C):
            at = mem.u64(st + 0x08) + c * 0x10
            t, off = mem.be16(at + 0x0C), mem.be16(at + 2)
            if t in comp:
                comp[t] = (s, off, stride)
                if t == POSITION:
                    pos_kind = mem.be32(at + 4) & 0xFFFF
            c += 1
        s += 1

    f = cfile.fopen(w.b, path, "r")
    if f is None:
        w.say(f"Unable to open .obj file 2 {path}\n")
        return cur
    _text(f)
    if w.verbose:
        q = i32(kind)
        w.say(f"order: {q >> 1} {_crem6(q + 3) >> 1} {_cdiv(q, 3) * 3 - q + 2}\n")
    a0, a1, a2 = _axes(kind)
    line.f = f
    counts = {"v": 0, "vn": 0, "vb": 0, "vt": 0, "f": 0}

    def data(t):
        s_, off, stride = comp[t]
        return mem.u64(mem.u64(_mesh(mem, ps, mdl, mesh) + 0x08) + s_ * STMS + 0x10) + off, stride

    def put(at, raw):
        if not cur <= at <= cur + mem.u32(ps + wrb.SIZE) - len(raw):
            raise ModelError("its .obj has more lines than its count left room for, past the end of the mesh")
        mem.write(at, raw)

    def floats():
        vals = [f32(atof(line.token().decode("latin-1"))) for _ in range(3)]
        # index 3 runs off the floats into the line buffer itself
        vals.append(struct.unpack_from("<f", line.buf, 0)[0])
        return vals

    while True:
        line.read()
        line.at = line.cut(0, 0x20)
        b0, b1, b2 = line.buf[0], line.buf[1], line.buf[2]
        if b0 == 0x76 and b1 == 0:
            n = u32(verts0 + counts["v"])
            at, stride = data(POSITION)
            at += u32(stride * n)
            v = floats()
            x = f32(f32(v[a0] * sx) + ox)
            y = f32(f32(sy * v[a1]) + oy)
            z = f32(f32(sz * v[a2]) + oz)
            if pos_kind == 4:
                put(at, struct.pack(">4H", _unit(x, True), _unit(y), _unit(z), 1))
            elif pos_kind == 1:
                put(at, struct.pack(">3f", x, y, z))
            else:
                w.say(f"Unknown vertex iType {pos_kind} for .obj import.\n")
            cur = _bind_new(w, mdl, mesh, comp, n, verts0 + counts["v"], cur, ps, put)
            # one byte at a time, both at once, the way the exe interleaves them
            a3, s3 = data(3)
            a13, s13 = data(0xD)
            for j in range(4):
                if comp[3][0]:
                    put(a3 + u32(s3 * n) + j, b"\xff")
                if comp[0xD][0]:
                    put(a13 + u32(s13 * n) + j, b"\0")
            counts["v"] += 1
        elif b0 == 0x76 and b1 in (0x6E, 0x62) and b2 == 0:
            t, key, last = (2, "vn", 0x7F) if b1 == 0x6E else (0xD, "vb", 0)
            if comp[t][0]:
                v = floats()
                at, stride = data(t)
                at += u32((verts0 + counts[key]) * stride)
                put(at, bytes(float_to_byte(v[a]) & 0xFF for a in (a0, a1, a2)) + bytes([last]))
                counts[key] += 1
        elif b0 == 0x76 and b1 == 0x74 and b2 == 0:
            if comp[8][0]:
                at, stride = data(8)
                at += u32((verts0 + counts["vt"]) * stride)
                u = f32(atof(line.token().decode("latin-1")))
                if u > HALF:
                    w.say("Invalid UV value\n")
                if u > QUARTER:
                    u = f32(HALF - u)
                v = atof(line.token().decode("latin-1"))
                u = f32(u + u)
                u = f32(u + u)
                hu = float_to_half(u) & 0xFFFF
                v = f32(v)
                hv = float_to_half(f32(v + v)) & 0xFFFF
                put(at, struct.pack(">2H", hu, hv))
                counts["vt"] += 1
        elif b0 == 0x66 and b1 == 0:
            at = mem.u64(mem.u64(_mesh(mem, ps, mdl, mesh) + 0x08) + 0x10) + \
                u32((faces0 + counts["f"]) * 6)
            a, b, c = (line.number() - 1 for _ in range(3))
            a, b, c = ((q + verts0) & 0xFFFF for q in (a, b, c))
            put(at, struct.pack(">3H", *((a, b, c) if kind & 8 else (a, c, b))))
            counts["f"] += 1
        if line.eof:
            break
    wrb.call(w, 0x414390, mdl, mesh, 0, 0x7FFF, 0)
    return cur


HALF, QUARTER, NORM_D = f32(0.5), f32(0.25), 32767.0


def _crem6(x: int) -> int:
    r = abs(x) % 6
    return -r if x < 0 else r


def _unit(v: float, first: bool = False) -> int:
    """a coordinate as int16 of 32767; the first clamps below -1 to 0, the others to -1"""
    if -1.0 > v:
        if first:
            return 0
        v = -1.0
    elif v > 1.0:
        if first:
            return 0x7FFF
        v = 1.0
    return cvtt(v * NORM_D) & 0xFFFF


def _bind_new(w, mdl, mesh, comp, n, vertex, cur, ps, put):
    """a new vertex on bone 0 at full weight, there and in ENVD 0's list"""
    mem = w.mem
    s_, off, stride = comp[BLEND_INDEX]
    if not s_:
        return cur
    me = _mesh(mem, ps, mdl, mesh)
    stms = mem.u64(me + 0x08)
    row = u32(stride * n)
    put(mem.u64(stms + s_ * STMS + 0x10) + off + row, b"\0\xff\xff\xff")
    put(mem.u64(stms + comp[BLEND_WEIGHT][0] * STMS + 0x10) + row + comp[BLEND_WEIGHT][1], b"\xff")
    if mem.u32(me + 0x24) == 0:
        raise ModelError("its .obj goes into a mesh with bone numbers but no bones, "
                         "which crashes the original tool")
    cur, k = wrb.grow_envd(w, mdl, mesh, 0, 1, ps, cur)
    e = _envd(mem, ps, mdl, mesh, 0)
    mem.set_u8(mem.u64(e + 0x20) + k, 0xFF)
    mem.set_be16(mem.u64(e + 0x18) + k * 2, vertex)
    return cur


# a component catalog entry: name, +0x20 file offset, +0x24 vertices, +0x26
# triangles, +0x28 dwords per usage (16 of them), +0x38 bytes per vertex
# record, +0x3a flags and +0x4c..+0x5c the numbers :k scatters it by
ENTRY = 0x74


@wrb.handler(0x4240A0)
def open_components(w, path: str) -> bool:
    """
    FUN_004240a0: the component catalog at `path`, opened "rb+" and its
    table read, unless it's open already. "Already" only compares the
    first character of the two paths (a -1 length to FUN_00409990)
    """
    mem, heap = w.mem, w.heap
    raw = path.encode("latin-1")
    if compare(raw, mem.get(wrb.COMPONENTS_NAME, len(raw) + 1), -1) & 0xFFFF == 0:
        return True
    if mem.u8(wrb.COMPONENTS_OPEN):
        w.b.state.pop("components", None)
    mem.set_u8(wrb.COMPONENTS_OPEN, 0)
    if i32(mem.u32(wrb.COMPONENTS_COUNT)) > 0:
        heap.delete(mem.u64(wrb.COMPONENTS_TABLE))
        mem.set_u32(wrb.COMPONENTS_COUNT, 0)
    f = cfile.fopen(w.b, path, "rb+")
    if f is None:
        w.b.state.pop("components", None)
        w.say(f"Unable to open component catalog {path}\n")
        return False
    w.b.state["components"] = f
    mem.set_u8(wrb.COMPONENTS_OPEN, 1)
    f.readinto(mem, wrb.COMPONENTS_COUNT, 4, 1)
    count = i32(mem.u32(wrb.COMPONENTS_COUNT))
    if count == 0:
        w.say(f"No objects in component catalog {path}\n")
        w.b.state.pop("components", None)
        mem.set_u8(wrb.COMPONENTS_OPEN, 0)
        return False
    table = heap.new(i32(count * ENTRY))
    mem.set_u64(wrb.COMPONENTS_TABLE, table)
    if not table:
        raise ModelError("its component catalog's object count doesn't read")
    f.readinto(mem, table, ENTRY, count)
    if len(raw) >= 0x64:
        raise ModelError("its component catalog's path overruns the original tool's buffer")
    mem.strcpy(wrb.COMPONENTS_NAME, raw)
    return True


def _component(w, name: str) -> int:
    """where the catalog entry `name` names sits (case blind, 31 characters at most), -1 if nowhere"""
    mem = w.mem
    raw = name.encode("latin-1")
    table = mem.u64(wrb.COMPONENTS_TABLE)
    for k in range(i32(mem.u32(wrb.COMPONENTS_COUNT))):
        entry = table + k * ENTRY
        if compare(mem.get(entry, ENTRY), raw, -31, True) & 0xFFFF == 0:
            return k * ENTRY
    return -1


@wrb.handler(0x424220)
def insert_component(w, mdl, mesh, sx, sy, sz, rx, ry, rz, ox, oy, oz, name, path, quad, cur, ps=None):
    """
    FUN_00424220 (:S): a catalogued component's vertices and triangles
    appended to a mesh, its positions turned (y, then z, then x), scaled
    and moved; every other usage copied raw from its record. "BoundingBox"
    instead fits the selection box to the mesh (:e,9999)
    """
    mem, heap = w.mem, w.heap
    ps = w.ps if ps is None else ps
    if not valid(w, mdl, mesh, "Insert Component", ps):
        return cur
    if compare(name.encode("latin-1"), b"BoundingBox\0", 0) & 0xFFFF == 0:
        return wrb.call(w, 0x412D70, mdl, mesh, cur)
    if not open_components(w, path):
        return cur
    at = _component(w, name)
    if at < 0:
        w.say(f"Unable to locate component {name} in {path}\n")
        return cur
    f = w.b.state.get("components")
    if f is None:
        # found in a table whose file has since failed to open: fseek(NULL)
        raise ModelError("its recipe reads a component catalog that's no longer open")

    def entry():
        return mem.u64(wrb.COMPONENTS_TABLE) + at

    me = _mesh(mem, ps, mdl, mesh)
    verts0, faces0 = mem.be16(mem.u64(me) + 0x38), mem.be16(mem.u64(me) + 0x3A)
    kind = mem.be32(mem.u64(_pos_stream(mem, me) + 0x08) + 4)
    cur = wrb.grow_verts(w, mdl, mesh, mem.u16(entry() + 0x24), ps, cur)
    size = mem.u16(entry() + 0x38)
    record = heap.new(size)
    # where each usage sits in a record: position first, 4 bytes over
    offs, run = [], 0
    for k in range(16):
        dwords = mem.u8(entry() + 0x28 + k)
        offs.append(run if dwords else -1)
        if dwords:
            run += dwords * 4 + (4 if k == 0 else 0)
    f.seek(i32(mem.u32(entry() + 0x20)))
    n = verts0
    for _ in range(mem.u16(entry() + 0x24)):
        if not record:
            raise ModelError("its component catalog has an empty vertex record, "
                             "which crashes the original tool")
        f.readinto(mem, record, size, 1)
        me = _mesh(mem, ps, mdl, mesh)
        s = 1
        while s < mem.u32(me + 0x20):
            st = mem.u64(me + 0x08) + s * STMS
            row = u32(mem.be16(mem.u64(st) + 0x1A) * n)
            c = 0
            while c < mem.u32(st + 0x1C):
                comp = mem.u64(st + 0x08) + c * 0x10
                usage = mem.be16(comp + 0x0C)
                if usage >= 16:
                    raise ModelError("its mesh has a component the original tool reads garbage for")
                if offs[usage] < 0:
                    w.say(f"Component {name} missing usage {usage}\n")
                elif usage == POSITION:
                    p = mem.u64(st + 0x10) + u32(mem.be16(comp + 2) + row)
                    x, y, z = (mem.f32(record + q * 4) for q in range(3))
                    x, z = volume.rotate(x, z, ry)
                    x, y = volume.rotate(x, y, rz)
                    y, z = volume.rotate(y, z, rx)
                    y = f32(f32(y * sy) + oy)
                    z = f32(f32(z * sz) + oz)
                    x = f32(f32(x * sx) + ox)
                    for q, v in enumerate((x, y, z)):
                        comp_write(kind, q, mem, p, v)
                    if kind != 1:
                        mem.set_be16(p + 6, 1)
                else:
                    dst = i32(u32(mem.u32(st + 0x2C) + row + mem.be16(comp + 2)))
                    heap.copy(1, cur, dst, record, offs[usage], mem.u8(entry() + 0x28 + usage) << 2)
                c = (c + 1) & 0xFFFF
                st = mem.u64(_mesh(mem, ps, mdl, mesh) + 0x08) + s * STMS
            s = (s + 1) & 0xFFFF
            me = _mesh(mem, ps, mdl, mesh)
        n += 1
    heap.delete(record)
    tri = heap.new(6)
    cur = wrb.grow_faces(w, mdl, mesh, mem.u16(entry() + 0x26), cur, ps)
    k = 0
    while mem.u16(entry() + 0x26) > k:
        f.readinto(mem, tri, 3, 2)
        polys = mem.u64(mem.u64(_mesh(mem, ps, mdl, mesh) + 0x08) + 0x10) + faces0 * 6 + k * 6
        for q in range(3):
            mem.set_be16(polys + q * 2, mem.u16(tri + q * 2) + verts0)
        k += 1
    heap.delete(tri)
    return cur


PLACED = 0x45BD00        # -> 12 bytes a component :k has scattered: x, z, catalog index
PLACED_COUNT = 0x45BD08  # i32, kept across :k lines while flag 1 asks for it
SCATTER_BUSY = 0x45AD30  # u8 the scatter loop clears every try
POSES = 0x45A6C0         # 0x3c a pose, POSE_COUNT of them; nothing in the exe ever adds one
POSE_COUNT = 0x45A6AC
TWO32 = 2.3283064365386963e-10
DEG = 0.017453292519943295
FAR_AWAY = f32(99999.0)
NO_NEAR = f32(999999.0)


class MT19937:
    """std::mt19937 as libstdc++ has it: the exe seeds one from steady_clock at startup"""

    def __init__(self, seed: int):
        mt = [seed & 0xFFFFFFFF]
        for i in range(1, 624):
            mt.append((0x6C078965 * (mt[-1] ^ (mt[-1] >> 30)) + i) & 0xFFFFFFFF)
        self.mt, self.i = mt, 624

    def __call__(self) -> int:
        mt = self.mt
        if self.i >= 624:
            for k in range(624):
                y = (mt[k] & 0x80000000) | (mt[(k + 1) % 624] & 0x7FFFFFFF)
                mt[k] = mt[(k + 397) % 624] ^ (y >> 1) ^ (0x9908B0DF if y & 1 else 0)
            self.i = 0
        y = mt[self.i]
        self.i += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        return y ^ (y >> 18)


def _random(w) -> MT19937:
    """the build's one generator; a fixed seed in b.state["seed"] makes :k repeatable"""
    rng = w.b.state.get("random")
    if rng is None:
        seed = w.b.state.get("seed")
        rng = w.b.state["random"] = MT19937(time.monotonic_ns() if seed is None else seed)
    return rng


def _div(a: float, b: float) -> float:
    """IEEE division, the way SSE does it: nothing raises"""
    if b != 0.0:
        return a / b
    if a != a or a == 0.0:
        return math.nan
    return math.copysign(math.inf, a) * math.copysign(1.0, b)


def _sqrtf(v: float) -> float:
    return f32(math.sqrt(v)) if v >= 0.0 else (v if v != v else math.nan)


def _sinf(v: float) -> float:
    return f32(math.sin(v)) if math.isfinite(v) else math.nan


def _cosf(v: float) -> float:
    return f32(math.cos(v)) if math.isfinite(v) else math.nan


def _asin(v: float) -> float:
    return math.asin(v) if -1.0 <= v <= 1.0 else math.nan


def _powf(a: float, b: float) -> float:
    """mingw's powf: pow on doubles, rounded back, and C's answers where Python raises"""
    odd = float(b).is_integer() and int(b) % 2 == 1
    try:
        return f32(math.pow(a, b))
    except ValueError:
        return math.nan
    except ZeroDivisionError:
        return math.copysign(math.inf, a) if odd else math.inf
    except OverflowError:
        return -math.inf if a < 0.0 and odd else math.inf


@wrb.handler(0x423FA0)
def pose_value(w, mask: int, a: float, b: float, default: float) -> float:
    """
    FUN_00423fa0: the first pose whose flags take `mask` (and whose box
    holds (a, b), with 0x8000) as a clamped c*a^p + d*b^q + e + f; the
    default otherwise. Nothing ever adds a pose, so it's always the default
    """
    mem = w.mem
    for k in range(max(i32(mem.u32(POSE_COUNT)), 0)):
        at = POSES + k * 0x3C
        flags = i32(mem.u32(at + 0x38))
        if i32(flags & mask) <= 0:
            continue
        if flags & 0x8000 and not (a >= mem.f32(at + 0x24) and mem.f32(at + 0x28) >= a and
                                   b >= mem.f32(at + 0x2C) and mem.f32(at + 0x30) >= b):
            continue
        ta = f32(mem.f32(at + 0x0C) * _powf(a, mem.f32(at + 4)))
        tb = f32(_powf(b, mem.f32(at + 8)) * mem.f32(at + 0x10))
        v = f32(f32(f32(tb + ta) + mem.f32(at + 0x14)) + mem.f32(at + 0x18))
        mem.set_f32(at, v)
        lo, hi = mem.f32(at + 0x1C), mem.f32(at + 0x20)
        if lo > v or v > hi:
            v = lo if lo > v else hi
            mem.set_f32(at, v)
        return v
    return default


def _inside_tri(p, a, b, c) -> bool:
    """FUN_00423360: (x, z) of p within triangle abc, edges included"""
    d1 = f32(f32(f32(p[0] - b[0]) * f32(a[2] - b[2])) - f32(f32(a[0] - b[0]) * f32(p[2] - b[2])))
    d2 = f32(f32(f32(p[0] - c[0]) * f32(b[2] - c[2])) - f32(f32(b[0] - c[0]) * f32(p[2] - c[2])))
    d3 = f32(f32(f32(p[0] - a[0]) * f32(c[2] - a[2])) - f32(f32(c[0] - a[0]) * f32(p[2] - a[2])))
    out = True
    if not 0.0 > d1 and not 0.0 > d2:
        out = 0.0 > d3
    if d1 > 0.0 or d2 > 0.0:
        return not out
    return not (out and d3 > 0.0)


def _area(a, b, c) -> float:
    """FUN_00423460: Heron's area of abc on the xz plane"""
    ab = _sqrtf(f32(f32(f32(b[2] - a[2]) ** 2) + f32(f32(b[0] - a[0]) ** 2)))
    ac = _sqrtf(f32(f32(f32(c[2] - a[2]) ** 2) + f32(f32(c[0] - a[0]) ** 2)))
    bc = _sqrtf(f32(f32(f32(b[2] - c[2]) ** 2) + f32(f32(b[0] - c[0]) ** 2)))
    s = f32(f32(f32(ac + ab) + bc) * HALF)
    return _sqrtf(f32(f32(f32(f32(s - ab) * s) * f32(s - ac)) * f32(s - bc)))


@wrb.handler(0x423D70)
def surface(w, x: float, z: float, out: list) -> bool:
    """
    FUN_00423d70: the picked triangle (:n) under (x, z). Fills `out` (four
    floats, out[1] left as it was) with the triangle's plane x, y, z and
    the height there, interpolated off the corners' y
    """
    mem = w.mem
    out[0], out[2], out[3] = x, z, 0.0
    picks = mem.u64(wrb.PICKS)
    for k in range(i32(mem.u32(wrb.PICKS_COUNT))):
        t = [mem.f32(picks + k * 0x40 + q * 4) for q in range(16)]
        a, b, c = t[0:4], t[4:8], t[8:12]
        if not _inside_tri(out, a, b, c):
            continue
        whole = _area(a, b, c)
        wb = f32(_div(_area(a, out, c), whole))
        wc = f32(_div(_area(a, b, out), whole))
        wa = f32(_div(_area(out, b, c), whole))
        out[3] = f32(f32(f32(wa * t[1]) + f32(wb * t[5])) + f32(wc * t[9]))
        out[0], out[1], out[2] = t[12], t[13], t[14]
        return True
    return False


def _box(w, mdl, mesh, ps, verts, kind, stride, empty):
    """the mesh's vertex extent as (min x, max x, min y, max y, min z, max z), the way :k scans it"""
    mem = w.mem
    if not verts:
        return empty
    lo = hi = None
    at = 0
    for i in range(verts):
        p = mem.u64(_pos_stream(mem, _mesh(mem, ps, mdl, mesh)) + 0x10) + at
        v = [comp_read(kind, n, mem, p) for n in range(3)]
        if i == 0:
            lo, hi = list(v), list(v)
        else:
            # minss/maxss: the old value wins a NaN
            for n in range(3):
                lo[n] = v[n] if v[n] < lo[n] else lo[n]
                hi[n] = v[n] if v[n] > hi[n] else hi[n]
        at += stride
    return lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]


@wrb.handler(0x427820)
def scatter(w, mdl, mesh, count, flags, p5, p6, p7, p8, p9, nnames, path, names, cur, ps=None):
    """
    FUN_00427820 (:k): `count` components out of a catalog scattered over
    the surface :n picked, each one :S'd into the mesh at a random spot
    inside the selection's bounds, turned to the surface, spun and sized at
    random, kept apart from the ones before it. names[] are the catalog
    names to draw from (biased by p9); a component's own flags in the
    catalog decide whether the next ones cluster around it. Flag 1 keeps
    the placed list for the next :k, 4 allows clustering, 8 one size for
    both axes. The mesh's (and MDL's) bounding box grows to fit after
    """
    mem, heap = w.mem, w.heap
    ps = w.ps if ps is None else ps
    rand = _random(w)
    if not valid(w, mdl, mesh, "Insert Random Component", ps):
        return cur
    if not open_components(w, path):
        return cur
    count = i16(count)
    me = _mesh(mem, ps, mdl, mesh)
    verts = mem.be16(mem.u64(me) + 0x38)
    kind = mem.be32(mem.u64(_pos_stream(mem, me) + 0x08) + 4)
    stride = mem.be16(mem.u64(_pos_stream(mem, me)) + 0x1A)
    wide = (-FAR_AWAY, FAR_AWAY, -FAR_AWAY, FAR_AWAY, -FAR_AWAY, FAR_AWAY)
    box = wide if kind == 1 else _box(w, mdl, mesh, ps, verts, kind, stride, wide)
    minx, maxx, miny, maxy, minz, maxz = box

    # only the bone's position matters; its turn and scale come back unused
    pos = [0.0, 0.0, 0.0]
    bone = i32(mem.be32(mem.u64(mem.u64(ps + wrb.MINS) + mdl * 8) + 0x30))
    # FUN_00405020 gets the edited block's number where a model's belongs
    code, words = skeleton.bone(w.b, mem.u16(wrb.EDIT_BLOCK), bone)
    if words is not None:
        pos = list(struct.unpack("<3f", struct.pack("<3I", *words[0])))
    if i16(code) < 0:
        w.say(f"ERROR: {i16(code)} Unable to get joint parameters for MINS {mdl} Bone {bone}\n")

    m = mem.u64(ps + wrb.MDLS) + mdl * 0x40

    def floats(at):
        return [mem.be_f32(at + k * 4) for k in range(6)]

    mb = floats(mem.u64(m + 0x28) + 0x10)
    lb = floats(mem.u64(m + 0x20) + 0x30)
    eb = floats(mem.u64(me + 0x18) + 0x30)

    # how far a placed component's local offset may reach and still land in
    # the int16 range, per axis
    def limits(lo, hi, l_lo, l_hi):
        d = float(f32(hi - lo))
        span = float(f32(l_hi - l_lo))
        return (f32(_div((-1.0 - lo) * span, d) + l_lo), f32(l_hi + _div(span * (1.0 - hi), d)))

    lim_x = limits(minx, maxx, lb[0], lb[3])
    lim_y = limits(miny, maxy, lb[1], lb[4])
    lim_z = limits(minz, maxz, lb[2], lb[5])
    if kind == 1:
        sx = sy = sz = 1.0
        cx = cy = cz = 0.0
    else:
        sx = f32(f32(mb[3] - mb[0]) * HALF)
        cx = f32(mb[3] - sx)
        sy = f32(f32(mb[4] - mb[1]) * HALF)
        cy = f32(mb[4] - sy)
        sz = f32(f32(mb[5] - mb[2]) * HALF)
        cz = f32(mb[5] - sz)
    scale32 = f32(_div(float(f32(maxx - minx)) * 32.0, float(f32(lb[3] - lb[0]))))

    placed = i32(mem.u32(PLACED_COUNT))
    table = heap.new(i32((placed + count) * 12))
    heap.fill(table, 0, 0, i32((placed + count) * 12))
    if mem.u64(PLACED):
        heap.copy(1, table, 0, mem.u64(PLACED), 0, i32(placed * 12))
        heap.delete(mem.u64(PLACED))
    mem.set_u64(PLACED, table)

    out_cur = cur
    inserted = 0
    if count > 0:
        out_cur, inserted = _scatter(w, mdl, mesh, count, flags, p5, p6, p7, p8, p9, nnames, path,
                                     names, cur, ps, rand, kind, pos, (sx, sy, sz, cx, cy, cz),
                                     (lim_x, lim_y, lim_z), scale32)

    # the extent again, over what's there now, to grow the boxes it outgrew
    me = _mesh(mem, ps, mdl, mesh)
    verts = mem.be16(mem.u64(me) + 0x38)
    nx, xx, ny, xy, nz, xz = _box(w, mdl, mesh, ps, verts, kind, stride, wide)
    m = mem.u64(ps + wrb.MDLS) + mdl * 0x40
    trailer = mem.u64(_mesh(mem, ps, mdl, mesh) + 0x18)
    mdl_box = mem.u64(m + 0x20)

    def grow(off, v, beyond):
        mem.set_be_f32(trailer + off, v)
        if beyond:
            mem.set_be_f32(mdl_box + off, v)

    if minx > nx:
        v = f32(f32(nx * sx) + cx)
        grow(0x30, v, eb[0] > v)
    if xx > maxx:
        v = f32(f32(xx * sx) + cx)
        grow(0x3C, v, v > eb[3])
    if miny > ny:
        v = f32(f32(ny * sy) + cy)
        grow(0x34, v, eb[1] > v)
    if xy > maxy:
        v = f32(f32(sy * xy) + cy)
        grow(0x40, v, v > eb[4])
    if minz > nz:
        v = f32(f32(sz * nz) + cz)
        grow(0x38, v, eb[2] > v)
    if xz > maxz:
        v = f32(f32(sz * xz) + cz)
        grow(0x44, v, v > eb[5])
    if w.verbose or count != inserted:
        w.say(f"Total Objects Inserted: {inserted}/{count}\n")
    if not flags & 1:
        heap.delete(mem.u64(PLACED))
        mem.set_u64(PLACED, 0)
        mem.set_u32(PLACED_COUNT, 0)
    return out_cur


def _scatter(w, mdl, mesh, count, flags, p5, p6, p7, p8, p9, nnames, path, names, cur, ps, rand,
             kind, pos, frame, lims, scale32):
    """:k's placing loop, rand() call for rand() call; (block, objects inserted)"""
    mem = w.mem
    sx, sy, sz, cx, cy, cz = frame
    (llx, lhx), (lly, lhy), (llz, lhz) = lims
    lo_x, hi_x = mem.f32(volume.BOUNDS), mem.f32(volume.BOUNDS + 4)
    lo_z, hi_z = mem.f32(volume.BOUNDS + 0x10), mem.f32(volume.BOUNDS + 0x14)
    tries_max = i32(count * 10)
    pick, cluster = True, False
    comp = anchor = name_idx = 0
    inserted = 0
    out = [0.0, 0.0, 0.0, 0.0]

    def entry(k):
        return mem.u64(wrb.COMPONENTS_TABLE) + k * ENTRY

    def name_of():
        k = i16(name_idx)
        if not 0 <= k < len(names):
            raise ModelError("its recipe draws a component name past the end of its list")
        return names[k]

    def uniform():
        return rand() * TWO32

    def spread():
        # double(p5 * 0.5 + p6), the base every distance here scales
        return float(p5) * 0.5 + float(p6)

    while True:
        if cluster:
            c5c = mem.f32(entry(comp) + 0x5C)
            if c5c > uniform() or c5c == 0.0:
                cluster = False
                rand()
                pick = True
            elif 0.1 > uniform():
                pick = True
        else:
            rand()
            pick = True
        tries = 0
        while True:
            tries += 1
            mem.set_u8(SCATTER_BUSY, 0)
            placed = None
            if not cluster:
                u = f32(uniform())
                t = pose_value(w, 0x10, u, f32(1.0 - u), u)
                x = f32(lo_x + f32(f32(hi_x - lo_x) * t))
                u = f32(uniform())
                t = pose_value(w, 0x20, u, f32(1.0 - u), u)
                z = f32(lo_z + f32(f32(hi_z - lo_z) * t))
                if pick:
                    u = f32(uniform())
                    t = pose_value(w, 0x40, x, z, u)
                    name_idx = cvtt(f32(_powf(t, p9) * float(nnames & 0xFFFF)))
                name = name_of().encode("latin-1")
                comp = -1
                for k in range(i32(mem.u32(wrb.COMPONENTS_COUNT))):
                    if compare(name, mem.get(entry(k), ENTRY), -31, True) & 0xFFFF == 0:
                        comp = k
                        break
            else:
                span = float(i32(mem.u32(PLACED_COUNT) - anchor))
                r1, r2 = rand(), rand()
                angle = f32(r2 * TWO32 * 360.0 * DEG)
                base = spread() * float(mem.f32(entry(comp) + 0x4C)) if comp >= 0 else 0.0
                r3 = rand()
                k = i16(cvtt(r1 * TWO32 * span + float(anchor)))
                dist = f32((r3 * TWO32 * 0.3 + 1.01) * base * float(sx))
                e = mem.u64(PLACED) + k * 12
                x = f32(f32(_sinf(angle) * dist) + mem.f32(e))
                z = f32(f32(dist * _cosf(angle)) + mem.f32(e + 4))
                cluster = tries <= 9
            if comp < 0:
                w.say(f"Error: Unable to locate object {i16(name_idx)}/{nnames & 0xFFFF}: {name_of()}\n")
            else:
                placed = _place(w, comp, x, z, sx, sz, rand, spread)
            if placed is not None:
                x, z = placed
                ok, cluster, anchor = _land(w, comp, x, z, out, flags, cluster, anchor)
                if ok:
                    result = _commit(w, mdl, mesh, comp, x, z, out, flags, p5, p6, p7, p8, kind, pos,
                                     frame, lims, scale32, rand, name_of(), path, cur, ps)
                    if result is not None:
                        cur = result
                        inserted += 1
                        pick = False
                        if count > inserted:
                            break
                        return cur, inserted
            if tries_max <= tries:
                # it gives up on the lot, and counts that as one more in
                return cur, inserted + 1
            pick = False


def _place(w, comp, x, z, sx, sz, rand, spread):
    """
    the spot after the near-to rule (catalog flag 2: next to the closest
    placed component with flag 1) and the keep-apart test; None if it
    lands too close to one already placed
    """
    mem = w.mem
    table = mem.u64(wrb.COMPONENTS_TABLE)
    me = table + comp * ENTRY
    placed = i32(mem.u32(PLACED_COUNT))
    if placed <= 0:
        return x, z
    at = mem.u64(PLACED)
    if mem.u8(me + 0x3A) & 2:
        best, near = NO_NEAR, 0
        for i in range(placed):
            e = at + i * 12
            if mem.u8(table + i16(mem.u16(e + 8)) * ENTRY + 0x3A) & 1:
                d2 = f32(f32(f32(mem.f32(e) - x) ** 2) + f32(f32(mem.f32(e + 4) - z) ** 2))
                if best > d2:
                    near = i
                best = d2 if d2 < best else best
        if best != NO_NEAR:
            u = rand() * TWO32
            e = at + i16(near) * 12
            other = table + i16(mem.u16(e + 8)) * ENTRY
            reach = float(mem.f32(other + 0x54)) * u
            dist = f32(spread() * float(f32(sz + sx)) * 0.5 * reach)
            angle = f32(rand() * TWO32 * 6.283185307179586)
            x = f32(f32(_sinf(angle) * dist) + mem.f32(e))
            z = f32(f32(dist * _cosf(angle)) + mem.f32(e + 4))
    apart = spread() * float(f32(sz + sx)) * 0.5
    for i in range(placed):
        e = at + i * 12
        j = i16(mem.u16(e + 8))
        if j == comp:
            rad = float(mem.f32(me + 0x4C))
        else:
            other = table + j * ENTRY
            if not mem.u8(other + 0x3A) & 1 or mem.u8(me + 0x3A) & 1:
                mine, theirs = mem.f32(me + 0x50), mem.f32(other + 0x50)
                rad = float(mine if mine > theirs else theirs)
            else:
                rad = 0.0
        need = f32(rad * apart)
        dz = f32(f32(z - mem.f32(e + 4)) ** 2)
        dx = f32(f32(x - mem.f32(e)) ** 2)
        if need > _sqrtf(f32(dx + dz)):
            return None
    return x, z


def _land(w, comp, x, z, out, flags, cluster, anchor):
    """the spot is selected and over a picked triangle; (ok, cluster, anchor)"""
    mem = w.mem
    if mem.u16(volume.ACTIVE) != 0 and not volume.inside(w, x, 0.0, z, 5):
        return False, cluster, anchor
    if not surface(w, x, z, out):
        return False, cluster, anchor
    if not cluster:
        anchor = i16(mem.u32(PLACED_COUNT))
        if mem.u8(mem.u64(wrb.COMPONENTS_TABLE) + comp * ENTRY + 0x3A) & 4:
            cluster = bool((u32(flags) >> 2) & 1)
    return True, cluster, anchor


def _commit(w, mdl, mesh, comp, x, z, out, flags, p5, p6, p7, p8, kind, pos, frame, lims, scale32,
            rand, name, path, cur, ps):
    """sizes and turns drawn, the spot taken, and the component :S'd in; None if it won't fit"""
    mem = w.mem
    sx, sy, sz, cx, cy, cz = frame
    (llx, lhx), (lly, lhy), (llz, lhz) = lims
    me = mem.u64(wrb.COMPONENTS_TABLE) + comp * ENTRY
    placed = i32(mem.u32(PLACED_COUNT))
    e = mem.u64(PLACED) + placed * 12
    mem.set_u16(e + 8, comp)
    mem.set_f32(e, x)
    mem.set_f32(e + 4, z)
    height = out[3]
    t1 = pose_value(w, 1, x, z, 1.0)
    k = float(t1) * (rand() * TWO32)
    t4 = pose_value(w, 4, x, z, 1.0)
    size_a = f32(float(p5) * k + float(f32(t4 * p6)))
    size_b = size_a
    if not flags & 8:
        t2 = pose_value(w, 2, x, z, 1.0)
        k = rand() * TWO32 * float(t2)
        t8 = pose_value(w, 8, x, z, 1.0)
        size_b = f32(k * float(p7) + float(f32(t8 * p8)))
    ra = rand()
    lean = mem.f32(me + 0x58)
    rb = rand()
    tilt_x = _asin(_div(float(f32(-out[0])) * math.pi, 180.0))
    tilt_z = _asin(_div(float(f32(-out[2])) * math.pi, 180.0))
    rc = rand()
    lx, ly, lz = f32(x - pos[0]), f32(height - pos[1]), f32(z - pos[2])
    if kind != 1:
        if llx > lx or lx > lhx or lly > ly or ly > lhy or llz > lz or lz > lhz:
            return None
        lx = f32(_div(f32(lx - cx), sx))
        ly = f32(_div(f32(ly - cy), sy))
        lz = f32(_div(f32(lz - cz), sz))
    mem.set_u32(PLACED_COUNT, placed + 1)
    if mem.u8(me + 0x3A) & 8:
        out[:] = [0.0, 1.0, 0.0, 0.0]
    yaw = f32(rb * TWO32 * 360.0 * DEG)
    lean = f32(_div(ra * TWO32 * float(lean) * math.pi, 180.0))
    s, c = _sinf(yaw), _cosf(yaw)
    size_a = f32(size_a * scale32)
    rot_x = f32(tilt_x + float(f32(c * lean)))
    rot_z = f32(tilt_z + float(f32(s * lean)))
    size_b = f32(size_b * scale32)
    rot_y = f32(360.0 * (rc * TWO32) * DEG)
    return insert_component(w, mdl, mesh, size_a, size_b, size_a, rot_x, rot_y, rot_z, lx, ly, lz,
                            name, path, tuple(out), cur, ps)
