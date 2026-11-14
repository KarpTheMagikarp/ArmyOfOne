from pathlib import Path

from patcher.install_resolver import resolve_install_dir, validate_install_dir
from patcher.manifest import GameManifest


def make_manifest() -> GameManifest:
    return GameManifest(
        game_id="ffxiii",
        display_name="Final Fantasy XIII",
        manifest_version="v1.0.0",
        executable_relative_path="bin/game.exe",
        patches=[],
    )


def test_validate_install_dir_true_when_exe_present(tmp_path: Path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "game.exe").write_bytes(b"fake exe")

    assert validate_install_dir(tmp_path, make_manifest()) is True


def test_validate_install_dir_false_when_exe_missing(tmp_path: Path):
    assert validate_install_dir(tmp_path, make_manifest()) is False


def test_resolve_prefers_manual_path_when_given_and_valid(tmp_path: Path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "game.exe").write_bytes(b"fake exe")

    result = resolve_install_dir(make_manifest(), manual_path=tmp_path)

    assert result.found is True
    assert result.install_dir == tmp_path
    assert result.source == "manual"


def test_resolve_rejects_manual_path_without_the_real_exe(tmp_path: Path):
    # user pointed at some unrelated folder
    result = resolve_install_dir(make_manifest(), manual_path=tmp_path)

    assert result.found is False
    assert result.install_dir is None
    assert "couldn't find" in result.error


def test_resolve_manual_path_wins_even_when_steam_paths_also_given(tmp_path: Path):
    manual_dir = tmp_path / "manual"
    manual_dir.mkdir()
    (manual_dir / "bin").mkdir()
    (manual_dir / "bin" / "game.exe").write_bytes(b"fake exe")

    result = resolve_install_dir(
        make_manifest(), manual_path=manual_dir, steam_library_paths=[tmp_path / "unrelated_steam_lib"]
    )

    assert result.source == "manual"
    assert result.install_dir == manual_dir


def _make_fake_steam_library(root: Path, app_id: str, installdir: str, with_exe: bool) -> Path:
    steamapps = root / "steamapps"
    steamapps.mkdir(parents=True)
    acf = steamapps / f"appmanifest_{app_id}.acf"
    acf.write_text(f'"AppState"\n{{\n\t"appid"\t\t"{app_id}"\n\t"installdir"\t\t"{installdir}"\n}}\n')
    install_dir = steamapps / "common" / installdir
    install_dir.mkdir(parents=True)
    if with_exe:
        (install_dir / "bin").mkdir()
        (install_dir / "bin" / "game.exe").write_bytes(b"fake exe")
    return root


def test_resolve_finds_a_valid_steam_install(tmp_path: Path):
    library = _make_fake_steam_library(tmp_path / "lib", "292120", "FINAL FANTASY XIII", with_exe=True)

    result = resolve_install_dir(make_manifest(), steam_library_paths=[library])

    assert result.found is True
    assert result.source == "steam"
    assert result.install_dir == library / "steamapps" / "common" / "FINAL FANTASY XIII"


def test_resolve_rejects_a_steam_install_thats_missing_its_exe(tmp_path: Path):
    # steam's own manifest says it's installed, but the exe isn't actually there
    # (corrupted install, interrupted verify, whatever) - shouldn't be trusted
    library = _make_fake_steam_library(tmp_path / "lib", "292120", "FINAL FANTASY XIII", with_exe=False)

    result = resolve_install_dir(make_manifest(), steam_library_paths=[library])

    assert result.found is False
    assert result.install_dir is None


def test_resolve_reports_not_found_when_nothing_given(tmp_path: Path):
    result = resolve_install_dir(make_manifest())

    assert result.found is False
    # advice-free on purpose: this string goes to a window, a terminal and
    # a log file, and each caller adds the fix in its own vocabulary
    assert "couldn't automatically find" in result.error
    assert "click" not in result.error.lower()
