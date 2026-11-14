"""
The number formats a mesh's vertex streams hold and the checks every edit
runs on its arguments, the way FF XIII HD Models.exe has them.

Vertex components come as big endian float32, as half floats, as int16
normalised to 32767, or as a byte around 127. The conversions are kept
to the exe's own instruction order (single vs double precision, where it
truncates), since a different rounding moves a vertex by an ulp and the
model's bytes with it.
"""

import math
import struct

from patcher.mods.hdmodels.cnum import bswap16, f32, i16, i32, u32

_F = struct.Struct("<f")
NORM = f32(32767.0)


def cvtt(x: float) -> int:
    """cvttss2si / cvttsd2si into 32 bits: out of range (or NaN) is 0x80000000"""
    if x != x or not -2147483648.0 <= x < 2147483648.0:
        return -0x80000000
    return int(x)


def half_to_float(p: int) -> float:
    """FUN_00409780"""
    if i16(p) == 0:
        return 0.0
    value = f32(float(p & 0x3FF) * f32(0.0009765625))
    exp = (p & 0x7FFF) >> 10
    power = -14.0
    if exp:
        value = f32(value + 1.0)
        power = float(i16(exp - 15))
    value = f32(value * math.pow(2.0, power))
    return -value if i16(p) < 0 else value


def float_to_half(p: float) -> int:
    """FUN_00409820, its rounding and its out of range cases kept"""
    negative = p < 0.0
    v = -p if negative else p
    exp = 0
    if v < 1.0:
        while True:
            v = f32(v + v)
            exp -= 1
            if v >= 1.0 or exp == -14:
                break
    if v > 2.0:
        while True:
            prev = exp
            v = f32(v * 0.5)
            exp += 1
            if not (v > 2.0 and exp < 15):
                break
        mant = u32(cvtt((v - 1.0) * 1024.0 + 0.5))
        if mant < 0x400:
            out = cvtt(float(mant) + float(prev + 0x10) * 1024.0)
        else:
            out = 0x7BFF
            if exp != 15:
                out = cvtt(float(exp + 0x10) * 1024.0 + 0.0)
    else:
        mant = u32(cvtt((v - 1.0) * 1024.0 + 0.5))
        if mant > 0x3FF:
            out = cvtt(float(exp + 0x10) * 1024.0 + 0.0)
        else:
            out = cvtt(float(mant) + float(exp + 0xF) * 1024.0)
    return i32(out + 0x8000) if negative else out


def byte_to_float(p: int) -> float:
    """FUN_0040a010: a byte around 127 as -1 to 1"""
    d = float((p & 0xFF) - 0x7F)
    return f32(d * 0.0078125 if d >= 0 else d / 127.0)


def float_to_byte(p: float) -> int:
    """FUN_0040a040: back again, nudged half a step; 0 below -1, -1 above 1"""
    p = f32(p)
    if p > f32(0.00390625):
        p = f32(p + 0.00390625)
    elif p < 0.003937007874015748:
        p = f32(p - 0.003937007874015748)
    # NaN falls through every test, like comiss lets it
    if -1.0 > p:
        return 0
    if p > 1.0:
        return -1
    if 0.0 > p:
        return cvtt(f32(p * 127.0)) + 0x7F
    return cvtt(f32(p * 128.0)) + 0x7F


def comp_read(kind: int, n: int, mem, at: int) -> float:
    """FUN_004125d0: component n of a vertex, float32 when kind is 1, else int16"""
    if kind != 1:
        return f32(float(i16(bswap16(mem.u16(at + n * 2)))) / NORM)
    return mem.be_f32(at + n * 4)


def comp_write(kind: int, n: int, mem, at: int, v: float) -> None:
    """FUN_0041b3a0: and back, int16 clamped to +-32767"""
    if kind == 1:
        mem.set_be_f32(at + n * 4, v)
        return
    if -1.0 > v:
        q = -32767
    elif v <= 1.0 or v != v:
        q = i16(cvtt(f32(v * NORM)))
    else:
        q = 0x7FFF
    mem.set_be16(at + n * 2, q)


def valid(w, mdl: int, mesh: int, name: str, ps: int = None) -> bool:
    """FUN_0040d2c0: the MDL and mesh an edit names are there"""
    mem = w.mem
    ps = w.ps if ps is None else ps
    if mem.u32(ps + 0x60) <= mdl:
        w.say(f"{name}: Invalid MDL - #{mdl}\n")
        return False
    if (mesh & 0xFFFF) < mem.u32(mem.u64(ps + 0x08) + mdl * 0x40 + 0x30):
        return True
    w.say(f"{name}: Invalid Mesh - #{mesh & 0xFFFF} in MDL #{mdl}\n")
    return False


def vertex_range(w, first: int, last: int, name: str, top: int):
    """
    FUN_0040d1c0: a vertex range as an edit names it, made concrete against
    `top` vertices. Negative counts back from the end, last 0 means just the
    first, 32767 (either sign) means all of them. Returns (ok, first, last)
    """
    first, last = u32(first), u32(last)
    if i32(first) < 0:
        first = u32(first + top)
    t = last
    if i32(t) < 0:
        t = u32(t + top)
        last = t
    skip = False
    if first & 0xFFFF7FFF == 0x7FFF:
        if t != 0:
            skip = True
        else:
            t = u32(top - 1)
            first, last = 0, t
    if not skip and t == 0:
        t = first
        last = t
    if i32(t) < i32(top):
        if i32(t) < i32(first):
            w.say(f"{name}: Vertex 1 {i32(first)} > Vertex 2 {i32(t)}\n")
            return False, first, last
    else:
        if w.verbose and t & 0xFFFF7FFF != 0x7FFF:
            w.say(f"{name}: Idx2 > Max Vertex.  Setting to Max\n")
        last = u32(top - 1)
    if i32(first) >= 0 and i32(last) >= 0:
        return True, first, last
    w.say(f"Error: {name}: Negative Index\n")
    return False, first, last
