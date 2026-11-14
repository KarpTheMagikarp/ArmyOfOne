"""
The normal and tangent edits (mesh_weights.py: :G :g :y :o :n) against FF
XIII HD Models.exe. One small made up model: a bent grid with a uv seam,
duplicate and lonely vertices and a couple of degenerate triangles, two
meshes sharing its edge positions, and three of hdm_world's noise meshes
(one without tangents, one without uvs). The hashes and messages are what
the real exe wrote for these exact inputs under Wine
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
TRB = "models\\mod\\" + OUT + ".trb"

NO_TANGENT = W.WRB_LAYOUT[:-1]
NO_UV = W.WRB_LAYOUT[:5]


def vert(pos, normal=(0, 0, 0, 0), uv=(0.0, 0.0), tangent=(0, 0, 0, 0), pad=0, bones=(0, 0xFF, 0xFF, 0xFF)):
    v = bytearray(32)
    struct.pack_into(">3hH", v, 0, *pos, pad)
    v[8:12] = bytes(normal)
    v[12:16] = bytes(bones)
    v[16:20] = bytes((0xFF, 0, 0, 0))
    struct.pack_into(">ee", v, 0x18, *uv)
    v[0x1C:0x20] = bytes(tangent)
    return bytes(v)


def grid_pos(r, c):
    return c * 4000 - 10000, r * 3000 - 6000, (c - 2) ** 2 * 900 - r * 500


def grid():
    verts = []
    for r in range(5):
        for c in range(6):
            v = r * 6 + c
            normal = (0, 0, 0, 0) if v % 4 == 0 else (0x7F, 0x7F, 0xFF, 0x7F)
            tangent = (0, 0, 0, 0) if v % 3 == 0 else (0xFF, 0x7F, 0x7F, 0xFF)
            verts.append(vert(grid_pos(r, c), normal, (c / 5, r / 4), tangent))
    # a uv seam down the last column, then a column past it
    for r in range(5):
        verts.append(vert(grid_pos(r, 5), (0x60, 0x90, 0xF0, 0x7F), (0.0, r / 4), (0, 0, 0, 0), pad=r & 1))
    for r in range(5):
        verts.append(vert(grid_pos(r, 6), (0x70, 0x80, 0xF8, 0x7F), (0.25, r / 4), (0x7F, 0xFF, 0x7F, 0)))
    verts.append(verts[7])                                           # 40: an exact copy
    verts.append(vert((123, 456, 789), (1, 2, 3, 4), (0.5, 0.5)))  # 41: in no triangle
    verts.append(vert(grid_pos(2, 1), (0, 0, 0, 0), (0.2, 0.5)))   # 42: on top of 13
    # 43, 44: a pair on the x = 0 centre line, far apart in angle
    verts += [vert((0, 9000, 100), (0x7F, 0x20, 0x7F, 0x7F), (0.1, 0.9)),
              vert((0, 9000, 100), (0xF0, 0x7F, 0x7F, 0x7F), (0.9, 0.9))]
    faces = []
    for r in range(4):
        for c in range(5):
            a = r * 6 + c
            faces += [(a, a + 1, a + 6), (a + 1, a + 7, a + 6)]
    for r in range(4):
        a, b = 30 + r, 35 + r
        faces += [(a, b, a + 1), (b, b + 1, a + 1)]
    faces += [(8, 40, 13), (2, 2, 3), (12, 13, 42), (43, 26, 27), (44, 28, 29)]
    return W.wrb_mesh_from(verts, faces, name=b"grid")


def edge(col, seed):
    """the grid's column `col` again, plus a strip beside it, other normals"""
    verts = [vert(grid_pos(r, col), ((seed * 31 + r * 17) & 0xFF, 0x7F + r * 9, 0xD0, 0x7F), (r / 4, 0.5),
                  (0x7F, 0x7F, 0xFF, 0xFF)) for r in range(5)]
    verts += [vert((x, y, z + 2500), (0x7F, 0x30, 0xF0, 0x7F), (r / 4, 1.0)) for r, (x, y, z) in
              enumerate(grid_pos(r, col) for r in range(5))]
    faces = [(r, r + 5, r + 1) for r in range(4)] + [(r + 5, r + 6, r + 1) for r in range(4)]
    return W.wrb_mesh_from(verts, faces, name=b"edge%d" % col)


def tris(n, count=None, k=0):
    return [((i + k) % n, (i * 3 + 1 + k) % n, (i * 7 + 2 + k) % n) for i in range(count or n)]


