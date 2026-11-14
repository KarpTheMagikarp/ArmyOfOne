"""
What the PC has to draw with, for the Settings drawer's Auto graphics preset:
the biggest GPU's dedicated memory and the desktop's size, the two things that
decide what these games can hold at speed. Read without running anything:
the display driver writes its memory size into its class key, and Windows
knows the desktop's. Anything unreadable comes back None, and Auto picks the
middle for a PC it can't see.
"""

import sys

# every display adapter's driver key lives under this class
DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def video_memory_gb() -> float | None:
    """the largest adapter's dedicated memory in GB, or None"""
    if sys.platform != "win32":
        return None
    import winreg

    best = 0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS) as root:
            for n in range(256):
                try:
                    name = winreg.EnumKey(root, n)
                except OSError:
                    break
                if not name.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, name) as adapter:
                        size, _ = winreg.QueryValueEx(adapter, "HardwareInformation.qwMemorySize")
                except OSError:
                    continue
                # a QWORD, or bytes from an older driver that wrote it raw
                if isinstance(size, bytes):
                    size = int.from_bytes(size[:8], "little")
                best = max(best, int(size or 0))
    except OSError:
        return None
    return best / (1 << 30) if best else None


def desktop_width() -> int | None:
    """the primary display's real width in pixels, whatever its scaling, or None"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        dc = user32.GetDC(0)
        try:
            # DESKTOPHORZRES: the mode's own width, which GetSystemMetrics
            # shrinks by the scaling for a process that isn't DPI aware
            return int(gdi32.GetDeviceCaps(dc, 118)) or None
        finally:
            user32.ReleaseDC(0, dc)
    except (OSError, AttributeError):
        return None


def tier(memory_gb: float | None, width: int | None) -> str:
    """
    Low, Medium or High for a PC. These games want little next to a modern
    card, so the line is memory: under 2GB is an old card or a laptop's
    integrated chip, 4GB holds the game with its HD textures, and 4K's
    framebuffers and shadow maps take one tier off anything under 8GB
    """
    if memory_gb is None:
        pick = 1
    else:
        pick = 0 if memory_gb < 2 else 1 if memory_gb < 4 else 2
        if width and width >= 3840 and memory_gb < 8:
            pick = max(pick - 1, 0)
    return ("Low", "Medium", "High")[pick]


def auto_tier() -> str:
    return tier(video_memory_gb(), desktop_width())
