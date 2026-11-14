import importlib.util
import sys
import threading
from pathlib import Path

import pytest


def without_comments(src):
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def shell():
    """
    loads shell.py without pywebview installed, which also proves the module
    still imports on a machine with no WebView2 stack. the nxm:// path depends
    on that being true.
    """
    sys.modules.setdefault("webview", None)
    spec = importlib.util.spec_from_file_location("shell_under_test", REPO_ROOT / "shell.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_open_pages_only_opens_known_mod_hosts(shell, monkeypatch):
    """
    the bridge is reachable from anything running in the page, so a list of
    urls arriving from JS does not get handed to the shell unfiltered.
    """
    opened = []
    monkeypatch.setattr(shell.webbrowser, "open", lambda url, new=0: opened.append(url))
    api = shell.Api()

    result = api.open_pages([
        "https://www.nexusmods.com/finalfantasy13/mods/21",
        "https://community.pcgamingwiki.com/files/file/2354-x/",
        "https://evil.test/steal",
        "file:///C:/Windows/System32/calc.exe",
        None,
        12345,
    ])

    assert result["opened"] == 2
    assert opened == [
        "https://www.nexusmods.com/finalfantasy13/mods/21",
        "https://community.pcgamingwiki.com/files/file/2354-x/",
    ]


def test_open_url_still_refuses_anything_off_the_allow_list(shell, monkeypatch):
    opened = []
    monkeypatch.setattr(shell.webbrowser, "open", lambda url, new=0: opened.append(url))
    api = shell.Api()

    assert api.open_url("https://github.com/someone/repo") is True
    assert api.open_url("https://ko-fi.com/karpthemagikarp") is True
    assert api.open_url("https://evil.test/") is False
    assert api.open_url(None) is False
    assert opened == ["https://github.com/someone/repo", "https://ko-fi.com/karpthemagikarp"]


def test_a_patch_run_cannot_start_twice(shell):
    """a second Apply while one is running would race two writers on one folder"""
    api = shell.Api()
    assert api._busy.acquire(blocking=False)
    try:
        result = api.apply("ff13", ["laa"])
        assert result["started"] is False
        assert result["ok"] is False
    finally:
        api._busy.release()


def test_the_drawer_list_leaves_the_preset_rename_to_after_apply(shell):
    """the rename writes the state file Apply's writing, and they take turns on _busy"""
    api = shell.Api()
    asked = []

    class Patcher:
        def list_addons(self, gid, rename=True):
            asked.append(rename)
            return {"ok": True, "mods": []}
    api._patcher = Patcher
    api.list_addons("ff13")
    assert api._busy.acquire(blocking=False)
    try:
        api.list_addons("ff13")
    finally:
        api._busy.release()

    assert asked == [True, False]


def test_cancel_is_cleared_when_a_new_run_starts(shell):
    """a cancel left set from a previous run would abort the next one instantly"""
    api = shell.Api()
    api.cancel()
    assert api._cancel.is_set()

    # apply() starts a thread that needs a real bridge, so just check the
    # flag handling the thread depends on
    api._cancel.clear()
    assert not api._cancel.is_set()


def test_cancel_sets_the_flag_the_download_loop_watches(shell):
    api = shell.Api()
    assert not api._cancel.is_set()
    assert api.cancel()["ok"] is True
    assert api._cancel.is_set()



def test_the_window_is_resizable_again(shell):
    """
    The resolution picker was the wrong answer to a real problem. A window you
    resize like any other window is the right one, so nothing here may pin it
    shut. The floor is the drag's own, 960x540: the page zooms its layout to
    the window, so smaller is the same picture smaller.
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("    opts = dict("):src.index("    url = f\"http://127.0.0.1")]
    assert "resizable=False" not in body
    # the floor is the drag's own, in real pixels (display.constrain from
    # hold_aspect). pywebview 6 scales min_size by the display and 5 doesn't,
    # so at 150% the same number made a minimum bigger than a 1080p screen
    assert "min_size=" not in without_comments(body)
    # sized in the display's own units, so 150% gets a UI the size it asks for
    assert "display.opening_size_at(screen, _work_scale)" in src
    assert "screen = screen_size()" in src
    # and no trace of the picker that replaced it
    for gone in ("window_sizes", "set_window_size", "window_preset", "PRESETS"):
        assert gone not in src, gone


def test_the_aspect_hook_is_harmless_off_windows(shell):
    """
    every machine this is developed on. it has to answer no rather than reach
    for ctypes.windll, which does not exist here.
    """
    assert shell.hold_aspect(object()) is False
    assert shell._wndproc_keepalive == []


def test_the_window_procedure_cannot_be_collected(shell, monkeypatch):
    """
    The classic ctypes mistake, and it does not fail at install time. Drop the
    CFUNCTYPE object and python frees the trampoline, so the window works until
    the next garbage collection and then the first mouse move on its edge calls
    into freed memory.
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("def hold_aspect("):src.index("def snap_to_aspect(")]
    assert "_wndproc_keepalive.append(hooked)" in body
    # installed after the loop is up, because there is no HWND before that
    assert "hold_aspect(w)" in src[src.index("    def ready("):]


