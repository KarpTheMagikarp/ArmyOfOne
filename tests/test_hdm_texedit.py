"""
The texture commands (texedit.py) against FF XIII HD Models.exe. Every input
is built here: two made up models of DXT1 textures (and a DXT5 or two the
editor has to refuse), their pixels from a fixed generator that makes plenty
of blocks in each colour order and some with both colours equal, so the
endpoint swaps and fix ups all run. The hashes are what the real exe wrote
for these exact inputs under Wine, so a pass means byte for byte
"""

import hashlib
import struct

import pytest

from patcher.mods.hdmodels import build
from tests import hdm_world as W

PATHS = ["chr\\pc\\c005\\bin\\c005.win32", "chr\\pc\\c105\\bin\\c105.win32"]
OUT = "chr\\pc\\c905\\bin\\c905.win32"
RECIPE = "Data\\T.txt"
DXT1, DXT5 = 0x18, 0x1A

# (format, width, height, mips): 2 high has no block rows, 12x20 doesn't halve evenly
HERO = [(DXT1, 16, 16, 3), (DXT1, 32, 8, 4), (DXT1, 8, 2, 2), (DXT1, 12, 20, 3),
        (DXT5, 16, 16, 1), (DXT1, 64, 64, 7), (DXT1, 16, 16, 3)]
OTHER = [(DXT1, 16, 16, 3), (DXT1, 32, 32, 2), (DXT5, 8, 8, 1)]


def blocks(count: int, seed: int) -> bytes:
    """DXT1 blocks off an LCG: a third with colours equal or one step apart"""
    state = seed * 2654435761 + 12345
    out = bytearray()
    for _ in range(count):
        words = []
        for _ in range(3):
            state = (state * 6364136223846793005 + 1442695040888963407) & (2 ** 64 - 1)
            words.append(state >> 32)
        c0 = words[0] & 0xFFFF
        pick = words[0] >> 16 & 7
        c1 = c0 if pick == 0 else (c0 + (1, -1, 32, -2048)[pick - 1]) & 0xFFFF if pick < 5 \
            else words[1] & 0xFFFF
        out += struct.pack("<HHI", c0, c1, words[2])
    return bytes(out)


