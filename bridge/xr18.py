"""Bidirectional OSC link to the Behringer XR18 with transparent OSC proxy.

The bridge owns UDP port 10024 on the Pi. External OSC clients (X-Air
Edit, Mixing Station, etc.) point their "mixer IP" config at the Pi
instead of the real mixer. From their perspective the Pi IS the mixer.

Architecture (read-loop in a single thread):
  packet arrives on the bound socket
    -> if source IP == real mixer:
         - the bridge's own internal logic dispatches it (motor-sync
           callback for known address families)
         - the RAW bytes are forwarded to every known client
           (auto-discovered from prior writes/queries)
    -> if source IP == anything else:
         - source is recorded in the auto-client table
         - the RAW bytes are forwarded to the real mixer
         - the RAW bytes are also forwarded to every OTHER known client
           (so multiple consoles see each other's writes)

Raw-byte forwarding is essential: X-Air's /node/... bulk-state replies
have multi-arg payloads that don't round-trip cleanly through
pythonosc's parse-and-rebuild path. By forwarding the original datagram
unchanged we stay protocol-agnostic and support discovery probes
(/xinfo) and bulk queries the bridge itself doesn't understand.

The bridge sends its own /xremote keepalive every 8 s so the mixer
keeps streaming state-change notifications to the bridge -- those
notifications are what drive the Extender's motors.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional

from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket, ParseError

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


def discover_mixer(port: int = 10024,
                   total_timeout: float = 5.0,
                   per_attempt_timeout: float = 1.0) -> Optional[str]:
    """Discover an X-Air mixer on the LAN via /xinfo broadcast.

    Broadcasts /xinfo to 255.255.255.255 (limited broadcast) on the
    given UDP port. Any X-Air mixer on the same subnet replies with
    /xinfo ,ssss <ip> <name> <model> <fw>. Returns the IP the mixer
    reports for itself (parsed out of the reply, not the source IP --
    the mixer's own self-reported IP is what other X-Air apps trust).

    Returns None if no mixer replies within total_timeout seconds.
    Retries the broadcast every per_attempt_timeout seconds until
    either a reply arrives or the total budget is exhausted.

    Caller can fall back to a static IP if this returns None.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # We bind to an ephemeral local port -- the mixer will reply
        # to our source port, NOT the port we send to.
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(per_attempt_timeout)

        # Build the probe: bare /xinfo with no args.
        b = OscMessageBuilder(address="/xinfo")
        probe = b.build().dgram

        deadline = time.time() + total_timeout
        while time.time() < deadline:
            try:
                sock.sendto(probe, ("255.255.255.255", port))
            except (socket.error, OSError) as e:
                logger.warn("osc", "discovery broadcast failed", err=str(e))
                return None
            try:
                dgram, src = sock.recvfrom(65535)
            except socket.timeout:
                continue
            # Parse the reply. Expected format:
            #   /xinfo  ,ssss  <ip>  <name>  <model>  <fw>
            try:
                packet = OscPacket(dgram)
            except (ParseError, Exception):
                continue
            for msg in packet.messages:
                if msg.message.address != "/xinfo":
                    continue
                params = list(msg.message.params)
                if len(params) >= 1 and isinstance(params[0], str):
                    reported_ip = params[0].strip()
                    name = params[1] if len(params) > 1 else "?"
                    model = params[2] if len(params) > 2 else "?"
                    fw = params[3] if len(params) > 3 else "?"
                    logger.info(
                        "osc", "mixer discovered",
                        ip=reported_ip, name=name, model=model,
                        firmware=fw, src=f"{src[0]}:{src[1]}",
                    )
                    return reported_ip
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass


# Callback signature for any target's incoming position update.
TargetUpdateCallback = Callable[[Target, float], None]


# Internal logic only dispatches on these address families. Anything
# else from the mixer is still forwarded to clients but doesn't trigger
# the motor-sync callback.
_DISPATCH_PATTERNS = (
    "/ch/",        # /ch/NN/mix/fader, /ch/NN/mix/on, /ch/NN/mix/MM, /ch/NN/mix/MM/level
    "/lr/",        # /lr/mix/fader, /lr/mix/on
    "/bus/",       # /bus/N/mix/fader
    "/dca/",       # /dca/N/fader
    "/headamp/",   # /headamp/NN/gain
)


class XR18Link:
    """Transparent OSC proxy + internal subscriber. start()/stop() lifecycle."""

    KEEPALIVE_INTERVAL_S = 8.0   # /xremote expires after 10 s
    CLIENT_EXPIRY_S = 60.0       # drop unseen clients from fan-out list
    CLIENT_CLEANUP_INTERVAL_S = 10.0
    RECV_BUFFER = 65535          # max UDP datagram

    def __init__(self, ip: str, port: int = 10023, local_port: int = 10024):
        self.ip = ip
        self.port = port
        self.local_port = local_port

        # Auto-discovered fan-out clients: (src_ip, src_port) -> last_seen.
        # Entries expire after CLIENT_EXPIRY_S of silence.
        self._auto_clients: dict[tuple[str, int], float] = {}
        self._auto_clients_lock = threading.Lock()

        self._sock: Optional[socket.socket] = None
        self._local_ip: str = "0.0.0.0"
        self._read_thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._cleanup_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cb: Optional[TargetUpdateCallback] = None

    # ----- Public API ----------------------------------------------------

    def set_target_callback(self, cb: TargetUpdateCallback) -> None:
        self._cb = cb

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Read timeout lets the read loop check self._stop periodically.
        self._sock.settimeout(0.5)
        try:
            self._sock.bind(("0.0.0.0", self.local_port))
        except (socket.error, OSError) as e:
            logger.error("osc", "could not bind local OSC port",
                         port=self.local_port, err=str(e))
            self._sock.close()
            self._sock = None
            raise

        # Determine the Pi's IP as seen by the mixer. UDP "connect" is
        # destination-only (no handshake); getsockname() then returns
        # the local address the kernel would use for that route.
        # This IP is substituted into /xinfo and /status replies so
        # external clients see the Pi as a discoverable mixer at this
        # address, not the real mixer's.
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect((self.ip, self.port))
            self._local_ip = probe.getsockname()[0]
            probe.close()
        except Exception:
            self._local_ip = "0.0.0.0"
        logger.info("osc", "local IP for discovery rewrite",
                    local_ip=self._local_ip)

        self._read_thread = threading.Thread(
            target=self._read_loop, name="osc-read", daemon=True,
        )
        self._read_thread.start()

        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, name="osc-keepalive", daemon=True,
        )
        self._keepalive_thread.start()

        self._cleanup_thread = threading.Thread(
            target=self._client_cleanup_loop,
            name="osc-client-cleanup", daemon=True,
        )
        self._cleanup_thread.start()

        logger.info("osc", "XR18 link started (transparent proxy)",
                    target=f"{self.ip}:{self.port}",
                    local_port=self.local_port)

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        logger.info("osc", "XR18 link stopped")

    def send_target(self, target: Target, value: float) -> None:
        """Send a value to any supported target.

        `value` is 0.0..1.0; for bool targets we threshold at 0.5.
        The packet goes to the mixer AND is fanned out to all clients.
        """
        from .targets import target_value_kind
        try:
            address = target_to_address(target)
        except ValueError as e:
            logger.warn("osc", "bad target", err=str(e))
            return
        kind = target_value_kind(target)
        if kind == "bool":
            self._build_and_send(address, 1 if value >= 0.5 else 0, fanout=True)
        else:
            value = max(0.0, min(1.0, value))
            self._build_and_send(address, value, fanout=True)

    def query_target(self, target: Target) -> None:
        """Ask the mixer for the current value of `target`. The reply
        arrives via the normal read loop and triggers the callback."""
        try:
            address = target_to_address(target)
        except ValueError:
            return
        self._build_and_send(address, fanout=False)

    # ----- Read loop -----------------------------------------------------

    def _read_loop(self) -> None:
        """Single-threaded read of all incoming UDP packets, routed by
        source IP."""
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                dgram, src = self._sock.recvfrom(self.RECV_BUFFER)
            except socket.timeout:
                continue
            except (socket.error, OSError) as e:
                if self._stop.is_set():
                    return
                logger.warn("osc", "recv error", err=str(e))
                continue
            try:
                self._handle_packet(dgram, src)
            except Exception as e:
                logger.error("osc", "handler raised", err=str(e))

    def _handle_packet(self, dgram: bytes,
                       src: tuple[str, int]) -> None:
        """Route a single inbound packet by source."""
        from_mixer = (src[0] == self.ip)

        if from_mixer:
            # Mixer notifications / query replies: dispatch internally
            # for motor sync, then fan out to all clients (with
            # discovery-reply IP rewriting so clients see the Pi as
            # a separate mixer).
            self._dispatch_mixer_packet(dgram)
            out = self._rewrite_discovery_reply(dgram)
            self._send_raw_to_clients(out, exclude=None)
        else:
            # Client write or query: remember client, forward to mixer,
            # fan out to OTHER clients.
            self._remember_client(src)
            self._send_raw_to_mixer(dgram)
            self._send_raw_to_clients(dgram, exclude=src)

    def _rewrite_discovery_reply(self, dgram: bytes) -> bytes:
        """If `dgram` is a /xinfo or /status reply from the mixer, swap
        the embedded mixer-IP string for the bridge's own IP so external
        clients (X-Air Edit's scan) treat the bridge as a discoverable
        mixer. For anything else, return the dgram unchanged.

        /xinfo reply format: address='/xinfo', params=(ip, name, model, fw)
        /status reply format: address='/status', params=(state, ip, name)
        """
        try:
            packet = OscPacket(dgram)
        except (ParseError, Exception):
            return dgram
        if len(packet.messages) != 1:
            return dgram
        msg = packet.messages[0].message
        addr = msg.address
        if addr not in ("/xinfo", "/status"):
            return dgram
        args = list(msg.params)
        if addr == "/xinfo":
            # First string is the IP.
            if not args or not isinstance(args[0], str):
                return dgram
            args[0] = self._local_ip
        else:  # /status
            # Second string is the IP. First is state ("active").
            if len(args) < 2 or not isinstance(args[1], str):
                return dgram
            args[1] = self._local_ip
        b = OscMessageBuilder(address=addr)
        for a in args:
            b.add_arg(a)
        return b.build().dgram

    def _dispatch_mixer_packet(self, dgram: bytes) -> None:
        """Parse a mixer-sourced packet and fire the target callback for
        any address family we care about. Unparseable or unhandled
        packets are silently ignored -- they still get fanned out
        verbatim by the caller."""
        try:
            packet = OscPacket(dgram)
        except (ParseError, Exception):
            return
        for msg in packet.messages:
            address = msg.message.address
            args = msg.message.params
            if not args:
                continue
            if not any(address.startswith(p) for p in _DISPATCH_PATTERNS):
                continue
            target = address_to_target(address)
            logger.info("osc-in", "target update",
                        address=address, args=tuple(args),
                        target=str(target))
            if target is None:
                continue
            try:
                raw = args[0]
                if isinstance(raw, bool):
                    pos = 1.0 if raw else 0.0
                else:
                    pos = float(raw)
                    if pos < 0.0:
                        pos = 0.0
                    elif pos > 1.0:
                        pos = 1.0
            except (TypeError, ValueError):
                logger.warn("osc-in", "could not coerce value",
                            address=address, args=tuple(args))
                continue
            if self._cb is not None:
                try:
                    self._cb(target, pos)
                except Exception as e:
                    logger.error("sync", "target callback raised",
                                 err=str(e))

    # ----- Outbound helpers ---------------------------------------------

    def _build_and_send(self, address: str, *args, fanout: bool) -> None:
        """Build an OSC packet from scratch and send to mixer (+ clients
        if fanout)."""
        if self._sock is None:
            return
        b = OscMessageBuilder(address=address)
        for a in args:
            b.add_arg(a)
        dgram = b.build().dgram
        self._send_raw_to_mixer(dgram)
        if fanout:
            self._send_raw_to_clients(dgram, exclude=None)

    def _send_raw_to_mixer(self, dgram: bytes) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendto(dgram, (self.ip, self.port))
        except (socket.error, OSError) as e:
            logger.warn("osc", "mixer send failed", err=str(e))

    def _send_raw_to_clients(self, dgram: bytes,
                             exclude: Optional[tuple[str, int]]) -> None:
        if self._sock is None:
            return
        with self._auto_clients_lock:
            targets = [c for c in self._auto_clients.keys() if c != exclude]
        for client in targets:
            try:
                self._sock.sendto(dgram, client)
            except (socket.error, OSError) as e:
                logger.warn("osc", "client send failed",
                            dest=f"{client[0]}:{client[1]}", err=str(e))

    # ----- Auto-discovery housekeeping ----------------------------------

    def _remember_client(self, src: tuple[str, int]) -> None:
        now = time.time()
        with self._auto_clients_lock:
            is_new = src not in self._auto_clients
            self._auto_clients[src] = now
        if is_new:
            logger.info("osc", "discovered client",
                        client=f"{src[0]}:{src[1]}")

    def _client_cleanup_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.CLIENT_CLEANUP_INTERVAL_S)
            if self._stop.is_set():
                return
            cutoff = time.time() - self.CLIENT_EXPIRY_S
            dropped: list[tuple[str, int]] = []
            with self._auto_clients_lock:
                for client, last_seen in list(self._auto_clients.items()):
                    if last_seen < cutoff:
                        del self._auto_clients[client]
                        dropped.append(client)
            for client in dropped:
                logger.info("osc", "client expired",
                            client=f"{client[0]}:{client[1]}")

    # ----- Keepalive -----------------------------------------------------

    def _keepalive_loop(self) -> None:
        while not self._stop.is_set():
            self._build_and_send("/xremote", fanout=False)
            self._stop.wait(self.KEEPALIVE_INTERVAL_S)
