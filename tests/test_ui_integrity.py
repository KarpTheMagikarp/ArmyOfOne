"""
Two bugs that shipped because nothing here looks at the page as a page.

The rest of the UI assertions in this suite grep for a string and pass when
they find it. None of that catches a declaration the CSS parser threw away or a
function used as a boolean. Both survived every probe and every test here.

Still source-level, because CI runs pytest and nothing else. But these look at
structure rather than substrings: comment nesting, whether a token anything
depends on exists at all, and how a name is spelled at its call site.
"""

import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "ui" / "index.html"


@pytest.fixture(scope="module")
def ui():
    return UI.read_text(encoding="utf-8")


def style_blocks(text):
    """(start offset, body) for every <style> block on the page"""
    for match in re.finditer(r"<style\b[^>]*>(.*?)</style>", text, re.S | re.I):
        yield match.start(1), match.group(1)


def comment_faults(css):
    """
    every unopened close and unclosed open, as (offset, what).

    Walks the text rather than counting, because the two failures look
    identical to a counter: "/* a */ b */" and "/* a /* b */" both have two of
    one token and one of the other.
    """
    faults, i, depth_open_at = [], 0, None
    while i < len(css):
        if depth_open_at is None and css.startswith("/*", i):
            depth_open_at = i
            i += 2
            continue
        if depth_open_at is not None and css.startswith("*/", i):
            depth_open_at = None
            i += 2
            continue
        if depth_open_at is None and css.startswith("*/", i):
            faults.append((i, "close with no open"))
            i += 2
            continue
        i += 1
    if depth_open_at is not None:
        faults.append((depth_open_at, "open never closed"))
    return faults


def test_every_css_comment_is_opened_and_closed(ui):
    """
    The one that cost the most for the least. A paragraph of prose between two
    declarations lost its opening slash-star, so the parser read it as a broken
    declaration and discarded everything up to the next semicolon. That
    semicolon belonged to --shadow, which meant --shadow was never defined and
    both rules using it silently lost their box-shadow.

    Nothing caught it: --shadow has no test, and the ramp test that sits three
    lines above it only greps for the substrings "--edge:" and "--edge-dim:",
    which were both present and fine.
    """
    problems = []
    for offset, css in style_blocks(ui):
        for at, what in comment_faults(css):
            line = ui.count("\n", 0, offset + at) + 1
            context = css[at:at + 70].replace("\n", " ")
            problems.append(f"index.html:{line}  {what}:  {context}")

    assert not problems, "unbalanced CSS comment(s):\n  " + "\n  ".join(problems)


def test_the_tokens_that_other_rules_depend_on_are_all_declared(ui):
    """
    The general form of the same bug. var(--x) with no fallback is invalid at
    computed-value time, which does not fail loudly: it throws away the WHOLE
    declaration, including any other shadow written beside it. #auth .card lost
    a hand-written 0 18px 60px as collateral for a token it only half used.
    """
    # anywhere, not just at the start of a line: #cursor.neutral declares --tx
    # and --ty inline, mid-rule. a colon is what separates a declaration from a
    # var() reference, which never has one.
    declared = set(re.findall(r"(--[\w-]+)\s*:", ui))
    # and the ones only ever written from JS, which is a real declaration as
    # far as the cascade is concerned
    declared |= set(re.findall(r"setProperty\(\s*[\"'](--[\w-]+)", ui))

    used = set(re.findall(r"var\(\s*(--[\w-]+)\s*(?:,|\))", ui))
    missing = sorted(used - declared)

    assert not missing, (
        f"used with var() but never declared: {missing}. a var() that resolves "
        "to nothing voids the entire declaration it sits in")


def test_nothing_reaches_for_an_element_that_does_not_exist(ui):
    """
    The general form of the bug that started this file. #qName was written to
    from six places and could never render, and when it was finally removed
    tools/ui_harness.js kept reading it and died on a null.

    Both halves are the same mistake: a selector and a markup id that stopped
    agreeing, with nothing in between to notice. Ids created at runtime count
    as defined, since half this UI is built in JS.
    """
    referenced = (set(re.findall(r"\$\(\"#([\w-]+)\"\)", ui))
                  | set(re.findall(r"getElementById\(\"([\w-]+)\"\)", ui)))
    defined = (set(re.findall(r"id=\"([\w-]+)\"", ui))
               | set(re.findall(r"\.id\s*=\s*\"([\w-]+)\"", ui)))

    assert referenced, "found no selectors at all, the pattern has gone stale"
    dangling = sorted(referenced - defined)
    assert not dangling, (
        f"selected but never created: {dangling}. $() returns null and the next "
        "property read throws, or worse, silently does nothing")