def model(textures, seed: int, far: bool = False):
    txbs, imgb = [], bytearray()
    for k, (fmt, w, h, mips) in enumerate(textures):
        tx, size = W.txb(fmt, w, h, mips, at=len(imgb))
        if far and k == 0:
            tx = tx[:0x58] + struct.pack(">I", 0x80000010) + tx[0x5C:]
        txbs.append(tx)
        imgb += blocks(size // 8, seed * 100 + k)
    junk = bytearray(b"SEDBzzz\0" + bytes(range(0x58)))
    junk[0x47] = 0
    return W.trb(txbs + [bytes(junk)]), bytes(imgb)


def dds(magic: int, count: int, seed: int) -> bytes:
    head = bytearray(0x80)
    head[:4] = b"DDS "
    struct.pack_into("<I", head, 4, magic)
    return bytes(head) + blocks(count, seed)


def world(cmds, flags=0, other_imgb=True, far=False):
    """
    `cmds` maps a texture of the first model to the lines that follow its
    block, every texture going out in order; `far` points the second model's
    first texture past 2GB into its .imgb
    """
    files = {}
    for t, (path, textures) in enumerate(zip(PATHS, (HERO, OTHER))):
        data, imgb = model(textures, t + 1, far and t == 1)
        files["models\\orig\\" + path + ".trb"] = data
        if t == 0 or other_imgb:
            files["models\\orig\\" + path + ".imgb"] = imgb
    files["Data\\good.dds"] = dds(0x7C, 0x200, 7)
    files["Data\\short.dds"] = dds(0x7C, 5, 8)
    files["Data\\bad.dds"] = dds(0x7D, 0x200, 9)
    out = [(0, k, 1 if k in cmds else 0) for k in range(len(HERO))]
    out += [(0, len(HERO)), (0, len(HERO) + 1), (0, len(HERO) + 2)]
    edits = [line for k in sorted(cmds) for line in cmds[k]]
    files[RECIPE] = W.recipe(PATHS, OUT, out, edits=edits, flags=flags)
    return files


SCALES = ["1", "0.5", "2", "1.5", "0.333", "-1", "10", "0.1", "1e10", "1e40", "", "x"]

CASES = {
    "untouched": dict(cmds={0: [":X"]}),
    "scale_modes": dict(cmds={
        0: [f":E,{m},{s},{a}" for m, s, a in [(0, "0.5", 3), (2, "1.5", -4), (3, "2", 1)]] + [":X"],
        3: [":E,4,0.75,5", ":E,1,1,-31", ":X"],
        5: [f":E,{m},{s},{(m * 7) % 11 - 5}" for m in (1, 2, 3, 4) for s in SCALES[:6]] + [":X"]}),
    "scale_odd": dict(cmds={
        0: [f":E,{m},{s},0" for m in (1, 2, 4) for s in SCALES[6:]] + [":E,5,2,3", ":E,-1,2,3", ":X"],
        1: [":E,2,1,-63", ":E,2,1,63", ":E,3,0,31", ":E,1,-0.75,40", ":X"]}),
    "scale_ranges": dict(cmds={
        0: [":e,1,0.5,2,4,20", ":e,2,2,0,-1,30", ":e,3,1,7,10,-1", ":e,4,1.25,-3,20,40", ":X"],
        5: [":e,2,0,0,32,-1", ":e,1,3,0,-2,8", ":e,3,1,-9,31,31", ":e,4,0.9,1,70,-1",
            ":e,1,0.5,0,9,3", ":X"]}),
    "scale_windows": dict(cmds={
        0: [":F,1,0.5,4,0,8,4,12", ":F,2,2,-3,4,-1,0,-1", ":F,3,1,9,-3,-3,-2,7",
            ":F,4,1,3,0,16,0,16", ":F,1,1,5,0,17,0,4", ":F,2,1,5,-4,8,0,8", ":X"],
        1: [":F,2,0.5,10,0,6,9,31", ":F,1,2,1,2,-1,-1,-1", ":X"],
        3: [":F,3,1.5,0,5,19,2,11", ":F,4,0.5,0,0,-1,4,-1", ":X"],
        5: [":F,2,0.75,6,8,40,16,64", ":X"]}),
    "alpha": dict(cmds={k: [":A", ":X"] for k in (0, 1, 2, 3, 5)}),
    "channels": dict(cmds={
        0: [":C,1,2", ":C,2,3", ":C,3,1", ":C,0,2", ":X"],
        1: [":C,2,1", ":C,3,2", ":C,1,3", ":C,2,2", ":C,0,0", ":C,4,1", ":C,1,-1", ":X"],
        3: [":S,1,3", ":S,2,1", ":S,3,2", ":S,0,1", ":X"],
        5: [":S,2,3", ":S,1,1", ":S,0,0", ":S,5,1", ":X"]}),
    "channel_windows": dict(cmds={
        0: [":c,2,1,0,8,4,-1", ":c,1,3,4,-1,0,8", ":c,3,2,-3,7,-2,9", ":c,0,1,0,16,0,16", ":X"],
        1: [":T,1,2,0,4,8,24", ":T,2,3,-1,-1,0,-1", ":T,3,1,2,6,9,30", ":X"],
        3: [":c,2,3,0,-1,0,-1", ":T,1,2,4,8,4,8", ":c,1,2,0,30,0,4", ":T,1,3,-5,4,0,4", ":X"]}),
    "dds": dict(cmds={
        0: [":D,Data\\good.dds", ":E,2,0.5,0", ":X"],
        1: [":D,Data\\short.dds", ":X"],
        3: [":D,Data\\bad.dds", ":D,Data\\none.dds", ":D", ":X"],
        5: [":D,DATA\\GOOD.DDS", ":X"]}),
    "borrow": dict(cmds={
        0: [":I,1,0", ":E,1,0.5,0", ":X"],
        1: [":J,0,0,0,8,0,16,4,0", ":J,1,1,4,-1,8,24,8,8", ":X"],
        3: [":J,1,0,0,-1,0,-1,0,0", ":J,1,2,0,4,0,4,0,0", ":J,0,1,0,8,0,8,0,4", ":X"],
        5: [":I,0,0", ":I,1,2", ":J,1,1,0,4,0,8,0,0", ":X"],
        6: [":I,0,0", ":X"]}),
    "borrow_no_imgb": dict(cmds={0: [":I,1,0", ":J,1,0,0,-1,0,-1,0,0", ":E,2,2,0", ":X"]},
                           other_imgb=False),
    "borrow_far": dict(cmds={0: [":I,1,0", ":X"], 6: [":J,1,0,0,8,0,8,4,4", ":X"]}, far=True),
    "raw": dict(cmds={
        0: [":R,0,0,0,1,2,3,4", ":R,4,8,0,65535,0,-1,70000", ":R,8,4,1,9,9,9,9",
            ":R,0,0,2,7,7,7,7", ":R,2,1,0,5,5,5,5", ":R,16,0,0,1,1,1,1", ":R,0,-1,0,1,1,1,1",
            ":R,0,0,3,1,1,1,1", ":R,0,0,-1,3,3,3,3", ":X"],
        2: [":R,1,4,0,8,8,8,8", ":R,0,0,1,6,6,6,6", ":X"],
        5: [":R,12,20,2,2,4,6,8", ":R,60,60,0,1,2,3,4", ":R,0,4,5,11,12,13,14", ":X"]}),
    "flow": dict(cmds={
        0: ["plain line", ":", ":Z", ":f,1", "", ":E,2,1,9,ignored", ":X", ":E,1,0,0"],
        4: [":E,2,0,0", ":X"],
        6: [":E,3,0,7"]}, flags=1),
    "quiet_refusals": dict(cmds={4: [":A", ":X"], 5: [":C,9,1", ":S,1,7", ":X"]}),
}

GOLDEN = {
    "alpha": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "6e787b1e2243cca43c80927c7f9dc2a56d13f88a5c192902565848d357997f8c",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "borrow": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "15a0c92bbbd62e1efc2871657209801ac6ecf43b24d1dccd8b25a7b3df08d1bd",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "borrow_far": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "2f52d7b831b32507101c971581229e593eff33de6fe130dc53dc713d7a0880ee",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "borrow_no_imgb": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "1c71755ab07d38d81f612e0a729a6963452ae4657ed5447823186196c0c3f78d",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "channel_windows": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "6255e405c4b1408ff16742f1f1a6ebfc5ada91ce2cc152a49efe3e24f258e903",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "channels": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "9dce3deaa54df7b968864dbe31b6d3430c646cd845d59b77b4a48d97df2d1537",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "dds": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "ee5affc7202314bd113df2a134feaa955c52296474f04d3187bf660adeefa6b0",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "flow": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "10262b629f1bb329418ebd40da22826c912edcad596ad47aa4dda45752b4f31e",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "quiet_refusals": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "d4a39530d15188d51065c3f45d112355aea6f1456e4acc83c01e3a51266c3b31",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "raw": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "fba0fe109f1861fecdb6831640fc3cbd0cfcd00950b220da7800d8deb25b73a7",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "scale_modes": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "fbee1b78848cbbac73cae690aa6d39afe8d78315cc5da6dd31f42cfa32cf7602",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "scale_odd": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "7276635b914ac71d01f55424b60ca5ad8d15fe779adcb10b53606fe262f7faea",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "scale_ranges": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "9bfd18a82692c3bae8a084b81ac7023c1339d2ec7fc74144a2ce0e58f67d9584",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "scale_windows": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "d3dc49f1282cfc0da65006f6fcbeabcb6c2213e09e493fce3f9bbbcab056f327",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
    "untouched": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.imgb":
            "6402b692994977942cec1b1d5becfb05c780121f3e3d4efe7251ad6abe68adf9",
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5e0612b8a0102b4f793237b3dd9d5e3677115abcba3e96684ac79897783f1de2",
    },
}


