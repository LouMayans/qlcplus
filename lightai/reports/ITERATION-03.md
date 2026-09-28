# Iteration 3: fix every validation finding, fix the QLC+ engine, retrain (2026-09-26)

## What was asked
- Keep iterating until lightai is fully functional, with sub-agents validating separate parts.
- Keep responses **under 100 ms** and write a report each iteration.

## What was done
Every finding from the three iteration 2 agents was fixed or answered. IDs refer to their reports.

| Area | Fix |
|---|---|
| QLC+ engine (fork) | **D1:** EFX 16-bit handling is now decided once per universe, so a BEAM230 V3 no longer freezes the other spots at pan/tilt 0. **D2:** zero values written with a priority (P100 kills, P20 overrides) are kept, so a kill really holds dimmers at 0. Built warning-clean. |
| Previews (H1) | Previews start and stop one at a time; the channel release survives a second cancel. Starting a preview ends a running calibration (L10). |
| QLC+ dropping (M-H3) | A lost QLC+ connection gives a clean 503 "QLC+ not reachable"; ending a calibration always clears it, even when QLC+ is gone. |
| Console | "Apply to the look" applies the update instead of sending a false "wrong" (H2). Ctrl+Enter re-plans edited text (M8). The tag editor never drops a label (M9). Empty fixture boxes are refused (L2). Calibration results stay visible (L3). Clarify text is escaped (L4). Validation errors are readable (L5). Delete, re-address and add-fixture ask for a browser confirm. Button errors show on the page. |
| Security (M10, L6, L7, L11) | Host header must be 127.0.0.1 or localhost (DNS rebinding). Cross-site POSTs are refused by their Origin. A non-loopback `--host` requires a token. Text, markup and answers have size limits. BPM must be 40-220. Teach rows must match their sentence. |
| Learning (M2, M4, M5, M6, M7, L8, L9) | "that's pink" during "find blue" records pink instead of confirming blue. Unknown feedback kinds, verdicts or directions are rejected, and a missing direction no longer reverses the learning. A color complaint no longer lowers the look's taste score. A ruled-out wheel slot is named as such. Speed feedback on a proposal also corrects its preview. Unusable correction rows are skipped. A double-click counts once. |
| Speed (M3) | Feedback only reloads the rig when a rig fact changed. A rig reload dropped from **~770 ms to ~25 ms** (fixture library, YAML and roles template cached by file time; retriever encoder and name vectors reused). Proposal previews are simulated when played, not when planned. |
| Language fixes (agent patch + D17-D19) | "turn the spots green" is a new look, not a function. An untagged movement verb still picks the recipe. "p100" is a priority, never 100 BPM. "wide" is a size. A movement span that swallowed a zone is split ("color chase spots and washes"). "move wash 1 left 50 cm" and "move fixture 3 up 30 cm" find their direction. "breathe every 2 s" is one 2 s breath. The exact function name wins, and duplicate names ask. Counter-clockwise rotation, unreadable or huge distances, and a period given as tempo are handled. |
| Looks (D17-D19) | A one-color chase gets exactly one house color. A move stopped by the map edge says how far it really went. Static looks have no speed in their name; mirrored looks are named "Mirrored". |
| Training data | 119 new sentences from the language agent (all validated), delete and move templates, sentence length raised to 64 tokens. The golden gate now also proves each golden sentence builds a runnable plan. |

## Results

**Language model v5 promoted.** v4 missed the slot gate after quantization (0.928 vs 0.93), so v5 was trained for 5
epochs and exported with per-channel INT8 weights and float output heads. Quantization now costs nothing
(INT8 0.970 vs float 0.969 slot F1).

| Gate | Needed | v5 |
|---|---|---|
| Dev intent accuracy | 0.97 | 0.990 |
| Dev slot F1 | 0.93 | 0.970 |
| Golden sentences (now also must build a plan) | 56/56 | 56/56 |
| Must-abstain sentences | 30/30 | 30/30 |
| Parse p95 | | 12.5 ms |

