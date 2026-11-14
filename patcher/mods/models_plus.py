"""
HD Models Plus's main download, built and put in from the Mods drawer.

Its .bat is menus around one tool. FF XIII HD Models.exe takes a recipe out of
Data\\, reads the game's own models out of models\\orig\\ and writes new ones
into models\\mod\\, and LOADFILE imports those into both voice sets' scr pair
with ff13tool -i. Around that, FFXIIItrbSetup.bat finds the game, copies both
scr files out to unpack them whole, and turns away a game whose six aren't the
sizes Steam ships unless you type in the HD Project's folder by hand.

So all of it's ours. The dozen originals the recipes read come straight out
of the archives, a vanilla copy wherever there's one (the HD backup first,
since that's the game from before anything was modded), each recipe is built
natively in the .bat's order (hdmodels/, byte for byte what the tool would
make, with no tool run and no Windows needed), and what it built goes in
through the drawer's own door. That makes it a mod like any other: on
the Installed list, stacked over whatever was there, the HD Project's own
characters included, and off again with Remove. And the HD Project, installed
after it, reads Playable Characters as in and leaves these six alone, since
it already leaves the drawer's models be.
"""

import re
import tempfile
from pathlib import Path

from patcher.mods.hd_runner import TOOLS

NAME = "HD Models Plus"
# what marks its download, beside FFXIIItrbSetup.bat. Its models are built
# natively now (hdmodels/), so the exe itself is never run
TOOL = TOOLS["modelsplus"]
# what the drop has to hold for this to be its main download rather than one
# of the Nova Chrysalia versions, which go in as they are
MARKS = ("_ff xiii hd models+ install.bat", "supportfiles/ffxiiitrbsetup.bat")

# _All HQ.bat's runs, in its order, with the normal hair for Lightning: the HQ
# hair is rigged differently and holds still in some in-game cutscenes. Snow
# has no Ultimate model, so both qualities run his HQ recipe
_LATER = ("Snow", "X_Sazh2", "X_Sazh1", "X_Hope2", "X_Hope1",
          "X_Vanille2", "X_Vanille1", "X_Fang2", "X_Fang1")
VERSIONS = {
    "Ultimate": ("X_Lightning_Hair2", "X_Lightning1", *_LATER),
    "Ultimate, HQ Hair": ("X_Lightning2", "X_Lightning1", *_LATER),
    "High Quality": ("Lightning_Hair", "Snow", "Sazh", "Hope", "Vanille", "Fang"),
}
# picked when the drawer asks, and marked as the one to pick
RECOMMENDED = "Ultimate"
ABOUT = {
    "Ultimate": "The most detailed: rounder arms, legs and fingers.",
    "Ultimate, HQ Hair": "The same, with Lightning's sharper hair, which holds still in a "
                         "few cutscenes.",
    "High Quality": "The game's own cutscene models, used everywhere.",
}
PEOPLE = {"1": "Lightning", "2": "Snow", "3": "Sazh", "4": "Hope", "5": "Vanille", "6": "Fang"}

# a recipe names its models as chr\<group>\<id>\bin\<id>.win32, then the two
# files it writes. Anything else in that shape is a path we won't hand over
MODEL = re.compile(r"^chr\\([a-z0-9]+)\\([a-z0-9_]+)\\bin\\\2\.win32(\.trb|\.imgb)?$", re.I)
WORK = "work"


class ModelsError(Exception):
    pass


def is_main_download(members) -> bool:
    leaves = {member.replace("\\", "/").lower() for member, _ in members}
    return any(leaf == mark or leaf.endswith("/" + mark) for leaf in leaves for mark in MARKS)


def root_of(members) -> str | None:
    """
    the folder in the drop its SupportFiles sits in, "" for the drop itself.
    By its setup .bat as much as its tool: the tool's never run, and an
    antivirus that takes it out of a download leaves the recipes whole
    """
    for member, _ in members:
        path = member.replace("\\", "/")
        for mark in (TOOL, "FFXIIItrbSetup.bat"):
            if path.lower().endswith("supportfiles/" + mark.lower()):
                return path[:-len("supportfiles/" + mark)]
    return None


def person(model_id: str) -> str:
    """who a party model is, by its field or battle id"""
    if re.fullmatch(r"c[02]0[1-6]", model_id):
        return PEOPLE[model_id[-1]]
    return model_id


