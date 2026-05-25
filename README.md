# midibridge

A USB-MIDI to OSC bridge between the **Behringer X-Touch Extender** (or compatible MCU-XT surface) and the **Behringer X-Air family** of digital mixers (XR12 / XR16 / XR18 / X18 / XR-USB).

Use it to control your X-Air mixer from a physical fader surface — motorized faders, mute LEDs, scribble strips, colored channel labels, the works. Runs on a Raspberry Pi (or any Linux box with USB) and just sits between the controller and the mixer on your network.

Since v0.5 the bridge also acts as a **transparent OSC proxy** for software clients (X-Air Edit, Mixing Station, your own OSC software). Point those apps at the Pi instead of the mixer and they all stay in sync with each other AND with the physical fader surface — no per-client config, no IP gymnastics.

```
   X-Touch Extender ──USB MIDI──┐
                                │
   X-Air Edit (Mac)  ──UDP──────┼──→  Raspberry Pi  ──UDP──→  XR18
                                │     (this software)         (mixer)
   Mixing Station (iOS)  ──UDP──┤
                                │
   any OSC client  ──UDP────────┘
```

Drag a fader on any surface — every other surface mirrors instantly.

## Features

- **Channel faders** with motor sync — move a fader anywhere, all surfaces follow
- **Mute buttons** with bidirectional LED feedback
- **Per-channel preamp gain (trim)** on knobs (toggleable lock for live use)
- **Colored scribble strips** — Behringer's proprietary SysEx protocol, fully documented in [PROTOCOL.md](PROTOCOL.md)
- **Bus mode** — press Select 1-6 to enter "sends on fader" mode for each aux bus, perfect for dialing in room-fill or monitor mixes during sound check
- **Master mirror** — make one fader drive multiple targets (e.g. Main LR + 6 aux bus masters in lockstep)
- **Transparent OSC proxy** — multiple software clients (X-Air Edit, Mixing Station, custom OSC apps) point at the Pi and stay in live sync with each other and with the hardware surface. Auto-discovery, no per-client config
- **Systemd service** for autostart on boot, with USB hotplug recovery and OSC port reuse on restart
- **Debug HTTP** on port 8080 — visit `/debug` for live state, event ring buffer, and system info
- **Operation vs Setup modes** — setup unlocks dangerous controls (trim, bus mode); operation mode locks them down so venue staff can use the surface safely

## Important: Extender mode

**The Extender must be in `Ctrl` mode, NOT `MC` mode.** This bridge speaks the Ctrl-mode protocol for faders, buttons, and LEDs. In MC mode the CC numbers shift and the bridge appears connected but nothing works in either direction — Extender input is ignored by the bridge, and the bridge's output (motors, LEDs, scribble strips) is ignored by the Extender.

If the bridge seems "connected but dead" — scribbles wiped, faders move on the Extender but XR18 doesn't respond, mixer changes don't drive motors — check the mode first. It's the single most common cause of "nothing works." The mode is shown on the small LCD next to the encoders. Refer to the X-Touch Extender manual for the button sequence to switch modes; it's a power-on combination involving Select buttons.

## Why this exists

