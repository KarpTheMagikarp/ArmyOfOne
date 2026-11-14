import json
from pathlib import Path

from patcher.updater import (
    check_for_update,
    download_and_install_manifest,
    is_newer,
    parse_version,
)


def test_parse_version_handles_v_prefix():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_stops_at_non_numeric_suffix():
    assert parse_version("v1.2.0-beta") == (1, 2, 0)


def test_is_newer_basic_comparisons():
    assert is_newer("v1.1.0", "v1.0.0") is True
    assert is_newer("v1.0.0", "v1.1.0") is False
    assert is_newer("v1.0.0", "v1.0.0") is False
    assert is_newer("v2.0.0", "v1.9.9") is True


def fake_fetch(response_json: dict):
    """returns a fetch() stand-in that always returns the given payload, no real network"""
    def fetch(url: str) -> bytes:
        return json.dumps(response_json).encode()
    return fetch


def test_check_for_update_reports_available_when_release_is_newer():
    fetch = fake_fetch({
        "tag_name": "v1.1.0",
        "body": "- fixed something\n- added something else",
        "assets": [
            {"name": "ffxiii.json", "browser_download_url": "https://example.com/ffxiii.json"},
        ],
    })

    info = check_for_update("v1.0.0", "https://api.example.com/releases/latest", "ffxiii.json", fetch=fetch)

    assert info.available is True
    assert info.latest_version == "v1.1.0"
    assert "fixed something" in info.release_notes
    assert info.download_url == "https://example.com/ffxiii.json"


def test_check_for_update_no_notification_on_fresh_matching_install():
    """
    this is the important one: a fresh install where the bundled
    manifest already matches the latest release should NOT report an
    update, same as a normal already-up-to-date check. no special
    "first run" flag needed, it just falls out of the version compare.
    """
    fetch = fake_fetch({"tag_name": "v1.0.0", "body": "", "assets": []})

    info = check_for_update("v1.0.0", "https://api.example.com/releases/latest", "ffxiii.json", fetch=fetch)

    assert info.available is False


def test_check_for_update_false_when_current_is_newer_than_latest():
    # shouldn't happen in practice, but a dev build ahead of the last
    # release shouldn't get told to "update" backwards
    fetch = fake_fetch({"tag_name": "v1.0.0", "body": "", "assets": []})

    info = check_for_update("v1.2.0", "https://api.example.com/releases/latest", "ffxiii.json", fetch=fetch)

    assert info.available is False


def test_check_for_update_fails_quiet_on_network_error():
    def broken_fetch(url: str) -> bytes:
        raise ConnectionError("no internet")

    info = check_for_update("v1.0.0", "https://api.example.com/releases/latest", "ffxiii.json", fetch=broken_fetch)

    assert info.available is False


def test_check_for_update_fails_quiet_on_malformed_response():
    def malformed_fetch(url: str) -> bytes:
        return b"not json at all"

    info = check_for_update("v1.0.0", "https://api.example.com/releases/latest", "ffxiii.json", fetch=malformed_fetch)

    assert info.available is False


def test_check_for_update_handles_release_with_no_matching_asset():
    fetch = fake_fetch({
        "tag_name": "v1.1.0",
        "body": "some notes",
        "assets": [{"name": "ffxiii2.json", "browser_download_url": "https://example.com/ffxiii2.json"}],
    })

    info = check_for_update("v1.0.0", "https://api.example.com/releases/latest", "ffxiii.json", fetch=fetch)

    assert info.available is True
    assert info.download_url is None  # no asset named ffxiii.json in this release


def test_download_and_install_manifest_writes_the_fetched_bytes(tmp_path: Path):
    fetch = fake_fetch({"game_id": "ffxiii", "manifest_version": "v1.1.0"})
    destination = tmp_path / "ffxiii.json"

    download_and_install_manifest("https://example.com/ffxiii.json", destination, fetch=fetch)

    assert json.loads(destination.read_text(encoding="utf-8"))["manifest_version"] == "v1.1.0"
