---
name: qlc-fixture-definition-format
description: "Exact QLC+ .qxf fixture-definition format: channel presets, groups, capabilities, modes, heads, physical — plus how to author a new fixture"
metadata:
  node_type: memory
  type: reference
  originSessionId: 0a2fa9d9-bc6b-46d6-bdbb-e98d6dad045a
---

# QLC+ .qxf fixture-definition format

A **`.qxf`** file is a UTF-8 XML document declaring one lighting fixture: its channel pool, one or more DMX-footprint **modes**, optional multi-**head** grouping, and **physical** metadata. QLC+ parses/writes it via `engine/src/qlcfixturedef.cpp` (root) with each sub-object owning its own load/save (`qlcchannel.cpp`, `qlccapability.cpp`, `qlcfixturemode.cpp`, `qlcfixturehead.cpp`, `qlcphysical.cpp`). This is the join point between the rig and everything an auto-generator builds on top: scenes address fixtures by mode channel index, and effects need the engine to *understand* which channels are RGB/CMY/Pan/Tilt — which it derives entirely from the metadata defined here. See [[club-rig-mayans]] for the venue's actual patched fixtures, [[fixture-types-and-roles]] for how each type is used in shows, and [[qlc-save-file-format]] for how a workspace references these defs.

## 1. Top-level structure (the full XML tree)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE FixtureDefinition>
<FixtureDefinition xmlns="http://www.qlcplus.org/FixtureDefinition">
 <Creator>
  <Name>Q Light Controller Plus</Name>      <!-- ignored on load -->
  <Version>4.13.1</Version>                  <!-- ignored on load -->
  <Author>yourname</Author>                 <!-- the only Creator field read back -->
 </Creator>
 <Manufacturer>...</Manufacturer>           <!-- required -->
 <Model>...</Model>                         <!-- required; name() == "Manufacturer Model" -->
 <Type>Moving Head</Type>                   <!-- see type list below -->

 <!-- 1..N Channel definitions (the unordered channel POOL) -->
 <Channel Name="..." [Default="N"] [Preset="..."]> ... </Channel>

 <!-- 1..N Modes. At least ONE mode is REQUIRED or load fails. -->
 <Mode Name="..."> ... </Mode>

 <!-- 0..1 global Physical block -->
 <Physical> ... </Physical>
</FixtureDefinition>
```

Load/save rules (`qlcfixturedef.cpp`):

| Rule | Detail |
|---|---|
| Root element | Must be `FixtureDefinition`. |
| Namespace | `xmlns` written on save is `http://www.qlcplus.org/` + doctype = `http://www.qlcplus.org/FixtureDefinition`. |
| Creator | `Name`/`Version` skipped on load; only `<Author>` is read back. |
| Unknown tags | Warned and skipped (lenient parser) at every level. |
| **Zero modes** | **Hard failure** — `loadXML` returns false if no modes. |
| Duplicates | `clear()` runs before parse; nothing is appended to stale state. |
| Save order | Fixed: Manufacturer, Model, Type, all Channels, all Modes, Physical. |
| Filename | Convention `Manufacturer-Model.qxf`; user defs live in the user fixtures dir. |

### `<Type>` strings
Must match exactly (`stringToType`/`typeToString`); any unrecognised value becomes `Other`. Unset default is `Dimmer`.

`Color Changer` · `Dimmer` · `Effect` · `Fan` · `Flower` · `Hazer` · `Laser` · `Moving Head` · `Scanner` · `Smoke` · `Strobe` · `LED Bar (Beams)` · `LED Bar (Pixels)` · `Other`

## 2. `<Channel>` element

The channel pool is an **unordered set** of named functions. Modes later reference them by name and order them into DMX slots. A channel is defined one of two ways:

- **Preset channel** — `Preset="..."` attribute, **no children**. The preset implies Group, ControlByte (MSB/LSB), Colour, and one auto-generated capability. Keeps files tiny and gives the engine semantic knowledge (RGB mixing, P/T, strobe). *Any hand-written children on a preset channel are ignored on save — the writer short-circuits after the preset attr.*
- **Custom channel** — no `Preset`; must carry `<Group Byte="N">`, optional `<Colour>`, and explicit `<Capability>` ranges.

