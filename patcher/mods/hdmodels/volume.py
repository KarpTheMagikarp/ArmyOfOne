"""
Selection volumes: which vertices an edit touches.

A recipe sets up to a hundred volumes with `:e` (or a legacy recipe's
per-edit box), and every shaping edit asks, per vertex, whether it falls
inside. Each volume is 0x28 bytes in the exe's .data:

    +0x00  i16  kind: 0/1 box, 2 cylinder round z, 3 cylinder round y,
                negative to cut that shape out of the selection instead
    +0x04  f32  half sizes x, y, z
    +0x10  f32  rotation about x, y, z, stored negated (the test undoes it)
    +0x1c  f32  centre x, y, z

and in front of the first one, a bounding box of them all.

The point test (FUN_00410a50) is ported from its assembly rather than the
decompile, which drops the "axis not tested" escapes from the cut-out
pass. Float math is single precision with a rounding after every op, the
rotations through x87 fpatan/fsin/fcos the way mingw's libm does them.
"""

import math

from patcher.mods.hdmodels import wrb
from patcher.mods.hdmodels.cnum import f32, i16

ACTIVE = 0x44A180       # i16, nonzero when any volume selects
COUNT = 0x44A182        # i16
BOUNDS = 0x44A184       # min x, max x, min y, max y, min z, max z
FIRST = 0x44A19C
SIZE = 0x28
LIMIT = 100
LAST_AXIS = 0x45AD34    # f32, where a flattened test put the point back on its free axis
DEGREE = 0.017453292519943295
BIG = f32(10000.0)


def rotate(a: float, b: float, angle: float):
    """FUN_00410960: (a, b) turned by angle, through its polar form like the exe"""
    if (a == 0.0 and b == 0.0) or angle == 0.0:
        return a, b
    r = math.sqrt(f32(f32(b * b) + f32(a * a)))
    r = f32(r)
    t = f32(angle + f32(math.atan2(b, a)))
    return f32(f32(math.cos(t)) * r), f32(f32(math.sin(t)) * r)


def _get(mem, n: int):
    at = FIRST + n * SIZE
    f = mem.f32
    return (i16(mem.u16(at)), (f(at + 4), f(at + 8), f(at + 0xC)),
            (f(at + 0x10), f(at + 0x14), f(at + 0x18)), (f(at + 0x1C), f(at + 0x20), f(at + 0x24)))


def place(mem, n: int, x: float, y: float, z: float):
    """FUN_00411e90: a point in volume n's own frame, out into the model's"""
    _, _, (rx, ry, rz), (cx, cy, cz) = _get(mem, n)
    y, z = rotate(y, z, -rx)
    x, z = rotate(x, z, -ry)
    x, y = rotate(x, y, -rz)
    return f32(cx + x), f32(cy + y), f32(cz + z)


def _grow(mem, n: int, x: float, y: float, z: float) -> None:
    """FUN_00411f90: the bounding box stretched over one corner"""
    px, py, pz = place(mem, n, x, y, z)
    for k, v in enumerate((px, py, pz)):
        lo, hi = BOUNDS + k * 8, BOUNDS + k * 8 + 4
        if v < mem.f32(lo):
            mem.set_f32(lo, v)
        if mem.f32(hi) < v:
            mem.set_f32(hi, v)


@wrb.handler(0x412050)
def define(w, kind: int, sx: float, sy: float, sz: float, rx: float, ry: float, rz: float,
           cx: float, cy: float, cz: float) -> None:
    """FUN_00412050: a volume; a kind under 100 (either sign) starts the list over"""
    mem = w.mem
    kind = i16(kind)
    count = i16(mem.u16(COUNT))
    if i16(abs(kind)) <= 99:
        for n in range(max(count, 0)):
            at = FIRST + n * SIZE
            mem.set_u16(at, 0)
            for k in range(3):
                mem.set_f32(at + 4 + k * 4, 1.0)
            for off in range(0x10, 0x28, 4):
                mem.set_u32(at + off, 0)
        mem.set_u16(COUNT, 1)
        n = 0
        for k in range(3):
            mem.set_f32(BOUNDS + k * 8, BIG)
            mem.set_f32(BOUNDS + k * 8 + 4, -BIG)
    else:
        if count > 99:
            w.say(f"Error: Bounding box limit of {LIMIT} reached.\n")
            return
        n = count
        mem.set_u16(COUNT, count + 1)
    mem.set_u16(ACTIVE, 1)
    shape = i16(int(math.fmod(kind, 100)))
    at = FIRST + n * SIZE
    half = f32(0.5)
    hx, hy, hz = f32(sx * half), f32(sy * half), f32(sz * half)
    for k, v in enumerate((hx, hy, hz, -rx, -ry, -rz, cx, cy, cz)):
        mem.set_f32(at + 4 + k * 4, v)
    mem.set_u16(at, shape)
    if shape & 0xFFFF < 2:
        for x, y, z in ((-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, -hz), (-hx, hy, hz),
                        (hx, -hy, -hz), (hx, -hy, hz), (hx, hy, -hz), (hx, hy, hz)):
            _grow(mem, n, x, y, z)
        return
    if shape not in (2, 3):
        return
    cos, sin = 1.0, 0.0
    step = 0
    while True:
        step += 15
        a = f32(hx * cos)
        b = f32((hy if shape == 2 else hz) * sin)
        third = hz if shape == 2 else hy
        for bb in (b, -b):
            for aa, cc in ((a, -third), (a, third), (-a, -third), (-a, third)):
                if shape == 2:
                    _grow(mem, n, aa, bb, cc)
                else:
                    _grow(mem, n, aa, cc, bb)
        if step == 105:
            return
        angle = float(step) * DEGREE
        cos, sin = math.cos(angle), math.sin(angle)


