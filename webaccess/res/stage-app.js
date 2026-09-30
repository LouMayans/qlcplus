/*
  stage-app.js

  Bootstrap and shared state for the /stage page. Owns:
    - the `app` object passed to stage-editor.js / stage-props.js
    - WebSocket wiring (or #demo's fake rig + animated DMX)
    - draft/saved tracking, Save/Revert, the dirty badge, beforeunload
    - following the QLC+ show currently loaded (switch/reload banner)
    - the default fixture layout for a show with no stage file yet
    - undo/redo history (draft edits + camera moves)
    - the Settings popup (FOV/anamorphic scale/beam look, per-browser)
    - mode switching (View/Edit/Props)
    - the render loop's per-frame DMX -> fixture state pipeline
    - window.__stage, and the #selftest panel

  `app` contract (read by stage-editor.js and stage-props.js):
    app.demo, app.ws, app.mode, app.rigIndex, app.dmxByUniverse
    app.stage = { saved, draft, dirty, showPath, fileExists }
    app.props = { saved, draft, editingId, selectedPartId, dirty, onChanged }
    app.selection (Set of "fixture:<id>" / "object:<id>")
    app.scene (stage-scene.js's createSceneManager() result)
    app.editor (set by stage-editor.js: { refreshAll, placeFixture, gizmoMode,
      duplicateObject, deleteObject, setLocked })
    app.rebuildSceneFromDraft(), app.markStageDirty(), app.propDefLookup(obj)
    app.commitDraftChange(mutateFn) - wraps a draft mutation: marks dirty,
      pushes one undo/redo entry (before/after deep clones) and rebuilds.
    app.savePropsLibrary(), app.placePropInstance(propId, posOverride)
    app.showToast(text)
*/

import { StageWS, decodeDmxFrame } from "./stage-ws.js";
import { selftest as unitsSelftest } from "./stage-units.js";
import { buildRigIndex, computeFixtureState, selftest as rigSelftest } from "./stage-rig.js";
import { createSceneManager, userToThree, threeToUser } from "./stage-scene.js";
import { initEditorMode } from "./stage-editor.js";
import { initPropsMode, STARTER_PROPS, pendingAssets } from "./stage-props.js";

const DEMO = location.hash.indexOf("demo") !== -1;
const SELFTEST = location.hash.indexOf("selftest") !== -1;

// Operator preference: no native scrollbar chrome anywhere (stage.css hides
// the thumb by default). "scroll" events don't bubble, so a capturing
// listener on the document is the only way to catch them from any
// descendant (side panel, lists, settings popup, context menus, look
// gallery, properties) without wiring every scrollable container by hand;
// a ".scrolling" class makes the thumb visible for ~800ms after the last
// scroll tick (stage.css also shows it on plain :hover).
(function setupScrollIndicators() {
  const timers = new WeakMap();
  document.addEventListener(
    "scroll",
    (ev) => {
      const el = ev.target;
      if (!el || !el.classList) return;
      el.classList.add("scrolling");
      clearTimeout(timers.get(el));
      timers.set(
        el,
        setTimeout(() => el.classList.remove("scrolling"), 800)
      );
    },
    true
  );
})();

function deepClone(o) {
  return JSON.parse(JSON.stringify(o));
}

function showBaseName(path) {
  if (!path) return "";
  const norm = String(path).replace(/\\/g, "/");
  const parts = norm.split("/");
  return parts[parts.length - 1] || norm;
}

// ------------------------------------------------------ default layout --
// (item 6) A show with no stage file yet gets EVERY fixture placed
// somewhere inside a default room, so nothing is left in "Unplaced".

const ROOM_DEFAULT = { width: 480, depth: 432, height: 168, haze: 0.35 }; // 40'x36'x14'
const MARGIN_IN = 24; // 2' from the walls
const HUNG_Z = 13 * 12;
const FLOOR_Z = 0;

function round16(v) {
  return Math.round(v * 16) / 16;
}
function clampIn(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function fixtureEntry(model, pos, rot, hang) {
  return {
    model: model.manufacturer + "/" + model.model,
    pos: pos,
    rot: rot,
    hang: hang,
    invertPan: false,
    invertTilt: false,
    panOffset: 0,
    tiltOffset: 0,
    look: null,
  };
}

// rig.monitor.grid is in "grid units" (m or ft); fixture.monitor.x/y are mm.
function monitorGridSizeMM(mon) {
  if (!mon || !mon.grid || !(mon.grid[0] > 0) || !(mon.grid[2] > 0)) return null;
  const k = mon.units === "ft" ? 304.8 : 1000;
  return { w: mon.grid[0] * k, d: mon.grid[2] * k };
}

function categorizeKind(kind) {
  if (kind === "movingHead" || kind === "fx") return "head";
  if (kind === "par" || kind === "bar" || kind === "panel" || kind === "strobe") return "floor";
  return "back"; // fog/hazer/other
}

function isMonitorPlaceable(model, monGrid) {
  return !!(monGrid && model.monitorSeed && (model.monitorSeed.x || model.monitorSeed.y));
}

// Places fixtures with a QLC+ 2D Monitor position proportionally inside the
// (possibly different-sized) room - never shrinks the room to the grid.
function placeMonitorFixtures(models, draft, monGrid) {
  models.forEach((m) => {
    const seed = m.monitorSeed;
    const x = clampIn((seed.x / monGrid.w) * draft.room.width, MARGIN_IN, draft.room.width - MARGIN_IN);
    const y = clampIn((seed.y / monGrid.d) * draft.room.depth, MARGIN_IN, draft.room.depth - MARGIN_IN);
    draft.fixtures[m.id] = fixtureEntry(m, [round16(x), round16(y), HUNG_Z], [0, 0, seed.rot || 0], "hung");
  });
}

// Lays out `models` in left-to-right rows that wrap to stay inside the
// room, continuing from `startIndex` so a later auto-place of newly added
// fixtures (autoPlaceMissing) doesn't overlap a row already laid out.
function packRow(models, draft, opts, startIndex) {
  if (!models.length) return;
  const usableW = Math.max(draft.room.width - MARGIN_IN * 2, opts.spacing);
  const perRow = Math.max(1, Math.floor(usableW / opts.spacing) + 1);
  models.forEach((m, i) => {
    const idx = startIndex + i;
    const row = Math.floor(idx / perRow);
    const col = idx % perRow;
    const x = MARGIN_IN + (perRow <= 1 ? usableW / 2 : col * (usableW / (perRow - 1 || 1)));
    const y = clampIn(opts.yStart + opts.yDir * row * opts.spacing, MARGIN_IN, draft.room.depth - MARGIN_IN);
    draft.fixtures[m.id] = fixtureEntry(m, [round16(x), round16(y), opts.z], [0, 0, 0], opts.hang);
  });
}

function bucketize(models, monGrid) {
  const monitorModels = [];
  const buckets = { head: [], floor: [], back: [] };
  models.forEach((m) => {
    if (isMonitorPlaceable(m, monGrid)) monitorModels.push(m);
    else buckets[categorizeKind(m.kind)].push(m);
  });
  return { monitorModels, buckets };
}

function layoutBuckets(buckets, draft, startCounts) {
  // moving heads/scanners: hung rows across the room, ~4' spacing
  packRow(buckets.head, draft, { spacing: 4 * 12, z: HUNG_Z, hang: "hung", yStart: MARGIN_IN, yDir: 1 }, startCounts.head);
  // pars/bars/panels/strobes: a floor row near the front, ~3' spacing
  packRow(buckets.floor, draft, { spacing: 3 * 12, z: FLOOR_Z, hang: "floor", yStart: MARGIN_IN, yDir: 1 }, startCounts.floor);
  // fog/hazer/fx/other: along the back wall
  packRow(
    buckets.back,
    draft,
    { spacing: 3 * 12, z: FLOOR_Z, hang: "floor", yStart: draft.room.depth - MARGIN_IN, yDir: -1 },
    startCounts.back
  );
}

/** First layout for a show with no stage file: every fixture gets a spot. */
function defaultLayoutAll(draft, rigIndex) {
  draft.room = Object.assign({}, ROOM_DEFAULT, { haze: (draft.room && draft.room.haze) || ROOM_DEFAULT.haze });
  draft.anchor = [Math.round(draft.room.width / 2), Math.round(draft.room.depth / 2), 0];
  draft.fixtures = {};
  const monGrid = monitorGridSizeMM(rigIndex.monitor);
  const { monitorModels, buckets } = bucketize(rigIndex.list, monGrid);
  placeMonitorFixtures(monitorModels, draft, monGrid);
  layoutBuckets(buckets, draft, { head: 0, floor: 0, back: 0 });
}

/** New fixture ids that showed up after RIG_CHANGED get auto-placed too. */
function autoPlaceMissing(draft, rigIndex) {
  const missing = rigIndex.list.filter((m) => !draft.fixtures[m.id]);
  if (!missing.length) return 0;
  const monGrid = monitorGridSizeMM(rigIndex.monitor);
  const { monitorModels, buckets } = bucketize(missing, monGrid);
  placeMonitorFixtures(monitorModels, draft, monGrid);
  // continue each bucket's row/column sequence from how many of that kind
  // are already placed, so new fixtures don't overlap the existing layout
  const startCounts = { head: 0, floor: 0, back: 0 };
  Object.keys(draft.fixtures).forEach((idStr) => {
    const m = rigIndex.byId.get(Number(idStr));
    if (m && !isMonitorPlaceable(m, monGrid)) startCounts[categorizeKind(m.kind)]++;
  });
  layoutBuckets(buckets, draft, startCounts);
  return missing.length;
}

// Demo mode: hang the fake rig on a truss line and add a booth and dance
// floor, so the view shows moving, coloured beams without any setup.
function demoLayout(draft, rig) {
  const D = draft.room.depth;
  const place = (f, pos, hang) => {
    draft.fixtures[String(f.id)] = { model: f.manufacturer + "/" + f.model, pos: pos, rot: [0, 0, 0], hang: hang,
      invertPan: false, invertTilt: false, panOffset: 0, tiltOffset: 0, look: null };
  };
  const heads = rig.fixtures.filter((f) => f.type === "Moving Head");
  const pars = rig.fixtures.filter((f) => f.type === "LED Par");
  const bars = rig.fixtures.filter((f) => f.type === "LED Bar (Pixels)");
  const others = rig.fixtures.filter((f) => heads.indexOf(f) < 0 && pars.indexOf(f) < 0 && bars.indexOf(f) < 0);
  heads.forEach((f, i) => place(f, [90 + i * 60, D * 0.75, 156], "hung"));
  pars.forEach((f, i) => place(f, [150 + i * 100, D * 0.92, 0], "floor"));
  bars.forEach((f, i) => place(f, [200 + i * 200, D * 0.97, 0], "floor"));
  others.forEach((f, i) => place(f, [40 + i * 30, D * 0.95, 0], "floor"));
  draft.objects.push(
    { id: "demo-booth", prop: "dj-booth", name: "DJ booth", category: "booth", aliases: ["the booth"], pos: [300, D * 0.9, 0], rz: 0, scale: [1, 1, 1], color: null },
    { id: "demo-floor", prop: "dance-floor", name: "Dance floor", category: "floor_area", aliases: [], pos: [300, D * 0.42, 0], rz: 0, scale: [1, 1, 1], color: null }
  );
}

function defaultStageDraft() {
  return {
    version: 1,
    units: "in",
    room: Object.assign({}, ROOM_DEFAULT),
    anchor: [Math.round(ROOM_DEFAULT.width / 2), Math.round(ROOM_DEFAULT.depth / 2), 0],
    fixtures: {},
    models: {},
    objects: [],
    propDefs: {},
  };
}

// (item 7) Starters are always available even if the library file predates
// them: merge them UNDER whatever the shared library holds (library wins).
function mergeWithStarters(lib) {
  return { version: (lib && lib.version) || 1, props: Object.assign({}, deepClone(STARTER_PROPS), (lib && lib.props) || {}) };
}
function defaultPropsLibrary() {
  return mergeWithStarters(null);
}

// ------------------------------------------------------------ app state --

const app = {
  demo: DEMO,
  ws: null,
  mode: "view", // operator default (2026-09-29): View mode on load - look-only until the operator opts into Edit
  t: 0,
  rigRaw: null,
  rigIndex: null,
  dmxByUniverse: {},
  // Perf item 5: the per-frame loop only recomputes fixture DMX->visual
  // state when this is true (a new DMX frame arrived over the websocket, or
  // the fixture rig itself was just rebuilt) - see onDmx and
  // rebuildSceneFromDraft below, and the setOnFrame callback in boot().
  // Starts true so the very first frame (before any DMX has arrived) still
  // renders fixtures at their default/home pose instead of nothing.
  _dmxDirty: true,
  // `draft` starts populated with sane defaults (not null) so that
  // initEditorMode()/initPropsMode() can safely render immediately during
  // boot(), before the async getStage/getProps replies (or demo-mode
  // localStorage load) arrive and replace it.
  stage: { saved: null, draft: defaultStageDraft(), dirty: false, showPath: null, fileExists: false },
  props: { saved: null, draft: defaultPropsLibrary(), editingId: null, selectedPartId: null, dirty: false, onChanged: null },
  selection: new Set(),
  scene: null,
  editor: null,
};

function propDefLookup(objInstance) {
  const live = app.props.draft && app.props.draft.props && app.props.draft.props[objInstance.prop];
  if (live) return live;
  return (app.stage.draft.propDefs && app.stage.draft.propDefs[objInstance.prop]) || null;
}
app.propDefLookup = propDefLookup;

function rebuildSceneFromDraft() {
  if (!app.scene) return;
  if (app.rigRaw) app.rigIndex = buildRigIndex(app.rigRaw, app.stage.draft.models);
  app.scene.setRoom(app.stage.draft.room);
  // re-frame the camera when the room size changes (first load, default
  // layout, edited room)
  const r = app.stage.draft.room;
  const roomKey = [r.width, r.depth, r.height].join("x");
  if (roomKey !== app.framedRoom) {
    app.framedRoom = roomKey;
    const sel = document.getElementById("camera-preset-select");
    app.scene.cameraPreset((sel && sel.value) || "foh");
  }
  app.scene.setAnchor(app.stage.draft.anchor);
  app.scene.rebuildFixtures(app.rigIndex, app.stage.draft.fixtures, app.stage.draft.models);
  app._dmxDirty = true; // freshly (re)built fixtures need one state pass even without a new DMX frame
  app.scene.rebuildObjects(app.stage.draft.objects, app.propDefLookup);
  if (app.editor && app.editor.refreshAll) app.editor.refreshAll();
  if (app._seatsRefresh) app._seatsRefresh();
}
app.rebuildSceneFromDraft = rebuildSceneFromDraft;

function updateDirtyBadge() {
  const badge = document.getElementById("dirty-badge");
  if (badge) badge.classList.toggle("hidden", !app.stage.dirty);
}

function markStageDirty() {
  if (!app.stage.saved) app.stage.dirty = true;
  else app.stage.dirty = JSON.stringify(app.stage.draft) !== JSON.stringify(app.stage.saved);
  updateDirtyBadge();
}
app.markStageDirty = markStageDirty;

// -------------------------------------------------------------- toasts --

function showToast(text) {
  const el = document.createElement("div");
  el.className = "stage-toast";
  el.textContent = text;
  document.body.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 300);
  }, 3200);
}
app.showToast = showToast;

