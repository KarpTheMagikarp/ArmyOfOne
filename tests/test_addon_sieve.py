"""
The addon sieve: a dropped archive becomes a folder NovaFix will load, or a
refusal that says why.
"""

import json
import struct
import zipfile
from pathlib import Path

import pytest

from patcher.addon_sieve import (
    RECORD_NAME,
    SieveError,
    ingest_archive,
    install,
    installed_mods,
    normalise_mod_name,
    remove_mod,
    sift,
)

I386 = 0x014C
AMD64 = 0x8664


def pe(machine=I386, tag=b""):
    """a minimal but real PE header, since bitness is read from the file"""
    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)
    return bytes(dos) + b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 18 + tag


def make_zip(tmp_path, name, files):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, content in files.items():
            zf.writestr(member, content)
    return path


@pytest.mark.parametrize("filename,expected", [
    ("Better Lighting-217-1-2-0-1699999999.zip", "Better_Lighting"),
    ("Better_Lighting-217-1-2-0.zip", "Better_Lighting"),
    ("FFXIII-2_AudioFix-43-1-1-0.zip", "FFXIII-2_AudioFix"),
    ("SomeMod.zip", "SomeMod"),
    ("pack.tar.gz", "pack"),
])
def test_the_nexus_version_tail_is_stripped(filename, expected):
    assert normalise_mod_name(filename) == expected


@pytest.mark.parametrize("filename,expected", [
    ("FFXIII-2.zip", "FFXIII-2"),
    ("Chapter-13.zip", "Chapter-13"),
    ("Mod_2024.zip", "Mod_2024"),
    ("4GB Patch.zip", "4GB_Patch"),
])
def test_meaningful_trailing_numbers_are_not_mistaken_for_a_version(filename, expected):
    """
    a looser rule read "FFXIII-2" as a version tail and filed it as "FFXIII".
    requiring three or more groups makes the Nexus pattern distinctive enough
    to match while leaving real names alone.
    """
    assert normalise_mod_name(filename) == expected


def test_a_name_that_is_only_digits_survives():
    """stripping the tail must not leave nothing behind"""
    assert normalise_mod_name("2013.zip") == "2013"
    assert normalise_mod_name("-1-2-3.zip")


def stage(tmp_path, files):
    root = tmp_path / "staging"
    for member, content in files.items():
        target = root / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return root


def test_only_payloads_are_kept(tmp_path):
    staging = stage(tmp_path, {
        "Better/hook.dll": pe(),
        "Better/hook.ini": b"[settings]\nlevel=1\n",
        "Better/readme.txt": b"docs",
        "Better/preview.png": b"\x89PNG",
        "__MACOSX/._hook.dll": b"junk",
    })

    result = sift(staging, "Better-1-0.zip")

    assert sorted(p.name for p in result.payloads) == ["hook.dll", "hook.ini"]
    assert any("readme.txt" in i for i in result.ignored)
    assert not any("__MACOSX" in str(p.source) for p in result.payloads)


def test_an_archive_with_no_payloads_is_refused(tmp_path):
    archive = make_zip(tmp_path, "photos.zip", {"beach.jpg": b"\xff\xd8", "notes.txt": b"hi"})

    result = ingest_archive(archive, tmp_path / "addons")

    assert result["ok"] is False
    assert result["error"] == "No compatible mod files found in archive."
    assert not (tmp_path / "addons").exists() or not any((tmp_path / "addons").iterdir())


def test_a_64_bit_dll_is_refused_with_the_reason(tmp_path):
    """NovaFix checks IMAGE_FILE_MACHINE_I386 and silently skips anything else"""
    staging = stage(tmp_path, {"mod/hook.dll": pe(AMD64), "mod/hook.ini": b"x"})

    result = sift(staging, "mod.zip")

    assert result.payloads == []
    assert any("64-bit" in reason for _, reason in result.rejected)


