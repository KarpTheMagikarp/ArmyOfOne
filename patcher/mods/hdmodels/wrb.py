"""
The mesh block: a model's geometry, and the editor that reshapes it.

A wrb block is big endian, and nested like this, each piece found from the
sizes of the ones before it (no offsets table, so every edit that grows or
shrinks a piece has to fix up the sizes above it):

    0x94  u32   how many MDLs
    0xa0  MDL   0x30 header, then 0x20 more past its own size word
          MESH  0xe0 each, STMS count at +0x32, ENVD count at +0x33
            STMS  a vertex stream: header, a 0x10 entry per component,
                  then the vertices (stride at +0x1a, count at +0x14),
                  then MDR/MPR chunks
            ENVD  bone weights and the names of the bones they bind to
          0x50 after the meshes, 0x80 to close the MDL
    ...   LTCD, MICT, a MINS per MDL, and maybe PESR at the end

The exe parses that into a 0x68 byte struct of pointers into the block
(FUN_0040ade0) and every edit works through those pointers, then
re-parses whenever it resizes the block. So this is ported onto cmem.Mem a
line at a time: the struct, the block and the exe's globals sit at
addresses, and the arithmetic stays the exe's.

Edits come in as `:<letter>,args` lines until `:X`, an optional digit after
the colon picking a variant. Each is read here exactly as the exe reads it,
then handed to the function that does it, registered from its own module
by the exe address it was ported from.
"""

import importlib
import operator
import struct
from itertools import repeat

from patcher.mods.hdmodels import state
from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import f32, i16, i32, u16, u32

PARSE = 0x68
# the parse struct's fields the dispatcher itself touches
MDLS, LTCD, MICT, MINS, MINS_AT, MINS_END, PESR = 0x08, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38
PESR_A, PESR_B, NO_PESR, MDL_COUNT, SIZE = 0x40, 0x48, 0x50, 0x60, 0x64

# globals
LEGACY_VOLUMES = 0x44A180   # i16, cleared by edits in recipes before version 42
EDIT_BLOCK = 0x45AD44       # u16, the output block being edited
POSES = 0x45A6C0            # 0x5dc bytes the e command keeps poses in
POSE_COUNT = 0x45A6AC
CATALOG_COUNT = 0x45AD40    # entries in the open mesh catalog
CATALOG_LOADED = 0x45AD31
CATALOG_TABLE = 0x45AD28
COMPONENTS_NAME = 0x45ACA0  # the open component catalog's path
COMPONENTS_OPEN = 0x45AD04
COMPONENTS_COUNT = 0x45AD08
COMPONENTS_TABLE = 0x45AD10
PICKS_COUNT = 0x45AD3C
PICKS = 0x45AD20
PICKS_USED = 0x45AD38
BONE_NAMES = 0x45AD60       # up to 500 pointers
BONE_NAME_COUNT = 0x45AD46  # u16

HALF = f32(0.5)

# the modules the edits live in, imported the first time an edit's needed
MODULES = ("volume", "mesh_ops", "mesh_import", "mesh_shape", "mesh_weights", "mesh_misc")
HANDLERS = {}


def handler(address: int):
    """registers a port of the exe's function at `address`"""
    def register(fn):
        HANDLERS[address] = fn
        return fn
    return register


def _loaded():
    if not getattr(_loaded, "done", False):
        for name in MODULES:
            try:
                importlib.import_module(f"patcher.mods.hdmodels.{name}")
            except ModuleNotFoundError as e:
                if not e.name or not e.name.endswith(name):
                    raise
        _loaded.done = True


def call(w, address: int, *args, **kw):
    _loaded()
    settle(w)
    fn = HANDLERS.get(address)
    if fn is None:
        raise ModelError(f"it uses a mesh edit (:{w.letter}) this can't do yet")
    return fn(w, *args, **kw)


class Edit:
    """one wrb block's run of edits: the address space, its parse, the recipe"""

    def __init__(self, b, n: int):
        self.b = b
        self.n = n
        self.mem = state.push(b)
        self.heap = b.heap
        self.r = b.r
        self.say = b.say
        self.ps = self.mem.malloc(PARSE)
        # the second parse the insert and copy edits read another model into
        self.other = self.mem.malloc(PARSE)
        self.letter = ""
        self.variant = 0

    @property
    def version(self) -> int:
        return self.b.version

    @property
    def verbose(self) -> bool:
        return bool(self.r.flags & 3)

    def U(self) -> int:
        return u16(self.r.int())

    def S(self) -> int:
        return i16(self.r.int())

    def I(self) -> int:
        return i32(self.r.int())

    def F(self) -> float:
        return self.r.float()

    def T(self, optional: bool = False) -> str:
        return self.r.str(optional)

    def mdl(self, n: int, ps: int = None) -> int:
        """the n'th MDL's 0x40 byte entry in the parse"""
        ps = self.ps if ps is None else ps
        if not 0 <= n < self.mem.u32(ps + MDL_COUNT):
            # the exe would go on through whatever memory follows
            raise ModelError("its recipe edits a mesh the model hasn't got")
        return self.mem.u64(ps + MDLS) + n * 0x40

    def mesh(self, mdl: int, n: int, ps: int = None) -> int:
        """a mesh's 0x30 byte entry in the parse"""
        _due(self, self.ps if ps is None else ps, mdl, n)
        entry = self.mdl(mdl, ps)
        if not 0 <= n < self.mem.u32(entry + 0x30):
            raise ModelError("its recipe edits a mesh the model hasn't got")
        return self.mem.u64(entry + 0x18) + n * 0x30


def _tag(mem, at: int, tag: bytes) -> bool:
    """FUN_00409aa0 over len(tag): the 4cc at `at` reads as `tag`"""
    if mem.buf[at:at + len(tag)] == tag:
        return True
    for k, want in enumerate(tag):
        have = mem.u8(at + k)
        if (0 if have == 0x7E else have) != (0 if want == 0x7E else want):
            return False
    return True


class _Reshaped(Exception):
    """the block no longer has the shape the parse's arrays were sized for"""


def parse(w, ps: int, blk: int, again: bool = False, moved=None) -> None:
    """
    FUN_0040ade0: the struct of pointers into a wrb block every edit works
    through. `moved` is (mdl, mesh, grow) from a resize that only put `grow`
    bytes inside that one mesh, so every other mesh's entries just move:
    they're left owing that move until something other than the grows can
    read them (see settle)
    """
    mem = w.mem
    if again:
        # the exe frees every array and mallocs it again after each resize.
        # Most resizes keep every count, so the arrays are refilled in place
        # instead; anything that changed a count (or that the full parse
        # would act on, an MDR chunk or a bad read) falls back to the real
        # thing, with the messages the try made dropped so none come twice
        said = []
        try:
            _parse(w, ps, blk, said.append, True, moved)
        except Exception:
            release(mem, ps)
        else:
            for text in said:
                w.say(text)
            return
    _parse(w, ps, blk, w.say, False)


def _owed(mem) -> dict:
    """ps -> {(mdl, mesh): (pointer move, offset move)} not yet written in"""
    if not hasattr(mem, "parse_owed"):
        mem.parse_owed = {}
    return mem.parse_owed


