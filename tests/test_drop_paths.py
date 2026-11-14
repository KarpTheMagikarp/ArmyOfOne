"""
How a dropped file's path reaches this process, which for a long time it did
not.

The page read `e.dataTransfer.files[0].path`. That property is an Electron
extension to Chromium; stock Chromium never had it and Microsoft declined to
add it to WebView2 (WebView2Feedback #501). So it was always undefined, every
drop fell into the "couldn't read that file's location" branch, and because an
uncancelled file drop is a navigation as far as Chromium is concerned,
pywebview turned it into webbrowser.open() on the dropped path. Dropping a
folder opened the folder.

The real path is host-side only: pywebview 5.0 stamps pywebviewFullPath onto
the serialised event if a Python drop handler is subscribed, and subscribing is
also what cancels the drop.
"""

import ast
import re
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def ui():
    return (REPO / "ui" / "index.html").read_text(encoding="utf-8")


def shell():
    return (REPO / "shell.py").read_text(encoding="utf-8")


def test_the_page_no_longer_reads_a_property_that_does_not_exist():
    """
    `file.path` in WebView2 is undefined, always was, and reading it is how
    this went unnoticed for so long: undefined is not an error.
    """
    text = ui()
    # the comment explaining why is allowed to name it; code is not
    code = "\n".join(line for line in text.splitlines()
                     if "Electron" not in line and "extension" not in line
                     and "never had" not in line)
    assert not re.search(r"\bfile\.path\b", code), \
        "something still reads file.path, which is undefined in WebView2"
    assert not re.search(r"files\[0\]\.path", code)


def test_the_page_still_cancels_the_drop():
    """
    Cancelling is the point of the page's listener now. Without it Chromium
    treats the drop as a navigation, WebView2 raises NewWindowRequested and
    pywebview's default handler opens the path in the shell.
    """
    text = ui()
    body = text[text.index("async function onDrop(e){"):]
    body = body[:body.index("\n  }")]
    assert "e.preventDefault()" in body, "the drop is no longer cancelled"

    assert 'addEventListener("dragover"' in text, \
        "without a cancelled dragover the drop event never fires at all"


def test_the_shell_subscribes_to_drops_host_side():
    text = shell()
    assert "def install_drop_handler(" in text
    assert "DOMEventHandler" in text, "no pywebview DOM subscription, so no paths"
    assert "pywebviewFullPath" in text, "the path field is never read"
    assert "install_drop_handler(w, api)" in text, "the handler is defined and never installed"


def test_the_subscription_asks_for_prevent_default():
    """
    DOMEventHandler(fn) defaults to NOT preventing default, which is the whole
    failure this is fixing.
    """
    text = shell()
    body = text[text.index("def install_drop_handler("):]
    body = body[:body.index("\ndef ")] if "\ndef " in body else body

    handlers = re.findall(r"DOMEventHandler\(([^)]*)\)", body)
    assert handlers, "no drop handler at all"
    for args in handlers:
        assert args.count(",") >= 2, f"DOMEventHandler({args}) does not prevent default"
        assert "True, True" in args.replace(" ", " "), \
            f"DOMEventHandler({args}) needs preventDefault and stopPropagation"


def drop_handler_reads():
    """every `x.y` install_drop_handler touches, as (x, y), docstring excluded"""
    tree = ast.parse(shell())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "install_drop_handler")
    return {(ast.unparse(n.value), n.attr) for n in ast.walk(fn) if isinstance(n, ast.Attribute)}


def test_the_host_listens_on_window_and_only_for_the_drop():
    """
    pywebview hands Python the whole event, and serialises every node on it
    deep, the element the listener sits on included. On document that's the
    entire page: ~360ms and 6.6MB a drop in Chromium, paid on the page's
    thread before the overlay can come down, and then parsed twice on the
    window's own thread in Python. The dragover subscription paid the same on
    every dragover, which fires the whole time a file is held over the window.

    On window it's the overlay the drop landed on, a couple of milliseconds.
    The page cancels dragover itself, so nothing else is needed.
    """
    reads = drop_handler_reads()
    assert ("window.dom", "window") in reads, "the drop isn't heard on window"
    assert ("window.dom", "document") not in reads, \
        "listening on document again, which serialises the whole page per event"
    assert not any(attr == "dragover" for _, attr in reads), \
        "a host-side dragover subscription is back, and it costs a full serialise per dragover"


