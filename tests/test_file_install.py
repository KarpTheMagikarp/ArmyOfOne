import io
import json
from pathlib import Path

import pytest

from patcher.mods.file_install import install_from_url, latest_release_asset_url
from tests.helpers import make_tar_bytes, make_tar_with_symlink, make_zip_bytes


def fake_fetch(responses: dict):
    """
    returns a fetch() stand-in mapping url -> bytes, no real network.
    responses values can be raw bytes, or a dict (auto-encoded as json).
    """
    def fetch(url: str) -> bytes:
        value = responses[url]
        if isinstance(value, dict):
            return json.dumps(value).encode()
        return value
    return fetch


def test_latest_release_asset_url_skips_source_code_archives():
    api_url = "https://api.github.com/repos/someone/somerepo/releases/latest"
    fetch = fake_fetch({
        api_url: {
            "tag_name": "v1.0.0",
            "assets": [
                {"name": "Source code (zip)", "browser_download_url": "https://example.com/src.zip"},
                {"name": "Source code (tar.gz)", "browser_download_url": "https://example.com/src.tar.gz"},
                {"name": "FF13Fix.zip", "browser_download_url": "https://example.com/FF13Fix.zip"},
            ],
        }
    })

    url = latest_release_asset_url("someone/somerepo", fetch=fetch)

    assert url == "https://example.com/FF13Fix.zip"


def test_latest_release_asset_url_returns_none_on_no_assets():
    api_url = "https://api.github.com/repos/someone/somerepo/releases/latest"
    fetch = fake_fetch({api_url: {"tag_name": "v1.0.0", "assets": []}})

    url = latest_release_asset_url("someone/somerepo", fetch=fetch)

    assert url is None


def test_latest_release_asset_url_returns_none_on_network_error():
    def broken_fetch(url: str) -> bytes:
        raise ConnectionError("no internet")

    url = latest_release_asset_url("someone/somerepo", fetch=broken_fetch)

    assert url is None


def test_install_from_url_writes_a_single_raw_file(tmp_path: Path):
    download_url = "https://example.com/d3d9.dll"
    fetch = fake_fetch({download_url: b"fake dll bytes"})

    installed = install_from_url(download_url, tmp_path, fetch=fetch)

    assert installed == ["d3d9.dll"]
    assert (tmp_path / "d3d9.dll").read_bytes() == b"fake dll bytes"


def test_install_from_url_extracts_a_zip(tmp_path: Path):
    download_url = "https://example.com/FF13Fix.zip"
    zip_bytes = make_zip_bytes({"d3d9.dll": b"fake dll", "FF13Fix.ini": b"[Settings]\n"})
    fetch = fake_fetch({download_url: zip_bytes})

    installed = install_from_url(download_url, tmp_path, fetch=fetch)

    assert set(installed) == {"d3d9.dll", "FF13Fix.ini"}
    assert (tmp_path / "d3d9.dll").read_bytes() == b"fake dll"
    assert (tmp_path / "FF13Fix.ini").read_bytes() == b"[Settings]\n"


def test_install_from_url_flattens_a_zip_with_an_internal_folder(tmp_path: Path):
    """
    the real bug: a real FF13Fix release zip wraps its files in its own
    subfolder (e.g. "FF13Fix_164/d3d9.dll"), not flat at the zip root
    like the fixture above assumes. this must still land as bin/d3d9.dll,
    not bin/FF13Fix_164/d3d9.dll - the game can't find a DirectX proxy
    dll buried in a subfolder.
    """
    download_url = "https://example.com/FF13Fix.zip"
    zip_bytes = make_zip_bytes({
        "FF13Fix_164/d3d9.dll": b"fake dll",
        "FF13Fix_164/FF13Fix.ini": b"[Settings]\n",
    })
    fetch = fake_fetch({download_url: zip_bytes})

    installed = install_from_url(download_url, tmp_path, fetch=fetch)

    assert set(installed) == {"d3d9.dll", "FF13Fix.ini"}
    assert (tmp_path / "d3d9.dll").read_bytes() == b"fake dll"
    assert (tmp_path / "FF13Fix.ini").read_bytes() == b"[Settings]\n"
    assert not (tmp_path / "FF13Fix_164").exists()


def test_install_from_url_creates_the_target_directory(tmp_path: Path):
    target = tmp_path / "nested" / "bin"
    download_url = "https://example.com/d3d9.dll"
    fetch = fake_fetch({download_url: b"fake dll bytes"})

    install_from_url(download_url, target, fetch=fetch)

    assert (target / "d3d9.dll").exists()