def settle(w) -> None:
    """
    every parse's moves written into its entries. An import (:~) grows its
    mesh a few thousand times and only ever reads that mesh's entries in
    between, so while one runs (w.defer_moves) the rest are moved once, here
    """
    mem = w.mem
    owed = _owed(mem)
    while owed:
        ps, moves = owed.popitem()
        try:
            mdls = mem.u64(ps + MDLS)
            for (i, j), (ptr, off) in moves.items():
                _move_mesh(mem, mem.u64(mdls + i * 0x40 + 0x18) + j * 0x30, None, ptr, off)
        except Exception:
            blk = mem.u64(ps)
            release(mem, ps)
            _parse(w, ps, blk, lambda text: None, False)


def _due(w, ps: int, mdl: int, mesh: int) -> None:
    """settle first when the mesh about to be read is still owed a move"""
    owed = getattr(w.mem, "parse_owed", None)
    if owed and (mdl, mesh) in owed.get(ps, ()):
        settle(w)


def _parse(w, ps: int, blk: int, say, reuse: bool, moved=None) -> None:
    mem = w.mem
    count = mem.be32(blk + 0x94)
    was = mem.u64(ps)
    owed = _owed(mem).setdefault(ps, {}) if moved else None
    if not moved:
        _owed(mem).pop(ps, None)
    if reuse:
        mdls = mem.u64(ps + MDLS)
        if not mdls or not mem.u64(ps + MINS) or mem.u32(ps + MDL_COUNT) != count:
            raise _Reshaped
    else:
        mdls = mem.malloc(count << 6)
    mem.set_u64(ps, blk)
    mem.set_u32(ps + MDL_COUNT, count)
    mem.set_u64(ps + MDLS, mdls)
    if not _tag(mem, blk, b"SEDBwrb"):
        say("Parse WRB> Not a WRB block\n")
    at = 0xA0
    for i in range(count):
        m = mdls + i * 0x40
        meshes = mem.be32(blk + u32(mem.be32(blk + at + 0x2C) + 0x20 + at) + 0x24)
        if reuse and (mem.u32(m + 0x30) != meshes or not mem.u64(m + 0x18)):
            raise _Reshaped
        mem.set_u32(m + 0x34, at)
        mem.set_u64(m, blk + at)
        if not _tag(mem, blk + at, b"MDL"):
            say(f"MDL: {i}  ID: {_id(mem, blk + at)}\n")
        mem.set_u64(m + 0x08, blk + u32(at + 0x30))
        at = u32(mem.be32(mem.u64(m) + 0x2C) + 0x20 + at)
        cur = u32(at + 0x30)
        mem.set_u64(m + 0x10, blk + at)
        mem.set_u32(m + 0x30, meshes)
        mesh_arr = mem.u64(m + 0x18) if reuse else mem.malloc(meshes * 0x30)
        mem.set_u64(m + 0x18, mesh_arr)
        for j in range(meshes):
            me = mesh_arr + j * 0x30
            if moved is None or (i, j) == moved[:2]:
                cur = _parse_mesh(w, blk, i, j, me, cur, say, reuse)
                if owed is not None:
                    owed.pop((i, j), None)
                continue
            grow = moved[2] if (i, j) > moved[:2] else 0
            if not getattr(w, "defer_moves", False):
                cur = _move_mesh(mem, me, cur, blk + grow - was, grow)
                continue
            ptr, off = owed.get((i, j), (0, 0))
            ptr, off = ptr + blk + grow - was, off + grow
            start, end = _ENDS.unpack_from(mem.buf, me + 0x28)
            if (start + off) & 0xFFFFFFFF != cur:
                raise _Reshaped
            owed[i, j] = ptr, off
            cur = (end + off) & 0xFFFFFFFF
        mem.set_u64(m + 0x20, blk + cur)
        at = u32(cur + 0x80)
        mem.set_u32(m + 0x38, at)
        mem.set_u64(m + 0x28, blk + u32(cur + 0x50))

    p = blk + at
    mem.set_u64(ps + 0x58, p)
    mem.set_u64(ps + LTCD, p)
    size = mem.be32(p + 0x0C)
    if not _tag(mem, p, b"LTCD"):
        say(f"LTCD ID: {_id(mem, p)}\n")
    nxt = u32(at + size + 0x20)
    mem.set_u64(ps + MICT, blk + u32(at + size))
    if not _tag(mem, blk + u32(at + size), b"MICT"):
        say(f"MICT ID: {_id(mem, blk + u32(at + size))}\n")
    if reuse:
        mins, starts, ends = (mem.u64(ps + field) for field in (MINS, MINS_AT, MINS_END))
    else:
        mins, starts, ends = mem.malloc(count << 3), mem.malloc(count << 2), mem.malloc(count << 2)
    mem.set_u64(ps + MINS, mins)
    mem.set_u64(ps + MINS_AT, starts)
    mem.set_u64(ps + MINS_END, ends)
    for k in range(count):
        mem.set_u32(starts + k * 4, nxt)
        mem.set_u64(mins + k * 8, blk + nxt)
        if not _tag(mem, blk + nxt, b"MINS"):
            say(f"MINS ID: {_id(mem, blk + nxt)}\n")
        nxt = u32(nxt + (0x90 if mem.be32(blk + nxt + 0x68) > 0x20 else 0x80))
        mem.set_u32(ends + k * 4, nxt)
    mem.set_u64(ps + PESR, blk + nxt)
    if mem.u32(ps + SIZE) < nxt + 0x20:
        mem.set_u8(ps + NO_PESR, 1)
        return
    found = _tag(mem, blk + nxt, b"PESR")
    mem.set_u8(ps + NO_PESR, 0 if found else 1)
    if found:
        parts = mem.be32(blk + nxt + 0x10)
        if i16(parts) != 0:
            first = u32(nxt + 0x20)
            mem.set_u64(ps + PESR_A, blk + first)
            if i16(parts) != 1:
                mem.set_u64(ps + PESR_B, blk + u32(mem.be32(blk + first + 0x0C) + first))


_BE16, _BE32 = struct.Struct(">H"), struct.Struct(">I")
_U64 = struct.Struct("<Q")
_STMS = struct.Struct("<QQQIIIIIIII")
_ENVD = struct.Struct("<QQQQQIII")
_MESH_HEAD = struct.Struct("<QQQ")
# a stream's header from +8: sizes, parts, vertices, stride, and the word
# that says MDR/MPR chunks follow (only ever tested against 0)
_STMS_HEAD = struct.Struct(">8xIIII2xH2xH")
# an ENVD's header from +8 and its table right behind it at +0x10
_ENVD_HEAD = struct.Struct(">8xIIH2xHH")


def _be(mem, fmt, at: int) -> int:
    # past the end of memory reads short, the way Mem.be32 always has
    try:
        return fmt.unpack_from(mem.buf, at)[0]
    except struct.error:
        return int.from_bytes(mem.buf[at:at + fmt.size], "big")


