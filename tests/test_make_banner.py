"""
tools/make_banner.py, the XIII-2 area-name banner for the demo video. The
numbers in it were measured off the real thing, so these hold it to them:
where it sits at rest, which way it moves, and what order things leave in.
"""
import importlib.util
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
pytest.importorskip("fontTools")
pytest.importorskip("brotli")

spec = importlib.util.spec_from_file_location("make_banner", ROOT / "tools" / "make_banner.py")
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)

if not mb.FONT.is_file():
    pytest.skip("the app's font isn't in assets/", allow_module_level=True)


@pytest.fixture(scope="module")
def academia():
    # the banner it was measured against, so "at rest" means the game's numbers
    return mb.Banner("Academia", "-4XX AF-", "Central Bridge", hold=3.2)


def ink(frame, rows, cols=slice(None), thr=128):
    a = frame[rows, cols, 3] > thr
    ys, xs = np.where(a)
    off_x = cols.start or 0
    return (xs.min() + off_x, xs.max() + off_x, ys.min() + rows.start, ys.max() + rows.start) if len(xs) else None


def test_at_rest_it_sits_where_the_game_puts_it(academia):
    frame = academia.frame(mb.SETTLED + 0.6)
    name = ink(frame, slice(490, 540), slice(1100, 1440))
    tag = ink(frame, slice(500, 540), slice(1440, 1700))
    sub = ink(frame, slice(548, 582), slice(1500, 1800))
    # off the A alone, since Saira's d climbs past its caps
    cap_top = ink(frame, slice(490, 540), slice(1150, 1190))[2]
    # boxes measured off the capture once it had settled: name 1155..1425 with its
    # cap top on 499, tag 1443..1658, subtitle 1531..1742
    assert abs(name[0] - 1155) <= 1 and abs(name[1] - 1425) <= 1 and abs(cap_top - 499) <= 1
    assert abs(tag[0] - 1443) <= 2 and abs(tag[1] - 1658) <= 2
    assert abs(sub[0] - 1531) <= 3 and abs(sub[1] - 1742) <= 4


def test_the_rule_brightens_toward_the_right_edge(academia):
    row = academia.frame(mb.SETTLED + 0.6)[mb.RULE_Y, :, 3].astype(int)
    assert row[:mb.RULE_FROM].max() == 0
    assert row[800] < row[1300] < row[1900]


def test_nothing_is_on_screen_before_it_starts_or_after_it_ends(academia):
    assert academia.frame(0.0)[..., 3].max() == 0
    assert academia.frame(academia.duration)[..., 3].max() == 0


def test_the_subtitle_slides_in_from_the_left_and_the_name_from_the_right(academia):
    early, rest = academia.frame(0.6), academia.frame(mb.SETTLED + 0.6)
    assert ink(early, slice(548, 582))[0] < ink(rest, slice(548, 582))[0]
    assert ink(early, slice(490, 540), slice(1000, 1440))[0] > ink(rest, slice(490, 540), slice(1000, 1440))[0]


def test_leaving_it_glides_left_and_fades_from_the_left_so_the_tag_goes_last(academia):
    rest = academia.frame(academia.leaves - 0.05)
    mid = academia.frame(academia.leaves + 0.12)
    late = academia.frame(academia.leaves + 0.32)
    # the subtitle is gone first
    assert ink(mid, slice(548, 582), slice(1500, 1800)) is None
    # the tag hasn't started fading yet, and it's further left than at rest
    assert ink(mid, slice(500, 540), slice(1300, 1700))[1] < ink(rest, slice(500, 540), slice(1300, 1700))[1]
    # the name's left end fades before its right end does
    name = mid[495:540, :, 3]
    left_half, right_half = name[:, 1100:1250].max(), name[:, 1300:1420].max()
    assert left_half < right_half
    # and by the end only the tag is left
    assert ink(late, slice(490, 540), slice(0, 1300)) is None
    assert ink(late, slice(500, 540), slice(1300, 1700), thr=40) is not None


def test_a_long_subtitle_steps_left_instead_of_running_off_screen():
    long = mb.Banner("Mouse Support", "-LR-", "Camera, strikes and menus, all of them")
    box = ink(long.frame(mb.SETTLED + 0.6), slice(548, 582))
    assert box[1] <= mb.W - 40


def test_a_long_name_and_tag_step_left_together_instead_of_running_off_screen():
    long = mb.Banner("ArmyOfOne", "-Free and open source-", "github.com/KarpTheMagikarp/ffxiii-trilogy-patcher")
    frame = long.frame(mb.SETTLED + 0.6)
    line = ink(frame, slice(495, 540))
    assert line[1] <= mb.W - 40
    # the game's gap between name and tag survives the move
    short = mb.Banner("ArmyOfOne", "-Free and open source-")
    assert short.name_x == long.name_x < mb.NAME_X


def test_it_writes_prores_4444_with_alpha_one_frame_per_sixtieth(monkeypatch, tmp_path):
    seen = {}

    class Encoder:
        def __init__(self, cmd, stdin):
            seen["cmd"], seen["frames"] = cmd, 0
            self.stdin = self

        def write(self, data):
            seen["frames"] += 1

        def close(self):
            pass

        def wait(self):
            return 0

    banner = mb.Banner("Mouse Support", "-LR-", "Camera, strikes and menus", hold=0.5)
    monkeypatch.setattr(banner, "frame", lambda t: np.zeros((1, 1, 4), np.uint8))
    monkeypatch.setattr(subprocess, "Popen", Encoder)
    banner.write(tmp_path / "card.mov")

    cmd = " ".join(seen["cmd"])
    assert "prores_ks" in cmd and "-profile:v 4444" in cmd and "yuva444p10le" in cmd
    assert seen["frames"] == round(banner.duration * mb.FPS)


def test_the_cli_takes_a_tag_that_starts_with_a_dash(tmp_path):
    """every tag the game uses starts with one, and argparse would read it as a flag"""
    out = tmp_path / "still.png"
    mb.main(["Mouse Support", "--tag", "-LR-", "--still", "1.5", "--out", str(out)])
    from PIL import Image
    img = Image.open(out)
    assert img.mode == "RGBA" and img.size == (mb.W, mb.H)
