"""
The skeleton editor (skeleton.py) against FF XIII HD Models.exe. Every input
is built here, nothing from the game: two made up skeletons that share most
of their bones, run through each command. The hashes are what the real exe
wrote for these exact inputs under Wine, so a pass means byte for byte
"""

import hashlib

import pytest

from patcher.mods.hdmodels import build
from tests import hdm_world as W

PATHS = ["chr\\pc\\c005\\bin\\c005.win32", "chr\\pc\\c105\\bin\\c105.win32"]
OUT = "chr\\pc\\c905\\bin\\c905.win32"
RECIPE = "Data\\T.txt"


def tree(rows, version=9100, extra=b"", salt=0.0):
    """
    a skeleton from (name, parent) rows in order: first child and next
    sibling worked out the way the game links them, transforms made up but
    easy to tell apart
    """
    names = [n for n, _ in rows]
    child, sibling = [-1] * len(rows), [-1] * len(rows)
    for k, (_, up) in enumerate(rows):
        if up < 0:
            continue
        if child[up] == -1:
            child[up] = k
        else:
            at = child[up]
            while sibling[at] != -1:
                at = sibling[at]
            sibling[at] = k
    bones = [W.bone(name=k, kind=1 if k == 0 else 5, parent=up, child=child[k], sibling=sibling[k],
                    order=k, pos=(k * 0.25 + salt, -k * 0.5, 1.0 + k),
                    rot=(0.0, 0.125 * (k % 3), 0.0, 0.875 + salt / 8),
                    scale=(1.0, 1.0, 1.0 + k / 16))
             for k, (_, up) in enumerate(rows)]
    return W.skl(bones, names, version=version, extra=extra)


HERO = tree([(b"root", -1), (b"spine", 0), (b"neck", 1), (b"head", 2), (b"C_Jaw", 3),
             (b"C_U", 3), (b"L_eye", 3), (b"R_eye", 3), (b"L_arm", 1), (b"L_fing3a", 8),
             (b"L_fing4b", 8), (b"R_arm", 1)], extra=bytes(range(8)))
OLD = tree([(b"root", -1), (b"spine", 0), (b"neck", 1), (b"head", 2), (b"C_Jaw", 3),
            (b"L_eye", 3), (b"R_eye", 3), (b"tail", 0), (b"hair", 3), (b"hair2", 8),
            (b"L_arm", 1), (b"L_fing3a", 10), (b"L_brow", 3)], version=1000, salt=0.5)


def model(skel, tag=b"lks"):
    blocks = [(b"SEDBabc").ljust(8, b"\0") + bytes(range(40)), skel,
              (b"SEDBxyz").ljust(8, b"\0") + bytes(range(24))]
    return W.trb(blocks, tags=[b"abc", tag, b"xyz", b"m1", b"m2"],
                 names=[b"F00\\one", b"skel", b"three", b"meta1", b"meta2"])


def world(edits, skels=(HERO, OLD), src=0, blocks=None, untagged=False, **kw):
    """`untagged`: the second model's skeleton isn't tagged lks, so nothing finds it"""
    files = {}
    for k, (path, skel) in enumerate(zip(PATHS, skels)):
        files["models\\orig\\" + path + ".trb"] = model(skel, b"skl" if untagged and k else b"lks")
    blocks = blocks or [(0, 0), (src, 1), (0, 2), (0, 3), (0, 4)]
    files[RECIPE] = W.recipe(PATHS[:len(skels)], OUT, blocks, edits=edits, imgb="no.imgb", **kw)
    return files


