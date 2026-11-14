"""
A mod from the Mods drawer and the HD Project in the same game: the survey
doesn't take one for the other, an HD install leaves what the drawer put in
and says whose it is, and the drawer's mod still comes off after a Revert.
"""

from pathlib import Path

import pytest

from tests.test_first_press import N910, Xiii

W001 = ("chr/weapon/w001/bin/w001.win32.trb", "chr/weapon/w001/bin/w001.win32.imgb")


@pytest.fixture
def xiii(tmp_path, monkeypatch):
    return Xiii(tmp_path, monkeypatch)


def drop(xiii, tmp_path, name, files) -> dict:
    root = tmp_path / name
    for member, data in files.items():
        (root / member).parent.mkdir(parents=True, exist_ok=True)
        (root / member).write_bytes(data)
    return xiii.api.install_mod("ff13", str(root))


def bigger(xiii, paths) -> dict:
    sizes = xiii.sizes()
    return {p: b"B" * (sizes[p] + 5000) for p in paths}


def test_a_dropped_model_doesnt_read_as_the_hd_category_it_sits_in(xiii, tmp_path):
    assert drop(xiii, tmp_path, "Big NPC", bigger(xiii, N910))["ok"]

    answer = xiii.survey()
    assert answer["verified"] and answer["applied"] == [], answer


def test_hd_over_nothing_but_a_dropped_model_says_whose_it_is(xiii, tmp_path):
    drop(xiii, tmp_path, "Big NPC", bigger(xiii, N910))
    before = {p: p.read_bytes() for p in xiii.pair()}

    ending, _ = xiii.install(["npcs"])

    assert ending["stage"] == "failed", ending
    assert "Big NPC from the Mods drawer" in ending["error"], ending
    assert "Remove it there" in ending["error"]
    assert {p: p.read_bytes() for p in xiii.pair()} == before


@pytest.fixture
def xiii2(tmp_path, monkeypatch):
    from tests.test_first_press import Xiii2
    return Xiii2(tmp_path, monkeypatch)


RAIN = "vfx/field/rain.win32.imgb"


def test_a_dropped_file_stays_removable_through_an_hd_install(xiii2, tmp_path):
    """XIII-2's stub ff13tool repacks what the index points at, the way the real one does"""
    from tests.hd2_world import entry_bytes

    root = tmp_path / "Rain Fix"
    (root / "vfx" / "field").mkdir(parents=True)
    (root / RAIN).write_bytes(b"rain" * 900)
    vanilla = entry_bytes(xiii2.game, RAIN)
    assert xiii2.api.install_mod("ff13-2", str(root))["ok"]

    ending, _ = xiii2.install(["weapons"])
    assert ending["stage"] == "done", ending
    assert entry_bytes(xiii2.game, RAIN) == b"rain" * 900
    listed = {m["name"]: m for m in xiii2.api.list_addons("ff13-2")["mods"]}
    assert listed["Rain Fix"]["state"] == "in", listed

    weapons = entry_bytes(xiii2.game, "chr/weapon/w101/bin/w101.win32.imgb")
    assert xiii2.api.remove_addon("ff13-2", "Rain Fix", "files")["ok"]
    assert entry_bytes(xiii2.game, RAIN) == vanilla
    assert entry_bytes(xiii2.game, "chr/weapon/w101/bin/w101.win32.imgb") == weapons


def test_a_mod_taken_off_after_the_hd_backup_doesnt_come_back_with_a_revert(xiii2, tmp_path):
    """
    the backup was taken with it in, so putting the backup back puts it back
    too, and that mod was taken off on purpose
    """
    from patcher.mods import game_files

    from tests.hd2_world import entry_bytes

    sys_dir = xiii2.game / "alba_data" / "sys"
    vanilla = {p.name: p.read_bytes() for p in sys_dir.glob("*u.win32.bin")}
    rain = entry_bytes(xiii2.game, RAIN)
    root = tmp_path / "Rain Fix"
    (root / "vfx" / "field").mkdir(parents=True)
    (root / RAIN).write_bytes(b"rain" * 900)
    xiii2.api.install_mod("ff13-2", str(root))
    ending, _ = xiii2.install(["weapons"])
    assert ending["stage"] == "done", ending
    assert xiii2.api.remove_addon("ff13-2", "Rain Fix", "files")["ok"]
    # out of the game, and out of the list, with what a restore would bring back kept
    assert not [m for m in xiii2.api.list_addons("ff13-2")["mods"] if m.get("kind") == "files"]
    assert [r.get("off") for r in game_files.records(xiii2.game / ".patcher-undo")] == [True]

    xiii2.events.clear()
    assert xiii2.api.restore_hd_project("ff13-2")["ok"]

    said = [e["text"] for e in xiii2.events if e.get("stage") == "line"]
    assert any("Rain Fix came back with the HD backup" in s for s in said), said
    # the index as it was; the archive keeps the mod's bytes past its end,
    # unreferenced, since the backup still has it in and its place stays taken
    assert (sys_dir / "filelistu.win32.bin").read_bytes() == vanilla["filelistu.win32.bin"]
    assert entry_bytes(xiii2.game, RAIN) == rain


