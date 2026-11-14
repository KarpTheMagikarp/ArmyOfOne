from pathlib import Path

from patcher.engine import PatchStatus, run_patches
from patcher.manifest import GameManifest, Patch
from patcher.state import (
    load_state,
    record_applied,
    save_state,
    scan_patch_status,
)
from tests.helpers import make_fake_pe


def make_manifest(patches: list[Patch]) -> GameManifest:
    return GameManifest(
        game_id="test_game",
        display_name="Test Game",
        manifest_version="v1.0.0",
        executable_relative_path="game.exe",
        patches=patches,
    )


def laa_patch() -> Patch:
    return Patch(
        id="flip_laa_flag", label="4GB Patch", type="binary_patch",
        default_enabled=True, requires_backup=False, operation="flip_laa_flag",
    )


def _manifest_with_patch(**fields) -> GameManifest:
    """a one-patch manifest for the verification tests"""
    base = dict(id="thing", label="Thing", default_enabled=True, requires_backup=False)
    base.update(fields)
    return make_manifest([Patch(**base)])


class _result:
    """the shape on_applied is handed"""

    def __init__(self, patch_id, installed_files, revert_info=None):
        self.patch_id = patch_id
        self.installed_files = installed_files
        self.revert_info = revert_info


def placeholder_patch() -> Patch:
    return Patch(
        id="frame_pacer_fix", label="Frame Pacer Fix", type="binary_patch",
        default_enabled=True, operation="frame_pacer_fix",  # not in CHECKS or OPERATIONS
    )


def test_load_state_returns_empty_default_when_missing(tmp_path: Path):
    state = load_state(tmp_path / "state.json")
    assert state == {"applied_patches": {}}


def test_save_and_load_state_roundtrip(tmp_path: Path):
    state_path = tmp_path / "nested" / "state.json"
    save_state(state_path, {"applied_patches": {"flip_laa_flag": {"applied_at": "x"}}})

    loaded = load_state(state_path)

    assert loaded["applied_patches"]["flip_laa_flag"]["applied_at"] == "x"


def test_record_applied_adds_a_timestamped_entry(tmp_path: Path):
    state_path = tmp_path / "state.json"
    record_applied(state_path, "flip_laa_flag")

    state = load_state(state_path)
    assert "flip_laa_flag" in state["applied_patches"]
    assert "applied_at" in state["applied_patches"]["flip_laa_flag"]


def test_scan_reads_ground_truth_from_the_real_file_when_patched(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0020))  # already has LAA set

    manifest = make_manifest([laa_patch()])
    status = scan_patch_status(tmp_path, manifest, tmp_path / "state.json")

    assert status["flip_laa_flag"] is True


def test_scan_reads_ground_truth_from_the_real_file_when_not_patched(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))

    manifest = make_manifest([laa_patch()])
    status = scan_patch_status(tmp_path, manifest, tmp_path / "state.json")

    assert status["flip_laa_flag"] is False


def test_scan_ignores_stale_recorded_state_when_file_says_otherwise(tmp_path: Path):
    # record says applied, but the actual file on disk says it isn't
    # (e.g. the game got reinstalled since). the file should win.
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))

    state_path = tmp_path / "state.json"
    record_applied(state_path, "flip_laa_flag")

    manifest = make_manifest([laa_patch()])
    status = scan_patch_status(tmp_path, manifest, state_path)

    assert status["flip_laa_flag"] is False


def test_scan_falls_back_to_recorded_state_for_unimplemented_operation(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe())

    state_path = tmp_path / "state.json"
    record_applied(state_path, "frame_pacer_fix")

    manifest = make_manifest([placeholder_patch()])
    status = scan_patch_status(tmp_path, manifest, state_path)

    assert status["frame_pacer_fix"] is True


def test_scan_falls_back_to_false_when_nothing_verifies_and_nothing_recorded(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe())

    manifest = make_manifest([placeholder_patch()])
    status = scan_patch_status(tmp_path, manifest, tmp_path / "state.json")

    assert status["frame_pacer_fix"] is False


def test_run_patches_skips_already_applied_patches_via_scan(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0020))  # already patched
    state_path = tmp_path / "state.json"

    manifest = make_manifest([laa_patch()])
    scan = scan_patch_status(tmp_path, manifest, state_path)

    results = run_patches(
        tmp_path, manifest, {"flip_laa_flag"},
        already_applied_check=lambda pid: scan.get(pid, False),
    )

    assert results[0].status == PatchStatus.ALREADY_APPLIED