```xml
<!-- preset form -->
<Channel Name="Pan" Preset="PositionPan"/>

<!-- custom form -->
<Channel Name="Dimmer" Default="0">
 <Group Byte="0">Intensity</Group>     <!-- required for custom; Byte = control byte -->
 <Colour>Red</Colour>                  <!-- optional; only if not NoColour -->
 <Capability Min="0" Max="112">Dimmer (0 - 100%)</Capability>
 <Capability Min="113" Max="255">Dimmer (100%)</Capability>
</Channel>
```

| Attribute | Required | Meaning |
|---|---|---|
| `Name` | yes | Unique within pool; empty name fails the channel. **Join key** used by `<Mode>`/`<Head>` lookups — a typo silently yields a NULL slot. |
| `Default` | no | Power-on DMX value 0-255; only written when non-zero. |
| `Preset` | no | If present, channel is fully preset-defined (see §3). |

### `<Group Byte="N">`
`Byte` is the `ControlByte` enum: **`0` = MSB** (coarse/primary), **`1` = LSB** (fine). Pairs 8/16-bit channels and drives head channel caching.

The 12 valid Group strings (`stringToGroup`/`groupToString`):

| Group | Typical use |
|---|---|
| `Intensity` | dimmer, RGBW/CMY colour intensities |
| `Colour` | colour wheel / macro |
| `Gobo` | gobo wheel / index |
| `Speed` | P/T movement speed |
| `Pan` | horizontal position |
| `Tilt` | vertical position |
| `Shutter` | strobe / iris |
| `Prism` | prism rotation |
| `Beam` | focus / zoom |
| `Effect` | misc effects |
| `Maintenance` | reset / lamp control |
| `Nothing` | no function / unused |

(`NoGroup = INT_MAX` is internal-only, never on disk. The UI alphabetises: Beam, Colour, Effect, Gobo, Intensity, Maintenance, Nothing, Pan, Prism, Shutter, Speed, Tilt.)

### `<Colour>` (primary colour of an intensity channel)
Written only when colour != `NoColour`. The engine uses this to know which DMX channel produces which physical colour — **essential for the colour picker and RGB-matrix effects.**

| String | Hex | | String | Hex |
|---|---|---|---|---|
| `Red` | 0xFF0000 | | `White` | 0xFFFFFF |
| `Green` | 0x00FF00 | | `Amber` | 0xFF7E00 |
| `Blue` | 0x0000FF | | `UV` | 0x9400D3 |
| `Cyan` | 0x00FFFF | | `Lime` | 0xADFF2F |
| `Magenta` | 0xFF00FF | | `Indigo` | 0x4B0082 |
| `Yellow` | 0xFFFF00 | | | |

`Generic` => `NoColour` (fallback, normally not written).

## 3. Channel `Preset` values — COMPLETE list

The `Preset="..."` string is the **literal enum identifier** (serialised via Qt meta-enum). Applying a preset auto-sets Group, ControlByte, default Colour, default channel name, and one capability. **A preset does not overwrite a `Name=` you supply** — it only fills the name if empty. So `<Channel Name="Strobe" Preset="ShutterStrobeSlowFast"/>` keeps "Strobe" as the name. `Custom` (no attr) and `LastPreset` (sentinel) are never written.

### Intensity group (ControlByte MSB; `*Fine` => LSB)
| Preset | Colour | Default name |
|---|---|---|
| `IntensityMasterDimmer` / `…Fine` | – | "Master dimmer" |
| `IntensityDimmer` / `…Fine` | – | "Dimmer" |
| `IntensityRed` / `…Fine` | Red | "Red" |
| `IntensityGreen` / `…Fine` | Green | "Green" |
| `IntensityBlue` / `…Fine` | Blue | "Blue" |
| `IntensityCyan` / `…Fine` | Cyan | "Cyan" |
| `IntensityMagenta` / `…Fine` | Magenta | "Magenta" |
| `IntensityYellow` / `…Fine` | Yellow | "Yellow" |
| `IntensityAmber` / `…Fine` | Amber | "Amber" |
| `IntensityWhite` / `…Fine` | White | "White" |
| `IntensityUV` / `…Fine` | UV | "UV" |
| `IntensityIndigo` / `…Fine` | Indigo | "Indigo" |
| `IntensityLime` / `…Fine` | Lime | "Lime" |
| `IntensityHue` / `…Fine` | – | "Hue" |
| `IntensitySaturation` / `…Fine` | – | "Saturation" |
| `IntensityLightness` / `…Fine` | – | "Lightness" |
| `IntensityValue` / `…Fine` | – | "Value" |

