# midibridge

A USB-MIDI to OSC bridge between the **Behringer X-Touch Extender** (or compatible MCU-XT surface) and the **Behringer X-Air family** of digital mixers (XR12 / XR16 / XR18 / X18 / XR-USB).

Use it to control your X-Air mixer from a physical fader surface — motorized faders, mute LEDs, scribble strips, colored channel labels, the works. Runs on a Raspberry Pi (or any Linux box with USB) and just sits between the controller and the mixer on your network.

```
   X-Touch Extender   <--USB MIDI-->   Raspberry Pi   <--OSC over UDP-->   XR18
   (motorized faders,                  (this software)                    (mixer)
    knobs, scribbles,
    mute/select LEDs)
```

## Features

- **Channel faders** with motor sync — move a fader anywhere, all surfaces follow
- **Mute buttons** with bidirectional LED feedback
- **Per-channel preamp gain (trim)** on knobs (toggleable lock for live use)
- **Colored scribble strips** — Behringer's proprietary SysEx protocol, fully documented in [PROTOCOL.md](PROTOCOL.md)
- **Bus mode** — press Select 1-6 to enter "sends on fader" mode for each aux bus, perfect for dialing in room-fill or monitor mixes during sound check
- **Master mirror** — make one fader drive multiple targets (e.g. Main LR + 6 aux bus masters in lockstep)
- **Systemd service** for autostart on boot, with USB hotplug recovery and OSC port reuse on restart
- **Debug HTTP** on port 8080 — visit `/debug` for live state, event ring buffer, and system info
- **Operation vs Setup modes** — setup unlocks dangerous controls (trim, bus mode); operation mode locks them down so venue staff can use the surface safely

## Why this exists

