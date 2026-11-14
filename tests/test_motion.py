"""
What the page keeps moving, and what it lets rest.

Measured in headless Chromium with software compositing, which is what a
blocklisted GPU, RDP, a VM or Wine get: the still's drift cost 505ms of CPU a
second at 720p, the same drift under menu 2's opaque cutscene layer cost the
same for nothing, the fog clip decoded under that layer for the length of an
HD install, and the glove's mouse tracking laid the page out on every frame.
tools/motion_probe.js drives the behaviour; these pin the stylesheet it rests
on, which jsdom can't run.
"""

import json
import sys
import re
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "ui" / "index.html"


def rule(text: str, selector: str) -> str:
    at = text.index(selector + "{")
    return text[at:text.index("}", at)]


def test_the_drift_steps_rather_than_painting_every_frame():
    """four steps a second, a fraction of a pixel each, where every frame was a full repaint"""
    text = UI.read_text(encoding="utf-8")
    drift = rule(text, "#bg.still::before")
    assert re.search(r"animation:drift 48s steps\(\d+\)", drift), drift
    steps = int(re.search(r"steps\((\d+)\)", drift).group(1))
    assert 48 * 2 <= steps <= 48 * 8, "slower is visible, faster is the cost coming back"
    assert "steps(" in rule(text, "#bg.fallback::before")


def test_a_list_comes_in_a_row_at_a_time_and_goes_at_once():
    """
    the game's menus leave a gap you can watch between one row and the next,
    so ours are a beat apart on the way in. Out they go together, or Apply,
    which a Q/E switch doesn't rebuild, was still leaving when the next list
    came and never slid back in
    """
    text = re.sub(r"/\*.*?\*/", "", UI.read_text(encoding="utf-8"), flags=re.S)
    beat = re.search(r":root\{--beat:calc\((\d+)ms \* var\(--pace\)\)\}", text)
    assert beat and int(beat.group(1)) >= 40, "back to a gap too quick to see"
    for leaving in (".sect .row", "\n#goWrap"):
        assert "transition-delay" not in rule(text, leaving), leaving
    for coming in (".sect.on .row", "\n#sectGo.on #goWrap"):
        assert "transition-delay:calc(var(--i,0) * var(--beat))" in rule(text, coming), coming
    # a switch's Launch on those beats too, after Apply rather than before it
    assert "body.sw{--in-at:0s}" in text
    assert 'document.body.classList.add("in2", "sw");' in text


def test_a_launch_the_scan_offers_late_takes_whats_left_of_its_beat():
    """
    the scan that offers Launch answers some time after the list has started
    in, and Launch counted its whole beat from then: four beats behind Apply
    on a 100ms scan, ten on a 500ms one
    """
    text = re.sub(r"/\*.*?\*/", "", UI.read_text(encoding="utf-8"), flags=re.S)
    launch = rule(text, "body.in2 #sectGo #launchWrap")
    assert launch.count("max(0s, calc(var(--in-at) + var(--i,0) * var(--beat) - var(--late,0s)))") \
        == 2, launch
    # counted from when the list started in, both ways in
    assert text.count('startIn();\n    document.body.classList.add("in2"') == 2
    assert "`${performance.now() - inAt}ms`" in text


def test_nothing_drifts_under_menu_two():
    """the cutscene layer there is opaque, and a patch run sits under it for minutes"""
    text = UI.read_text(encoding="utf-8")
    assert "body.m2 #bg::before{animation-play-state:paused}" in text
    cinema = rule(text, "body.m2 #cinema")
    assert "opacity:1" in cinema, "the pause is only invisible while this covers the fog"
    assert "background:#0a0e12" in rule(text, "#cinema"), "and only if it is opaque"


def test_a_games_screen_rests_the_fog_once_its_covered():
    """
    it played on under the clip's opaque layer, which redrew the window two
    dozen times a second for a picture nobody could see, and took that headroom
    from every clip Q/E plays over it. Held once the layer's all the way up,
    let go as soon as the way back starts, so it's moving by the time it shows
    """
    text = UI.read_text(encoding="utf-8")
    pick = text[text.index("function pick(id, fromCycle){"):text.index("/* Q/E spam collapses")]
    covered = pick[pick.index('afterMotion([$("#cinema")], FADE_MS).then(() => {'):]
    covered = covered[:covered.index("});")]
    # only if the screen it was asked for is still the one up
    assert 'if (moving === moveGen && document.body.classList.contains("m2")) Fog.hold(true);' \
        in covered
    # and never at the moment of going in, install or not, or the fog stops
    # dead while it's still showing through the fade
    assert pick.count("Fog.hold(") == 1
    back = text[text.index("function back(){"):]
    assert "Fog.hold(false);" in back[:back.index("\n}\n")]
    # and an install ending under it doesn't wake it for nothing
    stop = text[text.index("  function stop(){"):]
    assert 'if (!document.body.classList.contains("m2")) Fog.hold(false);' in stop[:stop.index("\n  }")]


