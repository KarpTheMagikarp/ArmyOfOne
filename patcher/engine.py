"""
orchestrates the actual patching: given a loaded manifest, an install
directory, and which patches got selected, this is what runs them for
real against files on disk. patch_ops.py stays pure (bytes in bytes out),
all the file system stuff lives here instead.
"""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from patcher import zipnames
from patcher.access import permission_message, write_failure
from patcher.download import Cancelled, IncompleteDownload, NotEnoughSpace

# Exceptions whose message is already the thing to show a user: it names the
# problem, the folder and usually the fix. Flattening these into a generic
# "install raised" or, worse, into "we'll open the mod page" is how a full disk
# became an invitation to go and download something in a browser.
SPEAKS_FOR_ITSELF = (NotEnoughSpace, IncompleteDownload)
from patcher.mods import install_from_url, latest_release_asset
from patcher.fileops import scratch_near, temp_sibling, write_atomic
from patcher.logging_setup import get_logger
from patcher.manifest import GameManifest, Patch
from patcher.patch_ops import (OPERATIONS, clear_laa_flag, identify_xiii_build,
                               is_laa_enabled, read_fingerprint)
from patcher.updater import default_fetch


class PatchStatus:
    # the patch is right, this executable cannot take it. distinct from failure
    # (nothing went wrong) and from skipped (we made a decision about it).
    UNSAFE_BUILD = "unsafe_build"
    # we undid something that would have stopped the game launching
    REPAIRED = "repaired"
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    WOULD_APPLY = "would_apply"
    SKIPPED = "skipped"
    FAILED = "failed"
    # a free Nexus account cannot resolve a download link, so the user has to
    # press Mod Manager Download once on the mod page. not a failure: the run
    # is waiting on them, and the nxm handler finishes it.
    NEEDS_WEBSITE = "needs_website"
    # no usable Nexus api key stored
    NEEDS_ACCOUNT = "needs_account"
    # a prerequisite patch has not been applied. The case this was written for
    # was HD Models Plus, which reused files The HD Project extracts and so
    # produced a broken install rather than an error when run first. That mod
    # is bundled into The HD Project now and the row is gone; the mechanism
    # stays, because "wrong order looks installed and renders wrong" is the
    # normal shape of a mod that builds on another one.
    BLOCKED = "blocked"
    # downloaded and staged, but the mod ships an interactive installer that a
    # person has to drive. we do not silently launch installers.
    NEEDS_INSTALLER = "needs_installer"
    # hosted somewhere that cannot be automated at all (Patreon, a forum post)
    MANUAL = "manual"
    # we could not fetch it, and there is no useful page to send anyone to.
    # the user downloads it however they like and drops the file on the app.
    NEEDS_FILE = "needs_file"


@dataclass
class PatchResult:
    patch_id: str
    label: str
    status: str
    detail: str = ""
    # which files a file_install actually wrote. recorded into state so a
    # later run can check they are still there instead of trusting the log.
    installed_files: list[str] | None = None
    # where a user has to go when we cannot finish the job for them
    page_url: str | None = None
    # anything an undo needs that the file list cannot express
    revert_info: dict | None = None


def backup_path_for(target_path: Path, required_filename: str | None = None) -> Path:
    """
    where a backup of target_path should live. most patches just want
    our own generic .backup suffix (used for our own restore action),
    but some things (steam's DRM on FFXIII's exe specifically)
    require the backup to be named exactly something else
    (untouched.exe) or they won't recognize a patched exe as legitimate
    at all. required_filename overrides the generic scheme for those.
    """
    if required_filename:
        return target_path.with_name(required_filename)
    # ".backup" is seven more characters against a 260 limit, which is enough
    # to push an already-deep install over on its own. temp_sibling borrows
    # from the stem instead of growing the path when it has to.
    return temp_sibling(target_path, "backup")


def ensure_backup(target_path: Path, required_filename: str | None = None) -> Path:
    """
    makes a backup copy if one doesn't already exist. deliberately won't
    overwrite an existing backup, otherwise running the patcher twice
    could end up "backing up" an already-patched file over the real
    original.
    """
    backup = backup_path_for(target_path, required_filename)
    if not backup.exists():
        # write_atomic, not copy2. A failed copy2 leaves a truncated backup,
        # the never-overwrite rule above keeps it forever, and on XIII the DRM
        # reads it and the game won't start.
        write_atomic(backup, target_path.read_bytes())
    return backup


def restore_backup(target_path: Path, required_filename: str | None = None) -> bool:
    """
    restores target_path from its backup, if one exists. this is
    the "restore" action, separate from anything in the main patch run,
    so it's always unambiguous what it does.
    """
    backup = backup_path_for(target_path, required_filename)
    if not backup.exists():
        return False
    # the executable the game is about to run. a half written one is worse
    # than the patched one it replaces.
    write_atomic(target_path, backup.read_bytes())
    return True


def clean_copy(backup: Path, data: bytes) -> bytes | None:
    """backup's bytes if it's this same build with the 4GB flag off, else None"""
    try:
        original = backup.read_bytes()
        # the fingerprint carries the link timestamp, so a Steam update or the
        # other XIII build never passes for this one
        if (len(original) == len(data) and not is_laa_enabled(original)
                and read_fingerprint(original) == read_fingerprint(data)):
            return original
    except (OSError, ValueError):
        pass
    return None


def keep_untouched(target: Path, name: str, data: bytes) -> str | None:
    """
    makes the copy beside the exe this build with the 4GB flag off, and says
    what it did. None when it already was.

    The cover hands that copy to the DRM when the game checks itself, so one
    from another build, or one that's patched too, is a refused launch.
    Clearing the flag rebuilds it exactly, because the flag is all we change.
    """
    backup = target.with_name(name)
    if clean_copy(backup, data) is not None:
        return None
    note = f"kept a clean copy as {name}"
    if backup.exists():
        # moved, not overwritten. it may be the only copy of the other build
        stale = temp_sibling(backup, "old")
        backup.replace(stale)
        note = (f"the old {name} wasn't a clean copy of this exe, so it's now "
                f"{stale.name} and a clean copy took its place")
    write_atomic(backup, clear_laa_flag(data), durable=True)
    return note


def untouched_for_a_patched_exe(target: Path, patch: Patch, ours: bool,
                                dry_run: bool = False) -> PatchResult | None:
    """
    the copy the DRM reads, for an exe that was already patched when we got
    here. None when there's nothing to say about it.

    Only rebuilt when we made the patch: ours is one bit, so clearing it is the
    exact original. Another 4GB tool may have touched the checksum too.
    """
    if not target.is_file():
        return None
    name, data = patch.required_backup_filename, target.read_bytes()
    if identify_xiii_build(data).get("safe_to_edit") \
            or clean_copy(target.with_name(name), data) is not None:
        return None
    if not ours:
        # not DONE. that's what sent people into a launch the cover got blamed for
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           f"another tool's 4GB patch is on and {name} isn't a clean copy, "
                           "so Steam won't start the game. Verify the game files through "
                           "Steam, then Apply again")
    note = f"would rebuild {name}" if dry_run else keep_untouched(target, name, data)
    return PatchResult(patch.id, patch.label, PatchStatus.ALREADY_APPLIED,
                       f"already applied, {note}")


def unpatch_exe(target: Path, name: str | None, data: bytes) -> str:
    """
    takes the 4GB flag back off, and says how.

    From the copy beside it when that's this same build, which also drops
    anything else a 4GB tool changed. A copy of any other build would swap in
    a different executable, so then the flag is cleared in place, which is
    exact for ours.
    """
    original = clean_copy(target.with_name(name), data) if name else None
    if original is not None:
        write_atomic(target, original)
        return f"restoring {name}"
    write_atomic(target, clear_laa_flag(data))
    return "clearing the 4GB flag"


def resolve_target_path(install_dir: Path, manifest: GameManifest, patch: Patch) -> Path:
    relative = patch.target_relative_path or manifest.executable_relative_path
    return install_dir / relative


# the manifest spells the add-on folder "addons" because that is NovaFix's
# default. which one it actually reads is decided by what is already on disk.
ADDON_SEGMENT = "addons"


def install_target_dir(install_dir: Path, patch: Patch) -> Path:
    """
    where this patch's files go.

    Almost always install_dir / install_relative_path. NovaFix's add-on folder
    is the exception: it scans the first of addons/extra/extras/mods/dll that
    exists, so a manifest can't hardcode the name.
    """
    from patcher.novafix_config import resolve_addons_dir

    relative = PurePosixPath((patch.install_relative_path or "").replace("\\", "/"))
    parts = relative.parts
    if ADDON_SEGMENT not in parts:
        return install_dir / relative

    cut = parts.index(ADDON_SEGMENT)
    proxy_root = install_dir.joinpath(*parts[:cut])
    return resolve_addons_dir(proxy_root).joinpath(*parts[cut + 1:])


def apply_binary_patch(install_dir: Path, manifest: GameManifest, patch: Patch) -> PatchResult:
    target_path = resolve_target_path(install_dir, manifest, patch)

    if not target_path.exists():
        return PatchResult(
            patch.id, patch.label, PatchStatus.FAILED,
            f"couldn't find {target_path.name} in the game folder"
        )

    operation = OPERATIONS.get(patch.operation)
    if operation is None:
        return PatchResult(
            patch.id, patch.label, PatchStatus.SKIPPED,
            f"no implementation yet for operation '{patch.operation}'"
        )

    note = None
    try:
        original = target_path.read_bytes()
        if patch.requires_backup and patch.required_backup_filename:
            note = keep_untouched(target_path, patch.required_backup_filename, original)
        elif patch.requires_backup:
            ensure_backup(target_path)

        patched = operation(original)
        write_atomic(target_path, patched)
    except PermissionError:
        return PatchResult(
            patch.id, patch.label, PatchStatus.FAILED,
            permission_message(target_path)
        )
    except Exception as e:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED, f"stopped on an error: {e}")

    # which exe this was, for the log only, so a NovaFix "unsupported build"
    # report can be matched up. The fingerprint table is XIII's, so only XIII
    # gets a name; a size collision on another game would print a wrong one.
    if manifest.game_id == "ffxiii":
        build = identify_xiii_build(original)
        if build.get("known"):
            get_logger().info("4GB patch on XIII's %s", build["name"])
    return PatchResult(patch.id, patch.label, PatchStatus.APPLIED, note or "")


def apply_file_install(
    install_dir: Path,
    manifest: GameManifest,
    patch: Patch,
    fetch: Callable[[str], bytes] = default_fetch,
    on_progress=None,
    cancel=None,
) -> PatchResult:
    if not patch.github_repo:
        return PatchResult(
            patch.id, patch.label, PatchStatus.FAILED,
            "manifest is missing github_repo for this file_install patch"
        )

    download_url, why = latest_release_asset(patch.github_repo, fetch=fetch)
    if download_url is None:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           why or f"couldn't find a release asset for {patch.github_repo}")

    target_dir = install_dir / (patch.install_relative_path or "")
    try:
        installed_files = install_from_url(download_url, target_dir, fetch=fetch,
                                           source_subdir=patch.source_subdir,
                                           on_progress=on_progress, cancel=cancel)
    except PermissionError:
        return PatchResult(
            patch.id, patch.label, PatchStatus.FAILED,
            permission_message(target_dir)
        )
    except Cancelled:
        raise
    except SPEAKS_FOR_ITSELF as e:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))
    except Exception as e:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED, f"install raised: {e}")

    return PatchResult(patch.id, patch.label, PatchStatus.APPLIED,
                       f"installed: {', '.join(installed_files)}",
                       installed_files=installed_files)


