"""
The whole HD Project install, from a dropped folder to a repacked archive.
The tools stay the mod's, the sequencing moves here (see hd_runner for why).

    back up              filelist_scr* and white_scr*, ours, before anything
    copy in              the pair, out of the game and into the mod folder
    ff13tool -x          unpack
    seed                 cache the originals into models/orig, shape models/mod
    per id               FFXIII_ModelsHD or FFXIII_HDProject, then copy the
                         result out of models/mod and into the unpacked tree
    ff13tool -c          repack, once, not once per category
    copy back            into white_data/sys

Seed and the per id copy live in Setup.bat and GENERATESINGLE's loadcopy=1
branch, not in the tools. Skip them and 859 steps "succeed" installing nothing.

The backup is ours, not theirs. RESTORESINGLE restores from models/orig, which
the mod refills from the live archive when it's missing, so on a modded game
"uninstall" hands the mod back. A copy of both archives taken before anything
runs doesn't have that problem. It costs disk, so the space check asks for
room for both it and the mod's own ~20GB up front.
"""

import json
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from patcher.access import writable_or_reason
from patcher.download import free_space
from patcher.fileops import (CopyCancelled, Held, Stranded, claim, clear_tree, discard,
                             replace_together, stage_copy, temp_sibling, write_atomic)
from patcher.mods.hd_layout import XIII, layout_of
from patcher.mods.hd_recipe import read_category
from patcher.mods.hd_runner import (HARVEST, REVERT, Cancelled, Result, Unsure, plan,
                                    repack_steps, run, seed_steps, unpack_steps)

# the mod's own script says "upwards of 20 GB" for a first install. this is on
# top of the backup, which is why the two are added rather than maxed.
SCRATCH_GB = 20.0
# what a swap into the game keeps free on top of the file itself
SWAP_SLACK = 64 * 1024 * 1024
# size and sha256 of every file in the backup, written as each one is taken
MANIFEST = "backup.json"


class InstallError(Exception):
    pass


class RestoreIncomplete(InstallError):
    """a restore that stopped partway, carrying what did go back"""

    def __init__(self, message: str, restored: list):
        super().__init__(message)
        self.restored = restored


@dataclass
class Prep:
    """what was worked out before anything ran, and what it will cost"""
    variant: str
    archives: list = field(default_factory=list)     # Paths in the game
    # game files a run overwrites that are NOT the scr pair: the image archive
    # and the zone blobs a zone install rewrites. Backed up and restored with
    # the rest, but never copied into the mod folder and never copied back,
    # because the zone steps write to them where they lie.
    extra: list = field(default_factory=list)
    backup_dir: Path | None = None
    needed_bytes: int = 0
    free_bytes: int = 0
    # the backup lives in the undo store beside the install, so usually on the
    # game's drive rather than the mod's. Checking one drive and writing to
    # two is how "I have plenty of space" and "it ran out of space" are both
    # true at once.
    backup_bytes: int = 0
    backup_free: int = 0
    same_drive: bool = False
    # every file that goes into the game is written in full beside the one it
    # replaces and then swapped, so the game's drive needs room for the
    # biggest of them on top of everything else
    swap_bytes: int = 0
    game_free: int = 0
    game_with_backup: bool = True
    game_with_mod: bool = False

    def _budgets(self) -> list:
        """[kind, need, free] per drive, merged where two share one"""
        mod = ["mod", self.needed_bytes, self.free_bytes]
        budgets = [mod]
        backup = mod
        if self.same_drive:
            mod[1] += self.backup_bytes
        else:
            backup = ["backup", self.backup_bytes, self.backup_free]
            budgets.append(backup)
        if self.game_with_backup:
            backup[1] += self.swap_bytes
        elif self.game_with_mod:
            mod[1] += self.swap_bytes
        else:
            budgets.append(["game", self.swap_bytes, self.game_free])
        return budgets

    @property
    def enough_room(self) -> bool:
        return all(free >= need for _, need, free in self._budgets())

    def shortfall(self) -> str:
        """which drive is short and by how much, or "" """
        gb = 1024 ** 3
        for kind, need, free in self._budgets():
            if free >= need:
                continue
            if kind == "mod" and self.same_drive:
                return f"{need / gb:.0f} GB is needed and there is {free / gb:.0f} GB"
            if kind == "mod":
                return f"the mod folder's drive needs {need / gb:.0f} GB and has {free / gb:.0f} GB"
            if kind == "backup":
                return (f"the backup goes to {self.backup_dir}, whose drive needs "
                        f"{need / gb:.0f} GB and has {free / gb:.0f} GB")
            return f"the game's drive needs {need / gb:.0f} GB and has {free / gb:.0f} GB"
        return ""


def find_variant(install_dir: Path) -> str:
    """
    which language archive the game actually loads.

    u is the English set and c the Japanese. With only one on disk that is the
    answer. With both, it is the launcher's voice setting, which is what
    Setup.bat asks too: installing into u for someone playing with Japanese
    voices finishes, reads APPLIED, and changes nothing they can see.
    """
    from patcher.launcher_skip import voice_mode

    present = voice_sets(install_dir)
    if not present:
        layout = layout_of(install_dir)
        archive = layout.chr_archive.split("{")[0]
        raise InstallError(
            f"no {archive} archives under {layout.data}/sys. this does not look like a "
            f"{layout.title} install")
    if len(present) == 1:
        return present[0]
    return "c" if voice_mode(install_dir) == 0 else "u"


def voice_sets(install_dir: Path, whole: bool = False) -> list:
    """
    the voice sets on disk, u for English and c for Japanese, each only with both
    halves of its character pair. `whole` wants its image pair too, a second pair
    per language on XIII and the same one on XIII-2: what the game needs to load
    a set, rather than what an HD install rebuilds in it.
    """
    layout = layout_of(install_dir)

    def there(voice):
        need = layout.pair(install_dir, voice)
        if whole:
            need += [layout.sys_dir(install_dir) / name for name in _pair_names(("img", voice))]
        return all(p.is_file() for p in need)

    return [v for v in ("u", "c") if there(v)]


def voices_with_hd(install_dir: Path) -> set:
    """
    the voice sets the HD Project is in, read off their archives having grown
    past the backup taken before its first install, which is what Revert goes
    by too: HD art is bigger art. Empty with no backup, a game it never went
    into, and after a Revert, which keeps the backup and shrinks the game back.
    """
    from patcher.paths import undo_store_for

    backup = undo_store_for(install_dir) / "hdproject"
    try:
        grown = grown_since_backup(backup, Path(install_dir)) if backup.is_dir() else []
    except OSError:
        return set()
    # a zone blob carries its set after the zone number, white_z0010u_img
    return {(pair_key(name) or ("", name.split("_")[1][-1]))[1] for name in grown}


def archive_pair(install_dir: Path, variant: str) -> list:
    """the index and archive the character pass rebuilds, in that order"""
    return layout_of(install_dir).pair(install_dir, variant)


def prepare(install_dir: Path, mod_root: Path, store: Path, variant: str | None = None) -> Prep:
    """
    everything that can be decided without writing: which archives, how much
    room, where the backup goes. Writes nothing.

    `variant` is a voice set other than the one the launcher is on, which is how
    the voices box builds what a copy can't carry into the other set.
    """
    install_dir, mod_root, store = Path(install_dir), Path(mod_root), Path(store)
    if variant is not None and variant not in voice_sets(install_dir):
        raise InstallError(f"the {variant} voice files aren't all there, so nothing can go into "
                           "them. Nothing has been changed.")
    variant = variant or find_variant(install_dir)
    archives = archive_pair(install_dir, variant)

    for path in archives:
        if not path.is_file():
            raise InstallError(f"{path.name} is missing from {layout_of(install_dir).data}/sys")

    # both ends, before anything, because finding out the game folder is
    # read-only after forty minutes of repacking is the worst possible time.
    # The install directory is the one that matters: on Windows the ACL is
    # inherited from the folder, so this is the same answer as probing every
    # file for the cost of one probe.
    for label, folder in (("the game folder", install_dir), ("the mod folder", mod_root)):
        reason = writable_or_reason(folder)
        if reason:
            raise InstallError(f"{label} cannot be written to: {reason}")

    held = in_use(archives)
    if held:
        raise InstallError(
            f"{', '.join(name for name, _ in held)} cannot be opened for writing, "
            "which usually means the game is running or Steam is verifying it. "
            "Close both and start this again. Nothing has been changed.")

    payload = sum(p.stat().st_size for p in archives)
    backup_dir = store / "hdproject"
    # the store may not exist yet, so ask about the nearest parent that does or
    # free_space gets a path Windows cannot stat
    anchor = backup_dir
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent

    prep = Prep(variant=variant, archives=archives, backup_dir=backup_dir,
                # a copy of the pair in the mod folder, the repacked result
                # alongside it, and room for the extracted tree. The last one
                # is a guess here and survey_extraction replaces it with the
                # real figure out of the index.
                needed_bytes=payload * 2 + int(SCRATCH_GB * 1024 ** 3),
                free_bytes=free_space(mod_root),
                backup_bytes=payload,
                backup_free=free_space(anchor),
                same_drive=_same_drive(mod_root, anchor),
                game_free=free_space(install_dir),
                game_with_backup=_same_drive(install_dir, anchor),
                game_with_mod=_same_drive(install_dir, mod_root))
    return prep


