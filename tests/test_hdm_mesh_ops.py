"""
The structural mesh edits (mesh_ops.py: :R :O :C :D :B :M :I :A :c) against FF
XIII HD Models.exe. Two small made up models, built here: three meshes with
a couple of bone bindings each, and a second model to splice and compare
meshes from. The hashes are what the real exe wrote for these exact inputs
under Wine, so a pass means byte for byte
"""

import hashlib

import pytest

from patcher.mods.hdmodels import build
from patcher.mods.hdmodels.recipe import Recipe
from tests import hdm_world as W

PATHS = ["chr\\pc\\c005\\bin\\c005.win32", "chr\\pc\\c105\\bin\\c105.win32"]
OUT = "chr\\pc\\c905\\bin\\c905.win32"
RECIPE = "Data\\T.txt"
CMP = "models\\mod\\cmp.txt"


def tris(n, count=None, k=0):
    return [((i + k) % n, (i * 3 + 1 + k) % n, (i * 7 + 2 + k) % n) for i in range(count or n)]


HERO = W.wrb([
    [W.wrb_mesh(12, tris(12), name=b"one",
                envds=[(b"head", [0, 1, 2, 5, 9], [1, 2, 3, 4, 5]), (b"R_eye", [3, 4, 5, 6], [9, 8, 7, 6])]),
     W.wrb_mesh(9, tris(9, k=1), name=b"two", seed=3, envds=[(b"neck", [0, 2, 4, 6, 8, 1], [5] * 6)])],
    [W.wrb_mesh(6, tris(6, 4), name=b"three", seed=7)],
])
OTHER = W.wrb([
    [W.wrb_mesh(12, tris(12, k=2), name=b"other_one", seed=5,
                envds=[(b"spine", [1, 3, 5, 7], [4, 3, 2, 1]), (b"head", [0, 2], [7, 7]),
                       (b"L_eye", [8, 9, 10, 11, 4], [1] * 5)]),
     W.wrb_mesh(7, tris(7), name=b"other_two", seed=9, envds=[(b"hip", [0, 6], [2, 2])])],
    [W.wrb_mesh(5, tris(5, 3), name=b"other_three", seed=11, envds=[(b"tail", [4], [9])])],
], mins_big=True)


def model(mesh):
    blocks = [b"SEDBabc".ljust(8, b"\0") + bytes(range(40)), mesh,
              b"SEDBxyz".ljust(8, b"\0") + bytes(range(24))]
    return W.trb(blocks, tags=[b"abc", b"brw", b"xyz", b"m1", b"m2"],
                 names=[b"F00\\one", b"mesh", b"three", b"meta1", b"meta2"])


def world(edits, **kw):
    files = {"models\\orig\\" + p + ".trb": model(m) for p, m in zip(PATHS, (HERO, OTHER))}
    files[RECIPE] = W.recipe(PATHS, OUT, [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)], edits=edits,
                             imgb="no.imgb", **kw)
    return files


