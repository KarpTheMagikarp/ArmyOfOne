import threading
from pathlib import Path

import pytest

from patcher.acquire import Acquisition, Stage


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    events = []
    acq = Acquisition(job_id="audio", page_url="https://nexus.test/mods/43?tab=files",
                      emit=events.append, clock=clock, grace=18.0)
    return acq, clock, events, tmp_path


def test_the_first_prompt_tells_the_user_what_to_do(setup):
    acq, clock, events, _ = setup
    acq.start()

    stages = [e.stage for e in events]
    assert stages == [Stage.OPENING, Stage.WATCHING]
    assert "press Download" in events[-1].prompt
    assert "we'll grab it" in events[-1].prompt.lower()


def test_the_prompt_changes_itself_when_nothing_arrives(setup):
    """
    a blocked catcher looks exactly like a working one. leaving the optimistic
    prompt up forever is the failure this whole design exists to avoid.
    """
    acq, clock, events, _ = setup
    acq.start()

    clock.advance(10)
    assert acq.tick() is Stage.WATCHING

    clock.advance(9)                     # past the grace period
    assert acq.tick() is Stage.FALLBACK
    assert "Drag the downloaded file" in events[-1].prompt
    assert "browse" in events[-1].prompt


def test_the_prompt_only_changes_once(setup):
    acq, clock, events, _ = setup
    acq.start()
    clock.advance(30)
    acq.tick()
    before = len(events)
    for _ in range(5):
        clock.advance(5)
        acq.tick()
    assert len(events) == before, "no repeated nagging"


def test_a_drop_is_accepted_before_the_timeout(setup):
    """
    someone who already has the file must not have to watch a countdown for
    the privilege of using it.
    """
    acq, clock, events, tmp = setup
    acq.start()
    mod = tmp / "mod.zip"
    mod.write_bytes(b"x")

    clock.advance(2)
    assert acq.offer(mod, source="drop") is True
    assert acq.stage is Stage.ARRIVED
    assert acq.result == mod
    assert events[-1].source == "drop"


def test_a_catch_from_the_embedded_browser_wins(setup):
    acq, clock, events, tmp = setup
    acq.start()
    mod = tmp / "caught.zip"
    mod.write_bytes(b"x")

    acq.offer(mod, source="catcher")

    assert events[-1].source == "catcher"
    assert events[-1].stage is Stage.ARRIVED


def test_a_second_file_is_ignored_once_one_arrived(setup):
    """the catcher and the folder watcher can both fire for the same download"""
    acq, clock, events, tmp = setup
    acq.start()
    first, second = tmp / "a.zip", tmp / "b.zip"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    assert acq.offer(first, source="catcher") is True
    assert acq.offer(second, source="downloads") is False
    assert acq.result == first


def test_a_file_that_does_not_exist_is_refused(setup):
    acq, clock, events, tmp = setup
    acq.start()
    assert acq.offer(tmp / "ghost.zip") is False
    assert acq.stage is Stage.WATCHING


def test_the_drop_target_still_works_after_giving_up(setup):
    """
    watching stops after the give-up window; the escape hatch never does. she
    may go and find the file twenty minutes later.
    """
    acq, clock, events, tmp = setup
    acq.give_up = 60
    acq.start()
    clock.advance(600)

    assert acq.expired() is True
    mod = tmp / "late.zip"
    mod.write_bytes(b"x")
    assert acq.offer(mod, source="picker") is True
    assert acq.stage is Stage.ARRIVED


def test_run_returns_as_soon_as_a_poll_finds_something(setup):
    acq, clock, events, tmp = setup
    mod = tmp / "found.zip"
    mod.write_bytes(b"x")
    calls = []

    def poll():
        calls.append(1)
        return mod if len(calls) >= 3 else None

    result = acq.run(poll=poll, interval=1.0, sleep=lambda s: clock.advance(s))

    assert result == mod
    assert events[-1].source == "downloads"


def test_run_is_cancellable(setup):
    acq, clock, events, tmp = setup
    cancel = threading.Event()
    cancel.set()

    assert acq.run(poll=lambda: None, sleep=lambda s: clock.advance(s), cancel=cancel) is None
    assert acq.stage is Stage.CANCELLED


def test_a_drop_on_another_thread_ends_a_running_wait(setup):
    """
    the UI thread takes the drop while the worker thread is polling. The drop's
    caller installs it, so the run hands nothing back: handing it back had the
    run's thread install the same file at the same time
    """
    acq, clock, events, tmp = setup
    mod = tmp / "dropped.zip"
    mod.write_bytes(b"x")
    ticks = []

    def sleep(seconds):
        clock.advance(seconds)
        ticks.append(1)
        if len(ticks) == 2:
            acq.offer(mod, source="drop")

    assert acq.run(poll=lambda: None, interval=1.0, sleep=sleep) is None
    assert acq.stage is Stage.ARRIVED and acq.result == mod
    assert events[-1].source == "drop"


