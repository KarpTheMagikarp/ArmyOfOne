"""
The mesh edits that move vertices around instead of adding or removing
them: poke values straight into a vertex attribute, scale, rotate, bulge
limbs in or out, bake light into vertex colours, weld seams between two
meshes, plus the debug ones that draw the selection volumes as geometry or
hunt for a vertex by position.

Nearly all of them read a vertex's position out of stream 1 (comp 0 is
always the position there in the game's models), ask volume.selected
whether it's in the selection, and write back through meshnum's comp
helpers. The float math is the exe's to the instruction: single precision
ops round after every step, the double ones stay double, and the x87
trig wrappers come out as one rounding of the true value.
"""

import math

from patcher.mods.hdmodels import volume, wrb
from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import f32, i16, i32, u16, u32
from patcher.mods.hdmodels.meshnum import (byte_to_float, comp_read, comp_write, cvtt, float_to_byte,
                                           float_to_half, half_to_float, valid, vertex_range)

# SSE and x87 both hand back this NaN (sign bit set) for 0/0, sqrt(-1) and friends
NAN = -math.nan
NORM = f32(32767.0)
TOP = f32(255.0)
ONE = f32(1.0)
NINETY = f32(90.0)
RAD = 0.017453292519943295
SCALE_DOWN = 0x45A6A8   # f32, the :H measure pass leaves its smallest ratio here


def _div(a: float, b: float) -> float:
    try:
        return a / b
    except ZeroDivisionError:
        if a != a or a == 0.0:
            return NAN
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


def _fdiv(a: float, b: float) -> float:
    return f32(_div(a, b))


def _sqrt(x: float) -> float:
    if x != x:
        return x
    return math.sqrt(x) if x >= 0.0 else NAN


def _fsqrt(x: float) -> float:
    return f32(_sqrt(x))


def _acosf(x: float) -> float:
    # FUN_00437d10: fpatan(sqrt(1 - x*x), x) on the x87, one rounding to float
    if x != x or abs(x) > 1.0:
        return NAN
    return f32(math.acos(x))


def _trig(fn, x: float) -> float:
    return fn(x) if math.isfinite(x) else NAN


def _sin(x: float) -> float:
    return _trig(math.sin, x)


def _cos(x: float) -> float:
    return _trig(math.cos, x)


def _tanf(x: float) -> float:
    return f32(_trig(math.tan, x))


def _streams(w, me: int) -> int:
    return w.mem.u32(me + 0x20)


def _stream(w, me: int, k: int) -> int:
    """stream k's 0x38 byte entry in a mesh's parse"""
    if not 0 <= k < _streams(w, me):
        # the exe would read whatever follows the stream array
        raise ModelError("its recipe edits a vertex stream the mesh hasn't got")
    return w.mem.u64(me + 0x08) + k * 0x38


def _verts(w, me: int) -> int:
    return w.mem.be16(w.mem.u64(me) + 0x38)


def _stride(w, s: int) -> int:
    return w.mem.be16(w.mem.u64(s) + 0x1A)


def _data(w, s: int) -> int:
    return w.mem.u64(s + 0x10)


def _format(w, s: int) -> int:
    """comp 0's format word, the way most edits read it (1 means float32)"""
    return w.mem.be32(w.mem.u64(s + 0x08) + 4)


def _format_short(w, s: int) -> int:
    # :U and :W read only the top half of it, so a float32 stream reads as int16 there
    return i16(w.mem.be16(w.mem.u64(s + 0x08) + 4))


def _position(w, kind: int, at: int):
    mem = w.mem
    return comp_read(kind, 0, mem, at), comp_read(kind, 1, mem, at), comp_read(kind, 2, mem, at)


def _raw_position(mem, at: int):
    """the first three int16 of a vertex, whatever its stream's format says"""
    return tuple(i16(mem.be16(at + k * 2)) for k in range(3))


def _scaled(mem, at: int):
    return tuple(_fdiv(float(v), NORM) for v in _raw_position(mem, at))


def _vertex_loop(first: int, last: int):
    # the exe's loops compare signed, so a range that came out backwards does nothing
    return range(i32(first), i32(last) + 1)


