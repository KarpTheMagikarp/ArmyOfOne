"""
Running the mod's tools without its batch file.

The three real binaries are Windows PE (two x86, one x64) so they cannot run
here. The stubs below stand in for them: same argument positions, same output
shape, and crucially the same exit code semantics, which are not the obvious
ones. Everything around the process boundary is exercised for real, including
streaming, cancelling and the confinement checks.

What that leaves unverified is exactly one thing: whether the real tools accept
the arguments we build. The plan tests pin those against the batch file so the
gap is as small as it can be from here.
"""

import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.conftest import Log

from patcher.mods import hd_runner
from patcher.mods.hd_runner import (OK_COUNT, OK_IMPORTED, OK_PRODUCED, OK_ZERO, Cancelled,
                                    RunnerError, Step, plan, repack_steps, run,
                                    run_local, run_step, seed_steps, succeeded,
                                    tool_path, unpack_steps)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data" / "hd"


def stub(support: Path, name: str, body: str):
    """
    a stand-in tool. a .py plus a launcher named exactly like the real exe, so
    tool_path resolves the same name it will look for on Windows.
    """
    support.mkdir(parents=True, exist_ok=True)
    script = support / (name + ".py")
    script.write_text(body, encoding="utf-8")
    launcher = support / name
    if sys.platform == "win32":
        launcher.write_bytes(_windows_launcher(script))
    else:
        launcher.write_text(f"#!{sys.executable}\n"
                            f"import runpy,sys\n"
                            f"sys.argv[0]={str(script)!r}\n"
                            f"runpy.run_path({str(script)!r}, run_name='__main__')\n",
                            encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def _windows_launcher(script: Path) -> bytes:
    """
    a real console exe for the stand-in, made the way pip makes one for an
    entry point: distlib's launcher, the interpreter on a #! line, then a zip
    whose __main__ runs the script. A batch file named .exe is no program to
    CreateProcess, which is all Popen has on Windows
    """
    import io
    import zipfile

    from pip._vendor.distlib.scripts import ScriptMaker

    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("__main__.py", "import runpy, sys\n"
                                        f"sys.argv[0] = {str(script)!r}\n"
                                        f"runpy.run_path({str(script)!r}, run_name='__main__')\n")
    python = f'"{sys.executable}"' if " " in sys.executable else sys.executable
    return ScriptMaker(None, None)._get_launcher("t") + f"#!{python}\n".encode() + body.getvalue()


@pytest.fixture
def mod(tmp_path, monkeypatch):
    """a mod folder with stubbed tools, named as the real ones are"""
    root = tmp_path / "HDProject"
    support = root / "SupportFiles"
    (root / "Data").mkdir(parents=True)
    support.mkdir(parents=True)

    # the real names are .exe; on this host they are plain executables, so the
    # table is pointed at what actually exists rather than faking a .exe
    if sys.platform != "win32":
        monkeypatch.setitem(hd_runner.TOOLS, "pack", "ff13tool")
        monkeypatch.setitem(hd_runner.TOOLS, "models", "FFXIII_ModelsHD")
        monkeypatch.setitem(hd_runner.TOOLS, "inject", "FFXIII_HDProject")
    return root, support


def names():
    return (hd_runner.TOOLS["pack"], hd_runner.TOOLS["models"], hd_runner.TOOLS["inject"])


def test_a_count_of_zero_is_a_failure_not_a_success():
    """
    FFXIII_HDProject returns a COUNT. Reading it the ordinary way calls every
    successful injection an error and every no-op a success, both directions
    wrong, which is why this is pinned rather than commented.
    """
    counted = Step(tool="inject", args=[], label="x", success=OK_COUNT)
    assert not succeeded(counted, 0), "0 means it processed nothing"
    assert succeeded(counted, 1)
    assert succeeded(counted, 900)
    assert not succeeded(counted, 1001), "over 1000 is an error code"

    ordinary = Step(tool="models", args=[], label="x", success=OK_ZERO)
    assert succeeded(ordinary, 0)
    assert not succeeded(ordinary, 1)


def test_output_streams_a_line_at_a_time(mod):
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "import sys,time\n"
                        "for i in range(5):\n"
                        "    print('unpacking chunk', i, flush=True)\n"
                        "    time.sleep(0.02)\n")

    seen, stamps = Log(), []
    def line(text, stream="out"):
        seen(text, stream)
        stamps.append(time.monotonic())

    code = run_step(Step(tool="pack", args=[], label="unpack"), root, support, on_line=line)

    assert code == 0
    assert seen == [f"unpacking chunk {i}" for i in range(5)]
    # arriving as it goes rather than in one lump at the end, which is the
    # whole reason for an embedded terminal
    assert stamps[-1] - stamps[0] > 0.02


def test_stderr_is_interleaved_rather_than_lost(mod):
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "import sys\n"
                        "print('before', flush=True)\n"
                        "print('a warning', file=sys.stderr, flush=True)\n"
                        "print('after', flush=True)\n")

    seen = Log()
    run_step(Step(tool="pack", args=[], label="x"), root, support, on_line=seen)

    assert "a warning" in seen, "stderr went nowhere, which is where errors hide"


def test_stderr_arrives_labelled_as_stderr(mod):
    """
    It used to be folded into stdout with stderr=STDOUT. Readable, and useless
    the one time it mattered: a live run stopped with code 1 partway through an
    extraction and the terminal held three thousand filenames with no way to
    tell which of them, if any, was the tool complaining.
    """
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "import sys\n"
                        "print('extracting chr/pc/pc001', flush=True)\n"
                        "print('cannot create file', file=sys.stderr, flush=True)\n")

    seen = Log()
    run_step(Step(tool="pack", args=[], label="x"), root, support, on_line=seen)

    assert seen.on("err") == ["cannot create file"]
    assert seen.on("out") == ["extracting chr/pc/pc001"]


def test_the_command_is_echoed_before_it_runs(mod):
    """
    a log that says "unpacking white_scru stopped with code 1" and not what was
    actually invoked sends you off verifying the wrong command
    """
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "pass\n")

    seen = Log()
    run_step(Step(tool="pack", args=["-x", "-ff13", "filelist_scru.win32.bin"],
                  label="unpack"), root, support, on_line=seen)

    echoed = seen.on("cmd")
    assert len(echoed) == 1, echoed
    assert "-x -ff13 filelist_scru.win32.bin" in echoed[0]
    # and NOT the mod folder. The cwd is half of what a relative path means and
    # the log header carries it once; on every line it was a sixty character
    # path repeated 114 times in a real run.
    assert str(root) not in echoed[0]

    # a step that runs somewhere else does say so, which is the case where it
    # is the whole point: the zone imports resolve all three of their path
    # arguments against a directory two levels inside models\\mod.
    (root / "models" / "mod" / "zoneu" / "z010").mkdir(parents=True)
    seen = Log()
    run_step(Step(tool="pack", args=["-i", "a", "b", "c"], label="import",
                  cwd="models\\mod\\zoneu\\z010"), root, support, on_line=seen)
    assert "models\\mod\\zoneu\\z010" in seen.on("cmd")[0]


def test_a_failure_carries_the_reason_the_tool_gave(mod):
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "import sys\n"
                        "for i in range(3076): print('file', i, flush=True)\n"
                        "print('ERROR: could not open output file', file=sys.stderr, flush=True)\n"
                        "sys.exit(1)\n")

    result = run([Step(tool="pack", args=[], label="unpacking white_scru")], root, support)

    assert not result.ok and result.code == 1
    assert result.reason == "ERROR: could not open output file"
    assert result.errors == ["ERROR: could not open output file"]
    # the tail of stdout too, because what it was working on when it died is
    # the other half of the story
    assert result.lines[-1] == "file 3075"
    assert len(result.lines) <= 12, "the whole extraction is not a useful tail"


