"""
wrb.parse's shortcuts against a parse from scratch. After a resize it
refills its arrays in place, moves the meshes the resize didn't touch rather
than reading them again, and during an import leaves those moves owed until
settle(). Each has to come out exactly where the full parse would put
everything, or every edit after it reads the wrong bytes
"""

import pytest

from patcher.mods.hdmodels import wrb
from patcher.mods.hdmodels.cmem import Mem
from patcher.mods.hdmodels.heap import Heap
from tests import hdm_world as W


def tris(n, k=0):
    return [((i + k) % n, (i * 3 + 1 + k) % n, (i * 7 + 2 + k) % n) for i in range(n)]


BLOCK = W.wrb([
    [W.wrb_mesh(12, tris(12), name=b"one", envds=[(b"head", [0, 1, 2, 5], [1, 2, 3, 4]),
                                                    (b"R_eye", [3, 4], [9, 8])]),
     W.wrb_mesh(9, tris(9, 1), name=b"two", seed=3, envds=[(b"neck", [0, 2, 4], [5] * 3)]),
     W.wrb_mesh(6, tris(6, 2), name=b"three", seed=5)],
    [W.wrb_mesh(7, tris(7), name=b"four", seed=7, envds=[(b"hip", [0, 6], [2, 2])]),
     W.wrb_mesh(5, tris(5, 3), name=b"five", seed=9, envds=[(b"tail", [4], [9])])],
])


class Fake:
    mdl, mesh = wrb.Edit.mdl, wrb.Edit.mesh

    def __init__(self):
        self.mem = Mem()
        self.heap = Heap(self.mem, lambda text: None)
        self.said = []
        self.ps = self.mem.malloc(wrb.PARSE)

    def say(self, text):
        self.said.append(text)


def dump(mem, ps):
    """every value the parse wrote, less the addresses of its own arrays"""
    out = [mem.get(ps, 8), mem.u32(ps + wrb.MDL_COUNT)]
    mdls, count = mem.u64(ps + wrb.MDLS), mem.u32(ps + wrb.MDL_COUNT)
    for i in range(count):
        m = mdls + i * 0x40
        out.append(mem.get(m, 0x18) + mem.get(m + 0x20, 0x20))
        for j in range(mem.u32(m + 0x30)):
            me = mem.u64(m + 0x18) + j * 0x30
            out += [mem.get(me, 8) + mem.get(me + 0x18, 0x18),
                    mem.get(mem.u64(me + 0x08), mem.u32(me + 0x20) * 0x38),
                    mem.get(mem.u64(me + 0x10), max(mem.u32(me + 0x24), 1) * 0x38)]
    for field, size in ((wrb.MINS, 8), (wrb.MINS_AT, 4), (wrb.MINS_END, 4)):
        out.append(mem.get(mem.u64(ps + field), count * size))
    return out


def fresh(w):
    """the same block parsed from nothing"""
    ps = w.mem.malloc(wrb.PARSE)
    w.mem.set_u32(ps + wrb.SIZE, w.mem.u32(w.ps + wrb.SIZE))
    wrb._parse(w, ps, w.mem.u64(w.ps), lambda text: None, False)
    return dump(w.mem, ps)


def start():
    w = Fake()
    blk = w.heap.new(len(BLOCK))
    w.mem.write(blk, BLOCK)
    w.mem.set_u32(w.ps + wrb.SIZE, len(BLOCK))
    wrb.parse(w, w.ps, blk)
    return w, blk


def grow(w, blk, kind, mdl, mesh):
    if kind == "verts":
        return wrb.grow_verts(w, mdl, mesh, 3, w.ps, blk)
    if kind == "faces":
        return wrb.grow_faces(w, mdl, mesh, 2, blk)
    return wrb.grow_envd(w, mdl, mesh, 0, 2, w.ps, blk)[0]


MESHES = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]


@pytest.mark.parametrize("kind", ["verts", "faces", "envd"])
@pytest.mark.parametrize("mdl,mesh", MESHES)
def test_a_grow_reparses_where_a_full_parse_would(kind, mdl, mesh):
    w, blk = start()
    if kind == "envd" and not w.mem.u32(w.mesh(mdl, mesh) + 0x24):
        pytest.skip("no ENVD to grow")
    grow(w, blk, kind, mdl, mesh)
    assert dump(w.mem, w.ps) == fresh(w)
    assert not w.said


def one(mem, ps, mdl, mesh):
    """one mesh's entry and records"""
    me = mem.u64(mem.u64(ps + wrb.MDLS) + mdl * 0x40 + 0x18) + mesh * 0x30
    return [mem.get(me, 8) + mem.get(me + 0x18, 0x18),
            mem.get(mem.u64(me + 0x08), mem.u32(me + 0x20) * 0x38),
            mem.get(mem.u64(me + 0x10), max(mem.u32(me + 0x24), 1) * 0x38)]


def test_owed_moves_land_once_settled():
    w, blk = start()
    w.defer_moves = True
    for mdl, mesh in [(0, 1), (0, 1), (1, 0), (0, 0), (0, 1)]:
        blk = grow(w, blk, "faces", mdl, mesh)
        # the mesh just grown is right straight away, the rest only owe
        owed = dict(wrb._owed(w.mem))
        ps = w.mem.malloc(wrb.PARSE)
        w.mem.set_u32(ps + wrb.SIZE, w.mem.u32(w.ps + wrb.SIZE))
        wrb._parse(w, ps, blk, lambda text: None, False)
        w.mem.parse_owed = owed
        assert one(w.mem, w.ps, mdl, mesh) == one(w.mem, ps, mdl, mesh)
    assert wrb._owed(w.mem)[w.ps]
    w.defer_moves = False
    wrb.settle(w)
    assert dump(w.mem, w.ps) == fresh(w)
    assert not wrb._owed(w.mem)


def test_a_changed_count_falls_back_to_the_full_parse():
    w, blk = start()
    # the arrays sized for a count the block no longer has
    w.mem.set_u32(w.mesh(1, 1) + 0x24, 7)
    wrb.parse(w, w.ps, blk, True)
    assert dump(w.mem, w.ps) == fresh(w)
