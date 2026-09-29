---
name: stage-visualizer
description: The 3D stage view (/stage on QLC+ web access): WebSocket protocol, stage file format, axes and pan/tilt maths shared by the JS page and lightai, build and test rules
metadata:
  type: project
---

# 3D Stage visualizer: shared spec

Plan: `~/.claude/plans/peppy-zooming-liskov.md` (M1-M6). Branch `stage-visualizer`.
The page is served by QLC+ web access at `/stage` (files in `webaccess/res/`, installed to `<qlc>/Web`).
**The QLC+ show (.qxw) is the only source of fixture identity and patch.** The stage file only holds placement,
room objects and looks, keyed by **fixture ID**. IDs survive readdressing, so positions do too.

## WebSocket protocol (same-origin `/qlcplusWS`, text frames)
Payloads that are JSON always come **after a fixed number of `|`**. Never split them on `|`.

| Send | Reply / push | Level |
|---|---|---|
| `QLC+API\|getStageRig` | `QLC+API\|getStageRig\|<json rig>` | 10 |
| `QLC+API\|getStage` | `QLC+API\|getStage\|<json {path, exists, stage}>` or `...\|ERR\|reason` | 10 |
| `QLC+API\|saveStage\|<json>` | `QLC+API\|saveStage\|OK\|<rev>` or `...\|ERR\|reason`; broadcasts `VIS\|STAGE_SAVED\|<rev>` | 20 |
| `QLC+API\|getProps` | `QLC+API\|getProps\|<json {path, exists, props}>` | 10 |
| `QLC+API\|saveProps\|<json>` | `QLC+API\|saveProps\|OK\|<rev>` or `ERR`; broadcasts `VIS\|PROPS_SAVED\|<rev>` | 20 |
| `VIS\|SUBSCRIBE` | a full snapshot, then every 33 ms `VIS\|DMX\|<uni>\|<base64 512 bytes>` for changed universes (post-Grand-Master) | 20 |
| `VIS\|UNSUBSCRIBE` | (none) | |
| `VIS\|PREVIEW\|<json>` / `VIS\|PREVIEW_STOP` | relayed as-is to subscribers (lightai previews) | 20 |
| (push) | `VIS\|RIG_CHANGED\|<serial>`: a fixture was added, removed, readdressed or changed, or a show was loaded (debounced 300 ms) | |

Other pushes on the same socket (`FUNCTION|..`, `GM_VALUE|..`, widget events) can be ignored.
`<uni>` is the 0-based universe index. Byte `i` of the base64 frame is DMX channel `i+1`.

**Rig JSON** (`getStageRig`):
```json
{"serial":3,"show":"C:/.../Main Project.qxw",
 "universes":[{"id":0,"name":"Universe 1"}],
 "monitor":{"grid":[5,3,5],"units":"m"},
 "fixtures":[{"id":3,"name":"BEAM230 #4","manufacturer":"Mayans","model":"BEAM230","mode":"16 Channel",
   "type":"Moving Head","universe":0,"address":48,"channels":16,
   "physical":{"panMax":540,"tiltMax":270,"lensMin":0,"lensMax":0,"width":0,"height":0,"depth":0,
               "layout":[1,1],"focusType":"Head"},
   "heads":[[0,1,2,3]],
   "ch":[{"name":"PAN","group":"Pan","byte":0,"colour":0,"preset":"PositionPan",
          "caps":[{"min":0,"max":255,"name":"Pan","preset":"Custom","res":["#ff0000", ...]}]}],
   "monitor":{"x":2275,"y":3039,"rot":0,"gel":"#ffffff"}}]}
```
- `address` is 0-based within the universe.
- `group` is a `QLCChannel::groupToString` name: Intensity, Colour, Gobo, Speed, Pan, Tilt, Shutter, Prism, Beam, Effect, Maintenance, Nothing.
- `byte` is 0 for MSB and 1 for LSB.
- `colour` is the `QLCChannel::PrimaryColour` value as an int RGB, e.g. Red = 16711680 and Amber = 0xFF7E00; 0 means none.
- `res` holds a capability's resources: colours as `#rrggbb`, and gobo paths as strings relative to the Gobos dir.
- `monitor` is the QLC+ 2D Monitor position in mm, or null.

## Stage file `<show>.stage.json` (next to the .qxw)
- All lengths are **inches**, rounded to 1/16".
- **User axes:** X across (= QLC+ Monitor XPos), Y depth (= Monitor YPos), Z height.
- `pos` and `anchor` are measured from a fixed room origin. The UI shows and accepts **pos − anchor**. Moving the anchor never moves anything.
```json
{ "version":1, "units":"in",
  "room":{"width":600,"depth":480,"height":168,"haze":0.35},
  "anchor":[300,240,0],
  "fixtures":{"3":{"model":"Mayans/BEAM230","pos":[x,y,z],"rot":[rx,ry,rz],"hang":"hung|floor|wall",
                   "invertPan":false,"invertTilt":false,"panOffset":0,"tiltOffset":0,"look":null}},
  "models":{"Mayans/BEAM230":{"look":null,"panMax":540,"tiltMax":270,"beam":2.5,"panSpeed":225,"tiltSpeed":190}},
  "objects":[{"id":"o1","prop":"cocktail-table","name":"Table 3","category":"table","aliases":["the tables"],
              "pos":[x,y,z],"rz":0,"scale":[1,1,1],"color":null}],
  "propDefs":{"cocktail-table":{ ...a copy of the prop definition... }} }
```

**Prop library** (`%USERPROFILE%\QLC+\stage-props.json`, shared across shows):
```json
{"version":1,"props":{"cocktail-table":{"name":"Cocktail table","category":"table","aliases":[],
  "parts":[{"shape":"box|cylinder|sphere|cone|plane|torus","size":[w,d,h],"pos":[x,y,z],"rot":[rx,ry,rz],
            "color":"#8b5a2b","material":"matte|gloss|metal|glass|led"}]}}}
```
- A prop's origin is its bottom centre.
- Part `pos` is the centre of the part relative to that origin, in inches.
- For cylinder, cone and sphere, `size` = [diameter, diameter, height].

## Status (2026-09-29, Settings popup redesign - centered/tabbed modal)

Solo pass, files: `stage.html` (popup markup only), `stage-app.js` (`wireSettingsPopup`/
`wireGraphicsSettingsTab` only), new `stage-settings.css`, two small `stage.css` edits (deleted
the old anchored-dropdown `#settings-popup{...}` rule; left everything else - `.settings-tab-btn`,
`.graphics-group*`, `#settings-body` scroll rules, the backdrop-filter/quality-low multi-id
selectors - untouched since they already target `#settings-popup`/shared classes correctly for
the new design too), `webaccess/res/CMakeLists.txt` (`stage-settings.css` added to
`WEBFILES_CLASSIC`).

