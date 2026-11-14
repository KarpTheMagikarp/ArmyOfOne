"""
HD Models Plus's main download from the Mods drawer: its versions offered,
the one picked built natively out of the game's original models, and put in
like any drawer mod, both voice sets, over the HD Project's characters when
they're in, and off again with Remove. The builder's a stand-in here that
checks it was handed originals and writes something it can be told by (the
real one has tests of its own, byte for byte against the exe); the last
test here runs the real one through the drawer's build.
"""

import zipfile
from pathlib import Path

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from patcher.mods import game_files, hd_vanilla, models_plus
from patcher.mods.hdmodels import build as builder
from tests.helpers import make_fake_pe, pack_plain
from tests.test_hd_runner import stub

MANIFESTS = Path(__file__).resolve().parent.parent / "manifests"
PARTY = [f"c00{n}" for n in range(1, 7)] + [f"c20{n}" for n in range(1, 7)]


def model(mid: str, half: str) -> str:
    return f"chr/pc/{mid}/bin/{mid}.win32.{half}"


SIZES = {model(m, h): 700 + 31 * i + (13 if h == "imgb" else 0)
         for i, m in enumerate(PARTY) for h in ("trb", "imgb")}

# what it reads and writes, the way the mod's own recipes spell it: the models
# seven lines down, then the two files it makes
READS = {"Lightning": ("c001", "c201"), "X_Lightning1": ("c001",), "Snow": ("c002", "c202")}
for n, who in enumerate(("Sazh", "Hope", "Vanille", "Fang"), start=3):
    READS[who] = (f"c00{n}", f"c20{n}")
    READS[f"X_{who}2"] = (f"c00{n}", f"c20{n}")
    READS[f"X_{who}1"] = (f"c00{n}",)
for name in ("Lightning_Hair", "X_Lightning2", "X_Lightning_Hair2"):
    READS[name] = READS["Lightning"]


def recipe(name: str, fail: bool = False) -> bytes:
    reads = READS[name]
    out = reads[-1]
    lines = ["42", "0", "1" if len(reads) > 1 else "0", "0", str(len(reads))]
    lines += [f"chr\\pc\\{m}\\bin\\{m}.win32" for m in reads]
    lines += [f"chr\\pc\\{out}\\bin\\{out}.win32.trb", f"chr\\pc\\{out}\\bin\\{out}.win32.imgb"]
    lines += ["0", ":X", f"; {name}"] + (["FAIL"] if fail else [])
    return "\r\n".join(lines).encode() + b"\r\n"


def stand_in(raw, files, ask=None, answers=0, log=None, cancel=None):
    """
    the builder: refuses anything that isn't an original, the way the real one
    would just build wrong, and writes what it built from where it can be read
    """
    import re
    text = raw.decode()
    name = text.rsplit("; ", 1)[1].split("\r")[0]
    if "FAIL" in text:
        raise builder.ModelError("the game's c204.win32.trb isn't there to build from")
    paths = [line.strip().replace("\\", "/") for line in text.splitlines()
             if re.match(r"^chr\\\S+\.win32(\.trb|\.imgb)?$", line.strip())]
    for each in [p for p in paths if p.endswith(".win32")]:
        for half in (".trb", ".imgb"):
            data = files.read("models\\orig\\" + each.replace("/", "\\") + half)
            if data is None or set(data) != {ord("V")}:
                raise builder.ModelError(f"{each}{half} isn't an original")
    return {"models\\mod\\" + out.replace("/", "\\"): ("MP " + name + " " + out + " ").encode() * 40
            for out in paths if not out.endswith(".win32")}


@pytest.fixture(autouse=True)
def builds(monkeypatch, request):
    if not request.node.name.startswith("test_the_real_builder"):
        monkeypatch.setattr(builder, "run", stand_in)


