"""
Leviathan's Tears and FF XIII-2 HD on one game.

Both write white_img. So they take turns, and Revert and Rebuild have to know
which of them the HD backup is older than: whatever the backup holds an older
copy of comes off with it, and a journal that no longer fits is never what
decides the ending.
"""

import time
from pathlib import Path

import pytest

from patcher.engine import PatchStatus
from patcher.state import load_state
from tests.conftest import Log
from tests.hd2_world import categories, entry_bytes, make_game, make_mod
from tests.rain import PAYLOAD
from tests.test_revert import manifest

REPO = Path(__file__).resolve().parent.parent
LT_ID = "install_leviathans_tears"
WEATHER = ["vfx/field/weather/weather07/veffs.jp.win32.imgb",
           "vfx/field/weather/weather08/veffs.jp.win32.imgb"]


def weather():
    """the two files it replaces, shaped like the real ones: 0x16180 inflated"""
    return [(path, bytes(reversed(PAYLOAD))[:0x16180]) for path in WEATHER]


def game_files(game, variants=("u",)):
    sys_dir = game / "alba_data" / "sys"
    return {p: p.read_bytes() for v in variants
            for p in (sys_dir / f"filelist{v}.win32.bin", sys_dir / f"white_img{v}.win32.bin")}


@pytest.fixture
def world(tmp_path, monkeypatch):
    def build(variants=("u",)):
        for variant in variants:
            game = make_game(tmp_path, variant=variant, zones=(), extra=weather())
        root = make_mod(tmp_path, monkeypatch)
        monkeypatch.setattr("patcher.mods.hd_install.free_space", lambda p: 500 * 1024 ** 3)
        monkeypatch.setattr("patcher.download.free_space", lambda p: 500 * 1024 ** 3)
        return game, root, tmp_path / "state.json"
    return build


def hd(game, root, selected):
    from patcher.mods import hd2_install
    from patcher.mods.hd2_recipe import detect

    result = hd2_install.install(game, detect(root), categories(), set(selected),
                                 game / ".patcher-undo", on_line=Log())
    assert result.ok, result.errors
    # applied_at and the backup's clock are compared, so they must not share a tick
    time.sleep(0.02)


def apply(game, state, ids):
    """Apply as the window runs it, reading what earlier runs recorded"""
    from patcher.engine import run_patches
    from patcher.state import record_applied, record_reader

    return run_patches(
        game, manifest(), ids,
        already_applied_check=lambda pid: pid == "install_novafix",
        on_applied=lambda r: record_applied(state, r.patch_id, r.installed_files,
                                            r.revert_info),
        recorded=record_reader(state),
        fetch=lambda u: (_ for _ in ()).throw(OSError("offline")))


def lt(game, state):
    result = next(r for r in apply(game, state, {LT_ID}) if r.patch_id == LT_ID)
    assert result.status == PatchStatus.APPLIED, result.detail
    time.sleep(0.02)
    return result


def revert(game, state):
    from patcher.revert import revert_all
    return revert_all(game, manifest(), state)


def test_revert_after_hd_then_leviathans_tears_then_hd_again(world):
    """the second HD run rewrites white_img under the journal Leviathan's Tears kept"""
    game, root, state = world()
    vanilla = game_files(game)
    hd(game, root, {"weapons"})
    lt(game, state)
    hd(game, root, {"enemies"})
    assert entry_bytes(game, WEATHER[0]) == PAYLOAD, "the HD run lost it"

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game) == vanilla
    assert LT_ID not in load_state(state)["applied_patches"]
    assert all(r.ok for r in revert(game, state)), "a second revert found something left"


def test_revert_takes_the_voice_set_the_backup_never_held_off_by_its_own_undo(world):
    """it patches both voice sets, and the HD backup only holds the one the game plays"""
    game, root, state = world(variants=("u", "c"))
    vanilla = game_files(game, "uc")
    hd(game, root, {"weapons"})
    lt(game, state)
    hd(game, root, {"enemies"})

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game, "uc") == vanilla
    assert LT_ID not in load_state(state)["applied_patches"]


