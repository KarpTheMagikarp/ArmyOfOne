"""
A FOMOD's options as the drawer's own questions (patcher/mods/fomod.py): a
group at a time, a yes or no for each plugin of a group that takes any
number, and what the answers come to read like any drop.
"""

import pytest

import patcher.bridge as bridge_mod
from patcher.bridge import UiBridge
from patcher.mods import fomod
from tests.test_mod_drops import C108, library_with, vanilla, zipped

C201 = "chr/pc/c201/bin/c201.win32.imgb"

CONFIG = """<?xml version="1.0" encoding="UTF-16"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="http://qconsulting.ca/fo3/ModConfig5.0.xsd">
  <moduleName>Serah Outfits</moduleName>
  <requiredInstallFiles>
    <folder source="Core" destination="" />
  </requiredInstallFiles>
  <installSteps order="Explicit">
    <installStep name="Outfit">
      <optionalFileGroups order="Explicit">
        <group name="Colour" type="SelectExactlyOne">
          <plugins order="Explicit">
            <plugin name="Red">
              <description>The red one.</description>
              <files><folder source="Options\\Red" destination="" /></files>
              <conditionFlags><flag name="colour">red</flag></conditionFlags>
              <typeDescriptor><type name="Recommended"/></typeDescriptor>
            </plugin>
            <plugin name="Blue">
              <description>The blue one.</description>
              <files><folder source="Options\\Blue" destination="" /></files>
              <conditionFlags><flag name="colour">blue</flag></conditionFlags>
              <typeDescriptor><type name="Optional"/></typeDescriptor>
            </plugin>
            <plugin name="Broken">
              <files><folder source="Options\\Broken" destination="" /></files>
              <typeDescriptor><type name="NotUsable"/></typeDescriptor>
            </plugin>
          </plugins>
        </group>
        <group name="Extras" type="SelectAny">
          <plugins order="Explicit">
            <plugin name="Hood">
              <description>Lightning's hood.</description>
              <files><file source="Extras\\hood.imgb" destination="chr\\pc\\c201\\bin\\c201.win32.imgb" /></files>
              <typeDescriptor><type name="Optional"/></typeDescriptor>
            </plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
  </installSteps>
  <conditionalFileInstalls>
    <patterns>
      <pattern>
        <dependencies operator="And"><flagDependency flag="colour" value="blue"/></dependencies>
        <files><file source="Patches\\blue.txt" destination="notes\\blue.txt" /></files>
      </pattern>
    </patterns>
  </conditionalFileInstalls>
</config>"""


def drop(tmp_path, config=CONFIG, name="Serah Outfits-77-1-0-1760000000.zip"):
    return zipped(tmp_path, name, {
        "fomod/ModuleConfig.xml": config.encode("utf-16"),
        "fomod/info.xml": b"<fomod><Name>Serah Outfits</Name></fomod>",
        "Core/readme.txt": b"read me",
        f"Options/Red/{C108}": b"red" * 900,
        f"Options/Blue/{C108}": b"blue" * 900,
        f"Options/Broken/{C108}": b"broken" * 900,
        "Extras/hood.imgb": b"hood" * 900,
        "Patches/blue.txt": b"blue notes",
    })


class Reading:
    def __init__(self, path):
        import zipfile

        self.archive = zipfile.ZipFile(path)
        self.listed = [(i.filename, i.file_size) for i in self.archive.infolist()]

    def read(self, member):
        return self.archive.read(member)


def test_its_groups_are_asked_one_at_a_time_and_its_answers_pick_the_files(tmp_path):
    source = Reading(drop(tmp_path))
    config = fomod.read(source, source.listed)
    assert config.name == "Serah Outfits"

    picked, question = fomod.walk(config, {})
    assert question.title == "Colour" and question.options == ["Red", "Blue"]
    assert question.recommended == "Red" and question.about["Blue"] == "The blue one."
    assert question.more, "the hood's question is still to come"

    picked, question = fomod.walk(config, {question.key: "Blue"})
    assert question.title == "Extras: Hood" and question.options == [fomod.YES, fomod.NO]
    assert not question.more

    picked, question = fomod.walk(config, {"0": "Blue", "1.0": fomod.YES})
    assert question is None
    files = fomod.installed(config, picked, source.listed)
    # the required folder, the picked colour's, the hood over its path, and
    # the note the blue flag turns on
    assert files == {"readme.txt": "Core/readme.txt", C108: f"Options/Blue/{C108}",
                     C201: "Extras/hood.imgb", "notes/blue.txt": "Patches/blue.txt"}


def test_a_plugin_left_out_or_not_usable_brings_none_of_its_files(tmp_path):
    source = Reading(drop(tmp_path))
    config = fomod.read(source, source.listed)
    picked, question = fomod.walk(config, {"0": "Red", "1.0": fomod.NO})
    assert question is None
    files = fomod.installed(config, picked, source.listed)
    assert files[C108] == f"Options/Red/{C108}"
    assert C201 not in files and "notes/blue.txt" not in files
    assert all("Broken" not in member for member in files.values())


