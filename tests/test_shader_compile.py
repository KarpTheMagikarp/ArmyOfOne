"""
Every effect the shader row's presets run, through ReShade 6.8's own
preprocessor and parser (tools/fxcheck), with each preset's definitions. An
effect that doesn't parse runs as no effect at all in game, with nothing on
screen to say so, and the rest of this suite reads the files as text.

Skipped without the checker: tools/fxcheck/build.sh makes it, and CI's shader
job builds it on every push. FXCHECK can point at one built elsewhere.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / "assets" / "mods" / "reshade"
SHADERS = BUNDLE / "reshade-shaders" / "Shaders"
PRESETS = ("Fabula-Lous Shader.ini", "Fabula-Lous Shader XIII-2.ini", "Fabula-Lous Shader LR.ini")


def checker() -> Path | None:
    named = os.environ.get("FXCHECK")
    built = REPO / "tools" / "fxcheck"
    for path in ([Path(named)] if named else []) + [built / "fxcheck", built / "fxcheck.exe"]:
        if path.is_file():
            return path
    return None


def runs(preset: Path) -> list:
    """(effect, its definitions as NAME=VALUE) for each technique the preset runs"""
    sections, section = {"": {}}, ""
    for line in preset.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            sections.setdefault(section, {})
        elif "=" in line:
            key, _, value = line.partition("=")
            sections[section][key] = value
    out = []
    for entry in sections[""]["Techniques"].split(","):
        effect = entry.partition("@")[2]
        # the preset's own come first, as ReShade adds them, so they win a clash
        said = sections.get(effect, {}).get("PreprocessorDefinitions", "")
        said += "," + sections[""].get("PreprocessorDefinitions", "")
        out.append((effect, [d.strip() for d in said.split(",") if d.strip()]))
    return out


def check(fx: Path, defines=()) -> subprocess.CompletedProcess:
    found = checker()
    if found is None:
        pytest.skip("no tools/fxcheck/fxcheck: tools/fxcheck/build.sh builds it")
    return subprocess.run([str(found), str(fx), *defines], capture_output=True, text=True,
                          timeout=120)


@pytest.mark.parametrize("preset", PRESETS)
def test_every_effect_a_preset_runs_parses_in_reshade(preset):
    for effect, defines in runs(BUNDLE / preset):
        done = check(SHADERS / effect, defines)
        assert done.returncode == 0, f"{preset}: {effect} {defines}\n{done.stdout}"
        # SweetFX's warnings are theirs to have; one in the grade is ours
        if effect == "FabulaLous.fx":
            assert "warning" not in done.stdout, done.stdout


@pytest.mark.parametrize("defines, broken", [
    # the haze's own passes, which only XIII-2's preset compiles
    (["FABULA_HAZE=1"], ("float settle =", "float settle")),
    # and a haze texture read on the path XIII and LR compile, where it isn't declared
    ([], ("float luma = dot(", "float luma = tex2Dlod(FabulaLevel, 0.5).r + dot(")),
    # a pass reading the texture it draws into, which ReShade only says and runs
    (["FABULA_HAZE=1"], ("PixelShader = FabulaLevelPass;", "PixelShader = FabulaKeepPass;")),
])
def test_the_checker_turns_down_a_grade_that_wouldnt_run(tmp_path, defines, broken):
    """so a checker that passes everything can't pass for one that works"""
    for name in ("FabulaLous.fx", "ReShade.fxh", "ReShadeUI.fxh"):
        shutil.copyfile(SHADERS / name, tmp_path / name)
    grade = tmp_path / "FabulaLous.fx"
    text = grade.read_text(encoding="utf-8")
    assert text.count(broken[0]) == 1
    grade.write_text(text.replace(broken[0], broken[1]), encoding="utf-8")
    done = check(grade, defines)
    assert done.returncode == 1 and "error" in done.stdout, done.stdout
