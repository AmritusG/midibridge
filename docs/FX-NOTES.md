# FX mode and EQ mode: facts and sources

Everything FX mode relies on, with the source next to each fact. Sources:

- **[M]** Patrick-Gilles Maillot, *Unofficial X32/M32 OSC Remote Protocol*, v4.02 (Jan 2020). Page numbers are the document's own. The XR18 runs the X32's FX algorithms in its four FX slots, with the same type list and order (cross-checked with [C] and [O]).
- **[MX]** Maillot, `XAirSetScene.h` in github.com/pmaillot/X32-Behringer: the X-Air scene node list ("Per XAir OSC Spec 1.09").
- **[C]** `X-AirCmdList.txt`: the X-Air OSC command list (address, type, range, text), extracted from an X-Air app's source. Copy at github.com/SpotlightKid/xair-remote/doc/. It matches Behringer's *X AIR Parameters* list for firmware 1.14.
- **[O]** OpenFader `crates/proto-xair` (github.com/gladsonsam/OpenFader): an X-Air implementation whose owner checked it against a live XR18 on firmware 1.20.
- **[B]** Behringer, *X-TOUCH / X-TOUCH EXTENDER MIDI Mode Implementation* (musictribe CDN). The PDF link returned HTTP 500 during this pass. The facts below come from its indexed text and from the X-TOUCH ONE sheet of the same series.
- **[A]** Aldaviva/BehringerXTouchExtender (README and wiki).
- **[mb]** This repo's own findings (PROTOCOL.md, decoder.py), from captures on the venue hardware.

The Behringer X Air OSC PDF (manualmachine.com) and the behringer.world X-Air OSC wiki both returned HTTP 403 to automated fetches, so they were not used directly. [C] carries the same table.

## a. Channel 1 → FX 1 send level

| Fact | Value | Source |
|---|---|---|
| Channel sends per channel | `/ch/NN/mix/01..10`: 01-06 = bus 1-6, **07-10 = FX 1-4** | [O] `mix.rs` ("fx1..4 map to wire slots 07..=10", XR18 FW 1.20); [MX] lists `/ch/01/mix/01..10` |
| Ch 1 → FX 1 send | **`/ch/01/mix/07/level`**, float 0..1 | [C] `/ch/01/mix/01/level,f,0.0-1.0,"-oo - +10"` (the same block applies to 07) |
| Scaling | X-Air fader law ("level"): 0..0.0625 = −∞/−90..−60 dB, ..0.25 = −60..−30, ..0.5 = −30..−10, ..1.0 = −10..+10 dB. 0.75 = 0 dB | [M] p.11, plus the conversion code in its appendix |
| Notification form | the mixer notifies `/ch/NN/mix/MM/level`. FX mode accepts that and the short `/ch/NN/mix/MM` | [mb] PROTOCOL.md |
| Send tap default | FX sends `4` = POST (fader). FX mode does not change it | [O] `mix.rs` (live XR18) |
| FX send bus master | `/fxsend/1/mix/fader` (level), `/fxsend/1/mix/on` (0/1) | [C] |

The XR18 has a separate FX-send master (`/fxsend/1`) between the channel sends and the effect. If it is muted or at −∞ the reverb is silent, so FX mode treats it like the return (see e).

## b. FX 1 type address and reverb indices

| Fact | Value | Source |
|---|---|---|
| Type address | **`/fx/1/type`**, int 0..60 (61 effects) | [C] `/fx/1/type,i,0-60,"61 Fx, see doc"`; [M] p.39 `/fx/[1…4]/type enum int [0…60]` |
| Full order | HALL AMBI RPLT ROOM CHAM PLAT VREV VRM GATE RVRS DLY 3TAP 4TAP CRS FLNG PHAS DIMC FILT ROTA PAN SUB D/RV CR/R FL/R D/CR D/FL MODD GEQ2 GEQ TEQ2 TEQ DES2 DES P1A P1A2 PQ5 PQ5S WAVD LIM CMB CMB2 FAC FAC1M FAC2 LEC LEC2 ULC ULC2 ENH2 ENH EXC2 EXC IMG EDI SON AMP2 AMP DRV2 DRV PIT2 PIT | [M] p.39 and p.161 (appendix); [O] `catalogue.rs` agrees |
| Insert vs send | `/fx/1/insert` 0/1. When it is 1, FX 1 is an insert and a send reverb won't be heard | [C] |

