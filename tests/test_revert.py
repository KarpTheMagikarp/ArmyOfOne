"""
Uninstall. Every applied patch records enough to undo itself; this turns that
record back into a clean game folder.
"""

import json
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from patcher.archive import filelist
from patcher.archive.entry import SECTOR
from patcher.engine import run_patches
from patcher.patch_ops import is_laa_enabled
from patcher.revert import revert_all, revert_patch
from patcher.state import load_state
from tests import rain
from tests.test_transaction import digest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "data" / "filelistu_sample.bin"
WEATHER = ["vfx/field/weather/weather07/veffs.jp.win32.imgb",
           "vfx/field/weather/weather08/veffs.jp.win32.imgb"]


def manifest():
    # Leviathan's Tears back on Apply, on a stand-in for the payload that
    # stopped shipping: its revert is what an earlier version's install needs
    return rain.manifest()


def fake_pe():
    header = bytearray(0x80)
    struct.pack_into("<I", header, 0x3C, 0x80)
    return bytes(header) + b"PE\x00\x00" + bytes(20) + b"pad"


def vanilla_rain(size: int) -> bytes:
    """what the game keeps at a weather entry, as long as the real index says it is"""
    return (b"vanilla rain" * (size // 12 + 1))[:size]


def sums(sys_dir: Path, names) -> dict:
    """
    each file by its digest, never whole: the images are sparse gigabytes, and
    holding both sets in memory got a full run killed on an 8GB box
    """
    return {name: digest(sys_dir / name) for name in names}


def fake_game(tmp_path, with_archives=False):
    m = manifest()
    root = tmp_path / "FFXIII2"
    exe = root / m.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(fake_pe())
    if with_archives:
        if not SAMPLE.is_file():
            pytest.skip("no sample filelist")
        sys_dir = root / "alba_data" / "sys"
        sys_dir.mkdir(parents=True)
        index = filelist.parse(SAMPLE.read_bytes())
        top = max(index.entries(), key=lambda e: e.sector_offset)
        size = top.byte_offset + top.sector_span * SECTOR
        for variant in ("u", "c"):
            shutil.copyfile(SAMPLE, sys_dir / f"filelist{variant}.win32.bin")
            white = sys_dir / f"white_img{variant}.win32.bin"
            with white.open("wb") as handle:
                handle.truncate(size)
            with white.open("rb+") as handle:
                for path in WEATHER:
                    _, entry = index.locate(path)
                    handle.seek(entry.byte_offset)
                    handle.write(zlib.compress(vanilla_rain(entry.uncompressed), 9))
    return root


def apply(root, state_path, ids, undo_store=None):
    from patcher.state import record_applied
    return run_patches(
        root, manifest(), ids,
        # NovaFix owns the addons folder the audio fix lives in
        already_applied_check=lambda pid: pid == "install_novafix",
        on_applied=lambda r: record_applied(state_path, r.patch_id,
                                            r.installed_files, r.revert_info),
        undo_store=undo_store,
        fetch=lambda u: (_ for _ in ()).throw(OSError("offline")))


def test_a_bundled_install_is_fully_removed(tmp_path):
    root = fake_game(tmp_path)
    state = tmp_path / "state.json"
    bin_dir = root / "alba_data" / "prog" / "win" / "bin" / "addons" / "xiii2-audio"
    apply(root, state, {"install_audio_fix"})
    assert (bin_dir / "xiii2_audio.asi").is_file()

    patch = next(p for p in manifest().patches if p.id == "install_audio_fix")
    result = revert_patch(root, manifest(), patch, state)

    assert result.ok, result.detail
    assert not (bin_dir / "xiii2_audio.asi").exists()
    assert not (bin_dir / "LICENSE-MinHook.txt").exists()
    assert "install_audio_fix" not in load_state(state)["applied_patches"]


def test_the_4gb_patch_is_reverted_bit_for_bit(tmp_path):
    root = fake_game(tmp_path)
    state = tmp_path / "state.json"
    exe = root / manifest().executable_relative_path
    original = exe.read_bytes()

    apply(root, state, {"flip_laa_flag"})
    assert is_laa_enabled(exe.read_bytes())

    patch = next(p for p in manifest().patches if p.id == "flip_laa_flag")
    result = revert_patch(root, manifest(), patch, state)

    assert result.ok, result.detail
    assert exe.read_bytes() == original


def test_an_archive_injection_is_restored_from_its_journal(tmp_path):
    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    sys_dir = root / "alba_data" / "sys"
    before = {v: (sys_dir / f"filelist{v}.win32.bin").read_bytes() for v in ("u", "c")}

    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")
    assert (sys_dir / "filelistu.win32.bin").read_bytes() != before["u"]

    patch = next(p for p in manifest().patches if p.id == "install_leviathans_tears")
    result = revert_patch(root, manifest(), patch, state)

    assert result.ok, result.detail
    for variant in ("u", "c"):
        index = filelist.parse((sys_dir / f"filelist{variant}.win32.bin").read_bytes())
        with (sys_dir / f"white_img{variant}.win32.bin").open("rb") as handle:
            for path in WEATHER:
                _, entry = index.locate(path)
                handle.seek(entry.byte_offset)
                assert zlib.decompress(handle.read(entry.stored)) == \
                    vanilla_rain(entry.uncompressed)


def test_a_file_we_did_not_install_is_never_deleted(tmp_path):
    """
    reverting is driven by recorded state, never by guessing from the
    manifest. someone who put a file there by hand keeps it.
    """
    root = fake_game(tmp_path)
    state = tmp_path / "state.json"
    bin_dir = root / "alba_data" / "prog" / "win" / "bin" / "addons"
    apply(root, state, {"install_audio_fix"})

    theirs = bin_dir / "something_else.dll"
    theirs.write_bytes(b"not ours")

    patch = next(p for p in manifest().patches if p.id == "install_audio_fix")
    revert_patch(root, manifest(), patch, state)

    assert theirs.is_file()


def test_reverting_something_never_applied_is_harmless(tmp_path):
    root = fake_game(tmp_path)
    patch = next(p for p in manifest().patches if p.id == "install_audio_fix")
    result = revert_patch(root, manifest(), patch, tmp_path / "state.json")
    assert result.ok
    assert "wasn't applied" in result.detail


def test_a_missing_file_is_not_an_error(tmp_path):
    """the user may have deleted it by hand; the end state is the same"""
    root = fake_game(tmp_path)
    state = tmp_path / "state.json"
    bin_dir = root / "alba_data" / "prog" / "win" / "bin" / "addons" / "xiii2-audio"
    apply(root, state, {"install_audio_fix"})
    (bin_dir / "xiii2_audio.asi").unlink()

    patch = next(p for p in manifest().patches if p.id == "install_audio_fix")
    assert revert_patch(root, manifest(), patch, state).ok


def test_revert_all_undoes_everything_in_reverse(tmp_path):
    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    exe = root / manifest().executable_relative_path
    original_exe = exe.read_bytes()
    bin_dir = root / "alba_data" / "prog" / "win" / "bin" / "addons"

    apply(root, state, {"flip_laa_flag", "install_audio_fix", "install_leviathans_tears"},
          undo_store=tmp_path / "undo")

    results = revert_all(root, manifest(), state)

    assert all(r.ok for r in results), [r.detail for r in results if not r.ok]
    assert exe.read_bytes() == original_exe
    assert not (bin_dir / "xiii2-audio" / "xiii2_audio.asi").exists()
    assert load_state(state)["applied_patches"] == {}


def test_a_failed_revert_keeps_the_record(tmp_path):
    """
    forgetting it would make a half-reverted patch look like it was never
    applied, so it would never be retried.
    """
    root = fake_game(tmp_path)
    state = tmp_path / "state.json"
    apply(root, state, {"install_leviathans_tears"})   # no archives, so it failed

    from patcher.state import record_applied
    record_applied(state, "install_leviathans_tears", ["x.imgb"], {"journals": []})

    patch = next(p for p in manifest().patches if p.id == "install_leviathans_tears")
    result = revert_patch(root, manifest(), patch, state)

    assert not result.ok
    assert "install_leviathans_tears" in load_state(state)["applied_patches"]


def test_revert_finds_files_in_the_folder_novafix_actually_scans(tmp_path):
    """
    NovaFix reads exactly one of addons/extra/extras/mods/dll, the first that
    exists, so the engine installs into that one. Revert recorded the manifest
    path instead, looked in a folder nothing had been written to, deleted
    nothing and reported success. On XIII the leftover add-on then kept the 4GB
    guard answering yes, which is the guard that decides whether it is safe to
    patch a CEG executable.
    """
    from patcher.engine import PatchStatus, install_target_dir, run_patches
    from patcher.manifest import load_manifest
    from patcher.revert import revert_patch
    from patcher.state import load_state

    root = Path(__file__).resolve().parent.parent
    manifest = load_manifest(root / "manifests" / "ffxiii.json")
    exe = tmp_path / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"\0" * 64)
    # the install already has a mods/ folder, so that is the one NovaFix reads
    (exe.parent / "mods").mkdir()

    patch = next(p for p in manifest.patches if p.id == "install_exe_redirect")
    landed = install_target_dir(tmp_path, patch)
    assert landed.name == "ff13-exe-redirect" and "mods" in landed.parts

    from patcher.state import record_applied

    state = tmp_path / "state.json"
    results = run_patches(tmp_path, manifest, {"install_exe_redirect"},
                          already_applied_check=lambda pid: pid == "install_novafix",
                          on_applied=lambda r: record_applied(state, r.patch_id,
                                                              r.installed_files, r.revert_info),
                          fetch=lambda u: (_ for _ in ()).throw(OSError("offline")))
    assert next(r for r in results if r.patch_id == "install_exe_redirect").status \
        == PatchStatus.APPLIED
    asi = landed / patch.bundled_files[0]["target"]
    assert asi.is_file()

    result = revert_patch(tmp_path, manifest, patch, state)
    assert result.ok is True
    assert not asi.exists(), "revert said ok and left the add-on loading"
    assert "install_exe_redirect" not in load_state(state)["applied_patches"]


def test_an_injection_that_has_been_undone_stops_reporting_as_applied(tmp_path):
    """
    The row that lied. An archive injection was answered by the state record
    alone, which stays true after a Steam file verification has put the
    original archive back, so the menu said the mod was installed about a mod
    that was gone.

    The journal already holds the offset, the length and the original bytes of
    every region we overwrote, so reading those regions back is a handful of
    seeks rather than a pass over a multi-gigabyte archive.
    """
    from patcher.state import scan_patch_status

    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    sys_dir = root / "alba_data" / "sys"
    images = [f"white_img{v}.win32.bin" for v in ("u", "c")]
    vanilla = sums(sys_dir, images)
    # what an apply can move in an image: its length and the weather's sectors
    index = filelist.parse(SAMPLE.read_bytes())
    spans = [index.locate(path)[1] for path in WEATHER]
    kept = {}
    for name in images:
        with (sys_dir / name).open("rb") as handle:
            regions = []
            for entry in spans:
                handle.seek(entry.byte_offset)
                regions.append((entry.byte_offset, handle.read(entry.sector_span * SECTOR)))
        kept[name] = ((sys_dir / name).stat().st_size, regions)

    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")
    assert scan_patch_status(root, manifest(), state)["install_leviathans_tears"] is True

    # Steam verifies the files and puts the originals back, without telling us
    for name, (size, regions) in kept.items():
        with (sys_dir / name).open("rb+") as handle:
            handle.truncate(size)
            for offset, data in regions:
                handle.seek(offset)
                handle.write(data)
    assert sums(sys_dir, images) == vanilla

    assert scan_patch_status(root, manifest(), state)["install_leviathans_tears"] is False, \
        "said an injected mod was installed after the archive was put back"


def test_an_injection_still_in_the_archive_reports_as_applied(tmp_path):
    """the other direction: the check must not be pessimistic either"""
    from patcher.state import scan_patch_status

    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")

    for _ in range(3):
        assert scan_patch_status(root, manifest(), state)["install_leviathans_tears"] is True


def test_a_replaced_archive_is_not_mistaken_for_an_injection(tmp_path):
    """
    A wholesale replacement makes the recorded offsets describe a file that no
    longer exists, and anything read at them is meaningless. Reading garbage
    that differs from the original would otherwise look exactly like our own
    injected bytes.
    """
    from patcher.state import scan_patch_status

    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")

    target = root / "alba_data" / "sys" / "white_imgu.win32.bin"
    size = target.stat().st_size + 4096
    with target.open("wb") as handle:
        for at in range(0, size, 1 << 20):
            handle.write(b"\xcc" * min(1 << 20, size - at))

    assert scan_patch_status(root, manifest(), state)["install_leviathans_tears"] is False


ARCHIVE_FILES = [f"filelist{v}.win32.bin" for v in ("u", "c")] + \
                [f"white_img{v}.win32.bin" for v in ("u", "c")]


def test_reverting_an_injection_restores_the_index_as_well_as_the_data(tmp_path):
    """
    An injected archive is two files, and the revert has to put back both.

    white_img holds the compressed blobs; filelist is the index saying where
    each one starts and how long it is. The apply rewrites both. Restoring only
    white_img leaves an index describing the injected layout over vanilla data,
    and nothing downstream reports it: scan_patch_status then reads the patch as
    not applied, so the user is told it came off cleanly.

    The test above this one asserts the DATA decompresses back to vanilla, which
    it does. It never asserted the index came back, and zlib tolerating a stray
    trailing byte is why that went unnoticed.
    """
    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    sys_dir = root / "alba_data" / "sys"

    before = sums(sys_dir, ARCHIVE_FILES)
    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")
    after = sums(sys_dir, ARCHIVE_FILES)
    touched = {n for n in ARCHIVE_FILES if after[n] != before[n]}
    assert touched, "the apply changed nothing, so this proves nothing"

    patch = next(p for p in manifest().patches if p.id == "install_leviathans_tears")
    result = revert_patch(root, manifest(), patch, state)
    assert result.ok, result.detail

    now = sums(sys_dir, touched)
    stale = [n for n in sorted(touched) if now[n] != before[n]]
    assert not stale, f"revert left these rewritten: {stale}"
    # and the folder its undo went in, emptied by it
    assert not list((tmp_path / "undo").glob("run-*"))


def test_leviathans_tears_reads_as_applied_after_a_real_run(tmp_path):
    """
    Reported from a real XIII-2 install: Apply finishes, the row goes back to
    REQUIRED. Everything else on that game settles correctly, so the injection
    path is the odd one out.

    This walks the same route the window does: apply, record, then ask
    scan_patch_status what is on the disk.
    """
    from patcher.engine import PatchStatus
    from patcher.state import scan_patch_status

    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"

    results = apply(root, state, {"install_leviathans_tears"},
                    undo_store=tmp_path / "undo")
    result = next(r for r in results if r.patch_id == "install_leviathans_tears")
    assert result.status == PatchStatus.APPLIED, result.detail

    recorded = load_state(state)["applied_patches"]
    assert "install_leviathans_tears" in recorded, "the run recorded nothing"

    status = scan_patch_status(root, manifest(), state)
    assert status["install_leviathans_tears"] is True, (
        "applied, recorded, and the scan still says no")


def test_an_injection_is_read_out_of_the_archive_not_the_undo_folder(tmp_path):
    """
    The row that says REQUIRED about a mod that is already installed.

    Every other patch type answers from the game folder, so losing the state
    json or the undo store costs them nothing: the LAA bit is in the exe, the
    dll hashes against its pin. An injection used to answer from the span
    journal, which lives in neither, so anything that cleared the state folder
    dragged a perfectly installed mod back to REQUIRED and Apply would
    cheerfully inject it a second time.
    """
    from patcher.state import scan_patch_status

    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")
    assert scan_patch_status(root, manifest(), state)["install_leviathans_tears"] is True

    shutil.rmtree(tmp_path / "undo")
    state.unlink()

    assert scan_patch_status(root, manifest(), state)["install_leviathans_tears"] is True, \
        "the payload is sitting in white_img and the row says it still needs installing"


def test_an_untouched_archive_still_reads_as_not_installed(tmp_path):
    """the other direction, or the check above is just a function returning True"""
    from patcher.state import scan_patch_status

    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    assert scan_patch_status(root, manifest(), state)["install_leviathans_tears"] is False


def test_the_doctor_explains_why_an_injection_reads_as_applied(tmp_path, monkeypatch):
    """
    The row saying REQUIRED after a successful Apply is the one state in this
    tool with nothing to look at: the answer lives inside a multi-gigabyte
    archive, compared against spans recorded somewhere else.

    So the doctor walks the same steps the check does and names the one that
    decided. Both directions, because "it is there" and "it is gone" are both
    answers somebody needs.
    """
    from patcher.doctor import BAD, OK, _injection_report, Report

    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: state)

    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")

    def findings():
        report = Report(game="XIII-2")
        _injection_report(report, manifest(), root)
        return [f for f in report.findings if "Leviathan" in f.title]

    found = findings()
    assert found, "said nothing at all about the injection"
    assert all(f.level == OK for f in found), [f.detail for f in found]
    assert any("in the archive" in f.detail for f in found)

    # now put the archives back the way Steam's verify would, and leave a
    # record behind it: applied once, not there now, which is the disagreement
    # the report exists to explain
    from patcher.state import record_applied

    patch = next(p for p in manifest().patches if p.id == "install_leviathans_tears")
    revert_patch(root, manifest(), patch, state)
    assert "install_leviathans_tears" not in load_state(state)["applied_patches"]
    record_applied(state, "install_leviathans_tears", [], {"journals": []})

    gone = findings()
    assert [f.level for f in gone] == [BAD], [f.detail for f in gone]
    assert "Steam" in gone[0].detail