def _same_drive(one: Path, other: Path) -> bool:
    """
    whether two paths share a volume, so their free space is one budget.

    Letter and device both have to match: letter alone merges a folder-mounted
    volume with C:, device alone merges two disks cloned with one serial. A
    wrong answer only moves when running out of room gets noticed, since every
    write is staged. A path that doesn't exist yet asks via its nearest parent.
    """
    def device(path):
        path = Path(path)
        while not path.exists() and path != path.parent:
            path = path.parent
        return path.stat().st_dev

    one, other = Path(one).resolve(strict=False), Path(other).resolve(strict=False)
    if one.drive.lower() != other.drive.lower():
        return False
    try:
        return device(one) == device(other)
    except OSError:
        return True


def environment_notes(mod_root: Path, prep: Prep, unpacked: int) -> list:
    """
    the machine's own contribution, which is where the difference between one
    developer's PC and everybody else's lives. Never fatal on its own.
    """
    from patcher.mods.hd_environment import survey as look_around

    try:
        game = prep.archives[0].parent.parent.parent if prep.archives else mod_root
        return look_around(mod_root, game, unpacked)
    except Exception:
        # a warning that cannot be produced is not a reason to stop an install
        return []


def measure(prep: Prep, mod_root: Path, groups=(), on_line=None, unpacks: bool = True) -> None:
    """
    replaces the free space guess with what the index actually says, and
    refuses outright if the extraction cannot land where it has been pointed.

    Deliberately not fatal when the survey itself fails. A filelist this cannot
    parse is a reason to fall back to the old estimate and let the tools try,
    not a new way for the install to refuse before it has attempted anything.
    """
    from patcher.mods.hd_preflight import MAX_PATH, explain, survey_extraction

    if not unpacks:
        # a zones only run never extracts the scr pair, so none of its cost
        # applies. install() adds what the zones take.
        prep.needed_bytes = 0
        if on_line:
            for note in environment_notes(mod_root, prep, 0):
                on_line(f"[prep] {note.text}", "err" if note.level == "stop" else "out")
        return

    try:
        # ff13tool writes into a folder named after the archive, so that folder
        # is where the path limit is measured from, not the mod folder itself
        survey = survey_extraction(prep.archives[0],
                                   Path(mod_root) / prep.archives[1].name.split(".")[0])
    except Exception as e:
        if on_line:
            # names the file, because this is the one thing here worth sending
            # on if it keeps happening, and says it is harmless, because the
            # install carries on either way
            on_line(f"[prep] could not read {prep.archives[0].name} ahead of time, "
                    "so the size check is an estimate. Not a problem on its own.")
            on_line(f"[prep] {e}")
        return

    complaint = explain(survey, mod_root)
    if complaint:
        raise InstallError(complaint)

    payload = sum(p.stat().st_size for p in prep.archives)
    # the extraction, the two archive copies, and the model caches the seed
    # fills plus what the tools write beside them (most of a GB on a full
    # install)
    cache = survey.cache_bytes(groups)
    prep.needed_bytes = survey.bytes_needed + payload * 2 + cache
    if on_line:
        # the path length only once it's close enough to Windows' limit to matter
        near = survey.longest > MAX_PATH - 60
        on_line(f"[prep] {survey.entries} files, "
                f"{survey.bytes_needed / 1024**3:.1f} GB unpacked, "
                f"{cache / 1024**3:.1f} GB of model caches"
                + (f", longest path {survey.longest} of {MAX_PATH} characters" if near else ""))
        for note in environment_notes(mod_root, prep, survey.bytes_needed):
            on_line(f"[prep] {note.text}", "err" if note.level == "stop" else "out")


def back_up(prep: Prep, on_line=None, cancel=None) -> list:
    """
    a copy of both archives, taken before anything else happens.

    Copied, not journalled, since the mod repacks the whole file and the
    original can't be rebuilt from the result. Everything missing is staged
    first, then the whole backup as it would stand is checked to still go back
    as one game (_apart()), and only then renamed into place. Half a pair is
    only ever finished with the game's copy when that copy belongs with it.
    """
    backup = prep.backup_dir
    backup.mkdir(parents=True, exist_ok=True)
    sources = [source for group in paired([*prep.archives, *prep.extra]) for source in group]
    kept = [source for source in sources if (backup / source.name).exists()]
    for source in kept:
        # exists, full stop. Not "exists and the same size": a modded archive
        # is almost never vanilla's size, so that check would wave through the
        # exact overwrite it's there to stop (the mod's own orig cache mistake).
        # Kept only if it's still the file that was taken, so a damaged backup
        # stops us now instead of on the day Revert needs it.
        problem = backup_problem(prep, source.name)
        if problem:
            raise InstallError(
                f"the backup of {source.name} in {backup} is damaged ({problem}), so Revert "
                "could not be trusted to put your game back. Verify the game files through "
                f"Steam, delete {backup}, and run this again so a clean backup can be taken. "
                "Nothing has been changed.")
        if on_line:
            on_line(f"[backup] {source.name} already saved, keeping the first copy")

    # staged and swapped, so a copy that dies halfway never gets the real name.
    # Under it, "exists" would be enough to keep it forever.
    staged = []
    try:
        for source in sources:
            if source in kept:
                continue
            if on_line:
                on_line(f"[backup] {source.name} ({source.stat().st_size / 1024**3:.1f} GB)")
            temp, digest = stage_copy(source, backup / source.name, cancel=cancel)
            staged.append((temp, backup / source.name, digest))
        known = _manifest(backup)["files"]
        for _, target, digest in staged:
            # on record and gone means it was taken once and moved out since. The
            # game's copy can only stand in for it if it's that very file
            if known.get(target.name, {}).get("sha256") not in (None, digest):
                raise InstallError(
                    f"{target.name} was in the backup in {backup} and isn't any more, and the "
                    "game's copy is not that file, so a Revert from what's left couldn't put "
                    f"your game back. If you renamed or moved {target.name} out of that "
                    f"folder, put it back. Otherwise verify the game files through Steam, "
                    f"delete {backup}, and run this again so a clean backup can be taken. "
                    "Nothing has been changed.")
        view = _archives_in(backup)
        view.update({target.name: temp for temp, target, _ in staged})
        records = dict(known)
        records.update({target.name: {"sha256": digest} for _, target, digest in staged
                        if target.name not in known})
        kind, names, why = _apart(view, records, prep.archives[0].parents[2])
        if kind:
            raise InstallError(_wont_back_up(backup, kind, names, why,
                                             [t.name for _, t, _ in staged]))
        replace_together([(temp, target) for temp, target, _ in staged])
    except CopyCancelled as e:
        for temp, _, _ in staged:
            discard(temp)
        raise Cancelled(str(e)) from None
    except BaseException:
        for temp, _, _ in staged:
            discard(temp)
        raise

    taken = {target.name for _, target, _ in staged}
    for name in sorted(set(view) - taken):
        if name not in _manifest(backup)["files"]:
            # taken by an older build, before there was anything to check it
            # against. It just passed being checked whole, so it is adopted, and
            # that build installed right after taking it. Every one in there,
            # not just this run's, or the next run reads it all through again
            _record(backup, backup / name, _sha256(backup / name), "adopted")
            mark_written(backup)
    for _, target, digest in staged:
        if known.get(target.name, {}).get("sha256") != digest:
            _record(backup, target, digest, "taken")
    # every pair in there was just checked whole, so it says so, and an older
    # build's pair that passed on its bytes isn't read through again next time
    for group in paired(list(view)):
        _pair_up(backup, group)
    return [backup / source.name for source in sources]


def _archives_in(folder: Path) -> dict:
    """name -> path for every archive in a backup folder, by name shape"""
    return {p.name: p for p in Path(folder).iterdir()
            if p.is_file() and (pair_key(p.name) or ZONE_BLOB.match(p.name))}


def _pair_names(key: tuple) -> tuple:
    kind, voice = key
    return ((f"filelist_scr{voice}.win32.bin", f"white_scr{voice}.win32.bin") if kind == "scr"
            else (f"filelist{voice}.win32.bin", f"white_img{voice}.win32.bin"))


def _apart(view: dict, records: dict, game: Path) -> tuple:
    """
    (kind, names, why) for the first thing in a backup that couldn't go back
    as one game, or ("", (), ""). kind is "half", "pair" or "zone". `view` is
    name -> the file that is, or is about to be, in the backup.

    Each half of a pair names the other's hash, and a half whose partner isn't
    there is never talked round. With no partner on record, every compressed
    entry has to read back. Then each zone blob against its index in
    white_img. (A backup with a filelist from after a repack and a white_img
    from before crashed the game right after the logo.)
    """
    from patcher.mods.hd_zones import pair_problem, zone_problem

    halves = {}
    for name in view:
        if pair_key(name):
            halves.setdefault(pair_key(name), []).append(name)
    def said(text):
        # a staged copy has a temp name, and the message is about the real one
        for name, path in view.items():
            text = text.replace(Path(path).name, name)
        return text

    for key, names in sorted(halves.items()):
        index, archive = _pair_names(key)
        if len(names) == 1:
            return "half", (names[0], index if names[0] == archive else archive), ""
        broken = _partner_broken(records, index, archive)
        if broken:
            return "pair", (index, archive), broken
        if broken is None:
            problem = pair_problem(view[index], view[archive], samples=None)
            if problem:
                return "pair", (index, archive), said(problem)

    from patcher.mods.hd_zones import _entries

    sys_dir, read = layout_of(game).sys_dir(game), {}
    for name in sorted(n for n in view if ZONE_BLOB.match(n)):
        listing, image = _pair_names(("img", name.split("_")[1][-1]))
        # the zone's index goes back under the backup's white_img when it holds
        # one, and under the game's when it doesn't
        listing = view.get(listing, sys_dir / listing)
        if listing not in read:
            try:
                read[listing] = _entries(listing)
            except Exception:
                read[listing] = None
        problem = zone_problem(view.get(image, sys_dir / image), listing, view[name],
                               name=name, entries=read[listing])
        if problem:
            return "zone", (image, name), said(problem)
    return "", (), ""


