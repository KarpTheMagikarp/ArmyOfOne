"""
The catalog mesh edits (mesh_import.py: :~ :T :J :j) against FF XIII HD
Models.exe. A made up model (a bent 6x5 grid with six bones, plus a small
mesh for the bone weight blends) and a made up catalog of its own vertices
nudged around, built here. The hashes are what the real exe wrote for
these exact inputs under Wine, so a pass means byte for byte
"""

import hashlib
import struct

import pytest

from patcher.mods.hdmodels import build
from patcher.mods.hdmodels.recipe import Recipe
from tests import hdm_world as W

PATH = "chr\\pc\\c005\\bin\\c005.win32"
OUT = "chr\\pc\\c905\\bin\\c905.win32"
RECIPE = "Data\\T.txt"
CAT = "data\\Meshes.bin"

XS = (-0.5, -0.4, -0.15, 0.0, 0.3, 0.65)
YS = (0.0, 0.05, 0.2, 0.42, 0.45)


def _q(v: float) -> int:
    return round(v * 32767)


def _vertex(k: int, pos, bones, weights) -> bytes:
    """32 bytes in WRB_LAYOUT: position, normal, bones, weights, colour, uv, tangent"""
    v = bytearray(32)
    struct.pack_into(">3h", v, 0, *(_q(p) for p in pos))
    v[8:12] = bytes(((k * 13) % 200 + 20, (k * 7) % 200 + 30, 200 - k % 50, 0x7F if k % 2 else 0x80))
    v[12:16], v[16:20] = bytes(bones), bytes(weights)
    v[20:24] = bytes(((k * 41) & 0xFF, 0xFF if k % 7 == 0 else (k * 3) & 0xFF, 0x10, 0x80))
    struct.pack_into(">ee", v, 24, k * 0.01, 0.5 - k * 0.02)
    v[28:32] = bytes((127 + k, 127 - k, 60 + k * 3, 0x7F if k % 3 else 0x80))
    return bytes(v)


def _grid():
    pos, verts = [], []
    for j, y in enumerate(YS):
        for i, x in enumerate(XS):
            k = j * len(XS) + i
            p = (x, y, round(x * y * 0.5 + (0.03 if (i + j) % 3 == 0 else 0.0), 4))
            base = k % 6
            bones = (base, (base + 1) % 6, (base + 3) % 6, 0xFF if k % 4 else (base + 4) % 6)
            weights = (0x70, 0x50, 0x3F, 0) if k % 4 else (0x60, 0x40, 0x3F, 0x20)
            pos.append(p)
            verts.append(_vertex(k, p, bones, weights))
    faces = []
    for j in range(len(YS) - 1):
        for i in range(len(XS) - 1):
            a = j * len(XS) + i
            b, c, d = a + 1, a + len(XS) + 1, a + len(XS)
            faces += [(a, b, c), (a, c, d)] if (i + j) % 2 else [(a, b, d), (b, c, d)]
    # a corner repeated and a triangle twice over, for the odd paths
    faces += [(2, 2, 9), faces[3]]
    return pos, verts, faces


GRID_POS, GRID_VERTS, GRID_FACES = _grid()


def _envds(verts, count=6):
    """an ENVD per bone, listing the vertices whose blend indices name it"""
    out = []
    for n in range(count):
        vl = [k for k, v in enumerate(verts) if n in v[12:16]]
        out.append((b"bone%d" % n, vl, [verts[k][16 + verts[k][12:16].index(n)] for k in vl]))
    return out