@wrb.handler(0x412620)
def set_vertices(w, mdl, mesh, stream, first, last, kind, offset, x, y, z, ps=None):
    """
    FUN_00412620 (:V): x, y, z written into one attribute of a run of
    vertices. `kind` picks the encoding (0 normalised int16 with a w of 1,
    1/3 halves, 2 added onto halves, 4/5 bytes, 6/8 int16, 7/11 bytes, 9
    float32, 10 a colour multiply-add), plus 100 to only touch vertices in
    the selection
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh, stream, kind, offset = u16(mdl), u16(mesh), u16(stream), u16(kind), u16(offset)
    me = w.mesh(mdl, mesh, ps)
    ok, first, last = vertex_range(w, first, last, "Edit Vertex", _verts(w, me))
    if not ok or not valid(w, mdl, mesh, "Edit Vertex", ps):
        return
    enc = kind % 100
    pos = _stream(w, me, 1)
    fmt = _format(w, pos)
    pos_stride = _stride(w, pos)
    for i in _vertex_loop(first, last):
        if kind > 99:
            if not volume.selected(w, *_position(w, fmt, _data(w, pos) + i32(pos_stride * i)), 7):
                continue
        s = _stream(w, me, stream)
        at = _data(w, s) + i32(_stride(w, s) * i) + offset
        if enc == 0:
            for k, v in enumerate((x, y, z)):
                mem.set_be16(at + k * 2, cvtt(v * 32767.0))
            mem.set_be16(at + 6, 1)
        elif enc in (1, 3):
            mem.set_be16(at, float_to_half(x))
            mem.set_be16(at + 2, float_to_half(y))
        elif enc == 2:
            for k, v in enumerate((x, y)):
                was = half_to_float(mem.be16(at + k * 2))
                mem.set_be16(at + k * 2, float_to_half(f32(was + v)))
        elif enc == 4:
            mem.set_u8(at, cvtt(x * 255.1))
            mem.set_u8(at + 1, cvtt(y * 255.1))
        elif enc == 5:
            for k, v in enumerate((x, y, z)):
                mem.set_u8(at + k, float_to_byte(v))
        elif enc in (6, 8):
            mem.set_be16(at, cvtt(x))
            mem.set_be16(at + 2, cvtt(y))
            if enc == 8:
                mem.set_be16(at + 4, cvtt(z))
                mem.set_be16(at + 6, 1)
        elif enc == 7:
            mem.set_u8(at, cvtt(x))
            mem.set_u8(at + 1, cvtt(y))
        elif enc == 9:
            for k, v in enumerate((x, y, z)):
                mem.set_be_f32(at + k * 4, v)
        elif enc == 10:
            _tint(w, me, at, x, y, cvtt(z))
        elif enc == 11:
            for k, v in enumerate((x, y, z)):
                mem.set_u8(at + k, cvtt(v))
        else:
            w.say("Invalid vertex data type\n")


def _tint(w, me: int, at: int, scale: float, add: float, flags: int) -> None:
    """:V type 10: each of three colour bytes times scale plus add, or the same on 255 minus it"""
    mem = w.mem
    if flags & 2 and mem.u16(volume.ACTIVE):
        # tests vertex 0's position every time, not the one being tinted
        if not volume.inside(w, *_scaled(mem, _data(w, _stream(w, me, 1))), 7):
            return
    for k in range(3):
        b = mem.u8(at + k)
        if flags & 1:
            v = cvtt(f32(f32(float(b) * scale) + add))
        else:
            v = cvtt(f32(TOP - f32(f32(float(0xFF - b) * scale) + add)))
        v = i16(v)
        mem.set_u8(at + k, 0 if v < 0 else min(v, 0xFF))


# a box's corners as signs on its half sizes, then its twelve triangles
_CORNERS = ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))
_BOX = ((-1, -2, -3), (-1, -3, -4), (-5, -7, -6), (-5, -8, -7), (-5, -1, -4), (-5, -4, -8),
        (-8, -4, -3), (-8, -3, -7), (-7, -3, -2), (-7, -2, -6), (-6, -2, -1), (-6, -1, -5))


@wrb.handler(0x412D70)
def show_volumes(w, mdl, mesh, cur, ps=None):
    """
    FUN_00412d70 (:e,9999): every selection volume built as geometry on the
    end of a mesh (a box, or an octagonal cylinder for kind 2), so you can
    see where it sits. Flat volumes and the kind 3 cylinder are skipped
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    n = 0
    while n < i16(mem.u16(volume.COUNT)):
        at = volume.FIRST + n * volume.SIZE
        sx, sy, sz = mem.f32(at + 4), mem.f32(at + 8), mem.f32(at + 0xC)
        if (sx != 0.0) + (sy != 0.0) + (sz != 0.0) > 1:
            kind = i16(mem.u16(at))
            if -1 <= kind <= 1:
                cur = _box(w, mdl, mesh, n, sx, sy, sz, cur, ps)
            elif kind in (2, -2):
                cur = _cylinder(w, mdl, mesh, n, sx, sy, sz, cur, ps)
        n += 1
    return cur


def _corner(w, mdl, mesh, n, at, x, y, z, ps):
    px, py, pz = volume.place(w.mem, n, x, y, z)
    set_vertices(w, mdl, mesh, 1, at, at, 0, 0, px, py, pz, ps=ps)


def _box(w, mdl, mesh, n, sx, sy, sz, cur, ps):
    w.mesh(mdl, mesh, ps)
    cur = wrb.grow_verts(w, mdl, mesh, 8, ps, cur)
    for k, (a, b, c) in enumerate(_CORNERS):
        _corner(w, mdl, mesh, n, -1 - k, sx if a > 0 else -sx, sy if b > 0 else -sy,
                sz if c > 0 else -sz, ps)
    for tri in _BOX:
        cur = wrb.grow_faces(w, mdl, mesh, 1, cur, ps)
        wrb.edit_poly(w, mdl, mesh, 0, 0, 0, *tri)
    return cur


