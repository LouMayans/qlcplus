---
name: fixture-types-and-roles
description: "Professional guide to fixture types (spot/beam/wash/par/pixel-bar/strobe/blinder/FX/laser/haze): purpose, typical DMX channels, placement, show roles — mapped to the venue rig"
metadata:
  node_type: memory
  type: reference
  originSessionId: 0a2fa9d9-bc6b-46d6-bdbb-e98d6dad045a
---

# Professional lighting fixture types & their show roles

A practical, designer's reference to the major professional fixture classes: what each is for, how its DMX channels are typically laid out, how to rig it, the musical moments where it earns its place, and the mistakes that separate a clean show from an amateur one. It closes with a venue-specific mapping for the Mayans club rig. For the actual patched inventory see [[club-rig-mayans]]; for how to combine these into looks see [[lightshow-design-principles]] and [[effect-recipes-cookbook]].

> **How to read the DMX charts below.** Channel layouts vary by manufacturer, model, *and DMX mode* (most fixtures offer a "basic/compressed" mode and an "extended/16-bit" mode that splits pan/tilt into coarse+fine and adds control channels). The orderings here are **typical/representative**, drawn from real manuals (Sharpy BEAM 230, Robe Robin Pointe, Martin Atomic 3000, Chauvet Strike/Swarm/COLORband, ADJ Ultra Bar). **Always patch from the exact DMX chart for your model and mode** — channel order is the one thing you must never assume. The Mayans custom fixtures have their channel order fixed in their `.qxf` definitions; see [[qlc-fixture-definition-format]].

---

## 1. Moving-head SPOT

**Purpose.** The workhorse "do-everything" moving fixture. A spot produces a **hard-edged beam** that can be shaped, textured, and colored: it projects **gobos** (patterns/breakups/logos) onto surfaces, throws **aerial looks** in haze, and acts as a key/effect light. CMY or CMY+wheel color mixing gives a near-infinite palette. If you can only own one class of moving light, this is it.

**Typical DMX channel layout (representative 16–24 ch extended mode — approximate).**

| # | Channel | Notes |
|---|---|---|
| 1–4 | Pan, Pan fine, Tilt, Tilt fine | 16-bit position |
| 5 | Pan/Tilt speed | vector / movement smoothing |
| 6 | Color wheel | split/indexed colors + continuous scroll |
| 7–9 | Cyan, Magenta, Yellow | CMY subtractive mixing |
| 10 | CTO / CTC | color-temperature correction |
| 11 | Static gobo wheel | fixed patterns |
| 12–13 | Rotating gobo select + index/rotation | position & spin speed/direction |
| 14 | Animation wheel | model-dependent |
| 15–16 | Prism insert + prism rotation | e.g. 3-/8-facet |
| 17 | Frost / diffusion | softens hard edge toward a wash |
| 18 | Iris | beam diameter / pulse-iris chases |
| 19–20 | Zoom, Focus | beam angle; sharpen gobo/edge |
| 21 | Shutter / strobe | open, closed, strobe, pulse, random |
| 22 | Dimmer / intensity | |
| 23 | Control / reset / lamp | motor reset, lamp on/off, presets |

**Placement & rigging.** Spots are flexible — front truss for gobo texture on the band and key light, overhead/mid truss for aerial gobo cones and tabletop breakups on the floor, back truss for shafts and silhouettes. Mount **above head height** so pan/tilt clears the rig and people. Always use a rated clamp **and an independent safety cable/bond**. Leave clearance for the full tilt arc; don't bury a spot behind a downstage edge or it will clip its own beam. FOH spots used as key light read best at roughly **30–45° above the performer** (typical).

**Where it shines (musical moments).** Verse/chorus key lighting and texture; **gobo breakups** during builds and breakdowns; slow rotating-gobo "tunnels" in ambient passages; sharp **iris pulses** and gobo-shake on percussive hits; logo/branding projection between sets; ballad specials (frost on, slow CMY drift).