MESH = W.wrb([
    [grid(), edge(0, 1), edge(0, 4), edge(5, 2)],
    [W.wrb_mesh(40, tris(40), seed=5, name=b"noise"),
     W.wrb_mesh(24, tris(24, k=3), stride=28, layout=NO_TANGENT, seed=9, name=b"notan"),
     W.wrb_mesh(20, tris(20, k=1), stride=24, layout=NO_UV, seed=2, name=b"nouv")],
])


def world(edits, **kw):
    blocks = [b"SEDBabc".ljust(8, b"\0") + bytes(range(40)), MESH]
    model = W.trb(blocks, tags=[b"abc", b"brw", b"m1", b"m2"], names=[b"F00\\one", b"mesh", b"meta1", b"meta2"])
    return {"models\\orig\\" + PATH + ".trb": model,
            RECIPE: W.recipe([PATH], OUT, [(0, 0), (0, 1), (0, 2), (0, 3)], edits=list(edits) + [":X"],
                             imgb="no.imgb", **kw)}


CASES = {
    "untouched": [],
    "recalc": [":G,0,0,0,32767,1"],
    "recalc_zero_only": [":G,0,0,0,32767,0"],
    "recalc_normals": [":G,0,0,0,32767,3", ":G,0,1,0,32767,2"],
    "recalc_tangents": [":G,0,0,0,32767,5"],
    "recalc_range": [":G,0,0,8,20,1", ":G,0,3,-4,-1,1", ":G,0,2,3,0,1"],
    "recalc_selected": [":e,0,0.6,0.6,0.9,0,0,0,0,0,0", ":G,0,0,0,32767,9"],
    "recalc_cut_out": [":e,0,2,2,2,0,0,0,0,0,0", ":e,-101,0.3,2,2,0,0,0,0.2,0,0", ":G,0,0,0,32767,25"],
    "recalc_noise": [":G,1,0,0,32767,1", ":G,1,1,0,32767,3", ":G,1,2,0,32767,3"],
    "recalc_invalid": [":G,0,0,50,60,1", ":G,0,0,9,4,1", ":G,0,0,-100,5,1"],
    "recalc_with_passes": [":G,0,0,0,32767,1,7", ":G,0,3,0,32767,0,14"],
    "flat": [":g,0,0,0,32767,0"],
    "flat_overwrite": [":g,0,0,0,32767,1", ":g,0,1,0,32767,3"],
    "flat_keep_normals": [":g,0,0,0,32767,4", ":g,0,2,0,32767,5"],
    "flat_range": [":g,0,0,8,20,1", ":g,0,0,-5,-1,0"],
    "flat_selected": [":e,0,0.1,0.1,0.1,0,0,0,-0.3,-0.18,0.11", ":g,0,0,0,32767,9",
                      ":e,0,0.1,0.1,0.1,0,0,0,0,0,0", ":g,0,1,0,32767,9"],
    "flat_noise": [":g,1,0,0,32767,1", ":g,1,1,0,32767,1", ":g,1,2,0,32767,0"],
    "flat_with_passes": [":g,0,0,0,32767,1,9"],
    "smooth": [":y,0,0,-1,-1,-1,-1,0,42.0"],
    "smooth_centre": [":y,0,0,-1,-1,-1,-1,1,1.0"],
    "smooth_pairs": [":y,0,0,0,1,-1,-1,1,25.0", ":y,0,3,0,0,-1,-1,0,25.0"],
    "smooth_three": [":y,0,0,0,1,0,2,1,25.0"],
    "smooth_third_only": [":y,0,0,-1,-1,0,3,0,90.0"],
    "smooth_overrun": [":y,0,1,0,0,0,2,0,180.0"],
    "smooth_selected": [":e,0,1,1,1,0,0,0,-0.3,0,0", ":y,0,0,0,1,0,2,1,180.0"],
    "smooth_noise": [":y,1,0,1,0,1,0,1,90.0", ":y,1,2,-1,-1,-1,-1,0,180.0"],
    "smooth_invalid": [":y,4,0,-1,-1,-1,-1,1,42.0", ":y,0,0,0,9,-1,-1,1,42.0", ":y,0,0,-1,-1,3,0,1,42.0"],
    "dedupe": [":o,0,0,0,32767,0"],
    "dedupe_positions": [":o,0,0,0,32767,30"],
    "dedupe_report": [":o,0,0,0,32767,1"],
    "dedupe_all": [":o,-1,-1,0,32767,22"],
    "dedupe_range": [":o,0,0,5,-3,30", ":o,0,0,0,-1,14"],
    "dedupe_selected": [":e,0,0.4,2,2,0,0,0,0.3,0,0", ":o,0,0,0,32767,30"],
    "dedupe_then_recalc": [":G,0,0,0,32767,1,1", ":g,0,1,0,32767,1,1"],
    "pick_surface": [":n,1,0,0,0", ":n,0,0,0,1", ":n,2,0,0,0"],
}
KW = {"dedupe_report": dict(flags=1)}


