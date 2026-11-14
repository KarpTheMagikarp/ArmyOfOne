"""
the two ways a fresh download never got as far as a window. A zip extracted by
Explorer marks every file as downloaded, and .NET won't load a marked assembly.
On NTFS the mark is the name.dll:Zone.Identifier stream; anywhere else that's
just a file called that, which exercises the same open, remove and exists
calls. And no WebView2 at all drops pywebview to IE, which can't run the page.
"""
import os
import re
from pathlib import Path

from patcher.access import BLOCKED_MESSAGE, ZONE_MARK, unblock

ROOT = Path(__file__).resolve().parent.parent


def mark(path: Path) -> None:
    with open(str(path) + ZONE_MARK, "w", encoding="ascii") as stream:
        stream.write("[ZoneTransfer]\nZoneId=3\n")


def marked(path: Path) -> bool:
    return os.path.exists(str(path) + ZONE_MARK)


def test_the_bundle_s_dlls_lose_the_mark(tmp_path):
    runtime = tmp_path / "pythonnet" / "runtime"
    runtime.mkdir(parents=True)
    dlls = [runtime / "Python.Runtime.dll", tmp_path / "webview" / "lib" / "Microsoft.Web.WebView2.Core.dll"]
    for dll in dlls:
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_bytes(b"MZ")
        mark(dll)

    assert unblock(tmp_path) == []
    assert not any(marked(dll) for dll in dlls)
    # the files themselves are untouched
    assert all(dll.read_bytes() == b"MZ" for dll in dlls)


def test_only_dlls_are_touched(tmp_path):
    clip = tmp_path / "assets" / "cut_xiii.webm"
    clip.parent.mkdir()
    clip.write_bytes(b"\x1aE")
    mark(clip)

    unblock(tmp_path)
    assert marked(clip)


def test_nothing_to_do_is_quiet(tmp_path):
    (tmp_path / "clean.dll").write_bytes(b"MZ")
    assert unblock(tmp_path) == []
    assert unblock(tmp_path / "not-there") == []


def test_it_runs_before_pywebview_can_load_anything():
    """
    pywebview's first window loads pythonnet, so the marks have to be gone
    before the import, and a marked bundle has to be what the failure says
    """
    source = (ROOT / "shell.py").read_text(encoding="utf-8")
    main = source[source.index("\ndef main()"):]
    assert main.index("unblock(root())") < main.index("import webview")
    assert main.count("BLOCKED_MESSAGE") >= 2
    assert "Unblock" in BLOCKED_MESSAGE and re.search(r"extract it again", BLOCKED_MESSAGE)


def test_a_dead_internet_explorer_window_says_webview2_instead():
    """
    pywebview falls back to IE's engine without raising when WebView2 is
    missing or older than 86, and IE can't parse the page: a dark window that
    never said why. ready() has to catch it while the window is still hidden
    """
    source = (ROOT / "shell.py").read_text(encoding="utf-8")
    ready = source[source.index("    def ready("):]
    ready = ready[:ready.index("\n    try:")]
    assert '!= "edgechromium"' in ready and "complain(WEBVIEW2_MESSAGE)" in ready
    assert ready.index("complain(WEBVIEW2_MESSAGE)") < ready.index("show_when_ready")
    assert "opening_size_at(screen, _work_scale)" in source


def test_a_second_launch_brings_the_open_window_forward_instead():
    """
    two windows applying to one game at once each rewrote the record of what
    was set aside without the other's, and Revert forgot a player's own dll
    """
    import shell

    source = (ROOT / "shell.py").read_text(encoding="utf-8")
    main = source[source.index("\ndef main()"):]
    # after the CLI branch, so downloads and commands still get through
    assert main.index("return cli_main()") < main.index("if already_open():") < main.index("import webview")
    if os.name != "nt":
        assert shell.already_open() is False