**Pro tips & common mistakes.**
- **Set focus per throw distance** — a gobo razor-sharp on the floor is mush on the back wall. Re-focus when the rig moves.
- **Haze makes the spot a 3D fixture.** Dry air = you only see where the beam lands.
- Use **frost** to blend a spot into a wash look instead of buying more washes for a small rig.
- Mistake: leaving **pan/tilt speed** at default so moves snap; dial in vector smoothing for musical movement.
- Mistake: running everything at full zoom-wide — narrow for punchy aerials, widen for coverage.
- Mind **gobo orientation/index** so logos aren't upside-down or mirrored.
- *Note: the current Mayans rig has no spot — none of the venue fixtures project gobos. Add one if you ever need texture/projection.*

---

## 2. Moving-head BEAM (Sharpy-style) — *the rig's signature fixture*

**Purpose.** The **aerial specialist**. A beam fixture (the original *Clay Paky Sharpy* and the whole "7R 230W" / "BEAM 230" class it spawned) uses a parabolic-reflector optical system to throw an intensely bright, **near-parallel, pencil-thin beam** (roughly **0–4° beam angle** on a 7R) that stays tight over long throws. It does **not** wash, and its gobos are "beam reducers," not projection patterns. Its job is **mid-air**: tight columns, **fans**, sweeps, and crisscross looks. The Mayans rig is built around **13 of these** — they are the venue's headline look.

**Typical DMX channel layout — Sharpy / BEAM 230 class (16 ch, drawn from 7R 230W manuals; ranges where quoted are spec).**

| # | Channel | Notes |
|---|---|---|
| 1 | Color wheel | ≈7 dichroic colors + split colors |
| 2 | Stop / strobe | shutter: open, strobe rate, blackout |
| 3 | Dimmer / intensity | |
| 4 | Static gobo wheel | ≈7+ beam-shaping gobos + open |
| 5 | Prism insert (8-facet) | *e.g. 000–127 out / 128–255 in (spec)* |
| 6 | Prism rotation | speed & direction |
| 7 | Effect / gobo-shake / macros | model-dependent |
| 8 | Frost | *frost out low / frost in small window (spec)* |
| 9 | Focus | mechanical, multi-step — tightens beam |
| 10–13 | Pan, Pan fine, Tilt, Tilt fine | ≈540° pan / ≈250–265° tilt, 16-bit |
| 14 | Pan/Tilt speed | |
| 15 | Reset / lamp / special functions | |

**Placement & rigging.** Beams live to be seen *in the air*, so put them where their shafts read against darkness: **back and mid truss**, floor "uplight" positions firing up through haze, or symmetrical pairs/rows for fans. **Even spacing** makes clean fans and "combs." Same safety rules: rated clamp + safety. Avoid aiming straight into audience eyes at close range — the beam is genuinely intense.

**Where it shines.** EDM/club **drops and builds** (synchronized fans on the beat), big-room sweeps, crisscross "laser-like" looks for a fraction of a laser's cost, slow rising columns in ambient/intro sections, strobe-shutter chases on a kick pattern.

**Pro tips & common mistakes.**
- **Haze is mandatory** — a beam fixture in clean air is almost pointless; the entire effect is the visible shaft.
- Build **fans** by offsetting pan in equal steps across a row; symmetry is everything. With 13 units, address them in a known left-to-right order so pan-offset chases sweep cleanly (see [[effect-recipes-cookbook]]).
- The prism multiplies the single beam into a spread — great for instant "more beams," but rotate it slowly or it looks cheap.
- Mistake: treating a beam like a wash or gobo projector — it won't cover a stage or project a clean pattern.
- Mistake: too-fast moves — beams read best with **crisp, musical, often slower** moves so the eye can track the shaft.

---

## 3. Moving-head WASH / wash-zoom

**Purpose.** **Color and coverage.** A wash produces a **wide, soft-edged field** of light with no hard edge — RGBW/RGBWA+UV or CMY color mixing, usually with a motorized **zoom** to go from tight beam-ish to wide flood. Many modern washes have a **pixel ring / pixel-mappable LEDs** around the front for "eye-candy" ring effects. Washes paint the stage, the band, the backdrop, and the air with color and provide overall coverage.