The X-Touch Editor app (Behringer's official tool) can't talk to a standalone Extender — it expects an X-Touch main unit too. So the Extender's full feature set (motors, LEDs, scribbles) is essentially unreachable without custom software. The X-Air mixers expose a clean OSC API but nothing speaks MCU on the other side.

This bridge fills that gap. Plug the Extender into a Pi, point it at your mixer, get a real fader surface.

As a bonus, **the proxy also works around a long-standing limitation of X-Air Edit and Mixing Station**: those apps don't subscribe to mixer notifications properly, so they never see state changes made by other clients (other apps, hardware surfaces, automation). Through this bridge they do — because the bridge subscribes on their behalf and fans state out to every connected client.

## Hardware tested

- **Controller:** Behringer X-Touch Extender (USB MIDI, class-compliant)
- **Mixer:** Behringer XR18 (firmware 1.25)
- **Bridge:** Raspberry Pi 4 (8GB), Raspberry Pi OS Bookworm 64-bit
- **Network:** wired Ethernet (Wi-Fi works but adds latency)

Should also work with: XR12, XR16, X18, XR-USB-Mixer (same OSC protocol). The Behringer X-Touch (the full version) should mostly work too, but the scribble strip SysEx may differ — untested.

## Installation

### 1. Hardware

Connect the X-Touch Extender to your Pi via USB. Plug both the Pi and the XR18 into the same network. Note the XR18's IP address (set a DHCP reservation or static IP — the bridge needs to know where to find it).

### 2. Put the Extender into Ctrl mode (CRITICAL)

This bridge speaks Behringer's raw-CC protocol, not Mackie MCU. The Extender must be in **Ctrl mode** before the bridge can talk to it.

The Extender's mode is shown on the small LCD next to the encoders during startup. To change modes, refer to the X-Touch Extender manual for the exact button-combination during power-on for your firmware version — it's typically holding one or more Select buttons while powering on, then using the on-screen prompt to select **Ctrl** over **MC**. The setting persists across power cycles.

If the bridge appears not to control the Extender at all (no scribble strips, no motor response, no input from faders), check this first — the Extender may have reverted to MC mode.

### 3. Software

```bash
ssh pi@raspberrypi.local
git clone https://github.com/AmritusG/midibridge.git ~/midibridge
cd ~/midibridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
nano config.yaml  # edit XR18 IP address, customize mappings
```

### 4. Try it manually

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

## Connecting software clients (X-Air Edit, Mixing Station, etc.)

The bridge is a transparent OSC proxy on UDP port 10024. Any OSC software client can connect through it, and clients that connect through the bridge see each other's state changes live.

**Point the client at the Pi's IP, not the mixer's.**

- **X-Air Edit**: in the Connection panel, click Rescan. The Pi appears in the scan list with the same name as your mixer (Behringer's discovery protocol embeds the device name; the bridge passes that through but rewrites the IP). Pick the entry with the Pi's IP and click Connect. If the Pi doesn't show up at first, fully quit and relaunch Edit (it caches scan results aggressively — you'll need to do this whenever the bridge restarts too).
- **Mixing Station** (iOS/Android): in the mixer selection screen, manually enter the Pi's IP, or let auto-discovery find it and pick the Pi entry from the list.
- **Custom OSC software**: send your OSC packets to `<pi-ip>:10024` instead of `<mixer-ip>:10024`. Subscribe via `/xremote` if you want state notifications — the bridge will pass them along.

Connecting an app directly to the mixer still works fine — the bridge is opt-in, not mandatory. But apps connected directly won't see state changes made via other clients (this is a Behringer limitation, not the bridge's).

Auto-discovery learns about each client on its first packet and expires unused clients after 60 seconds of silence.

## Configuration

See `config.example.yaml` for the full annotated config. Key sections:

- **`xr18.ip`** — mixer IP. Either a literal address (e.g. `192.168.1.100`) or `auto` (the default, also used if the field is omitted) to discover the mixer on the LAN at startup via `/xinfo` broadcast. Static is faster to start and predictable; `auto` makes the install plug-and-play across networks.
- **`xr18.port`** / **`xr18.local_port`** — OSC ports (10024 for X-Air family, not 10023)
- **`midi.port_name`** — substring match for the Extender's MIDI port name
- **`faders`** — strip 1-8 → channel/lr/bus/dca target
- **`knobs`** — strip 1-8 → trim or other target (only active with `--setup`)
- **`mutes`** — strip 1-8 → channel mute or `mute_lr`
- **`scribble`** — strip labels, background colors, text shade
- **`mirror`** — fan out one fader's value to multiple targets (e.g. Main → all 6 aux buses)
- **`lock_trim_in_operation_mode`** — set `true` to require `--setup` for trim adjustments

