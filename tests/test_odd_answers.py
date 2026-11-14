"""
valid JSON that isn't the shape anybody expected. A hand-edited config, a
journal a power cut ended halfway, and the answers a hotel captive portal or a
school's filtering proxy hands back in place of GitHub's and Nexus's. Each was
an AttributeError or a KeyError out of Apply rather than a quiet "nothing here".
"""
import pytest

from patcher.archive.spanbackup import JournalUnreadable, RestoreError, load_journal
from patcher.mods import nexus_auth, nexus_sso, nxm_handler
from patcher.mods.file_install import latest_release_asset
from patcher.mods.nexus_api import NxmRequest, get_download_url
from patcher.updater import check_for_update

ODD = [b"null", b"[]", b"[1, 2]", b"3", b'"text"', b"true"]


@pytest.mark.parametrize("body", ODD + [b'{"nexus_api_key": 5, "nexus_app_id": []}'])
def test_a_nexus_config_of_the_wrong_shape_is_an_empty_one(tmp_path, body):
    config = tmp_path / "nexus_config.json"
    config.write_bytes(body)

    assert nxm_handler.load_api_key(config) is None
    assert nexus_sso.resolve_app_id(config) in (None, nexus_sso.resolve_app_id(None))
    assert nexus_auth.sign_out(config)["ok"]

    nxm_handler.save_api_key(config, "abc")
    assert nxm_handler.load_api_key(config) == "abc"


@pytest.mark.parametrize("body", ODD + [b'{"assets": 7}', b'{"assets": [3, null]}', b'{"tag_name": 2}'])
def test_an_update_check_that_gets_something_else_just_finds_nothing(body):
    info = check_for_update("v1.0.0", "https://example", "ffxiii.json", fetch=lambda url: body)
    assert not info.available


@pytest.mark.parametrize("body", ODD + [b'{"assets": 7}', b'{"assets": [3, null]}'])
def test_a_release_lookup_that_gets_something_else_says_there_is_no_file(body):
    url, why = latest_release_asset("someone/thing", fetch=lambda url: body)
    assert url is None and why


@pytest.mark.parametrize("body", ODD + [b'{"code": 403, "message": "nope"}', b"[3]"])
def test_nexus_links_of_the_wrong_shape_are_no_link(body):
    nxm = NxmRequest(game_domain="finalfantasyxiii", mod_id=1, file_id=2,
                     key="k", expires=1, user_id=3)
    assert get_download_url("key", nxm, fetch=lambda url, headers: body) is None


@pytest.mark.parametrize("body", [b"", b"{", b"null", b"[]", b'{"target": "x"}',
                                  b'{"target": "x", "target_size": 1, "created": 0, "spans": [3]}'])
def test_a_cut_journal_is_one_error_both_kinds_of_caller_catch(tmp_path, body):
    journal = tmp_path / "white_img.journal"
    journal.write_bytes(body)

    with pytest.raises(JournalUnreadable) as caught:
        load_journal(journal)
    # readers catch ValueError, restores catch RestoreError
    assert isinstance(caught.value, ValueError) and isinstance(caught.value, RestoreError)
