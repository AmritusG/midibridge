"""Extender MIDI port management, motor faders, LED rings, scribble strips.

CONTROL_MAP (output / feedback side)
====================================
  Motor fader     CC 70..77 on MIDI ch 1, value 0..127  -> strip 1..8
  Encoder ring    CC 48..55 on MIDI ch 1, value encoded -> strip 1..8

Encoder ring value (Mackie Control standard):
  Top nibble (bits 4..6): display mode
    0 = single dot   (single segment lit at position)
    1 = boost/cut    (segments from center outward)
    2 = wrap         (segments from left)
    3 = spread       (segments from center symmetrically)
  Bottom nibble (bits 0..3): position 0..11 (segment 0 = off, 1..11 lit)
  Bit 6 toggles the centre LED (used in some modes; we leave it 0).

We use mode 0 ("single dot") so each ring shows a single lit segment
corresponding to the current value. Position 0 = ring fully off.
"""

from __future__ import annotations

import threading
from typing import Optional

import mido

from . import logger


_CC_FADER_BASE = 70
_CC_RING_BASE  = 48


# ----- Port discovery & open -------------------------------------------

def find_input_port(name_fragment: str) -> Optional[str]:
    for name in mido.get_input_names():
        if name_fragment.lower() in name.lower():
            return name
    return None


def find_output_port(name_fragment: str) -> Optional[str]:
    for name in mido.get_output_names():
        if name_fragment.lower() in name.lower():
            return name
    return None


def list_inputs() -> list[str]:
    return list(mido.get_input_names())


def list_outputs() -> list[str]:
    return list(mido.get_output_names())


def open_input(name_fragment: str) -> mido.ports.BaseInput:
    full = find_input_port(name_fragment)
    if full is None:
        available = list_inputs()
        msg = (
            f"No MIDI input port matching '{name_fragment}'. "
            f"Available: {available!r}"
        )
        logger.error("midi", msg)
        raise RuntimeError(msg)
    logger.info("midi", "opening input port", port=full)
    return mido.open_input(full)


def open_output(name_fragment: str) -> mido.ports.BaseOutput:
    full = find_output_port(name_fragment)
    if full is None:
        available = list_outputs()
        msg = (
            f"No MIDI output port matching '{name_fragment}'. "
            f"Available: {available!r}"
        )
        logger.error("midi", msg)
        raise RuntimeError(msg)
    logger.info("midi", "opening output port", port=full)
    return mido.open_output(full)


# ----- Motor fader + ring output ---------------------------------------

def _position_to_ring_value(midi_value: int) -> int:
    """Map 0..127 to MCU single-dot ring encoding.

    Mode 0 (single dot), position 0..11 in low nibble. Position 0
    means ring off; positions 1..11 map to the 11 lit segments.
    """
    if midi_value <= 0:
        return 0  # ring off
    # 1..127 -> 1..11
    pos = 1 + int(midi_value * 10 / 127)
    if pos > 11:
        pos = 11
    return pos  # mode bits = 0


class MotorOutput:
    """Thread-safe writer for motor-fader CCs, LED-ring CCs, and SysEx."""

    def __init__(self, port: mido.ports.BaseOutput):
        self._port = port
        self._lock = threading.Lock()

    def send_fader(self, strip: int, midi_value: int) -> None:
        """Drive motor fader on strip 1..8 to MIDI value 0..127."""
        if not 1 <= strip <= 8:
            return
        midi_value = max(0, min(127, midi_value))
        cc = _CC_FADER_BASE + (strip - 1)
        msg = mido.Message(
            "control_change", channel=0, control=cc, value=midi_value
        )
        with self._lock:
            try:
                self._port.send(msg)
            except Exception as e:
                logger.warn("midi", "motor fader send failed",
                            strip=strip, err=str(e))

    def send_ring(self, knob: int, midi_value: int) -> None:
        """Drive the LED ring around encoder `knob` (1..8) for value 0..127."""
        if not 1 <= knob <= 8:
            return
        midi_value = max(0, min(127, midi_value))
        cc = _CC_RING_BASE + (knob - 1)
        value = _position_to_ring_value(midi_value)
        msg = mido.Message(
            "control_change", channel=0, control=cc, value=value
        )
        with self._lock:
            try:
                self._port.send(msg)
            except Exception as e:
                logger.warn("midi", "ring send failed", knob=knob, err=str(e))

    def send_button_led(self, button: str, strip: int, on: bool) -> None:
        """Light or unlight a strip button LED (rec/solo/mute/select).

        The X-Touch family lights button LEDs by responding to the same
        note number used for the press event: Note On vel=127 = lit,
        Note Off (or Note On vel=0) = unlit.
        """
        if not 1 <= strip <= 8:
            return
        bases = {"rec": 8, "solo": 16, "mute": 24, "select": 32}
        if button not in bases:
            return
        note = bases[button] + (strip - 1)
        velocity = 127 if on else 0
        msg = mido.Message("note_on", channel=0, note=note, velocity=velocity)
        with self._lock:
            try:
                self._port.send(msg)
            except Exception as e:
                logger.warn("midi", "button LED send failed",
                            button=button, strip=strip, err=str(e))

    def send_sysex(self, payload: bytes) -> None:
        """Send a SysEx message (raw bytes excluding F0/F7 framing)."""
        msg = mido.Message("sysex", data=tuple(payload))
        with self._lock:
            try:
                self._port.send(msg)
            except Exception as e:
                logger.warn("midi", "sysex send failed", err=str(e))

    def close(self) -> None:
        with self._lock:
            try:
                self._port.close()
            except Exception:
                pass


