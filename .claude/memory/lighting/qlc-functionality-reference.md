---
name: qlc-functionality-reference
description: "What every QLC+ feature does and how it is structured: all function types, Virtual Console widgets, Simple Desk, I/O, RGB-matrix JS — a capabilities map"
metadata:
  node_type: memory
  type: reference
  originSessionId: 0a2fa9d9-bc6b-46d6-bdbb-e98d6dad045a
---

# QLC+ functionality reference

What QLC+ can do and how to use it, oriented toward programming a nightclub rig and toward an AI auto-generating functions for this venue. This is the conceptual/operational map; for the exact XML element names and attribute orders see [[qlc-save-file-format]] and [[qlc-fixture-definition-format]]. For the venue's actual patch and fixtures see [[club-rig-mayans]] and [[fixture-types-and-roles]]; for applied patterns see [[effect-recipes-cookbook]] and [[lightshow-design-principles]].

QLC+ is split into functional areas you switch between via top tabs: **Fixture Manager** (patch), **Function Manager** (build cues/effects), **Virtual Console** (live control surface), **Simple Desk** (manual DMX desk), **Show Manager** (timeline), and **Input/Output** (DMX/MIDI/OSC wiring). The DMX engine runs continuously underneath, merging everything running into the universe output.

---

## 1. Function types

Functions are the reusable building blocks. Every function shares a common machinery: a unique `ID`, a `Name`, an optional folder `Path`, a `BlendMode` (how it merges into the universe: `NormalBlend`, `MaskBlend`, `AdditiveBlend`, `SubtractiveBlend`), and in this fork a per-function `Priority` (omitted when 0). Most timed functions also share **speed** (FadeIn / FadeOut / Duration in ms), **Direction** (Forward/Backward), **RunOrder** (Loop / SingleShot / PingPong / Random), and **Tempo** (Time vs Beats; in Beats mode all speed values are beat counts at 1000 = 1 beat).

### 1.1 Function type chooser

| Type | What it does | Use it when | Key parameters |
|---|---|---|---|
| **Scene** | A static look: fixed DMX values for a set of fixture channels, reached via a fade. | Any held state — a color wash, a fixed gobo/position, a blackout-to-this look. The atom everything else is built from. | `<Speed>` FadeIn/FadeOut (fade to/from the look). No hold (a scene holds until stopped). Per-fixture channel/value lists. |
| **Chaser** | An ordered list of steps, each step being another function (usually Scenes), stepped through over time. | Sequencing looks: color chases, look-to-look cue lists, anything "go through these in order". The workhorse of cue lists. | Per-step FadeIn / **Hold** / FadeOut; Direction; RunOrder; SpeedModes (Common / PerStep / Default — whether timing is shared, per-step, or inherited from each member). |
| **Sequence** | A Chaser subclass bound to ONE Scene; each step stores DMX value overrides for that scene's fixtures. | Programming a multi-step look against a single fixture set without making N separate Scenes — e.g. a hand-drawn movement/color sequence on the movers. | Same as Chaser, plus a bound Scene ID. Steps carry packed channel values, not function references. |
| **EFX** | A mathematical movement/animation generator: drives pan/tilt (or dimmer/RGB) along a parametric curve. | Continuous geometric motion on movers (circles, figure-eights, lines, lissajous) without hand-keying steps. | Algorithm; Width/Height (size); Rotation; per-axis Offset/Frequency/Phase; PropagationMode (how fixtures spread across the pattern); IsRelative; speed (Duration = one full cycle). |
| **RGB Matrix** | Runs a pattern algorithm across a 2-D grid of fixture heads (a fixture group laid out as a matrix). | Pixel effects on LED panels, PAR arrays, bars — text scrollers, images, plasma, audio spectrum, scripted patterns. | Algorithm (Plain/Text/Image/Audio/Script); colors; ControlMode (RGB/Amber/White/UV/Dimmer/Shutter); FixtureGroup; per-script properties; speed (Duration = step interval). |
| **Collection** | Fires several functions simultaneously. | "Press one thing, start many" — a macro/look that combines a wash scene + a mover EFX + a matrix effect at once. | None except its member list. No timing/direction of its own; each member runs with its own settings. |
| **Show** | A timeline: tracks, each track bound to a scene, with functions placed at absolute start times. | Pre-programmed timed shows synced to music — a full song's worth of cues laid out on a clock or beat grid. | TimeDivision (Time or BPM_x_4 + BPM); per-track functions with StartTime / Duration. |
| **Script** | A text program: sequential commands (start/stop functions, waits, channel sets, jumps, system commands). | Logic/choreography that's awkward as a chaser — conditional loops, randomized strobes, launching audio, orchestrating other functions over time. | RunOrder/Direction/Speed plus a list of command lines (see §5). |
| **Audio** | Plays an audio file. | Triggering stings, tracks, or SFX from a cue/button; audio-reactive setups feed the Audio Spectrum matrix/triggers. | Source path; Volume (gain 0–1); fade in/out via Speed; loop via RunOrder = Loop. |
| **Video** | Plays a video file or URL on a screen. | Projection/LED-wall content, IMAG, visual backdrops. | Source path/URL; Screen; Fullscreen; Geometry; Rotation; ZIndex; loop via RunOrder. |

