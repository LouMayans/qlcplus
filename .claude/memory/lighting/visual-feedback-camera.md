---
name: visual-feedback-camera
description: "How to SEE and TEST the rig: capture the lights on a USB camera + frames, and drive QLC+ over the Web Access WebSocket API (trigger functions, set RAW DMX channels via the CH command, blackout). The control-API reference + camera loop other chats need so they don't re-research it."
metadata:
  node_type: memory
  type: reference
---

# Seeing & testing the rig — camera loop + QLC+ WebSocket control

This is the closed feedback loop that lets an assistant *see* a light show and iterate, and the
authoritative reference for **driving QLC+ programmatically** (triggering functions, setting raw
DMX channels, blacking out) for testing and venue discovery. It's venue-agnostic; the active venue
comes from [[lighting-venues]]. Scripts live in `.claude/scripts/` (portable — see "Portability").

**The loop:** trigger something in QLC+ → capture it on the camera → extract frames → open/analyze
the frames → adjust the `.qxw` (or the DMX) → repeat. The Read tool can view PNG/JPG **frames** but
**not video**, so ffmpeg extracts frames first.

## QLC+ Web Access WebSocket control API (the reusable reference)

> Source of truth: `webaccess/src/webaccess.cpp` (`WebAccess::slotHandleWebSocketMessage` — the
> `cmdList[0]` dispatch). Don't re-research this; update here if the source changes.

**Endpoint:** `ws://<host>:9999/qlcplusWS` (plain) or `wss://<host>:9999/qlcplusWS` (TLS). Frames are
**pipe-delimited UTF-8 text**. QLC+ must be launched with web access on:

```
qlcplus.exe -w -p -o "<project.qxw>"      # -w web access, -p operate mode, -o load project
#   TLS:  add  --web-cert cert.pem --web-key key.pem   -> wss://
#   auth: add  -a <web-auth-file>                       -> Basic auth + per-command access levels
```

| Message (send) | Effect | Reply |
|---|---|---|
| `QLC+API\|setFunctionStatus\|<funcID>\|1` | **Start** a function (scene/chaser/EFX/show/collection) by ID | — |
| `QLC+API\|setFunctionStatus\|<funcID>\|0` | **Stop** that function | — |
| `QLC+API\|getFunctionsList` | List every function | `QLC+API\|getFunctionsList\|<id>\|<name>\|<type>\|…` |
| `QLC+API\|getFunctionsNumber` | Count of functions | number |
| `QLC+API\|isProjectLoaded` | Whether a project is loaded | bool |
| `CH\|<absAddr>\|<value>` | **Set a RAW DMX channel** via Simple Desk (`setAbsoluteChannelValue`). `absAddr` is **1-based**; value 0–255 | — |
| `QLC+API\|sdResetUniverse\|<u>` | **Blackout**: clear all Simple Desk overrides on universe `<u>` (**1-based**) | channel values |
| `QLC+API\|sdResetChannel\|<absAddr>` | Clear one Simple Desk channel (`absAddr` 1-based) | channel values |
| `QLC+API\|getChannelsValues\|<u>\|<start>\|<count>` | Read DMX values (`u`, `start` 1-based) | values |
| `GM_VALUE\|<0-255>` | Set the Grand Master | — |
| `<widgetID>\|<value>` | Press a Virtual Console widget (button/slider) by its widget ID | — |

**Raw-channel addressing (the key fact for testing what a channel does):**
`absAddr (1-based) = universeIndex(0-based) * 512 + channel(0-based) + 1`.
So Universe 1 (index 0) channel 0 → `CH|1|255`; Universe 2 (index 1) channel 0 → `CH|513|255`.
Simple Desk values are LTP overrides mixed into the DMX output and **persist until reset** (so always
`sdResetUniverse` between probes). They layer on top of any running function.

**Auth levels** (only enforced when `-a` is set): `setFunctionStatus` and `LOOP` need `VC_ONLY_LEVEL`;
`CH`, `getChannelsValues`, `sdReset*` need `SIMPLE_DESK_AND_VC_LEVEL`. Local dev (no `-a`) needs none.
The fork also adds a server-side chaser auto-loop (`LOOP|…`) used by `webaccess/res/control.html`.

## One-time setup per PC

The whole system is self-contained in the repo and moves with the branch. On a **new machine** only
the camera device name (and possibly tool paths) differ — re-run setup:

1. `pwsh .claude/scripts/list-cameras.ps1` — lists DirectShow video devices and writes the chosen
   one into `rig-capture.local.json` (gitignored, per-PC). Pass `-Set "<name>"` if there are several.
