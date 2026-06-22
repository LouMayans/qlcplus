---
name: club-rig-mayans
description: "The venue's real QLC+ rig & project: fixtures, DMX addressing/universes, custom .qxf, existing function inventory and naming conventions — reuse for automated edits"
metadata:
  node_type: memory
  type: project
  originSessionId: 0a2fa9d9-bc6b-46d6-bdbb-e98d6dad045a
---

# Mayans club rig & project inventory

Authoritative inventory of THIS venue's QLC+ setup. Future automated edits MUST reuse the exact addressing, fixture IDs, names, and conventions below. Related: [[qlc-fixture-definition-format]] (the `.qxf` custom fixtures), [[qlc-save-file-format]] (the `.qxw` workspace structure these IDs live in), [[fixture-types-and-roles]] (what each fixture class is for).

Source files:
- Workspace: `C:\Users\Louma\Documents\GIT Clones\QLCProjectCloneOld\qlcplus\SaveFile\Main Project.qxw` (QLC+ 4.14.4, author "louma", 7478 lines).
- Custom fixtures: `C:\Users\Louma\Documents\GIT Clones\QLCProjectCloneOld\qlcplus\Fixtures\`.
- Live control surface: the user's **web app via the QLC+ Web Access WebSocket API** (HTTPS/WSS, built into this fork). See [[salesforce-qlcplus-integration]].

**Two hard rules before any edit:**
1. **DMX `Address` in the file is 0-based.** `Address` = desk channel − 1. Address 0 = DMX 1; Address 256 = DMX 257.
2. **Fixture IDs (0-34) are stable handles.** Groups, scenes, channel-groups, EFX and VC widgets all reference fixtures by ID. Never renumber an existing fixture.

This fork adds a per-function `Priority="N"` attribute. Values in use: 0 (default, 222 funcs), 1, 4, 5, 10, 20, 50, 100. Higher wins. Name suffixes `P20`/`P100` encode the priority (override scenes = P20, the kill scene = P100).

---

## 1) Rig — patched fixtures (35 fixtures, IDs 0-34)

### Custom Mayans fixtures (defined in `./Fixtures/`)

| ID | Model | Fixture Name | Univ | Addr (0-based) | DMX start | Ch | .qxf file |
|----|-------|--------------|------|------|-----|----|-----------|
| 34 | BEAM230 V3 | BEAM230 #1 | 0 | 0 | 1 | 16 | Mayans-BEAM230 V3.qxf |
| 1 | BEAM230 | BEAM230 #2 | 0 | 16 | 17 | 16 | Mayans-BEAM230.qxf |
| 2 | BEAM230 | BEAM230 #3 | 0 | 32 | 33 | 16 | Mayans-BEAM230.qxf |
| 3 | BEAM230 | BEAM230 #4 | 0 | 48 | 49 | 16 | Mayans-BEAM230.qxf |
| 4 | BEAM230 | BEAM230 #5 | 0 | 64 | 65 | 16 | Mayans-BEAM230.qxf |
| 5 | BEAM230 | BEAM230 #6 | 0 | 80 | 81 | 16 | Mayans-BEAM230.qxf |
| 6 | BEAM230 | BEAM230 #7 | 0 | 96 | 97 | 16 | Mayans-BEAM230.qxf |
| 7 | BEAM230 | BEAM230 #8 | 0 | 112 | 113 | 16 | Mayans-BEAM230.qxf |
| 12 | BEAM230 | BEAM230 #9 | 0 | 192 | 193 | 16 | Mayans-BEAM230.qxf |
| 13 | BEAM230 | BEAM230 #10 | 0 | 208 | 209 | 16 | Mayans-BEAM230.qxf |
| 14 | BEAM230 | BEAM230 #11 | 0 | 224 | 225 | 16 | Mayans-BEAM230.qxf |
| 15 | BEAM230 | BEAM230 #12 | 0 | 240 | 241 | 16 | Mayans-BEAM230.qxf |
| 0 | BEAM230V2 | BEAM230V2 #13 | 0 | 256 | 257 | 16 | Mayans-BEAM230 V2.qxf |
| 8 | WASH | Wash #1 | 0 | 128 | 129 | 16 | WASH-Mayans-Mayans.qxf |
| 9 | WASH | Wash #2 | 0 | 144 | 145 | 16 | WASH-Mayans-Mayans.qxf |
| 10 | WASH | Wash #3 | 0 | 160 | 161 | 16 | WASH-Mayans-Mayans.qxf |
| 11 | WASH | Wash #4 | 0 | 176 | 177 | 16 | WASH-Mayans-Mayans.qxf |
| 31 | Revolver Wash | Revolver Wash | 1 | 64 | 65 | 12 | Mayans-Revolver-Wash.qxf |

### Library fixtures

| ID | Mfr / Model | Fixture Name | Univ | Addr (0-based) | DMX start | Ch | Mode |
|----|-------------|--------------|------|------|-----|----|------|
| 16 | American DJ VPar | VPAR Left Column | 1 | 32 | 33 | 7 | 7 Channel |
| 17 | American DJ VPar | VPAR Right Column | 1 | 40 | 41 | 7 | 7 Channel |
| 28 | American DJ VPar | VPAR Stage Large Truss Floor #1 | 1 | 48 | 49 | 7 | 7 Channel |
| 29 | American DJ VPar | VPAR Stage Small Truss Floor #2 | 1 | 55 | 56 | 7 | 7 Channel |
| 18 | Venue ThinPAR 38 | Center Truss Washes | 1 | 16 | 17 | 7 | 7-CH Mode |
| 19 | Venue Tetra Bar | Tetra Bar #1 | 1 | 0 | 1 | 6 | 6-CH |
| 20 | Venue Tetra Bar | Tetra Bar #2 | 1 | 6 | 7 | 6 | 6-CH |
| 26 | Chauvet Swarm 5 FX | Ceiling Truss Swarms | 1 | 80 | 81 | 9 | Standard Mode |
| 30 | Chauvet Swarm 5 FX | Stage Large Truss Swarms | 1 | 117 | 118 | 9 | Standard Mode |
| 21 | Generic RGB | South East LED Wall | 1 | 99 | 100 | 3 | RGB |
| 22 | Generic RGB | North East LED Wall | 1 | 102 | 103 | 3 | RGB |
| 32 | Generic RGB | North West LED Wall | 1 | 126 | 127 | 3 | RGB |
| 23 | Generic RGB | Middle Truss White Panel #1 | 1 | 105 | 106 | 3 | RGB |
| 24 | Generic RGB | Middle Truss White Panel #2 | 1 | 108 | 109 | 3 | RGB |
| 25 | Generic RGB | Middle Truss White Panel #3 | 1 | 111 | 112 | 3 | RGB |
| 27 | Generic RGB | Female Bathroom 1 RGB | 1 | 114 | 115 | 3 | RGB |
| 33 | Generic / Generic | FOG | 1 | 499 | 500 | 1 | 1 Channel |

### Totals per model
- **Moving "SPOT" beams (13):** BEAM230 base ×11, BEAM230 V2 ×1, BEAM230 V3 ×1.
- **Moving washes (5):** Mayans WASH ×4, Revolver Wash ×1.
- **PARs / bars:** ADJ VPar ×4, Venue Tetra Bar ×2, Venue ThinPAR 38 ×1.
- **FX / static:** Chauvet Swarm 5 FX ×2, Generic RGB ×7, Generic FOG ×1.

### Universe / address map

Universes are stored as IDs: **Universe ID 0 = "Universe 1", ID 1 = "Universe 2"**, IDs 2 & 3 exist but are empty/unpatched.

**Universe 1 (ID 0)** — all 13 beams + 4 washes, contiguous 16-channel blocks, 0-271 (0-based):

| DMX range (0-based) | Fixtures |
|------|----------|
| 0-15 | BEAM230 #1 (V3, id34) |
| 16-127 | BEAM230 #2-#8 (id1-7) |
| 128-191 | Wash #1-#4 (id8-11) |
| 192-255 | BEAM230 #9-#12 (id12-15) |
| 256-271 | BEAM230V2 #13 (id0) |

Note the gap at 128-191 (washes sit mid-block between beam runs). **Next free block in U1 = DMX 272+ (Address 272).**

**Universe 2 (ID 1)** — everything else:

| DMX range (0-based) | Fixtures |
|------|----------|
| 0-11 | Tetra Bar #1, #2 (id19-20) |
| 16-22 | Center Truss Washes (id18) |
| 32-46 | VPAR Left/Right Column (id16-17) |
| 48-61 | VPAR Stage Floor #1/#2 (id28-29) |
| 64-75 | Revolver Wash (id31) |
| 80-88 | Ceiling Truss Swarms (id26) |
| 99-116 | LED walls + Middle Truss panels + Female Bathroom (id21-25,27) |
| 117-125 | Stage Large Truss Swarms (id30) |
| 126-128 | North West LED Wall (id32) |
| 499 | FOG (id33) |

---

## 2) Custom-fixture channel layouts (0-based FixtureVal indices)

`<FixtureVal>` payloads are `channel,value` pairs where `channel` is the **0-based index into the fixture's mode channel list**. These indices are load-bearing for every scene/EFX.

**BEAM230 (base, ids 1-7, 12-15) and BEAM230V2 (id0)** — same order:

| Ch | Function | | Ch | Function |
|----|----------|--|----|----------|
| 0 | Pan | | 8 | **Color** |
| 1 | PanFine | | 9 | Color Effect |
| 2 | Tilt | | 10 | GOBO |
| 3 | TiltFine | | 11 | PRISM |
| 4 | Pan/Tilt speed | | 12 | PRISM Rotation |
| 5 | **Dimmer** | | 13 | FOCUS |
| 6 | **Strobe** | | 14 | LAMP |
| 7 | Frost | | 15 | RESET |

**BEAM230 V3 (id34 ONLY — DIFFERENT order; handle as a special case everywhere):**

| Ch | Function | | Ch | Function |
|----|----------|--|----|----------|
| 0 | Pan | | 8 | **Color** |
| 1 | Tilt | | 9 | Color Effect |
| 2 | Pan/Tilt speed | | 10 | Frost |
| 3 | PanFine | | 11 | PRISM |
| 4 | TiltFine | | 12 | **LAMP** |
| 5 | **FOCUS** | | 13 | PRISM Rotation |
| 6 | **Strobe** | | 14 | GOBO |
| 7 | **Dimmer** | | 15 | RESET |

Caveat — the user's color convention for id34: color scenes write the color value to **channel 5** on id34 (not channel 8). On the V3, channel 5 is FOCUS by the fixture definition, but the SPOT BEAM COLORS channel group and the override scenes use ch5 for id34, and the resulting DMX value (e.g. 8 = red) lands red in practice. Replicate the existing pattern rather than "correcting" to ch8 — match what the channel groups do.

**WASH (ids 8-11):**

| Ch | Function | | Ch | Function |
|----|----------|--|----|----------|
| 0 | Master Dimmer | | 8 | PanFine |
| 1 | Red | | 9 | TiltFine |
| 2 | Green | | 10 | Motor Speed |
| 3 | Blue | | 11 | Focus/Zoom |
| 4 | White | | 12 | Strobe |
| 5 | Test Mode Dimming | | 13 | gradient/sound |
| 6 | Pan | | 14 | speed |
| 7 | Tilt | | 15 | Reset |

**Revolver Wash (id31):**

| Ch | Function | | Ch | Function |
|----|----------|--|----|----------|
| 0 | Pan | | 6 | Blue |
| 1 | Tilt | | 7 | White |
| 2 | TiltFine | | 8 | Strobe |
| 3 | PanFine | | 9 | Master dimmer |
| 4 | Red | | 10 | Open Light |
| 5 | Green | | 11 | Gobo wheel |

Quick reference — spot control channels:

| Role | BEAM230 base/V2 | BEAM230 V3 (id34) |
|------|------|------|
| Dimmer | ch5 | ch7 (and LAMP ch12) |
| Strobe | ch6 | ch6 |
| Color | ch8 | ch5 (per user convention) |

**BEAM230 color-wheel DMX values** (the Color channel — ch8 base / ch5 on id34; verified from the existing override scenes): White/Open `0`, Red `8`, Black/Deep Blue `16`, Yellow `40`, Purple `48`, Cyan `72`, Blue `88`, Orange `96`, Emerald/Green `104`. V3 LAMP (ch12) is a no-op "Nothing" group — safe to drive; ch7 is the real V3 dimmer. BEAM Strobe (ch6) preset `ShutterStrobeSlowFast`: rises slow→fast with value (≈60 slow, 120 medium, 200 fast); dimmer must be up for strobe to show.

**RGB/PAR channel orders (0-based, library fixtures):**

| Fixture (ids) | Channel order |
|---|---|
| ADJ VPar 7ch (16,17,28,29) | 0 R, 1 G, 2 B, 3 Amber, 4 Master Dimmer, 5 Strobe, 6 Color Macro |
| Venue ThinPAR 38 7ch (18) | 0 R, 1 G, 2 B, 3 Color Macro, 4 Strobe, 5 Mode, 6 Dimmer |
| Venue Tetra Bar 6ch (19,20) | 0 R, 1 G, 2 B, 3 Amber, 4 Dimmer, 5 Strobe (single segment — NOT a pixel matrix) |
| Generic RGB 3ch (21,22,23,24,25,27,32) | 0 R, 1 G, 2 B (no master dimmer) |

For an RGB-Matrix pixel row prefer the Generic RGB fixtures (no master dimmer to fight); Tetra/VPar need their Dimmer channel raised separately or matrix output stays dark.

---

## 3) Fixture Groups & Channels Groups

**Fixture Groups (2):**

| ID | Name | Size | Heads |
|----|------|------|-------|
| 0 | SPOT Group | 2×4 | 34,1,2,3,4,5,6,7,12,13,14,15 (the 12 beams) + 16,17,18,19,20,23,24,25,30,0 (VPARs, ThinPAR, Tetra bars, panels, stage swarms, V2 beam) laid out 2-wide. Master selection for EFX/movement. |
| 1 | VIP SPOT 1 | 1×1 | fixture 2 (BEAM230 #3) |

**Channels Groups (3)** — each a list of `fixtureID,channelIndex` pairs over the 12 spots; used as VC slider targets and grouped dimmer/color/strobe control:

| ID | Name | Members |
|----|------|---------|
| 0 | Dimmer Group Spot | ids 1-7,12-15 @ ch5 + id34 @ ch12 (V3 LAMP). Master dimmer of the spots. |
| 1 | SPOT BEAM COLORS | ids 1-7,12-15 @ ch8 (Color) + id34 @ ch5. |
| 2 | SPOT STROBE | ids 1-7,12-15 @ ch6 (Strobe) + id11 @ ch12 + id34 @ ch9. |

---

## 4) Function inventory (270 functions)

Counts by type: **181 Scene · 40 Sequence · 17 Chaser · 17 EFX · 10 Show · 4 Collection · 1 Script** (= 270 definitions). The raw file has 431 `<Function …>` tags; the extra 161 are Virtual Console widget *references* (`<Function ID="…"/>` with no `Type`), not definitions.

Most functions have no `Path` (flat in root). Foldered functions (Path → count):

| Path | Count | Purpose |
|------|-------|---------|
| `ALL SPOTLIGHT FUNCTIONS OVERRIDE P20` | 10 | global spot-color override scenes @ priority 20 |
| `ALL SPOTLIGHT FUNCTIONS` | 1 | |
| `SPOT LIGHTS FOR VIP` | 8 | |
| `SPOT LIGHT COLOR OVERRIDES 01`..`12` | 8 each (96) | per-spot, per-color override scenes (one folder per physical spot 1-12, 8 colors each) |
| `North Spot Position1` | 7 | position scenes |
| `South Spot Positions` | 10 | position scenes |
| `Center Truss Colors` | 2 | |
| `VPAR Columns` | 2 | |
| `Chaser Scenes/Chaser 1 Scenes` | 2 | show building blocks |
| `Chaser Sequences/Chaser 1` | 2 | |
| `SubChasers` | 2 | |
| `EFX Chasers` | 5 | |

### Naming conventions (reuse EXACTLY)

| Pattern | Meaning |
|---------|---------|
| `SPOT <n> <Color>` | per-spot color scene. n = 1-12. Color ∈ {Black Blue, Blue, Green, Orange, Purple, Red, White, Yellow}. 96 scenes — the bulk of the 181. |
| `ALL SPOTLIGHT COLOR <COLOR> P20` | global spot color override @ P20 |
| `ALL SPOTLIGHT COLOR OFF P100` | kill scene @ P100 |
| `ALL SPOTS <COLOR>` | global spot color |
| `All Lights <Color>` / `Literally Everything <Color>` | whole-rig look |
| `ON OFF <zone>` | per-zone toggle (e.g. `ON OFF ALL SPOT LIGHTS`, `ON OFF Roof Truss Swarms`) |
| `North Spots Looking <dir>` / `South Spots Position <dir> <n> Rotation` | movement; positions stored as Scene+Sequence pairs |
| `SPOT VIP 1`..`8` | VIP spot looks |
| `Default Scene Club 1` | default house state |
| `Color Chasers`, `Line Serial <X/Y/Diamond>`, `Washer Movement`, `Small/Medium Circular Spin on the Floor` | descriptive chasers/EFX |

Auto-generated leftovers exist and are tolerated but should NOT be imitated for new work: `New Sequence NNN`, `Auto Chaser Show N`, `(Copy)` suffixes.

Show names: `Chaser 1`..`7 Show`, `Chaser Show 7`, `Small/Medium Spinning Flashing Fast White`, `Show 2`.
Collections: `On 1`, `On 2`, `Literally Everything Red`, `Literally Everything Blue`.

---

## 5) Deep-dives — representative functions

### SCENE — id1 "ALL SPOTLIGHT COLOR RED P20" (Priority 20, Path `ALL SPOTLIGHT FUNCTIONS OVERRIDE P20`)
```xml
<Speed FadeIn="0" FadeOut="0" Duration="0"/>
<FixtureVal ID="1">8,8</FixtureVal>     <!-- ids 2-7,12-15 all "8,8" -->
<FixtureVal ID="34">5,8</FixtureVal>
```
Snap scene (no fade). For the 11 base BEAM230s it sets ch8 (Color) = 8 (red on that color wheel). For id34 (V3) it writes **ch5** = 8 per the user's color convention. **Only the Color channel is touched** — dimmer/position are left to lower-priority scenes. Priority 20 overrides default (P0) base looks but yields to the P100 kill scene. This is the canonical template for new global color overrides.

### CHASER — id244 "Color Chasers" (Priority 0)
```xml
<Speed FadeIn="0" Hold/Duration="300"/>   <!-- Direction Forward, RunOrder Loop -->
<SpeedModes ... Duration="Common"/>
<Step Number="0">199</Step>   <!-- All Lights Purple -->
<Step Number="1">0</Step>     <!-- All Lights Red -->
<Step Number="2">243</Step>   <!-- All Lights Blac Blue -->
<Step Number="3" Hold="300">0</Step>
```
4-step looping chaser whose steps are **references to whole-rig color scenes** (purple → red → blackblue → red), 300 ms common duration, snap. Pattern: build color motion by chaining existing `All Lights <Color>` scenes rather than re-specifying channels.

### EFX — id160 "Small Circular Spin on the Floor" (Priority 0)
```xml
<Fixture> ID 34,1,2,3,4,5,6,7,13,14,15,12,0 ... </Fixture>   <!-- 13 beams, Direction Forward -->
<PropagationMode>Parallel</PropagationMode>
<Speed FadeIn="0" FadeOut="0" Duration="3000"/>
<Direction>Forward</Direction> <RunOrder>Loop</RunOrder>
<Algorithm>Circle</Algorithm> <Width>40</Width> <Height>40</Height> <Rotation>0</Rotation>
<Axis Name="X"><Offset>127</Offset><Frequency>2</Frequency><Phase>90</Phase></Axis>
<Axis Name="Y"><Offset>127</Offset><Frequency>3</Frequency><Phase>0</Phase></Axis>
```
All 13 moving spots run a Circle pan/tilt pattern, **Parallel** (every head at the same circle point simultaneously), small radius (40×40), centered at pan/tilt 127, 3 s/loop. X/Y frequency 2/3 with 90° phase gives the tight floor circle. Sibling id164 "Medium Circular Spin" = same with larger Width/Height. Project-wide EFX usage: Algorithms Circle×7, Line×4, Lissajous×4, Diamond×1, SquareChoppy×1; PropagationModes Parallel×8, Serial×5, Asymmetric×4. See [[fixture-types-and-roles]] for which fixtures suit which algorithm.

### COLLECTION — id268 "Literally Everything Red" (Priority 0)
```xml
<Step Number="0">256</Step>
<Step Number="1">259</Step>
<Step Number="2">1</Step>
```
Collections fire all members at once. Member 3 (id1) = the "ALL SPOTLIGHT COLOR RED P20" scene; combined with ids 256 & 259 (other red scenes) it turns the whole venue red from one button. Blue twin id269 references ids 60, 251, 262.

### SHOW — id47 "Chaser 1 Show" (Priority 0)
```xml
<TimeDivision Type="Time" BPM="120"/>
<Track ID="0" Name="Track 1"/>  <Track ID="1" Name="Track 2"/>
<Track ID="2" Name="All On" SceneID="51">
 <ShowFunction ID="57" StartTime="0" Duration="15000" Color="#556b80"/></Track>
