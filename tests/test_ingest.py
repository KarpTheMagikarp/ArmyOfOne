import io
import zipfile
from pathlib import Path

import pytest

from patcher.ingest import GAME_ASSETS, IngestError, is_asset, match_to_index, scan
from patcher.ingest import Candidate

# real paths taken from a decrypted XIII-2 index
INDEX = [
    "vfx/field/weather/weather07/veffs.jp.win32.imgb",
    "vfx/field/weather/weather08/veffs.jp.win32.imgb",
    "vfx/chr/c109/v1/veffs.jp.win32.imgb",
    "chr/pc/c001/bin/c001.win32.imgb",
    "chr/pc/c001/bin/c001.win32.trb",
]


def make_zip(tmp_path, files):
    path = tmp_path / "mod.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def test_stacked_suffixes_are_recognised():
    """engine filenames stack extensions; a plain .suffix test misreads them"""
    assert is_asset("Data/vfx/field/weather/weather07/veffs.jp.win32.imgb")
    assert is_asset("chr/pc/c001/bin/c001.win32.trb")
    assert not is_asset("Readme.txt")
    assert not is_asset("Install Leviathan's Tears.bat")
    assert not is_asset("screenshots/preview.png")


def test_scan_separates_assets_from_documentation(tmp_path):
    archive = make_zip(tmp_path, {
        "Data/vfx/field/weather/weather07/veffs.jp.win32.imgb": b"rain" * 100,
        "Readme.txt": b"instructions",
        "Install.bat": b"@echo off",
        "preview.png": b"\x89PNG",
    })

    result = scan(archive)

    assert [c.name for c in result.candidates] == ["veffs.jp.win32.imgb"]
    assert sorted(result.ignored) == ["Install.bat", "Readme.txt", "preview.png"]


def test_matching_uses_path_depth_not_just_the_filename(tmp_path):
    """
    there are dozens of veffs.jp.win32.imgb, one per weather and effect
    folder. picking by filename alone writes rain data over something else.
    """
    archive = make_zip(tmp_path, {
        "Data/vfx/field/weather/weather08/veffs.jp.win32.imgb": b"rain",
    })

    result = scan(archive, INDEX)

    assert len(result.matched) == 1
    assert result.matched[0].game_path == "vfx/field/weather/weather08/veffs.jp.win32.imgb"


def test_a_bare_filename_match_is_refused(tmp_path):
    """not enough information to pick between identically named entries"""
    archive = make_zip(tmp_path, {"veffs.jp.win32.imgb": b"rain"})

    result = scan(archive, INDEX)

    assert result.matched == []
    assert "only the filename matched" in result.candidates[0].reason


def test_an_unknown_asset_is_reported_not_guessed(tmp_path):
    archive = make_zip(tmp_path, {"Data/chr/pc/c999/bin/c999.win32.imgb": b"x"})

    result = scan(archive, INDEX)

    assert result.matched == []
    assert "no entry in the game's index" in result.candidates[0].reason


def test_the_real_leviathans_tears_layout_resolves_both_targets(tmp_path):
    """the actual shape of that mod: two weather folders plus a BAT and readme"""
    archive = make_zip(tmp_path, {
        "Data/vfx/field/weather/weather07/veffs.jp.win32.imgb": b"a" * 90512,
        "Data/vfx/field/weather/weather08/veffs.jp.win32.imgb": b"b" * 90512,
        "Install Leviathan's Tears.bat": b"@echo off",
        "Readme.txt": b"instructions",
        "mod/Cocoon.exe": b"MZ",
    })

    result = scan(archive, INDEX)

    assert len(result.matched) == 2
    assert sorted(c.game_path for c in result.matched) == [
        "vfx/field/weather/weather07/veffs.jp.win32.imgb",
        "vfx/field/weather/weather08/veffs.jp.win32.imgb",
    ]
    # the bundled third-party tool is not an asset and must not be ingested
    assert "mod/Cocoon.exe" in result.ignored


