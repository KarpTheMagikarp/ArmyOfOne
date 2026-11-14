from pathlib import Path

from patcher.mods.nxm_handler import load_api_key, save_api_key


def test_save_and_load_api_key_roundtrip(tmp_path: Path):
    config_path = tmp_path / "nexus_config.json"

    save_api_key(config_path, "my-secret-key")

    assert load_api_key(config_path) == "my-secret-key"


def test_load_api_key_returns_none_when_file_missing(tmp_path: Path):
    assert load_api_key(tmp_path / "nexus_config.json") is None


def test_load_api_key_returns_none_on_corrupt_file(tmp_path: Path):
    config_path = tmp_path / "nexus_config.json"
    config_path.write_text("not valid json{{{")

    assert load_api_key(config_path) is None


def test_save_api_key_creates_parent_directories(tmp_path: Path):
    config_path = tmp_path / "nested" / "nexus_config.json"

    save_api_key(config_path, "key")

    assert config_path.exists()


def test_saving_a_key_does_not_delete_the_app_id(tmp_path):
    """
    The sign-in destroying the thing that made sign-in possible.

    nexus_config.json holds two unrelated things: the API key, which is a
    credential, and nexus_app_id, which is configuration nexus_sso reads to
    build the login url. save_api_key wrote a fresh single-key dict, so storing
    a key that one-click login had just produced deleted the id that produced
    it. sign_out had always merged, and says so in its docstring.
    """
    import json

    from patcher.mods.nxm_handler import load_api_key, save_api_key

    config = tmp_path / "nexus_config.json"
    config.write_text(json.dumps({"nexus_app_id": "abc-123"}), encoding="utf-8")

    save_api_key(config, "key-two")

    stored = json.loads(config.read_text(encoding="utf-8"))
    assert stored["nexus_app_id"] == "abc-123", "the app id was clobbered"
    assert load_api_key(config) == "key-two"


def test_saving_a_key_keeps_every_other_setting(tmp_path):
    """the app id is the one that mattered, but nothing here owns the file"""
    import json

    from patcher.mods.nxm_handler import save_api_key

    config = tmp_path / "nexus_config.json"
    config.write_text(json.dumps({"nexus_api_key": "old", "nexus_app_id": "abc",
                                  "something_else": 7}), encoding="utf-8")

    save_api_key(config, "new")

    assert json.loads(config.read_text(encoding="utf-8")) == {
        "nexus_api_key": "new", "nexus_app_id": "abc", "something_else": 7}


def test_a_config_we_cannot_parse_still_takes_the_key(tmp_path):
    """
    nothing to merge into is not a reason to refuse a credential the user has
    just authorised. covers a hand-edit, a truncated write and a file that is
    not utf-8 at all, which is the UnicodeDecodeError sibling load_api_key
    already guards against.
    """
    from patcher.mods.nxm_handler import load_api_key, save_api_key

    config = tmp_path / "nexus_config.json"
    for broken in (b"{ not json", b"\xff\xfe\x00bad", b"[1, 2, 3]"):
        config.write_bytes(broken)
        save_api_key(config, "key-four")
        assert load_api_key(config) == "key-four", broken
