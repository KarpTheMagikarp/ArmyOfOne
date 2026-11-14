"""
The Downloads watch from the shell's side: a file picked while it's being
watched for goes in once, a watch ends past its give up so the job can be
watched for again, and the Mods drawer's own watch hands over what lands
without installing it, and never an installer.
"""

import functools
import time

import pytest

import shell
from patcher import acquire


class Bridge:
    def __init__(self):
        self.installed = []

    def download_filter(self, game, job):
        return {"suffixes": [".zip", ".exe"], "minBytes": None}

    def install_dropped(self, game, path, job_id=None, cancel=None):
        self.installed.append(path)
        return {"ok": True}

    def game_running(self, game):
        return ""


@pytest.fixture
def api(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr("patcher.download_watch.downloads_folder", lambda: downloads)
    made = shell.Api()
    made._window = None
    made.events = []
    made._emit = made.events.append
    bridge = Bridge()
    monkeypatch.setattr(made, "_patcher", lambda: bridge)
    made.bridge, made.downloads = bridge, downloads
    return made


def watching(api, job, game="") -> bool:
    return (game, job) in api._acquiring


def settle(api, job, seconds=6.0, game=""):
    deadline = time.monotonic() + seconds
    while watching(api, job, game) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not watching(api, job, game), "the watch never ended"


def stages(api):
    return [e["stage"] for e in api.events if e.get("type") == "acquire"]


def test_a_file_picked_while_its_watched_for_goes_in_once(api, tmp_path):
    """the watch's own thread used to install the picked file as well, at the same time"""
    assert api.begin_acquire("audio", "")["started"]
    mod = tmp_path / "mod.zip"
    mod.write_bytes(b"x")

    assert api.supply_file("audio", str(mod))["ok"]
    settle(api, "audio")

    assert api.bridge.installed == [str(mod)]


def test_a_watch_ends_past_its_give_up_and_can_be_started_again(api, monkeypatch):
    """it used to stop looking and never end, so Open page only ever heard "already" """
    monkeypatch.setattr(acquire, "Acquisition",
                        functools.partial(acquire.Acquisition, give_up=0.2))
    assert api.begin_acquire("hdproject", "", True)["started"]
    settle(api, "hdproject")

    assert stages(api)[-1] == "expired"
    assert api.begin_acquire("hdproject", "", True).get("started")
    api.cancel_acquire("hdproject")


def test_the_drawers_watch_hands_over_what_lands_and_installs_nothing(api):
    assert api.begin_acquire("hdproject", "", True)["started"]
    (api.downloads / "setup.exe").write_bytes(b"x" * 100)
    (api.downloads / "FFXIII HD Project.zip").write_bytes(b"x" * 100)
    settle(api, "hdproject")

    arrived = [e for e in api.events if e.get("stage") == "arrived"]
    assert arrived and arrived[0]["path"].endswith("FFXIII HD Project.zip")
    assert api.bridge.installed == []


def test_an_unpack_asked_for_while_a_mod_goes_in_says_that(api):
    """
    the two shared one flag, so the drawer heard "still unpacking" over a mod
    going in, and waited on an unpack that was never coming
    """
    api._installing_mod = True
    said = api.unpack_drop("D:\\Downloads\\FFXIII HD Project.zip")
    assert said["busy"] and said["error"] == shell.MOD_GOING_IN
    assert not api._unpacking
    assert "Mods drawer" in api._start_hd("ff13", "D:\\mod", ["pcs"])["error"]


def test_a_version_turned_away_is_pressed_again_not_dropped_again(api):
    """
    a version picked in the drawer stays up while the install is turned away,
    so what to do next is press Install again, not drop the 2GB download
    """
    api._installing_mod = True
    said = api.install_mod("ff13", "D:\\Downloads\\HD Models Plus.zip", None, "Ultimate")
    assert said["busy"] and "Press Install again once it's in" in said["error"], said
    assert api.install_mod("ff13", "D:\\Downloads\\HD Models Plus.zip")["error"] == shell.MOD_GOING_IN
    api._installing_mod, api._hd_running = False, True
    said = api.install_mod("ff13", "D:\\Downloads\\HD Models Plus.zip", None, "Ultimate")
    assert "Press Install again once that's finished" in said["error"], said


def test_a_watch_that_cant_start_isnt_left_holding_the_job(api, monkeypatch):
    """with nothing behind it, the job answered "already" to every Open page after"""
    def gone():
        raise OSError(2, "no Downloads")

    monkeypatch.setattr("patcher.download_watch.downloads_folder", gone)
    said = api.begin_acquire("hdproject", "", True)
    assert said["ok"] is False and "Downloads" in said["error"]
    assert not watching(api, "hdproject")


def test_each_step_says_whose_it_is(api, tmp_path):
    """a drop going in unpacks too, and its steps were taken for an HD Project's unpack and hidden"""
    events = []
    api._emit = events.append

    class Bridge:
        def install_mod(self, *args, on_line=None, on_phase=None, **kwargs):
            on_phase(phase="unpack", name="Big Pack", done=1, of=2, bytes=True)
            return {"ok": True}

    api._patcher = lambda: Bridge()
    api._running = lambda game: ""
    api.install_mod("ff13-2", str(tmp_path / "Big Pack.7z"))
    assert events[0]["job"] == "install" and events[0]["phase"] == "unpack"


def test_a_watch_that_cant_start_hasnt_opened_the_page(api, monkeypatch):
    """the page opens it then, and opening it here as well gave two tabs"""
    opened = []
    monkeypatch.setattr(shell.webbrowser, "open", lambda *a, **k: opened.append(a))

    def gone():
        raise OSError(2, "no Downloads")

    monkeypatch.setattr("patcher.download_watch.downloads_folder", gone)
    assert api.begin_acquire("hdproject", "https://www.nexusmods.com/x", True)["ok"] is False
    assert opened == []


def test_a_row_of_two_files_watches_for_both_and_only_its_own(api, monkeypatch):
    """
    LR's DLC restoration is two downloads off one Nexus page. The watch ended
    on the first, and anything else that landed meanwhile was taken for it
    """
    def two(game, job):
        return {"suffixes": [".zip"], "minBytes": None, "nexus": "23", "want": 2,
                "sizes": {}}

    monkeypatch.setattr(api.bridge, "download_filter", two, raising=False)
    api.bridge.nexus_sizes = lambda game, job: {
        "FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip": 68648}
    assert api.begin_acquire("dlcrestore", "", True)["started"]
    (api.downloads / "HD Fonts and GUI-33-1-0-1759000000.zip").write_bytes(b"x" * 100)
    (api.downloads / "Aerith_Garb_Restauration-23-1-1-1759690588.zip").write_bytes(b"x" * 100)
    time.sleep(2.5)
    # Download pressed again over the browser's warning: the same file, not the other
    (api.downloads / "Aerith_Garb_Restauration-23-1-1-1759690588 (1).zip").write_bytes(b"x" * 100)
    time.sleep(2.5)
    assert watching(api, "dlcrestore"), "still watching for the second"
    (api.downloads / "FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip").write_bytes(b"y" * 9)
    settle(api, "dlcrestore")

    arrived = [e for e in api.events if e.get("stage") == "arrived"]
    assert [(e["path"].rsplit("/", 1)[-1].rsplit("\\", 1)[-1], e["left"]) for e in arrived] == [
        ("Aerith_Garb_Restauration-23-1-1-1759690588.zip", 1),
        ("FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip", 0)]
    assert api.bridge.installed == []


def test_two_games_rows_sharing_an_id_each_have_a_watch_of_their_own(api):
    """XIII-2's DLC Restoration and LR's are both dlcrestore"""
    assert api.begin_acquire("dlcrestore", "", True, "ff13-2")["started"]
    assert api.begin_acquire("dlcrestore", "", True, "lr")["started"]
    assert api.begin_acquire("dlcrestore", "", True, "lr").get("already")
    assert watching(api, "dlcrestore", "ff13-2") and watching(api, "dlcrestore", "lr")
    api.cancel_acquire("dlcrestore", "ff13-2")
    settle(api, "dlcrestore", game="ff13-2")
    assert watching(api, "dlcrestore", "lr")
    assert {e["game"] for e in api.events if e.get("type") == "acquire"} == {"ff13-2", "lr"}
    api.cancel_acquire("dlcrestore", "lr")
    settle(api, "dlcrestore", game="lr")


def test_a_stop_while_nexus_is_asked_for_sizes_ends_that_watch_and_no_other(api):
    """
    with a key saved, the sizes are asked for before the watch starts, and a
    Stop then, followed by Open page again, used to leave the new watch
    unstoppable once the old one wound down
    """
    import threading

    asked, answer = threading.Event(), threading.Event()

    def sizes(game, job):
        if not asked.is_set():
            asked.set()
            answer.wait(5)
        return {}

    api.bridge.nexus_sizes = sizes
    assert api.begin_acquire("hdfonts", "", True)["started"]
    asked.wait(5)
    api.cancel_acquire("hdfonts")
    assert api.begin_acquire("hdfonts", "", True)["started"]
    newer = api._acquiring[("", "hdfonts")]
    answer.set()
    time.sleep(1.0)
    assert api._acquiring.get(("", "hdfonts")) is newer
    said = [e["stage"] for e in api.events if e.get("type") == "acquire"]
    assert said[:2] == ["cancelled", "opening"] and said.count("opening") == 1, said
    api.cancel_acquire("hdfonts")
    settle(api, "hdfonts")


def test_a_mod_in_the_archives_isnt_taken_off_while_a_drop_goes_in(api):
    """
    LR's patchers put their DLC files in ahead of their records, and take one
    out from under another and back, which a Remove beside them would cut into
    """
    api._installing_mod = True
    said = api.remove_addon("lr", "Aerith Garb Restauration", "files")
    assert said["ok"] is False and "running on this game's archives" in said["error"]
