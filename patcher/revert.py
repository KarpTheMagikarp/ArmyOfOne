"""
Putting a game back the way it was.

Every applied patch records enough to undo itself (files written, the user's
dlls set aside, span journals), and this turns that back into an uninstall.
Driven only by recorded state, never by what the manifest says a patch would
have written, so anything the user put there by hand stays.
"""

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from patcher.archive.spanbackup import (RESHAPED, RestoreError, check, discard, load_journal,
                                        matches, restore)
from patcher.engine import resolve_target_path
from patcher.fileops import Held, claim, write_atomic
from patcher.paths import undo_store_for
from patcher.state import digest_of, load_state, save_state

# the HD Project is not in applied_patches. It keeps whole-file backups beside
# the install rather than span journals, and its record lives under
# categories:<patch id>, which is bridge._selection_key's spelling
HD_PATCH = "hd_texture_pack"

BINARY_UNDO = {
    # flipping the large-address-aware bit back is the same operation in
    # reverse, so a backup is not strictly needed. untouched.exe is used when
    # it's this same build, because Steam's DRM wants the exact original.
    "flip_laa_flag": "clear_laa_flag",
}


@dataclass
class RevertResult:
    patch_id: str
    label: str
    ok: bool
    detail: str = ""
    removed: list = field(default_factory=list)
    # every file put back in full, for what the HD restore took with it
    paths: list = field(default_factory=list)
    # undo files to delete once the record stops naming them. The other way
    # round, a crash in between leaves a record no revert can ever finish
    leftovers: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"patchId": self.patch_id, "label": self.label, "ok": self.ok,
                "detail": self.detail, "removed": self.removed}


def _revert_files(install_dir: Path, patch, record: dict) -> RevertResult:
    """
    deletes the files this patch installed and puts back anything of the
    user's it set aside. Already gone is fine. Changed since we installed it
    means it's someone else's now, so it stays. A set aside file only goes
    back as the exact file that was set aside.
    """
    # record_applied flattens the extras into the entry, so they live at the
    # top level rather than under a "revert_info" key
    # a record written before the engine started storing the resolved folder
    # names the manifest path, so it goes back through the same resolver rather
    # than being trusted. the two agree on an install that has no competing
    # add-on folder, and only that case ever worked.
    from patcher.engine import backup_path_for, install_target_dir, remove_file

    relative = record.get("dir")
    target_dir = (install_dir / relative if relative
                  else install_target_dir(install_dir, patch))
    # older records have no digests, and those still go by name
    digests = record.get("digests")
    digests = digests if isinstance(digests, dict) else {}
    # name to the sha256 of what was set aside. A list is from before those
    # were kept, and goes back by name as it always did
    kept = record.get("kept")
    if isinstance(kept, list):
        kept = dict.fromkeys(kept)
    kept = kept if isinstance(kept, dict) else {}
    removed, restored, notes, problems = [], [], [], []

    for name in record.get("files", []):
        target = target_dir / name
        backup = backup_path_for(target)
        existed = target.exists()
        now = digest_of(target) if existed else None
        if now and digests.get(name, now) != now:
            note = f"{name} changed after we installed it, so it stays"
            if name in kept and backup.is_file():
                note += f", and yours is still {backup.name}"
            notes.append(note)
            continue
        try:
            theirs = backup.read_bytes() if name in kept and backup.is_file() else None
            want = kept.get(name)
            if theirs is not None and want and hashlib.sha256(theirs).hexdigest() != want:
                # anything else under that name is a guess, and a wrong dll
                # beside the exe is a game that won't start
                notes.append(f"{backup.name} isn't the file that was set aside when this went "
                             "in, so it was left where it is")
                theirs = None
            if theirs == b"":
                # set aside by a build before empties were skipped. Putting a
                # 0-byte dll back is a game that won't start, so it just goes
                remove_file(backup)
                theirs = None
            if theirs is not None:
                write_atomic(target, theirs)
                remove_file(backup)
            elif existed:
                target.unlink()
        except OSError as e:
            problems.append(f"{name}: {e}")
            continue
        if existed:
            removed.append(name)
        if theirs is not None:
            restored.append(name)

    # settings a run left theirs in place of. Back to ours byte for byte, they
    # come off with the rest; anything else is theirs
    shipped = record.get("shipped") if isinstance(record.get("shipped"), dict) else {}
    for name, ours in shipped.items():
        target = target_dir / name
        if name in record.get("files", []) or not target.is_file():
            continue
        if digest_of(target) != ours:
            notes.append(f"{name} changed after we installed it, so it stays")
            continue
        if remove_file(target):
            removed.append(name)
        else:
            problems.append(f"{name}: couldn't remove it")

    # and the folders a bundle made, once nothing's left in them. One with a
    # log or a changed setting in it stays, which is somebody's
    for name in removed:
        folder = (target_dir / name).parent
        while folder != target_dir and folder.is_relative_to(target_dir):
            try:
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent

    if problems:
        return RevertResult(patch.id, patch.label, False, "; ".join(problems), removed)
    said = []
    if len(removed) > 4:
        from patcher.engine import _into

        where = _into(removed).replace(" into ", " from ")
        said.append(f"removed {len(removed)} files{where}")
    elif removed:
        said.append(f"removed {', '.join(removed)}")
    if restored:
        said.append(f"put your own {', '.join(restored)} back")
    return RevertResult(patch.id, patch.label, True,
                        "; ".join(said + notes) or "nothing left to remove", removed)


