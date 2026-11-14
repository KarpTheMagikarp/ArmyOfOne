"""
figures out which folder to actually treat as "the install," whether
that comes from automatic steam detection or a folder the user pointed
at manually. either way it gets validated the same way, by checking the
expected exe is actually there, so a bad manual folder doesn't get
silently trusted just because a user clicked "select folder" on the
wrong thing.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from patcher.manifest import GameManifest
from patcher.paths import persistent_data_root
from patcher.steam_locator import installs_for_game

OVERRIDES_NAME = "install_paths.json"


def _overrides_path() -> Path:
    return persistent_data_root() / OVERRIDES_NAME


def remembered_path(game_id: str) -> Path | None:
    """
    a folder the user pointed us at once, for a game detection keeps missing.

    Worth persisting rather than asking every launch: the reasons detection
    fails (a legacy libraryfolders.vdf, a game copied rather than installed, a
    second Windows account) are all permanent, so without this the same person
    browses to the same folder every single time.
    """
    try:
        stored = json.loads(_overrides_path().read_bytes())
    except (ValueError, OSError):
        return None
    value = stored.get(game_id) if isinstance(stored, dict) else None
    return Path(value) if value else None


def remember_path(game_id: str, install_dir: Path | None) -> None:
    """records an override, or clears it when install_dir is None"""
    path = _overrides_path()
    try:
        stored = json.loads(path.read_bytes())
        if not isinstance(stored, dict):
            stored = {}
    except (ValueError, OSError):
        stored = {}

    if install_dir is None:
        stored.pop(game_id, None)
    else:
        stored[game_id] = str(install_dir)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    except OSError:
        # not being able to remember is a worse experience, not a broken one.
        # the path the user just picked still works for this run.
        pass


@dataclass
class InstallResolution:
    found: bool
    install_dir: Path | None
    source: str | None  # "steam" or "manual"
    error: str | None = None


def validate_install_dir(install_dir: Path, manifest: GameManifest) -> bool:
    """checks that the game's actual exe exists under this folder, not just that the folder exists"""
    return (install_dir / manifest.executable_relative_path).exists()


def resolve_install_dir(
    manifest: GameManifest,
    manual_path: Path | None = None,
    steam_library_paths: list[Path] | None = None,
) -> InstallResolution:
    """
    a manual_path always wins if one's given, that's the user overriding
    auto-detection on purpose. otherwise tries steam_library_paths if
    we have any. either path gets validated against the real exe before
    it's trusted, an install "found" by steam's own manifest but missing
    its actual exe (corrupted install, whatever) doesn't get treated as
    good either.
    """
    if manual_path is not None:
        if validate_install_dir(manual_path, manifest):
            return InstallResolution(found=True, install_dir=manual_path, source="manual")
        return InstallResolution(
            found=False, install_dir=None, source=None,
            error=f"couldn't find {manifest.display_name}'s exe under {manual_path}"
        )

    if steam_library_paths is not None:
        # every library that claims the game, not just the first: Steam leaves
        # the .acf behind when a folder's deleted by hand, and a dead one on C:
        # mustn't shadow the real install on D:. One stat per extra claim.
        for candidate in installs_for_game(
                steam_library_paths, manifest.game_id, app_id=manifest.steam_app_id):
            if validate_install_dir(candidate, manifest):
                return InstallResolution(found=True, install_dir=candidate, source="steam")

    # after steam, not before: an override is for when detection fails, and a
    # game that has since been moved back to a normal library should be found
    # where it actually is rather than where it used to be.
    saved = remembered_path(manifest.game_id)
    if saved is not None and validate_install_dir(saved, manifest):
        return InstallResolution(found=True, install_dir=saved, source="manual")

    # deliberately says nothing about HOW to fix it. this string is shown in
    # the window, printed to a terminal by --doctor, and written to the log,
    # and "click here" is wrong in two of those three.
    return InstallResolution(
        found=False, install_dir=None, source=None,
        error=f"couldn't automatically find {manifest.display_name}"
    )