def download(tmp_path, *, zipped=False, fail=(), name="FF_XIII_HD_Models_Plus-11-4-1-7-1649368856",
             extra=None):
    """the main download as Nexus has it"""
    files = {"_FF XIII HD Models+ Install.bat": b"@echo off\r\n",
             "Readme HD Models+.txt": b"Unzip the files into any directory",
             "SupportFiles/FFXIIItrbSetup.bat": b"@echo off\r\n",
             "SupportFiles/ff13tool.exe": b"MZ not this one",
             f"SupportFiles/{models_plus.TOOL}": b"MZ never run",
             "Data/FFXIII_Ultimate_Models.bin": b"\x00" * 64,
             "Textures/pc/C001C_01.bin": b"texture" * 10}
    for recipe_name in READS:
        files[f"Data/{recipe_name}.txt"] = recipe(recipe_name, fail=recipe_name in fail)
    files.update(extra or {})
    if zipped:
        path = tmp_path / f"{name}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for member, data in files.items():
                archive.writestr(member, data)
        return path
    root = tmp_path / name
    for member, data in files.items():
        (root / member).parent.mkdir(parents=True, exist_ok=True)
        (root / member).write_bytes(data)
    return root


def scr(sys_dir, voice, entries):
    pack_plain(sys_dir / f"white_scr{voice}.win32.bin",
               sys_dir / f"filelist_scr{voice}.win32.bin", entries)


def rows(grown=()):
    return [(path, b"V" * size if path not in grown else b"G" * (size + 4000))
            for path, size in SIZES.items()] + [("chr/npc/n910/bin/n910.win32.trb", b"n" * 500)]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """XIII with both voice sets, the six as Steam ships them, and a bridge on it"""
    library = tmp_path / "steam"
    game = library / "steamapps" / "common" / "FFXIII"
    sys_dir = game / "white_data" / "sys"
    sys_dir.mkdir(parents=True)
    (game / "white_data" / "prog" / "win" / "bin").mkdir(parents=True)
    (game / "white_data" / "prog" / "win" / "bin" / "ffxiiiimg.exe").write_bytes(make_fake_pe())
    (library / "steamapps" / "appmanifest_292120.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"292120"\n\t"installdir"\t\t"FFXIII"\n}\n')
    for voice in "uc":
        scr(sys_dir, voice, rows())
    monkeypatch.setattr(hd_vanilla, "table", lambda layout: dict(SIZES))
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths={"ffxiii": MANIFESTS / "ffxiii.json"},
                      steam_library_paths=[library])
    return bridge, game, tmp_path


def held(game) -> dict:
    """voice -> path -> the bytes the game reads there now"""
    from patcher.mods.hd_zones import read_record

    out = {}
    for pair in game_files.pairs(game):
        listed = game_files.entries(pair.index)
        out[pair.voice] = {p: read_record(pair.archive, listed[p]) for p in SIZES}
    return out


def built_by(data: bytes) -> str:
    return data.split(b" ")[1].decode() if data.startswith(b"MP ") else ""


def test_its_main_download_offers_its_versions_and_changes_nothing(world):
    bridge, game, tmp = world
    before = held(game)

    answer = bridge.install_mod("ff13", str(download(tmp, zipped=True)))

    assert answer["kind"] == "versions" and answer["title"] == "HD Models Plus", answer
    assert answer["versions"] == ["Ultimate", "Ultimate, HQ Hair", "High Quality"]
    assert set(answer["about"]) == set(answer["versions"])
    # asked under its own Worth getting row, with the one to pick already picked
    assert answer["jobId"] == "hdmodelsplus" and answer["recommended"] == "Ultimate", answer
    assert "run" not in answer["error"].lower(), "nobody's sent to run its .bat any more"
    assert held(game) == before


@pytest.mark.parametrize("zipped", [True, False])
def test_ultimate_builds_the_six_and_goes_into_both_voice_sets(world, zipped):
    bridge, game, tmp = world
    drop = download(tmp, zipped=zipped)

    done = bridge.install_mod("ff13", str(drop), None, "Ultimate")

    assert done["ok"] and done["summary"] == "Installed HD Models Plus.", done
    assert "22 game files into both voice sets" in done["message"], done["message"]
    now = held(game)
    for voice in "uc":
        # the battle models off their HQ recipes with the normal hair, and the
        # field models Ultimate sculpts, all but Snow's, who has none
        assert built_by(now[voice][model("c201", "trb")]) == "X_Lightning_Hair2"
        assert built_by(now[voice][model("c001", "imgb")]) == "X_Lightning1"
        assert built_by(now[voice][model("c206", "imgb")]) == "X_Fang2"
        assert built_by(now[voice][model("c202", "trb")]) == "Snow"
        assert now[voice][model("c002", "trb")] == b"V" * SIZES[model("c002", "trb")]
    listed = {m["name"]: m for m in bridge.list_addons("ff13")["mods"]}
    assert listed["HD Models Plus"]["about"] == "Ultimate, 22 game files in both voice sets"
    assert not list((game / ".patcher-undo" / models_plus.WORK).glob("*")), "its build folder went"
    if not zipped:
        assert not (drop / "models").exists(), "nothing's left in the download either"