def describe_binary_patch(install_dir: Path, manifest: GameManifest, patch: Patch) -> PatchResult:
    """
    the dry-run twin of apply_binary_patch: same validation (missing
    file, unimplemented op), but never touches disk. kept as a
    separate function rather than an if-branch inside apply_binary_patch
    so there's no risk of a dry run accidentally sharing a code path
    with the one that actually writes bytes.
    """
    target_path = resolve_target_path(install_dir, manifest, patch)

    if not target_path.exists():
        return PatchResult(
            patch.id, patch.label, PatchStatus.FAILED,
            f"couldn't find {target_path.name} in the game folder"
        )

    if OPERATIONS.get(patch.operation) is None:
        return PatchResult(
            patch.id, patch.label, PatchStatus.SKIPPED,
            f"no implementation yet for operation '{patch.operation}'"
        )

    backup_note = ""
    if patch.requires_backup:
        backup_name = patch.required_backup_filename or backup_path_for(target_path).name
        backup_note = f", would back up to {backup_name} first"

    return PatchResult(
        patch.id, patch.label, PatchStatus.WOULD_APPLY,
        f"would patch {target_path}{backup_note}"
    )


def describe_file_install(install_dir: Path, manifest: GameManifest, patch: Patch) -> PatchResult:
    """the dry-run twin of apply_file_install: describes the install without downloading or writing anything"""
    if not patch.github_repo:
        return PatchResult(
            patch.id, patch.label, PatchStatus.FAILED,
            "manifest is missing github_repo for this file_install patch"
        )

    target_dir = install_dir / (patch.install_relative_path or "")
    return PatchResult(
        patch.id, patch.label, PatchStatus.WOULD_APPLY,
        f"would download the latest release from {patch.github_repo} and install into {target_dir}"
    )


def apply_nexus(install_dir: Path, manifest: GameManifest, patch: Patch,
                source, fetch: Callable[[str], bytes] = default_fetch,
                staging_dir: Path | None = None,
                on_progress=None, cancel=None) -> PatchResult:
    """
    installs a Nexus-hosted mod, when the account tier allows it.

    the branching lives in nexus_source.resolve(): premium gets a real link,
    free gets told to press the button on the site. this function only turns
    that answer into a PatchResult.
    """
    # a mirror skips Nexus entirely: no key, no tier, same flow for everyone.
    # Only ever point it at something the author has agreed to, it's
    # redistribution.
    if patch.mirror_url:
        target_dir = install_dir / (patch.install_relative_path or "")
        try:
            installed_files = install_from_url(patch.mirror_url, target_dir, fetch=fetch,
                                               source_subdir=patch.source_subdir,
                                               expected_sha256=patch.mirror_sha256,
                                               on_progress=on_progress, cancel=cancel)
        except Cancelled:
            raise
        except SPEAKS_FOR_ITSELF as e:
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))
        except Exception as e:
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED, f"mirror install failed: {e}")
        return PatchResult(patch.id, patch.label, PatchStatus.APPLIED,
                           f"installed from mirror: {', '.join(installed_files)}",
                           installed_files=installed_files)

    if source is None:
        return PatchResult(patch.id, patch.label, PatchStatus.NEEDS_FILE,
                           "we'll open the mod page and pick up the download automatically")

    resolution = source.resolve(patch.nexus_game_domain, patch.nexus_mod_id, patch.nexus_file_id)

    if resolution.status == "needs_file":
        # the normal outcome, not a failure. the page opens, the user
        # downloads it the way they always have, and drops it on the window.
        return PatchResult(patch.id, patch.label, PatchStatus.NEEDS_FILE,
                           resolution.detail, page_url=resolution.page_url)
    if resolution.status == "needs_website":
        return PatchResult(patch.id, patch.label, PatchStatus.NEEDS_WEBSITE,
                           resolution.detail, page_url=resolution.page_url)
    if resolution.status == "needs_account":
        return PatchResult(patch.id, patch.label, PatchStatus.NEEDS_ACCOUNT,
                           resolution.detail, page_url=resolution.page_url)
    if not resolution.ok:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           resolution.detail, page_url=resolution.page_url)

    if patch.runs_installer:
        # multi-gb texture packs ship a .bat that asks questions and needs
        # tens of gb of scratch space. downloading it unattended is fine;
        # running it unattended is not.
        from patcher.mods.hd_pack_installer import download_and_stage
        target = staging_dir or (install_dir / ".patcher-downloads")
        try:
            staged = download_and_stage(resolution.download_url, target, fetch=fetch,
                                        on_progress=on_progress, cancel=cancel)
        except Cancelled:
            raise
        except SPEAKS_FOR_ITSELF as e:
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))
        except Exception as e:
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED, f"download failed: {e}")
        return PatchResult(patch.id, patch.label, PatchStatus.NEEDS_INSTALLER,
                           f"downloaded to {staged}, run its installer to finish",
                           page_url=resolution.page_url)

    target_dir = install_dir / (patch.install_relative_path or "")
    try:
        installed_files = install_from_url(resolution.download_url, target_dir, fetch=fetch,
                                           source_subdir=patch.source_subdir,
                                           on_progress=on_progress, cancel=cancel)
    except PermissionError:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           permission_message(target_dir))
    except Cancelled:
        raise
    except SPEAKS_FOR_ITSELF as e:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))
    except Exception as e:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED, f"install raised: {e}")

    return PatchResult(patch.id, patch.label, PatchStatus.APPLIED,
                       f"installed: {', '.join(installed_files)}",
                       installed_files=installed_files)


def apply_direct(install_dir: Path, manifest: GameManifest, patch: Patch,
                 fetch: Callable[[str], bytes] = default_fetch,
                 on_progress=None, cancel=None) -> PatchResult:
    """
    a public download that needs no account.

    File hosts often answer a bot with a confirmation page, so anything that's
    clearly HTML comes back as "open this yourself" with the url instead of
    landing in the game folder.
    """
    from patcher.mods.file_install import NotAFileError

    target_dir = install_dir / (patch.install_relative_path or "")
    try:
        installed_files = install_from_url(
            patch.download_url, target_dir, fetch=fetch,
            source_subdir=patch.source_subdir,
            expected_sha256=patch.mirror_sha256,
            archive_password=patch.archive_password,
            on_progress=on_progress, cancel=cancel,
        )
    except NotAFileError as e:
        # only offer a page when the manifest actually names one. falling back
        # to download_url meant a host that answers with a confirmation page
        # got that same URL opened in a browser on every single run, which is
        # how a patch with no fallback_page_url still hijacked the screen.
        if patch.fallback_page_url:
            return PatchResult(
                patch.id, patch.label, PatchStatus.NEEDS_WEBSITE,
                f"{e}. download it from the page and drop it in",
                page_url=patch.fallback_page_url)
        return PatchResult(
            patch.id, patch.label, PatchStatus.NEEDS_FILE,

            "we'll open the mod page and pick up the download automatically")
    except Cancelled:
        # the user pressed Stop. Cancelled subclasses Exception, so without
        # this it fell into the handler below and came back as "we'll open the
        # mod page", which then opened a browser at somebody who had just
        # asked the tool to stop doing things.
        raise
    except SPEAKS_FOR_ITSELF as e:
        # full disk, truncated download, unreadable archive: each carries its
        # own sentence with the fix, so don't flatten it into "open the page".
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))
    except PermissionError:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           permission_message(target_dir))
    except OSError as e:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           write_failure(e, target_dir))
    except Exception as e:
        if patch.fallback_page_url:
            return PatchResult(patch.id, patch.label, PatchStatus.NEEDS_WEBSITE,
                               f"couldn't fetch it automatically ({e})",
                               page_url=patch.fallback_page_url)
        return PatchResult(patch.id, patch.label, PatchStatus.NEEDS_FILE,

                           "we'll open the mod page and pick up the download automatically")

    return PatchResult(patch.id, patch.label, PatchStatus.APPLIED,
                       f"installed: {', '.join(installed_files)}",
                       installed_files=installed_files)


def unusable_binary(name: str, data: bytes) -> str | None:
    """
    a sentence for the user if a supplied file can't possibly work, else None.

    Checks shape instead of pinning a hash, since a pin turns every other
    NovaFix release into a hard failure.
    """
    if not name.lower().endswith((".dll", ".asi", ".exe")):
        return None
    if len(data) < 0x40 or data[:2] != b"MZ":
        # nearly always an HTML error page or a zip saved under the wrong name
        return (f"{name} isn't a Windows program file. If you downloaded it, check you "
                "saved the file itself rather than a web page, and that you unzipped it")

    try:
        machine = read_fingerprint(data)["machine"]
    except ValueError:
        return f"{name} looks damaged. Try downloading it again"

    if machine and machine != 0x014C:
        # these games are 32-bit, and a 64-bit proxy installs cleanly then never
        # loads, which reads as the patcher having done nothing at all
        return (f"{name} is the 64-bit build. These games are 32-bit, so grab the "
                "x86 or Win32 download instead")
    return None


