"""
Finding out whether the 4GB patch actually worked, by watching the game start.

Whether the cover beats XIII's CEG check on a given machine can't be read off
the files, so the game gets launched and the next twenty seconds answer it. A
refusal takes the patch back off and records it so nothing puts it back. The
decision is pure and tested; the watching is Win32 and isn't.
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass, field

OK = "ok"
DRM_ERROR = "drm_error"
UNKNOWN = "unknown"
# a fast death Windows logged as a crash in a dll of its own, which no DRM
# check on the exe makes: src_38's launch check blamed the 4GB patch for one
# of these on LR, and the game still wouldn't start with the patch off
CRASHED = "crashed"

# How long to keep looking before giving up on an answer. Steam's own startup,
# the cloud sync and Square Enix's launcher all sit in front of the game, so
# this is generous: the cost of waiting is a background thread, the cost of
# being impatient is calling a slow machine a failure.
WATCH_SECONDS = 45.0

# Fast enough to see a process that is created and then killed by the loader.
# A statically imported DLL that will not load is exactly that: the process
# exists for the moment it takes the loader to give up, and at half a second a
# poll can step straight over it and call the whole thing UNKNOWN.
POLL_SECONDS = 0.2

# A DRM refusal is immediate: the wrapper checks itself and gives up before the
# game draws anything. A process that lived longer than this and then exited was
# somebody quitting, which is not our business.
DIED_FAST_SECONDS = 12.0

# Gone this long before it counts as gone. A game that hands itself back to
# Steam to be started again is two processes a moment apart, not a refusal.
GONE_SECONDS = 3.0

# How long Windows Error Reporting can take to log a crash after the process
# has gone. It writes the event from WerFault, a beat behind the death.
CRASH_LOG_SECONDS = 8.0

# The window Steam puts up when its wrapper refuses.
ERROR_WINDOW_TITLE = "Steam Error"

# and the ones Windows shows when a process can't start at all, like the loader
# refusing a DLL beside the game. All shaped "<exe> - <something>", so matched
# by shape against the exe we launched instead of a list of English strings.
LOADER_ERROR_SUFFIXES = (" - System Error", " - Application Error",
                         " - Bad Image", " - Entry Point Not Found",
                         " - DLL Initialization Failed")


@dataclass
class Seen:
    """what the watch saw, with what Windows had to say about the death"""
    verdict: str
    ran: float = 0.0
    # the exit code, when it ended and its handle was got in time
    code: int | None = None
    # the Application Error Windows logged: module, path, exception
    crash: dict = field(default_factory=dict)
    # Steam's error box, or the loader's
    window: bool = False


# what an exit code says about a game that never got going, for the codes a
# start-up failure leaves. Anything else is said as the number
EXIT_SAID = {
    0xC0000005: "it crashed",
    0xC0000135: "a dll it loads at start is missing",
    0xC000007B: "a dll beside it isn't one Windows can load into a 32-bit game",
    0xC0000139: "a dll beside it is missing something the game asks it for",
    0xC0000142: "a dll failed as it started up",
    0xC0000409: "it stopped itself over a problem it found",
    0xC0000428: "Windows turned away a dll it couldn't verify",
    0xC0000906: "antivirus blocked a file it loads",
    0xC0E90002: "Windows' app control turned away a file it loads",
}


def evidence(seen: Seen) -> dict:
    """what a refusal was filed on, kept with it so a later look can weigh it"""
    out = {"ran": round(seen.ran, 1), "window": seen.window}
    if seen.code is not None:
        out["code"] = f"{seen.code:#010x}"
    if seen.crash:
        out["crash"] = dict(seen.crash)
    return out


def said(game: str, seen: Seen, owner: str = "", drawer: bool = False) -> str:
    """
    a launch that died fast, in a sentence or two. `owner` is whose the dll
    it crashed in is, and `drawer` whether that's a mod the drawer put in,
    which is the one thing here a player can take off and try again without
    """
    when = "as it started" if seen.ran < 3 else f"{seen.ran:.0f} seconds after it started"
    module = str(seen.crash.get("module") or "")
    if seen.window:
        return f"{game} wouldn't start: Windows or Steam put up an error box {when}."
    if module:
        stem = module.rsplit(".", 1)[0].lower()
        whose = f", which is {owner}'s" if owner and owner.lower() != stem else ""
        line = f"{game} closed {when}. Windows logged a crash in {module}{whose}."
        if drawer and owner:
            line += (f" Take {owner} off under Installed in the Mods drawer and launch again "
                     "to see if it's the one.")
        elif module.lower().endswith(".exe"):
            line += " That's the game's own code rather than a mod's dll."
        return line
    if seen.code == 0:
        return f"{game} closed itself {when}, without an error."
    if seen.code is not None:
        why = EXIT_SAID.get(seen.code, f"it ended with code {seen.code:#010x}")
        return f"{game} closed {when}: {why}."
    return f"{game} closed {when}, and Windows didn't log why."


def read_signals(error_window: bool, ever_seen: bool, still_alive: bool,
                 ran_seconds: float, crashed_in: str = "", executable_name: str = "") -> str:
    """
    what the watch saw, turned into a verdict.

    UNKNOWN matters most: a cancel at Steam's prompt or a slow machine must
    never get a working patch torn off. `crashed_in` is the module Windows
    logged a crash in. Any dll but the exe is CRASHED, never a DRM refusal;
    one in the exe stays a refusal, since a wrapper that finds itself patched
    can crash the game from its own code.
    """
    if error_window:
        return DRM_ERROR
    if crashed_in and (not ever_seen or not still_alive and ran_seconds < DIED_FAST_SECONDS):
        if crashed_in.lower() != executable_name.lower():
            return CRASHED
        # it was there to crash, however fast it went
        ever_seen = True
    if not ever_seen:
        return UNKNOWN
    if still_alive:
        return OK
    return DRM_ERROR if ran_seconds < DIED_FAST_SECONDS else OK


def _running(name: str) -> bool:
    """whether a process with this executable name exists right now"""
    return bool(_pids(name))


def _pids(name: str) -> list:
    """every process with this executable name, by id"""
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID:
        return []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        wanted, out = name.lower(), []
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.lower() == wanted:
                out.append(int(entry.th32ProcessID))
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return out
    finally:
        kernel32.CloseHandle(snapshot)


def _open(pid: int):
    """a handle that keeps its exit code readable after it's gone, or None"""
    import ctypes

    # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, which a game running
    # as the same user always grants
    handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
    return handle or None