def test_the_subscription_through_pywebview_itself():
    """
    the same, through pywebview's own DOM classes with the browser stood in,
    so a rename on their side shows up here rather than as drops arriving
    empty on someone's PC
    """
    pytest.importorskip("webview")
    import types

    from webview.dom import _dnd_state
    from webview.dom.dom import DOM

    import shell

    class Window:
        def __init__(self):
            self.events = types.SimpleNamespace(loaded=self)
            self.scripts = []
            self.dom = DOM(self)

        def __iadd__(self, handler):
            return self

        def evaluate_js(self, script):
            self.scripts.append(script)
            return ["drop", "dragover", "dragenter"] if "getOwnPropertyNames" in script else "h1"

    emitted = []
    api = types.SimpleNamespace(_emit=emitted.append, _log=None)
    window = Window()
    listeners = _dnd_state["num_listeners"]
    try:
        assert shell.install_drop_handler(window, api) is True

        added = [s for s in window.scripts if "addEventListener(" in s]
        assert len(added) == 1 and "addEventListener('drop'" in added[0], added
        assert "if ('window' === 'document')" in added[0], "subscribed somewhere other than window"
        assert "e.preventDefault();" in added[0] and "e.stopPropagation();" in added[0]

        handler = window.dom._elements["window"]._event_handlers["drop"][0]
        handler({"dataTransfer": {"files": [{"name": "HD.zip", "pywebviewFullPath": r"D:\mods\HD.zip"},
                                            {"name": "x"}]}})
        assert emitted == [{"type": "dropped", "paths": [r"D:\mods\HD.zip"]}]
    finally:
        _dnd_state["num_listeners"] = listeners


def test_the_page_cancels_drops_before_the_assets_load():
    """
    pywebview used to cancel dragover from the host, and with that gone the
    page's listener is the only cancel there is. An uncancelled drag never
    fires a drop, and Chromium opens the file instead. The window can show at
    its 6s deadline with the assets still loading, so the listeners can't wait
    on them.
    """
    text = ui()
    assert text.index("\nDrop.build();") < text.index("\nloadAssets().then("), \
        "the drop listeners wait on the assets, and a drop before then opens Explorer"
    assert text.index("const Drop = (() => {") < text.index("\nDrop.build();")


def test_the_overlay_is_what_a_drop_lands_on():
    """
    pywebview serialises the drop's target too, and with the overlay letting
    pointer events through, the target was whatever sat under the cursor,
    often a container holding most of the window. Taking the drop keeps that
    to the overlay's handful of nodes, and only works if nothing that takes
    pointer events sits above it.
    """
    text = ui()
    style = text[text.index('<style id="drop-style">'):]
    style = style[:style.index("</style>")]
    shown = re.search(r"#drop\.on\{([^}]*)\}", style).group(1)
    assert "pointer-events:auto" in shown.replace(" ", ""), "the drop falls through the overlay"

    own = int(re.search(r"z-index:(\d+)", re.search(r"#drop\{([^}]*)\}", style).group(1)).group(1))
    others = [int(z) for z in re.findall(r"z-index:\s*(\d+)", text)]
    assert own == max(others) and others.count(own) == 1, \
        f"the overlay at {own} isn't above everything else ({sorted(set(others))})"


def test_a_missing_dom_api_is_survivable():
    """
    an older pywebview has no window.dom at all. That has to fall back to the
    pickers rather than take the window down on startup.
    """
    body = shell()[shell().index("def install_drop_handler("):]
    assert "except Exception" in body
    assert "return False" in body, "a failed subscription is not reported to the caller"


def test_the_page_handles_the_event_the_shell_sends_back():
    text = ui()
    receiver = text[text.index("window.__patch = {"):]
    assert 'type === "dropped"' in receiver, "the host-side answer is never handled"
    assert "Drop.dropped" in receiver

    assert "dropped };" in text or "dropped }" in text, \
        "Drop.dropped is not exported, so the receiver cannot call it"


