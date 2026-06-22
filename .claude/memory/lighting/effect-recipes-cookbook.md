---
name: effect-recipes-cookbook
description: "The actionable cookbook: concrete club lighting effects mapped to QLC+ implementations (EFX, RGB Matrix, Chasers, Collections, Scripts) on the venue rig, with genre playbooks"
metadata:
  node_type: memory
  type: reference
  originSessionId: 0a2fa9d9-bc6b-46d6-bdbb-e98d6dad045a
---

# Club lighting effect recipes — QLC+ cookbook

This is the venue's working recipe book. Every recipe maps a concrete *visual result* to a concrete *QLC+ construct* with exact parameters, then tells you how to wire it to the Virtual Console (VC) for live use. It is written so an assistant can generate the `.qxw` XML directly — see [[qlc-save-file-format]] for element/attribute syntax and [[qlc-functionality-reference]] for the engine mechanics behind each construct. The rig, fixture IDs, addresses and roles are in [[club-rig-mayans]] and [[fixture-types-and-roles]]; design rationale (why/when) is in [[lightshow-design-principles]].

## Rig shorthand used throughout

| Token | Meaning | Fixture IDs |
|---|---|---|
| BEAMS | 13x BEAM230 moving beams | 34, 1-7, 12-15, 0 |
| WASHES | 4x WASH moving washes | 8, 9, 10, 11 |
| REV | Revolver Wash | 31 |
| SWARM | 2x Swarm 5 FX | (see [[club-rig-mayans]]) |
| TETRA | 2x Tetra pixel bars | 19, 20 |
| VPARS | 4x ADJ VPar | 16, 17, 28, 29 |
| THINPAR | Center Truss ThinPAR washes | 18 |
| GENRGB | Generic RGB | (see [[club-rig-mayans]]) |

Conventions: DMX values are 8-bit (0-255). All times are milliseconds in the XML (`FadeIn`/`Hold`/`FadeOut`), or beats when the function's `<Tempo>Beats</Tempo>` is set (1000 = 1 beat). Per-fixture wave spread uses EFX `<StartOffset>` in degrees, distributed `offset(i) = round(360 / N * i)`.

Before building anything, confirm the prep in [[qlc-functionality-reference]] §0: Fixture Groups exist for BEAMS, WASHES, TETRA, PARS; the TETRA group grid is laid out left-to-right matching physical pixel order. RGB Matrix and traveling EFX waves both depend on that grid/order.

---

## 1. Color washes & rainbow chases

### 1.1 Whole-room color wash (Scene)
- **Visual:** every color-capable fixture sits on one saturated color.
- **Construct:** Scene. One `<FixtureVal>` per WASH/PAR/REV/GENRGB fixture writing its R/G/B (and W/A where present) channels; BEAMS use their color-wheel channel value for the nearest matching color.
- **Params:** Dimmer up (e.g. 255), R/G/B set per palette color. `FadeIn=1500, FadeOut=1500` for a DJ-default smooth entry; `Duration=0`.
- **VC:** put each color Scene in a **Solo Frame** (color base) so selecting one cancels the others. One button per palette color.

### 1.2 Color crossfade between palettes (Chaser, clean path)
- **Visual:** the wash morphs color to color without muddy midpoints.
- **Construct:** Chaser of color Scenes (1.1). **SpeedMode Common**, `FadeIn=1500, Hold=0, FadeOut=0`, `RunOrder=Loop`.
- **Why Hold=0:** continuous morph, no static dwell. To avoid the dirty-white midpoint on opposite hues (e.g. yellow→blue), either step through an intermediate Scene or use the dip-to-black variant: insert a black Scene between, or pull Dimmer during the swap.
- **VC:** bind a **Speed Dial** to the chaser to stretch the morph live.

### 1.3 Rainbow chase across the rig (RGB Matrix, preferred for TETRA/PARS)
- **Visual:** a spectrum sweeps across the pixel grid; the rig reads as a moving rainbow.
- **Construct:** RGB Matrix on the TETRA+PARS group. `<Algorithm Type="Script">Gradient</Algorithm>` (or `Stripes`) with **5 color stops** spanning the spectrum (`<Color Index="0..4">` packed RGB). `ControlMode=Rgb`.
- **Params:** `Direction=Forward`, `Duration` 5000-20000 (full sweep), `FadeIn=Hold=FadeOut` short. For a frozen spectrum set `Duration` very high (standing rainbow).
- **Alt (BEAMS, no RGB):** EFX with `EFXFixture Mode=RGB` is not applicable to color-wheel beams; instead use a Chaser of color-wheel Scenes with per-step different wheel positions and `StartOffset`-style staggering via Per-Step fades.
- **VC:** Solo Frame (color base) + Speed Dial.