def recipe(text: str) -> tuple:
    """
    (models it reads, files it writes), as forward-slashed archive paths. The
    header lists both before the opcodes start, and the tool reads every one
    relative to the folder it runs in, so none of them may climb out
    """
    reads, writes = [], []
    for line in text.splitlines():
        line = line.strip()
        hit = MODEL.match(line)
        if not hit:
            continue
        path = line.replace("\\", "/").lower()
        (writes if hit.group(3) else reads).append(path)
    if not reads or len(writes) != 2:
        raise ModelsError("one of its recipes doesn't say which models it reads and writes")
    return reads, writes


def plan(source, root: str, version: str) -> list:
    """[(recipe name, its text, reads, writes)] for one version, checked against the drop"""
    if version not in VERSIONS:
        raise ModelsError(f"{version} isn't one of its versions. Drop it again to pick one.")
    steps = []
    for name in VERSIONS[version]:
        member = f"{root}Data/{name}.txt"
        if not source.has(member):
            raise ModelsError(f"its Data folder is missing {name}.txt, so this download isn't "
                              "whole. Download it again and drop that.")
        text = source.read(member).decode("utf-8", "replace")
        steps.append((name, text, *recipe(text)))
    return steps


def _vanilla(path: str, data: bytes, sizes: dict) -> bool:
    want = sizes.get(path)
    return want is not None and len(data) == want


def originals(install_dir, models, source=None, root: str = "", hd_root=None,
              tick=None) -> dict:
    """
    path -> the game's own bytes for each half of each model, or raises naming
    the person with none to be had. Each half the size Steam ships it at, from
    the first of, in each voice set: where a drawer mod holds it, what's under
    that mod, then the HD backup; anywhere else the game, then the backup.
    After those, the drop's own models\\orig (their .bat leaves one), then the
    HD Project's. `tick()` as each half is found.

    Under the drawer before the backup, since a backup taken after a mod went
    in holds the mod. The game before the backup too, since a backup can hold
    one taken off since. The backup is for what the HD Project replaced, which
    is never the size Steam ships. A half at a time, since a recolor holds
    just the .imgb and the .trb beside it is still the game's own
    """
    from patcher.mods import game_files, hd_vanilla
    from patcher.mods.hd_layout import XIII
    from patcher.mods.hd_zones import read_record
    from patcher.paths import undo_store_for

    install_dir = Path(install_dir)
    sizes = hd_vanilla.table(XIII)
    store = undo_store_for(install_dir) / "hdproject"

    def from_archive(index, archive):
        def read(path):
            entry = game_files.entries(index).get(path)
            if entry is None or entry.is_stub or entry.uncompressed != sizes.get(path):
                return None
            return read_record(archive, entry)
        return read

    def from_folder(folder):
        def read(path):
            target = Path(folder) / path
            try:
                return target.read_bytes() if target.stat().st_size == sizes.get(path) else None
            except OSError:
                return None
        return read

    def from_drop(path):
        member = f"{root}models/orig/{path}"
        found = next((m for m, _ in source.members() if m.lower() == member.lower()), None) \
            if source is not None else None
        return source.read(found) if found else None

    # per voice set: what it holds, then the readers for a half it holds and
    # for one it doesn't
    sets = []
    for pair in (p for p in game_files.pairs(install_dir) if p.kind == "scr"):
        index, archive = store / pair.index.name, store / pair.archive.name
        backup = [from_archive(index, archive)] if index.is_file() and archive.is_file() else []
        under = [lambda path, index=pair.index: game_files.underneath(index, path)]
        sets.append((game_files.held(pair.index), under + backup,
                     [from_archive(pair.index, pair.archive)] + backup))
    after = [from_drop] + ([from_folder(Path(hd_root) / "models" / "orig")]
                           if hd_root is not None else [])

    out = {}
    for model in models:
        for half in (f"{model}.trb", f"{model}.imgb"):
            readers = [read for held, if_held, if_not in sets
                       for read in (if_held if half in held else if_not)] + after
            for read in readers:
                try:
                    data = read(half)
                except Exception:
                    continue
                if data is not None and _vanilla(half, data, sizes):
                    out[half] = data
                    if tick is not None:
                        tick()
                    break
            else:
                who = person(model.rsplit("/", 1)[-1].split(".")[0])
                raise ModelsError(
                    f"it builds from the game's original character models, and {who}'s is "
                    "changed in your game with no original copy here to build from. Take off "
                    "whatever changed it, or have Steam verify the game files (which takes "
                    "every mod in them off too), then drop it again.")
    return out


