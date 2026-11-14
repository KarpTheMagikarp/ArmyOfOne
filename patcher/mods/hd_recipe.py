"""
Reads The FF XIII HD Project's data files instead of interpreting its .bat.

The 396 line .bat is mostly menus and path detection; the real work is driven by
data files, verified against the real mod:

    Data/LoadHD<Category>.txt    which ids are in a category
    Data/<id>.txt                what to do to that id, as opcodes
    Data/FFXIII_*_Models.bin     a flat container holding the payload blobs

The .bat loops `for %%d in (Data\\loadHD*.txt) do call "FFXIII HD Installer" ...`,
and opts into the PC category by restoring LoadHDPCs.txt from .tx1 first.

Read only. The opcodes are mesh surgery, not file swaps, and
`Recipe.needs_mesh_surgery` says so.
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path

# opcodes that only reference a payload blob, versus the ones that edit geometry
# in place. Every zone, npc and fa recipe in the shipped mod uses the first set
# alone; only the ten playable character recipes reach for the second.
BLOB_OPS = {"~", "X"}
MESH_OPS = {"V", "E", "P", "T", "N", "R", "J", "O", "D", "I", "e", "u",
            "/G", "/SCULPTING", "/~"}


class RecipeError(Exception):
    pass


@dataclass
class Section:
    """a "#:Label" and the members under it"""
    label: str
    members: list = field(default_factory=list)      # (type code or None, id)


@dataclass
class Category:
    """
    one LoadHD<name>.txt.

    sectioned, which is easy to miss: most categories carry a single "#:Label"
    at the top, but LoadHDEnvironments.txt has one per zone with its members
    underneath, so flattening to a single label reports the file as belonging
    to whichever zone happened to come last.
    """
    name: str
    sections: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.sections[0].label if self.sections else self.name

    @property
    def members(self) -> list:
        return [m for section in self.sections for m in section.members]

    @property
    def ids(self) -> list:
        return [i for _, i in self.members]


@dataclass
class Recipe:
    """one <id>.txt: which game files it touches and what it does to them"""
    target: str                       # the group, e.g. "fa", "zone", "npc", "pc\\c001"
    base: str                         # chr/fa/f066/bin/f066.win32
    trb: str                          # ...win32.trb, the model bundle
    imgb: str                         # ...win32.imgb, the texture blob
    ops: list = field(default_factory=list)          # (opcode, [fields])
    header: list = field(default_factory=list)       # the seven leading numbers, verbatim

    @property
    def blobs(self) -> list:
        """(blob name, container file) for every payload this recipe pulls in"""
        out = []
        for code, fields in self.ops:
            if code == "~" and len(fields) >= 2:
                out.append((fields[-2], fields[-1].replace("\\", "/")))
        return out

    @property
    def needs_mesh_surgery(self) -> bool:
        """
        whether this recipe edits geometry rather than just swapping a payload.

        the line between what a native installer could plausibly do and what
        would mean reimplementing a model editor for an undocumented format.
        """
        return any(code in MESH_OPS for code, _ in self.ops)

    @property
    def game_paths(self) -> list:
        """the archive paths this recipe rewrites, forward slashed"""
        return [p.replace("\\", "/") for p in (self.trb, self.imgb)]


def read_category(path: Path) -> Category:
    """
    parses a LoadHD<name>.txt. "#:Label" opens a section, then members until
    the next. A member is "<type> <id>" (s s101, P c001) or one token (Z8 in
    the zone lists), which keeps a type of None since zones don't map to a
    recipe file.
    """
    path = Path(path)
    sections = []

    for raw in path.read_text(encoding="latin-1").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#:"):
            sections.append(Section(label=line[2:].strip()))
            continue

        parts = line.split()
        if len(parts) == 1:
            member = (None, parts[0])
        elif len(parts) == 2:
            member = (parts[0], parts[1])
        else:
            raise RecipeError(f"{path.name}: cannot read {line!r}")

        if not sections:
            sections.append(Section(label=path.stem))
        sections[-1].members.append(member)

    return Category(name=path.stem, sections=sections)


def read_recipe(path: Path) -> Recipe:
    """
    parses an <id>.txt.

    Seven leading numbers and strings whose meaning is not established, then the
    three game paths, then a count, then the opcode lines. The header is carried
    through verbatim rather than guessed at: this module has no reason to
    interpret it and every reason not to invent a meaning for it.
    """
    path = Path(path)
    lines = [l.rstrip() for l in path.read_text(encoding="latin-1").splitlines()]
    if len(lines) < 11:
        raise RecipeError(f"{path.name}: too short to be a recipe")

    ops = []
    for line in lines[10:]:
        if not line.startswith(":"):
            continue
        body = line[1:]
        code, _, rest = body.partition(",")
        ops.append((code, rest.split(",") if rest else []))

    return Recipe(target=lines[2], base=lines[7], trb=lines[8], imgb=lines[9],
                  ops=ops, header=lines[:7])


def read_container(path: Path) -> dict:
    """
    reads a FFXIII_*_Models.bin into {name: payload bytes}.

        uint32                       entry count
        count x { char[32] name      null padded
                  uint32 offset      from the start of the file
                  uint32 length }
        payloads

    The first offset equals the header size (4 + 6*40 = 244 on General),
    which is how the layout was confirmed. The blobs are named .txt but hold
    float vertex data.
    """
    raw = Path(path).read_bytes()
    if len(raw) < 4:
        raise RecipeError(f"{path.name} is too short to be a container")

    count, = struct.unpack_from("<I", raw, 0)
    expected_header = 4 + count * 40
    if expected_header > len(raw):
        raise RecipeError(f"{path.name} claims {count} entries, which its size cannot hold")

    out = {}
    for i in range(count):
        base = 4 + i * 40
        name = raw[base:base + 32].split(b"\x00")[0].decode("latin-1")
        offset, length = struct.unpack_from("<II", raw, base + 32)
        if offset < expected_header or offset + length > len(raw):
            raise RecipeError(f"{path.name}: {name} points outside the file")
        out[name] = raw[offset:offset + length]
    return out


def find_data_dir(root: Path) -> Path | None:
    """
    the mod's Data/ folder inside a drop, or None. Checks the folder, its
    parent and one wrapper level down, no deeper, since a recursive hunt for
    "Data" eventually finds the wrong one.
    """
    root = Path(root)
    if not root.is_dir():
        return None
    for candidate in (root, *(c for c in sorted(root.iterdir()) if c.is_dir())):
        data = candidate / "Data"
        if data.is_dir() and any(data.glob("LoadHD*.tx*")):
            return data
    return None


def looks_like_archive(path: Path) -> bool:
    name = Path(path).name.lower()
    return name.endswith((".zip", ".7z", ".rar", ".tar.gz", ".tgz"))


def names_in_archive(path: Path) -> list:
    """
    the member names, without unpacking six gigabytes to find out what it is.

    zip is read from its central directory, which is cheap whatever the
    archive weighs. rar and 7z cannot be listed with the standard library at
    all, so they come back empty and the caller falls through to unpacking.
    """
    import zipfile

    path = Path(path)
    if not path.name.lower().endswith(".zip"):
        return []
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()[:4000]
    except (zipfile.BadZipFile, OSError):
        return []


def archive_holds_project(names: list) -> bool:
    """
    whether this listing is the HD Project, by the same three signs detect()
    uses on a folder: the category lists, a model container, and the tools.
    """
    lowered = [n.replace("\\", "/").lower() for n in names]

    def anywhere(test):
        return any(test(n) for n in lowered)

    return (anywhere(lambda n: "/data/loadhd" in n or n.startswith("data/loadhd"))
            and anywhere(lambda n: "models" in n and n.endswith(".bin"))
            and anywhere(lambda n: "supportfiles/" in n))


def detect(root: Path) -> dict | None:
    """
    whether this is The FF XIII HD Project, by shape since the folder is
    named whatever it was unzipped to: a Data/ with LoadHD lists, a model
    container beside them, and SupportFiles/. None, not an error, for
    anything else.
    """
    root = Path(root)
    data = find_data_dir(root)
    if data is None:
        return None

    containers = sorted(p.name for p in data.glob("FFXIII_*Models*.bin"))
    if not containers:
        return None

    home = data.parent
    support = home / "SupportFiles"
    if not support.is_dir():
        return None

    lists = sorted(p.name for p in data.glob("LoadHD*.tx*"))
    return {
        "root": str(home),
        "data": str(data),
        "lists": lists,
        "containers": containers,
        "tools": sorted(p.name for p in support.iterdir() if p.is_file()),
        "has_textures": (home / "textures").is_dir(),
    }


def installer_steps(categories: list, selected: set, installed: set | None = None) -> list:
    """
    the mod's own menu keys for getting from `installed` to `selected`, for
    when we can't do the geometry work ourselves. Install All only when every
    category is wanted, since the mod's A includes characters with no prompt.
    """
    installed = set(installed or ())
    # XIII-2's mod has no menu to press keys in, so there is nothing to say
    if any("menu" not in c for c in categories):
        return []
    wanted = {c["id"] for c in categories if c["id"] in selected}
    by_id = {c["id"]: c for c in categories}

    adding = [by_id[i] for i in (wanted - installed) if i in by_id]
    removing = [by_id[i] for i in (installed - wanted) if i in by_id]
    order = {c["id"]: n for n, c in enumerate(categories)}
    adding.sort(key=lambda c: order[c["id"]])
    removing.sort(key=lambda c: order[c["id"]])

    steps = []
    if adding and len(wanted) == len(categories) and not installed:
        steps.append("A, to install everything")
    else:
        for category in adding:
            steps.append(f"C then {category['menu']}, to add {category['label']}")
    for category in removing:
        steps.append(f"D then {category['menu']}, to remove {category['label']}")
    return steps


def survey(data_dir: Path) -> dict:
    """
    what this copy of the mod would do, without doing any of it.

    the point is a straight answer to "can this be installed natively", per
    category, rather than one answer for the whole mod. They are not the same:
    the character recipes are a different kind of work from the rest.
    """
    data_dir = Path(data_dir)
    categories, recipes, missing = {}, {}, []

    for path in sorted(data_dir.glob("LoadHD*.tx*")):
        category = read_category(path)
        categories[path.name] = category
        for _, member in category.members:
            recipe_path = data_dir / f"{member}.txt"
            if not recipe_path.is_file():
                missing.append(member)
                continue
            recipes[member] = read_recipe(recipe_path)

    return {
        "categories": categories,
        "recipes": recipes,
        "missing_recipes": missing,
        "mesh_surgery": sorted(k for k, r in recipes.items() if r.needs_mesh_surgery),
        "blob_only": sorted(k for k, r in recipes.items() if not r.needs_mesh_surgery),
    }