def apply_bundled_install(install_dir: Path, manifest: GameManifest, patch: Patch,
                          on_progress=None, cancel=None,
                          earlier: set[str] | None = None,
                          kept: dict | None = None) -> PatchResult:
    """
    copies files that ship with the patcher into the game.

    Anything already there that isn't ours is set aside as .backup and Revert
    puts it back. earlier is the sha256 of every file state says we installed
    for this patch or one it replaces (how our old NovaFix is told from
    ReShade). kept is the earlier record's sha256 of each file it set aside.
    """
    import hashlib

    from patcher.download import Progress
    from patcher.fileops import scratch_near, temp_sibling, write_atomic
    from patcher.paths import bundled_data_root

    target_dir = install_target_dir(install_dir, patch)
    staged = []

    for spec in patch.bundled_files:
        asset = bundled_data_root() / spec["source"]
        if not asset.is_file():
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                               missing_from_patcher(spec["source"]))
        data = asset.read_bytes()

        # shape first, THEN the hash. Both catch a bad file, but only one of
        # them says anything useful: an HTML error page saved over the dll or a
        # 64-bit build gets a sentence naming the actual problem, where a hash
        # mismatch just says the bytes are wrong. The hash is what is left for a
        # file that looks right and still is not the one we shipped.
        problem = unusable_binary(spec["target"], data)
        if problem:
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED, problem)

        expected = spec.get("sha256")
        if expected and hashlib.sha256(data).hexdigest().lower() != expected.lower():
            # only for assets we commit and therefore vouch for. pinning one the
            # USER drops in means refusing any build but the exact one whose
            # hash we happened to record, which is what made a perfectly good
            # NovaFix download fail on a second machine.
            return PatchResult(
                patch.id, patch.label, PatchStatus.FAILED,
                f"{spec['target']} is not the build this patcher ships. Redownload "
                "the patcher rather than replacing files inside it by hand")

        staged.append((spec, data))

    # a file this patch used to install somewhere else still gets loaded from
    # there, and two copies of a hooking dll means the second one errors with
    # "Couldn't find pattern of patch target!". Runs after the writes (see
    # displace() below) so a failed write never leaves the user with neither.
    displaced = []

    def displace():
        ours = {hashlib.sha256(data).hexdigest() for _, data in staged}
        for stale_relative in patch.moved_from:
            stale = install_dir / stale_relative
            if not stale.is_file():
                continue
            # only ours. someone else's winmm.dll at the same path belongs to
            # them, and a patcher that deletes files it did not write is a
            # patcher people stop trusting.
            if hashlib.sha256(stale.read_bytes()).hexdigest() not in ours:
                continue
            try:
                stale.unlink()
                displaced.append(Path(stale_relative).name)
            except OSError as e:
                # not pass: this is the two-copies case above. Usually the game
                # is still running, which the user can fix if we say so.
                return (f"couldn't remove the older copy at {stale_relative} ({e}). "
                        "Close the game if it's running, then try again")
        return None

    known = set(earlier or ()) | {hashlib.sha256(data).hexdigest() for _, data in staged}
    was = kept if isinstance(kept, dict) else {}
    kept, set_aside, moved, made = {}, [], [], []

    # somebody else's where one of these goes leaves the whole patch out, before
    # anything's written: half of one ReShade under the other's settings is
    # neither of them. One the Mods drawer put in is the drawer's even when it's
    # the very same build, since its Remove takes that file with it
    drawer = _drawer_claims(install_dir, manifest, target_dir, patch)
    for spec, _ in staged:
        target = target_dir / spec["target"]
        if spec.get("foreign") != "refuse":
            continue
        if spec["target"].replace("\\", "/").lower() in drawer:
            # named by its folder: the drawer's is a ReShade, not the preset
            # this one's called after
            theirs = PurePosixPath(spec["target"].replace("\\", "/")).parent.name or spec["target"]
            return PatchResult(patch.id, patch.label, PatchStatus.SKIPPED,
                               f"{theirs} is in from the Mods drawer already, so "
                               f"{patch.label} was left out. Remove that one under Installed "
                               "first if you'd rather have this one.")
        if target.is_file() and not _ours(patch, target.read_bytes(), known):
            return PatchResult(patch.id, patch.label, PatchStatus.SKIPPED,
                               f"there's already a {spec['target']} that isn't this app's, so "
                               f"{patch.label} was left out. Take it out, or Remove it in Mods "
                               "if it went in from there, then Apply again.")

    # a preset still under the name it shipped as goes on as itself, tweaks and
    # all, rather than a fresh copy landing beside it. The record that goes
    # with the new name is only written for a run that finishes, so one that
    # doesn't puts the old names back too
    settings = {}
    for spec in patch.bundled_files:
        there = target_dir / spec["target"]
        if spec.get("config") and there.is_file():
            try:
                settings[there] = there.read_bytes()
            except OSError:
                pass
    renamed = renamed_bundled(install_dir, patch, ours=True)

    def take_back():
        # nothing gets recorded for a run that stops partway, so what it moved,
        # theirs included, goes back now or never
        import os

        for target, there, fresh in reversed(moved):
            try:
                if there is None:
                    remove_file(target)
                else:
                    write_atomic(target, there)
                if fresh:
                    remove_file(backup_path_for(target))
            except OSError:
                pass
        for folder in reversed(made):
            try:
                folder.rmdir()
            except OSError:
                pass
        for old, new in reversed(renamed):
            try:
                os.replace(target_dir / new, target_dir / old)
            except OSError:
                pass
        for there, data in settings.items() if renamed else ():
            try:
                if there.read_bytes() != data:
                    _rewritten(there, data)
            except OSError:
                pass

    try:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            installed, written, shipped = [], [], {}
            for index, (spec, data) in enumerate(staged, start=1):
                if cancel is not None and cancel.is_set():
                    take_back()
                    return PatchResult(patch.id, patch.label, PatchStatus.FAILED, "cancelled")
                target = target_dir / spec["target"]
                backup = backup_path_for(target)
                for folder in reversed(target.parents):
                    if folder.is_relative_to(target_dir) and not folder.exists():
                        folder.mkdir()
                        made.append(folder)

                there = target.read_bytes() if target.is_file() else None
                # ReShade, DXVK and every ASI loader live under these names too.
                # An empty one is nobody's: kept as .backup, Revert would put a
                # 0-byte dll back beside the exe and the game wouldn't start
                theirs = bool(there) and not _ours(patch, there, known)
                if spec.get("config") and theirs:
                    # settings changed since are the user's: they stay, and
                    # ours goes beside them, the way a drawer mod's do
                    fresh = target.with_name(target.name + ".new")
                    try:
                        beside, unreadable = (fresh.read_bytes() if fresh.is_file() else None), False
                    except OSError:
                        # one that can't be read can't be told from theirs
                        beside, unreadable = None, True
                    if there == data:
                        pass
                    elif {hashlib.sha256(d).hexdigest() for d in (data, *_before_rename(patch, data))} \
                            & set(earlier or ()):
                        # theirs started from this version of ours, under the preset's old
                        # name if it's had one since, and only changed after. ReShade
                        # rewrites its settings on every exit, so that's any install that's
                        # been run: nothing new to set beside them
                        shipped[spec["target"]] = hashlib.sha256(data).hexdigest()
                        if beside is not None and _ours(patch, beside, known):
                            # one an earlier run set beside them stays on the record
                            written.append(spec["target"] + ".new")
                    elif unreadable or (beside is not None and beside != data
                                        and not _ours(patch, beside, known)):
                        # a .new somebody has been editing is theirs as well
                        set_aside.append(f"your {spec['target']} and {fresh.name} stay as "
                                         "you had them")
                    else:
                        moved.append((fresh, beside, False))
                        write_atomic(fresh, data)
                        written.append(spec["target"] + ".new")
                        set_aside.append(f"your {spec['target']} stays as you had it, with "
                                         f"this version beside it as {fresh.name}")
                    if on_progress:
                        on_progress(Progress("install", index, len(staged), spec["target"]))
                    continue
                if theirs and backup.exists():
                    # one already set aside is never written over
                    take_back()
                    return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                                       backup_taken(spec["target"], backup))
                moved.append((target, there, not backup.exists()))
                if theirs:
                    write_atomic(backup, there, durable=True)
                    kept[spec["target"]] = hashlib.sha256(there).hexdigest()
                    set_aside.append(f"your own {spec['target']} is saved as {backup.name}")
                elif backup.is_file():
                    # one an earlier run left is theirs too, and still goes
                    # back, held to the hash taken when it was set aside
                    kept[spec["target"]] = (was.get(spec["target"])
                                            or hashlib.sha256(backup.read_bytes()).hexdigest())

                write_atomic(target, data)
                installed.append(spec["target"])
                written.append(spec["target"])
                fresh = target.with_name(target.name + ".new")
                if spec.get("config") and fresh.is_file():
                    # theirs went, deleted or ours copied over it, and a .new of ours an
                    # earlier run set beside it has nothing left to stand beside
                    try:
                        beside = fresh.read_bytes()
                    except OSError:
                        beside = None
                    if beside is not None and _ours(patch, beside, known):
                        if remove_file(fresh):
                            moved.append((fresh, beside, False))
                        else:
                            written.append(spec["target"] + ".new")
                if on_progress:
                    on_progress(Progress("install", index, len(staged), spec["target"]))
        except BaseException:
            take_back()
            raise
    except Cancelled:
        raise
    except SPEAKS_FOR_ITSELF as e:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))
    except PermissionError:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           permission_message(target_dir))
    except OSError as e:
        # antivirus taking the dll as it lands, a full disk: the sentence for it
        from patcher.access import write_failure

        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           write_failure(e, target_dir))
    except Exception as e:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           f"couldn't install it: {e}")

    # every new file is on disk before the old one comes off. If one won't come
    # off, the run goes back the way it came, since a failed run records nothing
    problem = displace()
    if problem:
        take_back()
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED, problem)

    # the folder we actually wrote into. the manifest names .../addons/..., and
    # install_target_dir resolves that segment to whichever of
    # addons/extra/extras/mods/dll the install already had. recording the
    # manifest path meant revert looked in a folder nothing was ever put in,
    # deleted nothing, and reported success.
    revert_info = {"dir": str(target_dir.relative_to(install_dir)).replace("\\", "/")}
    if kept:
        revert_info["kept"] = kept
    if shipped:
        revert_info["shipped"] = shipped
    # a folder's worth is a count, a dll or two is its name
    said = [f"installed {', '.join(installed)}" if len(installed) <= 4
            else f"installed {len(installed)} files{_into(installed)}"]
    if displaced:
        said.append(f"removed an older copy from {Path(patch.moved_from[0]).parent}")
    if any(spec["target"].lower() == "d3d9.dll" for spec in patch.bundled_files or ()):
        said += _ff13fix_beside(target_dir, {spec["target"].lower()
                                             for spec in patch.bundled_files})
    return PatchResult(patch.id, patch.label, PatchStatus.APPLIED,
                       "; ".join(said + set_aside),
                       installed_files=written,
                       revert_info=revert_info)


def _before_rename(patch: Patch, data: bytes) -> list:
    """settings of ours pointed back at each name a preset in them shipped as, one per name"""
    import re

    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    text = data[len(bom):].decode("utf-8", "surrogateescape")
    found = []
    for spec in patch.bundled_files:
        now = PurePosixPath(spec["target"]).name
        for old in spec.get("was") or ():
            then = PurePosixPath(old).name
            back = re.sub(PRESET_LINE, lambda m, now=now, then=then:
                          m.group(1) + m.group(2).replace(now, then) + m.group(3), text)
            if back != text:
                found.append(bom + back.encode("utf-8", "surrogateescape"))
    return found


def _into(names) -> str:
    """' into <folder>' when every one of them is in the same one, else nothing"""
    tops = {Path(name).parts[0] for name in names if len(Path(name).parts) > 1}
    flat = any(len(Path(name).parts) == 1 for name in names)
    return f" into {tops.pop()}" if len(tops) == 1 and not flat else ""


def _drawer_claims(install_dir: Path, manifest, target_dir: Path, patch: Patch) -> set:
    """
    a refuse-foreign patch's targets the Mods drawer's loader mods hold, as
    they'd be named relative to target_dir. Those are kept beside the exe
    """
    if not any(spec.get("foreign") == "refuse" for spec in patch.bundled_files):
        return set()
    from patcher.mods import loader_mods
    from patcher.paths import undo_store_for

    exe_dir = (install_dir / manifest.executable_relative_path).parent
    try:
        held = loader_mods.claimed(undo_store_for(install_dir))
        prefix = target_dir.resolve().relative_to(exe_dir.resolve()).as_posix().lower()
    except (OSError, ValueError):
        return set()
    if prefix in ("", "."):
        return held
    return {key[len(prefix) + 1:] for key in held if key.startswith(prefix + "/")}


