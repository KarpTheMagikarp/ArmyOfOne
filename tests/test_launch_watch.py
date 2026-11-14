"""
The verdict logic, which is the part that can be wrong quietly.

Everything attached to a verdict is destructive: DRM_ERROR undoes the 4GB
patch and records the install as one that cannot take it. So the interesting
tests are the ones about NOT saying DRM_ERROR.
"""

import pytest

from patcher.launch_watch import (CRASHED, DIED_FAST_SECONDS, DRM_ERROR, OK, UNKNOWN,
                                  read_signals)


def test_steams_error_window_is_the_unambiguous_signal():
    assert read_signals(error_window=True, ever_seen=False, still_alive=False,
                        ran_seconds=0.0) == DRM_ERROR
    # and it wins even over a process that looks healthy, because that process
    # may be the launcher rather than the game
    assert read_signals(error_window=True, ever_seen=True, still_alive=True,
                        ran_seconds=60.0) == DRM_ERROR


def test_a_game_that_is_still_running_is_a_working_launch():
    assert read_signals(False, ever_seen=True, still_alive=True, ran_seconds=20.0) == OK


def test_a_process_that_dies_immediately_is_the_drm_refusing():
    """the CEG wrapper checks itself and gives up before the game draws"""
    assert read_signals(False, ever_seen=True, still_alive=False,
                        ran_seconds=1.5) == DRM_ERROR


def test_a_process_that_ran_and_then_exited_is_somebody_quitting():
    assert read_signals(False, ever_seen=True, still_alive=False,
                        ran_seconds=DIED_FAST_SECONDS + 1) == OK


def test_never_seeing_the_game_at_all_is_unknown():
    """
    Steam never started it, the user cancelled at the prompt, the machine was
    slower than the watch. None of those are evidence, and treating them as a
    failure tears a working patch off an install that was fine.
    """
    assert read_signals(False, ever_seen=False, still_alive=False, ran_seconds=0.0) == UNKNOWN
    assert read_signals(False, ever_seen=False, still_alive=False,
                        ran_seconds=99.0) == UNKNOWN


@pytest.mark.parametrize("seconds", [0.0, DIED_FAST_SECONDS - 0.01, DIED_FAST_SECONDS])
def test_the_boundary_between_refused_and_quit(seconds):
    verdict = read_signals(False, True, False, seconds)
    assert verdict == (OK if seconds >= DIED_FAST_SECONDS else DRM_ERROR)


def test_the_watch_is_inert_off_windows():
    """
    CreateToolhelp32Snapshot does not exist here, and a watcher that throws
    would take the launch with it. There is no Steam error window to find on a
    posix box, so there is no verdict to give.
    """
    from patcher import launch_watch

    assert launch_watch.watch("ffxiiiimg.exe", seconds=0.1).verdict == UNKNOWN


def test_the_windows_loader_box_counts_as_a_failure_too():
    """
    The other way this can fail, and the one with the worse ending. A DLL the
    loader will not take stops the game dead before any of it runs, and nothing
    about that dialog is Steam's, so watching only for Steam's box left it
    invisible. Our shim is a static import, which makes this our failure mode
    rather than a hypothetical.
    """
    from patcher.launch_watch import is_error_title

    exe = "ffxiiiimg.exe"
    assert is_error_title("Steam Error", exe)
    assert is_error_title("ffxiiiimg.exe - System Error", exe)
    assert is_error_title("ffxiiiimg.exe - Application Error", exe)
    assert is_error_title("ffxiiiimg.exe - Bad Image", exe)
    assert is_error_title("ffxiiiimg.exe - Entry Point Not Found", exe)
    # matched by shape against the executable name, which is not translated,
    # rather than against English captions which are
    assert is_error_title("FFXIIIIMG.EXE - System Error", exe)


def test_ordinary_windows_are_not_mistaken_for_failures():
    """
    the whole watch hangs off this. a false positive here tears the patch off
    an install that was working.
    """
    from patcher.launch_watch import is_error_title

    exe = "ffxiiiimg.exe"
    for title in ("FINAL FANTASY XIII", "Steam", "ffxiiiimg.exe",
                  "Notepad - Untitled", "My Steam Error Notes.txt - Notepad",
                  "some other game.exe - System Error", ""):
        assert not is_error_title(title, exe), title
    # and with no executable to compare against, only Steam's exact box counts
    assert not is_error_title("ffxiiiimg.exe - System Error", "")
    assert is_error_title("Steam Error", "")


def test_the_poll_is_fast_enough_to_see_a_process_that_dies_on_load():
    """
    A statically imported DLL the loader refuses gives the process the length
    of one failed resolution. At half a second a poll steps straight over that
    and reports UNKNOWN, which does nothing, which leaves a game that will not
    start and a tool with no opinion.
    """
    from patcher import launch_watch

    assert launch_watch.POLL_SECONDS <= 0.25


