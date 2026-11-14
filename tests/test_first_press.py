"""
The first Install press, from every state a game can be in when it gets one.

The backup answers "is it already in" for a game this app saw clean, and the
rest used to go on the record or on nothing: a record that outlived its backup
badged a verified game APPLIED, a .bat install read as not there, and a first
install over it backed up a modded game and built HD art over HD art. The
shipped vanilla sizes (hd_vanilla) are what answers now. A fixture game isn't a
real one, so each world brings its own: its untouched index stands in for the
table, the way pin_vanilla does for XIII-2's zone sizes.
"""

import shutil
from pathlib import Path

import pytest

from patcher.mods import hd_vanilla
from patcher.mods.hd_layout import XIII, XIII2

REPO = Path(__file__).resolve().parent.parent
N910 = ("chr/npc/n910/bin/n910.win32.trb", "chr/npc/n910/bin/n910.win32.imgb")


def tables(monkeypatch, models: dict, zones: dict | None = None):
    monkeypatch.setattr(hd_vanilla, "table", lambda layout: models)
    monkeypatch.setattr(hd_vanilla, "zone_size", lambda layout, name: (zones or {}).get(name))


class Xiii:
    """the XIII shell world, with its game's vanilla sizes as the table"""

    def __init__(self, tmp_path, monkeypatch):
        from tests.test_hd_end_to_end import make_world

        self.api, self.events, self.game, self.mod = make_world(tmp_path, monkeypatch)
        self.data = self.mod / "Data"
        self.index = self.game / "white_data" / "sys" / "filelist_scru.win32.bin"
        self.vanilla = {p: p.read_bytes() for p in self.pair()}
        tables(monkeypatch, dict(hd_vanilla.index_sizes(self.index)))

    def pair(self):
        sys_dir = self.game / "white_data" / "sys"
        return [sys_dir / "filelist_scru.win32.bin", sys_dir / "white_scru.win32.bin"]

    def outside(self, paths):
        """the mod's own .bat, or a copy of the game from a PC that had it: grown, no backup"""
        from tests.helpers import index_entries, write_scr_pair

        index, archive = self.pair()
        write_scr_pair(index, archive, index_entries(self.data, grow=paths))

    def verify(self):
        """Steam's Verify Integrity of Game Files"""
        for path, raw in self.vanilla.items():
            path.write_bytes(raw)

    def install(self, selected, mode="add"):
        from tests.test_hd_end_to_end import run_and_wait

        self.events.clear()
        hd = run_and_wait(self.api, self.events, self.game, self.mod, selected, mode=mode)
        ending = next(e for e in hd if e.get("stage") in ("done", "failed", "cancelled"))
        return ending, [e for e in hd if e.get("stage") == "line"]

    def sizes(self):
        return hd_vanilla.index_sizes(self.index)

    def survey(self):
        return self.api.hd_survey("ff13", "hdproject", str(self.mod))


@pytest.fixture
def xiii(tmp_path, monkeypatch):
    return Xiii(tmp_path, monkeypatch)


def test_a_record_that_outlived_its_backup_doesnt_badge_a_verified_game(xiii):
    xiii.install(["npcs"])
    assert "npcs" in xiii.api.hd_categories("ff13", "hdproject")["installed"]

    xiii.verify()
    shutil.rmtree(xiii.game / ".patcher-undo")
    answer = xiii.survey()

    # unverified sent the drawer to the record, which still says NPCs
    assert answer["verified"], answer
    assert answer["applied"] == [], answer


def test_hd_from_the_mods_own_installer_badges_as_applied(xiii):
    xiii.outside(N910)

    answer = xiii.survey()

    assert answer["verified"], answer
    assert answer["applied"] == ["npcs"], answer


def test_hd_from_outside_isnt_behind_a_pipeline_it_was_never_built_by(xiii, monkeypatch):
    """a missing stamp means an install of ours from before stamps, not one of theirs"""
    from patcher.mods import hd_state

    monkeypatch.setattr(hd_state, "PIPELINE", {"npcs": 2})
    xiii.outside(N910)

    answer = xiii.survey()

    assert answer["applied"] == ["npcs"] and answer["suspect"] == {}, answer


def test_a_vanilla_game_with_no_backup_reads_as_vanilla_not_unknown(xiii):
    answer = xiii.survey()

    assert answer["verified"] and answer["applied"] == [], answer


