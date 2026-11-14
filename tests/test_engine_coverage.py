"""
Every patch type in every shipped manifest must be executable by the engine.

This exists because archive_inject shipped wired into bridge.install_dropped
but never registered in run_patches, so a normal Apply run died with
"unknown patch type 'archive_inject'". Every test passed, because they all
exercised the drop path and none of them ran a real manifest through the
engine.
"""

import json
import os
import re
import struct
import tempfile
from pathlib import Path

import pytest

from patcher.engine import PatchStatus, run_patches
from patcher.manifest import VALID_TYPES, load_manifest

MANIFESTS = Path(__file__).resolve().parent.parent / "manifests"


def fake_pe():
    header = bytearray(0x80)
    struct.pack_into("<I", header, 0x3C, 0x80)
    return bytes(header) + b"PE\x00\x00" + bytes(20) + b"pad"


def fake_install(manifest):
    root = Path(tempfile.mkdtemp())
    exe = root / manifest.executable_relative_path
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(fake_pe())
    return root


def offline(url):
    raise OSError("no network in tests")


@pytest.mark.parametrize("name", ["ffxiii", "ffxiii2", "ffxiii3"])
def test_no_shipped_patch_hits_the_unknown_type_branch(name):
    """the exact failure from the live run"""
    manifest = load_manifest(MANIFESTS / f"{name}.json")
    root = fake_install(manifest)

    results = run_patches(root, manifest, {p.id for p in manifest.patches}, fetch=offline)

    unknown = [r for r in results if "unknown patch type" in (r.detail or "")]
    assert not unknown, [f"{r.patch_id}: {r.detail}" for r in unknown]


@pytest.mark.parametrize("name", ["ffxiii", "ffxiii2", "ffxiii3"])
def test_dry_run_describes_every_shipped_patch(name):
    """the dry-run branch has its own type dispatch and can drift separately"""
    manifest = load_manifest(MANIFESTS / f"{name}.json")
    root = fake_install(manifest)

    results = run_patches(root, manifest, {p.id for p in manifest.patches},
                          fetch=offline, dry_run=True)

    unknown = [r for r in results if "unknown patch type" in (r.detail or "")]
    assert not unknown, [f"{r.patch_id}: {r.detail}" for r in unknown]


def test_every_declared_type_is_dispatchable():
    """
    a type can be added to VALID_TYPES and to a manifest without the engine
    learning to run it. asset_pack is the one known exception: it is a
    placeholder that deliberately reports SKIPPED.
    """
    from patcher.manifest import GameManifest, Patch

    root = Path(tempfile.mkdtemp())
    (root / "game.exe").write_bytes(fake_pe())

    required = {
        "binary_patch": dict(operation="flip_laa_flag", target_relative_path="game.exe"),
        "file_install": dict(github_repo="a/b", install_relative_path="."),
        "nexus": dict(nexus_game_domain="g", nexus_mod_id="1", install_relative_path="."),
        "direct": dict(download_url="https://x.test/a.zip", install_relative_path="."),
        "archive_inject": dict(archive_relative_dir="data/sys",
                               injections=[{"game_path": "a/b.imgb", "source": "*b.imgb"}]),
        "bundled_install": dict(install_relative_path=".",
                                bundled_files=[{"source": "assets/nope.bin", "target": "nope.bin"}]),
        "manual": dict(manual_url="https://x.test/page"),
        "asset_pack": dict(),
    }
    assert set(required) == VALID_TYPES, "a new patch type needs a case here"

    for patch_type, extra in required.items():
        patch = Patch(id="p", label="P", type=patch_type, default_enabled=True, **extra)
        manifest = GameManifest(
            game_id="g", display_name="G", manifest_version="v1",
            executable_relative_path="game.exe", patches=[patch])

        results = run_patches(root, manifest, {"p"}, fetch=offline)

        assert len(results) == 1
        assert "unknown patch type" not in (results[0].detail or ""), patch_type
        if patch_type == "asset_pack":
            assert results[0].status == PatchStatus.SKIPPED


def test_no_user_facing_string_contradicts_the_acquisition_flow():
    """
    the live log showed "opening the mod page. download it, then drop the
    file on this window" while the UI was running the catch-then-fallback
    prompts. two sources of truth telling the user different things.
    """
    import re

    stale = ("drop it on this window", "drop the file on this window",
             "drop it here", "then drop the file")
    root = Path(__file__).resolve().parent.parent
    # acquire.py owns the staged prompts and is allowed to mention dropping,
    # because that is its fallback stage
    for path in (root / "patcher").rglob("*.py"):
        if path.name == "acquire.py":
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in stale:
            assert phrase not in text, f"{path.name} still says {phrase!r}"


def test_the_apply_run_never_summons_a_sign_in():
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
    run_body = ui[ui.index("async function run(){"):ui.index("go.addEventListener")]
    for forbidden in ("Auth.needed", "Auth.open", "nexus_sign_in", "nexus_set_key",
                      "nexus_open_key_page", "window.prompt"):
        assert forbidden not in run_body, f"Apply still reaches for {forbidden}"


