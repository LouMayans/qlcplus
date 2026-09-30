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

**Iteration 10 (2026-09-29): Claude designer, 3D stage, teaching (report ITERATION-10.md).**
- *Room:* `rig/stage.py` reads `<show>.stage.json` (inches; X across, Y from the DJ's back wall, Z up; anchor = the
  dance-floor centre) live; orders, mirror pairs, groups, places, and `aim()` = the exact inverse of `stage-rig.js`
  (verified 0.0 in on real QLC+ DMX). Positions are provisional: `cfg.positions_final` gates left/right groups and
  computed mirroring; looks record `stage:<hash>` in facts_used and `design/reaim.py` rebuilds stale ones.
- *Designer:* `design/` = backend (claude.exe headless, subscription), runlog (Claude tab), spec (DesignResult),
  validate, composer (loop = Chaser of section Collections; with `minutes` = QLC+ Show, one track per layer), prompt
  (briefing file: rig, room, recipes, colour names, moods, KB files), jobs (repair rounds), sandbox (port 9997,
  sanitized copy), research, teacher (nightly labels, weight 1; review_queue.jsonl), moods (`knowledge/moods.yaml`).
- *claude.exe facts:* newest `%USERPROFILE%\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe`;
  stream-json needs `--verbose`; prompt on stdin; `--json-schema` answer in the result event's `structured_output`;
  `--system-prompt-file`/`--append-system-prompt-file` exist (not in --help); `--bare` can't use the subscription
  login; `--tools <list>` + `--strict-mcp-config` cut the fixed overhead ~29.6K -> ~9.7K tokens; the stream's
  `rate_limit_event` carries the 5-hour / 7-day usage windows.
- *Gotchas:* FastAPI request models must be module-level in server.py (postponed annotations can't see classes defined
  inside create_app: the body silently becomes a query param). `getChannelsValues` replies have FOUR fields per channel
  (index|value|type|override). `QlcInstance` holds a machine-wide lock (one test QLC+ at a time). Since 2026-09-29 the installed
  `C:\qlcplus` has the 3D stage too (rebuilt from master; backup in C:\qlcplus-backup-20260929). FakeQlc already has
  a `loop` attribute (its event loop). With TestClient after executing on another loop, close `executor.client` first.
  Zone names are accepted with underscores. The briefing must list valid colour names (unknown colours were the
  main repair cause).
- *Tools:* `tools/check_design_live.py [--real "<request>"]` (isolated dev QLC+ 9994, aim check, plays designs in the
  3D stage in visible full-screen Chrome); `python -m lightai research "<topic>"`.
- *Pixels, groups, 3D files, references (2026-09-29):* QLC+ loads a .qxw IN ORDER: a `<FixtureGroup>` must come
  before the functions (an RGBMatrix read before its group caches 0 steps and never animates) - use
  `Workspace.add_fixture_group` (add_function routes groups there). Group IDs are their own namespace: they live in
  `Look.groups` / `ComposedShow.groups`, never in `look.ids` (a group ID in ids once made delete/update remove an
  unrelated function); a design shares pending groups through `build_look(..., groups=list)`. GDTF draws fixtures
  HANGING with the beam along -Z, so an MVR identity Matrix = stage `hung`, rot 0 (stage R = M . Rx(180));
  `mine/formats3d/to_stage.py` only proposes, never writes. Reference library: `<data_dir>/references`, indexed in
  index.json (failures cached; `lightai refs index --force` retries), `lightai refs list|query`, and
  `mine/index.references_brief` puts the best matches in every design briefing. `tools/check_console_ui.py` = 28
  visible-Chrome checks of the console against FakeQlc + a canned Claude.
- *Labels v4 (2026-09-29, 29 intents / 23 slots):* + `design_show` (planner: mode "design", the console's Design tab
  button; no QLC+ change), `correction` (app.plan routes a model-detected correction to app.correct with the correction
  command as the fragment; alone: "nothing to correct"), `scene.add`, `scene.remove`; slots `object`, `coordinates`,
  `place`. fixture_edit.move/rotate now cover objects and edit the 3D stage whenever the show has one ("2d"/"monitor"
  words keep the old 2D-map edit). Typed effect words reach the iteration-10 recipes via normalize.MOVEMENT_RECIPES
  (they used to fall into circle_wave / color_chase). norm_distance returns exact "in" too. Seeds: data/seed.txt
  iteration-10 sections (~610 lines); grammar templates use `{splace}` (NOT `{place}`: that is a LITERALS list of venue
  words). tests/standin.py = a stand-in model answering from 'intent | markup' (use_stand_in replaces ai.model, which
  survives the parser rebuild after every executed change).
