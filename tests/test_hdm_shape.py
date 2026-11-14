"""
The vertex shaping mesh edits (mesh_shape.py) against FF XIII HD Models.exe.
Every input is made up here: a mesh block of four small meshes (int16 and
float32 positions, skinned and not, some sharing vertices), run through
each command with selection volumes of every kind. The hashes are what the
real exe wrote for these exact inputs under Wine, so a pass means byte for
byte
"""

import hashlib
import random
import struct

import pytest

from patcher.mods.hdmodels import build
from tests import hdm_world as W

PATH = "chr\\pc\\c005\\bin\\c005.win32"
OUT = "chr\\pc\\c905\\bin\\c905.win32"
RECIPE = "Data\\T.txt"
TRB = "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb"

# float32 positions, everything after them 4 bytes on
FLOATS = ((0x00, 0x0, 1, 3), (0x0C, 0x2, 3, 4), (0x10, 0xF, 6, 4), (0x14, 0xE, 3, 4),
          (0x18, 0x3, 3, 4), (0x1C, 0x8, 2, 2), (0x20, 0xD, 3, 4))
# no skin and no colour, for the edits that go looking for them
BARE = ((0x00, 0x0, 4, 4), (0x08, 0x2, 3, 4), (0x18, 0x8, 2, 2))


def vertex(r, floats=False, pos=None, bones=0):
    if pos is None:
        pos = [r.randint(-12000, 12000) for _ in range(3)]
    if floats:
        head = struct.pack(">3f", *(p / 32767 for p in pos))
    else:
        head = struct.pack(">4h", *pos, 1)
    rest = bytes(r.randint(0, 255) for _ in range(3)) + b"\0"
    rest += bytes(r.choice([0xFF] + list(range(bones))) if bones else 0xFF for _ in range(4))
    rest += bytes(r.randint(0, 255) for _ in range(8))
    rest += struct.pack(">2H", r.randint(0x3000, 0x3C00), r.randint(0x3000, 0x3C00))
    return head + rest + bytes(r.randint(0, 255) for _ in range(0x24 - len(head) - len(rest)))


def envds(names, count):
    return [(n, list(range(k, count, len(names))), [0x40 + j for j in range(len(range(k, count, len(names))))])
            for k, n in enumerate(names)]


def faces(r, count, n):
    return [tuple(r.randrange(count) for _ in range(3)) for _ in range(n)]


def block() -> bytes:
    r = random.Random(13)
    a = [vertex(r, bones=3)[:0x20] for _ in range(24)]
    b = [vertex(r, floats=True, bones=2) for _ in range(16)]
    # c sits a few units off a's first ten, d has a's first ten exactly (position and normal)
    near = [[v + r.randint(-40, 40) for v in struct.unpack(">3h", x[:6])] for x in a[:10]]
    c = [vertex(r, pos=p, bones=4)[:0x20] for p in near] + [vertex(r, bones=4)[:0x20] for _ in range(10)]
    d = [x[:12] + vertex(r)[12:0x20] for x in a[:10]] + [vertex(r)[:0x20] for _ in range(2)]
    meshes = [
        W.wrb_mesh_from(a, faces(r, 24, 8), envds=envds((b"root", b"spine", b"L_arm"), 24), name=b"skin"),
        W.wrb_mesh_from(b, faces(r, 16, 6), layout=FLOATS, envds=envds((b"spine", b"neck"), 16), name=b"hair"),
        W.wrb_mesh_from(c, faces(r, 20, 7), envds=envds((b"L_arm", b"root", b"head", b"spine"), 20), name=b"cape"),
        W.wrb_mesh_from(d, faces(r, 12, 4), layout=BARE, name=b"bare"),
    ]
    return W.wrb([meshes[:2], meshes[2:]])


def world(edits, version=42, flags=0) -> dict:
    blocks = [b"SEDBabc".ljust(8, b"\0") + bytes(range(40)), block()]
    trb = W.trb(blocks, tags=[b"abc", b"brw", b"m1", b"m2"], names=[b"F00\\one", b"mesh", b"meta1", b"meta2"])
    return {"models\\orig\\" + PATH + ".trb": trb,
            RECIPE: W.recipe([PATH], OUT, [(0, 0), (0, 1), (0, 2), (0, 3)], edits=list(edits) + [":X"],
                             imgb="no.imgb", version=version, flags=flags)}