def _revert_injection(patch, record: dict, store: Path | None = None) -> RevertResult:
    """
    restores the archives from their span journals, in reverse so overlapping
    spans land right. `store` is the game's undo store, claimed first, so a
    --revert during the window's HD install waits its turn.
    """
    if store is None:
        return _put_back_injection(patch, record)
    try:
        held = claim(store)
    except Held:
        return RevertResult(
            patch.id, patch.label, False,
            "something else is working on this game's archives right now, most likely the HD "
            "install in the window. Let it finish, then run this again. Nothing has been "
            "changed.")
    except OSError as e:
        return RevertResult(patch.id, patch.label, False,
                            f"couldn't open the undo store at {store} ({e}). Nothing has "
                            "been changed.")
    with held:
        return _put_back_injection(patch, record)


def _put_back_injection(patch, record: dict) -> RevertResult:
    journals = [Path(j) for j in record.get("journals", [])]
    if not journals:
        return RevertResult(
            patch.id, patch.label, False,
            "no undo journal was recorded for this, so the archives can't be put back "
            "automatically. verify the game files through Steam instead")

    # Decided before a byte moves. An archive is only consistent once both
    # halves are back: the journal puts the vanilla blobs into white_img and
    # the index says where they start. Restoring the first and then finding we
    # cannot do the second leaves the install worse than we found it, and that
    # is exactly what a revert must never do, so the check comes first.
    backups, index_plan, refusal = _index_plan(record, _pins(patch))
    if refusal:
        return RevertResult(patch.id, patch.label, False, refusal)

    # the data half too: a missing or stale journal stops the revert, or the
    # index goes back over injected bytes, the mismatch the check above stops
    for journal in journals:
        problem = check(journal)
        if problem == RESHAPED:
            return RevertResult(patch.id, patch.label, False, REWRITTEN)
        if problem:
            return RevertResult(patch.id, patch.label, False, INCOMPLETE)

    restored, problems = [], []
    for journal in reversed(journals):
        try:
            restore(journal)
            restored.append(journal.name)
        except (RestoreError, OSError) as e:
            problems.append(str(e))
            break
    if problems:
        # checked above, so this is a disk error partway through. The journals
        # stay, and running the revert again replays them over the top.
        return RevertResult(
            patch.id, patch.label, False,
            f"the undo stopped partway on a disk error ({problems[0]}). Running the revert "
            "again finishes it", restored)

    # the index, after the data, matching the order Undo.roll_back() uses
    # mid-apply
    for put_back in reversed(index_plan):
        try:
            restored.append(put_back())
        except Exception as e:
            problems.append(str(e))

    if problems:
        # the data is back and the index isn't, and the row now reads not
        # installed. Apply refuses a pair in that state, so this says which way out
        return RevertResult(patch.id, patch.label, False,
                            f"{'; '.join(problems)}. The rest is back already, so run the "
                            "revert again to finish it", restored)

    return RevertResult(patch.id, patch.label, True,
                        f"restored {len(restored)} archive(s) from backup", restored,
                        leftovers=[*journals, *(backup for backup, _ in backups)])


def _drop(leftovers: list) -> None:
    """undo files nothing names any more. Litter if this fails, never a stuck revert"""
    for path in leftovers:
        try:
            if path.name.endswith(".journal"):
                discard(path)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass
    # and the run-* folder each Apply made for them, once that's all it held
    for folder in {Path(path).parent for path in leftovers}:
        try:
            if folder.name.startswith("run-") and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass


def _pins(patch) -> dict:
    """the manifest's sha256 for each payload, by the index's spelling of its path"""
    from patcher.engine import pinned

    return {path.replace("\\", "/").lower(): sha.lower() for path, sha in pinned(patch)}


