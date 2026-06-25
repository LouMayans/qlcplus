---
name: qlc-save-file-format
description: "Exact QLC+ .qxw project save-file XML schema (Workspace, Engine, Fixtures, all Function types, VC) for programmatic generation/editing"
metadata:
  node_type: memory
  type: reference
  originSessionId: 0a2fa9d9-bc6b-46d6-bdbb-e98d6dad045a
---

# QLC+ .qxw save-file format

The QLC+ project workspace file. Extension `.qxw` ("QLC+ XML Workspace"). UTF-8, written via `QXmlStreamWriter` (one element per line, leading-space indentation). All numbers are decimal strings. Generic booleans are the literal strings `True`/`False`; several specific fields instead use `0`/`1` (called out per-field below).

This document is for generating/editing projects programmatically. See [[qlc-fixture-definition-format]] for the separate `.qxf` fixture-definition files, [[qlc-functionality-reference]] for runtime function behavior and the Virtual Console, and [[club-rig-mayans]] for this venue's actual fixture/universe layout.

> Loader robustness: every `loadXML` reads attributes and child elements by name in any order and skips unknown tags with a warning. So generation order *inside* a function is flexible — only the `<Workspace>`/`<Engine>` nesting and the element/attribute names are mandatory. The write orders below are what a freshly-saved file looks like; follow them only if you want byte-exact round-tripping.

---

## 1. Document root & top-level section order

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Workspace>
<Workspace xmlns="http://www.qlcplus.org/Workspace" CurrentWindow="FunctionManager">
 <Creator>
  <Name>Q Light Controller Plus</Name>
  <Version>4.x.x</Version>
  <Author>OS user name</Author>
 </Creator>
 <Engine>
  <InputOutputMap> ... </InputOutputMap>
  <Fixture> ... </Fixture>            <!-- repeated, sorted by fixture ID asc -->
  <FixtureGroup> ... </FixtureGroup>  <!-- repeated -->
  <ChannelsGroup> ... </ChannelsGroup><!-- repeated -->
  <Palette> ... </Palette>            <!-- repeated -->
  <Function> ... </Function>          <!-- repeated, hash order (NOT ID-sorted) -->
  <Monitor> ... </Monitor>            <!-- only if monitor props exist -->
 </Engine>
 <VirtualConsole> ... </VirtualConsole>
 <SimpleDesk> ... </SimpleDesk>
</Workspace>
```

| Level | Element | Attributes | Notes |
|---|---|---|---|
| root | `<Workspace>` | `xmlns` (fixed namespace), `CurrentWindow` | `CurrentWindow` = class name of active tab: `FunctionManager`, `VirtualConsole`, `SimpleDesk`, `FixtureManager`, `InputOutputManager`, `ShowManager`. Omitted if none. |
| 1 | `<Creator>` | — | `<Name>` = `Q Light Controller Plus`, `<Version>` = APPVERSION, `<Author>` = OS user. |
| 1 | `<Engine>` | optional `Autostart="<fid>"` | Only present if a startup function is set. Children in the order shown above. |
| 1 | `<VirtualConsole>` | — | Busking surface; see [[qlc-functionality-reference]]. |
| 1 | `<SimpleDesk>` | — | Manual fader desk state. |

`<DOCTYPE Workspace>` precedes the root. The namespace is always `http://www.qlcplus.org/Workspace`.

---

## 2. `<Fixture>` (engine-level, one per patched fixture)

Children, in write order. Generic/dimmer fixtures use the literal `"Generic"` for Manufacturer/Model/Mode.

| Child | Notes |
|---|---|
| `<Manufacturer>` | e.g. `Stairville`, or `Generic` |
| `<Model>` | e.g. `LED PAR56`, or `Generic`. `RGB Panel` triggers the `<Width>`/`<Height>` children. |
| `<Mode>` | mode name as in the `.qxf`, or `Generic` |
| `<Width>` / `<Height>` | **only** when `Model == "RGB Panel"` (pixel-matrix dimensions) |
| `<ID>` | quint32, unique among fixtures; referenced everywhere by this ID |
| `<Name>` | display name |
| `<Universe>` | 0-based universe index |
| `<CrossUniverse>` | text `True`, only if the fixture spans universes |
| `<Address>` | **0-based** DMX start address within the universe (UI shows 1-based; subtract 1) |
| `<Channels>` | channel count |
| `<ExcludeFade>` | CSV of channel indices, only if non-empty |
| `<ForcedHTP>` | CSV of channel indices, only if non-empty |
| `<ForcedLTP>` | CSV of channel indices, only if non-empty |
| `<ChannelModifier Channel="<idx>" Name="<modName>">` | repeated; per-channel value remap |