def test_pywebview_is_pinned_above_the_version_that_added_this():
    """
    a bare pin can resolve below 5.0, where there is no host-side drop route at
    all and the feature silently does nothing
    """
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"^pywebview([^\s#]*)", text, re.M)

    assert match, "pywebview is not in requirements.txt"
    assert ">=5" in match.group(1), f"pywebview pinned as {match.group(1)!r}, which may resolve below 5.0"


def test_the_installed_pywebview_still_has_the_pieces_this_depends_on():
    """
    The pin has a ceiling but a wide range under it, so this checks the actual
    resolved version rather than the number.

    Every one of these is silent when it goes. No pywebviewFullPath and drops
    yield an empty path list, which looks exactly like dropping the wrong
    thing. A DOMEventHandler that stops taking positional flags means the drop
    is never cancelled, and an uncancelled drop opens the folder in Explorer,
    which is the original bug. Checked against 6.2.1 by hand; this is so the
    next upgrade does not need the same afternoon.
    """
    webview = pytest.importorskip("webview")

    from webview.dom import DOMEventHandler
    import inspect

    args = list(inspect.signature(DOMEventHandler.__init__).parameters)[1:4]
    assert args == ["callback", "prevent_default", "stop_propagation"], \
        f"DOMEventHandler now takes {args}, so DOMEventHandler(fn, True, True) means something else"

    from pathlib import Path as P
    util = P(inspect.getfile(webview)).parent / "util.py"
    assert "pywebviewFullPath" in util.read_text(encoding="utf-8"), \
        "this pywebview no longer stamps the dropped file's path, so drops arrive empty"

    for name in ("OPEN_DIALOG", "FOLDER_DIALOG", "screens", "start", "create_window"):
        assert hasattr(webview, name), f"webview.{name} is gone"
    # window.dom itself is set per instance in Window.__init__, but the class
    # it's made from is here, and the drop is heard on its window
    from webview.dom.dom import DOM
    assert isinstance(DOM.__dict__.get("window"), property), \
        "window.dom.window is gone, so the drop handler never subscribes and drops arrive empty"
    for name in ("show", "hide", "resize", "evaluate_js"):
        assert hasattr(webview.Window, name), f"Window.{name} is gone"

    assert "hidden" in inspect.signature(webview.create_window).parameters, \
        "create_window lost hidden=, so the window opens before the page has painted"


def test_the_panel_offers_a_way_in_that_does_not_need_a_drop():
    """
    the drop route depends on the host side working and this is the part that
    always does, so it cannot be the thing that is missing.

    Both pickers live on the drop zone now. They used to be duplicated as a
    pair further down the panel, next to the toggles, which is a second place
    to point at the same mod and a second thing to read past.
    """
    text = ui()
    at = text.index('panel.id = "md";')
    zone = text[at:text.index("function available()", at)]

    assert "pick_file" in text and "pick_folder" in text, \
        "no picker anywhere, so a broken drop leaves no route in"
    assert 'id="mdPick"' in zone and 'id="mdPickDir"' in zone, \
        "the zone cannot take a folder, and the HD Project ships as one"
    # the folder one is inside the zone, whose own click handler picks a file
    assert 'browse("folder")' in zone and "stopPropagation" in zone

    # and not a second pair beside the toggles
    body = text[text.index("function buildOptions("):]
    body = body[:body.index("\n  function refresh(")]
    assert "pick_file" not in body and "pick_folder" not in body, \
        "there are two places to point at the mod again"


@pytest.fixture
def project_zip(tmp_path):
    """an archive shaped like the one people actually download"""
    root = tmp_path / "src" / "FFXIII HD Project"
    (root / "Data").mkdir(parents=True)
    (root / "SupportFiles").mkdir(parents=True)
    (root / "textures").mkdir(parents=True)
    (root / "Data" / "LoadHDNPCs.txt").write_text("#:NPCs\nn n910\n", encoding="utf-8")
    (root / "Data" / "FFXIII_General_Models.bin").write_bytes(b"\x00" * 64)
    (root / "SupportFiles" / "ff13tool.exe").write_bytes(b"MZ")
    (root / "textures" / "x.dds").write_bytes(b"DDS ")

    archive = tmp_path / "HDProject.zip"
    with zipfile.ZipFile(archive, "w") as out:
        for path in sorted(root.rglob("*")):
            out.write(path, str(path.relative_to(root.parent)))
    return archive


