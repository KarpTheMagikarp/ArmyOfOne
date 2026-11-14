"""
A .bat that gives up, played until it doesn't or said exactly why it can't.

Each script here is the shape real installers come in, failing the way they
fail on a PC that isn't their author's: the game in another folder, a
registry key its installer writes, a runtime it checks for, the download
zipped one folder deeper, a Windows version or admin check, a tool that
stops to ask something. Where the virtual PC can be what the script wanted,
the mod goes in, and the drawer's message says what was changed to get it
there. Where it can't, the reason is the cause, never the script's goodbye.
"""

import struct
import sys

import pytest

from patcher.mods import diagnose, sandbox
from tests.helpers import make_import_pe, make_locked_zip_bytes, make_zip_bytes
from tests.test_hd_runner import stub
from tests.test_sandbox import GAME, MOVIE, drawer, drop, laid, play, xiii

PAYLOAD = b"outfit " * 300
COPY = r"""copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""
BYE = "echo Exiting...\npause\n"
# the real lookups, before conftest swaps them for none on every test
HOST_RUNTIME, HOST_ARCHIVER = sandbox.host_runtime, sandbox.host_archiver


def played(tmp_path, script, files=None, setup=None, name="Outfit"):
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, name, {"install.bat": script, **(files or {"op.win32.wmp": PAYLOAD})})
    if setup is not None:
        setup(folder)
    return play(game, folder)


def python_here(monkeypatch):
    """this PC's Python lent to the play, the way a PC with Python installed has it"""
    monkeypatch.setattr(sandbox, "host_runtime",
                        lambda runtime: sys.executable if runtime == "python" else None)


def test_the_game_where_square_enix_installs_it(tmp_path):
    p = played(tmp_path, r"""@echo off
set "game=C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII"
if not exist "%game%\white_data\prog\win\bin\ffxiiiimg.exe" (echo Game not found & pause & exit /b 1)
""" + COPY + BYE)
    assert laid(p) == {MOVIE: PAYLOAD}, p.problem
    assert p.tried == ["with the game at C:\\Program Files (x86)\\Square Enix\\FINAL FANTASY XIII "
                       "too"]


def test_the_game_on_a_drive_this_pc_has_no_letter_for(tmp_path):
    """no check at all, the copy itself finds nowhere to go"""
    p = played(tmp_path, r"""@echo off
copy /y "%~dp0op.win32.wmp" "D:\Games\FINAL FANTASY XIII\white_data\movie\us\" > nul
""" + BYE)
    assert laid(p) == {MOVIE: PAYLOAD}, p.problem
    assert p.tried == ["with the game at D:\\Games\\FINAL FANTASY XIII too"]


def test_the_game_read_out_of_square_enixs_registry_entry(tmp_path):
    p = played(tmp_path, r"""@echo off
for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Square Enix\FINAL FANTASY XIII" /v InstallLocation 2^>nul') do set "game=%%b"
if not defined game (echo FINAL FANTASY XIII isn't installed & pause & exit /b 1)
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, p.problem
    assert p.tried == ["with the registry key it checks for, HKLM\\SOFTWARE\\WOW6432Node\\"
                       "Square Enix\\FINAL FANTASY XIII"]


@pytest.mark.parametrize("files", [
    {"Outfit v1.2/op.win32.wmp": PAYLOAD},
    {"Files/Outfit/op.win32.wmp": PAYLOAD, "Files/readme.txt": b"read me"},
], ids=["zipped one folder deeper", "two folders deeper"])
def test_a_payload_zipped_deeper_than_its_bat_expects(tmp_path, files):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n'
               r"""if not exist "%~dp0op.win32.wmp" (echo Extract all files first & pause & exit)
""" + COPY, files=files)
    assert laid(p) == {MOVIE: PAYLOAD}, p.problem
    assert len(p.tried) == 1 and p.tried[0].endswith("op.win32.wmp where it looked for it")


def test_a_payload_folder_zipped_deeper(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""if not exist "%~dp0data\" (echo The data folder is missing & pause & exit /b 1)
xcopy /e /i /y "%~dp0data" "%game%\white_data" > nul
""", files={"Outfit Mod/data/movie/us/op.win32.wmp": PAYLOAD})
    assert laid(p) == {MOVIE: PAYLOAD}, p.problem
    assert p.tried == ["with its Outfit Mod\\data where it looked for it"]


def test_one_made_to_be_run_from_inside_the_games_folder(tmp_path):
    """it looks for the game's launcher beside itself, which it is once unzipped into the game"""
    p = played(tmp_path, r"""@echo off
if not exist "%~dp0FFXiiiLauncher.exe" (echo Put these files in the game folder first & pause & exit /b 1)
copy /y "%~dp0op.win32.wmp" "%~dp0white_data\movie\us\" > nul
""" + BYE)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["from inside the game's folder"]


CLIMBS = r"""@echo off
if not exist "..\white_data\sys\filelist_scrc.win32.bin" goto DIRNOTOK
CHOICE /C YN /N /M "Install the outfit? (Y/N) "
if %ERRORLEVEL% EQU 2 goto :eof
copy /y "op.win32.wmp" "..\white_data\movie\us\" > nul
goto :eof
:DIRNOTOK
echo Unzip this folder into your FINAL FANTASY XIII folder first
pause
"""


@pytest.mark.parametrize("member", ["Lightning Mod/BareLight.bat", "BareLight.bat",
                                    "FINAL FANTASY XIII/Lightning Mod/BareLight.bat"])
def test_one_made_to_be_unzipped_a_folder_down_inside_the_game(tmp_path, member):
    """
    ..\\white_data\\sys is the game once its folder's unzipped into the game's,
    whether the zip has that folder, the .bat sits loose at its top, or the
    zip's laid out like steamapps\\common. Never a part of the download it's
    missing
    """
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "Lightning Mod", {
        member: CLIMBS, member.replace("BareLight.bat", "op.win32.wmp"): PAYLOAD})
    p = play(game, folder)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["unzipped into a folder of its own inside the game's"]


def test_one_climbing_to_the_game_with_nothing_to_find_it_by_starts_there(tmp_path):
    game = xiii(tmp_path / "steam")
    folder = drop(tmp_path, "Movie Mod", {"Movie Mod/install.bat": r"""@echo off
copy /y "op.win32.wmp" "..\white_data\movie\us\" > nul
""", "Movie Mod/op.win32.wmp": PAYLOAD})
    p = play(game, folder)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == []


def test_a_file_of_the_games_it_wants_copied_in_beside_it_is_lent(tmp_path):
    """the readme's "copy ffxiiiimg.exe into this folder first", done for it"""
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""if not exist "%~dp0ffxiiiimg.exe" (echo Copy ffxiiiimg.exe from the game in here first & pause & exit /b 1)
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\" > nul
""")
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the game's ffxiiiimg.exe where it looked for it"]


def test_a_search_through_its_own_folders_finds_the_payload_where_it_is(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""if exist "%~dp0data\op.win32.wmp" set "src=%~dp0data"
if exist "%~dp0files\op.win32.wmp" set "src=%~dp0files"
if "%src%"=="" (echo Can't find the mod's files & pause & exit /b 1)
copy /y "%src%\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", files={"Outfit/op.win32.wmp": PAYLOAD})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with its Outfit\\op.win32.wmp where it looked for it"]


def test_a_search_for_a_program_it_expects_installed_names_the_program(tmp_path):
    p = played(tmp_path, r"""@echo off
if exist "C:\Program Files\xdeltaUI\xdelta3.exe" set "tool=C:\Program Files\xdeltaUI\xdelta3.exe"
if exist "C:\Program Files (x86)\xdeltaUI\xdelta3.exe" set "tool=C:\Program Files (x86)\xdeltaUI\xdelta3.exe"
if not defined tool (echo Install xdeltaUI first & pause & exit /b 1)
""" + BYE)
    assert p.problem == "said \"Install xdeltaUI first\""
    assert p.detail.startswith("It looks for xdelta3.exe in 2 places, and it isn't in any of "
                               "them: C:\\Program Files\\xdeltaUI\\xdelta3.exe and "
                               "C:\\Program Files (x86)\\xdeltaUI\\xdelta3.exe."), p.detail
    assert "the game" not in p.problem + p.detail


@pytest.mark.parametrize("script", [
    r"""@echo off
if "%~1"=="" (echo Drag your FINAL FANTASY XIII folder onto this file & pause & exit /b)
set "game=%~1"
""" + COPY,
    r"""@echo off
set GAMEDIR=%1
if [%GAMEDIR%]==[] goto usage
copy /y "%~dp0op.win32.wmp" %GAMEDIR%\white_data\movie\us\ > nul
goto :eof
:usage
echo Usage: drag the game folder onto install.bat
pause
""",
    r"""@echo off
if not "%~1"=="" goto start
echo Drag and drop the game's folder onto this .bat
pause
exit /b
:start
set "game=%~1"
""" + COPY,
], ids=["checks %~1", "checks a variable set from %1", "falls through to its usage"])
def test_one_made_to_have_the_games_folder_dragged_onto_it(tmp_path, script):
    p = played(tmp_path, script)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the game's folder dragged onto it"]


def test_one_made_to_have_the_games_exe_dragged_onto_it(tmp_path):
    p = played(tmp_path, r"""@echo off
if /i not "%~nx1"=="ffxiiiimg.exe" (echo Drag ffxiiiimg.exe onto this file & pause & exit /b 1)
set "game=%~dp1..\..\..\.."
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the game's ffxiiiimg.exe dragged onto it"]


def test_a_calls_own_percent_one_isnt_something_dragged_on(tmp_path):
    p = played(tmp_path, r"""@echo off
call :need "%~dp0data\op.win32.wmp"
goto :eof
:need
if not exist "%~1" (echo Missing %~nx1 & pause & exit /b 1)
""", files={"readme.txt": b"no payload here"})
    assert p.tried == [], p.tried
    assert "dragged" not in p.problem + p.detail


def test_the_games_save_folder_is_never_taken_for_the_game(tmp_path):
    p = played(tmp_path, r"""@echo off
if not exist "%USERPROFILE%\Documents\My Games\FINAL FANTASY XIII" (echo Run the game once first & pause & exit /b 1)
""" + BYE)
    assert p.tried == []
    assert p.problem == ("looks for the game's saves and settings at C:\\Users\\Player\\Documents"
                         "\\My Games\\FINAL FANTASY XIII, which a copy of the game doesn't have")
    assert "echo Run the game once first" in p.detail


def test_two_candidates_for_a_missing_payload_are_said_not_guessed(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n'
               r"""if not exist "%~dp0op.win32.wmp" (echo op.win32.wmp is missing & pause & exit)
""" + COPY, files={"Short/op.win32.wmp": PAYLOAD, "Long/op.win32.wmp": b"long " * 300})
    assert p.problem == "needs op.win32.wmp from its download, which isn't in what was dropped"
    assert p.tried == []
    assert p.detail.startswith("Its download has op.win32.wmp in more than one place "
                               "(Long\\op.win32.wmp and Short\\op.win32.wmp)"), p.detail


