#!/usr/bin/env python3
"""midibridge — Phase 4/5.

Final appliance:
  - Bidirectional XR18 <-> Extender sync (faders motor-track)
  - Touch-aware suppression on motor faders
  - Knob -> bus master in --setup mode (locked by default)
  - LED ring feedback on encoders for any target the knob is mapped to
  - Scribble strip labels (top + bottom row per strip) set on startup
  - HTTP /debug endpoint with ring buffer
  - Reconnect logic for MIDI and OSC paths
  - Designed to run as a systemd service (see install-service.sh)
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from bridge import config as cfg_mod
from bridge import extender, logger
from bridge.debug_http import DebugServer
from bridge.watchdog import UsbWatchdog
from bridge.decoder import (
    decode,
    FaderMove, FaderTouch, KnobTurn, KnobPush, StripButton, UnknownEvent,
)
from bridge.targets import Target, target_label
from bridge.xr18 import (
    XR18Link, midi_to_fader_position, fader_position_to_midi,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="X-Touch Extender to XR18 OSC bridge")
    p.add_argument("--config", "-c", default="config.yaml",
                   help="Path to YAML config (default: config.yaml)")
    p.add_argument("--setup", action="store_true",
                   help="Enable knobs (setup mode). Default: knobs locked.")
    p.add_argument("--list-ports", action="store_true",
                   help="List available MIDI input/output ports and exit")
    return p.parse_args()


def cmd_list_ports() -> int:
    print("MIDI inputs:")
    for n in extender.list_inputs():
        print(f"  {n}")
    print("MIDI outputs:")
    for n in extender.list_outputs():
        print(f"  {n}")
    return 0


class SyncState:
    """Shared state between MIDI-input thread and OSC-server thread."""

    def __init__(self,
                 fader_map: dict[int, Optional[Target]],
                 knob_map: dict[int, Optional[Target]],
                 mute_map: dict[int, Optional[Target]],
                 lock_trim_in_operation_mode: bool = False,
                 mirror_map: dict = None):
        self.fader_map = fader_map
        self.knob_map = knob_map
        self.mute_map = mute_map
        self.lock_trim_in_operation_mode = lock_trim_in_operation_mode
        self.mirror_map = mirror_map or {}
        # Bus mode: None = Live mode (faders drive channel/lr targets as
        # per fader_map). Integer 1..6 = "Sends on Fader" mode for that
        # bus, where faders 1-7 drive ch1..ch7's send level to that bus,
        # and fader 8 drives the bus master.
        self.bus_mode: Optional[int] = None
        # Inverse fader map: target -> Extender strip (for motor faders)
        self.inverse_fader_map: dict[Target, int] = {}
        seen_f = {}
        for strip, target in fader_map.items():
            if target is None:
                continue
            if target in seen_f:
                logger.warn("config",
                            "target on multiple faders, only one motor-syncs",
                            target=target_label(target),
                            strips=[seen_f[target], strip])
            seen_f[target] = strip
            self.inverse_fader_map[target] = strip
        # Inverse knob map: target -> knob strip (for ring feedback)
        self.inverse_knob_map: dict[Target, int] = {}
        seen_k = {}
        for knob, target in knob_map.items():
            if target is None:
                continue
            if target in seen_k:
                logger.warn("config",
                            "target on multiple knobs, only one ring-syncs",
                            target=target_label(target),
                            knobs=[seen_k[target], knob])
            seen_k[target] = knob
            self.inverse_knob_map[target] = knob
        # Inverse mute map: target -> strip (for mute LED feedback)
        self.inverse_mute_map: dict[Target, int] = {}
        for strip, target in mute_map.items():
            if target is None:
                continue
            self.inverse_mute_map[target] = strip
        # Latest known mixer position per target
        self.target_pos: dict[Target, float] = {}
        # Touch state per Extender strip (1..8)
        self.touched: dict[int, bool] = {i: False for i in range(1, 9)}
        # Last user-driven FaderMove timestamp per strip (1..8).
        # The X-Touch Extender in Ctrl mode does NOT send fader-touch
        # events, so we infer "user is holding this fader" from a recent
        # FaderMove. Used by the XR18 callback to suppress motor writes
        # while the user is moving the fader (otherwise the mixer's echo
        # of our own write loops back and fights the user's hand).
        self.last_move: dict[int, float] = {i: 0.0 for i in range(1, 9)}
        # Last bridge-issued motor write per strip: (midi_value, timestamp).
        # The Extender echoes our motor-fader writes back on its input
        # port as if the user had moved the fader. We use this to detect
        # and ignore those echoes — otherwise they'd update last_move
        # and trigger spurious user-suppression, breaking Edit -> Ext
        # motor sync.
        self.last_motor_write: dict[int, tuple[int, float]] = {}
        self.lock = threading.Lock()


# Helpers: which fader on the Extender drives what, depending on bus_mode.
def _fader_target_in_mode(state: SyncState, strip: int) -> Optional[Target]:
    """In Live mode, returns the configured fader_map target.
    In Bus N mode, returns send_chN_to_busN for strips 1..7, bus_N for strip 8.
    """
    if state.bus_mode is None:
        return state.fader_map.get(strip)
    bus = state.bus_mode
    if 1 <= strip <= 7:
        return f"send_ch{strip}_to_bus{bus}"
    if strip == 8:
        return f"bus_{bus}"
    return None


def _send_motor_fader(state: SyncState, motor: extender.MotorOutput,
                      strip: int, midi: int) -> None:
    """Write a motor-fader command AND record it in state.last_motor_write.
    The recorded write is consulted by the FaderMove handler to recognize
    and ignore the Extender's echo of our own write (which would otherwise
    appear to be a user move and trip recent-move suppression)."""
    motor.send_fader(strip, midi)
    with state.lock:
        state.last_motor_write[strip] = (midi, time.time())


def _scribble_for_strip(state: SyncState, scribble_cfg, strip: int):
    """Return (top, bottom, bg, top_text, bottom_text) tuple for strip.
    In Bus N mode, returns "->BUS N" style labels; in Live mode the configured."""
    from bridge.config import ScribbleEntry
    if state.bus_mode is None:
        e = scribble_cfg.entries.get(strip, ScribbleEntry())
        return e.top, e.bottom, e.background, e.top_text, e.bottom_text
    bus = state.bus_mode
    if 1 <= strip <= 7:
        # Yellow background to match X-Air Edit's Bus color coding
        return (f"CH {strip}", f">BUS{bus}", "yellow", "dark", "dark")
    if strip == 8:
        return (f"BUS {bus}", "MASTER", "yellow", "dark", "dark")
    return ("", "", "white", "dark", "dark")


def _refresh_extender_for_mode(state: SyncState, motor, scribble_cfg, link):
    """Repaint scribble strips, snap motors, light Select LEDs to reflect
    current bus_mode (Live or Bus N)."""
    # Scribble re-paint
    for strip in range(1, 9):
        top, bot, bg_name, tt_name, bt_name = _scribble_for_strip(
            state, scribble_cfg, strip)
        bg = extender.SCRIBBLE_COLOR_NAMES.get(bg_name, extender.SCRIBBLE_WHITE)
        tt = extender.TEXT_LIGHT if tt_name == "light" else extender.TEXT_DARK
        bt = extender.TEXT_LIGHT if bt_name == "light" else extender.TEXT_DARK
        try:
            extender.encode_scribble(motor, strip,
                                     top=top, bottom=bot,
                                     background=bg,
                                     top_text=tt, bottom_text=bt)
        except Exception as e:
            logger.warn("scribble", "set failed", strip=strip, err=str(e))

    # Select LEDs: light the active bus's Select button, dark the rest.
    # In Live mode all dark.
    for strip in range(1, 9):
        lit = (state.bus_mode is not None and strip == state.bus_mode)
        motor.send_button_led("select", strip, lit)

    # Motors: snap each strip to whatever target is now mapped to it.
    for strip in range(1, 9):
        target = _fader_target_in_mode(state, strip)
        if target is None:
            continue
        with state.lock:
            pos = state.target_pos.get(target)
        if pos is None:
            # We don't have a cached value -- query the mixer
            link.query_target(target)
            continue
        midi = fader_position_to_midi(pos)
        _send_motor_fader(state, motor, strip, midi)


def _enter_bus_mode(state, bus: int, motor, scribble_cfg, link):
    state.bus_mode = bus
    logger.info("mode", "entering bus mode", bus=bus)
    _refresh_extender_for_mode(state, motor, scribble_cfg, link)


def _exit_bus_mode(state, motor, scribble_cfg, link):
    prev = state.bus_mode
    state.bus_mode = None
    logger.info("mode", "exiting bus mode", was_bus=prev)
    _refresh_extender_for_mode(state, motor, scribble_cfg, link)


def handle_midi_event(
    ev,
    state: SyncState,
    setup_mode: bool,
    link: XR18Link,
    motor: extender.MotorOutput,
    scribble_cfg=None,
) -> None:
    """Handle one decoded event from the Extender."""
    if isinstance(ev, FaderMove):
        # Echo filter: the Extender mirrors our own motor-fader writes
        # back as if the user had moved the fader. Detect and drop those
        # — they don't represent user intent, and treating them as user
        # moves would trip the recent-move suppression and block Edit ->
        # Ext motor sync.
        with state.lock:
            recent_write = state.last_motor_write.get(ev.strip)
        if recent_write is not None:
            written_val, written_at = recent_write
            if (ev.value == written_val
                    and (time.time() - written_at) < 0.5):
                # Echo of our own write. Clear so we don't suppress a
                # subsequent legitimate user move at the same value.
                with state.lock:
                    state.last_motor_write.pop(ev.strip, None)
                logger.debug("fader", "ignored self-echo",
                             strip=ev.strip, value=ev.value)
                return

        target = _fader_target_in_mode(state, ev.strip)
        if target is None:
            logger.info("fader", "move (reserved/inert)",
                        strip=ev.strip, value=ev.value)
        else:
            position = midi_to_fader_position(ev.value)
            link.send_target(target, position)
            # Optimistic local update: some targets (channel sends, in
            # particular) aren't echoed back by the mixer to the writer,
            # so without this the cached target_pos would lag behind
            # and the touch-release resync would snap the motor to a
            # stale value.
            #
            # Also stamp last_move[strip] so the XR18 callback can detect
            # "user is actively moving this fader" and suppress motor
            # writes for ~250ms after the last move — otherwise the
            # mixer's echo of our own write fights the user's hand.
            # (Ctrl-mode Extender doesn't send fader-touch events, so
            # this is the only way to detect grab.)
            now = time.time()
            with state.lock:
                state.target_pos[target] = position
                state.last_move[ev.strip] = now
            logger.info("fader", "move -> XR18",
                        strip=ev.strip, value=ev.value,
                        target=target_label(target), pos=f"{position:.3f}",
                        mode=("bus" + str(state.bus_mode)) if state.bus_mode else "live")
            # Mirror to secondary targets only in Live mode (mirroring
            # would not make sense for bus sends).
            if state.bus_mode is None:
                for secondary in state.mirror_map.get(target, []):
                    link.send_target(secondary, position)
                    with state.lock:
                        state.target_pos[secondary] = position
                    logger.info("fader", "  mirror -> XR18",
                                target=target_label(secondary),
                                pos=f"{position:.3f}")

    elif isinstance(ev, FaderTouch):
        with state.lock:
            was_touched = state.touched[ev.strip]
            state.touched[ev.strip] = ev.pressed
        logger.debug("fader", "touch",
                     strip=ev.strip, pressed=ev.pressed)
        if was_touched and not ev.pressed:
            target = _fader_target_in_mode(state, ev.strip)
            if target is not None:
                with state.lock:
                    pos = state.target_pos.get(target)
                if pos is not None:
                    midi = fader_position_to_midi(pos)
                    _send_motor_fader(state, motor, ev.strip, midi)
                    logger.debug("sync", "resync motor on release",
                                 strip=ev.strip,
                                 target=target_label(target),
                                 pos=f"{pos:.3f}", midi=midi)

    elif isinstance(ev, KnobTurn):
        target = state.knob_map.get(ev.strip)
        if target is None:
            logger.debug("knob", "turn (unmapped)",
                         strip=ev.strip, value=ev.value)
        else:
            # Trim targets are unlocked in operation mode by default --
            # they're routine sound-check adjustments. After sound check,
            # the operator can set lock_trim_in_operation_mode: true in
            # config.yaml to lock them too, preventing accidental gain
            # increases. Other knob targets (bus, DCA) are always locked
            # outside --setup.
            is_trim = isinstance(target, str) and target.startswith("trim_")
            trim_unlocked = is_trim and not state.lock_trim_in_operation_mode
            if not setup_mode and not trim_unlocked:
                logger.info("knob",
                            "turn (locked; run with --setup to enable)",
                            strip=ev.strip, target=target_label(target))
            else:
                position = midi_to_fader_position(ev.value)
                link.send_target(target, position)
                logger.info("knob", "turn -> XR18",
                            strip=ev.strip, value=ev.value,
                            target=target_label(target), pos=f"{position:.3f}")

    elif isinstance(ev, KnobPush):
        # Knob push is intentionally a no-op. The Extender's ring LEDs
        # don't accept external CC control in Ctrl mode (probed and
        # confirmed), so resetting trim via push would leave the ring
        # showing the old position -- confusing. Knob twists are the
        # canonical way to change trim; the Extender lights its own
        # ring locally during twists.
        target = state.knob_map.get(ev.strip)
        logger.debug("knob", "push (no-op)",
                     strip=ev.strip, pressed=ev.pressed,
                     target=target_label(target) if target else None)
    elif isinstance(ev, StripButton):
        # Select button: toggle Bus mode (setup mode only, strips 1..6)
        if ev.button == "select" and ev.pressed:
            if not setup_mode:
                logger.debug("button", "select ignored (operation mode)",
                             strip=ev.strip)
            elif 1 <= ev.strip <= 6:
                if state.bus_mode == ev.strip:
                    _exit_bus_mode(state, motor, scribble_cfg, link)
                else:
                    _enter_bus_mode(state, ev.strip, motor, scribble_cfg, link)
            else:
                logger.debug("button", "select ignored (not 1..6)",
                             strip=ev.strip)
        # Mute button: only acts in Live mode, ignored while in bus mode
        elif ev.button == "mute" and ev.pressed:
            if state.bus_mode is not None:
                logger.debug("button", "mute ignored (in bus mode)",
                             strip=ev.strip, bus=state.bus_mode)
            else:
                target = state.mute_map.get(ev.strip)
                if target is None:
                    logger.debug("button", "mute (unmapped)", strip=ev.strip)
                else:
                    # Read current mixer state, send the opposite.
                    # /ch/NN/mix/on uses 1=unmuted, 0=muted, so toggling
                    # means sending NOT(current). Our internal model stores
                    # this as float 0.0 or 1.0.
                    with state.lock:
                        current = state.target_pos.get(target, 1.0)
                    new_value = 0.0 if current >= 0.5 else 1.0
                    link.send_target(target, new_value)
                    # Optimistically update local state so subsequent presses
                    # toggle reliably even if the mixer's echo is delayed.
                    with state.lock:
                        state.target_pos[target] = new_value
                    motor.send_button_led("mute", ev.strip, new_value < 0.5)
                    logger.info("button", "mute toggle -> XR18",
                                strip=ev.strip, target=target_label(target),
                                muted=(new_value < 0.5))
        else:
            logger.debug("button", f"{ev.button} (unmapped)",
                         strip=ev.strip, pressed=ev.pressed)
    elif isinstance(ev, UnknownEvent):
        logger.warn("midi", "unrecognized message", raw=ev.raw)


def make_xr18_callback(state: SyncState, motor: extender.MotorOutput):
    """Callback fired on any incoming target update from the mixer."""
    def on_target(target: Target, position: float) -> None:
        with state.lock:
            state.target_pos[target] = position
            bus_mode = state.bus_mode
            # In bus mode, the fader mapping is dynamic: ch sends drive
            # strips 1-7, bus master drives strip 8. Compute it inline.
            fader_strip = None
            if bus_mode is None:
                fader_strip = state.inverse_fader_map.get(target)
            else:
                if isinstance(target, str):
                    # send_chN_to_busBUS_MODE -> strip N
                    expected_prefix = f"send_ch"
                    expected_suffix = f"_to_bus{bus_mode}"
                    if (target.startswith(expected_prefix)
                            and target.endswith(expected_suffix)):
                        try:
                            ch_str = target[len(expected_prefix):
                                            -len(expected_suffix)]
                            ch = int(ch_str)
                            if 1 <= ch <= 7:
                                fader_strip = ch
                        except ValueError:
                            pass
                    elif target == f"bus_{bus_mode}":
                        fader_strip = 8
            knob_strip = state.inverse_knob_map.get(target)
            mute_strip = state.inverse_mute_map.get(target)
            # Suppress motor write if the user is actively manipulating
            # this fader. Two cases:
            #   - touched: the Extender sent a fader-touch event recently
            #     (MC mode supports this; Ctrl mode does NOT)
            #   - recent_move: a FaderMove arrived in the last 250 ms,
            #     which is our Ctrl-mode-friendly stand-in for "touched"
            touched = state.touched.get(fader_strip, False) if fader_strip else False
            recent_move = False
            if fader_strip is not None:
                last = state.last_move.get(fader_strip, 0.0)
                recent_move = (time.time() - last) < 0.25
            suppress = touched or recent_move

        midi = fader_position_to_midi(position)

        if fader_strip is not None and not suppress:
            _send_motor_fader(state, motor, fader_strip, midi)
            logger.info("sync", "motor <- XR18",
                        strip=fader_strip, target=target_label(target),
                        pos=f"{position:.3f}", midi=midi)
        elif fader_strip is not None and suppress:
            logger.debug("sync", "skip motor (user moving)",
                         strip=fader_strip, target=target_label(target),
                         touched=touched, recent_move=recent_move)

        if knob_strip is not None:
            motor.send_ring(knob_strip, midi)
            logger.info("sync", "ring <- XR18",
                        knob=knob_strip, target=target_label(target),
                        pos=f"{position:.3f}", midi=midi)

        # Mute LEDs only update in Live mode -- in bus mode the mute
        # buttons are inactive and lighting them would be confusing.
        if mute_strip is not None and bus_mode is None:
            # XR18 sends "on" = 1 (unmuted) or 0 (muted).
            # We light the MUTE LED when the channel IS muted.
            muted = position < 0.5
            motor.send_button_led("mute", mute_strip, muted)
            logger.info("sync", "mute LED <- XR18",
                        strip=mute_strip, target=target_label(target),
                        muted=muted)
    return on_target


def apply_scribble(cfg, motor: extender.MotorOutput) -> None:
    """Push scribble strip labels for each configured strip."""
    for strip, entry in sorted(cfg.scribble.entries.items()):
        bg = extender.SCRIBBLE_COLOR_NAMES.get(
            entry.background, extender.SCRIBBLE_WHITE
        )
        top_shade = extender.TEXT_LIGHT if entry.top_text == "light" else extender.TEXT_DARK
        bot_shade = extender.TEXT_LIGHT if entry.bottom_text == "light" else extender.TEXT_DARK
        extender.encode_scribble(
            motor, strip,
            top=entry.top, bottom=entry.bottom,
            background=bg,
            top_text=top_shade, bottom_text=bot_shade,
        )
        logger.info("scribble", "set",
                    strip=strip, top=entry.top, bottom=entry.bottom,
                    bg=entry.background)


def open_midi_with_retry(name_fragment: str,
                         opener,
                         retry_s: float = 2.0,
                         max_tries: int = 30):
    """Repeatedly try to open the named port, sleeping between attempts.

    Returns the open port or raises after max_tries.
    """
    for attempt in range(1, max_tries + 1):
        try:
            return opener(name_fragment)
        except RuntimeError as e:
            logger.warn("midi", "open failed; retrying",
                        attempt=attempt, max=max_tries, err=str(e))
            time.sleep(retry_s)
    raise RuntimeError(f"giving up after {max_tries} attempts to open MIDI port")


def main() -> int:
    args = parse_args()

    if args.list_ports:
        logger.setup("INFO", 100)
        return cmd_list_ports()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}", file=sys.stderr)
        return 2

    try:
        cfg = cfg_mod.load(cfg_path)
    except Exception as e:
        print(f"Bad config: {e}", file=sys.stderr)
        return 2

    logger.setup(cfg.logging.level, cfg.logging.ring_size)

    # Fingerprint the config file so we can verify what's running
    import hashlib
    try:
        with open(cfg_path, "rb") as f:
            cfg_sha = hashlib.sha256(f.read()).hexdigest()[:8]
    except Exception:
        cfg_sha = "?"

    logger.info(
        "system",
        f"starting midibridge ({'SETUP' if args.setup else 'operation'} mode)",
        active_faders=cfg.active_fader_count(),
        reserved_faders=cfg.reserved_fader_count(),
        active_knobs=cfg.active_knob_count(),
        active_mutes=cfg.active_mute_count(),
        knobs_live=args.setup,
        trim_locked=cfg.lock_trim_in_operation_mode and not args.setup,
        xr18_target=f"{cfg.xr18.ip}:{cfg.xr18.port}",
        local_osc_port=cfg.xr18.local_port,
        config_sha8=cfg_sha,
    )

    # Log full mappings so a glance at the journal tells you what landed.
    # Sorted by strip so it's easy to scan.
    def _fmt_map(m):
        return ", ".join(
            f"{k}={target_label(v) if v is not None else '-'}"
            for k, v in sorted(m.items())
        )
    logger.info("system", f"faders: {_fmt_map(cfg.fader_map)}")
    logger.info("system", f"knobs:  {_fmt_map(cfg.knob_map)}")
    logger.info("system", f"mutes:  {_fmt_map(cfg.mute_map)}")

    # ----- Debug HTTP -----
    debug_srv = DebugServer(cfg.debug_port)
    debug_srv.start()

    # ----- USB watchdog -----
    watchdog = UsbWatchdog(cfg.midi.port_name, interval_s=3.0)
    watchdog.start()

    state = SyncState(cfg.fader_map, cfg.knob_map, cfg.mute_map,
                      lock_trim_in_operation_mode=cfg.lock_trim_in_operation_mode,
                      mirror_map=cfg.mirror_map)

    # ----- MIDI (with reconnect) -----
    try:
        port_in = open_midi_with_retry(cfg.midi.port_name, extender.open_input)
        port_out_raw = open_midi_with_retry(cfg.midi.port_name, extender.open_output)
    except RuntimeError as e:
        print(f"\nMIDI error: {e}", file=sys.stderr)
        debug_srv.stop()
        return 3
    motor = extender.MotorOutput(port_out_raw)

    # Scribble labels
    apply_scribble(cfg, motor)

    # ----- XR18 link -----
    # Parse edit_mirror "host:port" strings into (host, int(port)) tuples.
    # Invalid entries are logged and skipped so a typo doesn't kill startup.
    mirror_dests: list[tuple[str, int]] = []
    for entry in cfg.xr18.edit_mirror:
        if ":" not in entry:
            logger.warn("config", "edit_mirror entry missing port, skipping",
                        entry=entry)
            continue
        host, _, port_str = entry.rpartition(":")
        try:
            mirror_dests.append((host, int(port_str)))
        except ValueError:
            logger.warn("config", "edit_mirror entry has non-numeric port, skipping",
                        entry=entry)
    if mirror_dests:
        logger.info("config", "edit_mirror enabled",
                    destinations=[f"{h}:{p}" for (h, p) in mirror_dests])
    link = XR18Link(cfg.xr18.ip, cfg.xr18.port, cfg.xr18.local_port,
                    mirror_destinations=mirror_dests)
    link.set_target_callback(make_xr18_callback(state, motor))
    try:
        link.start()
    except Exception as e:
        print(f"\nOSC error: {e}", file=sys.stderr)
        port_in.close()
        motor.close()
        debug_srv.stop()
        return 4

    # Initial state sync: query every distinct target referenced anywhere.
    queried: set = set()
    for src in (cfg.fader_map, cfg.knob_map, cfg.mute_map):
        for target in src.values():
            if target is not None and target not in queried:
                link.query_target(target)
                queried.add(target)
    # Also pre-query channel-to-bus sends (used in bus mode) and bus
    # masters, so entering bus mode for the first time instantly knows
    # current positions instead of having to wait for the mixer to push.
    if args.setup:
        for ch in range(1, 8):
            for bus in range(1, 7):
                t = f"send_ch{ch}_to_bus{bus}"
                if t not in queried:
                    link.query_target(t)
                    queried.add(t)
        for bus in range(1, 7):
            t = f"bus_{bus}"
            if t not in queried:
                link.query_target(t)
                queried.add(t)

    logger.info("system", "listening. Ctrl+C to stop.")

    # ----- MIDI input loop with reconnect -----
    try:
        while True:
            try:
                for msg in port_in:
                    ev = decode(msg)
                    if ev is not None:
                        handle_midi_event(ev, state, args.setup, link, motor,
                                          scribble_cfg=cfg.scribble)
            except (OSError, IOError) as e:
                logger.error("midi", "input loop crashed; reconnecting",
                             err=str(e))
                try: port_in.close()
                except Exception: pass
                # Re-acquire both input and output (USB unplug typically
                # invalidates both)
                time.sleep(1.0)
                try:
                    port_in = open_midi_with_retry(
                        cfg.midi.port_name, extender.open_input
                    )
                    new_out = open_midi_with_retry(
                        cfg.midi.port_name, extender.open_output
                    )
                    motor.close()
                    motor = extender.MotorOutput(new_out)
                    # Rewire callback to the new motor object
                    link.set_target_callback(make_xr18_callback(state, motor))
                    apply_scribble(cfg, motor)
                    # Resync state from cached XR18 positions
                    with state.lock:
                        snapshot = dict(state.target_pos)
                    for target, pos in snapshot.items():
                        midi = fader_position_to_midi(pos)
                        strip = state.inverse_fader_map.get(target)
                        if strip is not None:
                            _send_motor_fader(state, motor, strip, midi)
                        knob = state.inverse_knob_map.get(target)
                        if knob is not None:
                            motor.send_ring(knob, midi)
                        mute = state.inverse_mute_map.get(target)
                        if mute is not None:
                            motor.send_button_led("mute", mute, pos < 0.5)
                    logger.info("system", "midi reconnected")
                except RuntimeError as e2:
                    logger.error("midi", "reconnect failed", err=str(e2))
                    return 3
    except KeyboardInterrupt:
        logger.info("system", "shutdown requested")
        return 0
    finally:
        try: port_in.close()
        except Exception: pass
        link.stop()
        motor.close()
        watchdog.stop()
        debug_srv.stop()


if __name__ == "__main__":
    sys.exit(main())
