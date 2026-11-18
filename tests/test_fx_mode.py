"""FX mode: knob 1 long press, reverb knobs, type stepping, mixer sync,
and the normal surface (faders, mutes, mirror, trim) left alone."""

from __future__ import annotations

import pytest

from bridge import extender, fx, logger
from bridge.decoder import FaderMove, StripButton
from conftest import Rig, hall_params, vrev_params

CYAN = extender.SCRIBBLE_CYAN
MAGENTA = extender.SCRIBBLE_MAGENTA


def enter_vrev(rig, send=0.5):
    rig.warm(fx_type=6, params=vrev_params(), send=send)
    assert rig.long_press() == 1
    assert rig.fx.active
    rig.link.clear()


# ----- long press --------------------------------------------------------

def test_long_press_enters_and_second_long_press_leaves(rig):
    rig.warm(fx_type=6, params=vrev_params())
    rig.push(1, True)
    hold = [h for h in rig.sched.pending() if h.delay == 1.0]
    assert len(hold) == 1, "hold timer armed for hold_s"
    assert not rig.fx.active, "nothing happens before hold_s"
    rig.sched.fire(delay=1.0)
    assert rig.fx.active, "FX mode on while knob still held"
    rig.push(1, False)
    assert rig.fx.active, "release after the hold does not toggle again"
    assert rig.motor.scribble[1][2] == CYAN

    rig.long_press()
    assert not rig.fx.active
    assert rig.motor.scribble[1] == ("Headmic", "   1", MAGENTA)


def test_short_press_does_nothing(rig):
    rig.warm(fx_type=6, params=vrev_params())
    rig.link.clear()
    writes_before = rig.motor.scribble_writes
    rig.push(1, True)
    rig.push(1, False)
    assert rig.sched.fire(delay=1.0) == 0, "hold timer was cancelled"
    assert not rig.fx.active
    assert rig.link.writes == [] and rig.link.target_writes == []
    assert rig.motor.scribble_writes == writes_before


def test_hold_s_comes_from_config():
    r = Rig(fx_overrides={"hold_s": 1.5})
    r.push(1, True)
    assert [h.delay for h in r.sched.pending()] == [1.5]


def test_disabled_fx_mode_ignores_hold():
    r = Rig(fx_overrides={"enabled": False})
    r.push(1, True)
    assert r.sched.pending() == []
    assert not r.fx.active


def test_works_in_operation_mode_with_trim_locked():
    r = Rig(setup_mode=False)
    r.state.lock_trim_in_operation_mode = True
    r.warm(fx_type=6, params=vrev_params())
    r.long_press()
    assert r.fx.active
    r.link.clear()
    r.turn(2, r.motor.last_ring(2) + 3)
    assert r.link.writes_to("/fx/1/par/01")


# ----- knob 1: channel 1 -> FX 1 send ---------------------------------

def test_knob1_sends_fx1_send_level_with_scaling(rig):
    enter_vrev(rig, send=0.5)
    ring = rig.motor.last_ring(1)
    assert ring == 64, "ring shows the send level (0.5 * 127)"
    rig.turn(1, ring + 4)
    writes = rig.link.writes_to("/ch/01/mix/07/level")
    assert writes == [pytest.approx(0.5 + 4 / 127)]
    assert isinstance(writes[0], float)
    assert rig.link.target_writes == [], "trim_1 not touched"
    rig.turn(1, ring + 2)
    assert rig.link.writes_to("/ch/01/mix/07/level")[-1] == pytest.approx(
        0.5 + 2 / 127)
    # Bottom row shows dB on the X-Air fader law.
    assert rig.motor.scribble[1][0] == "VREV"
    assert rig.motor.scribble[1][1] == fx.format_level(0.5 + 2 / 127)


def test_knob1_send_clamps_at_full(rig):
    enter_vrev(rig, send=0.99)
    r = rig.motor.last_ring(1)
    rig.turn(1, min(127, r + 10))
    assert rig.link.writes_to("/ch/01/mix/07/level")[-1] <= 1.0


