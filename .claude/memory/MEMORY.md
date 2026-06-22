# Project memory index

This folder is the **canonical, git-tracked project memory** for this QLC+ fork. Keep
project knowledge here (not only in the per-user `~/.claude` memory dir) so it travels with
the repo. One file per topic; this file is the index.

- [build-procedure.md](build-procedure.md) — the verified recipe to build + install QLC+ here (MSYS2 MinGW64 + CMake/Ninja → double-clickable `C:\qlcplus`); D2XX SDK setup, the `QDebug` build fix, `-Werror` gotcha, and the full DLL-bundling closure.
- [priority-system-rebuild.md](priority-system-rebuild.md) — the per-function priority feature: **now IMPLEMENTED** on `priority-rebuild` (builds 426/426 warning-clean, installs, runs); records the key decisions/deviations and the pre-existing-vs-real test status. See also `../../PRIORITY_SYSTEM_REBUILD_SPEC.md` at the repo root.
- [salesforce-qlcplus-integration.md](salesforce-qlcplus-integration.md) — controlling QLC+ from a **Salesforce LWC** over the internet: the bridge is built INTO QLC+ by adding TLS/WSS to its Web Access WebSocket API (no cloud relay). LWC contract + reference page at repo root: `../../SALESFORCE_QLCPLUS_INTEGRATION.md`, `../../test-ws.html`.
- [mayans-beam-color-rgb.md](mayans-beam-color-rgb.md) — user-confirmed RGB(0-255) for each BEAM230 color-wheel color name (White/Red/Yellow/Green/Orange good; Blue=15,15,255; Pink=255,5,140). Reuse for the planned RGB PAR fixtures.

**Entry point:** the project slash command **`/lightshow`** (`.claude/commands/lightshow.md`) is the operator's way to drive all lighting work — it loads the knowledge base below + the live `SaveFile/Main Project.qxw` and runs in modes `create` / `update` / `research` / `tune`. Keep it in sync if the rig, conventions, or memory layout change.

## Lighting design knowledge base — [lighting/](lighting/) (index: [lighting/README.md](lighting/README.md))

Curated base for programming world-class club shows and **auto-generating** new looks/effects on the
Mayans rig. Half (A) = exactly how QLC+ stores/does things (reverse-engineered from `engine/src`, so generated XML is correct); half (B) = pro lighting craft. Start at [lighting/effect-recipes-cookbook.md](lighting/effect-recipes-cookbook.md) to build something.
- [lighting/qlc-save-file-format.md](lighting/qlc-save-file-format.md) — exact `.qxw` XML for every Function type; the reference for generating/editing projects.
- [lighting/qlc-fixture-definition-format.md](lighting/qlc-fixture-definition-format.md) — exact `.qxf` format + full channel Preset/Group enum lists; how to author a fixture.
- [lighting/qlc-functionality-reference.md](lighting/qlc-functionality-reference.md) — what every Function, VC widget, Simple Desk, I/O, the Script language & RGB-matrix JS API do.
- [lighting/club-rig-mayans.md](lighting/club-rig-mayans.md) — the venue's REAL rig: fixtures, DMX addressing/universes, custom `.qxf`, 270-function inventory, naming conventions, Art-Net output, website (Web Access WS) control, VC layout. Reuse for any edit.
- [lighting/fixture-types-and-roles.md](lighting/fixture-types-and-roles.md) — spot/beam/wash/par/pixel-bar/strobe/FX/laser/haze: purpose, DMX channels, show roles, mapped to the rig.
- [lighting/lightshow-design-principles.md](lighting/lightshow-design-principles.md) — energy arcs, color, beat sync, layered looks, movement, the drop, busking (the why/when).
- [lighting/effect-recipes-cookbook.md](lighting/effect-recipes-cookbook.md) — concrete effects → exact QLC+ constructs on the real rig + genre playbooks + XML-generation cheat sheet.
- [lighting/cross-platform-concepts.md](lighting/cross-platform-concepts.md) — ONYX/grandMA/Avolites/MagicQ/Resolume concepts mapped to QLC+ equivalents and workarounds.