def test_hq_hair_and_high_quality_run_their_own_recipes(world):
    bridge, game, tmp = world
    drop = download(tmp)
    assert bridge.install_mod("ff13", str(drop), None, "Ultimate, HQ Hair")["ok"]
    assert built_by(held(game)["u"][model("c201", "trb")]) == "X_Lightning2"

    done = bridge.install_mod("ff13", str(drop), None, "High Quality")

    assert done["ok"] and done["summary"] == "Updated HD Models Plus.", done
    now = held(game)
    assert built_by(now["u"][model("c201", "imgb")]) == "Lightning_Hair"
    assert built_by(now["c"][model("c205", "trb")]) == "Vanille"
    # High Quality doesn't touch the field models, so the last version's come off
    assert now["u"][model("c001", "trb")] == b"V" * SIZES[model("c001", "trb")]
    assert [m["name"] for m in bridge.list_addons("ff13")["mods"]] == ["HD Models Plus"]


def test_the_drawer_hears_how_far_the_build_and_the_install_have_got(world):
    """
    the runs are most of a build: six of them for High Quality, each a
    seventh, with the originals and the layout the seventh before them. The
    install of what it built is a step of its own, from 0 again
    """
    bridge, game, tmp = world
    heard = []

    done = bridge.install_mod("ff13", str(download(tmp)), None, "High Quality",
                              on_phase=lambda **said: heard.append(said))

    assert done["ok"], done
    assert [said["phase"] for said in heard[:2]] == ["read", "build"]
    for phase in ("build", "install"):
        shares = [said["done"] for said in heard if said["phase"] == phase and "done" in said]
        assert shares[0] == 0 and shares[-1] == 100 and shares == sorted(set(shares)), shares
        assert all(said["of"] == 100 and said["name"] == "HD Models Plus"
                   for said in heard if said["phase"] == phase and "done" in said)
    built = [said["done"] for said in heard if said["phase"] == "build" and "done" in said]
    assert {14, 28, 42, 57, 71, 85, 99} <= set(built), built
    # the originals are read a half at a time, the first half of that seventh
    assert any(0 < share < 7 for share in built), built
    last_build = max(i for i, said in enumerate(heard) if said["phase"] == "build")
    assert all(said["phase"] == "install" for said in heard[last_build + 1:])


def test_remove_puts_every_original_back(world):
    bridge, game, tmp = world
    before = held(game)
    assert bridge.install_mod("ff13", str(download(tmp)), None, "Ultimate")["ok"]

    done = bridge.remove_addon("ff13", "HD Models Plus", "files")

    assert done["ok"], done
    assert held(game) == before


def test_over_the_hd_projects_characters_it_builds_from_the_backup_and_goes_on_top(world):
    """
    the HD Project's Playable Characters in first, so the six in the game are
    its own. Their .bat stops there and asks for the HD Project's folder; the
    backup this app took before that install has the originals
    """
    bridge, game, tmp = world
    sys_dir = game / "white_data" / "sys"
    backup = game / ".patcher-undo" / "hdproject"
    backup.mkdir(parents=True)
    for voice in "uc":
        for name in (f"filelist_scr{voice}.win32.bin", f"white_scr{voice}.win32.bin"):
            (backup / name).write_bytes((sys_dir / name).read_bytes())
        scr(sys_dir, voice, rows(grown=set(SIZES)))
    hd = held(game)

    done = bridge.install_mod("ff13", str(download(tmp)), None, "Ultimate")

    assert done["ok"], done
    assert built_by(held(game)["c"][model("c204", "trb")]) == "X_Hope2"
    # the HD Project's own Snow field model stays, Ultimate has none for him
    assert held(game)["u"][model("c002", "trb")] == hd["u"][model("c002", "trb")]

    assert bridge.remove_addon("ff13", "HD Models Plus", "files")["ok"]
    assert held(game) == hd, "what was under it was the HD Project's, and that's what's back"


