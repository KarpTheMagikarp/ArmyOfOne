import sys
from pathlib import Path

from patcher.steam_locator import (
    default_steam_library_vdf_path,
    find_game_install,
    find_install_for_game,
    installdir_from_acf,
    library_paths_from_vdf,
    parse_vdf,
)

FAKE_LIBRARYFOLDERS_VDF = r"""
"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"label"		""
		"apps"
		{
			"228980"		"12345"
		}
	}
	"1"
	{
		"path"		"D:\\SteamLibrary"
		"label"		""
		"apps"
		{
			"292120"		"98765"
			"292140"		"55555"
		}
	}
}
"""

FAKE_ACF = r"""
"AppState"
{
	"appid"		"292120"
	"universe"		"1"
	"name"		"FINAL FANTASY XIII"
	"installdir"		"FINAL FANTASY XIII"
	"StateFlags"		"4"
}
"""


def test_parse_vdf_handles_nested_blocks():
    data = parse_vdf(FAKE_ACF)
    assert data["AppState"]["appid"] == "292120"
    assert data["AppState"]["installdir"] == "FINAL FANTASY XIII"


def test_parse_vdf_unescapes_backslashes():
    data = parse_vdf(FAKE_LIBRARYFOLDERS_VDF)
    path = data["libraryfolders"]["0"]["path"]
    assert path == "C:\\Program Files (x86)\\Steam"


def test_library_paths_from_vdf_finds_both_libraries():
    paths = library_paths_from_vdf(FAKE_LIBRARYFOLDERS_VDF)
    assert paths == ["C:\\Program Files (x86)\\Steam", "D:\\SteamLibrary"]


def test_installdir_from_acf():
    assert installdir_from_acf(FAKE_ACF) == "FINAL FANTASY XIII"


def _make_fake_library(root: Path, app_id: str, installdir: str) -> Path:
    """builds a fake steam library folder with a manifest and install dir on disk"""
    steamapps = root / "steamapps"
    steamapps.mkdir(parents=True)
    acf = steamapps / f"appmanifest_{app_id}.acf"
    acf.write_text(f'"AppState"\n{{\n\t"appid"\t\t"{app_id}"\n\t"installdir"\t\t"{installdir}"\n}}\n')
    (steamapps / "common" / installdir).mkdir(parents=True)
    return root


def test_find_game_install_locates_the_right_library(tmp_path: Path):
    empty_library = tmp_path / "empty_lib"
    empty_library.mkdir()
    (empty_library / "steamapps").mkdir()

    real_library = _make_fake_library(tmp_path / "real_lib", "292120", "FINAL FANTASY XIII")

    result = find_game_install([empty_library, real_library], "292120")

    assert result == real_library / "steamapps" / "common" / "FINAL FANTASY XIII"


def test_find_game_install_returns_none_when_not_found(tmp_path: Path):
    library = tmp_path / "lib"
    library.mkdir()
    (library / "steamapps").mkdir()

    result = find_game_install([library], "292120")

    assert result is None


def test_find_install_for_game_uses_our_own_game_id(tmp_path: Path):
    library = _make_fake_library(tmp_path / "lib", "292140", "FINAL FANTASY XIII-2")

    result = find_install_for_game([library], "ffxiii2")

    assert result == library / "steamapps" / "common" / "FINAL FANTASY XIII-2"


def test_find_install_for_game_rejects_unknown_game_id(tmp_path: Path):
    try:
        find_install_for_game([tmp_path], "some_other_game")
        assert False, "expected a ValueError"
    except ValueError:
        pass


def test_default_steam_library_vdf_path_returns_none_off_windows():
    if sys.platform != "win32":
        assert default_steam_library_vdf_path() is None
