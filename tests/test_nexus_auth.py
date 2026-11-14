import json

import pytest

from patcher.mods import nexus_auth
from patcher.mods.nexus_auth import (
    ALREADY_SIGNED_IN,
    NOT_CONFIGURED,
    REJECTED,
    SIGNED_IN,
    TIMED_OUT,
    sign_in,
    sign_out,
    status,
)


class FakeSocket:
    """replays a scripted server side of the sso websocket"""

    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.closed = False

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self):
        if not self.messages:
            raise EOFError("server went away")
        return json.dumps(self.messages.pop(0))

    def close(self):
        self.closed = True


TOKEN_ACK = {"success": True, "data": {"connection_token": "tok-123"}}
KEY_MESSAGE = {"success": True, "data": {"api_key": "real-key-abc"}}


@pytest.fixture
def config(tmp_path):
    return tmp_path / "nexus_config.json"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("FFXIII_NEXUS_APP_ID", "ffxiii-patcher")


def test_without_an_app_id_it_says_so_instead_of_hanging(config):
    opened = []
    result = sign_in(config, open_browser=opened.append, connect=lambda url: FakeSocket([]))

    assert result.status == NOT_CONFIGURED
    assert not result.ok
    assert "issued by Nexus staff" in result.detail
    assert opened == [], "must not send the user to a login page that cannot work"


def test_full_sign_in_stores_the_key_and_opens_the_browser(config, configured):
    socket = FakeSocket([TOKEN_ACK, KEY_MESSAGE])
    opened, progress = [], []

    result = sign_in(config, open_browser=opened.append,
                     connect=lambda url: socket, on_progress=progress.append)

    assert result.status == SIGNED_IN
    assert result.ok
    # identify goes first, carrying the resolved app id
    assert socket.sent[0]["appid"] == "ffxiii-patcher"
    assert socket.sent[0]["protocol"] == 2
    # the browser opens only after the server accepts, and carries the same id
    assert len(opened) == 1
    assert socket.sent[0]["id"] in opened[0]
    assert "application=ffxiii-patcher" in opened[0]
    # and the key is persisted for the nxm handoff to use later
    assert json.loads(config.read_text(encoding="utf-8"))["nexus_api_key"] == "real-key-abc"
    assert socket.closed


def test_the_api_key_never_crosses_the_bridge(config, configured):
    socket = FakeSocket([TOKEN_ACK, KEY_MESSAGE])
    result = sign_in(config, open_browser=lambda u: None, connect=lambda url: socket)

    payload = result.as_dict()
    assert "real-key-abc" not in json.dumps(payload)
    assert set(payload) == {"ok", "status", "detail"}


def test_a_rejected_app_id_reports_the_reason_and_stops(config, configured):
    socket = FakeSocket([{"success": False, "error": "invalid application"}])
    opened = []

    result = sign_in(config, open_browser=opened.append, connect=lambda url: socket)

    assert result.status == REJECTED
    assert result.detail == "invalid application"
    assert opened == []
    assert not config.exists()


def test_timeout_is_reported_not_raised(config, configured):
    socket = FakeSocket([TOKEN_ACK])   # token arrives, key never does
    ticks = iter([0, 1, 2, 999])
    result = sign_in(config, open_browser=lambda u: None, connect=lambda url: socket,
                     timeout_seconds=5)

    assert result.status == TIMED_OUT
    assert "personal API key" in result.detail


def test_already_signed_in_short_circuits(config, configured):
    config.write_text(json.dumps({"nexus_api_key": "existing"}))
    connects = []

    result = sign_in(config, open_browser=lambda u: None,
                     connect=lambda url: connects.append(url))

    assert result.status == ALREADY_SIGNED_IN
    assert result.ok
    assert connects == [], "no reason to open a socket when a key is already stored"


def test_force_signs_in_again_over_an_existing_key(config, configured):
    config.write_text(json.dumps({"nexus_api_key": "old-key"}))
    socket = FakeSocket([TOKEN_ACK, KEY_MESSAGE])

    result = sign_in(config, open_browser=lambda u: None, connect=lambda url: socket, force=True)

    assert result.status == SIGNED_IN
    assert json.loads(config.read_text(encoding="utf-8"))["nexus_api_key"] == "real-key-abc"


def test_status_reports_both_halves(config, monkeypatch):
    monkeypatch.delenv("FFXIII_NEXUS_APP_ID", raising=False)
    assert status(config) == {"configured": False, "signedIn": False}

    monkeypatch.setenv("FFXIII_NEXUS_APP_ID", "app-1")
    config.write_text(json.dumps({"nexus_api_key": "k"}))
    assert status(config) == {"configured": True, "signedIn": True}


def test_sign_out_drops_the_key_but_keeps_the_app_id(config):
    config.write_text(json.dumps({"nexus_api_key": "k", "nexus_app_id": "app-1"}))

    sign_out(config)

    data = json.loads(config.read_text(encoding="utf-8"))
    assert "nexus_api_key" not in data
    assert data["nexus_app_id"] == "app-1", "the app id is config, not a credential"


def test_app_id_comes_from_the_config_file_when_no_env_var(config, monkeypatch):
    monkeypatch.delenv("FFXIII_NEXUS_APP_ID", raising=False)
    config.write_text(json.dumps({"nexus_app_id": "from-file"}))
    socket = FakeSocket([TOKEN_ACK, KEY_MESSAGE])
    opened = []

    result = sign_in(config, open_browser=opened.append, connect=lambda url: socket)

    assert result.status == SIGNED_IN
    assert socket.sent[0]["appid"] == "from-file"
    assert "application=from-file" in opened[0]


def test_connect_failure_is_a_timeout_not_a_crash(config, configured):
    def broken(url):
        raise OSError("network unreachable")

    result = sign_in(config, open_browser=lambda u: None, connect=broken)
    assert result.status == TIMED_OUT
    assert not result.ok