**Typical DMX channel layout (representative RGBW zoom wash, extended mode — approximate).**

| # | Channel | Notes |
|---|---|---|
| 1–4 | Pan, Pan fine, Tilt, Tilt fine | 16-bit position |
| 5 | Pan/Tilt speed | |
| 6 | Dimmer / master intensity | |
| 7 | Shutter / strobe | |
| 8–11 | Red, Green, Blue, White | (or Amber/UV on RGBWA+UV) |
| 12 | Color macros / presets | |
| 13 | CTO / virtual color wheel | |
| 14 | Zoom | narrow ↔ wide field |
| 15 | Pixel/ring control mode | macro or per-pixel block (if equipped) |
| 16+ | Per-pixel RGBW cells | pixel-ring fixtures add many channels |
| last | Control / reset / function | |

**Placement & rigging.** Washes go almost anywhere: **back/mid truss** for color from behind and over the band, **front** for color key, **side/floor** for high-impact color sweeps and backlight. Because the field is wide, they can be **spread farther apart** than beams. Overhead height, rated clamp + safety. Match zoom to throw so adjacent washes blend without hot spots or dark gaps.

**Where it shines.** Establishing the **color mood** of a song; smooth color fades across a build; saturated backwash on a drop; **pixel-ring eye-candy** chases as accent; audience color wash; gentle ballad coverage with wide zoom and low intensity.

**Pro tips & common mistakes.**
- **Zoom to blend.** Overlap fields slightly so the stage reads as one color field, not spotty pools.
- Use **White/Amber** for natural-looking key and skin tones; pure RGB key light makes people look ill.
- The pixel ring is **accent, not base** — drive main color from the body LEDs and let the ring add motion.
- Mistake: strobing a wash like a strobe — LED washes can flash, but they're not a substitute for a real strobe's punch.
- Mistake: leaving zoom narrow so you get a hard-ish pool — that's a spot's job; let washes be soft.

---

## 4. LED PAR / wash can / "flat par"

**Purpose.** The **uplighter and static color-wash brick.** PARs (traditional "can" shape) and slim **flat pars** use RGBW / RGBA / **Hex (RGBWA+UV)** LEDs to throw saturated, even color onto walls, drapes, risers, and the band. No movement, no gobos — just clean, controllable color. They also serve as cheap **blinders/floods** when pointed at the crowd. The venue's **VPar / ThinPAR** are this class.

**Typical DMX channel layout (common 4/6/8-channel modes, from RGBW/Hex par manuals).**

| Mode | Channels |
|---|---|
| Basic 4-ch (RGBW dimmer) | 1 Red · 2 Green · 3 Blue · 4 White |
| Common 8-ch | 1 Master dimmer · 2 Red · 3 Green · 4 Blue · 5 White/Amber · 6 Strobe/shutter · 7 Color macros · 8 Programs (Auto/Jump/Fade/Pulse/Sound) |

(Hex fixtures add **Amber** and **UV** channels; RGBA swaps White for Amber.)

**Placement & rigging.** Classic use is **on the floor against walls/drapes as uplight** (every few feet for even coverage), on the deck as **low backlight/shin-busters**, or clamped to truss for top color. Tiny and light, but still safety-cable anything overhead. For even uplight, **match spacing to beam angle** so color bands overlap.

**Where it shines.** **Uplighting** rooms; static saturated color **base layer** behind moving lights; quick full-room color changes between songs; **audience blinder/flood** moments when aimed out; UV/blacklight looks (Hex/UV pars) for special segments.

**Pro tips & common mistakes.**
- For uplight, **overlap the edges** of adjacent pars — gaps and hot spots scream amateur.
- Use **dimmer-first** patching (master dimmer channel) so you can fade the whole rig cleanly rather than pulling RGB to zero unevenly.
- Color-match your pars: different batches/brands drift, so build a **calibrated color palette** rather than "R255 G0 B0 everywhere."
- Mistake: using pure-white RGB for key — add amber/white or use a Hex fixture for usable skin tones.
- Mistake: forgetting they're a great **cheap blinder** — flat pars aimed at the crowd add impact for free.

