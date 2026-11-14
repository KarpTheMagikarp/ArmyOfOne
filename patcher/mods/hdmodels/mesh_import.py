"""
Meshes pulled in from a catalog: the HD Models Plus way of swapping a
character's low poly body for a sculpted one without shipping game data.

A catalog (FFXIII_Ultimate_Models.bin, an add-on's .dat) is a u32 count,
then 0x28 byte entries (a 0x20 byte name, then offset and count), and at
each offset `count` vertices of six floats: where a vertex of the original
mesh sits, and where it should go. The same data can come as a text file
of the same name instead (a count line, then x,z,y,x,z,y per line with the
y's negated), which is what the catalog was built from.

  :~  matches the mesh's vertices against the catalog's, splits the
      triangles between them that pass an angle and length test, then
      moves everything that matches (FUN_0041cc80)
  :T  adds a vertex halfway (or anywhere) along an edge, blending every
      component and splitting the triangles on that edge (FUN_0041b6a0)
  :J  just moves the matching vertices, :j part of the way (FUN_00420710)
"""

import bisect
import math
import struct

from patcher.mods.hdmodels import cfile, volume, wrb
from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import atof, atoi, f32, i16, i32, u16, u32
from patcher.mods.hdmodels.meshnum import (byte_to_float, comp_read, comp_write, cvtt,
                                           float_to_byte, float_to_half, half_to_float, valid)
from patcher.mods.hdmodels.skeleton import compare

CATALOG_NAME = 0x45BD10     # -> the open catalog's path, kept to skip reopening it
ENTRY = 0x28
NEAR = 0.00015              # a mesh vertex within this (L1) of a catalog one is a match
MOVE_NEAR = 0.0001          # and this close is close enough to move it
HALF = f32(0.5)
DEFAULT_CATALOG = ("FFXIII_Ultimate_Models.bin", "data\\FFXIII_Ultimate_Models.bin")
_REC = struct.Struct("<6f")


def open_catalog(w, path: str) -> None:
    """FUN_00410690: the catalog's table loaded, unless it's the one already open"""
    mem, b = w.mem, w.b
    named = mem.u64(CATALOG_NAME)
    if not named or not mem.u8(named) or mem.cstr(named) != path.encode("latin-1"):
        b.state.pop("catalog", None)
        if mem.u8(wrb.CATALOG_LOADED):
            if mem.u64(wrb.CATALOG_TABLE):
                mem.free(mem.u64(wrb.CATALOG_TABLE))
            mem.set_u64(wrb.CATALOG_TABLE, 0)
        mem.set_u8(wrb.CATALOG_LOADED, 0)
        mem.set_u32(wrb.CATALOG_COUNT, 0)
        if named:
            if mem.u8(named):
                mem.free(named)
            mem.set_u64(CATALOG_NAME, 0)
    elif mem.u8(wrb.CATALOG_LOADED):
        return

    for name in (path, *DEFAULT_CATALOG):
        f = cfile.fopen(b, name, "rb")
        if f is not None:
            break
    else:
        if w.verbose:
            w.say(f"Catalog file {path} not found.\n")
        return
    text = name.encode("latin-1")
    named = mem.malloc(len(text) + 1)
    mem.strcpy(named, text)
    mem.set_u64(CATALOG_NAME, named)
    b.state["catalog"] = f
    f.readinto(mem, wrb.CATALOG_COUNT, 1, 4)
    count = mem.i32(wrb.CATALOG_COUNT)
    if count > 0:
        # the exe would allocate whatever it says; a table this much bigger
        # than the file is a broken catalog, not a big one
        if count * ENTRY > len(f.data) + (64 << 20):
            raise ModelError("its mesh catalog says it holds more meshes than it could")
        table = mem.malloc(count * ENTRY)
        mem.set_u64(wrb.CATALOG_TABLE, table)
        f.readinto(mem, table, ENTRY, count)
        mem.set_u8(wrb.CATALOG_LOADED, 1)
        return
    b.state.pop("catalog", None)
    mem.free(named)
    mem.set_u64(CATALOG_NAME, 0)