def test_under_a_costume_from_the_drawer_it_finds_the_original_beneath(world):
    bridge, game, tmp = world
    costume = tmp / "Serah Dress"
    for half in ("trb", "imgb"):
        (costume / model("c201", half)).parent.mkdir(parents=True, exist_ok=True)
        (costume / model("c201", half)).write_bytes(b"dress " * 300)
    assert bridge.install_mod("ff13", str(costume))["ok"]

    done = bridge.install_mod("ff13", str(download(tmp)), None, "Ultimate")

    assert done["ok"], done
    assert built_by(held(game)["u"][model("c201", "imgb")]) == "X_Lightning_Hair2"


def test_a_recolor_holding_half_a_model_still_leaves_both_originals(world):
    """a texture-only mod ships the .imgb alone: under it for that half, the game for the other"""
    bridge, game, tmp = world
    recolor = tmp / "Lightning Recolor"
    (recolor / model("c201", "imgb")).parent.mkdir(parents=True)
    (recolor / model("c201", "imgb")).write_bytes(b"recolor " * 300)
    assert bridge.install_mod("ff13", str(recolor))["ok"]

    done = bridge.install_mod("ff13", str(download(tmp)), None, "Ultimate")

    assert done["ok"], done
    assert built_by(held(game)["u"][model("c201", "trb")]) == "X_Lightning_Hair2"


def test_a_backup_taken_over_a_costume_isnt_its_original(world):
    """
    the HD Project's first backup came after a costume the size Steam ships
    went in from the drawer, so it holds the costume. Under the drawer's mod
    is where the original is
    """
    bridge, game, tmp = world
    costume = tmp / "Lightning Recolor"
    for half in ("trb", "imgb"):
        (costume / model("c201", half)).parent.mkdir(parents=True, exist_ok=True)
        (costume / model("c201", half)).write_bytes(b"R" * SIZES[model("c201", half)])
    assert bridge.install_mod("ff13", str(costume))["ok"]
    sys_dir, backup = game / "white_data" / "sys", game / ".patcher-undo" / "hdproject"
    backup.mkdir(parents=True, exist_ok=True)
    for voice in "uc":
        for name in (f"filelist_scr{voice}.win32.bin", f"white_scr{voice}.win32.bin"):
            (backup / name).write_bytes((sys_dir / name).read_bytes())

    done = bridge.install_mod("ff13", str(download(tmp)), None, "Ultimate")

    # the stand-in builder refuses anything but an original
    assert done["ok"], done


def test_a_backup_holding_a_costume_taken_off_since_isnt_its_original(world):
    """the costume's Remove put the game's own back, so the game comes before the backup"""
    bridge, game, tmp = world
    costume = tmp / "Lightning Recolor"
    for half in ("trb", "imgb"):
        (costume / model("c201", half)).parent.mkdir(parents=True, exist_ok=True)
        (costume / model("c201", half)).write_bytes(b"R" * SIZES[model("c201", half)])
    assert bridge.install_mod("ff13", str(costume))["ok"]
    sys_dir, backup = game / "white_data" / "sys", game / ".patcher-undo" / "hdproject"
    backup.mkdir(parents=True, exist_ok=True)
    for voice in "uc":
        for name in (f"filelist_scr{voice}.win32.bin", f"white_scr{voice}.win32.bin"):
            (backup / name).write_bytes((sys_dir / name).read_bytes())
    assert bridge.remove_addon("ff13", "Lightning Recolor", "files")["ok"]

    done = bridge.install_mod("ff13", str(download(tmp)), None, "Ultimate")

    assert done["ok"], done


def test_a_model_with_no_original_to_build_from_is_refused_whole(world):
    """their own installer's case too: a modded game and nowhere to find what it was"""
    bridge, game, tmp = world
    sys_dir = game / "white_data" / "sys"
    for voice in "uc":
        scr(sys_dir, voice, rows(grown={model("c204", "trb")}))
    before = held(game)

    done = bridge.install_mod("ff13", str(download(tmp)), None, "Ultimate")

    assert not done["ok"] and "Hope's is changed in your game" in done["error"], done
    assert "takes every mod in them off too" in done["error"]
    assert held(game) == before


