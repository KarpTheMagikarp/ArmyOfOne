"""
One test per defect found in the cross-platform audit.

Every case here is a machine that is not the machine this was developed on: a
Cyrillic username, a Steam install the registry does not know about, a
libraryfolders.vdf Steam has not rewritten since 2019, a game folder the user
cannot write to, a download that stops halfway.

None of these were caught by the existing suite because the existing suite
runs on a clean English-locale sandbox with write access to everything, which
is exactly the environment that never reports a bug.
"""

import io
import json
import os
import stat
import struct
import sys
import warnings
import zipfile
from pathlib import Path

import pytest

from patcher import zipnames
from patcher.engine import PatchStatus

MANIFESTS = Path(__file__).resolve().parent.parent / "manifests"
from patcher.access import permission_message, probe_writable, write_failure
from patcher.addon_sieve import normalise_mod_name
from patcher.download import IncompleteDownload, stream_download
from patcher.engine import PatchStatus, backup_path_for, run_patches
from patcher.install_resolver import remember_path, remembered_path, resolve_install_dir
from patcher.manifest import GameManifest, Patch
from patcher.nxm_registry import same_command
from patcher.state import load_state
from patcher.steam_locator import library_paths_from_vdf
from tests.helpers import make_fake_pe


CYRILLIC = "Дмитрий"
CHINESE = "李伟"


def legacy_zip(names_bytes: list[bytes]) -> bytes:
    """
    a zip whose member names are raw bytes with the utf-8 flag CLEAR.

    Cannot be built with zipfile's writer: it sets the flag itself the moment
    a name is non-ASCII, which is the opposite of the bug being reproduced.
    Windows Explorer's own Send to > Compressed folder wrote exactly this for
    years.
    """
    out, central = io.BytesIO(), []
    for raw in names_bytes:
        offset = out.tell()
        body = b"x"
        crc = zipfile.crc32(body)
        out.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0,
                              crc, len(body), len(body), len(raw), 0))
        out.write(raw)
        out.write(body)
        central.append((raw, crc, len(body), offset))
    directory = out.tell()
    for raw, crc, size, offset in central:
        out.write(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0,
                              crc, size, size, len(raw), 0, 0, 0, 0, 0, offset))
        out.write(raw)
    end = out.tell()
    out.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central),
                          end - directory, directory, 0))
    return out.getvalue()


class TestNonAsciiPaths:
    def test_a_cyrillic_username_survives_the_state_round_trip(self, tmp_path):
        home = tmp_path / CYRILLIC / "AppData" / "Local"
        home.mkdir(parents=True)
        state = home / "ffxiii.json"
        install = tmp_path / CYRILLIC / "Игры" / "FF13"

        from patcher.state import record_applied
        record_applied(state, "install_novafix", ["d3d9.dll"],
                       {"journals": [str(install / ".patcher-undo" / "a.journal")]})

        loaded = load_state(state)
        assert loaded["applied_patches"]["install_novafix"]["files"] == ["d3d9.dll"]
        assert CYRILLIC in loaded["applied_patches"]["install_novafix"]["journals"][0]

    def test_a_config_written_in_utf8_is_not_read_as_the_ansi_codepage(self, tmp_path):
        """
        json.loads on str inherits the locale codec AND chokes on a BOM, which
        is what Notepad writes by default. Reading bytes fixes both.
        """
        config = tmp_path / "nexus_config.json"
        config.write_bytes("\ufeff{\"nexus_api_key\": \"ключ\"}".encode("utf-8"))

        from patcher.mods.nxm_handler import load_api_key
        assert load_api_key(config) == "ключ"

    def test_a_corrupt_config_degrades_instead_of_raising(self, tmp_path):
        """
        UnicodeDecodeError is a SIBLING of JSONDecodeError under ValueError,
        not a subclass, so `except (JSONDecodeError, OSError)` never caught it
        and a hand-edited file took the app down.
        """
        config = tmp_path / "nexus_config.json"
        config.write_bytes(b'{"nexus_api_key": "\xff\xfe not utf-8"}')

        from patcher.mods.nxm_handler import load_api_key
        assert load_api_key(config) is None

    def test_a_non_latin_mod_name_keeps_its_own_folder(self):
        """
        The old ASCII allowlist collapsed any fully non-Latin name to "mod", so
        a Russian and a Chinese mod shared one folder, overwrote each other's
        record, and removing either deleted both.
        """
        russian = normalise_mod_name("Лучшее освещение-217-1-2-0-1699999999.zip")
        chinese = normalise_mod_name("李伟的模组.zip")

        assert russian != "mod"
        assert chinese != "mod"
        assert russian != chinese
        assert "Лучшее" in russian and "李伟" in chinese

    def test_a_windows_device_name_does_not_become_an_unopenable_folder(self):
        assert normalise_mod_name("CON.zip") != "CON"
        assert normalise_mod_name("nul.zip").lower() != "nul"

    def test_trailing_dots_are_stripped_because_windows_drops_them_silently(self):
        """a folder created as "Mod." opens as "Mod", so every later lookup misses"""
        assert not normalise_mod_name("My Mod..zip").endswith(".")

    def test_a_mislabelled_zip_member_name_is_recovered(self):
        raw = legacy_zip([b"ascii.dll", "Лучшее.dll".encode("utf-8"),
                          "日本語.ini".encode("utf-8")])
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            repaired = [zipnames.repair(i) for i in zf.infolist()]

        assert repaired == ["ascii.dll", "Лучшее.dll", "日本語.ini"]

    def test_a_genuinely_cp437_name_is_left_alone(self):
        """
        the repair must not fire on a name that really was CP437, or it renames
        a member the caller can then no longer open.
        """
        raw = legacy_zip(["café.txt".encode("cp437")])
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            info = zf.infolist()[0]
            assert zipnames.repair(info) == info.filename

    def test_a_percent_encoded_url_yields_a_readable_filename(self):
        url = "https://cdn.example/%D0%9B%D1%83%D1%87%D1%88%D0%B5%D0%B5.zip?key=abc"
        assert zipnames.filename_from_url(url) == "Лучшее.zip"

    def test_a_zip_member_with_windows_illegal_characters_still_extracts(self, tmp_path):
        """
        macOS and Linux allow : ? * in filenames and NTFS does not. traversal
        was guarded; this half was not, so the extract died on an OSError
        naming a character rather than a problem.
        """
        target = zipnames.safe_target(tmp_path, "weird:name?.dll", flatten=False)
        assert target is not None
        assert not any(c in target.name for c in ':?*<>"|')

    def test_traversal_is_still_refused(self, tmp_path):
        assert zipnames.safe_target(tmp_path, "../../windows/system32/x.dll", flatten=False) is None