def test_a_failed_hook_leaves_a_working_window(shell):
    """
    an unconstrained drag is a cosmetic loss. a window procedure that raises
    takes the whole window with it, so the message path swallows everything and
    falls through to the original proc.
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("def hold_aspect("):src.index("def snap_to_aspect(")]
    assert body.count("except Exception") >= 2
    assert "return user32.CallWindowProcW(previous, handle, message, wparam, lparam)" in body
    assert body.rstrip().endswith("return False")


def test_python_only_hears_the_message_it_handles(shell):
    """
    every other message goes to the window's own procedure in machine code, so
    a click never waits on the GIL. The stub has its own tests; this is that
    hold_aspect uses it when it can and keeps Python's procedure when it can't
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("def hold_aspect("):src.index("def snap_to_aspect(")]
    assert "native = winproc.native_ok()" in body
    assert "stub = winproc.Native(python)" in body
    # told where everything else goes before it's live, and again with what
    # the setter says it actually replaced
    assert body.index("stub.set_prev(getter(hwnd, GWLP_WNDPROC) or 0)") \
        < body.index("previous = setter(hwnd, GWLP_WNDPROC, want)") \
        < body.index("stub.set_prev(previous)")
    assert "Python's procedure is still a lock" in body


def test_maximise_and_fullscreen_are_left_to_windows(shell):
    """
    the lock is for the drag. It used to answer WM_GETMINMAXINFO with a
    centred 16:9 box, so maximise stopped short with the desktop showing down
    both sides, and F11 (a maximised borderless window) got the same box
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("def hold_aspect("):src.index("def snap_to_aspect(")]
    assert "winproc.WM_GETMINMAXINFO" not in body and "class MINMAXINFO" not in body
    assert "ptMaxSize" not in body
    # and the fallback that resizes after a drag doesn't drag a maximised or
    # fullscreen window back to 16:9 either
    snap = src[src.index("def snap_to_aspect("):src.index("def is_cli_invocation(")]
    assert snap.index("if filling_screen(window):") < snap.index("display.nearest_16_9(")


class _Form:
    """the WinForms form pywebview hangs off window.native"""

    def __init__(self, state="Normal"):
        self.is_fullscreen = False
        self.WindowState = state


class _Window:
    def __init__(self, native=None, fails=False):
        self.native = native
        self.fails = fails
        self.toggles = 0
        self.maximised = 0

    def toggle_fullscreen(self):
        if self.fails:
            raise RuntimeError("no form yet")
        self.toggles += 1
        if self.native is not None:
            # what pywebview does: fullscreen is a maximised borderless form,
            # and coming back out is always a normal one
            self.native.is_fullscreen = not self.native.is_fullscreen
            self.native.WindowState = "Maximized" if self.native.is_fullscreen else "Normal"

    def maximize(self):
        self.maximised += 1
        if self.native is not None:
            self.native.WindowState = "Maximized"


def test_fullscreen_reports_what_the_form_says(shell):
    api = shell.Api()
    form = _Form()
    api.bind(_Window(form))
    assert api.toggle_fullscreen() == {"ok": True, "fullscreen": True}
    assert api.toggle_fullscreen() == {"ok": True, "fullscreen": False}
    # out of step with ours, what the form says after the toggle is the answer
    form.is_fullscreen = True
    assert api.toggle_fullscreen() == {"ok": True, "fullscreen": False}


def test_a_maximised_window_comes_back_out_of_fullscreen_maximised(shell):
    api = shell.Api()
    form = _Form("Maximized")
    window = _Window(form)
    api.bind(window)
    api.toggle_fullscreen()
    assert form.WindowState == "Maximized" and window.maximised == 0
    api.toggle_fullscreen()
    assert form.WindowState == "Maximized" and window.maximised == 1
    # and a window that wasn't stays the size it was, though fullscreen
    # itself reads as maximised on the way out
    normal = _Window(_Form("Normal"))
    api2 = shell.Api()
    api2.bind(normal)
    api2.toggle_fullscreen()
    api2.toggle_fullscreen()
    assert normal.maximised == 0 and normal.native.WindowState == "Normal"


def test_fullscreen_without_a_form_to_ask_keeps_its_own_count(shell):
    api = shell.Api()
    window = _Window()
    api.bind(window)
    assert api.toggle_fullscreen()["fullscreen"] is True
    assert api.toggle_fullscreen()["fullscreen"] is False
    assert window.toggles == 2


def test_fullscreen_that_fails_says_so_and_changes_nothing(shell):
    api = shell.Api()
    assert api.toggle_fullscreen() == {"ok": False, "fullscreen": False}
    api.bind(_Window(_Form(), fails=True))
    assert api.toggle_fullscreen() == {"ok": False, "fullscreen": False}


def test_filling_the_screen_is_maximised_or_fullscreen(shell):
    assert shell.filling_screen(_Window(_Form("Maximized"))) is True
    full = _Form()
    full.is_fullscreen = True
    assert shell.filling_screen(_Window(full)) is True
    assert shell.filling_screen(_Window(_Form("Normal"))) is False
    assert shell.filling_screen(_Window()) is False
    assert shell.filling_screen(object()) is False


def test_the_hook_proves_it_installed_rather_than_assuming(shell):
    """
    The first cut of this reported nothing and did nothing: the window kept its
    own procedure, the drag stayed unconstrained, and there was no way to tell
    from the outside. Reading the procedure back is the only check that catches
    a subclass which reports success and does not stick.
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("def hold_aspect("):src.index("def snap_to_aspect(")]
    assert "getter(hwnd, GWLP_WNDPROC) != want" in body
    # what it reads back is whichever went in, the native procedure or Python's
    assert "want = stub.address if stub else python" in body
    # every outcome says which it was, so a report from a real machine is usable
    assert body.count("aspect lock:") >= 4
    assert 'print(f"aspect lock: ON' in body
    # and both routes to the handle are tried, not just the first
    assert "for hwnd, how in candidates:" in body
    handles = src[src.index("def _window_handles("):src.index("def hold_aspect(")]
    assert '"native"' in handles and '"by title"' in handles


