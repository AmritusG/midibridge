"""Test fakes: an Extender that records what the bridge drives, an XR18
link that records OSC traffic, and a scheduler the tests fire by hand.

bridge.py (the entry script) shares its name with the bridge/ package, so
it is loaded here as the module `bridge_main`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import logger  # noqa: E402
from bridge.config import load  # noqa: E402
from bridge.fx import FxMode  # noqa: E402

_spec = importlib.util.spec_from_file_location("bridge_main", ROOT / "bridge.py")
bridge_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge_main)

logger.setup("WARNING", 2000)


# ----- fakes -----------------------------------------------------------

class FakeExtender:
    """Stands in for extender.MotorOutput. Decodes scribble SysEx back
    into (top, bottom, colour) per strip."""

    def __init__(self):
        self.faders: list[tuple[int, int]] = []
        self.rings: list[tuple[int, int]] = []
        self.leds: list[tuple[str, int, bool]] = []
        self.scribble: dict[int, tuple[str, str, int]] = {}
        self.scribble_writes = 0

    def send_fader(self, strip, midi):
        self.faders.append((strip, midi))

    def send_ring(self, knob, midi):
        self.rings.append((knob, midi))

    def send_button_led(self, button, strip, on):
        self.leds.append((button, strip, on))

    def send_sysex(self, payload: bytes):
        assert payload[:5] == bytes([0x00, 0x20, 0x32, 0x15, 0x4C])
        strip = payload[5] + 1
        colour = payload[6] & 0b111
        top = payload[7:14].decode().rstrip()
        bottom = payload[14:21].decode().rstrip()
        self.scribble[strip] = (top, bottom, colour)
        self.scribble_writes += 1

    def last_ring(self, knob):
        for k, v in reversed(self.rings):
            if k == knob:
                return v
        return None


class FakeLink:
    """Stands in for XR18Link: records writes and queries."""

    def __init__(self):
        self.target_writes: list[tuple] = []
        self.writes: list[tuple[str, object]] = []
        self.queries: list[str] = []
        self.target_queries: list = []
        self.listeners = []

    def send_target(self, target, value):
        self.target_writes.append((target, value))

    def query_target(self, target):
        self.target_queries.append(target)

    def send_address(self, address, value):
        self.writes.append((address, value))

    def query_address(self, address):
        self.queries.append(address)

    def add_listener(self, fn):
        self.listeners.append(fn)

    def clear(self):
        self.target_writes.clear()
        self.writes.clear()
        self.queries.clear()
        self.target_queries.clear()

    def writes_to(self, address):
        return [v for a, v in self.writes if a == address]


class _Handle:
    def __init__(self, delay, fn):
        self.delay = delay
        self.fn = fn
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeScheduler:
    def __init__(self):
        self.handles: list[_Handle] = []

    def __call__(self, delay, fn):
        h = _Handle(delay, fn)
        self.handles.append(h)
        return h

    def pending(self):
        return [h for h in self.handles if not h.cancelled]

    def fire(self, delay=None):
        """Run every pending callback (optionally only those scheduled
        with this delay)."""
        due = [h for h in self.pending()
               if delay is None or abs(h.delay - delay) < 1e-9]
        for h in due:
            h.cancelled = True
            h.fn()
        return len(due)


class TickClock:
    """Advances 1 s per read, so the scribble throttle never kicks in
    unless a test uses a manual clock."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 1.0
        return self.t


# ----- the rig -----------------------------------------------------------

CONFIG_YAML = ROOT / "tests" / "fixtures" / "venue.yaml"


class Rig:
    """bridge.py's handlers wired the way main() wires them, around the
    fakes."""

    def __init__(self, cfg_path=CONFIG_YAML, setup_mode=True,
                 fx_overrides=None, clock=None):
        self.cfg = load(cfg_path)
        if fx_overrides:
            for k, v in fx_overrides.items():
                setattr(self.cfg.fx_mode, k, v)
        self.setup_mode = setup_mode
        self.motor = FakeExtender()
        self.link = FakeLink()
        self.sched = FakeScheduler()
        cfg = self.cfg
        self.state = bridge_main.SyncState(
            cfg.fader_map, cfg.knob_map, cfg.mute_map,
            lock_trim_in_operation_mode=cfg.lock_trim_in_operation_mode,
            mirror_map=cfg.mirror_map)
        self.fx = FxMode(
            cfg.fx_mode, self.link,
            ring_writer=lambda k, m: bridge_main._send_ring(
                self.state, self.motor, k, m),
            scribble_writer=lambda *a: bridge_main._write_scribble(
                self.motor, *a),
            restore=lambda: bridge_main.restore_after_fx(
                self.state, self.motor, cfg.scribble, self.link),
            scheduler=self.sched,
            clock=clock or TickClock(),
        )
        self.on_target = bridge_main.make_xr18_callback(
            self.state, self.motor, self.fx)
        bridge_main.apply_scribble(cfg, self.motor)

    # Extender -> bridge
    def event(self, ev):
        bridge_main.handle_midi_event(
            ev, self.state, self.setup_mode, self.link, self.motor,
            scribble_cfg=self.cfg.scribble, fx=self.fx)

    def push(self, strip, pressed):
        from bridge.decoder import KnobPush
        self.event(KnobPush(strip=strip, pressed=pressed))

    def turn(self, strip, value):
        from bridge.decoder import KnobTurn
        self.event(KnobTurn(strip=strip, value=value))

    def long_press(self, strip=1):
        self.push(strip, True)
        fired = self.sched.fire(delay=self.cfg.fx_mode.hold_s)
        self.push(strip, False)
        return fired

    # mixer -> bridge
    def mixer(self, address, *args):
        self.fx.on_osc(address, args)

    def warm(self, fx_type=6, params=None, send=0.5, rtn_on=1, rtn_lr=1,
             rtn_fader=0.75, fxsend_on=1, fxsend_fader=0.75):
        """Simulate the startup query replies."""
        self.mixer("/fx/1/type", fx_type)
        self.mixer("/fx/1/insert", 0)
        for n, v in (params or {}).items():
            self.mixer(f"/fx/1/par/{n:02d}", v)
        self.mixer("/ch/01/mix/07/level", send)
        self.mixer("/rtn/1/mix/on", rtn_on)
        self.mixer("/rtn/1/mix/lr", rtn_lr)
        self.mixer("/rtn/1/mix/fader", rtn_fader)
        self.mixer("/fxsend/1/mix/on", fxsend_on)
        self.mixer("/fxsend/1/mix/fader", fxsend_fader)


def vrev_params():
    return {1: 0.2, 2: 0.5, 3: 0.1, 4: 0, 5: 1, 6: 0.5, 7: 0.3, 8: 0.8,
            9: 0.5, 10: 0.9}


def hall_params():
    return {n: 0.5 for n in range(1, 13)}


@pytest.fixture
def rig():
    return Rig()
