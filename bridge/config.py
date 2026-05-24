"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .targets import Target, parse_target


@dataclass
class XR18Config:
    ip: str
    port: int = 10023
    local_port: int = 10023


@dataclass
class MidiConfig:
    port_name: str


@dataclass
class LoggingConfig:
    level: str = "INFO"
    ring_size: int = 500


@dataclass
class ScribbleEntry:
    top: str = ""
    bottom: str = ""
    background: str = "white"   # color name
    top_text: str = "dark"      # "dark" or "light"
    bottom_text: str = "dark"


@dataclass
class ScribbleConfig:
    # scribble[strip 1..8] = ScribbleEntry
    entries: dict[int, ScribbleEntry] = field(default_factory=dict)


@dataclass
class Config:
    xr18: XR18Config
    midi: MidiConfig
    # fader_map[fader_number 1..8] = Target or None
    fader_map: dict[int, Optional[Target]] = field(default_factory=dict)
    # knob_map[knob_number 1..8] = Target or None (active only with --setup)
    knob_map: dict[int, Optional[Target]] = field(default_factory=dict)
    # mute_map[strip 1..8] = Target or None (must be a "mute_*" target)
    mute_map: dict[int, Optional[Target]] = field(default_factory=dict)
    # mirror_map[primary_target] = list of secondary targets that should
    # receive the same float value (e.g. lr -> [bus_1..bus_6] means moving
    # the LR fader also moves all aux bus masters in parallel).
    mirror_map: dict[Target, list] = field(default_factory=dict)
    # If true, trim_* knobs are locked in operation mode (require --setup
    # to use). Flip to true after sound-check is done, to prevent the
    # extender from cranking input gain beyond safe levels.
    lock_trim_in_operation_mode: bool = False
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    scribble: ScribbleConfig = field(default_factory=ScribbleConfig)
    debug_port: int = 8080

    def active_fader_count(self) -> int:
        return sum(1 for v in self.fader_map.values() if v is not None)

    def reserved_fader_count(self) -> int:
        return sum(1 for v in self.fader_map.values() if v is None)

    def active_knob_count(self) -> int:
        return sum(1 for v in self.knob_map.values() if v is not None)

    def active_mute_count(self) -> int:
        return sum(1 for v in self.mute_map.values() if v is not None)


def _load_strip_map(section_name: str, raw: dict) -> dict[int, Optional[Target]]:
    """Parse a 1..8 -> target-or-null mapping section."""
    out: dict[int, Optional[Target]] = {}
    for k, v in (raw or {}).items():
        n = int(k)
        if not 1 <= n <= 8:
            raise ValueError(
                f"config: {section_name} entry {n} out of range (must be 1-8)"
            )
        if v is None:
            out[n] = None
        else:
            try:
                out[n] = parse_target(v)
            except ValueError as e:
                raise ValueError(f"config: {section_name}[{n}]: {e}") from None
    # Make sure all 8 are declared so behaviour is explicit
    for n in range(1, 9):
        out.setdefault(n, None)
    return out


def load(path: str | Path) -> Config:
    """Load and validate the YAML config. Raises ValueError on bad input."""
    path = Path(path)
    with path.open("r") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")

    # XR18
    xr18_raw = raw.get("xr18") or {}
    if "ip" not in xr18_raw:
        raise ValueError("config: xr18.ip is required")
    xr18 = XR18Config(
        ip=str(xr18_raw["ip"]),
        port=int(xr18_raw.get("port", 10023)),
        local_port=int(xr18_raw.get("local_port", 10024)),
    )

    # MIDI
    midi_raw = raw.get("midi") or {}
    if "port_name" not in midi_raw:
        raise ValueError("config: midi.port_name is required")
    midi = MidiConfig(port_name=str(midi_raw["port_name"]))

    # Faders and knobs
    fader_map = _load_strip_map("faders", raw.get("faders") or {})
    knob_map = _load_strip_map("knobs", raw.get("knobs") or {})
    mute_map = _load_strip_map("mutes", raw.get("mutes") or {})
    # Validate mute_map: every non-None entry must be a mute_* target
    for strip, t in mute_map.items():
        if t is None:
            continue
        if not (isinstance(t, str) and (t.startswith("mute_") or t == "mute_lr")):
            raise ValueError(
                f"config: mutes.{strip}={t!r} must be a mute_N or mute_lr target"
            )

    # Mirror map: when primary target is written, secondaries get the
    # same value too. Targets normalized through parse_target().
    mirror_map: dict = {}
    for k, v in (raw.get("mirror") or {}).items():
        primary = parse_target(k)
        if not isinstance(v, (list, tuple)):
            raise ValueError(
                f"config: mirror.{k} must be a list of targets, got {v!r}"
            )
        mirror_map[primary] = [parse_target(t) for t in v]

    # Logging
    log_raw = raw.get("logging") or {}
    logging_cfg = LoggingConfig(
        level=str(log_raw.get("level", "INFO")).upper(),
        ring_size=int(log_raw.get("ring_size", 500)),
    )

    # Scribble strip text + color
    scribble = ScribbleConfig()
    for k, v in (raw.get("scribble") or {}).items():
        n = int(k)
        if not 1 <= n <= 8:
            raise ValueError(f"config: scribble entry {n} out of range (must be 1-8)")
        if v is None:
            continue
        # Accept three forms:
        #   1: ["TOP", "BOT"]                          # list/tuple
        #   2: {top: TOP, bottom: BOT, background: white, top_text: dark, bottom_text: dark}
        #   3: "TOP"                                   # plain string
        entry = ScribbleEntry()
        if isinstance(v, (list, tuple)):
            entry.top = str(v[0]) if len(v) > 0 else ""
            entry.bottom = str(v[1]) if len(v) > 1 else ""
        elif isinstance(v, dict):
            entry.top = str(v.get("top", ""))
            entry.bottom = str(v.get("bottom", ""))
            entry.background = str(v.get("background", "white")).lower()
            entry.top_text = str(v.get("top_text", "dark")).lower()
            entry.bottom_text = str(v.get("bottom_text", "dark")).lower()
        else:
            entry.top = str(v)
        scribble.entries[n] = entry

    debug_port = int(raw.get("debug_port", 8080))
    lock_trim = bool(raw.get("lock_trim_in_operation_mode", False))

    return Config(
        xr18=xr18, midi=midi,
        fader_map=fader_map, knob_map=knob_map, mute_map=mute_map,
        mirror_map=mirror_map,
        lock_trim_in_operation_mode=lock_trim,
        logging=logging_cfg,
        scribble=scribble,
        debug_port=debug_port,
    )
