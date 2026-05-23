"""Structured logging with an in-memory ring buffer for /debug.

Two sinks:
- stdout (formatted lines via the standard logging module)
- in-memory deque of structured events for later inspection

The ring buffer is exposed via `get_ring()` and will be served over
HTTP in Phase 4.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class RingEvent:
    ts: float           # unix time, seconds
    level: str          # DEBUG/INFO/WARNING/ERROR
    kind: str           # short category, e.g. "fader", "knob", "system"
    msg: str            # human-readable summary
    data: dict[str, Any]  # structured fields


_ring: deque[RingEvent] = deque(maxlen=500)
_ring_lock = threading.Lock()
_stdout_logger = logging.getLogger("midibridge")


def setup(level: str = "INFO", ring_size: int = 500) -> None:
    """Configure stdout logging and ring size. Call once at startup."""
    global _ring
    with _ring_lock:
        _ring = deque(maxlen=ring_size)

    _stdout_logger.setLevel(getattr(logging, level, logging.INFO))
    # python-osc's server calls logging.basicConfig() which installs a
    # handler on the root logger; without this, every event would be
    # printed twice (once by us, once by root via propagation).
    _stdout_logger.propagate = False
    if not _stdout_logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        _stdout_logger.addHandler(h)


def event(level: str, kind: str, msg: str, **data: Any) -> None:
    """Log a structured event to both stdout and the ring."""
    ev = RingEvent(ts=time.time(), level=level, kind=kind, msg=msg, data=data)
    with _ring_lock:
        _ring.append(ev)
    log_fn = getattr(_stdout_logger, level.lower(), _stdout_logger.info)
    if data:
        # Compact one-line summary for stdout
        kv = " ".join(f"{k}={v}" for k, v in data.items())
        log_fn(f"[{kind}] {msg}  {kv}")
    else:
        log_fn(f"[{kind}] {msg}")


def get_ring() -> list[dict]:
    """Return a snapshot of the ring buffer for HTTP/debug exposure."""
    # Snapshot under the lock so we never iterate during mutation.
    with _ring_lock:
        snapshot = list(_ring)
    return [asdict(e) for e in snapshot]


# Convenience shortcuts
def info(kind: str, msg: str, **data: Any) -> None: event("INFO", kind, msg, **data)
def warn(kind: str, msg: str, **data: Any) -> None: event("WARNING", kind, msg, **data)
def error(kind: str, msg: str, **data: Any) -> None: event("ERROR", kind, msg, **data)
def debug(kind: str, msg: str, **data: Any) -> None: event("DEBUG", kind, msg, **data)
