"""
FF XIII HD Models.exe's main(): a recipe in, one character model out.

A .trb is the game's container for a model and everything hanging off it,
little endian:

    0x00  header, 16 u32. [4] the file's size, [12] how many blocks, [13]
          where the names start; offsets count from the end of the table
    0x40  the table, 16 bytes a block: its number, offset, size, and a word
    ...   the blocks, each "SEDB" and a type: txb a texture, wrb the meshes,
          SKL the skeleton, elb, msst, and others carried as they are.
          Second to last, a 4 byte tag a block; last, a 16 byte record a
          block, a texture's name among them
    ...   the names, NUL separated, one a block

The recipe names the models to take blocks from (models\\orig\\...), the one
to write (models\\mod\\...), then a line per output block saying which model
and which block of it. Blocks are copied in that order, each through the
editor for its type, which reads its own commands off the recipe as it goes.
A texture's pixels live in the .imgb beside the .trb, and the txb editor
copies them across as it rewrites the texture's mip table.

Names are swapped on the way through (c_c005 to c_c205), a block at a time.
"""

import struct

from patcher.mods.hdmodels.cnum import i16, stoi, u32
from patcher.mods.hdmodels.recipe import Recipe

# the newest recipe version this reads: anything past it wants a newer exe
NEWEST = 52
# the second list's names are read 16 characters at a time
SWAP_LINE = 0x11
# no real model comes near this; past it the recipe's pointing at junk, and
# the exe would happily write gigabytes of zeros
LARGEST = 1 << 30


class ModelError(Exception):
    """a recipe this can't build, said in a line"""


class Out:
    """a file the exe fwrite()s and fseek()s, in memory"""

    def __init__(self):
        self.data = bytearray()
        self.at = 0

    def write(self, data) -> None:
        data = bytes(data)
        end = self.at + len(data)
        if end > LARGEST:
            raise ModelError("its recipe would write a model too big to be real")
        if end > len(self.data):
            self.data.extend(bytes(end - len(self.data)))
        self.data[self.at:end] = data
        self.at = end

    def seek(self, at: int) -> None:
        self.at = at


class Trb:
    """one of the models a recipe reads"""

    def __init__(self, data: bytes, kind: int, ident: bytes, imgb: str):
        self.data = data
        self.kind = kind
        self.ident = ident
        self.imgb = imgb
        self.header = list(struct.unpack_from("<16I", data.ljust(0x40, b"\0")))
        count = self.header[12]
        self.table = bytes(data[0x40:0x40 + count * 16].ljust(count * 16, b"\0"))
        self.start = u32((count + 4) * 16)

    @property
    def count(self) -> int:
        return self.header[12]

    def offset(self, n: int) -> int:
        return struct.unpack_from("<I", self.table, n * 16 + 4)[0]

    def size(self, n: int) -> int:
        return struct.unpack_from("<I", self.table, n * 16 + 8)[0]

    def read(self, at: int, size: int) -> bytearray:
        """fseek + fread into a zeroed buffer, short at the end of the file"""
        at &= 0xFFFFFFFF
        got = bytearray(self.data[at:at + size])
        return got + bytearray(size - len(got))

    def tag(self, n: int) -> bytearray:
        return self.read(self.start + n * 4 + self.offset(self.count - 2), 4)

    def record(self, n: int) -> bytearray:
        return self.read(self.start + n * 16 + self.offset(self.count - 1), 16)


class _Opened:
    """the caller's files, each path trimmed the way Windows opens it"""

    def __init__(self, files):
        self.inner = files

    def read(self, path: str):
        from patcher.mods.hdmodels.cfile import win32_name
        return self.inner.read(win32_name(path))

    def __getattr__(self, name):
        return getattr(self.inner, name)


class Stopped(Exception):
    """the build was asked to stop part way"""


