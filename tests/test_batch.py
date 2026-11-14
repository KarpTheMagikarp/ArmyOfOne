"""
The .bat interpreter against what cmd.exe does. Most expectations were read
off cmd itself (wine's, where wine agrees with Windows), the rest off the
rules cmd is known to follow where wine doesn't: & after FOR's DO stays in
the loop, echo( prints what follows, and wildcard copy targets exist.
"""

import pytest

from patcher.mods import batch
from tests.batch_world import Mem, play


def lines(text, **kw):
    return play(text, **kw)[0]


def test_percent_expansion_the_way_cmd_reads_a_line():
    out = lines("@echo off\nset x=1 \n" + r"""echo [%x%]
echo %undefined%x
echo %undef:~1,2%z
set v=abcdef
echo %v:~1,3% %v:~-2% %v:c=X% %v:*c=Y%
echo 50%
set "p=C:\some path\"
echo [%p:~0,-1%]
""")
    assert out == ["[1 ]", "x", "~1,2z", "bcd ef abXdef Ydef", "50", "[C:\\some path]"]


def test_a_block_is_expanded_once_and_delayed_expansion_sees_the_change():
    out = lines(r"""@echo off
setlocal enabledelayedexpansion
set n=0
for %%f in (a b c) do (
  set /a n=!n!+1
  echo in loop %n% !n! %%f
)
echo after !n! %n%
""")
    assert out == ["in loop 0 1 a", "in loop 0 2 b", "in loop 0 3 c", "after 3 3"]


def test_call_shift_exit_b_and_the_arguments():
    out, _, run, code = play(r"""@echo off
call :sub one "two words" three
echo back el=%errorlevel%
goto :eof
:sub
echo sub %1 [%~2] %3 all=%*
shift
echo shifted %1
exit /b 7
""")
    assert out == ['sub one [two words] three all=one "two words" three',
                   'shifted "two words"', "back el=7"]
    assert code == 7


def test_if_and_for_take_the_rest_of_their_line_and_all():
    out = lines(r"""@echo off
if 1==2 echo a & echo b
if 1==1 echo c & echo d
for %%x in (1 2) do echo x%%x & echo y
if 1==2 (echo e) else echo f & echo g
if 1==1 echo a else echo b
""")
    assert out == ["c ", "d", "x1 ", "y", "x2 ", "y", "f ", "g", "a else echo b"]


def test_for_variants():
    out = lines(r"""@echo off
for /l %%i in (1,2,6) do echo i=%%i
mkdir d1\d2\d3
for /R /d %%a in (*) do echo dir %%~nxa
echo line1> log.txt
echo line2>> log.txt
for /f "usebackq tokens=1,2 delims= " %%a in ("log.txt") do echo got %%a [%%b]
for /f "tokens=2,* delims=," %%a in ("x,y,z,w") do echo t2=%%a rest=%%b
for /f "eol=; tokens=*" %%i in (log.txt) do echo whole=%%i
for %%a in ("C:\x y\file.name.txt") do echo n=%%~na x=%%~xa p=%%~pa d=%%~da
""")
    assert out == ["i=1", "i=3", "i=5", "dir d1", "dir d2", "dir d3",
                   "got line1 []", "got line2 []", "t2=y rest=z,w",
                   "whole=line1", "whole=line2",
                   "n=file.name x=.txt p=\\x y\\ d=C:"]


def test_for_over_files_and_recursively():
    out = lines(r"""@echo off
for %%f in (art\*.dds) do echo f=%%f
for /R art %%f in (*.dds) do echo r=%%~nxf
""", files={r"C:\mod\art\b.dds": "1", r"C:\mod\art\a.dds": "1", r"C:\mod\art\x.txt": "1",
            r"C:\mod\art\deep\c.dds": "1"})
    assert out == [r"f=art\a.dds", r"f=art\b.dds", "r=a.dds", "r=b.dds", "r=c.dds"]