def _parse_mesh(w, blk: int, i: int, j: int, me: int, cur: int, say, reuse: bool) -> int:
    # runs after every resize, thousands of times a recipe, so each record
    # is worked out in locals and packed in one go
    mem = w.mem
    buf = mem.buf
    be16 = lambda at: _be(mem, _BE16, at)
    be32 = lambda at: _be(mem, _BE32, at)
    p = blk + cur
    at = u32(cur + 0xE0)
    streams, envds = buf[p + 0x32], buf[p + 0x33]
    if not _tag(mem, p, b"MESH"):
        say(f"MDL: {i}  Mesh: {j}  ID: {_id(mem, p)}\n")
    if reuse:
        stms, envd = mem.u64(me + 0x08), mem.u64(me + 0x10)
        if not stms or not envd or mem.u32(me + 0x20) != streams or mem.u32(me + 0x24) != envds:
            raise _Reshaped
    else:
        stms = mem.malloc(streams * 0x38)
        envd = mem.malloc(max(envds, 1) * 0x38)
    _MESH_HEAD.pack_into(buf, me, p, stms, envd)
    mem.set_u32(me + 0x20, streams)
    mem.set_u32(me + 0x24, envds)
    mem.set_u32(me + 0x28, cur)
    for k in range(streams):
        s = stms + k * 0x38
        start = at
        head = blk + at
        body = u32(at + 0x20)
        if buf[head:head + 4] != b"STMS" and not _tag(mem, head, b"STMS"):
            say(f"MDL: {i}  Mesh: {j}  STMS: {k}  ID: {_id(mem, head)}\n")
        try:
            lo, hi, parts, verts, stride, chunks = _STMS_HEAD.unpack_from(buf, head)
        except struct.error:
            lo, hi, parts, verts = (be32(head + n) for n in (8, 0xC, 0x10, 0x14))
            stride, chunks = be16(head + 0x1A), mem.u16(head + 0x1E)
        table_at = body
        body = (body + i32(parts) * 0x10) & 0xFFFFFFFF
        pad = (hi - lo) & 0xFFFFFFFF
        size = (verts * stride) & 0xFFFFFFFF
        data_at = body
        if chunks == 0:
            body = u32(body + size)
            size = at = 0
        else:
            # trailing MDR/MPR chunks: each one found knocks one off the
            # mesh's own count of them, in the block itself
            if reuse:
                raise _Reshaped
            at = 0
            chunk = blk + body
            while _tag(mem, chunk, b"MDR") or _tag(mem, chunk, b"MPR"):
                at = u32(at + be32(chunk + 0x0C))
                mem.set_be32(p + 0x10, be32(p + 0x10) - 1)
                chunk = blk + at + body
        if chunks:
            hi = be32(head + 0x0C)
        if pad != (hi + start - body) & 0xFFFFFFFF:
            say(f"STMS Padding Discrepenancy MDL:{i}  Mesh:{j}  STMS:{k}  Padding "
                  f"{i32(pad)} {i32(start - body + hi)}\n")
        _STMS.pack_into(buf, s, head, blk + table_at, blk + data_at, pad, parts & 0xFFFFFFFF, table_at,
                        start, u32(body + pad), data_at, at, size)
        at = u32(at + body + pad)
    if envds == 0:
        mem.set_u32(envd + 0x2C, at)
        mem.set_u32(envd + 0x30, at)
    for k in range(envds):
        e = envd + k * 0x38
        start = at
        head = blk + at
        if buf[head:head + 4] != b"ENVD" and not _tag(mem, head, b"ENVD"):
            say(f"MDL: {i}  Mesh: {j},  ENVD: {k},  ID: {_id(mem, head)}\n")
        table = blk + ((at + 0x10) & 0xFFFFFFFF)
        try:
            if table != head + 0x10:
                raise struct.error
            lo, hi, first, names_at, last = _ENVD_HEAD.unpack_from(buf, head)
        except struct.error:
            names_at, first, last = be16(table + 4), be16(table), be16(table + 6)
            lo, hi = be32(head + 0x08), be32(head + 0x0C)
        ids = (names_at - first + at + 0x20) & 0xFFFFFFFF
        at = (last - names_at + ids) & 0xFFFFFFFF
        weights = at
        at = (at + ((hi - lo) & 0xFFFFFFFF) + lo - last - first) & 0xFFFFFFFF
        _ENVD.pack_into(buf, e, head, table, blk + u32(start + 0x20), blk + ids, blk + weights,
                        u32(hi - lo), start, at)
    end = u32(at + 0x50)
    mem.set_u32(me + 0x2C, end)
    mem.set_u64(me + 0x18, blk + at)
    return end


_MESH_ENTRY = struct.Struct("<QQQQIIII")
_ENDS = struct.Struct("<II")
# what moves in each record: pointers into the block by the block's move,
# offsets into it by the bytes grown ahead of them (see _parse_mesh's packs)
_MOVES = {"QQQIIIIIIII": (1, 1, 1, 0, 0, 2, 2, 2, 2, 0, 0),
          "QQQQQIIII": (1, 1, 1, 1, 1, 0, 2, 2, 0),
          "empty": (0, 0, 0, 0, 0, 0, 2, 2, 0)}
_LAYOUTS = {}


def _shifted(buf, at: int, fmt: str, n: int, ptr: int, off: int) -> None:
    # all in map() so the adds run in C: this is most of a re-parse
    layout = _LAYOUTS.get((fmt, n))
    if layout is None:
        moves = _MOVES[fmt] * n
        layout = _LAYOUTS[fmt, n] = (
            struct.Struct("<" + ("QQQQQIIII" if fmt == "empty" else fmt) * n),
            tuple(int(k == 1) for k in moves), tuple(int(k == 2) for k in moves))
    record, ptrs, offs = layout
    record.pack_into(buf, at, *map(operator.add, record.unpack_from(buf, at), map(
        operator.add, map(operator.mul, ptrs, repeat(ptr)), map(operator.mul, offs, repeat(off)))))


def _move_mesh(mem, me: int, cur: int, ptr: int, off: int) -> int:
    """
    a mesh the resize didn't touch, parsed again by moving last parse's
    entries: what _parse_mesh would write for the same bytes `off` further
    along a block that moved by `ptr`. Anything that doesn't line up, or a
    stream with MDR chunks (whose parse writes to the block), has the mesh
    parsed for real by the caller's fallback
    """
    buf = mem.buf
    p, stms, envd, end_at, streams, envds, was_cur, end = _MESH_ENTRY.unpack_from(buf, me)
    p += ptr
    if cur is None:
        cur = (was_cur + off) & 0xFFFFFFFF
    if not stms or not envd or (was_cur + off) & 0xFFFFFFFF != cur or \
            buf[p + 0x32] != streams or buf[p + 0x33] != envds:
        raise _Reshaped
    for k in range(streams):
        head = _U64.unpack_from(buf, stms + k * 0x38)[0] + ptr
        if buf[head + 0x1E] or buf[head + 0x1F]:
            raise _Reshaped
    _shifted(buf, stms, "QQQIIIIIIII", streams, ptr, off)
    if envds:
        _shifted(buf, envd, "QQQQQIIII", envds, ptr, off)
    else:
        _shifted(buf, envd, "empty", 1, ptr, off)
    end += off
    _MESH_ENTRY.pack_into(buf, me, p, stms, envd, end_at + ptr, streams, envds, cur, end)
    return end


def _id(mem, at: int) -> str:
    return mem.get(at, 8).split(b"\0")[0].decode("latin-1")