# selection volumes in the meshes' own units (positions run about -0.37 to 0.37)
BOX = [":e,0,0.4,0.5,0.6,0,0,0,0.05,-0.05,0"]
VOLUMES = {
    "box200": [":e,200,-0.2,0.15,-0.3,0.2,-0.25,0.3"],
    "box": BOX,
    "box_turned": [":e,1,0.5,0.3,0.7,20,-35,50,0,0,0.02"],
    "round_z": [":e,2,0.5,0.45,0.8,0,0,0,0,0.05,0"],
    "round_z_turned": [":e,2,0.6,0.35,0.6,10,25,-40,0.02,0,0"],
    "round_y_turned": [":e,3,0.45,0.8,0.5,15,30,-10,0,0,0"],
    "cut_box": BOX + [":e,-101,0.15,0.2,0.8,0,0,30,0.05,-0.05,0"],
    "cut_round": BOX + [":e,-102,0.2,0.2,0.8,5,0,0,0,0,0"] + [":e,-103,0.1,0.8,0.15,0,0,0,0.1,0,0.1"],
    "cut201": BOX + [":e,-201,-0.05,0.05,-0.4,0.4,-0.4,0.4"],
    "several": [":e,0,0.2,0.3,0.8,0,0,0,-0.2,0,0", ":e,102,0.25,0.25,0.8,0,0,0,0.2,0,0",
                ":e,201,-0.02,0.02,-0.4,0.4,-0.4,0.4", ":e,103,0.15,0.8,0.15,0,45,0,0,0,0",
                ":e,-100,0.05,0.05,0.8,0,0,0,0,0,0"],
    "switched_off": BOX + [":E,0"],
}

V_ALL = [":V,0,0,0,23,0,0,0.1,-0.2,0.3", ":V,0,0,-5,-1,1,24,0.5,-0.25,0", ":V,0,0,0,32767,2,24,0.001,-0.002,0",
         ":V,0,0,3,3,3,24,1.5,70000.0,0", ":V,0,0,10,20,4,20,0.5,1.2,0", ":V,0,0,0,32767,5,8,-0.5,0.7,1.5",
         ":V,0,0,0,32767,6,0,1000.7,-2000.2,0", ":V,0,0,7,0,7,20,300,-5,0", ":V,0,0,1,9,8,0,100,-100,40000.5",
         ":V,0,0,0,3,9,0,0.25,-0.5,1e38", ":V,0,0,0,32767,10,20,0.5,10,1", ":V,0,0,0,32767,10,16,1.5,-20,0",
         ":V,0,0,0,32767,11,20,251,250,249", ":V,0,0,0,1,12,0,1,1,1", ":V,0,0,20,10,0,0,1,1,1",
         ":V,0,0,0,99999,11,20,1,2,3", ":V,0,0,-99999,0,11,20,1,2,3", ":2V,0,0,1,4,8,3,24,-0.5,0.125,0",
         ":2V,0,0,0,0,2,6,0,7,9,0", ":3V,0,0,1,15,22,1,24,-3,3,0", ":V,0,1,0,32767,9,0,0.1,0.2,0.3"]
V_SELECTED = [":V,0,0,0,32767,111,20,251,251,251", ":V,0,0,0,32767,101,24,-0.5,0.5,0",
              ":V,0,0,0,32767,110,20,0.5,10,3", ":V,0,1,0,32767,100,0,0.01,0.02,0.03",
              ":V,0,1,0,32767,109,0,0.01,0.02,0.03"]
SHADE = [f":u,0,0,{fl},0,32767,0.2,1.0,-0.3,{st},{sh},96" for fl, st, sh in
         ((0, 1.0, 0), (1, 0.5, 3), (2, 1.0, -5), (4, 2.0, 0), (8, -1.0, 7), (0x10, 0.8, 0), (0x20, 1.0, 0),
          (0x40, 1.0, 10), (0x60, 1.0, 0), (0x80, 1.0, 0), (83, 1.0, 0), (0x3B, 0.7, -2), (0x76, 0.6, 1),
          (0x2C, 1.0, 300), (0x97, 0.3, -300))]