def _from_catalog(w, name: str):
    """the named mesh's vertices out of the open catalog, None if it isn't in there"""
    mem = w.mem
    if not mem.u8(wrb.CATALOG_LOADED):
        return None
    want = name.encode("latin-1")
    table = mem.u64(wrb.CATALOG_TABLE)
    for k in range(max(mem.i32(wrb.CATALOG_COUNT), 0)):
        entry = table + k * ENTRY
        # case blind, over the name and its NUL
        if i16(compare(want, mem.get(entry, len(want) + 1), 0, True)) != 0:
            continue
        count = mem.i32(entry + 0x24)
        # the buffer's sized off the low 16 bits, the read off all 32
        if not 0 <= count < 0x8000:
            raise ModelError("its mesh catalog has a mesh too big for the original tool")
        if w.verbose:
            w.say(f"Ultimate Vertex Count: {count}\n")
        f = w.b.state["catalog"]
        # fseek takes a 32 bit long, so past 2GB it fails and reads from wherever it was
        f.seek(mem.i32(entry + 0x20))
        got = f.read(count * 0x18).ljust(count * 0x18, b"\0")
        return [_REC.unpack_from(got, n * 0x18) for n in range(count)]
    return None


class _Text:
    """a vertex file opened "r": CRLF read as LF, ^Z the end, fgets and feof like msvcrt"""

    def __init__(self, data: bytes):
        self.data = data.split(b"\x1a", 1)[0].replace(b"\r\n", b"\n")
        self.at = 0
        self.eof = False
        self.buf = b""

    def gets(self) -> str:
        """fgets into a 0xff buffer that keeps its last line when there's nothing left"""
        if self.at >= len(self.data):
            self.eof = True
            return self.line()
        end = self.data.find(b"\n", self.at, self.at + 0xFE)
        stop = end + 1 if end >= 0 else min(self.at + 0xFE, len(self.data))
        # only a read that ran out before its newline or its 254 chars sees the end
        if end < 0 and stop - self.at < 0xFE:
            self.eof = True
        self.buf = self.data[self.at:stop]
        self.at = stop
        return self.line()

    def line(self) -> str:
        text = self.buf.split(b"\0", 1)[0]
        if 1 <= len(text) <= 0xFF and text.endswith(b"\n"):
            text = text[:-1]
        return text.decode("latin-1")


def _fields(line: str):
    """FUN_00409ab0 six times: comma fields, the missing ones empty"""
    parts = line.split(",")
    return [f32(atof(parts[k] if k < len(parts) else "")) for k in range(6)]


def _clamp(v: float) -> float:
    if -1.0 > v:
        return -1.0
    return 1.0 if v > 1.0 else v


def _from_text(w, name: str, clamped: bool):
    """the vertex file itself, when the catalog hasn't got it; None if it can't be opened"""
    f = cfile.fopen(w.b, name, "r")
    if f is None:
        w.say(f"Unable to open vertex file {name}\n")
        return None
    t = _Text(bytes(f.data))
    count = i16(atoi(t.gets()))
    if count < 0:
        raise ModelError("its vertex file has a negative vertex count")
    if clamped and w.verbose:
        w.say(f"Vertex File Count: {count}\n")
    out = []
    for _ in range(count):
        line = t.gets()
        if t.eof:
            w.say("Unexpected EOF in Vertex File.\n")
            break
        a, b, c, d, e, g = _fields(line)
        if clamped:
            out.append((_clamp(a), _clamp(-c), _clamp(b), _clamp(d), -_clamp(g), _clamp(e)))
        else:
            out.append((a, -c, b, d, -g, e))
    if not clamped and w.verbose:
        w.say(f"File Vertex Count: {count}\n")
    # whatever the file was short of stays as the heap had it
    out += [(0.0,) * 6] * (count - len(out))
    return out


def _vertices(w, name: str, catalog: str, clamped: bool):
    open_catalog(w, catalog)
    found = _from_catalog(w, name)
    return found if found is not None else _from_text(w, name, clamped)


def _l1(x: float, y: float, z: float, rec) -> float:
    """|dy| + |dx| + |dz| in single precision, summed in the exe's order"""
    return f32(f32(abs(f32(y - rec[1])) + abs(f32(x - rec[0]))) + abs(f32(z - rec[2])))


