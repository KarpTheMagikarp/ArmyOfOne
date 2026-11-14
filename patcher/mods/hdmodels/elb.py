"""
The elb block: per-bone values the game reads alongside the skeleton.

Two things can happen to one. With recipe flag 8 the whole block is taken
from the second model instead (its block tagged "ble"), and with a
positive block parameter, `:E,field,n,value` lines set single floats in it
until `:X`: fields 1 to 3 in the n'th 0x20 byte record after the header,
4 to 6 in the n'th 0xc byte record of the table after those.
"""

import struct

from patcher.mods.hdmodels import state
from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import i32, u32

_F = struct.Struct("<f")


def edit(b, n: int, block: bytearray, length: int, param: int):
    """main's elb branch: the swap for the second model's, then FUN_00401550"""
    if b.r.flags & 8:
        found = state.find_block(b, 1, b"ble") if len(b.trbs) > 1 else -1
        # the exe only checks for 0 here, so a miss (-1) reads past its table
        if found != 0:
            if found < 0:
                raise ModelError("its recipe swaps in an elb block the second model hasn't got")
            src = b.trbs[1]
            length = u32(src.offset(found + 1) - src.offset(found))
            size = src.size(found)
            length = min(length, u32(size + (-size & 0xF)))
            block = src.read(src.start + src.offset(found), length)
            b.replace(block, length, n, 1)
    if param > 0:
        _edits(b, block, length)
    return block, length


def _edits(b, block: bytearray, length: int) -> None:
    records = 0x40
    table = (struct.unpack_from("<I", block, 0x30)[0] + 2) * 0x20 if length >= 0x34 else 0
    while True:
        line = b.r.read_line(0xFF)
        if line is None:
            return
        if b.r.flags & 3:
            b.say(line + "\n")
        head, _, b.r.rest = line.partition(",")
        if head[:1] != ":":
            continue
        kind = head[1:2]
        if kind == "E":
            field, n, value = i32(b.r.int()), i32(b.r.int()), b.r.float()
            if 1 <= field <= 3:
                _put(block, length, records + n * 0x20 + 8 + (field - 1) * 4, value)
            elif 4 <= field <= 6:
                _put(block, length, table + n * 0xC + (field - 4) * 4, value)
        elif kind == "X":
            return
        elif kind:
            b.say(f"Invalid ELB Edit {head}\n")


def _put(block: bytearray, length: int, at: int, value: float) -> None:
    if not 0 <= at <= len(block) - 4:
        raise ModelError("its recipe edits past the end of a bone table")
    _F.pack_into(block, at, value)