def test_archive_inject_never_asks_the_user_for_anything():
    """
    the payload is bundled, so an install with no network and no interaction
    must still reach the archives. it fails here only because the fixture has
    no game archives to write into, which is a different thing from asking.
    Leviathan's Tears was the one, and tests.rain has it as it was
    """
    from tests import rain

    manifest = rain.manifest()
    root = fake_install(manifest)

    results = run_patches(root, manifest, {"install_leviathans_tears"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "install_leviathans_tears")

    assert result.status != PatchStatus.NEEDS_FILE
    assert result.status != PatchStatus.NEEDS_WEBSITE
    assert "no language archives" in result.detail


def read_ui_text():
    return (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def without_comments(source):
    """
    js with the // comments stripped, for assertions about ORDER.

    These tests keep tripping over their own explanations: a comment saying
    "back() would also fire" makes an index() check find back() before the
    call it is describing.
    """
    import re
    return re.sub(r"//[^\n]*", "", source)


def test_the_acquisition_flow_is_actually_reachable_from_a_run():
    """
    Acquisition and Drop.request were both built, tested and orphaned: nothing
    called either, so a needs_file outcome ended on a DROP IT chip with no
    page opened, no timer, and no picker on screen. Same shape of bug as
    archive_inject never being registered in the engine.
    """
    ui = read_ui_text()
    run_body = ui[ui.index("async function run(){"):ui.index("go.addEventListener")]
    assert "Drop.acquire" in run_body, "a run must drive the staged acquisition"
    assert "needsFile" in run_body


def test_the_shell_exposes_what_the_acquisition_needs():
    shell = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    for method in ("begin_acquire", "cancel_acquire", "pick_file", "supply_file"):
        assert f"def {method}" in shell, f"shell.py is missing {method}"
    assert "Acquisition(" in shell, "shell.py never constructs an Acquisition"
    assert "DownloadWatcher(" in shell


def test_the_manual_picker_is_the_primary_escape_hatch():
    """
    WebView2 strips the filesystem path off dragged files in some
    configurations, and a drop that silently does nothing is the worst
    failure available. the picker always works.
    """
    ui = read_ui_text()
    assert 'id="askBrowse"' in ui
    assert "Press Choose file" in ui
    assert "GRACE_MS" in ui
    # the grace period has to be visible in the UI, not only in acquire.py
    assert "18000" in ui


def test_a_rar_gets_a_folder_route_in_the_ui():
    """
    Leviathan's Tears ships as a .rar. when bsdtar cannot read it, the only
    useful instruction is "extract it and point us at the folder", so the UI
    has to offer that rather than dead-ending on a file picker.
    """
    ui = read_ui_text()
    assert 'id="askFolder"' in ui
    assert "function offerFolder" in ui
    assert "pick_folder" in ui and 'browse("folder")' in ui

    shell = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    assert "def pick_folder" in shell
    assert "FOLDER_DIALOG" in shell


def test_the_audio_rows_that_could_not_be_automated_are_gone():
    """
    LR's was a Windows sound setting, not a patch. XIII's was a cosmetic FMV
    upgrade behind a Nexus login. Neither belongs in a one-click tool.
    XIII-2's stays: buzzing and muffled in-game voices are broken gameplay.
    """
    import json
    root = Path(__file__).resolve().parent.parent
    jobs = json.loads((root / "ui" / "jobs.json").read_text(encoding="utf-8"))
    by_game = {g["id"]: {j["id"] for j in g["jobs"]} for g in jobs["games"]}

    assert "audiofix" not in by_game["ff13"]
    assert "audiofix" not in by_game["lr"]
    assert "audio" in by_game["ff13-2"]

    for name, missing in (("ffxiii", "install_cutscene_audio"),
                          ("ffxiii3", "install_lr_cutscene_audio")):
        manifest = load_manifest(MANIFESTS / f"{name}.json")
        assert all(p.id != missing for p in manifest.patches)


def test_every_progress_callback_uses_one_shape():
    """
    two call styles existed: Progress objects from download.py, and a bare
    (done, total, label) from the newer appliers. the bridge only handled the
    first, so a bundled install died with
    "report() takes from 1 to 2 positional arguments but 3 were given".
    """
    import re

    root = Path(__file__).resolve().parent.parent / "patcher"
    offenders = []
    for path in root.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            call = re.search(r"\bon_progress\(([^)]*)\)", line)
            if not call or "on_progress=" in line:
                continue
            args = call.group(1)
            if args and "Progress(" not in args:
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, offenders


def test_a_bundled_install_reaches_the_bridges_progress_handler(stub_bundle, tmp_path):
    """
    end to end through the real bridge, which is where the TypeError actually
    surfaced. the unit tests all passed because none of them used the bridge's
    own callback.
    """
    import json
    import shutil

    import patcher.bridge as bridge_mod
    from patcher.bridge import UiBridge

    manifest = load_manifest(stub_bundle.manifest("ffxiii2"))
    library = tmp_path / "lib"
    install = library / "steamapps" / "common" / "FFXIII2"
    exe = install / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(fake_pe())
    (library / "steamapps" / "appmanifest_292140.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"292140"\n\t"installdir"\t\t"FFXIII2"\n}\n')

    import pytest as _pytest
    monkey = _pytest.MonkeyPatch()
    monkey.setattr(bridge_mod, "state_path_for", lambda gid: tmp_path / ".state" / f"{gid}.json")
    try:
        bridge = UiBridge(manifest_paths={"ffxiii2": stub_bundle.manifest("ffxiii2")},
                          steam_library_paths=[library])
        events = []
        bridge.apply("ff13-2", ["ff132fix"], on_event=events.append)
    finally:
        monkey.undo()

    done = [e for e in events if e.get("state") == "done"]
    assert done, events
    assert done[0]["status"] == PatchStatus.APPLIED, done[0]["detail"]
    assert (exe.parent / "d3d9.dll").is_file()

    progress = [e for e in events if e["type"] == "progress"]
    assert progress, "the bundled install produced no progress events"
    for event in progress:
        assert {"stage", "done", "total", "fraction"} <= set(event)


def test_ff13fix_is_gone_from_every_manifest():
    """NovaFix is explicitly incompatible with it; both own d3d9.dll"""
    for name in ("ffxiii", "ffxiii2", "ffxiii3"):
        manifest = load_manifest(MANIFESTS / f"{name}.json")
        assert all(p.id != "install_ff13fix" for p in manifest.patches)
        assert any(p.id == "install_novafix" for p in manifest.patches), name


def test_the_proxy_actively_supersedes_the_old_one():
    """
    NovaFix disables its own overlapping features when it detects FF13Fix, so
    ceasing to install it is not enough: the old files have to go.
    """
    for name in ("ffxiii", "ffxiii2"):
        manifest = load_manifest(MANIFESTS / f"{name}.json")
        proxy = next(p for p in manifest.patches if p.id == "install_novafix")
        assert "install_ff13fix" in proxy.replaces, name


def test_the_audio_fix_lives_in_the_addons_folder():
    manifest = load_manifest(MANIFESTS / "ffxiii2.json")
    audio = next(p for p in manifest.patches if p.id == "install_audio_fix")
    assert audio.install_relative_path.endswith("addons/xiii2-audio")
    assert "install_novafix" in audio.requires
    # the chain rename only existed because two proxies shared a filename
    assert all("chain_existing_as" not in f for f in audio.bundled_files)


def test_the_ui_offers_settings_launch_and_the_addons_folder():
    ui = read_ui_text()
    # both are built in JS, so the authored body markup stays untouched
    assert 'toolTab("nvTab", "Settings"' in ui and "novafix_settings" in ui
    assert 'wrap.id = "launchWrap"' in ui and "launch_game" in ui
    assert "#nv{position:absolute;top:0;right:0" in ui, "the drawer comes from the right"
    assert "open_addons" in ui
    shell = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    for method in ("open_addons", "novafix_settings", "save_novafix_settings", "launch_game"):
        assert f"def {method}" in shell, method


def test_the_drawers_live_inside_the_scaled_stage():
    """
    #app is the stage, scaled by zoom, so anything appended to the body
    instead sits outside it: pinned to the window while the UI beside it is
    scaled, and stranded in the dead margin back when windows letterboxed.
    Both drawers spent a long time like that, which is what put the tabs out in
    the black bars in the first place.
    """
    ui = without_comments(read_ui_text())
    for panel in ("#nv{", "#md{"):
        block = ui[ui.index(panel):ui.index("}", ui.index(panel))]
        assert "position:absolute" in block, f"{panel} is not anchored to the stage"
    assert ui.count("app.appendChild(panel);") == 2
    assert 'document.body.appendChild(panel)' not in ui
    # and the bar that opens them to #frame, not to the body. Not #stage either:
    # that starts a rule's width under the help bar and clips, which left a
    # pixel of art between the bar and the tabs
    assert '$("#frame").appendChild(bar);' in ui
    bar = ui[ui.index("#toolBar{"):ui.index("}", ui.index("#toolBar{"))]
    assert "top:var(--help-h)" in bar and "height:var(--help-h)" in ui
    assert 'document.body.appendChild(tab)' not in ui


def test_nothing_in_the_stage_draws_a_second_cursor():
    """
    #app hides the system cursor and draws the glove instead, so a control in
    there asking for cursor:pointer gets both at once. It went unnoticed while
    the drawers hung off the body, where there is no glove, and turned into
    fifteen elements the moment they moved inside the stage.

    Scoped to the two blocks that hold everything inside #app. The base sheet's
    own cursor:pointer rules are #auth and #ask, which really do live on the
    body.
    """
    import re
    ui = read_ui_text()
    for block in ("novafix-style", "mods-style"):
        css = re.search(rf'<style id="{block}">(.*?)</style>', ui, re.S).group(1)
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        assert "cursor:" not in css, f"{block} overrides the glove"


def test_the_drawers_are_opaque():
    """
    --panel-hard is 92%, which reads as frosted over the art and as ghosted
    text over the help and footer bars the drawers now cross. Blur is the
    prettier fix and is banned for good reason, see
    test_no_backdrop_filter_anywhere, so opaque is the one that is left.
    """
    ui = without_comments(read_ui_text())
    for panel in ("#nv{", "#md{"):
        block = ui[ui.index(panel):ui.index("}", ui.index(panel))]
        assert "background:#05070a" in block, f"{panel} lets the bars through"


def test_the_mods_footer_stays_on_screen_with_the_hd_categories_open():
    """
    Worth getting took its full height whatever the drawer had room for, so
    with the HD Project's seven categories and Install open on a 720 stage the
    footer, status line and Close, hung 15px under the window, 47 with the
    voices row. Then it scrolled on its own above an Installed that scrolled
    on its own. Now everything between the header and the footer is one
    scroller that gives way, and neither list scrolls by itself.
    """
    ui = without_comments(read_ui_text())
    body = ui[ui.index("#mdBody{"):ui.index("}", ui.index("#mdBody{"))]
    assert "flex:1" in body and "min-height:0" in body and "overflow-y:auto" in body
    for inner in ("#mdExtras{", "#mdList{"):
        rule = ui[ui.index(inner):ui.index("}", ui.index(inner))]
        assert "overflow" not in rule and "flex" not in rule, rule
    assert "container-type" not in ui[ui.index("#mdList{"):ui.index("#mdList .mod{")]
    markup = ui[ui.index('<div id="mdBody">'):ui.index("<footer>", ui.index('<div id="mdBody">'))]
    assert markup.index('id="mdZone"') < markup.index('id="mdExtras"') < markup.index('id="mdList"')


def test_the_stage_scales_then_fills_the_window():
    """
    A maximised window goes edge to edge, the way an app's does. fit() takes
    the zoom that fits 1280x720 and gives the stage the rest of the window's
    shape in its own px, so nothing is stretched: the lists hold left, the art
    and drawers right, the bars run across and the clips cover.

    This reverses test_the_stage_scales_and_never_stretches, which letterboxed
    a maximised window on near black. The objection that retired the first fill
    was a 2000x805 window dragged into any shape spreading the menu down the
    left; shell.py holds a drag 16:9 now, so the extra only comes from a
    screen's own shape. And only between 3:2 and 12:5: a 32:9 screen cropped
    the clip at Lightning's eyes and a 4:3 one cut the art's fade to a seam, so
    those get even bars.

    Sized off the visual viewport to the fraction: at 125% innerHeight rounds a
    1369 pixel client to 1095 css px and the stage would stop short.
    """
    ui = without_comments(read_ui_text())
    fit = ui[ui.index("function fit(){"):ui.index("addEventListener(\"resize\", fit)")]
    assert "const ASPECT = [3 / 2, 12 / 5];" in ui
    assert "const sw = px(Math.min(w, h * ASPECT[1])), sh = px(Math.min(h, w / ASPECT[0]));" in fit
    # a pinch on a touchpad magnifies, it doesn't shrink the stage to what's in view
    assert "vv.width * vv.scale" in fit and "vv.height * vv.scale" in fit
    assert "SCALE = Math.min(sw / BASE_W, sh / BASE_H, MAX_SCALE);" in fit
    assert "STAGE_W = sw / SCALE; STAGE_H = sh / SCALE;" in fit
    assert "app.style.zoom = SCALE" in fit
    assert 'app.style.width = STAGE_W + "px"' in fit and 'app.style.height = STAGE_H + "px"' in fit
    # the bars, when there are any, split evenly on whole device pixels
    assert 'app.style.left = half(w - sw) / SCALE + "px";' in fit
    assert 'app.style.top = half(h - sh) / SCALE + "px";' in fit
    assert "visualViewport" in fit
    assert "Grid.soon()" in fit
    app = ui[ui.index("#app{"):ui.index("}", ui.index("#app{"))]
    assert "position:absolute;left:0;top:0;" in app
    # nothing centres it in css any more: fit() places it
    assert "display:flex;align-items:center;justify-content:center;" not in ui[:ui.index("#app{")]
    # and the art shrinks into its side on a taller stage rather than losing its fade
    art = ui[ui.index(".art img{"):ui.index("}", ui.index(".art img{"))]
    assert "max-width:100%" in art


def test_grid_puts_every_label_on_whole_device_pixels():
    """
    At a fractional zoom a box's face snaps to the pixel grid as it's painted,
    its label's baseline rounds on its own, and a sprite off a fractional top is
    drawn one of two ways depending on the fraction. A maximised 2560x1369
    window had the patch rows' labels anywhere from +0.08 to -0.84px off the
    middle of their boxes, a different way per row. Grid rounds the layout the
    way WPF does; tools/center_probe.js reads it back off the pixels at 19
    sizes, and these pin what makes it exact rather than close.
    """
    ui = without_comments(read_ui_text())
    grid = ui[ui.index("const Grid = (() => {"):ui.index("function fit(){")]
    for box in ('box: ".row"', '"#go, #launch, #btnAbout, #btnCredits"', '".tool-tab.on"',
                '"#foot"', '"#gauge"', '"#nv .sel"', '"#mdExtras .opt-row"', '"#md footer > button"',
                '"#ask button"', '"#termStop"'):
        assert box in grid, f"{box} isn't on the grid"
    # the face goes onto a whole pixel by its layout top: a translate would
    # anti-alias its edges
    assert "lift(box, m, job.dy);" in grid
    # and going relative for that top doesn't wake up a left or right meant for
    # another case, the way the patch rows' checkbox indent did on the HD
    # categories' boxes
    lift = grid[grid.index("const lift = (el, m, d) => {"):]
    lift = lift[:lift.index("};")]
    assert 'el.style.left = el.style.right = "auto";' in lift
    # an i or a checkbox in a flex row moves by its margins: relative would
    # make it the anchor for the caveat bubble that spans the row
    assert "el.style.marginTop = lp(laid(em.mt * D) + d);" in grid
    assert "el.style.marginBottom = lp(laid(em.mb * D) - d);" in grid
    # text paints at its rounded baseline plus its rounded translate, so a
    # label's move is whole pixels or its fraction is thrown away
    assert "lm.base - Math.round(unit(b))" in grid
    # a glyph word onto a whole pixel before it's centred: off one, a sprite
    # draws one of two ways depending on the fraction
    assert 'job.moves.push([el, gm, gm.T - at, "lift"]);' in grid
    # and it really lands there: a length is written on blink's own 64ths and a
    # 256th further from zero, since blink cuts toward zero and a float a hair
    # short put an exact whole pixel at .985, which drew the word 0.41px low.
    # A 256th up rather than out left every upward lift a 64th short
    assert 'const lp = v => (unit(v) + Math.sign(unit(v)) / 256) / D + "px";' in grid
    # and every length it reads is what blink laid out: the typed OM's float,
    # not getComputedStyle's six figures, cut toward zero to the 64th. Frame
    # Rate's padding taken as written put its baseline at 291.486 where blink
    # had 291.5, and blink drew it a pixel lower than Grid had it
    assert 'el.computedStyleMap && el.computedStyleMap().get(prop)' in grid
    assert "const laid = v => Math.sign(v) * Math.floor(Math.abs(v) * 64 + 1e-3) / 64;" in grid
    assert "const px = (el, prop) => laid(css(el, prop) * D);" in grid
    assert 'const b = lr.bottom * dpr() - px(el, "padding-bottom") - edge(el, "Bottom")' in grid
    assert 'r.height * dpr() - px(box, "padding-bottom") - edge(box, "Bottom");' in grid
    # but a border as it's drawn, never off the typed OM: Chromium 153 gives a
    # border-style none its computed width, medium, 3px, and every row's
    # missing bottom border put the user's labels 3px low and glyph words 3px high
    edge = grid[grid.index("const edge = (el, side) => {"):]
    edge = edge[:edge.index("};")]
    assert "/^(none|hidden)$/.test(cs[`border${side}Style`]) ? 0" in edge
    assert "computedStyleMap" not in edge
    assert 'const insets = (el, face) => face === "padding" ? [edge(el, "Top"), edge(el, "Bottom")] : [0, 0];' in grid
    assert 'from = cr.top * dpr() + edge(cb, "Top") + rides(cb);' in grid
    for read in ('"border-top-width"', '"border-bottom-width"', "borderTopWidth", "borderBottomWidth"):
        assert read not in grid.replace("border${side}Width", ""), read
    # PATCH QUEUE sits where its CSS puts it, on purpose: Grid centring it
    # moved it half a pixel and softened it, and lifting its row opened a
    # pixel above the gauge's at 164 of 1023 window sizes. Nothing in Grid
    # touches the strip, and the gauge's row takes up whatever Grid makes of
    # the footer under it, so the strip's top stays where the footer's own
    # 36px put it
    assert ".qrow-label" not in grid and ".qrow-bar" not in grid and ".qlabel" not in grid
    assert "#frame{--sb-h:40px;--foot-h0:36px;--foot-h:var(--foot-h0)}" in ui
    assert "padding-bottom:calc(5px + var(--foot-h0) - var(--foot-h))}" in ui
    # a row's label is its trimmed box: the name inside is an inline span whose
    # box runs to the descent, which read as wrapped and was left alone
    assert 'text: b => [b.querySelector(b.matches(".opt") ? ".label > span:not(.box)" : ".label")],' in grid
    # measured heights go back onto the 64ths before they're rounded: 16.4999
    # out of a float32 rect, where blink had 16.5, rounded the other way
    assert "const c = T + Math.round(unit(r.height * dpr() - bt - bb)) / 2;" in grid
    # half a pixel of hysteresis, or a box sitting on a half flips every pass
    assert "Math.abs(v - was) < .55 ? was" in grid
    # sizes before positions: a checkbox centred by flex moves when it's resized
    assert grid.index("host.style.setProperty(name, lp(h));") < grid.index("const plan = [];") \
        < grid.index("for (const job of plan){")
    # the strip and the sign-in window live on body, outside the zoom
    assert "const zoomOf = el => (app.contains(el) ? SCALE : 1) * dpr();" in grid
    assert ".observe(document.body," in grid
    # what a pass applied survives a resize to another zoom as the same css px
    mine = grid[grid.index("const mine = el => {"):]
    mine = mine[:mine.index("};")]
    assert "m[k] *= D / m.at" in mine and "m.T = m.base = m.pt = undefined;" in mine
    # a top is laid out afresh off its css length at the new zoom, so it's cut
    # the way blink cuts it rather than scaled as a float
    assert 'for (const k of ["dy", "top"]) if (m[k]) m[k] = laid((m[k] + Math.sign(m[k]) / 256) * D / m.at);' in mine
    # where two face heights fit equally, the smaller unless the other is
    # clearly nearer: picking the nearer flipped on layout noise and seven HD
    # rows walked by fourteen, and keeping the last one made it depend on history
    face = grid[grid.index("function faceH(nat, c){"):]
    face = face[:face.index("\n  }")]
    assert "gap(b) < gap(a) - .1 ? b : a" in face
    # a move a hair off what blink already has isn't written, or it swaps
    # between its 64ths every pass
    assert "const worth = (d, was) => was === undefined || Math.abs(unit(d) - unit(was)) > 1e-6;" in grid
    assert "if (!worth(top, hadTop) && !worth(bottom, hadBottom)) continue;" in grid
    # a padded box's natural height off the padding it has as laid out, not the
    # sum of asks or the css: the select's 4px came to 5.6056 where blink had
    # 5.5938, and every pass wrote a height a 64th off the last
    assert "- (nowT + nowB - laid(m.pt * D) - laid(m.pb * D));" in grid
    assert "const top = laid(m.pt * D) + m.shift, bottom = Math.max(0, laid(m.pb * D) + add - m.shift);" in grid
    # and measured again once a resized checkbox or i has had its say
    assert "if (size(items)) size(items);" in grid
    # a checkbox's or an i's own size is its css one: off its box at the first
    # zoom it read 15.9997, which the next zoom floored to an i two pixels
    # smaller than a fresh load gave it
    assert 'const es = getComputedStyle(el), own = css(el, "height");' in grid
    assert "em.n = own || el.getBoundingClientRect().height * dpr() / D;" in grid
    assert "const a = Math.floor(want + 1e-6);" in grid
    # a [hidden] i reads as no size: it's measured once it's drawn
    assert "if (!el.getClientRects().length) continue;" in grid
    # an empty or wrapped note gives back whatever it was moved by as one line
    assert "if (lm.tr) job.moves.push([el, lm, 0]);" in grid
    # something mid transform sits a pass out and goes again when it lands
    assert "a.finished.then(() => Grid.soon(), () => Grid.soon());" in grid
    # it reads the caps off a canvas in the real face, so not before the face is in
    ready = ui[ui.index("document.fonts.ready.then("):]
    assert ready.index("centreText();") < ready.index("Grid.start();")


def test_grid_only_wakes_for_what_can_move_a_box():
    """
    A pass is a couple of milliseconds of forced layout, and the review found
    it running on every frame of an HD install: the log's own scroll, and the
    progress bar having .waiting taken off on every tick, a class it didn't
    have. Idle is no passes at all, and a run is a handful.
    """
    ui = without_comments(read_ui_text())
    grid = ui[ui.index("const Grid = (() => {"):ui.index("function fit(){")]
    assert '"#termOut, #tip, #vdiag, #hintText, #cursor, #cursorShade, #artStage"' in grid
    # an attribute set to what it was is no change
    assert "r.oldValue !== r.target.getAttribute(r.attributeName)" in grid
    # a label's words changing in place, when it can't wrap
    assert "/^(nowrap|pre)$/.test(getComputedStyle(r.target).whiteSpace)" in grid
    # the scrolls that matter are the lists', not the log's
    assert 'document.addEventListener("scroll", e => { if (loud(e)) Grid.soon(); }, true);' in grid
    pct = ui[ui.index("#termPct{"):ui.index("}", ui.index("#termPct{"))]
    assert "white-space:nowrap" in pct


def synthetic_xiii(image_size, text_size, laa=False, tag=b""):
    """a PE carrying the header fields NovaFix fingerprints builds on"""
    dos = bytearray(0x80)
    struct.pack_into("<I", dos, 0x3C, 0x80)
    coff = bytearray(20)
    struct.pack_into("<HHI", coff, 0, 0x014C, 1, 0x545C8A57)
    struct.pack_into("<H", coff, 16, 224)
    struct.pack_into("<H", coff, 18, 0x0002 | (0x0020 if laa else 0))
    optional = bytearray(224)
    struct.pack_into("<H", optional, 0, 0x10B)
    struct.pack_into("<I", optional, 56, image_size)
    section = bytearray(40)
    section[0:5] = b".text"
    struct.pack_into("<I", section, 8, text_size)
    return (bytes(dos) + b"PE\x00\x00" + bytes(coff) + bytes(optional)
            + bytes(section) + b"\x00" * 64 + tag)


COMMUNITY = (38313984, 13185024)
ORIGINAL_CEG = (38899712, 13181582)


def run_xiii_laa(tmp_path, exe_bytes):
    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(exe_bytes)
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    return exe, next(r for r in results if r.patch_id == "flip_laa_flag")



def test_the_patched_executable_still_matches_novafixs_layout_check(tmp_path):
    """
    NovaFix's SameLayout() compares machine, optional-header magic, SizeOfImage
    and .text size. an LAA flip touches only Characteristics, so a patched
    executable still matches and keeps full capabilities. this asserts our
    patch really does leave those four alone.
    """
    from patcher.patch_ops import flip_laa_flag, read_fingerprint

    before = synthetic_xiii(*ORIGINAL_CEG)
    after = flip_laa_flag(before)

    left, right = read_fingerprint(before), read_fingerprint(after)
    for field in ("machine", "magic", "image_size", "text_size"):
        assert left[field] == right[field], f"{field} changed, NovaFix would not match it"
    assert before != after, "the patch has to change something"


def test_all_three_games_are_fully_handled_by_this_tool_alone():
    """
    nothing in a default run depends on another launcher or mod manager.
    every required job is something this tool does itself.
    """
    import json
    jobs = json.loads((MANIFESTS.parent / "ui" / "jobs.json").read_text(encoding="utf-8"))
    kinds = {(g["id"], j["id"]): j.get("kind", "required")
             for g in jobs["games"] for j in g["jobs"]}

    for name in ("ffxiii", "ffxiii2", "ffxiii3"):
        manifest = load_manifest(MANIFESTS / f"{name}.json")
        required = [p for p in manifest.patches
                    if kinds.get((manifest.ui_id, p.ui_job_id)) == "required"]
        assert required, name
        for patch in required:
            assert patch.type in ("binary_patch", "bundled_install", "file_install",
                                  "direct", "archive_inject"), f"{name}: {patch.id}"


def test_only_xiii_gets_a_xiii_build_name(tmp_path):
    """
    the fingerprint table is XIII's. a XIII-2 executable whose sizes happened
    to collide would otherwise be labelled with a confidently wrong name.
    """
    manifest = load_manifest(MANIFESTS / "ffxiii2.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.APPLIED
    assert "CEG" not in (result.detail or "")


def test_mods_can_be_installed_by_drop_or_by_picker():
    """
    drop-only is an invisible wall: plenty of people have never dragged a file
    into an application. both routes have to exist and both have to end in the
    same sieve.
    """
    ui = read_ui_text()
    assert 'toolTab("mdTab", "Mods"' in ui
    assert 'id="mdZone"' in ui and 'id="mdPick"' in ui and 'id="mdPickDir"' in ui

    # every route ends in the same sieve. They used to end in three different
    # ones: the picker went straight to install_addon, so choosing the HD
    # Project's own zip came back "No compatible mod files found in archive".
    assert 'browse("file")' in ui and 'browse("folder")' in ui, "no picker route"
    assert "Mods.route(path)" in ui, "the global drop does not use the sieve"
    assert ui.count("async function route(path, onlyHd, tries = 0, game = null)") == 1
    # one entry point, so the routes cannot drift apart
    assert ui.count("api.install_addon(game, path)") == 1

    shell = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    assert "def pick_file" in shell and "def pick_folder" in shell
    for extension in (".zip", ".7z", ".rar"):
        assert extension in shell, f"the picker does not offer {extension}"


def test_every_game_knows_its_steam_appid():
    from patcher.bridge import UiBridge

    bridge = UiBridge()
    assert bridge.steam_app_id("ff13") == "292120"
    assert bridge.steam_app_id("ff13-2") == "292140"
    assert bridge.steam_app_id("lr") == "345350"
    assert bridge.steam_app_id("nope") is None


def test_an_appid_means_steam_and_there_is_no_second_condition():
    """
    This has now been got wrong twice and both times a real install came back
    with Application load error 3:0000065432, so the rule is written down as a
    rule rather than as a derivation.

    First attempt: the DRM stub only needs to know which app it is, since Steam
    puts SteamAppId and SteamGameId in the environment of the process it
    launches. Supplied both, client running, no change.

    Second attempt: the CEG wrapper is what forces the Steam route, so the
    community replacement executable, which has no wrapper, can go direct.
    Same error. An unwrapped executable is still a Steam app. "Can be edited"
    and "can be launched on its own" are different properties, and only the
    first one is about the wrapper.

    The only install that goes direct is one Steam cannot serve at all.
    """
    import inspect
    from pathlib import Path as P

    from patcher import launch

    exe = P("E:/g/white_data/prog/win/bin/ffxiiiimg.exe")
    assert launch.plan("292120", exe, windows=True).route == launch.STEAM
    assert launch.plan("292140", exe, windows=True).route == launch.STEAM
    assert launch.plan("345350", None, windows=True).route == launch.STEAM
    # no appid is the one and only direct case
    assert launch.plan(None, exe, windows=True).route == launch.DIRECT
    assert launch.plan("292120", exe, windows=False).route == launch.DIRECT

    # nothing about the executable may re-enter the decision. this is the guard
    # that fails the next time somebody reaches for the same idea.
    assert list(inspect.signature(launch.plan).parameters) == [
        "app_id", "executable", "windows"]

    src = (Path(__file__).resolve().parent.parent / "patcher" / "launch.py").read_text(encoding="utf-8")
    body = src[src.index("def plan("):src.index("def shows_launcher(")]
    for banned in ("SteamAppId", "SteamGameId", "ceg", "safe_to_edit", "identify_xiii_build"):
        assert banned not in body, f"{banned} is back in the routing decision"


def steam_library(tmp_path, ui_id, exe_bytes):
    """a library steam recognises, holding one game with the exe we hand it"""
    names = {"ff13": "ffxiii", "ff13-2": "ffxiii2", "lr": "ffxiii3"}
    manifest = load_manifest(MANIFESTS / f"{names[ui_id]}.json")
    library = tmp_path / ui_id
    install = library / "steamapps" / "common" / "game"
    target = install / manifest.executable_relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(exe_bytes)
    (library / "steamapps" / f"appmanifest_{manifest.steam_app_id}.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"%s"\n\t"installdir"\t\t"game"\n}\n' % manifest.steam_app_id
    )
    return library


def test_nothing_reads_the_executable_to_decide_how_to_launch(tmp_path):
    """
    The bridge used to answer "does this exe carry CEG" so the launch could
    skip Steam when it did not. That question is real and the answer was right;
    it was the wrong question, and a real install paid for it twice. Gone, so
    the only thing the launch looks at is whether Steam sells the game.

    The fingerprint itself stays where it belongs: patch_ops still uses it to
    decide whether the 4GB patch is safe to write, which is what it was for.
    """
    from patcher.bridge import UiBridge
    from patcher.patch_ops import identify_xiii_build

    assert not hasattr(UiBridge(), "executable_has_ceg")

    shell_src = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    body = shell_src[shell_src.index("    def launch_game("):shell_src.index("    def _silence_launcher(")]
    assert "executable_has_ceg" not in body
    assert "launch.plan(app_id, target, windows=sys.platform == \"win32\")" in body

    # and the build table is untouched, because the 4GB guard still needs it
    assert identify_xiii_build(synthetic_xiii(*COMMUNITY))["safe_to_edit"] is True
    assert identify_xiii_build(synthetic_xiii(*ORIGINAL_CEG))["safe_to_edit"] is False


def test_the_launcher_hint_goes_only_to_the_games_that_show_one():
    """
    XIII and XIII-2 come up behind Square Enix's launcher and its own checkbox
    is the only thing that removes it. Lightning Returns starts straight into
    the game, so the same line there would be noise about a window its players
    never see.
    """
    from patcher import launch

    assert launch.shows_launcher("292120") is True
    assert launch.shows_launcher("292140") is True
    assert launch.shows_launcher("345350") is False
    assert launch.shows_launcher(None) is False

    shell_src = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    body = shell_src[shell_src.index("    def launch_game("):shell_src.index("    def pick_file(")]
    assert "self._silence_launcher(game_id)" in body
    # and the guess that cost a user an error box is gone for good
    assert "SteamAppId" not in body
    assert "_watch_launch" not in shell_src, "nothing to watch once the guess is out"


def test_the_launcher_checkbox_is_ticked_for_the_user(tmp_path):
    """
    "Tick Do not show this window again" was the old answer and it is three
    steps of homework: tick it, close the launcher, come back, then press
    Launch again wondering whether the first press did anything. The checkbox
    writes one value, so we write it.
    """
    from patcher import launcher_skip

    setup = tmp_path / "setup.xml"
    setup.write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<SaveData>\n"
        "  <window_mode>1</window_mode>\n  <full_resolution>8</full_resolution>\n"
        "  <confirm_mode>false</confirm_mode>\n</SaveData>\n")

    assert launcher_skip.set_skip(tmp_path).changed is True
    after = setup.read_text(encoding="utf-8")
    assert "<confirm_mode>true</confirm_mode>" in after
    # the rest of the file is the user's graphics choices and is not ours to
    # reformat. an ElementTree round trip rewrites every line of it.
    assert "<full_resolution>8</full_resolution>" in after
    assert after.startswith("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<SaveData>")
    assert after.endswith("</SaveData>\n")

    # idempotent, so pressing Launch twice does not rewrite the file twice
    assert launcher_skip.set_skip(tmp_path).changed is False
    # and it goes back the same way, for anyone who wants the launcher again
    assert launcher_skip.set_skip(tmp_path, skip=False).changed is True
    assert "<confirm_mode>false</confirm_mode>" in setup.read_text(encoding="utf-8")


def test_nothing_is_invented_when_setup_xml_is_not_what_we_expect(tmp_path):
    """
    setup.xml holds resolution and shadow quality as bare enum indices with no
    documentation anywhere. Writing one from scratch, or bolting an element
    onto a shape we have never seen, is a user in a 720p window asking why the
    patcher changed their graphics.
    """
    from patcher import launcher_skip

    assert launcher_skip.set_skip(tmp_path).changed is False
    assert "no setup.xml" in launcher_skip.set_skip(tmp_path).why

    (tmp_path / "setup.xml").write_text("<SaveData>\n  <window_mode>1</window_mode>\n</SaveData>\n")
    result = launcher_skip.set_skip(tmp_path)
    assert result.changed is False and "no confirm_mode" in result.why
    assert "confirm_mode" not in (tmp_path / "setup.xml").read_text(encoding="utf-8")


def test_a_hand_edited_setup_xml_is_still_understood(tmp_path):
    """
    every "how do I get my launcher back" answer online tells people to edit
    this file by hand, so it turns up however a text editor left it.
    """
    from patcher import launcher_skip

    setup = tmp_path / "setup.xml"
    setup.write_text("<SaveData><Confirm_Mode> FALSE </Confirm_Mode></SaveData>")
    assert launcher_skip.set_skip(tmp_path).changed is True
    assert setup.read_text(encoding="utf-8") == "<SaveData><Confirm_Mode>true</Confirm_Mode></SaveData>"


def test_a_launcher_that_cannot_be_silenced_never_blocks_the_launch(tmp_path):
    """
    one value in a file we did not write, in a game that plays fine either
    way. refusing to start because a config edit failed would be a worse tool
    than one that shows an extra window.
    """
    from patcher import launcher_skip
    from patcher.manifest import load_manifest

    src = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    body = src[src.index("    def _silence_launcher("):src.index("    def pick_file(")]
    assert "return {" not in body, "silencing must not produce a launch result"
    assert "raise" not in body
    assert "except Exception" in body, "a failed edit could still take the launch down"
    # and LR, which has no launcher, is never touched
    lr = load_manifest(Path(__file__).resolve().parent.parent / "manifests" / "ffxiii3.json")
    (tmp_path / "setup.xml").write_text("<confirm_mode>false</confirm_mode>")
    assert launcher_skip.follow(tmp_path, lr).changed is False
    assert (tmp_path / "setup.xml").read_text(encoding="utf-8") == "<confirm_mode>false</confirm_mode>"


def test_the_patchers_own_addons_stay_out_of_the_mods_list():
    """
    00_ff13_exe_redirect.asi installs into addons/ff13-exe-redirect, so the
    drawer listed it as a mod. Nobody who did not write it knows what it is,
    and it is half of the 4GB patch: the Remove button next to it would break
    the game it was installed to fix.

    The names come off the manifest rather than a list kept here, so an add-on
    added later is excluded the day it ships. The code patches' add-on is the
    one exception: a dropped mod puts it in, on any of the three
    """
    from patcher.bridge import UiBridge

    bridge = UiBridge()
    assert bridge.our_addon_folders("ff13") == {"ff13-exe-redirect", "ff13-code-patches", "xiii-save-anywhere"}
    # derived, not hardcoded: XIII-2's audio fix and LR's mouse support each
    # turned up here the day they went in the manifest
    assert bridge.our_addon_folders("ff13-2") == {"ff13-code-patches", "xiii2-audio"}
    assert bridge.our_addon_folders("lr") == {"ff13-code-patches", "lr-mouse"}
    assert bridge.our_addon_folders("nope") == set()


def test_the_mods_list_hides_ours_and_keeps_theirs(tmp_path):
    from patcher import addon_sieve

    for name, payload in (("ff13-exe-redirect", "00_ff13_exe_redirect.asi"),
                          ("SomeTexturePack", "pack.asi")):
        (tmp_path / name).mkdir()
        (tmp_path / name / payload).write_bytes(b"MZ")

    listed = addon_sieve.installed_mods(tmp_path, {"ff13-exe-redirect"})
    assert [m["name"] for m in listed] == ["SomeTexturePack"]
    # and with nothing to skip it is the plain listing it always was
    assert len(addon_sieve.installed_mods(tmp_path)) == 2


def test_removing_the_patchers_own_addon_is_refused():
    """
    The list hides it, so a name reaching remove_addon came from somewhere
    other than the UI. Apply would put it straight back, so a removal that
    appeared to work would be a lie either way.
    """
    from patcher.bridge import UiBridge

    result = UiBridge().remove_addon("ff13", "ff13-exe-redirect")
    assert result["ok"] is False
    assert "part of the patcher" in result["error"]


def test_the_drop_zone_uses_the_menu_face():
    ui = read_ui_text()
    zone = ui[ui.index("#mdZone{"):ui.index("#mdList{")]
    assert "var(--font-display)" in zone, "the heading should be the menu face"
    assert ".hot" in zone, "a drop target with no highlight is a guess"


def test_a_waiting_patch_takes_priority_over_a_mod_drop():
    """
    a run stalled on a needed file is what the user is looking at. a mod drop
    must not steal that file out from under it. The folder an unpack the
    drawer started comes back to the drawer, since it was never that file, and
    so does a drop the drawer took before the job started waiting.
    """
    ui = read_ui_text()
    handler = ui[ui.index("async function dropped(paths"):]
    handler = handler[:handler.index("\n  }")]
    assert 'const where = unpacked ? "drawer" : reading ? takenBy' in handler
    assert handler.index('if (where === "strip" && active){ hand(path); return; }') \
        < handler.index("Mods.route")


def doctor_install(tmp_path, *, exe_laa, backup_laa=None, leftovers=(), ini=None):
    root = tmp_path / "FINAL FANTASY XIII"
    binp = root / "white_data" / "prog" / "win" / "bin"
    binp.mkdir(parents=True)
    (binp / "ffxiiiimg.exe").write_bytes(synthetic_xiii(*ORIGINAL_CEG, laa=exe_laa))
    if backup_laa is not None:
        (binp / "untouched.exe").write_bytes(synthetic_xiii(*ORIGINAL_CEG, laa=backup_laa))
    for name in leftovers:
        (binp / name).write_text("x")
    if ini is not None:
        (binp / "NovaFix.ini").write_text(ini)
    return root


def levels(report):
    return {f.title: f.level for f in report.findings}


def test_the_doctor_says_a_patched_ceg_build_will_not_launch(tmp_path):
    """
    the real diagnosis. a clean untouched.exe does not save it on its own:
    something has to hand that file to the DRM when it reads the executable,
    and here nothing is.
    """
    from patcher.doctor import BAD, diagnose

    root = doctor_install(tmp_path, exe_laa=True, backup_laa=False)
    report = diagnose("ffxiii", root, [])

    bad = [f for f in report.findings if f.level == BAD]
    assert bad and "won't launch" in bad[0].title
    assert "stock Steam build" in bad[0].detail
    assert "run the patcher again" in bad[0].detail


def test_the_doctor_is_happy_with_an_unpatched_ceg_build(tmp_path):
    from patcher.doctor import BAD, diagnose

    root = doctor_install(tmp_path, exe_laa=False, backup_laa=False)

    assert not [f for f in diagnose("ffxiii", root, []).findings if f.level == BAD]


def test_the_doctor_notices_surviving_ff13fix_files(tmp_path):
    from patcher.doctor import WARN, diagnose

    root = doctor_install(tmp_path, exe_laa=True, backup_laa=False,
                          leftovers=("FF13Fix_README.txt", "MinHook_LICENSE.txt"))
    report = diagnose("ffxiii", root, [])

    assert levels(report).get("FF13Fix files are still here") == WARN


def test_the_doctor_notices_graphics_settings_the_launcher_will_ignore(tmp_path):
    """
    the bottleneck a real run hit: settings written, launcher still deciding.
    """
    from patcher.doctor import WARN, diagnose

    root = doctor_install(tmp_path, exe_laa=True, backup_laa=False,
                          ini="[Graphics]\nDisplayMode=Borderless\n")
    report = diagnose("ffxiii", root, [])

    assert levels(report).get("launcher graphics are not overridden") == WARN


def test_writing_a_graphics_setting_turns_the_override_on(tmp_path):
    """
    otherwise we write a file NovaFix reads and then ignores. the user changed
    a graphics value, so meaning it to take effect is the only reading.
    """
    from patcher import novafix_config

    result = novafix_config.apply_changes(tmp_path, ".", "ffxiii2",
                                          {"Display": {"DisplayMode": "Borderless"}})

    assert result["ok"] is True
    values = novafix_config.read(tmp_path / "NovaFix.ini")
    assert values["Graphics"]["OverrideLauncherGraphics"] == "On"
    assert values["Graphics"]["DisplayMode"] == "Borderless"


def test_turning_the_override_off_is_still_allowed(tmp_path):
    """the default must not be a value the user cannot change back"""
    from patcher import novafix_config

    novafix_config.apply_changes(tmp_path, ".", "ffxiii2",
                                 {"Display": {"GraphicsSource": "The Game's Launcher"}})

    values = novafix_config.read(tmp_path / "NovaFix.ini")
    assert values["Graphics"]["OverrideLauncherGraphics"] == "Off"


def test_the_purge_list_covers_what_survived_a_real_run(tmp_path):
    """readmes and licences were left behind because only the ini was listed"""
    from patcher.engine import LEFTOVERS

    survived = {"FF13Fix_README.txt", "FF13Fix_LICENSE.txt", "MinHook_LICENSE.txt"}
    assert survived <= set(LEFTOVERS["install_ff13fix"])


def test_the_community_build_takes_the_4gb_patch(tmp_path):
    """no CEG on that executable, so nothing self-validates and nothing breaks"""
    from patcher.patch_ops import is_laa_enabled

    exe, result = run_xiii_laa(tmp_path, synthetic_xiii(*COMMUNITY))

    assert result.status == PatchStatus.APPLIED
    assert is_laa_enabled(exe.read_bytes())
    assert (exe.parent / "untouched.exe").is_file()
    # the row's hint says what happened to the player, and which build this
    # was goes to the log: it rode along in the hint once
    assert result.detail == "kept a clean copy as untouched.exe", result.detail


def test_the_ceg_build_is_left_unpatched(tmp_path):
    """
    XIII's Steam executable reads itself to validate. FF13Fix hid that by
    redirecting the read to untouched.exe; NovaFix has no such hook, so a
    patched executable simply will not launch.
    """
    from patcher.patch_ops import is_laa_enabled

    exe, result = run_xiii_laa(tmp_path, synthetic_xiii(*ORIGINAL_CEG))

    assert result.status == PatchStatus.UNSAFE_BUILD
    assert "Steam's DRM" in result.detail
    assert not is_laa_enabled(exe.read_bytes())


def test_an_already_broken_install_is_repaired_from_the_clean_copy(tmp_path):
    """
    the real case: a previous version of this tool patched it while FF13Fix was
    there. removing FF13Fix removed the redirect, so the game stopped starting.
    fix it rather than sending the user to verify files.
    """
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    original = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG, laa=True))
    (exe.parent / "untouched.exe").write_bytes(original)

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.REPAIRED
    assert "untouched.exe" in result.detail
    assert exe.read_bytes() == original, "restored byte for byte"
    assert not is_laa_enabled(exe.read_bytes())


def test_a_broken_install_with_no_clean_copy_is_repaired_by_clearing_the_flag(tmp_path):
    """
    flipping the flag is its own inverse, so clearing it reconstructs the
    original exactly when the flag was the only change. no Steam verify, no
    manual step.
    """
    from patcher.patch_ops import flip_laa_flag, is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    original = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(original))

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.REPAIRED
    assert "clearing the 4GB flag" in result.detail
    assert exe.read_bytes() == original