### 1.4 Pastel / breathing wash
- **Visual:** desaturated slow color drift, ambient.
- **Construct:** RGB Matrix `Plasma` (Rgb mode) on WASHES/PARS group, low `Ramp` property (cohesion), 2-3 desaturated `<Color>` stops, long `FadeIn`/`Hold`.
- **VC:** ambient page; drop the FX submaster to ~25%.

---

## 2. Dimmer chases & running lights

### 2.1 Running light (Chaser of single-fixture Scenes)
- **Visual:** one lit fixture runs along the BEAM row.
- **Construct:** Chaser; each Step references a Scene with one BEAM at Dimmer 255 and the rest implicitly 0 (HTP). Step order follows physical left-to-right.
- **Params:** **SpeedMode Common**, snap = `FadeIn=0, Hold=120, FadeOut=0`; soft tail = `FadeIn=0, Hold=80, FadeOut=200` (overlap). `RunOrder=Loop`, `Direction=Forward`. Beat-lock: set `<Tempo>Beats</Tempo>`, `Hold=1 beat` — keep fades at 0 (in Beats mode fades stay in ms).
- **VC:** movement Solo Frame; Speed Dial on the chaser; Direction button.

### 2.2 Knight Rider bounce
- **Construct:** same as 2.1 but `RunOrder=PingPong`. Add tail via `FadeOut=2-3x` step time so trailing fixtures glow behind the leader.
- **Variations:** dual scanner = two lit Scenes per step from opposite ends; mirror = split at center, both halves outward.

### 2.3 Build-on / build-off
- **Construct:** Chaser where each Step's Scene *adds* a fixture (build-on) without zeroing prior ones — use cumulative Scenes. Build-off starts full, removes one per step.
- **VC:** great on a Flash button for a riser fill.

### 2.4 Intensity wave without a chaser (EFX, Dimmer mode)
- **Visual:** smooth sine of brightness rolling across PARS.
- **Construct:** EFX over the PARS group with each `EFXFixture` `<Mode>1</Mode>` (Dimmer) and staggered `<StartOffset>`. `Algorithm=Circle` drives the dimmer sine.
- **Params:** `Duration` sets wave speed; `StartOffset(i)=360/N*i`.

---

## 3. Ballyhoo & beam fans

### 3.1 Ballyhoo (busy aerial search)
- **Visual:** beams sweep the room continuously, never repeating cleanly.
- **Construct:** EFX on BEAMS. `Algorithm=Lissajous` with non-harmonic X/Y `<Frequency>` (e.g. X Frequency=2, Y Frequency=3) so the path drifts. Per-fixture `<StartOffset>` spread 0-330 so beams never bunch.
- **Params:** `<Width>110</Width> <Height>90</Height>`, `Rotation` to taste, `Duration` 8000-15000 (wide) or 2000-4000 (tight "boil"). Dimmer up via a companion Scene; narrow beam (small zoom/iris).
- **VC:** movement Solo Frame; Speed Dial on EFX `Duration`.

### 3.2 Beam fan-out (static splay)
- **Visual:** beams splayed like a hand fan.
- **Construct:** Scene (not animated). Fan Tilt linearly across the 13 BEAMS: `tilt(i) = base + (i - (N-1)/2) * delta`. Optionally fan Pan for an X-cross.
- **Params:** pick `delta` so the total spread fills the ceiling; center fixture at `base`. Dimmer 255.
- **Animated variant:** EFX `Algorithm=Line`, low `<Frequency>`, modest Width so the fan breathes open/closed.
- **VC:** position Solo Frame; combine with a relative EFX (see 4.4) to add wiggle on top.

### 3.3 Unison sweep (wall of parallel beams)
- **Construct:** EFX on BEAMS, `Algorithm=Line` or `Line2`, **all `<StartOffset>` = 0** (zero phase spread = lockstep). Pan sweep = audience scan; Tilt = ceiling-to-floor.
- **Params:** big `<Width>` (audience scan), `Duration` fast for drops, slow for mood. `RunOrder=PingPong` for left-right-left bounce.

---