# ----- Scribble strips (SysEx) ----------------------------------------
#
# Behringer X-Touch Extender scribble strip protocol (23 bytes total):
#
#   F0  00 20 32  15  4C  <track 0..7>  <colors>  <7 top>  <7 bot>  F7
#
# Where:
#   00 20 32  = Behringer manufacturer ID (NOT Mackie's 00 00 66 -- this
#               is the single most important detail; the Extender ignores
#               anything sent with the Mackie manufacturer ID in MC mode)
#   15        = X-Touch Extender device ID (14 = full X-Touch)
#   4C        = scribble command (constant)
#   <track>   = 0..7, zero-indexed (legend on device is 1-indexed)
#   <colors>  = bit-packed 0b00LU0BBB:
#                 L = lower row text (0=light, 1=dark)
#                 U = upper row text (0=light, 1=dark)
#                 BBB = background (0=black 1=red 2=green 3=yellow
#                                   4=blue 5=magenta 6=cyan 7=white)
#   <7 top>   = top row ASCII, exactly 7 chars (pad with 0x20 spaces)
#   <7 bot>   = bottom row ASCII, exactly 7 chars
#
# Confirmed working on Extender firmware 1.25 in MC mode.
# Source: Aldaviva/BehringerXTouchExtender wiki, byte-for-byte verified
# in our hardware probe.
#
# Black background is unreadable -- the LCD looks broken when set to it.
# Default to white background with dark text for legibility.

SCRIBBLE_BLACK   = 0
SCRIBBLE_RED     = 1
SCRIBBLE_GREEN   = 2
SCRIBBLE_YELLOW  = 3
SCRIBBLE_BLUE    = 4
SCRIBBLE_MAGENTA = 5
SCRIBBLE_CYAN    = 6
SCRIBBLE_WHITE   = 7

SCRIBBLE_COLOR_NAMES = {
    "black":   SCRIBBLE_BLACK,
    "red":     SCRIBBLE_RED,
    "green":   SCRIBBLE_GREEN,
    "yellow":  SCRIBBLE_YELLOW,
    "blue":    SCRIBBLE_BLUE,
    "magenta": SCRIBBLE_MAGENTA,
    "cyan":    SCRIBBLE_CYAN,
    "white":   SCRIBBLE_WHITE,
}

TEXT_LIGHT = 0
TEXT_DARK  = 1


def encode_scribble(motor: MotorOutput,
                    strip: int,
                    top: str = "",
                    bottom: str = "",
                    background: int = SCRIBBLE_WHITE,
                    top_text: int = TEXT_DARK,
                    bottom_text: int = TEXT_DARK) -> None:
    """Set scribble strip text + colors for `strip` (1..8).

    Each row is truncated/padded to 7 ASCII characters. Non-ASCII
    characters are replaced with '?'. background defaults to white with
    dark text (most legible).
    """
    if not 1 <= strip <= 8:
        return
    track = strip - 1  # convert 1-indexed UI to 0-indexed wire format
    background = max(0, min(7, background))
    top_text = 1 if top_text else 0
    bottom_text = 1 if bottom_text else 0
    colors = (background & 0b111) | (top_text << 4) | (bottom_text << 5)

    def _pad(s: str) -> bytes:
        b = s.encode("ascii", errors="replace")[:7]
        return b + b" " * (7 - len(b))

    payload = (
        bytes([0x00, 0x20, 0x32, 0x15, 0x4C, track, colors])
        + _pad(top) + _pad(bottom)
    )
    motor.send_sysex(payload)
