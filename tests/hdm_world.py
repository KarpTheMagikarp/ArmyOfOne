"""
.trb and .imgb files laid out the way FF XIII HD Models.exe reads them, for
the native builder's tests and for running the real exe on the same input
"""

import struct

from patcher.mods.hdmodels.texture import mip_sizes


def trb(blocks, names=None, tags=None, records=None, words=None) -> bytes:
    """
    a .trb of `blocks` (bytes each) and the two blocks the format ends on: a 4
    byte tag and a 16 byte record per block, then the names
    """
    count = len(blocks) + 2
    tags = tags or [b"bk%02d" % n for n in range(count)]
    records = records or [(b"rec%d" % n).ljust(16, b"\0") for n in range(count)]
    names = names or [b"name%d" % n for n in range(count)]
    words = words or [2] * count
    body = bytearray()
    table = []
    for n, data in enumerate(blocks):
        table.append((n, len(body), len(data), words[n]))
        body += data + bytes(-len(data) & 0xF)
    meta = b"".join(t[:4].ljust(4, b"\0") for t in tags)
    table.append((count - 2, len(body), count * 0x14 + 0x40, words[count - 2]))
    body += meta + bytes(4)
    table.append((count - 1, len(body), (count + 2) * 0x20, words[count - 1]))
    body += b"".join(r[:16].ljust(16, b"\0") for r in records)
    pool = b"".join(n + b"\0" for n in names)
    names_at = len(body)
    start = 0x40 + count * 16
    header = [0] * 16
    header[0] = 0x31425254
    header[4] = start + names_at + len(pool)
    header[12] = count
    header[13] = names_at
    header[14] = count
    out = struct.pack("<16I", *header)
    out += b"".join(struct.pack("<4I", *row) for row in table)
    return out + bytes(body) + pool


def txb(fmt: int, width: int, height: int, mips: int, at: int = 0, extra: int = 0) -> tuple:
    """a txb block pointing into an .imgb at `at`, and the pixel bytes its mips take"""
    head = bytearray(0x50)
    head[0:8] = b"SEDBtxb\0"
    head[0x46] = fmt
    head[0x47] = mips
    struct.pack_into(">H", head, 0x4A, width)
    struct.pack_into(">H", head, 0x4C, height)
    sizes = mip_sizes(fmt, width, height, mips)
    levels = mips + 1
    levels += levels & 1
    table = [0] * (levels * 2 + extra)
    table[0] = 0x18
    pos = at
    for n, size in enumerate(sizes):
        table[2 + n * 2] = pos
        table[3 + n * 2] = size
        pos += max(size, 0x10) if size else 0
    block = bytes(head) + b"".join(struct.pack(">I", w) for w in table)
    return block, pos - at


def recipe(trbs, out, blocks, edits=(), version=42, flags=0, swaps=(), all_count=1,
           imgb=None, names=(), names_first=False) -> bytes:
    """
    a model conversion .txt: header, models, outputs, block list, then edits.
    `names` is the second swap list (whole names, the one skeletons get)
    """
    lines = [str(version), str(flags), str(len(swaps))]
    if version > 14:
        lines.append(str(all_count))
    for frm, to in swaps:
        lines += [frm, to]
    lines.append(str(-len(names) if names_first else len(names)))
    for frm, to in names:
        lines += [frm, to]
    lines.append(str(len(trbs)))
    lines += trbs
    lines.append(out + ".trb")
    lines.append(imgb if imgb is not None else out + ".imgb")
    lines.append(str(len(blocks)))
    lines += [",".join(str(v) for v in b) for b in blocks]
    lines += list(edits)
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")


def bone(name=0, kind=5, pos=(0.0, 0.0, 0.0), rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0),
         parent=-1, child=-1, sibling=-1, order=0, w48=2, w4c=5, w2=2, w3=-1, tail=None) -> bytes:
    """one 0xb0 byte bone of a SKL block; floats go in as floats, or as ints for raw bits"""
    def word(v):
        return struct.pack("<f", v) if isinstance(v, float) else struct.pack("<I", v & 0xFFFFFFFF)

    head = [name, kind, w2, w3, *pos, *rot, *scale, parent, child, sibling, order, w48, w4c]
    out = b"".join(word(v) for v in head)
    return out + (tail if tail is not None else bytes(0xB0 - len(out)))[:0xB0 - len(out)]