def read(xiii, path):
    from patcher.mods import game_files
    from patcher.mods.hd_zones import read_record
    index, archive = xiii.pair()
    return read_record(archive, game_files.entries(index)[path])


def names(xiii):
    return [m["name"] for m in xiii.api.list_addons("ff13")["mods"] if m.get("kind") == "files"]


def test_a_mod_dropped_after_an_hd_install_goes_on_top_and_stays_through_a_restore(xiii, tmp_path):
    ending, _ = xiii.install(["weapons"])
    assert ending["stage"] == "done", ending
    hd = {p: p.read_bytes() for p in xiii.pair()}

    assert drop(xiii, tmp_path, "Big NPC", bigger(xiii, N910))["ok"]
    assert xiii.api.remove_addon("ff13", "Big NPC", "files")["ok"]
    assert {p.name: p.read_bytes() for p in xiii.pair()}[xiii.pair()[0].name] == hd[xiii.pair()[0]]

    mod = bigger(xiii, N910)
    drop(xiii, tmp_path, "Big NPC", mod)
    xiii.events.clear()
    assert xiii.api.restore_hd_project("ff13")["ok"]
    said = [e["text"] for e in xiii.events if e.get("stage") == "line"]
    # the backup predates it, so it went back on top of vanilla rather than out
    assert any("Big NPC went back on top" in s for s in said), said
    assert names(xiii) == ["Big NPC"]
    assert {p: read(xiii, p) for p in N910} == mod
    # and Remove still takes it off, down to the vanilla it went back over
    assert xiii.api.remove_addon("ff13", "Big NPC", "files")["ok"]
    assert {p: p.read_bytes() for p in xiii.pair()} == xiii.vanilla


def test_reinstalling_hd_keeps_the_drawers_mods_on_top_in_their_order(xiii, tmp_path):
    """
    a repair rebuilds from the backup, which has none of what the drawer put in
    since. Those went out with it and the message said to drop them again;
    they're lifted first and laid back in the order they first went in
    """
    ending, _ = xiii.install(["weapons"])
    assert ending["stage"] == "done", ending
    first, second = bigger(xiii, N910), {p: b"C" * len(d) for p, d in bigger(xiii, N910).items()}
    assert drop(xiii, tmp_path, "Big NPC", first)["ok"]
    assert drop(xiii, tmp_path, "Bigger NPC", second)["ok"]

    ending, lines = xiii.install(["weapons"])

    assert ending["stage"] == "done", ending
    said = [line["text"] for line in lines]
    assert any("Big NPC and Bigger NPC went back on top" in s for s in said), said
    assert not any("Drop it on the Mods drawer again" in s for s in said), said
    assert names(xiii) == ["Big NPC", "Bigger NPC"]
    # the newer one on top, the older under it, as they were stacked
    assert {p: read(xiii, p) for p in N910} == second
    assert xiii.api.remove_addon("ff13", "Bigger NPC", "files")["ok"]
    assert {p: read(xiii, p) for p in N910} == first


