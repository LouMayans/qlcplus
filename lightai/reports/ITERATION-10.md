# Iteration 10: the Claude show designer, the 3D stage, and learning from conversations (2026-09-29)

## What was asked
- Understand free language like *"Can you create 3 different shows that resemble fast strobing but changing in light a
  dreamy way"*, research if needed, and create full, long-running shows with the club's fixtures. Time doesn't matter:
  shows are made before opening.
- Connect lightai to Claude so it can research professional lighting design with agents, and update itself when it
  doesn't understand an input.
- Show every Claude run in the console: what it researched, what it read, tokens used.
- Learn from the operator's corrections ("that's wrong", "what I meant was ...").
- Research show files from any major lighting program, and 3D visualization files.
- Use the new 3D stage (`SaveFile/Main Project.stage.json`, made in another session) so lightai knows where every
  fixture is and can make spatially clever shows. Its positions are provisional and will be measured later.
- Work with subagents on the right model; run browser tests in a visible, full-screen Chrome.

## The answer to "is the model good enough?"
No. The local model is a classifier: 21 command types, one look per command. It stays the booth controller: fast,
offline and safe. Claude became the designer, researcher and teacher, and lightai stays the only thing that writes to
QLC+: it checks everything Claude proposes against the real show and builds it with its own compiler.