def test_a_higher_priority_wins_a_path_and_the_later_one_among_equals():
    config = fomod.Config(name="x", root="", required=[
        ("a.imgb", "out.imgb", False, 5), ("b.imgb", "out.imgb", False, 0),
        ("c.imgb", "other.imgb", False, 0), ("d.imgb", "OTHER.imgb", False, 0)])
    files = fomod.installed(config, [], [("a.imgb", 1), ("b.imgb", 1), ("c.imgb", 1),
                                         ("d.imgb", 1)])
    assert files == {"out.imgb": "a.imgb", "OTHER.imgb": "d.imgb"}


def test_one_required_plugin_or_a_select_all_asks_nothing(tmp_path):
    config = """<config><moduleName>Fix</moduleName><installSteps order="Explicit">
      <installStep name="One"><optionalFileGroups order="Explicit">
        <group name="Only" type="SelectExactlyOne"><plugins order="Explicit">
          <plugin name="It"><files><folder source="A" destination=""/></files>
            <typeDescriptor><type name="Required"/></typeDescriptor></plugin>
        </plugins></group>
        <group name="Both" type="SelectAll"><plugins order="Explicit">
          <plugin name="B"><files><folder source="B" destination=""/></files>
            <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
        </plugins></group>
      </optionalFileGroups></installStep></installSteps></config>"""
    source = Reading(zipped(tmp_path, "Fix.zip", {"fomod/ModuleConfig.xml": config.encode(),
                                                   "A/a.ztr": b"a", "B/b.ztr": b"b"}))
    picked, question = fomod.walk(fomod.read(source, source.listed), {})
    assert question is None and [p.name for p in picked] == ["It", "B"]


def test_a_step_its_flags_hide_is_never_asked(tmp_path):
    config = """<config><moduleName>Steps</moduleName><installSteps order="Explicit">
      <installStep name="First"><optionalFileGroups order="Explicit">
        <group name="Want more" type="SelectExactlyOne"><plugins order="Explicit">
          <plugin name="Yes"><conditionFlags><flag name="more">on</flag></conditionFlags>
            <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
          <plugin name="No"><typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
        </plugins></group></optionalFileGroups></installStep>
      <installStep name="Second">
        <visible><dependencies operator="And"><flagDependency flag="more" value="on"/></dependencies></visible>
        <optionalFileGroups order="Explicit">
        <group name="Which" type="SelectExactlyOne"><plugins order="Explicit">
          <plugin name="X"><files><folder source="X" destination=""/></files>
            <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
          <plugin name="Y"><files><folder source="Y" destination=""/></files>
            <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
        </plugins></group></optionalFileGroups></installStep>
      </installSteps></config>"""
    source = Reading(zipped(tmp_path, "Steps.zip", {"fomod/ModuleConfig.xml": config.encode()}))
    parsed = fomod.read(source, source.listed)
    assert fomod.walk(parsed, {"0": "No"})[1] is None
    assert fomod.walk(parsed, {"0": "Yes"})[1].title == "Which"


@pytest.fixture
def world(tmp_path, monkeypatch):
    library, game, paths = library_with(tmp_path)
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    return UiBridge(manifest_paths=paths, steam_library_paths=[library]), game, tmp_path


def test_the_drawer_asks_each_question_then_installs_only_what_was_picked(world):
    """
    before, a FOMOD went in as every option's files at once wherever they
    didn't clash, and as a version question only where they did
    """
    bridge, game, tmp = world
    before = vanilla(game)
    path = str(drop(tmp))
    first = bridge.install_mod("ff13-2", path)
    assert first["kind"] == "versions" and first["question"] == "Colour"
    assert first["versions"] == ["Red", "Blue"] and first["recommended"] == "Red"
    second = bridge.install_mod("ff13-2", path, version="Blue")
    assert second["kind"] == "versions" and second["question"] == "Extras: Hood"
    done = bridge.install_mod("ff13-2", path, version=fomod.NO)
    assert done["ok"], done
    assert "1 game file into both voice sets" in done["message"]
    rows = [m for m in bridge.list_addons("ff13-2")["mods"] if m.get("kind") == "files"]
    assert [m["name"] for m in rows] == ["Serah Outfits"]
    from patcher.mods import game_files

    pair = game_files.pairs(game)[0]
    entry = game_files.entries(pair.index)[C108]
    assert entry.uncompressed == len(b"blue" * 900)
    assert bridge.remove_addon("ff13-2", "Serah Outfits", "files")["ok"]
    assert vanilla(game) == before


def test_a_drop_with_no_answer_starts_the_questions_over(world):
    bridge, game, tmp = world
    path = str(drop(tmp))
    bridge.install_mod("ff13-2", path)
    bridge.install_mod("ff13-2", path, version="Blue")
    again = bridge.install_mod("ff13-2", path)
    assert again["question"] == "Colour"


def test_a_config_that_wont_read_says_so(world):
    bridge, game, tmp = world
    broken = drop(tmp, config="<config><moduleName>Oops</moduleName>", name="Oops.zip")
    said = bridge.install_mod("ff13-2", str(broken))
    assert not said["ok"] and "installer options won't read" in said["error"]
