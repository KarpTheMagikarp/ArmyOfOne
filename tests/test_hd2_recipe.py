"""
Telling FF XIII-2 HD apart from everything else, and reading its data files.

The fixtures under data/hd2 are the mod's own lists and recipes, unedited.
"""

import zipfile
from pathlib import Path

import pytest

from patcher.mods import hd2_recipe
from patcher.mods.hd2_recipe import (RecipeError, archive_holds, detect, model_ids,
                                     recipe_paths)
from tests.hd2_world import DATA2, make_mod

REPO = Path(__file__).resolve().parent.parent


def test_the_mod_as_its_zip_unpacks_is_recognised(tmp_path, monkeypatch):
    root = make_mod(tmp_path, monkeypatch)

    found = detect(root)

    assert found is not None
    assert found["name"] == "FFXIII-2 HD"
    assert Path(found["root"]) == root
    # lower case, as the zip ships them, and handed on as they really are
    assert Path(found["data"]).name == "data"
    assert Path(found["support"]).name == "supportfiles"
    assert found["lists"] == ["PCModels.txt", "NPCModels.txt"]
    assert found["zones"] == ["z0010"]
    assert found["groups"] == ["fa", "npc", "mon", "pc", "weapon"]


def test_the_folder_it_was_unpacked_into_is_found_one_level_down(tmp_path, monkeypatch):
    make_mod(tmp_path / "Downloads", monkeypatch)

    found = detect(tmp_path / "Downloads")

    assert found is not None
    assert Path(found["root"]).name == "FF XIII-2 HD"


def test_the_bat_spelling_of_the_folders_is_recognised_too(tmp_path, monkeypatch):
    root = make_mod(tmp_path, monkeypatch)
    (root / "data").rename(root / "Data")
    (root / "supportfiles").rename(root / "SupportFiles")

    assert detect(root) is not None


@pytest.mark.parametrize("missing", ["data/FFXIII_Ultimate_Models.bin",
                                     "supportfiles/FFXIII2_HDProject.exe",
                                     "data/PCModels.txt+data/NPCModels.txt"])
def test_one_landmark_alone_is_not_the_mod(tmp_path, monkeypatch, missing):
    root = make_mod(tmp_path, monkeypatch)
    for rel in missing.split("+"):
        (root / rel).unlink()

    assert detect(root) is None


def test_neither_mod_is_mistaken_for_the_other(tmp_path, monkeypatch):
    from patcher.mods.hd_recipe import detect as detect_xiii
    from tests.test_hd_install import make_mod as make_xiii_mod

    xiii2 = make_mod(tmp_path / "two", monkeypatch)
    xiii, data, _ = make_xiii_mod(tmp_path / "one", monkeypatch)
    (data / "FFXIII_General_Models.bin").write_bytes(b"\x00" * 4)

    assert detect_xiii(xiii) is not None and detect(xiii2) is not None
    assert detect_xiii(xiii2) is None
    assert detect(xiii) is None


def test_the_zip_is_known_from_its_listing_without_unpacking(tmp_path):
    # what Compress-Archive wrote for the user: backslashes, no wrapper folder
    names = ["data\\c108.txt", "data\\FFXIII_Ultimate_Models.bin", "data\\PCModels.txt",
             "supportfiles\\FFXIII2_HDProject.exe", "supportfiles\\ff13tool.exe",
             "FFXIII-2 HD.bat", "textures\\pc\\c101C_01.bin"]
    assert archive_holds(names)
    # and zipped up inside a folder, forward slashes, the way 7-Zip writes it
    assert archive_holds([f"FF XIII-2 HD/{n.replace(chr(92), '/')}" for n in names])


def test_xiii_s_hd_project_is_not_this_mod_by_listing():
    names = ["Data/LoadHDNPCs.txt", "Data/FFXIII_General_Models.bin",
             "SupportFiles/FFXIII_HDProject.exe", "SupportFiles/ff13tool.exe"]
    assert not archive_holds(names)


def test_the_real_model_lists_read_as_their_bat_reads_them():
    pcs = model_ids(DATA2 / "PCModels.txt")
    npcs = model_ids(DATA2 / "NPCModels.txt")

    assert len(pcs) == 23 and pcs[0] == "c108" and pcs[-1] == "c240"
    assert len(npcs) == 43 and npcs[0] == "n012" and npcs[-1] == "n925"


def test_comments_blanks_and_paths_in_a_list_are_skipped(tmp_path):
    listing = tmp_path / "PCModels.txt"
    listing.write_text("; the party\r\nc108\r\n\r\n..\\..\\evil\r\nc108\r\nC109\r\n",
                       encoding="latin-1")

    assert model_ids(listing) == ["c108", "c109"]


def test_a_recipe_names_the_model_it_rewrites():
    assert recipe_paths(DATA2 / "c108.txt", "pc", "c108") == (
        "chr\\pc\\c108\\bin\\c108.win32.trb", "chr\\pc\\c108\\bin\\c108.win32.imgb")
    assert recipe_paths(DATA2 / "n910.txt", "npc", "n910")[1].endswith("n910.win32.imgb")


def test_a_recipe_that_rewrites_somebody_else_is_refused(tmp_path):
    # the paths are copied into the archive, so a recipe pointing at another
    # model would put this id's output over that one
    doctored = tmp_path / "c108.txt"
    doctored.write_text((DATA2 / "c108.txt").read_text(encoding="latin-1")
                        .replace("c108\\bin\\c108", "c159\\bin\\c159"), encoding="latin-1")

    with pytest.raises(RecipeError, match="not c108"):
        recipe_paths(doctored, "pc", "c108")


def test_six_header_lines_or_seven_the_paths_are_found():
    # XIII's recipes carry one more header line than XIII-2's
    xiii = REPO / "tests" / "data" / "hd" / "n910.txt"
    assert recipe_paths(xiii, "npc", "n910")[0] == "chr\\npc\\n910\\bin\\n910.win32.trb"


def test_zones_are_read_off_the_texture_folders(tmp_path):
    textures = tmp_path / "textures"
    for name in ("z0010", "Z0210", "pc", "z010", "z00100"):
        (textures / name).mkdir(parents=True)
    (textures / "z0020").write_text("a file, not a zone")

    assert hd2_recipe.zones(textures) == ["z0010", "z0210"]


def test_a_dropped_zip_of_the_mod_unpacks_and_is_found(tmp_path, monkeypatch):
    """through the bridge, which is what the Mods drawer calls"""
    from patcher.bridge import UiBridge

    root = make_mod(tmp_path / "src", monkeypatch)
    archive = tmp_path / "FF XIII-2 HD-1-1-0-0-1618762296.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())

    bridge = UiBridge(manifest_paths={})
    assert bridge.identify_drop(str(archive), unpack=False)["kind"] == "maybe-hdproject"
    found = bridge.identify_drop(str(archive))

    assert found["kind"] == "hdproject"
    assert found["jobId"] == "hdproject2"
    assert found["detail"]["name"] == "FFXIII-2 HD"


def test_a_dropped_folder_says_which_mod_it_is(tmp_path, monkeypatch):
    from patcher.bridge import UiBridge
    from tests.test_hd_install import make_mod as make_xiii_mod

    bridge = UiBridge(manifest_paths={})
    two = make_mod(tmp_path / "two", monkeypatch)
    one, data, _ = make_xiii_mod(tmp_path / "one", monkeypatch)
    (data / "FFXIII_General_Models.bin").write_bytes(b"\x00" * 4)

    assert bridge.identify_drop(str(two))["jobId"] == "hdproject2"
    assert bridge.identify_drop(str(one))["jobId"] == "hdproject"