def test_an_x86_x64_pair_keeps_the_32_bit_one(tmp_path):
    """archives ship both; picking by walk order is a coin flip"""
    staging = stage(tmp_path, {
        "mod/x64/hook.dll": pe(AMD64, b"sixtyfour"),
        "mod/x86/hook.dll": pe(I386, b"thirtytwo"),
    })

    result = sift(staging, "mod.zip")

    assert len(result.payloads) == 1
    assert result.payloads[0].machine == I386
    assert result.payloads[0].source.read_bytes().endswith(b"thirtytwo")


@pytest.mark.parametrize("name", ["d3d9.dll", "dxvk.dll", "ReShade32.dll", "d3d9_chain.dll"])
def test_renderer_backends_are_refused(tmp_path, name):
    """
    NovaFix treats these as renderer backends, not add-ons. dropping a ReShade
    or DXVK download in here would install something it then warns about.
    """
    staging = stage(tmp_path, {f"mod/{name}": pe()})

    result = sift(staging, "mod.zip")

    assert result.payloads == []
    assert any("renderer" in reason for _, reason in result.rejected)


def test_a_lone_ini_is_not_a_mod(tmp_path):
    staging = stage(tmp_path, {"mod/settings.ini": b"[a]\nb=1\n"})

    result = sift(staging, "mod.zip")

    assert result.payloads == []
    assert any("no .dll or .asi" in reason for _, reason in result.rejected)


def test_an_asi_script_counts_as_code(tmp_path):
    staging = stage(tmp_path, {"mod/script.asi": pe()})
    assert [p.name for p in sift(staging, "mod.zip").payloads] == ["script.asi"]


def test_something_that_is_not_a_pe_is_refused(tmp_path):
    staging = stage(tmp_path, {"mod/hook.dll": b"not a binary at all"})

    result = sift(staging, "mod.zip")

    assert result.payloads == []
    assert any("isn't a Windows binary" in reason for _, reason in result.rejected)


def test_a_mod_lands_in_its_own_folder(tmp_path):
    addons = tmp_path / "addons"
    archive = make_zip(tmp_path, "Better Lighting-217-1-0-0.zip", {
        "Better/hook.dll": pe(),
        "Better/hook.ini": b"[settings]\nlevel=1\n",
        "Better/readme.txt": b"docs",
    })

    result = ingest_archive(archive, addons)

    assert result["ok"] is True
    folder = addons / "Better_Lighting"
    assert sorted(p.name for p in folder.iterdir()) == [RECORD_NAME, "hook.dll", "hook.ini"]
    assert not (folder / "readme.txt").exists()


def test_staging_is_always_destroyed(tmp_path):
    import tempfile as _t
    before = set(Path(_t.gettempdir()).glob("temp_mod_staging_*"))
    archive = make_zip(tmp_path, "mod.zip", {"m/hook.dll": pe()})

    ingest_archive(archive, tmp_path / "addons")
    ingest_archive(tmp_path / "nope.zip", tmp_path / "addons")

    assert set(Path(_t.gettempdir()).glob("temp_mod_staging_*")) == before


def test_updating_replaces_code(tmp_path):
    addons = tmp_path / "addons"
    ingest_archive(make_zip(tmp_path, "Mod-1-0-0.zip", {"m/hook.dll": pe(tag=b"v1")}), addons)
    ingest_archive(make_zip(tmp_path, "Mod-1-2-0.zip", {"m/hook.dll": pe(tag=b"v2")}), addons)

    assert (addons / "Mod" / "hook.dll").read_bytes().endswith(b"v2")
    assert len(list(addons.iterdir())) == 1, "an update must not make a second folder"


def test_an_untouched_ini_is_replaced_on_update(tmp_path):
    addons = tmp_path / "addons"
    ingest_archive(make_zip(tmp_path, "Mod-1-0-0.zip",
                            {"m/hook.dll": pe(), "m/hook.ini": b"level=1\n"}), addons)

    result = ingest_archive(make_zip(tmp_path, "Mod-1-2-0.zip",
                                     {"m/hook.dll": pe(), "m/hook.ini": b"level=2\n"}), addons)

    assert (addons / "Mod" / "hook.ini").read_bytes() == b"level=2\n"
    assert result["preserved"] == []


