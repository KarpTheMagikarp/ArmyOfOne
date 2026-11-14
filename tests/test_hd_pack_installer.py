from pathlib import Path

from patcher.mods.hd_pack_installer import (
    download_and_stage,
    extract_if_zip,
    find_installer_candidates,
)
from tests.helpers import make_zip_bytes


def test_download_and_stage_writes_the_file(tmp_path: Path):
    fetch = lambda url: b"fake archive bytes"

    result = download_and_stage("https://example.com/HDProject.zip", tmp_path, fetch=fetch)

    assert result == tmp_path / "HDProject.zip"
    assert result.read_bytes() == b"fake archive bytes"


def test_download_and_stage_strips_query_string_from_filename(tmp_path: Path):
    fetch = lambda url: b"data"

    result = download_and_stage(
        "https://cdn.example.com/files/HDProject.zip?token=abc123&expires=999", tmp_path, fetch=fetch
    )

    assert result.name == "HDProject.zip"


def test_download_and_stage_creates_the_directory(tmp_path: Path):
    target_dir = tmp_path / "nested" / "downloads"
    fetch = lambda url: b"data"

    download_and_stage("https://example.com/file.zip", target_dir, fetch=fetch)

    assert target_dir.exists()


def test_extract_if_zip_extracts_a_real_zip(tmp_path: Path):
    zip_path = tmp_path / "HDProject.zip"
    zip_path.write_bytes(make_zip_bytes({"setup.exe": b"fake installer", "readme.txt": b"read me"}))

    extract_dir = extract_if_zip(zip_path)

    assert extract_dir == tmp_path / "HDProject"
    assert (extract_dir / "setup.exe").read_bytes() == b"fake installer"
    assert (extract_dir / "readme.txt").read_bytes() == b"read me"


def test_extract_if_zip_returns_none_for_non_zip(tmp_path: Path):
    rar_path = tmp_path / "HDProject.rar"
    rar_path.write_bytes(b"fake rar content, not actually parseable")

    assert extract_if_zip(rar_path) is None


def test_find_installer_candidates_finds_exe_and_bat(tmp_path: Path):
    (tmp_path / "setup.exe").write_bytes(b"")
    (tmp_path / "install.bat").write_bytes(b"")
    (tmp_path / "readme.txt").write_bytes(b"")
    (tmp_path / "textures").mkdir()

    candidates = find_installer_candidates(tmp_path)

    assert [c.name for c in candidates] == ["install.bat", "setup.exe"]


def test_find_installer_candidates_returns_empty_list_when_none_found(tmp_path: Path):
    (tmp_path / "readme.txt").write_bytes(b"")

    assert find_installer_candidates(tmp_path) == []


def test_find_installer_candidates_ignores_subfolders(tmp_path: Path):
    nested = tmp_path / "some_folder"
    nested.mkdir()
    (nested / "setup.exe").write_bytes(b"")  # buried, shouldn't count as top-level

    assert find_installer_candidates(tmp_path) == []