class Build:
    """what main() keeps in globals, for the block editors to share"""

    def __init__(self, recipe: Recipe, files, log=None, cancel=None):
        self.r = recipe
        self.files = _Opened(files)
        self.log = log or (lambda text: None)
        self.cancel = cancel
        self.trbs = []
        self.replace_from, self.replace_to, self.replace_block = [], [], []
        # how many of the swaps apply to blocks of the first model
        self.replace_all = 1
        self.swap_from, self.swap_to, self.swap_first = [], [], False
        self.no_imgb = False
        self.out = Out()
        self.imgb = Out()
        self.imgb_at = 0
        self.textures_replaced = 0
        self.header = [0] * 16
        self.table = bytearray()
        self.params = []
        self.msst_trb = -1
        self.version = 0
        self.texture_dir = b"pc"
        self.skl_size = 0
        # state the editors keep between blocks
        self.state = {}

    @property
    def flags(self) -> int:
        return self.r.flags

    @property
    def verbose(self) -> bool:
        return bool(self.r.flags & 3)

    @property
    def count(self) -> int:
        return self.header[12]

    def say(self, text: str) -> None:
        self.log(text)

    def check(self) -> None:
        # a big import is minutes of Python, so Stop is heard between edits
        if self.cancel is not None and self.cancel.is_set():
            raise Stopped

    def replace(self, buf: bytearray, length: int, block: int, trb: int) -> None:
        """FUN_00409b30: every swap that applies to this block, tried at every byte"""
        live = [n for n in range(len(self.replace_from))
                if (trb != 0 or n < self.replace_all) and self.replace_block[n] in (-1, block)]
        self._scan(buf, 0, length, live)

    def replace_exact(self, buf: bytearray, length: int, block: int, base: int = 0) -> None:
        """FUN_00409cd0 with its flag: only the swaps pinned to this one block"""
        self._scan(buf, base, length, [n for n in range(len(self.replace_from))
                                       if self.replace_block[n] == block])

    def _scan(self, buf, base: int, length: int, live: list) -> None:
        # the exe tries every swap at every byte; a swap can only start on its
        # own first byte, so bytes.find jumps between the places one could
        # and every swap is still tried there in order. A ~ or NUL first byte
        # matches more than itself, so a list with one goes the long way
        needles = self.replace_from
        live = [n for n in live if needles[n]]
        if length < 0 or not live:
            return
        if any(_norm(needles[n][0]) == 0 for n in live):
            for at in range(length + 1):
                for n in live:
                    if at + len(needles[n]) < length and matches(buf, base + at, needles[n]):
                        put(buf, base + at, self.replace_to[n], len(needles[n]))
            return
        firsts = {n: bytes(needles[n][:1]) for n in live}
        ends = {n: base + length - len(needles[n]) for n in live}
        found = {}
        at = 0
        while True:
            for n in live:
                if found.get(n, -1) < base + at:
                    hit = buf.find(firsts[n], base + at, ends[n]) if base + at < ends[n] else -1
                    found[n] = hit if hit >= 0 else 1 << 62
            at = min(found.values()) - base
            if at >= 1 << 61:
                return
            for n in live:
                if at + len(needles[n]) < length and matches(buf, base + at, needles[n]):
                    put(buf, base + at, self.replace_to[n], len(needles[n]))
                    found.clear()
            at += 1

    def swap(self, buf: bytearray, length: int) -> None:
        """FUN_00409e70: the second list, a whole NUL-ended name swapped where it starts"""
        for n, needle in enumerate(self.swap_from):
            name, with_ = cstr(needle), cstr(self.swap_to[n])
            last = max(length - len(name), 0)
            if not name or _norm(name[0]) == 0:
                hits = range(last + 1)
            else:
                # a match can only start on the name's own first byte
                hits = _finds(buf, name[:1], last + 1)
            for at in hits:
                if same_string(buf, at, name):
                    buf[at:at + len(with_)] = with_

    def grew(self, block: int, size: int, was: int) -> None:
        """a block's new size in the table, and every one after it moved along"""
        delta = size - was
        for n in range(block + 1, self.count):
            add(self.table, n * 16 + 4, delta)
        struct.pack_into("<I", self.table, block * 16 + 8, u32(size))
        self.header[4] = u32(self.header[4] + delta)
        self.header[13] = u32(self.header[13] + delta)