---

## 5. LED BAR / batten / pixel bar (Tetra-style)

**Purpose.** **Linear eye-candy and pixel motion.** A pixel bar is a strip of **individually addressable LED cells** (e.g. Chauvet COLORband PiX / ADJ Ultra Bar = 12 cells; COLORado/PXL battens = 8/16 cells; ADJ Tetra-style bars). Because each cell is its own RGB(W) pixel, the bar does **chases, sweeps, gradients, pixel-mapped graphics, and audience effects** that a single-color wash can't. The venue's **Tetra Bar** is this class. Many modern battens add **tilt and zoom** so the linear element can also move.

**Typical DMX channel layout (very mode-dependent — from ADJ Ultra Bar 12 / COLORband PiX; modes from 3 up to ~20+ ch).**

| Mode | Channels |
|---|---|
| Compressed (~5 ch, whole bar one color) | 1 Dimmer · 2 Red · 3 Green · 4 Blue · 5 Strobe/programs |
| Pixel mode (per-cell; scales with cell count) | optional header (master dimmer · strobe · mode), then **R,G,B(+W) per cell** — 12 cells → 36–48 color channels |
| Motorized batten add | Tilt (+ fine) · Tilt speed · Zoom · control/reset |

Choose the **lowest channel mode that gives the control you need** — full pixel mode eats universe space fast.

**Placement & rigging.** Bars love **linear positions**: vertical "totems" flanking the stage, horizontal rows on truss edges or stage lip for the "wall of pixels," around DJ booths/risers, or as set-dressing strips. Daisy-chain DMX (many support **Art-Net/sACN/Kling-Net** for pixel mapping). Mind the universe budget when running many in pixel mode.

**Where it shines.** **Pixel chases and sweeps** that run with the music; left-to-right "comet" runs across a row; color **gradients** during builds; pixel-mapped text/graphics via a media server; **audience-facing flash/chase** for drops; booth/riser accent that makes a small stage look produced.

**Pro tips & common mistakes.**
- **Pixel mapping vs. effects engine:** for simple chases use your console's effect/FX engine; for graphics/video, drive from a media server over Art-Net/sACN.
- Run in **the smallest adequate DMX mode** — don't burn 48 channels per bar if a chase needs 8.
- Keep cell **spacing/orientation consistent** across multiple bars or your "sweep" looks broken.
- Mistake: rainbow-everything ("skittles") — pixel control is for *motion and gradients*, not every cell a different color.
- Mistake: forgetting the bar can also be a plain **wash** — sometimes the whole bar one color is the best look.

---

## 6. STROBE and BLINDER

**Purpose — STROBE (Atomic/Freq-style).** Raw **impact**. A strobe (e.g. *Martin Atomic 3000*) is a high-output flash source for **drops, hits, and stutter effects**. It delivers flashes at controllable **rate and duration** plus effect macros (ramp, lightning, random, spikes, single shot). It's about the *moment*, not coverage.

**Purpose — BLINDER.** **Audience contact and warm impact.** A blinder (e.g. *Chauvet Strike* series — warm-white COB pods) is pointed **at the crowd** to blind/warm-wash them at peak moments, and doubles as a strobe and a wide flood. The warm tungsten-like color is flattering and high-energy.

**Typical STROBE DMX layout (Martin Atomic 3000 DMX, 4-ch mode — manual/spec):**

| # | Channel | Values (spec) |
|---|---|---|
| 1 | Intensity | 0–5 off; 6–255 dim→bright |
| 2 | Flash duration | 0–255 = 0…650 ms |
| 3 | Flash rate | 0–5 single flash; 6–255 = 0.5…25 Hz |
| 4 | Effects | 0–5 std strobe; 6–42 ramp up; 43–85 ramp down; 86–128 ramp up/down; 129–171 random; 172–214 lightning; 215–255 spikes |