// Item 8: once the real WebGL renderer string is known (see stage-scene.js's
// gpuRendererString / getRenderStats().gpuRenderer), warn the operator once
// if it's a software rasterizer (SwiftShader, llvmpipe, "Software" in
// ANGLE's string) rather than the actual GPU - the single most common cause
// of "this runs fully on the CPU" on a machine that does have working
// graphics hardware.
let gpuWarningShown = false;
function checkGpuRendererOnce(rendererString) {
  if (gpuWarningShown || !rendererString) return;
  gpuWarningShown = true;
  if (/swiftshader|llvmpipe|software/i.test(rendererString)) {
    showToast("The browser is rendering 3D on the CPU - turn on Settings → System → Use graphics acceleration in Edge");
  }
}

// --------------------------------------------------- undo/redo history --
// (item 3) One chronological stack covering both draft edits (deep-cloned
// before/after snapshots of app.stage.draft) and camera moves (position +
// orbit target). Capped at 200 entries; a new action clears the redo stack.

const HISTORY_CAP = 200;
const history = { undo: [], redo: [] };

function updateHistoryButtons() {
  const u = document.getElementById("undo-btn");
  const r = document.getElementById("redo-btn");
  if (u) u.disabled = history.undo.length === 0;
  if (r) r.disabled = history.redo.length === 0;
}

function pushHistoryEntry(entry) {
  history.undo.push(entry);
  if (history.undo.length > HISTORY_CAP) history.undo.shift();
  history.redo.length = 0;
  updateHistoryButtons();
}
function pushDraftHistory(before, after) {
  if (JSON.stringify(before) === JSON.stringify(after)) return;
  pushHistoryEntry({ type: "draft", before: before, after: after });
}
function pushCameraHistory(before, after) {
  if (JSON.stringify(before) === JSON.stringify(after)) return;
  pushHistoryEntry({ type: "camera", before: before, after: after });
}
function resetHistory() {
  history.undo.length = 0;
  history.redo.length = 0;
  updateHistoryButtons();
}

// Wraps a stage-draft mutation: marks the draft dirty, pushes one
// undo/redo entry (skipped if the mutation was a no-op) and rebuilds the
// scene. Every committed edit in stage-editor.js/stage-app.js goes through
// this, so Ctrl+Z/Ctrl+Y cover them uniformly.
function commitDraftChange(mutateFn) {
  const before = deepClone(app.stage.draft);
  mutateFn();
  const after = deepClone(app.stage.draft);
  pushDraftHistory(before, after);
  markStageDirty();
  rebuildSceneFromDraft();
}
app.commitDraftChange = commitDraftChange;

function applyHistoryEntry(entry, useAfter) {
  if (entry.type === "draft") {
    app.stage.draft = deepClone(useAfter ? entry.after : entry.before);
    app.selection.clear();
    markStageDirty();
    rebuildSceneFromDraft();
  } else if (entry.type === "camera" && app.scene) {
    app.scene.setCameraState(useAfter ? entry.after : entry.before);
  }
}
function undo() {
  if (!history.undo.length) return false;
  const entry = history.undo.pop();
  applyHistoryEntry(entry, false);
  history.redo.push(entry);
  updateHistoryButtons();
  return true;
}
function redo() {
  if (!history.redo.length) return false;
  const entry = history.redo.pop();
  applyHistoryEntry(entry, true);
  history.undo.push(entry);
  updateHistoryButtons();
  return true;
}

// --------------------------------------------------------- view settings --
// (items 11/12/13) FOV, anamorphic scale, beam look, room box/grid - all
// per-browser (localStorage), never part of the stage file or history.

const VIEW_SETTINGS_KEY = "qlcplus-stage-view-settings";
function loadViewSettingsFromStorage() {
  try {
    const raw = localStorage.getItem(VIEW_SETTINGS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}
function saveViewSettingsToStorage(vs) {
  try {
    localStorage.setItem(VIEW_SETTINGS_KEY, JSON.stringify(vs));
  } catch (e) {
    /* private browsing etc: ignore */
  }
}

function updateHudLegend(vs) {
  const el = document.getElementById("hud-legend");
  if (!el) return;
  const wheelText =
    vs.wheelMode === "zoom" ? "Wheel zoom" : vs.wheelMode === "speed" ? "Wheel adjusts move speed" : "Wheel off";
  const middleText = vs.orbitMiddle ? "Middle-drag orbit" : "Middle-drag look";
  el.textContent = app.mode === "edit"
    ? "WASD move · Q/E down/up · Arrows look · Shift fast · 1/2/3 gizmo · Left-click select · " +
      "Left-drag pan/move · " + middleText + " · " + wheelText + " · Right-click menu · " +
      "Hold Alt move / Shift rotate / Ctrl scale · Ctrl+Z undo / Ctrl+Y redo"
    : "WASD move · Q/E down/up · Arrows look · Shift fast · Click: identify (again or Esc clears) · " +
      "Left-drag pan · " + middleText + " · " + wheelText + " · Edit mode to change the layout";
}

// ------------------------------------------------- camera controls glue --
// stage-camera.js (a separate module, dynamically imported below so the
// page still boots if it isn't present yet) owns WASD/QE fly, middle-drag
// mouselook, wheel zoom/speed and their own camera-history entries. This
// file only supplies it the settings/blocking/history/toast glue.

function isCameraInputBlocked() {
  const ae = document.activeElement;
  if (ae) {
    const tag = ae.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || ae.isContentEditable) return true;
  }
  if (app.editor && app.editor.isManipulating && app.editor.isManipulating()) return true;
  if (app.scene && app.scene.transform && app.scene.transform.dragging) return true;
  const popupIds = ["settings-popup", "context-menu", "look-gallery-popover", "delete-confirm"];
  for (const id of popupIds) {
    const el = document.getElementById(id);
    if (el && !el.classList.contains("hidden")) return true;
  }
  return false;
}

// Passed to stage-camera.js both as its live getSettings() and pushed again
// via setSettings() whenever a Settings-popup control changes. Field names
// are offered under both this page's own names (orbitSensitivity/
// panSensitivity/smoothMove) and the shorter names used in the operator's
// spec (lookSensitivity/panSpeed/smooth), since the two modules were written
// in parallel - cheap insurance against a naming mismatch.
function cameraSettingsSnapshot() {
  const vs = app.scene ? app.scene.getViewSettings() : {};
  return {
    moveSpeedFtS: vs.moveSpeedFtS,
    fastMul: vs.fastMul,
    vertSpeedFtS: vs.vertSpeedFtS,
    lookSensitivity: vs.orbitSensitivity,
    orbitSensitivity: vs.orbitSensitivity,
    panSpeed: vs.panSensitivity,
    panSensitivity: vs.panSensitivity,
    smooth: vs.smoothMove,
    smoothMove: vs.smoothMove,
    keepLevel: !!vs.keepLevel,
    orbitMiddle: !!vs.orbitMiddle,
    wheelMode: vs.wheelMode,
  };
}

async function initCameraControlsGlue() {
  try {
    const mod = await import("./stage-camera.js");
    if (!mod || !mod.initCameraControls) return null;
    return mod.initCameraControls({
      camera: app.scene.camera,
      orbit: app.scene.orbit,
      domElement: app.scene.renderer.domElement,
      getSettings: cameraSettingsSnapshot,
      isBlocked: isCameraInputBlocked,
      onHistory: pushCameraHistory,
      toast: showToast,
    });
  } catch (e) {
    // Not delivered yet (or failed to load) - the page still works, just
    // without WASD fly / middle-drag mouselook until it lands.
    console.warn("stage-camera.js not available - WASD/middle-drag camera controls disabled", e);
    return null;
  }
}

function wireSettingsPopup() {
  const gearBtn = document.getElementById("settings-btn");
  const popup = document.getElementById("settings-popup");
  if (!gearBtn || !popup) return;

  const els = {
    fovSlider: document.getElementById("fov-slider"),
    fovValue: document.getElementById("fov-value"),
    beamWidth: document.getElementById("beam-width-slider"),
    beamWidthValue: document.getElementById("beam-width-value"),
    beamBrightness: document.getElementById("beam-brightness-slider"),
    beamBrightnessValue: document.getElementById("beam-brightness-value"),
    beamLength: document.getElementById("beam-length-input"),
    roomBoxToggle: document.getElementById("room-box-toggle"),
    floorGridToggle: document.getElementById("floor-grid-toggle"),
    closeBtn: document.getElementById("settings-close-btn"),
    closeFooterBtn: document.getElementById("settings-close-footer-btn"),
    resetTabBtn: document.getElementById("settings-reset-tab-btn"),
    resetAllBtn: document.getElementById("settings-reset-all-btn"),
    // item 12/13: WASD/QE fly camera + mouse feel
    moveSpeed: document.getElementById("move-speed-slider"),
    moveSpeedValue: document.getElementById("move-speed-value"),
    fastMul: document.getElementById("fast-mul-slider"),
    fastMulValue: document.getElementById("fast-mul-value"),
    vertSpeed: document.getElementById("vert-speed-slider"),
    vertSpeedValue: document.getElementById("vert-speed-value"),
    orbitSens: document.getElementById("orbit-sens-slider"),
    orbitSensValue: document.getElementById("orbit-sens-value"),
    panSens: document.getElementById("pan-sens-slider"),
    panSensValue: document.getElementById("pan-sens-value"),
    smoothMove: document.getElementById("smooth-move-toggle"),
    keepLevel: document.getElementById("keep-level-toggle"),
    orbitMiddle: document.getElementById("orbit-middle-toggle"),
    wheelMode: document.getElementById("wheel-mode-select"),
  };

  function applyToUI(vs) {
    if (els.fovSlider) {
      els.fovSlider.value = vs.fov;
      els.fovValue.textContent = Math.round(vs.fov) + "°";
    }
    if (els.beamWidth) {
      els.beamWidth.value = vs.beamWidthMul;
      els.beamWidthValue.textContent = Number(vs.beamWidthMul).toFixed(2);
    }
    if (els.beamBrightness) {
      els.beamBrightness.value = vs.beamBrightnessMul;
      els.beamBrightnessValue.textContent = Number(vs.beamBrightnessMul).toFixed(2);
    }
    if (els.beamLength) els.beamLength.value = vs.beamLengthFt;
    if (els.roomBoxToggle) els.roomBoxToggle.checked = vs.showRoomBox !== false;
    if (els.floorGridToggle) els.floorGridToggle.checked = vs.showFloorGrid !== false;
    if (els.moveSpeed) {
      els.moveSpeed.value = vs.moveSpeedFtS;
      els.moveSpeedValue.textContent = Math.round(vs.moveSpeedFtS);
    }
    if (els.fastMul) {
      els.fastMul.value = vs.fastMul;
      els.fastMulValue.textContent = Number(vs.fastMul).toFixed(1) + "×";
    }
    if (els.vertSpeed) {
      els.vertSpeed.value = vs.vertSpeedFtS;
      els.vertSpeedValue.textContent = Math.round(vs.vertSpeedFtS);
    }
    if (els.orbitSens) {
      els.orbitSens.value = vs.orbitSensitivity;
      els.orbitSensValue.textContent = Number(vs.orbitSensitivity).toFixed(2) + "×";
    }
    if (els.panSens) {
      els.panSens.value = vs.panSensitivity;
      els.panSensValue.textContent = Number(vs.panSensitivity).toFixed(2) + "×";
    }
    if (els.smoothMove) els.smoothMove.checked = vs.smoothMove !== false;
    if (els.keepLevel) els.keepLevel.checked = !!vs.keepLevel;
    if (els.orbitMiddle) els.orbitMiddle.checked = !!vs.orbitMiddle;
    if (els.wheelMode) els.wheelMode.value = vs.wheelMode || "off";
    updateHudLegend(vs);
  }

  function apply(patch) {
    app.scene.setViewSettings(patch);
    const vs = app.scene.getViewSettings();
    saveViewSettingsToStorage(vs);
    applyToUI(vs);
    if (app.cameraControls && app.cameraControls.setSettings) app.cameraControls.setSettings(cameraSettingsSnapshot());
  }
  app._applyViewSettings = apply;

  apply(loadViewSettingsFromStorage());

  if (els.fovSlider) els.fovSlider.addEventListener("input", () => apply({ fov: parseFloat(els.fovSlider.value) }));
  if (els.beamWidth) els.beamWidth.addEventListener("input", () => apply({ beamWidthMul: parseFloat(els.beamWidth.value) }));
  if (els.beamBrightness) els.beamBrightness.addEventListener("input", () => apply({ beamBrightnessMul: parseFloat(els.beamBrightness.value) }));
  if (els.beamLength) els.beamLength.addEventListener("change", () => apply({ beamLengthFt: Math.max(5, parseFloat(els.beamLength.value) || 40) }));
  if (els.roomBoxToggle) els.roomBoxToggle.addEventListener("change", () => apply({ showRoomBox: els.roomBoxToggle.checked }));
  if (els.floorGridToggle) els.floorGridToggle.addEventListener("change", () => apply({ showFloorGrid: els.floorGridToggle.checked }));
  if (els.moveSpeed) els.moveSpeed.addEventListener("input", () => apply({ moveSpeedFtS: parseFloat(els.moveSpeed.value) }));
  if (els.fastMul) els.fastMul.addEventListener("input", () => apply({ fastMul: parseFloat(els.fastMul.value) }));
  if (els.vertSpeed) els.vertSpeed.addEventListener("input", () => apply({ vertSpeedFtS: parseFloat(els.vertSpeed.value) }));
  if (els.orbitSens) els.orbitSens.addEventListener("input", () => apply({ orbitSensitivity: parseFloat(els.orbitSens.value) }));
  if (els.panSens) els.panSens.addEventListener("input", () => apply({ panSensitivity: parseFloat(els.panSens.value) }));
  if (els.smoothMove) els.smoothMove.addEventListener("change", () => apply({ smoothMove: els.smoothMove.checked }));
  if (els.keepLevel) els.keepLevel.addEventListener("change", () => apply({ keepLevel: els.keepLevel.checked }));
  if (els.orbitMiddle) els.orbitMiddle.addEventListener("change", () => apply({ orbitMiddle: els.orbitMiddle.checked }));
  if (els.wheelMode) els.wheelMode.addEventListener("change", () => apply({ wheelMode: els.wheelMode.value }));

  document.querySelectorAll(".fov-preset").forEach((btn) => {
    btn.addEventListener("click", () => apply({ fov: parseFloat(btn.dataset.fov) }));
  });

  // ---- Redesigned modal: centered dialog, dark backdrop, Esc/backdrop-click
  // to close, a tab rail (CS2-style), and a fixed footer with per-tab and
  // whole-popup reset. The popup keeps the id "settings-popup" and its
  // hidden-class toggling so isCameraInputBlocked() (this file, above) and
  // the activity-bar gear button keep working unmodified. ----
  function closePopup() {
    popup.classList.add("hidden");
  }
  function openPopup() {
    popup.classList.remove("hidden");
  }
  gearBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (popup.classList.contains("hidden")) openPopup();
    else closePopup();
  });
  if (els.closeBtn) els.closeBtn.addEventListener("click", closePopup);
  if (els.closeFooterBtn) els.closeFooterBtn.addEventListener("click", closePopup);
  // Clicking the dark backdrop (a click whose target IS the outer overlay,
  // not the dialog box or anything inside it) closes the popup - the dialog
  // itself is a separate element so this needs no stopPropagation juggling.
  popup.addEventListener("click", (ev) => {
    if (ev.target === popup) closePopup();
  });
  // item 14a: scrolling the (now scrollable) settings body must not bubble
  // out to the page/3D view
  popup.addEventListener("wheel", (ev) => ev.stopPropagation());
  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !popup.classList.contains("hidden")) closePopup();
  });

  // ---- Tabs: Video / Graphics / Camera & Controls / Interface / Keys ----
  // Remembers the last-open tab (localStorage, try/catch - private
  // browsing etc). "Reset tab to defaults" acts on whichever tab is active.
  const TAB_IDS = ["video", "graphics", "camera", "interface", "keys"];
  const TAB_STORAGE_KEY = "qlcplus-stage-settings-tab";
  let activeTab = "video";
  try {
    const saved = localStorage.getItem(TAB_STORAGE_KEY);
    if (saved && TAB_IDS.indexOf(saved) !== -1) activeTab = saved;
  } catch (e) {
    /* private browsing etc: ignore */
  }
  function showTab(name) {
    if (TAB_IDS.indexOf(name) === -1) return;
    activeTab = name;
    document.querySelectorAll(".settings-tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    TAB_IDS.forEach((id) => {
      const el = document.getElementById("settings-tab-" + id);
      if (el) el.classList.toggle("hidden", id !== name);
    });
    if (els.resetTabBtn) els.resetTabBtn.disabled = name === "keys"; // Keys is a read-only reference
    try {
      localStorage.setItem(TAB_STORAGE_KEY, name);
    } catch (e) {
      /* ignore */
    }
  }
  document.querySelectorAll(".settings-tab-btn").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      showTab(btn.dataset.tab);
    });
  });
  showTab(activeTab);

  // ---- Keys tab: read-only reference built from the same KEYBINDS table
  // the standalone "?" keybind-help overlay uses (defined further down this
  // file; module-level `const`, already initialized by the time this runs -
  // wireSettingsPopup() is only called from the end-of-file init sequence). ----
  const keysBody = document.getElementById("settings-keybind-body");
  if (keysBody) {
    keysBody.innerHTML = "";
    KEYBINDS.forEach(([key, desc]) => {
      const row = document.createElement("div");
      row.className = "kb-row";
      const kbd = document.createElement("kbd");
      kbd.textContent = key;
      const span = document.createElement("span");
      span.textContent = desc;
      row.appendChild(kbd);
      row.appendChild(span);
      keysBody.appendChild(row);
    });
  }

  // ---- Reset: per-tab and "reset all" ----
  // Camera & Controls tab: FOV/movement/mouse-feel live in view settings
  // (apply()/app.scene.setViewSettings), plus the seat tour interval, which
  // is wired independently in wireSeats() - nudge it via its own "input"
  // listener rather than duplicating its storage/label logic here.
  function resetCameraTab() {
    apply({
      fov: 55,
      moveSpeedFtS: 8,
      fastMul: 3,
      vertSpeedFtS: 6,
      orbitSensitivity: 1,
      panSensitivity: 1,
      smoothMove: true,
      keepLevel: false,
      orbitMiddle: false,
      wheelMode: "off",
    });
    const tour = document.getElementById("tour-interval-slider");
    if (tour) {
      tour.value = 8;
      tour.dispatchEvent(new Event("input"));
    }
  }
  // Interface tab: scene-display toggles live in view settings; labels/perf
  // HUD/auto-perf are wired independently elsewhere in this file - flipping
  // the checkbox and dispatching "change" reuses that existing glue instead
  // of duplicating it.
  function resetInterfaceTab() {
    apply({ showRoomBox: true, showFloorGrid: true });
    [
      ["labels-toggle", true],
      ["perf-hud-toggle", false],
      ["auto-perf-toggle", true],
    ].forEach(([id, checked]) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.checked = checked;
      el.dispatchEvent(new Event("change"));
    });
  }
  function resetActiveTab() {
    if (activeTab === "video" && app._resetVideoTab) app._resetVideoTab();
    else if (activeTab === "graphics" && app._resetGraphicsTab) app._resetGraphicsTab();
    else if (activeTab === "camera") resetCameraTab();
    else if (activeTab === "interface") resetInterfaceTab();
  }
  function resetAll() {
    resetCameraTab();
    resetInterfaceTab();
    if (app._resetGraphicsAll) app._resetGraphicsAll();
  }
  if (els.resetTabBtn) els.resetTabBtn.addEventListener("click", resetActiveTab);
  if (els.resetAllBtn) els.resetAllBtn.addEventListener("click", resetAll);
}

