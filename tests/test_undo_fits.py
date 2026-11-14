"""
Every undo outside the HD backup, held to the rule the HD backup is: it only
goes back as the bytes that were taken, it never leaves an index disagreeing
with the archive it describes, and that's settled before anything moves.
"""

import hashlib
import json
import random
import threading
import zlib
from pathlib import Path

import pytest

import patcher.bridge as bridge_mod
import patcher.revert as revert_mod
from patcher.archive import filelist
from patcher.archive.entry import SECTOR
from patcher.archive.inject import Replacement
from patcher.archive.spanbackup import (MISSING, RestoreError, SpanBackup, check, load_journal,
                                        restore)
from patcher.archive.transaction import TransactionError, apply_injection
from patcher.engine import PatchStatus, run_patches
from patcher.manifest import load_manifest
from patcher.revert import revert_patch, settle_after_restore
from patcher.state import load_state, record_applied
from tests.helpers import make_fake_pe
from tests.test_bundled_install import fake_game as bare_game, novafix_run, revert_novafix
from tests.test_revert import SAMPLE, WEATHER, _record_for, apply, fake_game, manifest, vanilla_rain
from tests.rain import PAYLOAD
from tests.test_transaction import payload

OTHER = "vfx/field/weather/weather00/veffs.jp.win32.imgb"
REPO = Path(__file__).resolve().parent.parent
MANIFESTS = REPO / "manifests"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def edited(tmp_path, spans=((1000, 500), (5000, 500))):
    """a file with each span journalled, then written over"""
    target = tmp_path / "white_imgu.win32.bin"
    target.write_bytes(bytes(range(256)) * 400)
    backup = SpanBackup(target, tmp_path / "undo")
    for offset, length in spans:
        backup.record(offset, length)
    journal = backup.commit()
    with target.open("rb+") as handle:
        for offset, length in spans:
            handle.seek(offset)
            handle.write(b"N" * length)
    return target, journal, backup.spans_path


def test_undo_bytes_that_are_not_the_ones_taken_never_go_back(tmp_path):
    target, journal, spans = edited(tmp_path)
    data = bytearray(spans.read_bytes())
    data[10] ^= 0xFF
    spans.write_bytes(bytes(data))
    before = target.read_bytes()

    assert check(journal) == MISSING
    with pytest.raises(RestoreError):
        restore(journal)
    assert target.read_bytes() == before


def test_undo_bytes_cut_short_are_refused_before_the_first_write(tmp_path):
    """restore() goes last span first, and that's the one a cut lands in"""
    target, journal, spans = edited(tmp_path)
    spans.write_bytes(spans.read_bytes()[:-200])
    before = target.read_bytes()

    assert check(journal) == MISSING
    with pytest.raises(RestoreError):
        restore(journal)
    assert target.read_bytes() == before


def test_a_journal_written_before_the_hashes_still_goes_back(tmp_path):
    target, journal, _ = edited(tmp_path)
    doc = json.loads(journal.read_text(encoding="utf-8"))
    for span in doc["spans"]:
        del span["original"]
    journal.write_text(json.dumps(doc))

    assert check(journal) == ""
    restore(journal)
    assert target.read_bytes() == bytes(range(256)) * 400


def test_a_journal_from_a_later_build_still_loads(tmp_path):
    """so going back a version doesn't turn every undo into an incomplete one"""
    _, journal, _ = edited(tmp_path)
    doc = json.loads(journal.read_text(encoding="utf-8"))
    doc["spans"][0]["something_new"] = 1
    journal.write_text(json.dumps(doc))

    assert load_journal(journal).spans[0].offset == 1000
    assert check(journal) == ""


def lt():
    return next(p for p in manifest().patches if p.id == "install_leviathans_tears")


def regions():
    """where in white_img anything in these tests writes, from the vanilla index"""
    index = filelist.parse(SAMPLE.read_bytes())
    for path in (*WEATHER, OTHER):
        _, entry = index.locate(path)
        yield entry.byte_offset, entry.sector_span * SECTOR


