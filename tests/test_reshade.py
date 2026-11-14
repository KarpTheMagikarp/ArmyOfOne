"""
ReShade dropped on the Mods drawer. NovaFix only takes it as ReShade\\d3d9.dll
beside itself, with its settings and shaders in that folder, and says any other
way crashes the game, so that's the only way it goes in. The XIII Extra's own
ReShade is tests/test_reshade_bundle.py.

The package here is laid out like a real one, ChaserJZX's FF13_Clean: the same
build as d3d9.dll and ReShade32.dll, its settings under both names, a preset,
shaders, and two empty marker files.
"""

import zipfile
from pathlib import Path

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from patcher.mods import game_files as gf
from tests.helpers import make_export_pe
from tests.test_mod_drops import library_with

REPO = Path(__file__).resolve().parent.parent
BUILD = make_export_pe(["Direct3DCreate9", "ReShadeVersion"], version=(4, 7))
SETTINGS = (b"[GENERAL]\r\nCurrentPresetPath=.\\CleanPreset.ini\r\n"
            b"EffectSearchPaths=.\\reshade-shaders\\Shaders\r\n"
            b"TextureSearchPaths=.\\reshade-shaders\\Textures\r\n"
            b"[INPUT]\r\nKeyMenu=36,0,0,0\r\n")
PRESET = b"Techniques=Deblur,MXAO\r\n[deblur.fx]\r\nStrength=1.0\r\n"


def package(root="FF13_Clean", build=BUILD, settings=SETTINGS, newer=False) -> dict:
    files = {f"{root}/d3d9.dll": build, f"{root}/ReShade32.dll": build,
             f"{root}/CleanPreset.ini": PRESET,
             f"{root}/reshade-shaders/Shaders/deblur.fx": b"// deblur",
             f"{root}/reshade-shaders/Shaders/ReShade.fxh": b"// header",
             f"{root}/reshade-shaders/Textures/AreaTex.dds": b"DDS " + b"\0" * 124,
             f"{root}/FF13FIX = R32DLL": b"", f"{root}/NO FF13FIX = D3D9": b"",
             f"{root}/readme.txt": b"Put the files next to the exe."}
    if newer:
        files[f"{root}/ReShade.ini"] = settings
    else:
        files[f"{root}/d3d9.ini"] = settings
        files[f"{root}/ReShade32.ini"] = settings
    return files


def zipped(tmp_path, name, files) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in files.items():
            archive.writestr(member, data)
    return path


@pytest.fixture
def xiii(tmp_path, monkeypatch):
    library, _, paths = library_with(tmp_path, xiii=True)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])
    beside = library / "steamapps" / "common" / "FFXIII" / "white_data" / "prog" / "win" / "bin"
    # NovaFix where Apply puts it, or one that reads like it where the build
    # isn't checked out: its setting's name is how a NovaFix is known
    (beside / "d3d9.dll").write_bytes((REPO / "assets/mods/novafix/d3d9.dll").read_bytes()
                                      if (REPO / "assets/mods/novafix/d3d9.dll").is_file()
                                      else make_export_pe(["Direct3DCreate9"])
                                      + "OverrideLauncherGraphics".encode("utf-16-le"))
    return bridge, beside, tmp_path


def look(tmp_path, files):
    root = tmp_path / "drop"
    for member, data in files.items():
        (root / member).parent.mkdir(parents=True, exist_ok=True)
        (root / member).write_bytes(data)
    game = tmp_path / "FINAL FANTASY XIII-2"
    if not game.exists():
        from tests.hd2_world import make_game
        game = make_game(tmp_path, "u")
    with gf.Source(root) as source:
        return gf.look_in(source, gf.read_game(game))


def picked(tmp_path, version):
    """the drop look() was given, looked at again with one of its versions picked"""
    with gf.Source(tmp_path / "drop") as source:
        return gf.look_in(source, gf.read_game(tmp_path / "FINAL FANTASY XIII-2"),
                          version=version)