<Track ID="3" Name="Color" SceneID="55">
 <ShowFunction ID="59" StartTime="0" Duration="15000" Color="#556b80"/></Track>
```
Timeline show on a 120-BPM/time grid. Two active tracks each hold one 15 s clip at t=0: an "All On" scene (id51, hidden in `Chaser Scenes/Chaser 1 Scenes`, sets ch5/ch6 dimmer+strobe across spots) and a "Color" track. Richer example id163 "Small Spinning Flashing Fast White" layers three tracks: EFX id160 at t0 + a copy at t3000, a white scene, and a fast-strobe scene (strobe+spin+white combo).

---

## 6) Output patch & input / control surface

**Output (Universes 1 & 2):** Plugin **ArtNet**, UID `169.254.37.211` (NIC), Line 2, `outputIP=192.168.5.162`, transmitMode Full. All DMX goes out over Art-Net to the node at **192.168.5.162**. Universes 3-4 have no output.

**Live control surface — the website.** The rig is driven from the user's **web app over the QLC+ Web Access WebSocket API** (HTTPS/WSS, built into this fork; port 9999, endpoint `/qlcplusWS`, pipe-delimited `widgetID|value` protocol). The website operates the Virtual Console directly by **widget ID**; there is no hardware or app control surface. See [[salesforce-qlcplus-integration]] and repo-root `SALESFORCE_QLCPLUS_INTEGRATION.md`.

**Legacy input config (still in the `.qxw`, NO LONGER the control path):** an OSC input patch on Universes 1 & 2 (Plugin OSC, profile "mayans mayans", inputPort 9000, feedback on U2) plus **141** `<Input Universe="0" Channel="N">` bindings on VC widgets, left over from the previous setup. These are dormant now that control comes from the website — leave or strip them, but don't rely on them. No `.qxi` MIDI profile is used.

Also present: a Script function (id6 "New Script 6") containing URL-encoded `setfixture:0 ch:5 val:255 // BEAM230 #1, Dimmer`-style commands — a scratch/test script, not production.