def test_send_address_follows_slot_and_channel():
    r = Rig(fx_overrides={"fx_slot": 2, "channel": 3})
    fxm = fx.FxMode(r.cfg.fx_mode, r.link, lambda *a: None,
                    lambda *a: None, lambda: None, scheduler=r.sched)
    assert fxm.addr_send == "/ch/03/mix/08/level"
    assert fxm.addr_type == "/fx/2/type"
    assert fxm.addr_rtn_lr == "/rtn/2/mix/lr"


# ----- knobs 2-8: reverb parameters ----------------------------------

def test_knobs_2_to_8_vintage_reverb_addresses(rig):
    enter_vrev(rig)
    expected = {2: 1, 3: 2, 4: 3, 5: 7, 6: 8, 7: 9, 8: 10}
    for knob, par in expected.items():
        rig.link.clear()
        start = rig.motor.last_ring(knob)
        rig.turn(knob, start + 1)
        addr = f"/fx/1/par/{par:02d}"
        assert rig.link.writes_to(addr) == [
            pytest.approx(vrev_params()[par] + 1 / 127)], knob
    labels = [rig.motor.scribble[k][0] for k in range(2, 9)]
    assert labels == ["PreDly", "Decay", "Mod", "LoCut", "HiCut",
                      "LoMult", "HiMult"]
    assert all(rig.motor.scribble[k][2] == CYAN for k in range(1, 9))


def test_knobs_2_to_8_hall_reverb_addresses(rig):
    rig.warm(fx_type=0, params=hall_params())
    rig.long_press()
    expected = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 7, 8: 8}
    for knob, par in expected.items():
        rig.link.clear()
        rig.turn(knob, rig.motor.last_ring(knob) - 2)
        assert rig.link.writes_to(f"/fx/1/par/{par:02d}") == [
            pytest.approx(0.5 - 2 / 127)], knob
    labels = [rig.motor.scribble[k][0] for k in range(2, 9)]
    assert labels == ["PreDly", "Decay", "Size", "Damping", "Diffuse",
                      "LoCut", "HiCut"]


def test_knob_map_comes_from_config():
    r = Rig(fx_overrides={})
    r.cfg.fx_mode.knob_maps[6] = [6, 5, 4, None, None, None, 1]
    r.warm(fx_type=6, params=vrev_params())
    r.long_press()
    r.link.clear()
    r.turn(2, r.motor.last_ring(2) + 1)
    assert r.link.writes_to("/fx/1/par/06")
    assert r.motor.scribble[2][0] == "Level"
    r.link.clear()
    r.turn(5, 50)
    r.turn(5, 52)
    assert r.link.writes == [], "unmapped knob does nothing"


def test_enum_param_steps_and_sends_int():
    r = Rig()
    r.cfg.fx_mode.knob_maps[6] = [4, 2, 3, 7, 8, 9, 10]   # knob 2 = Vintage
    r.warm(fx_type=6, params=vrev_params())
    r.long_press()
    r.link.clear()
    start = r.motor.last_ring(2)
    assert r.motor.scribble[2][:2] == ("Vintage", "OFF")
    r.turn(2, start + 2)
    assert r.link.writes_to("/fx/1/par/04") == [], "needs a few ticks"
    r.turn(2, start + 4)
    assert r.link.writes_to("/fx/1/par/04") == [1]
    assert isinstance(r.link.writes_to("/fx/1/par/04")[0], int)
    assert r.motor.scribble[2][1] == "ON"


def test_first_turn_after_a_counter_jump_does_not_jump_the_value(rig):
    enter_vrev(rig)
    rig.turn(2, 120)     # Extender counter far from the ring we wrote
    assert rig.link.writes == []
    rig.turn(2, 121)
    assert rig.link.writes_to("/fx/1/par/01") == [
        pytest.approx(vrev_params()[1] + 1 / 127)]


def test_ring_echo_is_ignored(rig):
    enter_vrev(rig)
    rig.turn(2, rig.motor.last_ring(2))   # Extender echoing our ring write
    assert rig.link.writes == []