def test_the_package_sorts_into_reshade_and_nothing_else(tmp_path):
    found = look(tmp_path, package())
    assert found.reshade_files == {
        "FF13_Clean/d3d9.dll": "ReShade/d3d9.dll",
        # 4.x reads its settings by its own name first
        "FF13_Clean/d3d9.ini": "ReShade/d3d9.ini",
        "FF13_Clean/CleanPreset.ini": "ReShade/CleanPreset.ini",
        "FF13_Clean/reshade-shaders/Shaders/deblur.fx": "ReShade/reshade-shaders/Shaders/deblur.fx",
        "FF13_Clean/reshade-shaders/Shaders/ReShade.fxh":
            "ReShade/reshade-shaders/Shaders/ReShade.fxh",
        "FF13_Clean/reshade-shaders/Textures/AreaTex.dds":
            "ReShade/reshade-shaders/Textures/AreaTex.dds",
    }
    assert found.reshade_info == {"menu": "Home", "preset": "CleanPreset"}
    # not a loader over NovaFix, not an add-on, not art to pack, nothing left out
    assert not found.loaders and not found.loader_files and not found.code and not found.art
    assert gf.left_out(found) == "" and not gf.lost_any(found)


def test_a_newer_build_has_its_settings_as_reshade_ini(tmp_path):
    newer = make_export_pe(["ReShadeVersion"], version=(6, 3))
    found = look(tmp_path, package(build=newer, newer=True))
    assert found.reshade_files["FF13_Clean/ReShade.ini"] == "ReShade/ReShade.ini"


def test_a_64_bit_build_is_left_out_and_said(tmp_path):
    wide = make_export_pe(["ReShadeVersion"], machine=0x8664, version=(6, 3))
    found = look(tmp_path, {"ReShade64/ReShade64.dll": wide, "ReShade64/ReShade.ini": SETTINGS})
    assert not found.reshade_files and found.x64 == ["ReShade64/ReShade64.dll"]
    assert "built for 64-bit" in gf.explain(found, None, "ReShade")


def test_a_drop_goes_in_beside_novafix_and_comes_off_whole(xiii):
    bridge, beside, tmp = xiii
    novafix = (beside / "d3d9.dll").read_bytes()
    phases = []

    done = bridge.install_mod("ff13", str(zipped(tmp, "Complete Package ReShade 4.7.0.zip",
                                                  package())),
                              on_phase=lambda **said: phases.append(said))

    assert done["ok"], done
    assert done["summary"] == "Installed ReShade." and done["title"] == "ReShade"
    assert (beside / "ReShade" / "d3d9.dll").read_bytes() == BUILD
    assert (beside / "ReShade" / "d3d9.ini").read_bytes() == SETTINGS
    assert sorted(p.name for p in (beside / "ReShade").iterdir()) == \
        ["CleanPreset.ini", "d3d9.dll", "d3d9.ini", "reshade-shaders"]
    assert (beside / "d3d9.dll").read_bytes() == novafix, "NovaFix was touched"
    assert {"phase": "install", "name": "ReShade"} in phases
    # how far its files are in, as it goes, not 0 and then 100
    shares = [said["done"] for said in phases if said.get("name") == "ReShade" and "done" in said]
    assert shares[0] == 0 and shares[-1] == 100 and any(0 < s < 100 for s in shares), shares

    listed = [m for m in bridge.list_addons("ff13")["mods"] if m["name"] == "ReShade"]
    assert listed and listed[0]["about"] == "CleanPreset preset, Home opens its menu"

    gone = bridge.remove_addon("ff13", "ReShade", "loader")
    assert gone["ok"] and gone["summary"] == "Removed ReShade."
    assert not (beside / "ReShade").exists()
    assert (beside / "d3d9.dll").read_bytes() == novafix


def test_without_novafix_it_says_what_loads_it(xiii):
    bridge, beside, tmp = xiii
    (beside / "d3d9.dll").unlink()
    done = bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))
    assert done["ok"]
    assert done["summary"] == "Installed ReShade. It loads through NovaFix, which Apply puts on."


def test_a_renderer_that_skips_it_is_said(xiii):
    """NovaFix only looks for ReShade\\d3d9.dll when it picks the renderer itself"""
    bridge, beside, tmp = xiii
    (beside / "NovaFix.ini").write_text("[Performance]\r\nD3D9Backend=LocalDXVK\r\n")
    done = bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))
    assert done["summary"].endswith("Set Renderer back to Automatic in Settings for it to load.")