def test_the_repair_prefers_a_clean_copy_over_clearing_the_flag(tmp_path):
    """
    restoring real bytes is exact; clearing the flag is only exact if the flag
    was the sole change. prefer the one that cannot be wrong.
    """
    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    original = synthetic_xiii(*ORIGINAL_CEG, tag=b"ORIGINAL-EXTRA-BYTES")
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG, laa=True, tag=b"ORIGINAL-EXTRA-BYTES"))
    (exe.parent / "untouched.exe").write_bytes(original)

    run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)

    assert exe.read_bytes() == original


def test_a_dirty_backup_is_not_used_for_the_repair(tmp_path):
    """a copy that is itself patched is not an original and would fix nothing"""
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG, laa=True))
    (exe.parent / "untouched.exe").write_bytes(synthetic_xiii(*ORIGINAL_CEG, laa=True))

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert "clearing the 4GB flag" in result.detail
    assert not is_laa_enabled(exe.read_bytes())


def test_a_repair_never_swaps_in_another_build(tmp_path):
    """an untouched.exe left from the other build is clean, the right length, and wrong"""
    from patcher.patch_ops import flip_laa_flag

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    (exe.parent / "untouched.exe").write_bytes(synthetic_xiii(*COMMUNITY))

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.REPAIRED
    assert exe.read_bytes() == clean
    assert "clearing the 4GB flag" in result.detail


def test_a_copy_from_another_build_is_moved_aside_before_the_patch(tmp_path):
    """the DRM reads untouched.exe through the cover, so a stale one is a refused launch"""
    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean, other = synthetic_xiii(*ORIGINAL_CEG), synthetic_xiii(*COMMUNITY)
    exe.write_bytes(clean)
    (exe.parent / "untouched.exe").write_bytes(other)
    write_novafix_ini(exe.parent, RedirectExecutableReads="On")

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.APPLIED
    assert (exe.parent / "untouched.exe").read_bytes() == clean
    assert (exe.parent / "untouched.exe.old").read_bytes() == other
    assert "untouched.exe.old" in result.detail


def patched_without_a_copy(tmp_path):
    from patcher.patch_ops import flip_laa_flag

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    write_novafix_ini(exe.parent, RedirectExecutableReads="On")
    return manifest, exe, clean


def run_laa_again(tmp_path, manifest, record, **options):
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline,
                          already_applied_check=lambda pid: True,
                          recorded=lambda pid: record if pid == "flip_laa_flag" else {},
                          **options)
    return next(r for r in results if r.patch_id == "flip_laa_flag")


def test_our_patch_with_no_copy_gets_one_rebuilt(tmp_path):
    """
    That read DONE, the DRM found nothing to read, and the refusal was filed
    against the cover. Ours is the one bit, so the original comes back exactly.
    """
    manifest, exe, clean = patched_without_a_copy(tmp_path)
    ours = {"applied_at": "2026-09-01T00:00:00+00:00"}

    preview = run_laa_again(tmp_path, manifest, ours, dry_run=True)
    assert "would rebuild untouched.exe" in preview.detail
    assert not (exe.parent / "untouched.exe").exists()

    result = run_laa_again(tmp_path, manifest, ours)

    assert result.status == PatchStatus.ALREADY_APPLIED
    assert (exe.parent / "untouched.exe").read_bytes() == clean
    assert "untouched.exe" in result.detail


def test_someone_elses_patch_with_no_copy_is_not_called_done(tmp_path):
    """another 4GB tool may have touched the checksum, so a rebuilt copy would be a guess"""
    manifest, exe, clean = patched_without_a_copy(tmp_path)

    result = run_laa_again(tmp_path, manifest, {})

    assert result.status == PatchStatus.FAILED
    assert "Verify the game files through Steam" in result.detail
    assert not (exe.parent / "untouched.exe").exists()


def test_a_patched_community_exe_is_never_sent_to_steam(tmp_path):
    """a Steam verify swaps the community exe for the CEG one, which is the opposite of a fix"""
    from patcher.patch_ops import flip_laa_flag

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(flip_laa_flag(synthetic_xiii(*COMMUNITY)))

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline,
                          already_applied_check=lambda pid: True)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.ALREADY_APPLIED
    assert "Steam" not in result.detail


def patched_with_a_record(tmp_path, exe_bytes, **ini):
    from patcher.state import applied_recorder

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(exe_bytes)
    if ini:
        write_novafix_ini(exe.parent, **ini)
    state = tmp_path / "state.json"
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline,
                          on_applied=applied_recorder(tmp_path, manifest, state))
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.APPLIED
    patch = next(p for p in manifest.patches if p.id == "flip_laa_flag")
    return manifest, patch, exe, state


def test_revert_never_writes_another_build_over_the_exe(tmp_path):
    """untouched.exe outlives a swap to the community exe, and Revert used to swap back"""
    from patcher.revert import revert_patch

    clean = synthetic_xiii(*COMMUNITY)
    manifest, patch, exe, state = patched_with_a_record(tmp_path, clean)
    exe.with_name("untouched.exe").write_bytes(synthetic_xiii(*ORIGINAL_CEG))

    result = revert_patch(tmp_path, manifest, patch, state)

    assert result.ok, result.detail
    assert exe.read_bytes() == clean


def test_revert_leaves_an_exe_steam_has_since_replaced(tmp_path):
    """writing the old copy back would downgrade whatever Steam just installed"""
    from patcher.revert import revert_patch

    manifest, patch, exe, state = patched_with_a_record(
        tmp_path, synthetic_xiii(*ORIGINAL_CEG), RedirectExecutableReads="On")
    update = synthetic_xiii(38903808, 13183488)
    exe.write_bytes(update)

    result = revert_patch(tmp_path, manifest, patch, state)

    assert result.ok, result.detail
    assert exe.read_bytes() == update


def write_novafix_ini(exe_dir, **graphics):
    lines = ["[Graphics]"] + [f"{k}={v}" for k, v in graphics.items()]
    (exe_dir / "NovaFix.ini").write_text("\r\n".join(lines) + "\r\n")


def test_a_fork_with_the_redirect_re_enables_the_4gb_patch(tmp_path):
    """
    stock NovaFix filters its CreateFile hook to .ini, so a patched executable
    cannot pass the DRM. a fork carrying FF13Fix's redirect can, and says so in
    its own config rather than us guessing from a version.
    """
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    write_novafix_ini(exe.parent, RedirectExecutableReads="On")

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.APPLIED
    assert is_laa_enabled(exe.read_bytes())
    # the redirect reads this file, so it has to exist
    assert (exe.parent / "untouched.exe").is_file()


def test_the_redirect_being_off_still_means_no_patch(tmp_path):
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    write_novafix_ini(exe.parent, RedirectExecutableReads="Off")

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.UNSAFE_BUILD
    assert not is_laa_enabled(exe.read_bytes())


def test_stock_novafix_with_no_such_setting_gets_the_safe_answer(tmp_path):
    """absence is not consent: a plain build must not be assumed to have it"""
    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    write_novafix_ini(exe.parent, OverrideLauncherGraphics="On")

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)

    assert next(r for r in results
                if r.patch_id == "flip_laa_flag").status == PatchStatus.UNSAFE_BUILD