def test_revert_after_hd_then_leviathans_tears(world):
    game, root, state = world()
    vanilla = game_files(game)
    hd(game, root, {"weapons"})
    lt(game, state)

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game) == vanilla
    assert LT_ID not in load_state(state)["applied_patches"]


def test_leviathans_tears_before_the_backup_comes_back_with_it_and_then_off(world):
    game, root, state = world()
    vanilla = game_files(game)
    lt(game, state)
    hd(game, root, {"weapons"})

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game) == vanilla
    assert LT_ID not in load_state(state)["applied_patches"]


def test_a_revert_that_cannot_restore_leaves_leviathans_tears_recorded(world, monkeypatch):
    """its files never went back, so it is still in them and still has to come off"""
    from patcher.mods import hd_install

    game, root, state = world()
    hd(game, root, {"weapons"})
    lt(game, state)
    hd(game, root, {"enemies"})
    before = game_files(game)

    def held(paths):
        return [(Path(p).name, "in use") for p in paths]

    free = hd_install.in_use
    monkeypatch.setattr(hd_install, "in_use", held)
    results = {r.patch_id: r for r in revert(game, state)}

    assert not results["hd_texture_pack"].ok
    assert not results[LT_ID].ok and "Nothing has been changed" in results[LT_ID].detail
    assert game_files(game) == before
    assert LT_ID in load_state(state)["applied_patches"]

    monkeypatch.setattr(hd_install, "in_use", free)
    assert all(r.ok for r in revert(game, state))
    assert LT_ID not in load_state(state)["applied_patches"]


def test_leviathans_tears_waits_for_the_hd_install(world):
    """XIII-2's install rewrites white_img and its index, and a patch landing mid-run is lost"""
    from patcher.mods.hd_install import exclusive

    game, root, state = world()
    before = game_files(game)

    with exclusive(game / ".patcher-undo"):
        result = next(r for r in apply(game, state, {LT_ID}) if r.patch_id == LT_ID)

    assert result.status == PatchStatus.FAILED
    assert "Nothing has been changed" in result.detail, result.detail
    assert game_files(game) == before
    assert LT_ID not in load_state(state)["applied_patches"]


def test_a_revert_of_it_waits_for_the_hd_install_too(world):
    from patcher.mods.hd_install import exclusive
    from patcher.revert import revert_patch

    game, root, state = world()
    lt(game, state)
    before = game_files(game)
    patch = next(p for p in manifest().patches if p.id == LT_ID)

    with exclusive(game / ".patcher-undo"):
        result = revert_patch(game, manifest(), patch, state)

    assert not result.ok and "Nothing has been changed" in result.detail, result.detail
    assert game_files(game) == before
    assert revert_patch(game, manifest(), patch, state).ok


def test_a_rebuild_says_leviathans_tears_came_off_and_forgets_it(tmp_path, monkeypatch):
    from tests.test_hd2_end_to_end import build_world, run_and_wait

    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    state = tmp_path / "state" / "ffxiii2.json"
    assert run_and_wait(api, events, mod, ["weapons"])[0]["stage"] == "done"
    time.sleep(0.02)
    lt(game, state)

    ending, lines = run_and_wait(api, events, mod, ["weapons"], mode="set")

    assert ending["stage"] == "done", ending
    said = [e["text"] for e in lines if e.get("stage") == "line"]
    assert any("Leviathan's Tears came off" in line for line in said), said
    assert entry_bytes(game, WEATHER[0]) != PAYLOAD
    assert LT_ID not in load_state(state)["applied_patches"]


def voice(game, mode):
    """the launcher's voice setting, which picks the voice set an install writes"""
    (game / "setup.xml").write_bytes(
        b"<SaveData>\r\n<voice_mode>%d</voice_mode>\r\n</SaveData>\r\n" % mode)


def has_lt(game, variant):
    return entry_bytes(game, WEATHER[0], variant) == PAYLOAD


