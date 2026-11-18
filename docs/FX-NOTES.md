# FX mode: facts and sources

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

1. Turning knob 1 in FX mode moves X-Air Edit's *Ch 1 → FX1* send. If it doesn't, the mixer wants the short write form `/ch/01/mix/07` (the form PROTOCOL.md records for bus sends).
2. The Extender rings follow when a value changes in X-Air Edit (CC 80-87). If they don't, the rings stay dark and everything else still works.
3. The values shown on the scribble strips match X-Air Edit's readouts (see the range disagreements above).
4. Whether a type change in X-Air Edit keeps or resets the parameters (answers f).