def test_the_glove_sways_for_a_while_rather_than_forever():
    """
    an idle glove kept the compositor at 60fps. It parks on its rest pose
    (forwards), which keeps the animation around for a key to play again
    """
    text = UI.read_text(encoding="utf-8")
    for selector, name in (("#cursor.target", "sway"), ("#cursor.link", "swayY")):
        animation = re.search(rf"animation:{name} [\d.]+s ease-in-out (\S+) (\S+)}}",
                              rule(text, selector) + "}")
        assert animation, rule(text, selector)
        assert animation.group(1).isdigit() and int(animation.group(1)) >= 8, animation.group(0)
        assert animation.group(2) == "forwards", "without it a finished sway can't be played again"
    keys = text[text.index('addEventListener("keydown", () => {\n  for (const anim of cur.getAnimations'):]
    assert 'anim.playState === "finished") anim.play()' in keys[:300]


def test_the_glove_moves_without_layout():
    """translate composes ahead of transform, so the anchor offset and press scale still hold"""
    text = UI.read_text(encoding="utf-8")
    for selector in ("#cursor.target", "#cursor.link"):
        assert "transition:translate " in rule(text, selector), selector
        assert "left " not in rule(text, selector) and "top " not in rule(text, selector)
    shade = text[text.index("#cursorShade{position:absolute"):]
    assert "transition:translate " in shade[:shade.index("}")]
    place = text[text.index("function place(x, y = curY){"):]
    place = place[:place.index("\n}\n")]
    assert "style.translate" in place and "style.left" not in place
    pointer = text[text.index("const Pointer = {"):text.index("/* mouse coordinates are viewport px")]
    assert not re.search(r"cur\.style\.(left|top)\s*=", pointer), "a layout write slipped back in"


def test_each_games_menu_is_its_own_look_and_only_a_change_of_look_fades():
    """
    XIII's light bars and HELP mark, XIII-2's and LR's dark ones. The dark
    glass and the other mark are laid over XIII's and faded in, so a switch
    across the two is one cross-fade, and XIII-2 to LR, one look, moves nothing
    """
    games = json.loads((REPO / "ui" / "jobs.json").read_text(encoding="utf-8"))["games"]
    assert {g["id"]: g.get("look") for g in games} == {"ff13": "xiii", "ff13-2": "xiii2",
                                                       "lr": "xiii2"}
    text = UI.read_text(encoding="utf-8")
    dusk = rule(text, "\n.dusk")
    assert "opacity:0" in dusk and "transition:opacity var(--look-fade)" in dusk
    assert 'body[data-look="xiii2"] .dusk{opacity:1}' in text
    for bar in ("#help", "#foot"):
        assert f'<{"header" if bar == "#help" else "footer"} id="{bar[1:]}">\n      <i class="dusk"' \
            in text, bar
    # the footer's own items over its glass
    assert "#foot > :not(.dusk){position:relative}" in text
    late = rule(text, ".help-mark .qm.late")
    assert "position:absolute" in late and "opacity:0" in late
    assert 'body[data-look="xiii2"] .help-mark .qm:not(.late){opacity:0}' in text
    assert '<img class="qm late" src="../assets/help_icon_xiii2.png" alt="" aria-hidden="true">' \
        in text
    # the type fades with the glass rather than snapping white over it
    assert "color var(--look-fade) ease, text-shadow var(--look-fade) ease" in rule(
        text, "\n#hintText")
    assert "color var(--look-fade) ease" in rule(text, "\n#pathTxt")
    # set where the game on screen changes, and nowhere it'd fade for nothing
    script = text[text.index("function look(id){"):]
    assert "if (document.body.dataset.look !== want)" in script[:script.index("\n}\n")]
    for where, call in (("function preview(id, quiet){", "look(id);"),
                        ("function pick(id, fromCycle){", "look(id);"),
                        ("function cycle(dir){", "look(list[queuedIdx].id);"),
                        ("function commitCycle(){", "look(target);")):
        body = text[text.index(where):]
        assert call in body[:body.index("\n}\n")], where