## 4. Circle / figure-8 with phased waves

### 4.1 Synchronized circles
- **Construct:** EFX on BEAMS, `Algorithm=Circle`. X axis `<Frequency>1</Frequency> <Phase>90</Phase>`, Y axis `<Frequency>1</Frequency> <Phase>0</Phase>` (90 deg apart = round circle). Equal `<Width>`/`<Height>` = true circle; unequal = ellipse.
- **Params:** `Width=Height=60-90` to keep beams on the floor/crowd; `Duration` = spin speed. Reverse spin by swapping per-fixture `<Direction>Backward</Direction>`.

### 4.2 Wave of circles (phased ripple)
- **Construct:** as 4.1 but per-fixture `<StartOffset>` spread `360/13*i` so all beams ride the same circle at staggered points — the spin ripples down the rig.
- **Mirror variant:** set left-half fixtures `<Direction>Forward</Direction>` and right-half `Backward` so the two sides counter-rotate.

### 4.3 Figure-8
- **Construct:** EFX `Algorithm=Eight`. (Engine handles the 2:1 Tilt:Pan internally; for manual Lissajous control use X Frequency=2, Y Frequency=4 with appropriate phase.) Per-fixture `<StartOffset>` spread for a traveling-8 wave.
- **Variations:** push Y Frequency to 3-4x for pretzel/clover; narrow `<Height>` for a bowtie.

### 4.4 Relative orbit (busking anchor)
- **Visual:** beams wiggle/orbit around wherever you've aimed them.
- **Construct:** EFX with `<IsRelative>1</IsRelative>`, `Algorithm=Circle`, small `Width=Height=20`. Park the beams first with a position Scene (or Simple Desk), then fire this EFX on top — center (127,127) maps to the current aim.
- **VC:** put the position Scene + relative EFX in a **Collection** for one-tap "moving look where I want it."

---

## 5. Pixel-bar effects (TETRA)

All run as RGB Matrix on the TETRA group (grid ordered left-to-right). Layer two matrices on the same group: one `ControlMode=Rgb` for color, one `ControlMode=Dimmer` for motion/strobe.

### 5.1 Chase / marquee
- **Construct:** RGB Matrix `<Algorithm Type="Script">Marquee</Algorithm>`, `ControlMode=Rgb`. Block/gap via script `<Property>`; `Direction`, `Duration` = scroll speed.

### 5.2 Wave / color scroll
- **Construct:** `Sinewave` or `Waves` (Rgb) for a brightness/hue wave scrolling up the bar; or `Gradient` with 5 stops + short `Duration` for a moving rainbow (see 1.3).

### 5.3 Comet / tail
- **Construct:** `<Algorithm Type="Script">Onebyone</Algorithm>` in `ControlMode=Dimmer` with `FadeOut` long enough to leave a decaying trail behind the head. Head color from a companion Rgb-mode `Plain` matrix.
- **Params:** `Duration` = head speed; `RunOrder=PingPong` for bounce, `Loop` for wrap.

### 5.4 Sparkle / twinkle
- **Construct:** `Randompixelperrowmulticolor` (Rgb) or `Randomsingle` (Dimmer for white twinkle). Short `Hold`, moderate `FadeOut` = decay. Spawn density via script `<Property>`.
- **VC:** drive from an Audio Trigger **high band** for hat/snare-reactive sparkle (see §10).

### 5.5 VU / level meter
- **Construct:** there is no native VU script; build it via an **Audio Trigger** slider mapped to a `Fill` RGB Matrix's intensity, or map the band level to a submaster scaling a gradient-colored `Fill`. Green-low/amber-mid/red-peak via the 5 color stops.
- **Params:** fast attack, slow decay (Audio Trigger behavior); mirror-from-center via `Fillfromcenter`.

---

## 6. The strobe family

Two engines: BEAM/WASH dedicated `Shutter/Strobe` channel (value range = rate), or square-waved Dimmer for fine control. For TETRA use RGB Matrix `Strobe`/`Blinder` scripts in `ControlMode=Dimmer`.