def _out(files) -> dict:
    lower = {k.lower(): v for k, v in files.items()}

    class Files:
        def read(self, path):
            return lower.get(path.lower())

    return build._main(build.Build(Recipe(files[RECIPE]), Files()))


@pytest.mark.parametrize("name", sorted(CASES))
def test_weights_match_the_exe(name):
    got = _out(world(CASES[name], **KW.get(name, {})))
    assert {k: hashlib.sha256(v).hexdigest() for k, v in got.items()} == GOLDEN[name]


GOLDEN = {
    "dedupe": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "2a6f3a3672a959035eb05f625a5850933b9f2195b3e1223250e5c055b3cd2d44",
    },
    "dedupe_all": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7873e116e7cfa99c6fe3421eec4fbfa4ebb716241ca90f4e6d3505eb1086033c",
    },
    "dedupe_positions": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "85bd4f3ce22523ef2626116bed95605dade4e9c6f7720189365ec9304c81d70c",
    },
    "dedupe_range": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "9281ee60cfcaa4e20909f53c84572363b850295b016d496a8aeb0f4636cc85bf",
    },
    "dedupe_report": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "cad5f8a77ec06817c28d674bffb379fa9914339191552c5fa3f8d153cb5caa3e",
    },
    "dedupe_selected": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "30109efabdff6e1322d2edf430f4b9b9cb4064aade4e8a145dc61fea6de33816",
    },
    "dedupe_then_recalc": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "d09863e7212390e84a438882ccdf51adbefa3394c9b663ad8b5117cc0cb8a513",
    },
    "flat": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "1d5b54fac2d6bbb652017ede4b8f8c9cb7030f3acc185b6df37911bbe561a519",
    },
    "flat_keep_normals": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "f62dc958930a96a755f40168a1e8d2c736ff91f53497520461701a9838267104",
    },
    "flat_noise": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "4d62a4ea276768d1ac442ceb62e1d85f2ec0e944a125d1a3eb8f13d7d60baa24",
    },
    "flat_overwrite": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "1c52123c12177bf7b5a19516973d57e69b884c83d6ba42dbac2c80c09e81fe9b",
    },
    "flat_range": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "0c04369c701d7aa3bc7bdb6bc0155bfd0530c40524daab09e24b85b398244f46",
    },
    "flat_selected": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "1c2be17973f3a084c0c68f78dcbef2d5e1f9d6d1c66ada13c97d5bd24e736975",
    },
    "flat_with_passes": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "cc28b111e80b16d9ef1fcdce6403051b183feafbced81a2eda12d561fe241571",
    },
    "pick_surface": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "cad5f8a77ec06817c28d674bffb379fa9914339191552c5fa3f8d153cb5caa3e",
    },
    "recalc": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "259b6acaadcbe0bf66e7135f141a6b4acf4cadd5c63c37636c1f06bfce60943d",
    },
    "recalc_cut_out": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "f49ab7405e762857f6d02a872388cdd5297ed5607984013d1127710462911edd",
    },
    "recalc_invalid": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "cad5f8a77ec06817c28d674bffb379fa9914339191552c5fa3f8d153cb5caa3e",
    },
    "recalc_noise": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "d90bfda478ad062260f0859855fe661dd212a38e99a80186514800fe96a5a66d",
    },
    "recalc_normals": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "eaa5d84dabbc9c58c399294dd210e039a4abbadf8a44b6f75c5adc8955eca54e",
    },
    "recalc_range": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "d9abea6d2246f45e9019b4bad3273b95dd488fb66a7679ae0fa20c185fef474b",
    },
    "recalc_selected": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "21404089269dbbc3fcbf8a50182cf4b7a98911b4ece6a81fceca528f1339ee3f",
    },
    "recalc_tangents": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "c40df4c052691a427c324e6c33683ed451dd67dcb41a011ebe67345d675a47cc",
    },
    "recalc_with_passes": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "44c75331cf2ac3355dc8a2c5c64e4b02ff12f0c8fc641713850737a00b4f8385",
    },
    "recalc_zero_only": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "559140f0e0f3dffb02c06505bd267bf6514dd210a5d6951a8711d2557627922b",
    },
    "smooth": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "5f9bbc3fcf5bd2e5ac385597b460a165f0d6062cba73f39d19bf14ec143c8cb7",
    },
    "smooth_centre": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "a34299b4f35981d1355bd68911710592eca7d76b906e2a98bf9a3ac167c1f72c",
    },
    "smooth_invalid": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "cad5f8a77ec06817c28d674bffb379fa9914339191552c5fa3f8d153cb5caa3e",
    },
    "smooth_noise": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "e83cb088e352be99465e0fe9a861f9d25c9c9b09eb19df2d59a2fa380fb92189",
    },
    "smooth_overrun": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "c3be269d514f143027d6dd26dde26ccdaf7701842a299bba49604f1c89607937",
    },
    "smooth_pairs": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7bb734c3a1e72ab57554cd6014b2ec77cb0bf316224a71a492d48df99c1915cc",
    },
    "smooth_selected": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "42d523800b076eb06bdf67d3071979ee0aac21a622d7e8ffc8b320a2a3386154",
    },
    "smooth_third_only": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "fe249c0d0b5dedfd511143c9b700d02d1022fb6a05f08934f5de815b2fd43528",
    },
    "smooth_three": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "a98727e211b24d568714948035975c05b0a1dcfc583ee2eb0c5032c2f027fca9",
    },
    "untouched": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "cad5f8a77ec06817c28d674bffb379fa9914339191552c5fa3f8d153cb5caa3e",
    },
}


