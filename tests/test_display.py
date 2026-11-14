import pytest

from patcher import display as d

CHROME = (16, 39)


def client(rect):
    left, top, right, bottom = rect
    return (right - left) - CHROME[0], (bottom - top) - CHROME[1]


@pytest.mark.parametrize("edge", range(d.WMSZ_LEFT, d.WMSZ_BOTTOMRIGHT + 1))
def test_every_edge_comes_back_16_by_9(edge):
    """
    a drag that ends up 2.4:1 is the one that costs frames, and the ultrawide
    shape is the one nobody meant to make.
    """
    dragged = (100, 100, 100 + 2400 + CHROME[0], 100 + 700 + CHROME[1])
    w, h = client(d.constrain(dragged, edge, CHROME))
    # within half a pixel: 700 tall is 1244.4 wide, and a window is whole pixels
    assert abs(w - h * 16 / 9) <= 0.5


def test_the_axis_the_user_is_dragging_is_the_one_that_wins():
    """
    pull the right edge and the width is what you asked for, with the height
    following. pull the bottom and it is the other way round. correcting the
    axis under the mouse is what makes a locked window feel like it is
    fighting you.
    """
    start = (0, 0, 1280 + CHROME[0], 720 + CHROME[1])

    wider = d.constrain((0, 0, 1900 + CHROME[0], 720 + CHROME[1]), d.WMSZ_RIGHT, CHROME)
    assert client(wider) == (1900, d.height_for(1900))

    taller = d.constrain((0, 0, 1280 + CHROME[0], 1000 + CHROME[1]), d.WMSZ_BOTTOM, CHROME)
    assert client(taller) == (d.width_for(1000), 1000)

    # a corner follows the width, same as a side
    corner = d.constrain((0, 0, 1600 + CHROME[0], 1400 + CHROME[1]), d.WMSZ_BOTTOMRIGHT, CHROME)
    assert client(corner) == (1600, d.height_for(1600))
    assert client(start) == (1280, 720)


def test_the_edge_you_are_not_holding_stays_put():
    """
    the window has to grow away from the mouse, not walk across the desktop.
    drag the left edge and the right edge is the anchor, and the same for top
    against bottom.
    """
    box = (500, 400, 500 + 1280 + CHROME[0], 400 + 720 + CHROME[1])

    pulled_left = d.constrain((200, 400, box[2], box[3]), d.WMSZ_LEFT, CHROME)
    assert pulled_left[2] == box[2], "right edge moved while dragging the left"
    assert pulled_left[1] == box[1], "top moved while dragging a side"

    pulled_up = d.constrain((box[0], 100, box[2], box[3]), d.WMSZ_TOP, CHROME)
    assert pulled_up[3] == box[3], "bottom moved while dragging the top"
    assert pulled_up[0] == box[0], "left moved while dragging the top"

    pulled_corner = d.constrain((200, 100, box[2], box[3]), d.WMSZ_TOPLEFT, CHROME)
    assert (pulled_corner[2], pulled_corner[3]) == (box[2], box[3])