Auto-capability name is "<Function> (0 - 100%)" (intensity) / "<Colour> intensity (0 - 100%)" for colours.

### Position group (Group = Pan or Tilt)
| Preset | Group | Default name |
|---|---|---|
| `PositionPan` / `PositionPanFine` | Pan | "Pan" / "Pan fine" (LSB) |
| `PositionTilt` / `PositionTiltFine` | Tilt | "Tilt" / "Tilt fine" (LSB) |
| `PositionXAxis` | Pan | "X Axis" |
| `PositionYAxis` | Tilt | "Y Axis" |

### Speed group
| Preset | Default name | Auto-capability |
|---|---|---|
| `SpeedPanSlowFast` / `SpeedPanFastSlow` | "Pan speed" | "Pan (Slow to fast)" / "(Fast to slow)" |
| `SpeedTiltSlowFast` / `SpeedTiltFastSlow` | "Tilt speed" | "Tilt (Slow to fast)" / "(Fast to slow)" |
| `SpeedPanTiltSlowFast` / `SpeedPanTiltFastSlow` | "Pan/Tilt speed" | "Pan and tilt (Slow to fast)" / "(Fast to slow)" |

### Colour group
| Preset | Default name | Auto-capability |
|---|---|---|
| `ColorMacro` | "Color macro" | "Color macro presets" |
| `ColorWheel` / `ColorWheelFine` | "Color wheel" (+" fine" LSB) | "Color wheel presets" |
| `ColorRGBMixer` | "RGB mixer" | name |
| `ColorCTOMixer` | "CTO mixer" | name |
| `ColorCTCMixer` | "CTC mixer" | name |
| `ColorCTBMixer` | "CTB mixer" | name |

### Gobo group
| Preset | Default name | Auto-capability |
|---|---|---|
| `GoboWheel` / `GoboWheelFine` | "Gobo wheel" (+" fine" LSB) | "Gobo wheel presets" |
| `GoboIndex` / `GoboIndexFine` | "Gobo index" (+" fine" LSB) | "Gobo index presets" |

### Shutter group
| Preset | Default name | Auto-capability |
|---|---|---|
| `ShutterStrobeSlowFast` / `…FastSlow` | "Strobe" | "Strobe (Slow to fast)" / "(Fast to slow)" |
| `ShutterIrisMinToMax` / `…MaxToMin` | "Iris" | "Iris (Minimum to maximum)" / "(Maximum to minimum)" |
| `ShutterIrisFine` | "Iris fine" (LSB) | name |

### Beam group
| Preset | Default name | Auto-capability |
|---|---|---|
| `BeamFocusNearFar` / `…FarNear` | "Focus" | "Beam (Near to far)" / "(Far to near)" |
| `BeamFocusFine` | "Focus fine" (LSB) | name |
| `BeamZoomSmallBig` / `…BigSmall` | "Zoom" | "Zoom (Small to big)" / "(Big to small)" |
| `BeamZoomFine` | "Zoom fine" (LSB) | name |

### Prism group
| Preset | Default name | Auto-capability |
|---|---|---|
| `PrismRotationSlowFast` / `…FastSlow` | "Prism rotation" | "Prism rotation (Slow to fast)" / "(Fast to slow)" |

### Nothing group
| Preset | Group | Default name |
|---|---|---|
| `NoFunction` | Nothing | "No function" |

## 4. `<Capability>` element

A capability maps a contiguous DMX value range to a human-readable function on a custom channel.

```xml
<Capability Min="0" Max="112">Dimmer (0 - 100%)</Capability>
```

