"""Shared machinery for the "hold a knob, the 8 knobs become an editor"
modes (FX mode, EQ mode): the hold timer, relative encoder turns, ring
and scribble painting with a write throttle, and leaving cleanly.

A subclass supplies:
  _on_enter()            mixer-side work on entry (called with the lock held)
  _on_exit()             optional cleanup (lock held)
  _strip_view(strip)     -> (top, bottom, ring 0..127 or None)
and handles its own knob turns, pushes and mixer messages.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

# Encoder deltas bigger than this are treated as a counter resync (e.g.
# the Extender's counter was moved by something else), not a user turn.
MAX_DELTA = 16
# Minimum spacing of scribble SysEx writes per strip while values stream.
SCRIBBLE_MIN_INTERVAL_S = 0.05


def threading_scheduler(delay: float, fn: Callable[[], None]):
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()
    return t


class KnobEditor:
    """Thread-safe: called from the MIDI thread (knobs), the OSC thread
    (mixer echoes) and timer threads."""

    kind = "editor"          # log kind / name, set by subclasses

    def __init__(self, link,
                 ring_writer: Callable[[int, int], None],
                 scribble_writer: Callable[..., None],
                 restore: Callable[[], None],
                 scheduler: Callable = threading_scheduler,
                 clock: Callable[[], float] = time.monotonic,
                 before_enter: Optional[Callable[[], None]] = None):
        self.link = link
        self._ring_writer = ring_writer
        self._scribble_writer = scribble_writer
        self._restore = restore
        self._schedule = scheduler
        self._clock = clock
        # Called (without our lock) right before entering, so another
        # editor mode can leave first. Set after construction if the two
        # modes refer to each other.
        self.before_enter = before_enter
        self._lock = threading.RLock()
        self.active = False
        self._last_raw: dict[int, Optional[int]] = {}
        self._hold_handle = None
        self._shown: dict[int, tuple] = {}
        self._scribble_sent_at: dict[int, float] = {}
        self._scribble_timer: dict[int, object] = {}

    # ----- subclass hooks / settings ------------------------------------

    @property
    def hold_s(self) -> float:
        raise NotImplementedError

    def _colours(self, strip: int) -> tuple:
        """(background name, top text shade, bottom text shade)."""
        raise NotImplementedError

    def _on_enter(self) -> None:
        pass

    def _on_exit(self) -> None:
        pass

    def _strip_view(self, strip: int):
        raise NotImplementedError

    # ----- hold ---------------------------------------------------------

    def _hold_push(self, pressed: bool) -> None:
        """The hold knob went down or up: arm or cancel the toggle."""
        self._cancel_hold()
        if pressed:
            self._hold_handle = self._schedule(self.hold_s, self._hold_fired)

    def _cancel_hold(self) -> None:
        if self._hold_handle is not None:
            try:
                self._hold_handle.cancel()
            except Exception:
                pass
            self._hold_handle = None

    def _hold_fired(self) -> None:
        with self._lock:
            self._hold_handle = None
            was_active = self.active
        if was_active:
            self.exit()
        else:
            self.enter()

    # ----- enter / exit -------------------------------------------------

    def enter(self) -> None:
        if self.active:
            return
        if self.before_enter is not None:
            self.before_enter()
        with self._lock:
            if self.active:
                return
            self.active = True
            self._last_raw = {}
            self._shown = {}
            self._on_enter()
            self._paint_all(force=True)

    def exit(self, restore: bool = True) -> None:
        """Leave the mode. restore=False when another editor mode is about
        to paint the surface anyway (switching modes)."""
        with self._lock:
            if not self.active:
                return
            self.active = False
            for t in self._scribble_timer.values():
                try:
                    t.cancel()
                except Exception:
                    pass
            self._scribble_timer = {}
            self._shown = {}
            self._on_exit()
        if restore:
            self._restore()

    # ----- relative turns -----------------------------------------------

    def _delta(self, knob: int, raw: int) -> int:
        last = self._last_raw.get(knob)
        self._last_raw[knob] = raw
        if last is None:
            return 0
        d = raw - last
        return 0 if abs(d) > MAX_DELTA else d

    # ----- display ------------------------------------------------------

    def _paint_all(self, force: bool = False) -> None:
        for s in range(1, 9):
            self._paint_strip(s, force=force)

    def _paint_strip(self, strip: int, ring: bool = True,
                     force: bool = False) -> None:
        top, bottom, ring_val = self._strip_view(strip)
        if ring and ring_val is not None:
            self._ring_writer(strip, ring_val)
            # The Extender's encoder counter now sits at ring_val (if the
            # ring write moves it); continue relative turns from there.
            self._last_raw[strip] = ring_val
        view = (top, bottom, self._colours(strip))
        if not force and self._shown.get(strip) == view:
            return
        self._shown[strip] = view
        now = self._clock()
        last = self._scribble_sent_at.get(strip, -1e9)
        if force or now - last >= SCRIBBLE_MIN_INTERVAL_S:
            self._send_scribble(strip)
        elif strip not in self._scribble_timer:
            wait = SCRIBBLE_MIN_INTERVAL_S - (now - last)
            self._scribble_timer[strip] = self._schedule(
                wait, lambda s=strip: self._flush_scribble(s))

    def _flush_scribble(self, strip: int) -> None:
        with self._lock:
            self._scribble_timer.pop(strip, None)
            if self.active:
                self._send_scribble(strip)

    def _send_scribble(self, strip: int) -> None:
        top, bottom, colours = self._shown.get(
            strip, ("", "", self._colours(strip)))
        self._scribble_sent_at[strip] = self._clock()
        self._scribble_writer(strip, top, bottom, *colours)

    def repaint(self) -> None:
        """Full repaint (e.g. after a MIDI reconnect)."""
        with self._lock:
            if self.active:
                self._shown = {}
                self._paint_all(force=True)

    # ----- outbound -----------------------------------------------------

    def _write(self, address: str, value) -> None:
        self.link.send_address(address, value)