class TestSteamDetection:
    def test_the_legacy_flat_libraryfolders_format_is_read(self):
        """
        the pre-2021 format maps an index straight to a string under a
        capital-L root key. Read as the current nested format it returns
        nothing, which presents as every game being missing.
        """
        legacy = '''
        "LibraryFolders"
        {
            "TimeNextStatsReport"  "1"
            "ContentStatsID"       "2"
            "1"  "D:\\\\SteamLibrary"
            "2"  "\\\\\\\\nas\\\\games\\\\Lib"
        }
        '''
        assert library_paths_from_vdf(legacy) == ["D:\\SteamLibrary", "\\\\nas\\games\\Lib"]

    def test_the_current_nested_format_still_works(self):
        current = '''
        "libraryfolders"
        {
            "0" { "path" "C:\\\\Steam" "apps" { "292120" "1" } }
            "1" { "path" "D:\\\\SteamLibrary" }
        }
        '''
        assert library_paths_from_vdf(current) == ["C:\\Steam", "D:\\SteamLibrary"]

    def test_a_manually_picked_folder_is_remembered_across_runs(self, tmp_path, monkeypatch):
        """
        the reasons detection fails are all permanent, so without persistence
        the same person browses to the same folder every single launch.
        """
        import patcher.install_resolver as resolver
        monkeypatch.setattr(resolver, "persistent_data_root", lambda: tmp_path)

        install = tmp_path / "Игры" / "FF13"
        install.mkdir(parents=True)
        (install / "game.exe").write_bytes(make_fake_pe())

        manifest = GameManifest(game_id="ffxiii", display_name="FFXIII",
                                manifest_version="v1", executable_relative_path="game.exe",
                                patches=[])

        assert resolve_install_dir(manifest, steam_library_paths=[]).found is False

        remember_path("ffxiii", install)
        assert remembered_path("ffxiii") == install

        again = resolve_install_dir(manifest, steam_library_paths=[])
        assert again.found is True
        assert again.install_dir == install
        assert again.source == "manual"

    def test_an_override_pointing_at_a_folder_without_the_exe_is_not_used(self, tmp_path, monkeypatch):
        import patcher.install_resolver as resolver
        monkeypatch.setattr(resolver, "persistent_data_root", lambda: tmp_path)

        wrong = tmp_path / "SteamLibrary"
        wrong.mkdir()
        remember_path("ffxiii", wrong)

        manifest = GameManifest(game_id="ffxiii", display_name="FFXIII",
                                manifest_version="v1", executable_relative_path="game.exe",
                                patches=[])
        assert resolve_install_dir(manifest, steam_library_paths=[]).found is False

    def test_the_nxm_handler_is_not_rewritten_for_a_case_difference(self):
        """
        Windows paths are case insensitive, so exact string equality rewrote
        the registry on every single launch.
        """
        assert same_command(r'"C:\Users\Cody\app.exe" --handle-nxm "%1"',
                            r'"c:\users\cody\app.exe" --handle-nxm "%1"') is True
        assert same_command(r'"C:\Vortex\vortex.exe" -d "%1"',
                            r'"C:\Users\Cody\app.exe" --handle-nxm "%1"') is False


