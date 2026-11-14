"""
The transcript that survives the pane's line cap and the process dying.
"""

import re
import time
from pathlib import Path

from patcher.runlog import KEEP, RunLog


def test_both_streams_are_distinguishable_in_the_file(tmp_path):
    """
    the whole reason for splitting them. A log where a complaint looks like a
    filename is the log that sent somebody chasing disk space.
    """
    with RunLog(tmp_path / "run.log") as log:
        log.write("> ff13tool.exe -x", "cmd")
        log.write("extracting chr/pc/pc001", "out")
        log.write("ERROR: could not create file", "err")

    text = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "$ > ff13tool.exe -x" in text
    assert "  extracting chr/pc/pc001" in text
    assert "! ERROR: could not create file" in text


def test_the_header_records_what_the_run_was_against(tmp_path):
    with RunLog(tmp_path / "run.log", header={"mod": "D:/Mods/HD", "game": "D:/FFXIII"}) as log:
        log.write("x")

    text = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "mod: D:/Mods/HD" in text
    assert "game: D:/FFXIII" in text


def test_lines_are_on_disk_before_the_run_ends(tmp_path):
    """
    the interesting case is the process dying, and a buffered log loses exactly
    the part that explains why
    """
    log = RunLog(tmp_path / "run.log")
    log.write("first")
    log.write("boom", "err")

    assert "boom" in (tmp_path / "run.log").read_text(encoding="utf-8"), \
        "nothing reached disk until close, which is too late to be useful"
    log.close()


def test_a_log_that_cannot_be_written_is_not_an_error(tmp_path):
    """
    a read only data folder is a reason to lose the transcript, not a reason to
    refuse to install the mod
    """
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")

    log = RunLog(blocked / "nested" / "run.log")
    log.write("this goes nowhere")
    log.close()

    assert not log.usable


def test_old_runs_are_pruned(tmp_path):
    """an archive extraction transcript is not small and nobody reads the
    thirtieth one"""
    folder = tmp_path / "runs"
    folder.mkdir()
    for i in range(KEEP + 5):
        stale = folder / f"old{i:02d}.log"
        stale.write_text("x" * 100)
        # distinct mtimes, because the prune is oldest first and same-second
        # files sort arbitrarily
        import os
        os.utime(stale, (time.time() - (100 - i), time.time() - (100 - i)))

    RunLog(folder / "new.log").close()

    kept = sorted(p.name for p in folder.glob("*.log"))
    assert "new.log" in kept
    assert len(kept) <= KEEP + 1
    assert "old00.log" not in kept, "the oldest should go first"
    assert f"old{KEEP + 4:02d}.log" in kept, "it pruned the newest instead"


def test_a_long_step_keeps_its_ends_and_counts_the_middle(tmp_path):
    """
    the live one. A zones run wrote 28,538 lines and 2.7MB, of which about
    28,000 were `Extracted <path> (uncompressed)`. A file nobody opens is not
    a diagnostic.
    """
    log = RunLog(tmp_path / "run.log")
    log.step("unpacking white_scru", 0, 66)
    for n in range(3076):
        log.write(f"Extracted white_scru/chr/pc/c{n:03d}/bin/c{n:03d}.win32.trb")
    log.write("Extracted 3076 files in total.")
    log.write("2928 files uncompressed. 148 files copied.")
    log.close("OK.")

    text = (tmp_path / "run.log").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) < 40, f"{len(lines)} lines is still a wall"

    # ff13tool puts its totals last and they are the ones that say it finished
    assert "  Extracted 3076 files in total." in lines
    assert "  2928 files uncompressed. 148 files copied." in lines
    # the opening is kept too, so the shape of what it was doing survives
    assert any("c000.win32.trb" in line for line in lines)
    assert any(re.match(r"^  \.\.\. \d+ more lines \.\.\.$", line) for line in lines), \
        "it dropped lines without saying how many"