class _Near:
    """
    the catalog's vertices sorted on x, so finding the ones within a hair of
    a point doesn't mean testing all twenty thousand. The exe does test them
    all, in order, so what comes back is in catalog order
    """
    WINDOW = 0.001

    def __init__(self, recs):
        self.recs = recs
        # a NaN x never matches anything, and would scramble the sort
        order = sorted((k for k in range(len(recs)) if recs[k][0] == recs[k][0]),
                       key=lambda k: recs[k][0])
        self.keys = [recs[k][0] for k in order]
        self.order = order

    def within(self, x: float, y: float, z: float, test):
        lo = bisect.bisect_left(self.keys, x - self.WINDOW)
        hi = bisect.bisect_right(self.keys, x + self.WINDOW)
        hits = sorted(self.order[lo:hi])
        return [k for k in hits if test(_l1(x, y, z, self.recs[k]))]


@wrb.handler(0x420710)
def shift_to_catalog(w, mdl: int, mesh: int, scale: float, name: str, catalog: str, ps=None):
    """
    FUN_00420710 (:J, :j): every vertex sitting on a catalog vertex moved
    `scale` of the way to where the catalog puts it. Assumes the position is
    the first thing in the second stream, like the game's meshes have it
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    if not valid(w, mdl, mesh, "Shift Vertex Data", ps):
        return
    recs = _vertices(w, name, catalog, False)
    if recs is None:
        return
    me = w.mesh(mdl, mesh, ps)
    head = mem.u64(me)
    if mem.u8(head + 0x32) < 2:
        raise ModelError("its recipe moves the vertices of a mesh without a vertex stream")
    stms = mem.u64(me + 0x08) + 0x38
    data = mem.u64(stms + 0x10)
    verts = mem.be16(head + 0x38)
    stride = mem.be16(mem.u64(stms) + 0x1A)
    kind = mem.be32(mem.u64(stms + 0x08) + 4)
    moved = 0
    near = _Near(recs)
    for v in range(verts):
        at = data + v * stride
        x, y, z = (comp_read(kind, n, mem, at) for n in range(3))
        if mem.u16(wrb.LEGACY_VOLUMES) and not volume.inside(w, x, y, z, 7):
            continue
        last = -1
        for k in near.within(x, y, z, lambda d: d <= MOVE_NEAR):
            rec = recs[k]
            if last != -1 and w.verbose:
                w.say(f"Dup Data:{v} File:{last} / {k}\n")
            moved += 1
            for n, (was, to) in enumerate(((x, rec[3]), (y, rec[4]), (z, rec[5]))):
                comp_write(kind, n, mem, at, f32(f32(f32(to - was) * scale) + was))
            last = i16(k)
    if w.verbose:
        w.say(f"{i16(moved)} Verticies Moved.\n")


def _div(a: float, b: float) -> float:
    """a / b the way SSE does it: no exceptions, just inf and NaN"""
    if b == 0.0:
        if a == 0.0 or a != a:
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def _sqrt(v: float) -> float:
    return f32(math.sqrt(v)) if v >= 0.0 else math.nan


def _length(x: float, y: float, z: float) -> float:
    return _sqrt(f32(f32(f32(x * x) + f32(y * y)) + f32(z * z)))


# which edge of a triangle :T's new vertex lands on
XY, YZ, ZX = "xy", "yz", "zx"


def _split_edge(x: int, y: int, z: int, a: int, b: int):
    """the edge (a, b) in triangle (x, y, z), checked in the exe's order, which
    decides the degenerate cases (a == b, or a corner repeated)"""
    if x == a:
        if a == b or y == b:
            return XY
        if z != b:
            return None
        return YZ if y == a else ZX
    if y != a and z != a:
        return None
    if x == b:
        if y == a:
            return XY
        return YZ if b in (y, z) else ZX
    if y != b and z != b:
        return None
    if y == a:
        return XY if a == b else YZ
    return YZ


class _Blend:
    """
    the stack arrays the bone weight merge works in. They're set up once per
    :T, not per merge, so a second stream with weights sees the first one's
    leftovers, and the count keeps growing
    """

    def __init__(self):
        self.bone = [0xFF] * 8
        self.src = [0] * 8
        self.wt = [0.0] * 8
        self.n = 0
        # x*x + z*z of the two ends, from the position; ties between the 4th
        # and 5th strongest bone go to the end nearer the middle
        self.spread_a = 1.0
        self.spread_b = 0.0

    def add(self, bone: int, src: int, weight: float) -> None:
        if self.n >= 8:
            raise ModelError("its recipe blends more bone weights than the original tool has room for")
        self.bone[self.n], self.src[self.n], self.wt[self.n] = bone, src, weight
        self.n = u16(self.n + 1)


@wrb.handler(0x41B6A0)
def insert_vertex(w, mdl: int, mesh: int, flags: int, a: int, b: int, t: float, uv_t: float,
                  cur: int, ps=None) -> int:
    """
    FUN_0041b6a0 (:T): a new vertex at the end of the mesh, `t` of the way
    from b to a (`uv_t` for the UVs), every component blended its own way.
    With flags & 1 every triangle on the edge a-b is split in two at it
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    head = mem.u64(w.mesh(mdl, mesh, ps))
    verts = mem.be16(head + 0x38)
    a, b = i16(a), i16(b)
    # negative counts back from the end, in 16 bits
    a = i16(verts + a) if a < 0 else a
    b = i16(verts + b) if b < 0 else b
    cur = wrb.grow_verts(w, mdl, mesh, 1, ps, cur)
    me = w.mesh(mdl, mesh, ps)
    new = i16(mem.be16(mem.u64(me) + 0x38) - 1)
    blend = _Blend()
    k = 1
    while k < mem.u8(mem.u64(me) + 0x32):
        s = mem.u64(me + 0x08) + k * 0x38
        data = mem.u64(s + 0x10)
        stride = mem.be16(mem.u64(s) + 0x1A)
        at_a, at_b, at_new = (u32(v * stride) for v in (a, b, new))
        _inside_block(w, ps, data, (at_a, at_b, at_new), stride)
        mem.memmove(data + at_new, data + at_b, stride)
        bones = weights = None
        c = 0
        while c < mem.u32(s + 0x1C):
            comp = mem.u64(s + 0x08) + c * 0x10
            usage, off, kind = mem.be16(comp + 0xC), mem.be16(comp + 2), mem.be32(comp + 4)
            if usage == 0:
                _blend_position(w, blend, kind, data + at_a, data + at_b, data + at_new, t)
            elif usage in (2, 0xD):
                _blend_normal(mem, data + at_a + off, data + at_b + off, data + at_new + off, t)
            elif usage == 3:
                for q in range(4):
                    x, y = mem.u8(data + at_a + off + q), mem.u8(data + at_b + off + q)
                    mem.set_u8(data + at_new + off + q, 0xFF if 0xFF in (x, y) else (x + y) >> 1)
            elif 8 <= usage <= 0xB:
                _blend_uv(mem, data + at_a + off, data + at_b + off, data + at_new + off, uv_t)
            elif usage in (0xE, 0xF):
                ends = tuple(data + v + off for v in (at_a, at_b, at_new))
                if usage == 0xF:
                    bones = ends
                else:
                    weights = ends
                if bones and weights:
                    cur = _blend_weights(w, mdl, mesh, blend, bones, weights, t, new, ps, cur)
                    me = w.mesh(mdl, mesh, ps)
                    s = mem.u64(me + 0x08) + k * 0x38
                    data = mem.u64(s + 0x10)
                    bones = weights = None
            c += 1
        k += 1

    if not flags & 1:
        return cur
    faces = mem.be16(mem.u64(me) + 0x3A)
    for f in range(faces):
        me = w.mesh(mdl, mesh, ps)
        poly = mem.u64(mem.u64(me + 0x08) + 0x10) + f * 6
        x, y, z = mem.be16(poly), mem.be16(poly + 2), mem.be16(poly + 4)
        edge = _split_edge(x, y, z, a, b)
        if edge is None:
            continue
        cur = wrb.grow_faces(w, mdl, mesh, 1, cur, ps)
        # -1 is the new vertex, counted back from the end
        x, y, z = i16(x), i16(y), i16(z)
        if edge == XY:
            wrb.edit_poly(w, mdl, mesh, x, y, z, x, -1, z)
            wrb.edit_poly(w, mdl, mesh, 0, 0, 0, -1, y, z)
        else:
            wrb.edit_poly(w, mdl, mesh, x, y, z, x, y, -1)
            wrb.edit_poly(w, mdl, mesh, 0, 0, 0, -1, *((z, x) if edge == YZ else (y, z)))
    return cur