def _index_plan(record: dict, pins: dict | None = None):
    """
    how each filelist gets put back, as callables that only write, or why it
    can't. Returns (backups to delete afterwards, plan, refusal).

    Everything is read, checked and rebuilt up front, since an index that
    won't rebuild after the data's gone back leaves a broken pair. The apply
    time copy is used while it's still the file that was taken and nothing's
    rewritten the index since. Otherwise the two numbers the injection changed
    per entry go back in place, once every entry is still where the journal
    puts it and still holds the injection. That route is why a missing copy
    isn't a 20 minute Steam verify to undo a 90KB mod.
    """
    copies = {spec["target"]: spec for spec in record.get("index_backups", [])
              if spec.get("backup") and spec.get("target")}
    numbers = {}
    for spec in record.get("index_entries", []):
        if spec.get("target") and spec.get("entries"):
            numbers.setdefault(spec["target"], []).extend(spec["entries"])
    if not copies and not numbers:
        return [], [], INCOMPLETE

    plan = []
    for target in dict.fromkeys([*copies, *numbers]):
        data = _exact_copy(copies.get(target))
        if data is None and target in numbers:
            data = _renumbered(Path(target), numbers[target], pins or {})
            if data is None:
                return [], [], REWRITTEN
        if data is None:
            return [], [], INCOMPLETE
        plan.append(_writer(Path(target), data))
    return [(Path(s["backup"]), Path(s["target"])) for s in copies.values()], plan, None


# deliberately names nothing on disk. this is the one message that ends with
# "go and re-download part of your game", so it says what happened and that
# nothing was broken getting there. which backup file is missing is a question
# for --doctor, not for somebody who just pressed a button.
INCOMPLETE = ("this mod's undo record is incomplete, so removing it could leave the game's "
              "data files worse off than they are now. Nothing has been changed. Use Steam to "
              "Verify Integrity of Game Files to put this game back to a clean state")

REWRITTEN = ("the game's data files have been rewritten by something else since this mod "
             "went in, so its undo no longer fits them. Nothing has been changed. Use "
             "Steam to Verify Integrity of Game Files to put this game back to a clean state")


def _exact_copy(spec: dict | None) -> bytes | None:
    """the copy's bytes when putting it back is exact: it's the one that was
    taken, and the live index is the one the injection wrote or the copy itself"""
    if spec is None:
        return None
    try:
        data = Path(spec["backup"]).read_bytes()
    except OSError:
        return None
    taken = hashlib.sha256(data).hexdigest()
    if spec.get("sha256", taken) != taken:
        return None
    # a missing index goes back from the copy, which is the best there is.
    # Records from before the hashes are taken at their word, as they always were
    now = digest_of(Path(spec["target"]))
    if spec.get("written") and now is not None and now not in (spec["written"], taken):
        return None
    return data


def _renumbered(target: Path, entries: list, pins: dict) -> bytes | None:
    """
    the live index with the two numbers per entry put back, or None when it's
    not an index those fit any more. Pre-flighted like prepare(), and
    replace_entry refuses any length change, so the reverse only goes through
    where the forward edit could have.
    """
    from patcher.archive import filelist

    image = target.with_name(target.name.replace("filelist", "white_img", 1))
    try:
        raw = target.read_bytes()
        filelist.verify_roundtrip(raw)
        index = filelist.parse(raw)
        for entry in entries:
            _, live = index.locate(entry["path"])
            # the numbers only mean anything over the span the journal puts
            # back, and only while the entry still holds the injection or is
            # already back. Somebody else's mod there isn't ours to take off.
            # Older records lack both and are taken as before
            if live is None or live.sector_offset != entry.get("sector_offset",
                                                               live.sector_offset):
                return None
            now = [live.uncompressed, live.stored]
            if ("injected" in entry
                    and now not in (entry["injected"], [entry["uncompressed"], entry["stored"]])
                    and not _holds(image, live, pins.get(entry["path"].lower()))):
                return None
            filelist.replace_entry(index, entry["path"], uncompressed=entry["uncompressed"],
                                   stored=entry["stored"])
        return filelist.rebuild(index)
    except Exception:
        return None


def _holds(image: Path, entry, pin: str | None) -> bool:
    """whether an entry inflates to the payload the manifest pins, whoever wrote it there"""
    from patcher.mods.hd_zones import read_record

    try:
        return bool(pin) and hashlib.sha256(read_record(image, entry)).hexdigest() == pin
    except Exception:
        return False


def _writer(target: Path, data: bytes):
    def put_back() -> str:
        # a truncated index is the one thing that stops the game booting at all
        try:
            write_atomic(target, data, durable=True)
        except OSError as e:
            raise RevertFailed(f"couldn't put {target.name} back: {e.strerror or e}") from e
        return target.name
    return put_back


class RevertFailed(Exception):
    """carries a sentence already fit to show someone"""


