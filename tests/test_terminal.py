"""
The embedded terminal, after a live run made three things obvious at once.

An install failed with "unpacking white_scru stopped with code 1" and nothing
else. The tool had said why, on stderr, into a pipe that was being merged into
stdout and then thrown away at the point of failure. So: the streams are split,
stderr is red, and the reason travels with the failure.

The rest is what you notice once you have actually sat in front of it for forty
minutes. The pane cannot be got out of the way, scrolling it drags the stage
behind it, and its own buttons sit underneath the drawer.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def ui():
    return (REPO / "ui" / "index.html").read_text(encoding="utf-8")


def module(name):
    text = ui()
    start = text.index(f"const {name} = (() =>")
    rest = text[start + 10:]
    return text[start:start + 10 + rest.index("\n})();")]


def test_stderr_is_rendered_differently_from_stdout():
    text = ui()
    assert "#termOut .ln.err{" in text, "no style for stderr, so it reads as more filenames"

    rule = text[text.index("#termOut .ln.err{"):]
    rule = rule[:rule.index("}")]
    assert re.search(r"color:#[0-9a-f]{6}", rule), "stderr has no colour of its own"
    # a colour alone is not enough at 11.5px against a wall of grey. The wash
    # is what makes four red lines findable in three thousand.
    assert "background:" in rule, "stderr has no fill, so it does not stand out when scrolling"


def test_the_stream_tag_reaches_the_class():
    body = module("Hd")
    assert "ev.stream" in body, "the stream is sent and the page ignores it"
    assert 'ev.stream === "out"' in body, \
        "stdout is not distinguished, so every ordinary line gets a class"


def test_the_shell_sends_a_stream_with_every_line():
    text = (REPO / "shell.py").read_text(encoding="utf-8")
    work = text[text.index("def install_hd_project"):text.index("def cancel_hd_project")]

    assert re.search(r'def line\(text, stream=', work), \
        "the shell's line callback still takes text alone, so the stream is dropped"
    assert 'emit("line", text=text, stream=stream)' in work


def test_a_failure_carries_the_reason_and_the_log():
    text = (REPO / "shell.py").read_text(encoding="utf-8")
    work = text[text.index("def install_hd_project"):text.index("def cancel_hd_project")]
    # the tool's failure, not the first ending in the run: the other set's
    # build has an except Cancelled of its own ahead of it
    failed = work[work.index('verdict = (f"FAILED at'):]
    failed = failed[:failed.index("except Cancelled")]

    for field in ('"reason"', '"tail"', '"log"'):
        assert field in failed, f"the failure event has no {field}, which is the whole fix"

    body = module("Hd")
    assert "ev.reason" in body and "ev.tail" in body, "the page drops them again"
    assert "Full log:" in body, "nothing tells anyone where the transcript is"


def test_the_terminal_can_be_got_out_of_the_way_without_closing_it():
    text = ui()
    assert 'id="termMin"' in text, "no minimize button"
    # the log is clipped by the collapsed panel rather than removed from the
    # layout, which is what lets the collapse animate at all
    assert "#term.min{" in text and "#term.min #termOut{display:none}" not in text

    body = module("Term")
    assert "function minimize(" in body and "function expand(" in body

    # a run in progress has to survive it, so minimize collapses and never
    # touches .on. Closing is a separate button that legitimately does.
    minimized = body[body.index("function minimize("):body.index("function toggle(")]
    assert '"min"' in minimized
    assert '"on"' not in minimized, "minimizing takes the terminal down instead of collapsing it"

    # and a new run un-collapses, or the first thing somebody sees after
    # pressing Install is a window that looks like it did nothing
    opened = body[body.index("function open("):body.index("function line(")]
    assert "expand()" in opened


def test_collapsing_still_shows_errors_arriving():
    """
    out of the way must not mean unaware. Minimized is a request for space, not
    a request to miss the line the whole pane exists for.
    """
    body = module("Term")
    assert '"alert"' in body, "a collapsed terminal gives no sign that something failed"
    line = body[body.index("function line("):body.index("function step(")]
    assert 'kind === "err"' in line
    assert "classList.contains(\"min\")" in line, \
        "it marks the header whether or not it is collapsed, which is just noise"


def test_scrolling_the_log_does_not_move_the_stage():
    """
    the reported symptom. Running out of log at the bottom handed the rest of
    the wheel to #app, which scrolls, and the whole game screen slid.
    """
    text = ui()
    rule = text[text.index("#termOut{"):]
    rule = rule[:rule.index("}")]
    assert "overscroll-behavior:contain" in rule


def test_the_terminal_clears_the_drawer_without_ever_resizing():
    """
    Two things at once. Stop and Close are at the right hand end of its header,
    which is exactly where the drawer sits, and they were unclickable with Mods
    open, so there was no way to stop a run from the panel that started it.
    And it used to track whichever drawer was open and slide to match, so
    opening Mods mid install resized the thing you were reading.

    It reserves the drawer's width always, using the drawers' own expression
    rather than a number: #frame is inset:0 inside #app, so min(400px,84%)
    resolves in the same box and lands on the same edge at every window size.
    """
    text = ui()
    rule = text[text.index("#term{position:absolute"):]
    rule = rule[:rule.index("}")]

    assert "right:min(400px,84%)" in rule, "the terminal runs under the drawer"
    # height is the collapse and is meant to animate; width is the one that
    # used to slide under somebody mid install
    assert "transition:height" in rule
    for what in ("transition:right", "transition:width", "transition:all"):
        assert what not in rule, f"{what} puts the resizing back"
    # the comment explaining what it replaced is allowed to name it; code is not
    code = "\n".join(line for line in text.splitlines()
                     if "--side variable" not in line)
    assert "var(--side" not in code, "the measuring machinery is still wired up"
    assert "fitTermBesideDrawers" not in text and "watchDrawers" not in text

    # the same expression the drawers use, so the two cannot drift apart
    drawer = text[text.index("#md{"):]
    drawer = drawer[:drawer.index("}")]
    assert "width:min(400px,84%)" in drawer


def test_the_log_records_how_the_run_ended():
    """
    the first log a user sent back stopped at the tool's last line of output.
    No verdict, no exit code, nothing saying a failure had happened at all,
    which is a strange property for the file that exists to explain one.
    """
    text = (REPO / "shell.py").read_text(encoding="utf-8")
    work = text[text.index("def install_hd_project"):text.index("def cancel_hd_project")]

    assert "log.close(verdict)" in work, "the log is closed without an outcome"
    for word in ("OK.", "FAILED at", "CANCELLED", "REFUSED", "CRASHED"):
        assert word in work, f"no verdict for the {word.lower()} case"

    # set before the try, so a path that returns early still writes one
    assert work.index("verdict = ") < work.index("try:"), \
        "an early return closes the log with an unbound verdict"


def test_a_long_run_shows_how_far_through_it_is():
    """
    894 steps over forty minutes, and the only indication of progress was a
    step counter buried in a header. A bar is the one thing in here that
    answers "how much longer".
    """
    text = ui()
    assert 'id="termBar"' in text and 'role="progressbar"' in text
    assert 'id="termPct"' in text

    body = module("Term")
    assert "function progress(" in body
    # index rather than index+1: a bar reading 100% while the last and longest
    # step is still running is worse than no bar
    step = body[body.index("function step("):body.index("function progress(")]
    assert "index / total" in step and "index + 1) / total" not in step


def test_a_step_with_no_progress_of_its_own_sweeps_rather_than_freezes():
    """
    a bar frozen for the four minutes of a repack is indistinguishable from a
    hang, which is the thing it exists to rule out
    """
    text = ui()
    assert "#termBar.waiting i{" in text
    assert "@keyframes termSweep" in text

    body = module("Term")
    assert "share === null" in body
    opened = body[body.index("function open("):body.index("function line(")]
    assert "progress(null)" in opened, \
        "the backup and the copy in happen before the first step, in silence"


def test_within_step_progress_is_blended_into_the_overall_position():
    """
    the seed copies two thousand files under one step number out of 894. A
    step is a tenth of a percent, so without this the bar does not move
    through the slowest part of the run that is not a tool.
    """
    body = module("Term")
    assert "function within(" in body
    within = body[body.index("function within("):body.index("function finish(")]
    assert "at + " in within and "/ of" in within

    assert 'ev.stage === "progress"' in module("Hd")

    shell = (REPO / "shell.py").read_text(encoding="utf-8")
    work = shell[shell.index("def install_hd_project"):shell.index("def cancel_hd_project")]
    assert "on_share=within" in work
    assert 'emit("progress", share=share)' in work


def test_a_complete_install_does_not_read_as_a_half_broken_one():
    """
    102 ids the mod ships no art for, reported as skips, made a run that
    applied 789 of 789 look like it had gone wrong 102 times
    """
    body = module("Hd")
    done = body[body.index('ev.stage === "done"'):body.index('ev.stage === "cancelled"')]
    assert "ev.nothing" in done
    assert "nothing to install" in done
    # and the two counts stay apart: only a real skip earns the gaps wording
    assert 'ev.skipped ? "Done, with gaps" : "Done"' in done


def test_minimize_and_close_read_as_a_title_bar():
    """
    three buttons in a row all saying words made Close look like one more
    choice rather than the thing that dismisses the panel
    """
    text = ui()
    header = text[text.index('<div id="term">'):text.index('<div id="termBar"')]

    assert '<span class="win">' in header, "the two window controls are not grouped"
    assert "&#x2013;" in header, "minimize is still a word"
    assert "&#x2715;" in header, "close is still a word"
    # Stop keeps its word: it changes what the run does and a glyph for
    # "abandon a forty minute install" is a guess
    assert ">Stop</button>" in header

    # a glyph is not a name, so the name is on the attributes
    for control in ("termMin", "termClose"):
        at = header.index(f'id="{control}"')
        assert 'aria-label="' in header[at:at + 200]
        assert 'title="' in header[at:at + 200]

    rule = text[text.index("#term header .win button{"):]
    rule = rule[:rule.index("}")]
    assert "border:0" in rule and "background:transparent" in rule
    assert "min-width:42px" in rule, "a square target, not a text button"

    assert "#term header .win button.x:hover{" in text, \
        "close has no destructive hover, which is the one convention this borrows"


def test_the_minimize_glyph_changes_with_the_state():
    text = ui()
    for name, glyph, label in (("expand", "\\u2013", "Minimize"),
                               ("minimize", "\\u25A1", "Expand")):
        body = text[text.index(f"  function {name}(){{"):]
        body = body[:body.index("\n  }")]
        assert glyph in body, f"{name} does not set its glyph"
        assert f'"{label}"' in body, f"{name} does not rename the control"
        assert "aria-label" in body and "aria-expanded" in body


def test_collapsing_and_reopening_slide_rather_than_snap():
    """
    it went height:auto and display:none, which are the two values a
    transition cannot start from: the panel vanished in one frame
    """
    text = ui()

    panel = text[text.index("#term{position:absolute"):]
    panel = panel[:panel.index("}")]
    assert "transition:height" in panel, "the height change is not animated"
    assert "overflow:hidden" in panel, "the log spills out during the collapse"

    collapsed = text[text.index("#term.min{"):]
    collapsed = collapsed[:collapsed.index("}") + 1]
    assert "height:auto" not in collapsed, "auto is the one height a transition cannot leave"
    assert "var(--term-head)" in collapsed, "the collapsed height is a magic number"

    # the log stays in the layout and is clipped, rather than being removed
    assert "#term.min #termOut{display:none}" not in text

    # and the header is a height rather than padding, so the collapsed size is
    # a number both rules agree on
    header = text[text.index("#term header{"):]
    header = header[:header.index("}")]
    assert "height:var(--term-head)" in header

    # nothing animates for somebody who asked for that
    reduced = text[text.index("@media (prefers-reduced-motion:reduce){"):]
    assert "#term{transition:none}" in reduced[:reduced.index("}\n")+400]


def test_a_real_installs_stream_in_a_real_dom():
    """
    tools/term_probe.js: a 5000 line burst lands as one append and two layout
    reads, keeps the last 2000 in order, and stderr still flags a collapsed
    pane at once. Skips without node and jsdom.
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

    result = subprocess.run(["node", str(REPO / "tools" / "term_probe.js")],
                            capture_output=True, text=True, cwd=REPO, env=env, timeout=180)
    if "Cannot find module 'jsdom'" in result.stderr:
        pytest.skip("jsdom is not installed (npm install jsdom)")

    assert result.returncode == 0, result.stdout + result.stderr
