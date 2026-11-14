import json

from patcher.mods.nexus_sso import (
    build_identify_message,
    extract_api_key,
    generate_connection_id,
    sso_login_url,
    wait_for_api_key,
)


def test_generate_connection_id_looks_like_a_uuid():
    conn_id = generate_connection_id()
    assert len(conn_id) == 36
    assert conn_id.count("-") == 4


def test_generate_connection_id_is_different_each_time():
    assert generate_connection_id() != generate_connection_id()


def test_sso_login_url_includes_id_and_application():
    url = sso_login_url("abc-123", app_id="my-app")

    assert "id=abc-123" in url
    assert "application=my-app" in url
    assert url.startswith("https://www.nexusmods.com/sso?")


def test_build_identify_message_shape():
    message = json.loads(build_identify_message("abc-123", app_id="my-app"))

    assert message == {"id": "abc-123", "token": None, "protocol": 2, "appid": "my-app"}


def test_build_identify_message_includes_reconnect_token_when_given():
    message = json.loads(build_identify_message("abc-123", app_id="my-app", reconnect_token="prev-token"))

    assert message["token"] == "prev-token"


def test_extract_api_key_finds_it_when_present():
    message = {"success": True, "data": {"api_key": "the-real-key"}, "error": None}
    assert extract_api_key(message) == "the-real-key"


def test_extract_api_key_none_for_connection_token_ack():
    # this is what the server sends first, before the user has authorized anything
    message = {"success": True, "data": {"connection_token": "some-token"}, "error": None}
    assert extract_api_key(message) is None


def test_extract_api_key_none_for_empty_data():
    assert extract_api_key({"success": True, "data": None, "error": None}) is None


class FakeWebSocket:
    """
    a minimal stand-in for a real websocket connection. queue up the
    messages it should "receive" in order, and it plays them back one
    at a time - the last one can be the real api key message, or the
    queue can just run dry to simulate a timeout/nothing-ever-arrives.
    """

    def __init__(self, messages: list[str]):
        self.messages = list(messages)
        self.sent = []
        self.closed = False

    def send(self, data: str) -> None:
        self.sent.append(data)

    def recv(self) -> str:
        if not self.messages:
            raise ConnectionError("no more fake messages queued")
        return self.messages.pop(0)

    def close(self) -> None:
        self.closed = True


def test_wait_for_api_key_returns_the_key_once_it_arrives():
    fake_ws = FakeWebSocket([
        json.dumps({"success": True, "data": {"connection_token": "tok"}, "error": None}),
        json.dumps({"success": True, "data": {"api_key": "the-real-key"}, "error": None}),
    ])

    result = wait_for_api_key("conn-id", connect_fn=lambda url: fake_ws)

    assert result == "the-real-key"
    assert fake_ws.closed is True


def test_wait_for_api_key_sends_the_identify_message_first():
    fake_ws = FakeWebSocket([json.dumps({"data": {"api_key": "key"}})])

    wait_for_api_key("conn-id", connect_fn=lambda url: fake_ws, app_id="my-app")

    sent_message = json.loads(fake_ws.sent[0])
    assert sent_message["id"] == "conn-id"
    assert sent_message["appid"] == "my-app"


def test_wait_for_api_key_connects_to_the_real_sso_url():
    fake_ws = FakeWebSocket([json.dumps({"data": {"api_key": "key"}})])
    captured_url = {}

    def connect(url):
        captured_url["url"] = url
        return fake_ws

    wait_for_api_key("conn-id", connect_fn=connect)

    assert captured_url["url"] == "wss://sso.nexusmods.com"


def test_wait_for_api_key_returns_none_on_connect_failure():
    def broken_connect(url):
        raise ConnectionError("could not reach nexus")

    assert wait_for_api_key("conn-id", connect_fn=broken_connect) is None


def test_wait_for_api_key_ignores_malformed_messages_and_keeps_waiting():
    fake_ws = FakeWebSocket([
        "not valid json",
        json.dumps({"data": {"api_key": "the-real-key"}}),
    ])

    result = wait_for_api_key("conn-id", connect_fn=lambda url: fake_ws)

    assert result == "the-real-key"


def test_wait_for_api_key_times_out_if_nothing_ever_arrives():
    fake_ws = FakeWebSocket([
        json.dumps({"data": {"connection_token": "tok"}}),
    ])
    # a fake clock that ticks well past the timeout after the first read,
    # so this test doesn't need to actually wait through a real timeout
    fake_time = [0.0]

    def fake_clock():
        current = fake_time[0]
        fake_time[0] += 1000  # jump straight past any timeout after being read once
        return current

    result = wait_for_api_key("conn-id", connect_fn=lambda url: fake_ws, timeout_seconds=5, clock=fake_clock)

    assert result is None
    assert fake_ws.closed is True