def _revert_binary(install_dir: Path, manifest, patch) -> RevertResult:
    """undoes an in-place exe edit, from the clean copy when it's this same build"""
    from patcher.engine import unpatch_exe
    from patcher.patch_ops import is_laa_enabled

    target = resolve_target_path(install_dir, manifest, patch)
    if not target.exists():
        return RevertResult(patch.id, patch.label, True, "the target file is gone")

    operation = BINARY_UNDO.get(patch.operation)
    if operation is None:
        return RevertResult(patch.id, patch.label, False,
                            f"no way to undo {patch.operation} without a backup")

    try:
        data = target.read_bytes()
        if not is_laa_enabled(data):
            # a Steam verify or update got here first, and the copy beside it
            # is the build that one replaced
            return RevertResult(patch.id, patch.label, True,
                                f"{target.name} is already unpatched")
        backup = patch.required_backup_filename if patch.requires_backup else None
        how = unpatch_exe(target, backup, data)
    except Exception as e:
        return RevertResult(patch.id, patch.label, False, str(e))
    return RevertResult(patch.id, patch.label, True, f"reverted {target.name} by {how}")


def revert_patch(install_dir: Path, manifest, patch, state_path: Path) -> RevertResult:
    """undoes one patch, using only what was recorded when it was applied"""
    state = load_state(state_path)
    record = state["applied_patches"].get(patch.id)
    if record is None:
        return RevertResult(patch.id, patch.label, True, "wasn't applied")

    if patch.type == "archive_inject":
        result = _revert_injection(patch, record, undo_store_for(install_dir))
    elif patch.type == "binary_patch":
        result = _revert_binary(install_dir, manifest, patch)
    elif patch.type in ("file_install", "bundled_install", "nexus", "direct"):
        result = _revert_files(install_dir, patch, record)
    else:
        result = RevertResult(patch.id, patch.label, True, "nothing was installed for this")

    if result.ok and patch.replaces:
        from patcher.engine import put_back_leftovers

        back = put_back_leftovers(install_dir, manifest, patch)
        if back:
            result.detail = "; ".join(filter(None, [result.detail, f"put your own {', '.join(back)} back"]))

    if result.ok:
        # only forget it once the undo actually worked, or a failed revert
        # would look like it was never applied and never be retried
        state = load_state(state_path)
        state["applied_patches"].pop(patch.id, None)
        save_state(state_path, state)
        _drop(result.leftovers)
        _drop_logs(install_dir, patch, record)
    return result


def _drop_logs(install_dir: Path, patch, record: dict) -> None:
    """
    what a mod of ours writes about itself while the game runs, never on its
    record, once the mod's own file is off: a log nothing reads any more.
    Kept while that file's still there, a changed one Revert left.
    """
    from patcher.engine import RUNTIME_LOGS, install_target_dir

    logs = RUNTIME_LOGS.get(patch.id, ())
    # one that doesn't ship any more, Savior's Patch, has its files on the
    # record an earlier Apply left
    files = [spec["target"] for spec in patch.bundled_files if not spec.get("config")] \
        or list(record.get("files") or ())
    if not logs or not files:
        return
    home = install_target_dir(install_dir, patch)
    if any((home / name).is_file() for name in files):
        return
    # a pattern for the ones that come numbered, like Save Anywhere's debug dumps
    found = [log for name in logs for log in (sorted(home.glob(name)) if "*" in name else [home / name])]
    for log in found:
        try:
            log.unlink(missing_ok=True)
            # ReShade's sits in the folder the bundle made, which Revert kept
            # for it
            folder = log.parent
            while folder != home and folder.is_relative_to(home):
                folder.rmdir()
                folder = folder.parent
        except OSError:
            pass


def revert_all(install_dir: Path, manifest, state_path: Path,
               patch_ids: set | None = None) -> list:
    """
    every applied patch undone, with the mods the Mods drawer put into the
    archives taken off around them when it's all of it. Those go first, while
    the archives are the ones they went into, and anything a repack had
    carried gets another go once the HD restore has put back the archives
    from before it
    """
    from patcher.mods import game_files

    if patch_ids is not None:
        return _revert_patches(install_dir, manifest, state_path, patch_ids)
    first = _files_off(install_dir)
    results = _revert_patches(install_dir, manifest, state_path, patch_ids)
    first = _files_after(install_dir, first)
    try:
        # one taken off after the HD backup was taken came back with it
        game_files.settle(install_dir)
    except Exception:
        # an index that won't parse any more is the restore's to report, and
        # the revert has done its part either way
        pass
    try:
        from patcher import gpu_preference

        gpu_preference.release(undo_store_for(install_dir))
    except OSError:
        # Windows' graphics settings are the user's to tidy then, and say ours
        pass
    return first + results