def test_the_doctor_on_a_patch_that_doesnt_ship_any_more(tmp_path, monkeypatch):
    """
    Leviathan's Tears isn't ours to talk about as a row now. It's only news
    while an earlier version's install of it is in the archive or on the
    record, and then the news is how it comes off
    """
    from dataclasses import replace

    from patcher.doctor import OK, WARN, Report, _injection_report
    from patcher.state import record_applied

    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    monkeypatch.setattr("patcher.paths.state_path_for", lambda game_id: state)
    # the stand-in's entry, retired the way the shipped one is
    retired = replace(manifest(), patches=[replace(p, retired=True) if p.id == rain.LT_ID else p
                                           for p in manifest().patches])

    def said():
        report = Report(game="XIII-2")
        _injection_report(report, retired, root)
        return [(f.level, f.detail) for f in report.findings if "Leviathan" in f.title]

    assert said() == []

    apply(root, state, {rain.LT_ID}, undo_store=tmp_path / "undo")
    [(level, detail)] = said()
    assert level == OK and "Remove under the Mods drawer's Installed" in detail, detail

    # a new PC: the record gone and the game's folder kept
    state.rename(tmp_path / "kept.json")
    [(level, detail)] = said()
    assert level == OK and "through Steam" in detail, detail
    (tmp_path / "kept.json").rename(state)

    patch = next(p for p in manifest().patches if p.id == rain.LT_ID)
    assert revert_patch(root, manifest(), patch, state).ok
    assert said() == []

    # put back by Steam's verify with the record left behind
    record_applied(state, rain.LT_ID, [], {"journals": []})
    [(level, detail)] = said()
    assert level == WARN and "clears the record" in detail, detail