def test_their_own_cache_of_originals_does_for_one(world):
    """a folder their .bat ran from keeps models\\orig, filled before it modded anything"""
    bridge, game, tmp = world
    sys_dir = game / "white_data" / "sys"
    for voice in "uc":
        scr(sys_dir, voice, rows(grown={model("c204", "trb"), model("c204", "imgb")}))
    drop = download(tmp)
    for half in ("trb", "imgb"):
        cached = drop / "models" / "orig" / model("c204", half)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"V" * SIZES[model("c204", half)])

    done = bridge.install_mod("ff13", str(drop), None, "Ultimate")

    assert done["ok"], done
    assert built_by(held(game)["u"][model("c204", "trb")]) == "X_Hope2"


def test_a_recipe_the_builder_fails_on_changes_nothing_and_says_whose(world):
    bridge, game, tmp = world
    before = held(game)

    done = bridge.install_mod("ff13", str(download(tmp, fail={"X_Hope2"})), None, "Ultimate")

    assert not done["ok"], done
    assert done["error"] == ("HD Models Plus wasn't installed: Hope's model couldn't be built: "
                             "the game's c204.win32.trb isn't there to build from. Nothing has "
                             "been changed."), done["error"]
    assert held(game) == before
    assert not list((game / ".patcher-undo" / models_plus.WORK).glob("*"))
    assert not [m for m in bridge.list_addons("ff13")["mods"] if m["name"] == "HD Models Plus"]


def test_a_build_folder_a_crash_left_is_swept_by_the_next(world):
    bridge, game, tmp = world
    left = game / ".patcher-undo" / models_plus.WORK / "models-plus-crashed"
    (left / "models" / "orig").mkdir(parents=True)
    (left / "models" / "orig" / "c201.win32.trb").write_bytes(b"V" * 10)
    (left / "models" / "orig" / "c201.win32.trb").chmod(0o444)

    assert bridge.install_mod("ff13", str(download(tmp)), None, "High Quality")["ok"]

    assert not left.exists()


def test_a_download_naming_a_file_outside_its_folder_is_refused_whole(world):
    """its Data folder is four up from the game's exe folder, by way of the build folder"""
    bridge, game, tmp = world
    escape = "Data/../../../../white_data/prog/win/bin/dinput8.dll"
    before = held(game)

    done = bridge.install_mod("ff13", str(download(tmp, zipped=True, extra={escape: b"MZ"})),
                              None, "Ultimate")

    assert not done["ok"] and "points outside its own folder" in done["error"], done
    assert not (game / "white_data" / "prog" / "win" / "bin" / "dinput8.dll").exists()
    assert held(game) == before
    assert not list((game / ".patcher-undo" / models_plus.WORK).glob("*"))


def test_its_worth_getting_row_reads_it_in_once_its_build_is(world):
    """its Open page is no use with it in. A costume that only shares its name isn't it"""
    bridge, game, tmp = world
    costume = tmp / "HD Models Plus"
    (costume / model("c201", "imgb")).parent.mkdir(parents=True)
    (costume / model("c201", "imgb")).write_bytes(b"dress " * 300)
    assert bridge.install_mod("ff13", str(costume))["ok"]
    assert "jobId" not in next(m for m in bridge.list_addons("ff13")["mods"]
                               if m["name"] == "HD Models Plus")
    assert bridge.remove_addon("ff13", "HD Models Plus", "files")["ok"]

    assert bridge.install_mod("ff13", str(download(tmp)), None, "Ultimate")["ok"]

    row = next(m for m in bridge.list_addons("ff13")["mods"] if m["name"] == "HD Models Plus")
    assert row["jobId"] == "hdmodelsplus"


