"""
FFXIIIwdbMod.exe's edits to an XIII .wdb, done here instead of by the exe.

Gameplay Tweaks ships the tool, and every mod since that calls its
FFXIIIwdbLoad.bat (Less Grinding's seven, say) hands it a .txt like

    :\\a line of description
    db\\resident\\bt_auto_ability.wdb
    auto_drop_0,7,bs,10000

a table's path, then `record,column,type,value` lines against it, and more
tables after a :NEWFILE. Read off the exe itself (docs/wdbmod.md), so a table
comes out byte for byte the way the tool would have written it.

A table is a WPD laid out flat: "WPD\\0", u32 BE count, 8 bytes, 32 byte
entries (name 16, u32 BE offset, u32 BE size, 8 more), then their data back to
back. The first four entries are the table's own (!!string, !!strtypelist with
a u32 per column, two more) and every one after is a row, a u32 BE per column.
"""

import re
import struct

HEADS = 4
ENTRY = 32
# the exe getline()s into 255 chars, and anything longer stops it reading
LONGEST = 254
HEX = "0123456789ABCDEF"


class WdbModError(ValueError):
    pass


def _be(buf, at: int) -> int:
    return struct.unpack_from(">I", buf, at)[0]


def _put(buf, at: int, value: int) -> None:
    struct.pack_into(">I", buf, at, value & 0xFFFFFFFF)


def atoi(text: str) -> int:
    """
    the exe's: leading space, a sign, digits, the rest ignored. Too big wraps
    round 32 bits rather than clamping, 99999999999 coming out 1215752191,
    which is what it was run on to find out
    """
    hit = re.match(r"[ \t\n\v\f\r]*([+-]?\d+)", text)
    if not hit:
        return 0
    wrapped = int(hit.group(1)) % 2 ** 32
    return wrapped - 2 ** 32 if wrapped >= 2 ** 31 else wrapped


def atof(text: str) -> float:
    hit = re.match(r"[ \t\n\v\f\r]*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)", text)
    return float(hit.group(1)) if hit else 0.0


def float_bits(value: float) -> int:
    # cvtsd2ss then the bits as they are, so a double too big for a float is inf
    try:
        return struct.unpack(">I", struct.pack(">f", value))[0]
    except OverflowError:
        return 0x7F800000 if value > 0 else 0xFF800000


def bits(word: int, code: str, value: int) -> int:
    """
    `word` with the field `code` names set to `value`. The code walks the word
    from its top bit: b/c skip a byte, s/i two, t turns each 0 after it into
    one bit skipped, p turns each hex digit after it into that many (0 being
    16). Its last letter is the field's width read the same way, t1 or p1
    being a single bit. So "ib" is the third byte down and "p47E7" the low 7
    """
    left, tee, pee = 32, False, False
    for c in code[:-1]:
        if c == "t":
            tee = True
        elif c == "p":
            pee = True
        elif c == "0" and tee:
            left -= 1
        elif pee and c in HEX:
            left -= HEX.index(c) or 16
        elif c in "bc":
            left, tee, pee = left - 8, False, False
        elif c in "si":
            left, tee, pee = left - 16, False, False
        else:
            raise WdbModError(f"its type {code!r} isn't one FFXIIIwdbMod reads")
    last = code[-1:]
    if last and last in "bc":
        width = 8
    elif last and last in "si":
        width = 16
    elif last == "1" and tee:
        width = 1
    elif pee and last and last in HEX:
        width = HEX.index(last) or 16
    else:
        raise WdbModError(f"its type {code!r} isn't one FFXIIIwdbMod reads")
    shift = left - width
    if shift < 0:
        raise WdbModError(f"its type {code!r} runs past the end of a 32 bit column")
    mask = ((1 << width) - 1) << shift
    return ((((value << shift) ^ word) & mask) ^ word) & 0xFFFFFFFF


def _ci(name: str) -> bytes:
    return name.encode("latin-1").lower()