```xml
  <Fixture>
   <Manufacturer>Stairville</Manufacturer>
   <Model>LED PAR56</Model>
   <Mode>Mode 1</Mode>
   <ID>0</ID>
   <Name>Stairville Led Par Group 1</Name>
   <Universe>0</Universe>
   <Address>0</Address>
   <Channels>5</Channels>
  </Fixture>
```

See [[qlc-fixture-definition-format]] for what Manufacturer/Model/Mode/channel order must match, and [[club-rig-mayans]] for the venue patch.

---

## 3. `<FixtureGroup>` and `<ChannelsGroup>`

**FixtureGroup** (a named 2D grid of fixture heads, used by RGB Matrix and grouped selection):

```xml
  <FixtureGroup ID="0">
   <Name>Front Truss</Name>
   <Size X="4" Y="1"/>
   <Head X="0" Y="0" Fixture="0">0</Head>   <!-- text body = head index -->
   <Head X="1" Y="0" Fixture="1">0</Head>
  </FixtureGroup>
```

- `<Size X="" Y="">` = grid dimensions.
- `<Head X="" Y="" Fixture="">headIndex</Head>` repeated — grid cell -> (fixture ID, head index). Text content is the head index.

**ChannelsGroup** (a single master over an arbitrary channel set — a busking submaster). Single element, no children; channel list is the text body:

```xml
  <ChannelsGroup ID="0" Name="All Dimmers" Value="0">0,0,1,0,2,0</ChannelsGroup>
```

- Attrs: `ID`, `Name`, `Value` (master level), optional `InputUniverse`/`InputChannel` (external input source).
- Text body = CSV of `fixtureID,channel` pairs.

---

## 4. Common `<Function>` attributes & shared child elements

Every function is `<Function ...>`. Common attributes (write order):

