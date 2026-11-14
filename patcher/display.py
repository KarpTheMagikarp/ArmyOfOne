"""
Window shape: an ordinary resizable window that stays 16:9 while dragged.
Maximise and fullscreen take the monitor's shape and the page fills it.

The UI is authored at 1280x720 and zoomed, so window size is compositing cost
on the fog and cutscene layers. Against a 1440p60 clip, dropped frames went
5% at 1280x720, 34% at 1920x1080, 64% stretched to 3440x1440. Holding the
ratio keeps the cost on one number the user can see.

The math lives here instead of shell.py so it can be tested.
"""

BASE_W, BASE_H = 1280, 720

# the smallest the window goes, opened or dragged. The page zooms the whole
# 1280x720 layout to fit, so a smaller window is the same picture smaller and
# nothing reflows. 75% is where the help bar's text is still readable. Holding
# 1280x720 put the footer under the taskbar on a 1366x768 laptop
MIN_W, MIN_H = 960, 540

# WM_SIZING's wParam: which edge or corner the user has hold of.
WMSZ_LEFT = 1
WMSZ_RIGHT = 2
WMSZ_TOP = 3
WMSZ_TOPLEFT = 4
WMSZ_TOPRIGHT = 5
WMSZ_BOTTOM = 6
WMSZ_BOTTOMLEFT = 7
WMSZ_BOTTOMRIGHT = 8

# a pure vertical drag is the only case where height is the axis the user is
# actually moving. everything else, corners included, follows the width.
DRIVEN_BY_HEIGHT = (WMSZ_TOP, WMSZ_BOTTOM)
MOVES_LEFT_EDGE = (WMSZ_LEFT, WMSZ_TOPLEFT, WMSZ_BOTTOMLEFT)
MOVES_TOP_EDGE = (WMSZ_TOP, WMSZ_TOPLEFT, WMSZ_TOPRIGHT)

# best guess at what Windows adds around the client area, used only to pick the
# opening size. the live constraint measures the real thing off the window.
CHROME_W, CHROME_H = 16, 39

# never touch the edges of the work area, whatever the fraction below works out to
FIT_W, FIT_H = 0.95, 0.95

# how much of the screen to open at. Launchers open modest (Steam, Battle.net
# and Riot all open around 1000px wide): this lands on 1280x720 at 1080p and a
# bit over 1400 wide at 1440p.
OPEN_FRACTION = 0.55

# the most a window ever opens at, before anyone has asked for more. Well above
# where OPEN_FRACTION lands on any normal monitor, so it is the backstop rather
# than the rule. Maximise and F11 go past it, that's what they're for.
OPEN_MAX_W, OPEN_MAX_H = BASE_W * 2, BASE_H * 2

# and a launcher does not open enormous on a 4K panel just because it can
OPEN_LAUNCHER_W, OPEN_LAUNCHER_H = 1600, 900


def height_for(width: int) -> int:
    return round(width * BASE_H / BASE_W)


def width_for(height: int) -> int:
    return round(height * BASE_W / BASE_H)


def nearest_16_9(outer, chrome=(CHROME_W, CHROME_H)) -> tuple[int, int]:
    """
    the closest 16:9 window to one that isn't, as an outer size. Grows, never
    shrinks, so the edge the user dragged stays where they left it. Fallback
    for when the WM_SIZING hook didn't install, run once the drag settles.
    """
    width = max(outer[0] - chrome[0], MIN_W)
    height = max(outer[1] - chrome[1], MIN_H)
    width = max(width, width_for(height))
    return width + chrome[0], height_for(width) + chrome[1]


def opening_size(screen) -> tuple[int, int]:
    """
    the client size to open at: launcher sized and 16:9. The authored size
    when it fits, else the biggest 16:9 that does, down to MIN_W (a 1366x768
    laptop has 728px of work area, so 720 plus a title bar didn't fit).
    """
    if not screen or screen[0] <= 0 or screen[1] <= 0:
        return BASE_W, BASE_H
    room_w = int(screen[0] * FIT_W) - CHROME_W
    room_h = int(screen[1] * FIT_H) - CHROME_H
    width = min(int(screen[0] * OPEN_FRACTION), OPEN_LAUNCHER_W,
                room_w, width_for(room_h), OPEN_MAX_W)
    if width < BASE_W:
        width = max(MIN_W, min(BASE_W, room_w, width_for(room_h)))
    return width, height_for(width)


def opening_size_at(screen, scale: float = 1.0) -> tuple[int, int]:
    """
    opening_size for a display scaled by `scale`, in real pixels. Picked in
    the display's own units and scaled up, or at 150% the text comes out two
    thirds the size of everything else on the screen.
    """
    if not screen or screen[0] <= 0 or screen[1] <= 0 or scale <= 1:
        return opening_size(screen)
    logical = (round(screen[0] / scale), round(screen[1] / scale))
    width = round(opening_size(logical)[0] * scale)
    room_w = int(screen[0] * FIT_W) - round(CHROME_W * scale)
    room_h = int(screen[1] * FIT_H) - round(CHROME_H * scale)
    width = max(MIN_W, min(width, room_w, width_for(room_h)))
    return width, height_for(width)


def centered_at(client, screen) -> tuple[int, int] | None:
    """
    top left corner for a window of this client size, centred on the screen.
    pywebview's CenterScreen doesn't work on WebView2 (issue 1771). None when
    the screen's unknown, so the toolkit places it instead of a guess.
    """
    if not screen or screen[0] <= 0 or screen[1] <= 0:
        return None
    outer_w = client[0] + CHROME_W
    outer_h = client[1] + CHROME_H
    # clamped at zero so a window wider than the work area starts on screen
    # rather than half off the left edge
    return max(0, (screen[0] - outer_w) // 2), max(0, (screen[1] - outer_h) // 2)


def constrain(rect, edge: int, chrome=(CHROME_W, CHROME_H)) -> tuple[int, int, int, int]:
    """
    correct a window rect mid drag so the client area stays 16:9.

    rect is (left, top, right, bottom) of the whole window, as WM_SIZING
    hands it over. chrome is measured off the live window since it changes
    with DPI and Windows version. The held edge picks which axis leads and
    which edges may move; anchor the wrong side and the window walks across
    the desktop.
    """
    left, top, right, bottom = rect
    chrome_w, chrome_h = chrome

    if edge in DRIVEN_BY_HEIGHT:
        height = max((bottom - top) - chrome_h, MIN_H)
        width = width_for(height)
    else:
        width = max((right - left) - chrome_w, MIN_W)
        height = height_for(width)

    outer_w, outer_h = width + chrome_w, height + chrome_h
    if edge in MOVES_LEFT_EDGE:
        left = right - outer_w
    else:
        right = left + outer_w
    if edge in MOVES_TOP_EDGE:
        top = bottom - outer_h
    else:
        bottom = top + outer_h
    return left, top, right, bottom
