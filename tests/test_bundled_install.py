"""
The bundled file install: XIII-2's audio fix ships with the patcher and lands
in NovaFix's add-on folder on Apply.
"""

import hashlib
import struct
from pathlib import Path

import pytest

from patcher.engine import PatchStatus, run_patches
from patcher.manifest import load_manifest

ROOT = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "assets" / "mods" / "xiii2-audio"
DLL = AUDIO / "xiii2_audio.asi"
NOTICES = AUDIO / "LICENSE-MinHook.txt"


def manifest(stub=None):
    return load_manifest(stub.manifest("ffxiii2") if stub
                         else ROOT / "manifests" / "ffxiii2.json")


def patch():
    return next(p for p in manifest().patches if p.ui_job_id == "audio")


def fake_game(tmp_path):
    m = manifest()
    root = tmp_path / "FFXIII2"
    exe = root / m.executable_relative_path
    exe.parent.mkdir(parents=True)
    header = bytearray(0x80)
    struct.pack_into("<I", header, 0x3C, 0x80)
    exe.write_bytes(bytes(header) + b"PE\x00\x00" + bytes(20) + b"pad")
    return root


def no_network(url):
    raise AssertionError("a bundled install must not touch the network")


def test_the_dll_ships_and_matches_its_checksum():
    assert DLL.is_file()
    digest = hashlib.sha256(DLL.read_bytes()).hexdigest()
    spec = next(f for f in patch().bundled_files if f["target"] == "xiii2_audio.asi")
    assert spec["sha256"] == digest


def test_the_licence_notices_ship_with_the_binary():
    """
    MinHook requires its copyright notice to be reproduced in the materials
    provided with any binary redistribution. that file is a licence
    obligation, not documentation, so it must actually be present.
    """
    assert NOTICES.is_file()
    assert "Tsuda Kageyu" in NOTICES.read_text(encoding="utf-8", errors="replace")
    assert "LICENSE-MinHook.txt" in [f["target"] for f in patch().bundled_files]


def test_it_installs_into_the_addons_folder(stub_bundle, tmp_path):
    """
    NovaFix loads DLL/ASI mods from addons/, folders and all, so the add-on
    gets one of its own. no filename collision, so nothing has to be renamed
    out of anyone's way.
    """
    root = fake_game(tmp_path)
    run_patches(root, manifest(stub_bundle), {"install_novafix"}, fetch=no_network)
    results = run_patches(root, manifest(stub_bundle), {"install_audio_fix"}, fetch=no_network,
                          already_applied_check=lambda pid: pid == "install_novafix")
    result = next(r for r in results if r.patch_id == "install_audio_fix")
    assert result.status == PatchStatus.APPLIED, result.detail
    landed = root / "alba_data" / "prog" / "win" / "bin" / "addons" / "xiii2-audio" / "xiii2_audio.asi"
    assert landed.read_bytes() == (stub_bundle.root / "assets" / "mods" / "xiii2-audio" / "xiii2_audio.asi").read_bytes()
    assert (landed.parent / "xiii2-audio.ini").is_file()
    # addons sits beside the proxy, which sits beside the exe
    assert (landed.parent.parent.parent / "ffxiii2img.exe").is_file()


def test_a_tampered_asset_is_refused_before_writing(tmp_path, monkeypatch):
    root = fake_game(tmp_path)
    fake_root = tmp_path / "bundle"
    target = fake_root / "assets" / "mods" / "xiii2-audio"
    target.mkdir(parents=True)
    (target / "xiii2_audio.asi").write_bytes(b"not the real dll")
    for name in ("xiii2-audio.ini", "LICENSE-MinHook.txt"):
        (target / name).write_bytes((AUDIO / name).read_bytes())
    monkeypatch.setattr("patcher.paths.bundled_data_root", lambda: fake_root)

    results = run_patches(root, manifest(), {"install_audio_fix"}, fetch=no_network,
                          already_applied_check=lambda pid: pid == "install_novafix")
    result = next(r for r in results if r.patch_id == "install_audio_fix")

    assert result.status == PatchStatus.FAILED
    # shape is checked before the hash, because "isn't a Windows program file"
    # names the actual problem and "does not match its checksum" does not
    assert "Windows program file" in result.detail
    assert not (root / "alba_data" / "prog" / "win" / "bin" / "addons" / "xiii2-audio" / "xiii2_audio.asi").exists()