def test_flattens_a_single_wrapper_folder():
    """the FF13Fix shape: one wrapper dir, contents must land at the root"""
    payload = make_zip_bytes({"FF13Fix_164/d3d9.dll": b"dll", "FF13Fix_164/FF13Fix.ini": b"ini"})
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        installed = install_from_url("https://x.test/a.zip", Path(d), fetch=lambda u: payload)
        assert sorted(installed) == ["FF13Fix.ini", "d3d9.dll"]
        assert (Path(d) / "d3d9.dll").read_bytes() == b"dll"


def test_colliding_names_raise_instead_of_silently_picking_one():
    """the DXVK shape: x32/d3d9.dll and x64/d3d9.dll must not race each other"""
    payload = make_zip_bytes({"x32/d3d9.dll": b"32", "x64/d3d9.dll": b"64"})
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValueError, match="more than one 'd3d9.dll'"):
            install_from_url("https://x.test/a.zip", Path(d), fetch=lambda u: payload)


def test_source_subdir_picks_the_right_architecture():
    payload = make_zip_bytes({"x32/d3d9.dll": b"32", "x64/d3d9.dll": b"64"})
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        installed = install_from_url("https://x.test/a.zip", Path(d),
                                     fetch=lambda u: payload, source_subdir="x32")
        assert installed == ["d3d9.dll"]
        assert (Path(d) / "d3d9.dll").read_bytes() == b"32"


def test_missing_source_subdir_is_a_clear_error():
    payload = make_zip_bytes({"x64/d3d9.dll": b"64"})
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValueError, match="no 'x32' folder"):
            install_from_url("https://x.test/a.zip", Path(d),
                             fetch=lambda u: payload, source_subdir="x32")


def test_rar_still_fails_loudly():
    """.rar needs a dependency python does not ship, so it must not half-work"""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValueError, match="extractor python doesn't ship"):
            install_from_url("https://x.test/pack.rar", Path(d), fetch=lambda u: b"Rar!\x1a\x07")
        assert list(Path(d).iterdir()) == []


def test_a_bare_dll_release_still_installs():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        installed = install_from_url("https://x.test/d3d9.dll", Path(d), fetch=lambda u: b"dll")
        assert installed == ["d3d9.dll"]


def test_dxvk_tarball_installs_the_32_bit_dll():
    """
    the real DXVK release shape: a .tar.gz wrapping a version folder that
    holds x32/ and x64/ with the same filenames in both.
    """
    payload = make_tar_bytes({
        "dxvk-2.3/x32/d3d9.dll": b"32bit",
        "dxvk-2.3/x32/dxgi.dll": b"32dxgi",
        "dxvk-2.3/x64/d3d9.dll": b"64bit",
        "dxvk-2.3/x64/dxgi.dll": b"64dxgi",
        "dxvk-2.3/README.md": b"readme",
    })
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        installed = install_from_url("https://x.test/dxvk-2.3.tar.gz", Path(d),
                                     fetch=lambda u: payload, source_subdir="x32")
        assert sorted(installed) == ["d3d9.dll", "dxgi.dll"]
        # the 32 bit one, specifically. picking wrong here is a game that
        # refuses to launch with no useful error.
        assert (Path(d) / "d3d9.dll").read_bytes() == b"32bit"
        assert not (Path(d) / "README.md").exists()


def test_tarball_without_a_subdir_still_detects_collisions():
    payload = make_tar_bytes({"dxvk-2.3/x32/d3d9.dll": b"32", "dxvk-2.3/x64/d3d9.dll": b"64"})
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValueError, match="more than one 'd3d9.dll'"):
            install_from_url("https://x.test/dxvk-2.3.tar.gz", Path(d), fetch=lambda u: payload)


@pytest.mark.parametrize("ext,compression", [
    (".tar.gz", "gz"), (".tgz", "gz"), (".tar.xz", "xz"), (".tar.bz2", "bz2"), (".tar", ""),
])
def test_every_tar_flavour_opens(ext, compression):
    payload = make_tar_bytes({"pack/d3d9.dll": b"dll"}, compression=compression)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert install_from_url(f"https://x.test/pack{ext}", Path(d), fetch=lambda u: payload) == ["d3d9.dll"]


def test_symlinks_in_a_tarball_are_not_installed():
    """a symlink is how a tar writes outside the folder it was extracted into"""
    payload = make_tar_with_symlink({"pack/d3d9.dll": b"dll"}, "pack/evil.dll", "../../../etc/passwd")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        installed = install_from_url("https://x.test/pack.tar.gz", Path(d), fetch=lambda u: payload)
        assert installed == ["d3d9.dll"]
        assert not (Path(d) / "evil.dll").exists()
