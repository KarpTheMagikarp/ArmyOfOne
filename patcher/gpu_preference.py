"""
Windows' own per-app graphics preference, set to the dedicated GPU for the
game's exe. These are 32-bit D3D9 games no driver profile knows, and a laptop
with two GPUs hands them the integrated one unless told otherwise, which is
most of the frame rate gone before any setting in the game matters. On a PC
with one GPU it does nothing.

It's the value Settings > System > Display > Graphics writes, one per exe under
the user's key, so it shows up there for anyone to change. Set only where the
exe has none: one there is somebody's choice, whatever it says. Revert takes
ours back off, and only while it's still ours.
"""

import json
import sys
from pathlib import Path

KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
HIGH = "GpuPreference=2;"
MARK = "gpu-preference.json"


def prefer(exe, store) -> bool:
    """the dedicated GPU for `exe`, unless it has a preference already. Whether it wrote"""
    if sys.platform != "win32":
        return False
    import winreg

    name = str(Path(exe))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            winreg.QueryValueEx(key, name)
        return False
    except FileNotFoundError:
        pass
    # the mark first, so a crash between leaves something Revert still finds
    marks = _marks(store)
    if name not in marks:
        marks.append(name)
        Path(store).mkdir(parents=True, exist_ok=True)
        (Path(store) / MARK).write_text(json.dumps({"exes": marks}), encoding="utf-8")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, HIGH)
    return True


def release(store) -> list:
    """ours taken back off, each only while it still says what we set. The exes it took"""
    marks = _marks(store)
    if not marks or sys.platform != "win32":
        return []
    import winreg

    gone = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            for name in marks:
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if value == HIGH:
                    winreg.DeleteValue(key, name)
                    gone.append(name)
    except FileNotFoundError:
        pass
    (Path(store) / MARK).unlink(missing_ok=True)
    return gone


def _marks(store) -> list:
    try:
        said = json.loads((Path(store) / MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(e) for e in said.get("exes", [])] if isinstance(said, dict) else []