def _inside_block(w, ps: int, data: int, offsets, stride: int) -> None:
    # the exe reads (and writes) wherever these land; past the block that's
    # someone else's memory, or nothing at all
    blk = w.mem.u64(ps)
    end = blk + w.mem.u32(ps + wrb.SIZE)
    if any(data + v + stride > end for v in offsets):
        raise ModelError("its recipe blends a vertex the mesh hasn't got")


def _blend_position(w, blend: _Blend, kind: int, pa: int, pb: int, pn: int, t: float) -> None:
    """position, always at the start of the vertex: b + (a - b) * t, in doubles"""
    mem = w.mem
    ax, ay, az = (comp_read(kind, n, mem, pa) for n in range(3))
    bx, by, bz = (comp_read(kind, n, mem, pb) for n in range(3))
    rest = 1.0 - t
    out = [bx * rest + f32(ax * t), by * rest + f32(ay * t), f32(az * t) + rest * bz]
    blend.spread_a = f32(f32(ax * ax) + f32(az * az))
    blend.spread_b = f32(f32(bx * bx) + f32(bz * bz))
    # a sign flip from blending two positives can only be a 16 bit overflow
    for n, (p, q) in enumerate(((ax, bx), (ay, by), (az, bz))):
        if q > 0.0 and p > 0.0 and i16(cvtt(out[n])) < 0:
            w.say(f"Error {n} +-\n")
    for n in range(3):
        comp_write(kind, n, mem, pn, f32(out[n]))


