# lightai: a trainable lighting assistant for the Mayans QLC+ rig

lightai turns typed booth commands into JSON and applies them to QLC+. It is **non-generative**: a small
fine-tuned DistilBERT model only understands *what you mean* (intent + details). Deterministic code
turns that into QLC+ functions using your real fixture data, so the output is always valid, and it
gets better from your corrections. Everything runs offline on the booth laptop, and everything is free.

```
you type ──> language model (DistilBERT INT8, ~5 ms) ──> JSON command ──> plan ──> QLC+
                                                         (LightCommand)    │   live: WebSocket (CH|, functions)
                                                                           │   structural: edit .qxw + reload
you rate the result ("that was pink not blue", "too fast") ──> rig facts / taste / language corrections
```

## Setup (already done on this laptop)
```
winget install -e --id Python.Python.3.12
python -m venv C:\lightai-env\venv
C:\lightai-env\venv\Scripts\python -m pip install -r requirements-train.txt
C:\lightai-env\venv\Scripts\python -m pip install -e .
```
Runtime data lives in `C:\lightai-data` (models, logs, corrections, feedback). Override paths in
`C:\lightai-data\config.yaml`, for example:
```yaml
project_path: C:\qlcplus\SaveFile\Main Project.qxw   # the file QLC+ actually runs
reload_strategy: auto                                 # fork reload-by-file, else POST /loadProject
tls_server_name: lights.mayansvip.com                 # verify the cert instead of skipping on loopback
```
If QLC+ web auth is on (`-wa`), put `{"username": "...", "password": "..."}` in `C:\lightai-data\qlc-credentials.json`.

## Daily use
| Command | What it does |
|---|---|
| `lightai serve` | Booth console at http://127.0.0.1:8765 plus the JSON API |
| `lightai repl` | The same in the terminal (`:y` apply, `:p` preview, `:keep`, `:ok`, `:fast`, `:saw pink 5`, `:bpm 128`) |
| `lightai parse "slow blue wash breathing at 60 BPM" --plan` | Print the JSON only; nothing runs |
| `lightai do "washes at 40%" --yes` | One-shot: parse, plan and execute |

(Run as `C:\lightai-env\venv\Scripts\python -m lightai ...` from this folder, or double-click
`tools\start-lightai.bat`, which starts the server and opens the console in your browser.)

Things you can say:
- **New looks:** "slow blue wash breathing at 60 BPM", "pink and blue chase on the pars", "big fast white sweep on the beams", "mirrored green circles on the spots", "orange strobe on the tetras as an override".
- **Assume and try:** "make an assumption on fixture 5 and make it move". It previews live, then asks how it looked. Press *Keep it* to save the look.
- **Fix the rig map:** "fixture 3 needs to be rotated 90 degrees", "move wash 1 left 50 cm", "rename fixture 21 to DJ Wall".
- **Patch:** "add 3 vpar 7 channel fixtures to universe 1" (puts them at the first free addresses, back to back),
  "add 3 american dj pars to universe 1 and 4 to universe 2" (two groups), "add 2 pars", "hang 2 moving heads on universe 1",
  "add a chauvet intimidator spot 260 at universe 2 address 150", "move fixture 16 to universe 1 address 300",
  "move the vpars to universe 1" (first free block), "change the dmx address of wash 1 to 250".
  A vague model ("pars", "american dj par", "moving heads", "spots") means the one your shows use most: the main show counts
  first, then the other shows in `SaveFile`. The plan says which model it assumed and which others you own also match; a
  model you name exactly is used as said. Without a mode it uses the mode your other fixtures of that model use. Overlaps
  are refused with the first free block that fits. Set the same address on the unit itself (menu or DIP switches).
