"""
The native model builder's container work against FF XIII HD Models.exe:
blocks copied across models, names renumbered and swapped, textures moved
into the new .imgb (and swapped for a mod's own), the elb's edits. The
hashes are the real exe's output on the same inputs, built here
"""

import hashlib
import struct

import pytest

from patcher.mods.hdmodels import build
from tests import hdm_world as W

FIRST = "chr\\pc\\c005\\bin\\c005.win32"
SECOND = "chr\\pc\\c595\\bin\\c595.win32"
OUT = "chr\\pc\\c205\\bin\\c205.win32"


def elb(records: int, rows: int, seed: int = 1) -> bytes:
    head = bytearray(0x40)
    head[0:8] = b"SEDBelb\0"
    struct.pack_into("<I", head, 0x30, records)
    size = (records + 2) * 0x20 - 0x40 + rows * 0xC
    return bytes(head) + bytes((n * seed + 1) & 0xFF for n in range(size))


def dds(width: int, height: int, mips: int) -> bytes:
    """a DXT1 .dds the way a mod ships one for a texture"""
    head = bytearray(0x80)
    head[0:4] = b"DDS "
    struct.pack_into("<IIII", head, 4, 0x7C, 0x81007, height, width)
    struct.pack_into("<I", head, 0x1C, mips)
    head[0x54:0x58] = b"DXT1"
    size = sum(W.mip_sizes(0x18, width, height, mips))
    return bytes(head) + bytes((n * 5 + 1) & 0xFF for n in range(size))


def model(seed: int, texture=(0x18, 32, 16, 3)):
    tex, size = W.txb(*texture)
    pixels = bytes((n * seed + 3) & 0xFF for n in range(size))
    blocks = [b"SEDBabc\0" + bytes(range(seed, seed + 40)), tex,
              b"c_c005 c20x_pulsS hello ~".ljust(48, b"\0"), elb(4, 3, seed)]
    names = [b"F00\\one.ps3", b"tex_a", b"c_c005_three", b"F00\\four", b"meta1", b"meta2"]
    records = [b"r0".ljust(16, b"\0"), b"tex_a".ljust(16, b"\0"), b"c_c005rec".ljust(16, b"\0"),
               b"r3".ljust(16, b"\0"), b"m", b"m"]
    tags = [b"cba", b"bxt", b"xyz", b"ble", b"t1", b"t2"]
    return W.trb(blocks, names=names, records=records, tags=tags), pixels


CASES = ("copy", "two models", "swapped textures", "pinned swap", "elb edits", "elb from the other")


def inputs(name: str):
    first, first_px = model(5)
    second, second_px = model(11, (3, 8, 8, 2))
    files = {f"models\\orig\\{FIRST}.trb": first, f"models\\orig\\{FIRST}.imgb": first_px,
             f"models\\orig\\{SECOND}.trb": second, f"models\\orig\\{SECOND}.imgb": second_px}
    swaps = [("c_c005", "c_c205"), ("c20x_pulsS", "c00x_pulsS")]
    all_blocks = [(0, n) for n in range(6)]
    trbs, edits, flags = [FIRST], [], 0
    blocks = all_blocks
    if name == "two models":
        trbs = [FIRST, SECOND]
        blocks = [(0, 0), (1, 1), (0, 2), (1, 3), (0, 4), (0, 5)]
    elif name == "swapped textures":
        # a mod's own, sized differently from the texture it replaces
        files["textures\\tex_a#.dds"] = dds(64, 32, 4)
    elif name == "pinned swap":
        swaps = [(":2\r\nc_c005", "c_c999"), ("hello", "HELLO")]
    elif name == "elb edits":
        blocks = [(0, 0), (0, 1), (0, 2), (0, 3, 1), (0, 4), (0, 5)]
        edits = [":E,1,0,1.5", ":E,3,2,-2.25", ":E,5,1,0.125", ":E,6,2,7", ":Q", ":X"]
    elif name == "elb from the other":
        trbs = [FIRST, SECOND]
        flags = 8
    files["Data\\T.txt"] = W.recipe(trbs, OUT, blocks, edits=edits, swaps=swaps, flags=flags)
    return files, ["-s"]


GOLDEN = {
    "copy": {"c205.win32.imgb": "911343e917667356", "c205.win32.trb": "4e764d070651a4d2"},
    "two models": {"c205.win32.imgb": "823e5957428e0705", "c205.win32.trb": "013362ee8c5d080e"},
    "swapped textures": {"c205.win32.imgb": "9c25c3fcf72f3c81", "c205.win32.trb": "5e307eb1d8d0a841"},
    "pinned swap": {"c205.win32.imgb": "911343e917667356", "c205.win32.trb": "b53d826bc623a158"},
    "elb edits": {"c205.win32.imgb": "911343e917667356", "c205.win32.trb": "8b229495c7045492"},
    "elb from the other": {"c205.win32.imgb": "911343e917667356", "c205.win32.trb": "5835bf39c1127aea"},
}


class Files:
    def __init__(self, files):
        self.files = {k.lower(): v for k, v in files.items()}

    def read(self, path):
        return self.files.get(path.lower())


@pytest.mark.parametrize("name", CASES)
def test_the_builder_makes_what_the_exe_makes(name):
    files, args = inputs(name)
    code, made = build.command_line(args + ["Data\\T.txt"], Files(files))
    assert code == 0
    got = {path.split("\\")[-1]: hashlib.sha256(data).hexdigest()[:16] for path, data in made.items()}
    assert got == GOLDEN[name]


def test_the_exes_command_line():
    files, _ = inputs("copy")
    assert build.command_line(["\\V"], Files(files)) == (52, {})
    said = []
    assert build.command_line([], Files(files), log=said.append) == (4, {})
    assert said[0] == build.BANNER + "\n"
    assert build.command_line(["-s", "Data\\Nope.txt"], Files(files))[0] == 3
    code, made = build.command_line(["-o", "3", "-s", "Data\\T.txt"], Files(files))
    assert code == 0 and len(made) == 2


def test_stop_is_heard_part_way():
    import threading

    files, _ = inputs("copy")
    stop = threading.Event()
    stop.set()
    with pytest.raises(build.Stopped):
        build.command_line(["-s", "Data\\T.txt"], Files(files), cancel=stop)