def test_run_patches_records_state_on_fresh_apply(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))
    state_path = tmp_path / "state.json"

    manifest = make_manifest([laa_patch()])
    results = run_patches(
        tmp_path, manifest, {"flip_laa_flag"},
        on_applied=lambda r: record_applied(state_path, r.patch_id, r.installed_files),
    )

    assert results[0].status == PatchStatus.APPLIED
    assert "flip_laa_flag" in load_state(state_path)["applied_patches"]


def test_run_patches_full_state_wiring_end_to_end(tmp_path: Path):
    """
    simulates two consecutive runs of the tool: first run patches for
    real and records it, second run should recognize it's already done
    via the scan and not touch the file again.
    """
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))
    state_path = tmp_path / "state.json"
    manifest = make_manifest([laa_patch()])

    def do_run():
        scan = scan_patch_status(tmp_path, manifest, state_path)
        return run_patches(
            tmp_path, manifest, {"flip_laa_flag"},
            already_applied_check=lambda pid: scan.get(pid, False),
            on_applied=lambda r: record_applied(state_path, r.patch_id, r.installed_files),
        )

    first_run = do_run()
    assert first_run[0].status == PatchStatus.APPLIED

    second_run = do_run()
    assert second_run[0].status == PatchStatus.ALREADY_APPLIED


def test_a_launch_verdict_survives_the_patch_being_reverted(tmp_path: Path):
    """
    launch_checks lives outside applied_patches on purpose. reverting a patch
    empties its applied entry, and that is exactly the moment the record has to
    still be there: the next Apply reads it to know not to try again.
    """
    from patcher.launch_watch import DRM_ERROR
    from patcher.state import launch_blocked, record_launch, save_state

    state_path = tmp_path / "state.json"
    record_applied(state_path, "flip_laa_flag", [])
    record_launch(state_path, "flip_laa_flag", DRM_ERROR, "Steam refused it", "dinput8_shim")

    state = load_state(state_path)
    state["applied_patches"].pop("flip_laa_flag")
    save_state(state_path, state)

    assert launch_blocked(state_path, "flip_laa_flag", "dinput8_shim")


def test_only_a_drm_error_counts_as_blocked(tmp_path: Path):
    from patcher.launch_watch import DRM_ERROR, OK, UNKNOWN
    from patcher.state import launch_blocked, record_launch

    state_path = tmp_path / "state.json"
    cover = "dinput8_shim"

    assert not launch_blocked(state_path, "flip_laa_flag", cover), "blocked with no record"

    for verdict in (OK, UNKNOWN):
        record_launch(state_path, "flip_laa_flag", verdict, "", cover)
        assert not launch_blocked(state_path, "flip_laa_flag", cover), verdict

    record_launch(state_path, "flip_laa_flag", DRM_ERROR, "", cover)
    assert launch_blocked(state_path, "flip_laa_flag", cover)

    # per patch, not a flag on the whole install
    assert not launch_blocked(state_path, "install_novafix", cover)
    # and per cover, not a flag on the machine. this is the one that shipped
    # wrong: a refusal of the NovaFix add-on blocked the dinput8 shim, which is
    # a different mechanism loading at a different point in the process.
    assert not launch_blocked(state_path, "flip_laa_flag", "proxy_config")
    assert not launch_blocked(state_path, "flip_laa_flag", "")


def test_a_junk_launch_checks_value_does_not_take_the_scan_down(tmp_path: Path):
    """a hand-edited or older state file has to degrade to 'no evidence'"""
    from patcher.state import launch_blocked, record_launch

    state_path = tmp_path / "state.json"
    state_path.write_text('{"applied_patches": {}, "launch_checks": "nope"}')

    assert not launch_blocked(state_path, "flip_laa_flag", "dinput8_shim")

    # and writing over it repairs the shape rather than inheriting it
    record_launch(state_path, "flip_laa_flag", "drm_error", "", "dinput8_shim")
    assert launch_blocked(state_path, "flip_laa_flag", "dinput8_shim")