- **Your fixtures by name:** any name of a fixture you have works: the models in your shows (`SaveFile`) and your own
  definitions (`Fixtures\` in the repo, `%USERPROFILE%\QLC+\Fixtures`), by model, maker, version or the names you gave
  them: "add a swarm", "add a betopper l1015", "add 2 beam230 v2", "thin pars at 50%", "chauvet swarms off". Your
  fixtures come before the QLC+ library; when a name fits several of yours equally ("betopper", "mayans"), it asks.
- **Which show changes:** lightai edits the show QLC+ has open, as long as it is saved as a file. The console header
  shows it ("show: ..."; "lightai edits ..." in orange when they differ). A new, unsaved QLC+ show has no file to edit:
  type a name in the header and press **New show** instead. That creates an empty show next to the main show (the main
  show's DMX outputs, no fixtures), opens it in QLC+ and lets lightai edit it. **Open main show in QLC+** goes back.
  What it learned about your main show's fixtures (zones, aliases, stage order, the kill function) applies to the main
  show only; what it learned about fixture models applies to every show.
- **History:** every command you type is logged with what came of it (`C:\lightai-data\history.jsonl`); the last 50 are
  at http://127.0.0.1:8765/history.
- **Live control:** "washes at 40%", "fog on", "grand master 75%", "start auto chaser show 3", "stop everything", "blackout", "lights back on", "release the washes".
- **Teach the rig:** "find blue on fixture 34", "what does channel 6 on fixture 1 do", "calibrate fixture 31".
- **Feedback:** "that was pink not blue", "too fast", "too dim", "fixture 34 is a v3", "looks good".
- **Tempo:** tap the **TAP** button or say "bpm 126". New looks use it. On the fork build, running AI looks are retimed live.

Anything unclear gets a question back instead of a guess.

## The models
- **Where they live:** `C:\lightai-data\models\v1`, `v2`, ... (about 65 MB each) and `models\retriever` (MiniLM, 23 MB).
  `models\current.txt` names the one in use. They are not in git: they are large, every retrain adds another, and they
  can be rebuilt from the training data. `data\seed.txt`, `lightai\train\grammar.yaml` and the rig facts it learns
  (`lightai\rig\learned.yaml`) are in git; your corrections and ratings are in `C:\lightai-data`
  (`corrections.jsonl`, `feedback.jsonl`), which is not. Back up `C:\lightai-data` to keep them.
- **Where they come from:** the base models are downloaded once from Hugging Face (free, Apache-2.0):
  `distilbert-base-uncased` for understanding commands and `sentence-transformers/all-MiniLM-L6-v2` for matching function
  names. They are cached in `%USERPROFILE%\.cache\huggingface`. Training fine-tunes the base model here on the laptop.
- **Trying other models:** any Hugging Face encoder of the BERT family can be trained on the same data, for example
  ```
  C:\lightai-env\venv\Scripts\python -m lightai train --epochs 5 --encoder bert-base-uncased
  C:\lightai-env\venv\Scripts\python -m lightai train --epochs 5 --encoder google/electra-small-discriminator
  ```
  Without `--promote-if-better` nothing changes in use; each run leaves `metrics.json` in its model folder (dev accuracy,
  golden and must-abstain results, parse speed) so the runs can be compared. Bigger models are slower and need more RAM.

## How it learns
| Loop | You do | It updates | When |
|---|---|---|---|
| Language | *Save correction* in the console (fix the intent/word labels), or confirm a result | `corrections.jsonl`, the next model | nightly (`lightai nightly`) |
| Rig facts | "that was pink not blue", calibration answers, "fixture 34 is a v3" | `lightai/rig/learned.yaml` (undo: `lightai facts undo <id>`) | instantly |
| Taste | Looks right / too fast / too dim / bigger ... | defaults + acceptance model in `C:\lightai-data\prefs` | instantly; model retrains nightly |
| Other shows | `lightai mine local <folder>` / `lightai mine github --token T`, then `lightai mine stats` | `lightai/knowledge/priors.yaml` (typical EFX sizes, speeds, palettes, gear gaps) | on demand |
| Borrow a look | `lightai transplant other.qxw "Their Function"` | a new look on your rig (dry run first) | on demand |

A new model is only promoted if it passes every gate: dev intent accuracy ≥ 97 %, slot F1 ≥ 0.93,
all golden commands (`data/golden/golden.yaml`), and all must-abstain sentences.

## Rig facts come from data, not code
Channel roles come from the `.qxf` fixture files, the `Blank Rig Template` channel groups and
`lightai/rig/overrides.yaml` (your zones, aliases, stage order, kill scene and quirks). Colors come from
`data/colors.yaml` (your confirmed RGB table). `lightai audit` checks the show against all of this
(`C:\lightai-data\rig-audit.md`). It lists color-wheel slots your function names reveal but the `.qxf`
doesn't, and scenes that drive the V3 beams on the wrong channel. `lightai fix v3-color` repairs what it
safely can: a dry run by default, `--yes` writes with a backup.

## QLC+ fork commands (this repo)
The fork adds these to the Web Access WebSocket (`webaccess/src/webaccess.cpp`, `ui/src/app.cpp`):

| Command | Reply |
|---|---|
| `QLC+API\|lightaiVersion` | `1` (feature detection) |
| `QLC+API\|getProjectFile` | `<open file>\|<1 if unsaved changes>` |
| `QLC+API\|loadProjectFile\|<abs path>[\|force]` | `OK` or `ERR\|reason`. Reloads only the open file, keeps its name, never opens a dialog, and refuses unsaved changes unless `force`. |
| `QLC+API\|saveProject` | `OK\|<path>` |
| `QLC+API\|getFunctionSpeed\|<id>` / `setFunctionSpeed\|<id>\|<fadeIn>\|<fadeOut>\|<duration>` (`-` keeps a value) | `<id>\|fadeIn\|fadeOut\|duration` |

lightai detects the fork automatically and falls back to `POST /loadProject` on stock QLC+.

## Safety rules built in
- Tests and experiments use a **separate QLC+ instance** with a dedicated test project
  (`lightai.devtools.make_test_project()`: the rig with no functions and no DMX outputs). QLC+'s recent-files
  settings are restored afterwards. Nothing in the test suite talks to port 9999.
- Structural changes always need confirmation. Every write makes a timestamped backup in `SaveFile\backups`.
- Blackout, lights back on, stop everything and release only happen when you mean *now*. Negated, future, question,
  second-hand, off-topic or partial-rig wording asks first ("blackout at the drop", "should we blackout?",
  "don't stop everything", "stop all the music", "kill the lights in the bathroom"). lightai can't schedule:
  say the command when you want it.
- A level with no fixtures named only moves the grand master when you say "grand master", "master" or "gm"
  ("battery at 5%" never dims the room). The console fires live commands on Enter only when the model is at
  least 90 % sure; otherwise it shows the plan and waits for Run.
- A running priority look (a P100 kill, a P20 look) wins over manual levels: QLC+'s Simple Desk writes at
  priority 0. lightai tells you which look holds the channels and won't calibrate a fixture it holds.
  Reloads are refused while functions run or QLC+ has unsaved changes, unless you confirm.
- Previews and calibration use Simple Desk overrides and release every channel they touched.
- Never sent: `QLC+CMD|opMode` (it can block QLC+) and `getWidgetSubIdList` (it crashes on unknown IDs).
- The API binds to 127.0.0.1. CORS is off unless you pass `--cors <origin>`.
- It only answers requests addressed to 127.0.0.1 or localhost, and refuses changes sent from other websites.
  This blocks DNS-rebinding and cross-site tricks from pages open in the booth browser.
- To reach it from another device, set a token first. It then refuses any change without that token:
  ```
  set LIGHTAI_TOKEN=pick-a-long-secret
  C:\lightai-env\venv\Scripts\python -m lightai serve --host 0.0.0.0
  ```
  Open the console as `http://<laptop-ip>:8765/?token=pick-a-long-secret`. Without a token it refuses to listen
  beyond this laptop.

## Tests
```
C:\lightai-env\venv\Scripts\python -m pytest tests -q                  # unit + fake-QLC+ journeys
set LIGHTAI_E2E=1 && C:\lightai-env\venv\Scripts\python -m pytest tests -q  # + live isolated QLC+ (stock and fork)
```
Two real-QLC+ checks (isolated instance on port 9994, fresh test project, never the show):
```
C:\lightai-env\venv\Scripts\python tools\check_engine_fixes.py [C:\qlcplus\qlcplus.exe]   # EFX with V3s + P100 kill
C:\lightai-env\venv\Scripts\python tools\check_live_fork.py      # reloads, previews, calibration, latency
```
The first proves the two engine fixes: stock QLC+ fails it (12 of 14 spots frozen, kill ineffective), the fork passes.
Run it again against `C:\qlcplus\qlcplus.exe` after installing the fork build.
Iteration reports live in `reports/`.
