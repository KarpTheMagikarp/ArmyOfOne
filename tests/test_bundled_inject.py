"""
The bundled path: a mod that ships with the patcher and installs on Apply with
no download, no mod page, no drop and no prompt.

Leviathan's Tears was the one, and it's retired: no row, no payload, never
applied. Its entry stays so what an earlier version put in still comes off,
and the path itself is held here on tests.rain's stand-in, which is that entry
as it was.
"""

import hashlib
import json
import shutil
import struct
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from patcher.archive import filelist
from patcher.archive.entry import SECTOR
from patcher.engine import PatchStatus, run_patches
from patcher.manifest import load_manifest
from patcher.revert import revert_patch
from patcher.state import load_state, record_applied
from tests import rain

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "data" / "filelistu_sample.bin"
WEATHER = rain.WEATHER


def shipped():
    return load_manifest(ROOT / "manifests" / "ffxiii2.json")


def retired():
    return next(p for p in shipped().patches if p.id == rain.LT_ID)


def fake_game(tmp_path):
    if not SAMPLE.is_file():
        pytest.skip("no sample filelist")
    m = shipped()
    root = tmp_path / "FFXIII2"
    sys_dir = root / "alba_data" / "sys"
    sys_dir.mkdir(parents=True)
    exe = root / m.executable_relative_path
    exe.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(0x80)
    struct.pack_into("<I", header, 0x3C, 0x80)
    exe.write_bytes(bytes(header) + b"PE\x00\x00" + bytes(20) + b"pad")

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
                # as long as the index says, since Apply checks a pair reads back first
                handle.write(zlib.compress((b"vanilla rain" * 8000)[:entry.uncompressed], 9))
    return root


def archives(root):
    """the indexes whole and the images where the weather sits: they're sparse gigabytes"""
    sys_dir = root / "alba_data" / "sys"
    index = filelist.parse(SAMPLE.read_bytes())
    out = {}
    for variant in ("u", "c"):
        out[variant] = (sys_dir / f"filelist{variant}.win32.bin").read_bytes()
        image = sys_dir / f"white_img{variant}.win32.bin"
        out[variant, "size"] = image.stat().st_size
        with image.open("rb") as handle:
            for path in WEATHER:
                _, entry = index.locate(path)
                handle.seek(entry.byte_offset)
                out[variant, path] = handle.read(entry.sector_span * SECTOR)
    return out


def lt_on(manifest, **spec):
    """the stand-in's entry with its injections changed"""
    return replace(manifest, patches=[
        replace(p, injections=[dict(s, **spec) for s in p.injections]) if p.id == rain.LT_ID
        else p for p in manifest.patches])


def test_it_is_off_apply_and_nothing_of_it_ships():
    patch = retired()
    assert patch.retired and not patch.default_enabled
    assert not any("bundled" in spec or "source" in spec for spec in patch.injections)
    assert not (ROOT / "assets" / "mods" / "leviathans-tears").exists()
    jobs = json.loads((ROOT / "ui" / "jobs.json").read_text(encoding="utf-8"))
    rows = {j["id"] for g in jobs["games"] for j in g["jobs"]}
    assert patch.ui_job_id not in rows
    # nor a copy of it anywhere else, which the test bundle kept for a while
    pins = {spec["sha256"] for spec in patch.injections}
    copies = [path for path in ROOT.rglob("*") if ".git" not in path.parts and path.is_file()
              and path.stat().st_size == len(rain.PAYLOAD)
              and hashlib.sha256(path.read_bytes()).hexdigest() in pins]
    assert copies == []


def test_asked_for_anyway_it_is_skipped_and_nothing_is_written(tmp_path):
    """a page from before it went, still sending its job"""
    root = fake_game(tmp_path)
    before = archives(root)

    def no_network(url):
        raise AssertionError("a retired patch fetches nothing")

    results = run_patches(root, shipped(), {rain.LT_ID}, undo_store=tmp_path / "undo",
                          fetch=no_network)
    result = next(r for r in results if r.patch_id == rain.LT_ID)

    assert result.status == PatchStatus.SKIPPED
    assert result.detail == "isn't part of the patcher any more"
    assert archives(root) == before


