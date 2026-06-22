# Lighting design knowledge base

A curated knowledge base for programming **world-class nightclub light shows** in QLC+ on the
Mayans rig — and for letting an assistant **auto-generate** new looks/effects so they don't have
to be built by hand. Two halves: (A) exactly how QLC+ stores and does things (reverse-engineered
from this repo's engine source, so generated `.qxw`/`.qxf` XML is correct), and (B) professional
lighting-design craft (fixture roles, show design, effect recipes, cross-platform technique).

Built from a deep research pass (engine source + web research on pro club/EDM lighting and the
ONYX / grandMA / Avolites / MagicQ / Resolume workflows). All QLC+ technical claims are grounded
in the source in `engine/src`; external lighting facts are flagged where approximate.

## Files

**A — How QLC+ works (the "so the XML is right" layer):**
- [qlc-save-file-format.md](qlc-save-file-format.md) — exact `.qxw` workspace XML: Workspace/Engine, Fixtures, every Function type (Scene, Chaser, Sequence, EFX, RGBMatrix, Collection, Show, Script, Audio, Video), with example snippets. The reference for generating/editing projects.
- [qlc-fixture-definition-format.md](qlc-fixture-definition-format.md) — exact `.qxf` fixture format: the full channel **Preset** and **Group** enum lists, capabilities, modes, heads, physical; recipe to author a new fixture.
- [qlc-functionality-reference.md](qlc-functionality-reference.md) — capabilities map: what every Function type, Virtual Console widget, Simple Desk, I/O, the Script command language and RGB-matrix JS API actually do.
- [club-rig-mayans.md](club-rig-mayans.md) — **the venue's real rig & project**: every fixture, DMX address/universe, custom `.qxf`, fixture/channel groups, the 270-function inventory, naming conventions, output (Art-Net) + website (Web Access WS) control, VC layout. Reuse this addressing/naming for any new work.

**B — Lighting craft (the "make it look amazing" layer):**
- [fixture-types-and-roles.md](fixture-types-and-roles.md) — spots/beams/washes/pars/pixel-bars/strobes/blinders/FX/lasers/haze: purpose, typical DMX channels, placement, show roles — mapped to the venue's fixtures.
- [lightshow-design-principles.md](lightshow-design-principles.md) — energy arcs, color palettes, beat sync, layered "looks", movement, contrast, the build-up & drop, haze, busking — the why/when.
- [effect-recipes-cookbook.md](effect-recipes-cookbook.md) — **the actionable cookbook**: concrete club effects → exact QLC+ constructs (EFX/RGB Matrix/Chaser/Collection/Script) on the real rig, plus genre playbooks and an XML-generation cheat sheet. Start here when building something.
- [cross-platform-concepts.md](cross-platform-concepts.md) — pro concepts from ONYX/grandMA/Avolites/MagicQ/Resolume (groups, palettes, cue stacks, the parametric FX engine, busking, timecode) mapped to QLC+ equivalents and workarounds.

## How to use it to build new looks

**Fastest path:** run the project slash command **`/lightshow <what you want>`** (defined in
`.claude/commands/lightshow.md`). It loads this knowledge base and the live save file, then
creates/updates shows & looks, researches lighting, or tunes this base — modes: `create`,
`update`, `research`, `tune`. Manually, the same flow is:

1. Decide the *look* using [fixture-types-and-roles.md](fixture-types-and-roles.md) + [lightshow-design-principles.md](lightshow-design-principles.md).
2. Pick the construct + parameters from [effect-recipes-cookbook.md](effect-recipes-cookbook.md).
3. Resolve real fixture IDs / addresses / channel indices from [club-rig-mayans.md](club-rig-mayans.md).
4. Emit the XML using [qlc-save-file-format.md](qlc-save-file-format.md) (and [qlc-fixture-definition-format.md](qlc-fixture-definition-format.md) if a new fixture is needed); confirm mechanics in [qlc-functionality-reference.md](qlc-functionality-reference.md).

Files cross-link with `[[name]]` wiki-links. Keep this base updated as the rig or conventions change.