(Also offered in 1-ch [fixed duration] and 3-ch modes; LED Atomics add an RGB "aura" backlight with extra channels.)

**Typical BLINDER DMX layout (Chauvet Strike-class; 3/4/5/6-ch personalities):**
- *Simple:* 1 Dimmer · 2 Strobe
- *Multi-zone/pod models:* per-pod **dimmer** channels + **strobe** + **macros/programs** (independently focusable pods get a dimmer each)

**Placement & rigging.** Strobes: anywhere they can punch the whole space — back truss, over the stage, or facing the room. Blinders: **face the audience**, typically front/downstage truss or stage lip, rows for even crowd coverage. Both are bright and heavy-duty — rated clamps + safety, and keep them off conventional dimmers (most are non-dim/relay or have their own intensity channel).

**Where it shines.** The **drop** (strobe slam the instant the beat returns); **build-ups** (accelerating flash rate); **lightning/spikes** macros for dramatic stabs; blinders for the **"hands up" warm wash** on a chorus or final breakdown; single-flash punctuation on a downbeat.

**Pro tips & common mistakes.**
- **Use sparingly.** A strobe everywhere = exhausting and loses all impact. Save it for *the* moment.
- **Photosensitive-epilepsy / safety:** signage and restraint with sustained strobing; know your venue's rules.
- Blinders read best **warm and quick** — a flash of warm light at the crowd is energy; a long hold just hurts eyes.
- Mistake: confusing the two — a *blinder* warms and lifts the crowd; a *strobe* freezes motion and slams. Different tools.
- Mistake: leaving blinders/strobes on a fade-capable dimmer that can't keep up — drive them on their dedicated channels.

---

## 7. Multi-beam centerpiece / DERBY / moonflower / FX (Swarm-style)

**Purpose.** **Cheap, dense mid-air movement for small rooms.** A combo-FX centerpiece (e.g. *Chauvet Swarm 5 FX* — a 3-in-1: **RGBAW rotating derby + red/green laser + white strobe**) throws dozens of spinning colored beams plus laser dots and strobe from a single box. It's the budget way to fill a DJ booth or small club with movement and beams without a truss full of moving heads.

**Typical DMX layout (Chauvet Swarm 5 FX — 9 channels, manual/spec):** channels map (per the Swarm manual) to **derby color/auto, derby motor rotation, derby strobe, laser on/color/pattern, laser motor, white-strobe rate, overall program/mode, sound-active/speed** — ~9 ch total; strobe rate up to ~20 Hz.

**Placement & rigging.** Centerpiece position — **booth front, ceiling center, or a single stand** firing out over a small floor. It's a "point it at the room" effect, not a coverage tool. Light enough for a stand or single clamp + safety.

**Where it shines.** **Small clubs, mobile DJ, bars, parties** — instant "lots happening" on a budget; sound-active mode for unattended rooms; derby for spinning colored beams, laser layer for texture, strobe for hits.

**Pro tips & common mistakes.**
- **Haze still helps** — the derby beams and laser only read mid-air with atmosphere.
- Great as a **filler/centerpiece**, but not a substitute for real moving heads on a proper stage — it can't be precisely aimed or color-matched to a designed look.
- Mistake: relying on it as the *whole* show on a real stage; mistake: running it full-tilt sound-active alongside a programmed rig so it fights your cues. Tame it or gate it to specific moments — in this fork, a low-priority sound-active state can be overridden by higher-priority designed cues (see [[club-rig-mayans]]).
- Built-in lasers are still lasers — observe the same **aiming/audience-scanning caution** as a standalone laser.

---

## 8. LASER and HAZER / FOGGER

### LASER
**Purpose.** **The sharpest, most "tech" mid-air effect** — coherent beams and, with fast scanners, **graphics/text/logos/animation**. Pro laser projectors are **RGB** (full color) and controlled via **ILDA** (from laser software, for graphics) and/or **DMX** (for integration with the lighting console). Scanner speed (e.g. **30K–35K @ 8°**) determines how clean graphics/text are; beam-only systems are simpler.