def _cylinder(w, mdl, mesh, n, sx, sy, sz, cur, ps):
    # vertex -1 caps the bottom and -10 the top, -2..-9 and -11..-18 go round them
    w.mesh(mdl, mesh, ps)
    cur = wrb.grow_verts(w, mdl, mesh, 18, ps, cur)
    _corner(w, mdl, mesh, n, -1, 0.0, 0.0, -sz, ps)
    _corner(w, mdl, mesh, n, -10, 0.0, 0.0, sz, ps)
    cos, sin = 1.0, 0.0
    for k in range(8):
        x, y = f32(sx * f32(cos)), f32(sy * f32(sin))
        _corner(w, mdl, mesh, n, -2 - k, x, y, -sz, ps)
        _corner(w, mdl, mesh, n, -11 - k, x, y, sz, ps)
        angle = float(k + 1) * 45.0 * math.pi / 180.0
        sin, cos = _sin(angle), _cos(angle)
    for k in range(1, 9):
        a, b, c, d = -2 - (k & 7), -1 - k, -11 - (k & 7), -10 - k
        for tri in ((-1, a, b), (-10, c, d), (a, d, b), (a, c, d)):
            cur = wrb.grow_faces(w, mdl, mesh, 1, cur, ps)
            wrb.edit_poly(w, mdl, mesh, 0, 0, 0, *tri)
    return cur


@wrb.handler(0x41B420)
def rescale(w, mdl, mesh, first, last, axes, scale, shift, ps=None):
    """FUN_0041b420 (:*): position times scale plus shift, on the axes `axes` picks (1 x, 2 y, 4 z)"""
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    me = w.mesh(mdl, mesh, ps)
    ok, first, last = vertex_range(w, first, last, "Rescale Mesh", _verts(w, me))
    if not ok or not valid(w, mdl, mesh, "Rescale Mesh", ps):
        return
    pos = _stream(w, me, 1)
    fmt = _format(w, pos)
    for i in _vertex_loop(first, last):
        at = _data(w, pos) + i32(_stride(w, pos) * i)
        p = _position(w, fmt, at)
        if not volume.selected(w, *p, 7):
            continue
        for k in range(3):
            if axes & (1 << k):
                comp_write(fmt, k, mem, at, f32(f32(p[k] * scale) + shift))


def _usages(w, me: int):
    """
    the streams and offsets of a mesh's position (usage 0), normal (2) and
    colour (3) components, as :u hunts for them: the last of each wins
    """
    mem = w.mem
    found = {0: None, 2: None, 3: None}
    k = 1
    while k < _streams(w, me):
        s = mem.u64(me + 0x08) + k * 0x38
        c = 0
        while c < mem.u32(s + 0x1C):
            entry = mem.u64(s + 0x08) + c * 0x10
            usage = mem.be16(entry + 0xC)
            if usage in found:
                found[usage] = (k, mem.be16(entry + 2), _stride(w, s), mem.be32(entry + 4))
            c += 1
        k = u16(k + 1)
    return found