def test_a_mod_dropped_into_one_voice_set_isnt_carried_into_the_other_as_hd(tmp_path, monkeypatch):
    """the voice copy carries what HD changed, and a file from the drawer isn't HD's"""
    from patcher.mods import game_files, hd_mirror
    from tests.hd2_world import entry_bytes
    from tests.test_hd_mirror import build, copy

    game = build(tmp_path, monkeypatch, selected={"weapons"})
    path = "chr/npc/n012/bin/n012.win32.imgb"
    japanese = entry_bytes(game, path, "c")
    root = tmp_path / "Only English"
    (root / "white_imgu" / "chr" / "npc" / "n012" / "bin").mkdir(parents=True)
    (root / "white_imgu" / path).write_bytes(b"EN" * 2000)
    with game_files.Source(root) as source:
        found = game_files.look(source.members(), game_files.read_game(game), source.read)
        assert game_files.install(game, game / ".patcher-undo", "Only English", found, source)["ok"]

    assert path not in hd_mirror.lacking(game, "u", "c").files
    copy(game)
    assert entry_bytes(game, path, "c") == japanese
    assert entry_bytes(game, "chr/weapon/w101/bin/w101.win32.imgb", "c") == \
        entry_bytes(game, "chr/weapon/w101/bin/w101.win32.imgb", "u")


def test_hd_with_a_clean_backup_still_leaves_a_dropped_model_alone(xiii, tmp_path):
    """the costume is what somebody put in on purpose; Remove is how it makes way"""
    ending, _ = xiii.install(["weapons"])
    assert ending["stage"] == "done", ending
    big = bigger(xiii, N910)
    assert drop(xiii, tmp_path, "Big NPC", big)["ok"]

    ending, _ = xiii.install(["npcs"])

    assert ending["stage"] == "failed", ending
    assert "Big NPC from the Mods drawer" in ending["error"], ending
    assert all(xiii.sizes()[p] == len(big[p]) for p in N910)
    listed = {m["name"]: m for m in xiii.api.list_addons("ff13")["mods"]}
    assert listed["Big NPC"]["state"] == "in", listed


def with_characters(xiii):
    """the world's mod with XIII's real Playable Characters list, and the game's index with them in"""
    from tests.helpers import index_entries, write_scr_pair

    listing = Path(__file__).parent / "data" / "hd" / "LoadHDPCs.tx1"
    (xiii.data / "LoadHDPCs.tx1").write_bytes(listing.read_bytes())
    index, archive = xiii.pair()
    write_scr_pair(index, archive, index_entries(xiii.data), size=2048)


PEOPLE = ("Lightning", "Snow", "Vanille", "Sazh", "Hope", "Fang")


def models(n, field=True, battle=True) -> list:
    ids = ([f"c{n:03d}"] if field else []) + ([f"c{200 + n}"] if battle else [])
    return [f"chr/pc/{i}/bin/{i}.win32.{s}" for i in ids for s in ("trb", "imgb")]


def test_hd_models_plus_from_the_drawer_shows_playable_characters_as_in(xiii, tmp_path):
    """
    HD Models Plus first, a file per person, then the HD Project: the party
    is in already, and an install leaves a model the drawer holds as it is.
    XIII lists 34 ids for the six of them, so most of the ids never got there
    """
    with_characters(xiii)
    for n, person in enumerate(PEOPLE, 1):
        assert drop(xiii, tmp_path, f"HD Models Plus - {person}", bigger(xiii, models(n)))["ok"]

    answer = xiii.survey()

    assert answer["verified"], answer
    assert answer["covered"] == {"pcs": sorted(f"HD Models Plus - {p}" for p in PEOPLE)}
    # the HD Project's own record of what it put in stays its own, which is
    # what the voice copy and the debts go by
    assert answer["applied"] == [], answer


def test_a_person_in_battle_alone_is_still_in(xiii, tmp_path):
    with_characters(xiii)
    for n, person in enumerate(PEOPLE, 1):
        assert drop(xiii, tmp_path, person, bigger(xiii, models(n, field=False)))["ok"]
    assert list(xiii.survey()["covered"]) == ["pcs"]


def test_five_of_the_six_leave_the_category_its_sixth(xiii, tmp_path):
    with_characters(xiii)
    for n, person in enumerate(PEOPLE[:5], 1):
        assert drop(xiii, tmp_path, person, bigger(xiii, models(n)))["ok"]
    assert xiii.survey()["covered"] == {}


def test_the_party_from_its_own_installer_counts_with_no_names(xiii, monkeypatch):
    """HD Models Plus's .bat, outside the drawer: in all the same, nobody to name"""
    from patcher.mods import hd_vanilla
    from tests.test_first_press import tables

    with_characters(xiii)
    # the characters as Steam ships them, which the world's table was made before
    tables(monkeypatch, dict(hd_vanilla.index_sizes(xiii.index)))
    xiii.outside([p for n in range(1, 7) for p in models(n)])
    answer = xiii.survey()
    assert answer["covered"] == {"pcs": []} and answer["applied"] == [], answer


