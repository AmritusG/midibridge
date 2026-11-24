"""EQ mode: knob 2 long press, low/mid/high EQ knobs on channel 1, the
two pushes, exclusivity with FX mode, mixer sync, and the normal surface
(faders, mutes, mirror, trim) left alone."""

from __future__ import annotations

import math

import pytest

from bridge import eq, extender, logger
from bridge.decoder import FaderMove, StripButton
from conftest import Rig, vrev_params

RED = extender.SCRIBBLE_RED
CYAN = extender.SCRIBBLE_CYAN
MAGENTA = extender.SCRIBBLE_MAGENTA
TOP_DARK_BIT = 0x10   # set = dark top text (extender.TEXT_DARK << 4)


def enter_eq(rig, **warm):
    rig.warm_eq(**warm)
    assert rig.eq_long_press() == 1
    assert rig.eq.active
    rig.link.clear()


def last(rig, address):
    w = rig.link.writes_to(address)
    return w[-1] if w else None


# ----- hold knob 2 ----------------------------------------------------------

def test_long_press_knob2_enters_and_second_leaves(rig):
    rig.warm_eq()
    rig.push(2, True)
    assert [h.delay for h in rig.sched.pending()] == [1.0]
    assert not rig.eq.active
    rig.sched.fire(delay=1.0)
    assert rig.eq.active, "EQ mode on while knob 2 still held"
    rig.push(2, False)
    assert rig.eq.active, "release after the hold does not toggle again"
    labels = [rig.motor.scribble[k][0] for k in range(1, 9)]
    assert labels == ["LO G", "LO F", "MID G", "MID F", "MID Q", "HI G",
                      "HI F", "LCUT"]
    assert all(rig.motor.scribble[k][2] == RED for k in range(1, 9))
    assert [rig.motor.scribble[k][1] for k in range(1, 9)] == [
        "+0.0dB", "159Hz", "+0.0dB", "1.98kHz", "2.0", "+0.0dB", "14.2kHz",
        "20Hz"]
    rig.eq_long_press()
    assert not rig.eq.active
    assert rig.motor.scribble[1] == ("Headmic", "   1", MAGENTA)


def test_short_push_knob2_does_nothing(rig):
    rig.warm_eq()
    rig.link.clear()
    writes_before = rig.motor.scribble_writes
    rig.push(2, True)
    rig.push(2, False)
    assert rig.sched.fire(delay=1.0) == 0, "hold timer was cancelled"
    assert not rig.eq.active
    assert rig.link.writes == [] and rig.link.target_writes == []
    assert rig.motor.scribble_writes == writes_before


def test_eq_pushes_do_nothing_outside_eq_mode(rig):
    rig.warm_eq()
    rig.link.clear()
    for k in (5, 8):
        rig.push(k, True)
        rig.push(k, False)
    assert rig.link.writes == []


def test_hold_knob_and_hold_s_come_from_config():
    r = Rig(eq_overrides={"hold_knob": 3, "hold_s": 1.5})
    r.push(2, True)
    assert r.sched.pending() == [], "knob 2 is not the hold knob now"
    r.push(2, False)
    r.push(3, True)
    assert [h.delay for h in r.sched.pending()] == [1.5]


def test_disabled_eq_mode_ignores_hold():
    r = Rig(eq_overrides={"enabled": False})
    r.push(2, True)
    assert r.sched.pending() == []


def test_works_in_operation_mode_with_trim_locked():
    r = Rig(setup_mode=False)
    r.state.lock_trim_in_operation_mode = True
    r.warm_eq()
    r.eq_long_press()
    r.link.clear()
    r.turn(3, r.motor.last_ring(3) + 1)
    assert r.link.writes_to("/ch/01/eq/3/g")


# ----- exclusivity with FX mode -----------------------------------------------

def test_fx_to_eq_leaves_fx_cleanly(rig):
    rig.warm(fx_type=6, params=vrev_params())
    rig.warm_eq()
    rig.long_press()                     # knob 1: FX mode
    assert rig.fx.active
    rig.eq_long_press()                  # knob 2: EQ mode
    assert rig.eq.active and not rig.fx.active
    assert rig.motor.scribble[1][:3] == ("LO G", "+0.0dB", RED)
    assert all(rig.motor.scribble[k][2] == RED for k in range(1, 9))
    # FX knobs are gone: knob 1 now moves the low gain, not the FX send.
    rig.link.clear()
    rig.turn(1, rig.motor.last_ring(1) + 1)
    assert rig.link.writes_to("/ch/01/mix/07/level") == []
    assert rig.link.writes_to("/ch/01/eq/1/g")
    # and leaving EQ mode goes back to normal, not to FX mode.
    rig.eq_long_press()
    assert not rig.eq.active and not rig.fx.active
    assert rig.motor.scribble[1] == ("Headmic", "   1", MAGENTA)