def test_a_successful_step_carries_no_tail(mod):
    """a fourteen hundred step plan holding every step's output is megabytes"""
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "for i in range(500): print('file', i, flush=True)\n")

    result = run([Step(tool="pack", args=[], label="unpack")], root, support)

    assert result.ok
    assert result.lines == [] and result.errors == []


def test_a_noisy_stderr_does_not_deadlock_the_tool(mod):
    """
    two pipes need two readers. One thread reading stdout to exhaustion first
    blocks the tool as soon as the stderr pipe fills, which on Windows is 4KB,
    so this hangs forever rather than failing.
    """
    root, support = mod
    pack, _, _ = names()
    # padded so the volume is unambiguous rather than lucky: 2000 lines of a
    # hundred bytes is 200KB down each pipe, past a Windows buffer at 4KB and
    # a Linux one at 64KB, so a single reader wedges on either
    stub(support, pack, "import sys\n"
                        "pad = 'x' * 90\n"
                        "for i in range(2000):\n"
                        "    print('out', i, pad, file=sys.stdout)\n"
                        "    print('err', i, pad, file=sys.stderr)\n")

    seen = Log()
    started = time.monotonic()
    code = run_step(Step(tool="pack", args=[], label="x"), root, support,
                    on_line=seen, cancel=threading.Event())

    assert code == 0, "the tool did not finish, which is what a full pipe looks like"
    assert time.monotonic() - started < 30
    assert len(seen.on("out")) == 2000
    assert len(seen.on("err")) == 2000


def test_arguments_arrive_exactly_as_built(mod):
    """
    the one thing the stubs can check that matters on Windows: the trailing
    backslashes the batch file passes are still there
    """
    root, support = mod
    _, _, inject = names()
    stub(support, inject, "import sys\n"
                          "print('|'.join(sys.argv[1:]), flush=True)\n"
                          "sys.exit(7)\n")

    seen = Log()
    step = Step(tool="inject", args=["w001.win32", "models\\orig\\", "models\\mod\\", "weapon\\"],
                label="w001", success=OK_COUNT)
    code = run_step(step, root, support, on_line=seen)

    assert seen == ["w001.win32|models\\orig\\|models\\mod\\|weapon\\"]
    assert succeeded(step, code)


def test_the_tools_run_from_the_mod_folder(mod):
    """every path the tools get is relative to it, so cwd is not incidental"""
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "import os\nprint(os.getcwd(), flush=True)\n")

    seen = Log()
    run_step(Step(tool="pack", args=[], label="x"), root, support, on_line=seen)

    assert Path(seen[0]).resolve() == root.resolve()


def test_a_long_run_can_be_cancelled(mod):
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "import time\n"
                        "print('started', flush=True)\n"
                        "time.sleep(30)\n")

    cancel = threading.Event()
    threading.Timer(0.6, cancel.set).start()

    started = time.monotonic()
    with pytest.raises(Cancelled):
        run_step(Step(tool="pack", args=[], label="unpack"), root, support, cancel=cancel)

    assert time.monotonic() - started < 10, "cancel waited for the process to finish"


def test_a_frozen_tool_is_ended_at_its_limit_and_the_run_goes_on(mod):
    """
    FF XIII HD Models has sat on one recipe for hours. Optional doesn't help a
    step that never returns, so the limit is what lets the run carry on
    """
    root, support = mod
    pack, models, _ = names()
    stub(support, models, "import pathlib, time\n"
                          "beat = pathlib.Path('beat.txt')\n"
                          "for n in range(30):\n"
                          "    beat.write_text(str(n))\n"
                          "    time.sleep(0.1)\n")
    stub(support, pack, "print('next one ran', flush=True)\n")

    # one freeze among working models is a gap, not the majority that fails a run
    steps = [Step(tool="pack", args=[], label="before", payload=True),
             Step(tool="models", args=[], label="n111 models", optional=True, payload=True,
                  limit=1),
             Step(tool="pack", args=[], label="after", payload=True)]
    seen = Log()
    result = run(steps, root, support, on_line=seen)

    assert result.ok
    assert result.skipped == [("n111 models", hd_runner.HUNG)]
    assert "n111 models was still running after 1 seconds, so it was ended and left out" in seen
    assert "next one ran" in seen
    # a .bat stands in for the tool on Windows, and ending cmd.exe leaves the
    # python under it running. The real tools are exes, so this holds there
    if sys.platform != "win32":
        last = (root / "beat.txt").read_text(encoding="utf-8")
        time.sleep(0.6)
        assert (root / "beat.txt").read_text(encoding="utf-8") == last, "the frozen tool was left running"


def test_a_failing_step_stops_the_run(mod):
    root, support = mod
    pack, models, _ = names()
    stub(support, pack, "import sys\nprint('boom', flush=True)\nsys.exit(3)\n")
    stub(support, models, "print('should not run', flush=True)\n")

    steps = [Step(tool="pack", args=[], label="unpack"),
             Step(tool="models", args=[], label="n910")]
    seen = Log()
    result = run(steps, root, support, on_line=seen)

    assert not result.ok
    assert result.failed.label == "unpack" and result.code == 3
    assert result.steps_run == 1
    assert "should not run" not in seen


def test_an_optional_step_is_stepped_over(mod):
    """
    refusing six categories because one id out of nine hundred had no data
    file would be worse than the gap it leaves, and the mod's own installer
    steps over it too
    """
    root, support = mod
    pack, models, _ = names()
    stub(support, pack, "print('packed', flush=True)\n")
    stub(support, models, "import sys\nprint('nope', flush=True)\nsys.exit(4)\n")

    steps = [Step(tool="models", args=[], label="n910", optional=True),
             Step(tool="pack", args=[], label="repack")]
    seen = Log()
    result = run(steps, root, support, on_line=seen)

    assert result.ok
    assert result.steps_run == 2
    assert any("skipped" in line for line in seen)
    assert "packed" in seen


def test_progress_is_reported_per_step(mod):
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "print('ok', flush=True)\n")

    steps = [Step(tool="pack", args=[], label=f"step {i}") for i in range(3)]
    marks = []
    run(steps, root, support, on_step=lambda s, n, total: marks.append((s.label, n, total)))

    assert marks == [("step 0", 0, 3), ("step 1", 1, 3), ("step 2", 2, 3)]


def test_a_tool_outside_supportfiles_is_refused(mod):
    """
    the executables come out of an archive the user dropped in. a name from
    the table, resolved, and still inside the folder afterwards.
    """
    root, support = mod
    with pytest.raises(RunnerError, match="missing"):
        tool_path(support, "pack")

    with pytest.raises(RunnerError, match="no such tool"):
        tool_path(support, "anything_else")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_tool_cannot_redirect_the_run(mod, tmp_path):
    root, support = mod
    pack, _, _ = names()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "real").write_text("#!/bin/sh\necho pwned\n")
    (elsewhere / "real").chmod(0o755)
    (support / pack).symlink_to(elsewhere / "real")

    with pytest.raises(RunnerError, match="does not sit directly"):
        tool_path(support, "pack")


