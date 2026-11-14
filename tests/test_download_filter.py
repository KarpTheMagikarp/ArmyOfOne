"""
The watcher looks at the user's Downloads folder, which is where a browser puts
everything. Its docstring has always promised "only files matching what the
waiting patch expects" and nothing ever passed it the patch, so it fell back to
every archive suffix plus .exe.

The failure is not that a GPU driver gets injected into Final Fantasy: scan()
finds no game assets in one and the install fails. It is that the watch ENDS.
The real mod download, still going, is never looked at again, and the user gets
an error about a file they did not choose.
"""

from pathlib import Path

import pytest

from patcher.bridge import UiBridge
from patcher.download_watch import (ARCHIVE_SUFFIXES, INSTALLER_SUFFIXES,
                                    DownloadWatcher)

REPO = Path(__file__).resolve().parent.parent


def bridge_with_manifests():
    """the real shipped manifests, because the point is what they actually say"""
    paths = {p.stem: p for p in sorted((REPO / "manifests").glob("*.json"))}
    return UiBridge(manifest_paths=paths, steam_library_paths=[])


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def settled(watcher, clock):
    """poll twice with the settle window in between, which is what a real wait does"""
    watcher.poll()
    clock.advance(watcher.settle_seconds + 1)
    return watcher.poll()


def test_exe_is_not_an_archive():
    """the whole point. Downloads is full of .exe and almost none of it is ours"""
    assert ".exe" not in ARCHIVE_SUFFIXES
    assert ".exe" in INSTALLER_SUFFIXES


def test_a_browser_installer_is_ignored_and_the_watch_continues(tmp_path):
    clock = FakeClock()
    watcher = DownloadWatcher(folder=tmp_path, clock=clock)
    watcher.start()

    (tmp_path / "FirefoxSetup.exe").write_bytes(b"x" * 5000)
    assert settled(watcher, clock) is None, "adopted an unrelated installer"

    # and the real download, landing later, is still caught
    (tmp_path / "LeviathansTears.rar").write_bytes(b"y" * 5000)
    found = settled(watcher, clock)
    assert found is not None and found.path.name == "LeviathansTears.rar"


def test_a_patch_that_ships_an_installer_still_accepts_one(tmp_path):
    """
    narrowing cannot break the three patches that legitimately download an
    installer, which is exactly what runs_installer is for
    """
    clock = FakeClock()
    watcher = DownloadWatcher(folder=tmp_path, clock=clock,
                              patterns=list(ARCHIVE_SUFFIXES) + list(INSTALLER_SUFFIXES))
    watcher.start()

    (tmp_path / "HDProjectInstaller.exe").write_bytes(b"z" * 5000)
    found = settled(watcher, clock)

    assert found is not None and found.path.suffix == ".exe"


def test_something_far_too_small_is_not_the_six_gig_texture_pack(tmp_path):
    clock = FakeClock()
    watcher = DownloadWatcher(folder=tmp_path, clock=clock, min_bytes=10_000)
    watcher.start()

    (tmp_path / "unrelated.zip").write_bytes(b"a" * 500)
    assert settled(watcher, clock) is None

    (tmp_path / "texturepack.zip").write_bytes(b"b" * 50_000)
    found = settled(watcher, clock)
    assert found is not None and found.path.name == "texturepack.zip"


def test_a_download_growing_past_the_floor_is_still_caught(tmp_path):
    """
    the floor must not poison the candidate. A browser creates the final name
    and fills it, so every big download is briefly tiny, and skipping it once
    has to mean skipping it once rather than forever.
    """
    clock = FakeClock()
    watcher = DownloadWatcher(folder=tmp_path, clock=clock, min_bytes=10_000)
    watcher.start()

    target = tmp_path / "big.rar"
    target.write_bytes(b"a" * 200)
    assert settled(watcher, clock) is None

    target.write_bytes(b"a" * 60_000)
    found = settled(watcher, clock)
    assert found is not None, "a download that grew past the floor was never picked up"


def test_the_floor_leaves_room_for_a_split_archive():
    """
    a tenth of the estimate, because Nexus serves big packs in parts and a
    floor that assumes one file would reject every one of them
    """
    bridge = bridge_with_manifests()
    limits = bridge.download_filter("ff13", "hdproject")

    assert limits["minBytes"] is not None
    # six gig pack, and a part could plausibly be a gig
    assert limits["minBytes"] < 1024 ** 3


@pytest.mark.parametrize("game,job", [("ff13", "hdproject"),
                                      ("ff13-2", "hdproject2")])
def test_every_installer_patch_can_match_an_exe(game, job):
    bridge = bridge_with_manifests()
    assert ".exe" in bridge.download_filter(game, job)["suffixes"]


@pytest.mark.parametrize("game,job", [("ff13", "laa"),
                                      ("ff13-2", "leviathanstears"),
                                      ("ff13-2", "dlcrestore"),
                                      ("lr", "lrfix")])
def test_no_drop_in_patch_can_match_an_exe(game, job):
    bridge = bridge_with_manifests()
    assert ".exe" not in bridge.download_filter(game, job)["suffixes"]


def test_an_unknown_job_gets_archives_and_no_floor():
    """
    an unknown job must not widen to .exe. guessing wider than we can justify
    is how this went wrong in the first place.
    """
    bridge = bridge_with_manifests()
    for game, job in [("ff13", "nonsense"), ("nonsense", "laa"), ("", "")]:
        limits = bridge.download_filter(game, job)
        assert ".exe" not in limits["suffixes"], f"{game}/{job} widened to exe"
        assert limits["minBytes"] is None


def test_the_shell_actually_passes_the_filter_through():
    """
    the bug was never in the watcher, it was that nobody handed it anything.
    a test on download_filter alone would have passed the whole time.
    """
    source = (REPO / "shell.py").read_text(encoding="utf-8")
    start = source.index("def begin_acquire")
    body = source[start:start + 2000]

    assert "download_filter" in body, "begin_acquire builds a watcher without asking for limits"
    assert "patterns=" in body and "min_bytes=" in body


@pytest.mark.parametrize("game, job, nexus", [
    ("ff13", "hdproject", None), ("ff13-2", "hdproject2", None),
    ("ff13", "hdfonts", "67"), ("ff13-2", "hdfonts", "40"), ("lr", "dlcrestore", "23"),
])
def test_only_a_row_with_no_floor_goes_by_its_mods_id(game, job, nexus):
    """
    XIII-2's HD Project is mod 1, which every version 1 of anything has in its
    name, and its floor keeps everything else out already
    """
    assert bridge_with_manifests().download_filter(game, job)["nexus"] == nexus
