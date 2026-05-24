# Protocol notes

A reverse-engineering reference for the X-Touch Extender (USB MIDI side) and the Behringer X-Air family (OSC side). These are the gotchas and discoveries from building this bridge — preserved here so future hackers don't have to re-discover them.

## X-Air OSC

### Port

The X-Air family listens on UDP **10024**, not 10023. Some older documents and projects use 10023 (which is the X32 port). 10023 will silently fail — packets get sent, nothing happens.

### Asymmetric zero-padding

Channel addresses are zero-padded to two digits, but bus and DCA addresses are not:

| Address                  | Channel | Bus | DCA |
| ------------------------ | ------- | --- | --- |
| `/ch/01/mix/fader`       | ✓       |     |     |
| `/ch/1/mix/fader`        | ✗       |     |     |
| `/bus/1/mix/fader`       |         | ✓   |     |
| `/bus/01/mix/fader`      |         | ✗   |     |
| `/dca/1/fader`           |         |     | ✓   |
| `/headamp/01/gain`       | ✓       |     |     |

Use the right form or the mixer ignores the write.

### Value types per target family

| Target           | Address                    | Type    | Range                 |
| ---------------- | -------------------------- | ------- | --------------------- |
| Channel fader    | `/ch/NN/mix/fader`         | float   | 0.0–1.0               |
| Main LR fader    | `/lr/mix/fader`            | float   | 0.0–1.0               |
| Bus master       | `/bus/N/mix/fader`         | float   | 0.0–1.0               |
| DCA master       | `/dca/N/fader`             | float   | 0.0–1.0               |
| Preamp gain      | `/headamp/NN/gain`         | float   | 0.0–1.0 (= −12…+60 dB)|
| Channel send     | `/ch/NN/mix/MM`            | float   | 0.0–1.0               |
| Channel mute     | `/ch/NN/mix/on`            | int     | 0 or 1 (1 = unmuted)  |
| LR mute          | `/lr/mix/on`               | int     | 0 or 1                |

The mute value is inverted from intuition: `on=1` means "channel is on, audio passes," not "channel is muted."

### Notification address vs write address

When you change a channel send via OSC, you write to `/ch/01/mix/01`. When the mixer notifies subscribers of a change to that same parameter, it sends `/ch/01/mix/01/level` (with the trailing `/level`).

This bridge handles both: incoming addresses match `^/ch/(\d+)/mix/(\d+)(?:/level)?$`, outgoing writes use the short form.

The mixer occasionally also sends bare `/ch/NN/mix/MM` addresses with no value, as some kind of bundle preamble. These can be ignored silently.

### Subscriptions

To receive change notifications from the mixer, subscribe with `/xremote` (no args). The subscription times out after 10 seconds, so you must re-send `/xremote` periodically. This bridge sends one every 8 seconds.

`/xremote` covers most parameters but NOT everything. Notably:

- **Preamp gain (`/headamp/NN/gain`)** IS covered. Tested empirically.
- **Channel sends (`/ch/NN/mix/MM/level`)** ARE covered. Tested empirically.
- **Faders, mutes, bus masters, DCAs** ARE all covered.

Bundle messages (`#bundle` containers) work fine. The mixer also sends `/meters/N` streams at high rate — subscribe via `/batchsubscribe` if you need them.

### The mixer doesn't echo your own writes back

If you write `/ch/01/mix/fader` with value 0.5, the mixer does NOT send you a notification of that change (it does notify *other* subscribers). This means a bridge needs to optimistically update its own local cache when sending, otherwise its state will lag behind.

### `/M/` prefix

You may occasionally see addresses prefixed with `/M/` in packet captures (e.g. `/M/headamp/03/gain`). This is the mixer's "machine" namespace for state-broadcast bundle members. The bridge can ignore these; they're informational duplicates of the regular addresses.

### Discovery: `/xinfo` and `/status`

X-Air Edit and Mixing Station discover mixers on the LAN by broadcasting `/xinfo` (and sometimes `/status`) to the subnet broadcast address (e.g. `192.168.1.255:10024`) AND to limited broadcast (`255.255.255.255:10024`). The mixer replies to the source IP with:

```
/xinfo  ,ssss  <ip>  <name>  <model>  <firmware>
```

For example: `/xinfo ,ssss "192.168.1.100" "XR18-Bearbacka" "XR18V2" "1.25"`.

The `/status` reply has type tag `,sss` and is `<state> <ip> <name>` — e.g. `"active" "192.168.1.100" "XR18-Bearbacka"`.

The IP field in both replies is the mixer's view of its own IP. Clients dedupe scan results by this IP, which is why the bridge has to rewrite the IP field before relaying these replies — otherwise the relayed reply gets deduped against the mixer's direct reply and the bridge never appears as a separate scan entry.

### X-Air Edit and Mixing Station don't honestly subscribe

Despite both apps showing "Connected" in their UI, neither sends `/xremote` keepalives. They send writes (drag a fader, send `/ch/NN/mix/fader 0.5`) but never subscribe for notifications. This means a client connected directly to the mixer sees only its OWN changes — never changes made by other clients, automation, or hardware control surfaces.

The "Synchronize" button in X-Air Edit exists precisely because of this: it's a one-shot bulk read of mixer state via `/node/...` queries, used to manually refresh the UI.

The midibridge proxy fixes this by subscribing on these clients' behalf and fanning state out to them — so multiple Edit/MS instances + hardware surfaces stay in lockstep.