// ============================================================ Graphics ====
// Goal 3 (overnight build): a Graphics settings tab built generically from
// R's stage-render.js RENDER_SETTINGS_SCHEMA/QUALITY_PRESETS, a perf HUD
// fed by scene.getRenderStats(), an "auto performance" stepper, and a
// render-mode selector. All guarded (dynamic import + typeof checks) so the
// page still works if stage-render.js/scene.setRenderSettings haven't
// landed yet - see .claude/memory/stage-visualizer.md "Overnight build
// contracts" / "Render API".

const GRAPHICS_STORAGE_KEY = "qlcplus-stage-graphics-v4";
const PRESET_ORDER = ["low", "medium", "high", "ultra"];

function loadGraphicsSettingsFromStorage() {
  try {
    const raw = localStorage.getItem(GRAPHICS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}
function saveGraphicsSettingsToStorage(settings) {
  try {
    localStorage.setItem(GRAPHICS_STORAGE_KEY, JSON.stringify(settings));
  } catch (e) {
    /* private browsing etc */
  }
}

function getByPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
}
function setByPath(obj, path, value) {
  const parts = path.split(".");
  let o = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    o[parts[i]] = o[parts[i]] || {};
    o = o[parts[i]];
  }
  o[parts[parts.length - 1]] = value;
}
function settingsEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}
function matchingPresetName(settings, presets) {
  for (const name of PRESET_ORDER) {
    // compare everything except the "quality" label itself, since a custom
    // tweak still copies its starting preset's name into that field
    const p = Object.assign({}, presets[name], { quality: settings.quality });
    if (settingsEqual(p, settings)) return name;
  }
  return null;
}

// Schema fields shown on the Settings > Video tab (preset/resolution/fps/
// exposure); everything else in RENDER_SETTINGS_SCHEMA - plus workLight,
// which the operator wants grouped with the other lighting/quality toggles
// on Graphics even though stage-render.js's own schema tags it "Quality" -
// renders on Settings > Graphics instead. This is a UI-only split (which
// tab a control appears on); it never changes the schema or the flat
// render-settings object both tabs read/write together.
const VIDEO_TAB_KEYS = ["quality", "resolutionScale", "dynamicResolution", "targetFps", "exposure"];
const GRAPHICS_GROUP_TITLE_OVERRIDE = { workLight: "Lighting" };

async function wireGraphicsSettingsTab() {
  const videoBody = document.getElementById("video-settings-body");
  const graphicsBody = document.getElementById("graphics-settings-body");
  const presetGroup = document.getElementById("quality-preset-group");
  const perfHudToggle = document.getElementById("perf-hud-toggle");
  const autoPerfToggle = document.getElementById("auto-perf-toggle");
  const renderModeSelect = document.getElementById("render-mode-select");
  if (!videoBody || !graphicsBody || !app.scene || typeof app.scene.setRenderSettings !== "function") return;

  let schema, presets, defaults;
  try {
    const mod = await import("./stage-render.js");
    schema = mod.RENDER_SETTINGS_SCHEMA;
    presets = mod.QUALITY_PRESETS;
    defaults = mod.DEFAULT_RENDER_SETTINGS;
  } catch (e) {
    console.warn("stage-render.js not available yet - Graphics tab disabled", e);
    return;
  }
  if (!schema || !presets) return;

  const stored = loadGraphicsSettingsFromStorage();
  let current = stored || JSON.parse(JSON.stringify(defaults));
  app.scene.setRenderSettings(current);

  const controlEls = {}; // key -> {input, valueEl}

  // Builds one container's groups from a filtered field list (preserving
  // schema order), each field re-grouped by its own (possibly overridden)
  // heading - a single schema "group" (e.g. "Quality") can now split across
  // both tabs, so grouping is done per-container, not once for the whole
  // schema.
  function renderFieldGroups(container, fields, titleOverride) {
    container.innerHTML = "";
    const groups = [];
    const groupIndex = new Map();
    fields.forEach((field) => {
      const gname = (titleOverride && titleOverride[field.key]) || field.group;
      if (!groupIndex.has(gname)) {
        groupIndex.set(gname, groups.length);
        groups.push({ name: gname, fields: [] });
      }
      groups[groupIndex.get(gname)].fields.push(field);
    });
    groups.forEach((g) => {
      const gEl = document.createElement("div");
      gEl.className = "graphics-group";
      const title = document.createElement("div");
      title.className = "graphics-group-title";
      title.textContent = g.name;
      gEl.appendChild(title);
      g.fields.forEach((field) => {
        const value = getByPath(current, field.key);
        if (field.type === "bool") {
          const row = document.createElement("div");
          row.className = "setting-row";
          const label = document.createElement("span");
          label.className = "setting-label";
          label.textContent = field.label;
          if (field.desc) label.title = field.desc;
          const control = document.createElement("span");
          control.className = "setting-control";
          const toggle = document.createElement("label");
          toggle.className = "toggle-switch";
          const input = document.createElement("input");
          input.type = "checkbox";
          input.checked = !!value;
          const slider = document.createElement("span");
          slider.className = "toggle-slider";
          toggle.appendChild(input);
          toggle.appendChild(slider);
          control.appendChild(toggle);
          row.appendChild(label);
          row.appendChild(control);
          gEl.appendChild(row);
          controlEls[field.key] = { input };
          input.addEventListener("change", () => applyGraphicsPatch(field.key, input.checked));
        } else if (field.type === "select") {
          const row = document.createElement("div");
          row.className = "setting-row";
          const label = document.createElement("span");
          label.className = "setting-label";
          label.textContent = field.label;
          if (field.desc) label.title = field.desc;
          const control = document.createElement("span");
          control.className = "setting-control";
          const select = document.createElement("select");
          (field.options || []).forEach((opt) => {
            const o = document.createElement("option");
            o.value = String(opt);
            o.textContent = String(opt);
            select.appendChild(o);
          });
          select.value = String(value);
          control.appendChild(select);
          row.appendChild(label);
          row.appendChild(control);
          gEl.appendChild(row);
          controlEls[field.key] = { input: select };
          select.addEventListener("change", () => {
            const raw = select.value;
            const num = Number(raw);
            applyGraphicsPatch(field.key, isNaN(num) || typeof value === "string" ? raw : num);
          });
        } else {
          const row = document.createElement("div");
          row.className = "setting-row";
          const label = document.createElement("span");
          label.className = "setting-label";
          label.textContent = field.label;
          if (field.desc) label.title = field.desc;
          const control = document.createElement("span");
          control.className = "setting-control";
          const input = document.createElement("input");
          input.type = "range";
          input.min = field.min;
          input.max = field.max;
          input.step = field.step || 1;
          input.value = value;
          const valueEl = document.createElement("span");
          valueEl.className = "setting-value";
          valueEl.textContent = Number(value).toFixed(field.step && field.step < 1 ? 2 : 0);
          control.appendChild(input);
          control.appendChild(valueEl);
          row.appendChild(label);
          row.appendChild(control);
          gEl.appendChild(row);
          controlEls[field.key] = { input, valueEl };
          input.addEventListener("input", () => {
            const v = parseFloat(input.value);
            valueEl.textContent = v.toFixed(field.step && field.step < 1 ? 2 : 0);
            applyGraphicsPatch(field.key, v);
          });
        }
      });
      container.appendChild(gEl);
    });
  }

  const videoFields = schema.filter((f) => VIDEO_TAB_KEYS.indexOf(f.key) !== -1);
  const graphicsFields = schema.filter((f) => VIDEO_TAB_KEYS.indexOf(f.key) === -1);
  renderFieldGroups(videoBody, videoFields);
  renderFieldGroups(graphicsBody, graphicsFields, GRAPHICS_GROUP_TITLE_OVERRIDE);

  function refreshControlsFromCurrent() {
    schema.forEach((field) => {
      const c = controlEls[field.key];
      if (!c) return;
      const v = getByPath(current, field.key);
      if (field.type === "bool") c.input.checked = !!v;
      else {
        c.input.value = v;
        if (c.valueEl) c.valueEl.textContent = Number(v).toFixed(field.step && field.step < 1 ? 2 : 0);
      }
    });
  }

  function refreshPresetButtons() {
    const match = matchingPresetName(current, presets);
    document.querySelectorAll(".quality-preset-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.preset === (match || "custom"));
    });
  }

  function applyGraphicsPatch(key, value) {
    setByPath(current, key, value);
    app.scene.setRenderSettings(current);
    current = app.scene.getRenderSettings();
    saveGraphicsSettingsToStorage(current);
    refreshPresetButtons();
  }

  function applyPreset(name) {
    if (!presets[name]) return;
    current = JSON.parse(JSON.stringify(presets[name]));
    app.scene.setRenderSettings(current);
    current = app.scene.getRenderSettings();
    saveGraphicsSettingsToStorage(current);
    refreshControlsFromCurrent();
    refreshPresetButtons();
  }
  app._applyGraphicsPreset = applyPreset;

  if (presetGroup) {
    presetGroup.querySelectorAll(".quality-preset-btn").forEach((btn) => {
      if (btn.dataset.preset === "custom") return; // indicator only, not clickable
      btn.addEventListener("click", () => applyPreset(btn.dataset.preset));
    });
  }
  refreshPresetButtons();

  // Settings popup footer hooks (wireSettingsPopup, above in this file - it
  // runs first in the init sequence, so app._applyViewSettings already
  // exists by the time these are called from a button click).
  function resetKeysToDefaults(keys) {
    keys.forEach((k) => setByPath(current, k, getByPath(defaults, k)));
    app.scene.setRenderSettings(current);
    current = app.scene.getRenderSettings();
    saveGraphicsSettingsToStorage(current);
    refreshControlsFromCurrent();
    refreshPresetButtons();
  }
  app._resetVideoTab = () => resetKeysToDefaults(VIDEO_TAB_KEYS);
  app._resetGraphicsTab = () => {
    resetKeysToDefaults(schema.map((f) => f.key).filter((k) => VIDEO_TAB_KEYS.indexOf(k) === -1));
    if (app._applyViewSettings) app._applyViewSettings({ beamWidthMul: 1, beamBrightnessMul: 1, beamLengthFt: 40 });
  };
  app._resetGraphicsAll = () => {
    const match = matchingPresetName(current, presets) || "medium";
    applyPreset(match);
    if (app._applyViewSettings) app._applyViewSettings({ beamWidthMul: 1, beamBrightnessMul: 1, beamLengthFt: 40 });
  };

  if (renderModeSelect && typeof app.scene.setRenderMode === "function") {
    renderModeSelect.addEventListener("change", () => app.scene.setRenderMode(renderModeSelect.value));
  }

  // ---- Perf HUD ----
  function setPerfHudVisible(visible, persist = true) {
    const hud = document.getElementById("perf-hud");
    if (hud) hud.classList.toggle("hidden", !visible);
    if (perfHudToggle) perfHudToggle.checked = visible;
    try {
      if (persist) localStorage.setItem("qlcplus-stage-perfhud-v3", visible ? "1" : "0");
    } catch (e) {
      /* ignore */
    }
  }
  // Operator spec (2026-09-29): the perf panel defaults OFF. New key (v3, was
  // v2) so an old saved "on" from before this change can't win the default -
  // only an explicit "1" under the new key turns it on.
  let perfHudVisible = false;
  try {
    perfHudVisible = localStorage.getItem("qlcplus-stage-perfhud-v3") === "1";
  } catch (e) {
    /* ignore */
  }
  setPerfHudVisible(perfHudVisible, false);

  // compact FPS readout in the top bar, always visible (the HUD can be covered or turned off)
  const fpsBadge = document.getElementById("fps-badge");
  if (fpsBadge) {
    fpsBadge.addEventListener("click", () => setPerfHudVisible(document.getElementById("perf-hud").classList.contains("hidden")));
    setInterval(() => {
      const st = app.scene && app.scene.getRenderStats ? app.scene.getRenderStats() : null;
      if (!st || !isFinite(st.fps)) return;
      const fps = Math.round(st.fps);
      fpsBadge.textContent = fps + " FPS";
      fpsBadge.classList.toggle("warn", fps < 30 && fps >= 20);
      fpsBadge.classList.toggle("bad", fps < 20);
    }, 500);
  }
  if (perfHudToggle) perfHudToggle.addEventListener("change", () => setPerfHudVisible(perfHudToggle.checked));
  const perfHudBtn = document.getElementById("perf-hud-btn");
  if (perfHudBtn) perfHudBtn.addEventListener("click", () => setPerfHudVisible(document.getElementById("perf-hud").classList.contains("hidden")));

  let perfAccum = 0;
  let lastQualityClass = null;
  app._updatePerfHud = function (dt) {
    perfAccum += dt;
    if (perfAccum < 0.5) return; // ~2Hz refresh - a number readout doesn't need more (perf item 5)
    perfAccum = 0;
    // Item 7: backdrop-filter blur is expensive to composite on an
    // integrated GPU - drop it whenever the current quality is Low,
    // regardless of how it got set (preset button, auto-perf step-down, or
    // a test hook via window.__stage.applyQualityPreset). Checked here
    // (already throttled to ~2Hz) rather than on every settings call site.
    const rs = app.scene.getRenderSettings ? app.scene.getRenderSettings() : null;
    const isLow = !!(rs && rs.quality === "low");
    if (isLow !== lastQualityClass) {
      lastQualityClass = isLow;
      document.body.classList.toggle("quality-low", isLow);
    }
    // Item 8: fetched unconditionally (not just while the HUD panel is
    // open) so the one-time SwiftShare/software-GL warning toast below
    // still fires even if the operator never opens the HUD.
    const s = app.scene.getRenderStats ? app.scene.getRenderStats() : null;
    checkGpuRendererOnce(s && s.gpuRenderer);
    if (document.getElementById("perf-hud").classList.contains("hidden")) return;
    if (!s) return;
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    };
    set("perf-fps", Math.round(s.fps));
    set("perf-js-ms", Number(s.jsMs || 0).toFixed(1) + " ms");
    set("perf-frame-ms", Number(s.frameMs).toFixed(1) + " ms");
    set("perf-draws", s.drawCalls);
    set("perf-tris", s.triangles);
    set("perf-lights", s.lights);
    set("perf-beams", s.beams);
    set("perf-res", Number(s.resolutionScale).toFixed(2));
    const gpuEl = document.getElementById("perf-gpu");
    if (gpuEl && s.gpuRenderer) {
      const short = s.gpuRenderer.length > 26 ? s.gpuRenderer.slice(0, 24) + "…" : s.gpuRenderer;
      gpuEl.textContent = short;
      gpuEl.title = s.gpuRenderer;
    }
  };

  // ---- Auto performance: step quality down if FPS < target for 5s ----
  function setAutoPerf(on) {
    autoPerfOn = on;
    if (autoPerfToggle) autoPerfToggle.checked = on;
    try {
      localStorage.setItem("qlcplus-stage-autoperf-v2", on ? "1" : "0");
    } catch (e) {
      /* ignore */
    }
  }
  let autoPerfOn = true;
  try {
    autoPerfOn = localStorage.getItem("qlcplus-stage-autoperf-v2") !== "0";
  } catch (e) {
    /* ignore */
  }
  setAutoPerf(autoPerfOn);
  if (autoPerfToggle) autoPerfToggle.addEventListener("change", () => setAutoPerf(autoPerfToggle.checked));

  let lowFpsSince = null;
  app._updateAutoPerf = function () {
    if (!autoPerfOn || !app.scene.getRenderStats) return;
    const s = app.scene.getRenderStats();
    const target = current.targetFps || 30;
    if (s.fps > 0 && s.fps < target * 0.85) {
      if (lowFpsSince == null) lowFpsSince = app.t;
      else if (app.t - lowFpsSince > 5) {
        const idx = PRESET_ORDER.indexOf(current.quality);
        if (idx > 0) {
          applyPreset(PRESET_ORDER[idx - 1]);
          showToast("Auto performance: quality lowered to " + PRESET_ORDER[idx - 1]);
        }
        lowFpsSince = app.t;
      }
    } else {
      lowFpsSince = null;
    }
  };
}

