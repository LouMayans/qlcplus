---
name: lighting-venues
description: "Per-venue registry for the lightshow system: each venue's rig.md + observed.md + .qxw, plus which venue is active. Read this first to know which rig you're working on."
metadata:
  node_type: memory
  type: project
---

# Lighting venues registry

The lightshow system is **per-venue**. Lights, DMX patch, physical positions, channel behavior,
and custom fixtures are all venue-specific — they describe a *room*, not the PC. Each venue is a
self-contained profile under `venues/<slug>/`; `/lightshow` and the capture/test scripts always
operate against **one active venue**. This file is the registry.

## How a venue profile is laid out

```
.claude/memory/lighting/venues/<slug>/
  rig.md        # authoritative patch: fixtures, IDs, DMX addresses/universes, channel maps,
                # custom .qxf list, function inventory, naming conventions, VC layout
  observed.md   # camera-verified knowledge: physical positions/aim + per-channel real behavior
                # (see [[visual-feedback-camera]] for how this gets filled in)
```
The venue's live `.qxw` project and `.qxf` fixtures stay in their normal repo locations
(`SaveFile/…`, `Fixtures/…`); `rig.md` points at them.

## Active venue

The active venue is recorded in **`.claude/scripts/rig-capture.config.json`** (`activeVenue`).
`/lightshow` resolves it here → loads that venue's `rig.md` + `observed.md`. Switch with
`/lightshow set venue <slug>` (updates the config). Capture settings (camera, ws url, fps) and the
machine-local camera device name live alongside it — see [[visual-feedback-camera]].

## Registry

| Slug | Name | rig.md | observed.md | Live project (.qxw) | Universes | Notes |
|------|------|--------|-------------|---------------------|-----------|-------|
| `mayans` | Mayans club | [venues/mayans/rig.md](venues/mayans/rig.md) | [venues/mayans/observed.md](venues/mayans/observed.md) | `SaveFile/Main Project.qxw` | U1 (beams+washes), U2 (PAR/bar/FX/RGB/FOG) | 35 fixtures (IDs 0–34); BEAM230 V3 (id34) has a different channel order — color on ch5. **active** |

## Adding a venue — `/lightshow learn venue <name>`

1. Create `venues/<slug>/` with `rig.md` + `observed.md`, add a row above, set it active in the config.
2. **If a `.qxw`/fixtures were provided**, import them into `rig.md` (patch, IDs, channel maps).
3. **If fixtures were NOT provided**, run camera-driven discovery (see [[visual-feedback-camera]]):
   sweep DMX with the `CH|addr|value` Simple-Desk command, capture on camera what physically
   responds, infer fixture boundaries + what each channel does + positions, then author new `.qxf`
   files (per [[qlc-fixture-definition-format]]) and a patch plan — confirm before committing.
4. Fill `observed.md` (geometry + channel calibration) and report the inventory + any gaps.

Related: [[visual-feedback-camera]] (the camera loop + WebSocket control/testing API),
[[qlc-save-file-format]], [[qlc-fixture-definition-format]], [[effect-recipes-cookbook]].
