"""
Normals, tangents and the passes that tidy them up after a mesh has been
reshaped: the two recalcs (:G and :g), seam smoothing (:y), duplicate
vertex removal (:o) and the surface picker :k snaps to (:n).

Everything here is single precision SSE in the exe, so every op is rounded
to float32 on its own (`f`). Python's double math rounded once per op lands
on the same bits for + - * / and sqrt. The grouping of each sum was read
off the assembly, not the decompile, which gets a few of them wrong.

Vertex streams are found by their component usage codes: 0 position,
2 normal (4 bytes around 127), 8 uv (two halves), 0xd tangent (same bytes,
the 4th one the handedness), 0xf bone weights.
"""

import bisect
import math
import struct

from patcher.mods.hdmodels import meshnum, skeleton, state, volume, wrb
from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import f32 as f, i16, i32, u16, u32
from patcher.mods.hdmodels.meshnum import byte_to_float, comp_read, float_to_byte, half_to_float

# the NaN SSE and x87 make from nothing (0/0, sqrt(-1)): sign bit set
NAN = struct.unpack("<f", b"\x00\x00\xc0\xff")[0]
NORM = f(32767.0)


def _div(a: float, b: float) -> float:
    """divss, which doesn't raise on a zero"""
    try:
        return f(a / b)
    except ZeroDivisionError:
        if a != a:
            return a
        if a == 0.0:
            return NAN
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


def _sqrt(x: float) -> float:
    # sqrtss of a negative is NaN; the exe only pings errno about it
    if x != x:
        return x
    return f(math.sqrt(x)) if x >= 0.0 else NAN


def _acos(x: float) -> float:
    """mingw's acosf: x87 atan2(sqrt(1 - x*x), x), out of range is NaN"""
    if x != x:
        return x
    d = 1.0 - x * x
    if not d >= 0.0:
        return NAN
    return f(math.atan2(math.sqrt(d), x))


def _len2(x: float, y: float, z: float) -> float:
    return f(f(f(x * x) + f(y * y)) + f(z * z))


def _dot(a, b) -> float:
    return f(f(f(a[0] * b[0]) + f(a[1] * b[1])) + f(a[2] * b[2]))


def _degrees(angle: float) -> float:
    # cvtss2sd, mulsd 180, divsd pi: double the whole way
    return float(angle) * 180.0 / math.pi


def unit(x: float, y: float, z: float):
    """
    FUN_0040fb50 on values: (x, y, z, ok). Divided through by the length
    unless it's 0, then anything within 1e-7 of 0 or of +-1 snapped there
    (tested in double, so NaN slips through untouched)
    """
    r = _sqrt(_len2(x, y, z))
    ok = r != 0.0
    if ok:
        x, y, z = _div(x, r), _div(y, r), _div(z, r)
    out = []
    for v in (x, y, z):
        if v < 1e-07 and v > -1e-07:
            v = 0.0
        if v < -0.9999999:
            v = -1.0
        if v > 0.9999999:
            v = 1.0
        out.append(v)
    return out[0], out[1], out[2], ok


@wrb.handler(0x40FB50)
def normalize(w, at: int) -> bool:
    """FUN_0040fb50: the float[3] at `at` made unit length in place"""
    mem = w.mem
    old = [mem.f32(at + k * 4) for k in range(3)]
    *new, ok = unit(*old)
    for k, v in enumerate(new):
        # unit() hands back the very object it didn't change, and the exe
        # stores all three only when it divided, so an untouched NaN keeps its bits
        if ok or v is not old[k]:
            mem.set_f32(at + k * 4, v)
    return ok


def _angle(a, b) -> float:
    """FUN_00410110: the angle between two vectors, through |cos|, so 0 to 90 degrees"""
    d = _dot(a, b)
    if 0.0 > d:
        d = -d
    la = _sqrt(_len2(*a))
    lb = _sqrt(_len2(*b))
    return _acos(_div(d, f(lb * la)))