def test_the_plan_matches_the_batch_file():
    """
    GENERATESINGLE picks the tool on whether Data/<id>.txt exists, and the two
    branches have different success rules. Getting that backwards is silent.
    """
    steps = plan(DATA, [("N", "n910"), ("W", "w001")], "u")

    assert [s.tool for s in steps] == ["models", "inject"]
    assert steps[0].args == ["Data\\n910.txt"] and steps[0].success == OK_ZERO
    assert steps[1].success == OK_COUNT
    assert steps[1].args == ["w001.win32",
                             "models\\orig\\chr\\weapon\\w001\\bin\\",
                             "models\\mod\\chr\\weapon\\w001\\bin\\",
                             "weapon\\"]


def test_the_plan_is_spelled_the_same_on_every_host():
    """
    built with Path, these would come out with forward slashes here and
    backslashes on Windows, so a plan comparison would pass locally and mean
    nothing about what ships
    """
    for step in plan(DATA, [("N", "n910"), ("W", "w001")], "u"):
        for arg in step.args:
            assert "/" not in str(arg), f"{arg} is spelled for the test host, not for Windows"


def test_an_unknown_type_letter_is_skipped_rather_than_guessed():
    """
    PROCESSLINE's table is the only thing mapping a letter to a folder, and a
    wrong folder writes a texture over the wrong asset
    """
    assert plan(DATA, [("X", "n910"), (None, "Z8")], "u") == []


def test_unpack_and_repack_bookend_the_right_archive():
    assert unpack_steps("u")[0].args == ["-x", "-ff13", "filelist_scru.win32.bin",
                                         "white_scru.win32.bin"]
    assert repack_steps("c")[0].args == ["-c", "filelist_scrc.win32.bin", "white_scrc"]
    # neither is optional. a failed unpack means there is nothing to work on
    # and a failed repack means the archive never gets rebuilt.
    assert not unpack_steps("u")[0].optional
    assert not repack_steps("u")[0].optional


def test_a_path_with_the_characters_that_break_their_batch_file(tmp_path, monkeypatch):
    """
    `!` and `^` in the mod path are a known failure of the mod's own installer:
    cmd's delayed expansion eats them, and v3.0.8 added a check that refuses to
    run rather than a fix. Popen takes an argument list, so there is no shell to
    confuse and nothing to escape.
    """
    root = tmp_path / "FFXIII HD^Project!2024"
    support = root / "SupportFiles"
    support.mkdir(parents=True)
    if sys.platform != "win32":
        monkeypatch.setitem(hd_runner.TOOLS, "pack", "ff13tool")
    stub(support, hd_runner.TOOLS["pack"], "import os\nprint(os.getcwd(), flush=True)\n")

    seen = Log()
    code = run_step(Step(tool="pack", args=["-c", "white_scru"], label="repack"),
                    root, support, on_line=seen)

    assert code == 0
    assert Path(seen[0]).resolve() == root.resolve()


def test_an_argument_with_a_space_is_not_split(tmp_path, monkeypatch):
    """the game lives under "FINAL FANTASY XIII", so this is the normal case"""
    root = tmp_path / "mod"
    support = root / "SupportFiles"
    support.mkdir(parents=True)
    if sys.platform != "win32":
        monkeypatch.setitem(hd_runner.TOOLS, "pack", "ff13tool")
    stub(support, hd_runner.TOOLS["pack"], "import sys\nprint(len(sys.argv) - 1, sys.argv[1], flush=True)\n")

    seen = Log()
    run_step(Step(tool="pack", args=["FINAL FANTASY XIII"], label="x"), root, support,
             on_line=seen)

    assert seen == ["1 FINAL FANTASY XIII"]


@pytest.mark.parametrize("nasty", ["../../../etc/passwd", "..", "a/b", "a\\b",
                                   "n9\x0010", "", "x" * 200, "con", " n910"])
def test_an_id_that_is_really_a_path_is_skipped(nasty):
    """
    an id becomes both a path segment and a filename in the arguments, so a
    corrupted or doctored LoadHD list would otherwise put "../.." straight into
    one. The lists live inside the folder the user dropped, which is the same
    folder the executables come from, so this is not the weakest link; it costs
    nothing and a merely corrupted file should fail cleanly too.
    """
    steps = plan(DATA, [("N", nasty)], "u")

    assert steps == [], f"{nasty!r} became a step"


def test_every_id_the_mod_actually_ships_is_accepted():
    """
    the guard is worthless if it rejects real ids. All 911 across the shipped
    lists are plain alphanumeric, the longest being four characters.
    """
    from patcher.mods.hd_runner import usable_id

    for member in ("n910", "c001", "w001", "s101", "f066", "z010block022u", "c592"):
        assert usable_id(member), f"{member} is a real id and was rejected"


def test_a_reserved_windows_device_name_is_not_a_filename():
    """
    con, nul, com1 and friends pass any sane character check and are still not
    files. Windows resolves them with an extension attached, so con.win32 is
    the console rather than a path, and a tool told to write it does something
    nobody wants to debug.
    """
    from patcher.mods.hd_runner import usable_id

    for member in ("con", "CON", "nul", "com1", "LPT9", "Aux"):
        assert not usable_id(member), f"{member} was accepted as an id"


def test_a_good_id_beside_a_bad_one_still_runs():
    """
    skipping rather than raising, so one corrupt line does not fail an install
    of nine hundred good ones
    """
    steps = plan(DATA, [("N", "../../etc"), ("N", "n910")], "u")

    assert len(steps) == 1
    assert steps[0].args == ["Data\\n910.txt"]


# transcribed off a user's run log. The whole extraction succeeded, nothing
# went to stderr, and it returned 1.
FF13TOOL_X = """
import pathlib, sys
names = ['chr/pc/c001/bin/c001.win32.trb', 'zone/z023/scr000.clb', 'sound/bgm.scd']
print('Game set to FF13.', flush=True)
if sys.argv[1] == '-x':
    print('File list processed. %d files listed in 19 file chunks.' % len(names), flush=True)
    for n in names:
        out = pathlib.Path('white_scru') / n
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b'x' * 32)
        print('Extracted white_scru/%s (uncompressed)' % n, flush=True)
    print('Extracted %d files in total.' % len(names), flush=True)
    print('%d files uncompressed. 1 files copied.' % (len(names) - 1), flush=True)
elif sys.argv[1] == '-c':
    for n in ('filelist_scru.win32.bin', 'white_scru.win32.bin'):
        pathlib.Path(n).write_bytes(b'REPACKED:' + n.encode())
    print('Added %d files in total.' % len(names), flush=True)
sys.exit(1)
"""


def test_a_complete_extraction_that_returns_one_is_a_success(mod):
    """
    The live failure, exactly. ff13tool extracted every file the index listed,
    wrote nothing to stderr and exited 1, and we called that a crash and
    stopped a 859 step install on its first step.
    """
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, FF13TOOL_X)

    result = run(unpack_steps("u"), root, support)

    assert result.ok, f"still reading the exit code: {result.reason or result.code}"
    assert (root / "white_scru").is_dir()


def test_an_extraction_that_produced_nothing_still_fails(mod):
    """the other half. Ignoring the code cannot mean ignoring the outcome."""
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, "import sys\nprint('Game set to FF13.', flush=True)\nsys.exit(1)\n")

    result = run(unpack_steps("u"), root, support)

    assert not result.ok
    assert "white_scru" in result.reason, result.errors


def test_an_extraction_that_stopped_early_fails_even_with_a_tree(mod):
    """
    it announces both numbers. Listing 3076 and extracting 40 is a partial
    extraction whatever it returns, and the tree existing says nothing.
    """
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, FF13TOOL_X.replace(
        "print('Extracted %d files in total.' % len(names), flush=True)",
        "print('Extracted 2 files in total.', flush=True)").replace(
        "19 file chunks.' % len(names)", "19 file chunks.' % 3076"))

    result = run(unpack_steps("u"), root, support)

    assert not result.ok, "a partial extraction was accepted because the folder exists"


