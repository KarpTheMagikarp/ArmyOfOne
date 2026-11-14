"""
The ReShade Extra: ReShade 6.8, the Fabula-Lous Shader preset and the SweetFX shaders
it runs, put in by Apply where each game's NovaFix loads it and listed in Mods
to come off again. One bundle, three games.

The dll and SMAA's two textures aren't in git, so the runs go through a copy of
the bundle with stand-ins for those and the pins redone to match. The checks on
the preset read the real files.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

import patcher.bridge as bridge_mod
import patcher.paths as paths_mod
from patcher import novafix_config
from patcher.engine import PatchStatus
from patcher.manifest import load_manifest
from tests.helpers import make_export_pe, make_fake_pe
from tests.test_mod_drops import library_with
from tests.test_reshade import BUILD, package, zipped

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / "assets" / "mods" / "reshade"
SHADERS = BUNDLE / "reshade-shaders" / "Shaders"
OURS = make_export_pe(["Direct3DCreate9", "ReShadeVersion"], version=(6, 8))
# the Extra's name, which is its preset's, and the file that preset is
NAME, PRESET = "Fabula-Lous Shader", "Fabula-Lous Shader.ini"
NOVAFIX = make_export_pe(["Direct3DCreate9"]) + "OverrideLauncherGraphics".encode("utf-16-le")
NEIGHBOURS = {"dinput8.dll": b"MZ saviors", "SaviorsPatch.ini": b"[General]\r\n",
              "ReShade.ini": b"[GENERAL]\r\nPresetPath=.\\Old.ini\r\n",
              "reshade-shaders/Shaders/Old.fx": b"// left by an installer"}
# ui id: its manifest, its folder in the library, and where its NovaFix sits in it
GAMES = {
    "ff13": ("ffxiii", "FFXIII", "white_data/prog/win/bin"),
    "ff13-2": ("ffxiii2", "FFXIII2", "alba_data/prog/win/bin"),
    "lr": ("ffxiii3", "FINAL FANTASY XIII-3", "."),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def the_patch(manifest="ffxiii"):
    return next(p for p in load_manifest(REPO / "manifests" / f"{manifest}.json").patches
                if p.ui_job_id == "reshade")


def shipped(game: str = "ff13") -> bytes:
    """the preset that game's manifest puts in, each its own under the one name"""
    spec = next(s for s in the_patch(GAMES[game][0]).bundled_files
                if s["target"] == f"ReShade/{PRESET}")
    return (REPO / spec["source"]).read_bytes()


# the preset files there are, by a game that runs each
PRESETS = {"ff13": "Fabula-Lous Shader.ini", "ff13-2": "Fabula-Lous Shader XIII-2.ini",
           "lr": "Fabula-Lous Shader LR.ini"}


def ini(text: str) -> dict:
    """section -> key -> value, keys above the first section under \"\" the way a preset has them"""
    out, section = {"": {}}, ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out.setdefault(section, {})
        elif "=" in line:
            key, _, value = line.partition("=")
            out[section][key.strip()] = value.strip()
    return out


# the switches a preset sets that these tests settle. Every other #if is
# SweetFX's own and stays as written, both ways
SWITCHES = {"FABULA_HAZE", "FABULA_LIFT"}