def test_settings_reshade_saved_stay_on_an_update(xiii):
    bridge, beside, tmp = xiii
    assert bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))["ok"]
    (beside / "ReShade" / "d3d9.ini").write_bytes(SETTINGS + b"[STYLE]\r\nAlpha=0.9\r\n")

    again = bridge.install_mod("ff13", str(zipped(tmp, "pkg2.zip", package(
        settings=SETTINGS.replace(b"36,", b"45,")))))

    assert again["ok"] and again["matchedExisting"]
    assert b"Alpha=0.9" in (beside / "ReShade" / "d3d9.ini").read_bytes()
    assert b"45," in (beside / "ReShade" / "d3d9.ini.new").read_bytes()


HOOK = make_export_pe(["Hook"])
LOADER = make_export_pe(["GetFileVersionInfoA"])


def test_other_mods_in_folders_beside_it_stay_theirs(tmp_path):
    """ReShade took every folder beside it, an add-on's and a loader's included"""
    found = look(tmp_path, dict(package(), **{
        "FF13_Clean/Hook/Hook.dll": HOOK,
        "FF13_Clean/HD Fonts/version.dll": LOADER,
        "FF13_Clean/HD Fonts/hd_textures/a.dds": b"DDS " + b"\0" * 124}))
    assert not any("Hook" in m or "HD Fonts" in m for m in found.reshade_files)
    assert found.code == ["FF13_Clean/Hook/Hook.dll"]
    assert found.loader_files == {"FF13_Clean/HD Fonts/version.dll": "version.dll",
                                  "FF13_Clean/HD Fonts/hd_textures/a.dds": "hd_textures/a.dds"}


def test_a_loader_in_the_same_folder_keeps_its_own_data(tmp_path):
    found = look(tmp_path, dict(package(), **{
        "FF13_Clean/version.dll": LOADER,
        "FF13_Clean/hd_textures/a.dds": b"DDS " + b"\0" * 124}))
    assert "FF13_Clean/hd_textures/a.dds" not in found.reshade_files
    assert found.loader_files["FF13_Clean/hd_textures/a.dds"] == "hd_textures/a.dds"
    assert "FF13_Clean/reshade-shaders/Shaders/deblur.fx" in found.reshade_files
    assert not any(rel.startswith("reshade-shaders") for rel in found.loader_files.values())


def test_reshade_renamed_for_an_asi_loader_is_still_reshade(tmp_path):
    found = look(tmp_path, {"ASI/ReShade32.asi": BUILD, "ASI/ReShade.ini": SETTINGS,
                            "ASI/CleanPreset.ini": PRESET})
    assert found.reshade_files["ASI/ReShade32.asi"] == "ReShade/d3d9.dll"
    assert not found.code


def test_two_different_builds_in_folders_of_their_own_are_versions(tmp_path):
    newer = make_export_pe(["ReShadeVersion"], version=(4, 9))
    files = {**{f"Quality/{k.split('/', 1)[1]}": v for k, v in package().items()},
             **{f"Performance/{k.split('/', 1)[1]}": v for k, v in package(build=newer).items()}}
    found = look(tmp_path, files)
    assert found.versions == ["Performance", "Quality"] and not found.reshade_files

    quality = picked(tmp_path, "Quality")
    assert quality.reshade_files["Quality/d3d9.dll"] == "ReShade/d3d9.dll"
    assert not any(m.startswith("Performance/") for m in quality.reshade_files)


def test_two_different_builds_side_by_side_are_said_not_guessed(tmp_path):
    newer = make_export_pe(["ReShadeVersion"], version=(4, 9))
    found = look(tmp_path, dict(package(), **{"FF13_Clean/ReShade32.dll": newer}))
    assert not found.reshade_files and not found.versions
    assert found.reshade_clash == ["FF13_Clean/ReShade32.dll", "FF13_Clean/d3d9.dll"]
    assert "2 different ReShade builds" in gf.explain(found, None, "pkg")
    assert gf.lost_any(found) and "different ReShade builds" in gf.left_out(found)