def test_a_script_that_wont_run_as_admin(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""net session >nul 2>&1
if %errorlevel% == 0 (echo Please don't run this as administrator & pause & exit /b)
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, p.problem
    assert p.tried == ["as a user without admin rights"]


def test_a_script_that_checks_for_windows_7(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""ver | find "6.1" >nul || (echo This patch only runs on Windows 7 & pause & exit /b 1)
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, p.problem
    assert p.tried == ["on Windows 6.1"]


def test_a_dotnet_framework_check(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""reg query "HKLM\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" /v Release >nul 2>&1 || (echo .NET Framework 4.8 is required & pause & exit /b 1)
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, p.problem
    assert p.tried == ["with the registry key it checks for, HKLM\\SOFTWARE\\Microsoft\\NET "
                       "Framework Setup\\NDP\\v4\\Full"]


INSTALL_PY = """import pathlib, sys
game = pathlib.Path(sys.argv[1].replace("\\\\", "/"))
here = pathlib.Path(__file__).resolve().parent
target = game / "white_data" / "movie" / "us"
target.mkdir(parents=True, exist_ok=True)
(target / "op.win32.wmp").write_bytes((here / "op.win32.wmp").read_bytes())
print("Done")
"""
PYTHON_BAT = f'@echo off\nset "game={GAME}"\n' + r"""where python >nul 2>&1 || (echo Python 3 is required & pause & exit /b 1)
python "%~dp0install.py" "%game%"
if errorlevel 1 (echo Install failed & pause & exit /b 1)
"""


def test_python_this_pc_has_runs_its_script(tmp_path, monkeypatch):
    python_here(monkeypatch)
    p = played(tmp_path, PYTHON_BAT, files={"op.win32.wmp": PAYLOAD, "install.py": INSTALL_PY})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_python_this_pc_hasnt_got(tmp_path):
    p = played(tmp_path, PYTHON_BAT, files={"op.win32.wmp": PAYLOAD, "install.py": INSTALL_PY})
    assert p.problem == "needs Python, which isn't installed on this PC"
    assert p.detail.startswith("Install Python 3 from python.org"), p.detail
    assert "Exiting" not in p.problem + p.detail


def test_python_run_without_a_check_first(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\npython "%~dp0install.py" "%game%"\n'
               + BYE, files={"op.win32.wmp": PAYLOAD, "install.py": INSTALL_PY})
    assert p.problem == "needs Python, which isn't installed on this PC"


def test_a_python_package_it_needs(tmp_path, monkeypatch):
    python_here(monkeypatch)
    p = played(tmp_path, PYTHON_BAT, files={"op.win32.wmp": PAYLOAD,
                                            "install.py": "import zz_not_a_real_package\n"})
    assert p.problem == ("runs python.exe, which needs the Python package zz_not_a_real_package, "
                         "which isn't installed")
    assert "pip install zz_not_a_real_package" in p.detail


def test_a_tool_that_waits_for_a_key_once_its_done(tmp_path):
    """Console.ReadKey with no console: the work's done, and on a PC a key ends it"""
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patcher.exe" "%game%\white_data\movie\us\op.win32.wmp"
if errorlevel 1 (echo Patching failed & pause & exit /b 1)
""", setup=lambda folder: stub(folder, "patcher.exe", "import pathlib, sys\n"
                               "movie = pathlib.Path(sys.argv[1].replace('\\\\', '/'))\n"
                               "movie.write_bytes(b'patched ' + movie.read_bytes()[:20])\n"
                               "print('Patching complete!')\n"
                               "print('Press any key to exit...')\n"
                               "sys.stderr.write('Unhandled exception. System.InvalidOperation"
                               "Exception: Cannot read keys when either application does not "
                               "have a console or when console input has been redirected.\\n')\n"
                               "sys.exit(3762504530 & 0xFF)\n"))
    assert laid(p) == {MOVIE: b"patched opening movie openin"}, (p.problem, p.detail)


def test_a_tool_that_asks_yes_or_no_is_answered_yes(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patcher.exe" "%game%\white_data\movie\us\op.win32.wmp"
if errorlevel 1 (echo Patching failed & pause & exit /b 1)
""", setup=lambda folder: stub(folder, "patcher.exe", "import pathlib, sys\n"
                               "movie = pathlib.Path(sys.argv[1].replace('\\\\', '/'))\n"
                               "answer = input('Overwrite the opening movie? (y/n) ')\n"
                               "if answer.strip().lower() != 'y':\n"
                               "    sys.exit(1)\n"
                               "movie.write_bytes(b'yes ' + movie.read_bytes()[:20])\n"))
    assert laid(p) == {MOVIE: b"yes opening movie openin"}, (p.problem, p.detail)


def test_a_tool_that_asks_for_something_else_says_what(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patcher.exe"
if errorlevel 1 (echo Patching failed & pause & exit /b 1)
""", setup=lambda folder: stub(folder, "patcher.exe",
                               "input('Enter your license key: ')\n"))
    assert p.problem == ("runs patcher.exe, which stopped to ask something that can't be "
                         "answered here: \"Enter your license key:\"")


def test_a_game_version_check_against_a_file_of_the_games(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""findstr /c:"1.0.2" "%game%\FFXiiiLauncher.exe" >nul || (echo Wrong game version & pause & exit /b 1)
""" + COPY)
    assert p.problem == ("checks the game's FFXiiiLauncher.exe for \"1.0.2\", which isn't in it")
    assert "echo Wrong game version" in p.detail


def test_a_trainer_that_wants_the_game_running(tmp_path):
    p = played(tmp_path, r"""@echo off
tasklist /fi "imagename eq ffxiiiimg.exe" | find /i "ffxiiiimg.exe" >nul || (echo Start the game first & pause & exit /b 1)
""" + BYE)
    assert p.problem.startswith("wants ffxiiiimg.exe running while it works")


def test_a_damaged_archive_in_the_download(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%ProgramFiles%\7-Zip\7z.exe" x "%~dp0payload.zip" -o"%TEMP%\outfit" -y
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\outfit\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", files={"payload.zip": make_zip_bytes({"op.win32.wmp": PAYLOAD})[:300]})
    assert p.problem == "unpacks payload.zip, which looks damaged or cut short", p.problem
    assert p.detail.startswith("Download the mod again")


def test_a_question_nothing_it_checks_answers(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r""":ask
set /p code=Enter the code from the readme:
call :check "%code%"
if errorlevel 1 (echo Wrong code & goto ask)
""" + COPY + r"""goto :eof
:check
set /a sum=0
for %%c in (%~1) do set /a sum+=1
if %sum% == 42 exit /b 0
exit /b 1
""")
    assert p.problem == "kept asking \"Enter the code from the readme:\" whatever it was told"
    assert "readme or mod page" in p.detail


def test_whatever_it_gives_up_on_its_goodbye_is_never_the_reason(tmp_path):
    p = played(tmp_path, r"""@echo off
echo ======================
echo   Outfit installer
echo ======================
if not exist "%~dp0data\op.win32.wmp" goto bye
""" + COPY + "goto :eof\n:bye\n" + BYE, files={"readme.txt": b"no payload here"})
    assert "Exiting" not in p.problem and "Exiting" not in p.detail
    assert p.problem == "needs data\\op.win32.wmp from its download, which isn't in what was dropped"


def test_a_dll_its_tool_needs_is_named_with_what_it_comes_with():
    why = diagnose.program_why("patcher.exe", 0xC0000135, [],
                               make_import_pe(["KERNEL32.dll", "MSVCP140.dll", "USER32.dll"]))
    assert why.said == ("runs patcher.exe, which needs the Microsoft Visual C++ 2015-2022 "
                        "Redistributable (MSVCP140.dll isn't on this PC)")
    assert why.fix.startswith("Install the Microsoft Visual C++ 2015-2022 Redistributable")
    # cmd says the code signed, Windows' own way unsigned
    assert diagnose.program_why("patcher.exe", -1073741515, [], b"").kind == "dll"


@pytest.mark.parametrize("code, lines, said", [
    (0x80008083, ["You must install or update .NET to run this application."],
     "runs fixer.exe, which needs a .NET runtime this PC doesn't have"),
    (1, ["The framework 'Microsoft.NETCore.App', version '6.0.0' (x64) was not found."],
     "runs fixer.exe, which needs a .NET runtime this PC doesn't have"),
    (0xE0434352, ["Unhandled Exception: System.IO.FileNotFoundException: Could not find file"],
     "runs fixer.exe, which crashed with a .NET error: \"Unhandled Exception: System.IO."
     "FileNotFoundException: Could not find file\""),
    (0xC0000005, [], "runs fixer.exe, which crashed (an access violation)"),
    (0xC000007B, [], "runs fixer.exe, which loads a DLL built for the other kind of Windows "
                     "(32 against 64-bit)"),
    (2, ["There is not enough space on the disk."], "runs fixer.exe, which ran out of disk space"),
    (1, ["ERROR: Data Error : textures.pak"], "runs fixer.exe, which found an archive in the "
                                              "download damaged or cut short"),
], ids=["dotnet host", "dotnet framework named", "dotnet crash", "access violation",
        "bitness", "disk", "archive"])
def test_a_tools_exit_and_output_are_read_the_way_windows_means_them(code, lines, said):
    why = diagnose.program_why("fixer.exe", code, lines)
    assert why.said == said
    assert why.fix


def test_the_net_version_it_names_is_the_one_to_install():
    why = diagnose.program_why("fixer.exe", 1, [
        "The framework 'Microsoft.NETCore.App', version '6.0.0' (x64) was not found."])
    assert why.fix.startswith("Install the .NET 6.0 Desktop Runtime (x64)")


def test_through_the_drawer_the_reason_and_what_to_do(tmp_path, monkeypatch):
    _, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Py Mod", {"install.bat": PYTHON_BAT, "install.py": INSTALL_PY,
                                       "op.win32.wmp": PAYLOAD})
    done = bridge.install_mod("ff13", str(folder))
    assert not done["ok"]
    assert done["error"] == ("Py Mod wasn't installed: its install.bat needs Python, which isn't "
                             "installed on this PC.")
    assert done["detail"].startswith("Install Python 3 from python.org")
    assert "Where it stopped: where python" in done["detail"]


def test_through_the_drawer_a_changed_pc_is_said_with_what_went_in(tmp_path, monkeypatch):
    _, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "Elsewhere", {"install.bat": r"""@echo off
set "game=C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII"
if not exist "%game%\white_data\prog\win\bin\ffxiiiimg.exe" (echo Game not found & pause & exit /b 1)
""" + COPY, "op.win32.wmp": PAYLOAD})
    done = bridge.install_mod("ff13", str(folder))
    assert done["ok"], done
    assert ("on a copy of the game (with the game at C:\\Program Files (x86)\\Square Enix\\"
            "FINAL FANTASY XIII too), and put in what it changed") in done["message"]


@pytest.mark.parametrize("script, files, tried", [
    (r"""powershell -NoProfile -Command "Expand-Archive -Force '%~dp0pack.zip' '%TEMP%\pack'"
copy /y "%TEMP%\pack\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", {"pack.zip": make_zip_bytes({"op.win32.wmp": PAYLOAD})}, []),
    (r"""powershell -c "Add-Type -A System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory('%~dp0pack.zip', '%TEMP%\pack')"
copy /y "%TEMP%\pack\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", {"pack.zip": make_zip_bytes({"op.win32.wmp": PAYLOAD})}, []),
    (r"""where 7z >nul 2>&1 || (echo 7-Zip must be on PATH & pause & exit /b 1)
7z x "%~dp0pack.zip" -o"%TEMP%\pack" -y
copy /y "%TEMP%\pack\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", {"pack.zip": make_zip_bytes({"op.win32.wmp": PAYLOAD})}, ["with 7-Zip on the PATH"]),
    (r"""7z x "%~dp0pack.zip" -o"%TEMP%\pack" -y
copy /y "%TEMP%\pack\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", {"pack.zip": make_zip_bytes({"op.win32.wmp": PAYLOAD})}, ["with 7-Zip on the PATH"]),
    (r"""robocopy "%~dp0Files" "%game%" /E /NFL /NDL >nul
if %errorlevel% geq 8 (echo Copy failed & pause & exit /b 1)
""", {"Outfit v2/Files/white_data/movie/us/op.win32.wmp": PAYLOAD},
     ["with its Outfit v2\\Files where it looked for it"]),
    (r"""xcopy /e /i /y "%~dp0data" "%game%\white_data" >nul
""", {"Outfit v2/data/movie/us/op.win32.wmp": PAYLOAD},
     ["with its Outfit v2\\data where it looked for it"]),
    (r"""set /p "name=Enter your name: "
if "%name%"=="" (echo A name is required & pause & exit /b 1)
""" + COPY, None, []),
], ids=["powershell Expand-Archive", "powershell ZipFile", "7-Zip looked for on the PATH",
        "7-Zip run off the PATH", "robocopy from a folder zipped deeper",
        "xcopy from a folder zipped deeper", "a name typed in"])
def test_what_it_leans_on_is_played_the_way_its_authors_pc_has_it(tmp_path, script, files, tried):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + script, files=files)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == tried


@pytest.mark.parametrize("script, files, problem", [
    ("start https://www.nexusmods.com/finalfantasy13\n", None,
     "opens a web page, https://www.nexusmods.com/finalfantasy13, rather than installing anything"),
    ('start "" /wait "%~dp0Setup.exe"\n', None, "starts Setup.exe, which isn't in the download"),
    ('start "" "%~dp0readme.txt"\n', {"readme.txt": b"copy the files in"},
     "only opens readme.txt for you to read"),
    (r"""mkdir "%LOCALAPPDATA%\OutfitMod" 2>nul
copy /y "%~dp0op.win32.wmp" "%LOCALAPPDATA%\OutfitMod\" >nul
echo Done!
""", None, "copies its files to C:\\Users\\Player\\AppData\\Local\\OutfitMod rather than into the "
           "game"),
    (r""":again
if not exist "%~dp0ready.txt" (echo Waiting for ready.txt... & timeout /t 1 >nul & goto again)
""", None, "went round in a loop, because it needs ready.txt from its download, which isn't in "
           "what was dropped"),
    ("goto install\n", None, "has a mistake in it: it goes to :install, a label that isn't in it"),
    (r"""msiexec /i "%~dp0Mod.msi" /qn
if errorlevel 1 (echo Install failed & pause & exit /b 1)
""", None, "needs to run an MSI installer, and that isn't done here"),
    (r""""%~dp0tools\xdelta3.exe" -d -s "%game%\white_data\movie\us\op.win32.wmp" "%~dp0op.xdelta" "%TEMP%\op.wmp"
""", None, "runs tools\\xdelta3.exe, which isn't anywhere in the download"),
], ids=["a web page", "a setup that isn't there", "a readme", "AppData", "a loop waiting on a file",
        "a label that isn't there", "an MSI", "a tool the download lacks"])
def test_what_it_did_instead_of_installing_is_the_reason(tmp_path, script, files, problem):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + script + BYE, files=files)
    assert p.problem == problem
    assert p.detail and "Exiting" not in p.problem + p.detail


def test_what_the_play_said_for_itself_isnt_what_the_script_said(tmp_path):
    p = played(tmp_path, r"""@echo off
curl -L -o "%TEMP%\pack.zip" https://example.com/pack.zip
if errorlevel 1 (echo Download failed & pause & exit /b 1)
""")
    assert p.problem == "needs to download something, and that isn't done here"
    assert "not available here" not in p.detail and "Download failed" in p.detail


def test_a_script_that_trips_the_reading_back_still_gets_its_last_words_said(tmp_path,
                                                                             monkeypatch):
    def broken(*_):
        raise ValueError("a trace nothing here expected")

    monkeypatch.setattr(diagnose, "_diagnose", broken)
    monkeypatch.setattr(diagnose, "NET", True)
    p = played(tmp_path, "@echo off\necho Couldn't patch the movie\n" + BYE,
               files={"readme.txt": b"nothing"})
    assert p.problem == (diagnose.IDLE + ", and the last thing it said was \"Couldn't patch the "
                         "movie\"")


@pytest.mark.parametrize("script", [
    r"""for /l %%i in (1,1,1500) do if not exist "%game%\white_data\" echo missing
""" + COPY,
    r"""set n=0
:again
if exist "%~dp0ready.txt" goto go
set /a n+=1
if %n% lss 1200 goto again
:go
""" + COPY,
    r"""set n=0
:again
if exist "%TEMP%\done.txt" goto go
set /a n+=1
echo x>"%TEMP%\p%n%.txt"
if %n% geq 1100 echo y>"%TEMP%\done.txt"
goto again
:go
""" + COPY,
], ids=["a FOR asking the same thing of every item", "a retry that counts its tries",
        "a loop that writes a piece a turn"])