def _exit_code(handle) -> int | None:
    import ctypes
    from ctypes import wintypes

    if not handle:
        return None
    code = wintypes.DWORD()
    if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
        return None
    # STILL_ACTIVE: not gone after all
    return None if code.value == 259 else int(code.value)


def _close(handle) -> None:
    import ctypes

    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)


# Event 1000's fields, in the order Windows 7 writes them with no names
CRASH_FIELDS = ("AppName", "AppVersion", "AppTimeStamp", "ModuleName", "ModuleVersion",
                "ModuleTimeStamp", "ExceptionCode", "FaultingOffset", "ProcessId",
                "ProcessCreationTime", "AppPath", "ModulePath")


def read_crashes(xml: str) -> list:
    """
    wevtutil's events, newest first, as dicts of their fields plus when.
    wevtutil writes them one after another with no root, so one goes round them
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(f"<Events>{re.sub(r'<[?]xml[^>]*>', '', xml)}</Events>")
    except ET.ParseError:
        return []
    out = []
    for event in root:
        fields, values = {}, []
        when = ""
        for node in event.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "TimeCreated":
                when = node.get("SystemTime", "")
            elif tag == "Data":
                values.append(node.text or "")
                if node.get("Name"):
                    fields[node.get("Name")] = node.text or ""
        if not fields:
            fields = dict(zip(CRASH_FIELDS, values))
        fields["when"] = when
        out.append(fields)
    return out


def _since(when: str) -> float:
    """an event's SystemTime as epoch seconds, 0 for one that won't read"""
    from datetime import datetime

    try:
        # seven decimal places is one more than fromisoformat takes
        trimmed = re.sub(r"(\.\d{6})\d*", r"\1", when.replace("Z", "+00:00"))
        return datetime.fromisoformat(trimmed).timestamp()
    except ValueError:
        return 0.0


def pick_crash(events: list, executable_name: str, started: float, pid: int | None) -> dict:
    """
    the newest crash of this exe since the launch, as module, path and
    exception, or {}. By process id when there is one: a crash of an earlier
    launch is the same exe and a different story
    """
    for fields in events:
        if fields.get("AppName", "").lower() != executable_name.lower():
            continue
        # a second of slack, for the clock WER stamps by against ours
        if _since(fields.get("when", "")) < started - 1:
            continue
        if pid is not None and fields.get("ProcessId"):
            try:
                if int(fields["ProcessId"], 0) != pid:
                    continue
            except ValueError:
                pass
        return {"module": fields.get("ModuleName", ""), "path": fields.get("ModulePath", ""),
                "exception": fields.get("ExceptionCode", "")}
    return {}


def _crashes() -> list:
    """the last two minutes' Application Error events, or [] when they can't be read"""
    try:
        done = subprocess.run(
            ["wevtutil", "qe", "Application", "/rd:true", "/c:20", "/f:xml",
             "/q:*[System[Provider[@Name='Application Error'] and (EventID=1000) "
             "and TimeCreated[timediff(@SystemTime) <= 120000]]]"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    raw = done.stdout or b""
    # its /uni switch, or a console set up for it, makes it UTF-16
    wide = b"\x00" in raw[:64]
    for encoding in ("utf-16-le",) if wide else ("utf-8", "mbcs", "latin-1"):
        try:
            return read_crashes(raw.decode(encoding))
        except (UnicodeDecodeError, LookupError):
            continue
    return []


def crash_of(executable_name: str, started: float, pid: int | None,
             wait: float = CRASH_LOG_SECONDS) -> dict:
    """the crash Windows logged for this launch, waiting on WER for up to `wait`"""
    deadline = time.time() + wait
    while True:
        found = pick_crash(_crashes(), executable_name, started, pid)
        if found or time.time() >= deadline:
            return found
        time.sleep(1.0)


def is_error_title(title: str, executable_name: str) -> bool:
    """
    whether this window caption is one of the two ways this launch can fail.

    Steam's box is an exact string. Windows' loader boxes are "<exe> - System
    Error" and friends, matched by shape rather than by caption text, because
    the caption is translated and the executable name is not.
    """
    if not title:
        return False
    if title.strip() == ERROR_WINDOW_TITLE:
        return True
    if not executable_name:
        return False
    stem = executable_name.lower()
    lowered = title.lower()
    return any(lowered.startswith(stem + suffix.lower())
               for suffix in LOADER_ERROR_SUFFIXES)


def _error_window(executable_name: str = "") -> bool:
    """any top-level window that says this launch is not going to happen"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if user32.FindWindowW(None, ERROR_WINDOW_TITLE):
        return True
    if not executable_name:
        return False

    found = []
    # EnumWindows rather than FindWindowW, because the loader's caption carries
    # the executable name and cannot be matched exactly without knowing which
    # of the several error shapes Windows picked.
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _param):
        length = user32.GetWindowTextLengthW(handle)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, length + 1)
            if is_error_title(buffer.value, executable_name):
                found.append(True)
                return False
        return True

    user32.EnumWindows(visit, 0)
    return bool(found)