def _files_off(install_dir: Path, names=None) -> list:
    from patcher.mods import game_files, loader_mods

    store = undo_store_for(install_dir)
    try:
        everything = game_files.records(store)
    except OSError:
        return []
    out = []
    for name in dict.fromkeys(r["name"] for r in reversed(everything)):
        if names is not None and name not in names:
            continue
        try:
            if not any(r["name"] == name for r in game_files.records(store)):
                # passed over by one taken off before it, its files gone with
                # an HD repack, and taken off whole along with that one
                out.append(RevertResult(f"files:{name}", name, True,
                                        "came off with the mod that was over it"))
                continue
            done = game_files.remove(install_dir, store, name)
            # what went in on disk as a part of it, LR's DLC files
            loader_mods.remove_parts(install_dir, store, name)
            out.append(RevertResult(f"files:{name}", name, True,
                                    f"{done['files']} files back as they were" if done["files"]
                                    else "wasn't in the game any more"))
        except (game_files.FilesError, loader_mods.LoaderError, OSError) as e:
            out.append(RevertResult(f"files:{name}", name, False, str(e)))
    # DLC files whose mod came off without them, by a Remove or the go above
    tried = {r.label for r in out}
    try:
        left = loader_mods.strays(store, {r["name"] for r in game_files.records(store)})
    except OSError:
        left = []
    for name in dict.fromkeys(r["part"] for r in left):
        if name in tried or (names is not None and name not in names):
            continue
        try:
            loader_mods.remove_parts(install_dir, store, name)
            out.append(RevertResult(f"files:{name}", name, True, "its DLC files came off"))
        except (loader_mods.LoaderError, OSError) as e:
            out.append(RevertResult(f"files:{name}", name, False, str(e)))
    return out + _disk_off(install_dir, store, names)


def _disk_off(install_dir: Path, store: Path, names=None) -> list:
    """the drawer's loose files, the game's own put back under each"""
    from patcher.mods import disk_files

    out = []
    try:
        everything = disk_files.records(store)
    except OSError:
        return out
    for record in reversed(everything):
        name = record["name"]
        if names is not None and name not in names:
            continue
        try:
            done = disk_files.remove(install_dir, store, name)
        except (disk_files.DiskError, OSError) as e:
            out.append(RevertResult(f"disk:{name}", name, False, str(e)))
            continue
        left = done.get("left") or []
        out.append(RevertResult(f"disk:{name}", name, True,
                                f"the game's own files back, but {', '.join(left)} changed "
                                "since and stays" if left else "the game's own files back"))
    return out


def _files_after(install_dir: Path, first: list) -> list:
    """another go at what couldn't come off before the restore put the older archives back"""
    stuck = {r.label for r in first if not r.ok}
    # by what came off, since a mod's archive files and its loose ones share its name
    again = {r.patch_id: r for r in _files_off(install_dir, stuck)} if stuck else {}
    return [again.get(r.patch_id, r) for r in first]


def _revert_patches(install_dir: Path, manifest, state_path: Path,
                    patch_ids: set | None = None) -> list:
    """
    undoes every applied patch in reverse order, since later patches can sit
    on earlier ones. The HD restore goes in between: an injection with nothing
    in the files the restore brings back comes off first if it's newer than
    the backup; one with something in them waits, and then _sort_restored()
    decides pair by pair.
    """
    state = load_state(state_path)
    applied = state["applied_patches"]
    wanted = [p for p in manifest.patches if p.id in applied]
    if patch_ids is not None:
        wanted = [p for p in wanted if p.id in patch_ids]

    def applied_at(patch):
        return applied.get(patch.id, {}).get("applied_at", "")

    ordered = sorted(wanted, key=applied_at, reverse=True)
    taken = _hd_backup_time(install_dir)
    if taken is None or (patch_ids is not None and HD_PATCH not in patch_ids):
        results = [revert_patch(install_dir, manifest, patch, state_path) for patch in ordered]
        return results + _after_novafix(install_dir, manifest, patch_ids, state_path)

    going = _going_back(install_dir)
    first = [p for p in ordered
             if _when(applied_at(p)) > taken and not _targets(applied[p.id]) & going]
    results = [revert_patch(install_dir, manifest, patch, state_path) for patch in first]
    hd = _revert_hd(install_dir, manifest, state_path)
    results.append(hd)
    back = {_same(p) for p in hd.paths}
    for patch in ordered:
        if patch in first:
            continue
        inside = _targets(applied[patch.id]) & going
        if inside - back:
            # a file that did not come back still carries the HD install, and
            # a journal replayed into it only refuses
            results.append(_waiting(patch))
        elif inside:
            results.append(_after_restore(install_dir, manifest, patch, state_path, back))
        else:
            results.append(revert_patch(install_dir, manifest, patch, state_path))
    return results + _after_novafix(install_dir, manifest, patch_ids, state_path)


def _after_novafix(install_dir: Path, manifest, patch_ids, state_path: Path) -> list:
    """what Apply changed in other files because NovaFix went on, put back"""
    return (_launcher_back(install_dir, manifest, patch_ids)
            + _ini_back(install_dir, manifest, patch_ids, state_path))