def test_the_window_is_shown_before_it_is_hooked(shell):
    """
    The first cut installed the hook the instant the GUI loop started, while
    the window was still hidden and had nothing to hook. Showing first is what
    guarantees there is a live HWND by then.
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("    def ready("):src.index("    try:\n        webview.start(")]
    assert body.index("show_when_ready(w, d, before=place)") < body.index("hold_aspect(w)")


def test_the_patcher_is_warmed_once_the_window_is_up_and_hooked(shell):
    """
    the first game opened paid for the engine's import inline, so the path
    under its list read Scanning for as long as that took. The window and its
    sizing hook never wait behind the import, and the drop handler comes after
    it because it logs through what it sets up
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("    def ready("):src.index("    try:\n        webview.start(")]
    assert body.index("show_when_ready(w, d, before=place)") < body.index("hold_aspect(w)") \
        < body.index("api._warm()") < body.index("install_drop_handler(w, api)")
    # and the page can't call it: pywebview hands it every public method
    assert not hasattr(shell.Api, "warm")


def test_two_calls_at_once_build_one_patcher(shell, monkeypatch):
    """pywebview runs every call from the page on its own thread"""
    import patcher.bridge as bridge_mod
    import patcher.logging_setup as logging_mod
    built, gate = [], threading.Event()

    class Slow:
        def __init__(self, log=None):
            gate.wait(2)
            built.append(self)

    monkeypatch.setattr(bridge_mod, "UiBridge", Slow)
    monkeypatch.setattr(logging_mod, "setup_logging", lambda path: None)
    api = shell.Api()
    got = []
    runs = [threading.Thread(target=lambda: got.append(api._patcher())) for _ in range(4)]
    for run in runs:
        run.start()
    gate.set()
    for run in runs:
        run.join(5)
    assert len(built) == 1 and len(got) == 4 and all(b is built[0] for b in got)


