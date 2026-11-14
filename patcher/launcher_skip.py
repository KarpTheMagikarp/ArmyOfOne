"""
Turning off Square Enix's launcher on the user's behalf.

XIII and XIII-2 start behind FFXiiiLauncher.exe, and its "Do not show this
window again" box just writes confirm_mode=true into setup.xml in the game
root. So we write it. Text in, text out, since the file holds the user's own
settings and an ElementTree round trip would reformat every line.

follow() decides when. Steam's Play goes through the same launcher, so it stays
hidden whenever our NovaFix is on and owns the graphics, otherwise there are
two places to set resolution and the one in front of you loses. That leaves the
launcher's voice setting nowhere to change, so set_voice writes it for the
Settings drawer (see voices.py).
"""

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from patcher.fileops import write_atomic

SETUP_NAME = "setup.xml"

# the element, whatever whitespace or casing it turns up with. the file is
# written by the launcher so it should be consistent, but it is also the file
# every "how do I get my launcher back" answer on the internet tells people to
# edit by hand, and those edits arrive however they arrive.
FLAG = re.compile(rb"(<\s*confirm_mode\s*>)(\s*[^<]*?\s*)(<\s*/\s*confirm_mode\s*>)", re.I)


@dataclass(frozen=True)
class Result:
    changed: bool
    why: str
    # the file couldn't be read or written, as opposed to there being nothing
    # to do. A missing one is the latter: the launcher hasn't made it yet
    failed: bool = False


def setup_path(install_dir) -> Path:
    return Path(install_dir) / SETUP_NAME


VOICE = re.compile(rb"(<\s*voice_mode\s*>)(\s*[^<]*?\s*)(<\s*/\s*voice_mode\s*>)", re.I)


def voice_mode(install_dir) -> int | None:
    """
    the launcher's voice setting: 0 Japanese, 1 English, None if unknown.

    It decides which language archives the game loads, so anything writing
    into those needs to ask it rather than assume English.
    """
    try:
        found = VOICE.search(setup_path(install_dir).read_bytes())
    except OSError:
        return None
    value = found.group(2).strip() if found else b""
    return int(value) if value.isdigit() else None


def set_skip(install_dir, skip: bool = True) -> Result:
    """
    flip confirm_mode and touch nothing else. A missing file means the
    launcher has never run, and we don't invent one: it stores resolution
    and shadows as undocumented enum indices, and a wrong guess is a 720p
    window. It appears once, then this works.
    """
    return _rewrite(install_dir, FLAG, "confirm_mode", b"true" if skip else b"false",
                    "launcher skipped" if skip else "launcher restored")


def set_voice(install_dir, mode: int) -> Result:
    """
    voice_mode, the one thing in the launcher's window NovaFix doesn't take
    over, by set_skip's rules: that element and nothing else, and no file made
    up when there isn't one
    """
    # under follow()'s lock: both rewrite the whole file, and a game switch's
    # check landing mid-save would write the old voice back
    with _FOLLOWING:
        return _rewrite(install_dir, VOICE, "voice_mode", str(int(mode)).encode("ascii"),
                        f"voice_mode set to {int(mode)}")