| Attribute | Required | Meaning |
|---|---|---|
| `Min`, `Max` | yes | uchar 0-255, clamped. Load fails if missing or `Min > Max`. Single value => `Min == Max`. |
| (element text) | no | Display name (`doc.text().simplified()`). Empty allowed but warns. |
| `Preset` | no | A **capability** preset (distinct from channel preset, §4.1). |
| `Res1`, `Res2` | no | Resource attrs; interpretation depends on the capability preset's `PresetType` (§4.2). Legacy aliases `Res`/`Color`/`Color2` still read. |

**Capabilities must not overlap** (`overlaps()` check); overlapping ones are dropped on load. Cover 0-255 with no gaps for clean UI behaviour.

### 4.1 Capability `Preset` enum (full list)
`Custom` (default, no attr) · `SlowToFast` · `FastToSlow` · `NearToFar` · `FarToNear` · `BigToSmall` · `SmallToBig` · `ShutterOpen` · `ShutterClose` · `StrobeSlowToFast` · `StrobeFastToSlow` · `StrobeRandom` · `StrobeRandomSlowToFast` · `StrobeRandomFastToSlow` · `StrobeFrequency` · `StrobeFreqRange` · `PulseSlowToFast` · `PulseFastToSlow` · `PulseFrequency` · `PulseFreqRange` · `RampUpSlowToFast` · `RampUpFastToSlow` · `RampDownSlowToFast` · `RampDownFastToSlow` · `RampUpFrequency` · `RampUpFreqRange` · `RampDownFrequency` · `RampDownFreqRange` · `RotationStop` · `RotationIndexed` · `RotationClockwise` · `RotationClockwiseSlowToFast` · `RotationClockwiseFastToSlow` · `RotationCounterClockwise` · `RotationCounterClockwiseSlowToFast` · `RotationCounterClockwiseFastToSlow` · `ColorMacro` · `ColorDoubleMacro` · `ColorWheelIndex` · `GoboMacro` · `GoboShakeMacro` · `GenericPicture` · `PrismEffectOn` · `PrismEffectOff` · `LampOn` · `LampOff` · `ResetAll` · `ResetPanTilt` · `ResetPan` · `ResetTilt` · `ResetMotors` · `ResetGobo` · `ResetColor` · `ResetCMY` · `ResetCTO` · `ResetEffects` · `ResetPrism` · `ResetBlades` · `ResetIris` · `ResetFrost` · `ResetZoom` · `SilentModeOn` · `SilentModeOff` · `SilentModeAutomatic` · `Alias` · `LastPreset` (sentinel)

### 4.2 `PresetType` => Res1/Res2 interpretation
| PresetType | Presets | Res1 / Res2 | Units |
|---|---|---|---|
| SingleValue | `StrobeFrequency`, `PulseFrequency`, `RampUpFrequency`, `RampDownFrequency`, `PrismEffectOn` | Res1 = float | `Hz` (freq) / `Faces` (prism) |
| DoubleValue | `StrobeFreqRange`, `PulseFreqRange`, `RampUpFreqRange`, `RampDownFreqRange` | Res1 = min Hz, Res2 = max Hz | Hz |
| SingleColor | `ColorMacro` | Res1 = colour name / `#rrggbb` | – |
| DoubleColor | `ColorDoubleMacro` | Res1, Res2 = two colours | – |
| Picture | `GoboMacro`, `GoboShakeMacro`, `GenericPicture` | Res1 = gobo image path (relative to GOBODIR if inside it) | – |
| None | all others | ignored | – |

### 4.3 Capability `<Alias>` sub-element
Swaps one channel for another in a named mode (e.g. expose a "Dimmer Fine" only in the 16-bit mode):
```xml
<Capability Min="0" Max="9" Preset="Alias">Some name
 <Alias Mode="8-bit" Channel="Dimmer" With="Dimmer Fine"/>
</Capability>
```
`Mode` = mode where alias applies · `Channel` = source channel to replace · `With` = replacement channel. Multiple `<Alias>` children allowed.

## 5. `<Mode>` element

A mode is an **ordered DMX footprint**: which pool channels occupy which slots. At least one mode is mandatory.

```xml
<Mode Name="16 Channels">
 <Physical> ... </Physical>          <!-- optional; overrides global -->
 <Channel Number="0">Pan</Channel>
 <Channel Number="1" ActsOn="0">Pan Fine</Channel>
 ...
 <Head>                               <!-- 0..N heads -->
  <Channel>0</Channel>
  <Channel>1</Channel>
 </Head>
</Mode>
```