def test_its_worth_getting_row_is_its_nexus_page_watched_through_the_drawer():
    """the row and the build go by one name, and the download's zip clears the watch's floor"""
    import json

    from patcher.manifest import load_manifest

    patch = next(p for p in load_manifest(MANIFESTS / "ffxiii.json").patches
                 if p.ui_job_id == "hdmodelsplus")
    jobs = json.loads((MANIFESTS.parent / "ui" / "jobs.json").read_text(encoding="utf-8"))
    job = next(j for g in jobs["games"] if g["id"] == "ff13" for j in g["jobs"]
               if j["id"] == "hdmodelsplus")

    assert patch.label == models_plus.NAME and job["kind"] == "extra"
    assert job["page"] == "https://www.nexusmods.com/finalfantasy13/mods/11"
    # what the command line's Apply does with it is stage it, never unpack it into the game
    assert patch.type == "nexus" and patch.runs_installer and not patch.default_enabled
    limits = UiBridge(manifest_paths={"ffxiii": MANIFESTS / "ffxiii.json"},
                      steam_library_paths=[]).download_filter("ff13", "hdmodelsplus")
    assert ".zip" in limits["suffixes"] and 0 < limits["minBytes"] < 83_360_725


def test_its_for_xiii_only(tmp_path, monkeypatch):
    from tests.test_mod_drops import library_with

    library, _, paths = library_with(tmp_path)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    bridge = UiBridge(manifest_paths=paths, steam_library_paths=[library])

    done = bridge.install_mod("ff13-2", str(download(tmp_path)), None, "Ultimate")

    assert not done["ok"] and "is for Final Fantasy XIII." in done["error"], done


def test_a_download_whose_tool_an_antivirus_took_still_builds(world):
    """the exe's never run, so a download missing it is whole enough"""
    bridge, game, tmp = world
    drop = download(tmp)
    (drop / "SupportFiles" / models_plus.TOOL).unlink()

    done = bridge.install_mod("ff13", str(drop), None, "High Quality")

    assert done["ok"], done


def test_a_download_missing_its_support_files_says_so(world):
    import shutil
    bridge, game, tmp = world
    drop = download(tmp)
    shutil.rmtree(drop / "SupportFiles")

    done = bridge.install_mod("ff13", str(drop))

    assert not done["ok"] and "missing its SupportFiles folder" in done["error"], done


def test_a_recipe_climbing_out_of_its_folder_is_never_handed_over():
    text = "42\r\n0\r\n0\r\n0\r\n1\r\nchr\\pc\\..\\..\\x\\bin\\x.win32\r\n"
    with pytest.raises(models_plus.ModelsError):
        models_plus.recipe(text)
    reads, writes = models_plus.recipe(recipe("Lightning").decode())
    assert reads == [model("c001", "trb")[:-4], model("c201", "trb")[:-4]]
    assert writes == [model("c201", "trb"), model("c201", "imgb")]


@pytest.fixture
def xiii(tmp_path, monkeypatch):
    """
    the HD Project's world, with XIII's real Playable Characters list and the
    six in its index. Its ff13tool unpacks what the archive really holds, the
    way the real one does: the world's own writes each entry as V's at its
    size, which turns any drawer mod back into an original on the repack
    """
    from patcher.mods import hd_runner
    from tests.test_first_press import Xiii, tables
    from tests.test_hd_install import FF13TOOL
    from tests.test_mod_drops_hd import with_characters

    world = Xiii(tmp_path, monkeypatch)
    with_characters(world)
    tables(monkeypatch, dict(hd_vanilla.index_sizes(world.index)))
    faithful = FF13TOOL.replace(
        "        target.write_bytes(b'V' * max(entry.uncompressed, 1))",
        "        payload = blob.read_bytes()[entry.byte_offset:entry.byte_offset + entry.stored]\n"
        "        if entry.is_compressed:\n"
        "            payload = zlib.decompress(payload)\n"
        "        target.write_bytes(payload or b'V')")
    assert faithful != FF13TOOL
    stub(world.mod / "SupportFiles", hd_runner.TOOLS["pack"], faithful)
    return world


def live(xiii, path: str) -> bytes:
    from patcher.mods.hd_zones import read_record

    index, archive = xiii.pair()
    return read_record(archive, game_files.entries(index)[path])


