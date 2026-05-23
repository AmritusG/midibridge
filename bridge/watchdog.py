"""USB / MIDI port watchdog.

Polls every few seconds to check whether the Extender's MIDI port is
still enumerated by the OS. Logs presence/absence transitions so the
/debug page (and the syslog) reflect real device status, separate from
whether any MIDI events have flowed recently.

The bridge's main MIDI loop already handles reconnect on I/O error.
This watchdog adds observability for the "device powered off / unplugged
but no one tried to send to it yet" case.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import mido

from . import logger


class UsbWatchdog:
    def __init__(self, port_name_fragment: str, interval_s: float = 3.0):
        self._fragment = port_name_fragment.lower()
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._present: Optional[bool] = None  # None = unknown at boot
        self._last_change: float = 0.0
        self._lock = threading.Lock()

    def _port_present(self) -> bool:
        try:
            for name in mido.get_input_names():
                if self._fragment in name.lower():
                    return True
        except Exception:
            # If we can't even query, treat as absent rather than crash
            return False
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            present = self._port_present()
            with self._lock:
                changed = self._present is None or present != self._present
                if changed:
                    self._present = present
                    self._last_change = time.time()
            if changed:
                if present:
                    logger.info("watchdog", "MIDI port present", port=self._fragment)
                else:
                    logger.warn("watchdog", "MIDI port absent", port=self._fragment)
            self._stop.wait(self._interval)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="usb-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        with self._lock:
            return {
                "present": bool(self._present) if self._present is not None else None,
                "last_change_age_s": (
                    round(time.time() - self._last_change, 1)
                    if self._last_change else None
                ),
            }
