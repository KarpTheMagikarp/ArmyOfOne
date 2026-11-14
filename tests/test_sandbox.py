"""
A mod's own .bat, played on a copy of the game. Every script here is written
the way the ones on Nexus are: MJB's path search and language question,
ff13tool unpacking and injecting, a builder of the author's own run on what
came out, menus of versions, and the ways they go wrong. The game is a real
format XIII, and nothing the script does may reach it before the drawer
puts in what it came to.
"""

import hashlib
import threading
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from patcher.mods import batch, game_files, sandbox
from patcher.mods.hd_runner import Cancelled
from tests.helpers import make_fake_pe, make_zip_bytes, pack_plain, vcdiff_patch
from tests.test_hd_runner import stub

MANIFESTS = Path(__file__).resolve().parent.parent / "manifests"
TRB = "chr/pc/c004/bin/c004.win32.trb"
IMGB = "chr/pc/c004/bin/c004.win32.imgb"
LIGHT = "chr/pc/c001/bin/c001.win32.trb"
MOVIE = "white_data/movie/us/op.win32.wmp"
GAME = r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII"


def xiii(root: Path, name: str = "FINAL FANTASY XIII", voices: str = "uc") -> Path:
    """XIII with its scr pairs holding Vanille and Lightning, both voice sets"""
    game = root / "steamapps" / "common" / name
    sys_dir = game / "white_data" / "sys"
    sys_dir.mkdir(parents=True)
    (game / "white_data" / "prog" / "win" / "bin").mkdir(parents=True)
    (game / "white_data" / "prog" / "win" / "bin" / "ffxiiiimg.exe").write_bytes(make_fake_pe())
    (game / "FFXiiiLauncher.exe").write_bytes(make_fake_pe())
    (game / "white_data" / "movie" / "us").mkdir(parents=True)
    (game / MOVIE).write_bytes(b"opening movie " * 400)
    for v in voices:
        pack_plain(sys_dir / f"white_scr{v}.win32.bin", sys_dir / f"filelist_scr{v}.win32.bin",
                   [(TRB, b"vanilla trb " * 100), (IMGB, b"vanilla imgb " * 300),
                    (LIGHT, b"lightning " * 100)])
        pack_plain(sys_dir / f"white_img{v}.win32.bin", sys_dir / f"filelist{v}.win32.bin",
                   [("gui/resident/menu.win32.imgb", b"menu" * 300)])
    return game


def sums(game: Path) -> dict:
    return {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(game.rglob("*"))
            if p.is_file() and ".patcher-undo" not in p.parts}


def drop(root: Path, name: str, files: dict) -> Path:
    folder = root / name
    for member, data in files.items():
        target = folder / member
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.replace("\r\n", "\n").replace("\n", "\r\n").encode()
        target.write_bytes(data)
    return folder


def play(game: Path, folder: Path, name: str = "", **kw):
    with game_files.Source(folder) as source:
        return sandbox.play(game, source, source.members(), name or folder.name, **kw)


def laid(played) -> dict:
    """what a play laid out for the drawer, path -> bytes"""
    if played.work is None:
        return {}
    return {p.relative_to(played.work).as_posix(): p.read_bytes()
            for p in sorted(played.work.rglob("*")) if p.is_file()}