def _ini_back(install_dir: Path, manifest, patch_ids, state_path: Path) -> list:
    """
    NovaFix's menu keys and add-on popup as they were before Apply set them,
    for whichever NovaFix goes on after. A line only when they couldn't be
    """
    from patcher import launcher_skip, novafix_config

    proxy = next((p for p in manifest.patches if p.id == "install_novafix"), None)
    if patch_ids is not None or proxy is None:
        return []
    try:
        if proxy.id in load_state(state_path)["applied_patches"]:
            # its own revert failed, a running game holding it most often, and
            # the keys are still right for it: the next Revert puts them back
            return []
        # a NovaFix still there now is the user's own, which Revert put back
        # or never took, as ours byte for byte or not
        kind = launcher_skip.novafix_kind(install_dir, proxy)
        novafix_config.put_back(install_dir, proxy.install_relative_path or "",
                                undo_store_for(install_dir),
                                stays=_proxy_digest(install_dir, proxy) if kind else None)
    except (OSError, ValueError) as e:
        return [RevertResult("novafix-menu", "NovaFix's menu keys and add-on popup", False,
                             f"couldn't put them back ({e})")]
    return []


def _proxy_digest(install_dir: Path, proxy) -> str | None:
    """the sha256 of the d3d9.dll beside the game, or None"""
    from patcher.engine import install_target_dir

    for spec in proxy.bundled_files or ():
        target = install_target_dir(install_dir, proxy) / spec["target"]
        if target.is_file():
            return digest_of(target)
    return None


def _launcher_back(install_dir: Path, manifest, patch_ids) -> list:
    """
    Square Enix's launcher shown again if Apply was what hid it, once NovaFix
    is off: with neither, its window is the only place the graphics change.
    Never a hide, even with NovaFix still on after its own revert failed. A
    line only when that did something, so a game it never touched says nothing
    about it.
    """
    from patcher import launcher_skip

    if patch_ids is not None:
        return []
    try:
        result = launcher_skip.follow(install_dir, manifest, may_hide=False)
    except Exception as e:
        return [RevertResult("launcher", "Square Enix launcher", False,
                             f"couldn't show it again ({e})")]
    if not result.changed and not result.failed:
        return []
    return [RevertResult("launcher", "Square Enix launcher", not result.failed,
                         "shows again when the game starts" if result.changed else result.why)]


def settle_after_restore(install_dir: Path, manifest, state_path: Path, put_back) -> list:
    """
    the injections a restore of the HD backup just took off, taken off the rest
    of the way and forgotten, as their RevertResults.

    A rebuild restores before it installs. Where the copy that went back does
    not hold an injection, its record would go on saying it is in the game,
    and its journal would refuse to replay into the new archive forever after.
    """
    back = {_same(p) for p in put_back}
    applied = load_state(state_path)["applied_patches"]
    out = []
    for patch in manifest.patches:
        record = applied.get(patch.id)
        if record is None or not _targets(record) & back:
            continue
        without, _, _ = _sort_restored(patch, record, back)
        if without:
            out.append(_came_off(install_dir, patch, state_path, without))
    return out


def _after_restore(install_dir: Path, manifest, patch, state_path: Path, back: set):
    """an injection with files the HD restore just put back, taken off what is left of it"""
    record = load_state(state_path)["applied_patches"].get(patch.id)
    if record is None:
        return RevertResult(patch.id, patch.label, True, "wasn't applied")
    without, holding, stuck = _sort_restored(patch, record, back)
    if stuck:
        names = ", ".join(sorted(Path(p).name for p in stuck))
        return RevertResult(
            patch.id, patch.label, False,
            f"its undo does not fit what the HD restore put back in {names}, so it cannot "
            "be taken off safely. Nothing has been changed for it. Use Steam to Verify "
            "Integrity of Game Files to put this game back to a clean state")
    # spelled out of the journals while they still exist, since the revert discards them
    sources = _spelled(record, holding)
    result = (_came_off(install_dir, patch, state_path, without) if without
              else revert_patch(install_dir, manifest, patch, state_path))
    if result.ok and holding:
        problem = _retake(install_dir, sources, holding)
        if problem:
            result.detail += (f". The HD backup still holds it ({problem}), so an HD install "
                              "and a Revert after this one would bring it back")
    return result


def _spelled(record: dict, files: set) -> list:
    """the record's own spelling of these files, since _same() may have folded the case"""
    spelled = {}
    for journal in record.get("journals", []):
        try:
            target = load_journal(Path(journal)).target
        except Exception:
            continue
        spelled[_same(target)] = target
    for key in ("index_backups", "index_entries"):
        for spec in record.get(key, []):
            if spec.get("target"):
                spelled[_same(spec["target"])] = spec["target"]
    return [Path(spelled[path]) for path in sorted(files) if path in spelled]


def _retake(install_dir: Path, sources: list, files: set) -> str:
    """
    the game's copies of these into the HD backup, now that they are clean and
    the backup's still hold the injection, or why that could not be done.
    Taken again rather than dropped, so the backup stays one pair with the zone
    blobs its indexes point into, and never half of one: a pair taken at two
    different times is worse than one that still holds the injection.
    """
    from patcher.mods.hd_install import retake

    if len(sources) != len(files):
        return "not every file of it could be named"
    try:
        with claim(undo_store_for(install_dir)):
            retake(undo_store_for(install_dir) / "hdproject", sources)
    except OSError as e:
        return str(e)
    return ""