def test_every_recorder_of_an_injection_records_its_index_backups_too():
    """
    revert restores the white_img spans from the journals and then puts each
    filelist back from its own copy, and an archive is only consistent once
    both have happened. bridge._inject_from recorded the journals and not the
    backups, so a mod installed by dropping it on the window reverted to an
    archive whose index still described the injected layout, and reported
    success. Apply, three files away, had always recorded both.

    Both recorders now build the record with transaction.carry_undo(), so that
    is what is held to it, and the two modules are held to going through it.
    """
    import re
    from pathlib import Path

    from patcher.archive.transaction import carry_undo

    result = {"undo": ["run/white_imgu.win32.bin.1.journal"], "archives": ["u"], "kept": [],
              "index_backups": [{"backup": "run/filelistu.win32.bin.backup",
                                 "target": "sys/filelistu.win32.bin"}],
              "index_entries": [{"target": "sys/filelistu.win32.bin", "entries": []}]}
    recorded = carry_undo(result, None)
    assert recorded["journals"] == result["undo"]
    assert recorded["index_backups"] == result["index_backups"]
    assert recorded["index_entries"] == result["index_entries"]

    root = Path(__file__).resolve().parent.parent
    for name in ("patcher/engine.py", "patcher/bridge.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "carry_undo(" in text, f"{name} records an injection some other way"
        for match in re.finditer(r"\{[^{}]*\"journals\"[^{}]*\}", text, re.S):
            assert '"index_backups"' in match.group(0), (
                f"{name} records an injection with journals but no index_backups, "
                f"so a revert of it restores the data and leaves the index:\n"
                f"{match.group(0)}")


def _record_for(state: Path, patch_id: str) -> dict:
    return json.loads(state.read_text(encoding="utf-8"))["applied_patches"][patch_id]


def _write_record(state: Path, patch_id: str, record: dict) -> None:
    doc = json.loads(state.read_text(encoding="utf-8"))
    doc["applied_patches"][patch_id] = record
    state.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def test_a_missing_index_backup_is_rebuilt_instead_of_sending_anyone_to_steam(tmp_path):
    """
    The answer to "your backup is gone" used to be verify the game files, which
    on XIII is tens of gigabytes and twenty minutes to undo a 90KB mod.

    It does not have to be. An injection changes exactly two numbers per entry
    in the index and nothing else: same sector offsets, same paths, same record
    lengths. Those numbers are recorded at apply time next to the backup, so
    losing the file costs nothing.
    """
    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    sys_dir = root / "alba_data" / "sys"

    before = sums(sys_dir, ARCHIVE_FILES)
    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")
    after = sums(sys_dir, ARCHIVE_FILES)
    touched = {n for n in ARCHIVE_FILES if after[n] != before[n]}
    assert touched, "the apply changed nothing, so this proves nothing"

    # the file route, deleted. the data route is all that is left.
    record = _record_for(state, "install_leviathans_tears")
    assert record["index_entries"], "nothing recorded the values to rebuild from"
    for spec in record["index_backups"]:
        Path(spec["backup"]).unlink()

    patch = next(p for p in manifest().patches if p.id == "install_leviathans_tears")
    result = revert_patch(root, manifest(), patch, state)

    assert result.ok, result.detail
    now = sums(sys_dir, touched)
    stale = [n for n in sorted(touched) if now[n] != before[n]]
    assert not stale, f"rebuilt index did not match vanilla byte for byte: {stale}"


def test_with_no_backup_and_no_values_it_refuses_without_touching_anything(tmp_path):
    """
    The half-revert is the thing to avoid. Restoring white_img and then finding
    the index cannot be put back leaves an archive disagreeing with itself,
    which is worse than the mod being installed, so the check runs before a
    byte moves rather than after.
    """
    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    sys_dir = root / "alba_data" / "sys"

    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")
    injected = sums(sys_dir, ARCHIVE_FILES)

    record = _record_for(state, "install_leviathans_tears")
    for spec in record["index_backups"]:
        Path(spec["backup"]).unlink()
    record["index_entries"] = []
    _write_record(state, "install_leviathans_tears", record)

    patch = next(p for p in manifest().patches if p.id == "install_leviathans_tears")
    result = revert_patch(root, manifest(), patch, state)

    assert not result.ok
    now = sums(sys_dir, ARCHIVE_FILES)
    for name in ARCHIVE_FILES:
        assert now[name] == injected[name], f"{name} was modified by a revert that then refused"
    # and it says so, because a refusal that leaves someone wondering whether
    # their game is half-undone is barely better than the half-undo
    assert "nothing has been changed" in result.detail.lower()


def test_the_refusal_is_something_you_could_read_to_a_person(tmp_path):
    """
    it is the one message that ends with "go and re-download your game", so it
    has to say what happened and that nothing was broken on the way
    """
    root = fake_game(tmp_path, with_archives=True)
    state = tmp_path / "state.json"
    apply(root, state, {"install_leviathans_tears"}, undo_store=tmp_path / "undo")

    record = _record_for(state, "install_leviathans_tears")
    for spec in record["index_backups"]:
        Path(spec["backup"]).unlink()
    record["index_entries"] = []
    _write_record(state, "install_leviathans_tears", record)

    patch = next(p for p in manifest().patches if p.id == "install_leviathans_tears")
    detail = revert_patch(root, manifest(), patch, state).detail

    for jargon in ("filelist", "sha256", "journal", "sector", "zlib", "index_backups",
                   "traceback", "None"):
        assert jargon not in detail, f"{jargon!r} in a message for a player: {detail}"
    assert "Steam" in detail
    assert len(detail) < 260, len(detail)