## d. The XR18 FX types that are reverbs

| Index | Code | Name ([M] p.161) |
|---|---|---|
| 0 | HALL | Hall Reverb |
| 1 | AMBI | Ambiance |
| 2 | RPLT | Rich Plate Reverb |
| 3 | ROOM | Room Reverb |
| 4 | CHAM | Chamber Reverb |
| 5 | PLAT | Plate Reverb |
| 6 | VREV | Vintage Reverb |
| 7 | VRM | Vintage Room |
| 8 | GATE | Gated Reverb |
| 9 | RVRS | Reverse Reverb |

The combination effects 21-23 (Delay+Chamber, Chorus+Chamber, Flanger+Chamber) contain a reverb but are dual effects whose parameters are mostly delay or modulation, so they are left out of the list and out of knob-8 stepping.

## c. Parameters per reverb (`/fx/1/par/NN`)

Wire format: every float parameter is sent as float **0..1**, and enum parameters as int indices. `linf` maps linearly: value = lo + f·(hi−lo). `logf` maps logarithmically: value = lo·(hi/lo)^f. Sources: [M] p.11 for the type rules and pp.86-91 for the tables; [C] for the 0..1 floats. [M] gives no units. The units below follow from the parameter names, and [O] uses the same ones.

**0 HALL** ([M] p.86): 1 Pre Delay linf 0-200 ms · 2 Decay logf 0.2-5 s · 3 Size linf 2-100 · 4 Damping logf 1k-20k Hz · 5 Diffuse linf 1-30 · 6 Level linf −12..+12 dB · 7 Lo Cut logf 10-500 Hz · 8 Hi Cut logf 200-20k Hz · 9 Bass Multi logf 0.5-2 · 10 Spread linf 0-50 · 11 Shape linf 0-250 · 12 Mod Speed linf 0-100

**1 AMBI** ([M] p.87): 1 Pre Delay 0-200 ms · 2 Decay logf 0.2-7.3 s · 3 Size 2-100 · 4 Damping logf 1k-20k · 5 Diffuse 1-30 · 6 Level ±12 dB · 7 Lo Cut · 8 Hi Cut · 9 Modulate linf 0-100 · 10 Tail Gain linf 0-100

**2 RPLT** ([M] p.88): 1 Pre Delay · 2 Decay logf 0.3-29 s · 3 Size linf 4-39 · 4 Damping · 5 Diffuse 1-30 · 6 Level · 7 Lo Cut · 8 Hi Cut · 9 Bass Multi logf 0.25-4 · 10 Spread 0-50 · 11 Attack 0-100 · 12 Spin 0-100 · 13 Echo L 0-1200 ms · 14 Echo R 0-1200 ms · 15 Echo Feed L −100..+100 · 16 Echo Feed R −100..+100

**3 ROOM** ([M] p.88): as RPLT, except 3 Size linf 4-72 · 11 Shape 0-250

**4 CHAM** ([M] p.89): 1-12 as ROOM · 13 Reflection L 0-500 ms · 14 Reflection R 0-500 ms · 15 Reflection Gain L 0-100 · 16 Reflection Gain R 0-100

**5 PLAT** ([M] p.87): 1 Pre Delay · 2 Decay logf 0.2-10 s · 3 Size 2-100 · 4 Damping · 5 Diffuse · 6 Level · 7 Lo Cut · 8 Hi Cut · 9 Bass Multi logf 0.5-2 · 10 Xover logf 10-500 Hz · 11 Mod linf 0-50 · 12 Mod Speed 0-100

**6 VREV** ([M] p.90, type string `fffiifffff`): 1 Pre Delay linf 0-120 ms · 2 Decay logf 0.3-4.5 s · 3 Modulate linf 0-10 · 4 Vintage enum OFF/ON (int) · 5 Position enum FRONT/REAR (int) · 6 Level ±12 dB · 7 Lo Cut logf 10-500 Hz · 8 Hi Cut logf 200-20k Hz · 9 Lo Multiply logf 0.5-2 · 10 Hi Multiply logf 0.25-1