### Plug-and-play deployment

With `xr18.ip: auto`, the bridge does broadcast discovery at startup and uses the first X-Air mixer that replies. Combined with DHCP for both the Pi and the mixer, the install requires zero IP configuration: power up on any network, and Edit, Mixing Station, and any other OSC client can scan-and-find the Pi (which in turn has scan-and-found the mixer). If multiple X-Air mixers are on the same LAN, the bridge picks whichever replies first — use a static IP in that case to pin to a specific one.

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
- X-Air Edit and Mixing Station don't honestly subscribe via `/xremote`, which is why state changes from other clients never reach them when connected directly to the mixer. The bridge fixes this by subscribing on their behalf and fanning state out.

## Known limitations

- **LED rings around encoders don't accept external control.** The CC 48-55 standard Mackie range is ignored; we probed CC ranges 16-127 on both MIDI channels 0 and 1 in Ctrl mode without finding the right one. The Extender lights its own rings locally during physical encoder turns, but external sync (e.g. when X-Air Edit changes a value) doesn't work. If you find the right protocol, please open an issue or PR.
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
  xr18.py              — transparent OSC proxy + /xremote subscriber
                         (raw UDP socket, byte-level forwarding)
  logger.py            — structured logger with ring buffer
  debug_http.py        — debug page server
  watchdog.py          — USB MIDI presence monitor
```

The OSC layer (`bridge/xr18.py`) does three things in a single read loop:

1. **Subscribes to the mixer** via `/xremote` keepalive every 8 seconds, so the bridge always receives parameter notifications. Those notifications drive the Extender's motors.
2. **Forwards client traffic** transparently in both directions. Packets from a client get forwarded to the mixer as raw bytes; packets from the mixer get forwarded to every known client as raw bytes. Original packet bytes are preserved so the bridge stays protocol-agnostic for OSC features it doesn't itself implement (X-Air's `/node/...` bulk-state replies, EQ/FX/dynamics parameters, anything else).
3. **Rewrites discovery replies** so the Pi appears as a separate mixer in client scan lists. Without this, clients would dedupe the bridge's relayed reply against the mixer's direct reply (same IP in payload) and show only the real mixer.

## Convenience aliases

For quick command-line management of the bridge from your laptop, [`scripts/aliases.sh`](scripts/aliases.sh) defines a handful of shell aliases. Append it to your `~/.zshrc` (or `~/.bashrc`), reload your shell, and you get:

| Alias | Action |
|---|---|
| `mdd` | SSH into the Pi |
| `mdd-cfg` | edit `config.yaml` on the Pi in nano |
| `mdd-restart` | restart the bridge service + show 10 fresh log lines |
| `mdd-status` | service health summary |
| `mdd-log` | tail journal live (Ctrl+C to exit) |
| `mdd-tail` | last 30 journal lines |
| `mdd-trim` | unlock trim knobs interactively for sound check |
| `mdd-lock` / `mdd-unlock` | permanently flip the `lock_trim_in_operation_mode` flag and restart |

Edit the three host/user/path variables at the top of the script to match your Pi's setup.

## Credits

The Behringer scribble strip protocol was reverse-engineered by [Aldaviva](https://github.com/Aldaviva) in the [BehringerXTouchExtender](https://github.com/Aldaviva/BehringerXTouchExtender) library. We use the byte format documented there, applied to the Extender in Ctrl mode.

Built by Amrit Stefan Anders Rosell with extensive help from Claude (Anthropic).

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Issues and PRs welcome. Particularly interested in:
- Encoder ring CC discovery (see Known Limitations)
- Confirmation/fixes for the full Behringer X-Touch (not Extender)
- Other X-Air mixer variants (X18, XR-USB, etc.)
- Other MCU-compatible controllers