def test_a_valid_looking_asset_that_is_not_ours_is_still_refused(tmp_path, monkeypatch):
    """
    the case the pin exists for: a real 32-bit dll, just not the one we ship.
    shape validation cannot catch that, and a swapped file reaching a game
    folder is the thing a committed checksum is there to prevent.
    """
    from tests.helpers import make_fake_pe

    root = fake_game(tmp_path)
    fake_root = tmp_path / "bundle"
    target = fake_root / "assets" / "mods" / "xiii2-audio"
    target.mkdir(parents=True)
    (target / "xiii2_audio.asi").write_bytes(make_fake_pe(machine=0x014C))
    for name in ("xiii2-audio.ini", "LICENSE-MinHook.txt"):
        (target / name).write_bytes((AUDIO / name).read_bytes())
    monkeypatch.setattr("patcher.paths.bundled_data_root", lambda: fake_root)

    results = run_patches(root, manifest(), {"install_audio_fix"}, fetch=no_network,
                          already_applied_check=lambda pid: pid == "install_novafix")
    result = next(r for r in results if r.patch_id == "install_audio_fix")

    assert result.status == PatchStatus.FAILED
    assert "not the build this patcher ships" in result.detail
    assert not (root / "alba_data" / "prog" / "win" / "bin" / "addons" / "xiii2-audio" / "xiii2_audio.asi").exists()


def test_it_is_ours_and_says_so():
    """
    it replaced a mod that wasn't ours to ship, so nothing of that one may be
    left in the bundle or credited as what's installed
    """
    assert "made for this patcher" in patch().credit
    assert not (ROOT / "assets" / "mods" / "ffxiii2-audiofix").exists()
    assert not (ROOT / "tests" / "data" / "novafix-stub" / "assets" / "mods" / "ffxiii2-audiofix").exists()


NOVAFIX = ROOT / "assets" / "mods" / "novafix" / "d3d9.dll"


def novafix_patch(game="ffxiii2"):
    return next(p for p in load_manifest(ROOT / "manifests" / f"{game}.json").patches
                if p.id == "install_novafix")


def test_only_assets_we_actually_ship_are_pinned():
    """
    A hash pin says "this is the build we vouch for". That only holds for a file
    committed to this repo. Pinning one the USER downloads turns a newer NovaFix
    release into "does not match its checksum", which is exactly what happened on
    a second machine with a perfectly good dll.
    """
    from patcher.manifest import load_manifest

    for name in ("ffxiii", "ffxiii2", "ffxiii3"):
        for patch in load_manifest(ROOT / "manifests" / f"{name}.json").patches:
            for spec in (patch.bundled_files or []):
                committed = (ROOT / spec["source"]).is_file()
                pinned = "sha256" in spec
                assert pinned == committed, (
                    f"{spec['source']}: committed={committed} but pinned={pinned}. "
                    "pin what we ship, shape-check what the user supplies")


def test_no_pinned_asset_is_a_file_git_may_rewrite():
    """
    A pin is a promise about bytes, so git has to hand every platform the same
    ones. "* text=auto eol=lf" does not: a readme that used to ship beside the XIII-2 audio fix
    is plain text, it was pinned from a CRLF working tree, and every clone made
    after that rule landed checked it out as LF. The hash missed, and a user
    with a perfectly good bundle was told to redownload the patcher.

    Checked through git rather than by looking for CRLF in the file, because a
    real binary can contain those two bytes and would fail that test for no
    reason. What matters is whether git considers the file its to normalise.
    """
    import subprocess

    from patcher.manifest import load_manifest

    sources = sorted({spec["source"]
                      for name in ("ffxiii", "ffxiii2", "ffxiii3")
                      for patch in load_manifest(ROOT / "manifests" / f"{name}.json").patches
                      for spec in (patch.bundled_files or [])
                      if "sha256" in spec})
    assert sources, "no pinned assets found, this test has stopped checking anything"

    try:
        result = subprocess.run(["git", "check-attr", "text", "--", *sources],
                                capture_output=True, text=True, cwd=ROOT, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("no git available to ask")
    if result.returncode != 0:
        pytest.skip("not a git checkout")

    # "<path>: text: unset" is the -text we want. "auto" or "set" both mean git
    # rewrites line endings on checkout, which is what broke the pin.
    normalised = [line for line in result.stdout.splitlines()
                  if line.strip() and not line.endswith(": text: unset")]
    assert not normalised, (
        "these are pinned but git may rewrite their line endings, so their hash "
        "differs by platform. add them to .gitattributes as -text:\n  "
        + "\n  ".join(normalised))


def test_the_supplied_proxy_is_checked_for_shape_instead():
    """it cannot be pinned, but it can be caught being the wrong thing entirely"""
    from patcher.engine import unusable_binary

    assert unusable_binary("d3d9.dll", b"<!DOCTYPE html>404 not found")
    assert "64-bit" in unusable_binary("d3d9.dll", _pe(machine=0x8664))
    assert unusable_binary("d3d9.dll", _pe()) is None


def _pe(machine=0x014C):
    import struct
    dos = bytearray(0x40); dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)
    return bytes(dos) + b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 18