class Table:
    """
    one .wdb, held the way the exe holds it. Anything not laid out the way it
    reads one (it never checks) is turned away here, since the exe would
    have written garbage over it rather than failed
    """

    def __init__(self, data: bytes, name: str = "the table"):
        self.name = name
        if len(data) < 16 or data[:4] != b"WPD\x00":
            raise WdbModError(f"{name} isn't a .wdb table")
        count = _be(data, 4)
        end = 16 + ENTRY * count
        if count < HEADS or len(data) < end:
            raise WdbModError(f"{name} is cut short or isn't a table of rows")
        self.head = bytearray(data[:16])
        self.table = bytearray(data[16:end])
        offs = [_be(self.table, i * ENTRY + 16) for i in range(count)]
        sizes = [_be(self.table, i * ENTRY + 20) for i in range(count)]
        self.cols = sizes[1] >> 2
        width = self.cols * 4
        rows_at = offs[3] + 4
        laid = (offs[0] == end and offs[1] >= offs[0] and offs[2] == offs[1] + width
                and offs[3] >= offs[2]
                and all(offs[i] == rows_at + (i - HEADS) * width and sizes[i] == width
                        for i in range(HEADS, count))
                and len(data) >= rows_at + (count - HEADS) * width)
        names_ok = all(self.table[i * ENTRY:i * ENTRY + 1] == b"!" for i in range(HEADS)) and \
            all(self.table[i * ENTRY:i * ENTRY + 1] != b"!" for i in range(HEADS, count))
        if not (laid and names_ok):
            raise WdbModError(f"{name} isn't laid out as a table FFXIIIwdbMod can edit")
        self.strings = bytearray(data[offs[0]:offs[1]])
        self.types = bytes(data[offs[1]:offs[1] + width])
        self.third = bytes(data[offs[2]:offs[3]])
        self.version = bytes(data[offs[3]:rows_at])
        self.rows = bytearray(data[rows_at:rows_at + (count - HEADS) * width])

    @property
    def count(self) -> int:
        return len(self.table) // ENTRY

    def _slot(self, i: int) -> bytes:
        """an entry's name as strcmp sees it: up to its NUL, wherever that is"""
        cut = self.table.find(b"\x00", i * ENTRY)
        return bytes(self.table[i * ENTRY:cut if cut >= 0 else len(self.table)])

    def _matches(self, i: int, name: str) -> bool:
        # the exe's own compare: case blind, terminator included
        want = _ci(name) + b"\x00"
        return bytes(self.table[i * ENTRY:i * ENTRY + len(want)]).lower() == want

    def row_of(self, record: str):
        want = record.encode("latin-1")
        return next((i for i in range(HEADS, self.count) if self._slot(i) == want), None)

    def _shift(self, start: int, by: int) -> None:
        for i in range(start, self.count):
            _put(self.table, i * ENTRY + 16, _be(self.table, i * ENTRY + 16) + by)

    def edit(self, record: str, column: int, kind: str, value: str) -> None:
        row = self.row_of(record)
        if row is None:
            raise WdbModError(f"{self.name} has no {record} in it")
        if not 0 <= column < self.cols:
            raise WdbModError(f"{record} in {self.name} has {self.cols} columns, not a "
                              f"column {column}")
        at = ((row - HEADS) * self.cols + column) * 4
        if kind[:1] == "f":
            word = float_bits(atof(value))
        elif kind[:1] == "w":
            word = atoi(value)
        elif kind[:1] == "l":
            word = self.link(value.encode("latin-1"))
        else:
            word = bits(_be(self.rows, at), kind, atoi(value))
        _put(self.rows, at, word)

    def link(self, value: bytes) -> int:
        """
        where `value` sits in !!string, added to the end when it isn't there.
        Only a string's start counts as a match, and one starting with "0"
        points at the first empty one, as the exe has it
        """
        pool, size = self.strings, len(self.strings)
        zero, check = value[:1] == b"0", value[:1] != b"0"
        for i in range(size - len(value)):
            if pool[i] == 0:
                if zero:
                    return i
                check = True
            elif check:
                cut = pool.find(b"\x00", i)
                if bytes(pool[i:cut if cut >= 0 else size]) == value:
                    return i
                check = False
        # after the last string's own NUL, keeping it, then padded back to 4
        spare = 0
        while size - spare - 2 >= 0 and pool[size - spare - 2] == 0:
            spare += 1
        at = size - spare
        grown = (size + len(value) + 1 + 3 - spare) & ~3
        new = bytearray(grown)
        new[:at] = pool[:at]
        new[at:at + len(value) + 1] = value + b"\x00"
        self.strings = new
        self._shift(1, grown - size)
        _put(self.table, 20, grown)
        return at

    def rename(self, line: str) -> None:
        if line.count(",") < 3:
            raise WdbModError("a :RENAME line in it is missing a part")
        _, flags, old, new = (line.split(",") + [""])[:4]
        for i in range(self.count):
            if self._matches(i, old):
                # strncpy into the 16 byte name, NUL padded
                self.table[i * ENTRY:i * ENTRY + 16] = new.encode("latin-1")[:16].ljust(16, b"\x00")
                return
        if not atoi(flags) & 1:
            raise WdbModError(f"its :RENAME can't find {old} in {self.name}")

    def copy(self, line: str) -> None:
        """
        :COPY,source,new name,where: the source row copied in as a new record,
        before the one named, or first or last for :BEGIN and :END. The exe
        reads a :END copy's offset from past the end of its table, so writes
        a broken one: this puts it where it was always meant to go
        """
        if line.count(",") < 3:
            raise WdbModError("a :COPY line in it is missing a part")
        _, source, name, where = line.split(",")[:4]
        found = [i for i in range(self.count) if self._matches(i, source)]
        if where.lower() == ":begin":
            at = HEADS
        elif where.lower() == ":end":
            at = self.count
        else:
            hits = [i for i in range(self.count) if self._matches(i, where)]
            at = hits[-1] if hits else -1
        src = found[-1] if found else -1
        if src < HEADS or at < HEADS:
            raise WdbModError(f"its :COPY can't find {source} or {where} among "
                              f"{self.name}'s records")
        encoded = name.encode("latin-1")
        if len(encoded) > 16:
            raise WdbModError(f"its :COPY names a record {name}, longer than the 16 "
                              "characters a record's name can be")
        width = self.cols * 4
        entry = bytearray(self.table[src * ENTRY:(src + 1) * ENTRY])
        entry[:16] = encoded.ljust(16, b"\x00")
        _put(entry, 16, _be(self.table, 3 * ENTRY + 16) + 4 + (at - HEADS) * width)
        row = self.rows[(src - HEADS) * width:(src - HEADS + 1) * width]
        self.table[at * ENTRY:at * ENTRY] = entry
        self.rows[(at - HEADS) * width:(at - HEADS) * width] = row
        self._shift(at + 1, width)
        # every entry moves along by the one the table grew
        self._shift(0, ENTRY)
        _put(self.head, 4, self.count)

    def dump(self) -> bytes:
        _put(self.head, 4, self.count)
        return bytes(self.head + self.table + self.strings + self.types + self.third +
                     self.version + self.rows)


