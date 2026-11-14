"""
can we actually write here, and what to tell the user when we can't.

Writes land in the Steam install (often Program Files, owned by
TrustedInstaller) or %LOCALAPPDATA%. The advice matters as much as the check,
since "run as administrator" is useless to someone who already is.
"""

import ctypes
import os
import stat
import sys
import uuid
from pathlib import Path


def is_elevated() -> bool | None:
    """
    True if this process can write to Program Files, None if we can't tell.

    None is not a failure: on anything but Windows there is no such thing as
    a UAC token, and a caller that treats None as False starts telling Linux
    users to right-click Run as administrator.
    """
    if sys.platform != "win32":
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return None


def clear_readonly(path: Path) -> bool:
    """
    Strips the read-only attribute so a replace() over the top can land.

    Steam sets this on nothing, but extractors do: a mod zipped from a CD rip,
    or anything unpacked by a tool that preserved the archive's own flags,
    arrives read-only and then the second run of a patch fails where the first
    succeeded. Returns whether it changed anything, so callers can put it back.
    """
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    if mode & stat.S_IWRITE:
        return False
    try:
        path.chmod(mode | stat.S_IWRITE)
        return True
    except OSError:
        return False


def probe_writable(directory: Path) -> str | None:
    """
    None if we can create a file in here, otherwise why not.

    os.access on Windows only reports the read-only attribute, so it says yes
    to a folder whose ACL denies us and knows nothing of VirtualStore
    redirection. Trying is the only honest answer. Probes the nearest existing
    ancestor instead of mkdir'ing, so a moved game folder isn't recreated
    empty and called writable.
    """
    probe_dir = Path(directory)
    while not probe_dir.is_dir() and probe_dir.parent != probe_dir:
        probe_dir = probe_dir.parent
    if not probe_dir.is_dir():
        return f"{directory} does not exist"

    probe = probe_dir / f".patcher-write-test-{uuid.uuid4().hex[:8]}"
    try:
        probe.write_bytes(b"")
    except OSError as e:
        return _explain(e, directory)
    finally:
        try:
            probe.unlink()
        except OSError:
            # left behind is untidy, not broken. the write itself is what we
            # came to find out and we already know the answer.
            pass
    return None


def _explain(error: OSError, path: Path) -> str:
    return write_failure(error, path)


def long_path_safe(path: Path | str) -> bool:
    """
    whether this path fits in the classic 260 character limit. Used to explain
    a failed write, since ERROR_PATH_NOT_FOUND on a valid folder reads as the
    folder not existing. Only matters on Windows without the long path opt-in.
    """
    if sys.platform != "win32":
        return True
    text = str(path)
    if text.startswith("\\\\?\\"):
        return True
    return len(os.fspath(text)) < 260


# ERROR_VIRUS_INFECTED and ERROR_VIRUS_DELETED: Windows Security, or the
# antivirus plugged into it, refusing a file it calls a threat
THREAT_ERRORS = (225, 226)


def is_threat_block(error) -> bool:
    return getattr(error, "winerror", None) in THREAT_ERRORS


def threat_message(name: str, mod: str = "") -> str:
    """
    the sentence for a file Windows won't hand over. Not "isn't a Windows
    binary" or "couldn't write": the file is fine as far as anyone can tell,
    and the one thing that changes the outcome is the user's call on whether
    the mod is trustworthy
    """
    return (f"Windows Security blocked {name} as a threat, so it couldn't be read or written. "
            "Antivirus flags a lot of game mods that hook in through a .dll. If you trust where "
            f"{mod or 'it'} came from, allow it under Windows Security, Protection history, and "
            "drop it again.")


def write_failure(error: Exception, fallback: Path | str) -> str:
    """
    One OSError, one sentence a player can act on.

    Raw errno text is the wrong thing to show: "[Errno 13] Permission denied"
    tells someone nothing about closing Steam, and "[Errno 28]" tells them
    nothing about their disk. The filename on the exception is better than the
    fallback when it's there, because it names the file rather than the folder.
    """
    where = getattr(error, "filename", None) or fallback
    if is_threat_block(error):
        return threat_message(Path(str(where)).name)
    if isinstance(error, PermissionError):
        return permission_message(where)
    errno = getattr(error, "errno", None)
    if errno == 28:
        return f"the drive holding {where} is full"

    too_long = errno == 36 or getattr(error, "winerror", None) in (3, 206)
    # windows reports a path over 260 characters as ERROR_PATH_NOT_FOUND,
    # which reads as "that folder does not exist" about a folder the user is
    # looking at. worth saying out loud, but only when the path really is long:
    # a genuine missing folder returns the same winerror.
    if too_long and not long_path_safe(where):
        return (f"the path to {where} is {len(str(where))} characters, past the "
                "260 Windows allows by default. Move the game somewhere shorter, "
                "or turn on Win32 long paths in Windows settings")
    if errno == 36:
        return f"the name of {where} is too long for this filesystem"
    if isinstance(error, OSError):
        return f"couldn't write to {where}: {error.strerror or error}"
    return f"stopped on an unexpected error: {error}"