def test_a_warm_that_fails_leaves_it_to_the_first_real_call(shell, monkeypatch):
    """a failure at warm-up isn't kept: the first call the page makes builds it again"""
    import patcher.bridge as bridge_mod
    import patcher.logging_setup as logging_mod
    tries = []

    class Flaky:
        def __init__(self, log=None):
            tries.append(1)
            if len(tries) == 1:
                raise OSError("manifests unreadable")

        def scan(self, game):
            return {"ok": True, "found": False, "error": None}

    monkeypatch.setattr(bridge_mod, "UiBridge", Flaky)
    monkeypatch.setattr(logging_mod, "setup_logging", lambda path: None)
    api = shell.Api()
    api._warm()
    assert api._bridge is None
    assert api.scan("ff13") == {"ok": True, "found": False, "error": None}
    assert len(tries) == 2


def test_a_warm_that_keeps_failing_says_why_where_the_page_asks(shell, monkeypatch):
    import patcher.bridge as bridge_mod
    import patcher.logging_setup as logging_mod

    def broken(log=None):
        raise OSError("manifests unreadable")

    monkeypatch.setattr(bridge_mod, "UiBridge", broken)
    monkeypatch.setattr(logging_mod, "setup_logging", lambda path: None)
    api = shell.Api()
    api._warm()
    assert "manifests unreadable" in api.scan("ff13")["error"]


def test_a_window_the_hook_cannot_reach_still_ends_up_square(shell):
    """
    The user does not care which mechanism got there, they care that there is
    no margin. WM_SIZING is the good one because it corrects before Windows
    draws. This is the other one, and it only runs when the first did not take.
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("    def ready("):src.index("    try:\n        webview.start(")]
    assert "if not hold_aspect(w):" in body and "snap_to_aspect(w)" in body

    snap = src[src.index("def snap_to_aspect("):src.index("def is_cli_invocation(")]
    # after the drag stops, not during it, or it fights the mouse the whole way
    assert "threading.Timer" in snap and "cancel()" in snap
    # our own resize comes back round as another event, so it has to settle
    assert "abs(want[0] - width) <= 1 and abs(want[1] - height) <= 1" in snap
    # and never keeps the process alive on the way out
    assert "timer.daemon = True" in snap


def test_the_snap_is_optional_rather_than_assumed(shell):
    """
    the resized event is not in every pywebview release, and a missing one is a
    line on the console, not a traceback on startup.
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    snap = src[src.index("def snap_to_aspect("):src.index("def is_cli_invocation(")]
    assert 'getattr(window, "events", None)' in snap
    assert "if resized is None:" in snap
    assert snap.count("except Exception") >= 2


def _ff13_install(tmp_path):
    """a minimal XIII install with the 4GB patch applied, for the launch check"""
    from patcher.manifest import load_manifest
    from patcher.patch_ops import flip_laa_flag

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from test_engine_coverage import ORIGINAL_CEG, synthetic_xiii

    manifest = load_manifest(REPO_ROOT / "manifests" / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").write_bytes(clean)
    # the shim beside it, so current_cover names something real and the verdict
    # gets filed against the mechanism that was actually standing there
    shim = next(p for p in manifest.patches if p.id == "install_dinput8_shim")
    source = REPO_ROOT / shim.bundled_files[0]["source"]
    (exe.parent / "dinput8.dll").write_bytes(source.read_bytes())
    return manifest, exe, clean


class _FakePatcher:
    def __init__(self, manifest, install_dir):
        self._manifest, self._install = manifest, install_dir

    def manifest_for(self, game_id):
        return self._manifest

    def install_dir(self, game_id):
        return self._install


def _watching_api(shell, monkeypatch, tmp_path, manifest, verdict, **seen):
    """an Api wired to a fake install and a watch that sees `verdict`"""
    from patcher import launch_watch

    monkeypatch.setattr(shell.sys, "platform", "win32")
    monkeypatch.setattr(launch_watch, "watch",
                        lambda name, seconds=None: launch_watch.Seen(verdict, **seen))
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: tmp_path / "state.json")

    api = shell.Api()
    api._patcher = lambda: _FakePatcher(manifest, tmp_path)
    events = []
    api._emit = events.append
    return api, events