# ----- entering: type and routing ------------------------------------

def test_entry_sets_default_type_over_non_reverb_and_logs_old(rig):
    old = {1: 0.25, 2: 0.75, 3: 0.125}
    rig.warm(fx_type=10, params=old)          # Stereo Delay
    rig.long_press()
    assert rig.link.writes_to("/fx/1/type") == [6]
    ring = logger.get_ring()
    ev = [e for e in ring if e["msg"].startswith("overwriting non-reverb")][-1]
    assert ev["data"]["old_type"] == "10:DLY"
    assert ev["data"]["old_params"] == {"01": 0.25, "02": 0.75, "03": 0.125}
    assert "/fx/1/par/10" in rig.link.queries, "new type's params re-read"
    assert rig.motor.scribble[1][0] == "VREV"
    assert rig.motor.scribble[2][:2] == ("PreDly", "--")


def test_entry_type_unknown_waits_for_mixer_then_logs(rig):
    rig.long_press()                          # cache empty
    assert "/fx/1/type" in rig.link.queries
    rig.mixer("/fx/1/type", 26)               # Modulation Delay
    rig.mixer("/fx/1/par/01", 0.4)
    assert rig.link.writes_to("/fx/1/type") == []
    rig.sched.fire(delay=fx.TYPE_CHECK_DELAY_S)
    assert rig.link.writes_to("/fx/1/type") == [6]
    ev = [e for e in logger.get_ring()
          if e["msg"].startswith("overwriting non-reverb")][-1]
    assert ev["data"]["old_params"] == {"01": 0.4}


def test_entry_keeps_vrev(rig):
    rig.warm(fx_type=6, params=vrev_params())
    rig.long_press()
    assert rig.link.writes_to("/fx/1/type") == []


def test_entry_keeps_other_reverb_by_default(rig):
    rig.warm(fx_type=5, params=hall_params())
    rig.long_press()
    assert rig.link.writes_to("/fx/1/type") == []
    assert rig.motor.scribble[1][0] == "PLAT"


def test_entry_replaces_other_reverb_when_configured():
    r = Rig(fx_overrides={"keep_current_reverb": False})
    r.warm(fx_type=5, params=hall_params())
    r.long_press()
    assert r.link.writes_to("/fx/1/type") == [6]


def test_entry_turns_return_on_and_routes_to_lr(rig):
    rig.warm(fx_type=6, params=vrev_params(), rtn_on=0, rtn_lr=0,
             rtn_fader=0.0, fxsend_on=0, fxsend_fader=0.0)
    rig.long_press()
    assert rig.link.writes_to("/rtn/1/mix/on") == [1]
    assert rig.link.writes_to("/rtn/1/mix/lr") == [1]
    assert rig.link.writes_to("/rtn/1/mix/fader") == [0.75]
    assert rig.link.writes_to("/fxsend/1/mix/on") == [1]
    assert rig.link.writes_to("/fxsend/1/mix/fader") == [0.75]


def test_entry_keeps_return_level_that_is_already_up(rig):
    rig.warm(fx_type=6, params=vrev_params(), rtn_fader=0.4,
             fxsend_fader=0.6)
    rig.long_press()
    for a in ("/rtn/1/mix/on", "/rtn/1/mix/lr", "/rtn/1/mix/fader",
              "/fxsend/1/mix/on", "/fxsend/1/mix/fader"):
        assert rig.link.writes_to(a) == [], a


def test_entry_route_values_arriving_later(rig):
    rig.mixer("/fx/1/type", 6)
    rig.long_press()
    assert "/rtn/1/mix/fader" in rig.link.queries
    rig.mixer("/rtn/1/mix/fader", 0.0)
    rig.mixer("/rtn/1/mix/on", 1)
    assert rig.link.writes_to("/rtn/1/mix/fader") == [0.75]
    assert rig.link.writes_to("/rtn/1/mix/on") == []
    rig.fx.exit()
    rig.link.clear()
    rig.mixer("/rtn/1/mix/lr", 0)        # after leaving: not touched
    assert rig.link.writes == []