class TestPermissions:
    def test_probe_writable_reports_a_read_only_directory(self, tmp_path):
        if sys.platform == "win32":
            pytest.skip("a folder's read-only bit doesn't stop writes on Windows, ACLs do")
        if os.geteuid() == 0:
            pytest.skip("root ignores the mode bits this is testing")
        locked = tmp_path / "ProgramFiles"
        locked.mkdir()
        locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            assert probe_writable(locked) is not None
        finally:
            locked.chmod(0o755)

    def test_probe_writable_passes_on_a_normal_directory(self, tmp_path):
        assert probe_writable(tmp_path) is None

    def test_probe_writable_leaves_nothing_behind(self, tmp_path):
        """a pre-flight that litters runs on every Apply press"""
        probe_writable(tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_probe_writable_does_not_create_the_folder_it_is_asked_about(self, tmp_path):
        """
        an exist_ok mkdir here meant the pre-flight for a game folder that had
        been moved silently created an empty one, called it writable, and let
        the run proceed against nothing.
        """
        probe_writable(tmp_path / "moved" / "away")
        assert list(tmp_path.iterdir()) == []

    def test_the_permission_message_names_the_path(self, tmp_path):
        message = permission_message(tmp_path / "FF13" / "bin")
        assert str(tmp_path) in message
        assert "permission" in message.lower() or "blocked" in message.lower()

    def test_a_non_admin_is_told_to_elevate(self, tmp_path, monkeypatch):
        """the Windows wordings are the whole point of the module and Linux
        never reaches them, so they get exercised directly"""
        monkeypatch.setattr("patcher.access.is_elevated", lambda: False)
        assert "administrator" in permission_message(tmp_path).lower()

    def test_an_admin_is_not_told_to_run_as_administrator(self, tmp_path, monkeypatch):
        """
        telling someone who is already elevated to elevate reads as the app not
        knowing what it is doing, and the real cause is a running game or an
        antivirus lock.
        """
        monkeypatch.setattr("patcher.access.is_elevated", lambda: True)
        message = permission_message(tmp_path)
        assert "even as administrator" in message
        assert "right-click" not in message
        # Defender's ransomware protection says no to admins too, and its
        # toast never names the app that asked
        assert "Controlled folder access" in message

    def test_a_path_over_the_windows_limit_is_explained_as_such(self, monkeypatch):
        """
        windows reports a too-long path as ERROR_PATH_NOT_FOUND, which reads as
        "that folder does not exist" about a folder the user is looking at.
        """
        monkeypatch.setattr("patcher.access.sys.platform", "win32")
        long = "C:\\" + "\\".join(["averylongfoldername"] * 15) + "\\game.exe"
        error = OSError(2, "The system cannot find the path specified")
        error.winerror = 3
        assert "260" in write_failure(error, long)

    def test_a_genuinely_missing_folder_is_not_blamed_on_path_length(self, monkeypatch):
        monkeypatch.setattr("patcher.access.sys.platform", "win32")
        error = OSError(2, "The system cannot find the path specified")
        error.winerror = 3
        assert "260" not in write_failure(error, "C:\\Games\\FF13")

    def test_a_full_disk_is_reported_as_a_full_disk(self, tmp_path):
        """errno 28 told a user nothing about their drive being full"""
        message = write_failure(OSError(28, "No space left on device", str(tmp_path)), tmp_path)
        assert "full" in message

    def test_a_patch_that_raises_does_not_abandon_the_rest_of_the_run(self, tmp_path, monkeypatch):
        """
        run_patches had no per-patch guard at all, so one PermissionError out
        of a build guard killed every remaining patch AND left the UI
        summarising the run as a success.
        """
        install = tmp_path / "FF13"
        (install / "bin").mkdir(parents=True)
        (install / "bin" / "game.exe").write_bytes(make_fake_pe())

        first = Patch(id="explodes", label="Explodes", type="binary_patch",
                      default_enabled=True, operation="flip_laa_flag")
        second = Patch(id="fine", label="Fine", type="binary_patch",
                       default_enabled=True, operation="flip_laa_flag")
        manifest = GameManifest(game_id="ffxiii", display_name="FFXIII",
                                manifest_version="v1",
                                executable_relative_path="bin/game.exe",
                                patches=[first, second])

        calls = {"n": 0}
        from patcher.engine import apply_binary_patch as real

        def sometimes_denied(install_dir, mani, patch):
            calls["n"] += 1
            if patch.id == "explodes":
                raise PermissionError(13, "Access is denied", str(install_dir))
            return real(install_dir, mani, patch)

        monkeypatch.setattr("patcher.engine.apply_binary_patch", sometimes_denied)
        results = run_patches(install, manifest, {"explodes", "fine"})

        assert calls["n"] == 2, "the second patch never ran"
        by_id = {r.patch_id: r for r in results}
        assert by_id["explodes"].status == PatchStatus.FAILED
        assert by_id["fine"].status == PatchStatus.APPLIED
        assert "permission" in by_id["explodes"].detail.lower()


class TestDeepPaths:
    def test_a_backup_name_does_not_push_a_deep_path_past_the_limit(self):
        """
        ".backup" is seven characters against a 260 limit, which is enough on
        its own for a library under a redirected OneDrive folder.
        """
        deep = Path("/".join(["averylongfoldername"] * 12)) / "ffxiiiimg.exe"
        naive = deep.with_name(deep.name + ".backup")
        assert len(str(naive)) > 255
        assert len(str(backup_path_for(deep))) < len(str(naive))
        assert len(str(backup_path_for(deep))) < 260

    def test_two_deep_targets_do_not_share_one_backup(self):
        """
        the first shortening attempt truncated the stem, so ffxiiiimg.exe and
        ffxiiiimg_dx11.exe both became ffxi.backup. ensure_backup refuses to
        overwrite an existing backup, so the second exe silently reused the
        first one's and a revert restored the wrong bytes over it.
        """
        deep = Path("/".join(["averylongfoldername"] * 12))
        assert backup_path_for(deep / "ffxiiiimg.exe") != backup_path_for(deep / "ffxiiiimg_dx11.exe")

    def test_a_temp_sibling_is_never_the_target_itself(self):
        """
        write_atomic writes the temp then replaces onto the target, and deletes
        the temp if anything raises. If the two are the same path that delete
        takes out the real file.
        """
        from patcher.fileops import temp_sibling
        for parent_len in (10, 200, 248, 249, 250, 300):
            target = Path("/" + "x" * (parent_len - 1)) / "a.tmp"
            assert temp_sibling(target) != target, f"collided at parent={parent_len}"

    def test_a_normal_path_keeps_the_readable_backup_name(self, tmp_path):
        assert backup_path_for(tmp_path / "game.exe").name == "game.exe.backup"


class TestInterruptedDownloads:
    def test_a_body_that_stops_short_is_not_promoted_to_the_final_name(self, tmp_path):
        """
        a lengthless response over a dropped connection returns what arrived
        and raises nothing. the truncated file was then renamed into place and
        recorded as installed forever, so every later run reported it done and
        the game never worked.
        """
        class Truncated:
            status = 200
            headers = {"Content-Length": "1000"}

            def __init__(self):
                self._sent = False

            def read(self, size=-1):
                if self._sent:
                    return b""
                self._sent = True
                return b"x" * 400

            def close(self):
                pass

        dest = tmp_path / "pack.zip"
        with pytest.raises(IncompleteDownload):
            stream_download("https://x.test/pack.zip", dest,
                            opener=lambda url, headers: Truncated())

        assert not dest.exists(), "a partial download must not take the final name"
        assert dest.with_suffix(".zip.part").exists(), "the partial is kept so a retry resumes"

    def test_a_complete_body_is_promoted(self, tmp_path):
        class Whole:
            status = 200
            headers = {"Content-Length": "5"}

            def __init__(self):
                self._sent = False

            def read(self, size=-1):
                if self._sent:
                    return b""
                self._sent = True
                return b"hello"

            def close(self):
                pass

        dest = tmp_path / "pack.zip"
        stream_download("https://x.test/pack.zip", dest,
                        opener=lambda url, headers: Whole())
        assert dest.read_bytes() == b"hello"
        assert not dest.with_suffix(".zip.part").exists()


class TestTempStaging:
    def test_staging_lands_on_the_same_volume_as_the_destination(self, tmp_path):
        """
        %TEMP% is on C: for practically everyone and nothing this unpacks
        belongs to C:. A 4GB archive on D: staged onto a system drive with
        500MB free fills it and dies on errno 28.
        """
        from patcher.fileops import scratch_near
        assert scratch_near(tmp_path) == str(tmp_path.resolve())

    def test_scratch_near_walks_up_to_something_that_exists(self, tmp_path):
        from patcher.fileops import scratch_near
        assert scratch_near(tmp_path / "not" / "created" / "yet") == str(tmp_path.resolve())

    def test_an_unreadable_archive_leaves_no_staging_behind(self, tmp_path, monkeypatch):
        """
        scan() mkdtemp'd with no cleanup, and was called up to three times per
        install, so a dropped 4GB rar left 12GB in %TEMP% and then failed to
        install anyway.
        """
        import patcher.ingest as ingest_mod

        archive = tmp_path / "mod.rar"
        archive.write_bytes(b"not really a rar")
        monkeypatch.setattr(ingest_mod, "try_system_extract", lambda a, d: None)

        before = set(tmp_path.iterdir())
        with pytest.raises(ingest_mod.IngestError):
            ingest_mod.scan(archive, allow_system_extract=True)

        assert set(tmp_path.iterdir()) == before

    def test_a_successful_unpack_hands_its_staging_to_the_caller(self, tmp_path, monkeypatch):
        import patcher.ingest as ingest_mod

        archive = tmp_path / "mod.rar"
        archive.write_bytes(b"not really a rar")

        def fake_extract(source, destination):
            (destination / "veffs.jp.win32.imgb").write_bytes(b"payload")
            return destination

        monkeypatch.setattr(ingest_mod, "try_system_extract", fake_extract)
        found = ingest_mod.scan(archive, allow_system_extract=True)

        assert found.staging is not None and found.staging.is_dir()
        assert found.source == found.staging
        staged = found.staging
        found.cleanup()
        assert not staged.exists()
        assert found.staging is None


class TestBundleIntegrity:
    def test_the_bundle_check_catches_a_manifest_promising_a_missing_file(self, tmp_path):
        """
        the engine reports a missing bundled asset as a per-patch failure,
        which is correct and is exactly why no build ever failed over it.
        """
        import subprocess
        import sys

        (tmp_path / "manifests").mkdir()
        (tmp_path / "manifests" / "ffxiii.json").write_text(json.dumps({
            "game_id": "ffxiii", "display_name": "FFXIII", "manifest_version": "v1",
            "executable_relative_path": "game.exe",
            "patches": [{
                "id": "install_novafix", "label": "NovaFix", "type": "bundled_install",
                "default_enabled": True,
                "bundled_files": [{"source": "assets/mods/novafix/d3d9.dll",
                                   "target": "d3d9.dll"}],
            }],
        }), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, "tools/check_bundle.py", str(tmp_path)],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)

        assert result.returncode == 1
        assert "install_novafix" in result.stderr