## What was built
| Area | Change |
|---|---|
| Room awareness | `rig/stage.py` reads `<show>.stage.json` live (never copies the provisional positions). It provides spatial orders (left to right, back to front, centre out, circular...), mirror pairs, location groups ("front row", "over the dance floor", "near the DJ", "near the bar"), named places from the stage objects, and **aiming**: the exact inverse of the 3D stage's pan/tilt maths. Hand-made zones stay in charge until the positions are marked final (`positions_final`). |
| Spatial looks | Every recipe takes `order`, and movement EFX can be centred on a place (`aim`). A new `position` recipe aims at a place: together, fan, cross, or straight down. Commands like "aim the spots at the DJ", "cross the beams over the dance floor", "spots circle left to right" and "washes pink center out" work offline. |
| Re-aim | Looks and shows remember what they aim at, plus the version of the stage file. After the layout changes, "Re-aim" rebuilds them in one confirmed step. |
| New effects | `color_morph` (dreamy pastel crossfades as a moving gradient), `dimmer_wave` (an intensity wave across the room), `strobe_chase`, `strobe_burst` (1 beat of strobe, 3 beats calm, changing colours); and RGB-matrix pixel effects: `pixel_chase` (QLC+'s Stripes script) and `pixel_wave` (Waves) run across the Tetra bars and the LED walls in their 3D order, each on a fixture group lightai creates or reuses. |
| Moods offline | `knowledge/moods.yaml`: 20 moods (dreamy, hypnotic, energetic, build-up, drop...) seeded from the knowledge base. "Dreamy blue wash" fills in only what wasn't said; "something hypnotic on the spots" proposes a look. |
| Designer | `design/`: a design request goes to Claude through the operator's subscription (the `claude.exe` bundled with the VS Code extension, headless). Claude answers in a strict schema (shows, sections, layered looks, fixtures named by phrase, never by ID), is briefed with the rig, the room map, the recipe catalog, the colour names, the moods and the knowledge base, and may research the web. lightai validates every phrase, recipe, place and colour; problems go back for up to two repair rounds. Loop shows are a Chaser of section Collections (follows the BPM); when a length is given, a QLC+ timeline Show with one track per layer. Apply, run, refine, remove, and rotate shows through the night on the QLC+ side (`LOOP`). |
| 3D preview | "Watch in 3D" opens the stage with the live show. The sandbox plays a design on a private copy of the show without DMX outputs, in a separate QLC+ (port 9997) with the 3D stage, so the real rig is never touched. |
| Claude activity | Every run (design, repair, research, teaching) is logged with its full event stream: the console's Claude tab shows the steps (web searches, pages read, files read, the answer), the sources, tokens, the cost figure Claude Code reports, and the subscription's 5-hour and 7-day usage. |
| Cheaper runs | Runs load only the tools they need (`--tools`) and no connectors (`--strict-mcp-config`): the fixed overhead per run fell from about 29,600 to 9,700 tokens (-67%). |
| Reference library | Decoders for shows made in other programs (xLights `.xsq`, Light-O-Rama `.lms`/`.loredit`, Vixen 3 `.tim`) and for 3D files (MVR scenes, GDTF fixtures, plus an MVR-to-`stage.json` proposal that is never written on its own). `lightai refs index | list | query` keeps a private index of `C:\lightai-data\references` (pacing, energy arc, effect mix, palette, sections), and every design request gets the best-matching references in Claude's briefing: structure to borrow, never content to copy. Oversized files and zip bombs are refused; XML is parsed without entities or network access. |
| Research | `lightai research "<topic>"` and a console box: Claude researches on the web (with a Haiku researcher helper), writes a cited report to `lightai/knowledge/research/`, and adds validated mood words. |
| Corrections | "no, I meant the spots", "not that, pink", "what I meant was swarms", "that's wrong": the previous command is re-planned with that part replaced, and lightai offers to undo what the wrong one changed. The History tab has Wrong / What I meant on every item. History rows carry the console session and what they correct. |
| Console | Four tabs. **Command** as before, plus "Design this with Claude" when a request is creative, long or unclear. **Design**: the request, job progress, a preview card per show (sections, layers, fixtures, spatial moves), Apply / Run / Stop / Remove / Refine, Preview in 3D (sandbox), Watch in 3D, and rotation through the night. **Claude**: every run with its steps, sources, tokens and the 5-hour / 7-day usage bars; the research box; the teaching review (accept, dismiss, undo). **History**: the conversation with Wrong / What I meant and Undo. A banner offers Re-aim when the 3D layout changed. `tools/check_console_ui.py` drives all of it in a visible full-screen Chrome against a fake QLC+ and a canned Claude (28 checks). |
| Teacher | Every night (and on demand) Claude reads the conversations the local model struggled with and writes training labels from what the operator meant. Clear ones train at weight 1 (the operator's own corrections count 3); unclear ones and new words wait for review in the console; the promotion gates are unchanged. |

## Results
- Tests: 593 unit tests pass (plus opt-in live tests); the console check passes 28/28 in a visible Chrome.
- Pixel effects, on an isolated real QLC+: a pixel chase added to a copy of the full main show runs and animates.
- Aiming, on an isolated real QLC+ from its real DMX output: all 14 spots aimed at the DJ pass within 0.0 inches of the
  target point (the maths is exact against the 3D stage's own).
- **The real request, end to end:** "Can you create 3 different Shows that resemble fast strobing but changing in light
  a dreamy way" went to Claude (Sonnet). It read the four knowledge-base files and designed **Velvet Flicker**, **Neon
  Lullaby** and **Rose Static** (3 sections each: colour morphs, dimmer waves, strobe bursts and chases, circle waves,
  an aim at the dance floor). One repair round fixed 17 problems (14 were zone names written with underscores, 3 an
  unknown colour); both causes are fixed, and the same first answer now passes with zero problems. 134 QLC+ functions
  were applied to an isolated copy of the show and each show played in the 3D stage in a visible, full-screen Chrome.
  Cost: $0.33 + $0.08 (the API-equivalent figure; it counts against the subscription, it isn't billed).
- Sandbox: a design plays in a separate QLC+; the show file is untouched and the sandbox is gone afterwards.

## Problems found while integrating (fixed)
- **Pixel groups loaded frozen.** New fixture groups were written after the show's functions; QLC+ loads the file in
  order, so an RGB matrix read before its group never animates. Groups now go where QLC+ saves them (after the
  fixtures, before the functions).
- **Removing a pixel look could delete an unrelated function.** QLC+ numbers fixture groups apart from functions, but
  a new group's number was listed among the look's function IDs, so deleting or rebuilding the look removed whatever
  function had that number. Groups are now their own list, and one design never gives two groups the same number.
- **Imported MVR fixtures were upside down.** GDTF draws every fixture hanging, with the beam along -Z; the converter
  took an unrotated fixture as standing on the floor, pointing up. Now checked against the 3D stage's own maths.
- **The History tab broke after a rotation** (a missing field in its history row).

## The local model: why last night's v8 was held back, and the gate fix
- The nightly trained v8 and did not promote it: 56 of 58 golden sentences, and a slot F1 of 0.972 against v7's
  saved 0.990.
- The two golden misses were wording traps, now guarded in the pipeline for any model: "make the spots **do** red
  circles" (v8 tagged "do" as a movement and was unsure, 0.78) and "**big fast** white sweep" (v8 read "big fast" as
  one speed). Filler movement words are dropped, a size word at the start of a speed becomes the size, and a clear
  "make / do / give me ... circles / chase / sweep / wave ..." request is accepted between 0.45 and 0.80 confidence
  (never with stop, faster, brighter or the other "change it" words). With them v8 passes 58/58.
- The slot F1 "regression" was a measuring error: the test split is a shuffle of all training sentences, so it changes
  whenever sentences are added. On the same data, v7 scores 0.968 and v8 0.972, with the same intent accuracy (0.996).
  The gate now re-scores the current model on the same data before comparing (falling back to its saved scores).
  v7 stays live for now; tonight's nightly compares its new model fairly and promotes it if nothing got worse.

## Part 2 (same day): teaching the local model, and the 3D stage by voice

### Asked
- "teach the local AI"; then: "give the AI access to update and move things inside the 3D visualization": move fixture
  X one foot to the left, place fixture X at 5 foot by 5 foot, rotate fixture X upside down or 90 degrees, move tables
  and chairs, add objects ("add a high top table and 4 stools next to the DJ booth", "move table X with its chairs a
  foot to the right from the stage").

### The local model: v9 (labels v4), promoted
| | v7 (was live) | v9 (live now) |
|---|---|---|
| Commands it knows | 25 | 29: + design a show, correction, add objects, remove objects |
| Golden sentences | 58 of 66 | 66 of 66 |
| Must-not-act sentences | (not measured) | 32 of 32 |
| Dev intent / slot F1 | re-scored by the gate on the same data: v9 is not worse | 0.979 / 0.963 (857 sentences) |
| Parse time | ~5 ms | 4.3 ms median, 14 ms at p95 |

- New training material: about 610 sentences written for this (show requests, corrections and their neighbours, the
  new effect words, the 3D-scene commands with the room's real object names), grammar templates for the scene commands,
  and the operator's own missed command ("Create a yellow strobing lightshow with all of the fixtures...", which v7
  read as "add a console button" at 0.998).
- Promoted through the fixed gate (the current model re-scored on the same data). The full test suite then found a few
  v9 tagging slips the gate doesn't cover (it tagged "fixture" as a model, "straight" as an effect, "dreamy" as a color,
  "dance" apart from "floor", "coordinate" as a fixture, and read bare "add fog" as patching a fixture). Each is now
  guarded in the pipeline for any model: a patch request needs a sign of patching, mood words aren't colors,
  "at / on / to (coordinate / position / spot) X by Y" is read as coordinates, and so on. 657 tests pass.
- Typed commands now reach the new effects (pixel wave, color morph, dimmer wave, strobe chase, strobe burst): before,
  "pink pixel wave on the tetras" became a pan/tilt circle wave.
- The teacher (Claude labelling the conversations the model struggled with) returned nothing every time: it read the
  log as a chat. Fixed (the brief replaces Claude Code's own system prompt; the task leads the message; thinking is
  capped). Its first real label is waiting in the console's Teaching review.

### The 3D stage by voice
- New `rig/scene.py` + `rig/scene_plans.py`: move (by feet/inches in a direction, to coordinates, next to / towards /
  away from something), turn (degrees, with its stools), flip upside down, mount on the wall or floor, add objects
  (twelve kinds; the show's own props when it has them, simple shapes otherwise; seats arranged around their table and
  linked to it) and remove them. Left and right are as seen from the DJ booth, like the 3D view's default camera
  ("from the dance floor" flips them); coordinates are measured like the editor shows them, from the dance-floor centre.
- Saved through QLC+ (`saveStage`), so every open 3D page reloads by itself; lightai keeps its own backups; every change
  carries the value it replaces, so a plan is refused if the scene was edited in the browser meanwhile, and "that's
  wrong" / Undo puts it back exactly. Without a QLC+ that has the 3D stage, the file is written directly.
- Checked live in a visible full-screen Chrome on an isolated copy of the show (`tools/check_scene_live.py`): all nine of
  the operator's example sentences, read by v9 itself, were saved through the dev QLC+ and shown by the open 3D page
  (18 of 18 checks).

## Open items
- The reference library is empty: downloads wait for the operator's approval of the source list in `knowledge/research/format-survey.md` (free items only, kept private).
- A command that asks for two things at once ("create a lightshow ... and add a button to turn it on") is read as
  one; splitting it into two steps is not built yet.
- Objects can't yet be turned to face something ("turn the booth to face the dance floor"): say the degrees.
- Single-look previews through the stage page's `VIS|PREVIEW` relay (the page doesn't render them yet).
- The 3D positions are provisional: set `positions_final: true` once measured, then use Re-aim.