**Typical DMX layout (varies widely — approximate):** **on/off & safety/arm, pattern/scene select, color (R/G/B or color select), horizontal (pan) position & scan, vertical (tilt) position & scan, rotation/zoom, scan speed, strobe/blanking.** (Real graphics shows run over **ILDA from laser software**, not DMX.)

**Safety & regulations (non-negotiable).** Lasers are typically **Class IV** — capable of eye/skin injury and igniting materials. Plan: **audience-scanning rules and designated beam zones, beam termination, IEC 60825-1 compliance, and in the US CDRH/FDA filings (variance) where applicable.** Hardware controls: **key switch, E-stop, interlocks, emission indicators**, plus pre-show checks and SOPs. **Do not scan the audience** unless the system and your measurements/permits explicitly support it — otherwise keep all beams above head height with hard terminations.

**Where it shines.** Big EDM **drops** (beam fans and tunnels), **logo/branding** and text reveals, liquid-sky and graphic looks, tight beam crisscross that even beam fixtures can't match.

**Pro tips & mistakes.** Lasers **must** have haze to read. Mistake: pointing a Class IV at the crowd "because it looks cool" — the cardinal sin and a legal/medical liability. Mistake: using a slow-scanner unit for graphics — text will smear.

### HAZER / FOGGER — *why atmosphere is mandatory for beam work*
**Purpose.** **To make light visible in the air.** This is the single most important and most overlooked piece of a beam/laser rig — and given the Mayans rig is 13 beams, it is the foundation everything else stands on.

- In clean air a beam or laser is just a **dot where it lands** — you don't see the shaft. Atmospheric particles **scatter the light** so beams become **three-dimensional pillars** and moving-head effects become aerial patterns that fill the space. As the pros put it: *"With haze you see a light beam; without haze you see only the spot the beam lands on. Aerial visibility is not brightness, it's atmosphere."*
- **HAZE vs FOG:** A **hazer** makes a **fine, even, long-hanging, nearly-invisible** suspension — ideal for continuous beam visibility without visible clouds. A **fog/smoke machine** makes **dense, short-lived clouds** — for dramatic reveals, low-fog floor effects, and bursts, but it's lumpy and dissipates fast. For **continuous beam and laser work, a hazer is the right tool**; foggers are for effect bursts.

**Placement & operation.** Hazer **upwind / at the edges** so haze drifts evenly across the beam field; let it build a thin, even layer rather than blasting clouds. Account for **HVAC/airflow** (it pulls haze out fast) and **smoke detectors / venue policy** (interlocks, notify fire systems). Use **water-based fluid** appropriate to the machine.

**Where it shines.** Any show with beams, washes-in-air, or lasers — i.e. essentially every modern club/touring show. Run a thin continuous haze; add fog bursts for specific dramatic moments.

**Pro tips & mistakes.**
- **Less is more** — good haze is *invisible* until a beam crosses it. Thick haze looks like fire and washes out contrast.
- **Pre-build the haze** before doors/cues; it takes time to fill a room evenly.
- Mistake #1 in all of beam lighting: **buying beams/lasers and no hazer.** The fixtures will look broken. Atmosphere first.
- Mistake: using a **fogger as a hazer** for continuous beams — clouds and dead patches, then nothing.

---

## Venue fixture → ideal show-role map

