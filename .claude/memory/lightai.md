---
name: lightai
description: The personal, non-generative lighting AI (lightai/) - layout, how to run/train/test it, operator testing rules, fork WebSocket commands, and gotchas
metadata:
  type: project
---

**What:** `lightai/` (Python 3.12 package, venv `C:\lightai-env\venv`, data `C:\lightai-data`) turns typed
booth commands into JSON with a fine-tuned DistilBERT (joint intent + BIO slots, INT8 ONNX, ~5 ms), then
plans and executes on QLC+ (WebSocket live ops, or `.qxw` edit + reload). User guide: `lightai/README.md`.
Iteration reports: `lightai/reports/ITERATION-*.md`. Built 2026-09-26 from the approved plan.

**Layout:** rig model `lightai/lightai/rig/` (roles from `.qxf` + Blank Rig Template channel groups +
`overrides.yaml`; learned operator facts `learned.yaml`), compiler `compiler/` (11 recipes, validator, backup
+ atomic writer, sidecar `SaveFile/lightai-looks.json`), NLU `nlu/` (normalizers, repair rules, policy,
retriever), `planner.py`, `exec/` (wsclient, http reload, executor), `feedback/`, `prefs/`, `api/` (FastAPI +
`console.html`), `train/` (grammar.yaml + `data/seed.txt` + corrections -> dataset; trainer; evaluate/promote),
`mine/` (show mining, priors, transplant), `rig/audit.py` (`lightai audit`, `lightai fix v3-color`).

**Run:** `C:\lightai-env\venv\Scripts\python -m lightai serve|repl|parse|do|audit|train|nightly|facts|mine|transplant`
from `lightai/`. Model gates for promotion: dev intent >= 0.97, slot F1 >= 0.93, golden 100 %, abstain 100 %.

**Operator rules for testing (feedback, 2026-09-26):**
- *Never test against the running QLC+ (port 9999 drives the real rig).* Always create a **new dedicated test
  project** (`lightai.devtools.make_test_project()` -> `lightai-test-rig.qxw`, rig only, no DMX I/O) on a
  separate `QlcInstance` port. **Why:** the laptop is on the club's Art-Net network. **How to apply:** every
  e2e/manual QLC+ test uses a fresh test project; only read-only queries ever go to 9999.
- Keep responses **< 100 ms** for live commands and parse+plan (measured: ~4-11 ms p50 live, ~15-37 ms p50
  for looks). Write an iteration report (`lightai/reports/`) after each iteration: what was asked / done / results.
- Iterate with independent sub-agents validating parts until fully functional.

**Fork commands (built in build-mingw; installed in `C:\qlcplus` on 2026-09-28 by `lightai\tools\install-fork-build.ps1`, backup `backup-before-lightai-fork-20260928-152553`; test copy `C:\lightai-data\qlcplus-fork`):**
`QLC+API|lightaiVersion`, `getProjectFile`, `loadProjectFile|<abs>[|force]` (only the open file, no dialogs,
keeps file name, refuses unsaved changes), `saveProject`, `getFunctionSpeed|id`,
`setFunctionSpeed|id|fi|fo|dur` (`-` keeps). Code: `webaccess/src/webaccess.cpp` (QLC+API chain),
`ui/src/app.cpp` (`slotWeb*`), `main/main.cpp` wiring. lightai auto-detects and falls back to POST /loadProject.

**Engine fixes in this working tree (2026-09-26, verified; installed in `C:\qlcplus` with the fork build on 2026-09-28):**
- *EFX 16-bit handling* is decided once per universe in `EFX::getFader()` via `EFXFixture::hasNonContiguousFineChannels()`.
  Stock QLC+ flips a shared fader to 8-bit mid-start when a BEAM230 V3 (pan 0, tilt 1, pan_fine 2, tilt_fine 3)
  joins, leaving earlier spots at coarse pan/tilt 0. lightai also starts V3s first as a workaround for stock builds.
- *Priority zeros*: `GenericFader::write` keeps zero-intensity channels while `priority2() > 0` (until fade-out),
  so a P100 kill holds dimmers at 0. Stock drops them and the P0 look wins.
- Check both with `engine_check.py` logic: isolated instance, V3s last in the EFX, P100 kill over P0 wash.
  Stock fails both (12 of 14 spots stuck, dimmers 255), the fork passes both.