CASES = {
    "untouched": dict(edits=[":X"]),
    "bone_words": dict(edits=[":E,1,0,1", ":E,2,1,7", ":E,3,2,1", ":E,4,3,-1", ":E,5,4,6",
                              ":E,6,5,3", ":E,7,6,1.5", ":E,7,7,-0.25", ":E,7,8,3e2", ":E,8,9,0.5",
                              ":E,8,10,-0.5", ":E,8,11,0.125", ":E,8,12,0.7071068", ":E,9,13,2",
                              ":E,9,14,0.333", ":E,9,15,1e-3", ":E,10,16,4", ":E,11,17,1",
                              ":E,12,0,1", ":E,-1,0,1", ":X"]),
    "check_cleanup": dict(edits=[":E,2,6,0.000001", ":E,2,7,-0.000009", ":E,3,12,0.99999999",
                                 ":E,4,12,0.000001", ":E,5,9,-0.0", ":E,6,3,2", ":E,6,4,2",
                                 ":E,1,3,5", ":X", ":X"]),
    "reorder": dict(edits=[":X", ":O,1,0,1", ":E,4,3,10", ":E,9,4,-1", ":X"]),
    "reorder_plain": dict(edits=[":O,1,0,0", ":X"]),
    "reorder_chain": dict(edits=[":O,1,0,2", ":X"]),
    "reorder_both": dict(edits=[":O,1,0,3", ":X"]),
    "reorder_from_old": dict(edits=[":O,0,1,1", ":X"], src=1),
    "reorder_v41": dict(edits=[":O,1,0,0,1", ":F,1,0", ":X"], version=41),
    "face_fix": dict(edits=[":A,1", ":F,1", ":X"]),
    "straighten": dict(edits=[":O,1,0,1", ":F,1", ":S", ":X"]),
    "params": dict(edits=[":P,2,1,3", ":Q,3,1,-1", ":P,1,1,-1", ":Q,0,1,0", ":P,40,1,0", ":X"]),
    "params_old": dict(edits=[":Q,2,0,-1", ":P,5,0,3", ":X"], src=1),
    "zero_order": dict(edits=[":Z"]),
    "matching": dict(edits=[":M,0,1,5", ":M,1,0,0", ":O,1,0,0"]),
    "add_missing": dict(edits=[":A,1", ":X"]),
    "add_missing_old": dict(edits=[":A,0", ":X"], src=1),
    "new_bones": dict(edits=[":B,extra1,5,13,4,-1,-1,0,0.5,1.5,2.5,0.0,0.0,0.0,1.0",
                             ":B,extra2,-1", ":B,extra3,-3", ":B,extra4,1,15,0,-1,-1,0,1,2,3,4,5,6,7",
                             ":B,extra5,-2", ":B,extra6,-4", ":X"]),
    "new_bones_old": dict(edits=[":B,horn,5,13,3,-1,-1,0,0.5,1.5,2.5,0.0,0.0,0.0,1.0", ":X"],
                          src=1),
    "bone_order": dict(edits=[":o,1,0,0", ":X"]),
    "bone_order_numbered": dict(edits=[":o,1,0,1", ":Z", ":o,0,0,1"]),
    "bone_lines": dict(edits=["3", "0", "-2", "11", "2", "7", "99", "-1", ":X"]),
    "bone_lines_named": dict(edits=["HEAD", "root", "nope", "l_arm", ":X"], version=43),
    "two_blocks": dict(edits=[":O,1,0,1", ":E,1,3,4", ":X"],
                       blocks=[(0, 0), (0, 1), (1, 1), (0, 2), (0, 3), (0, 4)]),
    "name_swaps": dict(edits=[":O,1,0,1", ":X"], names=[("L_fing3a", "L_fing3b"), ("hair", "fur")]),
    "name_swaps_first": dict(edits=[":O,1,0,0", ":F,1", ":X"], names=[("head", "kopf")],
                             names_first=True),
    "verbose": dict(edits=[":E,1,3,2", "4", ":X", ":O,1,0,1"], flags=1),
    "flag_switch": dict(edits=["::F,4", "12", "5", ":X"]),
    "answer_gated": dict(edits=[":X", ":O,1,0,1", ":S", ":F,1", "::I,2,2", ":E,3,13,0.0",
                                ":E,3,14,0.0", ":E,3,15,0.0", "::E", ":X"], answers=2),
    "untagged": dict(edits=[":O,1,0,1", ":F,1", ":A,1", ":M,1,0,0", ":P,1,1,0", ":o,1,0,1",
                            ":E,2,3,4"], untagged=True),
}