def snapshot(sys_dir) -> dict:
    """both indexes whole and the archives wherever an injection writes, since
    reading 1.4GB twice per test buys nothing"""
    out = {}
    for variant in ("u", "c"):
        out[variant] = (sys_dir / f"filelist{variant}.win32.bin").read_bytes()
        image = sys_dir / f"white_img{variant}.win32.bin"
        out[variant, "size"] = image.stat().st_size
        with image.open("rb") as handle:
            for offset, length in regions():
                handle.seek(offset)
                out[variant, offset] = handle.read(length)
    return out


def put_back(sys_dir, saved: dict) -> None:
    """what a Steam verify, or an HD restore of a copy without it, does here"""
    for variant in ("u", "c"):
        (sys_dir / f"filelist{variant}.win32.bin").write_bytes(saved[variant])
        with (sys_dir / f"white_img{variant}.win32.bin").open("rb+") as handle:
            for offset, _ in regions():
                handle.seek(offset)
                handle.write(saved[variant, offset])


def read(sys_dir, variant, path):
    index = filelist.parse((sys_dir / f"filelist{variant}.win32.bin").read_bytes())
    _, entry = index.locate(path)
    with (sys_dir / f"white_img{variant}.win32.bin").open("rb") as handle:
        handle.seek(entry.byte_offset)
        return entry, zlib.decompress(handle.read(entry.stored))


def injected(tmp_path):
    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    sys_dir = root / "alba_data" / "sys"
    vanilla = snapshot(sys_dir)
    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")
    assert snapshot(sys_dir) != vanilla, "the apply changed nothing, so this proves nothing"
    return root, state, sys_dir, vanilla


def their_rain() -> bytes:
    """five hex digits both ways and inside weather00's 50 sectors, so it goes in place"""
    head = random.Random(7).randbytes(80_000)
    return head + bytes(0x50000 - len(head))


def test_an_index_copy_that_is_not_the_one_taken_goes_back_by_its_numbers(tmp_path):
    root, state, sys_dir, vanilla = injected(tmp_path)
    for spec in _record_for(state, "install_leviathans_tears")["index_backups"]:
        copy = Path(spec["backup"])
        copy.write_bytes(bytes(copy.stat().st_size))

    result = revert_patch(root, manifest(), lt(), state)

    assert result.ok, result.detail
    assert snapshot(sys_dir) == vanilla


def test_another_mods_index_edits_outlive_the_revert(tmp_path):
    """
    the copy taken before the injection is only exact while nothing has
    rewritten the index since. Put back over anything that has, that mod's
    entries go back to vanilla sizes over its own bytes
    """
    root, state, sys_dir, _ = injected(tmp_path)
    _, entry = filelist.parse(SAMPLE.read_bytes()).locate(OTHER)
    for variant in ("u", "c"):
        with (sys_dir / f"white_img{variant}.win32.bin").open("rb+") as handle:
            handle.seek(entry.byte_offset)
            handle.write(zlib.compress(vanilla_rain(entry.uncompressed), 9))
    theirs = their_rain()
    apply_injection(root, "alba_data/sys", [Replacement(OTHER, theirs)],
                    store=tmp_path / "theirs", keep_undo=False)

    result = revert_patch(root, manifest(), lt(), state)

    assert result.ok, result.detail
    for variant in ("u", "c"):
        entry, data = read(sys_dir, variant, OTHER)
        assert (entry.uncompressed, data) == (len(theirs), theirs)
        for path in WEATHER:
            entry, data = read(sys_dir, variant, path)
            assert data == vanilla_rain(entry.uncompressed)


def test_another_mods_numbers_on_our_entry_stop_the_revert(tmp_path):
    """their mod went in over ours, in place and with its own numbers, and taking
    ours off would write vanilla over it"""
    root, state, sys_dir, _ = injected(tmp_path)
    apply_injection(root, "alba_data/sys", [Replacement(WEATHER[0], payload(0x15F90, 16000))],
                    store=tmp_path / "theirs", keep_undo=False)
    now = snapshot(sys_dir)

    result = revert_patch(root, manifest(), lt(), state)

    assert not result.ok
    assert snapshot(sys_dir) == now
    assert "Steam" in result.detail