def backup_taken(name: str, backup: Path) -> str:
    """the refusal for a file that isn't ours over one that's already set aside"""
    return (f"{name} here isn't the one this app installs, and {backup.name} already holds "
            "the file it set aside before, so nothing has been changed. Move whichever one "
            "you don't want out of the folder, then press Apply again.")


# every build of the shim writes this log. an old one set aside as the user's
# would come back into the loader's path on Revert, and an early build that
# can't load is a game that doesn't start
SHIM_LOG_MARK = "ff13-dinput8-shim.log".encode("utf-16-le")


def _ours(patch: Patch, data: bytes, known: set[str]) -> bool:
    """whether a file already in a bundled install's slot came from this app"""
    import hashlib

    if hashlib.sha256(data).hexdigest() in known:
        return True
    return patch.id == "install_dinput8_shim" and SHIM_LOG_MARK in data


VOICES = {"u": "English", "c": "Japanese"}


def voices_said(variants) -> str:
    """the archive letters an injection went into, said the way the Mods drawer says them"""
    names = sorted({VOICES[v] for v in variants if v in VOICES})
    if len(names) == 2:
        return "both voice sets"
    return f"the {names[0]} voice set" if names else "the game's archives"


def missing_from_patcher(source: str) -> str:
    """a file the patcher ships, gone from its folder: antivirus, or a half extracted zip"""
    return f"the patcher's own {Path(source).name} is missing. Redownload the patcher"


def _injected(patch: Patch, result: dict, earlier: dict | None) -> PatchResult:
    """
    an apply_injection() summary as a result. The journals and index backups
    are recorded because they are written whether or not anyone remembers
    them, and revert has no other way to find them, including an earlier
    run's for a pair this one found already holding the payload.
    """
    from patcher.archive.transaction import carry_undo

    where = result["archives"]
    kept = [k["variant"] for k in result.get("kept", [])]
    said = f"installed into {voices_said(where)}" if where else f"already in {voices_said(kept)}"
    if where and kept:
        said += f", {voices_said(kept)} had it already"
    return PatchResult(patch.id, patch.label, PatchStatus.APPLIED, said,
                       installed_files=[Path(f).name for f in result["files"]],
                       revert_info=carry_undo(result, earlier))


def recorded_digests(records) -> set[str]:
    """
    the sha256 of every file these state records say we installed, and of the
    settings versions a run left the user's own copies in place of
    """
    found = set()
    for record in records:
        for key in ("digests", "shipped"):
            digests = record.get(key) if isinstance(record, dict) else None
            if isinstance(digests, dict):
                found.update(d for d in digests.values() if isinstance(d, str))
    return found


def apply_archive_inject(install_dir: Path, manifest: GameManifest, patch: Patch,
                         fetch: Callable[[str], bytes] = default_fetch,
                         on_progress=None, cancel=None,
                         undo_store: Path | None = None,
                         supplied_file: Path | None = None,
                         earlier: dict | None = None) -> PatchResult:
    """
    injects a mod into the game's packed archives during a normal patch run.

    the payload has to come from somewhere first. supplied_file is what the
    drop path hands in; otherwise we try the manifest's download_url, and if
    that host will not serve an app (which is the usual answer for a community
    file host) we report NEEDS_FILE so the acquisition flow takes over.
    """
    import fnmatch as _fn
    import hashlib
    import tempfile

    from patcher.archive.inject import Replacement
    from patcher.paths import bundled_data_root, undo_store_for
    from patcher.archive.transaction import TransactionError, apply_injection
    from patcher.download import IncompleteDownload, opener_for, stream_download
    from patcher.ingest import IngestError, extract_member, scan
    from patcher.mods.file_install import NotAFileError, looks_like_web_page

    if not patch.archive_relative_dir or not patch.injections:
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           "manifest is missing archive_relative_dir or injections")

    # the bundled path: every payload ships with the patcher, so there is no
    # download, no mod page, no drop and no prompt. press Apply and it
    # installs. this is what a patch should look like when licensing allows
    # it, and everything below is only reached when it does not.
    if all(spec.get("bundled") for spec in patch.injections):
        replacements, problems = [], []
        for spec in patch.injections:
            asset = bundled_data_root() / spec["bundled"]
            if not asset.is_file():
                problems.append(missing_from_patcher(spec["bundled"]))
                continue
            data = asset.read_bytes()
            expected = spec.get("sha256")
            if expected:
                actual = hashlib.sha256(data).hexdigest()
                if actual.lower() != expected.lower():
                    # a bundled asset is ours to vouch for. a mismatch means
                    # the build is not what we shipped, and writing it into a
                    # game archive on that basis would be reckless.
                    problems.append(f"the patcher's own {Path(spec['bundled']).name} is "
                                    "damaged. Redownload the patcher")
                    continue
            replacements.append(Replacement(spec["game_path"], data))

        if problems:
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED, "; ".join(problems))

        store = Path(undo_store) if undo_store else undo_store_for(install_dir)
        try:
            result = apply_injection(
                install_dir, patch.archive_relative_dir, replacements, store=store,
                variants=tuple(patch.archive_variants),
                on_progress=on_progress, cancel=cancel)
        except TransactionError as e:
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))
        return _injected(patch, result, earlier)

    staging = Path(tempfile.mkdtemp(prefix="ffxiii-inject-",
                                    dir=scratch_near(install_dir)))
    unpacked = None
    try:
        source = Path(supplied_file) if supplied_file else None
        if source is None:
            if not patch.download_url:
                return PatchResult(patch.id, patch.label, PatchStatus.NEEDS_FILE,
                                   "drop the mod archive on this window to install it")
            name = zipnames.filename_from_url(patch.download_url)
            source = staging / (name if "." in name else name + ".zip")
            try:
                stream_download(patch.download_url, source,
                                opener=opener_for(fetch),
                                on_progress=on_progress, cancel=cancel, label=patch.label)
                with source.open("rb") as handle:
                    if looks_like_web_page(handle.read(512)):
                        raise NotAFileError("the host served a web page")
            except Cancelled:
                raise
            except IncompleteDownload as e:
                # the connection dropped, which is not the host refusing us.
                # the .part is still there and a retry resumes from it, so
                # sending the user to download it by hand throws that away.
                return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))
            except Exception:
                # the normal outcome for a community host. not a failure: the
                # acquisition flow opens the page and catches the download.
                return PatchResult(
                    patch.id, patch.label, PatchStatus.NEEDS_FILE,
                    "we'll open the mod page and pick up the download automatically",
                    page_url=patch.fallback_page_url or None)

        try:
            found = scan(source)
        except IngestError as e:
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))
        # scan unpacks a format python cannot read, and that copy lands beside
        # the source, which for a dropped file is the user's Downloads folder
        unpacked = found

        members = [c.member for c in found.candidates]
        replacements, missing = [], []
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
            # both targets or neither. a partial injection fixes one weather
            # zone and leaves the other broken, which reads as a game bug.
            names = ", ".join(Path(m).name for m in missing)
            return PatchResult(patch.id, patch.label, PatchStatus.NEEDS_FILE,
                               f"that archive is missing {names}")

        store = Path(undo_store) if undo_store else undo_store_for(install_dir)
        try:
            result = apply_injection(
                install_dir, patch.archive_relative_dir, replacements, store=store,
                variants=tuple(patch.archive_variants),
                on_progress=on_progress, cancel=cancel)
        except TransactionError as e:
            return PatchResult(patch.id, patch.label, PatchStatus.FAILED, str(e))

        return _injected(patch, result, earlier)
    finally:
        import shutil as _sh
        if unpacked is not None:
            unpacked.cleanup()
        _sh.rmtree(staging, ignore_errors=True)


def describe_nexus(install_dir: Path, manifest: GameManifest, patch: Patch, source) -> PatchResult:
    target = install_dir / (patch.install_relative_path or "")
    return PatchResult(patch.id, patch.label, PatchStatus.WOULD_APPLY,
                       f"would install Nexus mod {patch.nexus_mod_id} into {target}")


def ff13_laa_guard(install_dir: Path, manifest: GameManifest, patch: Patch):
    """
    decides what to do about XIII's 4GB patch, and repairs an install that a
    bad one already broke.

    XIII's CEG check reads its own exe, so a patched exe needs something in
    front of the check handing it untouched.exe. Right now that's the dinput8
    shim (COVER_SHIM), or a proxy that says it redirects exe reads. The
    community replacement exe has no CEG and takes the patch as is.

    Allowed isn't proven. The launch check records a DRM refusal against the
    patch and its cover, and once that exists the patch comes off and stays
    off. See docs/4gb-patch.md.

    None to apply normally, or a PatchResult that replaces it.
    """
    from patcher.patch_ops import identify_xiii_build, is_laa_enabled

    target = resolve_target_path(install_dir, manifest, patch)
    if not target.is_file():
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           f"couldn't find {target.name}")

    data = target.read_bytes()
    build = identify_xiii_build(data)
    patched = is_laa_enabled(data)

    if build.get("safe_to_edit"):
        return None       # community replacement: no CEG, patch away

    cover = current_cover(install_dir, manifest)

    if cover != COVER_NONE and launch_refused_this(manifest, patch, cover):
        # already tried with this cover on this machine and Steam said no, so
        # undo and stay undone. A different cover gets its own measurement.
        strip_redirect_addon(install_dir, manifest)
        strip_dinput8_shim(install_dir, manifest)
        return _undo_laa(target, patch, data,
                         "Steam's DRM refused to start the game with it on, so it stays "
                         "off on this install")

    if cover == COVER_PROXY:
        # A proxy that says in its own config that it redirects executable
        # reads. That is a claim the thing doing the work makes about itself at
        # the layer that does it, which is the only evidence here worth acting
        # on. Plain NovaFix does not write it, so it stays off by default.
        return None

    if cover == COVER_SHIM:
        # a static import: the loader runs its DllMain before the entry point,
        # where the CEG check is. A NovaFix add-on loads at render init, too
        # late. Still not proof on a given machine, which is what the launch
        # check is for.
        return None

    # nothing's in front of the check. Any redirect add-on left over is the
    # build that couldn't work, and a dinput8.dll that isn't our pinned shim
    # is one we can't vouch for, so both go.
    strip_redirect_addon(install_dir, manifest)
    strip_dinput8_shim(install_dir, manifest)

    # Wording only, and worth the lookup. Reaching here right after a refusal
    # means the cover was stripped on the way, so the current cover is none and
    # the generic message would be "this build can't take the patch" to someone
    # who watched the tool try it an hour ago.
    tried = refused_cover(manifest, patch)
    why = ("Steam's DRM refused to start the game with it on, so it stays off"
           if tried else
           "this is the Steam exe, which checks itself on launch, and 4GB "
           "Compatibility isn't in to cover that check, so patched it wouldn't start")

    if not patched:
        return PatchResult(patch.id, patch.label, PatchStatus.UNSAFE_BUILD, why if tried else
                           "left alone so the game still starts: this exe checks itself "
                           "against Steam's DRM on launch, and a patched one won't start")

    return _undo_laa(target, patch, data, why)


# What is standing between the 4GB patch and the DRM's self-check, named so a
# verdict can be filed against one of them rather than against the install.
COVER_SHIM = "dinput8_shim"
COVER_PROXY = "proxy_config"
COVER_NONE = "none"