def _blend_normal(mem, pa: int, pb: int, pn: int, t: float) -> None:
    """byte normals (and tangents): each normalised, blended, normalised again"""
    sign = 1.0 if mem.u8(pa + 3) == mem.u8(pb + 3) else -1.0
    a = [byte_to_float(mem.u8(pa + q)) for q in range(3)]
    b = [byte_to_float(mem.u8(pb + q)) for q in range(3)]
    la, lb = _length(*a), _length(*b)
    rest = 1.0 - t
    n = [f32(f32(_div(f32(f32(a[q] * t) * sign), la)) + _div(b[q] * rest, lb)) for q in range(3)]
    ln = _length(*n)
    for q in range(3):
        mem.set_u8(pn + q, float_to_byte(f32(_div(n[q], ln))))
    mem.set_u8(pn + 3, mem.u8(pb + 3))


def _blend_uv(mem, pa: int, pb: int, pn: int, t: float) -> None:
    rest = 1.0 - t
    for q in (0, 2):
        ha, hb = half_to_float(mem.be16(pa + q)), half_to_float(mem.be16(pb + q))
        mem.set_be16(pn + q, float_to_half(f32(f32(ha * t) + hb * rest)))


def _blend_weights(w, mdl: int, mesh: int, blend: _Blend, bones, weights, t: float, new: int,
                   ps: int, cur: int) -> int:
    """
    the two ends' bones pooled (a's scaled by t, b's by 1 - t), the strongest
    four kept and scaled back to 255, and the new vertex bound to each one in
    that bone's ENVD
    """
    mem = w.mem
    (bone_a, bone_b, bone_new), (wt_a, wt_b, wt_new) = bones, weights
    for q in range(4):
        bone, weight = mem.u8(bone_a + q), mem.u8(wt_a + q)
        blend.bone[q], blend.wt[q] = 0xFF, 0.0
        if bone != 0xFF and weight:
            blend.add(bone, 1, f32(float(weight) * t))
    rest = 1.0 - t
    for q in range(4):
        bone, weight = mem.u8(bone_b + q), mem.u8(wt_b + q)
        hit = next((j for j in range(blend.n) if blend.bone[j] == bone), None)
        if hit is not None:
            blend.src[hit] = 3
            blend.wt[hit] = f32(weight * rest + blend.wt[hit])
        elif bone != 0xFF and weight:
            blend.add(bone, 2, f32(weight * rest))

    # selection sort, strongest first, a tie keeping the earlier one
    for i in range(blend.n):
        best = i
        for m in range(i + 1, blend.n):
            if blend.wt[m] > blend.wt[best]:
                best = m
        if best != i:
            for arr in (blend.bone, blend.wt, blend.src):
                arr[i], arr[best] = arr[best], arr[i]

    for q in range(4):
        mem.set_u8(bone_new + q, blend.bone[q])
    fourth = blend.wt[3]
    if blend.wt[3] == blend.wt[4] and blend.bone[4] != 0xFF and blend.bone[3] != blend.bone[4] \
            and blend.n > 4 and _fifth_wins(blend):
        blend.bone[3] = blend.bone[4]
        mem.set_u8(bone_new + 3, blend.bone[4])
        fourth = blend.wt[4]

    scale = f32(_div(255.2, f32(f32(f32(blend.wt[0] + blend.wt[1]) + blend.wt[2]) + fourth)))
    blend.wt[0:4] = [f32(blend.wt[0] * scale), f32(blend.wt[1] * scale), f32(blend.wt[2] * scale),
                     f32(scale * fourth)]
    blend.wt[7] = scale
    out = [cvtt(v + 0.45) & 0xFF for v in blend.wt[0:4]]
    total = sum(out)
    short = i16(0xFF - total)
    if short:
        # what rounding lost (or gained) goes on the strongest bone, the
        # second one taking one of it when there's a second
        if short > 1 and blend.n > 1:
            out[1] = (out[1] + 1) & 0xFF
            short = 0xFE - total
        out[0] = (out[0] + short) & 0xFF
    for q in range(4):
        mem.set_u8(wt_new + q, out[q])
    for q in range(4):
        if not out[q]:
            mem.set_u8(bone_new + q, 0xFF)

    for q in range(4):
        if not out[q]:
            continue
        bone = blend.bone[q]
        if bone >= mem.u32(w.mesh(mdl, mesh, ps) + 0x24):
            raise ModelError("its recipe blends a vertex onto a bone its mesh doesn't bind")
        cur, had = wrb.grow_envd(w, mdl, mesh, bone, 1, ps, cur)
        e = mem.u64(w.mesh(mdl, mesh, ps) + 0x10) + bone * 0x38
        mem.set_u8(mem.u64(e + 0x20) + had, out[q])
        mem.set_be16(mem.u64(e + 0x18) + had * 2, new)
    return cur