def cstr(buf, at: int = 0) -> bytes:
    if not isinstance(buf, (bytes, bytearray)):
        buf = bytes(buf)
    end = buf.find(b"\0", at)
    return bytes(buf[at:end if end >= 0 else len(buf)])


def _finds(buf, first: bytes, stop: int):
    """every place `first` sits before `stop`, looked up as the scan gets there
    so a swap made on the way is seen"""
    at = buf.find(first, 0, stop)
    while at >= 0:
        yield at
        at = buf.find(first, at + 1, stop)


def _norm(c: int) -> int:
    return 0 if c == 0x7E else c


def matches(buf, at: int, needle: bytes) -> bool:
    """FUN_00409990 over len(needle): ~ on either side reads as a NUL, and a NUL ends it"""
    for k, want in enumerate(needle):
        have = buf[at + k] if at + k < len(buf) else 0
        if _norm(have) != _norm(want):
            return False
        if have == 0:
            return True
    return True


def same_string(buf, at: int, needle: bytes) -> bool:
    """FUN_00409990 with no length: the C string at `at` is `needle`, NUL and all"""
    tail = cstr(buf, at)
    return matches(tail + b"\0", 0, needle + b"\0")


def put(buf, at: int, with_: bytes, length: int) -> None:
    for k in range(length):
        c = with_[k] if k < len(with_) else 0
        if at + k < len(buf):
            buf[at + k] = 0 if c == 0x7E else c


def add(buf, at: int, delta: int) -> None:
    struct.pack_into("<I", buf, at, u32(struct.unpack_from("<I", buf, at)[0] + delta))


BANNER = "HD Models+  v4.1.7"
VERSION_QUERY = "\\V"


def command_line(argv: list, files, ask=None, log=None, cancel=None):
    """
    the exe as a .bat runs it, argv after its own name: (exit code, the
    models it wrote). -s quiets it, -o N answers its questions up front,
    and a first argument of \\V asks which recipe version it reads (52)
    """
    from patcher.mods.wdbmod import atoi

    say = log or (lambda text: None)
    silent, answers, recipe = False, 0, None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "-s":
            silent = True
            i += 1
            continue
        if arg != "-o":
            if argv[0] == VERSION_QUERY:
                return NEWEST, {}
            if not arg.startswith("-"):
                recipe = arg
            i += 1
            continue
        answers = atoi(argv[i + 1]) if i + 1 < len(argv) else 0
        i += 2
    if not silent:
        say(BANNER + "\n")
    if recipe is None:
        say("Error: No model conversion file specified on command line.  This application\n"
            "is not meant to be run directly but through an associated .bat file.\n")
        return 4, {}
    raw = _Opened(files).read(recipe)
    if raw is None:
        say(f"Error: Unable to locate/open model conversion file {recipe}\n")
        return 3, {}
    return 0, run(raw, files, ask=ask, answers=answers, log=log, cancel=cancel)


def questions(recipe_raw: bytes, ask, answers: int = 0) -> int:
    """
    every ::A a recipe would ask, asked in order without building anything:
    the reader handles the directives wherever they sit, so reading it to
    the end is enough. The answers they leave
    """
    r = Recipe(recipe_raw, ask=ask)
    r.answers = answers
    while r.read_line(0xFF) is not None:
        pass
    return r.answers


def run(recipe_raw: bytes, files, ask=None, answers: int = 0, log=None, cancel=None) -> dict:
    """
    the exe on one recipe. `files.read(path)` gets a file the way the exe
    names it, relative to its folder (models\\orig\\c005.win32.trb,
    data\\FFXIII_Ultimate_Models.bin), None when it isn't there. The models
    it writes come back as path -> bytes; nothing is written here
    """
    r = Recipe(recipe_raw, ask=ask, say=log)
    r.answers = answers
    return _main(Build(r, files, log, cancel))


