from pathlib import Path

from patcher.engine import (
    PatchStatus,
    apply_binary_patch,
    apply_file_install,
    backup_path_for,
    ensure_backup,
    restore_backup,
    run_patches,
)
from patcher.manifest import GameManifest, Patch
from patcher.patch_ops import is_laa_enabled
from tests.helpers import make_fake_pe


def make_manifest(patches: list[Patch]) -> GameManifest:
    return GameManifest(
        game_id="test_game",
        display_name="Test Game",
        manifest_version="v1.0.0",
        executable_relative_path="game.exe",
        patches=patches,
    )


def laa_patch(requires_backup: bool) -> Patch:
    return Patch(
        id="flip_laa_flag",
        label="4GB Patch",
        type="binary_patch",
        default_enabled=True,
        requires_backup=requires_backup,
        operation="flip_laa_flag",
    )


def test_ensure_backup_creates_a_copy(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe())

    backup = ensure_backup(target)

    assert backup.exists()
    assert backup.read_bytes() == target.read_bytes()


def test_ensure_backup_uses_required_filename_when_given(tmp_path: Path):
    """
    this is the real bug that shipped: steam's DRM on FFXIII's exe
    checks for a file named exactly untouched.exe, not our generic
    .backup suffix. an earlier version of this code always used the
    generic name regardless, which produced a valid backup that steam
    just didn't recognize - patch applied fine, game wouldn't launch.
    """
    target = tmp_path / "ffxiiiimg.exe"
    target.write_bytes(make_fake_pe())

    backup = ensure_backup(target, required_filename="untouched.exe")

    assert backup.name == "untouched.exe"
    assert backup == tmp_path / "untouched.exe"
    assert not (tmp_path / "ffxiiiimg.exe.backup").exists()
    assert backup.read_bytes() == target.read_bytes()


def test_ensure_backup_wont_overwrite_existing_backup(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))

    ensure_backup(target)  # first backup, captures the real original

    # simulate the file getting patched after the backup was made
    target.write_bytes(make_fake_pe(characteristics=0x0020))
    ensure_backup(target)  # should NOT clobber the backup with this

    backup = backup_path_for(target)
    assert is_laa_enabled(backup.read_bytes()) is False  # still the real original


def test_restore_backup_puts_original_back(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))
    ensure_backup(target)

    target.write_bytes(make_fake_pe(characteristics=0x0020))
    restored = restore_backup(target)

    assert restored is True
    assert is_laa_enabled(target.read_bytes()) is False


def test_restore_backup_returns_false_when_no_backup_exists(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe())

    assert restore_backup(target) is False


def test_restore_backup_works_with_required_filename(tmp_path: Path):
    target = tmp_path / "ffxiiiimg.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))
    ensure_backup(target, required_filename="untouched.exe")

    target.write_bytes(make_fake_pe(characteristics=0x0020))
    restored = restore_backup(target, required_filename="untouched.exe")

    assert restored is True
    assert is_laa_enabled(target.read_bytes()) is False


def test_ensure_backup_with_required_filename_wont_overwrite_existing(tmp_path: Path):
    target = tmp_path / "ffxiiiimg.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))
    ensure_backup(target, required_filename="untouched.exe")  # captures the real original

    target.write_bytes(make_fake_pe(characteristics=0x0020))  # simulate it getting patched
    ensure_backup(target, required_filename="untouched.exe")  # should NOT clobber untouched.exe

    untouched = tmp_path / "untouched.exe"
    assert is_laa_enabled(untouched.read_bytes()) is False  # still the real original


def test_apply_binary_patch_patches_the_file(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))

    manifest = make_manifest([laa_patch(requires_backup=True)])
    result = apply_binary_patch(tmp_path, manifest, manifest.patches[0])

    assert result.status == PatchStatus.APPLIED
    assert is_laa_enabled(target.read_bytes()) is True
    assert backup_path_for(target).exists()


def test_apply_binary_patch_uses_required_backup_filename(tmp_path: Path):
    """
    the actual end-to-end wiring for the real bug: a patch with
    required_backup_filename set should produce a backup with THAT
    name, not the generic .backup suffix, when run through the real
    apply_binary_patch entrypoint - not just the lower-level backup
    functions in isolation.
    """
    target = tmp_path / "ffxiiiimg.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))

    patch = Patch(
        id="flip_laa_flag", label="4GB Patch", type="binary_patch",
        default_enabled=True, requires_backup=True,
        required_backup_filename="untouched.exe", operation="flip_laa_flag",
    )
    manifest = GameManifest(
        game_id="ffxiii", display_name="Test Game", manifest_version="v1.0.0",
        executable_relative_path="ffxiiiimg.exe", patches=[patch],
    )

    result = apply_binary_patch(tmp_path, manifest, patch)

    assert result.status == PatchStatus.APPLIED
    assert (tmp_path / "untouched.exe").exists()
    assert not (tmp_path / "ffxiiiimg.exe.backup").exists()