def _sort_restored(patch, record: dict, back: set) -> tuple:
    """
    the injection's pairs the restore just put back, by what came back in
    them: (without it, with it, with it or unknown and no safe undo), each as
    both halves of the pair.

    By content, not clocks, since the backup can be older than this app. If
    the pin says it came back without, it's without. Same if its journal fits
    and finds the original bytes. Otherwise the pin has to confirm it's there,
    with its entries where the journal was taken, before the journal is
    replayed, since another mod's bytes in that spot aren't ours to put back.
    """
    without, holding, stuck = set(), set(), set()
    for journal in record.get("journals", []):
        image = _journal_target(journal)
        if image is None or image not in back:
            continue
        pair = _halves(record, image)
        present = _payload_in(patch, pair)
        problem = check(Path(journal))
        if present is False:
            without.update(pair)
        elif problem not in ("", RESHAPED):
            continue
        elif not problem and matches(Path(journal)):
            # its originals are what came back, which is only "without it"
            # when they are not its own bytes, journalled by a second apply
            (stuck if present else without).update(pair)
        elif present and not problem and _where_it_was(Path(journal), pair):
            holding.update(pair)
        else:
            stuck.update(pair)
    return without, holding, stuck


def _where_it_was(journal: Path, pair: set) -> bool:
    """whether the restored index puts every entry the journal covers where it was taken"""
    from patcher.archive.entry import find
    from patcher.archive.filelist import load

    index = next((p for p in pair if Path(p).name.startswith("filelist")), None)
    try:
        taken = load_journal(journal).metadata.get("entries") or []
        entries = list(load(Path(index)).entries())
    except Exception:
        return False
    for was in taken:
        now = find(entries, str(was.get("path", "")))
        if now is None or now.sector_offset != was.get("sector_offset"):
            return False
    return bool(taken)


def _halves(record: dict, image: str) -> set:
    """an archive the journal covers and the index the record says describes it"""
    folder, letter = Path(image).parent, Path(image).name.split(".")[0][-1:]
    found = {image}
    for key in ("index_backups", "index_entries"):
        for spec in record.get(key, []):
            target = _same(spec.get("target", ""))
            if Path(target).parent == folder and Path(target).name.split(".")[0][-1:] == letter:
                found.add(target)
    return found


def _payload_in(patch, pair: set) -> bool | None:
    """whether the manifest's pinned payload is in this pair, None if nothing is pinned"""
    from patcher.engine import injection_in, pinned

    wanted = pinned(patch)
    index = next((p for p in pair if Path(p).name.startswith("filelist")), None)
    image = next((p for p in pair if Path(p).name.startswith("white_")), None)
    if not wanted or index is None or image is None:
        return None
    return injection_in(Path(index), Path(image), wanted)


def _came_off(install_dir: Path, patch, state_path: Path, gone: set) -> RevertResult:
    """
    an injection whose files in `gone` just went back to copies without it.
    Those parts are forgotten, what it wrote anywhere else comes off by its own
    undo, and then it is no longer applied.
    """
    from patcher.mods.hd_install import after_changes

    state = load_state(state_path)
    record = state["applied_patches"].get(patch.id)
    if record is None:
        return RevertResult(patch.id, patch.label, True, "wasn't applied")

    # forgotten before the rest is replayed, so a disk error below never leaves
    # them to be replayed into a file they no longer describe. The record goes
    # first: a journal file left behind is litter, one the record still names
    # after it is gone is a revert that can never finish.
    rest = dict(record)
    rest["journals"] = [j for j in record.get("journals", []) if _journal_target(j) not in gone]
    for key in ("index_backups", "index_entries"):
        rest[key] = [spec for spec in record.get(key, [])
                     if _same(spec.get("target", "")) not in gone]
    state["applied_patches"][patch.id] = rest
    save_state(state_path, state)
    try:
        for journal in record.get("journals", []):
            if journal not in rest["journals"]:
                discard(Path(journal))
        for spec in record.get("index_backups", []):
            if spec not in rest["index_backups"] and spec.get("backup"):
                Path(spec["backup"]).unlink(missing_ok=True)
    except OSError:
        pass

    said, leftovers = "came off with the HD restore, whose copies were without it", []
    if rest["journals"]:
        result = _revert_injection(patch, rest, undo_store_for(install_dir))
        leftovers = result.leftovers
        if not result.ok:
            reshaped = any(check(Path(j)) == RESHAPED for j in rest["journals"])
            if reshaped and _targets(rest) <= _held_by_backup(install_dir):
                # the other voice set, rewritten by an HD install since it went
                # in. A full Revert puts that copy back first, and then it fits
                why = ("the rest is in archives the HD install rewrote after it went in, and "
                       "a full Revert takes it off those, since it puts them back first")
            else:
                why = f"its own undo could not take it off the rest: {after_changes(result.detail)}"
            return RevertResult(patch.id, patch.label, False,
                                f"came off the files the HD restore put back, and {why}",
                                result.removed)
        said += ", and its own undo took it off everything else"
    state = load_state(state_path)
    state["applied_patches"].pop(patch.id, None)
    save_state(state_path, state)
    _drop(leftovers)
    return RevertResult(patch.id, patch.label, True, said)