**7 VRM** ([M] p.90): 1 Reverb Delay linf 0-20 · 2 Decay logf 0.1-20 s · 3 Size linf 0-10 · 4 Density linf 1-30 · 5 ER Level linf 0-190 · 6 Level ±12 dB · 7 Lo Multiply logf 0.1-10 · 8 Hi Multiply logf 0.1-10 · 9 Lo Cut logf 10-500 · 10 Hi Cut logf 200-20k · 11 ER Left linf 0-10 · 12 ER Right linf 0-10 · 13 Freeze enum OFF/ON

**8 GATE** ([M] p.91): 1 Pre Delay 0-200 ms · 2 Decay logf 140-1000 ms · 3 Attack 0-30 · 4 Density 1-30 · 5 Spread 0-100 · 6 Level · 7 Lo Cut · 8 Hi Cut · 9 Hi Shv Gain linf −30..0 dB · 10 Diffuse 1-30

**9 RVRS** ([M] p.91): 1 Pre Delay · 2 Decay logf 140-1000 ms · 3 Rise 0-50 · 4 Diffuse 1-30 · 5 Spread 1-100 · 6 Level · 7 Lo Cut · 8 Hi Cut · 9 Hi Shv Gain −30..0 dB

**Where [M] and [O] disagree:** VREV Decay min (0.3 in [M], 0.4 in [O]); VREV Modulate (0-10 / 0-100); PLAT Decay min (0.2 / 0.5); ROOM/CHAM Size max (72 / 76); VRM delay and ER ranges (0-20, 0-10 / 0-200 ms). [M] is used. These ranges only affect the **displayed** value, because the wire value is always 0..1, so a wrong range shows a slightly wrong number but never sends a wrong value. To check, compare against X-Air Edit's readout during the live test.

**Knobs 2-8 chosen per type** (config `fx_mode.knob_maps`, editable):

| Type | Knob 2-8 → par |
|---|---|
| HALL, AMBI, RPLT, ROOM, CHAM, PLAT | 1 PreDly, 2 Decay, 3 Size, 4 Damping, 5 Diffuse, 7 LoCut, 8 HiCut |
| VREV | 1 PreDly, 2 Decay, 3 Mod, 7 LoCut, 8 HiCut, 9 LoMult, 10 HiMult (VREV has no size or damping. Lo/Hi Multiply shape the decay per band) |
| VRM | 1 RevDly, 2 Decay, 3 Size, 4 Density, 5 ERLevel, 9 LoCut, 10 HiCut |
| GATE | 1 PreDly, 2 Decay, 3 Attack, 4 Density, 5 Spread, 7 LoCut, 8 HiCut |
| RVRS | 1 PreDly, 2 Decay, 3 Rise, 4 Diffuse, 5 Spread, 7 LoCut, 8 HiCut |

Level (par 6) is left off the default maps because the FX return fader already sets the reverb's level.

## e. FX 1 return → main LR

| Fact | Value | Source |
|---|---|---|
| Return on/off | `/rtn/1/mix/on` int 0/1 | [C] |
| Return level | `/rtn/1/mix/fader` float 0..1 (fader law) | [C] |
| Return → LR | `/rtn/1/mix/lr` int 0/1 ("LR assignment") | [C] |
| Default state | Not stated in any source used. FX mode reads all three from the mixer and acts on what it finds | — |

On entry, FX mode switches `on` and `lr` to 1. A return level at −∞ (0.0) is raised to 0 dB (0.75), and any other level is kept. The FX-send master `/fxsend/1/mix/{on,fader}` gets the same treatment.

## f. Does changing the FX type reset its parameters?

Neither [M] nor [C] says. The `/fx/N/par/NN` slots are reinterpreted for each type. The parameter count and meaning come from the type ([M] p.86), so the old values cannot be kept meaningfully. **FX mode does not assume either way.** After any type change (its own or from X-Air Edit) it discards its cached parameters and re-reads `/fx/1/par/01..NN` from the mixer, showing `--` until the values arrive. Before overwriting a **non-reverb**, it logs the old type and all cached parameters to the ring log (`/debug`, kind `fx`), so they can be restored by hand.

## X-Touch Extender in Ctrl mode