SPIN = [":s,0,{m},1,0,32767,0.02,0.01,-0.03,10.0,0.0,0.0", ":s,0,{m},0,0,32767,0,0,0,0.01,-0.02,0.003",
        ":s,0,{m},2,3,30,0.02,0.01,-0.03,1.1,0.9,1.05", ":s,0,{m},5,0,32767,0.1,0.2,0.3,-25,40,90",
        ":s,0,{m},3,0,32767,1,1,1,1,1,1"]
LIMB = "0.01,0.02,0.35,-0.01,-0.02,-0.35"
EXPAND = [":H,0,0,7,0,0,0.35,0,0,-0.35,0,0,0,1.0,0.01,45", ":H,0,0,0,0,0,0.35,0,0,-0.35,0,0,0,1.2,0,60",
          ":H,0,0,1,0,0,0.35,0,0,-0.35,0.05,0,0,0.5,0,90", ":H,0,0,18,0,0,0.35,0,0,-0.35,0,0,0,1,0.002,30",
          ":H,0,0,76,0,0,0.35,0,0,-0.35,0,0,0,0.8,0,80"]

CASES = {
    "untouched": [],
    "set_every_kind": V_ALL,
    "set_float_mesh": [":V,0,1,0,32767,0,0,0.1,0.2,0.3", ":V,0,1,2,5,9,0,-0.1,0.25,0.5",
                       ":V,0,1,0,32767,11,24,1,2,3", ":2V,0,1,1,0,32767,1,28,0.5,0.25,0"],
    "rescale": [":*,0,0,0,32767,2,1.0,0.006", ":*,0,0,5,-3,5,1.07,-0.01", ":*,0,0,0,0,7,0.9,0.0",
                ":*,0,1,0,32767,7,1.5,-0.1", ":*,1,0,4,8,1,-1,0"],
    "shade": SHADE + [":u,0,0,5,0,32767,0,0,0,1,0,96", ":u,0,0,1,3,3,0,1,0,1,0,300",
                      ":u,0,1,83,0,32767,0.0,1.0,0.0,1.0,0.0,96", ":u,1,1,0,0,32767,1,0,0,1,0,0",
                      ":u,0,9,0,0,1,1,1,1,1,0,0"],
    "uv_map": [":U,0,0,0,32767,24,1.0,0.5,0.0,0.25", ":U,0,0,0,10,26,-2,0,3,-0.5", ":2U,0,0,1,-10,-1,28,0.5,0.5,0.5,0",
               ":U,0,0,5,5,24,100000,0,0,0", ":U,0,1,0,32767,28,1,1,1,0", ":U,9,0,0,1,24,1,1,1,1"],
    "uv_copy": [":W,1,1,0,0,0,32767,24,20", ":W,0,0,0,0,0,32767,20,24", ":2W,1,1,1,0,0,1,0,100,8,16",
                ":W,0,1,0,1,0,32767,24,20", ":W,1,9,0,0,0,1,0,0", ":W,0,0,9,0,0,1,0,0"],
    "transform_int16": [line.format(m=0) for line in SPIN],
    "transform_float": [line.format(m=1) for line in SPIN],
    "find_vertex": [":z,0,0,0,0.1,0.2,0.3", ":z,0,0,0,-0.084658,0.212745,0.349101", ":z,0,0,1,0,0,0", ":z,1,0,0,0,0,0", ":z,0,0,99,0,0,0"],
    "align": [":a,0,0,1,0,30000", ":a,1,0,0,0,65535", ":a,0,0,0,0,0", ":a,1,0,1,1,500", ":a,1,1,0,0,500",
              ":a,0,1,1,0,40000", ":a,0,9,0,0,5"],
    "expand": EXPAND + [":H,0,0,512,0,0,0.35,0,0,-0.35,0,0,0,1,0,70", ":H,0,0,519,0,0,0.35,0,0,-0.35,0,0,0,0.9,0,50",
                        ":H,0,0,1024,0,0,0.35,0,0,-0.35,0,0,0,1,0,70", ":H,0,0,7,0,0,0.35,0,0,-0.35,0,0,0,0.9,0,50",
                        ":H,0,1,7,0,0,0.35,0,0,-0.35,0,0,0,1.1,0,50", ":H,0,9,7,0,0,0,0,0,0,0,0,0,0,0,0"],
    "limb_girth": [f":L,0,0,{k},{LIMB},{g},0.6,{bf},{af}" for k, g, bf, af in
                   ((1024, 0.96, 0.25, 0.0), (0, 1.1, 0.5, 0.5), (0x0F3, 0.9, 0.2, 0.3), (0x3F, 1.05, 0.1, 0.0),
                    (0x93A, 0.8, 0.4, 0.6), (0xC0F, 1.2, 0.0, 1.0), (0x2C4, 0.97, 0.3, 0.2))]
                  + [f":L,0,1,0,{LIMB},1.2,0.6,0.5,0.5", ":L,0,9,0,0,0,0,0,0,0,0,0,0,0"],
    "xy_modify": BOX + [f":Q,0,0,{fl},0.9,0.35,0.01,-0.02,0.03,{off}" for fl, off in
                        ((5, 0), (1, 0.01), (6, 0), (12, 0.002), (0x1F, 0.05), (0x13, 0))]
                 + [":Q,0,1,15,1.1,0.5,0,0,0,0", ":Q,0,9,1,0,0,0,0,0,0"],
    "xy_modify_unboxed": [":Q,0,0,15,1.1,0.5,0,0,0,0", ":Q,0,0,7,0.95,-0.2,0.01,-0.02,0.03,0"],
    "limb_facing": [f":l,0,0,{k},{LIMB},{g},0.6,{bf},{af},{nx},{ny},{nz},{spr}" for k, g, bf, af, nx, ny, nz, spr in
                    ((0, 1.1, 0.3, 0.2, 0, 1, 0, 90), (0x15, 0.9, 0.2, 0.0, 1, 0, 0, 60),
                     (0x13F, 1.05, 0.0, 0.5, 0, 0, 1, 120), (0x3EA, 0.8, 0.5, 0.5, 0.3, 0.3, 0.9, 75),
                     (0x80, 1.2, 0.1, 0.1, -1, 0, 0, 180))]
                   + [f":l,0,1,0,{LIMB},1.1,0.6,0.3,0.2,0,1,0,90", ":l,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0"],
    "show_box": BOX + [":e,9999,0,0", ":V,0,0,-1,-1,11,20,1,2,3"],
    "show_cylinder": VOLUMES["round_z_turned"] + [":e,9999,0,1"],
    "show_several": VOLUMES["several"] + [":e,101,0.3,0.2,0.1,30,0,0,0.1,0.1,0.1", ":e,-202,0.1,0.1,0.2,0,0,0,0,0,0",
                                          ":e,9999,1,0", ":U,1,0,0,32767,24,1,1,1,0"],
    "show_flat": [":e,0,0,0.1,0.1,0,0,0,0,0,0", ":e,101,0,0,0.1,0,0,0,0,0,0", ":e,103,0.1,0.1,0.1,0,0,0,0,0,0",
                  ":e,9999,1,1"],
    "legacy": [":*,0,0,0,32767,7,-0.2,0.2,-0.2,0.2,-0.3,0.3,1.1,0.0",
               f":L,0,0,0,{LIMB},1.1,-0.3,0.3,-0.3,0.3,-0.4,0.4,0.6,0.5,0.5",
               ":Q,0,0,5,0.9,0.35,0.01,-0.02,0.03,-0.3,0.3,-0.3,0.3,-0.3,0.3,0",
               f":l,0,0,0,{LIMB},1.1,-0.3,0.3,-0.3,0.3,-0.4,0.4,0.6,0.3,0.2,0,1,0,90"],
    "verbose": [":a,0,0,1,0,30000", f":L,0,0,0,{LIMB},1.1,0.6,0.5,0.5", f":l,0,0,0,{LIMB},1.1,0.6,0.3,0.2,0,1,0,90"],
}
for _name, _lines in VOLUMES.items():
    CASES[f"set_{_name}"] = _lines + V_SELECTED
    CASES[f"rescale_{_name}"] = _lines + [":*,0,0,0,32767,7,1.2,0.01", ":*,0,1,0,32767,5,0.8,0.0"]