def test_eq_to_fx_leaves_eq_cleanly(rig):
    rig.warm(fx_type=6, params=vrev_params())
    enter_eq(rig)
    rig.long_press()                     # knob 1 held inside EQ mode
    assert rig.fx.active and not rig.eq.active
    assert rig.motor.scribble[1][0] == "VREV"
    assert all(rig.motor.scribble[k][2] == CYAN for k in range(1, 9))
    rig.link.clear()
    rig.turn(3, rig.motor.last_ring(3) + 1)
    assert rig.link.writes_to("/ch/01/eq/3/g") == []
    assert rig.link.writes_to("/fx/1/par/02")
    rig.push(8, True)                    # FX's knob 8: type step, not EQ off
    rig.push(8, False)
    assert rig.link.writes_to("/ch/01/eq/on") == []
    assert rig.link.writes_to("/fx/1/type") == [7]


def test_short_knob1_push_in_eq_mode_stays_in_eq(rig):
    enter_eq(rig)
    rig.push(1, True)
    rig.push(1, False)
    rig.sched.fire(delay=1.0)
    assert rig.eq.active and not rig.fx.active


# ----- knobs: addresses, types, laws -------------------------------------------

EXPECTED = {   # knob -> (address, start wire, wire step per tick)
    1: ("/ch/01/eq/1/g", 0.5, 2 / 120),
    2: ("/ch/01/eq/1/f", 0.30, 2 / 200),
    3: ("/ch/01/eq/3/g", 0.5, 2 / 120),
    4: ("/ch/01/eq/3/f", 0.665, 2 / 200),
    5: ("/ch/01/eq/3/q", 0.4648, -1 / 71),     # right = higher Q = lower wire
    6: ("/ch/01/eq/4/g", 0.5, 2 / 120),
    7: ("/ch/01/eq/4/f", 0.95, 2 / 200),
    8: ("/ch/01/preamp/hpf", 0.0, 2 / 100),
}


@pytest.mark.parametrize("knob", sorted(EXPECTED))
def test_every_knob_hits_its_address_with_a_float_on_the_grid(rig, knob):
    enter_eq(rig)
    addr, start, step = EXPECTED[knob]
    rig.turn(knob, rig.motor.last_ring(knob) + 1)
    w = rig.link.writes_to(addr)
    assert len(w) == 1 and isinstance(w[0], float), (addr, w)
    assert w[0] == pytest.approx(start + step, abs=0.006), addr
    others = [a for a, _ in rig.link.writes if a != addr]
    assert others == [], others


def test_gain_steps_half_a_db_and_clamps(rig):
    enter_eq(rig)
    r = rig.motor.last_ring(3)
    rig.turn(3, r + 1)
    assert eq.gain_db(last(rig, "/ch/01/eq/3/g")) == pytest.approx(0.5)
    rig.turn(3, r + 3)                   # two more ticks
    assert eq.gain_db(last(rig, "/ch/01/eq/3/g")) == pytest.approx(1.5)
    rig.turn(3, r + 1)                   # back two
    assert eq.gain_db(last(rig, "/ch/01/eq/3/g")) == pytest.approx(0.5)
    assert rig.motor.scribble[3][1] == "+0.5dB"
    # Clamp at +15 dB: from +14.5, five ticks up only reach 1.0.
    rig.mixer("/ch/01/eq/3/g", (14.5 + 15) / 30)
    n = len(rig.link.writes)
    base = rig.motor.last_ring(3)
    for i in range(1, 6):
        rig.turn(3, base + i)
    assert last(rig, "/ch/01/eq/3/g") == pytest.approx(1.0)
    assert len(rig.link.writes) == n + 1, "no writes once at the top"
    assert rig.motor.scribble[3][1] == "+15.0dB"


