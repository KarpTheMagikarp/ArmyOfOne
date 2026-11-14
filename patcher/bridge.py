"""
the layer index.html talks to. Everything crossing it is plain json, since
pywebview marshals through json and a Path or dataclass comes out as null.

Kept out of shell.py so it tests with no window and no pywebview. Also where
ui_job_id ("laa") gets joined to patch id ("flip_laa_flag") via the manifests;
a job with no patch behind it reports unsupported instead of success.
"""

import fnmatch
import shutil
from pathlib import Path
from typing import Callable

from patcher.access import writable_or_reason, write_failure
from patcher.download import Cancelled
from patcher.app import detect_steam_library_paths
from patcher.engine import PatchStatus, run_patches, supersede_handler
from patcher.fileops import clear_tree, scratch_near
from patcher.novafix_config import ADDON_DIR_NAMES, resolve_addons_dir
from patcher.install_resolver import remember_path, resolve_install_dir, validate_install_dir
from patcher.manifest import GameManifest, load_manifest
from patcher.mods import nexus_auth
from patcher.ingest import IngestError, extract_member, scan
from patcher.mods.nexus_source import NexusSource
from patcher.paths import (discover_manifests, nexus_config_path, state_path_for,
                           undo_store_for)
from patcher.state import (applied_recorder, load_state, record_applied,
                           record_reader, save_state, scan_patch_status)
from patcher.updater import default_fetch

# the engine's vocabulary is wider than the UI's. the UI only has two row
# states to render with, so anything that did not actually change a file has
# to collapse into a single honest "did not happen" bucket rather than
# borrowing the done state.
OK_STATUSES = {PatchStatus.APPLIED, PatchStatus.ALREADY_APPLIED, PatchStatus.WOULD_APPLY}

# outcomes that are not failures but are not finished either: the run did what
# it could and is now waiting on the user. worth separating from FAILED so the
# UI can say "press the button on the mod page" instead of "something broke".
WAITING_STATUSES = {
    PatchStatus.NEEDS_WEBSITE, PatchStatus.NEEDS_ACCOUNT,
    PatchStatus.NEEDS_INSTALLER, PatchStatus.MANUAL, PatchStatus.BLOCKED,
}

UNSUPPORTED = "unsupported"


def _voices_said(sets) -> str:
    sets = sorted(set(sets))
    if sets == ["c", "u"]:
        return "both voice sets"
    if len(sets) == 1:
        return f"the {'English' if sets[0] == 'u' else 'Japanese'} voice set"
    return "the game's archives"


def _common(paths) -> str:
    """the folder every one of these paths is in"""
    import posixpath

    return posixpath.commonpath([p.rsplit("/", 1)[0] for p in paths]) or "the game's folder"


def _over(install_dir: Path, record: dict) -> list:
    """the drawer's records over one on any of its files, oldest first"""
    from patcher.mods import game_files

    rid, above = game_files._rid(record), []
    for spec in record["pairs"]:
        for item in spec["entries"]:
            chain = game_files.stacked(install_dir, spec["index"], item["path"])
            ids = [game_files._rid(r) for r in chain]
            for other in chain[ids.index(rid) + 1:] if rid in ids else ():
                if all(game_files._rid(o) != game_files._rid(other) for o in above):
                    above.append(other)
    return sorted(above, key=lambda r: (r.get("at", 0), Path(r["_file"]).name))


def _beside(found, members) -> float:
    """
    what putting files beside the exe costs, in game_files' units: each one
    read out of the drop, written and synced on its own, and read back
    """
    from patcher.mods.game_files import FILE

    return sum(3 * found.sizes.get(member, 0) + 4 * FILE for member in members)


def _files_said(done: dict, name: str) -> str:
    """what putting a mod's files in did, in a line"""
    files = done.get("files", 0)
    if not files and done.get("already"):
        return f"{name} is already in, every file as it was."
    if not files and not done.get("adopted"):
        return f"Every file in {name} is what the game has already, so nothing changed."
    said = (f"{'Updated' if done.get('updated') else 'Installed'} {name}: {files} game "
            f"file{'' if files == 1 else 's'} into {_voices_said(done.get('sets', []))}")
    if done.get("adopted"):
        said += f", {done['adopted']} carried over from the last version"
    if done.get("same"):
        said += f". {done['same']} matched the game already and were left alone"
    kept = done.get("kept") or []
    if kept:
        said += (f". It doesn't have {' or '.join(kept)}, so the last version's "
                 f"{'stays' if len(kept) == 1 else 'stay'} in to keep the model whole")
    return said + "." + _crossed_said(done.get("crossed"), "the old version")


def _loader_said(done: dict, name: str, exe) -> str:
    """what putting a mod beside the game's exe did, in a line"""
    if not done.get("ok"):
        return done.get("error") or f"{name} didn't go in beside the game's exe."
    # another mod by the name already in, so this one got a number
    name = done.get("name") or name
    beside = exe.name if exe is not None else "the game's exe"
    bits = list(done.get("dlls") or [])
    folders = done.get("folders") or []
    if folders:
        bits.append(f"{len(folders)} folder{'' if len(folders) == 1 else 's'}")
    what = " and ".join([", ".join(bits[:-1]), bits[-1]] if len(bits) > 1 else bits) \
        or f"{done.get('files', 0)} files"
    said = (f"{'Updated' if done.get('updated') else 'Installed'} {name} beside {beside}: "
            f"{what}, {done.get('files', 0)} files in all.")
    kept = done.get("kept") or []
    if kept:
        said += (f" Your {', '.join(k.rsplit('/', 1)[-1] for k in kept)} stayed as you had it, "
                 "with the new one beside it as .new.")
    return said


def _codes_said(done: dict, name: str) -> str:
    """what putting a mod's Nova Chrysalia code patches in did, in a line"""
    if not done.get("ok"):
        return done.get("error") or f"{name}'s code patches didn't go in."
    count = done.get("count", 0)
    return (f"{'Updated' if done.get('updated') else 'Put in'} its {count} Nova Chrysalia code "
            f"patch{'' if count == 1 else 'es'}, which NovaFix applies in memory as the game "
            "starts. Nothing on disk is changed, and Nova isn't needed.")


def _disk_said(done: dict, name: str, alone: bool) -> str:
    """what putting a mod's loose files in the game's folder did, in a line"""
    if not done.get("ok"):
        return done.get("error") or f"{name}'s files didn't go in the game's folder."
    over, added = len(done.get("replaced") or []), len(done.get("added") or [])
    files = done.get("files", 0)
    count = f"{files} file{'' if files == 1 else 's'}"
    kept = "which is" if over == 1 else "which are"
    if over and added:
        said = (f"{count} in the game's folder, {over} over the game's own, {kept} kept to "
                f"put back, and {added} it didn't have")
    elif over:
        said = f"{count} in the game's folder, over the game's own, {kept} kept to put back"
    else:
        said = f"{count} the game didn't have, into its folder"
    return f"Installed {name}: {said}." if alone else f"And {said}."


def _played_said(played) -> str:
    """the line saying a mod's own .bat was what put it in"""
    bat = played.bat.replace("\\", "/").rsplit("/", 1)[-1]
    each = " once for each voice set" if len(played.languages) > 1 else ""
    # what the copy's PC was changed to, for a script that wanted it some way
    tried = played.tried
    how = "" if not tried else " (" + (tried[0] if len(tried) == 1 else
                                       ", ".join(tried[:-1]) + " and " + tried[-1]) + ")"
    return (f"Played its installer, {bat}, on a copy of the game{each}{how}, and put in what it "
            "changed.")


def _skipped_said(played) -> str:
    """what a played .bat tried that's nothing the game's files hold, in a line"""
    things = played.skipped
    if not things:
        return ""
    listed = things[0] if len(things) == 1 else ", ".join(things[:-1]) + " and " + things[-1]
    return f"It also tried to {listed}, which was skipped."


def _records_said(changed: dict) -> str:
    """what a drop's records inside the game's containers came to, in a line"""
    count = sum(len(inside) for inside in changed.values())
    where = " and ".join(sorted({path.rsplit("/", 1)[-1] for path in changed}))
    return (f"Its {count} record{'' if count == 1 else 's'} inside {where} went into the game's "
            "copy as it is now, so what other mods changed in it stays.")


def _back_said(rebuilt: dict) -> list:
    """the mods a version taken off from under them had to lift, as each went back on"""
    return [f"{other} went back on under it." if problem is None else
            f"{other} had to come off for it and couldn't go back on ({problem}), so drop it "
            "again to put it back." for other, problem in rebuilt["back"]]


def _rebuilt_said(rebuilt: dict) -> str:
    """for an install that failed after the version that was in came off for it"""
    lines = ["The version that was in came off first, so drop that one again to have it back."] \
        if rebuilt["took"] else []
    lines += [f"{other} went back on without it." if problem is None else
              f"{other} had to come off for it and couldn't go back on ({problem}), so drop it "
              "again to put it back." for other, problem in rebuilt["back"]]
    return "".join(f" {line}" for line in lines)


def _summed(shown: str, done, updated: bool, whole: bool, alone: bool = True) -> str:
    """
    the drawer's one line about an install. The whole account still goes with
    it, for whoever opens it, but "Installed X" is what most people came for
    """
    files = (done or {}).get("files", 0)
    if done is not None and alone and not files and done.get("already"):
        said = f"{shown} is already in."
    elif done is not None and alone and not files and not done.get("adopted"):
        said = f"{shown} is what the game has already."
    else:
        said = f"{'Updated' if updated else 'Installed'} {shown}."
    return said if whole else f"{said} Some of it didn't go in."


def _reshade_said(done: dict, exe) -> str:
    """what putting ReShade in beside NovaFix did, in a line"""
    if not done.get("ok"):
        return done.get("error") or "ReShade didn't go in."
    beside = exe.name if exe is not None else "the game's exe"
    said = (f"{'Updated' if done.get('updated') else 'Installed'} ReShade in a ReShade folder "
            f"beside {beside}, where NovaFix loads it from: {done.get('files', 0)} files in all.")
    kept = done.get("kept") or []
    if kept:
        said += (f" Your {', '.join(k.rsplit('/', 1)[-1] for k in kept)} stayed as you had it, "
                 "with the new one beside it as .new.")
    return f"{said} {done['warn']}" if done.get("warn") else said


def _crossed_said(crossed, under: str) -> str:
    """mods passed over on the way down a stack, a repack having dropped their files"""
    if not crossed:
        return ""
    one = len(crossed) == 1
    it = "it" if one else "them"
    return (f" {' and '.join(crossed)} {'was' if one else 'were'} under {under}, and the HD "
            f"Project's repack had dropped {'its' if one else 'their'} files there, so "
            f"{'it' if one else 'they'} came off too. Drop {it} on the drawer again to put {it} "
            "back.")


def _file_mod_said(record: dict, seen: dict) -> str:
    """the line under a game-file mod in the drawer's list"""
    from patcher.mods.game_files import SPOKEN

    paths = {i["path"] for spec in record.get("pairs", []) for i in spec.get("entries", [])}
    # the letter the index's name ends in, which LR spells its own way
    sets = {SPOKEN.get(spec["index"].rsplit("/", 1)[-1].split(".")[0][-1], "")
            for spec in record.get("pairs", [])}
    files = f"{len(paths)} game file{'' if len(paths) == 1 else 's'}"
    if record.get("records") and paths <= set(record["records"]):
        # one of Surihix's LR patchers: the container its records went into
        files = " and ".join(sorted({p.rsplit("/", 1)[-1] for p in record["records"]}))
    word = seen.get("state")
    if record.get("off"):
        return ("came back when the HD Project was restored from a backup. Remove takes it "
                "off again")
    if word == "out":
        return "not in the game any more, so Remove just clears it off this list"
    if word == "unknown":
        return f"{files}. Couldn't check whether they're still in"
    said = f"{files}, only partly in now" if word == "partly" else \
        f"{files} in {_voices_said(sets)}"
    # one built as a version, HD Models Plus's, says which
    if record.get("version"):
        said = f"{record['version']}, {said}"
    if record.get("dlc"):
        said += f", and {len(record['dlc'])} DLC files"
    if seen.get("under"):
        some = "all" if seen.get("covered") == seen.get("files") else "some"
        said += f", {some} of them covered by {' and '.join(seen['under'])}"
    return said


def _listed_names(names) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _tagged(name: str, origin: str = "") -> str:
    """
    the version a mod's name carries, or the download it came from, as the
    Installed list shows it beside the title: the record's name has lost it
    since names lost Nexus's tail
    """
    import re

    from patcher.mods.game_files import ARCHIVES
    from patcher.mods.titles import version

    leaf = re.split(r"[\\/]", str(origin or ""))[-1]
    leaf = next((leaf[:-len(x)] for x in ARCHIVES if leaf.lower().endswith(x)), leaf)
    said = version(name) or (version(leaf) if leaf else "")
    return f"v{said}" if said else ""