class TestDownloadWatcher:
    def test_the_folder_watcher_result_is_actually_accepted(self, tmp_path):
        """
        poll() returns a Sighting and offer() does Path(found), so this raised
        TypeError into a blanket handler and the downloads watch silently
        never worked once.
        """
        from patcher.acquire import Acquisition
        from patcher.download_watch import Sighting

        landed = tmp_path / "mod.zip"
        landed.write_bytes(b"payload")
        sightings = [Sighting(path=landed, size=7, seen_at=0.0)]

        acquisition = Acquisition(job_id="lev", page_url="https://example.test/mod")
        result = acquisition.run(poll=lambda: sightings.pop() if sightings else None,
                                 interval=0)

        assert result == landed, "the Sighting never reached offer()"


class TestSecondPassDefects:
    """
    Found by a reviewer who had not seen the first hardening pass. Several of
    these were introduced BY that pass, which is the argument for having the
    review done by something that did not write the code.
    """

    def test_a_percent_encoded_separator_cannot_escape_the_staging_folder(self):
        """
        unquote is what puts a separator back into a string that had none. the
        download url is remote input, and `staging / filename` with an
        absolute right-hand side throws the base away entirely.
        """
        for url in ("https://x.test/%2F..%2F..%2Fevil.exe",
                    "https://x.test/a%2Fb.zip",
                    "https://x.test/%5C..%5Cevil.dll"):
            name = zipnames.filename_from_url(url)
            assert "/" not in name and "\\" not in name, url
            assert (Path("/staging") / name).parent == Path("/staging"), url

    def test_a_device_name_from_a_url_keeps_its_extension(self):
        """
        the suffix decides whether this is treated as an archive later, so the
        escape goes into the stem
        """
        assert zipnames.filename_from_url("https://x.test/con.zip").endswith(".zip")
        assert zipnames.filename_from_url("https://x.test/con.zip") != "con.zip"

    def test_an_empty_or_dot_member_is_refused(self, tmp_path):
        """
        these resolve to dest_dir itself and the caller then opens a directory
        for writing. ZipInfo.is_dir() does not catch it, it only looks for a
        trailing slash.
        """
        assert zipnames.safe_target(tmp_path, "", flatten=False) is None
        assert zipnames.safe_target(tmp_path, ".", flatten=False) is None
        assert zipnames.safe_target(tmp_path, "./", flatten=False) is None

    def test_an_ambiguous_member_name_is_refused_rather_than_guessed(self, tmp_path):
        """
        duplicate member names are legal in the zip spec. resolving one
        last-write-wins would hand the wrong member's bytes to an injection
        that writes them into white_img at the right offset: wrong payload,
        right place, no error anywhere.
        """
        import zipfile as zf_mod
        from patcher.ingest import IngestError, extract_member

        archive = tmp_path / "dupes.zip"
        with warnings.catch_warnings():
            # zipfile warns about the duplicate. that IS the fixture.
            warnings.simplefilter("ignore", UserWarning)
            with zf_mod.ZipFile(archive, "w") as zf:
                zf.writestr("payload.imgb", b"first")
                zf.writestr("payload.imgb", b"second")

        with pytest.raises(IngestError):
            extract_member(archive, "payload.imgb", tmp_path / "out.imgb")

    def test_an_unambiguous_member_still_extracts(self, tmp_path):
        import zipfile as zf_mod
        from patcher.ingest import extract_member

        archive = tmp_path / "fine.zip"
        with zf_mod.ZipFile(archive, "w") as zf:
            zf.writestr("payload.imgb", b"the bytes")

        out = extract_member(archive, "payload.imgb", tmp_path / "out.imgb")
        assert out.read_bytes() == b"the bytes"

    def test_a_malformed_vdf_costs_detection_not_the_app(self):
        """
        this runs on every game switch in the UI. a truncated libraryfolders.vdf
        must degrade to "pick the game folder", not take out the window.
        """
        for text in ('"libraryfolders" "D:\\\\Lib"', "garbage {{{ \"", "", '"libraryfolders" { "0" { "path"'):
            assert library_paths_from_vdf(text) == []

    def test_a_patch_raising_something_that_is_not_an_oserror_is_contained(self, tmp_path, monkeypatch):
        """
        the first pass guarded OSError only, which left the CLI aborting whole
        runs over a BadZipFile or a KeyError out of an ingest.
        """
        import zipfile as zf_mod

        install = tmp_path / "FF13"
        (install / "bin").mkdir(parents=True)
        (install / "bin" / "game.exe").write_bytes(make_fake_pe())

        first = Patch(id="badzip", label="Bad zip", type="binary_patch",
                      default_enabled=True, operation="flip_laa_flag")
        second = Patch(id="fine", label="Fine", type="binary_patch",
                       default_enabled=True, operation="flip_laa_flag")
        manifest = GameManifest(game_id="ffxiii", display_name="FFXIII",
                                manifest_version="v1",
                                executable_relative_path="bin/game.exe",
                                patches=[first, second])

        from patcher.engine import apply_binary_patch as real

        def sometimes_bad(install_dir, mani, patch):
            if patch.id == "badzip":
                raise zf_mod.BadZipFile("File is not a zip file")
            return real(install_dir, mani, patch)

        monkeypatch.setattr("patcher.engine.apply_binary_patch", sometimes_bad)
        results = run_patches(install, manifest, {"badzip", "fine"})

        by_id = {r.patch_id: r for r in results}
        assert by_id["badzip"].status == PatchStatus.FAILED
        assert by_id["fine"].status == PatchStatus.APPLIED

    def test_a_cancel_still_stops_the_whole_run(self, tmp_path, monkeypatch):
        """
        widening the guard to Exception must not swallow a cancel into a failed
        row and carry on with the next patch.
        """
        from patcher.download import Cancelled

        install = tmp_path / "FF13"
        (install / "bin").mkdir(parents=True)
        (install / "bin" / "game.exe").write_bytes(make_fake_pe())

        manifest = GameManifest(
            game_id="ffxiii", display_name="FFXIII", manifest_version="v1",
            executable_relative_path="bin/game.exe",
            patches=[Patch(id="stopped", label="Stopped", type="binary_patch",
                           default_enabled=True, operation="flip_laa_flag")])

        def cancelled(*args, **kwargs):
            raise Cancelled()

        monkeypatch.setattr("patcher.engine.apply_binary_patch", cancelled)
        with pytest.raises(Cancelled):
            run_patches(install, manifest, {"stopped"})

    def test_an_injection_from_a_dropped_archive_cleans_up_after_itself(self, tmp_path, monkeypatch):
        """
        engine.apply_archive_inject was the one scan() caller with no
        cleanup(), so a dropped rar left a full copy in the user's Downloads
        folder on every run.
        """
        import patcher.ingest as ingest_mod

        downloads = tmp_path / "Downloads"
        downloads.mkdir()
        archive = downloads / "mod.rar"
        archive.write_bytes(b"not really a rar")

        def fake_extract(source, destination):
            (destination / "veffs.jp.win32.imgb").write_bytes(b"payload")
            return destination

        monkeypatch.setattr(ingest_mod, "try_system_extract", fake_extract)

        install = tmp_path / "FF13"
        install.mkdir()
        manifest = GameManifest(game_id="ffxiii2", display_name="FFXIII-2",
                                manifest_version="v1",
                                executable_relative_path="game.exe", patches=[])
        patch = Patch(id="lev", label="Leviathan's Tears", type="archive_inject",
                      default_enabled=True,
                      injections=[{"source": "*/nope.imgb", "game_path": "a/b.imgb"}],
                      archive_variants=("u",), archive_relative_dir="alba_data")

        from patcher.engine import apply_archive_inject
        apply_archive_inject(install, manifest, patch, supplied_file=str(archive))

        leftovers = [p.name for p in downloads.iterdir() if p.name != "mod.rar"]
        assert leftovers == [], f"left behind in Downloads: {leftovers}"