def test_a_dropped_folder_works_the_same_as_an_archive(tmp_path):
    root = tmp_path / "extracted"
    target = root / "Data/vfx/field/weather/weather07"
    target.mkdir(parents=True)
    (target / "veffs.jp.win32.imgb").write_bytes(b"rain")
    (root / "Readme.txt").write_text("hi")

    result = scan(root, INDEX)

    assert len(result.matched) == 1
    assert result.ignored == ["Readme.txt"]


def test_a_rar_we_cannot_open_gives_an_instruction_a_person_can_follow(tmp_path):
    """
    Leviathan's Tears ships as a .rar and Python has no stdlib support. the
    message has to name the action, not the limitation.
    """
    fake = tmp_path / "mod.rar"
    fake.write_bytes(b"Rar!\x1a\x07")

    with pytest.raises(IngestError) as caught:
        scan(fake, allow_system_extract=False)

    message = str(caught.value)
    assert "Extract it" in message
    assert "Choose folder" in message
    assert "libarchive" not in message and "stdlib" not in message


def test_a_rar_is_tried_with_the_system_extractor_first(tmp_path, monkeypatch):
    """
    Windows ships bsdtar, which reads RAR on recent builds. worth one attempt
    before asking the user to do it by hand.
    """
    import patcher.ingest as ingest_mod

    extracted = tmp_path / "unpacked"
    target = extracted / "Data" / "vfx" / "field" / "weather" / "weather07"
    target.mkdir(parents=True)
    (target / "veffs.jp.win32.imgb").write_bytes(b"rain")

    monkeypatch.setattr(ingest_mod, "try_system_extract",
                        lambda archive, destination: extracted)
    fake = tmp_path / "tears.rar"
    fake.write_bytes(b"Rar!\x1a\x07")

    result = scan(fake, INDEX)

    assert len(result.matched) == 1
    assert result.matched[0].game_path.endswith("weather07/veffs.jp.win32.imgb")


def test_a_failed_system_extract_falls_back_to_the_instruction(tmp_path, monkeypatch):
    import patcher.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "try_system_extract", lambda a, d: None)
    fake = tmp_path / "mod.rar"
    fake.write_bytes(b"Rar!\x1a\x07")

    with pytest.raises(IngestError, match="Choose folder"):
        scan(fake)


def test_a_missing_file_is_an_error_not_a_crash(tmp_path):
    with pytest.raises(IngestError, match="does not exist"):
        scan(tmp_path / "nope.zip")


def test_scan_writes_nothing(tmp_path):
    """a user must be able to drop the wrong file and be told so safely"""
    archive = make_zip(tmp_path, {"Data/chr/pc/c001/bin/c001.win32.trb": b"x"})
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}

    scan(archive, INDEX)

    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


def slow_tar(parts, finishes=True):
    """
    a tar that unpacks a megabyte each time it's waited on, then ends, or
    never does. Killed, it's only gone once it's been waited on after
    """
    import subprocess

    class Tar:
        def __init__(self, args, **kwargs):
            self.into, self.code, self.left = Path(args[args.index("-C") + 1]), None, parts
            self.killed = False

        def wait(self, timeout=None):
            if self.killed:
                self.code = -9
            elif self.code is None and self.left:
                self.left -= 1
                (self.into / f"part{self.left}.bin").write_bytes(b"x" * (1 << 20))
            elif self.code is None and finishes:
                self.code = 0
            if self.code is None:
                raise subprocess.TimeoutExpired("tar", timeout)
            return self.code

        def poll(self):
            return self.code

        def kill(self):
            self.killed = True

    return Tar


@pytest.fixture
def system_tar(monkeypatch):
    import shutil
    import subprocess

    import patcher.ingest as ingest_mod

    def use(tar, listed=None):
        made = []

        def start(*args, **kwargs):
            made.append(tar(*args, **kwargs))
            return made[-1]

        monkeypatch.setattr(shutil, "which", lambda name: name)
        monkeypatch.setattr(subprocess, "Popen", start)
        monkeypatch.setattr(ingest_mod, "_listed_size", listed or (lambda *a: 4 << 20))
        return made
    return use