@wrb.handler(0x41A8E0)
def shade(w, mdl, mesh, flags, first, last, lx, ly, lz, strength, shift, value, ps=None):
    """
    FUN_0041a8e0 (:u): vertex colours darkened by how far each normal turns
    from a light direction. `flags`: 1 start from `value` instead of the
    colour, 2 work on 255 minus it, 4/8 only ever raise/lower it, 0x10 only
    past 90 degrees, 0x20/0x40 the falloff (linear, sine, 1 - cosine, none),
    0x80 the alpha byte instead of rgb
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh, flags, value = u16(mdl), u16(mesh), u16(flags), u16(value)
    if not valid(w, mdl, mesh, "Shadowing", ps):
        return
    me = w.mesh(mdl, mesh, ps)
    ok, first, last = vertex_range(w, first, last, "Shadowing", _verts(w, me))
    if not ok:
        return
    strength = 0.0 if 0.0 > strength else (ONE if ONE < strength else strength)
    lo, hi = (3, 4) if flags & 0x80 else (0, 3)
    found = _usages(w, me) if _streams(w, me) > 1 else {}
    pos, nrm, col = found.get(0), found.get(2), found.get(3)
    if not (pos and nrm and col):
        w.say("Error: Shadowing: Unable to find usage parameter 0, 3 and 13 for modifying shadowing.")
        return
    # positions step by stream 1's stride, whichever stream they're really in
    pos_stride = _stride(w, _stream(w, me, 1))
    if i32(last) < i32(first):
        return
    mode = (flags >> 5) & 3
    add = float(i16(shift))
    light = _fsqrt(f32(f32(f32(lx * lx) + f32(ly * ly)) + f32(lz * lz)))
    for i in _vertex_loop(first, last):
        at = _data(w, _stream(w, me, pos[0])) + u32(pos_stride * i) + pos[1]
        if not volume.selected(w, *_position(w, pos[3], at), 7):
            continue
        at = _data(w, _stream(w, me, nrm[0])) + u32(nrm[2] * i) + nrm[1]
        nx, ny, nz = (byte_to_float(mem.u8(at + k)) for k in range(3))
        dot = f32(f32(f32(nx * lx) + f32(ny * ly)) + f32(nz * lz))
        length = _fsqrt(f32(f32(f32(nx * nx) + f32(ny * ny)) + f32(nz * nz)))
        angle = _acosf(_fdiv(dot, f32(light * length)))
        deg = f32(angle * 180.0 / math.pi)
        at = _data(w, _stream(w, me, col[0])) + u32(col[2] * i) + col[1]
        for k in range(lo, hi):
            was = mem.u8(at + k)
            v = (value if value <= 0xFF else 0xFF) if flags & 1 else was
            t = float(0xFF - v if flags & 2 else v)
            if flags & 0x10:
                if NINETY >= deg:
                    t = 0.0
                elif mode == 0:
                    t = f32(t * ((deg - 90.0) / 90.0 * strength))
                elif mode == 1:
                    t = f32(t * (strength * _sin((deg - 90.0) / 180.0 * math.pi)))
                elif mode == 2:
                    t = f32(t * ((1.0 - _cos((deg - 90.0) / 180.0 * math.pi)) * strength))
            elif mode == 0:
                t = f32(deg / 180.0 * strength * t)
            elif mode == 1:
                t = f32(t * _sin(deg * math.pi / 360.0))
            elif mode == 2:
                t = f32(t * (1.0 - _cos(deg * math.pi / 360.0)))
            t = f32(t + add)
            if t > TOP:
                out = 0 if flags & 2 else 0xFF
            else:
                out = 0 if 0.0 > t else cvtt(t)
                if flags & 2:
                    out = ~out
            out &= 0xFF
            if flags & 4 and out < was:
                out = was
            if flags & 8 and out > was:
                out = was
            mem.set_u8(at + k, out)


@wrb.handler(0x414040)
def copy_matching(w, mdl, mesh, stream, mdl2, mesh2, stream2, first, last, to, frm, ps=None):
    """
    FUN_00414040 (:W, "UV Copy"): for each vertex, the first vertex of
    another mesh whose stream starts with the same 12 bytes (same position)
    gives it 4 bytes, usually a UV, from `frm` into `to`
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh, stream, mdl2, mesh2, stream2 = (u16(v) for v in (mdl, mesh, stream, mdl2, mesh2, stream2))
    to, frm = u16(to), u16(frm)
    if not valid(w, mdl, mesh, "UV Copy 1", ps) or not valid(w, mdl2, mesh2, "UV Copy 2", ps):
        return
    me, other = w.mesh(mdl, mesh, ps), w.mesh(mdl2, mesh2, ps)
    ok, first, last = vertex_range(w, first, last, "UV Copy", _verts(w, me))
    if not ok:
        return
    src = _stream(w, other, stream2)
    src_stride = _stride(w, src)
    pos = _stream(w, me, 1)
    fmt = _format_short(w, pos)
    for i in _vertex_loop(first, last):
        if not volume.selected(w, *_position(w, fmt, _data(w, pos) + i32(_stride(w, pos) * i)), 7):
            continue
        s = _stream(w, me, stream)
        dst = _data(w, s) + i32(_stride(w, s) * i)
        key = mem.get(dst, 12)
        j, off = 0, 0
        while j < _verts(w, other):
            at = _data(w, src) + off
            if mem.get(at, 12) == key:
                mem.write(dst + to, mem.get(at + frm, 4))
                break
            off += src_stride
            j += 1


@wrb.handler(0x413DC0)
def project_uv(w, mdl, mesh, stream, first, last, offset, a, b, c, d, ps=None):
    """FUN_00413dc0 (:U, "UV Map"): a half float at `offset` set to a*x + b*y + c*z + d"""
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh, stream, offset = u16(mdl), u16(mesh), u16(stream), u16(offset)
    if not valid(w, mdl, mesh, "UV Map", ps):
        return
    me = w.mesh(mdl, mesh, ps)
    ok, first, last = vertex_range(w, first, last, "UV Map", _verts(w, me))
    if not ok:
        return
    pos = _stream(w, me, 1)
    fmt = _format_short(w, pos)
    for i in _vertex_loop(first, last):
        x, y, z = _position(w, fmt, _data(w, pos) + i32(_stride(w, pos) * i))
        if not volume.selected(w, x, y, z, 7):
            continue
        s = _stream(w, me, stream)
        u = f32(f32(f32(f32(y * b) + f32(x * a)) + f32(z * c)) + d)
        mem.set_be16(_data(w, s) + i32(_stride(w, s) * i) + offset, float_to_half(u))


