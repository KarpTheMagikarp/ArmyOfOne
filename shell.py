"""
the desktop window: pywebview + WebView2 around ui/index.html, with the Api
the page calls into, and a small localhost server for the UI's files.

    python shell.py
    python shell.py --debug     # right click > inspect element

The server speaks HTTP/1.1 with Range and keep-alive on purpose. The stdlib
SimpleHTTPRequestHandler is 1.0 with no Range, so every video seek refetched
the whole file and the cutscenes stuttered in the window but not in a browser.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import weakref
import webbrowser
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# has to be set before WebView2 spins up, so before any webview call.
# Chromium flags, forwarded verbatim. Kept small on purpose: the GPU forcing
# flags (--ignore-gpu-blocklist, --enable-gpu-rasterization, zero-copy) cause
# black regions and stale frames on blocklisted drivers. autoplay-policy just
# stops play() being rejected.
DEFAULT_ARGS = "--autoplay-policy=no-user-gesture-required"

# knobs for narrowing this down without editing the file:
#   --gpu         put the aggressive GPU flags back
#   --no-gpu      force software rendering, slow but a clean comparison
#   FFXIII_WEBVIEW_ARGS="..."   override the lot
GPU_ARGS = ("--ignore-gpu-blocklist --enable-gpu-rasterization "
            "--enable-zero-copy")
SOFTWARE_ARGS = "--disable-gpu --disable-gpu-compositing"


def browser_args(mode: str) -> str:
    override = os.environ.get("FFXIII_WEBVIEW_ARGS")
    if override:
        return override
    parts = [DEFAULT_ARGS]
    if mode == "gpu":
        parts.append(GPU_ARGS)
    elif mode == "software":
        parts.append(SOFTWARE_ARGS)
    return " ".join(parts)


CHUNK = 256 * 1024

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".ico": "image/x-icon",
}

# manifests get edited while the app is open, everything else is content
# addressed enough that a long cache is fine and saves a refetch on every
# game switch
NO_CACHE = {".json", ".html"}

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def root() -> Path:
    # one definition, shared with main.py. these two have to agree: the server
    # serves the UI out of this folder and the patcher reads manifests from it.
    from patcher.paths import bundled_data_root
    return bundled_data_root()


class Handler(BaseHTTPRequestHandler):
    """static file server that actually does byte ranges and keep-alive."""

    protocol_version = "HTTP/1.1"
    server_version = "ffxiii-shell"
    base = Path(".")

    def log_message(self, *a):
        pass  # the console is for the app's own logging, not request spam

    def do_HEAD(self):
        self.respond(body=False)

    def do_GET(self):
        self.respond(body=True)

    def resolve(self):
        """map the url onto a real file, refusing anything outside base."""
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        path = path.lstrip("/")
        try:
            from urllib.parse import unquote
            path = unquote(path)
        except Exception:
            pass
        target = (self.base / path).resolve()
        try:
            target.relative_to(self.base)
        except ValueError:
            return None  # someone walked out of the tree with ../
        if target.is_dir():
            target = target / "index.html"
        return target if target.is_file() else None

    def respond(self, body: bool):
        target = self.resolve()
        if target is None:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return

        size = target.stat().st_size
        ext = target.suffix.lower()
        ctype = TYPES.get(ext, "application/octet-stream")

        start, end = self.parse_range(size)
        if start is None and end == "bad":
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        partial = start is not None
        if not partial:
            start, end = 0, size - 1
        length = end - start + 1

        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        # this header is the whole point. without it Chromium assumes it can
        # never seek and buffers the entire clip before it will play or scrub.
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", formatdate(target.stat().st_mtime, usegmt=True))
        if ext in NO_CACHE:
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=604800")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        if not body:
            return

        try:
            with target.open("rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    buf = f.read(min(CHUNK, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    left -= len(buf)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # totally normal, Chromium cancels range requests constantly when
            # it decides it has buffered enough. not an error worth printing.
            pass

    def parse_range(self, size: int):
        """returns (start, end), or (None, None) for no range header."""
        header = self.headers.get("Range")
        if not header:
            return None, None
        m = RANGE_RE.match(header.strip())
        if not m:
            return None, None  # multi-range, just serve the whole thing
        first, last = m.group(1), m.group(2)
        if first == "":
            if last == "":
                return None, "bad"
            n = min(int(last), size)
            if n == 0:
                return None, "bad"
            return size - n, size - 1
        start = int(first)
        if start >= size:
            return None, "bad"
        end = int(last) if last else size - 1
        return start, min(end, size - 1)


def serve(directory: Path) -> int:
    """serve the repo root on a random free port, return the port.

    yes, a whole http server for one html file. index.html fetch()es
    assets/manifest.json and WebView2 is Chromium, so file:// fetch trips CORS.
    localhost sidesteps that and, now, actually streams media properly.
    """
    Handler.base = directory.resolve()

    class Quiet(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

        def handle_error(self, request, client_address):
            pass  # see the disconnect note in respond()

    # port 0 = let the OS pick, avoids clashing with anything already running
    httpd = Quiet(("127.0.0.1", 0), Handler)
    httpd.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return port


def cache_dir() -> str:
    # pywebview defaults to private_mode=True, which hands WebView2 a throwaway
    # profile. that means no persistent http cache, so every launch redownloads
    # and re-demuxes every clip from scratch. a real folder fixes that.
    d = Path(tempfile.gettempdir()) / "ffxiii-patcher-webview"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def webview2_missing() -> bool:
    """best effort guess at whether the Evergreen WebView2 runtime is absent.

    Advisory only, never blocks launch; it just picks the message after a
    launch fails. Both key paths are checked because 32-bit python on 64-bit
    windows gets redirected to WOW6432Node\\WOW6432Node and finds nothing.
    """
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False
    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    paths = [
        rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}",
        rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}",
    ]
    hives = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    for hive in hives:
        for path in paths:
            try:
                with winreg.OpenKey(hive, path) as k:
                    if winreg.QueryValueEx(k, "pv")[0] not in ("", "0.0.0.0"):
                        return False
            except OSError:
                continue
    return True


WEBVIEW2_MESSAGE = (
    "This app needs the Microsoft Edge WebView2 runtime, which is missing or "
    "too old on this PC.\n\nGrab the free Evergreen installer from:\n"
    "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
    "No admin rights needed, and you only do it once.")


def complain(msg: str) -> None:
    """console=False means print() goes nowhere, so use a real message box."""
    print(msg)
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, WINDOW_TITLE, 0x10)
    except Exception:
        pass


# where the work area starts. Not 0,0 with the taskbar docked left or top, and
# centring as if it were put the title bar under a top taskbar on a small screen
_work_origin = (0, 0)

# the primary display's scale, 1.5 at 150%. Read beside the work area, and in
# the same real-pixel terms
_work_scale = 1.0


def screen_size():
    """usable desktop, without touching pywebview.

    webview.screens spins up WinForms and .NET just to ask the monitor size,
    which Win32 answers for free. Work area, not full screen, since this also
    centres the window and the full screen puts its bottom under the taskbar.
    """
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            try:
                user32.SetProcessDPIAware()     # so the numbers are real pixels
            except Exception:
                pass
            SPI_GETWORKAREA = 0x0030
            rect = wintypes.RECT()
            if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width > 0 and height > 0:
                    global _work_origin, _work_scale
                    _work_origin = (rect.left, rect.top)
                    _work_scale = _system_scale(user32)
                    return width, height
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            pass
    return None


def _system_scale(user32) -> float:
    # GetDpiForSystem is 1607 and later; the device context answers on anything
    try:
        dpi = user32.GetDpiForSystem()
    except Exception:
        dpi = 0
    if not dpi:
        try:
            import ctypes

            dc = user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)    # LOGPIXELSX
            user32.ReleaseDC(0, dc)
        except Exception:
            dpi = 96
    return max(1.0, dpi / 96)


WINDOW_TITLE = "ArmyOfOne"

# an HD install picks its voice set out of setup.xml when it starts and writes
# into that set for the next hour, so a switch mid-run leaves HD in the set the
# game stops loading, with nothing grown yet for the row to warn about
MOD_GOING_IN = "still installing the last mod. Drop this one again once it's in."
VOICES_MID_INSTALL = ("The HD install is writing into this game's voice files. Change the "
                      "voices once it has finished.")

# WM_SIZING fires on every mouse move during a drag, so the callback has to
# outlive the function that installs it. dropped, python frees it, and the next
# message calls into nothing.
_wndproc_keepalive = []


def _window_handles(window):
    """
    every plausible HWND for our window, best first, as (hwnd, where from).
    .native is the documented route on newer pywebview, older ones get found
    by title. The caller checks which one sticks instead of trusting either.
    """
    import ctypes

    found = []
    try:
        handle = getattr(getattr(window, "native", None), "Handle", None)
        if handle is not None:
            to_int64 = getattr(handle, "ToInt64", None)
            found.append((int(to_int64() if to_int64 else handle), "native"))
    except Exception:
        pass
    try:
        by_title = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
        if by_title and not any(h == by_title for h, _ in found):
            found.append((int(by_title), "by title"))
    except Exception:
        pass
    return found


def hold_aspect(window) -> bool:
    """
    keep the window 16:9 while it's dragged.

    WM_SIZING hands over the rect before Windows uses it, so correcting it
    there means the edge just follows the mouse at the right ratio instead of
    snapping on release. Maximise and fullscreen are left to Windows; the page
    fills anything between 3:2 and 12:5.

    That one message is all Python hears. The window procedure is
    patcher/winproc's bit of x64 that passes everything else straight through,
    so clicks never wait on the GIL. Best effort: if it fails the page still
    lays out to whatever shape it gets.
    """
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    from patcher import display, winproc

    WM_SIZING, GWLP_WNDPROC = winproc.WM_SIZING, -4
    LRESULT = ctypes.c_ssize_t

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    try:
        candidates = _window_handles(window)
        if not candidates:
            print("aspect lock: OFF, could not find the window")
            return False

        # a user32 of our own, so these prototypes don't change anyone else's
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint,
                                     wintypes.WPARAM, wintypes.LPARAM)
        # 32 bit python has no ...PtrW and the plain W versions are the same
        # call there. without the argtypes ctypes truncates the previous proc
        # pointer to 32 bits on a 64 bit build and the window stops responding.
        setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        getter = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        getter.argtypes = [wintypes.HWND, ctypes.c_int]
        getter.restype = ctypes.c_void_p
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                                           wintypes.WPARAM, wintypes.LPARAM]
        user32.CallWindowProcW.restype = LRESULT

        def frame(handle):
            """
            what the chrome costs right now, measured rather than assumed.
            border metrics move with the DPI and with the Windows version, and
            a stale guess shows up as a permanent black strip along one edge.
            """
            outer, inner = RECT(), RECT()
            user32.GetWindowRect(handle, ctypes.byref(outer))
            user32.GetClientRect(handle, ctypes.byref(inner))
            return ((outer.right - outer.left) - (inner.right - inner.left),
                    (outer.bottom - outer.top) - (inner.bottom - inner.top))

        previous = None

        def proc(handle, message, wparam, lparam):
            try:
                if message == WM_SIZING:
                    box = ctypes.cast(lparam, ctypes.POINTER(RECT)).contents
                    box.left, box.top, box.right, box.bottom = display.constrain(
                        (box.left, box.top, box.right, box.bottom),
                        int(wparam), frame(handle))
                    return 1
            except Exception:
                # a throw out of a window procedure takes the window with it.
                # an unconstrained drag is the acceptable failure.
                pass
            return user32.CallWindowProcW(previous, handle, message, wparam, lparam)

        # try each candidate and confirm, rather than trusting the first. a
        # subclass can report success and leave the original proc in place,
        # which is a lock that silently does nothing, and that is exactly how
        # the first version of this shipped.
        why = "no candidate window"
        native = winproc.native_ok()
        for hwnd, how in candidates:
            hooked = WNDPROC(proc)
            # kept before anything can call it, and never let go: the window
            # outlives the process, and a proc python has freed is worse than
            # one that did not stick
            _wndproc_keepalive.append(hooked)
            python = ctypes.cast(hooked, ctypes.c_void_p).value
            stub = None
            if native:
                try:
                    stub = winproc.Native(python)
                    # live the moment the setter returns, so it needs somewhere
                    # to send things before it's told what it replaced
                    stub.set_prev(getter(hwnd, GWLP_WNDPROC) or 0)
                except Exception as e:
                    # whatever went wrong, Python's procedure is still a lock
                    print(f"aspect lock: no native procedure ({e}), Python's instead")
                    native, stub = False, None
            want = stub.address if stub else python
            previous = setter(hwnd, GWLP_WNDPROC, want)
            if not previous:
                why = f"hwnd {hwnd} ({how}) would not take the hook"
                continue
            if stub:
                stub.set_prev(previous)
            if getter(hwnd, GWLP_WNDPROC) != want:
                why = f"hwnd {hwnd} ({how}) did not keep the subclass"
                continue
            print(f"aspect lock: ON, hwnd {hwnd} ({how})"
                  + (", Python only for the drag" if stub else ""))
            return True
        print(f"aspect lock: OFF, {why}")
        return False
    except Exception as e:
        print(f"aspect lock: OFF ({type(e).__name__}: {e})")
        return False


def place_window(window, client, screen) -> bool:
    """
    the opening size and spot, set on the window itself in real pixels just
    before it's shown. pywebview 6 reads width, height, x and y as logical
    pixels and multiplies them by the display's scale, 5 does that to x and y
    only, and screen_size() measures in real pixels. At 125% or 150% that
    opened a window too big for the screen and off centre, a different way on
    each version. The HWND takes real pixels whichever is installed
    """
    if os.name != "nt" or not screen:
        return False
    import ctypes
    from ctypes import wintypes

    SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        for hwnd, how in _window_handles(window):
            outer, inner = wintypes.RECT(), wintypes.RECT()
            if not (user32.GetWindowRect(hwnd, ctypes.byref(outer))
                    and user32.GetClientRect(hwnd, ctypes.byref(inner))):
                continue
            # the chrome as this window has it, at this DPI, not CHROME_W's guess
            width = client[0] + (outer.right - outer.left) - (inner.right - inner.left)
            height = client[1] + (outer.bottom - outer.top) - (inner.bottom - inner.top)
            x = _work_origin[0] + max(0, (screen[0] - width) // 2)
            y = _work_origin[1] + max(0, (screen[1] - height) // 2)
            if user32.SetWindowPos(hwnd, None, x, y, width, height,
                                   SWP_NOZORDER | SWP_NOACTIVATE):
                print(f"window: placed at {x},{y}, {width}x{height} real pixels ({how})")
                return True
    except Exception as e:
        print(f"window: left where pywebview put it ({e})")
    return False


def filling_screen(window) -> bool:
    """
    maximised or fullscreen, where the size belongs to the monitor and nothing
    should pull it back to 16:9. Read off the WinForms form, so a pywebview
    without one, or anything odd, answers no
    """
    native = getattr(window, "native", None)
    try:
        return bool(getattr(native, "is_fullscreen", False)) \
            or str(getattr(native, "WindowState", "")) == "Maximized"
    except Exception:
        return False


def snap_to_aspect(window) -> bool:
    """
    the fallback when the WM_SIZING hook couldn't go in: snaps the window to
    16:9 after 250ms of no resizing. Snapping on every resize event fights the
    mouse the whole way.
    """
    from patcher import display

    events = getattr(window, "events", None)
    resized = getattr(events, "resized", None)
    if resized is None:
        print("window snap: unavailable on this pywebview")
        return False

    state = {"timer": None}
    guard = threading.Lock()

    def settle(width, height):
        # a maximised or fullscreen window is the monitor's shape, and a resize
        # here would pull it off the edges it was just put on
        if filling_screen(window):
            return
        want = display.nearest_16_9((width, height))
        # already square, which is every resize once the hook is working, and
        # also our own resize call coming back round as another event
        if abs(want[0] - width) <= 1 and abs(want[1] - height) <= 1:
            return
        try:
            window.resize(*want)
        except Exception as e:
            print(f"window snap: {e}")

    def later(width, height):
        with guard:
            if state["timer"] is not None:
                state["timer"].cancel()
            timer = threading.Timer(0.25, settle, (int(width), int(height)))
            timer.daemon = True    # never hold the process open on the way out
            state["timer"] = timer
            timer.start()

    try:
        resized += later
    except Exception as e:
        print(f"window snap: could not subscribe ({e})")
        return False
    return True


# what the exe hands to main.py. Named actions only: a bare game name there
# is the headless apply, which the exe never did and should not start doing
# because somebody typed the wrong thing into a shortcut.
CLI_ACTIONS = ("--revert", "--doctor", "--retry-4gb", "--setup-hd-pack", "--dry-run")


# held for the life of the process: the OS drops the mutex with it, crash or not
_instance = []


def already_open() -> bool:
    """
    True when another window of this app is up, after bringing it forward.
    A slow first launch gets double clicked, and two windows applying to one
    game clobbered each other's backup records. Downloads and commands take
    the CLI path before this, so they still reach the open window's data.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        # its own kernel32 with use_last_error: ctypes can call into Windows
        # between CreateMutexW and a plain GetLastError, and lose the answer
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.windll.user32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, "Local\\ArmyOfOne.window")
        if not handle:
            return False
        _instance.append(handle)
        if ctypes.get_last_error() != 183:      # ERROR_ALREADY_EXISTS
            return False
        other = user32.FindWindowW(None, WINDOW_TITLE)
        if other:
            user32.ShowWindow(other, 9)         # SW_RESTORE, in case it's minimised
            user32.SetForegroundWindow(other)
        return True
    except Exception:
        # a guard that can't tell lets the launch through, as before it existed
        return False