// =============================================================== Seats ====
// Goal 4 (overnight build): camera bookmarks derived from stage objects by
// category (contract in .claude/memory/stage-visualizer.md "Seats"):
// table -> 2-4 seats around it, booth -> 2-3, bar -> a stool per position
// along the front, dj -> the DJ spot (standing), plus one standing "Dance
// floor" spot. Eye height 44" seated / 66" standing; each looks at the
// object named/aliased "dance floor centre", else the anchor.

function propFootprintRadiusIn(obj) {
  const propDefs = app.stage.draft.propDefs || {};
  const def = propDefs[obj.prop];
  let r = 22; // inches - a reasonable fallback for an unknown prop
  if (def && Array.isArray(def.parts) && def.parts.length) {
    r = 0;
    def.parts.forEach((p) => {
      const size = p.size || [12, 12, 12];
      const pos = p.pos || [0, 0, 0];
      const ext = Math.hypot(pos[0] || 0, pos[1] || 0) + Math.max(size[0] || 0, size[1] || 0) / 2;
      if (ext > r) r = ext;
    });
  }
  const scale = obj.scale || [1, 1, 1];
  return r * Math.max(scale[0] || 1, scale[1] || 1);
}

function findDanceFloorTarget() {
  const objects = app.stage.draft.objects || [];
  const hit = objects.find((o) => {
    const name = (o.name || "").toLowerCase();
    const aliases = (o.aliases || []).map((a) => a.toLowerCase());
    return name.indexOf("dance floor centre") !== -1 || name.indexOf("dance floor center") !== -1 || aliases.some((a) => a.indexOf("dance floor cent") !== -1);
  });
  if (hit && Array.isArray(hit.pos)) return hit.pos.slice(0, 3);
  const anchor = app.stage.draft.anchor;
  return Array.isArray(anchor) ? anchor.slice(0, 3) : [0, 0, 0];
}

// Rough top-of-prop height in inches (bottom-of-part conventions differ by
// shape - box() centres on pos with pos.z already the half-height, model
// parts sit floor-aligned at their own pos.z) - used only to tell a "standing"
// (high-top, ~30"+) table from a normal seated one (contract: "standing
// tables -> eye 58"). Best-effort; unknown/empty propDefs fall back to 0
// (treated as seated) rather than guessing tall.
function propHeightIn(obj) {
  const propDefs = app.stage.draft.propDefs || {};
  const def = propDefs[obj.prop];
  let h = 0;
  if (def && Array.isArray(def.parts)) {
    def.parts.forEach((p) => {
      const size = p.size || [0, 0, 0];
      const pos = p.pos || [0, 0, 0];
      const top = (pos[2] || 0) + (p.shape === "model" ? size[2] || 0 : (size[2] || 0) / 2);
      if (top > h) h = top;
    });
  }
  const scale = (obj.scale && obj.scale[2]) || 1;
  return h * scale;
}

// A seat position must read as "inside the room": clamp a fair margin off
// every wall so a mis-derived seat can never end up outside the shell or
// pressed into it (goal 1, overnight wave 3).
function clampSeatToRoomXY(pos) {
  const room = app.stage.draft.room || { width: 576, depth: 480 };
  const margin = 14; // inches - enough to clear a 6" wall slab plus a little
  return [clampIn(pos[0], margin, room.width - margin), clampIn(pos[1], margin, room.depth - margin), pos[2] || 0];
}

function computeSeats() {
  const objects = app.stage.draft.objects || [];
  const lookAt = findDanceFloorTarget();
  const seats = [];

  // Real placed stools/chairs (category "seating") are the ground truth for
  // where a person actually sits at a table or the bar - use their own
  // positions instead of re-deriving an idealized ring/spacing, so the seat
  // always matches the visible chair mesh. Each stool is assigned to its
  // nearest table/bar anchor object.
  const anchors = objects.filter((o) => Array.isArray(o.pos) && (o.category === "table" || o.category === "bar"));
  const seatingObjs = objects.filter((o) => Array.isArray(o.pos) && o.category === "seating");
  const claimedAnchors = new Set();

  function nearest(obj, list) {
    let best = null, bestD = Infinity;
    list.forEach((a) => {
      const dx = a.pos[0] - obj.pos[0], dy = a.pos[1] - obj.pos[1];
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = a; }
    });
    return best;
  }

  seatingObjs.forEach((stool, i) => {
    // An explicit stool.parent (anchor NAME, set by e.g. club_scene.py) beats
    // nearest-by-distance - two different pieces of furniture can be only a
    // few inches apart (a tight club floor plan), which makes pure proximity
    // matching ambiguous/wrong (confirmed live: a bar-front stool matched a
    // cocktail table 6in further away instead of the bar 6in the other way).
    const anchor = (stool.parent && anchors.find((a) => a.name === stool.parent)) || nearest(stool, anchors);
    if (!anchor) return;
    claimedAnchors.add(anchor);
    const standing = anchor.category === "table" && propHeightIn(anchor) >= 26;
    seats.push({
      name: (anchor.name || anchor.prop || "Seat") + " (" + (stool.name || "seat " + (i + 1)) + ")",
      pos: clampSeatToRoomXY(stool.pos),
      eyeHeightIn: standing ? 58 : 44,
      lookAt: lookAt,
      category: anchor.category,
    });
  });

  // Fallback for a table/bar anchor with no explicit stool objects nearby
  // (a generic authored scene, not this club layout) - a plain ring, still
  // clamped inside the room.
  function ringSeats(obj, count, radiusIn, category, eyeHeightIn) {
    for (let i = 0; i < count; i++) {
      const ang = (i / count) * Math.PI * 2 + Math.PI / count; // offset so no seat sits dead-on an axis
      const x = obj.pos[0] + Math.cos(ang) * radiusIn;
      const y = obj.pos[1] + Math.sin(ang) * radiusIn;
      seats.push({ name: (obj.name || obj.prop || "Table") + " (seat " + (i + 1) + ")", pos: clampSeatToRoomXY([x, y, obj.pos[2] || 0]), eyeHeightIn: eyeHeightIn, lookAt: lookAt, category: category });
    }
  }
  anchors.forEach((obj) => {
    if (claimedAnchors.has(obj)) return;
    if (obj.category === "table") {
      const standing = propHeightIn(obj) >= 26;
      ringSeats(obj, 4, propFootprintRadiusIn(obj) + 18, "table", standing ? 58 : 44);
    } else if (obj.category === "bar") {
      ringSeats(obj, 3, propFootprintRadiusIn(obj) + 18, "bar", 44);
    }
  });

  // Booths (VIP sofas): the sofa IS the seat, and it's typically placed
  // flush against a wall facing into the room, so a full ring around its
  // centre (the old approach) puts some seats behind the backrest or clean
  // through the wall - confirmed live (a seat landed at room-x -9.75, outside
  // a room whose wall starts at x=3). Instead, seat points sit ON the sofa's
  // own cushion footprint, offset toward the room/lookAt side only (so they
  // are always on the open side of the sofa, regardless of its own rot/yaw
  // metadata) and spread along the sofa's width.
  objects.forEach((obj) => {
    if (!Array.isArray(obj.pos) || obj.category !== "booth") return;
    const toTarget = [lookAt[0] - obj.pos[0], lookAt[1] - obj.pos[1]];
    const len = Math.hypot(toTarget[0], toTarget[1]) || 1;
    const dir = [toTarget[0] / len, toTarget[1] / len]; // unit vector, sofa -> room centre
    const perp = [-dir[1], dir[0]];
    const r = propFootprintRadiusIn(obj);
    const inset = Math.min(16, r * 0.55); // stay ON the cushion, not beyond the couch
    const spread = Math.max(18, r * 0.7);
    const count = 3;
    for (let i = 0; i < count; i++) {
      const t = (i - (count - 1) / 2) * spread;
      const x = obj.pos[0] + dir[0] * inset + perp[0] * t;
      const y = obj.pos[1] + dir[1] * inset + perp[1] * t;
      seats.push({ name: (obj.name || "Booth") + " (seat " + (i + 1) + ")", pos: clampSeatToRoomXY([x, y, obj.pos[2] || 0]), eyeHeightIn: 44, lookAt: lookAt, category: "booth" });
    }
  });

  // DJ: stands BEHIND the console (the side away from the crowd/dance
  // floor), not with the camera embedded at the console's own centre point.
  let djAnchor = null;
  objects.forEach((obj) => {
    if (!Array.isArray(obj.pos) || obj.category !== "dj") return;
    djAnchor = obj;
    const toTarget = [lookAt[0] - obj.pos[0], lookAt[1] - obj.pos[1]];
    const len = Math.hypot(toTarget[0], toTarget[1]) || 1;
    const away = [-toTarget[0] / len, -toTarget[1] / len]; // unit vector, crowd -> away
    const stepBack = propFootprintRadiusIn(obj) * 0.5 + 8;
    const pos = [obj.pos[0] + away[0] * stepBack, obj.pos[1] + away[1] * stepBack, obj.pos[2] || 0];
    seats.push({ name: obj.name || "DJ", pos: clampSeatToRoomXY(pos), eyeHeightIn: 66, lookAt: lookAt, category: "dj" });
  });

  // Dance floor centre: a standing seat AT the look-at point itself would
  // make camera and target coincide in X/Y (confirmed live: seat pos and
  // lookAt were both [288,222,0]), collapsing the view to a straight-down
  // stare. Look toward the stage/DJ instead so it reads as a real vantage
  // point in the middle of the crowd.
  const floorObj = objects.find((o) => o.category === "floor_area" || (o.name || "").toLowerCase().indexOf("dance floor") !== -1);
  if (floorObj && Array.isArray(floorObj.pos)) {
    const stageObj = djAnchor || objects.find((o) => o.category === "stage" && Array.isArray(o.pos));
    let floorLookAt = lookAt;
    if (stageObj && Math.hypot(stageObj.pos[0] - floorObj.pos[0], stageObj.pos[1] - floorObj.pos[1]) > 1) {
      floorLookAt = stageObj.pos.slice(0, 3);
    } else {
      floorLookAt = [floorObj.pos[0] + 60, floorObj.pos[1], floorObj.pos[2] || 0];
    }
    seats.push({ name: "Dance floor centre", pos: clampSeatToRoomXY(floorObj.pos), eyeHeightIn: 66, lookAt: floorLookAt, category: "floor" });
  }
  return seats;
}

