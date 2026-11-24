"""End to end over real UDP: the real XR18Link against a fake XR18 on
localhost that answers queries from a parameter table and records
writes, with FX mode driving it."""

from __future__ import annotations

import socket
import threading
import time

from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder

from bridge.fx import FxMode, FxModeConfig
from bridge.xr18 import XR18Link


class FakeXR18:
    def __init__(self, table):
        self.table = dict(table)
        self.writes = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                dgram, src = self.sock.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            msg = OscMessage(dgram)
            if msg.address == "/xremote":
                continue
            if msg.params:
                self.table[msg.address] = msg.params[0]
                self.writes.append((msg.address, msg.params[0], dgram))
            elif msg.address in self.table:
                b = OscMessageBuilder(address=msg.address)
                b.add_arg(self.table[msg.address])
                self.sock.sendto(b.build().dgram, src)

    def close(self):
        self._stop.set()
        self._t.join(1)
        self.sock.close()


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_fx_mode_against_fake_xr18_over_udp():
    table = {"/fx/1/type": 10, "/fx/1/insert": 0, "/ch/01/mix/07/level": 0.5,
             "/rtn/1/mix/on": 0, "/rtn/1/mix/lr": 1, "/rtn/1/mix/fader": 0.0,
             "/fxsend/1/mix/on": 1, "/fxsend/1/mix/fader": 0.75}
    table.update({f"/fx/1/par/{n:02d}": 0.25 for n in range(1, 16)})
    mixer = FakeXR18(table)
    link = XR18Link("127.0.0.1", mixer.port, 0)
    rings = {}
    fxm = FxMode(FxModeConfig(enabled=True), link,
                 ring_writer=lambda k, m: rings.__setitem__(k, m),
                 scribble_writer=lambda *a: None, restore=lambda: None)
    link.add_listener(fxm.on_osc)
    link.start()
    try:
        fxm.startup_queries()
        assert _wait(lambda: fxm.fx_type == 10 and len(fxm.params) >= 15
                     and len(fxm.route) == 5)
        fxm.enter()
        # Non-reverb overwritten with Vintage Reverb; return switched on and
        # raised from -oo; params re-read for the new type.
        assert _wait(lambda: mixer.table["/fx/1/type"] == 6)
        assert _wait(lambda: mixer.table["/rtn/1/mix/on"] == 1)
        assert mixer.table["/rtn/1/mix/fader"] == 0.75
        assert _wait(lambda: fxm.params.get(2) is not None)
        assert _wait(lambda: 1 in rings and 2 in rings)

        fxm.on_knob_turn(1, rings[1] + 2)
        fxm.on_knob_turn(2, rings[2] + 3)
        assert _wait(lambda: any(a == "/fx/1/par/01" for a, *_ in mixer.writes))
        send = [w for w in mixer.writes if w[0] == "/ch/01/mix/07/level"]
        par = [w for w in mixer.writes if w[0] == "/fx/1/par/01"]
        typ = [w for w in mixer.writes if w[0] == "/fx/1/type"]
        assert abs(send[-1][1] - (0.5 + 2 / 127)) < 1e-6
        assert b",f" in send[-1][2]
        assert abs(par[-1][1] - (0.25 + 3 / 127)) < 1e-6
        assert typ[-1][1] == 6 and b",i" in typ[-1][2]
        touched = {a for a, *_ in mixer.writes}
        assert touched <= {"/fx/1/type", "/fx/1/par/01", "/ch/01/mix/07/level",
                           "/rtn/1/mix/on", "/rtn/1/mix/fader"}
    finally:
        link.stop()
        mixer.close()


def test_hold_with_real_timer_thread():
    """The production scheduler (threading.Timer) toggles after hold_s,
    and a release before hold_s cancels it."""
    from conftest import FakeLink
    fxm = FxMode(FxModeConfig(enabled=True, hold_s=0.2), FakeLink(),
                 ring_writer=lambda *a: None, scribble_writer=lambda *a: None,
                 restore=lambda: None)
    fxm.on_knob_push(1, True)
    time.sleep(0.05)
    fxm.on_knob_push(1, False)
    time.sleep(0.3)
    assert not fxm.active
    fxm.on_knob_push(1, True)
    assert _wait(lambda: fxm.active, 1.0)
    fxm.on_knob_push(1, False)


def test_eq_mode_against_fake_xr18_over_udp():
    """The venue XR18's ch 1 EQ (band 1 LCut, band 4 HCut) over real UDP:
    shelves set as ints, a gain turn sent as a float, reset sends 0.5."""
    from bridge.eq import EqMode, EqModeConfig
    bands = {1: (0, 0.30), 2: (2, 0.465), 3: (2, 0.665), 4: (5, 0.95)}
    table = {"/ch/01/eq/on": 1, "/ch/01/preamp/hpon": 1,
             "/ch/01/preamp/hpf": 0.0}
    for b, (t, f) in bands.items():
        table.update({f"/ch/01/eq/{b}/type": t, f"/ch/01/eq/{b}/f": f,
                      f"/ch/01/eq/{b}/g": 0.5, f"/ch/01/eq/{b}/q": 33 / 71})
    mixer = FakeXR18(table)
    link = XR18Link("127.0.0.1", mixer.port, 0)
    rings = {}
    eqm = EqMode(EqModeConfig(enabled=True), link,
                 ring_writer=lambda k, m: rings.__setitem__(k, m),
                 scribble_writer=lambda *a: None, restore=lambda: None)
    link.add_listener(eqm.on_osc)
    link.start()
    try:
        eqm.startup_queries()
        assert _wait(lambda: len(eqm.vals) == len(eqm.addresses))
        eqm.enter()
        assert _wait(lambda: mixer.table["/ch/01/eq/1/type"] == 1
                     and mixer.table["/ch/01/eq/4/type"] == 4)
        eqm.on_knob_turn(6, rings[6] + 1)          # high gain +0.5 dB
        eqm.on_knob_push(5, True)                  # reset high band
        eqm.on_knob_push(8, True)                  # EQ off
        assert _wait(lambda: mixer.table["/ch/01/eq/on"] == 0)
        w = {a: (v, d) for a, v, d in mixer.writes}
        assert b",i" in w["/ch/01/eq/1/type"][1]
        assert b",i" in w["/ch/01/eq/on"][1]
        g = [(v, d) for a, v, d in mixer.writes if a == "/ch/01/eq/4/g"]
        assert abs(g[0][0] - (0.5 + 2 / 120)) < 1e-6 and b",f" in g[0][1]
        assert g[-1][0] == 0.5
        assert {a for a, *_ in mixer.writes} == {
            "/ch/01/eq/1/type", "/ch/01/eq/4/type", "/ch/01/eq/4/g",
            "/ch/01/eq/on"}
    finally:
        link.stop()
        mixer.close()