def test_freq_steps_a_tenth_of_an_octave_and_clamps_low(rig):
    enter_eq(rig)
    r = rig.motor.last_ring(4)
    rig.turn(4, r + 1)
    hz0, hz1 = eq.freq_hz(0.665), eq.freq_hz(last(rig, "/ch/01/eq/3/f"))
    assert math.log2(hz1 / hz0) == pytest.approx(0.1, abs=0.005)
    rig.mixer("/ch/01/eq/1/f", 0.005)
    base = rig.motor.last_ring(2)
    rig.turn(2, base - 1)
    rig.turn(2, base - 2)
    assert rig.link.writes_to("/ch/01/eq/1/f") == [0.0]
    assert rig.motor.scribble[2][1] == "20Hz"


def test_q_knob_right_raises_q(rig):
    enter_eq(rig)
    r = rig.motor.last_ring(5)
    rig.turn(5, r + 1)
    q = eq.q_value(last(rig, "/ch/01/eq/3/q"))
    assert q > 2.0 and q == pytest.approx(2.0 * 1.05, rel=0.02)


def test_relative_turns_ignore_counter_jumps_and_ring_echo(rig):
    enter_eq(rig)
    rig.turn(3, 120)                     # far from the ring we wrote
    assert rig.link.writes == []
    rig.turn(3, 121)
    assert eq.gain_db(last(rig, "/ch/01/eq/3/g")) == pytest.approx(0.5)
    rig.link.clear()
    rig.turn(1, rig.motor.last_ring(1))  # echo of our ring write
    assert rig.link.writes == []


def test_knob_map_comes_from_config():
    r = Rig(eq_overrides={"knobs": {1: "mid_q", 2: None, 3: "low_g",
                                    4: None, 5: None, 6: None, 7: None,
                                    8: None}})
    r.warm_eq()
    r.eq_long_press()
    r.link.clear()
    r.turn(1, r.motor.last_ring(1) + 1)
    assert r.link.writes_to("/ch/01/eq/3/q")
    assert r.motor.scribble[1][0] == "MID Q"
    assert r.motor.scribble[2][:2] == ("", "")
    r.link.clear()
    r.turn(2, 50)
    r.turn(2, 52)
    assert r.link.writes == [], "unmapped knob does nothing"


def test_mid_band_comes_from_config():
    r = Rig(eq_overrides={"mid_band": 2})
    r.warm_eq()
    r.eq_long_press()
    r.link.clear()
    r.turn(3, r.motor.last_ring(3) + 1)
    assert r.link.writes_to("/ch/01/eq/2/g")
    assert r.motor.scribble[4][1] == "497Hz"


def test_turning_lowcut_while_off_switches_it_on(rig):
    enter_eq(rig, hpon=0, hpf=0.5)
    assert rig.motor.scribble[8][1] == "OFF"
    rig.turn(8, rig.motor.last_ring(8) + 1)
    assert rig.link.writes_to("/ch/01/preamp/hpon") == [1]
    assert isinstance(rig.link.writes_to("/ch/01/preamp/hpon")[0], int)
    assert rig.link.writes_to("/ch/01/preamp/hpf") == [pytest.approx(0.52)]
    assert rig.motor.scribble[8][1] == eq.format_hz(eq.lowcut_hz(0.52, 400))


# ----- band types on entry ------------------------------------------------------

def test_entry_turns_cuts_into_shelves_and_logs_old_types(rig):
    rig.warm_eq()                        # venue: band 1 LCut, band 4 HCut
    rig.eq_long_press()
    assert rig.link.writes_to("/ch/01/eq/1/type") == [1]     # LShv
    assert rig.link.writes_to("/ch/01/eq/4/type") == [4]     # HShv
    assert rig.link.writes_to("/ch/01/eq/3/type") == [], "PEQ mid kept"
    assert not [a for a, _ in rig.link.writes if a.startswith("/ch/01/eq/2/")]
    assert all(isinstance(v, int) for a, v in rig.link.writes
               if a.endswith("/type"))
    evs = [e for e in logger.get_ring() if e["msg"].startswith("band type")]
    old = {e["data"]["band"]: e["data"]["old_type"] for e in evs[-2:]}
    assert old == {1: "0:LCut", 4: "5:HCut"}
    assert evs[-1]["data"]["freq"] == "14.2kHz"
    # Frequencies are kept.
    assert rig.link.writes_to("/ch/01/eq/1/f") == []
    assert rig.link.writes_to("/ch/01/eq/4/f") == []