def test_a_newer_build_reads_the_settings_it_reads(tmp_path):
    """a stale d3d9.ini from a 4.x setup used to win over the ReShade.ini a 6.x reads"""
    newer = make_export_pe(["ReShadeVersion"], version=(6, 3))
    found = look(tmp_path, dict(package(build=newer), **{
        "FF13_Clean/ReShade.ini": SETTINGS.replace(b"KeyMenu=36", b"KeyMenu=45")}))
    assert found.reshade_files["FF13_Clean/ReShade.ini"] == "ReShade/ReShade.ini"
    assert "FF13_Clean/d3d9.ini" not in found.reshade_files
    assert found.reshade_info["menu"] == "Insert"


def test_a_flat_package_brings_its_shaders(tmp_path):
    flat = b"[GENERAL]\r\nEffectSearchPaths=.\\\r\nTextureSearchPaths=.\\\r\n"
    found = look(tmp_path, {"Flat/d3d9.dll": BUILD, "Flat/d3d9.ini": flat,
                            "Flat/SMAA.fx": b"// smaa", "Flat/ReShade.fxh": b"// h",
                            "Flat/AreaTex.dds": b"DDS " + b"\0" * 124})
    assert {m: r for m, r in found.reshade_files.items() if not m.endswith("d3d9.dll")} == {
        "Flat/d3d9.ini": "ReShade/d3d9.ini", "Flat/SMAA.fx": "ReShade/SMAA.fx",
        "Flat/ReShade.fxh": "ReShade/ReShade.fxh", "Flat/AreaTex.dds": "ReShade/AreaTex.dds"}
    assert not found.art and not gf.lost_any(found)


def test_shaders_one_up_from_the_build_go_in_and_its_settings_follow(tmp_path):
    up = (b"[GENERAL]\r\nEffectSearchPaths=..\\reshade-shaders\\Shaders\\**\r\n"
          b"TextureSearchPaths=..\\reshade-shaders\\Textures\r\n")
    found = look(tmp_path, {"Pack/x86/d3d9.dll": BUILD, "Pack/x86/d3d9.ini": up,
                            "Pack/reshade-shaders/Shaders/a.fx": b"// a",
                            "Pack/reshade-shaders/Textures/t.png": b"PNG"})
    assert found.reshade_files["Pack/reshade-shaders/Shaders/a.fx"] == \
        "ReShade/reshade-shaders/Shaders/a.fx"
    assert found.reshade_files["Pack/reshade-shaders/Textures/t.png"] == \
        "ReShade/reshade-shaders/Textures/t.png"
    assert found.reshade_rewrites["Pack/x86/d3d9.ini"] == (
        b"[GENERAL]\r\nEffectSearchPaths=.\\reshade-shaders\\Shaders\\**\r\n"
        b"TextureSearchPaths=.\\reshade-shaders\\Textures\r\n")


def test_paths_written_on_the_authors_pc_point_at_where_it_all_went(xiii):
    """ReShade 3 wrote whole paths, and a preset shared from one still says the author's drive"""
    bridge, beside, tmp = xiii
    whole = (b"[GENERAL]\r\nCurrentPresetPath=D:\\Games\\FFXIII\\bin\\CleanPreset.ini\r\n"
             b"EffectSearchPaths=D:\\Games\\FFXIII\\bin\\reshade-shaders\\Shaders\r\n"
             b"TextureSearchPaths=D:\\Games\\FFXIII\\bin\\reshade-shaders\\Textures\r\n"
             b"[INPUT]\r\nKeyMenu=36,0,0,0\r\n")
    done = bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package(settings=whole))))
    assert done["ok"], done
    assert (beside / "ReShade" / "d3d9.ini").read_bytes() == (
        b"[GENERAL]\r\nCurrentPresetPath=.\\CleanPreset.ini\r\n"
        b"EffectSearchPaths=.\\reshade-shaders\\Shaders\r\n"
        b"TextureSearchPaths=.\\reshade-shaders\\Textures\r\n"
        b"[INPUT]\r\nKeyMenu=36,0,0,0\r\n")