def test_a_loop_getting_somewhere_is_never_taken_for_a_wait(tmp_path, script):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + script)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_wait_for_what_never_comes_is_said_without_waiting_out_the_clock(tmp_path):
    import time

    began = time.monotonic()
    p = played(tmp_path, r"""@echo off
:again
if not exist "%~dp0ready.txt" (timeout /t 1 >nul & goto again)
""")
    assert p.problem == ("went round in a loop, because it needs ready.txt from its download, "
                         "which isn't in what was dropped")
    assert time.monotonic() - began < sandbox.GLANCE / 2


def test_an_optional_flag_read_off_percent_one_isnt_said_as_wanting_the_game_dragged_on(tmp_path):
    p = played(tmp_path, r"""@echo off
if /i "%~1"=="/silent" set quiet=1
echo Nothing to install for this version of the game.
""" + BYE)
    assert "dragged" not in p.problem, p.problem
    assert p.problem.startswith(diagnose.IDLE) or p.problem.startswith("said"), p.problem


def test_a_log_left_on_the_desktop_on_the_way_to_failing_isnt_what_it_was_for(tmp_path):
    p = played(tmp_path, r"""@echo off
echo started > "%USERPROFILE%\Desktop\outfit_install.txt"
if "%ok%"=="" (echo Install failed: couldn't read its settings & pause & exit /b 1)
""", files={"readme.txt": b"nothing"})
    assert p.problem == "said \"Install failed: couldn't read its settings\"", p.problem


def test_a_folder_with_ff13_in_its_name_isnt_the_game(tmp_path):
    """a mod library is somewhere the script keeps mods, and what it copies there isn't the game's"""
    p = played(tmp_path, r"""@echo off
set "lib=%USERPROFILE%\Documents\FF13 Mod Library"
if not exist "%lib%\" (echo Install the FF13 Mod Library tool first & pause & exit /b 1)
md "%lib%\Outfit" 2>nul
copy /y "%~dp0op.win32.wmp" "%lib%\Outfit\" > nul
""" + BYE)
    assert laid(p) == {} and p.tried == [], (p.tried, laid(p))
    # what's meant to be there is the script's to say, with where it looked behind it
    assert p.problem == "said \"Install the FF13 Mod Library tool first\""
    assert p.detail.startswith("It looks for C:\\Users\\Player\\Documents\\FF13 Mod Library, "
                               "which isn't there."), p.detail


def test_only_a_folder_named_the_way_the_games_own_is_taken_for_it(tmp_path):
    from patcher.mods import game_files

    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Probe", {"install.bat": "@echo off\n", "op.win32.wmp": PAYLOAD})
    with game_files.Source(folder) as source:
        stage = sandbox.Stage(game, game_files.read_game(game), source, source.members(),
                              "install.bat", "Probe", tmp_path / "stage")
        for elsewhere in (r"C:\Users\Player\Desktop\FINAL FANTASY XIII.url",
                          r"C:\Users\Player\Downloads\FFXIII Outfit v2.zip",
                          r"D:\Games\FINAL FANTASY XIII-2\alba_data\prog\win\bin\ffxiii2img.exe",
                          r"D:\Mods\FF13 Mods\white_data",
                          r"D:\Mods\FF13 Mods\white_data\movie\us\op.win32.wmp",
                          r"C:\Users\Player\Documents\FF13 Mod Library\white_data\movie\us",
                          r"C:\Users\Player\Documents\Stuff\white_data\movie\us\op.win32.wmp",
                          r"D:\Games\FINAL FANTASY XIII-2\white_data",
                          r"D:\Games\Anything\white_data",
                          r"D:\Games\Anything\white_data\movie\us"):
            assert stage.game_at(elsewhere) is None, elsewhere
        for path, base in ((r"D:\Games\FINAL FANTASY XIII\FFXiiiLauncher.exe",
                            r"D:\Games\FINAL FANTASY XIII"),
                           ("E:\\FFXIII", "E:\\FFXIII"),
                           (r"D:\Games\FINAL FANTASY XIII (Steam)\white_data",
                            r"D:\Games\FINAL FANTASY XIII (Steam)"),
                           (r"C:\Users\Player\Games\FINAL FANTASY XIII\white_data",
                            r"C:\Users\Player\Games\FINAL FANTASY XIII"),
                           (r"E:\Copy\white_data\movie\us\op.win32.wmp", r"E:\Copy")):
            assert stage.game_at(path) == base, path


def test_lightning_returns_spelled_out_with_a_13_is_its_folder(tmp_path):
    from patcher.mods import game_files
    from tests.helpers import make_fake_pe

    game = tmp_path / "steam" / "steamapps" / "common" / "LIGHTNING RETURNS FINAL FANTASY XIII"
    (game / "weiss_data" / "prog" / "win" / "bin").mkdir(parents=True)
    (game / "weiss_data" / "prog" / "win" / "bin" / "lrff13.exe").write_bytes(make_fake_pe())
    (game / "weiss_data" / "sys").mkdir(parents=True)
    folder = drop(tmp_path, "Probe", {"install.bat": "@echo off\n", "op.win32.wmp": PAYLOAD})
    with game_files.Source(folder) as source:
        stage = sandbox.Stage(game, game_files.read_game(game), source, source.members(),
                              "install.bat", "Probe", tmp_path / "stage")
        assert stage.game_at(r"D:\Games\Lightning Returns Final Fantasy 13") == \
            r"D:\Games\Lightning Returns Final Fantasy 13"
        assert stage.game_at(r"D:\Games\FINAL FANTASY XIII") is None


def test_a_find_with_nothing_to_find_says_nothing_about_windows(tmp_path):
    from patcher.mods.batch import Ran

    ran = Ran("find", "", 1, "builtin", "Microsoft Windows [Version 10.0.19045.5011]")
    assert diagnose._found_why(None, None, None, ran) is None