def test_an_edited_ini_is_never_overwritten(tmp_path):
    """
    a mod ships an ini so it can be edited. resetting a user's settings on
    every update is the thing that makes people stop trusting a mod manager.
    """
    addons = tmp_path / "addons"
    ingest_archive(make_zip(tmp_path, "Mod-1-0-0.zip",
                            {"m/hook.dll": pe(), "m/hook.ini": b"level=1\n"}), addons)
    (addons / "Mod" / "hook.ini").write_bytes(b"level=99\n; tuned by hand\n")

    result = ingest_archive(make_zip(tmp_path, "Mod-1-2-0.zip",
                                     {"m/hook.dll": pe(), "m/hook.ini": b"level=2\n"}), addons)

    assert (addons / "Mod" / "hook.ini").read_bytes() == b"level=99\n; tuned by hand\n"
    assert (addons / "Mod" / "hook.ini.new").read_bytes() == b"level=2\n"
    assert result["preserved"] == ["hook.ini"]


def test_an_ini_we_never_installed_is_left_alone(tmp_path):
    """the user put it there; it is not ours to replace"""
    addons = tmp_path / "addons"
    ingest_archive(make_zip(tmp_path, "Mod-1-0-0.zip", {"m/hook.dll": pe()}), addons)
    (addons / "Mod" / "hook.ini").write_bytes(b"mine\n")

    ingest_archive(make_zip(tmp_path, "Mod-1-2-0.zip",
                            {"m/hook.dll": pe(), "m/hook.ini": b"theirs\n"}), addons)

    assert (addons / "Mod" / "hook.ini").read_bytes() == b"mine\n"
    assert (addons / "Mod" / "hook.ini.new").read_bytes() == b"theirs\n"


def test_the_record_tracks_what_we_installed(tmp_path):
    addons = tmp_path / "addons"
    ingest_archive(make_zip(tmp_path, "Mod-1-0-0.zip",
                            {"m/hook.dll": pe(), "m/hook.ini": b"level=1\n"}), addons)

    record = json.loads((addons / "Mod" / RECORD_NAME).read_text(encoding="utf-8"))

    assert record["mod"] == "Mod"
    assert set(record["files"]) == {"hook.dll", "hook.ini"}


def test_installed_mods_lists_what_is_there(tmp_path):
    addons = tmp_path / "addons"
    ingest_archive(make_zip(tmp_path, "Alpha.zip", {"m/a.dll": pe()}), addons)
    ingest_archive(make_zip(tmp_path, "Beta.zip", {"m/b.asi": pe()}), addons)

    mods = installed_mods(addons)

    assert [m["name"] for m in mods] == ["Alpha", "Beta"]
    assert mods[0]["files"] == ["a.dll"]
    assert all(m["managed"] for m in mods)


def test_removing_a_mod_deletes_its_folder(tmp_path):
    addons = tmp_path / "addons"
    ingest_archive(make_zip(tmp_path, "Mod.zip", {"m/hook.dll": pe()}), addons)

    assert remove_mod(addons, "Mod")["ok"] is True
    assert not (addons / "Mod").exists()


def test_a_folder_we_did_not_install_is_not_removed(tmp_path):
    """a directory someone made by hand is theirs"""
    addons = tmp_path / "addons"
    (addons / "TheirMod").mkdir(parents=True)
    (addons / "TheirMod" / "x.dll").write_bytes(pe())

    result = remove_mod(addons, "TheirMod")

    assert result["ok"] is False
    assert "wasn't installed by this patcher" in result["error"]
    assert (addons / "TheirMod" / "x.dll").exists()


