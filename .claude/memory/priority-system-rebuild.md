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

## STATUS: IMPLEMENTED (2026-06-17, on `priority-rebuild`)

The full spec was implemented on `priority-rebuild` (NOT yet committed — sits as working-tree changes across 43 files). Whole project builds **426/426, warning-clean** under `-Werror -Wextra -Wall` (gcc 14.1 / Qt 5.15); installs to `C:\qlcplus` and launches standalone. Engine `universe_test` (incl. a new `writePriority()` case) and `function_test` pass; all VC/engine tests for the changed logic pass. Remaining engine/UI test failures are **pre-existing environment/CWD issues** (fixture `FixturesMap.xml` loaded via a CWD-relative path, Windows `/home`→`C:/home` path normalization, missing input-profile/plugin dirs) — verified by stashing all changes and reproducing `doc_test`'s 2 failures at the clean baseline, and by the fact that untouched tests fail identically.

Key implementation decisions / deviations from the spec (all deliberate):
- **Sentinel renamed** `NO_PRIORITY` → `NO_CHANNEL_PRIORITY` in universe.cpp: `NO_PRIORITY` collides with a Windows macro pulled in via `windows.h` (compile error). Value is `INT_MIN` (so negative user priorities still beat "unwritten").
- **`Universe::write()` signature**: priority added as the **last** param `write(int, uchar, bool forceLTP=false, int priority=0)` — NOT before forceLTP as the spec sketched. This preserves every existing positional `write(addr,val,true)` caller (a `bool` would otherwise silently bind to `int priority`). `writeMultiple`/`writeBlended` take priority as a trailing default, matching the spec.
- **VC slider non-overriding priority = `0`, not `-1`** (vcslider.cpp `writeDMXLevel`/`adjustIntensity`): `m_isOverriding ? louPriority() : 0`. Using `-1` would make every plain level slider lose to all priority-0 functions even with no priorities set, breaking the backward-compat guarantee. Spec §5.5 flagged this and offered `0`.
- **CueStack owner wiring is N/A in current upstream**: `VCCueList` drives a **Chaser** (`m_chaserID`), not a CueStack, so cue-list priority comes for free from each member scene's own `Function` priority. `CueStack` is owned only by **SimpleDesk** (`SimpleDeskEngine::createCueStack`); per the spec decision it stays at default priority 0. `CueStack` still got its own `m_priority`/`setPriority2`/`priority2` + fader stamping (defaults to 0) so it's wired if ever needed.
- **Naming**: `Function` uses `getPriority`/`setPriority`/`m_priority` + `priorityChanged(quint32)`; `GenericFader` & `CueStack` use `setPriority2`/`priority2`/`m_userPriority` (avoid clash with the existing fader-priority enum); VC widgets use `louPriority`/`setLouPriority`/`m_louPriority`.
- **Tree refresh on priority change**: `Doc::addFunction` connects `Function::priorityChanged` → existing `slotFunctionNameChanged` (refreshes the manager tree row) — the decoupled approach (bug #11), not the old `nameChanged` hack.
- **Function-manager tree**: 2 columns `Function | Priority` (the spec's `Visible?` column was dropped per user request, 2026-06-17). `COL_NAME 0`, `COL_PRIORITY 1`, hidden data-only `COL_PATH 2` (fixes the old COL_PRIORITY/COL_PATH collision, bug #8). Header sized so Function stretches and Priority is ResizeToContents (`setStretchLastSection(false)` + per-section resize modes in `FunctionManager::initTree`). Hidden-function filtering left as upstream (the "show hidden" change is optional and was NOT applied).
- **XML**: function `Priority` attr written only when non-zero (matches blendMode/path convention); VC widget `Priority` attr likewise. Old workspaces load byte-identical.
- **OSC -1 feedback path** fully wired: packetizer keeps float `-1.0` as byte `-1`; `OSCController::handlePacket` splits `-1` to a new `valueFeedback` signal (also fixed the multi-value drop quirk); plugin → `InputPatch` (separate `m_inputFeedbackBuffer` + `slotValueFeedback` + flush) → `InputOutputMap`/`Universe` `inputValueFeedback` → `VCWidget::slotInputValueFeedback` (virtual, no-op default) → `VCButton` override calls `updateFeedback()` only.
- **Restart action + external-0-stops**: `VCButton::Action` gained `Restart`; `slotInputValueChanged` for Toggle/Restart now starts on >0 / stops on explicit 0 (Blackout/StopAll unchanged). `vcbutton_test::input()` was updated to assert the new external-0 behaviour (the old assertion encoded the pre-feature behaviour).
- All 12 known bugs from the spec avoided (uninitialized priorities fixed, zero-target cleanup in `GenericFader::write` kept intact, per-channel stamping in `writeMultiple`, one sentinel, no dead VLA/`?:`).

## Decisions after the build (2026-06-17)
- **Button-controlled priority: considered and REJECTED.** Idea was to let a VC Button impose its own priority on the Function it starts (so a scene runs at the button's priority while pressed). Decided to **keep priority on the scene** instead: a scene's priority reflects its role and a button just starts/stops it; if a scene needs to "win", give it a high priority (or make a dedicated override scene). Rationale: single source of truth, no hidden button-dependent state, no multi-controller ambiguity — the same class of bug that plagued the original implementation. Do not re-add this without a new explicit request. (The `VCWidget` base still has `louPriority` from the slider work; it is just not consulted by buttons.)
- **All explanatory comments added during the priority work were stripped** at the user's request (see per-user memory `code-comment-preference`). The code/behaviour is unchanged; future edits here should stay comment-free.