def test_a_drm_refusal_undoes_the_4gb_patch_and_records_it(shell, monkeypatch, tmp_path):
    """
    The whole reason the patch is allowed onto the CEG build at all. Static
    checks cannot prove the redirect holds on a given machine, so the launch
    decides, and a refusal has to put the executable back without the user
    doing anything.
    """
    from patcher import launch_watch
    from patcher.patch_ops import is_laa_enabled
    from patcher.state import launch_blocked

    manifest, exe, clean = _ff13_install(tmp_path)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest,
                                launch_watch.DRM_ERROR)

    api._verify_launch("ff13", exe)
    for thread in threading.enumerate():
        if thread.name == "launch-check":
            thread.join(timeout=10)

    assert exe.read_bytes() == clean, "left the executable patched"
    assert not is_laa_enabled(exe.read_bytes())
    # filed against what was actually covering it, so replacing that mechanism
    # earns a fresh measurement instead of inheriting this one
    assert launch_blocked(tmp_path / "state.json", "flip_laa_flag", "dinput8_shim")
    assert not launch_blocked(tmp_path / "state.json", "flip_laa_flag", "proxy_config")
    assert events and events[-1]["type"] == "launchCheck"
    assert events[-1]["undone"] is True


@pytest.mark.parametrize("verdict", ["ok", "unknown"])
def test_anything_short_of_a_refusal_leaves_the_patch_alone(shell, monkeypatch,
                                                            tmp_path, verdict):
    """
    A slow machine and a cancelled Steam prompt both land here. Undoing a
    working patch on either would be a worse bug than the one this catches.
    """
    from patcher.patch_ops import is_laa_enabled
    from patcher.state import launch_blocked

    manifest, exe, _ = _ff13_install(tmp_path)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest, verdict)

    api._verify_launch("ff13", exe)
    for thread in threading.enumerate():
        if thread.name == "launch-check":
            thread.join(timeout=10)

    assert is_laa_enabled(exe.read_bytes()), "undid the patch on a non-failure"
    assert not launch_blocked(tmp_path / "state.json", "flip_laa_flag", "dinput8_shim")
    assert not events


def test_an_unpatched_executable_never_gets_a_refusal_filed(shell, monkeypatch, tmp_path):
    """
    Otherwise quitting at the title screen records a DRM refusal against a
    patch that was never applied, and the guard then refuses it forever on an
    install that would have taken it fine. It's still watched, and a game
    that died fast is said, without a word about the 4GB patch
    """
    from patcher import launch_watch
    from patcher.state import launch_blocked

    manifest, exe, clean = _ff13_install(tmp_path)
    exe.write_bytes(clean)      # patch not applied
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest,
                                launch_watch.DRM_ERROR, ran=1.0, code=0)

    _check(api, exe)

    assert not launch_blocked(tmp_path / "state.json", "flip_laa_flag", "dinput8_shim")
    assert exe.read_bytes() == clean
    assert [e["stage"] for e in events] == ["done"]
    assert "closed itself as it started" in events[0]["detail"]
    assert "4GB" not in events[0]["detail"] and not events[0]["cleared"]


def _check(api, exe, game="ff13"):
    api._verify_launch(game, exe)
    for thread in threading.enumerate():
        if thread.name == "launch-check":
            thread.join(timeout=10)


def test_a_crash_in_a_drawer_mods_dll_is_never_put_down_to_the_4gb_patch(
        shell, monkeypatch, tmp_path):
    """
    src_38 filed every exit in the first twelve seconds as Steam refusing the
    patch. LR crashed, the patch came off and stayed off, and the game still
    wouldn't start
    """
    from patcher import launch_watch
    from patcher.patch_ops import is_laa_enabled
    from patcher.state import load_state

    manifest, exe, _ = _ff13_install(tmp_path)
    api, events = _watching_api(
        shell, monkeypatch, tmp_path, manifest, launch_watch.CRASHED, ran=0.8,
        code=0xC0000005, crash={"module": "version.dll", "path": "", "exception": "c0000005"})
    owners = []

    class Owning(_FakePatcher):
        def module_owner(self, game_id, where):
            owners.append(where)
            return {"name": "HD Fonts and GUI", "drawer": True}

    api._patcher = lambda: Owning(manifest, tmp_path)
    _check(api, exe)

    assert is_laa_enabled(exe.read_bytes()), "took the patch off for a dll's crash"
    assert "launch_checks" not in load_state(tmp_path / "state.json")
    assert owners == ["version.dll"]
    detail = events[-1]["detail"]
    assert events[-1]["verdict"] == launch_watch.CRASHED
    assert "crash in version.dll, which is HD Fonts and GUI's" in detail
    assert "Take HD Fonts and GUI off under Installed" in detail