def test_conditions():
    out = lines(r"""@echo off
mkdir d1
if exist d1\ echo d1 is a dir
if exist d1\nothere\ echo wrong
if exist *.bat echo wildcards
if /i "ABC"=="abc" echo ci-equal
if "ABC"=="abc" echo wrong
set n=1
if defined n echo n defined
if not defined zz echo zz undefined
if 10 GTR 9 (echo numgt) else (echo strgt)
if "10" GTR "9" (echo numgt2) else (echo strgt2)
if 010 EQU 8 echo octal
""")
    assert out == ["d1 is a dir", "wildcards", "ci-equal", "n defined", "zz undefined",
                   "numgt", "strgt2", "octal"]


def test_operators_and_errorlevel():
    out = lines(r"""@echo off
mkdir d1
mkdir d1 2>nul || echo md failed el=%errorlevel%
cd nonexist 2>nul && echo cd ok || echo cd failed
echo h | find "h" && echo found
echo h | find "q" || echo not found
choice /c yn /m "go" < nul
""")
    assert out[:4] == ["md failed el=0", "cd failed", "h ", "found"]
    assert out[4] == "not found"


def test_set_a():
    out = lines(r"""@echo off
set /a y=010+0x10+3*(2+1)
set /a "z=7/2, w=-7/2, m=-7%%3"
set /a q=1, q+=4, q*=2, q^<^<=1
set /a r=1/0
echo y=%y% z=%z% w=%w% m=%m% q=%q% el=%errorlevel%
""")
    assert out[-1] == "y=33 z=3 w=-3 m=-1 q=20 el=1073750993"


def test_goto_loops_and_a_missing_label_ends_the_script():
    out, _, _, code = play(r"""@echo off
set count=0
:loop
set /a count+=1
if %count% LSS 3 goto loop
echo count=%count%
goto nowhere
echo never
""")
    assert out[0] == "count=3"
    assert "never" not in out and code == 1


def test_a_syntax_error_ends_the_whole_script():
    with pytest.raises(batch.Syntax):
        play(r"""@echo off
set x=
if %x%==y echo nope
echo never
""")


def test_a_loop_that_never_ends_is_called_one():
    world = Mem()
    world.put(r"C:\mod\x.bat", ":top\r\ngoto top\r\n")
    run = batch.Run(world, {}, r"C:\mod", limit=5000)
    with pytest.raises(batch.Endless):
        run.run(world.script(r"C:\mod\x.bat"), [r"C:\mod\x.bat"])


def test_redirection_creates_appends_and_reads():
    out, world, _, _ = play(r"""@echo off
type nothing.txt > empty.txt 2>nul
echo one> two.txt
echo two>> two.txt
set /p first=<two.txt
echo first=%first%
dir 2>nul >nul
""")
    assert world.text(r"C:\mod\empty.txt") == ""
    assert world.text(r"C:\mod\two.txt") == "one\r\ntwo\r\n"
    assert out[0] == "first=one"


def test_setlocal_puts_variables_and_the_folder_back():
    out = lines(r"""@echo off
set a=outer
mkdir sub
setlocal
set a=inner
cd sub
endlocal
echo %a% %cd%
call :x
echo after call %b%
goto :eof
:x
setlocal
set b=leaked
""")
    assert out == [r"outer C:\mod", "after call "]


def test_copy_targets_with_wildcards_name_files_the_way_cmd_does():
    _, world, _, _ = play(r"""@echo off
copy src\zone\filelist_z*u.win32.bin sub\*u_orig.win32.bin > nul
copy a.txt c.* > nul
copy a.txt *.bak > nul
ren b.txt *.old
""", files={r"C:\mod\src\zone\filelist_z0010u.win32.bin": "z1",
            r"C:\mod\src\zone\filelist_z0020u.win32.bin": "z2",
            r"C:\mod\a.txt": "x", r"C:\mod\b.txt": "y"}, folders=[r"C:\mod\sub"])
    assert world.text(r"C:\mod\sub\filelist_z0010u_orig.win32.bin") == "z1"
    assert world.text(r"C:\mod\sub\filelist_z0020u_orig.win32.bin") == "z2"
    assert world.text(r"C:\mod\c.txt") == "x"
    assert world.text(r"C:\mod\a.bak") == "x"
    assert world.text(r"C:\mod\b.old") == "y" and world.kind(r"C:\mod\b.txt") is None