def watch(executable_name: str, seconds: float = WATCH_SECONDS) -> Seen:
    """
    watches for the game to come up or be refused, and when it dies fast,
    its exit code and logged crash. Returns as soon as it knows. Anything
    unexpected is UNKNOWN, since the only action on a verdict is undoing a
    patch.
    """
    if os.name != "nt":
        return Seen(UNKNOWN)
    started = time.time()
    deadline = started + seconds
    first_seen = last_seen = None
    pid = handle = None
    try:
        while time.time() < deadline:
            if _error_window(executable_name):
                return Seen(DRM_ERROR, window=True)
            alive = _pids(executable_name)
            now = time.time()
            if alive:
                if first_seen is None:
                    first_seen = now
                if pid not in alive:
                    # the one it handed itself over to, if it did, is the one
                    # whose exit says how this went
                    _close(handle)
                    pid, handle = alive[0], _open(alive[0])
                last_seen = now
                if now - first_seen >= DIED_FAST_SECONDS:
                    return Seen(OK, ran=now - first_seen)
            elif first_seen is not None and now - last_seen >= GONE_SECONDS:
                ran = last_seen - first_seen
                crash = crash_of(executable_name, started, pid)
                return Seen(read_signals(False, True, False, ran, crash.get("module", ""),
                                         executable_name),
                            ran=ran, code=_exit_code(handle), crash=crash)
            time.sleep(POLL_SECONDS)
        if first_seen is None:
            # gone between two polls, which a dll the loader turns away is,
            # leaves nothing to have seen but the crash it logged
            crash = crash_of(executable_name, started, None, wait=0)
            return Seen(read_signals(False, False, False, 0.0, crash.get("module", ""),
                                     executable_name), crash=crash)
    except Exception:
        # a watcher that throws must not take the launch with it. the user
        # pressed Play and the game is either running or it is not; our opinion
        # about it is the optional part.
        return Seen(UNKNOWN)
    finally:
        try:
            _close(handle)
        except Exception:
            pass
    return Seen(read_signals(False, first_seen is not None, first_seen is not None,
                             (last_seen - first_seen) if first_seen and last_seen else 0.0))


# How long to keep trying to put the executable back. Generous because the
# thing in the way is a modal dialog waiting on a human: Steam's error box
# holds the refused process open, the process holds the image mapped, and
# Windows refuses to replace a mapped image. Nothing can be written until
# somebody presses OK, and that is not on our schedule.
RELEASE_SECONDS = 300.0


def wait_until_replaceable(target, seconds: float = RELEASE_SECONDS) -> bool:
    """
    blocks until `target` can be overwritten, or the wait runs out.

    Steam's error box keeps the refused process alive with the exe mapped,
    which failed the first real undo with WinError 5. Probes by opening for
    write, which is what the image lock forbids; a rename succeeds on a mapped
    image and would move a live game's exe. False on timeout instead of
    raising, so a background thread can say so.
    """
    from pathlib import Path

    target = Path(target)
    deadline = time.time() + seconds
    delay = 0.5
    while True:
        try:
            with open(target, "r+b"):
                return True
        except OSError:
            pass
        except Exception:
            return False
        if time.time() >= deadline:
            return False
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)