def lines_of(data: bytes) -> list:
    """a .txt's lines as the exe reads it in text mode: CRLF as one, Ctrl-Z the end"""
    text = data.split(b"\x1a", 1)[0].decode("latin-1")
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        line = line[:-1] if line.endswith("\r") else line
        if len(line) > LONGEST:
            raise WdbModError(f"line {n} of it is longer than FFXIIIwdbMod reads")
        out.append(line)
    if out and out[-1] == "":
        out.pop()
    return out


def _quiet(line: str) -> bool:
    return not line or line.startswith(":\\")


def run(data: bytes, load) -> list:
    """
    [(table path, its new bytes)] for a .txt, in the order it names them.
    `load(path)` gives a table's bytes as they are now. A table a :NEWFILE
    comes back to picks up from where the earlier part left it. Any line it
    can't do and nothing comes back: the exe writes nothing either way, and
    the .bat around it injects nothing after a failure
    """
    lines, done, n = lines_of(data), {}, 0
    while True:
        while n < len(lines) and _quiet(lines[n]):
            n += 1
        if n == len(lines):
            return list(done.values())
        path = lines[n]
        n += 1
        # Windows opens wdb_files\mod\ case blind, so a table met twice is one
        known = path.replace("/", "\\").lower()
        table = Table(done[known][1] if known in done else load(path), path.replace("/", "\\"))
        while n < len(lines):
            line = lines[n]
            n += 1
            if _quiet(line) or line.startswith(":NOUNINSTALL"):
                continue
            if line.startswith(":NEWFILE"):
                break
            if line.startswith(":COPY"):
                table.copy(line)
            elif line.startswith(":RENAME"):
                table.rename(line)
            elif len(line) < 2:
                continue
            elif line.count(",") != 3:
                raise WdbModError(f"its line {line!r} isn't record,column,type,value")
            else:
                record, column, kind, value = line.split(",")
                # an empty value is a line the exe skips
                if value:
                    table.edit(record, atoi(column), kind, value)
        done[known] = (done[known][0] if known in done else path, table.dump())