def is_cli_invocation(argv) -> bool:
    # one binary for the window and the CLI: Windows launches it with nxm://
    # for Mod Manager Download, and the messages send people to --revert and
    # friends, which main.py handles.
    return any(a.startswith("nxm://") or a.startswith("--handle-nxm")
               or a.split("=", 1)[0] in CLI_ACTIONS for a in argv)


# prints what is actually in assets/ on every launch. the single most common
# cause of "it is still laggy" is the old 4k clips never getting replaced, and
# nobody wants to open devtools to find that out.

_EBML_MASTERS = {0x18538067, 0x1654AE6B, 0xAE, 0xE0}   # Segment, Tracks, TrackEntry, Video
_EBML_WIDTH, _EBML_HEIGHT = 0xB0, 0xBA


def _vint(f, keep_marker):
    b = f.read(1)
    if not b or b[0] == 0:
        return None
    first, length, mask = b[0], 1, 0x80
    while not (first & mask):
        mask >>= 1
        length += 1
        if length > 8:
            return None
    val = first if keep_marker else (first & (mask - 1))
    rest = f.read(length - 1)
    if len(rest) != length - 1:
        return None
    for byte in rest:
        val = (val << 8) | byte
    return val


def webm_size(path, budget=4_000_000):
    """(width, height) straight out of the EBML header. no ffprobe dependency."""
    try:
        with open(path, "rb") as f:
            w = h = None
            while f.tell() < budget:
                eid = _vint(f, True)
                if eid is None:
                    break
                size = _vint(f, False)
                if size is None:
                    break
                if eid in _EBML_MASTERS:
                    continue                     # descend rather than skip
                data = f.read(size)
                if len(data) != size:
                    break
                if eid == _EBML_WIDTH:
                    w = int.from_bytes(data, "big")
                elif eid == _EBML_HEIGHT:
                    h = int.from_bytes(data, "big")
                if w and h:
                    return w, h
            return (w, h) if w and h else None
    except OSError:
        return None


def _clips_the_ui_wants(base, keys=("cut", "cutMp4"), kinds=(".webm", ".mp4")):
    """
    every clip filename the shipped manifest references, in order.

    Read off assets/manifest.json rather than listed here, so a game added to
    ui/jobs.json is covered without anyone remembering this function exists.
    """
    import json

    try:
        raw = (base / "assets" / "manifest.json").read_text(encoding="utf-8")
        games = json.loads(raw).get("games", [])
    except (OSError, ValueError):
        return []
    names = []
    for game in games:
        for key in keys:
            value = str(game.get(key) or "")
            if value.endswith(kinds):
                names.append(value.rsplit("/", 1)[-1])
    return names


def check_assets(base):
    """what's in assets/ and whether the UI can use it. A clip over 1440p is
    flagged for its decode, not its size: fullscreen on a 4K panel does show
    one, but it decodes slower than realtime in software and stutters."""
    folder = base / "assets"
    clips = sorted(folder.glob("*.webm")) if folder.is_dir() else []

    # name the ones the UI will actually ask for. "no .webm found" was true and
    # useless: every clip 404s, every game falls back to a still, and nothing
    # says which files are missing or that anything is wrong at all.
    wanted, missing = _clips_the_ui_wants(base), []
    for name in wanted:
        if not (folder / name).is_file():
            missing.append(name)
    if missing:
        print(f"assets: {len(missing)} of {len(wanted)} clips missing, "
              f"those games fall back to a still frame:")
        for name in missing:
            print(f"  {name}")
    # the small window's tier, which costs a bigger decode when it's absent
    # rather than a still
    small = [n for n in _clips_the_ui_wants(base, ("cut720",)) if not (folder / n).is_file()]
    if small:
        print(f"assets: no 720p cut for {', '.join(small)}, the 1440p one plays at every size")
    # a clip that can't play rests on its last frame, and without one on its first
    rest = [n for n in _clips_the_ui_wants(base, ("cutRest",), (".jpg",)) if not (folder / n).is_file()]
    if rest:
        print(f"assets: no last frame for {', '.join(rest)}, run tools/cut_stills.py")

    if not clips:
        return
    print("assets:")
    oversized = []
    for p in clips:
        dims = webm_size(p)
        kb = p.stat().st_size // 1024
        if dims:
            w, h = dims
            flag = ""
            if h > 1440:
                flag = "  <-- above 1440p, slow to decode"
                oversized.append(p.name)
            print(f"   {p.name:24} {w:>5}x{h:<5} {kb:>6} KB{flag}")
        else:
            print(f"   {p.name:24} {'?':>11} {kb:>6} KB")
    if oversized:
        print()
        print(f"   {len(oversized)} clip(s) above 1440p. these decode slower than")
        print("   realtime in software, which is felt as stutter no matter what")
        print("   the UI code does. replace them with the re-encoded versions.")
    print()


_printing = threading.Lock()


def print_diag(lines) -> None:
    # one write per burst, under a lock: each call from the page arrives on a
    # thread of its own, and print() writes the line and its newline apart
    if not lines:
        return
    text = "".join(f"   {line}\n" for line in lines) + "\n"
    with _printing:
        print(text, end="", flush=True)


def report_diag(win) -> None:
    """print what the page logged before the window was up, and have it send
    the rest itself as it's logged (Api.diag). Only the renderer can measure
    glyphs and layout, but nobody wants devtools open to read the numbers.
    """
    try:
        raw = win.evaluate_js(
            "window.__diagPush = true; JSON.stringify((window.__diag || []).splice(0))")
        lines = json.loads(raw) if raw else []
    except Exception:
        return
    print_diag(lines)


def _dialog(window, kind, log=None, file_types=()) -> dict:
    """
    one native picker, and an answer that says which way it went: picked,
    cancelled, empty or failed are told apart so the page can say something.
    Empty is real: WinForms' folder picker is a hack over OpenFileDialog and
    often returns an empty tuple.
    """
    if window is None:
        return {"ok": False, "error": "there is no window to open a dialog from"}
    try:
        result = window.create_file_dialog(kind, allow_multiple=False,
                                           file_types=tuple(file_types))
    except Exception as e:
        if log:
            log.warning("file dialog failed: %s", e)
        return {"ok": False, "error": f"the file browser could not be opened: {e}"}

    if result is None:
        return {"ok": False, "cancelled": True}
    if isinstance(result, str):
        # not what the API documents, and backends have done it
        return {"ok": True, "path": result}
    if not result:
        return {"ok": False, "empty": True,
                "error": "the browser closed without handing back a path"}
    return {"ok": True, "path": str(result[0])}


def _names(lifted) -> set:
    """the drawer mods game_files.lift() kept, by name"""
    return {mod["name"] for mod in lifted["mods"]} if lifted else set()


