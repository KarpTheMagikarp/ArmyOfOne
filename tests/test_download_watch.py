import threading
from pathlib import Path

import pytest

from patcher.download_watch import DownloadWatcher, Sighting, downloads_folder


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    return d


def watcher(folder, clock):
    return DownloadWatcher(folder=folder, clock=clock, settle_seconds=1.0)


def test_a_file_already_there_is_never_picked_up(folder):
    """
    an old copy of the same mod sitting in Downloads from last week must not
    be reported as a fresh install.
    """
    (folder / "leviathans-tears.zip").write_bytes(b"old")
    clock = Clock()
    w = watcher(folder, clock)
    w.start()

    clock.advance(5)
    assert w.poll() is None


def test_a_new_download_is_reported_once_it_stops_growing(folder):
    clock = Clock()
    w = watcher(folder, clock)
    w.start()

    target = folder / "FFXIII-2_AudioFix.zip"
    target.write_bytes(b"x" * 1000)

    clock.advance(0.5)
    assert w.poll() is None, "first sight only records the size"

    clock.advance(1.5)
    found = w.poll()
    assert found is not None
    assert found.path == target
    assert found.size == 1000


def test_the_settle_timer_does_not_reset_on_every_poll(folder):
    """
    an early version refreshed the timestamp on each poll, so the settle
    window restarted continuously and a finished file was never handed over.
    """
    clock = Clock()
    w = watcher(folder, clock)
    w.start()
    (folder / "mod.zip").write_bytes(b"x" * 1000)

    for _ in range(6):          # poll faster than the settle window
        clock.advance(0.3)
        found = w.poll()
        if found:
            break
    assert found is not None, "polling frequently must not prevent it settling"


def test_a_file_still_being_written_is_not_handed_over(folder):
    """
    a browser creates the final name and then fills it. acting on first sight
    hands over a truncated archive and produces a confusing 'corrupt zip' a
    few seconds later.
    """
    clock = Clock()
    w = watcher(folder, clock)
    w.start()

    target = folder / "big-texture-pack.zip"
    target.write_bytes(b"x" * 1000)
    clock.advance(2)
    w.poll()

    target.write_bytes(b"x" * 5000)     # still growing
    clock.advance(2)
    assert w.poll() is None

    clock.advance(2)
    found = w.poll()
    assert found is not None and found.size == 5000


def test_partial_download_extensions_are_ignored(folder):
    clock = Clock()
    w = watcher(folder, clock)
    w.start()

    for name in ("mod.zip.crdownload", "mod.zip.part", "mod.zip.tmp"):
        (folder / name).write_bytes(b"x" * 1000)

    clock.advance(5)
    assert w.poll() is None


def test_unrelated_file_types_are_ignored(folder):
    clock = Clock()
    w = watcher(folder, clock)
    w.start()

    (folder / "invoice.pdf").write_bytes(b"x" * 1000)
    (folder / "song.mp3").write_bytes(b"x" * 1000)

    clock.advance(5)
    assert w.poll() is None


def test_an_empty_file_is_not_reported(folder):
    clock = Clock()
    w = watcher(folder, clock)
    w.start()
    (folder / "mod.zip").touch()
    clock.advance(5)
    assert w.poll() is None


def test_wait_returns_as_soon_as_the_file_settles(folder):
    clock = Clock()
    w = watcher(folder, clock)
    w.start()
    (folder / "mod.zip").write_bytes(b"x" * 2000)

    def fake_sleep(seconds):
        clock.advance(seconds)

    found = w.wait(timeout=60, interval=0.5, sleep=fake_sleep)

    assert isinstance(found, Sighting)
    assert found.path.name == "mod.zip"


def test_wait_gives_up_rather_than_hanging(folder):
    clock = Clock()
    w = watcher(folder, clock)
    w.start()

    assert w.wait(timeout=5, interval=0.5, sleep=lambda s: clock.advance(s)) is None


def test_wait_is_cancellable_immediately(folder):
    clock = Clock()
    w = watcher(folder, clock)
    w.start()
    cancel = threading.Event()
    cancel.set()

    assert w.wait(timeout=600, cancel=cancel, sleep=lambda s: clock.advance(s)) is None


def test_a_missing_folder_is_not_an_error(tmp_path):
    """the downloads folder can be moved or absent; that is not a crash"""
    w = DownloadWatcher(folder=tmp_path / "nope", clock=Clock())
    w.start()
    assert w.poll() is None


def test_downloads_folder_resolves_to_something(tmp_path):
    assert isinstance(downloads_folder(), Path)