### 1.2 Speed semantics, by type (important — they differ)

| Concept | Scene | Chaser/Sequence | EFX | RGB Matrix |
|---|---|---|---|---|
| **FadeIn** | fade up to the look | step fade-in (or inherited) | fade into motion | fade-in of pixels |
| **Hold** | n/a (holds until stopped) | **per-step Hold** (dwell time) | n/a | n/a |
| **FadeOut** | fade down on stop | step fade-out | fade out of motion | fade-out of pixels |
| **Duration** | n/a | per-step total (FadeIn+Hold) | **one full pattern cycle** | **interval between animation steps** |

Sentinels: `4294967295` = default speed (inherit), `4294967294` = infinite. In **Beats** tempo mode these millisecond fields become beat counts (1000 = one beat), so a chaser locked to the master BPM steps on the beat.

### 1.3 Run order and direction

- **Loop** — repeat forever (live default for chases/EFX/matrix).
- **SingleShot** — play once and stop (cues, one-shot stings, audio/video that shouldn't repeat).
- **PingPong** — play forward then backward, repeat (smooth back-and-forth sweeps).
- **Random** — steps in random order (organic, non-repeating chases).
- **Direction** Forward/Backward sets the starting direction; PingPong alternates it.

### 1.4 EFX algorithms

`Circle`, `Eight`, `Line`, `Line2`, `Diamond`, `Square`, `SquareChoppy`, `SquareTrue`, `Leaf`, `Lissajous`. Width/Height scale the figure (0–127), Rotation rotates it (0–359), and the per-axis Frequency/Phase deform it (Lissajous especially is driven by the X/Y Frequency ratio and Phase). **PropagationMode** controls how multiple fixtures share the figure: `Parallel` (all together), `Serial` (staggered start offsets march the pattern across fixtures), `Asymmetric` (per-fixture offsets). Each EFX fixture also has a per-fixture Mode: `PanTilt` (0), `Dimmer` (1), or `RGB` (2) — the same EFX math can drive intensity or color, not just movement.

### 1.5 RGB Matrix algorithm types

| Algorithm `Type` | Colors used | What it renders | Notes |
|---|---|---|---|
| **Plain** | 1 | Solid fill of the whole matrix in one color. | Simplest; good as a color base under other layers. |
| **Text** | 2 | Scrolling/animated text. | Content string, Font, Animation = `Letters` (static), `Horizontal`, or `Vertical`; X/Y offset. |
| **Image** | 0 | Displays/animates an image file across the grid. | Animation = `Static`, `Horizontal`, `Vertical`, or `Animation`; path stored relative to the workspace. |
| **Audio** | 2 | Audio spectrum analyzer (bars react to live audio). | "Audio Spectrum". Great for beat-reactive panels. |
| **Script** | per-script (`acceptColors`, default 2) | Runs a JavaScript pattern (`.js` in the rgbscripts folder). | Only the script NAME is saved; parameters are exposed as Properties. See §5.2. |

ControlMode picks which physical channel the matrix output drives (`RGB`, `Amber`, `White`, `UV`, `Dimmer`, `Shutter`) — e.g. set `Dimmer` to run a "pixel" effect across single-channel dimmers, or `White`/`UV` to animate those emitters specifically.

---

## 2. Virtual Console (live control surface)

The VC is the busking interface: a grid of widgets you build in Design mode and operate in Operate mode. Everything lives inside one root **Frame** (the bottom drawing area, default 1920×1080). Every widget can carry a keyboard `<Key>` shortcut and/or an external `<Input>` (MIDI/OSC/DMX) mapping, plus Appearance (colors/font/frame style) and geometry (snapped to a 5px grid). A global **Grand Master** lives in VC Properties (not a widget): it scales output (`ChannelMode` Intensity/All, `ValueMode` Reduce/Limit).

| Widget | Role in live control | Key controls |
|---|---|---|
| **Frame** | Container / layout group. Can have pages (flip between layouts), a collapsible header, and an enable toggle that disables all children. | AllowChildren/Resize, ShowHeader, Multipage (PagesNum + per-page Shortcut + Next/Previous + PagesLoop), Enable binding. Tag children with `Page="N"`. |
| **Solo Frame** | Like Frame but enforces **mutual exclusion** — starting one child's function stops the others. | Mixing (allow simultaneous), ExcludeMonitored. Use for "one look at a time" banks. |
| **Button** | The core trigger. Fires a function or a global action. | `<Function>`; Action = `Toggle`/`Flash`/`Blackout`/`StopAll`/`Restart`; Flash has Override + ForceLTP; StopAll has FadeOut; startup `<Intensity>` %; Key/Input. |
| **Slider** | Fader/knob. Three modes. | **Level** = drive raw DMX channels (LowLimit/HighLimit + channel list, optional Monitor); **Playback** = scale a function's intensity (+ optional Flash); **Submaster** = scale all widgets in its parent frame. WidgetStyle Slider/Knob, InvertedAppearance, CatchValues. |
| **Cue List** | Step through a chaser/sequence with go/next/prev/stop — the show-runner's cue stack. | `<Chaser>`; NextPrevBehavior; PlaybackLayout; Next/Previous/Playback/Stop bindings; optional side crossfader (SlidersMode + CrossLeft). |
| **XY Pad** | 2-D pan/tilt control for movers, with stored presets. | Per-fixture Axis X/Y sub-ranges (LowLimit/HighLimit 0–1, Reverse); Pan/Tilt position + input; Presets (Position / EFX / Scene / FixtureGroup). |
| **Speed Dial** | Sets fade/duration timings on attached functions live; tap-tempo (the venue's hand-sync tool). | `<Visibility>` bitmask (127 = normal: PlusMinus+Dial+Tap+H/M/S/ms, no Infinite); `<AbsoluteValue Minimum Maximum>`; `<Time>` ms value; per-attached `<Function FadeIn="m" FadeOut="m" Duration="m">id</Function>` where `m` is the multiplier enum **0=None/not-sent, 1=Zero, 2=1/16, 3=1/8, 4=1/4, 5=1/2, 6=One(1×), 7=2×, 8=4×, 9=8×, 10=16×** (so `Duration="6"` = send the dial's value 1:1 to that function's Duration). Tap/Mult/Div by external `<Input>` (`<Tap><Mult><Div><MultDivReset><Apply>`) and/or keyboard `<Key>`(tap)/`<MultKey>`/`<DivKey>`. Drives a chaser's step rate only if its `SpeedModes Duration="Common"`. |
| **Label** | Static text (section headers, page titles). | Caption is the text. |
| **Clock** | Clock / stopwatch / countdown; can schedule functions at wall-clock times. | Type Clock/Stopwatch/Countdown (+ H/M/S); per-time `<Schedule Function=.. Time=..>`; PlayPause/Reset for non-clock. |
| **Matrix** (VCMatrix) | Live front-panel for an RGB Matrix function: intensity + color + preset/animation buttons. | `<Function>` (the RGBMatrix), InstantApply; Visibility bitmask; Control buttons (Color1–5, knobs, resets, Animation/Image/Text). |
| **Audio Triggers** | Maps live audio-spectrum bands to functions/levels/widgets. | BarsNumber; per-band SpectrumBar targets; toggle Key/Input. |

Practical busking layout: a **Solo Frame** of look Buttons (one active look at a time), a row of Playback **Sliders** for stem control, a **Cue List** for the structured part of the night, an **XY Pad** for movers, a **Speed Dial** tied to your chases for tempo, and a **Matrix** widget for pixel-effect color/animation changes. Pages on the root frame let you swap whole surfaces per DJ/song section.

---

## 3. Simple Desk

A manual DMX desk for hands-on, unprogrammed control — like a physical lighting board overlaid on the engine. Two parts:

- **Faders area**: direct sliders over raw DMX channels (per universe), for grabbing fixtures manually without building a function. Output merges into the engine with SimpleDesk fader priority.
- **Cue Stacks / Cues**: capture the current desk state into Cues, group Cues into CueStacks, and play them back with fade times (each Cue and each CueStack has its own FadeIn/FadeOut/Duration). A Cue is just a map of absolute DMX address → value (0–255).

Use it for soundcheck/setup, busking a fixture that isn't in a function, or quickly snapshotting a look you can later promote into a Scene. Simple Desk state is saved with the workspace (top-level, separate from Functions).

---

## 4. Input / Output, universes, patching

**Universe** = one DMX line of 512 channels. A workspace can have many (`ID` 0-based). Each universe has:

- **Input patch** — where control data comes IN (one per universe): a `Plugin` (ArtNet, E1.31, DMX USB, OSC, MIDI, uDMX, Loopback…), a `Line`, and optionally an **input Profile** (maps a controller's MIDI/OSC messages to clean channels). This is how MIDI controllers / OSC apps drive VC widgets.
- **Output patch(es)** — where DMX goes OUT (a universe can have MULTIPLE outputs, e.g. mirror to ArtNet + a USB dongle). Plugin + Line.
- **Feedback patch** — return channel for motorized faders / LED rings on controllers.
- **Passthrough** — copy input straight to output (merge an external console through QLC+).

Protocol-specific settings (ArtNet IP/subnet/net universe, E1.31 multicast/priority, DMX-USB serial/frequency) are stored as an opaque key/value bag per patch (`<PluginParameters …/>`) — the engine round-trips whatever keys the plugin defines.

**Patching a fixture** (Fixture Manager): pick manufacturer/model/mode (or Generic), assign it to a **Universe** and a 0-based **Address**, and it claims `Channels` consecutive channels. Optional per-fixture tweaks: ExcludeFade (channels that snap rather than fade), ForcedHTP/ForcedLTP (override merge behavior), channel modifiers.

**Merging / priority**: per channel, output is resolved by capability — **HTP** (highest-takes-precedence, typical for intensity: brightest wins) vs **LTP** (latest-takes-precedence, typical for color/position/gobo: last change wins). Running sources are layered as faders with priorities (`Auto`, `Override`, `Flashing`, `SimpleDesk`). The global **Grand Master** scales the final result, then output is written to the plugin.

**Beat generator** (on the I/O map): a global tempo source — `Internal`, `MIDI`/`Plugin`, or `Audio` — at a set `BPM` (`<BeatGenerator BeatType="Internal" BPM="120"/>` in the `.qxw`). Functions in Beats tempo mode lock to it, so chases/matrix effects run on the beat. **Build caveat (verified in engine source):** only **Chaser** (`chaserrunner.cpp`, steps on `timer->isBeat()`) and **RGB Matrix** (`rgbmatrix.cpp`) honor Beats mode at runtime — **EFX does NOT** (no beat path in `efx.cpp`), so a beats-tempo EFX just runs on its ms `Duration`. And in the **Qt-Widgets build (QLC+ 4, what this venue runs) there is NO UI to hand-tap/change the Internal master BPM** — the only runtime BPM setters are MIDI clock, audio beat-detect, the JS `ScriptRunner.setBPM()`, and whatever loads from the file (the tappable BeatGenerators panel is QMLUI-only). So for **audio-free hand-sync, the Speed Dial driving a function's `Duration` in ms is the real tempo tool**; Beats mode is only useful here pinned to a fixed file BPM (or via MIDI/audio). Tempo sandbox demonstrating all of this: `SaveFile/Tempo Sandbox.qxw`.

**Fixture groups** are a separate concept: a named set of fixture heads, optionally arranged on a grid. Required for RGB Matrix (the matrix maps onto the group's grid) and useful for EFX propagation and for addressing many fixtures at once. **Channel groups** bundle arbitrary channels across fixtures under one master level (e.g. "all dimmers"), controllable from a single Level slider.

---

## 5. Scripting

### 5.1 Script function command language

A Script function is lines of `keyword:value` tokens; the first token is the command. Values can be quoted (`"…"`) to include spaces; `//` starts a comment (unless preceded by `:`, so URLs survive). Only `ch`, `val`, `arg` are valid as secondary tokens.

**Time grammar** (anywhere a time/value is taken): bare integer = ms (`wait:500`); suffixes `h`/`m`/`s`/`ms` combine (`1m30s`, `250ms`); decimal = seconds (`1.5` = 1500 ms); `∞` = infinite; `random(min,max)` picks a random value using the same grammar (`wait:random(40ms,120ms)`).

| Command | Syntax | Does |
|---|---|---|
| `startfunction` | `startfunction:<id>` | Start a function (tracked, auto-stopped on exit if `stoponexit` true). |
| `stopfunction` | `stopfunction:<id>` | Stop a function. |
| `stoponexit` | `stoponexit:<true\|false>` | Whether started functions stop when the script ends (default true). |
| `blackout` | `blackout:on\|off` | Global blackout. |
| `wait` | `wait:<time>` | Pause for a duration. Most-used command. |
| `waitfunctionstart` | `waitfunctionstart:<id>` | Block until a function starts. |
| `waitfunctionstop` | `waitfunctionstop:<id>` | Block until a function stops. |
| `setfixture` | `setfixture:<id> ch:<n> val:<0-255>` | Fade one fixture channel. (See caveat below re `time:`.) |
| `systemcommand` | `systemcommand:<prog> arg:<a1> arg:<a2>` | Launch an external program detached. |
| `label` / `jump` | `label:<name>` / `jump:<name>` | Define a target / jump to it (loops). |
| `waitkey` | `waitkey:"<key>"` | **No-op in this engine — only logs; does not block.** Don't rely on it. |

**Caveats:** `waitkey` does not block. And the tokenizer only accepts `ch`/`val`/`arg` as secondary keywords, so a `time:` token on `setfixture` is flagged as a syntax error even though the handler understands it — to fade a channel with an explicit time, use the JS `ScriptRunner.setFixture(fx, ch, val, time)` instead.

There is also a **JavaScript ScriptRunner** (newer QML UI): the whole body is JS and commands are methods on a global `Engine` object — `Engine.startFunction(id)`, `Engine.waitTime("2s")`, `Engine.setFixture(fx,ch,val,time)`, `Engine.getChannelValue(uni,ch)`, `Engine.setBlackout(b)`, `Engine.setBPM(n)`, `Engine.random(min,max)`, etc. Use it when you need real loops, channel reads, explicit fade times, or BPM control.

### 5.2 RGB Matrix JavaScript scripting

A matrix Script algorithm is a single `.js` file (IIFE returning an `algo` object) that paints the grid each animation step. Same files run under two engines (legacy QtScript and QJSEngine).

**Required members:**

| Member | Required | Purpose |
|---|---|---|
| `apiVersion` | yes (>0; 1/2/3) | 1 = bare; 2 adds `properties`; 3 adds raw multi-color in/out. |
| `name`, `author` | name yes | Shown in UI; name is what's saved in XML. |
| `rgbMap(width,height,rgb,step)` | yes | Returns a 2-D array `[y][x]` of `0xRRGGBB` ints (0 = off) for the given step. |
| `rgbMapStepCount(width,height)` | yes | Number of animation steps; engine cycles `step` 0…count-1. |
| `acceptColors` | optional (default 2) | How many colors the script consumes. |
| `properties` | if apiVersion ≥ 2 | Array of `key:value|…` descriptors exposed as UI Properties. |
| `rgbMapSetColors(raw)` / `rgbMapGetColors()` | set required if apiVersion ≥ 3 | Receive/return the matrix's color array. |

**Property descriptor** format (pipe-joined): `name:` (id), `type:` (`list`/`range`/`float`/`string`), `display:` (label), `values:` (list options or `min,max`), `write:`/`read:` (JS setter/getter names). Values are passed as strings. Only the script NAME is stored in the workspace; property values are saved as the matrix's `<Property>` entries.

Minimal skeleton:

```js
(function() {
  var algo = new Object;
  algo.apiVersion = 2;
  algo.name = "My Effect";
  algo.rgbMap = function(w, h, rgb, step) {
    var m = new Array(h);
    for (var y = 0; y < h; y++) { m[y] = new Array(w);
      for (var x = 0; x < w; x++) m[y][x] = (x === step) ? rgb : 0; }
    return m;
  };
  algo.rgbMapStepCount = function(w, h) { return w; };
  return algo;
})();
```

For ready-to-use Script-function programs and RGB-matrix JS patterns tuned to this venue, see [[effect-recipes-cookbook]]. For how these map to physical fixtures and roles, see [[fixture-types-and-roles]] and [[club-rig-mayans]]; for the platform/engine background see [[cross-platform-concepts]].