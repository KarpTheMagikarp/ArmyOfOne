"""
The skeleton editor (FUN_00402a00) and the skeleton loading the mesh editor
shares, ported line for line onto cmem.Mem.

A SKL block, little endian:

    0x00  "SEDBSKL", +0x08 a version word (9000 and up also ends its names
          on KineDriver), +0x10 the block's size, +0x30 how many entries,
          +0x34 where the names start, counted from the data, +0x38 a counter
          that goes up with every bone added
    0x40  the entries, 16 bytes each: a count, an offset and a size into the
          data. The first is the bones, the second (when there is one) a blob
          carried as it is
    ...   the data: 0xb0 bytes a bone, the blob, then the names, NUL
          separated. A bone's word 0 says which name is its own, counted by
          NULs, so bones and names can be reordered apart

skelTable (0x45a540) has a 0x68 byte slot per model plus one, the last, for
the skeleton being edited (the "primary", 0x45a538): the block's header, then
pointers to its entries, bones and names, and their counts. A model's slot is
filled the first time anything asks for its skeleton and stays until freed,
so which commands ran before decides whether a later one sees the names
swapped or not. That's the exe's behaviour and it's kept.
"""

from patcher.mods.hdmodels import state
from patcher.mods.hdmodels.build import ModelError
from patcher.mods.hdmodels.cnum import i16, i32, stoi, u32

SKEL_TABLE = 0x45A540   # -> 0x68 bytes a model, and the primary's last
PRIMARY = 0x45A538      # -> the primary's slot
EXTRA = 0x45A530        # -> the primary's second entry, kept to write back
BLOCK_N = 0x45A528      # i16, the output block being edited
MAX_BONE = 0x45A526     # i16
MAX_NAME = 0x45A524     # u16, the highest name number handed out
NO_LINK = 0x45A522      # u8, set while :O adds bones it links up itself
MODEL_N = 0x45A520      # i16, the model the block came from
REQUIRED_COUNT = 0x45AD46   # u16, bones the mesh editor says it needs
REQUIRED = 0x45AD60         # -> names, 8 bytes each

SLOT = 0x68
VERSION, SIZE, ENTRY_COUNT, NAMES_AT, COUNTER = 0x08, 0x10, 0x30, 0x34, 0x38
ENTRIES, BONES, NAMES, MODEL, BONE_COUNT, DATA_AT, NAMES_SIZE = (
    0x40, 0x48, 0x50, 0x58, 0x5A, 0x5C, 0x60)

BONE = 0xB0
NAME, KIND, POS, ROT, SCALE = 0x00, 0x04, 0x10, 0x1C, 0x2C
PARENT, CHILD, SIBLING, ORDER, FLAGS13 = 0x38, 0x3C, 0x40, 0x44, 0x4C

ONE = 0x3F800000
# FUN_00402a00's FaceFix list, copied off .data (0x44a080) at every call
FACE = [b"C_U", b"C_Jaw", b"C_throat", b"C_wrin", b"L_Dlid", b"L_U", b"L_Ulid", b"L_brow",
        b"L_cheek", b"L_cor", b"L_eye", b"L_nose", b"L_puff", b"R_Dlid", b"R_U", b"R_Ulid",
        b"R_brow", b"R_cheek", b"R_cor", b"R_eye", b"R_nose", b"R_puff", b"C_D", b"C_chin",
        b"C_tong", b"L_D", b"L_broln", b"R_D", b"R_broln", b"L_wrin", b"R_wrin"]
# E's parameter number -> the bone word it sets; 6 to 15 are the floats
E_WORDS = {0: KIND, 1: NAME, 2: PARENT, 3: CHILD, 4: SIBLING, 5: FLAGS13, 16: ORDER}
# a parent chain this long is a loop, which blows the exe's stack
DEEPEST = 4000


def compare(a: bytes, b: bytes, n: int, fold: bool = False) -> int:
    """
    FUN_00409990: strncmp with the exe's quirks. ~ in either string reads as
    a NUL, `fold` uppercases a-z, n < 1 means "up to a's NUL". Past the end
    of either string reads as 0
    """
    if n < 1:
        whole = len(a.split(b"\0", 1)[0]) + 1
        n = whole if (whole <= -n or n == 0) else -n
        if n < 1:
            return 0
    la, lb = len(a), len(b)
    k = 0
    while True:
        x = a[k] if k < la else 0
        y = b[k] if k < lb else 0
        diff = None
        if fold:
            if 0x61 <= x <= 0x7A:
                up = x - 0x20
                if not 0x61 <= y <= 0x7A:
                    if y == 0x7E:
                        return up
                    diff = up - y
                else:
                    diff = up - (y - 0x20)
            elif 0x61 <= y <= 0x7A:
                diff = (0 if x == 0x7E else x) - (y - 0x20)
        if diff is None:
            if x != 0x7E:
                diff = x if y == 0x7E else x - y
            elif y != 0x7E:
                diff = -y
            else:
                k += 1
                if k >= n:
                    return 0
                continue
        if diff & 0xFFFF or x == 0:
            return diff
        k += 1
        if k >= n:
            return 0


def _slot(m, n: int) -> int:
    return m.u64(SKEL_TABLE) + n * SLOT


def _check_slot(m, n: int) -> None:
    if not 0 <= n <= m.u16(state.TRB_COUNT):
        raise ModelError("its recipe points the skeleton editor at a model it doesn't name")


def _names_size(m, slot: int) -> int:
    size = m.i32(slot + NAMES_SIZE)
    # the exe walks names with a 16 bit index, which never gets to the end
    # of a pool this big: it'd spin forever
    if size > 0xFFFF:
        raise ModelError("a skeleton's names run past 64K")
    return size


def _tail(m, slot: int, at: int, limit: int = 1 << 30) -> bytes:
    """the names from `at` to the end of the slot's buffer, the way a C string read sees them"""
    end = m.u64(slot + NAMES) + max(m.i32(slot + NAMES_SIZE), 0)
    return m.get(at, max(min(end - at, limit), 0))


def _cstr(m, slot: int, at: int) -> bytes:
    return _tail(m, slot, at).split(b"\0", 1)[0]


