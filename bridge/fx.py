"""FX mode: knob 1 held ~1 s turns the Extender's knobs into a reverb
editor for one XR18 FX slot.

Facts used here (addresses, type numbers, parameter tables, scaling)
are collected with their sources in docs/FX-NOTES.md.

  FX type          /fx/{slot}/type            int 0..60
  FX parameter     /fx/{slot}/par/{NN:02d}    float 0..1 (enum params: int)
  Channel FX send  /ch/{CC:02d}/mix/{6+slot:02d}/level   float 0..1 (fader law)
  FX return        /rtn/{slot}/mix/on|lr  int 0|1,  /rtn/{slot}/mix/fader float
  FX send master   /fxsend/{slot}/mix/on  int 0|1,  /fxsend/{slot}/mix/fader float

Knob behaviour in FX mode is relative: each KnobTurn's change from the
previous raw value moves the parameter. That way a knob never jumps a
parameter to wherever the Extender's own encoder counter happens to sit.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import logger
from .knob_editor import (  # noqa: F401  (re-exported for tests)
    KnobEditor, MAX_DELTA, SCRIBBLE_MIN_INTERVAL_S, threading_scheduler,
)


# ----- Parameter definitions -------------------------------------------

@dataclass(frozen=True)
class ParamDef:
    num: int            # /fx/N/par/<num>
    label: str          # scribble label, <= 7 chars
    scale: str          # "lin" | "log" | "enum"
    lo: float = 0.0
    hi: float = 1.0
    unit: str = ""      # "ms" "s" "Hz" "dB" "%" ""
    choices: tuple = ()

    def value(self, norm: float) -> float:
        """Normalised 0..1 -> engineering value (lin/log params)."""
        norm = min(1.0, max(0.0, norm))
        if self.scale == "log":
            return self.lo * math.exp(norm * math.log(self.hi / self.lo))
        return self.lo + norm * (self.hi - self.lo)

    def format(self, raw) -> str:
        """Mixer value (float 0..1, or int index for enums) -> <=7 chars."""
        if raw is None:
            return "--"
        if self.scale == "enum":
            i = int(raw)
            return self.choices[i] if 0 <= i < len(self.choices) else str(i)
        return format_value(self.value(float(raw)), self.unit)


def format_value(v: float, unit: str) -> str:
    if unit == "Hz":
        if v >= 1000:
            return f"{v / 1000:.1f}kHz"
        return f"{v:.0f}Hz"
    if unit == "ms":
        return f"{v:.1f}ms" if v < 10 else f"{v:.0f}ms"
    if unit == "s":
        return f"{v:.2f}s" if v < 10 else f"{v:.1f}s"
    if unit == "dB":
        return f"{v:+.1f}dB"
    if unit == "%":
        return f"{v:.0f}%"
    return f"{v:.2f}" if abs(v) < 10 else f"{v:.0f}"


def level_to_db(f: float) -> float:
    """X-Air fader/send law, 0..1 -> dB (Maillot, 'level' type)."""
    if f >= 0.5:
        return f * 40.0 - 30.0
    if f >= 0.25:
        return f * 80.0 - 50.0
    if f >= 0.0625:
        return f * 160.0 - 70.0
    return f * 480.0 - 90.0


def format_level(f) -> str:
    if f is None:
        return "--"
    if f <= 0.0:
        return "-oo"
    return f"{level_to_db(float(f)):+.1f}dB"


def _lin(n, label, lo, hi, unit=""):
    return ParamDef(n, label, "lin", lo, hi, unit)


def _log(n, label, lo, hi, unit=""):
    return ParamDef(n, label, "log", lo, hi, unit)


def _enum(n, label, *choices):
    return ParamDef(n, label, "enum", 0, len(choices) - 1, "", tuple(choices))


@dataclass(frozen=True)
class FxType:
    index: int
    code: str
    name: str
    params: dict   # num -> ParamDef


def _t(index, code, name, *params) -> FxType:
    return FxType(index, code, name, {p.num: p for p in params})


# Tables from Maillot, "Unofficial X32/M32 OSC Remote Protocol" 4.02,
# pp. 86-91 (the XR18 runs the same algorithms in FX slots 1-4).
_PRE = _lin(1, "PreDly", 0, 200, "ms")
_DAMP = _log(4, "Damping", 1000, 20000, "Hz")
_DIFF = _lin(5, "Diffuse", 1, 30)
_LEVEL = _lin(6, "Level", -12, 12, "dB")
_LOCUT = _log(7, "LoCut", 10, 500, "Hz")
_HICUT = _log(8, "HiCut", 200, 20000, "Hz")

REVERB_TYPES: dict[int, FxType] = {t.index: t for t in (
    _t(0, "HALL", "Hall Reverb",
       _PRE, _log(2, "Decay", 0.2, 5, "s"), _lin(3, "Size", 2, 100), _DAMP,
       _DIFF, _LEVEL, _LOCUT, _HICUT, _log(9, "BassMul", 0.5, 2),
       _lin(10, "Spread", 0, 50), _lin(11, "Shape", 0, 250),
       _lin(12, "ModSpd", 0, 100)),
    _t(1, "AMBI", "Ambience",
       _PRE, _log(2, "Decay", 0.2, 7.3, "s"), _lin(3, "Size", 2, 100), _DAMP,
       _DIFF, _LEVEL, _LOCUT, _HICUT, _lin(9, "Mod", 0, 100),
       _lin(10, "TailGn", 0, 100)),
    _t(2, "RPLT", "Rich Plate",
       _PRE, _log(2, "Decay", 0.3, 29, "s"), _lin(3, "Size", 4, 39), _DAMP,
       _DIFF, _LEVEL, _LOCUT, _HICUT, _log(9, "BassMul", 0.25, 4),
       _lin(10, "Spread", 0, 50), _lin(11, "Attack", 0, 100),
       _lin(12, "Spin", 0, 100), _lin(13, "EchoL", 0, 1200, "ms"),
       _lin(14, "EchoR", 0, 1200, "ms"), _lin(15, "EchFbL", -100, 100),
       _lin(16, "EchFbR", -100, 100)),
    _t(3, "ROOM", "Room Reverb",
       _PRE, _log(2, "Decay", 0.3, 29, "s"), _lin(3, "Size", 4, 72), _DAMP,
       _DIFF, _LEVEL, _LOCUT, _HICUT, _log(9, "BassMul", 0.25, 4),
       _lin(10, "Spread", 0, 50), _lin(11, "Shape", 0, 250),
       _lin(12, "Spin", 0, 100), _lin(13, "EchoL", 0, 1200, "ms"),
       _lin(14, "EchoR", 0, 1200, "ms"), _lin(15, "EchFbL", -100, 100),
       _lin(16, "EchFbR", -100, 100)),
    _t(4, "CHAM", "Chamber",
       _PRE, _log(2, "Decay", 0.3, 29, "s"), _lin(3, "Size", 4, 72), _DAMP,
       _DIFF, _LEVEL, _LOCUT, _HICUT, _log(9, "BassMul", 0.25, 4),
       _lin(10, "Spread", 0, 50), _lin(11, "Shape", 0, 250),
       _lin(12, "Spin", 0, 100), _lin(13, "ReflL", 0, 500, "ms"),
       _lin(14, "ReflR", 0, 500, "ms"), _lin(15, "RflGnL", 0, 100),
       _lin(16, "RflGnR", 0, 100)),
    _t(5, "PLAT", "Plate Reverb",
       _PRE, _log(2, "Decay", 0.2, 10, "s"), _lin(3, "Size", 2, 100), _DAMP,
       _DIFF, _LEVEL, _LOCUT, _HICUT, _log(9, "BassMul", 0.5, 2),
       _log(10, "Xover", 10, 500, "Hz"), _lin(11, "Mod", 0, 50),
       _lin(12, "ModSpd", 0, 100)),
    _t(6, "VREV", "Vintage Reverb",
       _lin(1, "PreDly", 0, 120, "ms"), _log(2, "Decay", 0.3, 4.5, "s"),
       _lin(3, "Mod", 0, 10), _enum(4, "Vintage", "OFF", "ON"),
       _enum(5, "Positn", "FRONT", "REAR"), _LEVEL, _LOCUT, _HICUT,
       _log(9, "LoMult", 0.5, 2), _log(10, "HiMult", 0.25, 1)),
    _t(7, "VRM", "Vintage Room",
       _lin(1, "RevDly", 0, 20), _log(2, "Decay", 0.1, 20, "s"),
       _lin(3, "Size", 0, 10), _lin(4, "Density", 1, 30),
       _lin(5, "ERLevel", 0, 190), _LEVEL, _log(7, "LoMult", 0.1, 10),
       _log(8, "HiMult", 0.1, 10), _log(9, "LoCut", 10, 500, "Hz"),
       _log(10, "HiCut", 200, 20000, "Hz"), _lin(11, "ERLeft", 0, 10),
       _lin(12, "ERRight", 0, 10), _enum(13, "Freeze", "OFF", "ON")),
    _t(8, "GATE", "Gated Reverb",
       _PRE, _log(2, "Decay", 140, 1000, "ms"), _lin(3, "Attack", 0, 30),
       _lin(4, "Density", 1, 30), _lin(5, "Spread", 0, 100), _LEVEL,
       _LOCUT, _HICUT, _lin(9, "HiShvGn", -30, 0, "dB"),
       _lin(10, "Diffuse", 1, 30)),
    _t(9, "RVRS", "Reverse Reverb",
       _PRE, _log(2, "Decay", 140, 1000, "ms"), _lin(3, "Rise", 0, 50),
       _lin(4, "Diffuse", 1, 30), _lin(5, "Spread", 1, 100), _LEVEL,
       _LOCUT, _HICUT, _lin(9, "HiShvGn", -30, 0, "dB")),
)}

# Order knob 8 steps through.
REVERB_ORDER = sorted(REVERB_TYPES)

# Full FX type enumeration (Maillot p. 39), so a non-reverb type can be
# named in the log before it is overwritten.
ALL_TYPE_CODES = (
    "HALL AMBI RPLT ROOM CHAM PLAT VREV VRM GATE RVRS DLY 3TAP 4TAP CRS "
    "FLNG PHAS DIMC FILT ROTA PAN SUB D/RV CR/R FL/R D/CR D/FL MODD GEQ2 "
    "GEQ TEQ2 TEQ DES2 DES P1A P1A2 PQ5 PQ5S WAVD LIM CMB CMB2 FAC FAC1M "
    "FAC2 LEC LEC2 ULC ULC2 ENH2 ENH EXC2 EXC IMG EDI SON AMP2 AMP DRV2 "
    "DRV PIT2 PIT"
).split()

# Default knob 2..8 -> parameter numbers, per reverb type.
DEFAULT_KNOB_MAPS: dict[int, list] = {
    0: [1, 2, 3, 4, 5, 7, 8],     # HALL: PreDly Decay Size Damping Diffuse LoCut HiCut
    1: [1, 2, 3, 4, 5, 7, 8],     # AMBI
    2: [1, 2, 3, 4, 5, 7, 8],     # RPLT
    3: [1, 2, 3, 4, 5, 7, 8],     # ROOM
    4: [1, 2, 3, 4, 5, 7, 8],     # CHAM
    5: [1, 2, 3, 4, 5, 7, 8],     # PLAT
    6: [1, 2, 3, 7, 8, 9, 10],    # VREV: PreDly Decay Mod LoCut HiCut LoMult HiMult
    7: [1, 2, 3, 4, 5, 9, 10],    # VRM: RevDly Decay Size Density ERLevel LoCut HiCut
    8: [1, 2, 3, 4, 5, 7, 8],     # GATE
    9: [1, 2, 3, 4, 5, 7, 8],     # RVRS
}


def resolve_type(raw) -> int:
    """Config value ("Vintage Reverb", "VREV", 6) -> reverb type index."""
    if isinstance(raw, bool):
        raise ValueError(f"bad FX type: {raw!r}")
    if isinstance(raw, int):
        if raw in REVERB_TYPES:
            return raw
        raise ValueError(f"FX type {raw} is not a reverb (reverbs are 0-9)")
    s = str(raw).strip().lower()
    if s.isdigit():
        return resolve_type(int(s))
    for t in REVERB_TYPES.values():
        if s in (t.code.lower(), t.name.lower()):
            return t.index
    raise ValueError(f"unknown reverb type {raw!r}")


def type_code(index) -> str:
    if index is not None and 0 <= index < len(ALL_TYPE_CODES):
        return ALL_TYPE_CODES[index]
    return "?"


def type_label(index) -> str:
    if index is None:
        return "?"
    if 0 <= index < len(ALL_TYPE_CODES):
        return f"{index}:{ALL_TYPE_CODES[index]}"
    return str(index)


# ----- Config ----------------------------------------------------------

@dataclass
class FxModeConfig:
    enabled: bool = False
    hold_s: float = 1.0
    fx_slot: int = 1
    channel: int = 1
    default_type: int = 6
    keep_current_reverb: bool = True
    step_types_with_knob8: bool = True
    color: str = "cyan"
    top_text: str = "dark"
    bottom_text: str = "dark"
    # reverb type index -> 7 parameter numbers for knobs 2..8 (None = unused)
    knob_maps: dict = field(default_factory=lambda: {
        k: list(v) for k, v in DEFAULT_KNOB_MAPS.items()})


_COLOR_NAMES = ("black", "red", "green", "yellow", "blue", "magenta",
                "cyan", "white")


def load_config(raw) -> FxModeConfig:
    """Parse the fx_mode: section. Missing section -> disabled."""
    if not raw:
        return FxModeConfig()
    if not isinstance(raw, dict):
        raise ValueError("config: fx_mode must be a mapping")
    c = FxModeConfig(
        enabled=bool(raw.get("enabled", False)),
        hold_s=float(raw.get("hold_s", 1.0)),
        fx_slot=int(raw.get("fx_slot", 1)),
        channel=int(raw.get("channel", 1)),
        default_type=resolve_type(raw.get("default_type", "Vintage Reverb")),
        keep_current_reverb=bool(raw.get("keep_current_reverb", True)),
        step_types_with_knob8=bool(raw.get("step_types_with_knob8", True)),
        color=str(raw.get("color", "cyan")).lower(),
        top_text=str(raw.get("top_text", "dark")).lower(),
        bottom_text=str(raw.get("bottom_text", "dark")).lower(),
    )
    if not 1 <= c.fx_slot <= 4:
        raise ValueError(f"config: fx_mode.fx_slot must be 1-4, got {c.fx_slot}")
    if not 1 <= c.channel <= 16:
        raise ValueError(f"config: fx_mode.channel must be 1-16, got {c.channel}")
    if c.color not in _COLOR_NAMES:
        raise ValueError(f"config: fx_mode.color {c.color!r} is not one of "
                         f"{', '.join(_COLOR_NAMES)}")
    if not 0.2 <= c.hold_s <= 5.0:
        raise ValueError(f"config: fx_mode.hold_s must be 0.2-5.0, got {c.hold_s}")
    for k, v in (raw.get("knob_maps") or {}).items():
        idx = resolve_type(k)
        if not isinstance(v, (list, tuple)) or len(v) != 7:
            raise ValueError(
                f"config: fx_mode.knob_maps[{k!r}] must list 7 parameter "
                f"numbers (knobs 2-8)")
        params = REVERB_TYPES[idx].params
        for p in v:
            if p is not None and p not in params:
                raise ValueError(
                    f"config: fx_mode.knob_maps[{k!r}]: {REVERB_TYPES[idx].code} "
                    f"has no parameter {p} (valid: {sorted(params)})")
        c.knob_maps[idx] = [None if p is None else int(p) for p in v]
    return c


# ----- Controller ------------------------------------------------------

_MAX_DELTA = MAX_DELTA
# An echo of our own ring write comes back within this many raw steps.
_RING_SNAP = 4
# Encoder ticks per enum step.
_ENUM_TICKS = 6
# Wait for a non-reverb's parameters before overwriting (so they're logged).
TYPE_CHECK_DELAY_S = 0.3
# 0 dB on the X-Air fader law.
_ZERO_DB = 0.75
_threading_scheduler = threading_scheduler


class FxMode(KnobEditor):
    """State machine for FX mode (knob 1 held)."""

    kind = "fx"

    def __init__(self, cfg: FxModeConfig, link,
                 ring_writer: Callable[[int, int], None],
                 scribble_writer: Callable[..., None],
                 restore: Callable[[], None],
                 scheduler: Callable = threading_scheduler,
                 clock: Callable[[], float] = time.monotonic,
                 before_enter: Optional[Callable[[], None]] = None):
        super().__init__(link, ring_writer, scribble_writer, restore,
                         scheduler, clock, before_enter)
        self.cfg = cfg

        s, ch = cfg.fx_slot, cfg.channel
        self.addr_type = f"/fx/{s}/type"
        self.addr_insert = f"/fx/{s}/insert"
        self.addr_par_prefix = f"/fx/{s}/par/"
        self.addr_send = f"/ch/{ch:02d}/mix/{6 + s:02d}/level"
        self._re_send = re.compile(rf"^/ch/{ch:02d}/mix/{6 + s:02d}(?:/level)?$")
        self.addr_rtn_on = f"/rtn/{s}/mix/on"
        self.addr_rtn_lr = f"/rtn/{s}/mix/lr"
        self.addr_rtn_fader = f"/rtn/{s}/mix/fader"
        self.addr_fxsend_on = f"/fxsend/{s}/mix/on"
        self.addr_fxsend_fader = f"/fxsend/{s}/mix/fader"
        self._route_addrs = (self.addr_rtn_on, self.addr_rtn_lr,
                             self.addr_rtn_fader, self.addr_fxsend_on,
                             self.addr_fxsend_fader)

        self.fx_type: Optional[int] = None
        self.params: dict[int, float] = {}    # par num -> raw mixer value
        self.send: Optional[float] = None
        self.route: dict[str, float] = {}
        self.insert: Optional[int] = None
        self._pending_type_check = False
        self._pending_route: set = set()
        self._enum_acc: dict[int, float] = {}

    @property
    def hold_s(self) -> float:
        return self.cfg.hold_s

    def _colours(self, strip: int) -> tuple:
        return (self.cfg.color, self.cfg.top_text, self.cfg.bottom_text)

    # ----- wiring -------------------------------------------------------

    def startup_queries(self) -> None:
        """Warm the cache so entering FX mode is instant."""
        self.link.query_address(self.addr_type)
        self.link.query_address(self.addr_insert)
        self.link.query_address(self.addr_send)
        for a in self._route_addrs:
            self.link.query_address(a)
        self._query_params(64)

    def _query_params(self, count: int) -> None:
        for n in range(1, count + 1):
            self.link.query_address(f"{self.addr_par_prefix}{n:02d}")

    # ----- knob 1 hold / knob 8 push -------------------------------------

    def on_knob_push(self, strip: int, pressed: bool) -> bool:
        """Returns True if FX mode consumed the event."""
        if not self.cfg.enabled:
            return False
        with self._lock:
            if strip == 1:
                self._hold_push(pressed)
                return True
            if (strip == 8 and self.active and pressed
                    and self.cfg.step_types_with_knob8):
                self.step_type()
                return True
            if strip == 8 and self.active:
                return True   # knob 8 release in FX mode
        return False

    # ----- enter / exit -------------------------------------------------

    def _on_enter(self) -> None:
        self._enum_acc = {}
        logger.info("fx", "FX mode on", slot=self.cfg.fx_slot,
                    channel=self.cfg.channel,
                    cached_type=type_label(self.fx_type))
        if self.insert == 1:
            logger.warn("fx", "FX slot is set to insert; a send-reverb "
                        "will not be heard until it is a send effect",
                        slot=self.cfg.fx_slot)
        self._pending_type_check = True
        if self.fx_type is not None:
            self._check_type()
        else:
            self.link.query_address(self.addr_type)
            self._query_params(64)
        self._pending_route = set(self._route_addrs)
        for a in self._route_addrs:
            if a in self.route:
                self._check_route(a)
            else:
                self.link.query_address(a)
        if self.send is None:
            self.link.query_address(self.addr_send)

    def _on_exit(self) -> None:
        self._pending_type_check = False
        self._pending_route = set()
        logger.info("fx", "FX mode off", type=type_label(self.fx_type))

    def _check_type(self) -> None:
        if not self._pending_type_check or self.fx_type is None:
            return
        self._pending_type_check = False
        cur = self.fx_type
        want = self.cfg.default_type
        if cur == want:
            return
        if cur in REVERB_TYPES and self.cfg.keep_current_reverb:
            logger.info("fx", "keeping the reverb already on the FX slot",
                        type=type_label(cur))
            return
        if cur not in REVERB_TYPES:
            logger.warn("fx", "overwriting non-reverb FX; old settings follow",
                        old_type=type_label(cur),
                        old_params={f"{n:02d}": self.params[n]
                                    for n in sorted(self.params)})
        else:
            logger.info("fx", "replacing reverb with configured type",
                        old_type=type_label(cur), new_type=type_label(want))
        self._set_type(want)

    def _check_type_locked(self) -> None:
        with self._lock:
            if self.active:
                self._check_type()

    def _check_route(self, addr: str) -> None:
        if addr not in self._pending_route or addr not in self.route:
            return
        self._pending_route.discard(addr)
        v = self.route[addr]
        if addr.endswith("/on") or addr.endswith("/lr"):
            if int(round(v)) != 1:
                self._write(addr, 1)
                self.route[addr] = 1
                logger.info("fx", "switched on for FX return", address=addr)
        elif v <= 0.0:
            self._write(addr, _ZERO_DB)
            self.route[addr] = _ZERO_DB
            logger.info("fx", "raised from -oo to 0 dB", address=addr)

    # ----- type stepping ------------------------------------------------

    def step_type(self) -> None:
        with self._lock:
            cur = self.fx_type
            if cur in REVERB_ORDER:
                nxt = REVERB_ORDER[(REVERB_ORDER.index(cur) + 1) % len(REVERB_ORDER)]
            else:
                nxt = self.cfg.default_type
            logger.info("fx", "step reverb type",
                        old_type=type_label(cur), new_type=type_label(nxt))
            self._set_type(nxt)

    def _set_type(self, index: int) -> None:
        self._write(self.addr_type, int(index))
        self._apply_type(index)

    def _apply_type(self, index: int) -> None:
        """New type on the slot: the old parameter values no longer mean
        anything, so drop them and ask the mixer for the new ones."""
        self.fx_type = index
        self.params = {}
        self._enum_acc = {}
        t = REVERB_TYPES.get(index)
        self._query_params(max(t.params) if t else 64)
        if self.active:
            self._paint_all(force=True)

    # ----- mixer -> bridge ---------------------------------------------

    def on_osc(self, address: str, args) -> None:
        """Raw listener for every mixer message (query replies and
        /xremote notifications)."""
        if not args:
            return
        raw = args[0]
        if isinstance(raw, (str, bytes)):
            return
        with self._lock:
            if address == self.addr_type:
                idx = int(raw)
                if self.fx_type is None:
                    # First sighting: parameters already cached (or on
                    # their way) belong to this type -- keep them.
                    self.fx_type = idx
                    if self.active:
                        self._paint_all(force=True)
                elif idx != self.fx_type:
                    self._apply_type(idx)
                if self._pending_type_check and idx not in REVERB_TYPES:
                    # Give the old parameters a moment to arrive so they
                    # can be logged before the slot is overwritten.
                    self._schedule(TYPE_CHECK_DELAY_S, self._check_type_locked)
                else:
                    self._check_type()
            elif address.startswith(self.addr_par_prefix):
                try:
                    n = int(address[len(self.addr_par_prefix):])
                except ValueError:
                    return
                self.params[n] = raw
                self._enum_acc.pop(n, None)
                self._refresh_param(n)
            elif self._re_send.match(address):
                self.send = min(1.0, max(0.0, float(raw)))
                if self.active:
                    self._paint_strip(1)
            elif address in self._route_addrs:
                self.route[address] = float(raw)
                self._check_route(address)
            elif address == self.addr_insert:
                self.insert = int(raw)

    def _refresh_param(self, n: int) -> None:
        if not self.active:
            return
        for knob, p in self._knob_params().items():
            if p is not None and p.num == n:
                self._paint_strip(knob)

    # ----- knobs --------------------------------------------------------

    def _knob_params(self) -> dict[int, Optional[ParamDef]]:
        t = REVERB_TYPES.get(self.fx_type)
        out: dict[int, Optional[ParamDef]] = {k: None for k in range(2, 9)}
        if t is None:
            return out
        nums = self.cfg.knob_maps.get(t.index, DEFAULT_KNOB_MAPS[t.index])
        for knob, num in zip(range(2, 9), nums):
            out[knob] = t.params.get(num) if num is not None else None
        return out

    def on_knob_turn(self, strip: int, raw: int) -> bool:
        """Returns True if FX mode consumed the event."""
        if not self.active:
            return False
        with self._lock:
            d = self._delta(strip, raw)
            if d == 0:
                return True
            if strip == 1:
                if self.send is None:
                    self.link.query_address(self.addr_send)
                    return True
                self.send = min(1.0, max(0.0, self.send + d / 127.0))
                self._write(self.addr_send, float(self.send))
                logger.info("fx", "send", channel=self.cfg.channel,
                            send=f"{self.send:.3f}", db=format_level(self.send))
                self._paint_strip(1, ring=False)
                return True
            p = self._knob_params().get(strip)
            if p is None:
                return True
            cur = self.params.get(p.num)
            if cur is None:
                self.link.query_address(f"{self.addr_par_prefix}{p.num:02d}")
                return True
            if p.scale == "enum":
                acc = self._enum_acc.get(p.num, float(int(cur))) + d / _ENUM_TICKS
                acc = min(float(len(p.choices) - 1), max(0.0, acc))
                self._enum_acc[p.num] = acc
                new = int(round(acc))
                if new == int(cur):
                    return True
            else:
                new = min(1.0, max(0.0, float(cur) + d / 127.0))
            self.params[p.num] = new
            self._write(f"{self.addr_par_prefix}{p.num:02d}", new)
            logger.info("fx", "param", type=type_label(self.fx_type),
                        par=p.num, name=p.label, value=p.format(new))
            self._paint_strip(strip, ring=False)
            return True

    # ----- display ------------------------------------------------------

    def _strip_view(self, strip: int):
        """(top, bottom, ring 0..127 or None) for a strip in FX mode."""
        if strip == 1:
            top = type_code(self.fx_type)
            ring = None if self.send is None else int(round(self.send * 127))
            return top, format_level(self.send), ring
        p = self._knob_params().get(strip)
        if p is None:
            return "", "", 0
        raw = self.params.get(p.num)
        if raw is None:
            return p.label, "--", None
        if p.scale == "enum":
            n = max(1, len(p.choices) - 1)
            ring = int(round(int(raw) / n * 127))
        else:
            ring = int(round(min(1.0, max(0.0, float(raw))) * 127))
        return p.label, p.format(raw), ring