def test_copy_into_a_folder_onto_a_file_and_together():
    _, world, _, _ = play(r"""@echo off
copy /y "a b.txt" out\ > nul
copy /Y a*.txt "out\*.*" > nul
copy a1.txt+a2.txt both.txt > nul
copy *.txt all.dat > nul
""", files={r"C:\mod\a b.txt": "s", r"C:\mod\a1.txt": "1", r"C:\mod\a2.txt": "2"},
        folders=[r"C:\mod\out"])
    assert world.text(r"C:\mod\out\a b.txt") == "s"
    assert world.text(r"C:\mod\out\a1.txt") == "1"
    assert world.text(r"C:\mod\both.txt") == "12"
    assert world.text(r"C:\mod\all.dat") == "s1212"


def test_robocopy_the_ways_their_installers_use_it():
    _, world, _, code = play(r"""@echo off
robocopy src dst *.trb *.imgb /S /XN /XO /XC > nul
robocopy src tree /e /xf * > nul
""", files={r"C:\mod\src\pc\c001\c001.win32.trb": "t", r"C:\mod\src\pc\c001\c001.win32.imgb": "i",
            r"C:\mod\src\pc\c001\notes.txt": "n", r"C:\mod\src\empty\deep\x.txt": "n",
            r"C:\mod\dst\pc\c001\c001.win32.trb": "kept"})
    assert world.text(r"C:\mod\dst\pc\c001\c001.win32.trb") == "kept"
    assert world.text(r"C:\mod\dst\pc\c001\c001.win32.imgb") == "i"
    assert world.kind(r"C:\mod\dst\pc\c001\notes.txt") is None
    assert world.kind(r"C:\mod\tree\pc\c001") == "dir"
    assert world.kind(r"C:\mod\tree\empty\deep") == "dir"
    assert world.kind(r"C:\mod\tree\pc\c001\c001.win32.trb") is None


def test_xcopy_and_its_file_or_folder_question():
    _, world, _, _ = play(r"""@echo off
xcopy /s /i /y art out > nul
xcopy one.txt new\ > nul
xcopy one.txt renamed.txt > nul
""", files={r"C:\mod\art\a.dds": "a", r"C:\mod\art\d\b.dds": "b", r"C:\mod\one.txt": "1"})
    assert world.text(r"C:\mod\out\a.dds") == "a" and world.text(r"C:\mod\out\d\b.dds") == "b"
    assert world.text(r"C:\mod\new\one.txt") == "1"
    assert world.text(r"C:\mod\renamed.txt") == "1"


def test_del_and_rd():
    _, world, _, _ = play(r"""@echo off
del /q work\*.tmp
del /s /q models\mod\*.*
rd /s /q gone
rd notempty
""", files={r"C:\mod\work\a.tmp": "1", r"C:\mod\work\b.keep": "1",
            r"C:\mod\models\mod\x\y.trb": "1", r"C:\mod\gone\z\a.txt": "1",
            r"C:\mod\notempty\a.txt": "1"})
    assert world.kind(r"C:\mod\work\a.tmp") is None and world.kind(r"C:\mod\work\b.keep")
    assert world.kind(r"C:\mod\models\mod\x\y.trb") is None
    assert world.kind(r"C:\mod\models\mod\x") == "dir"
    assert world.kind(r"C:\mod\gone") is None
    assert world.kind(r"C:\mod\notempty\a.txt") == "file"


def test_questions_go_to_the_world_with_what_was_on_screen():
    out, world, _, _ = play(r"""@echo off
echo 1. Install the outfit
echo 2. Uninstall
""" + "set /p pick=Choose: \n" + r"""if "%pick%"=="1" echo installing
CHOICE /C EJ /N /M "Update (E)nglish or (J)apanese version? "
if %ERRORLEVEL% EQU 2 echo japanese
""", answers=["1", "J"])
    first, second = world.asked
    assert (first.kind, first.var, first.prompt) == ("line", "pick", "Choose: ")
    assert "1. Install the outfit" in first.lines
    assert (second.kind, second.options) == ("choice", "EJ")
    assert "installing" in out and "japanese" in out


