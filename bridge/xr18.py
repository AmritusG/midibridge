"""Bidirectional OSC link to the Behringer XR18.

Generalized over targets (channels, LR, buses, DCAs) so the bridge
doesn't have to know address shapes.

Subscribes via /xremote (re-sent every 8s to keep alive) and dispatches
incoming updates on any target address to the registered callback.
"""

from __future__ import annotations

import socket
import threading
from typing import Callable, Optional

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_server import ThreadingOSCUDPServer

from . import logger
from .targets import Target, target_to_address, address_to_target


def midi_to_fader_position(midi_value: int) -> float:
    """0..127 (MIDI CC) -> 0.0..1.0 (XR18 fader position)."""
    if midi_value < 0:
        midi_value = 0
    elif midi_value > 127:
        midi_value = 127
    return midi_value / 127.0


def fader_position_to_midi(position: float) -> int:
    """0.0..1.0 (XR18 fader position) -> 0..127 (MIDI CC)."""
    if position < 0.0:
        position = 0.0
    elif position > 1.0:
        position = 1.0
    return int(round(position * 127))


# Callback signature for any target's incoming position update.
TargetUpdateCallback = Callable[[Target, float], None]


class XR18Link:
    """Bidirectional OSC link. start()/stop() lifecycle."""

    KEEPALIVE_INTERVAL_S = 8.0   # /xremote expires after 10 s

    def __init__(self, ip: str, port: int = 10023, local_port: int = 10024,
                 mirror_destinations: Optional[list[tuple[str, int]]] = None):
        self.ip = ip
        self.port = port
        self.local_port = local_port
        # Fan-out destinations: every OSC parameter write the bridge sends
        # to the mixer is also sent to each of these (host, port) pairs.
        # This is how we keep external clients (e.g. X-Air Edit) in sync,
        # since the X-Air firmware does not actually deliver state-change
        # notifications back to Edit even when Edit is "connected" -- Edit
        # is a write-only client from the mixer's perspective.
        # The /xremote keepalive is NOT mirrored (Edit doesn't subscribe).
        self.mirror_destinations: list[tuple[str, int]] = mirror_destinations or []

        self._dispatcher = Dispatcher()
        # Catch every address family we care about. Each routes through
        # the same handler, which uses address_to_target() to identify it.
        for pattern in (
            "/ch/*/mix/fader",
            "/lr/mix/fader",
            "/bus/*/mix/fader",
            "/dca/*/fader",
            "/headamp/*/gain",
            "/ch/*/mix/on",
            "/lr/mix/on",
            "/ch/*/mix/01",
            "/ch/*/mix/02",
            "/ch/*/mix/03",
            "/ch/*/mix/04",
            "/ch/*/mix/05",
            "/ch/*/mix/06",
            "/ch/*/mix/01/level",
            "/ch/*/mix/02/level",
            "/ch/*/mix/03/level",
            "/ch/*/mix/04/level",
            "/ch/*/mix/05/level",
            "/ch/*/mix/06/level",
        ):
            self._dispatcher.map(pattern, self._on_target_update)
        self._dispatcher.set_default_handler(self._on_unknown)

        self._server: Optional[ThreadingOSCUDPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cb: Optional[TargetUpdateCallback] = None

    # ----- Public API ----------------------------------------------------

    def set_target_callback(self, cb: TargetUpdateCallback) -> None:
        self._cb = cb

    def start(self) -> None:
        # Subclass to enable SO_REUSEADDR before bind, so a freshly
        # restarted service can reclaim the port even if a previous
        # process just released it (TIME_WAIT) or is being killed off
        # by systemd. UDP doesn't have TIME_WAIT proper, but the kernel
        # can still hold the address briefly; SO_REUSEADDR makes that
        # transparent.
        class _ReusableServer(ThreadingOSCUDPServer):
            allow_reuse_address = True

        try:
            self._server = _ReusableServer(
                ("0.0.0.0", self.local_port), self._dispatcher
            )
        except (socket.error, OSError) as e:
            logger.error("osc", "could not bind local OSC port",
                         port=self.local_port, err=str(e))
            raise

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="osc-server", daemon=True,
        )
        self._server_thread.start()

        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            name="osc-keepalive", daemon=True,
        )
        self._keepalive_thread.start()

        logger.info("osc", "XR18 link started",
                    target=f"{self.ip}:{self.port}",
                    local_port=self.local_port)

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        logger.info("osc", "XR18 link stopped")

    def send_target(self, target: Target, value: float) -> None:
        """Send a value to any supported target.

        `value` is always a 0.0..1.0 float coming in. For targets with
        value_kind=='bool' (mute switches) we threshold at 0.5 and send
        the corresponding int 0 or 1.
        """
        from .targets import target_value_kind
        try:
            address = target_to_address(target)
        except ValueError as e:
            logger.warn("osc", "bad target", err=str(e))
            return
        kind = target_value_kind(target)
        if kind == "bool":
            self._send_raw(address, 1 if value >= 0.5 else 0)
        else:
            value = max(0.0, min(1.0, value))
            self._send_raw(address, value)

    def query_target(self, target: Target) -> None:
        """Ask the mixer for the current value of `target`. Reply via callback."""
        try:
            address = target_to_address(target)
        except ValueError:
            return
        self._send_raw(address, mirror=False)

    # ----- Internals -----------------------------------------------------

    def _send_raw(self, address: str, *args, mirror: bool = True) -> None:
        if self._server is None:
            return
        b = OscMessageBuilder(address=address)
        for a in args:
            b.add_arg(a)
        dgram = b.build().dgram
        try:
            self._server.socket.sendto(dgram, (self.ip, self.port))
        except (socket.error, OSError) as e:
            logger.warn("osc", "send failed (mixer unreachable?)",
                        address=address, err=str(e))
        # Fan-out to mirror destinations so external clients (Edit) see
        # bridge-induced changes. Only mirror parameter writes (mirror=True),
        # never the /xremote keepalive or bare queries.
        if mirror and args:
            for (host, port) in self.mirror_destinations:
                try:
                    self._server.socket.sendto(dgram, (host, port))
                except (socket.error, OSError) as e:
                    logger.warn("osc", "mirror send failed",
                                dest=f"{host}:{port}",
                                address=address, err=str(e))

    def _keepalive_loop(self) -> None:
        while not self._stop.is_set():
            self._send_raw("/xremote", mirror=False)
            self._stop.wait(self.KEEPALIVE_INTERVAL_S)

    def _on_target_update(self, address: str, *args) -> None:
        if not args:
            # The X-Air mixer sometimes sends bare addresses without
            # values (bundle headers etc.). Ignore silently.
            return
        target = address_to_target(address)
        logger.info("osc-in", "target update",
                    address=address, args=args, target=str(target))
        if target is None:
            return
        # Incoming arg is float for continuous params, int 0/1 for switches.
        # Normalize to float 0..1 for the bridge's internal state model.
        try:
            raw = args[0]
            if isinstance(raw, bool):
                pos = 1.0 if raw else 0.0
            else:
                pos = float(raw)
                # Clamp anything weird
                if pos < 0.0: pos = 0.0
                elif pos > 1.0: pos = 1.0
        except (TypeError, ValueError):
            logger.warn("osc-in", "could not coerce value",
                        address=address, args=args)
            return
        if self._cb is not None:
            try:
                self._cb(target, pos)
            except Exception as e:
                logger.error("sync", "target callback raised", err=str(e))

    def _on_unknown(self, address: str, *args) -> None:
        logger.info("osc-in", "incoming (unmapped)",
                    address=address, args=args)
