"""what the Auto graphics preset picks for a PC"""

import pytest

from patcher.hardware import auto_tier, tier


@pytest.mark.parametrize("memory, width, picked", [
    (None, None, "Medium"),
    (None, 3840, "Medium"),
    (1, 1920, "Low"),
    (1.5, None, "Low"),
    (2, 1920, "Medium"),
    (3.9, 2560, "Medium"),
    (4, 1920, "High"),
    (6, 3840, "Medium"),
    (3, 3840, "Low"),
    (1, 3840, "Low"),
    (8, 3840, "High"),
    (24, 7680, "High"),
])
def test_tiers(memory, width, picked):
    assert tier(memory, width) == picked


def test_a_pc_it_cant_read_still_gets_a_tier():
    assert auto_tier() in ("Low", "Medium", "High")