def release(mem, ps: int) -> None:
    """FUN_0040aca0: the parse's own arrays, freed before a re-parse"""
    _owed(mem).pop(ps, None)
    mdls = mem.u64(ps + MDLS)
    for i in range(mem.u32(ps + MDL_COUNT)):
        m = mdls + i * 0x40
        for j in range(mem.u32(m + 0x30)):
            me = mem.u64(m + 0x18) + j * 0x30
            for field in (0x08, 0x10):
                if mem.u64(me + field):
                    mem.free(mem.u64(me + field))
                    mem.set_u64(me + field, 0)
        if mem.u64(m + 0x18):
            mem.free(mem.u64(m + 0x18))
            mem.set_u64(m + 0x18, 0)
    if mdls:
        mem.free(mdls)
        mem.set_u64(ps + MDLS, 0)
    if mem.u64(ps + MINS):
        for field in (MINS, MINS_AT, MINS_END):
            mem.free(mem.u64(ps + field))
        mem.set_u64(ps + MINS, 0)


def edit(b, n: int, block: bytearray, length: int):
    """FUN_00429420: a wrb block through its edits, and its new length"""
    w = Edit(b, n)
    try:
        # main's own buffer for the block, a tracked one, freed once written
        blk = w.heap.new(length)
        if not blk:
            raise ModelError("its mesh block is empty")
        w.mem.write(blk, bytes(block[:length]))
        cur, length = run(w, blk, length)
        out = bytearray(w.mem.get(cur, length))
        w.heap.delete(cur)
    except (IndexError, struct.error, OverflowError):
        raise ModelError("its recipe edits a mesh in a way that would crash the original tool") from None
    state.pull(b)
    return out, length


def run(w, blk: int, length: int):
    """FUN_00429420 proper, on a block already in memory: (block, length) after"""
    b, mem, n = w.b, w.mem, w.n
    mem.set_u32(w.ps + SIZE, length)
    mem.set_u16(EDIT_BLOCK, n)
    parse(w, w.ps, blk)
    mem.memset(POSES, 0, 0x5DC)
    cur = blk
    untouched = True
    while True:
        line = b.r.read_line(0xFF)
        if line is None:
            break
        if w.verbose:
            w.say(line + "\n")
        head, _, b.r.rest = line.partition(",")
        if head[:1] != ":":
            continue
        second = head[1:2] or "\0"
        digit = second in "123"
        letter = (head[2:3] or "\0") if digit else second
        w.letter = letter
        if letter == "X":
            _bones(w)
            mem.set_u8(COMPONENTS_NAME, 0)
            if mem.u8(COMPONENTS_OPEN):
                b.state.pop("components", None)
                mem.set_u8(COMPONENTS_OPEN, 0)
            if i32(mem.u32(COMPONENTS_COUNT)) > 0:
                w.heap.delete(mem.u64(COMPONENTS_TABLE))
                mem.set_u32(COMPONENTS_COUNT, 0)
            break
        run = EDITS.get(letter)
        if run is None:
            if letter != "\0":
                w.say(f"Invalid Model Edit {head}\n")
            continue
        # B, U, V and W take one argument less in their plain form, and A
        # and W fill it in with 1; after a digit the digit decides
        w.variant = ord(second) - 0x30 if digit else (1 if letter in "BUVW" else 0)
        b.check()
        cur, changed = run(w, cur)
        settle(w)
        if changed:
            untouched = False

    end = mem.u32(w.ps + SIZE)
    if not untouched:
        if n < 99:
            delta = u32(end - length)
            count = mem.u16(state.OUT_COUNT)
            table = mem.u64(state.OUT_TABLE)
            for k in range(n + 1, count):
                mem.set_u32(table + k * 16 + 4, mem.u32(table + k * 16 + 4) + delta)
            mem.set_u32(table + n * 16 + 8, mem.u32(table + n * 16 + 8) + delta)
            mem.set_u32(state.OUT_HEADER + 0x10, mem.u32(state.OUT_HEADER + 0x10) + delta)
            mem.set_u32(state.OUT_HEADER + 0x34, mem.u32(state.OUT_HEADER + 0x34) + delta)
        length = end
    b.state.pop("catalog", None)
    if mem.u8(CATALOG_LOADED):
        if mem.u64(CATALOG_TABLE):
            mem.free(mem.u64(CATALOG_TABLE))
        mem.set_u64(CATALOG_TABLE, 0)
    mem.set_u8(CATALOG_LOADED, 0)
    mem.set_u16(EDIT_BLOCK, 0)
    mem.set_u32(CATALOG_COUNT, 0)
    return cur, length


def _bones(w) -> None:
    """FUN_0040b770: every bone name the meshes' ENVDs bind to, kept for later edits"""
    mem = w.mem
    for k in range(mem.u16(BONE_NAME_COUNT)):
        mem.free(mem.u64(BONE_NAMES + k * 8))
    count = 0
    ps = w.ps
    for i in range(mem.u32(ps + MDL_COUNT)):
        m = w.mdl(i)
        for j in range(mem.u32(m + 0x30)):
            me = mem.u64(m + 0x18) + j * 0x30
            for k in range(mem.u32(me + 0x24)):
                name = mem.u64(mem.u64(me + 0x10) + k * 0x38 + 0x10)
                if any(same_string(mem, mem.u64(BONE_NAMES + q * 8), name) for q in range(count)):
                    continue
                text = mem.cstr(name)
                copy = mem.malloc(len(text) + 1)
                mem.strcpy(copy, text)
                mem.set_u64(BONE_NAMES + count * 8, copy)
                count += 1
                if count > 499:
                    w.say("Bone count over array size.\n")
    mem.set_u16(BONE_NAME_COUNT, count)


def same_string(mem, a: int, b: int) -> bool:
    """FUN_00409aa0 with no length: the C string at a, NUL and all, at b (~ reads as NUL)"""
    k = 0
    while True:
        x, y = mem.u8(a + k), mem.u8(b + k)
        if (0 if x == 0x7E else x) != (0 if y == 0x7E else y):
            return False
        if x == 0:
            return True
        k += 1


def legacy_box(w) -> None:
    """recipes older than version 42 give each edit its own selection box first"""
    if w.version >= 42:
        return
    x0, x1, y0, y1, z0, z1 = (w.F() for _ in range(6))
    call(w, 0x412050, 0, f32(x1 - x0), f32(y1 - y0), f32(z1 - z0), 0.0, 0.0, 0.0,
         f32(f32(x0 + x1) * HALF), f32(f32(y0 + y1) * HALF), f32(f32(z0 + z1) * HALF))


def _after(w) -> None:
    # the edits that break out of the exe's switch drop the legacy selection
    if w.version < 42:
        w.mem.set_u16(LEGACY_VOLUMES, 0)


def _import(w, cur):
    mdl, mesh, flags = w.U(), w.U(), w.U()
    legacy_box(w)
    lo, hi = w.F(), w.F()
    name, catalog = w.T(), w.T(True)
    w.defer_moves = True
    try:
        cur = call(w, 0x41CC80, mdl, mesh, flags, lo, hi, name, catalog, cur)
    finally:
        w.defer_moves = False
        settle(w)
    if w.version < 42:
        w.mem.set_u16(LEGACY_VOLUMES, 0)
    return cur, True


def _star(w, cur):
    a, b, c, d, e = w.U(), w.U(), w.I(), w.I(), w.U()
    legacy_box(w)
    lo, hi = w.F(), w.F()
    call(w, 0x41B420, a, b, c, d, e, lo, hi)
    _after(w)
    return cur, True


