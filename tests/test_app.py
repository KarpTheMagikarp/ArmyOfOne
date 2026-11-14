import json
from pathlib import Path

from patcher.app import run_full_automated_patch
from patcher.engine import PatchStatus
from patcher.manifest import GameManifest, Patch, load_manifest
from patcher.patch_ops import is_laa_enabled
from tests.helpers import make_fake_pe, make_zip_bytes

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_manifest(patches: list[Patch]) -> GameManifest:
    return GameManifest(
        game_id="ffxiii",
        display_name="Test Game",
        manifest_version="v1.0.0",
        executable_relative_path="bin/game.exe",
        patches=patches,
    )


def test_reports_not_found_when_no_install_resolves(tmp_path: Path):
    manifest = make_manifest([])
    outcome = run_full_automated_patch(manifest, state_path=tmp_path / "state.json", steam_library_paths=[])

    assert outcome.resolution.found is False
    assert outcome.results is None


def test_runs_default_enabled_patches_against_a_manual_install(tmp_path: Path):
    install_dir = tmp_path / "install"
    (install_dir / "bin").mkdir(parents=True)
    (install_dir / "bin" / "game.exe").write_bytes(make_fake_pe(characteristics=0x0000))

    manifest = make_manifest([
        Patch(id="flip_laa_flag", label="4GB Patch", type="binary_patch",
              default_enabled=True, operation="flip_laa_flag"),
        Patch(id="hd_texture_pack", label="HD Texture Pack", type="asset_pack",
              default_enabled=False, source_url="https://example.com/pack.zip"),
    ])

    outcome = run_full_automated_patch(
        manifest, state_path=tmp_path / "state.json", manual_path=install_dir
    )

    assert outcome.resolution.found is True
    assert outcome.resolution.source == "manual"

    laa_result = next(r for r in outcome.results if r.patch_id == "flip_laa_flag")
    pack_result = next(r for r in outcome.results if r.patch_id == "hd_texture_pack")

    assert laa_result.status == PatchStatus.APPLIED
    assert pack_result.status == PatchStatus.SKIPPED  # default_enabled is False
    assert is_laa_enabled((install_dir / "bin" / "game.exe").read_bytes()) is True


def test_second_run_recognizes_already_applied_state(tmp_path: Path):
    install_dir = tmp_path / "install"
    (install_dir / "bin").mkdir(parents=True)
    (install_dir / "bin" / "game.exe").write_bytes(make_fake_pe(characteristics=0x0000))
    state_path = tmp_path / "state.json"

    manifest = make_manifest([
        Patch(id="flip_laa_flag", label="4GB Patch", type="binary_patch",
              default_enabled=True, operation="flip_laa_flag"),
    ])

    first = run_full_automated_patch(manifest, state_path=state_path, manual_path=install_dir)
    second = run_full_automated_patch(manifest, state_path=state_path, manual_path=install_dir)

    assert first.results[0].status == PatchStatus.APPLIED
    assert second.results[0].status == PatchStatus.ALREADY_APPLIED


def test_end_to_end_against_the_real_shipped_ffxiii_manifest(stub_bundle, tmp_path):
    """
    XIII after the NovaFix pivot: the proxy installs from a bundled asset, and
    there is deliberately NO 4GB patch.

    editing XIII's CEG executable in place is what trips Steam DRM. FF13Fix
    used to work around that; NovaFix explicitly does not, and its readme says
    other 4GB patches either force FF13Fix or trigger DRM. the Nova Chrysalia
    mod loader is the supported route for that.
    """
    manifest = load_manifest(stub_bundle.manifest("ffxiii"))
    install_dir = tmp_path / "FFXIII"
    exe_path = install_dir / manifest.executable_relative_path
    exe_path.parent.mkdir(parents=True)
    exe_path.write_bytes(make_fake_pe(characteristics=0x0000))

    def offline(url):
        raise AssertionError("the bundled proxy must not need the network")

    outcome = run_full_automated_patch(
        manifest, state_path=tmp_path / "state.json", manual_path=install_dir, fetch=offline
    )

    assert outcome.resolution.found is True
    results_by_id = {r.patch_id: r for r in outcome.results}

    assert results_by_id["install_novafix"].status == PatchStatus.APPLIED
    assert (exe_path.parent / "d3d9.dll").is_file()

    # the 4GB patch is decided per executable. this fixture is a synthetic PE
    # that matches no known XIII build, and an unrecognised executable might be
    # the CEG one, which cannot take the patch without FF13Fix's redirect.
    assert results_by_id["flip_laa_flag"].status == PatchStatus.UNSAFE_BUILD
    assert is_laa_enabled(exe_path.read_bytes()) is False

    # the texture packs are Nexus-hosted extras, off by default
    assert results_by_id["hd_texture_pack"].status == PatchStatus.SKIPPED


def test_end_to_end_against_the_real_shipped_ffxiii2_manifest_uses_alba_data(tmp_path: Path):
    """
    ffxiii2's install folder is alba_data, not white_data like ffxiii -
    this was wrong in an earlier version of the manifest and would have
    made every patch silently fail to find its target. locking that in.
    """
    manifest = load_manifest(REPO_ROOT / "manifests" / "ffxiii2.json")

    assert "alba_data" in manifest.executable_relative_path
    assert "white_data" not in manifest.executable_relative_path

    install_dir = tmp_path / "install"
    exe_path = install_dir / manifest.executable_relative_path
    exe_path.parent.mkdir(parents=True)
    exe_path.write_bytes(make_fake_pe(characteristics=0x0000))

    outcome = run_full_automated_patch(
        manifest, state_path=tmp_path / "state.json", manual_path=install_dir,
        fetch=lambda url: (_ for _ in ()).throw(ConnectionError("no network in this test")),
    )

    assert outcome.resolution.found is True
    laa_result = next(r for r in outcome.results if r.patch_id == "flip_laa_flag")
    assert laa_result.status == PatchStatus.APPLIED
