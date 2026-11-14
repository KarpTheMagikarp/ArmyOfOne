"""
Nexus Mods' websocket SSO, the "Log in with Nexus Mods" flow Vortex uses: the
user clicks Authorize in their browser and the API key comes back over an open
socket. Wire format per Nexus's sso-integration-demo and node-nexus-api:

  - connect to wss://sso.nexusmods.com
  - send {"id": <uuid>, "token": <previous token or null>,
          "protocol": 2, "appid": <registered app id>}
  - the server answers with a connection_token for reconnects
  - the user opens nexusmods.com/sso?id=<uuid>&application=<app id>
  - the key arrives on the same connection

The blocker isn't code: only Nexus staff can issue an application id.
resolve_app_id() reads it from FFXIII_NEXUS_APP_ID, nexus_config.json or
NEXUS_APP_ID below, so when it arrives it's config, not a release. Callers
check is_configured(), and the manual API key path is the fallback.
Needs websocket-client, which PyInstaller bundles anyway.
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable, Protocol

NEXUS_SSO_WS_URL = "wss://sso.nexusmods.com"
NEXUS_SSO_LOGIN_URL = "https://www.nexusmods.com/sso"

PLACEHOLDER_APP_ID = "REPLACE_WITH_REGISTERED_NEXUS_APP_ID"

# drop a real id here once Nexus issues one, or set the env var, or put
# "nexus_app_id" in nexus_config.json. see resolve_app_id().
NEXUS_APP_ID = PLACEHOLDER_APP_ID

APP_ID_ENV_VAR = "FFXIII_NEXUS_APP_ID"


def resolve_app_id(config_path: Path | None = None) -> str | None:
    """
    the app id from whichever source has one, or None if nobody does.

    env var first so a developer can test against a real id without editing
    a tracked file and accidentally committing it. then the config file,
    which is where the shipped app stores it. the module constant last, for
    a build that wants it baked in.
    """
    from_env = os.environ.get(APP_ID_ENV_VAR, "").strip()
    if from_env and from_env != PLACEHOLDER_APP_ID:
        return from_env

    if config_path is not None and config_path.exists():
        from patcher.mods.nxm_handler import read_config

        stored = read_config(config_path).get("nexus_app_id", "")
        stored = stored if isinstance(stored, str) else ""
        if stored and stored.strip() != PLACEHOLDER_APP_ID:
            return stored.strip()

    if NEXUS_APP_ID and NEXUS_APP_ID != PLACEHOLDER_APP_ID:
        return NEXUS_APP_ID
    return None


def is_configured(config_path: Path | None = None) -> bool:
    """whether one-click login can even be attempted on this install"""
    return resolve_app_id(config_path) is not None


class WebSocketLike(Protocol):
    def send(self, data: str) -> None: ...
    def recv(self) -> str: ...
    def close(self) -> None: ...


def generate_connection_id() -> str:
    return str(uuid.uuid4())


def sso_login_url(connection_id: str, app_id: str = NEXUS_APP_ID) -> str:
    """the url to open in the user's browser: the whole 'click here, log in, authorize' step"""
    return f"{NEXUS_SSO_LOGIN_URL}?id={connection_id}&application={app_id}"


def build_identify_message(connection_id: str, app_id: str = NEXUS_APP_ID, reconnect_token: str | None = None) -> str:
    """the first message sent over the websocket, identifying this app and this login attempt"""
    return json.dumps({
        "id": connection_id,
        "token": reconnect_token,
        "protocol": 2,
        "appid": app_id,
    })


def extract_connection_token(message: dict) -> str | None:
    """
    the token the server hands back right after identify. reusing it on a
    reconnect resumes the SAME login attempt instead of starting a new one,
    which matters because the id is already baked into the URL sitting open
    in the user's browser. without it, a dropped connection means the page
    they are looking at is now dead and they have to start over.
    """
    data = message.get("data") or {}
    return data.get("connection_token")


def extract_error(message: dict) -> str | None:
    """the server's own reason for refusing, e.g. an unregistered app id"""
    if message.get("success") is False:
        return message.get("error") or "Nexus rejected the login request"
    return None


def extract_api_key(message: dict) -> str | None:
    """
    pulls an api key out of a parsed sso server message, if this
    particular message is the one carrying it (most messages along the
    way, like the initial connection_token, aren't).
    """
    data = message.get("data") or {}
    return data.get("api_key") or data.get("apikey")


def wait_for_api_key(
    connection_id: str,
    connect_fn: Callable[[str], WebSocketLike],
    app_id: str = NEXUS_APP_ID,
    timeout_seconds: float = 300,
    clock: Callable[[], float] = time.monotonic,
    reconnect_token: str | None = None,
    on_token: Callable[[str], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> str | None:
    """
    opens the sso websocket, identifies this app, and waits for the API key
    once the user authorizes in their browser. connect_fn and clock are
    injected so tests need no socket and no five minute wait. None on
    timeout, a malformed reply or any connection error, so a failed login
    reads as "didn't complete" instead of crashing setup.
    """
    try:
        ws = connect_fn(NEXUS_SSO_WS_URL)
    except Exception:
        return None

    try:
        ws.send(build_identify_message(connection_id, app_id, reconnect_token))

        start = clock()
        while clock() - start < timeout_seconds:
            raw = ws.recv()
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue

            problem = extract_error(data)
            if problem:
                # an unregistered app id lands here. reporting it beats
                # sitting on an open socket for five minutes waiting for a
                # key the server is never going to send.
                if on_error is not None:
                    on_error(problem)
                return None

            token = extract_connection_token(data)
            if token and on_token is not None:
                on_token(token)

            api_key = extract_api_key(data)
            if api_key:
                return api_key
            # anything else just means "not yet", keep waiting
        return None
    except Exception:
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass
