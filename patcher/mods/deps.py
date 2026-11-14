"""
The mods an add-on is made to be unpacked over, kept so the add-on installs
from a drop without anyone going to find them, offline and with no account.

A Vanille outfit for HD Models Plus ships its Data\\ and nothing else. Its .bat
runs SupportFiles\\FFXIIItrbSetup.bat and FF XIII HD Models.exe out of HD Models
Plus's own download, which its readme has you unpack it over. The drawer gets
that folder by keeping it: copied out of the base mod's download the time it
went in through the drawer, into this user's data folder. Every add-on after
that finds it there. One that comes first is told which mod to drop, once.

Nothing third party ships with the patcher: what's kept is this PC's own copy
of a download its owner already has.
"""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

# a tool folder past this is someone's whole download, not tools
KEEP_FILES, KEEP_BYTES = 2000, 512 << 20


@dataclass(frozen=True)
class Dep:
    id: str
    label: str
    data: str           # the game's data folder, which game a .bat is played for
    mark: str           # the member that says a download is this one, under the folder kept
    tools: frozenset    # what only it ships, lower case, so a .bat wanting one is its add-on


KNOWN = (
    Dep("hd_models_plus", "HD Models Plus", "white_data",
        "supportfiles/ffxiiitrbsetup.bat",
        frozenset({"ffxiiitrbsetup.bat", "ff xiii hd models.exe"})),
    Dep("hd_project", "the FFXIII HD Project", "white_data",
        "supportfiles/ffxiii_hdproject.exe",
        frozenset({"ffxiii_hdproject.exe", "ffxiii_modelshd.exe"})),
    # its FF XIII HD Models.exe is a different program to HD Models Plus's under
    # the same name, which is why a dep is picked by game first
    Dep("hd_project2", "the FFXIII-2 HD Project", "alba_data",
        "supportfiles/ffxiii2_hdproject.exe",
        frozenset({"ffxiii2_hdproject.exe", "ff xiii hd models.exe"})),
)


def keep_root() -> Path:
    from patcher.paths import persistent_data_root

    return persistent_data_root() / "deps"


def of(data: str, tool: str) -> Dep | None:
    """the base mod that alone ships `tool`, for a game by its data folder"""
    tool = tool.lower()
    return next((d for d in KNOWN if d.data == data and tool in d.tools), None)


def by_id(dep_id: str) -> Dep | None:
    return next((d for d in KNOWN if d.id == dep_id), None)


def label_of(root: Path) -> str | None:
    """what a kept download's called, for the line saying where its tools came from"""
    dep = by_id(Path(root).name) if Path(root).parent == keep_root() else None
    return dep.label if dep else None


def homes(data: str) -> list:
    """the kept downloads for a game, each laid out the way its own unpacks"""
    from patcher.mods.sandbox import _on_disk

    root = keep_root()
    return [root / d.id for d in KNOWN
            if d.data == data and _on_disk(root / d.id, d.mark.split("/")) is not None]


def foreign(has, data: str) -> bool:
    """
    whether a download is a known one for another game, by `has(member)`. The
    XIII-2 HD Project has an FF XIII HD Models.exe too, which lent to a XIII
    add-on would build models for the wrong game
    """
    return bool(data) and any(d.data != data and has(d.mark) for d in KNOWN)


def _top(member: str, mark: str):
    """the folder a mark's member says is kept, as the member spells it, or None"""
    low = member.replace("\\", "/").lower()
    if low != mark and not low.endswith("/" + mark):
        return None
    cut = len(member) - len(mark)
    return member.replace("\\", "/")[:cut] + member.replace("\\", "/")[cut:].split("/", 1)[0]


def remember(source, members, data: str) -> list:
    """
    every known base mod in a drop for this game, its tool folder copied into
    the keep. The ids kept. Best effort: an install never fails over this
    """
    kept = []
    names = [m for m, _ in members]
    for dep in KNOWN:
        if dep.data != data:
            continue
        top = next((t for t in (_top(m, dep.mark) for m in names) if t), None)
        if top is None:
            continue
        inside = [(m, s) for m, s in members if m.replace("\\", "/").startswith(top + "/")]
        if not inside or len(inside) > KEEP_FILES or sum(s for _, s in inside) > KEEP_BYTES:
            continue
        try:
            _keep(dep, top, inside, source)
        except OSError:
            continue
        kept.append(dep.id)
    return kept


def _keep(dep: Dep, top: str, inside: list, source) -> None:
    root = keep_root()
    root.mkdir(parents=True, exist_ok=True)
    folder = top.rsplit("/", 1)[-1]
    # laid out in full beside the keep, then swapped in, so a crash halfway
    # never leaves a kept folder short of a tool
    fresh = Path(tempfile.mkdtemp(prefix=f".{dep.id}-", dir=root))
    try:
        for member, _ in inside:
            rest = member.replace("\\", "/")[len(top) + 1:]
            target = fresh / folder / rest
            if ".." in Path(rest).parts:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.opened(member) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
        old = root / dep.id
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        fresh.replace(old)
    except BaseException:
        shutil.rmtree(fresh, ignore_errors=True)
        raise