function seatCameraState(seat) {
  const eyePosThree = userToThree(seat.pos[0], seat.pos[1], (seat.pos[2] || 0) + seat.eyeHeightIn);
  const targetThree = userToThree(seat.lookAt[0], seat.lookAt[1], seat.lookAt[2] || 0);
  return { position: [eyePosThree.x, eyePosThree.y, eyePosThree.z], target: [targetThree.x, targetThree.y, targetThree.z] };
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}
function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function wireSeats() {
  const select = document.getElementById("seat-select");
  const tourBtn = document.getElementById("seat-tour-btn");
  const tourIntervalSlider = document.getElementById("tour-interval-slider");
  const tourIntervalValue = document.getElementById("tour-interval-value");
  const labelsToggle = document.getElementById("labels-toggle");
  let seats = [];
  let currentIndex = -1;
  let tourTimer = null;
  let tourIntervalS = 8;
  // Operator spec (overnight wave 2): labels default OFF while at a seat -
  // they clutter a seated/standing view - restored to whatever the
  // operator's own preference was when going back to a free camera.
  let labelsPrefBeforeSeat = null;
  function setLabelsForSeatMode(atSeat) {
    if (!app.scene || typeof app.scene.setLabelsVisible !== "function") return;
    if (atSeat) {
      if (labelsPrefBeforeSeat === null) labelsPrefBeforeSeat = labelsToggle ? labelsToggle.checked : true;
      app.scene.setLabelsVisible(false);
      if (labelsToggle) labelsToggle.checked = false;
    } else if (labelsPrefBeforeSeat !== null) {
      const restore = labelsPrefBeforeSeat;
      labelsPrefBeforeSeat = null;
      app.scene.setLabelsVisible(restore);
      if (labelsToggle) labelsToggle.checked = restore;
    }
    if (app._syncLabelsToolbarBtn) app._syncLabelsToolbarBtn();
  }
  try {
    const v = parseFloat(localStorage.getItem("qlcplus-stage-tour-interval"));
    if (!isNaN(v) && v > 0) tourIntervalS = v;
  } catch (e) {
    /* ignore */
  }
  if (tourIntervalSlider) {
    tourIntervalSlider.value = tourIntervalS;
    if (tourIntervalValue) tourIntervalValue.textContent = String(tourIntervalS);
  }

  function refreshList() {
    seats = computeSeats();
    if (!select) return;
    const prevValue = select.value;
    select.innerHTML = "";
    const freeOpt = document.createElement("option");
    freeOpt.value = "";
    freeOpt.textContent = "Free camera";
    select.appendChild(freeOpt);
    seats.forEach((s, i) => {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = s.name;
      select.appendChild(o);
    });
    if (prevValue && Number(prevValue) < seats.length) select.value = prevValue;
  }
  refreshList();
  app._refreshSeatList = refreshList;

  let animHandle = null;
  function animateTo(targetState) {
    if (!app.scene) return;
    if (animHandle) cancelAnimationFrame(animHandle);
    const before = app.scene.getCameraState();
    const start = { position: before.position.slice(), target: before.target.slice() };
    const dur = 800; // ms - "~0.8s" per spec
    const t0 = performance.now();
    function step(now) {
      const t = Math.min(1, (now - t0) / dur);
      const k = easeInOutCubic(t);
      app.scene.setCameraState({
        position: [lerp(start.position[0], targetState.position[0], k), lerp(start.position[1], targetState.position[1], k), lerp(start.position[2], targetState.position[2], k)],
        target: [lerp(start.target[0], targetState.target[0], k), lerp(start.target[1], targetState.target[1], k), lerp(start.target[2], targetState.target[2], k)],
      });
      if (t < 1) {
        animHandle = requestAnimationFrame(step);
      } else {
        animHandle = null;
        // one history entry per seat change, like any other camera move
        pushCameraHistory(before, app.scene.getCameraState());
      }
    }
    animHandle = requestAnimationFrame(step);
    // slow frames (big scenes, weak GPUs) must not leave the camera half-way: finish on time
    setTimeout(() => {
      if (!animHandle) return;
      cancelAnimationFrame(animHandle);
      animHandle = null;
      app.scene.setCameraState(targetState);
      pushCameraHistory(before, app.scene.getCameraState());
    }, dur + 150);
  }

  function goto(i) {
    if (i < 0 || i >= seats.length) return false;
    currentIndex = i;
    if (select) select.value = String(i);
    setLabelsForSeatMode(true);
    animateTo(seatCameraState(seats[i]));
    return true;
  }
  app.seats = { list: () => seats.slice(), goto };

  function stopTour() {
    if (tourTimer) {
      clearInterval(tourTimer);
      tourTimer = null;
    }
    if (tourBtn) tourBtn.classList.remove("active");
  }
  function startTour() {
    if (!seats.length) return;
    if (tourTimer) clearInterval(tourTimer);
    if (currentIndex < 0) goto(0);
    tourTimer = setInterval(() => {
      currentIndex = (currentIndex + 1) % seats.length;
      goto(currentIndex);
    }, tourIntervalS * 1000);
    if (tourBtn) tourBtn.classList.add("active");
  }
  function goFree() {
    stopTour();
    currentIndex = -1;
    if (select) select.value = "";
    setLabelsForSeatMode(false);
  }
  app._seatsGoFree = goFree;
  app._seatsRefresh = refreshList;

  if (select) {
    select.addEventListener("change", () => {
      if (select.value === "") {
        goFree();
      } else {
        stopTour();
        goto(Number(select.value));
      }
    });
  }
  if (tourBtn) {
    tourBtn.addEventListener("click", () => {
      if (tourTimer) stopTour();
      else startTour();
    });
  }
  if (tourIntervalSlider) {
    tourIntervalSlider.addEventListener("input", () => {
      tourIntervalS = parseFloat(tourIntervalSlider.value) || 8;
      if (tourIntervalValue) tourIntervalValue.textContent = String(tourIntervalS);
      try {
        localStorage.setItem("qlcplus-stage-tour-interval", String(tourIntervalS));
      } catch (e) {
        /* ignore */
      }
      if (tourTimer) startTour(); // restart with the new interval
    });
  }

  window.addEventListener("keydown", (ev) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (document.activeElement && document.activeElement.isContentEditable)) return;
    if (ev.key === "[" || ev.key === "]") {
      if (!seats.length) return;
      stopTour();
      const dir = ev.key === "]" ? 1 : -1;
      const next = currentIndex < 0 ? 0 : (currentIndex + dir + seats.length) % seats.length;
      goto(next);
    } else if (ev.key === "f" || ev.key === "F") {
      if (!(ev.ctrlKey || ev.metaKey || ev.altKey)) goFree();
    }
  });
}

// ============================================================ Crosshair ===
// Goal 5: shown only while the middle-drag mouselook gesture (stage-camera.js)
// is active - a plain OrbitControls pan/orbit isn't "looking around" in the
// same sense, so the crosshair would just be visual noise then.
function wireCrosshair() {
  const el = document.getElementById("crosshair");
  if (!el) return;
  app._updateCrosshair = function () {
    const looking = !!(app.cameraControls && app.cameraControls.isMouselooking && app.cameraControls.isMouselooking());
    el.classList.toggle("hidden", !looking);
  };
}