# ff13tool v1.4 on an index that lists a path twice, as measured under wine.
# The count includes the repeat, the folder cannot, and a file it could not
# write ends the run on stdout with no total and whatever exit code it likes.
FF13TOOL_X_REPEATS = """
import pathlib, sys
names = ['chr/pc/c201/bin/c201.win32.trb', 'chr/pc/c201/bin/c201.win32.imgb',
         'chr/pc/c201/bin/c201.win32.trb', 'sys/tail.bin']
print('Game set to FF13.', flush=True)
print('File list processed. 4 files (1 duplicates) listed in 1 file chunks.', flush=True)
for n in names:
    out = pathlib.Path('white_scru') / n
    out.parent.mkdir(parents=True, exist_ok=True)
    if n == 'sys/tail.bin' and 'STOP' in globals():
        print('Error: Failed to open white_scru/sys/tail.bin for writing.', flush=True)
        sys.exit(0)
    out.write_bytes(b'x' * 32)
    print('Extracted white_scru/%s (uncompressed)' % n, flush=True)
print('Extracted 4 files in total.', flush=True)
sys.exit(1)
"""


def test_an_extraction_is_complete_at_one_file_per_path_listed(mod):
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, FF13TOOL_X_REPEATS)

    result = run(unpack_steps("u"), root, support)

    assert result.ok, result.errors
    assert sum(1 for p in (root / "white_scru").rglob("*") if p.is_file()) == 3


def test_an_extraction_that_said_error_fails_whatever_is_already_on_disk(mod):
    """
    a retry over a tree the last run finished finds every file there, so the
    one thing that separates this run from that one is the tool saying so
    """
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, FF13TOOL_X_REPEATS)
    assert run(unpack_steps("u"), root, support).ok
    stub(support, pack, "STOP = True\n" + FF13TOOL_X_REPEATS)

    result = run(unpack_steps("u"), root, support)

    assert not result.ok, "an extraction that reported an error was accepted"
    assert any("Failed to open" in text for text in result.errors), result.errors


def test_an_import_that_said_error_is_not_an_import():
    step = Step(tool="pack", args=[], label="z0010 into the game", success=OK_IMPORTED)
    said = ["Added filelist_z0010u.win32.bin (copied)", "Updated white_imgu.win32.bin",
            "Imported one file to white_imgu.win32.bin", "Wrote filelistu.win32.bin"]

    assert succeeded(step, 0, None, said)
    assert not succeeded(step, 0, None, said[:2] + ["Error: Failed to encrypt filelistu."]
                         + said[2:])


def test_a_repack_is_judged_on_having_rewritten_the_archive(mod):
    root, support = mod
    pack, _, _ = names()
    (root / "white_scru.win32.bin").write_bytes(b"VANILLA" * 64)
    stub(support, pack, "import pathlib, sys\n"
                        "pathlib.Path('white_scru.win32.bin').write_bytes(b'REPACKED' * 99)\n"
                        "sys.exit(1)\n")

    result = run(repack_steps("u"), root, support)

    assert result.ok
    assert (root / "white_scru.win32.bin").read_bytes().startswith(b"REPACKED")


def test_a_repack_that_left_the_copy_alone_fails(mod):
    """
    the archive is copied into the mod folder before any of this, so it is
    already sitting there and existing proves nothing. A clock comparison does
    not separate them either: the whole run fits inside one timestamp tick.
    """
    root, support = mod
    pack, _, _ = names()
    (root / "white_scru.win32.bin").write_bytes(b"VANILLA" * 64)
    stub(support, pack, "import sys\nprint('did nothing', flush=True)\nsys.exit(1)\n")

    result = run(repack_steps("u"), root, support)

    assert not result.ok
    assert "white_scru.win32.bin" in result.reason


def test_the_other_two_tools_keep_their_own_rules(mod):
    """
    ff13tool being a special case must not turn into every tool being one.
    FFXIII_HDProject still returns a count and FFXIII_ModelsHD still returns 0.
    """
    counted = Step(tool="inject", args=[], label="w001", success=OK_COUNT)
    assert succeeded(counted, 900) and not succeeded(counted, 0)

    ordinary = Step(tool="models", args=[], label="n910", success=OK_ZERO)
    assert succeeded(ordinary, 0) and not succeeded(ordinary, 1)


def test_a_produced_step_with_nothing_named_falls_back_to_the_ordinary_rule():
    """passing blind would be worse than the bug this replaced"""
    bare = Step(tool="pack", args=[], label="x", success=OK_PRODUCED)

    assert succeeded(bare, 0, None)
    assert not succeeded(bare, 1, None)


def test_the_plan_marks_both_pack_steps_and_nothing_else(mod):
    """
    a models or inject step given OK_PRODUCED with no produces would silently
    fall back to code == 0, which is right for models and wrong for inject
    """
    _, data, _ = mod[0], mod[0] / "Data", mod[1]
    steps = unpack_steps("u") + repack_steps("u")

    for step in steps:
        assert step.success == OK_PRODUCED
        assert step.produces, f"{step.label} is judged on an artefact it never names"
    assert steps[0].produces == "white_scru"
    assert steps[1].produces == "white_scru.win32.bin"


# the real failure, verbatim from a user's run. Every one of 854 texture steps
# said this, and every one of them was stepped over as a one-off.
#
# Exits 0 and complains on stderr, because a POSIX exit code is one byte and
# 4294967293 is a Windows DWORD holding -3. Zero on its own now means "no art
# for this id", which is the normal state of 102 of their 891, so the stub has
# to signal failure the other way round. The real Windows value is pinned in
# test_the_windows_exit_code_from_that_run_is_rejected.
HDPROJECT_NO_ORIG = """
import sys
print('The HD Project.  Version 3.0.0', flush=True)
print('Unable to open the .trb file.  Exiting.', file=sys.stderr, flush=True)
sys.exit(0)
"""


def test_the_windows_exit_code_from_that_run_is_rejected():
    """4294967293 is -3 as an unsigned DWORD, which is what Windows handed back"""
    counted = Step(tool="inject", args=[], label="w001 textures", success=OK_COUNT)

    assert not succeeded(counted, 4294967293)
    assert not succeeded(counted, 4294967295), "-1 the same way"
    assert not succeeded(counted, 0), "zero means it processed nothing"
    assert succeeded(counted, 253), "a real count still counts"


# a payload step that works has to change the tree one way or another, because
# that tree is what ff13tool -c turns back into the archive. Forced by the
# shape of the pipeline rather than assumed about the tools.
TOUCHES_TREE = ("import pathlib\n"
                "def touched():\n"
                "    f = pathlib.Path('white_scru/chr/pc/c001/bin/c001.win32.trb')\n"
                "    f.parent.mkdir(parents=True, exist_ok=True)\n"
                "    f.write_bytes((f.read_bytes() if f.exists() else b'') + b'HD')\n")


def payload_plan(count, root):
    """a plan shaped like a real one: unpack, N per id steps, repack"""
    ids = [f"w{n:03d}" for n in range(count)]
    middle = [Step(tool="inject", args=[f"{i}.win32", "models\\orig\\", "models\\mod\\", "weapon\\"],
                   label=f"{i} textures", success=OK_COUNT, optional=True, payload=True)
              for i in ids]
    return unpack_steps("u") + middle + repack_steps("u")