def _listed(names) -> str:
    from patcher.mods.titles import title

    names = [title(name) for name in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _open_dialog():
    """
    pywebview 6 renamed OPEN_DIALOG to FileDialog.OPEN and warns on the old
    name. accept either, because the old one is what earlier versions have and
    pinning the library over a constant would be silly.
    """
    import webview

    dialogs = getattr(webview, "FileDialog", None)
    return getattr(dialogs, "OPEN", None) if dialogs else webview.OPEN_DIALOG


def _folder_dialog():
    import webview

    dialogs = getattr(webview, "FileDialog", None)
    return getattr(dialogs, "FOLDER", None) if dialogs else webview.FOLDER_DIALOG


class Api:
    """the js bridge: links out, install detection, and running patches.

    every method here returns plain json types. pywebview marshals return
    values through json, so a Path or a dataclass arrives on the js side as
    null. patcher/bridge.py already flattens everything, this just carries it.
    """

    ALLOWED = ("https://codyle.vercel.app/", "https://github.com/",
               "https://www.linkedin.com/", "https://www.youtube.com/",
               "https://x.com/", "https://ko-fi.com/")

    def __init__(self):
        self._window = None
        self._fullscreen = False
        self._was_maximised = False
        self._bridge = None
        self._log = None
        self._busy = threading.Lock()
        # set to stop an in-flight run. One event per piece of work, not one
        # for the session, or a single Stop kills everything after it.
        self._cancel = threading.Event()
        # side operations get an event each (see _arm_side), and the HD run has
        # its own on top: its Stop is a button in its own terminal, and a
        # forty minute job sharing an event with every download watcher meant
        # one Stop cancelled the other and a new watcher cleared a pending Stop
        self._side_events = weakref.WeakSet()
        self._side_lock = threading.Lock()
        self._hd_cancel = threading.Event()
        # the game the UI is showing, so a dropped file knows which manifest
        # to resolve against without the page having to pass it every time
        self._current_game = None
        # job id -> Acquisition, so a drop can satisfy a wait already running
        self._acquiring = {}
        # one HD Project run at a time. it rewrites multi-gigabyte archives in
        # place, and two of them interleaved would be unrecoverable.
        self._hd_running = False
        self._unpacking = False
        # a drop going in from the Mods drawer. Apart from an unpack, so each
        # says the right thing to the other, and they still never overlap: a
        # .7z unpacks beside itself, and so does the HD Project's
        self._installing_mod = False
        # and never beside an Apply on the same game. On XIII-2 both rewrite
        # white_img, and a patch landing mid-zone is written over by an index
        # read before it. The archives' own lock catches that too; this says
        # so before anything starts. Each holds the ui game id it is running for.
        self._hd_game = None
        self._applying = None
        self._gate = threading.Lock()
        # the last game switch's launcher check, off the UI thread
        self._settling = None
        # pywebview runs each call from the page on a thread of its own, so the
        # first two to want the patcher at once could each build one
        self._making = threading.Lock()
        from patcher.pump import Pump
        self._pump = Pump(self._deliver)

    def bind(self, window) -> None:
        self._window = window

    def _patcher(self):
        """
        built on first use or by _warm() once the window's up, never in
        __init__. Importing the engine and loading every manifest during
        window construction delayed the first paint for something nothing
        needs until a game is picked.
        """
        if self._bridge is None:
            with self._making:
                if self._bridge is None:
                    from patcher.bridge import UiBridge
                    from patcher.logging_setup import setup_logging
                    from patcher.paths import log_path
                    self._log = setup_logging(log_path())
                    self._bridge = UiBridge(log=self._log)
        return self._bridge

    def _warm(self) -> None:
        """
        the patcher built while nobody's waiting on it. The first game anyone
        opened paid for the whole engine's import inline, and the path under
        its patch list said Scanning for as long as that took. Once the window
        is up there's a second or so of looking at the start page to hide it in.
        Private, or pywebview would hand it to the page too
        """
        try:
            self._patcher()
        except Exception:
            # the first real call builds it again and says why, where it's seen
            pass

    def diag(self, lines) -> None:
        """the page's diagnostics as they're logged, once report_diag has
        printed the backlog and asked for them. Only ever asked for with a
        console to print to"""
        if isinstance(lines, str):
            lines = [lines]
        if isinstance(lines, list):
            print_diag([str(line)[:2000] for line in lines[:200]])

    def toggle_fullscreen(self) -> dict:
        """
        F11 and Alt+Enter from the page: the whole monitor with no frame, then
        back to the window it was. pywebview keeps the size and spot it came
        from and puts them back itself. Reports the state the form says it's
        in where it can, so a flag of ours is only the fallback
        """
        window = self._window
        if window is None:
            return {"ok": False, "fullscreen": False}
        native = getattr(window, "native", None)
        if not self._fullscreen:
            # pywebview always comes back out as a normal window at the size
            # it left, so a maximised one came back maximised-sized and not
            # maximised, with the button out of step. Read before going in:
            # fullscreen is a maximised window too
            self._was_maximised = str(getattr(native, "WindowState", "")) == "Maximized"
        try:
            window.toggle_fullscreen()
        except Exception as e:
            print(f"fullscreen: {e}")
            return {"ok": False, "fullscreen": self._fullscreen}
        state = getattr(native, "is_fullscreen", None)
        if not isinstance(state, bool):
            state = not self._fullscreen
        self._fullscreen = state
        if not state and self._was_maximised:
            self._was_maximised = False
            try:
                window.maximize()
            except Exception as e:
                print(f"fullscreen: could not maximise again ({e})")
        return {"ok": True, "fullscreen": state}

    def open_url(self, url: str) -> bool:
        # only the links this app ships. the page is local and trusted, but the
        # bridge is reachable from any script running in it, and handing
        # arbitrary strings to the shell is not something to leave open.
        if not isinstance(url, str) or not url.startswith(self.ALLOWED):
            print(f"refused to open: {url!r}")
            return False
        webbrowser.open(url, new=2)
        return True

    def scan(self, ui_game_id: str) -> dict:
        """
        where is this game, and what's already done to it. Cheap enough to run
        inline on every game switch (registry, a vdf, a few stats).

        Only reads, except for the launcher's "don't show" flag and NovaFix's
        menu keys and add-on popup while they're still defaults, since opening
        the patcher is the next chance to stop Steam's Play showing Square
        Enix's launcher. That write runs on its own thread: a launcher holding
        setup.xml open makes it wait out a few seconds of retries.
        """
        self._current_game = ui_game_id
        try:
            found = self._patcher().scan(ui_game_id)
        except Exception as e:
            if self._log:
                self._log.exception("scan failed for %s", ui_game_id)
            return {"ok": False, "found": False, "path": None, "source": None,
                    "supported": [], "applied": [], "error": str(e)}
        if found.get("found"):
            self._settling = threading.Thread(target=self._silence_launcher,
                                              args=(ui_game_id,), daemon=True)
            self._settling.start()
        return found

    def nexus_status(self) -> dict:
        try:
            return self._patcher().nexus_status()
        except Exception as e:
            return {"configured": False, "signedIn": False, "error": str(e)}

    def nexus_jobs(self, ui_game_id: str, job_ids: list) -> list:
        """which of these jobs are Nexus-hosted and need an account"""
        try:
            return self._patcher().jobs_needing_nexus(ui_game_id, [str(j) for j in (job_ids or [])])
        except Exception:
            return []

    def nexus_sign_in(self) -> dict:
        """
        starts the Nexus login and returns immediately, same threading rule as
        apply(). this one blocks for even longer: the middle step is a person
        reading a browser page and clicking Authorize, up to five minutes.
        """
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "started": False, "error": "something else is already running"}
        threading.Thread(target=self._sign_in, daemon=True).start()
        return {"ok": True, "started": True, "error": None}

    def _sign_in(self) -> None:
        try:
            result = self._patcher().nexus_sign_in(
                # the login page goes to the real browser, never the webview:
                # navigating the webview replaces the app with a web page and
                # leaves no way back, the same reason open_url exists.
                open_browser=lambda url: webbrowser.open(url, new=2),
                on_share=lambda message: self._emit({"type": "nexus", "state": "progress",
                                                        "detail": message}),
            )
            self._emit({"type": "nexus", "state": "done", **result})
        except Exception as e:
            if self._log:
                self._log.exception("nexus sign in failed")
            self._emit({"type": "nexus", "state": "done", "ok": False,
                        "status": "error", "detail": str(e)})
        finally:
            self._busy.release()

    def nexus_set_key(self, api_key: str) -> dict:
        try:
            return self._patcher().nexus_set_key(str(api_key or ""))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def nexus_open_key_page(self) -> bool:
        """send the user to the page where a free personal key is generated"""
        webbrowser.open("https://www.nexusmods.com/users/myaccount?tab=api", new=2)
        return True

    def _arm_side(self):
        """
        a fresh cancel for one side operation. Stop is about the thing running
        now, and an event left set by an earlier press is how a drop ended up
        extracting its first member and then raising on the second. One each,
        so starting a second one cannot clear the first one's.
        """
        event = threading.Event()
        with self._side_lock:
            self._side_events.add(event)
        return event

    def hd_categories(self, ui_game_id: str, ui_job_id: str) -> dict:
        try:
            return self._patcher().hd_categories(str(ui_game_id), str(ui_job_id))
        except Exception as e:
            return {"categories": [], "selected": [], "installed": [], "error": str(e)}

    def hd_survey(self, ui_game_id: str, ui_job_id: str, mod_root: str) -> dict:
        """
        what each category costs and which are already in the game.

        Decorates the panel, so it never takes anything down. verified=False
        means we could not tell, which the page has to keep apart from
        "nothing is installed".
        """
        try:
            return self._patcher().hd_survey(str(ui_game_id), str(ui_job_id), str(mod_root))
        except Exception:
            return {"sizes": {}, "applied": [], "verified": False}

    def set_hd_categories(self, ui_game_id: str, ui_job_id: str, selected: list,
                          voices: bool | None = None) -> dict:
        try:
            return self._patcher().set_hd_categories(
                str(ui_game_id), str(ui_job_id), [str(s) for s in (selected or [])],
                None if voices is None else bool(voices))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def identify_drop(self, file_path: str, unpack: bool = True) -> dict:
        """
        what a path is. unpack=False keeps it cheap, which matters since this
        runs on the webview's thread and unpacking six gigabytes here freezes
        the window. The page asks cheap, gets "maybe-hdproject", and hands it
        to unpack_drop.
        """
        try:
            # unpacking again clears the folder a running install may be using
            return self._patcher().identify_drop(str(file_path),
                                                 unpack=bool(unpack) and not self._hd_running)
        except Exception as e:
            return {"kind": "unknown", "error": str(e)}

    def unpack_drop(self, file_path: str) -> dict:
        """
        unpacks an archive that might be the mod, off the UI thread.

        Answers with the same "dropped" event a real drop produces, carrying
        the unpacked folder, so it re-enters the page's existing routing
        instead of needing a second one. A folder is the cheap case there.
        """
        if self._unpacking:
            # busy, so the page keeps its disc going: the last one is still at it
            return {"ok": False, "busy": True,
                    "error": "still unpacking the last archive. Drop this one again once it "
                             "has finished."}
        if self._installing_mod:
            return {"ok": False, "busy": True, "error": MOD_GOING_IN}
        if self._hd_running:
            # a changed archive clears its unpacked folder, and the running
            # install may be working out of it. Said through "dropped", which
            # is the answer the page is waiting on.
            why = "an HD install is running, so drop the archive again once it has finished"
            self._emit({"type": "dropped", "paths": [], "from": str(file_path), "error": why})
            return {"ok": False, "error": why}
        self._unpacking = True

        def work():
            try:
                found = self._patcher().identify_drop(
                    str(file_path), unpack=True,
                    progress=lambda done, of: self._emit(
                        {"type": "mods", "job": "unpack", "phase": "unpack", "done": done,
                         "of": of, "bytes": True}))
                root = (found.get("detail") or {}).get("root") if found else None
                # "could not unpack it" is a different answer from "it is not
                # the mod", and giving the second for the first sends people
                # off to download the right file again
                why = (found or {}).get("error") or "that archive is not the HD Project"
                self._emit({"type": "dropped", "paths": [root] if root else [],
                            "from": str(file_path), "error": None if root else why})
            except Exception as e:
                if self._log:
                    self._log.exception("unpacking a drop failed")
                self._emit({"type": "dropped", "paths": [], "from": str(file_path),
                            "error": str(e)})
            finally:
                self._unpacking = False

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "started": True}

    def install_hd_project(self, ui_game_id: str, mod_root: str, selected: list,
                           mode: str = "add", first: list | None = None,
                           voices: bool = False) -> dict:
        """
        runs the HD Project's own tools from here, streaming into the terminal.

        `first` is the game's required fixes. Whatever's missing goes in before
        the mod, since XIII-2 refuses HD without the 4GB Patch. Returns once the
        thread is running; everything after arrives as "hd" events. The cancel
        is armed fresh so an old Stop can't kill this one.

        mode "add" builds `selected` on top of what the archive already has.
        "set" restores the vanilla backup first, which is the only way a
        category comes off, since the mod's output is one repacked archive with
        no per category undo. "add" also repairs: every HD file is derived from
        the vanilla one in models\\orig, so rebuilding writes the same bytes
        whatever's in the game now.

        `voices` is the other voice set's box. Ticked, the build goes into that
        set too; unticked, only the launcher's set changes, since the other
        costs its own backup and gigabytes of art. Ticked with no category,
        it's just that copy.
        """
        return self._start_hd(ui_game_id, mod_root, selected, mode, first, both=bool(voices))

    def _start_hd(self, ui_game_id: str, mod_root: str, selected: list, mode: str = "add",
                  first: list | None = None, both: bool = False) -> dict:
        """install_hd_project's run. `both` is the other voice set's box"""
        if self._hd_running:
            return {"ok": False, "error": "an install is already running"}
        if self._unpacking:
            # a changed archive clears its folder before unpacking again, and
            # this would be installing out of it
            return {"ok": False, "error": "an archive is still unpacking. Try again once it "
                                          "has finished."}
        if self._installing_mod:
            return {"ok": False, "error": "a mod is still going in from the Mods drawer. Try "
                                          "again once it's in."}
        if mode not in ("add", "set"):
            return {"ok": False, "error": f"no such install mode: {mode}"}
        running = self._running(ui_game_id)
        if running:
            return {"ok": False, "error": running}

        resolved = self._patcher().scan(ui_game_id)
        if not resolved.get("found") or not resolved.get("path"):
            return {"ok": False, "error": resolved.get("error") or "couldn't find the game"}

        from patcher.mods.hd_games import for_job
        job = self._patcher().hd_job(ui_game_id)
        game = for_job(job) if job else None
        if game is None:
            return {"ok": False, "error": "this game has no HD mod to install"}

        with self._gate:
            if self._hd_running:
                return {"ok": False, "error": "an install is already running"}
            if self._applying == ui_game_id:
                return {"ok": False, "error": "a patch run is going on this game, and it may be "
                                             "writing the same archives. Install once it has "
                                             "finished."}
            cancel = self._hd_cancel = threading.Event()
            self._hd_running, self._hd_game = True, ui_game_id

        def emit(stage, **rest):
            self._emit({"type": "hd", "stage": stage, **rest})

        def work():
            from patcher.mods.hd_install import (InstallError, RestoreIncomplete, after_changes,
                                                 so_far)
            from patcher.mods.hd_layout import layout_of
            from patcher.mods.hd_runner import Cancelled
            from patcher.paths import run_log_path, undo_store_for
            from patcher.runlog import RunLog

            log = RunLog(run_log_path(time.strftime(f"{job}-%Y%m%d-%H%M%S")),
                         header={"mod": mod_root, "game": resolved["path"],
                                 "categories": ", ".join(selected or [])})
            revert = layout_of(Path(resolved["path"])).revert
            # what has actually reached the game, filled in as it happens, so
            # every way this can end can say so instead of assuming nothing did
            landed = []
            verdict = "ENDED without reaching a verdict, which should not happen"
            # the terminal event, held back and emitted from the finally so the
            # running flag is already clear when the page hears about it.
            # Pressing Install the moment "Done" appears was answered with "an
            # install is already running", because the flag cleared a few
            # microseconds later.
            ending = None
            # the drawer's mods as they are, to go back on top of whatever the
            # build repacks out from under them (game_files.lift)
            lifted = None
            try:
                found = game.detect(Path(mod_root))
                if found is None:
                    verdict = f"REFUSED: the mod folder is not {game.name}"
                    ending = ("failed", {"error": f"that folder is not {game.name} any more"})
                    return

                patch = self._patcher().hd_categories(ui_game_id, job)
                emit("started", total=0, log=str(log.path) if log.usable else "")

                # stream is "out", "err" or "cmd". The terminal colours stderr
                # red off the back of it, which is the whole point: a merged
                # stream is readable and gives you no way to tell the one line
                # that explains a failure from the three thousand that do not.
                def line(text, stream="out"):
                    log.write(text, stream)
                    # "log" is the run log's alone: a diagnostic, not news
                    if stream != "log":
                        emit("line", text=text, stream=stream)

                def step(current, index, total):
                    log.step(current.label, index, total)
                    emit("step", label=current.label, index=index, total=total)

                # progress inside one step, for the long ones. The seed copies
                # two thousand files under a single step number, so without
                # this the bar sits still through the slowest part of the run
                # that is not a tool.
                def within(share):
                    emit("progress", share=share)

                def stage(label, index, total):
                    log.step(label, index, total)
                    emit("step", label=label, index=index, total=total)

                # nothing named is a removal or a refusal, and neither needs them.
                # The voices box alone can still run the tools on the other set,
                # and XIII-2's won't take HD without the 4GB Patch
                if not selected and both and mode == "add":
                    missing = self._no_other_set(Path(resolved["path"]))
                    if missing:
                        # asked before the fixes go on, since a refusal after
                        # them would have changed the game for nothing
                        verdict = "REFUSED: there's no other voice set"
                        ending = ("failed", {"error": missing,
                                             "log": str(log.path) if log.usable else ""})
                        return
                if selected or (both and mode == "add"):
                    self._patches_first(ui_game_id, first, cancel, line, landed)

                # the record says what we installed; the archive says what is
                # actually in there. They disagree after Steam verifies the
                # files, after a manual restore, after the state file is lost,
                # and on a game copied from another machine, and the record is
                # the one that loses. Everything below turns on this answer, so
                # it is asked of the archive first and only falls back.
                from patcher.mods import hd_mirror
                from patcher.mods.hd_install import prepare, restore
                from patcher.mods.hd_mirror import LANGUAGE

                prep = prepare(Path(resolved["path"]), Path(found["root"]),
                               undo_store_for(Path(resolved["path"])))
                other = "c" if prep.variant == "u" else "u"
                state, backed_up = game.applied(Path(resolved["path"]), found,
                                                patch["categories"], prep)
                if state is not None and state.verified:
                    here = set(state.categories)
                elif backed_up:
                    # the index would not parse, so the record is what is left
                    here = set(patch.get("installed") or [])
                else:
                    # no backup means no run of ours has ever finished here, and
                    # the backup and the record are written by the same code
                    # path, so a record claiming otherwise has outlived the
                    # thing it describes. Believing it refuses a first install.
                    here = set()

                want = set(selected or [])

                def touched(ids):
                    return game.touched(found, patch["categories"], ids)

                def whole(result, ids) -> set:
                    """
                    what a finished build put in whole. An id it skipped or
                    left as it was is whatever was there before, so the
                    category it's in is still owed, found by the files its
                    step would have written; a zone's steps write none of
                    those, and a zone short of anything owes the zones
                    """
                    done, short, zones = set(ids), [], bool(result.gaps)
                    for missed in getattr(result, "missed", None) or ():
                        if not missed.copies:
                            zones = True
                        # the tree it was going into, then the game's own path
                        short += ["/".join(to.replace("\\", "/").split("/")[1:]).lower()
                                  for _, to in missed.copies]
                    if zones:
                        done -= game.zone_categories(found, patch["categories"])
                    for category in sorted(done) if short else ():
                        fresh = touched([category])
                        if fresh is None or any(path.startswith(tuple(fresh.paths))
                                                for path in short):
                            done.discard(category)
                    return done

                def build_other(missing, blocked) -> str:
                    """
                    what the copy had to leave, built for the other set by the
                    tools: what that set doesn't have, and what this run built
                    in the pass that stayed out, or a repair would never reach it
                    """
                    zones = game.zone_categories(found, patch["categories"])
                    redo = ((want - zones if "characters" in blocked else set())
                            | (want & zones if "zones" in blocked else set()))
                    categories = sorted(set(missing) | redo)
                    if not categories:
                        return ""
                    going = LANGUAGE[other]
                    names = {c["id"]: c.get("label") or c["id"] for c in patch["categories"]}
                    line(f"[voices] {', '.join(names.get(c, c) for c in categories)} for the "
                         f"{going} voice files with the mod's own tools, since those files "
                         "differ between the two sets")
                    later = f"so install again with {going} Voices Too ticked to finish it"
                    # a build stopped partway leaves an older one the archives
                    # can't tell from a finished one, so it's owed until it's in
                    self._patcher().owe_hd(ui_game_id, other, categories)
                    try:
                        more = game.install(
                            Path(resolved["path"]), found, patch["categories"],
                            set(categories), undo_store_for(Path(resolved["path"])),
                            on_line=line, on_step=step, cancel=cancel, on_share=within,
                            landed=landed, variant=other)
                    except Cancelled:
                        line(f"[voices] stopped, {later}", "note")
                        more = None
                    except InstallError as e:
                        line(f"[voices] {e}", "err")
                        line(f"[voices] {later}", "note")
                        more = None
                    except Exception as e:
                        # this set's install is in and recorded, and a crash in
                        # the other one's is the other one's
                        if self._log:
                            self._log.exception("building the other voice set failed")
                        line(f"[voices] the {going} build crashed ({e}), {later}", "err")
                        more = None
                    if more is None or not more.ok:
                        if more is not None:
                            line(f"[voices] {more.failed.label} stopped with code {more.code}, "
                                 f"{later}", "err")
                        self._settle_hd_record(ui_game_id, mod_root, job, other)
                        return "didn't build, see above."
                    went = whole(more, categories)
                    self._patcher().owe_hd(ui_game_id, other, went, paid=True)
                    hd_mirror.settle(Path(resolved["path"]), prep.variant, other, went)
                    had = (self._patcher().hd_categories(ui_game_id, job).get("sets")
                           or {}).get(other) or []
                    self._patcher().record_hd_installed(
                        ui_game_id, job, sorted(set(had) | set(categories)),
                        built=sorted(categories), source=found["root"], variant=other)
                    return "built with the mod's tools."

                if mode == "set":
                    # the archive is rebuilt from vanilla, so what is named is
                    # what survives and everything else is gone by definition
                    removed = len(here - want)
                    # asked before the restore, because a refusal after it
                    # comes with the user's install already taken off
                    if want:
                        game.precheck(Path(resolved["path"]), found, patch["categories"], want,
                                      prep)
                    emit("line", text="[restore] putting the vanilla archives back first, "
                                      f"then {len(want)} categories on top of them")
                    # back to vanilla whoever changed it, which is not Revert's
                    # question. The other voice set too when its box is ticked,
                    # since the copy after this only adds: left as it was, it
                    # would keep what this takes off
                    from patcher.mods.hd_install import voice_sets

                    on_disk = voice_sets(Path(resolved["path"]), whole=True)
                    voices_back = [prep.variant] + ([other] if both and other in on_disk else [])
                    put_back = []
                    for each in voices_back:
                        try:
                            back = restore(prep, on_line=line, variant=each)
                        except RestoreIncomplete as e:
                            # some of it went back and the record is behind the disk
                            landed.append("part of the vanilla archives")
                            self._restored(ui_game_id, resolved["path"], e.restored, line)
                            raise
                        if back and not put_back:
                            landed.append("the vanilla archives")
                        if back:
                            self._restored(ui_game_id, resolved["path"], back, line, landed,
                                           again=set(first or ()) if want else ())
                        put_back += back
                    for each in voices_back[1:]:
                        self._patcher().record_hd_installed(
                            ui_game_id, job, sorted(game.zones_in(Path(resolved["path"]), found,
                                                                  patch["categories"], each)),
                            built=[], variant=each)
                    # vanilla owes nobody anything: what this builds and copies
                    # from here is noted as it goes
                    for each in voices_back:
                        self._patcher().owe_hd(ui_game_id, each, None, paid=True)
                    if len(voices_back) > 1:
                        hd_mirror.reverted(Path(resolved["path"]))
                    if not want:
                        self._patcher().record_hd_installed(
                            ui_game_id, job, sorted(game.zones_in(Path(resolved["path"]), found,
                                                                  patch["categories"],
                                                                  prep.variant)),
                            variant=prep.variant)
                        verdict = f"OK. Restored to vanilla, {removed} categories removed."
                        ending = ("done", {"steps": 0, "applied": 0, "skipped": 0,
                                           "nothing": 0, "removed": removed,
                                           "log": str(log.path) if log.usable else ""})
                        return
                    # the copies that went back can predate a fix, and the mod
                    # goes on top of whatever is there
                    self._patches_first(ui_game_id, first, cancel, line, landed)
                else:
                    removed = 0
                    # ticking something already applied is a repair: every HD
                    # file comes from models\\orig, so a rebuild writes the
                    # same bytes whatever's in the game now.
                    repairing = sorted(want & here)
                    if repairing:
                        names = {c["id"]: c["label"] for c in patch["categories"]}
                        doubt = game.cache_doubt(found, prep.variant)
                        if doubt:
                            # the one case where over the top is worse than
                            # doing nothing, so it stops rather than guessing
                            verdict = "REFUSED: the originals cache is not vanilla"
                            # the fixes may have gone in ahead of this, and only
                            # the [patches] lines further up would say so
                            ending = ("failed", {
                                "error": doubt,
                                "note": so_far(landed, revert) if landed else None,
                                "log": str(log.path) if log.usable else ""})
                            return
                        line("[repair] rebuilding from the vanilla originals: "
                             + ", ".join(names.get(i, i) for i in repairing))
                    if not want and both:
                        # the voices box on its own: what this set has, into
                        # the other one, with the tools for what differs
                        going = LANGUAGE[other]
                        outcome = self._copy_voices(ui_game_id, Path(resolved["path"]),
                                                    prep.variant, line, stage, within, cancel,
                                                    touched=touched)
                        if outcome is None:
                            # gone between the check up top and here
                            verdict = "REFUSED: there's no other voice set"
                            ending = ("failed", {
                                "error": f"the {going} voice files aren't all there, so there's "
                                         "nothing to put the HD Project in.",
                                "note": so_far(landed, revert) if landed else None,
                                "log": str(log.path) if log.usable else ""})
                            return
                        rest = ""
                        if not outcome.whole:
                            missing, _, known = self._patcher().voice_missing(
                                ui_game_id, prep.variant, other)
                            rest = build_other(missing if known else [], set(outcome.blocked))
                        verdict = (f"OK. {outcome.files} character files and "
                                   f"{len(outcome.zones)} zones into the {going} voice files."
                                   + (f" The rest {rest}" if rest else ""))
                        ending = ("done", {"steps": 0, "applied": 0, "skipped": 0, "nothing": 0,
                                           "removed": 0,
                                           "copied": {"files": outcome.files,
                                                      "zones": len(outcome.zones),
                                                      "language": going, "built": rest},
                                           "log": str(log.path) if log.usable else ""})
                        return
                    if not want:
                        verdict = "REFUSED: nothing was selected"
                        ending = ("failed", {
                            "error": "nothing is ticked, so there is nothing to install. "
                                     "Tick a category, or tick one that already says "
                                     "APPLIED to build it again.",
                            "log": str(log.path) if log.usable else ""})
                        return

                mark = len(landed)
                if lifted is None:
                    from patcher.mods.game_files import lift
                    lifted = lift(Path(resolved["path"]))
                try:
                    result = game.install(
                        Path(resolved["path"]), found, patch["categories"], want,
                        undo_store_for(Path(resolved["path"])),
                        on_line=line, on_step=step, cancel=cancel, on_share=within,
                        landed=landed,
                    )
                finally:
                    # once any of it is in, the other set is behind this one
                    # until a copy finishes, whatever its sizes say. Ticked or
                    # not: a rebuild at the length it had gets past the sizes,
                    # and a copy asked for later has to read it
                    if len(landed) > mark:
                        self._owe_copy(ui_game_id, Path(resolved["path"]), prep.variant, want)
                if result.ok:
                    went = whole(result, want)
                    self._patcher().owe_hd(ui_game_id, prep.variant, went, paid=True)
                    # adding leaves what was there, so the record is the union.
                    # Rebuilding names the survivors, so the record is the set.
                    # Only what this run built gets this build's pipeline stamp,
                    # and a zone left out is still whatever an earlier run left:
                    # stamping over it hid an older build's broken zone for good.
                    settled = set(want | here) if mode == "add" else set(want)
                    # XIII's applied() reads the scr index, and its zones sit beside it
                    settled |= game.zones_in(Path(resolved["path"]), found, patch["categories"],
                                             prep.variant)
                    built = set(want)
                    if result.gaps:
                        built -= (game.zone_categories(found, patch["categories"])
                                  & set(patch.get("installed") or []))
                    self._patcher().record_hd_installed(
                        ui_game_id, job, sorted(settled), built=sorted(built),
                        source=found["root"], variant=prep.variant)
                    # this set's own build of them, so whatever the other set
                    # built of them last isn't newer. Left owed, the same build
                    # in both sets read as an older one whichever way you went
                    hd_mirror.settle(Path(resolved["path"]), other, prep.variant, went)
                    # a repack carries the drawer's mods somewhere new, and the
                    # copy into the other voice set goes by where they are
                    from patcher.mods.game_files import settled as files_settled
                    for text in files_settled(Path(resolved["path"]), keep=_names(lifted)):
                        line(f"[files] {text}", "note")

                    # what it built into the other voice set as well, when the
                    # box for it is ticked, so the voices switch with HD either way
                    copied = self._voices_too(ui_game_id, Path(resolved["path"]), prep.variant,
                                              line, stage, within, cancel, build=build_other,
                                              touched=touched) if both else ""
                    verdict = (f"OK. {result.steps_run} steps, {result.applied} applied, "
                               f"{len(result.skipped)} skipped, "
                               f"{len(result.nothing)} with nothing to install."
                               + (f" {copied}" if copied else ""))
                    # the skip count goes to the page too. A run that applied
                    # 800 of 854 is a success with a gap in it, and saying so is
                    # the difference between a known gap and a mystery later.
                    # nothing is separate: an id the mod ships no art for is not
                    # a gap, and 102 of those reading as skips made a complete
                    # install look half broken.
                    ending = ("done", {"steps": result.steps_run, "applied": result.applied,
                                       "skipped": len(result.skipped),
                                       "nothing": len(result.nothing), "removed": removed,
                                       "log": str(log.path) if log.usable else ""})
                else:
                    # the reason the tool gave, not just the exit code. "stopped
                    # with code 1" on its own cost an evening of guessing at
                    # disk space, and the tool had said why on stderr the whole
                    # time, into a pipe that was being merged and discarded.
                    verdict = (f"FAILED at {result.failed.label} (exit {result.code})"
                               + (f": {result.reason}" if result.reason else ""))
                    ending = ("failed", {
                        "error": f"{result.failed.label} stopped with code {result.code}",
                        "reason": result.reason,
                        "tail": result.errors[-8:] or result.lines[-4:],
                        "label": result.failed.label, "code": result.code,
                        "skipped": len(result.skipped), "applied": result.applied,
                        "note": so_far(landed, revert),
                        "log": str(log.path) if log.usable else ""})
            except Cancelled:
                # a zone that was going in when Stop arrived finished first,
                # and restoring in set mode happens before any of this, so
                # "nothing changed" is only sometimes true
                verdict = f"CANCELLED. {so_far(landed, revert)}"
                ending = ("cancelled", {"note": f"Stopped. {so_far(landed, revert)}"})
            except InstallError as e:
                # these say what state the game is in themselves, a partial
                # restore included. A refusal after a rebuild's restore does
                # not: it only knows that it changed nothing itself.
                said, note = str(e), None
                if not isinstance(e, RestoreIncomplete) and landed \
                        and so_far(landed, revert) not in said:
                    said, note = after_changes(said), so_far(landed, revert)
                verdict = f"REFUSED: {said}"
                ending = ("failed", {"error": said, "note": note,
                                     "log": str(log.path) if log.usable else ""})
            except Exception as e:
                if self._log:
                    self._log.exception("hd project install failed")
                verdict = f"CRASHED: {type(e).__name__}: {e}"
                ending = ("failed", {"error": str(e), "note": so_far(landed, revert),
                                     "log": str(log.path) if log.usable else ""})
            finally:
                # a run that stopped partway leaves the record behind the disk,
                # and the record is what the panel falls back on
                if landed and (ending is None or ending[0] != "done"):
                    self._settle_hd_record(ui_game_id, mod_root, job)
                # a rebuild starts from the backup, which has the Mods drawer's
                # mods in as they were when it was taken
                if landed or lifted:
                    try:
                        from patcher.mods.game_files import lay_back, settled

                        def said(text):
                            log.write(f"[files] {text}", "note")
                            emit("line", text=f"[files] {text}", stream="note")

                        for text in settled(Path(resolved["path"]), keep=_names(lifted)):
                            said(text)
                        back = lay_back(Path(resolved["path"]), lifted, on_line=said)
                        if back:
                            said(f"{_listed(back)} went back on top, the way "
                                 f"{'it was' if len(back) == 1 else 'they were'}.")
                    except Exception:
                        if self._log:
                            self._log.exception("settling the drawer's mods after an install")
                # the first log anyone sent back stopped at the tool's last line
                # of output with no record of what happened next, so the file
                # that exists to explain a failure did not mention one.
                log.close(verdict)
                self._hd_running, self._hd_game = False, None
                if ending:
                    emit(ending[0], **ending[1])

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "started": True}

    def _voices_too(self, ui_game_id: str, install_dir: Path, source: str, line, stage,
                    within, cancel, build=None, touched=None) -> str:
        """
        what an install just built, put into the other voice set as well, since
        its box was ticked: copied across, and what the two languages ship
        differently built for it by `build(missing, blocked)`, the mod's own
        tools. Never what decides the install: a copy that can't happen says
        why, and ticking the box again finishes it. A few words for the run
        log's verdict, or "" when there's no other set to fill.
        """
        from patcher.mods import hd_mirror
        from patcher.mods.hd_install import InstallError
        from patcher.mods.hd_runner import Cancelled

        other = "c" if source == "u" else "u"
        going = hd_mirror.LANGUAGE[other]
        later = f"install again with {going} Voices Too ticked to finish it"
        try:
            outcome = self._copy_voices(ui_game_id, install_dir, source, line, stage, within,
                                        cancel, touched=touched)
        except Cancelled:
            line(f"[voices] stopped before the {going} voice files got it, so {later}", "note")
            return f"The {going} voice files: stopped."
        except InstallError as e:
            line(f"[voices] {e}", "err")
            if "Revert" not in str(e):
                line(f"[voices] {later[0].upper()}{later[1:]}.", "note")
            return f"The {going} voice files: {e}"
        except Exception as e:
            if self._log:
                self._log.exception("copying HD into the other voice set failed")
            line(f"[voices] the {going} voice files didn't get it ({e}), so {later}", "err")
            return f"The {going} voice files: {e}"
        if outcome is None:
            return ""
        if outcome.whole:
            return f"The {going} voice files: copied too." if not outcome.already else ""
        missing, _, known = self._patcher().voice_missing(ui_game_id, source, other)
        built = build(missing if known else [], set(outcome.blocked)) if build else ""
        return f"The {going} voice files: part copied, " + (f"the rest {built}" if built
                                                             else "see above.")

    def _copy_voices(self, ui_game_id: str, install_dir: Path, source: str, line, stage,
                     within, cancel, touched=None):
        """
        hd_mirror.copy() from `source` into the other voice set, recorded, or
        None when that set isn't on disk. Raises whatever the copy does.
        `touched(ids)` is what installing those categories rebuilds, so the copy
        reads what's owed in full and takes the rest on sizes
        """
        from patcher.mods import hd_mirror
        from patcher.mods.hd_install import voice_sets
        from patcher.paths import undo_store_for

        other = "c" if source == "u" else "u"
        if other not in voice_sets(install_dir, whole=True):
            return None
        # the payloads, not lacking()'s sizes: a repair or a newer build of the
        # mod can rewrite a file at the length it already had. Of what's owed,
        # which is everything built since the last copy finished, or every
        # category this set has when one before it never did
        owed = hd_mirror.owed(install_dir, source, other)
        ids = self._patcher().hd_has(ui_game_id, source) if owed == hd_mirror.EVERYTHING else owed
        fresh = self._fresh(touched, ids)
        outcome = hd_mirror.copy(install_dir, undo_store_for(install_dir), source, other,
                                 on_line=line, on_step=stage, on_share=within, cancel=cancel,
                                 fresh=fresh)
        try:
            self._patcher().record_hd_copied(ui_game_id, source, other, outcome,
                                             checked=ids if fresh is not None else None)
        except Exception:
            if self._log:
                self._log.exception("couldn't record what went into the other voice set")
        return outcome

    @staticmethod
    def _fresh(touched, ids):
        """
        what a copy reads in full, the rest taken on sizes: `touched(ids)`, or
        None for all of it when either is unknown
        """
        if touched is None or not ids:
            return None
        try:
            return touched(ids)
        except Exception:
            return None

    def _owe_copy(self, ui_game_id: str, install_dir: Path, source: str, categories) -> None:
        """
        the other voice set noted as behind `source` on `categories`, when there
        is one. Twice over: the copy's memo is what it reads in full and goes
        when a copy finishes, and the record's is what the tools build when
        that copy has to leave a pass out, so neither wipes the other
        """
        from patcher.mods import hd_mirror
        from patcher.mods.hd_install import voice_sets

        other = "c" if source == "u" else "u"
        try:
            if other in voice_sets(install_dir, whole=True):
                hd_mirror.owe(install_dir, source, other, categories)
                self._patcher().owe_hd(ui_game_id, other, categories)
        except Exception:
            if self._log:
                self._log.exception("couldn't note the other voice set as behind")

    @staticmethod
    def _no_other_set(install_dir: Path) -> str:
        """why the voices box on its own has nowhere to copy to, or "" """
        from patcher.mods.hd_install import find_variant, voice_sets
        from patcher.mods.hd_mirror import LANGUAGE

        other = "c" if find_variant(install_dir) == "u" else "u"
        if other in voice_sets(install_dir, whole=True):
            return ""
        return (f"the {LANGUAGE[other]} voice files aren't all there, so there's nothing to put "
                "the HD Project in.")

    def _running(self, ui_game_id: str) -> str:
        """the game running out of its folder, as the refusal, or "" """
        try:
            return self._patcher().game_running(str(ui_game_id)) if ui_game_id else ""
        except Exception:
            return ""

    def cancel_hd_project(self) -> dict:
        """
        asks the running install to stop. A tool working in the mod folder is
        stopped where it is; a zone already going into the game finishes first.
        """
        self._hd_cancel.set()
        return {"ok": True}

    def _settle_hd_record(self, ui_game_id: str, mod_root: str, job: str = "hdproject",
                          variant: str | None = None) -> None:
        """
        the record, made to agree with what the archives say is in them. The
        voices box's build writes the set the game doesn't load, which is the
        one to read when that stopped partway.
        """
        try:
            if variant is not None:
                answer = self._patcher().hd_set_state(ui_game_id, job, str(mod_root), variant)
            else:
                answer = self._patcher().hd_survey(ui_game_id, job, str(mod_root))
            if answer.get("verified"):
                self._patcher().record_hd_installed(ui_game_id, job,
                                                    answer.get("applied") or [], built=[],
                                                    variant=answer.get("variant"))
        except Exception:
            if self._log:
                self._log.exception("could not settle the HD record")

    def _patches_first(self, ui_game_id: str, first, cancel, line, landed: list) -> None:
        """
        whichever of `first` isn't on the game, applied ahead of the mod and said
        in its terminal. A fix that won't go in is said and left to the mod's
        own checks, which refuse what HD can't run without.
        """
        from patcher.engine import PatchStatus
        from patcher.mods.hd_runner import Cancelled

        try:
            missing = self._patcher().missing_jobs(ui_game_id, first)
        except Exception as e:
            line(f"[patches] couldn't check which fixes are on the game ({e})", "err")
            return
        if not missing:
            return
        names = list(missing.values())
        said = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        line(f"[patches] {said} "
             + ("isn't on the game yet, so it goes" if len(names) == 1
                else "aren't on the game yet, so they go")
             + " in before the mod")
        went = []

        def heard(event):
            if event.get("type") == "job" and event.get("state") == "done":
                job = event.get("jobId")
                name, why = missing.get(job, job), event.get("detail") or ""
                if event.get("ok"):
                    went.append(job)
                    # back on after a restore took it off is no change at all
                    off = f"the removal of {name}"
                    if off in landed:
                        landed.remove(off)
                    else:
                        landed.append(name)
                    line(f"[patches] {name}: {why or 'done'}")
                elif event.get("status") == PatchStatus.UNSAFE_BUILD:
                    # the engine's call about this exe, not a failure. XIII's DRM
                    # refusing the 4GB patch makes it on every install, and red
                    # every time lit the minimized terminal's alert for nothing
                    line(f"[patches] {name} stays off: " + (why or "this exe can't take it"))
                else:
                    line(f"[patches] {name} didn't go in: {why or 'no reason given'}", "err")
            elif event.get("type") == "finished" and event.get("error") and not cancel.is_set():
                line(f"[patches] {event['error']}", "err")

        try:
            self._patcher().apply(ui_game_id, list(missing), on_event=heard, cancel=cancel)
        except Exception as e:
            if self._log:
                self._log.exception("applying the fixes ahead of the mod failed")
            line(f"[patches] the fixes stopped partway ({e})", "err")
        if went:
            self._emit({"type": "hd", "stage": "patched", "gameId": ui_game_id, "applied": went})
        if cancel.is_set():
            raise Cancelled("stopped before the mod went in")

    def _restored(self, ui_game_id: str, install_dir, put_back, line, landed=None,
                  again=()) -> list:
        """
        what a restore of the HD backup took off besides the HD install: a
        patch applied since the backup was taken was in those files, and the
        copies that went back are older. Said, forgotten, and added to
        `landed`. Returns their labels. `again` is the jobs this run puts back
        on before its mod.
        """
        labels = []
        try:
            settled = self._patcher().settle_injections(ui_game_id, install_dir, put_back)
        except Exception as e:
            # bookkeeping after files already moved, so it must not turn a
            # restore into a crash. Revert sorts the record out from the disk.
            if self._log:
                self._log.exception("could not settle the patches a restore took off")
            line(f"[restore] could not check which patches came off with it ({e})", "err")
            return labels
        for off in settled:
            labels.append(off["label"])
            if off["ok"]:
                # one that's left the patcher has nothing to go back on with
                then = ("" if off.get("retired") else
                        " It goes back on before the mod." if off.get("jobId") in again
                        else " Apply it again to have it back.")
                line(f"[restore] {off['label']} came off with them, since the copies that "
                     f"went back were without it.{then}")
            else:
                line(f"[restore] {off['label']} {off['detail']}", "err")
            if landed is not None:
                landed.append(f"the removal of {off['label']}" if off["ok"] else
                              f"the removal of {off['label']} from the archives that went back")
        return labels

    def restore_hd_project(self, ui_game_id: str) -> dict:
        """
        puts the archives back from the copy taken before the first install.

        ours, not the mod's. Its own uninstall restores from a cache inside the
        mod folder that its docs call disposable, and repopulates that cache
        from the live game archive when it is missing, so on a modded install
        it hands the mod straight back.
        """
        from patcher.mods.hd_install import (InstallError, RestoreIncomplete, restore,
                                             restore_prep)
        from patcher.paths import undo_store_for

        if self._hd_running:
            return {"ok": False, "error": "an install is running, so the archives are in use"}
        if self._applying == ui_game_id:
            return {"ok": False, "error": "a patch run is going on this game, so the archives "
                                         "may be in use"}
        running = self._running(ui_game_id)
        if running:
            return {"ok": False, "error": running}
        resolved = self._patcher().scan(ui_game_id)
        if not resolved.get("found") or not resolved.get("path"):
            return {"ok": False, "error": resolved.get("error") or "couldn't find the game"}

        install_dir = Path(resolved["path"])

        def say(text, stream="out"):
            self._emit({"type": "hd", "stage": "line", "text": text, "stream": stream})

        from patcher.mods.game_files import lay_back, lift

        def back_on(lifted):
            # the backup has none of what went in after it: those go back on
            # top of the vanilla files, where they were on top of HD
            names = lay_back(install_dir, lifted, on_line=lambda t: say(f"[restore] {t}", "note"))
            if names:
                say(f"[restore] {_listed(names)} went back on top, the way "
                    f"{'it was' if len(names) == 1 else 'they were'}.", "note")

        # the drawer's mods as they are, to go back on after
        lifted = lift(install_dir)
        try:
            prep = restore_prep(install_dir, undo_store_for(install_dir))
            put_back = restore(prep, on_line=say)
        except RestoreIncomplete as e:
            self._restored(ui_game_id, install_dir, e.restored, say)
            self._settle_lifted(install_dir, lifted, say)
            return {"ok": False, "error": str(e), "restored": [p.name for p in e.restored]}
        except (InstallError, OSError) as e:
            lay_back(install_dir, lifted)
            return {"ok": False, "error": str(e)}

        off = self._restored(ui_game_id, install_dir, put_back, say)
        self._patcher().hd_reverted(ui_game_id)
        if put_back:
            from patcher.mods.game_files import settled
            from patcher.mods.hd_install import modded_before
            from patcher.mods.hd_mirror import reverted
            reverted(install_dir)
            still = modded_before(install_dir)
            if still:
                say(f"[restore] {still}", "note")
            try:
                for text in settled(install_dir, keep=_names(lifted)):
                    say(f"[restore] {text}", "note")
            except Exception:
                # the restore landed whatever this says, and the next one
                # or an HD install settles the drawer's mods again
                if self._log:
                    self._log.exception("settling the drawer's mods after a restore")
        try:
            back_on(lifted)
        except Exception:
            if self._log:
                self._log.exception("putting the drawer's mods back after a restore")
        return {"ok": True, "restored": [p.name for p in put_back], "cameOff": off}

    def _settle_lifted(self, install_dir: Path, lifted, say) -> None:
        """a restore cut short: what it took out of the drawer goes back on what's there"""
        from patcher.mods.game_files import lay_back, settled
        try:
            for text in settled(install_dir, keep=_names(lifted)):
                say(f"[restore] {text}", "note")
            lay_back(install_dir, lifted)
        except Exception:
            if self._log:
                self._log.exception("putting the drawer's mods back after a restore")

    def begin_acquire(self, job_id: str, page_url: str, drawer: bool = False,
                      game: str | None = None) -> dict:
        """
        starts getting a file for one job: opens the mod page and watches
        Downloads. Returns immediately; progress arrives as "acquire" events,
        and a drop or the picker can beat it. `drawer` is the Mods drawer's Open
        page, whose file goes through the drawer like a drop. One watch per game
        and job, since XIII-2 and LR both have a dlcrestore.
        """
        from patcher.acquire import Acquisition
        from patcher.download_watch import INSTALLER_SUFFIXES, DownloadWatcher, downloads_folder

        game = str(game or self._current_game or "")
        key = (game, str(job_id))
        if key in self._acquiring:
            return {"ok": True, "already": True}

        acquisition = Acquisition(
            job_id=str(job_id), page_url=str(page_url or ""),
            emit=lambda event: self._emit({**event.as_dict(), "game": game}),
        )
        self._acquiring[key] = acquisition
        try:
            limits = self._patcher().download_filter(game, str(job_id))
            # the drawer takes the HD Project as an archive, never an installer
            patterns = [s for s in limits["suffixes"]
                        if not (drawer and s in INSTALLER_SUFFIXES)]
            watcher = DownloadWatcher(folder=downloads_folder(), patterns=patterns,
                                      min_bytes=limits["minBytes"],
                                      names=limits.get("names") or (),
                                      nexus=limits.get("nexus"),
                                      skip=(limits.get("skip") or ()) if drawer else ())
            watcher.start()
            acquisition.sizes = dict(limits.get("sizes") or {})
            # a row that's several files keeps watching after the first, which
            # only the drawer takes: Apply's install is of one file
            acquisition.want = int(limits.get("want") or 1) if drawer else 1
            # last, so a watch that couldn't start leaves the page to the
            # caller, which opens it once
            if page_url and str(page_url).startswith(self.MOD_HOSTS):
                webbrowser.open(str(page_url), new=2)
        except Exception as e:
            # with no thread behind it, a job left here answers "already" forever
            self._acquiring.pop(key, None)
            if self._log:
                self._log.exception("couldn't start watching for %s", job_id)
            return {"ok": False, "error": f"Couldn't watch Downloads for it: {e}"}

        def wait():
            try:
                # off Nexus itself when a key's been saved, which is the only
                # way a download's percentage is known for most rows. On this
                # thread, since it's the network, and the watch's baseline was
                # taken before the page opened
                try:
                    acquisition.sizes.update(self._patcher().nexus_sizes(game, str(job_id)))
                except Exception:
                    pass
                found = acquisition.run(poll=watcher.poll, interval=0.75,
                                        cancel=self._arm_side(), partial=watcher.writing)
                if found is not None and not drawer:
                    self._install_supplied(str(job_id), found)
            except Exception as e:
                if self._log:
                    self._log.exception("acquisition failed for %s", job_id)
                # the end of this watch, so Open page can start another
                self._emit({"type": "acquire", "stage": "expired", "jobId": str(job_id),
                            "game": game, "error": str(e),
                            "prompt": "Couldn't watch for the download. Use Choose file."})
            finally:
                # a Stop and another Open page while this one wound down is
                # that one's watch, and stays
                if self._acquiring.get(key) is acquisition:
                    self._acquiring.pop(key, None)

        threading.Thread(target=wait, daemon=True).start()
        return {"ok": True, "started": True}

    def _install_supplied(self, job_id: str, path) -> dict:
        running = self._running(self._current_game or "")
        result = {"ok": False, "error": running} if running else self._patcher().install_dropped(
            self._current_game or "", str(path), job_id=job_id or None,
            cancel=self._arm_side())
        self._emit({"type": "acquire", "stage": "installed", "jobId": job_id,
                    **{k: v for k, v in result.items() if k != "jobId"}})
        return result

    def cancel_acquire(self, job_id: str, game: str | None = None) -> dict:
        key = (str(game or self._current_game or ""), str(job_id))
        acquisition = self._acquiring.pop(key, None)
        if acquisition is not None:
            acquisition.cancel()
        return {"ok": True}

    def open_addons(self, ui_game_id: str) -> dict:
        """
        pops open NovaFix's addons folder in Explorer.

        paired with opening the mod page: the download lands in one window and
        the folder it belongs in is already open next to it, so installing a
        DLL mod is a drag rather than a navigation exercise.
        """
        try:
            result = self._patcher().addons_dir(str(ui_game_id))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not result.get("ok"):
            return result
        try:
            if sys.platform == "win32":
                os.startfile(result["path"])
            else:
                subprocess.Popen(["xdg-open", result["path"]])
        except Exception as e:
            return {"ok": False, "error": str(e), "path": result["path"]}
        return result

    def install_addon(self, ui_game_id: str, file_path: str) -> dict:
        running = self._running(ui_game_id)
        if running:
            return {"ok": False, "error": running}
        try:
            return self._patcher().install_addon(str(ui_game_id), str(file_path))
        except Exception as e:
            if self._log:
                self._log.exception("addon install failed")
            return {"ok": False, "error": str(e)}

    def list_addons(self, ui_game_id: str) -> dict:
        try:
            # the preset rename writes the state file, which Apply and Remove
            # take turns on through this lock
            return self._patcher().list_addons(str(ui_game_id), rename=not self._busy.locked())
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def remove_addon(self, ui_game_id: str, mod_name: str, kind: str | None = None) -> dict:
        # a drop going in can be LR's patchers taking one out from under
        # another and putting it back, or putting DLC files in ahead of records
        if kind == "files" and (self._hd_running or self._applying == ui_game_id
                                or self._installing_mod):
            return {"ok": False, "error": "something is running on this game's archives, so "
                                          "take it off once that's done."}
        running = self._running(ui_game_id)
        if running:
            return {"ok": False, "error": running}
        # one of Apply's own comes off by reverting it, which rewrites the
        # state file Apply is writing. So the two take turns, on the same lock
        held = kind == "patch"
        if held and not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "Apply or a sign-in is going, so take it off once "
                                          "that's done."}
        try:
            return self._patcher().remove_addon(str(ui_game_id), str(mod_name),
                                                str(kind) if kind else None)
        except Exception as e:
            if self._log:
                self._log.exception("removing a mod failed")
            return {"ok": False, "error": str(e)}
        finally:
            if held:
                self._busy.release()

    def install_mod(self, ui_game_id: str, file_path: str, origin: str | None = None,
                    version: str | None = None) -> dict:
        """
        whatever was dropped on the Mods drawer, put where it goes. The webview
        already runs this off the window's thread; a big mod says how far it's
        got through "mods" events while it works.
        """
        # a version picked in the drawer stays up there to be pressed again,
        # so that's what to do rather than drop it
        again = "Press Install again" if version else "Drop it again"
        if self._hd_running:
            return {"ok": False, "error": f"an HD install is running on the archives. {again} "
                                          "once that's finished."}
        if self._applying == ui_game_id:
            return {"ok": False, "error": f"a patch run is going on this game. {again} once "
                                          "that's finished."}
        running = self._running(ui_game_id)
        if running:
            return {"ok": False, "error": running}
        if self._unpacking:
            return {"ok": False, "busy": True,
                    "error": "still unpacking the last archive. "
                             f"{again if version else 'Drop this one again'} once it has finished."}
        if self._installing_mod:
            return {"ok": False, "busy": True,
                    "error": MOD_GOING_IN.replace("Drop this one again", again) if version
                    else MOD_GOING_IN}
        self._installing_mod = True
        try:
            return self._patcher().install_mod(
                str(ui_game_id), str(file_path), origin=str(origin) if origin else None,
                version=str(version) if version else None,
                on_line=lambda text: self._emit({"type": "mods", "text": text}),
                # which job it is, since a drop going in unpacks as well
                on_phase=lambda **said: self._emit({"type": "mods", "job": "install", **said}))
        except Exception as e:
            if self._log:
                self._log.exception("installing a mod failed")
            return {"ok": False, "error": str(e)}
        finally:
            self._installing_mod = False

    def novafix_settings(self, ui_game_id: str) -> dict:
        from patcher import voices

        game_id = str(ui_game_id)
        installing = self._hd_running and self._hd_game == game_id
        try:
            state = self._patcher().novafix_settings(game_id, gaps=not installing)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if self._hd_running and self._hd_game == game_id:
            for row in state.get("settings") or ():
                if row.get("section") == voices.SECTION and not row.get("unavailable"):
                    row.update(unavailable="After the Install", note=VOICES_MID_INSTALL, warn={})
        return state

    def save_novafix_settings(self, ui_game_id: str, changes) -> dict:
        """
        the drawer's Apply. The voices are the launcher's own setting and
        nothing more: a set without the HD Project in it gets a note saying so,
        and the HD panel's voices box is where it goes in, never a switch.
        """
        from patcher import voices

        game_id, changes = str(ui_game_id), dict(changes or {})
        language = (changes.get(voices.SECTION) or {}).get(voices.KEY)
        # under the gate an install starts behind, so one can't pick its voice
        # set out of setup.xml between this check and the write
        with self._gate:
            if self._hd_running and self._hd_game == game_id and language:
                return {"ok": False, "error": VOICES_MID_INSTALL}
            try:
                return self._patcher().save_novafix_settings(game_id, changes)
            except Exception as e:
                return {"ok": False, "error": str(e)}

    def launch_game(self, ui_game_id: str) -> dict:
        """
        starts the game. patcher.launch owns the reasoning about which route.
        """
        from patcher import launch

        game_id = str(ui_game_id)
        # the row can offer Launch mid-install now that an install puts the
        # required fixes on, and a game holding its archives open fails the swap
        if self._hd_running and self._hd_game == game_id:
            return {"ok": False, "error": "the HD install is rewriting this game's archives. "
                                          "Launch once it has finished."}
        patcher = self._patcher()
        try:
            app_id = patcher.steam_app_id(game_id)
            target = patcher.game_executable(game_id)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        # the preset's new name is in the game the first time it opens ReShade,
        # and a dll src_38 renamed is its author's again before the game loads it
        try:
            if not self._busy.locked():
                patcher.rename_presets(game_id)
                patcher.unname_loaders(game_id)
                patcher.depth_off(game_id)
        except Exception:
            if self._log:
                self._log.exception("couldn't settle the game's files before launching")

        try:
            # a laptop's dedicated GPU, not the one Windows hands an unknown
            # 32-bit game, unless somebody's picked already
            from patcher import gpu_preference
            from patcher.paths import undo_store_for

            install_dir = patcher.install_dir(game_id)
            if target is not None and install_dir is not None and \
                    gpu_preference.prefer(target, undo_store_for(install_dir)) and self._log:
                self._log.info("%s set to the high performance GPU in Windows' graphics "
                               "settings", target)
        except Exception:
            if self._log:
                self._log.exception("couldn't set the game's GPU preference")

        plan = launch.plan(app_id, target, windows=sys.platform == "win32")

        if plan.route == launch.STEAM:
            self._silence_launcher(game_id)
            try:
                os.startfile(f"steam://rungameid/{plan.app_id}")     # noqa: S606
            except OSError as e:
                return {"ok": False, "error": f"couldn't reach Steam: {e}"}
            if self._log:
                self._log.info("launched %s through steam (%s)", plan.app_id, plan.why)
            self._verify_launch(game_id, target)
            return {"ok": True, "via": "steam", "appId": plan.app_id}

        if plan.executable is None:
            return {"ok": False, "error": "couldn't find the game executable"}
        try:
            # cwd is the executable's own folder because these games resolve
            # their data paths relative to it
            subprocess.Popen([str(plan.executable)], cwd=str(plan.executable.parent))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if self._log:
            self._log.info("launched %s directly (%s)", plan.executable, plan.why)
        return {"ok": True, "via": "direct", "path": str(plan.executable)}

    def _verify_launch(self, game_id: str, executable) -> None:
        """
        watches the launch we just started and takes the 4GB patch back off if
        the DRM refuses it.

        Nothing on disk can say whether the cover actually beats XIII's CEG
        check on this machine, so the launch answers it. A refusal takes the
        patch off and records it so the next Apply doesn't walk into it. Every
        launch is watched: a crash Windows logged in a dll gets named with
        whose dll it is and never blamed on the patch, and a game that still
        won't start with the patch off clears any refusal on record.

        Daemon thread, fire and forget. A launch Steam accepted must never be
        held up by this.
        """
        if sys.platform != "win32":
            return
        try:
            patch_id, manifest, install_dir = self._laa_context(game_id)
        except Exception:
            if self._log:
                self._log.exception("couldn't set up the launch check for %s", game_id)
            return
        if manifest is None or install_dir is None or executable is None:
            return

        # a refusal is only ever filed while the patch is on. otherwise a user
        # who quits at the title screen hands us a "died fast" that gets
        # recorded against a patch that was never applied, and the guard then
        # refuses it forever on an install that would have taken it fine.
        laa_on = False
        if patch_id is not None:
            try:
                from patcher.patch_ops import is_laa_enabled

                laa_on = is_laa_enabled(Path(executable).read_bytes())
            except (OSError, ValueError):
                return

        name = Path(executable).name

        # the shim's log before we start, so afterwards we can tell whether it
        # actually ran. a DRM refusal and a shim the loader would not take look
        # identical from outside and want the same repair, but they are very
        # different bug reports.
        from patcher.engine import shim_log_path

        log_path = shim_log_path(install_dir, manifest)
        try:
            log_before = log_path.stat().st_size if log_path and log_path.is_file() else -1
        except OSError:
            log_before = -1

        def run():
            from patcher import launch_watch
            from patcher.paths import state_path_for

            seen = launch_watch.watch(name)
            verdict = seen.verdict
            try:
                after = log_path.stat().st_size if log_path and log_path.is_file() else -1
            except OSError:
                after = -1
            spoke = after > log_before
            if self._log:
                self._log.info("launch check for %s: %s after %.1fs, exit %s, crash %s "
                               "(4GB on: %s, shim logged: %s)", name, verdict, seen.ran,
                               "none" if seen.code is None else f"{seen.code:#010x}",
                               seen.crash or "none logged", laa_on, spoke)
            if verdict in (launch_watch.OK, launch_watch.UNKNOWN):
                # OK and UNKNOWN both mean leave it alone. tearing a working
                # patch off because a machine was slower than the watch, or
                # because somebody cancelled at Steam's prompt, would be a
                # worse bug than the one this is here to catch.
                return
            # XIII's shim is the cover, and a crash in it is the cover failing,
            # which wants the same repair as the DRM refusing what it served
            if verdict == launch_watch.CRASHED and laa_on and \
                    str(seen.crash.get("module", "")).lower() == "dinput8.dll":
                from patcher.engine import COVER_SHIM, current_cover

                if current_cover(install_dir, manifest) == COVER_SHIM:
                    verdict = launch_watch.DRM_ERROR
            if verdict != launch_watch.DRM_ERROR or not laa_on:
                self._launch_failed(game_id, manifest, None if laa_on else patch_id, seen)
                return
            from patcher.state import record_launch

            # the record first, before anything that can fail, since it's what
            # makes the next Apply repair the install. Filed against the cover
            # in place, so a new cover earns its own answer.
            try:
                from patcher.engine import current_cover

                cover = current_cover(install_dir, manifest)
                # the same repair either way, and worth telling apart in the
                # record: a shim that never logged is one the loader refused,
                # which is a different bug from the DRM refusing what it served.
                # LR has no shim at all, so there is no log to have stayed
                # silent and claiming one did would be the wrong bug report.
                why = ("Steam refused the patched executable"
                       if spoke or log_path is None else
                       "the game would not start and the shim never logged, so it "
                       "did not load")
                record_launch(state_path_for(manifest.game_id), patch_id,
                              launch_watch.DRM_ERROR, why, cover,
                              evidence=launch_watch.evidence(seen))
                if self._log:
                    self._log.info("recorded DRM refusal against cover=%s (%s)", cover, why)
            except OSError:
                if self._log:
                    self._log.exception("couldn't record the launch verdict")

            # Steam's error box is modal and holds the refused process open,
            # the process holds the image mapped, and Windows will not replace
            # a mapped image. The first real run of this died right here on
            # WinError 5. So wait for the lock rather than race it.
            self._emit({"type": "launchCheck", "verdict": launch_watch.DRM_ERROR,
                        "gameId": game_id, "undone": False, "stage": "waiting",
                        "detail": "Steam wouldn't start the game with the 4GB patch on. "
                                  "Close the Steam error box and this will undo it."})
            freed = launch_watch.wait_until_replaceable(executable)

            stuck = None
            try:
                from patcher.engine import (PatchStatus, strip_dinput8_shim,
                                            strip_redirect_addon, undo_laa_patch)

                result = undo_laa_patch(install_dir, manifest,
                                        "the launch check saw Steam refuse it")
                undone = result is not None and result.status == PatchStatus.REPAIRED
                # the shim comes off with the patch, always. it is a static
                # import of the game, so leaving one behind after undoing the
                # thing it exists for is a second way to not start, and saying
                # "removed" about a file still sitting there is worse than
                # saying nothing.
                try:
                    strip_dinput8_shim(install_dir, manifest)
                    strip_redirect_addon(install_dir, manifest)
                except OSError as e:
                    stuck = getattr(e, "path", None) or str(e)
                    if self._log:
                        self._log.warning("left behind after the undo: %s", stuck)
            except Exception:
                undone = False
                if self._log:
                    self._log.exception("couldn't undo the 4GB patch after a DRM refusal")
            if self._log:
                self._log.info("undo after DRM refusal: lock freed=%s undone=%s", freed, undone)
            if stuck:
                detail = (f"Steam wouldn't start the game with the 4GB patch on. The patch "
                          f"is off, but {Path(str(stuck)).name} couldn't be removed. Close "
                          "the game and Steam, then press Apply.")
            elif undone:
                detail = ("Steam wouldn't start the game with the 4GB patch on, so it's "
                          "been removed. Launch again and it should run.")
            else:
                detail = ("Steam wouldn't start the game with the 4GB patch on. The game "
                          "was still holding the file, so press Apply once the error box "
                          "is closed and it will put the original back.")
            self._emit({"type": "launchCheck", "verdict": launch_watch.DRM_ERROR,
                        "gameId": game_id, "undone": undone and not stuck,
                        "stage": "done", "detail": detail})

        threading.Thread(target=run, daemon=True, name="launch-check").start()

    def _launch_failed(self, game_id: str, manifest, patch_id, seen) -> None:
        """
        says a launch that died fast, with what Windows logged and whose dll
        that is. `patch_id` is the 4GB patch when it was off for this launch,
        and a refusal of it on record is forgotten: a game that won't start
        without it wasn't refused for it
        """
        from patcher import launch_watch
        from patcher.paths import state_path_for
        from patcher.state import clear_launch_record, load_state

        cleared = False
        if patch_id is not None:
            try:
                path = state_path_for(manifest.game_id)
                entry = (load_state(path).get("launch_checks") or {}).get(patch_id)
                if isinstance(entry, dict) and entry.get("verdict") == launch_watch.DRM_ERROR:
                    clear_launch_record(path, patch_id)
                    cleared = True
            except (OSError, AttributeError):
                if self._log:
                    self._log.exception("couldn't clear the 4GB refusal on record")
        crash = seen.crash or {}
        owner = {}
        if crash:
            try:
                owner = self._patcher().module_owner(game_id, crash.get("path")
                                                     or crash.get("module", ""))
            except Exception:
                if self._log:
                    self._log.exception("couldn't say whose %s is", crash.get("module"))
        detail = launch_watch.said(manifest.display_name, seen, owner.get("name", ""),
                                   owner.get("drawer", False))
        if cleared:
            detail += (" It wouldn't start with the 4GB patch off either, so the patch wasn't "
                       "what stopped it, and it goes back on with the next Apply.")
        self._emit({"type": "launchCheck", "verdict": seen.verdict, "gameId": game_id,
                    "undone": False, "stage": "done", "detail": detail, "crash": crash,
                    "owner": owner.get("name", ""), "cleared": cleared})

    def _laa_context(self, game_id: str):
        """(patch id, manifest, install dir) for this game's 4GB patch, or Nones"""
        patcher = self._patcher()
        manifest = patcher.manifest_for(game_id)
        install_dir = patcher.install_dir(game_id)
        if manifest is None or install_dir is None:
            return None, None, None
        # an LAA guard is what marks the patch a launch can refuse, so it is
        # also what says this game needs watching. narrower than "any guard":
        # other guards exist and answer other questions.
        from patcher.engine import laa_patch

        patch = laa_patch(manifest)
        if patch is None:
            return None, manifest, install_dir
        return patch.id, manifest, install_dir

    def _silence_launcher(self, game_id: str) -> None:
        """
        ticks "Do not show this window again" while NovaFix owns the graphics,
        plus NovaFix's menu keys and add-on popup, the same once-only way.
        launcher_skip.follow decides. Best effort: a failed config edit goes to
        the log and the game still starts.
        """
        try:
            self._patcher().follow_launcher(game_id)
            self._patcher().settle_novafix(game_id)
        except Exception:
            if self._log:
                self._log.exception("couldn't settle the launcher for %s", game_id)

    def pick_file(self) -> dict:
        """
        the escape hatch that always works. a native picker rather than a
        drop, because someone who saved the file to their mods folder or the
        desktop can browse to it, and someone whose drag-and-drop does not
        expose a real path has no other route at all.

        {ok, path} or {ok: False, cancelled|error}. See _dialog().
        """
        return _dialog(self._window, _open_dialog(), self._log,
                       ("Mod archives (*.zip;*.7z;*.rar;*.tar.gz;*.tgz)",
                            "All files (*.*)"))

    def pick_folder(self) -> dict:
        """
        for archives Python cannot open. Leviathan's Tears ships as a .rar,
        and when bsdtar cannot read it the shortest honest path is: extract it
        with whatever Windows or 7-Zip gives you, then point us at the folder.
        scan() treats a folder exactly like an archive.
        """
        answer = _dialog(self._window, _folder_dialog(), self._log)
        if answer.get("empty"):
            # the reflection hack behind this one returns an empty tuple more
            # often than it should, and there is a working route to point at
            answer["error"] = ("The folder picker didn't hand back a folder, which it "
                               "sometimes does. Use Choose a file and pick any file inside "
                               "the mod folder instead.")
        return answer

    def pick_install_dir(self, ui_game_id: str) -> dict:
        """
        opens the folder picker and points a game at whatever comes back.

        The not-found message has always said to browse for the game folder,
        and until now there was nothing in the window that could. Anyone whose
        Steam layout we cannot read was simply stuck.
        """
        answer = self.pick_folder()
        if not answer.get("ok"):
            return {"ok": False, "cancelled": bool(answer.get("cancelled")),
                    "error": answer.get("error")}
        return self._patcher().set_install_dir(ui_game_id, answer["path"])

    def supply_file(self, job_id: str, file_path: str) -> dict:
        """
        installs a file the user supplied, by drop or by picker.

        if a wait is running for this job, satisfying it here ends the folder
        watcher too, so the two routes cannot both install the same mod.
        """
        try:
            running = self._running(self._current_game or "")
            if running:
                return {"ok": False, "error": running}
            waiting = self._acquiring.get((self._current_game or "", str(job_id)))
            if waiting is not None:
                waiting.offer(Path(str(file_path)), source="picker")
            return self._patcher().install_dropped(
                self._current_game or "", str(file_path), job_id=str(job_id) or None,
                cancel=self._arm_side())
        except Exception as e:
            if self._log:
                self._log.exception("supplied file failed")
            return {"ok": False, "error": str(e)}

    def nexus_sign_out(self) -> dict:
        try:
            return self._patcher().nexus_sign_out()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # hosts a mod page can legitimately live on. same reasoning as ALLOWED
    # above: the page is local and trusted, but the bridge is reachable from
    # anything running in it, so the shell does not open arbitrary strings.
    # every host a mod page in a shipped manifest actually lives on. patreon
    # is here because XIII-2's console content restore is hosted there and the
    # Mods drawer links it; without it the button silently refused.
    MOD_HOSTS = ("https://www.nexusmods.com/", "https://community.pcgamingwiki.com/",
                 "https://www.patreon.com/")

    def open_pages(self, urls: list) -> dict:
        """
        opens every page a run is still waiting on in one go, so a free Nexus
        account is one tab per mod and a Mod Manager Download each, instead of
        Apply, page, back, Apply again.
        """
        opened = 0
        for url in (urls or []):
            if isinstance(url, str) and url.startswith(self.MOD_HOSTS):
                webbrowser.open(url, new=2)
                opened += 1
        return {"ok": True, "opened": opened}

    def apply(self, ui_game_id: str, job_ids: list) -> dict:
        """
        kicks off a patch run and returns immediately. js_api calls run on the
        webview's thread, so doing the downloads here would freeze the window
        until Windows offers to kill it. The page hears progress via _emit().
        """
        running = self._running(ui_game_id)
        if running:
            return {"ok": False, "started": False, "error": running}
        with self._gate:
            if self._hd_running and self._hd_game == ui_game_id:
                return {"ok": False, "started": False,
                        "error": "the HD install is rewriting this game's archives. Apply once "
                                 "it has finished."}
            if not self._busy.acquire(blocking=False):
                return {"ok": False, "started": False, "error": "a patch run is already going"}
            self._applying = ui_game_id

        jobs = [str(j) for j in (job_ids or [])]
        self._cancel.clear()
        threading.Thread(target=self._run, args=(ui_game_id, jobs), daemon=True).start()
        return {"ok": True, "started": True, "error": None}

    def cancel(self) -> dict:
        """
        asks the running patch to stop. checked between chunks rather than
        between files, so a cancel during a 6GB download takes effect in about
        a megabyte's worth of time rather than at the end of the download.
        """
        # and every side operation, because the user pressed Stop on whatever
        # they can see running. Not the HD run, which has a Stop of its own.
        self._cancel.set()
        with self._side_lock:
            live = list(self._side_events)
        for event in live:
            event.set()
        return {"ok": True}

    def _run(self, ui_game_id: str, job_ids: list) -> None:
        # never opens a page mid-run. The row says what's needed and the user
        # decides.
        try:
            self._patcher().apply(ui_game_id, job_ids, on_event=self._emit,
                                  cancel=self._cancel)
        except Exception as e:
            if self._log:
                self._log.exception("patch run failed")
            # the page is waiting on a finished event to release its busy
            # state. never leave it spinning because something threw.
            self._emit({"type": "finished", "ok": False, "applied": 0,
                        "failed": len(job_ids), "error": str(e)})
        finally:
            self._applying = None
            self._busy.release()

    def _emit(self, event: dict) -> None:
        """one event for the page, queued: see patcher/pump.py for why nobody waits on it"""
        if self._window is None:
            return
        self._pump.put(event)

    def _deliver(self, payload: str) -> None:
        window = self._window
        if window is None:
            return
        # run_js where there is one (6.0 on). evaluate_js evals an escaped copy
        # of the script, stringifies a result nobody reads, and keeps a callback
        # entry per call for the life of the window: 5.6MB after one install
        run = getattr(window, "run_js", None) or window.evaluate_js
        run(f"window.__patch && window.__patch.receive({payload})")