def test_apply_binary_patch_skips_backup_when_not_required(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0000))

    manifest = make_manifest([laa_patch(requires_backup=False)])
    apply_binary_patch(tmp_path, manifest, manifest.patches[0])

    assert not backup_path_for(target).exists()


def test_apply_binary_patch_fails_when_target_missing(tmp_path: Path):
    manifest = make_manifest([laa_patch(requires_backup=False)])
    result = apply_binary_patch(tmp_path, manifest, manifest.patches[0])

    assert result.status == PatchStatus.FAILED
    assert "couldn't find" in result.detail


def test_apply_binary_patch_handles_permission_error_on_write(tmp_path: Path, monkeypatch):
    """
    the real bug this catches: a decent share of real users have steam
    installed under C:\\Program Files (x86)\\Steam, which windows
    protects - writing there without admin rights raises
    PermissionError. that wasn't caught before, so it would crash the
    whole run with a raw traceback instead of failing gracefully like
    everything else in this tool. can't rely on real OS permissions to
    test this (this sandbox runs as root, which bypasses them), so the
    write itself is mocked to raise it deterministically.
    """
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe())
    manifest = make_manifest([laa_patch(requires_backup=False)])

    def raise_permission_error(self, data):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(Path, "write_bytes", raise_permission_error)

    result = apply_binary_patch(tmp_path, manifest, manifest.patches[0])

    assert result.status == PatchStatus.FAILED
    assert "permission" in result.detail.lower()
    # the advice half is decided by whether we are already elevated, so it is
    # not in the message on a platform with no such thing. what has to be
    # there either way is the path, because "permission denied" alone does not
    # tell anyone which folder to go look at.
    assert str(tmp_path) in result.detail


def test_apply_binary_patch_handles_permission_error_during_backup(tmp_path: Path, monkeypatch):
    target = tmp_path / "ffxiiiimg.exe"
    target.write_bytes(make_fake_pe())

    patch = Patch(
        id="flip_laa_flag", label="4GB Patch", type="binary_patch",
        default_enabled=True, requires_backup=True,
        required_backup_filename="untouched.exe", operation="flip_laa_flag",
    )
    manifest = GameManifest(
        game_id="ffxiii", display_name="Test Game", manifest_version="v1.0.0",
        executable_relative_path="ffxiiiimg.exe", patches=[patch],
    )

    def raise_permission_error(*args, **kwargs):
        raise PermissionError("Access is denied")

    # the backup goes through write_atomic now, not copy2: a truncated
    # untouched.exe is permanent, because ensure_backup refuses to overwrite one
    monkeypatch.setattr("patcher.engine.write_atomic", raise_permission_error)

    result = apply_binary_patch(tmp_path, manifest, patch)

    assert result.status == PatchStatus.FAILED
    assert "permission" in result.detail.lower()
    # the exe itself should be untouched since the backup step failed first
    assert is_laa_enabled(target.read_bytes()) is False


def test_apply_file_install_handles_permission_error(tmp_path: Path, monkeypatch):
    patch = Patch(
        id="install_ff13fix", label="Install FF13Fix", type="file_install",
        default_enabled=True, github_repo="rebtd7/FF13Fix", install_relative_path="bin",
    )
    manifest = make_manifest([patch])

    def raise_permission_error(*args, **kwargs):
        raise PermissionError("Access is denied")

    monkeypatch.setattr("patcher.engine.install_from_url", raise_permission_error)
    monkeypatch.setattr("patcher.engine.latest_release_asset",
                        lambda repo, fetch=None: ("https://example.com/fake.zip", None))

    result = apply_file_install(tmp_path, manifest, patch)

    assert result.status == PatchStatus.FAILED
    assert "permission" in result.detail.lower()
    # the advice half is decided by whether we are already elevated, so it is
    # not in the message on a platform with no such thing. what has to be
    # there either way is the path, because "permission denied" alone does not
    # tell anyone which folder to go look at.
    assert str(tmp_path) in result.detail