def test_a_run_where_every_payload_step_failed_is_not_a_success(mod):
    """
    857 of 859 steps skipped, "Done. 859 steps." on screen, and a recompressed
    copy of the vanilla archive written over somebody's game. optional was
    written so one bad id out of nine hundred could slide; it swallowed all
    nine hundred and called it done.
    """
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    stub(support, inject, HDPROJECT_NO_ORIG)

    result = run(payload_plan(6, root), root, support)

    assert not result.ok, "a run that installed nothing reported success"
    assert result.applied == 0
    assert len(result.skipped) == 6
    assert "None of the 6" in result.errors[0], result.errors
    # and the reason the tool gave, so the next person does not have to read
    # three thousand lines to find it
    assert any("Unable to open the .trb file" in line for line in result.errors), result.errors


def test_it_stops_before_the_repack_rather_than_after(mod):
    """
    the repack is the expensive step and the one that produces the file
    install() copies into the game. Reaching a verdict after it means forty
    wasted minutes and an archive sitting there looking installable.
    """
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    stub(support, inject, HDPROJECT_NO_ORIG)

    result = run(payload_plan(4, root), root, support)

    assert not result.ok
    assert result.steps_run == 5, "it ran the repack anyway"
    assert result.failed.label.endswith("textures"), result.failed.label


def test_a_handful_of_bad_ids_still_installs(mod):
    """
    the case optional was actually written for. Three of nine hundred failing
    is not a broken setup, and refusing the whole install over it would be
    worse than the gap it leaves.
    """
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    # a count for the ones that work, zero for the two that do not
    stub(support, inject, "import sys\n" + TOUCHES_TREE +
                          "bad = sys.argv[1].startswith(('w001', 'w003'))\n"
                          "if bad: print('could not read it', file=sys.stderr, flush=True)\n"
                          "else: touched()\n"
                          "sys.exit(0 if bad else 7)\n")

    result = run(payload_plan(10, root), root, support)

    assert result.ok, result.errors
    assert result.applied == 8
    assert len(result.skipped) == 2
    assert [label for label, _ in result.skipped] == ["w001 textures", "w003 textures"]


def test_a_majority_failing_is_a_broken_setup_not_bad_luck(mod):
    """
    nine hundred models do not individually go wrong. Past half it is the
    setup, and a half applied texture pack is a broken game rather than a
    partial improvement.
    """
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    stub(support, inject, "import sys\n" + TOUCHES_TREE +
                          "good = sys.argv[1].startswith('w000')\n"
                          "if good: touched()\n"
                          "else: print('could not read it', file=sys.stderr, flush=True)\n"
                          "sys.exit(7 if good else 0)\n")

    result = run(payload_plan(10, root), root, support)

    assert not result.ok
    assert result.applied == 1
    assert "Only 1 of 10" in result.errors[0], result.errors


def test_exactly_half_is_still_refused(mod):
    """the boundary, since half a texture pack is the case this is guarding"""
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    stub(support, inject, "import sys\n" + TOUCHES_TREE +
                          "n = int(sys.argv[1][1:4])\n"
                          "if n < 3: touched()\n"
                          "else: print('could not read it', file=sys.stderr, flush=True)\n"
                          "sys.exit(7 if n < 3 else 0)\n")

    result = run(payload_plan(6, root), root, support)

    assert not result.ok, "half applied was accepted"
    assert result.applied == 3


def test_unpacking_and_repacking_with_nothing_between_is_refused(mod):
    """
    no payload steps means nothing touched the tree, so the repack would
    produce a copy of what the game already has. build_plan refuses an empty
    plan before it gets here; this is the same answer one layer down, and it
    does not depend on counting anything.
    """
    root, support = mod
    pack, _, _ = names()
    stub(support, pack, FF13TOOL_X)
    (root / "white_scru.win32.bin").write_bytes(b"VANILLA" * 64)

    result = run(unpack_steps("u") + repack_steps("u"), root, support)

    assert not result.ok
    assert "Nothing in white_scru changed" in result.errors[0], result.errors
    assert result.steps_run == 1, "it repacked anyway"


def test_a_tree_the_payload_steps_never_touched_is_not_repacked(mod):
    """
    the guarantee that does not care how the tools work. Whatever the pipeline
    turns out to be, something has to change the tree before repacking it is
    worth doing, because that tree is what ff13tool -c turns into the archive
    we copy into somebody's game.

    This catches a pipeline driven wrongly, which the counters above cannot:
    854 tools can all report success and still leave the tree alone.
    """
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    # reports a healthy count and writes nothing anywhere
    stub(support, inject, "import sys\nprint('injected 7', flush=True)\nsys.exit(7)\n")

    result = run(payload_plan(4, root), root, support)

    assert not result.ok, "it repacked a tree nothing had touched"
    assert result.applied == 4, "the tools did report working, which is the point"
    assert "Nothing in white_scru changed" in result.errors[0], result.errors


def vanilla_tree(root, pairs):
    """what ff13tool -x leaves behind, for the ids a plan will ask about"""
    for group, member in pairs:
        for suffix in (".win32.trb", ".win32.imgb"):
            out = root / "white_scru" / "chr" / group / member / "bin" / (member + suffix)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"VANILLA" * 8)
    return root


def test_the_seed_caches_the_originals_the_tools_read(mod):
    """
    The step that was missing entirely. Setup.bat robocopies the extracted tree
    into models\\orig and every one of 854 tool invocations failed with
    "Unable to open the .trb file" because we never did.
    """
    root, support = mod
    vanilla_tree(root, [("weapon", "w001"), ("npc", "n910")])

    code = run_local(seed_steps("u")[0], root)

    assert code == 0
    for group, member in (("weapon", "w001"), ("npc", "n910")):
        for suffix in (".win32.trb", ".win32.imgb"):
            cached = root / "models" / "orig" / "chr" / group / member / "bin" / (member + suffix)
            assert cached.is_file(), f"{cached} was not cached"


def test_the_seed_builds_the_folders_the_injector_writes_into(mod):
    """
    robocopy models\\orig models\\mod /e /xf * is a directory skeleton with no
    files in it. FFXIII_HDProject writes into models\\mod\\... and does not
    create the folder itself.
    """
    root, support = mod
    vanilla_tree(root, [("weapon", "w001")])

    run_local(seed_steps("u")[0], root)

    assert (root / "models" / "mod" / "chr" / "weapon" / "w001" / "bin").is_dir()
    assert not any((root / "models" / "mod").rglob("*.trb")), \
        "the skeleton has files in it, so orig was copied rather than mirrored"


def test_an_existing_cache_is_left_exactly_as_it_is(mod):
    """
    /XN /XO /XC together mean "only copy a file that is not there at all".
    models\\orig is a cache of ORIGINALS, and refreshing it from a tree
    extracted out of an already modded archive is how their uninstall learned
    to hand the mod back.
    """
    root, support = mod
    vanilla_tree(root, [("npc", "n910")])
    kept = root / "models" / "orig" / "chr" / "npc" / "n910" / "bin" / "n910.win32.trb"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"the real original, cached before anything was modded")

    run_local(seed_steps("u")[0], root)

    assert kept.read_bytes().startswith(b"the real original")


def voice_tree(root, variant, files):
    """the extracted tree of one voice set, {(group, member): bytes}"""
    for (group, member), data in files.items():
        for suffix in (".win32.trb", ".win32.imgb"):
            out = root / f"white_scr{variant}" / "chr" / group / member / "bin" / (member + suffix)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data + suffix.encode())