- **What changed**: the gear-button popup is now a centered fixed modal (80vw x 80vh, max
  1100x760, dark blurred backdrop, click-outside/Esc closes) with 5 top tabs instead of the old
  280px anchored dropdown with 2 tabs (View/Graphics): **Video** (quality preset + the schema's
  "Quality" group minus workLight: resolutionScale/dynamicResolution/targetFps/exposure),
  **Graphics** (every other `RENDER_SETTINGS_SCHEMA` group - Bloom/AO/Antialiasing/Shadows/
  Spotlights/Beams/Bounce Light/Reflections - plus `workLight` under a synthetic "Lighting"
  heading, plus a static "Beam appearance" section for the view-settings beam width/brightness/
  length sliders, which aren't in the render schema), **Camera & Controls** (FOV+presets,
  movement/mouse-feel, seat tour interval - all pre-existing view-settings controls, just
  regrouped), **Interface** (room box/floor grid/labels toggles, perf HUD + auto-perf toggles),
  **Keys** (read-only reference built from the same `KEYBINDS` table the standalone "?" overlay
  uses). No "Objects" tab: the popup never held an object list (the Objects list lives in the
  left panel's Scene section, `stage-editor.js`/out of file scope) - the operator's ask assumed
  one existed in the popup; there wasn't one to move.
- **Every existing element id kept** (grepped first): `fov-slider`, `move-speed-slider`,
  `beam-width-slider`, `room-box-toggle`, `labels-toggle`, `tour-interval-slider`,
  `quality-preset-group`/`.quality-preset-btn`, `graphics-settings-body`, `perf-hud-toggle`,
  `auto-perf-toggle`, `settings-btn`, `settings-popup`, `settings-close-btn`, etc. - only the two
  *old* per-tab reset buttons (`settings-reset-btn`, `graphics-reset-btn`) and the 2 old tab ids
  (`settings-tab-view`/`-graphics`) were retired, replaced by 5 new `settings-tab-<name>` panels
  and one footer (`settings-reset-tab-btn`/`settings-reset-all-btn`/`settings-close-footer-btn`),
  since nothing outside this file/HTML referenced those (checked `tools/**/*.py` too).
  `isCameraInputBlocked()`'s `popupIds` check and the activity-bar gear button both still work
  unmodified - same `#settings-popup` id/hidden-class contract.
- **Video/Graphics schema split**: `RENDER_SETTINGS_SCHEMA` (`stage-render.js`, not edited) has
  one flat "Quality" group covering both preset/perf fields AND `workLight`; the UI now needs
  those on two different tabs. Solved in `stage-app.js` with `VIDEO_TAB_KEYS` (a plain key list)
  filtering the schema into two field arrays before the existing group-building loop runs once
  per container (`video-settings-body` / `graphics-settings-body`) - the schema itself, the
  flat `current` render-settings object, and `applyGraphicsPatch`/`applyPreset`/
  `matchingPresetName` are all untouched and still operate on the whole object regardless of
  which tab a control renders on.
- **Reset, made tab-scoped**: footer has "Reset tab to defaults" (resets only the active tab's
  fields) and "Reset all". `app._resetVideoTab`/`app._resetGraphicsTab`/`app._resetGraphicsAll`
  are new hooks set inside `wireGraphicsSettingsTab` (partial-reset via `resetKeysToDefaults(keys)`
  against `DEFAULT_RENDER_SETTINGS`, not a full preset re-apply, so resetting Video doesn't touch
  Graphics fields and vice versa); Camera/Interface tabs reset via local functions in
  `wireSettingsPopup` that call `apply({...})` (the existing view-settings patcher) plus, for
  fields owned by *other* wiring functions elsewhere in the file (tour interval, labels, perf HUD,
  auto-perf - each already has its own listener/localStorage key), flip the checkbox/slider value
  and `dispatchEvent(new Event(...))` rather than duplicating that logic. Cross-closure hooks only
  work because of init order (`wireSettingsPopup()` runs before `wireGraphicsSettingsTab()` in the
  bottom-of-file init sequence, confirmed by reading it) - `app._applyViewSettings` already exists
  by the time `wireGraphicsSettingsTab`'s reset hooks reference it.
- **Last-open tab remembered** via `localStorage['qlcplus-stage-settings-tab']` (try/catch).
- **Toggle switches**: all popup checkboxes render as a small track-and-thumb switch
  (`.toggle-switch`/`.toggle-slider`, new, scoped to appear anywhere in the app since it's a
  bare class - only used inside the popup today) instead of a native checkbox, per the "CS2-style"
  ask; the underlying `<input type=checkbox>` is unchanged (still what `change` listeners bind to).
- **Verified live** against the running dev QLC+ (`http://127.0.0.1:9998/stage`, did not restart
  it) with a visible Chrome + CDP, using the `stage_check.CDPClient`/`live_drive.BROWSER`/
  `free_port` pattern from `tools/stagelib/click_check.py`: all 5 tabs render and switch;
  driving the FOV slider changes `getViewSettings().fov`; driving a Graphics-tab slider changes
  `getRenderSettings()`; WASD ("w") does not move the camera while the popup is open (reuses the
  existing `isCameraInputBlocked()` popupIds check, unmodified); Esc and a backdrop click both
  close it; reopening remembers the last tab; "Reset tab to defaults" and "Reset all" both verified
  to actually restore values (fov 100->55, exposure 2.0->1 low-preset default); zero page errors
  throughout; a routine `check_settings_view` on `tools/stagelib/stage_check.py` (test 16) doesn't
  touch any settings-popup DOM ids (only `window.__stage.setViewSettings`), confirmed unaffected.
  Screenshots: `lightai/reports/stage/settings-{video,graphics,camera,interface,keys}.png`,
  `settings-narrow-800.png` (800px-wide window - dialog stays centered/readable, no horizontal
  scroll; the harder `@media (max-width:640px)` full-bleed phone layout was written but not
  screenshotted at <640px this session).
- **Not done**: no "Objects" tab (see above - nothing existed to move); per-field tooltip
  descriptions only added for a handful of genuinely non-obvious controls (keep-level,
  middle-drag-orbit, mouse-look sensitivity, auto-performance) via native `title` attributes,
  not exhaustive; the dynamic schema-driven rows have an `if (field.desc) label.title = ...` hook
  ready for descriptions but `RENDER_SETTINGS_SCHEMA` itself (out of file scope) has no `desc`
  field today, so it's currently inert for those rows.

## Status (2026-09-29, GPU perf pass - Medium's 24fps regression root-caused and fixed)

Solo pass (perf-only agent, live-GPU measurements against `C:\qlcplus-dev` on the operator's
real Intel UHD 0x9A78/ANGLE-D3D11 laptop via `tools/stagelib/live_drive.py` and ad-hoc CDP
scripts - never against headless SwiftShader). Files touched: `stage-ledstrip.js`,
`stage-render.js`, `stage-scene.js`. Root cause was NOT in these files' beam/shadow code - it
was `stage-ledstrip.js` always creating real lights.

- **Root cause, confirmed by A/B (not guessed): 24 always-on `THREE.RectAreaLight`s from the
  demo's 3 decor LED strips.** `buildLedStrip()` created up to `MAX_LIGHTS_PER_STRIP` (8)
  RectAreaLights per strip and left them `.visible = true` by default (`lightBudget =
  desiredLights`, nothing ever called `setLightBudget()` to lower it) - regardless of quality
  preset or whether the strip was actually lit. RectAreaLight uses an LTC (linearly transformed
  cosine) evaluation, one of the most expensive per-fragment light types three.js has, and with
  3 strips x 8 lights = 24 of them permanently in the light list, EVERY lit fragment of EVERY
  material in the club scene paid for 24 LTC evaluations, at full native resolution, every
  frame. This is almost certainly also the source of the "Medium used to hold 57-120fps, now
  ~24fps" regression this file already flagged (the ledstrip default-brightness/emissive-floor
  fix from the same day's earlier materials pass touched this file but not the area-light count).
  - Confirmed live: after the operator removed the 3 ledstrip parts from the demo project
    (`C:\lightai-data\stage-demo\Main Project.stage.json`, backup `...stage.json.before-
    ledstrip-removal`) and from `tools/stagelib/club_scene.py`, a fresh page load with a
    **still-unpatched** `stage-ledstrip.js` (irrelevant once 0 strips exist) plus a reload
    jumped Medium from the previously-measured **~24 fps / steady 51 ms frames** straight to
    **~59 fps / worst 17-63 ms**, with `RectAreaLight` count in the live scene verified as 0 via
    `window.__stage.three().scene.traverse()`. No other change was needed to hit the target.
  - **Fixed in `stage-ledstrip.js` (`buildLedStrip`) so this can't silently regress again once
    strips come back**: a strip now creates **zero** RectAreaLights by default - `wantAreaLights
    = part.areaLights === true || (part.lightBudget > 0)`, both false unless a future caller
    explicitly opts in. The diffuser box and InstancedMesh LED dots still glow via emissive
    materials either way (the "strip reads as lit" look is unaffected) - only the extra real
    light contribution is gated off. Even when opted in, the count is still fixed at build time
    (never changed per-frame/per-dimmer, per the existing "fixed slot" rule below) and capped at
    `MAX_LIGHTS_PER_STRIP`.
  - **Re-ruled-out, now that the real cause is fixed** (re-tested live at Medium after the fix,
    all read as noise/no real difference at the new ~59fps ceiling): shadow-map update
    throttling (`renderer.shadowMap.autoUpdate=false` + manual `needsUpdate` every 2nd/4th
    frame), `shadows.enabled` on/off, `beams.volumetric` on/off, `resolutionScale` 0.5 vs 1.0 -
    all landed within 55-60fps of each other post-fix. A separate scene audit this session also
    found shadow depth-pass geometry is cheap regardless (only 94/328 meshes cast shadows,
    ~19.6k tris), consistent with shadows never having been the dominant cost - the earlier
    same-day A/B ("shadows.enabled false -> 60fps") was real but was riding on top of the
    RectAreaLight cost, not an independent second bottleneck: with the area lights gone, toggling
    shadows off now buys almost nothing (~56 vs ~59fps).
  - **Confirmed via code read, not just testing**: no `SpotLight.map`/gobo cookie texture is set
    anywhere in this codebase (`stage-beams.js`'s `createBeamSpotLight`/`fitSpotLightToBeam`
    never touch `.map`; grepped the whole `webaccess/res/` tree) - an earlier hypothesis from
    online research about `NUM_SPOT_LIGHT_MAPS` cost does not apply here; there is nothing to fix
    on that front.
- **Second, independent bug fixed as a safety net (was masked by the RectAreaLight cost, but is
  real and worth keeping fixed)**: dynamic resolution never engaged even while genuinely
  GPU-bound. `stage-render.js`'s `render()` measured its own CPU-side `performance.now()` delta
  around the `composer.render()` call and fed THAT to `updateDynamicResolution()` - WebGL command
  submission is asynchronous, so that number stays near the JS-only cost (a few ms) even while
  the browser's actual frame delivery is throttled by the GPU to a far slower real cadence
  (confirmed: this measured well under 10ms during the steady 51ms/frame pre-fix Medium state).
  Fixed by threading the caller's real rAF-to-rAF interval through instead: `stage-scene.js`
  `loop()` already computes `dt = Math.min((now-lastFrameTime)/1000, 0.1)` from real
  `requestAnimationFrame` timestamps (clamped so a multi-second warm-up pause never reads as one
  giant frame) - now passed as `pipeline.render(dt, dt*1000)`, and `render(dt, frameIntervalMs)`
  feeds `frameIntervalMs` (falling back to its own CPU timing if not given) to
  `updateDynamicResolution`. Also rewrote that function's hysteresis, since a real frame interval
  is vsync-quantized (floors at ~1000/refreshRate no matter how much GPU headroom exists, so a
  single "good" sample can't prove there's room to raise resolution): drop is immediate and
  larger (-0.15) the moment `avg > targetMs*1.15`, and that drop sets a `scaleCeiling` that
  raising can't exceed; raising only happens after ~3s of consecutive good windows (`avg <=
  targetMs*1.05`) AND a 4s cooldown since the last drop, in small +0.05 steps - so a bad probe
  self-corrects in under a second instead of oscillating, and a resize (`setSize`, the only
  visible side effect) can't happen more than a couple of times per adjustment window. Verified
  syntax-clean (`node --check`) and live via `live_drive.py` (see final table below) - not
  independently load-tested against a deliberately slow scene this session (the fix is a safety
  net for future regressions / weaker hardware, not required to hit today's target once the
  RectAreaLight fix landed).
- **Final `live_drive.py` results** (visible Chrome, real GPU, full Low->Medium camera-motion
  run, `dynamicResolution` at its preset default the whole time): **Low**: 59.4-60.3fps every
  step, worst frame 17-33ms, 0 slow frames. **Medium**: 54.1-61.6fps every step, worst frame
  17-33ms except the very first "settle" step right after `applyQualityPreset('medium')` (150ms
  worst frame, 715ms CDP eval lag - the documented one-time shader-recompile warm-up transient,
  not a steady-state issue; no `<-- FREEZE`/`<-- hitch` flag was raised for it). Program count
  flat (20 at Low, 29 at Medium) across every step - no shader-recompile churn during motion.
  Zero console errors.
- **Visual trade-off**: none identified - the 3 demo LED strips were already unlit (dimmer-driven
  emissive at 0) before this fix; removing their RectAreaLight contribution removes a light
  source that was never visibly distinguishable as "the strip's own glow" versus the emissive
  dot/diffuser meshes (which are untouched). The operator has asked for LED strips to stay
  unlit for now and will add real LED functionality later - `stage-ledstrip.js` is ready for that
  (pass `areaLights: true` or a `lightBudget` on the part) without reintroducing this cost by
  accident.
- **New settings keys**: none. No `RENDER_SETTINGS_SCHEMA`/`QUALITY_PRESETS` changes were needed
  - the fix was removing an uncontrolled, non-budgeted light source, not adding a knob for it.
- **Not done / next steps**: if LED strip lighting is added back later, it should go through the
  same fixed-slot-budget pattern as `spot`/`bounce` (a `RENDER_SETTINGS_SCHEMA` entry + a global
  cap checked against `spot.maxLights`-style quality tiers), not a per-strip independent light
  count, so a future scene with many strips can't reintroduce an unbounded light count. The
  shadow-map throttling code written to test hypothesis (a) above was a throwaway scratch script
  (`perf_ab4.py`, not committed to `tools/`) - not adopted since it wasn't needed once the real
  cause was fixed, but the technique (`renderer.shadowMap.autoUpdate=false` + manual
  `needsUpdate` on a cadence) is available if a future scene's real (non-RectAreaLight) shadow
  cost ever needs it.

## Status (2026-09-29, texture library / picker / filters pass)

Solo pass (parallel with a scene-performance agent working in stage-scene.js/
stage-render.js/stage-beams.js/stage-app.js - not touched here). Files
touched: `tools/stagelib/fetch_textures.py` (extended), new
`webaccess/res/stage-textures.js`, `stage-props.js`, `stage-propeditor.js`,
`stage-propeditor.css`, `stage-editor.js` (Properties/overrides only),
`webaccess/res/CMakeLists.txt`. Did NOT touch `club_scene.py`, `stage-scene.js`,
`stage-render.js`, `stage-beams.js`, `stage-app.js`.

- **Texture library, 37 slugs** (`fetch_textures.py`, unchanged 7 originals +
  30 new): CC0 only, colour/diffuse map only (1k JPG), a ~256px thumbnail per
  entry (Poly Haven's own `thumbnail_url`; ambientCG's `previewImage`
  `256-JPG-FFFFFF`), `index.json` v2 now carries `name`, `category`,
  `thumb`, `roughness`, `metalness` for EVERY entry (not just the new ones -
  the original 7 got backfilled too, same `map`/slug, no re-download needed).
  - **Wood** (6): `wood-panel` (existing, "Oak wood planks"), `wood-walnut`
    (american_walnut_veneer), `wood-dark-stained` (dark_wooden_planks),
    `wood-plywood` (plywood), `wood-weathered-planks` (old_wood_floor),
    `wood-parquet` (herringbone_parquet) - all Poly Haven.
  - **Metal** (6): `metal-rusty`/`metal-corrugated` (Poly Haven
    rusty_metal/corrugated_iron); `metal-brushed-steel` (ambientCG Metal009),
    `metal-aluminium` (Metal051A), `metal-diamond-plate` (DiamondPlate009),
    `metal-brass-copper` (Metal057A) - ambientCG has no real-world-size
    metadata, so these get a category-guessed `tileInchesX/Y` (24in) instead
    of a measured one.
  - **Stone** (7): `concrete` (existing); `stone-granite`/`stone-slate`/
    `stone-terrazzo`/`stone-plaster` (Poly Haven granite_tile/slate_floor/
    terrazzo_tiles/grey_plaster); `stone-marble-white`/`stone-marble-black`
    (ambientCG Marble021/Marble016).
  - **Fabric** (5): `fabric` (existing, relabelled "Linen fabric pattern");
    `fabric-denim` (Poly Haven denim_fabric); `fabric-carpet` (ambientCG
    Carpet001); `fabric-velvet-red`/`fabric-velvet-black` - see tint note
    below.
  - **Leather** (4, REQUIRED black+red both satisfied with REAL photos, no
    tint needed): `leather-black` (ambientCG Leather026 - a genuine black
    leather photo exists there), `leather-red` (Poly Haven leather_red_02),
    `leather-brown` (brown_leather), `leather-white` (leather_white).
  - **Tile** (3): `glossy-tile` (existing); `tile-checker` (Poly Haven
    checkered_pavement_tiles); `tile-subway` (ambientCG Tiles036, tagged
    "subway").
  - **Other** (3): `other-asphalt` (Poly Haven aerial_asphalt_01),
    `other-rubber` (rubber_tiles), `other-cork` (ambientCG Cork002).
  - **Brick** (3, existing, recategorized): `brick-red`/`brick-dark`/
    `brick-white`.
  - **Synthesized colour variant, documented method** (only case needed -
    velvet, not leather): no CC0 red/black VELVET photo exists on either
    source. `fabric-velvet-red` downloads Poly Haven's neutral
    `velour_velvet` ONCE and records `"tint":{"color":"#7a1020","amount":0.75}`
    in its index entry; `fabric-velvet-black` reuses the SAME downloaded
    `map`/`thumb` (no second download) with its own tint
    (`#0a0a0d`/0.88). `stage-textures.js` composes this baked-in tint UNDER
    any user-chosen filter tint (`composeFilters(tintAsFilters(entry.tint),
    userFilters)`) via the SAME canvas-tint mechanism as the Filters feature
    - the picker's thumbnail is also tinted live (small canvas, cheap) so it
    previews the right colour, not the neutral source photo.
  - Misses: none - every wanted slug resolved on the first run (log the
    script's own `MISS` lines if that ever changes).

- **`stage-textures.js`** (new module, shared by propeditor + editor):
  - `getLibraryIndex()`/`getTextureEntry(slug)`: fetch+cache `index.json`.
  - **Filters contract**: `{brightness, contrast, saturation, hue, tintColor,
    tintAmount, gloss}` (`DEFAULT_FILTERS`, `normalizeFilters`,
    `composeFilters(base, over)` - `over` wins ties, numeric knobs
    multiply/add, used for BOTH the "object-over-part" instance composition
    and to layer a library entry's own baked-in `tint` under a user filter).
    `applyColorFilters(hex, filters)` adjusts a FLAT colour (brightness/
    contrast/saturation done in RGB, hue via an HSL round-trip approximation,
    tint via linear RGB lerp) - used for untextured parts and as the
    no-shader fallback.
  - **Filter baking, per the hard rule (no new shader/uniform)**:
    `acquireTexture(slug, filters)` resolves a SHARED, refcounted, already-
    filtered base `THREE.Texture` - `bakeCache` keyed by `slug + rounded
    filter values`; the bake itself is a plain `THREE.Texture(img)` when
    filters are all-default (no canvas cost at all), else a
    `THREE.CanvasTexture` from `ctx.filter = "brightness() contrast()
    saturate() hue-rotate()"` + a `source-atop` tint composite - EXACTLY the
    hard-rule recipe. Callers `.clone()` this shared base per part instance
    (same pattern the old per-slug base-texture cache used) for independent
    `.repeat`/`.rotation`; the clone's `.dispose()` is wrapped to call
    `release()`, so three.js's OWN existing disposal (stage-scene.js's
    `disposeObject3D`: `m.map.dispose()`) is what actually drops the
    refcount and frees the bake once nothing uses it - **no new lifecycle
    hook was needed in any file outside this session's ownership.**
    Live-verified (CDP, see below): placing 2 identically-textured objects
    adds only ONE new GPU texture per distinct slug (shared), not one per
    instance; a new filter combo on ONE instance adds exactly the expected
    number of new bakes (not a full re-bake of everything).
  - `openTexturePicker(opts)`: a self-built popover (own DOM, `<dialog>`-like
    overlay, NOT reusing the "look gallery" popover) - category tabs (All +
    each `category` present in the library, alphabetical) + search + a lazy
    `<img loading=lazy>`/tinted-canvas thumbnail grid + a "None" tile.
    `onHover(slug|null)` fires on hover AND on click (click both previews
    and marks the selection); `onPick`/`onCancel` fire once, on OK/Cancel/
    Esc/the ✕ button. The CALLER owns the actual live-preview + revert-on-
    cancel logic (see contract below) - the picker itself never mutates
    anything.
  - `buildFilterControls(filters, onSet)`: one shared DOM widget (6 range/
    color rows + Reset) used verbatim by both the prop editor's part panel
    and the Properties panel's object/per-part "Look" rows.

- **Unified texture pipeline in `stage-props.js`** (real refactor, not just
  additive): the OLD `MATERIAL_TEXTURES`/`loadTextureIndex`/`loadBaseTexture`/
  `applyTiledTexture` (a second, separate texture cache/loader) is GONE,
  replaced by `MATERIAL_TEXTURE_SLUG` (matName -> slug, now just a name
  lookup) + one `applyTexture()` that goes through `stage-textures.js`'s
  `acquireTexture` - so a `part.material` preset (e.g. `"wood-panel"`) and a
  free-picked library texture (`part.texture`) share the exact same
  cache/loader/filter pipeline. `resolveEffectiveLook(part, instanceLook)`
  composes (part fields) < (instance override) into one `{texture, tileIn,
  rot, filters}` object per part, threaded through `createPartMesh(part,
  colorOverride, instanceLook)` -> `makeMaterial(matName, colorHex, side,
  part, look)`. A part's OWN `color`/`colorOverride` still tints the photo via
  three.js's native `map * color` (unchanged, pre-existing behaviour) -
  `look.filters` is a SEPARATE, additional effect (baked into the texture, or
  applied to the flat colour when there's no texture at all) - the two
  compose, they don't replace each other.
  - **New per-part fields** (all optional, undefined-safe everywhere):
    `part.texture` (library slug or null/undefined = none), `part.tileIn`
    (`[wIn, hIn]` - real-world tile size; defaults to the library entry's own
    `tileInchesX/Y` when unset), `part.textureRot` (0 or 90 - three.js
    `Texture.rotation`/`.center`, NOT baked into the canvas), `part.filters`
    (a `FilterSet`, see above).
  - Model/truss/ledstrip parts do NOT support `part.texture`/`filters` -
    deliberate, not an oversight: `createModelPartMesh`'s
    `template.clone(true)` shares materials (and their `.map`) BY REFERENCE
    across every instance/placement of that same glTF (confirmed from the
    materials-pass session's own notes) - mutating one instance's colour/map
    would corrupt every other copy of that model anywhere on the page. Doing
    this safely needs its own per-instance-material-clone pass with careful
    dispose auditing; out of scope this session, see "Not done" below.
  - **Perf audit fix, folded in mid-session per the scene-perf agent's
    request**: `makeMaterial`'s `side` defaulted to `THREE.DoubleSide`
    whenever a part's own `side` was falsy - and EVERY plain-authored part
    (STARTER_PROPS' `part()` helper, the prop editor's `defaultPartFor()`,
    and club_scene.py's own `box()` helper, which never sets `side` at all)
    hit that fallback, so effectively all non-wall furniture was double-sided
    (measured live: "matte 42/42, metal 12/12... all double-sided"). Changed
    the default to `THREE.FrontSide` for closed primitives (box/cylinder/
    sphere/cone/torus) in three places: the per-face box/cylinder/cone
    branch's `part.side || "both"` -> `|| "front"`; the sphere/torus branch,
    which previously **ignored `part.side` entirely** (hardcoded
    `THREE.DoubleSide` - a real pre-existing bug, now respects an explicit
    side too); `part()`'s and `defaultPartFor()`'s own baked-in default
    (`"both"` -> `"front"`). **Planes are untouched** (still default
    `"front"`, already correct, never double-sided by default anyway).
    **Walls are untouched and unaffected** - `club_scene.py`'s `wall_box()`
    always sets `part.side = "front"` EXPLICITLY already (never hits the
    now-changed fallback), so their one un-hidden "inner" face keeps
    rendering exactly as before, seen correctly from inside the room -
    verified this stays true by inspection (the fallback only ever applies
    when `part.side` is falsy, and explicit `"front"`/`"both"`/`"back"`
    always wins regardless of the default). The prop editor's "Visible from"
    selector (previously plane-only) now also appears for box/cylinder/
    sphere/cone/torus parts, so an operator can still switch a specific part
    back to `"both"` if it's genuinely meant to be seen from inside (e.g. an
    open-topped crate) - closed primitives have no other way to reach that
    now that the code-level default changed.
  - **Real bug found and fixed while wiring the refcounted bake in**: the
    prop editor's OWN local `disposeObject3D` (stage-propeditor.js, separate
    from stage-scene.js's) never disposed `material.map` at all - harmless
    before (nothing needed it released), but now that `rebuildPartsGroup()`
    reruns on EVERY single field edit and a textured part's map clone needs
    releasing to free its bake, this would have leaked one bake per edit
    while a textured part stayed open in the editor. Fixed - but SCOPED to
    only primitive parts (`node.userData.partId` truthy directly on the
    mesh - only true for primitive-shape meshes; a model/truss/ledstrip
    part's `partId` lives on its wrapper Group, never reached here) to avoid
    disposing a `model` part's SHARED-by-reference glTF texture, which would
    have corrupted every other instance of that model on the page.

- **Prop editor UI** (`stage-propeditor.js`): part panel gets a "Texture"
  swatch (thumbnail + name + "Change…" + "None"), tile-width/tile-height
  fields (ft-in, via `stage-units.js`, defaulting to the texture's own real
  size) + a Rotate 0°/90° select once a texture is set, and a "Filters"
  section (shared `buildFilterControls`) for EVERY primitive-shape part
  (works whether or not it has a texture, per the hard requirement). Hover-
  preview in the picker mutates `part.texture` directly and calls
  `rebuildPartsGroup()` (no history push); Cancel/Esc restores the pre-open
  value the same way; OK restores it too and THEN runs the real change
  through `commitChange` - undo sees one clean before/after transition, not
  the hover scrubbing.

- **Overrides contract, extended** (`objects[i].overrides`, stage-editor.js
  Properties panel "Look" section, below the existing "Instance" section -
  never touches the prop definition):
  ```
  overrides.filters = FilterSet         // object-wide, composed OVER every
                                         // part's own + per-part-look filters
  overrides.partLooks = {
    <partId>: { texture?, tileIn?, rot?, filters? }   // per-instance,
  }                                                     // per-part override
  ```
  Composition order (later wins): `part.filters` < `overrides.partLooks[id]
  .filters` < `overrides.filters` (computed once in `buildPropGroup` as
  `composeFilters(partLook.filters, objectFilters)`, then composed again
  under the part's own filters in `resolveEffectiveLook`) - i.e. genuinely
  "object-over-part" as specced. `partLooks[id].texture` follows the exact
  same "key presence = override" convention as `partColors` - an explicit
  `texture: null` legitimately means "no texture for this instance" even
  when the prop's own part has one (distinct from key-absence = "inherit").
  Same live-preview/revert pattern as the prop editor, but the "commit" path
  is `app.commitDraftChange` + a direct mutation of `app.stage.draft` (a live
  reference - confirmed by reading the existing `patchOverrides` pattern)
  and the "cheap redraw" path is `app.rebuildSceneFromDraft()` (exists,
  called by `commitDraftChange` internally, but ALSO safely callable directly
  for a no-history/no-dirty preview - confirmed from stage-app.js source).
  Per-part filters are in a collapsed `<details>` under each part's texture
  row (native disclosure, no extra JS/CSS needed) to keep the panel from
  becoming huge on a multi-part prop.

- **Live-verified via CDP (visible Chrome, real Intel UHD GPU)**, against the
  already-running `demo_show.py` dev instance on 9998 (never restarted):
  built a real "Test Walnut Table" (box top + cylinder shaft) through the
  ACTUAL prop editor UI (not a data injection) - assigned Walnut veneer to
  the top and Brushed steel to the shaft via the real picker, applied a
  Brightness filter, saved. Placed TWO instances via the real drag-from-
  library-row-to-viewport gesture (a genuine CDP gotcha found: the row's
  `getBoundingClientRect()` reports its true position even while SCROLLED
  OUT of the scrollable library list's visible area - hit-testing that point
  resolves to the scrolling container/aside, not the row, so the synthetic
  drag silently no-ops; fixed by `row.scrollIntoView({block:'center'})`
  before reading its rect - future CDP scripts driving ANY scrollable list
  row in this app should do the same). Gave one instance's shaft an
  override to Black leather (via the SAME picker, opened from the
  Properties panel) plus an object-wide Saturation/Hue filter.
  - `getRenderStats().programs` stayed at a constant **20** through every
    texture pick, instance placement, override and filter change - zero
    shader recompiles, the hard rule.
  - `getRenderStats().textures` grew exactly as the refcounted cache
    predicts: 21 -> 23 after placing 2 tables (2 NEW distinct textures -
    walnut, steel - shared across both instances, not doubled) -> 26 after
    the instance override + filters (leather's first unfiltered bake, then
    the 2 NEW filtered bakes once Saturation/Hue were dialled in on top of
    it - matches the exact sequence of user actions, not an uncontrolled
    balloon).
  - Zero console errors/exceptions across the whole session.
  - `window.__stage.status().dirty` went `false -> true` on the edits, and
    **no `*.stage.json` file was ever created on disk** (confirmed via a
    filesystem check) despite `dirty:true` persisting - draft edits never
    autosave, exactly per the contract. (This show had never been saved from
    `/stage` before, so there was no pre-existing mtime to compare against -
    file non-existence was the actual proof used.)
  - Direct engine-level check (`import('/stage-props.js').then(m =>
    m.createPartMesh(...))`) confirmed `material.map` is a fully-decoded,
    correctly-tiled texture on a synthetic textured part, independent of any
    UI - and a house-lights-on close-up screenshot of the placed table in the
    MAIN club scene shows clear, correct walnut wood grain
    (`textures-12-closeup-house-lights.png`). The prop editor's OWN
    full-part screenshots (`textures-04`/`-05`) look flat/dark despite the
    map being correctly assigned - that is its pre-existing, unrelated,
    dim 3-light neutral-studio rig (weak `HemisphereLight` + 2
    `DirectionalLight`s) under-exposing a photo texture at that specific
    camera angle, NOT a bug in this feature (verified by the direct
    engine-level check above showing the map genuinely applied, and by the
    same texture rendering with clear, correct detail under the main scene's
    brighter lighting).
  - Screenshots: `lightai/reports/stage/textures-00` through `-12`.

- **Not done / known limits**:
  - `part.texture`/`filters` on `model`/`truss`/`ledstrip` parts - see the
    shared-material-by-reference risk above; would need a real per-instance
    material-clone pass with careful dispose auditing.
  - `club_scene.py` showcase (VIP booth leather, a walnut/steel table in the
    actual club) was explicitly skipped: every VIP-booth sofa AND every table
    prop in `club_scene.py` is built from downloaded glTF `model` parts (not
    primitive box/cylinder parts), so the free-texture picker doesn't apply
    to them without the model-material work above - forcing it in would have
    meant hacking around the shared-material-reference hazard just for a
    demo. The "walnut top + steel shaft" table validation instead happened
    live via the real prop editor + real placement in the running scene (see
    above), which is the actual feature under test.
  - The CSS hue-rotate for a FLAT colour (`applyColorFilters`) is an HSL
    round-trip approximation, not a pixel-match of the canvas `ctx.filter`
    matrix used for photos - fine for a "tint/dim a swatch" control, would
    read as a slightly different result if directly diffed against the
    photo path at the same slider value.
  - No automated test coverage added (`stage_check.py` untouched) - this
    session's verification was the CDP scripts described above (not kept as
    permanent files; they lived in the session scratchpad).

## Status (2026-09-29, materials/bounce/glow/reflections pass)

Solo pass. Files touched: `stage-props.js`, `stage-propeditor.js` (no code change needed - its
material dropdown already reads `MATERIALS` dynamically), `stage-scene.js`, `stage-render.js`,
`stage-beams.js`, `stage-ledstrip.js` (small targeted fix, see below - outside the original file
list but the operator asked for it directly), `club_scene.py`, new `fetch_textures.py`.

- **New material presets** (`stage-props.js` `makeMaterial`/`MATERIALS`): `paint-matte`,
  `paint-satin`, `black-acoustic-foam`, `concrete`, `brick-red`, `brick-dark`, `brick-white`,
  `wood-panel`, `glossy-tile`, `fabric`, `mirror` - real-ish PBR roughness/metalness values, `side`
  handled the existing correct way (never `side || X`, `THREE.FrontSide` is 0). Textured presets
  (`MATERIAL_TEXTURES` table: concrete/brick-*/wood-panel/glossy-tile/fabric) load a shared,
  cached base `THREE.Texture` per slug from `/stage-lib/textures/index.json`
  (`fetch_textures.py`), `.clone()` it per part instance and size `.repeat` from the part's own
  size - `paint-*`/`black-acoustic-foam`/`mirror` stay flat PBR colour on purpose (no useful
  photo texture for those; mirror needs an envMap, not a diffuse map).
  - **Brick scale (operator-verified)**: `fetch_textures.py`'s `real_size_inches()` reads Poly
    Haven's `/info/<id>` `dimensions` field (mm) for each fetched texture and stores
    `tileInchesX/Y` in `index.json` - the material's `.repeat` divides the part's real size by
    THAT (not a guessed constant), so bricks render at the photo's own true real-world scale on
    a wall of any size. Fetched: `brick-red`=large_red_bricks (2000mm/78.74in tile),
    `brick-dark`=painted_brick (1800mm/70.87in), `brick-white`=white_bricks (1500mm/59.06in),
    `concrete`=concrete_wall_001 (59.06in), `wood-panel`=oak_wood_planks (47.24in),
    `glossy-tile`=blue_floor_tiles_01 (78.74in), `fabric`=fabric_pattern_05 (19.69in, via a
    `col_01`/`col_02`/... key match - Poly Haven's multi-colourway sets don't use the classic
    "Diffuse" key name). Verified live with close-up screenshots
    (`lightai/reports/stage/brick-closeup-1.png`/`-2.png`, real GPU, house lights on) - bricks
    read as normal, proportionate coursing next to a ~29.5in bar stool and ~27in sofa arm, not
    oversized/undersized. Did NOT do an exact per-photo pixel brick count (no crop/zoom tool in
    this session) - the scale claim rests on Poly Haven's own real-world-size metadata (the
    method the operator suggested as acceptable) plus this qualitative visual check.
  - **Real bug fixed while wiring this up**: box parts with per-face materials (walls, via
    `hiddenFaces`) were sizing every face's texture repeat from `part.size[0]/[2]` regardless of
    which face it was - correct for front/back faces but WRONG for right/left (needs
    depth,height) and top/bottom (needs width,depth). A 6in-thick x 480in-long side wall got a
    repeat computed from its 6in thickness, not its 480in visible face. Fixed in
    `createPartMesh`: a `FACE_PLANE_SIZE_BOX` table picks the correct in-plane (w,h) pair per
    face id before calling `makeMaterial`. Hidden faces now also skip `makeMaterial`/texture
    loading entirely (previously built then discarded) - a wall has 5 hidden faces per 1 visible.
- **Material-aware bounce** (goal 2, `stage-beams.js` `estimateSurfaceBounce`/`fitBounceLight`,
  `stage-scene.js` `computeBeamLength`): the existing throttled (~5-10Hz) `objectsGroup` raycast
  that shortens a beam's length now also captures the hit mesh's real material (plus the default
  room's plain "roomFloor" mesh as an extra raycast target, for the non-club-scene fallback room).
  `estimateSurfaceBounce(material)` -> `{color, albedo}` from the material's own colour lightness
  x a roughness/metalness-derived scale (metal+low-roughness "mirror" bucket scales up ~1.3x,
  glossy ~1.05x, matte/satin/foam/concrete/etc. scale with `0.55+0.35*(1-roughness)`) - no
  per-preset-name special-casing needed, it falls out of the real values. `fitBounceLight` tints
  the bounce PointLight by `beamColor * surfaceColor` and scales by `albedo` instead of a fixed
  0.5. Fixed light slot counts untouched.
- **Room glow** (goal 3, cheap GI approximation, `stage-scene.js`): one new, always-present
  `THREE.AmbientLight` (`roomGlow`) - never added/removed at runtime (fixed count), `.visible`
  only toggled on render-MODE change (same pattern as `hemi`/`dir`), intensity/colour smoothed
  toward a target over ~0.3s. Target = sum of lit beams' colour*dimmer, weighted by
  `avgRoomAlbedo` (computed once per `rebuildObjects()` from every real object material's own
  `estimateSurfaceBounce().albedo` - not per frame). Completely independent of
  `houseLightsOn`/`setHouseLights` (untouched, per the contract) - the room glow's OWN target
  goes to exactly 0 when every fixture is dark, regardless of house lights state.
  - **Two real bugs found and fixed via live screenshots, not just code review**:
    1. The target's scale used `currentRenderSettings.bounce.intensity`, which is exactly **0**
       at the Low preset (a knob for the real bounce POINT LIGHTS' per-quality expense) - that
       made the glow permanently zero at Low (the DEFAULT quality on page load), i.e. "bright
       look fills the room" silently never worked out of the box. Changed to a fixed scale
       (`gW * avgRoomAlbedo * 0.5`), independent of quality.
    2. **The smoothing froze instead of decaying to zero**: it lived inside `updateBeamLighting`,
       which only runs when `updateFixtureStates` gets fresh DMX - once a function is stopped and
       every channel holds steady at 0, that stops firing entirely, so the exponential decay
       toward the (now-zero) target never finished; the room stayed lit at whatever brightness it
       had the instant DMX hit 0. Caught by an explicit operator-requested test (stop function 45
       via `QLC+API|setFunctionStatus|45|0` over the WS, screenshot). Fixed by splitting the
       target computation (still in `updateBeamLighting`, DMX-driven) from the actual smoothing
       (new `updateRoomGlow()`, called once per rendered FRAME from `loop()`, real wall-clock
       `dt`) - now correctly fades all the way to a fully black room within ~1s of DMX going to
       0, verified live (`lighting-dmx0.png` is solid black; `lighting-foh.png` moments earlier
       with the same function running shows the brick room clearly lit by glow).
- **Reflections** (goal 4, `stage-render.js` schema/presets, `stage-scene.js`): new
  `reflections.cube` setting (0/128/256), a single low-res `THREE.CubeCamera` near the room
  centre (`roomCenterWorld()`, re-centred on `setRoom`), refreshed at <=4Hz from the main loop,
  applied as `envMap` only to materials tagged `.userData.reflective` (gloss/metal/mirror/
  glossy-tile - tagged in `makeMaterial`). `stage-props.js` exports `setEnvMapSource`/
  `setAssetReadyHook`; `stage-scene.js` registers both once. Toggling the setting disposes/
  rebuilds the cube RT and re-applies envMap to every existing reflective material, then one
  `requestWarmup()` - by design the only time this recompiles.
  - **Confirmed live (regressed Medium to a 1s+ freeze) that turning this on for a scene this
    size is expensive** - re-applying envMap to every reflective material in the club at once
    (floor, bar top, mirror, brass trim, DJ gloss panel...) plus the 6-face-per-refresh cube
    camera cost was too much for the operator's Intel UHD laptop at Medium. **Kept `cube: 0`
    (off) on BOTH Low and Medium presets** (spec asked for 128 on Medium; overridden for the
    30fps hard gate) - only High (128) and Ultra (256) enable it by default. Still available as
    a manual Settings > Graphics option at Medium if the operator wants to try it.
- **Club scene** (`club_scene.py`, operator directed mid-session - walls are real brick, not
  charcoal paint): all 4 walls -> `brick-red` (neutral near-white part colour so the photo's own
  colouring isn't multiplied/tinted); ceiling -> `black-acoustic-foam`; new `club-dj-foam-panel`
  object (thin foam panel mounted proud of the now-brick back wall behind the DJ booth, keeping
  the "foam behind the DJ booth" ask); bar counter gets a `wood-panel` crowd-facing fascia box (in
  addition to its existing gloss top); dance floor -> `glossy-tile` (was `gloss`); back-bar
  cabinet gets a `mirror` strip box above the bottle shelf. `python tools/stagelib/club_scene.py`
  validates clean (36/36 fixtures, 24 propDefs, 0 problems).
- **LED strips default OFF** (operator ask, mid-session): static decor LED strip PROPS aren't
  DMX-bound, so a nonzero default brightness broke "completely dark club at DMX 0". Changed the
  default to 0 in 3 places: `stage-props.js`'s `ledstripPart()` helper and its prop-editor
  "add ledstrip part" template, and every `led_part(...)` call in `club_scene.py` (back-bar purple
  glow, bar-lip blue strip, DJ console pink strip). Also fixed a real bug in `stage-ledstrip.js`
  (outside this session's original file list, edited directly per operator instruction): the LED
  dot material had a hardcoded `0.3 +` emissive-intensity floor, so even `dimmer=0` still glowed
  dimly - changed to a pure `dimmer * 2.0` (same max at dimmer=1) so brightness 0 is genuinely
  unlit. Verified: `lighting-dmx0.png` (function 45 stopped) is solid black.
- **Live-GPU testing** (`live_drive.py`, `tools/stagelib/shots*.py` one-off scripts in this
  session, not kept): **Low holds a rock-solid 60fps** across every run (6 consecutive full
  passes today, zero variance beyond +/-0.5fps) with no hitches/freezes. **Medium does NOT meet
  the 30fps gate** in this session's testing - consistently ~21-33fps (worst frame 50-70ms, no
  outright FREEZE lines) across 6 back-to-back runs, remarkably reproducible run-to-run.
  - **Ruled out, with direct controlled A/B tests** (temporarily disabling a code path, redeploying,
    re-running `live_drive.py`, then restoring): (1) the new textured materials (`material.map`
    never assigned) - no change in fps. (2) the room glow ambient light (`roomGlow.intensity`
    forced to 0 every frame) - no change in fps. (3) `reflections.cube` was already 0 at Medium
    by then (see above) - ruled out separately by the freeze it caused when it WAS on. So none of
    this session's 3 GPU-facing additions (textures, room glow, cube reflections) are the cause
    of Medium's shortfall.
  - `getRenderStats()` draw calls roughly DOUBLE at Medium vs Low (e.g. ~200 -> ~400) regardless
    of the above toggles, consistent with Medium's PRE-EXISTING `beams.quality`/
    `beams.volumetric:true` turning on the depth pre-pass (`stage-render.js`
    `renderDepthPrepass()`, wave-1 code, not touched this session) - a full second scene
    submission every frame. That architecture already existed when this file's own "Medium holds
    57-120 FPS" claim (see "Performance..." section below) was written; this session could not
    determine whether the scene has simply grown heavier since (club_scene.py's furniture/model
    count, wave 3's spotlight intensity bump, etc.) or something else is different now - a git
    diff isn't possible for that comparison (these stage-*.js files have never been committed on
    this branch, confirmed via `git log`/`git status` showing them all `??` untracked, so there is
    no tracked prior revision to `git checkout` for a clean before/after).
  - **Next session, if this needs to be resolved**: re-run `live_drive.py` from a COLD boot
    (this session's numbers came after ~30-40 min of continuous heavy GPU testing - thermal/power
    throttling on an integrated-GPU laptop was not ruled out, only shown to be very
    run-to-run-consistent, which is also what a settled throttled steady-state looks like); if
    still slow cold, treat `beams.volumetric`/the depth pre-pass cost at Medium as the actual
    target (not this session's material/lighting work, which is cleared by the A/B tests above).
- **Not done / known limits**: no normal/roughness maps applied anywhere (by design, goal 1);
  `fabric` preset is fetched but not used by `club_scene.py` (available for the operator/prop
  editor); the mirror/glossy-tile/metal envMap path only gets real content when `reflections.cube`
  is manually turned on (off by default at Low/Medium); cylinder/cone-shaped parts' texture
  tiling still uses the same (possibly wrong) w/h convention as before this session (only box
  parts' per-face sizing was audited/fixed - no textured preset in `club_scene.py` is currently
  applied to a cylinder/cone part, so this is untested, not unfixed-and-in-use).

## Status (2026-09-29, overnight wave 3 - seats/perf/light polish)

Solo pass (only agent running), token-capped. Files touched: `tools/stagelib/club_scene.py`,
`webaccess/res/stage-app.js` (computeSeats/seatCameraState area only), `webaccess/res/stage-scene.js`,
`webaccess/res/stage-beams.js`.

- **Seats, rewritten (`computeSeats()`, stage-app.js)**:
  - Root cause of *every* seat-placement bug below was confirmed live (seatList() + screenshots),
    not guessed: the old code derived seats geometrically (rings/spacing) instead of from the
    actually-placed stool objects, and both `computeSeats` and `club_scene.py`'s own object list
    had the back-bar cabinet tagged category **`bar`** (the OBJECT's `category` field is what
    `computeSeats` reads - the propDef's own category is separate/cosmetic and not what matters here),
    so it spawned its own 4 "stools" alongside the real bar's 14.
  - `club_scene.py`: back-bar cabinet's object-list category (and its propDef's) changed `bar` ->
    `decor`. Bar seats now come only from the counter.
  - New `obj.parent` field (7th OBJECTS tuple element, optional): cocktail/bar stools now carry an
    explicit parent anchor NAME. Needed because nearest-object-by-distance is genuinely ambiguous
    in this floor plan - a cocktail stool at [230,402] is only 6in from the bar counter's box and
    14in from its own table's centre, so pure proximity silently reassigned stools to the wrong
    furniture (confirmed live). `computeSeats` prefers `stool.parent` when present, falls back to
    nearest table/bar anchor otherwise (back-compat for scenes without the field).
  - Table/bar seats are now placed AT the real stool object's own position (not a re-derived
    ring/spacing), eye height 58" if the table's own height (from its propDef parts) is >=26"
    (`propHeightIn()`, new helper) else 44" - cocktail tables (30" tall) correctly read as standing.
  - Booth (VIP sofa) seats were the worst bug: a full 360 deg ring around the sofa's centre put one
    of 3 seats **outside the room** (`x=-9.75` against a wall starting at `x=3`, confirmed live) and
    another pressed into the sofa's own backrest/the wall behind it, regardless of the sofa's
    rot/yaw. Rewritten to offset toward `lookAt` (the dance floor) only, inset within the sofa's own
    footprint (on the cushion, not beyond the couch), spread along the perpendicular axis - always
    on the open/room-facing side by construction, no rotation bookkeeping needed.
  - DJ seat moved from dead-centre-of-the-console (eye height was fine, but the seat sat right on
    top of the console box) to just behind it (away from the dance floor, `stepBack` derived from
    the console's own footprint radius) - "standing behind the decks looking at the crowd".
  - "Dance floor centre" seat was staring straight down: `lookAt` and `pos` were the *same point*
    in X/Y (confirmed live: both `[288,222,0]`), so pan/tilt from eye to target was purely vertical.
    Now looks toward the DJ/stage object instead.
  - New `clampSeatToRoomXY()` safety net (14in wall margin, applied to every seat) so a
    mis-derived seat can never end up outside the shell or pressed into it, even in a future/generic
    authored scene that doesn't have this exact geometry.
  - Verified via `window.__stage.seatList()` + `goSeat()` + screenshots (`unlit`/`wireframe`/`lit` A/B,
    not just eyeballing `lit`) from a table, booth, bar and DJ seat - all read as correct interior
    club vantage points now. Did NOT add a numeric raycast-clearance test hook (out of budget) -
    verification was analytic (position math + margins) plus visual (screenshots), not automated.
- **Performance**: static prop geometry only (did not touch fixture bodies/beams/LED strips -
  those dominate draw calls and weren't in scope). `getRenderStats()` triangle counts are **very
  noisy run-to-run in this headless harness** (seen 166k-1.08M "at Low" across otherwise-identical
  reloads, `lights`/`beams` counts constant) - looks like beam geometry segment count depends on
  which quality preset was active at the exact moment each fixture's beam mesh was first built
  (a race against the page's own default-quality init, not something these edits touch or fix) -
  so "before/after getRenderStats()" is not a fair before/after for a specific prop swap. Used
  `stage-lib/props/index.json`'s own `tris` field instead, which is deterministic:
  - `club-potted-plant`: `potted_plant_02` (69,806 tris) -> `potted_plant_04` (8,929 tris). This
    ONE decor prop, x4 instances, was ~40% of the whole scene's static geometry budget. Saves
    4x60,877 = **243,508 triangles**.
  - `club-vip-table`: `modern_coffee_table_02` (13,645) -> `modern_coffee_table_01` (4,504), x4 =
    saves **36,564 triangles**.
  - `club-cocktail-stool`: `metal_stool_01` (8,334) -> `metal_stool_02` (6,532), x8 = saves
    **14,416 triangles**.
  - Total: **-294,488 triangles** of static prop geometry (deterministic, from index.json), no
    footprint/size changes (models are force-fit to the same `size` box either way).
  - Did NOT touch draw calls: confirmed from three.js's own `Mesh.copy()`/`Object3D.clone()`
    semantics that `template.clone(true)` in `stage-props.js`'s `createModelPartMesh` already
    shares `geometry`/`material` BY REFERENCE across every instance of the same glTF model (clone
    copies the reference, not the buffer) - the "share geometry/materials" ask was already
    satisfied structurally, nothing to change there. True `InstancedMesh` conversion (collapsing
    N draw calls per repeated model down to 1) was NOT attempted - real win but a genuine
    restructure of `stage-props.js`'s per-object build path (would need a cross-object pass to
    group same-propDef/no-override objects before building), judged too risky for the remaining
    budget. **Next agent**: this is the highest-value remaining performance lever if more budget
    opens up - stools (14 instances total across 2 models), VIP tables (4) and pendant lamps (3)
    are the best InstancedMesh candidates (repeated, rarely have per-instance overrides).
  - Frustum culling: already on by default for every prop/fixture mesh (three.js default,
    untouched). Intentionally OFF only for beam shell/core meshes (`stage-beams.js`, documented
    inline: geometry is rewritten every frame without a bounding-sphere recompute) - capped at 32
    pooled lights/beams, not a real cost.
- **Visible light (beams' floor pools)**: root cause confirmed by the numbers, not guesswork -
  `fitSpotLightToBeam` (stage-beams.js) set `SpotLight.intensity` to `dimmer * ~8`. three.js r170
  SpotLights are physically-based (`intensity` = candela, `decay=2` = real inverse-square) and this
  room's truss height is ~4.4m above the floor, so the old default gave a floor-pool irradiance of
  roughly `8/4.4^2 =~ 0.4` - invisible next to the hemi(2.2)/directional(1.0) house light. Bumped
  the default (and the one call site in `stage-scene.js`) to **220 candela**, landing in the
  "hundreds" range the operator flagged; verified LIVE via screenshot at the Medium preset from a
  seat (`lightai/reports/stage/probe-light-medium2.png`, not kept as a final deliverable) - clear
  bright bloomed pools on the dance floor, room/walls/furniture still readable, "dark but not
  black". Did NOT touch the bounce PointLights (`fitBounceLight`) - they sit ~5cm above their hit
  point, so even a modest intensity is already very bright there; no evidence they needed it.
  - **Gotcha for next session**: at the Medium+ preset on this headless SwiftShader setup, a
    single frame can take 10-17s (documented elsewhere in this file already) - a screenshot taken
    only ~4s after `setRenderMode`/`applyQualityPreset('medium')` can capture a **solid black
    canvas that never got its first completed frame**, which looks exactly like a real "seat view
    is black" rendering bug but isn't. Confirmed by direct A/B: the exact same seat/preset with a
    proper 20-25s settle rendered correctly. If a future session sees an all-black Medium/High
    screenshot, increase the settle time before assuming it's a real bug again.
- **Final verification screenshots**: `lightai/reports/stage/final-seat-{table,booth,bar}.png`,
  Low preset, show (function 45) running, taken after `demo_show.py start --club-layout --fresh`.
  All three read as a real interior club vantage point (dark-but-visible room, furniture, beams).
- **Not done / next steps**: InstancedMesh for repeated props (see above); the triangle-count
  measurement race noted above (which preset's beam segment count "wins" at fixture-build time)
  is itself worth a real fix someday but is unrelated to this session's changes; no automated
  seat-clearance raycast test was added to `stage_check.py` (would be check 27+).

## Status (2026-09-29, overnight wave 2 results)

Solo pass (only agent running): club_scene.py integration into stage_check.py
(`--stage-club`), new checks 19-26, and fixes. Last full run: **26/27 green**
(only 26 "performance" flaky - see below, informational anyway). Files
touched: `stage-app.js`, `stage-props.js`, `stage-scene.js`, `stage-render.js`,
`tools/stagelib/stage_check.py`, `webaccess/res/CMakeLists.txt`.

- **Harness**: `--stage-club` writes `club_scene.build_stage(project)` beside
  the sanitized copy before QLC+ starts. New checks 19 (club scene load/
  `assetsPending()`), 20 (seats), 21 (render settings), 22 (beam-to-floor),
  23 (ADJ VPar beams), 24 (prop overrides), 25 (LED strips), 26 (perf,
  WARN-only). They run BEFORE check 5 (which overwrites the whole
  stage.json with a 1-fixture/0-object payload) and BEFORE check 7 (project
  reload); 14/22 (Simple Desk-dependent) run before 21/26 (which leave
  "ultra" quality active and can drop this 54-object scene to multi-second
  software-GL frames - queuing a CDP call behind one of those cascades into
  a timeout). 21/26 now clear `localStorage['qlcplus-stage-graphics']`
  afterward so "ultra" doesn't leak into later checks' fresh navigates.
  `CDPClient.navigate/evaluate/send` timeouts bumped (40-75s) and
  `screenshot()` takes its own 60s default - this scene is legitimately slow
  to load/render under headless SwiftShader.
- **New window.__stage hooks** (stage-app.js): `assetsPending()`,
  `getRenderStats/getRenderMode/setRenderMode/getRenderSettings/
  setRenderSettings`, `qualityPresetNames/applyQualityPreset`, `beamCount`,
  `setObjectOverride/getPropDef/editPropDef`, `ledStrips/ledStripState/
  setLedStripColor`, `setLabelsVisible`. `pendingLooks()` added to the scene
  API (stage-scene.js) and `pendingAssets()` to stage-props.js (tracks
  in-flight model/ledstrip loads) - both feed `assetsPending()`.
- **Real bugs found and fixed**:
  - ledstrip instance colour overrides were applied to the placeholder bar
    but silently dropped once the real `buildLedStrip()` loaded in
    (stage-props.js).
  - Seats: default labels are now hidden while at a seat (`wireSeats()`
    tracks the operator's own Labels-toggle preference and restores it on
    "Free camera") - was cluttering every seat screenshot.
  - **FOH/stage camera presets placed the camera OUTSIDE the room, behind a
    solid (opaque, two-sided) wall** (`CAMERA_PRESETS.foh`/`.stage` in
    stage-scene.js used `depth*1.05`/`depth*0.95` from room centre, which
    only stayed inside a room with thin/no walls; club_scene.py's real
    walls exposed it). This is why FOH screenshots were solid black/grey
    even at extreme exposure - confirmed via `setRenderMode('unlit')`
    (bypasses all lighting/post) still showing nothing but labels+grid.
    **Not yet fixed in code** - see known issues.
  - Work light raised (`QUALITY_PRESETS.*.workLight` 0.15/0.1/0.08/0.05 ->
    0.24/0.2/0.16/0.12, hemisphere ground colour/intensity bumped) per
    operator ask, but this did NOT fix the black screenshots - the camera-
    outside-a-wall bug above is the real cause for FOH; **seat views (e.g.
    the DJ seat, which IS inside the room) were still black too and the
    root cause for those specifically was not isolated before the token
    budget ran out** - needs its own `setRenderMode('unlit')` A/B test from
    a seat position (not just FOH) to confirm/rule out the same class of
    bug (e.g. camera embedded in a prop's own mesh, or a separate lighting
    issue).
- **Known issues / next steps**:
  1. Fix `CAMERA_PRESETS.foh`/`.stage` (stage-scene.js) to sit INSIDE the
     room (e.g. `cz - d*0.42` / `cz + d*0.42` style, near the front/back
     wall but not through it) instead of `cz + d*1.05` / `cz - d*0.95`.
     `top`'s camera is also above the `club-ceiling` box object - check it
     isn't looking through/at the ceiling's topside.
  2. Diagnose the seat-view blackness independently of the FOH bug (see
     above) - do the `unlit` A/B test from an actual seat.
  3. Check 26 (performance) is flaky/WARN-worthy: applying "ultra" on this
     scene under software GL can make even the harness's own `navigate()`/
     `evaluate()` time out (documented, not something to chase further).
  4. lightai/reports/stage/club-*.png from the last full run are STALE/
     WRONG (captured before the camera and labels fixes landed, or from the
     black-screen bug above) - re-shoot once the camera fix lands.
  5. No PMREM/RoomEnvironment set for the glTF models (operator suggestion,
     "helps PBR models read") - not implemented, pure ambient/hemi lighting
     only.
- **Deploy note**: `webaccess/res/CMakeLists.txt` WEBFILES_CLASSIC now also
  lists `stage-camera.js`, `stage-propeditor.js/.css`, `stage-truss.js`
  (previously missing from the install list). `C:\qlcplus-dev\Web\` mirrors
  the repo's `webaccess/res/*.js/.css/.html` as of this session's end.

## Status (2026-09-28, overnight build wave 1 - U's pass)

U's files only (stage-editor.js, stage-app.js, stage-camera.js, stage.html,
stage.css), tested live against a static server on port 8913 with headless
Edge + CDP (`Input.dispatchMouseEvent`/`dispatchKeyEvent`), both against the
source tree and the deployed `C:\qlcplus-dev\Web` copy (with R's and P's
real files, not stubs) - zero console errors/exceptions in the final runs.

- **Rotate/scale bug, root-caused and fixed (stage-editor.js)** - two
  distinct bugs, found by live-reproducing with synthetic mouse/keyboard
  events rather than static reading:
  1. **Modifier-hold (Shift/Ctrl) direct manipulation**: `beginModifierHold()`
     called `app.scene.transform.setMode(activeModifier)`, which left the
     REAL TransformControls gizmo interactive at the selected item's pivot -
     exactly where a modifier-hold drag naturally starts. Its own
     `pointerdown` handler (registered on the same canvas) could steal the
     press before our custom code ran: grabbing its uniform-scale centre
     handle with a down-point essentially on the pivot makes its
     distance-ratio scale factor blow up (confirmed: an ordinary drag
     produced a scale of 2.6 billion); a rotate-ring grab could likewise
     yield ~0 rotation. Fix: `app.scene.transform.enabled = false` for the
     duration of a modifier hold (restored on release) - the gizmo stays
     visible (shows the active mode) but stops intercepting pointer events,
     so only our own pointerdown/move/up math runs.
  2. **Wrong-target reselection mid-drag**: `startManipulation()` was always
     passed `pointerDownHit` (whatever `scene.pick()` hit at mousedown), and
     would silently `selectOnly()` that hit if it differed from the current
     selection - even while a modifier was held. In the demo layout a click
     at an object's exact screen pivot often lands on a FIXTURE standing
     behind/near it; scale then aborts with a "fixtures keep their real
     size" toast, invisibly abandoning the intended object. Fix: while a
     modifier is held, pass `null` instead (operate on the current
     selection only) - only the plain unmodified click-and-drag-to-grab path
     still uses the hit to pick a target.
  - Backstop kept regardless: `app.scene.transform.size = 1.4` (was the
    three.js default of 1, so handles now sit a bit further from the pivot)
    plus a hard `clampScale()` (0.05-20) applied at every scale commit/live
    read site (native gizmo AND modifier-hold) - TransformControls' scale
    handle's distance-ratio math can still spike given an unlucky grab
    point/camera angle; this guarantees the draft can never end up with a
    broken (invisible or room-sized) object even then.
  - New test hooks added for this (harmless, kept): `window.__stage.select
    (kind, id)` (selects exactly like a click, for driving gizmo/manipulate
    tests without pixel-perfect coordinates), `screenPosOf(kind, id)`
    (camera-matrix projection for ANY item, not just fixtures).
- **Camera horizontal/vertical scale settings removed entirely** (stage.html/
  stage-app.js) - FOV kept. (`app.scene.setViewSettings({sx,sy})` still
  exists on R's side; nothing calls it anymore.)
- **Graphics settings tab** (stage.html/stage-app.js): a "Graphics" tab next
  to "View" in the settings popup, built generically from R's
  `stage-render.js` `RENDER_SETTINGS_SCHEMA`/`QUALITY_PRESETS` (dynamic
  `import()`, guarded - the tab silently no-ops if that module or
  `scene.setRenderSettings` isn't present), grouped by the schema's `group`
  field, persisted to `localStorage` (`qlcplus-stage-graphics`), with
  Low/Medium/High/Ultra/Custom preset buttons (Custom is an indicator only,
  highlighted when the live settings don't exactly match a preset). A perf
  HUD (top-right, toggle via the topbar icon or the settings checkbox, fed
  by `scene.getRenderStats()` at ~4Hz) and an "Auto performance" toggle
  (steps the quality preset down one level if FPS stays under 85% of
  `targetFps` for 5 continuous seconds). A render-mode `<select>`
  (Lit/Beams only/Unlit/Wireframe) in the top bar calls `scene.setRenderMode`.
- **Seats** (stage-app.js, new `computeSeats()`/`wireSeats()`): derives
  camera bookmarks from `stage.draft.objects` by category - table -> 4 seats
  in a ring around it (radius from the prop def's own bounding box via
  `propDefs`, plus clearance), booth -> 3, bar -> one stool per ~26" of
  front width, dj -> one standing spot at the object; plus one standing
  "Dance floor" spot from an object named/aliased "dance floor centre" (or
  any `floor_area`/"dance floor"-named object). Eye height 44"/66" per the
  contract; each seat looks at the "dance floor centre" object if one
  exists, else the anchor. A `<select id="seat-select">` in the top bar plus
  `[`/`]` (prev/next) and `F` (free camera) hotkeys; a "Tour" button cycles
  seats every N seconds (a Settings > View slider, persisted). Seat changes
  animate over ~0.8s (`requestAnimationFrame` + `easeInOutCubic`) and push
  ONE camera-history entry per change (via the existing `pushCameraHistory`,
  so Ctrl+Z/Ctrl+Y cover seat moves like any other camera move).
  `window.__stage.seatList()`/`goSeat(i)` added.
- **Game-UI polish** (stage.html/stage.css/stage-app.js): translucent
  blurred dark panels (`backdrop-filter`) on the new HUD elements and
  existing popups/menus; a keybinding help overlay (`?`/`H`, close via
  button/outside-click/Escape); photo mode (`Tab` or a topbar button toggles
  `body.photo-mode`, which hides topbar/side panels/HUD/toasts via CSS);
  a screenshot button (`renderer.domElement.toDataURL("image/png")` -> a
  download link); a fullscreen button; a top-down minimap (2D canvas,
  bottom-right, room outline + fixtures (cyan dots) + objects (amber
  squares) + camera position/view-cone (green), click to teleport
  horizontally); a crosshair shown only while `stage-camera.js`'s new
  `isMouselooking()` (added this session - the middle-drag gesture flag)
  is true.
- **Prop instance overrides** (stage-editor.js Properties panel, objects
  only, single-selection): an "Instance" section - a colour-override swatch,
  one colour swatch per part when the prop has more than one part, a "Hide"
  checkbox, and "Reset overrides" - writing only `objects[i].overrides =
  {color?, partColors?, visible?}` (never the prop definition), per the
  contract P's `stage-props.js` (`buildPropGroup`/`applyOverrides`) and R's
  `stage-scene.js` (`rebuildObjects`) both already read/render this shape.
  An "Edit prop..." button dynamic-`import()`s `stage-propeditor.js`
  (guarded) and calls `openPropEditor(app, entry.prop)`; a
  `<link rel="stylesheet" href="stage-propeditor.css">` was added to
  stage.html once P's files landed. Also deleted the old in-panel prop
  parts-builder markup from stage.html's Props section (name/category/
  aliases fields, parts list, add-part, place/save buttons) per P's
  request - editing now lives entirely in `openPropEditor()`; `stage-props.
  js` already guarded/hid these elements at runtime, so deleting the
  markup is safe.
- **Gotcha for next session**: headless Edge processes launched for CDP
  testing are NOT reliably killed by terminating the Python launcher
  process (`proc.terminate()`/`taskkill` on the launcher PID only) - they
  accumulate as orphaned `msedge.exe` processes across repeated test runs
  in the same session, which then causes bizarre, hard-to-diagnose test
  flakiness (a "fresh" `--user-data-dir`/`--remote-debugging-port` pairing
  can end up talking to a stale, already-navigated-once browser instance
  from many runs ago, minutes-late boot warmup on a truly cold profile
  dir, etc.) that looks like an app bug but isn't. If a CDP test run
  behaves inexplicably (e.g. `window.__stage` briefly undefined right after
  a navigate that reports `readyState: "complete"`), first run
  `taskkill //IM msedge.exe //F` and retry with a deleted `--user-data-dir`
  before suspecting the page code. A plain fixed `await asyncio.sleep(3.0)`
  after `navigate()` proved more reliable in this session than a tight
  polling loop for `window.__stage` on a brand-new profile dir.

## Performance on the operator's laptop (Intel UHD, ANGLE/D3D11): hard-won rules (2026-09-29)

**Result:** Medium holds 57-120 FPS (vsync) with no freezes from a cold start. Measure with
`tools/stagelib/live_drive.py`, which drives a VISIBLE Chrome on the real GPU. Headless SwiftShader
numbers mean nothing for freezes.

**Freeze causes and their fixes** (keep all of them):
1. **Changing the number of visible lights or shadow casters recompiles EVERY material** (seconds each on
   Intel). Lights live in fixed slot buckets (spots 8/16/32, bounce 6/16/32, shadow casters 2/4/8).
   Budgets only change `intensity` and `shadow.intensity`. Never toggle `.visible`/`.castShadow` per frame.
   Low and Medium share the same buckets.
2. **Shaders compile on first draw.** The page warms them up instead:
   - `requestWarmup()` pauses drawing immediately, then `renderer.compile()` runs and
     `KHR_parallel_shader_compile` completion is polled while a "Preparing lights and materials…" note shows.
   - **Compile with the composer's render target bound** (`pipeline.getSceneTarget()`). Scene-to-RT shaders
     differ from screen shaders (tone mapping, colour space); compiling for the screen warmed the wrong
     variants.
   - **Briefly unhide hidden meshes** (not lights) so beams and other hidden meshes compile too.
   - **`renderer.initTexture()` every texture** during the warm-up.
   - Don't use `compileAsync`: in r170 it can throw `reading 'isReady'` and never settle.
3. **`renderer.debug.checkShaderErrors = false`**, otherwise every compile blocks the page.
4. **glTF props:** `simplifyModelMaterials` keeps only the colour map, so all models share 1-2 shader
   variants. The model warm hook compiles each template before any copy is shown.
5. **Post chain leak:** EffectComposer.dispose() doesn't dispose passes; dispose every pass. The chain is
   rebuilt only when ao/bloom/antialias/mode change.
6. **Beam shader without a depth texture:** guard with `uHasDepth`. Otherwise the empty sampler reads 0
   and every beam fades out (the "no beams on Low" bug).
7. **`freezeStaticSubtree` must call `updateMatrix()` before setting matrixAutoUpdate = false.** Without
   it, positions set just before freezing are lost: the room box sat half a room off and cut the club
   into four.
8. **Look:**
   - pools ~70 cd, kept on the same fixture (stable slot owners) and faded (no flashes)
   - beams at 0.45 intensity (translucent, not pillars)
   - directional fill 0.35 and "gloss" roughness 0.32 (the old sun glared white off the dance floor)
9. **`THREE.RectAreaLight` is extremely expensive per-fragment (LTC evaluation) and must NEVER be
   created without a fixed, quality-gated budget, default OFF.** Confirmed root cause of a
   57-120fps -> ~24fps Medium regression (2026-09-29 GPU perf pass): `stage-ledstrip.js` created
   up to 8 RectAreaLights per LED strip, always `.visible=true` regardless of quality/whether the
   strip was even lit - 3 decor strips = 24 always-on area lights, each evaluated on every lit
   fragment of every material at full resolution. Fixed by defaulting to 0 real lights per strip
   (`part.areaLights`/`part.lightBudget` opt-in only) - the emissive dot/diffuser meshes still
   read as "lit" with zero real light cost. Any future area-light use needs the same fixed-slot
   budget pattern as `spot`/`bounce` (rule 1), not a per-object independent count.

**Testing notes:**
- Use Chrome, not Edge: fresh Edge profiles pop up sync prompts.
- Pass `--disable-features=CalculateNativeWinOcclusion` and the backgrounding flags. A covered window
  otherwise stops drawing and looks like a freeze.
- Bump `GRAPHICS_STORAGE_KEY` (now v4) when changing preset defaults, or the browser's saved values win.

## Overnight build contracts (2026-09-28, wave 1: shared by all agents)

**File ownership** (never edit another group's files; talk through the APIs below):
- **R** render/realism: `stage-scene.js`, `stage-looks.js`, new `stage-render.js`, `stage-beams.js`, `stage-ledstrip.js`
- **P** props: `stage-props.js`, new `stage-propeditor.js`, `stage-propeditor.css`, new `stage-truss.js`
- **U** UI/editor: `stage-editor.js`, `stage-app.js`, `stage-camera.js`, `stage.html`, `stage.css`
- **A** assets: `tools/stagelib/fetch_props.py`, `C:\qlcplus-dev\Web\stage-lib\props\**`
- **C** C++ / launcher: `main/main.cpp`, `webaccess/src/webaccessbase.*` (+ ctor plumbing), `tools/stagelib/demo_show.py`

**Render API** (R provides, U consumes):
- `scene.setRenderSettings(partial)` and `scene.getRenderSettings()`
- `scene.getRenderStats()`: `{fps, frameMs, drawCalls, triangles, lights, beams, bounceLights, resolutionScale}`
- `scene.setRenderMode("lit"|"beams"|"unlit"|"wireframe")`
- `stage-render.js` exports:
  - `RENDER_SETTINGS_SCHEMA`: array of `{key, label, group, type:"bool"|"range"|"select", min, max, step, options}`, with dotted keys such as `"bloom.strength"`
  - `QUALITY_PRESETS`: `{low, medium, high, ultra}`, each a full settings object
  - `DEFAULT_RENDER_SETTINGS`
- U builds the Graphics settings UI generically from the schema, persists it in localStorage, and calls `setRenderSettings`.

**Seats** (U): camera bookmarks derived from stage objects with category `table|booth|bar|dj|stage|point`:
- seated eye height 44", standing 66"
- each seat looks at the object named/aliased "dance floor centre", else the anchor

**Prop instance overrides** (P renders, U edits):
- Stored as `objects[i].overrides = {color?: "#hex", partColors?: {partId: "#hex"}, hiddenFaces?: {partId: [...]}, visible?: bool}`.
- `objects[i].scale` and `rot` stay per instance.
- **Overrides never modify the prop definition.** Editing the definition in the prop editor updates every instance.
- `stage-propeditor.js` exports `openPropEditor(app, propId|null)`; U's "Edit prop…" button calls it.

**New part shapes** (P implements them in `createPartMesh`):
- `"model"`: `{url:"/stage-lib/props/<slug>/model.glb", size:[w,d,h] inches}`, fitted to that size
- `"truss"`: `{trussType:"box12"|"tri12"|"box16", length}` (inches), built by `stage-truss.js`
- `"ledstrip"`: `{length, ledsPerMeter:60, color, brightness, fixtureId?}`, rendered by R's `stage-ledstrip.js` export `buildLedStrip(part)` returning an Object3D with `.update(state)`

**Asset index** (A writes, P reads): `/stage-lib/props/index.json` =
```
{version:1, models:[{id, name, category, url, sizeInches:[w,d,h], source, license, tris}]}
```
CC0 only (Poly Haven, Kenney, Quaternius).

**Page-agent testing without QLC+:** each page agent runs its own static server on the dev web folder with
`C:\lightai-env\venv\Scripts\python.exe -m http.server <port> -d C:\qlcplus-dev\Web --bind 127.0.0.1`
and opens `http://127.0.0.1:<port>/stage.html#demo` headless.
- Ports: R 8911, P 8912, U 8913.
- Never start QLC+ in wave 1, except agent C.
- Test with node at `C:\Program Files\Adobe\Adobe Creative Cloud Experience\libs\node.exe`: copy the file to `.mjs`, then `--check`.

## Axes and pan/tilt maths (JS `stage-rig.js` and Python `lightai/rig/stage.py` must match)
- **three.js world** (metres, y-up) = `(X, Z, −Y) × 0.0254`, where X/Y/Z are user inches.
- **Orientation of a fixture:** `R = Rz(yaw=rot[2]) · Ry(rot[1]) · Rx(rot[0]) · H`, using user axes, where H is:
  - `floor`: identity (base down, beam up at tilt centre)
  - `hung`: Rx(180°) (upside down, beam down at tilt centre)
  - `wall`: Rx(90°)
- **Pan (deg):** `p = pan16/65535 · panMax − panMax/2`. Negate if invertPan, then add panOffset.
- **Tilt (deg):** `t = tilt16/65535 · tiltMax − tiltMax/2`. Negate if invertTilt, then add tiltOffset.
- **16-bit value:** `pan16 = MSB·256 + LSB`. With no fine channel, use `MSB·257`.
- **Beam direction** in fixture-local user axes: `d = Rz(p) · Rx(t) · (0,0,1)`. Pan turns the yoke about the local up axis; tilt turns the head about the yoke's X. World direction = `R · d`.
- **Fallbacks** when the .qxf is 0 or missing and no `models` override exists: panMax 360, tiltMax 270, beam 10-30° (QLC+ 5 defaults, `qmlui/mainview3d.cpp:1071-1074`).

## Status (2026-09-28, overnight wave 1 - agent R render/realism)

R's files: `stage-scene.js`, `stage-looks.js`, new `stage-beams.js`,
`stage-render.js`, `stage-ledstrip.js`, plus vendored
`three/examples/jsm/{postprocessing/SSAOPass.js,SMAAPass.js;
shaders/SSAOShader.js,SAOShader.js,SMAAShader.js,FXAAShader.js,
GammaCorrectionShader.js; objects/Reflector.js;
lights/RectAreaLightUniformsLib.js,RectAreaLightTexturesLib.js;
math/SimplexNoise.js}` and their `WEBFILES_CLASSIC` entries
(`webaccess/res/CMakeLists.txt`; also added the previously-missing
`stage-looks.js` there).

- **Volumetric beams** (stage-beams.js): a shell+core double-cone
  ShaderMaterial (additive, `depthWrite:false`), Fresnel-driven radial
  falloff (`pow(viewDot, power)` - bright facing the camera, soft/zero at
  the silhouette, so no hard rim), haze extinction along length, animated
  simplex noise (beams.quality>=2 only - baked into the shader at
  creation, NOT hot-swappable), and depth-based soft intersection against
  a same-frame depth pre-pass (`stage-render.js`, camera-layer toggle
  `LAYER_BEAM=7` excludes beams from it, sampled from a SEPARATE render
  target so there's no WebGL feedback loop).
- **World-scale bug fixed**: beams under a scaled look root (e.g.
  bodyScale 0.35) now reach the real world length/width -
  `setBeamWorldShape()`/`getUniformWorldScale()` divide the target
  world-space size by the beam's parent's accumulated world scale before
  writing local geometry.
- **Real light**: a pooled 32 SpotLights + 32 PointLights budget
  (`spot.maxLights`/`bounce.count`), brightest-first
  (`pickTopByBrightness`), shadows on the top `shadows.maxShadowLights` of
  those; bounce lights refresh at ~10Hz at each bright beam's hit point
  (`color * dimmer * floorAlbedo(0.5) * bounce.intensity`). Dance floor is
  now glossy PBR (`MeshPhysicalMaterial`); optional `Reflector` behind
  `reflections.enabled`.
- **Post** (stage-render.js): RenderPass -> [SSAOPass] -> UnrealBloomPass
  -> [SMAAPass|FXAA ShaderPass] -> OutputPass (ACES + exposure). Dynamic
  resolution adjusts an internal 0.5-1 multiplier every 500ms toward
  `targetFps`. **`renderer.info.autoReset` must stay `false`** with a
  manual `renderer.info.reset()` once per OUR frame (not per internal
  composer pass) - the composer's own internal `renderer.render()` calls
  each auto-reset `info.render`, so reading it after `composer.render()`
  without this would report only the LAST internal blit, not the frame's
  real draw-call/triangle count (used by `getRenderStats()`).
- **Render API** wired into `createSceneManager`'s return value exactly
  per the contract: `setRenderSettings/getRenderSettings/getRenderStats/
  setRenderMode/getRenderMode`. "beams" mode hides fixture BODY meshes via
  a `LAYER_BEAM`-aware traversal (`setBodyMeshesHidden`) rather than
  hiding ancestor groups - three's renderer skips a whole subtree once any
  ancestor's `.visible=false`, which would also hide beams nested inside
  the same look/procedural group.
- **ADJ VPar** (stage-looks.js): procedural `builtin/adj-vpar` (round
  puck can + fixed yoke + 5 emitters), dispatched in `buildLook()` before
  the normal index lookup (no GDTF/network fetch). Extends the buildLook
  contract with an optional `emitters: THREE.Object3D[]` array;
  `swapInLook` (stage-scene.js) creates one beam per emitter when present
  (all headIndex 0). `resolveLookId` defaults any fixture whose
  manufacturer/model matches `isAdjVparModel()` to this look, ahead of the
  kind-based default.
- **LED strips** (stage-ledstrip.js): `buildLedStrip(part)` -
  InstancedMesh LED dots + diffuser box + capped RectAreaLights (falls
  back to PointLight if RectAreaLight is ever unavailable),
  `.update({color,dimmer})`. Calls `RectAreaLightUniformsLib.init()` at
  module load (idempotent).
- **P integration**: `rebuildObjects` now calls `buildPropGroup(def,
  {overrides: obj.overrides || (obj.color ? {color:obj.color} : undefined)})`
  instead of the old plain `{color}` (P's per-instance override contract).
  Beam/bounce raycasts already hit P's new part shapes for free
  (`objectsGroup.children` recursive raycast).
- **Tested** with a standalone harness (own scene manager instance, no
  QLC+/stage-app.js dependency) on port 8911, headless Edge via CDP
  (`websockets` Python lib, `/json/list` page target - NOT `/json/version`,
  which is the browser-level connection and won't run `Page.navigate`).
  Zero console errors; screenshot confirmed soft-edged, no-hard-rim beams
  with bloom. FPS is software-GL (SwiftShader) only - not representative
  of real GPU performance, but the *relative* ladder is: low/medium stay
  near their 30/60 target via dynamic resolution, high drops noticeably
  (SSAO+more shadow lights), ultra is impractically slow on software GL
  (multi-second frames - 8 shadow-casting 2048 lights + reflections + SSAO
  all as CPU-rasterized full extra passes) - needs a real-GPU check before
  trusting ultra's absolute numbers.
- **Known limitations**: beam geometry segment count and
  noise-shader-branch are fixed at fixture-BUILD time from
  `beams.quality` - changing that setting live doesn't rebuild existing
  beams (only new ones); AO uses SSAOPass (GTAO wasn't vendored - more
  complex multi-pass setup, out of scope this session); reflections are a
  single flat `Reflector` (no roughness-based blur).

## Status (2026-09-28, end of session 3 - M4 looks + operator fixes)

M4 (real fixture "looks") wired into the main /stage page, plus a batch of
operator-reported UX fixes that landed mid-session. Scope: stage-scene.js,
stage-app.js, stage-editor.js, stage.html, stage.css, stage-looks.js (one
small fix), stage-looks-test.html. Did NOT touch stage-props.js, tools/,
three/, or C++/CMake.

- **Fixture look rendering** (stage-scene.js): each fixture's procedural
  mesh is built immediately as before (`buildProceduralContent`, renamed
  from the old `buildFixtureObject3D` - now returns `{group, beams}`, no
  userData, mounted as a child of a separate keyed `root` group that never
  changes identity). A look is resolved via `resolveLookId(fxModel,
  stageFixtureEntry, modelsOverride)`: `fixtures[id].look` > `models[key]
  .look` > kind default (`defaultLookIdForKind`: movingHead->
  builtin/moving_head, par->builtin/par, strobe->builtin/strobe, fog->
  builtin/hazer, fx-with-"scanner"-in-name->builtin/scanner, everything else
  incl. bar/panel/other/non-scanner-fx -> null = stays procedural).
  `buildLook()` runs async; on resolve, `swapInLook()` removes/disposes the
  procedural content node and mounts `built.root` under the SAME `root` (so
  picking/gizmo/label are unaffected), re-parents a fresh beam cone under
  `built.lens` (identity transform), and replaces `fe.beams` with one
  descriptor carrying `setPanTilt`/`lensMeshes`. `updateFixtureStates` calls
  `b.setPanTilt(pan,tilt)` instead of rotating yoke/head directly when
  present, and glows `lensMeshes` (emissive = head colour, intensity =
  dimmer*1.2). Beam start width priority: `models[key].beamStart` (explicit)
  > `built.lensRadiusInches*2` > kind default. A stale in-flight buildLook
  (fixture rebuilt again before it resolved) is detected via identity check
  against `fixtureEntries.get(id)` and disposed unused. Failed loads
  `console.warn` once per look id (`warnedLookIds` Set) and keep the
  procedural mesh. `getFixtureLookInfo(fixtureId)` on the scene API backs
  `window.__stage.lookOf()` and the Properties panel's "Look" row.
- **Known data bug worked around, not fixed** (out of file ownership):
  `tools/stagelib/fetch_gdtf.py`'s `rebuild_index()` hardcodes
  `"movable": false` for every builtin `.dae` in `index.json`, even though
  `moving_head.dae`/`scanner.dae` really do have arm+head nodes.
  stage-looks.js's own `buildFromBuiltinModel` was given a one-line fix to
  stop trusting that flag (movability is now detected from the actual
  loaded nodes). But the Look gallery's grouping/badges read `index.json`
  entries directly (can't build every look just to group it), so
  stage-editor.js has its own small override table
  (`KNOWN_MOVABLE_LOOK_IDS = {"builtin/moving_head","builtin/scanner"}`) -
  if `tools/stagelib/fetch_gdtf.py` is ever fixed properly, that table (and
  this note) can be deleted.
- **Look picker** (stage-editor.js, Properties panel, single-fixture
  selection only): a "Look" row + "Change…" button opens
  `#look-gallery-popover` (viewport-clamped like the context menu, built
  fresh each open, dismissed on outside click/scroll/Escape). Groups:
  "Moving (spot/beam)" (movable first) / "Built-in (QLC+)" / by GDTF
  `category` thereafter. Cards use `renderLookThumbnail` (lazy per-open,
  cached in `lookThumbCache`). Picking a card shows a confirm sub-view
  ("Apply to all `<mfr>/<model>` fixtures" / "This fixture only" / Back);
  a "Reset to default" button always clears both `fixtures[id].look` and
  `models[key].look`. Everything commits via `app.commitDraftChange`. Shows
  "Real fixture models appear here after downloading from GDTF Share." when
  no index entry has a `path` (i.e. no real GDTF looks downloaded yet).
- **Housekeeping**: the hand-made GDTF-format test fixture now lives at
  `stage-lib/samples/sample-spot-1/` (not `stage-lib/gdtf/...` - that folder
  is reserved for real downloads only), sourced from
  `webaccess/res/stage-looks-sample/`. `stage-looks-test.html` points at the
  new path.
- **Properties panel is now a floating overlay** (stage.css `#right-panel`:
  `position:absolute` inside `#main`, which is now `position:relative`),
  not a flex column - opening/closing a selection never resizes the canvas
  or reframes the camera. `scene.resize()` is no longer called from
  `updateRightPanelVisibility` (stage-editor.js) - only real window resizes
  and the LEFT panel's collapse still call it.
- **View mode is look-only** (operator spec, overriding an earlier "click
  switches to Edit" idea that never shipped): Edit is the default mode on
  load. In View mode, left-click/right-click on items do nothing (a
  one-time toast hint "View mode — switch to Edit to change the layout"
  fires via `showViewModeHintOnce()`), no Properties/gizmo/manipulation/
  1-2-3/Delete/Ctrl+D - only camera navigation. Switching TO View
  (`app.setMode("view")`, stage-app.js) clears `app.selection`. Undo/redo
  is NOT mode-gated - always available. stage-props.js's drag-a-prop-into-
  scene was NOT verified/gated for View mode (out of file ownership) -
  check this if the operator reports it still working in View mode.
- **Camera controls extracted to `stage-camera.js`** (a separate module,
  NOT owned by this pass - written in parallel by another agent). This
  session's job was only the glue: removed WASD/QE fly, middle-drag orbit
  and wheel-zoom/speed entirely from stage-scene.js (`orbit.mouseButtons =
  {LEFT:PAN, MIDDLE:null, RIGHT:null}`, `enableZoom=false`,
  `enableRotate=false`); stage-app.js dynamically `import()`s
  `./stage-camera.js` inside a try/catch (page still boots if it's missing)
  and wires `initCameraControls({camera, orbit, domElement, getSettings,
  isBlocked, onHistory: pushCameraHistory, toast: showToast})`, calling
  `.update(dt)` from the existing per-frame callback and `.setSettings(...)`
  whenever a Settings-popup control changes. `getSettings()`/`setSettings`
  payload includes BOTH this page's own key names (orbitSensitivity/
  panSensitivity/smoothMove) and the shorter ones from the operator's
  spec (lookSensitivity/panSpeed/smooth) as a hedge against a naming
  mismatch between the two modules. New per-browser view settings:
  `keepLevel` (default false), `orbitMiddle` (default false), both exposed
  in the Settings popup's Movement section ("Mouse look sensitivity" is the
  renamed "Orbit sensitivity" label - same `orbitSensitivity` storage key).
  `getMouseConfig()`/the HUD legend now report the middle button as "look"
  or "orbit" depending on `orbitMiddle`, not from `orbit.mouseButtons`
  (which is permanently null there now). `app.isFlyActive()` (stage-app.js)
  replaces the old `scene.isFlyActive()` stage-editor.js used for the
  Shift-for-rotate-modifier guard - it defers to
  `app.cameraControls.isFlyActive()` if present. **If stage-camera.js still
  doesn't exist**, WASD/middle-drag do nothing (left-drag pan still works
  via OrbitControls) - check for it and verify the wiring once it lands.
- **No native scrollbars anywhere** (operator preference): stage.css hides
  the scrollbar thumb globally (`scrollbar-color`/`::-webkit-scrollbar-*`),
  showing it only on `:hover` or a `.scrolling` class; stage-app.js has a
  capturing `document.addEventListener("scroll", ..., true)` helper (scroll
  events don't bubble, capture is the only way to catch them page-wide)
  that toggles `.scrolling` for 800ms per scrolled element. Also
  `overflow-x: hidden` on every scrollable panel/popup.
- **Not done / not verified this session**: stage-camera.js's actual
  behavior (free-fly WASD, middle-drag mouselook, keepLevel/orbitMiddle
  semantics) - it's someone else's file; only smoke-test the glue once it
  exists. A `node --check` syntax pass could not be run (no usable
  `node.exe` found anywhere on this machine this session, despite a
  previous note claiming one was found - searched AppData/Program
  Files/msys64 and the MinGW64 shell's PATH) - verified instead with a
  custom Python brace/paren/bracket balance checker
  (ignores strings/comments but NOT regex literals - stage-looks.js's
  `joinUrl()`'s `/^https?:\/\//i` regex is a confirmed false-positive if
  re-run) plus manual review. Actually run `node --check` on these files
  once a working node.exe is located.

## Status (2026-09-28, end of session 2 - operator UX pass)

Big UX/interaction pass on top of session 1's M1/M2 foundation (mouse remap, Maya modifiers,
undo/redo, context menu, current-show-follow, default layout, shared prop library merge,
frozen-label fix, frustum beams, Settings popup, per-model beam look, per-face/side visibility,
item locking). Key points for later sessions:

- **Mouse**: left-drag pans, middle-drag orbits, wheel zooms, right-click opens a custom
  context menu (native menu suppressed). `orbit.mouseButtons = {LEFT: PAN, MIDDLE: ROTATE,
  RIGHT: null}` in `stage-scene.js`; verified against the vendored `OrbitControls.js` that
  `enabled=false` fully blocks its ctrl/shift/meta pan<->rotate swap, so disabling orbit while
  a Maya modifier is held (see below) is sufficient - no extra guard needed.
- **Maya modifiers** (stage-editor.js): hold Alt=move / Shift=rotate / Ctrl=scale switches
  the TransformControls mode, suppresses orbit (`scene.setOrbitSuppressed`) and drives a
  *custom* pointer-based manipulation (plane raycast for move, mouse-delta for rotate/scale) -
  it does NOT drag the gizmo mesh itself. W/E/R set the persistent gizmo mode; a fixture-only
  selection can't go into scale mode (toast "fixtures keep their real size").
- **History** (stage-app.js): one stack, `app.commitDraftChange(fn)` is the single choke point
  for every draft edit (deep-clones before/after, skips no-ops, rebuilds). Camera moves come
  from `orbit`'s `start`/`end` events, debounced 350ms so a burst of wheel ticks (which fire a
  start+end pair *per tick*, confirmed in the vendored source) collapses into one entry.
- **Default layout** (stage-app.js `defaultLayoutAll`/`autoPlaceMissing`): room 40'x36'x14',
  monitor-seeded fixtures placed proportionally (room is never shrunk to the grid), others
  bucketed by kind into wrapping rows. Clearance-from-monitor-fixtures and true overlap
  avoidance are NOT implemented precisely (only the room-default-size/all-placed guarantees
  are) - fine for check 10 but worth tightening later.
- **Beams**: frustum shape (`createBeam`/`setBeamShape` in stage-scene.js) rewrites a unit
  cylinder's position attribute from a cached template every frame - no geometry rebuild.
  Per-model look is `stage.draft.models[key] = {beamStart, beamSpread}` (inches/degrees),
  editable in the fixture properties panel; kind-based defaults and zoom-channel scaling live
  in `stage-rig.js`. Global "Beam width x/brightness/length limit" are view-only
  (`viewSettings` in stage-scene.js, persisted in `localStorage`, never in the stage file).
- **Room box off**: beams fall back to a floor-plane + throttled (~10Hz) object raycast
  instead of the room-box geometric hit test; see `computeBeamLength` in stage-scene.js.
- **Per-face/side visibility** (stage-props.js): `part.hiddenFaces` (box/cylinder/cone,
  material-group index driven) and `part.side` (plane Front/Back/Both). Picking and beam
  raycasts both skip a hidden face via `isHitOnHiddenFace()` in stage-scene.js. No hover
  highlight or normal-indicator arrow was built (props-mode has no live 3D preview wired up
  yet - `scene.showPropPreview()` is still a no-op stub from session 1).
- **Locking**: `locked:true` on a draft fixture/object entry; `scene.pick()` skips locked
  objects (clicks pass through), `stage-editor.js` blocks gizmo/manipulate/distribute/
  delete/duplicate for locked entries. Lock icon + dimmed row in the left-panel lists;
  "Unlock" only lives there (a locked item can't be right-clicked in the viewport).
- **Known simplification**: "Save as prop..." (context menu) and per-model beam edits both
  route through `app.commitDraftChange`/props-dirty correctly but have no automated coverage
  yet - only manual/inspection.
- Test harness (`tools/stagelib/stage_check.py`) grew checks 10-18 (see the file's docstring
  history / git log for exact numbering) plus a `<project>-foh-after.png` screenshot. New
  window.__stage hooks added this session: `undo/redo/historyInfo`, `duplicateObject/
  deleteObject/deleteFixture/addObject/setLocked/setModelOverride`, `labelStats`, `beamDir/
  beamInfo`, `mouseConfig`, `getCamera/setCamera`, `getViewSettings/setViewSettings`,
  `pickAt/screenPos`, `showName`.

## Status (2026-09-28, end of session 1)
- **Done:**
  - M1 (C++: `webaccess/src/webaccessstage.{h,cpp}` + routes and commands in `webaccess.cpp`)
  - M2 (`webaccess/res/stage*.{html,css,js}`, vendored `three/` r170)
  - test harness `tools/stagelib/stage_check.py`, green: 10/10 checks, 3 runs in a row
- **Page behaviour:**
  - On a show's first visit, the page imports the QLC+ 2D Monitor layout as an **unsaved** draft; the operator must click Save.
  - `#demo` hangs a fake rig on a truss.
- **Colour wheels:** wheels with only slot names (the BEAM230) get their colours from the name, using the table in `stage-rig.js` built from [[mayans-beam-color-rgb]].
- **Open items:**
  - Check 7 (show switch while the page is open) timed out twice before becoming green and hasn't been reproduced since. Watch for a hang in `openProjectFile` with a subscribed page.
  - The prop-builder UI has no automated coverage yet (the API round-trip needs `--allow-props-write`).
  - Beams are simple additive cones; realistic beams are M3.
- **Next:** M3 (realism and movement), M4 (GDTF looks), then M5 (lightai) and M6 (example venues).

## Known QLC+/webaccess quirk: Simple Desk after `openProjectFile force`

Confirmed by direct manual testing (raw `CH|<abs>|<val>` over the WS, then reading the raw
`VIS|DMX` bytes - not just the page's computed values): after `openProjectFile`/`loadProjectFile`
`"force"` reloads the show (what `check_readdress`/check 7 in `stage_check.py` uses), Simple
Desk channel overrides (`CH|<abs>|<val>`) stop taking effect on the live DMX output - the raw
byte for the targeted channel stays 0 no matter which channel/universe/value is sent, even
long after the reload settles. This reproduces on the CURRENT loaded show at ANY channel
(tried channel 1 and a channel in the 290s), so it isn't specific to a particular fixture or
address. It's a QLC+ engine/webaccess-side effect of the reload, not a bug in the `/stage` page
JS - the page correctly renders whatever DMX it's actually sent (verified pan/tilt maths against
a raw-zero frame matched exactly). Since this is outside `webaccess/res/` (would need a C++
fix), `stage_check.py`'s checks that rely on Simple Desk (currently check 14, beam direction)
are ordered to run BEFORE check 7 (readdress) rather than after, to avoid it. If a future check
needs Simple Desk AND must run after a project reload, expect this to bite and reload the page
was NOT sufficient - some deeper engine reinit needed which wasn't investigated further.

## Build and test rules
- **Only one QLC+ at a time** (operator rule, 2026-09-28). Close any dev or test instance, by PID,
  before opening another. `tools/stagelib/demo_show.py start` does this itself and refuses to
  start while the live `C:\qlcplus` copy is running; it never kills the live one.
- Demo: `demo_show.py start --club-layout` keeps the operator's saved demo stage file.
  `--fresh` discards it.
- **Never stop QLC+ by image name** (`taskkill /IM qlcplus.exe`, `Stop-Process -Name`): that also
  kills the live rig instance. On 2026-09-28 this closed the operator's live QLC+ mid-session.
  Stop only the exact PID you started (`QlcInstance.stop()` does this), or look up the PID that
  owns port 9998. Never restart the live instance yourself: the operator decides, because it
  outputs DMX to the real rig.
- **Never** install over `C:\qlcplus` while QLC+ runs there (the live rig, port 9999).
- **`cmake --install --prefix` does NOT relocate this project.** INSTALLROOT is absolute (`C:/qlcplus`),
  so it writes into the live install anyway. For the dev copy, `robocopy C:\qlcplus C:\qlcplus-dev /E`
  once, then copy `build-mingw/webaccess/src/qlcpluswebaccess.dll`, `build-mingw/main/qlcplus.exe`
  and `webaccess/res/stage*`, `three/` and `control.html` into `C:\qlcplus-dev\Web` by hand.
- Tests use a dev install plus `lightai.devtools.QlcInstance` on port **9998** with a cleaned project and no DMX I/O.
- Add web files to `WEBFILES_CLASSIC` (`webaccess/res/CMakeLists.txt`). `install(FILES)` flattens folders; `three/` is installed as a directory.
- Web access **404s any URL with a `?query`** (the route check fails), so pass options through `#hash` only: `/stage#selftest` and `/stage#demo` (fake rig, no QLC+ needed).
- A missing `.js` returns `control.html` with status 200, so a typo in a script path shows up as an HTML parse error.

## Status (2026-09-29, performance pass - integrated GPU / CPU-bound complaint)

Operator reported the 3D stage page "runs fully on the CPU" on a laptop with only an Intel UHD
(1GB shared) iGPU. Measured with CDP `Profiler.enable/start` (10s window) + a custom rAF-timing
harness against headless Edge/SwiftShader on `/stage#demo` at the Low preset (software GL, so
only JS self-time/idle-fraction is meaningful, not FPS): **before**, avg frame time 124ms
(effectively ~8fps even in a 20-beam/15-fixture demo scene), with `three.module.min.js setSize`
alone eating 21.4% of all CPU time (2.2s/10s) - the dynamic-resolution logic was thrashing,
repeatedly reallocating composer/depth render targets because frame times never got anywhere
near the 30fps target. **After** all fixes below: avg frame time 37.5ms (idle fraction went
71.5%->93.7%), `setSize` dropped to 0.5% (55ms/10s), reported `jsMs` (new HUD stat, see below)
0.5-0.7ms, render-call `frameMs` ~0.9-1.0ms. Verified via CDP: no new console errors/exceptions,
`beamInfo()` still reports sane frustum numbers, label show/hide toggles the DOM correctly
(`domCount` constant, `anyVisibleWhenOff:false`/`anyVisibleWhenOn:true`), and a screenshot
confirms the scene/HUD/toast all render correctly post-fix.

Changes, all in `webaccess/res/` (stage-scene.js, stage-beams.js, stage-editor.js, stage-app.js,
stage.css, stage.html) - copied to `C:\qlcplus-dev\Web\` (no QLC+ restart):

1. **Renderer flags** (stage-scene.js `createSceneManager`): `new THREE.WebGLRenderer({
   antialias: false, stencil: false, powerPreference: "high-performance" })` - was
   `{antialias:true}` unconditionally, which paid for native MSAA AND stage-render.js's
   FXAA/SMAA post pass at once whenever post-AA was also on.
2. **Beams (biggest single win): frustum shaping moved from CPU to the vertex shader**
   (stage-beams.js). `setBeamWorldShape`/`setMeshWorldShape` used to rewrite every vertex of a
   per-instance-cloned cylinder geometry every frame, then call `geo.computeVertexNormals()`
   and `geo.computeBoundingSphere()` - all CPU, every frame, per beam, x2 (shell+core) x N
   beams. Now: geometry is the **shared, unmutated** unit-cylinder template (no more
   `.clone()` per beam - `sharedGeometry()` is reused directly, and `group.dispose()` no
   longer disposes it, only the material); `BEAM_VERT` takes `uNearRadius`/`uFarRadius`/
   `uLocalLength` uniforms and reshapes `position` in the vertex shader every frame, plus
   computes an **analytic cone-frustum normal** (`(uLocalLength*cosθ, -(farR-nearR),
   uLocalLength*sinθ)`, normalized) instead of relying on `computeVertexNormals()`, so the
   Fresnel-style view-angle falloff in the fragment shader still looks right even at a wide
   spread angle. `setMeshWorldShape` now just writes 3 uniforms - no geometry touched.
3. **Static-scene `matrixAutoUpdate = false`**: new `freezeStaticSubtree(root)` helper
   (stage-scene.js, next to `clearGroup`) traverses a subtree setting `matrixAutoUpdate=false`
   then does one `updateMatrixWorld(true)`. Called at the end of `setRoom()` (walls/floor/grid
   - and the Reflector, frozen individually right after `applyReflectionSettings()` adds it)
   and at the end of `rebuildObjects()` (every prop/truss instance). **Not** applied to
   `fixturesGroup` (yoke/head/beam nodes animate every frame; too many multi-part
   look/LED-strip hierarchies to safely audit under this pass's budget). Because
   `stage-editor.js`'s live-drag code (`continueManipulation`'s translate/rotate/scale
   branches, and the `app.scene.transform` TransformControls `objectChange`/mouseUp handlers)
   mutates a frozen object's `.position`/`.quaternion`/`.scale` directly during a drag
   *before* the eventual `commitDraftChange`-triggered rebuild, each of those call sites now
   also calls `<node>.updateMatrix()` right after the mutation (harmless no-op on an
   unfrozen fixture root) - `updateMatrix()` sets `matrixWorldNeedsUpdate=true` even with
   `matrixAutoUpdate=false`, so the next frame's traversal still picks it up correctly.
   **Not manually verified with a real simulated drag this session** (would need synthetic
   pointer events on the canvas) - logic verified by reading three.js's own
   `Object3D.updateMatrix()`/`updateMatrixWorld()` source, worth a real drag smoke-test later.
4. **Labels**: `loop()` (stage-scene.js) now skips `labelRenderer.render()` entirely while
   `labelsVisible` is false, and caps it at 10Hz (`LABEL_RENDER_INTERVAL_MS=100`) while true.
   Gotcha found and fixed: `CSS2DObject`'s `.visible=false` only actually hides its DOM element
   (`display:none`) *inside* `CSS2DRenderer.render()` - so `setLabelsVisible()` now does one
   explicit `labelRenderer.render()` call itself right after flipping the flags, or turning
   labels off would leave the last-rendered ones stuck on screen. Verified via CDP: toggling
   off/on updates `.stage-label` DOM visibility correctly.
5. **Throttling**: perf-HUD numeric refresh 4Hz->2Hz (`perfAccum` threshold 0.25->0.5);
   minimap redraw capped to 5Hz (new `minimapAccum` in stage-app.js's `setOnFrame`, was
   unthrottled/every frame); beam-hit raycast (`computeBeamLength`) and the bounce-light
   refresh both now use a shared `beamHitThrottleMs()` (100ms normally, **200ms/5Hz at Low
   quality**, was a flat 100ms/10Hz); **fixture DMX->visual-state recompute
   (`computeFixtureState`+`updateFixtureStates`, previously run unconditionally every frame
   for every fixture) now only runs when `app._dmxDirty` is set** - set `true` by the
   websocket's `onDmx` handler (a new frame actually arrived) or by
   `rebuildSceneFromDraft()` (fixtures were just rebuilt) - or unconditionally while
   `app.demo` is on (the demo rig's DMX is continuously time-animated, so it's always
   "dirty"). Starts `true` so the very first frame still renders a pose before any DMX
   arrives.
6. **Frame limiter**: `loop()` now measures `now - lastFrameTime` against
   `1000/currentRenderSettings.targetFps` (Low=30) and returns immediately (before doing
   ANY work - orbit update, DMX recompute, beam depth uniforms, render) if not enough time
   has passed - was previously rendering on every single rAF tick regardless of the quality
   preset's target. `document.hidden` pause was already in place (unchanged).
7. **CSS**: `body.quality-low` class (toggled from `_updatePerfHud`, ~2Hz, reading
   `app.scene.getRenderSettings().quality` - covers every way quality can change: preset
   button, auto-perf step-down, or a `window.__stage.applyQualityPreset()` test/script call)
   disables `backdrop-filter` on the perf HUD/minimap/settings popup/HUD legend/context
   menu/look gallery/keybind-help panels, falling back to a plain, still-readable
   `rgba(12,14,20,0.92)` background.
8. **HUD**: added "JS ms/frame" (new `statsJsMs` in stage-scene.js's `loop()` - times
   orbit+onFrame+beam-depth-uniform work only, separately from the existing "Render ms"
   which is stage-render.js's `pipeline.render()` GPU-submit call) and "GPU" (the real
   `WEBGL_debug_renderer_info`/`UNMASKED_RENDERER_WEBGL` string, e.g. "ANGLE (Intel(R) UHD
   Graphics ...)" on real hardware, truncated with a `title` tooltip for the full string) rows
   to `#perf-hud`. A new `checkGpuRendererOnce()` (stage-app.js) shows a warning toast once
   if that string matches `/swiftshader|llvmpipe|software/i` - confirmed firing correctly in
   the CDP screenshot (headless Edge uses SwiftShader, so it's the expected/correct case to
   trigger on that profiling rig, and would similarly catch a real laptop with browser GPU
   acceleration turned off in Edge settings).

**Not done / open items for a future session**: a real synthetic-pointer-event drag test of
the `matrixAutoUpdate=false` + `updateMatrix()` fix (item 3) on both an object/prop and via the
native TransformControls gizmo; `fixturesGroup` itself is not frozen at all (could still save
CPU on a rig with many fixtures, but needs auditing every look/LED-strip/GDTF animated-part
hierarchy first to avoid breaking a moving part that isn't the yoke/head); did not re-run the
full `tools/stagelib/stage_check.py` suite this session (it manages its own QLC+ instance
lifecycle, which conflicts with the "only one QLC+, never restart it yourself" rule when a dev
instance is already running on 9998 - only ad-hoc CDP checks against the already-running
instance were used).

## View-mode inspect + click validation (2026-09-29)
- View mode is look-only: left/right click on a fixture/object → `inspect()` in stage-editor.js draws one
  reused BoxHelper outline (depthTest off, never disposed so its shader stays warm) + a fixed name tag
  `#inspect-tag` that follows the item (shown even with labels off). Clicking the same item again or Esc
  clears it (inside the closed club room every pixel hits the shell, so there is no "empty" click).
  Switching to Edit clears it. No selection / Properties / gizmo / context menu in View mode.
- The guard lives at the selection source: `selectOnly`/`toggleSelect` → `inspectKey` in View mode (covers canvas,
  left-panel Fixtures/Objects list rows, test hook); `updateGizmoAttachment` and `refreshPanel` also refuse in
  View mode; unplaced double-click placing and prop drag-drop show a toast instead. (A list-row click used to
  select + attach the gizmo in View mode - the canvas-only check missed it; click_check.py now covers lists.)
- `hud-legend` text is mode-aware (`updateHudLegend`, refreshed in `setMode`).
- `tools/stagelib/click_check.py`: visible-Chrome CDP check of View/Edit left+right clicks, View drag
  never moves anything, mode switches clear state; screenshots `lightai/reports/stage/clicks-*.png`.
- `window.__stage.three()` test hook → {renderer, scene, camera} for CDP perf experiments.
- LED strips were removed from the demo project + club_scene.py at the operator's request (2026-09-29);
  strip functionality will be revisited later.
