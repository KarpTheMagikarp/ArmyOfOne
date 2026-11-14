"""
The gap between the one machine this was built on and the ones it will run on.

Nothing here is exotic. A cloud synced Downloads folder is the Windows 11
default for a lot of people, and an install that quietly writes 1.3GB of
temporary files into it is an afternoon nobody will connect back to this app.
"""

from pathlib import Path

import pytest

from patcher.mods.hd_environment import Note, cloud_folder, survey


def test_a_redirected_downloads_folder_is_recognised():
    """the Windows 11 default for anybody signed into a Microsoft account"""
    assert cloud_folder(Path(r"C:\Users\Cody\OneDrive\Downloads\FF XIII HD")) == "OneDrive"


def test_a_work_account_names_the_tenant_after_it():
    assert cloud_folder(Path(r"C:\Users\Cody\OneDrive - Contoso\Mods\HD")) == "OneDrive"


def test_the_other_clients_count_too():
    assert cloud_folder(Path(r"C:\Users\C\Dropbox\mods")) == "Dropbox"
    assert cloud_folder(Path(r"C:\Users\C\Google Drive\mods")) == "Google Drive"


def test_a_folder_that_merely_contains_the_word_is_not_a_match():
    """
    matched on a whole path segment, so somebody's MyOneDriveBackups folder
    does not get a warning about a sync client they are not using
    """
    assert cloud_folder(Path(r"D:\MyOneDriveBackups\FF XIII HD")) is None
    assert cloud_folder(Path(r"D:\dropboxes\mods")) is None


def test_an_ordinary_path_produces_nothing():
    assert cloud_folder(Path(r"C:\FF13HD")) is None
    assert survey(Path(r"C:\FF13HD"), Path(r"D:\Steam\FFXIII")) == []


def test_the_warning_says_how_much_and_where_to_move_it():
    """
    "this might be slow" is not actionable. The number and the suggestion are
    the whole value of saying anything at all.
    """
    notes = survey(Path(r"C:\Users\C\OneDrive\Downloads\HD"),
                   Path(r"D:\Steam\FFXIII"), unpacked_bytes=1_389_977_317)

    assert len(notes) == 1
    assert notes[0].level == "warn", "this is slow, not broken, so it must not refuse"
    assert "1.3 GB" in notes[0].text
    assert "OneDrive" in notes[0].text
    assert "C:\\FF13HD" in notes[0].text, "it does not say what to do instead"


def test_a_synced_game_folder_is_its_own_warning():
    """
    a different failure: the sync client putting a file back mid write, which
    is corruption rather than slowness
    """
    notes = survey(Path(r"C:\FF13HD"), Path(r"C:\Users\C\OneDrive\Steam\FFXIII"))

    assert len(notes) == 1
    assert "Pausing" in notes[0].text


def test_both_at_once_are_both_reported():
    notes = survey(Path(r"C:\Users\C\OneDrive\HD"), Path(r"C:\Users\C\OneDrive\Steam\FFXIII"))

    assert len(notes) == 2
    assert all(isinstance(n, Note) for n in notes)


def test_placeholders_are_only_counted_where_the_attribute_exists(tmp_path):
    """
    st_file_attributes is Windows only, and a missing attribute has to read as
    "no placeholders" rather than taking the install down on Linux or in CI
    """
    (tmp_path / "a.bin").write_bytes(b"x")

    notes = survey(tmp_path, tmp_path)

    assert notes == [] or all("placeholder" not in n.text for n in notes)


def test_a_survey_that_throws_does_not_stop_an_install(tmp_path, monkeypatch):
    """a warning that cannot be produced is not a reason to refuse"""
    from patcher.mods import hd_install

    monkeypatch.setattr("patcher.mods.hd_environment.survey",
                        lambda *a: (_ for _ in ()).throw(OSError("nope")))
    prep = hd_install.Prep(variant="u")

    assert hd_install.environment_notes(tmp_path, prep, 0) == []
