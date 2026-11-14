"""
The joins between the pieces, which is where this project keeps breaking.

Every failure this file guards was invisible until a specific button was
pressed: a method on the bridge that the shell never forwarded, a lazy import
that only runs when an install starts, an event emitted under a name nothing
listens for. None of them raise at import time and none of them fail a unit
test of either side on its own.
"""

import ast
import importlib
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def source(name):
    return (REPO / name).read_text(encoding="utf-8")


def shell_tree():
    return ast.parse(source("shell.py"))


def test_main_still_reaches_the_event_loop():
    """
    A module-level def pasted into the middle of main() ends main() there. The
    tail keeps its four-space indent, so it parses, and it becomes dead code
    after the new function's return. That shipped: main() stopped at the nxm
    registration and the process exited to the prompt with no error, no
    traceback and no window, because there was nothing left of it to run.

    Nothing about that is visible in a diff. It is visible here.
    """
    main = next(n for n in shell_tree().body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = {ast.unparse(c.func) for c in ast.walk(main) if isinstance(c, ast.Call)}

    assert "webview.start" in calls, \
        "main() no longer reaches webview.start, so the app exits instead of opening"
    assert "webview.create_window" in calls


def test_main_gets_all_the_way_to_start_with_a_stand_in_pywebview(monkeypatch):
    """
    The static check above proves the call is still in the function. This
    proves the function reaches it, which is a different claim: everything
    between parse_args and start has to survive too.

    pywebview is the only part that cannot run here, and main() imports it
    itself rather than at module scope, so a stand-in in sys.modules is enough
    to walk the whole startup on any platform.
    """
    import sys
    import types

    started = {}

    class FakeWindow:
        def __init__(self, title, url, **kw):
            self.title, self.url, self.kw = title, url, kw
            self.width = self.height = 0
            self.dom = None

        def evaluate_js(self, js):
            return "ui-ready"

        def show(self):
            started["shown"] = True

        def resize(self, *a):
            pass

    fake = types.ModuleType("webview")
    fake.OPEN_DIALOG, fake.FOLDER_DIALOG, fake.SAVE_DIALOG = 10, 20, 30
    fake.screens = []
    fake.Window = FakeWindow
    fake.create_window = lambda title, url, **kw: FakeWindow(title, url, **kw)
    fake.start = lambda func=None, **kw: started.update(kwargs=kw, func=func)
    fake.dom = types.ModuleType("webview.dom")
    fake.dom.DOMEventHandler = lambda *a, **k: None

    import shell

    # the stand-in start() never runs the loop, so main() complains that the
    # window never opened. On Windows that's a real MessageBox, and nobody's
    # there to click OK on a CI runner, so the run just sat there for an hour
    complaints = []
    monkeypatch.setattr(shell, "complain", complaints.append)
    # and on Windows the real one would claim nxm:// in whoever's registry is
    # running the tests
    monkeypatch.setattr("patcher.nxm_registry.ensure_registered",
                        lambda *a, **k: types.SimpleNamespace(error=None, ours=False, owner="test"))

    argv, modules = sys.argv, dict(sys.modules)
    sys.argv = ["shell.py"]
    sys.modules["webview"], sys.modules["webview.dom"] = fake, fake.dom
    try:
        shell.main()
    finally:
        sys.argv = argv
        sys.modules.clear()
        sys.modules.update(modules)

    assert "kwargs" in started, "main() returned without ever calling webview.start"
    assert started["kwargs"]["gui"] == "edgechromium"
    assert callable(started["func"]), "start() got no callback, so nothing runs once the loop is up"


def python_files():
    for folder in ("patcher", "tools", "tests"):
        yield from sorted((REPO / folder).rglob("*.py"))
    yield REPO / "shell.py"
    yield REPO / "main.py"


def test_nothing_sits_after_a_return():
    """
    The general form of the bug above: a statement following a return in the
    same block never runs, and Python is perfectly happy about it.
    """
    stranded = []
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list):
                    continue
                for i, stmt in enumerate(block[:-1]):
                    if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                        stranded.append(
                            f"{path.relative_to(REPO)}:{block[i + 1].lineno} "
                            f"after {type(stmt).__name__.lower()} on line {stmt.lineno}")
    assert not stranded, "code that can never run:\n  " + "\n  ".join(stranded)


def test_every_lazy_import_in_the_shell_resolves():
    """
    Every patcher import in shell.py is inside a function, so a typo is not an
    ImportError at startup, it is an ImportError the first time a user presses
    the button that reaches it.
    """
    wanted = set()
    for node in ast.walk(shell_tree()):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("patcher"):
            wanted.add(node.module)
        elif isinstance(node, ast.Import):
            wanted |= {a.name for a in node.names if a.name.startswith("patcher")}

    assert wanted, "found no patcher imports at all, this test has gone stale"
    broken = []
    for name in sorted(wanted):
        try:
            importlib.import_module(name)
        except Exception as e:
            broken.append(f"{name}: {type(e).__name__}: {e}")
    assert not broken, "lazy imports that would fail at the moment they run: " + "; ".join(broken)