@wrb.handler(0x422F60)
def transform(w, mdl, mesh, mode, first, last, cx, cy, cz, a, b, c, ps=None):
    """
    FUN_00422f60 (:s): positions about a pivot, mode 0 moved by (a, b, c), 1
    rotated by those degrees, 2 scaled by them. The exe treats the stored
    y and z swapped (y up), so the pivot's y and the second value land on
    comp 2 and its z and the third on comp 1
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    me = w.mesh(mdl, mesh, ps)
    ok, first, last = vertex_range(w, first, last, "Rotate / Scale", _verts(w, me))
    if not ok or not valid(w, mdl, mesh, "Rotate / Scale", ps):
        return
    mode &= 3
    pos = _stream(w, me, 1)
    stride = _stride(w, pos)
    fmt = _format(w, pos)
    if mode == 1:
        a, b, c = f32(a * RAD), f32(b * RAD), f32(c * RAD)
    step = i32(stride * i32(first))
    for _ in _vertex_loop(first, last):
        at = _data(w, pos) + step
        step += stride
        x, z, y = _position(w, fmt, at)
        if not volume.selected(w, x, y, z, 7):
            continue
        x, y, z = f32(x - cx), f32(y - cy), f32(z - cz)
        if mode == 1:
            y, z = volume.rotate(y, z, a)
            x, z = volume.rotate(x, z, b)
            x, y = volume.rotate(x, y, c)
        elif mode == 2:
            x, y, z = f32(x * a), f32(y * b), f32(z * c)
        elif mode == 0:
            x, y, z = f32(x + a), f32(y + b), f32(z + c)
        comp_write(fmt, 0, mem, at, f32(x + cx))
        comp_write(fmt, 1, mem, at, f32(z + cz))
        comp_write(fmt, 2, mem, at, f32(y + cy))


@wrb.handler(0x427400)
def find_vertex(w, mdl, mesh, x, y, z, ps=None):
    """FUN_00427400 (:z,0): prints the vertices at a position, or the closest one; changes nothing"""
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    if not valid(w, mdl, mesh, "Debug: FindVertex", ps):
        return
    me = w.mesh(mdl, mesh, ps)
    count = _verts(w, me)
    pos = _stream(w, me, 1)
    fmt = _format(w, pos)
    stride = _stride(w, pos)
    w.say("Searching: (%f,%f,%f)  MDL:%d  Mesh:%d  Verts:%d\n" % (x, y, z, mdl, mesh, count))
    best, closest, near = f32(10.0), -1, (0.0, 0.0, 0.0)
    if count == 0:
        best = 10.0
    matched = False
    for i in range(count):
        p = _position(w, fmt, _data(w, pos) + i * stride)
        diff = f32(f32(abs(f32(p[0] - x)) + abs(f32(p[1] - y))) + abs(f32(p[2] - z)))
        if best > diff:
            best, closest, near = diff, i, p
        if 0.0001 >= diff:
            w.say("Match %d, (%f,%f,%f)\n" % (i, *p))
            matched = True
    if not matched:
        w.say("Cloest: %d  (%f,%f,%f)  diff: %f)\n" % (closest, *near, best))


def _skin_slots(w, me: int):
    """(stream, bone index offset, weight offset) of a mesh's skinning, -1 where it has none"""
    mem = w.mem
    stream, bones, weights = 1, -1, -1
    k = 1
    while k < _streams(w, me):
        s = mem.u64(me + 0x08) + k * 0x38
        c = 0
        while c < i16(mem.u16(s + 0x1C)):
            entry = mem.u64(s + 0x08) + c * 0x10
            usage = mem.be16(entry + 0xC)
            if usage == 0xF:
                stream, bones = k, i16(mem.be16(entry + 2))
            elif usage == 0xE:
                weights = i16(mem.be16(entry + 2))
            c += 1
        k = u16(k + 1)
    return stream, bones, weights