def test_every_prompt_is_plain_language(setup):
    """it is being tested by someone who is not technical"""
    from patcher.acquire import PROMPTS
    jargon = ("nxm", "api", "webview", "intercept", "protocol", "handler", "hook")
    for stage, text in PROMPTS.items():
        assert not any(word in text.lower() for word in jargon), f"{stage} says {text!r}"
        assert len(text) < 110


def test_a_download_turning_up_says_so_and_counts(setup):
    acq, clock, events, _ = setup
    acq.start()
    acq.growing(4096)
    clock.advance(1)
    acq.growing(4096)
    acq.growing(9000)
    downloading = [e for e in events if e.stage is Stage.DOWNLOADING]
    assert [e.landed for e in downloading] == [4096, 9000], "an unchanged size said again"
    assert downloading[0].as_dict()["bytes"] == 4096


def test_a_download_going_on_is_never_called_missed(setup):
    """past the grace period a working download still reads as one"""
    acq, clock, events, _ = setup
    acq.start()
    acq.growing(10)
    for _ in range(40):
        clock.advance(1)
        acq.growing(10 + clock.now)
        acq.tick()
    assert acq.stage is Stage.DOWNLOADING
    assert Stage.FALLBACK not in [e.stage for e in events]


def test_one_that_stops_without_a_file_goes_back_to_the_way_out(setup):
    acq, clock, events, _ = setup
    acq.start()
    acq.growing(10)
    clock.advance(5)
    acq.growing(None)
    assert acq.stage is Stage.DOWNLOADING, "a beat between the rename and the file is nothing"
    clock.advance(20)
    acq.growing(None)
    assert acq.stage is Stage.FALLBACK


def test_a_big_download_keeps_the_watch_going_past_the_give_up(setup):
    """six gigabytes on a slow line outlasts fifteen minutes of a quiet page"""
    acq, clock, events, _ = setup
    acq.give_up = 900
    acq.start()
    clock.advance(800)
    acq.growing(1)
    clock.advance(800)
    assert not acq.expired()
    clock.advance(200)
    assert acq.expired()


def test_run_hears_the_download_from_the_watcher(setup):
    acq, clock, events, tmp = setup
    sizes = iter([None, 100, 200, None])
    done = tmp / "mod.zip"
    done.write_bytes(b"x")
    found = iter([None, None, None, None, done])

    def sleep(seconds):
        clock.advance(seconds)

    assert acq.run(poll=lambda: next(found), partial=lambda: next(sizes), interval=1.0,
                   sleep=sleep) == done
    assert [e.landed for e in events if e.stage is Stage.DOWNLOADING] == [100, 200]


def test_a_download_that_stops_growing_hands_back_the_way_out(setup):
    """a dropped connection keeps its partial file, and used to hold the strip for fifteen minutes"""
    acq, clock, events, _ = setup
    acq.start()
    acq.growing(100)
    for _ in range(3):
        clock.advance(1)
        acq.growing(100)
    assert acq.stage is Stage.DOWNLOADING, "a beat without growth isn't a stall"
    for _ in range(27):
        clock.advance(1)
        acq.growing(100)
    assert acq.stage is Stage.HELD, "but a while is the browser sitting on it, most often"
    for _ in range(40):
        clock.advance(1)
        acq.growing(100)
    assert acq.stage is Stage.FALLBACK
    acq.growing(200)
    assert acq.stage is Stage.DOWNLOADING, "and picking up again takes it back"


def test_one_the_browser_holds_at_full_size_says_to_choose_keep(setup):
    """
    Chrome keeps a zip with an exe in it as "Unconfirmed 123456.crdownload"
    until Keep is pressed, and the count just stopping read as the app stuck
    """
    acq, clock, events, _ = setup
    acq.start()
    acq.growing(21_293_472)
    clock.advance(7)
    acq.growing(21_293_472)
    held = [e for e in events if e.stage is Stage.HELD]
    assert len(held) == 1
    assert "choose Keep" in held[0].prompt
    assert held[0].as_dict()["bytes"] == 21_293_472
    clock.advance(5)
    acq.growing(21_293_472)
    assert len([e for e in events if e.stage is Stage.HELD]) == 1, "said once"


def test_a_download_whose_size_is_known_says_how_much_of_it_there_is(setup):
    acq, clock, events, _ = setup
    acq.sizes = {"Aerith_Garb_Restauration-23-1-1-1759690588.zip": 21_293_472}
    acq.start()
    acq.growing(4096)
    acq.growing(10_000_000, "aerith_garb_restauration-23-1-1-1759690588.zip")
    acq.growing(12_000_000, "Some other file.zip")
    sizes = [(e.landed, e.total, e.file) for e in events if e.stage is Stage.DOWNLOADING]
    assert sizes == [(4096, None, ""),
                     (10_000_000, 21_293_472, "aerith_garb_restauration-23-1-1-1759690588.zip"),
                     (12_000_000, None, "Some other file.zip")]
    assert events[-1].as_dict()["total"] is None