def test_a_supplied_proxy_of_any_build_is_accepted():
    """
    the fix for a real failure: a different NovaFix build on a second machine
    was refused outright. any real 32-bit dll has to be usable.
    """
    from patcher.engine import unusable_binary

    if not NOVAFIX.is_file():
        pytest.skip("no d3d9.dll present; nothing to check")
    assert unusable_binary("d3d9.dll", NOVAFIX.read_bytes()) is None


def test_every_recorded_hash_is_lowercase_hex():
    """PowerShell's Get-FileHash returns uppercase; the comparison is case
    insensitive but a consistent file is easier to diff"""
    from patcher.manifest import load_manifest

    for name in ("ffxiii", "ffxiii2", "ffxiii3"):
        for patch in load_manifest(ROOT / "manifests" / f"{name}.json").patches:
            for spec in (patch.bundled_files or []):
                digest = spec.get("sha256")
                if not digest:
                    continue
                assert digest == digest.lower()
                assert len(digest) == 64
                assert all(c in "0123456789abcdef" for c in digest)


def test_every_committed_asset_is_pinned_to_what_is_on_disk():
    """
    A pin that drifts from the file is worse than no pin: it fails a run that
    would have worked, and the message blames the user's install. Replacing a
    bundled binary without repinning is the obvious way to get there, so the
    check that catches it runs here rather than only in CI.

    tools/pin_bundled.py fixes it.
    """
    import subprocess
    import sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "tools/pin_bundled.py", "--check"],
        capture_output=True, text=True, cwd=repo)

    assert result.returncode == 0, result.stdout + result.stderr


def novafix_run(root, stub, state):
    """one Apply of NovaFix wired the way the app wires it"""
    from patcher.state import applied_recorder, record_reader

    m = manifest(stub)
    results = run_patches(root, m, {"install_novafix"}, fetch=no_network,
                          on_applied=applied_recorder(root, m, state),
                          recorded=record_reader(state))
    return next(r for r in results if r.patch_id == "install_novafix")


def revert_novafix(root, stub, state):
    from patcher.revert import revert_patch

    m = manifest(stub)
    return revert_patch(root, m, next(p for p in m.patches if p.id == "install_novafix"), state)


def test_their_dll_is_set_aside_and_revert_puts_it_back(stub_bundle, tmp_path):
    """d3d9.dll is ReShade's and DXVK's name too, and installing over one lost it for good"""
    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    (bin_dir / "d3d9.dll").write_bytes(b"MZ reshade")
    state = tmp_path / "state.json"

    result = novafix_run(root, stub_bundle, state)

    assert result.status == PatchStatus.APPLIED, result.detail
    assert (bin_dir / "d3d9.dll.backup").read_bytes() == b"MZ reshade"
    assert "d3d9.dll.backup" in result.detail

    undone = revert_novafix(root, stub_bundle, state)

    assert undone.ok, undone.detail
    assert (bin_dir / "d3d9.dll").read_bytes() == b"MZ reshade"
    assert not (bin_dir / "d3d9.dll.backup").exists()


def test_a_second_apply_keeps_their_copy(stub_bundle, tmp_path):
    """the second run finds ours in the slot, and theirs has to outlive that"""
    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    (bin_dir / "d3d9.dll").write_bytes(b"MZ reshade")
    state = tmp_path / "state.json"

    novafix_run(root, stub_bundle, state)
    again = novafix_run(root, stub_bundle, state)

    assert again.status == PatchStatus.APPLIED, again.detail
    assert (bin_dir / "d3d9.dll.backup").read_bytes() == b"MZ reshade"
    assert revert_novafix(root, stub_bundle, state).ok
    assert (bin_dir / "d3d9.dll").read_bytes() == b"MZ reshade"


