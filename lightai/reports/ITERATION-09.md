# Iteration 9: every fixture you have, by any of its names (2026-09-28)

## What was asked
When a fixture is named, lightai should understand every fixture it could be: "I said swarm instead of par and it
should have known the other local fixtures we have in the main folder."

## What was checked
A new test says every fixture the operator has in the ways an operator says it: the 11 models patched in the shows in
`SaveFile`, plus the own definitions in the repo's `Fixtures` folder and the QLC+ user folder (the Betopper L1015 and
LF2405 are in no show yet). Each name was tried as "add a ...", "add 2 ... fixtures to universe 1", "add ... fixture to
project" and "... at 50%": 139 sentences. Before the fixes, 103 were right.

## What was wrong, and the fixes
| Problem | Fix |
|---|---|
| Own definitions that no show uses were unknown ("add a betopper"). | Your fixtures are the models in your shows plus your own definition files. |
| A library model beat your own when the library had one clear match ("add a beam v2" -> Chauvet Intimidator Beam 140SR V2). | Your fixtures come first whenever they explain every word; a library model you name in full still wins ("add an american dj mega hex par"). |
| Spaced or versioned model names missed ("l 1015", "thin par", "beam230 v2", "swarm 5 fxs"). | Letters and digits are also matched run together; "v2" and "v3" count; "fxs" is plural. |
| The word splitter cut "beam230v2" into "beam230v" + "2", and a lone "2" matched "BEAM230 #2". | "beam230v2" is one word; a lone number never names a model and is never matched against fixture numbers. |
| Fixture groups only knew your names and zones ("chauvet swarms", "venue tetras", "rgbs", "beam v3" at 50% failed). | A group can be a model or a maker: every fixture of that model in the show. |
| The model split one name over two tags ("beam230" + movement "v3", model "mayans" + movement "wash"). | When the next tag's words make a more exact name of one of your models, the two are one name. "washes breathe" stays a movement. |
| "mayans" was read as "mayan" and matched no maker; "mayans" + "washes" as two targets meant every Mayans fixture. | Maker names keep their plural; a maker tagged on its own narrows the other target to its fixtures. |
| "add a wash", "add a swarm", "add a par" were "not a lighting request" (the model, 0.80-1.00 sure). | An add/patch verb with your fixture's name is a patch request when the model has no reading ("none"), and "fixture" or "to project" wording beats a sure but different reading ("add rgb fixture to project" was a widget). |
| A maker alone picked its most used model ("add a mayans" -> BEAM230). | It asks which: "Which fixture is 'mayans'? 'Mayans BEAM230', 'Mayans WASH', 'Mayans BEAM230 V3'?"; "add a betopper" asks L1015 or LF2405. |
| "mayans beam230 at 50%" was read as "create a look" (0.94). | A sentence that is only fixtures and a percentage, with fixtures that resolve, is a level change. |
| "vpars" meant the pars zone, which holds the ThinPAR too. | "vpar(s)" is the VPar model now (fixtures 16, 17, 28, 29); "pars" still means the zone. |

## Results
- The 139 sentences: all right. "beam230" and "beams" mean the spots zone (all 14 BEAM230s of every version) and "pars"
  the pars zone, as your zone aliases say.
- Tests: 346 unit tests (128 new, `tests/test_names.py`) and 2 live end-to-end tests pass; reference sentences 58 of 58
  and must-refuse sentences 30 of 30 with the new word splitter.
- 15 new training sentences (makers and model names as groups, own definitions by name); tonight's retrain learns them.

## Open items
- The Swarm 5 FX and the Generic RGB fixtures (LED walls, panels) have no dimmer channel, so "swarms at 50%" and "rgbs at
  50%" are understood but refused ("have no dimmer"). Dimming them would mean scaling their color channels.
- "add some fog" / "more fog" are still not understood (iteration 8).