def _B(w, cur):
    mdl, mesh = w.U(), w.U()
    third = w.U() if w.variant != 1 else 1
    last = w.U()
    call(w, 0x40E7A0, mdl, mesh, third, last, cur)
    return cur, True


def _C(w, cur):
    mdl, mesh, count, at = w.U(), w.U(), w.S(), w.U()
    return call(w, 0x40C980, mdl, mesh, count, at, cur), True


def _D(w, cur):
    mdl, mesh = w.U(), w.U()
    call(w, 0x40D330, cur, mdl, mesh)
    return cur, True


def _E(w, cur):
    w.mem.set_u16(LEGACY_VOLUMES, w.I())
    return cur, False


def _F(w, cur):
    mdl, mesh, kind = w.U(), w.U(), w.S()
    fs = [w.F() for _ in range(6)]
    a, b = w.T(), w.T()
    return call(w, 0x415D00, mdl, mesh, kind, *fs, a, b, cur), True


def _H(w, cur):
    mdl, mesh, kind = w.U(), w.U(), w.I()
    fs = [w.F() for _ in range(12)]
    call(w, 0x422930, mdl, mesh, kind & 0xFFFF, *fs)
    return cur, True


def _I(w, cur):
    mdl, mesh, trb = w.U(), w.U(), w.U()
    if w.version < 42:
        block = w.U()
    else:
        block = state.find_block(w.b, trb, b"brw") & 0xFFFF
    mdl2, mesh2, shader = w.I(), w.I(), w.I()
    name = w.T(True)
    other = read_block(w, trb, block)
    if w.verbose:
        w.say(f"Insert Mesh (File #, mdl, mesh, mdl2, mesh2, shader, sName) ({trb}, {mdl}, {mesh}, "
              f"{mdl2 & 0xFFFF}, {mesh2 & 0xFFFF}, {shader & 0xFFFF}, {name})\n")
    cur = call(w, 0x40D4F0, mdl, mesh, mdl2 & 0xFFFF, mesh2 & 0xFFFF, shader & 0xFFFF, cur, other, name)
    return cur, True


def read_block(w, trb: int, block: int) -> int:
    """another model's wrb, read whole into a tracked buffer and parsed into w.other"""
    if trb >= len(w.b.trbs):
        raise ModelError("its recipe reads a mesh from a model it doesn't name")
    src = w.b.trbs[trb]
    if block >= src.count - 1:
        raise ModelError("its recipe reads a mesh block its model hasn't got")
    size = u32(src.offset(block + 1) - src.offset(block))
    buf = w.heap.new(i32(size + 1))
    if not buf:
        raise ModelError("its recipe reads a mesh block that's empty")
    w.mem.write(buf, bytes(src.read(src.start + src.offset(block), size)))
    w.mem.set_u32(w.other + SIZE, size)
    parse(w, w.other, buf)
    return buf


def _J(w, cur, scale=None):
    mdl, mesh = w.U(), w.U()
    legacy_box(w)
    if scale is None:
        scale = 1.0
    else:
        scale = w.F()
    name, catalog = w.T(), w.T(True)
    call(w, 0x420710, mdl, mesh, scale, name, catalog)
    _after(w)
    return cur, True


def _j(w, cur):
    return _J(w, cur, scale=True)


def _K(w, cur):
    mem = w.mem
    mdl, mesh, shader = w.U(), w.U(), w.S()
    head = mem.u64(w.mesh(mdl, mesh))
    mem.set_be32(head + 0x34, shader)
    name = w.T().encode("latin-1")
    # strncpy into a zeroed 15: the name never gets a NUL of its own at 15
    mem.write(head + 0x50, name.split(b"\0")[0][:0xF].ljust(0xF, b"\0"))
    return cur, True


def _L(w, cur):
    mdl, mesh, kind = w.U(), w.U(), w.S()
    fs = [w.F() for _ in range(7)]
    legacy_box(w)
    fs += [w.F() for _ in range(3)]
    call(w, 0x421390, mdl, mesh, kind, *fs)
    _after(w)
    return cur, True


def _M(w, cur):
    mdl, mesh, a, b = w.U(), w.U(), w.I(), w.I()
    c, d, e = w.U(), w.U(), w.U()
    call(w, 0x40E5E0, mdl, mesh, a, b, c, d, e)
    return cur, True


def _N(w, cur):
    mdl, mesh, a, b, c = w.U(), w.U(), w.U(), w.U(), w.U()
    cur = grow_faces(w, mdl, mesh, 1, cur)
    edit_poly(w, mdl, mesh, 0, 0, 0, a, b, c)
    return cur, True


def _O(w, cur, legacy=True):
    mdl, mesh, a, b = w.U(), w.U(), w.I(), w.I()
    if legacy:
        legacy_box(w)
    call(w, 0x413C10, mdl, mesh, a, b, cur)
    if legacy:
        _after(w)
    return cur, True


def _r(w, cur):
    return _O(w, cur, legacy=False)


def _P(w, cur):
    args = [w.U() for _ in range(8)]
    edit_poly(w, *args)
    return cur, True


def _Q(w, cur):
    mdl, mesh, kind = w.U(), w.U(), w.U()
    fs = [w.F() for _ in range(5)]
    legacy_box(w)
    last = w.F()
    call(w, 0x420E50, mdl, mesh, kind, fs[0], last, fs[1], fs[2], fs[3], fs[4])
    _after(w)
    return cur, True


def _R(w, cur):
    mdl, mesh, a, b = w.U(), w.U(), w.I(), w.I()
    call(w, 0x40D870, mdl, mesh, a, b, cur)
    return cur, True


def _S(w, cur):
    mdl, mesh = w.U(), w.U()
    fs = [w.F() for _ in range(9)]
    a, b = w.T(), w.T()
    # passed by value out of .rdata: 0, 1, 0, 0
    quad = (0.0, 1.0, 0.0, 0.0)
    return call(w, 0x424220, mdl, mesh, *fs, a, b, quad, cur), True


def _T(w, cur):
    mdl, mesh, a, b = w.U(), w.U(), w.S(), w.S()
    lo, hi = w.F(), w.F()
    flags = u16(w.r.int(optional=True))
    return call(w, 0x41B6A0, mdl, mesh, flags, a, b, lo, hi, cur), True


def _U(w, cur):
    mdl, mesh = w.U(), w.U()
    third = w.U() if w.variant != 1 else 1
    a, b, c = w.I(), w.I(), w.I()
    fs = [w.F() for _ in range(4)]
    call(w, 0x413DC0, mdl, mesh, third, a, b, c & 0xFFFF, *fs)
    return cur, True


def _V(w, cur):
    mdl, mesh = w.U(), w.U()
    third = w.U() if w.variant != 1 else 1
    a, b, c, d = w.I(), w.I(), w.I(), w.I()
    fs = [w.F() for _ in range(3)]
    call(w, 0x412620, mdl, mesh, third, a, b, c & 0xFFFF, d & 0xFFFF, *fs)
    return cur, True