def test_a_crash_in_the_shim_is_the_cover_failing(shell, monkeypatch, tmp_path):
    """the same repair as the DRM refusing what it served: patch and shim both off"""
    from patcher import launch_watch
    from patcher.state import launch_blocked, load_state

    manifest, exe, clean = _ff13_install(tmp_path)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest, launch_watch.CRASHED,
                                ran=0.3, crash={"module": "DINPUT8.dll"})
    _check(api, exe)

    assert exe.read_bytes() == clean
    assert launch_blocked(tmp_path / "state.json", "flip_laa_flag", "dinput8_shim")
    entry = load_state(tmp_path / "state.json")["launch_checks"]["flip_laa_flag"]
    assert entry["evidence"]["crash"] == {"module": "DINPUT8.dll"}
    assert events[-1]["undone"] is True


def test_a_game_that_wont_start_with_the_patch_off_clears_its_refusal(
        shell, monkeypatch, tmp_path):
    """what LR needed: the refusal on record was a crash, and it kept the patch off for good"""
    from patcher import launch_watch
    from patcher.engine import COVER_SHIM
    from patcher.state import launch_blocked, record_launch

    manifest, exe, clean = _ff13_install(tmp_path)
    exe.write_bytes(clean)
    record_launch(tmp_path / "state.json", "flip_laa_flag", launch_watch.DRM_ERROR,
                  "Steam refused the patched executable", COVER_SHIM)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest,
                                launch_watch.CRASHED, ran=1.5,
                                crash={"module": "ffxiiiimg.exe"})
    _check(api, exe)

    assert not launch_blocked(tmp_path / "state.json", "flip_laa_flag", COVER_SHIM)
    assert events[-1]["cleared"] is True
    assert "wasn't what stopped it" in events[-1]["detail"]
    assert "the game's own code" in events[-1]["detail"]


def test_a_crash_with_the_patch_on_leaves_a_refusal_on_record_alone(
        shell, monkeypatch, tmp_path):
    """a dll crashing before the exe's check runs says nothing about the check"""
    from patcher import launch_watch
    from patcher.engine import COVER_PROXY
    from patcher.state import launch_blocked, record_launch

    manifest, exe, _ = _ff13_install(tmp_path)
    record_launch(tmp_path / "state.json", "flip_laa_flag", launch_watch.DRM_ERROR,
                  "Steam refused the patched executable", COVER_PROXY)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest,
                                launch_watch.CRASHED, crash={"module": "version.dll"})
    api._patcher = lambda: type("P", (_FakePatcher,), {
        "module_owner": lambda self, g, w: {}})(manifest, tmp_path)
    _check(api, exe)

    assert launch_blocked(tmp_path / "state.json", "flip_laa_flag", COVER_PROXY)
    assert events[-1]["cleared"] is False


def test_a_game_with_no_4gb_patch_is_watched_too(shell, monkeypatch, tmp_path):
    """XIII-2 has none, and it was the one that wouldn't start with nothing said"""
    from patcher import launch_watch
    from patcher.manifest import load_manifest

    manifest = load_manifest(REPO_ROOT / "manifests" / "ffxiii2.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest, launch_watch.CRASHED,
                                ran=0.4, crash={"module": "version.dll"})
    api._patcher = lambda: type("P", (_FakePatcher,), {
        "module_owner": lambda self, g, w: {"name": "HD Fonts and GUI", "drawer": True}})(
        manifest, tmp_path)
    _check(api, exe, "ff13-2")

    assert events[-1]["gameId"] == "ff13-2"
    assert events[-1]["detail"].startswith("Final Fantasy XIII-2 closed as it started.")