---

## 7) Virtual Console layout

VC is a large control surface operated from the website: **3 Frames, 27 SoloFrames, 160 Buttons, 39 Sliders, 1 SpeedDial.** No CueList, no XYPad.

**SoloFrames (radio-style; one active at a time) — the organizing structure:**
- 12 × `SPOT <n> Colors` (n=1-12) — per-spot color picker (8 color buttons each).
- `ALL LIGHT COLORS`, `ALL SPOT LIGHT COLORS`, `Column Color Overrides` — global color banks.
- Zone panels: `Center Surround Washes`, `LED Strip Dance Floor`, `White 3 Triangle Truss`, `Corner LED Lights South East / North East / North WEST`, `Stage Large Truss Bottom PARS`, `Stage Small Truss Bottom PARS`, `Female Bathroom RGB`, `WASHERS`, `Default Club Scenes`.

**Sliders (all Level mode, monitored):**
- `MASTER DIMMER ALL ROOF LIGHTS`, `MASTER STROBE`, `Spot Light Speed`, `Dimmer`, `Strobe`, `FOG`, `All Dimmers ON`.
- 11 × {Red, Green, Blue} triples — RGB faders for LED-wall / panel / column zones (33 sliders).

**SpeedDial:** "Duration" (id166) — global speed/duration tap feeding chasers/EFX.