def test_programs_errorlevels_and_what_cmd_says_for_none():
    seen = []

    def tool(world, argv, cwd, io):
        seen.append(argv)
        io.out.write("Added chr/pc/c001.trb\nWrote filelist\n")
        return 3

    out, world, _, _ = play(r"""@echo off
"%~dp0SupportFiles\ff13tool.exe" -i "a b.bin" x > log.txt
echo el=%errorlevel%
set lines=0
for /f %%a in (log.txt) do if [%%a] EQU [Added] set /a lines+=1
echo lines=%lines%
nosuchthing
echo el=%errorlevel%
""", files={r"C:\mod\SupportFiles\ff13tool.exe": "MZ"}, programs={"ff13tool.exe": tool})
    assert seen == [["-i", "a b.bin", "x"]]
    assert out[:2] == ["el=3", "lines=1"]
    assert out[-1] == "el=9009"


def test_a_bat_run_without_call_never_comes_back():
    out = lines(r"""@echo off
other.bat x
echo never
""", files={r"C:\mod\other.bat": "@echo off\r\necho in other %1\r\n"})
    assert out == ["in other x"]


def test_argument_modifiers_on_files():
    out = lines(r"""@echo off
call :size data\big.bin
echo %~dp0 %~nx0
goto :eof
:size
echo size=%~z1 name=%~n1 ext=%~x1
""", files={r"C:\mod\data\big.bin": "x" * 1234})
    assert out == ["size=1234 name=big ext=.bin", r"C:\mod\ install.bat"]


def test_the_dots_trick_and_echo_forms():
    out = lines(r"""@echo off
@echo|set /p="."
echo.
echo(paren
echo:colon
""")
    assert out == [".", "", "paren", "colon"]


def test_paths_the_way_windows_reaches_them():
    assert batch.norm(r"c:\a\.\b\..\c\\d. \e ") == r"C:\a\c\d\e"
    assert batch.join(r"C:\mod", r"..\x") == r"C:\x"
    assert batch.join(r"C:\mod\sub", r"\top") == r"C:\top"
    assert batch.join(r"C:\mod", r"D:rel") == r"D:\rel"
    assert batch.parent(r"C:\a") == "C:\\" and batch.parent("C:\\") == "C:\\"
    assert batch.mask("filelist_z0010u.win32.bin", "*u_orig.win32.bin") == \
        "filelist_z0010u_orig.win32.bin"
    assert batch.split_args(r'"a b" c\"d e\\"f g"') == ["a b", 'c"d', 'e\\f g']


def test_the_admin_checks_scripts_use_all_say_admin():
    out = lines(r"""@echo off
net session >nul 2>&1 || echo net says no
fsutil dirty query C: >nul || echo fsutil says no
whoami /groups | find "S-1-16-12288" >nul || echo whoami says no
cacls "%SystemRoot%\system32\config\system" >nul 2>&1 || echo cacls says no
echo checked
""", tools={"net": lambda world, text, cwd, io: 0 if "session" in text else 2})
    assert out == ["checked"]


def test_shift_leaves_the_whole_line_and_a_missing_call_carries_on():
    out, _, _, code = play(r"""@echo off
call :sub a b c
call :nowhere
echo after el=%errorlevel%
goto :eof
:sub
shift
echo %1 [%*]
""")
    assert out[0] == "b [a b c]"
    assert out[1] == "The system cannot find the batch label specified - nowhere"
    assert out[2] == "after el=1"


def test_a_caret_at_the_end_of_a_line_runs_it_on_into_the_next():
    out, world, _, _ = play("@echo off\n"
                            "echo a ^\nb\n"
                            "copy ^\n  x.txt ^\n  y.txt > nul\n"
                            "set v=1^\n2\n"
                            "echo v=%v%\n"
                            # the ^ escapes what the next line starts with
                            "echo c ^\n&echo d\n"
                            "if 1==1 (\n  echo in^\nside\n)\n", files={r"C:\mod\x.txt": "x"})
    assert out == ["a b", "v=12", "c &echo d", "inside"]
    assert world.text(r"C:\mod\y.txt") == "x"


