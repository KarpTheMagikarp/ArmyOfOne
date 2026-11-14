"""
C's numbers the way FF XIII HD Models.exe has them: msvcrt's atoi and atof,
float32 rounding at every step the exe does one in single precision, and the
byte order flips its PS3 paths do. Everything in this package goes through
these, so a recipe's 0.005 lands on the same bits the exe's would
"""

import re
import struct

from patcher.mods.wdbmod import atoi, atof  # noqa: F401  same msvcrt underneath

_F = struct.Struct("<f")


def f32(value: float) -> float:
    """a double rounded to float32 and back, which is what every float store does"""
    try:
        return _F.unpack(_F.pack(value))[0]
    except OverflowError:
        return float("inf") if value > 0 else float("-inf")


def i32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


def i16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def u16(value: int) -> int:
    return value & 0xFFFF


def u32(value: int) -> int:
    return value & 0xFFFFFFFF


def bswap16(value: int) -> int:
    return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)


def bswap32(value: int) -> int:
    return int.from_bytes((value & 0xFFFFFFFF).to_bytes(4, "little"), "big")


def stoi(text: str) -> int:
    """std::stoi: like atoi, but nothing to read is an error rather than 0"""
    hit = re.match(r"[ \t\n\v\f\r]*([+-]?\d+)", text)
    if not hit:
        raise ValueError(text)
    value = int(hit.group(1))
    if not -(1 << 31) <= value < (1 << 31):
        raise ValueError(text)
    return value


def cstr(buf, at: int = 0) -> bytes:
    """the NUL-terminated string at `at`"""
    end = buf.find(b"\0", at)
    return bytes(buf[at:end if end >= 0 else len(buf)])