def show_when_ready(win, extra_delay: float = 0.0, before=None) -> None:
    """the window is created hidden and shown once the page has painted, since
    WebView2 flashes a box in the corner before layout and no CSS fixes it.
    Hard deadline, so a page that never reports ready still gets shown.
    """
    # wait for the PAGE's own signal, not readyState. readyState goes complete
    # as soon as the document's subresources are in, but the character art is
    # added by script after that, and it is the brightest thing on screen. show
    # too early and the window opens dark, then brightens a second later.
    deadline = time.time() + 6.0
    state = "unknown"
    while time.time() < deadline:
        try:
            state = win.evaluate_js(
                "window.__uiReady ? 'ui-ready' : document.readyState")
        except Exception:
            state = "unavailable"      # bridge not up yet, try again
        if state == "ui-ready":
            break
        time.sleep(0.05)
    time.sleep(0.12)                   # let the first real paint land
    if extra_delay > 0:
        print(f"holding the window back a further {extra_delay:.1f}s (--show-delay)")
        time.sleep(extra_delay)
    # the windowed exe has no stdout, so print() goes nowhere, and every poll
    # was a round trip to the UI thread plus a callback pywebview never frees
    if sys.stdout is not None:
        report_diag(win)
    if before is not None:
        before()
    try:
        win.show()
    except Exception as e:
        # never leave an invisible window behind
        print(f"could not show the window: {e}")
        try:
            win.resize(win.width, win.height)   # nudge it into existence
        except Exception:
            pass
    print(f"window shown (page state: {state})")