def voice_backup(folder, variant, files):
    """that voice set's pair in the backup, holding these as its vanilla files"""
    from tests.helpers import pack_plain

    rows = [(f"chr/{group}/{member}/bin/{member}{suffix}", data + suffix.encode())
            for (group, member), data in files.items()
            for suffix in (".win32.trb", ".win32.imgb")]
    folder.mkdir(parents=True, exist_ok=True)
    pack_plain(folder / f"white_scr{variant}.win32.bin", folder / f"filelist_scr{variant}.win32.bin",
               rows)
    return folder


def cached(root, group, member, suffix=".win32.trb"):
    return root / "models" / "orig" / "chr" / group / member / "bin" / (member + suffix)


def test_a_cache_of_the_other_voice_sets_originals_is_put_right_from_the_backup(mod):
    """
    one cache for both voice sets, which holds while the sets' files are the
    same. The copy into the other set leaves the tools only the ones that
    aren't, so a build for Japanese has to read Japanese originals
    """
    root, _ = mod
    english, japanese = {("npc", "n910"): b"English n910"}, {("npc", "n910"): b"Japanese n910"}
    voice_tree(root, "u", english)
    run_local(seed_steps("u")[0], root)
    assert cached(root, "npc", "n910").read_bytes() == b"English n910.win32.trb"
    voice_tree(root, "c", japanese)
    backup = voice_backup(root.parent / "backup", "c", japanese)
    seen = Log()

    assert run_local(seed_steps("c", source=backup)[0], root, on_line=seen) == 0

    assert cached(root, "npc", "n910").read_bytes() == b"Japanese n910.win32.trb"
    assert cached(root, "npc", "n910", ".win32.imgb").read_bytes() == b"Japanese n910.win32.imgb"
    assert any("other voice set's" in line for line in seen), seen


def test_the_cache_is_put_right_from_the_backup_and_never_the_tree(mod):
    """an HD file rewritten at its own length reads as vanilla in the tree"""
    root, _ = mod
    voice_tree(root, "u", {("npc", "n910"): b"English n910"})
    run_local(seed_steps("u")[0], root)
    voice_tree(root, "c", {("npc", "n910"): b"HD at length 13"})
    backup = voice_backup(root.parent / "backup", "c", {("npc", "n910"): b"Japanese n910"})

    run_local(seed_steps("c", source=backup)[0], root)

    assert cached(root, "npc", "n910").read_bytes() == b"Japanese n910.win32.trb"


def test_a_cache_already_of_this_voice_set_isnt_read_again(mod):
    """the usual run, which costs what it always did"""
    root, _ = mod
    voice_tree(root, "u", {("npc", "n910"): b"English n910"})
    run_local(seed_steps("u")[0], root)
    backup = voice_backup(root.parent / "backup", "u", {("npc", "n910"): b"something else"})

    run_local(seed_steps("u", source=backup)[0], root)

    assert cached(root, "npc", "n910").read_bytes() == b"English n910.win32.trb"


def test_a_cache_from_before_it_said_which_set_is_checked_once(mod):
    """an older build filled it without saying, from whichever set the launcher was on"""
    root, _ = mod
    kept = cached(root, "npc", "n910")
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"English n910.win32.trb")
    voice_tree(root, "c", {("npc", "n910"): b"Japanese n910"})
    backup = voice_backup(root.parent / "backup", "c", {("npc", "n910"): b"Japanese n910"})

    run_local(seed_steps("c", source=backup)[0], root)

    assert kept.read_bytes() == b"Japanese n910.win32.trb"
    assert (kept.parents[2] / hd_runner.VOICES_MARK).read_text(encoding="utf-8") == "c"


def test_a_seed_with_no_tree_to_read_fails(mod):
    root, support = mod

    assert run_local(seed_steps("u")[0], root) == 1


def test_the_seed_never_caches_a_model_bigger_than_vanilla_as_an_original(mod, monkeypatch):
    """
    the game's copy is all there is with no backup from before, and HD art
    cached as an original is HD art built over HD art on every run after
    """
    from patcher.mods import hd_vanilla

    root, support = mod
    vanilla_tree(root, [("npc", "n910"), ("weapon", "w001")])
    grown = root / "white_scru" / "chr" / "npc" / "n910" / "bin" / "n910.win32.imgb"
    grown.write_bytes(b"VANILLA" * 8 + b"HD" * 100)
    monkeypatch.setattr(hd_vanilla, "table", lambda layout: {
        f"chr/{g}/{m}/bin/{m}.win32.{h}": 56
        for g, m in (("npc", "n910"), ("weapon", "w001")) for h in ("trb", "imgb")})

    seen = Log()
    assert run_local(seed_steps("u")[0], root, on_line=seen) == 0

    orig = root / "models" / "orig" / "chr"
    assert not (orig / "npc" / "n910" / "bin" / "n910.win32.imgb").exists()
    assert (orig / "weapon" / "w001" / "bin" / "w001.win32.imgb").is_file()
    assert any("weren't cached as originals" in line for line in seen), seen


def test_a_stop_during_the_seed_stops_it_and_caches_nothing_after(mod, monkeypatch):
    root, support = mod
    vanilla_tree(root, [("weapon", f"w{n:03d}") for n in range(40)])
    cancel = threading.Event()
    real = hd_runner._cache_copy
    copied = []

    def slow(source, target, cancel=None, on_share=None):
        copied.append(target)
        if len(copied) == 5:
            cancel.set()
        return real(source, target, cancel=cancel, on_share=on_share)

    monkeypatch.setattr(hd_runner, "_cache_copy", slow)
    with pytest.raises(Cancelled):
        run_local(seed_steps("u")[0], root, cancel=cancel)

    assert len(copied) < 5 + hd_runner.SEED_WORKERS, "it went on caching after the Stop"


def test_a_backup_copy_that_wont_read_fails_the_seed_and_says_which(mod):
    """the game's copy outgrew the backup's, which is then the only original there is"""
    root, support = mod
    vanilla_tree(root, [("npc", "n910")])
    grown = root / "white_scru" / "chr" / "npc" / "n910" / "bin" / "n910.win32.imgb"
    grown.write_bytes(grown.read_bytes() + b"HD" * 50)
    backup = root.parent / "backup"
    backup.mkdir()
    from tests.helpers import pack_plain
    pack_plain(backup / "white_scru.win32.bin", backup / "filelist_scru.win32.bin",
               [(f"chr/npc/n910/bin/n910.win32.{h}", b"VANILLA" * 8) for h in ("trb", "imgb")])
    shutil.copyfile(backup / "filelist_scru.win32.bin", root / "filelist_scru.win32.bin")
    # the live index says it grew, and the backup's archive is cut short
    from tests.helpers import write_index
    write_index(root / "filelist_scru.win32.bin", [
        ("chr/npc/n910/bin/n910.win32.trb", 56), ("chr/npc/n910/bin/n910.win32.imgb", 156)])
    (backup / "white_scru.win32.bin").write_bytes(b"")

    seen = Log()
    code = run_local(seed_steps("u", source=backup)[0], root, on_line=seen)

    assert code == 1
    assert any("could not be read out of the backup" in line for line in seen.on("err")), seen


def test_a_cached_copy_bigger_than_vanilla_is_cached_again_clean(mod, monkeypatch):
    """their installer refills models\\orig from the live game, HD and all"""
    from patcher.mods import hd_vanilla

    root, support = mod
    vanilla_tree(root, [("npc", "n910")])
    cached = root / "models" / "orig" / "chr" / "npc" / "n910" / "bin" / "n910.win32.trb"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"HD" * 500)
    monkeypatch.setattr(hd_vanilla, "table", lambda layout: {
        f"chr/npc/n910/bin/n910.win32.{h}": 56 for h in ("trb", "imgb")})

    seen = Log()
    run_local(seed_steps("u")[0], root, on_line=seen)

    assert cached.read_bytes() == b"VANILLA" * 8
    assert any("cached again clean" in line for line in seen), seen