| Attribute | Form | Written when |
|---|---|---|
| `ID` | uint, unique, ≠ 4294967295 | always |
| `Type` | enum string (below) | always |
| `Name` | string | always |
| `Hidden` | `True` | only if not visible (presence ⇒ invisible; used by Sequence's container scene) |
| `Path` | string with `/` separators | only if a folder path is set (the `Type/` prefix is stripped) |
| `BlendMode` | blend-mode string | only if ≠ `Normal` |
| `Priority` | int | **only if ≠ 0** — the per-function priority field |

`Type` strings: `Scene`, `Chaser`, `Sequence`, `EFX`, `Collection`, `Script`, `RGBMatrix`, `Show`, `Audio`, `Video` (`Undefined` is the fallback). The loader instantiates the subclass by `Type`, applies the common fields (`Priority` defaults to 0 if absent), then calls the subclass `loadXML`.

### Shared child elements (used by multiple types)

**`<Speed FadeIn="" FadeOut="" Duration=""/>`** — self-closing, attribute order FadeIn, FadeOut, Duration. **Values in milliseconds.** No `Hold` here (Hold lives only on chaser `<Step>`). Absent attrs ⇒ 0.

**`<Direction>Forward|Backward</Direction>`** — enum `Forward=0, Backward=1`.

**`<RunOrder>Loop|SingleShot|PingPong|Random</RunOrder>`** — enum `Loop=0, SingleShot=1, PingPong=2, Random=3`.

**`<Tempo>Time|Beats</Tempo>`** — **only written when tempo type == `Beats`**. In Beats mode all `<Speed>`/`Hold` values are **beat counts where 1000 = 1 beat**, not ms. Enum `Original=-1, Time=0, Beats=1`.

Not serialized: override tempo/speeds, UI state, runtime attributes, elapsed/running state.

---

## 5. Scene

Holds a static look: a value per fixture channel. Write order: common attrs -> `<Tempo>`(if Beats) -> `<Speed>` -> `<ChannelGroupsVal>`(if any) -> `<FixtureVal>` (one per fixture) -> `<FixtureGroup>` refs -> `<Palette>` refs.

- **`<FixtureVal ID="<fxID>">`** — text = CSV `channel,value,channel,value,...`, **0-based** channel indices, values **0–255**. One per fixture, in add order. (If the scene is `Hidden` — i.e. a Sequence container — all values are written as 0.)
- **`<ChannelGroupsVal>`** — text = CSV `groupID,level,...`.
- **`<FixtureGroup ID="<id>"/>`** — self-closing **reference** (group is defined at engine level).
- **`<Palette ID="<id>"/>`** — self-closing reference.

Legacy read-only forms still parsed: `<ChannelGroups>` (CSV of group IDs, level 0), and per-channel `<Value Fixture="" Channel="">v</Value>`. New files always emit `<FixtureVal>`.

```xml
  <Function Type="Scene" Name="Led All Red" ID="0">
   <Speed FadeIn="0" FadeOut="0" Duration="0"/>
   <FixtureVal ID="0">0,0,1,255,2,0,3,0,4,0</FixtureVal>
   <FixtureVal ID="1">0,0,1,255,2,0,3,0,4,0</FixtureVal>
  </Function>
```

(Channel meaning depends on the fixture's `.qxf` channel order — see [[qlc-fixture-definition-format]]. For color/recipe patterns see [[effect-recipes-cookbook]].)

---

## 6. Chaser (+ Step)

An ordered list of steps, each referencing a member function (usually a Scene), with per-step or common timing. Write order: common attrs -> `<Tempo>`(if Beats) -> `<Speed>` -> `<Direction>` -> `<RunOrder>` -> `<SpeedModes>` -> `<Step>` (repeated).

**`<SpeedModes FadeIn="" FadeOut="" Duration=""/>`** — each value is `Common` (one shared value), `PerStep` (each step carries its own), or `Default` (use the member function's own speed).

**`<Step ...>`** attributes (write order): `Number` (0-based) , `FadeIn` (ms), `Hold` (ms), `FadeOut` (ms), `Note` (only if non-empty), `Values` (count — **Sequence mode only**).

- **Chaser step text body** = the member function's **ID** (single integer).
- Duration is recomputed on load from `FadeIn`+`Hold` (or `Hold = Duration − FadeIn` if a `Duration` attr is present).

```xml
  <Function Type="Chaser" Name="My Chaser" ID="20">
   <Speed FadeIn="0" FadeOut="0" Duration="1000"/>
   <Direction>Forward</Direction>
   <RunOrder>Loop</RunOrder>
   <SpeedModes FadeIn="Default" FadeOut="Default" Duration="Common"/>
   <Step Number="0" FadeIn="0" Hold="1000" FadeOut="0">0</Step>
   <Step Number="1" FadeIn="0" Hold="1000" FadeOut="0">6</Step>
  </Function>
```

---

## 7. Sequence

A `Chaser` subclass bound to one Scene, where each step stores explicit DMX values instead of referencing other functions. Extra **attribute on `<Function>`**: `BoundScene="<sceneID>"` (required; load fails without it). Same child order as Chaser, but steps are written in **Sequence mode**.

- **Sequence step** has the `Values="<count>"` attribute and a text body of **packed DMX**: `fixtureID:ch,val,ch,val:fixtureID:ch,val...` (colon-separated fixture chunks, comma-separated channel/value pairs). **Only non-zero values are written.** On load, each step starts from the bound scene's values, then overlays the step's packed values; `step.fid` is forced to the bound scene ID.

```xml
  <Function Type="Sequence" Name="My Seq" ID="21" BoundScene="5">
   <Speed FadeIn="0" FadeOut="0" Duration="1000"/>
   <Direction>Forward</Direction>
   <RunOrder>Loop</RunOrder>
   <SpeedModes FadeIn="PerStep" FadeOut="PerStep" Duration="PerStep"/>
   <Step Number="0" FadeIn="0" Hold="500" FadeOut="0" Values="2">0:1,255:1:1,255</Step>
  </Function>
```

The container Scene referenced by `BoundScene` is typically saved `Hidden` (so its own `<FixtureVal>`s are zeroed).

---

## 8. Collection

Runs all member functions simultaneously. Common attrs only, then repeated `<Step Number="<i>">funcID</Step>`. No speed/direction/run-order. Member order preserved by `Number`.

```xml
  <Function Type="Collection" Name="My Collection" ID="30">
   <Step Number="0">0</Step>
   <Step Number="1">6</Step>
  </Function>
```

---

## 9. EFX (+ EFX Fixture)

A geometric movement/pattern engine (mainly for moving heads, also dimmer/RGB modes). Write order: common attrs -> repeated `<Fixture>` (EFX members) -> `<PropagationMode>` -> `<Tempo>`(if Beats) -> `<Speed>` -> `<Direction>` -> `<RunOrder>` -> `<Algorithm>` -> `<Width>` -> `<Height>` -> `<Rotation>` -> `<StartOffset>` -> `<IsRelative>` -> X `<Axis>` -> Y `<Axis>`.

Top-level pattern parameters:

| Element | Range / values |
|---|---|
| `<Algorithm>` | `Circle`, `Eight`, `Line`, `Line2`, `Diamond`, `Square`, `SquareChoppy`, `SquareTrue`, `Leaf`, `Lissajous` (unknown ⇒ `Circle`) |
| `<Width>` / `<Height>` | 0–127 (pattern size) |
| `<Rotation>` | 0–359 |
| `<StartOffset>` | 0–359 |
| `<IsRelative>` | `0` / `1` |
| `<PropagationMode>` | `Parallel` (default), `Serial`, `Asymmetric` |

**`<Axis Name="X">` / `<Axis Name="Y">`** each contain `<Offset>` (0–255), `<Frequency>` (0–32), `<Phase>` (0–359).

**Per-EFX `<Fixture>`** (distinct from engine-level `<Fixture>`) children: `<ID>` (fixture ID), `<Head>` (head index), `<Mode>` (**numeric enum** `PanTilt=0, Dimmer=1, RGB=2`), `<Direction>` (`Forward`/`Backward`), `<StartOffset>` (0–359). (`<Intensity>` is legacy read-only.)

```xml
  <Function Type="EFX" Name="My EFX" ID="40">
   <Fixture>
    <ID>4</ID>
    <Head>0</Head>
    <Mode>0</Mode>
    <Direction>Forward</Direction>
    <StartOffset>0</StartOffset>
   </Fixture>
   <PropagationMode>Parallel</PropagationMode>
   <Speed FadeIn="0" FadeOut="0" Duration="20000"/>
   <Direction>Forward</Direction>
   <RunOrder>Loop</RunOrder>
   <Algorithm>Circle</Algorithm>
   <Width>127</Width>
   <Height>127</Height>
   <Rotation>0</Rotation>
   <StartOffset>0</StartOffset>
   <IsRelative>0</IsRelative>
   <Axis Name="X">
    <Offset>127</Offset>
    <Frequency>2</Frequency>
    <Phase>90</Phase>
   </Axis>
   <Axis Name="Y">
    <Offset>127</Offset>
    <Frequency>3</Frequency>
    <Phase>0</Phase>
   </Axis>
  </Function>
```

See [[effect-recipes-cookbook]] for usable Algorithm/Width/Height/Frequency/Phase combos, and [[fixture-types-and-roles]] for which fixtures support which EFX `<Mode>`.

---

## 10. RGB Matrix (+ Algorithm)

Drives a `<FixtureGroup>` pixel grid with a generated pattern. Write order: common attrs -> `<Tempo>`(if Beats) -> `<Speed>` -> `<Direction>` -> `<RunOrder>` -> `<Algorithm Type="...">` -> `<DimmerControl>`(legacy, only if true) -> repeated `<Color>` -> `<ControlMode>` -> `<FixtureGroup>` -> repeated `<Property>`.

- **`<Color Index="<i>">packedRGB</Color>`** — text = packed `0xAARRGGBB` as a **decimal** integer. Index 0 = primary, 1 = end/secondary, etc. (e.g. pure red `0xFFFF0000` = `4294901760`). Legacy read-only: `<MonoColor>`→color 0, `<EndColor>`→color 1.
- **`<ControlMode>`** — `RGB` (default), `Amber`, `White`, `UV`, `Dimmer`, `Shutter` (which channel group the matrix drives on each fixture).
- **`<FixtureGroup>id</FixtureGroup>`** — **plain text** element holding the controlled group's ID (NOT an attribute, NOT the full group).
- **`<Property Name="" Value=""/>`** — repeated, self-closing; per-script parameters.

### `<Algorithm Type="...">` variants

| `Type` | Colors | Body |
|---|---|---|
| `Plain` | 1 | empty element |
| `Text` | 2 | `<Content>text</Content>`, `<Font>QFont string</Font>`, `<Animation>Letters\|Horizontal\|Vertical</Animation>`, `<Offset X="" Y=""/>` (note: static = `Letters`) |
| `Image` | 0 | `<Filename>path</Filename>` (stored **relative to workspace dir** if inside it), `<Animation>Static\|Horizontal\|Vertical\|Animation</Animation>`, `<Offset X="" Y=""/>` |
| `Audio` | 2 | empty element (audio-spectrum visualizer) |
| `Script` | varies | element **text = the script's name**; the script body is NOT stored (loaded from the scripts cache by name); parameters live in the RGBMatrix `<Property>` elements |

```xml
  <Function Type="RGBMatrix" Name="My Matrix" ID="50">
   <Speed FadeIn="0" FadeOut="0" Duration="500"/>
   <Direction>Forward</Direction>
   <RunOrder>Loop</RunOrder>
   <Algorithm Type="Script">Stripes</Algorithm>
   <Color Index="0">4294901760</Color>
   <ControlMode>RGB</ControlMode>
   <FixtureGroup>0</FixtureGroup>
   <Property Name="orientation" Value="Vertical"/>
  </Function>
```

Text-algorithm body example:

```xml
   <Algorithm Type="Text">
    <Content>HELLO</Content>
    <Font>Sans Serif,12,-1,5,50,0,0,0,0,0</Font>
    <Animation>Horizontal</Animation>
    <Offset X="0" Y="0"/>
   </Algorithm>
```

The `<FixtureGroup>` ID must match an engine-level `<FixtureGroup>` (§3). See [[effect-recipes-cookbook]] for matrix patterns and [[club-rig-mayans]] for the venue's pixel groups.

---

## 11. Script

A line-based mini-language. Write order: common attrs -> `<Speed>` -> `<Direction>` -> `<RunOrder>` -> repeated `<Command>`.

- **`<Command>`** — one per source line, **percent-encoded** (`QUrl::toPercentEncoding` on save, decoded on load). That is why `:` shows up as `%3A`. Empty trailing lines are dropped.

Command keywords (the decoded content): `startfunction`, `stopfunction`, `stoponexit`, `blackout` (`on`/`off`), `wait`, `waitkey`, `waitfunctionstart`, `waitfunctionstop`, `setfixture`, `systemcommand`, `label`, `jump`; sub-keywords `ch`, `val`, `arg`.

```xml
  <Function Type="Script" Name="My Script" ID="60">
   <Speed FadeIn="0" FadeOut="0" Duration="0"/>
   <Direction>Forward</Direction>
   <RunOrder>Loop</RunOrder>
   <Command>startfunction%3A0</Command>
   <Command>wait%3A1s</Command>
   <Command>stopfunction%3A0</Command>
  </Function>
```

(`%3A` = `:`. So the lines decode to `startfunction:0`, `wait:1s`, `stopfunction:0`.)

---

## 12. Show (+ Track + ShowFunction)

A timeline arranging other functions across tracks. Write order: common attrs -> `<TimeDivision .../>` -> repeated `<Track>`.

- **`<TimeDivision Type="" BPM=""/>`** — `Type` = `Time`, `BPM_4_4`, `BPM_3_4`, `BPM_2_4`; `BPM` integer.
- **`<Track ID="" Name="" SceneID="" isMute="">`** — `SceneID` = bound scene (only if valid); `isMute` (lowercase i) = `0`/`1`. Contains repeated `<ShowFunction>`. (Legacy read-only `<Functions>` = CSV of IDs.)
- **`<ShowFunction ...>`** self-closing attributes: `UID`, `TrackID` (both only when a valid trackId is supplied), `ID` (referenced function), `StartTime` (ms), `Duration` (ms, **only if non-zero** — 0 means use the function's own length), `Color` (`#rrggbb`, only if valid), `Locked` (`1`, only if locked).

Default per-type `<ShowFunction>` colors (applied at load if missing): Chaser `#556b80`, Audio `#608053`, RGBMatrix `#659b9b`, EFX `#803c3c`, Video `#938c14`, else `#646464`.

```xml
  <Function Type="Show" Name="My Show" ID="70">
   <TimeDivision Type="Time" BPM="120"/>
   <Track ID="0" Name="Track 1" SceneID="5" isMute="0">
    <ShowFunction ID="20" StartTime="0" Duration="5000" Color="#556b80"/>
    <ShowFunction ID="40" StartTime="5000" Color="#803c3c"/>
   </Track>
  </Function>
```

---

## 13. Audio & Video

Both are valid function types using the common-attribute machinery (§4) plus a source-path child and a start-time/offset. Typical (approximate — verify against the specific build's `audio.cpp`/`video.cpp` if byte-exact output is needed):

- **Audio** (`Type="Audio"`): `<Source>` element with the audio file path (stored relative to workspace dir when inside it), plus speed/start-time fields. Used as a track clip in a Show.
- **Video** (`Type="Video"`): `<Source>` with the video path (or a URL/screen-output target), plus geometry/fullscreen and start-time fields.

For both, paths follow the same workspace-relative normalization as RGB Image filenames (§10). If you need exact attribute names for these two, treat the above as a guide and confirm in source — the requested detail focus was the other function types.

---

## 13b. Monitor (2D/3D layout map)

Optional `<Monitor>` element directly under `<Workspace>` (sibling of `<Engine>`/`<VirtualConsole>`), holding the Fixture Monitor's physical layout. Lets you ship a "stage map". Schema (from `engine/src/monitorproperties.cpp`):

```xml
 <Monitor Display="1" ShowLabels="1">
  <Font>Arial,11,-1,5,50,0,0,0,0,0</Font>
  <ChannelStyle>0</ChannelStyle>
  <ValueStyle>0</ValueStyle>
  <Grid Width="12" Height="8" Depth="5" Units="0"/>
  <FxItem ID="34" XPos="1000" YPos="2200" GelColor="#ff0000"/>
  <FxItem ID="8"  XPos="2000" YPos="4000" Rotation="90"/>
 </Monitor>
```

- `Display` attr: `0`=DMX text grid, `1`=Graphics (2D layout). `ShowLabels` `0`/`1`.
- `<Grid Width Height Depth Units>` — dimensions in `Units` (`0`=Meters default 5×3×5, `1`=Feet). Optional `POV` attr (`1`=Top,`2`=Front,`3`=Right,`4`=Left); omit ⇒ Undefined (keeps grid as written — safest).
- `<FxItem>` self-closing per fixture/head: `ID` (fixture ID, required), `XPos`/`YPos` (**always written, in mm** — a 12×8 m grid = 12000×8000 mm canvas), optional `Head`, `Linked`+`Name`, `Hidden`/`InvertedPan`/`InvertedTilt`=`True`, `Rotation` (Qt-Widgets build writes only Y rotation here), `GelColor` (`#rrggbb`, monitor swatch only — not DMX), `FixedZoom`. QMLUI/3D builds additionally write `ZPos`/`XRot`/`YRot`/`ZRot`/scale and `MeshItem`/`StageItem`; the loader skips unknowns so a 2D map loads fine in either.

## 13c. How QLC+ rewrites a file on save (canonical form)

When the operator opens a hand-edited `.qxw` in QLC+ and saves, **QLC+ rewrites the whole file** in its canonical form. Hand edits survive *semantically* (the loader is order-independent) but are reformatted — so anchors used for programmatic editing can change after a save. Observed on this repo's 4.14.x Qt-Widgets build:

- **`<Function>` attribute order becomes `ID, Type, Name`** (then `Hidden`/`Path`/`BlendMode`/`Priority`). A hand-written `Type`-first tag comes back `ID`-first. → grep `Type="EFX"`, not `<Function Type="EFX"`.
- **`<FixtureVal>` channel,value pairs are re-sorted ascending by channel index.** Values are unchanged; only order. E.g. a V2 line written `2,255,1,95,0,8` returns as `0,8,1,95,2,255`.
- **EFX `<Fixture>` blocks are expanded to multi-line** (`<ID>/<Head>/<Mode>/<Direction>/<StartOffset>` each on its own line). Inline one-line `<Fixture>…</Fixture>` is accepted on load but rewritten multi-line — so match EFX fixtures by `<ID>`, not by an inline string.
- **Indentation/whitespace normalized** to one space per nesting level.
- **Defaults dropped:** `Priority="0"` and other default attributes are omitted on rewrite.
- **`<Fixture>`s are ID-sorted; `<Function>`s are hash-ordered** (not ID-sorted); empty sections collapse (`<SimpleDesk><Engine/></SimpleDesk>`).

**Practical rule:** after the operator saves in QLC+, **re-READ before editing** and match on stable tokens (function/fixture IDs, names) rather than exact attribute order or channel-pair strings. Never assume your last-written formatting is still present.

## 14. Gotchas for hand-editing / programmatic generation

- **Unique IDs.** Function IDs must be unique across all `<Function>`s and ≠ `4294967295` (`Function::invalidId()`/`UINT_MAX`). Fixture IDs are a separate namespace, also unique, also referenced everywhere (`FixtureVal ID`, EFX `<ID>`, `Head Fixture=`, ChannelsGroup pairs). A duplicate or dangling ID silently breaks references.
- **Address is 0-based.** `<Address>` is 0-based within the universe even though the UI shows 1-based DMX addresses. Subtract 1 from the desk number.
- **Channel indexing is 0-based** and is **per-fixture**, in the fixture's `.qxf` channel order — not absolute DMX. `<FixtureVal>` `channel,value` pairs and CSV channel lists all use this index. Get the order wrong and you drive the wrong attribute. See [[qlc-fixture-definition-format]].
- **Values are 0–255** (8-bit). 16-bit attributes occupy two separate channel indices (coarse/fine) — set both.
- **Speeds/times are milliseconds** as decimal integers — *except* in `Beats` tempo mode, where they are beat counts at **1000 = 1 beat**. Special sentinels: `defaultSpeed()` = `4294967295`, `infiniteSpeed()` = `4294967294`. A `Duration` of `4294967294` = run forever/hold.
- **`Hold` is only on chaser `<Step>`**, never on a function-level `<Speed>`. Step `Duration` is derived from `FadeIn`+`Hold` on load.
- **Booleans are mixed.** Generic boolean fields use literal `True`/`False`; but `IsRelative`, `isMute`, `DimmerControl`, `Locked`, `CrossUniverse`-style flags and EFX `<Mode>` use `0`/`1` (or presence-only). Use each field's documented form.
- **`Priority` is omitted when 0.** Present only as an int attribute on `<Function>` when non-zero (this fork's per-function priority field; loader defaults to 0). Don't write `Priority="0"`.
- **`Path` folders** group functions in the Function Manager tree using `/` separators, with the leading `Type/` prefix stripped (e.g. a Scene in folder "Looks/Warm" stores `Path="Looks/Warm"`). The folder is purely organizational.
- **`<FixtureGroup>` is three different things** depending on context: a full element with `<Head>` children at engine level (§3); a self-closing `ID`-only **reference** inside a Scene (§5); a plain **text** element holding the group ID inside an RGB Matrix (§10). Don't mix the forms.
- **Sequence needs `BoundScene`** and its container Scene should be `Hidden`. The Sequence's steps carry packed DMX (`fx:ch,val:...`), only non-zero values written; missing channels fall back to the bound scene's values.
- **RGB Matrix colors are packed decimal** `0xAARRGGBB` integers, not `#rrggbb`. Keep alpha `0xFF` (e.g. red = `4294901760`). `<ShowFunction>` colors, by contrast, ARE `#rrggbb`. Two different color encodings in the same file.
- **RGB Matrix Script body isn't stored** — only the script *name* (in the `<Algorithm Type="Script">` text). The script file must exist in the QLC+ scripts cache on the target machine, and parameters live in `<Property>` elements. Renaming/missing scripts break the matrix.
- **Percent-encoding in Script `<Command>`.** Every command line is URL-percent-encoded; `:` becomes `%3A`, spaces `%20`, etc. Decode/encode accordingly when editing.
- **Paths are workspace-relative.** RGB Image filenames, Audio/Video sources are normalized relative to the workspace directory when inside it (else absolute). `Doc::setWorkspacePath` must be set for this to resolve; moving the `.qxw` without its asset folder breaks them.
- **`<Function>` order is hash-based, not ID-sorted** on save (unlike `<Fixture>`, which is ID-sorted). Don't rely on function order for anything; reorder freely. Engine reads them by ID.
- **`<Address>`/`<Universe>` collisions aren't validated** in the file — overlapping patches load fine but conflict at output. Validate your patch externally. See [[club-rig-mayans]].
- **Attribute/element order is loader-independent** but write order matters for byte-exact diffs (e.g. round-trip testing against QLC+'s own output). Follow §4–§12 write orders if that matters to you.
- **`CurrentWindow`** on `<Workspace>` is cosmetic (which tab opens). Safe to set to `FunctionManager` or omit.

For runtime semantics (how HTP/LTP, priority, fade, and the function intensities actually combine at output) see [[qlc-functionality-reference]]; for venue specifics see [[club-rig-mayans]]; for ready-to-use building blocks see [[effect-recipes-cookbook]].