def _e(w, cur):
    mem = w.mem
    kind = w.U()
    if kind == 0xD8F1:
        # -9999: forget every pose
        mem.set_u32(POSE_COUNT, 0)
        mem.memset(POSES, 0, 0x5DC)
    elif kind == 9999:
        mdl, mesh = w.U(), w.U()
        cur = call(w, 0x412D70, mdl, mesh, cur)
    else:
        a, b, c, d = w.F(), w.F(), w.F(), w.F()
        if kind == 9998:
            at = POSES + i32(mem.u32(POSE_COUNT)) * 0x3C
            for k, v in enumerate((a, b, c, d)):
                mem.set_f32(at + 0x24 + k * 4, v)
            mem.set_u32(at + 0x38, 0x8000)
        else:
            e, f = w.F(), w.F()
            boxes = {200: 0, 201: 100, 0xFF37: 0xFF9B}
            if kind in boxes:
                call(w, 0x412050, boxes[kind], f32(b - a), f32(d - c), f32(f - e), 0.0, 0.0, 0.0,
                     f32(f32(a + b) * HALF), f32(f32(c + d) * HALF), f32(f32(e + f) * HALF))
            else:
                g, h, k = w.F(), w.F(), w.F()
                call(w, 0x412050, kind, a, b, c, d, e, f, g, h, k)
    return cur, False


def _Y(w, cur):
    mem, ps = w.mem, w.ps
    kind = w.S()
    count = mem.u32(ps + MDL_COUNT)
    if kind == 1:
        a, b, value = w.U(), w.U(), w.I()
        w.mdl(a)
        mins = mem.u64(mem.u64(ps + MINS) + a * 8)
        mem.set_be32(mins + 0x38 + b * 4, value)
    elif kind == 2:
        at, value = w.S(), w.F()
        for i in range(count):
            mem.set_be_f32(mem.u64(w.mdl(i) + 0x28) + (at + 4) * 4, value)
    elif kind == 3:
        mdl, at, value = w.U(), w.S(), w.F()
        mem.set_be_f32(mem.u64(w.mdl(mdl) + 0x20) + 0x30 + at * 4, value)
    elif kind == 4:
        mdl, mesh, at, value = w.U(), w.U(), w.S(), w.F()
        mem.set_be_f32(mem.u64(w.mesh(mdl, mesh) + 0x18) + 0x30 + at * 4, value)
    elif kind == 5:
        at, scale, shift = w.S(), w.F(), w.F()
        for i in range(count):
            p = mem.u64(w.mdl(i) + 0x28) + (at + 4) * 4
            mem.set_be_f32(p, f32(f32(mem.be_f32(p) * scale) + shift))
    elif kind == 6:
        if mem.u8(ps + NO_PESR) == 0:
            at, value = w.S(), w.F()
            for field in (PESR_A, PESR_B):
                mem.set_be_f32(mem.u64(ps + field) + (at + 4) * 4, value)
        else:
            w.say("No PESR to edit.\n")
    return cur, True


def _a(w, cur):
    mdl, a, mesh, b, c = w.U(), w.I(), w.U(), w.I(), w.U()
    call(w, 0x411360, mdl, a & 0xFFFF, mesh, b & 0xFFFF, c)
    return cur, True


def _c(w, cur):
    mdl, mesh, trb = w.U(), w.U(), w.U()
    block = state.find_block(w.b, trb, b"brw") & 0xFFFF
    a, b = w.U(), w.I()
    name = w.T()
    other = read_block(w, trb, block)
    call(w, 0x425CA0, mdl, mesh, a, b & 0xFFFF, name)
    w.heap.delete(other)
    return cur, False


def _d(w, cur):
    drop_mdl(w, w.U(), cur, True)
    return cur, True


def _m(w, cur):
    drop_mdl(w, w.U(), cur, False)
    return cur, True


def _W(w, cur):
    mdl, mesh = w.U(), w.U()
    if w.variant == 1:
        c, d, e = 1, w.U(), w.I()
        f = 1
    else:
        c, d, e, f = w.U(), w.U(), w.I(), w.U()
    g, h, k, m = w.I(), w.I(), w.U(), w.U()
    call(w, 0x414040, mdl, mesh, c, d, e & 0xFFFF, f & 0xFFFF, g, h, k, m)
    return cur, True


def _o(w, cur):
    mdl, mesh, a, b, c = w.U(), w.U(), w.I(), w.I(), w.S()
    call(w, 0x4267E0, mdl, mesh, a, b, c, cur)
    return cur, True


def _p(w, cur):
    for _ in range(6):
        w.I()
    return cur, True


def _s(w, cur):
    mdl, mesh, a, b, c = w.U(), w.U(), w.U(), w.I(), w.I()
    fs = [w.F() for _ in range(6)]
    call(w, 0x422F60, mdl, mesh, a, b, c, *fs)
    return cur, True


def _u(w, cur):
    mdl, mesh, a, b, c = w.U(), w.U(), w.U(), w.I(), w.I()
    fs = [w.F() for _ in range(4)]
    d, e = w.S(), w.U()
    call(w, 0x41A8E0, mdl, mesh, a, b, c, *fs, d, e)
    return cur, True


def _v(w, cur):
    mdl, mesh, kind, a, b = w.U(), w.U(), w.U(), w.I(), w.I()
    if kind & 0xF == 3:
        picks = tuple(w.T() for _ in range(4))
    else:
        picks = tuple(w.U() for _ in range(4))
    rest = [w.S() for _ in range(4)]
    return call(w, 0x4191A0, mdl, mesh, kind, a, b, picks, *rest, cur), True


def _y(w, cur):
    mdl, mesh = w.U(), w.U()
    a, b, c, d = w.S(), w.S(), w.S(), w.S()
    e, f = w.U(), w.F()
    call(w, 0x4249B0, mdl, mesh, a, b, c, d, e, f)
    return cur, True


def _z(w, cur):
    if w.S() == 0:
        mdl, mesh = w.U(), w.U()
        a, b, c = w.F(), w.F(), w.F()
        call(w, 0x427400, mdl, mesh, a, b, c)
    return cur, False


def _k(w, cur):
    mdl, mesh, a, b = w.U(), w.U(), w.S(), w.S()
    fs = [w.F() for _ in range(5)]
    count = w.U()
    name = w.T()
    if i16(count) < 0:
        raise ModelError("its recipe asks a mesh edit for a negative count")
    names = [w.T() for _ in range(i16(count))]
    return call(w, 0x427820, mdl, mesh, a, b, *fs, count, name, names, cur), True


def _l(w, cur):
    mdl, mesh, kind = w.U(), w.U(), w.S()
    fs = [w.F() for _ in range(7)]
    legacy_box(w)
    fs += [w.F() for _ in range(7)]
    call(w, 0x421DF0, mdl, mesh, kind, *fs)
    _after(w)
    return cur, True


def _n(w, cur):
    mem = w.mem
    kind = w.U()
    if kind & 3:
        if i32(mem.u32(PICKS_COUNT)) > 0:
            w.heap.delete(mem.u64(PICKS))
            mem.set_u32(PICKS_COUNT, 0)
            mem.set_u32(PICKS_USED, 0)
        if kind & 2:
            return cur, False
    a, b, c = w.U(), w.U(), w.U()
    call(w, 0x423700, i16(kind), a, b, c)
    return cur, False


def _i(w, cur):
    mdl, mesh, a, b = w.U(), w.U(), w.U(), w.U()
    c, d, e = w.S(), w.S(), w.S()
    return call(w, 0x417300, mdl, mesh, a, b, c, d, e, cur), True