def test_the_survey_answers_the_rest_when_coverage_cant_be_told(xiii, tmp_path, monkeypatch):
    from patcher.mods import hd_state

    def broken(*args, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr(hd_state, "covered_categories", broken)
    with_characters(xiii)
    answer = xiii.survey()
    assert answer["verified"] and "voices" in answer and "covered" not in answer, answer


def test_only_a_category_of_models_counts_what_the_drawer_has(xiii, tmp_path):
    """a costume over an NPC says nothing about the NPCs' HD textures"""
    assert drop(xiii, tmp_path, "Big NPC", bigger(xiii, N910))["ok"]
    assert xiii.survey()["covered"] == {}


def test_the_hd_project_is_listed_under_installed_and_its_remove_is_the_revert(xiii, tmp_path):
    def rows():
        return [m for m in xiii.api.list_addons("ff13")["mods"] if m.get("kind") == "hd"]

    assert rows() == []
    ending, _ = xiii.install(["weapons"])
    assert ending["stage"] == "done", ending
    (row,) = rows()
    every = len(xiii.api.hd_categories("ff13", "hdproject")["categories"])
    assert row["title"] == row["name"] and "HD" in row["title"]
    assert row["tag"] == f"1 of {every} parts" and row["managed"]

    assert xiii.api.restore_hd_project("ff13")["ok"]
    assert rows() == []
    assert {p: p.read_bytes() for p in xiii.pair()} == xiii.vanilla


def test_a_drawer_mod_by_nexus_s_newer_name_lists_clean_with_its_version(xiii, tmp_path):
    raw = "Less Grinding-73-1-3-2026-08-27T12-00-00Z"
    assert drop(xiii, tmp_path, raw, bigger(xiii, N910))["ok"]
    (row,) = [m for m in xiii.api.list_addons("ff13")["mods"] if m.get("kind") == "files"]
    assert (row["title"], row["tag"]) == ("Less Grinding", "v1.3"), row
    # its next version is the same mod, not a second row
    assert drop(xiii, tmp_path, "Less Grinding-73-1-4-2026-09-30T08-00-00Z",
                bigger(xiii, N910))["ok"]
    rows = [m for m in xiii.api.list_addons("ff13")["mods"] if m.get("kind") == "files"]
    assert [(m["title"], m["tag"]) for m in rows] == [("Less Grinding", "v1.4")], rows


def test_one_that_went_in_under_its_whole_stamped_name_is_updated_by_the_plain_one(
        xiii, tmp_path, monkeypatch):
    """what earlier builds called it, id, version, time and all, is still the one mod"""
    from patcher.mods import game_files

    real = game_files.mod_name
    monkeypatch.setattr(game_files, "mod_name", lambda origin: Path(origin).name)
    assert drop(xiii, tmp_path, "Less Grinding 73 1.3 2026-08-27T12-00-00", bigger(xiii, N910))["ok"]
    assert [m["name"] for m in xiii.api.list_addons("ff13")["mods"]] == [
        "Less Grinding 73 1.3 2026-08-27T12-00-00"]
    monkeypatch.setattr(game_files, "mod_name", real)
    assert drop(xiii, tmp_path, "Less Grinding 73 1.4 2026-09-30T08-00-00", bigger(xiii, N910))["ok"]
    rows = [m for m in xiii.api.list_addons("ff13")["mods"] if m.get("kind") == "files"]
    assert [(m["name"], m["title"], m["tag"]) for m in rows] == [
        ("Less Grinding", "Less Grinding", "v1.4")], rows


def test_the_hd_row_says_when_the_4gb_patch_is_off(xiii, tmp_path):
    from patcher.patch_ops import flip_laa_flag, is_laa_enabled
    from tests.helpers import make_fake_pe

    exe = xiii.game / "white_data" / "prog" / "win" / "bin" / "ffxiiiimg.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    plain = make_fake_pe()
    assert not is_laa_enabled(plain)
    exe.write_bytes(plain)
    ending, _ = xiii.install(["weapons"])
    assert ending["stage"] == "done", ending

    def warned():
        (row,) = [m for m in xiii.api.list_addons("ff13")["mods"] if m.get("kind") == "hd"]
        return row.get("warn", "")

    assert warned() == "The 4GB Patch is off, and this needs the memory it gives the game"
    exe.write_bytes(flip_laa_flag(plain))
    assert warned() == ""
