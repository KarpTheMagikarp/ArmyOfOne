"""
How to start the game: through Steam, for anything Steam sells.

Running ffxiiiimg.exe ourselves (PCGamingWiki's bypass) gets Application load
error 3:0000065432. Setting SteamAppId/SteamGameId didn't help, and neither did
the CEG-free replacement exe: it's still a Steam app that expects Steam to have
started it.

Square Enix's launcher gets skipped by its own "Do not show this window again"
value in setup.xml, which patcher.launcher_skip writes. shows_launcher says
which games have one. Direct launch is only for builds Steam can't serve (no
appid).
"""

from dataclasses import dataclass
from pathlib import Path

STEAM = "steam"
DIRECT = "direct"


@dataclass(frozen=True)
class Plan:
    route: str
    app_id: str | None = None
    executable: Path | None = None
    why: str = ""


def plan(app_id: str | None, executable: Path | None, windows: bool) -> Plan:
    """
    which route to take. An appid means Steam, no second condition. "No CEG
    wrapper" has been read as "can launch direct" twice, and both times a real
    install got Application load error 3:0000065432. Pure and tested, since
    these cases can't be reproduced off Windows.
    """
    if not windows or not app_id:
        return Plan(DIRECT, app_id, executable, "nothing for steam to launch")
    return Plan(STEAM, app_id, executable, "steam app, steam has to start it")


def shows_launcher(app_id: str | None) -> bool:
    """
    whether Steam's launch for this app puts Square Enix's window up first.

    XIII and XIII-2 do. Lightning Returns starts straight into the game, so
    there is nothing to silence and no setup.xml worth touching for it.
    """
    return str(app_id or "") in ("292120", "292140")