def _number(b: Build, size: int = 0xFF) -> int:
    line = b.r.read_line(size)
    try:
        return stoi(line or "")
    except ValueError:
        raise ModelError("its recipe's header doesn't read") from None


def _main(b: Build) -> dict:
    from patcher.mods.hdmodels import skeleton

    r = b.r
    b.version = i16(_number(b))
    if b.version > NEWEST:
        raise ModelError("its recipe wants a newer FF XIII HD Models.exe")
    r.flags = _number(b)
    if r.flags & 0x20:
        b.texture_dir = (r.read_line(0x40) or "").encode("latin-1")
    swaps = 1
    if b.version >= 11:
        swaps = _number(b) & 0xFFFF
        if b.version > 14:
            b.replace_all = i16(_number(b))
    for _ in range(swaps):
        line = (r.read_line(0xFF) or "").encode("latin-1")
        pinned = -1
        if line[:1] == b":":
            try:
                pinned = i16(stoi(line[1:].decode("latin-1")))
            except ValueError:
                raise ModelError("its recipe's name swaps don't read") from None
            line = (r.read_line(0x28) or "").encode("latin-1")
        to = (r.read_line(0x28) or "").encode("latin-1")
        needle = cstr(line[:0x28])
        if not needle or not to or len(cstr(to)) > 0x28:
            raise ModelError("its recipe's name swaps don't read")
        b.replace_from.append(needle)
        b.replace_to.append(to[:0x28].ljust(0x28, b"\0"))
        b.replace_block.append(pinned)
    count = i16(_number(b))
    if count < 0:
        count, b.swap_first = -count, True
    for _ in range(count):
        b.swap_from.append((r.read_line(SWAP_LINE) or "").encode("latin-1")[:SWAP_LINE])
        b.swap_to.append((r.read_line(SWAP_LINE) or "").encode("latin-1")[:SWAP_LINE])
    models = i16(_number(b))
    if models < 1:
        raise ModelError("its recipe names no model to build from")
    for n in range(models):
        line = r.read_line(100) or ""
        lead = line[:1]
        kind = 1
        if lead in ("1", "2", "3"):
            kind, line = int(lead), line[1:]
        elif lead == "9":
            line, b.msst_trb = line[1:], n
        elif lead == "7":
            kind, line = 7, line[1:]
        base = "models\\orig\\" + line
        data = b.files.read(base + ".trb")
        if data is None:
            raise ModelError(f"the game's {leaf(line)}.trb isn't there to build from")
        b.trbs.append(Trb(data, kind, stem(line), base + ".imgb"))

    out_trb = "models\\mod\\" + (r.read_line(100) or "")
    out_imgb = r.read_line(100) or ""
    b.no_imgb = out_imgb.lower() == "no.imgb"
    if b.no_imgb and b.verbose:
        b.say("No IMGB output file will be generated.\n")
    if b.verbose:
        b.say("Loading Model\n")

    first = b.trbs[0]
    asked = _number(b, 100)
    total = first.count & 0xFFFF if asked < 0 else asked & 0xFFFF
    r.out_trb, r.out_block = [0] * total, [0] * total
    b.params = [0] * total
    b.table = bytearray(total * 16)
    running = 0
    for n in range(total):
        if asked < 0:
            r.out_block[n] = n
        else:
            fields = (r.read_line(0xFF) or "").split(",")
            try:
                r.out_trb[n] = stoi(fields[0]) & 0xFFFF
                r.out_block[n] = stoi(fields[1] if len(fields) > 1 else "") & 0xFFFF
                if len(fields) > 2 and fields[2] != "":
                    b.params[n] = stoi(fields[2])
            except ValueError:
                raise ModelError("its recipe's block list doesn't read") from None
            if r.out_trb[n] >= models:
                raise ModelError("its recipe takes a block from a model it doesn't name")
        src = b.trbs[r.out_trb[n]]
        k = r.out_block[n]
        b.table[n * 16:n * 16 + 16] = src.table[k * 16:k * 16 + 16]
        if k < src.count - 1:
            length = u32(src.offset(k + 1) - src.offset(k))
            size = struct.unpack_from("<I", b.table, n * 16 + 8)[0]
            length = min(length, u32(size + (-size & 0xF)))
        elif k == src.count - 1:
            length = u32(src.header[13] - src.offset(k))
        else:
            raise ModelError(f"its recipe asks for block {k} of a model that hasn't got one")
        struct.pack_into("<II", b.table, n * 16, n, running)
        running = u32(running + length)

    m = total
    struct.pack_into("<I", b.table, (m - 2) * 16 + 8, u32(m * 0x14 + 0x40))
    struct.pack_into("<I", b.table, (m - 1) * 16 + 8, u32((m + 2) * 0x20))
    tags_at = struct.unpack_from("<I", b.table, (m - 2) * 16 + 4)[0]
    struct.pack_into("<I", b.table, (m - 1) * 16 + 4, u32(tags_at + i16(m + 1) * 4))
    b.header = list(first.header)
    b.header[12] = m
    b.header[13] = u32(struct.unpack_from("<I", b.table, (m - 1) * 16 + 4)[0] + m * 16)
    b.header[4] = u32(b.header[13] + 0x40 + m * 16)
    b.header[14] = m
    b.out.write(struct.pack("<16I", *b.header))
    b.out.write(b.table)
    if b.verbose:
        b.say("Header and Trb Block data written.\n")

    for n in range(m - 2) if m > 2 else ():
        if b.verbose:
            b.say(f"Block {n}\n")
        b.check()
        _block(b, n)

    skeleton.release(b, 0, True)
    r.read_line(0xFF)

    # the tags and records the last two blocks hold, out of each block's own
    # model, read into the line buffer where the exe reads them
    scratch = bytearray(0x9C4)
    for n in range(m):
        t, k = r.out_trb[n], r.out_block[n]
        tag = b.trbs[t].tag(k)
        b.replace_exact(tag, 4, n)
        _place(scratch, n * 4, tag)
        record = b.trbs[t].record(k)
        b.replace_exact(record, 16, n)
        _place(scratch, 0x1F4 + n * 16, record)
    b.out.write(bytes(scratch[:(m + 1) * 4]).ljust((m + 1) * 4, b"\0"))
    records = scratch[0x1F4:0x1F4 + 2000]
    b.replace(records, 2000, i16(m), 1)
    b.out.write(bytes(records[:m * 16]).ljust(m * 16, b"\0"))

    pool = _names(b, m)
    b.replace(pool, len(pool), -2, 0)
    b.out.write(pool)
    b.header[4] = u32(b.header[4] + len(pool))
    b.out.seek(0)
    b.out.write(struct.pack("<16I", *b.header))
    b.out.write(b.table[:m * 16])
    if b.textures_replaced and not b.r.flags & 0:
        b.say(f"{b.textures_replaced} textures replaced.\n")
    outs = {out_trb: bytes(b.out.data)}
    if not b.no_imgb:
        outs["models\\mod\\" + out_imgb] = bytes(b.imgb.data)
    # what edits wrote besides the model: :c's comparison, a component
    # catalog :S wrote back into, the way the exe leaves them on disk
    for path, f in getattr(b, "written", {}).items():
        if f.dirty and path not in outs:
            outs[path] = bytes(f.data)
    return outs