def test_a_fast_death_logged_as_a_crash_in_a_dll_is_the_dlls():
    """
    no DRM check on the exe crashes inside a dll beside it, and src_38 filing
    one of these as a refusal kept LR's 4GB patch off for good
    """
    exe = "LRFF13.exe"
    assert read_signals(False, True, False, 1.0, "version.dll", exe) == CRASHED
    # one the poll stepped over entirely is still the dll's, by the log
    assert read_signals(False, False, False, 0.0, "d3d9.dll", exe) == CRASHED
    # past the window it's a crash in play, not a launch that didn't happen
    assert read_signals(False, True, False, DIED_FAST_SECONDS + 1, "version.dll", exe) == OK
    # and Steam's box still wins
    assert read_signals(True, True, False, 1.0, "version.dll", exe) == DRM_ERROR


def test_a_crash_in_the_exe_itself_is_still_read_as_a_refusal():
    """a wrapper that finds its exe patched can take the game down from its own code"""
    exe = "ffxiiiimg.exe"
    assert read_signals(False, True, False, 1.0, "FFXIIIIMG.EXE", exe) == DRM_ERROR
    assert read_signals(False, False, False, 0.0, "ffxiiiimg.exe", exe) == DRM_ERROR


EVENTS = """<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>\
<Provider Name='Application Error'/><EventID>1000</EventID>\
<TimeCreated SystemTime='2026-10-11T19:05:31.1234567Z'/></System><EventData>\
<Data Name='AppName'>ffxiii2img.exe</Data><Data Name='ModuleName'>version.dll</Data>\
<Data Name='ExceptionCode'>c0000005</Data><Data Name='ProcessId'>0x2a3c</Data>\
<Data Name='ModulePath'>D:\\XIII-2\\alba_data\\prog\\win\\bin\\version.dll</Data>\
</EventData></Event>
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>\
<TimeCreated SystemTime='2026-10-11T18:00:00.0000000Z'/></System><EventData>\
<Data>LRFF13.exe</Data><Data>1.0</Data><Data>5</Data><Data>LRFF13.exe</Data><Data>1</Data>\
<Data>2</Data><Data>c0000005</Data><Data>1234</Data><Data>0x10</Data><Data>0x1</Data>\
<Data>C:\\LR\\LRFF13.exe</Data><Data>C:\\LR\\LRFF13.exe</Data></EventData></Event>"""


def test_windows_crash_events_are_read_named_or_in_order():
    """Windows 7 writes event 1000's fields with no names, in a fixed order"""
    from patcher.launch_watch import read_crashes

    newer, older = read_crashes(EVENTS)
    assert newer["ModuleName"] == "version.dll" and newer["ProcessId"] == "0x2a3c"
    assert older["AppName"] == "LRFF13.exe" and older["ModulePath"] == "C:\\LR\\LRFF13.exe"
    assert read_crashes("") == [] and read_crashes("<not xml") == []


def test_only_this_launchs_crash_of_this_exe_counts():
    """an earlier launch's crash is the same exe and a different story"""
    from datetime import datetime, timezone

    from patcher.launch_watch import pick_crash, read_crashes

    events = read_crashes(EVENTS)
    launched = datetime(2026, 10, 11, 19, 5, tzinfo=timezone.utc).timestamp()
    assert pick_crash(events, "FFXIII2IMG.EXE", launched, 0x2a3c)["module"] == "version.dll"
    assert pick_crash(events, "ffxiii2img.exe", launched, None)["path"].endswith("version.dll")
    assert pick_crash(events, "ffxiii2img.exe", launched, 7) == {}
    assert pick_crash(events, "ffxiii2img.exe", launched + 60, None) == {}
    assert pick_crash(events, "LRFF13.exe", launched, None) == {}


def test_what_a_failed_launch_says():
    from patcher.launch_watch import Seen, said

    crash = Seen(CRASHED, 0.9, 0xC0000005, {"module": "version.dll"})
    line = said("Final Fantasy XIII-2", crash, "HD Fonts and GUI", drawer=True)
    assert line.startswith("Final Fantasy XIII-2 closed as it started. Windows logged a crash "
                           "in version.dll, which is HD Fonts and GUI's.")
    assert "Take HD Fonts and GUI off under Installed in the Mods drawer" in line
    # NovaFix isn't the drawer's to take off
    line = said("Final Fantasy XIII-2", Seen(CRASHED, 5.0, None, {"module": "d3d9.dll"}), "NovaFix")
    assert "5 seconds after" in line and "NovaFix's" in line and "Mods drawer" not in line
    assert said("LR", Seen(DRM_ERROR, 1.0, 0xC0000135)).endswith("a dll it loads at start is missing.")
    assert said("LR", Seen(DRM_ERROR, 1.0, 0x1234)).endswith("code 0x00001234.")
    assert said("LR", Seen(DRM_ERROR, 1.0, 0)) == "LR closed itself as it started, without an error."
    assert "didn't log why" in said("LR", Seen(DRM_ERROR, 1.0))