def test_an_update_is_matched_by_its_dll_not_its_filename(tmp_path):
    """
    "Mod v1.2.zip" and "Mod v1.3.zip" share no normalised name but ship the
    same dll. treating them as separate mods means NovaFix loads that hook
    twice, which is worse than an ugly folder name.
    """
    addons = tmp_path / "addons"
    first = ingest_archive(make_zip(tmp_path, "Better Lighting v1.2.zip",
                                    {"m/BetterLighting.dll": pe(tag=b"v1")}), addons)
    second = ingest_archive(make_zip(tmp_path, "Better Lighting v1.3.zip",
                                     {"m/BetterLighting.dll": pe(tag=b"v2")}), addons)

    assert first["matchedExisting"] is False
    assert second["matchedExisting"] is True
    assert second["modName"] == first["modName"]
    assert len([p for p in addons.iterdir() if p.is_dir()]) == 1
    assert (addons / first["modName"] / "BetterLighting.dll").read_bytes().endswith(b"v2")


def test_a_completely_renamed_archive_still_updates_in_place(tmp_path):
    addons = tmp_path / "addons"
    ingest_archive(make_zip(tmp_path, "Better Lighting-217-1-0-0.zip",
                            {"m/BetterLighting.dll": pe(tag=b"v1")}), addons)
    result = ingest_archive(make_zip(tmp_path, "betterlighting_FINAL_reupload.zip",
                                     {"m/BetterLighting.dll": pe(tag=b"v2")}), addons)

    assert result["matchedExisting"] is True
    # the folder keeps whatever the first install named it; identity comes
    # from the payload, so a later rename of the archive changes nothing
    assert len([p for p in addons.iterdir() if p.is_dir()]) == 1


def test_a_different_mod_gets_its_own_folder(tmp_path):
    """matching on payload must not collapse two unrelated mods together"""
    addons = tmp_path / "addons"
    ingest_archive(make_zip(tmp_path, "Alpha.zip", {"m/alpha.dll": pe()}), addons)
    ingest_archive(make_zip(tmp_path, "Beta.zip", {"m/beta.dll": pe()}), addons)

    assert sorted(p.name for p in addons.iterdir() if p.is_dir()) == ["Alpha", "Beta"]


def test_a_hand_made_folder_is_never_written_into(tmp_path):
    """
    it has no record, so it is not ours. writing an update into someone's
    hand-assembled folder would be the same overreach as deleting it.
    """
    addons = tmp_path / "addons"
    theirs = addons / "TheirSetup"
    theirs.mkdir(parents=True)
    (theirs / "hook.dll").write_bytes(pe(tag=b"theirs"))

    result = ingest_archive(make_zip(tmp_path, "Something.zip",
                                     {"m/hook.dll": pe(tag=b"ours")}), addons)

    assert result["matchedExisting"] is False
    assert result["modName"] == "Something"
    assert (theirs / "hook.dll").read_bytes().endswith(b"theirs")


@pytest.mark.parametrize("archive,payload,expected", [
    # the archive name is the same thing wearing upload junk
    ("betterlighting_FINAL_reupload.zip", "BetterLighting.dll", "BetterLighting"),
    # a browser-assigned filename says nothing; the dll does
    ("download (3).zip", "ChocoboSpeed.dll", "ChocoboSpeed"),
    # a generic dll stem is no better than the archive name
    ("Better Lighting-217-1-2-0.zip", "hook.dll", "Better_Lighting"),
    # a clean archive name is the author's own title, so keep it
    ("Crystarium Overhaul.zip", "CrystOver.dll", "Crystarium_Overhaul"),
    # noise is judged on the filename as dropped, before cleaning removes it
    ("Mod v1.4 FINAL.zip", "ParadigmTweaks.dll", "ParadigmTweaks"),
    # a meaningful trailing number is not a version
    ("FFXIII-2.zip", "hook.dll", "FFXIII-2"),
])
def test_the_folder_name_is_derived_not_asked_for(tmp_path, archive, payload, expected):
    """
    a popup for every install is the thing that makes a mod manager tedious.
    the archive name and the payload name between them are enough.
    """
    folder = tmp_path / archive.replace(" ", "_").replace("(", "").replace(")", "")
    folder.mkdir()
    path = folder / archive
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"m/{payload}", pe())

    result = ingest_archive(path, folder / "addons")

    assert result["modName"] == expected