def test_entry_leaves_shelves_alone_and_fixes_a_cut_mid(rig):
    rig.warm_eq(bands={1: (1, 0.3, 0.5, 0.5), 2: (2, 0.4, 0.5, 0.5),
                       3: (5, 0.6, 0.5, 0.5), 4: (4, 0.9, 0.5, 0.5)})
    rig.eq_long_press()
    types = [(a, v) for a, v in rig.link.writes if a.endswith("/type")]
    assert types == [("/ch/01/eq/3/type", 2)]


def test_entry_with_unknown_types_sets_them_on_arrival(rig):
    rig.eq_long_press()                  # nothing cached
    assert "/ch/01/eq/1/type" in rig.link.queries
    assert rig.link.writes == []
    rig.mixer("/ch/01/eq/1/type", 0)
    assert rig.link.writes_to("/ch/01/eq/1/type") == [1]
    rig.eq.exit()
    rig.link.clear()
    rig.mixer("/ch/01/eq/4/type", 5)     # after leaving: not touched
    assert rig.link.writes == []


def test_shelves_on_entry_can_be_switched_off():
    r = Rig(eq_overrides={"shelves_on_entry": False})
    r.warm_eq()
    r.eq_long_press()
    assert [a for a, _ in r.link.writes if a.endswith("/type")] == []


# ----- pushes in EQ mode ---------------------------------------------------------

def test_knob8_push_toggles_eq_on_off(rig):
    enter_eq(rig)
    rig.push(8, True)
    rig.push(8, False)
    assert rig.link.writes_to("/ch/01/eq/on") == [0]
    assert isinstance(rig.link.writes_to("/ch/01/eq/on")[0], int)
    # bypass shows: EQ strips' top text flips shade, LCUT (preamp) does not
    assert not rig.motor.scribble_flags[1] & TOP_DARK_BIT
    assert rig.motor.scribble_flags[8] & TOP_DARK_BIT
    rig.push(8, True)
    rig.push(8, False)
    assert rig.link.writes_to("/ch/01/eq/on") == [0, 1]
    assert rig.motor.scribble_flags[1] & TOP_DARK_BIT
    assert rig.link.writes_to("/ch/01/preamp/hpf") == [], "push is not a turn"


def test_knob5_push_resets_the_last_turned_band_to_flat(rig):
    enter_eq(rig)
    rig.push(5, True)                    # nothing turned yet: nothing to do
    rig.push(5, False)
    assert rig.link.writes == []
    r = rig.motor.last_ring(7)
    rig.turn(6, rig.motor.last_ring(6) + 3)          # high gain up 1.5 dB
    rig.turn(7, r + 1)                               # high freq: same band
    rig.link.clear()
    rig.push(5, True)
    rig.push(5, False)
    assert rig.link.writes == [("/ch/01/eq/4/g", 0.5)]
    assert rig.motor.scribble[6][1] == "+0.0dB"
    assert rig.motor.last_ring(6) == 64
    # LCUT has no gain: after turning it, the reset does nothing.
    rig.turn(8, rig.motor.last_ring(8) + 1)
    rig.link.clear()
    rig.push(5, True)
    assert rig.link.writes == []


def test_pushes_can_be_removed():
    r = Rig(eq_overrides={"push_eq_onoff": None, "push_reset_band": None})
    r.warm_eq()
    r.eq_long_press()
    r.link.clear()
    for k in (5, 8):
        r.push(k, True)
        r.push(k, False)
    assert r.link.writes == []


# ----- mixer -> Extender sync ---------------------------------------------------

def test_x_air_edit_changes_follow_on_rings_and_strips(rig):
    enter_eq(rig)
    rig.mixer("/ch/01/eq/4/g", 1.0)                 # high gain +15
    assert rig.motor.last_ring(6) == 127
    assert rig.motor.scribble[6][1] == "+15.0dB"
    rig.mixer("/ch/01/eq/3/f", 0.5)                 # mid 632 Hz
    assert rig.motor.scribble[4][1] == "632Hz"
    rig.mixer("/ch/01/eq/3/q", 0.0)                 # Q 10
    assert rig.motor.scribble[5][1] == "10"
    assert rig.motor.last_ring(5) == 127
    rig.mixer("/ch/01/preamp/hpon", 0)
    assert rig.motor.scribble[8][1] == "OFF"
    rig.mixer("/ch/01/eq/on", 0)
    assert not rig.motor.scribble_flags[3] & TOP_DARK_BIT
    rig.link.clear()
    rig.turn(6, rig.motor.last_ring(6) - 1)         # continues from +15
    assert eq.gain_db(last(rig, "/ch/01/eq/4/g")) == pytest.approx(14.5)
    assert rig.link.writes_to("/ch/01/eq/on") == [], "sync never writes"