def test_carets_in_a_for_set_are_cmds_to_take_off():
    out = lines("@echo off\n"
                "for /f \"delims=\" %%a in ('echo hello ^| sort') do echo got=[%%a]\n"
                "for %%a in (a^,b) do echo [%%a]\n"
                "for /f \"delims=\" %%a in ('echo x^&echo y') do echo amp=[%%a]\n"
                "for /f \"delims=\" %%a in ('echo \"q^|r\"') do echo dq=[%%a]\n"
                "for /f \"delims=\" %%a in ('echo a^^^^b') do echo cc=[%%a]\n")
    assert out == ["got=[hello ]", "[a]", "[b]", "amp=[x]", "amp=[y]", 'dq=["q^|r"]', "cc=[a^b]"]


def test_find_counts_lines_and_heads_a_files_hits_with_its_name():
    out = lines("@echo off\n"
                "(echo Added a& echo.& echo Added b& echo other)> log.txt\n"
                "type log.txt | find /c /v \"\"\n"
                "for /f \"tokens=3\" %%a in ('find /c \"Added\" log.txt') do echo count=%%a\n"
                "find \"Added\" log.txt\n"
                "find \"zzz\" < log.txt > nul || echo none\n")
    assert out == ["4", "count=2", "", "---------- LOG.TXT", "Added a", "Added b", "none"]


def test_the_smaller_corners_scripts_lean_on():
    out, world, _, _ = play(r"""@echo off
call :args one,two;three=four
(cd nonexist 2>nul) || echo block failed
(cd nonexist 2>nul) && echo never
(echo a & echo b) > f.txt
type missing.txt > err.txt 2>&1
set /a big=2147483647+1
echo big=%big%
echo q> q.txt
if exist q.txt\ (echo wrong) else echo a file isn't a folder
for /l %%i in (1,1,3) do echo l=%%i
(echo ;comment& echo one& echo two)> e.txt
for /f %%i in (e.txt) do echo e=%%i
for /f "skip=2" %%i in (e.txt) do echo s=%%i
for /f "eol=; delims=, tokens=2" %%i in ("x,y,z") do echo d=%%i
mkdir sub
for /r sub %%d in (.) do echo r=%%d %%~nxd
echo n> notes
if exist notes.* echo no dot is .*
echo axb| findstr /l "a.b" > nul || echo findstr /l is literal
for /f %%a in ('cd sub') do rem
echo cd=%cd%
copy q.txt nodir\ > nul 2>&1 || echo no such folder
if 1==1 (
  :: a comment in a block
  rem. and rem: are rem too
  rem: still rem
  echo after comments
)
goto :eof
:args
echo %1 %2 %3 %4
""")
    assert out == ["one two three four", "block failed", "big=-2147483648",
                   "a file isn't a folder", "l=1", "l=2", "l=3", "e=one", "e=two", "s=two", "d=y",
                   r"r=C:\mod\sub\. sub", "no dot is .*", "findstr /l is literal", r"cd=C:\mod",
                   "no such folder", "after comments"]
    assert world.text(r"C:\mod\f.txt") == "a \r\nb\r\n"
    assert world.text(r"C:\mod\err.txt") == "The system cannot find the file specified.\r\n"
    assert world.kind(r"C:\mod\nodir") is None


def test_a_program_on_a_path_it_set_is_found():
    seen = []
    out, world, _, _ = play(r"""@echo off
set "PATH=%~dp0tools;%PATH%"
ff13tool -x a b
""", files={r"C:\mod\tools\ff13tool.exe": "MZ"},
        programs={"ff13tool.exe": lambda world, argv, cwd, io: seen.append(argv) or 0})
    assert seen == [["-x", "a", "b"]]


