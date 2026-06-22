---
name: cross-platform-concepts
description: "Pro lighting concepts from ONYX/grandMA/Avolites/MagicQ/Resolume (groups, palettes, cue stacks, effect engine, busking, timecode) mapped to QLC+ equivalents and workarounds"
metadata:
  node_type: memory
  type: reference
  originSessionId: 0a2fa9d9-bc6b-46d6-bdbb-e98d6dad045a
---

# Cross-platform pro lighting concepts mapped to QLC+

A working lighting designer's guide for this venue. The first half explains how shows are actually *structured* on the five major platforms — Obsidian **ONYX**, **MA Lighting grandMA2/grandMA3**, **Avolites Titan**, **ChamSys MagicQ**, and **Resolume Arena/Avenue** (media/pixel). The second half maps each professional concept to its **QLC+** equivalent, names honestly what QLC+ *lacks*, and gives the practical workaround. For the venue-specific build patterns these concepts feed into, see [[lightshow-design-principles]], [[effect-recipes-cookbook]], and the authoritative QLC+ behavior in [[qlc-functionality-reference]].

---

## Part 1 — How pros structure a show (shared mental model)

Regardless of brand, a modern show is built in roughly this dependency stack, bottom to top:

```
Patch  →  GROUPS  →  PALETTES/PRESETS  →  CUES  →  CUE LISTS/STACKS  →  EXECUTORS/PLAYBACKS (+ PAGES)
                              ↑                                              ↑
                         FX ENGINE  ─────────────────────────────────  BUSKING / TIMECODE
```

The golden rule shared by all real consoles: **build referenced, not hard-coded.** A cue should *point at* a palette ("these fixtures, that blue, that position"), so that when you tour to a new venue you re-point the palette once and every cue, chase and effect that referenced it updates automatically. QLC+'s biggest architectural gap (covered below) is precisely that its building blocks store *absolute values*, not references.

---

### 1. Fixture Groups

**Concept.** A named selection of fixtures you recall instantly ("All Spots", "Front Truss", "Floor PARs"). Groups are the unit you point everything else at. Crucially, a group also carries an implicit **selection order** (fixture 1, 2, 3…) which downstream effects and spread use to know "which way the wave travels."

| Platform | Group behavior |
|---|---|
| **ONYX** | Groups recall a fixture set; presets/cues build on top. Selection and order feed FX. |
| **grandMA2/3** | Groups live in a pool; selection order is recorded with the group and is the backbone of fanning and phasers. |
| **Titan** | Groups recorded to handles; record modes (by Fixture/Channel/Stage/Quick Build) respect grouping. |
| **MagicQ** | Groups are first-class; FX are *built on groups* so they re-map per venue. |
| **Resolume** | "Groups" = layer groups / Lumiverse fixture arrangement, not lights — but conceptually the same: organize addressable elements. |

---

### 2. Palettes / Presets — the backbone of fast programming

**Concept.** A **palette** (MA/Titan/MagicQ term) or **preset** (ONYX term) is a *stored, named, referenceable* value for one functional attribute group: **Position**, **Color**, **Beam**, **Gobo**. You program a cue by *recalling palettes* ("Spots → Center → Deep Blue → Open"), and the cue stores a *reference*. Update "Center" once and every cue/effect using it follows. This is the single biggest speed and tour-resilience win in pro programming.

Palette **scope** matters (grandMA model, broadly applicable):

| Scope | Behavior | Typical use |
|---|---|---|
| **Selective** | Works only on the exact fixtures stored | Position (every fixture aims differently) |
| **Global** | Works per fixture *type* from one stored unit | Type-specific color/gobo |
| **Universal** | Any same-attribute value applies to any fixture | Color (best practice: Color = Universal) |

- **ONYX** — Presets are "the essential building block," split into functional groups (Color/Gobo/…) and can hold value, timing *and* FX.
- **Titan** — Separate Colour / Position / Gobo&Beam palette windows; palettes contain only what's in the programmer, and can even hold shapes/pixel-map effects.
- **MagicQ** — Palettes for position/colour/beam; FX can reference palette values, so changing the palette re-shapes the FX.
- **grandMA3 v2.4** broadened presets to work across fixture types.

