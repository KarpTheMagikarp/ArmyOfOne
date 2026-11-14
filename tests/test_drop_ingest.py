import json
import zipfile
from pathlib import Path

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from tests.helpers import make_fake_pe

MANIFESTS = Path(__file__).resolve().parent.parent / "manifests"


@pytest.fixture
def game(tmp_path, monkeypatch):
    """a real XIII-2 manifest against a fake install"""
    library = tmp_path / "steam"
    install = library / "steamapps" / "common" / "FFXIII2"
    (install / "alba_data" / "prog" / "win" / "bin").mkdir(parents=True)
    (install / "alba_data" / "prog" / "win" / "bin" / "ffxiii2img.exe").write_bytes(make_fake_pe())
    (library / "steamapps" / "appmanifest_292140.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"292140"\n\t"installdir"\t\t"FFXIII2"\n}\n')
    monkeypatch.setattr(bridge_mod, "state_path_for", lambda gid: tmp_path / ".state" / f"{gid}.json")
    return UiBridge(manifest_paths={"ffxiii2": MANIFESTS / "ffxiii2.json"},
                    steam_library_paths=[library]), tmp_path


def make_zip(tmp_path, name, files):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, content in files.items():
            zf.writestr(member, content)
    return path


def test_a_supplied_texture_pack_is_recognised(game):
    """the remaining supplied-archive case for XIII-2"""
    bridge, tmp = game
    archive = make_zip(tmp, "xiii2-hd.zip",
                       {"pack/thing.imgb": b"x" * 500, "readme.txt": b"instructions"})

    result = bridge.ingest("ff13-2", str(archive))

    assert result["ok"] is True
    assert result["jobId"] == "hdproject2"


def test_an_unrelated_file_is_rejected_not_guessed(game):
    bridge, tmp = game
    archive = make_zip(tmp, "holiday-photos.zip", {"beach.jpg": b"\xff\xd8", "notes.txt": b"hi"})

    result = bridge.ingest("ff13-2", str(archive))

    assert result["ok"] is False
    assert "matches a patch" in result["error"]


def test_a_genuinely_ambiguous_archive_asks_rather_than_picking(tmp_path, monkeypatch):
    """
    XIII used to have two texture mods that both expected *.imgb, and
    installing the wrong one into a game folder is not easily undone. The HD
    Project bundles HD Models Plus now so there is only one, which is why this
    checks the machinery rather than that pair: the refusal has to survive the
    day a second claimant comes back.
    """
    library = tmp_path / "steam"
    install = library / "steamapps" / "common" / "FFXIII"
    (install / "white_data" / "prog" / "win" / "bin").mkdir(parents=True)
    (install / "white_data" / "prog" / "win" / "bin" / "ffxiiiimg.exe").write_bytes(make_fake_pe())
    (library / "steamapps" / "appmanifest_292120.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"292120"\n\t"installdir"\t\t"FFXIII"\n}\n')
    monkeypatch.setattr(bridge_mod, "state_path_for", lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths={"ffxiii": MANIFESTS / "ffxiii.json"},
                      steam_library_paths=[library])

    archive = make_zip(tmp_path, "textures.zip", {"pack/thing.imgb": b"x" * 100})

    # one claimant now, so it resolves rather than asking
    assert bridge.ingest("ff13", str(archive))["ok"] is True

    # and with a second one it still refuses to guess
    patch = next(p for p in bridge._manifests["ff13"].patches
                 if p.ui_job_id == "hdproject")
    twin = type(patch)(**{**patch.__dict__, "id": "another_texture_pack",
                          "ui_job_id": "anotherpack"})
    bridge._manifests["ff13"].patches.append(twin)

    result = bridge.ingest("ff13", str(archive))
    assert result["ok"] is False
    assert sorted(result["ambiguous"]) == ["anotherpack", "hdproject"], result
    assert "drop it on the row you mean" in result["error"]


def test_dropping_on_a_specific_row_resolves_the_ambiguity(game):
    bridge, tmp = game
    archive = make_zip(tmp, "textures.zip", {"pack/thing.imgb": b"x" * 100})

    result = bridge.ingest("ff13-2", str(archive), job_id="hdproject2")

    assert result["ok"] is True
    assert result["jobId"] == "hdproject2"


def test_a_missing_file_is_reported_not_raised(game):
    bridge, tmp = game
    result = bridge.ingest("ff13-2", str(tmp / "nope.zip"))
    assert result["ok"] is False
    assert "couldn't find" in result["error"]


def test_a_rar_gets_a_useful_instruction(game):
    bridge, tmp = game
    rar = tmp / "mod.rar"
    rar.write_bytes(b"Rar!\x1a\x07")

    result = bridge.ingest("ff13-2", str(rar))

    assert result["ok"] is False
    # names the action, and points at the route that works
    assert "Extract it" in result["error"]
    assert "Choose folder" in result["error"]


def test_ingest_writes_nothing_to_the_game(game):
    """scanning is read-only; installing is a separate, later step"""
    bridge, tmp = game
    install = tmp / "steam" / "steamapps" / "common" / "FFXIII2"
    before = {p: p.stat().st_mtime_ns for p in install.rglob("*") if p.is_file()}

    archive = make_zip(tmp, "audio.zip", {"winmm.dll": b"MZ"})
    bridge.ingest("ff13-2", str(archive))

    after = {p: p.stat().st_mtime_ns for p in install.rglob("*") if p.is_file()}
    assert before == after


def test_an_unidentified_file_installs_nothing(game):
    bridge, tmp = game
    archive = make_zip(tmp, "photos.zip", {"beach.jpg": b"\xff\xd8"})
    install = tmp / "steam" / "steamapps" / "common" / "FFXIII2"
    before = {p for p in install.rglob("*")}

    assert bridge.install_dropped("ff13-2", str(archive))["ok"] is False
    assert {p for p in install.rglob("*")} == before
