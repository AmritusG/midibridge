"""Targets: what an Extender control points at on the XR18.

A target is one of:
  - int 1..16     channel fader
  - "lr"          main stereo LR bus fader
  - "bus_N" (1..6)  aux/bus master fader (drives AUX OUT 1..6)
  - "dca_N" (1..4)  DCA group master fader
  - "trim_N" (1..16)  channel preamp gain (input trim)
  - "mute_N" (1..16)  channel mute toggle (1=on/unmuted, 0=muted)
  - "mute_lr"      main LR mute toggle

A single source of truth: target -> OSC address, and back. The kind
of value (float 0..1 vs int 0/1) is determined by `target_value_kind`.

OSC ADDRESS MAP
================

  Target            OSC address                Value
  ------            -----------                -----
  int N (1..16)     /ch/{N:02d}/mix/fader     float 0.0..1.0
  "lr"              /lr/mix/fader              float 0.0..1.0
  "bus_N" (1..6)    /bus/{N}/mix/fader         float 0.0..1.0
  "dca_N" (1..4)    /dca/{N}/fader             float 0.0..1.0
  "trim_N" (1..16)  /headamp/{N:02d}/gain      float 0.0..1.0
  "mute_N" (1..16)  /ch/{N:02d}/mix/on         int 0|1 (1=on/unmuted)
  "mute_lr"         /lr/mix/on                 int 0|1 (1=on/unmuted)
"""

from __future__ import annotations

import re
from typing import Optional, Union

Target = Union[int, str]

# Target families (parsing config strings)
_RE_BUS_TARGET   = re.compile(r"^bus_(\d+)$")
_RE_DCA_TARGET   = re.compile(r"^dca_(\d+)$")
_RE_TRIM_TARGET  = re.compile(r"^trim_(\d+)$")
_RE_MUTE_TARGET  = re.compile(r"^mute_(\d+)$")
# Channel send level: how much of channel CC goes to bus BB
_RE_SEND_TARGET  = re.compile(r"^send_ch(\d+)_to_bus(\d+)$")

# Address families (parsing incoming OSC)
_RE_CH_ADDR     = re.compile(r"^/ch/(\d+)/mix/fader$")
_RE_BUS_ADDR    = re.compile(r"^/bus/(\d+)/mix/fader$")
_RE_DCA_ADDR    = re.compile(r"^/dca/(\d+)/fader$")
_RE_LR_ADDR     = re.compile(r"^/lr/mix/fader$")
_RE_TRIM_ADDR   = re.compile(r"^/headamp/(\d+)/gain$")
_RE_MUTE_ADDR   = re.compile(r"^/ch/(\d+)/mix/on$")
_RE_MUTE_LR_ADDR = re.compile(r"^/lr/mix/on$")
_RE_SEND_ADDR   = re.compile(r"^/ch/(\d+)/mix/(\d+)(?:/level)?$")


def parse_target(raw) -> Target:
    """Validate and normalize a config-supplied target. Raises ValueError."""
    if isinstance(raw, bool):
        raise ValueError(f"target must be int or string, got bool: {raw}")
    if isinstance(raw, int):
        if not 1 <= raw <= 16:
            raise ValueError(f"channel target out of range 1..16: {raw}")
        return raw
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s == "lr":
            return "lr"
        if s == "mute_lr":
            return "mute_lr"
        m = _RE_BUS_TARGET.match(s)
        if m:
            n = int(m.group(1))
            if not 1 <= n <= 6:
                raise ValueError(f"bus_N target out of range 1..6: {raw}")
            return f"bus_{n}"
        m = _RE_DCA_TARGET.match(s)
        if m:
            n = int(m.group(1))
            if not 1 <= n <= 4:
                raise ValueError(f"dca_N target out of range 1..4: {raw}")
            return f"dca_{n}"
        m = _RE_TRIM_TARGET.match(s)
        if m:
            n = int(m.group(1))
            if not 1 <= n <= 16:
                raise ValueError(f"trim_N target out of range 1..16: {raw}")
            return f"trim_{n}"
        m = _RE_MUTE_TARGET.match(s)
        if m:
            n = int(m.group(1))
            if not 1 <= n <= 16:
                raise ValueError(f"mute_N target out of range 1..16: {raw}")
            return f"mute_{n}"
        m = _RE_SEND_TARGET.match(s)
        if m:
            ch = int(m.group(1))
            bus = int(m.group(2))
            if not 1 <= ch <= 16:
                raise ValueError(f"send_chN_to_busM ch out of range 1..16: {raw}")
            if not 1 <= bus <= 6:
                raise ValueError(f"send_chN_to_busM bus out of range 1..6: {raw}")
            return f"send_ch{ch}_to_bus{bus}"
        if s.isdigit():
            n = int(s)
            if 1 <= n <= 16:
                return n
        raise ValueError(f"unrecognized target: {raw!r}")
    raise ValueError(f"target must be int or string, got {type(raw).__name__}: {raw!r}")