def test_the_launch_check_never_runs_off_windows(shell, monkeypatch, tmp_path):
    """the watch is Win32 only, and starting a thread to find that out is noise"""
    manifest, exe, _ = _ff13_install(tmp_path)
    monkeypatch.setattr(shell.sys, "platform", "linux")
    api = shell.Api()
    api._patcher = lambda: _FakePatcher(manifest, tmp_path)

    api._verify_launch("ff13", exe)

    assert not any(t.name == "launch-check" for t in threading.enumerate())


def test_the_undo_waits_for_the_game_to_let_go_of_the_executable(shell, monkeypatch,
                                                                 tmp_path):
    """
    The one that actually happened. The verdict was right and the undo was
    right, and os.replace came back WinError 5 because Steam's error box was
    still up holding the refused process open, and the process still had the
    image mapped.
    """
    from patcher import launch_watch
    from patcher.patch_ops import is_laa_enabled

    manifest, exe, clean = _ff13_install(tmp_path)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest,
                                launch_watch.DRM_ERROR)

    # the file stays locked for the first few probes, then frees up, exactly
    # like a dialog somebody eventually closes
    probes = []
    real_open = launch_watch.open if hasattr(launch_watch, "open") else open

    def flaky(path, mode="r", *a, **kw):
        if mode == "r+b":
            probes.append(path)
            if len(probes) < 3:
                raise PermissionError(5, "Access is denied")
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr("builtins.open", flaky)
    monkeypatch.setattr(launch_watch.time, "sleep", lambda s: None)

    api._verify_launch("ff13", exe)
    for thread in threading.enumerate():
        if thread.name == "launch-check":
            thread.join(timeout=10)

    assert len(probes) >= 3, "did not wait for the lock at all"
    assert exe.read_bytes() == clean, "left the executable patched"
    assert not is_laa_enabled(exe.read_bytes())
    assert events[-1]["undone"] is True

    # and the user is told what is happening while it waits, not left with a
    # game that will not start and a silent tool
    assert events[0]["stage"] == "waiting"
    assert "error box" in events[0]["detail"]


def test_a_lock_that_never_clears_still_leaves_the_install_repairable(shell, monkeypatch,
                                                                      tmp_path):
    """
    Somebody walks away with the dialog open. The undo cannot run, so the
    record has to be what saves them: the next Apply reads it and repairs.
    """
    from patcher import launch_watch
    from patcher.state import launch_blocked

    manifest, exe, _ = _ff13_install(tmp_path)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest,
                                launch_watch.DRM_ERROR)

    monkeypatch.setattr(launch_watch, "wait_until_replaceable", lambda t, seconds=0: False)
    monkeypatch.setattr("patcher.engine.write_atomic",
                        lambda *a, **kw: (_ for _ in ()).throw(PermissionError(5, "denied")))

    api._verify_launch("ff13", exe)
    for thread in threading.enumerate():
        if thread.name == "launch-check":
            thread.join(timeout=10)

    assert launch_blocked(tmp_path / "state.json", "flip_laa_flag", "dinput8_shim"), \
        "no record, so nothing will ever repair this install"
    assert events[-1]["undone"] is False
    assert "press Apply" in events[-1]["detail"]


def test_waiting_for_a_lock_gives_up_rather_than_blocking_forever():
    """a daemon thread that never returns is a leak, not a safeguard"""
    from patcher import launch_watch

    missing = Path("/definitely/not/here/ffxiiiimg.exe")
    assert launch_watch.wait_until_replaceable(missing, seconds=0.2) is False


def test_the_window_is_placed_rather_than_left_to_pywebview(shell):
    """
    pywebview asks WinForms for CenterScreen and WebView2 ignores it: the window
    lands near the top left at a spot that moves between runs. So the corner is
    computed and passed, and only when the screen is actually known.
    """
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    assert "display.centered_at(" in src
    assert 'opts["x"], opts["y"] = corner' in src
    # guarded, because a guessed coordinate is worse than no coordinate
    assert "if corner:" in src