def test_its_settings_never_land_in_novafixs_add_ons(xiii):
    bridge, beside, tmp = xiii
    done = bridge.install_mod("ff13", str(zipped(tmp, "mixed.zip", dict(package(), **{
        "Hook/Hook.dll": HOOK, "Hook/Hook.ini": b"a=1"}))))
    assert done["ok"], done
    addons = beside / "addons"
    landed = sorted(p.name for p in addons.rglob("*") if p.is_file()) if addons.is_dir() else []
    assert "Hook.dll" in landed and "Hook.ini" in landed
    assert not {"d3d9.ini", "ReShade32.ini", "CleanPreset.ini"} & set(landed), landed


def test_taken_off_and_put_back_on_keeps_the_settings_it_saved(xiii):
    """Remove leaves the settings ReShade saved, and putting it back on used to stop at them"""
    bridge, beside, tmp = xiii
    assert bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))["ok"]
    (beside / "ReShade" / "d3d9.ini").write_bytes(SETTINGS + b"[STYLE]\r\nAlpha=0.9\r\n")
    assert bridge.remove_addon("ff13", "ReShade", "loader")["ok"]
    assert (beside / "ReShade" / "d3d9.ini").is_file()

    again = bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))

    assert again["ok"], again
    assert b"Alpha=0.9" in (beside / "ReShade" / "d3d9.ini").read_bytes()
    assert (beside / "ReShade" / "d3d9.ini.new").read_bytes() == SETTINGS


def test_a_reshade_folder_made_by_hand_with_the_same_files_is_taken_on(xiii):
    bridge, beside, tmp = xiii
    for member, data in package().items():
        if member.endswith(("d3d9.dll", "d3d9.ini", "CleanPreset.ini")) or "/reshade-shaders/" in member:
            target = beside / "ReShade" / member.split("/", 1)[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    done = bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))
    assert done["ok"], done
    assert bridge.remove_addon("ff13", "ReShade", "loader")["ok"]
    # the folder was theirs before it was this, so it stays, empty
    assert not [p for p in (beside / "ReShade").rglob("*") if p.is_file()]


def test_a_build_gone_from_its_folder_is_said_in_the_list(xiii):
    bridge, beside, tmp = xiii
    assert bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))["ok"]
    (beside / "ReShade" / "d3d9.dll").unlink()
    row = next(m for m in bridge.list_addons("ff13")["mods"] if m["name"] == "ReShade")
    assert row["about"] == "d3d9.dll is missing"


@pytest.mark.parametrize("ini, said", [
    ("[performance]\r\nd3d9backend=localdxvk\r\n",
     " Set Renderer back to Automatic in Settings for it to load."),
    ("[Performance]\r\nD3D9Backend=Automatic\r\n", ""),
])
def test_the_renderer_is_read_the_way_novafix_reads_it(xiii, ini, said):
    bridge, beside, tmp = xiii
    (beside / "NovaFix.ini").write_text(ini)
    done = bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))
    assert done["summary"] == "Installed ReShade." + said


def test_a_chain_dll_novafix_loads_first_is_said(xiii):
    bridge, beside, tmp = xiii
    (beside / "d3d9_chain.dll").write_bytes(b"MZ")
    done = bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))
    assert "d3d9_chain.dll" in done["summary"]


def test_a_d3d9_dll_that_isnt_novafix_doesnt_count_as_it(xiii):
    """FF13Fix is a d3d9.dll beside the exe too, and it never loads ReShade"""
    bridge, beside, tmp = xiii
    (beside / "d3d9.dll").write_bytes(make_export_pe(["Direct3DCreate9"]))
    done = bridge.install_mod("ff13", str(zipped(tmp, "pkg.zip", package())))
    assert done["summary"] == "Installed ReShade. It loads through NovaFix, which Apply puts on."


