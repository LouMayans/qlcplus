# Iteration 5: patching features and an "open main show" button (2026-09-28)

## What was asked
- Add several fixtures in one sentence, placed at free addresses: "Add 3 vpar 7address type fixtures to universe 1".
- Move fixtures to other DMX addresses.
- A button in the booth console that opens the main project file in QLC+.

## What was done
| Area | Change |
|---|---|
| Add fixtures | New `count` word tag (labels v3) plus a fallback that reads "3", "three", "a couple of", "3x" from the words. Universe, address and mode are read from the words ("to universe 1" is universe 1, "7address type" is the 7-channel mode). The fixtures go back to back at the first free block that fits them all; an explicit start address is checked for overlaps. Names continue the model's numbering ("VPar 5", "VPar 6", ...). One write, one backup, one reload. |
| Library matching | A model filed twice in the library ("American DJ" and "American_DJ") is one candidate, and the model already patched in your show wins when matches tie. |
| Re-address | New command type `fixture_edit.readdress`: "move fixture 16 to universe 1 address 300", "move the vpars to universe 1" (first free block), "change the dmx address of wash 1 to 250". Overlaps are refused with the first free block that fits. Functions refer to fixtures by ID, so looks keep working. The plan reminds you to set the new address on the unit itself. |
| Open main show | QLC+ fork command `openProjectFile` (fork version 2): opens a show file by path, keeps its file name, refuses when QLC+ has unsaved changes unless confirmed. The console header shows which show QLC+ has open and an "Open main show in QLC+" button; when QLC+ is closed the button starts it with your usual launcher. Rebuilt warning-clean and installed in `C:\qlcplus` (backup `backup-before-lightai-fork-20260928-152553`). |
| Autostart | `C:\qlcplus\start-qlcplus.bat` opens the show named in `show-path.txt` (the repo's `SaveFile\Main Project.qxw`). |
| Training data | 42 hand-written sentences, 8 grammar templates, 2 golden cases (a counted add and a re-address); dataset 7,183 sentences. |

## Results
- Model v7 (labels v3, 5 epochs, 7,183 sentences) promoted and running in the server: dev intent 0.997, slot F1 0.990,
  golden 58 of 58, must-abstain 30 of 30, parse 3.7 ms median / 9.4 ms p95.
- Tests: 167 unit tests pass, plus 2 of 2 live end-to-end tests on an isolated QLC+ (port 9998, fresh test project),
  on both the installed `C:\qlcplus` build and the dev fork build.
- Isolated real QLC+ (fork and installed copy): `openProjectFile` 7 of 7 checks; engine fixes still pass.
- 23 patch sentences through the full pipeline on the real show: all right after the fixes below.

## Problems found during the checks, and fixes
| Problem | Fix |
|---|---|
| First v7 evaluation: golden 56 of 58. The raw model read a bare "blackout" as *lights back on* (0.74) and "lights on" as *blackout* (0.87). | One- and two-word critical commands ("blackout", "full blackout", "lights on", "lights back on", "release") are exact-phrase rules now and never depend on the model. 20 contrast sentences added to `seed.txt` for the next retrain. |
| "move the vpars to universe 1": the model tagged only "the" as the fixture. | When a move has no usable fixture, the words between the verb and "to / at / address" are read as the fixtures ("vpars"). |
| "change the dmx address of wash 1 to 250" was refused (correctly: it overlaps BEAM230 #12 at 241-256 and BEAM230V2 #13 at 257-272), but the hint suggested address 129, where Wash #1 already is. | Automatic placement and the hint never pick a moved fixture's current spot; it now suggests 289. An address you give may still overlap the old spot. |
| "move wash 1 to universe 1" when it is already there moved it anyway. | It now says where the fixture is and asks for the new address. |
| The live test assumed `C:\qlcplus` holds stock QLC+; it holds the fork build now. | The test asks the running instance for its fork version. |

## Open items
- The 20 short-command sentences are not in v7 yet. The nightly job retrains after at least one new confirmed or
  corrected command (or run `lightai nightly --force`). The exact-phrase rules cover those commands in the meantime.
- The iteration-5 changes are not committed yet.
- Moving a fixture to another universe is refused if its output is not patched there; the plan warns about it.