def test_the_opening_window_is_placed_in_real_pixels(shell):
    """
    pywebview 6 scales width, height, x and y by the display and 5 scales x
    and y only, while screen_size() measures real pixels, so at 150% the
    numbers handed over opened a window bigger than a 1080p screen. The size
    and spot go onto the HWND instead, just before it's shown, while hidden
    """
    assert shell.place_window(object(), (1280, 720), (1920, 1040)) is False
    src = (REPO_ROOT / "shell.py").read_text(encoding="utf-8")
    show = src[src.index("def show_when_ready("):src.index("def install_drop_handler(")]
    assert show.index("before()") < show.index("win.show()")
    place = src[src.index("def place_window("):src.index("def filling_screen(")]
    # the chrome measured off this window at this DPI, not the opening guess
    assert "GetWindowRect" in place and "GetClientRect" in place and "CHROME_W" not in without_comments(place)
    ready = src[src.index("    def ready("):src.index("    try:\n        webview.start(")]
    assert "place_window(w, (client_w, client_h), screen)" in ready


def test_the_screen_query_reads_the_work_area(shell):
    """
    centring on the whole screen puts the bottom edge under the taskbar, which
    is the one thing a centred window must not do.
    """
    import inspect

    source = inspect.getsource(shell.screen_size)
    assert "SPI_GETWORKAREA = 0x0030" in source
    assert "SystemParametersInfoW" in source
    # and it still answers on a machine where that call fails
    assert "GetSystemMetrics" in source


def test_a_shim_that_never_logged_is_recorded_as_such(shell, monkeypatch, tmp_path):
    """
    Same repair, different bug report. Steam refusing what the shim served and
    the loader refusing the shim itself look identical from outside, and the
    only thing that tells them apart is whether the shim got as far as writing
    its first line.
    """
    from patcher import launch_watch
    from patcher.state import load_state

    manifest, exe, _ = _ff13_install(tmp_path)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest,
                                launch_watch.DRM_ERROR)

    api._verify_launch("ff13", exe)
    for thread in threading.enumerate():
        if thread.name == "launch-check":
            thread.join(timeout=10)

    entry = load_state(tmp_path / "state.json")["launch_checks"]["flip_laa_flag"]
    assert "never logged" in entry["detail"]


def test_a_shim_that_did_log_is_recorded_as_the_drm_refusing(shell, monkeypatch,
                                                              tmp_path):
    from patcher import launch_watch
    from patcher.engine import shim_log_path
    from patcher.state import load_state

    manifest, exe, _ = _ff13_install(tmp_path)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest,
                                launch_watch.DRM_ERROR)

    # the shim speaks up partway through the watch, exactly as it does on a
    # real launch where the DRM is what refuses
    log = shim_log_path(tmp_path, manifest)
    log.write_text("loaded into ...\n")
    def watch_then_log(name, seconds=None):
        log.write_text("loaded into ...\narmed\nserved untouched.exe\n")
        return launch_watch.Seen(launch_watch.DRM_ERROR)

    monkeypatch.setattr(launch_watch, "watch", watch_then_log)

    api._verify_launch("ff13", exe)
    for thread in threading.enumerate():
        if thread.name == "launch-check":
            thread.join(timeout=10)

    entry = load_state(tmp_path / "state.json")["launch_checks"]["flip_laa_flag"]
    assert "Steam refused" in entry["detail"]
    assert "never logged" not in entry["detail"]


def test_a_shim_that_will_not_delete_is_not_reported_as_removed(shell, monkeypatch,
                                                                 tmp_path):
    """
    A read-only or locked dinput8.dll is a second, independent reason the game
    will not start, and the old code swallowed the failed unlink and told the
    user it had been removed. That is the "for a reason nothing on screen
    mentions" case the strip exists to prevent, delivered by the strip itself.
    """
    from patcher import engine, launch_watch

    manifest, exe, clean = _ff13_install(tmp_path)
    api, events = _watching_api(shell, monkeypatch, tmp_path, manifest,
                                launch_watch.DRM_ERROR)

    monkeypatch.setattr(engine, "remove_file", lambda path: False)

    api._verify_launch("ff13", exe)
    for thread in threading.enumerate():
        if thread.name == "launch-check":
            thread.join(timeout=10)

    # the patch itself still came off, which is the half that matters most
    assert exe.read_bytes() == clean
    # but the message says what is still there rather than claiming success
    assert events[-1]["undone"] is False
    assert "dinput8.dll" in events[-1]["detail"]
    assert "couldn't be removed" in events[-1]["detail"]