def test_a_first_install_over_hd_from_outside_says_so_and_keeps_it(xiii):
    xiii.outside(N910)

    ending, lines = xiii.install(["weapons"])

    assert ending["stage"] == "done", ending
    said = [e for e in lines if "already in your game from outside" in e["text"]]
    assert said and "NPCs is already in" in said[0]["text"], [e["text"] for e in lines]
    # a fact about the backup now, not an alarm: nothing goes HD over HD
    assert said[0].get("stream") == "note"
    # the backup is of the game as it was, NPCs in, and the survey still sees them
    assert set(xiii.survey()["applied"]) == {"npcs", "weapons"}
    assert set(xiii.api.hd_categories("ff13", "hdproject")["installed"]) == {"npcs", "weapons"}


def test_rebuilding_hd_from_outside_with_nothing_clean_refuses_before_anything(xiii):
    xiii.outside(N910)
    before = {p: p.read_bytes() for p in xiii.pair()}

    ending, _ = xiii.install(["npcs"])

    assert ending["stage"] == "failed", ending
    assert "already modded in your game" in ending["error"], ending
    assert "Nothing has been changed" in ending["error"]
    assert {p: p.read_bytes() for p in xiii.pair()} == before
    backup = xiii.game / ".patcher-undo" / "hdproject"
    assert not any(backup.glob("*.win32.bin")), "it backed up the game it then refused"


def test_the_mods_own_cache_of_originals_rebuilds_it_clean(xiii):
    """their .bat leaves models\\orig behind, filled from the game before it went in"""
    xiii.outside(N910)
    vanilla = {p: s for p, s in hd_vanilla.table(XIII).items() if p in N910}
    for path, size in vanilla.items():
        cached = xiii.mod / "models" / "orig" / path
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"V" * size)

    ending, _ = xiii.install(["npcs"])

    assert ending["stage"] == "done", ending
    for path, size in vanilla.items():
        # the stub tools add 6002 bytes to whatever original they're handed
        assert xiii.sizes()[path] == size + 6002, "HD art built over HD art"


def test_a_cache_filled_from_a_modded_game_is_cached_again_clean(xiii):
    """
    their installer refills models\\orig from the live game when it's missing,
    so after a Verify it can hold HD copies of what's now vanilla in the game
    """
    vanilla = {p: s for p, s in hd_vanilla.table(XIII).items() if p in N910}
    for path, size in vanilla.items():
        cached = xiii.mod / "models" / "orig" / path
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"V" * (size + 4096))

    ending, lines = xiii.install(["npcs"])

    assert ending["stage"] == "done", ending
    for path, size in vanilla.items():
        assert (xiii.mod / "models" / "orig" / path).stat().st_size == size
        assert xiii.sizes()[path] == size + 6002, "HD art built over HD art"
    assert any("cached again clean" in e["text"] for e in lines)


def test_an_id_shared_with_hd_from_outside_is_left_as_it_is(xiii, monkeypatch):
    """the lists overlap, so a category ticked fresh can hold an id already HD"""
    from tests.helpers import index_entries, write_scr_pair

    (xiii.data / "LoadHDOther.txt").write_text("#:Misc\nn n910\nw w777\n", encoding="utf-8")
    index, archive = xiii.pair()
    write_scr_pair(index, archive, index_entries(xiii.data))
    tables(monkeypatch, dict(hd_vanilla.index_sizes(index)))
    vanilla = dict(hd_vanilla.table(XIII))
    xiii.outside(N910)

    ending, lines = xiii.install(["misc"])

    assert ending["stage"] == "done", ending
    assert any("n910 already modded in your game" in e["text"] for e in lines), \
        [e["text"] for e in lines]
    sizes = xiii.sizes()
    assert sizes[N910[0]] == vanilla[N910[0]] + 4096, "built over the HD that was there"
    w777 = "chr/weapon/w777/bin/w777.win32.trb"
    assert sizes[w777] == vanilla[w777] + 6002, "the rest of the category wasn't built"


def test_a_restore_says_what_the_backup_still_carries(xiii):
    xiii.outside(N910)
    xiii.install(["weapons"])
    xiii.events.clear()

    result = xiii.api.restore_hd_project("ff13")

    assert result["ok"], result
    said = [e["text"] for e in xiii.events if e.get("stage") == "line"]
    assert any("already modded before this app's first install" in t for t in said), said