| Element / attr | Meaning |
|---|---|
| `Name` | Required; empty fails the mode. |
| `<Channel Number="i">Name</Channel>` | `Number` = 0-based DMX slot (re-numbered sequentially on save). Text must **exactly match** a pool channel name; a non-match silently inserts NULL. |
| `ActsOn="j"` | This channel modifies/depends on the channel at index `j` (stored in `m_actsOnMap`). Written only when set. Used for master-dimmer-over-a-slot, or fine coupled to coarse. |
| `<Physical>` | Per-mode override; absent => uses global (`useGlobalPhysical`). |

**Auto-derivation after load (`cacheHeads()`):**
- **Master intensity channel** = first Intensity / MSB / NoColour channel not in any head.
- **Secondary (fine) detection** = a channel is treated as fine when the *previous* channel shares its Group, prev is MSB and this is LSB. **=> Place each fine channel immediately after its coarse channel and give it `Byte="1"` (or use the `*Fine` preset).** Get this wrong and 16-bit fades break.

## 6. `<Head>` element (within a Mode)

```xml
<Head>
 <Channel>0</Channel>   <!-- channel INDEX within the mode (the Number), not a name -->
 <Channel>1</Channel>
</Head>
```
A head groups channel **indices** belonging to one physical light head (multi-head bars, or to tag a mover's P/T). `cacheChannels()` derives per-head RGB/CMY/colour-wheel/shutter/pan/tilt maps from each channel's Group/Colour/ControlByte. **If a head lacks Pan/Tilt it inherits them from the mode.** For single-head movers, wrapping the P/T (+ fines + speed) in one `<Head>` is the common convention.

## 7. `<Physical>` element

Once globally and/or once per mode. All-zero numeric fields count as empty.

```xml
<Physical>
 <Bulb Type="LED" Lumens="0" ColourTemperature="0"/>
 <Dimensions Weight="0" Width="0" Height="0" Depth="0"/>
 <Lens Name="Other" DegreesMin="0" DegreesMax="0"/>
 <Focus Type="Head" PanMax="540" TiltMax="270"/>
 <Layout Width="3" Height="2"/>            <!-- only written when not 1x1 -->
 <Technical PowerConsumption="0" DmxConnector="3-pin"/>
</Physical>
```

| Element | Attributes |
|---|---|
| `<Bulb>` | `Type` (string e.g. "LED", "230W"), `Lumens` (int), `ColourTemperature` (int K) |
| `<Dimensions>` | `Weight` (double kg, **C locale** = `.` decimal), `Width`/`Height`/`Depth` (int mm) |
| `<Lens>` | `Name` (default "Other"), `DegreesMin`/`DegreesMax` (double beam angle, C locale) |
| `<Focus>` | `Type` ("Fixed" default; "Head" for movers; "Mirror" for scanners), `PanMax`/`TiltMax` (int °) |
| `<Layout>` | `Width` (cols), `Height` (rows) — pixel-matrix; omitted unless non-1x1 |
| `<Technical>` | `PowerConsumption` (int W; if 0 => bulb watts + 100), `DmxConnector` ("5-pin" default; "3-pin", etc.) |

## 8. Related: channel modifiers (NOT part of .qxf)
`<!DOCTYPE ChannelModifier>` is a **separate** document (input-DMX-value => output-value remap table): root `ChannelModifier` with `<Name>` and `<Handler>` (`Original`/`Modified` byte pairs), types `SystemTemplate=0` / `UserTemplate=1`. Referenced from project files, never embedded in a `.qxf`. Listed only so you don't confuse the two.

## 9. Recipe: author a NEW fixture from scratch

1. **Header** — xml decl, `<!DOCTYPE FixtureDefinition>`, root with the `xmlns`, then `<Creator>` (only `<Author>` is read back).
2. **Identity** — `<Manufacturer>`, `<Model>`, `<Type>` (exact string from §1).
3. **Build the channel pool** — one `<Channel>` per distinct function:
   - Prefer a `Preset` whenever one fits — it gives the engine semantic knowledge (RGB mixing, P/T, strobe) for free and keeps the file tiny.
   - Use the colour intensity presets (`IntensityRed/Green/Blue/White/...`) for RGBW so the colour picker and RGB-matrix effects work — see [[effect-recipes-cookbook]].
   - For functions with no matching preset, write a custom channel: `<Group Byte="0|1">`, optional `<Colour>`, explicit non-overlapping `<Capability>` covering 0-255.
   - Put each fine channel right after its coarse one with `Byte="1"` (or the `*Fine` preset) so 16-bit pairing is detected.
4. **Define ≥1 `<Mode>`** — list `<Channel Number="i">Name</Channel>` in DMX order; text must exactly match a pool name. Add `ActsOn="j"` only for genuine dependencies. For movers, wrap Pan/Tilt (+ fines + speed) in a `<Head>` by index.
5. **Physical** — `<Bulb>`, `<Dimensions>`, `<Lens>`, `<Focus Type="Head" PanMax=… TiltMax=…>` for movers, `<Technical>`.
6. **Validate** — (a) root + DTD correct; (b) ≥1 mode; (c) every mode channel name resolves to a pool channel; (d) capabilities don't overlap and cover the range; (e) `Min<=Max` everywhere. Save as `Manufacturer-Model.qxf`.

### Worked example: generic RGBW moving-head spot (14-ch, 16-bit P/T)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE FixtureDefinition>
<FixtureDefinition xmlns="http://www.qlcplus.org/FixtureDefinition">
 <Creator>
  <Name>Q Light Controller Plus</Name>
  <Version>4.13.1</Version>
  <Author>louma</Author>
 </Creator>
 <Manufacturer>Generic</Manufacturer>
 <Model>RGBW Moving Spot 14ch</Model>
 <Type>Moving Head</Type>
 <Channel Name="Pan" Preset="PositionPan"/>
 <Channel Name="Pan Fine" Preset="PositionPanFine"/>
 <Channel Name="Tilt" Preset="PositionTilt"/>
 <Channel Name="Tilt Fine" Preset="PositionTiltFine"/>
 <Channel Name="Pan/Tilt Speed" Preset="SpeedPanTiltSlowFast"/>
 <Channel Name="Dimmer" Preset="IntensityMasterDimmer"/>
 <Channel Name="Shutter / Strobe">
  <Group Byte="0">Shutter</Group>
  <Capability Min="0" Max="7">Closed</Capability>
  <Capability Min="8" Max="15">Open</Capability>
  <Capability Min="16" Max="131" Preset="StrobeSlowToFast">Strobe (slow to fast)</Capability>
  <Capability Min="132" Max="255">Open</Capability>
 </Channel>
 <Channel Name="Red" Preset="IntensityRed"/>
 <Channel Name="Green" Preset="IntensityGreen"/>
 <Channel Name="Blue" Preset="IntensityBlue"/>
 <Channel Name="White" Preset="IntensityWhite"/>
 <Channel Name="Color Wheel" Preset="ColorWheel"/>
 <Channel Name="Gobo Wheel" Preset="GoboWheel"/>
 <Channel Name="Reset">
  <Group Byte="0">Maintenance</Group>
  <Capability Min="0" Max="249">No function</Capability>
  <Capability Min="250" Max="255" Preset="ResetAll">Reset</Capability>
 </Channel>
 <Mode Name="14 Channel">
  <Channel Number="0">Pan</Channel>
  <Channel Number="1">Pan Fine</Channel>
  <Channel Number="2">Tilt</Channel>
  <Channel Number="3">Tilt Fine</Channel>
  <Channel Number="4">Pan/Tilt Speed</Channel>
  <Channel Number="5">Dimmer</Channel>
  <Channel Number="6">Shutter / Strobe</Channel>
  <Channel Number="7">Red</Channel>
  <Channel Number="8">Green</Channel>
  <Channel Number="9">Blue</Channel>
  <Channel Number="10">White</Channel>
  <Channel Number="11">Color Wheel</Channel>
  <Channel Number="12">Gobo Wheel</Channel>
  <Channel Number="13">Reset</Channel>
  <Head>
   <Channel>0</Channel>
   <Channel>1</Channel>
   <Channel>2</Channel>
   <Channel>3</Channel>
  </Head>
 </Mode>
 <Physical>
  <Bulb Type="LED" Lumens="0" ColourTemperature="0"/>
  <Dimensions Weight="0" Width="0" Height="0" Depth="0"/>
  <Lens Name="Other" DegreesMin="0" DegreesMax="0"/>
  <Focus Type="Head" PanMax="540" TiltMax="270"/>
  <Technical PowerConsumption="0" DmxConnector="3-pin"/>
 </Physical>
</FixtureDefinition>
```
This exercises: preset channels (16-bit P/T, P/T speed, master dimmer, RGBW intensities, color/gobo wheels), a custom Shutter channel with a per-capability `StrobeSlowToFast` preset, a Maintenance channel with a `ResetAll` capability, a single `<Head>` grouping the P/T indices, and mover Physical.

## 10. The venue's real fixtures (study these)

Files in `…\qlcplus\Fixtures\`: `Mayans-BEAM230.qxf` (+ `… V2`, `… V3`), `WASH-Mayans-Mayans.qxf`, `Mayans-Revolver-Wash.qxf`. Full rig context in [[club-rig-mayans]].

| Fixture | Footprint / Type | What it demonstrates |
|---|---|---|
| **Mayans BEAM230** | 16-ch, `Moving Head` | Textbook preset use: `PositionPan(+Fine)`, `PositionTilt(+Fine)`, `SpeedPanTiltSlowFast`, `ShutterStrobeSlowFast`, `ColorWheel`, `BeamFocusNearFar`. Custom Dimmer (2 caps: `0-112` "Dimmer (0 - 100%)", `113-255` "Dimmer (100%)"). "Passthrough" custom channels (Frost/Color Effect/GOBO/PRISM) = single `0-255` empty-name capability. **Quirks to fix on a rebuild:** `PRISM Rotation` + `LAMP` filed under `Nothing`, `RESET` under `Maintenance`. Mode uses identity `ActsOn=i` (no-op). `<Head>` groups indices 0-4. Physical `Focus Type="Head" PanMax="540" TiltMax="270"`, `3-pin`. |
| **Mayans WASH** | 16-ch RGBW, `Moving Head` | Preset-driven colour mixing: `IntensityMasterDimmer` + `IntensityRed/Green/Blue/White`, `PositionPan/Tilt(+Fine)`, `SpeedPanTiltSlowFast`, `BeamFocusNearFar` (named "Focus/Zoom"), `ShutterStrobeSlowFast`. Also shows **custom channels with NO capability and NO colour** (`Test Mode Dimming`, `Speed of 14?`, `Reset`) — just `<Group Byte="0">Intensity</Group>`; legal placeholders that behave as plain 0-255 intensity. `Setting Gradient…` filed under `Effect`. `5-pin`. |
| **Mayans Revolver Wash** | 12-ch, `Moving Head` | The **only non-identity `ActsOn`**: slot 9 "Master dimmer" `ActsOn="10"` and slot 10 "Open Light" `ActsOn="9"` cross-reference each other. Real-world **mislabels** showing presets drive semantics regardless of name: `Pan fine` carries `Preset="SpeedPanTiltSlowFast"` (so it's a Speed channel), and both `Master dimmer` + `Open Light` use `Preset="ShutterIrisMinToMax"`. Focus `Fixed` (PanMax/TiltMax 0). |

### Key gotchas (from the source)
- **≥1 `<Mode>` is mandatory** or the whole def fails to load.
- Channel `Name` is the unique join key for `<Mode>`/`<Head>`; a typo silently yields a NULL slot.
- A `Preset` attribute makes the channel self-contained — hand-written `<Group>`/`<Capability>`/`<Colour>` on it are ignored on save (writer short-circuits).
- Fine (LSB) channels must immediately follow their MSB channel and share its Group to auto-pair; `*Fine` presets set `Byte="1"` for you.
- Capabilities are clamped 0-255 and must not overlap; overlapping / `Min>Max` caps are dropped or fail the load.
- `Weight` and lens `Degrees*` use the **C locale** (always `.` decimal separator) regardless of system locale.
- Presets win over names: a wrongly-preseted channel (see Revolver Wash) is grouped/treated by its **preset**, not its `Name` — verify presets when a fixture misbehaves.