def test_a_complaint_takes_the_lines_before_it_with_it(tmp_path):
    """
    what a tool said immediately before it gave up is the context the
    complaint has to be read against, so stderr flushes the held lines rather
    than letting the budget eat them
    """
    log = RunLog(tmp_path / "run.log")
    log.step("unpacking z002", 0, 3)
    for n in range(400):
        log.write(f"Extracted thing{n}")
    log.write("ERROR: could not create output file, path too long", "err")
    log.close()

    lines = (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
    where = next(i for i, line in enumerate(lines) if line.startswith("! "))
    before = lines[where - 1]
    assert "thing399" in before, f"the line before the complaint was dropped: {before!r}"


def test_stderr_is_never_dropped_however_much_of_it_there_is(tmp_path):
    log = RunLog(tmp_path / "run.log")
    log.step("noisy", 0, 1)
    for n in range(200):
        log.write(f"complaint {n}", "err")
    log.close()

    lines = (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in lines if line.startswith("! ")) == 200


def test_a_step_heading_is_not_dressed_up_as_a_command(tmp_path):
    """
    `$` meant both "here is a step" and "here is the command it ran", so
    neither could be scanned for
    """
    log = RunLog(tmp_path / "run.log")
    log.step("s101 textures", 2, 66)
    log.write("> FFXIII_HDProject.exe s101.win32", "cmd")
    log.write("The HD Project.  Version 3.0.0")
    log.close()

    lines = (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
    assert "[3/66] s101 textures" in lines
    assert "$ > FFXIII_HDProject.exe s101.win32" in lines
    assert not any(line.startswith("$ [") or line.startswith("$ ---") for line in lines)

    # and a blank line before each heading, so a run is not one block of text
    assert lines[lines.index("[3/66] s101 textures") - 1] == ""


def test_it_ends_with_where_the_time_went(tmp_path, monkeypatch):
    """
    a real run is fifteen hundred steps over half an hour, and "it took
    forever" needs the five that took it, without anyone adding up headings
    """
    from patcher import runlog

    clock = iter([0.0, 0.0, 2.0, 2.5, 90.5, 95.0])
    monkeypatch.setattr(runlog.time, "monotonic", lambda: next(clock))
    log = RunLog(tmp_path / "run.log")
    log.step("unpacking white_imgu", 0, 4)
    log.step("s101 textures", 1, 4)
    log.step("repacking white_imgu", 2, 4)
    log.step("copying back", 3, 4)
    log.close("OK. 4 steps.")

    lines = (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
    at = lines.index("slowest of 4 steps, 1m 35s in all:")
    assert lines[at + 1:at + 5] == ["      88.0s  [3/4] repacking white_imgu",
                                    "       4.5s  [4/4] copying back, and everything after it",
                                    "       2.0s  [1/4] unpacking white_imgu",
                                    "       0.5s  [2/4] s101 textures"]
    assert lines[-1] == "OK. 4 steps.", "the verdict has to stay last"


def test_a_run_with_no_steps_has_no_summary(tmp_path):
    with RunLog(tmp_path / "run.log") as log:
        log.write("refused before anything ran", "err")
    assert "slowest" not in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_two_runs_inside_a_second_keep_two_transcripts(tmp_path):
    """
    named to the second, and a voice switch's copy hands over to the tools
    inside one. The second run used to open the first one's file and write
    over it, mod: line and all
    """
    first = RunLog(tmp_path / "hdproject2-20261004-021500.log", header={"mod": "D:/Mods/HD"})
    first.write("the copy")
    first.close("OK")
    second = RunLog(tmp_path / "hdproject2-20261004-021500.log", header={"mod": "D:/Mods/HD"})
    second.write("the tools")
    second.close("OK")

    assert first.path != second.path and second.path.name == "hdproject2-20261004-021500-2.log"
    assert "the copy" in first.path.read_text(encoding="utf-8")
    assert "the tools" in second.path.read_text(encoding="utf-8")