def install_drop_handler(window, api) -> bool:
    """
    the only way a dropped file's real path reaches this process.

    JS can't have it: File.path is Electron's, and WebView2 never added it
    (WebView2Feedback #501). pywebview 5+ stamps pywebviewFullPath onto each
    file in a drop event subscribed from Python.

    Subscribed on window, not document, and only to drop: pywebview serialises
    the event's nodes deep, so on document every event carried the whole page
    (~360ms plus 6.6MB parsed twice, on every dragover). preventDefault matters
    too, since an uncancelled drop is a navigation that pywebview hands to
    webbrowser.open(), which is how dropping a folder opened the folder.

    Returns whether it subscribed. Without window.dom the Mods panel's pickers
    are the way in and the page says so.
    """
    try:
        from webview.dom import DOMEventHandler
    except Exception:
        if api._log:
            api._log.info("this pywebview has no DOM events, so drops fall back to the pickers")
        return False

    def on_drop(event):
        try:
            transfer = (event or {}).get("dataTransfer") or {}
            files = transfer.get("files") or []
            paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        except Exception:
            paths = []
        # back into the page, where the existing routing decides what it is.
        # one event either way, so a drop that yielded nothing still gets an
        # answer rather than looking like the app ignored it.
        api._emit({"type": "dropped", "paths": paths})

    try:
        window.dom.window.events.drop += DOMEventHandler(on_drop, True, True)
    except Exception as e:
        if api._log:
            api._log.warning("could not subscribe to drops: %s", e)
        return False
    return True


