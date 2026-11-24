"""EQ mode: knob 2 held ~1 s turns the Extender's knobs into a low/mid/
high EQ editor for one XR18 channel (default: channel 1, "Headmic 1").

Facts used here (addresses, value laws, band types, step sizes) are in
docs/FX-NOTES.md, section "Channel EQ", with their sources.

  EQ on/off         /ch/NN/eq/on            int 0|1
  band type         /ch/NN/eq/B/type        int 0..5  LCut LShv PEQ VEQ HShv HCut
  band frequency    /ch/NN/eq/B/f           float 0..1  log 20..20000 Hz, 201 steps
  band gain         /ch/NN/eq/B/g           float 0..1  lin -15..+15 dB, 121 steps
  band Q            /ch/NN/eq/B/q           float 0..1  log 10..0.3 (reversed), 72 steps
  low cut on/off    /ch/NN/preamp/hpon      int 0|1
  low cut freq      /ch/NN/preamp/hpf       float 0..1  log 20..lowcut_max_hz, 101 steps

Turns are relative, like FX mode: each encoder tick moves the value by a
fixed musical step (gain_step_db, freq_step_octaves, q_steps), snapped to
the mixer's own grid. Only the channel's EQ and low cut are ever written.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import logger
from .knob_editor import KnobEditor, threading_scheduler

# ----- facts -------------------------------------------------------------

EQ_TYPES = ("LCut", "LShv", "PEQ", "VEQ", "HShv", "HCut")
LCUT, LSHV, PEQ, VEQ, HSHV, HCUT = range(6)
_NO_GAIN = (LCUT, HCUT)          # cut filters ignore the gain value

GAIN_MIN_DB, GAIN_MAX_DB, GAIN_POINTS = -15.0, 15.0, 121
FREQ_MIN_HZ, FREQ_MAX_HZ, FREQ_POINTS = 20.0, 20000.0, 201
Q_AT_0, Q_AT_1, Q_POINTS = 10.0, 0.3, 72
LOWCUT_MIN_HZ, LOWCUT_POINTS = 20.0, 101
GAIN_FLAT = 0.5                   # 0 dB on the gain law


def gain_db(w: float) -> float:
    return GAIN_MIN_DB + w * (GAIN_MAX_DB - GAIN_MIN_DB)


def freq_hz(w: float) -> float:
    return FREQ_MIN_HZ * (FREQ_MAX_HZ / FREQ_MIN_HZ) ** w


def q_value(w: float) -> float:
    return Q_AT_0 * (Q_AT_1 / Q_AT_0) ** w


def lowcut_hz(w: float, max_hz: float) -> float:
    return LOWCUT_MIN_HZ * (max_hz / LOWCUT_MIN_HZ) ** w


def format_hz(hz: float) -> str:
    if hz < 999.5:
        return f"{hz:.0f}Hz"
    if hz < 9995:
        return f"{hz / 1000:.2f}kHz"
    return f"{hz / 1000:.1f}kHz"


def format_db(db: float) -> str:
    return f"{db:+.1f}dB"


def format_q(q: float) -> str:
    return f"{q:.1f}" if q < 9.95 else f"{q:.0f}"


# ----- knob parameters ------------------------------------------------------

@dataclass(frozen=True)
class EqParam:
    key: str            # config name, e.g. "low_g"
    label: str          # scribble top row, <= 7 chars
    role: str           # "low" | "mid" | "high" | "lowcut"
    field: str          # "g" | "f" | "q" | "hpf"


PARAMS: dict[str, EqParam] = {p.key: p for p in (
    EqParam("low_g", "LO G", "low", "g"),
    EqParam("low_f", "LO F", "low", "f"),
    EqParam("low_q", "LO Q", "low", "q"),
    EqParam("mid_g", "MID G", "mid", "g"),
    EqParam("mid_f", "MID F", "mid", "f"),
    EqParam("mid_q", "MID Q", "mid", "q"),
    EqParam("high_g", "HI G", "high", "g"),
    EqParam("high_f", "HI F", "high", "f"),
    EqParam("high_q", "HI Q", "high", "q"),
    EqParam("lowcut_f", "LCUT", "lowcut", "hpf"),
)}

DEFAULT_KNOBS = {1: "low_g", 2: "low_f", 3: "mid_g", 4: "mid_f",
                 5: "mid_q", 6: "high_g", 7: "high_f", 8: "lowcut_f"}

# Band type wanted on entry per role (None = leave as is unless it is a
# cut filter, which has no gain; then it becomes a PEQ).
_WANT_TYPE = {"low": LSHV, "mid": None, "high": HSHV}


# ----- config -----------------------------------------------------------------

_COLOR_NAMES = ("black", "red", "green", "yellow", "blue", "magenta",
                "cyan", "white")


@dataclass
class EqModeConfig:
    enabled: bool = False
    hold_knob: int = 2
    hold_s: float = 1.0
    channel: int = 1
    low_band: int = 1
    mid_band: int = 3
    high_band: int = 4
    shelves_on_entry: bool = True
    color: str = "red"
    top_text: str = "dark"
    bottom_text: str = "dark"
    knobs: dict = field(default_factory=lambda: dict(DEFAULT_KNOBS))
    push_eq_onoff: Optional[int] = 8
    push_reset_band: Optional[int] = 5
    gain_step_db: float = 0.5
    freq_step_octaves: float = 0.1
    q_steps: int = 1
    lowcut_step_octaves: float = 0.1
    lowcut_max_hz: float = 400.0


def _opt_knob(raw, name):
    if raw is None or raw is False:
        return None
    n = int(raw)
    if not 1 <= n <= 8:
        raise ValueError(f"config: eq_mode.{name} must be a knob 1-8 or null, got {n}")
    return n


def load_config(raw) -> EqModeConfig:
    """Parse the eq_mode: section. Missing section -> disabled."""
    if not raw:
        return EqModeConfig()
    if not isinstance(raw, dict):
        raise ValueError("config: eq_mode must be a mapping")
    d = EqModeConfig()
    c = EqModeConfig(
        enabled=bool(raw.get("enabled", False)),
        hold_knob=int(raw.get("hold_knob", d.hold_knob)),
        hold_s=float(raw.get("hold_s", d.hold_s)),
        channel=int(raw.get("channel", d.channel)),
        low_band=int(raw.get("low_band", d.low_band)),
        mid_band=int(raw.get("mid_band", d.mid_band)),
        high_band=int(raw.get("high_band", d.high_band)),
        shelves_on_entry=bool(raw.get("shelves_on_entry", True)),
        color=str(raw.get("color", d.color)).lower(),
        top_text=str(raw.get("top_text", d.top_text)).lower(),
        bottom_text=str(raw.get("bottom_text", d.bottom_text)).lower(),
        push_eq_onoff=_opt_knob(raw.get("push_eq_onoff", d.push_eq_onoff),
                                "push_eq_onoff"),
        push_reset_band=_opt_knob(raw.get("push_reset_band", d.push_reset_band),
                                  "push_reset_band"),
        gain_step_db=float(raw.get("gain_step_db", d.gain_step_db)),
        freq_step_octaves=float(raw.get("freq_step_octaves", d.freq_step_octaves)),
        q_steps=int(raw.get("q_steps", d.q_steps)),
        lowcut_step_octaves=float(raw.get("lowcut_step_octaves",
                                          d.lowcut_step_octaves)),
        lowcut_max_hz=float(raw.get("lowcut_max_hz", d.lowcut_max_hz)),
    )
    if not 2 <= c.hold_knob <= 8:
        raise ValueError(f"config: eq_mode.hold_knob must be 2-8 (knob 1 is "
                         f"FX mode), got {c.hold_knob}")
    if not 0.2 <= c.hold_s <= 5.0:
        raise ValueError(f"config: eq_mode.hold_s must be 0.2-5.0, got {c.hold_s}")
    if not 1 <= c.channel <= 16:
        raise ValueError(f"config: eq_mode.channel must be 1-16, got {c.channel}")
    bands = (c.low_band, c.mid_band, c.high_band)
    if not all(1 <= b <= 4 for b in bands) or len(set(bands)) != 3:
        raise ValueError(f"config: eq_mode low/mid/high_band must be three "
                         f"different bands 1-4, got {bands}")
    if c.color not in _COLOR_NAMES:
        raise ValueError(f"config: eq_mode.color {c.color!r} is not one of "
                         f"{', '.join(_COLOR_NAMES)}")
    for name in ("push_eq_onoff", "push_reset_band"):
        k = getattr(c, name)
        if k is not None and k in (1, c.hold_knob):
            raise ValueError(f"config: eq_mode.{name} cannot be knob {k} "
                             f"(knob 1 = FX mode hold, knob {c.hold_knob} = "
                             f"EQ mode hold)")
    if (c.push_eq_onoff is not None
            and c.push_eq_onoff == c.push_reset_band):
        raise ValueError("config: eq_mode.push_eq_onoff and push_reset_band "
                         "are the same knob")
    if c.gain_step_db <= 0 or c.freq_step_octaves <= 0 or c.q_steps < 1 \
            or c.lowcut_step_octaves <= 0:
        raise ValueError("config: eq_mode step sizes must be positive")
    if not 100.0 <= c.lowcut_max_hz <= 1000.0:
        raise ValueError(f"config: eq_mode.lowcut_max_hz must be 100-1000, "
                         f"got {c.lowcut_max_hz}")
    if "knobs" in raw:
        knobs = {}
        for k, v in (raw.get("knobs") or {}).items():
            n = int(k)
            if not 1 <= n <= 8:
                raise ValueError(f"config: eq_mode.knobs entry {n} must be 1-8")
            if v is not None and str(v) not in PARAMS:
                raise ValueError(f"config: eq_mode.knobs[{n}] = {v!r} is not "
                                 f"one of {', '.join(PARAMS)}")
            knobs[n] = None if v is None else str(v)
        c.knobs = {n: knobs.get(n) for n in range(1, 9)}
    return c


# ----- controller --------------------------------------------------------------

class EqMode(KnobEditor):
    """State machine for EQ mode (knob 2 held)."""

    kind = "eq"

    def __init__(self, cfg: EqModeConfig, link,
                 ring_writer: Callable[[int, int], None],
                 scribble_writer: Callable[..., None],
                 restore: Callable[[], None],
                 scheduler: Callable = threading_scheduler,
                 clock: Callable[[], float] = time.monotonic,
                 before_enter: Optional[Callable[[], None]] = None):
        super().__init__(link, ring_writer, scribble_writer, restore,
                         scheduler, clock, before_enter)
        self.cfg = cfg
        ch = f"/ch/{cfg.channel:02d}"
        self.band_of = {"low": cfg.low_band, "mid": cfg.mid_band,
                        "high": cfg.high_band}
        self.addr_on = f"{ch}/eq/on"
        self.addr_hpon = f"{ch}/preamp/hpon"
        self.addr_hpf = f"{ch}/preamp/hpf"
        self._band_prefix = f"{ch}/eq/"
        # every address EQ mode reads (and the only ones it may write)
        self.addresses = [self.addr_on, self.addr_hpon, self.addr_hpf]
        for role in ("low", "mid", "high"):
            for f in ("type", "f", "g", "q"):
                self.addresses.append(self.band_addr(role, f))
        self._watched = set(self.addresses)
        self.vals: dict[str, float] = {}       # address -> raw mixer value
        self._pending_types: set = set()
        self.last_role: Optional[str] = None   # band of the last-turned knob

        # mixer grid steps per encoder tick
        self._ticks = {
            "g": max(1, round(cfg.gain_step_db
                              / ((GAIN_MAX_DB - GAIN_MIN_DB) / (GAIN_POINTS - 1)))),
            "f": max(1, round(cfg.freq_step_octaves * math.log(2)
                              / math.log(FREQ_MAX_HZ / FREQ_MIN_HZ)
                              * (FREQ_POINTS - 1))),
            "q": cfg.q_steps,
            "hpf": max(1, round(cfg.lowcut_step_octaves * math.log(2)
                                / math.log(cfg.lowcut_max_hz / LOWCUT_MIN_HZ)
                                * (LOWCUT_POINTS - 1))),
        }
        self._points = {"g": GAIN_POINTS, "f": FREQ_POINTS, "q": Q_POINTS,
                        "hpf": LOWCUT_POINTS}

    @property
    def hold_s(self) -> float:
        return self.cfg.hold_s

    def band_addr(self, role: str, fld: str) -> str:
        return f"{self._band_prefix}{self.band_of[role]}/{fld}"

    def param_addr(self, p: EqParam) -> str:
        if p.role == "lowcut":
            return self.addr_hpf
        return self.band_addr(p.role, p.field)

    def _colours(self, strip: int) -> tuple:
        top = self.cfg.top_text
        p = self._knob_param(strip)
        # EQ switched off (knob-8 push or X-Air Edit): the EQ strips show
        # their top row in the other text shade, so the bypass is visible.
        if (p is not None and p.role != "lowcut"
                and self.vals.get(self.addr_on, 1) == 0):
            top = "light" if top == "dark" else "dark"
        return (self.cfg.color, top, self.cfg.bottom_text)

    # ----- wiring -------------------------------------------------------

    def startup_queries(self) -> None:
        for a in self.addresses:
            self.link.query_address(a)

    # ----- pushes -------------------------------------------------------

    def on_knob_push(self, strip: int, pressed: bool) -> bool:
        """Returns True if EQ mode consumed the event."""
        if not self.cfg.enabled:
            return False
        with self._lock:
            if strip == self.cfg.hold_knob:
                self._hold_push(pressed)
                return True
            if not self.active:
                return False
            if strip == self.cfg.push_eq_onoff:
                if pressed:
                    self.toggle_eq()
                return True
            if strip == self.cfg.push_reset_band:
                if pressed:
                    self.reset_band()
                return True
        return False

    def toggle_eq(self) -> None:
        cur = self.vals.get(self.addr_on)
        if cur is None:
            self.link.query_address(self.addr_on)
            return
        new = 0 if int(cur) else 1
        self._set(self.addr_on, new)
        logger.info("eq", "EQ " + ("on" if new else "off (bypass)"),
                    channel=self.cfg.channel)
        self._paint_all(force=True)

    def reset_band(self) -> None:
        role = self.last_role
        if role not in ("low", "mid", "high"):
            logger.info("eq", "reset: turn a band's knob first", last=role)
            return
        addr = self.band_addr(role, "g")
        self._set(addr, GAIN_FLAT)
        logger.info("eq", "band reset to 0 dB", band=self.band_of[role],
                    role=role)
        self._refresh(addr)

    # ----- enter / exit -------------------------------------------------

    def _on_enter(self) -> None:
        logger.info("eq", "EQ mode on", channel=self.cfg.channel,
                    bands=f"low {self.cfg.low_band}, mid {self.cfg.mid_band}, "
                          f"high {self.cfg.high_band}")
        self.last_role = None
        self._pending_types = set()
        if self.cfg.shelves_on_entry:
            self._pending_types = {"low", "mid", "high"}
            for role in ("low", "mid", "high"):
                self._check_type(role)
        for a in self.addresses:
            if a not in self.vals:
                self.link.query_address(a)

    def _on_exit(self) -> None:
        self._pending_types = set()
        logger.info("eq", "EQ mode off", channel=self.cfg.channel)

    def _check_type(self, role: str) -> None:
        if role not in self._pending_types:
            return
        addr = self.band_addr(role, "type")
        cur = self.vals.get(addr)
        if cur is None:
            return              # asked for on entry; checked on arrival
        self._pending_types.discard(role)
        cur = int(cur)
        want = _WANT_TYPE[role]
        if want is None:
            want = PEQ if cur in _NO_GAIN else cur
        if cur == want:
            return
        logger.warn("eq", "band type changed so the gain knob works; old "
                    "type follows", band=self.band_of[role], role=role,
                    old_type=f"{cur}:{_type_name(cur)}",
                    new_type=f"{want}:{_type_name(want)}",
                    freq=format_hz(freq_hz(float(
                        self.vals.get(self.band_addr(role, "f"), 0.0)))))
        self._set(addr, want)

    # ----- mixer -> bridge ---------------------------------------------

    def on_osc(self, address: str, args) -> None:
        if address not in self._watched or not args:
            return
        raw = args[0]
        if isinstance(raw, (str, bytes)):
            return
        with self._lock:
            self.vals[address] = raw
            if self.active and address.endswith("/type"):
                for role in ("low", "mid", "high"):
                    if address == self.band_addr(role, "type"):
                        self._check_type(role)
            self._refresh(address)

    def _refresh(self, address: str) -> None:
        if not self.active:
            return
        if address == self.addr_on:
            self._paint_all(force=True)
            return
        for strip in range(1, 9):
            p = self._knob_param(strip)
            if p is None:
                continue
            if self.param_addr(p) == address or (
                    p.role == "lowcut" and address == self.addr_hpon):
                self._paint_strip(strip)

    # ----- knobs --------------------------------------------------------

    def _knob_param(self, strip: int) -> Optional[EqParam]:
        key = self.cfg.knobs.get(strip)
        return PARAMS.get(key) if key else None

    def on_knob_turn(self, strip: int, raw: int) -> bool:
        """Returns True if EQ mode consumed the event."""
        if not self.active:
            return False
        with self._lock:
            d = self._delta(strip, raw)
            if d == 0:
                return True
            p = self._knob_param(strip)
            if p is None:
                return True
            addr = self.param_addr(p)
            cur = self.vals.get(addr)
            if cur is None:
                self.link.query_address(addr)
                return True
            self.last_role = p.role
            if p.field == "q":
                d = -d          # knob right = higher Q (narrower)
            n = self._points[p.field] - 1
            grid = round(float(cur) * n) + d * self._ticks[p.field]
            new = min(n, max(0, grid)) / n
            if p.role == "lowcut" and int(self.vals.get(self.addr_hpon, 1)) == 0:
                self._set(self.addr_hpon, 1)
                logger.info("eq", "low cut switched on by turning its knob",
                            channel=self.cfg.channel)
            if abs(new - float(cur)) < 1e-9:
                self._paint_strip(strip, ring=False)
                return True
            self._set(addr, float(new))
            logger.info("eq", "param", channel=self.cfg.channel, name=p.label,
                        value=self._format(p))
            self._paint_strip(strip, ring=False)
            return True

    # ----- display ------------------------------------------------------

    def _format(self, p: EqParam) -> str:
        w = self.vals.get(self.param_addr(p))
        if w is None:
            return "--"
        w = min(1.0, max(0.0, float(w)))
        if p.field == "g":
            return format_db(gain_db(w))
        if p.field == "f":
            return format_hz(freq_hz(w))
        if p.field == "q":
            return format_q(q_value(w))
        if int(self.vals.get(self.addr_hpon, 1)) == 0:
            return "OFF"
        return format_hz(lowcut_hz(w, self.cfg.lowcut_max_hz))

    def _strip_view(self, strip: int):
        p = self._knob_param(strip)
        if p is None:
            return "", "", 0
        w = self.vals.get(self.param_addr(p))
        if w is None:
            return p.label, "--", None
        w = min(1.0, max(0.0, float(w)))
        if p.field == "q":
            w = 1.0 - w         # ring follows the Q number, like the knob
        return p.label, self._format(p), int(round(w * 127))

    # ----- outbound -----------------------------------------------------

    def _set(self, address: str, value) -> None:
        assert address in self._watched, address
        self.vals[address] = value
        self._write(address, value)


def _type_name(i: int) -> str:
    return EQ_TYPES[i] if 0 <= i < len(EQ_TYPES) else str(i)
