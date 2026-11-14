"""
The percent on the Mods drawer's line: each part a share of the hundred by
what it costs, the percent how much of the whole is done whichever parts that's
in, only ever climbing, and a hundred only once the whole step is through.
"""

from patcher.mods.meter import Meter


def listening():
    heard = []
    return heard, lambda done, of: heard.append((done, of))


def test_each_part_fills_its_own_share_and_the_number_only_climbs():
    heard, say = listening()
    meter = Meter(say)
    meter.split(look=1, write=3)
    meter.add("look", 1)
    meter.add("write", 1.5)
    # past its own share: still a quarter of the whole
    meter.add("look", 5)
    assert heard == [(0, 100), (25, 100), (62, 100)]


def test_a_part_credited_before_its_turn_counts_then_not_in_a_leap_later():
    """an install's files that don't move have nothing to flush, known in the loop"""
    heard, say = listening()
    meter = Meter(say)
    meter.split(write=2, flush=2)
    meter.add("flush", 1)
    assert heard[-1] == (25, 100)
    meter.add("write", 2)
    assert heard[-1] == (75, 100)


def test_a_part_reported_as_done_of_of_never_goes_back():
    heard, say = listening()
    meter = Meter(say)
    meter.split(files=1, loader=1)
    meter.at("files", 50, 100)
    meter.at("files", 30, 100)
    meter.at("loader", 0, 0)
    assert heard == [(0, 100), (25, 100), (75, 100)]


def test_a_hundred_waits_for_the_end_not_the_last_part():
    heard, say = listening()
    meter = Meter(say)
    meter.split(only=10)
    meter.add("only", 10)
    assert heard[-1] == (99, 100)
    meter.end()
    assert heard[-1] == (100, 100)


def test_each_whole_percent_is_said_once():
    """a thousand files is a hundred calls into the page, not a thousand"""
    heard, say = listening()
    meter = Meter(say)
    meter.split(files=1000)
    for _ in range(1000):
        meter.add("files", 1)
    assert [done for done, _ in heard] == list(range(100))


def test_a_part_that_costs_nothing_takes_none_of_the_hundred():
    heard, say = listening()
    meter = Meter(say)
    meter.split(look=0, write=4)
    meter.add("look", 0)
    meter.add("write", 2)
    assert heard == [(0, 100), (50, 100)]


def test_with_nobody_listening_it_does_nothing():
    meter = Meter()
    meter.split(a=1)
    meter.add("a", 1)
    meter.at("a", 1, 1)
    meter.end()