def test_noise_is_judged_on_the_original_filename(tmp_path):
    """
    "Mod v1.4 FINAL.zip" cleans to "Mod", which looks like a fine title until
    you see what it came from. checking the cleaned name finds no noise and
    keeps a useless folder called Mod.
    """
    path = tmp_path / "Mod v1.4 FINAL.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("m/ParadigmTweaks.dll", pe())

    assert ingest_archive(path, tmp_path / "addons")["modName"] == "ParadigmTweaks"


@pytest.mark.parametrize("filename,expected", [
    ("Better Lighting v1.2.zip", "Better_Lighting"),
    ("Some Mod 2.0.zip", "Some_Mod"),
    ("Thing v1.4 FINAL.zip", "Thing"),
])
def test_v_prefixed_and_dotted_versions_are_stripped(filename, expected):
    """
    safer than bare trailing digits: a 'v' or a dotted decimal is a version
    almost every time, while "FFXIII-2" is neither.
    """
    assert normalise_mod_name(filename) == expected


def test_a_short_or_generic_stem_never_wins(tmp_path):
    path = tmp_path / "Chocobo Racing Tweaks-99-1-0-0.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("m/crt.dll", pe())      # too short to be a title

    assert ingest_archive(path, tmp_path / "addons")["modName"] == "Chocobo_Racing_Tweaks"


def test_the_authors_capitalisation_is_kept_when_the_names_agree(tmp_path):
    """
    "Alpha.zip" shipping alpha.dll should stay Alpha. swapping in the dll stem
    when both spell the same name is a downgrade, not a tidy-up.
    """
    path = tmp_path / "Alpha.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("m/alpha.dll", pe())

    assert ingest_archive(path, tmp_path / "addons")["modName"] == "Alpha"


def leftover_folder(addons, name, files):
    """a folder the way an older drawer left it: the files, and our record of them"""
    import hashlib

    folder = addons / name
    folder.mkdir(parents=True)
    record = {}
    for leaf, data in files.items():
        (folder / leaf).write_bytes(data)
        record[leaf] = hashlib.sha256(data).hexdigest()
    (folder / RECORD_NAME).write_text(json.dumps({"mod": name, "files": record}))
    return folder


def net_libraries():
    from tests.helpers import make_export_pe

    return {"LocateFile.dll": make_export_pe(["Locate"], managed=True),
            "WhiteBinTools.dll": make_export_pe(["Tools"], managed=True)}


def test_the_net_libraries_an_older_drawer_put_in_are_off_the_list_and_tidied(tmp_path):
    """
    Red Rose's patcher runs on LocateFile.dll and WhiteBinTools.dll, src_22
    put them in NovaFix's add-ons, and NovaFix named them in its popup. They
    were never anybody's mod
    """
    from patcher.addon_sieve import tidy_leftovers
    from tests.helpers import make_export_pe

    addons = tmp_path / "addons"
    leftover_folder(addons, "LocateFile", net_libraries())
    leftover_folder(addons, "Better", {"Better.dll": make_export_pe(["Hook"])})

    assert [m["name"] for m in installed_mods(addons)] == ["Better"]
    assert tidy_leftovers(addons) == ["LocateFile"]
    assert sorted(p.name for p in addons.iterdir()) == ["Better"]
    assert tidy_leftovers(addons) == []


def test_a_leftover_holding_anything_else_stays_where_it_is(tmp_path):
    from patcher.addon_sieve import tidy_leftovers

    addons = tmp_path / "addons"
    folder = leftover_folder(addons, "LocateFile", net_libraries())
    (folder / "notes.txt").write_text("mine")
    changed = leftover_folder(addons, "Edited", net_libraries())
    (changed / "LocateFile.dll").write_bytes(b"MZ" + b"\0" * 300)

    assert tidy_leftovers(addons) == []
    assert (folder / "LocateFile.dll").is_file() and (folder / RECORD_NAME).is_file()
    assert (changed / RECORD_NAME).is_file()


