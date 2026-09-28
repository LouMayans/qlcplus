# Iteration 7: a New show button, and lightai edits the show QLC+ has open (2026-09-28)

## What was asked
- Would adding fixtures while a new project is open overwrite the main project file? Does QLC+ or lightai know when a
  show isn't saved, and can it add fixtures to the open one even if it isn't saved, or should it be saved first?
- A console button that creates a new project and opens it, with a text field for the file name.

## Answers
- It never overwrote the main show with the empty one: it added the fixtures to `Main Project.qxw` and loaded the main
  show over the new one. Since iteration 6 it stops and asks first.
- QLC+ (the fork build) tells lightai which file is open and whether it has unsaved changes; a never-saved show has no
  file. lightai edits show files and has QLC+ reload them, and QLC+ has no command to add a fixture live, so a
  never-saved show can't be edited. Saving it alone didn't help either, because lightai only edited the main show. Now
  it edits whichever saved show QLC+ has open.

## What was done
| Area | Change |
|---|---|
| New show | Console header: a name field and a **New show** button. `POST /qlc/new-show {name}` creates `SaveFile\<name>.qxw` with the main show's universes, outputs and inputs and nothing else (no fixtures, functions or virtual console), opens it in QLC+ (starting QLC+ when it's closed, and asking first when QLC+ has unsaved changes) and switches lightai to it. Names: letters, digits, spaces and `- _ ( ) . , ' & +`, up to 60 characters; an existing name is refused. |
| Follows QLC+ | lightai edits the show QLC+ has open when it is a saved `.qxw` file. It checks when you plan a command (at most every 2 s) and when the console refreshes (every 15 s). A new, unsaved show can't be followed; the header then says in orange which file lightai edits. **Open main show in QLC+** switches both back. |
| Facts per show | The main show keeps its zones, aliases, stage order, kill and protected functions and per-fixture facts (they are keyed by its fixture and function IDs). Other shows get the facts about fixture models and the general rules only. A "fixture 5 is really a ..." answer is stored only for the main show. The looks lightai makes are recorded per show (`<show>.lightai-looks.json`), so a look that exists in the main show isn't skipped in a new one. |
| Stale plans | A plan remembers its show. Applying it after lightai switched shows is refused ("That was planned for X, but lightai now edits Y. Type it again."), because its IDs and addresses belong to the other show. |
| Fixture assumptions | In another show, the main show still counts first when guessing a vague model ("pars" -> American DJ VPar in a new, empty show). |

## Results
- Tests: 209 unit tests (19 new) and 2 live end-to-end tests pass.
- Isolated real QLC+ (installed fork build, port 9994, a fresh test show as the main show): 9 of 9 checks. The new show
  is created and opens in QLC+ saved and unmodified; lightai sees it empty; "add 2 pars to universe 1" patches two
  American DJ VPars into it, QLC+ reloads it and has them patched; the main show is untouched; and **Open main show**
  switches QLC+ and lightai back.

## Open items
- A show opened from another folder is followed too; its backups go to a `backups` folder next to it.
- The iteration 5 to 7 changes are not committed.