class _Mesh:
    """a mesh's streams as the edits here look them up through the parse"""

    def __init__(self, w, mdl: int, mesh: int, ps: int):
        mem = w.mem
        self.mem = mem
        self.me = w.mesh(mdl, mesh, ps)
        self.head = mem.u64(self.me)
        self.stms = mem.u64(self.me + 0x08)

    def verts(self) -> int:
        return self.mem.be16(self.head + 0x38)

    def faces(self) -> int:
        return self.mem.be16(self.head + 0x3A)

    def streams(self) -> int:
        return self.mem.u8(self.head + 0x32)

    def base(self, k: int) -> int:
        """stream k's vertex data"""
        return self.mem.u64(self.stms + k * 0x38 + 0x10)

    def stride(self, k: int) -> int:
        return self.mem.be16(self.mem.u64(self.stms + k * 0x38) + 0x1A)

    def components(self):
        """(stream, entry address, usage, offset, stride) for every component past the index stream"""
        mem = self.mem
        for k in range(1, self.streams()):
            s = self.stms + k * 0x38
            stride = self.stride(k)
            comps = mem.u64(s + 0x08)
            for j in range(max(i32(mem.u32(s + 0x1C)), 0)):
                e = comps + j * 0x10
                yield k, e, mem.be16(e + 0x0C), mem.be16(e + 0x02), stride

    def index(self, n: int) -> int:
        return self.mem.be16(self.base(0) + n * 2)

    def selection_point(self, v: int, stride: int):
        """the int16 position flags & 8 tests against the volumes, always out of stream 1"""
        mem = self.mem
        at = self.base(1) + i32(v * stride)
        return tuple(_div(float(i16(mem.be16(at + k * 2))), NORM) for k in range(3))


def _selected(w, x: float, y: float, z: float) -> bool:
    return w.mem.u16(volume.ACTIVE) == 0 or volume.inside(w, x, y, z, 7)


def _zero3(mem, at: int) -> bool:
    return mem.u8(at) == 0 and mem.u8(at + 1) == 0 and mem.u8(at + 2) == 0


def _put3(mem, at: int, x: float, y: float, z: float) -> None:
    mem.set_u8(at, float_to_byte(x))
    mem.set_u8(at + 1, float_to_byte(y))
    mem.set_u8(at + 2, float_to_byte(z))


def _get3(mem, at: int):
    return byte_to_float(mem.u8(at)), byte_to_float(mem.u8(at + 1)), byte_to_float(mem.u8(at + 2))


def _add3(acc: list, v) -> None:
    acc[0] = f(acc[0] + v[0])
    acc[1] = f(acc[1] + v[1])
    acc[2] = f(acc[2] + v[2])


def _sub(a, b):
    return f(a[0] - b[0]), f(a[1] - b[1]), f(a[2] - b[2])


def _arrays(*counts):
    # the exe sizes its scratch arrays off a signed short and dies on a negative one
    if any(n < 0 for n in counts):
        raise ModelError("its recipe recalculates a mesh too big for the original tool")