def test_revert_says_what_the_backup_still_carries(xiii, monkeypatch):
    from patcher.manifest import load_manifest
    from patcher.revert import _revert_hd

    xiii.outside(N910)
    xiii.install(["weapons"])

    result = _revert_hd(xiii.game, load_manifest(REPO / "manifests" / "ffxiii.json"),
                        xiii.game.parent / "state.json")

    assert result.ok, result
    assert "already modded before this app's first install" in result.detail, result.detail


class Xiii2:
    def __init__(self, tmp_path, monkeypatch):
        from tests.test_hd2_end_to_end import build_world

        self.monkeypatch = monkeypatch
        self.api, self.events, self.game, self.mod = build_world(tmp_path, monkeypatch)
        self.index = self.game / "alba_data" / "sys" / "filelistu.win32.bin"
        zone_dir = self.game / "alba_data" / "zone"
        tables(monkeypatch, dict(hd_vanilla.index_sizes(self.index)),
               {p.name: p.stat().st_size for p in zone_dir.glob("white_z*_img.win32.bin")})

    def install(self, selected, mode="add"):
        from tests.test_hd2_end_to_end import run_and_wait
        return run_and_wait(self.api, self.events, self.mod, selected, mode=mode)

    def survey(self):
        return self.api.hd_survey("ff13-2", "hdproject2", str(self.mod))


@pytest.fixture
def xiii2(tmp_path, monkeypatch):
    return Xiii2(tmp_path, monkeypatch)


def test_xiii2_reads_hd_from_outside_with_no_backup(xiii2):
    """
    their .bat installs everything at once, and with no backup and no record of
    what a run wrote, a category goes by how much of its group has grown
    """
    from tests.hd2_world import entry_bytes, rewrite_entries

    rewrite_entries(xiii2.game, {"chr/weapon/w101/bin/w101.win32.imgb":
                                 entry_bytes(xiii2.game, "chr/weapon/w101/bin/w101.win32.imgb")
                                 + b"HD" * 900})
    blob = xiii2.game / "alba_data" / "zone" / "white_z0010u_img.win32.bin"
    with blob.open("ab") as handle:
        handle.write(b"\x00" * 4096)

    answer = xiii2.survey()

    assert answer["verified"], answer
    assert set(answer["applied"]) == {"weapons", "zones"}, answer


def test_xiii2_rebuilding_hd_from_outside_with_nothing_clean_refuses(xiii2):
    from tests.hd2_world import entry_bytes, rewrite_entries

    path = "chr/weapon/w101/bin/w101.win32.imgb"
    rewrite_entries(xiii2.game, {path: entry_bytes(xiii2.game, path) + b"HD" * 900})
    before = xiii2.index.read_bytes()

    ending, _ = xiii2.install(["weapons"])

    assert ending["stage"] == "failed", ending
    assert "already modded in your game" in ending["error"], ending
    assert xiii2.index.read_bytes() == before


def test_xiii2_a_first_install_over_hd_from_outside_says_so(xiii2):
    from tests.hd2_world import entry_bytes, rewrite_entries

    path = "chr/weapon/w101/bin/w101.win32.imgb"
    rewrite_entries(xiii2.game, {path: entry_bytes(xiii2.game, path) + b"HD" * 900})

    ending, hd = xiii2.install(["enemies"])

    assert ending["stage"] == "done", ending
    said = [e["text"] for e in hd if e.get("stage") == "line" and "[backup]" in e["text"]]
    assert any("Weapons is already in your game from outside this app" in t for t in said), said
    assert set(xiii2.survey()["applied"]) == {"weapons", "enemies"}


def test_the_tools_hashes_go_to_the_run_log_not_the_pane(xiii):
    """
    which build of each tool a run trusted is for reading a run that went
    strange, and on the terminal it was a line of hex in the middle of an
    install, so it's the run log's alone
    """
    ending, lines = xiii.install(["weapons"])
    assert ending["stage"] == "done", ending
    assert not [e for e in lines if e.get("stream") == "log" or "sha256" in e["text"]], lines
    started = next(e for e in xiii.events if e.get("stage") == "started")
    assert started.get("log"), started
    assert " sha256 " in Path(started["log"]).read_text(encoding="utf-8")