def _partner_broken(records: dict, one: str, other: str) -> str | None:
    """
    why the recorded partner hashes disagree, "" when they agree, or None when
    neither half names a partner and the bytes have to answer instead
    """
    named = [(name, mate) for name, mate in ((one, other), (other, one))
             if (records.get(name) or {}).get("partner")]
    if not named:
        return None
    for name, mate in named:
        if records[name]["partner"] != (records.get(mate) or {}).get("sha256"):
            return f"{mate} is not the one {name} was taken with"
    return ""


def _wont_back_up(backup: Path, kind: str, names: tuple, why: str, taking: list) -> str:
    """the install's refusal for a backup that couldn't go back as one game"""
    redo = (f"verify the game files through Steam, delete {backup}, and run this again so a "
            "clean backup can be taken. Nothing has been changed.")
    if kind == "half":
        have, gone = names
        return (f"the backup in {backup} has {have} without {gone}, so Revert could not put "
                f"that pair back. If you renamed or moved {gone} out of that folder, put it "
                f"back. Otherwise {redo}")
    if kind == "zone":
        image, blob = names
        if image in taking and blob in taking:
            return (f"the game's {blob} doesn't fit the index for it in {image} ({why}), so a "
                    "backup of them couldn't put a working game back. Verify the game files "
                    "through Steam and run this again. Nothing has been changed.")
        if blob in taking:
            return (f"the game's {blob} doesn't fit the index for it in the backup's {image} "
                    f"({why}), so Revert could not put that zone back. To start clean, {redo}")
        moved = (f"If you renamed or moved {image} or its index out of that folder, put them "
                 "back. Otherwise" if image in taking else "To start clean,")
        return (f"the backup in {backup} holds a {blob} that the index for it in {image} "
                f"doesn't fit ({why}), so Revert could not put that zone back. {moved} {redo}")
    finishing = [name for name in names if name in taking]
    if len(finishing) == 1:
        gone = finishing[0]
        other = names[1] if gone == names[0] else names[0]
        return (f"the backup in {backup} has {other} but not {gone}, and the game's {gone} "
                f"and that {other} don't go together ({why}). If you renamed or moved {gone} "
                f"out of that folder, put it back. Otherwise {redo}")
    if len(finishing) == 2:
        return (f"the game's {names[0]} and {names[1]} don't belong together ({why}), so a "
                "backup of them couldn't put a working game back. Verify the game files "
                "through Steam and run this again. Nothing has been changed.")
    return (f"the backup in {backup} holds a {names[0]} and a {names[1]} that were not taken "
            f"together ({why}), so Revert could not put your game back from it. To start "
            f"clean, {redo}")


def partnered(records: dict, one: str, other: str) -> bool:
    """whether the manifest says these two were taken together, each naming the other's hash"""
    first, second = records.get(one) or {}, records.get(other) or {}
    return bool(first.get("partner") and second.get("partner")
                and first["partner"] == second.get("sha256")
                and second["partner"] == first.get("sha256"))


def _pair_up(backup_dir: Path, group: list) -> None:
    """
    each half of a pair that's whole in the backup records the other's hash.
    Only after the pair has been checked whole, so a partner on record always
    means the bytes were read through or matched one that was.
    """
    if len(group) != 2 or pair_key(Path(group[0]).name) is None:
        return
    data = _manifest(backup_dir)
    one, other = (data["files"].get(Path(p).name) for p in group)
    if not one or not other or partnered(data["files"], *(Path(p).name for p in group)):
        return
    one["partner"], other["partner"] = other.get("sha256"), one.get("sha256")
    write_atomic(Path(backup_dir) / MANIFEST, json.dumps(data, indent=2).encode("utf-8"),
                 durable=True)


PAIR = re.compile(r"^(filelist_scr|white_scr|filelist|white_img)([uc])\.win32\.bin$")


def pair_key(name: str) -> tuple | None:
    """("scr", v) or ("img", v) for either half of an index and its archive"""
    found = PAIR.match(name)
    if found is None:
        return None
    return ("scr" if "scr" in found.group(1) else "img"), found.group(2)


def paired(paths) -> list:
    """paths grouped into what only means something together, first seen first"""
    groups, at = [], {}
    for path in paths:
        key = pair_key(Path(path).name)
        if key is None:
            groups.append([path])
        elif key in at:
            groups[at[key]].append(path)
        else:
            at[key] = len(groups)
            groups.append([path])
    return groups


def mark_written(backup_dir: Path) -> bool:
    """
    said right before the first write into the game. restore() only puts the
    backup back once it has been, so a backup that died halfway, or one the
    game has already been put back to, is never copied over whatever the game
    has become since. True when this call is the one that said it, which is
    what makes unmark_written() safe after a swap that went back cleanly.
    """
    data = _manifest(backup_dir)
    if data.get("written"):
        return False
    data["written"] = time.time()
    write_atomic(Path(backup_dir) / MANIFEST, json.dumps(data, indent=2).encode("utf-8"),
                 durable=True)
    return True


def unmark_written(backup_dir: Path) -> None:
    """the swap that mark_written() was said for went back whole, so nothing was written"""
    try:
        data = _manifest(backup_dir)
        if data.pop("written", None) is not None:
            write_atomic(Path(backup_dir) / MANIFEST,
                         json.dumps(data, indent=2).encode("utf-8"), durable=True)
    except OSError:
        pass


def game_written(backup_dir: Path) -> bool:
    data = _manifest(backup_dir)
    if data.get("written"):
        return True
    # archives with no record beside them are an older build's backup, and
    # that build took it right before it installed. No archives at all is a
    # first backup that never finished.
    return not data["files"] and not data.get("retaking") and any(
        pair_key(p.name) or ZONE_BLOB.match(p.name)
        for p in Path(backup_dir).iterdir() if p.is_file())


def _manifest(backup_dir: Path) -> dict:
    try:
        data = json.loads((Path(backup_dir) / MANIFEST).read_text(encoding="utf-8"))
        if isinstance(data.get("files"), dict):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": 1, "files": {}}


