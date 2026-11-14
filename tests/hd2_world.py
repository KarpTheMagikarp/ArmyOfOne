"""
A small XIII-2 and a small FF XIII-2 HD, for the test_hd2_* files.

The game is real wherever anything reads it: sealed filelists in the game's own
container, entry records pointing at their text, every payload at its sector,
and each zone's sealed index stored inside white_img. The three tools are stubs
that copy the real ones where it matters, measured under wine against
ff13tool v1.4: an encrypted index is refused, -c and -i leave the checksum at
zero, and -i appends to the end of the archive and never writes over anything.
"""

import hashlib
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA2 = REPO / "tests" / "data" / "hd2"
SECTOR = 2048

# the three HD2Setup.bat measures carry its vanilla sizes, so a fixture game
# reads as vanilla to the same check a real one goes through
MODELS = {
    "chr/pc/c108/bin/c108.win32.trb": 677433,
    "chr/pc/c108/bin/c108.win32.imgb": 5000,
    "chr/pc/c108/bin/c108_def.win32.mpk": 700,
    "chr/pc/c159/bin/c159.win32.trb": 848878,
    "chr/pc/c159/bin/c159.win32.imgb": 4000,
    "chr/npc/n012/bin/n012.win32.trb": 205897,
    "chr/npc/n012/bin/n012.win32.imgb": 3000,
    "chr/npc/n910/bin/n910.win32.trb": 3000,
    "chr/npc/n910/bin/n910.win32.imgb": 3000,
    "chr/mon/m001/bin/m001.win32.trb": 2000,
    "chr/mon/m001/bin/m001.win32.imgb": 2000,
    "chr/mon/m002/bin/m002.win32.trb": 2000,
    "chr/mon/m002/bin/m002.win32.imgb": 2000,
    "chr/weapon/w101/bin/w101.win32.trb": 1500,
    "chr/weapon/w101/bin/w101.win32.imgb": 1500,
    "chr/fa/f001/bin/f001.win32.trb": 1200,
    "chr/fa/f001/bin/f001.win32.imgb": 1200,
    # a placeholder the build left out: in the index, holding nothing
    "chr/fa/f002/bin/f002.win32.trb": 0,
    "chr/fa/f002/bin/f002.win32.imgb": 0,
    "chr/summon/s001/bin/s001.win32.trb": 900,
    "chr/summon/s001/bin/s001.win32.imgb": 900,
    "vfx/field/rain.win32.imgb": 800,
    "sys/tail.bin": 300,
}

# the real filelistu lists 274 paths more than once, each copy at its own
# sectors, and anything keyed by path alone reads the first copy as moved
REPEATED = "vfx/event/ev001/bin/ev001.win32.imgb"

ZONE_FILES = [
    "bg/loc001/a001/bin/block001.win32.trb",
    "bg/loc001/a001/bin/block001.win32.imgb",
    "bg/loc001/a002/bin/block002.win32.trb",
    "bg/loc001/a002/bin/block002.win32.imgb",
    # MODZONE walks every .imgb under bg, whatever the depth or the name
    "bg/deep/x/y/bin/rock.win32.trb",
    "bg/deep/x/y/bin/rock.win32.imgb",
    # an .imgb with no .trb is not a block
    "bg/loc001/a003/bin/lonely.win32.imgb",
    "etc/notes.txt",
]


def header() -> bytes:
    from patcher.archive.filelist import load
    return load(REPO / "tests" / "data" / "filelistu_sample.bin").header