**API security (iteration 3):** Host header must be 127.0.0.1/localhost (DNS rebinding), cross-site POSTs are
refused by Origin, non-loopback `--host` requires `LIGHTAI_TOKEN` (console: `?token=`). Feedback kinds and
directions are validated; duplicate feedback on one plan is ignored.

**Speed notes:** rig reload is ~25 ms because the fixture library index, YAML files and roles template are cached
by mtime, and the retriever keeps its encoder and name vectors. Proposal previews are simulated when played, not
when planned.

**Model + schedule (2026-09-26, end of iteration 4):** v6 promoted (5 epochs, 6,766 sentences; dev intent 0.991,
slot F1 0.969; golden 56/56; abstain 30/30). Held-out: 87.4 % fully right on round-1's 438 sentences
(`C:/lightai-data/agents/nlu`), 84.2 % on round-2's 335 (`agents/nlu-r2`, harness `run_eval2.py`). INT8 export uses
per-channel weights with the two heads left in float (per-tensor INT8 cost v4 2.3 slot-F1 points; this costs none).
Train with `--epochs 5`. The nightly task "lightai nightly learning" runs at 05:30 (`lightai install-task --remove`).
Final checks: 141 unit tests, 2 live e2e (`LIGHTAI_E2E=1`), `tools/check_live_fork.py` 8/8, `tools/check_engine_fixes.py`.
Open user decisions: Simple Desk vs priority looks, priority fade-out dip, start-status race (ITERATION-04.md).

**Patch features (2026-09-28, labels v3):** `add_fixture` takes a count ("add 3 vpar 7 channel fixtures to universe 1":
new `count` slot + regex fallback; `patch_words` repair reads universe/address/mode from the words) and places fixtures
back to back at the first free block (`Planner._place`). New intent `fixture_edit.readdress` ("move fixture 16 to
universe 1 address 300", "move the vpars to universe 1") with overlap checks; executor op `readdress`. Library models
filed twice (American DJ / American_DJ) collapse, and the model already in the show wins (`prefer_rig_model`).
Fork v2 command `QLC+API|openProjectFile|<path>[|force]` opens another show file by path (loadProjectFile still only
reloads the open one); console header shows the open show and an "Open main show in QLC+" button
(`GET /qlc/show`, `POST /qlc/open-show`, starts `cfg.qlc_launcher` = `C:\qlcplus\start-qlcplus.bat` when QLC+ is closed).
QLC+ autostart opens the repo show via `C:\qlcplus\show-path.txt` (deploy/start-qlcplus.bat).
**Model v7 (2026-09-28, iteration 5):** promoted, labels v3 (25 intents incl. `fixture_edit.readdress`, slot `count`):
dev intent 0.997, slot F1 0.990, golden 58/58, abstain 30/30, parse p50 3.7 ms. Critical one/two-word commands
("blackout", "lights on", "release") are exact-phrase rules in `Parser.repair` (the raw model confused them). A move
never auto-places a fixture on its own current spot (`_place(avoid=...)`). The nightly job only retrains after >= 1 new
confirmed/corrected command (`--force` otherwise). The live e2e test detects the fork via `fork_version`, since
`C:\qlcplus\qlcplus.exe` is the fork build now.

**Iteration 6 (2026-09-28): house models, patch groups, other-show guard.** `rig/house.py` lists the models in the
operator's shows (main show + other `SaveFile/*.qxw`, autosaves skipped, cached by size/mtime); `assume_house_model`
turns an ambiguous/unresolved library match into the most-used owned model (called from `normalize_slot` and
`Parser.patch_words`); the planner states it plus alternatives and defaults the mode to the owned fixtures' mode.
`patch_groups` gives one count + one address per group ("3 to universe 1 and 4 to universe 2"); `_add_fixture` places
groups with `_place(avoid=...)`. `Parser.patch_intent` (first step of `repair`): add/patch/hang/install/mount + count +
owned model + (universe/address | end) -> add_fixture unless the model is >= 0.8 sure of another intent (a confident
readdress is still overridden). Executor pre-flight `_other_show`: a structural plan is refused BEFORE writing when QLC+
(fork) has another or an untitled show open; `allow_other_show` (API + console button) applies and switches QLC+ with
openProjectFile (`_open_main_show`); `/plan` warns. Console "Save correction" stores the labels as shown, so a mislabel
trains in at x3: the operator's first saved correction was mislabeled and was fixed (backup in `C:\lightai-data`).

**Iteration 7 (2026-09-28): New show + follow QLC+.** `cfg.main_project_path` = the club show (None = project_path);
`cfg.project_path` = the show lightai edits now; `cfg.main_show()`, `cfg.is_main_show()`. `LightAI.switch_show(path)`
reloads the rig and clears show-bound session state. The server's `follow_qlc()` (on `/plan`, throttled 2 s, and on
`GET /qlc/show`) switches to QLC+'s open show when it is a saved .qxw; untitled shows can't be followed. `POST
/qlc/new-show {name}` -> `rig/newshow.py` `create_empty_show` (Creator + Engine/InputOutputMap only, CurrentWindow
FixtureManager) in the main show's folder, opened via the shared `open_in_qlc` helper (also behind `/qlc/open-show`).
`Rig.load` gives non-main shows `portable_overrides` (models, rules, "all" zone only; `rig.is_main` False); the
feedback router stores fixture-model hints only for the main show; `cfg.sidecar_path` is per show
(`<stem>.lightai-looks.json`); `Plan.show` + executor refuse a plan made for another show. Verified on an isolated
real QLC+ (9/9). Never load the main show's I/O into a test instance: it has Art-Net out to the rig and OSC in on 9000.