def drawer(tmp_path, monkeypatch):
    """XIII where Steam put it, as FFXIII, and the bridge the drawer talks to"""
    library = tmp_path / "steam"
    game = xiii(library, name="FFXIII", voices="u")
    (library / "steamapps" / "appmanifest_292120.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"292120"\n\t"installdir"\t\t"FFXIII"\n}\n')
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    return game, UiBridge(manifest_paths={"ffxiii": MANIFESTS / "ffxiii.json"},
                          steam_library_paths=[library])


INJECTOR = r"""@echo off
title Vanille installer
CHOICE /C AM /N /M "Do you want to (A)utomatically find the game or (M)anually select it?"
if %ERRORLEVEL% EQU 2 goto manual
set "game=C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII"
if exist "%game%\FFXiiiLauncher.exe" goto found
:manual
set /p game=Enter the game folder:
:found
if not exist "%game%\white_data\sys\filelist_scru.win32.bin" goto nogame
CHOICE /C EJ /N /M "Install for (E)nglish or (J)apanese voices?"
if %ERRORLEVEL% EQU 1 set v=u
if %ERRORLEVEL% EQU 2 set v=c
cd /d "%~dp0files"
copy /y "%game%\white_data\sys\filelist_scr%v%.win32.bin" . > nul
..\ff13tool.exe -i -ff13 filelist_scr%v%.win32.bin "%game%\white_data\sys\white_scr%v%.win32.bin" chr\pc\c004\bin\c004.win32.trb > log.txt
set lines=0
for /f %%a in (log.txt) do if [%%a] EQU [Added] set /a lines+=1
if %lines% NEQ 1 goto failed
copy /y filelist_scr%v%.win32.bin "%game%\white_data\sys\" > nul
echo Done!
pause
goto :eof
:nogame
echo Game not found
pause
exit /b 1
:failed
echo Install failed
pause
exit /b 2
"""


def injector(tmp_path, name="Vanille Mod", bat=INJECTOR) -> Path:
    return drop(tmp_path, name, {"XIII Vanille Install.bat": bat, "ff13tool.exe": b"MZ",
                                 f"files/{TRB}": b"natural trb " * 120})


def test_an_injector_goes_into_each_voice_set_it_asks_about(tmp_path):
    game = xiii(tmp_path / "steam")
    before = sums(game)
    played = play(game, injector(tmp_path))
    assert not played.problem and not played.versions
    assert laid(played) == {f"white_scru/{TRB}": b"natural trb " * 120,
                            f"white_scrc/{TRB}": b"natural trb " * 120}
    assert played.languages == ["u", "c"]
    # it took the automatic search, and nothing it did reached the game
    assert ("Do you want to (A)utomatically find the game or (M)anually select it?", "A") in \
        played.asked
    assert sums(game) == before


def test_one_voice_set_is_played_once(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    played = play(game, injector(tmp_path))
    assert set(laid(played)) == {f"white_scru/{TRB}"} and played.languages == ["u"]


BUILDER = r"""@echo off
cd /d "%~dp0"
if exist FFXIII_Dir.txt set /p ff13=<FFXIII_Dir.txt
if defined ff13 goto have
echo Enter the path to FINAL FANTASY XIII including the trailing \
set /p "ff13=Path: "
:have
if not exist "%ff13%white_data\sys\filelist_scru.win32.bin" goto bad
echo %ff13%> FFXIII_Dir.txt
copy /y "%ff13%white_data\sys\filelist_scru.win32.bin" *.* > nul
copy /y "%ff13%white_data\sys\white_scru.win32.bin" *.* > nul
SupportFiles\ff13tool -x filelist_scru.win32.bin white_scru.win32.bin > nul
if not exist models md models
robocopy white_scru\chr\pc models\orig\chr\pc *.trb *.imgb /S > nul
robocopy models\orig models\mod /e /xf * > nul
"SupportFiles\FFXIII_ModelsHD.exe" Data\X_Vanille.txt
if %ERRORLEVEL% NEQ 0 goto bad
copy /y models\mod\chr\pc\c004\bin\*.* white_scru\chr\pc\c004\bin\ > nul
SupportFiles\ff13tool -c filelist_scru.win32.bin white_scru > nul
copy /y filelist_scru.win32.bin "%ff13%white_data\sys\" > nul
copy /y white_scru.win32.bin "%ff13%white_data\sys\" > nul
rd /s /q white_scru
del white_scru.win32.bin filelist_scru.win32.bin
echo All naturey!
pause
goto :eof
:bad
echo Something went wrong
pause
"""

# the author's model builder: reads the originals the .bat laid out, writes
# its own, and refuses to run anywhere it could reach the real game or where
# the whole unpacked archive was copied in for it
BUILDER_TOOL = r'''
import os, pathlib, sys
real = {real!r}
if real.lower() in os.getcwd().lower() and ".patcher-undo" not in os.getcwd().lower():
    sys.exit(7)
if any(real.lower() in a.lower() and ".patcher-undo" not in a.lower() for a in sys.argv):
    sys.exit(7)
if os.path.exists("white_scru"):
    print("Error: the whole archive's been copied in")
    sys.exit(8)
recipe = pathlib.Path(sys.argv[1].replace("\\", "/")).read_text(encoding="utf-8").strip().replace("\\", "/")
for half in (".trb", ".imgb"):
    orig = pathlib.Path("models/orig/" + recipe + half).read_bytes()
    if not orig.startswith(b"vanilla"):
        sys.exit(9)
    out = pathlib.Path("models/mod/" + recipe + half)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"natural " + orig[:20])
print("built", recipe)
'''


def builder(tmp_path, game: Path, name="Vanille Nude Mod") -> Path:
    folder = drop(tmp_path, name, {"XIII Vanille All Naturey.bat": BUILDER,
                                   "Data/X_Vanille.txt": "chr\\pc\\c004\\bin\\c004.win32\n",
                                   "SupportFiles/ff13tool.exe": b"MZ"})
    stub(folder / "SupportFiles", "FFXIII_ModelsHD.exe",
         BUILDER_TOOL.replace("{real!r}", repr(str(game))))
    return folder


def test_its_own_tool_runs_on_the_games_originals_and_what_it_built_goes_in(tmp_path):
    game = xiii(tmp_path / "steam")
    before = sums(game)
    played = play(game, builder(tmp_path, game))
    assert not played.problem, played.problem
    # the two halves it rebuilt and nothing it repacked untouched, Lightning's
    # model included
    assert laid(played) == {f"white_scru/{TRB}": b"natural vanilla trb vanilla ",
                            f"white_scru/{IMGB}": b"natural vanilla imgb vanilla"}
    assert played.asked == [("Path: ", GAME + "\\")]
    assert "FFXIII_ModelsHD.exe" in played.ran
    assert sums(game) == before


def test_nothing_it_does_reaches_the_real_game(tmp_path):
    game = xiii(tmp_path / "steam")
    before = sums(game)
    folder = drop(tmp_path, "Wrecker", {"install.bat": rf"""@echo off
set "game={GAME}"
del /s /q "%game%\*"
rd /s /q "%game%\white_data"
echo junk> "%game%\FFXiiiLauncher.exe"
"""})
    played = play(game, folder)
    assert sums(game) == before
    # the launcher it wrote over is the one thing left that's a file
    assert laid(played) == {"FFXiiiLauncher.exe": b"junk\r\n"}
    assert any("deleted" in note for note in played.notes)


MENU = r"""@echo off
:menu
cls
echo Vanille Outfits
echo 1. Install the Naturey outfit
echo 2. Install the Pulse robe
echo 3. Uninstall
echo 4. Exit
set /p pick=Choose:
if "%pick%"=="1" goto naturey
if "%pick%"=="2" goto robe
if "%pick%"=="3" goto menu
if "%pick%"=="4" goto :eof
goto menu
:naturey
set src=naturey
goto put
:robe
set src=robe
:put
set "game=C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII"
cd /d "%~dp0%src%"
copy /y "%game%\white_data\sys\filelist_scru.win32.bin" . > nul
..\ff13tool.exe -i filelist_scru.win32.bin "%game%\white_data\sys\white_scru.win32.bin" chr\pc\c004\bin\c004.win32.trb > nul
copy /y filelist_scru.win32.bin "%game%\white_data\sys\" > nul
echo Done
goto menu
"""


def menu_mod(tmp_path) -> Path:
    return drop(tmp_path, "Vanille Outfits", {
        "install.bat": MENU, "ff13tool.exe": b"MZ",
        f"naturey/{TRB}": b"naturey " * 100, f"robe/{TRB}": b"robe " * 100})


def test_a_menu_comes_back_as_versions_and_the_pick_is_what_goes_in(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = menu_mod(tmp_path)
    asked = play(game, folder)
    # uninstalling and leaving aren't versions of it
    assert asked.versions == ["The Naturey outfit", "The Pulse robe"]
    assert asked.work is None
    picked = play(game, folder, version="The Pulse robe")
    assert laid(picked) == {f"white_scru/{TRB}": b"robe " * 100}


def test_a_menu_it_comes_back_to_is_left_by_its_exit(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    played = play(game, menu_mod(tmp_path), version="The Naturey outfit")
    # back at the menu after installing, it took Exit rather than going round
    assert [said for _, said in played.asked if said][-1] == "4" or not played.problem
    assert laid(played) == {f"white_scru/{TRB}": b"naturey " * 100}


def test_installers_side_by_side_are_its_versions(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    one = INJECTOR.replace("files", "lightning-hair")
    folder = drop(tmp_path, "Hairs", {
        "Install Short Hair.bat": INJECTOR, "Install Long Hair.bat": one,
        "Uninstall.bat": "@echo off\necho bye\n", "ff13tool.exe": b"MZ",
        f"files/{TRB}": b"short " * 100, f"lightning-hair/{TRB}": b"long " * 100})
    assert play(game, folder).versions == ["Install Long Hair", "Install Short Hair"]
    assert laid(play(game, folder, version="Install Long Hair")) == \
        {f"white_scru/{TRB}": b"long " * 100}


def test_a_bat_made_to_run_from_inside_the_game_folder(tmp_path):
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "Movie Swap", {
        "Copy into your game folder and run.bat": r"""@echo off
if not exist white_data\movie\us\op.win32.wmp goto wrong
ren white_data\movie\us\op.win32.wmp op.win32.wmp.bak
copy /y newop.wmp white_data\movie\us\op.win32.wmp > nul
echo Swapped. > swap.log
goto :eof
:wrong
echo Put me in the game's folder first
""", "newop.wmp": b"a better opening " * 300})
    played = play(game, folder)
    # its own files, put in the game's folder for it to run from, and the
    # backup and log it left there, aren't the mod
    assert laid(played) == {MOVIE: b"a better opening " * 300}


def test_what_it_copies_beside_the_exe_goes_in_through_the_drawer(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    before = sums(game)
    folder = drop(tmp_path, "Sharper Text", {"install.bat": r"""@echo off
for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 292120" /v InstallLocation') do set "game=%%b"
if not defined game exit /b 1
xcopy /s /y /i "%~dp0bin" "%game%\white_data\prog\win\bin" > nul
""", "bin/fonts/sharp.dat": b"sharp " * 200})
    done = bridge.install_mod("ff13", str(folder))
    assert done["ok"], done
    assert done["message"].startswith("Played its installer, install.bat, on a copy of the game")
    landed = game / "white_data" / "prog" / "win" / "bin" / "fonts" / "sharp.dat"
    assert landed.read_bytes() == b"sharp " * 200
    assert "1 file the game didn't have, into its folder" in done["message"]
    mods = [m for m in bridge.list_addons("ff13")["mods"] if m["name"] == "Sharper Text"]
    assert [(m["kind"], m["files"]) for m in mods] == \
        [("disk", ["white_data/prog/win/bin/fonts/sharp.dat"])]
    assert bridge.remove_addon("ff13", "Sharper Text", "disk")["ok"]
    assert sums(game) == before


def test_an_index_swapped_without_its_archive_is_said(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Half Done", {"install.bat": rf"""@echo off
set "game={GAME}"
cd /d "%~dp0files"
copy /y "%game%\white_data\sys\filelist_scru.win32.bin" . > nul
copy /y "%game%\white_data\sys\white_scru.win32.bin" . > nul
..\ff13tool.exe -i filelist_scru.win32.bin white_scru.win32.bin chr\pc\c004\bin\c004.win32.trb > nul
copy /y filelist_scru.win32.bin "%game%\white_data\sys\" > nul
""", "ff13tool.exe": b"MZ", f"files/{TRB}": b"half " * 100})
    played = play(game, folder)
    assert played.work is None
    assert played.problem == ("changed the game in a way that can't go in: it changed what "
                              "filelist_scru.win32.bin lists without changing "
                              "white_scru.win32.bin to match, so neither went in")


def test_what_it_tries_beyond_the_game_is_said(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Online", {"install.bat": r"""@echo off
curl -L -o pack.zip https://example.com/pack.zip
if errorlevel 1 (echo Download failed & pause & exit /b 1)
"""})
    played = play(game, folder)
    assert played.problem == "needs to download something, and that isn't done here"


def test_a_program_it_doesnt_ship_is_named(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    played = play(game, drop(tmp_path, "Lost", {"install.bat": "@echo off\nWhiteBinTools -u x\n"}))
    assert played.problem.startswith("runs WhiteBinTools, which isn't anywhere in the download")


def test_cmd_stopping_it_after_its_work_keeps_the_work(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    bat = INJECTOR.replace("pause\ngoto :eof\n:nogame", "set x=\nif %x%==y echo never\n:nogame", 1)
    played = play(game, injector(tmp_path, bat=bat))
    assert set(laid(played)) == {f"white_scru/{TRB}"}
    assert played.notes == ["Its XIII Vanille Install.bat stopped before the end: cmd gave up on "
                            "one of its lines, saying \"The syntax of the command is incorrect\"."]


def test_a_loop_is_called_one(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    monkeypatch.setattr(batch, "LIMIT", 20_000)
    played = play(game, drop(tmp_path, "Spin", {"install.bat": "@echo off\n:a\ngoto a\n"}))
    assert "caught in a loop" in played.problem


def test_stop_ends_it(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    stop = threading.Event()
    stop.set()
    with pytest.raises(Cancelled):
        play(game, drop(tmp_path, "Slow", {"install.bat": "@echo off\n:a\nset /a n+=1\ngoto a\n"}),
             cancel=stop)
    assert not list((game / ".patcher-undo" / "work").glob("bat-*"))


def test_xdelta_over_a_loose_file_is_decoded(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    movie = (game / MOVIE).read_bytes()
    patch = vcdiff_patch(movie, [[("copy", 0, 100), ("add", b" remastered "),
                                  ("copy", 100, len(movie) - 100)]])
    folder = drop(tmp_path, "Remaster", {"install.bat": rf"""@echo off
set "game={GAME}"
xdelta3.exe -d -f -s "%game%\white_data\movie\us\op.win32.wmp" op.xdelta "%TEMP%\op.wmp"
move /y "%TEMP%\op.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
""", "xdelta3.exe": b"MZ", "op.xdelta": patch})
    played = play(game, folder)
    assert laid(played) == {MOVIE: movie[:100] + b" remastered " + movie[100:]}


def test_7zip_takes_files_out_of_an_archive_in_the_drop(tmp_path):
    import io
    import zipfile

    game = xiii(tmp_path / "steam", voices="u")
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as inner:
        inner.writestr(TRB, b"packed " * 100)
    folder = drop(tmp_path, "Packed", {"install.bat": rf"""@echo off
set "game={GAME}"
7za.exe x payload.zip -ofiles -y > nul
7za.exe e payload.zip -oflat -y > nul
if not exist flat\c004.win32.trb exit /b 1
cd files
copy /y "%game%\white_data\sys\filelist_scru.win32.bin" . > nul
..\ff13tool.exe -i filelist_scru.win32.bin "%game%\white_data\sys\white_scru.win32.bin" {TRB} > nul
copy /y filelist_scru.win32.bin "%game%\white_data\sys\" > nul
""", "7za.exe": b"MZ", "ff13tool.exe": b"MZ", "payload.zip": packed.getvalue()})
    assert laid(play(game, folder)) == {f"white_scru/{TRB}": b"packed " * 100}


def test_the_questions_are_answered_the_way_a_person_installing_would(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Q", {"install.bat": "@echo off\n"})
    with game_files.Source(folder) as source:
        stage = sandbox.Stage(game, game_files.read_game(game), source, source.members(),
                              "install.bat", "Q", tmp_path / "stage")
        stage.texts["install.bat"] = r"""
set /p dir=Where is it?
if exist "%dir%\filelistu.win32.bin" goto ok
if exist "%dir%\white_scru.win32.bin" goto ok
"""
        answers = sandbox.Answers(stage)

        def ask(kind, prompt, options="", var="", lines=()):
            return answers(batch.Ask(kind, prompt, options=options, var=var,
                                     where=("install.bat", len(answers.seen)),
                                     lines=tuple(lines)), None)

        assert ask("choice", "Do you wish to (C)ontinue the install, or (Q)uit?", "CQ") == "C"
        assert ask("choice", "Use this one? (Y/N)", "YN") == "Y"
        assert ask("choice", "Do you want to uninstall the old version first? (Y/N)", "YN") == "N"
        assert ask("choice", "(A)utomatic search or (M)anual?", "AM") == "A"
        # a path it checks the sys folder's files under means the sys folder
        assert ask("line", "Where is it? ", var="dir") == \
            GAME + "\\white_data\\sys\\"


def test_the_registry_knows_where_steam_put_the_game(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Reg", {"install.bat": r"""@echo off
for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 292120" /v InstallLocation') do set "game=%%b"
echo %game%> "%game%\where.dat"
"""})
    assert laid(play(game, folder)) == {"where.dat": (GAME + "\r\n").encode()}


def test_through_the_drawer_versions_install_and_come_back_out(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    before = sums(game)
    folder = drop(tmp_path, "Vanille Outfits", {
        "install.bat": MENU.replace("FINAL FANTASY XIII", "FFXIII"), "ff13tool.exe": b"MZ",
        f"naturey/{TRB}": b"naturey " * 100, f"robe/{TRB}": b"robe " * 100})

    asked = bridge.install_mod("ff13", str(folder))
    assert asked["kind"] == "versions" and asked["versions"] == ["The Naturey outfit",
                                                                 "The Pulse robe"]
    done = bridge.install_mod("ff13", str(folder), version="The Naturey outfit")
    assert done["ok"] and done["kind"] == "files", done
    index = game / "white_data" / "sys" / "filelist_scru.win32.bin"
    entry = game_files.entries(index)[TRB]
    from patcher.mods.hd_zones import read_record
    assert read_record(game / "white_data" / "sys" / "white_scru.win32.bin", entry) == \
        b"naturey " * 100
    mods = [m for m in bridge.list_addons("ff13")["mods"] if m["kind"] == "files"]
    assert [m["name"] for m in mods] == ["Vanille Outfits"]
    assert bridge.remove_addon("ff13", "Vanille Outfits", "files")["ok"]
    assert sums(game) == before
    assert not list((game / ".patcher-undo" / "work").glob("bat-*"))


def test_through_the_drawer_one_that_puts_nothing_in_says_what_happened(tmp_path, monkeypatch):
    _, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Not Here", {"setup.bat": r"""@echo off
if not exist "D:\Games\FFXIII\FFXiiiLauncher.exe" (echo Install the game to D:\Games\FFXIII first & pause & exit /b 1)
"""})
    done = bridge.install_mod("ff13", str(folder))
    assert not done["ok"]
    assert done["error"] == ("Not Here wasn't installed: its setup.bat said \"Install the game to "
                             "D:\\Games\\FFXIII first\".")
    assert "installers" not in done["error"]
    # it was played again with the game there, which got it no further
    assert "Played it again with the game at D:\\Games\\FFXIII too" in done["detail"]


def test_which_bat_is_the_installer():
    found = sandbox.find([("Mod/Install.bat", 1), ("Mod/Uninstall.bat", 1),
                          ("Mod/tools/extract.bat", 1), ("Mod/readme.txt", 1)])
    assert [bat for bat, _ in found] == ["Mod/Install.bat", "Mod/tools/extract.bat"]
    # one named for taking something away is still the mod when it's all there is
    assert sandbox.find([("Remove Tattoo.bat", 1)])[0][0] == "Remove Tattoo.bat"
    # one named for taking the mod back off never is, unless it says it
    # installs too; any other is played and judged by what it does
    assert [bat for bat, _ in sandbox.find([("Uninstall.bat", 1), ("Restore Vanilla.bat", 1),
                                            (MOVIE, 1)])] == ["Restore Vanilla.bat"]
    assert [bat for bat, _ in sandbox.find([("Backup and Install.bat", 1)])] == \
        ["Backup and Install.bat"]


def test_what_a_bats_name_says_about_taking_a_mod_back_off():
    for stem in ("Uninstall", "Uninstall Patch", "Unpatch", "Uninstall and Restore Backup",
                 "Restore Unpatched Files"):
        assert sandbox.undoes(stem), stem
    # the rest are played and judged by what they do: an undo of something is
    # often the mod, and an installer can undo something on the way
    for stem in ("Restore Vanilla", "Disable Mod", "Remove Censorship", "Restore Cut Content",
                 "Disable Intro Video", "Remover", "Cinematic Preset", "Backup and Install",
                 "Clean Install", "Uninstall Old and Install", "Reinstall"):
        assert not sandbox.undoes(stem), stem


def test_which_of_several_bats_are_its_versions():
    def versions(*names, texts=None):
        texts = texts or {}
        found = sandbox.find([(n, 1) for n in names], lambda m: texts.get(m, b""), "white_data")
        return sorted(sandbox.rivals(found))

    assert versions("Install Vanilla Hair.bat", "Install Long Hair.bat") == \
        ["Install Long Hair.bat", "Install Vanilla Hair.bat"]
    assert versions("Apply Patch.bat", "Remove Patch.bat") == ["Apply Patch.bat"]
    assert versions("Install Patch.bat", "Uninstall Patch.bat") == ["Install Patch.bat"]
    # the .bat that starts the game, beside the one that installs
    launch = b'start "" "C:\\Steam\\steamapps\\common\\FFXIII\\FFXiiiLauncher.exe"'
    assert versions("Backup and Install.bat", "Launch Game.bat",
                    texts={"Launch Game.bat": launch}) == ["Backup and Install.bat"]
    uncut = b"copy /y white_data\\Uncensored\\op.win32.wmp white_data\\movie\\us\\ > nul"
    assert versions("Remove Censorship.bat", "Launch Game.bat", texts={
        "Launch Game.bat": launch + b" >nul", "Remove Censorship.bat": uncut}) == \
        ["Remove Censorship.bat"]
    # an undo on its name puts it behind one that doesn't
    assert versions("Restore Vanilla.bat", "Install.bat") == ["Install.bat"]
    # a pack for all three plays the one for this game, and one .bat another
    # calls is that one's helper
    texts = {"XIII-2/install.bat": b"cd alba_data\\sys\r\ncopy /y a b",
             "XIII/install.bat": b"cd white_data\\sys\r\ncopy /y a b",
             "XIII/setup.bat": b"call helper.bat", "XIII/helper.bat": b"echo hi"}
    found = sandbox.find([(m, 1) for m in texts], texts.__getitem__, "white_data")
    assert found[0][0] == "XIII/install.bat"
    assert "XIII/helper.bat" not in [bat for bat, _ in found]


def test_a_folder_picker_is_shown_the_game(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Picker", {"install.bat": r"""@echo off
for /f "delims=" %%i in ('powershell -noprofile -command "(new-object -COM 'Shell.Application').BrowseForFolder(0,'Pick FINAL FANTASY XIII',0,0).self.path"') do set "game=%%i"
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
""", "op.win32.wmp": b"picked " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"picked " * 300}


def test_steams_own_files_say_where_the_game_is(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Vdf", {"install.bat": r"""@echo off
set "steam=%ProgramFiles(x86)%\Steam"
for /f "tokens=2 delims=	" %%a in ('findstr /c:"installdir" "%steam%\steamapps\appmanifest_292120.acf"') do set "dir=%%~a"
copy /y "%~dp0op.win32.wmp" "%steam%\steamapps\common\%dir%\white_data\movie\us\" > nul
""", "op.win32.wmp": b"vdf " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"vdf " * 300}


def test_a_program_that_finds_the_real_game_itself_stops_it_going_in(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Rogue", {"install.bat": "@echo off\npatcher.exe\n"})
    stub(folder, "patcher.exe", "import pathlib\n"
         f"pathlib.Path({str(game / MOVIE)!r}).write_bytes(b'went around')\n")
    with pytest.raises(sandbox.SandboxError, match="changed white_data/movie/us/op.win32.wmp there"):
        play(game, folder)
    assert not list((game / ".patcher-undo" / "work").glob("bat-*"))


def test_a_tool_run_from_inside_the_game_folder_is_copied_there_and_read_back(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Movie Tool", {"Run me in the game folder.bat": r"""@echo off
if not exist white_data\sys goto wrong
moviefix.exe white_data\movie\us\op.win32.wmp
goto :eof
:wrong
echo copy me into the game first
"""})
    stub(folder, "moviefix.exe", "import pathlib, sys\n"
         "movie = pathlib.Path(sys.argv[1].replace('\\\\', '/'))\n"
         "movie.write_bytes(b'fixed ' + movie.read_bytes()[:20])\n"
         "(movie.parent / 'op_extra.win32.wmp').write_bytes(b'extra')\n")
    assert laid(play(game, folder)) == {MOVIE: b"fixed opening movie openin",
                                        "white_data/movie/us/op_extra.win32.wmp": b"extra"}


def test_a_program_windows_wont_start_is_its_scripts_to_handle(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Broken Tool", {"install.bat": r"""@echo off
fixer.exe
if errorlevel 1 (echo the fixer didn't run & exit /b 1)
""", "fixer.exe": b"\x00not a program\x00"})
    played = play(game, folder)
    assert played.problem == "runs fixer.exe, which Windows couldn't start"
    assert "the fixer didn't run" in played.detail
    # and what it printed says the play's own paths, never the stage's
    assert str(tmp_path) not in played.detail
    assert "C:\\Users\\Player\\Downloads\\BrokenTool\\fixer.exe" \
        in played.detail.replace("/", "\\"), played.detail


def test_a_preset_it_puts_beside_the_exe_goes_in_and_its_readme_doesnt(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Preset", {"install.bat": rf"""@echo off
set "game={GAME}"
copy /y "%~dp0Clarity.txt" "%game%\white_data\prog\win\bin\" > nul
copy /y "%~dp0ReadMe Clarity.txt" "%game%\white_data\prog\win\bin\" > nul
copy /y "%~dp0Clarity.txt" "%game%\white_data\prog\win\bin\install.log" > nul
echo installed> "%game%\white_data\prog\win\bin\log.txt"
""", "Clarity.txt": b"[Clarity.fx]\r\nStrength=0.4\r\n", "ReadMe Clarity.txt": b"how to use it"})
    assert laid(play(game, folder)) == {
        "white_data/prog/win/bin/Clarity.txt": b"[Clarity.fx]\r\nStrength=0.4\r\n"}


def test_files_are_compared_without_holding_them_whole(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    monkeypatch.setattr(sandbox, "INLINE", 64)
    folder = drop(tmp_path, "Same", {"install.bat": "@echo off\n", "big.bin": b"x" * 5000,
                                     "big2.bin": b"x" * 5000, "other.bin": b"x" * 4999 + b"y"})
    with game_files.Source(folder) as source:
        stage = sandbox.Stage(game, game_files.read_game(game), source, source.members(),
                              "install.bat", "Same", tmp_path / "stage")
        world = sandbox.World(stage, sandbox.Answers(stage), dry=True)
        mod = stage.mod
        assert world.same(mod + "\\big.bin", mod + "\\big2.bin")
        assert not world.same(mod + "\\big.bin", mod + "\\other.bin")


@pytest.mark.parametrize("probe", [
    "",
    r'if exist "%~dp0patched.flag" goto done',
    r'if exist "C:\Program Files (x86)\Steam\steamapps\common\FFXIII\white_data\prog\win\bin'
    r'\ffxiiiimg.exe.backup" goto done',
    "echo Patching, please wait...",
], ids=["on its own", "a flag of its own looked for", "its backup looked for", "a status line"])
def test_a_bat_around_a_4gb_patcher_is_said_as_one(tmp_path, monkeypatch, probe):
    _, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Big Memory", {"Patch.bat": "@echo off\n" + probe + r"""
4gb_patch.exe "C:\Program Files (x86)\Steam\steamapps\common\FFXIII\white_data\prog\win\bin\ffxiiiimg.exe"
:done
""", "4gb_patch.exe": b"MZ"})
    done = bridge.install_mod("ff13", str(folder))
    assert done["error"] == ("Big Memory is a 4GB patcher, 4gb_patch.exe. Apply's 4GB patch does "
                             "the same, so there's nothing here to put in.")


MOVIE_COPY = rf"""@echo off
set "game={GAME}"
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""


def test_the_undo_store_isnt_part_of_the_game_it_plays_on(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    (game / ".patcher-undo").mkdir()
    (game / ".patcher-undo" / "journal.dat").write_bytes(b"spans")
    folder = drop(tmp_path, "Peek", {"install.bat": MOVIE_COPY + r"""
if exist "%game%\.patcher-undo\journal.dat" echo seen> "%game%\seen.dat"
for /r "%game%" %%f in (*.dat) do echo %%~nxf>> "%game%\walked.dat"
""", "op.win32.wmp": b"new " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"new " * 300}


def test_the_game_it_starts_at_the_end_isnt_run(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    spawned = []
    monkeypatch.setattr(sandbox, "_spawn", lambda *a: spawned.append(a) or (0, "", ""))
    folder = drop(tmp_path, "Then Play", {"install.bat": MOVIE_COPY + r"""
start "" "%game%\FFXiiiLauncher.exe"
"%game%\white_data\prog\win\bin\ffxiiiimg.exe"
start steam://rungameid/292120
""", "op.win32.wmp": b"new " * 300})
    played = play(game, folder)
    assert laid(played) == {MOVIE: b"new " * 300}
    assert spawned == [] and "FFXiiiLauncher.exe" in played.ran


def test_its_tool_is_handed_where_things_are_on_the_stage_quoted_or_not(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Paths Tool", {"install.bat": rf"""@echo off
set "game={GAME}"
"%~dp0tools\maker.exe" %~dp0Data\recipe.txt -o"%TEMP%\made"
copy /y "%TEMP%\made\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", "Data/recipe.txt": b"made by recipe"})
    stub(folder / "tools", "maker.exe", "import pathlib, sys\n"
         "recipe = pathlib.Path(sys.argv[1].replace('\\\\', '/'))\n"
         "out = pathlib.Path(sys.argv[2][2:].replace('\\\\', '/'))\n"
         "out.mkdir(exist_ok=True)\n"
         "(out / 'op.win32.wmp').write_bytes(recipe.read_bytes())\n")
    played = play(game, folder)
    assert not played.problem, played.problem
    assert laid(played) == {MOVIE: b"made by recipe"}


def test_its_check_for_admin_says_yes(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Admin", {"install.bat": r"""@echo off
net session >nul 2>&1
if %errorlevel% neq 0 (echo Run this as administrator & pause & exit /b 1)
""" + MOVIE_COPY.replace("@echo off\n", ""), "op.win32.wmp": b"admin " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"admin " * 300}


@pytest.mark.parametrize("check", [
    'reg query "HKU\\S-1-5-19" >nul 2>&1\n'
    "if %errorlevel% neq 0 (echo Run this as administrator & echo Exiting... & exit /b 1)",
    'ver | find "10.0" >nul\nif errorlevel 1 (echo This needs Windows 10 & exit /b 1)',
    'if not exist "%ProgramFiles%\\7-Zip\\7z.exe" (echo Install 7-Zip first & exit /b 1)',
    'reg query "HKLM\\SOFTWARE\\7-Zip" /V Path >nul 2>&1 || (echo Install 7-Zip first & exit /b 1)',
    '"%ProgramFiles%\\7-Zip\\7z.exe" t "%~dp0op.win32.wmp" >nul || (echo It\'s damaged & exit /b 1)',
    '"%ProgramFiles%\\7-Zip\\7z.exe" >nul 2>&1 || (echo Install 7-Zip first & exit /b 1)',
    'reg query "HKU\\S-1-5-19\\Environment" >nul 2>&1 || (echo Run this as admin & exit /b 1)',
    '"%SystemRoot%\\System32\\reg.exe" query "HKU\\S-1-5-19" >nul 2>&1 || (echo Admin only & exit /b 1)',
    '%windir%\\system32\\net.exe session >nul 2>&1 || (echo Run this as admin & exit /b 1)',
], ids=["admin by the registry", "windows 10", "7-zip where it installs", "7-zip by the registry",
        "7-zip testing the download", "7-zip run bare", "admin by a key under it",
        "reg.exe by its path", "net.exe by its path"])
def test_the_checks_it_makes_before_it_starts_pass(tmp_path, check):
    """each of these ended a play at "Exiting...", with nothing to say why"""
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Checked", {"install.bat": "@echo off\n" + check + "\n"
                                        + MOVIE_COPY.replace("@echo off\n", ""),
                                        "op.win32.wmp": b"checked " * 300})
    played = play(game, folder)
    assert not played.problem, played.problem
    assert laid(played) == {MOVIE: b"checked " * 300}


def test_winrar_unpacks_what_the_drop_carries(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Packed", {"install.bat": rf"""@echo off
set "game={GAME}"
set "rar=%ProgramFiles%\WinRAR\WinRAR.exe"
if not exist "%rar%" (echo WinRAR is needed & echo Exiting... & exit /b 1)
"%rar%" x -ibck -o+ "%~dp0payload.zip" "%TEMP%\packed\"
copy /y "%TEMP%\packed\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", "payload.zip": make_zip_bytes({"op.win32.wmp": b"unpacked " * 300})})
    assert laid(play(game, folder)) == {MOVIE: b"unpacked " * 300}


def test_one_that_only_says_goodbye_is_said_by_what_it_looked_for(tmp_path):
    """the drawer's own message quoted "Exiting...", which is no use to anybody"""
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Gone Missing", {"install.bat": rf"""@echo off
echo ==============================
echo  Outfit installer
echo ==============================
if not exist "%~dp0data\c004.win32.trb" goto quit
copy /y "%~dp0data\c004.win32.trb" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:quit
echo Exiting...
pause
"""})
    played = play(game, folder)
    assert played.problem == (
        "needs data\\c004.win32.trb from its download, which isn't in what was dropped")
    assert "Where it stopped: if not exist \"%~dp0data\\c004.win32.trb\" goto quit" \
        in played.detail


def test_a_tool_that_fails_is_said_when_the_script_says_nothing_of_it(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Broken Quietly", {"install.bat": r"""@echo off
fixer.exe
if errorlevel 1 goto end
echo fixed
:end
echo Exiting...
"""})
    stub(folder, "fixer.exe", "import sys\nprint('cannot open op.win32.wmp')\n"
                              "print('Exiting...')\nsys.exit(3)\n")
    assert play(game, folder).problem == ("runs fixer.exe, which failed saying \"cannot open "
                                          "op.win32.wmp\", so nothing changed")


# a payload copied without a check first, so cmd's own error is all it says
UNCHECKED = r"""copy /y "%~dp0data\op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""


@pytest.mark.parametrize("before, after", [
    (r'if exist "%TEMP%\outfit" rd /s /q "%TEMP%\outfit"', ""),
    (r'if exist "%game%\outfit_installed.txt" (echo Already installed & goto :eof)', ""),
    ('set lang=us\nif exist "%game%\\white_data\\movie\\jp" set lang=jp', ""),
    (r'if exist "%game%\white_data\movie\jp\nul" (set lang=jp) else (set lang=us)', ""),
    ("", r'if exist "%~dp0extracted" rd /s /q "%~dp0extracted"'),
    (r'if exist "C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII\FFXiiiLauncher.exe" '
     r'set "game=C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII"', ""),
], ids=["a tidy-up first", "an installed-already check", "an optional folder",
        "a folder asked by nul", "a tidy-up after", "the game looked for where it isn't"])
def test_a_look_on_the_way_isnt_what_stopped_it(tmp_path, before, after):
    """each of these was named, not the copy of nothing that was why"""
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Outfit", {"install.bat": f'@echo off\nset "game={GAME}"\n{before}\n'
                                       + UNCHECKED + f"{after}\necho Exiting...\npause\n"})
    assert play(game, folder).problem == (
        "needs data\\op.win32.wmp from its download, which isn't in what was dropped")


def test_a_search_that_comes_to_nothing_is_played_with_the_game_where_it_looked(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Scanner", {"install.bat": r"""@echo off
set game=
for %%d in (C D E F) do if exist "%%d:\Games\FINAL FANTASY XIII\FFXiiiLauncher.exe" set "game=%%d:\Games\FINAL FANTASY XIII"
if "%game%"=="" goto bye
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\" > nul
goto :eof
:bye
echo Exiting...
pause
""", "op.win32.wmp": b"outfit " * 300})
    played = play(game, folder)
    assert laid(played) == {MOVIE: b"outfit " * 300}, played.problem
    assert played.tried == ["with the game at C:\\Games\\FINAL FANTASY XIII too"]


def test_a_banner_is_said_by_its_words_not_its_rules(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Banner", {"install.bat": "@echo off\necho ====================\n"
                                       "echo   FFXIII Outfit Pack v1.2\n"
                                       "echo ====================\npause\nexit\n"})
    assert play(game, folder).problem == (
        f"{sandbox.IDLE}, and the last thing it said was \"FFXIII Outfit Pack v1.2\"")


@pytest.mark.parametrize("status", ["Installing, please wait...", "Patching the menu font"])
def test_a_status_line_isnt_why_its_tool_failed(tmp_path, status):
    """read as the script explaining itself, which hid the tool that failed"""
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Quiet", {"install.bat": f"@echo off\necho {status}\n" + r"""
"%~dp0fixer.exe"
if errorlevel 1 goto bye
echo Done
goto :eof
:bye
echo Exiting...
"""})
    stub(folder, "fixer.exe", "import sys\nsys.exit(2)\n")
    assert play(game, folder).problem == (
        "runs fixer.exe, which failed with code 2, so nothing changed")


SEVEN_ZA = r"""import sys, zipfile
args = sys.argv[1:]
out = next((a[2:] for a in args if a.startswith("-o")), ".")
zipfile.ZipFile(next(a for a in args[1:] if not a.startswith("-"))).extractall(out)
"""


def test_what_the_drawer_cant_open_goes_to_the_7zip_it_carries(tmp_path):
    """
    the 7-Zip installed here is a stand-in that only plays what the drawer
    opened, so a payload not named like an archive goes to the drop's own
    7za.exe even where the script would rather have the installed one, and
    without one, a zip is opened here all the same
    """
    game = xiii(tmp_path / "steam", voices="u")
    script = rf"""@echo off
set "game={GAME}"
set "sz=%~dp0tools\7za.exe"
if exist "%ProgramFiles%\7-Zip\7z.exe" set "sz=%ProgramFiles%\7-Zip\7z.exe"
"%sz%" x "%~dp0data.pak" -o"%TEMP%\packed" -y
if errorlevel 1 (echo Unpacking failed & goto :eof)
copy /y "%TEMP%\packed\op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""
    payload = make_zip_bytes({"op.win32.wmp": b"packed " * 300})
    folder = drop(tmp_path, "Packed", {"install.bat": script, "data.pak": payload})
    stub(folder / "tools", "7za.exe", SEVEN_ZA)
    assert laid(play(game, folder)) == {MOVIE: b"packed " * 300}

    alone = drop(tmp_path, "Packed Alone", {"install.bat": script, "data.pak": payload})
    assert laid(play(game, alone)) == {MOVIE: b"packed " * 300}
    # a 7z the drawer can't open, with no 7-Zip of its own beside it or on
    # this PC, is said as what it takes
    sevened = drop(tmp_path, "Packed 7z", {"install.bat": script,
                                           "data.pak": b"7z\xbc\xaf\x27\x1c" + b"\0" * 64})
    played = play(game, sevened)
    assert played.problem == "unpacks data.pak, which takes 7-Zip to open, and this PC hasn't got it"
    assert played.detail.startswith("Install 7-Zip from 7-zip.org, then drop the mod again.")


GOT_ADMIN = r""":: BatchGotAdmin
:-------------------------------------
REM  --> Check for permissions
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
REM --> If error flag set, we do not have admin.
if '%errorlevel%' NEQ '0' (
    echo Requesting administrative privileges...
    goto UACPrompt
) else ( goto gotAdmin )
:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~s0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    exit /B
:gotAdmin
    if exist "%temp%\getadmin.vbs" ( del "%temp%\getadmin.vbs" )
    pushd "%CD%"
    CD /D "%~dp0"
"""


def test_the_admin_snippet_half_the_bats_out_there_carry_finds_it_is(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Elevated", {"install.bat": "@echo off\n" + GOT_ADMIN
                                         + MOVIE_COPY.replace("@echo off\n", ""),
                                         "op.win32.wmp": b"admin " * 300})
    played = play(game, folder)
    assert not played.problem, played.problem
    assert laid(played) == {MOVIE: b"admin " * 300}


def test_putting_its_backup_back_isnt_one_of_its_versions(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Movies", {"install.bat": rf"""@echo off
set "game={GAME}"
echo 1. Install the remaster
echo 2. Install the director's cut
echo 3. Restore the original movie
echo 4. Exit
set /p pick=Choose:
if "%pick%"=="1" copy /y "%~dp0remaster\op.win32.wmp" "%game%\white_data\movie\us\" > nul
if "%pick%"=="2" copy /y "%~dp0cut\op.win32.wmp" "%game%\white_data\movie\us\" > nul
if "%pick%"=="3" copy /y "%~dp0backup\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", "remaster/op.win32.wmp": b"remaster " * 300, "cut/op.win32.wmp": b"cut " * 300,
        "backup/op.win32.wmp": b"original " * 300})
    assert play(game, folder).versions == ["The remaster", "The director's cut"]


def test_a_question_asked_over_and_over_ends_the_play(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    monkeypatch.setattr(batch, "LIMIT", 50_000)
    folder = drop(tmp_path, "Picky", {"install.bat": r"""@echo off
:ask
set /p game=Where is the game?
if not exist "%game%\FFXIII-2.exe" (echo That's not it & goto ask)
"""})
    played = play(game, folder)
    assert played.problem == "kept asking \"Where is the game?\" whatever it was told"
    wordless = drop(tmp_path, "Wordless", {"install.bat": "@echo off\n:menu\n"
                                           "echo Pick a number from the mod page\n"
                                           "choice /c 123 /n\nif errorlevel 1 goto menu\n"})
    assert play(game, wordless).problem == "kept asking the same question whatever it was told"


def test_when_its_own_search_misses_it_asks_and_is_told(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Searcher", {"install.bat": r"""@echo off
choice /c AM /n /m "(A)utomatic or (M)anual?"
if errorlevel 2 goto manual
if exist "D:\Games\FINAL FANTASY XIII\FFXiiiLauncher.exe" set "game=D:\Games\FINAL FANTASY XIII"
if not defined game (echo Couldn't find it & pause & exit /b 1)
goto go
:manual
set /p game=Game folder:
:go
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", "op.win32.wmp": b"found " * 300})
    played = play(game, folder)
    assert laid(played) == {MOVIE: b"found " * 300}
    assert ("Game folder:", GAME + "\\") in played.asked


def test_a_file_each_voice_set_makes_differently_stays_as_the_game_has_it(tmp_path):
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "Voices", {"install.bat": rf"""@echo off
set "game={GAME}"
CHOICE /C EJ /N /M "Install for (E)nglish or (J)apanese voices?"
if errorlevel 2 (set v=jp) else set v=en
copy /y "%~dp0%v%.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
""", "en.wmp": b"english " * 300, "jp.wmp": b"japanese " * 300})
    played = play(game, folder)
    assert played.work is None
    assert played.problem == ("changed the game in a way that can't go in: op.win32.wmp came "
                              "out different for each language, so it stayed as the game has it")


def test_a_backup_it_makes_of_the_games_file_isnt_the_mod(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Careful", {"install.bat": rf"""@echo off
set "game={GAME}"
md "%game%\backup"
copy /y "%game%\white_data\movie\us\op.win32.wmp" "%game%\backup\" > nul
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", "op.win32.wmp": b"new " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"new " * 300}


def test_a_zones_archive_it_changes_is_said_and_left(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Zone Art", {"install.bat": MOVIE_COPY + r"""
md "%game%\white_data\zone\z0010"
copy /y "%~dp0white_z0010_img.win32.bin" "%game%\white_data\zone\z0010\" > nul
""", "op.win32.wmp": b"new " * 300, "white_z0010_img.win32.bin": b"zone " * 300})
    played = play(game, folder)
    assert laid(played) == {MOVIE: b"new " * 300}
    assert any("white_z0010_img.win32.bin, a zone's archive" in note for note in played.notes)


def test_putting_in_what_the_game_already_has_is_nothing_to_put_in(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Same Again", {"XIII Vanille Install.bat": INJECTOR,
                                           "ff13tool.exe": b"MZ",
                                           f"files/{TRB}": b"vanilla trb " * 100})
    played = play(game, folder)
    assert played.work is None
    assert played.problem == ("only changed the game to what it already has, so there's "
                              "nothing to put in")


def test_an_earlier_plays_leftovers_are_swept(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    left = game / ".patcher-undo" / "work" / "bat-crashed" / "s"
    left.mkdir(parents=True)
    (left / "big").write_bytes(b"x" * 100)
    play(game, drop(tmp_path, "Again", {"install.bat": MOVIE_COPY, "op.win32.wmp": b"n" * 300}))
    assert not (game / ".patcher-undo" / "work" / "bat-crashed").exists()


def test_a_version_that_isnt_one_is_said(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    with pytest.raises(sandbox.SandboxError, match="Gold isn't one of its versions"):
        play(game, menu_mod(tmp_path), version="Gold")
    assert not list((game / ".patcher-undo" / "work").glob("bat-*"))


def test_one_that_puts_nothing_in_leaves_no_stage_behind(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    played = play(game, drop(tmp_path, "Lost", {"install.bat": "@echo off\nWhiteBinTools -u x\n"}))
    assert played.problem and not list((game / ".patcher-undo" / "work").glob("bat-*"))


def test_a_filelist_decrypted_and_encrypted_around_the_inject(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    bat = INJECTOR.replace(
        "..\\ff13tool.exe -i", "..\\ffxiiicrypt.exe -d filelist_scr%v%.win32.bin filelist\n"
                               "..\\ff13tool.exe -i").replace(
        "set lines=0", "..\\ffxiiicrypt.exe -e filelist_scr%v%.win32.bin filelist\nset lines=0")
    folder = injector(tmp_path, bat=bat)
    (folder / "ffxiiicrypt.exe").write_bytes(b"MZ")
    assert set(laid(play(game, folder))) == {f"white_scru/{TRB}"}


def test_a_tool_that_never_finishes_is_stopped(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    monkeypatch.setattr(sandbox, "TOOL_LIMIT", 1)
    folder = drop(tmp_path, "Hang", {"install.bat": "@echo off\n\"%~dp0wait.exe\"\n"})
    stub(folder, "wait.exe", "import time\ntime.sleep(30)\n")
    played = play(game, folder)
    assert "wait.exe was still going" in played.problem
    assert not list((game / ".patcher-undo" / "work").glob("bat-*"))


def test_a_tool_from_the_drop_fixing_a_game_file_its_handed(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Fixer", {"install.bat": rf"""@echo off
"%~dp0fixer.exe" "{GAME}\white_data\movie\us\op.win32.wmp"
"""})
    stub(folder, "fixer.exe", "import pathlib, sys\n"
         "movie = pathlib.Path(sys.argv[1].replace('\\\\', '/'))\n"
         "movie.write_bytes(b'fixed ' + movie.read_bytes()[:20])\n")
    assert laid(play(game, folder)) == {MOVIE: b"fixed opening movie openin"}


def test_what_a_tool_deletes_is_gone_and_what_the_script_deletes_isnt_handed_on(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Cleaner", {"install.bat": rf"""@echo off
set "game={GAME}"
cd /d "%~dp0"
md work
copy /y "%game%\white_data\movie\us\op.win32.wmp" work\op.wmp > nul
copy /y "%game%\white_data\movie\us\op.win32.wmp" work\keep.wmp > nul
"%~dp0eat.exe" work\op.wmp
if exist work\op.wmp echo still there> "%game%\fail.dat"
del work\keep.wmp
"%~dp0look.exe" work
copy /y work\seen.dat "%game%\white_data\movie\us\seen.dat" > nul
"""})
    stub(folder, "eat.exe", "import os, sys\nos.remove(sys.argv[1].replace('\\\\', '/'))\n")
    stub(folder, "look.exe", "import os, sys\n"
         "work = sys.argv[1].replace('\\\\', '/')\n"
         "names = ','.join(sorted(os.listdir(work)))\n"
         "open(os.path.join(work, 'seen.dat'), 'w').write(names)\n")
    assert laid(play(game, folder)) == {"white_data/movie/us/seen.dat": b""}


def test_a_dry_play_that_runs_on_too_long_is_called_a_loop(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    monkeypatch.setattr(sandbox, "GLANCE", 0)
    played = play(game, drop(tmp_path, "Long", {"install.bat": "@echo off\n"
                                                "for /l %%i in (1,1,5000) do set x=%%i\n"
                                                + MOVIE_COPY.replace("@echo off\n", ""),
                                                "op.win32.wmp": b"n" * 300}))
    assert "caught in a loop" in played.problem


def test_a_program_a_bat_and_a_zip_it_puts_beside_the_game_go_in_as_files(tmp_path, monkeypatch):
    import io
    import zipfile

    game, bridge = drawer(tmp_path, monkeypatch)
    before = sums(game)
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as inner:
        inner.writestr("pad/layout.dat", b"layout")
    folder = drop(tmp_path, "Pad Kit", {"install.bat": r"""@echo off
set "game=C:\Program Files (x86)\Steam\steamapps\common\FFXIII"
copy /y "%~dp0Play Modded.bat" "%game%\" > nul
copy /y "%~dp0padfix.exe" "%game%\white_data\prog\win\bin\" > nul
copy /y "%~dp0layouts.zip" "%game%\white_data\prog\win\bin\" > nul
""", "Play Modded.bat": "@echo off\nstart FFXiiiLauncher.exe\n", "padfix.exe": b"MZ" + b"\0" * 100,
        "layouts.zip": packed.getvalue()})
    done = bridge.install_mod("ff13", str(folder))
    assert done["ok"] and done["whole"], done
    assert (game / "Play Modded.bat").read_bytes() == b"@echo off\r\nstart FFXiiiLauncher.exe\r\n"
    assert (game / "white_data/prog/win/bin/padfix.exe").read_bytes() == b"MZ" + b"\0" * 100
    assert (game / "white_data/prog/win/bin/layouts.zip").read_bytes() == packed.getvalue()
    assert bridge.remove_addon("ff13", "Pad Kit", "disk")["ok"]
    assert sums(game) == before


def test_an_unpacker_of_its_own_gets_the_archive_it_ships(tmp_path):
    import io
    import zipfile

    game = xiii(tmp_path / "steam", voices="u")
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as inner:
        inner.writestr("op.win32.wmp", b"unzipped " * 300)
    folder = drop(tmp_path, "Unzip", {"install.bat": rf"""@echo off
"%~dp0unzip.exe" "%~dp0payload.zip" "%TEMP%\payload"
copy /y "%TEMP%\payload\op.win32.wmp" "{GAME}\white_data\movie\us\" > nul
""", "payload.zip": packed.getvalue()})
    stub(folder, "unzip.exe", "import sys, zipfile\n"
         "zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])\n")
    assert laid(play(game, folder)) == {MOVIE: b"unzipped " * 300}


def test_a_tool_run_from_above_the_game_gets_none_of_it_unnamed(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    laid_out = []
    lay = sandbox.World._lay
    monkeypatch.setattr(sandbox.World, "_lay", lambda w, p: laid_out.append(p) or lay(w, p))
    folder = drop(tmp_path, "Wide", {"install.bat": "@echo off\ncd /d C:\\\n\"%~dp0tool.exe\"\n"})
    stub(folder, "tool.exe", "print('ran')\n")
    play(game, folder)
    assert laid_out and not [p for p in laid_out if p.lower().startswith(GAME.lower())]


def slow(monkeypatch) -> None:
    """a PC where every look at the clock finds minutes gone, though no play of it is a loop"""
    ticks = iter(range(0, 1 << 40, 100))
    monkeypatch.setattr(sandbox, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    monkeypatch.setattr(sandbox, "GLANCE", 1 << 40)


GOG = """@echo off
echo Which edition is it? gog or steam
set /p ed=Edition:
if /i "%ed%"=="gog" echo The GOG release isn't supported& goto :eof
"""


def test_a_menu_behind_a_question_lists_the_same_on_a_slow_pc(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    folder = menu_mod(tmp_path)
    # its first answer puts nothing in, so the menu is only reached on a second play
    (folder / "install.bat").write_text(GOG + 'if /i not "%ed%"=="steam" goto :eof\n'
                                        + MENU.replace("@echo off\n", "", 1))
    listed = play(game, folder).versions
    assert listed == ["The Naturey outfit", "The Pulse robe"]
    slow(monkeypatch)
    assert play(game, folder).versions == listed


def test_a_question_is_answered_past_everything_it_turns_away(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = menu_mod(tmp_path)
    (folder / "install.bat").write_text(GOG + MENU.replace("@echo off\n", "", 1))
    assert play(game, folder).versions == ["The Naturey outfit", "The Pulse robe"]


def test_a_menu_still_comes_back_whole_when_its_plays_run_out(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    monkeypatch.setattr(sandbox, "TREE", 1)
    assert play(game, menu_mod(tmp_path)).versions == ["The Naturey outfit", "The Pulse robe"]


NESTED = r"""@echo off
echo Vanille Outfits
echo 1. Install the Naturey outfit
echo 2. Install the Pulse robe
set /p pick=Choose:
if "%pick%"=="1" set src=naturey
if "%pick%"=="2" set src=robe
echo Colour
echo 1. Red
echo 2. Blue
set /p tint=Choose a colour:
if "%tint%"=="1" set src=%src%-red
if "%tint%"=="2" set src=%src%-blue
set "game=C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII"
cd /d "%~dp0%src%"
copy /y "%game%\white_data\sys\filelist_scru.win32.bin" . > nul
..\ff13tool.exe -i filelist_scru.win32.bin "%game%\white_data\sys\white_scru.win32.bin" chr\pc\c004\bin\c004.win32.trb > nul
copy /y filelist_scru.win32.bin "%game%\white_data\sys\" > nul
"""


def test_a_menu_in_a_menu_lists_the_versions_it_installs_however_long_it_takes(
        tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Vanille Outfits", {
        "install.bat": NESTED, "ff13tool.exe": b"MZ",
        **{f"{o}-{c}/{TRB}": f"{o} {c} ".encode() * 50
           for o in ("naturey", "robe") for c in ("red", "blue")}})
    # a question at a time, each answer with what came before it in front
    listed = play(game, folder).versions
    assert listed == ["The Naturey outfit", "The Pulse robe"]
    assert play(game, folder, version="The Pulse robe").versions == [
        "The Pulse robe, Red", "The Pulse robe, Blue"]
    # a slow PC lists the same, since the search counts plays rather than seconds
    slow(monkeypatch)
    assert play(game, folder).versions == listed
    assert laid(play(game, folder, version="The Pulse robe, Blue")) == \
        {f"white_scru/{TRB}": b"robe blue " * 50}


def test_what_a_run_from_the_game_folder_mod_unpacks_over_the_game_goes_in(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Overlay", {
        "Unpack into your game folder then run me.bat": r"""@echo off
if not exist white_data\sys\filelist_scru.win32.bin goto wrong
copy /y white_data\prog\win\bin\fonts.dat white_data\prog\win\bin\fonts2.dat > nul
goto :eof
:wrong
echo Unpack me into the game first
""", MOVIE: b"overlaid movie " * 300, "white_data/prog/win/bin/fonts.dat": b"fonts " * 100,
        "tool.exe": b"MZ"})
    # over the game's movie, beside its exe, and what the .bat made of them;
    # not the .bat or its tool, which only sat in the game's folder to run
    assert laid(play(game, folder)) == {MOVIE: b"overlaid movie " * 300,
                                        "white_data/prog/win/bin/fonts.dat": b"fonts " * 100,
                                        "white_data/prog/win/bin/fonts2.dat": b"fonts " * 100}


def test_new_folders_a_run_from_the_game_folder_mod_unpacks_inside_the_games_go_in(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Shader Pack", {
        "install.bat": "@echo off\nif not exist white_data\\sys\\filelist_scru.win32.bin "
                       "echo Unpack me into the game first\n",
        "white_data/prog/win/bin/reshade-shaders/Shaders/Clarity.fx": b"// shader",
        "white_data/movie/us/extra/bonus.win32.wmp": b"bonus " * 300})
    assert laid(play(game, folder)) == {
        "white_data/prog/win/bin/reshade-shaders/Shaders/Clarity.fx": b"// shader",
        "white_data/movie/us/extra/bonus.win32.wmp": b"bonus " * 300}


def test_a_game_file_written_over_with_what_it_already_was_is_nothing_to_put_in(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "No-op", {"install.bat": MOVIE_COPY,
                                      "op.win32.wmp": (game / MOVIE).read_bytes()})
    played = play(game, folder)
    assert played.work is None
    assert played.problem == ("only changed the game to what it already has, so there's "
                              "nothing to put in")


LR_INJECTOR = r"""@echo off
set "game=C:\Program Files (x86)\Steam\steamapps\common\LR"
CHOICE /C EJ /N /M "Install for (E)nglish or (J)apanese voices?"
if %ERRORLEVEL% EQU 1 set v=a
if %ERRORLEVEL% EQU 2 set v=v
if not exist "%game%\weiss_data\sys\filelist%v%.win32.bin" set v=
cd /d "%~dp0files"
copy /y "%game%\weiss_data\sys\filelist%v%.win32.bin" . > nul
..\ff13tool.exe -i -ff133 filelist%v%.win32.bin "%game%\weiss_data\sys\white_img%v%.win32.bin" chr\pc\c000\bin\c000.win32.trb > nul
copy /y filelist%v%.win32.bin "%game%\weiss_data\sys\" > nul
"""
LR_TRB = "chr/pc/c000/bin/c000.win32.trb"


@pytest.mark.parametrize("sets, japanese", [("av", "v"), (["a", ""], "c")])
def test_lrs_voice_sets_each_get_the_play_meant_for_them(tmp_path, sets, japanese):
    from tests.test_patchdata import make_lr

    game = make_lr(tmp_path / "steam" / "steamapps" / "common", sets=sets)
    folder = drop(tmp_path, "LR Outfit", {"install.bat": LR_INJECTOR, "ff13tool.exe": b"MZ",
                                          f"files/{LR_TRB}": b"new outfit " * 100})
    # a letterless set is the Japanese release, which a folder says with c
    assert laid(play(game, folder)) == {f"white_imga/{LR_TRB}": b"new outfit " * 100,
                                        f"white_img{japanese}/{LR_TRB}": b"new outfit " * 100}


def test_lr_through_the_drawer_both_voice_sets_and_back_out(tmp_path, monkeypatch):
    from tests.test_patchdata import make_lr

    library = tmp_path / "steam"
    game = make_lr(library / "steamapps" / "common")
    (library / "steamapps" / "appmanifest_345350.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"345350"\n\t"installdir"\t\t"LR"\n}\n')
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths={"ffxiii3": MANIFESTS / "ffxiii3.json"},
                      steam_library_paths=[library])
    before = sums(game)
    folder = drop(tmp_path, "LR Outfit", {"install.bat": LR_INJECTOR, "ff13tool.exe": b"MZ",
                                          f"files/{LR_TRB}": b"new outfit " * 100})
    done = bridge.install_mod("lr", str(folder))
    assert done["ok"] and done["whole"], done
    from patcher.mods.hd_zones import read_record
    for letter in "av":
        sys_dir = game / "weiss_data" / "sys"
        entry = game_files.entries(sys_dir / f"filelist{letter}.win32.bin")[LR_TRB]
        assert read_record(sys_dir / f"white_img{letter}.win32.bin", entry) == \
            b"new outfit " * 100
    assert bridge.remove_addon("lr", "LR Outfit", "files")["ok"]
    assert sums(game) == before


def test_a_mod_that_also_runs_a_4gb_patcher_says_what_stopped_it(tmp_path, monkeypatch):
    _, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Textures", {"install.bat": r"""@echo off
4gb_patch.exe "C:\Program Files (x86)\Steam\steamapps\common\FFXIII\white_data\prog\win\bin\ffxiiiimg.exe"
curl -L -o textures.zip https://example.com/textures.zip
if errorlevel 1 (echo Download failed & exit /b 1)
""", "4gb_patch.exe": b"MZ"})
    done = bridge.install_mod("ff13", str(folder))
    assert done["error"] == ("Textures wasn't installed: its install.bat needs to download "
                             "something, and that isn't done here.")


def test_parts_joined_with_copy_b_are_streamed_together(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    monkeypatch.setattr(sandbox, "INLINE", 64)
    folder = drop(tmp_path, "Split", {"install.bat": rf"""@echo off
copy /b "%~dp0op.part1" + "%~dp0op.part2" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "op.part1": b"first half " * 100, "op.part2": b"second half " * 100})
    assert laid(play(game, folder)) == {MOVIE: b"first half " * 100 + b"second half " * 100}


def test_a_tool_is_never_handed_the_play_paths_however_its_arguments_put_them(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Args", {"install.bat": rf"""@echo off
set "game={GAME}"
cd /d "%~dp0"
"%~dp0tool.exe" in="%game%\white_data\movie\us\op.win32.wmp" @"%game%\files.txt" -list="%~dp0a.txt;%game%\b.txt" -o"%TEMP%\out" -Dgame.dir="%game%" ..\..\..\..\white_data \Users\Player "-o%game%\white_data" in="\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII\white_data" -o\Users\Player @\Users\Player a;\Users\Player -o..\..\..\..\white_data "%~dp0"..\..\..\..\..\white_data "-outdir%game%"
copy /y argv.json "%game%\argv.json" > nul
""", "a.txt": b"a"})
    stub(folder, "tool.exe", "import json, sys\n"
         "open('argv.json', 'w').write(json.dumps(sys.argv[1:]))\n")
    import json
    argv = json.loads(laid(play(game, folder))["argv.json"])
    assert len(argv) == 15
    # every path is on the stage: none the play's, none of the PC's own
    for word in argv:
        word = word.replace("\\", "/")
        assert "C:/Users" not in word and "Program Files (x86)/Steam" not in word, word
        assert "/.patcher-undo/work/bat-" in word, word


def test_a_tool_cant_reach_the_real_game_through_a_keyed_argument(tmp_path):
    # the play's game path is the real one on a PC where Steam put the game in
    # its default library, which this stand-in reads it as
    game = xiii(tmp_path / "steam", voices="u")
    before = sums(game)
    folder = drop(tmp_path, "Keyed", {"install.bat": rf"""@echo off
"%~dp0tool.exe" in="{GAME}\white_data\movie\us\op.win32.wmp"
"""})
    stub(folder, "tool.exe", "import pathlib, sys\n"
         f"steam = {str(tmp_path / 'steam')!r}\n"
         "word = sys.argv[1].split('=', 1)[-1]\n"
         "if word.lower().startswith('c:\\\\program files (x86)\\\\steam'):\n"
         "    word = steam + '/' + '/'.join(word.split('\\\\')[3:])\n"
         "path = pathlib.Path(word)\n"
         "if path.is_file():\n"
         "    path.write_bytes(b'patched by tool')\n")
    played = play(game, folder)
    assert sums(game) == before
    assert laid(played) == {MOVIE: b"patched by tool"}


def test_a_switch_among_its_arguments_doesnt_lose_what_it_wrote(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Quiet", {"install.bat": rf"""@echo off
cd /d "%~dp0"
"%~dp0maker.exe" /quiet
"%~dp0fixer.exe" /q "{GAME}\white_data\prog\win\bin\ffxiiiimg.exe"
copy /y made.wmp "{GAME}\white_data\movie\us\op.win32.wmp" > nul
"""})
    stub(folder, "maker.exe", "open('made.wmp', 'wb').write(b'made ' * 300)\n")
    stub(folder, "fixer.exe", "import pathlib, sys\n"
         "exe = pathlib.Path(sys.argv[-1].replace('\\\\', '/'))\n"
         "exe.write_bytes(exe.read_bytes() + b'fixed')\n")
    got = laid(play(game, folder))
    assert got[MOVIE] == b"made " * 300
    assert got["white_data/prog/win/bin/ffxiiiimg.exe"].endswith(b"fixed")


def test_what_a_tool_leaves_in_its_own_temp_comes_back(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Temp", {"install.bat": rf"""@echo off
"%~dp0maker.exe"
copy /y "%TEMP%\made.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
"""})
    stub(folder, "maker.exe", "import os\n"
         "open(os.path.join(os.environ['TEMP'], 'made.wmp'), 'wb').write(b'temp ' * 300)\n")
    assert laid(play(game, folder)) == {MOVIE: b"temp " * 300}


def test_where_a_command_lines_paths_are_as_written():
    def paths(text):
        return [(text[start:end], before, after, kind)
                for start, end, before, after, kind in sandbox._spans(text)]

    assert paths(r'in="C:\a b\x.wmp" @"C:\g\list.txt" -l="C:\a.txt;C:\b.txt"') == [
        (r"C:\a b\x.wmp", True, True, "drive"), (r"C:\g\list.txt", True, True, "drive"),
        (r"C:\a.txt", True, True, "drive"), (r"C:\b.txt", True, True, "drive")]
    # the folder's closing backslash stays where the script put it, and a
    # switch is a switch with or without quotes round it
    assert paths(r'-dir "C:\Mod\" -oC:\out "-oC:\x" /log:C:\x.txt C:\a,C:\b -outdirD:\y') == [
        ("C:\\Mod\\", True, True, "drive"), (r"C:\out", False, False, "drive"),
        (r"C:\x", True, True, "drive"), (r"C:\x.txt", False, False, "drive"),
        (r"C:\a", False, False, "drive"), (r"C:\b", False, False, "drive"),
        (r"D:\y", False, False, "drive")]
    # climbing and from the root where a path can start, the key kept off it,
    # and on past a closing quote when more of the path follows
    assert paths(r'in=..\..\x @..\y -o..\..\z \Users\x "C:\Mod\"..\..\w') == [
        (r"..\..\x", False, False, "climb"), (r"..\y", False, False, "climb"),
        (r"..\..\z", False, False, "climb"), (r"\Users\x", False, False, "root"),
        ('C:\\Mod\\"..\\..\\w', True, False, "drive")]
    # a list's next path, a .. part way along, and what only looks like one
    assert paths(r'-l="C:\a.txt;\Users\b.txt" white_data\..\..\x -msg=\"C:\y\"') == [
        (r"C:\a.txt", True, True, "drive"), (r"\Users\b.txt", True, True, "root"),
        (r"white_data\..\..\x", False, False, "climb"), ("C:\\y\\", True, True, "drive")]
    # a switch with a key isn't a drive, nor is /D:, but -oC: and -outdirD: are
    assert paths(r"/OUT:\Users\x /D:\y -oC:\a -outdirD:\b -r:\d+") == [
        (r"\Users\x", False, False, "root"), (r"\y", False, False, "root"),
        (r"C:\a", False, False, "drive"), (r"D:\b", False, False, "drive"),
        (r"\d+", False, False, "root")]
    # a quoted list, a key: and a list item that climbs, and / for \\
    assert paths(r'--in="C:\a.wmp","C:\b.txt" in:..\x a.txt,..\y "/Program Files (x86)/s"') == [
        (r"C:\a.wmp", True, True, "drive"), (r"C:\b.txt", True, True, "drive"),
        (r"..\x", False, False, "climb"), (r"..\y", False, False, "climb"),
        ("/Program Files (x86)/s", True, True, "root")]
    # a , past a drive path's closing quote isn't its name, one inside it is
    assert paths(r'"C:\a.txt",..\x "C:\Outfit, Red\..\x"') == [
        (r"C:\a.txt", True, True, "drive"), (r"..\x", False, False, "climb"),
        (r"C:\Outfit, Red\..\x", True, True, "drive")]
    assert paths(r"https://example.com/a/../b /q /log:x /f:csv 12:30 HKLM\SOFTWARE\Valve a=b") == []


def test_a_path_from_the_root_is_only_one_somewhere_the_play_has(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Mod", {"install.bat": "@echo off\r\n"})
    with game_files.Source(folder) as source:
        stage = sandbox.Stage(game, game_files.read_game(game), source, source.members(),
                              "install.bat", "Mod", tmp_path / "stage")
    world = sandbox.World(stage, None, dry=False)
    here = r"C:\Users\Player\Downloads\Mod"

    def paths(text):
        return [text[start:end] for start, end, *_ in world._paths(text, here)]

    # a new folder two down is still the game's, and \n, \nfullscreen=0, \t,
    # \d+ and key:\x aren't paths
    new = GAME[2:] + r"\white_data\NewMod\sub"
    assert paths(rf'-o"{new}" "\nFullscreen=0" "\nfullscreen=0" \t \d+ abc:\x') == [new]
    # and one the play hasn't got is a path when it's the real game, from the
    # drive the stage is on
    stage.folder = PureWindowsPath(r"D:\SteamLibrary\steamapps\common\FFXIII\.patcher-undo\w")
    stage.install_dir = PureWindowsPath(r"D:\SteamLibrary\steamapps\common\FFXIII")
    real = r"\SteamLibrary\steamapps\common\FFXIII\white_data"
    assert paths(rf'"{real}" \Games\x') == [real]


@pytest.mark.parametrize("bat", ["Uninstall.bat", "Uninstall Patch.bat", "Restore Vanilla.bat"])
def test_loose_files_with_an_uninstall_bat_go_in_as_themselves(tmp_path, monkeypatch, bat):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Movie Pack", {
        MOVIE: b"new opening " * 300, "originals/op.win32.wmp": b"older opening " * 300,
        bat: r"""@echo off
set "game=C:\Program Files (x86)\Steam\steamapps\common\FFXIII"
copy /y "%~dp0originals\op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""})
    done = bridge.install_mod("ff13", str(folder))
    assert done["ok"], done
    assert (game / MOVIE).read_bytes() == b"new opening " * 300
    assert "Played" not in done["message"]
    assert (f"{bat}, which only puts the game's own files back" if bat == "Restore Vanilla.bat"
            else f"{bat}, for taking it back off, which Remove does here") in done["message"]


def test_left_out_names_the_bat_that_was_played(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Movie Pack", {
        MOVIE: b"new opening " * 300,
        "Install.bat": "@echo off\r\necho Copy the white_data folder into your game folder.\r\n",
        "Add Desktop Shortcut.bat": "@echo off\r\necho made a shortcut\r\n"})
    done = bridge.install_mod("ff13", str(folder))
    assert "Left out: Install.bat, its installer, which played" in done["message"], done


UNCUT = r"""@echo off
if not exist white_data\movie\us\op.win32.wmp goto wrong
copy /y white_data\Uncensored\op_uncut.win32.wmp white_data\movie\us\op.win32.wmp > nul
goto :eof
:wrong
echo Put this in your game folder and run it again
"""


def test_a_run_from_the_game_folder_mod_named_for_removing_something_goes_in(
        tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    before = sums(game)
    folder = drop(tmp_path, "Uncensored", {
        "Remove Censorship.bat": UNCUT,
        "white_data/Uncensored/op_uncut.win32.wmp": b"uncut opening " * 300})
    done = bridge.install_mod("ff13", str(folder))
    assert done["ok"], done
    assert (game / MOVIE).read_bytes() == b"uncut opening " * 300
    # the copy it put over the opening, not the one it copied from as well
    assert set(sums(game)) == set(before)


def test_a_loaders_own_files_go_in_beside_what_its_bat_joined(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Tex Mod", {
        "Join textures.bat": r"""@echo off
if not exist white_data\prog\win\bin\ffxiiiimg.exe goto :eof
set "tex=white_data\prog\win\bin\TexMod"
copy /b "%tex%\textures.part1" + "%tex%\textures.part2" "%tex%\textures.pak" > nul
""",
        "white_data/prog/win/bin/d3d9.dll": b"MZ" + b"\0" * 200,
        "white_data/prog/win/bin/TexMod/config.ini": "[TexMod]\r\nEnabled=1\r\n",
        "white_data/prog/win/bin/TexMod/textures.part1": b"first " * 200,
        "white_data/prog/win/bin/TexMod/textures.part2": b"second " * 200})
    # its settings stay, what it joined from doesn't
    assert set(laid(play(game, folder))) == {
        "white_data/prog/win/bin/d3d9.dll", "white_data/prog/win/bin/TexMod/config.ini",
        "white_data/prog/win/bin/TexMod/textures.pak"}


def test_a_new_folder_from_the_root_two_down_is_the_stages(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    before = sums(game)
    folder = drop(tmp_path, "New Folder Mod", {"install.bat": r"""@echo off
if not exist white_data\sys\filelist_scru.win32.bin goto :eof
"%~dp0maker.exe" -o"%~p0white_data\NewMod\sub"
"""})
    # a path from the root is the real game here when it's the play's as written
    stub(folder, "maker.exe", "import pathlib, sys\n"
         f"steam = {str(tmp_path / 'steam')!r}\n"
         "out = sys.argv[1][2:]\n"
         "if out.startswith(chr(92)):\n"
         "    out = steam + '/' + '/'.join(out.split(chr(92))[4:])\n"
         "target = pathlib.Path(out.replace(chr(92), '/'))\n"
         "target.mkdir(parents=True, exist_ok=True)\n"
         "(target / 'made.wmp').write_bytes(b'made ' * 300)\n")
    assert laid(play(game, folder)) == {"white_data/NewMod/sub/made.wmp": b"made " * 300}
    assert sums(game) == before


MAKER = """import pathlib, sys
out = pathlib.Path(sys.argv[1][2:].replace(chr(92), '/'))
out.mkdir(parents=True, exist_ok=True)
(out / 'made.wmp').write_bytes(b'made ' * 300)
"""


def test_an_output_folders_down_that_arent_there_yet_comes_back(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Deep Out", {"install.bat": rf"""@echo off
"%~dp0maker.exe" -o"C:\ModOut\movie"
copy /y "C:\ModOut\movie\made.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
"%~dp0maker.exe" -o"{GAME}\white_data\prog\win\bin\hd_mod\movie"
"""})
    stub(folder, "maker.exe", MAKER)
    assert laid(play(game, folder)) == {
        MOVIE: b"made " * 300, "white_data/prog/win/bin/hd_mod/movie/made.wmp": b"made " * 300}


def test_a_temp_file_is_what_the_script_last_made_it_for_the_next_tool(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Phases", {"install.bat": rf"""@echo off
"%~dp0phase.exe" one
echo two> "%TEMP%\state.txt"
"%~dp0phase.exe" more
copy /y "%TEMP%\state.txt" "{GAME}\white_data\movie\us\state.txt" > nul
"""})
    stub(folder, "phase.exe", "import os, pathlib, sys\n"
         "state = pathlib.Path(os.environ['TEMP']) / 'state.txt'\n"
         "if sys.argv[1] == 'one':\n"
         "    state.write_bytes(b'one')\n"
         "else:\n"
         "    state.write_bytes(state.read_bytes().strip() + b'+more')\n")
    assert laid(play(game, folder)) == {"white_data/movie/us/state.txt": b"two+more"}


def test_a_list_of_quoted_paths_with_a_comma_between(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Comma List", {"install.bat": rf"""@echo off
"%~dp0tool.exe" --in="{GAME}\white_data\movie\us\op.win32.wmp","%~dp0tail.bin" --out="%~dp0made.wmp"
copy /y "%~dp0made.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "tail.bin": b"tail"})
    stub(folder, "tool.exe", "import pathlib, sys\n"
         "args = dict(a.lstrip('-').split('=', 1) for a in sys.argv[1:])\n"
         "parts = [pathlib.Path(p.replace(chr(92), '/')) for p in args['in'].split(',')]\n"
         "pathlib.Path(args['out'].replace(chr(92), '/')).write_bytes(\n"
         "    b''.join(p.read_bytes() for p in parts))\n")
    movie = (game / MOVIE).read_bytes()
    assert laid(play(game, folder)) == {MOVIE: movie + b"tail"}


def test_three_questions_answered_yes_are_found(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Asks", {"install.bat": rf"""@echo off
echo Install the new opening? (y/n)
set /p a=
echo Install the new title card? (y/n)
set /p b=
echo Install the new credits? (y/n)
set /p c=
if /i "%a%"=="n" goto skip
if /i "%b%"=="n" goto skip
if /i "%c%"=="n" goto skip
if /i "%a%"=="y" if /i "%b%"=="y" if /i "%c%"=="y" copy /y "%~dp0op.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:skip
echo Nothing picked
""", "op.wmp": b"new opening " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"new opening " * 300}


def test_parts_from_a_zipped_drop_arent_copied_out_for_every_play(tmp_path, monkeypatch):
    import zipfile

    monkeypatch.setattr(sandbox, "INLINE", 64)
    game = xiii(tmp_path / "steam", voices="u")
    archive = tmp_path / "Split.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Split/install.bat", rf"""@echo off
copy /b "%~dp0op.part1" + "%~dp0op.part2" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""".replace("\n", "\r\n"))
        z.writestr("Split/op.part1", b"first half " * 1000)
        z.writestr("Split/op.part2", b"second half " * 1000)
    with game_files.Source(archive) as source:
        opened, taken = source.opened, []
        source.opened = lambda member: taken.append(member) or opened(member)
        played = sandbox.play(game, source, source.members(), "Split")
        assert (played.work / MOVIE).read_bytes() == b"first half " * 1000 + b"second half " * 1000
    # each part out of the zip once, when what it went into was laid out
    assert sorted(taken) == ["Split/op.part1", "Split/op.part2"]


# a stand-in that does what 7-Zip's -o does with a folder it's handed, the
# default Steam library read as where this test's library is: on a PC where
# Steam put the game there, the play's paths are the real game's
SPILLER = """import pathlib, sys
steam = {steam!r}
for word in sys.argv[1:]:
    path = word[2:] if word[:2].lower() in ('-o', '/o') else word
    if path.lower().startswith('c:\\\\program files (x86)\\\\steam'):
        real = pathlib.Path(steam + '/' + '/'.join(path.split('\\\\')[3:]))
        if real.is_dir():
            (real / 'from_the_tool.bin').write_bytes(b'written by the tool')
"""


def test_a_switch_quoted_whole_is_still_swapped_and_7zips_own_line_with_it(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    before = sums(game)
    folder = drop(tmp_path, "Quoted", {"install.bat": rf"""@echo off
set "game={GAME}"
"%~dp0tool.exe" "-o%game%\white_data" -y
"%~dp07za.exe" x "%~dp0payload.7z" -o"%game%\white_data" -y
""", "payload.7z": b"7z\xbc\xaf\x27\x1c not one the drawer can open"})
    for exe in ("tool.exe", "7za.exe"):
        stub(folder, exe, SPILLER.format(steam=str(tmp_path / "steam")))
    play(game, folder)
    assert sums(game) == before


def test_what_a_run_from_the_game_folder_mod_only_works_from_stays_out(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    before = sums(game)
    bat = r"""@echo off
if not exist white_data\sys\filelist_scru.win32.bin goto wrong
cd white_data\HairMod\%~n0
..\..\..\ff13tool.exe -i ..\..\sys\filelist_scru.win32.bin ..\..\sys\white_scru.win32.bin chr\pc\c004\bin\c004.win32.trb > nul
goto :eof
:wrong
echo Unpack this into your game folder first, then run it
"""
    folder = drop(tmp_path, "Hair Pick", {
        "long.bat": bat, "short.bat": bat, "ff13tool.exe": b"MZ",
        f"white_data/HairMod/long/{TRB}": b"long hair " * 100,
        f"white_data/HairMod/short/{TRB}": b"short hair " * 100,
        "white_data/movie/us/originals/op.win32.wmp": (game / MOVIE).read_bytes()})
    assert bridge.install_mod("ff13", str(folder))["versions"] == ["long", "short"]
    done = bridge.install_mod("ff13", str(folder), version="short")
    assert done["ok"] and done["whole"], done
    from patcher.mods.hd_zones import read_record
    entry = game_files.entries(game / "white_data/sys/filelist_scru.win32.bin")[TRB]
    assert read_record(game / "white_data/sys/white_scru.win32.bin", entry) == b"short hair " * 100
    # neither version's payload, nor the original it carries, went in loose
    assert not [rel for rel in set(sums(game)) - set(before)
                if "HairMod" in rel or "originals" in rel]


def test_a_tool_has_a_temp_wherever_it_runs_with_what_the_script_left_there(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Temps", {"install.bat": rf"""@echo off
copy /y "%~dp0big.bin" "%TEMP%\big.bin" > nul
"%~dp0maker.exe" -o"C:\ModOut"
cd /d "%USERPROFILE%"
"%~dp0maker.exe" -o"%USERPROFILE%\made"
copy /y "C:\ModOut\made.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
copy /y "%USERPROFILE%\made\seen.txt" "{GAME}\white_data\movie\us\seen.txt" > nul
copy /y "C:\ModOut\seen.txt" "{GAME}\white_data\movie\us\seen0.txt" > nul
""", "big.bin": b"b" * 5000})
    stub(folder, "maker.exe", "import os, pathlib, sys\n"
         "temp = pathlib.Path(os.environ['TEMP'])\n"
         "(temp / 'scratch.tmp').write_bytes(b'half done')\n"
         "out = pathlib.Path(sys.argv[1][2:].replace('\\\\', '/'))\n"
         "out.mkdir(parents=True, exist_ok=True)\n"
         "(out / 'made.wmp').write_bytes(b'made ' * 300)\n"
         "(out / 'seen.txt').write_text(','.join(sorted(p.name for p in temp.iterdir())))\n")
    got = laid(play(game, folder))
    assert got[MOVIE] == b"made " * 300
    # its own scratch and what the script left there, as on the PC
    assert got["white_data/movie/us/seen.txt"] == b"big.bin,scratch.tmp"
    assert got["white_data/movie/us/seen0.txt"] == b"big.bin,scratch.tmp"


def test_a_temp_the_script_filled_goes_over_only_named(tmp_path, monkeypatch):
    game = xiii(tmp_path / "steam", voices="u")
    monkeypatch.setattr(sandbox, "BIG", 1000)
    folder = drop(tmp_path, "Full Temp", {"install.bat": rf"""@echo off
for %%n in (1 2 3 4) do copy /y "%~dp0part.bin" "%TEMP%\part%%n.bin" > nul
"%~dp0look.exe" "{GAME}\white_data\movie\us\seen.txt"
"%~dp0look.exe" "{GAME}\white_data\movie\us\seen2.txt" "%TEMP%\part2.bin"
""", "part.bin": b"p" * 400})
    stub(folder, "look.exe", "import os, pathlib, sys\n"
         "names = sorted(p.name for p in pathlib.Path(os.environ['TEMP']).iterdir())\n"
         "pathlib.Path(sys.argv[1]).write_text('seen:' + ','.join(names))\n")
    # 1.6 KB of what a program may never look at is past this BIG
    assert laid(play(game, folder)) == {"white_data/movie/us/seen.txt": b"seen:",
                                        "white_data/movie/us/seen2.txt": b"seen:part2.bin"}


def test_what_the_script_deletes_from_temp_is_gone_for_its_next_tool(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Temp Tidy", {"install.bat": rf"""@echo off
"%~dp0leave.exe"
del "%TEMP%\left.tmp"
"%~dp0look.exe" "{GAME}\white_data\movie\us\seen.txt"
"""})
    stub(folder, "leave.exe", "import os, pathlib\n"
         "(pathlib.Path(os.environ['TEMP']) / 'left.tmp').write_bytes(b'scratch')\n")
    stub(folder, "look.exe", "import os, pathlib, sys\n"
         "names = sorted(p.name for p in pathlib.Path(os.environ['TEMP']).iterdir())\n"
         "pathlib.Path(sys.argv[1]).write_text('seen:' + ','.join(names))\n")
    assert laid(play(game, folder)) == {"white_data/movie/us/seen.txt": b"seen:"}


def test_a_lone_bat_named_for_restoring_is_played_and_an_uninstall_isnt(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Restored Opening", {"Restore the Original Opening.bat": r"""@echo off
copy /b "%~dp0op.part1" + "%~dp0op.part2" "C:\Program Files (x86)\Steam\steamapps\common\FFXIII\white_data\movie\us\op.win32.wmp" > nul
""", "op.part1": b"first half " * 100, "op.part2": b"second half " * 100})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    assert (game / MOVIE).read_bytes() == b"first half " * 100 + b"second half " * 100
    alone = drop(tmp_path, "Old Tools", {"Uninstall.bat": "@echo off\necho removing\n"})
    assert bridge.install_mod("ff13", str(alone))["error"] == (
        "Old Tools's Uninstall.bat is for taking a mod back off, so it wasn't played.")


def test_a_key_or_an_at_on_a_climbing_path_and_an_escape_that_isnt_a_path(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Keyed Climb", {"install.bat": rf"""@echo off
cd /d "%~dp0tools\bin"
..\..\tool.exe in=..\..\data\op.wmp @..\..\data\list.txt out=made.wmp "\r\nFullscreen=0"
copy /y made.wmp "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "data/op.wmp": b"new movie " * 300, "data/list.txt": "a\r\nb\r\n",
        "tools/bin/keep.txt": "the tool runs from here"})
    stub(folder, "tool.exe", "import pathlib, sys\n"
         "args = dict(a.split('=', 1) for a in sys.argv[1:] if '=' in a and a[0] != chr(92))\n"
         "src = pathlib.Path(args['in'].replace(chr(92), '/'))\n"
         "listed = pathlib.Path([a for a in sys.argv[1:] if a[0] == '@'][0][1:].replace(chr(92), '/'))\n"
         "words = listed.read_text(encoding='utf-8').split()\n"
         "assert sys.argv[-1] == chr(92) + 'r' + chr(92) + 'nFullscreen=0', sys.argv[-1]\n"
         "pathlib.Path(args['out']).write_bytes(src.read_bytes() + ' '.join(words).encode())\n")
    played = play(game, folder)
    assert laid(played) == {MOVIE: b"new movie " * 300 + b"a b"}, played.problem


def test_a_join_that_cant_be_read_through_leaves_nothing_half_written(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Odd Join", {"install.bat": rf"""@echo off
set "game={GAME}"
cd /d "%~dp0files"
..\ff13tool.exe -i "%game%\white_data\sys\filelist_scru.win32.bin" "%game%\white_data\sys\white_scru.win32.bin" {TRB} > nul
cd /d "%game%\white_data\sys"
copy /b %~dp0head.bin + white_scru.win32.bin ..\movie\us\op.win32.wmp > nul
""", "ff13tool.exe": b"MZ", f"files/{TRB}": b"new trb " * 100, "head.bin": b"head " * 100})
    played = play(game, folder)
    assert MOVIE not in laid(played)
    assert any(note.startswith("op.win32.wmp stayed as it was") for note in played.notes)


def test_appending_to_a_file_in_a_loop_stays_one_flat_join(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Many Parts", {"install.bat": rf"""@echo off
cd /d "%~dp0"
copy /b first.bin whole.bin > nul
for /l %%i in (1,1,1500) do copy /b whole.bin + piece.bin whole.bin > nul
copy /y whole.bin "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "first.bin": b"F" * 100, "piece.bin": b"p"})
    assert laid(play(game, folder)) == {MOVIE: b"F" * 100 + b"p" * 1500}


BLANK = r"""@echo off
set "game=C:\Program Files (x86)\Steam\steamapps\common\FFXIII"
if not exist "%game%\FFXiiiLauncher.exe" goto :eof
copy /y "%~dp0blank.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
"""


@pytest.mark.parametrize("bat", ["Disable Intro Video.bat", "Intro Video Disabled.bat"])
def test_a_mod_named_for_turning_something_off_is_played(tmp_path, monkeypatch, bat):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "No Intro", {bat: BLANK, "blank.wmp": b"blank " * 300})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    assert (game / MOVIE).read_bytes() == b"blank " * 300


@pytest.mark.parametrize("keep", ["originals", "Original Opening", "Backup Files", "Stock"])
@pytest.mark.parametrize("bat", ["Restore Original Opening.bat", "Remove New Opening.bat"])
def test_an_undo_beside_files_that_go_in_as_they_are_is_passed_over(
        tmp_path, monkeypatch, bat, keep):
    game, bridge = drawer(tmp_path, monkeypatch)
    # the opening's another mod's already, so putting the originals back changes it
    (game / MOVIE).write_bytes(b"someone else's opening " * 200)
    folder = drop(tmp_path, "Opening B", {
        MOVIE: b"opening B " * 300, f"{keep}/op.win32.wmp": b"vanilla opening " * 300,
        bat: rf"""@echo off
set "game=C:\Program Files (x86)\Steam\steamapps\common\FFXIII"
copy /y "%~dp0{keep}\op.win32.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
"""})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    assert (game / MOVIE).read_bytes() == b"opening B " * 300


def test_an_installer_that_checks_for_the_launcher_beats_a_helper(tmp_path, monkeypatch):
    found = sandbox.find([("Install.bat", 1), ("Visit Nexus Page.bat", 1)], {
        "Install.bat": b'if not exist "%~dp0FFXiiiLauncher.exe" goto :eof\r\n'
                       b'xcopy /s /y "%~dp0Data\\*" "%~dp0" > nul\r\n',
        "Visit Nexus Page.bat": b"start https://www.nexusmods.com/finalfantasy13\r\n"}.get,
        "white_data")
    assert found[0][0] == "Install.bat"


STORE = """@echo off
echo If you have the Microsoft Store or GOG version, type MS or GOG.
set /p ed=Otherwise just press Enter:
"""


def test_a_store_question_then_four_parts_is_answered(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Parts", {"install.bat": STORE + rf"""choice /c YN /m "Install the new opening"
set op=%errorlevel%
choice /c YN /m "Install the HD title screen"
choice /c YN /m "Install the new menu sounds"
choice /c YN /m "Back up the game's files first"
if /i "%ed%"=="ms" goto :eof
if /i "%ed%"=="gog" goto :eof
if "%op%"=="1" copy /y "%~dp0opening.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "opening.wmp": b"new opening " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"new opening " * 300}


def test_a_store_question_then_a_big_menu_lists_it_all(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    lines = [f"echo {n}. Outfit {n}" for n in range(1, 13)]
    lines += ["set /p pick=Which outfit? ", 'if /i "%ed%"=="ms" goto :eof',
              'if /i "%ed%"=="gog" goto :eof']
    lines += [f'if "%pick%"=="{n}" copy /y "%~dp0outfits\\{n}.wmp" '
              f'"{GAME}\\white_data\\movie\\us\\op.win32.wmp" > nul' for n in range(1, 13)]
    folder = drop(tmp_path, "Outfits", {"install.bat": STORE + "\n".join(lines) + "\n",
                                        **{f"outfits/{n}.wmp": f"outfit {n} ".encode() * 300
                                           for n in range(1, 13)}})
    assert play(game, folder).versions == [f"Outfit {n}" for n in range(1, 13)]


def test_a_store_question_then_a_menu_with_an_unpack_only_choice(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Opening", {"install.bat": rf"""@echo off
echo Type GOG if you have the GOG version, or just press Enter:
set /p ed=
echo 1. Install the new opening
echo 2. Only unpack the files here, to copy them in yourself
choice /c 12 /n /m "Pick 1 or 2:"
if errorlevel 2 goto unpack
if /i "%ed%"=="gog" goto :eof
copy /y "%~dp0opening.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:unpack
"%~dp07za.exe" x "%~dp0files.zip" -o"%~dp0unpacked" -y > nul
""", "opening.wmp": b"new opening " * 300, "7za.exe": b"MZ"})
    played = play(game, folder)
    assert played.versions or laid(played) == {MOVIE: b"new opening " * 300}


LISTER = ("import pathlib, sys\n"
          "folder = pathlib.Path(sys.argv[1].replace(chr(92), '/'))\n"
          "names = sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*')\n"
          "               if p.is_file()) if folder.is_dir() else ['<no folder>']\n"
          "pathlib.Path(sys.argv[2].replace(chr(92), '/')).write_text(','.join(names))\n")


@pytest.mark.parametrize("how", ["named", "run in"])
def test_a_work_folder_in_temp_goes_over_whole_past_the_cap(tmp_path, monkeypatch, how):
    monkeypatch.setattr(sandbox, "BIG", 1000)
    game = xiii(tmp_path / "steam", voices="u")
    run = (r'"%~dp0lister.exe" "%TEMP%\FF13Tex" "%~dp0seen.txt"' if how == "named" else
           'cd /d "%TEMP%\\FF13Tex"\n"%~dp0lister.exe" . "%~dp0seen.txt"')
    folder = drop(tmp_path, "Tex Pack", {"install.bat": rf"""@echo off
xcopy /s /y /i "%~dp0textures" "%TEMP%\FF13Tex" > nul
{run}
copy /y "%~dp0seen.txt" "{GAME}\white_data\movie\us\seen.txt" > nul
""", **{f"textures/tex{n}.dds": b"t" * 400 for n in range(4)}})
    stub(folder, "lister.exe", LISTER)
    assert laid(play(game, folder)) == {
        "white_data/movie/us/seen.txt": b"tex0.dds,tex1.dds,tex2.dds,tex3.dds"}


def test_an_unpack_in_temp_handed_to_a_tool_goes_over(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Unpack Temp", {"install.bat": rf"""@echo off
copy /y "{GAME}\white_data\sys\filelist_scru.win32.bin" "%TEMP%\" > nul
copy /y "{GAME}\white_data\sys\white_scru.win32.bin" "%TEMP%\" > nul
"%~dp0ff13tool.exe" -x "%TEMP%\filelist_scru.win32.bin" "%TEMP%\white_scru.win32.bin" > nul
"%~dp0lister.exe" "%TEMP%\white_scru" "%~dp0seen.txt"
copy /y "%~dp0seen.txt" "{GAME}\white_data\movie\us\seen.txt" > nul
""", "ff13tool.exe": b"MZ"})
    stub(folder, "lister.exe", LISTER)
    assert laid(play(game, folder))["white_data/movie/us/seen.txt"].count(b".win32.") == 3


def test_a_record_copied_to_a_place_of_its_own_is_the_mod(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Swap", {"install.bat": rf"""@echo off
cd /d "{GAME}\white_data\sys"
"%~dp0ff13tool.exe" -x filelist_scru.win32.bin white_scru.win32.bin > nul
md "{GAME}\white_data\prog\win\bin\mod_data\chr\pc\c004\bin"
copy /y "white_scru\chr\pc\c001\bin\c001.win32.trb" "{GAME}\white_data\prog\win\bin\mod_data\chr\pc\c004\bin\c004.win32.trb" > nul
""", "ff13tool.exe": b"MZ"})
    # and not the unpack it took it from, left where it was made
    assert laid(play(game, folder)) == {
        "white_data/prog/win/bin/mod_data/chr/pc/c004/bin/c004.win32.trb": b"lightning " * 100}


def test_a_proxy_keeps_the_dll_it_chain_loads(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    (game / "white_data/prog/win/bin/d3d9.dll").write_bytes(b"MZ reshade " * 50)
    folder = drop(tmp_path, "Proxy", {"install.bat": r"""@echo off
set "bin=C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII\white_data\prog\win\bin"
ren "%bin%\d3d9.dll" d3d9_chain.dll
copy /y "%~dp0d3d9.dll" "%bin%\d3d9.dll" > nul
""", "d3d9.dll": b"MZ proxy " * 50})
    got = laid(play(game, folder))
    assert got["white_data/prog/win/bin/d3d9_chain.dll"] == b"MZ reshade " * 50
    assert got["white_data/prog/win/bin/d3d9.dll"] == b"MZ proxy " * 50


def test_an_uninstaller_beside_the_tool_it_runs_is_said_as_one(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Movie Pack", {
        MOVIE: b"new opening " * 300, "ff13tool.exe": b"MZ",
        "Uninstall.bat": '@echo off\r\n"%~dp0ff13tool.exe" -x a b\r\n'})
    done = bridge.install_mod("ff13", str(folder))
    assert "Uninstall.bat, for taking it back off, which Remove does here" in done["message"]


def test_what_one_voice_sets_play_left_on_the_stage_isnt_the_nexts(tmp_path):
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "Voices", {"install.bat": rf"""@echo off
choice /c EJ /m "Install for (E)nglish or (J)apanese voices?"
if errorlevel 2 (set v=c) else set v=u
"%~dp0tool.exe" "%TEMP%\%v%.txt" "{GAME}\white_data\movie\us\seen_%v%.txt"
"""})
    # writes its own file into TEMP, and says what else was in there
    stub(folder, "tool.exe", "import os, pathlib, sys\n"
         "pathlib.Path(sys.argv[1]).write_text('mine')\n"
         "temp = pathlib.Path(os.environ['TEMP'])\n"
         "pathlib.Path(sys.argv[2]).write_text(','.join(sorted(p.name for p in temp.iterdir())))\n")
    got = laid(play(game, folder))
    assert got["white_data/movie/us/seen_u.txt"] == b"u.txt"
    assert got["white_data/movie/us/seen_c.txt"] == b"c.txt"


RESTORER = INJECTOR.replace("echo Done!", r"""copy /y "%~dp0extras\op_full.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
echo Done!""")


@pytest.mark.parametrize("bat", ["Restore Cut Content.bat", "Cinematic Preset.bat",
                                 "Disable HUD.bat"])
def test_an_injector_named_for_undoing_something_does_all_it_does(tmp_path, monkeypatch, bat):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Cut Content", {
        bat: RESTORER.replace("FINAL FANTASY XIII", "FFXIII"), "ff13tool.exe": b"MZ",
        f"files/{TRB}": b"restored trb " * 100, "extras/op_full.wmp": b"full opening " * 300,
        "Visit Nexus Page.bat": 'start "" https://www.nexusmods.com/finalfantasy13\r\n'})
    done = bridge.install_mod("ff13", str(folder))
    assert done["ok"], done
    entry = game_files.entries(game / "white_data/sys/filelist_scru.win32.bin")[TRB]
    from patcher.mods.hd_zones import read_record
    assert read_record(game / "white_data/sys/white_scru.win32.bin", entry) == \
        b"restored trb " * 100
    assert (game / MOVIE).read_bytes() == b"full opening " * 300
    assert f"Played its installer, {bat}" in done["message"]


@pytest.mark.parametrize("bat", ["Back to Vanilla.bat", "Mod Off.bat", "Vanilla.bat"])
def test_a_bat_that_puts_originals_back_over_what_lands_is_passed_over(
        tmp_path, monkeypatch, bat):
    game, bridge = drawer(tmp_path, monkeypatch)
    (game / MOVIE).write_bytes(b"someone else's opening " * 200)
    folder = drop(tmp_path, "Opening B", {
        MOVIE: b"opening B " * 300, "originals/op.win32.wmp": b"vanilla opening " * 300,
        bat: r"""@echo off
set "game=C:\Program Files (x86)\Steam\steamapps\common\FFXIII"
copy /y "%~dp0originals\op.win32.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
"""})
    done = bridge.install_mod("ff13", str(folder))
    assert (game / MOVIE).read_bytes() == b"opening B " * 300
    assert f"{bat}, which only puts the game's own files back" in done["message"]


@pytest.mark.parametrize("ask", [
    "echo Type Y to uninstall the mod, or N to install it.\nset /p r=\n",
    "set /p r=Restore the original opening instead of installing? (y/n): \n"])
def test_an_answer_that_only_puts_originals_back_isnt_taken(tmp_path, ask):
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(b"someone else's opening " * 200)
    folder = drop(tmp_path, "HD Opening", {"install.bat": "@echo off\n" + ask + rf"""
if /i "%r%"=="y" goto restore
if /i "%r%"=="n" goto install
goto :eof
:install
copy /y "%~dp0hd\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:restore
copy /y "%~dp0original\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "hd/op.win32.wmp": b"hd opening " * 300, "original/op.win32.wmp": b"vanilla opening " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"hd opening " * 300}


@pytest.mark.parametrize("cd,run", [
    (r'cd /d "%TEMP%"', r'"%~dp0lister.exe" white_scru "%~dp0seen.txt"'),
    (r'cd /d "%~dp0"', r'"%~dp0lister.exe" white_scru "%~dp0seen.txt"'),
    (r'cd /d "%~dp0"', r'"%~dp0lister.exe" "%~dp0white_scru" "%~dp0seen.txt"')])
def test_an_unpack_handed_by_name_beside_where_the_tool_runs_goes_over(tmp_path, cd, run):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Unpack Here", {"install.bat": rf"""@echo off
{cd}
copy /y "{GAME}\white_data\sys\filelist_scru.win32.bin" . > nul
copy /y "{GAME}\white_data\sys\white_scru.win32.bin" . > nul
"%~dp0ff13tool.exe" -x filelist_scru.win32.bin white_scru.win32.bin > nul
{run}
copy /y "%~dp0seen.txt" "{GAME}\white_data\movie\us\seen.txt" > nul
""", "ff13tool.exe": b"MZ"})
    stub(folder, "lister.exe", LISTER)
    assert laid(play(game, folder))["white_data/movie/us/seen.txt"].count(b".win32.") == 3


@pytest.mark.parametrize("where", ["%USERPROFILE%", "C:\\"])
def test_the_temp_cap_holds_wherever_the_tool_runs(tmp_path, monkeypatch, where):
    game = xiii(tmp_path / "steam", voices="u")
    monkeypatch.setattr(sandbox, "BIG", 1000)
    folder = drop(tmp_path, "Full Temp", {"install.bat": rf"""@echo off
for %%n in (1 2 3 4) do copy /y "%~dp0part.bin" "%TEMP%\part%%n.bin" > nul
cd /d "{where}"
"%~dp0look.exe" "{GAME}\white_data\movie\us\seen.txt"
""", "part.bin": b"p" * 400})
    stub(folder, "look.exe", "import os, pathlib, sys\n"
         "names = sorted(p.name for p in pathlib.Path(os.environ['TEMP']).iterdir())\n"
         "pathlib.Path(sys.argv[1]).write_text('seen:' + ','.join(names))\n")
    assert laid(play(game, folder)) == {"white_data/movie/us/seen.txt": b"seen:"}


def test_an_installer_program_beside_an_uninstall_bat_is_still_said(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Movie Pack", {
        MOVIE: b"new opening " * 300, "Setup.exe": make_fake_pe(),
        "Uninstall.bat": "@echo off\r\necho removing\r\n"})
    done = bridge.install_mod("ff13", str(folder))
    assert "Uninstall.bat, for taking it back off" in done["message"]
    assert "Setup.exe, which wasn't run" in done["message"]


@pytest.mark.parametrize("keep", ["Original Files", "Default", "Backup Files"])
@pytest.mark.parametrize("how", [
    "set /p r=Type Y to uninstall the mod, or N to install it: \n"
    'if /i "%r%"=="y" goto restore\nif /i "%r%"=="n" goto install\n',
    'choice /c YN /m "Restore the original opening instead of installing"\n'
    "if errorlevel 2 goto install\nif errorlevel 1 goto restore\n"], ids=["set /p", "choice"])
def test_an_uninstall_answer_is_told_by_what_it_says_whatever_it_runs(tmp_path, keep, how):
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(b"someone else's opening " * 200)
    folder = drop(tmp_path, "HD Opening", {"install.bat": "@echo off\n" + how + rf"""goto :eof
:install
copy /y "%~dp0hd\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:restore
copy /y "%~dp0{keep}\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
"%~dp0tools\clearcache.exe"
""", "hd/op.win32.wmp": b"hd opening " * 300, f"{keep}/op.win32.wmp": b"vanilla opening " * 300})
    stub(folder / "tools", "clearcache.exe", "print('cleared')\n")
    assert laid(play(game, folder)) == {MOVIE: b"hd opening " * 300}


def per_part(parts: list, checks: str) -> str:
    """a question a part, checked for the answers its author thought of, n first"""
    lines = ["@echo off"]
    for part in parts:
        copy = f'copy /y "%~dp0{part}.wmp" "{GAME}\\white_data\\movie\\us\\{part}.wmp" > nul'
        lines += [f"set /p {part}=Install the new {part}? (y/n): ",
                  f'if /i "%{part}%"=="n" goto skip_{part}',
                  f'if /i "%{part}%"=="y" {copy}' if "y" in checks else copy, f":skip_{part}"]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("checks", ["n then y", "n alone"])
def test_a_per_part_installer_gets_every_part(tmp_path, checks):
    game = xiii(tmp_path / "steam", voices="u")
    parts = ["opening", "title", "ending", "menu"]
    folder = drop(tmp_path, "Parts", {"install.bat": per_part(parts, checks),
                                      **{f"{p}.wmp": f"new {p} ".encode() * 50 for p in parts}})
    assert sorted(laid(play(game, folder))) == sorted(f"white_data/movie/us/{p}.wmp"
                                                      for p in parts)


@pytest.mark.parametrize("ask", [
    'set /p s=Do you want the opening only, without the HD menu textures? (y/n): \n'
    'if /i "%s%"=="n" goto textures\nif /i "%s%"=="y" goto :eof\ngoto :eof\n:textures\n',
    # only ever checks for the no, so yes is whatever else is typed
    "set /p s=Install the HD menu textures as well? (y/n): \n"
    'if /i "%s%"=="n" goto :eof\n'])
def test_the_answer_that_leaves_a_part_out_isnt_taken(tmp_path, ask):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Pack", {"install.bat": rf"""@echo off
copy /y "%~dp0op.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
{ask}md "{GAME}\white_data\prog\win\bin\hd"
copy /y "%~dp0menu.dds" "{GAME}\white_data\prog\win\bin\hd\menu.dds" > nul
""", "op.wmp": b"hd opening " * 300, "menu.dds": b"hd menu " * 300})
    assert sorted(laid(play(game, folder))) == [MOVIE, "white_data/prog/win/bin/hd/menu.dds"]


def test_an_answer_that_skips_a_program_of_theirs_isnt_taken_for_more(tmp_path):
    # what the patcher makes isn't seen ahead, so the ready-made copy isn't more
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Opening Patch", {"install.bat": rf"""@echo off
set /p how=Patch your own opening (y) or copy the ready-made one in (n)? 
if /i "%how%"=="y" goto patch
if /i "%how%"=="n" goto copy
goto :eof
:patch
"%~dp0patcher.exe" "{GAME}\white_data\movie\us\op.win32.wmp"
goto :eof
:copy
copy /y "%~dp0ready\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "ready/op.win32.wmp": b"ready-made " * 300})
    stub(folder, "patcher.exe", "import pathlib, sys\nmovie = pathlib.Path(sys.argv[1])\n"
         "movie.write_bytes(b'patched ' + movie.read_bytes()[:20])\n")
    assert laid(play(game, folder)) == {MOVIE: b"patched opening movie openin"}


SWAP = rf"""@echo off
set "game={GAME}"
cd /d "%~dp0"
copy /y "%game%\white_data\sys\filelist_scru.win32.bin" . > nul
copy /y "%game%\white_data\sys\white_scru.win32.bin" . > nul
ff13tool.exe -x filelist_scru.win32.bin white_scru.win32.bin > nul
copy /y white_scru\chr\pc\c001\bin\c001.win32.trb white_scru\chr\pc\c004\bin\c004.win32.trb > nul
ff13tool.exe -c filelist_scru.win32.bin white_scru > nul
copy /y filelist_scru.win32.bin "%game%\white_data\sys\" > nul
copy /y white_scru.win32.bin "%game%\white_data\sys\" > nul
"""
SWAP_INJECT = SWAP.split("ff13tool.exe -c")[0] + r"""md swap\chr\pc\c004\bin
copy /y white_scru\chr\pc\c001\bin\c001.win32.trb swap\chr\pc\c004\bin\c004.win32.trb > nul
cd swap
..\ff13tool.exe -i ..\filelist_scru.win32.bin "%game%\white_data\sys\white_scru.win32.bin" chr\pc\c004\bin\c004.win32.trb > nul
cd ..
copy /y filelist_scru.win32.bin "%game%\white_data\sys\" > nul
"""
SWAP_LOOSE = SWAP.split("copy /y white_scru")[0] + r"""set "to=%game%\white_data\prog\win\bin"
md "%to%\mod_data\chr\pc\c004\bin"
copy /y white_scru\chr\pc\c001\bin\c001.win32.trb "%to%\mod_data\chr\pc\c004\bin\c004.win32.trb" > nul
"""
SWAPS = {"repack": (SWAP, f"white_scru/{TRB}"), "inject": (SWAP_INJECT, f"white_scru/{TRB}"),
         "mod_data": (SWAP_LOOSE, f"white_data/prog/win/bin/mod_data/{TRB}")}


@pytest.mark.parametrize("how", list(SWAPS))
def test_one_of_the_games_models_swapped_for_another_is_the_mod(tmp_path, how):
    # Lightning in Vanille's place, out of the game's own records
    game = xiii(tmp_path / "steam", voices="u")
    bat, lands = SWAPS[how]
    folder = drop(tmp_path, "Lightning As Vanille", {"install.bat": bat, "ff13tool.exe": b"MZ"})
    assert laid(play(game, folder)) == {lands: b"lightning " * 100}


def test_a_swap_beside_a_custom_model_is_a_version_either_way(tmp_path, monkeypatch):
    custom = rf"""@echo off
set "game={GAME}"
cd /d "%~dp0custom"
copy /y "%game%\white_data\sys\filelist_scru.win32.bin" . > nul
..\ff13tool.exe -i filelist_scru.win32.bin "%game%\white_data\sys\white_scru.win32.bin" chr\pc\c004\bin\c004.win32.trb > nul
copy /y filelist_scru.win32.bin "%game%\white_data\sys\" > nul
"""
    menu = ("@echo off\necho 1. Install the custom model\n"
            "echo 2. Install Lightning's model in her place\nset /p pick=Choose: \n"
            'if "%pick%"=="1" goto custom\nif "%pick%"=="2" goto swap\ngoto :eof\n:custom\n'
            + custom[10:] + "goto :eof\n:swap\n" + SWAP[10:])
    files = {"ff13tool.exe": b"MZ", f"custom/{TRB}": b"custom trb " * 100}
    played = play(xiii(tmp_path / "xiii", voices="u"),
                  drop(tmp_path, "Menu", {"install.bat": menu, **files}))
    assert played.versions == ["The custom model", "Lightning's model in her place"]
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Vanille Models", {
        "Custom Model.bat": custom.replace("FINAL FANTASY XIII", "FFXIII"),
        "Lightning Model.bat": SWAP.replace("FINAL FANTASY XIII", "FFXIII"), **files})
    assert bridge.install_mod("ff13", str(folder))["kind"] == "versions"
    assert bridge.install_mod("ff13", str(folder), version="Lightning Model")["ok"]
    from patcher.mods.hd_zones import read_record
    entry = game_files.entries(game / "white_data/sys/filelist_scru.win32.bin")[TRB]
    assert read_record(game / "white_data/sys/white_scru.win32.bin", entry) == b"lightning " * 100


def test_the_intro_swapped_for_a_short_movie_of_the_games_goes_in(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    (game / "white_data/movie/us/logo.win32.wmp").write_bytes(b"short logo " * 30)
    folder = drop(tmp_path, "Skip Intro", {"Skip Intro.bat": rf"""@echo off
set "movies={GAME}\white_data\movie\us"
copy /y "%movies%\logo.win32.wmp" "%movies%\op.win32.wmp" > nul
"""})
    assert laid(play(game, folder)) == {MOVIE: b"short logo " * 30}


@pytest.mark.parametrize("stash", ["Vanilla", "Original", "Backup"])
def test_a_payload_in_a_folder_named_for_the_game_it_suits_goes_in(tmp_path, stash):
    # the version for an unmodded game, or the original colours
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Opening", {"install.bat": rf"""@echo off
copy /y "%~dp0{stash}\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", f"{stash}/op.win32.wmp": b"hd opening " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"hd opening " * 300}


def test_a_drop_zipped_in_a_folder_named_vanilla_goes_in(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Sharper (Vanilla)", {
        "Vanilla/Install.bat": INJECTOR.replace("FINAL FANTASY XIII", "FFXIII"),
        "Vanilla/ff13tool.exe": b"MZ", f"Vanilla/files/{TRB}": b"sharper " * 100})
    done = bridge.install_mod("ff13", str(folder))
    from patcher.mods.hd_zones import read_record
    entry = game_files.entries(game / "white_data/sys/filelist_scru.win32.bin")[TRB]
    assert read_record(game / "white_data/sys/white_scru.win32.bin", entry) == b"sharper " * 100
    assert "only puts the game's own files back" not in done["message"]


def test_an_import_of_a_whole_unpack_leaves_an_optional_extra_out(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Vanille Tweak", {"Install.bat": r"""@echo off
set "game=C:\Program Files (x86)\Steam\steamapps\common\FFXIII"
cd /d "%~dp0"
copy /y "%game%\white_data\sys\filelist_scru.win32.bin" . > nul
copy /y "%game%\white_data\sys\white_scru.win32.bin" . > nul
ff13tool.exe -x filelist_scru.win32.bin white_scru.win32.bin > nul
copy /y "%~dp0mod\c004.win32.trb" white_scru\chr\pc\c004\bin\c004.win32.trb > nul
ff13tool.exe -i -all filelist_scru.win32.bin "%game%\white_data\sys\white_scru.win32.bin" white_scru > nul
copy /y filelist_scru.win32.bin "%game%\white_data\sys\" > nul
""", "ff13tool.exe": b"MZ", "mod/c004.win32.trb": b"tweaked " * 100,
        # for copying in by hand, the readme says
        f"Optional/{LIGHT}": b"optional lightning " * 50})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    from patcher.mods.hd_zones import read_record
    entries = game_files.entries(game / "white_data/sys/filelist_scru.win32.bin")
    archive = game / "white_data/sys/white_scru.win32.bin"
    assert read_record(archive, entries[TRB]) == b"tweaked " * 100
    assert read_record(archive, entries[LIGHT]) == b"lightning " * 100


OWN_TOOL = r"""@echo off
set "game=C:\Program Files (x86)\Steam\steamapps\common\FFXIII"
if not exist "%game%\FFXiiiLauncher.exe" (echo Game not found & pause & exit /b 1)
"%~dp0bin\patcher" "%game%\white_data\movie\us\op.win32.wmp"
"""


@pytest.mark.parametrize("beside", [
    ("Restore Backup.bat", r"""@echo off
copy /y "%~dp0backup\op.win32.wmp" "C:\Program Files (x86)\Steam\steamapps\common\FFXIII\white_data\movie\us\op.win32.wmp" > nul
"""),
    ("Launch Game.bat", '@echo off\r\nstart "" "%~dp0FFXiiiLauncher.exe"\r\n'),
    ("Play through Steam.bat", '@echo off\r\nstart "" "C:\\Program Files (x86)\\Steam\\steam.exe"'
                               ' -applaunch 292120\r\n'),
    ("Open Readme.bat", '@echo off\r\nnotepad.exe "%~dp0readme.txt"\r\n')], ids=lambda b: b[0])
def test_a_bat_that_only_runs_its_own_program_is_the_installer(tmp_path, monkeypatch, beside):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Opening Patch", {
        "FFXIII Uncensor.bat": OWN_TOOL, beside[0]: beside[1], "readme.txt": "read me",
        "backup/op.win32.wmp": b"opening movie " * 400})
    stub(folder / "bin", "patcher.exe", "import pathlib, sys\nmovie = pathlib.Path(sys.argv[1])\n"
         "movie.write_bytes(b'patched ' + movie.read_bytes()[:20])\n")
    done = bridge.install_mod("ff13", str(folder))
    assert done.get("kind") != "versions", done
    assert (game / MOVIE).read_bytes() == b"patched opening movie openin"


@pytest.mark.parametrize("helper", ["Play through Steam.bat", "Open Readme.bat"])
def test_an_injector_named_for_undoing_something_beats_a_helper(tmp_path, monkeypatch, helper):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Cut Content", {
        "Restore Cut Content.bat": RESTORER.replace("FINAL FANTASY XIII", "FFXIII"),
        helper: '@echo off\r\nnotepad.exe "%~dp0readme.txt"\r\n' if "Readme" in helper else
        '@echo off\r\nstart "" "C:\\Program Files (x86)\\Steam\\steam.exe" -applaunch 292120\r\n',
        "ff13tool.exe": b"MZ", "readme.txt": "read me", f"files/{TRB}": b"restored trb " * 100,
        "extras/op_full.wmp": b"full opening " * 300})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    assert (game / MOVIE).read_bytes() == b"full opening " * 300


ON_AND_OFF = r"""@echo off
copy /y "%~dp0{}" "C:\Program Files (x86)\Steam\steamapps\common\FFXIII\white_data\movie\us\op.win32.wmp" > nul
"""


@pytest.mark.parametrize("original", ["original", "Default", "Intro"])
def test_an_on_and_off_pair_is_asked_about_and_the_half_picked_goes_in(
        tmp_path, monkeypatch, original):
    # which half is the mod is only in the drop's name: No Intro here
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "No Intro", {
        "Disable Intro.bat": ON_AND_OFF.format("blank.wmp"),
        "Enable Intro.bat": ON_AND_OFF.format(original + r"\op.win32.wmp"),
        "blank.wmp": b"blank " * 300, f"{original}/op.win32.wmp": (game / MOVIE).read_bytes()})
    assert sorted(bridge.install_mod("ff13", str(folder))["versions"]) == [
        "Disable Intro", "Enable Intro"]
    assert bridge.install_mod("ff13", str(folder), version="Disable Intro")["ok"]
    assert (game / MOVIE).read_bytes() == b"blank " * 300


def test_a_rename_that_turns_the_intro_off_isnt_called_an_uninstall(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "No Intro", {"Disable Intro.bat": rf"""@echo off
cd /d "{GAME}\white_data\movie\us"
ren op.win32.wmp op.win32.wmp.off
"""})
    assert sandbox.UNDID not in play(game, folder).problem


@pytest.mark.parametrize("how", ["handed", "runs in"])
def test_a_tool_pointed_at_one_folder_of_an_unpack_gets_only_that(tmp_path, monkeypatch, how):
    game = xiii(tmp_path / "steam", voices="u")
    sys_dir = game / "white_data" / "sys"
    pack_plain(sys_dir / "white_scru.win32.bin", sys_dir / "filelist_scru.win32.bin",
               [(f"chr/pc/c{n:03}/bin/c{n:03}.win32.trb", bytes([n]) * 4096)
                for n in range(1, 41)])
    put = []
    lay = sandbox.World._lay
    monkeypatch.setattr(sandbox.World, "_lay", lambda w, p: put.append(p) or lay(w, p))
    run = (r'"%~dp0lister.exe" white_scru\chr\pc\c004 "%~dp0seen.txt"' if how == "handed" else
           'cd white_scru\\chr\\pc\\c004\n"%~dp0lister.exe" . "%~dp0seen.txt"\ncd /d "%~dp0"')
    folder = drop(tmp_path, "One Character", {"install.bat": rf"""@echo off
cd /d "%~dp0"
copy /y "{GAME}\white_data\sys\filelist_scru.win32.bin" . > nul
copy /y "{GAME}\white_data\sys\white_scru.win32.bin" . > nul
"%~dp0ff13tool.exe" -x filelist_scru.win32.bin white_scru.win32.bin > nul
{run}
copy /y "%~dp0seen.txt" "{GAME}\white_data\movie\us\seen.txt" > nul
""", "ff13tool.exe": b"MZ"})
    stub(folder, "lister.exe", LISTER)
    assert laid(play(game, folder)) == {"white_data/movie/us/seen.txt": b"bin/c004.win32.trb"}
    assert len([p for p in put if "\\white_scru\\" in p.lower()]) == 1


@pytest.mark.parametrize("run", [
    r'"%~dp0lister.exe" white_scru "%~dp0seen.txt"',
    'cd /d "%~dp0"\n' r'"%~dp0lister.exe" "{GAME}\white_data\sys\white_scru" "%~dp0seen.txt"'],
    ids=["beside it", "full path"])
def test_an_unpack_in_the_games_folder_handed_to_a_tool_goes_over(tmp_path, run):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "In Place", {"install.bat": rf"""@echo off
cd /d "{GAME}\white_data\sys"
"%~dp0ff13tool.exe" -x filelist_scru.win32.bin white_scru.win32.bin > nul
{run.replace("{GAME}", GAME)}
copy /y "%~dp0seen.txt" "{GAME}\white_data\movie\us\seen.txt" > nul
""", "ff13tool.exe": b"MZ"})
    stub(folder, "lister.exe", LISTER)
    assert laid(play(game, folder))["white_data/movie/us/seen.txt"].count(b".win32.") == 3


@pytest.mark.parametrize("tool", ["WhiteBinTools.exe", "4gb_patch.exe"])
def test_the_tool_an_uninstall_bat_runs_isnt_said_as_an_installer(tmp_path, monkeypatch, tool):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Movie Pack", {
        MOVIE: b"new opening " * 300, tool: make_fake_pe(),
        "Uninstall.bat": f'@echo off\r\n"%~dp0{tool}" -restore\r\n'})
    done = bridge.install_mod("ff13", str(folder))
    assert "Uninstall.bat, for taking it back off" in done["message"]
    assert "wasn't run" not in done["message"]


def test_a_quoted_path_after_a_bare_dash_is_the_stages(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Ini Tweak", {"install.bat": r"""@echo off
"%~dp0tool.exe" -"%~dp0settings.ini"
""", "settings.ini": "[Video]\r\nfullscreen=0\r\n"})
    stub(folder, "tool.exe", "import json, pathlib, sys\n"
         "pathlib.Path(sys.argv[0]).with_name('argv.json').write_text(json.dumps(sys.argv[1:]))\n")
    play(game, folder)
    import json
    seen = json.loads((folder / "argv.json").read_text(encoding="utf-8"))[0].replace("\\", "/")
    assert seen.startswith("-") and seen.endswith("/m/settings.ini") and "C:/Users" not in seen


OPENINGS = r"""@echo off
echo Opening Pack
echo 1. Install the HD opening
echo 2. Install the remastered opening
echo 3. Exit
set /p pick=Choose:
if "%pick%"=="1" copy /y "%~dp0hd\op.win32.wmp" "{game}\white_data\movie\us\op.win32.wmp" > nul
if "%pick%"=="2" copy /y "%~dp0remastered\op.win32.wmp" "{game}\white_data\movie\us\op.win32.wmp" > nul
"""


def openings(tmp_path, game=GAME):
    return drop(tmp_path, "Opening Pack", {"install.bat": OPENINGS.replace("{game}", game),
                                           "hd/op.win32.wmp": b"hd opening " * 300,
                                           "remastered/op.win32.wmp": b"remastered " * 300})


def test_a_menu_mod_in_already_still_offers_its_versions_and_takes_the_one_picked(tmp_path):
    # dropped again, or run by hand before: what's in is never compared
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(b"hd opening " * 300)
    folder = openings(tmp_path)
    assert play(game, folder).versions == ["The HD opening", "The remastered opening"]
    assert MOVIE not in laid(play(game, folder, version="The HD opening"))
    assert laid(play(game, folder, version="The remastered opening")) == {
        MOVIE: b"remastered " * 300}


def test_a_menu_mod_dropped_again_through_the_drawer_is_asked_about_again(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = openings(tmp_path, GAME.replace("FINAL FANTASY XIII", "FFXIII"))
    assert bridge.install_mod("ff13", str(folder), version="The HD opening")["ok"]
    assert bridge.install_mod("ff13", str(folder))["kind"] == "versions"
    assert (game / MOVIE).read_bytes() == b"hd opening " * 300


def test_a_menu_whose_first_choice_is_the_games_own_look_is_a_version(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    hud = "white_data/prog/win/bin/hud.dds"
    (game / hud).write_bytes(b"original hud " * 300)
    copy = 'copy /y "%~dp0{}\\hud.dds" "' + GAME + '\\white_data\\prog\\win\\bin\\hud.dds" > nul'
    folder = drop(tmp_path, "HUD Options", {"install.bat": "\n".join([
        "@echo off", "echo 1. Classic HUD", "echo 2. Minimal HUD", "set /p pick=Choose: ",
        f'if "%pick%"=="1" {copy.format("classic")}',
        f'if "%pick%"=="2" {copy.format("minimal")}']) + "\n",
        "classic/hud.dds": b"original hud " * 300, "minimal/hud.dds": b"minimal hud " * 300})
    assert play(game, folder).versions == ["Classic HUD", "Minimal HUD"]


@pytest.mark.parametrize("ask, lands", [
    ('choice /c YN /m "Install the 4K opening (N for the 1080p one)"\n'
     "if errorlevel 2 goto other\n", b"4k opening " * 300),
    ("set /p r=Type Y to install the mod, or N to uninstall it: \n"
     'if /i "%r%"=="n" goto other\n', b"4k opening " * 300)], ids=["4k or 1080p", "y or n"])
def test_an_answer_whose_play_is_in_already_is_still_the_one_given(tmp_path, ask, lands):
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(lands)
    rest = rf"""copy /y "%~dp04k\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:other
copy /y "%~dp0Original Files\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
"""
    folder = drop(tmp_path, "Opening 4K", {"install.bat": "@echo off\n" + ask + rest,
                                           "4k/op.win32.wmp": b"4k opening " * 300,
                                           "Original Files/op.win32.wmp": b"1080p " * 300})
    played = play(game, folder)
    assert played.asked[0][1] in ("Y", "y") and not laid(played)


INSTALL_AND_RESTORE = {
    "Install.bat": rf"""@echo off
copy /y "%~dp0files\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""",
    "Restore.bat": rf"""@echo off
copy /y "%~dp0backup\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
"""}


def test_a_restore_bat_beside_an_installer_thats_in_already_isnt_played(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Opening", {**INSTALL_AND_RESTORE,
                                           "files/op.win32.wmp": b"hd opening " * 300,
                                           "backup/op.win32.wmp": (game / MOVIE).read_bytes()})
    (game / MOVIE).write_bytes(b"hd opening " * 300)
    played = play(game, folder)
    assert played.bat == "Install.bat" and not laid(played)


def test_an_installer_for_another_game_in_the_pack_isnt_played_on_this_one(tmp_path):
    ask = ('@echo off\nset /p game=Drag your game folder here and press Enter: \n'
           'xcopy /s /y /i "%~dp0{0}\\{1}" "%game%\\{1}"\n')
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(b"xiii opening " * 300)
    folder = drop(tmp_path, "Trilogy Openings", {
        "Install XIII.bat": ask.format("XIII", "white_data"),
        "Install LR.bat": ask.format("LR", "weiss_data"),
        f"XIII/{MOVIE}": b"xiii opening " * 300,
        "LR/weiss_data/movie/us/op.win32.wmp": b"lr opening " * 300})
    played = play(game, folder)
    assert played.bat == "Install XIII.bat" and not laid(played)


def test_a_helpers_program_isnt_started_when_the_installer_gets_stuck(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "TOOL_LIMIT", 2)
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Opening", {
        "Install HD Opening.bat": f'@echo off\n"%~dp0builder.exe" "{GAME}\\{MOVIE}"\n',
        "Configure.bat": '@echo off\r\n"%~dp0ConfigTool.exe"\r\n'})
    stub(folder, "builder.exe", "import time\ntime.sleep(60)\n")
    stub(folder, "ConfigTool.exe", "import pathlib, sys\n"
         "pathlib.Path(sys.argv[0]).with_name('opened').write_text('x')\n")
    played = play(game, folder)
    assert played.bat == "Install HD Opening.bat" and not (folder / "opened").exists()


@pytest.mark.parametrize("mod, keep", [("HD", "Original"), ("Mod", "BackupFiles"),
                                       ("New Opening", "Original Files")])
def test_an_uninstaller_with_its_originals_laid_out_like_the_game_is_passed_over(
        tmp_path, mod, keep):
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(b"someone else's opening " * 200)
    folder = drop(tmp_path, "Opening B", {
        f"{mod}/{MOVIE}": b"opening B " * 300, f"{keep}/{MOVIE}": b"opening movie " * 400,
        "Restore Original Opening.bat": rf"""@echo off
copy /y "%~dp0{keep}\white_data\movie\us\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
"""})
    assert play(game, folder).problem == sandbox.UNDID


@pytest.mark.parametrize("mod", ["Mod", "Zack Model"])
def test_an_uninstaller_putting_a_record_back_is_passed_over(tmp_path, mod):
    game = xiii(tmp_path / "steam", voices="u")
    sys_dir = game / "white_data" / "sys"
    pack_plain(sys_dir / "white_scru.win32.bin", sys_dir / "filelist_scru.win32.bin",
               [(TRB, b"someone else's trb " * 60), (LIGHT, b"lightning " * 100)])
    folder = drop(tmp_path, "Vanille Model", {"Restore Original Model.bat": rf"""@echo off
set "game={GAME}"
cd /d "%~dp0Original"
copy /y "%game%\white_data\sys\filelist_scru.win32.bin" . > nul
..\ff13tool.exe -i filelist_scru.win32.bin "%game%\white_data\sys\white_scru.win32.bin" chr\pc\c004\bin\c004.win32.trb > nul
copy /y filelist_scru.win32.bin "%game%\white_data\sys\" > nul
""", "ff13tool.exe": b"MZ", f"{mod}/{TRB}": b"new vanille " * 100,
        f"Original/{TRB}": b"vanilla trb " * 100})
    assert play(game, folder).problem == sandbox.UNDID


@pytest.mark.parametrize("keep", ["originals", "backup", "Original Files"])
def test_an_uninstaller_beside_a_file_to_copy_in_by_hand_is_passed_over(tmp_path, keep):
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(b"someone else's opening " * 200)
    folder = drop(tmp_path, "Opening B", {
        "op.win32.wmp": b"opening B " * 300, f"{keep}/op.win32.wmp": b"opening movie " * 400,
        "Restore Original Opening.bat": rf"""@echo off
copy /y "%~dp0{keep}\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
"""})
    assert play(game, folder).problem == sandbox.UNDID


def test_an_on_and_off_pair_dropped_again_is_asked_about_again(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "No Intro", {
        "Disable Intro.bat": ON_AND_OFF.format("blank.wmp"),
        "Enable Intro.bat": ON_AND_OFF.format(r"original\op.win32.wmp"),
        "blank.wmp": b"blank " * 300, "original/op.win32.wmp": (game / MOVIE).read_bytes()})
    assert bridge.install_mod("ff13", str(folder), version="Disable Intro")["ok"]
    assert bridge.install_mod("ff13", str(folder))["kind"] == "versions"
    assert (game / MOVIE).read_bytes() == b"blank " * 300


@pytest.mark.parametrize("ask", [
    'choice /c YN /m "Do you want to go back to the original opening"\n'
    "if errorlevel 2 goto install\ngoto restore\n",
    'choice /c YN /m "Replace the HD opening with the original one"\n'
    "if errorlevel 2 goto install\ngoto restore\n",
    'choice /c YN /m "OK to restore the backed up opening"\n'
    "if errorlevel 2 goto install\ngoto restore\n",
    'choice /c YN /m "Do you want to uninstall the mod? (Y) Yes / (N) No"\n'
    "if errorlevel 2 goto install\ngoto restore\n",
    "set /p r=Do you want to go back to the original opening? (y/n): \n"
    'if /i "%r%"=="y" goto restore\nif /i "%r%"=="n" goto install\ngoto :eof\n',
    # only ever checks for the y, so no is whatever else is typed
    "set /p r=Restore the original opening instead of installing? (y/n): \n"
    'if /i "%r%"=="y" goto restore\n',
    "set /p r=Type Y to uninstall the mod, or anything else to install it: \n"
    'if /i "%r%"=="y" goto restore\n'],
    ids=["go back", "replace with", "ok to restore", "(Y) Yes", "set /p", "y alone", "y to undo"])
def test_a_question_about_going_back_is_answered_no(tmp_path, ask):
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(b"someone else's opening " * 200)
    folder = drop(tmp_path, "HD Opening", {"install.bat": "@echo off\n" + ask + rf""":install
copy /y "%~dp0HD\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:restore
copy /y "%~dp0Original Opening\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "HD/op.win32.wmp": b"hd opening " * 300,
        "Original Opening/op.win32.wmp": b"opening movie " * 400})
    assert laid(play(game, folder)) == {MOVIE: b"hd opening " * 300}


@pytest.mark.parametrize("one, two", [("Default Ending", "Extended Ending"),
                                      ("Stockings", "Tights"), ("Original Colors", "Recolor")])
def test_a_version_named_with_an_originals_word_is_offered_and_goes_in(tmp_path, one, two):
    game = xiii(tmp_path / "steam", voices="u")
    ending = "white_data/movie/us/ed.win32.wmp"
    (game / ending).write_bytes(b"vanilla ending " * 300)
    copy = 'copy /y "%~dp0{}\\white_data\\movie\\us\\ed.win32.wmp" "' + GAME + \
        '\\white_data\\movie\\us\\ed.win32.wmp" > nul'
    folder = drop(tmp_path, "Ending Pack", {
        "Pick Ending.bat": f"@echo off\necho 1. {one}\necho 2. {two}\nset /p pick=Choose: \n"
                           f'if "%pick%"=="1" {copy.format(one)}\n'
                           f'if "%pick%"=="2" {copy.format(two)}\n',
        f"{one}/{ending}": f"{one} ".encode() * 300, f"{two}/{ending}": f"{two} ".encode() * 300})
    assert play(game, folder).versions == [one, two]
    assert laid(play(game, folder, version=one)) == {ending: f"{one} ".encode() * 300}


@pytest.mark.parametrize("ask", [
    "set /p keep=Do you want to keep your current settings? (y/n): \n"
    'if /i "%keep%"=="y" goto done\n',
    'choice /c YN /m "Do you want to keep your current settings"\n'
    "if errorlevel 2 goto overwrite\ngoto done\n:overwrite\n"], ids=["set /p", "choice"])
def test_keeping_their_own_settings_keeps_them(tmp_path, ask):
    game = xiii(tmp_path / "steam", voices="u")
    (game / "white_data/prog/win/bin/FF13Fix.ini").write_text("[Options]\nFPS=60\n")
    folder = drop(tmp_path, "HD Opening", {"install.bat": rf"""@echo off
copy /y "%~dp0op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
{ask}copy /y "%~dp0FF13Fix.ini" "{GAME}\white_data\prog\win\bin\FF13Fix.ini" > nul
:done
""", "op.win32.wmp": b"hd opening " * 300, "FF13Fix.ini": "[Options]\nFPS=30\n"})
    assert sorted(laid(play(game, folder))) == [MOVIE]


def test_a_tool_run_in_an_unpack_beside_an_index_part_way_through_a_rebuild(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Checker", {"install.bat": rf"""@echo off
cd /d "%~dp0"
copy /y "{GAME}\white_data\sys\filelist_scru.win32.bin" . > nul
copy /y "{GAME}\white_data\sys\white_scru.win32.bin" . > nul
ff13tool.exe -x filelist_scru.win32.bin white_scru.win32.bin > nul
copy /y filelist_scru.win32.bin white_scru\ > nul
copy /y "%~dp0mod\c004.win32.trb" white_scru\chr\pc\c004\bin\c004.win32.trb > nul
cd white_scru
..\ff13tool.exe -i filelist_scru.win32.bin "{GAME}\white_data\sys\white_scru.win32.bin" chr\pc\c004\bin\c004.win32.trb > nul
"%~dp0lister.exe" . "%~dp0seen.txt"
copy /y filelist_scru.win32.bin "{GAME}\white_data\sys\" > nul
""", "ff13tool.exe": b"MZ", "mod/c004.win32.trb": b"modded " * 100})
    stub(folder, "lister.exe", LISTER)
    played = play(game, folder)
    assert laid(played)[f"white_scru/{TRB}"] == b"modded " * 100


@pytest.mark.parametrize("helper", [
    '@echo off\r\nstart "" "%~dp0..\\FFXiiiLauncher.exe"\r\n',
    '@echo off\r\ncd /d "%~dp0.."\r\nstart FFXiiiLauncher.exe\r\n',
    '@echo off\r\nexplorer "%~dp0Screenshots"\r\n',
    '@echo off\r\nstart "" "%~dp0Guide.docx"\r\n'], ids=["..", "cd ..", "explorer", "docx"])
def test_a_helper_that_opens_something_of_its_own_isnt_a_version(helper):
    files = {"Vanille Fix.bat": '@echo off\r\nset "game=C:\\Games\\FFXIII"\r\n'
                                '"%~dp0bin\\VanilleFix.exe" "%game%"\r\n', "Helper.bat": helper}
    found = sandbox.find([(m, 1) for m in files], lambda m: files[m].encode(), "white_data")
    assert sandbox.rivals(found) == ["Vanille Fix.bat"]


def test_a_tool_handed_a_model_inside_an_unpack_has_the_imgb_beside_it(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Model Tool", {"install.bat": rf"""@echo off
cd /d "%~dp0"
copy /y "{GAME}\white_data\sys\filelist_scru.win32.bin" . > nul
copy /y "{GAME}\white_data\sys\white_scru.win32.bin" . > nul
ff13tool.exe -x filelist_scru.win32.bin white_scru.win32.bin > nul
"%~dp0beside.exe" white_scru\chr\pc\c004\bin\c004.win32.trb "{GAME}\white_data\movie\us\seen.txt"
""", "ff13tool.exe": b"MZ"})
    stub(folder, "beside.exe", "import pathlib, sys\n"
         "here = pathlib.Path(sys.argv[1].replace(chr(92), '/')).parent\n"
         "names = sorted(p.name for p in here.iterdir() if p.is_file())\n"
         "pathlib.Path(sys.argv[2].replace(chr(92), '/')).write_text(','.join(names))\n")
    assert laid(play(game, folder))["white_data/movie/us/seen.txt"] == \
        b"c004.win32.imgb,c004.win32.trb"


ONE_COPY = r"""@echo off
copy /y "%~dp0{}\op.win32.wmp" "{}\white_data\movie\us\op.win32.wmp" > nul
"""


def test_installers_for_the_hd_project_and_for_the_unmodded_game_are_versions(
        tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    ffxiii = GAME.replace("FINAL FANTASY XIII", "FFXIII")
    folder = drop(tmp_path, "Sharp Opening", {
        "Vanilla.bat": ONE_COPY.format("Vanilla", ffxiii),
        "HD Project.bat": ONE_COPY.format("HD Project", ffxiii),
        "Vanilla/op.win32.wmp": b"sharp for vanilla " * 300,
        "HD Project/op.win32.wmp": b"sharp for hd " * 300})
    assert sorted(bridge.install_mod("ff13", str(folder))["versions"]) == ["HD Project", "Vanilla"]
    assert bridge.install_mod("ff13", str(folder), version="Vanilla")["ok"]
    assert (game / MOVIE).read_bytes() == b"sharp for vanilla " * 300


@pytest.mark.parametrize("hd", ["HD Project", "Original"])
def test_an_installer_that_picks_the_unmodded_games_version_itself(tmp_path, hd):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Sharp Opening", {"install.bat": rf"""@echo off
if exist "{GAME}\HDProject.ini" goto hd
copy /y "%~dp0Vanilla\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:hd
copy /y "%~dp0{hd}\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "Vanilla/op.win32.wmp": b"sharp for vanilla " * 300, f"{hd}/op.win32.wmp": b"hd " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"sharp for vanilla " * 300}


SETTINGS = '@echo off\r\n"%~dp0Config.exe"\r\n'
OPENED = "import pathlib, sys\npathlib.Path(sys.argv[0]).with_name('opened').write_text('x')\n"


@pytest.mark.parametrize("bat", [
    rf"""@echo off
copy /y "{GAME}\white_data\movie\us\op.win32.wmp" "%~dp0Backup\" > nul
copy /y "%~dp0op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""",
    rf"""@echo off
if exist "{GAME}\FFXiiiLauncher.exe" "%~dp0bin\patcher" "{GAME}\white_data\movie\us\op.win32.wmp"
""",
    rf"""@echo off
pushd "%~dp0bin" && patcher "{GAME}\white_data\movie\us\op.win32.wmp"
"""], ids=["backs up first", "if exist", "pushd"])
def test_the_installer_beside_a_settings_tool_is_played_and_the_tool_isnt(tmp_path, bat):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Opening", {"HD Opening.bat": bat, "Configure.bat": SETTINGS,
                                           "op.win32.wmp": b"patched opening movie openin",
                                           "Backup/.keep": b""})
    stub(folder, "Config.exe", OPENED)
    stub(folder / "bin", "patcher.exe", "import pathlib, sys\nmovie = pathlib.Path(sys.argv[-1])\n"
         "movie.write_bytes(b'patched ' + movie.read_bytes()[:20])\n")
    played = play(game, folder)
    assert played.bat == "HD Opening.bat" and not (folder / "opened").exists()
    assert laid(played) == {MOVIE: b"patched opening movie openin"}


def test_dropped_again_from_a_folder_named_for_restoring_the_mod_stays_in(
        tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    top, ffxiii = "Restore Cut Content", GAME.replace("FINAL FANTASY XIII", "FFXIII")
    folder = drop(tmp_path, top, {
        f"{top}/Install.bat": ONE_COPY.format("files", ffxiii),
        f"{top}/Restore.bat": ONE_COPY.format("backup", ffxiii),
        f"{top}/files/op.win32.wmp": b"uncut opening " * 300,
        f"{top}/backup/op.win32.wmp": (game / MOVIE).read_bytes()})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    again = bridge.install_mod("ff13", str(folder))
    assert (game / MOVIE).read_bytes() == b"uncut opening " * 300
    said = f"{again.get('error') or ''} {again.get('message') or ''}"
    assert "Restore.bat" not in said and sandbox.UNDID not in said


@pytest.mark.parametrize("ask", [
    "set /p r=Do you want to restore the cut content too? (y/n): \n"
    'if /i "%r%"=="y" goto extra\ngoto base\n',
    "set /p r=Remove the censorship as well? (y/n): \n"
    'if /i "%r%"=="y" goto extra\ngoto base\n',
    'choice /c YN /m "Would you like to remove the HUD"\nif errorlevel 2 goto base\ngoto extra\n',
    "set /p r=Install just the core files? (y/n): \n"
    'if /i "%r%"=="y" goto base\ngoto extra\n',
    "set /p r=Do you want the full install (Y) or only the opening (N)? \n"
    'if /i "%r%"=="n" goto base\ngoto extra\n',
    "set /p r=Also install the HD menu textures (Y) or skip them (N)? \n"
    'if /i "%r%"=="y" goto extra\ngoto base\n',
    "echo Prefer the vanilla look? Use the other download.\n"
    'choice /c YN /m "Add the HD menu textures?"\nif errorlevel 2 goto base\ngoto extra\n',
    "echo This will back up your original files and then install the mod.\n"
    'choice /c YN /m "Continue?"\nif errorlevel 2 goto :eof\ngoto extra\n'],
    ids=["restore too", "remove as well", "remove the HUD", "just the core", "full (Y) or only",
         "(Y) or skip", "echo then choice", "back up then install"])
def test_a_part_a_yes_or_no_adds_goes_in(tmp_path, ask):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Pack", {"install.bat": "@echo off\n" + ask + rf""":base
copy /y "%~dp0op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:extra
copy /y "%~dp0menu_hd.dds" "{GAME}\white_data\prog\win\bin\menu_hd.dds" > nul
goto base
""", "op.win32.wmp": b"hd opening " * 300, "menu_hd.dds": b"hd menu " * 300})
    assert sorted(laid(play(game, folder))) == [MOVIE, "white_data/prog/win/bin/menu_hd.dds"]


def test_an_on_and_off_pair_whose_off_half_has_a_menu_is_picked_in_two(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    target = GAME + "\\white_data\\movie\\us\\op.win32.wmp"
    folder = drop(tmp_path, "No Intro", {
        "Enable Intro.bat": f'@echo off\ncopy /y "%~dp0OriginalIntro\\op.win32.wmp" "{target}" > nul\n',
        "Disable Intro.bat": "\n".join([
            "@echo off", "echo 1. A black screen", "echo 2. The short logo", "set /p pick=Choose: ",
            f'if "%pick%"=="1" copy /y "%~dp0black.wmp" "{target}" > nul',
            f'if "%pick%"=="2" copy /y "%~dp0logo.wmp" "{target}" > nul']) + "\n",
        "OriginalIntro/op.win32.wmp": (game / MOVIE).read_bytes(),
        "black.wmp": b"black " * 300, "logo.wmp": b"logo " * 300})
    assert play(game, folder).versions == ["Enable Intro", "Disable Intro"]
    offered = play(game, folder, version="Disable Intro").versions
    assert offered == ["Disable Intro / A black screen", "Disable Intro / The short logo"]
    assert laid(play(game, folder, version=offered[1])) == {MOVIE: b"logo " * 300}


@pytest.mark.parametrize("how", ['del "{}\\white_data\\movie\\us\\op.win32.wmp"',
                                 'ren "{}\\white_data\\movie\\us\\op.win32.wmp" op.win32.wmp.off'],
                         ids=["del", "ren"])
def test_turning_the_intro_off_by_deleting_it_says_so(tmp_path, how):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "No Intro", {"Disable Intro.bat": "@echo off\n" + how.format(GAME)
                                         + "\necho The opening won't play now.\n"})
    assert "deletes the game's op.win32.wmp, which can't go in" in play(game, folder).problem


GUARDED = r"""@echo off
set "game={game}"
if exist "%game%\Backup\op.win32.wmp" (echo Already installed. Run Restore Backup.bat first. & pause & exit /b)
if not exist "%game%\Backup" mkdir "%game%\Backup"
copy /y "%game%\white_data\movie\us\op.win32.wmp" "%game%\Backup\op.win32.wmp" > nul
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
"""


def test_an_installer_that_wont_run_twice_isnt_swapped_for_the_restore_beside_it(
        tmp_path, monkeypatch):
    # run by hand once, then dropped on the drawer
    game, bridge = drawer(tmp_path, monkeypatch)
    ffxiii = GAME.replace("FINAL FANTASY XIII", "FFXIII")
    (game / "Backup").mkdir()
    (game / "Backup/op.win32.wmp").write_bytes((game / MOVIE).read_bytes())
    (game / MOVIE).write_bytes(b"hd opening " * 300)
    folder = drop(tmp_path, "HD Opening", {
        "Install.bat": GUARDED.format(game=ffxiii), "op.win32.wmp": b"hd opening " * 300,
        "Restore Backup.bat": f'@echo off\nmove /y "{ffxiii}\\Backup\\op.win32.wmp" '
                              f'"{ffxiii}\\white_data\\movie\\us\\op.win32.wmp" > nul\n'})
    done = bridge.install_mod("ff13", str(folder))
    assert (game / MOVIE).read_bytes() == b"hd opening " * 300
    assert "Run Restore Backup.bat first" in done["error"]


@pytest.mark.parametrize("ask", [
    "set /p r=HD Opening is already installed. Do you want to uninstall it? (y/n): \n"
    'if /i not "%r%"=="y" goto :eof\n',
    "set /p r=Type install or uninstall: \n"
    'if /i "%r%"=="install" goto :eof\nif /i not "%r%"=="uninstall" goto :eof\n',
    'choice /c YN /m "HD Opening is installed. Press Y to remove the mod or N to cancel"\n'
    "if errorlevel 2 goto :eof\n",
    "set /p r=HD Opening is installed. Type Y to un-install or N to cancel: \n"
    'if /i not "%r%"=="y" goto :eof\n',
    'choice /c RC /m "(R)estore original files or (C)ancel"\nif errorlevel 2 goto :eof\n'],
    ids=["uninstall it?", "typed words", "remove the mod or cancel", "un-install or cancel",
         "(R)estore original files"])
def test_dropped_again_an_installers_own_uninstall_isnt_taken(tmp_path, monkeypatch, ask):
    game, bridge = drawer(tmp_path, monkeypatch)
    ffxiii = GAME.replace("FINAL FANTASY XIII", "FFXIII")
    folder = drop(tmp_path, "HD Opening", {"install.bat": rf"""@echo off
set "game={ffxiii}"
if exist "%game%\HDOpening_installed.txt" goto installed
copy /y "%~dp0Mod\op.win32.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
echo installed> "%game%\HDOpening_installed.txt"
goto :eof
:installed
{ask}copy /y "%~dp0Vanilla Files\op.win32.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
del "%game%\HDOpening_installed.txt"
""", "Mod/op.win32.wmp": b"hd opening " * 300,
        "Vanilla Files/op.win32.wmp": (game / MOVIE).read_bytes()})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    bridge.install_mod("ff13", str(folder))
    assert (game / MOVIE).read_bytes() == b"hd opening " * 300


@pytest.mark.parametrize("first", [
    'choice /c YN /m "Do you want to keep the original intro"\nif errorlevel 2 goto intro\n'
    "goto tex\n",
    "set /p a=Do you want to keep the original intro? (y/n): \n"
    'if /i "%a%"=="n" goto intro\ngoto tex\n',
    "echo This mod only works with the Steam version.\ngoto intro\n"], ids=[
    "after a choice", "after a set /p", "after a line about it"])
def test_a_question_is_read_on_its_own_not_off_the_one_before(tmp_path, first):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Pack", {"install.bat": "@echo off\n" + first + rf""":intro
copy /y "%~dp0op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
:tex
choice /c YN /m "Install the HD menu textures"
if errorlevel 2 goto :eof
copy /y "%~dp0menu_hd.dds" "{GAME}\white_data\prog\win\bin\menu_hd.dds" > nul
""", "op.win32.wmp": b"new intro " * 300, "menu_hd.dds": b"hd menu " * 300})
    assert sorted(laid(play(game, folder))) == [MOVIE, "white_data/prog/win/bin/menu_hd.dds"]


@pytest.mark.parametrize("ask", [
    "set /p r=Press Y to restore the original files or N to keep the mod: \n"
    'if /i "%r%"=="y" goto restore\n',
    "set /p r=Type Y to undo the changes, N to keep them: \n"
    'if /i "%r%"=="y" goto restore\n'], ids=["restore the original files", "undo the changes"])
def test_an_answer_said_to_go_back_isnt_the_one_taken(tmp_path, ask):
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(b"someone else's opening " * 200)
    rest = rf"""copy /y "%~dp0HD\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto :eof
:restore
copy /y "%~dp0Game Files\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
"""
    folder = drop(tmp_path, "HD Opening", {"install.bat": "@echo off\n" + ask + rest,
                                           "HD/op.win32.wmp": b"hd opening " * 300,
                                           "Game Files/op.win32.wmp": b"vanilla " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"hd opening " * 300}


def test_dropped_again_with_cmd_stopping_after_its_work_the_version_in_stays(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    (game / MOVIE).write_bytes(b"4k opening " * 300)
    folder = drop(tmp_path, "Opening 4K", {"install.bat": rf"""@echo off
choice /c YN /m "Install the 4K opening (N for the 1080p one)"
if errorlevel 2 goto small
copy /y "%~dp04k\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
goto done
:small
copy /y "%~dp01080p\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
:done
set x=
if %x%==y echo never
""", "4k/op.win32.wmp": b"4k opening " * 300, "1080p/op.win32.wmp": b"1080p opening " * 200})
    assert not laid(play(game, folder))


def test_replacing_the_original_with_the_mods_is_a_yes(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Pack", {"install.bat": rf"""@echo off
copy /y "%~dp0menu_hd.dds" "{GAME}\white_data\prog\win\bin\menu_hd.dds" > nul
choice /c YN /m "Do you want to replace the original opening with the HD one"
if errorlevel 2 goto :eof
copy /y "%~dp0op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "op.win32.wmp": b"hd opening " * 300, "menu_hd.dds": b"hd menu " * 300})
    assert sorted(laid(play(game, folder))) == [MOVIE, "white_data/prog/win/bin/menu_hd.dds"]


@pytest.mark.parametrize("pick", ["The HD opening", "Vanilla"])
def test_dropped_again_and_picked_again_it_says_its_in(tmp_path, monkeypatch, pick):
    game, bridge = drawer(tmp_path, monkeypatch)
    ffxiii = GAME.replace("FINAL FANTASY XIII", "FFXIII")
    folder = openings(tmp_path, ffxiii) if pick.startswith("The") else drop(
        tmp_path, "Sharp Opening", {"Vanilla.bat": ONE_COPY.format("Vanilla", ffxiii),
                                    "HD Project.bat": ONE_COPY.format("HD Project", ffxiii),
                                    "Vanilla/op.win32.wmp": b"sharp for vanilla " * 300,
                                    "HD Project/op.win32.wmp": b"sharp for hd " * 300})
    assert bridge.install_mod("ff13", str(folder), version=pick)["ok"]
    assert bridge.install_mod("ff13", str(folder))["kind"] == "versions"
    again = bridge.install_mod("ff13", str(folder), version=pick)
    assert again["ok"] and again["summary"].endswith("is in already.")


@pytest.mark.parametrize("readme", [False, True], ids=["alone", "readme beside"])
def test_dropped_again_from_a_folder_named_like_originals(tmp_path, monkeypatch, readme):
    game, bridge = drawer(tmp_path, monkeypatch)
    top, ffxiii = "Original Opening Restored", GAME.replace("FINAL FANTASY XIII", "FFXIII")
    folder = drop(tmp_path, top, {
        f"{top}/Install.bat": ONE_COPY.format("files", ffxiii),
        f"{top}/files/op.win32.wmp": b"restored opening " * 300,
        **({"README.txt": "Unzip and run Install.bat"} if readme else {})})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    again = bridge.install_mod("ff13", str(folder))
    assert again["ok"] and sandbox.UNDID not in again["message"]


def test_an_installer_beside_an_on_and_off_pair_is_the_one_played(tmp_path):
    files = {"Install.bat": ONE_COPY.format("files", GAME),
             "Enable Intro.bat": ONE_COPY.format("original", GAME),
             "Disable Intro.bat": ONE_COPY.format("blank", GAME)}
    found = sandbox.find([(m, 1) for m in files], lambda m: files[m].encode(), "white_data")
    assert sandbox.rivals(found) == ["Install.bat"]


def test_a_menu_choice_that_only_deletes_something_isnt_a_version(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    (game / "white_data/prog/win/bin/shader.cache").write_bytes(b"cache " * 30)
    folder = drop(tmp_path, "HD Opening", {"install.bat": rf"""@echo off
echo 1. Install the HD opening
echo 2. Install the remastered opening
echo 3. Clear the shader cache
set /p pick=Choose: 
if "%pick%"=="1" copy /y "%~dp0hd\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
if "%pick%"=="2" copy /y "%~dp0rm\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
if "%pick%"=="3" del "{GAME}\white_data\prog\win\bin\shader.cache"
""", "hd/op.win32.wmp": b"hd " * 300, "rm/op.win32.wmp": b"rm " * 300})
    assert play(game, folder).versions == ["The HD opening", "The remastered opening"]


def test_a_yes_or_no_after_the_voices_question_isnt_another_voices_question(tmp_path):
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "HD Opening", {"install.bat": rf"""@echo off
choice /c EJ /m "Install for (E)nglish or (J)apanese voices?"
if errorlevel 2 (set v=c) else set v=u
choice /c YN /m "Also put in the HD menu textures"
if errorlevel 2 goto :eof
copy /y "%~dp0menu_hd.dds" "{GAME}\white_data\prog\win\bin\menu_hd.dds" > nul
""", "menu_hd.dds": b"hd menu " * 300})
    assert laid(play(game, folder)) == {"white_data/prog/win/bin/menu_hd.dds": b"hd menu " * 300}


GUARDED_BY_A_MARKER = r"""@echo off
set "game={game}"
if exist "%game%\HDOpening_installed.txt" (echo Already installed! & pause & goto :eof)
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
echo installed> "%game%\HDOpening_installed.txt"
"""


def test_dropped_again_an_installer_that_wont_run_twice_says_its_in(tmp_path, monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "HD Opening", {
        "Install.bat": GUARDED_BY_A_MARKER.format(game=GAME.replace("FINAL FANTASY XIII",
                                                                    "FFXIII")),
        "op.win32.wmp": b"hd opening " * 300})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    again = bridge.install_mod("ff13", str(folder))
    assert again["ok"] and again["summary"] == "HD Opening is in already."
    assert (game / MOVIE).read_bytes() == b"hd opening " * 300


@pytest.mark.parametrize("note", [
    "echo To uninstall, run Uninstall.bat.\n",
    "echo Make sure to uninstall any older version first.\n",
    "echo You can restore the original files at any time with Restore.bat.\n"],
    ids=["to uninstall", "uninstall older first", "restore any time"])
def test_a_note_on_undoing_it_later_doesnt_stop_the_install(tmp_path, note):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Opening", {"Install.bat": "@echo off\n" + note + rf"""
choice /c YN /m "Continue"
if errorlevel 2 goto :eof
copy /y "%~dp0Data\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "Uninstall.bat": rf"""@echo off
copy /y "%~dp0Original\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "Data/op.win32.wmp": b"hd opening " * 300, "Original/op.win32.wmp": b"opening movie " * 400})
    assert laid(play(game, folder)) == {MOVIE: b"hd opening " * 300}


@pytest.mark.parametrize("ask", [
    "set /p r=Restore the original Japanese theme song? (y/n): \n"
    'if /i not "%r%"=="y" goto :eof\n',
    "set /p r=Put the original Japanese title logo back? (y/n): \n"
    'if /i not "%r%"=="y" goto :eof\n',
    'choice /c YN /m "Use the original uncensored cutscenes"\nif errorlevel 2 goto :eof\n',
    'choice /c YN /m "Press Y to restore the original Japanese theme song or N to cancel"\n'
    "if errorlevel 2 goto :eof\n"], ids=["restore", "put back", "use the original", "Y to"])
def test_a_restoring_mods_yes_goes_in_however_it_reads(tmp_path, ask):
    # its yes reads like an uninstall's, and is only tried after the no
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Japanese Song", {"install.bat": "@echo off\n" + ask + rf"""
copy /y "%~dp0Japanese\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "Japanese/op.win32.wmp": b"kimi ga iru kara " * 300})
    assert laid(play(game, folder)) == {MOVIE: b"kimi ga iru kara " * 300}


@pytest.mark.parametrize("ask", [
    "set /p r=Skip the cutscenes? They are 6 GB (y/n): \n"
    'if /i "%r%"=="y" goto done\n',
    'choice /c YN /m "Install without the movies"\nif errorlevel 2 goto movies\ngoto done\n'
    ":movies\n",
    "set /p r=Do you also want to skip the 4K FMVs to save 6 GB? (y/n): \n"
    'if /i "%r%"=="y" goto done\n',
    "set /p r=Do you want to leave the movies untouched? (y/n): \n"
    'if /i "%r%"=="y" goto done\n',
    'choice /c YN /m "Save space by not installing the 4K movies"\n'
    "if errorlevel 2 goto movies\ngoto done\n:movies\n"],
    ids=["skip them", "install without", "also skip", "untouched", "save space"])
def test_a_pack_asking_to_leave_its_own_movies_out_puts_them_in(tmp_path, ask):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "FFXIII HD Pack", {"install.bat": rf"""@echo off
copy /y "%~dp0menu_hd.dds" "{GAME}\white_data\prog\win\bin\menu_hd.dds" > nul
""" + ask + rf"""copy /y "%~dp0op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
:done
""", "menu_hd.dds": b"hd menu " * 300, "op.win32.wmp": b"4k opening " * 300})
    assert sorted(laid(play(game, folder))) == [MOVIE, "white_data/prog/win/bin/menu_hd.dds"]


VOICED = rf"""set "game={GAME}"
cd /d "%~dp0files"
copy /y "%game%\white_data\sys\filelist_scr%v%.win32.bin" . > nul
..\ff13tool.exe -i -ff13 filelist_scr%v%.win32.bin "%game%\white_data\sys\white_scr%v%.win32.bin" chr\pc\c004\bin\c004.win32.trb > nul
copy /y filelist_scr%v%.win32.bin "%game%\white_data\sys\" > nul
"""


@pytest.mark.parametrize("ask", [
    "echo 1. English\necho 2. Japanese\nchoice /c 12 /n /m \"Choice: \"\n"
    "if errorlevel 2 (set v=c) else (set v=u)\n",
    'echo U = English voices\necho J = Japanese voices\nchoice /c UJ /m "Pick one"\n'
    "if errorlevel 2 (set v=c) else (set v=u)\n",
    'choice /c 12 /m "Install for (1) English or (2) Japanese voices"\n'
    "if errorlevel 2 (set v=c) else (set v=u)\n",
    "set /p lang=Type english or japanese voices: \n"
    'if /i "%lang%"=="japanese" (set v=c) else (set v=u)\n',
    'choice /c 12 /m "Voices: 1 (English) or 2 (Japanese)"\n'
    "if errorlevel 2 (set v=c) else (set v=u)\n",
    "echo 1. English voices\necho 2. Japanese voices\necho 3. Exit\n"
    'choice /c 123 /n /m "Select: "\nif errorlevel 3 goto :eof\n'
    "if errorlevel 2 (set v=c) else (set v=u)\n",
    "set /p lang=Voices (EN/JP): \n" 'if /i "%lang%"=="JP" (set v=c) else (set v=u)\n'],
    ids=["1 2 above Choice:", "U J", "Install for (1) or (2)", "typed", "1 (English)",
         "with an Exit", "EN or JP"])
def test_a_voices_question_however_its_put_is_played_for_each_voice_set(tmp_path, ask):
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "Vanille Mod", {"install.bat": "@echo off\n" + ask + VOICED,
                                            "ff13tool.exe": b"MZ",
                                            f"files/{TRB}": b"natural trb " * 120})
    played = play(game, folder)
    assert not played.versions
    assert sorted(laid(played)) == [f"white_scrc/{TRB}", f"white_scru/{TRB}"]


def test_restoring_the_japanese_voices_as_well_is_a_part_not_the_voices_question(tmp_path):
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "HD Pack", {"install.bat": rf"""@echo off
set /p r=Restore the original Japanese voices as well? (y/n): 
if /i "%r%"=="y" copy /y "%~dp0menu_hd.dds" "{GAME}\white_data\prog\win\bin\menu_hd.dds" > nul
copy /y "%~dp0op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "menu_hd.dds": b"hd menu " * 300, "op.win32.wmp": b"hd opening " * 300})
    played = play(game, folder)
    assert not played.languages
    assert sorted(laid(played)) == [MOVIE, "white_data/prog/win/bin/menu_hd.dds"]


@pytest.mark.parametrize("beside", [
    {"Install Instructions.bat": '@echo off\nstart "" "%~dp0Install Instructions.txt"\n'},
    {"Patch Notes.bat": '@echo off\nstart "" "%~dp0Patch Notes.txt"\n'},
    {"How to Install.bat": "@echo off\necho 1. Run 4K Opening.bat\necho 2. Enjoy\npause\n"},
    {"Install ReShade.bat": '@echo off\ncopy /y "%~dp0ReShade\\dxgi.dll" "{game}\\" > nul\n'}],
    ids=["readme with spaces", "notes", "only says how", "another program's"])
def test_a_bat_that_isnt_the_installer_doesnt_outrank_it(beside):
    files = {"4K Opening.bat": ONE_COPY.format("Data", GAME),
             **{k: v.replace("{game}", GAME) for k, v in beside.items()}}
    found = sandbox.find([(m, 1) for m in files], lambda m: files[m].encode(), "white_data")
    assert found[0][0] == "4K Opening.bat"


@pytest.mark.parametrize("second", [
    "set /p b=Continue (Y,N): \n" 'if /i not "%b%"=="y" goto :eof\n',
    'choice /c YN /m "Continue"\nif errorlevel 2 goto :eof\n'], ids=["set /p", "choice"])
def test_a_bare_continue_after_a_set_p_question_isnt_read_off_it(tmp_path, second):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Pack", {"install.bat": "@echo off\n"
                                        "set /p a=Do you want to keep the original intro? (y/n): \n"
                                        + second + rf"""
if /i not "%a%"=="y" copy /y "%~dp0op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
copy /y "%~dp0menu_hd.dds" "{GAME}\white_data\prog\win\bin\menu_hd.dds" > nul
""", "op.win32.wmp": b"new intro " * 300, "menu_hd.dds": b"hd menu " * 300})
    assert sorted(laid(play(game, folder))) == [MOVIE, "white_data/prog/win/bin/menu_hd.dds"]


@pytest.mark.parametrize("payload", ["Restore Cut Content", "Original Soundtrack"])
def test_dropped_again_with_its_payload_in_a_folder_named_like_originals(tmp_path, monkeypatch,
                                                                         payload):
    game, bridge = drawer(tmp_path, monkeypatch)
    ffxiii = GAME.replace("FINAL FANTASY XIII", "FFXIII")
    folder = drop(tmp_path, "Cut Content Mod", {"Install.bat": rf"""@echo off
xcopy /s /y /i "%~dp0{payload}\white_data" "{ffxiii}\white_data" > nul
""", f"{payload}/{MOVIE}": b"uncut opening " * 300})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    again = bridge.install_mod("ff13", str(folder))
    assert again["ok"] and again["summary"] == "Cut Content Mod is in already."


def test_an_installer_per_voice_set_in_folders_side_by_side_is_a_version(tmp_path):
    game = xiii(tmp_path / "steam")
    files = {"ff13tool.exe": b"MZ"}
    for folder, v in (("English Voices", "u"), ("Japanese Voices", "c")):
        files[f"{folder}/Install.bat"] = "@echo off\nset v=" + v + "\n" + VOICED.replace(
            "..\\ff13tool.exe", "..\\..\\ff13tool.exe")
        files[f"{folder}/files/{TRB}"] = b"natural trb " * 120
    folder = drop(tmp_path, "Vanille Outfit", files)
    assert play(game, folder).versions == ["English Voices", "Japanese Voices"]
    picked = play(game, folder, version="Japanese Voices")
    assert sorted(laid(picked)) == [f"white_scrc/{TRB}"]


def test_what_a_bat_that_put_nothing_in_last_said_isnt_the_drawers_own_answer(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "HD Opening", {"install.bat": """@echo off
echo This needs the HD Project, which isn't there.
choice /c YN /m "Continue"
"""})
    played = play(game, folder)
    assert played.problem.endswith('"This needs the HD Project, which isn\'t there."')


@pytest.mark.parametrize("ask", [
    "set /p r=Do you play with Japanese voices? (y/n): \n"
    'if /i "%r%"=="y" (set v=c) else goto :eof\n',
    'choice /c JE /m "(J)apanese voices or (E)xit"\nif errorlevel 2 goto :eof\nset v=c\n'],
    ids=["y/n", "or exit"])
def test_a_question_naming_the_japanese_voices_isnt_taken_for_the_voices_question(tmp_path, ask):
    # a part of the mod as often, so its yes is what goes in
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "Lip Sync Fix", {"install.bat": "@echo off\n" + ask + VOICED,
                                             "ff13tool.exe": b"MZ",
                                             f"files/{TRB}": b"natural trb " * 120})
    played = play(game, folder)
    assert not played.languages and sorted(laid(played)) == [f"white_scrc/{TRB}"]


@pytest.mark.parametrize("ask, versions", [
    ("echo 1. English theme song (My Hands)\necho 2. Japanese theme song (Kimi ga Iru Kara)\n"
     'choice /c 12 /n /m "Choice: "\nif errorlevel 2 (set s=jp) else (set s=en)\n',
     ["English theme song (My Hands)", "Japanese theme song (Kimi ga Iru Kara)"]),
    ("set /p s=Which theme song do you want, english or japanese? \n"
     'if /i "%s%"=="japanese" (set s=jp) else (set s=en)\n', [])], ids=["menu", "typed"])
def test_a_choice_of_english_or_japanese_songs_isnt_the_voices_question(tmp_path, ask, versions):
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "Theme Song", {"install.bat": "@echo off\n" + ask + rf"""
copy /y "%~dp0%s%\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "en/op.win32.wmp": b"my hands " * 300, "jp/op.win32.wmp": b"kimi ga iru kara " * 300})
    played = play(game, folder)
    assert not played.languages and played.versions == versions


def test_a_menu_put_up_before_another_question_is_still_its_versions(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Vanille Outfits", {"install.bat": r"""@echo off
echo This mod has two outfits:
echo 1. Naturey
echo 2. Pulse robe
choice /c YN /m "Continue"
if errorlevel 2 goto :eof
set /p pick=Which outfit (1/2)? 
if "%pick%"=="1" set src=naturey
if "%pick%"=="2" set src=robe
if not defined src goto :eof
""" + rf"""copy /y "%~dp0%src%\op.win32.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""", "naturey/op.win32.wmp": b"naturey " * 300, "robe/op.win32.wmp": b"robe " * 300})
    assert play(game, folder).versions == ["Naturey", "Pulse robe"]


def test_tied_installers_in_folders_side_by_side_are_each_offered_by_name(tmp_path):
    files = {f"{voices}/{name}": ONE_COPY.format(voices, GAME)
             for voices in ("English", "Japanese") for name in ("Install.bat",
                                                                 "Install Extras.bat")}
    found = sandbox.find([(m, 1) for m in files], lambda m: files[m].encode(), "white_data")
    named = sandbox.labels(sandbox.rivals(found))
    assert named == {"Install Extras (English)": "English/Install Extras.bat",
                     "Install (English)": "English/Install.bat",
                     "Install Extras (Japanese)": "Japanese/Install Extras.bat",
                     "Install (Japanese)": "Japanese/Install.bat"}


FAQ = r"""@echo off
type "%~dp0readme.txt"
choice /c YN /m "Continue"
if errorlevel 2 goto :eof
copy /y "%~dp0Data\op.win32.wmp" "{game}\white_data\movie\us\op.win32.wmp" > nul
"""
README = "HD Opening {}\r\n\r\nFAQ\r\nQ: How do I uninstall?\r\nA: Run Uninstall.bat.\r\n"


def test_the_next_version_of_a_mod_whose_question_is_misread_goes_in(tmp_path, monkeypatch):
    # the readme's "How do I uninstall?" makes Continue a no first; the drawer
    # has 1.0 in, but 1.1's yes still has to be looked for
    game, bridge = drawer(tmp_path, monkeypatch)
    ffxiii = GAME.replace("FINAL FANTASY XIII", "FFXIII")
    for version, data in (("1.0", b"hd opening " * 300), ("1.1", b"sharper opening " * 300)):
        folder = drop(tmp_path / version, f"HD Opening v{version}", {
            "install.bat": FAQ.format(game=ffxiii), "readme.txt": README.format(version),
            "Uninstall.bat": "@echo off\necho bye\n", "Data/op.win32.wmp": data})
        assert bridge.install_mod("ff13", str(folder))["ok"]
    assert (game / MOVIE).read_bytes() == b"sharper opening " * 300


def test_dropped_again_after_steam_put_the_game_back_the_injector_goes_back_in(tmp_path,
                                                                              monkeypatch):
    game, bridge = drawer(tmp_path, monkeypatch)
    sys_dir = game / "white_data" / "sys"
    steam = {p.name: p.read_bytes() for p in sys_dir.iterdir()}
    folder = drop(tmp_path, "XIII Vanille All Naturey", {
        "XIII Vanille All Naturey.bat": INJECTOR.replace("title Vanille installer\n",
                                                         "title Vanille installer\n"
                                                         "echo Uninstalling? Run Uninstall.bat.\n"
                                                         'choice /c YN /m "Continue"\n'
                                                         "if errorlevel 2 goto :eof\n")
        .replace("FINAL FANTASY XIII", "FFXIII"),
        "ff13tool.exe": b"MZ", f"files/{TRB}": b"natural trb " * 120})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    for name, data in steam.items():
        (sys_dir / name).write_bytes(data)
    from patcher.mods.hd_zones import read_record

    again = bridge.install_mod("ff13", str(folder))
    entry = game_files.entries(sys_dir / "filelist_scru.win32.bin")[TRB]
    assert again["ok"] and "is in already" not in again["summary"]
    assert read_record(sys_dir / "white_scru.win32.bin", entry) == b"natural trb " * 120


TOGGLE = r"""@echo off
set "game={game}"
if exist "%game%\HDOpening_installed.txt" goto installed
copy /y "%~dp0Mod\op.win32.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
echo installed> "%game%\HDOpening_installed.txt"
goto :eof
:installed
{ask}copy /y "%~dp0Vanilla Files\op.win32.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
del "%game%\HDOpening_installed.txt"
"""


@pytest.mark.parametrize("ask", [
    "echo 1. Uninstall\necho 2. Exit\nchoice /c 12 /n\nif errorlevel 2 goto :eof\n",
    "set /p r=Type UNINSTALL to remove it, or just press Enter to exit: \n"
    'if /i not "%r%"=="uninstall" goto :eof\n',
    "echo Do you want to uninstall it\nset /p r=(Y/N): \n" 'if /i not "%r%"=="y" goto :eof\n',
    ""], ids=["a menu", "typed", "asked without a ?", "not asked"])
def test_dropped_again_a_toggles_uninstall_is_told_by_what_it_puts_back(tmp_path, monkeypatch,
                                                                        ask):
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "HD Opening", {
        "install.bat": TOGGLE.format(game=GAME.replace("FINAL FANTASY XIII", "FFXIII"), ask=ask),
        "Mod/op.win32.wmp": b"hd opening " * 300,
        "Vanilla Files/op.win32.wmp": (game / MOVIE).read_bytes()})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    again = bridge.install_mod("ff13", str(folder))
    assert (game / MOVIE).read_bytes() == b"hd opening " * 300
    assert again["ok"] and again["summary"] == "HD Opening is in already."


@pytest.mark.parametrize("ask", [
    "set /p r=Skip the Eidolon summon animations too? (y/n): \n"
    'if /i not "%r%"=="y" goto done\n',
    'choice /c YN /m "Skip the opening logos as well"\nif errorlevel 2 goto done\n'],
    ids=["set /p", "choice"])
def test_a_part_a_skip_mod_offers_on_top_goes_in(tmp_path, ask):
    # worded like an HD pack's "also skip the movies?", told apart by what
    # each answer puts in: the yes puts in all the no does and more
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Skip Intro", {"install.bat": rf"""@echo off
copy /y "%~dp0blank.wmp" "{GAME}\white_data\movie\us\op.win32.wmp" > nul
""" + ask + rf"""copy /y "%~dp0blank.dds" "{GAME}\white_data\prog\win\bin\menu_hd.dds" > nul
:done
""", "blank.wmp": b"blank " * 300, "blank.dds": b"blank dds " * 300})
    assert sorted(laid(play(game, folder))) == [MOVIE, "white_data/prog/win/bin/menu_hd.dds"]


BARE = r"""@echo off
setlocal enableextensions enabledelayedexpansion
cd /d "%~dp0"
if not exist "..\..\FINAL FANTASY XIII" goto DIRNOTOK
if exist "..\white_data\sys\filelist_scru.win32.bin" goto FILEOK
if not exist "..\white_data\sys\filelist_scrc.win32.bin" goto DIRNOTOK
:FILEOK
if not exist "chr\pc\c004\bin" goto DIRNOTOK
CHOICE /C ND /N /M "Install (N)ude Vanille, or (D)efault Vanille? "
if %errorlevel% equ 2 goto DEFAULT
CHOICE /C BF /N /M "Should Vanille have (B)oots, or bare (F)eet? "
if %errorlevel% equ 1 copy data\boots.trb chr\pc\c004\bin\c004.win32.trb > nul
if %errorlevel% equ 2 copy data\bare.trb chr\pc\c004\bin\c004.win32.trb > nul
goto INSTALL
:DEFAULT
copy data\orig.trb chr\pc\c004\bin\c004.win32.trb > nul
:INSTALL
if not exist "..\white_data\sys\filelist_scru.win32.bin" goto JP
data\ff13tool -i ..\white_data\sys\filelist_scru.win32.bin ..\white_data\sys\white_scru.win32.bin chr\pc\c004\bin\c004.win32.trb
:JP
if not exist "..\white_data\sys\filelist_scrc.win32.bin" goto :eof
data\ff13tool -i ..\white_data\sys\filelist_scrc.win32.bin ..\white_data\sys\white_scrc.win32.bin chr\pc\c004\bin\c004.win32.trb
goto :eof
:DIRNOTOK
echo Unzip this into a folder inside FINAL FANTASY XIII, keeping its empty chr\pc\c004\bin
"""


def bare(tmp_path) -> Path:
    """BareLight's shape: a zip with an empty folder for its .bat to copy into"""
    import zipfile
    out = tmp_path / "Bare Vanille.zip"
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("Bare/Bare.bat", BARE.replace("\n", "\r\n"))
        for d in ("Bare/chr/", "Bare/chr/pc/", "Bare/chr/pc/c004/", "Bare/chr/pc/c004/bin/"):
            z.writestr(zipfile.ZipInfo(d), b"")
        z.writestr("Bare/data/ff13tool.exe", b"MZ")
        z.writestr("Bare/data/boots.trb", b"boots " * 100)
        z.writestr("Bare/data/bare.trb", b"bare feet " * 100)
        z.writestr("Bare/data/orig.trb", b"vanilla trb " * 100)
    return out


def test_an_empty_folder_its_zip_ships_is_there_for_its_bat(tmp_path):
    # and English alone is still the game it climbs to, scrc or not
    game = xiii(tmp_path / "steam", voices="u")
    played = play(game, bare(tmp_path))
    assert not played.problem, (played.problem, played.detail)
    # its Default is the game as it is, so it isn't a version of the mod, and
    # a question with one answer left isn't asked
    assert played.versions == ["Nude Vanille, Boots", "Nude Vanille, Bare feet"], played.versions
    assert laid(play(game, bare(tmp_path), version="Nude Vanille, Bare feet")) == {
        f"white_scru/{TRB}": b"bare feet " * 100}


def test_a_choice_naming_its_answers_is_asked_a_question_at_a_time(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    sys_dir = game / "white_data" / "sys"
    # a game whose c004 isn't the one its Default puts back, so that's a version
    pack_plain(sys_dir / "white_scru.win32.bin", sys_dir / "filelist_scru.win32.bin",
               [(TRB, b"someone's trb " * 100)])
    zipped = bare(tmp_path)
    assert play(game, zipped).versions == ["Nude Vanille", "Default Vanille"]
    assert play(game, zipped, version="Nude Vanille").versions == [
        "Nude Vanille, Boots", "Nude Vanille, Bare feet"]
    assert laid(play(game, zipped, version="Nude Vanille, Boots")) == {
        f"white_scru/{TRB}": b"boots " * 100}


@pytest.mark.parametrize("keys, prompt, labels", [
    ("BF", "Should Lightning have (B)oots, or bare (F)eet? ", {"B": "Boots", "F": "Bare feet"}),
    ("PN", "Do you want the leg (P)ouch or (N)o pouch? ", {"P": "Pouch", "N": "No pouch"}),
    ("EMLN", "Do you want an (E)arly, (M)id or (L)ate game l'Cie brand, or (N)one? ",
     {"E": "Early", "M": "Mid", "L": "Late game l'Cie brand", "N": "None"})])
def test_a_choice_says_its_answers_the_way_the_question_puts_them(keys, prompt, labels):
    assert sandbox.select_labels(keys, prompt) == labels


def both_sets(tmp_path, monkeypatch):
    """XIII in its Steam-named folder with both voice sets, and the drawer's bridge"""
    library = tmp_path / "steam"
    game = xiii(library)
    (library / "steamapps" / "appmanifest_292120.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"292120"\n\t"installdir"\t\t"FINAL FANTASY XIII"\n}\n')
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    return game, UiBridge(manifest_paths={"ffxiii": MANIFESTS / "ffxiii.json"},
                          steam_library_paths=[library])


def scr(game, voice, path=TRB) -> bytes:
    from patcher.mods.hd_zones import read_record
    sys_dir = game / "white_data" / "sys"
    return read_record(sys_dir / f"white_scr{voice}.win32.bin",
                       game_files.entries(sys_dir / f"filelist_scr{voice}.win32.bin")[path])


def test_what_a_mod_went_over_comes_back_from_the_other_voice_set(tmp_path, monkeypatch):
    game, bridge = both_sets(tmp_path, monkeypatch)
    vanilla = scr(game, "u")
    sys_dir = game / "white_data" / "sys"
    entry = game_files.entries(sys_dir / "filelist_scru.win32.bin")[TRB]
    folder = drop(tmp_path, "English Trb", {f"white_scru/{TRB}": b"english only " * 300})
    assert bridge.install_mod("ff13", str(folder))["ok"]
    # a repack's dropped the English original from where it sat
    with open(sys_dir / "white_scru.win32.bin", "r+b") as f:
        f.seek(entry.byte_offset)
        f.write(b"\0" * entry.stored)
    done = bridge.remove_addon("ff13", "English Trb", "files")
    assert done["ok"] and "nothing left" not in done["message"], done
    assert scr(game, "u") == vanilla


def test_a_remove_with_nothing_under_a_file_still_comes_off_and_keeps_its_pair_whole(
        tmp_path, monkeypatch):
    game, bridge = both_sets(tmp_path, monkeypatch)
    body = drop(tmp_path, "Vanille Body", {f"white_scru/{TRB}": b"mod trb " * 300,
                                           f"white_scru/{IMGB}": b"mod imgb " * 300})
    other = drop(tmp_path, "Lightning Tweak", {f"white_scru/{LIGHT}": b"mod light " * 300})
    assert bridge.install_mod("ff13", str(body))["ok"]
    # another mod since, so its index can't just go back whole
    assert bridge.install_mod("ff13", str(other))["ok"]
    real = game_files._way_back
    # nothing left under its .imgb anywhere
    monkeypatch.setattr(game_files, "_way_back", lambda view, spec, item, lower: None
                        if item["path"] == IMGB else real(view, spec, item, lower))
    done = bridge.remove_addon("ff13", "Vanille Body", "files")
    assert done["ok"], done
    assert "c004.win32.imgb and c004.win32.trb had nothing left" in done["message"], done
    # the model stays the mod's, both halves, rather than half of each
    assert (scr(game, "u", TRB), scr(game, "u", IMGB)) == (b"mod trb " * 300, b"mod imgb " * 300)
    assert scr(game, "u", LIGHT) == b"mod light " * 300
    assert [m["name"] for m in bridge.list_addons("ff13")["mods"]
            if m.get("kind") == "files"] == ["Lightning Tweak"]


NOVA_LOOSE = rf"""@echo off
if not exist "{GAME}\white_data\chr\pc\c004\bin\c004.win32.trb" goto nonova
copy /y "%~dp0c004.win32.trb" "{GAME}\white_data\chr\pc\c004\bin\c004.win32.trb" > nul
echo Done. Launch through Nova Chrysalia.
goto :eof
:nonova
echo Unpack the game with Nova Chrysalia first.
"""


def test_a_mod_made_for_novas_unpacked_game_goes_into_the_archives(tmp_path, monkeypatch):
    # the game unpacked is faked out of the archives, nothing written out for it
    game, bridge = both_sets(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Loose Vanille", {"install.bat": NOVA_LOOSE,
                                              "c004.win32.trb": b"loose trb " * 300})
    played = play(game, folder)
    assert "with the game unpacked the way Nova Chrysalia does it" in played.tried, played.tried
    done = bridge.install_mod("ff13", str(folder))
    assert done["ok"], done
    assert scr(game, "u") == scr(game, "c") == b"loose trb " * 300
    assert not (game / "white_data" / "chr").exists()
    assert bridge.remove_addon("ff13", "Loose Vanille", "files")["ok"]
    assert scr(game, "u") == b"vanilla trb " * 100