def bridge():
    from patcher.bridge import UiBridge
    paths = {p.stem: p for p in sorted((REPO / "manifests").glob("*.json"))}
    return UiBridge(manifest_paths=paths, steam_library_paths=[])


def test_a_dropped_zip_is_recognised_as_the_mod(project_zip):
    """
    the mod is downloaded as an archive, so that is what people drop. Looking
    only at folders meant it fell through to the ordinary mod ingest, which
    went hunting for loose .imgb files and said "no compatible mod files found
    in archive". True, and completely useless.
    """
    found = bridge().identify_drop(str(project_zip))

    assert found["kind"] == "hdproject", found
    assert Path(found["detail"]["data"]).name == "Data"
    assert found["unpackedTo"]


def test_a_zip_is_identified_without_unpacking_it_first(project_zip):
    """
    six gigabytes. The central directory answers the question for free, so
    nothing is written until the answer is yes.
    """
    before = set(project_zip.parent.iterdir())

    answer = bridge().identify_drop(str(project_zip), unpack=False)

    assert answer["kind"] == "maybe-hdproject"
    assert set(project_zip.parent.iterdir()) == before, "it unpacked something anyway"


def test_an_ordinary_mod_zip_is_left_alone(tmp_path):
    """
    Leviathan's Tears is a zip too, and it has to keep going down the ordinary
    ingest path rather than being unpacked looking for a mod it is not
    """
    archive = tmp_path / "LeviathansTears.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("veffs.jp.win32.imgb", b"payload")

    answer = bridge().identify_drop(str(archive))

    assert answer["kind"] == "archive"
    assert not list(tmp_path.glob(".*-unpacked")), "an unrelated archive was unpacked"


def test_dropping_the_same_zip_twice_unpacks_once(project_zip):
    first = bridge().identify_drop(str(project_zip))
    second = bridge().identify_drop(str(project_zip))

    assert first["unpackedTo"] == second["unpackedTo"]


def test_a_zip_that_escapes_its_folder_is_still_confined(tmp_path):
    """
    safe_target does the work, and this is the one place a zip is expanded
    without going through ingest.scan
    """
    archive = tmp_path / "HDProject.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("Data/LoadHDNPCs.txt", "#:NPCs\nn n910\n")
        out.writestr("Data/FFXIII_General_Models.bin", "\x00" * 64)
        out.writestr("SupportFiles/ff13tool.exe", "MZ")
        out.writestr("../../escaped.txt", "nope")

    bridge().identify_drop(str(archive))

    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_a_picker_says_which_way_it_went():
    """
    They returned a bare path, and null meant cancelled AND meant the dialog
    threw AND meant pywebview handed back an empty tuple. The page could not
    tell those apart, so it said nothing for all three, which from the other
    side is a button that does nothing when you press it.

    The empty tuple is not hypothetical. On the WinForms backend the folder
    picker is a reflection hack around OpenFileDialog with FOS_PICKFOLDERS set
    and it returns tuple(FileNames), which comes back empty: the browser opens,
    you choose a folder, and nothing is selected.
    """
    text = shell()
    helper = text[text.index("def _dialog("):text.index("def _open_dialog(")]

    assert '"cancelled": True' in helper, "a cancel is indistinguishable from a failure"
    assert '"empty": True' in helper, "an empty tuple reads as a cancel"
    assert 'isinstance(result, str)' in helper, \
        "a backend that returns a bare string would be indexed to its first character"
    assert "except Exception as e" in helper and '"error"' in helper

    for name in ("def pick_file", "def pick_folder"):
        body = text[text.index(name):]
        body = body[:body.index("\n    def ")]
        assert "_dialog(" in body, f"{name} does not go through the one helper"

    # and the page reads all three
    page = ui()
    browse = page[page.index("async function browse(which)"):]
    browse = browse[:browse.index("\n  }")]
    assert "answer.ok && answer.path" in browse
    assert "answer.cancelled" in browse
    assert "answer.error" in browse