def test_the_footer_swap_is_keyed_so_progress_does_not_replay_it(ui):
    """
    Status.busy takes a key precisely so a download does not restart the swap
    animation five times a second. Losing the second argument at a call site
    is silent: it still works, it just flickers for the whole download.
    """
    calls = re.findall(r"Status\.busy\(", ui)
    assert calls, "nothing calls Status.busy any more"
    for match in re.finditer(r"Status\.busy\((.*?)\);", ui, re.S):
        assert "," in match.group(1), (
            f"Status.busy called with no key, so every update swaps: {match.group(0)[:90]}")


def test_no_module_function_is_used_as_a_truthy_value(ui):
    """
    `if (Mods.isOpen && !active)` reads fine and is always true: isOpen is a
    function reference, so the guard collapsed to `if (!active)` and a failed
    drop on the main menu opened a drawer nobody asked for.

    Checked against the names each revealing-module IIFE actually returns, so a
    new predicate added to one of those surfaces is covered without touching
    this test.
    """
    predicates = set()
    for members in re.findall(r"return\s*\{\s*([\w,\s]+?)\s*\}\s*;", ui):
        for name in (m.strip() for m in members.split(",")):
            if name and re.match(r"^(is|has|can|should)[A-Z]", name):
                predicates.add(name)
    assert predicates, "found no predicate on any module surface, parse broke"

    bad = []
    for name in sorted(predicates):
        # <Module>.<pred> not followed by ( or = , i.e. read rather than called
        for match in re.finditer(rf"\b(\w+)\.{name}\b(?!\s*[(=])", ui):
            line = ui.count("\n", 0, match.start()) + 1
            bad.append(f"index.html:{line}  {match.group(0)} is a function, "
                       f"so this is always truthy. did you mean {match.group(0)}()?")

    assert not bad, "\n  ".join([""] + bad)


def test_the_window_drop_listener_is_live_before_any_job_waits(ui):
    """
    Drop.build registers the window's drop listener, and nothing called it
    until a job started waiting for a file. So the "permanent drop target" that
    module documents was permanent only after the first acquire: dropping an
    archive on a freshly opened window did nothing at all, which its own
    comment calls the worst possible failure.

    build is exported for exactly this and had no callers, which is what makes
    it worth a test rather than a comment.
    """
    assert re.search(r"^\s*Drop\.build\(\);", ui, re.M), \
        "nothing calls Drop.build at boot, so a drop before the first acquire is ignored"

    body = ui[ui.index("const Drop = ("):]
    start = body.index("function build(){")
    assert "if (el) return;" in body[start:start + 500], \
        "Drop.build is exported and called at boot, so it has to refuse a second run"


def test_the_hd_panel_lives_in_the_mods_drawer(ui):
    """
    It belongs beside the mod it configures, in the drawer, not spread across
    the patch queue. Seven extra rows in the queue pushed Apply off the bottom
    and made the main menu unreadable for a mod that is not even a patch.
    """
    assert "sectHd" not in ui, "the HD rows are back in the patch queue"
    assert "#hdRows" not in ui and "#hdGo" not in ui

    body = ui[ui.index("const Mods = (() =>"):]
    assert "function buildOptions(" in body, "the panel is not in the Mods drawer"
    assert '#mdExtras .opt-row' in ui, "the drawer has no toggle styling"


def test_the_toggles_do_not_exist_until_a_mod_has_been_pointed_at(ui):
    """
    They used to render from the start, greyed, with an inert Install under
    them. Seven toggles on a mod nobody has pointed at reads as though the mod
    ships with the patcher, and it does not: it is a 6GB download from Nexus
    and none of those toggles can do anything until it is on disk.
    """
    body = ui[ui.index("function renderExtras("):ui.index("function buildOptions(")]

    assert "&& dropped" in body and "buildOptions(row, label, job)" in body, \
        "the options are built whether or not anything has been dropped"
    assert '(dropped.jobId || "hdproject") === job.id' in body, \
        "a drop for one mod would open the toggles on another"

    # and the button below them is gated on a selection, or the voices box with
    # something to copy, and the read of the game: the panel it sits in cannot
    # exist without a drop
    opts = ui[ui.index("function buildOptions("):]
    opts = opts[:opts.index("\n  function refresh(")]
    assert "install.disabled = (!chosen.size && !copying) || running || surveying > 0;" in opts


def test_a_run_locks_the_toggles(ui):
    """
    changing the selection halfway through would make the recorded state
    disagree with what the tools are actually doing
    """
    body = ui[ui.index("function buildOptions("):]
    body = body[:body.index("\n  function refresh(")]
    assert "Hd.isRunning() || blocked.has(category.id)" in body, \
        "toggles stay live during a run"