# the blend mesh: ends sharing some bones and not others, so the merge
# overflows four and the 4th and 5th tie
BLEND = [
    _vertex(0, (0.1, 0.2, 0.3), (0, 1, 2, 3), (64, 64, 64, 63)),
    _vertex(1, (0.4, 0.2, -0.3), (4, 5, 1, 0), (64, 64, 64, 63)),
    _vertex(2, (-0.2, 0.1, 0.0), (2, 0xFF, 0xFF, 0xFF), (255, 0, 0, 0)),
    _vertex(3, (0.25, 0.5, 0.1), (3, 4, 0xFF, 0xFF), (200, 55, 0, 0)),
    _vertex(4, (0.3, 0.2, -0.3), (5, 4, 3, 2), (100, 80, 50, 25)),
    _vertex(5, (-0.9, 0.95, 0.99), (1, 0xFF, 0, 0xFF), (130, 0, 125, 0)),
    # the 4th/5th tie with both ends as far from the middle, then with a further out
    _vertex(6, (-0.1, 0.2, 0.3), (4, 5, 0, 2), (32, 64, 32, 32)),
    _vertex(7, (0.1, 0.2, 0.3), (3, 2, 1, 5), (100, 63, 64, 32)),
    _vertex(8, (0.4, 0.2, -0.3), (1, 2, 4, 3), (100, 64, 32, 32)),
    _vertex(9, (0.1, 0.2, 0.3), (0, 5, 4, 2), (64, 64, 50, 63)),
]
BLEND_FACES = [(0, 1, 2), (1, 3, 2), (0, 2, 4), (3, 4, 5), (0, 1, 5), (6, 7, 8), (7, 9, 8)]

# float positions, way past +-1 (the clamps, and :T's 16 bit overflow
# check), and normals of zero length
FLOAT_LAYOUT = ((0x00, 0x0, 1, 4), (0x0C, 0x2, 3, 4), (0x10, 0xF, 6, 4), (0x14, 0xE, 3, 4),
                (0x18, 0x8, 2, 2))
FLOAT_POS = [(40000.0, 2.5, -0.5), (30000.0, 0.25, 0.5), (-3.0, -1.5, 0.75), (0.5, 0.5, 0.5)]


def _float_vertex(k: int, pos) -> bytes:
    v = bytearray(32)
    struct.pack_into(">3f", v, 0, *pos)
    v[0x0C:0x10] = bytes((127, 127, 127, 0x80)) if k % 2 else bytes((40, 200, 127, 0x7F))
    v[0x10:0x14] = bytes((k % 2, 0xFF, 0xFF, 0xFF))
    v[0x14:0x18] = bytes((255, 0, 0, 0))
    struct.pack_into(">ee", v, 0x18, 0.25 * k, 1.0 - 0.25 * k)
    return bytes(v)


FLOATS = [_float_vertex(k, p) for k, p in enumerate(FLOAT_POS)]

MESH = W.wrb([
    [W.wrb_mesh_from(GRID_VERTS, GRID_FACES, name=b"grid", envds=_envds(GRID_VERTS)),
     W.wrb_mesh_from(BLEND, BLEND_FACES, name=b"blend", envds=_envds(BLEND))],
    [W.wrb_mesh(6, [(0, 1, 2), (2, 3, 4), (4, 5, 0)], name=b"other", seed=5),
     W.wrb_mesh_from(FLOATS, [(0, 1, 2), (1, 3, 2), (0, 3, 1)], layout=FLOAT_LAYOUT, name=b"floats",
                     envds=[(b"bone0", [0, 2], [255, 255]), (b"bone1", [1, 3], [255, 255])])],
])


def _decoded(p):
    return tuple(_q(v) / 32767.0 for v in p)


def _records(picks, shift):
    out = []
    for k in picks:
        x, y, z = _decoded(GRID_POS[k])
        out.append((x, y, z, x + shift[0], y + shift[1], z + shift[2]))
    return out


def _clamped(p):
    return tuple(max(-1.0, min(1.0, v)) for v in p)


ENTRIES = [
    ("Floats.txt", [_clamped(p) + (0.5, -0.25, 0.125) for p in FLOAT_POS]),
    ("Grid_All.txt", _records(range(30), (0.01, -0.02, 0.005))),
    ("Grid_Part.txt", _records([k for k in range(30) if k % 3], (-0.004, 0.003, 0.0))),
    ("Grid_Few.txt", _records([0, 1, 6, 7, 13, 20, 21, 27], (0.0, 0.0, -0.01))),
    ("Grid_Dup.txt", _records([3, 4, 3, 10, 4], (0.02, 0.0, 0.0))),
    ("Grid_Edge.txt", _records([8, 9, 14, 15, 16, 22], (0.0, 0.05, 0.0))),
]


