"""
the whole "sign in to Nexus" flow as one call, so the CLI and the UI drive
identical behaviour instead of each reimplementing the sequence.

what actually happens: open a websocket, identify this app, get a connection
token back, send the user to their browser to click Authorize, then sit on the
same socket until the api key arrives. nexus_sso.py owns the wire format,
nxm_handler.py owns storing the key, this owns the order of operations and
what to report at each step.

everything external is injected (connect, open_browser, clock) so the flow is
testable end to end with no network and no browser, which is the only way to
test something whose middle step is "a human clicks a button".
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from patcher.mods.nexus_sso import (
    generate_connection_id,
    is_configured,
    resolve_app_id,
    sso_login_url,
    wait_for_api_key,
)
from patcher.mods.nxm_handler import load_api_key, save_api_key

NOT_CONFIGURED = "not_configured"
ALREADY_SIGNED_IN = "already_signed_in"
SIGNED_IN = "signed_in"
TIMED_OUT = "timed_out"
REJECTED = "rejected"
UNAVAILABLE = "unavailable"


@dataclass
class LoginOutcome:
    status: str
    api_key: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (SIGNED_IN, ALREADY_SIGNED_IN)

    def as_dict(self) -> dict:
        # never hand the key itself across the js bridge. the page has no use
        # for it and anything running in the page could read it.
        return {"ok": self.ok, "status": self.status, "detail": self.detail}


def default_connect(url: str):
    """
    the real websocket. imported lazily because websocket-client is an
    optional dependency: without it the flow degrades to manual key entry
    rather than failing to import.
    """
    import websocket
    return websocket.create_connection(url, timeout=35)


def is_signed_in(config_path: Path) -> bool:
    return bool(load_api_key(config_path))


def status(config_path: Path) -> dict:
    """what the UI needs to decide whether to offer a sign in at all"""
    return {
        "configured": is_configured(config_path),
        "signedIn": is_signed_in(config_path),
    }


def sign_in(
    config_path: Path,
    open_browser: Callable[[str], None],
    connect=default_connect,
    on_progress: Callable[[str], None] | None = None,
    timeout_seconds: float = 300,
    force: bool = False,
) -> LoginOutcome:
    """
    runs the login and stores the key on success.

    returns rather than raises for every failure mode, because the caller is
    either a UI thread or a CLI print loop and neither wants a traceback for
    "the user didn't click the button in time".
    """
    say = on_progress or (lambda message: None)

    if not force and is_signed_in(config_path):
        return LoginOutcome(ALREADY_SIGNED_IN, detail="already signed in to Nexus")

    app_id = resolve_app_id(config_path)
    if app_id is None:
        # the honest message. there is no code path that fixes this.
        return LoginOutcome(
            NOT_CONFIGURED,
            detail="one-click Nexus login needs an application id issued by Nexus staff, "
                   "which this build doesn't have yet. use a personal API key instead.",
        )

    # only the real connector needs the optional package. an injected connect
    # (tests, or any future transport) must not be gated on a dependency it
    # does not use.
    if connect is default_connect:
        try:
            import websocket  # noqa: F401
        except ImportError:
            return LoginOutcome(
                UNAVAILABLE,
                detail="one-click login needs the websocket-client package. "
                       "use a personal API key instead.",
            )

    connection_id = generate_connection_id()
    errors: list[str] = []

    say("opening your browser to sign in to Nexus")
    # the browser is opened BEFORE waiting, not after, because wait_for_api_key
    # blocks on recv until the user acts. open it afterwards and there is
    # nothing for them to act on.
    opened = False

    def on_token(token: str) -> None:
        # the server accepted us, so the login page will now resolve. opening
        # on the token rather than immediately means a rejected app id never
        # sends the user to a page that cannot work.
        nonlocal opened
        if opened:
            return
        opened = True
        open_browser(sso_login_url(connection_id, app_id))
        say("waiting for you to click Authorize in your browser")

    api_key = wait_for_api_key(
        connection_id,
        connect_fn=connect,
        app_id=app_id,
        timeout_seconds=timeout_seconds,
        on_token=on_token,
        on_error=errors.append,
    )

    if errors:
        return LoginOutcome(REJECTED, detail=errors[0])
    if api_key is None:
        return LoginOutcome(
            TIMED_OUT,
            detail="didn't hear back from Nexus in time. you can try again, "
                   "or use a personal API key.",
        )

    save_api_key(config_path, api_key)
    say("signed in to Nexus")
    return LoginOutcome(SIGNED_IN, api_key=api_key, detail="signed in to Nexus")


def sign_out(config_path: Path) -> dict:
    """
    forgets the stored key. keeps any other settings in the file, notably the
    app id, which is configuration rather than credentials.
    """
    import json

    if not config_path.exists():
        return {"ok": True, "status": "signed_out"}
    from patcher.mods.nxm_handler import read_config

    data = read_config(config_path)
    data.pop("nexus_api_key", None)
    config_path.write_text(json.dumps(data), encoding="utf-8")
    return {"ok": True, "status": "signed_out"}