| Fact | Value | Source |
|---|---|---|
| Knob push | Note 0..7 on ch 1 (knob 1..8). note_on vel 127 = press, note_off or vel 0 = release | [mb] decoder.py (captured). PROTOCOL.md's old "Note 32-39" row was wrong and is now fixed |
| Knob turn | CC 80..87 on ch 1, **absolute** 0..127 in `Ctrl` mode (relative in `CtrlRel`) | [B] (encoders, absolute mode: CC 80-87, 0-127); [A] (Ctrl vs CtrlRel); [mb] decoder.py |
| Ring | Set by sending the encoder's own CC (80..87) with 0..127 | [B]: the X-TOUCH ONE sheet puts encoder and ring on one CC (80). The Extender sheet's ring row couldn't be read (PDF down). Motor faders already work this way on the Extender ([mb]). **Unconfirmed on the venue Extender. Check it in the live test.** The Mackie CC 48-55 used before does nothing in Ctrl mode ([mb] README) |
| Encoder counter | The Extender keeps its own 0..127 counter per encoder. FX mode reads turns as **relative** (change since last value), so a counter that's out of step never jumps a parameter | design choice; see bridge/fx.py |
| Scribble strip | SysEx `F0 00 20 32 15 4C <strip> <colour> <7 top> <7 bottom> F7`, 7 ASCII chars per row | [A]; [mb] PROTOCOL.md |
| Scribble update rate | No limit found in [A], [B] or [mb]. Each strip write is one 23-byte SysEx. FX mode limits writes to one per strip every 50 ms while values stream, then sends the latest value when the burst ends | design choice |

## Things to check in the live test (venue Pi)

1. Turning knob 1 in FX mode moves X-Air Edit's *Ch 1 → FX1* send. If it doesn't, the mixer wants the short write form `/ch/01/mix/07` (the form PROTOCOL.md records for bus sends). **Checked 2026-11-24 (XR18V2, FW 1.25): it moves. The `/level` write form works; no change needed.**
2. The Extender rings follow when a value changes in X-Air Edit (CC 80-87). If they don't, the rings stay dark and everything else still works. **Checked 2026-11-24: the rings light and follow.**
3. The values shown on the scribble strips match X-Air Edit's readouts (see the range disagreements above).
4. Whether a type change in X-Air Edit keeps or resets the parameters (answers f).

## Channel EQ

Everything EQ mode (bridge/eq.py) relies on. Extra sources for this section:

- **[O-eq]** OpenFader `crates/proto-xair/src/eq.rs` and `input.rs`. Their doc comments quote the XR18 firmware OSC map for FW 1.14 (xr-meters `parameters.txt`, taken from Behringer firmware) and xair-api-python `shared.py`, and the owner checked them on an XR18 with FW 1.20.
- **[live]** The venue XR18 ("XR18-Bearbacka", XR18V2, **FW 1.25**), read on 2026-11-24 with plain queries and `/node` text queries (no writes during STEP 1). `/node` replies give the mixer's own readout in Hz/dB/Q next to the raw float, so they check each law directly.
- **[M]**: Maillot's PDF could not be fetched this pass. The Google Drive and GitHub copies failed and the academia.edu copy needs a login. So this section has **no [M] page numbers**. Every fact below rests on [C], [MX], [O-eq] and the live mixer instead.

### Addresses and value laws