def test_an_empty_dll_is_not_set_aside(stub_bundle, tmp_path):
    """a 0-byte dll kept as .backup comes back on Revert as a game that won't start"""
    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    (bin_dir / "d3d9.dll").write_bytes(b"")
    state = tmp_path / "state.json"

    assert novafix_run(root, stub_bundle, state).status == PatchStatus.APPLIED
    assert not (bin_dir / "d3d9.dll.backup").exists()
    assert revert_novafix(root, stub_bundle, state).ok
    assert not (bin_dir / "d3d9.dll").exists()


def test_revert_drops_an_empty_backup_an_older_build_kept(stub_bundle, tmp_path):
    import json

    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    (bin_dir / "d3d9.dll").write_bytes(b"MZ reshade")
    state = tmp_path / "state.json"
    novafix_run(root, stub_bundle, state)
    # what an install from before the empty check left behind
    (bin_dir / "d3d9.dll.backup").write_bytes(b"")
    record = json.loads(state.read_text())
    record["applied_patches"]["install_novafix"]["kept"]["d3d9.dll"] = hashlib.sha256(b"").hexdigest()
    state.write_text(json.dumps(record))

    assert revert_novafix(root, stub_bundle, state).ok
    assert not (bin_dir / "d3d9.dll").exists()
    assert not (bin_dir / "d3d9.dll.backup").exists()


def test_our_older_build_is_not_mistaken_for_theirs(stub_bundle, tmp_path):
    """
    a release with a new NovaFix finds last release's in the slot. We recorded
    installing that one, so Revert must not hand it back as theirs.
    """
    from patcher.state import load_state, save_state

    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    state = tmp_path / "state.json"
    novafix_run(root, stub_bundle, state)

    older = b"MZ last release"
    (bin_dir / "d3d9.dll").write_bytes(older)
    saved = load_state(state)
    saved["applied_patches"]["install_novafix"]["digests"]["d3d9.dll"] = \
        hashlib.sha256(older).hexdigest()
    save_state(state, saved)

    result = novafix_run(root, stub_bundle, state)

    assert result.status == PatchStatus.APPLIED, result.detail
    assert not (bin_dir / "d3d9.dll.backup").exists()


def test_revert_takes_the_log_novafix_wrote_each_launch(stub_bundle, tmp_path):
    """never on its record, since NovaFix wrote it, and nothing reads it once NovaFix is off"""
    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    state = tmp_path / "state.json"
    novafix_run(root, stub_bundle, state)
    (bin_dir / "NovaFix.log").write_text("[info] hooked Direct3DCreate9")

    assert revert_novafix(root, stub_bundle, state).ok
    assert not (bin_dir / "NovaFix.log").exists()


def test_a_dll_revert_left_keeps_its_log(stub_bundle, tmp_path):
    """somebody's NovaFix now, maybe a newer one, still writing it"""
    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    state = tmp_path / "state.json"
    novafix_run(root, stub_bundle, state)
    (bin_dir / "d3d9.dll").write_bytes(b"MZ newer novafix")
    (bin_dir / "NovaFix.log").write_text("[info] hooked Direct3DCreate9")

    assert revert_novafix(root, stub_bundle, state).ok
    assert (bin_dir / "NovaFix.log").is_file()


def test_revert_leaves_a_dll_that_changed_after_the_install(stub_bundle, tmp_path):
    """the digest we recorded says whose file it is now, the name doesn't"""
    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    state = tmp_path / "state.json"
    novafix_run(root, stub_bundle, state)
    (bin_dir / "d3d9.dll").write_bytes(b"MZ dxvk")

    undone = revert_novafix(root, stub_bundle, state)

    assert undone.ok, undone.detail
    assert (bin_dir / "d3d9.dll").read_bytes() == b"MZ dxvk"
    assert "d3d9.dll" in undone.detail