| Effect | Construct | Key params |
|---|---|---|
| 6.1 Full strobe | Scene setting Shutter channel into its strobe band (per fixture def) | pick value for rate; color held |
| 6.2 Ramp/accel strobe | Chaser of Scenes stepping the Shutter value slow→fast, `SpeedMode Common`, `RunOrder=SingleShot` | start slow rate → end fast over the build duration |
| 6.3 Random strobe | RGB Matrix `Strobe` (Dimmer) `RunOrder=Random`, or Shutter random band | irregular = safer for photosensitivity |
| 6.4 Dual/alternating | two Scenes/groups strobing 180 deg out of phase (Chaser PingPong across two group Scenes) | group split A/B |
| 6.5 Machine-gun bursts | Chaser `RunOrder=SingleShot`, 3-5 fast Steps then a gap Step | beat-lock the gap |
| 6.6 Lightning | Collection/Chaser of 1-2 fast cool-white Dimmer spikes + a slow-decay tail Scene, random intervals | cool white tint, double-tap probability |

**VC:** strobe on **Flash buttons** (held = on) in an FX Solo Frame so they punch over the base look via LTP/HTP intensity.

---

## 7. Bumps, blinder hits, build-up + drop

### 7.1 Bump / stab
- **Construct:** Scene, Dimmer 0→255 with `FadeIn=0`. Fire from a **Flash** button (held = on, release = off). Color saturated.

### 7.2 Blinder hit
- **Construct:** Scene snapping audience-facing VPARS/THINPAR to full warm/white. `FadeIn=0`; choose snap-off (release) or `FadeOut=400`.
- **VC:** dedicated red Flash button. LTP override so it punches through any base.

### 7.3 Build-up + drop (the signature)
Build as named **Collections**/Chasers triggered in sequence (or a SingleShot master chaser / Script):

1. **BUILD:** accel-strobe (6.2) + RGB Matrix `Fillfromcenter` rising + raise Dimmer floor + BEAMS tilt up + color desaturates toward white. Accelerate via Speed Dial.
2. **PRE-DROP GAP:** snap **blackout** Scene (or one held pad color) for 1-2 beats.
3. **DROP Collection** (one button, all simultaneous): fast BEAM `Circle` EFX + blinder Scene (7.2) + TETRA `Strobe` matrix (Dimmer) + full-saturation color Scene + SingleShot strobe burst. All `FadeIn=0`.
4. **GROOVE:** settle into a beat-synced wash/chase.
- **Collection note:** members run first→last; the channel "winner" (LTP) must be the last member. See [[qlc-functionality-reference]] §4.
- **VC:** DROP on a big dedicated button; keep BUILD on a Speed-Dial-controlled chaser so you ride the riser by hand.

---

## 8. Sound-to-light

- **Construct:** VC **Audio Trigger** widget; it band-splits captured audio (Sub/Lo/Mid/Hi) and shows spectrum bars.
- **Wiring:**
  - Low band (kick) → **Slider** scaling blinder/wash Dimmer (bass-to-dimmer pulse).
  - Low band → **Button** pressing **Next** on a Cue List (beat-advanced chase) — show stays musical but auto-driven.
  - High band → sparkle matrix intensity (5.4).
- **Tempo path:** let an Audio Trigger drive the **global BPM**; then any `<Tempo>Beats</Tempo>` Chaser/EFX/RGB Matrix follows automatically (see [[qlc-functionality-reference]] §3d, §6).
- **Caveat:** sound-to-light reacts but doesn't phrase — combine with manual busking for builds/drops.

---

## 9. Gobo / prism / zoom / beam-optics (BEAMS, REV, SWARM)

| Effect | Construct | Channels / params |
|---|---|---|
| 9.1 Gobo morph | Chaser of Scenes stepping `Gobo select`; or single Scene parked in the gobo shake/scroll band | step rate beat-locked; sequence the wheel slots |
| 9.2 Prism spin | Scene: `Prism` insert on + `Prism rotate` into continuous-rotation band | speed/direction set by value in range |
| 9.3 Zoom pulse | EFX `Mode=Dimmer`-style not applicable; use a Chaser/Scene modulating the `Zoom` channel with a sine via stepped Scenes, beat-synced snap narrow | min/max zoom, per-fixture phase for a zoom wave |
| 9.4 Focus rack | Scene/Chaser sweeping `Focus` soft→crisp | pair with zoom to keep gobo sharp |
| 9.5 Iris pulse | Chaser snapping `Iris` closed on the beat, ease open | beat sync |
| 9.6 Color-temp shift | Scene/Chaser ramping CTO/CTB or RGBW balance | warm chill ↔ cool peak |

Always patch the exact channel from the fixture's `.qxf` (see [[qlc-fixture-definition-format]] and the per-model charts in [[club-rig-mayans]]); BEAM230 V1/V2/V3 differ slightly.