class TestNovaFixFolderPriority:
    """
    NovaFix scans exactly one add-on folder: the first of addons, extra,
    extras, mods, dll that exists, creating addons when none do. That makes the
    folder NAME a property of the user's disk, not something a manifest can
    hardcode.
    """

    def test_an_existing_mods_folder_is_used_rather_than_a_new_addons_one(self, tmp_path):
        """
        creating addons/ next to a user's mods/ does not add a folder, it takes
        priority away from theirs and their existing add-ons stop loading
        """
        from patcher.novafix_config import resolve_addons_dir

        proxy = tmp_path / "bin"
        (proxy / "mods").mkdir(parents=True)
        assert resolve_addons_dir(proxy) == proxy / "mods"

    def test_addons_wins_when_both_exist(self, tmp_path):
        """matching their order, not ours"""
        from patcher.novafix_config import resolve_addons_dir

        proxy = tmp_path / "bin"
        (proxy / "addons").mkdir(parents=True)
        (proxy / "mods").mkdir()
        assert resolve_addons_dir(proxy) == proxy / "addons"

    def test_addons_is_the_answer_when_nothing_exists(self, tmp_path):
        from patcher.novafix_config import resolve_addons_dir

        proxy = tmp_path / "bin"
        proxy.mkdir()
        assert resolve_addons_dir(proxy).name == "addons"

    def test_the_redirect_addon_installs_where_novafix_is_reading(self, tmp_path):
        """
        the one that matters on XIII. the 4GB guard treats this add-on being on
        disk as proof the patch is safe, so installing it into a folder NovaFix
        never scans means a patched executable and Application load error
        3:0000065432.
        """
        from patcher.engine import install_target_dir

        install = tmp_path / "FF13"
        proxy = install / "white_data" / "prog" / "win" / "bin"
        (proxy / "mods").mkdir(parents=True)

        patch = Patch(
            id="install_exe_redirect", label="4GB compatibility", type="bundled_install",
            default_enabled=True,
            install_relative_path="white_data/prog/win/bin/addons/ff13-exe-redirect",
            bundled_files=[{"source": "x.asi", "target": "00_ff13_exe_redirect.asi"}])

        assert install_target_dir(install, patch) == proxy / "mods" / "ff13-exe-redirect"

    def test_a_path_with_no_addons_segment_is_left_alone(self, tmp_path):
        """the proxy itself goes beside the exe, nowhere near an add-on folder"""
        from patcher.engine import install_target_dir

        patch = Patch(id="install_novafix", label="NovaFix", type="bundled_install",
                      default_enabled=True,
                      install_relative_path="white_data/prog/win/bin",
                      bundled_files=[{"source": "x.dll", "target": "d3d9.dll"}])

        assert install_target_dir(tmp_path, patch) == tmp_path / "white_data/prog/win/bin"