def test_an_ff13fix_we_installed_is_not_kept_as_theirs(stub_bundle, tmp_path):
    """
    NovaFix replaces FF13Fix, and the old record still says whose d3d9.dll that
    is. Kept as theirs, Revert would bring FF13Fix back live without its ini.
    """
    from patcher.state import record_applied

    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    ff13fix = b"MZ ff13fix"
    (bin_dir / "d3d9.dll").write_bytes(ff13fix)
    state = tmp_path / "state.json"
    record_applied(state, "install_ff13fix", ["d3d9.dll"],
                   {"digests": {"d3d9.dll": hashlib.sha256(ff13fix).hexdigest()}})

    result = novafix_run(root, stub_bundle, state)

    assert result.status == PatchStatus.APPLIED, result.detail
    assert not (bin_dir / "d3d9.dll.backup").exists()


def test_a_hand_edited_record_still_installs(stub_bundle, tmp_path):
    from patcher.state import record_applied

    root = fake_game(tmp_path)
    state = tmp_path / "state.json"
    record_applied(state, "install_novafix", ["d3d9.dll"], {"digests": ["not", "a", "dict"]})

    assert novafix_run(root, stub_bundle, state).status == PatchStatus.APPLIED


def test_novafix_says_when_a_dll_beside_it_will_switch_it_off(stub_bundle, tmp_path):
    """FF13Fix PLUS left in by hand as dinput8.dll, say: NovaFix stands down for it"""
    root = fake_game(tmp_path)
    bin_dir = root / "alba_data" / "prog" / "win" / "bin"
    (bin_dir / "dinput8.dll").write_bytes(b"MZ\0FF13Fix.ini\0Final Fantasy XIII\0")
    (bin_dir / "winmm.dll").write_bytes(b"MZ\0nothing of FF13Fix's\0")
    result = novafix_run(root, stub_bundle, tmp_path / "state.json")
    assert result.status == PatchStatus.APPLIED, result.detail
    assert "dinput8.dll beside it is built on FF13Fix, so NovaFix turns its own fixes off" \
        in result.detail, result.detail
    assert "winmm.dll" not in result.detail


def their_audio_fix() -> bytes:
    """a dll named the way cyanea-bt's FFXIII-2 AudioFix names itself"""
    from patcher import pe_name
    from tests.test_pe_name import dll

    return dll(resources={16: {1: {0x409: pe_name.version_info("FFXIII-2 AudioFix")}}})


@pytest.mark.parametrize("where", [
    "alba_data/prog/win/bin/winmm.dll",                             # their readme's manual install
    "alba_data/prog/win/bin/addons/winmm.dll",                      # where this patcher used to put it
    "alba_data/prog/win/bin/addons/FFXIII-2_AudioFix/winmm.dll",    # dropped in through the Mods drawer
])
def test_their_audio_fix_already_in_keeps_ours_out(stub_bundle, tmp_path, where):
    """
    people come to this with the community fix already installed and nobody
    will know to take it out. Both at once is two sets of hooks on the same
    sound code, so theirs is left to do the job and ours says why it held off
    """
    root = fake_game(tmp_path)
    theirs = root / where
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_bytes(their_audio_fix())

    results = run_patches(root, manifest(stub_bundle), {"install_audio_fix"}, fetch=no_network,
                          already_applied_check=lambda pid: pid == "install_novafix")
    result = next(r for r in results if r.patch_id == "install_audio_fix")

    assert result.status == PatchStatus.SKIPPED
    assert "FFXIII-2 AudioFix is already installed" in result.detail and where in result.detail
    assert not (root / "alba_data" / "prog" / "win" / "bin" / "addons" / "xiii2-audio").exists()
    assert theirs.read_bytes() == their_audio_fix()


def test_only_their_audio_fix_holds_ours_back(stub_bundle, tmp_path):
    """another winmm.dll (an ASI loader, say) and our own earlier install are no reason to stop"""
    from tests.helpers import make_fake_pe

    root = fake_game(tmp_path)
    (root / "alba_data" / "prog" / "win" / "bin" / "winmm.dll").write_bytes(make_fake_pe(machine=0x014C))
    for _ in range(2):
        results = run_patches(root, manifest(stub_bundle), {"install_audio_fix"}, fetch=no_network,
                              already_applied_check=lambda pid: pid == "install_novafix")
        result = next(r for r in results if r.patch_id == "install_audio_fix")
        assert result.status == PatchStatus.APPLIED, result.detail