def test_a_registry_key_in_a_variable_that_never_got_set(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""reg query %SE_KEY% >nul 2>&1 || (echo FINAL FANTASY XIII isn't installed & pause & exit /b 1)
""" + COPY)
    assert p.problem == "reads the registry at a key it keeps in %SE_KEY%, which it never set"
    assert p.tried == []


def test_steams_own_keys_are_there_and_a_key_given_later_hides_none_of_them(tmp_path):
    p = played(tmp_path, r"""@echo off
for /f "tokens=2*" %%a in ('reg query "HKCU\Software\Valve\Steam" /v SteamPath 2^>nul') do set "steam=%%b"
if not defined steam (echo Steam isn't installed & pause & exit /b 1)
reg query "HKCU\Software\Valve\Steam\Apps\292120" /v Installed 2>nul | find "0x1" >nul || (echo FINAL FANTASY XIII isn't installed in Steam & pause & exit /b 1)
reg query "HKLM\SOFTWARE\Square Enix\FINAL FANTASY XIII" /v Language >nul 2>&1 || (echo Run the game once & pause & exit /b 1)
for /f "tokens=2*" %%a in ('reg query "HKCU\Software\Valve\Steam" /v SteamPath 2^>nul') do set "steam=%%b"
set "game=%steam%\steamapps\common\FINAL FANTASY XIII"
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the registry key it checks for, HKLM\\SOFTWARE\\Square Enix\\"
                       "FINAL FANTASY XIII"]


def test_a_key_nothing_says_the_meaning_of_is_never_made_up(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""reg query "HKCU\Software\SomeModManager" /v Path >nul 2>&1 || (echo Install SomeModManager first & pause & exit /b 1)
""" + COPY)
    assert p.tried == [] and laid(p) == {}
    assert p.problem.startswith("checks the registry for HKCU\\Software\\SomeModManager, Path")


def test_a_program_the_look_ahead_couldnt_see_still_gets_the_pc_it_wanted(tmp_path):
    """the dry play can't tell whether unpack.exe's work installs, so only the real one gives up"""
    p = played(tmp_path, r"""@echo off
"%~dp0unpack.exe"
set "game=C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII"
if not exist "%game%\white_data\prog\win\bin\ffxiiiimg.exe" (echo Game not found & pause & exit /b 1)
""" + COPY, setup=lambda folder: stub(folder, "unpack.exe", "print('unpacked')\n"))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the game at C:\\Program Files (x86)\\Square Enix\\FINAL FANTASY XIII "
                       "too"]


def test_a_failure_that_ends_on_press_any_key_isnt_a_tool_finishing(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patcher.exe" "%game%\white_data\movie\us\op.win32.wmp"
if errorlevel 1 (echo Patching failed & pause & exit /b 1)
echo Patch applied successfully!
""", setup=lambda folder: stub(folder, "patcher.exe", "import sys\n"
                               "print('ERROR: the game files are not the version this patch is "
                               "for.')\n"
                               "print('Nothing was patched.')\n"
                               "print('Press any key to exit...')\n"
                               "sys.stderr.write('Unhandled exception. System.InvalidOperation"
                               "Exception: Cannot read keys when either application does not "
                               "have a console or when console input has been redirected.\\n')\n"
                               "sys.exit(0xE0434352 & 0xFF)\n"))
    assert p.problem == "runs patcher.exe, which says the game isn't the version it was made for"
    assert "Cannot read keys" not in p.problem


def test_the_games_own_folder_zipped_a_folder_deeper_is_the_downloads(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""if not exist "%~dp0white_data\" (echo The white_data folder is missing, extract everything & pause & exit /b 1)
xcopy /e /i /y "%~dp0white_data" "%game%\white_data" > nul
""" + BYE, files={"Files/white_data/movie/us/op.win32.wmp": PAYLOAD})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with its Files\\white_data where it looked for it"]


@pytest.mark.parametrize("script, files", [
    (r"""copy /y "%~dp0pack.pak" "%TEMP%\pack.zip" > nul
"%ProgramFiles%\7-Zip\7z.exe" x "%TEMP%\pack.zip" -o"%TEMP%\outfit" -y
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\outfit\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", {"pack.pak": make_zip_bytes({"op.win32.wmp": PAYLOAD})}),
    (r""""%ProgramFiles%\7-Zip\7z.exe" x "%~dp0outer.zip" -o"%TEMP%\o" -y
"%ProgramFiles%\7-Zip\7z.exe" x "%TEMP%\o\inner.zip" -o"%TEMP%\i" -y
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\i\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", {"outer.zip": make_zip_bytes({"inner.zip": make_zip_bytes({"op.win32.wmp": PAYLOAD})})}),
    (r"""powershell -NoProfile -Command "Expand-Archive -Force '%~dp0outer.zip' '%TEMP%\o'"
powershell -NoProfile -Command "Expand-Archive -Force '%TEMP%\o\inner.zip' '%TEMP%\i'"
copy /y "%TEMP%\i\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", {"outer.zip": make_zip_bytes({"inner.zip": make_zip_bytes({"op.win32.wmp": PAYLOAD})})}),
], ids=["a zip it renamed", "a zip in a zip", "a zip in a zip through PowerShell"])
def test_a_good_zip_the_drawer_never_opened_is_unpacked_not_called_damaged(tmp_path, script,
                                                                          files):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + script, files=files)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_tools_words_carry_the_plays_paths_never_the_stages(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patcher.exe" "%game%\white_data\movie\us\op.win32.wmp"
""" + BYE, setup=lambda folder: stub(folder, "patcher.exe", "import sys\n"
                                     "print('Error: could not open ' + sys.argv[1])\n"
                                     "sys.exit(1)\n"))
    assert p.problem == ("runs patcher.exe, which failed saying \"Error: could not open "
                         f"{GAME}\\white_data\\movie\\us\\op.win32.wmp\", so nothing changed")
    assert str(tmp_path) not in p.problem + p.detail


def test_a_readme_opened_on_the_way_still_lets_it_have_the_game_where_it_looked(tmp_path):
    p = played(tmp_path, r"""@echo off
start "" "%~dp0readme.txt"
set "game=D:\Games\FINAL FANTASY XIII"
if not exist "%game%\white_data\prog\win\bin\ffxiiiimg.exe" goto nogame
""" + COPY + "goto :eof\n:nogame\n" + BYE,
               files={"op.win32.wmp": PAYLOAD, "readme.txt": b"Install the game to D:\\Games."})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_an_optional_copy_before_a_tool_that_failed_isnt_the_reason(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""copy /y "%~dp0extras.ini" "%game%\" >nul 2>&1
"%~dp0patcher.exe" "%game%\white_data\movie\us\op.win32.wmp"
""" + BYE, setup=lambda folder: stub(folder, "patcher.exe", "import sys\n"
                                     "print('Error: unsupported file version')\n"
                                     "sys.exit(2)\n"))
    assert p.problem.startswith("runs patcher.exe, which"), p.problem


def test_a_calls_status_is_its_own_never_what_ran_last_before_it(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""reg query "HKCU\Software\OutfitMod" /v Installed >nul 2>&1
if not errorlevel 1 echo Updating an earlier install...
call :verify || (echo Verification failed & pause & exit /b 1)
copy /y "%~dp0data\op.win32.wmp" "%game%\white_data\movie\us\" > nul
goto :eof
:verify
if exist "%~dp0data\op.win32.wmp" exit /b 0
exit /b 1
""", files={"readme.txt": b"nothing"})
    assert "registry" not in p.problem and p.tried == [], (p.problem, p.tried)


def test_a_variable_ending_in_a_digit_isnt_something_dragged_on(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""set "p1=%~dp0"
if not exist "%p1%op.win32.wmp" (echo Extract all files first & pause & exit)
""" + COPY, files={"Outfit v1.2/op.win32.wmp": PAYLOAD})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with its Outfit v1.2\\op.win32.wmp where it looked for it"]


def test_a_check_inside_a_block_is_said_by_its_own_line(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""if "%~1"=="" (
  echo Installing the outfit...
  if not exist "%~dp0data\op.win32.wmp" goto missing
  copy /y "%~dp0data\op.win32.wmp" "%game%\white_data\movie\us\" > nul
)
goto :eof
:missing
echo The data folder is missing
pause
""", files={"readme.txt": b"nothing"})
    assert p.problem == "needs data\\op.win32.wmp from its download, which isn't in what was dropped"
    assert 'Where it stopped: if not exist "%~dp0data\\op.win32.wmp" goto missing' in p.detail


def test_an_optional_argument_loop_isnt_the_reason_a_tool_after_it_failed(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r""":parse
if "%~1"=="" goto parsed
if /i "%~1"=="/silent" set silent=1
shift
goto parse
:parsed
"%~dp0patcher.exe" "%game%\white_data\movie\us\op.win32.wmp"
""" + BYE, setup=lambda folder: stub(folder, "patcher.exe", "import sys\n"
                                     "print('Error: unsupported file version')\n"
                                     "sys.exit(2)\n"))
    assert p.problem.startswith("runs patcher.exe, which"), p.problem
    assert p.tried == []


def test_run_from_the_games_folder_and_still_idle_isnt_said_as_made_to_be(tmp_path):
    p = played(tmp_path, r"""@echo off
if not exist "%~dp0FFXiiiLauncher.exe" (echo Put these files in the game folder first & pause & exit /b 1)
echo All set.
""", files={"readme.txt": b"nothing"})
    assert p.tried == ["from inside the game's folder"]
    assert not p.problem.startswith("is made to be run"), p.problem


def test_this_pcs_python_3_never_stands_in_for_the_python_2_a_path_asks_for(tmp_path,
                                                                          monkeypatch):
    python_here(monkeypatch)
    monkeypatch.setattr(sandbox, "host_version", lambda runtime: "3.12.6")
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""if not exist "C:\Python27\python.exe" (echo Install Python 2.7 to C:\Python27 first & pause & exit /b 1)
"C:\Python27\python.exe" "%~dp0install.py" "%game%"
""", files={"op.win32.wmp": PAYLOAD, "install.py": "print 'old'\n"})
    assert p.problem == ("looks for Python 2.7 at C:\\Python27\\python.exe, and it isn't installed "
                         "on this PC")
    assert p.tried == []


def test_a_script_that_copies_from_what_its_given_gets_the_downloads_folder(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""if "%~1"=="" (echo Drag your texture folder onto this file & pause & exit /b)
xcopy /e /i /y "%~1" "%game%\white_data\" > nul
echo Done
""", files={"Textures/white_data/movie/us/op.win32.wmp": PAYLOAD})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with its Textures\\white_data folder dragged onto it"]


def test_a_wait_that_logs_each_pass_is_still_a_wait(tmp_path):
    """
    a line on the end of a log each time round is no step forward, and the
    log is never kept whole again each time
    """
    import time

    began = time.monotonic()
    p = played(tmp_path, r"""@echo off
:again
echo still waiting>>"%TEMP%\wait.log"
if not exist "%~dp0ready.txt" goto again
""")
    assert p.problem == ("went round in a loop, because it needs ready.txt from its download, "
                         "which isn't in what was dropped")
    assert time.monotonic() - began < sandbox.GLANCE / 2


@pytest.mark.parametrize("script", [
    r"""set n=0
:again
set /a n+=1
if not exist "%~dp0ready.txt" goto again
""",
    r"""for %%f in (ready.txt) do call :wait %%f
goto :eof
:wait
if not exist "%~dp0%1" goto wait
""",
], ids=["counting its tries without end", "in a label a FOR calls"])
def test_a_wait_that_counts_or_hides_in_a_call_is_still_a_wait(tmp_path, script):
    import time

    began = time.monotonic()
    p = played(tmp_path, "@echo off\n" + script)
    assert p.problem == ("went round in a loop, because it needs ready.txt from its download, "
                         "which isn't in what was dropped")
    assert time.monotonic() - began < sandbox.GLANCE / 2


def test_a_real_play_going_round_its_own_lines_has_a_clock(tmp_path, monkeypatch):
    """the look-ahead can't see its program, so the loop after it is only met played for real"""
    import time

    monkeypatch.setattr(sandbox, "LOOPING", 2)
    monkeypatch.setattr(sandbox, "GLANCE", 2)
    began = time.monotonic()
    p = played(tmp_path, r"""@echo off
"%~dp0prepare.exe"
:again
set /a n+=1
echo %n%>"%TEMP%\count.txt"
goto again
""", setup=lambda folder: stub(folder, "prepare.exe", "print('ready')\n"))
    assert p.problem.startswith("stopped: it went round its own lines for over"), p.problem
    assert time.monotonic() - began < 30


def test_a_check_after_a_call_inside_a_block_is_said_by_its_own_line(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""(
  call :banner
  if not exist "%~dp0data\op.win32.wmp" goto missing
)
copy /y "%~dp0data\op.win32.wmp" "%game%\white_data\movie\us\" > nul
goto :eof
:banner
echo Outfit installer
goto :eof
:missing
echo The data folder is missing
pause
""", files={"readme.txt": b"nothing"})
    assert 'Where it stopped: if not exist "%~dp0data\\op.win32.wmp" goto missing' in p.detail
    assert "goto :eof" not in p.detail


UNPACK = f'@echo off\nset "game={GAME}"\n' + r"""copy /y "%~dp0pack.pak" "%TEMP%\pack.zip" > nul
"%ProgramFiles%\7-Zip\7z.exe" x "%TEMP%\pack.zip" -o"%TEMP%\outfit" -y{switch}
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\outfit\op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""


def _marked(data: bytes, method=None, crc=False) -> bytes:
    """a one-member zip said to be packed another way, or with a CRC its bytes don't have"""
    data = bytearray(data)
    central = data.rindex(b"PK\x01\x02")
    if method is not None:
        struct.pack_into("<H", data, 8, method)
        struct.pack_into("<H", data, central + 10, method)
    if crc:
        wrong = struct.unpack_from("<I", data, central + 16)[0] ^ 0xFFFFFFFF
        struct.pack_into("<I", data, 14, wrong)
        struct.pack_into("<I", data, central + 16, wrong)
    return bytes(data)


def test_a_locked_zip_unpacks_with_the_password_its_bat_gives(tmp_path):
    p = played(tmp_path, UNPACK.replace("{switch}", " -pSECRET"),
               files={"pack.pak": make_locked_zip_bytes({"op.win32.wmp": PAYLOAD}, b"SECRET")})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


LOCKED = make_locked_zip_bytes({"op.win32.wmp": PAYLOAD}, b"SECRET")


@pytest.mark.parametrize("switch, pack, problem, fix", [
    ("", LOCKED, "unpacks pack.zip, which is locked with a password it doesn't give",
     "The mod's page or readme gives the password."),
    (" -pGUESS", LOCKED, "unpacks pack.zip with a password that doesn't open it",
     "The password in its .bat doesn't fit this copy of pack.zip."),
    ("", _marked(make_zip_bytes({"op.win32.wmp": PAYLOAD}), method=9),
     "unpacks pack.zip, which takes 7-Zip to open, and this PC hasn't got it",
     "Install 7-Zip from 7-zip.org"),
    ("", _marked(make_zip_bytes({"op.win32.wmp": PAYLOAD}), crc=True),
     "unpacks pack.zip, which looks damaged or cut short", "Download the mod again"),
], ids=["locked, no password", "locked, the wrong password", "Deflate64", "a bad CRC"])
def test_a_zip_nothing_here_can_unpack_says_what_it_takes(tmp_path, switch, pack, problem, fix):
    p = played(tmp_path, UNPACK.replace("{switch}", switch), files={"pack.pak": pack})
    assert p.problem == problem
    assert p.detail.startswith(fix), p.detail


SEVEN_ZA_ANY = r"""import pathlib, sys
args = sys.argv[1:]
out = pathlib.Path(next((a[2:] for a in args if a.startswith("-o")), "."))
secret = next((a[2:] for a in args if a.startswith("-p")), "")
out.mkdir(parents=True, exist_ok=True)
(out / "op.win32.wmp").write_bytes(("seven " + secret).encode() * 100)
"""


@pytest.mark.parametrize("switch, pack", [
    (" -pSECRET", LOCKED),
    ("", _marked(make_zip_bytes({"op.win32.wmp": PAYLOAD}), method=98)),
], ids=["a password", "PPMd"])
def test_a_zip_the_drops_own_7za_reads_goes_to_it(tmp_path, switch, pack):
    """the 7za.exe a mod carries reads what zipfile can't, with the password it's told"""
    p = played(tmp_path, UNPACK.replace("{switch}", switch).replace(
        '"%ProgramFiles%\\7-Zip\\7z.exe"', '"%~dp07za.exe"'), files={"pack.pak": pack},
        setup=lambda folder: stub(folder, "7za.exe", SEVEN_ZA_ANY))
    assert laid(p) == {MOVIE: ("seven " + switch[3:]).encode() * 100}, (p.problem, p.detail)


def test_this_pcs_own_7zip_unpacks_an_archive_nothing_else_here_can(tmp_path, monkeypatch):
    host = stub(tmp_path / "host", "7z.exe", SEVEN_ZA_ANY)
    monkeypatch.setattr(sandbox, "host_archiver", lambda: str(host))
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%ProgramFiles%\7-Zip\7z.exe" x "%~dp0outfit.7z" -o"%TEMP%\outfit" -y
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\outfit\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", files={"outfit.7z": b"7z\xbc\xaf\x27\x1c" + b"\0" * 64})
    assert laid(p) == {MOVIE: b"seven " * 100}, (p.problem, p.detail)


def test_a_zip_member_is_inflated_into_the_store_once_and_never_held_whole(tmp_path,
                                                                          monkeypatch):
    big = bytes(3 << 20)
    inflated = []
    real = sandbox.Stage.keep_from

    def keep_from(stage, read):
        ref = real(stage, read)
        inflated.append(ref)
        return ref

    def keep(stage, data):
        assert len(data) < sandbox.INLINE, "a member held whole"
        return sandbox.Inline(data)

    monkeypatch.setattr(sandbox.Stage, "keep_from", keep_from)
    monkeypatch.setattr(sandbox.Stage, "keep", keep)
    p = played(tmp_path, UNPACK.replace("{switch}", ""),
               files={"pack.pak": make_zip_bytes({"op.win32.wmp": big})})
    assert laid(p) == {MOVIE: big}, (p.problem, p.detail)
    assert len(inflated) == 1 and isinstance(inflated[0], sandbox.Kept)


def test_a_zip_bigger_than_the_drive_has_room_for_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr("patcher.download.free_space", lambda path: 1 << 20)
    p = played(tmp_path, UNPACK.replace("{switch}", ""),
               files={"pack.pak": make_zip_bytes({"op.win32.wmp": bytes(3 << 20)})})
    assert p.problem == "unpacks pack.zip, which needs more free space than the game's drive has"


def test_an_archive_it_unpacks_that_isnt_there_is_said_by_where_it_looked(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%ProgramFiles%\7-Zip\7z.exe" x "%~dp0outfit.7z" -o"%TEMP%\outfit" -y
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\outfit\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", files={"readme.txt": b"nothing"})
    assert p.problem == "needs outfit.7z from its download, which isn't in what was dropped"
    assert "Extraction failed" in p.detail


def test_a_backup_of_the_dragged_on_games_file_doesnt_make_it_a_texture_folder(tmp_path):
    """copying out of what it's given into its own folder is keeping the game's file"""
    p = played(tmp_path, r"""@echo off
if "%~1"=="" (echo Drag your FINAL FANTASY XIII folder onto this file & pause & exit /b)
set "game=%~1"
if not exist "%~dp0backup\" md "%~dp0backup"
copy /y "%game%\white_data\movie\us\op.win32.wmp" "%~dp0backup\" > nul
copy /y "%~dp0op.win32.wmp" "%game%\white_data\movie\us\" > nul
echo Done
""")
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the game's folder dragged onto it"]


@pytest.mark.parametrize("branch", [
    r'"%~dp0msgbox.exe" "FINAL FANTASY XIII was not found in its folder"',
    r'"%~dp0cecho.exe" "FINAL FANTASY XIII was not found in its folder"',
], ids=["a message box", "a colour echo"])
def test_a_program_on_a_checks_own_way_off_is_that_check_giving_up(tmp_path, branch):
    p = played(tmp_path, r"""@echo off
set "game=C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII"
if not exist "%game%\white_data\prog\win\bin\ffxiiiimg.exe" (
  """ + branch + r"""
  exit /b 1
)
""" + COPY, setup=lambda folder: (stub(folder, "msgbox.exe", "import sys\nsys.exit(0)\n"),
                                  stub(folder, "cecho.exe", "import sys\nprint(sys.argv[1])\n")))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the game at C:\\Program Files (x86)\\Square Enix\\FINAL FANTASY XIII "
                       "too"]


def test_a_registry_check_with_a_message_box_on_its_way_off(tmp_path):
    p = played(tmp_path, r"""@echo off
for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Square Enix\FINAL FANTASY XIII" /v InstallLocation 2^>nul') do set "game=%%b"
if not defined game (
  "%~dp0msgbox.exe" "FINAL FANTASY XIII isn't installed"
  exit /b 1
)
""" + COPY, setup=lambda folder: stub(folder, "msgbox.exe", "import sys\nsys.exit(0)\n"))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


@pytest.mark.parametrize("said", ["No errors found.", "1 file patched, 0 failed.",
                                  "Errors: 0", "Do not move the game folder.",
                                  "Nothing else to do.", "This won't touch your save files.",
                                  "If the game doesn't start, verify its files in Steam.",
                                  "Your original file is not deleted, it's in Backup."])
def test_a_tool_that_did_fine_and_then_wanted_a_key_finished(tmp_path, said):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patcher.exe" "%~dp0op.win32.wmp" "%TEMP%\patched.wmp"
if errorlevel 1 (echo Patching failed & pause & exit /b 1)
copy /y "%TEMP%\patched.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
""", setup=lambda folder: stub(folder, "patcher.exe", "import sys, shutil\n"
                               "shutil.copyfile(sys.argv[1], sys.argv[2])\n"
                               "print('Patched op.win32.wmp successfully.')\n"
                               f"print({said!r})\n"
                               "print('Press any key to exit...')\n"
                               "sys.stderr.write('Unhandled exception. System.InvalidOperation"
                               "Exception: Cannot read keys when either application does not "
                               "have a console or when console input has been redirected.\\n')\n"
                               "sys.exit(82)\n"))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_what_a_line_says_went_wrong_is_never_a_tally_or_an_instruction():
    for line in ("No errors found.", "1 file patched, 0 failed.", "Errors: 0",
                 "Do not move the game folder.", "Please don't close this window.", "All done.",
                 "Nothing went wrong.", "Nothing else to do.", "This won't touch your saves.",
                 "If the game doesn't start, verify its files.", "Your file is not deleted.",
                 "Not required for the Steam version.", "You don't need to run this again.",
                 "Your saves will not be touched.", "This tool doesn't need admin rights.",
                 "File is not compressed, skipping...", "already patched, nothing to do."):
        assert not diagnose.went_wrong(line), line
    for line in ("Nothing was patched.", "ERROR: file not found", "Patching failed",
                 "Couldn't open the archive", "That version is not supported",
                 "Checksum does not match.", "Game version not detected.", "No game selected",
                 "That's not a valid archive", "The game isn't installed", "Access is denied.",
                 "won't run without .NET", "File doesn't exist",
                 "This is not a FINAL FANTASY XIII movie file.", "Your game is not up to date.",
                 "Patch not applicable to this file.", "Nothing happened.",
                 "No .wmp files in that folder.", "That doesn't look like FINAL FANTASY XIII.",
                 "Steam DRM not removed.", "Finished with errors.", "Done. 0 files patched.",
                 "Patched 0 of 1 files.", "Nothing else to patch: 2 files failed.",
                 "No need to retry, the patch failed"):
        assert diagnose.went_wrong(line), line


def test_a_version_only_a_changed_pc_reaches_can_be_picked(tmp_path):
    """
    the look-ahead can't see unpack.exe, so the menu's only reached once the
    game's been mounted where it looks
    """
    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Outfits", {"install.bat": r"""@echo off
"%~dp0unpack.exe"
set "game=C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII"
if not exist "%game%\white_data\prog\win\bin\ffxiiiimg.exe" (echo Game not found & pause & exit /b 1)
echo 1. Install the Lightning outfit
echo 2. Install the Snow outfit
echo 3. Exit
set /p pick=Choose:
if "%pick%"=="1" set src=lightning
if "%pick%"=="2" set src=snow
if "%pick%"=="3" goto :eof
copy /y "%~dp0%src%\op.win32.wmp" "%game%\white_data\movie\us\" > nul
echo Done
""", "lightning/op.win32.wmp": b"lightning " * 300, "snow/op.win32.wmp": b"snow " * 300})
    stub(folder, "unpack.exe", "print('unpacked')\n")
    offered = play(game, folder)
    assert sorted(offered.versions) == ["The Lightning outfit", "The Snow outfit"], offered.versions
    snow = play(game, folder, version="The Snow outfit")
    assert laid(snow) == {MOVIE: b"snow " * 300}, (snow.problem, snow.detail)


@pytest.mark.parametrize("check, use, tried", [
    ('if not exist "%~dp1ffxiiiimg.exe"', r'"%~dp1..\..\..\movie\us\"',
     "with the game's ffxiiiimg.exe dragged onto it"),
    ('if "%~1%"==""', r'"%~1%\white_data\movie\us\"', "with the game's folder dragged onto it"),
    ('if "%1%"==""', r'"%1%\white_data\movie\us\"',
     "with the game's folder dragged onto it from a folder with no spaces in its path"),
], ids=["a name straight after %~dp1", "%~1%", "%1% in quotes of its own"])
def test_every_way_a_script_reads_what_was_dragged_onto_it(tmp_path, check, use, tried):
    p = played(tmp_path, "@echo off\n" + check + r""" (echo Drag the game onto this file & pause & exit /b 1)
copy /y "%~dp0op.win32.wmp" """ + use + " > nul\necho Done\n")
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == [tried]


def test_what_was_dragged_on_is_read_the_way_cmd_reads_a_line():
    for text in ('"%~dp1ffxiiiimg.exe"', '"%1%"', "%1", "%~1", '"%~n1_patched%~x1"', "%~$PATH:1"):
        assert diagnose.ARGUED.search(text), text
    for text in ("%p1%", "%%1", "%foo%1", "%~dp0", "%*", "100%"):
        assert not diagnose.ARGUED.search(text), text


def test_a_calls_errorlevel_read_after_it_isnt_the_program_before_it(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""reg query "HKCU\Software\OutfitMod" /v Installed >nul 2>&1
if not errorlevel 1 echo Updating an earlier install...
call :verify
if %errorlevel% neq 0 (echo Verification failed & pause & exit /b 1)
copy /y "%~dp0data\op.win32.wmp" "%game%\white_data\movie\us\" > nul
goto :eof
:verify
if exist "%~dp0data\op.win32.wmp" exit /b 0
exit /b 1
""", files={"readme.txt": b"nothing"})
    assert p.problem == "said \"Verification failed\""
    assert p.tried == []
    assert p.detail.startswith("It needs data\\op.win32.wmp from its download"), p.detail


def test_an_optional_extra_it_never_had_doesnt_hide_the_payload_zipped_deeper(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""if exist "%~dp0extras.ini" copy /y "%~dp0extras.ini" "%game%\" > nul
if exist "%~dp0data\op.win32.wmp" copy /y "%~dp0data\op.win32.wmp" "%game%\white_data\movie\us\" > nul
echo Done
pause
""", files={"Outfit/data/op.win32.wmp": PAYLOAD})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with its Outfit\\data\\op.win32.wmp where it looked for it"]


def test_a_mod_library_laid_out_like_the_game_isnt_the_game(tmp_path):
    p = played(tmp_path, r"""@echo off
set "lib=%USERPROFILE%\Documents\FF13 Mod Library"
if not exist "%lib%\white_data\movie\us\" (echo Set up the FF13 Mod Library tool first & pause & exit /b 1)
copy /y "%~dp0op.win32.wmp" "%lib%\white_data\movie\us\op.win32.wmp" > nul
echo Added the outfit to your mod library.
""")
    assert laid(p) == {}
    assert p.tried == []
    assert p.problem == "said \"Set up the FF13 Mod Library tool first\"", p.problem


@pytest.mark.parametrize("folder", ["FINAL FANTASY XIII (Steam)", "Final Fantasy XIII Complete",
                                    "FFXIII PC"])
def test_the_game_in_a_folder_named_for_it_with_a_word_more(tmp_path, folder):
    p = played(tmp_path, f'@echo off\nset "game=D:\\Games\\{folder}"\n' + r"""if not exist "%game%\white_data\" (echo Game not found & pause & exit /b 1)
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == [f"with the game at D:\\Games\\{folder} too"]


def test_a_mod_tools_registry_key_is_never_given_the_games_folder(tmp_path):
    p = played(tmp_path, r"""@echo off
for /f "tokens=2*" %%a in ('reg query "HKCU\Software\FFXIII Mod Library" /v Path 2^>nul') do set "lib=%%b"
if not defined lib (echo Install the FFXIII Mod Library tool first & pause & exit /b 1)
md "%lib%\Outfit" 2>nul
copy /y "%~dp0op.win32.wmp" "%lib%\Outfit\" > nul
""")
    assert laid(p) == {}
    assert p.tried == []


def test_python_2s_registry_key_never_gets_this_pcs_python_3(tmp_path, monkeypatch):
    python_here(monkeypatch)
    monkeypatch.setattr(sandbox, "host_version", lambda runtime: "3.12.6")
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\Python\PythonCore\2.7\InstallPath" /ve 2^>nul') do set "py=%%b"
if not defined py (echo Python 2.7 is required & pause & exit /b 1)
"%py%python.exe" "%~dp0install.py" "%game%"
""", files={"install.py": 'print "Installing"\n', "op.win32.wmp": PAYLOAD})
    assert p.tried == []
    assert p.problem == ("checks the registry for Python 2.7's install record (HKLM\\SOFTWARE\\"
                         "Python\\PythonCore\\2.7\\InstallPath), which isn't there"), p.problem
    assert p.detail.startswith("Install Python 2.7 from python.org"), p.detail


@pytest.mark.parametrize("query", [
    r"""reg query "HKCU\Software\Valve\Steam" /v SteamExe""",
    r"""reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 292120" /v UninstallString""",
], ids=["SteamExe", "Steam's uninstall entry"])
def test_what_steam_writes_on_any_pc_with_it_is_there(tmp_path, query):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + query + r""" >nul 2>&1 || (echo Steam isn't installed & pause & exit /b 1)
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == []


@pytest.mark.parametrize("pack, problem", [
    (LOCKED, "unpacks pack.zip with PowerShell, which can't open how it's packed"),
    (_marked(make_zip_bytes({"op.win32.wmp": PAYLOAD}), crc=True),
     "unpacks pack.zip, which looks damaged or cut short"),
], ids=["locked", "a bad CRC"])
def test_a_zip_powershell_cant_unpack_says_why(tmp_path, pack, problem):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""copy /y "%~dp0pack.pak" "%TEMP%\pack.zip" > nul
powershell -NoProfile -Command "Expand-Archive -Force '%TEMP%\pack.zip' '%TEMP%\pack'"
copy /y "%TEMP%\pack\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", files={"pack.pak": pack})
    assert p.problem == problem


def test_python_or_7zip_installed_since_is_found_on_the_next_drop(tmp_path, monkeypatch):
    """the reason says to install it and drop the mod again, which has to work without a restart"""
    monkeypatch.setattr(sandbox, "_HOSTS", {})
    monkeypatch.setattr(sandbox, "_MISSED", {})
    monkeypatch.setattr(sandbox, "_answers", lambda path: True)
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "nowhere"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nowhere"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nowhere"))
    installed = {}
    monkeypatch.setattr(sandbox.shutil, "which", lambda name, path=None: installed.get(name))
    assert HOST_RUNTIME("python") is None and HOST_ARCHIVER() is None
    installed.update(python=str(tmp_path / "python.exe"), **{"7z": str(tmp_path / "7z.exe")})
    # a moment later is still the same look
    assert HOST_RUNTIME("python") is None
    monkeypatch.setattr(sandbox, "MISSED_FOR", -1)
    assert HOST_RUNTIME("python") == str(tmp_path / "python.exe")
    assert HOST_ARCHIVER() == str(tmp_path / "7z.exe")


def test_python_2_this_pc_has_is_lent_where_its_registry_key_says(tmp_path, monkeypatch):
    """a Python 2.7 installed beside this PC's Python 3, found where its installer puts it"""
    monkeypatch.setattr(sandbox, "ON_WINDOWS", True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    stub(tmp_path / "local" / "Programs" / "Python" / "Python27", "python.exe",
         "import runpy, sys\nsys.argv = sys.argv[1:]\nrunpy.run_path(sys.argv[0], run_name='__main__')\n")
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\Python\PythonCore\2.7\InstallPath" /ve 2^>nul') do set "py=%%b"
if not defined py (echo Python 2.7 is required & pause & exit /b 1)
"%py%python.exe" "%~dp0install.py" "%game%"
if errorlevel 1 (echo Install failed & pause & exit /b 1)
""", files={"op.win32.wmp": PAYLOAD, "install.py": INSTALL_PY})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the registry key it checks for, HKLM\\SOFTWARE\\Python\\PythonCore"
                       "\\2.7\\InstallPath"]


@pytest.mark.parametrize("script", [
    r"""set n=0
:next
set /a n+=1
if not exist "%~dp0tex\t%n%.dds" goto done
if not exist "%game%\white_data\tex" md "%game%\white_data\tex"
copy /y "%~dp0tex\t%n%.dds" "%game%\white_data\tex\" > nul
goto next
:done
""",
    r"""for %%f in ("%~dp0tex\*.dds") do call :put "%%~f"
goto :eof
:put
if not exist "%game%\white_data\tex" md "%game%\white_data\tex"
copy /y "%~1" "%game%\white_data\tex\" > nul
goto :eof
""",
], ids=["a goto loop", "a label a FOR calls"])
def test_a_long_loop_putting_a_new_file_in_each_time_is_never_cut_short(tmp_path, monkeypatch,
                                                                       script):
    """past SPUN_ANYWAY the same check is a wait only when nothing new came of the pass"""
    from patcher.mods import batch

    monkeypatch.setattr(batch, "SPUN_ANYWAY", 150)
    files = {f"tex/t{n}.dds": f"texture {n} ".encode() * 10 for n in range(1, 201)}
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + script, files=files)
    assert len(laid(p)) == 200, (p.problem, p.detail, p.notes)


def test_a_wait_that_only_logs_is_still_caught_past_spun_anyway(tmp_path, monkeypatch):
    from patcher.mods import batch

    monkeypatch.setattr(batch, "SPUN_ANYWAY", 150)
    p = played(tmp_path, r"""@echo off
:again
echo waiting>>"%TEMP%\wait.log"
set /a tries+=1
if not exist "%~dp0ready.txt" goto again
""")
    assert p.problem == ("went round in a loop, because it needs ready.txt from its download, "
                         "which isn't in what was dropped")


def test_a_check_pass_a_for_calls_once_an_item_isnt_a_wait(tmp_path, monkeypatch):
    from patcher.mods import batch

    monkeypatch.setattr(batch, "SPIN", 50)
    files = {f"data/tex{n:03d}.dds": b"x" for n in range(120)}
    files["filelist.txt"] = "".join(f"tex{n:03d}.dds\r\n" for n in range(120)).encode()
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""for /f "usebackq delims=" %%f in ("%~dp0filelist.txt") do call :check "%%f"
if defined missing (echo Some files are missing & pause & exit /b 1)
""" + COPY + r"""goto :eof
:check
if not exist "%game%\white_data\sys" (echo Game folder not found & pause & exit 1)
if not exist "%~dp0data\%~1" set "missing=1"
goto :eof
""", files={**files, "op.win32.wmp": PAYLOAD})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_real_play_a_loop_guard_stopped_puts_nothing_in(tmp_path, monkeypatch):
    """what it copied before the guard stopped it is half an install, never one"""
    monkeypatch.setattr(sandbox, "LOOPING", 2)
    monkeypatch.setattr(sandbox, "GLANCE", 2)
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0prepare.exe"
""" + COPY + r""":again
set /a n+=1
echo %n%>"%TEMP%\count.txt"
goto again
""", setup=lambda folder: stub(folder, "prepare.exe", "print('ready')\n"))
    assert laid(p) == {}
    assert p.problem.startswith("stopped: it went round its own lines"), p.problem


def test_a_real_plays_clock_leaves_its_programs_copies_out(tmp_path, monkeypatch):
    """laying a big archive out for a tool and reading back what it left is the tool's time"""
    import time

    real = sandbox.World._lay

    def slow(world, path):
        if path.lower().endswith("white_imgu.win32.bin"):
            time.sleep(2.5)
        return real(world, path)

    monkeypatch.setattr(sandbox.World, "_lay", slow)
    monkeypatch.setattr(sandbox, "LOOPING", 2)
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0repack.exe" "%game%\white_data\sys\white_imgu.win32.bin"
for /l %%n in (1,1,700) do set /a done+=1
""" + COPY, setup=lambda folder: stub(folder, "repack.exe", "print('repacked')\n"))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


@pytest.mark.parametrize("key, title", [
    (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 292140",
     "FINAL FANTASY XIII-2"),
    (r"HKLM\SOFTWARE\WOW6432Node\Square Enix\LIGHTNING RETURNS FINAL FANTASY XIII",
     "LIGHTNING RETURNS: FINAL FANTASY XIII"),
], ids=["XIII-2's Steam entry", "LR's Square Enix key"])
def test_another_of_the_three_games_key_says_whose_mod_it_is(tmp_path, key, title):
    p = played(tmp_path, r"""@echo off
for /f "tokens=2*" %%a in ('reg query """ + f'"{key}"' + r""" /v InstallLocation 2^>nul') do set "game=%%b"
if not defined game (echo The game isn't installed & pause & exit /b 1)
copy /y "%~dp0d3d9.dll" "%game%\" > nul
""", files={"d3d9.dll": make_import_pe()})
    assert laid(p) == {}
    assert p.tried == []
    assert p.problem.endswith(f"so it's a mod for {title}"), p.problem
    assert f"Drop it in {title}'s Mods drawer instead." in p.detail


def test_a_compressed_archive_in_a_zipped_drop_is_copied_out_once(tmp_path, monkeypatch):
    """its head is read where it sits, and the one copy an unpack needs is kept for the rest"""
    from patcher.mods import game_files

    copies = []
    real = game_files.Source._copy_out

    def counted(source, member, *args, **kwargs):
        copies.append(member)
        return real(source, member, *args, **kwargs)

    monkeypatch.setattr(game_files.Source, "_copy_out", counted)
    import io
    import zipfile

    pack = make_zip_bytes({"op.win32.wmp": PAYLOAD + bytes(2 << 20)})
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Outfit/install.bat", (UNPACK.replace("{switch}", "")).replace("\n", "\r\n"))
        z.writestr("Outfit/pack.pak", pack)
    zipped = tmp_path / "Outfit.zip"
    zipped.write_bytes(outer.getvalue())
    game = xiii(tmp_path / "steam", voices="u")
    p = play(game, zipped)
    assert laid(p) == {MOVIE: PAYLOAD + bytes(2 << 20)}, (p.problem, p.detail)
    assert copies.count("Outfit/pack.pak") <= 1, copies


@pytest.mark.parametrize("bat", [
    UNPACK.replace("{switch}", " -pSECRET").replace('copy /y "%~dp0pack.pak" "%TEMP%\\pack.zip" > nul\n',
                                                   "").replace('"%TEMP%\\pack.zip"',
                                                               '"%~dp0pack.zip"'),
], ids=["the installed 7-Zip told the password"])
def test_a_locked_zip_beside_the_bat_unpacks_with_its_password(tmp_path, bat):
    """listed fine and never readable, so one to unpack rather than the drop's own files"""
    p = played(tmp_path, bat, files={"pack.zip": LOCKED})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_check_whose_find_runs_the_work_isnt_that_check_giving_up(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""if not exist "%~dp0extras\extras.ini" goto noextras
echo Extras found
:noextras
if exist "%game%\white_data\movie\us\op.win32.wmp" (
  "%~dp0wmppatch.exe" "%game%\white_data\movie\us\op.win32.wmp"
  goto done
)
echo The game wasn't found & pause & exit /b 1
:done
echo Finished.
""", setup=lambda folder: stub(folder, "wmppatch.exe", "import sys\n"
                               "print('ERROR: unsupported movie version, nothing patched')\n"
                               "sys.exit(3)\n"))
    assert p.problem.startswith("runs wmppatch.exe, which failed saying \"ERROR: unsupported "
                                "movie version"), p.problem


def test_a_zip_joined_out_of_parts_unpacks(tmp_path):
    whole = make_zip_bytes({"op.win32.wmp": PAYLOAD + bytes(2 << 20)})
    half = len(whole) // 2
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""copy /b "%~dp0pack.001" + "%~dp0pack.002" "%TEMP%\pack.zip" > nul
"%ProgramFiles%\7-Zip\7z.exe" x "%TEMP%\pack.zip" -o"%TEMP%\outfit" -y
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\outfit\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", files={"pack.001": whole[:half], "pack.002": whole[half:]})
    assert laid(p) == {MOVIE: PAYLOAD + bytes(2 << 20)}, (p.problem, p.detail)


def test_a_zip_whose_local_header_is_broken_is_damaged(tmp_path):
    pack = bytearray(make_zip_bytes({"op.win32.wmp": PAYLOAD}))
    # the local header's name, which zipfile holds up against the listing's
    pack[30:31] = b"X"
    p = played(tmp_path, UNPACK.replace("{switch}", ""), files={"pack.pak": bytes(pack)})
    assert p.problem == "unpacks pack.zip, which looks damaged or cut short", p.problem


def test_a_registry_setting_it_skips_never_hides_an_archive_it_couldnt_unpack(tmp_path):
    p = played(tmp_path, UNPACK.replace("{switch}", "").replace(
        'copy /y "%~dp0pack.pak"', 'reg add "HKCU\\Software\\OutfitMod" /v Installed /t REG_DWORD '
        '/d 1 /f > nul\ncopy /y "%~dp0pack.pak"'), files={"pack.pak": LOCKED})
    assert p.problem == "unpacks pack.zip, which is locked with a password it doesn't give"


def test_every_archive_a_wildcard_names_is_unpacked(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""copy /y "%~dp0pack.pak" "%TEMP%\pack.zip" > nul
"%ProgramFiles%\7-Zip\7z.exe" x "%TEMP%\*.zip" -o"%TEMP%\outfit" -y
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\outfit\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", files={"pack.pak": make_zip_bytes({"op.win32.wmp": PAYLOAD})})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_this_pcs_7zip_is_told_a_password_with_a_space_in_it(tmp_path, monkeypatch):
    host = stub(tmp_path / "host", "7z.exe", SEVEN_ZA_ANY)
    monkeypatch.setattr(sandbox, "host_archiver", lambda: str(host))
    p = played(tmp_path, UNPACK.replace("{switch}", ' "-pmy secret"'),
               files={"pack.pak": _marked(make_zip_bytes({"op.win32.wmp": PAYLOAD}), method=9)})
    assert laid(p) == {MOVIE: b"seven my secret" * 100}, (p.problem, p.detail)


def test_a_log_written_to_thousands_of_times_stays_a_short_list(tmp_path):
    from patcher.mods import game_files

    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Probe", {"install.bat": "@echo off\n", "op.win32.wmp": PAYLOAD})
    with game_files.Source(folder) as source:
        stage = sandbox.Stage(game, game_files.read_game(game), source, source.members(),
                              "install.bat", "Probe", tmp_path / "stage")
        world = sandbox.World(stage, sandbox.Answers(stage), dry=True)
        log = sandbox.TEMP + "\\install.log"
        world.write(log, b"started\r\n")
        for n in range(3000):
            world.write(log, f"line {n}\r\n".encode(), append=True)
        assert len(world.ref(log).parts) <= sandbox.APPENDS + 1
        assert world.read(log) == b"started\r\n" + b"".join(f"line {n}\r\n".encode()
                                                          for n in range(3000))


def test_a_menu_only_a_changed_pc_reaches_under_the_one_picked_from_is_offered_next(tmp_path):
    game = xiii(tmp_path / "steam", voices="u")
    files = {"install.bat": r"""@echo off
echo 1. Lightning outfit
echo 2. Snow outfit
set /p pick=Choose:
if "%pick%"=="1" set src=lightning
if "%pick%"=="2" set src=snow
"%~dp0unpack.exe"
set "game=C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII"
if not exist "%game%\white_data\prog\win\bin\ffxiiiimg.exe" (echo Game not found & pause & exit /b 1)
echo 1. Red cape
echo 2. Blue cape
set /p cape=Choose:
if "%cape%"=="1" set src=%src%_red
if "%cape%"=="2" set src=%src%_blue
copy /y "%~dp0%src%\op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""}
    for each in ("lightning_red", "snow_red", "lightning_blue", "snow_blue"):
        files[f"{each}/op.win32.wmp"] = (each + " ").encode() * 300
    folder = drop(tmp_path, "Outfits", files)
    stub(folder, "unpack.exe", "print('unpacked')\n")
    assert sorted(play(game, folder).versions) == ["Lightning outfit", "Snow outfit"]
    deeper = play(game, folder, version="Lightning outfit").versions
    assert sorted(deeper) == ["Lightning outfit, Blue cape", "Lightning outfit, Red cape"]
    p = play(game, folder, version="Lightning outfit, Blue cape")
    assert laid(p) == {MOVIE: b"lightning_blue " * 300}, (p.problem, p.detail)


SHOWN = "import sys\nprint('shown:', ' '.join(sys.argv[1:]))\n"
FINDS = r"""@echo off
set "game="
if exist "C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII\FFXiiiLauncher.exe" set "game=C:\Program Files (x86)\Square Enix\FINAL FANTASY XIII"
"""


@pytest.mark.parametrize("off", [
    '(\n  "%~dp0msgbox.exe" "FINAL FANTASY XIII wasn\'t found"\n  goto end\n)',
    'call :fail "FINAL FANTASY XIII wasn\'t found"',
], ids=["a message box then a goto to the end", "a call to a label that says so and exits"])
def test_a_compare_that_found_its_variable_empty_is_a_miss(tmp_path, off):
    """`if "%game%"==""` is `if not defined game`, so what it runs on its way off is it giving up"""
    p = played(tmp_path, FINDS + f'if "%game%"=="" {off}\n' + COPY + "goto :eof\n:fail\n"
               '"%~dp0msgbox.exe" %1\nexit 1\n:end\n',
               setup=lambda folder: stub(folder, "msgbox.exe", SHOWN))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the game at C:\\Program Files (x86)\\Square Enix\\FINAL FANTASY XIII "
                       "too"]


def test_a_failed_programs_errorlevel_compare_calling_an_error_label_is_where_it_stopped(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patch.exe" "%game%\white_data\movie\us\op.win32.wmp"
if %errorlevel% neq 0 call :error "patch.exe failed"
goto :eof
:error
"%~dp0msgbox.exe" %1
exit /b 1
""", setup=lambda folder: (stub(folder, "msgbox.exe", SHOWN), stub(
        folder, "patch.exe", "import sys\nprint('ERROR: unsupported movie version')\nsys.exit(3)\n")))
    assert p.problem.startswith('runs patch.exe, which failed saying "ERROR: unsupported movie '
                                'version"'), p.problem
    assert 'Where it stopped: if %errorlevel% neq 0 call :error "patch.exe failed"' in p.detail


def test_a_compare_whose_branch_runs_the_work_isnt_it_giving_up(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\nset "mode=full"\n' + r"""if not exist "%~dp0extras\extras.ini" goto noextras
echo Extras found
:noextras
if "%mode%"=="full" (
  "%~dp0wmppatch.exe" "%game%\white_data\movie\us\op.win32.wmp"
  goto done
)
echo Nothing picked & pause & exit /b 1
:done
echo Finished.
""", setup=lambda folder: stub(folder, "wmppatch.exe", "import sys\n"
                               "print('ERROR: unsupported movie version, nothing patched')\n"
                               "sys.exit(3)\n"))
    assert p.problem.startswith("runs wmppatch.exe, which failed saying \"ERROR: unsupported "
                                "movie version"), p.problem


def test_a_bat_that_only_writes_the_registry_says_so(tmp_path):
    p = played(tmp_path, r"""@echo off
echo Switching the game's voices to Japanese...
reg add "HKCU\Software\Square Enix\FINAL FANTASY XIII" /v Language /t REG_SZ /d ja /f
echo Done.
pause
""")
    assert laid(p) == {}
    assert p.problem == "needs to change a setting in the registry, and that isn't done here"


def test_a_registry_value_it_wrote_reads_back_in_the_same_play(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""reg add "HKCU\Software\OutfitMod" /v GamePath /t REG_SZ /d "%game%" /f > nul
reg add "HKCU\Software\OutfitMod" /v Runs /t REG_DWORD /d 16 /f > nul
for /f "tokens=2*" %%a in ('reg query "HKCU\Software\OutfitMod" /v GamePath 2^>nul') do set "where=%%b"
for /f "tokens=3" %%a in ('reg query "HKCU\Software\OutfitMod" /v Runs 2^>nul') do set "runs=%%a"
if not defined where (echo Couldn't save the game's path & pause & exit /b 1)
if not "%runs%"=="0x10" (echo Couldn't count & pause & exit /b 1)
copy /y "%~dp0op.win32.wmp" "%where%\white_data\movie\us\" > nul
""")
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_bat_a_fomod_picked_unpacks_its_renamed_zip(tmp_path):
    from patcher.mods import fomod, game_files

    game = xiii(tmp_path / "steam", voices="u")
    folder = drop(tmp_path, "Outfit", {
        "Main/install.bat": UNPACK.replace("{switch}", ""),
        "Main/pack.pak": make_zip_bytes({"op.win32.wmp": PAYLOAD}),
        "fomod/ModuleConfig.xml": b"<config><moduleName>Outfit</moduleName><requiredInstallFiles>"
                                  b'<folder source="Main" destination="" /></requiredInstallFiles>'
                                  b"</config>"})
    with game_files.Source(folder) as source:
        config = fomod.read(source, source.members())
        picked, _ = fomod.walk(config, {})
        chosen = fomod.Chosen(source, fomod.installed(config, picked, source.members()))
        p = sandbox.play(game, chosen, chosen.members(), "Outfit")
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


@pytest.mark.parametrize("pack", [
    b"", b"<!DOCTYPE html><html><body>Too many requests</body></html>",
], ids=["empty", "a web page saved as the zip"])
def test_an_archive_that_starts_like_no_archive_is_damaged(tmp_path, pack):
    p = played(tmp_path, UNPACK.replace("{switch}", ""), files={"pack.pak": pack})
    assert p.problem == "unpacks pack.zip, which looks damaged or cut short", p.problem


def test_the_drawer_never_puts_another_games_mod_in_off_its_files(tmp_path, monkeypatch):
    """its installer checks XIII-2's Steam entry, so its .asi going into XIII on its own is wrong"""
    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "XIII2Fix", {"install.bat": r"""@echo off
for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 292140" /v InstallLocation 2^>nul') do set "game=%%b"
if not defined game (echo FINAL FANTASY XIII-2 wasn't found & pause & exit /b 1)
copy /y "%~dp0XIII2Fix.asi" "%game%\" > nul
""", "XIII2Fix.asi": make_import_pe(["kernel32.dll"]), "XIII2Fix.ini": b"[XIII-2]\nfps=60\n"})
    done = bridge.install_mod("ff13", str(folder))
    assert not done["ok"]
    assert done["error"].endswith("so it's a mod for FINAL FANTASY XIII-2."), done
    assert not [p for p in game.rglob("XIII2Fix*")]


@pytest.mark.parametrize("app, data, said", [
    ("292140", "alba_data", "so it's a mod for FINAL FANTASY XIII-2"), ("292120", "white_data", ""),
], ids=["XIII-2's on XIII", "XIII's own"])
def test_steams_own_app_key_piped_to_find_says_whose_mod_it_is(tmp_path, app, data, said):
    p = played(tmp_path, r"""@echo off
reg query "HKCU\Software\Valve\Steam\Apps\{app}" /v Installed 2>nul | find "0x1" >nul
if errorlevel 1 (echo The game isn't installed. & pause & exit /b 1)
set "game={GAME}"
xcopy /e /i /y "%~dp0{data}" "%game%\{data}\" > nul
""".replace("{app}", app).replace("{data}", data).replace("{GAME}", GAME),
               files={f"{data}/movie/us/op.win32.wmp": PAYLOAD})
    if said:
        assert laid(p) == {} and p.problem.endswith(said), p.problem
    else:
        assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_rar_goes_to_the_drops_unrar_never_its_7za(tmp_path):
    """7za has no RAR codec, so with both in the download the UnRAR the script ran is the one"""
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""set "unrar=%ProgramFiles%\WinRAR\UnRAR.exe"
if not exist "%unrar%" set "unrar=%~dp0UnRAR.exe"
"%unrar%" x -o+ "%~dp0data.rar" "%TEMP%\data\"
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\data\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", files={"data.rar": b"Rar!\x1a\x07\x00" + bytes(200)}, setup=lambda folder: (
        # "%TEMP%\data\" ends in \", which the C runtime's argv reads as an
        # escaped quote. real UnRAR parses its own command line and doesn't care
        stub(folder, "UnRAR.exe", "import pathlib, sys\nout = pathlib.Path(sys.argv[-1].rstrip(chr(34)))\n"
                                  "out.mkdir(parents=True, exist_ok=True)\n"
                                  "(out / 'op.win32.wmp').write_bytes(b'unrar ' * 300)\n"),
        stub(folder / "tools", "7za.exe", "import sys\nprint('ERROR: Can not open the file as "
                                          "archive')\nsys.exit(2)\n")))
    assert laid(p) == {MOVIE: b"unrar " * 300}, (p.problem, p.detail)


