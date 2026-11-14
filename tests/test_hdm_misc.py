"""
The odd mesh edits (mesh_misc.py: :v :f :F :S :i :k) against FF XIII HD
Models.exe. One small made up model: a skeleton, then a wrb with two twin
meshes (same positions, different bones), a mesh to import into, one with
no triangles to merge into and one missing its tangents, plus the rig,
.obj and component catalog files the edits read. The hashes are what the
real exe wrote for these exact inputs under Wine; for :k, whose generator
the exe seeds off the clock, a copy with the seed patched to SEED
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
SEED = 5489

NO_TANGENT = W.WRB_LAYOUT[:-1]


def envd(name, vlist, step=7):
    return name, list(vlist), [(v * step + 40) & 0xFF for v in vlist]


def tris(n, count=None, k=0):
    return [((i + k) % n, (i * 3 + 1 + k) % n, (i * 7 + 2 + k) % n) for i in range(count or n)]


TWIN = [envd(b"root", range(30)), envd(b"arm", range(0, 20)), envd(b"leg", range(10, 30))]
MESH = W.wrb([
    [W.wrb_mesh(30, tris(30), envds=TWIN, seed=3, name=b"body"),
     W.wrb_mesh(30, tris(30, k=2), envds=[envd(b"root", range(30)), envd(b"tail", range(5, 30), 3)],
                seed=3, name=b"twin"),
     W.wrb_mesh(24, tris(24, k=1), envds=[envd(b"root", range(24)), envd(b"arm", range(12))], seed=8,
                name=b"target"),
     W.wrb_mesh(12, [], envds=[envd(b"root", range(12)), envd(b"leg", range(4, 12))], seed=5, name=b"empty")],
    [W.wrb_mesh(20, tris(20), envds=[envd(b"root", range(20))], stride=28, layout=NO_TANGENT, seed=2,
                name=b"notan"),
     W.wrb_mesh(16, tris(16, k=4), envds=[envd(b"root", range(16)), envd(b"arm", [3])], seed=6,
                name=b"spare")],
])
SKELETON = W.skl([W.bone(0, pos=(0.05, 0.1, -0.02)), W.bone(1, parent=0)], [b"root", b"arm"])


def rig_file() -> bytes:
    # groups: group, bone, name; then points: skipped, group, x, y, z, 4 bones, 4 weights
    groups = [(1, 0, "root"), (1, 1, "arm"), (1, 2, "brandnew"), (1, 3, "ROOT"), (2, 0, "leg")]
    points = [(1, 0.1, 0.2, 0.0, 0, 1, -1, -1, 200, 55, 0, 0), (1, -0.4, 0.5, 0.3, 0, 3, -1, -1, 100, 155, 0, 0),
              (1, 0.6, -0.2, -0.5, 2, 0, -1, -1, 128, 127, 0, 0), (2, -0.7, -0.6, 0.4, 0, 7, -1, -1, 255, 0, 0, 0),
              (1, 0.0, 0.0, 0.9, 1, 0, 2, -1, 50, 100, 105, 0), (1, 5.0, 5.0, 5.0, 0, -1, -1, -1, 255, 0, 0, 0)]
    lines = [str(len(groups))] + ["%d,%d,%s" % g for g in groups] + [str(len(points))]
    lines += ["9,%d,%.3f,%.3f,%.3f,%d,%d,%d,%d,%d,%d,%d,%d" % p for p in points]
    return ("\r\n".join(lines) + "\r\n").encode()


OBJ = ("# made up\r\no thing\r\nv 0.01 0.9 0.05\r\nv 0.02 0.91 0.05\r\nv 0.03 0.9 0.06\r\nv -2.0 3.0 0.5\r\n"
       "vn 0 1 0\r\nvn 0.5 0.5 -0.7\r\nvn 1 0 0\r\nvt 0.1 0.2\r\nvt 0.3 0.9\r\nvt 0.6 0.4\r\nvt 0.2 -0.1\r\n"
       "vb 0 0 1\r\nvb 1 0 0\r\nf 1/1/1 2/2/2 3/3/3\r\nf 1 3 4\r\nf 4//1 2//2 1//3")


def catalog(items) -> bytes:
    """
    a component catalog: a count, a 0x74 byte entry per component (name,
    +0x20 offset, +0x24 vertices, +0x26 triangles, +0x28 dwords per usage,
    +0x38 record size, +0x3a flags, +0x4c..+0x5c the scatter's numbers),
    then each one's vertex records and triangles
    """
    table, data = [], b""
    at = 4 + len(items) * 0x74
    for name, usages, verts, faces, flags, extra in items:
        rec = sum(d * 4 for d in usages.values()) + (4 if usages.get(0) else 0)
        body = b""
        for n, v in enumerate(verts):
            r = bytearray((0x40 + n * 13 + q * 7) & 0xFF for q in range(rec))
            struct.pack_into("<3f", r, 0, *v)
            body += bytes(r)
        body += b"".join(struct.pack("<3H", *f) for f in faces)
        e = bytearray(0x74)
        e[:len(name)] = name
        struct.pack_into("<IHH", e, 0x20, at, len(verts), len(faces))
        e[0x28:0x38] = bytes(usages.get(k, 0) for k in range(16))
        struct.pack_into("<HB", e, 0x38, rec, flags)
        for off, val in zip(range(0x4C, 0x60, 4), extra):
            struct.pack_into("<f", e, off, val)
        table.append(bytes(e))
        data += body
        at += len(body)
    return struct.pack("<I", len(items)) + b"".join(table) + data


USAGES = {0: 3, 2: 1, 3: 1, 8: 1, 13: 1, 14: 1, 15: 1}
SPIKE = [(0.0, 0.9, 0.0), (0.02, 0.92, 0.0), (0.0, 0.95, 0.03), (-0.01, 0.9, 0.02)]
CATALOG = catalog([
    (b"Spike", USAGES, SPIKE, [(0, 1, 2), (0, 2, 3)], 0x4, (0.02, 0.01, 1.0, 10.0, 0.3)),
    (b"plate_long_name_over_thirty_one_chars", {0: 3}, SPIKE[:3], [(2, 1, 0)], 0x5,
     (0.03, 0.02, 2.0, 0.0, 0.6)),
    (b"Ring", {0: 3, 2: 1}, SPIKE[1:], [(0, 1, 2)] * 3, 0xA, (0.01, 0.0, 0.5, 45.0, 0.0)),
])


def world(edits, **kw):
    blocks = [MESH, SKELETON, b"SEDBabc".ljust(8, b"\0") + bytes(range(40))]
    model = W.trb(blocks, tags=[b"brw", b"lks", b"abc", b"m1", b"m2"],
                  names=[b"mesh", b"skel", b"F00\\one", b"meta1", b"meta2"])
    return {"models\\orig\\" + PATH + ".trb": model,
            "Data\\rig.txt": rig_file(), "Data\\a.obj": OBJ.encode(),
            "Data\\b.obj": OBJ.replace("\r\n", "\n").encode() + b"\n",
            "Data\\none.obj": b"# nothing\nvt 0 0\n",
            "Data\\comp.bin": CATALOG, "Data\\comp2.bin": catalog([(b"Other", USAGES, SPIKE[:2], [(0, 1, 1)], 0, ())]),
            "Data\\empty.bin": bytes(4),
            RECIPE: W.recipe([PATH], OUT, [(0, 0), (0, 1), (0, 2), (0, 3)], edits=list(edits) + [":X"],
                             imgb="no.imgb", **kw)}


BOX = ":e,0,0.8,50,0.8,0,0,0,0.05,0,0"
CASES = {
    "untouched": [],
    "weights_set": [":v,0,0,0,2,6,0,1,255,255,200,55,0,0"],
    "weights_set_back": [":v,0,0,0,-4,-1,2,0,1,255,10,20,30,0", ":v,0,2,0,0,32767,0,255,255,1,90,0,0,165"],
    "weights_rejected": [":v,0,0,0,0,3,3,0,0,0,1,1,1,1", ":v,0,0,0,9,3,0,0,0,0,1,1,1,1",
                         ":v,5,0,0,0,1,0,0,0,0,1,1,1,1", ":v,0,0,1,0,20,0,1,0,0,0,0,0,0",
                         ":v,0,0,3,0,20,a,b,c,d,0,0,0,0", ":v,0,0,7,0,20,0,1,0,0,0,0,0,0"],
    "weights_copy": [":v,0,0,2,0,14,0,1,0,0,0,0,0,0"],
    "weights_copy_back": [":v,0,1,2,3,25,0,0,0,0,0,0,0,0", ":v,0,0,2,0,10,0,1,0,0,0,0,0,0"],
    "weights_selected": [":e,0,0.6,0.6,0.6,0,0,0,0,0,0", ":v,0,0,16,0,32767,1,2,255,255,128,127,0,0",
                         ":v,0,2,18,0,32767,0,1,0,0,0,0,0,0"],
    "rig": [":f,0,2,0,15,0,Data\\rig.txt"],
    "rig_tail": [":f,0,2,-8,-1,0,Data\\rig.txt", ":f,0,0,20,25,0,Data\\rig.txt"],
    "rig_missing": [":f,0,2,0,15,0,Data\\nope.txt", ":f,0,2,9,3,0,Data\\rig.txt"],
    "obj": [":F,0,2,0,1,0,1,0,1,0,x,Data\\a.obj"],
    "obj_kinds": [":F,0,2,1,0.5,0.1,2,0,1,-0.2,x,Data\\a.obj", ":F,0,0,8,1,0,1,0,1,0,x,Data\\b.obj",
                  ":F,0,2,5,0.3,0,0.3,0,0.3,0,x,Data\\b.obj"],
    "obj_odd": [":F,0,2,6,1,0,1,0,1,0,x,Data\\a.obj", ":F,0,0,7,1,0,1,0,1,0,x,Data\\b.obj",
                ":F,0,2,0,1,0,1,0,1,0,x,Data\\none.obj", ":F,0,2,0,1,0,1,0,1,0,x,Data\\gone.obj"],
    "component": [":S,0,2,1,1,1,0,0,0,0,0,0,Spike,Data\\comp.bin"],
    "components": [":S,0,2,0.5,2,1,0.3,-1.2,2.5,0.01,-0.02,0.03,spike,Data\\comp.bin",
                   ":S,0,0,1,1,1,0,0.7,0,0,0,0,PLATE_long_name_over_thirty_one_xx,Data\\comp.bin",
                   ":S,0,0,1,1,1,0,0,0.1,0,0,0,ring,Data\\comp.bin",
                   ":S,0,2,1,1,1,0,0,0,0,0,0,Other,Data\\comp2.bin"],
    "components_odd": [":S,0,2,1,1,1,0,0,0,0,0,0,BoundingBox,Data\\comp.bin",
                       ":S,0,2,1,1,1,0,0,0,0,0,0,Nope,Data\\comp.bin",
                       ":S,0,2,1,1,1,0,0,0,0,0,0,Spike,Ygone.bin", ":S,0,2,1,1,1,0,0,0,0,0,0,Spike,Data\\empty.bin"],
    "merge_into_empty": [":v,0,3,0,0,0,255,255,255,255,0,0,0,0", ":i,0,0,0,3,0,2,2"],
    "merge_reuse": [":v,0,3,0,0,0,255,255,255,255,0,0,0,0", ":i,0,0,0,3,1,2,2"],
    "merge_stuck": [":i,1,1,1,1,1,9,12", ":i,0,2,0,2,5,10,20"],
    "merge_delete": [":i,1,1,1,1,3,9,12"],
    "merge_delete_mdl": [":i,1,1,1,1,7,9,12"],
    "merge_decl": [":i,1,0,0,0,1,0,5", ":i,0,9,0,0,1,0,5"],
}
KW = {}

# :k, run against the exe with its mt19937 seeded to SEED
SCATTER = {
    "scatter": [":n,0,0,0,0", BOX,
                ":k,0,2,6,0,0.5,1,0.5,1,1,3,Data\\comp.bin,Spike,Ring,plate_long_name_over_thirty_one_chars"],
    "scatter_cluster": [":n,0,0,0,0", BOX, ":k,0,2,10,4,0.3,1,0.8,0.5,0.5,2,Data\\comp.bin,Spike,Ring"],
    "scatter_keep": [":n,0,0,0,0", BOX, ":k,0,2,5,1,0.5,1,0.5,1,2,2,Data\\comp.bin,Ring,Spike",
                     ":k,0,0,5,13,0.5,1,0.5,1,1,2,Data\\comp.bin,Spike,Ring"],
    "scatter_missing": [":n,0,0,0,0", BOX, ":k,0,2,3,0,0.5,1,0.5,1,1,2,Data\\comp.bin,Nope,Spike"],
    "scatter_no_surface": [BOX, ":k,0,2,3,0,0.5,1,0.5,1,1,1,Data\\comp.bin,Spike"],
}


def _out(files, seed=None) -> dict:
    lower = {k.lower(): v for k, v in files.items()}

    class Files:
        def read(self, path):
            return lower.get(path.lower())

    b = build.Build(Recipe(files[RECIPE]), Files())
    if seed is not None:
        b.state["seed"] = seed
    return build._main(b)


def _hashes(got):
    return {k: hashlib.sha256(v).hexdigest() for k, v in got.items()}


@pytest.mark.parametrize("name", sorted(CASES))
def test_misc_edits_match_the_exe(name):
    assert _hashes(_out(world(CASES[name], **KW.get(name, {})))) == GOLDEN[name]


@pytest.mark.parametrize("name", sorted(SCATTER))
def test_scatter_matches_the_seeded_exe(name):
    assert _hashes(_out(world(SCATTER[name]), seed=SEED)) == GOLDEN[name]


@pytest.mark.parametrize("edit", [
    # the vertex is the last one "arm" holds: the exe's list shuffle memmoves 4GB
    ":v,1,1,0,3,3,255,255,255,255,0,0,0,0",
    # a bone-weighted vertex merged: the ENVD lookup gets a NULL name
    ":i,0,0,0,2,0,1,1",
    # no group for a rig with points: the exe compares names through NULL
    ":f,0,2,0,5,0,Data\\nogroups.txt",
])
def test_edits_the_exe_crashes_on_are_refused(edit):
    files = world([edit])
    files["Data\\nogroups.txt"] = b"0\r\n1\r\n9,1,0,0,0,0,-1,-1,-1,255,0,0,0\r\n"
    with pytest.raises(build.ModelError):
        _out(files)


def test_the_generator_is_mt19937():
    from patcher.mods.hdmodels.mesh_misc import MT19937
    rng = MT19937(5489)
    first = [rng() for _ in range(3)]
    for _ in range(9996):
        rng()
    # std::mt19937's own check: its 10000th number from the default seed
    assert first == [3499211612, 581869302, 3890346734] and rng() == 4123659995


GOLDEN = {
    "component": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "4f446c4d83c98feac9c7b2b56f678915751150dcb9fc8b721e861b9928ac32bb",
    },
    "components": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "ede78cbf8753fdd99f3862d8705ccda60fd9f17f83a0b82c2176ee397d7b7a44",
    },
    "components_odd": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7b234995983e054079ccdb22a474b9c548f4d4bb30afccb3c0dc514ea46a9862",
    },
    "merge_decl": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7b234995983e054079ccdb22a474b9c548f4d4bb30afccb3c0dc514ea46a9862",
    },
    "merge_delete": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "0846c52fc0e2535fdb224b5cadccaef41360bd4886e77168f9c8e7b37381cf14",
    },
    "merge_delete_mdl": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "de7308b18a93cf164a54b90cdf6995a17e7150d2e3848d5f5c63bfd981b04dfb",
    },
    "merge_into_empty": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "dfd1d4fb9dc3fd97bde489cebec323030a9eed7bcc2b1a1c89c3043ad4059f58",
    },
    "merge_reuse": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "dfd1d4fb9dc3fd97bde489cebec323030a9eed7bcc2b1a1c89c3043ad4059f58",
    },
    "merge_stuck": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7b234995983e054079ccdb22a474b9c548f4d4bb30afccb3c0dc514ea46a9862",
    },
    "obj": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "2f7b1160d0479e9200f5bf59f511cf2e20a452dc67077f485f0853c73ac72212",
    },
    "obj_kinds": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "b89d7855eccdfcd3df4e4032127731f05e9f32ed5f292e00f4d54a89a7c8f626",
    },
    "obj_odd": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "efb5ec1c930eb66ed504c140cbcfdf103ea0111aa5734502e84dbd68868b1717",
    },
    "rig": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "02cf7394d55a0e236244ff534d26c3d17de854e02ce7f7870e462bd285fdef7f",
    },
    "rig_missing": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7b234995983e054079ccdb22a474b9c548f4d4bb30afccb3c0dc514ea46a9862",
    },
    "rig_tail": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "d71694510fc44d40f7ab4fbfc0a02059662cd30f62ddb5a947c01b759b35a9c4",
    },
    "scatter": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7334fed99bdd41700f2d87263e095a06e53212e29b040aa5f3fa264b0bffd4d9",
    },
    "scatter_cluster": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "ac3a3cb02e6f721e8f4810e7f150988ba477d54371d2ac1f09d0acd7eb5f6058",
    },
    "scatter_keep": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "354fb40d45e91009080947c6ab0327e87340af03b8ceb9120ca0a66f61f19419",
    },
    "scatter_missing": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "f71277b8fa4a21e53bc3c88661b6c42fc86eae06a777c0788ffaa61fe9f11865",
    },
    "scatter_no_surface": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7b234995983e054079ccdb22a474b9c548f4d4bb30afccb3c0dc514ea46a9862",
    },
    "untouched": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7b234995983e054079ccdb22a474b9c548f4d4bb30afccb3c0dc514ea46a9862",
    },
    "weights_copy": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "54ca51a69f02d322577e4ca7d568ee9f01e55538fd5392ab0a899a84bc1d218f",
    },
    "weights_copy_back": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "11340219c604867fdce8ec52eb0bde6d69ed80a83af90029d58f660c19413254",
    },
    "weights_rejected": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7b234995983e054079ccdb22a474b9c548f4d4bb30afccb3c0dc514ea46a9862",
    },
    "weights_selected": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7b234995983e054079ccdb22a474b9c548f4d4bb30afccb3c0dc514ea46a9862",
    },
    "weights_set": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "6ea0bf3b1310a2db138c2d548157df524a395ec97b7c2dda173e9c6872850b1a",
    },
    "weights_set_back": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7d3cf56598ef373c729579038c147d4d9025559e6fe5537a969fcb3cae26cf40",
    },
}