def test_set_p_reads_the_first_line_and_enter_alone_leaves_it_be():
    out = lines(r"""@echo off
(echo one& echo two)> two.txt
set /p first=<two.txt
echo [%first%]
set keep=old
set /p keep=New value:
echo el=%errorlevel% keep=%keep%
""", answers=[""])
    # Enter ends the prompt's line
    assert out == ["[one]", "New value:", "el=1 keep=old"]


def test_a_script_calling_itself_forever_is_a_loop_not_a_crash():
    with pytest.raises(batch.Endless, match="deeper and deeper"):
        play("@echo off\n:a\nif 1==1 (\n  call :a\n)\n")


def test_labels_calls_and_folders_the_way_cmd_keeps_them():
    out, world, _, _ = play(r"""@echo off
setlocal
set a=kept
call :drop
echo a=%a%
goto twice
:twice
echo first twice
if defined again goto end
set again=1
goto twice
:twice
echo second twice
:end   with a comment after it
mkdir sub
pushd sub
echo in=%cd%
popd
echo out=%cd%
cmd /c "set inner=1& cd sub"
echo inner=%inner% cd=%cd%
goto :eof
:drop
endlocal
""")
    # goto looks forward from where it is, so the second goto finds the second :twice
    assert out == ["a=kept", "first twice", "second twice", r"in=C:\mod\sub", r"out=C:\mod", r"inner= cd=C:\mod"]


def test_copy_xcopy_robocopy_and_move_corners():
    out, world, _, _ = play(r"""@echo off
copy one.txt one.txt > nul 2>&1 || echo not onto itself
xcopy /i /y art\*.dds out.dir > nul
echo d| xcopy one.txt asked.dir > nul
robocopy art mirror /mir > nul
echo robo=%errorlevel%
robocopy art mirror /mir > nul
echo again=%errorlevel%
move /y one.txt two.txt > nul
dir /b art\*.dds > list.txt
for /f %%f in ('dir /b art') do echo f=%%f
echo HELLO| find /i "hello" > nul && echo found any case
""", files={r"C:\mod\one.txt": "1", r"C:\mod\two.txt": "old", r"C:\mod\art\a.dds": "a",
            r"C:\mod\art\b.dds": "b", r"C:\mod\mirror\stale.dds": "s"})
    assert out == ["not onto itself", "robo=3", "again=0", "f=a.dds", "f=b.dds", "found any case"]
    assert world.text(r"C:\mod\out.dir\a.dds") == "a"
    assert world.text(r"C:\mod\asked.dir\one.txt") == "1"
    assert world.kind(r"C:\mod\mirror\stale.dds") is None
    assert world.text(r"C:\mod\two.txt") == "1" and world.kind(r"C:\mod\one.txt") is None
    assert world.text(r"C:\mod\list.txt") == "a.dds\r\nb.dds\r\n"


def test_choice_takes_its_default_and_what_is_piped_in():
    out = lines(r"""@echo off
choice /c ab /d b /t 3 > nul
echo default=%errorlevel%
echo b| choice /c ab > nul
echo piped=%errorlevel%
""")
    assert out == ["default=2", "piped=2"]


def test_start_runs_a_program_in_the_folder_it_names():
    _, world, _, _ = play(r"""@echo off
start "" /d "%~dp0tools" /wait "%~dp0tools\fix.exe" -go
""", files={r"C:\mod\tools\fix.exe": "MZ"})
    assert world.ran == [("fix.exe", "-go", r"C:\mod\tools")]


def test_start_finds_its_title_after_switches_and_a_started_bat_is_a_cmd_of_its_own():
    out, world, _, _ = play(r"""@echo off
start /wait "" "%~dp0tools\fix.exe" -once
start "" /wait helper.bat
echo back leaked=%leaked%
start "" /wait cmd /c echo started
""", files={r"C:\mod\tools\fix.exe": "MZ",
            r"C:\mod\helper.bat": "@echo off\r\nset leaked=yes\r\necho in helper\r\nexit\r\n"})
    assert out == ["in helper", "back leaked=", "started"]
    assert world.ran == [("fix.exe", "-once", r"C:\mod")]