def code(fx: Path, defines: dict | None = None) -> str:
    """
    an effect with its comments out, since SweetFX keeps old uniforms commented,
    and its #if on one of SWITCHES settled the way `defines` would have ReShade
    compile it
    """
    source = re.sub(r"/\*.*?\*/", "", fx.read_text(encoding="utf-8"), flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    defines = dict(defines or {})
    kept, stack = [], []
    for line in source.splitlines():
        word = line.strip().split()
        head = word[0] if word else ""
        if head in ("#if", "#ifdef", "#ifndef"):
            name = word[1] if len(word) == 2 else None
            # "#if FABULA_HAZE || FABULA_LIFT", the one other shape the grade has
            either = word[1::2] if head == "#if" and set(word[2::2]) == {"||"} else []
            if name in SWITCHES:
                on = (int(defines.get(name, 0)) != 0 if head == "#if"
                      else (name in defines) == (head == "#ifdef"))
                stack.append(on)
                continue
            if either and set(either) <= SWITCHES:
                stack.append(any(int(defines.get(n, 0)) != 0 for n in either))
                continue
            stack.append(None)
        elif head == "#else" and stack and stack[-1] is not None:
            stack[-1] = not stack[-1]
            continue
        elif head == "#endif" and stack:
            if stack.pop() is not None:
                continue
        if all(on is not False for on in stack):
            if head == "#define" and len(word) >= 3 and word[1] in SWITCHES:
                defines.setdefault(word[1], word[2])
            kept.append(line)
    assert not stack, f"{fx.name}: an #if without its #endif"
    return "\n".join(kept)


def uniforms(fx: Path, defines: dict | None = None) -> dict:
    """name -> (type, annotations) for every uniform the effect declares"""
    return {m.group(2): (m.group(1), m.group(3)) for m in re.finditer(
        r"uniform\s+(\w+)\s+(\w+)\s*<(.*?)>\s*=", code(fx, defines), flags=re.S)}


def definitions(values: dict) -> dict:
    """a preset section's PreprocessorDefinitions, which ReShade compiles that effect with"""
    pairs = [d.partition("=") for d in values.get("PreprocessorDefinitions", "").split(",") if d]
    return {name.strip(): value.strip() or "1" for name, _, value in pairs}


def bound(notes: str, name: str):
    found = re.search(rf"\b{name}\s*=\s*(-?[\d.]+)", notes)
    return float(found.group(1)) if found else None


def choices(notes: str) -> int | None:
    """how many entries a combo has. ui_items can be one string or several run together"""
    found = re.search(r"ui_items\s*=\s*((?:\"[^\"]*\"\s*)+);", notes)
    if not found:
        return None
    joined = "".join(re.findall(r"\"([^\"]*)\"", found.group(1)))
    return len([item for item in joined.split("\\0") if item])


def build(tmp_path, monkeypatch, game: str):
    """
    one game with NovaFix beside its exe, and the bundle with stand-ins for its
    binaries. The bridge, the ReShade folder and tmp_path
    """
    manifest_id, folder, where = GAMES[game]
    library, _, paths = library_with(tmp_path, xiii=game == "ff13")
    root = library / "steamapps" / "common" / folder
    if game == "lr":
        root.mkdir(parents=True)
        (root / "LRFF13.exe").write_bytes(make_fake_pe(machine=0x14C))
        (library / "steamapps" / "appmanifest_345350.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"345350"\n\t"installdir"\t\t"{folder}"\n}}\n')
        # its neighbours in the root: Savior's Patch, and what an official
        # ReShade install leaves beside the exe
        for name, data in NEIGHBOURS.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
    beside = root / where
    (beside / "d3d9.dll").write_bytes(NOVAFIX)

    payload = tmp_path / "payload"
    shutil.copytree(BUNDLE, payload / "assets" / "mods" / "reshade",
                    ignore=shutil.ignore_patterns("*.dll", "*.png"))
    (payload / "assets" / "mods" / "reshade" / "d3d9.dll").write_bytes(OURS)
    for name in ("AreaTex.png", "SearchTex.png"):
        (payload / "assets" / "mods" / "reshade" / "reshade-shaders" / "Textures" / name) \
            .write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    data = json.loads((REPO / "manifests" / f"{manifest_id}.json").read_text(encoding="utf-8"))
    for patch in data["patches"]:
        for spec in patch.get("bundled_files") or []:
            if patch["id"] == "install_novafix":
                spec["sha256"] = sha(NOVAFIX)
            elif patch["id"] == "install_reshade":
                spec["sha256"] = sha((payload / spec["source"]).read_bytes())
    manifest = tmp_path / f"{manifest_id}.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(paths_mod, "bundled_data_root", lambda: payload)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = bridge_mod.UiBridge(manifest_paths=dict(paths, **{manifest_id: manifest}),
                                 steam_library_paths=[library])
    return bridge, beside / "ReShade", tmp_path


@pytest.fixture
def world(tmp_path, monkeypatch):
    """XIII, where the deeper cases run: the code under them is the same for all three"""
    return build(tmp_path, monkeypatch, "ff13")


@pytest.fixture(params=list(GAMES))
def each(request, tmp_path, monkeypatch):
    return request.param, *build(tmp_path, monkeypatch, request.param)


def around(home):
    """every file in the folder ReShade goes into, but ReShade's own"""
    return {p.relative_to(home.parent).as_posix(): p.read_bytes()
            for p in home.parent.rglob("*") if p.is_file() and home not in p.parents}


def untouched(before: dict, home) -> bool:
    """what was there is still there, byte for byte. Apply writes NovaFix.ini and
    its own undo record beside it, which are the run's, not ReShade's"""
    now = around(home)
    return all(now.get(name) == data for name, data in before.items())


def installed_row(bridge, game="ff13"):
    rows = [m for m in bridge.list_addons(game)["mods"] if m["kind"] == "patch"]
    return rows[0] if rows else None


@pytest.mark.parametrize("game", list(GAMES))
def test_every_game_offers_it_unticked_in_apply(game):
    jobs = json.loads((REPO / "ui" / "jobs.json").read_text(encoding="utf-8"))
    entry = next(g for g in jobs["games"] if g["id"] == game)
    job = next(j for j in entry["jobs"] if j["id"] == "reshade")
    assert job["kind"] == "optional" and job["default"] is False
    assert "page" not in job, "nothing to download, so nothing to open"
    patch = the_patch(GAMES[game][0])
    assert patch.type == "bundled_install" and patch.in_mods and not patch.default_enabled
    assert "install_novafix" in patch.requires


@pytest.mark.parametrize("manifest", ["ffxiii2", "ffxiii3"])
def test_the_other_two_ship_xiiis_bundle_beside_their_own_novafix(manifest):
    """
    one bundle: a file that drifts in one manifest is a ReShade that differs by
    game. The preset is the one exception, XIII-2's and LR's, which go in
    under the same name so ReShade.ini and the drawer can't tell
    """
    patches = {p.id: p for p in load_manifest(REPO / "manifests" / f"{manifest}.json").patches}
    ours, xiii = patches["install_reshade"], the_patch()
    preset = f"ReShade/{PRESET}"
    assert [s["target"] for s in ours.bundled_files] == [s["target"] for s in xiii.bundled_files]
    assert [s for s in ours.bundled_files if s["target"] != preset] == \
        [s for s in xiii.bundled_files if s["target"] != preset]
    theirs, mine = (next(s for s in files if s["target"] == preset)
                    for files in (ours.bundled_files, xiii.bundled_files))
    own = {"ffxiii2": PRESETS["ff13-2"], "ffxiii3": PRESETS["lr"]}[manifest]
    assert theirs["source"].endswith(own) and theirs["sha256"] != mine["sha256"]
    assert {k: v for k, v in theirs.items() if k not in ("source", "sha256")} == \
        {k: v for k, v in mine.items() if k not in ("source", "sha256")}
    assert ours.install_relative_path == patches["install_novafix"].install_relative_path
    assert (ours.label, ours.requires, ours.player_note) == (xiii.label, xiii.requires,
                                                             xiii.player_note)


def test_apply_puts_it_where_novafix_loads_it(each):
    game, bridge, home, _ = each
    done = bridge.apply(game, ["reshade"])

    assert done["ok"], done
    assert done["results"][0]["detail"] == "installed 15 files into ReShade"
    assert (home / "d3d9.dll").read_bytes() == OURS
    assert (home.parent / "d3d9.dll").read_bytes() == NOVAFIX, "NovaFix's slot is never touched"
    assert (home / PRESET).read_bytes() == shipped(game)
    assert (home / "reshade-shaders" / "Shaders" / "SMAA.fxh").is_file()
    assert (home / "LICENSE-ReShade.md").is_file(), "BSD 3-Clause wants its notice with the dll"
    assert "reshade" in bridge.scan(game)["applied"]


def test_someone_elses_reshade_there_leaves_it_out_whole(each):
    game, bridge, home, _ = each
    home.mkdir()
    (home / "d3d9.dll").write_bytes(BUILD)
    theirs = b"[GENERAL]\r\nPresetPath=.\\Mine.ini\r\n"
    (home / "ReShade.ini").write_bytes(theirs)

    result = bridge.apply(game, ["reshade"])["results"][0]

    assert result["status"] == PatchStatus.SKIPPED and "isn't this app's" in result["detail"]
    assert sorted(p.name for p in home.iterdir()) == ["ReShade.ini", "d3d9.dll"]
    assert (home / "d3d9.dll").read_bytes() == BUILD
    assert (home / "ReShade.ini").read_bytes() == theirs
    assert installed_row(bridge, game) is None


def test_installed_lists_it_and_remove_takes_every_file_off(each):
    game, bridge, home, _ = each
    before = around(home)
    bridge.apply(game, ["reshade"])
    assert untouched(before, home), "nothing beside it is touched going in"
    row = installed_row(bridge, game)
    assert row["name"] == NAME and row["jobId"] == "reshade" and row["managed"]
    assert row["about"] == "ReShade preset, Home opens its menu"

    done = bridge.remove_addon(game, NAME, "patch")

    assert done["ok"] and done["summary"] == f"Removed {NAME}.", done
    assert done["message"] == f"Every file {NAME} put in is gone."
    assert not home.exists(), "the folders it made went with it"
    assert untouched(before, home), "or coming off, NovaFix and on LR Savior's Patch included"
    assert "reshade" not in bridge.scan(game)["applied"]
    assert installed_row(bridge, game) is None


def test_settings_reshade_saved_stay_through_remove_and_back(each):
    """ReShade writes ReShade.ini back on every exit, so this is every install that's run once"""
    game, bridge, home, _ = each
    bridge.apply(game, ["reshade"])
    saved = (home / "ReShade.ini").read_bytes() + b"\r\n[STYLE]\r\nAlpha=1.000000\r\n"
    (home / "ReShade.ini").write_bytes(saved)
    (home / "ReShade.log").write_bytes(b"INFO | Initializing crosire's ReShade")

    done = bridge.remove_addon(game, NAME, "patch")

    assert done["ok"] and done["message"] == "ReShade.ini changed since it went in, so it stays."
    # the settings stay for next time, its log is nobody's
    assert sorted(p.name for p in home.iterdir()) == ["ReShade.ini"]

    assert bridge.apply(game, ["reshade"])["ok"]
    assert (home / "ReShade.ini").read_bytes() == saved
    assert (home / "ReShade.ini.new").read_bytes() == (BUNDLE / "ReShade.ini").read_bytes()
    assert "reshade" in bridge.scan(game)["applied"]

    assert bridge.remove_addon(game, NAME, "patch")["ok"]
    assert sorted(p.name for p in home.iterdir()) == ["ReShade.ini"]
    assert (home / "ReShade.ini").read_bytes() == saved


def depth_off(ini: bytes) -> bytes:
    """a ReShade.ini as Apply leaves it: ReShade's depth add-on off on the end"""
    return ini + b"\r\n[ADDON]\r\nDisabledAddons=Generic Depth\r\n"


def test_reshades_depth_addon_is_off_and_a_choice_about_it_stays(each):
    """
    nothing in the preset reads depth, and the add-on that finds it watches
    every draw call, so Apply turns it off. Once: a DisabledAddons line is
    what ReShade writes when somebody ticks an add-on in its menu, and that's
    theirs, empty or not
    """
    game, bridge, home, _ = each
    bridge.apply(game, ["reshade"])
    ini = home / "ReShade.ini"
    assert ini.read_bytes() == depth_off((BUNDLE / "ReShade.ini").read_bytes())
    assert bridge.apply(game, ["reshade"])["ok"]
    assert ini.read_bytes().count(b"DisabledAddons") == 1
    assert not list(home.rglob("*.new"))

    # theirs, after a run: ReShade wrote the section back with nothing in it
    theirs = ini.read_bytes().replace(b"DisabledAddons=Generic Depth", b"DisabledAddons=")
    ini.write_bytes(theirs)
    bridge.list_addons(game)
    assert bridge.apply(game, ["reshade"])["ok"]
    assert ini.read_bytes() == theirs

    # an [ADDON] section of theirs without the line gets it, inside the section
    ini.write_bytes(b"[ADDON]\r\nAddonPath=.\\addons\r\n\r\n[GENERAL]\r\nPerformanceMode=1\r\n")
    bridge.list_addons(game)
    assert ini.read_bytes().startswith(
        b"[ADDON]\r\nDisabledAddons=Generic Depth\r\nAddonPath=.\\addons\r\n")


def test_the_depth_addon_waits_while_the_game_runs(each, monkeypatch):
    """ReShade writes its own copy back on exit, so a line put in under it is lost"""
    game, bridge, home, _ = each
    bridge.apply(game, ["reshade"])
    ini = home / "ReShade.ini"
    ini.write_bytes((BUNDLE / "ReShade.ini").read_bytes())
    monkeypatch.setattr(bridge, "game_running", lambda *_: True)
    bridge.list_addons(game)
    assert b"DisabledAddons" not in ini.read_bytes()
    monkeypatch.setattr(bridge, "game_running", lambda *_: False)
    bridge.list_addons(game)
    assert ini.read_bytes() == depth_off((BUNDLE / "ReShade.ini").read_bytes())


def test_settings_reshade_rewrote_get_no_copy_of_the_same_version_beside_them(each):
    """
    ReShade writes ReShade.ini back on every exit, so on an install that's been
    run it's never the file that went in. Ours still is, and a .new of it would
    be a copy of what they started from, on every update
    """
    game, bridge, home, _ = each
    bridge.apply(game, ["reshade"])
    saved = (home / "ReShade.ini").read_bytes() + b"\r\n[STYLE]\r\nAlpha=1.000000\r\n"
    (home / "ReShade.ini").write_bytes(saved)

    # twice: the run that leaves theirs is the record the next one reads
    for _ in range(2):
        (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()
        result = bridge.apply(game, ["reshade"])["results"][0]
        assert result["ok"] and ".new" not in result["detail"], result
        assert (home / "ReShade.ini").read_bytes() == saved
        assert not list(home.rglob("*.new"))


def test_a_new_one_set_beside_before_stays_on_the_record_for_remove(each):
    """a run that leaves theirs alone still owns the .new an earlier one put beside it"""
    game, bridge, home, _ = each
    bridge.apply(game, ["reshade"])
    saved = (home / "ReShade.ini").read_bytes() + b"\r\n[STYLE]\r\nAlpha=1.000000\r\n"
    (home / "ReShade.ini").write_bytes(saved)
    assert bridge.remove_addon(game, NAME, "patch")["ok"]
    assert bridge.apply(game, ["reshade"])["ok"]
    assert (home / "ReShade.ini.new").is_file()
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()
    assert bridge.apply(game, ["reshade"])["ok"]

    assert bridge.remove_addon(game, NAME, "patch")["ok"]

    assert sorted(p.name for p in home.iterdir()) == ["ReShade.ini"]
    assert (home / "ReShade.ini").read_bytes() == saved


def test_reshades_log_doesnt_keep_its_folder_after_remove(each):
    """named after the dll in 6.x, ReShade.log before. Either was all that kept the folder"""
    game, bridge, home, _ = each
    bridge.apply(game, ["reshade"])
    (home / "d3d9.log").write_bytes(b"INFO | Initializing crosire's ReShade")
    (home / "ReShade.log").write_bytes(b"INFO | Initializing crosire's ReShade")

    assert bridge.remove_addon(game, NAME, "patch")["ok"]
    assert not home.exists()


def test_the_installed_line_reads_the_settings_it_has_now(world):
    bridge, home, _ = world
    bridge.apply("ff13", ["reshade"])
    settings = (home / "ReShade.ini").read_text(encoding="utf-8")
    (home / "ReShade.ini").write_text(settings.replace("KeyOverlay=36", "KeyOverlay=35")
                                      .replace(PRESET, "Mine.ini"), encoding="utf-8")
    assert installed_row(bridge)["about"] == "Mine preset, End opens its menu"
    # Installed shows the name alone, and a line only when something's wrong
    assert installed_row(bridge)["warn"] == ""
    (home / "d3d9.dll").unlink()
    assert installed_row(bridge)["about"] == installed_row(bridge)["warn"] == "d3d9.dll is missing"


def test_a_drawer_reshade_waits_for_ours_to_come_off(each):
    game, bridge, home, tmp = each
    bridge.apply(game, ["reshade"])

    done = bridge.install_mod(game, str(zipped(tmp, "pkg.zip", package())))

    assert not done["ok"] and f"The patcher's own ReShade, {NAME}, is in" in done["error"], done
    assert (home / "d3d9.dll").read_bytes() == OURS

    assert bridge.remove_addon(game, NAME, "patch")["ok"]
    done = bridge.install_mod(game, str(zipped(tmp, "pkg.zip", package())))
    assert done["ok"], done
    assert (home / "d3d9.dll").read_bytes() == BUILD


def test_remove_only_answers_for_what_went_in(world):
    bridge, _, _ = world
    done = bridge.remove_addon("ff13", NAME, "patch")
    assert not done["ok"] and done["error"] == f"{NAME} isn't in."


@pytest.mark.parametrize("game", list(PRESETS))
def test_the_preset_runs_techniques_its_effects_declare(game):
    preset = ini((BUNDLE / PRESETS[game]).read_text(encoding="utf-8"))
    order = preset[""]["Techniques"].split(",")
    assert order == preset[""]["TechniqueSorting"].split(","), "the menu's order is the run order"
    # AA on the game's own edges first, the sharpening after it so it can't
    # sharpen the jaggies back in, the grade last over the finished picture
    assert [t.split("@")[0] for t in order] == ["SMAA", "LumaSharpen", "FabulaLous"]
    for entry in order:
        name, _, effect = entry.partition("@")
        compiled = code(SHADERS / effect, definitions(preset.get(effect, {})))
        assert re.search(rf"\btechnique\s+{name}\b", compiled), entry
    assert set(preset) - {""} == {t.partition("@")[2] for t in order}


@pytest.mark.parametrize("game", list(PRESETS))
def test_every_value_in_the_preset_is_a_setting_its_effect_has_in_range(game):
    preset = ini((BUNDLE / PRESETS[game]).read_text(encoding="utf-8"))
    for effect, values in preset.items():
        if not effect:
            continue
        declared = uniforms(SHADERS / effect, definitions(values))
        for key, value in values.items():
            if key == "PreprocessorDefinitions":
                continue
            assert key in declared, f"{effect} has no {key}"
            kind, notes = declared[key]
            low, high, items = bound(notes, "ui_min"), bound(notes, "ui_max"), choices(notes)
            for number in (float(v) for v in value.split(",")):
                where = f"{effect} {key}={value}"
                if kind == "bool":
                    assert number in (0, 1), where
                if items is not None:
                    assert 0 <= number < items, where
                assert low is None or number >= low, where
                assert high is None or number <= high, where


@pytest.mark.parametrize("game", list(PRESETS))
def test_the_look_stays_gentle(game):
    """subtle is the brief: a preset that reads as a filter gets turned off"""
    preset = ini((BUNDLE / PRESETS[game]).read_text(encoding="utf-8"))
    assert float(preset["LumaSharpen.fx"]["sharp_strength"]) <= 0.65
    assert float(preset["LumaSharpen.fx"]["sharp_clamp"]) <= 0.05
    grade = preset["FabulaLous.fx"]
    assert 0 < float(grade["Contrast"]) <= 0.25
    if game == "ff13":
        # FFXIII's brightest places are near white already, so the bright end
        # comes down a little, never up, and never so far it reads as a dimmer
        assert 0 < float(grade["Highlights"]) <= 0.08
        assert 0 < float(grade["Vibrance"]) <= 0.2
        assert "Haze" not in grade
    elif game == "lr":
        # LR's top is stretched up only as far as each frame has room, so the
        # bright end barely comes down, and its haze off a lit frame is gentler
        # than XIII-2's, since LR went for a more natural light
        assert 0 <= float(grade["Highlights"]) <= 0.03
        assert 0 < float(grade["Whites"]) <= 0.2 and 0 <= float(grade["Blacks"]) <= 0.05
        assert 0 < float(grade["Vibrance"]) <= 0.2
        assert 0 < float(grade["Haze"]) <= 0.45
    else:
        # XIII-2's veil coming off darkens the hazy places, so the bright end
        # only comes down enough that the S-curve doesn't clip more of it than
        # the game does, and the colour asked for goes to the slider's top,
        # which is as far as it stays inside
        assert 0 < float(grade["Highlights"]) <= 0.03
        assert 0 < float(grade["Vibrance"]) <= 0.25
        assert 0 < float(grade["Haze"]) <= 0.5
    # predication and depth edges both read the depth buffer, which nothing
    # here counts on ReShade finding in a D3D9 game
    assert preset["SMAA.fx"]["PredicationEnabled"] == "0"
    assert preset["SMAA.fx"]["EdgeDetectionType"] != "2"


def test_the_grades_own_defaults_are_the_presets():
    """
    Reset in ReShade's menu lands on the look that ships, not somewhere near it,
    XIII's in XIII and LR and XIII-2's in XIII-2
    """
    def defaults(defines):
        source = code(SHADERS / "FabulaLous.fx", defines)
        named = dict(re.findall(r"#define\s+(FABULA_\w+)\s+(-?[\d.]+)", source))
        return {m.group(1): float(named.get(m.group(2), m.group(2))) for m in re.finditer(
            r"uniform\s+float\s+(\w+)\s*<.*?>\s*=\s*([\w.-]+)\s*;", source, flags=re.S)}

    for game in PRESETS:
        values = ini((BUNDLE / PRESETS[game]).read_text(encoding="utf-8"))["FabulaLous.fx"]
        defines = definitions(values)
        assert defaults(defines) == {key: float(value) for key, value in values.items()
                                     if key != "PreprocessorDefinitions"}, game


def test_only_xiii2s_preset_turns_the_haze_on():
    """
    XIII compiles the grade as it was, one pass and nothing kept between
    frames, so it draws what it did. LR's blurs its frame small first, for the
    local contrast and the glow, and XIII-2's measures its frame first
    """
    grade = SHADERS / "FabulaLous.fx"
    xiii = ini((BUNDLE / PRESETS["ff13"]).read_text(encoding="utf-8"))
    xiii2 = ini((BUNDLE / PRESETS["ff13-2"]).read_text(encoding="utf-8"))
    lr = ini((BUNDLE / PRESETS["lr"]).read_text(encoding="utf-8"))
    assert definitions(xiii["FabulaLous.fx"]) == {} and not xiii[""]["PreprocessorDefinitions"]
    assert definitions(xiii2["FabulaLous.fx"]) == {"FABULA_HAZE": "1"}
    assert definitions(lr["FabulaLous.fx"]) == {"FABULA_LIFT": "1"}
    lifted = code(grade, {"FABULA_LIFT": "1"})
    # blurred small, read off that copy and settled, then the glow, then the grade
    assert re.findall(r"\bpass\b\s*(\w*)", lifted) == [
        "Quarter", "Across", "Down", "Cell", "Read", "Settle", "Kept",
        "Bright", "GlowAcross", "GlowDown", ""]
    assert re.findall(r"RenderTarget\s*=\s*(\w+)", lifted) == [
        "FabulaQuarterTex", "FabulaSoftTex", "FabulaQuarterTex",
        "FabulaCellTex", "FabulaReadTex", "FabulaSettledTex", "FabulaKeptTex",
        "FabulaGlowTex", "FabulaGlowSoftTex", "FabulaGlowTex"]
    # what keeps it cheap at 4K: nothing it draws into is bigger than a quarter
    # of the frame across, and what it reads the frame into is a fixed 32x18 or
    # one pixel, so its passes together touch less than one full frame
    sizes = re.findall(r"texture \w+ \{ Width = BUFFER_WIDTH / (\d+); Height = BUFFER_HEIGHT / (\d+);", lifted)
    fixed = re.findall(r"texture \w+ \{ Width = (\d+); Height = (\d+);", lifted)
    assert len(sizes) == 4 and len(fixed) == 4
    assert len(sizes) + len(fixed) == len(re.findall(r"\btexture\b", lifted))
    assert all(int(w) >= 4 and w == h for w, h in sizes)
    assert all(int(w) * int(h) <= 32 * 18 for w, h in fixed)
    # the settling creeps in steps finer than half precision, as XIII-2's does
    for name in ("FabulaReadTex", "FabulaSettledTex", "FabulaKeptTex"):
        assert re.search(rf"texture {name} \{{[^}}]*Format = RGBA32F;", lifted), name
    assert re.search(r'source\s*=\s*"frametime"', lifted), "settles by time, not by frames"
    assert {"Whites", "Blacks", "Clarity", "Glow", "Haze"} <= set(uniforms(grade, {"FABULA_LIFT": "1"}))
    assert not {"Whites", "Blacks", "Clarity", "Glow"} & set(uniforms(grade, {}))

    plain = code(grade, {})
    assert len(re.findall(r"\bpass\b", plain)) == 1 and "texture" not in plain
    assert "Haze" not in uniforms(grade, {})
    hazy = code(grade, {"FABULA_HAZE": "1"})
    # measured before the grade reads it, which is the last pass, and kept for
    # the next frame's settling
    passes = re.findall(r"\bpass\b\s*(\w*)", hazy)
    assert passes == ["Dark", "Grid", "Rows", "Cols", "Now", "Level", "Keep", ""]
    targets = re.findall(r"RenderTarget\s*=\s*(\w+)", hazy)
    assert targets == ["FabulaDarkTex", "FabulaGridTex", "FabulaRowsTex", "FabulaColsTex",
                       "FabulaNowTex", "FabulaLevelTex", "FabulaLastTex"]
    shaders = re.findall(r"PixelShader\s*=\s*(\w+)", hazy)
    assert shaders == ["FabulaDarkPass", "FabulaGridPass", "FabulaRowsPass", "FabulaColsPass",
                       "FabulaNowPass", "FabulaLevelPass", "FabulaKeepPass", "FabulaLousPass"]
    assert re.search(r'source\s*=\s*"frametime"', hazy), "settles by time, not by frames"
    # the settling creeps in steps finer than half precision has, so the level is float
    for name in ("FabulaNowTex", "FabulaLevelTex", "FabulaLastTex"):
        assert re.search(rf"texture {name} \{{[^}}]*Format = R32F;", hazy), name


def test_lr_reads_each_frame_before_it_stretches_it():
    """
    LR runs a clock, from lamplit nights to Dead Dunes at noon. A fixed stretch
    took the dunes from a third of the frame at white to half of it, so the top
    only goes up by the room a frame has, white still lands on white, the haze
    only comes off a lit frame, and the glow is a night thing
    """
    lifted = code(SHADERS / "FabulaLous.fx", {"FABULA_LIFT": "1"})
    # off the settled read only: this frame's flicked the whole frame on fast
    # pans, as sky slid in and out of a cell
    assert re.search(r"clamp\(0\.95 / max\(steady\.g, 1e-3\), 1\.0, 1\.0 / \(1\.0 - Whites\)\)", lifted)
    assert "max(frame.g, steady.g)" not in lifted
    assert "Shoulder(lit * stretch) / Shoulder(stretch)" in lifted
    assert re.search(r"smoothstep\(0\.3, 0\.42, min\(frame\.b, steady\.b\)\)", lifted)
    assert re.search(r"Glow \* \(1\.0 - smoothstep\(0\.3, 0\.55, steady\.b\)\)", lifted)
    # and a frame with nothing kept yet is read as it is, not settled from zero
    assert "kept ? eased : now.rgb" in lifted
    # white cloth in sky-lit shade keeps the tint the game gave it, mid tones only,
    # so a pale sky still goes blue as its haze comes off
    assert "lerp(1.0, keep, neutral)" in lifted
    assert "(1.0 - smoothstep(0.7, 0.85, source))" in lifted


def test_the_haze_only_comes_off_a_hazy_frame():
    """
    a frame whose darkest level is near black has no veil to give: the gate
    shuts below 8% so a dark room's shadows stay, and the most that comes off
    is a quarter, so a bright fog can't wash the frame out the other way
    """
    hazy = code(SHADERS / "FabulaLous.fx", {"FABULA_HAZE": "1"})
    assert re.search(r"Haze \* level \* smoothstep\(0\.08, 0\.2, level\), 0\.25\)", hazy)
    # on a big drop, a cut to a dark room, never more than this frame has, so
    # it isn't crushed while the settled level comes down; a small one settles,
    # so a dark thing crossing for a frame doesn't flicker the rest
    assert "float level = lerp(settled, min(settled, now + 0.02), " \
           "smoothstep(0.08, 0.16, settled - now));" in hazy
    # one lift per pixel, off its darkest channel, so hues stay; eased in from
    # black over twice the veil and scaled back up, so nothing under it clips
    # and white stays white
    assert re.search(r"lift = veil \* smoothstep\(0\.0, max\(2\.0 \* veil, 1e-5\), "
                     r"min\(color\.r, min\(color\.g, color\.b\)\)\)", hazy)
    assert re.search(r"color = \(color - lift\) / \(1\.0 - lift\)", hazy)
    # bars black right across are left out of the level, and a hitch counts as
    # a tenth of a second at most
    assert re.search(r"tex2Dlod\(FabulaRows", hazy) and re.search(r"tex2Dlod\(FabulaCols", hazy)
    assert re.search(r"clamp\(FrameTime, 0\.0, 100\.0\)", hazy)
    assert bound(uniforms(SHADERS / "FabulaLous.fx", {"FABULA_HAZE": "1"})["Haze"][1], "ui_max") <= 0.6


def test_the_haze_measures_and_settles_the_way_its_readme_says():
    """
    the numbers README.txt gives for it, which a ReShade under wine was held to:
    the darkest channel, a harmonic mean with 0.05 added, 150ms down and 400ms
    up by frame time with a hitch at 100ms at most, and the level a frame ago
    being this frame's settled one
    """
    hazy = code(SHADERS / "FabulaLous.fx", {"FABULA_HAZE": "1"})

    def body(name):
        return re.search(rf"float\d? {name}\(.*?\n}}", hazy, flags=re.S).group(0)

    dark = body("FabulaDarkPass")
    assert "low = min(low, min(c.r, min(c.g, c.b)));" in dark
    assert "origin + patch * (float2(x, y) + 0.5) * 0.25" in dark
    assert "(float2(x, y) - 1.5) / float2(128.0, 72.0)" in body("FabulaGridPass")
    assert "return sum / 16.0;" in body("FabulaGridPass")
    # a row or column is a bar when even its brightest cell is black
    assert ("top = max(top, tex2Dlod(FabulaGrid, float4((x + 0.5) / 32.0, texcoord.y, "
            "0.0, 0.0)).r);") in body("FabulaRowsPass")
    assert ("top = max(top, tex2Dlod(FabulaGrid, float4(texcoord.x, (y + 0.5) / 18.0, "
            "0.0, 0.0)).r);") in body("FabulaColsPass")
    assert "for (int x = 0; x < 32; x++)" in body("FabulaRowsPass")
    assert "for (int y = 0; y < 18; y++)" in body("FabulaColsPass")
    now = body("FabulaNowPass")
    assert now.count("for (int y = 0; y < 18; y++)") == 2
    assert now.count("for (int x = 0; x < 32; x++)") == 2
    assert now.count("> 1.0 / 255.0)") == 2
    for kept in ("low.y = min(low.y, y);", "high.y = max(high.y, y);",
                 "low.x = min(low.x, x);", "high.x = max(high.x, x);",
                 # and the row or column a bar's edge cuts through
                 "low.x += low.x > 0.0 ? 1.0 : 0.0;", "low.y += low.y > 0.0 ? 1.0 : 0.0;",
                 "high.x -= high.x < 31.0 ? 1.0 : 0.0;", "high.y -= high.y < 17.0 ? 1.0 : 0.0;",
                 "x >= low.x && x <= high.x && y >= low.y && y <= high.y ? 1.0 : 0.0"):
        assert kept in now, kept
    assert "sum += inside / (g + 0.05);" in now and "saturate(cells / sum - 0.05)" in now
    level = body("FabulaLevelPass")
    assert "now < last ? 150.0 : 400.0" in level
    assert "lerp(last, now, 1.0 - exp(-clamp(FrameTime, 0.0, 100.0) / settle))" in level
    assert re.search(r"return tex2Dlod\(FabulaLevel,", body("FabulaKeepPass"))
    assert re.search(r"float now = tex2Dlod\(FabulaNow,", body("FabulaLousPass"))


def test_the_vibrance_slider_stops_where_every_channel_stays_inside():
    """the fade over the last quarter to black or white only holds them in up to 0.25"""
    grade = SHADERS / "FabulaLous.fx"
    assert bound(uniforms(grade)["Vibrance"][1], "ui_max") <= 0.25
    assert re.search(r"saturate\(4\.0 \* min\(bottom", code(grade)), "the quarter the fade runs over"


def test_reshade_ini_reads_everything_from_its_own_folder():
    settings = ini((BUNDLE / "ReShade.ini").read_text(encoding="utf-8"))
    general = settings["GENERAL"]
    targets = {s["target"] for s in the_patch().bundled_files}
    assert general["PresetPath"] == f".\\{PRESET}" and f"ReShade/{PRESET}" in targets
    assert general["EffectSearchPaths"] == ".\\reshade-shaders\\Shaders\\**"
    assert general["TextureSearchPaths"] == ".\\reshade-shaders\\Textures\\**"
    assert general["PerformanceMode"] == "1"
    assert settings["INPUT"]["KeyOverlay"].split(",")[0] == "36", "Home, what the drawer says"
    # past the tutorial, so the first Home opens on the effects
    assert settings["OVERLAY"]["TutorialProgress"] == "4"


@pytest.mark.parametrize("game", list(PRESETS))
def test_everything_the_effects_pull_in_ships_with_them(game):
    targets = {s["target"] for s in the_patch(GAMES[game][0]).bundled_files}
    preset = ini((BUNDLE / PRESETS[game]).read_text(encoding="utf-8"))
    used = [entry.partition("@")[2] for entry in preset[""]["Techniques"].split(",")]
    for effect in used:
        assert f"ReShade/reshade-shaders/Shaders/{effect}" in targets
        source = code(SHADERS / effect, definitions(preset.get(effect, {})))
        for header in re.findall(r'#include\s+"([^"]+)"', source):
            assert f"ReShade/reshade-shaders/Shaders/{header}" in targets, (effect, header)
        # a texture's source is a file to ship; a uniform's is ReShade's own
        for picture in re.findall(r'texture\s+\w+\s*<[^>]*?source\s*=\s*"([^"]+)"', source):
            assert f"ReShade/reshade-shaders/Textures/{picture}" in targets, (effect, picture)
    assert "ReShade/reshade-shaders/LICENSE-SweetFX.txt" in targets
    assert all(s["config"] for s in the_patch().bundled_files
               if s["target"].endswith(".ini")), "both settings files are the user's once changed"
    assert [s.get("foreign") for s in the_patch().bundled_files
            if s["target"] == "ReShade/d3d9.dll"] == ["refuse"]


def test_in_mods_and_foreign_only_go_where_they_mean_something(tmp_path):
    def manifest(**patch):
        path = tmp_path / "m.json"
        path.write_text(json.dumps({
            "game_id": "g", "display_name": "G", "manifest_version": "1",
            "executable_relative_path": "g.exe",
            "patches": [dict({"id": "p", "label": "P", "default_enabled": False}, **patch)]}))
        return path

    def bundled(**spec):
        return {"type": "bundled_install", "install_relative_path": ".",
                "bundled_files": [dict({"source": "a/b.dll", "target": "X/b.dll"}, **spec)]}

    with pytest.raises(ValueError):
        load_manifest(manifest(type="manual", manual_url="https://x.test", in_mods=True))
    with pytest.raises(ValueError):
        load_manifest(manifest(**bundled(foreign="replace")))
    with pytest.raises(ValueError):
        load_manifest(manifest(**bundled(target="../b.dll")))
    patch = load_manifest(manifest(in_mods=True, **bundled(foreign="refuse"))).patches[0]
    assert patch.in_mods and patch.bundled_files[0]["foreign"] == "refuse"


def test_a_drawer_reshade_of_the_same_build_keeps_apply_off_it(each):
    """
    same bytes as ours is still the drawer's: its Remove takes that dll with
    it, and ours over it would leave two rows owning one file. On LR the
    drawer's folder and Apply's are both the game's root
    """
    game, bridge, home, tmp = each
    drop = dict(package(), **{"FF13_Clean/d3d9.dll": OURS, "FF13_Clean/ReShade32.dll": OURS})
    assert bridge.install_mod(game, str(zipped(tmp, "same.zip", drop)))["ok"]
    before = {p.relative_to(home).as_posix(): p.read_bytes() for p in home.rglob("*") if p.is_file()}

    result = bridge.apply(game, ["reshade"])["results"][0]

    assert result["status"] == PatchStatus.SKIPPED and "from the Mods drawer" in result["detail"]
    assert {p.relative_to(home).as_posix(): p.read_bytes()
            for p in home.rglob("*") if p.is_file()} == before
    assert [m["kind"] for m in bridge.list_addons(game)["mods"]] == ["loader"]


def test_ours_with_its_record_gone_is_still_listed_and_comes_off(each):
    """the state file is in AppData, which a new PC loses while the game keeps its folder"""
    game, bridge, home, tmp = each
    bridge.apply(game, ["reshade"])
    saved = (home / "ReShade.ini").read_bytes() + b"\r\n[STYLE]\r\nAlpha=1.000000\r\n"
    (home / "ReShade.ini").write_bytes(saved)
    (tmp / ".state" / f"{GAMES[game][0]}.json").unlink()

    assert installed_row(bridge, game)["name"] == NAME
    done = bridge.remove_addon(game, NAME, "patch")

    assert done["ok"] and done["message"] == "ReShade.ini changed since it went in, so it stays."
    assert sorted(p.name for p in home.iterdir()) == ["ReShade.ini"]
    assert (home / "ReShade.ini").read_bytes() == saved
    assert installed_row(bridge, game) is None


def test_a_reshade_that_wont_load_says_so(each):
    """NovaFix only takes ReShade\\d3d9.dll when it picks the renderer itself"""
    game, bridge, home, _ = each
    (home.parent / "NovaFix.ini").write_text("[Performance]\r\nD3D9Backend=System\r\n")
    result = bridge.apply(game, ["reshade"])["results"][0]
    assert result["ok"] and result["detail"].endswith(
        "Set Renderer back to Automatic in Settings for it to load.")
    assert installed_row(bridge, game)["about"] == \
        "Set Renderer back to Automatic in Settings for it to load."
    # and a chain dll beside NovaFix, which its Automatic takes ahead of ReShade
    (home.parent / "NovaFix.ini").unlink()
    (home.parent / "d3d9_chain.dll").write_bytes(b"MZ chain")
    assert installed_row(bridge, game)["about"].startswith("NovaFix loads d3d9_chain.dll")


def renderer(home) -> str:
    """the renderer NovaFix.ini beside the ReShade folder asks for, as written"""
    return novafix_config.profile(home.parent / "NovaFix.ini")["performance"]["d3d9backend"]


@pytest.mark.parametrize("written, loads", [("1", False), ("2", False), ("7", False),
                                             ("0", True), ("automatic", True)])
def test_the_numbers_an_older_novafix_wrote_for_the_renderer_count(world, written, loads):
    """
    NovaFix still reads a number there, clamped: 1 is System, anything past it
    LocalDXVK, which with dxvk.dll beside it is DXVK somebody put there
    """
    bridge, home, _ = world
    (home.parent / "dxvk.dll").write_bytes(b"MZ dxvk")
    (home.parent / "NovaFix.ini").write_text(f"[Performance]\r\nD3D9Backend={written}\r\n")
    result = bridge.apply("ff13", ["reshade"])["results"][0]
    assert result["ok"]
    blocked = result["detail"].endswith("Set Renderer back to Automatic in Settings for it to load.")
    assert blocked is not loads, result["detail"]
    assert renderer(home) == written


@pytest.mark.parametrize("written", ["LocalDXVK", "2", "7"])
def test_dxvk_with_no_dxvk_dll_goes_back_to_automatic_with_the_shader(each, written):
    """
    the XIII-2 the shader never loaded in: Renderer on DXVK and no dxvk.dll, so
    NovaFix ran Windows' D3D9 and never looked for ReShade. Apply with the
    shader puts it on Automatic, which loses nothing, and says so
    """
    game, bridge, home, _ = each
    ini_path = home.parent / "NovaFix.ini"
    ini_path.write_bytes(f"[Performance]\r\nShowFpsCounter=true\r\nD3D9Backend={written}\r\n".encode())
    assert installed_row(bridge, game) is None

    result = bridge.apply(game, ["reshade"])["results"][0]

    assert result["ok"], result
    assert result["detail"] == ("installed 15 files into ReShade. Renderer was on DXVK with no "
                                "dxvk.dll beside NovaFix, so the game ran on Windows D3D9 anyway. "
                                "It's on Automatic now, which is what loads the shader."), result
    assert renderer(home) == "Automatic"
    assert novafix_config.profile(ini_path)["performance"]["showfpscounter"] == "true", "only that line"
    assert installed_row(bridge, game)["about"] != \
        "Set Renderer back to Automatic in Settings for it to load."


def test_windows_d3d9_picked_stays_picked(world):
    """System is somebody's choice that works, so it's said rather than changed"""
    bridge, home, _ = world
    (home.parent / "NovaFix.ini").write_text("[Performance]\r\nD3D9Backend=System\r\n")
    result = bridge.apply("ff13", ["reshade"])["results"][0]
    assert result["detail"] == ("installed 15 files into ReShade. Set Renderer back to Automatic "
                                "in Settings for it to load.")
    assert renderer(home) == "System"


def test_a_dxvk_picked_after_the_shader_went_in_stays_picked(world):
    """
    the drawer offers DXVK to somebody about to put a dxvk.dll in. An Apply for
    anything else, the shader still ticked, leaves it and says what it costs
    """
    bridge, home, _ = world
    assert bridge.apply("ff13", ["reshade"])["results"][0]["status"] == PatchStatus.APPLIED
    assert bridge.save_novafix_settings("ff13", {"Performance": {"D3D9Backend": "LocalDXVK"}})["ok"]

    again = bridge.apply("ff13", ["reshade"])["results"][0]

    assert again["status"] == PatchStatus.ALREADY_APPLIED
    assert renderer(home) == "LocalDXVK"
    assert again["detail"].endswith("Set Renderer back to Automatic in Settings for it to load.")


@pytest.mark.parametrize("written", ["LocalDXVK", "System"])
def test_a_chain_dll_keeps_the_renderer_and_both_steps_are_said(world, written):
    """
    neither renderer loads d3d9_chain.dll and Automatic loads it first, so
    Automatic would wake it, and taking the chain away alone leaves the shader off
    """
    bridge, home, _ = world
    (home.parent / "d3d9_chain.dll").write_bytes(b"MZ chain")
    (home.parent / "NovaFix.ini").write_text(f"[Performance]\r\nD3D9Backend={written}\r\n")
    result = bridge.apply("ff13", ["reshade"])["results"][0]
    assert renderer(home) == written
    assert result["detail"] == ("installed 15 files into ReShade. Set Renderer back to Automatic "
                                "in Settings and remove d3d9_chain.dll, which Automatic loads "
                                "first, for it to load.")


def test_a_read_only_novafix_ini_on_dxvk_says_what_to_do_by_hand(world):
    import os
    import stat

    bridge, home, _ = world
    ini = home.parent / "NovaFix.ini"
    ini.write_text("[Performance]\r\nD3D9Backend=LocalDXVK\r\n")
    os.chmod(ini, stat.S_IREAD)
    try:
        result = bridge.apply("ff13", ["reshade"])["results"][0]
    finally:
        os.chmod(ini, stat.S_IREAD | stat.S_IWRITE)
    assert result["status"] == PatchStatus.APPLIED
    assert renderer(home) == "LocalDXVK"
    assert result["detail"].endswith("Set Renderer back to Automatic in Settings for it to load.")


def test_a_novafix_ini_windows_wont_hand_over_leaves_the_apply_standing(world, monkeypatch):
    """another program's lock on it: nothing to say about the renderer, and the shader's in"""
    bridge, home, _ = world
    (home.parent / "NovaFix.ini").write_text("[Performance]\r\nD3D9Backend=LocalDXVK\r\n")
    real = Path.read_bytes

    def locked(self):
        if self.name == "NovaFix.ini":
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    result = bridge.apply("ff13", ["reshade"])["results"][0]
    assert result["status"] == PatchStatus.APPLIED
    assert result["detail"] == "installed 15 files into ReShade", result["detail"]


def test_a_utf16_novafix_ini_on_dxvk_is_seen_and_stays_utf16(world):
    """Notepad's UTF-16, which Windows reads by its BOM: read as UTF-8 it had no renderer at all"""
    bridge, home, _ = world
    ini = home.parent / "NovaFix.ini"
    ini.write_bytes(b"\xff\xfe" + "[Performance]\r\nShowFpsCounter=On\r\nD3D9Backend=LocalDXVK\r\n"
                    .encode("utf-16-le"))
    result = bridge.apply("ff13", ["reshade"])["results"][0]
    assert result["detail"].endswith("It's on Automatic now, which is what loads the shader.")
    raw = ini.read_bytes()
    assert raw.startswith(b"\xff\xfe")
    assert "D3D9Backend=Automatic\r\n" in raw[2:].decode("utf-16-le")
    assert renderer(home) == "Automatic"


def test_a_renderer_novafix_cant_read_is_its_default(world):
    """"²" passes isdigit() and int() refuses it, which took the whole Apply down"""
    bridge, home, _ = world
    (home.parent / "NovaFix.ini").write_bytes("[Performance]\r\nD3D9Backend=²\r\n".encode())
    result = bridge.apply("ff13", ["reshade"])["results"][0]
    assert result["ok"] and result["detail"] == "installed 15 files into ReShade", result
    assert renderer(home) == "²"


def test_the_xiii2_it_never_loaded_on_takes_its_own_preset_and_automatic(tmp_path, monkeypatch):
    """
    the user's: in from src_36 with XIII's preset as it shipped, never once
    loaded, since the Renderer was on DXVK with no dxvk.dll. The next Apply puts
    XIII-2's preset over the untouched one, and Automatic in
    """
    bridge, home, tmp = build(tmp_path, monkeypatch, "ff13-2")
    bridge.apply("ff13-2", ["reshade"])
    xiii, older = (BUNDLE / PRESETS["ff13"]).read_bytes(), b"// the grade src_36 shipped\r\n"
    (home / PRESET).write_bytes(xiii)
    (home / GRADE).write_bytes(older)
    state, data = record_of(tmp, "ff13-2")
    record = data["applied_patches"]["install_reshade"]
    record["digests"][f"ReShade/{PRESET}"] = sha(xiii)
    record["digests"][f"ReShade/{GRADE}"] = sha(older)
    state.write_text(json.dumps(data), encoding="utf-8")
    (home.parent / "NovaFix.ini").write_text("[Performance]\r\nD3D9Backend=LocalDXVK\r\n")
    assert "reshade" not in bridge.scan("ff13-2")["applied"]

    result = bridge.apply("ff13-2", ["reshade"])["results"][0]

    assert result["status"] == PatchStatus.APPLIED, result
    assert result["detail"].endswith("It's on Automatic now, which is what loads the shader.")
    assert (home / PRESET).read_bytes() == shipped("ff13-2")
    assert renderer(home) == "Automatic"


def test_a_new_settings_file_somebody_edited_is_left_alone(world):
    bridge, home, tmp = world
    bridge.apply("ff13", ["reshade"])
    (home / "ReShade.ini").write_bytes(b"[GENERAL]\r\nPresetPath=.\\Mine.ini\r\n")
    assert bridge.remove_addon("ff13", NAME, "patch")["ok"]
    assert bridge.apply("ff13", ["reshade"])["ok"]
    edited = (home / "ReShade.ini.new").read_bytes() + b"\r\n; mine now\r\n"
    (home / "ReShade.ini.new").write_bytes(edited)
    # and ours has moved on since that .new went in, so there's one to offer
    state, data = record_of(tmp)
    data["applied_patches"]["install_reshade"]["digests"]["ReShade/ReShade.ini.new"] = sha(b"older")
    state.write_text(json.dumps(data), encoding="utf-8")
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()

    result = bridge.apply("ff13", ["reshade"])["results"][0]

    assert result["ok"], result
    assert (home / "ReShade.ini.new").read_bytes() == edited
    assert "ReShade.ini.new stay as you had them" in result["detail"]


def test_an_older_applys_dlc_restoration_is_listed_and_comes_off(tmp_path, monkeypatch):
    """it was in Apply's list, and that row is gone: Installed is where it comes off now"""
    import patcher.bridge as bridge_mod
    from patcher.state import record_applied
    from tests.test_mod_drops import library_with

    library, _, paths = library_with(tmp_path)
    lr = library / "steamapps" / "common" / "FINAL FANTASY XIII-3"
    lr.mkdir(parents=True)
    (library / "steamapps" / "appmanifest_345350.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"345350"\n\t"installdir"\t\t"FINAL FANTASY XIII-3"\n}\n')
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    manifest = load_manifest(REPO / "manifests" / "ffxiii3.json")
    names = [f"c{n}.win32.imgb" for n in range(5)]
    for name in names:
        (lr / name).write_bytes(name.encode())
    record_applied(tmp_path / ".state" / f"{manifest.game_id}.json", "restore_outerworld_addons",
                   names, {"dir": ".", "digests": {n: sha(n.encode()) for n in names}})
    exe = lr / manifest.executable_relative_path
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(make_export_pe([]))
    bridge = bridge_mod.UiBridge(
        manifest_paths=dict(paths, ffxiii3=REPO / "manifests" / "ffxiii3.json"),
        steam_library_paths=[library])

    row = installed_row_for(bridge, "lr")
    assert row and row["name"] == "DLC Restoration" and row["jobId"] == "dlcrestore"
    done = bridge.remove_addon("lr", "DLC Restoration", "patch")
    assert done["ok"], done
    assert not any((lr / n).exists() for n in names)
    assert installed_row_for(bridge, "lr") is None


def installed_row_for(bridge, game):
    rows = [m for m in bridge.list_addons(game).get("mods", []) if m["kind"] == "patch"]
    return rows[0] if rows else None


def test_a_count_of_files_only_names_a_folder_they_share():
    from patcher.engine import _into

    assert _into(["ReShade/a", "ReShade/b/c"]) == " into ReShade"
    assert _into(["a.imgb", "b.imgb"]) == ""
    assert _into(["ReShade/a", "Other/b"]) == ""
    assert _into(["ReShade/a", "loose.ini"]) == ""


def test_remove_waits_for_apply(tmp_path):
    """a revert writes the state file Apply is writing, so the two take turns"""
    import shell

    api = shell.Api()
    api._patcher = lambda: pytest.fail("reached the bridge while Apply held the lock")
    api._running = lambda game: ""
    assert api._busy.acquire(blocking=False)
    try:
        done = api.remove_addon("ff13", NAME, "patch")
    finally:
        api._busy.release()
    assert not done["ok"] and "once that's done" in done["error"]


def test_ours_adopted_puts_back_what_it_set_aside_and_takes_its_new_file(each):
    """the same Remove as with the record, from what's on the disk"""
    game, bridge, home, tmp = each
    (home / "reshade-shaders" / "Shaders").mkdir(parents=True)
    mine = b"// my own curves"
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").write_bytes(mine)
    assert bridge.apply(game, ["reshade"])["ok"]
    (home / "ReShade.ini").write_bytes(b"[GENERAL]\r\nPresetPath=.\\Mine.ini\r\n")
    assert bridge.remove_addon(game, NAME, "patch")["ok"]
    assert bridge.apply(game, ["reshade"])["ok"]
    assert (home / "ReShade.ini.new").is_file()
    (tmp / ".state" / f"{GAMES[game][0]}.json").unlink()

    assert bridge.remove_addon(game, NAME, "patch")["ok"]

    left = sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())
    assert left == ["ReShade.ini", "reshade-shaders/Shaders/Curves.fx"]
    assert (home / "reshade-shaders" / "Shaders" / "Curves.fx").read_bytes() == mine


def record_of(tmp, game="ff13") -> tuple:
    """the state file and what's in it"""
    state = tmp / ".state" / f"{GAMES[game][0]}.json"
    return state, json.loads(state.read_text(encoding="utf-8"))


# the preset before the grade was an effect of its own, near enough: what
# counts is that the record says it's the one that went in
OLD_PRESET = "\r\n".join([
    "PreprocessorDefinitions=",
    "Techniques=SMAA@SMAA.fx,LumaSharpen@LumaSharpen.fx,Curves@Curves.fx,Vibrance@Vibrance.fx",
    "TechniqueSorting=SMAA@SMAA.fx,LumaSharpen@LumaSharpen.fx,Curves@Curves.fx,Vibrance@Vibrance.fx",
    "", "[Curves.fx]", "Contrast=0.150000", "Formula=2", "Mode=0",
    "", "[Vibrance.fx]", "Vibrance=0.120000", ""]).encode()
GRADE = "reshade-shaders/Shaders/FabulaLous.fx"


def from_before_the_grade(game, home, tmp, tweak: bytes = b"") -> bytes:
    """
    what the last release left after a game: its preset, tweaked or not, a
    record that says that's the one it put in, no FabulaLous.fx, and settings
    ReShade wrote back on exit
    """
    (home / PRESET).write_bytes(OLD_PRESET + tweak)
    (home / GRADE).unlink()
    (home / "ReShade.ini").write_bytes((home / "ReShade.ini").read_bytes()
                                       + b"\r\n[STYLE]\r\nAlpha=1.000000\r\n")
    state, data = record_of(tmp, game)
    record = data["applied_patches"]["install_reshade"]
    record["digests"][f"ReShade/{PRESET}"] = sha(OLD_PRESET)
    del record["digests"][f"ReShade/{GRADE}"]
    record["files"].remove(f"ReShade/{GRADE}")
    state.write_text(json.dumps(data), encoding="utf-8")
    return OLD_PRESET + tweak


def test_an_install_from_before_the_grade_takes_it_with_nothing_set_beside(each):
    """
    the preset untouched since it went in is ours, so the new one goes over it.
    ReShade.ini changed, but only by ReShade, and ours is the version that went
    in: a .new of it would be a copy of what they started from
    """
    game, bridge, home, tmp = each
    bridge.apply(game, ["reshade"])
    from_before_the_grade(game, home, tmp)
    settings = (home / "ReShade.ini").read_bytes()
    assert "reshade" not in bridge.scan(game)["applied"], "the grade isn't there yet"

    result = bridge.apply(game, ["reshade"])["results"][0]

    assert result["ok"] and ".new" not in result["detail"], result
    assert (home / PRESET).read_bytes() == shipped(game)
    assert (home / GRADE).is_file()
    assert (home / "ReShade.ini").read_bytes() == settings
    assert not list(home.rglob("*.new"))
    assert "reshade" in bridge.scan(game)["applied"]

    # the record says which version theirs is on, so the run after knows too
    (home / GRADE).unlink()
    assert bridge.apply(game, ["reshade"])["ok"]
    assert not list(home.rglob("*.new"))


def test_a_preset_tweaked_before_the_grade_stays_with_the_new_one_beside_it(world):
    bridge, home, tmp = world
    bridge.apply("ff13", ["reshade"])
    mine = from_before_the_grade("ff13", home, tmp, tweak=b"\r\n[Vibrance.fx]\r\nVibrance=0.3\r\n")

    result = bridge.apply("ff13", ["reshade"])["results"][0]

    assert result["ok"], result
    assert (home / PRESET).read_bytes() == mine
    assert (home / f"{PRESET}.new").read_bytes() == shipped("ff13")
    assert f"with this version beside it as {PRESET}.new" in result["detail"]
    assert not (home / "ReShade.ini.new").exists()


# what ReShade adds to its settings the first time it exits
SAVED = b"\r\n[STYLE]\r\nAlpha=1.000000\r\n"


@pytest.mark.parametrize("renamed_first", [False, True])
def test_an_install_from_the_crystarium_release_gets_no_new_settings_beside_it(each, renamed_first):
    """
    that release's ReShade.ini was today's with the preset's old name in it, and
    the rename only pointed theirs at the new one: the same version of ours
    """
    game, bridge, home, tmp = each
    bridge.apply(game, ["reshade"])
    from_before_the_grade(game, home, tmp)
    then = (BUNDLE / "ReShade.ini").read_bytes().replace(PRESET.encode(), b"Crystarium.ini")
    (home / "ReShade.ini").write_bytes(then + SAVED)
    (home / PRESET).rename(home / "Crystarium.ini")
    state, data = record_of(tmp, game)
    record = data["applied_patches"]["install_reshade"]
    record["files"] = [f.replace(PRESET, "Crystarium.ini") for f in record["files"]]
    record["digests"] = {k.replace(PRESET, "Crystarium.ini"): v for k, v in record["digests"].items()}
    record["digests"]["ReShade/ReShade.ini"] = sha(then)
    state.write_text(json.dumps(data), encoding="utf-8")
    if renamed_first:
        bridge.list_addons(game)

    result = bridge.apply(game, ["reshade"])["results"][0]

    assert result["ok"] and ".new" not in result["detail"], result
    assert not list(home.rglob("*.new")) and not (home / "Crystarium.ini").exists()
    assert (home / PRESET).read_bytes() == shipped(game)
    assert (home / "ReShade.ini").read_bytes() == depth_off((then + SAVED).replace(
        b"Crystarium.ini", PRESET.encode()))


def test_a_new_one_somebody_edited_beside_the_same_version_survives_remove(world):
    bridge, home, _ = world
    bridge.apply("ff13", ["reshade"])
    (home / "ReShade.ini").write_bytes((home / "ReShade.ini").read_bytes() + SAVED)
    assert bridge.remove_addon("ff13", NAME, "patch")["ok"]
    assert bridge.apply("ff13", ["reshade"])["ok"]
    edited = (home / "ReShade.ini.new").read_bytes() + b"\r\n; mine now\r\n"
    (home / "ReShade.ini.new").write_bytes(edited)
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()
    assert bridge.apply("ff13", ["reshade"])["ok"]

    assert bridge.remove_addon("ff13", NAME, "patch")["ok"]

    assert (home / "ReShade.ini.new").read_bytes() == edited


@pytest.mark.parametrize("how", ["deleted", "ours copied over"])
def test_a_new_one_with_nothing_left_to_stand_beside_goes(world, how):
    bridge, home, _ = world
    bridge.apply("ff13", ["reshade"])
    (home / "ReShade.ini").write_bytes((home / "ReShade.ini").read_bytes() + SAVED)
    assert bridge.remove_addon("ff13", NAME, "patch")["ok"]
    assert bridge.apply("ff13", ["reshade"])["ok"]
    if how == "deleted":
        (home / "ReShade.ini").unlink()
    else:
        (home / "ReShade.ini").write_bytes((home / "ReShade.ini.new").read_bytes())
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()

    assert bridge.apply("ff13", ["reshade"])["ok"]

    assert not (home / "ReShade.ini.new").exists()
    assert (home / "ReShade.ini").read_bytes() == depth_off((BUNDLE / "ReShade.ini").read_bytes())
    assert bridge.remove_addon("ff13", NAME, "patch")["ok"]
    assert not home.exists()


def ours_beside_then_theirs_gone(bridge, home, how):
    """theirs, ours beside it as .new, then theirs deleted or ours copied over it"""
    bridge.apply("ff13", ["reshade"])
    (home / "ReShade.ini").write_bytes((home / "ReShade.ini").read_bytes() + SAVED)
    assert bridge.remove_addon("ff13", NAME, "patch")["ok"]
    assert bridge.apply("ff13", ["reshade"])["ok"]
    if how == "deleted":
        (home / "ReShade.ini").unlink()
    else:
        (home / "ReShade.ini").write_bytes((home / "ReShade.ini.new").read_bytes())
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()
    return (home / "ReShade.ini.new").read_bytes()


@pytest.mark.parametrize("how", ["deleted", "ours copied over"])
def test_a_new_one_somebody_edited_stays_when_theirs_goes(world, how):
    bridge, home, _ = world
    ours_beside_then_theirs_gone(bridge, home, how)
    edited = (home / "ReShade.ini.new").read_bytes() + b"\r\n; mine now\r\n"
    (home / "ReShade.ini.new").write_bytes(edited)

    assert bridge.apply("ff13", ["reshade"])["ok"]

    assert (home / "ReShade.ini.new").read_bytes() == edited


def test_a_run_that_stops_puts_back_the_new_one_it_took(world, monkeypatch):
    import patcher.fileops as fileops

    bridge, home, _ = world
    new = ours_beside_then_theirs_gone(bridge, home, "deleted")
    real = fileops.write_atomic

    def locked(target, data, durable=False):
        if Path(target).name == "Curves.fx":
            raise PermissionError(13, "locked", str(target))
        return real(target, data, durable)
    monkeypatch.setattr(fileops, "write_atomic", locked)

    assert not bridge.apply("ff13", ["reshade"])["ok"]

    assert (home / "ReShade.ini.new").read_bytes() == new


def test_a_new_one_that_wont_delete_stays_on_the_record_for_remove(world, monkeypatch):
    import patcher.engine as engine_mod

    bridge, home, _ = world
    ours_beside_then_theirs_gone(bridge, home, "deleted")
    real = engine_mod.remove_file
    monkeypatch.setattr(engine_mod, "remove_file",
                        lambda path: not Path(path).name.endswith(".new") and real(path))
    assert bridge.apply("ff13", ["reshade"])["ok"]
    monkeypatch.setattr(engine_mod, "remove_file", real)

    assert bridge.remove_addon("ff13", NAME, "patch")["ok"]

    assert not home.exists()


@pytest.mark.parametrize("theirs", ["changed again", "gone"])
def test_a_new_one_that_cant_be_read_is_left_alone(world, monkeypatch, theirs):
    bridge, home, _ = world
    ours_beside_then_theirs_gone(bridge, home, "ours copied over" if theirs == "changed again" else "deleted")
    if theirs == "changed again":
        (home / "ReShade.ini").write_bytes((home / "ReShade.ini").read_bytes() + SAVED)
    real = Path.read_bytes

    def held(self):
        if self.name == "ReShade.ini.new":
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", held)

    result = bridge.apply("ff13", ["reshade"])["results"][0]

    assert result["ok"], result
    monkeypatch.setattr(Path, "read_bytes", real)
    assert (home / "ReShade.ini.new").is_file()


def test_settings_back_to_ours_come_off_with_remove(world):
    bridge, home, _ = world
    bridge.apply("ff13", ["reshade"])
    (home / "ReShade.ini").write_bytes((home / "ReShade.ini").read_bytes() + SAVED)
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()
    assert bridge.apply("ff13", ["reshade"])["ok"]
    (home / "ReShade.ini").write_bytes((BUNDLE / "ReShade.ini").read_bytes())

    done = bridge.remove_addon("ff13", NAME, "patch")

    assert done["ok"] and done["message"] == f"Every file {NAME} put in is gone.", done
    assert not home.exists()


def test_settings_from_before_any_rename_count_as_the_version_that_went_in():
    """one candidate per name a preset had, BOM or not"""
    import dataclasses

    from patcher.engine import _before_rename

    renamed = [dict(spec, was=["ReShade/Crystarium.ini", "ReShade/Middle.ini"])
               if spec["target"] == f"ReShade/{PRESET}" else spec for spec in the_patch().bundled_files]
    patch = dataclasses.replace(the_patch(), bundled_files=renamed)
    now = f"[GENERAL]\r\nPresetPath=.\\{PRESET}\r\n".encode()
    for bom in (b"", b"\xef\xbb\xbf"):
        assert _before_rename(patch, bom + now) == [
            bom + now.replace(PRESET.encode(), name) for name in (b"Crystarium.ini", b"Middle.ini")]
    assert _before_rename(patch, b"[GENERAL]\r\nPresetPath=.\\Mine.ini\r\n") == []


def test_remove_after_an_update_says_the_settings_stay(each):
    game, bridge, home, _ = each
    bridge.apply(game, ["reshade"])
    saved = (home / "ReShade.ini").read_bytes() + SAVED
    (home / "ReShade.ini").write_bytes(saved)
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()
    assert bridge.apply(game, ["reshade"])["ok"]

    done = bridge.remove_addon(game, NAME, "patch")

    assert done["ok"] and done["message"] == "ReShade.ini changed since it went in, so it stays."
    assert sorted(p.name for p in home.iterdir()) == ["ReShade.ini"]
    assert (home / "ReShade.ini").read_bytes() == saved


def from_before_the_rename(game, home, tmp, tweak: bytes = b"", saved: bytes = b"") -> tuple:
    """
    what the last release left after a game or two: the preset under the name
    it shipped as, with whatever ReShade saved into it, settings ReShade wrote
    back pointing at it, and a record that lists the old name
    """
    old = home / "Crystarium.ini"
    preset = (home / PRESET).read_bytes() + tweak
    (home / PRESET).unlink()
    old.write_bytes(preset)
    settings = (home / "ReShade.ini").read_bytes().replace(PRESET.encode(),
                                                           b"Crystarium.ini") + saved
    (home / "ReShade.ini").write_bytes(settings)
    state = tmp / ".state" / f"{GAMES[game][0]}.json"
    state.write_text(state.read_text(encoding="utf-8").replace(PRESET, "Crystarium.ini"),
                     encoding="utf-8")
    return preset, settings


def test_an_install_from_before_the_rename_moves_over_with_its_tweaks(each):
    """
    the preset shipped as Crystarium.ini. ReShade saves into it as it's tweaked
    and rewrites ReShade.ini on every exit, so both are the user's by now: every
    byte comes along under the new name, and the settings follow it there
    """
    game, bridge, home, tmp = each
    bridge.apply(game, ["reshade"])
    preset, settings = from_before_the_rename(
        game, home, tmp, tweak=b"\r\n[Vibrance.fx]\r\nVibrance=0.300000\r\n",
        saved=b"\r\n[STYLE]\r\nAlpha=1.000000\r\n")

    assert "reshade" in bridge.scan(game)["applied"], "the old name is still the install"
    assert (home / "Crystarium.ini").is_file(), "and a scan only reads"

    assert installed_row(bridge, game)["about"] == "ReShade preset, Home opens its menu"
    assert not (home / "Crystarium.ini").exists()
    assert (home / PRESET).read_bytes() == preset
    assert (home / "ReShade.ini").read_bytes() == settings.replace(b"Crystarium.ini",
                                                                   PRESET.encode())
    assert "reshade" in bridge.scan(game)["applied"]

    # the record went with it, so Remove still knows what's ours and what changed
    done = bridge.remove_addon(game, NAME, "patch")
    assert done["ok"] and done["message"] == (
        f"{PRESET} and ReShade.ini changed since it went in, so they stay."), done
    assert sorted(p.name for p in home.iterdir()) == sorted([PRESET, "ReShade.ini"])


def test_apply_over_one_from_before_the_rename_leaves_one_preset(each):
    game, bridge, home, tmp = each
    bridge.apply(game, ["reshade"])
    from_before_the_rename(game, home, tmp)
    # a shader gone, so Apply has the whole thing to put on again
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()
    assert "reshade" not in bridge.scan(game)["applied"]

    assert bridge.apply(game, ["reshade"])["ok"]

    assert not (home / "Crystarium.ini").exists()
    assert (home / PRESET).read_bytes() == shipped(game)
    assert (home / "ReShade.ini").read_bytes() == depth_off((BUNDLE / "ReShade.ini").read_bytes())
    assert not list(home.glob("*.new")), "nothing set beside what was ours already"

    assert bridge.remove_addon(game, NAME, "patch")["ok"]
    assert not home.exists()


def test_an_apply_that_stops_short_puts_the_old_names_back(world, monkeypatch):
    """
    its record is only written for a run that finishes, so files moved for one
    that doesn't were ours under names nothing knew, and Remove left them
    """
    import patcher.fileops as fileops
    from patcher.engine import apply_bundled_install

    bridge, home, tmp = world
    bridge.apply("ff13", ["reshade"])
    preset, settings = from_before_the_rename("ff13", home, tmp)
    (home / "reshade-shaders" / "Shaders" / "Curves.fx").unlink()
    real = fileops.write_atomic

    def locked(target, data, durable=False):
        if Path(target).name == "d3d9.dll" and Path(target).parent.name == "ReShade":
            raise PermissionError(13, "The process cannot access the file", str(target))
        return real(target, data, durable)
    monkeypatch.setattr(fileops, "write_atomic", locked)
    manifest = bridge._manifests["ff13"]
    patch = next(p for p in manifest.patches if p.id == "install_reshade")

    done = apply_bundled_install(bridge.install_dir("ff13"), manifest, patch)

    assert done.status == PatchStatus.FAILED, done.detail
    assert (home / "Crystarium.ini").read_bytes() == preset and not (home / PRESET).exists()
    assert (home / "ReShade.ini").read_bytes() == settings

    # and through the window, where the end of Apply moves them again, record and all
    assert bridge.apply("ff13", ["reshade"])["results"][0]["status"] != "APPLIED"
    monkeypatch.setattr(fileops, "write_atomic", real)
    done = bridge.remove_addon("ff13", NAME, "patch")
    assert done["ok"] and done["message"] == f"Every file {NAME} put in is gone.", done
    assert not home.exists()


def test_a_record_left_on_the_old_names_follows_the_files(world):
    """the files moved and the record never got written: a crash mid Apply"""
    bridge, home, tmp = world
    bridge.apply("ff13", ["reshade"])
    from_before_the_rename("ff13", home, tmp)
    (home / "Crystarium.ini").rename(home / PRESET)
    (home / "ReShade.ini").write_bytes((BUNDLE / "ReShade.ini").read_bytes())

    bridge.list_addons("ff13")
    done = bridge.remove_addon("ff13", NAME, "patch")

    assert done["ok"] and done["message"] == f"Every file {NAME} put in is gone.", done
    assert not home.exists()


def test_the_settings_line_the_rename_changed_isnt_the_users_change(world):
    """the last release's ReShade.ini as it shipped, which named the old preset"""
    import hashlib
    import json

    bridge, home, tmp = world
    bridge.apply("ff13", ["reshade"])
    _, settings = from_before_the_rename("ff13", home, tmp)
    state_file = tmp / ".state" / "ffxiii.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["applied_patches"]["install_reshade"]["digests"]["ReShade/ReShade.ini"] = \
        hashlib.sha256(settings).hexdigest()
    state_file.write_text(json.dumps(state))

    bridge.list_addons("ff13")
    done = bridge.remove_addon("ff13", NAME, "patch")

    assert done["ok"] and done["message"] == f"Every file {NAME} put in is gone.", done
    assert not home.exists()


def test_only_presets_in_its_own_folder_are_pointed_at_the_new_name(world):
    """somebody's copy of the old preset elsewhere is theirs, and a shortcut to ours follows"""
    import stat

    bridge, home, tmp = world
    bridge.apply("ff13", ["reshade"])
    from_before_the_rename("ff13", home, tmp)
    elsewhere = tmp / "presets" / "Crystarium.ini"
    lines = ("[GENERAL]\r\nPresetPath=.\\Crystarium.ini\r\n"
             f"CurrentPresetPath={elsewhere}\r\n"
             "PresetShortcutPaths=.\\Other.ini,.\\Crystarium.ini\r\n")
    (home / "ReShade.ini").write_bytes(lines.encode())
    (home / "ReShade.ini").chmod(stat.S_IREAD)

    bridge.list_addons("ff13")

    assert (home / "ReShade.ini").read_bytes().decode() == (
        f"[GENERAL]\r\nPresetPath=.\\{PRESET}\r\nCurrentPresetPath={elsewhere}\r\n"
        f"PresetShortcutPaths=.\\Other.ini,.\\{PRESET}\r\n")
    # read-only is how some keep ReShade off it, and that's kept
    assert not (home / "ReShade.ini").stat().st_mode & stat.S_IWUSR
    (home / "ReShade.ini").chmod(stat.S_IREAD | stat.S_IWRITE)


def test_the_rename_waits_while_the_game_has_reshade_open(world, monkeypatch):
    """a running ReShade saves its preset back under the name it opened"""
    bridge, home, tmp = world
    bridge.apply("ff13", ["reshade"])
    from_before_the_rename("ff13", home, tmp)
    monkeypatch.setattr(bridge_mod.UiBridge, "game_running", lambda self, gid: "it's running")

    bridge.list_addons("ff13")
    assert (home / "Crystarium.ini").is_file() and not (home / PRESET).exists()

    monkeypatch.setattr(bridge_mod.UiBridge, "game_running", lambda self, gid: "")
    bridge.list_addons("ff13")
    assert (home / PRESET).is_file() and not (home / "Crystarium.ini").exists()


def test_a_preset_by_that_name_in_somebody_elses_reshade_stays_put(world):
    bridge, home, _ = world
    home.mkdir()
    (home / "d3d9.dll").write_bytes(BUILD)
    (home / "Crystarium.ini").write_bytes(b"Techniques=Mine@Mine.fx\r\n")
    (home / "ReShade.ini").write_bytes(b"[GENERAL]\r\nPresetPath=.\\Crystarium.ini\r\n")

    bridge.list_addons("ff13")
    assert bridge.rename_presets("ff13") == []

    assert sorted(p.name for p in home.iterdir()) == ["Crystarium.ini", "ReShade.ini", "d3d9.dll"]
    assert (home / "ReShade.ini").read_bytes() == b"[GENERAL]\r\nPresetPath=.\\Crystarium.ini\r\n"