**Iteration 8 (2026-09-28): history + count-less patches.** The server logs each `/plan` (text, intent, confidence,
mode, summary, show) and `/execute` outcome to `C:\lightai-data\history.jsonl`; `GET /history?limit=` joins them. Before
this, the only way to see what the operator typed was brute-forcing `/plan/{pid}` (pid = `p<unix time % 100000>-<n>`,
n counts from 1 per server start). `PATCH_START` accepts no count / a / another and "to project|show|patch"; without a
count `match_house_models(need_model_word=True)` so aliases ("fog" = fixture 33) don't turn "add fog" into a patch.
Known gap: "add some fog" / "more fog" -> none (model); "give me some fog" asks.

**Iteration 9 (2026-09-28): fixtures by any name.** `house_models` = shows in SaveFile + own definitions
(`cfg.local_fixture_dirs`: repo `Fixtures/`, `%USERPROFILE%\QLC+\Fixtures`); `assume_house_model` is house-first
(a library model only wins when named in full, score >= 1.2); maker-only or equal-usage ties -> ambiguous (asks).
Matching also runs letters+digits together ('l 1015', 'thin par'), maker/model tokens keep plural forms ('mayans'),
digits never match fixture numbers. `TargetResolver._single` falls back to rig models (`house_models(only_rig=True)`,
zones=False to avoid recursion). Pipeline: `join_model_spans` (target/model + adjacent stray tag -> one name when more
exact), `level_only` ('<fixtures> at N%' -> set_level), `maker_targets`. `WORD_RE` keeps 'beam230v2' one word. The
pars zone no longer aliases 'vpar(s)'. `tests/test_names.py` (128 cases) is the regression net for names.

**Safety rules in the policy (iteration 4):** blackout / lights-on / stop-all / release never act on negated, future,
question, reported, "prep", off-topic or partial-rig wording at ANY confidence (the model is often 0.99 on "blackout at
the drop"); a level with no target moves the grand master only if the text says so; the console auto-fires live intents
only at >= 0.9 confidence. QLC+'s Simple Desk writes at priority 0, so it loses to any P>0 look (lightai warns and refuses
calibration there; making the desk win is an open engine decision for the user).

**Gotchas:** QLC+ saves **Hidden scenes with all values 0** (never generate Hidden; ignore hidden scenes as
evidence). The BEAM230 base wheel has more slots than its `.qxf` lists (show uses 16, 48=purple?, 72 cyan,
96 orange fanta, 104 emerald) - see audit "wheel slots from function names". A test QLC+ instance writes
its file into QLC+'s recent-files registry list; `QlcInstance.stop()` cleans it. Git Bash commands longer
than ~6-8k characters fail here: write long scripts to a file first. The Bash tool also turns a doubled
backslash into one, so write regexes with single backslashes inside raw strings. Python's write_text on Windows writes CRLF
(most lightai files are CRLF now, each file consistent); Git Bash `grep $'\r'` does not detect CR, use Python. Training needs RAM: auto low-memory
mode (frozen embeddings + 3 layers) below 2.5 GB free; runs at below-normal priority.
Related: [[build-procedure]], [[priority-system-rebuild]], [[salesforce-qlcplus-integration]], [[mayans-beam-color-rgb]].