def hd_both_sides(world):
    """HD in English, then Leviathan's Tears in both, then HD in Japanese"""
    game, root, state = world(variants=("u", "c"))
    vanilla = game_files(game, "uc")
    voice(game, 1)
    hd(game, root, {"weapons"})
    lt(game, state)
    voice(game, 0)
    hd(game, root, {"weapons"})
    assert has_lt(game, "u") and has_lt(game, "c")
    return game, state, vanilla


def test_revert_goes_by_what_each_voice_set_s_copy_holds(world):
    """
    each pair is backed up the first time an install writes that voice set, so
    the English copy is older than Leviathan's Tears and the Japanese one holds it
    """
    game, state, vanilla = hd_both_sides(world)

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game, "uc") == vanilla
    assert LT_ID not in load_state(state)["applied_patches"]


def test_a_restore_of_the_set_backed_up_after_it_leaves_it_where_it_is(world):
    from patcher.mods.hd_install import restore, restore_prep
    from patcher.revert import settle_after_restore

    game, state, vanilla = hd_both_sides(world)

    put_back = restore(restore_prep(game, game / ".patcher-undo"), variant="c")

    assert settle_after_restore(game, manifest(), state, put_back) == []
    assert has_lt(game, "u") and has_lt(game, "c")
    assert LT_ID in load_state(state)["applied_patches"]
    assert all(r.ok for r in revert(game, state))
    assert game_files(game, "uc") == vanilla


def test_its_own_undo_failing_on_the_rest_keeps_the_rest_recorded(world):
    """what went back with the backup is forgotten either way, and the message says both"""
    game, root, state = world(variants=("u", "c"))
    hd(game, root, {"weapons"})
    lt(game, state)
    hd(game, root, {"enemies"})
    # something else has rewritten the Japanese set since
    with (game / "alba_data" / "sys" / "white_imgc.win32.bin").open("ab") as handle:
        handle.write(b"somebody else's mod" * 99)

    result = next(r for r in revert(game, state) if r.patch_id == LT_ID)

    assert not result.ok
    assert result.detail.startswith("came off the files the HD restore put back"), result.detail
    assert "Nothing has been changed" not in result.detail
    left = load_state(state)["applied_patches"][LT_ID]
    assert left["journals"] and all("white_imgc" in j for j in left["journals"])


@pytest.mark.parametrize("voices", [True, False])
def test_a_rebuild_takes_it_off_the_other_voice_set_too(tmp_path, monkeypatch, voices):
    """
    half a patch with a record saying all of it is how its undo gets lost
    later. With the voices box ticked both sets go back to vanilla and the copy
    puts HD in Japanese again without what came off; without it Japanese keeps
    its HD and the patch comes off it by its own undo
    """
    from tests.test_hd2_end_to_end import build_world, run_and_wait

    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    make_game(game.parent, variant="c", zones=(), extra=weather())
    state = tmp_path / "state" / "ffxiii2.json"
    assert run_and_wait(api, events, mod, ["weapons"], voices=True)[0]["stage"] == "done"
    time.sleep(0.02)
    lt(game, state)
    assert has_lt(game, "c")

    ending, lines = run_and_wait(api, events, mod, ["weapons"], mode="set", voices=voices)

    assert ending["stage"] == "done", ending
    assert not has_lt(game, "u") and not has_lt(game, "c")
    weapon = "chr/weapon/w101/bin/w101.win32.imgb"
    assert b"HD" in entry_bytes(game, weapon, "c")
    assert entry_bytes(game, weapon, "c") == entry_bytes(game, weapon, "u")
    assert LT_ID not in load_state(state)["applied_patches"]


def test_a_rebuild_that_fails_after_taking_it_off_says_so(tmp_path, monkeypatch):
    from patcher.mods import hd2_install
    from tests.test_hd2_end_to_end import build_world, run_and_wait

    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    state = tmp_path / "state" / "ffxiii2.json"
    assert run_and_wait(api, events, mod, ["weapons"])[0]["stage"] == "done"
    time.sleep(0.02)
    lt(game, state)
    monkeypatch.setattr(hd2_install, "chr_steps", lambda *a, **k: [])

    ending, _ = run_and_wait(api, events, mod, ["weapons"], mode="set")

    assert ending["stage"] == "failed", ending
    assert "removal of Leviathan's Tears" in (ending.get("note") or ""), ending