- *3D stage by voice (rig/scene.py, rig/scene_plans.py):* axes = inches, origin a floor corner, +X = the DJ's right
  looking into the room (= screen-right in the default FOH camera and the top view), +Y from the DJ wall to the bar,
  +Z up; the editor shows feet-inches RELATIVE TO THE ANCHOR (dance-floor centre), so spoken coordinates are anchor-
  relative. Objects: `id` (free string), `prop` -> the FILE's own `propDefs` (a new kind must add its propDef there, or
  the object silently doesn't render), `pos`, yaw in `rot[2]` AND legacy `rz` (write both; +yaw = counter-clockwise
  seen from above), `scale`, `parent` = the table's NAME (stools). Primitive parts are centred on pos, model parts stand
  on it. Save through QLC+ `QLC+API|saveStage|<json>` (reply `QLC+API|saveStage|OK|<rev>`, needs fork lightaiVersion
  3 and QLC+ having this show open): it writes the file (+ one .bak) and broadcasts `VIS|STAGE_SAVED` so open /stage
  pages reload; a direct file write gives open pages NO notice. lightai keeps its own copies in data_dir/stage-backups;
  every change carries its `before` value (stale plans are refused; undo = scene.invert). Page hooks for CDP checks:
  window.__stage.getDraft(), .select(kind, id), .camera('top'). `tools/check_scene_live.py [--standin]` = visible check.
- *Teacher fix:* labelling runs REPLACE Claude Code's system prompt (--system-prompt-file) and lead with the task;
  appended, Haiku treated the log as a chat and returned no labels. MAX_THINKING_TOKENS=2048 via the backend's
  `thinking_tokens`, label timeout 240 s, budget $0.25.
- *Model v9 (2026-09-29, labels v4) promoted:* golden 66/66, abstain 32/32, dev intent 0.979 / slot F1 0.963 (857).
  The gate passed but the full test suite caught tagging slips the gate doesn't cover: ALWAYS run the whole suite after
  a promotion. Guards added in the pipeline (any model): Parser.tidy_models_and_moods (generic words aren't models,
  mood words aren't colors, add_fixture needs a patch cue else it asks), trim_targets ('fixture 5 by'), scene_coordinates
  ('on coordinate / at position / at the spot X by Y' needs the lead-in), spatial 'straight down' drops the words from
  movement/direction, 'X over the dance' + next word. Training takes ~26 min (1,285 steps, 5 epochs, 4 threads).
- *Desktop/taskbar launcher (2026-09-29):* `lightai.lnk` on the (OneDrive) desktop and in the Start menu ->
  `C:\lightai-env\venv\Scripts\pythonw.exe tools\open_lightai.pyw` (icon `tools\lightai.ico`): starts `lightai serve`
  in a minimized console titled "lightai server on port 8765 - close this window to stop it" when /health doesn't
  answer, then opens Chrome `--new-window` on the console. A .lnk to a .bat can't be pinned; one to an .exe can.
  Programmatic taskbar pinning is BLOCKED on this Windows 11 (the ExplorerCommandHandler
  {90AA3A4E-1CBA-4233-B8BB-535773D48449} verb trick does nothing): the operator pins it by right-click. The stage proxy
  injects a "lightai console" link into #topbar-left of /stage (target "lightai-console"; the console sets window.name).
- *Installing QLC+ (2026-09-29):* `ninja -C build-mingw -k 0` STALLS on the translations target: it runs
  `cmd /C .\translate.sh`, and on this PC .sh opens in VS Code, so cmd waits forever (kill that cmd.exe; no .qm files
  exist anyway). Then `cmake --install build-mingw` (MinGW shell) -> C:\qlcplus (keeps show-path.txt/certs; copies
  deploy scripts + SaveFile\Main Project.qxw as a fallback). The downloaded 3D models/textures are NOT in the source:
  `robocopy C:\qlcplus-dev\Web\stage-lib C:\qlcplus\Web\stage-lib /E /XC /XN /XO`. The auto-mode classifier blocks
  installing into C:\qlcplus ("production deploy") until the operator approves. Check with
  `tools/check_stage_via_lightai.py` (isolated 9994/8766, visible Chrome: stage file loads, AI moves show live).
- *Live aiming / triangulation (2026-09-29, rig/aim_live.py):* Parser.beam_refs spots "where spot 2's beam lands",
  "the end of the beam" (no fixture = the last live aim), "spot 2 beam that ends on the floor", "the same spot as
  spot 2" -> cmd.spatial["aim_beam"]; numbered fixtures + an aim ("place spot 2 ... straight down") or any beam
  reference plan `aim_live` (mode live; zones aimed at a place stay a saved look). The executor reads the reference's
  pan/tilt from QLC+ (getChannelsValues), follows the beam with dmx_to_degrees + beam_direction to the floor (or a
  deck top), aims with the position recipe (aim "point:x,y,z"), writes live overrides and reads the last channel back
  (sends have no reply: the read-back orders them). session.last_aim + app.AGAIN ("do it again", "re-aim"...) repeat
  it with the stage as it is now. Checked live: 14 beams on spot 2's floor spot within 0.02 in (tools/check_aim_live.py).
- *3D stage arrow keys (webaccess/res/stage-camera.js):* arrows look (or orbit with the orbit-middle setting) like
  middle-drag, 60 deg/s, Shift x2; QLC+ serves Web files from disk (WebAccessBase::webFilePath -> C:\qlcplus\Web), so a
  web-only change is a file copy, no rebuild (tools/check_stage_keys.py checks it). Chrome opens MAXIMIZED everywhere
  (launcher maximizes the console window itself; check tools use --start-maximized), never full screen.
- *Moving-head size (2026-09-29):* the operator measured the club's spot (Mayans BEAM230): 20 in from the legs to the
  head, straight. QLC+'s generic moving_head.dae is authored 42.49 in tall and the page used to draw it at bodyScale
  0.35 (14.9 in). Now stage-looks.js BUILTIN_HEIGHT_INCHES fits builtin/moving_head to 20 in on an inner node and
  stage-scene.js bodyScaleFor defaults to 1 (the operator wants the model itself right-sized, scene scale 1). Measured
  in the page: 14.82 w x 11.12 d x 20.00 h in; pan pivot 10.40 in and tilt axis 13.91 in from the legs; the beam
  starts at the tilt axis, 3.71 in wide. lightai: Stage.beam_origin() = pos + R(0,0,13.91 x bodyScale) for movers
  on the builtin look (MOVING_HEAD_TILT_AXIS_IN), used by Stage.aim and aim_live. The washes share the model (20 in
  until measured). Web-only changes: copy the files into C:\qlcplus\Web and C:\qlcplus-dev\Web.
- *Shell gotcha:* the Bash tool collapses a double backslash (\\) to one even inside quoted heredocs: write
  backslash-heavy code with the Write tool or chr(92). A \r that sneaks into a Python literal becomes a CR,
  which read_text turns into a line break. Python's write_text writes CRLF on Windows; fine here (core.autocrlf=true
  normalizes on commit).