def test_the_injection_written_again_by_something_else_still_comes_off(tmp_path):
    """the same payload in other bytes, which the pin still knows: the mod's own
    installer, or a build whose zlib packs it differently"""
    root, state, sys_dir, vanilla = injected(tmp_path)
    rain = PAYLOAD
    blob = zlib.compress(rain, 7)
    for variant in ("u", "c"):
        path = sys_dir / f"filelist{variant}.win32.bin"
        index = filelist.parse(path.read_bytes())
        with (sys_dir / f"white_img{variant}.win32.bin").open("rb+") as handle:
            for game_path in WEATHER:
                _, entry = index.locate(game_path)
                handle.seek(entry.byte_offset)
                handle.write(blob + bytes(entry.sector_span * SECTOR - len(blob)))
                filelist.replace_entry(index, game_path, uncompressed=len(rain), stored=len(blob))
        path.write_bytes(filelist.rebuild(index))

    result = revert_patch(root, manifest(), lt(), state)

    assert result.ok, result.detail
    assert snapshot(sys_dir) == vanilla


def test_undo_bytes_that_are_not_the_ones_taken_stop_the_revert_before_anything_moves(tmp_path):
    root, state, sys_dir, _ = injected(tmp_path)
    journal = Path(_record_for(state, "install_leviathans_tears")["journals"][0])
    spans = journal.with_name(journal.name.replace(".journal", ".spans"))
    spans.write_bytes(bytes(spans.stat().st_size))
    now = snapshot(sys_dir)

    result = revert_patch(root, manifest(), lt(), state)

    assert not result.ok
    assert snapshot(sys_dir) == now
    assert "Nothing has been changed" in result.detail


def test_bytes_written_over_the_injection_since_still_end_in_a_pair_that_agrees(tmp_path):
    """the spans go back whole and the index with them, whatever sits there now"""
    root, state, sys_dir, vanilla = injected(tmp_path)
    offset, _ = next(regions())
    with (sys_dir / "white_imgu.win32.bin").open("rb+") as handle:
        handle.seek(offset)
        handle.write(zlib.compress(b"someone else's rain" * 512, 9))

    result = revert_patch(root, manifest(), lt(), state)

    assert result.ok, result.detail
    assert snapshot(sys_dir) == vanilla


def test_an_index_that_will_not_rebuild_is_found_out_before_the_data_moves(tmp_path,
                                                                            monkeypatch):
    root, state, sys_dir, _ = injected(tmp_path)
    for spec in _record_for(state, "install_leviathans_tears")["index_backups"]:
        Path(spec["backup"]).unlink()
    now = snapshot(sys_dir)

    def refuse(raw):
        raise filelist.VerificationFailed("does not reproduce")
    monkeypatch.setattr(filelist, "verify_roundtrip", refuse)

    result = revert_patch(root, manifest(), lt(), state)

    assert not result.ok
    assert snapshot(sys_dir) == now


def test_entries_moved_since_do_not_get_their_old_numbers(tmp_path):
    root, state, sys_dir, _ = injected(tmp_path)
    for spec in _record_for(state, "install_leviathans_tears")["index_backups"]:
        Path(spec["backup"]).unlink()
    for variant in ("u", "c"):
        path = sys_dir / f"filelist{variant}.win32.bin"
        index = filelist.parse(path.read_bytes())
        _, entry = index.locate(WEATHER[0])
        filelist.relocate_entry(index, WEATHER[0], entry.sector_offset + 1,
                                entry.uncompressed, entry.stored)
        path.write_bytes(filelist.rebuild(index))
    now = snapshot(sys_dir)

    result = revert_patch(root, manifest(), lt(), state)

    assert not result.ok
    assert snapshot(sys_dir) == now