def test_only_fx1_and_ch1_send_are_written(rig):
    rig.warm(fx_type=10, params={1: 0.1}, rtn_on=0, rtn_lr=0, rtn_fader=0.0,
             fxsend_on=0, fxsend_fader=0.0)
    rig.long_press()
    for k in range(1, 9):
        r = rig.motor.last_ring(k) or 0
        rig.turn(k, r + 1)
        rig.turn(k, r + 3)
    rig.push(8, True)
    rig.push(8, False)
    rig.long_press()
    allowed = ("/fx/1/", "/ch/01/mix/07/level", "/rtn/1/mix/",
               "/fxsend/1/mix/")
    assert rig.link.writes
    for a, _ in rig.link.writes:
        assert a.startswith(allowed), a
    assert rig.link.target_writes == []


# ----- knob 8: type stepping -------------------------------------------

def test_knob8_push_steps_type_and_remaps(rig):
    enter_vrev(rig)
    rig.push(8, True)
    rig.push(8, False)
    assert rig.link.writes_to("/fx/1/type") == [7]
    assert rig.fx.fx_type == 7
    assert "/fx/1/par/13" in rig.link.queries
    assert rig.motor.scribble[1][0] == "VRM"
    assert rig.motor.scribble[2][:2] == ("RevDly", "--")
    assert rig.motor.scribble[7][0] == "LoCut"
    # The mixer reports the new type's values; knobs follow the VRM map.
    for n in range(1, 14):
        rig.mixer(f"/fx/1/par/{n:02d}", 0.5 if n != 13 else 0)
    rig.link.clear()
    rig.turn(7, rig.motor.last_ring(7) + 1)
    assert rig.link.writes_to("/fx/1/par/09") == [pytest.approx(0.5 + 1 / 127)]


def test_type_stepping_wraps_through_all_reverbs(rig):
    rig.warm(fx_type=0, params=hall_params())
    rig.long_press()
    seen = [rig.fx.fx_type]
    for _ in range(10):
        rig.push(8, True)
        rig.push(8, False)
        seen.append(rig.fx.fx_type)
    assert seen == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0]


def test_knob8_push_outside_fx_mode_does_nothing(rig):
    rig.warm(fx_type=6)
    rig.link.clear()
    rig.push(8, True)
    rig.push(8, False)
    assert rig.link.writes == []


def test_knob8_stepping_can_be_switched_off():
    r = Rig(fx_overrides={"step_types_with_knob8": False})
    r.warm(fx_type=6, params=vrev_params())
    r.long_press()
    r.link.clear()
    r.push(8, True)
    assert r.link.writes_to("/fx/1/type") == []


# ----- mixer -> Extender sync while in FX mode ------------------------

def test_x_air_edit_changes_follow_on_rings_and_scribbles(rig):
    enter_vrev(rig)
    rig.mixer("/fx/1/par/02", 1.0)                 # Decay -> 4.5 s
    assert rig.motor.last_ring(3) == 127
    assert rig.motor.scribble[3][:2] == ("Decay", "4.50s")
    rig.mixer("/ch/01/mix/07/level", 0.75)         # send -> 0 dB
    assert rig.motor.last_ring(1) == 95
    assert rig.motor.scribble[1][1] == "+0.0dB"
    rig.mixer("/ch/01/mix/07", 0.25)               # short form too
    assert rig.motor.scribble[1][1] == "-30.0dB"
    # Next turn continues from the synced value, not the old one.
    rig.link.clear()
    rig.turn(3, rig.motor.last_ring(3) - 1)
    assert rig.link.writes_to("/fx/1/par/02") == [pytest.approx(1 - 1 / 127)]


def test_type_changed_in_x_air_edit_remaps(rig):
    enter_vrev(rig)
    rig.mixer("/fx/1/type", 0)
    assert rig.motor.scribble[1][0] == "HALL"
    assert rig.motor.scribble[5][0] == "Damping"
    assert rig.link.writes_to("/fx/1/type") == []