- *Promotion gate (fixed 2026-09-29):* the dev split is a seeded shuffle of ALL rows, so it reshuffles whenever rows
  are added and a model's saved metrics.json is from a different dev set (v7 saved slot F1 0.990 but scores 0.968 on
  the data v8 scored 0.972 on; the nightly wrongly held v8 back). `promote()` now re-scores the current model on the
  same data (`baseline_metrics`, falling back to metrics.json). `lightai evaluate <dir>` OVERWRITES `<dir>/metrics.json`:
  back it up when only checking. Pipeline guards from v8's golden misses: `Parser.tidy_look_slots` (filler movement
  words do/make/go dropped; "big fast" split into size + speed) and a `create_look` AGREE wording rule in policy.py.

**Safety rules in the policy (iteration 4):** blackout / lights-on / stop-all / release never act on negated, future,
question, reported, "prep", off-topic or partial-rig wording at ANY confidence (the model is often 0.99 on "blackout at
the drop"); a level with no target moves the grand master only if the text says so; the console auto-fires live intents
only at >= 0.9 confidence. QLC+'s Simple Desk writes at priority 0, so it loses to any P>0 look (lightai warns and refuses
calibration there; making the desk win is an open engine decision for the user).

**3D stage proxy (2026-09-29):** the browser 3D stage view (QLC+'s `/stage`, see
[[stage-visualizer]]) is now also reachable through the lightai server itself, on lightai's own
port at the same paths - `lightai/lightai/api/stage_proxy.py` (`add_stage_routes`, called from
`create_app` in `api/server.py` right before `return app`, registered LAST so its catch-all GET
route `/{full_path:path}` never shadows another lightai route - it only proxies paths starting
with `stage`/`three/`/`gobos/`, plus the exact path `favicon.ico`, and 404s everything else
itself without asking QLC+). Nothing stage-related connects until a browser actually requests
`/stage` or opens `/qlcplusWS`; the console at `/` only gets a "3D Stage" link, no preloaded
assets. HTTP is proxied with a shared, lazily-created `httpx.AsyncClient` (stream=True end to
end, so multi-MB glTF/texture GETs are never buffered in memory); the WebSocket is bridged with
one fresh raw `websockets` connection per browser socket (pumped both ways; either side closing
closes the other, which ends the 30Hz DMX subscription). Both paths reuse `wsclient`'s own
URL/TLS/auth logic (`candidate_urls`, `basic_auth_header`, `open_ws`, `tls_context`,
`is_loopback` - `QlcClient._candidates`/`_open` were refactored to call the same `candidate_urls`/
`open_ws` so there's one source of truth, not two copies that could drift). QLC+ unreachable ->
a friendly 502 page; a stock QLC+ build with no `/stage` -> a friendly 404 page. `_http_bases`
tries https then http when `qlc_url` is "auto" (matching `QlcClient`); the httpx client uses a
short **connect** timeout (3s) but a long **read** timeout (30s) - without that split, probing
https against a plain-http QLC+ (the normal case, e.g. the `:9998` dev build) hung for the FULL
timeout doing a TLS handshake it could never finish, instead of failing over to http in ~3s (hit
this live during testing; fixed before this shipped). The winning scheme is cached 30s so it's
only paid once per cold start, not once per asset.
Token: when the server has one (non-loopback `--host`), only `/stage` itself and `/qlcplusWS`
require `?token=...` (checked with `hmac.compare_digest`, same as the header check elsewhere);
sub-resource GETs (js/css/three/stage-lib/gobos/favicon) are never gated - a `<script src>` or a
`<link>` can't add a header or a query param itself, and nothing sensitive is in them.
`webaccess/res/stage-ws.js` forwards `?token=...` from `location.search` onto its own
`/qlcplusWS` URL (a no-op when QLC+ serves the page directly). `console.html`'s existing
`?token=`/`sessionStorage` TOKEN logic now also rewrites the "3D Stage" link's href to
`/stage?token=...` when a token is set.
New `serve` flag `--qlc-port` (and env `LIGHTAI_QLC_PORT`) overrides `cfg.qlc_port` for testing
against a second QLC+ instance without touching `%LIGHTAI_DATA%\config.yaml` - e.g. `python -m
lightai serve --port 8766 --qlc-port 9998 --no-embeddings` (from `lightai/`) points a throwaway
lightai server at the dev QLC+ + stage build on `:9998` instead of the real rig on `:9999`.
Verified live this way: opened `http://127.0.0.1:8766/stage` in a visible Chrome via CDP (the
`tools/stagelib/click_check.py` pattern) - `window.__stage.status()` connected with 36 fixtures,
`assetsPending()` reached 0, `dmxStats()` showed frames arriving on 2 universes, zero console
errors/exceptions; `click_check.py --url http://127.0.0.1:8766/stage` itself PASSED (all 14
view/edit click checks); `http://127.0.0.1:8766/` (the console) still loaded and its HTML has no
`/stage` or `/three` request, only the inert link. Tests: `lightai/tests/test_stage_proxy.py`
(HTTP proxy against `fakeqlc.FakeQlcHttp` - new, a tiny `ThreadingHTTPServer`-based fake static
file server, added to `fakeqlc.py`; WS bridge round-trip and token enforcement against the
existing `fakeqlc.FakeQlc`; 502 on a dead port) - 6 tests, builds a bare `FastAPI()` +
`add_stage_routes` rather than the full `create_app`/`LightAI` stack, so it doesn't need a
promoted model. Whole suite: 352 passed, 2 skipped, unaffected.
**For the real rig install (`C:\qlcplus`, port 9999) to serve `/stage` through lightai in
production:** it needs the fork build with the 3D stage feature actually installed (the same
`install-fork-build.ps1` used for the other fork commands); until then, `/stage` through lightai
on `:9999` correctly shows the friendly "this QLC+ build has no 3D stage" 404 page rather than
erroring.

**Gotchas:** QLC+ saves **Hidden scenes with all values 0** (never generate Hidden; ignore hidden scenes as
evidence). The BEAM230 base wheel has more slots than its `.qxf` lists (show uses 16, 48=purple?, 72 cyan,
96 orange fanta, 104 emerald) - see audit "wheel slots from function names". A test QLC+ instance writes
its file into QLC+'s recent-files registry list; `QlcInstance.stop()` cleans it. Git Bash commands longer
than ~6-8k characters fail here: write long scripts to a file first. The Bash tool also turns a doubled
backslash into one, so write regexes with single backslashes inside raw strings. Python's write_text on Windows writes CRLF
(most lightai files are CRLF now, each file consistent); Git Bash `grep $'\r'` does not detect CR, use Python. Training needs RAM: auto low-memory
mode (frozen embeddings + 3 layers) below 2.5 GB free; runs at below-normal priority.
Related: [[build-procedure]], [[priority-system-rebuild]], [[salesforce-qlcplus-integration]], [[mayans-beam-color-rgb]], [[stage-visualizer]].