def test_steams_own_folder_counts_as_a_library(tmp_path, monkeypatch):
    """
    The legacy flat libraryfolders.vdf lists only the EXTRA libraries. Reading
    the list as gospel meant a user with a second drive and the game in Steam's
    default folder got the second drive and nothing else, and was told the game
    could not be found with it sitting in the usual place.
    """
    from patcher import app as app_module
    from patcher.steam_locator import find_game_install

    root = tmp_path / "Steam"
    other = tmp_path / "D" / "SteamLibrary"
    for library in (root, other):
        (library / "steamapps" / "common").mkdir(parents=True)

    vdf = root / "steamapps" / "libraryfolders.vdf"
    vdf.write_text('"LibraryFolders"\n{\n\t"1"\t\t"%s"\n}\n'
                   % str(other).replace("\\", "\\\\"), encoding="utf-8")
    (root / "steamapps" / "appmanifest_292120.acf").write_text(
        '"AppState"\n{\n\t"installdir"\t\t"FINAL FANTASY XIII"\n}\n', encoding="utf-8")

    monkeypatch.setattr(app_module, "default_steam_library_vdf_path", lambda: vdf)
    monkeypatch.setattr(app_module, "steam_roots", lambda: [root])

    libraries = app_module.detect_steam_library_paths()

    assert other in libraries, "lost the library the vdf did list"
    assert root in libraries, "never looked in Steam's own folder"
    assert find_game_install(libraries, "292120") == \
        root / "steamapps" / "common" / "FINAL FANTASY XIII"


def test_the_steam_root_is_not_listed_twice(tmp_path, monkeypatch):
    """the modern vdf does include the root, and a duplicate is a wasted stat"""
    from patcher import app as app_module

    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    vdf = root / "steamapps" / "libraryfolders.vdf"
    vdf.write_text('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
                   % str(root).replace("\\", "\\\\"), encoding="utf-8")

    monkeypatch.setattr(app_module, "default_steam_library_vdf_path", lambda: vdf)
    monkeypatch.setattr(app_module, "steam_roots", lambda: [root])

    assert app_module.detect_steam_library_paths() == [root]


def test_a_stale_manifest_does_not_shadow_a_real_install(tmp_path):
    """
    Delete a game folder by hand to free space and Steam leaves the .acf
    behind. Returning the first library that claimed the app meant that dead
    manifest permanently shadowed a working install on another drive, reported
    as "couldn't find the game".
    """
    from patcher.install_resolver import resolve_install_dir
    from patcher.manifest import load_manifest

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    stale = tmp_path / "C" / "Steam"
    real = tmp_path / "D" / "SteamLibrary"
    for library in (stale, real):
        (library / "steamapps").mkdir(parents=True)
        (library / "steamapps" / "appmanifest_292120.acf").write_text(
            '"AppState"\n{\n\t"installdir"\t\t"FINAL FANTASY XIII"\n}\n', encoding="utf-8")

    # only the second one actually has the game in it
    exe = real / "steamapps" / "common" / "FINAL FANTASY XIII" / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")

    resolution = resolve_install_dir(manifest, steam_library_paths=[stale, real])

    assert resolution.found, resolution.error
    assert resolution.install_dir == exe.parent.parent.parent.parent.parent


def test_a_half_written_appmanifest_does_not_take_detection_down(tmp_path):
    """
    parse_vdf raises IndexError on a file that ends on a dangling key, which is
    what a power cut mid write leaves. Unguarded that came out of scan() as
    "list index out of range" printed where the install path goes, and took
    --doctor down with it, which is the one command whose job is this.
    """
    from patcher.steam_locator import find_game_install, installdir_from_acf

    whole = '"AppState"\n{\n\t"appid"\t\t"292120"\n\t"installdir"\t\t"FINAL FANTASY XIII"\n}\n'
    for cut in range(len(whole)):
        assert installdir_from_acf(whole[:cut]) in (None, "FINAL FANTASY XIII"), cut

    library = tmp_path / "Steam"
    (library / "steamapps").mkdir(parents=True)
    (library / "steamapps" / "appmanifest_292120.acf").write_text(
        '"AppState"\n{\n\t"installdir"', encoding="utf-8")

    assert find_game_install([library], "292120") is None


def test_an_installdir_cannot_escape_its_library(tmp_path):
    """
    pathlib throws the left side away when the right side is absolute, so an
    absolute installdir in a hand edited .acf would have pointed the whole tool
    at C:\\Windows. Two comparisons to refuse it.
    """
    from patcher.steam_locator import find_game_install, safe_installdir

    assert safe_installdir("FINAL FANTASY XIII") == "FINAL FANTASY XIII"
    for bad in ("C:\\Windows", "\\\\server\\share", "/etc", "..\\..\\Windows",
                "a/../../b", "", ".", ".."):
        assert safe_installdir(bad) is None, bad

    library = tmp_path / "Steam"
    (library / "steamapps").mkdir(parents=True)
    (library / "steamapps" / "appmanifest_292120.acf").write_text(
        '"AppState"\n{\n\t"installdir"\t\t"C:\\\\Windows"\n}\n', encoding="utf-8")

    assert find_game_install([library], "292120") is None


def _synthetic_patch(**overrides):
    """a Patch built from whatever fields the dataclass currently requires"""
    import inspect

    from patcher.manifest import Patch

    fields = Patch.__dataclass_fields__
    required = [n for n, f in fields.items()
                if f.default is inspect._empty and f.default_factory is inspect._empty]
    kwargs = {n: "" for n in required}
    kwargs.update(id="x", label="Test Mod", type="direct", default_enabled=True,
                  requires_backup=False, install_relative_path="",
                  download_url="https://example.test/mod.zip")
    kwargs.update(overrides)
    return Patch(**{k: v for k, v in kwargs.items() if k in fields})


class _NoPatches:
    patches = []
    game_id = "ffxiii"