def _byte(m, slot: int, k: int) -> int:
    """a names byte, 0 past the buffer"""
    if 0 <= k < m.i32(slot + NAMES_SIZE):
        return m.u8(m.u64(slot + NAMES) + k)
    return 0


def name_at(m, slot: int, word: int) -> int:
    """
    the inlined walk everywhere: the name a bone's word points at, counted
    by NULs in 16 bits. Not found (or name 0) is the first name
    """
    names = m.u64(slot + NAMES)
    size = _names_size(m, slot)
    want = word & 0xFFFF
    if size > 0 and want:
        pool = m.get(names, size)
        seen = 0
        nul = pool.find(b"\0")
        while 0 <= nul and nul + 1 < size:
            seen += 1
            if seen == want:
                return names + nul + 1
            nul = pool.find(b"\0", nul + 1)
    return names


def _bone_name(m, slot: int, n: int) -> bytes:
    """a bone's name for a message, without reading past anything"""
    if not 0 <= n < m.u16(slot + BONE_COUNT):
        return b"?"
    return _cstr(m, slot, name_at(m, slot, m.u32(m.u64(slot + BONES) + n * BONE)))


def _text(name: bytes) -> str:
    return name.decode("latin-1")


def find_lks(b, model: int) -> int:
    """FUN_0040a660(model, "lks"), its answer only ever printed or used to read the block"""
    model &= 0xFFFF
    if model >= len(b.trbs):
        raise ModelError("its recipe points the skeleton editor at a model it doesn't name")
    return state.find_block(b, model, b"lks")


def find_bone(b, name: bytes, model: int, start: int = 0) -> int:
    """
    FUN_00401e90: a bone by name in a model's skeleton, from bone `start` on.
    The name is looked up first, case blind, then the first bone at or past
    `start` wearing that name's number. -1 no such name, -2 no such bone
    """
    m = b.mem
    model &= 0xFFFF
    _check_slot(m, model)
    slot = _slot(m, model)
    key = name.split(b"\0", 1)[0] + b"\0"
    size = _names_size(m, slot)
    if size <= 0:
        return -1
    pool = m.get(m.u64(slot + NAMES), size)
    k = number = 0
    while True:
        # k is where a name starts; compare() never reads past len(key)
        if compare(key, pool[k:k + len(key)], 0, True) & 0xFFFF == 0:
            count = m.u16(slot + BONE_COUNT)
            if start & 0xFFFF >= count:
                return -2
            bones = m.u64(slot + BONES)
            if number & 0xFFFF != m.u32(bones + (start & 0xFFFF) * BONE):
                at = bones + ((start + 1) & 0xFFFF) * BONE
                while True:
                    start += 1
                    if start & 0xFFFF == count:
                        return -2
                    word = m.u32(at)
                    at += BONE
                    if word == number & 0xFFFF:
                        break
            return i16(start)
        nul = pool.find(b"\0", k)
        if nul < 0 or nul + 1 >= size:
            return -1
        k = nul + 1
        number += 1


