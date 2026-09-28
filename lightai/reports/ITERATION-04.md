# Iteration 4: fix every round-2 finding, retrain (2026-09-26)

## What was asked
- Keep iterating until lightai is fully functional, validated by independent sub-agents; keep responses under 100 ms;
  report each iteration.

## What was done
All findings of the three round-2 agents (`C:\lightai-data\agents\{qlc-r2,nlu-r2,api-r2}\REPORT.txt`) were fixed,
except three that are design decisions for you (see the end).

| Area | Fix |
|---|---|
| Safety of instant commands | Blackout, lights back on, stop everything and release never act on negated, future, conditional, question, reported, "prep", off-topic or partial-rig wording, at any model confidence ("blackout at the drop", "should we blackout?", "don't stop everything", "kill the lights in the bathroom"). A tempo is not set on "do not set the tempo to 90". A level with no fixtures only moves the grand master when you say "grand master" ("battery at 5%" asks). Two commands in one sentence ask to go one at a time. "full kill on the rig" asks "full or off?". The console only fires these on Enter when the model is at least 90 % sure and nothing was guessed. |
| Looks and names | "at p20" is a priority, never 20 % brightness; "p100" / "priority 20" never become a function or fixture 20. "start ... mirrored" starts the mirrored look (an exact full name wins over a cut-short one). Duplicate names are listed with type and ID, and delete / update / add-button ask which ID. Two zones with two colors are paired in the order you said them. Names now include the level and fade ("Red Spots Wash 50%"); a strobe name no longer claims a BPM it ignores. |
| Rotation and moves | "counter clockwise", "counter-clockwise", "anti clockwise" and "to the left" rotate counter-clockwise. A no-op rotation (720°) does not rewrite or reload the show. A move stopped at the map edge says so in the summary. |
| Updating looks | "bigger", "tighter", "dimmer", "a bit brighter", "speed up", "slow down" change a saved look relative to its own values; "speed up the X" updates the look instead of starting it. |
| Feedback learning | Color complaints read which color was asked for and which was seen ("i said yellow but they're white", "X instead of Y", "more X than Y"); a complaint where both are the same teaches nothing. A complaint on a look mixing BEAM230 models asks which fixture instead of changing all three models. Venue chatter ("the bass is too loud") is no longer feedback on the last look. Planning "too fast" no longer changes the proposal until the feedback runs. A double click counts once, but a deliberate repeat later counts again and a failed save can be retried. Unknown fixture numbers are rejected. |
| QLC+ reloads | A reload restarts only top-level looks, so stopping a look by name stops all its parts. A look that is in the file but not loaded in QLC+ (for example after a blocked reload) is reloaded on the confirmed retry. After a reload, the tapped tempo is re-applied to running looks. A reload that cannot reach QLC+ is reported as not done. |
| Overrides and outages | Priority looks (P20, P100 kill) that hold the channels are named when you set levels or preview, and calibration refuses to start on a fixture such a look holds (QLC+'s Simple Desk writes at priority 0). Overrides that could not be released while QLC+ was gone are released as soon as it is back. With QLC+ down, commands fail within milliseconds for 3 s instead of queueing 4 s attempts. "release everything" resets only the channels that are overridden (it was 322 ms). A second calibration ends the first; an answer racing /stop no longer errors; Ctrl+C ends a calibration. |
| Server | Requests over 256 KB get 413; cross-site reads are refused; the function search is bounded; NaN or Infinity input gives 422 instead of 500; the token check is constant-time and the console removes the token from the address bar. The usage log no longer duplicates rows after an outage. A model swap loads off the event loop, a broken promotion is skipped instead of stopping the background task, current.txt is written atomically, and an incomplete current.txt falls back to the newest complete model. Plans expire least-recently-used. Tap tempo folds into 40-220 BPM (double or half time). |
| Console | Calibration hide timer, "Reload anyway" flow, no blocking alert() boxes, re-joining a split tag, no stale `fixture_edit.readdress` entry. |
| Training data | 91 validated sentences and 22 grammar templates from the language agent (turn + color, movement verbs, rotation sense, two-role color feedback, chatter with percentages, update comparatives, color-named functions, toggles); "everywhere" is the whole rig. |

## Results

**Language model v6 promoted** (5 epochs, 6,766 training sentences; dev intent 0.991, slot F1 0.969, golden 56/56
including the plan check, abstain 30/30, parse p95 12.3 ms).

Held-out suites (never trained on), measured with the language agent's corrected harness, "fully right" = intent,
every slot and the plan all correct:

| Suite | v5 (iteration 3) | v5 + agent patch | v6 + iteration 4 |
|---|---|---|---|
| Round-1 suite (438 sentences) | 83.1 % | 84.2 % | **87.4 %** (intent 96.1 %) |
| Round-2 suite (335 new sentences) | 71.9 % | 82.4 % | **84.2 %** (intent 91.9 %) |
| Iteration-3 fix checks (72) | 66.7 % | 86.1 % | **95.8 %** |

For comparison, iteration 2 started at 59.8 % on the round-1 suite.

**Safety probe** (3,409 adversarial sentences: "blackout at the drop", "should we blackout?", "don't stop everything",
"stop all the drinks at the bar", ...): high-impact plans that would act dropped from **1,639 to about 150**. What still
acts is mostly "... for the encore" (read as a present command) and release commands aimed at specific fixtures,
which are legitimate. "stop all the chatter", "black out the windows" and "stop everything on the bathroom lights" now
ask. Plain commands ("blackout", "kill the lights", "lights back on", "stop everything", "bpm 128", "the dj is playing
124") still act at once.

**Tests:** 141 passed (108 at the end of iteration 3), plus the 2 live-QLC+ tests.

## Final checks
The round-3 agents were cut short by a usage limit, so the last checks were scripted instead (no more agents):

| Check | Result |
|---|---|
| Unit + fake-QLC+ tests | 141 passed |
| Live tests on real isolated QLC+, stock and fork (`LIGHTAI_E2E=1`) | 2 passed |
| Golden / must-abstain gates on v6 with the final code | 56/56, 30/30 |
| Real fork, isolated (`tools/check_live_fork.py`): reload restarts only the look; stopping it by name stops every part; a blocked reload is loaded on the confirmed retry; "release everything" fast; three parallel previews and a replaced calibration leave nothing overridden; a P100 kill is named when setting levels | 8 of 8 passed |
| Engine fixes (`tools/check_engine_fixes.py`) | fork passes, stock fails (unchanged since iteration 3) |

Live latency on the real fork, plan + execute (n=15):

| Command | p50 | p95 |
|---|---|---|
| washes at 40% | 4.6 ms | 5.3 ms |
| set channel 9 on fixture 34 to 128 | 4.3 ms | 5.2 ms |
| blackout / lights back on | 3.6 ms | 5.3 ms |
| release the washes | 3.9 ms | 4.3 ms |
| what's running | 5.5 ms | 6.0 ms |
| release everything (was 322 ms) | 6.7 ms | |
| create a look: write + reload | 298 ms | |

## Decisions for you
- **Simple Desk vs priority looks.** QLC+'s Simple Desk writes at priority 0, so while a P20 look or the P100 kill runs,
  manual levels, previews and calibration on those channels do nothing on stage. Your priority spec leaves the Simple
  Desk at 0. lightai now warns and refuses calibration there. If you want manual overrides to win over every look
  (like a console's programmer), that is a one-line engine change (`setPriority2` on the Simple Desk fader).
- **Fade-out of a priority look.** When a P20 look with a fade-out stops, it fades to black and then the P0 look
  underneath snaps back, because the lower look can't write until the fade ends. A smooth crossfade needs the engine to
  fade priority faders toward the lower look's values.
- **Rare start race.** When another client stops a look at the same moment lightai starts it, lightai can report
  "Running" for a look that then stops (2 in 40 trials). Re-reading the status would add about 60 ms to every start.