def sweep(install_dir) -> None:
    """build folders an earlier run left, a crash or a power cut mid build"""
    from patcher.fileops import clear_tree
    from patcher.paths import undo_store_for

    work = undo_store_for(install_dir) / WORK
    for leftover in work.glob("models-plus-*") if work.is_dir() else ():
        clear_tree(leftover)


def _stamp(path: Path):
    try:
        info = path.stat()
        return info.st_mtime_ns, info.st_size
    except OSError:
        return None


def build(install_dir, source, root: str, version: str, on_line=None, cancel=None,
          progress=None, hd_root=None) -> Path:
    """
    the version's models, built into a folder of ours under the undo store, and
    that folder's models\\mod. The caller puts those in and clears the folder.
    Nothing touches the game here: a recipe that fails leaves it as it was.
    `progress(done, of)` hears how far it's got, out of a hundred.

    Built natively (hdmodels/), the way FF XIII HD Models.exe builds them, out
    of the download's own Data\\ and textures and the originals read above,
    in the .bat's order so a later recipe's model goes over an earlier one's
    """
    from patcher import zipnames
    from patcher.fileops import clear_tree, write_atomic
    from patcher.mods import hd_runner
    from patcher.mods.hdmodels import build as builder
    from patcher.mods.meter import Meter
    from patcher.paths import undo_store_for

    meter = Meter(progress)
    steps = plan(source, root, version)
    for member, _ in source.members():
        relative = member[len(root):] if member.startswith(root) else ""
        if relative.split("/", 1)[0].lower() in ("data", "textures") and \
                zipnames.safe_target(Path(tempfile.gettempdir()) / "models-plus", relative,
                                     False) is None:
            # Data/../../ is the game folder from where their .bat runs. No
            # copy of theirs names a file that way, so none of this one is trusted
            raise ModelsError(f"{member} in it points outside its own folder, which no real "
                              "copy of it does. Nothing has been changed.")
    needs = sorted({model for _, _, reads, _ in steps for model in reads})
    # the runs are most of it, reading the originals about one more
    meter.split(read=1.0, runs=len(steps))
    halves = 2 * len(needs)
    vanilla = originals(install_dir, needs, source=source, root=root, hd_root=hd_root,
                        tick=lambda: meter.add("read", 1.0 / halves))
    files = _Files(source, root, vanilla)

    sweep(install_dir)
    (undo_store_for(install_dir) / WORK).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="models-plus-", dir=undo_store_for(install_dir) / WORK))
    try:
        for name, _, _, writes in steps:
            if cancel is not None and cancel.is_set():
                raise hd_runner.Cancelled(f"stopped while building {name}")
            who = person(writes[0].rsplit("/", 1)[-1].split(".")[0])
            raw = files.read(f"Data\\{name}.txt")
            try:
                made = builder.run(raw, files, log=on_line, cancel=cancel)
            except builder.Stopped:
                raise hd_runner.Cancelled(f"stopped while building {name}") from None
            except builder.ModelError as e:
                raise ModelsError(f"{who}'s model couldn't be built: {e}. Nothing has been "
                                  "changed.") from None
            wanted = {f"models/mod/{path}" for path in writes}
            got = {path.replace("\\", "/").lower(): data for path, data in made.items()}
            if not wanted <= set(got):
                raise ModelsError(f"{who}'s model couldn't be built: its recipe didn't make it. "
                                  "Nothing has been changed.")
            for path, data in got.items():
                target = work / Path(*path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(target, data)
            meter.add("runs", 1)
    except BaseException:
        # half a build is nobody's
        clear_tree(work)
        raise
    meter.end()
    return work


class _Files:
    """
    what the builder opens, by the exe's own relative paths: models\\orig\\
    out of the originals read for it, the rest out of the download beside
    its SupportFiles, any case, never above it
    """

    def __init__(self, source, root: str, vanilla: dict):
        self.source, self.root = source, root
        self.vanilla = {path.lower(): data for path, data in vanilla.items()}
        self.members = {m.replace("\\", "/").lower(): m for m, _ in source.members()}

    def read(self, path: str):
        rel = path.replace("\\", "/").lstrip("./").lower()
        if rel.startswith("models/orig/"):
            return self.vanilla.get(rel[len("models/orig/"):])
        if ".." in rel.split("/"):
            return None
        member = self.members.get((self.root + rel).lower())
        return self.source.read(member) if member is not None else None


def built_at(work: Path) -> Path:
    return Path(work) / "models" / "mod"