def test_the_installer_terminal_is_wired_to_both_of_its_buttons(ui):
    """
    a forty minute repack with no way to stop it, or no way to dismiss the
    pane afterwards, is worse than no terminal
    """
    body = ui[ui.index("const Term = (() =>"):]
    body = body[:body.index("const Mods = (() =>")]
    assert '$("#termStop").addEventListener' in body
    assert '$("#termClose").addEventListener' in body
    assert "cancel_hd_project" in body, "Stop does not reach the backend"


def test_the_hd_events_are_routed_where_the_backend_sends_them(ui):
    """
    shell.py emits these into window.__patch.receive. Routing them in the
    acquire receiver instead is silent: the install runs and the terminal
    stays empty.
    """
    receiver = ui[ui.index("window.__patch = {"):]
    receiver = receiver[:receiver.index("\n}")]
    assert 'event.type === "hd"' in receiver, \
        "hd events are not handled where shell.py delivers them"


def test_every_backend_call_the_ui_makes_exists_on_the_shell(ui):
    """
    The UI reaches the backend through window.pywebview.api, which is shell.py's
    Api class, and every call site guards with `if (api && api.thing)`. So a
    method that only exists on UiBridge is not a crash, it is a button that
    does nothing, which is the failure mode this file was started over.

    Three of these were exactly that: hd_categories, set_hd_categories and
    identify_drop lived on the bridge and the shell never forwarded them.
    """
    import ast

    called = set(re.findall(r"api\.([a-z_][a-z0-9_]*)\s*\(", ui))
    assert called, "found no backend calls at all, the pattern has gone stale"

    shell = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    exposed = {item.name
               for node in ast.walk(ast.parse(shell))
               if isinstance(node, ast.ClassDef)
               for item in node.body
               if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}

    missing = sorted(called - exposed)
    assert not missing, (
        f"the UI calls these and shell.py does not expose them: {missing}. "
        "every call site is guarded, so this is a silent no-op rather than an error")


def test_a_category_the_backend_refuses_cannot_be_ticked(ui):
    """
    build_plan refuses an unsupported category. If the UI lets it be ticked,
    that refusal is what the user sees after pressing Install, which is a
    worse way to learn it than the row saying so.
    """
    body = ui[ui.index("function buildOptions("):]
    body = body[:body.index("\n  function refresh(")]

    assert "c.supported === false" in body, "unsupported categories are not recognised"
    assert "blocked.has(category.id)" in body, "a blocked category can still be toggled"
    assert "!blocked.has(c.id)" in body, "a blocked category can still be on by default"
    assert "if (!blocked.has(id)) chosen.add(id)" in body, \
        "a stored selection could re-tick a blocked category"


def test_nothing_scrolls_the_whole_app_into_view(ui):
    """
    scrollIntoView walks up to the nearest scrollable ancestor. Inside the
    drawer that ancestor is the app itself, so opening the HD panel after a
    drop scrolled the entire stage sideways and left a black strip down one
    edge. Caught by measuring the drawer's position, not by reading the code.
    """
    # comments are allowed to name it; code is not
    code = "\n".join(line for line in ui.splitlines()
                     if not line.lstrip().startswith(("//", "*", "/*")))
    assert "scrollIntoView" not in code, (
        "that call is back. It scrolls an ancestor the caller does not choose; "
        "set scrollTop on the element you actually mean instead")