def test_an_install_from_before_it_retired_still_comes_off(tmp_path):
    """applied the way an earlier version did, taken off by this one's entry"""
    root = fake_game(tmp_path)
    before = archives(root)
    state = tmp_path / "state.json"
    results = run_patches(root, rain.manifest(), {rain.LT_ID}, undo_store=tmp_path / "undo",
                          on_applied=lambda r: record_applied(state, r.patch_id,
                                                              r.installed_files, r.revert_info))
    assert next(r for r in results if r.patch_id == rain.LT_ID).status == PatchStatus.APPLIED
    assert archives(root) != before

    result = revert_patch(root, shipped(), retired(), state)

    assert result.ok, result.detail
    assert archives(root) == before
    assert rain.LT_ID not in load_state(state)["applied_patches"]


def test_apply_installs_a_bundled_injection_with_no_interaction(tmp_path):
    root = fake_game(tmp_path)

    def no_network(url):
        raise AssertionError("the bundled path must not touch the network")

    results = run_patches(root, rain.manifest(), {rain.LT_ID},
                          undo_store=tmp_path / "undo", fetch=no_network)
    result = next(r for r in results if r.patch_id == rain.LT_ID)

    assert result.status == PatchStatus.APPLIED, result.detail
    assert "both voice sets" in result.detail

    sys_dir = root / "alba_data" / "sys"
    for variant in ("u", "c"):
        index = filelist.parse((sys_dir / f"filelist{variant}.win32.bin").read_bytes())
        filelist.verify_roundtrip((sys_dir / f"filelist{variant}.win32.bin").read_bytes())
        with (sys_dir / f"white_img{variant}.win32.bin").open("rb") as handle:
            for path in WEATHER:
                _, entry = index.locate(path)
                handle.seek(entry.byte_offset)
                assert zlib.decompress(handle.read(entry.stored)) == rain.PAYLOAD


def test_a_tampered_asset_is_refused_before_anything_is_written(tmp_path):
    """
    if the shipped file is not what we shipped, the build is wrong and writing
    it into a game archive on that basis would be reckless.
    """
    root = fake_game(tmp_path)
    tampered = tmp_path / "bundle" / "veffs.jp.win32.imgb"
    tampered.parent.mkdir()
    tampered.write_bytes(b"not the real payload")
    before = archives(root)

    results = run_patches(root, lt_on(rain.manifest(), bundled=str(tampered)), {rain.LT_ID},
                          undo_store=tmp_path / "undo")
    result = next(r for r in results if r.patch_id == rain.LT_ID)

    assert result.status == PatchStatus.FAILED
    assert "is damaged" in result.detail
    assert archives(root) == before


def test_a_missing_asset_fails_clearly(tmp_path):
    """a broken build should say which file is missing, not crash"""
    root = fake_game(tmp_path)

    results = run_patches(root, lt_on(rain.manifest(), bundled=str(tmp_path / "gone.imgb")),
                          {rain.LT_ID}, undo_store=tmp_path / "undo")
    result = next(r for r in results if r.patch_id == rain.LT_ID)

    assert result.status == PatchStatus.FAILED
    assert "is missing. Redownload the patcher" in result.detail


def test_the_mod_author_is_still_credited_in_the_manifest():
    assert "Krisan Thyme" in retired().credit


def test_the_drawer_lists_an_earlier_install_and_remove_takes_it_off(tmp_path, monkeypatch):
    """Installed is where an earlier version's Leviathan's Tears comes off now"""
    from patcher.bridge import UiBridge

    root = fake_game(tmp_path)
    before = archives(root)
    state = tmp_path / "state" / "ffxiii2.json"
    monkeypatch.setattr("patcher.bridge.state_path_for",
                        lambda gid: tmp_path / "state" / f"{gid}.json")
    results = run_patches(root, rain.manifest(), {rain.LT_ID}, undo_store=tmp_path / "undo",
                          on_applied=lambda r: record_applied(state, r.patch_id,
                                                              r.installed_files, r.revert_info))
    assert next(r for r in results if r.patch_id == rain.LT_ID).status == PatchStatus.APPLIED
    bridge = UiBridge(manifest_paths={"ffxiii2": ROOT / "manifests" / "ffxiii2.json"})
    monkeypatch.setattr(bridge, "install_dir", lambda gid: root)

    rows = bridge.extra_rows("ff13-2")
    assert [(r["name"], r["managed"]) for r in rows] == [("Leviathan's Tears", True)], rows

    done = bridge.remove_extra("ff13-2", "Leviathan's Tears")

    assert done["ok"], done
    assert archives(root) == before
    assert bridge.extra_rows("ff13-2") == []