MESSAGES = [":G,0,0,0,32767,1", ":g,0,0,0,32767,1", ":o,0,0,0,32767,1", ":n,1,0,0,0",
            ":y,0,7,-1,-1,-1,-1,1,1.0", ":G,0,0,0,32767,16"]
# what the exe printed for MESSAGES with the recipe's verbose flag, from its first edit on
SAID = """\
:G,0,0,0,32767,1
Normalize face Normal Vector: 0-0  Poly: 49
Normalize face Normal Vector: 0-0  Poly: 50
No polygons for mdl 0 mesh 0 vertex 41
:g,0,0,0,32767,1
MDL 0 Mesh 0: Normalize face vector 49 problem.
  2: -0.061037, -0.183111, 0.000000
  2: -0.061037, -0.183111, 0.000000
  3: 0.061037, -0.183111, 0.027467
MDL 0 Mesh 0: Normalize face vector 50 problem.
  12: -0.305185, 0.000000, 0.079348
  13: -0.183111, 0.000000, -0.003052
  42: -0.183111, 0.000000, -0.003052
MDL 0 Mesh 0: Normalize Vertex 41 vector problem.  0.000000 values
  0.000000, 0.000000, 0.000000
MDL 0 Mesh 0: Normalize Vertex 42 vector problem.  1.000000 values
  0.000000, 0.000000, 0.000000
MDL 0 Mesh 0: Normalize Tangent Vector: 41
:o,0,0,0,32767,1
Duplication (0,0): Total Vertexes: 43  Removed: 0   0.000000 pct
:n,1,0,0,0
Error: Block lks not found in file 0
Error: GetSkeleton: Block 0, -1 specified is not a Skeleton.
ERROR: -1 Unable to get joint parameters for MINS 0 Bone 0
GenerateSurface: trb:0  MDL:0  Mesh:0  Count:53
:y,0,7,-1,-1,-1,-1,1,1.0
Reconcile Normals: Invalid Mesh - #7 in MDL #0
:G,0,0,0,32767,16
Normalize face Normal Vector: 0-0  Poly: 49
Normalize face Normal Vector: 0-0  Poly: 50
No polygons for mdl 0 mesh 0 vertex 41
:X
"""


def test_messages_match_the_exe():
    files = world(MESSAGES, flags=1)
    lower = {k.lower(): v for k, v in files.items()}

    class Files:
        def read(self, path):
            return lower.get(path.lower())

    said = []
    build._main(build.Build(Recipe(files[RECIPE]), Files(), log=said.append))
    text = "".join(said)
    assert text[text.index(MESSAGES[0]):] == SAID


@pytest.mark.parametrize("edit", [
    # mesh 5 of a 4 mesh MDL: the exe reads its vertex count from past the end of the parse
    ":G,0,5,0,32767,1",
    ":g,0,9,0,32767,1",
    # no tangent slot, so the exe writes tangents over the index stream it then walks
    ":G,1,2,0,32767,1",
])
def test_edits_the_exe_reads_garbage_through_are_refused(edit):
    with pytest.raises(build.ModelError):
        _out(world([edit]))