def current_cover(install_dir: Path, manifest: GameManifest) -> str:
    """
    which mechanism, if any, is currently hiding the patch from the DRM.

    One function so the guard and the launch check cannot disagree about what
    is in place, which is the sort of drift that produces a verdict filed
    against the wrong thing.
    """
    if dinput8_shim_installed(install_dir, manifest):
        return COVER_SHIM
    if proxy_redirects_executable(install_dir, manifest):
        return COVER_PROXY
    return COVER_NONE


def refused_cover(manifest: GameManifest, patch: Patch) -> str | None:
    """
    the cover a recorded refusal was measured against, or None.

    For wording, never for deciding. What a mechanism that is no longer on the
    disk once did is not a reason to refuse the one that is, which is the whole
    point of keying the record; it is a very good reason not to tell somebody
    their build cannot take the patch when they watched the tool try it.
    """
    from patcher.launch_watch import DRM_ERROR
    from patcher.paths import state_path_for
    from patcher.state import load_state

    try:
        checks = load_state(state_path_for(manifest.game_id)).get("launch_checks")
    except OSError:
        return None
    if not isinstance(checks, dict):
        return None
    entry = checks.get(patch.id)
    if not isinstance(entry, dict) or entry.get("verdict") != DRM_ERROR:
        return None
    return entry.get("cover") or None


def launch_refused_this(manifest: GameManifest, patch: Patch, cover: str) -> bool:
    """
    whether an earlier launch with this patch on, under this same cover, was
    refused by the DRM. Per cover, because the add-on cover failing said
    nothing about whether the shim would work.
    """
    from patcher.paths import state_path_for
    from patcher.state import launch_blocked

    try:
        return launch_blocked(state_path_for(manifest.game_id), patch.id, cover)
    except OSError:
        # an unreadable state file means no evidence, which means carry on.
        # refusing to patch because a json could not be opened would be its own
        # bug report.
        return False


def _undo_laa(target: Path, patch: Patch, data: bytes, why: str) -> PatchResult:
    """
    puts the executable back the way Steam shipped it, from untouched.exe when
    that's this same build and by clearing the flag when it isn't.
    """
    # nothing to undo. this runs on every Apply for the whole affected
    # population, so without it the guard rewrites a perfectly good executable
    # once per run and reports a repair that did not happen.
    if not is_laa_enabled(data):
        return PatchResult(patch.id, patch.label, PatchStatus.UNSAFE_BUILD, why)

    how = unpatch_exe(target, patch.required_backup_filename or "untouched.exe", data)
    return PatchResult(patch.id, patch.label, PatchStatus.REPAIRED,
                       f"undid the 4GB patch by {how}. {why}",
                       installed_files=[])


def undo_laa_patch(install_dir: Path, manifest: GameManifest, why: str) -> PatchResult | None:
    """
    takes the 4GB patch back off for callers outside a patch run, like the
    launch check after Steam refused the game. Same repair as the guard so
    there's only one of it. None if there's no guarded patch or target.
    """
    patch = laa_patch(manifest)
    if patch is None:
        return None
    target = resolve_target_path(install_dir, manifest, patch)
    if not target.is_file():
        return None
    return _undo_laa(target, patch, target.read_bytes(), why)


def dinput8_shim_installed(install_dir: Path, manifest: GameManifest) -> bool:
    """
    whether our build of the dinput8 shim is beside the executable.

    Hashed against the manifest pin, not looked up by name, because a
    dinput8.dll in a game folder could be anybody's and this one is a static
    import: whatever sits there runs before the entry point with the game's
    full privileges. Only the build whose behaviour the tests describe counts.
    """
    shim = next((p for p in manifest.patches if p.id == "install_dinput8_shim"), None)
    return shim is not None and installed_on_disk(install_dir, shim)


class StripFailed(OSError):
    """a file we had to remove is still there, which is not a detail"""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"couldn't remove {path.name}")


def remove_file(path: Path) -> bool:
    """
    deletes a file, clearing read-only if that's what's in the way. True when
    it's gone, including when it was never there.
    """
    from patcher.access import clear_readonly

    if not path.exists():
        return True
    try:
        path.unlink()
        return True
    except OSError:
        pass
    if not clear_readonly(path):
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def shim_log_path(install_dir: Path, manifest: GameManifest) -> Path | None:
    """where the shim writes, so its silence can be told from its absence"""
    shim = next((p for p in manifest.patches if p.id == "install_dinput8_shim"), None)
    if shim is None:
        return None
    return install_target_dir(install_dir, shim) / "ff13-dinput8-shim.log"


def strip_dinput8_shim(install_dir: Path, manifest: GameManifest) -> list[str]:
    """
    takes our shim and its log back off, returning what it removed.

    Always paired with the 4GB patch coming off: a leftover static import the
    loader can't use is a game that won't start. Only ours though, dinput8.dll
    is a common mod name, so an unknown one gets named by the doctor instead.
    """
    shim = next((p for p in manifest.patches if p.id == "install_dinput8_shim"), None)
    if shim is None:
        return []
    home = install_target_dir(install_dir, shim)
    ours = installed_on_disk(install_dir, shim)
    taken = []
    for name in LEFTOVERS.get("install_dinput8_shim", ()):
        if name == "dinput8.dll" and not ours:
            continue
        if not (home / name).is_file():
            continue
        if not remove_file(home / name):
            # a failed delete has to surface, or the user's told the shim is
            # gone while a locked copy keeps the game from starting.
            raise StripFailed(home / name)
        taken.append(name)

    # the install set theirs aside to make room, and ours is going for good.
    # Only as the file that was set aside, since a wrong one here is a game
    # that won't start. Otherwise it stays aside and Revert says so
    theirs = backup_path_for(home / "dinput8.dll")
    if ours and theirs.is_file() and _set_aside_intact(manifest, shim, "dinput8.dll", theirs):
        try:
            theirs.replace(home / "dinput8.dll")
        except OSError:
            pass    # still set aside, and Revert takes it back from there
    return taken


def _set_aside_intact(manifest: GameManifest, patch: Patch, name: str, backup: Path) -> bool:
    """whether a file a bundled install set aside is still the one it recorded.
    True for a record from before those were kept, which went by name"""
    import hashlib

    from patcher.paths import state_path_for
    from patcher.state import load_state

    record = load_state(state_path_for(manifest.game_id))["applied_patches"].get(patch.id)
    kept = record.get("kept") if isinstance(record, dict) else None
    want = kept.get(name) if isinstance(kept, dict) else None
    try:
        data = backup.read_bytes()
    except OSError:
        return False
    # an empty one would be a dll the loader can't use, so it isn't put back
    return bool(data) and (want is None or hashlib.sha256(data).hexdigest() == want)


def redirect_addon_installed(install_dir: Path, manifest: GameManifest) -> bool:
    """
    whether our build of the redirect add-on is where NovaFix loads it.
    Reported only, so the doctor can tell it from an older build.
    """
    addon = next((p for p in manifest.patches if p.id == "install_exe_redirect"), None)
    return addon is not None and installed_on_disk(install_dir, addon)


def installed_on_disk(install_dir: Path, patch: Patch) -> bool:
    """
    whether our build of this patch's files is in the game folder.

    Goes by content where there's a pinned sha256, since an off-build d3d9.dll
    that passed on name alone once kept a broken install broken through every
    re-run. Unpinned (user supplied) files fall back to presence. Patches that
    aren't just named files return False and the state record answers.
    """
    import hashlib

    if patch.type != "bundled_install" or not patch.bundled_files:
        return False
    target_dir = install_target_dir(install_dir, patch)
    for spec in patch.bundled_files:
        target = target_dir / spec["target"]
        # a settings file still under the name it shipped as is the same
        # install, renamed the next time the patcher's at it
        if not target.is_file() and not any((target_dir / old).is_file()
                                            for old in spec.get("was") or ()):
            return False
        expected = spec.get("sha256")
        # a settings file is the user's to change, and ReShade rewrites its own
        # every time its menu does, so being there is all that counts
        if not expected or spec.get("config"):
            continue
        try:
            if hashlib.sha256(target.read_bytes()).hexdigest().lower() != expected.lower():
                return False
        except OSError:
            # unreadable is not "installed". the re-run will fail loudly with a
            # permission message, which is the more useful outcome than
            # silently treating it as done.
            return False
    return True


# the settings a ReShade.ini names presets with. 4.x wrote CurrentPresetPath,
# and the shortcut keys' presets are one comma separated list
PRESET_LINE = r"(?im)^(\s*(?:(?:Current)?PresetPath|PresetShortcutPaths)\s*=\s*)(.*?)(\s*)$"


def renamed_bundled(install_dir: Path, patch: Patch, state_path: Path | None = None,
                    ours: bool = False, busy=None) -> list:
    """
    moves a settings file of ours that shipped under an older name onto its
    current one, keeping whatever ReShade saved into it, and repoints a
    ReShade.ini still naming it. [(old, new)] for what moved.

    Only on an install that's ours (record or pinned files), never when the new
    name already exists, and not while busy() has a reason, since a running
    ReShade saves back under the name it opened.
    """
    import hashlib
    import os
    import re

    from patcher.state import load_state, save_state

    if patch.type != "bundled_install":
        return []
    pairs = [(old, spec["target"]) for spec in patch.bundled_files
             for old in spec.get("was") or ()]
    if not pairs:
        return []
    target_dir = install_target_dir(install_dir, patch)
    state = load_state(state_path) if state_path is not None else {"applied_patches": {}}
    record = state["applied_patches"].get(patch.id)
    moves = [(old, new) for old, new in pairs
             if (target_dir / old).is_file() and not (target_dir / new).exists()]
    if moves and record is None and not ours and not _pins_hold(target_dir, patch):
        return []
    if moves and busy is not None and busy():
        moves = []

    moved = []
    for old, new in moves:
        for suffix in ("", ".new"):
            source, target = target_dir / (old + suffix), target_dir / (new + suffix)
            if not source.is_file() or target.exists():
                continue
            try:
                os.replace(source, target)
            except OSError:
                # still under its old name, which installed_on_disk takes as in
                continue
            moved.append((old + suffix, new + suffix))

    def folder(path) -> str:
        return os.path.normcase(os.path.abspath(path))

    # by folder as well as name: a preset of the same name somewhere else is
    # somebody's copy, and pointing at ours from there finds nothing
    names = {(folder((target_dir / old).parent), PurePosixPath(old).name.lower()):
             PurePosixPath(new).name for old, new in moved}
    rewritten = {}
    for spec in patch.bundled_files if names else ():
        settings = target_dir / spec["target"]
        if not spec.get("config") or not settings.is_file():
            continue
        try:
            raw = settings.read_bytes()
        except OSError:
            continue
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw[3 if bom else 0:].decode("utf-8", "surrogateescape")

        def point(value, home=settings.parent):
            # the folder part kept as it was: ReShade writes it relative or whole
            cut = max(value.rfind("\\"), value.rfind("/")) + 1
            spelled = value[:cut].replace("\\", os.sep).replace("/", os.sep) or "."
            where = spelled if os.path.isabs(spelled) else os.path.join(home, spelled)
            new = names.get((folder(where), value[cut:].lower()))
            return value if new is None else value[:cut] + new

        def swap(line):
            values = line.group(2).split(",") if "shortcut" in line.group(1).lower() \
                else [line.group(2)]
            return line.group(1) + ",".join(map(point, values)) + line.group(3)

        pointed = re.sub(PRESET_LINE, swap, text)
        if pointed == text:
            continue
        data = (b"\xef\xbb\xbf" if bom else b"") + pointed.encode("utf-8", "surrogateescape")
        try:
            _rewritten(settings, data)
        except OSError:
            continue
        rewritten[spec["target"]] = (hashlib.sha256(raw).hexdigest(),
                                     hashlib.sha256(data).hexdigest())

    if record is not None:
        changed = False
        gone = {old + suffix: new + suffix for old, new in pairs for suffix in ("", ".new")
                if not (target_dir / (old + suffix)).exists()}
        files = record.get("files") or []
        if any(name in gone for name in files):
            record["files"] = [gone.get(name, name) for name in files]
            if isinstance(record.get("digests"), dict):
                record["digests"] = {gone.get(k, k): v for k, v in record["digests"].items()}
            changed = True
        digests = record.get("digests")
        for name, (before, after) in rewritten.items():
            # the line we changed isn't the user's change: as it went in
            # before, as it went in now
            if isinstance(digests, dict) and digests.get(name) == before:
                digests[name] = after
                changed = True
        if changed:
            try:
                save_state(state_path, state)
            except OSError:
                pass
    if moved:
        get_logger().info("%s: moved %s", patch.label,
                          ", ".join(f"{old} to {new}" for old, new in moved))
    return moved