def test_every_name_the_shell_imports_actually_exists():
    """
    `from patcher.mods.hd_install import instal` is a clean parse, a clean
    startup, and a crash forty minutes into someone's evening.
    """
    missing = []
    for node in ast.walk(shell_tree()):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("patcher"):
            continue
        module = importlib.import_module(node.module)
        for alias in node.names:
            if alias.name == "*":
                continue
            if hasattr(module, alias.name):
                continue
            # `from patcher import display` names a SUBMODULE, which is not an
            # attribute of the package until something imports it. That is a
            # real import either way, so try it as one before calling it broken.
            try:
                importlib.import_module(f"{node.module}.{alias.name}")
            except ImportError:
                missing.append(f"{node.module}.{alias.name}")
    assert not missing, f"imported but not defined: {missing}"


def test_the_shell_forwards_every_bridge_call_the_ui_makes():
    """
    The UI reaches the backend through window.pywebview.api, which is the
    shell's Api, and every call site is guarded with `if (api && api.thing)`.
    A method that only exists on UiBridge is not an error, it is a button that
    does nothing.
    """
    ui = source("ui/index.html")
    called = set(re.findall(r"api\.([a-z_][a-z0-9_]*)\s*\(", ui))
    exposed = {item.name
               for node in ast.walk(shell_tree())
               if isinstance(node, ast.ClassDef)
               for item in node.body
               if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}

    assert called, "no backend calls found, this test has gone stale"
    assert not sorted(called - exposed), \
        f"the UI calls these and the shell does not expose them: {sorted(called - exposed)}"


def test_the_ui_passes_a_number_of_arguments_the_shell_can_take():
    """
    the half the name check misses. pywebview hands JS arguments across
    positionally, so an extra one is a TypeError raised inside the bridge on
    somebody else's machine, at the moment they press the button. Adding a
    mode to install_hd_project is exactly the change that does it.
    """
    ui = source("ui/index.html")
    methods = {item.name: item
               for node in ast.walk(shell_tree())
               if isinstance(node, ast.ClassDef)
               for item in node.body
               if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}

    wrong = []
    for name, count in _call_arities(ui):
        fn = methods.get(name)
        if fn is None:
            continue
        args = fn.args.args[1:]           # self
        if fn.args.vararg:
            continue
        low = len(args) - len(fn.args.defaults)
        if not low <= count <= len(args):
            wrong.append(f"api.{name} called with {count}, takes {low} to {len(args)}")
    assert not wrong, "; ".join(wrong)


def _call_arities(text):
    """(name, argument count) for every api.x(...) in the page"""
    found = []
    for match in re.finditer(r"\bapi\.([a-z_][a-z0-9_]*)\s*\(", text):
        depth, commas, quote, i = 0, 0, "", match.end() - 1
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
            elif ch in "\"'`":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    body = text[match.end():i].strip()
                    found.append((match.group(1), 0 if not body else commas + 1))
                    break
            elif ch == "," and depth == 1:
                commas += 1
            i += 1
    return found


def test_the_shell_only_forwards_bridge_methods_that_exist():
    """
    the other direction. self._patcher().thing() is resolved at call time, so
    a renamed bridge method is a crash inside a thread nobody is watching.
    """
    from patcher.bridge import UiBridge

    calls = set(re.findall(r"self\._patcher\(\)\.([a-z_][a-z0-9_]*)\s*\(", source("shell.py")))
    assert calls, "no bridge calls found, this test has gone stale"
    missing = sorted(c for c in calls if not hasattr(UiBridge, c))
    assert not missing, f"the shell calls these and UiBridge does not have them: {missing}"


def test_every_event_the_shell_emits_is_handled_by_the_page():
    """
    shell.py emits {"type": ...} into window.__patch.receive. An event under a
    name nothing listens for is silent: the work happens and the UI never
    moves, which is exactly how the first version of the terminal behaved.
    """
    emitted = set(re.findall(r'self\._emit\(\{"type": "(\w+)"', source("shell.py")))
    ui = source("ui/index.html")
    receiver = ui[ui.index("window.__patch = {"):]

    assert emitted, "no emitted event types found, this test has gone stale"
    unhandled = sorted(t for t in emitted
                       if f'type === "{t}"' not in receiver and f'"{t}"' not in receiver)
    assert not unhandled, f"emitted but nothing listens for them: {unhandled}"