def test_the_folder_picker_points_somewhere_when_it_comes_back_empty():
    """
    it is the one that fails this way, and there is a working route to name
    """
    body = shell()[shell().index("def pick_folder("):]
    body = body[:body.index("\n    def ")]
    assert 'answer.get("empty")' in body
    assert "Choose a file" in body, "it fails without saying what does work"


def test_unpacking_a_dropped_archive_does_not_freeze_the_window():
    """
    identify_drop runs on the webview's own thread. Listing a zip's central
    directory is instant; unpacking six gigabytes of it is minutes of a window
    that does not repaint, which Windows eventually offers to kill.
    """
    text = shell()
    body = text[text.index("def unpack_drop("):]
    body = body[:body.index("\n    def ")]

    assert "threading.Thread(" in body and "daemon=True" in body
    assert 'self._emit({"type": "dropped"' in body, \
        "the answer does not come back as the event the page already routes"

    # and the page asks the cheap question first
    page = ui()
    route = page[page.index("async function route(path, onlyHd, tries = 0, game = null)"):]
    route = route[:route.index("\n  }")]
    assert "api.identify_drop(path, false)" in route, \
        "the page asks for an unpack on the thread that draws the window"
    assert "maybe-hdproject" in route and "api.unpack_drop(path)" in route


def test_a_file_chosen_inside_the_mod_folder_finds_the_mod():
    """
    the working route when the folder picker is the one that hands back
    nothing: navigate into the unpacked mod and choose anything in it
    """
    from patcher.bridge import UiBridge

    body = (REPO / "patcher" / "bridge.py").read_text(encoding="utf-8")
    body = body[body.index("def identify_drop("):body.index("def _unpack_for_project(")]
    assert "path.parent" in body, "a file inside the mod folder is just a file"


def test_a_file_inside_the_project_resolves_to_the_project(tmp_path):
    from patcher.bridge import UiBridge

    root = tmp_path / "HDProject"
    for name in ("Data", "SupportFiles", "textures"):
        (root / name).mkdir(parents=True)
    (root / "Data" / "LoadHDNPCs.txt").write_text("#:NPCs\nn n910\n", encoding="utf-8")
    (root / "Data" / "FFXIII_General_Models.bin").write_bytes(b"\x00" * 64)
    (root / "SupportFiles" / "ff13tool.exe").write_bytes(b"MZ")

    bridge = UiBridge()
    answer = bridge.identify_drop(str(root / "Data" / "LoadHDNPCs.txt"), unpack=False)

    assert answer["kind"] == "hdproject", answer
    assert answer["via"].endswith("LoadHDNPCs.txt")


def test_an_unpack_that_never_finished_is_not_reused(project_zip):
    """
    the three landmarks detect() looks for arrive early, so a cut-off unpack
    passes it with most of the art missing, and missing art reads as nothing to
    install rather than as a failure
    """
    staging = project_zip.parent / f".{project_zip.stem}-unpacked"
    partial = staging / "FFXIII HD Project"
    for name in ("Data", "SupportFiles"):
        (partial / name).mkdir(parents=True)
    (partial / "Data" / "LoadHDNPCs.txt").write_text("#:NPCs\nn n910\n", encoding="utf-8")
    (partial / "Data" / "FFXIII_General_Models.bin").write_bytes(b"\x00" * 64)

    found = bridge().identify_drop(str(project_zip))

    assert found["kind"] == "hdproject", found
    assert (Path(found["unpackedTo"]) / "FFXIII HD Project" / "textures" / "x.dds").is_file(), \
        "the half finished folder was used as it was"


def test_a_new_download_under_the_same_name_is_unpacked_again(project_zip):
    first = bridge().identify_drop(str(project_zip))
    with zipfile.ZipFile(project_zip, "a") as out:
        out.writestr("FFXIII HD Project/textures/y.dds", b"DDS new")

    second = bridge().identify_drop(str(project_zip))

    assert first["unpackedTo"] == second["unpackedTo"]
    assert (Path(second["unpackedTo"]) / "FFXIII HD Project" / "textures" / "y.dds").is_file()


