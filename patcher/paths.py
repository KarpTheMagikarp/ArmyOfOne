"""
where things live, frozen or not. main.py and shell.py each had their own
copy of this and they have to agree: shell.py serves the UI out of the
bundled root while main.py writes state next to the exe, and if those two
ever drift the app reads a manifest from one place and records having
patched it in another.
"""

import os
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def bundled_data_root() -> Path:
    """
    read-only payload: manifests/, ui/, assets/. a normal python run finds it
    at the repo root. a frozen build finds it at sys._MEIPASS, which for a
    onedir build is the _internal folder, NOT the folder holding the exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return _REPO_ROOT


# the name from before ArmyOfOne, kept on purpose: every install's records,
# backups and the Nexus key live under it, and a new folder would forget them
APP_DIR_NAME = "FFXIIITrilogyPatcher"


def _beside_app() -> Path:
    """where this used to live: next to the exe, or the repo in a dev run"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def persistent_data_root() -> Path:
    """
    writable data that has to outlive the process: state, log, nexus key,
    downloads. onefile extracts to a temp dir that gets deleted on exit, so
    none of this can live under bundled_data_root() or it silently vanishes
    between runs.

    Per user rather than per copy of the app. Keeping it beside the exe meant
    every fresh download or re-extract started with no memory of what it had
    installed, which is not cosmetic: the FF13Fix purge could not find its own
    records to clean up, and a prerequisite sitting plainly on disk still read
    as missing.

    A portable.txt next to the exe opts back into the old spot, for anyone
    running this off a USB stick who wants it self-contained.
    """
    beside = _beside_app()
    if (beside / "portable.txt").is_file():
        return beside

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")

    # no sensible per-user location, so stay where we were rather than guess
    return Path(base) / APP_DIR_NAME if base else beside


_migrated = False


def migrate_legacy_data() -> list[str]:
    """
    brings over data an older build left beside the exe.

    Copies rather than moves, so going back to an older build still works, and
    never overwrites anything already in the new location. Returns what it
    brought across, for the log.
    """
    global _migrated
    if _migrated:
        return []
    _migrated = True

    old, new = _beside_app(), persistent_data_root()
    try:
        if old.resolve() == new.resolve():
            return []
    except OSError:
        return []

    brought = []
    for relative in (".state", "nexus_config.json"):
        source, target = old / relative, new / relative
        if not source.exists() or target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            brought.append(relative)
        except OSError:
            # a read-only Downloads folder or a locked file is not worth
            # failing a run over. the cost is a forgotten record, not a crash.
            pass
    return brought


def discover_manifests() -> dict[str, Path]:
    """game_id -> manifest path. dropping a new json in is the whole install."""
    manifest_dir = bundled_data_root() / "manifests"
    if not manifest_dir.is_dir():
        return {}
    return {p.stem: p for p in sorted(manifest_dir.glob("*.json"))}


def state_path_for(game_id: str) -> Path:
    return persistent_data_root() / ".state" / f"{game_id}.json"


def log_path() -> Path:
    return persistent_data_root() / "patcher.log"


def run_log_path(name: str) -> Path:
    """
    a full transcript of one tool run, which the terminal pane cannot be.

    The pane drops its oldest lines past a couple of thousand to stay
    responsive, and an extraction emits five figures of them, so by the time
    something fails the beginning is long gone. This keeps the lot, including
    the commands and both streams, in one file somebody can send on.
    """
    return persistent_data_root() / "runs" / f"{name}.log"


def nexus_config_path() -> Path:
    return persistent_data_root() / "nexus_config.json"


def undo_store_for(install_dir: Path) -> Path:
    """
    where an archive injection keeps the bytes it needs to put back, and the
    only place index backups live. One definition for Apply and drops alike,
    or which copy counted as vanilla depends on how a mod went in. Beside the
    install, not in %LOCALAPPDATA%, since a journal only means anything
    against that exact archive and should go when the game does.
    """
    return Path(install_dir) / ".patcher-undo"