def test_trim_changes_do_not_take_over_rings_in_fx_mode(rig):
    enter_vrev(rig)
    rings = list(rig.motor.rings)
    rig.on_target("trim_1", 0.9)
    assert rig.motor.rings == rings


def test_scribble_updates_are_throttled():
    t = [100.0]
    r = Rig(clock=lambda: t[0])
    r.warm(fx_type=6, params=vrev_params())
    r.long_press()
    base = r.motor.scribble_writes
    r.mixer("/fx/1/par/01", 0.1)
    assert r.motor.scribble_writes == base, "too soon after entry paint"
    r.mixer("/fx/1/par/01", 0.15)
    assert len([h for h in r.sched.pending()
                if h.delay <= fx.SCRIBBLE_MIN_INTERVAL_S]) == 1
    r.sched.fire()
    assert r.motor.scribble_writes == base + 1
    assert r.motor.scribble[2][1] == fx.REVERB_TYPES[6].params[1].format(0.15)


# ----- leaving ---------------------------------------------------------

def test_leaving_restores_scribbles_rings_and_trim(rig):
    rig.on_target("trim_1", 0.4)
    rig.on_target("trim_2", 0.6)
    enter_vrev(rig)
    rig.turn(2, rig.motor.last_ring(2) + 5)
    vrev_written = rig.link.writes_to("/fx/1/par/01")
    rig.link.clear()
    rig.long_press()
    assert not rig.fx.active
    assert rig.motor.scribble[1] == ("Headmic", "   1", MAGENTA)
    assert rig.motor.scribble[8][0] == "MASTER"
    assert rig.motor.last_ring(1) == round(0.4 * 127)
    assert rig.motor.last_ring(2) == round(0.6 * 127)
    assert rig.motor.last_ring(8) == 0, "knob 8 has no trim"
    # Reverb settings are left as they are.
    assert rig.fx.params[1] == pytest.approx(vrev_written[-1])
    assert not [a for a, _ in rig.link.writes if a.startswith("/fx/")]
    # Knob 1 is trim again, absolute as before.
    rig.link.clear()
    rig.turn(1, 70)
    assert rig.link.target_writes == [("trim_1", pytest.approx(70 / 127))]
    assert rig.link.writes == []


def test_leaving_in_bus_mode_restores_bus_scribbles(rig):
    rig.event(StripButton(strip=2, button="select", pressed=True))
    assert rig.motor.scribble[1][:2] == ("CH 1", ">BUS2")
    enter_vrev(rig)
    rig.long_press()
    assert rig.motor.scribble[1][:2] == ("CH 1", ">BUS2")


def test_bus_mode_switch_while_in_fx_mode_keeps_fx_scribbles(rig):
    enter_vrev(rig)
    rig.event(StripButton(strip=3, button="select", pressed=True))
    assert rig.state.bus_mode == 3
    assert rig.motor.scribble[1][0] == "VREV"
    rig.long_press()
    assert rig.motor.scribble[1][:2] == ("CH 1", ">BUS3")


# ----- faders, mutes, mirror: same inside and outside FX mode -----------

def _surface_traffic(r):
    """Run fader, mirror and mute actions; return what reached the mixer
    and the Extender's motors/LEDs."""
    r.link.clear()
    r.motor.faders.clear()
    r.motor.leds.clear()
    r.event(FaderMove(strip=1, value=100))
    r.event(FaderMove(strip=8, value=90))          # lr + mirror to buses
    r.event(StripButton(strip=1, button="mute", pressed=True))
    r.event(StripButton(strip=8, button="mute", pressed=True))
    r.on_target(5, 0.3)                            # Edit moves ch 5
    r.on_target("mute_2", 0.0)                     # Edit mutes ch 2
    return (list(r.link.target_writes), list(r.link.writes),
            list(r.motor.faders), list(r.motor.leds))