def test_a_tool_that_says_it_didnt_then_waits_for_a_key_failed(tmp_path):
    """a bare no is a failure unless it's a note, so the empty file it left never goes in"""
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patcher.exe" "%~dp0op.win32.wmp" "%TEMP%\patched.wmp"
if errorlevel 1 (echo Patching failed & pause & exit /b 1)
copy /y "%TEMP%\patched.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
""", setup=lambda folder: stub(folder, "patcher.exe", "import sys\n"
                               "open(sys.argv[2], 'wb').close()\n"
                               "print('This is not a FINAL FANTASY XIII movie file.')\n"
                               "print('Done.')\n"
                               "sys.stderr.write('Unhandled exception. System.InvalidOperation"
                               "Exception: Cannot read keys when either application does not "
                               "have a console or when console input has been redirected.\\n')\n"
                               "sys.exit(82)\n"))
    assert laid(p) == {}
    assert p.problem.startswith('runs patcher.exe, which failed saying "This is not a FINAL '
                                'FANTASY XIII movie file."'), p.problem


def test_a_key_it_deleted_is_gone_when_it_checks(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""reg add "HKCU\Software\OutfitMod" /v Size /t REG_SZ /d big /f >nul
reg delete "HKCU\Software\OutfitMod" /f >nul 2>&1
reg query "HKCU\Software\OutfitMod" >nul 2>&1
if not errorlevel 1 (echo Couldn't reset the settings, run this as administrator & pause & exit /b 1)
reg add "HKCU\Software\OutfitMod\Sub" /v Fresh /t REG_SZ /d yes /f >nul
reg query "HKCU\Software\OutfitMod" >nul 2>&1
if errorlevel 1 (echo Couldn't write the settings & pause & exit /b 1)
reg delete "HKCU\Software\Valve\Steam" /v SteamPath /f >nul
reg query "HKCU\Software\Valve\Steam" /v SteamPath >nul 2>&1
if not errorlevel 1 (echo Steam's path is still there & pause & exit /b 1)
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


@pytest.mark.parametrize("before", [
    r'reg query "HKCU\Software\OutfitMod" /v Version >nul 2>&1',
    r'reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 292140" '
    r'/v InstallLocation >nul 2>&1',
], ids=["its own key", "another game's key, for an optional part"])
def test_a_find_in_a_file_is_never_blamed_on_a_query_before_it(tmp_path, before):
    game = xiii(tmp_path / "steam", voices="u")
    (game / "version.txt").write_text("FINAL FANTASY XIII 1.0.0.1\n")
    folder = drop(tmp_path, "Outfit", {"install.bat": f'@echo off\nset "game={GAME}"\n'
                                       + before + "\n" + r"""find "1.0.0.2" "%game%\version.txt" >nul