def test_the_hd_project_after_it_reads_the_characters_as_in_and_leaves_them(xiii, tmp_path):
    done = xiii.api.install_mod("ff13", str(download(tmp_path)), None, "Ultimate")
    assert done["ok"], done
    ours = {p: live(xiii, p) for p in (model("c201", "trb"), model("c004", "imgb"))}

    answer = xiii.survey()
    assert answer["covered"] == {"pcs": ["HD Models Plus"]} and answer["applied"] == [], answer

    # ticked anyway: the rest of the category gets built, these six stay
    ending, lines = xiii.install(["pcs"])
    assert ending["stage"] == "done", ending
    assert {p: live(xiii, p) for p in ours} == ours
    said = " ".join(e["text"] for e in lines)
    assert "HD Models Plus from the Mods drawer, so left as they are" in said, said
    listed = {m["name"]: m for m in xiii.api.list_addons("ff13")["mods"]}
    assert listed["HD Models Plus"]["state"] == "in", listed


def test_dropped_after_the_hd_projects_characters_it_goes_on_top(xiii, tmp_path):
    ending, _ = xiii.install(["pcs"])
    assert ending["stage"] == "done", ending
    hd = {p: live(xiii, p) for p in (model("c201", "trb"), model("c006", "imgb"))}
    assert all(set(data) != {ord("V")} for data in hd.values()), "the HD Project's are in"

    done = xiii.api.install_mod("ff13", str(download(tmp_path)), None, "Ultimate")

    # built from the originals in the backup the HD install took, not from HD art
    assert done["ok"], done
    assert built_by(live(xiii, model("c201", "trb"))) == "X_Lightning_Hair2"
    assert built_by(live(xiii, model("c006", "imgb"))) == "X_Fang1"
    assert "pcs" in xiii.survey()["applied"]

    assert xiii.api.remove_addon("ff13", "HD Models Plus", "files")["ok"]
    assert {p: live(xiii, p) for p in hd} == hd


def test_the_real_builder_makes_what_the_drawer_puts_in(tmp_path, monkeypatch):
    """High Quality's six recipes through hdmodels on small real-shaped models"""
    from tests import hdm_world as W

    originals, recipes = {}, {}
    for name in models_plus.VERSIONS["High Quality"]:
        field, battle = READS[name]
        tex, size = W.txb(0x18, 8, 8, 2)
        for mid in (field, battle):
            originals[model(mid, "trb")] = W.trb(
                [b"SEDBabc\0" + mid.encode().ljust(24, b"\0"), tex],
                names=[b"F00\\a", f"c_{mid}".encode(), b"m1", b"m2"])
            originals[model(mid, "imgb")] = bytes((n * 7 + len(mid)) & 0xFF for n in range(size))
        recipes[name] = W.recipe([f"chr\\pc\\{field}\\bin\\{field}.win32",
                                  f"chr\\pc\\{battle}\\bin\\{battle}.win32"],
                                 f"chr\\pc\\{battle}\\bin\\{battle}.win32",
                                 [(0, 0), (1, 1), (0, 2), (0, 3)],
                                 swaps=[(f"c_{field}", f"c_{battle}")])
    monkeypatch.setattr(models_plus, "originals", lambda *a, **k: dict(originals))
    root = tmp_path / "HD Models Plus"
    for name, text in recipes.items():
        (root / "Data").mkdir(parents=True, exist_ok=True)
        (root / "Data" / f"{name}.txt").write_bytes(text)
    (root / "SupportFiles").mkdir()
    (root / "SupportFiles" / "FFXIIItrbSetup.bat").write_bytes(b"@echo off\r\n")
    game = tmp_path / "game"
    game.mkdir()

    class Files:
        def read(self, path):
            low = path.replace("\\", "/").lower()
            if low.startswith("models/orig/"):
                return originals.get(low[len("models/orig/"):])
            hit = root / Path(*path.replace("\\", "/").split("/"))
            return hit.read_bytes() if hit.is_file() else None

    with game_files.Source(root) as source:
        work = models_plus.build(game, source, "", "High Quality")
    built = {p.relative_to(models_plus.built_at(work)).as_posix(): p.read_bytes()
             for p in models_plus.built_at(work).rglob("*") if p.is_file()}
    want = {}
    for name in models_plus.VERSIONS["High Quality"]:
        for path, data in builder.run(recipes[name], Files()).items():
            want[path.replace("\\", "/").lower().removeprefix("models/mod/")] = data
    assert {k.lower(): v for k, v in built.items()} == want
    assert all(b"c_c20" in data for path, data in want.items() if path.endswith(".trb"))