@wrb.handler(0x414390)
def recalc(w, mdl: int, mesh: int, first: int, last: int, flags: int, ps: int = None) -> None:
    """
    FUN_00414390 (:G): normals and tangents rebuilt from the triangles. A
    vertex normal is its faces' normals weighted by the corner angle; the
    tangent comes from the uv gradients. flags: 1 overwrite set ones too
    (else only all-zero ones get written), 2 leave tangents, 4 leave
    normals, 8 only inside the selection, 16 hush the warnings
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    m = _Mesh(w, mdl, mesh, ps)
    ok, first, last = meshnum.vertex_range(w, first, last, "Recalc mesh", m.verts())
    if not ok or not meshnum.valid(w, mdl, mesh, "Recalc mesh", ps):
        return
    faces, verts = i16(m.faces()), i16(m.verts())
    _arrays(faces, verts)
    loud = not flags & 0x10 or w.verbose

    pos = nrm = uv = tan = (0, 0, 0)
    kind = 0
    if m.streams() >= 2:
        for k, _, usage, off, stride in m.components():
            if usage == 0:
                pos = (k, off, stride)
                # the kind comes from stream 1's first component whatever stream holds positions
                kind = mem.be32(mem.u64(m.stms + 0x38 + 0x08) + 4)
            elif usage == 2:
                nrm = (k, off, stride)
            elif usage == 8:
                uv = (k, off, stride)
            elif usage == 0xD:
                tan = (k, off, stride)

    def position(v):
        at = m.base(pos[0]) + pos[1] + i32(pos[2] * v)
        return comp_read(kind, 0, mem, at), comp_read(kind, 1, mem, at), comp_read(kind, 2, mem, at)

    def texcoord(v):
        at = m.base(uv[0]) + uv[1] + i32(v * uv[2])
        return half_to_float(i16(mem.be16(at))), half_to_float(i16(mem.be16(at + 2)))

    points = [(0.0, 0.0, 0.0)] * verts
    normals = []
    sdir = [[0.0, 0.0, 0.0] for _ in range(verts)]
    tdir = [[0.0, 0.0, 0.0] for _ in range(verts)]
    corners = []
    for k in range(faces):
        a, b, c = (m.index(k * 3 + q) for q in range(3))
        corners.append((a, b, c))
        p0, p1, p2 = position(a), position(b), position(c)
        points[a], points[b], points[c] = p0, p1, p2
        (u0, v0), (u1, v1), (u2, v2) = texcoord(a), texcoord(b), texcoord(c)
        dx1, dy1, dz1 = _sub(p1, p0)
        dx2, dy2, dz2 = _sub(p2, p0)
        dv1, dv2 = f(v1 - v0), f(v2 - v0)
        # (the exe also sums a per-vertex uv/edge ratio here that nothing reads)
        nx, ny, nz, good = unit(f(f(dy1 * dz2) - f(dz1 * dy2)), f(f(dz1 * dx2) - f(dx1 * dz2)),
                                f(f(dx1 * dy2) - f(dy1 * dx2)))
        normals.append((nx, ny, nz))
        if not good and loud:
            w.say(f"Normalize face Normal Vector: {mdl}-{mesh}  Poly: {k}\n")
        du1, du2 = f(u1 - u0), f(u2 - u0)
        r = _div(1.0, f(f(dv2 * du1) - f(dv1 * du2)))
        s = (f(f(f(dx1 * dv2) - f(dv1 * dx2)) * r), f(f(f(dy1 * dv2) - f(dv1 * dy2)) * r),
             f(f(f(dv2 * dz1) - f(dv1 * dz2)) * r))
        t = (f(f(f(dx2 * du1) - f(dx1 * du2)) * r), f(f(f(dy2 * du1) - f(dy1 * du2)) * r),
             f(f(f(dz2 * du1) - f(du2 * dz1)) * r))
        for v in (a, b, c):
            _add3(sdir[v], s)
        for v in (a, b, c):
            _add3(tdir[v], t)

    # the exe walks every face for every vertex; the faces touching each
    # vertex, in face order, give the same sums without the n squared
    touching = [[] for _ in range(verts)]
    for k, (a, b, c) in enumerate(corners):
        touching[a].append(k)
        if b != a:
            touching[b].append(k)
        if c != a and c != b:
            touching[c].append(k)

    for v in range(verts):
        if not i32(first) <= v <= i32(last):
            continue
        acc = [0.0, 0.0, 0.0]
        count = 0.0
        for k in touching[v]:
            a, b, c = corners[k]
            if a == v:
                e1, e2 = _sub(points[b], points[a]), _sub(points[c], points[a])
            elif b == v:
                e1, e2 = _sub(points[a], points[b]), _sub(points[c], points[b])
            else:
                e1, e2 = _sub(points[b], points[c]), _sub(points[a], points[c])
            d = f(f(f(e2[0] * e1[0]) + f(e2[1] * e1[1])) + f(e2[2] * e1[2]))
            l1, l2 = _sqrt(_len2(*e1)), _sqrt(_len2(*e2))
            angle = _acos(_div(d, f(l2 * l1)))
            count = f(count + 1.0)
            n = normals[k]
            acc = [f(f(n[0] * angle) + acc[0]), f(f(n[1] * angle) + acc[1]), f(f(angle * n[2]) + acc[2])]
        if count == 0.0:
            if w.verbose:
                w.say(f"No polygons for mdl {mdl} mesh {mesh} vertex {v}\n")
            continue
        nx, ny, nz, good = unit(*acc)
        if not good and loud:
            w.say(f"Normalize Vertex Normal Vector: {mdl}-{mesh} {v}\n")
        n = (nx, ny, nz)
        s = sdir[v]
        d = _dot(n, s)
        tx, ty, tz, good = unit(f(s[0] - f(nx * d)), f(s[1] - f(ny * d)), f(s[2] - f(d * nz)))
        if not good and not flags & 0x10:
            w.say(f"Normalize Pre-Tangent Vector: {mdl}-{mesh} {v}\n")
        bx, by, bz, good = unit(f(f(ny * tz) - f(nz * ty)), f(f(nz * tx) - f(tz * nx)),
                                f(f(ty * nx) - f(ny * tx)))
        if not good and loud:
            w.say(f"Normalize Vertex Tangent Vector: {mdl}-{mesh} {v}\n")
        # handedness: which way n x sdir faces the summed tdir
        side = (f(f(ny * s[2]) - f(nz * s[1])), f(f(nz * s[0]) - f(s[2] * nx)), f(f(s[1] * nx) - f(ny * s[0])))
        if _dot(side, tdir[v]) > 0.0:
            handed = 1.0
        else:
            handed = -1.0
            bx, by, bz = -bx, -by, -bz

        if flags & 8 and not _selected(w, *m.selection_point(v, pos[2])):
            continue
        if not flags & 4:
            at = m.base(nrm[0]) + i32(v * nrm[2]) + nrm[1]
            if flags & 1 or _zero3(mem, at):
                _put3(mem, at, nx, ny, nz)
                mem.set_u8(at + 3, 0x7F)
                _reindex(m, at, corners, touching, verts)
        if not flags & 2:
            at = m.base(tan[0]) + i32(v * tan[2]) + tan[1]
            if flags & 1 or _zero3(mem, at):
                _put3(mem, at, bx, by, bz)
                mem.set_u8(at + 3, 0xFF if handed > 0.0 else 0)
                _reindex(m, at, corners, touching, verts)


def _reindex(m: _Mesh, at: int, corners: list, touching: list, verts: int) -> None:
    """
    A mesh without a normal or tangent component has the exe writing those
    bytes over the start of the index stream, which its vertex loop keeps
    re-reading. Follow the faces that moved, or give up where the exe would
    read past its arrays
    """
    start = m.base(0)
    end = start + len(corners) * 6
    if at + 4 <= start or at >= end:
        return
    for k in range((max(at, start) - start) // 6, (min(at + 4, end) - 1 - start) // 6 + 1):
        new = tuple(m.index(k * 3 + q) for q in range(3))
        if new == corners[k]:
            continue
        if max(new) >= verts:
            raise ModelError("its recipe recalculates a mesh with no normal or tangent slot, "
                             "which the original tool can't do")
        for v in set(corners[k]):
            touching[v].remove(k)
        for v in set(new):
            bisect.insort(touching[v], k)
        corners[k] = new


@wrb.handler(0x417F20)
def recalc_flat(w, mdl: int, mesh: int, first: int, last: int, flags: int, ps: int = None) -> None:
    """
    FUN_00417f20 (:g): the older recalc. Face normals summed unweighted,
    normals always overwritten, tangents only where zero unless flags & 1.
    flags & 4 keeps the normals the mesh has and only redoes tangents off
    them. Its flags & 8 tests vertex 0's position for every vertex, so the
    selection is all or nothing; kept
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    m = _Mesh(w, mdl, mesh, ps)
    ok, first, last = meshnum.vertex_range(w, first, last, "Recalc mesh2", m.verts())
    if not ok or not meshnum.valid(w, mdl, mesh, "Recalc mesh2", ps):
        return
    faces, verts = m.faces(), m.verts()

    pos = nrm = uv = tan = (0, 0, 0)
    kind = 0
    if m.streams() >= 2:
        for k, e, usage, off, stride in m.components():
            if usage == 0:
                pos = (k, off, stride)
                kind = mem.be32(e + 4)
            elif usage == 2:
                nrm = (k, off, stride)
            elif usage == 8:
                uv = (k, off, stride)
            elif usage == 0xD:
                tan = (k, off, stride)

    points, texcoords, normals = [], [], []
    summed = [0.0] * verts
    for v in range(verts):
        at = m.base(pos[0]) + pos[1] + v * pos[2]
        points.append((comp_read(kind, 0, mem, at), comp_read(kind, 1, mem, at), comp_read(kind, 2, mem, at)))
        at = m.base(uv[0]) + uv[1] + v * uv[2]
        # stored v, u: the first half lands in the second slot
        texcoords.append((half_to_float(i16(mem.be16(at + 2))), half_to_float(i16(mem.be16(at)))))
        if flags & 4:
            normals.append(list(_get3(mem, m.base(nrm[0]) + i32(v * nrm[2]) + nrm[1])))
        else:
            normals.append([0.0, 0.0, 0.0])
    sdir = [[0.0, 0.0, 0.0] for _ in range(verts)]
    tdir = [[0.0, 0.0, 0.0] for _ in range(verts)]

    for k in range(faces):
        a, b, c = (m.index(k * 3 + q) for q in range(3))
        pa, pb, pc = points[a], points[b], points[c]
        ex1, ey1, ez1 = _sub(pb, pa)
        ex2, ey2, ez2 = _sub(pc, pa)
        ta, tb, tc = texcoords[a], texcoords[b], texcoords[c]
        dub, duc = f(tb[0] - ta[0]), f(tc[0] - ta[0])
        dvb, dvc = f(tb[1] - ta[1]), f(tc[1] - ta[1])
        r = _div(1.0, f(f(dub * dvc) - f(duc * dvb)))
        nx, ny, nz, good = unit(f(f(ey1 * ez2) - f(ey2 * ez1)), f(f(ex2 * ez1) - f(ex1 * ez2)),
                                f(f(ex1 * ey2) - f(ex2 * ey1)))
        if not good and w.verbose:
            w.say(f"MDL {mdl} Mesh {mesh}: Normalize face vector {k} problem.\n")
            for v, p in ((a, pa), (b, pb), (c, pc)):
                w.say(f"  {v}: {p[0]:f}, {p[1]:f}, {p[2]:f}\n")
        if not flags & 4:
            for v in (a, b, c):
                _add3(normals[v], (nx, ny, nz))
                summed[v] = f(1.0 + summed[v])
        s = (f(f(f(ex1 * dvc) - f(ex2 * dvb)) * r), f(f(f(ey1 * dvc) - f(ey2 * dvb)) * r),
             f(f(f(dvc * ez1) - f(dvb * ez2)) * r))
        t = (f(f(f(ex2 * dub) - f(ex1 * duc)) * r), f(f(f(ey2 * dub) - f(ey1 * duc)) * r),
             f(f(f(ez2 * dub) - f(ez1 * duc)) * r))
        for v in (a, b, c):
            _add3(sdir[v], s)
        for v in (a, b, c):
            _add3(tdir[v], t)

    def wanted(v):
        if not i32(first) <= v <= i32(last):
            return False
        return not flags & 8 or _selected(w, *m.selection_point(0, 0))

    if not flags & 4:
        base = m.base(nrm[0])
        for v in range(verts):
            if not wanted(v):
                continue
            n = normals[v]
            n[0], n[1], n[2], good = unit(*n)
            if not good and w.verbose:
                w.say(f"MDL {mdl} Mesh {mesh}: Normalize Vertex {v} vector problem.  {summed[v]:f} values\n")
                w.say(f"  {n[0]:f}, {n[1]:f}, {n[2]:f}\n")
            _put3(mem, base + i32(v * nrm[2]) + nrm[1], *n)
    if flags & 2:
        return
    base = m.base(tan[0])
    for v in range(verts):
        if not wanted(v):
            continue
        n, s = normals[v], sdir[v]
        d = _dot(n, s)
        tx, ty, tz, good = unit(f(s[0] - f(n[0] * d)), f(s[1] - f(n[1] * d)), f(s[2] - f(d * n[2])))
        if not good and w.verbose:
            w.say(f"MDL {mdl} Mesh {mesh}: Normalize Tangent Vector: {v}\n")
        at = base + i32(v * tan[2]) + tan[1]
        if not (flags & 1 or _zero3(mem, at)):
            continue
        _put3(mem, at, tx, ty, tz)
        side = f(f(f(f(f(n[1] * tz) - f(n[2] * ty)) * tdir[v][0]) +
                   f(f(f(n[2] * tx) - f(tz * n[0])) * tdir[v][1])) +
                 f(f(f(ty * n[0]) - f(n[1] * tx)) * tdir[v][2]))
        mem.set_u8(at + 3, 0 if side > 0.0 else 0xFF)