# ReShade's built-in Generic Depth add-on swaps every big depth buffer a D3D9
# game makes for an INTZ texture and counts every draw call against it, all
# for effects that read depth. None of ours do (SMAA finds edges by colour,
# predication off), and XIII's field is thousands of draws a frame, so it's off on our
# install unless the ini already says what it wants
DEPTH_ADDON = "Generic Depth"


def depth_off(install_dir: Path, patch: Patch, state_path: Path | None = None,
              busy=None) -> bool:
    """
    `[ADDON] DisabledAddons=Generic Depth` in our ReShade.ini, written once.
    A DisabledAddons line already there is the user's (ReShade writes it when
    an add-on's ticked off in its menu), even an empty one, so it's left be,
    and so is a read-only file. Not while the game runs, since ReShade saves
    its own copy back on exit. True when it wrote
    """
    import hashlib
    import re
    import stat

    from patcher.state import load_state, save_state

    if patch.type != "bundled_install" or not patch.bundled_files:
        return False
    specs = [s for s in patch.bundled_files
             if s.get("config") and PurePosixPath(s["target"]).name.lower() == "reshade.ini"]
    if not specs:
        return False
    target_dir = install_target_dir(install_dir, patch)
    state = load_state(state_path) if state_path is not None else {"applied_patches": {}}
    record = state["applied_patches"].get(patch.id)
    if record is None and not _pins_hold(target_dir, patch):
        return False
    if busy is not None and busy():
        return False

    wrote = False
    for spec in specs:
        settings = target_dir / spec["target"]
        try:
            # read-only is how some keep ReShade off their settings, us too
            if not settings.stat().st_mode & stat.S_IWUSR:
                continue
            raw = settings.read_bytes()
        except OSError:
            continue
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw[3 if bom else 0:].decode("utf-8", "surrogateescape")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines(keepends=True)
        heads = [i for i, line in enumerate(lines) if re.match(r"\s*\[", line)]
        section = next((i for i in heads if lines[i].strip().lower() == "[addon]"), None)
        if section is not None:
            end = next((i for i in heads if i > section), len(lines))
            if any(re.match(r"(?i)\s*DisabledAddons\s*=", line) for line in lines[section + 1:end]):
                continue
            lines.insert(section + 1, f"DisabledAddons={DEPTH_ADDON}{newline}")
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            if lines and lines[-1].strip():
                lines.append(newline)
            lines += [f"[ADDON]{newline}", f"DisabledAddons={DEPTH_ADDON}{newline}"]
        data = (b"\xef\xbb\xbf" if bom else b"") + "".join(lines).encode("utf-8", "surrogateescape")
        try:
            _rewritten(settings, data)
        except OSError:
            continue
        wrote = True
        digests = record.get("digests") if record is not None else None
        before = hashlib.sha256(raw).hexdigest()
        # our line isn't the user's change, so the file's ours as it is now, and
        # the version that went in stays on the record as the one theirs grows
        # from, or the next Apply would set a .new of it beside them
        if isinstance(digests, dict) and digests.get(spec["target"]) == before:
            digests[spec["target"]] = hashlib.sha256(data).hexdigest()
            shipped = record.setdefault("shipped", {})
            if isinstance(shipped, dict):
                shipped.setdefault(spec["target"], before)
            try:
                save_state(state_path, state)
            except OSError:
                pass
    if wrote:
        get_logger().info("%s: ReShade's %s add-on off, nothing of ours reads depth",
                          patch.label, DEPTH_ADDON)
    return wrote


def _rewritten(path: Path, data: bytes) -> None:
    """write_atomic, read-only again after if it was: how some keep ReShade off their settings"""
    import os
    import stat

    locked = not path.stat().st_mode & stat.S_IWUSR
    write_atomic(path, data)
    if locked:
        os.chmod(path, stat.S_IREAD)


def _pins_hold(target_dir: Path, patch: Patch) -> bool:
    """every pinned file of a bundled patch that isn't settings, there and ours"""
    from patcher.state import digest_of

    pinned = [s for s in patch.bundled_files if s.get("sha256") and not s.get("config")]
    return bool(pinned) and all(
        (digest_of(target_dir / s["target"]) or "").lower() == s["sha256"].lower()
        for s in pinned)


def injection_on_disk(install_dir: Path, patch: Patch) -> bool | None:
    """
    whether our payload is inside the game's archives right now.

    Read straight from the archive: the index gives the entry's offset and
    size, and the inflated bytes are hashed against the manifest's pin. Two
    seeks on a 12GB file. The span journal can't answer this on its own since
    it lives in the undo store and can go missing.

    None when there's nothing to compare against, so the caller falls back
    instead of reading "can't tell" as "not installed".
    """
    from patcher.archive.transaction import discover

    wanted = pinned(patch)
    if not wanted or not patch.archive_relative_dir:
        return None

    variants = tuple(patch.archive_variants) if patch.archive_variants else ("u", "c")
    pairs = discover(install_dir, patch.archive_relative_dir, variants)
    if not pairs:
        return None
    for pair in pairs:
        present = injection_in(pair.filelist_path, pair.whitebin_path, wanted)
        if present is not True:
            return present
    return True


def pinned(patch: Patch) -> list:
    """(game path, sha256) for every payload of an injection the manifest pins"""
    if getattr(patch, "type", None) != "archive_inject" or not getattr(patch, "injections", None):
        return []
    return [(spec["game_path"], spec["sha256"]) for spec in patch.injections
            if spec.get("game_path") and spec.get("sha256")]


def injection_in(index_path: Path, image_path: Path, wanted: list) -> bool | None:
    """injection_on_disk() for one index and the archive it describes"""
    import hashlib
    import zlib

    from patcher.archive import filelist as filelist_mod

    try:
        index = filelist_mod.parse(Path(index_path).read_bytes())
        with Path(image_path).open("rb") as handle:
            for game_path, expected in wanted:
                _, entry = index.locate(game_path)
                if entry is None or entry.is_stub:
                    return False
                handle.seek(entry.byte_offset)
                blob = handle.read(entry.stored)
                if len(blob) != entry.stored:
                    return False
                if entry.is_compressed:
                    try:
                        blob = zlib.decompress(blob)
                    except zlib.error:
                        # the index claims a deflate stream and the bytes
                        # are not one, so whatever is in there is not ours
                        return False
                    if len(blob) != entry.uncompressed:
                        return False
                if hashlib.sha256(blob).hexdigest().lower() != expected.lower():
                    return False
    except (OSError, ValueError, KeyError):
        # an unreadable or unparseable index. the patch itself reports the real
        # problem when it runs; guessing either way here would be worse
        return None
    return True


def proxy_redirects_executable(install_dir: Path, manifest: GameManifest) -> bool:
    """
    whether the installed proxy can hide a patched executable from the DRM.

    stock NovaFix cannot: its CreateFile hook filters to .ini files. a fork
    carrying the redirect advertises it by writing RedirectExecutableReads into
    its own config, so this reads that rather than trusting a version number.
    """
    from patcher import novafix_config

    proxy = next((p for p in manifest.patches if p.id == "install_novafix"), None)
    if proxy is None:
        return False
    values = novafix_config.read(
        novafix_config.config_path(install_dir, proxy.install_relative_path or ""))
    setting = values.get("Graphics", {}).get("RedirectExecutableReads", "")
    return setting.strip().lower() in ("on", "true", "1", "yes")


def strip_redirect_addon(install_dir: Path, manifest: GameManifest) -> list[str]:
    """
    removes the exe-redirect add-on and its log wherever NovaFix would read
    them. Returns what it took.

    Older builds armed whenever untouched.exe existed, so after the 4GB patch
    came off they still fed the DRM a different file and it refused the game.
    untouched.exe stays, it's the only clean copy.
    """
    addon = next((p for p in manifest.patches if p.id == "install_exe_redirect"), None)
    if addon is None:
        return []
    home = install_target_dir(install_dir, addon)
    taken = []
    for name in LEFTOVERS.get("install_exe_redirect", ()):
        stale = home / name
        if not stale.is_file():
            continue
        if not remove_file(stale):
            raise StripFailed(stale)
        taken.append(name)
    try:
        if home.is_dir() and not any(home.iterdir()):
            home.rmdir()
    except OSError:
        pass
    return taken


def ff13_shim_guard(install_dir: Path, manifest: GameManifest, patch: Patch):
    """
    keeps the dinput8 shim off an install whose launch already refused it.
    Otherwise the shim goes in and ff13_laa_guard strips it out every run,
    with a window where the game has a static import and no patch to cover.
    """
    target = next((p for p in manifest.patches if p.build_guard == "ff13_laa"), None)
    # COVER_SHIM specifically. A refusal recorded against some other mechanism
    # is not a measurement of this one, and treating it as one is how a verdict
    # about software we no longer ship ends up blocking software we do.
    if target is None or not launch_refused_this(manifest, target, COVER_SHIM):
        return None
    return PatchResult(patch.id, patch.label, PatchStatus.UNSAFE_BUILD,
                       "not installed: Steam's DRM still refused the game with this in "
                       "place, so it has nothing left to do")


