# Iteration 1: build the working base (2026-09-26)

## What was asked
- Plan a personal, trainable, **non-generative** lighting AI (DistilBERT-class) for the Mayans QLC+ rig: typed commands become JSON that a program applies to QLC+ (file edits or live commands), validated to run natively on this laptop with millisecond response times.
- Your answers: first win = **generate a new look from a sentence**; typed input on the booth laptop; learn from corrections, live usage and other people's shows; new looks are made before the night or between sets; **manual BPM tap** instead of audio; no video or audio analysis for now; "better club" = variety with consistency.
- After the plan: the model must be able to **make an assumption, act on a fixture, and be corrected** ("wrong color", "wrong model") and learn from that. Everything must be free.
- During the build: test against a **new, separate project**, run more tests with coverage, use **sub-agents** to validate different parts, **iterate until fully functional**, keep responses **under 100 ms**, and write a report like this one after every iteration.

## What was done
| Area | Result |
|---|---|
| Environment | Python 3.12 + venv at `C:\lightai-env\venv`; data, models and logs in `C:\lightai-data` (outside OneDrive). |
| Benchmark (phase 0) | DistilBERT INT8 encoder p95 9.5 ms; MiniLM retriever p95 3.9 ms; QLC+ WebSocket round trip p95 0.3 ms. All gates passed. |
| Live QLC+ check | Read-only smoke test against the running QLC+ (TLS, 223 functions, 228 widgets). Nothing that changes lights was ever sent to it. |
| Rig model | Roles for all 36 fixtures derived from the `.qxf` files, the Blank Rig Template channel groups and operator facts. Matches the club notes, including the three BEAM230 variants and V3 color on ch8. |
| Look compiler | 11 recipes: color wash, breathing, color chase, running light, circles, circle wave, ballyhoo, figure 8, sweep, strobe, kill. Validated XML, backups, atomic writes and a sidecar of generated looks. Byte-identical round trip of the show file. |
| Language model | Joint intent + slot DistilBERT: 20 intents, 17 slot types, about 4,600 training rows (hand-written seeds plus grammar over the real rig vocabulary). v2 is promoted: dev intent 98.7 %, slot F1 0.967, golden 52/52, abstain 30/30. |
| Understanding | Normalizers for fixtures, zones, stage positions, colors, BPM, fades, angles and distances. Repair rules and an off-topic guard. The policy asks instead of guessing. |
| Planner and executor | Every intent maps to concrete actions: live WebSocket, Simple Desk previews, calibration, file edits + reload, feedback. |
| Learning | Rig facts (`learned.yaml`, undoable), taste model (scikit-learn), language corrections, and a nightly job (`lightai nightly`). |
| Interfaces | CLI (`repl`, `parse`, `do`, ...), local HTTP API on 127.0.0.1:8765, booth console page with BPM tap, outcome buttons and a teach editor. |
| Tools | `lightai audit` (found 22 scenes and 6 channel groups driving the V3 beams on wrong channels) and `lightai fix v3-color` (dry run by default, not applied). Show mining + priors, fixture taxonomy, and `lightai transplant`. |
| Security | `deploy\start-qlcplus.bat` now passes `-wa`, so web auth is enforced when `webpass.txt` exists (it was silently off). |
| QLC+ fork | New WebSocket commands: `lightaiVersion`, `getProjectFile`, `loadProjectFile`, `saveProject`, `getFunctionSpeed`, `setFunctionSpeed`. Building now. |

## Results
- Unit tests: **58 passed** (plus 1 opt-in live test). Line coverage **49 %** (planner, executor, API server, CLI, audit and mining still barely covered).
- End-to-end on an **isolated QLC+ instance** (port 9998, show copy with no DMX I/O): created *slow blue wash breathing at 60 BPM*, reloaded in 0.42 s, started and stopped it, checked DMX values, and a Simple Desk probe set and released cleanly.
- Parse + plan latency, measured while the compiler build and three agents were loading the CPU:

| Intent | parse p50 | plan p50 | total p50 | total p95 |
|---|---|---|---|---|
| create_look | 6.7 ms | 24.6 ms | 36.7 ms | 66.8 ms |
| propose_look | 5.8 ms | 8.3 ms | 15.6 ms | 46.2 ms |
| rotate fixture | 5.6 ms | 1.0 ms | 6.4 ms | 31.8 ms |
| set_level | 4.2 ms | 0.1 ms | 4.3 ms | 18.0 ms |
| blackout | 4.6 ms | 0.1 ms | 4.8 ms | 20.8 ms |
| run_function | 11.3 ms | 0.1 ms | 11.4 ms | 34.3 ms |

## Problems found and fixed in this iteration
- **"wash" read as an effect** in the flagship sentence: new training templates plus a repair rule (wash + another effect + no target = the wash fixtures).
- **"mirrored blue" read as one color**, **"smooth" read as a speed**, **"looks good" read as an observation**: templates plus deterministic repair rules.
- **Off-topic questions** ("is the kitchen still open") read as status queries: more "none" examples plus a guard for unslotted commands with no lighting words.
- **Color feedback bug**: learning "value 88 is pink, not blue" deleted its own exclusion. Exclusions are now per color.
- **Training ran out of RAM** (1.0 GB free): added a low-memory mode (embeddings and lower layers frozen) and below-normal CPU priority so QLC+ keeps precedence.
- **The test instance polluted QLC+'s recent-files list**: the entry was removed, and the harness now snapshots and restores QLC+'s registry settings.
- **Tests now run against a dedicated new project** `lightai-test-rig.qxw`, never a copy of the real show.

## Next (iteration 2)
- Collect the three validation agents' findings (language, QLC+ integration, API + learning) and fix them.
- Build and test the fork commands on an isolated instance.
- Raise coverage with a fake QLC+ WebSocket server (executor, planner, API, audit, mining, nightly).
- Make the create_look preview lazy to cut plan time.
- Retrain v3 with the agents' failure sentences.