def _record(backup_dir: Path, saved: Path, digest: str, how: str) -> None:
    data = _manifest(backup_dir)
    stamp = saved.stat()
    data["files"][saved.name] = {"size": stamp.st_size, "sha256": digest, "how": how,
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # the file's own mtime, so adopting an older build's backup keeps the day
    # it was really taken, which is what Revert orders the other patches by
    taken = data.get("taken")
    data["taken"] = (min(taken, stamp.st_mtime) if isinstance(taken, (int, float))
                     else stamp.st_mtime)
    write_atomic(Path(backup_dir) / MANIFEST, json.dumps(data, indent=2).encode("utf-8"),
                 durable=True)


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def backup_taken_at(backup_dir: Path) -> float | None:
    """when the first file went into this backup, or None if there is none"""
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        return None
    taken = _manifest(backup_dir).get("taken")
    if isinstance(taken, (int, float)):
        return float(taken)
    # an older build's backup: the files themselves are the only clock
    stamps = [p.stat().st_mtime for p in backup_dir.iterdir() if p.is_file()]
    return min(stamps) if stamps else None


def retake(backup_dir: Path, sources: list) -> None:
    """
    game files into the backup again, as one group, for copies the backup has
    that Revert has just taken something off in the game. Staged and swapped
    like back_up(), and unrecorded while the swap happens: a record naming the
    old hash would call the new copy damaged, where an unrecorded file is what
    an interrupted back_up() leaves and gets checked by its structure instead.
    A swap that went back whole gets its old record back with it.
    """
    backup_dir = Path(backup_dir)
    staged, before = [], None
    try:
        for source in sources:
            target = backup_dir / Path(source).name
            temp, digest = stage_copy(Path(source), target)
            staged.append((temp, target, digest))
        data = _manifest(backup_dir)
        before = json.dumps(data, indent=2).encode("utf-8")
        for _, target, _ in staged:
            data["files"].pop(target.name, None)
        # an empty record beside archives reads as an older build's backup the
        # game was written from, and a cut right here must not make it one
        data["retaking"] = [target.name for _, target, _ in staged]
        write_atomic(backup_dir / MANIFEST, json.dumps(data, indent=2).encode("utf-8"),
                     durable=True)
        replace_together([(temp, target) for temp, target, _ in staged])
    except BaseException as e:
        for temp, _, _ in staged:
            discard(temp)
        if before is not None and not isinstance(e, Stranded):
            try:
                write_atomic(backup_dir / MANIFEST, before, durable=True)
            except OSError:
                pass
        raise
    # the clean copies are in either way, and unrecorded they still restore
    try:
        for _, target, digest in staged:
            _record(backup_dir, target, digest, "retaken")
        for group in paired([target for _, target, _ in staged]):
            _pair_up(backup_dir, group)
        data = _manifest(backup_dir)
        data.pop("retaking", None)
        write_atomic(backup_dir / MANIFEST, json.dumps(data, indent=2).encode("utf-8"),
                     durable=True)
    except OSError:
        pass


def backup_problem(prep: Prep, name: str) -> str:
    """
    why the saved copy of `name` cannot be trusted, or "".

    Against the manifest when there is one. An older build's backup has no
    record, so it is held to what the file says about itself: an index has to
    parse and every entry it lists has to fit inside the archive beside it.
    A copy that died halfway fails that; a copy that finished passes.
    """
    saved = Path(prep.backup_dir) / name
    if not saved.is_file():
        return "it is missing"
    known = _manifest(prep.backup_dir)["files"].get(name)
    if known is not None:
        size = saved.stat().st_size
        return "" if size == known.get("size") else \
            f"it is {size:,} bytes and was {known.get('size'):,} when it was taken"
    return _structure_problem(Path(prep.backup_dir), name)


def _structure_problem(folder: Path, name: str) -> str:
    from patcher.mods.hd_zones import check_archive

    # the variant comes off the name, because a backup can hold both
    key = pair_key(name)
    if key is not None:
        kind, variant = key
        index, archive = ((folder / f"filelist_scr{variant}.win32.bin",
                           folder / f"white_scr{variant}.win32.bin") if kind == "scr" else
                          (folder / f"filelist{variant}.win32.bin",
                           folder / f"white_img{variant}.win32.bin"))
        if not archive.is_file() or not index.is_file():
            return ""        # half a pair cannot be checked, and is not evidence of damage
        return check_archive(index, archive)
    if ZONE_BLOB.match(name):
        variant = name.split("_")[1][-1]
        image, listing = folder / f"white_img{variant}.win32.bin", folder / f"filelist{variant}.win32.bin"
        if not image.is_file() or not listing.is_file():
            return ""
        from patcher.archive.filelist import load, parse
        from patcher.mods.hd_zones import read_entry
        zone = name.split("_")[1][:-1]
        try:
            raw = read_entry(image, list(load(listing).entries()),
                             f"zone/filelist_{zone}{variant}.win32.bin")
            inner = list(parse(raw).entries())
        except Exception:
            return ""
        size = (folder / name).stat().st_size
        for entry in inner:
            if not entry.is_stub and entry.byte_offset + entry.stored > size:
                return f"it ends before {entry.path}, which its own index says is inside it"
    return ""


def in_use(paths) -> list:
    """
    files something else holds open for writing, as (name, reason).

    The folder probe answers ACLs and is blind to this: a running game holds
    its archives with a share mode, so a writable folder still hands back
    PermissionError on the file, forty minutes into a repack. Opening for
    update is the cheapest way to ask and changes nothing.
    """
    held = []
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        try:
            with path.open("r+b"):
                pass
        except OSError as e:
            held.append((path.name, str(e)))
    return held


# three digits on XIII (z010), four on XIII-2 (z0010)
ZONE_BLOB = re.compile(r"^white_z\d{3,4}[uc]_img\.win32\.bin$")


def restore_groups(backup_dir: Path, game: Path, variant: str | None = None) -> tuple:
    """
    what the backup holds, pointed back at where each file came from, as
    (groups in the order they go back, names missing their other half).
    `variant` narrows it to one voice set.

    The backup folder is the record, since a Revert has no category list and
    the two voice sets can have been modded at different times. Each scr pair,
    then each image pair, then every zone blob: an index has to go back before
    its blobs, since a vanilla index over an HD blob still reads vanilla data.
    Matched by name shape, so nothing else in the undo store gets filed into
    the game as an archive.
    """
    saved = {p.name for p in Path(backup_dir).iterdir() if p.is_file()}
    layout = layout_of(game)
    sys_dir, zone_dir = layout.sys_dir(game), layout.zone_dir(game)
    voices = variant or "uc"
    groups, halves = [], []
    for index, archive in (("filelist_scr{}.win32.bin", "white_scr{}.win32.bin"),
                           ("filelist{}.win32.bin", "white_img{}.win32.bin")):
        for voice in voices:
            names = [index.format(voice), archive.format(voice)]
            have = [name for name in names if name in saved]
            if len(have) == 2:
                groups.append([sys_dir / name for name in names])
            elif have:
                halves += have
    groups += [[zone_dir / name] for name in sorted(saved)
               if ZONE_BLOB.match(name) and name.split("_")[1][-1] in voices]
    return groups, halves


def restore_prep(install_dir: Path, store: Path) -> Prep:
    """
    what restore() needs, without prepare()'s demand that the game be whole:
    a game missing an archive after a swap that could not be undone is exactly
    the one that needs putting back
    """
    return Prep(variant="", archives=archive_pair(install_dir, "u"),
                backup_dir=Path(store) / "hdproject")


def restore(prep: Prep, on_line=None, variant: str | None = None) -> list:
    """
    puts the archives back from the backup taken before the first install.

    Everything's checked before anything moves: every hash in the backup's
    manifest, and nothing in the game held open. Each group then goes back
    staged in full and hashed again, so a bad copy never replaces anything.
    See restore_groups() for order.

    Revert leaves a backup alone if the game hasn't been written from it
    since, so it doesn't undo a Steam update. `variant` is the rebuild's way
    in: that voice set goes back to vanilla whoever changed it, the other
    stays.
    """
    backup = prep.backup_dir
    if backup is None or not backup.is_dir():
        raise InstallError("there is no backup to restore from")
    with exclusive(backup.parent):
        return _restore(prep, backup, on_line, variant)


def _restore(prep: Prep, backup: Path, on_line, variant: str | None) -> list:
    if variant is None and not game_written(backup):
        return []

    game = prep.archives[0].parents[2]
    # the advice has to lead somewhere: the backup stays marked as written
    # until it's gone, so a verify alone leaves every later Revert refusing
    clean = (f"Nothing has been changed. To get back to a clean game, verify the game files "
             f"through Steam, delete {backup}, then run {layout_of(game).revert} to take off "
             "the rest.")
    known, here = _manifest(backup)["files"], _archives_in(backup)
    # on record and gone means it was taken and moved out since, and putting
    # the rest back would leave the game's copy of it as it is, mod and all
    gone = sorted(name for name in known if name not in here
                  and (pair_key(name) or ZONE_BLOB.match(name)))
    if gone:
        raise InstallError(
            f"the backup was taken with {gone[0]} in it and it isn't in {backup} any more, so "
            "putting the rest back would leave the game's copy as it is. If you renamed or "
            f"moved it, put it back and run this again. {clean}")

    groups, halves = restore_groups(backup, game, variant)
    if variant is not None and not groups and not halves:
        return []           # this voice set was never backed up, so never written
    if halves:
        index, archive = _pair_names(pair_key(halves[0]))
        gone = index if halves[0] == archive else archive
        raise InstallError(
            f"the backup has {halves[0]} without {gone}, so putting it back would break the "
            f"game rather than fix it. If you renamed or moved {gone} out of {backup}, put it "
            f"back and run this again. {clean}")
    if not groups:
        raise InstallError(
            f"the backup in {backup} holds nothing that goes back into the game. If you moved "
            f"its files out, put them back and run this again. {clean}")

    targets = [t for group in groups for t in group]
    held = in_use(targets)
    if held:
        raise InstallError(
            f"{', '.join(name for name, _ in held)} cannot be opened for writing, which "
            "usually means the game is running or Steam is verifying it. Close both and "
            "try again. Nothing has been changed.")

    for target in targets:
        problem = backup_problem(prep, target.name)
        if not problem and target.name in known:
            if on_line:
                on_line(f"[restore] checking the backup of {target.name}")
            if _sha256(backup / target.name) != known[target.name].get("sha256"):
                problem = "its contents are not what was recorded when it was taken"
        if problem:
            raise InstallError(
                f"the backup of {target.name} is damaged ({problem}), so putting it back "
                f"would break the game rather than fix it. {clean}")

    # every file is the one that was taken, which says nothing about whether
    # the two halves of a pair were taken together, or a zone blob with the
    # index for it. An older build would finish half a pair from the game.
    # The whole backup, not just what goes back now: a rebuild puts its own
    # voice set back and then backs up again, which refuses on anything in
    # here that doesn't hold together, by which time the install is gone
    kind, names, why = _apart(here, known, game)
    if kind == "half":
        raise InstallError(
            f"the backup has {names[0]} without {names[1]}, so it can't be put back whole. If "
            f"you renamed or moved {names[1]} out of {backup}, put it back and run this "
            f"again. {clean}")
    if kind == "zone":
        raise InstallError(
            f"the backup's {names[1]} doesn't fit the index for it in {names[0]} ({why}), so "
            f"putting it back would break that zone rather than fix it. {clean}")
    if kind:
        raise InstallError(
            f"the backup's {names[0]} and {names[1]} were not taken together ({why}), so "
            f"putting them back would break the game rather than fix it. {clean}")

    # the worst single group, because each one frees its old files before the next
    need = max(sum((backup / t.name).stat().st_size for t in group) for group in groups)
    free = free_space(targets[0].parent)
    if free < need + SWAP_SLACK:
        raise InstallError(
            f"putting the backup back safely needs {(need + SWAP_SLACK) / 1024 ** 3:.1f} GB "
            f"free on the game's drive and there is {free / 1024 ** 3:.1f} GB. Nothing has "
            "been changed.")

    put_back, digests = [], {}
    for group in groups:
        try:
            digests.update(_restore_group(prep, group, known, on_line))
        except (InstallError, OSError) as e:
            if not put_back and not isinstance(e, Stranded):
                if isinstance(e, InstallError):
                    raise
                raise InstallError(f"{e}. Nothing has been changed.") from e
            done = (", ".join(t.name for t in put_back) + " went back, then "
                    if put_back else "")
            left = ", ".join(t.name for g in groups[groups.index(group):] for t in g)
            what = stranded(e) if isinstance(e, Stranded) else f"({e})"
            raise RestoreIncomplete(
                f"{done}{group[0].name} could not go back. {what} Still not put back: "
                f"{left}. Run this again once whatever held it is closed.", put_back) from e
        put_back += group
    # a rebuild that happened to cover everything the backup holds has put the
    # game back as fully as Revert would, and the mark has to say so, or a
    # hand install after it is taken back by the next Revert
    everything, broken = restore_groups(backup, prep.archives[0].parents[2])
    if variant is None or (len(everything) == len(groups) and not broken):
        _settle_backup(backup, digests)
    return put_back


def _restore_group(prep: Prep, group: list, known: dict, on_line=None) -> dict:
    need = sum((prep.backup_dir / t.name).stat().st_size for t in group) + SWAP_SLACK
    free = free_space(group[0].parent)
    if free < need:
        raise InstallError(f"putting {group[0].name} back safely needs {need / 1024 ** 3:.1f} GB "
                           f"free on the game's drive and there is {free / 1024 ** 3:.1f} GB")
    staged, digests = [], {}
    try:
        for target in group:
            if on_line:
                on_line(f"[restore] {target.name}")
            expect = (known.get(target.name) or {}).get("sha256")
            temp, digests[target.name] = stage_copy(prep.backup_dir / target.name, target,
                                                    expect=expect)
            staged.append((temp, target))
        replace_together(staged)
    except BaseException:
        for temp, _ in staged:
            discard(temp)
        raise
    # a .prev beside one of these is the game's copy from a swap that could
    # not be undone, and the backup has just replaced it
    for target in group:
        discard(temp_sibling(target, "prev"))
    return digests


def _settle_backup(backup_dir: Path, digests: dict) -> None:
    """
    the game matches the backup again, so it is no longer something to put
    back. Best effort: every file is already back by now, and a record that
    stays behind costs one more copy of identical bytes, where an error here
    would report a finished restore as a failed one.
    """
    try:
        data = _manifest(backup_dir)
        for name, digest in digests.items():
            if name not in data["files"]:
                # an older build's file, hashed on the way back in anyway, so
                # the next look at it has something to hold it to
                saved = Path(backup_dir) / name
                data["files"][name] = {"size": saved.stat().st_size, "sha256": digest,
                                       "how": "adopted",
                                       "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        data.pop("written", None)
        # what XIII-2's runs recorded they wrote, which is not in the game any more
        data.pop("categories", None)
        write_atomic(Path(backup_dir) / MANIFEST, json.dumps(data, indent=2).encode("utf-8"),
                     durable=True)
    except OSError:
        pass


def modded_before(install_dir: Path) -> str:
    """
    what a restore just put back still carries, or "". A first install over a
    game that already had the mod in from outside takes that game as the
    backup, so back to how it was isn't vanilla
    """
    from patcher.mods import hd_vanilla
    from patcher.mods.hd_state import without_file_mods

    layout, count = layout_of(install_dir), 0
    try:
        for voice in voice_sets(install_dir):
            # what the Mods drawer put in comes off with its own Remove
            grown = hd_vanilla.grown(without_file_mods(layout.pair(install_dir, voice)[0]),
                                     layout)
            count = max(count, len({p.rsplit(".win32.", 1)[0] for p in grown}))
    except Exception:
        return ""
    if not count:
        return ""
    return (f"{count} character models in it were already modded before this app's first "
            "install, so they're still in, and Steam's Verify Integrity of Game Files is the way "
            "to vanilla from here")


def grown_since_backup(backup_dir: Path, game: Path) -> list:
    """
    the backed up files the game now holds a bigger copy of.

    Bigger, not different. HD art is bigger art, where a voice pack taken off
    since, or a backup that was itself of a modded game, only ever reads
    smaller or missing, and flagging those would say "not clean" on every
    Revert forever with nothing the user could do about it.
    """
    groups, _ = restore_groups(backup_dir, game)
    return [t.name for group in groups for t in group
            if t.is_file() and t.stat().st_size > (Path(backup_dir) / t.name).stat().st_size]


def suspect_backup(mod_root: Path, prep: Prep) -> str:
    """
    why the backup about to be taken might not be of a vanilla game, or "".

    Our backup is only as vanilla as the game was when it was taken. Someone
    who installed the mod by hand first would back up modded archives and
    Revert would put those back forever. A populated models\\orig with no
    backup of ours beside it is the tell: their own batch probes that folder
    to decide whether this is a first install.
    """
    if not prep.archives or prep.backup_dir is None:
        return ""
    # a backup of ours already exists, so whatever it holds was decided on an
    # earlier run and saying anything now is too late to be useful. Except a
    # zone backed up for the first time on this run, which is still taken of
    # whatever the game holds now, hand installs included.
    if any((prep.backup_dir / p.name).exists() for p in prep.archives):
        fresh = {p.name for p in prep.extra if not (prep.backup_dir / p.name).exists()}
        try:
            from patcher.mods.hd_preflight import zones_already_modded
            game = prep.archives[0].parent.parent.parent
            zones = [n for n, _, _ in zones_already_modded(game, prep.variant) if n in fresh]
        except Exception:
            zones = []
        if not zones:
            return ""
        names = ", ".join(sorted(n.split("_")[1] for n in zones))
        return (f"{len(zones)} of the zone archives about to be backed up for the first time "
                f"are not the size a vanilla copy ships them at ({names}), so something "
                "other than this app has modded them. Revert would put back what is there "
                "now. For a clean starting point, verify the game files through Steam first.")

    # the archive itself, measured. Their Setup.bat carries the vanilla byte
    # sizes of the six main character models and this reads the same numbers
    # out of the index, so it is evidence rather than an inference about what
    # some folder implies.
    try:
        from patcher.mods.hd_preflight import already_modded
        changed = already_modded(prep.archives[0])
    except Exception:
        changed = []
    if changed:
        names = ", ".join(sorted({p.split("/")[2] for p, _, _ in changed}))
        return (f"The character models in your game are not the size a vanilla copy "
                f"ships them at ({names}), so something has already modded this install. "
                "The backup taken now is what Revert would put back, so it would put back "
                "a modded game. If you want a clean starting point, verify the game files "
                "through Steam first and then come back.")

    # and the same question for the zones, which are twenty separate files on
    # disk rather than entries in an index. Their Setup.bat checks these too.
    try:
        from patcher.mods.hd_preflight import zones_already_modded
        game = prep.archives[0].parent.parent.parent
        zones = zones_already_modded(game, prep.variant)
    except Exception:
        zones = []
    if zones:
        names = ", ".join(sorted(n.split("_")[1] for n, _, _ in zones))
        return (f"{len(zones)} of the zone archives in your game are not the size a "
                f"vanilla copy ships them at ({names}), so something has already modded "
                "this install. The backup taken now is what Revert would put back, so it "
                "would put back a modded game. If you want a clean starting point, verify "
                "the game files through Steam first and then come back.")

    # a copy into the other voice set has no mod folder, only the archives
    if mod_root is None:
        return ""
    cache = Path(mod_root) / "models" / "orig"
    if not cache.is_dir() or (cache / CACHE_MARK).is_file() or not any(cache.rglob("*")):
        return ""

    return ("This mod has been installed from this folder before, by its own installer. "
            "The archives in your game may already carry it, and the backup taken now is "
            "what Revert would put back, so it would put back a modded game. If you want "
            "a clean starting point, verify the game files through Steam first and then "
            "come back.")


# left in models\\orig by our installs. Their installer fills that cache too, and
# the one our own seed fills out of the backup read as theirs whenever the
# backup was gone: after a Verify and the recovery steps, every first install
# warned that the mod had been installed by hand.
CACHE_MARK = "patcher.txt"


def mark_cache(mod_root: Path) -> None:
    """
    says this folder's originals cache is ours from here on. Called once the
    backup is taken, because that is what the seed fills it from. The archives
    are still measured on every first backup; this only answers the folder.
    """
    cache = Path(mod_root) / "models" / "orig"
    try:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / CACHE_MARK).write_text(
            "ArmyOfOne installs from this folder. It fills models\\orig "
            "out of its own backup of the game, so this cache isn't a sign the mod's "
            "installer ran here.\n", encoding="utf-8")
    except OSError:
        # costs a spurious warning on some later first backup, nothing more
        pass


# XIII-2's zone pairs under zone\\, the cached originals and the working copies
ZONE_COPY = re.compile(r"^(filelist_z\d{4}[a-z]|white_z\d{4}[a-z]_img)(_orig)?\.win32\.bin$",
                       re.I)


def clear_caches(mod_root: Path, on_line=None, layout=XIII) -> int:
    """
    what a finished run leaves in the mod folder that no later run reads.
    Returns bytes freed.

    models\\mod goes (it's in the game and rebuilt next run). XIII keeps
    chr\\pc and chr\\npc like its own cleanup does, since other mods read
    them; XIII-2's .bat empties all of it. Zone caches go, they're copies of
    our backup (XIII's own cleanup means to delete its 4.49GB too, but its
    glob misses every zone). models\\orig\\chr stays, it's shared with HD
    Models Plus, and so does anything in zone\\ not named the way a run names
    it.
    """
    from patcher.mods.hd2_recipe import ZONE

    mod_root, freed = Path(mod_root), 0
    mod = mod_root / "models" / "mod"
    if layout is XIII:
        gone = _all_but(mod, {mod / "chr" / "pc", mod / "chr" / "npc"})
        orig = mod_root / "models" / "orig"
        gone += sorted(p for p in orig.glob("zone*") if p.is_dir()) if orig.is_dir() else []
    else:
        gone = [mod]
        if (mod_root / "zone").is_dir():
            for entry in sorted((mod_root / "zone").iterdir()):
                if entry.is_dir() and ZONE.match(entry.name):
                    gone += [entry / "orig", entry / "mod"]
                elif entry.is_file() and ZONE_COPY.match(entry.name):
                    gone.append(entry)
    for target in gone:
        if not target.exists() or _through_link(mod_root, target):
            continue
        size = _weight(target)
        if target.is_dir():
            clear_tree(target)
        else:
            discard(target)
        if target.exists():
            # a file somebody has open. It goes with the next run's cleanup
            if on_line:
                on_line(f"[clean] could not remove all of {target.relative_to(mod_root)}")
        else:
            freed += size
    for folder in {t.parent for t in gone if t.parent != mod_root}:
        try:
            while folder != mod_root and not _through_link(mod_root, folder) \
                    and folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
                folder = folder.parent
        except OSError:
            pass
    if on_line and freed:
        # a weapons run's outputs are megabytes, and "0.0 GB" reads as nothing
        said = (f"{freed / 1024 ** 3:.1f} GB" if freed >= 1024 ** 3
                else f"{max(1, round(freed / 1024 ** 2))} MB")
        on_line(f"[clean] freed {said} of working copies in the mod folder")
    return freed


def _all_but(folder: Path, kept: set) -> list:
    """what's under folder less the kept subtrees, in as few paths as covers it"""
    if _linked(folder) or not folder.is_dir():
        return []
    out = []
    for entry in sorted(folder.iterdir()):
        if entry in kept or _linked(entry):
            continue
        if any(k.is_relative_to(entry) for k in kept):
            out += _all_but(entry, kept)
        else:
            out.append(entry)
    return out


def _linked(path: Path) -> bool:
    # a junction is a link wearing another reparse tag, and is_junction is 3.12's
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _through_link(mod_root: Path, target: Path) -> bool:
    """a link anywhere between the mod folder and target, whose far side isn't ours to empty"""
    return any(_linked(step) for step in (target, *target.parents)
               if step != mod_root and step.is_relative_to(mod_root))


def _weight(target: Path) -> int:
    try:
        if target.is_dir():
            return sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
        return target.stat().st_size
    except OSError:
        return 0


def clear_leftovers(mod_root: Path, variant: str, on_line=None, layout=XIII) -> int:
    """
    the extracted tree and the working copies of the archives. Returns bytes
    freed.

    Before a run, because a cancelled run's tree is half modified and
    `ff13tool -x` overwrites without emptying, so stale files would make it
    into the repack. After, because it's ~1.8GB in someone's Downloads, but
    only once the game has the result, since on a failure the tree is the
    evidence. models\\orig\\chr stays; anything more is clear_caches()'s call.
    """
    mod_root = Path(mod_root)
    archive = layout.chr_archive.format(variant)
    targets = [mod_root / archive.split(".")[0],
               mod_root / archive,
               mod_root / layout.chr_index.format(variant)]

    freed = 0
    for target in targets:
        if not target.exists():
            continue
        try:
            if target.is_dir():
                freed += sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
                shutil.rmtree(target)
            else:
                freed += target.stat().st_size
                target.unlink()
        except OSError as e:
            # a locked file is a reason to leave it, not to fail an install
            # that has already put the archives back in the game
            if on_line:
                on_line(f"[clean] could not remove {target.name}: {e}")

    if freed and on_line:
        on_line(f"[clean] freed {freed / 1024**3:.1f} GB of working files")
    return freed


def members_for(data_dir: Path, categories: list, selected) -> list:
    """the (type, id) pairs every selected category covers, in manifest order"""
    data_dir = Path(data_dir)
    chosen, seen = [], set()
    for category in categories:
        if category["id"] not in selected:
            continue
        listing = data_dir / category["list"]
        if not listing.is_file():
            continue
        for member in read_category(listing).members:
            if member not in seen:
                seen.add(member)
                chosen.append(member)
    return chosen


def zones_wanted(data_dir: Path, categories: list, selected) -> list:
    """the zone ids the selected categories name, or []"""
    from patcher.mods.hd_zones import zone_ids

    chosen = [c for c in categories if c["id"] in selected]
    return zone_ids(members_for(data_dir, chosen, {c["id"] for c in chosen}))


def zone_categories(data_dir: Path, categories: list) -> set:
    """the ids of the categories whose lists name zones"""
    return {c["id"] for c in categories if zones_wanted(data_dir, [c], {c["id"]})}


def build_plan(install_dir: Path, mod_root: Path, data_dir: Path, categories: list,
               selected, variant: str, originals: Path | None = None, leave=()) -> list:
    """
    unpack, then every selected id, then one repack, then the zone groundwork.

    A category that contributes no steps is refused by name instead of
    silently waited through. Zones can't be planned up front, since their
    block count is inside per zone archives that have to be unpacked first, so
    the groundwork goes on the end and install() plans the blocks after.
    `leave` is ids unbuildable() found nothing clean for; all of them is
    nothing_clean().
    """
    from patcher.mods.hd_zones import prepare_steps, zone_ids

    chosen = [c for c in categories if c["id"] in selected]
    if not chosen:
        raise InstallError("nothing selected that this can install")

    unsupported = [c["label"] for c in chosen if c.get("supported") is False]
    if unsupported:
        raise InstallError(
            f"{', '.join(unsupported)} cannot be installed from here yet, so nothing has "
            "been changed. Untick it, or use the mod's own installer for that part.")

    leave = {i.lower() for i in leave}
    steps = []
    empty = []
    zones = []
    for category in chosen:
        members = members_for(data_dir, [category], {category["id"]})
        produced = plan(data_dir, [m for m in members if (m[1] or "").lower() not in leave],
                        variant)
        every = zone_ids(members)
        mine = [z for z in every if z not in leave]
        left = any((m[1] or "").lower() in leave for m in members) or len(mine) < len(every)
        if not produced and not mine and not left:
            empty.append(category["label"])
        steps += produced
        zones += [z for z in mine if z not in zones]

    if empty:
        raise InstallError(
            f"{', '.join(empty)} would install nothing, so nothing has been changed. "
            "This is a bug rather than something you did; please report it.")
    if leave and not steps and not zones:
        raise nothing_clean(leave, install_dir)

    # one id can appear in more than one category, and running its tools twice
    # is wasted minutes rather than a wrong result
    seen, unique = set(), []
    for step in steps:
        key = (step.tool, tuple(str(a) for a in step.args))
        if key not in seen:
            seen.add(key)
            unique.append(step)

    # the seed goes between the unpack and the first tool, or every tool says
    # "Unable to open the .trb file". Narrowed to the groups this plan touches,
    # read off the arguments so the two can't disagree.
    groups = {step.args[1].split("\\")[3] for step in unique
              if step.tool == "inject" and len(step.args) > 1}
    groups |= {source.split("\\")[3] for step in unique
               for source, _ in step.copies if source.count("\\") > 3}

    archive = []
    if unique:
        archive = (unpack_steps(variant) + seed_steps(variant, groups, source=originals)
                   + unique + repack_steps(variant))
    return archive + prepare_steps(install_dir, variant, zones, source=originals)


def verify_rebuilt(index: Path, archive: Path, current: Path) -> str:
    """
    why a repacked pair is not fit to go into the game, or "".

    ff13tool's exit code means nothing and the repack step only proves the
    file changed, so this is the last look before it replaces the game's
    copy: every file the game's index lists is still listed, nothing points
    past the end, and the tail of the archive really inflates.
    """
    from patcher.mods.hd_zones import _entries, check_archive

    try:
        before = _entries(current)
    except Exception:
        before = None
    return check_archive(index, archive, since=before)


def unbuildable(steps: list, prep: Prep, mod_root: Path) -> set:
    """
    the ids a plan would build with nothing clean to build them from.

    Every HD file is built from the original in models\\orig, so a model the
    game already has modded, with no backup and no clean cache, would be HD
    over HD (a .bat install, a copied game, an overlapping category). Installs
    plan again without them and only refuse when that leaves nothing.
    """
    from patcher.mods import hd_vanilla
    from patcher.mods.hd_layout import layout_of

    layout = layout_of(prep.archives[0].parents[2])
    paths = hd_vanilla.planned(steps)
    if not paths or not hd_vanilla.known(layout):
        return set()
    try:
        live = hd_vanilla.index_sizes(prep.archives[0])
        kept = None if prep.backup_dir is None else prep.backup_dir / prep.archives[0].name
        backup = hd_vanilla.index_sizes(kept) if kept is not None and kept.is_file() else None
    except Exception:
        # the archive checks further on have more to say about an index that won't read
        return set()
    return {stem.rsplit("/", 1)[-1] for stem in
            hd_vanilla.stuck(paths, live, backup, Path(mod_root) / "models" / "orig", layout)}


def zones_unbuildable(prep: Prep, zones) -> list:
    """
    the zones whose blob the harvest would read isn't the size Steam ships it
    at: the backup's, or the game's before a first one. The harvest copies it
    again over any other size it finds cached, so that blob is the original
    the tools get
    """
    from patcher.mods import hd_vanilla
    from patcher.mods.hd_layout import layout_of

    game = prep.archives[0].parents[2]
    layout, out = layout_of(game), []
    for zone in zones:
        name = f"white_{zone}{prep.variant}_img.win32.bin"
        clean = hd_vanilla.zone_size(layout, name)
        kept = None if prep.backup_dir is None else prep.backup_dir / name
        source = kept if kept is not None and kept.is_file() else layout.zone_dir(game) / name
        if clean is not None and source.is_file() and source.stat().st_size != clean:
            out.append(zone)
    return out


def _named(ids) -> str:
    names = sorted(ids)
    return ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")


def left_out(planned: list, steps: list, dropped: set) -> list:
    """
    the payload steps a plan lost to its `leave`, for Result.missed: those ids
    keep whatever they had, so their categories aren't this build. Not one a
    Mods drawer mod holds, which is the same file in both voice sets
    """
    from patcher.mods import hd_vanilla

    def ids(step):
        return {hit[1] for hit in map(hd_vanilla.parse_path, hd_vanilla.planned([step])) if hit}

    return [s for s in planned if s.payload and s not in steps and not ids(s) & dropped]


def dropped_in(install_dir, steps) -> set:
    """
    the ids a plan would build that a mod from the Mods drawer holds. Left as
    they are like anything else already modded: the costume is what somebody
    put in on purpose, and Remove in the drawer is how it makes way for HD
    """
    from patcher.mods import hd_vanilla
    from patcher.mods.game_files import holding

    ids = {hit[1] for hit in map(hd_vanilla.parse_path, hd_vanilla.planned(steps)) if hit}
    try:
        return set().union(*holding(install_dir, ids).values()) if ids else set()
    except Exception:
        return set()


def _dropped_in(install_dir, ids) -> tuple:
    """(the ids a mod from the Mods drawer holds, what to say about them)"""
    from patcher.mods.game_files import holding

    try:
        held = holding(install_dir, ids) if install_dir is not None else {}
    except Exception:
        held = {}
    ours = set().union(*held.values()) if held else set()
    if not ours:
        return set(), ""
    names = sorted(held)
    return ours, (f"{_named(ours)} {'is' if len(ours) == 1 else 'are'} "
                  f"{' and '.join(names)} from the Mods drawer, so left as "
                  f"{'it is' if len(ours) == 1 else 'they are'}. Remove "
                  f"{'it' if len(names) == 1 else 'them'} there to have "
                  f"{'it' if len(ours) == 1 else 'them'} built HD")


def left_as_they_are(ids, install_dir=None) -> str:
    ours, said = _dropped_in(install_dir, ids)
    rest = set(ids) - ours
    if not rest:
        return f"[plan] {said}"
    return (f"[plan] {_named(rest)} already modded in your game, with no vanilla copy here to "
            "build from, so left as they are rather than built HD over HD. Steam's Verify puts "
            "them back to vanilla if you want them built" + (f". {said}" if said else ""))


def nothing_clean(ids, install_dir=None) -> InstallError:
    ours, said = _dropped_in(install_dir, ids)
    if ours == set(ids):
        return InstallError(f"everything ticked is a mod you put in from the Mods drawer: "
                            f"{said}. Nothing has been changed.")
    return InstallError(
        f"everything ticked is already modded in your game ({_named(ids)}), and there's no "
        "vanilla copy of it here to build from: nothing in models\\orig, and no backup of ours "
        "from before it went in. Building over it would put HD art on HD art. Verify the game "
        "files through Steam, then install it. Nothing has been changed.")


def outside(prep: Prep, categories: list, applied) -> str:
    """
    what to say before a first backup of a game that already has some of the
    mod in, or "". That backup is what Revert puts back, so whatever it holds
    came from outside this app and stays. `applied()` reads the game.
    """
    if prep.backup_dir is None or any((prep.backup_dir / p.name).exists() for p in prep.archives):
        return ""
    try:
        state = applied()
    except Exception:
        return ""
    if state is None or not state.verified or not state.categories:
        return ""
    names = [c.get("label") or c["id"] for c in categories if c["id"] in state.categories]
    what = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return (f"{what} {'is' if len(names) == 1 else 'are'} already in your game from outside "
            "this app. That stays as it is: the backup taken now is what Revert puts back, so "
            "Revert takes off what this app installs and leaves that in. Steam's Verify is the "
            "way back to vanilla.")


UNCHANGED = re.compile(r",? so nothing has been changed(?=\.)|\s*Nothing has been changed\.")


def after_changes(message: str) -> str:
    """
    a refusal's message for a run that had already changed something, which is
    a rebuild whose restore went first. Its "nothing has been changed" was only
    ever about itself, and so_far() says the rest.
    """
    return UNCHANGED.sub("", message).strip()


def so_far(landed: list, revert: str = REVERT) -> str:
    """the part of a message that says what an interrupted run already changed"""
    def said(items):
        what = items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"
        return f"{what[0].upper()}{what[1:]}"

    whole = [str(x) for x in landed if not isinstance(x, Unsure)]
    torn = [str(x) for x in landed if isinstance(x, Unsure)]
    if not landed:
        return "Nothing in the game has been changed."
    parts = [f"{said(whole)} already went into the game, complete."] if whole else []
    if torn:
        # never "nothing else was changed" beside a failure that could not
        # prove it undid itself
        parts.append(f"{said(torn)} may be half in the game, so put the game back "
                     f"with {revert} before playing.")
    else:
        parts.append("Nothing else was changed.")
    return " ".join(parts)


def _moved_beside(source: Path, target: Path) -> Path | None:
    """
    the rebuilt file renamed in beside the one it replaces, flushed first, when
    the mod folder and the game share a drive, or None to copy it instead. A
    rename where a copy is gigabytes, and the rebuilt copy is only ever cleared
    up after this anyway
    """
    import os

    if not _same_drive(source.parent, target.parent):
        return None
    temp = temp_sibling(target, "part")
    try:
        # ff13tool closed it without a flush, and the swap is the step that
        # has to find all of it on disk
        with source.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(source, temp)
    except OSError:
        # a mount that only looks like the same drive, or something holding it
        return None
    return temp


def _copy_back(prep: Prep, mod_root: Path, result: Result, on_line=None,
               on_share=None, cancel=None) -> None:
    """
    the rebuilt scr pair into the game, as a pair. Checked, both written in
    full beside the game's copies, then swapped together, since a copy
    straight over the top truncates the archive the moment it starts.
    """
    revert = layout_of(prep.archives[0].parents[2]).revert
    rebuilt = [Path(mod_root) / target.name for target in prep.archives]
    for path in rebuilt:
        if not path.is_file():
            raise InstallError(f"{path.name} was not rebuilt, so nothing was copied back. "
                               + so_far(result.landed, revert))
    problem = verify_rebuilt(rebuilt[0], rebuilt[1], prep.archives[0])
    if problem:
        raise InstallError(f"the rebuilt archive failed its read-back ({problem}), so it was "
                           "not copied into the game. " + so_far(result.landed, revert))

    need = sum(p.stat().st_size for p in rebuilt) + SWAP_SLACK
    free = free_space(prep.archives[0].parent)
    if free < need:
        raise InstallError(
            f"copying the rebuilt archives in safely needs {need / 1024 ** 3:.1f} GB free on "
            f"the game's drive and there is {free / 1024 ** 3:.1f} GB. "
            + so_far(result.landed, revert) + " Free some up and run the install again.")

    staged = []
    try:
        for source, target in zip(rebuilt, prep.archives):
            # a rename has no chunks for a Stop to land between, so it's asked here
            if cancel is not None and cancel.is_set():
                raise CopyCancelled(f"stopped before {target.name} went back into the game")
            temp = _moved_beside(source, target)
            if temp is not None:
                if on_line:
                    on_line(f"[copy] {target.name} moved into the game, same drive")
            else:
                if on_line:
                    on_line(f"[copy] {target.name} back into the game")
                temp, _ = stage_copy(source, target, cancel=cancel, on_share=on_share)
            staged.append((temp, target))
        # after the staging, which Stop and a full disk can still end with the
        # game untouched, and before the first byte of it moves
        try:
            marked = mark_written(prep.backup_dir)
        except OSError as e:
            raise InstallError(f"the backup's record in {prep.backup_dir} could not be updated "
                               f"({e}), and Revert goes by it, so nothing was copied back. "
                               + so_far(result.landed, revert)) from e
        try:
            replace_together(staged)
        except OSError as e:
            if marked and not isinstance(e, Stranded):
                unmark_written(prep.backup_dir)
            raise
    except CopyCancelled as e:
        for temp, _ in staged:
            discard(temp)
        raise Cancelled(str(e)) from None
    except InstallError:
        for temp, _ in staged:
            discard(temp)
        raise
    except Stranded as e:
        result.landed.append(Unsure("the character archives"))
        raise InstallError(stranded(e) + " " + so_far(result.landed, revert)) from e
    except OSError as e:
        for temp, _ in staged:
            discard(temp)
        raise InstallError(
            f"the rebuilt archives could not be written into the game ({e}). This usually "
            "means the game is running. " + so_far(result.landed, revert)) from e
    result.landed.append("the character archives")


def stranded(e: Stranded) -> str:
    """what a swap that could not be taken back left where, said plainly"""
    where = [f"the game's own copy of {Path(target).name} is beside it as {Path(prev).name}"
             if prev else f"{Path(target).name} was not there before and could not be "
             "taken back out" for target, prev in e.left]
    return (f"A swap into the game failed and could not be taken back ({e}): "
            f"{'; '.join(where)}. Something else is holding those files, so close the "
            "game, Steam and anything scanning the folder.")


def zone_paths(prep: Prep, mod_root: Path, zones) -> str:
    """
    the MAX_PATH check for the zone archives, or "".

    They extract two folders deeper than the scr pair and were never measured
    at all, so a mod folder that passed the scr check could still die partway
    through a zone with nothing useful on screen.
    """
    from patcher.mods.hd_preflight import explain, survey_zone_paths

    game = prep.archives[0].parents[2]
    sys_dir = game / "white_data" / "sys"
    try:
        survey = survey_zone_paths(sys_dir / f"white_img{prep.variant}.win32.bin",
                                   sys_dir / f"filelist{prep.variant}.win32.bin",
                                   prep.variant, zones, mod_root)
    except Exception:
        return ""
    return explain(survey, mod_root)


@contextmanager
def exclusive(store: Path):
    """fileops.claim() for an HD run, refused the way an install says things"""
    try:
        held = claim(store)
    except Held:
        raise InstallError(
            "something else is working on this game's archives right now, a patch being "
            "applied or another copy of the patcher. Let it finish, then try again. Nothing "
            "has been changed.") from None
    with held:
        yield


def install(install_dir: Path, mod_root: Path, data_dir: Path, support: Path,
            categories: list, selected, store: Path,
            on_line=None, on_step=None, cancel=None, skip_space_check=False,
            on_share=None, landed: list | None = None, variant: str | None = None) -> Result:
    """
    the whole thing, in the order that makes a failure survivable: the backup
    before any byte of the game is touched, then the characters all the way
    in, then each zone whole or not at all, so a failed zone leaves the
    characters alone. `landed` is the caller's list, filled as things reach
    the game, so it still says what changed when this raises.
    """
    with exclusive(store):
        return _install(install_dir, mod_root, data_dir, support, categories, selected,
                        store, on_line=on_line, on_step=on_step, cancel=cancel,
                        skip_space_check=skip_space_check, on_share=on_share, landed=landed,
                        variant=variant)


def _install(install_dir: Path, mod_root: Path, data_dir: Path, support: Path,
             categories: list, selected, store: Path, on_line=None, on_step=None,
             cancel=None, skip_space_check=False, on_share=None,
             landed: list | None = None, variant: str | None = None) -> Result:
    from patcher.mods.hd_zones import plan_for, zone_archives

    install_dir, mod_root = Path(install_dir), Path(mod_root)
    prep = prepare(install_dir, mod_root, store, variant)
    result = Result()
    if landed is not None:
        result.landed = landed

    # the plan comes first, because what it costs depends on which groups it
    # touches: a weapons only install caches 74MB of models where a full one
    # caches 1.2GB. build_plan writes nothing, so nothing is committed by
    # working that out before asking about space.
    steps = build_plan(install_dir, mod_root, data_dir, categories, selected, prep.variant,
                       originals=prep.backup_dir)
    zones = zones_wanted(data_dir, categories, selected)
    dropped = dropped_in(install_dir, steps)
    leave = unbuildable(steps, prep, mod_root) | set(zones_unbuildable(prep, zones)) | dropped
    if leave:
        planned = steps
        steps = build_plan(install_dir, mod_root, data_dir, categories, selected, prep.variant,
                           originals=prep.backup_dir, leave=leave)
        result.missed += left_out(planned, steps, dropped)
        result.gaps += [f"zone {z}" for z in zones if z in leave]
        zones = [z for z in zones if z not in leave]
        if on_line:
            on_line(left_as_they_are(leave, install_dir), "note")
    seeded = next((s.args[1:] for s in steps if s.tool is None and s.action == "seed"), ())
    # the tools work on a copy inside the mod folder, which is what every
    # relative path in the plan is written against. A zones only install never
    # opens it, so it neither copies it nor pays for it.
    unpacks = any(s.tool == "pack" and s.args[0] == "-x" and "scr" in str(s.args[2])
                  for s in steps)
    measure(prep, mod_root, groups=seeded, on_line=on_line, unpacks=unpacks)
    if unpacks:
        # the rebuilt pair is swapped in beside the game's own, so at the very
        # least it needs the room the current one takes
        prep.swap_bytes = sum(p.stat().st_size for p in prep.archives) + SWAP_SLACK

    # a zone install writes to the game where it lies: the blob is replaced on
    # disk and the index is imported straight into white_img<v>. Neither is the
    # scr pair, so neither is in the backup we already take, and without this
    # Revert would put back a game still carrying half a zone install.
    if zones:
        prep.extra = zone_archives(install_dir, prep.variant, zones)
        prep.backup_bytes += sum(p.stat().st_size for p in prep.extra)

        # the scr pair was asked about in prepare(); these are the other files
        # a zone run writes to, and a zone writes to the game while it runs
        # rather than at the end, so finding out late is worse here than
        # anywhere else in the app
        held = in_use(prep.extra)
        if held:
            raise InstallError(
                f"{', '.join(name for name, _ in held)} cannot be opened for writing, "
                "which usually means the game is running or Steam is verifying it. "
                "Close both and start this again. Nothing has been changed.")

        # and what the mod folder holds while it works: the cache, the cached
        # bg subtrees, and one extraction and working copy at a time. The
        # working copy grows as HD pixels go in, so three times the blobs is
        # deliberately generous; refusing at the start beats failing at 90%.
        blobs = [p.stat().st_size for p in prep.extra if p.name.startswith("white_z")]
        prep.needed_bytes += sum(blobs) * 3
        # zones go in one at a time, so the game's drive needs the biggest one
        prep.swap_bytes = max(prep.swap_bytes, max(blobs, default=0) + SWAP_SLACK)

        too_long = zone_paths(prep, mod_root, zones)
        if too_long:
            raise InstallError(too_long)

    if not prep.enough_room and not skip_space_check:
        raise InstallError(
            f"there is not enough room: {prep.shortfall()}. Free some up, or move the "
            "mod folder to a drive with more space, and start it again. Nothing has "
            "been changed.")

    if on_line:
        voices = {"u": "English", "c": "Japanese"}.get(prep.variant, prep.variant)
        on_line(f"[prep] {len(steps)} steps, into the {voices} voice set")
        # which builds of the tools this run trusts, for the run's log: the
        # terminal has no use for a hash
        for note in tool_notes(support):
            on_line(f"[prep] {note}", "log")
        from patcher.mods.hd_state import applied_categories, zones_installed

        def there():
            state = applied_categories(prep.archives[0], None, data_dir, categories,
                                       mod_root=mod_root)
            state.categories |= {c["id"] for c in categories if zones_installed(
                install_dir, prep.variant, prep.backup_dir, data_dir, c)}
            return state

        known = outside(prep, categories, there)
        doubt = known or suspect_backup(mod_root, prep)
        if doubt:
            # loud, because it is the only moment it can be said. After this
            # the backup exists and whatever it holds is what Revert means.
            on_line(f"[backup] {doubt}", "note" if known else "err")
    clear_leftovers(mod_root, prep.variant, on_line=on_line)
    back_up(prep, on_line=on_line, cancel=cancel)
    mark_cache(mod_root)

    if unpacks:
        for source in prep.archives:
            if on_line:
                on_line(f"[copy] {source.name} into the mod folder")
            shutil.copyfile(source, mod_root / source.name)

    # the groundwork for the zones reads out of the backup, which has to exist
    # first, and has nothing to do with the characters, so it waits for them
    cut = next((i for i, s in enumerate(steps) if s.action == HARVEST), len(steps))
    characters, groundwork = steps[:cut], steps[cut:]

    if characters:
        run(characters, mod_root, support, on_line=on_line, on_step=on_step,
            cancel=cancel, on_share=on_share, total=len(steps), into=result)
        if not result.ok:
            return result
        _copy_back(prep, mod_root, result, on_line=on_line, on_share=on_share, cancel=cancel)

    # the second pass, and the reason there is one: a zone's blocks live inside
    # its own archive, so how many there are is not knowable until the steps
    # above have unpacked it. The counters carry over so the bar and the
    # tallies read as one run rather than two.
    if zones:
        run(groundwork, mod_root, support, on_line=on_line, on_step=on_step,
            cancel=cancel, on_share=on_share, start=cut, total=len(steps), into=result)
        if not result.ok:
            return result
        blocks = plan_for(mod_root, install_dir, data_dir, prep.variant, zones,
                          undo=Path(store) / "hdproject-commit", backup=prep.backup_dir)
        if not blocks:
            raise InstallError(
                "the zone archives unpacked and there were no backgrounds in any of "
                "them, so there is nothing to install. " + so_far(result.landed))
        if on_line:
            on_line(f"[zones] {len(blocks)} steps across {len(zones)} zones")
        run(blocks, mod_root, support, on_line=on_line, on_step=on_step,
            cancel=cancel, on_share=on_share,
            start=len(steps), total=len(steps) + len(blocks), into=result)
        if not result.ok:
            return result

    # only once the game has the result. A failure keeps everything, because
    # the extracted tree is the evidence and deleting it is the last thing
    # anybody wants while working out what went wrong.
    clear_leftovers(mod_root, prep.variant, on_line=on_line)
    clear_caches(mod_root, on_line=on_line)
    return result


def tool_notes(support: Path) -> list:
    """
    which build of each tool this run is about to trust.

    Their exit codes and the four words an import prints were learned from one
    version of each, so a log from a run that went strange should say whether
    the tools were the ones that were learned from.
    """
    import hashlib

    from patcher.mods.hd_runner import TOOLS

    notes = []
    for name in TOOLS.values():
        path = Path(support) / name
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            continue
        notes.append(f"{name} sha256 {digest}")
    return notes