def lr_laa_guard(install_dir: Path, manifest: GameManifest, patch: Patch):
    """
    decides what to do about Lightning Returns' 4GB patch.

    Nobody knows whether LR's Steam build checks itself, since no LR mod has
    ever edited the exe. So this only checks what the file can answer (it's
    32-bit, so really capped at 2GB) and leaves the DRM question to the launch
    check. Unlike XIII, no cover is the normal state here, so a recorded
    refusal is the only thing keeping the patch off.
    """
    from patcher.patch_ops import read_fingerprint

    target = resolve_target_path(install_dir, manifest, patch)
    if not target.is_file():
        return PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                           f"couldn't find {target.name}")

    data = target.read_bytes()
    try:
        fingerprint = read_fingerprint(data)
    except ValueError as e:
        return PatchResult(patch.id, patch.label, PatchStatus.UNSAFE_BUILD,
                           f"couldn't read the exe ({e}), so it was left alone")

    # I386 with a PE32 optional header. A 64-bit rerelease addresses far past
    # 4GB already and the flag means nothing in it, so flipping a bit in a
    # header we have misread is all that could come of running on one.
    if fingerprint["machine"] != 0x014C or fingerprint["magic"] != 0x10B:
        return PatchResult(patch.id, patch.label, PatchStatus.UNSAFE_BUILD,
                           "this build is not 32-bit, so it is not limited to 2GB and "
                           "there is nothing for the 4GB patch to do")

    # the record first, and an already-patched executable is exactly the state
    # it has to act on, so there is no shortcut past it here. _undo_laa is what
    # no-ops when there is nothing to take off.
    if not launch_refused_this(manifest, patch, current_cover(install_dir, manifest)):
        return None

    return _undo_laa(target, patch, data,
                     "Steam refused to start the game with it on, so it stays off on "
                     "this install")


def save_anywhere_guard(install_dir: Path, manifest: GameManifest, patch: Patch):
    """keeps Save Anywhere out while a code mod writes where it hooks"""
    from patcher import save_anywhere

    hit = save_anywhere.blocking(install_dir, manifest)
    if hit is None:
        return None
    owner, code, site = hit
    # it would install fine and then switch itself off at launch, which
    # reads to the user as Save Anywhere just not working
    return PatchResult(patch.id, patch.label, PatchStatus.BLOCKED,
                       f"not installed: {owner}'s code patch at {code.rva:X} changes the "
                       f"game code Save Anywhere hooks ({site}), which would switch Save "
                       f"Anywhere off. Remove {owner} first if you'd rather have this")


def their_audio_fix(install_dir: Path, manifest: GameManifest) -> str | None:
    """
    where cyanea-bt's FFXIII-2 AudioFix is, if it's in. Found by the name in
    its version info, which their own installer, a hand install beside the exe
    and an older copy of this patcher putting it in addons/ all share
    """
    from patcher import pe_name
    from patcher.novafix_config import resolve_addons_dir

    bin_dir = (install_dir / manifest.executable_relative_path).parent
    places = sorted(bin_dir.glob("*.dll"))
    addons = resolve_addons_dir(bin_dir)
    if addons.is_dir():
        places += sorted(p for p in addons.rglob("*") if p.suffix.lower() in (".dll", ".asi"))
    for path in places:
        try:
            name = pe_name.display_name(path.read_bytes())
        except OSError:
            continue
        if name.lower().startswith("ffxiii-2 audiofix"):
            return path.relative_to(install_dir).as_posix()
    return None


def xiii2_audio_guard(install_dir: Path, manifest: GameManifest, patch: Patch):
    """keeps our audio fix out while theirs is in"""
    theirs = their_audio_fix(install_dir, manifest)
    if theirs is None:
        return None
    # theirs fixes the same bugs plus cutscene surround, and two fixes hooking
    # the same functions is a game with no sound
    return PatchResult(patch.id, patch.label, PatchStatus.SKIPPED,
                       f"FFXIII-2 AudioFix is already installed ({theirs}) and fixes the same "
                       f"things, so {patch.label} stays out of its way. Delete that file and "
                       "Apply again if you'd rather have this one")


BUILD_GUARDS = {"ff13_laa": ff13_laa_guard, "ff13_shim": ff13_shim_guard,
                "lr_laa": lr_laa_guard, "xiii_save_anywhere": save_anywhere_guard,
                "xiii2_audio": xiii2_audio_guard}

# The guards that mark a 4GB patch, which is to say the patches a launch can
# refuse. The launch check, the revert and undo_laa_patch all need to find
# "this game's 4GB patch" and none of them care which game they are looking at,
# so the set lives here rather than as the string "ff13_laa" in four files.
# ff13_shim_guard is the exception and still names ff13_laa on purpose: the
# shim covers XIII's executable and only XIII's.
LAA_GUARDS = ("ff13_laa", "lr_laa")


def _ff13fix_beside(folder: Path, ours: set) -> list:
    """
    what NovaFix will switch its own fixes off for, beside it: a .dll put
    there by hand that's FF13Fix or built on it. Said, since it costs the
    frame pacing NovaFix is here for and nothing in the game says why
    """
    from patcher.mods.loader_mods import ff13fix_built

    try:
        found = sorted(p.name for p in Path(folder).iterdir()
                       if p.suffix.lower() in (".dll", ".asi") and p.name.lower() not in ours
                       and ff13fix_built(p))
    except OSError:
        return []
    return [f"{name} beside it is built on FF13Fix, so NovaFix turns its own fixes off while "
            f"it's there; take it out of {Path(folder).name} for NovaFix's frame pacing"
            for name in found]


def laa_patch(manifest: GameManifest) -> Patch | None:
    """this game's 4GB patch, or None if it has not got one"""
    return next((p for p in manifest.patches if p.build_guard in LAA_GUARDS), None)


def held_off(install_dir: Path, manifest: GameManifest) -> dict:
    """
    patch id -> why, for the 4GB patch while a launch refusal on record keeps
    it off, which is what its guard does with one. The Launch row has no
    business waiting on a patch Apply won't put on: it hid LR's for good
    """
    patch = laa_patch(manifest)
    if patch is None:
        return {}
    cover = current_cover(install_dir, manifest)
    # XIII's guard keeps it off with nothing covering it whatever the record
    # says, and words it as the refusal when there's one
    stays = launch_refused_this(manifest, patch, cover) or (
        patch.build_guard == "ff13_laa" and cover == COVER_NONE
        and refused_cover(manifest, patch) is not None)
    if not stays:
        return {}
    return {patch.id: "Steam refused to start the game with it on, so it stays off on this "
                      "install. The game starts without it."}


def purge(install_dir: Path, manifest: GameManifest, patch_id: str, state_path):
    """
    removes a patch that's being superseded, and anything it left behind.

    Lives here because it needs state and the engine never imports it; callers
    pass it as on_supersede. Only deletes what state says we installed, plus
    the files the old tool writes itself at runtime.
    """
    from patcher.revert import revert_patch
    from patcher.state import load_state

    patch = next((p for p in manifest.patches if p.id == patch_id), None)
    removed = []
    if patch is not None and patch_id in load_state(state_path)["applied_patches"]:
        result = revert_patch(install_dir, manifest, patch, state_path)
        if not result.ok:
            return (patch.label, f"couldn't remove the old {patch.label}: {result.detail}")
        removed.extend(result.removed)

    # install_target_dir, not the raw manifest path: a patch routed through the
    # add-on folder lands in whichever of addons/extra/extras/mods/dll the
    # install actually has, and looking in the one the manifest names finds
    # nothing while the real file stays loaded.
    folders = {install_target_dir(install_dir, p) for p in manifest.patches
               if p.install_relative_path}
    stuck, aside = [], []
    for leftover in LEFTOVERS.get(patch_id, ()):
        for directory in folders:
            stale = directory / leftover
            if not stale.is_file():
                continue
            if leftover in KEPT_LEFTOVERS:
                outcome = set_aside(stale)
                if outcome == "aside":
                    aside.append(leftover)
                    continue
                if outcome == "kept":
                    # a different one already sits under the .backup name, so
                    # neither is ours to replace. Off its name is all NovaFix
                    # needed, and that's the part that couldn't happen
                    stuck.append(leftover)
                    continue
                # an identical copy is already set aside: this one is spare
            if remove_file(stale):
                removed.append(leftover)
            else:
                # NOT swallowed. NovaFix scans loaded modules for FF13Fix
                # markers and silently disables its own overlapping features
                # when it finds any, so a locked FF13Fix.ini that we report
                # nothing about is a NovaFix that quietly does less, on a run
                # that looked completely green.
                stuck.append(leftover)

    # and take the folder with it if we emptied it. an add-on directory that
    # still exists reads as installed to anything looking, including our own
    # doctor, and an empty one has nothing left to say.
    if patch is not None and patch.install_relative_path:
        home = install_target_dir(install_dir, patch)
        try:
            if home.is_dir() and not any(home.iterdir()):
                home.rmdir()
        except OSError:
            pass

    if not removed and not stuck and not aside:
        return None
    # a superseded patch is usually gone from the manifest, so there is no
    # label to borrow. printing the raw id leaks an internal name at exactly
    # the moment we are explaining something to a user.
    label = patch.label if patch is not None else SUPERSEDED_LABELS.get(patch_id, patch_id)
    if stuck:
        names = ", ".join(sorted(set(stuck)))
        return (label, f"couldn't remove {names}. close the game and run this again, "
                       "or the old files stay active alongside the new ones")
    said = []
    if removed:
        said.append(f"removed to make way: {', '.join(sorted(set(removed)))}")
    if aside:
        said.append(", ".join(f"your {n} is saved as {n}.backup" for n in sorted(set(aside))))
    return (label, "; ".join(said))


# names for patches we no longer carry, so the purge can still say what it
# removed in words a player recognises
SUPERSEDED_LABELS = {"install_ff13fix": "FF13Fix"}


def supersede_handler(install_dir: Path, manifest: GameManifest, state_path):
    """the on_supersede callback callers pass into run_patches"""
    return lambda patch_id: purge(install_dir, manifest, patch_id, state_path)


# of those, the ones holding somebody's settings. Moved off the name rather than
# deleted: the player's FF13Fix tuning is theirs, and Revert puts it back beside
# the FF13Fix dll it puts back
KEPT_LEFTOVERS = {"FF13Fix.ini"}


def set_aside(path: Path) -> str:
    """
    path moved to its .backup name. "aside" when it went, "same" when an
    identical copy is already there, "kept" when a different one is
    """
    backup = backup_path_for(path)
    try:
        if backup.exists():
            return "same" if backup.read_bytes() == path.read_bytes() else "kept"
        os.replace(path, backup)
    except OSError:
        return "kept"
    return "aside"


def put_back_leftovers(install_dir: Path, manifest: GameManifest, patch: Patch) -> list:
    """the settings a supersede set aside for this patch, back under their names"""
    folders = {install_target_dir(install_dir, p) for p in manifest.patches
               if p.install_relative_path}
    back = []
    for superseded in patch.replaces:
        for name in KEPT_LEFTOVERS.intersection(LEFTOVERS.get(superseded, ())):
            for directory in folders:
                original, backup = directory / name, backup_path_for(directory / name)
                if backup.is_file() and not original.exists():
                    try:
                        os.replace(backup, original)
                        back.append(name)
                    except OSError:
                        pass
    return back


