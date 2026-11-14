"""
Reading the HD Project's data files rather than running its batch script.

Every fixture in tests/data/hd is the mod's own, not something shaped to make a
parser pass. models_sample.bin is rebuilt from two of the real container's
smallest blobs so it stays small, in the real layout, with real contents.
"""

import struct
from pathlib import Path

import pytest

from patcher.mods.hd_recipe import (RecipeError, read_category, read_container,
                                    read_recipe, survey)

DATA = Path(__file__).resolve().parent / "data" / "hd"
pytestmark = pytest.mark.skipif(not DATA.is_dir(), reason="no HD Project fixtures")


def test_a_flat_category_reads_as_one_section():
    category = read_category(DATA / "LoadHDEidolons.txt")

    assert category.label == "Eidolons"
    assert len(category.sections) == 1
    assert ("s", "s101") in category.members
    assert len(category.members) == 22


def test_the_zone_list_keeps_its_sections():
    """
    LoadHDEnvironments.txt carries a "#:Label" per zone rather than one at the
    top. Flattening it reports the whole file as belonging to whichever zone
    came last, which is how this was caught.
    """
    category = read_category(DATA / "LoadHDEnvironments.txt")

    assert len(category.sections) == 20, "sections were flattened"
    assert category.sections[0].label == "Lake_Bresha"
    assert [s.label for s in category.sections].count("Faultwarrens") == 1
    # one zone marker per section, and it is not split into a type and an id
    assert category.sections[0].members == [(None, "Z2")]


def test_a_zone_marker_is_not_given_an_invented_type():
    """
    Z8 does not resolve to a recipe file the way c001 does, so typing it would
    suggest it did
    """
    category = read_category(DATA / "LoadHDEnvironments.txt")
    assert all(kind is None for kind, _ in category.members)


def test_the_empty_pc_preset_is_a_real_answer():
    """
    LoadHDPCs.tx3 is a header and nothing else, which is how the mod says
    "install no characters". An empty file is not a parse failure.
    """
    category = read_category(DATA / "LoadHDPCs.tx3")

    assert category.members == []
    assert category.label == "PCs"


def test_the_full_pc_preset_lists_every_character():
    category = read_category(DATA / "LoadHDPCs.tx1")
    assert len(category.members) == 34
    assert ("P", "c001") in category.members


def test_a_recipe_names_the_two_files_it_rewrites():
    recipe = read_recipe(DATA / "n910.txt")

    assert recipe.target == "npc"
    assert recipe.game_paths == ["chr/npc/n910/bin/n910.win32.trb",
                                 "chr/npc/n910/bin/n910.win32.imgb"]
    assert recipe.header[0] == "42"


def test_a_recipe_reports_which_container_its_payload_comes_from():
    recipe = read_recipe(DATA / "n910.txt")

    names = {name for name, _ in recipe.blobs}
    containers = {container for _, container in recipe.blobs}
    assert names == {"X_n910_Lebreau.txt"}
    assert containers == {"Data/FFXIII_General_Models.bin"}, "backslashes leaked through"


@pytest.mark.parametrize("name", ["n910.txt", "f066.txt", "z010block022u.txt"])
def test_zone_npc_and_fa_recipes_only_reference_payloads(name):
    """
    the line that decides how much of this could ever be native. These three
    use :~ and :X alone.
    """
    assert not read_recipe(DATA / name).needs_mesh_surgery


def test_a_character_recipe_edits_geometry():
    """
    c001 reaches for :V, :E, :P, :J and :/SCULPTING. That is vertex level model
    editing, not file replacement, and no amount of archive work reproduces it.
    """
    recipe = read_recipe(DATA / "c001.txt")

    assert recipe.needs_mesh_surgery
    codes = {code for code, _ in recipe.ops}
    assert "V" in codes and "/SCULPTING" in codes


def test_a_truncated_recipe_is_refused(tmp_path):
    short = tmp_path / "broken.txt"
    short.write_text("42\n32\nnpc\n")

    with pytest.raises(RecipeError, match="too short"):
        read_recipe(short)


def test_the_container_reads_back_every_blob():
    blobs = read_container(DATA / "models_sample.bin")

    assert set(blobs) == {"f170_Stone.txt", "z10_022_Tomato.txt"}
    assert all(len(b) > 0 for b in blobs.values())


def test_the_payload_is_float_data_despite_the_txt_name():
    """
    the single clearest sign this mod edits models. The blobs are named .txt
    and hold vertex floats, so anything treating them as text is already wrong.
    """
    blob = read_container(DATA / "models_sample.bin")["f170_Stone.txt"]

    printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in blob[:256])
    assert printable < 200, "reads like text, which would change the whole picture"

    floats = struct.unpack_from("<8f", blob, 0)
    assert all(abs(f) < 1e4 for f in floats), f"not plausible vertex data: {floats}"


def test_a_container_pointing_outside_itself_is_refused(tmp_path):
    bad = tmp_path / "bad.bin"
    body = struct.pack("<I", 1) + b"x.txt".ljust(32, b"\x00") + struct.pack("<II", 44, 999_999)
    bad.write_bytes(body + b"payload")

    with pytest.raises(RecipeError, match="outside the file"):
        read_container(bad)


def test_an_absurd_entry_count_is_refused(tmp_path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(struct.pack("<I", 10_000_000) + b"nowhere near enough")

    with pytest.raises(RecipeError, match="size cannot hold"):
        read_container(bad)


def test_survey_splits_native_from_not(tmp_path):
    """
    the answer this module exists to give, per category rather than per mod:
    the character recipes are a different kind of work from the rest.

    the category file is written here rather than copied, because the mod's own
    NPC and Miscellaneous lists name 467 ids whose recipes are not in Data/ and
    would drown the thing being tested. The recipes themselves are the mod's.
    """
    import shutil
    for name in ("n910.txt", "f066.txt", "c001.txt"):
        shutil.copy(DATA / name, tmp_path / name)
    (tmp_path / "LoadHDMixed.txt").write_text("#:Mixed\nn n910\nf f066\nP c001\n")

    result = survey(tmp_path)

    assert set(result["blob_only"]) == {"f066", "n910"}
    assert set(result["mesh_surgery"]) == {"c001"}
    assert not result["missing_recipes"]


def test_survey_reports_ids_it_has_no_recipe_for():
    """
    the shipped Data/ carries a sample of recipes, not all of them. Saying so
    beats reporting the rest as having nothing to do.
    """
    result = survey(DATA)

    assert result["missing_recipes"], "ids with no recipe file went unreported"
    assert set(result["mesh_surgery"]) == {"c001"}