// ============================================================= Minimap ====
// Goal 5: a top-down 2D canvas of the room - fixtures, objects and the
// camera position with its view cone. Click to teleport (keeps the current
// height/look direction, just moves the horizontal position - a minimap
// teleport is about "where", not "how you're looking").
function wireMinimap() {
  const wrap = document.getElementById("minimap-wrap");
  const canvas = document.getElementById("minimap-canvas");
  const toggleBtn = document.getElementById("minimap-toggle-btn");
  if (!wrap || !canvas) return;
  const ctx = canvas.getContext("2d");

  function setVisible(v) {
    wrap.classList.toggle("hidden", !v);
    try {
      localStorage.setItem("qlcplus-stage-minimap", v ? "1" : "0");
    } catch (e) {
      /* ignore */
    }
  }
  let visible = true;
  try {
    visible = localStorage.getItem("qlcplus-stage-minimap") !== "0";
  } catch (e) {
    /* ignore */
  }
  setVisible(visible);
  if (toggleBtn) toggleBtn.addEventListener("click", () => setVisible(wrap.classList.contains("hidden")));

  function roomToCanvas(x, y, room, w, h) {
    const margin = 8;
    const sx = (w - margin * 2) / Math.max(1, room.width);
    const sy = (h - margin * 2) / Math.max(1, room.depth);
    const s = Math.min(sx, sy);
    return [margin + x * s, margin + y * s, s];
  }

  app._updateMinimap = function () {
    if (wrap.classList.contains("hidden") || !app.scene) return;
    const room = app.stage.draft.room || { width: 480, depth: 360 };
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#05060a";
    ctx.fillRect(0, 0, w, h);
    const [ox, oy, s] = roomToCanvas(0, 0, room, w, h);
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.lineWidth = 1;
    ctx.strokeRect(ox, oy, room.width * s, room.depth * s);

    // fixtures
    if (app.rigIndex) {
      ctx.fillStyle = "#00d0ff";
      app.rigIndex.list.forEach((m) => {
        const entry = app.stage.draft.fixtures[m.id];
        if (!entry || !Array.isArray(entry.pos)) return;
        const [px, py] = roomToCanvas(entry.pos[0], entry.pos[1], room, w, h);
        ctx.beginPath();
        ctx.arc(px, py, 2, 0, Math.PI * 2);
        ctx.fill();
      });
    }
    // objects
    ctx.fillStyle = "#ffb020";
    (app.stage.draft.objects || []).forEach((o) => {
      if (!Array.isArray(o.pos)) return;
      const [px, py] = roomToCanvas(o.pos[0], o.pos[1], room, w, h);
      ctx.fillRect(px - 2, py - 2, 4, 4);
    });

    // camera position + view cone
    const cs = app.scene.getCameraState();
    if (cs) {
      const [cx, cy] = threeToUser(cs.position);
      const [tx, ty] = threeToUser(cs.target);
      const [pcx, pcy] = roomToCanvas(cx, cy, room, w, h);
      const [ptx, pty] = roomToCanvas(tx, ty, room, w, h);
      const ang = Math.atan2(pty - pcy, ptx - pcx);
      const coneLen = 16, coneSpread = 0.5;
      ctx.fillStyle = "rgba(0, 230, 118, 0.25)";
      ctx.beginPath();
      ctx.moveTo(pcx, pcy);
      ctx.lineTo(pcx + Math.cos(ang - coneSpread) * coneLen, pcy + Math.sin(ang - coneSpread) * coneLen);
      ctx.lineTo(pcx + Math.cos(ang + coneSpread) * coneLen, pcy + Math.sin(ang + coneSpread) * coneLen);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = "#00e676";
      ctx.beginPath();
      ctx.arc(pcx, pcy, 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
  };

  canvas.addEventListener("click", (ev) => {
    if (!app.scene) return;
    const rect = canvas.getBoundingClientRect();
    const cx = ev.clientX - rect.left, cy = ev.clientY - rect.top;
    const room = app.stage.draft.room || { width: 480, depth: 360 };
    const margin = 8;
    const s = Math.min((canvas.width - margin * 2) / Math.max(1, room.width), (canvas.height - margin * 2) / Math.max(1, room.depth));
    const userX = (cx - margin) / s;
    const userY = (cy - margin) / s;
    const cs = app.scene.getCameraState();
    const curThreeY = cs.position[1];
    const eye = userToThree(userX, userY, 66); // stand at a reasonable eye height
    const before = cs;
    app.scene.setCameraState({ position: [eye.x, curThreeY, eye.z], target: cs.target });
    pushCameraHistory(before, app.scene.getCameraState());
    if (app._seatsGoFree) app._seatsGoFree();
  });
}

// ======================================================== Game UI polish ==
// Goal 5: keybinding help overlay (? or H), photo mode (Tab hides all UI),
// screenshot (canvas -> PNG), fullscreen.
const KEYBINDS = [
  ["Left-click", "Select"], ["Left-drag (selection)", "Move"], ["Shift+drag", "Rotate"], ["Ctrl+drag", "Scale"],
  ["1 / 2 / 3", "Move / Rotate / Scale gizmo"], ["Delete", "Delete selection"], ["Ctrl+D", "Duplicate"],
  ["Ctrl+Z / Ctrl+Y", "Undo / Redo"], ["WASD", "Fly move"], ["Q / E", "Fly down / up"], ["Shift (flying)", "Fast"],
  ["Middle-drag", "Mouselook / orbit"], ["Right-click", "Context menu"], ["[ / ]", "Previous / next seat"],
  ["F", "Free camera"], ["Tab", "Photo mode (hide UI)"], ["? or H", "This help"],
];

function wireGameUiPolish() {
  // ---- keybinding help overlay ----
  const helpOverlay = document.getElementById("keybind-help");
  const helpBody = document.getElementById("keybind-help-body");
  const helpBtn = document.getElementById("help-btn");
  const helpClose = document.getElementById("keybind-help-close");
  if (helpBody) {
    helpBody.innerHTML = "";
    KEYBINDS.forEach(([key, desc]) => {
      const row = document.createElement("div");
      row.className = "kb-row";
      const kbd = document.createElement("kbd");
      kbd.textContent = key;
      const span = document.createElement("span");
      span.textContent = desc;
      row.appendChild(kbd);
      row.appendChild(span);
      helpBody.appendChild(row);
    });
  }
  function showHelp() {
    if (helpOverlay) helpOverlay.classList.remove("hidden");
  }
  function hideHelp() {
    if (helpOverlay) helpOverlay.classList.add("hidden");
  }
  if (helpBtn) helpBtn.addEventListener("click", showHelp);
  if (helpClose) helpClose.addEventListener("click", hideHelp);
  if (helpOverlay) helpOverlay.addEventListener("click", (ev) => { if (ev.target === helpOverlay) hideHelp(); });

  // ---- photo mode / Hide UI (Tab, topbar camera button, bottom toolbar) ----
  // Bottom-toolbar "Hide UI" button (item 2) reuses this exact toggle rather
  // than duplicating it - it's the same body.photo-mode class the Tab
  // hotkey and topbar button already drove.
  const photoBtn = document.getElementById("photo-mode-btn");
  const hideUiBtn = document.getElementById("bottom-hide-ui-btn");
  const hideUiBtnLabel = document.getElementById("bottom-hide-ui-label");
  function setPhotoMode(on) {
    document.body.classList.toggle("photo-mode", on);
    if (photoBtn) photoBtn.classList.toggle("active", on);
    if (hideUiBtn) {
      hideUiBtn.classList.toggle("active", on);
      hideUiBtn.title = on ? "Show all UI (Tab)" : "Hide all UI (Tab)";
    }
    if (hideUiBtnLabel) hideUiBtnLabel.textContent = on ? "Show UI" : "Hide UI";
    // The freed space is CSS-only (panels go display:none); the renderer's
    // drawing buffer/camera aspect only updates via an explicit resize()
    // call (stage-scene.js's resize() reads #viewport's clientWidth/Height -
    // there's no ResizeObserver), so kick it here in both directions.
    if (app.scene && app.scene.resize) app.scene.resize();
  }
  if (photoBtn) photoBtn.addEventListener("click", () => setPhotoMode(!document.body.classList.contains("photo-mode")));
  if (hideUiBtn) hideUiBtn.addEventListener("click", () => setPhotoMode(!document.body.classList.contains("photo-mode")));

  // ---- bottom toolbar: Labels button (item 3), synced with the Settings-
  // popup "Labels" checkbox and the seat-mode auto-hide logic (wireSeats()) ----
  const bottomHouseBtn = document.getElementById("bottom-house-btn");
  if (bottomHouseBtn) {
    bottomHouseBtn.addEventListener("click", () => {
      if (!app.scene || !app.scene.setHouseLights) return;
      app.scene.setHouseLights(!app.scene.getHouseLights());
      syncHouseButton();
    });
    syncHouseButton();
  }
  const bottomLabelsBtn = document.getElementById("bottom-labels-btn");
  function syncLabelsToolbarBtn() {
    if (!bottomLabelsBtn) return;
    const cb = document.getElementById("labels-toggle");
    bottomLabelsBtn.classList.toggle("active", cb ? cb.checked : true);
  }
  app._syncLabelsToolbarBtn = syncLabelsToolbarBtn;
  if (bottomLabelsBtn) {
    bottomLabelsBtn.addEventListener("click", () => {
      const cb = document.getElementById("labels-toggle");
      const next = !(cb ? cb.checked : true);
      if (app.scene && app.scene.setLabelsVisible) app.scene.setLabelsVisible(next);
      if (cb) cb.checked = next;
      syncLabelsToolbarBtn();
    });
  }
  syncLabelsToolbarBtn();

  window.addEventListener("keydown", (ev) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (document.activeElement && document.activeElement.isContentEditable);
    if (ev.key === "Tab" && !typing) {
      ev.preventDefault();
      setPhotoMode(!document.body.classList.contains("photo-mode"));
      return;
    }
    if ((ev.key === "?" || ev.key === "h" || ev.key === "H") && !typing && !(ev.ctrlKey || ev.metaKey || ev.altKey)) {
      if (helpOverlay && !helpOverlay.classList.contains("hidden")) hideHelp();
      else showHelp();
      return;
    }
    if (ev.key === "Escape" && helpOverlay && !helpOverlay.classList.contains("hidden")) hideHelp();
  });

  // ---- screenshot ----
  const screenshotBtn = document.getElementById("screenshot-btn");
  if (screenshotBtn) {
    screenshotBtn.addEventListener("click", () => {
      if (!app.scene || !app.scene.renderer) return;
      try {
        const url = app.scene.renderer.domElement.toDataURL("image/png");
        const a = document.createElement("a");
        a.href = url;
        a.download = "stage-" + new Date().toISOString().replace(/[:.]/g, "-") + ".png";
        document.body.appendChild(a);
        a.click();
        a.remove();
      } catch (e) {
        showToast("Screenshot failed: " + e.message);
      }
    });
  }

  // ---- fullscreen ----
  const fullscreenBtn = document.getElementById("fullscreen-btn");
  if (fullscreenBtn) {
    fullscreenBtn.addEventListener("click", () => {
      try {
        if (document.fullscreenElement) document.exitFullscreen();
        else document.documentElement.requestFullscreen();
      } catch (e) {
        /* fullscreen can be denied (headless, iframe, etc) - not fatal */
      }
    });
  }
}

// -------------------------------------------------------------- saving --

async function savePropsLibrary() {
  const payload = app.props.draft;
  if (app.demo) {
    try {
      localStorage.setItem("qlcplus-stage-props-demo", JSON.stringify(payload));
    } catch (e) {
      /* private browsing etc: ignore, matches the M2 spec's demo-mode note */
    }
  } else {
    await app.ws.saveProps(payload);
  }
  app.props.saved = deepClone(payload);
  app.props.dirty = false;
}
app.savePropsLibrary = savePropsLibrary;

function placePropInstance(propId, posOverride) {
  const def = app.props.draft.props[propId];
  if (!def) return null;
  const id = "o" + Date.now().toString(36) + Math.floor(Math.random() * 1000);
  const pos = posOverride || [app.stage.draft.anchor[0], app.stage.draft.anchor[1], 0];
  commitDraftChange(() => {
    app.stage.draft.objects = app.stage.draft.objects || [];
    app.stage.draft.objects.push({
      id: id,
      prop: propId,
      name: def.name,
      category: def.category,
      aliases: (def.aliases || []).slice(),
      pos: [pos[0], pos[1], pos[2] || 0],
      rz: 0,
      scale: [1, 1, 1],
      color: null,
    });
  });
  return id;
}
app.placePropInstance = placePropInstance;

async function saveStage() {
  if (!app.demo && !app.stage.showPath) {
    throw new Error("Save the show in QLC+ first — the stage file is stored next to it");
  }
  // refresh the propDefs snapshot for every prop currently used, so the
  // stage file renders correctly even if the shared library later changes
  const usedPropIds = new Set((app.stage.draft.objects || []).map((o) => o.prop));
  const propDefs = {};
  usedPropIds.forEach((pid) => {
    const def = (app.props.draft.props && app.props.draft.props[pid]) || app.stage.draft.propDefs[pid];
    if (def) propDefs[pid] = deepClone(def);
  });
  app.stage.draft.propDefs = propDefs;

  const payload = deepClone(app.stage.draft);
  if (app.demo) {
    try {
      localStorage.setItem("qlcplus-stage-demo", JSON.stringify(payload));
    } catch (e) {
      /* ignore */
    }
  } else {
    await app.ws.saveStage(payload);
  }
  app.stage.saved = deepClone(app.stage.draft);
  app.stage.fileExists = true;
  markStageDirty();
  rebuildSceneFromDraft();
  // Save does not clear history (per spec).
}

function revertStage() {
  app.stage.draft = deepClone(app.stage.saved || defaultStageDraft());
  markStageDirty();
  app.selection.clear();
  rebuildSceneFromDraft();
}

window.addEventListener("beforeunload", (ev) => {
  if (app.stage.dirty || app.props.dirty) {
    ev.preventDefault();
    ev.returnValue = "";
  }
});

// -------------------------------------------------------------- status --

function setConnStatus(state) {
  const dot = document.getElementById("conn-dot");
  const text = document.getElementById("conn-text");
  if (!dot || !text) return;
  dot.className = "";
  if (state === "ok") {
    dot.classList.add("ok");
    text.textContent = "connected";
  } else if (state === "demo") {
    dot.classList.add("demo");
    text.textContent = "demo mode";
  } else {
    text.textContent = "disconnected - retrying...";
  }
}

function updateShowNameLabel() {
  const el = document.getElementById("show-name");
  if (!el) return;
  if (app.demo) {
    el.textContent = "Demo";
    return;
  }
  el.textContent = app.stage.showPath ? showBaseName(app.stage.showPath) : "Unsaved show";
}

function showReloadBanner(message, action) {
  const banner = document.getElementById("reload-banner");
  document.getElementById("reload-banner-text").textContent = message;
  app._reloadAction = action;
  banner.classList.remove("hidden");
}
function hideReloadBanner() {
  document.getElementById("reload-banner").classList.add("hidden");
  app._reloadAction = null;
}

// -------------------------------------------------------- demo fake rig --

function demoFixtureBase(id, name, manufacturer, model, mode, type, address, channels, physical, heads, ch) {
  return {
    id: id,
    name: name,
    manufacturer: manufacturer,
    model: model,
    mode: mode,
    type: type,
    universe: 0,
    address: address,
    channels: channels,
    physical: physical,
    heads: heads,
    ch: ch,
    monitor: null,
  };
}

function buildDemoRig() {
  const fixtures = [];
  let addr = 0;
  let id = 1;

  for (let i = 0; i < 8; i++) {
    const a = addr;
    addr += 10;
    fixtures.push(
      demoFixtureBase(
        id++,
        "Moving Head " + (i + 1),
        "Demo",
        "MovingHead8",
        "8 Channel",
        "Moving Head",
        a,
        8,
        { panMax: 540, tiltMax: 270, lensMin: 8, lensMax: 24, layout: [1, 1], focusType: "Head" },
        [[0, 1, 2, 3, 4, 5, 6, 7]],
        [
          { name: "Pan", group: "Pan", byte: 0, colour: 0 },
          { name: "Pan fine", group: "Pan", byte: 1, colour: 0 },
          { name: "Tilt", group: "Tilt", byte: 0, colour: 0 },
          { name: "Tilt fine", group: "Tilt", byte: 1, colour: 0 },
          { name: "Dimmer", group: "Intensity", byte: 0, colour: 0 },
          { name: "Red", group: "Intensity", byte: 0, colour: 0xff0000 },
          { name: "Green", group: "Intensity", byte: 0, colour: 0x00ff00 },
          { name: "Blue", group: "Intensity", byte: 0, colour: 0x0000ff },
        ]
      )
    );
  }

  for (let i = 0; i < 4; i++) {
    const a = addr;
    addr += 4;
    fixtures.push(
      demoFixtureBase(id++, "Par " + (i + 1), "Demo", "ParRGB", "4 Channel", "LED Par", a, 4, {}, [[0, 1, 2, 3]], [
        { name: "Dimmer", group: "Intensity", byte: 0, colour: 0 },
        { name: "Red", group: "Intensity", byte: 0, colour: 0xff0000 },
        { name: "Green", group: "Intensity", byte: 0, colour: 0x00ff00 },
        { name: "Blue", group: "Intensity", byte: 0, colour: 0x0000ff },
      ])
    );
  }

  for (let i = 0; i < 2; i++) {
    const a = addr;
    addr += 12;
    const heads = [];
    const ch = [];
    for (let h = 0; h < 4; h++) {
      heads.push([h * 3, h * 3 + 1, h * 3 + 2]);
      ch.push(
        { name: "Red" + h, group: "Intensity", byte: 0, colour: 0xff0000 },
        { name: "Green" + h, group: "Intensity", byte: 0, colour: 0x00ff00 },
        { name: "Blue" + h, group: "Intensity", byte: 0, colour: 0x0000ff }
      );
    }
    fixtures.push(demoFixtureBase(id++, "Pixel Bar " + (i + 1), "Demo", "PixelBar4", "12 Channel", "LED Bar (Pixels)", a, 12, {}, heads, ch));
  }

  {
    const a = addr;
    addr += 1;
    fixtures.push(
      demoFixtureBase(id++, "Hazer 1", "Demo", "HazeCo", "1 Channel", "Hazer", a, 1, {}, [[0]], [
        { name: "Fog", group: "Intensity", byte: 0, colour: 0 },
      ])
    );
  }

  return {
    serial: 1,
    show: "demo",
    universes: [{ id: 0, name: "Universe 1" }],
    monitor: { grid: [5, 3, 5], units: "m" },
    fixtures: fixtures,
  };
}

function hslToRgb(h, s, l) {
  function hue2rgb(p, q, t) {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [
    Math.round(hue2rgb(p, q, h + 1 / 3) * 255),
    Math.round(hue2rgb(p, q, h) * 255),
    Math.round(hue2rgb(p, q, h - 1 / 3) * 255),
  ];
}

function updateDemoDmx(t) {
  const bytes = app.dmxByUniverse[0];
  if (!bytes || !app.rigIndex) return;
  app.rigIndex.list.forEach((model) => {
    const a = model.address;
    if (model.kind === "movingHead") {
      const panT = Math.sin(t * 0.3 + model.id) * 0.5 + 0.5;
      const tiltT = Math.sin(t * 0.5 + model.id * 1.7) * 0.22 + 0.5; // within ±60° of straight down
      const pan16 = Math.round(panT * 65535);
      const tilt16 = Math.round(tiltT * 65535);
      bytes[a] = (pan16 >> 8) & 0xff;
      bytes[a + 1] = pan16 & 0xff;
      bytes[a + 2] = (tilt16 >> 8) & 0xff;
      bytes[a + 3] = tilt16 & 0xff;
      bytes[a + 4] = 255;
      const [r, g, b] = hslToRgb(((t * 30 + model.id * 40) % 360) / 360, 1, 0.5);
      bytes[a + 5] = r;
      bytes[a + 6] = g;
      bytes[a + 7] = b;
    } else if (model.kind === "par") {
      bytes[a] = 255;
      const [r, g, b] = hslToRgb(((t * 20 + model.id * 60) % 360) / 360, 1, 0.5);
      bytes[a + 1] = r;
      bytes[a + 2] = g;
      bytes[a + 3] = b;
    } else if (model.kind === "bar") {
      for (let i = 0; i < 4; i++) {
        const [r, g, b] = hslToRgb(((t * 40 + model.id * 30 + i * 90) % 360) / 360, 1, 0.5);
        bytes[a + i * 3] = r;
        bytes[a + i * 3 + 1] = g;
        bytes[a + i * 3 + 2] = b;
      }
    }
  });
}

// ------------------------------------------------------------- loading --

async function loadAllFromServer() {
  try {
    app.rigRaw = await app.ws.getStageRig();
  } catch (e) {
    app.rigRaw = { fixtures: [] };
  }
  app.stage.showPath = app.rigRaw.show || null;

  try {
    const r = await app.ws.getStage();
    app.stage.saved = r && r.exists ? r.stage : defaultStageDraft();
    app.stage.fileExists = !!(r && r.exists);
  } catch (e) {
    // getStage ERRs when the show has never been saved to a file - work on
    // an in-memory draft; Save will explain what to do (see saveStage()).
    app.stage.saved = defaultStageDraft();
    app.stage.fileExists = false;
  }
  app.stage.draft = deepClone(app.stage.saved);
  app.rigIndex = buildRigIndex(app.rigRaw, app.stage.draft.models);

  if (!app.stage.fileExists) {
    defaultLayoutAll(app.stage.draft, app.rigIndex);
    markStageDirty();
  }

  try {
    const r = await app.ws.getProps();
    app.props.saved = mergeWithStarters(r && r.exists ? r.props : null);
  } catch (e) {
    app.props.saved = defaultPropsLibrary();
  }
  app.props.draft = deepClone(app.props.saved);

  updateShowNameLabel();
  rebuildSceneFromDraft();
  if (app.props.onChanged) app.props.onChanged();
}

async function reloadStageFromServer() {
  try {
    const r = await app.ws.getStage();
    app.stage.saved = r && r.exists ? r.stage : defaultStageDraft();
    app.stage.fileExists = !!(r && r.exists);
    app.stage.draft = deepClone(app.stage.saved);
    markStageDirty();
    app.selection.clear();
    rebuildSceneFromDraft();
  } catch (e) {
    /* keep the current draft if the reload itself fails */
  }
}

async function reloadPropsFromServer() {
  try {
    const r = await app.ws.getProps();
    app.props.saved = mergeWithStarters(r && r.exists ? r.props : null);
    app.props.draft = deepClone(app.props.saved);
    app.props.dirty = false;
    rebuildSceneFromDraft();
    if (app.props.onChanged) app.props.onChanged();
  } catch (e) {
    /* ignore */
  }
}

// (item 5) The page always follows whatever show QLC+ currently has loaded.
async function switchToNewShow(rig) {
  app.rigRaw = rig;
  app.stage.showPath = rig.show || null;
  try {
    const r = await app.ws.getStage();
    app.stage.saved = r && r.exists ? r.stage : defaultStageDraft();
    app.stage.fileExists = !!(r && r.exists);
  } catch (e) {
    app.stage.saved = defaultStageDraft();
    app.stage.fileExists = false;
  }
  app.stage.draft = deepClone(app.stage.saved);
  app.rigIndex = buildRigIndex(app.rigRaw, app.stage.draft.models);
  if (!app.stage.fileExists) defaultLayoutAll(app.stage.draft, app.rigIndex);

  try {
    const r = await app.ws.getProps();
    app.props.saved = mergeWithStarters(r && r.exists ? r.props : null);
  } catch (e) {
    app.props.saved = defaultPropsLibrary();
  }
  app.props.draft = deepClone(app.props.saved);

  app.selection.clear();
  resetHistory();
  markStageDirty();
  updateShowNameLabel();
  rebuildSceneFromDraft();
  if (app.props.onChanged) app.props.onChanged();
  hideReloadBanner();
}

// --------------------------------------------------------- mode switch --

function syncHouseButton() {
  const b = document.getElementById("bottom-house-btn");
  if (b && app.scene && app.scene.getHouseLights) b.classList.toggle("active", app.scene.getHouseLights());
}
function setMode(mode) {
  app.mode = mode;
  // View mode shows the club as the fixtures light it (house lights off); editing needs to see the room
  if (app.scene && app.scene.setHouseLights) {
    app.scene.setHouseLights(mode !== "view");
    syncHouseButton();
  }
  document.querySelectorAll(".mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  if (app.scene && app.scene.getViewSettings) updateHudLegend(app.scene.getViewSettings());
  document.getElementById("edit-section").style.display = mode === "props" ? "none" : "";
  document.getElementById("props-section").style.display = mode === "props" ? "" : "none";
  if (mode !== "edit") {
    app.scene.transform.detach();
  } else if (app.editor) {
    app.editor.refreshAll();
  }
  if (mode === "view") {
    // View mode is look-only (operator spec): nothing stays selected, no
    // Properties panel, no gizmo - only camera navigation.
    app.selection.clear();
    if (app.editor) app.editor.refreshAll();
  }
}
app.setMode = setMode;

// ---------------------------------------------------- left activity bar --
// (item 8) VS Code-style: a narrow icon bar picks which side panel shows -
// Scene (edit-section) or Props (props-section, and entering/leaving it
// also switches app.mode, replacing the old top-bar "Props" button).
// Clicking the already-active icon collapses the panel so the 3D view
// fills the space; clicking it again restores it. Remembered in
// localStorage. Settings has no panel of its own - it just opens the
// existing gear popup.
const LEFT_PANEL_KEY = "qlcplus-stage-left-panel";
let leftPanelState = { panel: "scene", collapsed: false };
let modeBeforeProps = "view";

function loadLeftPanelState() {
  try {
    const raw = localStorage.getItem(LEFT_PANEL_KEY);
    if (raw) {
      const v = JSON.parse(raw);
      if (v && (v.panel === "scene" || v.panel === "props")) leftPanelState = { panel: v.panel, collapsed: !!v.collapsed };
    }
  } catch (e) {
    /* ignore */
  }
}
function saveLeftPanelState() {
  try {
    localStorage.setItem(LEFT_PANEL_KEY, JSON.stringify(leftPanelState));
  } catch (e) {
    /* private browsing etc: ignore */
  }
}

function applyLeftPanelState() {
  const panelEl = document.getElementById("left-panel");
  const sceneBtn = document.getElementById("activity-scene-btn");
  const propsBtn = document.getElementById("activity-props-btn");
  if (panelEl) panelEl.classList.toggle("hidden", leftPanelState.collapsed);
  if (sceneBtn) sceneBtn.classList.toggle("active", !leftPanelState.collapsed && leftPanelState.panel === "scene");
  if (propsBtn) propsBtn.classList.toggle("active", !leftPanelState.collapsed && leftPanelState.panel === "props");
  if (app.scene) app.scene.resize();
}

// keeps app.mode in step with which side panel is showing: opening Props
// enters props mode; leaving it (Scene, or collapsing the panel) returns
// to whichever of view/edit was active before Props was opened.
function syncModeWithLeftPanel() {
  const showingProps = !leftPanelState.collapsed && leftPanelState.panel === "props";
  if (showingProps && app.mode !== "props") {
    modeBeforeProps = app.mode === "props" ? "view" : app.mode;
    setMode("props");
  } else if (!showingProps && app.mode === "props") {
    setMode(modeBeforeProps || "view");
  }
}

function setLeftPanel(panel) {
  if (leftPanelState.panel === panel && !leftPanelState.collapsed) {
    leftPanelState.collapsed = true;
  } else {
    leftPanelState.panel = panel;
    leftPanelState.collapsed = false;
  }
  saveLeftPanelState();
  syncModeWithLeftPanel();
  applyLeftPanelState();
}

function initActivityBar() {
  loadLeftPanelState();
  const sceneBtn = document.getElementById("activity-scene-btn");
  const propsBtn = document.getElementById("activity-props-btn");
  const settingsBtn = document.getElementById("activity-settings-btn");
  if (sceneBtn) sceneBtn.addEventListener("click", () => setLeftPanel("scene"));
  if (propsBtn) propsBtn.addEventListener("click", () => setLeftPanel("props"));
  if (settingsBtn)
    settingsBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const popup = document.getElementById("settings-popup");
      if (popup) popup.classList.toggle("hidden");
    });
  syncModeWithLeftPanel();
  applyLeftPanelState();
}

// -------------------------------------------------------- self test UI --

function combinedSelftest() {
  const u = unitsSelftest();
  const r = rigSelftest();
  return { pass: u.pass + r.pass, fail: u.fail + r.fail, results: u.results.concat(r.results) };
}

function runSelftestPanel() {
  const result = combinedSelftest();
  const panel = document.getElementById("selftest-panel");
  panel.classList.remove("hidden");
  document.getElementById("selftest-summary").textContent = result.pass + " passed, " + result.fail + " failed";
  const rowsEl = document.getElementById("selftest-results");
  rowsEl.innerHTML = "";
  result.results.forEach((r) => {
    const div = document.createElement("div");
    div.className = "selftest-row " + (r.ok ? "pass" : "fail");
    div.textContent = (r.ok ? "PASS  " : "FAIL  ") + r.name + " - " + r.detail;
    rowsEl.appendChild(div);
  });
}

// ------------------------------------------------------- window.__stage --

function setupWindowStageAPI() {
  window.__stage = {
    status() {
      const placed = Object.keys(app.stage.draft.fixtures || {}).length;
      const total = app.rigIndex ? app.rigIndex.list.length : 0;
      return {
        connected: app.demo ? true : !!(app.ws && app.ws.connected),
        fixtures: total,
        placed: placed,
        unplaced: Math.max(total - placed, 0),
        dirty: !!app.stage.dirty,
        mode: app.mode,
        fps: app.scene ? Math.round(app.scene.getFps()) : 0,
      };
    },
    debug() {
      if (!app.rigIndex) return [];
      return app.rigIndex.list.map((model) => {
        const entry = app.stage.draft.fixtures[model.id];
        const states = computeFixtureState(model, app.dmxByUniverse, 1, entry || null);
        const s0 = states && states[0];
        return {
          id: model.id,
          name: model.name,
          kind: model.kind,
          pan: s0 ? s0.pan : null,
          tilt: s0 ? s0.tilt : null,
          dimmer: s0 ? s0.dimmer : null,
          color: s0 ? s0.color : null,
          shutter: s0 ? s0.shutter : null,
        };
      });
    },
    selftest() {
      return combinedSelftest();
    },
    camera(preset) {
      if (app.scene) app.scene.cameraPreset(preset);
    },
    setLabelsVisible(visible) {
      if (app.scene && app.scene.setLabelsVisible) app.scene.setLabelsVisible(!!visible);
      const t = document.getElementById("labels-toggle");
      if (t) t.checked = !!visible;
      if (app._syncLabelsToolbarBtn) app._syncLabelsToolbarBtn();
    },
    getDraft() {
      return deepClone(app.stage.draft);
    },
    // test hook: an edit through the page's own draft path (marks it dirty, never saves)
    setFixturePos(id, pos) {
      const key = String(id);
      const model = app.rigIndex && app.rigIndex.list.find((m) => String(m.id) === key);
      commitDraftChange(() => {
        const cur = app.stage.draft.fixtures[key] || { model: model ? model.manufacturer + "/" + model.model : "", rot: [0, 0, 0], hang: "hung" };
        // accepts a position [x, y, z] or a whole fixture entry with .pos
        const entry = Array.isArray(pos) ? { pos: pos } : Object.assign({}, pos || {});
        if (Array.isArray(entry.pos)) {
          Object.assign(cur, entry);
          cur.pos = entry.pos.slice(0, 3);
          app.stage.draft.fixtures[key] = cur;
        }
      });
      return !!app.stage.draft.fixtures[key];
    },
    dmxStats() {
      const out = { frames: app.dmxFrames || 0, universes: {} };
      Object.keys(app.dmxByUniverse).forEach((u) => {
        const b = app.dmxByUniverse[u];
        out.universes[u] = { length: b.length, nonZero: b.reduce((n, v) => n + (v ? 1 : 0), 0) };
      });
      return out;
    },
    // ---- undo/redo (item 3/9) ----
    undo() {
      return undo();
    },
    redo() {
      return redo();
    },
    historyInfo() {
      return { undo: history.undo.length, redo: history.redo.length };
    },
    // ---- context-menu/keyboard actions (item 4/9) ----
    duplicateObject(id) {
      return app.editor && app.editor.duplicateObject ? app.editor.duplicateObject(id) : null;
    },
    deleteObject(id) {
      return app.editor && app.editor.deleteObject ? app.editor.deleteObject(id) : false;
    },
    deleteFixture() {
      return false; // fixtures come from the QLC+ show; always refused
    },
    addObject(propId, pos) {
      return placePropInstance(propId, pos);
    },
    setLocked(kind, id, locked) {
      if (!app.editor || !app.editor.setLocked) return false;
      return app.editor.setLocked(kind, kind === "fixture" ? Number(id) : id, !!locked);
    },
    // test hook: a per-model override (beamStart/beamSpread/panMax/tiltMax/...)
    setModelOverride(modelKey, patch) {
      commitDraftChange(() => {
        app.stage.draft.models[modelKey] = Object.assign({}, app.stage.draft.models[modelKey], patch);
      });
      return true;
    },
    // ---- labels (item 8/9) ----
    labelStats() {
      if (!app.scene) return { dom: 0, expected: 0 };
      const dom = app.scene.labelRenderer.domElement.querySelectorAll(".stage-label").length;
      let expected = 0;
      app.scene.scene.traverse((o) => {
        if (o.isCSS2DObject) expected++;
      });
      return { dom: dom, expected: expected };
    },
    // ---- beams (item 9/12) ----
    beamDir(fixtureId) {
      return app.scene ? app.scene.getBeamWorldDir(Number(fixtureId), 0) : null;
    },
    beamInfo(fixtureId) {
      if (!app.scene || !app.rigIndex) return null;
      const key = Number(fixtureId);
      const model = app.rigIndex.byId.get(key);
      if (!model) return null;
      const entry = app.stage.draft.fixtures[key] || null;
      const states = computeFixtureState(model, app.dmxByUniverse, 1, entry);
      const s0 = states && states[0];
      const spreadDeg = (s0 && s0.beamSpreadDeg) || model.beamSpread;
      const geo = app.scene.getBeamGeometryInfo(key, 0, model.beamStart, spreadDeg);
      if (!geo) return null;
      return { startInches: model.beamStart, spreadDeg: spreadDeg, nearRadius: geo.nearRadius, farRadius: geo.farRadius, length: geo.length };
    },
    // ---- mouse/camera (item 1/9/11) ----
    mouseConfig() {
      return app.scene ? app.scene.getMouseConfig() : null;
    },
    getCamera() {
      return app.scene ? app.scene.getCameraState() : null;
    },
    setCamera(state) {
      if (app.scene) app.scene.setCameraState(state);
      return app.scene ? app.scene.getCameraState() : null;
    },
    getViewSettings() {
      return app.scene ? app.scene.getViewSettings() : null;
    },
    setViewSettings(patch) {
      if (app._applyViewSettings) app._applyViewSettings(patch);
      else if (app.scene) app.scene.setViewSettings(patch);
      return app.scene ? app.scene.getViewSettings() : null;
    },
    pickAt(clientX, clientY) {
      if (!app.scene) return null;
      const hit = app.scene.pick(clientX, clientY);
      if (!hit) return null;
      const [kind, idStr] = String(hit.userData.key).split(":");
      return { kind: kind, id: kind === "fixture" ? Number(idStr) : idStr };
    },
    screenPos(fixtureId) {
      return app.scene ? app.scene.getFixtureScreenPos(Number(fixtureId)) : null;
    },
    // test hook: screen position of ANY item (fixture or object), by raw
    // camera/projection matrix math (no THREE import needed here) - used by
    // CDP-driven tests to click/drag the gizmo without guessing pixels.
    screenPosOf(kind, id) {
      if (!app.scene) return null;
      if (kind === "fixture") return app.scene.getFixtureScreenPos(Number(id));
      const g = app.scene.objectEntries && app.scene.objectEntries.get(id);
      if (!g) return null;
      const cam = app.scene.camera;
      cam.updateMatrixWorld();
      const mv = cam.matrixWorldInverse.elements;
      const pm = cam.projectionMatrix.elements;
      const wp = g.getWorldPosition(g.position.clone());
      const mul = (m, v) => [0, 1, 2, 3].map((i) => m[0 * 4 + i] * v[0] + m[1 * 4 + i] * v[1] + m[2 * 4 + i] * v[2] + m[3 * 4 + i] * v[3]);
      let v = mul(mv, [wp.x, wp.y, wp.z, 1]);
      v = mul(pm, v);
      const ndcX = v[0] / v[3];
      const ndcY = v[1] / v[3];
      const rect = app.scene.renderer.domElement.getBoundingClientRect();
      return { x: rect.left + (ndcX * 0.5 + 0.5) * rect.width, y: rect.top + (-ndcY * 0.5 + 0.5) * rect.height };
    },
    // ---- fixture looks (M4) ----
    lookOf(fixtureId) {
      return app.scene ? app.scene.getFixtureLookInfo(Number(fixtureId)) : null;
    },
    setLook(scope, id, lookId) {
      if (scope === "model") {
        commitDraftChange(() => {
          app.stage.draft.models[id] = app.stage.draft.models[id] || {};
          app.stage.draft.models[id].look = lookId || null;
        });
        return true;
      }
      if (scope === "fixture") {
        const key = Number(id);
        const entry = app.stage.draft.fixtures[key];
        if (!entry) return false;
        commitDraftChange(() => {
          entry.look = lookId || null;
        });
        return true;
      }
      return false;
    },
    // ---- current show (item 5/9) ----
    showName() {
      if (app.demo) return "Demo";
      return app.stage.showPath ? showBaseName(app.stage.showPath) : "Unsaved show";
    },
    // test hook: select an item exactly like a click would (see
    // stage-editor.js's app.editor.select) - lets CDP-driven tests drive
    // the gizmo/direct-manipulation paths without pixel-perfect clicks.
    select(kind, id) {
      return app.editor && app.editor.select ? app.editor.select(kind, id) : false;
    },
    // ---- seats (overnight build, goal 7) ----
    seatList() {
      return app.seats && app.seats.list ? app.seats.list() : [];
    },
    goSeat(i) {
      return app.seats && app.seats.goto ? app.seats.goto(i) : false;
    },
    // ---- asset loading / render settings / stats (overnight wave 2) ----
    assetsPending() {
      const propsPending = typeof pendingAssets === "function" ? pendingAssets() : 0;
      const looksPending = app.scene && typeof app.scene.pendingLooks === "function" ? app.scene.pendingLooks() : 0;
      return propsPending + looksPending;
    },
    getRenderStats() {
      return app.scene && app.scene.getRenderStats ? app.scene.getRenderStats() : null;
    },
    // test hook: raw three.js handles for CDP perf experiments (never used by the page itself)
    three() {
      return app.scene ? { renderer: app.scene.renderer, scene: app.scene.scene, camera: app.scene.camera } : null;
    },
    getRenderMode() {
      return app.scene && app.scene.getRenderMode ? app.scene.getRenderMode() : null;
    },
    setRenderMode(mode) {
      if (app.scene && app.scene.setRenderMode) app.scene.setRenderMode(mode);
      return app.scene && app.scene.getRenderMode ? app.scene.getRenderMode() : null;
    },
    getRenderSettings() {
      return app.scene && app.scene.getRenderSettings ? app.scene.getRenderSettings() : null;
    },
    setRenderSettings(patch) {
      if (app.scene && app.scene.setRenderSettings) app.scene.setRenderSettings(patch);
      return app.scene && app.scene.getRenderSettings ? app.scene.getRenderSettings() : null;
    },
    async qualityPresetNames() {
      try {
        const mod = await import("./stage-render.js");
        return Object.keys(mod.QUALITY_PRESETS || {});
      } catch (e) {
        return [];
      }
    },
    async applyQualityPreset(name) {
      try {
        const mod = await import("./stage-render.js");
        const presets = mod.QUALITY_PRESETS || {};
        if (!presets[name] || !app.scene || !app.scene.setRenderSettings) return false;
        app.scene.setRenderSettings(presets[name]);
        return true;
      } catch (e) {
        console.warn("applyQualityPreset failed", name, e);
        return false;
      }
    },
    // test hook: number of beams/emitters a fixture currently has (e.g. the
    // ADJ VPar builtin/adj-vpar look mounts one beam per emitter - see
    // swapInLook in stage-scene.js)
    beamCount(fixtureId) {
      const fe = app.scene && app.scene.fixtureEntries && app.scene.fixtureEntries.get(Number(fixtureId));
      return fe ? fe.beams.length : null;
    },
    // ---- prop instance overrides / definitions (overnight wave 2, item 24) ----
    // Contract (.claude/memory/stage-visualizer.md "Prop instance overrides"):
    // an instance override never mutates the shared definition; editing the
    // definition (this hook, or the real prop editor) updates every
    // instance that resolves to it (they all share propDefLookup(obj)).
    setObjectOverride(objectId, patch) {
      const obj = (app.stage.draft.objects || []).find((o) => o.id === objectId);
      if (!obj) return false;
      commitDraftChange(() => {
        obj.overrides = Object.assign({}, obj.overrides, patch);
      });
      return true;
    },
    getPropDef(propId) {
      return deepClone(propDefLookup({ prop: propId }));
    },
    editPropDef(propId, patch) {
      const liveStore = app.props.draft.props;
      if (liveStore && liveStore[propId]) {
        Object.assign(liveStore[propId], patch);
        app.props.dirty = true;
        rebuildSceneFromDraft();
        return true;
      }
      const stageStore = app.stage.draft.propDefs;
      if (stageStore && stageStore[propId]) {
        commitDraftChange(() => {
          Object.assign(stageStore[propId], patch);
        });
        return true;
      }
      return false;
    },
    // ---- LED strips (overnight wave 2, item 25) ----
    ledStrips() {
      if (!app.scene || !app.scene.objectEntries) return [];
      const out = [];
      app.scene.objectEntries.forEach((group, objId) => {
        group.traverse((n) => {
          if (n.name === "ledStrip") out.push({ objectId: objId, ledCount: (n.userData && n.userData.ledCount) || 0 });
        });
      });
      return out;
    },
    ledStripState(objectId) {
      if (!app.scene || !app.scene.objectEntries) return null;
      const group = app.scene.objectEntries.get(objectId);
      if (!group) return null;
      let state = null;
      group.traverse((n) => {
        if (n.name === "ledStrip") {
          const dots = n.children.find((c) => c.isInstancedMesh);
          state = { color: dots ? "#" + dots.material.color.getHexString() : null, ledCount: (n.userData && n.userData.ledCount) || 0 };
        }
      });
      return state;
    },
    setLedStripColor(objectId, color, dimmer) {
      if (!app.scene || !app.scene.objectEntries) return false;
      const group = app.scene.objectEntries.get(objectId);
      if (!group) return false;
      let updated = 0;
      group.traverse((n) => {
        if (n.name === "ledStrip" && typeof n.update === "function") {
          n.update({ color: color, dimmer: dimmer });
          updated++;
        }
      });
      return updated > 0;
    },
    _debug() {
      return app.editor && app.editor._debug ? app.editor._debug() : null;
    },
  };
}

// ------------------------------------------------------------------ boot --

async function boot() {
  const container = document.getElementById("viewport");
  const scene = createSceneManager(container);
  app.scene = scene;
  scene.setOnCameraHistory(pushCameraHistory);

  let minimapAccum = 0;
  scene.setOnFrame((dt) => {
    if (app.cameraControls) app.cameraControls.update(dt);
    app.t += dt;
    if (app.demo) updateDemoDmx(app.t); // continuously animated -> always dirty while demo is on
    // Perf item 5: recompute fixture DMX->visual state only when a DMX frame
    // actually arrived (app._dmxDirty, set by onDmx) or the demo rig's own
    // time-based animation is running - not unconditionally every rendered
    // frame (computeFixtureState + updateFixtureStates walk every fixture/
    // beam and were previously the single biggest per-frame JS cost).
    if (app.rigIndex && (app.demo || app._dmxDirty)) {
      app._dmxDirty = false;
      const statesById = new Map();
      app.rigIndex.list.forEach((model) => {
        const entry = app.stage.draft.fixtures[model.id];
        if (!entry) return;
        statesById.set(model.id, computeFixtureState(model, app.dmxByUniverse, 1, entry));
      });
      scene.updateFixtureStates(statesById);
    }
    if (app._updatePerfHud) app._updatePerfHud(dt);
    if (app._updateAutoPerf) app._updateAutoPerf();
    if (app._updateCrosshair) app._updateCrosshair();
    minimapAccum += dt;
    if (minimapAccum >= 0.2) { // ~5Hz cap (perf item 5) - a top-down dot map doesn't need more
      minimapAccum = 0;
      if (app._updateMinimap) app._updateMinimap();
    }
  });

  if (app.demo) {
    app.rigRaw = buildDemoRig();
    app.rigIndex = buildRigIndex(app.rigRaw, app.stage.draft.models);
    app.dmxByUniverse = { 0: new Uint8Array(512) };

    let savedStage = null;
    let savedProps = null;
    try {
      const s = localStorage.getItem("qlcplus-stage-demo");
      if (s) savedStage = JSON.parse(s);
    } catch (e) {
      /* ignore */
    }
    try {
      const p = localStorage.getItem("qlcplus-stage-props-demo");
      if (p) savedProps = JSON.parse(p);
    } catch (e) {
      /* ignore */
    }
    if (!savedStage) {
      savedStage = defaultStageDraft();
      demoLayout(savedStage, app.rigRaw);
    }
    app.stage.saved = savedStage;
    app.stage.draft = deepClone(app.stage.saved);
    app.stage.showPath = "demo";
    app.stage.fileExists = true;
    app.props.saved = mergeWithStarters(savedProps);
    app.props.draft = deepClone(app.props.saved);
    setConnStatus("demo");
  } else {
    app.dmxByUniverse = {};
    app.ws = new StageWS({
      onOpen: () => {
        setConnStatus("ok");
        app.ws.subscribe();
        loadAllFromServer();
      },
      onClose: () => setConnStatus("down"),
      onDmx: (uni, base64) => {
        app.dmxByUniverse[uni] = decodeDmxFrame(base64);
        app.dmxFrames = (app.dmxFrames || 0) + 1;
        app._dmxDirty = true; // perf item 5: tells the per-frame loop a recompute is actually needed
      },
      onRigChanged: async () => {
        try {
          const rig = await app.ws.getStageRig();
          const newShow = rig.show || null;
          if (app.stage.showPath !== null && newShow !== app.stage.showPath) {
            // the operator switched shows in QLC+
            if (app.stage.dirty) {
              showReloadBanner(
                "QLC+ switched to " + (showBaseName(newShow) || "an unsaved show") + ". Load its stage? Unsaved changes will be lost.",
                () => switchToNewShow(rig)
              );
            } else {
              await switchToNewShow(rig);
            }
            return;
          }
          app.rigRaw = rig;
          app.rigIndex = buildRigIndex(rig, app.stage.draft.models);
          const placedCount = autoPlaceMissing(app.stage.draft, app.rigIndex);
          if (placedCount > 0) {
            markStageDirty();
            showToast("Placed " + placedCount + " new fixture" + (placedCount === 1 ? "" : "s"));
          }
          rebuildSceneFromDraft();
        } catch (e) {
          /* the periodic getStageRig retry on the next RIG_CHANGED will catch up */
        }
      },
      onStageSaved: () => {
        if (app.stage.dirty) showReloadBanner("The stage was saved elsewhere.", reloadStageFromServer);
        else reloadStageFromServer();
      },
      onPropsSaved: () => {
        if (app.props.dirty) showReloadBanner("The prop library was saved elsewhere.", reloadPropsFromServer);
        else reloadPropsFromServer();
      },
    });
    app.ws.start();
  }

  rebuildSceneFromDraft();
  initEditorMode(app);
  initPropsMode(app);
  app.cameraControls = await initCameraControlsGlue();
  app.isFlyActive = () => !!(app.cameraControls && app.cameraControls.isFlyActive && app.cameraControls.isFlyActive());
  scene.start();
  scene.cameraPreset("foh");
  updateShowNameLabel();
  wireSettingsPopup();
  wireGraphicsSettingsTab().catch((e) => console.warn("Graphics settings tab failed to initialize", e));
  wireSeats();
  wireCrosshair();
  wireMinimap();
  wireGameUiPolish();
  updateHistoryButtons();

  document.querySelectorAll(".mode-btn").forEach((b) =>
    b.addEventListener("click", () => {
      setMode(b.dataset.mode);
      if (leftPanelState.panel === "props" && !leftPanelState.collapsed) {
        leftPanelState.panel = "scene";
        saveLeftPanelState();
        applyLeftPanelState();
      }
    })
  );
  initActivityBar();
  setMode(app.mode); // sync mode buttons/panels/selection with the default mode (view, as of 2026-09-29)
  document.getElementById("save-btn").addEventListener("click", () => {
    saveStage().catch((e) => alert("Save failed: " + e.message));
  });
  document.getElementById("revert-btn").addEventListener("click", revertStage);
  const undoBtn = document.getElementById("undo-btn");
  const redoBtn = document.getElementById("redo-btn");
  if (undoBtn) undoBtn.addEventListener("click", undo);
  if (redoBtn) redoBtn.addEventListener("click", redo);
  window.addEventListener("keydown", (ev) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return; // browser handles Ctrl+Z natively there
    if (!(ev.ctrlKey || ev.metaKey)) return;
    const key = ev.key.toLowerCase();
    if (key === "z" && !ev.shiftKey) {
      ev.preventDefault();
      undo();
    } else if (key === "y" || (key === "z" && ev.shiftKey)) {
      ev.preventDefault();
      redo();
    }
  });
  document.getElementById("labels-toggle").addEventListener("change", (ev) => {
    scene.setLabelsVisible(ev.target.checked);
    if (app._syncLabelsToolbarBtn) app._syncLabelsToolbarBtn();
  });
  document.getElementById("camera-preset-select").addEventListener("change", (ev) => scene.cameraPreset(ev.target.value));
  document.getElementById("reload-banner-btn").addEventListener("click", () => {
    if (app._reloadAction) app._reloadAction();
    hideReloadBanner();
  });
  document.getElementById("reload-banner-dismiss").addEventListener("click", hideReloadBanner);

  setupWindowStageAPI();
  if (SELFTEST) runSelftestPanel();
}

boot().catch((e) => {
  // Never let a boot error surface as an uncaught console error in #demo
  // mode testing; show it inline instead.
  console.error("stage boot failed", e);
  const div = document.createElement("div");
  div.style.cssText = "position:fixed;top:50px;left:20px;color:#ff6b6b;background:#1a1a1a;padding:10px;border-radius:8px;z-index:999";
  div.textContent = "Stage failed to start: " + (e && e.message);
  document.body.appendChild(div);
});