def test_clearing_a_record_lets_a_user_try_again(tmp_path: Path):
    from patcher.state import clear_launch_record, launch_blocked, record_launch

    state_path = tmp_path / "state.json"
    record_launch(state_path, "flip_laa_flag", "drm_error", "", "dinput8_shim")
    clear_launch_record(state_path, "flip_laa_flag")

    assert not launch_blocked(state_path, "flip_laa_flag", "dinput8_shim")
    # a clear on something never recorded is a no-op, not a crash
    clear_launch_record(state_path, "nothing_here")


def test_a_record_with_no_cover_on_it_blocks_nothing(tmp_path: Path):
    """
    What the shipped build wrote, and why the next mechanism was unreachable.

    A refusal used to be filed against the patch alone, which reads as "this
    install cannot take the 4GB patch". It was never that. It was a refusal of
    the NovaFix add-on, which loads at render init and could not have beaten a
    check that runs in the entry point. Those records describe something that
    is not installed any more, so they decide nothing.
    """
    import json

    from patcher.state import launch_blocked

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"applied_patches": {}, "launch_checks": {
        "flip_laa_flag": {"verdict": "drm_error", "detail": "Steam refused it",
                          "checked_at": "2026-09-18T20:22:53+00:00"}}}))

    assert not launch_blocked(state_path, "flip_laa_flag", "dinput8_shim")
    assert not launch_blocked(state_path, "flip_laa_flag", "proxy_config")
    assert not launch_blocked(state_path, "flip_laa_flag", "")


def test_a_deleted_download_stops_reporting_as_applied(tmp_path: Path):
    """
    A downloaded mod had no pinned hash and never could, because the version
    moves, so it fell back to "we have a note saying we did this". That note
    stays true after the user deletes the file, and the row kept saying it was
    there.
    """
    from patcher.state import applied_recorder

    install = tmp_path / "game"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "mod.dll").write_bytes(b"MZ" + b"\xaa" * 500)

    manifest = _manifest_with_patch(type="file_install", install_relative_path="bin")
    state_path = tmp_path / "state.json"
    record = applied_recorder(install, manifest, state_path)
    record(_result("thing", ["mod.dll"]))

    assert scan_patch_status(install, manifest, state_path)["thing"] is True

    (install / "bin" / "mod.dll").unlink()
    assert scan_patch_status(install, manifest, state_path)["thing"] is False


def test_a_replaced_download_stops_reporting_as_applied(tmp_path: Path):
    """
    The harder half, and the one presence alone cannot see. Steam's verify, a
    different mod writing the same filename, or the user dropping another build
    in all leave a file with the right name and the wrong contents.
    """
    from patcher.state import applied_recorder

    install = tmp_path / "game"
    (install / "bin").mkdir(parents=True)
    target = install / "bin" / "mod.dll"
    target.write_bytes(b"MZ" + b"\xaa" * 500)

    manifest = _manifest_with_patch(type="file_install", install_relative_path="bin")
    state_path = tmp_path / "state.json"
    applied_recorder(install, manifest, state_path)(_result("thing", ["mod.dll"]))
    assert scan_patch_status(install, manifest, state_path)["thing"] is True

    target.write_bytes(b"MZ" + b"\xbb" * 500)     # same name, somebody else's file
    assert scan_patch_status(install, manifest, state_path)["thing"] is False


def test_a_record_without_digests_still_falls_back_to_presence(tmp_path: Path):
    """
    installs written by an older build have no digests. they must not all
    suddenly report as missing, which would mean re-running every patch on
    every existing install.
    """
    install = tmp_path / "game"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "mod.dll").write_bytes(b"MZ")

    manifest = _manifest_with_patch(type="file_install", install_relative_path="bin")
    state_path = tmp_path / "state.json"
    record_applied(state_path, "thing", ["mod.dll"])        # no digests, as before

    assert scan_patch_status(install, manifest, state_path)["thing"] is True
    (install / "bin" / "mod.dll").unlink()
    assert scan_patch_status(install, manifest, state_path)["thing"] is False