def _fifth_wins(blend: _Blend) -> bool:
    """FUN_0041b6a0's tie break between the 4th and 5th bone"""
    a, b = blend.spread_a, blend.spread_b
    src = blend.src[3]
    if a > b and src == 2:
        return True
    if b > a and src == 1:
        return True
    if a == b:
        return blend.bone[3] > blend.bone[4]
    return blend.src[4] == src and blend.bone[3] > blend.bone[4]


class _Point:
    """FUN_0041cc80's 0x5c byte vertex record, the parts of it anything reads"""
    __slots__ = ("vert", "a", "b", "t", "x", "y", "z")

    def __init__(self, vert, a, b, t, pos):
        self.vert, self.a, self.b, self.t = vert, a, b, t
        self.x, self.y, self.z = pos


def _probe(w, kind: int, at: int):
    """FUN_004171d0: a vertex's position, complained about and clamped to +-1"""
    x, y, z = (comp_read(kind, n, w.mem, at) for n in range(3))
    if x > 1.0 or y > 1.0 or z > 1.0:
        w.say(">1.0\n")
    if -1.0 > x or -1.0 > y or -1.0 > z:
        w.say("<-1.0\n")
    return _clamp(x), _clamp(y), _clamp(z)


def _gap(p: _Point, q: _Point) -> float:
    """FUN_00410440"""
    dx, dy, dz = f32(p.x - q.x), f32(p.y - q.y), f32(p.z - q.z)
    return _sqrt(f32(f32(f32(dx * dx) + f32(dy * dy)) + f32(dz * dz)))