def catalog(entries=ENTRIES) -> bytes:
    """count, a 0x28 byte entry per mesh (name, offset, count), then the vertices"""
    at = 4 + 0x28 * len(entries)
    table, body = b"", b""
    for name, recs in entries:
        table += name.encode().ljust(0x20, b"\0") + struct.pack("<II", at + len(body), len(recs))
        body += b"".join(struct.pack("<6f", *r) for r in recs)
    return struct.pack("<i", len(entries)) + table + body


def vertex_file(recs) -> bytes:
    """the text form: x,z,-y then the target the same way, y's sign flipped"""
    lines = [str(len(recs))]
    for x, y, z, tx, ty, tz in recs:
        lines.append(",".join(repr(v) for v in (x, z, -y, tx, tz, -ty)))
    return ("\r\n".join(lines) + "\r\n").encode()


# off the edge of +-1, so the reader's clamps kick in, and short a line
LOOSE = vertex_file(_records([0, 5, 6, 11, 12, 17], (-1.8, -1.5, 1.2)))[:-2]
OTHER = catalog([("Grid_Few.txt", _records([0, 1, 2, 3], (0.1, 0.1, 0.1)))])
EMPTY = struct.pack("<i", 0)


def model():
    blocks = [b"SEDBabc".ljust(8, b"\0") + bytes(range(40)), MESH,
              b"SEDBxyz".ljust(8, b"\0") + bytes(range(24))]
    return W.trb(blocks, tags=[b"abc", b"brw", b"xyz", b"m1", b"m2"],
                 names=[b"F00\\one", b"mesh", b"three", b"meta1", b"meta2"])


def world(edits, catalog_file=True, **kw):
    files = {"models\\orig\\" + PATH + ".trb": model(), "Loose.txt": LOOSE}
    if catalog_file:
        files.update({CAT: catalog(), "data\\Other.dat": OTHER, "data\\Empty.bin": EMPTY})
    files[RECIPE] = W.recipe([PATH], OUT, [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)], edits=edits,
                             imgb="no.imgb", **kw)
    return files