def test_other_channels_are_ignored(rig):
    enter_eq(rig)
    rig.mixer("/ch/02/eq/1/g", 1.0)
    assert rig.motor.scribble[1][1] == "+0.0dB"
    assert "/ch/02/eq/1/g" not in rig.eq.vals


def test_trim_changes_do_not_take_over_rings_in_eq_mode(rig):
    enter_eq(rig)
    rings = list(rig.motor.rings)
    rig.on_target("trim_1", 0.9)
    assert rig.motor.rings == rings


# ----- leaving -------------------------------------------------------------------

def test_leaving_restores_scribbles_rings_and_trim_and_keeps_eq(rig):
    rig.on_target("trim_1", 0.4)
    rig.on_target("trim_2", 0.6)
    enter_eq(rig)
    rig.turn(1, rig.motor.last_ring(1) + 4)
    low_g = last(rig, "/ch/01/eq/1/g")
    rig.link.clear()
    rig.eq_long_press()
    assert not rig.eq.active
    assert rig.motor.scribble[1] == ("Headmic", "   1", MAGENTA)
    assert rig.motor.scribble[3][:2] == ("   BT", "  Left")
    assert rig.motor.scribble[8][0] == "MASTER"
    assert rig.motor.last_ring(1) == round(0.4 * 127)
    assert rig.motor.last_ring(2) == round(0.6 * 127)
    assert rig.motor.last_ring(8) == 0, "knob 8 has no trim"
    assert rig.link.writes == [], "leaving writes nothing to the mixer"
    assert rig.eq.vals["/ch/01/eq/1/g"] == low_g
    rig.turn(2, 70)                      # knob 2 is trim again, absolute
    assert rig.link.target_writes == [("trim_2", pytest.approx(70 / 127))]
    assert rig.link.writes == []


def test_leaving_in_bus_mode_restores_bus_scribbles(rig):
    rig.event(StripButton(strip=2, button="select", pressed=True))
    enter_eq(rig)
    rig.event(StripButton(strip=4, button="select", pressed=True))
    assert rig.motor.scribble[1][0] == "LO G", "bus switch keeps EQ strips"
    rig.eq_long_press()
    assert rig.motor.scribble[1][:2] == ("CH 1", ">BUS4")


# ----- only ch1 EQ + low cut; faders/mutes/mirror unchanged ----------------------

def test_only_ch1_eq_and_low_cut_are_written(rig):
    rig.warm(fx_type=6, params=vrev_params())
    rig.warm_eq(hpon=0)
    rig.eq_long_press()
    for k in range(1, 9):
        r = rig.motor.last_ring(k) or 0
        rig.turn(k, r + 1)
        rig.turn(k, r + 3)
        rig.turn(k, r - 2)
    for k in range(1, 9):
        rig.push(k, True)
        rig.push(k, False)
    rig.eq_long_press()
    allowed = ("/ch/01/eq/1/", "/ch/01/eq/3/", "/ch/01/eq/4/", "/ch/01/eq/on",
               "/ch/01/preamp/hpf", "/ch/01/preamp/hpon")
    assert rig.link.writes
    for a, _ in rig.link.writes:
        assert a.startswith(allowed), a
    assert rig.link.target_writes == []


def _surface_traffic(r):
    r.link.clear()
    r.motor.faders.clear()
    r.motor.leds.clear()
    r.event(FaderMove(strip=1, value=100))
    r.event(FaderMove(strip=8, value=90))          # lr + mirror to buses
    r.event(StripButton(strip=1, button="mute", pressed=True))
    r.event(StripButton(strip=8, button="mute", pressed=True))
    r.on_target(5, 0.3)
    r.on_target("mute_2", 0.0)
    return (list(r.link.target_writes), list(r.link.writes),
            list(r.motor.faders), list(r.motor.leds))