def lay_down_chain(install_dir, manifest, skip=()):
    """
    puts the real bundled files where the engine would, straight off the assets
    the patcher ships, so a guard that checks their hashes sees what it would
    see on a healthy install.
    """
    from patcher.engine import install_target_dir
    from patcher.paths import bundled_data_root

    for patch in manifest.patches:
        if patch.type != "bundled_install" or patch.id in skip:
            continue
        target_dir = install_target_dir(install_dir, patch)
        target_dir.mkdir(parents=True, exist_ok=True)
        for spec in patch.bundled_files:
            source = bundled_data_root() / spec["source"]
            # a bundle can be a folder of its own, ReShade's
            (target_dir / spec["target"]).parent.mkdir(parents=True, exist_ok=True)
            (target_dir / spec["target"]).write_bytes(source.read_bytes())


def test_the_addon_is_never_accepted_as_cover_for_the_4gb_patch(tmp_path):
    """
    The one that cost four evenings, and the reason is a load order, not a bug
    in the hook.

    NovaFix calls compat::addon_loader::Initialize() from Initialize(), which
    only runs through EnsureInitialized(), whose only callers are the D3D9
    proxy exports and its GetProcAddress hook. Add-ons therefore load at the
    first Direct3D9 factory resolution, which is render init. The CEG check
    runs in the executable's entry point, and a process it refuses never
    reaches render init at all. That is why a failed launch left no
    ff13-exe-redirect.log: the add-on was not silent, it was not loaded.

    So the add-on sitting there, our own pinned build of it, changes nothing.
    """
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    # the add-on and the loader both genuinely ours, and the shim deliberately
    # absent: this is about what the add-on alone is worth
    lay_down_chain(tmp_path, manifest, skip=("install_dinput8_shim",))

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.UNSAFE_BUILD
    assert not is_laa_enabled(exe.read_bytes()), "wrote the patch anyway"
    assert "still starts" in result.detail

    # and the community build is still patched, which is the whole point
    exe.write_bytes(synthetic_xiii(*COMMUNITY))
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.APPLIED
    assert is_laa_enabled(exe.read_bytes())


def test_a_proxy_that_advertises_the_redirect_is_still_accepted(tmp_path):
    """
    The one route that survives. A proxy replaces d3d9.dll, which XIII imports
    statically, so the loader runs its DllMain before the entry point. That is
    where FF13Fix hooked from and it is the only timing that beats the check.
    A proxy saying so in its own config is a claim made at the layer that
    would do the work.
    """
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    write_novafix_ini(exe.parent, RedirectExecutableReads="On")

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)

    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.APPLIED
    assert is_laa_enabled(exe.read_bytes())


def test_the_community_build_is_patched_with_nothing_else_installed(tmp_path):
    """no CEG to answer to, so the redirect is irrelevant to it"""
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*COMMUNITY))

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)

    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.APPLIED
    assert is_laa_enabled(exe.read_bytes())


def test_a_recorded_drm_refusal_undoes_the_patch_and_keeps_it_off(tmp_path, monkeypatch):
    """
    The point of the whole arrangement. Static checks cannot tell you the hook
    holds on a given machine, so the launch does, and once it has said no that
    answer outranks every file on disk: the patch comes off and stays off with
    the shim sitting right there verifying as ours.
    """
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.patch_ops import flip_laa_flag, is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").write_bytes(clean)
    lay_down_chain(tmp_path, manifest)

    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", engine_module.COVER_SHIM)

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.REPAIRED
    assert exe.read_bytes() == clean, "did not restore the original bytes"
    assert "refused" in result.detail

    # a second Apply does not put it back, and does not claim to repair an
    # executable that is already clean. this guard runs on every Apply for the
    # whole affected population, so a pointless rewrite here is one per run.
    before = exe.read_bytes()
    stamp = exe.stat().st_mtime_ns
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert not is_laa_enabled(exe.read_bytes())
    assert exe.read_bytes() == before and exe.stat().st_mtime_ns == stamp, \
        "rewrote an executable that needed nothing"
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.UNSAFE_BUILD


def test_only_a_drm_refusal_blocks_the_patch(tmp_path, monkeypatch):
    """
    A watch that timed out, or a user who cancelled at Steam's prompt, is not
    evidence of anything. Treating either as a failure tears a working patch
    off an install that was fine.
    """
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    write_novafix_ini(exe.parent, RedirectExecutableReads="On")

    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)

    for verdict in (launch_watch.OK, launch_watch.UNKNOWN):
        exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
        state_module.record_launch(record, "flip_laa_flag", verdict, "")
        results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
        assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
            == PatchStatus.APPLIED, verdict
        assert is_laa_enabled(exe.read_bytes()), verdict


def test_an_install_already_broken_this_way_is_repaired(tmp_path):
    """
    Somebody is sitting on a 4GB patched CEG executable with nothing
    redirecting the DRM's read, because an older version of this tool put it
    there. Running the patcher has to put it back rather than leaving them with
    a game that will not start and no idea why.
    """
    from patcher.patch_ops import flip_laa_flag, is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").write_bytes(clean)
    assert is_laa_enabled(exe.read_bytes())

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "flip_laa_flag")

    assert result.status == PatchStatus.REPAIRED
    assert exe.read_bytes() == clean, "did not restore the original bytes"
    assert "untouched.exe" in result.detail

    # and with no clean copy it reconstructs by clearing the one bit
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").unlink()
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.REPAIRED
    assert not is_laa_enabled(exe.read_bytes())


def test_the_addon_must_actually_be_on_disk(tmp_path):
    """
    checked by looking for the file, not by trusting a recorded install. the
    game loads the file; a state record with a missing file would be a
    confident wrong answer about whether the patch is safe.
    """
    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    (exe.parent / "addons" / "ff13-exe-redirect").mkdir(parents=True)  # folder, no asi

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)

    assert next(r for r in results
                if r.patch_id == "flip_laa_flag").status == PatchStatus.UNSAFE_BUILD


def test_the_4gb_patch_runs_after_the_addon_that_enables_it():
    """
    manifest order decides this. with the patch first, its guard looks for an
    add-on that has not been installed yet and refuses every first run.
    """
    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    ids = [p.id for p in manifest.patches]

    assert ids.index("install_exe_redirect") < ids.index("flip_laa_flag")
    assert ids.index("install_novafix") < ids.index("install_exe_redirect")


def test_the_exe_redirect_addon_is_not_offered_to_anybody(tmp_path):
    """
    Not because the hook is wrong. Because an add-on cannot be early enough:
    NovaFix loads them at the first D3D9 factory resolution and the CEG check
    runs in the entry point, so the add-on's own log never even gets written on
    a refused launch.

    Kept in the manifest so the engine knows where its files are and can take
    them off installs that have them.
    """
    import json

    from patcher.engine import install_target_dir, purge
    from patcher.state import load_state

    jobs = json.loads((MANIFESTS.parent / "ui" / "jobs.json").read_text(encoding="utf-8"))
    for game in jobs["games"]:
        assert "exeredirect" not in {j["id"] for j in game["jobs"]}, game["id"]

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    addon = next(p for p in manifest.patches if p.id == "install_exe_redirect")
    assert addon.ui_job_id is None, "back in the UI, where it cannot help"
    assert not addon.default_enabled

    # it still comes off cleanly when something asks, including from an install
    # that never recorded it: the file is what arms the hook, not our state
    landing = install_target_dir(tmp_path, addon)
    landing.mkdir(parents=True)
    (landing / "00_ff13_exe_redirect.asi").write_bytes(b"MZ")
    (landing / "ff13-exe-redirect.log").write_text("active")
    state = tmp_path / "state.json"
    state.write_text('{"applied_patches": {}}')

    note = purge(tmp_path, manifest, "install_exe_redirect", state)
    assert note is not None and "00_ff13_exe_redirect.asi" in note[1]
    assert not (landing / "00_ff13_exe_redirect.asi").exists()
    assert not landing.exists(), "left an empty add-on folder looking installed"
    assert load_state(state)["applied_patches"] == {}


def test_the_purge_names_the_old_tool_not_its_patch_id(tmp_path):
    """
    a superseded patch is gone from the manifest, so there is no label to
    borrow. a real run printed "install_ff13fix: skipped", leaking an internal
    id at exactly the moment we were explaining something to a user.
    """
    from patcher.engine import purge

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    binp = tmp_path / "white_data" / "prog" / "win" / "bin"
    binp.mkdir(parents=True)
    (binp / "FF13Fix.ini").write_text("x")

    label, detail = purge(tmp_path, manifest, "install_ff13fix", tmp_path / "state.json")

    assert label == "FF13Fix"
    assert "install_" not in label
    assert "FF13Fix.ini" in detail


def test_a_dry_run_takes_nothing_off(tmp_path):
    """it printed "nothing will actually be changed" and purged FF13Fix on the way"""
    from patcher.engine import install_target_dir, supersede_handler
    from patcher.state import load_state, record_applied

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    (exe.parent / "FF13Fix.ini").write_text("their settings")

    addon = next(p for p in manifest.patches if p.id == "install_exe_redirect")
    home = install_target_dir(tmp_path, addon)
    home.mkdir(parents=True)
    (home / "00_ff13_exe_redirect.asi").write_bytes(b"MZ")
    state = tmp_path / "state.json"
    record_applied(state, addon.id, ["00_ff13_exe_redirect.asi"],
                   {"dir": home.relative_to(tmp_path).as_posix()})

    results = run_patches(tmp_path, manifest, {"install_novafix", "install_dinput8_shim"},
                          fetch=offline, dry_run=True,
                          on_supersede=supersede_handler(tmp_path, manifest, state))

    assert (exe.parent / "FF13Fix.ini").read_text(encoding="utf-8") == "their settings"
    assert (home / "00_ff13_exe_redirect.asi").is_file()
    assert addon.id in load_state(state)["applied_patches"]
    novafix = next(r for r in results if r.patch_id == "install_novafix")
    assert novafix.status == PatchStatus.WOULD_APPLY
    assert "FF13Fix" in novafix.detail, "the preview hides what the real run removes"


def test_the_dialog_constants_survive_the_pywebview_rename():
    """
    pywebview 6 renamed OPEN_DIALOG to FileDialog.OPEN and warns on the old
    name. both spellings have to work, because pinning the library over a
    constant would be silly.
    """
    shell = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    assert "def _open_dialog" in shell and "def _folder_dialog" in shell
    assert "FileDialog" in shell
    # the deprecated names may only appear as the fallback inside those helpers
    for deprecated in ("webview.OPEN_DIALOG", "webview.FOLDER_DIALOG"):
        assert shell.count(deprecated) <= 1, f"{deprecated} used outside the helper"


def test_launch_is_offered_whenever_the_game_is_worth_starting():
    """
    It used to be tied to a run: hidden on every scan, shown at the end of a
    patch. So a game that was already patched, or one patched before the app
    was last closed, offered no way to start it, and the tool sent people back
    to Steam, which is the habit it exists to replace.

    The answer is derived from what is on disk instead, which means required
    jobs and not the queue. An optional extra ticked on a game that already
    works must not retract the offer.
    """
    ui = without_comments(read_ui_text())
    start = ui.index("function launchable(){")
    body = ui[start:ui.index("\n}", start)]
    assert 'jobsOf(game(), "required")' in body, "required is the line, not the queue"
    assert "pending()" not in body and "jobs()" not in body
    assert "found.has(target)" in body, "a folder we cannot see is not launchable"
    assert "busy" in body, "never mid run, the installer is writing into it"
    # nor on a required row a launch refusal keeps off, which Apply won't put on
    assert "held.get(target)" in body and "done.has(j.id) || kept.has(j.id)" in body

    # one driver, and it is the one thing every path already ends on
    assert ui.count("Launch.toggle(") == 1
    gauge = ui[ui.index("function buildGauge(pace){"):ui.index("function renderGauge(")]
    assert "Launch.toggle(launchable());" in gauge
    assert "Launch.show()" not in ui and "Launch.hide()" not in ui


def test_a_moved_file_is_removed_from_its_old_home(tmp_path, stub_bundle):
    """
    two copies of a hooking dll in one process means the second finds its
    target already patched. a real run showed the add-on listed twice and
    "Couldn't find pattern of patch target!".
    """
    manifest = load_manifest(stub_bundle.manifest("ffxiii2"))
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(fake_pe())

    audio = next(p for p in manifest.patches if p.id == "install_audio_fix")
    # nothing ships with moved_from right now, so the audio fix stands in for
    # whichever patch next changes where it installs
    audio.moved_from = ["alba_data/prog/win/bin/xiii2_audio.asi"]
    ours = (stub_bundle.root / audio.bundled_files[0]["source"]).read_bytes()
    stale = tmp_path / audio.moved_from[0]
    stale.write_bytes(ours)

    results = run_patches(tmp_path, manifest, {"install_audio_fix"}, fetch=offline,
                          already_applied_check=lambda pid: pid == "install_novafix")
    result = next(r for r in results if r.patch_id == "install_audio_fix")

    assert result.status == PatchStatus.APPLIED, result.detail
    assert not stale.exists(), "the old copy is still there and will load twice"
    assert (exe.parent / "addons" / "xiii2-audio" / "xiii2_audio.asi").read_bytes() == ours
    assert "removed an older copy" in result.detail


def test_someone_elses_file_at_that_path_is_left_alone(tmp_path, stub_bundle):
    """a patcher that deletes files it did not write is one people stop using"""
    manifest = load_manifest(stub_bundle.manifest("ffxiii2"))
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(fake_pe())

    audio = next(p for p in manifest.patches if p.id == "install_audio_fix")
    audio.moved_from = ["alba_data/prog/win/bin/xiii2_audio.asi"]
    theirs = b"MZ" + b"a different add-on, same name" * 30
    stale = tmp_path / audio.moved_from[0]
    stale.write_bytes(theirs)

    run_patches(tmp_path, manifest, {"install_audio_fix"}, fetch=offline,
                already_applied_check=lambda pid: pid == "install_novafix")

    assert stale.read_bytes() == theirs