def test_a_revert_cut_off_partway_through_a_span_can_be_finished(tmp_path):
    """a disk error or a power cut inside restore() leaves a span part one, part the other"""
    root, state, sys_dir, vanilla = injected(tmp_path)
    offset, length = next(regions())
    with (sys_dir / "white_imgu.win32.bin").open("rb+") as handle:
        handle.seek(offset)
        handle.write(vanilla["u", offset][:length // 2])

    result = revert_patch(root, manifest(), lt(), state)

    assert result.ok, result.detail
    assert snapshot(sys_dir) == vanilla


def test_a_verify_since_the_injection_still_reverts_cleanly(tmp_path):
    root, state, sys_dir, vanilla = injected(tmp_path)
    put_back(sys_dir, vanilla)

    result = revert_patch(root, manifest(), lt(), state)

    assert result.ok, result.detail
    assert snapshot(sys_dir) == vanilla


def test_a_stopped_injection_takes_back_what_already_landed(tmp_path):
    """Stop after the first entry used to leave it in white_img under a vanilla index"""
    root = fake_game(tmp_path, with_archives=True)
    sys_dir = root / "alba_data" / "sys"
    vanilla = snapshot(sys_dir)
    stop = threading.Event()

    with pytest.raises(TransactionError, match="cancelled"):
        apply_injection(root, "alba_data/sys", [Replacement(p, payload()) for p in WEATHER],
                        store=tmp_path / "undo", on_progress=lambda progress: stop.set(),
                        cancel=stop)

    assert snapshot(sys_dir) == vanilla


def test_a_revert_stopped_between_its_halves_is_finished_not_applied_over(tmp_path,
                                                                           monkeypatch):
    """
    the data back and the index not, which reads as not installed. Applied
    over, the injected index was taken as the original and the next Revert
    put it back over vanilla data
    """
    root, state, sys_dir, vanilla = injected(tmp_path)

    def held(*args, **kwargs):
        raise OSError("held by the antivirus")
    monkeypatch.setattr(revert_mod, "write_atomic", held)
    first = revert_patch(root, manifest(), lt(), state)
    monkeypatch.undo()
    assert not first.ok and "run the revert again" in first.detail

    again = apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")
    refused = next(r for r in again if r.patch_id == "install_leviathans_tears")
    assert refused.status == PatchStatus.FAILED
    assert "run it again" in refused.detail

    finished = revert_patch(root, manifest(), lt(), state)
    assert finished.ok, finished.detail
    assert snapshot(sys_dir) == vanilla


def test_a_rollback_that_cannot_put_an_index_back_says_so(tmp_path, monkeypatch):
    """nothing gets recorded for a failed run, so nothing could ever finish it later"""
    import patcher.archive.transaction as transaction

    root = fake_game(tmp_path, with_archives=True)
    stop = threading.Event()

    def held(*args, **kwargs):
        raise OSError("held by the antivirus")

    def progress(step):
        # the English pair is in, index and all, by the time the Japanese one starts
        if step.label.startswith("c:"):
            monkeypatch.setattr(transaction, "copy_atomic", held)
            stop.set()

    with pytest.raises(TransactionError) as caught:
        apply_injection(root, "alba_data/sys", [Replacement(p, payload()) for p in WEATHER],
                        store=tmp_path / "undo", on_progress=progress, cancel=stop)

    assert "white_imgu.win32.bin couldn't be put back" in str(caught.value)
    assert "Steam" in str(caught.value)


def test_a_revert_cut_off_after_it_forgot_the_record_leaves_litter_not_a_stuck_one(tmp_path,
                                                                                     monkeypatch):
    """the record goes first, so a crash before its undo files are gone can't
    leave one naming files that aren't there any more"""
    root, state, sys_dir, vanilla = injected(tmp_path)

    def power_cut(path):
        raise SystemExit("power cut")
    monkeypatch.setattr(revert_mod, "discard", power_cut)
    with pytest.raises(SystemExit):
        revert_patch(root, manifest(), lt(), state)
    monkeypatch.undo()

    assert "install_leviathans_tears" not in load_state(state)["applied_patches"]
    assert snapshot(sys_dir) == vanilla


def test_a_payload_that_would_run_past_the_end_of_the_archive_is_refused(tmp_path):
    """written anyway, the archive grows and its own undo no longer fits it"""
    import os

    from tests.hd2_world import index_bytes, pack_rows

    sys_dir = tmp_path / "game" / "alba_data" / "sys"
    sys_dir.mkdir(parents=True)
    data, entries = pack_rows([("a/first.bin", os.urandom(3000)), ("z/last.bin", os.urandom(300))])
    last = entries[-1]
    image = sys_dir / "white_imgu.win32.bin"
    image.write_bytes(data[:last.byte_offset + last.stored])
    (sys_dir / "filelistu.win32.bin").write_bytes(index_bytes(entries))
    before = image.read_bytes()

    with pytest.raises(TransactionError, match="past the end"):
        apply_injection(tmp_path / "game", "alba_data/sys",
                        [Replacement("z/last.bin", os.urandom(400))],
                        store=tmp_path / "undo", variants=("u",))

    assert image.read_bytes() == before


def test_a_pair_the_hd_restore_put_back_clean_settles_whatever_its_journal_holds(tmp_path):
    """nothing is replayed into a pair that came back without it"""
    root, state, sys_dir, vanilla = injected(tmp_path)
    journal = next(Path(j) for j in _record_for(state, "install_leviathans_tears")["journals"]
                   if "white_imgu" in Path(j).name)
    spans = journal.with_name(journal.name.replace(".journal", ".spans"))
    damaged = bytearray(spans.read_bytes())
    damaged[100] ^= 0xFF
    spans.write_bytes(bytes(damaged))
    put_back(sys_dir, vanilla)
    back = [sys_dir / f"{name}{v}.win32.bin" for v in "uc" for name in ("filelist", "white_img")]

    settled = settle_after_restore(root, manifest(), state, back)

    assert [r.ok for r in settled] == [True], [r.detail for r in settled]
    assert "install_leviathans_tears" not in load_state(state)["applied_patches"]


def test_a_set_aside_dll_that_changed_since_stays_where_it_is(stub_bundle, tmp_path):
    root = bare_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    (bin_dir / "d3d9.dll").write_bytes(b"MZ reshade")
    state = tmp_path / "state.json"
    novafix_run(root, stub_bundle, state)
    (bin_dir / "d3d9.dll.backup").write_bytes(b"MZ something else")

    undone = revert_novafix(root, stub_bundle, state)

    assert undone.ok, undone.detail
    assert not (bin_dir / "d3d9.dll").exists()
    assert (bin_dir / "d3d9.dll.backup").read_bytes() == b"MZ something else"
    assert "d3d9.dll.backup" in undone.detail


def test_a_file_already_set_aside_is_never_written_over(stub_bundle, tmp_path):
    """their ReShade went aside for NovaFix, then DXVK went over NovaFix by hand"""
    root = bare_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    (bin_dir / "d3d9.dll").write_bytes(b"MZ reshade")
    state = tmp_path / "state.json"
    novafix_run(root, stub_bundle, state)
    (bin_dir / "d3d9.dll").write_bytes(b"MZ dxvk")

    again = novafix_run(root, stub_bundle, state)

    assert again.status == PatchStatus.FAILED
    assert "d3d9.dll.backup" in again.detail
    assert (bin_dir / "d3d9.dll.backup").read_bytes() == b"MZ reshade"
    assert (bin_dir / "d3d9.dll").read_bytes() == b"MZ dxvk"


def test_a_second_apply_holds_a_set_aside_file_to_its_first_hash(stub_bundle, tmp_path):
    root = bare_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    (bin_dir / "d3d9.dll").write_bytes(b"MZ reshade")
    state = tmp_path / "state.json"
    novafix_run(root, stub_bundle, state)
    (bin_dir / "d3d9.dll.backup").write_bytes(b"MZ something else")
    novafix_run(root, stub_bundle, state)

    undone = revert_novafix(root, stub_bundle, state)

    assert undone.ok, undone.detail
    assert not (bin_dir / "d3d9.dll").exists()
    assert (bin_dir / "d3d9.dll.backup").read_bytes() == b"MZ something else"


def test_a_bundled_install_stopped_partway_hands_theirs_back(tmp_path):
    """nothing is recorded for a stopped run, so Revert could never find what it set aside"""
    root = fake_game(tmp_path)
    addons = root / "alba_data" / "prog" / "win" / "bin" / "addons" / "xiii2-audio"
    addons.mkdir(parents=True, exist_ok=True)
    (addons / "xiii2_audio.asi").write_bytes(b"MZ their build of it")
    stop = threading.Event()

    results = run_patches(root, manifest(), {"install_audio_fix"},
                          already_applied_check=lambda pid: pid == "install_novafix",
                          on_progress=lambda progress: stop.set(), cancel=stop,
                          fetch=lambda url: (_ for _ in ()).throw(OSError("offline")))

    result = next(r for r in results if r.patch_id == "install_audio_fix")
    assert result.status == PatchStatus.FAILED
    assert (addons / "xiii2_audio.asi").read_bytes() == b"MZ their build of it"
    assert sorted(p.name for p in addons.iterdir()) == ["xiii2_audio.asi"]


def shim_refused(tmp_path, monkeypatch, set_aside: bytes, there: bytes):
    """the 4GB guard after a DRM refusal, with the shim's record saying what it set aside"""
    from patcher import engine as engine_module, launch_watch, state as state_module
    from patcher.patch_ops import flip_laa_flag
    from tests.test_engine_coverage import (MANIFESTS as M, ORIGINAL_CEG, lay_down_chain,
                                            offline, synthetic_xiii)

    xiii = load_manifest(M / "ffxiii.json")
    exe = tmp_path / xiii.executable_relative_path
    exe.parent.mkdir(parents=True)
    clean = synthetic_xiii(*ORIGINAL_CEG)
    exe.write_bytes(flip_laa_flag(clean))
    exe.with_name("untouched.exe").write_bytes(clean)
    lay_down_chain(tmp_path, xiii)
    record = tmp_path / "state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: record)
    state_module.record_applied(record, "install_dinput8_shim", ["dinput8.dll"],
                                {"kept": {"dinput8.dll": sha(set_aside)}})
    state_module.record_launch(record, "flip_laa_flag", launch_watch.DRM_ERROR,
                               "refused", engine_module.COVER_SHIM)
    (exe.parent / "dinput8.dll.backup").write_bytes(there)
    run_patches(tmp_path, xiii, {"flip_laa_flag"}, fetch=offline)
    return exe.parent


def test_the_shim_coming_off_hands_back_the_dinput8_it_set_aside(tmp_path, monkeypatch):
    home = shim_refused(tmp_path, monkeypatch, b"MZ asi loader", b"MZ asi loader")

    assert (home / "dinput8.dll").read_bytes() == b"MZ asi loader"
    assert not (home / "dinput8.dll.backup").exists()


def test_the_shim_coming_off_hands_back_nothing_else(tmp_path, monkeypatch):
    """a dinput8.dll is a static import of the game, and a wrong one won't start"""
    home = shim_refused(tmp_path, monkeypatch, b"MZ asi loader", b"MZ something else")

    assert not (home / "dinput8.dll").exists()
    assert (home / "dinput8.dll.backup").read_bytes() == b"MZ something else"


def lr(tmp_path, monkeypatch):
    library = tmp_path / "steam"
    install = library / "steamapps" / "common" / "LR"
    install.mkdir(parents=True)
    (install / "LRFF13.exe").write_bytes(make_fake_pe())
    (library / "steamapps" / "appmanifest_345350.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"345350"\n\t"installdir"\t\t"LR"\n}\n')
    state = tmp_path / ".state" / "ffxiii3.json"
    monkeypatch.setattr(bridge_mod, "state_path_for", lambda gid: state)
    bridge = bridge_mod.UiBridge(manifest_paths={"ffxiii3": MANIFESTS / "ffxiii3.json"},
                                 steam_library_paths=[library])
    return bridge, install, state


def drop(tmp_path, bridge, files, cancel=None):
    import zipfile

    archive = tmp_path / "outerworld.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for member, content in files.items():
            zf.writestr(member, content)
    return bridge.install_dropped("lr", str(archive), job_id="dlcrestore", cancel=cancel)


def take_off(install, state):
    m = load_manifest(MANIFESTS / "ffxiii3.json")
    patch = next(p for p in m.patches if p.id == "restore_outerworld_addons")
    return revert_patch(install, m, patch, state)


def test_a_dropped_mod_only_takes_off_what_is_still_its_own(tmp_path, monkeypatch):
    bridge, install, state = lr(tmp_path, monkeypatch)
    result = drop(tmp_path, bridge, {"mod/a.win32.imgb": b"A" * 100, "mod/b.win32.trb": b"B" * 100})
    assert result["ok"], result
    (install / "a.win32.imgb").write_bytes(b"edited since")

    undone = take_off(install, state)

    assert undone.ok, undone.detail
    assert (install / "a.win32.imgb").read_bytes() == b"edited since"
    assert not (install / "b.win32.trb").exists()


def test_a_dropped_mod_puts_back_what_it_wrote_over(tmp_path, monkeypatch):
    bridge, install, state = lr(tmp_path, monkeypatch)
    (install / "a.win32.imgb").write_bytes(b"the game's own")
    result = drop(tmp_path, bridge, {"mod/a.win32.imgb": b"A" * 100})
    assert result["ok"], result
    assert (install / "a.win32.imgb").read_bytes() == b"A" * 100

    undone = take_off(install, state)

    assert undone.ok, undone.detail
    assert (install / "a.win32.imgb").read_bytes() == b"the game's own"


def test_a_dropped_mod_stopped_partway_leaves_what_it_found(tmp_path, monkeypatch):
    bridge, install, state = lr(tmp_path, monkeypatch)
    (install / "a.win32.imgb").write_bytes(b"the game's own")
    stop = threading.Event()
    stop.set()

    result = drop(tmp_path, bridge, {"mod/a.win32.imgb": b"A" * 100,
                                     "mod/b.win32.trb": b"B" * 100}, cancel=stop)

    assert not result["ok"]
    assert (install / "a.win32.imgb").read_bytes() == b"the game's own"
    assert sorted(p.name for p in install.iterdir()) == ["LRFF13.exe", "a.win32.imgb"]


def test_a_dropped_mod_over_an_older_builds_record_still_knows_its_own_files(tmp_path,
                                                                             monkeypatch):
    """that record has names and no digests, and set aside as theirs the old
    version would come back on Revert as the user's own file"""
    bridge, install, state = lr(tmp_path, monkeypatch)
    (install / "a.win32.imgb").write_bytes(b"last version of the mod")
    record_applied(state, "restore_outerworld_addons", ["a.win32.imgb"])

    result = drop(tmp_path, bridge, {"mod/a.win32.imgb": b"A" * 100})
    assert result["ok"], result
    assert not (install / "a.win32.imgb.backup").exists()

    undone = take_off(install, state)

    assert undone.ok, undone.detail
    assert not (install / "a.win32.imgb").exists()


def test_a_dropped_mod_never_writes_over_a_file_it_already_set_aside(tmp_path, monkeypatch):
    bridge, install, state = lr(tmp_path, monkeypatch)
    (install / "a.win32.imgb").write_bytes(b"the game's own")
    assert drop(tmp_path, bridge, {"mod/a.win32.imgb": b"A" * 100})["ok"]
    (install / "a.win32.imgb").write_bytes(b"edited since")

    result = drop(tmp_path, bridge, {"mod/a.win32.imgb": b"A2" * 50})

    assert not result["ok"]
    assert (install / "a.win32.imgb.backup").read_bytes() == b"the game's own"
    assert (install / "a.win32.imgb").read_bytes() == b"edited since"


def test_a_second_drop_with_fewer_files_still_takes_off_the_first(tmp_path, monkeypatch):
    bridge, install, state = lr(tmp_path, monkeypatch)
    (install / "b.win32.trb").write_bytes(b"the game's own")
    assert drop(tmp_path, bridge, {"mod/a.win32.imgb": b"A" * 100,
                                   "mod/b.win32.trb": b"B" * 100})["ok"]
    assert drop(tmp_path, bridge, {"mod/a.win32.imgb": b"A2" * 50})["ok"]

    undone = take_off(install, state)

    assert undone.ok, undone.detail
    assert sorted(p.name for p in install.iterdir()) == ["LRFF13.exe", "b.win32.trb"]
    assert (install / "b.win32.trb").read_bytes() == b"the game's own"