| Fact | Value | Source |
|---|---|---|
| Bands per channel | 4: `/ch/NN/eq/1..4` (bus/main have 6) | [MX] node list `/ch/01/eq/1..4`; [O-eq]; [live] `/node ch/01/eq` lists 4 bands |
| EQ on/off | `/ch/NN/eq/on` int 0/1 | [C] `/ch/01/eq/on,i,0-1,"OFF, ON"`; [live] `1` |
| Band type | `/ch/NN/eq/B/type` int 0..5 = **LCut, LShv, PEQ, VEQ, HShv, HCut** | [C] line 125; [O-eq] |
| Band frequency | `/ch/NN/eq/B/f` float 0..1, **log, 20..20000 Hz, 201 points**: Hz = 20·1000^f | [C] `"20 - 20000"`; [O-eq] `f log(201)`; [live] f 0.30 → 158.9, 0.465 → 496.6, 0.665 → 1k98, 0.95 → 14k16 (exactly 20·1000^f) |
| Band gain | `/ch/NN/eq/B/g` float 0..1, **linear, −15..+15 dB, 121 points** (0.25 dB grid); 0.5 = 0 dB | [C] `"-15.0 - +15.0"`; [O-eq] `g lin(121)`; [live] g 0.5 → 0.00 |
| Band Q | `/ch/NN/eq/B/q` float 0..1, **log, reversed: 0 = Q 10, 1 = Q 0.3, 72 points**: Q = 10·0.03^q | [C] `"10.0 - 0.3"`; [O-eq] `q log(72)`; [live] q 0.4647887 (= 33/71, so a 72-point grid) → shown "2.0" (the law gives 1.96; the mixer prints one decimal) |
| Low cut on/off | `/ch/NN/preamp/hpon` int 0/1 | [C] line 152; [O-eq] `input.rs`; [live] `1` |
| Low cut frequency | `/ch/NN/preamp/hpf` float 0..1, **log, 20..400 Hz**, 101 points assumed | [O-eq] `log_set(20, 400)`, firmware map + xair-api; [live] hpf 0.04 → `/node` "22.5" Hz = 20·20^0.04 (a 20..200 law would give 21.9) |
| Low cut slope | no `/ch/NN/preamp/hpslope` on the XR18 (query got no reply) | [live] |

**Contradiction, resolved:** [C] gives the low-cut range as `"20 - 200"`, while [O-eq] (and the X32) give 20-400. The live mixer settles it as **20-400 Hz** (row above, measured after the on-site test moved the low cut). Only the displayed Hz depends on this; the wire value is always 0..1. `eq_mode.lowcut_max_hz` holds it. The 101-point grid for `hpf` is not confirmed by any source used. Values off the grid are accepted by the mixer, so a wrong guess only changes the step size slightly.

### Band defaults on the venue XR18, and the band choice

[live], 2026-11-24, channels 1 and 2 identical (so these look like the XR18's defaults):

| Band | Type | Freq | Gain | Q |
|---|---|---|---|---|
| 1 | **LCut** (0) | 158.9 Hz | 0 dB | 2.0 |
| 2 | PEQ (2) | 496.6 Hz | 0 dB | 2.0 |
| 3 | PEQ (2) | 1.98 kHz | 0 dB | 2.0 |
| 4 | **HCut** (5) | 14.16 kHz | 0 dB | 2.0 |

A cut filter has no gain, so LOW and HIGH gain would do nothing on bands 1 and 4 as found. **Amrit's decision (2026-11-24): shelves on entry.** Entering EQ mode sets band 1 to LShv (1) and band 4 to HShv (4), keeping their frequencies, and logs the old type and frequency (kind `eq`, "band type changed…"). A mid band that is a cut becomes a PEQ. A PEQ/VEQ mid is left as it is. MID is band 3 (1.98 kHz, presence range for a head mic); band 2 is never written. On site the change ran as logged: `band=1 old_type=0:LCut`, `band=4 old_type=5:HCut`. Note that this removes the 159 Hz low cut and the 14 kHz high cut the defaults had. The preamp low cut (knob 8) can put a low cut back.

### Step sizes (config `eq_mode`)

| Knob | Per encoder click | On the mixer grid |
|---|---|---|
| Gain | 0.5 dB (`gain_step_db`) | 2 points of 121 |
| Frequency | 0.1 octave (`freq_step_octaves`) | 2 points of 201 = 0.0997 oct (one grid point = 1/20 octave) |
| Q | 1 mixer step (`q_steps`) | 1 point of 72 ≈ ×1.05; knob right = higher Q |
| Low cut | 0.1 octave (`lowcut_step_octaves`) | 2 points of 101 ≈ 0.086 oct (20..400) |

Turns snap to the grid, so the bridge's value always equals one the mixer can hold, and a value received from X-Air Edit continues from where it is.

### Live check results (venue, 2026-11-24)

a. Hold knob 2: strips turn red with LO G … LCUT: **yes**.
b. Knobs 1 and 6 change low and high on Headmic 1, and X-Air Edit's Ch1 EQ follows: **yes, both**.
c. Knob 8 push switches Ch1 EQ off and on in X-Air Edit: **yes**.
After the test, the mixer read: band 1 LShv 105.0 Hz +0.50 dB, band 3 PEQ 2k43 0 dB, band 4 HShv 14k16 0 dB, EQ ON, low cut ON 22.5 Hz.