def target_to_address(target: Target) -> str:
    """target -> OSC address (for sending to mixer).

    Note the asymmetric zero-padding the XR18 uses:
      channels (1..16) are zero-padded to two digits
      buses (1..6) are NOT zero-padded
      DCAs (1..4) are NOT zero-padded
      headamp (1..16) IS zero-padded to two digits
    Confirmed by capturing X-Air Edit traffic.
    """
    if isinstance(target, int):
        return f"/ch/{target:02d}/mix/fader"
    if target == "lr":
        return "/lr/mix/fader"
    if target == "mute_lr":
        return "/lr/mix/on"
    m = _RE_BUS_TARGET.match(target)
    if m:
        return f"/bus/{int(m.group(1))}/mix/fader"
    m = _RE_DCA_TARGET.match(target)
    if m:
        return f"/dca/{int(m.group(1))}/fader"
    m = _RE_TRIM_TARGET.match(target)
    if m:
        return f"/headamp/{int(m.group(1)):02d}/gain"
    m = _RE_MUTE_TARGET.match(target)
    if m:
        return f"/ch/{int(m.group(1)):02d}/mix/on"
    m = _RE_SEND_TARGET.match(target)
    if m:
        ch = int(m.group(1))
        bus = int(m.group(2))
        return f"/ch/{ch:02d}/mix/{bus:02d}"
    raise ValueError(f"unknown target: {target!r}")


def address_to_target(address: str) -> Optional[Target]:
    """OSC address -> target (for dispatching incoming mixer updates).

    Returns None for addresses we don't track as targets (so they fall
    through to the default handler for debug logging).
    """
    m = _RE_CH_ADDR.match(address)
    if m:
        return int(m.group(1))
    if _RE_LR_ADDR.match(address):
        return "lr"
    if _RE_MUTE_LR_ADDR.match(address):
        return "mute_lr"
    m = _RE_BUS_ADDR.match(address)
    if m:
        return f"bus_{int(m.group(1))}"
    m = _RE_DCA_ADDR.match(address)
    if m:
        return f"dca_{int(m.group(1))}"
    m = _RE_TRIM_ADDR.match(address)
    if m:
        return f"trim_{int(m.group(1))}"
    m = _RE_MUTE_ADDR.match(address)
    if m:
        return f"mute_{int(m.group(1))}"
    m = _RE_SEND_ADDR.match(address)
    if m:
        ch = int(m.group(1))
        bus = int(m.group(2))
        # /ch/NN/mix/MM where MM is 01..06 for bus sends
        if 1 <= bus <= 6:
            return f"send_ch{ch}_to_bus{bus}"
    return None


def target_value_kind(target: Target) -> str:
    """Returns "float" for continuous params (0.0..1.0) or
    "bool" for on/off switches (sent as int 0 or 1)."""
    if isinstance(target, str):
        if target.startswith("mute_"):
            return "bool"
    return "float"


def target_label(target: Target) -> str:
    """Human-readable label for logs."""
    if isinstance(target, int):
        return f"ch{target:02d}"
    return target