# files a superseded tool writes at runtime. never ours, never in our state,
# and precisely what the replacement looks for when deciding whether to
# disable its own features.
LEFTOVERS = {
    # the readmes and licences survived a real purge because only the ini and
    # logs were listed. they are litter rather than a hazard, but "FF13Fix is
    # gone" should mean gone.
    "install_ff13fix": ("FF13Fix.ini", "FF13Fix.log", "d3d9.log",
                        "FF13Fix_README.txt", "FF13Fix_LICENSE.txt",
                        "MinHook_LICENSE.txt"),
    # This one IS a hazard, which is why it is listed by name rather than left
    # to the state record. The add-on hooks CreateFileA/W before the game's
    # entry point whenever untouched.exe exists, and an install whose state we
    # never recorded still has the .asi on disk and still gets the hook. Its
    # own log goes too, or the folder looks live to anyone reading it.
    "install_exe_redirect": ("00_ff13_exe_redirect.asi", "ff13-exe-redirect.log"),
    # the shim is a static import, so after a DRM refusal it has to come off
    # with the 4GB patch or the game still won't start.
    "install_dinput8_shim": ("dinput8.dll", "ff13-dinput8-shim.log"),
}

# what the mods we ship write beside themselves each time the game runs. Never
# on a record, since nothing of ours made them, and nothing reads them once the
# mod's off, so Revert takes them with it
RUNTIME_LOGS = {
    "install_novafix": ("NovaFix.log",),
    "install_dinput8_shim": ("ff13-dinput8-shim.log",),
    "install_saviors_patch": ("SaviorsPatch.log",),
    "install_lr_mouse": ("lr-mouse.log",),
    "install_save_anywhere": ("xiii-save-anywhere.log", "xiii-save-anywhere.log.1", "debug/pause-*.bin",
                              "pause-*.bin"),
    # ReShade's, beside its ini. 6.x makes the name up as it starts, so both
    # the names it's gone by
    "install_reshade": ("ReShade/ReShade.log", "ReShade/d3d9.log"),
}


def run_patches(
    install_dir: Path,
    manifest: GameManifest,
    enabled_patch_ids: set[str],
    already_applied_check: Callable[[str], bool] | None = None,
    on_applied: Callable[[PatchResult], None] | None = None,
    fetch: Callable[[str], bytes] = default_fetch,
    dry_run: bool = False,
    nexus_source=None,
    on_progress=None,
    cancel=None,
    undo_store=None,
    supplied_files: dict | None = None,
    on_supersede=None,
    recorded: Callable[[str], dict] | None = None,
) -> list[PatchResult]:
    """
    runs every patch in enabled_patch_ids, in manifest order. Everything else
    comes back skipped, so there's always one result per patch.

    already_applied_check and on_applied are how state plugs in without this
    module importing it. Leave them None to run with no state at all.
    dry_run still runs the check (it's read-only) so an applied patch says so,
    but describe_* runs instead of apply_* and on_applied never fires.
    recorded is what an earlier run stored per patch id, so a bundled install
    can tell our older file from the user's.
    """
    supplied_files = supplied_files or {}
    results = []
    # what is on disk before this run, plus anything we apply during it. a
    # prerequisite satisfied earlier in the same run has to count, otherwise
    # ticking a mod and the mod it builds on together would always block the
    # second one.
    satisfied = set()
    by_id = {p.id: p for p in manifest.patches}

    for patch in manifest.patches:
        if patch.id not in enabled_patch_ids:
            results.append(PatchResult(patch.id, patch.label, PatchStatus.SKIPPED, "not selected"))
            continue
        if patch.retired:
            # asked for anyway, by a page from before it went. What an earlier
            # run put in still comes off, from the Mods drawer or Revert
            results.append(PatchResult(patch.id, patch.label, PatchStatus.SKIPPED,
                                       "isn't part of the patcher any more"))
            continue

        missing = [
            by_id[required] for required in patch.requires
            if required in by_id and required not in satisfied
            and not (already_applied_check is not None and already_applied_check(required))
            # what the game loads is what is on disk, so that is what settles a
            # prerequisite. state gets lost every time someone re-extracts the
            # app, and a run where NovaFix is sitting right there but its record
            # is gone should not report "needs NovaFix first".
            and not installed_on_disk(install_dir, by_id[required])
        ]
        # the user's words, never ours. this said "needs restore_console_content
        # installed first", which is an internal id nothing on screen uses.
        missing_labels = ", ".join(p.label for p in missing)

        if patch.replaces and on_supersede and not dry_run:
            # NovaFix scans loaded modules for FF13Fix markers and silently
            # disables its own overlapping features when it finds any. so
            # superseding it means actually removing the old files, not just
            # ceasing to install them: a user who ran the old version would
            # otherwise get a NovaFix that quietly does less. That's real
            # deleting, so a dry run skips it.
            for superseded in patch.replaces:
                note = on_supersede(superseded)
                if note:
                    label, detail = note
                    results.append(PatchResult(superseded, label, PatchStatus.SKIPPED, detail))

        if patch.type == "manual":
            # a manual patch is never installed by us, so a prerequisite that
            # is also manual can never be ticked off and BLOCKED would be a
            # permanent dead end. say what order to do them in instead.
            # player_note, never notes. notes is maintainer documentation full
            # of patch ids and manifest field names, and it was going straight
            # into the hint bar.
            detail = patch.player_note or "this one has to be installed by hand"
            if missing:
                detail = f"install {missing_labels} first, then this one. {detail}"
            results.append(PatchResult(
                patch.id, patch.label, PatchStatus.MANUAL, detail,
                page_url=patch.manual_url))
            continue

        if missing:
            # ordering here is not cosmetic. A mod that reads files another
            # one extracts, run first, leaves something that looks installed
            # and renders wrong, which is worse than a refusal.
            results.append(PatchResult(
                patch.id, patch.label, PatchStatus.BLOCKED,
                f"needs {missing_labels} first"))
            continue

        if patch.build_guard and not dry_run:
            verdict = BUILD_GUARDS.get(patch.build_guard)
            try:
                outcome = verdict(install_dir, manifest, patch) if verdict else None
            except Exception as e:
                # a guard writes the exe before any apply_* runs, outside their
                # handlers. Unelevated on Program Files this throws first, and
                # it mustn't take the rest of the run with it.
                results.append(PatchResult(
                    patch.id, patch.label, PatchStatus.FAILED, write_failure(e, install_dir)))
                continue
            if outcome is not None:
                results.append(outcome)
                if outcome.status == PatchStatus.REPAIRED and on_applied:
                    # recorded so a later revert knows we touched it
                    on_applied(outcome)
                continue

        if patch.type in ("binary_patch", "file_install", "nexus", "direct",
                          "archive_inject", "bundled_install"):
            if already_applied_check is not None and already_applied_check(patch.id):
                satisfied.add(patch.id)
                # deliberately does NOT claim "verified on disk". the engine
                # has no idea how the caller decided, and it said that even
                # when the answer came from a stale json record.
                verdict = None
                if patch.type == "binary_patch" and patch.requires_backup \
                        and patch.required_backup_filename:
                    try:
                        verdict = untouched_for_a_patched_exe(
                            resolve_target_path(install_dir, manifest, patch), patch,
                            ours=bool(recorded and recorded(patch.id)), dry_run=dry_run)
                    except Exception as e:
                        verdict = PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                                              write_failure(e, install_dir))
                results.append(verdict or PatchResult(
                    patch.id, patch.label, PatchStatus.ALREADY_APPLIED, "already applied"))
                continue

            if dry_run:
                if patch.type == "binary_patch":
                    results.append(describe_binary_patch(install_dir, manifest, patch))
                elif patch.type == "nexus":
                    results.append(describe_nexus(install_dir, manifest, patch, nexus_source))
                elif patch.type == "bundled_install":
                    old = [SUPERSEDED_LABELS.get(i) or (by_id[i].label if i in by_id else i)
                           for i in patch.replaces]
                    also = f", and take off the older {' and '.join(old)} if it's there" \
                        if old else ""
                    results.append(PatchResult(
                        patch.id, patch.label, PatchStatus.WOULD_APPLY,
                        f"would install {', '.join(f['target'] for f in patch.bundled_files)} "
                        f"into {install_dir / (patch.install_relative_path or '')}{also}"))
                elif patch.type == "archive_inject":
                    targets = ", ".join(Path(i["game_path"]).name for i in patch.injections)
                    results.append(PatchResult(
                        patch.id, patch.label, PatchStatus.WOULD_APPLY,
                        f"would inject {targets} into "
                        f"{'/'.join(patch.archive_variants)} archives"))
                elif patch.type == "direct":
                    results.append(PatchResult(
                        patch.id, patch.label, PatchStatus.WOULD_APPLY,
                        f"would download {patch.download_url} into "
                        f"{install_dir / (patch.install_relative_path or '')}"))
                else:
                    results.append(describe_file_install(install_dir, manifest, patch))
                continue

            try:
                if patch.type == "binary_patch":
                    result = apply_binary_patch(install_dir, manifest, patch)
                elif patch.type == "nexus":
                    result = apply_nexus(install_dir, manifest, patch, nexus_source, fetch=fetch,
                                         on_progress=on_progress, cancel=cancel)
                elif patch.type == "bundled_install":
                    earlier = recorded_digests(
                        recorded(i) for i in (patch.id, *patch.replaces)) if recorded else None
                    before = recorded(patch.id) if recorded else {}
                    result = apply_bundled_install(install_dir, manifest, patch,
                                                   on_progress=on_progress, cancel=cancel,
                                                   earlier=earlier, kept=before.get("kept"))
                elif patch.type == "archive_inject":
                    result = apply_archive_inject(install_dir, manifest, patch, fetch=fetch,
                                                  on_progress=on_progress, cancel=cancel,
                                                  undo_store=undo_store,
                                                  supplied_file=supplied_files.get(patch.id),
                                                  earlier=recorded(patch.id) if recorded
                                                  else None)
                elif patch.type == "direct":
                    result = apply_direct(install_dir, manifest, patch, fetch=fetch,
                                          on_progress=on_progress, cancel=cancel)
                else:
                    result = apply_file_install(install_dir, manifest, patch, fetch=fetch,
                                                on_progress=on_progress, cancel=cancel)
            except Cancelled:
                # a stop is not a per-patch failure. it ends the whole run, and
                # the caller is the one that knows how to report that.
                raise
            except Exception as e:
                # every apply_* catches what it expects to see. this is for
                # what they don't: a locked file, a full disk, a BadZipFile out
                # of an ingest, a KeyError from an ambiguous archive member.
                # OSError alone left the CLI aborting whole runs over any of
                # those, which is the behaviour this exists to prevent.
                result = PatchResult(patch.id, patch.label, PatchStatus.FAILED,
                                     write_failure(e, install_dir))

            if result.status == PatchStatus.APPLIED:
                satisfied.add(patch.id)

            if on_applied is not None and result.status == PatchStatus.APPLIED:
                # the whole result, because a file_install needs to record
                # WHICH files it wrote. a patch id alone cannot be verified
                # against disk afterwards, which is how a deleted d3d9.dll
                # kept reporting as already applied.
                on_applied(result)
            results.append(result)
        elif patch.type == "asset_pack":
            results.append(PatchResult(
                patch.id, patch.label, PatchStatus.SKIPPED,
                "asset pack installs aren't built yet"
            ))
        else:
            results.append(PatchResult(
                patch.id, patch.label, PatchStatus.FAILED,
                f"unknown patch type '{patch.type}'"
            ))

    return results