def test_an_unpack_with_no_room_says_so_instead_of_not_the_mod(project_zip, monkeypatch):
    monkeypatch.setattr("patcher.download.free_space", lambda p: 1024)

    found = bridge().identify_drop(str(project_zip))

    assert found["kind"] == "archive"
    assert "GB free" in found.get("error", ""), found


def test_unpacking_again_keeps_the_originals_cache(project_zip):
    """models\\orig came out of the game before the first install and cannot be taken again"""
    first = bridge().identify_drop(str(project_zip))
    cached = (Path(first["unpackedTo"]) / "FFXIII HD Project" / "models" / "orig" / "chr"
              / "npc" / "n910.win32.trb")
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"VANILLA")
    with zipfile.ZipFile(project_zip, "a") as out:
        out.writestr("FFXIII HD Project/textures/y.dds", b"DDS new")

    second = bridge().identify_drop(str(project_zip))

    assert (Path(second["unpackedTo"]) / "FFXIII HD Project" / "textures" / "y.dds").is_file()
    assert cached.read_bytes() == b"VANILLA", "the originals cache went with the old unpack"


def test_a_newer_version_under_the_same_name_is_what_gets_installed(tmp_path):
    """its top folder carries its version, and the old one sorted first"""
    def build(top, tool):
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr(f"{top}/Data/LoadHDNPCs.txt", "#:NPCs\nn n910\n")
            out.writestr(f"{top}/Data/FFXIII_General_Models.bin", b"\x00" * 64)
            out.writestr(f"{top}/SupportFiles/ff13tool.exe", tool)
            out.writestr(f"{top}/textures/x.dds", b"DDS ")

    archive = tmp_path / "FFXIII HD Project.zip"
    build("FFXIII HD Project v3.1", b"MZ old tools")
    first = bridge().identify_drop(str(archive))
    cached = Path(first["detail"]["root"]) / "models" / "orig" / "chr" / "npc" / "n910.win32.trb"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"VANILLA")

    archive.unlink()
    build("FFXIII HD Project v3.2", b"MZ new tools")
    second = bridge().identify_drop(str(archive))

    root = Path(second["detail"]["root"])
    assert root.name == "FFXIII HD Project v3.2"
    assert (root / "SupportFiles" / "ff13tool.exe").read_bytes() == b"MZ new tools"
    assert (root / "models" / "orig" / "chr" / "npc" / "n910.win32.trb").read_bytes() == b"VANILLA"


def test_the_overlay_comes_down_when_pywebview_keeps_the_drop():
    """
    pywebview's listener stops the drop where it sits. When that was on
    document, a bubbling listener on window never heard it, and that listener
    was the only thing taking the overlay down: after a folder drop it sat over
    the window for good. Capture runs first wherever pywebview listens.
    """
    text = ui()
    assert 'addEventListener("drop", onDrop, true)' in text, \
        "the page's drop listener is back in the bubble phase, behind pywebview's"

    body = text[text.index("  async function dropped(paths, detail){"):]
    body = body[:body.index("\n  }")]
    assert "hide()" in body and "depth = 0" in body, \
        "the host's answer, the one thing every drop gets, no longer takes the overlay down"


def test_a_drop_in_a_real_dom():
    """
    the structure above, behaviour here, with pywebview's listener stood in.
    Skips without node and jsdom.
    """
    import os
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node is not installed")
    env = {**os.environ}
    for candidate in (REPO / "node_modules", Path.home() / "node_modules"):
        if candidate.is_dir():
            env["NODE_PATH"] = str(candidate)
            break

    result = subprocess.run(["node", str(REPO / "tools" / "drop_probe.js")],
                            capture_output=True, text=True, cwd=REPO, env=env, timeout=180)
    if "Cannot find module 'jsdom'" in result.stderr:
        pytest.skip("jsdom is not installed (npm install jsdom)")

    assert result.returncode == 0, result.stdout + result.stderr