def test_pressing_stop_is_not_reported_as_a_download_problem(tmp_path, monkeypatch):
    """
    Cancelled subclasses Exception, so the generic handler caught it and turned
    Stop into "we'll open the mod page and pick up the download automatically".
    That opens a browser at somebody who has just asked the tool to stop doing
    things, and it leaves the row looking like a network failure.
    """
    from patcher import engine
    from patcher.download import Cancelled

    def stopped(*a, **kw):
        raise Cancelled()

    monkeypatch.setattr(engine, "install_from_url", stopped)

    with pytest.raises(Cancelled):
        engine.apply_direct(tmp_path, _NoPatches(), _synthetic_patch(),
                            fetch=lambda url: b"")


def test_a_full_disk_says_so_instead_of_opening_a_browser(tmp_path, monkeypatch):
    """
    NotEnoughSpace already carries the drive and both numbers. Collapsing it
    into "we'll open the mod page" sends somebody to a website to solve a
    problem on their own hard disk.
    """
    from patcher import engine
    from patcher.download import NotEnoughSpace

    def full(*a, **kw):
        raise NotEnoughSpace(5_000_000_000, 1000, tmp_path)

    monkeypatch.setattr(engine, "install_from_url", full)

    result = engine.apply_direct(tmp_path, _NoPatches(), _synthetic_patch(),
                                 fetch=lambda url: b"")

    assert result.status == PatchStatus.FAILED
    assert "free on" in result.detail and "GB" in result.detail
    assert "mod page" not in result.detail


def test_a_truncated_download_says_so_too(tmp_path, monkeypatch):
    from patcher import engine
    from patcher.download import IncompleteDownload

    def cut_off(*a, **kw):
        raise IncompleteDownload(12_000_000, 40_000_000, "mod.zip")

    monkeypatch.setattr(engine, "install_from_url", cut_off)

    result = engine.apply_direct(tmp_path, _NoPatches(), _synthetic_patch(),
                                 fetch=lambda url: b"")

    assert result.status == PatchStatus.FAILED
    assert "mod.zip" in result.detail and "picks up where it left off" in result.detail


def test_a_fallback_page_does_not_swallow_a_real_error_either(tmp_path, monkeypatch):
    """
    the page fallback exists for a host that answers with a confirmation page,
    not for a disk with nothing left on it
    """
    from patcher import engine
    from patcher.download import NotEnoughSpace

    def full(*a, **kw):
        raise NotEnoughSpace(5_000_000_000, 1000, tmp_path)

    monkeypatch.setattr(engine, "install_from_url", full)
    patch = _synthetic_patch(fallback_page_url="https://example.test/page")

    result = engine.apply_direct(tmp_path, _NoPatches(), patch, fetch=lambda url: b"")

    assert result.status == PatchStatus.FAILED
    assert "free on" in result.detail


def test_a_mod_install_that_fails_partway_takes_itself_back(tmp_path, monkeypatch):
    """
    Two files, the second one denied. The old path left the first on disk with
    no .patcher-addon.json beside it, which the drawer then marks unmanaged and
    refuses to remove: a dead end that ends in Explorer.
    """
    from patcher import addon_sieve

    addons = tmp_path / "addons"
    addons.mkdir()
    source = tmp_path / "staging"
    source.mkdir()
    (source / "Mod.dll").write_bytes(b"MZ" + b"\0" * 200)
    (source / "Mod.ini").write_bytes(b"[settings]\n")

    result = addon_sieve.SiftResult(
        mod_name="Mod",
        payloads=[addon_sieve.Payload(source=source / "Mod.dll", name="Mod.dll", kind="code"),
                  addon_sieve.Payload(source=source / "Mod.ini", name="Mod.ini", kind="config")],
    )

    real = addon_sieve.write_atomic
    calls = []

    def second_one_fails(destination, data):
        calls.append(destination.name)
        if len(calls) > 1:
            raise PermissionError(5, "Access is denied")
        return real(destination, data)

    monkeypatch.setattr(addon_sieve, "write_atomic", second_one_fails)

    with pytest.raises(addon_sieve.SieveError) as caught:
        addon_sieve.install(result, addons)

    assert "Access is denied" not in str(caught.value), "raw errno reached the user"
    leftovers = list(addons.rglob("*"))
    assert leftovers == [], f"left a half installed mod behind: {leftovers}"


def test_updating_a_mod_never_leaves_a_truncated_dll(tmp_path, monkeypatch):
    """
    shutil.copyfile truncates its destination before it fills it, so a failure
    partway through an update left a zero length dll where a working one was,
    and NovaFix loads whatever is under that name.
    """
    from patcher import addon_sieve

    addons = tmp_path / "addons"
    installed = addons / "Mod"
    installed.mkdir(parents=True)
    good = b"MZ" + b"\xcc" * 4000
    (installed / "Mod.dll").write_bytes(good)
    (installed / addon_sieve.RECORD_NAME).write_text(
        json.dumps({"mod": "Mod", "files": {}}), encoding="utf-8")

    source = tmp_path / "staging"
    source.mkdir()
    (source / "Mod.dll").write_bytes(b"MZ" + b"\xdd" * 9000)
    result = addon_sieve.SiftResult(
        mod_name="Mod",
        payloads=[addon_sieve.Payload(source=source / "Mod.dll", name="Mod.dll", kind="code")],
    )

    monkeypatch.setattr(addon_sieve, "write_atomic",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError(28, "No space left")))

    with pytest.raises(addon_sieve.SieveError):
        addon_sieve.install(result, addons)

    assert (installed / "Mod.dll").read_bytes() == good, \
        "the working dll was replaced by a partial one"


def test_stopping_mid_extract_leaves_nothing_behind(tmp_path):
    """
    The one that ends in a game that will not start. Members were written
    straight to their real names, so Stop pressed partway through a proxy dll
    left a truncated d3d9.dll beside the game exe, with nothing in state to say
    it was there. The next launch loads whatever is under that name.
    """
    from patcher.download import Cancelled, stream_extract

    payload = b"MZ" + b"\xcc" * (3 * 1024 * 1024)
    archive = tmp_path / "mod.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("first.txt", b"small")
        zf.writestr("d3d9.dll", payload)

    dest = tmp_path / "game"

    class StopAfter:
        """Stop pressed once the second member is underway"""

        def __init__(self, after):
            self.left = after

        def is_set(self):
            self.left -= 1
            return self.left < 0

    with pytest.raises(Cancelled):
        stream_extract(archive, dest, cancel=StopAfter(5), flatten=True)

    leftovers = sorted(p.name for p in dest.rglob("*") if p.is_file())
    assert leftovers == [], f"left files in the game folder after Stop: {leftovers}"


