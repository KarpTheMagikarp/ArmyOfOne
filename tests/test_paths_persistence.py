"""
Where durable data lives.

State used to sit beside the exe, so every fresh download or re-extract started
with no memory of what the app had installed. That is not cosmetic: the FF13Fix
purge could not find its own records to clean up, and a prerequisite sitting
plainly on disk still read as missing.
"""

import importlib
import json
from pathlib import Path

import pytest

import patcher.paths as paths


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    # each platform reads its own variable, so point both at the same place
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "userdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "userdata"))
    importlib.reload(paths)
    yield paths
    importlib.reload(paths)


def test_data_lives_per_user_not_beside_the_app(fresh, tmp_path):
    root = fresh.persistent_data_root()

    assert root == tmp_path / "userdata" / fresh.APP_DIR_NAME
    assert fresh._beside_app() not in root.parents
    assert root != fresh._beside_app()


def test_state_and_log_follow_it(fresh):
    root = fresh.persistent_data_root()
    assert fresh.state_path_for("ffxiii2").parent.parent == root
    assert fresh.log_path().parent == root


def test_a_portable_marker_keeps_everything_beside_the_app(fresh, monkeypatch, tmp_path):
    """someone running this off a USB stick wants it self-contained"""
    beside = tmp_path / "app"
    beside.mkdir()
    (beside / "portable.txt").write_text("keep my data here")
    monkeypatch.setattr(fresh, "_beside_app", lambda: beside)

    assert fresh.persistent_data_root() == beside


def test_records_from_an_older_build_are_brought_across(fresh, monkeypatch, tmp_path):
    old = tmp_path / "old-install"
    (old / ".state").mkdir(parents=True)
    (old / ".state" / "ffxiii2.json").write_text(
        json.dumps({"applied_patches": {"install_novafix": {"applied_at": "x"}}}))
    (old / "nexus_config.json").write_text("{}")
    monkeypatch.setattr(fresh, "_beside_app", lambda: old)
    fresh._migrated = False

    brought = fresh.migrate_legacy_data()

    assert sorted(brought) == [".state", "nexus_config.json"]
    carried = json.loads((fresh.persistent_data_root() / ".state" / "ffxiii2.json").read_text(encoding="utf-8"))
    assert "install_novafix" in carried["applied_patches"]
    # copied, not moved, so an older build still works if they go back to it
    assert (old / ".state" / "ffxiii2.json").is_file()


def test_migration_never_overwrites_what_is_already_there(fresh, monkeypatch, tmp_path):
    old = tmp_path / "old-install"
    (old / ".state").mkdir(parents=True)
    (old / ".state" / "ffxiii2.json").write_text('{"applied_patches": {"stale": {}}}')
    monkeypatch.setattr(fresh, "_beside_app", lambda: old)

    current = fresh.persistent_data_root() / ".state"
    current.mkdir(parents=True)
    (current / "ffxiii2.json").write_text('{"applied_patches": {"current": {}}}')
    fresh._migrated = False

    fresh.migrate_legacy_data()

    kept = json.loads((current / "ffxiii2.json").read_text(encoding="utf-8"))
    assert "current" in kept["applied_patches"]


def test_migration_runs_once(fresh, monkeypatch, tmp_path):
    old = tmp_path / "old-install"
    (old / ".state").mkdir(parents=True)
    monkeypatch.setattr(fresh, "_beside_app", lambda: old)
    fresh._migrated = False

    assert fresh.migrate_legacy_data() is not None
    assert fresh.migrate_legacy_data() == []


def test_an_unwritable_old_location_is_not_fatal(fresh, monkeypatch, tmp_path):
    """a read-only Downloads folder costs a forgotten record, not a crash"""
    old = tmp_path / "old-install"
    (old / ".state").mkdir(parents=True)
    monkeypatch.setattr(fresh, "_beside_app", lambda: old)

    def explode(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(fresh.shutil, "copytree", explode)
    fresh._migrated = False

    assert fresh.migrate_legacy_data() == []


def test_both_entry_points_migrate_before_reading_state():
    root = Path(__file__).resolve().parent.parent
    for name in ("main.py", "shell.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "migrate_legacy_data()" in source, f"{name} starts amnesiac"