def main() -> None:
    from patcher.access import (attach_parent_console, make_console_unicode_safe,
                                silence_device_errors)
    from patcher.paths import migrate_legacy_data

    cli = is_cli_invocation(sys.argv[1:])
    if cli:
        attach_parent_console()
    make_console_unicode_safe()
    silence_device_errors()
    migrate_legacy_data()

    if cli:
        from main import main as cli_main
        return cli_main()

    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true", help="enable devtools")
    ap.add_argument("--gpu", action="store_true",
                    help="re-enable the aggressive GPU flags")
    ap.add_argument("--no-gpu", action="store_true",
                    help="force software rendering, for comparison")
    ap.add_argument("--show-delay", type=float, default=0.0, metavar="SECS",
                    help="hold the window back this much longer after the page "
                         "is ready. a diagnostic: if the artefact still appears "
                         "with this set high, it is coming from the page, not "
                         "from the window appearing")
    args = ap.parse_args()

    if already_open():
        print("already open: brought that window forward instead")
        return

    mode = "gpu" if args.gpu else "software" if args.no_gpu else "default"
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = browser_args(mode)
    print(f"webview args ({mode}): {os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']}")

    # before pywebview, whose first window loads pythonnet's .NET assemblies.
    # A release zip extracted by Explorer marks every one of them as
    # downloaded, and .NET won't load a marked one
    blocked = []
    if os.name == "nt" and getattr(sys, "frozen", False):
        from patcher.access import unblock
        blocked = unblock(root())
        if blocked:
            print(f"still marked as downloaded: {', '.join(p.name for p in blocked[:5])}")

    # imported here, not at module scope, for two reasons. the env var above
    # has to be set first, and more importantly the nxm:// path returns before
    # this line: handling a download link should not need pywebview, pythonnet
    # or a working WebView2 install, and at module scope it did.
    import webview

    # deliberately NOT a hard gate, see webview2_missing()'s docstring. if the
    # guess is wrong we would be refusing to start for no reason.
    if webview2_missing():
        print("note: could not find the WebView2 runtime in the registry. "
              "trying anyway.")

    base = root()
    check_assets(base)
    port = serve(base)
    from patcher import display
    screen = screen_size()
    client_w, client_h = display.opening_size_at(screen, _work_scale)
    corner = display.centered_at((client_w, client_h), screen)
    print(f"window: {client_w}x{client_h}"
          + (f" at {corner[0]},{corner[1]}" if corner else " (placed by the toolkit)"))

    # index.html sits in ui/ and points at ../assets/, so the server has to be
    # rooted at the repo, not at ui/
    opts = dict(
        width=client_w + display.CHROME_W,
        height=client_h + display.CHROME_H,
        # no min_size: hold_aspect owns the floor in real pixels. pywebview 6
        # scales min_size by DPI and 5 doesn't.
        # matches body's #05070a, so there's no seam before the page paints.
        background_color="#05070a",
    )
    if corner:
        # placed rather than left to CenterScreen, which does not centre on the
        # WebView2 backend: it lands near the top left and moves between runs
        opts["x"], opts["y"] = corner
    url = f"http://127.0.0.1:{port}/ui/index.html"

    # claim nxm:// so the Mod Manager Download button reaches us. HKCU only,
    # no elevation, and it steps aside if Vortex or MO2 already owns it.
    try:
        from patcher.nxm_registry import ensure_registered
        state = ensure_registered()
        if state.error:
            print(f"nxm handler: {state.error}")
        elif state.ours:
            print("nxm handler: registered")
        else:
            print(f"nxm handler: left with the existing owner ({state.owner})")
    except Exception as e:
        print(f"nxm handler: skipped ({e})")

    api = Api()
    deferred = True
    try:
        win = webview.create_window(WINDOW_TITLE, url, hidden=True,
                                    js_api=api, **opts)
    except TypeError:
        deferred = False
        win = webview.create_window(WINDOW_TITLE, url, js_api=api, **opts)
        print("note: this pywebview cannot defer showing the window, "
              "so you may see a brief flash on startup")
    api.bind(win)
    # with a console, every page load (reloads under --debug too) prints what
    # it logged before the bridge was up and has it send the rest
    if sys.stdout is not None:
        loaded = getattr(getattr(win, "events", None), "loaded", None)
        if loaded is not None:
            loaded += lambda: report_diag(win)
    # Showing first is not cosmetic ordering. The hook needs a live HWND and
    # the first cut installed it the instant the loop started, while the window
    # was still hidden and .native had nothing on it yet, which is a lock that
    # reports nothing and does nothing. By the time the page has painted there
    # is definitely a window to hook.
    reached_the_loop = threading.Event()

    def ready(w=win, d=args.show_delay):
        reached_the_loop.set()
        # pywebview drops to Internet Explorer's engine, without raising, when
        # WebView2 is missing or older than 86, gui="edgechromium" or not. IE
        # can't parse the page, so that was a dark window that never said why.
        # It's still hidden here, so it goes before anyone sees it
        if os.name == "nt" and getattr(webview, "renderer", "edgechromium") != "edgechromium":
            print(f"renderer: {webview.renderer}, not WebView2")
            complain(WEBVIEW2_MESSAGE)
            w.destroy()
            return
        place = lambda: place_window(w, (client_w, client_h), screen)
        if deferred:
            show_when_ready(w, d, before=place)
        else:
            place()
        if not hold_aspect(w):
            # second best, and the user still never sees margin
            snap_to_aspect(w)
        # after the window and its sizing hook, so neither waits on an import,
        # and before the drop handler, which logs through what this sets up:
        # ahead of it, a drop handler that failed told a log that wasn't there
        api._warm()
        install_drop_handler(w, api)

    try:
        webview.start(
            ready,
            gui="edgechromium",   # be explicit, do not let it fall back to mshtml
            debug=args.debug,     # devtools costs frames, so it is opt in now
            private_mode=False,
            storage_path=cache_dir(),
            # only exists in a source checkout. A built exe carries it as its
            # own icon (ArmyOfOne.spec), which pywebview falls back to
            icon=str(Path(__file__).with_name("app.ico")),
        )
        # start() can return without ever opening anything and without raising:
        # it walks the windows calling _initialize, and if any initialized
        # handler returns False it just returns. That reads as the app exiting
        # to the prompt for no reason, which is the worst thing to debug from a
        # bug report. ready() is only started once start() is past that and
        # about to run the loop, so its absence says the window never existed.
        if not reached_the_loop.is_set() and blocked:
            from patcher.access import BLOCKED_MESSAGE
            complain(BLOCKED_MESSAGE)
        elif not reached_the_loop.is_set():
            complain("The window never opened, and pywebview gave no reason.\n\n"
                     "This is usually a broken WebView2 runtime. Reinstalling the "
                     "Evergreen runtime from\n"
                     "https://developer.microsoft.com/microsoft-edge/webview2/\n"
                     "is the fix.")
    except Exception as e:
        # now that it has actually failed, the registry guess is worth using to
        # pick the more useful of the two messages. A marked DLL first: it fails
        # the same way a missing runtime does, and reinstalling WebView2 won't
        # fix it
        if blocked or "0x80131515" in str(e):
            from patcher.access import BLOCKED_MESSAGE
            complain(BLOCKED_MESSAGE)
        elif webview2_missing():
            complain(WEBVIEW2_MESSAGE)
        else:
            complain(f"Could not open the window:\n{e}\n\nIf your graphics "
                     "drivers are old, try running it with  --no-gpu")


if __name__ == "__main__":
    main()