def selected(w, x: float, y: float, z: float, mask: int) -> bool:
    """FUN_00411340: inside the selection, or there isn't one"""
    if w.mem.u16(ACTIVE) == 0:
        return True
    return inside(w, x, y, z, mask)


def inside(w, x: float, y: float, z: float, mask: int) -> bool:
    """
    FUN_00410a50: the point against every volume, cut-outs first. `mask`
    picks the axes tested (1 x, 2 y, 4 z); with one axis left out, the point
    is also put back on that axis and its coordinate left in LAST_AXIS
    """
    mem = w.mem
    count = i16(mem.u16(COUNT))
    fx = 1.0 if mask & 1 else 0.0
    fy = 1.0 if mask & 2 else 0.0
    fz = 1.0 if mask & 4 else 0.0
    if count <= 0:
        return False

    def local(n):
        kind, (sx, sy, sz), (rx, ry, rz), (cx, cy, cz) = _get(mem, n)
        lx, ly, lz = f32(x - cx), f32(y - cy), f32(z - cz)
        ly, lz = rotate(ly, lz, rx)
        lx, lz = rotate(lx, lz, ry)
        lx, ly = rotate(lx, ly, rz)
        return kind, (sx, sy, sz), (rx, ry, rz), (cx, cy, cz), lx, ly, lz

    def within(v, size):
        return -size <= v <= size

    def ellipse(a, sa, fa, b, sb, fb):
        return f32(f32(f32(f32(a * a) / f32(sa * sa)) * fa) +
                   f32(f32(f32(b * b) / f32(sb * sb)) * fb)) <= 1.0

    for n in range(count):
        kind, (sx, sy, sz), _, _, lx, ly, lz = local(n)
        if mask == 0:
            continue
        if kind == -2:
            if ellipse(lx, sx, fx, ly, sy, fy) and (within(lz, sz) or fz == 0.0):
                return False
        elif kind == -1:
            if (within(lx, sx) or fx == 0.0) and (within(ly, sy) or fy == 0.0) and \
                    (within(lz, sz) or fz == 0.0):
                return False
        elif kind == -3:
            if ellipse(lx, sx, fx, lz, sz, fz) and (within(ly, sy) or fy == 0.0):
                return False

    for n in range(count):
        kind, (sx, sy, sz), (rx, ry, rz), (cx, cy, cz), lx, ly, lz = local(n)
        if kind in (0, 1):
            if not ((within(lx, sx) or fx == 0.0) and (within(ly, sy) or fy == 0.0) and
                    (within(lz, sz) or fz == 0.0)):
                continue
            if fx != 0.0 and fy != 0.0 and fz == 0.0:
                lz = 0.0
                ly, lz = rotate(ly, lz, -rx)
                lx, lz = rotate(lx, lz, -ry)
                mem.set_f32(LAST_AXIS, f32(cz + lz))
                return True
            if fx != 0.0 and fy == 0.0 and fz != 0.0:
                ly = 0.0
                ly, lz = rotate(ly, lz, -rx)
                lx, ly = rotate(lx, ly, -rz)
                mem.set_f32(LAST_AXIS, f32(cy + ly))
                return True
            if fx == 0.0 and fy != 0.0 and fz != 0.0:
                lx = 0.0
                lx, lz = rotate(lx, lz, -ry)
                lx, ly = rotate(lx, ly, -rz)
                mem.set_f32(LAST_AXIS, f32(cx + lx))
                return True
            mem.set_f32(LAST_AXIS, 0.0)
            return True
        if kind == 2:
            if not ellipse(lx, sx, fx, ly, sy, fy):
                continue
            if not (within(lz, sz) or fz == 0.0):
                continue
            if fx != 0.0 and fy != 0.0 and fz == 0.0:
                lz = 0.0
                ly, lz = rotate(ly, lz, -rx)
                lx, lz = rotate(lx, lz, -ry)
                mem.set_f32(LAST_AXIS, f32(cz + lz))
            return True
        if kind == 3:
            if not ellipse(lx, sx, fx, lz, sz, fz):
                continue
            if not (within(ly, sy) or fy == 0.0):
                continue
            if fx != 0.0 and fy == 0.0 and fz != 0.0:
                ly = 0.0
                lz, ly = rotate(lz, ly, -rx)
                lx, ly = rotate(lx, ly, -rz)
                mem.set_f32(LAST_AXIS, f32(cy + ly))
            return True
    return False