@wrb.handler(0x411360)
def align(w, mdl, mesh, mdl2, mesh2, reach, ps=None):
    """
    FUN_00411360 (:a, "Align Vertex"): every vertex of the second mesh
    within `reach` of one in the first is snapped onto it and takes its
    bone weights, matched up by bone name. Closes the seams between parts
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh, mdl2, mesh2, reach = u16(mdl), u16(mesh), u16(mdl2), u16(mesh2), u16(reach)
    if not valid(w, mdl, mesh, "Align Vertex", ps) or not valid(w, mdl2, mesh2, "Align Vertex 2", ps):
        return
    me, other = w.mesh(mdl, mesh, ps), w.mesh(mdl2, mesh2, ps)
    count, count2 = _verts(w, me), _verts(w, other)
    skin = _skin_slots(w, me)
    skin2 = _skin_slots(w, other) if _streams(w, other) > 1 else (1, -1, -1)
    if min(skin[1], skin[2], skin2[1], skin2[2]) < 0:
        w.say("Bone Or Weighting not found.\n")
        return
    stride, stride2 = _stride(w, _stream(w, me, 1)), _stride(w, _stream(w, other, 1))
    welded = 0
    for i in range(count):
        p = _data(w, _stream(w, me, 1)) + i * stride
        if mem.u16(volume.ACTIVE) and not volume.inside(w, *_scaled(mem, p), 7):
            continue
        for j in range(count2):
            q = _data(w, _stream(w, other, 1)) + j * stride2
            if mem.u16(volume.ACTIVE) and not volume.inside(w, *_scaled(mem, q), 7):
                continue
            # the distance is taken on the raw words, byte order and all
            gap = sum(abs(i16(mem.u16(p + k) - mem.u16(q + k))) for k in (0, 2, 4))
            if reach < gap:
                continue
            mem.write(q, mem.get(p, 6))
            welded = u16(welded + 1)
            _take_weights(w, me, other, skin, skin2, i * stride, j * stride2)
    if w.verbose:
        w.say(f"Vertices Aligned: {welded}\n")


def _take_weights(w, me, other, skin, skin2, at, at2) -> None:
    mem = w.mem
    for b in range(4):
        dst = _data(w, _stream(w, me, skin[0])) + b + at
        src = _data(w, _stream(w, other, skin2[0])) + b + at2
        bone = mem.u8(src + skin2[1])
        if bone == 0xFF:
            # the weight goes in the bone index's stream, wherever it really lives
            mem.set_u8(dst + skin[1], 0xFF)
            mem.set_u8(dst + skin[2], 0)
            continue
        if bone >= mem.u32(other + 0x24):
            raise ModelError("its recipe welds a vertex bound to a bone its mesh doesn't list")
        name = mem.u64(mem.u64(other + 0x10) + bone * 0x38 + 0x10)
        for e in range(mem.u32(me + 0x24)):
            if wrb.same_string(mem, mem.u64(mem.u64(me + 0x10) + e * 0x38 + 0x10), name):
                mem.set_u8(dst + skin[1], e)
                mem.set_u8(dst + skin[2], mem.u8(src + skin2[2]))
                break
        else:
            w.say(f"{mem.cstr(name).decode('latin-1')} not found.\n")


@wrb.handler(0x422930)
def expand(w, mdl, mesh, flags, ax, ay, az, bx, by, bz, cx, cy, cz, slope, push, limit, ps=None):
    """
    FUN_00422930 (:H, "Expand/Contract"): vertices within `limit` degrees of
    the line from a towards b get pushed out from (or pulled in to) the
    centre c, past a floor that narrows with the angle. Flags 1/2/4 pick
    the axes, bits 3-8 the floor, 0x200/0x400 measure the floor first
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh, flags = u16(mdl), u16(mesh), u32(flags)
    if not valid(w, mdl, mesh, "Expand/Contract", ps):
        return
    limit = f32(limit / 57.29578)
    if flags & 7 == 0:
        flags |= 7
    me = w.mesh(mdl, mesh, ps)
    pos = _stream(w, me, 1)
    at = _data(w, pos)
    count, stride, fmt = _verts(w, me), _stride(w, pos), _format(w, pos)
    d0, d1, d2 = f32(bx - ax), f32(by - ay), f32(bz - az)
    span = _fsqrt(f32(f32(f32(d0 * d0) + f32(d1 * d1)) + f32(d2 * d2)))
    if flags & 0x200:
        mem.set_f32(SCALE_DOWN, 2.0)
    measure = flags & 0x600
    floor = f32((50 - ((flags >> 3) & 0x3F)) * 2 / 100.0)
    for _ in range(count):
        x, y, z = _position(w, fmt, at)
        here = at
        at += stride
        if not volume.selected(w, x, z, y, 7):
            continue
        e0, e1, e2 = f32(x - ax), f32(z - ay), f32(y - az)
        r = _fsqrt(f32(f32(f32(e0 * e0) + f32(e1 * e1)) + f32(e2 * e2)))
        dot = f32(f32(f32(e0 * d0) + f32(e1 * d1)) + f32(e2 * d2))
        if 0.0 > dot:
            dot = -dot
        angle = _acosf(_fdiv(dot, f32(span * r)))
        if measure:
            t = _tanf(angle)
            k = 1.0 + t * t
            if limit >= angle:
                v = f32(_div(r, _sqrt(k)))
                if mem.f32(SCALE_DOWN) > v:
                    mem.set_f32(SCALE_DOWN, v)
            continue
        if angle > limit:
            continue
        t = _tanf(angle)
        least = f32(floor * f32(_fsqrt(f32(f32(t * t) + ONE)) * mem.f32(SCALE_DOWN)))
        if not flags & 0x200 and least > r:
            continue
        s = _fdiv(f32(f32(least + f32(f32(r - least) * slope)) + push), r)
        if flags & 1:
            v = f32(f32(f32(x - cx) * s) + cx)
            # x never crosses 0, it stops on it
            if 0.0 > x:
                x = 0.0 if 0.0 < v else v
            else:
                x = 0.0 if 0.0 > v else v
        if flags & 2:
            z = f32(f32(f32(z - cy) * s) + cy)
        if flags & 4:
            y = f32(f32(f32(y - cz) * s) + cz)
        comp_write(fmt, 0, mem, here, x)
        comp_write(fmt, 1, mem, here, y)
        comp_write(fmt, 2, mem, here, z)


def _limb_axis(ax, ay, az, bx, by, bz):
    dx, dy, dz = f32(ax - bx), f32(ay - by), f32(az - bz)
    length = _fsqrt(f32(f32(f32(dx * dx) + f32(dy * dy)) + f32(dz * dz)))
    return length, (_fdiv(dx, length), _fdiv(dy, length), _fdiv(dz, length))