def test_apply_binary_patch_skips_unimplemented_operation(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe())

    placeholder = Patch(
        id="apply_ffxiii_fix",
        label="Apply FFXIII Fix",
        type="binary_patch",
        default_enabled=True,
        operation="apply_ffxiii_fix",  # not in the OPERATIONS registry yet
    )
    manifest = make_manifest([placeholder])
    result = apply_binary_patch(tmp_path, manifest, manifest.patches[0])

    assert result.status == PatchStatus.SKIPPED
    assert "no implementation" in result.detail


def test_run_patches_reports_one_result_per_manifest_patch(tmp_path: Path):
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe())

    manifest = make_manifest([
        laa_patch(requires_backup=False),
        Patch(id="frame_pacer_fix", label="Frame Pacer Fix", type="binary_patch",
              default_enabled=True, operation="frame_pacer_fix"),
    ])

    # only enable the LAA patch, leave frame_pacer_fix unselected
    results = run_patches(tmp_path, manifest, enabled_patch_ids={"flip_laa_flag"})

    assert len(results) == 2
    laa_result = next(r for r in results if r.patch_id == "flip_laa_flag")
    frame_result = next(r for r in results if r.patch_id == "frame_pacer_fix")
    assert laa_result.status == PatchStatus.APPLIED
    assert frame_result.status == PatchStatus.SKIPPED
    assert frame_result.detail == "not selected"


def test_run_patches_skips_asset_packs(tmp_path: Path):
    manifest = make_manifest([
        Patch(id="hd_texture_pack", label="HD Texture Pack", type="asset_pack",
              default_enabled=False, source_url="https://example.com/pack.zip"),
    ])

    results = run_patches(tmp_path, manifest, enabled_patch_ids={"hd_texture_pack"})

    assert results[0].status == PatchStatus.SKIPPED
    assert "asset pack" in results[0].detail


def test_dry_run_describes_without_touching_the_file(tmp_path: Path):
    target = tmp_path / "game.exe"
    original_bytes = make_fake_pe(characteristics=0x0000)
    target.write_bytes(original_bytes)

    manifest = make_manifest([laa_patch(requires_backup=True)])
    results = run_patches(tmp_path, manifest, enabled_patch_ids={"flip_laa_flag"}, dry_run=True)

    assert results[0].status == PatchStatus.WOULD_APPLY
    assert "would patch" in results[0].detail
    assert "would back up" in results[0].detail
    # nothing actually happened
    assert target.read_bytes() == original_bytes
    assert not backup_path_for(target).exists()


def test_dry_run_still_reports_already_applied_accurately(tmp_path: Path):
    """
    an already-applied patch should say so in a dry run too, not
    misleadingly claim it "would apply" something that's already done -
    the already-applied check is read-only, so there's no reason to
    skip it just because dry_run is on.
    """
    target = tmp_path / "game.exe"
    target.write_bytes(make_fake_pe(characteristics=0x0020))  # already patched

    manifest = make_manifest([laa_patch(requires_backup=False)])
    results = run_patches(
        tmp_path, manifest, enabled_patch_ids={"flip_laa_flag"},
        already_applied_check=lambda pid: True,
        dry_run=True,
    )

    assert results[0].status == PatchStatus.ALREADY_APPLIED


def test_dry_run_describes_file_install_without_downloading(tmp_path: Path):
    patch = Patch(
        id="install_ff13fix", label="Install FF13Fix", type="file_install",
        default_enabled=True, github_repo="rebtd7/FF13Fix", install_relative_path="bin",
    )
    manifest = make_manifest([patch])

    def fail_if_called(*args, **kwargs):
        raise AssertionError("dry run should never actually fetch anything")

    results = run_patches(
        tmp_path, manifest, enabled_patch_ids={"install_ff13fix"},
        fetch=fail_if_called, dry_run=True,
    )

    assert results[0].status == PatchStatus.WOULD_APPLY
    assert "rebtd7/FF13Fix" in results[0].detail
    assert not (tmp_path / "bin").exists()  # never even created the target dir


def test_dry_run_still_catches_missing_target_file(tmp_path: Path):
    manifest = make_manifest([laa_patch(requires_backup=False)])

    results = run_patches(tmp_path, manifest, enabled_patch_ids={"flip_laa_flag"}, dry_run=True)

    assert results[0].status == PatchStatus.FAILED
    assert "couldn't find" in results[0].detail