---

## 10. Genre playbook

Which recipes to stack per vibe. Design reasoning in [[lightshow-design-principles]].

### House / Techno — hypnotic, restrained, then brutal
- **Base movement:** slow **Circle** or **Lissajous** EFX on BEAMS, long `Duration`, `Tempo=Beats`, `IsRelative=1` orbiting a fixed crowd aim (4.4); PingPong `Line2` slow scan (3.3).
- **Color:** narrow palette (deep blues/reds/UV). RGB Matrix `Plasma`/`Gradient` (Rgb), low Ramp, 2-3 stops — no rainbow (1.4).
- **Texture:** second matrix `Evenodd`/`Circles` (Dimmer) breathing.
- **Peak:** SingleShot `Strobe` on a Flash button; Solo Frame "blackout-but-strobe." Techno = `SquareChoppy` beams + white strobe; house = warmer, more sustain.

### Big-room EDM / Festival — fans, snaps, huge drops
- **Build:** fan the BEAMS (StartOffset 0→330) opening on `Circle`/`Eight`, accelerate via Speed Dial; matrix `Fillfromcenter` rising (3.1/7.3).
- **Color snaps:** Common/Per-Step chaser, `FadeIn=Hold=FadeOut=0`, stepping saturated primaries on the beat (`Tempo=Beats`).
- **DROP Collection:** fast BEAM Circle + blinder Scene + TETRA `Strobe` (Dimmer) + full-sat color Scene + SingleShot strobe — one button (7.3).
- **Post-drop:** PingPong beam fan, wide sweep, big sustained washes.

### Hip-hop / Open-format — punchy bumps, blinders, fast changes
- **Color bumps:** Common-mode chaser, snap fades, `RunOrder=Random` across bold colors; trigger per phrase.
- **Blinders:** dedicated LTP Flash buttons for the wheel-ups/rewinds (7.2).
- **Quick changes:** multipage VC, one page per vibe; lean on **Solo Frames** so each song's base look cleanly replaces the last.
- **Sparkle:** TETRA `Randompixelperrowmulticolor` driven by Audio Trigger high band (5.4, §8).

### Ambient / between-sets — low, slow, unobtrusive
- One slow RGB Matrix `Gradient`/`Noise` (Rgb), long Fade/Hold, deep dim color (1.4); BEAMS parked or a very slow `Leaf` EFX at low intensity.
- Drop Grand Master / FX submaster to ~20-30%. Park as a single Solo-Frame look so one tap brings the room down between DJs. No strobe, no fast movement.

---

## Quick build-targets for an XML generator

When generating `.qxw` from a recipe, the construct → element mapping (full detail in [[qlc-save-file-format]]):

- **Scene** → `<Function Type="Scene">` + one `<FixtureVal ID="..">ch,val,...</FixtureVal>` per fixture.
- **Chaser** → `<Function Type="Chaser">` + `<SpeedModes .../>` + `<Step Number Hold FadeIn FadeOut>funcID</Step>`. Beat-lock with `<Tempo>Beats</Tempo>`.
- **EFX** → `<Function Type="EFX">` + per-fixture `<Fixture><ID><Head><Mode><Direction><StartOffset></Fixture>`, `<Algorithm>`, `<Width>/<Height>/<Rotation>/<StartOffset>/<IsRelative>`, X/Y `<Axis>` with `<Offset>/<Frequency>/<Phase>`.
- **RGB Matrix** → `<Function Type="RGBMatrix">` + `<Algorithm Type="Script">Name</Algorithm>`, `<Color Index>` (up to 5), `<ControlMode>`, `<FixtureGroup>id</FixtureGroup>`, `<Property Name Value/>`.
- **Collection** → `<Function Type="Collection">` + `<Step Number>funcID</Step>` (last member wins shared channels).

Verified enums to emit: EFX Algorithm {Circle, Eight, Line, Line2, Diamond, Square, SquareChoppy, SquareTrue, Leaf, Lissajous}; EFXFixture `<Mode>` numeric {0=PanTilt, 1=Dimmer, 2=RGB}; Chaser SpeedModes {Common, PerStep, Default}; RunOrder {Loop, SingleShot, PingPong, Random}; Direction {Forward, Backward}; Tempo {Time, Beats}; RGB Matrix ControlMode {Rgb, White, Amber, UV, Dimmer, Shutter}.