def _f(w, cur):
    mdl, mesh, a, b, c = w.U(), w.U(), w.I(), w.I(), w.I()
    name = w.T()
    return call(w, 0x419EC0, mdl, mesh, a, b, c & 0xFFFF, name, cur), True


def _A(w, cur):
    mem = w.mem
    mdl, mesh = w.U(), w.U()
    third = 1 if w.variant in (0, 1) else w.U()
    a, b, c = w.U(), w.I(), w.I()
    name = w.T(True).encode("latin-1")
    if name:
        head = mem.u64(w.mesh(mdl, mesh))
        mem.write(head + 0x50, name[:0x10].ljust(0x10, b"\0"))
    return call(w, 0x40ED40, mdl, mesh, third, a, b & 0xFFFF, c & 0xFFFF, cur), True


def _G(w, cur):
    mdl, mesh, a, b, c = w.U(), w.U(), w.I(), w.I(), w.S()
    flags = u16(w.r.int(optional=True))
    smooth = (i16(flags) >> 3) & 1
    if flags & 2:
        call(w, 0x4249B0, mdl, mesh, -1, -1, -1, -1, smooth, 25.0)
    if flags & 1:
        call(w, 0x4267E0, mdl, mesh, a, b, 0, cur)
    call(w, 0x414390 if w.letter == "G" else 0x417F20, mdl, mesh, a, b, c)
    if flags & 4:
        call(w, 0x4249B0, mdl, mesh, -1, -1, -1, -1, smooth, 25.0)
    return cur, True


EDITS = {
    "~": _import, "*": _star, "B": _B, "C": _C, "D": _D, "E": _E, "F": _F, "H": _H,
    "I": _I, "J": _J, "K": _K, "L": _L, "M": _M, "N": _N, "O": _O, "P": _P, "Q": _Q,
    "R": _R, "S": _S, "T": _T, "U": _U, "V": _V, "e": _e, "Y": _Y, "a": _a, "c": _c,
    "d": _d, "W": _W, "o": _o, "p": _p, "r": _r, "s": _s, "u": _u, "v": _v, "y": _y,
    "z": _z, "k": _k, "l": _l, "m": _m, "n": _n, "i": _i, "j": _j, "f": _f, "A": _A,
    "G": _G, "g": _G,
}
def edit_poly(w, mdl, mesh, a, b, c, d, e, f) -> None:
    """FUN_0040bb70: a triangle's corners swapped out (or the last one set, all zero)"""
    mem = w.mem
    me = w.mesh(mdl, mesh)
    head = mem.u64(me)
    verts = mem.be16(head + 0x38)
    faces = mem.be16(head + 0x3A)
    polys = mem.u64(mem.u64(me + 0x08) + 0x10)

    # negative corners count back from the end of the vertex list
    x, y, z, d, e, f = (u16(verts + v) if i16(v) < 0 else v for v in (a, b, c, d, e, f))
    if (a | b | c) & 0xFFFF == 0:
        at = polys + i32(faces * 3 - 3) * 2
        for k, v in enumerate((d, e, f)):
            mem.set_be16(at + k * 2, v)
        return
    at = _find_face(mem, polys, faces, x, y, z)
    if at is not None:
        for q, v in enumerate((d, e, f)):
            mem.set_be16(at + q * 2, v)
        return
    w.say(f"Edit Poly> Poly not found ({x},{y},{z})/({i16(a)},{i16(b)},{i16(c)}) -> ({d},{e},{f})\n")


def _find_face(mem, polys: int, faces: int, x: int, y: int, z: int):
    """the first triangle reading (x, y, z), by a byte search that only
    counts a hit on a triangle's own boundary"""
    if max(x, y, z) > 0xFFFF or polys + faces * 6 > len(mem.buf):
        for k in range(faces):
            at = polys + k * 6
            if (mem.be16(at), mem.be16(at + 2), mem.be16(at + 4)) == (x, y, z):
                return at
        return None
    want = struct.pack(">HHH", x, y, z)
    end = polys + faces * 6
    at = mem.buf.find(want, polys, end)
    while at >= 0:
        if (at - polys) % 6 == 0:
            return at
        at = mem.buf.find(want, at + 1, end)
    return None


def shift(w, mdl: int, mesh: int, ps: int, delta: int, shrink: bool) -> None:
    """FUN_0040bdc0: a mesh grew or shrank by delta, so every size above it moves too"""
    mem = w.mem
    delta = -delta if shrink else delta
    m = mem.u64(ps + MDLS) + mdl * 0x40
    head = mem.u64(m)
    if mdl == u32(mem.u32(ps + MDL_COUNT) - 1):
        mem.set_u32(head + 0x0C, 0)
    elif mem.be32(head + 0x0C) != 0:
        mem.set_be32(head + 0x0C, mem.be32(head + 0x0C) + delta)
    if i16(mesh) >= 0:
        me = mem.u64(mem.u64(m + 0x18) + i16(mesh) * 0x30)
        mem.set_be32(me + 0x08, mem.be32(me + 0x08) + delta)
        mem.set_be32(me + 0x0C, mem.be32(me + 0x0C) + delta)
    mem.set_be32(head + 0x08, mem.be32(head + 0x08) + delta)
    blk = mem.u64(ps)
    for at in (0x38, 0x5C, 0x58):
        mem.set_be32(blk + at, mem.be32(blk + at) + delta)
    mem.set_u32(blk + 0x10, mem.u32(blk + 0x10) + delta)
    mem.set_u32(ps + SIZE, mem.u32(ps + SIZE) + delta)


def grow_verts(w, mdl: int, mesh: int, added: int, ps: int, old: int) -> int:
    """FUN_0040c090: room for `added` more vertices in every stream of a mesh"""
    mem, heap = w.mem, w.heap
    _due(w, ps, mdl, mesh)
    me = mem.u64(ps + MDLS) + mdl * 0x40
    me = mem.u64(me + 0x18) + mesh * 0x30
    head = mem.u64(me)
    mem.set_be16(head + 0x38, mem.be16(head + 0x38) + added)
    grow = 0
    streams = mem.u8(head + 0x32)
    stms = mem.u64(me + 0x08)
    for k in range(1, streams):
        s = stms + k * 0x38
        stride = mem.be16(mem.u64(s) + 0x1A)
        count = mem.be32(mem.u64(s) + 0x14)
        end = i32((count + added) * stride + mem.u32(s + 0x2C))
        grow = i32(grow + ((-end) & 0xF) + i32(end - mem.u32(s + 0x28)))
    size = i32(mem.u32(ps + SIZE))
    new = heap.new(i32(grow + 1 + size))
    done = i32(mem.u32(mem.u64(me + 0x08) + 0x5C))
    shift(w, mdl, mesh, ps, grow, False)
    heap.copy(1, new, 0, old, 0, done)
    if mem.u8(mem.u64(me) + 0x32) > 1:
        for k in range(1, mem.u8(mem.u64(me) + 0x32)):
            s = mem.u64(me + 0x08) + k * 0x38
            p = mem.u64(s)
            stride = mem.be16(p + 0x1A)
            count = mem.be32(p + 0x14)
            span = mem.be32(p + 0x0C)
            more = i32(stride * added)
            start, body, pad_end = i32(mem.u32(s + 0x24)), i32(mem.u32(s + 0x2C)), i32(mem.u32(s + 0x28))
            end = i32(body + stride * count + more)
            end = i32(((-end) & 0xF) + end)
            mem.set_be32(p + 0x14, count + added)
            mem.set_be32(mem.u64(s) + 0x0C, (span - pad_end) + end)
            mem.set_be32(mem.u64(s) + 0x08, mem.be32(mem.u64(s) + 0x08) + more)
            heap.copy(1, new, done, old, i32(mem.u32(s + 0x24)), i32(body + stride * count - start))
            done = i32(done + (end - i32(mem.u32(s + 0x24))))
    me_now = mem.u64(ps + MDLS) + mdl * 0x40
    envd = mem.u64(mem.u64(me_now + 0x18) + mesh * 0x30 + 0x10)
    tail = i32(mem.u32(envd + 0x2C))
    heap.copy(1, new, done, old, tail, size - tail)
    parse(w, ps, new, True, (mdl, mesh, grow))
    heap.delete(old)
    return new