def index_bytes(entries, sealed=True, zero_checksum=False) -> bytes:
    """a filelist in the game's container, records and all"""
    from patcher.archive import crypt
    from patcher.archive.entry import format_entry
    from patcher.archive.filelist import Filelist, rebuild

    chunk, records = bytearray(), bytearray()
    for i, entry in enumerate(entries):
        records += struct.pack("<I", 0x1000000 + i * 0x100)
        records += len(chunk).to_bytes(3, "little") + bytes([16])
        chunk += format_entry(entry).encode("utf-8") + b"\x00"
    chunk += b"end\x00"
    book = Filelist(header=header(), entry_records=bytes(records), chunks=[bytes(chunk)],
                    original_blobs=[None], edited={0}, entry_count=len(entries),
                    was_encrypted=False, remainder=b"\x00" * 8)
    plain = bytearray(rebuild(book, encrypt=False))
    if zero_checksum:
        size = crypt.body_size(bytes(plain))
        struct.pack_into("<I", plain, 32 + size - 4, 0)
    return crypt.encrypt(bytes(plain)) if sealed else bytes(plain)


def pack_rows(rows):
    """(archive bytes, entries) with each payload at its sector, deflated when it helps"""
    from patcher.archive.entry import ChunkEntry

    body, entries = bytearray(), []
    for path, payload in rows:
        stored = zlib.compress(payload, 6) if payload else b""
        if len(stored) >= len(payload):
            stored = payload
        entries.append(ChunkEntry(path=path, sector_offset=len(body) // SECTOR,
                                  uncompressed=len(payload), stored=len(stored)))
        body += stored
        body += b"\x00" * (-len(body) % SECTOR)
    return bytes(body), entries


def write_pair(blob: Path, index: Path, rows, sealed=True) -> list:
    data, entries = pack_rows(rows)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(data)
    index.write_bytes(index_bytes(entries, sealed=sealed))
    return entries