def skl(bones, names, version=9100, extra=b"", entries=None, counter=None, header=None) -> bytes:
    """
    a SKL block: the 0x40 header, an entry per part (bones, and the extra
    blob when there is one), the bones, the blob, then the names NUL
    separated and ended the way the game's are (HierarchyData, and
    KineDriver from version 9000 on), padded to 4
    """
    pool = b"".join(n + b"\0" for n in names) + b"HierarchyData\0"
    if version >= 9000:
        pool += b"KineDriver\0"
    pool += bytes(-len(pool) & 3)
    data = b"".join(bones)
    rows = entries or ([(len(bones), 0, len(data), 0)] +
                       ([(1, len(data), len(extra), 0)] if extra else []))
    head = bytearray(header if header is not None else bytes(0x40))
    head[0:8] = b"SEDBSKL\0"
    start = 0x40 + len(rows) * 16
    struct.pack_into("<I", head, 0x08, version)
    struct.pack_into("<I", head, 0x10, start + len(data) + len(extra) + len(pool))
    struct.pack_into("<I", head, 0x30, len(rows))
    struct.pack_into("<I", head, 0x34, len(data) + len(extra))
    struct.pack_into("<I", head, 0x38, len(bones) if counter is None else counter)
    table = b"".join(struct.pack("<4I", *(v & 0xFFFFFFFF for v in row)) for row in rows)
    return bytes(head) + table + data + extra + pool


# the vertex layout every real character mesh's second stream has: (offset, type, two words)
WRB_LAYOUT = ((0x00, 0x0, 4, 4), (0x08, 0x2, 3, 4), (0x0C, 0xF, 6, 4), (0x10, 0xE, 3, 4),
              (0x14, 0x3, 3, 4), (0x18, 0x8, 2, 2), (0x1C, 0xD, 3, 4))


def _chunk(tag: bytes, size: int, total: int, count: int = 0, body: bytes = b"") -> bytes:
    """a 0x20 byte header the way wrb chunks start: tag, used size, padded size, a count"""
    head = bytearray(0x20)
    head[0:len(tag)] = tag
    struct.pack_into(">III", head, 0x08, size, total, count)
    return bytes(head) + body


def _pad16(data: bytes) -> bytes:
    return data + bytes(-len(data) & 0xF)


def wrb_mesh(verts: int, faces, envds=(), stride: int = 32, layout=WRB_LAYOUT, name=b"mesh",
             shader: int = 0, seed: int = 0, ids=(0, 0)) -> bytes:
    """
    one MESH: an index stream of `faces` (triangles), a vertex stream of
    `verts` made up vertices in `layout`, then an ENVD per (bone name, vertex
    list, weights) in `envds`. Blend indices (type 15) only ever name an ENVD
    the mesh has, or 0xff
    """
    polys = b"".join(struct.pack(">3H", *f) for f in faces)
    rows = bytes.fromhex("00000000000000000000000100ff0000")
    used = 0x30 + len(polys)
    s0 = bytearray(_chunk(b"STMS", used, used + (-used & 0xF), 1) + rows + polys)
    struct.pack_into(">IHH", s0, 0x14, len(faces) * 3, ids[0], 2)
    s0 = _pad16(bytes(s0))

    table = b"".join(struct.pack(">IIIHH", off, a, b, kind, 0) for off, kind, a, b in layout)
    data = bytearray()
    for v in range(verts):
        vert = bytearray(((v * 37 + k * 11 + seed) * 29) & 0xFF for k in range(stride))
        # one past the last ENVD stands for "no bone": 0xff, and never weighted
        picks = [(v + k + seed) % (len(envds) + 1) for k in range(4)]
        bones = [p if p < len(envds) else 0xFF for p in picks]
        weights = [w if b != 0xFF else 0 for w, b in
                   zip((0x80, 0x40 if v % 3 else 0, 0x3F if v % 5 else 0, 0), bones)]
        for off, kind, _, _ in layout:
            if kind == 0xF and off + 4 <= stride:
                vert[off:off + 4] = bytes(bones)
            elif kind == 0xE and off + 4 <= stride:
                vert[off:off + 4] = bytes(weights)
        data += vert
    used = 0x20 + len(table) + len(data)
    s1 = bytearray(_chunk(b"STMS", used, used + (-used & 0xF), len(layout)) + table + bytes(data))
    struct.pack_into(">IHH", s1, 0x14, verts, ids[1], stride)
    s1 = _pad16(bytes(s1))

    parts = []
    for bone_name, vlist, weights in envds:
        names = bone_name + bytes(-len(bone_name) % 4 or 4)
        names_at = 0x10 + len(names)
        n = len(vlist)
        between = -(2 * n) & 3
        last = names_at + 2 * n + between
        tail = 2 * n + between + n
        tail += -tail & 3
        size = tail + names_at + 0x10
        total = size + (-(names_at + tail) & 0xF)
        body = bytearray(struct.pack(">8H", 0x10, n, names_at, last, n, names_at, last, 0))
        body += names + b"".join(struct.pack(">H", v) for v in vlist) + bytes(between)
        body += bytes(weights)
        body += bytes(total - 0x10 - len(body))
        # ENVD's header is only 0x10, its table sits where a chunk's count would
        parts.append(_chunk(b"ENVD", size, total)[:0x10] + bytes(body))

    # the boxes inside a closing chunk only get 0x10 byte headers
    trailer = _chunk(b"AABB", 0x50, 0, 1) + _chunk(b"AABB", 0x28, 0)[:0x10] \
        + struct.pack(">6f", -1, 0, -1, 1, 2, 1) + bytes(8)
    head = bytearray(0xE0)
    head[0:4] = b"MESH"
    head[0x32], head[0x33] = 2, len(envds)
    struct.pack_into(">IHH", head, 0x34, shader, verts, len(faces))
    head[0x40:0x43] = b"STR"
    struct.pack_into(">I", head, 0x48, 0x11 + len(name))
    head[0x50:0x50 + 16] = name[:16].ljust(16, b"\0")
    for k, tag in enumerate((b"RSID", b"RSTP", b"PRID", b"PRTP")):
        head[0x60 + k * 0x20:0x64 + k * 0x20] = tag
    body = bytes(head[0x20:]) + s0 + s1 + b"".join(parts) + trailer
    size = 0x20 + len(body)
    return _chunk(b"MESH", size, size, 8 + len(envds))[:0x20] + body