---

### 3. Cues + cue timing (fade / delay / follow)

**Concept.** A **cue** is a snapshot of a look plus *per-cue (often per-parameter) timing*: **fade** (transition time), **delay** (wait before moving), and **follow/wait** (auto-advance after N seconds). Pros split timing — e.g. intensity fades in 3 s while position snaps, or color fades over 8 s with a 2 s delay.

- **ONYX** — Cue stores chosen parameter/timing/FX values; cuelists default to LTP.
- **MA / Titan / MagicQ** — All support in/out fade, delay, and follow/wait, frequently split per feature group (Intensity/Position/Color/Beam).

---

### 4. Cue stacks / cue lists

**Concept.** An ordered list of cues played with **GO** (next), **BACK**, and **GOTO**. The theatrical, repeatable spine of a show. Each can run as a single fader's stack, enabling many independent looks.

---

### 5. Executors / faders / playbacks + pages

**Concept.** **Playbacks/executors** are the physical (or touchscreen) controls onto which you assign cue lists, chases, single cues, groups or macros. A **fader** scales intensity/rate; a **GO/flash button** fires. **Pages** multiply a finite number of faders into hundreds of assignments by swapping the bank. This is the live control surface.

- **MagicQ** — Programmer + playbacks + preset faders coexist without mode-swapping, ideal for busking; executors fire cues, select groups, run macros, or proxy other buttons.
- **grandMA** — Executors organized into pages; a common idiom is a dedicated **rate/special master per effect**.

---

### 6. The Effect / FX engine (parametric movement & color)

**Concept — the most important one to understand.** Instead of keyframing motion, pros run a **math waveform** (sine, cosine, ramp/sawtooth) on a parameter and shape it with:

| Control | What it does |
|---|---|
| **Rate / Speed** | Cycles per time (BPM-locked or seconds); a per-line Rate multiplier scales it |
| **Size / Width / Amplitude** | How far the value swings |
| **Offset / Base** | The center the wave swings around |
| **Phase / Spread** | *The key to "the wave"* — each fixture (in selection order) starts the cycle at a different point (0–360°), turning a circle into a chase/fan/wave |
| **Fan** | Spreads a *static* value across the group (e.g. symmetric tilt fan) rather than animating it |
| **Wings / Blocks / Parts** | Mirror or segment the spread (wing of 2 = first half L→R, second half R→L, in sync) |
| **Direction** | Forward / reverse / bounce / random |

grandMA3 reframes this as **Phasers** (successor to MA2 "Forms") — same math. MagicQ's FX engine generates the same parametrically and can drive **palette-referenced** FX. ONYX gives **every parameter its own FX section**, and FX values can exist with *no* base value so you layer effects live.

---

### 7. Busking workflow

**Concept.** Live, improvised, beat-driven operation with no fixed cue order: fire palettes onto groups on the fly, layer effects, ride rate/size faders to music. Demands (a) clean groups, (b) comprehensive palettes, (c) a logical executor/page layout, (d) effects on dedicated rate masters. MagicQ is famous for this because programmer + playbacks + presets all mix live.

---

### 8. Timecode

**Concept.** Locking the cue list to an external clock — **SMPTE/LTC** (audio timecode), **MTC** (MIDI), or network — so cues fire at exact HH:MM:SS:FF. Used for tightly-tracked, non-negotiable shows (festivals, broadcast, theme parks). ONYX has a dedicated **Timecode cuelist** where each cue carries a trigger time.

---

### 9. Pixel-mapping

**Concept.** Treat a group of LED cells (battens, matrices, web) as a low-res screen and render video/text/generative content onto it via a **grid that mirrors the physical layout**. Titan's Pixel Mapper drives whole fixture groups "as if one entity"; MagicQ maps movies/bitmaps/text/live feeds in 2D/3D; Resolume is the media-server end — it outputs clip pixels to a **Lumiverse** (≤512 ch each) over Art-Net/sACN to the rig.

---

### 10. HTP vs LTP, tracking, intensity master / grandmaster