def test_a_7z_unpack_says_how_far_against_what_it_holds(tmp_path, system_tar):
    from patcher.ingest import try_system_extract

    system_tar(slow_tar(3))
    heard = []
    out = try_system_extract(tmp_path / "Pack.7z", tmp_path / "out",
                             progress=lambda done, of: heard.append((done >> 20, of >> 20)))

    assert out == tmp_path / "out"
    assert heard == [(1, 4), (2, 4), (3, 4)]


def test_a_rar_isnt_listed_and_says_what_it_has_out_so_far(tmp_path, system_tar):
    """a solid .rar is only listed by unpacking it, which would be every unpack twice"""
    from patcher.ingest import try_system_extract

    def listed(*args):
        raise AssertionError("a .rar was listed")

    system_tar(slow_tar(2), listed)
    heard = []
    assert try_system_extract(tmp_path / "Pack.rar", tmp_path / "out",
                              progress=lambda done, of: heard.append((done >> 20, of)))
    assert heard == [(1, 0), (2, 0)]


def test_an_unpack_out_of_time_is_stopped_not_left_writing(tmp_path, system_tar):
    from patcher.ingest import try_system_extract

    made = system_tar(slow_tar(1, finishes=False))
    assert try_system_extract(tmp_path / "Pack.7z", tmp_path / "out", timeout=0.2,
                              progress=lambda done, of: None) is None
    assert made[0].killed and made[0].code == -9, "killed and never waited on"


def test_with_nobody_listening_an_unpack_still_waits_for_tar(tmp_path, system_tar):
    """what scan() and the add-on sieve ask for, with nothing to report to"""
    from patcher.ingest import try_system_extract

    made = system_tar(slow_tar(3))
    assert try_system_extract(tmp_path / "Pack.rar", tmp_path / "out") == tmp_path / "out"
    assert made[0].code == 0 and len(list((tmp_path / "out").iterdir())) == 3


def test_a_report_that_raises_doesnt_cost_the_unpack(tmp_path, system_tar):
    from patcher.ingest import try_system_extract

    def say(done, of):
        raise RuntimeError("the window's gone")

    system_tar(slow_tar(3))
    assert try_system_extract(tmp_path / "Pack.7z", tmp_path / "out", progress=say) \
        == tmp_path / "out"


@pytest.mark.parametrize("listing, size", [
    # bsdtar, which is Windows' tar.exe: the size is the fifth column
    ("drwxr-xr-x  0 0      0           0 Oct 10 13:39 My Mod/\n"
     "-rw-r--r--  0 0      0     3000000 Oct 10 13:39 My Mod/a file.bin\n"
     "-rw-r--r--  0 0      0       12345 Oct 10 13:39 My Mod/sub/b.dds\n", 3012345),
    # GNU tar: the third, after owner/group
    ("drwxr-xr-x root/root         0 2026-10-10 13:39 My Mod/\n"
     "-rw-r--r-- root/root   3000000 2026-10-10 13:39 My Mod/a file.bin\n", 3000000),
    # a device, which no mod holds: a listing that can't be read says nothing
    ("crw-r--r--  0 0      0      1,3 Oct 10 13:39 null\n", 0),
])
def test_a_listing_is_read_for_what_it_holds(monkeypatch, listing, size):
    import subprocess

    from patcher.ingest import _listed_size

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, listing.encode(), b""))
    assert _listed_size("tar", Path("Pack.7z"), 0) == size


def test_windows_own_tar_wins_over_whatever_path_has(tmp_path, monkeypatch):
    """
    Git for Windows' Unix tools, MSYS and Cygwin put GNU tar on PATH ahead of
    System32, and GNU tar can't open a .7z or a .rar
    """
    import shutil
    import sys

    from patcher.ingest import system_tar

    system32 = tmp_path / "Windows" / "System32"
    system32.mkdir(parents=True)
    (system32 / "tar.exe").write_bytes(b"MZ")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\Program Files\Git\usr\bin\tar.exe")
    assert system_tar() == str(system32 / "tar.exe")

    # no System32 copy, an old or stripped install: PATH is still worth trying
    (system32 / "tar.exe").unlink()
    assert system_tar() == r"C:\Program Files\Git\usr\bin\tar.exe"
