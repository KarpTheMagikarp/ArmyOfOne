"""
An HD Models Plus add-on's .bat played with no HD Models Plus anywhere: its
SupportFiles\\ tools stand in built in, FF XIII HD Models.exe runs natively
(hdmodels/build.py) on the game's original models, and what it built goes in
through the .bat's own ff13tool -i
"""

import pytest

from patcher.mods import models_plus, sandbox
from patcher.mods.hdmodels import build
from tests import hdm_world as W
from tests.test_sandbox import IMGB, TRB, laid, play, sums, xiii

MODEL = "chr\\pc\\c004\\bin\\c004.win32"
OUT = "chr\\pc\\c004\\bin\\c004.win32"

ADDON = r"""@echo off
cd /d "%~dp0"
if not exist "SupportFiles\FF XIII HD Models.exe" goto MISSING
if not exist SupportFiles\FFXIIItrbSetup.bat goto MISSING
call Supportfiles\FFXIIItrbSetup.bat
if %ERRORLEVEL% NEQ 0 goto END
"SupportFiles\FF XIII HD Models" -s Data\Body.txt
if %ERRORLEVEL% NEQ 0 goto FAILED
set /p ff13filepath=<FFXIII_Dir.txt
cd models\mod
..\..\SupportFiles\ff13tool -i "%ff13filepath%white_data\sys\filelist_scru.win32.bin" "%ff13filepath%white_data\sys\white_scru.win32.bin" chr\pc\c004\bin\c004.win32.trb > tool.log
..\..\SupportFiles\ff13tool -i "%ff13filepath%white_data\sys\filelist_scru.win32.bin" "%ff13filepath%white_data\sys\white_scru.win32.bin" chr\pc\c004\bin\c004.win32.imgb >> tool.log
cd ..\..
goto END
:MISSING
@echo Some required files are missing.
goto END
:FAILED
@echo Error creating model.  Exiting without loading new model.
:END
exit /b 0
"""


def original():
    """a small c004 with a texture, the way the game has it"""
    tex, size = W.txb(0x18, 16, 16, 3)
    pixels = bytes((n * 13) & 0xFF for n in range(size))
    trb = W.trb([b"SEDBabc\0" + bytes(24), tex, b"c_c004 body".ljust(32, b"\0")],
                names=[b"F00\\a", b"tex_a", b"c_c004_b", b"m1", b"m2"],
                records=[b"r0".ljust(16, b"\0"), b"tex_a".ljust(16, b"\0"), b"r2".ljust(16, b"\0"),
                         b"m", b"m"])
    return {f"{MODEL.replace(chr(92), '/')}.trb": trb, f"{MODEL.replace(chr(92), '/')}.imgb": pixels}


def recipe(edits=(), blocks=None):
    return W.recipe([MODEL], OUT, blocks or [(0, n) for n in range(5)], edits=edits,
                    swaps=[("c_c004", "c_c005")]).decode("latin-1")


@pytest.fixture
def originals(monkeypatch):
    models = original()
    asked = []

    def fake(install_dir, wanted, **kw):
        asked.extend(wanted)
        return {path: data for path, data in models.items()
                if any(path.startswith(w) for w in wanted)}
    monkeypatch.setattr(models_plus, "originals", fake)
    return models, asked


def expected(text: str, models: dict) -> dict:
    class Files:
        def read(self, path):
            low = path.replace("\\", "/").lower()
            if low == "data/body.txt":
                return text.encode("latin-1")
            return models.get(low.removeprefix("models/orig/"))
    return build.run(text.encode("latin-1"), Files())


def addon(root, text):
    from tests.test_sandbox import drop
    return drop(root, "Vanille Nude Mod", {"XIII Vanille All Naturey.bat": ADDON,
                                           "Data/Body.txt": text})


def test_an_addon_builds_its_model_natively_with_no_hd_models_plus_anywhere(tmp_path, originals):
    models, asked = originals
    game = xiii(tmp_path / "steam")
    before = sums(game)
    text = recipe()
    p = play(game, addon(tmp_path / "downloads", text))
    built = expected(text, models)
    assert laid(p) == {f"white_scru/{TRB}": built[f"models\\mod\\{OUT}.trb"],
                       f"white_scru/{IMGB}": built[f"models\\mod\\{OUT}.imgb"]}, \
        (p.problem, p.detail)
    # the swap went through: it's c005's model now, built from c004's original
    assert b"c_c005" in built[f"models\\mod\\{OUT}.trb"]
    assert asked == [MODEL.replace("\\", "/")]
    assert p.tried == ["with HD Models Plus's tools built in"], p.tried
    # nothing of theirs ran for real, and nothing touched the real game
    assert sums(game) == before


def test_a_recipe_the_builder_cant_build_says_so_shortly(tmp_path, originals):
    game = xiii(tmp_path / "steam")
    bad = recipe(blocks=[(0, 0), (0, 9), (0, 3), (0, 4)])
    p = play(game, addon(tmp_path / "downloads", bad))
    assert laid(p) == {}
    assert p.problem == "builds a character model, and its recipe asks for block 9 of a model " \
                        "that hasn't got one"
    assert p.detail.startswith(sandbox.MODELS_FIX)


def test_an_original_the_game_hasnt_got_says_whose(tmp_path, monkeypatch):
    def none(install_dir, wanted, **kw):
        raise models_plus.ModelsError("it builds from the game's original character models, "
                                      "and Hope's is changed in your game")
    monkeypatch.setattr(models_plus, "originals", none)
    game = xiii(tmp_path / "steam")
    p = play(game, addon(tmp_path / "downloads", recipe()))
    assert laid(p) == {}
    assert "Hope's is changed in your game" in p.problem


def test_its_questions_are_asked_like_the_bats_own(tmp_path, originals):
    game = xiii(tmp_path / "steam")
    text = recipe().replace("42\r\n", "42\r\n::A,1,1,Use the long hair? (Y/N)\r\n", 1)
    p = play(game, addon(tmp_path / "downloads", text))
    assert any("long hair" in str(q) for q, _ in p.asked), p.asked
    assert laid(p), (p.problem, p.detail)


def test_the_kit_never_stands_in_on_another_game(tmp_path):
    class Game:
        data = "alba_data"

    class Stage:
        game = Game()

    class World:
        stage = Stage()
        dry = False

        def staged(self, path, text, cwd, io):
            return "staged"

    assert sandbox._hd_models(World(), ["Data\\x.txt"], "C:\\m", None, "x.exe") == "staged"
    assert sandbox.Stage._models_kit(type("S", (), {"game": Game()})(),
                                     "SupportFiles\\FF XIII HD Models.exe") is None