def test_a_backup_holding_an_earlier_application_is_judged_by_what_it_holds(world):
    """
    the backup is taken once and kept, so its copy can be older than this
    application of Leviathan's Tears and still hold the one before it
    """
    game, root, state = world()
    vanilla = game_files(game)
    lt(game, state)
    hd(game, root, {"weapons"})
    assert all(r.ok for r in revert(game, state))
    lt(game, state)
    hd(game, root, {"weapons"})

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game) == vanilla
    assert LT_ID not in load_state(state)["applied_patches"]


def test_a_revert_never_leaves_it_in_the_backup_for_the_next_one(world):
    game, root, state = world()
    vanilla = game_files(game)
    lt(game, state)
    hd(game, root, {"weapons"})
    assert all(r.ok for r in revert(game, state))
    assert game_files(game) == vanilla
    hd(game, root, {"weapons"})

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game) == vanilla, "the backup brought it back"


def test_a_copy_holding_it_under_an_undo_that_no_longer_fits_is_said_and_kept(world):
    """something else rewrote white_img between Leviathan's Tears and the backup"""
    game, root, state = world()
    lt(game, state)
    with (game / "alba_data" / "sys" / "white_imgu.win32.bin").open("ab") as handle:
        handle.write(b"\0" * 4096)
    hd(game, root, {"weapons"})

    result = next(r for r in revert(game, state) if r.patch_id == LT_ID)

    assert not result.ok and "cannot be taken off safely" in result.detail, result.detail
    assert has_lt(game, "u")
    assert LT_ID in load_state(state)["applied_patches"]


def test_a_rebuild_keeps_it_when_the_copy_that_went_back_holds_it(tmp_path, monkeypatch):
    from tests.test_hd2_end_to_end import build_world, run_and_wait

    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    state = tmp_path / "state" / "ffxiii2.json"
    lt(game, state)
    assert run_and_wait(api, events, mod, ["weapons"])[0]["stage"] == "done"

    ending, lines = run_and_wait(api, events, mod, ["weapons"], mode="set")

    assert ending["stage"] == "done", ending
    assert not any("came off" in e.get("text", "") for e in lines)
    assert has_lt(game, "u")
    assert LT_ID in load_state(state)["applied_patches"]