CASES = {
    "untouched": [":X"],
    "remove": [":R,0,0,2,4", ":R,0,1,-3,-1", ":R,1,0,0,0", ":X"],
    "remove_bone": [":R,0,0,3,6", ":X"],
    "remove_all_from": [":R,0,1,4,32767", ":X"],
    "remove_backwards": [":R,0,0,5,2", ":X"],
    "copy": [":C,0,0,1,4", ":C,0,1,8,0", ":X"],
    "copy_append": [":C,0,0,2,-1", ":C,0,0,11,-1", ":C,0,1,3,-1", ":X"],
    "delete": [":D,0,0", ":X"],
    "delete_each": [":D,0,1", ":D,1,0", ":D,0,0", ":X"],
    "delete_invalid": [":D,0,2", ":D,2,0", ":X"],
    "drop_component": [":B,0,0,1,2", ":X"],
    "drop_first_last": [":1B,0,1,0", ":B,1,0,1,6", ":X"],
    "drop_invalid": [":B,3,0,1,1", ":B,0,5,1,1", ":X"],
    "move": [":M,0,0,0,5,10,-20,30", ":M,1,0,32767,0,1,1,1", ":X"],
    "move_zero": [":M,0,1,0,3,0,0,0", ":X"],
    "add_component": [":2A,0,0,1,2,1,8", ":X"],
    "add_at_end": [":2A,0,1,1,0,7,32", ":X"],
    "add_types": [":2A,1,0,1,15,0,0", ":2A,0,0,1,1,3,12", ":2A,0,1,1,9,2,8", ":X"],
    "add_named": [":A,0,0,13,3,12,renamed", ":X"],
    "add_then_drop": [":2A,0,0,1,8,7,32", ":B,0,0,1,7", ":X"],
    "insert": [":I,0,1,1,0,0,5,inserted", ":X"],
    "insert_append": [":I,1,1,1,1,0,3", ":X"],
    "insert_twice": [":I,0,0,1,0,1,-1,", ":I,0,0,1,1,0,2,x", ":X"],
    "insert_invalid": [":I,0,0,1,5,0,0", ":I,0,3,1,0,0,0", ":X"],
    "compare": [f":c,0,0,1,0,0,{CMP}", f":c,0,1,0,0,1,{CMP}", ":X"],
    "compare_sizes_differ": [f":c,0,0,1,0,1,{CMP}", ":X"],
    "remove_selected_all": [":r,0,0,2,6", ":X"],
    "remove_selected_box": [":e,0,1.2,1.2,1.2,0,0,0,0,0,0", ":r,0,0,0,32767", ":r,0,1,0,32767", ":X"],
    "remove_selected_cut": [":e,0,4,4,4,0,0,0,0,0,0", ":e,-101,0.6,0.6,4,0,0,30,0,0,0", ":O,1,0,-4,-1", ":X"],
    "remove_selected_backwards": [":r,0,0,5,2", ":X"],
    "mixed": [":D,1,0", ":R,0,0,0,1", ":C,0,0,0,-1", ":2A,0,0,1,8,6,28", ":I,0,0,1,0,0,1", ":X"],
    "verbose": [":D,0,0", ":X"],
}
KW = {"verbose": dict(flags=1)}