def test_an_add_on_beside_the_build_still_goes_to_novafix(xiii):
    """ReShade's folder kept everything in it out of the add-ons, the add-on's own dll too"""
    bridge, beside, tmp = xiii
    done = bridge.install_mod("ff13", str(zipped(tmp, "pack.zip", {
        "ReShade32.dll": BUILD, "ReShade32.ini": SETTINGS,
        "FF13Tweaks.dll": HOOK, "FF13Tweaks.ini": b"fov=1"})))
    assert done["ok"], done
    landed = sorted(p.name for p in (beside / "addons").rglob("*") if p.is_file())
    assert "FF13Tweaks.dll" in landed and "FF13Tweaks.ini" in landed
    assert "ReShade32.ini" not in landed
    assert (beside / "ReShade" / "d3d9.dll").read_bytes() == BUILD


def test_an_asi_loaders_plugins_beside_reshade_stay_the_loaders(tmp_path):
    found = look(tmp_path, {"dinput8.dll": LOADER, "scripts/ReShade32.asi": BUILD,
                            "scripts/WidescreenFix.asi": HOOK,
                            "scripts/WidescreenFix.ini": b"w=1"})
    assert found.reshade_files["scripts/ReShade32.asi"] == "ReShade/d3d9.dll"
    assert found.loader_files["scripts/WidescreenFix.asi"] == "scripts/WidescreenFix.asi"
    assert found.loader_files["scripts/WidescreenFix.ini"] == "scripts/WidescreenFix.ini"
    assert "scripts/ReShade32.asi" not in found.loader_files


def test_game_file_versions_and_two_reshades_dont_share_a_picker(tmp_path):
    """picking an outfit used to leave both ReShades out without a word"""
    newer = make_export_pe(["ReShadeVersion"], version=(4, 9))
    c108 = "chr/pc/c108/bin/c108.win32.imgb"
    found = look(tmp_path, {f"Outfit/A/{c108}": b"a" * 5000, f"Outfit/B/{c108}": b"b" * 5000,
                            "ReShade/old/d3d9.dll": BUILD, "ReShade/new/d3d9.dll": newer})
    assert found.versions == ["Outfit/A", "Outfit/B"], found.versions
    outfit = picked(tmp_path, "Outfit/A")
    assert outfit.reshade_clash and not outfit.reshade_files
    assert gf.lost_any(outfit) and "different ReShade builds" in gf.left_out(outfit)


def test_a_reshade_version_inside_another_installs_when_picked(tmp_path):
    newer = make_export_pe(["ReShadeVersion"], version=(5, 9))
    files = dict(package(), **{"FF13_Clean/ReShade 5/d3d9.dll": newer,
                               "FF13_Clean/ReShade 5/ReShade.ini": SETTINGS})
    found = look(tmp_path, files)
    assert len(found.versions) == 2, found.versions
    five = picked(tmp_path, next(v for v in found.versions if "5" in v))
    assert five.reshade_files["FF13_Clean/ReShade 5/d3d9.dll"] == "ReShade/d3d9.dll"
    assert five.reshade_files["FF13_Clean/ReShade 5/ReShade.ini"] == "ReShade/ReShade.ini"
    assert not five.reshade_clash
    top = picked(tmp_path, next(v for v in found.versions if "5" not in v))
    assert top.reshade_files["FF13_Clean/d3d9.dll"] == "ReShade/d3d9.dll"


def test_a_search_path_takes_only_what_it_looks_for_from_where_it_points(tmp_path):
    """PresetSearchPaths=..\\ took everything beside the build's folder, another mod's included"""
    found = look(tmp_path, {
        "Pack/ReShade/d3d9.dll": BUILD,
        "Pack/ReShade/d3d9.ini": b"[GENERAL]\r\nPresetSearchPaths=..\\\r\n",
        "Pack/Second.ini": PRESET, "Pack/readme.txt": b"hello",
        "Pack/Screenshots/one.png": b"PNG", "Pack/HD Fonts/version.dll": LOADER,
        "Pack/HD Fonts/hd_textures/a.dds": b"DDS " + b"\0" * 124})
    assert found.reshade_files["Pack/Second.ini"] == "ReShade/Second.ini"
    assert not any(m.startswith(("Pack/HD Fonts", "Pack/Screenshots")) or m == "Pack/readme.txt"
                   for m in found.reshade_files), found.reshade_files
    assert found.loader_files["Pack/HD Fonts/hd_textures/a.dds"] == "hd_textures/a.dds"