def test_a_job_of_two_files_hands_each_over_and_ends_on_the_second(setup):
    """LR's DLC restoration is two downloads off one page, and the watch ended on the first"""
    acq, clock, events, tmp = setup
    acq.want = 2
    first, second = tmp / "Aerith.zip", tmp / "Outerworld.zip"
    first.write_bytes(b"a")
    second.write_bytes(b"o")
    found = iter([None, first, None, None, second, None])
    assert acq.run(poll=lambda: next(found), interval=1.0, sleep=clock.advance) == second
    arrived = [e.as_dict() for e in events if e.stage is Stage.ARRIVED]
    assert [(a["path"], a["left"]) for a in arrived] == [(str(first), 1), (str(second), 0)]
    assert acq.stage is Stage.ARRIVED


def test_the_give_up_counts_from_the_last_file_that_landed(setup):
    acq, clock, events, tmp = setup
    acq.want, acq.give_up = 2, 100
    first = tmp / "Aerith.zip"
    first.write_bytes(b"a")
    acq.start()
    clock.advance(90)
    assert not acq._landed_one(first)
    clock.advance(90)
    assert not acq.expired(), "a fresh wait for the second"
    clock.advance(20)
    assert acq.expired()


def test_a_partial_held_for_a_virus_scan_then_renamed_isnt_called_missed(setup):
    """full size, held still for a scan, then renamed: the file turns up a beat later"""
    acq, clock, events, _ = setup
    acq.start()
    acq.growing(500)
    for _ in range(20):
        clock.advance(1)
        acq.growing(500)
    acq.growing(None)
    clock.advance(1.5)
    acq.growing(None)
    assert Stage.FALLBACK not in [e.stage for e in events]


def test_a_download_retried_late_is_still_watched_for(setup):
    """falling back after the give up used to end the watch on the spot"""
    acq, clock, events, _ = setup
    acq.give_up = 900
    acq.start()
    clock.advance(600)
    acq.growing(10)
    clock.advance(400)
    acq.growing(20)
    acq.growing(None)
    clock.advance(20)
    acq.growing(None)
    assert acq.stage is Stage.FALLBACK
    assert not acq.expired()
    clock.advance(10)
    acq.growing(5)
    assert acq.stage is Stage.DOWNLOADING


def test_run_ends_past_the_give_up_and_says_so(setup):
    """a run that never ended kept the job taken, and Open page couldn't watch for it again"""
    acq, clock, events, _ = setup
    acq.give_up = 60
    assert acq.run(poll=lambda: None, interval=1.0, sleep=clock.advance) is None
    assert acq.stage is Stage.EXPIRED and events[-1].stage is Stage.EXPIRED
    assert events[-1].as_dict()["stage"] == "expired"


def test_a_named_download_that_only_stops_coming_in_isnt_said_to_be_held(setup):
    """
    a named one short of its size is a connection's stall, and a 6GB pack that
    paused read as finished and kept by the browser
    """
    acq, clock, events, _ = setup
    acq.sizes = {"Big Pack.zip": 6_000_000}
    acq.start()
    acq.growing(100, "Big Pack.zip")
    for _ in range(30):
        clock.advance(1)
        acq.growing(100, "Big Pack.zip")
    assert Stage.HELD not in [e.stage for e in events]
    for _ in range(40):
        clock.advance(1)
        acq.growing(100, "Big Pack.zip")
    assert acq.stage is Stage.FALLBACK


def test_a_named_download_all_there_and_sitting_still_is_held(setup):
    acq, clock, events, _ = setup
    acq.sizes = {"Aerith.zip": 500}
    acq.start()
    acq.growing(500, "Aerith.zip")
    clock.advance(7)
    acq.growing(500, "Aerith.zip")
    assert acq.stage is Stage.HELD


def test_an_unnamed_download_after_a_named_one_isnt_said_under_its_name(setup):
    acq, clock, events, _ = setup
    acq.sizes = {"Aerith.zip": 500}
    acq.start()
    acq.growing(400, "Aerith.zip")
    acq.growing(30)
    last = events[-1].as_dict()
    assert (last["file"], last["total"], last["bytes"]) == ("", None, 30)


def test_a_watch_stopped_before_it_started_says_nothing_after(setup):
    """Stop pressed while Nexus was being asked for the sizes, before run()"""
    acq, clock, events, _ = setup
    acq.cancel()
    assert acq.run(poll=lambda: None, interval=1.0, sleep=clock.advance) is None
    assert [e.stage for e in events] == [Stage.CANCELLED]
