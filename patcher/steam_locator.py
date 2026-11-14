"""
finds where a Steam game is installed: libraryfolders.vdf lists the libraries,
each library's appmanifest_<appid>.acf says what's there and under what folder.
Parsing is pure text in, dict out; the registry lookup is the one OS-coupled
bit, kept small.
"""

import os
import re
import sys
from pathlib import Path

# fallback only. a manifest can carry its own steam_app_id, which is what
# makes adding a game a json drop instead of a code edit.
GAME_APP_IDS = {
    "ffxiii": "292120",
    "ffxiii2": "292140",
    "ffxiii3": "345350",
}

_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def _unescape(s: str) -> str:
    return s.replace('\\\\', '\\').replace('\\"', '"')


def parse_vdf(text: str) -> dict:
    """
    parses valve's KeyValues (vdf) format into nested dicts. handles
    quoted keys/values and nested blocks, which is all steam's
    libraryfolders.vdf and appmanifest .acf files actually use.
    """
    tokens = []
    for m in _TOKEN_RE.finditer(text):
        if m.group(1) is not None:
            tokens.append(_unescape(m.group(1)))
        else:
            tokens.append(m.group(2))

    pos = 0

    def parse_block():
        nonlocal pos
        result = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == '}':
                pos += 1
                return result
            key = tok
            pos += 1
            if pos < len(tokens) and tokens[pos] == '{':
                pos += 1
                result[key] = parse_block()
            else:
                result[key] = tokens[pos]
                pos += 1
        return result

    return parse_block()


def library_paths_from_vdf(vdf_text: str) -> list[str]:
    """
    pulls the library paths out of a parsed libraryfolders.vdf. Reads both the
    current nested shape and the old flat one (index -> path), which anyone
    who hasn't let Steam rewrite the file still has. Root key is matched case
    insensitively since the legacy file says "LibraryFolders".
    """
    try:
        data = parse_vdf(vdf_text)
    except (IndexError, KeyError, ValueError):
        # a truncated or half-written vdf is a real thing to find on disk, and
        # this runs on every game switch in the UI. one bad file must cost the
        # detection, not the app.
        return []

    folders = next((v for k, v in data.items() if k.lower() == "libraryfolders"), {})
    if not isinstance(folders, dict):
        # a value where a block was expected, which is what a truncated file
        # looks like to the tokeniser
        return []

    paths = []
    for key, value in folders.items():
        if isinstance(value, dict):
            if "path" in value:
                paths.append(value["path"])
        elif key.isdigit() and value:
            # legacy flat form. the keys that are not indices are counters
            # like TimeNextStatsReport, hence the isdigit guard.
            paths.append(value)
    return paths


def installdir_from_acf(acf_text: str) -> str | None:
    """
    pulls installdir out of a parsed appmanifest .acf. Guarded like
    library_paths_from_vdf, since a half written .acf is real and parse_vdf
    raises IndexError at 34 of a manifest's 71 truncation points, which used
    to take --doctor down with it.
    """
    try:
        data = parse_vdf(acf_text)
    except (IndexError, KeyError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    state = data.get("AppState")
    if not isinstance(state, dict):
        return None
    name = state.get("installdir")
    return name if isinstance(name, str) and name else None


def safe_installdir(name: str) -> str | None:
    """
    the installdir if it's a plain folder name we can join onto a library. An
    absolute one makes pathlib drop the library, and ..\\.. walks out of it.
    Corruption, not an attack, but it's two comparisons.
    """
    if not name or ":" in name:
        return None
    if name.startswith(("\\", "/")):
        return None
    parts = name.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return name


def game_install_candidates(library_paths: list[Path], app_id: str):
    """
    every library that claims this app, in order. A generator since an .acf
    isn't proof: Steam leaves it behind when a folder's deleted by hand, so a
    stale one on C: would shadow the real install on D:. The caller validates
    each.
    """
    for library in library_paths:
        manifest_path = library / "steamapps" / f"appmanifest_{app_id}.acf"
        try:
            if not manifest_path.exists():
                continue
            text = manifest_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # a library on a drive that has gone away since the vdf was written
            continue
        installdir = installdir_from_acf(text)
        if installdir is None:
            continue
        safe = safe_installdir(installdir)
        if safe is None:
            continue
        yield library / "steamapps" / "common" / safe


def find_game_install(library_paths: list[Path], app_id: str) -> Path | None:
    """the first library claiming this app, or None. see game_install_candidates"""
    return next(game_install_candidates(library_paths, app_id), None)


def find_install_for_game(
    library_paths: list[Path],
    game_id: str,
    app_id: str | None = None,
) -> Path | None:
    """
    same as find_game_install but takes our own game_id instead of a raw
    steam app id. pass app_id straight from the manifest when it has one,
    the GAME_APP_IDS table is just the fallback for older manifests.
    """
    app_id = app_id or GAME_APP_IDS.get(game_id)
    if app_id is None:
        raise ValueError(f"no known steam app id for game_id {game_id!r}")
    return find_game_install(library_paths, app_id)


def installs_for_game(
    library_paths: list[Path],
    game_id: str,
    app_id: str | None = None,
):
    """every library claiming this game, in order. see game_install_candidates"""
    app_id = app_id or GAME_APP_IDS.get(game_id)
    if app_id is None:
        raise ValueError(f"no known steam app id for game_id {game_id!r}")
    return game_install_candidates(library_paths, app_id)


def steam_roots() -> list[Path]:
    """
    every place Steam might be, best guess first: HKCU, HKLM, the usual paths.
    HKLM needs KEY_WOW64_32KEY since 32-bit Steam writes under WOW6432Node and
    a 64-bit python gets redirected away from it. HKCU\\Software\\Valve isn't
    redirected.
    """
    if sys.platform != "win32":
        return []

    import winreg

    found: list[Path] = []

    def remember(value) -> None:
        if not value:
            return
        path = Path(str(value))
        if path not in found:
            found.append(path)

    lookups = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath", 0),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "InstallPath", 0),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath",
         winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath", 0),
    ]
    for hive, subkey, value_name, flag in lookups:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | flag) as key:
                remember(winreg.QueryValueEx(key, value_name)[0])
        except OSError:
            continue

    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(variable)
        if base:
            remember(Path(base) / "Steam")
    remember(Path(r"C:\Program Files (x86)\Steam"))
    return found


def default_steam_library_vdf_path() -> Path | None:
    """
    finds the default libraryfolders.vdf on this machine. windows-only,
    these games don't run anywhere else. returns None if steam can't be
    found so callers can fall back to asking the user to browse for it
    manually.
    """
    for root in steam_roots():
        # steamapps is where Steam writes it now. config/ is where older
        # clients put it, and an install that has not been through a recent
        # update still has it there and nowhere else.
        for relative in (("steamapps", "libraryfolders.vdf"),
                         ("config", "libraryfolders.vdf")):
            candidate = root.joinpath(*relative)
            if candidate.exists():
                return candidate
    return None
