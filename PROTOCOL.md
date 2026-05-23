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

## Behringer X-Touch Extender USB MIDI

### MCU mode vs Ctrl mode

The Extender supports two modes: **MC** (Mackie Control extender) and **Ctrl** (raw CC). This bridge uses MC mode. Switch by holding the right Select button during power-on and following the LCD prompt.

### CC ranges

In MC mode:

| Control               | Direction | Note/CC base   | Per strip          |
| --------------------- | --------- | -------------- | ------------------ |
| Fader position        | TX (Pi→Ext) | Pitch bend ch N-1 | One channel per strip |
| Fader move from user  | RX        | Pitch bend ch N-1 |                   |
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

Note: the decoder.py file uses different note number bases internally; verify against your actual hardware by capturing MIDI.

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

This works in MC mode despite the protocol being documented for "Ctrl" mode in the [Aldaviva BehringerXTouchExtender library](https://github.com/Aldaviva/BehringerXTouchExtender) (which is where we got the format from, with credit). 23 bytes total fixed length.

Black-on-black is unreadable (LCD has no backlight contrast). Avoid it.

### Operating-mode quirks

The Extender does NOT respond to most CC writes immediately after power-on. Wait ~2 seconds after USB enumeration before sending state initialization, or scribble strips and LEDs may not paint.

The Vidvox "HAP Alpha" UI label maps to Hap5, not standalone HapA (this is unrelated to this project but useful to know if you're encoding video for DXV).

## Open questions

1. **Ring LED CC.** What's the right CC + encoding for external ring LED control on the X-Touch Extender in MC mode?
2. **Full X-Touch.** Does this bridge work end-to-end on the full Behringer X-Touch (not Extender)? Scribble strip format may differ.
3. **Channel meters.** The mixer streams `/meters/0` etc. via `/batchsubscribe`. Worth surfacing on the Extender's LED meter columns? (Untested.)

PRs welcome on any of these.