## Behringer X-Touch Extender USB MIDI

### MC mode vs Ctrl mode

The Extender supports two modes: **MC** (Mackie Control extender) and **Ctrl** (Behringer's own protocol). **This bridge requires Ctrl mode.** In MC mode, the bridge appears connected over USB but no commands flow in either direction — Extender output is on different CCs/notes that the bridge doesn't decode, and the bridge's writes are on CCs the Extender doesn't react to. The Behringer scribble strip SysEx (the `00 20 32` manufacturer ID block, see below) is the one thing that works in both modes.

To switch modes: power off the Extender, hold a Select button while powering on, and follow the on-LCD prompt to choose `Ctrl`. Setting persists across power cycles.

If the Extender appears unresponsive to the bridge (no scribble paint, no motor sync, faders don't drive the mixer, mixer changes don't drive motors), check this first.

### CC ranges (Ctrl mode)

The bridge speaks Ctrl mode, so all CC numbers below apply only when the Extender is in Ctrl mode:

| Control               | Direction | Note/CC base   | Per strip          |
| --------------------- | --------- | -------------- | ------------------ |
| Fader position        | TX (Pi→Ext) | CC 70-77 ch 0 | strip 1..8 = CC 70..77 |
| Fader move from user  | RX        | CC 70-77 ch 0  | strip 1..8 = CC 70..77 |
| Fader touch           | RX        | Note 104-111   | strip = note - 104+1 |
| Encoder turn (rel)    | RX        | CC 16-23       | + value 1-63 = right, 65-127 = left |
| Encoder press         | RX        | Note 32-39     |                    |
| Rec button            | RX        | Note 0-7       |                    |
| Solo button           | RX        | Note 8-15      |                    |
| Mute button           | RX        | Note 16-23     |                    |
| Select button         | RX        | Note 24-31     |                    |
| Rec LED               | TX        | Note 0-7       | vel 127 = lit      |
| Solo LED              | TX        | Note 8-15      | vel 127 = lit      |
| Mute LED              | TX        | Note 16-23     | vel 127 = lit      |
| Select LED            | TX        | Note 24-31     | vel 127 = lit      |

Fader values are 7-bit (0-127) in Ctrl mode, not 14-bit like Mackie's pitch-bend convention. This means motor positioning has 1 part in 128 resolution — perceptually smooth but less granular than a true MCU surface.

Note: verify the byte layout against your actual hardware by capturing MIDI; some firmware revisions may differ.

### Fader-touch events: Ctrl mode does NOT send them

Despite what the CC range table suggests (and despite some documentation), the Extender in Ctrl mode does NOT actually send fader-touch note events when you physically grab a fader. The bridge had to switch to inferring "user is holding this fader" from recent FaderMove rate (any FaderMove within the last 250 ms = touched) as a workaround.

This matters for echo suppression: the Extender ALSO echoes the bridge's own motor-fader writes back as FaderMove events. Without de-duplication, those echoes would loop back as mixer writes and create an infinite feedback. The bridge tracks `(midi_value, timestamp)` per strip on every motor write and silently drops any matching FaderMove arriving within 500 ms.

### Encoder ring LEDs — UNSOLVED

Sending CC 48 + value to the Extender (the Mackie V-Pot standard for ring control) does nothing. We probed CC 16, 32, 48, 64, 80, 96, 112 on both MIDI channels 0 and 1 with values 8, 41 (Mackie wrap), and 127. Nothing lit.

The Extender lights its own rings locally during physical encoder turns, but external sync from software is unsolved as of this writing. If you crack it, please PR.

### Scribble strips — SysEx

This is the big one. The Mackie Control standard uses `F0 00 00 66 ...` for scribble strip writes (manufacturer ID Mackie). The X-Touch Extender ignores those entirely.

The Extender uses **Behringer's own manufacturer ID `00 20 32`** with a proprietary command:

```
F0  00 20 32  15  4C  <track>  <colors>  <7 top bytes>  <7 bottom bytes>  F7
```

- **`track`**: 0-7 (zero-indexed strip)
- **`colors`** byte layout: `0b00LU0BBB`
  - `BBB` = background color (0=black, 1=red, 2=green, 3=yellow, 4=blue, 5=magenta, 6=cyan, 7=white)
  - `U` = upper text shade (0=dark, 1=light/negative)
  - `L` = lower text shade
- **`top` / `bottom`**: 7 ASCII bytes each, space-padded if shorter

This works in Ctrl mode, which matches the mode targeted by the [Aldaviva BehringerXTouchExtender library](https://github.com/Aldaviva/BehringerXTouchExtender) (where we got the format from, with credit). 23 bytes total fixed length.

Black-on-black is unreadable (LCD has no backlight contrast). Avoid it.

### Operating-mode quirks

The Extender does NOT respond to most CC writes immediately after power-on. Wait ~2 seconds after USB enumeration before sending state initialization, or scribble strips and LEDs may not paint.

## Open questions

1. **Ring LED CC.** What's the right CC + encoding for external ring LED control on the X-Touch Extender in Ctrl mode?
2. **Full X-Touch.** Does this bridge work end-to-end on the full Behringer X-Touch (not Extender)? Scribble strip format and mode behavior may differ.
3. **Channel meters.** The mixer streams `/meters/0` etc. via `/batchsubscribe`. Worth surfacing on the Extender's LED meter columns? (Untested.)

PRs welcome on any of these.