The X-Touch Editor app (Behringer's official tool) can't talk to a standalone Extender — it expects an X-Touch main unit too. So the Extender's full feature set (motors, LEDs, scribbles) is essentially unreachable without custom software. The X-Air mixers expose a clean OSC API but nothing speaks MCU on the other side.

This bridge fills that gap. Plug the Extender into a Pi, point it at your mixer, get a real fader surface.

## Hardware tested

- **Controller:** Behringer X-Touch Extender (USB MIDI, class-compliant)
- **Mixer:** Behringer XR18 (firmware 1.21)
- **Bridge:** Raspberry Pi 4 (8GB), Raspberry Pi OS Bookworm 64-bit
- **Network:** wired Ethernet (Wi-Fi works but adds latency)

Should also work with: XR12, XR16, X18, XR-USB-Mixer (same OSC protocol). The Behringer X-Touch (the full version) should mostly work too, but the scribble strip SysEx may differ — untested.

## Installation

### 1. Hardware

Connect the X-Touch Extender to your Pi via USB. Plug both the Pi and the XR18 into the same network. Note the XR18's IP address (set a DHCP reservation or static IP — the bridge needs to know where to find it).

### 2. Software

```bash
ssh pi@raspberrypi.local
git clone https://github.com/YOUR-USERNAME/midibridge.git ~/midibridge
cd ~/midibridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
nano config.yaml  # edit XR18 IP address, customize mappings
```

### 3. Try it manually

```bash
.venv/bin/python3 bridge.py --setup
```

`--setup` enables knobs and bus mode. Move a fader; the XR18 should respond. Move a channel in X-Air Edit; the Extender's fader should follow. Ctrl+C to stop.

If it works, install the systemd service:

```bash
./install-service.sh
sudo systemctl enable midibridge
sudo systemctl start midibridge
journalctl -u midibridge -f
```

The bridge now starts on boot and restarts on crash.

## Configuration

See `config.example.yaml` for the full annotated config. Key sections:

- **`xr18`** — mixer IP and OSC port (10024 for X-Air family, not 10023)
- **`midi.port_name`** — substring match for the Extender's MIDI port name
- **`faders`** — strip 1-8 → channel/lr/bus/dca target
- **`knobs`** — strip 1-8 → trim or other target (only active with `--setup`)
- **`mutes`** — strip 1-8 → channel mute or `mute_lr`
- **`scribble`** — strip labels, background colors, text shade
- **`mirror`** — fan out one fader's value to multiple targets (e.g. Main → all 6 aux buses)
- **`lock_trim_in_operation_mode`** — set `true` to require `--setup` for trim adjustments

## Bus mode (sends on fader)

Run the bridge with `--setup` (or use the included `unlock-trim.sh` wrapper). Press **Select 1** on the Extender. Scribble strips turn yellow, faders snap to channel-1-7's send levels into bus 1, and fader 8 controls bus 1's master. Press Select 1 again to return to Live mode. Press Select 3 to jump directly to bus 3 sends. The active bus's Select LED lights up.

This is how you dial in a room-fill or monitor mix per speaker without needing X-Air Edit on a laptop.

## Operation vs Setup modes

- **Operation mode** (`bridge.py` with no flag): faders and mutes work, knobs are locked, Select buttons do nothing. Safe for venue staff.
- **Setup mode** (`bridge.py --setup`): everything unlocked including bus mode. For sound engineers and installers.

The systemd service runs in operation mode. For temporary setup access, the included `unlock-trim.sh` stops the service, runs `--setup` interactively, and on Ctrl+C automatically restarts the locked service.

## Protocol notes

A few hard-won discoveries are documented in [PROTOCOL.md](PROTOCOL.md):

- The XR18 listens for OSC writes on port **10024**, not 10023 (despite some docs saying otherwise)
- Asymmetric zero-padding: `/ch/01/...` is padded, but `/bus/1/...` and `/dca/1/...` are not
- Behringer scribble strips use a proprietary SysEx format with manufacturer ID `00 20 32` (not the Mackie ID `00 00 66`)
- The mixer notifies subscribers of channel sends at `/ch/NN/mix/MM/level` (with trailing `/level`), but you write to `/ch/NN/mix/MM` (no suffix)
- Headamp gain notifications go through `/xremote` correctly but were initially missed because the protocol allows multiple address forms

## Known limitations

- **LED rings around encoders don't accept external control in MC mode.** The CC 48-55 standard Mackie range is ignored; we probed CC ranges 16-127 on both MIDI channels 0 and 1 without finding the right one. The Extender lights its own rings locally during physical encoder turns, but external sync (e.g. when X-Air Edit changes a value) doesn't work. If you find the right protocol, please open an issue or PR.
- The Behringer X-Touch (full version, not Extender) likely needs different SysEx for scribble strips and may need different CC ranges for some controls. Untested.
- The bridge has no UI beyond the debug HTTP page. Reconfiguration requires editing YAML and restarting.

## Architecture

```
bridge.py              — main entry, event loop, mode state machine
bridge/
  config.py            — YAML loader
  targets.py           — target type system (channel/bus/dca/trim/mute/send)
  decoder.py           — MIDI → event objects
  extender.py          — MIDI port management, motor/LED/scribble output
  xr18.py              — OSC client, /xremote keepalive, dispatcher
  logger.py            — structured logger with ring buffer
  debug_http.py        — debug page server
  watchdog.py          — USB MIDI presence monitor
```

## Credits

The Behringer scribble strip protocol was reverse-engineered by [Aldaviva](https://github.com/Aldaviva) in the [BehringerXTouchExtender](https://github.com/Aldaviva/BehringerXTouchExtender) library. We use the byte format documented there, confirmed empirically to also work in MC mode.

Built by Amrit Stefan Anders Rosell with extensive help from Claude (Anthropic).

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Issues and PRs welcome. Particularly interested in:
- Encoder ring CC discovery (see Known Limitations)
- Confirmation/fixes for the full Behringer X-Touch (not Extender)
- Other X-Air mixer variants (X18, XR-USB, etc.)
- Other MCU-compatible controllers