if errorlevel 1 (echo This mod needs the game at version 1.0.0.2 & pause & exit /b 1)
""" + COPY, "op.win32.wmp": PAYLOAD})
    p = play(game, folder)
    assert p.problem == 'checks the game\'s version.txt for "1.0.0.2", which isn\'t in it', \
        p.problem
    assert p.tried == []


WMPPATCH = "import sys\nprint('ERROR: unsupported movie version, nothing patched')\nsys.exit(3)\n"
INSTALL = r"""
:install
"%~dp0wmppatch.exe" "%game%\white_data\movie\us\op.win32.wmp"
exit /b
"""


@pytest.mark.parametrize("body", [
    r"""reg query "HKCU\Software\OutfitMod" /v Installed >nul 2>&1
if %errorlevel% neq 0 call :install
echo All done.
goto :eof
""" + INSTALL,
    r"""find /i "outfit" "%game%\white_data\movie\us\op.win32.wmp" >nul
if %errorlevel%==1 (
  "%~dp0wmppatch.exe" "%game%\white_data\movie\us\op.win32.wmp"
  goto done
)
echo Already installed.
:done
echo All done.
""",
    r"""set "installed="
for /f "tokens=3" %%a in ('reg query "HKCU\Software\OutfitMod" /v Installed 2^>nul') do set "installed=%%a"
if "%installed%"=="" call :install
echo All done.
goto :eof
""" + INSTALL,
], ids=["a failed query's errorlevel", "a find that missed its marker", "an empty variable"])
def test_an_install_unless_its_in_check_is_its_work_not_giving_up(tmp_path, body):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + body,
               setup=lambda folder: stub(folder, "wmppatch.exe", WMPPATCH))
    assert p.problem.startswith('runs wmppatch.exe, which failed saying "ERROR: unsupported '
                                'movie version'), p.problem


def test_a_4gb_patcher_that_also_sets_a_compatibility_flag_is_still_the_4gb_patch(
        tmp_path, monkeypatch):
    from tests.helpers import make_fake_pe

    game, bridge = drawer(tmp_path, monkeypatch)
    folder = drop(tmp_path, "FF13 4GB Fix", {"install.bat": r"""@echo off