def test_faders_mutes_mirror_unaffected_by_fx_mode():
    outside = Rig()
    outside.warm(fx_type=6, params=vrev_params())
    inside = Rig()
    inside.warm(fx_type=6, params=vrev_params())
    inside.long_press()
    assert inside.fx.active
    a = _surface_traffic(outside)
    b = _surface_traffic(inside)
    assert a == b
    targets = [t for t, _ in a[0]]
    assert targets == [1, "lr", "bus_1", "bus_2", "bus_3", "bus_4", "bus_5",
                       "bus_6", "mute_1", "mute_lr"]
    assert a[1] == [], "no raw FX writes from faders/mutes"
    assert (5, round(0.3 * 127)) in a[2]
    assert ("mute", 2, True) in a[3]
    inside.long_press()
    assert not inside.fx.active
    assert _surface_traffic(inside) == _surface_traffic(outside)


# ----- formatting and config ---------------------------------------

def test_value_scaling_and_units():
    vrev = fx.REVERB_TYPES[6].params
    assert vrev[1].format(0.0) == "0.0ms"
    assert vrev[1].format(1.0) == "120ms"
    assert vrev[2].format(0.0) == "0.30s"
    assert vrev[2].format(1.0) == "4.50s"
    assert vrev[2].value(0.5) == pytest.approx((0.3 * 4.5) ** 0.5)   # log
    assert vrev[8].format(1.0) == "20.0kHz"
    assert vrev[7].format(0.0) == "10Hz"
    assert vrev[6].format(0.5) == "+0.0dB"
    assert vrev[5].format(1) == "REAR"
    assert fx.level_to_db(0.75) == pytest.approx(0.0)
    assert fx.level_to_db(1.0) == pytest.approx(10.0)
    assert fx.format_level(0.0) == "-oo"
    for t in fx.REVERB_TYPES.values():
        for p in t.params.values():
            assert len(p.label) <= 7, p.label
            for v in ((0, 1) if p.scale == "enum" else (0.0, 0.37, 1.0)):
                assert len(p.format(v)) <= 7, (t.code, p.label, p.format(v))


def test_reverb_list_and_type_names():
    assert [fx.REVERB_TYPES[i].code for i in fx.REVERB_ORDER] == [
        "HALL", "AMBI", "RPLT", "ROOM", "CHAM", "PLAT", "VREV", "VRM",
        "GATE", "RVRS"]
    assert len(fx.ALL_TYPE_CODES) == 61
    assert fx.resolve_type("Vintage Reverb") == 6
    assert fx.resolve_type("vrev") == 6
    assert fx.resolve_type(6) == 6
    with pytest.raises(ValueError):
        fx.resolve_type(10)       # Stereo Delay is not a reverb


def test_config_validation():
    good = {"enabled": True, "default_type": "Hall Reverb",
            "knob_maps": {"VREV": [1, 2, 3, 4, 5, 6, 7]}}
    c = fx.load_config(good)
    assert c.default_type == 0 and c.knob_maps[6] == [1, 2, 3, 4, 5, 6, 7]
    assert c.knob_maps[0] == fx.DEFAULT_KNOB_MAPS[0]
    with pytest.raises(ValueError, match="no parameter 11"):
        fx.load_config({"knob_maps": {"VREV": [1, 2, 3, 4, 5, 6, 11]}})
    with pytest.raises(ValueError, match="7 parameter"):
        fx.load_config({"knob_maps": {"VREV": [1, 2]}})
    with pytest.raises(ValueError, match="fx_slot"):
        fx.load_config({"fx_slot": 5})
    with pytest.raises(ValueError, match="color"):
        fx.load_config({"color": "purple"})
    assert fx.load_config(None).enabled is False


def test_shipped_configs_load_with_fx_mode():
    from bridge.config import load
    from conftest import ROOT
    for name in ("config.example.yaml", "tests/fixtures/venue.yaml"):
        c = load(ROOT / name)
        assert c.fx_mode.enabled and c.fx_mode.default_type == 6
        used = {e.background for e in c.scribble.entries.values()}
        assert c.fx_mode.color not in used, name