| Venue fixture | Class | Primary role | Best moments | Placement | Notes |
|---|---|---|---|---|---|
| **BEAM230** (×13: V1/V2/V3 variants) | Moving-head **Beam** (7R 230W, Sharpy-class) | Tight aerial beams, **fans**, crisscross, mid-air columns — the rig's signature | EDM/club **drops & builds**, sweeps, intros | Back/mid truss, floor uplight, symmetrical rows for fans | **Haze mandatory.** 16 ch; 0–4° beam, 8-facet prism, frost, ≈540°/250° P/T. Address in known L→R order for clean pan-offset fans |
| **WASH** | Moving-head **Wash / wash-zoom** | Color washes, stage coverage, eye-candy (pixel ring if equipped) | Song mood/color, build fades, saturated backwash, ballads | Back/mid truss for color from behind; side/floor for sweeps | **Zoom to blend**; White/Amber for skin tones; ring = accent only |
| **Revolver Wash** | Moving-head **Wash** (with rotating/pixel-ring FX) | Color coverage + rotating ring eye-candy accent | Mood color plus motion accent on builds/choruses | Mid/back truss | Drive base color from body; let the revolving ring add the motion layer |
| **VPar / ThinPAR** | **LED PAR / flat par** (RGBW/Hex) | Uplighting, static color base layer, cheap blinder/flood | Room uplight, color base behind movers, UV looks, crowd flood | Floor against walls/drapes; deck backlight; truss top color | Overlap spacing for even uplight; master-dimmer patching; color-match units |
| **Tetra Bar** | **LED pixel bar / batten** | Pixel chases, sweeps, gradients, audience eye-candy | Build sweeps, comet runs, pixel-mapped graphics, drop flashes | Vertical totems, stage-lip rows, riser/booth accents | Smallest adequate DMX mode; consistent cell orientation; avoid "skittles" |
| **Swarm 5 FX** | **Multi-beam centerpiece** (derby + laser + strobe FX) | Cheap dense mid-air movement & beams for small floor | Small-floor/DJ fill, sound-active filler, accent FX | Booth front / ceiling center / single stand firing over floor | ~9 ch; haze still helps; gate it (low priority) so it doesn't fight programmed cues |
| **Generic RGB** | **Generic LED / RGB** (par, strip, or fixture) | Flexible color fill / accent / base layer | Color base, accent washes, fill where a dedicated fixture isn't needed | Wherever color fill is required | Plain RGB — add amber/white-substitute looks carefully; calibrate to the rest of the palette |
| *(not in rig) Spot* | Moving-head **Spot** | Gobo texture / projection / logos | Texture builds, breakups, branding | Front/mid truss | **None of the current venue fixtures project gobos** — add a spot if you need texture |
| *(not in rig) Strobe* | **Strobe** (Atomic/Freq) | Impact, drops, stutter, lightning | The drop, accelerating builds, single-flash punctuation | Back/over-stage or facing room | 4-ch: intensity/duration/rate/effects; use sparingly; epilepsy caution |
| *(not in rig) Blinder* | **Blinder** (Strike) | Warm audience wash + impact | "Hands-up" choruses, final breakdown, crowd contact | Front/downstage truss, facing audience, in rows | Warm + quick reads best; dedicated channels, not slow dimmers |
| *(not in rig) Hazer* | **Atmosphere** | Makes all beams/lasers visible | **Every show with beams** — run a thin continuous layer | Upwind/edges; mind HVAC & smoke detectors | **Without this the 13 BEAM230s / Swarm look broken.** Haze ≠ fog bursts |

**Bottom-line layering philosophy for the Mayans rig:** the **VPar/ThinPAR/Generic RGB** build the **color and uplight base**; the **WASH and Revolver Wash** paint **mood and coverage**; the **13 BEAM230s** deliver the **aerial signature** (synchronized fans on the drop) that defines the room; the **Tetra Bar** adds **linear pixel motion**; the **Swarm 5 FX** fills **small-floor movement** as gated accent. The biggest gaps versus a full touring rig are a **hazer** (critical — the beams need atmosphere to read), a dedicated **strobe/blinder** for crowd impact, and a **spot** for gobo texture. For how these layers combine into cues — and how the fork's per-function **priority** values let designed looks override sound-active/idle states — see [[lightshow-design-principles]], [[effect-recipes-cookbook]], and [[club-rig-mayans]].

---

*Accuracy note:* Sharpy/BEAM230, Martin Atomic 3000, and Chauvet Swarm channel/value details are from those products' manuals/spec sheets and marked where quoted. Spot, Wash, PAR, pixel-bar, and laser channel orderings are **representative/typical composites** — real channel order depends on the exact model and DMX mode, so patch every fixture from its own current DMX chart (or, for the Mayans custom fixtures, from its `.qxf` — see [[qlc-fixture-definition-format]]).