def test_a_step_copies_its_result_into_the_tree(mod):
    """
    GENERATESINGLE's loadcopy=1 branch. The tools write into models\\mod and
    nothing there is in the tree ff13tool -c repacks, so without this the
    archive is rebuilt out of exactly what went in.
    """
    root, support = mod
    _, _, inject = names()
    stub(support, inject, "import pathlib, sys\n"
                          "d = pathlib.Path('models/mod/chr/weapon/w001/bin')\n"
                          "d.mkdir(parents=True, exist_ok=True)\n"
                          "(d / 'w001.win32.trb').write_bytes(b'HD TRB')\n"
                          "(d / 'w001.win32.imgb').write_bytes(b'HD IMGB')\n"
                          "sys.exit(5)\n")
    steps = plan(root / "Data", [("W", "w001")], "u")

    result = run(steps, root, support)

    assert result.ok, result.errors
    landed = root / "white_scru" / "chr" / "weapon" / "w001" / "bin"
    assert (landed / "w001.win32.trb").read_bytes() == b"HD TRB"
    assert (landed / "w001.win32.imgb").read_bytes() == b"HD IMGB"


def test_a_tool_that_reports_success_and_wrote_nothing_is_not_a_success(mod):
    """
    the count says it worked and there is nothing to install. Taking its word
    for it is how 854 of them passed.
    """
    root, support = mod
    _, _, inject = names()
    stub(support, inject, "import sys\nprint('injected 9', flush=True)\nsys.exit(9)\n")
    steps = plan(root / "Data", [("W", "w001")], "u")

    result = run(steps, root, support)

    assert not result.ok
    assert "left no w001.win32.trb" in " ".join(result.errors), result.errors


def test_both_tools_copy_their_result_back(mod):
    """
    the copy sits outside GENERATESINGLE's if/else, so it applies to ModelsHD
    and to HDProject alike
    """
    (mod[0] / "Data").mkdir(parents=True, exist_ok=True)
    (mod[0] / "Data" / "n910.txt").write_text("recipe", encoding="utf-8")

    steps = plan(mod[0] / "Data", [("N", "n910"), ("W", "w001")], "u")

    assert [s.tool for s in steps] == ["models", "inject"]
    for step in steps:
        assert len(step.copies) == 2, step.label
        for source, destination in step.copies:
            assert source.startswith("models\\mod\\chr\\")
            assert destination.startswith("white_scru\\chr\\")


def test_the_seed_only_caches_the_groups_the_plan_needs(mod):
    """
    all six is 1.2GB out of a real archive. A weapons only install needs 74MB
    of it, and their own script caches per group for the same reason.
    """
    root, support = mod
    vanilla_tree(root, [("weapon", "w001"), ("mon", "m001"), ("fa", "f066")])

    run_local(seed_steps("u", ["weapon"])[0], root)

    assert (root / "models" / "orig" / "chr" / "weapon" / "w001").is_dir()
    assert not (root / "models" / "orig" / "chr" / "mon").exists()
    assert not (root / "models" / "orig" / "chr" / "fa").exists()


def test_no_groups_means_all_of_them(mod):
    """a plan covering every category amounts to the same thing"""
    root, support = mod
    vanilla_tree(root, [("weapon", "w001"), ("mon", "m001")])

    run_local(seed_steps("u")[0], root)

    assert (root / "models" / "orig" / "chr" / "weapon").is_dir()
    assert (root / "models" / "orig" / "chr" / "mon").is_dir()


def test_the_seed_says_something_while_it_works(mod):
    """
    two thousand files and a gigabyte under one label. Silence reads as a hang,
    and this is the longest step in the run that is not a tool.
    """
    root, support = mod
    vanilla_tree(root, [("weapon", f"w{n:03d}") for n in range(140)])

    seen = Log()
    run_local(seed_steps("u", ["weapon"])[0], root, on_line=seen)

    progress = [line for line in seen if "cached" in line and "currently in" in line]
    assert len(progress) > 1, "it copies 280 files and says nothing until it is done"
    assert "weapon" in progress[0]


def test_an_id_the_mod_ships_no_art_for_is_not_a_skip(mod):
    """
    102 of the 891 ids in their own lists have no texture in textures\\, and
    FFXIII_HDProject says so by returning a count of zero with nothing on
    stderr. Reporting those as skips made a complete install, 789 of 789, look
    like it had gone wrong 102 times.
    """
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    # w000 and w002 have art, the other two do not
    stub(support, inject, "import sys\n" + TOUCHES_TREE +
                          "n = int(sys.argv[1][1:4])\n"
                          "if n % 2 == 0: touched()\n"
                          "sys.exit(5 if n % 2 == 0 else 0)\n")
    steps = [Step(tool="inject", args=[f"w{n:03d}.win32", "models\\orig\\", "models\\mod\\",
                                       "weapon\\"],
                  label=f"w{n:03d} textures", success=OK_COUNT, optional=True, payload=True)
             for n in range(4)]

    result = run(unpack_steps("u") + steps + repack_steps("u"), root, support)

    assert result.ok, result.errors
    assert result.applied == 2
    assert len(result.nothing) == 2, result.nothing
    assert result.skipped == [], "a no-op was counted as a failure"


def test_a_no_op_does_not_count_against_whether_enough_of_the_run_worked(mod):
    """
    102 of 891 is 11%, and under the old counting a run where every single id
    that HAD art installed would still be judged on 891
    """
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    # one works, five have no art at all
    stub(support, inject, "import sys\n" + TOUCHES_TREE +
                          "good = sys.argv[1].startswith('w000')\n"
                          "if good: touched()\n"
                          "sys.exit(5 if good else 0)\n")
    steps = [Step(tool="inject", args=[f"w{n:03d}.win32", "a\\", "b\\", "weapon\\"],
                  label=f"w{n:03d} textures", success=OK_COUNT, optional=True, payload=True)
             for n in range(6)]

    result = run(unpack_steps("u") + steps + repack_steps("u"), root, support)

    assert result.ok, "one of one installable id worked and it was called a broken setup"
    assert result.payload_steps == 1, "the no-ops are counted as steps that could have worked"


def test_a_tool_that_returns_zero_and_complains_is_still_a_failure(mod):
    """
    zero means "processed nothing", which is a no-op only when it had nothing
    to say. Something on stderr means it tried and could not.
    """
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    stub(support, inject, "import sys\n"
                          "print('cannot read the container', file=sys.stderr, flush=True)\n"
                          "sys.exit(0)\n")

    result = run(payload_plan(2, root), root, support)

    assert not result.ok
    assert result.nothing == [], "a complaint was read as having nothing to do"
    assert len(result.skipped) == 2


def test_every_id_reporting_nothing_is_not_a_quiet_success(mod):
    """
    a plan of 891 ids where the mod ships art for none of them is not a thing.
    It is what a tool that has lost its inputs looks like if it happens to
    return zero rather than a negative, and the tree guard would catch it, but
    a run that installs nothing should say so on its own.
    """
    root, support = mod
    pack, _, inject = names()
    stub(support, pack, FF13TOOL_X)
    stub(support, inject, "import sys\nprint('The HD Project.', flush=True)\nsys.exit(0)\n")

    result = run(payload_plan(5, root), root, support)

    assert not result.ok
    assert len(result.nothing) == 5
    assert "All 5 ids came back with nothing" in result.errors[0], result.errors