def permission_message(path: Path | str, action: str = "write to") -> str:
    """
    One sentence, and it changes depending on whether elevation is even the
    problem. Told to an admin, "run as administrator" reads as the app not
    knowing what it's doing, and the actual cause is almost always a running
    game holding a file open or an antivirus lock.
    """
    where = str(path)
    elevated = is_elevated()
    if elevated:
        return (f"Windows denied permission to {action} {where}, even as administrator. "
                "Close the game and Steam if they're running, then try again. "
                "Antivirus can also lock files mid-write, and if Windows Security "
                "said it blocked an unauthorized change, allow ArmyOfOne under "
                "Ransomware protection, Controlled folder access.")
    if elevated is False:
        # Windows won't let Explorer drop into an elevated window, so say it
        # now rather than leave the drop looking broken later
        return (f"no permission to {action} {where}. That folder needs administrator "
                "rights. Close the app, right-click it and pick Run as administrator. "
                "Dragging files onto it won't work that way, so use Choose file instead.")
    return f"no permission to {action} {where}."


def writable_or_reason(install_dir: Path) -> str | None:
    """
    The pre-flight for a whole run. Checks the install root rather than every
    file we might touch, because on Windows the ACL that matters is inherited
    from the folder and a per-file check costs one probe per patch for the
    same answer.
    """
    return probe_writable(Path(install_dir))


def make_console_unicode_safe() -> None:
    """
    stop a non-ASCII path from killing a run through print(). A redirected
    console makes stdout an ANSI codepage TextIOWrapper, where the first
    Cyrillic path raises; backslashreplace makes it ugly instead of fatal. A
    windowed build has no stdout, hence the None checks.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):
            pass


def silence_device_errors() -> None:
    """
    stops Windows popping a modal out of an ordinary stat call.

    Scanning Steam libraries touches every drive letter the vdf mentions, and
    an empty card reader answers with the kernel's "There is no disk in the
    drive" box, which no try/except reaches. SEM_FAILCRITICALERRORS turns that
    into a plain error. Set once at startup, and only that bit, since hiding
    crash dialogs would hide real failures.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        SEM_FAILCRITICALERRORS = 0x0001
        kernel32 = ctypes.windll.kernel32
        # keep whatever else is set rather than replacing the mode outright
        previous = kernel32.SetErrorMode(SEM_FAILCRITICALERRORS)
        kernel32.SetErrorMode(previous | SEM_FAILCRITICALERRORS)
    except Exception:
        pass


ZONE_MARK = ":Zone.Identifier"

BLOCKED_MESSAGE = (
    "Windows blocked some of this app's files because the zip came from the "
    "internet, and they couldn't be unblocked from here.\n\n"
    "Right-click the zip you downloaded, pick Properties, tick Unblock at the "
    "bottom, press OK, then extract it again.")


def unblock(root: Path) -> list[Path]:
    """
    takes the downloaded-from-the-internet mark off the DLLs under root and
    returns the ones still carrying it. Explorer copies a zip's Zone.Identifier
    onto everything it extracts and .NET won't load a marked assembly, so the
    window died with 0x80131515. Only DLLs, only under our own bundle.
    """
    still = []
    for folder, _, names in os.walk(root):
        for name in names:
            if not name.lower().endswith(".dll"):
                continue
            path = Path(folder) / name
            try:
                os.remove(str(path) + ZONE_MARK)
            except FileNotFoundError:
                continue
            except OSError:
                # extracted somewhere this user can't modify. Nothing more to
                # try from in here, but the caller can say what fixes it
                pass
            if os.path.exists(str(path) + ZONE_MARK):
                still.append(path)
    return still


_borrowed = False


def attach_parent_console() -> bool:
    """
    output for a command typed into a terminal, from the windowed build. The
    release exe has no console, so `ArmyOfOne.exe ffxiii --revert` printed
    nothing. Borrows the parent terminal; from Explorer there isn't one and
    notify() reaches the user instead.
    """
    global _borrowed
    if sys.platform != "win32" or sys.stdout is not None:
        return False
    try:
        ATTACH_PARENT_PROCESS = -1
        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False
        console = open("CONOUT$", "w", encoding="utf-8", errors="backslashreplace",
                       buffering=1)
    except Exception:
        return False
    sys.stdout = sys.stderr = console
    _borrowed = True
    return True


def release_console(note: str = "") -> None:
    """
    the borrowed terminal handed back, before work that must not be cut short.

    Attached, this process gets that terminal's Ctrl+C and dies with its
    window, and cmd has already handed its prompt back, so either is easy to
    do halfway through a restore. After this the result arrives through
    notify()'s message box.
    """
    global _borrowed
    if not _borrowed:
        return
    if note:
        print(note)
    sys.stdout = sys.stderr = None
    try:
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass
    _borrowed = False