for _name in ("box", "round_y_turned", "cut_round", "several"):
    _lines = VOLUMES[_name]
    CASES[f"shade_{_name}"] = _lines + [":u,0,0,83,0,32767,0.0,1.0,0.0,1.0,0.0,96", ":u,0,1,2,0,32767,1,0,0,1,0,0"]
    CASES[f"uv_{_name}"] = _lines + [":U,0,0,0,32767,24,1.0,0.5,0.0,0.25", ":W,1,1,0,0,0,32767,24,20"]
    CASES[f"transform_{_name}"] = _lines + [line.format(m=m) for m in (0, 1) for line in SPIN[:3]]
    CASES[f"align_{_name}"] = _lines + [":a,0,0,1,0,30000", ":a,1,0,0,0,20000"]
    CASES[f"expand_{_name}"] = _lines + EXPAND[:2] + [":H,0,1,7,0,0,0.35,0,0,-0.35,0,0,0,1.1,0,50"]
    CASES[f"limbs_{_name}"] = _lines + [f":L,0,0,1024,{LIMB},0.96,0.6,0.25,0.1", f":L,0,1,0,{LIMB},1.2,0.6,0.5,0.5",
                                        f":l,0,0,0,{LIMB},1.1,0.6,0.3,0.2,0,1,0,90"]
    CASES[f"show_{_name}"] = _lines + [":e,9999,0,0"]