| Concept | Definition | Typical assignment |
|---|---|---|
| **HTP** (Highest Takes Precedence) | Intensity pile-on: 50% + 75% → 75%; remove the 75% and it falls back to 50% | **Dimmer / intensity** |
| **LTP** (Latest Takes Precedence) | "Last word wins" — the most recent change owns the parameter | **Color, gobo, pan/tilt** |
| **Tracking** | Changed values store in a cue; *unchanged* values **track through** later cues until a new move. Enables edit-once propagation and **move-in-dark** | (architecture-wide) |
| **Intensity master / Grandmaster** | Any playback can scale its stack's intensity; the **Grandmaster** scales *all* output and overrules everything | (masters) |

---

### 11. Selection order & spread

**Concept.** The *order* you pick fixtures (or the order baked into a group) defines how spread/fan/phase distributes across them — pick 1→8 and a chase runs L→R; reverse selection and it runs R→L; pick odds-then-evens for interleaved looks. Selection order is the hidden input to every parametric effect.

---

## Part 2 — The QLC+ equivalent of each concept (and what it lacks)

QLC+'s engine is built from **Functions** (Scene, Chaser, Sequence, EFX, RGB Matrix, Collection, Show, Audio, Video, Script) plus the **Virtual Console** (VC) control surface and **Simple Desk**. Here is the honest mapping. (For exact function behavior and XML, see [[qlc-functionality-reference]].)

| Pro concept | QLC+ equivalent | Honest gap | Practical workaround |
|---|---|---|---|
| **Fixture Group** | **Fixture Group** (also defines the physical grid for RGB Matrix) | Group ordering is mainly meaningful to EFX/RGB Matrix; no rich "selection order" buffer for arbitrary live re-ordering | Pre-build multiple Groups with different element orders (L→R, R→L, odds/evens) to fake selection-order variants |
| **Palettes / Presets (referenced)** | **No first-class palettes.** Closest: a **Scene** storing only the channels of one attribute group | **QLC+'s biggest architectural gap** — Scenes store *absolute values*, not references, so nothing auto-updates downstream | Make per-attribute "preset Scenes" ("COL Deep Blue", "POS Center") containing only that attribute's channels, then layer them via **Collections**. Touring = manually re-record those preset Scenes |
| **Cue with fade/delay/follow** | **Scene** (the look) + **Chaser** step **fade-in / hold / fade-out / duration**; **Sequence** for scene-based cue runs | Timing is per *step*, not richly per-parameter; no split intensity/position/color times within one cue | Separate fast and slow attributes into different Scenes/Chasers running in parallel to emulate split timing |
| **Cue stack / cue list** | **VC Cue List** widget (driving a Chaser of Scenes) with GO/BACK; **Chaser** as the underlying ordered list | No tracking through the list | Build cues as full looks (cue-only style) since tracking isn't available |
| **Executors / faders / playbacks + pages** | **VC Buttons, Sliders, Speed Dials, Cue List** on **VC Frames**; multi-page via the Frame's pages | No motorized fader feedback unless via MIDI surface; pages are per-Frame, less fluid than console-wide pages | Use a **Frame with pages** + a MIDI/OSC control surface profile to mimic banked executors |
| **FX engine (rate/size/offset/spread/fan/wings/direction)** | **EFX** for pan/tilt (algorithms: Circle, Eight, Line, Diamond, Lissajous; width/height/X-Y offset/rotation/startoffset; fixture order **Parallel/Serial/Asymmetric**; direction; relative mode). **RGB Matrix** for color/intensity | **No general parametric fan/spread engine for arbitrary attributes.** EFX is position-only; **no sine table for color/dimmer/beam**, and **no live rate/size/phase faders** on an arbitrary attribute | Movement: EFX + Serial/Asymmetric order ≈ phase spread; reverse via Direction. Color/dimmer "effects": **RGB Matrix** scripts or **Chasers** of stepped Scenes; expose speed via a **VC Speed Dial** |
| **Pixel-mapping** | **RGB Matrix** (algorithms: **Text, Image/animated GIF, Audio spectrum, Script(JS)**) on a **Fixture Group** grid; plus **Video** function | Less powerful than Titan/MagicQ/Resolume; no multi-layer media compositor, limited live-feed | Heavy media: **Resolume/MadMapper as media server → Art-Net/sACN into the rig**; QLC+ handles conventional lighting + simple RGB Matrix FX |
| **Default look / multi-function recall** | **Collection** (fires many functions at once) | Just a parallel trigger, not a referenced look with timing inheritance | Use Collections as "master looks" combining preset-Scenes + EFX + Matrix |
| **Timecode-locked cue list** | **Show** function (multi-track timeline with audio/video tracks + function blocks); **MIDI Beat Clock** for BPM sync | **No native external SMPTE/MTC timecode-to-cue triggering** — QLC+ can *output* an LTC audio file but not *chase* incoming timecode | Place functions on the **Show timeline** against an embedded **audio track** (pseudo-timeline). For true TC, slave via MIDI Beat Clock or front it with a TC tool driving MIDI cues |
| **Busking** | **Simple Desk** + a **VC Frame** of Buttons/Sliders/Speed Dials + **Solo Frame** | No live programmer that mixes with playbacks the MagicQ way; no on-the-fly palette-onto-group | Pre-build a dense VC: preset-Scene buttons, group looks in a **Solo Frame**, EFX/Matrix on **Speed Dials**, intensity on **Submasters**; rehearse — improvisation is constrained |
| **HTP** | Honors **HTP for intensity** channels by default in function mixing | — | Works as expected for dimmers |
| **LTP** | **LTP** for non-intensity (color/position/beam) via function override and Simple Desk | Mixing model simpler than a console; this fork reworks per-function **priority** | See the repo's priority-rebuild work — it specifically improves this layer |
| **Tracking / move-in-dark** | **Effectively none** — QLC+ is cue-only; Scenes carry absolute values | **No cue tracking, no move-in-dark.** Editing one value does *not* propagate forward | Program full looks per cue; for "move in dark," manually pre-position fixtures at 0% intensity in the preceding Scene |
| **Intensity master / Grandmaster** | **VC Grand Master** widget; per-stack **Submaster** (Slider in Submaster mode); **Solo Frame** for exclusive selection | Grandmaster is intensity-scaling; fewer master flavors (no true inhibitive masters per se) | Use Submasters as group inhibitive masters; Grand Master for the global kill |
| **Selection order & spread** | Group order + **EFX fixture order** (Parallel/Serial/Asymmetric) + RGB Matrix grid order | No live "select in this order, now spread" — order is baked in at build time | Maintain duplicate Groups with alternate orderings for different spread directions on demand |