Buttons (160): mostly Toggle actions bound to scenes/collections/shows with `<Intensity Adjust="False">100</Intensity>` (e.g. button → `<Function ID="45"/>`). Many still carry a dormant `<Input>` binding from the old setup; the website now triggers each widget by its **widget ID** via the Web Access API. Unassigned buttons use `Function ID="4294967295"` (= none).

---

## 8) Conventions to follow when adding new functions

- **Addresses are 0-based.** Write `Address = desk − 1`. New beams → Universe 1, next free block = DMX 272+ (16-ch blocks). LED/wash/effects → Universe 2. FOG is parked at DMX 500 (Address 499); don't reuse.
- **Preserve fixture IDs (0-34).** Reference fixtures by ID in scenes/EFX/groups. Never renumber existing fixtures; new fixtures take the next free ID (35+).
- **Color scenes touch ONLY the Color channel.** Base BEAM230/V2 → ch8; V3 id34 → ch5 (match the user's convention and the SPOT BEAM COLORS channel group, do not "fix" to ch8). Leave dimmer/position to lower-priority scenes.
- **Spot control channels:** Dimmer = ch5 base / ch7 (+LAMP ch12) on V3; Strobe = ch6; always special-case id34 because its channel order differs.
- **Name templates, verbatim:** `SPOT <n> <Color>`, `ALL SPOTLIGHT COLOR <COLOR> P<priority>`, `ALL SPOTLIGHT COLOR OFF P100`, `ON OFF <zone>`, `All Lights <Color>`, `Literally Everything <Color>`, `SPOT VIP <n>`. Color set = {Black Blue, Blue, Green, Orange, Purple, Red, White, Yellow}.
- **Priority suffix convention:** encode priority in the name. Default/base = P0 (no suffix), overrides = P20, kill scene = P100. Set `Priority="N"` to match the suffix.
- **Snap vs fade:** override/color scenes are snap (`FadeIn=0 FadeOut=0 Duration=0`). Chasers default to common 300 ms snap steps.
- **Build motion by reference:** chasers/collections reference existing scenes by function ID rather than re-specifying channels (see id244, id268). Collections fire members simultaneously; chasers step.
- **EFX defaults that work here:** Algorithm Circle, Offset 127 on both axes, Width/Height 40 (small) to larger (medium), Duration 3000, RunOrder Loop, PropagationMode Parallel for unison floor circles; Serial/Asymmetric for sweeps. Target the 13 beams via Fixture Group 0 "SPOT Group" or the explicit 13-ID list.
- **The website triggers VC widgets by widget ID** via the Web Access WebSocket API, so a new widget needs a stable ID and a valid `<Function>` (unassigned = `Function ID="4294967295"`) — an OSC `<Input>` binding is no longer required to reach it.
- **Don't imitate auto-generated names** (`New Sequence NNN`, `(Copy)`, `Auto Chaser Show N`).

See [[qlc-save-file-format]] for the exact XML element grammar of Scene/Chaser/EFX/Show/Collection, and [[qlc-fixture-definition-format]] for editing the `.qxf` channel/mode definitions referenced above.