def test_a_whole_path_to_the_preset_beside_the_build_is_that_preset(tmp_path):
    whole = (b"[GENERAL]\r\nCurrentPresetPath=D:\\Mods\\FF13 Clean\\CleanPreset.ini\r\n"
             b"EffectSearchPaths=C:\\X\\Shaders\r\n")
    found = look(tmp_path, {"FF13 Clean/d3d9.dll": BUILD, "FF13 Clean/d3d9.ini": whole,
                            "FF13 Clean/CleanPreset.ini": PRESET,
                            "FF13 Clean/reshade-shaders/Shaders/a.fx": b"// a",
                            "Old/Shaders/b.fx": b"// b"})
    assert found.reshade_rewrites["FF13 Clean/d3d9.ini"] == (
        b"[GENERAL]\r\nCurrentPresetPath=.\\CleanPreset.ini\r\n"
        b"EffectSearchPaths=.\\reshade-shaders\\Shaders\r\n")
    assert "Old/Shaders/b.fx" not in found.reshade_files


def test_a_folder_of_presets_or_pictures_comes_with_it(tmp_path):
    found = look(tmp_path, dict(package(), **{
        "FF13_Clean/ReShade Presets/Warm.ini": PRESET,
        "FF13_Clean/LUT/lut.png": b"PNG", "FF13_Clean/LUT/how to.txt": b"use it"}))
    assert found.reshade_files["FF13_Clean/ReShade Presets/Warm.ini"] == \
        "ReShade/ReShade Presets/Warm.ini"
    assert found.reshade_files["FF13_Clean/LUT/lut.png"] == "ReShade/LUT/lut.png"
    assert not found.art


def test_a_path_out_and_back_into_its_folder_is_written_from_there(tmp_path):
    back = (b"[GENERAL]\r\nEffectSearchPaths= .\\reshade-shaders\\Shaders , "
            b"..\\FF13_Clean\\reshade-shaders\\Shaders\\**\r\n")
    found = look(tmp_path, package(settings=back))
    assert found.reshade_rewrites["FF13_Clean/d3d9.ini"] == (
        b"[GENERAL]\r\nEffectSearchPaths= .\\reshade-shaders\\Shaders ,"
        b".\\reshade-shaders\\Shaders\\**\r\n")


def test_a_whole_path_never_takes_another_mods_folder_of_that_name(tmp_path):
    """C:\\...\\Textures landed on a loader's own textures, which went in without them"""
    whole = b"[GENERAL]\r\nTextureSearchPaths=C:\\Games\\FF13\\Textures\r\n"
    found = look(tmp_path, {"Pack/ReShade/d3d9.dll": BUILD, "Pack/ReShade/d3d9.ini": whole,
                            "Pack/UI Mod/dinput8.dll": LOADER,
                            "Pack/UI Mod/Textures/menu.png": b"PNG"})
    assert "Pack/UI Mod/Textures/menu.png" not in found.reshade_files
    assert found.loader_files["Pack/UI Mod/Textures/menu.png"] == "Textures/menu.png"


def test_a_general_section_alone_isnt_reshades_settings(tmp_path):
    """an add-on's own .ini beside ReShade went nowhere"""
    found = look(tmp_path, {"ReShade32.dll": BUILD, "ReShade32.ini": SETTINGS,
                            "FF13Tweaks.dll": HOOK, "FF13Tweaks.ini": b"[General]\r\nFov=1\r\n"})
    assert "FF13Tweaks.ini" not in found.reshade_spare and "FF13Tweaks.ini" not in found.reshade_files


def test_loose_game_art_beside_it_stays_art(tmp_path):
    """a folder of .dds next to ReShade is art that needs packing, not ReShade's"""
    found = look(tmp_path, dict(package(), **{"FF13_Clean/HD Textures/menu_bg.dds":
                                              b"DDS " + b"\0" * 124}))
    assert "FF13_Clean/HD Textures/menu_bg.dds" not in found.reshade_files
    assert "FF13_Clean/HD Textures/menu_bg.dds" in found.art
