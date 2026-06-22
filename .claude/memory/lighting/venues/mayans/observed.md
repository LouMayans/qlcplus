---
name: venue-mayans-observed
description: "Camera-verified knowledge for the Mayans rig: physical fixture positions/aim and per-channel real-world behavior. Filled in from camera captures; UNVERIFIED rows are still to be confirmed."
metadata:
  node_type: memory
  type: project
---

# Mayans — observed geometry & channel behavior

What the **camera** has confirmed about the physical Mayans rig — the spatial/real-world layer that
the patch in [[club-rig-mayans]] can't capture. Filled in by the `/lightshow` **calibrate** /
**learn venue** flow using the capture loop in [[visual-feedback-camera]]. Until a row is observed
it stays `UNVERIFIED` — do **not** treat placeholders as fact.

Status legend: `UNVERIFIED` (placeholder, never seen) · `OBSERVED <date>` (confirmed on camera) ·
`CONFLICTS` (camera disagrees with the patch — investigate).

## 1) Observed geometry — where each fixture is and what it points at

Seed cheaply: trigger a known per-fixture stimulus (e.g. `SPOT n Red`, a position scene, or a raw
`CH` sweep) and note *where in the frame* that fixture and its beam land. Camera vantage point must
be recorded so "house-left/right" is unambiguous.

**Camera vantage:** UNVERIFIED — record where the camera sits (e.g. "booth, facing stage") and what
it frames (dancefloor / stage / truss) once set up.

| Fixture ID | Model | Mount (truss/floor/wall) | Position in room | Aims at | Pan/Tilt home | Beam coverage | Status |
|-----------|-------|--------------------------|------------------|---------|---------------|---------------|--------|
| 34 (BEAM230 V3 #1) | BEAM230 V3 | TBD | TBD | TBD | TBD | TBD | UNVERIFIED |
| 0–7, 12–15 (BEAM230 ×12) | BEAM230 | TBD | TBD | TBD | TBD | TBD | UNVERIFIED |
| 8–11 (Wash ×4) | WASH | TBD | TBD | TBD | TBD | TBD | UNVERIFIED |
| 31 (Revolver Wash) | Revolver | TBD | TBD | TBD | TBD | TBD | UNVERIFIED |
| 16,17,28,29 (VPar ×4) | ADJ VPar | TBD | TBD | TBD | — | TBD | UNVERIFIED |
| 18 (ThinPAR) | ThinPAR 38 | TBD | TBD | TBD | — | TBD | UNVERIFIED |
| 19,20 (Tetra Bar ×2) | Tetra Bar | TBD | TBD | TBD | — | TBD | UNVERIFIED |
| 21–25,27,32 (Generic RGB) | Generic RGB | TBD | TBD | TBD | — | TBD | UNVERIFIED |
| Swarm 5 FX, FOG | FX/FOG | TBD | TBD | TBD | — | TBD | UNVERIFIED |

(Expand to one row per fixture as positions are confirmed. Add a simple room sketch / fixture map
here once enough fixtures are placed.)

## 2) Observed channel behavior (calibration)

Confirm or correct the documented channel maps in [[club-rig-mayans]] against what the camera
actually shows. Drive one channel at a time with `CH|<absAddr 1-based>|<value>` (blackout first with
`QLC+API|sdResetUniverse|<u>`), capture, record. `absAddr = universeIndex*512 + channel0based + 1`.

**Known things to verify first:**
- **BEAM230 V3 (id34) color on ch5** — the venue convention writes color to ch5 (FOCUS per the .qxf).
  Confirm on camera that a `CH` to id34's ch5 changes color, not focus.
- **BEAM230 color wheel values** (per [[club-rig-mayans]]): 0 open, 8 red, 16 deep-blue, 40 yellow,
  48 purple, 72 cyan, 88 blue, 96 orange, 104 green — confirm each lands as named.
- **Pan/tilt orientation** — does pan 0→255 sweep house-left→right or the reverse? Tilt up vs down?
- **Dimmer threshold** — lowest DMX value at which each model is visibly lit.

| Fixture ID | Channel (0-based) | Documented function | Observed behavior | Status |
|-----------|-------------------|---------------------|-------------------|--------|
| 34 | 5 | FOCUS (venue uses as COLOR) | TBD | UNVERIFIED |
| 1 | 8 | Color | TBD | UNVERIFIED |
| 1 | 0 | Pan | TBD | UNVERIFIED |
| 1 | 5 | Dimmer | TBD | UNVERIFIED |

(Append rows as channels are swept. Note any value→effect mappings that differ from the patch as
`CONFLICTS` and flag them in [[club-rig-mayans]].)