def _position(w, me: int):
    """the last stream component with usage 0: (vertices at, offset, stride, format)"""
    mem = w.mem
    found = None
    for k in range(1, mem.u8(mem.u64(me) + 0x32)):
        s = mem.u64(me + 0x08) + k * 0x38
        stride = mem.be16(mem.u64(s) + 0x1A)
        for c in range(max(mem.i32(s + 0x1C), 0)):
            comp = mem.u64(s + 0x08) + c * 0x10
            if mem.be16(comp + 0xC) == 0:
                found = mem.u64(s + 0x10), mem.be16(comp + 2), stride, mem.be32(comp + 4)
    return found


@wrb.handler(0x41CC80)
def import_mesh(w, mdl: int, mesh: int, flags: int, lo: float, hi: float, name: str, catalog: str,
                cur: int, ps=None) -> int:
    """
    FUN_0041cc80 (:~): the catalog mesh `name` laid over this one. Every
    vertex sitting on one of the catalog's gets a record; each triangle with
    two or three of those has its edges between them split at the middle
    when they're at least `hi` long (one, two or all three, retriangulated
    to suit), then every record, the new middles included, that sits on a
    catalog vertex is moved to where the catalog says.

    `lo` is meant to be the least angle between the ends' normals, but the
    exe never fills the normals in: it reads leftover heap, which is all
    zeros here, giving NaN, which passes. `flags` is read and ignored
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    if w.verbose:
        w.say(f"Smooth: mdl:{mdl}  mesh{mesh}\n")
    if not valid(w, mdl, mesh, "Smooth", ps):
        return cur
    cat = _vertices(w, name, catalog, True)
    if cat is None:
        return cur
    me = w.mesh(mdl, mesh, ps)
    head = mem.u64(me)
    verts, faces = mem.be16(head + 0x38), mem.be16(head + 0x3A)
    pos = _position(w, me)
    if verts and pos is None:
        raise ModelError("its recipe imports over a mesh with no vertex positions")
    data, off, stride, kind = pos or (0, 0, 0, 0)

    near = _Near(cat)
    recs = []
    for v in range(verts):
        x, y, z = _probe(w, kind, data + off + v * stride)
        if mem.u16(volume.ACTIVE) and not volume.inside(w, x, y, z, 7):
            continue
        if near.within(x, y, z, lambda d: d < NEAR):
            recs.append(_Point(v, v, v, 0.0, (x, y, z)))
    matched = len(recs)
    if w.verbose:
        w.say(f"Matching vertices: {matched}\n")
    if not matched:
        return cur
    where = {}
    for n, p in enumerate(recs):
        where.setdefault(p.vert, n)

    def edge(p: int, q: int) -> bool:
        # FUN_004104a0 with the angle always NaN: only the length counts
        return not hi > _gap(recs[p], recs[q])

    def middle(p: int, q: int) -> int:
        """the record halfway along p-q: one an earlier triangle made, or a new vertex"""
        nonlocal cur
        vp, vq = recs[p].vert, recs[q].vert
        for n in range(matched, matched + max(i16(len(recs) - matched), 0)):
            m = recs[n]
            if (m.a, m.b) == (vp, vq) and abs(f32(m.t - HALF)) < 0.001:
                return n
            if (m.a, m.b) == (vq, vp) and abs(m.t - (1.0 - HALF)) < 0.001:
                return n
        if len(recs) >= 0x7FFF:
            raise ModelError("its recipe splits a mesh past what the original tool can count")
        new = mem.be16(mem.u64(w.mesh(mdl, mesh, ps)) + 0x38)
        cur = insert_vertex(w, mdl, mesh, 0, i16(vp), i16(vq), HALF, HALF, cur, ps)
        # read back out of the second stream, wherever the position really is
        s1 = mem.u64(w.mesh(mdl, mesh, ps) + 0x08) + 0x38
        at = mem.u64(s1 + 0x10) + i32(stride * new) + off
        recs.append(_Point(new, vp, vq, HALF, _probe(w, kind, at)))
        where.setdefault(new, len(recs) - 1)
        return len(recs) - 1

    def mid(p: int, q: int) -> int:
        return recs[middle(p, q)].vert

    def poly(*corners) -> None:
        wrb.edit_poly(w, mdl, mesh, *(i16(v) for v in corners))

    def more(*corners) -> None:
        nonlocal cur
        cur = wrb.grow_faces(w, mdl, mesh, 1, cur, ps)
        poly(0, 0, 0, *corners)

    for f in range(faces):
        at = mem.u64(mem.u64(w.mesh(mdl, mesh, ps) + 0x08) + 0x10) + f * 6
        v0, v1, v2 = (mem.be16(at + q) for q in (0, 2, 4))
        if max(v0, v1, v2) >= 0x8000:
            raise ModelError("its recipe imports over a mesh too big for the original tool")
        c0, c1, c2 = (where.get(v, ~v) for v in (v0, v1, v2))
        if (c0 >= 0) + (c1 >= 0) + (c2 >= 0) < 2:
            continue
        ab = c0 >= 0 and c1 >= 0 and edge(c0, c1)
        bc = c1 >= 0 and c2 >= 0 and edge(c1, c2)
        ca = c2 >= 0 and c0 >= 0 and edge(c2, c0)
        if ab and bc and ca:
            m01, m12, m20 = mid(c0, c1), mid(c1, c2), mid(c2, c0)
            poly(v0, v1, v2, m01, m12, m20)
            more(v0, m01, m20)
            more(m01, v1, m12)
            more(m12, v2, m20)
        elif ab and ca:
            n20, n01 = middle(c2, c0), middle(c0, c1)
            m20, m01 = recs[n20].vert, recs[n01].vert
            poly(v0, v1, v2, v0, m01, m20)
            # the quad left over is cut along its shorter diagonal
            if _gap(recs[n01], recs[c2]) > _gap(recs[n20], recs[c1]):
                more(m20, v1, v2)
                more(m20, m01, v1)
            else:
                more(m01, v2, m20)
                more(m01, v1, v2)
        elif ab and bc:
            n01, n12 = middle(c0, c1), middle(c1, c2)
            m01, m12 = recs[n01].vert, recs[n12].vert
            poly(v0, v1, v2, v1, m12, m01)
            if _gap(recs[n12], recs[c0]) > _gap(recs[n01], recs[c2]):
                more(m01, v2, v0)
                more(m01, m12, v2)
            else:
                more(m01, m12, v0)
                more(m12, v2, v0)
        elif bc and ca:
            n12, n20 = middle(c1, c2), middle(c2, c0)
            m12, m20 = recs[n12].vert, recs[n20].vert
            poly(v0, v1, v2, v2, m20, m12)
            if _gap(recs[n20], recs[c1]) > _gap(recs[n12], recs[c0]):
                more(m12, v0, v1)
                more(m12, m20, v0)
            else:
                more(m12, m20, v1)
                more(m20, v0, v1)
        elif ab:
            m = mid(c0, c1)
            poly(v0, v1, v2, v0, m, v2)
            more(m, v1, v2)
        elif bc:
            m = mid(c1, c2)
            poly(v0, v1, v2, v0, v1, m)
            more(m, v2, v0)
        elif ca:
            m = mid(c2, c0)
            poly(v0, v1, v2, v0, v1, m)
            more(m, v1, v2)

    s1 = mem.u64(w.mesh(mdl, mesh, ps) + 0x08) + 0x38
    data1, stride1 = mem.u64(s1 + 0x10), mem.be16(mem.u64(s1) + 0x1A)
    for p in recs:
        hits = near.within(p.x, p.y, p.z, lambda d: d < MOVE_NEAR)
        if hits:
            c = cat[hits[0]]
            at = data1 + i32(p.vert * stride1) + off
            for n in range(3):
                comp_write(kind, n, mem, at, c[3 + n])
    return cur