GOLDEN = {
    "answer_gated": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "18d82bc558a1af3056ab329b32beefc6387f6d11f16a03ce146ab62abee7a14e"},
    "add_missing": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "b4493fe15545a74abaa417d9a2b60fd13b271d03473ee36598cf87378992f721"},
    "add_missing_old": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "b51d9f1fe5eb25ff7000a748c7e00295766b3809e032c99439affcef08607507"},
    "bone_lines": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "de4e5bf0f83608bc8ee85a6c48bef2c9e2ec83b571e4f41dd4a15c4e3407dfc1"},
    "bone_lines_named": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "de4e5bf0f83608bc8ee85a6c48bef2c9e2ec83b571e4f41dd4a15c4e3407dfc1"},
    "bone_order": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "21e86df4314a96cb826cfc9f6ee108ced3440b20033390c9f2085e7591277b11"},
    "bone_order_numbered": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "7cff14dd86f6e61161b84f4f22f80f392ddc0ede40aa014ce15cb41dbae87b95"},
    "bone_words": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "010245e89f3f01a28ee587ab623f6bd210f43183a0aaa3709e125d9176cbb8ef"},
    "check_cleanup": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "bf56e985d532e27bb4deacb50a49842fb40e93ed443eb934a075587fc691a720"},
    "face_fix": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "0262c29de6dd71ebff208d3876f23dbc22837b60a18cde1e5c4913e49fedf78d"},
    "flag_switch": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "de4e5bf0f83608bc8ee85a6c48bef2c9e2ec83b571e4f41dd4a15c4e3407dfc1"},
    "matching": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "fd70b0c0ff7f02eb28785668574f5ab2323a0e801d945d76189787e50ea6dade"},
    "name_swaps": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "bc3905e15f617a6857df0ac91c84fa5500321ece53cedc35408e641e7359816c"},
    "name_swaps_first": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "31dabbda3ea7b325df11ed7e98b62756b4ee198ce3c5261a7c3d50af074a3b2a"},
    "new_bones": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "ea60acd0fcc7c58e1d6eab8c225e8e8a049436bb5060ac18abd62bd9d8772377"},
    "new_bones_old": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "dc7585cab6fbd478cc6e344dfb70fc99094d15ac7bc5a3730e0f03203e526d56"},
    "params": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "48a806118a0e236f7d9b25375bbb21cd5cfb1876488a4d3439ee96af0f9381e0"},
    "params_old": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "0c39122c636ea48a6f7086cae48b10a56b4404ec7479c0e998aa7c25b432ef1b"},
    "reorder": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "ed530714adad17d6e131ba2fc9533ebab7be61dc8824091ffb5bfbe8a032f136"},
    "reorder_both": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "db135f397b507d101daf2fcabf1959294218c735fffd0bb347320678e819c4ba"},
    "reorder_chain": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "704cf7d26a0388f6a31ce25d4288366017d5083179fd2949a212dbe988e01d27"},
    "reorder_from_old": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "d7243a1bc2c2caba212fafd1fc63ae82b6a9aab60597c09a96b6ed58d932ca25"},
    "reorder_plain": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "fd70b0c0ff7f02eb28785668574f5ab2323a0e801d945d76189787e50ea6dade"},
    "reorder_v41": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "be8b8598e5066c11001f0b12c700b0802d3cecc948817bbc311226c08fb1d80c"},
    "straighten": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "a9a246cd635b810eb1e5d926f56b05145b3f5c2d6ad8c4a92d62bb11e7c54894"},
    "two_blocks": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "4d46d352a5281e17eced7193b4cb4fe8b98e94019c8b8faaa16c73abb1fc7fc8"},
    "untagged": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "b4370483e16f436fa3656f1ed314942b9a6e003c001c430acd6e223834d1ecc3"},
    "untouched": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "de4e5bf0f83608bc8ee85a6c48bef2c9e2ec83b571e4f41dd4a15c4e3407dfc1"},
    "verbose": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "ed530714adad17d6e131ba2fc9533ebab7be61dc8824091ffb5bfbe8a032f136"},
    "zero_order": {"models\\mod\\chr\\pc\\c905\\bin\\c905.win32.trb":
        "45f692b1dd7ebc86682223b500b9c65b64207c7ef230fe060503f2be521cf698"},
}


def _out(files, answers=0) -> dict:
    lower = {k.lower(): v for k, v in files.items()}

    class Files:
        def read(self, path):
            return lower.get(path.lower())

    return build.run(files[RECIPE], Files(), answers=answers)


@pytest.mark.parametrize("name", sorted(CASES))
def test_skeleton_matches_the_exe(name):
    case = dict(CASES[name])
    answers = case.pop("answers", 0)
    got = _out(world(**case), answers)
    assert {k: hashlib.sha256(v).hexdigest() for k, v in got.items()} == GOLDEN[name]


def test_an_unknown_command_stops_the_build():
    # the exe returns 11 from main() there, with half a model written
    with pytest.raises(build.ModelError):
        _out(world([":E,1,3,2", ":K,1"]))


def test_bone_reads_a_transform_as_raw_words(monkeypatch):
    # the mesh editor's way in: any model's bone, loaded on demand
    from patcher.mods.hdmodels import skeleton

    seen = {}
    edit = skeleton.edit

    def peek(b, *args):
        seen["b"] = b
        return edit(b, *args)

    monkeypatch.setattr(skeleton, "edit", peek)
    _out(world([":X"]))
    b = seen["b"]
    code, (pos, rot, scale) = skeleton.bone(b, 1, 3)
    assert code == 0
    assert pos == (0x3FA00000, 0xBFC00000, 0x40800000)   # 1.25, -1.5, 4.0
    assert rot[3] == 0x3F700000                           # 0.9375
    assert scale == (0x3F800000, 0x3F800000, 0x3F980000)   # 1, 1, 1.1875
    assert skeleton.bone(b, 1, -1) == (0, None)
    assert skeleton.bone(b, 1, 40)[0] == -10