def _place(buf: bytearray, at: int, data) -> None:
    if at < len(buf):
        buf[at:at + len(data)] = data[:len(buf) - at]


def _names(b: Build, m: int) -> bytearray:
    """each output block's name out of its own model's names, renumbered and swapped"""
    pool = bytearray()
    counter = 0
    for n in range(m):
        t, want = b.r.out_trb[n], b.r.out_block[n]
        src = b.trbs[t]
        start = u32(src.header[13] + src.start)
        if start == src.header[4]:
            continue
        names = src.read(start, u32(src.header[4] - start))
        size = len(names)
        nul = 0
        at = 0
        while at < size:
            c = names[at]
            if nul == want:
                end = at
                if c == 0x46:
                    if at + 3 < size and names[at + 3] == 0x5C:
                        # F00\ and on: each gets the next number, the high
                        # digit never a letter, the low one in hex
                        names[at + 1] = (0x30 + (counter >> 4)) & 0xFF
                        low = counter & 0xF
                        names[at + 2] = 0x30 + low if low <= 9 else 0x37 + low
                        counter = (counter + 1) & 0xFFFF
                    end = _win32(names, at)
                elif c != 0:
                    end = _win32(names, at)
                b.replace_exact(names, end - at, n, at)
                if at < size and nul == want:
                    k = at
                    while True:
                        c = names[k]
                        pool.append(c)
                        nul += c == 0
                        if size <= k + 1:
                            break
                        k += 1
                        if nul != want:
                            break
            else:
                nul += c == 0
            if want < nul:
                break
            at += 1
    return pool


