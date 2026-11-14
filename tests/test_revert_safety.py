"""
Revert, where it can go wrong: journals that are not all there, archives that
changed under them, and the HD Project's backup taking its place in the order.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from patcher import revert
from patcher.archive.spanbackup import MISSING, RESHAPED, SpanBackup, check
from patcher.state import load_state, save_state
from tests.test_hd_zones import ZONES, world  # noqa: F401  (fixture)

PATCH = SimpleNamespace(id="some_injection", label="Some Injection")


def injected(tmp_path):
    """an archive with one span journalled and then overwritten, and its record"""
    archive = tmp_path / "white_imgu.win32.bin"
    archive.write_bytes(b"vanilla!" * 1024)
    store = tmp_path / "undo"
    spans = SpanBackup(archive, store)
    spans.record(2048, 2048)
    journal = spans.commit()
    with archive.open("rb+") as handle:
        handle.seek(2048)
        handle.write(b"injected" * 256)

    index = tmp_path / "filelistu.win32.bin"
    index.write_bytes(b"INJECTED INDEX")
    backup = store / "filelistu.win32.bin.backup"
    backup.write_bytes(b"VANILLA INDEX")
    record = {"journals": [str(journal)],
              "index_backups": [{"backup": str(backup), "target": str(index)}]}
    return archive, index, journal, record


def test_a_missing_journal_stops_the_revert_before_the_index_moves(tmp_path):
    """vanilla sizes over injected bytes is an archive disagreeing with itself"""
    archive, index, journal, record = injected(tmp_path)
    journal.unlink()
    before = archive.read_bytes(), index.read_bytes()

    result = revert._revert_injection(PATCH, record)

    assert not result.ok
    assert (archive.read_bytes(), index.read_bytes()) == before, "half a revert happened"
    assert "Nothing has been changed" in result.detail
    assert "journal" not in result.detail.lower()


def test_an_archive_rewritten_since_stops_the_revert_before_anything_moves(tmp_path):
    archive, index, journal, record = injected(tmp_path)
    with archive.open("ab") as handle:
        handle.write(b"somebody else's mod" * 99)
    before = archive.read_bytes(), index.read_bytes()

    result = revert._revert_injection(PATCH, record)

    assert not result.ok
    assert (archive.read_bytes(), index.read_bytes()) == before
    assert "rewritten by something else" in result.detail


def test_a_journal_that_fits_still_reverts_both_halves(tmp_path):
    archive, index, journal, record = injected(tmp_path)

    result = revert._revert_injection(PATCH, record)

    assert result.ok, result.detail
    assert archive.read_bytes() == b"vanilla!" * 1024
    assert index.read_bytes() == b"VANILLA INDEX"


def test_check_writes_nothing_and_says_which_problem(tmp_path):
    archive, _, journal, _ = injected(tmp_path)
    assert check(journal) == ""
    with archive.open("ab") as handle:
        handle.write(b"x")
    assert check(journal) == RESHAPED
    journal.with_name(journal.name.replace(".journal", ".spans")).unlink()
    assert check(journal) == MISSING


def test_revert_puts_the_hd_project_back_and_forgets_it(world):
    from patcher.manifest import load_manifest
    from patcher.mods.hd_install import install
    from tests.test_hd_end_to_end import REPO

    game, root, data, support = world
    sys_dir, zone_dir = game / "white_data" / "sys", game / "white_data" / "zone"
    vanilla = {p: p.read_bytes() for p in (*sys_dir.iterdir(), *zone_dir.iterdir())}
    store = game / ".patcher-undo"
    install(game, root, data, support, ZONES, {"zones"}, store=store, skip_space_check=True)
    state_path = game.parent / "state.json"
    save_state(state_path, {"applied_patches": {},
                            "categories:hd_texture_pack": {"installed": ["zones"],
                                                           "pipeline": {"zones": 2}}})

    results = revert.revert_all(game, load_manifest(REPO / "manifests" / "ffxiii.json"),
                                state_path)

    assert [r.patch_id for r in results] == [revert.HD_PATCH]
    assert results[0].ok, results[0].detail
    for path, was in vanilla.items():
        assert path.read_bytes() == was, f"{path.name} did not come back"
    record = load_state(state_path)["categories:hd_texture_pack"]
    assert record["installed"] == [] and record["pipeline"] == {}


def test_the_hd_restore_takes_its_place_by_when_the_backup_was_taken(tmp_path, monkeypatch):
    """
    an injection made after the backup has to come off first or its journal
    no longer fits; one made before it is inside the backup
    """
    store = tmp_path / ".patcher-undo" / "hdproject"
    store.mkdir(parents=True)
    taken = time.time() - 3600
    (store / "backup.json").write_text(json.dumps({"files": {}, "taken": taken}))

    def at(offset):
        return datetime.fromtimestamp(taken + offset, timezone.utc).isoformat()

    state_path = tmp_path / "state.json"
    save_state(state_path, {"applied_patches": {"before": {"applied_at": at(-60)},
                                                "after": {"applied_at": at(+60)}}})
    manifest = SimpleNamespace(patches=[SimpleNamespace(id="before", label="Before"),
                                        SimpleNamespace(id="after", label="After")])
    order = []
    monkeypatch.setattr(revert, "revert_patch",
                        lambda d, m, p, s: order.append(p.id) or SimpleNamespace(ok=True))
    monkeypatch.setattr(revert, "_revert_hd",
                        lambda d, m, s: order.append("hd") or SimpleNamespace(ok=True, paths=[]))

    revert.revert_all(tmp_path, manifest, state_path)

    assert order == ["after", "hd", "before"]


def test_a_game_with_no_hd_backup_reverts_exactly_as_before(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    save_state(state_path, {"applied_patches": {"one": {"applied_at": "2026-01-01T00:00:00+00:00"}}})
    manifest = SimpleNamespace(patches=[SimpleNamespace(id="one", label="One")])
    called = []
    monkeypatch.setattr(revert, "revert_patch", lambda d, m, p, s: called.append(p.id))
    monkeypatch.setattr(revert, "_revert_hd", lambda *a: called.append("hd"))

    revert.revert_all(tmp_path, manifest, state_path)

    assert called == ["one"]


def test_a_backup_the_game_was_never_written_from_is_not_put_back(tmp_path):
    """
    a backup taken by a run that stopped before its first write, then a Steam
    update: putting that backup back would undo the update
    """
    from patcher.manifest import load_manifest
    from patcher.mods.hd_install import back_up, prepare
    from tests.test_hd_end_to_end import REPO
    from tests.test_hd_install import make_game

    game = make_game(tmp_path)
    back_up(prepare(game, tmp_path, game / ".patcher-undo"))
    sys_dir = game / "white_data" / "sys"
    for path in sys_dir.iterdir():
        path.write_bytes(b"UPDATED BY STEAM" * 64)
    updated = {p: p.read_bytes() for p in sys_dir.iterdir()}
    state_path = tmp_path / "state.json"
    save_state(state_path, {"applied_patches": {}})

    results = revert.revert_all(game, load_manifest(REPO / "manifests" / "ffxiii.json"),
                                state_path)

    for path, was in updated.items():
        assert path.read_bytes() == was, f"{path.name} was put back over the update"
    assert [r.patch_id for r in results] == [revert.HD_PATCH] and not results[0].removed


def test_a_backup_cut_short_is_not_a_failed_revert(tmp_path):
    """half a pair saved and the run closed: the game was never touched, so nothing failed"""
    from patcher.manifest import load_manifest
    from patcher.mods.hd_install import back_up, prepare
    from tests.test_hd_end_to_end import REPO
    from tests.test_hd_install import make_game

    game = make_game(tmp_path)
    prep = prepare(game, tmp_path, game / ".patcher-undo")
    back_up(prep)
    (prep.backup_dir / prep.archives[1].name).unlink()
    state_path = tmp_path / "state.json"
    save_state(state_path, {"applied_patches": {}})

    results = revert.revert_all(game, load_manifest(REPO / "manifests" / "ffxiii.json"),
                                state_path)

    assert [r.ok for r in results] == [True], [r.detail for r in results]


def test_a_second_revert_leaves_what_it_did_not_write_and_says_so(world):
    from patcher.manifest import load_manifest
    from patcher.mods.hd_install import install
    from tests.test_hd_end_to_end import REPO

    game, root, data, support = world
    install(game, root, data, support, ZONES, {"zones"}, store=game / ".patcher-undo",
            skip_space_check=True)
    state_path = game.parent / "state.json"
    save_state(state_path, {"applied_patches": {}})
    manifest = load_manifest(REPO / "manifests" / "ffxiii.json")
    first = revert.revert_all(game, manifest, state_path)
    assert first[0].ok and first[0].removed, first[0].detail

    # the mod's own installer, run by hand for a category this app turns away
    blob = game / "white_data" / "zone" / "white_z010u_img.win32.bin"
    by_hand = blob.read_bytes() + b"HD BY HAND" * 4096
    blob.write_bytes(by_hand)
    second = revert.revert_all(game, manifest, state_path)

    assert blob.read_bytes() == by_hand
    assert not second[0].ok and not second[0].removed, second[0].detail
    assert blob.name in second[0].detail and "Verify" in second[0].detail


def test_a_voice_pack_taken_off_since_does_not_fail_every_revert(tmp_path):
    """smaller or missing is not what an HD install looks like, and Verify cannot fix it"""
    from patcher.manifest import load_manifest
    from patcher.mods import hd_install
    from tests.test_hd_end_to_end import REPO
    from tests.test_hd_file_safety import modded_both_voices

    game, store, _ = modded_both_voices(tmp_path, in_game=True)
    state_path = tmp_path / "state.json"
    save_state(state_path, {"applied_patches": {}})
    manifest = load_manifest(REPO / "manifests" / "ffxiii.json")
    first = revert.revert_all(game, manifest, state_path)
    assert first[0].ok, first[0].detail
    assert not hd_install.game_written(store / "hdproject")

    for name in ("filelist_scrc.win32.bin", "white_scrc.win32.bin"):
        (game / "white_data" / "sys" / name).unlink()
    again = revert.revert_all(game, manifest, state_path)

    assert again[0].ok and not again[0].removed, again[0].detail