---

## Bottom line for a designer moving to QLC+

QLC+ gets you **80% of the *output*** of a pro console at zero cost, and its **EFX (movement)** and **RGB Matrix (pixel)** engines are genuinely good. What you give up is the **referenced, parametric, tracking** *architecture* that makes big consoles fast to program and re-tour:

1. **No real palettes/presets** — you rebuild absolute Scenes per attribute and re-record them by hand when the rig changes. The single biggest day-to-day difference.
2. **No general fan/spread/phase effect engine** outside position (EFX) and pixel (RGB Matrix) — color/beam "effects" must be faked with Chasers or Matrix scripts.
3. **No tracking / move-in-dark** — everything is cue-only; program complete looks.
4. **No native timecode-to-cue** — use the Show timeline against an audio track, or slave via MIDI Beat Clock / an external TC tool.
5. **Busking is more pre-built than improvised** — invest heavily in a clean Virtual Console (Solo Frames, Submasters, Speed Dials, preset-Scene buttons) before showtime.

**Design pattern that best emulates the pros in QLC+:** per-attribute "preset" Scenes → combined via Collections → fired from a paginated VC Frame, with EFX/RGB Matrix on Speed Dials and Submasters as inhibitive masters. It's the closest you'll get to a palette-driven, busk-able console workflow within QLC+'s absolute-value engine. Concrete builds of this pattern live in [[effect-recipes-cookbook]] and the venue-tuned philosophy in [[lightshow-design-principles]].