def test_a_real_dom_rests_what_nobody_can_see():
    """tools/motion_probe.js, skipped without node and jsdom"""
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
    result = subprocess.run(["node", str(REPO / "tools" / "motion_probe.js")],
                            capture_output=True, text=True, cwd=REPO, env=env, timeout=180)
    if "Cannot find module 'jsdom'" in result.stderr:
        pytest.skip("jsdom is not installed (npm install jsdom)")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_windowed_exe_never_polls_the_page_for_diagnostics(monkeypatch, capsys):
    """
    print() goes nowhere without a console, and every poll was a UI-thread
    round trip and a callback pywebview keeps for the life of the window
    """
    import shell

    asked, started = [], []

    class Window:
        def evaluate_js(self, script):
            asked.append(script)
            # the backlog the page logged before it was asked, as pywebview hands it back
            if "__diag" in script:
                return json.dumps(["boot: window ready at 230ms", "   316ms  955KB  art_xiii.png"])
            return "ui-ready"

        def show(self):
            pass

    def thread(*args, target=None, **kwargs):
        started.append(target)
        return types.SimpleNamespace(start=lambda: None)

    console = sys.stdout                      # capsys's, so what's printed can be read
    monkeypatch.setattr(shell.threading, "Thread", thread)
    monkeypatch.setattr(shell.time, "sleep", lambda s: None)
    monkeypatch.setattr(shell.sys, "stdout", None)
    shell.show_when_ready(Window())
    assert not started
    assert not [s for s in asked if "__diag" in s], asked

    # a console to print to gets the backlog once and the page sends the rest,
    # which is how boot timings report. Nothing polls in either case
    monkeypatch.setattr(shell.sys, "stdout", console)
    capsys.readouterr()
    shell.show_when_ready(Window())
    assert not started
    once = [s for s in asked if "__diag" in s]
    assert len(once) == 1 and "window.__diagPush = true" in once[0] and ".splice(0)" in once[0]
    out = capsys.readouterr().out
    assert "   boot: window ready at 230ms\n" in out and "art_xiii.png" in out
    assert not hasattr(shell, "drain_diag")


def test_every_load_of_the_page_is_asked_for_its_diagnostics():
    """
    asked once at show, a reload under --debug left __diagPush unset in the new
    page and the terminal heard nothing from it for the rest of the session
    """
    src = (REPO / "shell.py").read_text(encoding="utf-8")
    main = src[src.index("    api.bind(win)"):src.index("    def ready(")]
    assert "if sys.stdout is not None:" in main
    assert "loaded += lambda: report_diag(win)" in main


def test_the_shell_prints_what_the_page_sends(monkeypatch, capsys):
    import shell
    api = shell.Api()
    api.diag(["boot: window ready at 230ms", 7])
    api.diag("rendering: software")
    api.diag({"not": "lines"})
    out = capsys.readouterr().out
    assert "   boot: window ready at 230ms\n" in out and "   7\n" in out
    assert "   rendering: software\n" in out and "not" not in out
    # a page that floods it can't take the console with it
    api.diag(["x" * 5000] * 300)
    out = capsys.readouterr().out.splitlines()
    assert len([line for line in out if line.strip()]) == 200
    assert max(len(line) for line in out) == 3 + 2000


def games():
    return json.loads((REPO / "ui" / "jobs.json").read_text(encoding="utf-8"))["games"]


def test_every_cut_has_a_720p_tier_beside_it():
    """named after the full one, so the pair is obvious in the assets folder"""
    for game in games():
        if game.get("cut"):
            assert game.get("cut720") == game["cut"].replace(".webm", "_720.webm"), game["id"]


def test_the_720p_cut_is_only_asked_for_at_1x_or_under():
    """
    the cut covers the stage, so once that's drawn past 1280x720 a 720p cut
    is upscaled. The stage takes a maximised window's shape now, so what's
    drawn is the cover, not the zoom alone. The 1440p one stays next in line,
    which is also where a missing 720p file falls through to
    """
    text = UI.read_text(encoding="utf-8")
    pick = text[text.index("function cutSources(g){"):]
    pick = pick[:pick.index("\n}\n")]
    assert "const drawn = Math.max(STAGE_W / BASE_W, STAGE_H / BASE_H) * SCALE * (devicePixelRatio || 1);" in pick
    assert "const small = cutSmall || drawn <= 1.05;" in pick
    assert '[small && g.cut720,"video/webm"],[g.cut,"video/webm"]' in pick
    play = text[text.index("function playCut(g, now){"):]
    play = play[:play.index("\n}\n")]
    assert "const sources = cutSources(g);" in play and "sources.forEach(" in play