GOLDEN = {
    "add_at_end": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "a88b6541a2588834f6984ff680215bc636869355fff60048dcd87f04de83e26c",
    },
    "add_component": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "f4dabbf29b8e36ec9ae5565f74094a8f514e07a6dddbfd4bd48802c3ff5373fa",
    },
    "add_named": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "89f2da17d868599d5d68930c937727b9905e43f08a7293c024086d0ee1775646",
    },
    "add_then_drop": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "2d90067f2b5005b7885b8e49a66aa647d2903b3fe8e4841add95a700859f749b",
    },
    "add_types": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "449cd86f56c91d9458ed6bb644e3436a76c40aa2a5e6a8846b60df15d4e93bab",
    },
    "compare": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "696f9623bc6a8dbd9c1d39ecdba0855c32710ae12b606ffd2fbc09ece79b2e2b",
        "models\\mod\\cmp.txt":
            "3c5d2218d52139faa7e9607e9093cf647508d5bd3592a21a8f8dad8c4b6301a1",
    },
    "compare_sizes_differ": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "696f9623bc6a8dbd9c1d39ecdba0855c32710ae12b606ffd2fbc09ece79b2e2b",
    },
    "copy": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "060c8b78b660a16b09fd9deb7c9f9f3dfce44e56ec58771eea8ac56f416722cd",
    },
    "copy_append": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "7c3b438e6baa9329845c92417cb88c727f6fbcea73f21643c7d5cdf5dae8c22f",
    },
    "delete": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "57b8dcfe9d6b44374668d2742852f4dfef9bec12d0aa0cb77a5b5864923b27fb",
    },
    "delete_each": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "ad0bd62a0166da3eba961cc039032f5a3f4073869d4325d43753b9fe02ab5f99",
    },
    "delete_invalid": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "696f9623bc6a8dbd9c1d39ecdba0855c32710ae12b606ffd2fbc09ece79b2e2b",
    },
    "drop_component": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "c096e7c09901714658f78ec3aa1909c1f3522a43df676645e743b74206ff2248",
    },
    "drop_first_last": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "41dc0e4ec8c21d5787599deabe29dec56b3defe01b4a896a5120df33785a348e",
    },
    "drop_invalid": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "696f9623bc6a8dbd9c1d39ecdba0855c32710ae12b606ffd2fbc09ece79b2e2b",
    },
    "insert": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "24d63bf015a35d649ae7f066c6bd602756f43d18df8ee703a70fc8ccf4af142d",
    },
    "insert_append": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "6372ab5dbcb9e75dc4598473c8c088a01fbc324c9fc86512d08a5807efbc2914",
    },
    "insert_invalid": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "696f9623bc6a8dbd9c1d39ecdba0855c32710ae12b606ffd2fbc09ece79b2e2b",
    },
    "insert_twice": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "3eb332446d8a56c586803b56430a610f2d255260309d961cb9694afdb3531472",
    },
    "mixed": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "6fc668f9ed4db95911f9d24207b26f27234664fd529c56939eac548e6cc5d5db",
    },
    "move": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "b9f55f08222a26850a203114a96018620dd677043ea000225ffea64ac5dede0f",
    },
    "move_zero": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "696f9623bc6a8dbd9c1d39ecdba0855c32710ae12b606ffd2fbc09ece79b2e2b",
    },
    "remove": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "3a892374ef57fb2bd1d70c61519dabda3821eb4b9431cca82fc63eabb9ae403c",
    },
    "remove_all_from": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "e00da95cfe0af2d5c304374f470b75712bcdcfa3d3816e410534a83d599503a9",
    },
    "remove_selected_all": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "2323848bd99ebf6ee66fa1bba16ce39eb095e530f945db9a5b1b9d344cf2fb45",
    },
    "remove_selected_box": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "fbb94bd4146bd5b3f0fa5fff4b782ffc9c8d8b422b9444600af29e4101c5c48e",
    },
    "remove_selected_cut": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "968a7619ec4591ac4f0d085eb351e1ab9517f00cd59757b5b37bf227eb690a32",
    },
    "remove_selected_backwards": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "696f9623bc6a8dbd9c1d39ecdba0855c32710ae12b606ffd2fbc09ece79b2e2b",
    },
    "remove_backwards": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "696f9623bc6a8dbd9c1d39ecdba0855c32710ae12b606ffd2fbc09ece79b2e2b",
    },
    "remove_bone": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "c3cc2006b3c71de99420167e0b80921e56d33f9446a968068f98475433409e27",
    },
    "untouched": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "696f9623bc6a8dbd9c1d39ecdba0855c32710ae12b606ffd2fbc09ece79b2e2b",
    },
    "verbose": {
        "models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
            "57b8dcfe9d6b44374668d2742852f4dfef9bec12d0aa0cb77a5b5864923b27fb",
    },
}


def _out(files) -> dict:
    """build.run, plus the text files :c appends to"""
    lower = {k.lower(): v for k, v in files.items()}

    class Files:
        def read(self, path):
            return lower.get(path.lower())

    b = build.Build(Recipe(files[RECIPE]), Files())
    out = build._main(b)
    out.update({path: bytes(f.data) for path, f in getattr(b, "written", {}).items()})
    return out


@pytest.mark.parametrize("name", sorted(CASES))
def test_mesh_ops_match_the_exe(name):
    got = _out(world(CASES[name], **KW.get(name, {})))
    assert {k: hashlib.sha256(v).hexdigest() for k, v in got.items()} == GOLDEN[name]


def test_adding_a_component_type_with_no_layout_is_refused():
    # the exe fills those rows from uninitialised stack
    with pytest.raises(build.ModelError):
        _out(world([":2A,0,0,1,5,1,8", ":X"]))