def wrb(mdls, mins_big=False) -> bytes:
    """
    a whole wrb block: MDLs, each a list of wrb_mesh() bytes, then LTCD,
    MICT and a MINS per MDL, no PESR. Laid out like a real character's
    """
    body = bytearray()
    bodies = []
    for k, meshes in enumerate(mdls):
        name = _chunk(b"NAME", 0x23, 0x30)[:0x20] + b"group%d" % k + bytes(0x10 - len(b"group%d" % k))
        name = name[:0x0C] + struct.pack(">I", 0x30) + name[0x10:]
        lst = bytearray(_chunk(b"HEAD", 0x2C, 0x30)[:0x20] + bytes(0x10))
        struct.pack_into(">I", lst, 0x24, len(meshes))
        close = _chunk(b"AABB", 0x80, 0, 2) + _chunk(b"AABB", 0x28, 0x30)[:0x10] \
            + struct.pack(">6f", -1, 0, -1, 1, 2, 1) + bytes(8) \
            + _chunk(b"COMP", 0x28, 0)[:0x10] + struct.pack(">6f", -0.5, 0, -0.5, 0.5, 1.5, 0.5) + bytes(8)
        inner = bytes(name) + bytes(lst) + b"".join(meshes) + close
        size = 0x20 + len(inner)
        bodies.append((size, inner, len(meshes)))
    for k, (size, inner, n) in enumerate(bodies):
        head = _chunk(b"MDL", size, 0 if k == len(bodies) - 1 else size, 2 + n)
        body += head + inner
    ltcd = _chunk(b"LTCD", 0x40, 0x40, 1) + _chunk(b"HEAD", 0x18, 0)
    mins = b""
    for k in range(len(mdls)):
        # NAME's size at +0x68 decides the MINS's: past 0x20 it takes 0x90, else 0x80
        m = bytearray(_chunk(b"MINS", 0x80, 0x80, 2) + _chunk(b"HEAD", 0x40, 0x40) + bytes(0x20))
        struct.pack_into(">IIHH", m, 0x40, 0xFFFFFFFF, 3, 0x0F05, 0)
        struct.pack_into(">f", m, 0x50, -1.0)
        m += _chunk(b"NAME", 0x23 if mins_big else 0x20, 0)
        m = m.ljust(0x90 if mins_big else 0x80, b"\0")
        mins += bytes(m)
    mict = _chunk(b"MICT", 0x20 + len(mins), 0x20 + len(mins), len(mdls))
    tail = ltcd + mict + mins
    size = 0xA0 + len(body) + len(tail)
    head = bytearray(0xA0)
    head[0:8] = b"SEDBwrb\0"
    struct.pack_into(">I", head, 0x08, 0x18)
    struct.pack_into("<I", head, 0x10, size)
    head[0x30:0x33] = b"WRB"
    struct.pack_into(">II", head, 0x38, size - 0x30, 0)
    struct.pack_into(">I", head, 0x40, len(mdls) + 1)
    head[0x50:0x54] = b"MDLC"
    struct.pack_into(">II", head, 0x58, size - 0x80, size - 0x80)
    struct.pack_into(">I", head, 0x60, len(mdls) + 1)
    head[0x70:0x74] = b"HEAD"
    struct.pack_into(">II", head, 0x78, 0x2C, 0x30)
    struct.pack_into(">I", head, 0x94, len(mdls))
    head[0x98] = 1
    return bytes(head) + bytes(body) + tail


def wrb_mesh_from(vertices, faces, layout=WRB_LAYOUT, **kw) -> bytes:
    """wrb_mesh() with the vertex stream given outright: one bytes per vertex, all one stride"""
    stride = len(vertices[0])
    mesh = bytearray(wrb_mesh(len(vertices), faces, stride=stride, layout=layout, **kw))
    s0 = 0x30 + len(faces) * 6
    at = 0xE0 + s0 + (-s0 & 0xF) + 0x20 + 0x10 * len(layout)
    mesh[at:at + stride * len(vertices)] = b"".join(vertices)
    return bytes(mesh)