def grow_faces(w, mdl: int, mesh: int, added: int, old: int, ps: int = None) -> int:
    """FUN_0040c440: room for `added` more triangles in a mesh's index stream"""
    mem, heap = w.mem, w.heap
    ps = w.ps if ps is None else ps
    _due(w, ps, mdl, mesh)
    me = w.mesh(mdl, mesh, ps)
    stms = mem.u64(me + 0x08)
    first = mem.u64(stms)
    stride = mem.be16(first + 0x1A)
    count = mem.be32(first + 0x14)
    span = mem.be32(first + 0x0C)
    size = i32(mem.u32(ps + SIZE))
    more = (added * 3) & 0xFFFF
    extra = i32(stride * more)
    body = i32(stride * count + mem.u32(stms + 0x2C))
    grow = i32(extra + body)
    grow = i32(((-grow) & 0xF) + i32(grow - mem.u32(stms + 0x28)))
    new = heap.new(i32(size + 1 + grow))
    count = u32(more + count)
    mem.set_be32(mem.u64(stms) + 0x14, count)
    mem.set_be16(mem.u64(me) + 0x3A, u16(count // 3))
    mem.set_be32(mem.u64(stms) + 0x0C, span + grow)
    mem.set_be32(mem.u64(stms) + 0x08, mem.be32(mem.u64(stms) + 0x08) + extra)
    shift(w, mdl, mesh, ps, grow, False)
    heap.copy(1, new, 0, old, 0, body)
    tail = i32(mem.u32(mem.u64(me + 0x08) + 0x28))
    heap.copy(1, new, tail + grow, old, tail, size - tail)
    heap.delete(old)
    parse(w, ps, new, True, (mdl, mesh, grow))
    return new


def grow_envd(w, mdl: int, mesh: int, envd: int, added: int, ps: int, old: int):
    """FUN_0040c680: room for `added` more bones in one of a mesh's ENVDs; (block, bones before)"""
    mem, heap = w.mem, w.heap
    _due(w, ps, mdl, mesh)
    me = mem.u64(mem.u64(ps + MDLS) + mdl * 0x40 + 0x18) + mesh * 0x30
    e = mem.u64(me + 0x10) + envd * 0x38
    start, finish = i32(mem.u32(e + 0x2C)), i32(mem.u32(e + 0x30))
    table = mem.u64(e + 0x08)
    had = mem.be16(table + 2)
    names_at = mem.be16(table + 4)
    names_end = mem.be16(table + 6)
    bones = added + had
    size = i32(mem.u32(ps + SIZE))
    words = u32(bones * 2 + 2) & 0x7FFFC
    names = i32(names_at + 0x10 + words)
    pad = u32(-(bones + names)) & 0xF
    grow = i32((start - finish) + names_at + 0x10 + bones + words + pad)
    new = heap.new(i32(size + 1 + grow))
    mem.set_be16(table + 2, had + added)
    mem.set_be16(table + 8, had + added)
    mem.set_be16(table + 6, i16(names) - 0x10)
    mem.set_u16(table + 0x0C, mem.u16(table + 6))
    head = mem.u64(e)
    mem.set_be32(head + 0x0C, mem.be32(head + 0x0C) + grow)
    mem.set_be32(head + 0x08, (u32(-(bones + names)) & 3) + mem.be32(head + 0x0C) - pad)
    shift(w, mdl, mesh, ps, grow, False)
    keep = i32(start + 0x10 + names_end)
    heap.copy(1, new, 0, old, 0, keep)
    heap.copy(1, new, start + names, old, keep, had)
    heap.copy(1, new, finish + grow, old, finish, size - finish)
    parse(w, ps, new, True, (mdl, mesh, grow))
    heap.delete(old)
    return new, had


def drop_mdl(w, mdl: int, cur: int, with_mins: bool) -> None:
    """FUN_0040b950: an MDL cut out of the block (and its MINS with it, for :d)"""
    mem, heap, ps = w.mem, w.heap, w.ps
    w.mdl(mdl)
    if mdl == 0 and mem.u32(ps + MDL_COUNT) == 1:
        # the exe clears a field of the MDL before the first one, and crashes
        raise ModelError("its recipe removes the only part of a mesh")
    if with_mins:
        mict = mem.u64(ps + MICT)
        if u32(mem.be32(mict + 0x10) - 1) == mdl:
            mem.set_u32(mem.u64(mem.u64(ps + MINS) + mdl * 8 - 8) + 0x0C, 0)
        mem.set_be32(mict + 0x10, mem.be32(mict + 0x10) - 1)
        lo = i32(mem.u32(mem.u64(ps + MINS_END) + mdl * 4))
        gone = i32(mem.u32(mem.u64(ps + MINS_AT) + mdl * 4) - lo)
        heap.copy(1, cur, i32(mem.u32(mem.u64(ps + MINS_AT) + mdl * 4)), cur, lo,
                  i32(mem.u32(ps + SIZE)) - lo)
        mem.set_be32(mict + 0x08, gone + mem.be32(mict + 0x08))
        _resize_top(mem, ps, gone)
    mdls = mem.u64(ps + MDLS)
    if u32(mem.u32(ps + MDL_COUNT) - 1) == mdl:
        mem.set_u32(mem.u64(mdls + mdl * 0x40 - 0x40) + 0x0C, 0)
    lo = i32(mem.u32(mdls + mdl * 0x40 + 0x38))
    gone = i32(mem.u32(mdls + mdl * 0x40 + 0x34) - lo)
    heap.copy(1, cur, i32(mem.u32(mdls + mdl * 0x40 + 0x34)), cur, lo, i32(mem.u32(ps + SIZE)) - lo)
    _resize_top(mem, ps, gone)
    mem.set_u32(ps + MDL_COUNT, mem.u32(ps + MDL_COUNT) - 1)
    blk = mem.u64(ps)
    mem.set_be32(blk + 0x60, mem.be32(blk + 0x60) - 1)
    mem.set_be32(blk + 0x94, mem.be32(blk + 0x94) - 1)
    parse(w, ps, cur, True)


def _resize_top(mem, ps: int, delta: int) -> None:
    blk = mem.u64(ps)
    for at in (0x38, 0x5C, 0x58):
        mem.set_be32(blk + at, delta + mem.be32(blk + at))
    mem.set_u32(blk + 0x10, mem.u32(blk + 0x10) + delta)
    mem.set_u32(ps + SIZE, mem.u32(ps + SIZE) + delta)
