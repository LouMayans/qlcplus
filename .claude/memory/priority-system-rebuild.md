---
name: priority-system-rebuild
description: QLC+ function-priority feature — plan to re-implement cleanly on a fresh upstream fork
metadata: 
  node_type: memory
  type: project
  originSessionId: 5dda5587-cb25-4c41-beb4-145ff75c5d67
---

User (lighting designer, runs QLC+ classic Qt UI on Windows/MinGW, uses OSC + Virtual Console) built a per-function **priority system** on their `LouMayans/qlcplus` `master` branch but it's buggy and they want to rebuild it from scratch on a fresh fork of `mcallegari/qlcplus`.

Full rebuild spec written to repo root: `PRIORITY_SYSTEM_REBUILD_SPEC.md` (created 2026-06-17). It is the authoritative reference for the "build it later" request.

Branch `priority-rebuild` (created 2026-06-17, commit e06be78d4) is the clean rebuild base: it sits directly on the latest upstream `mcallegari/qlcplus` master (`b625e9d8f`, 2026-06-15) and carries only the spec + the `how to` file. The old buggy work stays on `master` as reference. `upstream` remote = https://github.com/mcallegari/qlcplus.git. NOT pushed to origin yet. IMPORTANT: upstream is **1376 commits ahead** of the original fork point (78c165e94), so the rebuild must adapt the spec to *current* upstream code — the old file line numbers/structure will have moved.

Key facts:
- Branch forked from upstream at commit `78c165e94` (2024-09-03). All local work is in `78c165e94..HEAD`.
- The 158-file diff is ~95% noise (translation `.ts` files, `.history/`, fixtures, build/CI, CRLF churn, qmlui line-ending churn, stray qDebug). Real feature ≈ 18 files, all in `engine/src` + classic `ui/src` (the QML UI was NOT touched for this feature).
- Core mechanism: `Function` gets int `priority` (XML attr "Priority", default 0); `Universe` keeps `m_channelLouPriority[]` parallel to `m_preGMValues`, reset each tick in `processFaders()`; `write/writeMultiple/writeBlended` take a priority arg and reject writes whose priority `<` the channel's current owner (higher priority overrides HTP/LTP; equal falls back to HTP/LTP). `GenericFader` carries the function priority and passes it down. Propagation wired in scene.cpp + efx.cpp (rgbmatrix/cuestack NOT wired — a gap).
- Two SEPARATE optional features got tangled in: VC Button "Restart" action, and an OSC `-1` "feedback-only" parallel signal path (`valueFeedback`/`inputValueFeedback`) + "external 0 stops the function". User confirmed (2026-06-17) BOTH of these are to be KEPT in the rebuild.
- Decisions confirmed by user (2026-06-17): priority must ALSO apply to **RGBMatrix** (it's a Function — wire `getFader()` + add a spinbox to the RGB Matrix editor) and **CueStack** (not a Function — give it its own priority field sourced from its owning VCCueList/SimpleDesk widget). Spec §4.4/§5.6 updated accordingly (commit cadd31f41).
- Documented 12 known bugs to avoid — most important: uninitialized `m_louPriority` in GenericFader & VCWidget; and the commented-out zero-target channel cleanup in `GenericFader::write()` (the "0 values still went through" hack) which leaves zombie FadeChannels and is the likely root of most bugs.
- Build: MSYS2 MinGW 64-bit shell, `make install` → installs to `C:\qlcplus`.
