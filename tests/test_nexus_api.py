import json

import pytest

from patcher.mods.nexus_api import NxmRequest, get_download_url, parse_nxm_url


def test_parse_nxm_url_extracts_all_parts():
    url = "nxm://finalfantasy13/mods/17/files/123?key=abc123&expires=1234567890&user_id=999"

    nxm = parse_nxm_url(url)

    assert nxm.game_domain == "finalfantasy13"
    assert nxm.mod_id == "17"
    assert nxm.file_id == "123"
    assert nxm.key == "abc123"
    assert nxm.expires == "1234567890"
    assert nxm.user_id == "999"


def test_parse_nxm_url_works_without_query_string():
    # premium users' nxm links sometimes have no key/expires at all
    url = "nxm://finalfantasy13/mods/17/files/123"

    nxm = parse_nxm_url(url)

    assert nxm.game_domain == "finalfantasy13"
    assert nxm.mod_id == "17"
    assert nxm.file_id == "123"
    assert nxm.key is None
    assert nxm.expires is None
    assert nxm.user_id is None


def test_parse_nxm_url_rejects_non_nxm_scheme():
    with pytest.raises(ValueError):
        parse_nxm_url("https://example.com/mods/17/files/123")


def test_parse_nxm_url_rejects_malformed_path():
    with pytest.raises(ValueError):
        parse_nxm_url("nxm://finalfantasy13/something/weird")


def fake_fetch(expected_url_fragment: str, response_json):
    def fetch(url: str, headers: dict) -> bytes:
        assert expected_url_fragment in url
        assert "apikey" in headers
        return json.dumps(response_json).encode()
    return fetch


def test_get_download_url_returns_the_first_link():
    nxm = NxmRequest(
        game_domain="finalfantasy13", mod_id="17", file_id="123",
        key="abc", expires="999", user_id="1",
    )
    fetch = fake_fetch(
        "games/finalfantasy13/mods/17/files/123/download_link.json",
        [{"name": "Nexus CDN", "URI": "https://cdn.example.com/file.zip"}],
    )

    url = get_download_url("my-api-key", nxm, fetch=fetch)

    assert url == "https://cdn.example.com/file.zip"


def test_get_download_url_includes_key_expires_user_id_in_request():
    nxm = NxmRequest(
        game_domain="finalfantasy13", mod_id="17", file_id="123",
        key="thekey", expires="123", user_id="456",
    )
    captured = {}

    def fetch(url: str, headers: dict) -> bytes:
        captured["url"] = url
        return json.dumps([{"URI": "https://example.com/file.zip"}]).encode()

    get_download_url("my-api-key", nxm, fetch=fetch)

    assert "key=thekey" in captured["url"]
    assert "expires=123" in captured["url"]
    assert "user_id=456" in captured["url"]


def test_get_download_url_sends_the_api_key_as_header_not_in_url():
    nxm = NxmRequest(game_domain="g", mod_id="1", file_id="2", key=None, expires=None, user_id=None)
    captured = {}

    def fetch(url: str, headers: dict) -> bytes:
        captured["headers"] = headers
        return json.dumps([{"URI": "https://example.com/file.zip"}]).encode()

    get_download_url("secret-key-123", nxm, fetch=fetch)

    assert captured["headers"]["apikey"] == "secret-key-123"
    assert "secret-key-123" not in captured.get("url", "")


def test_get_download_url_returns_none_on_network_error():
    def broken_fetch(url: str, headers: dict) -> bytes:
        raise ConnectionError("no internet")

    nxm = NxmRequest(game_domain="g", mod_id="1", file_id="2", key=None, expires=None, user_id=None)

    assert get_download_url("key", nxm, fetch=broken_fetch) is None


def test_get_download_url_returns_none_on_empty_response():
    fetch = fake_fetch("download_link.json", [])
    nxm = NxmRequest(game_domain="finalfantasy13", mod_id="17", file_id="123", key="a", expires="1", user_id="1")

    assert get_download_url("key", nxm, fetch=fetch) is None


def test_get_download_url_returns_none_on_malformed_json():
    def bad_fetch(url: str, headers: dict) -> bytes:
        return b"not json"

    nxm = NxmRequest(game_domain="g", mod_id="1", file_id="2", key=None, expires=None, user_id=None)

    assert get_download_url("key", nxm, fetch=bad_fetch) is None


def test_an_nxm_url_cannot_steer_the_api_call():
    """the url arrives through a protocol handler anything on the machine can call"""
    import pytest
    from patcher.mods.nexus_api import get_download_url, parse_nxm_url

    for bad in ("nxm://finalfantasy13/mods/17/files/..%2F..%2Fusers",
                "nxm://final..fantasy/mods/17/files/1",
                "nxm://finalfantasy13/mods/17x/files/1"):
        with pytest.raises(ValueError):
            parse_nxm_url(bad)

    nxm = parse_nxm_url("nxm://finalfantasy13/mods/17/files/1?key=a%26b%3Dc&expires=1&user_id=2")
    seen = []
    get_download_url("apikey", nxm, fetch=lambda url, headers: seen.append(url) or b"[]")
    assert "key=a%26b%3Dc&expires=1&user_id=2" in seen[0], seen