def test_an_already_installed_prerequisite_on_disk_satisfies_a_dependency(tmp_path, stub_bundle):
    """
    The audio fix reported "needs NovaFix first" on a machine where NovaFix was
    sitting right there. State is lost every time the app is re-extracted, so a
    prerequisite has to be answerable from what the game actually loads.
    """
    manifest = load_manifest(stub_bundle.manifest("ffxiii2"))
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(fake_pe())
    # NovaFix present on disk, nothing recorded anywhere
    (exe.parent / "d3d9.dll").write_bytes(b"MZ" + b"\x00" * 400)

    results = run_patches(tmp_path, manifest, {"install_audio_fix"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "install_audio_fix")

    assert result.status != PatchStatus.BLOCKED, result.detail
    assert (exe.parent / "addons" / "xiii2-audio" / "xiii2_audio.asi").is_file()


def test_a_genuinely_absent_prerequisite_still_blocks(tmp_path, stub_bundle):
    manifest = load_manifest(stub_bundle.manifest("ffxiii2"))
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(fake_pe())

    results = run_patches(tmp_path, manifest, {"install_audio_fix"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "install_audio_fix")

    assert result.status == PatchStatus.BLOCKED
    assert "NovaFix" in result.detail


def test_a_supplied_binary_of_the_wrong_shape_says_what_to_do(tmp_path, monkeypatch):
    """
    the failure a hash pin used to produce was "does not match its checksum",
    which tells a user nothing they can act on.
    """
    manifest = load_manifest(MANIFESTS / "ffxiii2.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(fake_pe())

    fake_root = tmp_path / "bundle"
    (fake_root / "assets" / "mods" / "novafix").mkdir(parents=True)
    (fake_root / "assets" / "mods" / "novafix" / "d3d9.dll").write_text("<!DOCTYPE html>404")
    monkeypatch.setattr("patcher.paths.bundled_data_root", lambda: fake_root)

    results = run_patches(tmp_path, manifest, {"install_novafix"}, fetch=offline)
    result = next(r for r in results if r.patch_id == "install_novafix")

    assert result.status == PatchStatus.FAILED
    assert "checksum" not in result.detail
    assert "web page" in result.detail


def test_the_drawer_tabs_do_not_wait_for_a_run():
    """
    gating them on the proxy meant a first-time user saw no tabs at all and had
    no way to learn they existed.
    """
    ui = without_comments(read_ui_text())
    # bounded by the found branch's first and last lines. the not-found branch
    # starts at offerFolderPick, so that is the end marker rather than the
    # exact text of the message, which is wording and moves.
    scan = ui[ui.index("if (r.found){"):ui.index("offerFolderPick(g);")]
    assert "Nova.offer(true)" in scan
    assert "Mods.offer(true)" in scan
    # found is what stops a stale applied set outliving a scan that failed
    assert "found.add(g.id)" in scan
    assert "found.delete(g.id)" in ui


def test_the_open_drawer_marks_its_own_tab():
    """
    The bar sits in the top right corner and the drawers open below it, so a
    tab stays visible and pressable while its drawer is up. That makes it the
    way back out, which only works if you can see which one is open.

    :has because the panels are appended after #frame, so no sibling combinator
    reaches back to the bar. A renderer without it loses the marker and keeps
    everything else.
    """
    ui = read_ui_text()
    assert "#app:has(#nv.on) #nvTab,#app:has(#md.on) #mdTab{" in ui


def test_the_corner_bar_does_not_show_on_the_game_list():
    """
    Nothing ever calls offer(false), so once a game has been found the tabs
    stay on. Menu 1 has the character art in that corner and nothing to
    configure yet, so the bar is hidden by the screen it is wrong for rather
    than by remembering to take it down.
    """
    ui = read_ui_text()
    assert "body.m1 #toolBar{opacity:0;visibility:hidden;" in ui
    # there once the game's found, fading with the clip rather than sliding
    assert "#toolBar.on{display:flex}" in ui
    assert "#toolBar.on.in" not in ui
    assert "@starting-style{#toolBar.on{opacity:0}}" in ui


def test_one_broken_clip_cannot_take_the_others_down():
    """
    cutDead was a single boolean, so the first game whose clip was missing or
    slow to fail turned video off for every game for the rest of the session.
    The evidence only ever concerned one file.
    """
    ui = without_comments(read_ui_text())
    assert "const cutDead = new Map();" in ui
    assert "deadCut(g.id)" in ui and "cutDead.set(g.id, performance.now())" in ui
    assert "cutDead = true" not in ui


def test_a_dead_cut_gets_another_go_after_a_minute():
    """
    a connection dropped while the browser picked a source runs the list out
    with no error code, exactly like a missing file, and that was stills for
    the session
    """
    ui = without_comments(read_ui_text())
    dead = ui[ui.index("function deadCut(id){"):]
    dead = dead[:dead.index("\n}")]
    assert "performance.now() - at > 60000" in dead and "cutDead.delete(id)" in dead


def test_a_failure_only_speaks_for_its_own_generation():
    """
    fail() ran whole whatever generation produced it, so a clip 404ing after a
    Q/E spin marked the wrong game and dropped the node the new generation was
    crossfading. Only the element itself is unconditionally binned.
    """
    ui = without_comments(read_ui_text())
    body = ui[ui.index("  const fail = () => {"):ui.index('  v.addEventListener("error", fail')]
    drop_self = body.index("drop(v);")
    gate = body.index("if (mine !== cutGen) return;")
    assert drop_self < gate, "your own element is binned either way"
    for shared in ("cutDead.set(g.id", "drop(outgoing)", "mediaBlocked = true"):
        assert body.index(shared) > gate, f"{shared} runs for a stale generation"


def test_every_video_leaves_the_document_through_drop():
    """
    A bare remove() takes the node out and leaves the decoder holding its
    sources, the net timer armed and the element in pendingPlay, which holds a
    strong reference. Chrome's media pool is small and per tab, so a few game
    switches exhaust it and every clip after that is slow or refuses to start.
    """
    ui = without_comments(read_ui_text())
    assert "function fadeOut(node)" in ui
    assert 'node.tagName === "VIDEO" ? drop(node) : node.remove()' in ui
    # the two crossfades and the one deliberate deletion all route through it
    assert ui.count("fadeOut(n)") == 2 and "fadeOut(outgoing)" in ui
    assert "B.remove()" not in ui, "fogB skipped drop(), so the rule had an exception"
    assert "setTimeout(() => n.remove(), 300)" not in ui


def test_a_missing_clip_is_not_diagnosed_as_a_blocked_page():
    """
    An absent file and a page that refuses inline media both give an empty
    currentSrc with readyState 0. Reading that as the environment latched
    mediaBlocked and put a CSP banner up over one missing clip.
    """
    ui = without_comments(read_ui_text())
    assert "const refusedAll = !v.currentSrc && v.readyState === 0 && !v.error;" in ui


def test_the_fog_stall_watchdog_actually_runs():
    """
    It lived inside the crossfade tick, which returns early on SEAMLESS.
    SEAMLESS is const true, so in every shipped build a starved fog decoder sat
    on one frame with nothing watching it.
    """
    ui = without_comments(read_ui_text())
    # playFrames has a tick() of its own earlier in the file, so the crossfade
    # one has to be found after the watchdog rather than from the top
    head = ui.index("  let recovered = false")
    watchdog = ui[head:ui.index("  (function tick(now){", head)]
    assert "setInterval" in watchdog, "rAF does not run while the window is occluded"
    assert "SEAMLESS" not in watchdog, "the watchdog must not be gated on the crossfade"
    # a second stall rests on the still and comes back. The gradient for good
    # was a fog that stopped and never moved again
    assert 'Fog.rest("stalled twice")' in watchdog and "Fog.fallback" not in watchdog
    assert "kick(A);" in watchdog, "a first stall earns a restart, not a demotion"
    # and the crossfade tick stops scheduling itself when there is nothing to do
    fade = ui.index("  (function tick(now){", head)
    tick = ui[fade:ui.index("    const t = A.currentTime;", fade)]
    assert tick.index("if (SEAMLESS || !B) return;") < tick.index("requestAnimationFrame(tick)")


def test_a_cut_plays_out_of_the_pages_memory_once_its_down():
    """
    a <video> on the local server asks Python for every range it buffers, so a
    busy moment over there was a clip stalling over here. Once a clip is down
    it's a blob URL, and tools/video_probe.js plays one with the server hung
    """
    ui = without_comments(read_ui_text())
    clips = ui[ui.index("const Clips = (() => {"):]
    clips = clips[:clips.index("})();")]
    assert "URL.createObjectURL(b)" in clips
    # a 404 stays asked, so a missing 720p file isn't fetched on every visit,
    # and a dropped connection doesn't, so it's tried again
    assert ".catch(() => asked.delete(k))" in clips
    assert "/^https?:/.test(k)" in clips, "a preview's own blobs aren't copied"
    play = ui[ui.index("function playCut(g, now){"):]
    play = play[:play.index("\n}\n")]
    assert "so.src = Clips.get(src)" in play and "so.dataset.file = src" in play
    # what the browser picked is what comes down for next time
    assert "Clips.want(from.dataset.file)" in play
    warm = ui[ui.index("function preloadArt(){"):]
    warm = warm[:warm.index("\n}")]
    # one after another, the game on screen first
    assert "order.reduce((ready, g) => ready.then(" in warm
    assert "(b.id === shown) - (a.id === shown)" in warm


def test_choppy_cuts_step_down_and_never_turn_into_stills():
    """
    one sample counted from the first frame used to turn every cut into a
    still for the session, and later two bad ones turned them into stills for
    minutes. A decoder warming up drops frames, so does a busy second. Now a
    choppy cut steps down a tier and the bottom tier plays on
    """
    ui = without_comments(read_ui_text())
    watch = ui[ui.index("function watchQuality(v){"):]
    watch = watch[:watch.index("\n}\n")]
    assert "setTimeout(() => { warm = read(); }, 350);" in watch, "judged once it's warm"
    assert "q.droppedVideoFrames - warm.droppedVideoFrames" in watch
    assert "if (++strikes < 2) return;" in watch
    assert watch.index("cutMp4 = false; strikes = 0;") < watch.index("cutSmall = true; strikes = 0;")
    assert "lowEnd" not in ui and "lowUntil" not in ui


def test_a_switch_is_a_straight_crossfade_with_nothing_frozen():
    """
    clips cross straight into each other. A spin used to put up first frames
    while starts were spaced out, a switch faded in on the poster before the
    decoder had a frame, and the outgoing clip was paused for the fade: all
    three were the picture stopping between games. And no zoom on it
    """
    ui = without_comments(read_ui_text())
    assert "function cutSoon" not in ui and "CUT_GAP" not in ui
    cycle = ui[ui.index("function cycle(dir){"):]
    cycle = cycle[:cycle.index("\n}\n")]
    assert "if (list[queuedIdx].id !== cutFor) playCut(list[queuedIdx]);" in cycle
    play = ui[ui.index("function playCut(g, now){"):]
    play = play[:play.index("\n}\n")]
    up = play[play.index("const bringUp = () => {"):]
    up = up[:up.index("};")]
    assert "fadeOut(outgoing)" in up and "outgoing.pause()" not in up
    assert "else if (g.cutPoster)" not in play
    harvest = ui[ui.index("function harvestCuts(){"):]
    harvest = harvest[:harvest.index("\n}\n")]
    assert 'v.classList.contains("up")' in harvest
    raw = read_ui_text()
    rest = raw[raw.index("#cinema canvas.seq,#cinema img.seq,#cinema video{"):]
    rest = rest[:rest.index("}")]
    assert "transform" not in rest and "opacity:0;transition:opacity calc(.26s * var(--pace)) ease" in rest


def test_a_network_blip_doesnt_mark_a_cut_dead():
    ui = without_comments(read_ui_text())
    play = ui[ui.index("function playCut(g, now){"):]
    play = play[:play.index("\n}\n")]
    assert "const blip = v.error && v.error.code === MediaError.MEDIA_ERR_NETWORK;" in play
    assert "if (!blip) cutDead.set(g.id, performance.now());" in play


def test_a_slow_fog_rests_on_the_still_and_comes_back():
    """
    the fog's metadata not landing in 2.5s was the gradient for good, and that
    is usually the server busy rather than a file missing. The error listener
    is still what calls a missing file dead
    """
    ui = without_comments(read_ui_text())
    assert 'Fog.rest("metadata is slow")' in ui
    assert 'Fog.fallback("metadata never arrived")' not in ui
    assert 'Fog.fallback("no playable source")' in ui
    rest = ui[ui.index("  rest(why){"):]
    rest = rest[:rest.index("\n  },")]
    assert 'this.set("still");' in rest and "this.dead" in rest
    assert 'if (!busy && !weakMachine && !document.hidden) this.set("video");' in rest
    assert "this.restMs = Math.min(ms * 2, 480000);" in rest


def test_back_lands_on_the_game_you_came_out_of():
    """
    the first row previewed XIII under a mouse still sitting on XIII-2, which
    hovered its art straight back in with the glove on the wrong row
    """
    ui = without_comments(read_ui_text())
    body = ui[ui.index("function back(){"):]
    body = body[:body.index("\n}\n")]
    assert "const from = (keep && gamesEl.querySelector(`.row[data-id=\"${keep}\"]`)) || ring()[0];" in body
    assert "if (from) from.focus();" in body
    # the glove follows once it has landed, onto wherever focus is by then
    assert "if (at && $(\"#menu1\").contains(at)) Pointer.enter(at);" in body


def test_the_way_back_takes_apply_and_launch_out_with_the_rows():
    """everything in the patch list leaves on the stagger, or Apply sat there alone"""
    ui = read_ui_text()
    rule = ui[ui.index("body.out2 .sect .hdr,"):]
    rule = rule[:rule.index("{")]
    for part in (".sect .row", "#goWrap", "#launchWrap"):
        assert part in rule, part
    script = without_comments(ui)
    assert 'const M2_ITEMS = ".sect .hdr, .sect .row, #goWrap, #launchWrap";' in script


def test_the_change_of_screen_is_sequenced_by_the_css_not_timers():
    """
    a timer beside a transition drifts from it the moment a frame comes out
    late, which had the patch list arriving over a start page still going
    """
    ui = read_ui_text()
    script = without_comments(ui)
    pick = script[script.index("function pick(id, fromCycle){"):]
    pick = pick[:pick.index("\n}\n")]
    assert 'document.body.classList.add("in2");' in pick and "showSections(true);" in pick
    assert "IN_AFTER" not in script and "function snap()" not in script
    assert "body.in2 .sect{transition-delay:var(--in-at)}" in ui
    back = script[script.index("function back(){"):]
    back = back[:back.index("\n}\n")]
    # the layer is emptied once its fade is done, and only by this visit
    assert "afterMotion([$(\"#cinema\")], FADE_MS)" in back
    assert "if (mine !== moveGen || cut !== cutGen) return;" in back
    assert "clearCinema()" in back and back.index("afterMotion([$(") < back.index("clearCinema()")
    # and nothing holds it down after: body.m2 #cinema outranked the .cut that
    # did, and every entry after the first came in as a cut
    assert "#cinema.cut" not in ui and 'classList.add("cut")' not in script


def test_focus_moves_with_the_screen_and_the_glove_waits_for_the_rows():
    """
    left on the list going out, Enter mid change unticked Audio for the next
    Apply; left on the start page going in, a second Enter picked again
    """
    script = without_comments(read_ui_text())
    back = script[script.index("function back(){"):]
    back = back[:back.index("\n}\n")]
    assert back.index("Pointer.hold(LAND_MS);") < back.index("if (from) from.focus();") \
        < back.index("afterMotion([from], LAND_MS)")
    pick = script[script.index("function pick(id, fromCycle){"):]
    pick = pick[:pick.index("\n}\n")]
    assert "first.focus();" in pick and "Pointer.hold(LAND_MS);" in pick
    assert "document.activeElement.blur()" in pick
    # held, a focus change can't put the glove on a row mid slide
    assert "if (performance.now() < (this.heldUntil || 0)) return;" in script


def test_settings_open_on_what_was_read_when_the_tab_was_offered():
    """
    a "Reading..." line for the few ms a local ini takes read as slow every
    single time. The read happens when the tab is offered, before anyone can
    press it, and the one opening makes underneath only redraws on a change
    """
    ui = without_comments(read_ui_text())
    nova = ui[ui.index("const Nova = (() => {"):ui.index("const Hd = (() => {")]
    # once a start, since what the last start read is kept and opened on too
    assert "if (on && available() && target && !fresh.has(target)) ask(target);" in nova
    assert "had = knownFor(game)" in nova and "localStorage.getItem(KEPT + game)" in nova
    assert "if (had){ loaded = had; render(); }" in nova
    assert "if (same(answer, loaded)) return;" in nova
    # a save's read starts after it, never joins one from before, and only the
    # newest answer for a game is kept or drawn, whichever lands last
    assert "ask(saved, true)" in nova
    assert "if (newest && answer && answer.ok){\n          known.set(game, answer);" in nova
    assert "if (!newest || game !== target || !panel.classList.contains(\"on\")) return;" in nova
    # a redraw under an edit keeps the control and what's been typed
    assert "row.dataset.key = `${setting.section}/${setting.key}`;" in nova
    assert "if (control.tagName === \"INPUT\") control.value = held.value;" in nova


def test_the_strip_under_a_game_moves_nothing_on_the_start_page():
    """
    in the column, it took 40px off the stage when it showed: the start page's
    list jumped up mid slide and the art shrank. Over the stage, menu 2 makes
    room for it and menu 1 never knows
    """
    ui = read_ui_text()
    assert "#statusbar{position:absolute;left:0;right:0;bottom:var(--foot-h);" in ui
    assert "#menu2{bottom:var(--sb-h)}" in ui
    assert "body.m1 #statusbar{display:none}" not in ui
    assert "body.m1 #statusbar{opacity:0;visibility:hidden;" in ui


def test_no_video_on_the_page_is_ever_opaque():
    """
    On Windows a video Chromium draws straight into the page can become a
    DirectComposition underlay, and on the user's machine that swap chain came
    up at a stale size: the fog and the cuts shrank into the top left with the
    underlay's black backing around them. Off 1 by more than 0.001, a video
    gets a pass of its own, and nothing in one goes to DirectComposition
    """
    ui = read_ui_text()
    op = float(re.search(r":root\{--clip-op:([\d.]+)\}", ui).group(1))
    # .999 is inside Chromium's 0.001 and goes straight into the page again
    assert 0.9 < op <= 0.995
    assert "#fogA{opacity:var(--clip-op)}" in ui
    assert "#cinema video.up{opacity:var(--clip-op)}" in ui
    assert "#cinema video.up{opacity:1}" not in ui
    # the fog's crossfade partner, if it's ever back, stays under it too
    assert "B.style.opacity = `calc(${op} * var(--clip-op))`" in ui


def test_the_art_fades_with_the_clip_and_doesnt_slide():
    """it slid off right for a round, which read as the art leaving instead of the scene changing"""
    ui = read_ui_text()
    stage = ui[ui.index("#artStage{position:absolute"):]
    stage = stage[:stage.index("}")]
    # #cinema's own fade, both ways, so the art and the fog trade places with the clip together
    assert "transition:opacity calc(.36s * var(--pace)) ease" in stage and "transform" not in stage
    assert "transition:opacity calc(.36s * var(--pace)) ease;background:#0a0e12}" in ui
    assert "body.m2 #artStage{opacity:0}" in ui


def test_the_q_e_name_comes_in_on_the_paths_slide():
    """half the footer sliding in and the other half fading read as two different screens"""
    ui = read_ui_text()
    path = re.search(r"#pathStat\{opacity:0;transform:(translateX\(\d+px\));pointer-events:none;\s*"
                     r"transition:([^}]*)\}", ui)
    shift, moves = path.group(1), path.group(2).strip()
    switcher = ui[ui.index("#switcher{display:flex"):]
    switcher = switcher[:switcher.index("}")]
    assert moves in switcher
    away = ui[ui.index("body.m1 #switcher{"):]
    away = away[:away.index("}")]
    assert f"transform:{shift}" in away and moves in away
    # visibility waits for the slide out, or it blinks off at the start of it
    assert "visibility 0s linear calc(.26s * var(--pace))" in away
    assert "calc(.26s * var(--pace)) cubic-bezier" in moves


def test_the_change_of_screen_runs_off_one_pace():
    """
    it read a touch quick, and slowing it meant hunting down every duration,
    stagger and timer that has to stay in step with the rest. One number now,
    and nothing in the motion sits on a fixed time to drift out of step with it
    """
    ui = read_ui_text()
    assert ui.count(":root{--pace:") == 1
    assert 1 <= float(re.search(r":root\{--pace:([\d.]+)\}", ui).group(1)) <= 2
    css = re.sub(r"/\*.*?\*/", "", "".join(re.findall(r"<style[^>]*>(.*?)</style>", ui, re.S)), flags=re.S)
    moved = ("#menu1", "#menu2", "#m1list", "#games .row", "#btnAbout", "#btnCredits", ".sect",
             "#goWrap", "#launchWrap", "#cinema", "#artStage", "#pathStat", "#switcher",
             "#statusbar", "#toolBar")
    # every time in them is 0 or scaled by the pace on the spot. --in-at and
    # --back-in already are, which the asserts after this loop hold them to
    unpaced = re.compile(r"(?<![\w.-])(\d*\.?\d+)m?s\b(?!\s*\*\s*var\(--pace\))")
    checked = 0
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not any(m in selectors for m in moved):
            continue
        for value in re.findall(r"transition(?:-delay)?:([^;]*)", body):
            for part in re.split(r",(?![^()]*\))", value):
                if part.split()[0] == "filter":
                    continue  # the hover's brightness, not the change of screen
                fixed = [m.group(0) for m in unpaced.finditer(part) if float(m.group(1))]
                assert not fixed, f"{' '.join(selectors.split())}: {part.strip()}"
                checked += 1
    assert checked > 30, checked
    for scaled in ("--in-at", "--back-in", "--beat"):
        assert re.search(rf":root\{{{scaled}:calc\([^)]*\* var\(--pace\)\)\}}", ui), scaled
    js = without_comments(ui)
    assert 'getPropertyValue("--pace")' in js
    assert "const OUT_MS = 330 * PACE, FADE_MS = 400 * PACE, LAND_MS = 700 * PACE;" in js
    # the Q/E switch's own beats: out, then the new list in once it has gone,
    # and the glove onto its first row when that row has landed, not on a timer
    assert "}, 70 * PACE);" in js
    assert 'afterMotion([first, first.closest(".sect")], LAND_MS).then(land)' in js
    # and the waits on a fade: a clip's crossfade out, and the list a switch
    # rebuilds once its own fade has hidden it
    assert "node.remove()), 300 * PACE);" in js
    assert "cycleTimer = setTimeout(commitCycle, 140 * PACE);" in js


def test_the_highlight_eases_out_without_a_hard_edge():
    """
    the plate's ramp dipped dark and flashed white right before the slant, and
    every bar's end faded in 34px, so the slant and the ends read as hard lines,
    and a longer straight fade took APPLIED and PLAY with it. The ramp only
    climbs then settles, the fade eases out along the slant, and the bars run
    on past their words into a soft end that bows in a little
    """
    ui = read_ui_text()
    lit = ui[ui.index(".row .lit{position:absolute"):]
    lit = lit[:lit.index("transition:filter")]
    assert "#fff" not in lit and "#a1a5ab" not in lit
    assert "rgba(0,0,0,.35) calc(var(--lit-fade) + (97% - var(--lit-fade)) * .6)" in lit
    assert "calc(100% - 34px)" not in ui
    # the end bows like a "(", its edges eased out so the top hairline stops
    # with it, and both layers meet rather than either one winning
    assert "radial-gradient(72px 38px at calc(100% + 6px) 50%" in ui
    # the patch rows, Apply and Launch. The start page has no bars to end
    assert ui.count("-webkit-mask-image:var(--bar-end);-webkit-mask-composite:source-in;") == 3
    assert ui.count("mask-image:var(--bar-end);mask-composite:intersect") == 3
    # and every bar reaches past the status column, so no word sits in the fade
    assert ui.count("var(--bar-run)") >= 6


def test_the_start_page_is_the_title_menu():
    """
    dark plates on the start page's light fog read as heavy whatever their ends
    did, so it's the game's own title menu: capitals standing on a soft shadow,
    each entry a step in, no APPID, no heading. The shadow is the words' own,
    so it can't drift from them, and the glove goes by where the words start
    """
    ui = read_ui_text()
    assert "Select target</h2>" not in ui
    assert 'className = "ink"' in ui and '<span class="ink">About</span>' in ui
    assert "#games .row .right,#games .row .lit{display:none}" in ui
    style = ui[ui.index("#games .row .label,#btnAbout .lbl,#btnCredits .lbl{"):]
    style = style[:style.index("}")]
    assert "text-transform:uppercase" in style and "var(--title-in) + var(--step,0px)" in style
    # ink darker than its line, so the words lead, and no engraved glow
    assert "color:#15171a" in style and "text-shadow" not in style
    line = ui[ui.index("#games .ink::after,#btnAbout .ink::after,#btnCredits .ink::after{"):]
    line = line[:line.index("}")]
    # a shadow: no lit edge under it, and placed off the words' own box
    assert "box-shadow" not in line and "top:var(--line-y)" in line
    assert "width:calc(100% * (1 + var(--before) + var(--after)))" in line
    assert "height:2px" in line
    # the lines slide out ahead of their words and are drawn back in after
    assert "body.m2 #games .ink::after,body.m2 #btnAbout .ink::after" in ui
    assert "transform:translateX(-70px) scaleX(.35)" in ui
    # the reflection starts at the line, and the fog behind the menu is lifted
    assert "-webkit-box-reflect:below calc(-.78em - var(--tuck))" in ui
    assert "#m1list::before{" in ui and "body.m2 #m1list::before{opacity:0" in ui
    # nothing reaches the art
    assert "#games{-webkit-mask-image:linear-gradient(to right,#000 calc(100% - 40px),transparent)" in ui
    snap = ui[ui.index("  snap(el){"):ui.index("/* mouse coordinates are viewport px")]
    assert 'const ink = label && label.querySelector(".ink");' in snap
    assert "place(at - 5, y + h/2 - 3);" in snap


def test_the_dark_bar_drops_its_rule_inside_a_game_only():
    """
    XIII-2's and LR's bottom bar loses its rule in a game and keeps it on the
    start page. Clear wasn't enough: at a fractional dpi the light bar under a
    clear border showed through as a bright line, so it's painted over instead
    """
    ui = read_ui_text()
    rule = ui[ui.index('body.m2[data-look="xiii2"] #foot{'):]
    rule = rule[:rule.index("}")]
    assert "border-top-color:rgb(37,39,41)" in rule
    assert 'body.m1[data-look="xiii2"] #foot' not in ui
    # one entry reflects at a time, whichever of keys or mouse is driving
    assert "body.keys #games .row:focus-visible .ink" in ui
    assert "body:not(.keys) #games .row:hover .ink" in ui
    assert "#games .row:hover .ink,#games .row:focus-visible .ink" not in ui


def test_row_text_ends_before_the_highlight_fades():
    """
    the plate was sized a fixed run past the widest label, which left the end
    of "Lightning Returns: FFXIII" and "4GB Compatibility" in its fade: dark ink
    on a plate going dark under it. It goes by the fade's own stop now, off the
    same variable the mask reads
    """
    ui = read_ui_text()
    assert "mask-image:linear-gradient(112deg,#000 var(--lit-fade)," in ui
    fit = ui[ui.index("function fitAll(){"):ui.index("function buildGames(){")]
    assert 'getPropertyValue("--lit-fade")' in fit
    assert "const want = (widest + 10) / fade;" in fit


def test_right_aligned_text_ends_on_one_edge():
    """Apply and Launch ended 4px short of the patch chips, and the headers 2px"""
    ui = read_ui_text()
    rule = lambda sel: ui[ui.index(sel):ui.index("}", ui.index(sel))]
    for sel in (".row .right{", ".hdr{", "#go{", "#launch{"):
        assert "var(--col-end)" in rule(sel), sel
    for sel in ("#go .right{", "#launch .right{"):
        assert "padding-right" not in rule(sel), sel


def test_a_dlc_download_goes_into_the_game_it_was_asked_for_on():
    """installed into whichever game was up when it landed, it went into the wrong one"""
    ui = without_comments(read_ui_text())
    assert "const anyMod = new Map(), due = new Map();" in ui
    assert "anyMod.set(key, target)" in ui
    arrived = ui[ui.index("const forGame = anyMod.get(key);"):]
    arrived = arrived[:arrived.index("else if (ev.path) route(ev.path, true);")]
    assert "forGame !== target" in arrived and "due.set(forGame" in arrived
    assert "arrive(ev.path, forGame)" in arrived
    # one after another, each on the game it was asked for on
    assert "then(() => route(path, false, 0, game))" in ui
    assert "accept(path, { title: what.title, game: game || target, queued: !!game })" in ui


def test_a_refused_cinema_clip_paints_something():
    """
    onVideoDead deliberately did nothing for cinema, on the grounds that
    playCut's error path covers it. That path runs on a media error event, and
    a play() refused twice never fires one, so the layer sat dead and invisible
    over #cinema's flat fill.
    """
    ui = without_comments(read_ui_text())
    body = ui[ui.index("function onVideoDead(v){"):ui.index("/* throttled.")]
    assert "cutStill(g)" in body and "cutFallback(g)" in body


def test_the_restart_is_offered_where_the_change_was_made():
    """
    NovaFix reads its ini once at startup, so every graphics change costs a
    restart, and nothing outside the game process can shortcut that. The part
    this app controls is how many presses the restart takes: it was apply,
    close the drawer, find Launch game, press it.

    Only when there is something to offer. A game that is already running
    cannot be reached by the file we just wrote, and a game that is not
    launchable has nothing to start.
    """
    ui = without_comments(read_ui_text())
    assert 'id="nvPlay"' in ui
    save = ui[ui.index("  function save(){"):ui.index("  return { offer, open, close, unnote };")]
    assert "play.hidden = r.gameRunning || !launchable();" in save
    # a notice, like a voice switch leaving HD behind, holds it open the same way
    assert "if (!play.hidden || r.notice) return;" in save, "closing under the offer retracts it"
    # and it belongs to one save, not to the panel
    assert '$("#nvPlay").hidden = true;' in ui


def test_the_corner_bar_is_sized_off_the_row_height():
    """
    It shipped once at 40px against 30px menu rows and a 50px help bar, which
    is the second heaviest slab on screen for a pair of secondary controls.
    Four fifths of --row-h keeps it subordinate to a menu row rather than at
    the mercy of whoever last edited the padding. A literal in :root now, not a
    calc, because Grid reads both as numbers before it sets each to the whole
    pixel height that centres its own label; the ratio is pinned here instead.
    """
    import re
    ui = read_ui_text()
    row = float(re.search(r"--row-h:(\d+(?:\.\d+)?)px;", ui).group(1))
    tab_h = float(re.search(r"--tab-h:(\d+(?:\.\d+)?)px;", ui).group(1))
    assert tab_h == row * .8
    tab = ui[ui.index(".tool-tab{"):ui.index("}", ui.index(".tool-tab{"))]
    assert "height:var(--tab-h)" in tab
    assert "padding:0 17px" in tab, "vertical padding is what made it bulky"
    # the type is the old side rail's, unchanged. the box was the problem.
    assert "font-size:12px;letter-spacing:2px" in tab


def test_every_row_and_both_buttons_are_reachable_by_keyboard():
    """
    Apply and Launch belong to the vertical list; the patch rows used to be all
    the keyboard could reach.

    The wrapper is what gets measured: a button inside a collapsed max-height
    box still reports its own full height, so asking the button alone would say
    Launch is up when it is not.
    """
    ui = without_comments(read_ui_text())
    body = ui[ui.index("function visible(b){"):ui.index("function ring(){")]
    assert "#sectGo button" in body
    assert "#goWrap, #launchWrap" in body
    assert "b.disabled" in body
    ring = ui[ui.index("function ring(){"):ui.index("function step(dir){")]
    assert "m2 ? actionRows() : []" in ring, "menu 1 leaves menu 2 laid out at opacity 0"
    assert "#toolBar" not in ring, "the corner is not part of the vertical list"


def test_the_drawers_are_pointer_driven():
    """
    Dropdowns and a drop zone are pointer controls. Wiring them into the WASD
    ring built a second, worse way to use them, and it ran through the corner
    bar, so holding W off the top row walked the glove out of the menu and into
    a panel full of <select>s.

    Three things keep it out: the corner is off the ring, the corner is off the
    tab order, and an open drawer returns from the key handler before it can
    drive the list behind it.
    """
    ui = without_comments(read_ui_text())

    step = ui[ui.index("function step(dir){"):ui.index("function lateral(dir){")]
    assert "toolBar" not in step and "focusBar" not in step, \
        "the vertical walk has no business in the corner"

    start = ui.index("function lateral(dir){")
    lat = ui[start:ui.index('addEventListener("keydown"', start)]
    assert "toolBar" not in lat and "toolTabs" not in lat

    tab = ui[ui.index("function toolTab(id, label"):ui.index("function syncTools(){")]
    assert "b.tabIndex = -1" in tab, "focus must not land in the corner by Tab either"

    keys = ui[ui.index('addEventListener("keydown", e => {'):]
    guard = keys.index("drawerOpen()")
    # ahead of every movement key, or the guard is decoration
    for key in ('k === "w"', 'k === "enter"', 'k === "q"'):
        assert guard < keys.index(key), f"{key} runs before the drawer guard"
    # and NOT preventDefault'd, so a <select> keeps its own arrow keys
    line = keys[guard:keys.index("\n", guard)]
    assert "return" in line and "preventDefault" not in line


def test_the_window_keys_have_a_listener_of_their_own():
    """
    F11 and Alt+Enter are about the window, not the screen: an open drawer
    doesn't stop them, a held key toggles once, and the menu's own handler,
    which reads Enter as "press the focused row", never sees an Alt+Enter.
    tools/ui_probe.js drives all of it in Chromium.
    """
    ui = without_comments(read_ui_text())
    own = ui.index('if (e.key !== "F11" && !(e.key === "Enter" && e.altKey)) return;')
    menu = ui.index('if (k === "tab"){ Pointer.set("keys"); return; }')
    assert own < menu, "registered ahead of the menu's listener"
    block = ui[own:menu]
    assert "e.stopImmediatePropagation();" in block
    assert "if (!e.repeat) Fullscreen.toggle();" in block
    # on capture, or a row's own Enter handler hears Alt+Enter first
    assert "}, true);" in block[:block.index('addEventListener("keydown"')]
    # Escape walks out one thing at a time, and leaving fullscreen is the last
    esc = ui[ui.index('else if (k === "escape"){'):]
    esc = esc[:esc.index("\n});")]
    assert esc.index("closeDrawers()") < esc.index("anyPopOpen()") \
        < esc.index('Fullscreen.on && document.body.classList.contains("m1")') < esc.index("back();")


def test_a_window_changing_size_says_nothing_in_the_help_bar():
    """
    Going fullscreen used to put "Fullscreen. Press F11 or Alt+Enter to go back
    to a window." in the help bar. The help bar is about what's on screen, and a
    window changing size is the window's business, so F11, Alt+Enter and a
    maximise all leave it alone.
    """
    ui = without_comments(read_ui_text())
    full = ui[ui.index("const Fullscreen = {"):ui.index("\n};", ui.index("const Fullscreen = {"))]
    assert "say(" not in full
    assert "Press F11" not in ui
    fit = ui[ui.index("function fit(){"):ui.index('addEventListener("resize", fit)')]
    assert "say(" not in fit


def test_the_drawer_header_is_a_tab_pair_not_an_ini_path():
    """
    It used to read "Editing NovaFix.ini": a file the user never chose, in the
    widest line of an open panel. The header now says which drawer you are in
    and lets you cross to the other one, in the same slash-and-size shorthand
    the Q / E switcher already uses in the footer.
    """
    ui = read_ui_text()
    assert "nvWhere" not in ui and "mdWhere" not in ui

    body = without_comments(ui)
    build = body[body.index("function drawerTabs(activeId){"):
                 body.index("function syncDrawerTabs(){")]
    assert 'b.classList.toggle("now", id === activeId)' in build
    assert "b.tabIndex = -1" in build, "mouse only, like the corner that opens it"
    # crossing over goes through the corner tab's own toggle, so switching and
    # opening stay one code path rather than two that can drift
    assert 'document.getElementById(id + "Tab")' in build

    css = re.search(r'<style id="novafix-style">(.*?)</style>', ui, re.S).group(1)
    # one size for both: a pair trading sizes reflowed the heading on every switch
    assert not re.search(r"\.dtab:not\(\.now\)\{[^}]*font-size", css), "the same size"
    assert "font-size" not in re.search(r"\.dtab\{[^}]*transition:([^}]*)\}", css).group(1)
    assert re.search(r"\.dtab:not\(\.now\)\{[^}]*opacity:\.7", css), "faded instead"
    assert ".dsep{" in css


def test_the_corner_bar_is_ordered_by_slot_not_by_css():
    """
    The keyboard walks the DOM and the eye reads the flexbox, so CSS order was
    two sources of truth: the bar read Mods then Settings while the arrow keys
    went Settings then Mods, because the scan builds the drawers in the other
    order. Inserting by slot makes the two agree by construction.
    """
    ui = without_comments(read_ui_text())
    assert 'toolTab("mdTab", "Mods", 0,' in ui
    assert 'toolTab("nvTab", "Settings", 1,' in ui
    assert "el.dataset.slot > slot" in ui
    for dead in ("#mdTab{order:", "#nvTab{order:"):
        assert dead not in ui, f"{dead} is back to disagreeing with the DOM"


def test_resolution_is_a_list_not_two_free_numbers():
    """
    these games render 16:9 internally and lay their UI out for it, so an
    arbitrary size is a stretched HUD rather than a feature.
    """
    from patcher import novafix_config

    keys = {s.key for s in novafix_config.settings_for("ffxiii2")}
    assert "Resolution" in keys
    assert "Width" not in keys and "Height" not in keys

    resolution = next(s for s in novafix_config.settings_for("ffxiii2")
                      if s.key == "Resolution")
    assert resolution.kind == "choice"
    assert "1920x1080" in resolution.choices


def test_the_gauge_is_one_bar_not_a_row_of_lamps():
    """
    Filling segment i only when job i had succeeded left gold-grey-gold gaps
    whenever results came back out of order, and pulling the fill back to the
    current segment on a failure made the bar look like it kept loading just to
    collapse. Everything goes through one fraction of the whole gauge now.
    """
    import re

    ui = read_ui_text()
    assert "function renderGauge(pace = GAUGE_SEG_MS)" in ui
    # word boundary, or resetGauge matches and the check never fires
    assert not re.search(r"\bsetGauge\s*\(", ui), "the per-segment painter is back"

    render = ui[ui.index("function renderGauge("):ui.index("function paintGauge")]
    # each segment is told how much of its own span the overall fill reaches
    assert "filled - i" in render
    assert "gaugeDone + gaugeLive" in render


def test_a_failed_job_gives_back_only_its_own_segment():
    ui = read_ui_text()
    end = ui[ui.index("async function jobEnd"):ui.index("function creep")]
    # success banks a segment, failure releases the live part and nothing else
    assert "gaugeDone++" in end
    assert "gaugeDone--" not in end
    assert "gaugeDone = 0" not in end


def test_a_run_starts_the_gauge_from_empty():
    """an already-patched game would otherwise begin full with nowhere to move"""
    ui = read_ui_text()
    assert "buildGauge(); resetGauge();" in ui


def test_an_open_drawer_stops_the_game_selection_moving_under_it():
    """
    The NovaFix panel edits one game's NovaFix.ini. Scrolling used to cycle the
    game behind it while the panel kept showing the old one, so the portrait
    said XIII-2 while the settings were still XIII's and nothing on screen said
    which one Save would write to.

    The guard lives in cycle() rather than in the wheel handler because Q and E
    reach the same function and had the same bug.
    """
    ui = read_ui_text()

    assert "function drawerOpen()" in ui
    assert '"#nv.on, #md.on"' in ui, "both drawers, not just NovaFix"

    body = ui[ui.index("function cycle(dir){"):]
    body = body[:body.index("\n}")]
    assert "drawerOpen()" in body, "cycle() does not check for an open drawer"

    wheel = ui[ui.index('addEventListener("wheel"'):]
    wheel = wheel[:wheel.index("}, {passive:true});")]
    assert "drawerOpen()" in wheel, (
        "the wheel handler still feeds its accumulator while a drawer is open, "
        "so a long scroll can leave a notch primed for the moment it closes")


def test_the_gauge_banks_every_decided_job_not_only_the_successes():
    """
    The lurch. jobEnd filled a whole segment for every outcome and then gave it
    back for anything that was not a success, so a run with four skipped rows
    played four forward-and-back thrusts, each animated by the stylesheet's
    .12s width transition rather than hidden by it.

    During a run the gauge answers "how far through the queue are we". The chip
    on each row already says which ones worked.
    """
    ui = read_ui_text()
    body = ui[ui.index("async function jobEnd("):]
    body = body[:body.index("\n}")]

    banked = body.index("gaugeDone++")
    ok_branch = body.index("if (ok){")
    assert banked < ok_branch, (
        "gaugeDone++ is inside the success branch again, so a skipped or failed "
        "job fills a segment and then hands it back")
    assert body.count("gaugeLive = 0") == 1, (
        "the failure path is releasing the segment a second time")


def test_the_gauge_only_comes_down_in_one_place():
    """
    settleGauge is the single transition from "jobs decided" to "jobs that
    stuck", and it owns the repaint so the drop lands inside its eased window
    instead of snapping ahead of it on the stylesheet's .12s.
    """
    ui = read_ui_text()
    assert "async function settleGauge()" in ui

    settle = ui[ui.index("async function settleGauge()"):]
    settle = settle[:settle.index("\n}")]
    assert "buildGauge(SETTLE_SEG_MS)" in settle, "settleGauge does not own the repaint"

    run_body = ui[ui.index("async function run(){"):ui.index("go.addEventListener")]
    assert "await settleGauge()" in run_body


def test_the_gauge_is_not_torn_down_on_every_repaint():
    """
    buildGauge used to clear #gauge and recreate the segments, which throws the
    fills away mid-transition: every repaint during a run restarted from zero
    width and jumped, and the stylesheet transition could do nothing because
    the elements it was animating no longer existed.
    """
    ui = read_ui_text()
    body = ui[ui.index("function buildGauge(pace){"):]
    body = body[:body.index("\n}")]
    assert "children.length !== n" in body, "buildGauge rebuilds unconditionally again"


def test_the_gauge_never_runs_backwards_in_a_real_run():
    """
    The structural tests above pin the shape. This one measures the rendered
    widths through the real jobEnd, which is the thing a person actually sees.

    Skips without node and jsdom, which CI does not install today.
    """
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node is not installed")

    repo = Path(__file__).resolve().parent.parent
    probe = repo / "tools" / "gauge_probe.js"
    env = {**os.environ}
    for candidate in (repo / "node_modules", Path.home() / "node_modules"):
        if candidate.is_dir():
            env["NODE_PATH"] = str(candidate)
            break

    result = subprocess.run(["node", str(probe)], capture_output=True, text=True,
                            cwd=repo, env=env, timeout=180)
    if "Cannot find module 'jsdom'" in result.stderr:
        pytest.skip("jsdom is not installed (npm install jsdom)")

    assert result.returncode == 0, result.stdout + result.stderr


def test_escape_dismisses_a_drawer_without_leaving_the_screen():
    """
    Escape used to do both: the drawer's own listener closed it, then the
    global handler found nothing open and ran back(), so dismissing the NovaFix
    settings dropped you on the startup screen.

    One owner now. The drawers have no keydown listener of their own, and the
    global handler closes front to back: drawer, then pops, then back().
    """
    ui = read_ui_text()

    esc = ui[ui.index('else if (k === "escape"){'):]
    esc = without_comments(esc[:esc.index("\n  }")])
    assert "closeDrawers()" in esc, "the global Escape does not know about drawers"
    assert esc.index("closeDrawers()") < esc.index("back()"), (
        "back() is reachable before the drawer has been given a chance to close")

    # a drawer that closes itself on Escape runs BEFORE the global handler, so
    # the global one then sees nothing open and navigates away anyway
    for module in ("const Nova = (() => {", "const Mods = (() => {"):
        if module not in ui:
            continue
        body = ui[ui.index(module):]
        body = body[:body.index("\n})();")]
        # the literal comparison, not the word: the module's own comments
        # mention Escape and should be allowed to
        assert 'e.key === "Escape"' not in body and 'e.key !== "Escape"' not in body, (
            f"{module.split()[1]} handles Escape itself again, which re-opens the "
            "fall-through: it closes first and back() still fires")


def test_a_click_outside_a_drawer_dismisses_it():
    ui = read_ui_text()
    assert "addEventListener(\"pointerdown\"" in ui

    handler = ui[ui.index('addEventListener("pointerdown"'):]
    handler = handler[:handler.index("}, true);")]
    assert "closeDrawers()" in handler
    assert "Tab" in handler or "tabSel" in handler, (
        "the tab buttons must be excluded, or pressing an open tab closes the "
        "drawer and the toggle immediately reopens it")


def test_pending_novafix_edits_survive_a_close():
    """
    Three ways out now and two of them are easy to hit by accident, so open()
    must not wipe unsaved edits the way it used to.
    """
    ui = read_ui_text()
    body = ui[ui.index("  async function open(){"):]
    body = body[:body.index("\n  }")]
    assert "dirtyFor !== target" in body, (
        "open() clears dirty unconditionally again, so an accidental click "
        "outside silently discards every unsaved setting")


def test_the_drawer_exits_behave_in_a_real_dom():
    """structure above, behaviour here. skips without node and jsdom."""
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node is not installed")

    repo = Path(__file__).resolve().parent.parent
    env = {**os.environ}
    for candidate in (repo / "node_modules", Path.home() / "node_modules"):
        if candidate.is_dir():
            env["NODE_PATH"] = str(candidate)
            break

    result = subprocess.run(["node", str(repo / "tools" / "drawer_exit_probe.js")],
                            capture_output=True, text=True, cwd=repo, env=env, timeout=180)
    if "Cannot find module 'jsdom'" in result.stderr:
        pytest.skip("jsdom is not installed (npm install jsdom)")

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_novafix_panel_says_apply_and_reports_what_actually_happened():
    """
    "Save" described the file operation. "Apply" describes the intent, and the
    note has to tell the truth about the result, which differs depending on
    whether the game is up: NovaFix reads its ini once at startup, so "takes
    effect next time you start the game" is wrong advice for someone who is
    already playing.
    """
    ui = read_ui_text()
    assert '<button id="nvSave" class="go">Apply</button>' in ui
    assert ">Save<" not in ui, "a Save label came back"

    save = ui[ui.index("  function save(){"):]
    save = save[:save.index("\n  }")]
    assert "r.gameRunning" in save, (
        "the note does not branch on whether the game is running, so it tells "
        "someone mid-session to restart later")


def _ui_jobs():
    return json.loads((Path(__file__).resolve().parent.parent / "ui" / "jobs.json").read_bytes())


def test_apply_only_offers_jobs_it_can_actually_finish():
    """
    The one-click claim, enforced.

    A job whose mod ships its own installer, or lives behind a login, can never
    end with the files in place: the engine returns NEEDS_INSTALLER or MANUAL
    no matter how the download was obtained. The Nexus gate was never the
    binding constraint on those, the installer is, so a mirror would not change
    it either.

    Those belong in the Mods drawer, marked kind "extra". Anything still in the
    Apply list has to be able to finish, where finishing may include a website
    click the app then picks up on its own.
    """
    ui_by_game = {g["id"]: {j["id"]: j for j in g["jobs"]} for g in _ui_jobs()["games"]}

    offenders = []
    for name in ("ffxiii", "ffxiii2", "ffxiii3"):
        manifest = load_manifest(MANIFESTS / f"{name}.json")
        jobs = ui_by_game.get(manifest.ui_id, {})
        for patch in manifest.patches:
            if not patch.ui_job_id:
                continue
            job = jobs.get(patch.ui_job_id)
            if job is None or job.get("kind") == "extra":
                continue
            if patch.runs_installer:
                offenders.append(f"{name}/{patch.id} runs an installer")
            if patch.type == "manual":
                offenders.append(f"{name}/{patch.id} is manual-only")

    assert not offenders, (
        "these are offered in the Apply list but cannot finish there: "
        + "; ".join(offenders) + ". Mark them kind \"extra\" in ui/jobs.json "
        "and the inline fallback so they surface in the Mods drawer instead.")


def test_every_extra_carries_a_page_to_send_someone_to():
    """
    An extra the app cannot install and cannot link is worse than not listing
    it: the drawer would show a name and no way to act on it.

    The host check matters more than it looks. open_pages drops anything
    outside MOD_HOSTS and still returns ok, so a page on an unlisted host
    gives you a button that does nothing at all when pressed.
    """
    from shell import Api

    for game in _ui_jobs()["games"]:
        for job in game["jobs"]:
            if job.get("kind") != "extra":
                continue
            page = job.get("page", "")
            assert page.startswith("https://"), f"{game['id']}/{job['id']} has no page"
            assert page.startswith(Api.MOD_HOSTS), (
                f"{game['id']}/{job['id']} links {page}, which open_pages refuses")
            # what to press on the page, said when it opens. The row itself
            # says what the mod is, like every other row
            assert job.get("get"), f"{game['id']}/{job['id']} doesn't say what to download"
            assert job.get("help"), f"{game['id']}/{job['id']} doesn't say what it is"
            assert "outside" not in job


def test_the_inline_fallback_agrees_with_the_shipped_jobs_file():
    """
    index.html carries a copy for when the fetch fails. A kind that differs
    between the two means the Apply list changes shape depending on whether a
    file loaded, which is the worst kind of intermittent.
    """
    ui = read_ui_text()
    start = ui.index("const FALLBACK_MANIFEST = {")
    brace = ui.index("{", start)
    depth, i = 0, brace
    while True:
        if ui[i] == "{":
            depth += 1
        elif ui[i] == "}":
            depth -= 1
        if depth == 0:
            break
        i += 1
    inline = json.loads(ui[brace:i + 1])

    shipped = {(g["id"], j["id"]): j.get("kind", "required")
               for g in _ui_jobs()["games"] for j in g["jobs"]}
    fallback = {(g["id"], j["id"]): j.get("kind", "required")
                for g in inline["games"] for j in g["jobs"]}

    assert shipped == fallback, "ui/jobs.json and the inline fallback disagree"


def test_a_wrong_build_of_a_bundled_file_is_not_installed(tmp_path):
    """
    The real one. A user's d3d9.dll was 91,136 bytes off the build this patcher
    ships. "Installed" was is_file(), so every re-run saw it and skipped the
    patch, the NovaFix actually loading the exe-redirect add-on was a version
    nobody had tested, the add-on never ran, XIII's DRM read its own patched
    bytes and the game died with Application load error 3:0000065432. Every
    check in the tool said ok.

    A file we ship has a pinned hash, so "is this ours" has an exact answer and
    there is no reason to guess from the filename.
    """
    from patcher.engine import installed_on_disk, install_target_dir

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    proxy = next(p for p in manifest.patches if p.id == "install_novafix")

    lay_down_chain(tmp_path, manifest)
    assert installed_on_disk(tmp_path, proxy) is True

    # somebody else's NovaFix, right name, right shape, wrong build
    landing = install_target_dir(tmp_path, proxy) / proxy.bundled_files[0]["target"]
    ours = landing.read_bytes()
    landing.write_bytes(ours[:-91136])
    assert installed_on_disk(tmp_path, proxy) is False, "presence is not proof"

    # and the re-run puts ours back rather than skipping it
    results = run_patches(tmp_path, manifest, {"install_novafix"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "install_novafix").status \
        in (PatchStatus.APPLIED, PatchStatus.ALREADY_APPLIED)
    assert landing.read_bytes() == ours


def test_a_backup_is_never_left_half_written(tmp_path):
    """
    copy2 onto the live path plus "never overwrite an existing backup" means
    one failed copy leaves a truncated untouched.exe forever: every later run
    sees a file and skips it, XIII's DRM reads short bytes through the redirect
    and the game dies with Application load error 3:0000065432, and a revert
    copies the truncated file over a working executable.
    """
    from patcher.engine import ensure_backup, restore_backup

    exe = tmp_path / "ffxiiiimg.exe"
    exe.write_bytes(b"O" * 40000)
    ensure_backup(exe, "untouched.exe")
    assert (tmp_path / "untouched.exe").read_bytes() == b"O" * 40000

    src = (Path(__file__).resolve().parent.parent / "patcher" / "engine.py").read_text(encoding="utf-8")
    for name in ("def ensure_backup(", "def restore_backup("):
        body = src[src.index(name):src.index("\n\n\ndef ", src.index(name))]
        assert "write_atomic" in body, f"{name} writes straight onto the live path"
        assert "shutil.copy2" not in body

    exe.write_bytes(b"P" * 40000)
    assert restore_backup(exe, "untouched.exe") is True
    assert exe.read_bytes() == b"O" * 40000


def test_a_supersede_that_cannot_delete_the_old_copy_is_not_a_success(tmp_path):
    """
    Two copies of a hooking dll in one process means the second finds its
    target already hooked, and the game reports "Couldn't find pattern of patch
    target!" on the first launch. The delete was wrapped in except OSError:
    pass, so the run reported APPLIED over exactly that. The usual cause is the
    game being open, which the user can fix in seconds if anyone tells them.
    """
    import patcher.engine as engine_mod

    manifest = load_manifest(MANIFESTS / "ffxiii2.json")
    patch = next((p for p in manifest.patches if p.moved_from), None)
    if patch is None:
        pytest.skip("no shipped patch supersedes a path")

    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"\0" * 64)
    from patcher.paths import bundled_data_root

    stale = tmp_path / patch.moved_from[0]
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes((bundled_data_root() / patch.bundled_files[0]["source"]).read_bytes())

    original = Path.unlink

    def refuse(self, *args, **kwargs):
        if self == stale:
            raise OSError("the game is running")
        return original(self, *args, **kwargs)

    # straight at the installer: run_patches would report BLOCKED on this
    # patch's prerequisites and never reach the code under test
    Path.unlink = refuse
    try:
        result = engine_mod.apply_bundled_install(tmp_path, manifest, patch)
    finally:
        Path.unlink = original

    assert result.status == PatchStatus.FAILED
    assert "Close the game" in result.detail
    # and the run is taken back rather than left as a second copy nothing records,
    # so the old one carries on working until the game is closed
    landed = engine_mod.install_target_dir(tmp_path, patch) / patch.bundled_files[0]["target"]
    assert stale.is_file() and not landed.exists()


def test_the_ui_badge_reads_the_files_not_the_record(tmp_path):
    """
    The one that kept a real install broken. scan_patch_status is what drives
    the row badge and Apply's already-applied check, and for a bundled_install
    it only looked at the json record. Steam's verify deletes d3d9.dll, or a
    different NovaFix build replaces it, and the row still says DONE while the
    patcher never puts ours back.
    """
    from patcher.engine import install_target_dir
    from patcher.state import record_applied, scan_patch_status

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    proxy = next(p for p in manifest.patches if p.id == "install_novafix")
    state = tmp_path / "state.json"

    lay_down_chain(tmp_path, manifest)
    record_applied(state, proxy.id, ["d3d9.dll"], {"dir": ""})
    assert scan_patch_status(tmp_path, manifest, state)[proxy.id] is True

    dll = install_target_dir(tmp_path, proxy) / "d3d9.dll"
    dll.write_bytes(dll.read_bytes()[:-91136])
    assert scan_patch_status(tmp_path, manifest, state)[proxy.id] is False, "record over bytes"

    dll.unlink()
    assert scan_patch_status(tmp_path, manifest, state)[proxy.id] is False


def test_every_poster_is_the_same_shape_as_the_clip_it_stands_in_for():
    """
    The shake. A <video> shows its poster until the decoder produces a frame,
    and object-fit:cover crops against the INTRINSIC ratio of whatever is
    showing. The posters were 1456x840, which is 1.733:1, while the clips are
    2560x1440, which is 16:9. So the poster was cropped 2.5% vertically and the
    video was not, and the picture jumped the moment the decoder took over.

    Only visible entering a game from the startup list, because switching from
    inside menu 2 crossfades an outgoing layer over the swap.
    """
    from pathlib import Path as P

    try:
        from PIL import Image
    except ImportError:
        pytest.skip("pillow not installed")

    assets = P(__file__).resolve().parent.parent / "assets"
    posters = sorted(assets.glob("*.jpg"))
    assert posters, "no stills to check"
    for poster in posters:
        with Image.open(poster) as im:
            width, height = im.size
        assert abs(width / height - 16 / 9) < 0.002, \
            f"{poster.name} is {width}x{height} ({width/height:.4f}), the clips are 16:9"


def test_the_window_waits_for_the_font_before_it_is_shown():
    """
    shell.py holds the window back until __uiReady. That waited for the art
    bitmap only, and the font lands well after it: measured at boot, the moment
    document.fonts.ready resolved the game list went from 140px tall to 262px
    in one frame, and centreText wrote --text-nudge at the same instant, which
    moves the baseline of every label in the app.

    So the window was being shown onto a layout that had not settled, which is
    the jolt on first open. Both halves have to land before anyone sees it.
    """
    ui = without_comments(read_ui_text())
    start = ui.index("const start = () => {")
    body = ui[start:ui.index("};", start)]
    assert "document.fonts.ready" in body, "readiness ignores the font again"
    assert "window.__uiReady = true" in body
    # inside the then, not before it
    gate = body.index("document.fonts.ready")
    assert body.index("window.__uiReady = true") > gate

    # and the shell still has its own deadline, so a font that never loads
    # costs a wait rather than a window that never appears
    shell = (Path(__file__).resolve().parent.parent / "shell.py").read_text(encoding="utf-8")
    assert "deadline = time.time() + 6.0" in shell


def test_pressing_apply_takes_the_hook_off_a_broken_install(tmp_path):
    """
    End to end on the state a real user is sitting in: stock Steam build, 4GB
    patch applied by an older version of this reasoning, redirect add-on live,
    game refusing to start. One Apply has to leave them with a working game.
    """
    from patcher.engine import install_target_dir
    from patcher.patch_ops import flip_laa_flag, is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").write_bytes(clean)

    addon = next(p for p in manifest.patches if p.id == "install_exe_redirect")
    home = install_target_dir(tmp_path, addon)
    home.mkdir(parents=True)
    (home / "00_ff13_exe_redirect.asi").write_bytes(b"MZ")

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.REPAIRED

    assert exe.read_bytes() == clean, "executable not put back"
    assert not (home / "00_ff13_exe_redirect.asi").exists(), "the hook is still armed"
    assert not home.exists()
    # the backup stays: nothing reads it now and it is their only clean copy
    assert exe.with_name("untouched.exe").is_file()


def test_the_hook_comes_off_even_when_the_executable_is_already_clean(tmp_path):
    """
    The case that made the diagnosis. The add-on arms on untouched.exe
    existing, NOT on the executable being patched, so undoing the 4GB patch
    left the hook in place and the game still would not start. Removing it has
    to happen on its own, not as a side effect of repairing something.
    """
    from patcher.engine import install_target_dir
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(clean)
    exe.with_name("untouched.exe").write_bytes(clean)

    addon = next(p for p in manifest.patches if p.id == "install_exe_redirect")
    home = install_target_dir(tmp_path, addon)
    home.mkdir(parents=True)
    (home / "00_ff13_exe_redirect.asi").write_bytes(b"MZ")

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.UNSAFE_BUILD
    assert not is_laa_enabled(exe.read_bytes())
    assert not (home / "00_ff13_exe_redirect.asi").exists()


def test_the_doctor_tells_our_addon_apart_from_somebody_elses(tmp_path):
    """
    The report is what somebody reads when the game will not start, and for a
    while it printed "ok  addons  ff13-exe-redirect" over the exact thing
    causing it. Presence is not the question: an .asi under this name that is
    not our pinned build is an older hook nobody here has reasoned about, and
    that one armed whenever untouched.exe existed.
    """
    from patcher.doctor import BAD, OK, WARN, diagnose
    from patcher.engine import install_target_dir

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    library = steam_library(tmp_path, "ff13", synthetic_xiii(*ORIGINAL_CEG))
    install = library / "steamapps" / "common" / "game"
    lay_down_chain(install, manifest, skip=("install_exe_redirect",))

    def verdict():
        return next((f for f in diagnose("ffxiii", steam_library_paths=[library]).findings
                     if "exe-redirect" in f.title), None)

    missing = verdict()
    assert missing is not None and missing.level == OK
    assert "render init" in missing.detail

    addon = next(p for p in manifest.patches if p.id == "install_exe_redirect")
    home = install_target_dir(install, addon)
    home.mkdir(parents=True, exist_ok=True)
    (home / "00_ff13_exe_redirect.asi").write_bytes(b"MZ")

    bad = verdict()
    assert bad is not None and bad.level == BAD
    assert "3:0000065432" in bad.detail

    lay_down_chain(install, manifest)   # now the real thing
    good = verdict()
    assert good is not None and good.level == OK


def test_the_dinput8_shim_is_what_lets_the_4gb_patch_on(tmp_path):
    """
    The load order is the whole thing. ffxiiiimg.exe imports dinput8.dll
    statically, so the loader maps our proxy and runs its DllMain before the
    entry point, and the CEG check is in the entry point. That is the timing
    FF13Fix had as a d3d9.dll proxy and the timing a NovaFix add-on cannot
    have, because add-ons load at render init and a refused process never gets
    there.
    """
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))

    # nothing in front of the check
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.UNSAFE_BUILD
    assert not is_laa_enabled(exe.read_bytes())

    # our pinned shim beside the executable
    lay_down_chain(tmp_path, manifest)
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.APPLIED
    assert is_laa_enabled(exe.read_bytes())


def test_somebody_elses_dinput8_is_not_accepted_as_cover(tmp_path):
    """
    The game imports this file statically, so whatever sits there runs first
    with the game's privileges. Only the pinned build is accepted as cover for
    the 4GB patch.

    It is not deleted, though. That filename is a decade-old modding
    convention and the next person's ReShade or input fix lives under it too.
    Refusing to patch is our call to make; removing their mod is not.
    """
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    stranger = exe.parent / "dinput8.dll"
    stranger.write_bytes(b"MZ not ours")

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)

    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.UNSAFE_BUILD
    assert not is_laa_enabled(exe.read_bytes())
    assert stranger.read_bytes() == b"MZ not ours", "deleted a mod we did not install"