def test_a_partial_restore_whose_bookkeeping_fails_still_says_what_changed(tmp_path, monkeypatch):
    from patcher.mods import hd_install
    from tests.test_hd2_end_to_end import build_world, run_and_wait

    api, events, game, mod = build_world(tmp_path, monkeypatch)
    assert run_and_wait(api, events, mod, ["weapons", "zones"])[0]["stage"] == "done"
    real = hd_install._restore_group

    def blob_fails(prep, group, known, on_line=None):
        if group[0].name.startswith("white_z"):
            raise OSError(28, "No space left on device")
        return real(prep, group, known, on_line)

    def settle_fails(*args, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(hd_install, "_restore_group", blob_fails)
    monkeypatch.setattr(api._patcher(), "settle_injections", settle_fails)

    ending, lines = run_and_wait(api, events, mod, ["weapons", "zones"], mode="set")

    assert ending["stage"] == "failed", ending
    assert "could not go back" in ending["error"], ending
    assert any("could not check which patches" in e.get("text", "") for e in lines)


def test_a_rebuild_after_a_revert_and_a_second_application_takes_it_off(tmp_path, monkeypatch):
    """the first Revert took the backup's copy of it again, clean, so the rebuild's is too"""
    from tests.test_hd2_end_to_end import build_world, run_and_wait

    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    state = tmp_path / "state" / "ffxiii2.json"
    vanilla = game_files(game)
    lt(game, state)
    assert run_and_wait(api, events, mod, ["weapons"])[0]["stage"] == "done"
    assert all(r.ok for r in revert(game, state))
    lt(game, state)
    assert run_and_wait(api, events, mod, ["weapons"])[0]["stage"] == "done"

    ending, lines = run_and_wait(api, events, mod, ["weapons"], mode="set")

    assert ending["stage"] == "done", ending
    assert any("Leviathan's Tears came off" in e.get("text", "") for e in lines)
    assert not has_lt(game, "u")
    assert LT_ID not in load_state(state)["applied_patches"]
    assert all(r.ok for r in revert(game, state))
    assert game_files(game) == vanilla


def test_another_mod_under_it_is_not_put_back_over_the_backup(world, tmp_path):
    """
    HD, Revert, another rain mod on the same two files by hand, Leviathan's
    Tears over that, HD, Revert. Its journal holds the other mod's bytes, and
    the backup's copy never held either of them
    """
    from patcher.archive.inject import Replacement
    from patcher.archive.transaction import apply_injection

    game, root, state = world()
    vanilla = game_files(game)
    hd(game, root, {"weapons"})
    assert all(r.ok for r in revert(game, state))
    original = bytes(reversed(PAYLOAD))[:0x16180]
    other = b"another rain mod" * 8 + original[128:]
    apply_injection(game, "alba_data/sys", [Replacement(w, other) for w in WEATHER],
                    store=tmp_path / "theirs", variants=("u",), keep_undo=False)
    lt(game, state)
    hd(game, root, {"weapons"})

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game) == vanilla
    backup = game / ".patcher-undo" / "hdproject"
    assert {p.name for p in backup.iterdir()} >= {"filelistu.win32.bin", "white_imgu.win32.bin"}


def test_a_revert_keeps_the_backup_one_pair_with_its_zone_blobs(tmp_path, monkeypatch):
    """
    the backup's white_img holds the zone indexes that point into its blobs,
    so a copy of it taken later, after the mod's own .bat rewrote a zone by
    hand, would point a vanilla blob at an index it never had
    """
    from tests import hd2_world
    from tests.hd2_world import filler, zone_entry_bytes
    from tests.test_hd2_end_to_end import build_world, run_and_wait

    block = "bg/loc001/a001/bin/block001.win32.trb"
    api, events, game, mod = build_world(tmp_path, monkeypatch, extra=weather(), rain=True)
    state = tmp_path / "state" / "ffxiii2.json"
    lt(game, state)
    assert run_and_wait(api, events, mod, ["weapons", "zones"])[0]["stage"] == "done"
    assert all(r.ok for r in revert(game, state))
    # their .bat by hand: z0010's blob and its index in white_img, rewritten together
    real = hd2_world.filler
    monkeypatch.setattr(hd2_world, "filler", lambda path, size: real(
        path, size + 50 if path.startswith("z0010") else size))
    make_game(game.parent, zones=("z0010",), extra=weather())
    monkeypatch.setattr(hd2_world, "filler", real)
    assert run_and_wait(api, events, mod, ["weapons"])[0]["stage"] == "done"

    results = revert(game, state)

    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert zone_entry_bytes(game, "z0010", block) == filler("z0010" + block, 900)
    assert not has_lt(game, "u")


def test_a_retake_cut_short_is_not_read_as_a_written_backup(world):
    """
    a power cut between the retake's record write and its swap: the pair is
    unrecorded, which an older build's backup also is, and that one was
    written from. This one was not
    """
    import json

    from patcher.mods import hd_install

    game, root, state = world()
    hd(game, root, {"weapons"})
    backup = game / ".patcher-undo" / "hdproject"
    names = ["filelistu.win32.bin", "white_imgu.win32.bin"]
    data = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
    for name in names:
        data["files"].pop(name)
    data["retaking"] = names
    data.pop("written", None)
    (backup / "backup.json").write_text(json.dumps(data))

    assert not hd_install.game_written(backup)
    put_back = hd_install.restore(hd_install.restore_prep(game, game / ".patcher-undo"),
                                  variant="u")
    assert sorted(p.name for p in put_back) == names


