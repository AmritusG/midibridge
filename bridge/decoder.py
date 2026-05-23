"""Decode raw MIDI messages from the X-Touch Extender (MC mode).

This module owns the *only* place where raw CC/note numbers are
interpreted. Everything downstream sees named events. Per the
project's standing rule on explicit index management, the maps are
documented in CONTROL_MAP below.

CONTROL_MAP — observed from the Extender in MC mode
====================================================

  Strip 1..8 are channel strips, left-to-right on the device.

  Faders:
    Fader move        CC 70..77 on MIDI ch 1  -> strip 1..8
    Fader touch       Note 110..117 on MIDI ch 1 -> strip 1..8 (on/off)

  Encoders (knobs):
    Knob turn         CC 80..87 on MIDI ch 1  -> strip 1..8
    Knob push         Note 0..7 on MIDI ch 1  -> strip 1..8 (on/off)

  Channel strip buttons (per strip, vertically REC/SOLO/MUTE/SELECT):
    REC               Note 8..15  on MIDI ch 1 -> strip 1..8
    SOLO              Note 16..23 on MIDI ch 1 -> strip 1..8
    MUTE              Note 24..31 on MIDI ch 1 -> strip 1..8
    SELECT            Note 32..39 on MIDI ch 1 -> strip 1..8

  All values 0..127. Velocity 127 = press, 0 = release (or note_off).

Anything outside this table arrives as UnknownEvent and is logged
once with the raw message for diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

import mido


# ----- Event types ------------------------------------------------------

@dataclass(frozen=True)
class FaderMove:
    strip: int          # 1..8
    value: int          # 0..127


@dataclass(frozen=True)
class FaderTouch:
    strip: int          # 1..8
    pressed: bool


@dataclass(frozen=True)
class KnobTurn:
    strip: int          # 1..8
    value: int          # 0..127


@dataclass(frozen=True)
class KnobPush:
    strip: int          # 1..8
    pressed: bool


@dataclass(frozen=True)
class StripButton:
    strip: int          # 1..8
    button: str         # "rec" | "solo" | "mute" | "select"
    pressed: bool


@dataclass(frozen=True)
class UnknownEvent:
    raw: str            # repr of the mido message


Event = Union[FaderMove, FaderTouch, KnobTurn, KnobPush, StripButton, UnknownEvent]


# ----- The index map (single source of truth) ---------------------------

# CC ranges: (start_cc, count, kind)
_CC_FADER_BASE = 70    # 70..77 = strips 1..8
_CC_KNOB_BASE = 80     # 80..87 = strips 1..8

# Note ranges: (start_note, count, kind)
_NOTE_KNOB_PUSH_BASE = 0     # 0..7   = strips 1..8
_NOTE_REC_BASE       = 8     # 8..15
_NOTE_SOLO_BASE      = 16    # 16..23
_NOTE_MUTE_BASE      = 24    # 24..31
_NOTE_SELECT_BASE    = 32    # 32..39
_NOTE_FADER_TOUCH_BASE = 110 # 110..117


def decode(msg: mido.Message) -> Optional[Event]:
    """Map a raw mido message to a named Event, or None to drop silently."""

    if msg.type == "control_change":
        cc = msg.control
        val = msg.value
        if _CC_FADER_BASE <= cc <= _CC_FADER_BASE + 7:
            return FaderMove(strip=cc - _CC_FADER_BASE + 1, value=val)
        if _CC_KNOB_BASE <= cc <= _CC_KNOB_BASE + 7:
            return KnobTurn(strip=cc - _CC_KNOB_BASE + 1, value=val)
        return UnknownEvent(raw=str(msg))

    if msg.type in ("note_on", "note_off"):
        n = msg.note
        # In MC mode the Extender sometimes sends note_off, and sometimes
        # note_on with velocity 0 for release. Normalise both.
        pressed = (msg.type == "note_on" and msg.velocity > 0)

        if _NOTE_KNOB_PUSH_BASE <= n <= _NOTE_KNOB_PUSH_BASE + 7:
            return KnobPush(strip=n - _NOTE_KNOB_PUSH_BASE + 1, pressed=pressed)
        if _NOTE_REC_BASE <= n <= _NOTE_REC_BASE + 7:
            return StripButton(strip=n - _NOTE_REC_BASE + 1, button="rec", pressed=pressed)
        if _NOTE_SOLO_BASE <= n <= _NOTE_SOLO_BASE + 7:
            return StripButton(strip=n - _NOTE_SOLO_BASE + 1, button="solo", pressed=pressed)
        if _NOTE_MUTE_BASE <= n <= _NOTE_MUTE_BASE + 7:
            return StripButton(strip=n - _NOTE_MUTE_BASE + 1, button="mute", pressed=pressed)
        if _NOTE_SELECT_BASE <= n <= _NOTE_SELECT_BASE + 7:
            return StripButton(strip=n - _NOTE_SELECT_BASE + 1, button="select", pressed=pressed)
        if _NOTE_FADER_TOUCH_BASE <= n <= _NOTE_FADER_TOUCH_BASE + 7:
            return FaderTouch(strip=n - _NOTE_FADER_TOUCH_BASE + 1, pressed=pressed)
        return UnknownEvent(raw=str(msg))

    # Pitchbend, aftertouch, sysex, clock etc. -- not used by the Extender
    # in MC mode for the controls we care about.
    return UnknownEvent(raw=str(msg))