CASES = {
    "untouched": [":X"],
    "shift": [f":J,0,0,Grid_Part.txt,{CAT}", ":X"],
    "shift_part_way": [f":j,0,0,0.25,grid_all.TXT,{CAT}", f":j,0,0,-1.5,Grid_Few.txt,{CAT}", ":X"],
    "shift_duplicates": [f":J,0,0,Grid_Dup.txt,{CAT}", ":X"],
    "shift_from_text": [f":j,0,0,0.5,Loose.txt,{CAT}", ":X"],
    "shift_missing": [f":J,0,0,Nope.txt,{CAT}", f":J,0,9,Grid_All.txt,{CAT}", f":J,2,0,Grid_All.txt,{CAT}",
                      ":X"],
    "insert": [":T,0,1,0,1,0.5,0.5", ":X"],
    "insert_split": [":T,0,0,0,1,0.5,0.5,1", ":T,0,0,7,1,0.3,0.6,1", ":T,0,0,-1,8,0.5,0.5,1", ":X"],
    "insert_blends": [":T,0,1,0,1,0.5,0.5,1", ":T,0,1,1,0,0.5,0.5,1", ":T,0,1,2,3,0.25,0.75,1",
                      ":T,0,1,4,5,0.9,0.1,1", ":T,0,1,0,4,1.5,-0.5", ":T,0,1,6,7,0.75,0.5",
                      ":T,0,1,8,9,0.5,0.5,1", ":X"],
    "insert_odd": [":T,0,0,3,3,0.5,0.5,1", ":T,0,0,2,9,0.5,0.5,1", ":T,0,0,9,2,0.5,0.5,3", ":X"],
    "import_everything": [f":~,0,0,0,0.0,0.0,Grid_All.txt,{CAT}", ":X"],
    "import_long_edges": [f":~,0,0,0,0.0,0.12,Grid_Part.txt,{CAT}", ":X"],
    "import_mixed": [f":~,0,0,3,0.0,0.16,Grid_Few.txt,{CAT}", f":~,0,0,0,-1.0,0.2,Grid_Edge.txt,{CAT}",
                     ":X"],
    "import_twice": [f":~,0,0,0,0.0,0.15,Grid_All.txt,{CAT}", f":~,0,0,0,0.0,0.05,Grid_Part.txt,{CAT}",
                     ":X"],
    "import_none_long_enough": [f":~,0,0,0,0.0,1.0,Grid_All.txt,{CAT}", ":X"],
    "import_in_a_box": [":e,1,0.5,1,1,0,0,0,-0.2,0.2,0", f":~,0,0,0,0.0,0.0,Grid_All.txt,{CAT}", ":X"],
    "import_from_text": [":~,0,0,0,0.0,0.1,Loose.txt,nocat\\x.bin", ":X"],
    "import_missing": [f":~,0,0,0,0.0,0.0,Nope.txt,{CAT}", f":~,0,5,0,0.0,0.0,Grid_All.txt,{CAT}",
                       f":~,3,0,0,0.0,0.0,Grid_All.txt,{CAT}", ":X"],
    "import_then_insert": [f":~,0,0,0,0.0,0.1,Grid_Part.txt,{CAT}", ":T,0,0,-1,-2,0.5,0.5,1",
                           f":J,0,0,Grid_Few.txt,{CAT}", ":X"],
    "verbose": [f":~,0,0,0,0.0,0.12,Grid_Part.txt,{CAT}", f":J,0,0,Grid_Dup.txt,{CAT}",
                ":T,0,1,0,1,0.5,0.5,1", ":X"],
    "legacy": [f":~,0,0,0,-0.45,0.45,-1,1,-1,1,0.0,0.0,Grid_All.txt,{CAT}",
               f":J,0,0,-1,1,-1,1,-1,1,Grid_Few.txt,{CAT}", ":X"],
    "catalog_switching": [f":J,0,0,Grid_Few.txt,{CAT}", ":J,0,0,Grid_Few.txt,data\\Other.dat",
                          f":j,0,0,0.5,Grid_Part.txt,{CAT}", f":J,0,0,Grid_Few.txt,{CAT}",
                          ":J,0,0,Grid_Part.txt,data\\Empty.bin", ":X"],
    "verbose_no_catalog": [":~,0,0,0,0.0,0.1,Loose.txt,nocat\\x.bin", ":j,0,0,0.5,Loose.txt,nocat\\x.bin",
                           ":X"],
    "shift_in_a_box": [":e,1,0.5,1,1,0,0,0,-0.2,0.2,0", f":J,0,0,Grid_All.txt,{CAT}", ":X"],
    "floats": [":T,1,1,0,1,0.5,0.5,1", ":T,1,1,1,3,0.25,0.5,1", f":~,1,1,0,0.0,0.0,Floats.txt,{CAT}",
               f":J,1,1,Floats.txt,{CAT}", ":X"],
    # Windows drops a path's trailing spaces and dots, which the Vanille
    # add-on's generated recipes lean on; only off the last part though
    "trailing_spaces": [f":J,0,0,Grid_Part.txt,{CAT} ", f":J,0,0,Grid_Few.txt,{CAT}. .",
                        ":J,0,0,Grid_Dup.txt,data .\\Meshes.bin", f":~,0,0,0,0.0,0.1,Grid_Edge.txt,{CAT} ",
                        ":j,0,0,0.5,Loose.txt  ,data\\nope.bin", ":X"],
}
KW = {"verbose": dict(flags=1), "legacy": dict(version=41), "verbose_no_catalog": dict(flags=1)}
NO_CATALOG = {"import_from_text", "verbose_no_catalog"}