def test_a_clip_thats_not_there_is_asked_for_once():
    """
    an assets folder without the 720p cuts paid a round trip to the server for
    each one on every way into a game, and the warm-up held the missing file
    rather than the one that plays instead, so that streamed off the server
    the first time. And a game whose every file has 404'd gets its poster,
    never a <video> with nothing to pick from, which reads as blocked media
    """
    text = UI.read_text(encoding="utf-8")
    clips = text[text.index("const Clips = (() => {"):]
    clips = clips[:clips.index("\n})();")]
    assert "if (r.status === 404) gone.add(k);" in clips
    assert "gone: url => gone.has(key(url))" in clips
    pick = text[text.index("function cutSources(g){"):]
    pick = pick[:pick.index("\n}\n")]
    assert ".filter(([src]) => src && !Clips.gone(src));" in pick
    warm = text[text.index("function preloadArt(){"):]
    warm = warm[:warm.index("\n}\n")]
    assert "Clips.want(first[0]).then(() => Clips.gone(first[0]) ? hold(g) : null)" in warm
    play = text[text.index("function playCut(g, now){"):]
    play = play[:play.index("\n}\n")]
    assert "if (!sources.length){" in play
    assert play.index("if (!sources.length){") < play.index('document.createElement("video")')
    # and not marked dead for it, which would draw the gradient on the next visit
    gone = play[play.index("if (!sources.length){"):]
    gone = gone[:gone.index("\n  }\n")]
    assert "cutDead" not in gone and "cutStill(g)" in gone


def test_h264_on_the_gpu_takes_the_full_size_cut():
    """
    plenty of GPUs decode H.264 and leave VP9 to the CPU, and the mp4s are
    H.264, so where the browser says so they lead, and one that can't keep up
    steps off to the webm before anything turns into a still
    """
    text = UI.read_text(encoding="utf-8")
    ask = text[text.index("function cutDecode(){"):]
    ask = ask[:ask.index("\n}\n")]
    assert "decodes('video/mp4; codecs=\"avc1.640034\"', 3840, 2160, 60000000)" in ask
    assert "cutMp4 = !!(h && h.supported && h.powerEfficient && h.smooth);" in ask
    pick = text[text.index("function cutSources(g){"):]
    pick = pick[:pick.index("\n}\n")]
    assert "const gpu = cutMp4 && (drawn > 1.05 || !g.cut720 || Clips.gone(g.cut720));" in pick
    assert pick.index('[gpu && g.cutMp4,"video/mp4"]') < pick.index('[small && g.cut720,"video/webm"]')
    watch = text[text.index("function watchQuality(v){"):]
    watch = watch[:watch.index("\n}\n")]
    assert watch.index("cutMp4 = false; strikes = 0;") < watch.index("cutSmall = true; strikes = 0;")
    # and one that loads and then won't decode, which the element never moves
    # on from by itself
    play = text[text.index("function playCut(g, now){"):]
    play = play[:play.index("\n}\n")]
    assert 'const h264 = cutMp4 && !blip && !!from && /\\.mp4$/.test(from.dataset.file || "");' in play
    assert "return playCut(g, true);" in play


def test_a_decoder_that_cant_keep_up_starts_on_the_720p_cuts():
    """
    without hardware VP9 the 1440p cuts played choppy, and the quality watch
    only stepped down after two of them. The browser says up front whether the
    GPU decodes them and how they've played here before, and the answer is in
    before the cuts are fetched, so the ones held are the ones that'll play
    """
    text = UI.read_text(encoding="utf-8")
    ask = text[text.index("function cutDecode(){"):]
    ask = ask[:ask.index("\n}\n")]
    assert "decodingInfo" in ask and "(r.powerEfficient && r.smooth)" in ask
    assert "cutSmall = true;" in ask
    assert "whenReady(() => cutDecode().then(preloadArt));" in text


def test_the_720p_cuts_are_720p():
    """a quarter of the 1440p decode, and drawn 1:1 in a window at 1x"""
    import shell

    clips = [REPO / "assets" / Path(g["cut720"]).name for g in games() if g.get("cut720")]
    present = [clip for clip in clips if clip.is_file()]
    if not present:
        pytest.skip("the clips aren't in this checkout")
    for clip in present:
        assert shell.webm_size(clip) == (1280, 720), clip.name