def test_a_stale_assets_manifest_cannot_beat_the_build(ui):
    """
    assets/manifest.json is generated and gitignored, so a source drop leaves
    whatever was there from last time. The fetched copy used to win outright,
    which is how a category unlocked in the source still read NOT YET on a
    machine whose assets folder was older than its ui folder.
    """
    body = ui[ui.index("async function loadAssets("):]
    body = body[:body.index("\n}")]

    assert "(m.built || 0) >= (FALLBACK_MANIFEST.built || 0)" in body, \
        "the fetched manifest wins however old it is"
    assert "build_ui_manifest" in body, "the warning does not say how to fix it"

    # and the inlined copy carries a stamp to compare against
    import json
    anchor = "const FALLBACK_MANIFEST = "
    start = ui.index(anchor) + len(anchor)
    depth, i = 0, start
    while True:
        if ui[i] == "{":
            depth += 1
        elif ui[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    baked = json.loads(ui[start:i + 1])
    assert isinstance(baked.get("built"), int) and baked["built"] > 0


def _fallback(text: str, drop: str = "") -> dict:
    import json

    anchor = "const FALLBACK_MANIFEST = "
    start = text.index(anchor) + len(anchor)
    depth, i = 0, start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    baked = json.loads(text[start:i + 1])
    baked.pop(drop, None)
    return baked


def test_the_generated_manifest_and_the_inlined_one_agree():
    """
    they are written by the same build step, so a disagreement means the step
    has not been run since the source changed
    """
    import json
    import subprocess
    import sys

    made = UI.parent.parent / "assets" / "manifest.json"
    before = made.read_text(encoding="utf-8") if made.is_file() else None
    # the generator also rewrites the copy inside index.html, which used to
    # make the comparison below agree with itself whatever was committed, and
    # left a tracked file edited by running the tests. Snapshot, compare, put back.
    page = UI.read_text(encoding="utf-8")
    repo = UI.parent.parent
    try:
        subprocess.run([sys.executable, str(repo / "tools" / "build_ui_manifest.py")],
                       cwd=repo, check=True, capture_output=True)
        synced = UI.read_text(encoding="utf-8")
    finally:
        UI.write_text(page, encoding="utf-8")
    # "built" is the newest mtime of the inputs, which a checkout or an unzip
    # moves without changing a thing, so it is the one field left out
    def unstamped(text):
        made = json.loads(text)
        made.pop("built", None)
        return made

    if before is not None:
        assert unstamped(made.read_text(encoding="utf-8")) == unstamped(before), \
            "assets/manifest.json is out of date; run tools/build_ui_manifest.py"
    assert _fallback(synced, drop="built") == _fallback(page, drop="built"), \
        "the FALLBACK_MANIFEST in ui/index.html is out of date; run tools/build_ui_manifest.py"

    generated = json.loads(made.read_text(encoding="utf-8"))
    generated.pop("built", None)
    assert _fallback(UI.read_text(encoding="utf-8"), drop="built") == generated


def test_zones_reach_the_page_as_installable(ui):
    """
    the toggle the user is trying to press. It is unlocked in manifests/, and
    the page reads a built copy of that, so this checks the whole chain rather
    than the source alone.
    """
    import json

    anchor = "const FALLBACK_MANIFEST = "
    start = ui.index(anchor) + len(anchor)
    depth, i = 0, start
    while True:
        if ui[i] == "{":
            depth += 1
        elif ui[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    baked = json.loads(ui[start:i + 1])

    zones = next(c for g in baked["games"] for j in g["jobs"]
                 for c in j.get("categories", []) if c["id"] == "zones")
    assert zones.get("supported") is not False, "the page still locks Environmental Zones"


def test_the_arts_nobody_is_looking_at_wait_their_turn(ui):
    """
    shell.py holds the window shut until the FIRST character art has decoded,
    and preloadArt pulled the other two down the same connection at the same
    time. Measured with tools/boot_timing.py: the gating art finished at 543ms
    with two 1.1MB siblings still in flight, and moving them behind the gate
    took the whole boot from 549ms to 157ms.
    """
    boot = ui[ui.index("loadAssets().then(() => {"):]
    boot = boot[:boot.index("\n});")]

    assert "preview(GAMES()[0].id);" in boot, "nothing shows the first art"
    # behind the window opening, and behind the decoder saying which cuts it
    # can keep up with, which is a few ms of asking and never a gate of its own
    assert "whenReady(() => cutDecode().then(preloadArt));" in boot, \
        "preloadArt is back on the critical path"
    # and not also called bare
    assert re.sub(r"//[^\n]*", "", boot).count("preloadArt") == 1

    warm = ui[ui.index("function preloadArt(){"):]
    warm = warm[:warm.index("\n}")]
    assert "g.id !== shown" in warm, "it re-fetches the one already on screen"


def test_the_ready_gate_releases_what_was_waiting_on_it(ui):
    body = ui[ui.index("function whenReady(fn){"):]
    body = body[:body.index("\n}")]
    assert "window.__uiReady" in body, "a waiter added after ready would never run"

    gate = ui[ui.index("settled.then(() => {"):]
    gate = gate[:gate.index("\n    });")]
    assert "window.__uiReady = true;" in gate
    assert "readyWaiters.splice(0)" in gate, "waiters run more than once"
    assert "try { fn(); } catch (_){}" in gate, \
        "one throwing waiter takes the rest of them with it"


def test_a_slow_boot_says_what_held_it_up(ui):
    """
    "it takes a while to load" is unarguable and unactionable from the other
    end of a bug report. shell.py already prints __diag to the terminal.
    """
    body = ui[ui.index("function bootTimings(){"):]
    body = body[:body.index("\n}")]

    assert "getEntriesByType(\"resource\")" in body
    assert "diag(" in body, "the numbers never leave the page"
    assert "rows.slice(0, 3)" in body, "it does not name what finished last"
    assert "catch (_){" in body, "a diagnostic that can fail a boot"

    # and it is called from the gate, so it measures the real wait
    gate = ui[ui.index("settled.then(() => {"):]
    assert "bootTimings();" in gate[:gate.index("\n    });")]