set "game=C:\Program Files (x86)\Steam\steamapps\common\FFXIII"
"%~dp0LargeAddressAware.exe" "%game%\white_data\prog\win\bin\ffxiiiimg.exe"
reg add "HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers" /v "%game%\white_data\prog\win\bin\ffxiiiimg.exe" /t REG_SZ /d "~ RUNASADMIN" /f
echo Done.
pause
""", "LargeAddressAware.exe": make_fake_pe()})
    done = bridge.install_mod("ff13", str(folder))
    assert not done["ok"] and "is a 4GB patcher, LargeAddressAware.exe. Apply's 4GB patch " \
        "does the same" in done["error"], done


def _host_7zip(monkeypatch):
    import shutil

    real = shutil.which("7z") or shutil.which("7za")
    if real is None:
        pytest.skip("no 7-Zip on this PC to lend the play")
    monkeypatch.setattr(sandbox, "host_archiver", lambda: real)


SEVEN_X = f'@echo off\nset "game={GAME}"\n' + r"""
"%ProgramFiles%\7-Zip\7z.exe" x "%~dp0{archive}" -o"%TEMP%\outfit" -y
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "%TEMP%\outfit\op.win32.wmp" "%game%\white_data\movie\us\" > nul
"""


def test_a_raw_lzma_goes_to_this_pcs_7zip_not_called_damaged(tmp_path, monkeypatch):
    import lzma

    _host_7zip(monkeypatch)
    p = played(tmp_path, SEVEN_X.replace("{archive}", "op.win32.wmp.lzma"), files={
        "op.win32.wmp.lzma": lzma.compress(PAYLOAD, format=lzma.FORMAT_ALONE)})
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_zip_split_in_parts_goes_to_this_pcs_7zip_with_its_parts(tmp_path, monkeypatch):
    from tests.helpers import make_split_zip

    _host_7zip(monkeypatch)
    p = played(tmp_path, SEVEN_X.replace("{archive}", "pack.zip"),
               files=make_split_zip({"op.win32.wmp": PAYLOAD}))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_zip_split_in_parts_without_7zip_says_7zip_not_a_crash(tmp_path):
    from tests.helpers import make_split_zip

    p = played(tmp_path, SEVEN_X.replace("{archive}", "pack.zip"),
               files=make_split_zip({"op.win32.wmp": PAYLOAD}))
    assert p.problem == "unpacks pack.zip, which takes 7-Zip to open, and this PC hasn't got it", \
        p.problem


def test_a_hash_of_a_game_file_is_never_a_damaged_download(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%ProgramFiles%\7-Zip\7z.exe" h -scrcSHA256 "%game%\white_data\prog\win\bin\ffxiiiimg.exe" | find /i "0F9A47C2" >nul
if errorlevel 1 (echo Your ffxiiiimg.exe isn't the one this mod is for & pause & exit /b 1)
""" + COPY)
    assert "damaged" not in p.problem and "Download the mod again" not in p.detail, p.problem