def _waiting(patch) -> RevertResult:
    return RevertResult(
        patch.id, patch.label, False,
        "left in for now: it sits in archives the HD backup puts back, and that restore did "
        "not finish (see above). Nothing has been changed for it. Run the revert again once "
        "that is sorted and it comes off with them")


def _same(path) -> str:
    """one spelling per file, so a journal's target and a restored path compare"""
    return os.path.normcase(os.path.realpath(str(path)))


def _journal_target(journal) -> str | None:
    try:
        return _same(load_journal(Path(journal)).target)
    except Exception:
        return None


def _targets(record: dict) -> set:
    """every file an injection's undo writes to"""
    found = {_journal_target(j) for j in record.get("journals", [])} - {None}
    for key in ("index_backups", "index_entries"):
        found |= {_same(spec["target"]) for spec in record.get(key, []) if spec.get("target")}
    return found


def _held_by_backup(install_dir: Path) -> set:
    """every game file the HD backup holds a copy of, whether or not it will go back"""
    from patcher.mods.hd_install import restore_groups, restore_prep

    try:
        prep = restore_prep(install_dir, undo_store_for(install_dir))
        groups, _ = restore_groups(prep.backup_dir, prep.archives[0].parents[2])
    except Exception:
        return set()
    return {_same(target) for group in groups for target in group}


def _going_back(install_dir: Path) -> set:
    """the files the HD restore is about to put back, which is none when it will not write"""
    from patcher.mods.hd_install import game_written, restore_prep

    try:
        backup = restore_prep(install_dir, undo_store_for(install_dir)).backup_dir
        if not backup.is_dir() or not game_written(backup):
            return set()
    except Exception:
        return set()
    return _held_by_backup(install_dir)


def _when(stamp: str) -> float:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _hd_backup_time(install_dir: Path) -> float | None:
    from patcher.mods.hd_install import backup_taken_at

    return backup_taken_at(undo_store_for(install_dir) / "hdproject")


def _revert_hd(install_dir: Path, manifest, state_path: Path) -> RevertResult:
    """the HD Project's archives back from its own backup, verified on the way"""
    from patcher.mods.hd_install import (InstallError, RestoreIncomplete, grown_since_backup,
                                         restore, restore_prep)

    patch = next((p for p in manifest.patches if p.id == HD_PATCH), None)
    label = "The FF XIII HD Project" if patch is None else patch.label
    prep = restore_prep(install_dir, undo_store_for(install_dir))
    try:
        put_back = restore(prep)
    except RestoreIncomplete as e:
        return RevertResult(HD_PATCH, label, False, str(e), [p.name for p in e.restored],
                            [str(p) for p in e.restored])
    except (InstallError, OSError) as e:
        return RevertResult(HD_PATCH, label, False, str(e))

    state = load_state(state_path)
    record = state.get(f"categories:{HD_PATCH}")
    if isinstance(record, dict):
        record["installed"], record["pipeline"] = [], {}
        # every voice set went back, so none of them has anything to say
        for part in ("sets", "pipelines", "owed", "failed", "refused"):
            record.pop(part, None)
        save_state(state_path, state)
    if put_back:
        from patcher.mods.hd_mirror import reverted
        reverted(install_dir)
    if not put_back:
        # ours is off, but "back to how it was" is a claim about the disk, and
        # the mod's own installer run by hand since reads exactly like this
        try:
            grown = grown_since_backup(prep.backup_dir, Path(install_dir))
        except OSError:
            grown = []
        if grown:
            return RevertResult(
                HD_PATCH, label, False,
                "this app has not written to the game since its backup was taken, so it had "
                f"nothing of its own to put back, but {grown[0]} has grown since, which is "
                "what an HD install looks like. The mod's own installer does that, and "
                "Steam's Verify Integrity of Game Files is the way to a clean game from here")
        return RevertResult(HD_PATCH, label, True,
                            "this app has not written to the game since its backup was taken, "
                            "so there was nothing of its own to put back")
    from patcher.mods.hd_install import modded_before

    still = modded_before(Path(install_dir))
    return RevertResult(HD_PATCH, label, True,
                        f"put back {len(put_back)} archive(s) from the backup"
                        + (f". {still}" if still else ""),
                        [p.name for p in put_back], [str(p) for p in put_back])