class _Seam:
    """one mesh's stream 1 the way :y reads it: a 6 byte position key at +0, normal bytes at +8"""

    def __init__(self, w, mdl: int, mesh: int, ps: int, kind: int):
        mem = w.mem
        m = _Mesh(w, mdl, mesh, ps)
        self.w, self.mem, self.kind = w, mem, kind
        self.count = m.verts()
        self.stride = m.stride(1)
        self.base = m.base(1)
        self.keys = {}

    def at(self, v: int) -> int:
        return self.base + i32(v * self.stride)

    def key(self, v: int) -> bytes:
        return self.mem.get(self.at(v), 6)

    def picked(self, v: int) -> bool:
        if self.w.mem.u16(volume.ACTIVE) == 0:
            return True
        at = self.at(v)
        mem, kind = self.mem, self.kind
        return volume.inside(self.w, comp_read(kind, 0, mem, at), comp_read(kind, 1, mem, at),
                             comp_read(kind, 2, mem, at), 7)

    def first(self, key: bytes, count: int, after: int = -1):
        """the first vertex past `after` and under `count` wearing `key`"""
        if self.stride < 11:
            # normal writes would land on the next vertex's key, so look every time
            return next((v for v in range(after + 1, count) if self.key(v) == key), None)
        if count not in self.keys:
            table = {}
            for v in range(count):
                table.setdefault(self.key(v), []).append(v)
            self.keys[count] = table
        hits = self.keys[count].get(key, ())
        k = bisect.bisect_right(hits, after)
        return hits[k] if k < len(hits) else None

    def normal(self, v: int):
        return _get3(self.mem, self.at(v) + 8)

    def put(self, v: int, n) -> None:
        _put3(self.mem, self.at(v) + 8, *n)


