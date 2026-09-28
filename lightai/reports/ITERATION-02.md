# Iteration 2: validate, harden, and finish phase 1.5 + phase 2 (2026-09-26)

## What was asked
- Test against a **new, separate QLC+ project**, never the live show.
- Run more tests with coverage. Use **sub-agents** that each validate a different part.
- **Iterate until fully functional.** Keep responses **under 100 ms**. Report each iteration.

## What was done
| Area | Change |
|---|---|
| Test isolation | Dedicated test project `lightai-test-rig.qxw` (Blank Rig Template, no functions, no DMX I/O) for every QLC+ test. The harness refuses port 9999. QLC+'s registry recent-files list is snapshotted and cleaned after each test instance, including when several overlap. |
| Validation agents | Three independent agents: language + planner (300+ fresh sentences), QLC+ integration (every recipe checked channel by channel on an isolated QLC+), API + console + learning loop. Findings are in the next section. |
| Fake QLC+ | `tests/fakeqlc.py` mirrors the WebSocket protocol, including unknown-command echoes, push-only replies and the fork commands, for fast deterministic journeys. |
| Fork (phase 1.5) | New WebSocket commands built warning-clean under `-Werror` (gcc 16): `lightaiVersion`, `getProjectFile`, `loadProjectFile`, `saveProject`, `get/setFunctionSpeed`. **18/18 checks passed** on an isolated fork instance. The reload by file keeps the file name and never opens a dialog (the stock file-open path can show a blocking warning box). Test install: `C:\lightai-data\qlcplus-fork`. Production install script: `lightai/tools/install-fork-build.ps1` (not run). |
| Live tempo | "bpm 120" retimes **running** AI looks live through `setFunctionSpeed`, with no reload: measured **10.7 ms** on the fork build. |
| Phase 2 intents | `update_look` (in place, or replaced with buttons rebound), `delete_look` (AI looks only; warns if your functions use it), `add_widget` (Toggle button in a "lightai" VC frame), `add_fixture` (fuzzy match against all 1,730 library fixtures, mode choice, free-address search, overlap check). Label set v2 (24 intents, 19 slots). |
| Correction loop | "too fast" / "bigger" / "too dim" now also fixes the look it was about. A proposal is adjusted before *Keep it*; a saved look gets a ready update plan with an **Apply to the look** button. |
| Calibration | New RGB mode: "find blue on fixture 21" lights each color channel alone and learns swapped channels. Wheel answers accept free text ("no, that's hot pink"). |
| Audit | Discovers **color-wheel slots your function names reveal** that the `.qxf` doesn't list (BEAM230: 16 "black blue", 48 "purple", 72 cyan, 96 "orange fanta", 104 "emerald"), with a one-line command to confirm each. The V3 repair translates by color name and skips colors the V3 wheel isn't known to have. |
| Robustness | Executor failures now return errors instead of crashing the API. The default reload strategy is `auto`. Fire-and-forget commands end with a read-back barrier, so results are confirmed. The API closes its QLC+ connection on shutdown. File handles are closed. Corrections survive label-set additions. Weak function matches ask before updating or deleting. |
| Docs | `lightai/README.md`, project memory `.claude/memory/lightai.md`, corrected Monitor placement note, corrected V3 color channel in `/lightshow`. |

## Results
- **Tests: 91 passed** (plus 2 live e2e runs against real isolated QLC+, stock and fork, both passing). Coverage rose from **49 % to 75 %**.
- Parse + plan latency after moving the look preview to on-demand (other agents were loading the CPU at the same time):

| Intent | total p50 | total p95 |
|---|---|---|
| create_look | 4.0 ms | 5.2 ms |
| propose_look | 7.6 ms | 10.9 ms |
| rotate fixture | 3.6 ms | 5.4 ms |
| set_level | 3.3 ms | 4.2 ms |
| blackout | 2.2 ms | 2.8 ms |
| run_function | 4.8 ms | 6.3 ms |

- Live execution (fake QLC+): single commands take a few ms including the confirmation read-back. A structural reload on the fork build takes about 0.25 s, 0.46 s including the file write.

## Validation agents' findings
Three agents tested separate parts in isolation. Nothing touched the live show or port 9999. Full reports and scripts are in `C:\lightai-data\agents\{nlu,qlc,api}`. All fixes landed in iteration 3.

**Language + planner agent** (438 fresh sentences it wrote, held out from training):

| Measure | v2 model, before | with its proposed fixes |
|---|---|---|
| Intent right | 86.8 % | 88.4 % |
| Every slot right | 68.3 % | 78.1 % |
| Plan is the right plan | 74.7 % | 87.7 % |
| Fully right (intent + slots + plan) | 59.8 % | 74.7 % |

Main misses: "turn the spots green" read as a function name, imperative verbs ("strobe the spots") without a movement, "p100" read as 100 BPM, "breathe every 2 s" giving a 4 s breath, and weak blackout wording. It proposed 119 new training sentences and a patch.

**QLC+ integration agent** (every recipe checked channel by channel on an isolated QLC+ with the test rig): 20 defects.
- **D1, critical:** in every spot movement look, 7 of 14 spots stayed at pan/tilt 0. A QLC+ engine bug switches a shared fader to 8-bit mid-start when a BEAM230 V3 (non-adjacent fine channels) joins.
- **D2, high:** QLC+ drops zero-intensity channels, so a P100 kill does not hold dimmers at 0 and a P20 look lets lower looks' colors leak.
- **D3, high:** "start <look>" could start a sub-scene with the same name prefix.
- D4-D20: release vs stop confusion, color-wheel fallbacks, chase padding one color too many, clamped moves reported as full distance, names without "Mirrored", release latency on a visible Simple Desk page, and others.

**API + console + learning agent** (real server, headless Chrome, isolated QLC+ on port 9995): 0 critical, 2 high, 10 medium, 11 low.
- **H1:** three previews started at the same moment left Simple Desk overrides stuck.
- **H2:** the console's "Apply to the look" button sent a false "wrong" verdict instead of applying.
- **Medium:** QLC+ dropping turned /stop into a 500 and left a calibration on; "that's pink" during "find blue" was read as yes; /feedback took 100-600 ms and blocked live commands; unknown feedback values reversed the learning; no Host-header check (DNS rebinding).
- Every live operation measured **well under 100 ms** (sentence to DMX p95 19-44 ms). The exception was /feedback, which was fixed in iteration 3.

## Production QLC+ stopped (resolved)
At 17:31 the production QLC+ (port 9999) was no longer running. You confirmed you closed it on purpose, so
there was no incident. As a result of the check, the test harness now refuses to start any instance on
port 9999, as an extra guard.

## Next
- Fix the agents' findings, retrain (v3 is training now with the new intents; v4 will add the agents' sentences), and re-run the agents on the fixes.