def test_it_cannot_be_dragged_smaller_than_readable():
    """
    the page zooms the 1280x720 layout to the window, so smaller is the same
    picture smaller. 75% is as small as the help bar stays readable
    """
    squashed = d.constrain((0, 0, 400 + CHROME[0], 300 + CHROME[1]), d.WMSZ_BOTTOMRIGHT, CHROME)
    assert client(squashed) == (d.MIN_W, d.MIN_H)

    # and from the vertical axis too, where the floor has to come off the height
    flattened = d.constrain((0, 0, 1280 + CHROME[0], 200 + CHROME[1]), d.WMSZ_BOTTOM, CHROME)
    assert client(flattened) == (d.MIN_W, d.MIN_H)

    # under the authored size is allowed now, held to 16:9 like any other drag
    smaller = d.constrain((0, 0, 1100 + CHROME[0], 619 + CHROME[1]), d.WMSZ_RIGHT, CHROME)
    assert client(smaller) == (1100, 619)
    assert (d.MIN_W, d.MIN_H) == (d.BASE_W * 3 // 4, d.BASE_H * 3 // 4)


def test_constraining_an_already_correct_window_changes_nothing():
    """held steady mid drag, this runs on every mouse move. it has to settle."""
    box = (300, 200, 300 + 1920 + CHROME[0], 200 + 1080 + CHROME[1])
    once = d.constrain(box, d.WMSZ_BOTTOMRIGHT, CHROME)
    assert once == box
    assert d.constrain(once, d.WMSZ_BOTTOMRIGHT, CHROME) == once


def test_the_chrome_is_taken_off_before_the_ratio_is_applied():
    """
    the 16:9 belongs to the client area, the way a game means it. applying it
    to the whole window instead leaves the page permanently off by a title bar,
    which is a 39px black strip that never goes away.
    """
    fat = (0, 0, 1920 + 40, 1080 + 120)
    out = d.constrain(fat, d.WMSZ_BOTTOMRIGHT, (40, 120))
    assert (out[2] - out[0]) - 40 == 1920
    assert (out[3] - out[1]) - 120 == 1080


def test_the_window_opens_launcher_sized_not_screen_sized():
    """
    Launchers open modest and centred. This used to take 0.94 of the screen,
    which on a 1440p monitor is a 2183px window before anyone has asked for
    anything, and it read as an application rather than a front end.
    """
    # the work area, so these are already taskbar free
    assert d.opening_size((1920, 1040)) == (1280, 720)
    assert d.opening_size((2560, 1400)) == (1408, 792)
    # a 1366x768 laptop's work area is 728 tall, and 720 plus a title bar ran
    # under the taskbar. it opens as big as fits instead
    width, height = d.opening_size((1366, 728))
    assert height + d.CHROME_H <= 728 * d.FIT_H and width == d.width_for(height)
    assert d.opening_size((1366, 728)) == (1159, 652)
    # and never past the floor, however small the screen
    assert d.opening_size((800, 600)) == (d.MIN_W, d.MIN_H)
    # an ultrawide opens as a 16:9 window on it, not as an ultrawide
    assert d.opening_size((3440, 1400)) == (1600, 900)
    assert d.opening_size(None) == (1280, 720)
    assert d.opening_size((0, 0)) == (1280, 720)

    for screen in [(1920, 1040), (2560, 1400), (3840, 2120), (3440, 1400)]:
        w, h = d.opening_size(screen)
        assert round(w / h, 2) == round(16 / 9, 2), screen
        assert w + d.CHROME_W <= screen[0] and h + d.CHROME_H <= screen[1], screen
        # never more than two thirds of the width, or it stops reading as one
        assert (w + d.CHROME_W) / screen[0] <= 0.70, screen


def test_a_big_monitor_does_not_open_a_big_window():
    """
    index.html clamps its zoom at 2x, so a window past 2560x1440 is more pixels
    to composite and not one pixel more picture. Long before that, a launcher
    has stopped looking like one.
    """
    assert d.opening_size((3840, 2120)) == (1600, 900)
    assert d.opening_size((7680, 4320)) == (1600, 900)


def test_the_window_opens_in_the_middle_of_the_screen():
    """
    We place it ourselves. pywebview asks for FormStartPosition.CenterScreen
    and WebView2 ignores it, landing the window near the top left at a spot
    that moves between runs, which is Windows cascading it.
    """
    for screen in [(1920, 1040), (2560, 1400), (3840, 2120)]:
        client = d.opening_size(screen)
        x, y = d.centered_at(client, screen)
        # equal gaps either side, within a pixel of rounding
        right_gap = screen[0] - (x + client[0] + d.CHROME_W)
        bottom_gap = screen[1] - (y + client[1] + d.CHROME_H)
        assert abs(x - right_gap) <= 1, screen
        assert abs(y - bottom_gap) <= 1, screen
        assert x > 0 and y > 0, screen


def test_placement_is_left_to_the_toolkit_when_the_screen_is_unknown():
    """a guessed coordinate is worse than no coordinate"""
    assert d.centered_at((1280, 720), None) is None
    assert d.centered_at((1280, 720), (0, 0)) is None


def test_a_window_wider_than_the_screen_still_starts_on_it():
    """
    the floor is 960x540 whatever the screen, so a tiny display can still get
    a window bigger than its work area. it has to start at the corner rather
    than half off the left edge with the title bar unreachable.
    """
    assert d.centered_at((960, 540), (800, 480)) == (0, 0)


def test_the_base_size_matches_what_the_page_is_authored_at():
    from pathlib import Path

    import re

    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
    found = re.search(r"BASE_W = (\d+), BASE_H = (\d+), MAX_SCALE = (\d+)", ui)
    assert found and (int(found[1]), int(found[2])) == (d.BASE_W, d.BASE_H)
    # F11 on a 4K panel is 3x. A cap under that leaves the stage floating in
    # the middle of a black screen, which is not what fullscreen asked for
    assert int(found[3]) >= 3
    assert (d.OPEN_MAX_W, d.OPEN_MAX_H) == (d.BASE_W * 2, d.BASE_H * 2)


def test_the_snap_fallback_grows_rather_than_shrinks():
    """
    Shrinking to fit pulls the edge the user just dragged back past where they
    left it, which reads as the window refusing the drag. Growing means
    whatever they uncovered stays uncovered.
    """
    # dragged wide: the height comes up to meet the width
    assert d.nearest_16_9((2000 + 16, 805 + 39)) == (2000 + 16, d.height_for(2000) + 39)
    # dragged tall: the width comes out to meet the height
    assert d.nearest_16_9((1300 + 16, 1000 + 39)) == (d.width_for(1000) + 16, 1000 + 39)

    for outer in [(2016, 844), (1316, 1039), (1296, 759), (3456, 1479)]:
        w, h = d.nearest_16_9(outer)
        assert round((w - 16) / (h - 39), 2) == round(16 / 9, 2), outer
        assert w >= outer[0] and h >= outer[1], f"{outer} shrank to {(w, h)}"


def test_a_window_already_square_is_left_alone():
    """
    this runs off the resize event and its own resize comes back round as
    another one. a correction that never settles is an infinite loop.
    """
    square = (1920 + 16, 1080 + 39)
    assert d.nearest_16_9(square) == square
    assert d.nearest_16_9(d.nearest_16_9((2000 + 16, 805 + 39))) == d.nearest_16_9((2016, 844))


def test_the_snap_will_not_go_under_the_floor():
    assert d.nearest_16_9((400, 300)) == (d.MIN_W + 16, d.MIN_H + 39)


def test_a_scaled_display_opens_at_the_size_its_scale_asks_for():
    """
    the page lays out 1280x720 css px, so on a 1080p laptop at 150% a window
    of 1280x720 real pixels drew every word at two thirds the size of
    everything else on that screen
    """
    # 100% is exactly what it always was
    assert d.opening_size_at((1920, 1032), 1.0) == d.opening_size((1920, 1032))
    # 125% on 1080p gets the whole authored layout at the display's scale
    assert d.opening_size_at((1920, 1032), 1.25) == (1600, 900)
    # 150% on 1080p wants 1920x1080, which doesn't fit, so it's the biggest that does
    width, height = d.opening_size_at((1920, 1032), 1.5)
    assert width > 1280 and height + round(d.CHROME_H * 1.5) <= 1032 * d.FIT_H
    # a 4K panel at 150% stops looking like a postage stamp
    assert d.opening_size_at((3840, 2112), 1.5) == (2112, 1188)
    # and nothing ever comes out bigger than the screen it's opening on
    for screen, scale in (((1366, 728), 1.25), ((1600, 860), 1.5), ((2560, 1392), 2.0)):
        width, height = d.opening_size_at(screen, scale)
        assert width + d.CHROME_W <= screen[0] and height + d.CHROME_H <= screen[1]