def named(data: bytes) -> list:
    """the tables a .txt edits, as the archives spell paths: db/resident/x.wdb"""
    lines, out, n = lines_of(data), [], 0
    while n < len(lines):
        while n < len(lines) and _quiet(lines[n]):
            n += 1
        if n < len(lines):
            out.append(lines[n].replace("\\", "/").lower())
        while n < len(lines) and not lines[n].startswith(":NEWFILE"):
            n += 1
        n += 1
    return out


def whole(record: dict) -> bool:
    """whether all a drawer mod put in is tables its .txts edit, so replaying them is all of it"""
    tables = {t for txt in record.get("wdb") or () for t in named(txt.encode("latin-1"))}
    return bool(tables) and all(_spot(item["path"].lower(), tables) is not None
                                for spec in record["pairs"] for item in spec["entries"])


def _spot(path: str, tables) -> str | None:
    # ff13tool -i takes a record by its path or with one folder in front of it
    for given in tables:
        if path == given or "/" in given and path == given.split("/", 1)[1]:
            return given
    return None


def _key(keys: dict, low: str) -> str | None:
    """the index's own spelling of a table, by _spot's rule"""
    if low in keys:
        return keys[low]
    rest = low.split("/", 1)[1] if "/" in low else None
    return keys.get(rest) if rest else None


def _white_imgs(install_dir) -> list:
    """[(pair, {lowered path: the index's own spelling})] for XIII's white_img, English first"""
    from patcher.mods import game_files

    out = []
    for pair in game_files.pairs(install_dir):
        if pair.kind == "img":
            keys = {k.lower(): k for k in game_files.entries(pair.index)}
            out.append((pair, keys))
    return sorted(out, key=lambda p: p[0].voice != "u")


def replayable(install_dir, record: dict) -> list:
    """its .txts, once each has been checked against the game's tables as they are now"""
    replay(install_dir, record["wdb"], dry=True)
    return record["wdb"]


def replay(install_dir, txts, dry: bool = False):
    """
    (a folder and its look()) with a mod's .txts played again over the
    tables as the game has them now, into each voice set the way
    FFXIIIwdbLoad put them in: out of the English set, into both. Its
    edits are fields, so whatever went in under it or came off since is
    kept as it is now
    """
    from pathlib import Path

    from patcher.fileops import clear_tree
    from patcher.mods import game_files, patchdata
    from patcher.mods.hd_zones import read_record

    install_dir = Path(install_dir)
    sets = _white_imgs(install_dir)
    if not sets:
        raise WdbModError("the game's tables aren't in a white_img of XIII's")
    first, keys = sets[0]
    entries = game_files.entries(first.index)
    built = {}

    def load(path: str) -> bytes:
        low = path.replace("\\", "/").lower()
        if low in built:
            return built[low]
        spot = _key(keys, low)
        if spot is None:
            raise WdbModError(f"the game hasn't got {path}")
        return read_record(first.archive, entries[spot])

    for txt in txts:
        for path, data in run(txt.encode("latin-1"), load):
            built[path.replace("\\", "/").lower()] = data
    if dry:
        return None
    work, found = patchdata._work(install_dir), game_files.Look()
    try:
        for pair, spelled in sets:
            for low, data in built.items():
                spot = _key(spelled, low)
                if spot is not None:
                    patchdata._lay(install_dir, work, found, pair, spot, {(): data})
    except BaseException:
        clear_tree(work)
        raise
    return work, found