class UiBridge:
    """
    holds the loaded manifests and answers the two questions the UI has:
    where is this game, and please run these jobs.

    manifest_paths and fetch are injectable for the same reason they are
    everywhere else in this project: so tests never need a steam install or
    a network.
    """

    def __init__(self, manifest_paths: dict[str, Path] | None = None, fetch=default_fetch,
                 steam_library_paths: list[Path] | None = None, log=None):
        paths = discover_manifests() if manifest_paths is None else manifest_paths
        self._manifests: dict[str, GameManifest] = {}
        self._fetch = fetch
        self._steam_library_paths = steam_library_paths
        self._log = log
        # a FOMOD's answers so far, by the download they're for
        self._fomods = {}
        for path in paths.values():
            manifest = load_manifest(path)
            if manifest.ui_id:
                self._manifests[manifest.ui_id] = manifest

    def _libraries(self) -> list[Path]:
        if self._steam_library_paths is not None:
            return self._steam_library_paths
        return detect_steam_library_paths()

    def _patch_for_job(self, manifest: GameManifest, ui_job_id: str):
        return next((p for p in manifest.patches if p.ui_job_id == ui_job_id), None)

    def download_filter(self, ui_game_id: str, ui_job_id: str) -> dict:
        """
        what a download has to look like to be this job's. Unknown job, game or
        manifest gets archives with no size floor and never .exe, since taking
        whatever landed in Downloads is how a GPU driver once got installed
        into Final Fantasy.
        """
        from patcher.download_watch import ARCHIVE_SUFFIXES, INSTALLER_SUFFIXES

        suffixes = list(ARCHIVE_SUFFIXES)
        min_bytes = None
        names, nexus, sizes, want, skip = [], None, {}, 1, []

        manifest = self._manifests.get(ui_game_id)
        patch = self._patch_for_job(manifest, ui_job_id) if manifest else None
        if patch is not None:
            if patch.runs_installer:
                suffixes += list(INSTALLER_SUFFIXES)
            if patch.estimated_size_gb:
                # a tenth of the estimate. The HD packs are the only patches
                # carrying a size and also the only ones whose download runs
                # long enough for something unrelated to land mid-wait, which
                # is exactly the case worth catching. A tenth still clears any
                # plausible multi-part split of a 6GB pack.
                min_bytes = int(patch.estimated_size_gb * (1024 ** 3) * 0.10)
            # a Nexus download carries its mod's id in its name, so a watch for
            # one mod leaves another mod's download alone. Not an HD Project's,
            # whose floor keeps anything else out already, and whose name the
            # id can't be picked out of for certain: XIII-2's is mod 1, which
            # every version 1 of anything has in its name too
            if patch.type == "nexus" and patch.nexus_mod_id and min_bytes is None:
                nexus = patch.nexus_mod_id
            names, sizes = list(patch.download_names), dict(patch.download_sizes)
            want = max(1, len(patch.recipes))
            if want > 1:
                # a row with one of its files in already waits on the rest, and
                # passes over another download of the one that's in, which is
                # what "download both" gets from someone with one in
                try:
                    got = self._parts_in(ui_game_id, self.file_mods(ui_game_id))
                    want = max(1, want - got.get(ui_job_id, [0])[0])
                    skip = self._downloads_in(ui_game_id, patch)
                except Exception:
                    pass

        return {"suffixes": suffixes, "minBytes": min_bytes, "names": names, "nexus": nexus,
                "sizes": sizes, "want": want, "skip": skip}

    def _downloads_in(self, ui_game_id: str, patch) -> list:
        """the downloads a row's mods went in from, by name, for its watch to pass over"""
        from patcher.mods import game_files

        install_dir = self.install_dir(ui_game_id)
        if install_dir is None:
            return []
        recipes = {r.lower() for r in patch.recipes}
        everything = game_files.records(undo_store_for(install_dir))
        return sorted({Path(str(r["source"])).name for r in everything
                       if str(r.get("recipe", "")).lower() in recipes and r.get("source")
                       and not r.get("off")})

    def nexus_sizes(self, ui_game_id: str, ui_job_id: str) -> dict:
        """
        file name -> bytes for a Nexus row's files, off Nexus itself when a key
        has been saved, which is what a download's percentage is out of. {}
        with no key, which is most people, and then it's the bytes so far
        """
        from patcher.mods.nexus_api import file_sizes
        from patcher.mods.nxm_handler import load_api_key

        manifest = self._manifests.get(ui_game_id)
        patch = self._patch_for_job(manifest, ui_job_id) if manifest else None
        if patch is None or patch.type != "nexus" or not patch.nexus_mod_id:
            return {}
        try:
            key = load_api_key(nexus_config_path())
        except Exception:
            return {}
        if not key:
            return {}
        return file_sizes(key, patch.nexus_game_domain or "", patch.nexus_mod_id)

    def _selection_key(self, patch_id: str) -> str:
        return f"categories:{patch_id}"

    def hd_job(self, ui_game_id: str) -> str | None:
        """this game's HD mod, as a job id: the one row that has categories"""
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return None
        return next((p.ui_job_id for p in manifest.patches if p.categories), None)

    def hd_categories(self, ui_game_id: str, ui_job_id: str) -> dict:
        """
        the category choices this job offers and what's ticked now. The mod's
        installer asks these as a cmd menu tree but they're independent toggles,
        so they come back flat. A job with none returns [] instead of an error.
        """
        manifest = self._manifests.get(ui_game_id)
        patch = self._patch_for_job(manifest, ui_job_id) if manifest else None
        if patch is None or not patch.categories:
            return {"categories": [], "selected": [], "installed": []}

        state = load_state(state_path_for(manifest.game_id))
        recorded = state.get(self._selection_key(patch.id)) or {}
        installed = [c for c in recorded.get("installed", [])
                     if any(x["id"] == c for x in patch.categories)]
        selected = recorded.get("selected")
        if selected is None:
            # first look: the manifest's defaults, which leave the character
            # models off
            selected = [c["id"] for c in patch.categories if c.get("default")]

        known = {c["id"] for c in patch.categories}
        sets = recorded.get("sets") if isinstance(recorded.get("sets"), dict) else {}
        sets = {v: [c for c in ids if c in known] for v, ids in sets.items()
                if v in ("u", "c") and isinstance(ids, list)}
        # the voices can change outside the app too, through Square Enix's
        # launcher or by hand, and then `installed` is about the other set
        live = self._live_variant(ui_game_id)
        if live in sets:
            installed = sets[live]
        return {
            "categories": [dict(c) for c in patch.categories],
            "selected": [c for c in selected
                         if any(x["id"] == c for x in patch.categories)],
            "installed": installed,
            # per voice set, for when the archives can't be asked: the voices
            # row with the mod's folder gone
            "sets": sets,
            # the other voice set's box, which stays off until somebody ticks it
            "voices": recorded.get("voices_too") is True,
        }

    def hd_survey(self, ui_game_id: str, ui_job_id: str, mod_root: str) -> dict:
        """
        what each category costs and which are already in the game.

        Sizes come from the game's index and the mod's lists; what's applied
        comes from diffing that index against our vanilla backup, so it holds
        up after a Steam verify or a game copied from another PC. verified
        False means nothing to compare against, which isn't the same as
        nothing installed. Best effort: on any error the panel just gets less.
        """
        from pathlib import Path

        answer = {"sizes": {}, "applied": [], "verified": False, "suspect": {}}
        try:
            from patcher.mods.hd_install import archive_pair, find_variant
            from patcher.mods.hd_preflight import category_sizes
            from patcher.mods.hd_recipe import detect
            from patcher.mods.hd_state import applied_categories, covered_categories
            from patcher.mods.titles import title
            from patcher.paths import undo_store_for

            manifest = self._manifests.get(ui_game_id)
            patch = self._patch_for_job(manifest, ui_job_id) if manifest else None
            if patch is None or not patch.categories:
                return answer

            # where the game is, not scan()'s full status: on XIII-2 that reads
            # the index for Leviathan's Tears, and the survey reads it anyway
            install_dir = self.install_dir(ui_game_id)
            if install_dir is None:
                return answer
            # the set the game loads, which is the one this reads
            answer["variant"] = find_variant(Path(install_dir))

            from patcher.mods.hd_games import for_job
            game = for_job(ui_job_id)
            if game is not None and game.job_id != "hdproject":
                shape = game.detect(Path(mod_root))
                if shape is None:
                    return answer
                answer.update(game.survey(Path(install_dir), shape, patch.categories))
                answer["voices"] = self._voices_box(ui_game_id, Path(install_dir), game, shape,
                                                    patch.categories, answer["variant"],
                                                    answer["sizes"], answer["applied"])
                return answer

            shape = detect(Path(mod_root))
            if shape is None:
                return answer

            install_dir = Path(install_dir)
            data = Path(shape["data"])
            from patcher.mods.hd_preflight import zone_sizes
            from patcher.mods.hd_state import zones_installed

            variant = find_variant(install_dir)
            index, _ = archive_pair(install_dir, variant)
            # two sources, because a zone's art is not in the scr archive. Its
            # own blob is loose on disk and its size is the real cost.
            answer["sizes"] = {**category_sizes(index, data, patch.categories),
                               **zone_sizes(install_dir, variant, data, patch.categories)}

            store = undo_store_for(install_dir) / "hdproject"
            vanilla = store / index.name
            # without a backup the shipped vanilla sizes answer, so a copy that
            # came over from another PC, or got the mod from its own .bat,
            # badges what it has instead of falling back on a record of nothing
            state = applied_categories(index, vanilla if vanilla.is_file() else None, data,
                                       patch.categories, mod_root=Path(shape["root"]))
            if state.verified:
                found = set(state.categories)
                found |= {c["id"] for c in patch.categories
                          if zones_installed(install_dir, variant, store, data, c)}
                answer["applied"] = sorted(found)
                answer["verified"] = True
                # HD Models Plus standing in for Playable Characters. Its own
                # key, so the voice box and the debts keep going by what the HD
                # Project itself put in, and its own try: it only decorates
                try:
                    answer["covered"] = {
                        cid: [title(name) for name in names]
                        for cid, names in covered_categories(
                            index, vanilla if vanilla.is_file() else None, data,
                            patch.categories, mod_root=Path(shape["root"]),
                            applied=found).items()}
                except Exception:
                    if self._log is not None:
                        self._log.exception("couldn't tell what the Mods drawer covers")
                answer["suspect"] = self._hd_suspect(
                    ui_game_id, install_dir, variant, store, data, patch, sorted(found))
            if game is not None:
                answer["voices"] = self._voices_box(ui_game_id, install_dir, game, shape,
                                                    patch.categories, variant, answer["sizes"],
                                                    answer["applied"])
        except Exception:
            pass
        return answer

    def _voices_box(self, ui_game_id, install_dir, game, found, categories, variant, sizes,
                    applied):
        """
        the other voice set's box in the HD panel, or None on a game with one
        set: what that set has, what it's behind on, and what each category
        would cost it (the art a copy appends, plus the backup of each file it
        writes the first time). Best effort like the rest of the survey.
        """
        from patcher.mods import hd_mirror
        from patcher.mods.hd_install import voice_sets

        try:
            other = "c" if variant == "u" else "u"
            if variant not in ("u", "c") or other not in voice_sets(install_dir, whole=True):
                return None
            box = {"language": hd_mirror.LANGUAGE[other], "has": [], "owed": [], "art": {},
                   "parts": {}, "backup": {}}
            state = game.applied_in(install_dir, found, categories, other)
            if state is not None and state.verified:
                box["has"] = sorted(state.categories)
            here = set(applied or [])
            owed = hd_mirror.owed(install_dir, variant, other)
            owed = here if owed == hd_mirror.EVERYTHING else set(owed or ())
            # a tools build into it that stopped is only in the record, the
            # copy before it having finished, and a copy alone still finishes it
            box["owed"] = sorted(here & (owed | self.hd_owed(ui_game_id, other)))
            files = {}
            for category in categories:
                cid = category["id"]
                fresh = game.touched(found, categories, [cid])
                zones = game.zones_of(found, categories, [cid])
                mine = hd_mirror.writes(install_dir, other, bool(fresh and fresh.paths), zones)
                box["parts"][cid] = [p.name for p in mine]
                files.update((p.name, p) for p in mine)
                if cid in here:
                    box["art"][cid] = hd_mirror.weigh(install_dir, variant, fresh, zones)
                elif sizes.get(cid):
                    box["art"][cid] = sizes[cid]
            box["backup"] = hd_mirror.unbacked(install_dir, files.values())
            return box
        except Exception:
            if self._log is not None:
                self._log.exception("couldn't size up the other voice set")
            return None

    def _hd_suspect(self, ui_game_id, install_dir, variant, store, data, patch, applied) -> dict:
        """
        installed categories there's reason to think came out wrong, id -> why.

        The stamp says the code that wrote it has been fixed since (a dict
        lookup). The pair check reads the archives, which also catches a run
        that died between two steps. Only runs for what's installed. Best
        effort: saying nothing beats saying it wrong.
        """
        from patcher.mods.hd_state import half_written_zones, stale_pipelines

        suspect = {}
        try:
            manifest = self._manifests.get(ui_game_id)
            recorded = {}
            if manifest is not None:
                state = load_state(state_path_for(manifest.game_id))
                recorded = state.get(self._selection_key(patch.id)) or {}
            # the set being looked at, which the voices can have moved to
            # without the app, through the launcher or a hand edit
            stamps = (recorded.get("pipelines") or {}).get(variant)
            if isinstance(stamps, dict):
                recorded = dict(recorded, pipeline=stamps)
            # only what one of our runs built has a pipeline to be behind. One
            # in from outside has no stamp because it was never ours to stamp
            sets = recorded.get("sets") if isinstance(recorded.get("sets"), dict) else {}
            ours = set(recorded.get("installed") or []) | set(sets.get(variant) or [])
            suspect.update(stale_pipelines(recorded, [c for c in applied if c in ours]))
        except Exception:
            pass

        try:
            for category in patch.categories:
                # every applied one, not the zone ones: a category whose list
                # names no zones costs a file read and comes back empty, and
                # there is no field in the manifest that says which is which
                if category["id"] not in applied:
                    continue
                broken = half_written_zones(install_dir, variant, store, data, category)
                if broken:
                    suspect[category["id"]] = (
                        "An area from an earlier install shows up as colored static. "
                        "Reinstalling rebuilds it from the original files."
                        if len(broken) == 1 else
                        f"{len(broken)} areas from an earlier install show up as colored "
                        "static. Reinstalling rebuilds them from the original files.")
        except Exception:
            pass
        return suspect

    def set_hd_categories(self, ui_game_id: str, ui_job_id: str, selected: list,
                          voices: bool | None = None) -> dict:
        """
        records which categories the user wants and returns how to get there.
        Only the want: nothing here touches the install, so it can't leave a
        game half changed. `voices` is the other voice set's box, None to keep.
        """
        manifest = self._manifests.get(ui_game_id)
        patch = self._patch_for_job(manifest, ui_job_id) if manifest else None
        if patch is None or not patch.categories:
            return {"ok": False, "error": "that job has no options"}

        known = {c["id"] for c in patch.categories}
        wanted = [str(s) for s in (selected or []) if str(s) in known]

        path = state_path_for(manifest.game_id)
        state = load_state(path)
        key = self._selection_key(patch.id)
        recorded = state.get(key) or {}
        recorded["selected"] = wanted
        recorded.setdefault("installed", [])
        if voices is not None:
            recorded["voices_too"] = bool(voices)
        state[key] = recorded
        save_state(path, state)

        from patcher.mods.hd_recipe import installer_steps
        steps = installer_steps(patch.categories, set(wanted), set(recorded["installed"]))
        return {"ok": True, "selected": wanted, "steps": steps,
                "mesh": sorted(c["id"] for c in patch.categories
                               if c.get("mesh") and c["id"] in wanted)}

    def settle_injections(self, ui_game_id: str, install_dir, put_back) -> list:
        """
        the patches a rebuild's restore just took off with the files it put
        back, forgotten, as [{"label", "ok", "detail", "jobId"}]. See
        revert.settle_after_restore().
        """
        from patcher.revert import settle_after_restore

        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return []
        results = settle_after_restore(Path(install_dir), manifest,
                                       state_path_for(manifest.game_id), put_back)
        by_id = {p.id: p for p in manifest.patches}
        return [{"label": r.label, "ok": r.ok, "detail": r.detail,
                 "jobId": by_id[r.patch_id].ui_job_id if r.patch_id in by_id else None,
                 "retired": r.patch_id in by_id and by_id[r.patch_id].retired}
                for r in results]

    def record_hd_installed(self, ui_game_id: str, ui_job_id: str, installed: list,
                            built=None, source=None, variant=None, copied_from=None) -> dict:
        """
        records what's actually on disk now, as opposed to what was wanted.

        `selected` decides the next run; `installed` is what a run finished, and
        it's what makes unticking mean "remove". `built` is what this run
        rebuilt (None means all of installed), which earns the pipeline stamp.
        `source` is the mod folder it came from. `variant` is the voice set it
        went into (None for the one the game loads); `sets` and `pipelines`
        keep each set's own since they can be built at different times.
        `copied_from` is the set whose files were copied in, stamps included.
        """
        from patcher.mods.hd_state import PIPELINE, PIPELINE_FROM

        manifest = self._manifests.get(ui_game_id)
        patch = self._patch_for_job(manifest, ui_job_id) if manifest else None
        if patch is None or not patch.categories:
            return {"ok": False, "error": "that job has no options"}

        known = {c["id"] for c in patch.categories}
        done = [str(i) for i in (installed or []) if str(i) in known]

        path = state_path_for(manifest.game_id)
        state = load_state(path)
        key = self._selection_key(patch.id)
        recorded = state.get(key) or {}
        recorded.setdefault("selected", done)
        live = self._live_variant(ui_game_id)
        variant = variant or live
        sets = recorded.get("sets") if isinstance(recorded.get("sets"), dict) else {}
        pipelines = recorded.get("pipelines") if isinstance(recorded.get("pipelines"), dict) else {}
        legacy = recorded.get("pipeline") if isinstance(recorded.get("pipeline"), dict) else {}
        if live in ("u", "c"):
            # a record from before each set had its own line describes the set
            # the game was loading, which hasn't changed if nothing says so
            if not sets and isinstance(recorded.get("installed"), list):
                sets = {live: [str(c) for c in recorded["installed"] if str(c) in known]}
            if not pipelines and legacy:
                pipelines = {live: dict(legacy)}

        # which pipeline built each of them, so a later fix can say "this one
        # was written by code we have since corrected" rather than waiting for
        # somebody to notice in game. Only the ones this run finished: a
        # category still carrying an older build's output keeps its old number.
        # A set nothing has stamped yet borrows the old game-wide ones, which
        # is the best anyone knows about what built it.
        stamps = pipelines.get(variant)
        if not isinstance(stamps, dict):
            stamps = {} if copied_from else legacy
        stamps = {k: v for k, v in stamps.items() if k in done}
        if copied_from:
            theirs = pipelines.get(copied_from)
            if not isinstance(theirs, dict):
                theirs = legacy if copied_from == live else {}
            # a set's own build of a category is kept over the copy, as the copy keeps it
            for category in done:
                if category not in stamps and category in theirs:
                    stamps[category] = theirs[category]
        # stamping everything in `done` wiped the warning off a category an
        # older build got wrong whenever somebody added a different one
        fresh = done if built is None else [str(b) for b in built if str(b) in done]
        for category in fresh:
            stamps[category] = PIPELINE.get(category, PIPELINE_FROM)

        if variant in ("u", "c"):
            recorded["sets"] = dict(sets, **{variant: done})
            recorded["pipelines"] = dict(pipelines, **{variant: stamps})
        if variant == live:
            recorded["installed"] = done
            recorded["pipeline"] = stamps
        if source:
            recorded["source"] = str(source)
        state[key] = recorded
        save_state(path, state)
        return {"ok": True, "installed": done}

    def hd_reverted(self, ui_game_id: str) -> None:
        """every voice set back from the backup, so the record has nothing in any of them"""
        manifest = self._manifests.get(ui_game_id)
        patch = self._patch_for_job(manifest, self.hd_job(ui_game_id)) if manifest else None
        if patch is None:
            return
        path = state_path_for(manifest.game_id)
        state = load_state(path)
        recorded = state.get(self._selection_key(patch.id))
        if not isinstance(recorded, dict):
            return
        recorded["installed"], recorded["pipeline"] = [], {}
        for part in ("sets", "pipelines", "owed", "failed", "refused"):
            recorded.pop(part, None)
        save_state(path, state)

    def owe_hd(self, ui_game_id: str, variant: str, categories, paid: bool = False) -> None:
        """
        a build into `variant` of `categories` started, or with `paid` finished.
        What's owed when one stops partway, or fails, is the categories that set
        may still carry an older build of, which neither the record nor the
        archives can tell from a finished one, and the voices note on the other
        set says so. `categories` None with `paid` clears the set.
        """
        manifest = self._manifests.get(ui_game_id)
        job = self.hd_job(ui_game_id)
        patch = self._patch_for_job(manifest, job) if manifest and job else None
        if patch is None:
            return
        path = state_path_for(manifest.game_id)
        state = load_state(path)
        recorded = state.get(self._selection_key(patch.id))
        if not isinstance(recorded, dict):
            if paid:
                return
            recorded = state[self._selection_key(patch.id)] = {}
        book = recorded.get("owed") if isinstance(recorded.get("owed"), dict) else {}
        had = {str(c) for c in book.get(variant) or []}
        named = None if categories is None else {str(c) for c in categories}
        if paid:
            now = set() if named is None else had - named
        else:
            now = had | (named or set())
        if now == had:
            return
        if now:
            book[variant] = sorted(now)
        else:
            book.pop(variant, None)
        recorded["owed"] = book
        save_state(path, state)

    def hd_owed(self, ui_game_id: str, variant: str) -> set:
        """what a build into `variant` started and never finished"""
        manifest = self._manifests.get(ui_game_id)
        job = self.hd_job(ui_game_id)
        patch = self._patch_for_job(manifest, job) if manifest and job else None
        if patch is None:
            return set()
        recorded = load_state(state_path_for(manifest.game_id)).get(
            self._selection_key(patch.id))
        book = recorded.get("owed") if isinstance(recorded, dict) else None
        return {str(c) for c in (book.get(variant) or [])} if isinstance(book, dict) else set()

    def hd_switched(self, ui_game_id: str, variant: str) -> None:
        """
        the voices moved to `variant`, so what the record says the game loads
        moves with them: that set's line in `sets`, or nothing at all for a set
        no HD has grown into, whatever the record remembers (a Revert or a
        Steam verify since). A set with HD the record can't name is left be.
        """
        from patcher.mods.hd_install import voices_with_hd

        job = self.hd_job(ui_game_id)
        install_dir = self.install_dir(ui_game_id)
        if not job or install_dir is None:
            return
        if variant not in voices_with_hd(install_dir):
            categories = []
        else:
            categories = (self.hd_categories(ui_game_id, job).get("sets") or {}).get(variant)
            if categories is None:
                return
        self.record_hd_installed(ui_game_id, job, categories, built=[], variant=variant)

    def _live_variant(self, ui_game_id: str) -> str | None:
        """the voice set the game loads, or None when that can't be told"""
        from patcher.mods.hd_install import find_variant

        install_dir = self.install_dir(ui_game_id)
        try:
            return find_variant(install_dir) if install_dir is not None else None
        except Exception:
            return None

    def hd_source(self, ui_game_id: str):
        """
        (game, what detect() found) for the folder this game's HD mod was last
        installed from, or (game, None) when it isn't there any more. The record
        says where, and for an install older than that record, the run log's
        header does.
        """
        from patcher.mods.hd_games import for_job

        manifest = self._manifests.get(ui_game_id)
        job = self.hd_job(ui_game_id)
        game = for_job(job) if job else None
        if manifest is None or game is None:
            return None, None
        recorded = (load_state(state_path_for(manifest.game_id))
                    .get(self._selection_key(self._patch_for_job(manifest, job).id)) or {})
        # a state file is hand-editable, so the record's folder is a string or nothing
        source = recorded.get("source") if isinstance(recorded, dict) else None
        for source in [source if isinstance(source, str) else None, *self._logged_sources(job)]:
            try:
                found = game.detect(Path(source)) if source and Path(source).is_dir() else None
            except (OSError, ValueError):
                found = None
            if found is not None:
                return game, found
        return game, None

    @staticmethod
    def _logged_sources(job: str) -> list:
        """the mod folders named in this job's run logs, newest first"""
        from patcher.paths import run_log_path

        folder = run_log_path("x").parent
        try:
            logs = sorted(folder.glob(f"{job}-*.log"), reverse=True)
        except OSError:
            return []
        found = []
        for log in logs:
            try:
                with log.open(encoding="utf-8", errors="replace") as handle:
                    head = [handle.readline() for _ in range(4)]
            except OSError:
                continue
            found += [line[len("mod: "):].strip() for line in head if line.startswith("mod: ")]
        return found

    def voice_gap(self, ui_game_id: str, language: str) -> dict | None:
        """
        what a switch to `language` leaves of the HD Project behind, for the
        voices row to say: None when that set has what the one being left has,
        which is every switch after an install with the voices box ticked.
        Otherwise {"from", "mod", "none"}, none for a set with no HD in it at
        all, and "missing" and "older" by label, when they can be told. A set with more
        than the one being left keeps it, and nothing here ever moves a file.
        """
        from patcher import voices
        from patcher.mods import hd_mirror
        from patcher.mods.hd_games import for_job
        from patcher.mods.hd_install import find_variant, voices_with_hd

        install_dir = self.install_dir(ui_game_id)
        job = self.hd_job(ui_game_id)
        game = for_job(job) if job else None
        if install_dir is None or game is None or language not in voices.LANGUAGES:
            return None
        going, leaving = voices.LANGUAGES[language][1], find_variant(install_dir)
        hd = voices_with_hd(install_dir)
        if going == leaving or leaving not in hd:
            return None
        lack = hd_mirror.lacking(install_dir, leaving, going)
        missing, _, _ = self.voice_missing(ui_game_id, leaving, going)
        # what went in since the last copy finished, a same-length repair
        # included, which the sizes can't see and the set still counts as having.
        # Only of what the set being left still has: a rebuild without one
        # took it off, and there's nothing of it to be behind on
        owed = hd_mirror.owed(install_dir, leaving, going)
        has = self.hd_has(ui_game_id, leaving)
        if owed == hd_mirror.EVERYTHING:
            owed = set(has or ())
        elif owed and has is not None:
            owed = set(owed) & set(has)
        behind = sorted(set(missing) | set(owed or ()))
        if not lack and not behind:
            return None
        absent, older = self.voice_labels(ui_game_id, going, behind) if behind else ([], [])
        return {"from": leaving, "mod": game.name, "none": going not in hd, "missing": absent,
                "older": older}

    def voice_labels(self, ui_game_id: str, target: str, categories) -> tuple:
        """
        ([what `target` hasn't got], [what it has an older build of]), by label,
        of `categories`. The second is a build into it that stopped partway
        """
        patch = self.hd_categories(ui_game_id, self.hd_job(ui_game_id))
        names = {c["id"]: c.get("label") or c["id"] for c in patch["categories"]}
        has, _ = self._carried(ui_game_id)
        had = has(target) if has is not None else None
        if had is None:
            had = set((patch.get("sets") or {}).get(target) or [])
        return ([names.get(c, c) for c in categories if c not in had],
                [names.get(c, c) for c in categories if c in had])

    def record_hd_copied(self, ui_game_id: str, source: str, target: str, outcome,
                         checked=None) -> None:
        """
        what a finished hd_mirror.copy() left in `target`, as the record keeps
        it: every category `source` has, less the pass that stayed out. Those
        are the source's build now, so whatever a build into `target` still
        owed of them is paid, of `checked` (what the copy read rather than took
        on sizes) when it's given
        """
        job = self.hd_job(ui_game_id)
        if not job:
            return
        patch = self.hd_categories(ui_game_id, job)
        sets = patch.get("sets") or {}
        zones = self._zone_ids(patch)
        has = self.hd_has(ui_game_id, source)
        if has is not None:
            # behind the source on what it hasn't got is nothing, and left owed
            # it came back as an older build once the source got them back
            # from the target itself
            stale = self.hd_owed(ui_game_id, target) - set(has)
            if stale:
                self.owe_hd(ui_game_id, target, stale, paid=True)
        copied = set(has or [])
        if "characters" in outcome.blocked:
            copied &= zones
        if "zones" in outcome.blocked:
            copied -= zones
        if not copied:
            return
        if outcome.files or outcome.zones or not set(sets.get(target) or []) >= copied:
            self.record_hd_installed(ui_game_id, job,
                                     sorted(set(sets.get(target) or []) | copied),
                                     built=[], variant=target, copied_from=source)
        # the target has the source's build of these now. Not the other way
        # round: the copy only reads what the source changed, so a model the
        # target's own newer build changed and the source's didn't stays as it
        # was in each, and the source is still behind on it
        self.owe_hd(ui_game_id, target, copied if checked is None else copied & set(checked),
                    paid=True)

    def hd_has(self, ui_game_id: str, variant: str) -> list | None:
        """the categories the record says one voice set has, or None when it can't say"""
        job = self.hd_job(ui_game_id)
        if not job:
            return None
        patch = self.hd_categories(ui_game_id, job)
        have = (patch.get("sets") or {}).get(variant)
        if have is None and variant == self._live_variant(ui_game_id):
            have = patch.get("installed") or []
        return have

    @staticmethod
    def _zone_ids(patch: dict) -> set:
        """the categories that are zones, XIII-2's by flag and XIII's by their list"""
        return {c["id"] for c in patch["categories"]
                if c.get("zones") or str(c.get("list", "")).lower() == "loadhdenvironments.txt"}

    def voice_missing(self, ui_game_id: str, source: str, target: str):
        """
        (the categories `source` has and `target` doesn't, the mod folder to
        build them from or None, whether that could be told at all), for what
        hd_mirror.copy() has to leave to the tools and what the voices row says
        a set is short of. A build into `target` that stopped partway can leave
        it an older build of one it counts as having.
        """
        has, found = self._carried(ui_game_id)
        if has is None:
            return [], None, False
        here, there = has(source), has(target)
        owed = self.hd_owed(ui_game_id, target)
        if here is None or there is None:
            return sorted(owed), found, bool(owed)
        return sorted((here - there) | (owed & here)), found, True

    def _carried(self, ui_game_id: str):
        """
        (has, found): has(variant) is the categories one voice set carries, by
        the archives with the mod folder at hand, by the record checked against
        the archives without, or None when it can't be told. (None, None) for
        a game with no HD job
        """
        from patcher.mods import hd_mirror
        from patcher.mods.hd_install import voices_with_hd

        install_dir = self.install_dir(ui_game_id)
        job = self.hd_job(ui_game_id)
        if install_dir is None or not job:
            return None, None
        game, found = self.hd_source(ui_game_id)
        patch = self.hd_categories(ui_game_id, job)
        recorded, hd = patch.get("sets") or {}, voices_with_hd(install_dir)
        live, zone_ids = self._live_variant(ui_game_id), self._zone_ids(patch)

        def has(variant):
            if variant not in hd:
                return set()
            if found is not None and game is not None:
                state = game.applied_in(install_dir, found, patch["categories"], variant)
                if state is not None and state.verified:
                    return set(state.categories)
            if variant in recorded:
                said = set(recorded[variant])
            elif variant == live:
                said = set(patch.get("installed") or []) or None
            else:
                return None
            # the record without the folder, less what the archives show is
            # gone: a verify that put one set's archive back took its HD with it
            if said:
                chr_left, zones_left = hd_mirror.hd_left(install_dir, variant)
                said = {c for c in said if (zones_left if c in zone_ids else chr_left)}
            return said

        return has, found

    def hd_set_state(self, ui_game_id: str, ui_job_id: str, mod_root: str, variant: str) -> dict:
        """
        hd_survey's applied/verified for one voice set, the launcher's or not.
        What the voices box's build left in the other set when it stopped partway.
        """
        from patcher.mods.hd_games import for_job

        answer = {"applied": [], "verified": False, "variant": variant}
        game = for_job(ui_job_id)
        install_dir = self.install_dir(ui_game_id)
        if game is None or install_dir is None:
            return answer
        try:
            found = game.detect(Path(mod_root))
            if found is None:
                return answer
            patch = self.hd_categories(ui_game_id, ui_job_id)
            state = game.applied_in(install_dir, found, patch["categories"], variant)
        except Exception:
            return answer
        if state is not None:
            answer.update(applied=sorted(state.categories), verified=state.verified)
        return answer

    def identify_drop(self, file_path: str, unpack: bool = True, progress=None) -> dict:
        """_identify_drop(), and what the drawer calls the mod while it works on it"""
        from patcher.mods.game_files import mod_name
        from patcher.mods.titles import title

        answer = self._identify_drop(file_path, unpack, progress)
        try:
            answer.setdefault("title", title(mod_name(file_path)))
        except Exception:
            pass
        return answer

    def _identify_drop(self, file_path: str, unpack: bool = True, progress=None) -> dict:
        """
        what a dropped path is, before anything is done with it.

        People drop the zip they downloaded, so archives get identified too.
        A zip is read from its central directory for free; rar and 7z can't be
        listed with the stdlib, so those unpack first. unpack=False answers
        without writing anything. `progress(bytes, of)` reports the unpack,
        `of` 0 for a .rar since it won't say its size.
        """
        from patcher.mods.hd_games import GAMES, identify
        from patcher.mods.hd_recipe import looks_like_archive, names_in_archive

        path = Path(file_path)
        if not path.exists():
            return {"kind": "missing"}

        # XIII's HD Project or XIII-2's, and the job id says which. The page
        # opens the options on whichever game's row that id belongs to.
        if path.is_dir():
            game, found = identify(path)
            if found is not None:
                return {"kind": "hdproject", "jobId": game.job_id, "detail": found}
            return {"kind": "folder"}

        if not looks_like_archive(path):
            # somebody browsing for a folder with a FILE picker, which is the
            # working route when the folder picker is the one that hands back
            # nothing. Navigating into the mod folder and choosing anything in
            # it means the folder, so check the parent before giving up.
            for near in (path.parent, path.parent.parent):
                if near != path and near.is_dir():
                    game, found = identify(near)
                    if found is not None:
                        return {"kind": "hdproject", "jobId": game.job_id,
                                "detail": found, "via": str(path)}
            return {"kind": "file"}

        names = names_in_archive(path)
        if names and not any(game.holds(names) for game in GAMES):
            # a listing we could read that is not either mod. an ordinary mod
            # archive, so say so without unpacking it.
            return {"kind": "archive"}
        if not unpack:
            return {"kind": "maybe-hdproject" if names else "archive"}

        unpacked, problem = self._unpack_for_project(path, progress)
        if unpacked is None:
            return {"kind": "archive", "error": problem} if problem else {"kind": "archive"}
        game, found = identify(unpacked)
        if found is None:
            # any other mod goes in from its archive, or is turned away while
            # the HD Project's download is awaited, so the unpack is only
            # gigabytes of litter beside somebody's download
            clear_tree(unpacked)
            return {"kind": "archive"}
        return {"kind": "hdproject", "jobId": game.job_id, "detail": found,
                "unpackedTo": str(unpacked)}

    # written into the staging folder last, naming the archive it came from
    UNPACKED = ".unpacked"

    def _unpack_for_project(self, archive: Path, progress=None):
        """
        unpacks beside the archive. (folder, "") or (None, why).

        Beside it, not %TEMP%: it's six gigabytes and the download's drive is
        the likeliest to have room. A finished unpack is reused; a partial one
        passes detect() and installs with art missing, so anything unfinished
        is cleared and redone. models\\orig is carried across since it's the
        vanilla cache from the game, not from the mod.
        """
        import json
        import tempfile

        from patcher.ingest import try_system_extract
        from patcher.mods.hd_games import identify

        def detect(folder):
            return identify(folder)[1]

        archive = Path(archive)
        stamp = archive.stat()
        marker = {"name": archive.name, "size": stamp.st_size, "mtime_ns": stamp.st_mtime_ns}
        staging = archive.parent / f".{archive.stem}-unpacked"
        # the usual folder, and any fallback a previous unpack had to use
        # because that one would not clear. By prefix rather than glob: a
        # stem like "HD Project [v3]" is a pattern to glob.
        fallback = f".{archive.stem}-unpacked-"
        earlier = [staging, *sorted(p for p in archive.parent.iterdir()
                                    if p.name.startswith(fallback) and p.is_dir())]
        for folder in earlier:
            try:
                if json.loads((folder / self.UNPACKED).read_text(encoding="utf-8")) == marker:
                    return folder, ""
            except (OSError, ValueError):
                pass

        # set aside rather than inside, so an unpack cut off after this still
        # finds it next time. An older one already there is the real cache and
        # what is in staging now is at best a partial copy of it.
        kept = archive.parent / f".{archive.stem}-orig"
        for folder in earlier:
            if not folder.is_dir():
                continue
            found = detect(folder)
            cache = Path(found["root"]) / "models" / "orig" if found else None
            if cache is not None and cache.is_dir() and not kept.exists():
                try:
                    cache.rename(kept)
                except OSError:
                    pass
            clear_tree(folder)
        try:
            staging.mkdir(parents=True)
        except OSError:
            # something in the old folder would not delete, and unpacking into
            # what is left of it is how an older version got installed. Beside
            # the archive when there is room to, %TEMP% when even that fails.
            try:
                staging = Path(tempfile.mkdtemp(prefix=f".{archive.stem}-unpacked-",
                                                dir=archive.parent))
            except OSError:
                staging = Path(tempfile.mkdtemp(prefix="ffxiii-hd-"))

        name = archive.name.lower()
        if name.endswith(".zip"):
            import zipfile

            from patcher import zipnames
            from patcher.download import free_space
            try:
                with zipfile.ZipFile(archive) as opened:
                    members = opened.infolist()
                    need = sum(m.file_size for m in members) + 256 * 1024 * 1024
                    free = free_space(staging)
                    if free < need:
                        return None, (f"unpacking it needs {need / 1024 ** 3:.1f} GB free on the "
                                      f"drive it is on and there is {free / 1024 ** 3:.1f} GB")
                    total, copied, said = need - 256 * 1024 * 1024, 0, 0
                    for member in members:
                        # flatten=False: the mod is a tree and Data/ beside SupportFiles/
                        # is the whole shape detect() looks for
                        target = zipnames.safe_target(staging, member.filename, False)
                        if target is None:
                            continue
                        if member.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with opened.open(member) as src, target.open("wb") as out:
                            shutil.copyfileobj(src, out, length=1024 * 1024)
                        copied += member.file_size
                        # a percent at a time is plenty for a line in a drawer
                        if progress is not None and copied - said >= total / 100:
                            said = copied
                            progress(copied, total)
            except (zipfile.BadZipFile, OSError) as e:
                if self._log:
                    self._log.warning("could not unpack %s: %s", archive.name, e)
                clear_tree(staging)
                return None, f"it could not be unpacked ({e})"
        # rar and 7z, which only the system extractor can open. Six gigabytes
        # through tar takes a while on a hard drive, so not the five minutes
        # sized for a 90KB mod.
        elif try_system_extract(archive, staging, timeout=4 * 3600, progress=progress) is None:
            # half an unpack is nothing to leave beside a download. Room is
            # read before it goes: an unpack that stopped on a full drive
            # leaves it all but empty
            from patcher.download import free_space

            full = free_space(staging) < 256 * 1024 * 1024
            clear_tree(staging)
            if full:
                return None, (f"the drive {archive.name} is on ran out of room unpacking it. "
                              f"Free up more than {archive.stat().st_size / 1024 ** 3:.1f} GB, "
                              "since what's inside takes more than the download, and drop it "
                              "again.")
            return None, ""

        found = detect(staging)
        if kept.is_dir() and found is not None:
            home = Path(found["root"]) / "models" / "orig"
            if not home.exists():
                try:
                    home.parent.mkdir(parents=True, exist_ok=True)
                    kept.rename(home)
                except OSError:
                    pass
        (staging / self.UNPACKED).write_text(json.dumps(marker), encoding="utf-8")
        return staging, ""

    def supported_job_ids(self, ui_game_id: str) -> list[str]:
        """
        which ui jobs have a patch behind them. an asset_pack counts as
        supported here even though the engine cannot install one yet: the
        manifest entry is real, it resolves, and the engine reports its own
        skip reason. that is a different thing from a job id no manifest has
        ever heard of, and collapsing the two would hide which is which.
        """
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return []
        return [p.ui_job_id for p in manifest.patches if p.ui_job_id]

    def scan(self, ui_game_id: str) -> dict:
        """
        resolves the real install directory and reports what is already done.
        read-only, so it is safe to call on every game switch.
        """
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return {"ok": False, "found": False, "path": None, "source": None,
                    "supported": [], "applied": [],
                    "error": f"no manifest for {ui_game_id}"}

        resolution = resolve_install_dir(manifest, steam_library_paths=self._libraries())
        supported = self.supported_job_ids(ui_game_id)

        if not resolution.found:
            return {"ok": True, "found": False, "path": None, "source": None,
                    "supported": supported, "applied": [], "error": resolution.error}

        status = scan_patch_status(resolution.install_dir, manifest, state_path_for(manifest.game_id))
        applied = [p.ui_job_id for p in manifest.patches
                   if p.ui_job_id and status.get(p.id, False)]
        try:
            from patcher.engine import held_off

            kept = held_off(resolution.install_dir, manifest)
        except Exception:
            # the Launch row waits on it, which is where it was before
            kept = {}
        held = {p.ui_job_id: kept[p.id] for p in manifest.patches
                if p.ui_job_id and p.id in kept and not status.get(p.id, False)}

        return {
            "ok": True,
            "found": True,
            # str() because a WindowsPath does not survive the json hop
            "path": str(resolution.install_dir),
            "source": resolution.source,
            "supported": supported,
            "applied": applied,
            # required rows a launch refusal keeps off, which Launch doesn't wait on
            "held": held,
            "error": None,
        }

    def set_install_dir(self, ui_game_id: str, folder: str | None) -> dict:
        """
        points this game at a folder the user picked, and remembers it.

        Validated before it is stored, because the folder someone browses to
        is as often the Steam root or the library root as the game itself, and
        an override that does not hold the exe turns "not found" into fourteen
        confusing per-patch failures.
        """
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return {"ok": False, "error": f"no manifest for {ui_game_id}"}

        if not folder:
            remember_path(manifest.game_id, None)
            return self.scan(ui_game_id)

        chosen = Path(folder)
        if not validate_install_dir(chosen, manifest):
            return {"ok": False, "found": False, "path": None, "source": None,
                    "supported": self.supported_job_ids(ui_game_id), "applied": [],
                    "error": f"{manifest.executable_relative_path} isn't in that folder. "
                             f"Pick the one holding {manifest.display_name} itself"}

        remember_path(manifest.game_id, chosen)
        if self._log is not None:
            self._log.info("install dir for %s set manually to %s", manifest.game_id, chosen)
        return self.scan(ui_game_id)

    def nexus_status(self) -> dict:
        """
        whether one-click login is even possible on this build, and whether
        the user has already done it. the UI needs both: a build with no app
        id must not offer a sign in that cannot work.
        """
        state = nexus_auth.status(nexus_config_path())
        # the tier decides which experience the UI can promise. premium means
        # a run needs no clicks at all; free means one click per Nexus mod.
        if state.get("signedIn"):
            source = NexusSource(nexus_config_path())
            account = source.account()
            state["isPremium"] = account.is_premium
            state["name"] = account.name
            state["keyValid"] = account.valid
        else:
            state["isPremium"] = False
            state["name"] = None
            state["keyValid"] = False
        state["apiKeyPage"] = "https://www.nexusmods.com/users/myaccount?tab=api"
        return state

    def nexus_sign_in(self, open_browser, on_progress=None) -> dict:
        """
        blocking, same as apply(). the middle of this flow is a human
        clicking Authorize in a browser, so it can sit for minutes and has to
        be on a worker thread.
        """
        outcome = nexus_auth.sign_in(
            nexus_config_path(), open_browser=open_browser, on_progress=on_progress)
        if self._log is not None:
            self._log.info("nexus sign in -> %s (%s)", outcome.status, outcome.detail)
        return outcome.as_dict()

    def nexus_set_key(self, api_key: str) -> dict:
        """
        stores a personal api key, but only after Nexus confirms it.

        validating first matters: a mistyped key otherwise gets written to
        disk and every later run fails with a 403 that looks like a bug in
        this tool rather than a typo. validate.json is exempt from the rate
        limit, so this costs nothing against the quota.
        """
        from patcher.mods.nexus_api import validate_api_key
        from patcher.mods.nxm_handler import save_api_key

        key = (api_key or "").strip()
        if not key:
            return {"ok": False, "error": "no key given"}

        account = validate_api_key(key)
        if not account.valid:
            return {"ok": False, "error": account.error or "Nexus didn't accept that key"}

        save_api_key(nexus_config_path(), key)
        if self._log is not None:
            self._log.info("nexus key stored for %s (premium=%s)", account.name, account.is_premium)
        return {"ok": True, "name": account.name, "isPremium": account.is_premium}

    def nexus_sign_out(self) -> dict:
        return nexus_auth.sign_out(nexus_config_path())

    def jobs_needing_nexus(self, ui_game_id: str, job_ids: list[str]) -> list[str]:
        """
        which of these jobs need a signed in Nexus account. Goes by patch type,
        and a patch with a mirror_url never counts since it never touches Nexus.
        """
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return []
        # nothing "needs" a Nexus account any more. a nexus patch is obtained
        # by opening its page and catching the download, so reporting jobs
        # here would summon a sign-in prompt for a flow that does not use one.
        # a stored Premium key still accelerates the download; it is just
        # never a precondition.
        return []

    def ingest(self, ui_game_id: str, file_path: str, job_id: str | None = None) -> dict:
        """
        installs a file the user downloaded themselves and dropped on the
        window. No key, no Premium, no protocol handler needed. job_id narrows
        it to a row; without one we work it out and refuse if two jobs could
        claim it.
        """
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return {"ok": False, "error": f"no manifest for {ui_game_id}"}

        source = Path(file_path)
        if not source.exists():
            return {"ok": False, "error": f"couldn't find {source.name}"}

        resolution = resolve_install_dir(manifest, steam_library_paths=self._libraries())
        if not resolution.found:
            return {"ok": False, "error": resolution.error}

        try:
            found = scan(source)
        except IngestError as e:
            return {"ok": False, "error": str(e)}

        try:
            return self._identify(manifest, found, source, resolution, job_id)
        finally:
            # identify-only, so nothing downstream needs the unpacked copy
            found.cleanup()

    def _identify(self, manifest, found, source: Path, resolution, job_id) -> dict:
        matches = self._match_jobs(manifest, found, job_id)
        if not matches:
            return {"ok": False, "error": f"nothing in {source.name} matches a patch for this game",
                    "assets": len(found.candidates)}
        if len(matches) > 1:
            # never guess which mod a file is. installing the wrong one into a
            # game folder is not something the user can easily undo.
            return {"ok": False, "ambiguous": [p.ui_job_id for p in matches],
                    "error": "this file could be more than one patch, drop it on the row you mean"}

        patch = matches[0]
        return {"ok": True, "jobId": patch.ui_job_id, "patchId": patch.id,
                "label": patch.label, "assets": len(found.candidates),
                "installDir": str(resolution.install_dir),
                "source": str(source)}

    def install_dropped(self, ui_game_id: str, file_path: str, job_id: str | None = None,
                        on_progress=None, cancel=None) -> dict:
        """
        installs a file the user supplied. Never installs anything it couldn't
        identify confidently, since a wrong guess writes files the user can't
        easily undo. One scan for the whole install, so a format python can't
        open only gets unpacked once.
        """
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return {"ok": False, "error": f"no manifest for {ui_game_id}"}

        source = Path(file_path)
        if not source.exists():
            return {"ok": False, "error": f"couldn't find {source.name}"}

        resolution = resolve_install_dir(manifest, steam_library_paths=self._libraries())
        if not resolution.found:
            return {"ok": False, "error": resolution.error}

        try:
            found = scan(source)
        except IngestError as e:
            return {"ok": False, "error": str(e)}

        try:
            return self._install_identified(manifest, found, source, resolution, job_id,
                                            on_progress=on_progress, cancel=cancel)
        finally:
            found.cleanup()

    def _install_identified(self, manifest, found, source: Path, resolution, job_id,
                            on_progress=None, cancel=None) -> dict:
        identified = self._identify(manifest, found, source, resolution, job_id)
        if not identified.get("ok"):
            return identified

        patch = next(p for p in manifest.patches if p.id == identified["patchId"])
        install_dir = Path(identified["installDir"])
        # whatever scan ended up reading, which for an unpacked archive is the
        # staging folder rather than the file that was dropped
        source = found.source

        if patch.type == "archive_inject":
            return self._inject_from(patch, manifest, install_dir, source, found,
                                     on_progress=on_progress, cancel=cancel)

        target = install_dir / (patch.install_relative_path or "")

        # only the members this patch is responsible for. a mod archive is
        # full of readmes, screenshots and installer scripts, and flattening
        # the whole thing into a game folder leaves litter the user then has
        # to identify and remove by hand.
        wanted = self._members_to_install(patch, found)
        if not wanted:
            return {"ok": False, "jobId": patch.ui_job_id,
                    "error": f"{source.name} doesn't contain the files this patch needs"}

        # probe before the first file: halfway through a mod, files on disk
        # with no state record are invisible to revert and the doctor.
        refusal = writable_or_reason(target)
        if refusal:
            return {"ok": False, "jobId": patch.ui_job_id, "error": refusal}

        from patcher.engine import backup_path_for, backup_taken, recorded_digests
        from patcher.fileops import copy_atomic, unswap
        from patcher.state import digest_of

        state_path = state_path_for(manifest.game_id)
        # what an earlier install of this left is ours to write over. Anything
        # else gets set aside first, the way a bundled install keeps a dll. A
        # record from before digests names its files, and those go by name the
        # way Revert has always read one
        earlier = load_state(state_path)["applied_patches"].get(patch.id)
        earlier = earlier if isinstance(earlier, dict) else {}
        ours = recorded_digests([earlier])
        hashed = earlier.get("digests") if isinstance(earlier.get("digests"), dict) else {}
        was = earlier.get("kept") if isinstance(earlier.get("kept"), dict) else {}
        by_name = {name for name in earlier.get("files") or [] if name not in hashed}
        installed, kept, aside = [], {}, []
        try:
            target.mkdir(parents=True, exist_ok=True)
            for index, member in enumerate(wanted, start=1):
                leaf = Path(member.replace("\\", "/")).name
                dest, backup = target / leaf, backup_path_for(target / leaf)
                # an empty one isn't worth setting aside, see apply_bundled_install
                theirs = (leaf not in installed and leaf not in by_name and dest.is_file()
                          and dest.stat().st_size > 0 and digest_of(dest) not in ours)
                if theirs and backup.exists():
                    # one already set aside is never written over
                    raise ValueError(backup_taken(leaf, backup))
                if theirs:
                    kept[leaf] = copy_atomic(dest, backup)
                    aside.append(leaf)
                elif backup.is_file() and leaf not in kept:
                    kept[leaf] = was.get(leaf) or digest_of(backup)
                # listed before the write, so one that dies partway comes back out too
                installed.append(leaf)
                extract_member(source, member, dest)
                if on_progress:
                    from patcher.download import Progress
                    on_progress(Progress("install", index, len(wanted), leaf))
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
        except Exception as e:
            if self._log:
                self._log.exception("installing dropped file failed")
            # and take back what did land. a half installed mod that the tool
            # has no record of is worse than no mod: the next run reinstalls on
            # top of it and the user has no way to undo it from here.
            for leaf in dict.fromkeys(installed):
                try:
                    unswap(target / leaf, backup_path_for(target / leaf) if leaf in aside else None)
                except OSError:
                    pass
            stopped = isinstance(e, Cancelled)
            reason = ("stopped" if stopped else
                      write_failure(e, target) if isinstance(e, OSError) else str(e))
            return {"ok": False, "jobId": patch.ui_job_id, "error": reason}

        installed = list(dict.fromkeys(installed))
        # digests and the folder, so Revert takes off only what is still ours.
        # Whatever an earlier drop left that this one didn't write is still
        # ours to take off, and what it set aside still goes back
        earlier_only = [name for name in earlier.get("files") or [] if name not in installed]
        digests = {name: hashed[name] for name in earlier_only if name in hashed}
        digests.update({leaf: d for leaf in installed if (d := digest_of(target / leaf))})
        kept = {**{name: was[name] for name in earlier_only if name in was}, **kept}
        undo = {"dir": str(target.relative_to(install_dir)).replace("\\", "/"), "digests": digests}
        if kept:
            undo["kept"] = kept
        record_applied(state_path, patch.id, installed + earlier_only, undo)
        if self._log:
            self._log.info("installed %s from a supplied file: %s", patch.id, installed)
        return {"ok": True, "jobId": patch.ui_job_id, "label": patch.label,
                "installed": installed, "status": PatchStatus.APPLIED}

    def _inject_from(self, patch, manifest, install_dir: Path, source: Path, found,
                     on_progress=None, cancel=None) -> dict:
        """
        injects a dropped mod into the game's packed archives.

        the payload never lands in the game folder as a loose file: it goes
        into white_img at the offset the index gives, and the index is
        rewritten to match. both language archives move together or neither
        does.
        """
        import fnmatch as _fn
        import tempfile

        from patcher.archive.inject import Replacement
        from patcher.archive.transaction import TransactionError, apply_injection, carry_undo

        # a bundled injection has no "source" pattern, and an explicit job id
        # skips _match_jobs, so a file dropped on its row would KeyError here.
        if any("source" not in spec for spec in patch.injections):
            return {"ok": False, "jobId": patch.ui_job_id,
                    "error": f"{patch.label} already ships its own files, so there is "
                             "nothing to drop in for it. press Apply instead."}

        members = [c.member for c in found.candidates]
        replacements, missing = [], []
        staging = Path(tempfile.mkdtemp(prefix="ffxiii-inject-",
                                        dir=scratch_near(install_dir)))
        try:
            for spec in patch.injections:
                pattern = spec["source"].replace("\\", "/").lower()
                hit = next((m for m in members
                            if _fn.fnmatch(m.replace("\\", "/").lower(), pattern)), None)
                if hit is None:
                    missing.append(spec["game_path"])
                    continue
                extracted = extract_member(source, hit, staging / Path(hit).name)
                replacements.append(Replacement(spec["game_path"], extracted.read_bytes()))

            if missing:
                # a partial injection is worse than none: the rain would be
                # fixed in one weather zone and not the other
                return {"ok": False, "jobId": patch.ui_job_id,
                        "error": f"{source.name} is missing "
                                 f"{', '.join(Path(m).name for m in missing)}"}

            result = apply_injection(
                install_dir, patch.archive_relative_dir, replacements,
                store=undo_store_for(install_dir),
                variants=tuple(patch.archive_variants),
                on_progress=on_progress, cancel=cancel,
            )
        except TransactionError as e:
            return {"ok": False, "jobId": patch.ui_job_id, "error": str(e)}
        except Exception as e:
            if self._log:
                self._log.exception("archive injection failed")
            return {"ok": False, "jobId": patch.ui_job_id, "error": str(e)}
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        # journals and index_backups both have to be recorded, or revert puts
        # the white_img spans back and leaves the filelist describing the
        # injected layout while reporting success. An earlier run's undo for a
        # pair that already had the payload is that pair's only undo, so it
        # carries across.
        state_path = state_path_for(manifest.game_id)
        earlier = load_state(state_path)["applied_patches"].get(patch.id)
        record_applied(state_path, patch.id, [Path(r.game_path).name for r in replacements],
                       carry_undo(result, earlier))
        if self._log:
            self._log.info("injected %s into archives %s", patch.id, result["archives"])
        return {"ok": True, "jobId": patch.ui_job_id, "label": patch.label,
                "installed": result["files"], "archives": result["archives"],
                "status": PatchStatus.APPLIED}

    def _members_to_install(self, patch, found) -> list[str]:
        """
        which archive members belong to this patch.

        driven off the same `expects` patterns used to identify it, so what
        gets installed is exactly what made us confident about the match in
        the first place.
        """
        import fnmatch as _fn

        patterns = [w.lower() for w in (patch.expects or [])]
        names = [(c.member, c.name.lower()) for c in found.candidates]
        names += [(n, Path(n).name.lower()) for n in found.ignored]

        wanted = [member for member, leaf in names
                  if any(_fn.fnmatch(leaf, pattern) for pattern in patterns)]
        # de-duplicate while keeping order: two folders can hold the same leaf
        seen, ordered = set(), []
        for member in wanted:
            if member not in seen:
                seen.add(member)
                ordered.append(member)
        return ordered

    def _match_jobs(self, manifest: GameManifest, found, job_id: str | None) -> list:
        """
        which patches a dropped archive could satisfy.

        deliberately conservative. an archive claims a patch only when it
        carries files the patch is actually responsible for, so dropping a
        texture pack cannot be mistaken for the audio fix.
        """
        candidates = [p for p in manifest.patches if p.ui_job_id and not p.retired]
        if job_id:
            return [p for p in candidates if p.ui_job_id == job_id]

        names = {c.name.lower() for c in found.candidates}
        names |= {Path(n).name.lower() for n in found.ignored}

        exact, fuzzy = [], []
        for patch in candidates:
            patterns = [w.lower() for w in (patch.expects or [])]
            if not patterns:
                continue
            # fnmatch so a manifest can say "*.imgb" for a texture pack that
            # ships hundreds of files nobody can enumerate, while still naming
            # winmm.dll exactly where the mod is one known file
            for pattern in patterns:
                if not any(fnmatch.fnmatch(name, pattern) for name in names):
                    continue
                if "*" in pattern or "?" in pattern:
                    fuzzy.append(patch)
                else:
                    exact.append(patch)
                break

        # a named filename beats a wildcard. veffs.jp.win32.imgb is literally
        # what Leviathan's Tears ships, and it also matches a texture pack's
        # "*.imgb"; treating those as equally likely would make every drop
        # ambiguous and push the work back onto the user for no reason.
        return exact or fuzzy

    def proxy_dir(self, ui_game_id: str):
        """where NovaFix's d3d9.dll, NovaFix.ini and addons folder live"""
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return None, None
        resolution = resolve_install_dir(manifest, steam_library_paths=self._libraries())
        if not resolution.found:
            return None, None
        proxy = next((p for p in manifest.patches if p.id == "install_novafix"), None)
        if proxy is None:
            return None, None
        return resolution.install_dir, (proxy.install_relative_path or "")

    def addons_dir(self, ui_game_id: str, create: bool = True) -> dict:
        """
        the folder NovaFix loads DLL/ASI mods from, created if absent.

        Resolved the way NovaFix does it (addons, or an existing extra, extras,
        mods or dll), since an empty addons/ beside the mods/ it actually reads
        is worse than nothing. create=False reads without leaving a folder.
        """
        install_dir, relative = self.proxy_dir(ui_game_id)
        if install_dir is None:
            return {"ok": False, "error": "couldn't find the game"}

        target = resolve_addons_dir(install_dir / relative)
        existed = target.is_dir()
        if not existed and not create:
            return {"ok": True, "path": str(target), "created": False}
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": write_failure(e, target)}
        # mkdir(exist_ok=True) proves nothing about a folder that's already
        # there, which addons/ usually is, so actually probe it.
        refusal = writable_or_reason(target)
        if refusal:
            return {"ok": False, "error": refusal}
        return {"ok": True, "path": str(target), "created": not existed}

    def install_addon(self, ui_game_id: str, file_path: str) -> dict:
        """
        takes a dropped mod archive and installs it into addons/.

        the archive never touches the game folder: it is staged in a temp
        directory, sifted, and only files that pass every check are moved.
        """
        from patcher import addon_sieve

        where = self.addons_dir(ui_game_id)
        if not where.get("ok"):
            return where
        result = addon_sieve.ingest_archive(Path(file_path), Path(where["path"]))
        if self._log is not None:
            self._log.info("addon install %s -> %s", Path(file_path).name,
                           result.get("modName") or result.get("error"))
        return result

    def our_addon_folders(self, ui_game_id: str) -> set:
        """
        folders under addons/ that this app's own patches install into.

        Read off the manifest rather than listed here, so an add-on added later
        is excluded the day it ships instead of the day somebody remembers. The
        segment that matters is the one directly under whichever addons folder
        NovaFix is using, which is the mod folder the sieve would also create.
        """
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return set()
        # the code patches' add-on, which goes in with the first mod that has
        # any rather than off the manifest, and is theirs to list
        names = {"ff13-code-patches"}
        for patch in manifest.patches:
            parts = [p for p in (patch.install_relative_path or "")
                     .replace("\\", "/").split("/") if p]
            for i, part in enumerate(parts[:-1]):
                if part.lower() in ADDON_DIR_NAMES:
                    names.add(parts[i + 1])
        return names

    def rename_presets(self, ui_game_id: str) -> list:
        """our own settings files moved to the names they have now, engine.renamed_bundled"""
        from patcher.engine import renamed_bundled

        manifest = self._manifests.get(ui_game_id)
        if manifest is None or not any(s.get("was") for p in manifest.patches
                                       for s in p.bundled_files or ()):
            return []
        install_dir = self.install_dir(ui_game_id)
        moved = []
        for patch in manifest.patches if install_dir is not None else ():
            try:
                # asked only once there's something to move
                moved += renamed_bundled(install_dir, patch, state_path_for(manifest.game_id),
                                         busy=lambda: self.game_running(ui_game_id))
            except Exception:
                # still under the old name, which reads as in all the same
                if self._log is not None:
                    self._log.exception("couldn't move %s's settings to their new name",
                                        patch.label)
        return moved

    def depth_off(self, ui_game_id: str) -> bool:
        """ReShade's depth add-on off in our ReShade.ini, engine.depth_off"""
        from patcher.engine import depth_off

        manifest = self._manifests.get(ui_game_id)
        install_dir = self.install_dir(ui_game_id)
        if manifest is None or install_dir is None:
            return False
        wrote = False
        for patch in manifest.patches:
            try:
                wrote |= depth_off(install_dir, patch, state_path_for(manifest.game_id),
                                   busy=lambda: self.game_running(ui_game_id))
            except Exception:
                if self._log is not None:
                    self._log.exception("couldn't turn ReShade's depth add-on off for %s",
                                        patch.label)
        return wrote

    def unname_loaders(self, ui_game_id: str) -> list:
        """
        every drawer mod's dll beside the exe that src_38 gave a name for
        NovaFix's popup (pe_name.py) back to its author's bytes. HD Fonts and
        GUI's version.dll was one, and XIII-2 stopped starting with it in
        """
        from patcher import pe_name
        from patcher.mods import loader_mods

        install_dir, exe = self.install_dir(ui_game_id), self.game_executable(ui_game_id)
        if install_dir is None or exe is None or self.game_running(ui_game_id):
            return []
        try:
            return loader_mods.rewrite(exe.parent, undo_store_for(install_dir),
                                       lambda data, digests: pe_name.unnamed(data, digests))
        except Exception:
            if self._log is not None:
                self._log.exception("couldn't put back the loaders beside %s", exe.name)
            return []

    def module_owner(self, ui_game_id: str, where: str) -> dict:
        """
        whose a dll the game crashed in is, for the launch check's sentence:
        {"name", "drawer"}, a drawer mod's by its record and anything else by
        the name its version resource gives it, NovaFix's or ReShade's.
        `where` is the path Windows logged, or just the dll's name, which is
        then looked for beside the exe
        """
        from pathlib import PureWindowsPath

        from patcher import pe_name
        from patcher.mods import loader_mods
        from patcher.mods.titles import title

        install_dir, exe = self.install_dir(ui_game_id), self.game_executable(ui_game_id)
        name = PureWindowsPath(str(where or "")).name
        if install_dir is None or exe is None or not name:
            return {}
        logged = Path(str(where))
        found = logged if logged.is_absolute() and logged.is_file() else exe.parent / name
        try:
            found = found.resolve()
            rel = found.relative_to(exe.parent.resolve()).as_posix().lower()
            # Windows' own, a graphics driver's: whose those are says nothing
            found.relative_to(Path(install_dir).resolve())
        except (OSError, ValueError):
            return {}
        for record in loader_mods.records(undo_store_for(install_dir)):
            if rel in {r.replace("\\", "/").lower() for r in record["files"]}:
                return {"name": title(record.get("part") or record["name"]), "drawer": True}
        try:
            shown = pe_name.display_name(found.read_bytes()) if found.is_file() else ""
        except OSError:
            shown = ""
        return {"name": shown, "drawer": False} if shown else {}

    def list_addons(self, ui_game_id: str, rename: bool = True) -> dict:
        """
        the drawer's list: the game-file mods in the archives, then NovaFix's
        add-ons. `rename=False` while Apply's writing the state file the
        rename's record goes in
        """
        from patcher import addon_sieve
        from patcher.mods.titles import title

        # before the rows read the preset's name off its settings
        if rename:
            self.rename_presets(ui_game_id)
            self.unname_loaders(ui_game_id)
            self.depth_off(ui_game_id)
        where = self.addons_dir(ui_game_id, create=False)
        # the mods this app keeps records of are listed whatever NovaFix's
        # folder is doing, or one couldn't be seen to be taken off
        ours = self.hd_rows(ui_game_id) + self.file_mods(ui_game_id) \
            + self.loader_mods(ui_game_id) + self.extra_rows(ui_game_id)
        named = {m["name"].lower() for m in ours}
        for row in self.disk_mods(ui_game_id):
            # one mod whose loose files went in with its archives' is one row
            if row["name"].lower() not in named or not row.get("with"):
                ours.append(row)
        if not where.get("ok") and not ours:
            return where
        addons = []
        if where.get("ok"):
            self.tidy_addons(Path(where["path"]))
            from patcher.mods.loader_mods import ff13fix_built

            addons = [dict(m, kind="addon", about="NovaFix add-on")
                      for m in addon_sieve.installed_mods(Path(where["path"]),
                                                          self.our_addon_folders(ui_game_id))]
            for row in addons:
                # NovaFix blocks one of FF13Fix's family from its add-ons
                hit = next((f for f in row.get("files", ()) if f.lower().endswith((".dll", ".asi"))
                            and ff13fix_built(Path(row["path"]) / f)), None)
                if hit:
                    row["warn"] = (f"NovaFix won't load {hit}: it's built on FF13Fix, which "
                                   "NovaFix already covers")
        rows = ours + addons
        short = self._without_4gb(ui_game_id)
        for row in rows:
            # the HD mods want the memory the 4GB Patch opens up, and without
            # it the game runs out partway through and crashes or crawls
            if short and not row.get("warn") and (row.get("kind") == "hd" or row.get("jobId") in (
                    "hdmodelsplus", "hdfonts")):
                row["warn"] = f"The {short} is off, and this needs the memory it gives the game"
        titles = [m.get("title") or title(m["name"]) for m in rows]
        tags = [m.get("tag") or _tagged(m["name"]) for m in rows]
        # two different downloads reading the same, version and all, look like
        # one mod listed twice, so only those keep the whole of what they're
        # called. One mod's archive row and loose-file row share a name and a
        # version beside a title already tells two apart
        called = {}
        for m, t, v in zip(rows, titles, tags):
            called.setdefault((t.lower(), v), set()).add(m["name"].lower())
        return {"ok": True, "path": where.get("path"),
                "mods": [dict(m, title=m["name"] if len(called[t.lower(), v]) > 1 else t, tag=v)
                         for m, t, v in zip(rows, titles, tags)],
                "parts": self._parts_in(ui_game_id, rows)}

    def _without_4gb(self, ui_game_id: str) -> str:
        """this game's 4GB Patch's name when the exe on disk hasn't got its flag, else "" """
        from patcher.engine import laa_patch
        from patcher.patch_ops import is_laa_enabled

        manifest, install_dir = self._manifests.get(ui_game_id), self.install_dir(ui_game_id)
        patch = laa_patch(manifest) if manifest is not None else None
        rel = patch.target_relative_path or manifest.executable_relative_path \
            if patch is not None else None
        if not rel or install_dir is None:
            return ""
        try:
            with open(install_dir / rel, "rb") as exe:
                head = exe.read(4096)
            return "" if is_laa_enabled(head) else patch.label
        except (OSError, ValueError):
            return ""

    def hd_rows(self, ui_game_id: str) -> list:
        """
        the HD Project, under Installed while any of it's in the game, so it
        comes off from where everything else does: its Remove is the Revert to
        the backup taken before its first install
        """
        job = self.hd_job(ui_game_id)
        manifest = self._manifests.get(ui_game_id)
        if job is None or manifest is None:
            return []
        state = self.hd_categories(ui_game_id, job)
        have = set(state["installed"]) | {c for ids in state["sets"].values() for c in ids}
        if not have:
            return []
        patch = self._patch_for_job(manifest, job)
        every = len(state["categories"])
        part = "part" if every == 1 else "parts"
        return [{"name": patch.label, "title": patch.label, "kind": "hd", "jobId": job,
                 "managed": True,
                 "tag": f"all {every} {part}" if len(have) >= every else f"{len(have)} of {every} {part}"}]

    def _parts_in(self, ui_game_id: str, rows: list) -> dict:
        """
        Worth getting row -> [how many of its downloads are in, how many it
        is], for a row that's more than one: LR's DLC restoration is two
        patchers off one page, and one of them in isn't the row in
        """
        manifest = self._manifests.get(ui_game_id)
        out = {}
        for patch in (manifest.patches if manifest else ()):
            if not patch.ui_job_id or len(patch.recipes) < 2:
                continue
            wanted = {r.lower() for r in patch.recipes}
            there = {str(m.get("recipe", "")).lower() for m in rows
                     if m.get("jobId") == patch.ui_job_id}
            out[patch.ui_job_id] = [len(wanted & there), len(wanted)]
        return out

    def tidy_addons(self, addons: Path) -> None:
        """the .NET leftovers out of NovaFix's add-ons, see addon_sieve.leftover"""
        from patcher import addon_sieve

        try:
            gone = addon_sieve.tidy_leftovers(addons)
        except OSError:
            return
        if gone and self._log is not None:
            self._log.info("took %s out of NovaFix's add-ons: .NET libraries, nothing it loads",
                           ", ".join(gone))

    def extras_in(self, ui_game_id: str) -> list:
        """
        the patches the Mods drawer lists under Installed: on record, or ours
        byte for byte on the disk with the record gone. The state file lives in
        AppData, which a new PC or a reinstalled Windows loses while the game's
        folder keeps everything, and the row is what Remove hangs off
        """
        from patcher.engine import installed_on_disk

        manifest = self._manifests.get(ui_game_id)
        listed = [p for p in (manifest.patches if manifest else []) if p.in_mods]
        if not listed:
            return []
        recorded = load_state(state_path_for(manifest.game_id))["applied_patches"]
        install_dir = self.install_dir(ui_game_id) if any(
            p.id not in recorded and p.bundled_files for p in listed) else None
        return [p for p in listed if p.id in recorded
                or (install_dir is not None and installed_on_disk(install_dir, p))]

    def extra_rows(self, ui_game_id: str) -> list:
        """
        Installed's rows for those. A ReShade says its preset and menu key off
        the .ini it's reading now, since its own menu can change either
        """
        from patcher.mods.game_files import reshade_said
        from patcher.mods.loader_mods import reshade_about

        install_dir = self.install_dir(ui_game_id)
        manifest = self._manifests.get(ui_game_id)
        recorded = load_state(state_path_for(manifest.game_id))["applied_patches"] \
            if manifest else {}
        rows = []
        for patch in self.extras_in(ui_game_id):
            record = recorded.get(patch.id) or {}
            home = install_dir / (record.get("dir") or patch.install_relative_path or ".") \
                if install_dir else None
            targets = [s["target"] for s in patch.bundled_files] or list(record.get("files") or [])
            about, warn = "Went in with Apply", ""
            ini = next((t for t in targets if t.lower().endswith("reshade.ini")), None)
            if ini and home is not None:
                try:
                    text = (home / ini).read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    text = ""
                said = reshade_said(text) if text else {}
                # the row's already named after our own preset, so on that one
                # the line says what it runs on instead of the name twice
                own = {Path(n.replace("\\", "/")).stem.lower() for s in patch.bundled_files
                       if s.get("config") and not s["target"].lower().endswith("reshade.ini")
                       for n in (s["target"], *(s.get("was") or ()))}
                if str(said.get("preset") or "").lower() in own:
                    said = dict(said, preset="ReShade")
                about = reshade_about(said)
                exe = self.game_executable(ui_game_id)
                # it only loads through NovaFix on Automatic, and a row saying
                # its preset and key over a ReShade that never loads is a lie
                why = self._reshade_blocked(ui_game_id, exe) if exe is not None else ""
                if why:
                    about = warn = why
            gone = [t.rsplit("/", 1)[-1] for t in targets
                    if t.lower().endswith(".dll") and home is not None and not (home / t).is_file()]
            if gone:
                about = warn = f"{gone[0]} is missing"
            rows.append({"name": patch.label, "kind": "patch", "jobId": patch.ui_job_id,
                         "files": targets, "about": about, "warn": warn, "managed": True})
        return rows

    def _adopt(self, install_dir: Path, patch, state_path: Path) -> dict:
        """
        a record for one of ours found on the disk without one, so Remove can
        take it off. Settings files go down as they shipped: one ReShade has
        written since is the user's, and Remove leaves it
        """
        from patcher.engine import backup_path_for, install_target_dir
        from patcher.state import digest_of

        target_dir = install_target_dir(install_dir, patch)
        files, digests, kept = [], {}, {}
        for spec in patch.bundled_files:
            name, pin = spec["target"], spec.get("sha256")
            target = target_dir / name
            if not target.is_file():
                continue
            files.append(name)
            if pin:
                digests[name] = pin
            # the user's own that the install set aside goes back on Remove
            backup = backup_path_for(target)
            if backup.is_file():
                kept[name] = digest_of(backup)
            # and ours beside a settings file that had become theirs comes off
            fresh = target.with_name(target.name + ".new")
            if spec.get("config") and pin and fresh.is_file() and digest_of(fresh) == pin:
                files.append(name + ".new")
                digests[name + ".new"] = pin
        extra = {"dir": target_dir.relative_to(install_dir).as_posix(), "digests": digests}
        if kept:
            extra["kept"] = kept
        record_applied(state_path, patch.id, files, extra)
        return dict(extra, files=files)

    def remove_extra(self, ui_game_id: str, name: str) -> dict:
        """an extra off again, the way Revert takes that one patch off"""
        from patcher.revert import revert_patch

        manifest = self._manifests.get(ui_game_id)
        patch = next((p for p in self.extras_in(ui_game_id) if p.label == name), None)
        install_dir = self.install_dir(ui_game_id)
        if patch is None or install_dir is None:
            return {"ok": False, "error": f"{name} isn't in."}
        # so what comes off is what's there, under the names the record has
        self.rename_presets(ui_game_id)
        state_path = state_path_for(manifest.game_id)
        record = load_state(state_path)["applied_patches"].get(patch.id)
        if record is None:
            record = self._adopt(install_dir, patch, state_path)
        done = revert_patch(install_dir, manifest, patch, state_path)
        if not done.ok:
            return {"ok": False, "error": done.detail}
        where = install_dir / (record.get("dir") or patch.install_relative_path or ".")
        # ReShade writes its .ini back on every exit, so that one usually stays,
        # and after an update it's under shipped rather than files
        shipped = record.get("shipped") if isinstance(record.get("shipped"), dict) else {}
        stayed = sorted({Path(f).name for f in [*record.get("files", []), *shipped]
                         if f not in done.removed and (where / f).is_file()})
        if not stayed:
            return {"ok": True, "message": f"Every file {name} put in is gone."}
        return {"ok": True, "message": f"{' and '.join(stayed)} changed since it went in, so "
                                       f"{'it stays' if len(stayed) == 1 else 'they stay'}."}

    def loader_mods(self, ui_game_id: str) -> list:
        """the mods beside the game's exe, from their records"""
        import re

        from patcher.mods import loader_mods

        install_dir = self.install_dir(ui_game_id)
        if install_dir is None:
            return []
        exe = self.game_executable(ui_game_id)
        try:
            rows = loader_mods.listed(undo_store_for(install_dir),
                                      exe.parent if exe is not None else None)
        except OSError:
            return []
        # a Worth getting row's mod as it went in, so that row can say it's in:
        # named for it, or under the name Nexus gives its download, with the dll
        # its entry expects still beside the exe. Letters and digits only, since
        # Savior's Patch comes down as SaviorsPatch half the time.
        from patcher.download_watch import nexus_named

        def bare(text):
            return re.sub(r"[^a-z0-9]", "", str(text).lower())

        try:
            dropped = {r.get("name"): re.split(r"[\\/]", str(r.get("origin") or ""))[-1]
                       for r in loader_mods.records(undo_store_for(install_dir))}
        except OSError:
            dropped = {}
        manifest = self._manifests.get(ui_game_id)
        for patch in (manifest.patches if manifest else ()):
            wants = [w.lower() for w in patch.expects or ()]
            if patch.type != "nexus" or not patch.ui_job_id or not wants:
                continue
            for row in rows:
                there = [f.lower() for f in row["files"]]
                named = bare(patch.label) in bare(row["name"]) or bool(
                    patch.nexus_mod_id and nexus_named(dropped.get(row["name"], ""),
                                                       patch.nexus_mod_id))
                if named and all(any(fnmatch.fnmatch(f, w) for f in there) for w in wants):
                    row["jobId"] = patch.ui_job_id
        for row in rows:
            row.setdefault("tag", _tagged(row["name"], dropped.get(row["name"], "")))
        return rows

    def remove_addon(self, ui_game_id: str, mod_name: str, kind: str | None = None) -> dict:
        from patcher.mods.titles import title

        done = self._remove_addon(ui_game_id, mod_name, kind)
        shown = title(mod_name)
        # the record's name is the download's whole name, stamp and all, for
        # one that was in before the app was, so the messages say the row's
        for key in ("message", "error"):
            if isinstance(done.get(key), str) and str(mod_name) != shown:
                done[key] = done[key].replace(str(mod_name), shown)
        if done.get("ok"):
            done["summary"] = f"Removed {shown}."
        return done

    def _remove_addon(self, ui_game_id: str, mod_name: str, kind: str | None = None) -> dict:
        done = self._remove_one(ui_game_id, mod_name, kind)
        # a mod whose loose files went in beside its archives' or its loader's
        # is one row, and they come off with it
        if done.get("ok") and kind != "disk" and any(
                m["name"].lower() == str(mod_name).lower() and m.get("with")
                for m in self.disk_mods(ui_game_id)):
            also = self.remove_disk_mod(ui_game_id, str(mod_name))
            if not also.get("ok") or "changed since" in also.get("message", ""):
                done = dict(done, message=" ".join(
                    filter(None, (done.get("message"), also.get("message") or also.get("error")))))
        return done

    def _remove_one(self, ui_game_id: str, mod_name: str, kind: str | None = None) -> dict:
        from patcher import addon_sieve

        if kind == "disk" or (kind is None and not any(
                m["name"] == str(mod_name) for m in self.file_mods(ui_game_id)
                + self.loader_mods(ui_game_id)) and any(
                m["name"] == str(mod_name) for m in self.disk_mods(ui_game_id))):
            return self.remove_disk_mod(ui_game_id, str(mod_name))
        if kind == "patch":
            return self.remove_extra(ui_game_id, str(mod_name))
        if kind == "files" or (kind is None and any(
                m["name"] == str(mod_name) for m in self.file_mods(ui_game_id))):
            return self.remove_file_mod(ui_game_id, str(mod_name))
        if kind == "loader" or (kind is None and any(
                m["name"] == str(mod_name) for m in self.loader_mods(ui_game_id))):
            return self.remove_loader_mod(ui_game_id, str(mod_name))
        # before resolving anything, because the answer does not depend on
        # finding the game. the list already hides these, so a name reaching
        # here came from somewhere other than the UI, and removing one breaks
        # the game it was installed to fix while Apply puts it straight back.
        if str(mod_name) in self.our_addon_folders(ui_game_id):
            return {"ok": False, "error": f"{mod_name} is part of the patcher"}
        where = self.addons_dir(ui_game_id)
        if not where.get("ok"):
            return where
        return addon_sieve.remove_mod(Path(where["path"]), str(mod_name))

    def remove_loader_mod(self, ui_game_id: str, name: str) -> dict:
        from patcher.mods import loader_mods

        install_dir, exe = self.install_dir(ui_game_id), self.game_executable(ui_game_id)
        if install_dir is None or exe is None:
            return {"ok": False, "error": "couldn't find the game"}
        record = loader_mods._ours(undo_store_for(install_dir), name)
        try:
            # code patches are on record from the game's folder (_codes_from)
            done = loader_mods.remove(install_dir if (record or {}).get("nccp") else exe.parent,
                                      undo_store_for(install_dir), name)
            # a preset it pointed ReShade at, pointed back while it's still that
            pointed = (record or {}).get("preset")
            if done.get("ok") and isinstance(pointed, dict):
                self._unpoint_preset(exe.parent / "ReShade" / "ReShade.ini", pointed)
        except loader_mods.LoaderError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": write_failure(e, exe.parent)}
        if not done.get("ok"):
            return done
        said = f"Removed {name}. Everything it put beside {exe.name} is gone."
        if done.get("left"):
            said = (f"Removed {name}. {', '.join(done['left'])} changed since it went in, so "
                    "that stays where it is.")
        return {"ok": True, "removed": name, "message": said}

    def file_mods(self, ui_game_id: str) -> list:
        """the mods put into the archives from the drawer, each with where it stands now"""
        from patcher.mods import game_files, loader_mods, models_plus

        install_dir = self.install_dir(ui_game_id)
        if install_dir is None:
            return []
        try:
            everything = game_files.records(undo_store_for(install_dir))
        except OSError:
            return []
        manifest = self._manifests.get(ui_game_id)
        # HD Models Plus as its build went in, which is its Worth getting row,
        # by the name both go by, so that row can say it's in
        built = next((p.ui_job_id for p in (manifest.patches if manifest else ())
                      if p.label == models_plus.NAME and p.ui_job_id), None)
        # and a mod the drawer put in by reading its .bat or its patcher, by
        # which one: the Console Content Patch's, LR's two DLC patchers
        read = {recipe.lower(): p.ui_job_id for p in (manifest.patches if manifest else ())
                if p.ui_job_id for recipe in p.recipes}
        out = []
        for record in everything:
            try:
                seen = game_files.state(install_dir, record, everything)
            except Exception:
                seen = {"state": "unknown"}
            # one taken off is only kept while the HD backup still has it in
            if record.get("off") and seen["state"] in ("out", "unknown"):
                continue
            detail = _file_mod_said(record, seen)
            # under its name only when it isn't simply in
            wrong = record.get("off") or seen["state"] in ("out", "partly", "unknown")
            row = {"name": record["name"], "kind": "files", "files": [detail],
                   "detail": detail, "about": detail, "warn": detail if wrong else "",
                   "state": seen["state"], "managed": True,
                   "tag": _tagged(record["name"], record.get("source", ""))}
            if built and record["name"] == models_plus.NAME and seen["state"] in ("in", "partly") \
                    and record.get("version") in models_plus.VERSIONS:
                row["jobId"] = built
            recipe = str(record.get("recipe") or "")
            if recipe.lower() in read and seen["state"] in ("in", "partly"):
                row["jobId"], row["recipe"] = read[recipe.lower()], recipe
            out.append(row)
        # DLC files whose mod came off without them, a file held open at the
        # time, under that mod's name so a Remove can still take them
        try:
            left = loader_mods.strays(undo_store_for(install_dir),
                                      {r["name"] for r in everything})
        except OSError:
            left = []
        for name in dict.fromkeys(r["part"] for r in left):
            files = [rel for r in left if r["part"] == name for rel in r["files"]
                     if (install_dir / rel).is_file()]
            said = f"{len(files)} DLC files left in {_common(files)}" if files else \
                "What's left of its DLC files"
            out.append({"name": name, "kind": "files", "files": [said], "detail": said,
                        "about": said, "warn": said, "state": "in", "managed": True})
        # what that hashed, for the first look after the next start
        game_files.keep_contents(install_dir, undo_store_for(install_dir))
        return out

    def remove_file_mod(self, ui_game_id: str, name: str) -> dict:
        from patcher.mods import game_files, loader_mods
        from patcher.mods.titles import title

        install_dir = self.install_dir(ui_game_id)
        if install_dir is None:
            return {"ok": False, "error": "couldn't find the game"}
        store = undo_store_for(install_dir)
        back = []
        try:
            record = next((r for r in game_files.records(store) if r["name"] == name), None)
            if record is None and any(r.get("part") == name for r in loader_mods.records(store)):
                # all that's left of it is the DLC files a Remove before couldn't take
                loader_mods.remove_parts(install_dir, store, name)
                return {"ok": True, "removed": name, "message": f"Removed {name}'s DLC files, "
                                                               "which were all that was left of it."}
            if record is not None and (record.get("records") or record.get("wdb")):
                done, back = self._remove_rebuilt(install_dir, store, record)
            else:
                done = game_files.remove(install_dir, store, name)
        except (game_files.FilesError, loader_mods.LoaderError) as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": write_failure(e, install_dir)}
        stuck = ""
        try:
            # its DLC files, which went in beside the game as a part of it
            loader_mods.remove_parts(install_dir, store, name)
        except (loader_mods.LoaderError, OSError) as e:
            # the file that wouldn't go, without the "couldn't come off" around it
            cause = e.__cause__ if isinstance(e.__cause__, OSError) else e
            why = write_failure(cause, install_dir) if isinstance(cause, OSError) else str(e)
            stuck = (f" Its DLC files couldn't all come off, so they're listed on their own "
                     f"until they can: {why.rstrip('.')}.")
        if self._log:
            self._log.info("took %s off, %d files back", name, done["files"])
        if done["files"] and done.get("handed"):
            # a table both changed is the later mod's now, this one's edits in it
            said = (f"Removed {name}. {done['files']} of the files it changed "
                    f"{'is' if done['files'] == 1 else 'are'} back the way "
                    f"{'it was' if done['files'] == 1 else 'they were'}. Another mod went in over "
                    + ("the other one, so that stays" if done["handed"] == 1 else
                       f"the other {done['handed']}, so those stay")
                    + " as that mod left it until it comes off too.")
        elif done["files"]:
            said = f"Removed {name}. Every file it changed is back the way it was."
        elif done.get("handed"):
            said = (f"Removed {name}. Another mod had replaced its files, and that mod's "
                    "are still in.")
        else:
            said = f"Removed {name}. It was already gone from the game, so only its entry here went."
        if done.get("stuck"):
            # a repack dropped what they went over, so they stay; the row still goes
            files = sorted({path.rsplit("/", 1)[-1] for path in done["stuck"]})
            one = len(files) == 1
            said = (f"Removed {name}, but {_listed_names(files)} had nothing left under "
                    f"{'it' if one else 'them'} to go back to, so {'it stays' if one else 'they stay'} "
                    f"as {name} left {'it' if one else 'them'}. The game runs fine like that. "
                    "Steam's Verify Integrity of Game Files puts "
                    f"{'it' if one else 'them'} back, along with every other mod's files.")
        said += _crossed_said(done.get("crossed"), "it")
        if done.get("left"):
            said += f" {game_files.lost_said(*done['left'][0])}"
        for other, problem in back:
            other = title(other)
            said += (f" {other} went back on over it." if problem is None else
                     f" {other} had to come off with it and couldn't go back on ({problem}), "
                     "so drop it again to put it back.")
        return {"ok": True, "removed": name, "message": said + stuck}

    def _remove_rebuilt(self, install_dir: Path, store: Path, record: dict) -> tuple:
        """
        (game_files.remove's answer, [(mod, why it didn't go back on or None)])
        for a mod whose records live inside a container (Surihix's LR patchers)
        or a table's fields (Gameplay Tweaks). Mods on top built their copy
        from this one's, so they come off first, this one goes, then they go
        back on over what's left. Cut short, what came off goes back on.
        """
        from patcher.mods import game_files, patchdata, wpd
        from patcher.mods import wdbmod as wdbmods

        above = _over(install_dir, record)
        # either kind built over it can be built again without it, a table's
        # fields as much as a container's records. Over a .txt's edits only one
        # that's all .txt edits itself, though: anything it has whole was made
        # with this one's edits in
        kind = "records" if record.get("records") else "wdb"
        if not above or any(not other.get(kind) or kind == "wdb" and not wdbmods.whole(other)
                            for other in above):
            # a mod that put a whole container of its own in over it never had
            # this one's records to begin with, so the usual hand down is right
            return game_files.remove(install_dir, store, record["name"]), []
        # one of those back on goes on top, so a mod that went in over a file
        # it has whole would end up under it
        lifted = {game_files._rid(r) for r in above + [record]}
        for other in above:
            for spec in other["pairs"]:
                for item in spec["entries"]:
                    if item.get("lost") or item["path"] in (other.get("records") or {}):
                        continue
                    chain = game_files.stacked(install_dir, spec["index"], item["path"])
                    ids = [game_files._rid(r) for r in chain]
                    on = [r for r in chain[ids.index(game_files._rid(other)) + 1:]
                          if game_files._rid(r) not in lifted] \
                        if game_files._rid(other) in ids else []
                    if on:
                        raise game_files.FilesError(
                            f"{other['name']} has to come off and go back on for "
                            f"{record['name']} to come off, and {on[0]['name']} went in over its "
                            f"{item['path'].rsplit('/', 1)[-1]}, which would end up under it. "
                            f"Remove {on[0]['name']} first, then {record['name']}.")
        held = {}
        for other in above:
            try:
                held[other["name"]] = patchdata.captured(install_dir, other) \
                    if kind == "records" else wdbmods.replayable(install_dir, other)
            except (patchdata.BuildError, wpd.WpdError, wdbmods.WdbModError, OSError,
                    ValueError) as e:
                # handed what's under this one, it'd keep this one's records in
                # the game with its DLC files gone, so nothing moves
                raise game_files.FilesError(
                    f"{other['name']} went in over {record['name']} and has to be built again "
                    f"without it, but {e}. Remove {other['name']} first.") from None
        off = []
        try:
            for other in reversed(above):
                game_files.remove(install_dir, store, other["name"])
                off.append(other)
            done = game_files.remove(install_dir, store, record["name"])
        except Exception as e:
            lost = [name for name, why in self._put_back(install_dir, store, off[::-1], held)
                    if why is not None]
            if not lost:
                raise
            why = write_failure(e, install_dir) if isinstance(e, OSError) else str(e)
            raise game_files.FilesError(
                f"{why}. {' and '.join(lost)} came off for it and couldn't go back on, so drop "
                f"{'it' if len(lost) == 1 else 'them'} again.") from e
        return done, self._put_back(install_dir, store, above, held)

    def _put_back(self, install_dir: Path, store: Path, mods: list, held: dict) -> list:
        """[(mod, why it didn't go back on or None)], each built over what the game has now"""
        from patcher.fileops import clear_tree
        from patcher.mods import game_files, loader_mods, patchdata, wdbmod

        back = []
        for other in mods:
            work = None
            try:
                work, found = wdbmod.replay(install_dir, other["wdb"]) if other.get("wdb") \
                    else patchdata.rebuild(install_dir, held[other["name"]])
                with game_files.Source(work) as built:
                    game_files.install(install_dir, store, other["name"], found, built,
                                       origin=other.get("source", ""),
                                       extra={k: other[k] for k in ("recipe", "records", "dlc",
                                                                    "wdb") if k in other})
                back.append((other["name"], None))
            except Exception as e:
                # one that won't go back on doesn't keep the rest off
                back.append((other["name"], write_failure(e, install_dir)
                             if isinstance(e, OSError) else str(e)))
                # and its DLC files, which are no use with nothing listing them
                try:
                    loader_mods.remove_parts(install_dir, store, other["name"])
                except (loader_mods.LoaderError, OSError):
                    pass
            finally:
                if work is not None:
                    clear_tree(work)
        return back

    def install_mod(self, ui_game_id: str, file_path: str, origin: str | None = None,
                    version: str | None = None, on_line=None, cancel=None,
                    on_phase=None) -> dict:
        """
        whatever a mod dropped on the drawer turns out to be, put where it goes.

        HD Projects go to their options, a file a listed patch names goes to
        that patch, game files go into the archives entry by entry, .dll and
        .asi go to NovaFix's add-ons, and anything else gets a sentence saying
        what to do with it. `origin` is the archive a folder came out of (the
        mod's name), `version` the folder picked when a mod has versions.
        """
        from patcher.mods import game_files
        from patcher.mods.hd_games import GAMES, identify
        from patcher.mods.hd_recipe import names_in_archive
        from patcher.mods.titles import title as titled

        manifest = self._manifests.get(ui_game_id)
        install_dir = self.install_dir(ui_game_id)
        if manifest is None or install_dir is None:
            return {"ok": False, "error": "couldn't find the game"}
        path = Path(file_path)
        if not path.exists():
            return {"ok": False, "error": f"{path.name} is not there any more."}
        origin = Path(origin) if origin else path
        name = game_files.mod_name(origin)

        def phase(word: str, **more) -> None:
            # what the drawer's line says it's doing, so a long unpack and a
            # long install don't read as one "Loading"
            if on_phase is not None:
                on_phase(phase=word, name=more.pop("name", None) or titled(name), **more)

        if path.is_dir():
            game, found = identify(path)
            if found is not None:
                return {"ok": False, "kind": "hdproject", "jobId": game.job_id, "detail": found}
        elif path.name.lower().endswith(".zip"):
            names = names_in_archive(path)
            if names and any(game.holds(names) for game in GAMES):
                return {"ok": False, "kind": "maybe-hdproject"}

        unpacked = None
        if path.is_file() and path.name.lower().endswith((".rar", ".7z")):
            # the only way to read one is to unpack it, and if it's the HD
            # Project that unpack is the one its install goes on to use
            phase("unpack")
            unpacked, problem = self._unpack_for_project(
                path, lambda done, of: phase("unpack", done=done, of=of, bytes=True))
            if unpacked is None:
                return {"ok": False, "error": problem or (
                    f"Windows couldn't open {path.name}. Unpack it yourself and drop the folder "
                    "instead.")}
            game, found = identify(unpacked)
            if found is not None:
                return {"ok": False, "kind": "hdproject", "jobId": game.job_id, "detail": found,
                        "unpackedTo": str(unpacked)}
        try:
            phase("read")
            done = self._install_mod(ui_game_id, Path(install_dir), unpacked or path,
                                     origin, name, version, on_line, cancel,
                                     lasting=unpacked is None, phase=phase)
            return self._novafix_said(ui_game_id, done)
        finally:
            if unpacked is not None:
                clear_tree(unpacked)

    def _novafix_said(self, ui_game_id: str, done: dict) -> dict:
        """
        a mod that went in beside the exe and is built on FF13Fix says so as it
        goes in, in the warning colour: NovaFix switches itself off for it,
        which is the frame rate going, and nothing else would say why
        """
        if not (isinstance(done, dict) and done.get("ok") and done.get("kind") in ("loader", "files")):
            return done
        row = next((r for r in self.loader_mods(ui_game_id)
                    if r["name"] == done.get("modName") and r.get("warn", "").startswith("NovaFix")),
                   None)
        if row is None:
            return done
        return dict(done, whole=False, message=" ".join(
            filter(None, (done.get("message"), row["warn"] + "."))))

    def _install_mod(self, ui_game_id, install_dir: Path, path: Path, origin: Path,
                     name: str, version, on_line, cancel, lasting: bool = True,
                     phase=None) -> dict:
        """
        install_mod's reading and installing. `lasting` is whether `path` is
        still there afterwards, which it isn't when it's an unpack of the drop,
        and `phase(word, ...)` is install_mod's, for the drawer's line
        """
        from patcher.download import free_space
        from patcher.mods import (deltas, deps, fomod, game_files, models_plus, patchdata,
                                  recipes, sandbox)
        from patcher.mods.hd_games import GAMES
        from patcher.mods.hd_runner import Cancelled as Stopped
        from patcher.mods.meter import Meter
        from patcher.mods.titles import title as titled

        done = addon = loader = shade = disk = codes = None
        # a drop of separate tweaks, one .bat each, and the name the drop goes by
        picks, base = [], name
        # what a drop's records were built into, and a version of it taken off
        # from under another mod first, with how that one went back on
        work, extra, rebuilt = None, None, {"took": [], "back": []}
        spills = None
        # a .bat played on a copy of the game, and the stage it left its result in
        played = staged = None
        # FFXIIIwdbLoad's .txts it played, kept so a Remove under it can replay them
        edits = []
        try:
            with game_files.Source(path) as source:
                members = source.members()
                for outer in source.nested:
                    if not any(each.holds(source.listing(outer)) for each in GAMES):
                        continue
                    if lasting and source.root == Path(path):
                        # a zip on disk in a dropped folder: opened as if it
                        # had been dropped itself
                        return {"ok": False, "kind": "maybe-hdproject",
                                "path": str(Path(path) / outer)}
                    return {"ok": False, "error": (
                        f"{outer.rsplit('/', 1)[-1]} inside {origin.name} is the HD Project. "
                        "Take it out and drop it on the window by itself.")}
                # a FOMOD's options, asked a group at a time the way a mod's
                # versions are, and what the answers come to is read from
                # then on like any drop
                try:
                    config = fomod.read(source, members)
                except fomod.FomodError as e:
                    return {"ok": False, "error": f"{titled(name)}'s installer options won't "
                                                  f"read: {e}. Unpack it and drop the folder "
                                                  "of the option you want."}
                if config is not None:
                    picked = self._fomod_answered(str(origin), config, version, name)
                    if isinstance(picked, dict):
                        return picked
                    picked, answered = picked
                    chosen = fomod.installed(config, picked, members)
                    if not chosen:
                        return {"ok": False, "error": f"the options picked for {titled(name)} "
                                                      "come to no files. Drop it again and "
                                                      "pick others."}
                    # the answer to its last question isn't a version of what
                    # it comes to, which can still have versions of its own
                    source = fomod.Chosen(source, chosen)
                    version = None if answered else version
                    members = source.members()
                game = game_files.read_game(install_dir)
                # a base mod add-ons run tools out of, HD Models Plus say: its
                # tool folder's kept, so an add-on for it never needs it again
                try:
                    deps.remember(source, members, game.data)
                except Exception:
                    if self._log:
                        self._log.exception("keeping %s's tools failed", name)
                # a Nova Chrysalia modpack names itself and its game. Nova
                # turns down a pack whose GameEntry isn't the game it's
                # installing to, so one that works there says the right game
                nova = game_files.nova_config(source)
                name = nova.get("name") or name
                if nova.get("game") and game.data and nova["game"] != game.data:
                    return {"ok": False, "error": f"{name} is a Nova Chrysalia modpack for "
                                                  f"{game_files.DATA_DIRS[nova['game']]}. Switch "
                                                  "to that game and drop it again."}
                # its versions aren't folders in the drop, they're which of its
                # recipes run, so it never gets as far as look()'s versions
                if models_plus.is_main_download(members):
                    return self._models_plus(ui_game_id, install_dir, source, members, origin,
                                             version, on_line, cancel, phase)
                # Surihix's LR patchers, whose PatchData.bin says what they do
                plan = patchdata.find(source, members)
                if plan is not None:
                    return self._patchdata(ui_game_id, install_dir, source, plan, origin, name,
                                           on_line, cancel, phase)
                # a .bat around its author's tools, done the way it reads rather
                # than its files going in on their own without what it does
                recipe = recipes.find(source, members)
                if recipe is not None:
                    return self._recipe(install_dir, source, recipe, origin, name, version,
                                        on_line, cancel, phase)
                # any other .bat: played on a copy of the game, and what it left
                # the game's files as is the drop from here on
                # each of a pack of tweaks is a mod of its own, so a second one
                # picked goes in beside the first rather than over it as its
                # update, and "in already" means that one's in
                picks = sandbox.tweaks(source, members, game.data) if version else []
                tweak = game_files.picked(version.partition(" / ")[0], {t: t for t in picks}) \
                    if picks else None
                if tweak is not None:
                    name = f"{base} ({sandbox.tweak_name(tweak)})"
                in_game, went_over = self._on_record(install_dir, name)

                played = sandbox.play(install_dir, source, members, base, version, cancel,
                                      on_line, progress=lambda n, of: phase(
                                          "build", done=n, of=of) if phase else None,
                                      in_game=in_game, went_over=went_over,
                                      lenders=self._lenders(install_dir, origin, game.data))
                if played is not None and played.versions:
                    shown = titled(name)
                    top = version is None and bool(sandbox.tweaks(source, members, game.data))
                    return {"ok": False, "kind": "versions", "modName": name, "title": shown,
                            "versions": played.versions, "tweaks": top,
                            "done": played.current,
                            "error": f"{shown} comes in {len(played.versions)} versions. Pick "
                                     "the one you want."}
                if played is not None and played.own:
                    # its "Original" picked, which is no mod at all
                    shown = titled(name)
                    pick = version.rsplit(" / ", 1)[-1].strip()
                    said = (f"{pick} is how the game already is, so nothing changed."
                            if played.own == "already" else
                            f"{pick} puts the game's own back. That's Remove on {shown} under "
                            "Installed.")
                    return {"ok": True, "kind": "files", "modName": name, "title": shown,
                            "matchedExisting": True, "whole": True, "preserved": [],
                            "rejected": [], "summary": said, "message": "", "error": None,
                            **self._tweaks_left(ui_game_id, base, picks if tweak else [])}
                if played is not None and played.already:
                    # dropped again, or put in by hand before: the drop's own
                    # files would only be a second guess at what its .bat did
                    shown = titled(name)
                    bat = played.bat.replace("\\", "/").rsplit("/", 1)[-1]
                    return {"ok": True, "kind": "files", "modName": name, "title": shown,
                            "matchedExisting": True, "whole": True, "preserved": [],
                            "rejected": [], "summary": f"{shown} is in already.",
                            "message": f"Played its {bat} on a copy of the game, and the mod "
                                       "is in already, so nothing changed.",
                            "error": None,
                            **self._tweaks_left(ui_game_id, base, picks if tweak else [])}
                if played is not None and played.kind == "othergame":
                    # its installer checks for another of the three, so its
                    # files going in here on their own would be that game's
                    # mod in this one
                    bat = played.bat.replace("\\", "/").rsplit("/", 1)[-1]
                    return {"ok": False, "error": f"{name} wasn't installed: its {bat} "
                                                  f"{played.problem}.", "detail": played.detail}
                if played is not None and played.work is None and version is not None and \
                        game_files.picked(version, game_files.look_in(
                            source, game, root=base).keys) is None:
                    # the pick was one of its .bats or menus and the play used
                    # it up. The drop's own files never had that version, and
                    # asking them would only bury why the play put nothing in
                    bat = played.bat.replace("\\", "/").rsplit("/", 1)[-1]
                    return {"ok": False, "detail": played.detail, "error": (
                        f"{name} wasn't installed: its {bat} {played.problem}." if played.problem
                        else f"{name} wasn't installed: its {bat} ran but changed nothing in "
                             "the game.")}
                if played is not None and played.work is not None:
                    staged = played.work.parent
                    # a zip it left in the game's folder is a file there, not
                    # more of the mod to open
                    source = game_files.Source(played.work, open_nested=False)
                    members, version = source.members(), None
                    edits = [txt.decode("latin-1") for txt in played.wdb]
                found = game_files.look_in(source, game, version=version, root=base)
                if staged is not None:
                    game_files.placed(found, members)
                other = game_files.wrong_game(found, game)
                if other:
                    return {"ok": False, "error": f"{name}'s files are laid out for {other}. "
                                                  "Switch to that game and drop it again."}
                if found.unmatched and not found.picks and not found.clashes and game.pairs:
                    # game files that are another game's, and a .dll among
                    # them would have gone into this one on its own
                    other = self._for_other(ui_game_id, found, members)
                    if other:
                        return {"ok": False, "error": f"{name} is for {other}. Switch to that "
                                                      "game and drop it again."}
                # a played drop had its version picked before the play, so a
                # clash in what it laid out is left out and said, not asked
                if found.versions and version is None and staged is None:
                    return {"ok": False, "kind": "versions", "modName": name,
                            "title": titled(name), "versions": found.versions,
                            "error": f"{name} comes in {len(found.versions)} versions. Pick the "
                                     "one you want."}
                # xdelta patches named for one of the game's files: a loose
                # one's decoded whole and goes in over it, an index and
                # archive pair's the way the Console Content Patch's do
                patched, plan = {}, None
                if found.deltas:
                    plan, loose, left = deltas.sort(found.deltas, source.read, game)
                    found.deltas, found.delta_why = list(left), dict(left)
                    for n, (member, rel) in enumerate(loose.items()):
                        # decoded onto the game's drive rather than into memory,
                        # since what it patches can be a movie
                        spills = spills or deltas.spill(install_dir)
                        made, raw = spills / str(n), source.read(member)
                        try:
                            # the decoded copy, then the copy going in beside it
                            need = 2 * deltas.size(raw) + (64 << 20)
                            if free_space(spills) < need:
                                return {"ok": False, "error": (
                                    f"{name} wasn't installed: {rel.rsplit('/', 1)[-1]} needs "
                                    f"{need / 1024 ** 3:.1f} GB free on the game's drive to be "
                                    "patched, and it hasn't got that. Free some up and drop it "
                                    "again.")}
                            with made.open("w+b") as out:
                                size = deltas.whole(raw, install_dir / rel, out)
                        except deltas.DeltaError as e:
                            # a full drive is an OSError, and said as one further out
                            made.unlink(missing_ok=True)
                            found.deltas.append(member)
                            found.delta_why[member] = (f"it was made from another version of "
                                                       f"{rel.rsplit('/', 1)[-1]} ({e})")
                            continue
                        patched[member] = made
                        found.disk[member], found.sizes[member] = rel, size
                    if plan is not None and (found.picks or found.code or found.loader_files
                                             or found.reshade_files or found.disk
                                             or found.records):
                        # two installs as one, a build's records under files of
                        # its own, isn't something to guess the order of
                        for pair in plan.pairs.values():
                            for half in (pair.index_patch, pair.archive_patch):
                                found.deltas.append(half)
                                found.delta_why[half] = ("it patches the game's archives "
                                                         "whole, which goes in on its own: drop "
                                                         "the patches by themselves")
                        plan = None
                lead = None
                if plan is not None:
                    lead = ("Patched " + " and ".join(sorted(p.archive.rsplit("/", 1)[-1]
                                                             for p in plan.pairs.values()))
                            + " the way its xdelta patches say, record by record, without "
                              "writing the archives out again.")
                elif found.whole:
                    # an index and its archive shipped whole, dragged over the
                    # game's by hand the author's way: only what's different
                    # from Steam's goes in, record by record
                    plan, left = recipes.wholes(found.whole, game)
                    found.whole_why.update(left)
                    halves = {m for p in (plan.pairs.values() if plan else ())
                              for m in (p.index_whole, p.archive_whole)}
                    found.whole = [m for m in found.whole if m not in halves]
                    if plan is not None and (found.picks or found.code or found.loader_files
                                             or found.reshade_files or found.disk
                                             or found.records or found.whole):
                        for half in sorted(halves):
                            found.whole.append(half)
                            found.whole_why[half] = ("it goes in on its own, taken apart record "
                                                     "by record: drop it by itself")
                        plan = None
                    elif plan is not None:
                        archives = sorted(p.archive.rsplit("/", 1)[-1] for p in plan.pairs.values())
                        lead = (f"Put in only what its {' and '.join(archives)} "
                                f"change{'s' if len(archives) == 1 else ''} from Steam's, record "
                                "by record, rather than swapping in its whole "
                                f"archive{'' if len(archives) == 1 else 's'}.")
                if plan is not None:
                    return self._recipe(install_dir, source,
                                        recipes.Recipe(bat="", options=[plan]), origin, name,
                                        None, on_line, cancel, phase, lead=lead)
                # records inside one of the game's files, through WPDtool's
                # folders: what they go into is built over what the game has
                # now and goes in with the drop's other files as one mod
                if found.records:
                    work, extra = self._records_in(install_dir, source, found, name, origin,
                                                   rebuilt, phase)
                    source = patchdata.Joined(source, work)
                shown = titled(name)
                # a ReShade preset on its own, for the ReShade that's in
                if not (found.picks or found.code or found.loader_files or found.reshade_files
                        or found.reshade or found.disk):
                    preset = game_files.presets_in(members, source.read)
                    usable = [m for m in found.shade_addons
                              if not m.lower().endswith(".addon64")]
                    if preset[0] or usable:
                        put = self._preset_from(ui_game_id, install_dir, source, preset, name,
                                                origin) if preset[0] else \
                            self._shade_addon_from(ui_game_id, install_dir, source, usable,
                                                   members, name, origin)
                        if not put.get("ok"):
                            return {"ok": False, "error": put.get("error")}
                        name = put.get("name") or name
                        return {"ok": True, "kind": "loader", "modName": name,
                                "title": titled(name), "matchedExisting": bool(put.get("updated")),
                                "whole": True, "preserved": [], "rejected": [],
                                "summary": f"{'Updated' if put.get('updated') else 'Installed'} "
                                           f"{titled(name)}.",
                                "message": put["message"], "error": None}
                named = "ReShade" if found.reshade_files and not (
                    found.picks or found.code or found.loader_files) else shown
                going = found.picks or found.code or found.loader_files or found.reshade_files \
                    or found.disk or found.patches
                if phase is not None and going:
                    phase("install", name=named)

                def far(n, of):
                    phase("install", name=named, done=n, of=of)

                # one percent over every part, the game's files and then what
                # goes beside the exe, so a hundred means the last of them is in
                meter = Meter(far if phase is not None and going else None)
                meter.split(files=game_files.weigh(install_dir, found) if found.picks else 0,
                            code=_beside(found, found.code),
                            loader=_beside(found, found.loader_files),
                            shade=_beside(found, found.reshade_files),
                            disk=_beside(found, found.disk))
                # a mod of loose files alone by its name is another one unless
                # this drop has one of its files, so the rest takes another name
                strangers = self._disk_strangers(install_dir, found)
                if found.picks:
                    done = game_files.install(
                        install_dir, undo_store_for(install_dir), name, found, source,
                        origin=str(origin), on_line=on_line, cancel=cancel,
                        progress=lambda n, of: meter.at("files", n, of),
                        extra={**(extra or {}), "wdb": edits} if edits else extra,
                        others=strangers)
                if found.code:
                    # on its own: the game files above may be in already, and
                    # a .dll that won't copy mustn't say they aren't
                    try:
                        addon = self._addon_from(
                            ui_game_id, source, name, found.code,
                            skip=set(found.loader_files) | set(found.x64) | set(found.x64_files)
                            | set(found.blocked_files)
                            | game_files.reshade_side(found))
                    except OSError as e:
                        addon = {"ok": False, "error": "Its .dll didn't go in: "
                                                       + write_failure(e, install_dir)}
                    except Exception as e:
                        if self._log:
                            self._log.exception("the add-on side of %s failed", name)
                        addon = {"ok": False, "error": f"Its .dll didn't go in: {e}"}
                    meter.at("code", 1, 1)
                if found.loader_files:
                    loader = self._loader_from(ui_game_id, install_dir, source, found, name,
                                               origin, lambda n, of: meter.at("loader", n, of),
                                               others=strangers)
                if found.reshade_files:
                    shade = self._reshade_from(ui_game_id, install_dir, source, found, origin,
                                               lambda n, of: meter.at("shade", n, of))
                if found.disk:
                    # under the name its other files went in by, "Pack (2)"
                    # when another mod had it, so they're one row
                    named = (done or {}).get("name") or \
                        ((loader or {}).get("name") if (loader or {}).get("ok") else None)
                    disk = self._disk_from(ui_game_id, install_dir, source, found,
                                           named or name, origin,
                                           lambda n, of: meter.at("disk", n, of),
                                           read=lambda m: patched[m].read_bytes()
                                           if m in patched else source.read(m),
                                           opened=lambda m: patched[m].open("rb")
                                           if m in patched else source.opened(m),
                                           alone=named is None)
                if found.patches:
                    # one Remove for the lot when its game files went in
                    whose = done.get("name") if (done or {}).get("ok") else None
                    codes = self._codes_from(ui_game_id, install_dir, source, found.patches,
                                             whose or name, origin, part=whose is not None,
                                             others=strangers)
                    # said by how that went rather than as left out
                    found.patches = []
                meter.end()
        except (game_files.FilesError, patchdata.BuildError, sandbox.SandboxError) as e:
            return {"ok": False, "error": f"{name} wasn't installed: {e}" + _rebuilt_said(rebuilt)}
        except Stopped:
            return {"ok": False, "error": f"Stopped. {name} wasn't installed."
                                          + _rebuilt_said(rebuilt)}
        except OSError as e:
            return {"ok": False,
                    "error": f"{name} wasn't installed: {write_failure(e, install_dir)}"
                             + _rebuilt_said(rebuilt)}
        except Exception as e:
            # a zip that's half downloaded, an index that won't parse: said,
            # never a raw traceback in the drawer
            if self._log:
                self._log.exception("installing %s failed", name)
            return {"ok": False, "error": f"Couldn't read {origin.name}: {e}"
                                          + _rebuilt_said(rebuilt)}
        finally:
            for folder in (work, spills, staged):
                if folder is not None:
                    clear_tree(folder)

        if done is None and addon is None and loader is None and shade is None and disk is None \
                and codes is None:
            # a .bat around a 4GB patcher and nothing else is said as that,
            # which Apply does
            if played is not None and played.problem and not (
                    played.idle and game_files.LARGE_ADDRESS.search(
                        " ".join([played.bat.rsplit("/", 1)[-1], *played.ran]).lower())):
                bat = played.bat.rsplit("/", 1)[-1]
                # what to do about it, and what showed it, behind the line's (i)
                return {"ok": False, "error": f"{name} wasn't installed: its {bat} "
                                              f"{played.problem}.", "detail": played.detail}
            return {"ok": False, "error": self._nothing_in(ui_game_id, found, game, members,
                                                           name)}
        said = []
        if done is not None:
            # another mod already had the name, so this one got a number
            name = done.get("name", name)
            said.append(_files_said(done, name))
            if (extra or {}).get("records"):
                said.append(_records_said(extra["records"]))
        elif (loader or {}).get("ok"):
            name = loader.get("name") or name
        elif addon is None and loader is None and shade is not None:
            # ReShade on its own is what it is, whatever its download was called
            name = (shade or {}).get("name") or "ReShade"
        elif (disk or {}).get("ok") and addon is None and loader is None:
            name = disk.get("name") or name
        elif (codes or {}).get("ok") and addon is None and loader is None and disk is None:
            name = codes.get("name") or name
        said += _back_said(rebuilt)
        if addon is not None:
            said.append(f"{addon.get('modName') or name} went into NovaFix's add-ons."
                        if addon.get("ok") else
                        (addon.get("error") or "Its .dll didn't go in."))
        if loader is not None:
            said.append(_loader_said(loader, name, self.game_executable(ui_game_id)))
        if shade is not None:
            said.append(_reshade_said(shade, self.game_executable(ui_game_id)))
        if disk is not None:
            said.append(_disk_said(disk, name, alone=not said))
        if codes is not None:
            said.append(_codes_said(codes, name))
        for extra in (game_files.left_out(found, played.problem if played else "",
                                          played.bat if played else ""),
                      (done or {}).get("note", "")):
            if extra:
                said.append(extra)
        if staged is not None:
            # how it went in leads, and what it tried that didn't comes last
            said = [_played_said(played), *said,
                    *[line for line in [_skipped_said(played), *played.notes] if line]]
        if self._log:
            self._log.info("mod %s: %s", name, " ".join(said))
        parts = (done, addon, loader, shade, disk, codes)
        ok = any((part or {}).get("ok", False) for part in parts)
        updated = bool((done or {}).get("updated") or (addon or {}).get("matchedExisting")
                       or (loader or {}).get("updated") or (shade or {}).get("updated")
                       or (disk or {}).get("updated") or (codes or {}).get("updated"))
        whole = all((part or {}).get("ok", False) for part in parts if part is not None) \
            and not game_files.lost_any(found)
        summary = _summed(titled(name), done, updated, whole,
                          alone=addon is None and loader is None and shade is None
                          and disk is None and codes is None)
        for side in (shade, codes):
            if (side or {}).get("ok") and side.get("warn"):
                # installed and still not going to load: that's the line, not a detail
                summary += f" {side['warn']}"
        return {"ok": ok,
                "kind": "files" if done else "loader" if loader or shade or codes
                else "disk" if disk else "addon",
                "modName": name, "title": titled(name), "matchedExisting": updated,
                "whole": whole,
                "preserved": (addon or {}).get("preserved") or [],
                "rejected": (addon or {}).get("rejected") or [],
                "summary": summary if ok else "",
                "message": " ".join(said), "error": None if ok else " ".join(said),
                **self._tweaks_left(ui_game_id, base, picks if ok and name != base else [])}

    def _tweaks_left(self, ui_game_id: str, base: str, picks: list) -> dict:
        """
        a pack of tweaks' list again once one's in, with the ones in ticked,
        so the next is a pick and Install rather than the download dropped again
        """
        from patcher.addon_sieve import stamped
        from patcher.mods.sandbox import tweak_name
        from patcher.mods.titles import title as titled

        if not picks:
            return {}
        # one that went in under its download's whole stamped name is in too
        names = {n.lower() for m in self.file_mods(ui_game_id) + self.loader_mods(ui_game_id)
                 + self.disk_mods(ui_game_id)
                 for n in (str(m.get("name", "")), stamped(str(m.get("name", "")))[0])}
        return {"again": {"modName": base, "title": titled(base), "versions": list(picks),
                          "tweaks": True,
                          "done": [t for t in picks
                                   if f"{base} ({tweak_name(t)})".lower() in names]}}

    def _models_plus(self, ui_game_id, install_dir: Path, source, members, origin: Path,
                     version, on_line, cancel, phase) -> dict:
        """
        HD Models Plus's main download: its versions offered, then the one
        picked built with its own tool and put in the way any drawer mod is.
        models_plus says why it's done this way rather than with its .bat
        """
        from patcher.fileops import clear_tree
        from patcher.mods import game_files, models_plus
        from patcher.mods.hd_runner import Cancelled as Stopped

        name = models_plus.NAME
        game = game_files.read_game(install_dir)
        if game.data != "white_data":
            return {"ok": False, "error": f"{name} is for Final Fantasy XIII. Switch to that game "
                                          "and drop it again."}
        root = models_plus.root_of(members)
        if root is None:
            return {"ok": False, "error": f"{name}'s download is missing its SupportFiles "
                                          "folder. Download it again and drop that."}
        if version is None:
            # its Worth getting row, which the drawer opens the choice under the
            # way the HD Project's options open under its own
            manifest = self._manifests.get(ui_game_id)
            job = next((p.ui_job_id for p in (manifest.patches if manifest else ())
                        if p.label == name and p.ui_job_id), None)
            return {"ok": False, "kind": "versions", "modName": name, "title": name, "jobId": job,
                    "versions": list(models_plus.VERSIONS), "about": dict(models_plus.ABOUT),
                    "recommended": models_plus.RECOMMENDED,
                    "error": f"{name} comes in {len(models_plus.VERSIONS)} versions. Pick the "
                             "one you want."}
        version = game_files.picked(version, models_plus.VERSIONS) or version
        # the HD Project's own cache of originals, one more place to find a
        # model the game has modded and the backup doesn't
        try:
            hd_root = (self.hd_source(ui_game_id)[1] or {}).get("root")
        except Exception:
            hd_root = None

        def said(word, **more):
            if phase is not None:
                phase(word, name=name, **more)

        work = None
        try:
            said("build")
            work = models_plus.build(install_dir, source, root, version, on_line=on_line,
                                     cancel=cancel, hd_root=hd_root,
                                     progress=lambda n, of: said("build", done=n, of=of))
            with game_files.Source(models_plus.built_at(work)) as built:
                found = game_files.look_in(built, game, root=name)
                said("install")
                done = game_files.install(install_dir, undo_store_for(install_dir), name, found,
                                          built, origin=str(origin), on_line=on_line,
                                          cancel=cancel, extra={"version": version},
                                          progress=lambda n, of: said("install", done=n, of=of))
        except (models_plus.ModelsError, game_files.FilesError) as e:
            return {"ok": False, "error": f"{name} wasn't installed: {e}"}
        except Stopped:
            return {"ok": False, "error": f"Stopped. {name} wasn't installed."}
        except OSError as e:
            return {"ok": False, "error": f"{name} wasn't installed: "
                                          f"{write_failure(e, install_dir)}"}
        except Exception as e:
            if self._log:
                self._log.exception("building %s failed", name)
            return {"ok": False, "error": f"{name} wasn't installed: {e}"}
        finally:
            if work is not None:
                clear_tree(work)

        shown = done.get("name", name)
        lines = [f"Built its {version} models for all six with its own tool.",
                 _files_said(done, shown)]
        if done.get("note"):
            lines.append(done["note"])
        if self._log:
            self._log.info("mod %s: %s", shown, " ".join(lines))
        return {"ok": True, "kind": "files", "modName": shown, "title": shown,
                "matchedExisting": bool(done.get("updated")), "whole": True,
                "preserved": [], "rejected": [],
                "summary": _summed(shown, done, bool(done.get("updated")), True),
                "message": " ".join(lines), "error": None}

    def _recipe(self, install_dir: Path, source, recipe, origin: Path, name: str, version,
                on_line, cancel, phase, lead: str | None = None) -> dict:
        """
        a mod that's a .bat around its author's tools, put in the way the .bat
        reads (recipes.py), as one mod in the drawer: what its xdelta patches
        really change decoded out of them, then the files it injects
        """
        from patcher.fileops import clear_tree
        from patcher.mods import game_files, recipes
        from patcher.mods.hd_runner import Cancelled as Stopped
        from patcher.mods.titles import title as titled

        shown = titled(name)
        chosen, versions = recipes.steps(recipe)
        if versions:
            if version is None:
                return {"ok": False, "kind": "versions", "modName": name, "title": shown,
                        "versions": versions,
                        "error": f"{shown} comes in {len(versions)} versions. Pick the one you "
                                 "want."}
            mine = game_files.picked(version, versions)
            chosen = [o for o in recipe.usable() if mine is not None and o.label == mine]
            if not chosen:
                return {"ok": False, "error": f"{version} isn't one of its versions. Drop it "
                                              "again to pick one."}

        def said(word, **more):
            if phase is not None:
                phase(word, name=shown, **more)

        work = None
        try:
            said("build")
            work, found = recipes.build(install_dir, source, chosen,
                                        progress=lambda n, of: said("build", done=n, of=of))
            with game_files.Source(work) as built:
                said("install")
                done = game_files.install(
                    install_dir, undo_store_for(install_dir), name, found, built,
                    origin=str(origin), on_line=on_line, cancel=cancel,
                    extra={"recipe": recipe.bat.rsplit("/", 1)[-1],
                           "steps": [o.label for o in chosen]},
                    progress=lambda n, of: said("install", done=n, of=of))
        except (recipes.BuildError, game_files.FilesError) as e:
            return {"ok": False, "error": f"{shown} wasn't installed: {e}"}
        except Stopped:
            return {"ok": False, "error": f"Stopped. {shown} wasn't installed."}
        except OSError as e:
            return {"ok": False, "error": f"{shown} wasn't installed: "
                                          f"{write_failure(e, install_dir)}"}
        except Exception as e:
            if self._log:
                self._log.exception("putting %s in by its .bat failed", name)
            return {"ok": False, "error": f"{shown} wasn't installed: {e}"}
        finally:
            if work is not None:
                clear_tree(work)

        name = done.get("name", name)
        shown = titled(name)
        lines = [lead or (f"Did what {recipe.bat.rsplit('/', 1)[-1]} does without running it"
                          + (f": {', then '.join(o.label for o in chosen if o.label)}." if any(
                              o.label for o in chosen) else ".")), _files_said(done, shown)]
        left = [o for o in recipe.options if o.problem and o not in chosen]
        if left and left[0].label:
            lines.append(f"Its {left[0].label} option isn't done here: {left[0].problem}.")
        if done.get("note"):
            lines.append(done["note"])
        if self._log:
            self._log.info("mod %s: %s", name, " ".join(lines))
        return {"ok": True, "kind": "files", "modName": name, "title": shown,
                "matchedExisting": bool(done.get("updated")), "whole": True,
                "preserved": [], "rejected": [],
                "summary": _summed(shown, done, bool(done.get("updated")), True),
                "message": " ".join(lines), "error": None}

    def _records_in(self, install_dir: Path, source, found, name: str, origin, rebuilt: dict,
                    phase=None) -> tuple:
        """
        (the folder they're built in, the record's extra) for a drop's records
        inside the game's containers (patchdata.lay): each container built over
        what the game has now and put among found's picks. A version of it
        already in comes off first the way Remove takes it: built over that
        one, it would keep whatever records the new one leaves out, and under
        another mod it's that one's copy too
        """
        from patcher.fileops import clear_tree
        from patcher.mods import game_files, patchdata
        from patcher.mods.titles import title as titled

        store = undo_store_for(install_dir)
        game = game_files.read_game(install_dir)
        touches = {(pair.name(install_dir), path) for path, voice in found.records
                   for pair in patchdata._wanted(game, path) if not voice or pair.voice == voice}
        earlier = game_files._going(store, name, touches, str(origin))[0]
        older = next((r for r in earlier if r.get("records")), None)
        if older is not None:
            # said as off only once it is: a held archive refuses before
            # anything moves, and the old version's still in
            if _over(install_dir, older):
                rebuilt["back"] = self._remove_rebuilt(install_dir, store, older)[1]
            else:
                game_files.remove(install_dir, store, older["name"])
            rebuilt["took"].append(older["name"])
            if older.get("dlc"):
                # one of the patchers by that name, whose DLC files are its.
                # One held open stays listed under its name until it comes off
                from patcher.mods import loader_mods

                try:
                    loader_mods.remove_parts(install_dir, store, older["name"])
                except (loader_mods.LoaderError, OSError):
                    pass
        shown = titled(name)
        if phase is not None:
            phase("build", name=shown)
        work, built, changed = patchdata.lay(
            install_dir, found.records, source.read,
            progress=(lambda n, of: phase("build", name=shown, done=n, of=of)) if phase else None)
        try:
            for path, voice in built.picks:
                if any(path == theirs for theirs, _ in list(found.picks) + list(found.clashes)):
                    raise game_files.FilesError(f"it has {path} whole and records inside it too, "
                                                "with nothing to say which goes in")
            found.picks.update(built.picks)
            found.sizes.update(built.sizes)
            found.matched |= built.matched
            found.voices.update(built.voices)
            for path, those in built.members_at.items():
                found.members_at.setdefault(path, []).extend(those)
            for path, names in built.pairs_for.items():
                found.pairs_for.setdefault(path, set()).update(names)
        except BaseException:
            clear_tree(work)
            raise
        return work, {"records": changed}

    def _patchdata(self, ui_game_id: str, install_dir: Path, source, plan, origin: Path,
                   name: str, on_line, cancel, phase) -> dict:
        """
        one of Surihix's LR patchers, put in the way its PatchData.bin reads
        (patchdata.py): its DLC files beside the game as a part of it, then the
        container it changes built again in every voice set and put in as a
        drawer mod, which Remove takes the DLC files off with. That order, since
        DLC files nothing lists do no harm and the game listing DLC it hasn't
        got does
        """
        from patcher.fileops import clear_tree
        from patcher.mods import game_files, loader_mods, patchdata
        from patcher.mods.hd_runner import Cancelled as Stopped
        from patcher.mods.titles import title as titled

        shown = titled(name)
        store = undo_store_for(install_dir)

        def said(word, **more):
            if phase is not None:
                phase(word, name=shown, **more)

        work = done = part = None
        back, took = [], []

        def failed(why: str) -> dict:
            try:
                if part is not None:
                    # a first install's DLC files go back off with it
                    loader_mods.remove(install_dir, store, part)
                elif took:
                    # and the old version's are no use with its records gone
                    loader_mods.remove_parts(install_dir, store, name)
            except (loader_mods.LoaderError, OSError):
                pass
            lines = [why]
            if took:
                lines.append("The version that was in came off first, so drop that one again to "
                             "have it back.")
            lines += [f"{other} went back on without it." if problem is None else
                      f"{other} had to come off for it and couldn't go back on ({problem}), so "
                      "drop it again to put it back." for other, problem in back]
            return {"ok": False, "error": " ".join(lines)}

        try:
            # the name it goes in under, worked out the way the install will:
            # the version already in when it's an update, whatever its case
            earlier, name = game_files._going(store, name, patchdata.touches(install_dir, plan),
                                              str(origin))
            # one of those under another patcher's: an update would go in over
            # that one and take the old version off from under it, which the
            # one between has built its copy of the container on. So the old
            # one comes off first the way Remove takes it, and this goes on
            # over what's left
            older = next((r for r in earlier if r.get("records") and _over(install_dir, r)), None)
            if older is not None:
                # taken only once it's off: a refusal moves nothing, and its
                # DLC files are still its own
                back = self._remove_rebuilt(install_dir, store, older)[1]
                took.append(older["name"])
            said("build")
            work, found, loose, changed = patchdata.build(
                install_dir, source, plan, progress=lambda n, of: said("build", done=n, of=of))
            if loose:
                had = any(r.get("part") == name for r in loader_mods.records(store))
                # from the game's own folder, which the full Revert has too
                put = loader_mods.install(install_dir, store, name, loose, source.read,
                                          origin=str(origin), extra={"part": name},
                                          owners=self._disk_owners(install_dir, install_dir))
                part = None if had else put["name"]
            with game_files.Source(work) as built:
                said("install")
                done = game_files.install(
                    install_dir, store, name, found, built, origin=str(origin), on_line=on_line,
                    cancel=cancel, extra={"recipe": plan.exe, "records": changed,
                                          "dlc": sorted(loose.values())},
                    progress=lambda n, of: said("install", done=n, of=of))
        except (patchdata.BuildError, game_files.FilesError, loader_mods.LoaderError) as e:
            return failed(f"{shown} wasn't installed: {e}")
        except Stopped:
            return failed(f"Stopped. {shown} wasn't installed.")
        except OSError as e:
            return failed(f"{shown} wasn't installed: {write_failure(e, install_dir)}")
        except Exception as e:
            if self._log:
                self._log.exception("putting %s in by its PatchData failed", name)
            return failed(f"{shown} wasn't installed: {e}")
        finally:
            if work is not None:
                clear_tree(work)

        name = done.get("name", name)
        shown = titled(name)
        what = " and ".join(sorted({p.rsplit("/", 1)[-1] for p in changed}))
        line = (f"Did what {plan.exe} does without running it: {what} in "
                f"{_voices_said(done.get('sets') or [])}" if changed else
                f"Did what {plan.exe} does without running it")
        if loose:
            line += f", and {len(loose)} DLC files in {_common(loose.values())}"
        lines = [line + "."]
        for other, problem in back:
            lines.append(f"{other} went back on under it." if problem is None else
                         f"{other} had to come off for it and couldn't go back on ({problem}), "
                         "so drop it again to put it back.")
        if done.get("note"):
            lines.append(done["note"])
        if self._log:
            self._log.info("mod %s: %s", name, " ".join(lines))
        return {"ok": True, "kind": "files", "modName": name, "title": shown,
                "matchedExisting": bool(done.get("updated")), "whole": True,
                "preserved": [], "rejected": [],
                "summary": _summed(shown, done, bool(done.get("updated")), True,
                                   alone=not loose),
                "message": " ".join(lines), "error": None}

    def _addon_from(self, ui_game_id: str, source, name: str, code=None, skip=()) -> dict:
        """
        the .dll and .asi side of a drop, through the add-on sieve. Only those
        and their .ini files are copied out for it, so a folder that's mostly
        textures doesn't go through staging whole. `code` is the ones look()
        left for NovaFix, when it's sorted them: a loader's and a .NET
        installer's .dlls aren't add-ons. `skip` is what's a loader's, its
        .ini files included, or its 64-bit build's
        """
        import tempfile

        from patcher import zipnames
        from patcher.addon_sieve import PAYLOAD_EXTENSIONS

        wanted = None if code is None else set(code)
        staging = Path(tempfile.mkdtemp(prefix="ffxiii-addon-",
                                        dir=scratch_near(source.path.parent)))
        try:
            folder = staging / name
            folder.mkdir()
            for member, _ in source.members():
                suffix = Path(member).suffix.lower()
                if suffix not in PAYLOAD_EXTENSIONS or member in skip:
                    continue
                if wanted is not None and suffix != ".ini" and member not in wanted:
                    continue
                target = zipnames.safe_target(folder, member, False)
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read(member))
            return self.install_addon(ui_game_id, str(folder))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _preset_from(self, ui_game_id: str, install_dir: Path, source, found: tuple, name: str,
                     origin) -> dict:
        """
        a ReShade preset into the ReShade that's in, ours or the drawer's
        (game_files.presets_in): the preset beside ReShade.ini, its shaders and
        textures in folders of their own under ReShade's, which it searches
        all the way down, less any ReShade has by that name already, and
        ReShade.ini pointed at the preset when it's the only one. What it
        pointed at before is on the record, so Remove points it back
        """
        import re

        from patcher.mods import game_files, loader_mods
        from patcher.mods.titles import title

        presets, shaders, textures = found
        shown = title(name)
        exe = self.game_executable(ui_game_id)
        home = exe.parent / "ReShade" if exe is not None else None
        settings = home / "ReShade.ini" if home is not None else None
        if home is None or not settings.is_file() or not (home / "d3d9.dll").is_file():
            return {"ok": False, "error": f"{shown} is a ReShade preset, and this game has no "
                                          "ReShade for it yet. Tick Fabula-Lous Shader and press "
                                          "Apply, or drop a ReShade, then drop this again."}
        slug = re.sub(r"[^A-Za-z0-9 _.-]+", " ", shown).strip() or "Preset"
        have = {p.name.lower() for p in (home / "reshade-shaders").rglob("*") if p.is_file()} \
            if (home / "reshade-shaders").is_dir() else set()
        files, kept = {}, 0
        for member in presets:
            files[member] = f"ReShade/{member.replace(chr(92), '/').rsplit('/', 1)[-1]}"
        for kind, those in (("Shaders", shaders), ("Textures", textures)):
            for inside, member in those.items():
                if inside.rsplit("/", 1)[-1].lower() in have:
                    kept += 1
                    continue
                files[member] = f"ReShade/reshade-shaders/{kind}/{slug}/{inside}"
        try:
            done = loader_mods.install(exe.parent, undo_store_for(install_dir), name, files,
                                       source.read, origin=str(origin), adopt=True,
                                       taken=self._ours_beside(ui_game_id, install_dir, exe))
        except (loader_mods.LoaderError, game_files.FilesError) as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": f"{shown} didn't go in: {write_failure(e, exe.parent)}"}
        chosen = [files[m].rsplit("/", 1)[-1] for m in presets]
        line = f"Installed {shown} into ReShade: {len(chosen)} preset{'s' if len(chosen) > 1 else ''}"
        added = len(files) - len(presets)
        if added:
            line += f" and {added} shader and texture file{'' if added == 1 else 's'}"
        if kept:
            line += f", keeping the {kept} ReShade had already"
        line += "."
        if len(chosen) == 1 and not done.get("kept"):
            was = self._point_preset(settings, chosen[0])
            if was is not None:
                record = loader_mods._ours(undo_store_for(install_dir), done["name"])
                if record is not None:
                    record["preset"] = {"was": was, "now": f".\\{chosen[0]}"}
                    loader_mods._write(record)
                line += " ReShade starts on it now, and its menu switches back."
        else:
            line += " Pick it at the top of ReShade's menu."
        return dict(done, message=line)

    def _shade_addon_from(self, ui_game_id: str, install_dir: Path, source, addons: list,
                          members: list, name: str, origin) -> dict:
        """
        a ReShade add-on, ShaderToggler's say, beside the ReShade that's in,
        where ReShade looks for them, with the settings beside it in the drop.
        Only ReShade's build with full add-on support loads one, and the
        Fabula-Lous Shader's is the standard build, so that's said rather than
        put in to do nothing
        """
        from patcher.mods import game_files, loader_mods
        from patcher.mods.titles import title

        shown = title(name)
        exe = self.game_executable(ui_game_id)
        home = exe.parent / "ReShade" if exe is not None else None
        if home is None or not (home / "d3d9.dll").is_file():
            return {"ok": False, "error": f"{shown} is a ReShade add-on, and this game has no "
                                          "ReShade for it yet. It needs ReShade's build with full "
                                          "add-on support: drop that, then drop this again."}
        ours = next((p for p in self.extras_in(ui_game_id)
                     if any(str(s.get("target", "")).lower().startswith("reshade/")
                            for s in p.bundled_files)), None)
        if ours is not None:
            return {"ok": False, "error": f"{shown} is a ReShade add-on, which only ReShade's "
                                          f"build with full add-on support loads, and {ours.label}'s "
                                          f"ReShade is the standard build. Remove {ours.label} "
                                          "under Installed and drop the full add-on build, then "
                                          "drop this again."}
        files = {}
        for addon in addons:
            folder = addon.replace("\\", "/").rpartition("/")[0]
            files[addon] = f"ReShade/{game_files._leaf(addon)}"
            # its settings beside it, which aren't ReShade's own or a preset
            for member, size in members:
                leaf = game_files._leaf(member).lower()
                if member.replace("\\", "/").rpartition("/")[0] == folder \
                        and leaf.endswith(".ini") and leaf not in ("reshade.ini", "d3d9.ini") \
                        and size < 1 << 20 and not game_files.PRESET_LINE.search(source.read(member)):
                    files[member] = f"ReShade/{game_files._leaf(member)}"
        try:
            done = loader_mods.install(exe.parent, undo_store_for(install_dir), name, files,
                                       source.read, origin=str(origin), adopt=True,
                                       taken=self._ours_beside(ui_game_id, install_dir, exe))
        except (loader_mods.LoaderError, game_files.FilesError) as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": f"{shown} didn't go in: {write_failure(e, exe.parent)}"}
        leaves = " and ".join(game_files._leaf(a) for a in addons)
        return dict(done, message=f"Installed {shown} beside ReShade: {leaves}. ReShade loads it "
                                  "if it's the build with full add-on support.")

    @staticmethod
    def _point_preset(settings: Path, preset: str):
        """ReShade.ini's PresetPath set to `preset`, beside it, and what it was, or None"""
        import re

        try:
            raw = settings.read_bytes()
        except OSError:
            return None
        text = raw.decode("utf-8-sig", errors="replace")
        line = re.search(r"(?im)^(\s*PresetPath\s*=\s*)(.*?)(\s*)$", text)
        was = line.group(2) if line else ""
        now = f".\\{preset}"
        if line:
            text = text[:line.start(2)] + now + text[line.end(2):]
        else:
            text = re.sub(r"(?im)^\[GENERAL\][^\n]*\n", lambda m: m.group(0) + f"PresetPath={now}\n",
                          text, count=1) if re.search(r"(?im)^\[GENERAL\]", text) \
                else f"[GENERAL]\nPresetPath={now}\n" + text
        try:
            from patcher.fileops import write_atomic

            write_atomic(settings, (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"")
                         + text.encode("utf-8"))
        except OSError:
            return None
        return was

    @staticmethod
    def _unpoint_preset(settings: Path, pointed: dict) -> None:
        """ReShade.ini back on the preset it had, unless it's been moved on since"""
        import re

        try:
            raw = settings.read_bytes()
        except OSError:
            return
        text = raw.decode("utf-8-sig", errors="replace")
        line = re.search(r"(?im)^(\s*PresetPath\s*=\s*)(.*?)(\s*)$", text)
        if line is None or line.group(2) != pointed.get("now"):
            return
        text = text[:line.start(2)] + str(pointed.get("was") or "") + text[line.end(2):]
        try:
            from patcher.fileops import write_atomic

            write_atomic(settings, (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"")
                         + text.encode("utf-8"))
        except OSError:
            pass

    @staticmethod
    def _lenders(install_dir: Path, origin: Path, data: str = ""):
        """
        the downloads an add-on's .bat may reach into for what it hasn't got:
        the base mods kept for it first, then the ones the drawer put mods in
        from, and the folders beside the drop
        """
        from patcher.mods import deps, disk_files, game_files, loader_mods, sandbox

        store, homes = undo_store_for(install_dir), list(deps.homes(data)) if data else []
        kept, gone = {Path(h).name for h in homes}, {}
        for each in (game_files, disk_files, loader_mods):
            try:
                rows = each.records(store)
            except (OSError, ValueError):
                continue
            for record in rows:
                if not record.get("origin"):
                    continue
                at = Path(str(record["origin"]))
                dep = next((d for d in deps.KNOWN if d.data == data and d.id not in kept
                            and d.label.lower() == str(record.get("name", "")).lower()), None)
                if dep is not None:
                    # a base mod in from before its tools were kept: kept now,
                    # while its download's still there to keep them from
                    if UiBridge._keep_from(at, data, dep.id):
                        kept.add(dep.id)
                        homes.insert(0, deps.keep_root() / dep.id)
                    else:
                        # gone since, or one without its tools, its Nova version say
                        gone[dep.id] = str(at)
                # the download it went in from, a .7z or .rar listed off its
                # headers and only unpacked if it's lent, or the folder it was
                # unpacked to beside it
                for spot in (at, at.with_suffix("")):
                    if spot != origin and (spot.is_dir() or (
                            spot.is_file() and spot.suffix.lower() in (".zip", ".7z", ".rar"))):
                        homes.append(spot)
        return sandbox.Lenders(homes, [origin.parent] if origin.parent != origin else [], data,
                               gone)

    @staticmethod
    def _keep_from(at: Path, data: str, dep_id: str) -> bool:
        """a base mod's tools kept out of the download it went in from, if that's still there"""
        from patcher.mods import deps, game_files

        if not at.exists():
            return False
        try:
            with game_files.Source(at) as source:
                return dep_id in deps.remember(source, source.members(), data)
        except Exception:
            # a download that won't open is only one less place to look
            return False

    @staticmethod
    def _on_record(install_dir: Path, name: str) -> tuple:
        """
        (whether a mod by this name went in through the drawer and all of it's
        still there, what its record says each of its files went in over) for
        a .bat played again: game path or index|record -> (digest, size or
        None), "" for one it added. Not "still there" after Steam put the game
        back, or with one taken off and only kept for an HD restore
        """
        from patcher.mods import disk_files, game_files, loader_mods

        store = undo_store_for(install_dir)
        try:
            files, disk, loader = ([r for r in each.records(store)
                                    if str(r.get("name", "")).lower() == name.lower()]
                                   for each in (game_files, disk_files, loader_mods))
            over = {}
            for record in disk:
                for rel, digest in record["kept"].items():
                    kept = store / str(record.get("keep_dir", "")) / rel
                    over[rel] = (digest or "", kept.stat().st_size if kept.is_file() else None)
                over.update({rel: ("", None) for rel in record["added"]})
            for record in files:
                for spec in record["pairs"]:
                    for item in spec["entries"]:
                        size = (item.get("was") or [None, None])[1]
                        over[f"{spec['index']}|{item['path']}"] = (item.get("under") or "", size)
            live = [r for r in files if not r.get("off")]
            whole = [r for r in disk + loader if not r.get("pending")]
            changed = {row["name"].lower() for row in disk_files.listed(store, install_dir)
                       if row.get("warn")}
            there = bool(live or whole) and name.lower() not in changed and all(
                game_files.state(install_dir, r)["state"] == "in" for r in live)
            return there, over
        except (OSError, KeyError, TypeError, ValueError):
            return False, {}

    @staticmethod
    def _disk_strangers(install_dir: Path, found) -> set:
        """the names of loose-file mods on their own that share nothing with this drop's"""
        from patcher.mods import disk_files

        mine = {rel.lower() for rel in found.disk.values()}
        try:
            return {r["name"].lower() for r in disk_files.records(undo_store_for(install_dir))
                    if not r.get("with") and not mine & {
                        rel.lower() for rel in set(r["files"]) | set(r["kept"]) | set(r["added"])}}
        except OSError:
            return set()

    @staticmethod
    def _disk_owners(install_dir: Path, base: Path) -> dict:
        """path under `base`, lowercased -> the loose-file mod holding it"""
        from patcher.mods import disk_files

        try:
            under = Path(base).relative_to(install_dir).as_posix().lower().strip(".")
            held = disk_files.claimed(undo_store_for(install_dir))
        except (OSError, ValueError):
            return {}
        if not under:
            return dict(held)
        return {key[len(under) + 1:]: who for key, who in held.items()
                if key.startswith(under + "/")}

    def _disk_from(self, ui_game_id: str, install_dir: Path, source, found, name: str, origin,
                   progress=None, read=None, opened=None, alone: bool = False) -> dict:
        """
        the files a mod has that the game reads loose off the disk, its movies
        or LR's DLC, over the game's own with those kept to put back
        (disk_files.py). On their own, like the loader side: the archives may
        be in already, and this failing mustn't say they aren't
        """
        from patcher.mods import disk_files, game_files, loader_mods
        from patcher.mods.titles import title

        store, exe = undo_store_for(install_dir), self.game_executable(ui_game_id)
        # a loader's data folder, LR's HD Fonts and GUI's inside weiss_data,
        # is that mod's: paths there are under the game's folder like these
        taken = {}
        if exe is not None:
            beside = exe.parent.relative_to(install_dir).as_posix()
            for record in loader_mods.records(store):
                for rel in record["files"]:
                    path = f"{beside}/{rel}" if beside != "." else rel
                    taken[path.replace("\\", "/").lower()] = title(record.get("part")
                                                                    or record["name"])
        def fits(rel: str, side: Path) -> str:
            """a zone's archive against the index white_img keeps for it, before it goes in"""
            from patcher.mods.hd_zones import zone_problem

            leaf = rel.rsplit("/", 1)[-1]
            if not (game_files.ZONE_BLOB.match(leaf.lower())
                    and leaf.lower().endswith("_img.win32.bin")):
                return ""
            inner = f"zone/filelist_{leaf.lower().split('_')[1]}.win32.bin"
            for pair in game_files.pairs(install_dir):
                if inner in game_files.entries(pair.index):
                    problem = zone_problem(pair.archive, pair.index, side, name=leaf)
                    return (f"{leaf} doesn't fit the index the game keeps for that zone "
                            f"({problem}), so the game would crash reading it") if problem else ""
            return ""

        try:
            done = disk_files.install(install_dir, store, name,
                                      found.disk, read or source.read, sizes=found.sizes,
                                      origin=str(origin), progress=progress, taken=taken,
                                      opened=opened or getattr(source, "opened", None),
                                      check=fits,
                                      # on its own it's named the way a loader mod is,
                                      # against every other mod's name
                                      others={m["name"].lower() for m in
                                              self.file_mods(ui_game_id)
                                              + self.loader_mods(ui_game_id)} if alone else None)
        except (disk_files.DiskError, game_files.FilesError) as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": f"{name}'s files didn't go in the game's folder: "
                                          f"{write_failure(e, install_dir)}"}
        except Exception as e:
            if self._log:
                self._log.exception("the disk side of %s failed", name)
            return {"ok": False, "error": f"{name}'s files didn't go in the game's folder: {e}"}
        return done

    def disk_mods(self, ui_game_id: str) -> list:
        """the drawer's rows for mods with files loose in the game's folder"""
        from patcher.mods import disk_files

        install_dir = self.install_dir(ui_game_id)
        if install_dir is None:
            return []
        try:
            # one a crash cut short is undone before it's listed as in
            if not self.game_running(ui_game_id):
                disk_files.settle(install_dir, undo_store_for(install_dir), wait=False)
            return disk_files.listed(undo_store_for(install_dir), install_dir)
        except OSError:
            return []

    def remove_disk_mod(self, ui_game_id: str, name: str) -> dict:
        from patcher.mods import disk_files

        install_dir = self.install_dir(ui_game_id)
        if install_dir is None:
            return {"ok": False, "error": "couldn't find the game"}
        try:
            done = disk_files.remove(install_dir, undo_store_for(install_dir), name)
        except disk_files.DiskError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": write_failure(e, install_dir)}
        if not done.get("ok"):
            return done
        said = f"Removed {name}. The game's own files are back."
        if done.get("left"):
            said = (f"Removed {name}, but {', '.join(done['left'])} changed since it went in, so "
                    "that stays as it is, with the game's own still kept to put back.")
        return {"ok": True, "removed": name, "message": said}

    def _loader_from(self, ui_game_id: str, install_dir: Path, source, found, name: str,
                     origin, progress=None, others=()) -> dict:
        """
        a loader and its data beside the game's exe, where its readme says it
        goes. Never over a file this app put there itself or one it doesn't
        know: NovaFix is d3d9.dll, and two mods can't both be version.dll
        """
        from patcher.mods import game_files, loader_mods

        exe = self.game_executable(ui_game_id)
        if exe is None:
            return {"ok": False, "error": f"The game's exe isn't where it should be, so {name} "
                                          "has nowhere to go beside it."}
        # FF13Fix itself, or FF13Fix PLUS: NovaFix does all it did and more,
        # and switches itself off whole while one's loaded, frame pacing and
        # every fix with it. One that's only built on it goes in, said in the
        # warning colour (_novafix_said), since it's a mod of its own
        leaves = {game_files._leaf(m).lower(): m for m in found.loader_files}
        ff13fix = "ff13fix.ini" in leaves or "ff13fix.log" in leaves
        for leaf in ("d3d9.dll", "dinput8.dll"):
            try:
                ff13fix = ff13fix or (leaf in leaves and loader_mods.ff13fix_bytes(
                    source.read(leaves[leaf])))
            except Exception:
                pass
        if ff13fix:
            return {"ok": False, "error": (
                f"{name} wasn't installed: it's FF13Fix, which NovaFix replaces. NovaFix does "
                "everything FF13Fix did and more, and switches itself off while FF13Fix is "
                "loaded, so the two together run worse than either. Apply NovaFix instead.")}
        # on its own, like the add-on side: the game files may be in already,
        # and the loader failing mustn't say they aren't. Its author's bytes,
        # never ones with a name added for NovaFix's popup (pe_name.py)
        try:
            # Savior's Patch from an earlier Apply comes off under Installed,
            # so a newer one dropped over it is told to Remove it first
            listed = {}
            taken = self._ours_beside(ui_game_id, install_dir, exe, listed)
            return loader_mods.install(exe.parent, undo_store_for(install_dir), name,
                                       found.loader_files, source.read, taken=taken,
                                       origin=str(origin), progress=progress, others=others,
                                       owners={**self._disk_owners(install_dir, exe.parent),
                                               **listed})
        except (loader_mods.LoaderError, game_files.FilesError) as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": f"{name} didn't go in beside the game's exe: "
                                          f"{write_failure(e, exe.parent)}"}
        except Exception as e:
            if self._log:
                self._log.exception("the loader side of %s failed", name)
            return {"ok": False, "error": f"{name} didn't go in beside the game's exe: {e}"}

    def _codes_from(self, ui_game_id: str, install_dir: Path, source, members, name: str,
                    origin, part: bool = False, others=()) -> dict:
        """
        Nova Chrysalia's code patches, applied in memory as the game starts by
        our NovaFix add-on rather than by Nova's launcher (mods/nccp.py says
        why). Each is checked against this game's exe first, and against every
        other mod's, since two writing one spot would be decided by file name.
        `part` puts them in as part of the mod whose game files just went in,
        so its one Remove takes them too
        """
        from patcher import launcher_skip, save_anywhere
        from patcher.mods import loader_mods, nccp

        exe = self.game_executable(ui_game_id)
        where = self.addons_dir(ui_game_id)
        if exe is None or not where.get("ok"):
            return {"ok": False, "error": f"{name}'s code patches didn't go in: "
                                          f"{where.get('error') or 'the game exe is missing'}."}
        folder = Path(where["path"]) / nccp.FOLDER
        store = undo_store_for(install_dir)
        try:
            # from the game's folder whether it's a part or a row of its own,
            # so one file has one spelling and a version that adds game files
            # is still an update of the one that hadn't any
            under = folder.relative_to(install_dir).as_posix()
            image = exe.read_bytes()
            mine, wanted = {}, {}
            for member in members:
                leaf = member.replace("\\", "/").rsplit("/", 1)[-1]
                # Nova copies Code\\<Name>.nccp to <Name>.nccp, so the mod's
                # name is the file's and an update lands on the same one
                file = f"{loader_mods._slug(name)}.nccp" if len(members) == 1 else \
                    f"{loader_mods._slug(name)}.{leaf}"
                mine[file.lower()] = nccp.read_for(source.read(member), image, leaf)
                wanted[member] = f"{under}/{file}"
            # its own files clashing would come down to which name sorts first
            seen = []
            for patches in mine.values():
                hit = nccp.overlap(patches, seen)
                if hit is not None:
                    return {"ok": False, "error": f"{name}'s code patches didn't go in: two of "
                                                  f"its files change the code at {hit.rva:X} "
                                                  "differently."}
                seen += patches
            # an earlier version of it, under whatever names its files had then
            earlier = [r for r in loader_mods.records(store) if r.get("nccp") and (
                str(r.get("name", "")).lower() == name.lower() or r.get("part") == name)]
            its = {rel.replace("\\", "/").rsplit("/", 1)[-1].lower()
                   for r in earlier for rel in r["files"]}
            for path in sorted(folder.glob("*.nccp")) if folder.is_dir() else ():
                if path.name.lower() in mine or path.name.lower() in its:
                    continue
                try:
                    theirs = nccp.parse(path.read_bytes())[0]
                except OSError:
                    continue
                hit = nccp.overlap(seen, theirs)
                if hit is not None:
                    return {"ok": False, "error": (
                        f"{name}'s code patch at {hit.rva:X} changes the same code as "
                        f"{self._code_owner(install_dir, path)}'s. Both can't be in, so its "
                        "code patches didn't go in. Remove that one first if you'd rather "
                        "have this.")}
            # Save Anywhere would load, find its hooks taken and switch itself
            # off, so the mod that does that waits until it's gone
            manifest = self._manifests[ui_game_id]
            hit = save_anywhere.clash(seen) if save_anywhere.installed(install_dir, manifest) \
                else None
            if hit is not None:
                return {"ok": False, "error": (
                    f"{name}'s code patch at {hit[0].rva:X} changes the game code Save Anywhere "
                    f"hooks ({hit[1]}), which would switch Save Anywhere off. Its code patches "
                    "didn't go in. Remove Save Anywhere first if you'd rather have this.")}
            if not nccp.ensure_addon(folder):
                return {"ok": False, "error": f"{name}'s code patches need the patcher's "
                                              f"{nccp.ADDON}, which this build doesn't have."}
            # off first, the way it was spelled then: what this version ships
            # replaces it whole, and one by another name would be a second mod
            for record in earlier:
                loader_mods.remove(install_dir, store, record["name"])
            done = loader_mods.install(install_dir, store, name, wanted, source.read,
                                       origin=str(origin), others=others,
                                       extra={"part": name, "nccp": True} if part
                                       else {"nccp": True})
        except nccp.NccpError as e:
            return {"ok": False, "error": f"{name}'s code patches didn't go in: {e}."}
        except loader_mods.LoaderError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": f"{name}'s code patches didn't go in: "
                                          f"{write_failure(e, folder)}"}
        except Exception as e:
            # the game files may be in already, and this mustn't say they aren't
            if self._log:
                self._log.exception("the code patches of %s failed", name)
            return {"ok": False, "error": f"{name}'s code patches didn't go in: {e}"}
        done["updated"] = done.get("updated") or bool(earlier)
        done["count"] = sum(len(p) for p in mine.values())
        install, relative = self.proxy_dir(ui_game_id)
        proxy = next((p for p in self._manifests[ui_game_id].patches
                      if p.id == "install_novafix"), None)
        # in and inert until NovaFix is, which is what loads the add-on
        done["warn"] = "" if install is not None and proxy is not None and \
            launcher_skip.novafix_kind(install, proxy) else \
            "They're applied by NovaFix as the game starts, which Apply puts on."
        return done

    def _code_owner(self, install_dir: Path, path: Path) -> str:
        from patcher import save_anywhere

        return save_anywhere.code_owner(install_dir, path)

    def _ours_beside(self, ui_game_id: str, install_dir: Path, exe: Path,
                     listed: dict | None = None) -> dict:
        """
        file name -> the patch of this app's that put it beside the exe. Given
        `listed`, the ones under the drawer's Installed go there instead, to be
        said the way a mod's are, with a Remove that makes room
        """
        manifest = self._manifests.get(ui_game_id)
        beside = exe.parent.relative_to(install_dir).as_posix().lower()
        # an extra's files are only its own while it's in
        extras = {p.id for p in self.extras_in(ui_game_id)}
        recorded = load_state(state_path_for(manifest.game_id))["applied_patches"]
        taken = {}
        for patch in manifest.patches:
            if patch.in_mods and patch.id not in extras:
                continue
            if (patch.install_relative_path or ".").replace("\\", "/").strip("/").lower() \
                    == beside:
                names = [each.get("target") for each in patch.bundled_files or []]
                record = recorded.get(patch.id) if patch.in_mods else None
                if not names and isinstance(record, dict):
                    # one that doesn't ship any more has its files on the record
                    # an earlier Apply left, Savior's Patch's dinput8.dll
                    names = list(record.get("files") or [])
                into = listed if listed is not None and patch.in_mods else taken
                for name in names:
                    target = str(name or "").replace("\\", "/").lower()
                    if target:
                        into[target] = patch.label or patch.id
        return taken

    def _reshade_from(self, ui_game_id: str, install_dir: Path, source, found, origin,
                      progress=None) -> dict:
        """
        ReShade into a ReShade folder beside NovaFix, the one way its readme
        says doesn't crash the game, through the loader door: a record, a
        Remove that takes it off whole, and settings ReShade has saved since
        staying the user's on an update
        """
        from patcher.mods import game_files, loader_mods

        exe = self.game_executable(ui_game_id)
        if exe is None:
            return {"ok": False, "error": "The game's exe isn't where it should be, so ReShade "
                                          "has nowhere to go beside it."}
        ours = next((p for p in self.extras_in(ui_game_id)
                     if any(str(s.get("target", "")).lower().startswith("reshade/")
                            for s in p.bundled_files)), None)
        if ours is not None:
            return {"ok": False, "error": f"The patcher's own ReShade, {ours.label}, is in from "
                                          "Extras. Remove it under Installed first, then drop "
                                          "this one again."}
        rewrites = dict(found.reshade_rewrites)
        try:
            # a ReShade folder made by hand, the same files, is this one
            done = loader_mods.install(exe.parent, undo_store_for(install_dir), "ReShade",
                                       found.reshade_files,
                                       lambda member: rewrites.get(member) or source.read(member),
                                       taken=self._ours_beside(ui_game_id, install_dir, exe),
                                       origin=str(origin), adopt=True,
                                       extra={"reshade": dict(found.reshade_info)},
                                       progress=progress)
        except (loader_mods.LoaderError, game_files.FilesError) as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": f"ReShade didn't go in: {write_failure(e, exe.parent)}"}
        except Exception as e:
            if self._log:
                self._log.exception("the ReShade side of a drop failed")
            return {"ok": False, "error": f"ReShade didn't go in: {e}"}
        done["warn"] = self._reshade_blocked(ui_game_id, exe)
        return done

    def _reshade_unblock(self, ui_game_id: str) -> str:
        """
        Renderer left on DXVK with no dxvk.dll beside NovaFix, put back on
        Automatic as the shader goes in, and said, or "". See
        novafix_config.off_dxvk. DXVK that's really there, or Windows' D3D9
        picked, is theirs to change
        """
        from patcher import launcher_skip, novafix_config

        install_dir, relative = self.proxy_dir(ui_game_id)
        proxy = next((p for p in self._manifests[ui_game_id].patches
                      if p.id == "install_novafix"), None)
        if install_dir is None or proxy is None \
                or not launcher_skip.novafix_kind(install_dir, proxy):
            return ""
        try:
            if not novafix_config.off_dxvk(install_dir, relative):
                return ""
        except OSError:
            # still on DXVK, and the line after this says what to do by hand
            if self._log is not None:
                self._log.exception("couldn't put %s's renderer back on Automatic", ui_game_id)
            return ""
        if self._log is not None:
            self._log.info("%s's renderer was DXVK with no dxvk.dll, now Automatic", ui_game_id)
        return ("Renderer was on DXVK with no dxvk.dll beside NovaFix, so the game ran on "
                "Windows D3D9 anyway. It's on Automatic now, which is what loads the shader.")

    def _reshade_blocked(self, ui_game_id: str, exe: Path) -> str:
        """why ReShade won't load as things stand, or "": it only loads through NovaFix"""
        from patcher import launcher_skip, novafix_config

        install_dir, relative = self.proxy_dir(ui_game_id)
        proxy = next((p for p in self._manifests[ui_game_id].patches
                      if p.id == "install_novafix"), None)
        # a d3d9.dll beside the exe can as well be FF13Fix or DXVK, which never load it
        if install_dir is None or proxy is None \
                or not launcher_skip.novafix_kind(install_dir, proxy):
            return "It loads through NovaFix, which Apply puts on."
        # what NovaFix's Automatic looks for first, ahead of ReShade. The other two
        # renderers never look for either, so on those the renderer comes first
        chain = (Path(install_dir) / relative / "d3d9_chain.dll").is_file()
        try:
            backend = novafix_config.renderer(
                novafix_config.profile(novafix_config.config_path(install_dir, relative)))
        except OSError:
            backend = "Automatic"
        if backend != "Automatic":
            return ("Set Renderer back to Automatic in Settings and remove d3d9_chain.dll, "
                    "which Automatic loads first, for it to load." if chain
                    else "Set Renderer back to Automatic in Settings for it to load.")
        if chain:
            return "NovaFix loads d3d9_chain.dll instead, so remove that file for ReShade to load."
        return ""

    def _fomod_answered(self, key: str, config, version, name: str):
        """
        (the plugins a FOMOD's answers pick, whether `version` was one of
        them), or the drawer's next question as its answer. A drop with no
        version starts the questions over, and each version after is the
        answer to the one last asked. With all of them in, the answers are
        kept, so a version after that is one of what they come to's own
        """
        from patcher.mods import fomod
        from patcher.mods.titles import title as titled

        held = self._fomods.get(key)
        answered = False
        if version is None or held is None:
            held = self._fomods[key] = {"answers": {}, "asked": None}
        elif held["asked"] is not None:
            held["answers"][held["asked"]] = str(version)
            answered = True
        picked, question = fomod.walk(config, held["answers"])
        if question is None:
            held["asked"] = None
            return picked, answered
        held["asked"] = question.key
        shown = titled(name)
        return {"ok": False, "kind": "versions", "modName": name, "title": shown,
                "question": question.title, "versions": question.options,
                "recommended": question.recommended, "about": question.about,
                "more": question.more,
                "error": f"{shown} asks: {question.title}. Pick one."}

    def _nothing_in(self, ui_game_id: str, found, game, members, name: str) -> str:
        """why nothing went in, after checking the other games for where it does"""
        from patcher.mods import game_files

        if not game.pairs and (found.unmatched or found.zones):
            # every file reads as not this game's then, and another game
            # that shares the paths would get the blame
            return f"Couldn't find this game's data files, so {name} wasn't installed."
        if found.unmatched and not found.picks:
            title = self._for_other(ui_game_id, found, members)
            if title:
                return f"{name} is for {title}. Switch to that game and drop it again."
        return game_files.explain(found, game, name)

    def _for_other(self, ui_game_id: str, found, members) -> str:
        """the other game whose archives take most of a drop's unmatched files, or "" """
        from patcher.mods import game_files

        for other_id in self._manifests:
            if other_id == ui_game_id:
                continue
            there = self.install_dir(other_id)
            if there is None:
                continue
            try:
                theirs = game_files.read_game(there)
                took = game_files.look(members, theirs)
            except Exception:
                continue
            if len(took.picks) * 2 >= len(found.unmatched):
                return game_files.DATA_DIRS.get(theirs.data, other_id)
        return ""

    def novafix_settings(self, ui_game_id: str, gaps: bool = True) -> dict:
        """
        the Settings drawer. `gaps` is whether to work out what each voice set
        is short of on the HD Project, which reads both sets' archives and is
        pointless while an install is rewriting one of them.
        """
        from patcher import novafix_config, voices

        manifest = self._manifests.get(ui_game_id)
        install_dir, relative = self.proxy_dir(ui_game_id)
        if install_dir is None:
            return {"ok": False, "error": "couldn't find the game"}
        state = novafix_config.current(install_dir, relative, manifest.game_id)
        short = {}
        for language in voices.LANGUAGES if gaps else ():
            try:
                short[language] = self.voice_gap(ui_game_id, language)
            except Exception:
                # a note on the row, and the row works without it
                if self._log is not None:
                    self._log.exception("couldn't work out what the %s voices are short of",
                                        language)
        try:
            voice = voices.row(install_dir, manifest.steam_app_id, gaps=short)
        except Exception:
            # one row, in a drawer that works without it
            if self._log is not None:
                self._log.exception("couldn't read the voice setting")
            voice = None
        if voice:
            state["settings"].append(voice)
        return dict(state, ok=True)

    def save_novafix_settings(self, ui_game_id: str, changes: dict) -> dict:
        """
        NovaFix.ini and the launcher's voices, in one Apply. A switch onto a
        voice set that's short of the HD Project happens all the same and says
        so, since the voices are the player's to pick either way
        """
        from patcher import novafix_config, voices

        manifest = self._manifests.get(ui_game_id)
        install_dir, relative = self.proxy_dir(ui_game_id)
        if install_dir is None:
            return {"ok": False, "error": "couldn't find the game"}
        # the voices are setup.xml's rather than an ini's, so they're split off
        # here and checked first: a refusal after NovaFix.ini was written would
        # leave a panel that's half applied with nothing saying which half
        changes = dict(changes or {})
        language_rows = dict(changes.pop(voices.SECTION, None) or {})
        language = language_rows.pop(voices.KEY, None)
        if language_rows:
            changes[voices.SECTION] = language_rows    # apply_changes refuses those
        if language:
            refusal = voices.check(install_dir, manifest.steam_app_id, language)
            if refusal:
                return {"ok": False, "error": refusal}
        gap = None
        if language:
            # read before the write, after which it's the set being left
            try:
                gap = self.voice_gap(ui_game_id, language)
            except Exception:
                if self._log is not None:
                    self._log.exception("couldn't work out what the %s voices are short of",
                                        language)
        result = novafix_config.apply_changes(install_dir, relative,
                                              manifest.game_id, changes)
        failed = ""
        if result.get("ok") and language:
            said = voices.save(install_dir, manifest.steam_app_id, language, gap=gap)
            if not said["ok"]:
                failed = said["error"]
            elif said["changed"]:
                result["written"] = True
                result.setdefault("changed", {})[voices.SECTION] = [voices.KEY]
                try:
                    self.hd_switched(ui_game_id, voices.LANGUAGES[language][1])
                except Exception:
                    if self._log is not None:
                        self._log.exception("couldn't move the HD record to the new voices")
            if said.get("notice"):
                result["notice"] = said["notice"]
        if result.get("ok"):
            # handing the graphics to the launcher is pointless with its window
            # hidden, and taking them back hides it again. Even when the voices
            # then failed: NovaFix.ini already says who owns the graphics
            self.follow_launcher(ui_game_id,
                                 chosen="GraphicsSource" in ((changes or {}).get("Display") or {}))
            self.settle_novafix(ui_game_id)
        if failed:
            return {"ok": False, "error": f"The rest is saved. {failed}" if result.get("written")
                    else failed}
        if result.get("ok"):
            # NovaFix reads this file once, at startup. Nothing outside the
            # game process can make a running instance re-read it: it has no
            # file watcher and exports no reload. So whether the game is up
            # right now changes what actually just happened, and the panel
            # should say which.
            from patcher.running import is_running
            result["gameRunning"] = is_running(self.game_executable(ui_game_id))
            if self._log is not None:
                self._log.info("novafix settings written: %s (game running: %s)",
                               result.get("changed"), result["gameRunning"])
        return result

    def manifest_for(self, ui_game_id: str):
        return self._manifests.get(ui_game_id)

    def settle_novafix(self, ui_game_id: str) -> bool:
        """
        NovaFix's own menu keys off and its add-on popup only when they change, see
        novafix_config.first_look, once NovaFix is beside the game to read
        them. Best effort like the launcher flag: the game plays either way
        """
        from patcher import launcher_skip, novafix_config

        install_dir, relative = self.proxy_dir(ui_game_id)
        if install_dir is None:
            return False
        store = undo_store_for(install_dir)
        # the cheap answer first: looked at once already, which is most switches
        if all(novafix_config.looked_at(store, which) == "" for which in novafix_config.ONCE):
            return False
        proxy = next(p for p in self._manifests[ui_game_id].patches if p.id == "install_novafix")
        try:
            # FF13Fix, DXVK and ReShade are a d3d9.dll too, and none of them
            # reads NovaFix.ini
            if not launcher_skip.novafix_kind(install_dir, proxy):
                return False
            from patcher.state import digest_of

            wrote = novafix_config.first_look(
                install_dir, relative, store,
                current_dll=lambda: digest_of(Path(install_dir) / relative / "d3d9.dll"))
        except (OSError, ValueError):
            if self._log is not None:
                self._log.exception("couldn't settle NovaFix.ini for %s", ui_game_id)
            return False
        if wrote and self._log is not None:
            self._log.info("NovaFix.ini for %s: menu on F8 alone, add-on popup every launch, "
                           "where those were still NovaFix's own", ui_game_id)
        return wrote

    def follow_launcher(self, ui_game_id: str, chosen: bool = False):
        """
        Square Enix's launcher kept in line with who owns the graphics, see
        launcher_skip.follow. Best effort: one flag in a file we didn't write,
        in a game that plays either way, so nothing here ever fails a caller.
        """
        from patcher import launcher_skip

        manifest = self._manifests.get(ui_game_id)
        install_dir = self.install_dir(ui_game_id)
        if manifest is None or install_dir is None:
            return None
        try:
            result = launcher_skip.follow(install_dir, manifest, chosen=chosen)
        except Exception:
            if self._log is not None:
                self._log.exception("couldn't settle the launcher for %s", ui_game_id)
            return None
        if self._log is not None:
            self._log.info("square enix launcher for %s: %s", ui_game_id, result.why)
        return result

    def install_dir(self, ui_game_id: str):
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            return None
        resolution = resolve_install_dir(manifest, steam_library_paths=self._libraries())
        return resolution.install_dir if resolution.found else None

    def game_running(self, ui_game_id: str) -> str:
        """
        why nothing should be written to this game right now, or "": the game,
        or Square Enix's launcher in front of it, running out of its folder.
        Windows won't let a file the game has open be replaced, so a run that
        started anyway would stop on the first one it couldn't write
        """
        from patcher.running import running_from

        manifest = self._manifests.get(ui_game_id)
        install_dir = self.install_dir(ui_game_id)
        if manifest is None or install_dir is None:
            return ""
        try:
            name = running_from(install_dir, self.game_executable(ui_game_id))
        except Exception:
            # not knowing is not a reason to refuse, and the writes check what
            # they open anyway
            return ""
        if not name:
            return ""
        return (f"{manifest.display_name} is running ({name}). Close it first: its files can't "
                "be changed while it has them open, so nothing was changed.")

    def game_executable(self, ui_game_id: str):
        root = self.install_dir(ui_game_id)
        if root is None:
            return None
        target = root / self._manifests[ui_game_id].executable_relative_path
        return target if target.is_file() else None

    def steam_app_id(self, ui_game_id: str) -> str | None:
        """
        the appid to hand steam://rungameid, or None if it is not a plain number.

        Validated rather than trusted: this ends up in a URL the OS hands to
        whatever registered the steam: scheme, and a manifest is a file on disk
        that anyone can edit.
        """
        manifest = self._manifests.get(ui_game_id)
        app_id = str(getattr(manifest, "steam_app_id", "") or "")
        return app_id if app_id.isdigit() else None

    # what can go in with nobody there: the files ship with the app and nothing
    # waits on a download, a sign-in or a person running a .bat
    UNATTENDED = ("binary_patch", "bundled_install", "archive_inject")

    def missing_jobs(self, ui_game_id: str, wanted) -> dict:
        """
        job id to label for each of `wanted` that isn't on the game, in manifest
        order, by the same scan of the disk Apply skips on. Only the unattended
        kinds: the rest are Apply's to ask about.
        """
        manifest = self._manifests.get(ui_game_id)
        if manifest is None or not wanted:
            return {}
        resolution = resolve_install_dir(manifest, steam_library_paths=self._libraries())
        if not resolution.found:
            return {}
        status = scan_patch_status(resolution.install_dir, manifest,
                                   state_path_for(manifest.game_id))
        return {p.ui_job_id: p.label for p in manifest.patches
                if p.ui_job_id in set(wanted) and p.type in self.UNATTENDED
                and not status.get(p.id, False)}

    def apply(self, ui_game_id: str, job_ids: list[str],
              on_event: Callable[[dict], None] | None = None,
              dry_run: bool = False, cancel=None) -> dict:
        """
        runs the selected jobs in manifest order, emitting an event as each one
        starts and finishes. Blocking: shell.py puts it on a worker thread.
        One run_patches() call per patch so the progress bar moves during the
        run instead of jumping at the end.
        """
        emit = on_event or (lambda event: None)
        manifest = self._manifests.get(ui_game_id)
        if manifest is None:
            emit({"type": "finished", "ok": False, "error": f"no manifest for {ui_game_id}"})
            return {"ok": False, "error": f"no manifest for {ui_game_id}"}

        resolution = resolve_install_dir(manifest, steam_library_paths=self._libraries())
        if not resolution.found:
            emit({"type": "finished", "ok": False, "error": resolution.error})
            return {"ok": False, "error": resolution.error}

        # ask once, up front, rather than letting fourteen patches each
        # discover the same locked folder and report it in fourteen different
        # wordings. dry runs skip it because they write nothing.
        if not dry_run:
            blocked = writable_or_reason(resolution.install_dir)
            if blocked:
                if self._log is not None:
                    self._log.error("install dir not writable: %s", blocked)
                emit({"type": "finished", "ok": False, "applied": 0,
                      "failed": len(job_ids or []) or 1, "error": blocked})
                return {"ok": False, "error": blocked}

        state_path = state_path_for(manifest.game_id)
        status = scan_patch_status(resolution.install_dir, manifest, state_path)
        # one source for the whole run: it caches the account tier so a batch
        # of Nexus patches costs a single validate call, not one per patch
        nexus = NexusSource(nexus_config_path(), fetch=None)
        requested = list(job_ids or [])

        # manifest order, not the order the UI happened to send. the manifest
        # is where dependencies between patches are expressible, so it is the
        # one that gets to decide sequence.
        ordered = [p for p in manifest.patches if p.ui_job_id in requested]
        known = {p.ui_job_id for p in ordered}
        results = []

        for job_id in requested:
            if job_id not in known:
                # no manifest entry claims this id. say so instead of letting
                # it ride to a green DONE, which is what the old UI timer did
                # for six of the fourteen jobs on screen.
                event = {"type": "job", "jobId": job_id, "state": "done",
                         "status": UNSUPPORTED, "ok": False, "waiting": False,
                         "detail": "this one isn't hooked up yet",
                         "pageUrl": None}
                emit(event)
                results.append(event)

        for patch in ordered:
            if cancel is not None and cancel.is_set():
                emit({"type": "finished", "ok": False, "applied": 0,
                      "failed": 0, "error": "stopped"})
                return {"ok": False, "error": "stopped"}
            emit({"type": "job", "jobId": patch.ui_job_id, "state": "run"})

            # byte-level progress for this patch. the throttle in download.py
            # has already collapsed the flood, so this is a handful of events
            # a second rather than one per megabyte.
            def report(progress, job_id=patch.ui_job_id):
                emit({
                    "type": "progress", "jobId": job_id,
                    "stage": progress.stage,
                    "done": progress.done, "total": progress.total,
                    "fraction": round(progress.fraction, 4),
                    "label": progress.label,
                })

            try:
                batch = run_patches(
                    resolution.install_dir, manifest, {patch.id},
                    already_applied_check=lambda pid: status.get(pid, False),
                    on_applied=applied_recorder(resolution.install_dir, manifest, state_path),
                    on_supersede=supersede_handler(resolution.install_dir, manifest, state_path),
                    recorded=record_reader(state_path),
                    fetch=self._fetch,
                    dry_run=dry_run,
                    nexus_source=nexus,
                    on_progress=report,
                    cancel=cancel,
                )
            except Exception as e:
                # run_patches catches per-patch failures itself, so reaching
                # here means something got past it: a PermissionError out of a
                # guard that runs before the try, most often. Letting it
                # propagate abandons every remaining patch AND leaves this row
                # spinning, which the UI then summarises as a clean run.
                if self._log is not None:
                    self._log.exception("patch %s raised", patch.id)
                event = {
                    "type": "job", "jobId": patch.ui_job_id, "state": "done",
                    "status": PatchStatus.FAILED, "ok": False, "waiting": False,
                    "detail": write_failure(e, resolution.install_dir),
                    "pageUrl": None,
                }
                emit(event)
                results.append(event)
                continue

            result = next((r for r in batch if r.patch_id == patch.id), None)
            if result is None:
                continue
            if self._log is not None:
                self._log.info("patch %s -> %s (%s)", result.patch_id, result.status, result.detail)
            detail = result.detail or ""
            if result.status in OK_STATUSES and any(
                    str(s.get("target", "")).lower() == "reshade/d3d9.dll"
                    for s in patch.bundled_files) and not dry_run:
                # in, and still never loading, is the thing to say about it. Put
                # right only as it goes in: a DXVK picked since is somebody's plan
                exe = self.game_executable(ui_game_id)
                fixed = self._reshade_unblock(ui_game_id) \
                    if result.status == PatchStatus.APPLIED else ""
                for line in (fixed, self._reshade_blocked(ui_game_id, exe) if exe is not None else ""):
                    if line:
                        detail = f"{detail.rstrip('.')}. {line}" if detail else line
            event = {
                "type": "job", "jobId": patch.ui_job_id, "state": "done",
                "status": result.status,
                "ok": result.status in OK_STATUSES,
                "waiting": result.status in WAITING_STATUSES,
                "detail": detail,
                # where the user has to go when we could not finish it for them
                "pageUrl": result.page_url,
            }
            emit(event)
            results.append(event)

        # so Steam's own Play starts the game the way our Launch does, and the
        # first launch after Apply isn't Square Enix's settings window, nor
        # NovaFix's menu on a stray `
        if not dry_run:
            self.follow_launcher(ui_game_id)
            self.settle_novafix(ui_game_id)
            self.rename_presets(ui_game_id)
            self.depth_off(ui_game_id)

        ok = all(r["ok"] for r in results) if results else False
        emit({"type": "finished", "ok": ok,
              "applied": sum(1 for r in results if r["ok"]),
              "failed": sum(1 for r in results if not r["ok"]),
              "error": None})
        return {"ok": ok, "results": results}