def _rewrite(install_dir, element, name: str, want: bytes, done: str) -> Result:
    path = setup_path(install_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Result(False, "no setup.xml yet, the launcher has not run")
    except OSError as e:
        return Result(False, f"could not read setup.xml: {e}", failed=True)

    match = element.search(raw)
    if match is None:
        # a shape we have not seen. inserting the element ourselves is a guess
        # about a parser we cannot test against.
        return Result(False, f"no {name} in setup.xml")

    if match.group(2).strip().lower() == want:
        return Result(False, "already set")

    updated = raw[:match.start(2)] + want + raw[match.end(2):]
    try:
        write_atomic(path, updated)
    except OSError as e:
        return Result(False, f"could not write setup.xml: {e}", failed=True)
    return Result(True, done)


# in our undo store rather than the state file: the page asks on every game
# switch, and a state write there could land in the middle of an Apply writing
# the same file. Written only by a hide that flipped the flag, so a box the user
# ticked themselves is never undone
HIDDEN = "launcher-hidden"
# a hidden launcher that came back on screen, which is the user's doing,
# usually to reach the voice setting. Nothing hides it again and Revert leaves
# it, so a box they tick after that stays theirs. A Graphics Source pick or a
# Revert starts over
RELEASED = "launcher-released"
# the scan's thread, Launch and a drawer save can all land at once, and an
# interleaved read and write puts back a flag the other one just changed
_FOLLOWING = threading.Lock()


def flag(install_dir) -> str | None:
    """confirm_mode as "true" or "false", "missing" with no setup.xml, else None"""
    try:
        match = FLAG.search(setup_path(install_dir).read_bytes())
    except FileNotFoundError:
        return "missing"
    except OSError:
        return None
    return match.group(2).strip().lower().decode("ascii", "replace") if match else None


def novafix_kind(install_dir, proxy) -> str:
    """
    "ours" for the build we ship, "other" for any NovaFix that isn't (a newer
    pin after an update, or one dropped in by hand, and either overrides the
    launcher just the same), "" for nothing that reads the key at all.
    """
    from patcher.engine import install_target_dir, installed_on_disk

    if installed_on_disk(install_dir, proxy):
        return "ours"
    key = "OverrideLauncherGraphics".encode("utf-16-le")
    for spec in proxy.bundled_files or ():
        try:
            if key in (install_target_dir(install_dir, proxy) / spec["target"]).read_bytes():
                return "other"
        except OSError:
            continue
    return ""


def override(path) -> str | None:
    """
    [Graphics] OverrideLauncherGraphics as NovaFix's GetPrivateProfileString
    hands it over: section and key in any case, the first of each, one pair of
    quotes off. None when it isn't there.
    """
    section = None
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("[") and "]" in line:
            if section == "graphics":
                return None
            section = line[1:line.index("]")].strip().lower()
        elif section == "graphics" and "=" in line and not line.startswith(";"):
            key, _, value = line.partition("=")
            if key.strip().lower() == "overridelaunchergraphics":
                value = value.strip()
                if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value
    return None


def owns_graphics(install_dir, proxy) -> bool:
    """
    whether the graphics have been handed to Square Enix's launcher.

    NovaFix's ReadBoolean: Off, or a number no bigger than 1 that's 0. Anything
    else, absent included, is its default of overriding the launcher. Reading
    it any other way hides the window while nothing overrides it.
    """
    from patcher.novafix_config import config_path

    path = config_path(install_dir, proxy.install_relative_path or "")
    if not path.is_file():
        return False
    value = (override(path) or "").strip()
    if value.lower() in ("off", "on"):
        return value.lower() == "off"
    return re.fullmatch(r"[0-9]+", value) is not None and int(value) == 0


def follow(install_dir, manifest, chosen: bool = False, may_hide: bool = True) -> Result:
    """
    the launcher hidden once our NovaFix is on and owns the graphics, and
    shown again when that stops being true, if hiding it was our doing.

    `chosen` is a Graphics Source just picked in the drawer, which beats
    everything: The Game's Launcher shows it (that choice needs the window),
    the patcher hides it. `may_hide` is off for Revert, which only puts things
    back. A never-started game has no setup.xml yet, so its launcher shows
    once and the next follow() catches it.
    """
    from patcher.launch import shows_launcher

    if not shows_launcher(getattr(manifest, "steam_app_id", None)):
        return Result(False, "this game has no launcher")
    with _FOLLOWING:
        return _follow(install_dir, manifest, chosen, may_hide)


def _follow(install_dir, manifest, chosen: bool, may_hide: bool) -> Result:
    from patcher.paths import undo_store_for

    store = undo_store_for(install_dir)
    hidden, released = store / HIDDEN, store / RELEASED
    proxy = next((p for p in manifest.patches if p.id == "install_novafix"), None)
    novafix = novafix_kind(install_dir, proxy) if proxy is not None else ""
    if novafix or chosen:
        try:
            owns = proxy is not None and owns_graphics(install_dir, proxy)
        except OSError as e:
            return Result(False, f"couldn't read NovaFix.ini, so it's left as it is ({e})")
    else:
        owns = False

    if hidden.is_file() and flag(install_dir) in ("false", "missing"):
        _mark(released, b"the patcher hid Square Enix's launcher and it was brought back, so "
                        b"it stays\n")
        _unmark(hidden)
    if chosen or not may_hide:
        _unmark(released)

    if novafix and not owns:
        if not may_hide:
            # Revert with our NovaFix still on, its own revert having failed:
            # hidden is still right, and the marker waits for the next one
            if novafix == "ours":
                return Result(False, "left as it is")
        elif chosen or (novafix == "ours" and not hidden.is_file() and not released.is_file()):
            result = set_skip(install_dir, True)
            if result.changed:
                _mark(hidden, b"the patcher hid Square Enix's launcher. Revert shows it again\n")
            return result
        else:
            # hidden once already, brought back by the user, or a NovaFix we
            # don't vouch for, which overrides just the same: none of those is
            # a reason to touch the window
            return Result(False, "left as it is")
    if not (owns and chosen) and not hidden.is_file():
        return Result(False, "left as it was")
    result = set_skip(install_dir, False)
    if not result.failed:
        _unmark(hidden)
    return result


def _mark(path, text: bytes) -> None:
    # best effort: without it a later Revert leaves the launcher as it is,
    # which the user can live with
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, text)
    except OSError:
        pass


def _unmark(path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # the next follow() tries again
        pass