def test_a_nexus_mod_is_checked_the_same_way(tmp_path: Path):
    """it lands files like any other install and was answered by the record alone"""
    from patcher.state import applied_recorder

    install = tmp_path / "game"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "outfit.bin").write_bytes(b"\x01" * 64)

    manifest = _manifest_with_patch(type="nexus", install_relative_path="bin")
    state_path = tmp_path / "state.json"
    applied_recorder(install, manifest, state_path)(_result("thing", ["outfit.bin"]))

    assert scan_patch_status(install, manifest, state_path)["thing"] is True
    (install / "bin" / "outfit.bin").write_bytes(b"\x02" * 64)
    assert scan_patch_status(install, manifest, state_path)["thing"] is False


def test_the_header_check_does_not_read_the_whole_executable(tmp_path: Path):
    """
    the scan runs on every game switch, and reading 38MB to look at one bit in
    the header is most of the delay between opening a game and its rows knowing
    what they are
    """
    from patcher.patch_ops import flip_laa_flag
    from patcher.state import _checked
    from patcher.patch_ops import is_laa_enabled

    target = tmp_path / "game.exe"
    head = make_fake_pe(characteristics=0x0000)
    target.write_bytes(head + b"\x00" * (8 * 1024 * 1024))

    reads = []
    real_open = Path.open

    def counting_open(self, *a, **kw):
        handle = real_open(self, *a, **kw)
        if self == target:
            real_read = handle.read

            def read(size=-1):
                block = real_read(size)
                reads.append(len(block))
                return block
            handle.read = read
        return handle

    Path.open = counting_open
    try:
        assert _checked(is_laa_enabled, target) is False
        target.write_bytes(flip_laa_flag(head) + b"\x00" * (8 * 1024 * 1024))
        assert _checked(is_laa_enabled, target) is True
    finally:
        Path.open = real_open

    assert reads and max(reads) <= 64 * 1024, f"read {max(reads)} bytes for a header bit"


def test_a_patch_in_the_addons_folder_is_checked_where_it_was_written(tmp_path: Path):
    """
    An `addons` segment in a manifest path is a placeholder, not a folder name.
    NovaFix scans the first of addons/extra/extras/mods/dll that exists, so on
    an install that already had `mods/` that is where the engine writes. The
    scan resolved the raw path instead, looked in a folder nothing was written
    to, found no digests, and reported the XIII-2 audio fix as not applied on
    every run.
    """
    from patcher.state import applied_recorder

    install = tmp_path / "game"
    proxy = install / "bin"
    # the folder NovaFix would actually pick, created before anything is applied
    (proxy / "mods").mkdir(parents=True)
    (proxy / "mods" / "mod.dll").write_bytes(b"MZ" + b"\xaa" * 500)

    manifest = _manifest_with_patch(type="file_install",
                                    install_relative_path="bin/addons")
    state_path = tmp_path / "state.json"
    applied_recorder(install, manifest, state_path)(_result("thing", ["mod.dll"]))

    recorded = load_state(state_path)["applied_patches"]["thing"]
    assert recorded.get("digests"), "nothing was hashed, so the file was never found"
    assert scan_patch_status(install, manifest, state_path)["thing"] is True

    (proxy / "mods" / "mod.dll").write_bytes(b"MZ" + b"\xbb" * 500)
    assert scan_patch_status(install, manifest, state_path)["thing"] is False


def test_a_refusal_with_nothing_covering_it_counts_only_with_what_was_seen(tmp_path: Path):
    """LR's: the only thing keeping its patch off, and src_38 filed one for any fast exit"""
    from patcher.engine import COVER_NONE, COVER_SHIM
    from patcher.launch_watch import DRM_ERROR
    from patcher.state import launch_blocked, load_state, record_launch

    state_path = tmp_path / "state.json"
    record_launch(state_path, "flip_laa_flag", DRM_ERROR, "refused", COVER_NONE)
    assert not launch_blocked(state_path, "flip_laa_flag", COVER_NONE)
    record_launch(state_path, "flip_laa_flag", DRM_ERROR, "refused", COVER_NONE,
                  evidence={"ran": 0.6, "window": False, "code": "0x00000000"})
    assert launch_blocked(state_path, "flip_laa_flag", COVER_NONE)
    assert load_state(state_path)["launch_checks"]["flip_laa_flag"]["evidence"]["code"] == \
        "0x00000000"
    # XIII's, against a cover, stand as they were filed
    record_launch(state_path, "flip_laa_flag", DRM_ERROR, "refused", COVER_SHIM)
    assert launch_blocked(state_path, "flip_laa_flag", COVER_SHIM)
