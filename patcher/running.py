"""
Is the game running right now.

NovaFix reads NovaFix.ini once, at startup. It imports GetPrivateProfileStringW
and WritePrivateProfileStringW but no ReadDirectoryChangesW and no
FindFirstChangeNotification, and the only things it exports beyond the D3D9
entry points are NovaFix_IsOverlayOpen and NovaFix_RestartHelper. So there is
no way for anything outside the game process to make a running NovaFix pick up
a config change.

That makes "did this take effect" a question with two different answers, and
the honest message depends on which one applies. Hence this.

Deliberately does NOT offer to close the game. These are Square Enix JRPGs with
save points rather than autosave, and terminating one to apply an anti-aliasing
setting can cost somebody an hour of Pulse.

It's also what stops a patch or an install starting under a running game.
Windows won't let anything replace a file the game has open, so a run that
started anyway would get some way in and then fail on the one it couldn't
write, which is a worse place to stop than the first click.
"""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _Entry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


def processes() -> list:
    """
    (pid, exe name lowercased) for every running process, empty when we
    cannot tell.

    Toolhelp rather than tasklist: no subprocess, no console window to
    suppress, no output to parse in whatever codepage the machine is set to.
    """
    if sys.platform != "win32":
        return []

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return []

    found = []
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        while True:
            found.append((entry.th32ProcessID, entry.szExeFile.lower()))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    except OSError:
        return []
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def process_names() -> set[str]:
    """every running process's exe name, lowercased. empty when we cannot tell"""
    return {name for _, name in processes()}


def image_path(pid: int) -> str | None:
    """
    the full path a process was started from, or None where Windows won't say.
    Limited information is all this asks for, which a process of the same
    user hands over; an elevated one doesn't, and neither do the system's
    """
    if sys.platform != "win32":
        return None
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(handle)


_own_kernel32 = None


def _kernel32():
    """
    kernel32 with these calls' real signatures, in an instance of its own: a
    HANDLE is pointer sized on 64-bit, and setting argtypes on the shared
    windll would change them for every other caller in the process
    """
    global _own_kernel32
    if _own_kernel32 is None:
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.OpenProcess.restype = wintypes.HANDLE
        dll.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        dll.QueryFullProcessImageNameW.restype = wintypes.BOOL
        dll.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                                   wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD))
        dll.CloseHandle.argtypes = (wintypes.HANDLE,)
        _own_kernel32 = dll
    return _own_kernel32


def running_from(folder, executable=None) -> str:
    """
    the exe name of a process started from inside `folder`, the game or
    Square Enix's launcher in front of it, or "". By the path each was started
    from, so a copy of the game somewhere else doesn't count; one Windows
    won't give the path of counts by `executable`'s name alone, which is the
    cheap direction to be wrong in when the cost is closing the game first
    """
    import os

    if not folder:
        return ""
    root = os.path.normcase(os.path.abspath(folder)).rstrip("\\/") + os.sep
    mine = Path(executable).name.lower() if executable else ""
    own = os.getpid()
    for pid, name in processes():
        if pid in (0, 4, own) or not name.endswith(".exe"):
            continue
        path = image_path(pid)
        if path is None:
            if mine and name == mine:
                return name
            continue
        if os.path.normcase(os.path.abspath(path)).startswith(root):
            return name
    return ""


def is_running(executable: Path | str | None) -> bool:
    """
    whether a process with this exe's name is up.

    Matched on name alone. The alternative is opening every process to read its
    full image path, which needs more rights than we have any business asking
    for to decide the wording of a sentence. A false positive costs an
    unnecessary "restart the game" hint, which is the cheap direction to be
    wrong in.
    """
    if not executable:
        return False
    return Path(executable).name.lower() in process_names()
