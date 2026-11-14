"""
What ff13tool is about to try, worked out from the index before it runs.

`ff13tool -x` once died with code 1 after 3076 of 14381 files and said nothing
useful. The two usual causes are knowable up front, since filelist_scr* lists
every path and size the tool will write:

    MAX_PATH   every entry becomes mod_root/<path>, ff13tool isn't long path
               aware, and Windows refuses past 259 characters
    real size  the mod's script guesses twice the archive plus 20GB; the index
               has the actual total

Both are cheap and both give a sentence a person can act on.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from patcher.mods.hd_runner import REVERT

# 260 counts the terminating null, so a usable path is 259 characters. Long
# path support does not rescue this: it needs the opt-in manifest as well as
# the registry key, and the mod's tools are old enough to have neither.
MAX_PATH = 259

# reserved on Windows anywhere in a path, not just at the end
BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')

# the two the seed caches and the tools rewrite. A .trb is the model and its
# .imgb holds the image data, and the mod's own robocopy takes exactly this
# pair, so an install that took one without the other would be half a model.
MODEL_SUFFIXES = (".win32.trb", ".win32.imgb")

# the six main character models at vanilla byte sizes, from the mod's own
# Setup.bat CHECKFILESIZE lines (it quietly falls back to a reduced list when
# they don't match). They check extracted files; the index has the same sizes,
# so we can ask before unpacking or backing anything up.
VANILLA_PCS = {
    "chr/pc/c201/bin/c201.win32.trb": 794339,
    "chr/pc/c201/bin/c201.win32.imgb": 2130264,
    "chr/pc/c202/bin/c202.win32.trb": 886815,
    "chr/pc/c202/bin/c202.win32.imgb": 2410136,
    "chr/pc/c203/bin/c203.win32.trb": 777069,
    "chr/pc/c203/bin/c203.win32.imgb": 2128868,
    "chr/pc/c204/bin/c204.win32.trb": 714859,
    "chr/pc/c204/bin/c204.win32.imgb": 2326632,
    "chr/pc/c205/bin/c205.win32.trb": 719745,
    "chr/pc/c205/bin/c205.win32.imgb": 2164820,
    "chr/pc/c206/bin/c206.win32.trb": 830980,
    "chr/pc/c206/bin/c206.win32.imgb": 2288372,
}


# XIII-2's HD2Setup.bat measures three character models before it trusts a
# game, at these sizes. The vanilla filelistu carries the same numbers.
VANILLA2_CHR = {
    "chr/pc/c108/bin/c108.win32.trb": 677433,
    "chr/pc/c159/bin/c159.win32.trb": 848878,
    "chr/npc/n012/bin/n012.win32.trb": 205897,
}

# each zone's own index as the vanilla English white_imgu carries it, read off
# the real filelistu. A zone install imports a rewritten one over it, so a
# different size here means something has already been in that zone.
VANILLA2_ZONE_INDEXES = {
    "u": {
        "z0010": 47688, "z0020": 33176, "z0030": 3944, "z0043": 4904, "z0052": 5512,
        "z0060": 3656, "z0070": 3800, "z0080": 9968, "z0092": 6504, "z0114": 41600,
        "z0130": 13000, "z0157": 10472, "z0160": 8160, "z0191": 6168, "z0210": 11496,
    },
}

# each zone blob the mod rewrites, at its size on a Steam copy, both voice sets.
# Nobody publishes these for XIII-2, so they come off a listing of the same
# install tests/data/filelistu_sample.bin did. Clean where it counts: that index
# still has every zone's own index mid-archive, where an HD import would have
# appended a new one at the end, and each c blob is within 12MB of its u twin
# when HD art adds 74 to 553MB to whichever set it goes into.
VANILLA2_ZONES = {
    "white_z0010u_img.win32.bin": 1141753856,
    "white_z0020u_img.win32.bin": 282927104,
    "white_z0030u_img.win32.bin": 351246336,
    "white_z0043u_img.win32.bin": 94439424,
    "white_z0052u_img.win32.bin": 236054528,
    "white_z0060u_img.win32.bin": 83206144,
    "white_z0070u_img.win32.bin": 49430528,
    "white_z0080u_img.win32.bin": 88553472,
    "white_z0092u_img.win32.bin": 137158656,
    "white_z0114u_img.win32.bin": 408901632,
    "white_z0130u_img.win32.bin": 213133312,
    "white_z0157u_img.win32.bin": 137240576,
    "white_z0160u_img.win32.bin": 908138496,
    "white_z0191u_img.win32.bin": 64354304,
    "white_z0210u_img.win32.bin": 170481664,
    "white_z0010c_img.win32.bin": 1153591296,
    "white_z0020c_img.win32.bin": 291217408,
    "white_z0030c_img.win32.bin": 351023104,
    "white_z0043c_img.win32.bin": 94337024,
    "white_z0052c_img.win32.bin": 236034048,
    "white_z0060c_img.win32.bin": 83101696,
    "white_z0070c_img.win32.bin": 49446912,
    "white_z0080c_img.win32.bin": 91164672,
    "white_z0092c_img.win32.bin": 137226240,
    "white_z0114c_img.win32.bin": 417482752,
    "white_z0130c_img.win32.bin": 216254464,
    "white_z0157c_img.win32.bin": 137940992,
    "white_z0160c_img.win32.bin": 908275712,
    "white_z0191c_img.win32.bin": 65540096,
    "white_z0210c_img.win32.bin": 171094016,
}


@dataclass
class Survey:
    entries: int = 0
    bytes_needed: int = 0
    longest: int = 0                              # full path, characters
    longest_path: str = ""
    too_long: list = field(default_factory=list)  # (path, full length)
    illegal: list = field(default_factory=list)   # paths Windows will not take
    # the model files under chr/<group>, by group. The seed copies these into
    # models\\orig and the tools write about as much again into models\\mod, so
    # this is most of what an install costs beyond the extraction itself.
    by_group: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.too_long and not self.illegal

    def cache_bytes(self, groups=()) -> int:
        """what models\\orig and models\\mod will weigh for these groups"""
        wanted = set(groups) or set(self.by_group)
        return sum(size for group, size in self.by_group.items() if group in wanted) * 2


def survey_extraction(filelist_path: Path, mod_root: Path) -> Survey:
    """
    measures the extraction ff13tool -x is about to perform.

    mod_root is the cwd the tool runs in, so the path it writes is that plus
    the entry's own path. Both halves matter and only one of them is the mod's
    fault.
    """
    from patcher.archive.filelist import load

    filelist = load(Path(filelist_path))
    # normalised the way the tool will write it, since the index spells paths
    # with forward slashes and Windows counts characters either way
    base = len(str(Path(mod_root))) + 1
    found = Survey()

    for entry in filelist.entries():
        found.entries += 1
        found.bytes_needed += entry.uncompressed
        if entry.path.endswith(MODEL_SUFFIXES):
            parts = entry.path.split("/")
            if len(parts) > 2 and parts[0] == "chr":
                found.by_group[parts[1]] = found.by_group.get(parts[1], 0) + entry.uncompressed
        full = base + len(entry.path)
        if full > found.longest:
            found.longest, found.longest_path = full, entry.path
        if full > MAX_PATH:
            found.too_long.append((entry.path, full))
        if BAD_CHARS.search(entry.path):
            found.illegal.append(entry.path)

    return found


def survey_zone_paths(image: Path, listing: Path, variant: str, zones, mod_root: Path) -> Survey:
    """
    the same measurement for the zone archives, read out of the indexes
    white_img<v> carries for them. ff13tool -x lands every entry under
    models\\orig\\zone<v>\\white_<zone><v>_img\\, two folders deeper than the
    scr pair goes, and nothing else the zones write is deeper than that.
    """
    from patcher.archive.filelist import load, parse
    from patcher.mods.hd_zones import read_entry

    entries = list(load(Path(listing)).entries())
    base = len(str(Path(mod_root))) + 1
    found = Survey()
    for zone in zones:
        raw = read_entry(Path(image), entries, f"zone/filelist_{zone}{variant}.win32.bin")
        prefix = len(f"models\\orig\\zone{variant}\\white_{zone}{variant}_img\\")
        for entry in parse(raw).entries():
            found.entries += 1
            full = base + prefix + len(entry.path)
            if full > found.longest:
                found.longest, found.longest_path = full, entry.path
            if full > MAX_PATH:
                found.too_long.append((entry.path, full))
            if BAD_CHARS.search(entry.path):
                found.illegal.append(entry.path)
    return found


def already_modded(filelist_path: Path) -> list:
    """
    which of the six main character models aren't vanilla size. Empty means
    the game looks untouched, which is what makes "is this backup vanilla"
    answerable. A missing entry isn't counted, that's a stranger problem.
    """
    from patcher.archive.filelist import load
    from patcher.mods.hd_layout import XIII2, layout_of

    filelist_path = Path(filelist_path)
    book = load(filelist_path)
    sizes = {entry.path.replace("\\", "/").lower(): entry.uncompressed
             for entry in book.entries()}

    # the index sits at <game>/<data>/sys, which is how it says which game it is
    table = VANILLA2_CHR if layout_of(filelist_path.parents[2]) is XIII2 else VANILLA_PCS
    changed = []
    for path, expected in table.items():
        actual = sizes.get(path.lower())
        if actual is not None and actual != expected:
            changed.append((path, expected, actual))
    return changed


# vanilla zone archive sizes, both languages, from their Setup.bat's
# CHECKFILESIZE lines (4.49GB English, 4.53GB Japanese). A blob off this size is
# modded, and it's also the zone install's cost before the game's been read.
VANILLA_ZONES = {
    "white_z002u_img.win32.bin": 343752704,
    "white_z003u_img.win32.bin": 317165568,
    "white_z004u_img.win32.bin": 367048704,
    "white_z006u_img.win32.bin": 204761088,
    "white_z008u_img.win32.bin": 515491840,
    "white_z010u_img.win32.bin": 170987520,
    "white_z015u_img.win32.bin": 163403776,
    "white_z016u_img.win32.bin": 317988864,
    "white_z017u_img.win32.bin": 277108736,
    "white_z018u_img.win32.bin": 305455104,
    "white_z019u_img.win32.bin": 414627840,
    "white_z020u_img.win32.bin": 89540608,
    "white_z021u_img.win32.bin": 76806144,
    "white_z022u_img.win32.bin": 146659328,
    "white_z023u_img.win32.bin": 143656960,
    "white_z024u_img.win32.bin": 63563776,
    "white_z026u_img.win32.bin": 240226304,
    "white_z027u_img.win32.bin": 154578944,
    "white_z029u_img.win32.bin": 429817856,
    "white_z030u_img.win32.bin": 78182400,
    "white_z002c_img.win32.bin": 348907520,
    "white_z003c_img.win32.bin": 318754816,
    "white_z004c_img.win32.bin": 370788352,
    "white_z006c_img.win32.bin": 210784256,
    "white_z008c_img.win32.bin": 513894400,
    "white_z010c_img.win32.bin": 171452416,
    "white_z015c_img.win32.bin": 165844992,
    "white_z016c_img.win32.bin": 321077248,
    "white_z017c_img.win32.bin": 281950208,
    "white_z018c_img.win32.bin": 310155264,
    "white_z019c_img.win32.bin": 414902272,
    "white_z020c_img.win32.bin": 89786368,
    "white_z021c_img.win32.bin": 79265792,
    "white_z022c_img.win32.bin": 150048768,
    "white_z023c_img.win32.bin": 145975296,
    "white_z024c_img.win32.bin": 63342592,
    "white_z026c_img.win32.bin": 242812928,
    "white_z027c_img.win32.bin": 153210880,
    "white_z029c_img.win32.bin": 430682112,
    "white_z030c_img.win32.bin": 78241792,
}


def zones_already_modded(game_dir: Path, variant: str) -> list:
    """
    zone blobs that are not the size a vanilla copy ships them at.

    Sizes rather than a diff, because there is nothing to diff against on a
    first run: this is exactly the moment the backup has not been taken yet.
    """
    from patcher.mods.hd_layout import XIII2, layout_of

    if layout_of(game_dir) is XIII2:
        return _zones_already_modded2(game_dir, variant)
    zone_dir = Path(game_dir) / "white_data" / "zone"
    changed = []
    for name, size in VANILLA_ZONES.items():
        if f"{variant}_img" not in name:
            continue
        blob = zone_dir / name
        if blob.is_file() and blob.stat().st_size != size:
            changed.append((name, blob.stat().st_size, size))
    return changed


def _zones_already_modded2(game_dir: Path, variant: str) -> list:
    """
    XIII-2's version: the blobs by size like XIII's, and where there is a table
    for it, each zone's own index at the size the main index lists it. An HD
    zone install rewrites both, so either one is enough. Named by blob so
    callers read it the same way as XIII's.
    """
    from patcher.archive.entry import find
    from patcher.archive.filelist import load
    from patcher.mods.hd_layout import XIII2

    zone_dir = XIII2.zone_dir(game_dir)
    changed = {}
    for name, size in VANILLA2_ZONES.items():
        blob = zone_dir / name
        if f"{variant}_img" in name and blob.is_file() and blob.stat().st_size != size:
            changed[name] = (name, blob.stat().st_size, size)

    wanted = VANILLA2_ZONE_INDEXES.get(variant)
    listing = XIII2.sys_dir(game_dir) / f"filelist{variant}.win32.bin"
    if wanted and listing.is_file():
        entries = list(load(listing).entries())
        for zone, size in wanted.items():
            name = f"white_{zone}{variant}_img.win32.bin"
            entry = find(entries, f"zone/filelist_{zone}{variant}.win32.bin")
            if entry is not None and entry.uncompressed != size:
                changed.setdefault(name, (name, entry.uncompressed, size))
    return [changed[name] for name in sorted(changed)]


def cache_not_vanilla(mod_root: Path, variant: str) -> str:
    """
    why models\\orig can't be trusted as a source of originals, or "".

    Every HD file is derived from the vanilla model in models\\orig, so a
    repair is only right while that cache holds originals. If it was first
    filled from an already modded game (what their installer does when the
    cache goes missing), the tools write HD over HD. Checked by size, since
    the cache isn't in our backup and vanilla sizes are known.
    """
    cache = Path(mod_root) / "models" / "orig"
    wanted = {cache / f"zone{variant}" / name: size
              for name, size in VANILLA_ZONES.items() if f"{variant}_img" in name}
    wanted.update({cache / path: size for path, size in VANILLA_PCS.items()})

    for path, size in sorted(wanted.items()):
        if not path.is_file() or path.stat().st_size == size:
            continue
        return (f"{path.name} in the originals cache is {path.stat().st_size:,} bytes "
                f"and a vanilla copy is {size:,}, so that cache was filled from a game "
                "that had already been modded. Rebuilding from it would write HD art "
                "over HD art. Delete models\\orig in the mod folder and run this again, "
                f"or put the game back with {REVERT} and install from vanilla.")
    return ""


def zone_sizes(game_dir: Path, variant: str, data_dir: Path, categories: list) -> dict:
    """
    what a zone category costs: the sum of its loose archives under
    white_data\\zone, since the whole of each gets rewritten. Absent rather
    than zero when the game has none, since zero reads as free.
    """
    from patcher.mods.hd_recipe import read_category
    from patcher.mods.hd_zones import zone_ids

    zone_dir = Path(game_dir) / "white_data" / "zone"
    out = {}
    for category in categories:
        listing = Path(data_dir) / category.get("list", "")
        if not listing.is_file():
            continue
        total = 0
        for zone in zone_ids(read_category(listing).members):
            blob = zone_dir / f"white_{zone}{variant}_img.win32.bin"
            if blob.is_file():
                total += blob.stat().st_size
        if total:
            out[category["id"]] = total
    return out


def category_sizes(filelist_path: Path, data_dir: Path, categories: list) -> dict:
    """
    what each category costs on disk, by id, in bytes (Enemies 463MB,
    Eidolons 38MB): the sum of the models its ids own. Unreadable comes back
    absent, not zero, since zero reads as free.
    """
    from patcher.archive.filelist import load
    from patcher.mods.hd_recipe import read_category
    from patcher.mods.hd_runner import GROUPS, usable_id

    sizes = {entry.path.replace("\\", "/").lower(): entry.uncompressed
             for entry in load(Path(filelist_path)).entries()}

    out = {}
    for category in categories:
        listing = Path(data_dir) / category.get("list", "")
        if not listing.is_file():
            continue
        total = 0
        for kind, member in read_category(listing).members:
            group = GROUPS.get((kind or "").upper())
            if group is None or not usable_id(member):
                continue
            for suffix in MODEL_SUFFIXES:
                key = f"chr/{group}/{member}/bin/{member}{suffix}".lower()
                total += sizes.get(key, 0)
        # zero means nothing in this category resolved to a chr model, which is
        # true of Environmental Zones: its entries are Z markers against a
        # different archive set entirely. "0 MB" beside it would be a lie, and
        # absent renders as nothing at all.
        if total:
            out[category["id"]] = total
    return out


def headroom(survey: Survey) -> int:
    """how many characters the mod folder could still grow by, or a negative"""
    return MAX_PATH - survey.longest


def explain(survey: Survey, mod_root: Path) -> str:
    """
    the sentence to put in front of somebody, or "" if there is nothing wrong.

    Names the shortening that fixes it rather than just the limit, because
    "path too long" invites deleting files and the actual answer is to move the
    folder up a few levels.
    """
    if survey.illegal:
        return (f"{len(survey.illegal)} paths in this archive contain characters "
                f"Windows will not accept, starting with {survey.illegal[0]!r}. "
                "The archive is probably damaged, so download it again.")
    if not survey.too_long:
        return ""

    mod_root = Path(mod_root)
    over = survey.longest - MAX_PATH
    room = len(str(mod_root)) - over
    return (
        f"{len(survey.too_long)} of {survey.entries} files would end up with a "
        f"path longer than Windows allows ({survey.longest} characters, and the "
        f"limit is {MAX_PATH}).\n\n"
        f"The mod folder is what makes it long:\n{mod_root}\n\n"
        f"Move it somewhere shorter, {room} characters or less, and this goes "
        f"away. Something like C:\\FF13HD works.")