def _foot(x, y, z, ax, ay, az, u):
    """
    a vertex against the limb's axis: how far along it from a, the closest
    point on it, the offset out to the vertex. y and z come in swapped
    """
    t = f32(f32(f32(f32(x - ax) * u[0]) + f32(f32(z - ay) * u[1])) + f32(f32(y - az) * u[2]))
    px, py, pz = f32(f32(u[0] * t) + ax), f32(f32(u[1] * t) + ay), f32(f32(u[2] * t) + az)
    ex, ey, ez = f32(x - px), f32(z - py), f32(y - pz)
    return t, (px, py, pz), (ex, ey, ez)


def _keeps_x(mode, x, y, ex, top, bottom) -> bool:
    """the limb edits only move x when it can't cross the model's centre line"""
    if mode & 3 == 0:
        return True
    if mode & 1 and top > y:
        return True
    if mode & 2 and y > bottom:
        return True
    return (x > 0.0 and ex > 0.0) or (0.0 > x and 0.0 > ex)


@wrb.handler(0x421390)
def limb_girth(w, mdl, mesh, kind, ax, ay, az, bx, by, bz, girth, reach, before, after, ps=None):
    """
    FUN_00421390 (:L, "Arm/Leg Diameter"): vertices within `reach` of the
    axis a to b scaled out from it by `girth`, eased in over the stretch
    `kind` marks out (low 4 bits start, next 4 end, in 15ths, bits 8-9 how
    much at the start), fading out over `before` lengths behind a and
    `after` lengths past b. Bits 10-11 limit which side may move in x
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    if not valid(w, mdl, mesh, "Arm/Leg Diameter", ps):
        return
    kind = i16(kind)
    length, u = _limb_axis(ax, ay, az, bx, by, bz)
    mode = kind >> 10
    if kind & 0x3FF == 0:
        lo, hi, start, end = 0.0, length, girth, girth
    else:
        lo = f32((kind & 0xF) / 15.0 * length)
        hi = f32(((kind >> 4) & 0xF) / 15.0 * length)
        start, end = girth, f32(((kind >> 8) & 3) * 0.25 * (girth - 1.0) + 1.0)
        if lo > hi:
            lo, hi, start, end = hi, lo, end, girth
    me = w.mesh(mdl, mesh, ps)
    pos = _stream(w, me, 1)
    at = _data(w, pos)
    count, stride, fmt = _verts(w, me), _stride(w, pos), _format(w, pos)
    widest = 0.0
    behind = -before
    for i in range(count):
        x, y, z = _position(w, fmt, at)
        here = at
        at += stride
        if not volume.selected(w, x, z, y, 7):
            continue
        t, (px, py, pz), (ex, ey, ez) = _foot(x, y, z, ax, ay, az, u)
        r = _fsqrt(f32(f32(f32(ex * ex) + f32(ey * ey)) + f32(ez * ez)))
        if not reach >= r:
            continue
        widest = r if r > widest else widest
        along = -t
        s = None
        if 0.0 >= t and length >= along:
            if lo > along:
                s = start
            elif along > hi:
                s = end
            else:
                f = _fdiv(f32(along - lo), f32(hi - lo))
                s = f32((1.0 - f) * start + f32(f * end))
        elif t > 0.0 and along > f32(behind * length):
            s = f32(_fdiv(_fdiv(along, before), length) * (start - 1.0) + start)
            if (ONE > start and start > s) or (start > ONE and s > start):
                w.say("dia < range: %f %f\n" % (start, s))
        elif along > length and f32(f32(after * length) + length) > along:
            s = f32(_fdiv(f32(_fdiv(along, length) - ONE), after) * (1.0 - end) + end)
            if (ONE > end and end > s) or (end > ONE and s > end):
                w.say("dia > range: %f %f\n" % (end, s))
        if s is None:
            if w.verbose:
                w.say("%d outside line lengths %f  max: %f  (%f,%f,%f)\n" % (i, along, length, x, z, y))
            continue
        if _keeps_x(mode, x, y, ex, az, bz):
            comp_write(fmt, 0, mem, here, f32(f32(ex * s) + px))
        comp_write(fmt, 2, mem, here, f32(f32(ey * s) + py))
        comp_write(fmt, 1, mem, here, f32(f32(ez * s) + pz))
    if w.verbose:
        w.say("Max Dia: %f\n" % widest)


@wrb.handler(0x420E50)
def xy_modify(w, mdl, mesh, flags, scale, offset, slope, cx, cz, cy, ps=None):
    """
    FUN_00420e50 (:Q, "XY Modify"): x, z (and y with flag 8) scaled about
    (cx, cz, cy) by `scale`, plus `offset` (instead of the position, with
    flag 0x10). With flag 4 the scale eases back to 1 beyond a slope off the
    first volume's top and bottom
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh, flags = u16(mdl), u16(mesh), u16(flags)
    first = volume.FIRST
    half, mid = mem.f32(first + 0xC), mem.f32(first + 0x24)
    if not valid(w, mdl, mesh, "XY Modify", ps):
        return
    me = w.mesh(mdl, mesh, ps)
    pos = _stream(w, me, 1)
    at = _data(w, pos)
    count, stride, fmt = _verts(w, me), _stride(w, pos), _format(w, pos)
    top, bottom = f32(mid + half), f32(mid - half)
    eases = (flags >> 2) & 1
    fixed = flags & 0x10
    for _ in range(count):
        x, y, z = _position(w, fmt, at)
        here = at
        at += stride
        if not volume.selected(w, x, z, y, 7):
            continue
        dy, below = f32(y - cy), f32(bottom - cy)
        if f32(below * slope) > dy:
            t = f32(f32(f32(cy - bottom) * slope) + dy)
        else:
            t = 0.0
            m = f32(f32(top - cy) * slope)
            if dy > m:
                t = f32(dy - m)
        eased = eases and t != 0.0
        reach = f32(top - cy) if y > cy else below

        def scaled(v, moved):
            if moved and eased:
                return f32((scale + _div(t, (1.0 - slope) * reach) * (1.0 - scale)) * v)
            return f32(v * scale)

        if flags & 1:
            v = offset if fixed else f32(f32(x - cx) + offset)
            comp_write(fmt, 0, mem, here, f32(scaled(v, x != cx) + cx))
        if flags & 2:
            v = offset if fixed else f32(f32(z - cz) + offset)
            comp_write(fmt, 2, mem, here, f32(scaled(v, z != cz) + cz))
        if flags & 8:
            v = offset if fixed else f32(dy + offset)
            comp_write(fmt, 1, mem, here, f32(scaled(v, y != cy) + cy))