def load(b, model: int, swap: bool, primary: bool) -> int:
    """
    FUN_00402620: a model's skeleton into its slot (or the primary's), unless
    it's there already. The block is the model's first tagged "lks", read
    straight from the file, so the recipe's name swaps never touch it; the
    second list does, when `swap`. Negative when it isn't a skeleton
    """
    m = b.mem
    count = m.u16(state.TRB_COUNT)
    table = m.u64(SKEL_TABLE)
    if not table:
        table = m.malloc((count + 1) * SLOT)
        for n in range(count + 1):
            m.set_u16(table + n * SLOT + MODEL, 0xFFFF)
            m.set_u16(table + n * SLOT + BONE_COUNT, 0)
        m.set_u64(SKEL_TABLE, table)
        m.set_u64(PRIMARY, table + i16(count) * SLOT)
    model16 = model & 0xFFFF
    if primary:
        result, n = count, i16(count)
    else:
        result, n = model, i16(model16)
    _check_slot(m, n)
    slot = table + n * SLOT
    if m.i16(slot + MODEL) >= 0:
        return result

    k = find_lks(b, model16)
    src = b.trbs[model16]
    if k < 0:
        # the exe reads "block -1", the heap header in front of the table,
        # which never starts SEDBSKL, so it always lands on this message
        b.say(f"Error: GetSkeleton: Block {i16(model16)}, -1 specified is not a Skeleton.\n")
        return -1
    if k + 1 >= src.count:
        raise ModelError("its recipe asks for a skeleton from a model that hasn't got one")
    size = u32(src.offset(k + 1) - src.offset(k))
    at = u32(src.offset(k) + src.start)
    if at + size > len(src.data):
        raise ModelError("a skeleton block runs past the end of its model")
    data = bytearray(src.data[at:at + size])
    if compare(bytes(data), b"SEDBSKL\0", 7) & 0xFFFF:
        b.say(f"Error: GetSkeleton: Block {i16(model16)}, {k} specified is not a Skeleton.\n")
        return -1
    if swap:
        if model16 == 0:
            if b.swap_first:
                b.swap(data, size)
        elif not b.swap_first or model16 != 1:
            b.swap(data, size)

    def piece(start: int, length: int) -> bytes:
        if start < 0 or length < 0 or start + length > size:
            raise ModelError("a skeleton block is cut short")
        return bytes(data[start:start + length])

    m.write(slot, piece(0, 0x40))
    entries = m.i32(slot + ENTRY_COUNT)
    if entries < 1:
        raise ModelError("a skeleton block has no bone table")
    m.set_u64(slot + ENTRIES, m.put(piece(0x40, entries * 16)))
    start = i32((entries + 4) * 16)
    m.set_u32(slot + DATA_AT, start)
    bones = (m.u32(m.u64(slot + ENTRIES) + 8) // BONE) & 0xFFFF
    m.set_u16(slot + BONE_COUNT, bones)
    m.set_u64(slot + BONES, m.put(piece(start, bones * BONE)))
    names = i32(m.i32(slot + SIZE) - m.i32(slot + NAMES_AT) - start)
    m.set_u32(slot + NAMES_SIZE, names)
    if names < 0:
        raise ModelError("a skeleton block's names don't fit it")
    m.set_u64(slot + NAMES, m.put(piece(start + m.u32(slot + NAMES_AT), names)))
    m.set_u16(slot + MODEL, model16)
    if primary and entries > 1:
        second = m.i32(m.u64(slot + ENTRIES) + 0x14)
        m.set_u64(EXTRA, m.put(piece(u32(second + start), u32(m.i32(slot + NAMES_AT) - second))))
    return result


def free(b, which: int, every: bool) -> None:
    """FUN_00401b00: a model's slot emptied, or every slot"""
    m = b.mem
    count = i16(m.u16(state.TRB_COUNT))
    for n in range(count + 1) if count >= 0 else ():
        if which == n or every:
            slot = _slot(m, n)
            if m.i16(slot + MODEL) >= 0:
                for at in (NAMES, BONES, ENTRIES):
                    m.free(m.u64(slot + at))
                m.set_u16(slot + MODEL, 0xFFFF)


def release(b, which: int = 0, every: bool = True) -> None:
    """FUN_00405000: what main() calls once every block's done"""
    m = getattr(b, "mem", None)
    if m is not None and m.u64(SKEL_TABLE):
        free(b, which, every)


def bone(b, model: int, n: int):
    """
    FUN_00405020, for the mesh editor: bone `n` of a model's skeleton as
    (code, (pos, rot, scale)), the three as raw u32 words. Code 0 with no
    words for a negative bone (the exe leaves its outputs alone), -10 past
    the end, the load's own negative when the model has no skeleton
    """
    if n < 0:
        return 0, None
    state.mem_for(b)
    m = b.mem
    got = load(b, i16(model), False, False)
    if i16(got) < 0:
        return got, None
    slot = _slot(m, model & 0xFFFF)
    count = m.u16(slot + BONE_COUNT)
    if count < n:
        return -10, None
    if n == count:
        raise ModelError("a mesh asks for the bone one past the end of its skeleton")
    at = m.u64(slot + BONES) + n * BONE
    words = [m.u32(at + POS + k * 4) for k in range(10)]
    return 0, (tuple(words[0:3]), tuple(words[3:7]), tuple(words[7:10]))


def swap_bones(b, x: int, y: int) -> None:
    """FUN_004019e0: two of the primary's bones trade places, and every link to them follows"""
    m = b.mem
    p = m.u64(PRIMARY)
    count = m.u16(p + BONE_COUNT)
    if not (0 <= x < count and 0 <= y < count):
        raise ModelError("its recipe reorders a bone the skeleton hasn't got")
    bones = m.u64(p + BONES)
    held = m.get(bones + x * BONE, BONE)
    m.memmove(bones + x * BONE, bones + y * BONE, BONE)
    m.write(bones + y * BONE, held)
    for n in range(count):
        at = bones + n * BONE
        for link in (PARENT, CHILD, SIBLING, ORDER):
            v = m.i32(at + link)
            if v == y:
                m.set_u32(at + link, x)
            elif v == x:
                m.set_u32(at + link, y)


def add_bone(b, block: int, name: bytes) -> int:
    """
    FUN_00402000: a blank bone on the end of the primary, its name slotted in
    before the trailer the names end on, and the output table told the block
    grew. Returns the new bone's index
    """
    m = b.mem
    p = m.u64(PRIMARY)
    names = m.u64(p + NAMES)
    was = m.i32(p + NAMES_SIZE)
    # trailing NULs trimmed to one, unless it's all NULs
    if was - 1 > 0:
        at, keep = was - 1, was
        trim = True
        if m.u8(names + at - 1) == 0:
            while True:
                keep = at
                if at == 1:
                    trim = False
                    break
                at -= 1
                if m.u8(names + at - 1) != 0:
                    break
        if trim:
            m.set_u32(p + NAMES_SIZE, keep)
    count = m.u16(p + BONE_COUNT)
    bones = m.malloc((count + 1) * BONE)
    m.memmove(bones, m.u64(p + BONES), count * BONE)
    name = name.split(b"\0", 1)[0]
    length = i16(len(name))
    size = m.i32(p + NAMES_SIZE)
    grown = u32(length + size + 1)
    if grown & 3:
        grown = (grown & ~3) + 4
    pool = m.malloc(grown)
    entry = m.u64(state.OUT_TABLE) + i16(block) * 16 + 8
    m.set_u32(entry, m.u32(entry) + (grown - was) + BONE)
    trailer = b"HierarchyData\0"
    if m.u32(p + VERSION) >= 9000:
        trailer += b"KineDriver\0"
    keep = size - len(trailer)
    if keep < 0 or length < 0:
        raise ModelError("a skeleton's names are too short to add a bone to")
    m.memmove(pool, names, keep)
    m.write(pool + keep, name + b"\0" + trailer)
    m.set_u32(p + NAMES_SIZE, grown)
    m.free(m.u64(p + BONES))
    m.free(names)
    m.set_u64(p + BONES, bones)
    m.set_u64(p + NAMES, pool)
    m.set_u16(MAX_NAME, m.u16(MAX_NAME) + 1)
    m.set_u16(MAX_BONE, m.u16(MAX_BONE) + 1)

    new = bones + count * BONE
    m.memset(new, 0, BONE)
    m.set_u32(new + ORDER, count)
    m.set_u32(new + 0x08, 2)
    m.set_u32(new + 0x0C, 0xFFFFFFFF)
    m.set_u32(new + NAME, m.u16(MAX_NAME))
    m.set_u32(new + CHILD, 0xFFFFFFFF)
    m.set_u32(new + SIBLING, 0xFFFFFFFF)
    m.set_u32(new + KIND, 5)
    m.set_u32(new + PARENT, count - 1)
    old = m.u32(p + VERSION) < 9000
    m.set_u32(new + 0x48, 0x12 if old else 2)
    m.set_u32(new + FLAGS13, 2 if old else 5)

    # hang it off the last bone: its child if free, else the first free
    # sibling slot walking back up the list
    last = count - 1
    if not m.u8(NO_LINK) and last > 0:
        at = bones + last * BONE
        if m.i32(at + CHILD) == -1:
            m.set_u32(at + CHILD, count)
        else:
            sib, prev = m.i32(at + SIBLING), at
            at -= BONE
            linked = False
            while sib != -1:
                last -= 1
                if last == 0:
                    linked = True
                    break
                if m.i32(at + CHILD) == -1:
                    m.set_u32(at + CHILD, count)
                    linked = True
                    break
                sib, prev = m.i32(at + SIBLING), at
                at -= BONE
            if not linked:
                m.set_u32(prev + SIBLING, count)
    count = (count + 1) & 0xFFFF
    m.set_u16(p + BONE_COUNT, count)
    first = m.u64(p + ENTRIES)
    m.set_u32(first + 8, count * BONE)
    m.set_u32(p + NAMES_AT, m.u32(p + NAMES_AT) + BONE)
    m.set_u32(first, m.u32(first) + 1)
    m.set_u32(p + COUNTER, m.u32(p + COUNTER) + 1)
    return i16(count - 1)


def copy_bone(b, name: bytes, model: int, depth: int = 0) -> int:
    """
    FUN_00402400: a bone from a model's skeleton into the primary, its
    missing parents first, unless the primary has it already. Returns its
    index in the primary
    """
    if depth > DEEPEST:
        raise ModelError("a skeleton's parents go round in a loop")
    m = b.mem
    count = m.u16(state.TRB_COUNT)
    have = find_bone(b, name, count, 0)
    if have >= 0:
        return have
    model &= 0xFFFF
    at = find_bone(b, name, model, 0)
    slot = _slot(m, model)
    if at < 0:
        b.say(f"Error Finding Bone Name {_text(name)} in model file.\n")
        raise ModelError("its recipe copies a bone the other skeleton hasn't got")
    src = m.u64(slot + BONES) + at * BONE
    parent = m.i16(src + PARENT)
    up = m.u64(slot + NAMES)
    if m.i32(slot + NAMES_SIZE) > 0:
        if not 0 <= parent < m.u16(slot + BONE_COUNT):
            raise ModelError("its recipe copies a root bone the skeleton is missing")
        up = name_at(m, slot, m.u32(m.u64(slot + BONES) + parent * BONE))
    under = copy_bone(b, _cstr(m, slot, up), model, depth + 1)
    new = add_bone(b, m.i16(BLOCK_N), name)
    dst = m.u64(m.u64(PRIMARY) + BONES) + new * BONE
    src = m.u64(slot + BONES) + at * BONE
    m.set_u32(dst + 0x08, m.u32(src + 0x08))
    m.set_u32(dst + 0x0C, m.u32(src + 0x0C))
    m.set_u32(dst + PARENT, under)
    m.set_u32(dst + KIND, m.u32(src + KIND))
    m.memmove(dst + POS, src + POS, 40)
    return new


def fing_rename(m, slot: int) -> None:
    """FUN_00401e10: ?_fing3a/4a/5a become ...b, and ...b become ...c, one step each"""
    names = m.u64(slot + NAMES)
    for k in range(m.i32(slot + NAMES_SIZE)):
        if _byte(m, slot, k) == 0 and compare(_tail(m, slot, names + k + 3), b"fing", 4) & 0xFFFF == 0 \
                and (_byte(m, slot, k + 7) - 0x33) & 0xFF < 3:
            last = _byte(m, slot, k + 8)
            if last == 0x62:
                m.set_u8(names + k + 8, 0x63)
            elif last == 0x61:
                m.set_u8(names + k + 8, 0x62)


def _has_fing3c(m, slot: int) -> bool:
    size = m.i32(slot + NAMES_SIZE)
    if i16(size) <= 0:
        return False
    names = m.u64(slot + NAMES)
    return any(compare(_tail(m, slot, names + k), b"fing3c", 6) & 0xFFFF == 0
               for k in range(((size - 1) & 0xFFFF) + 1))


def edit(b, n: int, model: int, block, length: int) -> bool:
    """
    the editor on output block `n`, a skeleton out of model `model`. True
    when nothing changed and main() writes the block as it is; False when
    the editor wrote it itself
    """
    m = state.push(b)
    try:
        code = _editor(b, i16(n), i16(model))
    except RecursionError:
        raise ModelError("a skeleton's parents go round in a loop") from None
    # the recipe reader owns these, ::F can change them mid-edit
    m.set_u32(state.FLAGS, b.r.flags)
    m.set_u32(state.ANSWERS, b.r.answers)
    state.pull(b)
    if code == 999:
        return True
    if code == 0:
        return False
    if code == 0xB:
        raise ModelError("its recipe has a skeleton command this doesn't know")
    raise ModelError("its skeleton block won't load")


def _editor(b, n: int, model: int) -> int:
    """FUN_00402a00: the commands to EOF, ':X' just checks, then the block out if anything changed"""
    m = b.mem
    r = b.r
    m.set_u16(MODEL_N, model)
    m.set_u16(BLOCK_N, n)
    if i16(load(b, model, True, True)) < 0:
        b.say("Error: Unable to load primary skeleton.\n")
        return -1
    p = m.u64(PRIMARY)

    last = -1
    top_bone = m.u16(MAX_BONE)
    count = m.u16(p + BONE_COUNT)
    if count:
        last = count - 1
        top_name, named, ordered = m.u16(MAX_NAME), False, False
        bones = m.u64(p + BONES)
        for k in range(count):
            word = m.u32(bones + k * BONE)
            if top_name & 0xFFFF < word:
                top_name, named = word, True
            order = m.i32(bones + k * BONE + ORDER)
            if i16(top_bone) < order:
                top_bone, ordered = order, True
        if named:
            m.set_u16(MAX_NAME, top_name)
        if ordered:
            m.set_u16(MAX_BONE, top_bone)
    if m.i16(MAX_BONE) != last and m.i32(p + ENTRY_COUNT) == 1:
        b.say(f"Bone quantity mismatch {m.i16(MAX_BONE)} vs. {last}\n")
        count = m.u16(p + BONE_COUNT)
    m.set_u16(MAX_BONE, count - 1)
    if r.flags & 3:
        b.say(f"Max Bone: {m.i16(MAX_BONE)}  Max Name: {m.u16(MAX_NAME)}\n")

    pos = 0             # where the next bare bone line puts its bone
    changed = False
    listed = 1          # numbering for the :B lines it prints for missing bones
    at_name = True      # name scan state, shared with :X's required bone check

    while True:
        line = r.read_line(0xFF)
        if line is None:
            break
        if r.flags & 3:
            b.say(line + "\n")
        if line[:1] != ":":
            pos, listed, at_name = _bone_line(b, line, pos, listed, at_name)
            continue
        _, _, r.rest = line.partition(",")
        letter = line[1:2]
        if letter == "X":
            at_name = _check(b, at_name)
            continue
        handler = COMMANDS.get(letter)
        if handler is None:
            b.say(f"Invalid command in Bone file {line.partition(',')[0]}\n")
            return 0xB
        handler(b, n, letter)
        changed = True

    table = m.u64(state.OUT_TABLE)
    outs = m.u16(state.OUT_COUNT)
    entry = table + n * 16
    size = m.u32(entry + 8)
    was = m.u32(state.SKL_SIZE)
    for k in range(n + 1, outs):
        m.set_u32(table + k * 16 + 4, m.u32(table + k * 16 + 4) + size - was)
    m.set_u32(p + SIZE, size)
    for word in (0x10, 0x34):
        at = state.OUT_HEADER + word
        m.set_u32(at, m.u32(at) - was + m.u32(entry + 8))
    code = 999
    if changed:
        entries = m.i32(p + ENTRY_COUNT)
        table_at = m.u64(p + ENTRIES)
        for k in range(1, entries):
            at = table_at + k * 16
            m.set_u32(at + 4, m.u32(at - 12) + m.u32(at - 8))
        b.out.write(m.get(p, 0x40))
        b.out.write(m.get(table_at, entries * 16))
        b.out.write(m.get(m.u64(p + BONES), m.u16(p + BONE_COUNT) * BONE))
        if entries > 1:
            extra = m.u64(EXTRA)
            want = m.u32(table_at + 0x18)
            if want > m.sizes.get(extra, 0):
                raise ModelError("a skeleton's second entry is bigger than the data it came with")
            b.out.write(m.get(extra, want))
        b.out.write(m.get(m.u64(p + NAMES), m.i32(p + NAMES_SIZE)))
        code = 0
    if m.u64(SKEL_TABLE):
        free(b, 0, True)
    return code


def _bone_line(b, line: str, pos: int, listed: int, at_name: bool):
    """
    a line without a colon: the bone it names (a number, or a name when the
    recipe's version is odd) moves to `pos`, and pos moves on
    """
    m = b.mem
    p = m.u64(PRIMARY)
    flags = b.r.flags
    raw = line.encode("latin-1")
    value = None
    if b.version & 1:
        hit = None
        number = k = 0
        while k < m.i32(p + NAMES_SIZE):
            at = m.u64(p + NAMES) + k
            if m.u8(at) == 0:
                number += 1
                at_name = True
            elif at_name:
                if compare(raw + b"\0", _tail(m, p, at), 0, True) & 0xFFFF == 0:
                    hit = number
                    break
                at_name = False
            k += 1
        if hit is None:
            if flags & 3:
                b.say("-1\n")
        else:
            if flags & 3:
                b.say(f"{hit}\n")
            value = hit
    else:
        try:
            got = stoi(line)
        except ValueError:
            raise ModelError("its recipe has a skeleton line that isn't a number") from None
        if got == -2:
            return pos, listed, at_name
        if flags & 3:
            b.say(f"{got}\n")
        if got >= 0:
            value = got

    if value is None:
        pos = i16(pos + 1)
        if b.r.flags & 7:
            b.say(f":B,{line},5,{m.u16(MAX_NAME) + listed},0,-1,-1,0,0,0,0,0,0,0,0\n")
            b.say(f":E,{m.i16(MAX_BONE) + listed - 1},3,{m.i16(MAX_BONE) + listed}\n")
            listed = (listed + 1) & 0xFFFF
        else:
            b.say(f"{line} Bone not found.\n")
        return pos, listed, at_name

    count = m.u16(p + BONE_COUNT)
    bones = m.u64(p + BONES)
    found = next((k for k in range(count) if m.u32(bones + k * BONE) == u32(value)), None)
    if found is None:
        b.say(f"{line} #{value}  No matching bone.\n")
    elif found != pos:
        swap_bones(b, pos, found)
    return i16(pos + 1), listed, at_name


def _check(b, at_name: bool) -> bool:
    """
    ':X': tiny floats zeroed, a near 1 rotation snapped to identity, double
    links cut, and a lot said about what looks wrong. Hands back the name
    scan state, which the required bone check moves
    """
    m = b.mem
    p = m.u64(PRIMARY)
    verbose = b.r.flags & 3
    k = 0
    while k < m.u16(p + BONE_COUNT):
        q = m.u64(p + BONES) + k * BONE
        for off in (0x10, 0x14, 0x18, 0x1C, 0x20, 0x24):
            v = m.f32(q + off)
            if v < 0.0:
                v = -v
            if v < 1e-05:
                m.set_u32(q + off, 0)
        w = m.f32(q + 0x28)
        size = -w if w < 0.0 else w
        if size < 1e-05:
            m.set_u32(q + 0x28, 0)
        elif w > 0.99999998:
            for off in (0x1C, 0x20, 0x24):
                m.set_u32(q + off, 0)
            m.set_u32(q + 0x28, ONE)
        links = 0
        for j in range(m.u16(p + BONE_COUNT)):
            at = m.u64(p + BONES) + j * BONE
            if m.u32(at + CHILD) == k:
                if links:
                    m.set_u32(at + CHILD, 0xFFFFFFFF)
                links = (links + 1) & 0xFFFF
            if m.u32(at + SIBLING) == k:
                if links:
                    m.set_u32(at + SIBLING, 0xFFFFFFFF)
                links = (links + 1) & 0xFFFF
        own = _text(_bone_name(m, p, k))
        if k > 0 and links == 0:
            b.say(f"Bone {k} ({own}) Not Created.\n")
        parent = m.i32(q + PARENT)
        if k != 0 and parent < 0:
            b.say(f"Bone {k} ({own}) No Parent.\n")
        if k < parent:
            b.say(f"Bone {k} ({own}) Order issue.  Parent: {parent} ({_text(_bone_name(m, p, parent))})\n")
        if verbose:
            if links >= 2 and k > 0:
                b.say(f"Bone {k} ({own}) Duplicate Creation ({links}).\n")
            child, sibling = m.u32(q + CHILD), m.u32(q + SIBLING)
            if child <= k:
                b.say(f"Bone {k} ({own}) Order issue.  Child 1: {child} ({_text(_bone_name(m, p, child))})\n")
            if sibling <= k:
                b.say(f"Bone {k} ({own}) Order issue.  Child 2: {sibling} "
                      f"({_text(_bone_name(m, p, sibling))})\n")
        k += 1

    # every bone the mesh editor said it needs, by name. The walk here is its
    # own (full width counts, no 16 bit wrap) and it leaves at_name behind
    for r in range(m.u16(REQUIRED_COUNT)):
        want = m.cstr(m.u64(REQUIRED + r * 8))
        found = False
        for j in range(m.u16(p + BONE_COUNT)):
            size = m.i32(p + NAMES_SIZE)
            if size <= 0:
                at_name = True
                continue
            names = m.u64(p + NAMES)
            word = m.u32(m.u64(p + BONES) + j * BONE)
            at = names
            if word:
                seen = k = 0
                landed = False
                while True:
                    c = m.u8(names + k)
                    k += 1
                    if c == 0:
                        seen += 1
                        if k >= size:
                            at_name = True
                            break
                        if seen == word:
                            at, landed = names + k, True
                            break
                    elif k >= size:
                        at_name = False
                        break
                if not landed:
                    continue
            at_name = True
            if compare(_tail(m, p, at), want + b"\0", 0) & 0xFFFF == 0:
                found = True
                break
        if not found:
            b.say(f"Required Bone {_text(want)} not found in Skeleton.\n")
    if verbose:
        b.say("Skeleton Exit\n")
    return at_name


def _not_skeleton(b, what: str, model: int) -> None:
    b.say(f"{what}: Block {model} specified is not a Skeleton.\n")


def _order(b, n: int, letter: str) -> None:
    """
    ':o,model,_,opts': the primary's order words (+0x44) renumbered to match
    another skeleton's bone order, bones it hasn't got numbered after. With
    opts & 2 every parent link is rewritten through the new numbers
    """
    m = b.mem
    r = b.r
    model = r.int() & 0xFFFF
    find_lks(b, model)
    r.int()
    opts = r.int()
    if i16(load(b, i16(model), False, False)) < 0:
        _not_skeleton(b, "Bone Order", model)
        return
    p = m.u64(PRIMARY)
    other = _slot(m, model)
    for k in range(m.u16(p + BONE_COUNT)):
        m.set_u32(m.u64(p + BONES) + k * BONE + ORDER, 0xFFFFFFFF)
    for k in range(m.u16(other + BONE_COUNT)):
        name = _cstr(m, other, name_at(m, other, m.u32(m.u64(other + BONES) + k * BONE)))
        if m.u16(p + BONE_COUNT):
            at = find_bone(b, name, m.u16(state.TRB_COUNT), 0)
            if at >= 0:
                m.set_u32(m.u64(p + BONES) + at * BONE + ORDER, k)
    if not m.u16(p + BONE_COUNT):
        return
    spare = 0
    for k in range(m.u16(p + BONE_COUNT)):
        at = m.u64(p + BONES) + k * BONE
        if m.i32(at + ORDER) == -1:
            m.set_u32(at + ORDER, m.u16(other + BONE_COUNT) + spare)
            spare += 1
    if opts & 2:
        count = m.u16(p + BONE_COUNT)
        for k in range(count):
            bones = m.u64(p + BONES)
            parent = m.i32(bones + k * BONE + PARENT)
            if not 0 <= parent < count:
                raise ModelError("its recipe renumbers the parent of a root bone")
            m.set_u32(bones + k * BONE + PARENT, m.u32(bones + parent * BONE + ORDER))


def _params(b, n: int, letter: str) -> None:
    """
    ':P,bone,model,_,from' copies one bone's position, rotation and scale
    over from another skeleton (by number, or by its own name when `from`
    is negative); ':Q' also takes its parent and makes it a plain bone
    """
    m = b.mem
    r = b.r
    which = u32(r.int())
    model = r.int()
    if b.version < 42:
        r.int()
    else:
        find_lks(b, model)
    src_n = r.int()
    if i16(load(b, i16(model), False, False)) < 0:
        b.say("Bone Parameter Update: Block specified is not a Skeleton.\n")
        return
    p = m.u64(PRIMARY)
    if not which < m.u16(p + BONE_COUNT):
        b.say(f"Skeleton :PQ> Invalid bone number {i32(which)}. Max: {m.u16(p + BONE_COUNT)}\n")
        return
    q = m.u64(p + BONES) + which * BONE
    other = _slot(m, model & 0xFFFF)
    if src_n < 0:
        own = _cstr(m, p, name_at(m, p, m.u32(q)))
        src_n = find_bone(b, own, model, 0)
        if src_n <= 0:
            b.say(f"Bone {_text(own)} not found in 2nd skeleton.\n")
            return
    if src_n >= m.u16(other + BONE_COUNT):
        raise ModelError("its recipe copies a bone past the end of the other skeleton")
    src = m.u64(other + BONES) + src_n * BONE
    m.memmove(q + POS, src + POS, 40)
    if m.u32(p + VERSION) > 8999:
        m.memmove(q + ORDER, src + ORDER, 12)
    if letter != "P":
        m.set_u32(q + KIND, 5)
        m.set_u32(q + PARENT, m.u32(src + PARENT))
        if m.u32(q + FLAGS13) == 0:
            m.set_u32(q + FLAGS13, 1)


def _reorder(b, n: int, letter: str) -> None:
    """
    ':O,from,to,opts': every bone of skeleton `from` copied in if missing and
    the primary put in its order, then `to`'s order after that. opts & 1
    (or the primary having fing3c and opts & 2) bumps the finger names;
    opts & 2 links the added bones up as a chain instead of leaving it to
    add_bone
    """
    m = b.mem
    r = b.r
    first = r.int() & 0xFFFF
    if b.version < 42:
        r.int()
        second = r.int() & 0xFFFF
        r.int()
    else:
        find_lks(b, first)
        second = r.int() & 0xFFFF
        find_lks(b, second)
    opts = r.int()
    if i16(load(b, i16(first), True, False)) < 0:
        _not_skeleton(b, "Bone Order", first)
        return
    p = m.u64(PRIMARY)
    models = m.u16(state.TRB_COUNT)
    src = _slot(m, first)
    if opts & 1:
        fing_rename(m, src)
    elif not _has_fing3c(m, src) and _has_fing3c(m, p) and opts & 2:
        fing_rename(m, src)

    def name_of(slot, k):
        return _cstr(m, slot, name_at(m, slot, m.u32(m.u64(slot + BONES) + k * BONE)))

    copied = last = 0
    if m.u16(src + BONE_COUNT):
        k = 0
        while k < m.u16(src + BONE_COUNT):
            copy_bone(b, name_of(src, k), first)
            k += 1
        k = 0
        while k < m.u16(src + BONE_COUNT):
            name = name_of(src, k)
            at = find_bone(b, name, models, k)
            if at == -1:
                b.say(f":{_text(name)}: not found in src.\n")
            elif at == -2:
                b.say(f":{_text(name)}: not found in dest.\n")
                b.say(f"Bone Add Error {k} {at}  max {m.u16(p + BONE_COUNT)}\n")
            elif at < 0 or m.u16(p + BONE_COUNT) < at:
                b.say(f"Error FindIndexByBone: Returned {at}\n")
            elif at != k:
                swap_bones(b, k, at)
            k += 1
        copied = last = m.u16(src + BONE_COUNT)

    m.set_u8(NO_LINK, 1 if opts & 2 else 0)
    if i16(load(b, i16(second), False, False)) < 0:
        _not_skeleton(b, "Bone Order", i16(second))
        return
    for k in range(m.u16(REQUIRED_COUNT)):
        copy_bone(b, m.cstr(m.u64(REQUIRED + k * 8)), second)
    m.set_u8(NO_LINK, 0)
    dst = _slot(m, second)
    at_k = copied
    k = 0
    while k < m.u16(dst + BONE_COUNT):
        name = name_of(dst, k)
        at = find_bone(b, name, models, at_k & 0xFFFF)
        if at == -1:
            if not opts & 2:
                b.say(f":{_text(name)}: not found in src\n")
        elif at >= 0:
            if at_k != at:
                swap_bones(b, at_k, at)
            at_k += 1
        k += 1

    if not opts & 2:
        return
    count = m.u16(p + BONE_COUNT)
    if count <= copied & 0xFFFF:
        return
    bones = m.u64(p + BONES)
    if last < count - 1:
        k = last
        while True:
            k += 1
            m.set_u32(bones + (k - 1) * BONE + CHILD, k)
            count = m.u16(p + BONE_COUNT)
            if not k < count - 1:
                break
    m.set_u32(bones + (count - 1) * BONE + CHILD, 0xFFFFFFFF)
    up = last - 1
    if up == -1:
        return
    at = bones + up * BONE
    if m.i32(at + CHILD) == -1:
        m.set_u32(at + CHILD, last)
        return
    if m.i32(at + SIBLING) != -1:
        # back up the list to the first bone with a free child or sibling
        # slot, giving up before bone -1
        walk = bones + (up - 1) * BONE
        while True:
            at = walk
            if at == bones - BONE:
                return
            if m.i32(at + CHILD) == -1:
                m.set_u32(at + CHILD, last)
                return
            walk = at - BONE
            if m.i32(at + SIBLING) == -1:
                break
    m.set_u32(at + SIBLING, last)


def _straighten(b, n: int, letter: str) -> None:
    """':S': every bone the child of the one before, no siblings"""
    m = b.mem
    p = m.u64(PRIMARY)
    k = 0
    while k < m.u16(p + BONE_COUNT):
        at = m.u64(p + BONES) + k * BONE
        m.set_u32(at + CHILD, k + 1 if k < m.u16(p + BONE_COUNT) - 1 else 0xFFFFFFFF)
        m.set_u32(at + SIBLING, 0xFFFFFFFF)
        k += 1


def _zero_order(b, n: int, letter: str) -> None:
    """':Z': every order word 0"""
    m = b.mem
    p = m.u64(PRIMARY)
    for k in range(m.u16(p + BONE_COUNT)):
        m.set_u32(m.u64(p + BONES) + k * BONE + ORDER, 0)


def _matching(b, n: int, letter: str) -> None:
    """
    ':M,a,b,flags': prints the bones of `a` (its first, and those whose word
    0x4c is `flags`) that `b` has too. Only the loads change anything, and a
    skeleton it had to load is let go again
    """
    m = b.mem
    r = b.r
    first = r.int() & 0xFFFF
    second = r.int() & 0xFFFF
    want = u32(r.int())
    _check_slot(m, first)
    _check_slot(m, second)
    had_first = m.i16(_slot(m, first) + MODEL)
    had_second = m.i16(_slot(m, second) + MODEL)
    if i16(load(b, i16(first), False, False)) < 0:
        b.say(f"Matching Bones: Block {first} specified is not a Skeleton.\n")
        return
    if i16(load(b, i16(second), False, False)) < 0:
        b.say(f"Matching Bones: Block {second} specified is not a Skeleton.\n")
        return
    src = _slot(m, first)
    k = 0
    while k < m.u16(src + BONE_COUNT):
        at = m.u64(src + BONES) + k * BONE
        if k == 0 or m.u32(at + FLAGS13) == want:
            name = _cstr(m, src, name_at(m, src, m.u32(at)))
            if find_bone(b, name, second, 0) > 0:
                b.say(_text(name) + "\n")
        k += 1
    if had_first < 0 and m.u64(SKEL_TABLE):
        free(b, i16(first), False)
    if had_second < 0 and m.u64(SKEL_TABLE):
        free(b, i16(second), False)


def _face_fix(b, n: int, letter: str) -> None:
    """':F,model[,_]': the face bones' transforms copied over from another skeleton"""
    m = b.mem
    r = b.r
    model = r.int() & 0xFFFF
    if i16(load(b, i16(model), False, False)) < 0:
        b.say(f"Error: FaceFix: Invalid TRB number {model}\n")
        return
    if b.version < 42:
        r.int()
    else:
        find_lks(b, model)
    models = m.u16(state.TRB_COUNT)
    for name in FACE:
        mine = find_bone(b, name, models, 0)
        theirs = find_bone(b, name, model, 0)
        if theirs < 1:
            if b.r.flags & 3:
                b.say(f"FaceFix: bone {_text(name)} not found.  {mine} {theirs}\n")
            continue
        if mine < 0:
            raise ModelError("its recipe fixes a face bone the skeleton hasn't got")
        dst = m.u64(m.u64(PRIMARY) + BONES) + mine * BONE
        src = m.u64(_slot(m, model) + BONES) + theirs * BONE
        m.memmove(dst + POS, src + POS, 40)


def _edit_bone(b, n: int, letter: str) -> None:
    """':E,bone,param,value': one word of one bone"""
    m = b.mem
    r = b.r
    which = r.int()
    param = r.int()
    p = m.u64(PRIMARY)
    if which < 0 or m.u16(p + BONE_COUNT) <= which:
        b.say(f"Skeleton :E> Invalid bone number {which}. Max: {m.u16(p + BONE_COUNT)}\n")
        return
    q = m.u64(p + BONES) + which * BONE
    if param in E_WORDS:
        m.set_u32(q + E_WORDS[param], r.int())
    elif 6 <= param <= 8:
        m.set_f32(q + POS + (param - 6) * 4, r.float())
    elif 9 <= param <= 12:
        m.set_f32(q + ROT + (param - 9) * 4, r.float())
    elif 13 <= param <= 15:
        m.set_f32(q + SCALE + (param - 13) * 4, r.float())
    else:
        b.say(f"Skeleton :E> Unknown Parameter {param}\n")


def _new_bone(b, n: int, letter: str) -> None:
    """
    ':B,name,kind,...': a bone added and filled in. A kind of -1 to -4 is a
    shorthand that only sets its kind and hangs it under the bone list's
    second bone or itself
    """
    m = b.mem
    r = b.r
    name = r.str().encode("latin-1")
    add_bone(b, n, name)
    kind = r.int()
    p = m.u64(PRIMARY)
    count = m.u16(p + BONE_COUNT)
    q = m.u64(p + BONES) + (count - 1) * BONE
    if u32(kind + 4) < 4:
        b.say("B Check - boneType value not (i >= 0) || (i < -4)\n")
        m.set_u32(q + KIND, 1 if u32(kind) & 0xFFFFFFFD == 0xFFFFFFFD else 5)
        if kind < -2:
            m.set_u32(q + CHILD, count)
        else:
            if count < 2:
                raise ModelError("its recipe hangs a new bone off a bone the skeleton hasn't got")
            m.set_u32(m.u64(p + BONES) + BONE + SIBLING, count)
    else:
        m.set_u32(q + KIND, kind)
        for off in (NAME, PARENT, CHILD, SIBLING, FLAGS13):
            m.set_u32(q + off, r.int())
        for k in range(7):
            m.set_f32(q + POS + k * 4, r.float())
    if m.u32(q + FLAGS13) != 0 or m.u32(q + KIND) > 1:
        for k in range(3):
            m.set_u32(q + SCALE + k * 4, ONE)


def _add_missing(b, n: int, letter: str) -> None:
    """':A,model[,_]': every bone of another skeleton the primary hasn't got, added on the end"""
    m = b.mem
    r = b.r
    model = r.int() & 0xFFFF
    if i16(load(b, i16(model), False, False)) < 0:
        b.say(f"Error: Add Missing Bones: Invalid TRB number {model}\n")
        return
    if b.version < 42:
        r.int()
    else:
        find_lks(b, model)
    models = m.u16(state.TRB_COUNT)
    src = _slot(m, model)
    k = 0
    while k < m.u16(src + BONE_COUNT):
        name = _cstr(m, src, name_at(m, src, m.u32(m.u64(src + BONES) + k * BONE)))
        if find_bone(b, name, models, 0) < 0:
            add_bone(b, n, name)
            p = m.u64(PRIMARY)
            new = m.u64(p + BONES) + (m.u16(p + BONE_COUNT) - 1) * BONE
            theirs = m.u64(src + BONES) + k * BONE
            parent = m.i32(theirs + PARENT)
            m.set_u32(new + KIND, m.u32(theirs + KIND))
            up = m.u64(src + NAMES)
            if m.i32(src + NAMES_SIZE) > 0:
                if not 0 <= parent < m.u16(src + BONE_COUNT):
                    raise ModelError("its recipe adds a root bone the skeleton is missing")
                up = name_at(m, src, m.u32(m.u64(src + BONES) + parent * BONE))
            under = find_bone(b, _cstr(m, src, up), models, 0)
            m.set_u32(new + CHILD, 0xFFFFFFFF)
            m.set_u32(new + SIBLING, 0xFFFFFFFF)
            m.set_u32(new + PARENT, under)
            m.set_u32(new + FLAGS13, m.u32(theirs + FLAGS13))
            m.memmove(new + POS, theirs + POS, 40)
        k += 1


COMMANDS = {
    "o": _order, "P": _params, "Q": _params, "O": _reorder, "S": _straighten,
    "Z": _zero_order, "M": _matching, "F": _face_fix, "E": _edit_bone,
    "B": _new_bone, "A": _add_missing,
}