def test_a_retake_the_backup_refuses_puts_its_record_back(world, monkeypatch):
    """a scanner holding the backup's copy, and then only another Revert"""
    from patcher.mods import hd_install

    game, root, state = world()
    vanilla = game_files(game)
    lt(game, state)
    hd(game, root, {"weapons"})
    swap = hd_install.replace_together

    def held(staged):
        if any(target.parent.name == "hdproject" for _, target in staged):
            raise PermissionError(13, "being used by another process")
        return swap(staged)

    monkeypatch.setattr(hd_install, "replace_together", held)
    first = {r.patch_id: r for r in revert(game, state)}
    monkeypatch.setattr(hd_install, "replace_together", swap)
    assert first[LT_ID].ok and "still holds it" in first[LT_ID].detail, first[LT_ID].detail
    assert game_files(game) == vanilla

    assert all(r.ok for r in revert(game, state))
    assert game_files(game) == vanilla, "the backup's copy went back in on its own"


def test_a_second_apply_leaves_the_pair_that_has_it_alone_and_keeps_its_undo(world):
    """
    Leviathan's Tears on English only, and the Japanese set arriving later
    without it, so Apply offers it again. Written again, the English pair would
    journal its own bytes as the originals and could never come off
    """
    game, root, state = world()
    vanilla_u = game_files(game)
    lt(game, state)
    make_game(game.parent, variant="c", zones=(), extra=weather())
    vanilla_c = game_files(game, "c")
    first = load_state(state)["applied_patches"][LT_ID]["journals"]

    again = lt(game, state)

    assert "the English voice set had it already" in again.detail, again.detail
    assert set(first) <= set(load_state(state)["applied_patches"][LT_ID]["journals"])
    hd(game, root, {"weapons"})
    results = revert(game, state)
    assert all(r.ok for r in results), [(r.label, r.detail) for r in results]
    assert game_files(game) == vanilla_u and game_files(game, "c") == vanilla_c
    assert LT_ID not in load_state(state)["applied_patches"]


def test_an_older_builds_journal_of_its_own_bytes_is_said_not_called_off(world, monkeypatch):
    """a build that wrote the pair again anyway left an undo that undoes nothing"""
    from patcher.archive import transaction

    game, root, state = world()
    lt(game, state)
    make_game(game.parent, variant="c", zones=(), extra=weather())
    check = transaction._already_there
    monkeypatch.setattr(transaction, "_already_there", lambda plan, wanted: False)
    lt(game, state)
    monkeypatch.setattr(transaction, "_already_there", check)
    hd(game, root, {"weapons"})

    result = next(r for r in revert(game, state) if r.patch_id == LT_ID)

    assert not result.ok and "cannot be taken off safely" in result.detail, result.detail
    assert has_lt(game, "u")
    assert LT_ID in load_state(state)["applied_patches"]


@pytest.mark.parametrize("damage", ["missing", "unreadable"])
def test_a_kept_pair_whose_earlier_journal_is_damaged_is_refused_not_half_undone(tmp_path,
                                                                                 damage):
    """
    its index put back without the data under it describes a layout the
    archive does not have, and the entries stop reading
    """
    game = make_game(tmp_path, variant="u", zones=(), extra=weather())
    state = tmp_path / "state.json"
    lt(game, state)
    journal = Path(next(j for j in load_state(state)["applied_patches"][LT_ID]["journals"]
                        if "white_imgu" in j))
    if damage == "missing":
        journal.unlink()
    else:
        journal.write_text("{ not json")
    make_game(tmp_path, variant="c", zones=(), extra=weather())
    lt(game, state)
    before = game_files(game)

    result = next(r for r in revert(game, state) if r.patch_id == LT_ID)

    assert not result.ok and "incomplete" in result.detail, result.detail
    assert game_files(game) == before
    assert has_lt(game, "u")
