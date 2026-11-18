"""The two I/O edges FX mode added to: XR18Link's raw listener and
address writes, and the Extender's ring CC."""

from __future__ import annotations

import mido
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_message import OscMessage

from bridge import extender
from bridge.decoder import decode, KnobPush, KnobTurn
from bridge.xr18 import XR18Link


def _dgram(address, *args):
    b = OscMessageBuilder(address=address)
    for a in args:
        b.add_arg(a)
    return b.build().dgram


class _Sock:
    def __init__(self):
        self.sent = []

    def sendto(self, dgram, dest):
        self.sent.append((dgram, dest))


def test_listener_gets_raw_int_type_and_target_path_unchanged():
    link = XR18Link("10.0.0.2", 10024, 10024)
    heard, targets = [], []
    link.add_listener(lambda a, args: heard.append((a, args)))
    link.set_target_callback(lambda t, v: targets.append((t, v)))
    link._dispatch_mixer_packet(_dgram("/fx/1/type", 6))
    link._dispatch_mixer_packet(_dgram("/ch/01/mix/fader", 0.5))
    link._dispatch_mixer_packet(_dgram("/ch/01/mix/07/level", 0.25))
    assert heard == [("/fx/1/type", (6,)), ("/ch/01/mix/fader", (0.5,)),
                     ("/ch/01/mix/07/level", (0.25,))]
    assert targets == [(1, 0.5)], "FX send is not a bus-send target"


def test_send_address_keeps_int_and_float_types_and_fans_out():
    link = XR18Link("10.0.0.2", 10024, 10024)
    link._sock = _Sock()
    link._auto_clients[("10.0.0.9", 5555)] = 0.0
    link.send_address("/fx/1/type", 6)
    link.send_address("/ch/01/mix/07/level", 0.5)
    link.query_address("/fx/1/par/01")
    msgs = [(OscMessage(d).address, OscMessage(d).params, dest)
            for d, dest in link._sock.sent]
    assert msgs == [
        ("/fx/1/type", [6], ("10.0.0.2", 10024)),
        ("/fx/1/type", [6], ("10.0.0.9", 5555)),
        ("/ch/01/mix/07/level", [0.5], ("10.0.0.2", 10024)),
        ("/ch/01/mix/07/level", [0.5], ("10.0.0.9", 5555)),
        ("/fx/1/par/01", [], ("10.0.0.2", 10024)),
    ]
    assert b",i" in link._sock.sent[0][0]
    assert b",f" in link._sock.sent[2][0]


class _Port:
    def __init__(self):
        self.msgs = []

    def send(self, m):
        self.msgs.append(m)


def test_ring_goes_out_on_encoder_cc_absolute():
    port = _Port()
    m = extender.MotorOutput(port)
    m.send_ring(1, 100)
    m.send_ring(8, 0)
    m.send_ring(3, 200)
    assert [(x.control, x.value) for x in port.msgs] == [(80, 100), (87, 0),
                                                          (82, 127)]


def test_knob_push_and_turn_decode():
    assert decode(mido.Message("note_on", note=0, velocity=127)) == \
        KnobPush(strip=1, pressed=True)
    assert decode(mido.Message("note_off", note=0)) == \
        KnobPush(strip=1, pressed=False)
    assert decode(mido.Message("note_on", note=7, velocity=0)) == \
        KnobPush(strip=8, pressed=False)
    assert decode(mido.Message("control_change", control=80, value=33)) == \
        KnobTurn(strip=1, value=33)