def _out(files) -> dict:
    lower = {k.lower(): v for k, v in files.items()}

    class Files:
        def read(self, path):
            return lower.get(path.lower())

    return build.run(files[RECIPE], Files())


@pytest.mark.parametrize("name", sorted(CASES))
def test_texture_commands_match_the_exe(name):
    got = _out(world(**CASES[name]))
    assert {k: hashlib.sha256(v).hexdigest() for k, v in got.items()} == GOLDEN[name]


def test_an_edit_off_the_end_of_the_pixels_is_refused():
    # a 4x4 told it has 4 mips: the editor's layout outruns the 48 bytes the exe mallocs
    files = world({})
    tx, size = W.txb(DXT1, 4, 4, 4)
    files["models\\orig\\" + PATHS[0] + ".trb"] = W.trb([tx])
    files["models\\orig\\" + PATHS[0] + ".imgb"] = blocks(size // 8, 3)
    files[RECIPE] = W.recipe(PATHS[:1], OUT, [(0, 0, 1), (0, 1), (0, 2)], edits=[":A", ":X"])
    with pytest.raises(build.ModelError):
        _out(files)


@pytest.mark.parametrize("line", [
    # x past the top mip's multiples of 4 lands a block past the last one
    ":R,8,8,1,9,9,9,9",
    # a source with fewer mips than the texture it's copied into runs out
    ":J,1,1,0,-1,0,-1,0,0",
    ":J,0,1,0,8,0,8,-4,4",
    ":I,0,5",
    ":I,9,0",
    ":J,0,9,0,-1,0,-1,0,0",
])
def test_a_command_the_exe_would_run_off_a_buffer_with_is_refused(line):
    with pytest.raises(build.ModelError):
        _out(world({0: [line, ":X"]}))