GOLDEN = {
    "catalog_switching": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5fa751b413f322ff91eb9ccd26a3de26c03830555930477904daccd0c08997e2",
    },
    "floats": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "a73130fc3492097486bead3bffecaaffa594770974f3288b8856c759fe2dab41",
    },
    "import_everything": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "c50fd4f2aee00f9047a420233ce1248a7c419ad76e2318f19d8073550886593a",
    },
    "import_from_text": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "875b4a3b44319a46f21fc3fea2378e21ded720de258b007e99ad86d7f34dea51",
    },
    "import_in_a_box": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "cc014b47c4ecbdcbd0e32b0548365ff18e22ee0e447f4ab4caa5abd48c874da1",
    },
    "import_long_edges": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "bd9c70126def4c758ffb135bc927444eb279114411f591d1f5f5f904217df3bc",
    },
    "import_missing": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "217758938efee9f4a3482405748d152681f4f4241d64ff40285afbc0b55e234a",
    },
    "import_mixed": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "e57bd9bd35c90a954cbe63080c8c508c5626edca011304ea04c33e810e32e81e",
    },
    "import_none_long_enough": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7b203126d5e0ca39beb020a72640c90dbb04658edb0ed42bb712c08f7122532a",
    },
    "import_then_insert": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "ad8dce48230aed1f712b6d87493643cf753b0b5086b762e814e21bc554030895",
    },
    "import_twice": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "b6b654e965f2a83c34ca85c02cb8209350c7e4cbdd0de0b520033c6dab4d1e78",
    },
    "insert": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "3fc207080814eba63994051ae936949c8d555d6576788422797b743e0c1a6700",
    },
    "insert_blends": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "13edd3d60e876f92f4e13b77e7ace375be726752b11f34f97cf217a591388904",
    },
    "insert_odd": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "4189ec1c6367263e70a52c77ae8f38c1b5a00dbf13e79349c484efaa7c54b945",
    },
    "insert_split": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "b464242d6f4605cd19a41df6341c7534c40fff979dcdc8bbecef547fa078c42f",
    },
    "legacy": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "3bb6f1d63a7f37d3d2f07d7e9da395cbe64eeab2e6c38b6ff03ffecf5830c932",
    },
    "shift": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "e88a1bb0307a6d467addf90328801d6e2200517b76ba273d9d7c9ee6d130f1e4",
    },
    "shift_duplicates": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "85eb98b467e41e4b5c9ed9b93ea5494ba1153fdbe3d737d2a607621f3408300b",
    },
    "shift_from_text": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "bffee5f27453b719aec7f3d0bb89d159b175bc8567fc8f908f5906760b6ce9f8",
    },
    "shift_in_a_box": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "722557047d33f5e98838d9eab54b633f4d51f6bd08c3364f8907f4e1b3cf1a32",
    },
    "shift_missing": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "217758938efee9f4a3482405748d152681f4f4241d64ff40285afbc0b55e234a",
    },
    "shift_part_way": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "0bff5f09944bf36ca62ebc93fb650e3a18d006189eb4c46744befd1f41061c47",
    },
    "trailing_spaces": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "d830ab85577fcc050cb50140556314c07358e479383fe5bcfeaabd3c82d8d474",
    },
    "untouched": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "217758938efee9f4a3482405748d152681f4f4241d64ff40285afbc0b55e234a",
    },
    "verbose": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "122267813ab90128637efd32af181baf13366182bc356a218af5608a3bf172e2",
    },
    "verbose_no_catalog": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "875b4a3b44319a46f21fc3fea2378e21ded720de258b007e99ad86d7f34dea51",
    },
}


def _out(files) -> dict:
    lower = {k.lower(): v for k, v in files.items()}

    class Files:
        def read(self, path):
            return lower.get(path.lower())

    return build._main(build.Build(Recipe(files[RECIPE]), Files()))


@pytest.mark.parametrize("name", sorted(CASES))
def test_mesh_import_matches_the_exe(name):
    files = world(CASES[name], catalog_file=name not in NO_CATALOG, **KW.get(name, {}))
    got = _out(files)
    assert {k: hashlib.sha256(v).hexdigest() for k, v in got.items()} == GOLDEN[name]


def test_a_catalog_mesh_too_big_for_the_exe_is_refused():
    # the exe sizes its buffer off the low 16 bits of the count and reads all 32
    big = catalog([("Huge.txt", [(0.0,) * 6] * 0x8000)])
    files = world([f":~,0,0,0,0.0,0.0,Huge.txt,{CAT}", ":X"])
    files[CAT] = big
    with pytest.raises(build.ModelError):
        _out(files)


def test_blending_past_the_mesh_is_refused():
    with pytest.raises(build.ModelError):
        _out(world([":T,0,1,0,-900,0.5,0.5", ":X"]))
