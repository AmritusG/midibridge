"""The venue scribble strips are exactly Amrit's block (2026-11-24), and
they come back exactly after leaving FX mode and EQ mode."""

from __future__ import annotations

from bridge import extender
from conftest import Rig

M, B, W, G = (extender.SCRIBBLE_MAGENTA, extender.SCRIBBLE_BLUE,
              extender.SCRIBBLE_WHITE, extender.SCRIBBLE_GREEN)
TOP_DARK, BOTTOM_DARK = 0x10, 0x20

# (top, bottom, background); trailing pad stripped, leading spaces kept.
AMRIT = {
    1: ("Headmic", "   1", M),
    2: ("Headmic", "   2", M),
    3: ("  BT", " Left", B),
    4: ("  BT", " Right", B),
    5: ("Laptop", "Left", W),
    6: ("Laptop", "Right", W),
    7: ("Handmic", "", M),
    8: ("MASTER", "  LR", G),
}


def strips(rig):
    """(top, bottom, background, text-shade bits) per strip, as last sent."""
    return {n: (*rig.motor.scribble[n], rig.motor.scribble_flags[n] & 0x30)
            for n in rig.motor.scribble}


def assert_amrit(rig):
    got = strips(rig)
    for n, (top, bottom, bg) in AMRIT.items():
        assert got[n][:3] == (top, bottom, bg), f"strip {n}"
        light = n == 8
        assert got[n][3] == (0 if light else TOP_DARK | BOTTOM_DARK), \
            f"strip {n} text shade"


def test_startup_paints_amrits_block():
    assert_amrit(Rig())


def test_leaving_fx_mode_repaints_amrits_block():
    rig = Rig()
    rig.warm()
    rig.long_press()
    assert rig.fx.active
    rig.long_press()
    assert not rig.fx.active
    assert_amrit(rig)


def test_leaving_eq_mode_repaints_amrits_block():
    rig = Rig()
    rig.warm_eq()
    rig.eq_long_press()
    assert rig.eq.active
    rig.eq_long_press()
    assert not rig.eq.active
    assert_amrit(rig)