def test_faders_mutes_mirror_unaffected_by_eq_mode():
    outside = Rig()
    outside.warm_eq()
    inside = Rig()
    inside.warm_eq()
    inside.eq_long_press()
    assert inside.eq.active
    a = _surface_traffic(outside)
    b = _surface_traffic(inside)
    assert a == b
    assert [t for t, _ in a[0]] == [1, "lr", "bus_1", "bus_2", "bus_3",
                                    "bus_4", "bus_5", "bus_6", "mute_1",
                                    "mute_lr"]
    assert a[1] == []
    inside.eq_long_press()
    assert _surface_traffic(inside) == _surface_traffic(outside)


# ----- laws, formatting, config ------------------------------------------------

def test_value_laws_match_the_venue_mixers_readout():
    # /node replies from the venue XR18 (FW 1.25), 2026-11-24
    assert eq.freq_hz(0.30) == pytest.approx(158.9, abs=0.05)
    assert eq.freq_hz(0.465) == pytest.approx(496.6, abs=0.1)
    assert eq.freq_hz(0.665) == pytest.approx(1980, abs=5)
    assert eq.freq_hz(0.95) == pytest.approx(14160, abs=5)
    # 0.4647887 = 33/71: the Q grid has 72 points. The mixer prints
    # Q 1.96 as "2.0", as format_q does.
    assert eq.q_value(33 / 71) == pytest.approx(1.96, abs=0.005)
    assert eq.format_q(eq.q_value(0.4647887)) == "2.0"
    assert 0.30 * 200 == pytest.approx(60) and 0.95 * 200 == pytest.approx(190)
    assert eq.gain_db(0.5) == 0.0
    assert eq.gain_db(0.0) == -15.0 and eq.gain_db(1.0) == 15.0
    assert eq.q_value(0.0) == 10.0 and eq.q_value(1.0) == pytest.approx(0.3)
    assert eq.lowcut_hz(0.0, 400) == 20.0
    assert eq.lowcut_hz(1.0, 400) == pytest.approx(400)


def test_labels_and_values_fit_seven_chars():
    for p in eq.PARAMS.values():
        assert len(p.label) <= 7
    for w in [i / 200 for i in range(201)]:
        for s in (eq.format_db(eq.gain_db(w)), eq.format_hz(eq.freq_hz(w)),
                  eq.format_q(eq.q_value(w)),
                  eq.format_hz(eq.lowcut_hz(w, 400))):
            assert len(s) <= 7, s


def test_config_validation():
    c = eq.load_config({"enabled": True, "mid_band": 2,
                        "knobs": {1: "high_q"}})
    assert c.mid_band == 2 and c.knobs[1] == "high_q" and c.knobs[2] is None
    assert eq.load_config(None).enabled is False
    for bad, msg in (({"hold_knob": 1}, "hold_knob"),
                     ({"low_band": 3}, "three"),
                     ({"color": "purple"}, "color"),
                     ({"knobs": {1: "bass"}}, "not one of"),
                     ({"push_eq_onoff": 2}, "cannot be knob 2"),
                     ({"push_reset_band": 1}, "cannot be knob 1"),
                     ({"push_eq_onoff": 5, "push_reset_band": 5}, "same knob"),
                     ({"gain_step_db": 0}, "positive")):
        with pytest.raises(ValueError, match=msg):
            eq.load_config(bad)
    assert eq.load_config({"push_eq_onoff": None}).push_eq_onoff is None


def test_shipped_configs_load_with_eq_mode():
    from bridge.config import load
    from conftest import ROOT
    for name in ("config.example.yaml", "tests/fixtures/venue.yaml"):
        c = load(ROOT / name)
        assert c.eq_mode.enabled and c.eq_mode.hold_knob == 2, name
        assert c.eq_mode.knobs == eq.DEFAULT_KNOBS, name
        used = {e.background for e in c.scribble.entries.values()}
        assert c.eq_mode.color not in used, name
        assert c.eq_mode.color != c.fx_mode.color, name
        assert c.eq_mode.color != "yellow", "yellow is bus mode"


def test_fixture_matches_the_venue_config_except_ip():
    from conftest import ROOT
    a = (ROOT / "config.yaml")
    if not a.exists():
        pytest.skip("config.yaml is local only (gitignored)")
    venue = a.read_text().splitlines()
    fixture = (ROOT / "tests" / "fixtures" / "venue.yaml").read_text().splitlines()
    diff = [(x, y) for x, y in zip(venue, fixture) if x != y]
    assert len(venue) == len(fixture)
    assert [y.split("#")[0].strip() for _, y in diff] == ["ip: auto"]