def _blend(*normals):
    x, y, z = normals[0]
    for n in normals[1:]:
        x, y, z = f(x + n[0]), f(y + n[1]), f(z + n[2])
    r = _sqrt(_len2(x, y, z))
    return _div(x, r), _div(y, r), _div(z, r)


def _smooth_within(s: _Seam, limit: float, main: bool, centre: bool) -> None:
    """a mesh's own duplicate vertices (same position, split for uvs) given one shared normal"""
    for i in range(s.count - 1):
        if not s.picked(i):
            continue
        j = s.first(s.key(i), s.count, i)
        if j is None:
            continue
        a, b = s.normal(i), s.normal(j)
        angle = _angle(a, b)
        if main:
            # the main mesh rounds its angle to float, and with `centre` lets x == 0 seams through
            close = limit > f(_degrees(angle)) or (
                s.mem.u8(s.at(i)) == 0 and s.mem.u8(s.at(i) + 1) == 0 and centre)
        else:
            close = float(limit) > _degrees(angle)
        if close:
            n = _blend(a, b)
            s.put(i, n)
            s.put(j, n)


@wrb.handler(0x4249B0)
def reconcile(w, mdl: int, mesh: int, mdl2: int, mesh2: int, mdl3: int, mesh3: int, centre: int,
              limit: float, ps: int = None) -> None:
    """
    FUN_004249b0 (:y): smooths normal seams. Vertices sharing a position
    within a mesh get the average of their normals when they're under
    `limit` degrees apart; with a second (and third) mesh named, every
    vertex of the first also averages with its twin in those, whatever the
    angle, and the second with the third. Positions match on stream 1's
    first 6 bytes, normals sit at byte 8: the layout the game's meshes use
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    if not meshnum.valid(w, mdl, mesh, "Reconcile Normals", ps):
        return
    second = i16(mdl2) >= 0 and i16(mesh2) >= 0
    if second and not meshnum.valid(w, i16(mdl2), i16(mesh2), "Reconcile Normals", ps):
        return
    third = i16(mdl3) >= 0 and i16(mesh3) >= 0
    if third and not meshnum.valid(w, i16(mdl3), i16(mesh3), "Reconcile Normals", ps):
        return

    main = _Mesh(w, mdl, mesh, ps)
    # the main mesh's position format reads every mesh's positions for the volume test
    kind = mem.be32(mem.u64(main.stms + 0x38 + 0x08) + 4)
    one = _Seam(w, mdl, mesh, ps, kind)
    two = _Seam(w, i16(mdl2), i16(mesh2), ps, kind) if second else None
    three = _Seam(w, i16(mdl3), i16(mesh3), ps, kind) if third else None
    limit = f(limit)

    _smooth_within(one, limit, True, bool(centre & 1))
    if second:
        _smooth_within(two, limit, False, False)
    if third:
        _smooth_within(three, limit, False, False)
    if not second:
        return

    for i in range(one.count):
        if not one.picked(i):
            continue
        key = one.key(i)
        j2 = two.first(key, two.count) if two.count else None
        j3 = three.first(key, three.count) if third and three.count else None
        if j2 is None and j3 is None:
            continue
        parts = [one.normal(i)]
        if j2 is not None:
            parts.append(two.normal(j2))
        if j3 is not None:
            parts.append(three.normal(j3))
        n = _blend(*parts)
        one.put(i, n)
        if j2 is not None:
            two.put(j2, n)
        if j3 is not None:
            three.put(j3, n)

    if not third:
        return
    # the exe walks the third mesh as far as the second mesh's count, not its own
    for i in range(two.count):
        if not two.picked(i):
            continue
        j = three.first(two.key(i), two.count)
        if j is not None:
            n = _blend(two.normal(i), three.normal(j))
            two.put(i, n)
            three.put(j, n)


# :o's component checks, the flag bit that skips each
DEDUPE_CODES = ((0xF, 2), (2, 4), (8, 8), (0xD, 0x10))


@wrb.handler(0x4267E0)
def dedupe(w, mdl: int, mesh: int, first: int, last: int, flags: int, blk: int, ps: int = None) -> None:
    """
    FUN_004267e0 (:o): vertices that are exact copies of an earlier one
    folded into it (faces repointed, the copy cut with :R's remover). A
    copy matches on stream 1's first 8 bytes (12 for float positions) and
    the weights, normal, uv and tangent words, flags 2/4/8/16 skipping
    those; flags & 1 only reports them. A negative MDL or mesh means all.

    The exe compares every vertex with every one below it. Slots below the
    one being looked at never move (a removal only shifts the ones above),
    so their keys are hashed once and only the candidates get looked at
    """
    mem = w.mem
    ps = w.ps if ps is None else ps
    mdl, mesh = u16(mdl), u16(mesh)
    if i16(mdl) < 0:
        mdl_end = u16(mem.u32(ps + wrb.MDL_COUNT))
        if mdl_end == 0:
            return
        mdl = 0
    else:
        mdl_end = u16(mdl + 1)
    mesh_start = mesh if i16(mesh) >= 0 else 0
    # where each component was last seen, kept from mesh to mesh like the exe's locals
    found = {code: [0, 0, 0] for code, _ in DEDUPE_CODES}
    tally = {"total": 0, "removed": 0}
    cur = mdl
    while True:
        if cur < mem.u32(ps + wrb.MDL_COUNT):
            entry = mem.u64(ps + wrb.MDLS) + cur * 0x40
            mesh_end = u16(mesh + 1)
            if i16(mesh) < 0:
                mesh_end = u16(mem.u32(entry + 0x30))
            if mesh_end:
                cm = mesh_start
                while True:
                    _dedupe_mesh(w, cur, cm, first, last, flags, blk, ps, found, tally)
                    cm = u16(cm + 1)
                    if mesh_end <= cm:
                        break
        else:
            w.say(f"Dup Vertices: Invalid MDL {cur}\n")
        cur += 1
        if mdl_end <= u16(cur):
            return


def _repoint(mem, idx: int, count: int, old: int, new: int) -> None:
    """every index naming vertex `old` (a u16 compared as an int) swapped for `new`"""
    if not 0 <= old <= 0xFFFF:
        return
    raw = mem.get(idx, count * 2)
    needle = old.to_bytes(2, "big")
    at = raw.find(needle)
    while at >= 0:
        if not at & 1:
            mem.set_be16(idx + at, new)
        at = raw.find(needle, at + 1)


def _dedupe_mesh(w, mdl, mesh, first, last, flags, blk, ps, found, tally) -> None:
    mem = w.mem
    if not meshnum.valid(w, mdl, mesh, "Dup Vertices", ps):
        return
    m = _Mesh(w, mdl, mesh, ps)
    ok, first, last = meshnum.vertex_range(w, first, last, "Dup Vertices", m.verts())
    if not ok:
        return
    first, last = i32(first), i32(last)
    stride = m.stride(1)
    kind = mem.be32(mem.u64(m.stms + 0x38 + 0x08) + 4)
    width = 12 if kind == 1 else 8
    seen = set()
    # a component this mesh hasn't got is read out of stream 1 until the
    # first removal, then out of whatever stream the last mesh had it in
    ptr = {code: m.base(1) for code, _ in DEDUPE_CODES}
    if mem.u32(m.me + 0x20) > 1:
        for k, _, usage, off, step in m.components():
            if usage in found:
                found[usage][:] = [k, off, step]
                ptr[usage] = m.base(k)
                seen.add(usage)
    checked = [code for code, bit in DEDUPE_CODES if not flags & bit]
    steady = all(code in seen for code in checked)
    streams = {code: found[code][0] for code, _ in DEDUPE_CODES}

    def key(v):
        parts = [mem.get(base1 + u32(v * stride), width)]
        for code in checked:
            _, off, step = found[code]
            parts.append(mem.get(ptr[code] + off + u32(v * step), 4))
        return tuple(parts)

    def table(upto):
        t = {}
        for v in range(first, upto):
            t.setdefault(key(v), []).append(v)
        return t

    base1 = m.base(1)
    i = last - 1
    hashed = None
    while i > first:
        at = base1 + u32(i * stride)
        if mem.u16(volume.ACTIVE) == 0 or volume.inside(
                w, comp_read(kind, 0, mem, at), comp_read(kind, 1, mem, at), comp_read(kind, 2, mem, at), 7):
            tally["total"] += 1
            if hashed is None:
                hashed = table(i)
            j = first
            mine = key(i)
            while j < i:
                hits = hashed.get(mine, ())
                k = bisect.bisect_left(hits, j)
                if k == len(hits) or hits[k] >= i:
                    break
                j = hits[k]
                if flags & 1:
                    w.say(f"Duplicate Vertices: {i} and {j}\n")
                else:
                    _repoint(mem, m.base(0), mem.be16(m.head + 0x3A) * 3, i, j)
                    wrb.call(w, 0x40D870, mdl, mesh, i, i, blk, ps=ps)
                    m = _Mesh(w, mdl, mesh, ps)
                    base1 = m.base(1)
                    for code in ptr:
                        ptr[code] = m.base(streams[code])
                    # slot i now holds the next vertex up
                    mine = key(i)
                    if not steady:
                        hashed = table(i)
                tally["removed"] += 1
                j += 1
        i -= 1

    if w.verbose:
        w.say(f"Duplication ({mdl},{mesh}): Total Vertexes: {tally['total']}  Removed: {tally['removed']}   ")
        if tally["total"] == 0:
            tally["total"] = 1
            over = 1.0
        else:
            over = float(f(float(tally["total"])))
        pct = i16(meshnum.cvtt(float(f(float(tally["removed"]))) * 1000.0 / over))
        w.say(f"{pct / 10.0:f} pct\n")


def _plane(a, b, c):
    """FUN_004235f0: a triangle's unit normal, and its distance off the origin along the raw one"""
    e1x, e1y, e1z = _sub(b, a)
    e2x, e2y, e2z = _sub(c, a)
    nx = f(f(e1y * e2z) - f(e1z * e2y))
    ny = f(f(e1z * e2x) - f(e2z * e1x))
    nz = f(f(e1x * e2y) - f(e1y * e2x))
    d = f(f(f(ny * a[1]) + f(nx * a[0])) + f(nz * a[2]))
    nx, ny, nz, _ = unit(nx, ny, nz)
    return nx, ny, nz, d


@wrb.handler(0x423700)
def pick_surface(w, kind: int, trb: int, mdl: int, mesh: int) -> None:
    """
    FUN_00423700 (:n): every triangle of another model's mesh with a corner
    inside the selection, posed onto its MINS bone, kept in the global pick
    list :k later snaps vertices onto. Each entry is 0x40 bytes: the three
    corners as float[4] and the triangle's plane
    """
    b, mem, heap = w.b, w.mem, w.heap
    trb = u16(trb)
    mdl, mesh = u16(mdl), u16(mesh)
    block = i16(state.find_block(b, trb, b"brw"))
    if block < 0:
        w.say("Error: Generate Surface: Unable to find wrb block.\n")
        return
    src = b.trbs[trb]
    if block + 1 >= src.count:
        raise ModelError("its recipe picks a surface from a mesh block its model hasn't got")
    size = i32(src.offset(block + 1) - src.offset(block))
    buf = heap.new(i32(size + 1))
    if not buf:
        raise ModelError("its recipe picks a surface from a mesh block that's empty")
    mem.write(buf, bytes(src.read(src.start + src.offset(block), size)))
    ps = mem.malloc(wrb.PARSE)
    mem.set_u32(ps + wrb.SIZE, size)
    wrb.parse(w, ps, buf)
    if meshnum.valid(w, mdl, mesh, "Gen Surface", ps):
        _pick_faces(w, trb, mdl, mesh, ps)
    wrb.release(mem, ps)
    mem.free(ps)
    heap.delete(buf)


def _pick_faces(w, trb: int, mdl: int, mesh: int, ps: int) -> None:
    mem, heap = w.mem, w.heap
    m = _Mesh(w, mdl, mesh, ps)
    faces = m.faces()
    stride = m.stride(1)
    kind = mem.be32(mem.u64(m.stms + 0x38 + 0x08) + 4)
    # the MDL's bounding box, which int16 positions are scaled out to
    box_at = mem.u64(mem.u64(ps + wrb.MDLS) + mdl * 0x40 + 0x28)
    lo = [mem.be_f32(box_at + 0x10 + k * 4) for k in range(3)]
    hi = [mem.be_f32(box_at + 0x1C + k * 4) for k in range(3)]
    bone = i32(mem.be32(mem.u64(mem.u64(ps + wrb.MINS) + mdl * 8) + 0x30))
    code, words = skeleton.bone(w.b, trb, bone)
    offset = (0.0, 0.0, 0.0)
    if words is not None:
        offset = tuple(struct.unpack("<3f", struct.pack("<3I", *words[0])))
    if i16(code) < 0:
        w.say(f"ERROR: {i16(code)} Unable to get joint parameters for MINS {mdl} Bone {bone}\n")

    picked = 0
    for k in range(faces):
        corners = []
        inside = False
        for q in range(3):
            at = m.base(1) + i32(m.index(k * 3 + q) * stride)
            p = [comp_read(kind, n, mem, at) for n in range(3)]
            if kind != 1:
                p = [f((float(p[n]) + 1.0) * float(f(hi[n] - lo[n])) * 0.5 + float(lo[n])) for n in range(3)]
            p = [f(p[n] + offset[n]) for n in range(3)]
            corners.append(p)
            if _selected(w, *p):
                inside = True
        if not inside:
            continue
        picked += 1
        count, room = i32(mem.u32(wrb.PICKS_COUNT)), i32(mem.u32(wrb.PICKS_USED))
        if count >= room:
            room += 100
            mem.set_u32(wrb.PICKS_USED, room)
            grown = heap.new(i32(room << 6))
            if room > 100:
                heap.copy(1, grown, 0, mem.u64(wrb.PICKS), 0, i32(count << 6))
                heap.delete(mem.u64(wrb.PICKS))
            mem.set_u64(wrb.PICKS, grown)
        # the corners' 4th floats are stack the exe never writes; zero here
        entry = struct.pack("<16f", *corners[0], 0.0, *corners[1], 0.0, *corners[2], 0.0,
                            *_plane(*corners))
        scratch = mem.put(entry)
        heap.copy(1, mem.u64(wrb.PICKS), i32(count << 6), scratch, 0, 0x40)
        mem.free(scratch)
        mem.set_u32(wrb.PICKS_COUNT, count + 1)
    if w.verbose:
        w.say(f"GenerateSurface: trb:{trb}  MDL:{mdl}  Mesh:{mesh}  Count:{picked}\n")
