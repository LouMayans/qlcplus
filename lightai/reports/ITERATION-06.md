# Iteration 6: assume the fixtures you own, several groups per patch, and the show QLC+ has open (2026-09-28)

## What was asked
With QLC+ showing a new, empty show, the operator typed "Add 3 american dj par lights 7 address to universe 1 and 4 to
universe 2". lightai asked which PAR model instead of assuming. It should assume the model the shows already use (the
American DJ VPar), learning from the main project and the other shows.

## What happened (from the log)
- The console saved the sentence to `corrections.jsonl` at 19:57 UTC. The parse was: count 3, model
  "american dj par lights" (0.62), mode "7 address", address "universe 1 and 4", address "universe 2".
- "american dj par" matches 30 or more American DJ PARs equally well in the fixture library. The VPar scored lower (the
  word "par" sits inside "VPar"), and the "prefer the model in your show" rule only looked at the top five, so it asked.
- "and 4 to universe 2" was dropped: a command had one count and one address.
- Had it been applied, it would have changed `Main Project.qxw` and loaded it over the new, empty show. The check for
  "another show is open" ran after the write, and an unsaved show was not detected at all.

## What was done
| Area | Change |
|---|---|
| Your fixtures | New `lightai/rig/house.py` lists the models in your shows: the main show first, then the other shows in `SaveFile` (autosaves and backups skipped, each file cached). A vague model ("pars", "american dj par", "moving heads", "beams", "spots") becomes the model your shows use most, matched by maker, model name, the names you gave the fixtures, the rig's zones, or the fixture type. The plan states the assumption and lists the other models you own that also match. A model you name exactly is never replaced. A maker you don't own ("chauvet par") still asks, now offering real Chauvet PARs. |
| Mode | Without a mode, the mode your other fixtures of that model use (VPar: 7 Channel) instead of the largest. |
| Groups | "3 ... to universe 1 and 4 to universe 2": one count and one destination per group, each group in its own free block, groups never overlap each other, and IDs and names continue across groups. |
| Short patch requests | "add 2 pars" (the model was 0.30 sure), "add 1 par to universe 2" and "hang 2 moving heads on universe 1" (the model was 0.97 sure it was a move) are patch requests when an add / patch / hang / install / mount verb and a count come with a model you own, followed only by a universe or address. Confident other readings are kept: "add 2 washes to the look" stays a widget request and "add 20% to the pars" stays a level. A function name tagged inside a patch request is ignored. |
| Plural models | "pars" finds library models named "Par ..." ("plus" stays "plus"). |
| Another show open | Checked before anything is written. If QLC+ has another show open, or a new, unsaved one, a structural change stops with "Nothing was changed", and the console offers *Apply to the main show and open it in QLC+* (it says when that show has unsaved changes). The plan warns about it up front. Once confirmed, QLC+ switches with `openProjectFile`, and nothing from the other show is restarted. |
| Your saved correction | The row you saved labeled "universe 1 and 4" as one address. It now has two groups (backup `C:\lightai-data\corrections.jsonl.bak-20260928-162042`). It counts as a new example, so tonight's 05:30 job retrains and promotes only if every gate passes. 18 new training sentences cover groups and short patch wording. |

## Results
- Your sentence now plans 7 x American DJ VPar (7 Channel): 3 on universe 1 at 289-309 and 4 on universe 2 at 130-157
  (IDs 36-42), with the warning that QLC+ has a new, unsaved show open.
- Tests: 190 unit tests (24 new) and 2 live end-to-end tests pass.
- Isolated real QLC+ (installed fork build, port 9994, fresh test shows): 9 of 9 checks. It refuses while another show is
  open and leaves the file untouched; once confirmed it applies the change and QLC+ switches; QLC+ has the new fixtures
  patched; and no extra question comes once the main show is open.
- Parse and plan: 10-30 ms. The first patch request after a restart takes about 55 ms because it reads the other shows once.

## Open items
- lightai edits one show, the main show. Working on whichever show QLC+ has open would need the learned rig facts
  (zones, aliases, stage order, the kill function) kept per show. Not done.
- "add 3 strobes" still asks: you own no strobe model, and it may mean console buttons.
- The iteration 5 and 6 changes are not committed.