def test_a_failed_member_does_not_replace_a_good_file(tmp_path, monkeypatch):
    """
    A second run over an existing install. The destination used to be opened
    with "wb", which truncates before it fills, so a failure partway through
    replaced a working dll with a prefix of the new one.
    """
    from patcher import download
    from patcher.download import stream_extract

    good = b"MZ" + b"\xaa" * 5000
    dest = tmp_path / "game"
    dest.mkdir()
    (dest / "d3d9.dll").write_bytes(good)

    archive = tmp_path / "mod.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("d3d9.dll", b"MZ" + b"\xbb" * 400_000)

    real_replace = Path.replace
    monkeypatch.setattr(Path, "replace",
                        lambda self, target: (_ for _ in ()).throw(OSError(28, "No space")))

    with pytest.raises(OSError):
        stream_extract(archive, dest, flatten=True)

    monkeypatch.setattr(Path, "replace", real_replace)
    assert (dest / "d3d9.dll").read_bytes() == good, "clobbered a working dll"
    strays = [p.name for p in dest.iterdir() if p.name != "d3d9.dll"]
    assert strays == [], f"left scratch files behind: {strays}"


def test_a_reserved_device_name_in_an_archive_cannot_swallow_a_file():
    """
    Windows resolves a reserved stem as a device in any directory and with any
    extension. A member called NUL.asi opened for writing succeeds and sends
    every byte nowhere: the extract reports success, the install record lists
    the file, and nothing is on disk. The two sibling helpers in this module
    already escaped these; the one on the archive member path did not.
    """
    from patcher.zipnames import filename_from_url, windows_safe

    for reserved in ("NUL.asi", "CON.dll", "aux", "PRN", "com1.txt", "lpt9", "nul"):
        assert windows_safe(reserved) != reserved, reserved
        assert windows_safe(reserved).split(".")[0].lower() not in {
            "con", "prn", "aux", "nul", "com1", "lpt9"}, reserved

    # ordinary names are left exactly alone
    for fine in ("Mod.dll", "readme.txt", "d3d9.dll", "console.ini", "auxiliary.dll"):
        assert windows_safe(fine) == fine, fine

    # and the helper that was already right stays right
    assert filename_from_url("https://x.test/NUL.zip") != "NUL.zip"


def test_a_novafix_ini_in_another_codepage_survives_a_settings_change(tmp_path):
    """
    write()'s own docstring promises everything it does not touch comes back
    byte for byte. errors="replace" broke that silently: every byte that was
    not valid UTF-8 became U+FFFD and got written back as EF BF BD, so a
    Russian NovaFix.ini came out of a Window Mode change with the user's own
    comments permanently replaced.
    """
    from patcher import novafix_config

    ini = tmp_path / "NovaFix.ini"
    comment = "; мой комментарий"
    ini.write_bytes(f"[Display]\r\n{comment}\r\nWindowMode=0\r\n".encode("cp1251"))
    before = ini.read_bytes()

    novafix_config.write(ini, {"Display": {"WindowMode": "1"}})
    after = ini.read_bytes()

    assert comment.encode("cp1251") in after, "mangled the user's own comment"
    assert b"\xef\xbf\xbd" not in after, "wrote replacement characters into their file"
    assert b"WindowMode=1" in after, "the change did not land"
    assert after.count(b"\r\n") == before.count(b"\r\n"), "changed the line endings"


def test_a_download_is_never_walked_down_to_plaintext():
    """
    CPython's redirect handler allows http, https and ftp as targets without
    caring which one it started on. What comes down that connection is a dll
    written next to the game executable and loaded into it, and no shipped
    manifest pins a checksum, so the bytes on the wire are the whole of the
    trust.
    """
    from urllib.error import URLError

    from patcher.download import _NoDowngrade

    class Req:
        def __init__(self, url):
            self.url = url

        def get_full_url(self):
            return self.url

        def __getattr__(self, name):
            return lambda *a, **kw: None

    handler = _NoDowngrade()
    with pytest.raises(URLError):
        handler.redirect_request(Req("https://a.test/mod.zip"), None, 302,
                                 "Found", {}, "http://b.test/mod.zip")
    with pytest.raises(URLError):
        handler.redirect_request(Req("https://a.test/mod.zip"), None, 302,
                                 "Found", {}, "ftp://b.test/mod.zip")


def test_the_streaming_opener_is_the_one_that_refuses_downgrades():
    """the guard is worth nothing if the production path does not use it"""
    import inspect

    from patcher import download

    source = inspect.getsource(download.default_opener)
    assert "_SAFE_OPENER" in source
    assert "urlopen(" not in source, "went round the opener that carries the guard"


def test_the_cli_says_once_that_the_folder_is_locked(tmp_path, monkeypatch):
    """
    The UI has had this pre-flight since the permissions pass; the CLI never
    did. Against a library under Program Files it produced fourteen rows each
    explaining the same permission problem in its own wording, and nothing
    anywhere said the folder needs administrator rights.
    """
    from patcher import app as app_module
    from patcher.manifest import load_manifest

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    install = tmp_path / "game"
    exe = install / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")

    monkeypatch.setattr("patcher.access.writable_or_reason",
                        lambda path: "no permission to write to that folder")

    outcome = app_module.run_full_automated_patch(
        manifest, tmp_path / "state.json", manual_path=install)

    assert not outcome.resolution.found
    assert "no permission" in outcome.resolution.error
    assert outcome.results is None, "ran patches against a folder it cannot write to"


def test_a_dry_run_still_works_on_a_locked_folder(tmp_path, monkeypatch):
    """refusing to SHOW somebody what would happen is the wrong answer"""
    from patcher import app as app_module
    from patcher.manifest import load_manifest

    manifest = load_manifest(MANIFESTS / "ffxiii.json")
    install = tmp_path / "game"
    exe = install / manifest.executable_relative_path
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")

    monkeypatch.setattr("patcher.access.writable_or_reason",
                        lambda path: "no permission to write to that folder")

    outcome = app_module.run_full_automated_patch(
        manifest, tmp_path / "state.json", manual_path=install, dry_run=True)

    assert outcome.resolution.found
    assert outcome.results is not None