@wrb.handler(0x421DF0)
def limb_facing(w, mdl, mesh, kind, ax, ay, az, bx, by, bz, girth, reach, before, after,
                nx, ny, nz, spread, ps=None):
    """
    FUN_00421df0 (:l, the other "Arm/Leg Diameter"): like :L, but only
    vertices facing within `spread` degrees of (nx, ny, nz) about the axis
    move, each axis by its own share of `girth` (kind's bits 0-5, in
    thirds), fading towards the edge of the spread from bits 6-9 (16ths)
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    if not valid(w, mdl, mesh, "Arm/Leg Diameter", ps):
        return
    kind = i16(kind)
    shares = (1.0, 1.0, 1.0)
    if kind & 0x3F:
        shares = tuple(float(f32(((kind >> s) & 3) / 3.0)) for s in (0, 2, 4))
    over = girth - 1.0
    k1, k2, k3 = f32(shares[0] * over + 1.0), f32(shares[1] * over + 1.0), f32(over * shares[2] + 1.0)
    fade = f32(((kind >> 6) & 0xF) * 0.0625 * spread)
    length, u = _limb_axis(ax, ay, az, bx, by, bz)
    facing = _fsqrt(f32(f32(f32(nx * nx) + f32(ny * ny)) + f32(nz * nz)))
    me = w.mesh(mdl, mesh, ps)
    pos = _stream(w, me, 1)
    at = _data(w, pos)
    count, stride, fmt = _verts(w, me), _stride(w, pos), _format(w, pos)
    widest = 0.0
    behind = -before
    for i in range(count):
        x, y, z = _position(w, fmt, at)
        here = at
        at += stride
        if not volume.selected(w, x, z, y, 7):
            continue
        t, (px, py, pz), (ex, ey, ez) = _foot(x, y, z, ax, ay, az, u)
        r = _fsqrt(f32(f32(f32(ex * ex) + f32(ey * ey)) + f32(ez * ez)))
        cosv = _fdiv(f32(f32(f32(ny * ey) + f32(nx * ex)) + f32(nz * ez)), f32(facing * r))
        angle = _acosf(cosv)
        if not reach >= r:
            continue
        deg = f32(angle * 180.0 / math.pi)
        if not spread >= deg:
            continue
        g1, g2, g3 = k1, k2, k3
        if deg > fade and fade > 0.0:
            q = 1.0 - _fdiv(f32(deg - fade), f32(spread - fade))
            g1, g2, g3 = (f32((k - 1.0) * q + 1.0) for k in (k1, k2, k3))
        widest = r if r > widest else widest
        along = -t
        if 0.0 >= t and length > along:
            out = (f32(f32(ex * g1) + px), f32(f32(ey * g2) + py), f32(f32(ez * g3) + pz))
        elif t > 0.0 and along > f32(behind * length):
            q = _fdiv(_fdiv(along, before), length)
            out = tuple(f32(p + (g + (g - 1.0) * q) * e)
                        for p, g, e in ((px, g1, ex), (py, g2, ey), (pz, g3, ez)))
        elif along > length and f32(f32(after * length) + length) > along:
            q = _fdiv(f32(_fdiv(along, length) - ONE), after)
            out = tuple(f32(p + (g + (1.0 - g) * q) * e)
                        for p, g, e in ((px, g1, ex), (py, g2, ey), (pz, g3, ez)))
        else:
            if w.verbose:
                w.say("%d outside line lengths %f  max:%f  (%f,%f,%f)\n" % (i, along, length, x, z, y))
            continue
        comp_write(fmt, 0, mem, here, out[0])
        comp_write(fmt, 2, mem, here, out[1])
        comp_write(fmt, 1, mem, here, out[2])
    if w.verbose:
        w.say("Max Dia: %f\n" % widest)
