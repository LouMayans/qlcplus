# Iteration 8: the unhandled "add swarm fixture" commands, and a command history (2026-09-28)

## What was asked
One command in the history wasn't handled; find it and handle it.

## What happened
The server keeps recent plans in memory only, so they were read back by plan ID. In the new show **Hello** the
operator typed:
1. "add 5 par lights to universe 1 and 2 to universe 2": handled (7 American DJ VPars, applied).
2. "add swarm fixture to project": the model was unsure (add a fixture 0.34 / propose a look 0.27) and lightai asked.
3. "add swarm fixture to universe 1": the model leaned to *move fixtures* (0.75) and lightai asked.

The rule that settles patch requests needed a number ("add 2 ...") and a universe, an address or the end of the
sentence. "add swarm fixture" has no number, and "to project" was neither.

## What was done
| Area | Change |
|---|---|
| Patch requests | No number means one: "add swarm fixture", "add a swarm", "add one more swarm". "to project", "to the show", "into the patch" end the fixture words like a universe does. Without a number the words must name a model you own ("swarm" -> Chauvet Swarm 5 FX); an alias doesn't count, so "add fog" is not read as "patch a fog machine". |
| History | Every command typed in the console is logged with its plan, and whether it ran and why not, to `C:\lightai-data\history.jsonl` (rotates at 5 MB). `http://127.0.0.1:8765/history` shows the last 50. |
| Training data | 10 new sentences for patch requests without a number and "to project" wording. |

## Results
- In Hello: "add swarm fixture to project" plans 1 x Chauvet Swarm 5 FX (Standard Mode, 9 ch) on universe 2 at 15-23,
  and "add swarm fixture to universe 1" plans it on universe 1 at 36-44. Both state the assumption: "the 'swarm' your
  shows use (2 in Main Project.qxw: Ceiling Truss Swarms, Stage Large Truss Swarms)".
- Unchanged: "add fog", "add strobe", "add blue to the washes" and "add the swarm chase to the look" are not patch
  requests; "move the swarm to universe 1" is still a move.
- Tests: 218 unit tests (9 new) pass.

## Open items
- "add some fog" and "more fog" are read as not a lighting request (the model, 0.97), and "give me some fog" asks.
  "fog on" and "fog at 50%" work. How much fog "some" and "more" mean is the operator's call.