def test_the_watcher_opens_nothing_it_has_not_matched(folder, monkeypatch):
    """it reads names and sizes, never contents"""
    opened = []
    real_open = Path.open

    def spy(self, *a, **k):
        opened.append(self.name)
        return real_open(self, *a, **k)

    clock = Clock()
    w = watcher(folder, clock)
    w.start()
    (folder / "secret.pdf").write_bytes(b"private")
    monkeypatch.setattr(Path, "open", spy)   # patch AFTER the fixture writes
    clock.advance(5)
    w.poll()

    assert opened == []


def test_a_download_still_being_written_says_how_far_it_has_got(folder):
    """
    Chrome's "Unconfirmed 123456.crdownload" has no name to match, and the
    file it becomes isn't there yet, so this is what a "Downloading" line
    has to go on
    """
    clock = Clock()
    (folder / "old.part").write_bytes(b"x" * 10)
    w = watcher(folder, clock)
    w.start()
    assert w.partial() is None, "one from before the wait isn't this download"

    (folder / "Unconfirmed 123456.crdownload").write_bytes(b"x" * 4096)
    assert w.partial() == 4096
    (folder / "Unconfirmed 123456.crdownload").write_bytes(b"x" * 9000)
    assert w.partial() == 9000
    # and never a finished download, which poll() is for
    assert w.poll() is None


def test_a_finished_download_is_not_a_partial_one(folder):
    clock = Clock()
    w = watcher(folder, clock)
    w.start()
    (folder / "mod.zip").write_bytes(b"x" * 100)
    assert w.partial() is None


def test_only_a_download_that_could_be_this_one_is_counted(folder):
    """an installer, Office's own temp files and a film were all read as the mod downloading"""
    clock = Clock()
    w = watcher(folder, clock)
    w.start()
    for name in ("setup.exe.crdownload", "~WRL0001.tmp", "film.mkv.part"):
        (folder / name).write_bytes(b"x" * 500)
    assert w.partial() is None

    (folder / "Nicht bestätigt 123456.crdownload").write_bytes(b"x" * 600)
    assert w.partial() == 600, "Chrome's unnamed one, in German"
    (folder / "Nicht bestätigt 123456.crdownload").unlink()
    (folder / "FFXIII HD Project.zip.crdownload").write_bytes(b"x" * 700)
    assert w.partial() == 700


def test_two_downloads_at_once_dont_take_turns_at_the_count(folder):
    import os

    clock = Clock()
    w = watcher(folder, clock)
    w.start()
    first = folder / "Mod.zip.crdownload"
    first.write_bytes(b"x" * 100)
    os.utime(first, ns=(10**18, 10**18))
    assert w.partial() == 100
    other = folder / "Other.zip.crdownload"
    other.write_bytes(b"x" * 9999)
    os.utime(other, ns=(2 * 10**18, 2 * 10**18))
    assert w.partial() == 100, "the newer one took the count over"
    first.unlink()
    assert w.partial() == 9999


def test_a_paused_download_picked_up_again_is_counted(folder):
    clock = Clock()
    paused = folder / "Mod.zip.crdownload"
    paused.write_bytes(b"x" * 100)
    w = watcher(folder, clock)
    w.start()
    assert w.partial() is None, "not until it moves"
    paused.write_bytes(b"x" * 300)
    assert w.partial() == 300


def test_firefoxs_empty_placeholder_from_before_the_wait_is_still_caught_filled(folder):
    """Firefox makes the finished name, empty, as a download starts"""
    clock = Clock()
    placeholder = folder / "Mod.zip"
    placeholder.write_bytes(b"")
    w = watcher(folder, clock)
    w.start()
    placeholder.write_bytes(b"x" * 1000)
    clock.advance(0.5)
    assert w.poll() is None
    clock.advance(1.5)
    found = w.poll()
    assert found is not None and found.path == placeholder


def test_firefoxs_random_name_while_it_asks_where_is_followed(folder):
    """with its save dialog up Firefox writes "AbC1xYz9.part", and the watch gave up on a download going"""
    clock = Clock()
    w = watcher(folder, clock)
    w.start()
    (folder / "AbC1xYz9.part").write_bytes(b"x" * 300)
    assert w.partial() == 300
    (folder / "~WRL0001.tmp").write_bytes(b"x" * 900)
    assert w.partial() == 300, "a program's own temp file isn't a download"


@pytest.mark.parametrize("name,ok", [
    ("Aerith_Garb_Restauration-23-1-1-1759690588.zip", True),
    ("FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip", True),
    ("FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437 (1).zip", True),
    ("Aerith_Garb_Restauration-23-1-1-1759690588.zip.crdownload", True),
    ("Some Mod-123-1-0-1759690588.zip", False),
    ("Some Mod-23-1759690588.zip", False),
    ("Aerith_Garb_Restauration.zip", False),
    ("Mod-1-23-4-1759690588.zip", True),
])
def test_a_nexus_download_is_known_by_its_mods_id(name, ok):
    from patcher.download_watch import nexus_named

    assert nexus_named(name, "23") is ok