def test_undoing_the_patch_takes_the_shim_with_it(tmp_path, monkeypatch):
    """
    Half a repair is worse than none. The shim is a static import, so an
    install with the patch undone and the shim still there is a game that will
    not start for a reason nothing on screen mentions.
    """
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.patch_ops import flip_laa_flag, is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").write_bytes(clean)
    lay_down_chain(tmp_path, manifest)
    shim = exe.parent / "dinput8.dll"
    (exe.parent / "ff13-dinput8-shim.log").write_text("armed")
    assert shim.is_file()

    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", engine_module.COVER_SHIM)

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)

    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.REPAIRED
    assert exe.read_bytes() == clean
    assert not is_laa_enabled(exe.read_bytes())
    assert not shim.exists(), "the game still imports a dll that no longer has a job"
    assert not (exe.parent / "ff13-dinput8-shim.log").exists()
    # and the backup stays, because it is the only clean copy they have
    assert exe.with_name("untouched.exe").is_file()


def test_an_older_build_of_the_shim_is_never_kept_as_theirs(tmp_path):
    """
    with no record to go by it looks like anybody's dinput8.dll, and Revert
    would put an old dev build back in the loader's path
    """
    from patcher.engine import SHIM_LOG_MARK, install_target_dir

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    shim = next(p for p in manifest.patches if p.id == "install_dinput8_shim")
    home = install_target_dir(tmp_path, shim)
    home.mkdir(parents=True)
    (home / "ffxiiiimg.exe").write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    (home / "dinput8.dll").write_bytes(b"MZ older shim " + SHIM_LOG_MARK)

    results = run_patches(tmp_path, manifest, {"install_dinput8_shim"}, fetch=offline)

    assert next(r for r in results if r.patch_id == shim.id).status == PatchStatus.APPLIED
    assert not (home / "dinput8.dll.backup").exists()