def test_a_tool_that_says_done_then_waits_on_a_key_it_never_asked_for_finished(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patcher.exe" "%~dp0op.win32.wmp" "%TEMP%\patched.wmp"
if errorlevel 1 (echo Patching failed & pause & exit /b 1)
copy /y "%TEMP%\patched.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
""", setup=lambda folder: stub(folder, "patcher.exe", "import sys, shutil\n"
                               "shutil.copyfile(sys.argv[1], sys.argv[2])\n"
                               "print('Patched op.win32.wmp.')\n"
                               "print('Done.')\n"
                               "sys.stderr.write('Unhandled exception. System.InvalidOperation"
                               "Exception: Cannot read keys when either application does not "
                               "have a console or when console input has been redirected.\\n')\n"
                               "sys.exit(82)\n"))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


@pytest.mark.parametrize("hashes", [
    '"%ProgramFiles%\\7-Zip\\7z.exe" h -scrcSHA256 "{file}"',
    'certutil -hashfile "{file}" SHA256',
], ids=["7-Zip's h", "certutil -hashfile"])
@pytest.mark.parametrize("rel", ["white_data/prog/win/bin/ffxiiiimg.exe",
                                 "white_data/sys/filelistu.win32.bin"], ids=["exe", "filelist"])
@pytest.mark.parametrize("matches", [True, False], ids=["this game's", "another release's"])
def test_a_version_check_by_a_files_hash_reads_the_games_own(tmp_path, hashes, rel, matches):
    import hashlib

    game = xiii(tmp_path / "steam", voices="u")
    known = hashlib.sha256((game / rel).read_bytes()).hexdigest() if matches else "0f9a47c2" * 8
    folder = drop(tmp_path, "Outfit", {"install.bat": f'@echo off\nset "game={GAME}"\n'
                                       + hashes.format(file="%game%\\" + rel.replace("/", "\\"))
                                       + f' | find /i "{known}" >nul\n' + r"""
if errorlevel 1 (echo That file isn't the one this mod is for & pause & exit /b 1)
""" + COPY, "op.win32.wmp": PAYLOAD})
    p = play(game, folder)
    if matches:
        assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    else:
        assert laid(p) == {}
        assert p.problem == f"checks {rel.rsplit('/', 1)[-1]} against a hash of the one it was " \
                            "made for, which this one doesn't match", p.problem


KEYLESS_END = ("sys.stderr.write('Unhandled exception. System.InvalidOperationException: Cannot "
               "read keys when either application does not have a console or when console "
               "input has been redirected.\\n')\nsys.exit(82)\n")
PATCHES = f'@echo off\nset "game={GAME}"\n' + r"""
"%~dp0patcher.exe" "%~dp0op.win32.wmp" "%TEMP%\patched.wmp"
if errorlevel 1 (echo Patching failed & pause & exit /b 1)
copy /y "%TEMP%\patched.wmp" "%game%\white_data\movie\us\op.win32.wmp" > nul
"""


def _patcher(said: list, out: str = "copy") -> str:
    made = {"copy": "import shutil\nshutil.copyfile(sys.argv[1], sys.argv[2])\n",
            "empty": "open(sys.argv[2], 'wb').close()\n"}[out]
    return "import sys\n" + made + "".join(f"print({line!r})\n" for line in said) + KEYLESS_END


@pytest.mark.parametrize("said", [
    ["Finished with errors."], ["Done. 0 files patched."], ["Patched 0 of 1 files."],
    ["ERROR: header checksum mismatch", "Cleaning up...", "Removing temp files...",
     "Closing handles...", "Done."],
], ids=["errors", "0 files", "0 of 1", "an error five lines up"])
def test_a_tool_that_failed_before_its_done_never_has_its_empty_file_put_in(tmp_path, said):
    p = played(tmp_path, PATCHES, setup=lambda folder: stub(folder, "patcher.exe",
                                                             _patcher(said, "empty")))
    assert laid(p) == {}
    assert p.problem.startswith("runs patcher.exe, which failed saying"), p.problem


@pytest.mark.parametrize("said", [
    ["Patched op.win32.wmp.", "Your saves will not be touched.", "Done.", "Press any key..."],
    ["Patched op.win32.wmp.", "Your original will not be deleted.", "Press any key..."],
    ["This tool doesn't need admin rights.", "Patched op.win32.wmp.", "Press any key..."],
    ["File is not compressed, skipping...", "Patched op.win32.wmp.", "Press any key..."],
    ["Patched op.win32.wmp.", "Press any key to exit (this window will not close by itself)"],
    ["Patched op.win32.wmp.", "Done."],
], ids=["saves untouched", "not deleted", "no admin needed", "not compressed",
        "window won't close", "a bare done"])
def test_a_tool_that_did_fine_then_wanted_a_key_with_a_note_on_the_way_finished(tmp_path, said):
    p = played(tmp_path, PATCHES, setup=lambda folder: stub(folder, "patcher.exe",
                                                             _patcher(said)))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


@pytest.mark.parametrize("asked", ["Backup complete. Apply the patch now? (y/n)",
                                   "Verification successful. Proceed with patching? [Y/N]"])
def test_a_yes_or_no_after_a_done_word_is_still_answered(tmp_path, asked):
    tool = ("import sys, shutil\nprint('Backing up op.win32.wmp...')\n"
            f"print({asked!r})\nanswer = sys.stdin.readline() if not sys.stdin.isatty() else ''\n"
            "if not answer.strip():\n" + "    " + KEYLESS_END.replace("\n", "\n    ").rstrip()
            + "\nshutil.copyfile(sys.argv[1], sys.argv[2])\nprint('Patched.')\n")
    p = played(tmp_path, PATCHES, setup=lambda folder: stub(folder, "patcher.exe", tool))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


@pytest.mark.parametrize("extra", [
    r'copy /y "%~dp0op.win32.wmp" "%USERPROFILE%\Desktop\outfit-failed.log" >nul',
    r'"%~dp0msgbox.exe" "FINAL FANTASY XIII was not found, see" "%~dp0readme.txt"',
    r'"%~dp0msgbox.exe" "Edit the path at the top of" "%~f0"',
    r'"%~dp0msgbox.exe" Game not found. Fix the path in install.bat',
], ids=["a log kept on the desktop", "shown the readme", "shown the script",
        "a bare install.bat in the message"])
def test_what_a_check_runs_on_its_way_off_is_never_the_install(tmp_path, extra):
    p = played(tmp_path, FINDS + 'if "%game%"=="" (\n  ' + extra + '\n  "%~dp0msgbox.exe" '
               '"FINAL FANTASY XIII wasn\'t found"\n  goto end\n)\n' + COPY + ":end\n",
               files={"op.win32.wmp": PAYLOAD, "readme.txt": b"read me"},
               setup=lambda folder: stub(folder, "msgbox.exe", SHOWN))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)
    assert p.tried == ["with the game at C:\\Program Files (x86)\\Square Enix\\FINAL FANTASY XIII "
                       "too"]


@pytest.mark.parametrize("say", ["echo Checking the registry...",
                                 r'reg query "HKCU\Software\OutfitMod" >nul 2>&1'],
                         ids=["an echo naming the registry", "another query before"])
def test_a_query_piped_into_find_in_a_block_is_still_read(tmp_path, say):
    p = played(tmp_path, "@echo off\n(\n  " + say + "\n" + r"""  reg query "HKLM\SOFTWARE\WOW6432Node\Square Enix\FINAL FANTASY XIII" /v InstallLocation 2>nul | find "InstallLocation" >nul
  if errorlevel 1 (echo FINAL FANTASY XIII isn't installed & pause & exit /b 1)
)
for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Square Enix\FINAL FANTASY XIII" /v InstallLocation 2^>nul') do set "game=%%b"
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_the_drops_own_7za_hashes_for_real(tmp_path):
    """the stand-in's h is a model; the download's own 7za is the real thing, -ba and all"""
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
for /f "tokens=1" %%h in ('"%~dp07za.exe" h -ba -scrcCRC64 "%game%\white_data\prog\win\bin\ffxiiiimg.exe"') do set "hash=%%h"
if not "%hash%"=="0123456789ABCDEF" (echo Wrong game version & pause & exit /b 1)
""" + COPY, setup=lambda folder: stub(folder, "7za.exe",
                                      "print('0123456789ABCDEF            11  ffxiiiimg.exe')\n"))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_the_stand_in_7zips_bare_hash_reads_in_a_for(tmp_path):
    import hashlib

    game = xiii(tmp_path / "steam", voices="u")
    exe = game / "white_data" / "prog" / "win" / "bin" / "ffxiiiimg.exe"
    known = hashlib.sha256(exe.read_bytes()).hexdigest()
    folder = drop(tmp_path, "Outfit", {"install.bat": f'@echo off\nset "game={GAME}"\n' + r"""
for /f "tokens=1" %%h in ('""%ProgramFiles%\7-Zip\7z.exe" h -ba -scrcSHA256 "%game%\white_data\prog\win\bin\ffxiiiimg.exe""') do set "hash=%%h"
""" + f'if not "%hash%"=="{known}" (echo Wrong game version & pause & exit /b 1)\n' + COPY,
                                       "op.win32.wmp": PAYLOAD})
    p = play(game, folder)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_cleared_key_keeps_its_subkeys_and_a_remade_one_is_empty(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""reg add "HKCU\Software\OutfitMod\Sub" /v Keep /t REG_SZ /d yes /f >nul
reg add "HKCU\Software\OutfitMod" /v Old /t REG_SZ /d yes /f >nul
reg delete "HKCU\Software\OutfitMod" /va /f >nul
reg query "HKCU\Software\OutfitMod\Sub" /v Keep >nul 2>&1
if errorlevel 1 (echo The subkey went too & pause & exit /b 1)
reg query "HKCU\Software\OutfitMod" /v Old >nul 2>&1
if not errorlevel 1 (echo The value stayed & pause & exit /b 1)
reg delete "HKCU\Software\Valve\Steam" /f >nul
reg add "HKCU\Software\Valve\Steam" /v Fresh /t REG_SZ /d yes /f >nul
reg query "HKCU\Software\Valve\Steam" /v SteamPath >nul 2>&1
if not errorlevel 1 (echo Steam's old values came back & pause & exit /b 1)
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


@pytest.mark.parametrize("pack", [bytes(64 << 10), "<html><body>Rate limited</body></html>"
                                  .encode("utf-16")], ids=["all zeros", "a UTF-16 web page"])
def test_a_file_of_nothing_or_of_wide_text_is_a_damaged_archive(tmp_path, pack):
    p = played(tmp_path, UNPACK.replace("{switch}", ""), files={"pack.pak": pack})
    assert p.problem == "unpacks pack.zip, which looks damaged or cut short", p.problem


def test_7zips_own_volumes_go_in_after_a_cd_elsewhere(tmp_path, monkeypatch):
    _host_7zip(monkeypatch)
    whole = make_zip_bytes({"op.win32.wmp": PAYLOAD + bytes(200_000)})
    half = len(whole) // 2
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""cd /d "%TEMP%"
"%ProgramFiles%\7-Zip\7z.exe" x "%~dp0pack.zip.001" -o"outfit" -y
if errorlevel 1 (echo Extraction failed & pause & exit /b 1)
copy /y "outfit\op.win32.wmp" "%game%\white_data\movie\us\" > nul
""", files={"pack.zip.001": whole[:half], "pack.zip.002": whole[half:]})
    assert laid(p) == {MOVIE: PAYLOAD + bytes(200_000)}, (p.problem, p.detail)


def test_a_command_wrapped_in_one_more_pair_of_quotes_gets_its_arguments_whole(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""for /f "delims=" %%a in ('""%~dp0args.exe" "%game%\white_data""') do set "got=%%a"
if not "%got%"=="%game%\white_data" (echo It got "%got%" & pause & exit /b 1)
""" + COPY, setup=lambda folder: stub(folder, "args.exe",
                                      "import sys\nprint('|'.join(sys.argv[1:]))\n"))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


@pytest.mark.parametrize("said, why", [
    (["Patching op.win32.wmp...", "Nothing was patched. Press any key to exit."],
     "Nothing was patched"),
    (["Backup complete.", "Patching failed! Press any key to close."], "Patching failed!"),
    (["Writing... done", "ERROR: write failed, press any key to exit"], "ERROR: write failed"),
], ids=["nothing patched", "failed after a backup", "an error after a done"])
def test_a_failure_on_the_closing_line_itself_is_one(tmp_path, said, why):
    p = played(tmp_path, PATCHES, setup=lambda folder: stub(folder, "patcher.exe",
                                                             _patcher(said, "empty")))
    assert laid(p) == {}
    assert p.problem == f'runs patcher.exe, which failed saying "{why}", so nothing changed', \
        p.problem


def test_a_bare_done_over_an_empty_output_never_goes_in(tmp_path):
    """the files laid for it have new times of their own, so it's what it changed that counts"""
    p = played(tmp_path, PATCHES, setup=lambda folder: stub(folder, "patcher.exe",
                                                             _patcher(["Done."], "empty")))
    assert laid(p) == {}


def test_a_bare_done_after_a_yes_it_was_given_has_finished(tmp_path):
    tool = ("import sys, shutil\nprint('Apply the patch? (y/n)')\n"
            "answer = sys.stdin.readline()\nif not answer.strip():\n    "
            + KEYLESS_END.replace("\n", "\n    ").rstrip()
            + "\nshutil.copyfile(sys.argv[1], sys.argv[2])\nprint('Done.')\n" + KEYLESS_END)
    p = played(tmp_path, PATCHES, setup=lambda folder: stub(folder, "patcher.exe", tool))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_an_instruction_with_its_reason_after_a_comma_is_a_note(tmp_path):
    p = played(tmp_path, PATCHES, setup=lambda folder: stub(folder, "patcher.exe", _patcher([
        "Patched op.win32.wmp.", "Do not delete the Backup folder, or you won't be able to "
        "uninstall.", "Press any key to exit..."])))
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_clearing_a_key_that_isnt_there_never_makes_it(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""reg delete "HKCU\Software\OutfitMod" /va /f >nul 2>&1
reg query "HKCU\Software\OutfitMod" >nul 2>&1
if not errorlevel 1 (echo Already installed & pause & exit /b 0)
""" + COPY)
    assert laid(p) == {MOVIE: PAYLOAD}, (p.problem, p.detail)


def test_a_hash_of_a_file_that_isnt_there_says_so(tmp_path):
    p = played(tmp_path, f'@echo off\nset "game={GAME}"\n' + r"""
"%ProgramFiles%\7-Zip\7z.exe" h "%game%\nope.exe" | find /i "0F9A47C2" >nul
if errorlevel 1 (echo Wrong version & pause & exit /b 1)
""" + COPY)
    assert laid(p) == {} and "Unsupported" not in p.problem + p.detail, p.problem