# XIII-2's tools, standing in for the real ones the way they were traced: each
# writes its own id's models\mod folder and nothing else. Every start and end
# gets a file of its own under timeline/. One shared file appended to used to
# be enough, but Windows fakes O_APPEND as seek then write, so six tools at
# once wrote over each other's lines and an end went missing
SIDE_TOOL = r"""
import pathlib, sys, time
def here(text):
    return pathlib.Path(str(text).replace(chr(92), '/').strip('/'))
member = sys.argv[2].split('.')[0] if sys.argv[1] == '/s' else here(sys.argv[2]).stem
out = here(sys.argv[4]) if sys.argv[1] == '/s' else None
kind = 'textures' if out is not None else 'models'
marks = pathlib.Path('timeline')
marks.mkdir(exist_ok=True)
(marks / f'{member}.{kind}.start').write_text(str(time.monotonic()))
print(f'{kind} for {member}: one', flush=True)
time.sleep(float(pathlib.Path('pause.txt').read_text(encoding="utf-8")) if pathlib.Path('pause.txt').exists() else 0.3)
print(f'{kind} for {member}: two', flush=True)
if out is None:
    group = 'pc' if member.startswith('c') else 'npc'
    out = pathlib.Path('models/mod/chr') / group / member / 'bin'
outcome = pathlib.Path('outcome') / member
code = int(outcome.read_text(encoding="utf-8")) if outcome.exists() else (1 if kind == 'textures' else 0)
if code > 0 or kind == 'models':
    out.mkdir(parents=True, exist_ok=True)
    for half in ('trb', 'imgb'):
        (out / f'{member}.win32.{half}').write_bytes(f'HD {kind} {member}'.encode())
print(f'{kind} for {member}: three', flush=True)
(marks / f'{member}.{kind}.end').write_text(str(time.monotonic()))
sys.exit(code)
"""


def side_step(member, group="weapon", tool="inject2"):
    rel = "\\".join(["chr", group, member, "bin", ""])
    stem = f"{member}.win32"
    copies = [("models\\mod\\" + rel + stem + s, f"white_imgu\\{rel}{stem}{s}")
              for s in (".trb", ".imgb")]
    if tool.startswith("inject"):
        return Step(tool=tool, args=["/s", stem, "models\\orig\\" + rel, "models\\mod\\" + rel,
                                     group + "\\"],
                    label=f"{member} textures", success=OK_COUNT, optional=True, payload=True,
                    copies=copies)
    return Step(tool=tool, args=["-s", f"data\\{member}.txt"], label=f"{member} models",
                success=OK_ZERO, optional=True, payload=True, copies=copies)


@pytest.fixture
def side(mod, monkeypatch):
    root, support = mod
    for key in ("inject2", "models2", "inject", "models"):
        stub(support, hd_runner.TOOLS[key], SIDE_TOOL)
    monkeypatch.setattr(hd_runner, "_workers", lambda: 4)
    return root, support


def timeline(root):
    spans = {}
    for mark in (root / "timeline").glob("*.*.*"):
        member, kind, what = mark.name.split(".")
        spans.setdefault((member, kind), {})[what] = float(mark.read_text())
    return spans


def overlapping(spans) -> bool:
    ordered = sorted((s["start"], s["end"]) for s in spans.values())
    return any(b[0] < a[1] for a, b in zip(ordered, ordered[1:]))


def test_xiii2s_per_id_steps_run_side_by_side(side):
    root, support = side
    (root / "pause.txt").write_text("1.0")
    steps = [side_step(f"w{n:03d}") for n in range(6)]

    began = time.monotonic()
    result = run(steps, root, support)
    took = time.monotonic() - began

    assert result.ok and result.applied == 6, result
    assert overlapping(timeline(root)), "they still ran one after another"
    assert took < 5, f"six one second steps took {took:.1f}s on four workers"
    for n in range(6):
        landed = root / "white_imgu" / "chr" / "weapon" / f"w{n:03d}" / "bin"
        assert (landed / f"w{n:03d}.win32.imgb").read_bytes() == f"HD textures w{n:03d}".encode()


def test_a_models_mesh_edits_start_after_its_textures_are_in(side):
    """their .bat does textures for every group, then the model lists, and the
    model tool redoes a model's textures as it writes, so it has to go last"""
    root, support = side
    steps = [side_step("c108", "pc"), side_step("n012", "npc"),
             side_step("c108", "pc", tool="models2"), side_step("n012", "npc", tool="models2")]

    result = run(steps, root, support)

    assert result.ok and result.applied == 4, result
    spans = timeline(root)
    for member in ("c108", "n012"):
        assert spans[(member, "models")]["start"] >= spans[(member, "textures")]["end"]
    landed = root / "white_imgu" / "chr" / "pc" / "c108" / "bin" / "c108.win32.trb"
    assert landed.read_bytes() == b"HD models c108"


def test_xiiis_tools_still_go_one_at_a_time(side):
    """not traced, so not trusted beside each other"""
    root, support = side
    steps = [side_step(f"w{n:03d}", tool="inject") for n in range(4)]

    result = run(steps, root, support)

    assert result.ok and result.applied == 4
    assert not overlapping(timeline(root))


def test_a_batch_settles_the_same_as_one_at_a_time(side, monkeypatch):
    root, support = side
    (root / "pause.txt").write_text("0.05")
    (root / "outcome").mkdir()
    # nothing to install, a tool that refused, and the rest fine
    (root / "outcome" / "w001").write_text("0")
    (root / "outcome" / "w003").write_text("-4")
    steps = [side_step(f"w{n:03d}") for n in range(6)]

    def tallies(workers):
        monkeypatch.setattr(hd_runner, "_workers", lambda: workers)
        got = run(steps, root, support)
        return (got.ok, got.applied, sorted(got.nothing), sorted(got.skipped),
                got.steps_run, bool(got.errors))

    assert tallies(4) == tallies(1)


def test_a_batchs_lines_arrive_whole_and_in_step_order(side):
    root, support = side
    seen, stepped = Log(), []
    steps = [side_step(f"w{n:03d}") for n in range(5)]

    run(steps, root, support, on_line=seen, on_step=lambda s, i, t: stepped.append((s.label, i)))

    # each step's own lines one after another, never two tools interleaved
    said = [line for line in seen if " for w" in line]
    for n in range(0, len(said), 3):
        assert len({line.split(":")[0] for line in said[n:n + 3]}) == 1, said
    assert [i for _, i in stepped] == list(range(5)), "the bar went backwards"


def test_the_verdict_still_stops_a_batch_where_most_failed(side):
    root, support = side
    (root / "pause.txt").write_text("0.05")
    (root / "outcome").mkdir()
    for n in range(5):
        (root / "outcome" / f"w{n:03d}").write_text("-1")
    steps = [side_step(f"w{n:03d}") for n in range(6)]

    result = run(steps, root, support)

    assert not result.ok
    assert result.errors[0].startswith("Only 1 of 6"), result.errors


def test_a_stop_mid_batch_ends_the_tools_and_starts_no_more(side):
    root, support = side
    (root / "pause.txt").write_text("30")
    cancel = threading.Event()
    threading.Timer(1.5, cancel.set).start()
    steps = [side_step(f"w{n:03d}") for n in range(12)]

    began = time.monotonic()
    with pytest.raises(Cancelled):
        run(steps, root, support, cancel=cancel)

    assert time.monotonic() - began < 15, "a Stop waited on the tools to finish"
    started = [k for k, v in timeline(root).items() if "start" in v]
    assert len(started) <= 4, f"{len(started)} started after the Stop"