def test_a_watch_for_one_mod_leaves_anything_else_that_lands(folder):
    """
    the drawer's watch now goes on past its first file, so something else
    downloaded meanwhile has to be told apart by more than being a zip
    """
    clock = Clock()
    w = DownloadWatcher(folder=folder, clock=clock, settle_seconds=1.0, nexus="23")
    w.start()
    (folder / "HD Fonts and GUI-40-1-0-1759000000.zip").write_bytes(b"x" * 100)
    mine = folder / "Aerith_Garb_Restauration-23-1-1-1759690588.zip"
    mine.write_bytes(b"x" * 200)
    clock.advance(0.5)
    assert w.poll() is None
    clock.advance(1.5)
    assert w.poll().path == mine
    clock.advance(2)
    assert w.poll() is None, "each one is handed over once"


def test_a_watch_by_name_takes_a_download_named_like_it(folder):
    clock = Clock()
    w = DownloadWatcher(folder=folder, clock=clock, settle_seconds=1.0,
                        patterns=(".7z", ".zip"), names=["*console*content*patch*"])
    w.start()
    (folder / "something else.7z").write_bytes(b"x" * 100)
    mine = folder / "FFXIII-2_-_Console_Content_Patch_v2.3.7z"
    mine.write_bytes(b"x" * 200)
    clock.advance(0.5)
    w.poll()
    clock.advance(1.5)
    assert w.poll().path == mine


def test_a_download_being_written_says_what_it_will_be_called(folder):
    clock = Clock()
    w = DownloadWatcher(folder=folder, clock=clock, nexus="23")
    w.start()
    (folder / "Other-40-1-0-1759000000.zip.crdownload").write_bytes(b"x" * 900)
    assert w.writing() is None, "another mod's"
    (folder / "Unconfirmed 123456.crdownload").write_bytes(b"x" * 500)
    found = w.writing()
    assert (found.size, found.name) == (500, ""), "Chrome hasn't named it, so it could be"
    (folder / "Unconfirmed 123456.crdownload").unlink()
    (folder / "Aerith_Garb_Restauration-23-1-1-1759690588.zip.crdownload").write_bytes(b"x" * 700)
    found = w.writing()
    assert (found.size, found.name) == (700, "Aerith_Garb_Restauration-23-1-1-1759690588.zip")
    assert w.partial() == 700


def test_a_browsers_second_copy_of_a_file_handed_over_isnt_the_next_one(folder):
    """
    pressing Download again over the browser's warning saves "Mod (1).zip",
    and a row of two files ended on that, with the other never taken
    """
    clock = Clock()
    w = DownloadWatcher(folder=folder, clock=clock, settle_seconds=1.0, nexus="23")
    w.start()
    mine = folder / "Aerith_Garb_Restauration-23-1-1-1759690588.zip"
    mine.write_bytes(b"x" * 200)
    w.poll()
    clock.advance(1.5)
    assert w.poll().path == mine
    (folder / "Aerith_Garb_Restauration-23-1-1-1759690588 (1).zip.crdownload").write_bytes(b"x" * 50)
    assert w.writing() is None, "not followed as the next one either"
    (folder / "Aerith_Garb_Restauration-23-1-1-1759690588 (1).zip").write_bytes(b"x" * 200)
    w.poll()
    clock.advance(1.5)
    assert w.poll() is None
    other = folder / "FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip"
    other.write_bytes(b"y" * 9)
    w.poll()
    clock.advance(1.5)
    assert w.poll().path == other


@pytest.mark.parametrize("name, was", [
    ("Mod (1).zip", "mod.zip"), ("Mod(12).7z", "mod.7z"), ("Mod.zip", "mod.zip"),
    ("Pack (2).tar.gz", "pack.tar.gz"), ("Setup (1).exe", "setup.exe"), ("Notes (1)", "notes"),
])
def test_a_copys_name_is_the_name_it_is_a_copy_of(name, was):
    from patcher.download_watch import original

    assert original(name) == was


def test_a_download_already_in_is_passed_over_for_the_one_that_isnt(folder):
    clock = Clock()
    w = DownloadWatcher(folder=folder, clock=clock, settle_seconds=1.0, nexus="23",
                        skip=["Aerith_Garb_Restauration-23-1-1-1759690588.zip"])
    w.start()
    (folder / "Aerith_Garb_Restauration-23-1-1-1759690588 (1).zip").write_bytes(b"x" * 200)
    other = folder / "FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip"
    other.write_bytes(b"y" * 9)
    w.poll()
    clock.advance(1.5)
    assert w.poll().path == other
    clock.advance(1.5)
    assert w.poll() is None
