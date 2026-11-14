"""
tracks which patches are already applied to an install.

The files on disk win wherever there's a way to check them (patch_ops.CHECKS,
pinned or recorded hashes, archive read-back); the json record of what we did
is the fallback. Only imports resolve_target_path from engine, and engine
never imports this: run_patches() takes callbacks instead.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from patcher.engine import resolve_target_path
from patcher.fileops import write_atomic
from patcher.manifest import GameManifest
from patcher.patch_ops import CHECKS


def load_state(state_path: Path) -> dict:
    """
    always comes back with an applied_patches dict, whatever is actually on
    disk. a missing file is the normal first-run case, but a truncated write,
    a hand-edit or an older format would otherwise KeyError every caller
    downstream. losing a state record just means re-verifying against the real
    file, which scan_patch_status() does anyway, so degrading to empty is
    strictly better than refusing to run.
    """
    if not state_path.exists():
        return {"applied_patches": {}}
    try:
        state = json.loads(state_path.read_bytes())
    # ValueError rather than JSONDecodeError: json.loads on bytes raises
    # UnicodeDecodeError for a file that is not valid utf-8, and that is a
    # SIBLING of JSONDecodeError under ValueError, not a subclass. the
    # narrower catch let a hand-edited config take the whole app down.
    except (ValueError, OSError):
        return {"applied_patches": {}}
    if not isinstance(state, dict) or not isinstance(state.get("applied_patches"), dict):
        return {"applied_patches": {}}
    return state


def save_state(state_path: Path, state: dict) -> None:
    # atomic because this is the file most likely to be caught mid-write: it
    # gets rewritten after every single patch, and a torn write here is exactly
    # what produces the malformed file load_state() now has to defend against.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(state_path, json.dumps(state, indent=2).encode("utf-8"))


def record_applied(state_path: Path, patch_id: str, installed_files: list[str] | None = None,
                   extra: dict | None = None) -> None:
    """
    installed_files is what makes a file_install verifiable later. without it
    all we have is "we think we did this once", which survives the user
    deleting the files and keeps reporting already-applied forever.
    """
    state = load_state(state_path)
    entry = {"applied_at": datetime.now(timezone.utc).isoformat()}
    if installed_files:
        entry["files"] = list(installed_files)
    if extra:
        # anything an undo will need that the file list alone cannot express:
        # a dll we renamed out of the way, the journals for an archive edit.
        # recorded at apply time because it is unknowable afterwards.
        entry.update(extra)
    state["applied_patches"][patch_id] = entry
    save_state(state_path, state)


def record_launch(state_path: Path, patch_id: str, verdict: str, detail: str = "",
                  cover: str = "", evidence: dict | None = None) -> None:
    """
    what happened the last time we launched with patch_id applied, and what
    was covering it.

    Kept out of applied_patches, since reverting empties that and this has to
    survive exactly then. cover matters because a refusal measures one way of
    hiding the patch, not the machine; swap the mechanism and the old answer
    is about something that's gone. evidence is what the watch saw
    (launch_watch.evidence), which a record with no cover needs to count.
    """
    state = load_state(state_path)
    checks = state.setdefault("launch_checks", {})
    if not isinstance(checks, dict):
        checks = state["launch_checks"] = {}
    checks[patch_id] = {"verdict": verdict, "detail": detail, "cover": cover,
                        "checked_at": datetime.now(timezone.utc).isoformat()}
    if evidence:
        checks[patch_id]["evidence"] = evidence
    save_state(state_path, state)


def launch_blocked(state_path: Path, patch_id: str, cover: str = "") -> bool:
    """
    whether this patch, covered this way, has already been proved to stop the
    game starting.

    Needs an observed failure (a timeout or a cancel at Steam's prompt isn't
    evidence) under the same cover. A record with no cover predates covers and
    describes the old NovaFix add-on, so it blocks nothing. One filed against
    no cover at all (LR's) also needs evidence, since it's all that keeps the
    patch off, and before src_39 a plain crash could file one.
    """
    from patcher.engine import COVER_NONE
    from patcher.launch_watch import DRM_ERROR

    checks = load_state(state_path).get("launch_checks")
    if not isinstance(checks, dict):
        return False
    entry = checks.get(patch_id)
    if not isinstance(entry, dict) or entry.get("verdict") != DRM_ERROR:
        return False
    if entry.get("cover") == COVER_NONE and not entry.get("evidence"):
        return False
    return bool(entry.get("cover")) and entry.get("cover") == cover


def clear_launch_record(state_path: Path, patch_id: str) -> None:
    """so a user who changes something and retries is not stuck with the old answer"""
    state = load_state(state_path)
    checks = state.get("launch_checks")
    if isinstance(checks, dict) and checks.pop(patch_id, None) is not None:
        save_state(state_path, state)


def digest_of(path: Path) -> str | None:
    """sha256 of a file, or None if it cannot be read"""
    import hashlib

    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _install_target(install_dir: Path, patch) -> Path:
    """
    the folder a patch's files actually landed in. Same resolver the engine
    writes through, since `addons` in a manifest path can really mean mods/
    or extra/ on an install that already had one.
    """
    from patcher.engine import install_target_dir

    return install_target_dir(install_dir, patch)


def digests_for(install_dir: Path, patch, names) -> dict[str, str]:
    """
    sha256 of each file a patch just installed. A download has no pin in the
    manifest (its version moves), so hashing what we wrote is what lets it be
    verified later.
    """
    if not names:
        return {}
    target_dir = _install_target(install_dir, patch)
    found = {}
    for name in names:
        value = digest_of(target_dir / name)
        if value:
            found[name] = value
    return found


def _installed_files_are_present(install_dir: Path, patch, entry) -> bool | None:
    """
    True/False if we can check, None if nothing's recorded to check.

    Prefers recorded digests over presence, since a Steam verify, another mod
    with the same filename or a half finished copy all pass on name. Looks in
    the folder the apply wrote into, not the install root.
    """
    if entry is None:
        return None
    recorded = entry.get("files")
    if recorded is None:
        return None
    if not recorded:
        return False

    target_dir = _install_target(install_dir, patch)
    digests = entry.get("digests")
    if isinstance(digests, dict) and digests:
        for name in recorded:
            want = digests.get(name)
            if want is None:
                # recorded before digests existed, or unreadable at apply time
                if not (target_dir / name).exists():
                    return False
                continue
            if digest_of(target_dir / name) != want:
                return False
        return True
    return all((target_dir / name).exists() for name in recorded)


def _injection_is_present(entry) -> bool | None:
    """
    whether an archive injection's bytes are still in the archive.

    The journal has the offset, length and original bytes of every region we
    overwrote, so reading them back is a handful of seeks. A region that still
    differs from its original is one we're still in. Without this a Steam
    verify left the row saying applied. None when there's no journal.
    """
    from patcher.archive.spanbackup import load_journal

    if entry is None:
        return None
    journals = entry.get("journals")
    if not journals:
        return None

    checked = False
    for raw in journals:
        journal_path = Path(raw)
        spans_path = journal_path.parent / journal_path.name.replace(".journal", ".spans")
        if not journal_path.is_file() or not spans_path.is_file():
            continue
        try:
            journal = load_journal(journal_path)
            target = Path(journal.target)
            if not target.is_file():
                return False
            # a size change means the archive was replaced wholesale, so the
            # offsets describe a file that no longer exists and anything read
            # at them is meaningless
            if target.stat().st_size != journal.target_size:
                return False
            with target.open("rb") as live, spans_path.open("rb") as original:
                for span in journal.spans:
                    checked = True
                    live.seek(span.offset)
                    original.seek(span.journal_offset)
                    if live.read(span.length) != original.read(span.length):
                        return True      # still ours
        except (OSError, ValueError, KeyError):
            return None

    # every span we could read is byte for byte the original again
    return False if checked else None


def _checked(check, target_path: Path) -> bool:
    """
    runs a PE check against as little of the file as it needs: the header's in
    the first page, and reading XIII's whole 38MB exe for one bit slowed every
    game switch. A check that raises on the prefix gets the whole file.
    """
    from patcher.patch_ops import CHECK_PREFIX

    with target_path.open("rb") as handle:
        head = handle.read(CHECK_PREFIX)
    try:
        return check(head)
    except ValueError:
        return check(target_path.read_bytes())


def scan_patch_status(install_dir: Path, manifest: GameManifest, state_path: Path) -> dict[str, bool]:
    """
    True/False for every patch in the manifest. where we can, this is
    read straight off the real file (a registered check function and a
    target file that exists). where we can't verify that way (an
    unimplemented operation, or an asset pack with no check yet) it
    falls back to the recorded state instead, which is weaker (just "we
    think we did this before") but still better than nothing.
    """
    recorded = load_state(state_path)["applied_patches"]
    status: dict[str, bool] = {}

    for patch in manifest.patches:
        entry = recorded.get(patch.id)

        if patch.type in ("file_install", "nexus"):
            # the files we wrote are the ground truth, same as the LAA bit is
            # for a binary patch: delete d3d9.dll and this has to say False.
            # nexus lands files the same way, so it's answered the same way.
            present = _installed_files_are_present(install_dir, patch, entry)
            if present is None:
                # recorded before file tracking existed, or a nexus patch that
                # ends in somebody else's installer and lands nothing we can
                # name. unverifiable, so treat it as not applied: reinstalling
                # is idempotent and cheap, wrongly skipping it leaves the user
                # with a broken game.
                status[patch.id] = False
            else:
                status[patch.id] = present
            continue

        if patch.type == "archive_inject":
            # read the injected regions back instead of trusting the record.
            # Hashing the entry the index points at needs nothing outside the
            # game folder; the journal (in the undo store) is the fallback for
            # an injection with no pinned hash.
            from patcher.engine import injection_on_disk

            present = injection_on_disk(install_dir, patch)
            if present is None:
                present = _injection_is_present(entry)
            status[patch.id] = entry is not None if present is None else present
            continue

        if patch.type == "bundled_install":
            # the files, not the record. This is what the UI badge and Apply's
            # skip check run, so a record with nothing under it would say DONE
            # forever after a Steam verify. engine.installed_on_disk hashes
            # against the manifest pin.
            from patcher.engine import installed_on_disk

            status[patch.id] = installed_on_disk(install_dir, patch)
            continue

        if patch.type != "binary_patch":
            status[patch.id] = entry is not None
            continue

        check = CHECKS.get(patch.operation)
        target_path = resolve_target_path(install_dir, manifest, patch)

        if check is None or not target_path.exists():
            status[patch.id] = entry is not None
            continue
        try:
            status[patch.id] = _checked(check, target_path)
        except (ValueError, OSError):
            # a corrupt or locked executable. this runs before the per patch
            # loop and outside every handler, so letting it out aborts the
            # whole run for every game with a message naming a PE offset. the
            # patch itself reports the real problem when it tries.
            status[patch.id] = False

    return status


def applied_recorder(install_dir: Path, manifest: GameManifest, state_path: Path):
    """
    the on_applied callback, plus a digest of everything it just installed,
    so recording and verifying are written against each other.
    """
    by_id = {p.id: p for p in manifest.patches}

    def record(result) -> None:
        patch = by_id.get(result.patch_id)
        extra = dict(result.revert_info or {})
        digests = digests_for(install_dir, patch, result.installed_files) if patch else {}
        if digests:
            extra["digests"] = digests
        record_applied(state_path, result.patch_id, result.installed_files, extra or None)

    return record


def record_reader(state_path: Path):
    """the recorded callback: what an earlier run left in state for a patch id"""
    def read(patch_id: str) -> dict:
        entry = load_state(state_path)["applied_patches"].get(patch_id)
        return entry if isinstance(entry, dict) else {}

    return read