def _win32(names: bytearray, at: int) -> int:
    """a .ps3 in a name made .win32, and where the part to swap names in ends"""
    j = at
    while True:
        dot = names[j] == 0x2E
        nxt = names[j + 1] if j + 1 < len(names) else 0
        if dot and nxt == 0x70 and j + 3 < len(names) and names[j + 2] == 0x73 \
                and names[j + 3] == 0x33:
            for k, c in enumerate(b"win32\0"):
                if j + 1 + k < len(names):
                    names[j + 1 + k] = c
            return j
        if nxt == 0:
            return j + 1
        j += 1


def _block(b: Build, n: int) -> None:
    from patcher.mods.hdmodels import elb, msst, skeleton, texture, wrb

    t, k = b.r.out_trb[n], b.r.out_block[n]
    src = b.trbs[t]
    length = u32(src.offset(k + 1) - src.offset(k))
    size = src.size(k)
    length = min(length, u32(size + (-size & 0xF)))
    block = src.read(src.start + src.offset(k), length)
    b.replace(block, length, n, t)
    if t == 0:
        if b.swap_first:
            b.swap(block, length)
    elif not b.swap_first or t != 1:
        b.swap(block, length)
    was = length
    if src.kind == 7:
        raise ModelError("it builds from a PS3 model, which this can't")
    kind = bytes(block[:8])
    if kind[:7] == b"SEDBtxb" and not b.no_imgb:
        if b.verbose:
            b.say("Editing TXB Block\n")
        block, length = texture.txb(b, t, k, block, length, b.params[n])
        if length != was:
            if b.verbose:
                b.say(f"Txb Length Change: {was}{length}\n")
            b.grew(n, length, was)
    if kind[:7] == b"SEDBwrb":
        if b.verbose:
            b.say("Editing WRB Block\n")
        if src.kind in (2, 3):
            raise ModelError("it builds from a PS3 model, which this can't")
        block, length = wrb.edit(b, n, block, length)
        b.replace(block, length, n, t)
        b.out.write(block[:length])
    elif kind[:7] == b"SEDBSKL":
        b.skl_size = size
        if b.verbose:
            b.say("Editing Skeleton\n")
        if src.kind in (2, 3):
            raise ModelError("it builds from a PS3 model, which this can't")
        if skeleton.edit(b, n, t, block, length):
            b.out.write(block[:length])
    elif kind[:7] == b"SEDBelb":
        block, length = elb.edit(b, n, block, length, b.params[n])
        b.out.write(block[:length])
        if length != was:
            b.grew(n, length, was)
    elif kind[:8] == b"SEDBmsst":
        block, length = msst.edit(b, n, block, length, b.params[n])
        b.out.write(block[:length])
        b.grew(n, length, was)
    else:
        b.out.write(block[:length])


def stem(path: str) -> bytes:
    """FUN_00401910: up to 4 characters of the file name, extension off"""
    return leaf(path).split(".", 1)[0][:4].encode("latin-1")


def leaf(path: str) -> str:
    return path.rsplit("\\", 1)[-1]