def test_one_the_game_has_open_is_finished_next_time(tmp_path, monkeypatch):
    """the .dlls go last, so what's left is still a leftover and still off the list"""
    from patcher.addon_sieve import leftover, tidy_leftovers

    addons = tmp_path / "addons"
    folder = leftover_folder(addons, "LocateFile", dict(net_libraries(), **{"x.ini": b"a=1"}))
    real = Path.unlink

    def held(self, *args, **kwargs):
        if self.suffix == ".dll":
            raise PermissionError(13, "in use", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", held)
    assert tidy_leftovers(addons) == []
    assert leftover(folder) and installed_mods(addons) == []
    monkeypatch.setattr(Path, "unlink", real)
    assert tidy_leftovers(addons) == ["LocateFile"]


def test_a_folder_with_a_native_dll_beside_its_net_one_is_a_mod(tmp_path):
    from patcher.addon_sieve import leftover, tidy_leftovers
    from tests.helpers import make_export_pe

    addons = tmp_path / "addons"
    folder = leftover_folder(addons, "Hooked", {"Hook.dll": make_export_pe(["Hook"]),
                                                "Helper.dll": make_export_pe(["H"], managed=True)})
    assert not leftover(folder)
    assert [m["name"] for m in installed_mods(addons)] == ["Hooked"]
    assert tidy_leftovers(addons) == []


def test_a_leftover_with_anything_else_in_it_is_still_listed(tmp_path):
    """
    hidden and never tidied was the worst of both: off the list, so no Remove,
    and still in NovaFix's popup. An edited ini and the .new beside it count
    """
    from patcher.addon_sieve import tidy_leftovers

    addons = tmp_path / "addons"
    noted = leftover_folder(addons, "Noted", net_libraries())
    (noted / "notes.txt").write_text("mine")
    edited = leftover_folder(addons, "Edited", dict(net_libraries(), **{"x.ini": b"a=1"}))
    (edited / "x.ini").write_bytes(b"a=2")
    renewed = leftover_folder(addons, "Renewed", dict(net_libraries(), **{"x.ini": b"a=1"}))
    (renewed / "x.ini.new").write_bytes(b"a=3")

    assert [m["name"] for m in installed_mods(addons)] == ["Edited", "Noted", "Renewed"]
    assert tidy_leftovers(addons) == []


@pytest.mark.parametrize("record", ["null", "[]", '{"files": null}', '{"files": []}',
                                    '{"mod": "x", "files": "LocateFile.dll"}'])
def test_a_record_in_some_other_shape_breaks_nothing(tmp_path, record):
    """one hand-edited record used to take the whole drawer's list down with it"""
    from patcher.addon_sieve import find_existing, tidy_leftovers

    addons = tmp_path / "addons"
    folder = leftover_folder(addons, "LocateFile", net_libraries())
    (folder / RECORD_NAME).write_text(record)

    assert [m["name"] for m in installed_mods(addons)] == ["LocateFile"]
    assert tidy_leftovers(addons) == []
    assert sorted(p.name for p in folder.iterdir()) == sorted([*net_libraries(), RECORD_NAME])

    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "Other.dll").write_bytes(pe())
    assert find_existing(addons, sift(staged, "Other.zip")) is None


def test_a_linked_folder_is_never_tidied_through(tmp_path):
    """a link in the add-ons folder points at files that aren't in it"""
    from patcher.addon_sieve import leftover, tidy_leftovers

    elsewhere = leftover_folder(tmp_path / "elsewhere", "LocateFile", net_libraries())
    addons = tmp_path / "addons"
    addons.mkdir()
    try:
        (addons / "LocateFile").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("no symlinks here")
    inner = leftover_folder(addons, "Inner", net_libraries())
    (inner / "LocateFile.dll").unlink()
    (inner / "LocateFile.dll").symlink_to(elsewhere / "LocateFile.dll")

    assert not leftover(addons / "LocateFile") and not leftover(inner)
    assert tidy_leftovers(addons) == []
    assert sorted(p.name for p in elsewhere.iterdir()) == sorted([*net_libraries(), RECORD_NAME])