def test_taking_the_shim_off_hands_their_dinput8_back(tmp_path, monkeypatch):
    """the shim set theirs aside to go in, so the repair that strips it puts theirs back"""
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.patch_ops import flip_laa_flag

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").write_bytes(clean)
    lay_down_chain(tmp_path, manifest)
    (exe.parent / "dinput8.dll.backup").write_bytes(b"MZ asi loader")

    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", engine_module.COVER_SHIM)

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)

    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.REPAIRED
    assert (exe.parent / "dinput8.dll").read_bytes() == b"MZ asi loader"
    assert not (exe.parent / "dinput8.dll.backup").exists()


def test_the_shim_stops_reinstalling_itself_after_a_refusal(tmp_path, monkeypatch):
    """
    otherwise every Apply installs it and the 4GB guard strips it back out, and
    the window between those two is an install carrying a static import of the
    game with no patch left to cover
    """
    from patcher import engine as engine_module, launch_watch, state as state_module

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))

    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", engine_module.COVER_SHIM)

    results = run_patches(tmp_path, manifest, {"install_dinput8_shim"}, fetch=offline)

    assert next(r for r in results if r.patch_id == "install_dinput8_shim").status \
        == PatchStatus.UNSAFE_BUILD
    assert not (exe.parent / "dinput8.dll").exists()


