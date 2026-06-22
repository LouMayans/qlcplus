---
name: mayans-beam-color-rgb
description: User-confirmed RGB (0-255) values for each Mayans BEAM230 color-wheel color name; reuse when building RGB PAR fixtures
metadata:
  type: project
---

The Mayans BEAM230 (V1/V2/V3) color-wheel colors map to these RGB values. The user
visually tested these on the real rig and **confirmed/corrected** them on 2026-06-22, so
treat them as the canonical color set (reuse for the planned RGB PAR fixtures and any look
that references these color names):

| Color  | R   | G   | B   | Hex       | Status |
|--------|-----|-----|-----|-----------|--------|
| White  | 255 | 255 | 255 | `#FFFFFF` | confirmed good |
| Red    | 255 | 0   | 0   | `#FF0000` | confirmed good |
| Yellow | 255 | 255 | 0   | `#FFFF00` | confirmed good |
| Green  | 0   | 255 | 0   | `#00FF00` | confirmed good |
| Orange | 255 | 110 | 0   | `#FF6E00` | confirmed good |
| Blue   | 15  | 15  | 255 | `#0F0FFF` | **corrected** (was 0/0/255) |
| Pink   | 255 | 5   | 140 | `#FF058C` | **corrected** (was 255/60/150) |

**Why:** These are the actual on-the-wall colors the operator wants; the wheel's per-color
DMX values differ per BEAM version (see the `<Channel Name="Color">` blocks in the
`Fixtures/Mayans-BEAM230 *.qxf` files), but the *target RGB* is the same set by color name.

**How to apply:** When authoring RGB/RGBA PAR fixtures or color presets, drive the colors to
these RGB triples. Note the American DJ VPar (copied to `Fixtures/American-DJ-VPar.qxf`) is
**RGBA (has an Amber channel)** — orange/amber there comes off the Amber channel, not an R+G
mix, so adapt accordingly. Related: [[mayans-beam-v3-channel-order]] if present.