def test_copy_joins_files_with_spaces_in_their_names():
    _, world, _, _ = play(r"""@echo off
copy /b log.txt + "a b.txt" both.txt > nul
copy /b "a+b.txt" + log.txt plus.txt > nul
copy log.txt + "a b.txt" > nul
""", files={r"C:\mod\log.txt": "log\r\n", r"C:\mod\a b.txt": "spaced\r\n",
            r"C:\mod\a+b.txt": "plus\r\n"})
    assert world.text(r"C:\mod\both.txt") == "log\r\nspaced\r\n"
    assert world.text(r"C:\mod\plus.txt") == "plus\r\nlog\r\n"
    assert world.text(r"C:\mod\log.txt") == "log\r\nspaced\r\n"


def test_find_on_a_file_that_isnt_there_and_a_rem_that_ends_in_a_caret():
    out = lines(r"""@echo off
find "x" missing.txt
echo el=%errorlevel%
rem a remark that ends in a caret ^
echo after the remark
echo a & rem and one after a command ^
echo after that one
""")
    assert out == ["File not found - MISSING.TXT", "el=2", "after the remark", "a ",
                   "after that one"]


def test_find_reads_every_file_a_wildcard_matches():
    out = lines("@echo off\n"
                "(echo hit one& echo miss)> a.txt\n"
                "echo hit two> b.txt\n"
                "echo hit three> c.log\n"
                "find \"hit\" *.txt\n"
                "echo el=%errorlevel%\n"
                "find \"hit\" *.ini\n"
                "echo el=%errorlevel%\n")
    assert out == ["", "---------- A.TXT", "hit one", "", "---------- B.TXT", "hit two", "el=0",
                   "File not found - *.INI", "el=2"]


def test_an_archive_isnt_read_whole_as_text(monkeypatch):
    monkeypatch.setattr(batch, "TEXT_LIMIT", 10)
    out = lines("@echo off\n"
                "echo hit and more than ten bytes> big.bin\n"
                "echo hit> small.bin\n"
                "find /c \"hit\" *.bin\n")
    assert out == ["", "---------- BIG.BIN: 0", "", "---------- SMALL.BIN: 1"]


def test_start_finds_its_program_here_or_in_its_folder_and_hands_on_only_what_follows():
    out, world, _, _ = play(r"""@echo off
start /b /wait "" /d "%~dp0tools" cmd /c fix.bat
echo el=%errorlevel%
start "" /d "%~dp0tools" onlyhere.bat
start /wait "" /d "%~dp0tools" both.bat
start /wait "fix" fix -j
""", files={r"C:\mod\tools\fix.bat": "@echo off\r\necho fixed in %cd%\r\n",
            r"C:\mod\tools\onlyhere.bat": "@echo off\r\necho only here\r\n",
            r"C:\mod\both.bat": "@echo off\r\necho the one beside it, in %cd%\r\n",
            r"C:\mod\tools\both.bat": "@echo off\r\necho the one in tools\r\n",
            r"C:\mod\fix.exe": "MZ"})
    assert out == [r"fixed in C:\mod\tools", "el=0", "only here",
                   r"the one beside it, in C:\mod\tools"]
    assert world.ran == [("fix.exe", "-j", r"C:\mod")]


def test_a_rem_inside_quotes_or_after_an_escaped_amp_is_no_remark():
    out = lines("@echo off\n"
                "echo \"fish & rem chips\" ^\nand more\n"
                "echo Tom ^& rem ^\nJerry\n"
                "echo a & rem comment ^\n"
                "echo after the remark\n")
    assert out == ['"fish & rem chips" and more', "Tom & rem Jerry", "a ", "after the remark"]


def test_exit_with_its_switch_run_on_is_still_an_exit():
    """exit/b the way cmd takes it, so a check that leaves on one is it giving up"""
    for line in ("exit/b 1", "exit /b 1", "exit", "goto :eof", "goto:eof"):
        assert batch._exits(batch.parse(line)[0]), line
    for line in ("goto end", "echo exit", "exiting"):
        assert not batch._exits(batch.parse(line)[0]), line