def test_the_shim_installs_before_the_patch_that_depends_on_it():
    """
    manifest order decides this. with the patch first, its guard looks for a
    shim that has not been installed yet and refuses every first run.
    """
    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    ids = [p.id for p in manifest.patches]

    assert ids.index("install_dinput8_shim") < ids.index("flip_laa_flag")
    assert ids.index("install_novafix") < ids.index("install_dinput8_shim")


def test_the_shipped_shim_is_a_32_bit_dll_that_can_actually_load():
    """
    A statically imported DLL that fails to load is not a broken feature, it is
    a game that does not start, with no log and nothing to read. So: the right
    machine, marked as a DLL, exporting exactly what the real dinput8 exports
    under undecorated names, and importing nothing exotic enough to be missing.

    The secure CRT is the specific hazard. mingw's import library claims
    msvcrt.dll exports the whole _s family unconditionally, and which of them a
    given Windows actually has varies.
    """
    from pathlib import Path

    from patcher.patch_ops import read_static_imports

    dll = (Path(__file__).resolve().parent.parent
           / "assets" / "mods" / "ff13-dinput8-shim" / "dinput8.dll")
    data = dll.read_bytes()

    assert data[:2] == b"MZ"
    pe = int.from_bytes(data[0x3C:0x40], "little")
    assert data[pe:pe + 4] == b"PE\0\0"
    assert int.from_bytes(data[pe + 4:pe + 6], "little") == 0x014C, "not i386"
    assert int.from_bytes(data[pe + 22:pe + 24], "little") & 0x2000, "not marked as a DLL"

    assert set(read_static_imports(data)) <= {"kernel32.dll", "msvcrt.dll"}


def test_the_shim_has_a_name_for_novafixs_popup():
    """
    NovaFix lists every dll beside the game when it starts, by its version
    resource's ProductName and otherwise by the module's own name, and the game
    imports this one as DINPUT8.dll. That's what the popup said
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    data = (repo / "assets" / "mods" / "ff13-dinput8-shim" / "dinput8.dll").read_bytes()
    # a StringFileInfo entry is its key then its value, both UTF-16, which is
    # all a test needs to see without a PE library the app doesn't otherwise use
    key = data.find("ProductName\0".encode("utf-16-le"))
    value = data.find("FFXIII 4GB Compatibility\0".encode("utf-16-le"))
    assert 0 < key < value < key + 64, (key, value)
    assert 'VALUE "ProductName",      "FFXIII 4GB Compatibility"' in \
        (repo / "addon-src" / "ff13-dinput8-shim" / "version.rc").read_text(encoding="utf-8")
    assert '"$RC" "$src/version.rc"' in (repo / "tools" / "build_shim.sh").read_text(encoding="utf-8")


def test_the_shim_and_novafix_do_not_want_the_same_filename():
    """
    NovaFix is the d3d9 proxy and this is the dinput8 one. Picking the same
    file would mean one of them silently not loading, and NovaFix's author is
    explicit that it must not be stacked with FF13Fix-style setups.
    """
    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    targets = {}
    for patch in manifest.patches:
        if patch.type != "bundled_install":
            continue
        for spec in patch.bundled_files:
            key = (patch.install_relative_path or "", spec["target"])
            assert key not in targets, f"{patch.id} and {targets[key]} both write {key[1]}"
            targets[key] = patch.id


def test_a_refusal_of_one_cover_does_not_condemn_another(tmp_path, monkeypatch):
    """
    The one that shipped broken. A DRM refusal was filed against flip_laa_flag
    with no record of what had been hiding it, so it read as "this install
    cannot take the 4GB patch". It wasn't. It was a refusal of the NovaFix
    add-on, which loads after the check it was meant to beat and could never
    have worked, and it went on to block the dinput8 shim, which is a different
    mechanism at a different point in the process lifetime.

    A verdict measures one cover. Replace the cover and it has to be measured
    again.
    """
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    lay_down_chain(tmp_path, manifest)

    # exactly the shape the shipped build wrote: a refusal with no cover on it
    record = tmp_path / "launch-state.json"
    record.write_text(json.dumps({"applied_patches": {}, "launch_checks": {
        "flip_laa_flag": {"verdict": "drm_error", "detail": "Steam refused it",
                          "checked_at": "2026-09-18T20:22:53+00:00"}}}))
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)

    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.APPLIED, "a verdict about the add-on blocked the shim"
    assert is_laa_enabled(exe.read_bytes())


def test_a_refusal_of_the_shim_does_block_the_shim(tmp_path, monkeypatch):
    """the other half: keyed does not mean toothless"""
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.engine import COVER_SHIM
    from patcher.patch_ops import flip_laa_flag, is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").write_bytes(clean)
    lay_down_chain(tmp_path, manifest)

    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", COVER_SHIM)

    results = run_patches(tmp_path, manifest, {"flip_laa_flag", "install_dinput8_shim"},
                          fetch=offline)

    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.REPAIRED
    assert not is_laa_enabled(exe.read_bytes())
    assert not (exe.parent / "dinput8.dll").exists()
    assert next(r for r in results if r.patch_id == "install_dinput8_shim").status \
        == PatchStatus.UNSAFE_BUILD


def test_the_refusal_is_explained_even_once_the_cover_is_gone(tmp_path, monkeypatch):
    """
    By the second Apply the shim has been stripped, so the current cover is
    none and the plain "this build can't take the patch" line would be what
    somebody reads an hour after watching the tool try it.
    """
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.engine import COVER_SHIM

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))

    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", COVER_SHIM)

    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    detail = next(r for r in results if r.patch_id == "flip_laa_flag").detail

    assert "Steam's DRM refused" in detail
    assert "community replacement" not in detail


def test_the_cover_recorded_is_the_one_that_was_in_place(tmp_path):
    """
    current_cover is one function so the guard and the launch check cannot
    disagree about what was standing there, which is the drift that files a
    verdict against the wrong thing.
    """
    from patcher.engine import COVER_NONE, COVER_PROXY, COVER_SHIM, current_cover

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))

    assert current_cover(tmp_path, manifest) == COVER_NONE

    write_novafix_ini(exe.parent, RedirectExecutableReads="On")
    assert current_cover(tmp_path, manifest) == COVER_PROXY

    # the shim outranks it: it is the thing we ship and the thing we verified
    lay_down_chain(tmp_path, manifest)
    assert current_cover(tmp_path, manifest) == COVER_SHIM

    # and somebody else's dinput8.dll is not our shim
    (exe.parent / "dinput8.dll").write_bytes(b"MZ nope")
    assert current_cover(tmp_path, manifest) == COVER_PROXY


def test_a_second_cover_gets_its_own_turn_after_the_first_fails(tmp_path, monkeypatch):
    """
    Falls out of keying the verdict, and is the behaviour we want rather than
    an accident of it. If the shim is refused and the installed proxy says in
    its own config that it redirects executable reads, that is a different
    mechanism at a different layer and nothing has measured it. Refusing it on
    the shim's result would be the same mistake one level down.

    The launch check still decides. All this buys is a second attempt, and an
    attempt that fails is undone the same way the first one was.
    """
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.engine import COVER_PROXY, current_cover
    from patcher.patch_ops import flip_laa_flag, is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").write_bytes(clean)
    lay_down_chain(tmp_path, manifest)
    write_novafix_ini(exe.parent, RedirectExecutableReads="On")

    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", engine_module.COVER_SHIM)

    # the shim was what failed, so it comes off and the patch with it
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.REPAIRED
    assert not (exe.parent / "dinput8.dll").exists()

    # and now the proxy's own claim is the cover, untested, so it gets tried
    assert current_cover(tmp_path, manifest) == COVER_PROXY
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.APPLIED
    assert is_laa_enabled(exe.read_bytes())

    # until that one is refused too, at which point there is nothing left
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", COVER_PROXY)
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.REPAIRED
    assert not is_laa_enabled(exe.read_bytes())


def test_the_shim_guard_asks_about_the_shim_not_about_whatever_is_there(tmp_path,
                                                                        monkeypatch):
    """
    The guard has to name COVER_SHIM rather than reading the current cover,
    because by the time it runs the shim is usually gone: the previous Apply
    stripped it on the way to undoing the patch. Reading the current cover then
    answers about something else, the shim reinstalls, the 4GB guard strips it
    again, and every Apply reports an install and a removal of the same file.
    """
    from patcher import engine as engine_module, launch_watch, state as state_module

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    # the shim already stripped, and something else standing there instead, so
    # the current cover is emphatically not the shim
    write_novafix_ini(exe.parent, RedirectExecutableReads="On")
    assert not (exe.parent / "dinput8.dll").exists()
    assert engine_module.current_cover(tmp_path, manifest) == engine_module.COVER_PROXY

    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", engine_module.COVER_SHIM)

    results = run_patches(tmp_path, manifest, {"install_dinput8_shim"}, fetch=offline)

    assert next(r for r in results if r.patch_id == "install_dinput8_shim").status \
        == PatchStatus.UNSAFE_BUILD, "reinstalled the shim that was already refused"
    assert not (exe.parent / "dinput8.dll").exists()


def test_a_dinput8_that_is_not_ours_is_left_where_it_is(tmp_path):
    """
    dinput8.dll is a decade-old modding convention and the next person's
    ReShade or input fix lives under it too. Deleting somebody's mod because it
    shares a name with ours is not a repair, it is a different kind of damage,
    so an unrecognised one is reported rather than removed.
    """
    from patcher.engine import strip_dinput8_shim

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(synthetic_xiii(*ORIGINAL_CEG))
    stranger = exe.parent / "dinput8.dll"
    stranger.write_bytes(b"MZ somebody else's mod")

    assert strip_dinput8_shim(tmp_path, manifest) == []
    assert stranger.is_file(), "deleted a mod we did not install"

    # and ours does come off, because leaving a static import with no job is
    # a game that will not start
    lay_down_chain(tmp_path, manifest)
    (exe.parent / "ff13-dinput8-shim.log").write_text("armed")
    taken = strip_dinput8_shim(tmp_path, manifest)
    assert sorted(taken) == ["dinput8.dll", "ff13-dinput8-shim.log"]
    assert not stranger.exists()


def test_the_doctor_names_a_dinput8_it_does_not_recognise(tmp_path):
    """
    the other half of leaving it alone: if we are not going to remove it, the
    report has to say it is there, because it runs before the game whatever it
    is
    """
    from patcher.doctor import BAD, diagnose

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    library = steam_library(tmp_path, "ff13", synthetic_xiii(*ORIGINAL_CEG))
    install = library / "steamapps" / "common" / "game"
    lay_down_chain(install, manifest, skip=("install_dinput8_shim",))
    (install / manifest.executable_relative_path).parent.joinpath("dinput8.dll") \
        .write_bytes(b"MZ not ours")

    finding = next(f for f in diagnose("ffxiii", steam_library_paths=[library]).findings
                   if "dinput8" in f.title)

    assert finding.level == BAD
    assert "statically" in finding.detail


LR_BUILD = (82083840, 10383108)


def run_lr_laa(tmp_path, exe_bytes):
    manifest = load_manifest(MANIFESTS / "ffxiii3.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(exe_bytes)
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline)
    return exe, next(r for r in results if r.patch_id == "flip_laa_flag")


def test_lr_takes_the_4gb_patch(tmp_path):
    """
    LRFF13.exe is 32-bit, which NovaFix's lightning_returns adapter states as
    I386 with a PE32 optional header, so without the flag it cannot address
    past 2GB however much memory the machine has.
    """
    from patcher.patch_ops import is_laa_enabled

    exe, result = run_lr_laa(tmp_path, synthetic_xiii(*LR_BUILD))

    assert result.status == PatchStatus.APPLIED
    assert is_laa_enabled(exe.read_bytes())


def test_lr_reads_an_already_set_flag_off_the_disk_as_applied(tmp_path):
    """
    Very likely the state of a real install: reports on the Steam forums say
    LRFF13.exe ships with the flag already set, and nobody has confirmed it
    either way. Either answer has to be fine, and this is the half that makes
    it fine, because the scan reads the bit rather than a record of our own
    work. The row says APPLIED, the engine skips, and 78MB is not rewritten to
    change no bytes.
    """
    from patcher.state import scan_patch_status

    manifest = load_manifest(MANIFESTS / "ffxiii3.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state.json"

    exe.write_bytes(synthetic_xiii(*LR_BUILD))
    assert scan_patch_status(tmp_path, manifest, state)["flip_laa_flag"] is False

    exe.write_bytes(synthetic_xiii(*LR_BUILD, laa=True))
    assert scan_patch_status(tmp_path, manifest, state)["flip_laa_flag"] is True

    before, stamp = exe.read_bytes(), exe.stat().st_mtime_ns
    results = run_patches(tmp_path, manifest, {"flip_laa_flag"}, fetch=offline,
                          already_applied_check=lambda pid: pid == "flip_laa_flag")

    assert next(r for r in results if r.patch_id == "flip_laa_flag").status \
        == PatchStatus.ALREADY_APPLIED
    assert exe.read_bytes() == before and exe.stat().st_mtime_ns == stamp


def test_lr_refuses_an_executable_that_is_not_32_bit(tmp_path):
    """
    A 64-bit rerelease addresses far past 4GB already, so the flag means
    nothing in it and all that could come of running is flipping a bit in a
    header we have misread.
    """
    image = bytearray(synthetic_xiii(*LR_BUILD))
    struct.pack_into("<H", image, 0x84, 0x8664)   # COFF Machine: AMD64

    exe, result = run_lr_laa(tmp_path, bytes(image))

    assert result.status == PatchStatus.UNSAFE_BUILD
    assert "32-bit" in result.detail
    assert exe.read_bytes() == bytes(image)


def test_a_refusal_on_lr_undoes_the_patch_whatever_the_cover(tmp_path, monkeypatch):
    """
    Where this parts company with XIII. There, no cover means the patch is
    refused on its own merits and the record only sharpens the wording. Here,
    no cover is the normal state and the patch is allowed, so the record is the
    only thing between a user and a second executable Steam will not start.
    """
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.patch_ops import flip_laa_flag, is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii3.json")
    clean = synthetic_xiii(*LR_BUILD)
    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "Steam refused the patched executable",
                               engine_module.COVER_NONE,
                               evidence={"ran": 1.0, "window": True})

    exe, result = run_lr_laa(tmp_path, flip_laa_flag(clean))

    assert result.status == PatchStatus.REPAIRED
    assert not is_laa_enabled(exe.read_bytes())
    assert exe.read_bytes() == clean, "clearing the flag is not an exact inverse"
    from patcher.engine import held_off

    assert held_off(tmp_path, manifest) == {
        "flip_laa_flag": "Steam refused to start the game with it on, so it stays off on this "
                         "install. The game starts without it."}


def test_an_lr_refusal_from_before_the_launch_check_saw_anything_keeps_nothing_off(
        tmp_path, monkeypatch):
    """
    src_38's check filed any exit in the first twelve seconds, and a user's LR
    got one from a crash that kept it down with the patch off too. With no
    cover it's the only thing keeping the patch off, so one that doesn't say
    what was seen is measured again
    """
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.engine import held_off
    from patcher.patch_ops import is_laa_enabled

    manifest = load_manifest(MANIFESTS / "ffxiii3.json")
    record = tmp_path / "launch-state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "Steam refused the patched executable",
                               engine_module.COVER_NONE)

    assert held_off(tmp_path, manifest) == {}
    exe, result = run_lr_laa(tmp_path, synthetic_xiii(*LR_BUILD))

    assert result.status == PatchStatus.APPLIED
    assert is_laa_enabled(exe.read_bytes())


def test_the_launch_check_watches_every_game_with_a_4gb_patch(tmp_path):
    """
    _laa_context used to name ff13_laa, so adding LR's patch would have wired
    it up with nothing watching it: the one game whose DRM status nobody knows
    would have been the one game the launch check ignored.
    """
    from patcher.engine import laa_patch

    watched = {}
    for name in ("ffxiii", "ffxiii2", "ffxiii3"):
        manifest = load_manifest(MANIFESTS / f"{name}.json")
        patch = laa_patch(manifest)
        watched[name] = patch.id if patch else None

    assert watched == {"ffxiii": "flip_laa_flag", "ffxiii2": None,
                       "ffxiii3": "flip_laa_flag"}