2. Ensure `ffmpeg` is on PATH (it's the one external dependency; see [[build-procedure]]). QLC+ build
   is per [[build-procedure]].
3. Launch QLC+ with web access: `qlcplus.exe -w -p -o "<active venue .qxw>"` (deploy/start-qlcplus.bat
   does this with TLS).

## The scripts (`.claude/scripts/`)

| Script | Purpose | Example |
|---|---|---|
| `list-cameras.ps1` | Discover + save this PC's camera | `list-cameras.ps1 -Set "USB Video"` |
| `capture-rig.ps1` | Capture a burst (or `-Single` still) and print frame paths | `capture-rig.ps1 -Seconds 4 -Fps 4` |
| `trigger-function.ps1` | Start/stop a function by ID; `-List` dumps id/name/type | `trigger-function.ps1 -Id 45` |
| `set-channel.ps1` | Set one raw DMX channel (`CH`) or `-Reset` a universe | `set-channel.ps1 -Universe 0 -Channel 5 -Value 255` |
| `observe-function.ps1` | **The verify loop:** trigger → settle → capture burst → stop → list frames | `observe-function.ps1 -Id 45` |
| `probe-channels.ps1` | **Discovery:** sweep a channel range, capture each state, write a manifest | `probe-channels.ps1 -Universe 0 -From 0 -To 15` |

`_common.ps1` is the shared library (path/tool resolution, the WS helpers `Start/Stop-QlcFunction`,
`Set-QlcChannel`, `Reset-QlcUniverse`, `Send-QlcWs`, and `Invoke-RigCapture`). Config:
`rig-capture.config.json` (tracked: active venue, ws url, fps/seconds, venue→qxw) + per-PC
`rig-capture.local.json` (gitignored: camera, optional tool paths). See `rig-capture.local.example.json`.

## Verify / iterate a show (CREATE & UPDATE)

After writing or editing a function, if a camera + a running QLC+ are available:
1. `observe-function.ps1 -Id <newFuncID>` — triggers it, captures a burst, lists the frames.
2. **Open the frames** and judge against intent: right fixtures lit? colors as designed? beams aimed
   where expected? movement/chase visible across the burst frames? strobe/coverage right?
3. Adjust the `.qxw` (recolor, retime, repan, fix channels) and re-run until it matches.
4. If QLC+/camera/rig isn't reachable, **degrade gracefully**: skip the loop and tell the operator
   how to test manually (load in QLC+, run the function) — same as the pre-camera behavior.

## Discover / learn a venue (when fixtures weren't given)

To map an unknown rig (see [[lighting-venues]] and [[qlc-fixture-definition-format]]):
1. Blackout, then sweep with `probe-channels.ps1` (it uses `CH` + `sdResetUniverse` per channel and
   captures a still of each state). Use `-Values 0,64,128,192,255` on a channel to reveal range
   behavior (color wheel positions, pan/tilt travel, dimmer threshold).
2. **Read the per-channel frames** to infer: which physical fixture responds, where it sits/aims,
   and what each channel does (intensity / color / pan / tilt / gobo / strobe).
3. Group contiguous channels into fixtures; **author a `.qxf`** per discovered model and a patch
   plan; confirm with the operator before committing.
4. Record positions + confirmed channel behavior in the venue's `observed.md`; note any value→effect
   mappings, and flag conflicts with the documented patch.

## What to look for in frames
Which fixtures are lit vs dark; actual color vs intended; beam aim/position and what it lands on;
room coverage; movement across burst frames; strobe; any dead/wrong fixture. Record a fixed **camera
vantage** in `observed.md` so "house-left/right" is unambiguous.

## Gotchas
- **Read can't open video** — always extract frames with ffmpeg first (the scripts do).
- **Dark club exposure** — beams blow out, the room is black. Prefer **bursts** over single stills
  for movement; grab a blackout reference still first; webcam auto-exposure may need a manual setting.
- **Scripts are ASCII-only** — Windows PowerShell 5.1 mis-reads non-ASCII in a no-BOM `.ps1`. No
  em-dashes/smart quotes in the scripts.
- **Frames are scratch** — they land in `.claude/scratch/` (gitignored): structure travels, binaries
  don't. Don't commit captures.
- **QLC+ must run with `-w`**; for `CH`/Simple-Desk the project should be loaded and in operate mode.
- **Portability:** no script hardcodes a machine path — repo root from `$PSScriptRoot`, tools from
  PATH (config override as fallback), per-PC values isolated to the gitignored local file. Moving the
  branch carries everything; only re-run `list-cameras.ps1` on the new PC.

Related: [[lighting-venues]] (active venue + registry), [[club-rig-mayans]] (Mayans patch/channels —
the `CH` target addresses), [[qlc-save-file-format]], [[qlc-fixture-definition-format]],
[[effect-recipes-cookbook]], [[salesforce-qlcplus-integration]] (the same WS API used remotely).