**Held-out suite** (the language agent's 438 sentences, never trained on):

| Measure | v2, iteration 2 start | v2 + agent patch | v5, iteration 3 |
|---|---|---|---|
| Intent right | 86.8 % | 88.4 % | 94.5 % |
| Every slot right | 68.3 % | 78.1 % | 85.8 % |
| Right plan | 74.7 % | 87.7 % | 86.5 %* |
| Fully right | 59.8 % | 74.7 % | 79.0 % |

\* The old harness still expects preview frames inside a proposal plan; they are now simulated on play, so 19
proposals are counted as wrong plans here. The round 2 agent re-measures with a fixed harness.

**QLC+ engine fixes verified** on isolated instances with a fresh test project, V3s deliberately started last:

| Check | Stock QLC+ | Fork build |
|---|---|---|
| All 14 spots move in a circle look | 12 of 14 stuck at pan/tilt 0 | all 14 move |
| P100 kill over a P0 wash | dimmers stay 255 | dimmers held at 0, back to 255 after |

QLC+'s own engine unit tests still pass with both changes: **210 passed, 0 failed** across efx (50, 1 skipped by
design), efxfixture 21, genericfader 6, universe 27, scene 19, chaser 23, chaserrunner 23, fadechannel 13,
mastertimer 12 and collection 16.

**Tests:** 108 passed (91 in iteration 2), plus 2 live-QLC+ tests that run with `LIGHTAI_E2E=1`. Coverage 75 %.
New regression tests cover concurrent previews, calibration answers, the confidence rule, repairs, the retriever,
naming, chase padding and rig-reload caching. Writing them exposed one more bug: several commands arriving together
each opened their own QLC+ connection and broke each other's requests. Connecting is now serialized.

**Speed:** rig reload went from 770 ms to 25 ms; feedback no longer reloads the rig unless a rig fact changed; a 14-spot
proposal is planned without simulating 160 preview frames.

**Nightly learning** is registered in Task Scheduler for 05:30 daily.

## Round 2 validation (three agents, isolated, port 9999 never touched)
Full reports: `C:\lightai-data\agents\{qlc-r2,nlu-r2,api-r2}\REPORT.txt`. Every finding is fixed in iteration 4.

**QLC+ integration:** both engine fixes hold on the fork. All 14 spots move with the V3s started last; 16-bit fine
channels are sane. The kill holds dimmers at 0 on spots and washes, and a P20 look shows exactly its own values.
31 of 31 looks across all 11 recipes are correct on DMX. Structural writes are about 8 times faster than in round 1.
Every live op is under 100 ms except "release everything" (322 ms). New defects:
- **High:** after a confirmed reload, stopping a look by name left its parts running (the restart gave them a second
  start source). After a live BPM retime, the next look was written but never loaded, and the retry reported success.
  "start ... mirrored" started the non-mirrored look. "at p20" made a P20 look at 20 % brightness.
- **Medium:** "counter clockwise" rotated clockwise; a cut-short model name patched the wrong fixture; one color
  complaint on a look mixing BEAM230 models changed all three models' facts; Simple Desk overrides lose to any
  priority look (by design of the priority system).

**Language (v5):** on the round-1 suite with a corrected harness, 83.1 % fully right (59.8 % at the start of
iteration 2). On 335 new held-out sentences, 71.9 %. Parse + plan p95 18-29 ms, none over 100 ms at normal load.
- **Critical:** qualified wording still acted: "blackout at the drop", "should we blackout?", "lights back on
  later", "don't stop everything". Chatter with a percentage ("battery at 5%") became a grand-master change.
- **High:** "i said yellow but they're white" was recorded backwards as an operator fact; comparatives on
  update_look ("bigger", "dimmer", "speed up") did nothing; two zones with two colors could be swapped.
- It supplied a tested patch (82.4 % on the new suite, 86.1 % on the fix checks, golden and abstain intact),
  91 validated training sentences and grammar templates.

**API and console:** H1 passed 70 of 70 real trials; H2, M1-M5, M7, M8, M10 and most low findings are fixed; feedback
now takes 28-62 ms (was 168-509 ms). New: the console still ran negated or future commands on Enter; a second
calibration left the first one's channels on; overrides were never released after a WebSocket-only drop; with QLC+
down every command took 4 s and queued; the usage log duplicated rows after each outage; a broken model promotion
silently stopped the background task; NaN input gave 500s; and a set of console and learning-loop details.
