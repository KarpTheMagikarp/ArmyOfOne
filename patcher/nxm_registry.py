"""
Registering as the handler for nxm:// links.

Without this, pressing "Mod Manager Download" on Nexus does nothing useful:
Windows looks up the protocol, finds nothing (or finds Vortex), and the whole
download handoff never reaches us. Everything built on top of that flow is
inert until the key exists.

Registration goes under HKEY_CURRENT_USER, never HKEY_LOCAL_MACHINE. HKCU
needs no elevation, which matters because this app is a portable exe that
people run straight out of a folder, and a patcher that demands admin on first
launch is a patcher a lot of people close.

The handler is NOT claimed silently or permanently:

  * if something else already owns nxm (Vortex, Mod Organizer), we leave it
    alone unless explicitly told to take over. quietly stealing a protocol
    from the mod manager someone already uses is hostile, and they would have
    no idea why their downloads stopped working.
  * unregister() puts it back, and remembers whose it was.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

PROTOCOL = "nxm"
KEY = f"Software\\Classes\\{PROTOCOL}"
# the old name, like the data folder: it holds whoever had nxm before us
BACKUP_KEY = "Software\\FFXIIITrilogyPatcher\\nxm_backup"

# marks a command as ours so we can tell "already registered" from "someone
# else owns this", without string-matching an exe path that may have moved
MARKER = "--handle-nxm"


@dataclass
class Registration:
    registered: bool
    owner: str | None = None      # the command currently handling nxm
    ours: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        return {"registered": self.registered, "ours": self.ours,
                "owner": self.owner, "error": self.error}


def available() -> bool:
    return sys.platform == "win32"


def handler_command(executable: Path | None = None) -> str:
    """
    the command Windows runs for an nxm link.

    a frozen build points at the exe directly. a dev checkout has to go
    through the interpreter, or the registry ends up pointing at shell.py as
    though it were executable.
    """
    if getattr(sys, "frozen", False):
        target = executable or Path(sys.executable)
        return f'"{target}" {MARKER} "%1"'
    script = Path(__file__).resolve().parent.parent / "shell.py"
    return f'"{Path(sys.executable)}" "{script}" {MARKER} "%1"'


def current() -> Registration:
    """who owns nxm right now"""
    if not available():
        return Registration(registered=False, error="windows only")
    try:
        import winreg
    except ImportError:
        return Registration(registered=False, error="winreg unavailable")

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY + r"\shell\open\command") as key:
            command, _ = winreg.QueryValueEx(key, "")
    except FileNotFoundError:
        return Registration(registered=False)
    except OSError as e:
        return Registration(registered=False, error=str(e))

    return Registration(registered=True, owner=command, ours=MARKER in command)


def same_command(stored: str | None, wanted: str) -> bool:
    """
    whether the registry already points at this exe.

    Exact string equality is wrong: the value is a Windows path and those are
    case insensitive, so an exe launched as C:\\users\\cody\\... compares
    unequal to the C:\\Users\\Cody\\... the registry holds, and the handler
    gets rewritten on every single launch.

    Case folded unconditionally rather than through os.path.normcase, which is
    a no-op off Windows. This value is always a Windows registry command
    whatever host is asking about it, and a comparison that only works on the
    platform we cannot run tests on is not a comparison.
    """
    if not stored:
        return False
    return stored.strip().replace("/", "\\").casefold() == \
        wanted.strip().replace("/", "\\").casefold()


def register(force: bool = False, executable: Path | None = None) -> Registration:
    """
    claims nxm:// for this app.

    refuses when another tool already owns it unless force is set. someone
    with Vortex installed should have to say yes before their downloads start
    arriving somewhere else.
    """
    if not available():
        return Registration(registered=False, error="windows only")
    import winreg

    existing = current()
    if existing.registered and not existing.ours and not force:
        return Registration(registered=True, owner=existing.owner, ours=False,
                            error="another application already handles nxm links")

    command = handler_command(executable)
    if existing.ours and same_command(existing.owner, command):
        return Registration(registered=True, owner=command, ours=True)

    try:
        # remember what was there first, so unregister can hand it back rather
        # than leaving the user with no handler at all
        if existing.registered and not existing.ours:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, BACKUP_KEY) as backup:
                winreg.SetValueEx(backup, "previous", 0, winreg.REG_SZ, existing.owner)

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:NXM Protocol")
            # the empty URL Protocol value is what tells windows this key is a
            # protocol handler at all. without it the rest is ignored.
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY + r"\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    except OSError as e:
        return Registration(registered=False, error=str(e))

    return Registration(registered=True, owner=command, ours=True)


def unregister() -> Registration:
    """gives the protocol back to whoever had it, or removes it entirely"""
    if not available():
        return Registration(registered=False, error="windows only")
    import winreg

    existing = current()
    if not existing.registered or not existing.ours:
        # not ours to remove
        return existing

    previous = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BACKUP_KEY) as backup:
            previous, _ = winreg.QueryValueEx(backup, "previous")
    except (FileNotFoundError, OSError):
        pass

    try:
        if previous:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY + r"\shell\open\command") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, previous)
            return Registration(registered=True, owner=previous, ours=False)

        for sub in (KEY + r"\shell\open\command", KEY + r"\shell\open", KEY + r"\shell", KEY):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except FileNotFoundError:
                pass
    except OSError as e:
        return Registration(registered=False, error=str(e))
    return Registration(registered=False)


def ensure_registered(log=None) -> Registration:
    """
    called on startup. cheap, idempotent, and never fatal.

    a stale command is repaired automatically: people move a portable exe
    around, and a registry entry pointing at where it used to be is worse than
    none because the failure is silent.
    """
    if not available():
        return Registration(registered=False, error="windows only")

    state = current()
    if state.registered and not state.ours:
        if log:
            log.info("nxm is handled by another app, leaving it alone: %s", state.owner)
        return state

    wanted = handler_command()
    if state.ours and same_command(state.owner, wanted):
        return state

    result = register()
    if log:
        if result.ours:
            log.info("registered as the nxm handler: %s", wanted)
        elif result.error:
            log.warning("could not register the nxm handler: %s", result.error)
    return result
