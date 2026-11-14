"""
An add-on's .bat that runs another mod's tools out of that mod's download,
the way an add-on for the FFXIII HD Project runs its SupportFiles\\ tools.
Its readme says to unpack it over that one, so on its own its SupportFiles\\
isn't there, and the drawer finds it in the download it goes on. (HD Models
Plus add-ons don't need this any more: test_hd_models_native.py)
"""

import sys
import zipfile
from pathlib import Path

import pytest

from patcher.mods import deps, game_files, sandbox
from tests.test_hd_runner import stub
from tests.test_sandbox import (BUILDER, BUILDER_TOOL, GAME, IMGB, TRB, drawer, drop, laid,
                                sums, xiii)

BUILT = {f"white_scru/{TRB}": b"natural vanilla trb vanilla ",
         f"white_scru/{IMGB}": b"natural vanilla imgb vanilla"}


def addon(tmp_path, bat=BUILDER):
    """the outfit alone: its .bat and its recipe, none of the tools it runs"""
    return drop(tmp_path, "Vanille Nude Mod", {"XIII Vanille All Naturey.bat": bat,
                                               "Data/X_Vanille.txt": "chr\\pc\\c004\\bin\\c004.win32\n"})


def portable(support: Path, name: str, body: str):
    """
    stub()'s tool, but finding its script beside itself, so a copy of the
    folder (kept) still runs once the original's gone
    """
    if sys.platform == "win32":
        pytest.skip("the portable stand-in is a #! script")
    support.mkdir(parents=True, exist_ok=True)
    (support / (name + ".py")).write_text(body, encoding="utf-8")
    launcher = support / name
    launcher.write_text(f"#!{sys.executable}\nimport os, runpy, sys\n"
                        "script = os.path.realpath(__file__) + '.py'\nsys.argv[0] = script\n"
                        "runpy.run_path(script, run_name='__main__')\n", encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def base(tmp_path, game, name="HD Project", tool=stub):
    """the download it goes on, SupportFiles and all"""
    folder = drop(tmp_path, name, {"_FFXIII HD Project Install.bat": "@echo off\n",
                                   "SupportFiles/FFXIII_HDProject.exe": b"MZ",
                                   "SupportFiles/ff13tool.exe": b"MZ"})
    tool(folder / "SupportFiles", "FFXIII_ModelsHD.exe",
         BUILDER_TOOL.replace("{real!r}", repr(str(game))))
    return folder


def played(game, folder, lenders=None):
    with game_files.Source(folder) as source:
        return sandbox.play(game, source, source.members(), folder.name, lenders=lenders)


def test_its_tools_come_out_of_the_download_beside_it(tmp_path):
    game = xiii(tmp_path / "steam")
    before = sums(game)
    base(tmp_path / "downloads", game)
    p = played(game, addon(tmp_path / "downloads"),
               sandbox.Lenders(near=[tmp_path / "downloads"]))
    assert laid(p) == BUILT, (p.problem, p.detail)
    assert p.tried == ["with SupportFiles out of your HD Project download, the mod it goes "
                       "on top of"]
    assert sums(game) == before


def test_its_tools_come_out_of_the_zip_a_mod_was_put_in_from(tmp_path):
    game = xiii(tmp_path / "steam")
    folder = base(tmp_path / "unpacked", game)
    archive = tmp_path / "HD Project 4.0.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for real in folder.rglob("*"):
            if real.is_file():
                z.write(real, f"HD Project/{real.relative_to(folder).as_posix()}")
    p = played(game, addon(tmp_path), sandbox.Lenders(homes=[archive]))
    assert laid(p) == BUILT, (p.problem, p.detail)
    assert "HD Project 4.0 download" in p.tried[0]


def test_one_checked_for_with_if_exist_is_lent_too(tmp_path):
    game = xiii(tmp_path / "steam")
    base(tmp_path / "downloads", game)
    check = BUILDER.replace('cd /d "%~dp0"\n', 'cd /d "%~dp0"\nif not exist "SupportFiles\\'
                            'FFXIII_ModelsHD.exe" (echo Put this in the HD Project & pause & '
                            'exit /b 1)\n')
    p = played(game, addon(tmp_path / "downloads", check),
               sandbox.Lenders(near=[tmp_path / "downloads"]))
    assert laid(p) == BUILT, (p.problem, p.detail)


def test_what_it_ships_itself_wins_over_the_lenders(tmp_path):
    game = xiii(tmp_path / "steam")
    lender = base(tmp_path / "downloads", game)
    (lender / "Data").mkdir()
    (lender / "Data" / "X_Vanille.txt").write_text("chr\\pc\\c999\\bin\\c999.win32\n")
    p = played(game, addon(tmp_path / "downloads"),
               sandbox.Lenders(near=[tmp_path / "downloads"]))
    assert laid(p) == BUILT, (p.problem, p.detail)


def test_nothing_to_lend_names_the_mod_its_an_addon_for(tmp_path):
    game = xiii(tmp_path / "steam")
    p = played(game, addon(tmp_path), sandbox.Lenders(near=[tmp_path]))
    assert laid(p) == {}
    assert p.problem == ("is an add-on for the FFXIII HD Project, and runs FFXIII_ModelsHD.exe out "
                         "of its SupportFiles folder, which this download doesn't have")
    assert p.detail.startswith("Drop your FFXIII HD Project download on the drawer once")
    assert p.needs == "hd_project"


def test_a_data_file_by_name_alone_is_never_borrowed(tmp_path):
    game = xiii(tmp_path / "steam")
    drop(tmp_path / "downloads", "Someone Else", {"op.win32.wmp": b"theirs " * 300})
    folder = drop(tmp_path / "downloads", "Movie", {"install.bat": rf"""@echo off
set "game={GAME}"
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""})
    p = played(game, folder, sandbox.Lenders(near=[tmp_path / "downloads"]))
    assert laid(p) == {}
    assert p.tried == []


def test_the_drawer_finds_it_beside_the_drop(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    base(tmp_path / "downloads", game)
    done = bridge.install_mod("ff13", str(addon(tmp_path / "downloads")))
    assert done["ok"], done
    assert "HD Project download" in done["message"]


SETUP = BUILDER.replace('cd /d "%~dp0"\n', 'cd /d "%~dp0"\nif not exist SupportFiles\\FFXIII_HDProject.exe '
                        '(echo Some required files are missing. & pause & exit /b 1)\n')


def xiii2_hd(tmp_path, game):
    """the XIII-2 HD Project's own unpack, with a tool by the add-on's tool's name, but XIII-2's"""
    folder = drop(tmp_path, ".FF XIII-2 HD-1-1-0-0-1618762296-unpacked",
                  {"SupportFiles/FFXIII2_HDProject.exe": b"MZ", "SupportFiles/ff13tool.exe": b"MZ"})
    stub(folder / "SupportFiles", "FFXIII_ModelsHD.exe",
         BUILDER_TOOL.replace("{real!r}", repr(str(game))))
    return folder


def test_another_games_hd_project_is_never_lent(tmp_path):
    game = xiii(tmp_path / "steam")
    xiii2_hd(tmp_path / "downloads", game)
    p = played(game, addon(tmp_path / "downloads"),
               sandbox.Lenders(near=[tmp_path / "downloads"], data="white_data"))
    assert laid(p) == {}
    assert p.tried == []
    assert p.needs == "hd_project"


def test_a_lender_short_of_what_the_bat_names_isnt_lent(tmp_path):
    game = xiii(tmp_path / "steam")
    lender = base(tmp_path / "downloads", game)
    (lender / "SupportFiles" / "FFXIII_HDProject.exe").unlink()
    p = played(game, addon(tmp_path / "downloads", SETUP),
               sandbox.Lenders(near=[tmp_path / "downloads"], data="white_data"))
    assert laid(p) == {}
    assert p.problem == ("is an add-on for the FFXIII HD Project, and needs FFXIII_HDProject.exe "
                         "out of its SupportFiles folder, which this download doesn't have")


def test_the_right_one_is_lent_with_the_wrong_one_beside_it(tmp_path):
    game = xiii(tmp_path / "steam")
    xiii2_hd(tmp_path / "downloads", game)
    base(tmp_path / "downloads", game)
    p = played(game, addon(tmp_path / "downloads", SETUP),
               sandbox.Lenders(near=[tmp_path / "downloads"], data="white_data"))
    assert laid(p) == BUILT, (p.problem, p.detail)
    assert "HD Project download" in p.tried[0]


def test_a_base_mod_dropped_once_is_kept_for_every_addon_after(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = base(tmp_path / "hd", game, tool=portable)
    bridge.install_mod("ff13", str(folder))
    assert deps.homes("white_data") == [deps.keep_root() / "hd_project"]
    # the download's gone since, the way a Downloads folder gets cleared
    import shutil
    shutil.rmtree(folder)
    done = bridge.install_mod("ff13", str(addon(tmp_path / "elsewhere", SETUP)))
    assert done["ok"], done


def test_a_tool_called_without_its_extension_is_found_in_the_download(tmp_path):
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "Movie Fix", {
        "- Movie Fix.bat": "@echo off\ncd /d \"%~dp0\"\ncall MovieLoad Fix.txt\n",
        "Fix.txt": "x",
        "Tools/MovieLoad.bat": rf"""@echo off
copy /y "%~dp0op.win32.wmp" "{GAME}\white_data\movie\us\" > nul
""",
        "Tools/op.win32.wmp": b"less grinding " * 100})
    p = played(game, folder)
    assert laid(p) == {"white_data/movie/us/op.win32.wmp": b"less grinding " * 100}, \
        (p.problem, p.detail)


def sealed(folder: Path, archive: Path, top: str) -> Path:
    """a download as a .7z, made by the bsdtar Windows ships as tar.exe"""
    import shutil as _sh
    import subprocess
    bsdtar = _sh.which("bsdtar") or (_sh.which("tar") if sys.platform == "win32" else None)
    if bsdtar is None:
        pytest.skip("no bsdtar to make a .7z with")
    stage = archive.parent / "packing"
    _sh.copytree(folder, stage / top)
    done = subprocess.run([bsdtar, "-a", "-cf", str(archive), top], cwd=stage, capture_output=True)
    if done.returncode != 0:
        pytest.skip("this bsdtar can't write a .7z")
    _sh.rmtree(stage)
    _sh.rmtree(folder)
    return archive


def test_its_tools_come_out_of_the_7z_a_mod_was_put_in_from(tmp_path):
    game = xiii(tmp_path / "steam")
    before = sums(game)
    archive = sealed(base(tmp_path / "unpacked", game, tool=portable),
                     tmp_path / "HD Project 4.0.7z", "HD Project")
    p = played(game, addon(tmp_path / "elsewhere", SETUP),
               sandbox.Lenders(homes=[archive], data="white_data"))
    assert laid(p) == BUILT, (p.problem, p.detail)
    assert "HD Project 4.0 download" in p.tried[0]
    assert sums(game) == before


def test_a_7z_thats_another_games_download_is_never_lent(tmp_path):
    game = xiii(tmp_path / "steam")
    archive = sealed(xiii2_hd(tmp_path / "unpacked", game), tmp_path / "FF XIII-2 HD.7z", "HD")
    p = played(game, addon(tmp_path / "elsewhere", SETUP),
               sandbox.Lenders(homes=[archive], data="white_data"))
    assert laid(p) == {} and p.tried == []
    assert p.needs == "hd_project"


def test_the_install_history_lends_a_7z_origin(tmp_path, monkeypatch):
    from patcher.bridge import UiBridge
    from patcher.mods import disk_files, loader_mods
    archive = tmp_path / "HD Project.7z"
    archive.write_bytes(b"7z")
    rar = tmp_path / "Other.rar"
    rar.write_bytes(b"Rar!")
    monkeypatch.setattr(game_files, "records", lambda store: [{"origin": str(archive)}])
    monkeypatch.setattr(disk_files, "records", lambda store: [{"origin": str(rar)}])
    monkeypatch.setattr(loader_mods, "records", lambda store: [])
    lenders = UiBridge._lenders(tmp_path, tmp_path / "drop" / "Vanille.zip", "white_data")
    assert archive in lenders.homes and rar in lenders.homes


def test_a_base_mod_in_from_before_tools_were_kept_has_them_kept_from_its_download(
        tmp_path, monkeypatch):
    from patcher.bridge import UiBridge
    from patcher.mods import disk_files, loader_mods
    game = xiii(tmp_path / "steam")
    folder = base(tmp_path / "hd", game, tool=portable)
    monkeypatch.setattr(game_files, "records",
                        lambda store: [{"name": "the FFXIII HD Project", "origin": str(folder)}])
    monkeypatch.setattr(disk_files, "records", lambda store: [])
    monkeypatch.setattr(loader_mods, "records", lambda store: [])
    assert deps.homes("white_data") == []
    UiBridge._lenders(game, tmp_path / "drop" / "Vanille.zip", "white_data")
    # kept the first time an add-on looked, so clearing Downloads after is fine
    assert deps.homes("white_data") == [deps.keep_root() / "hd_project"]


def test_a_base_mod_whose_download_is_gone_says_where_it_was(tmp_path, monkeypatch):
    from patcher.bridge import UiBridge
    from patcher.mods import disk_files, loader_mods
    game = xiii(tmp_path / "steam")
    gone = tmp_path / "Downloads" / "HD Project 4.0.7z"
    monkeypatch.setattr(game_files, "records",
                        lambda store: [{"name": "the FFXIII HD Project", "origin": str(gone)}])
    monkeypatch.setattr(disk_files, "records", lambda store: [])
    monkeypatch.setattr(loader_mods, "records", lambda store: [])
    lenders = UiBridge._lenders(game, tmp_path / "drop" / "Vanille.zip", "white_data")
    p = played(game, addon(tmp_path / "drop", SETUP), lenders)
    assert laid(p) == {}
    assert "the FFXIII HD Project is in, but that tool only comes in its download, and HD Project " \
           "4.0.7z isn't in Downloads any more" in p.problem, p.problem
    assert "dropping it is enough" in p.detail


def test_a_base_mod_in_from_a_download_without_its_tools_says_which_one_has_them(
        tmp_path, monkeypatch):
    from patcher.bridge import UiBridge
    from patcher.mods import disk_files, loader_mods
    game = xiii(tmp_path / "steam")
    nova = drop(tmp_path / "Downloads", "HD Project (Nova)",
                {"modconfig.ini": "[ModPackConfig]\nName=HD Project\nGameEntry=1\n"})
    monkeypatch.setattr(game_files, "records",
                        lambda store: [{"name": "the FFXIII HD Project", "origin": str(nova)}])
    monkeypatch.setattr(disk_files, "records", lambda store: [])
    monkeypatch.setattr(loader_mods, "records", lambda store: [])
    lenders = UiBridge._lenders(game, tmp_path / "drop" / "Vanille.zip", "white_data")
    p = played(game, addon(tmp_path / "drop", SETUP), lenders)
    assert "HD Project (Nova), the one it went in from, doesn't have it" in p.problem, p.problem
    assert p.detail.startswith("Drop the FFXIII HD Project download that has SupportFiles in it")