OPTIONS = {"legacy": dict(version=41), "verbose": dict(flags=1)}

GOLDEN = {
    "align":
        "4973351b6934a790ad039bc648d6d0b7a869f2498fb8bbc380a9974ec9fcbadc",
    "align_box":
        "0311aaa851e1ca210d4dbaaae2efb2f4ab10f842f8d03ec2ad2591ca4c45eb99",
    "align_cut_round":
        "2ab229d61f82ad273f51440fd7a58090b58c4273c1f149205f4985c3d1f176f7",
    "align_round_y_turned":
        "86761bb84a7aa11ae8d2f6ae4046456b2fc37b3505377019dcd261e5776a7b98",
    "align_several":
        "b8f9ec0a34a203d420e613e1b7ff8eebe0073fcd840f18676198a17609ac6a93",
    "expand":
        "81172d9f83f4d9a6f9bcfaa7086eea77edd5d33b6bd856f0180f06b32bf75221",
    "expand_box":
        "99e95e2fb7d10e1777f8866723d228ddcb10b88688fd1b0a73d513fe177eac87",
    "expand_cut_round":
        "ebeddcbf06cf901f5b136a49f21c0fa83c136d73b07fc767b3ede78b0cdca2fa",
    "expand_round_y_turned":
        "c5425df3e73d3732b3b9f9aa2afcbf7df1f0c11010e6177ef326793164a1e45e",
    "expand_several":
        "51bc9bc7af0f0eec710cf72e46d41b0a545323e1eeeb6ace407787827e626d7d",
    "find_vertex":
        "eb86ea3b4d9b00501351df7e22fe7a8f2148d341c7c523a38be4ae7a0cfbcb2d",
    "legacy":
        "4bcc4ddb7abb41e0368cd12f6830380cc905a7467b27ad62f10e6e42dbf6f3e9",
    "limb_facing":
        "9ec4a2f4e595d569db4bed8f5ab57ed23e36916c6b6c6502fa0b313d5b699391",
    "limb_girth":
        "df26971eef98c4e4529475adb786ca8013bbcf440d76f081dd0697cb6fb1425d",
    "limbs_box":
        "f5060e224393f370bc7acf185ee0b730cd93f65318fad70d0fe82ecd1d96028e",
    "limbs_cut_round":
        "175d05250cea547afa1bfd6c8a6b715edf7260b7d700ceb616283813092f2a3e",
    "limbs_round_y_turned":
        "f183b086b91c90c35fd3254b793868340fa7e08884254bf8da67c8a127cee574",
    "limbs_several":
        "8d329df85c386fc259dd50f80052915640e53d86ae972e80d960771735cafe63",
    "rescale":
        "266a5fda2500163d3c2f14676beefd7de1ba9971a232e53f78c5265388895adf",
    "rescale_box":
        "d968c0d34513b5c41d28d05bf6cc9fa074df285c32b69296a774d194174a3d46",
    "rescale_box200":
        "cfc3a944f8b559dc10ffd16c48d022c83ef961bc0e5a754717f3f669d591f49e",
    "rescale_box_turned":
        "a0e49914d50f9c0fc581d96bd5d183a4028f3f9e86d6edf55d0782df3533b542",
    "rescale_cut201":
        "7db35597d47646334a3832f0290b39d2cb1863c895c696f86825cbcdf47e6337",
    "rescale_cut_box":
        "d968c0d34513b5c41d28d05bf6cc9fa074df285c32b69296a774d194174a3d46",
    "rescale_cut_round":
        "784340e9ee9060a8a03655ff645381bfa6a14810d074292e541432634b590c4e",
    "rescale_round_y_turned":
        "470a84da43c8ca1f725eb39cbec2e34955ae84335a191d45ff8b94181802e48f",
    "rescale_round_z":
        "b0e90d66a974ac3456c1f6aeaab409b0b519b52eb0bb92a4f55c56a3f702b761",
    "rescale_round_z_turned":
        "83a4a9bd1fdc357abee81d21275e394995b462989a343b95574a15418b84e225",
    "rescale_several":
        "ab7e06d287a1f29e5822ae68cc3ba6b8f5f54fbb3afea66c1ec10baa5d5aa86a",
    "rescale_switched_off":
        "5f0b1d7322afc2374fba815ab9bd587257e2809d09fa15a21f3c7cf3fc5b1959",
    "set_box":
        "cf78074aa12f14db6da90172e6e10226c85f5efc60839328cf6fbad961869bdb",
    "set_box200":
        "fd08d9bff6fa3e21612fade7951116ae1eef84ee97e9fe02b50491c2e6f2a32b",
    "set_box_turned":
        "38c9334bd24a46eade6bdfb633b0e86183b98c49ba77dab23a01799ce4ecef9e",
    "set_cut201":
        "111b0c097bcdcd6c7cad6a0ac4e68e4e4221226e998a7fbb6f0b909da3ab6975",
    "set_cut_box":
        "3a90df7a8e4a5580fa2a25dfca06ac410186a653cbbe2c3ee8ee1b7711b77b5f",
    "set_cut_round":
        "fc862190fbeff7129de9957d385c599659e6afcf0a99fc8ae061059ab67da162",
    "set_every_kind":
        "d23b55cee6b1b5210a5f2e8c55c511c87554a901b5c0aa55f8a4206d15123434",
    "set_float_mesh":
        "c213ae1bc405694996f73dbc7a0ca441e93f6d60ac59fffe07d7229f4a2a840b",
    "set_round_y_turned":
        "b30bc92be828cd2bc8814eba26589ea8477d11d15ec769a3d8723f2ec5063b14",
    "set_round_z":
        "c579d0f63f945de383e9af13d3b5aa95bef2c67ca0c81389011e17013fa542da",
    "set_round_z_turned":
        "10df5dc04070676b28dd04a730d981493653c93299ff1d485227d0c10e5ac399",
    "set_several":
        "91b9af7252389f4d7bfdcb6ee268ef6a4818c7ea9605bf32d26ae74e8dd3c20a",
    "set_switched_off":
        "6e2f41255300d5cc12ed87102f1e59cb2f328d0f497772aba790eb7fc6f6c8f7",
    "shade":
        "e374b830722734b14427f72956473383e70112aeeec8ba37740d3238f2e9b48b",
    "shade_box":
        "52e1d18cdcb438c4dae3acbd3354d4460f3d3b9a3e3ed516c9d3d0e540ba6305",
    "shade_cut_round":
        "8eaf818e04e5b656b2d49110e9eb7ab0fb813545ef5985d93ac8b61b075d62eb",
    "shade_round_y_turned":
        "a23ab7a55905ec3ea211df58adb86bcaf6884216d45453a4e497ae4d852a6706",
    "shade_several":
        "aa0176bac15cf8ca973f0b42f784e514e59e61a2705ec6a6b37df6b35f8e9208",
    "show_box":
        "10acca414087c122f3b11cbb62ca553bdcc4acd4ec48c4d931c10e21fbb79dfd",
    "show_cut_round":
        "c70f0ebc127de98def354a0c60ba91cdf4bd154c18823eef00be745a7b6cb33c",
    "show_cylinder":
        "95bf15e201a528f309d166ddf78d5c04bc76166603e4f02bdba8e4d8ec5c07ee",
    "show_flat":
        "58ae2bd3f56e294d661335e167b1e51590be649d019affb952ea19079838b257",
    "show_round_y_turned":
        "eb86ea3b4d9b00501351df7e22fe7a8f2148d341c7c523a38be4ae7a0cfbcb2d",
    "show_several":
        "3f2cddbddee60410dc74814aaaf5891e8de732acfae2c26653d102716c899a2e",
    "transform_box":
        "6750fd8c7d13f56e13e3904250e2c232d0fee20d36abcf81dddb823934c8a057",
    "transform_cut_round":
        "4bde5fe7e5e24843e05984e9adeb14c9e5adf297c05a597dd3340f900b844ed7",
    "transform_float":
        "a74f111243d6edb6dd58897cc6510bff94ab769af4881dcb5968ad75b95a3687",
    "transform_int16":
        "4ba6a44df6f2c20d04175aae85eca31d72dc07ecbceae9499ebcc80e66c64687",
    "transform_round_y_turned":
        "9954ea7d8fec4c18643afbb7506b49e7ce5ba157300e5502f03a97e2438d902f",
    "transform_several":
        "a1926892061fbbf55758ef98406dee61a9f5539773b967624cad8b60943b504e",
    "untouched":
        "eb86ea3b4d9b00501351df7e22fe7a8f2148d341c7c523a38be4ae7a0cfbcb2d",
    "uv_box":
        "e92b476cec849188a8ac889ccbc2aaa2fe106f82fe554c880fdf7119966849fc",
    "uv_copy":
        "60588b2ddd363e199ed64b554fe60d441b32c6f54d811265bf0262efd8d3ad1b",
    "uv_cut_round":
        "608e643713792bdf687014fbce558f2cf7c76c196f48dd76f51b88ecc1393c9a",
    "uv_map":
        "964cb177195425ddad87feb7bd04638aa3a685fbeb32b3a7af1bdac157582eab",
    "uv_round_y_turned":
        "1aa5fa08dbc1c47eeb010ab43e60d4058cc599b7ad2d27f1436ee63c7179d663",
    "uv_several":
        "6359ba3a1f810b77ba00fe0444fce49e22b55401463997f9e85ce1750464edc8",
    "verbose":
        "db09bf560cf3c7ff42adf8c774d2bc3c2ffb78fc65e819a1e762e89047f74c19",
    "xy_modify":
        "abea0080cd0e7043ae02a9496341c62f90a675cf939a5e88cd7717b731d502f0",
    "xy_modify_unboxed":
        "57969d88b3382e84f85bf933baaea68de08491891a908d18c924b22559c7a6b8",
}


def _out(files) -> dict:
    lower = {k.lower(): v for k, v in files.items()}

    class Files:
        def read(self, path):
            return lower.get(path.lower())

    return build.run(files[RECIPE], Files())


@pytest.mark.parametrize("name", sorted(CASES))
def test_shaping_matches_the_exe(name):
    got = _out(world(CASES[name], **OPTIONS.get(name, {})))
    assert hashlib.sha256(got[TRB]).hexdigest() == GOLDEN[name]
