"""
the orchestration behind both the CLI and the window: find the install,
check what's applied, run the patches, record what happened. No UI in here,
so it tests without one.
"""

from dataclasses import dataclass
from pathlib import Path

from patcher.engine import PatchResult, run_patches, supersede_handler
from patcher.install_resolver import InstallResolution, resolve_install_dir
from patcher.manifest import GameManifest
from patcher.state import applied_recorder, record_applied, record_reader, scan_patch_status
from patcher.steam_locator import (default_steam_library_vdf_path,
                                   library_paths_from_vdf, steam_roots)
from patcher.updater import default_fetch


@dataclass
class AutomatedPatchOutcome:
    resolution: InstallResolution
    results: list[PatchResult] | None = None


def detect_steam_library_paths() -> list[Path]:
    """
    every Steam library on this machine, [] if Steam can't be found. Steam's
    own folder always counts: the legacy flat vdf only lists the extra
    libraries, and a missing or mangled vdf lists nothing.
    """
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        # Steam writes the vdf with its own root in it on the current format,
        # so the roots would otherwise be duplicated. casefold rather than
        # resolve: these are Windows paths being deduplicated on a machine that
        # may not have them, and resolve() would reach the filesystem.
        key = str(path).replace("/", "\\").rstrip("\\").casefold()
        if key and key not in seen:
            seen.add(key)
            found.append(path)

    vdf_path = default_steam_library_vdf_path()
    if vdf_path is not None:
        try:
            raw = vdf_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        for entry in library_paths_from_vdf(raw):
            if entry.strip():
                add(Path(entry))

    for root in steam_roots():
        if (root / "steamapps").is_dir():
            add(root)
    return found


def run_full_automated_patch(
    manifest: GameManifest,
    state_path: Path,
    manual_path: Path | None = None,
    steam_library_paths: list[Path] | None = None,
    fetch=default_fetch,
    dry_run: bool = False,
    enabled_patch_ids: set[str] | None = None,
    nexus_source=None,
) -> AutomatedPatchOutcome:
    """
    the zero-config path: runs every default_enabled patch, skips whatever's
    already applied (checked against the files) and records what's new.

    steam_library_paths and fetch are injectable so tests need no Steam and
    no network. enabled_patch_ids replaces the default_enabled pick, which is
    what the UI passes once boxes are ticked. dry_run resolves the install
    and checks what's applied (both read only) but never writes, backs up or
    downloads.
    """
    if steam_library_paths is None:
        steam_library_paths = detect_steam_library_paths()

    resolution = resolve_install_dir(manifest, manual_path=manual_path, steam_library_paths=steam_library_paths)
    if not resolution.found:
        return AutomatedPatchOutcome(resolution=resolution)

    # The same pre-flight the UI does, which the CLI never had. Without it a
    # library under Program Files produced fourteen rows each explaining the
    # same permission problem in its own wording, and nothing anywhere said
    # "this folder needs administrator rights". A dry run is read-only, so it
    # is exempt: refusing to SHOW somebody what would happen because the folder
    # is locked is the wrong answer.
    if not dry_run:
        from patcher.access import writable_or_reason

        blocked = writable_or_reason(resolution.install_dir)
        if blocked:
            return AutomatedPatchOutcome(
                resolution=InstallResolution(
                    found=False, install_dir=resolution.install_dir,
                    source=resolution.source, error=blocked))

    if enabled_patch_ids is None:
        enabled_ids = {p.id for p in manifest.patches if p.default_enabled}
    else:
        enabled_ids = set(enabled_patch_ids)
    scan = scan_patch_status(resolution.install_dir, manifest, state_path)

    results = run_patches(
        resolution.install_dir, manifest, enabled_ids,
        already_applied_check=lambda pid: scan.get(pid, False),
        on_applied=applied_recorder(resolution.install_dir, manifest, state_path),
        on_supersede=supersede_handler(resolution.install_dir, manifest, state_path),
        recorded=record_reader(state_path),
        fetch=fetch,
        dry_run=dry_run,
        nexus_source=nexus_source,
    )
    if not dry_run:
        # the window's Apply does the same, so Steam's Play skips Square Enix's
        # launcher whichever of the two patched the game
        from patcher import launcher_skip

        try:
            launcher_skip.follow(resolution.install_dir, manifest)
        except Exception:
            # one flag in a game that plays either way
            pass

    return AutomatedPatchOutcome(resolution=resolution, results=results)