def test_the_hd_stages_the_shell_sends_are_all_understood():
    """
    same again one level down. the terminal switches on ev.stage, and a stage
    with no branch is a run that streams nothing and never finishes.
    """
    # only the hd ones. "stage" is also the acquire flow's word, and its
    # stages are handled in a different receiver entirely.
    shell = source("shell.py")
    hd = shell[shell.index("def install_hd_project"):shell.index("def begin_acquire")]
    stages = set(re.findall(r'emit\("(\w+)"', hd))
    stages |= set(re.findall(r'"stage": "(\w+)"', hd))
    ui = source("ui/index.html")
    body = ui[ui.index("const Hd = (() =>"):]
    body = body[:body.index("const Term = (() =>")]

    assert stages, "no hd stages found, this test has gone stale"
    # started carries no payload and is deliberately not branched on
    unhandled = sorted(s for s in stages - {"started"} if f'"{s}"' not in body)
    assert not unhandled, f"stages the terminal does not handle: {unhandled}"


def test_the_manifest_categories_survive_the_build_step():
    """
    the UI renders categories and the backend decides them, stamped across by
    build_ui_manifest.py. A category added to manifests/ and not rebuilt is a
    toggle that never appears.
    """
    generated = REPO / "assets" / "manifest.json"
    if not generated.is_file():
        import subprocess, sys
        subprocess.run([sys.executable, str(REPO / "tools" / "build_ui_manifest.py")],
                       check=True, capture_output=True)

    from patcher.manifest import load_manifest
    backend = load_manifest(REPO / "manifests" / "ffxiii.json")
    patch = next(p for p in backend.patches if p.ui_job_id == "hdproject")

    built = json.loads(generated.read_text(encoding="utf-8"))
    job = next(j for g in built["games"] for j in g["jobs"] if j["id"] == "hdproject")

    assert [c["id"] for c in job["categories"]] == [c["id"] for c in patch.categories]
    for wanted, got in zip(patch.categories, job["categories"]):
        assert wanted == got, f"{wanted['id']} differs between the manifest and the build"


def test_every_category_names_a_list_file_the_mod_actually_ships():
    """
    a category whose list file does not exist installs nothing and says
    nothing, because members_for skips a missing listing
    """
    from patcher.manifest import load_manifest
    patch = next(p for p in load_manifest(REPO / "manifests" / "ffxiii.json").patches
                 if p.ui_job_id == "hdproject")

    # the names the mod's own batch file loops over, from Data\loadHD*.txt
    shipped = {"LoadHDEnvironments.txt", "LoadHDWeapons.txt", "LoadHDEidolons.txt",
               "LoadHDNPCs.txt", "LoadHDEnemy.txt", "LoadHDOther.txt", "LoadHDPCs.tx1"}
    for category in patch.categories:
        assert category["list"] in shipped, \
            f"{category['id']} points at {category['list']}, which the mod does not ship"


def test_the_tool_names_match_what_the_mod_ships():
    """
    resolved by fixed name out of SupportFiles, so a typo here is "missing from
    SupportFiles" on a folder that has it
    """
    from patcher.mods.hd_runner import TOOLS

    # XIII's HD Project, then FF XIII-2 HD, whose supportfiles has these
    # exact names (the model tool's has spaces in it)
    assert {TOOLS[k] for k in ("pack", "models", "inject")} == \
        {"ff13tool.exe", "FFXIII_ModelsHD.exe", "FFXIII_HDProject.exe"}
    assert {TOOLS[k] for k in ("pack", "models2", "inject2")} == \
        {"ff13tool.exe", "FF XIII HD Models.exe", "FFXIII2_HDProject.exe"}


def test_every_xiii2_category_points_at_what_the_mod_ships():
    """
    FF XIII-2 HD's texture folders and model lists, off the listing of the real
    download. A group it has no folder for installs nothing and says nothing.
    """
    from patcher.manifest import load_manifest
    patch = next(p for p in load_manifest(REPO / "manifests" / "ffxiii2.json").patches
                 if p.ui_job_id == "hdproject2")

    folders = {"fa", "mon", "npc", "pc", "weapon"}
    lists = {"PCModels.txt", "NPCModels.txt"}
    assert patch.nexus_game_domain == "finalfantasyxiii2" and patch.nexus_mod_id == "1"
    assert [c["id"] for c in patch.categories if c.get("zones")] == ["zones"]
    for category in patch.categories:
        assert set(category.get("groups") or []) <= folders, category["id"]
        assert category.get("models") in lists | {None}, category["id"]


@pytest.mark.parametrize("path", ["patcher/mods/hd_runner.py", "patcher/mods/hd_install.py",
                                  "patcher/mods/hd_recipe.py", "patcher/mods/hd2_install.py",
                                  "patcher/mods/hd2_zones.py", "patcher/mods/hd2_archive.py",
                                  "patcher/mods/hd2_recipe.py"])
def test_no_module_shells_out(path):
    """
    shell=True on a path the user chose is how a folder name becomes a command.
    Popen with a list has no shell to confuse, which is also why a mod folder
    with a caret in it works here and not in their .bat.
    """
    text = source(path)
    assert "shell=True" not in text
    assert "os.system" not in text