def filler(path: str, size: int) -> bytes:
    seed = path.encode()
    return (seed * (size // max(len(seed), 1) + 1))[:size]


def make_game(tmp_path: Path, variant: str = "u", zones=("z0010",), laa: bool = True,
              name: str = "FINAL FANTASY XIII-2", extra=()) -> Path:
    """
    alba_data with a sealed main pair, a zone blob per zone and its sealed
    index inside white_img, and an exe with or without the 4GB flag. `extra`
    is more (path, bytes) for white_img. Called again with the other
    variant, it adds that voice set beside the first.
    """
    from tests.helpers import make_fake_pe

    game = tmp_path / name
    sys_dir, zone_dir = game / "alba_data" / "sys", game / "alba_data" / "zone"
    bin_dir = game / "alba_data" / "prog" / "win" / "bin"
    for folder in (sys_dir, zone_dir, bin_dir):
        folder.mkdir(parents=True, exist_ok=True)
    (bin_dir / "ffxiii2img.exe").write_bytes(
        make_fake_pe(characteristics=0x0022 if laa else 0x0002, machine=0x14c))

    rows = [(path, filler(path, size)) for path, size in MODELS.items()] + list(extra)
    rows.insert(1, (REPEATED, filler(REPEATED, 700)))
    for zone in zones:
        zone_rows = [(p, filler(zone + p, 900)) for p in ZONE_FILES]
        blob_bytes, zone_entries = pack_rows(zone_rows)
        (zone_dir / f"white_{zone}{variant}_img.win32.bin").write_bytes(blob_bytes)
        rows.append((f"zone/filelist_{zone}{variant}.win32.bin",
                     index_bytes(zone_entries, sealed=True)))
    rows.append((REPEATED, filler(REPEATED, 700)))
    write_pair(sys_dir / f"white_img{variant}.win32.bin", sys_dir / f"filelist{variant}.win32.bin",
               rows)
    return game


def vanilla_zone_indexes(game: Path, variant: str = "u") -> dict:
    """the fixture's own zone index sizes, which stand in for VANILLA2_ZONE_INDEXES"""
    from patcher.archive.filelist import load

    entries = load(game / "alba_data" / "sys" / f"filelist{variant}.win32.bin").entries()
    return {e.path.split("_")[1].split(".")[0][:-1]: e.uncompressed for e in entries
            if e.path.startswith("zone/filelist_")}


def pin_vanilla(monkeypatch, game: Path) -> None:
    """the fixture's zone blobs and indexes stand in for both vanilla tables"""
    from patcher.mods import hd_preflight

    zone_dir = game / "alba_data" / "zone"
    monkeypatch.setattr(hd_preflight, "VANILLA2_ZONES",
                        {p.name: p.stat().st_size for p in zone_dir.glob("white_z*_img.win32.bin")})
    monkeypatch.setattr(hd_preflight, "VANILLA2_ZONE_INDEXES", {"u": vanilla_zone_indexes(game)})


# ff13tool v1.4 as measured: game flag required, a decrypted index or nothing,
# -x into a folder named after the archive beside it, -c and -i rewriting the
# index decrypted with a zero checksum, -i appending at the next sector
FF13TOOL = "REPO = " + repr(str(REPO)) + r"""
import pathlib, sys, zlib
sys.path.insert(0, REPO)
from patcher.archive import crypt
from patcher.archive.entry import ChunkEntry
from patcher.archive.filelist import parse
from tests.hd2_world import index_bytes, pack_rows

def here(text):
    return pathlib.Path(str(text).replace(chr(92), '/'))

if '-ff132' not in sys.argv:
    print('Game automatically set to FF13.', flush=True)
    print('Error: this stub only speaks XIII-2', file=sys.stderr, flush=True)
    sys.exit(1)
print('Game set to FF13-2.', flush=True)
args = [a for a in sys.argv[1:] if a != '-ff132']
mode = args[0]

def read_index(path):
    raw = path.read_bytes()
    if crypt.is_encrypted(raw):
        print('Error: Unable to handle encrypted/decrypted file.', flush=True)
        sys.exit(1)
    return list(parse(raw).entries())

if mode == '-x':
    index, blob = here(args[1]), here(args[2])
    entries = read_index(index)
    out = blob.parent / blob.name[:-len('.win32.bin')]
    data = blob.read_bytes()
    repeats = len(entries) - len({e.path.lower() for e in entries})
    counted = str(len(entries)) + ' files'
    if repeats:
        counted += ' (' + str(repeats) + ' duplicates)'
    print('File list processed. ' + counted + ' listed in 1 file chunks.', flush=True)
    for e in entries:
        target = out / e.path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = data[e.byte_offset:e.byte_offset + e.stored]
        if e.is_compressed and payload:
            payload = zlib.decompress(payload)
        target.write_bytes(payload)
        print('Extracted ' + target.as_posix() + ' (uncompressed)', flush=True)
    print('Extracted ' + str(len(entries)) + ' files in total.', flush=True)

elif mode == '-c':
    index, tree = here(args[1]), here(args[2])
    entries = read_index(index)
    rows = [(e.path, (tree / e.path).read_bytes() if (tree / e.path).is_file() else b'')
            for e in entries]
    data, packed = pack_rows(rows)
    archive = tree.parent / (tree.name + '.win32.bin')
    archive.write_bytes(data)
    index.write_bytes(index_bytes(packed, sealed=False, zero_checksum=True))
    print('Added ' + str(len(rows)) + ' files in total.', flush=True)
    print('Wrote ' + archive.as_posix(), flush=True)
    print('Wrote ' + index.as_posix(), flush=True)

elif mode == '-i':
    index, blob, wanted = here(args[1]), here(args[2]), here(args[3])
    entries = read_index(index)
    key = wanted.as_posix().lower()
    matches = [e for e in entries if e.path.lower() == key]
    if not matches or not wanted.is_file():
        print('Error: Could not open ' + wanted.as_posix() + ' for reading.', flush=True)
        sys.exit(1)
    payload = wanted.read_bytes()
    # every copy of a path listed twice is written again, each to its own sectors
    moved = {}
    for match in matches:
        at = -(-blob.stat().st_size // 2048) * 2048
        with blob.open('r+b') as handle:
            handle.seek(at)
            handle.write(payload)
        moved[id(match)] = ChunkEntry(path=match.path, sector_offset=at // 2048,
                                      uncompressed=len(payload), stored=len(payload))
    index.write_bytes(index_bytes([moved.get(id(e), e) for e in entries], sealed=False,
                                  zero_checksum=True))
    name = wanted.as_posix().split('/', 1)[-1]
    if len(matches) > 1:
        print('Added ' + str(len(matches)) + ' instances of ' + name + ' (copied)', flush=True)
    else:
        print('Added ' + name + ' (copied)', flush=True)
    print('Updated ' + sys.argv[-2], flush=True)
    print('Imported one file to ' + sys.argv[-2], flush=True)
    print('Wrote ' + args[1], flush=True)
sys.exit(1)
"""

HD = r"""
import hashlib, pathlib, sys

def here(text):
    return pathlib.Path(str(text).replace(chr(92), '/'))

# bigger and incompressible, the way HD art is, so an installed file never
# repacks down to the size it went in at
def bulk(seed, want=6000):
    out, block = bytearray(), hashlib.sha256(seed).digest()
    while len(out) < want:
        out += block
        block = hashlib.sha256(block).digest()
    return bytes(out[:want])
"""

# FFXIII2_HDProject: [/s] name orig\ mod\ texdir\ [/s]. Returns how many
# textures it swapped, 0 when the mod ships none for this pair
INJECT = HD + r"""
args = [a for a in sys.argv[1:] if a.lower() != '/s']
stem, orig, mod, texdir = args[:4]
src, dst = here(orig), here(mod)
halves = [src / (stem + '.trb'), src / (stem + '.imgb')]
if not all(p.is_file() for p in halves):
    print('Unable to open base file ' + orig + stem + '.  Exiting.', flush=True)
    raise SystemExit(4294967293)
if not dst.is_dir():
    print('Unable to open output files ' + mod + stem + '.  Exiting.', flush=True)
    raise SystemExit(4294967292)
art = pathlib.Path('textures') / here(texdir)
member = stem.split('.')[0].lower()
hits = [p for p in art.iterdir() if p.name.lower().startswith(member)] if art.is_dir() else []
if not hits:
    sys.exit(0)
for half in halves:
    (dst / half.name).write_bytes(half.read_bytes() + b'HD' + bulk(half.name.encode()))
sys.exit(len(hits))
"""

# FF XIII HD Models: -s data\<id>.txt, reading models\orig and writing models\mod
MODELS_TOOL = HD + r"""
recipe = here(sys.argv[-1])
if not recipe.is_file():
    print('Unable to locate/open model conversion file ' + sys.argv[-1], flush=True)
    sys.exit(3)
lines = [l.strip() for l in recipe.read_text(encoding='latin-1').splitlines()]
at = next(i for i, l in enumerate(lines) if l.lower().endswith('.win32.trb'))
for rel in lines[at:at + 2]:
    source = pathlib.Path('models/orig') / here(rel)
    if not source.is_file():
        print('Unable to open TRB model file models\\orig\\' + rel, flush=True)
        sys.exit(2)
    target = pathlib.Path('models/mod') / here(rel)
    if not target.parent.is_dir():
        print('Unable to open Output model file', flush=True)
        sys.exit(2)
    target.write_bytes(source.read_bytes() + b'HDMODEL' + bulk(rel.encode()))
print('Header and Trb Block data written.', flush=True)
sys.exit(0)
"""

STUBS = {"pack": FF13TOOL, "inject2": INJECT, "models2": MODELS_TOOL}


def use_stub_names(monkeypatch):
    """the real names end in .exe; here they are plain executables"""
    from patcher.mods import hd_runner

    if sys.platform != "win32":
        monkeypatch.setitem(hd_runner.TOOLS, "pack", "ff13tool")
        monkeypatch.setitem(hd_runner.TOOLS, "inject2", "FFXIII2_HDProject")
        monkeypatch.setitem(hd_runner.TOOLS, "models2", "FF XIII HD Models")
        monkeypatch.setitem(hd_runner.TOOLS, "models2fixed", "FF XIII HD Models.fixed")


def make_mod(tmp_path: Path, monkeypatch, tools=None, name: str = "FF XIII-2 HD") -> Path:
    """
    the mod as its zip unpacks: lower case folders, the real lists and a few
    real recipes, a texture per id the fixture game has art for
    """
    from patcher.mods import hd_runner
    from tests.test_hd_runner import stub

    root = tmp_path / name
    data, support, textures = root / "data", root / "supportfiles", root / "textures"
    for folder in (data, support, textures):
        folder.mkdir(parents=True, exist_ok=True)
    (root / "FFXIII-2 HD.bat").write_text("@echo off\r\n", encoding="ascii")
    for source in sorted(DATA2.glob("*.txt")):
        (data / source.name).write_bytes(source.read_bytes())
    (data / "FFXIII_Ultimate_Models.bin").write_bytes(struct.pack("<I", 0))

    art = {"pc": ["c108C_01.bin", "c159C_01.bin"], "npc": ["n012C_01_n012.bin"],
           "mon": ["m001C_01.bin"], "weapon": ["w101C_01.bin"], "fa": ["f001C_01.bin"],
           "z0010": ["block001_hmaa.bin", "rock_es01.bin"]}
    for folder, names in art.items():
        (textures / folder).mkdir(parents=True, exist_ok=True)
        for texture in names:
            (textures / folder / texture).write_bytes(hashlib.sha256(texture.encode()).digest() * 64)

    use_stub_names(monkeypatch)
    bodies = tools or {}
    for key, body in STUBS.items():
        stub(support, hd_runner.TOOLS[key], bodies.get(key, body))
    # what detection looks for in a real download, beside the stubs
    for exe in ("ff13tool.exe", "FFXIII2_HDProject.exe", "FF XIII HD Models.exe"):
        if not (support / exe).exists():
            (support / exe).write_bytes(b"MZ")
    return root


def categories() -> list:
    import json
    manifest = json.loads((REPO / "manifests" / "ffxiii2.json").read_text(encoding="utf-8"))
    return next(p for p in manifest["patches"] if p["id"] == "hd_texture_pack")["categories"]


def entry_bytes(game: Path, path: str, variant: str = "u") -> bytes:
    from patcher.archive.filelist import load
    from patcher.mods.hd_zones import read_entry

    sys_dir = game / "alba_data" / "sys"
    return read_entry(sys_dir / f"white_img{variant}.win32.bin",
                      list(load(sys_dir / f"filelist{variant}.win32.bin").entries()), path)


def rewrite_entries(game: Path, replaced: dict, variant: str = "u") -> None:
    """the main pair written again with some payloads swapped, as a hand install leaves it"""
    from patcher.archive.filelist import load
    from patcher.mods.hd_zones import read_entry

    sys_dir = game / "alba_data" / "sys"
    image, index = sys_dir / f"white_img{variant}.win32.bin", sys_dir / f"filelist{variant}.win32.bin"
    rows = [(e.path, replaced.get(e.path) or (b"" if e.is_stub else read_entry(image, [e], e.path)))
            for e in load(index).entries()]
    write_pair(image, index, rows)


def zone_entry_bytes(game: Path, zone: str, path: str, variant: str = "u") -> bytes:
    """a file out of a zone, through the zone index the game's white_img holds"""
    from patcher.archive.filelist import parse
    from patcher.mods.hd_zones import read_entry

    raw = entry_bytes(game, f"zone/filelist_{zone}{variant}.win32.bin", variant)
    blob = game / "alba_data" / "zone" / f"white_{zone}{variant}_img.win32.bin"
    return read_entry(blob, list(parse(raw).entries()), path)
