/*
  stage-editor.js

  Edit mode: selection (list or canvas raycast), TransformControls gizmo
  with snap, Maya-style modifier-held direct manipulation (Alt=move,
  Shift=rotate, Ctrl=scale), the right-click context menu, keyboard
  shortcuts, the numeric properties panel (feet/inches via stage-units.js),
  the anchor, room size, the Unplaced tray, distribute, and delete.

  All edits mutate app.stage.draft directly and go through
  app.commitDraftChange() (stage-app.js), which marks the draft dirty,
  rebuilds the scene and pushes one undo/redo history entry. Nothing
  reaches disk until Save (stage-app.js owns saveStage/revert). See
  stage-app.js's header for the shared `app` object contract.

  View vs. Edit mode (operator spec): View is look-only - camera navigation
  (pan/mouselook/WASD, all in stage-scene.js/stage-camera.js) is all that
  works; a click/right-click on an item does nothing beyond a one-time
  "switch to Edit" hint (showViewModeHintOnce), and entering View mode
  (stage-app.js's setMode) clears the selection and hides Properties. Edit
  mode has everything below. Undo/redo (Ctrl+Z/Ctrl+Y, wired in stage-app.js)
  is NOT gated by mode - it always works, for both draft edits and camera
  moves.

  Selection/manipulation model (Edit mode only):
  - A left CLICK (<4px movement) on a fixture/object selects it (shows the
    Properties panel - a floating overlay over the viewport, see stage.css,
    so it never resizes the canvas - and attaches the move gizmo); on empty
    space it deselects and hides the panel.
  - A left DRAG that starts on the current, unlocked selection moves it on
    the horizontal plane at its own height - exactly like an Alt-drag -
    while a drag starting on empty space or an unselected item still pans
    (OrbitControls owns it). Right-click always opens the context menu.
  - Alt/Shift/Ctrl-held drags (translate/rotate/scale) work as before, but
    the mode used to COMMIT a drag on release is always read from the
    TransformControls event/`.mode` at drag time, never from the app's
    "sticky" gizmo-mode preference - those two can differ while a modifier
    is held (the gizmo mode changes to match the modifier, but the sticky
    preference doesn't), and using the wrong one was the cause of
    scale/rotate/move "snapping back" on release.
*/

import * as THREE from "three";
import { parseLength, formatLength } from "./stage-units.js";
import { userToThree, threeToUser, mountQuaternion } from "./stage-scene.js";
import { loadLookIndex, renderLookThumbnail } from "./stage-looks.js";
import { getTextureEntry, buildThumbElement, openTexturePicker, buildFilterControls } from "./stage-textures.js";

// The index.json the fetch_gdtf.py downloader/index-builder produces
// hardcodes movable:false for every builtin .dae (tools/stagelib/
// fetch_gdtf.py, out of this fork's stage-visualizer file ownership), even
// though builtin/moving_head.dae and builtin/scanner.dae both really do have
// arm+head nodes (stage-looks.js itself now detects this correctly at build
// time - see its own small fix). This table lets the Look gallery's
// "Moving (spot/beam)" grouping/badge get it right without touching that
// generator or its data file.
const KNOWN_MOVABLE_LOOK_IDS = new Set(["builtin/moving_head", "builtin/scanner"]);
function lookEntryIsMovable(entry) {
  return !!(entry && (entry.movable || KNOWN_MOVABLE_LOOK_IDS.has(entry.id)));
}

const SNAP_OPTIONS_IN = [1, 3, 6, 12];

function round16th(inches) {
  return Math.round(inches * 16) / 16;
}
function roundScale(v) {
  return Math.round(v * 1000) / 1000;
}
// Hard backstop for the native TransformControls scale handles: their drag
// ratio is (current distance from the gizmo's screen-projected origin) /
// (distance at mousedown). Grabbing a handle close to that origin - easy to
// do since the whole gizmo sits right on top of a small object, or on a
// fixture/other item just behind it on screen - makes the denominator tiny
// and the result can explode to billions (confirmed live via CDP-driven
// #demo testing: an ordinary ~60px drag on a real handle produced a scale
// of 1.27e9). A sane clamp keeps a bad grab from ever reaching the draft as
// a broken (invisible or room-sized) object, while normal drags are
// untouched.
function clampScale(v) {
  return Math.min(Math.max(v, 0.05), 20);
}
function formatDeg(v) {
  const r = Math.round((v || 0) * 100) / 100;
  return String(Object.is(r, -0) ? 0 : r);
}

// rot=[rx,ry,rz]; objects historically only stored a bare `rz` yaw - read
// that as [0,0,rz] and (see setRotAxis) keep writing rz back for anything
// that still expects it (item 11).
function getRot(entry) {
  if (entry && Array.isArray(entry.rot)) return entry.rot.slice();
  return [0, 0, (entry && entry.rz) || 0];
}
function setRotAxis(entry, axisIndex, value, isObject) {
  const rot = getRot(entry);
  rot[axisIndex] = value;
  entry.rot = rot;
  if (isObject) entry.rz = rot[2];
}
function yawDeltaDeg(qBefore, qAfter) {
  const deltaQ = qAfter.clone().multiply(qBefore.clone().invert());
  return THREE.MathUtils.radToDeg(2 * Math.atan2(deltaQ.y, deltaQ.w));
}

function isTypingTarget() {
  const t = document.activeElement;
  if (!t) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable;
}

const MODIFIER_MODE = { Alt: "translate", Shift: "rotate", Control: "scale" };

export function initEditorMode(app) {
  const els = {
    fixturesList: document.getElementById("fixtures-list"),
    unplacedList: document.getElementById("unplaced-list"),
    objectsList: document.getElementById("objects-list"),
    snapSelect: document.getElementById("snap-select"),
    anchorX: document.getElementById("anchor-x"),
    anchorY: document.getElementById("anchor-y"),
    anchorZ: document.getElementById("anchor-z"),
    anchorSetBtn: document.getElementById("anchor-set-btn"),
    roomWidth: document.getElementById("room-width"),
    roomDepth: document.getElementById("room-depth"),
    roomHeight: document.getElementById("room-height"),
    rightPanel: document.getElementById("right-panel"),
    propsPanel: document.getElementById("selection-fields"),
    noSelection: document.getElementById("selection-empty"),
    distAxis: document.getElementById("distribute-axis"),
    distSpacing: document.getElementById("distribute-spacing"),
    distBtn: document.getElementById("distribute-btn"),
    contextMenu: document.getElementById("context-menu"),
    lookGallery: document.getElementById("look-gallery-popover"),
    deleteOverlay: document.getElementById("delete-confirm"),
    deleteText: document.getElementById("delete-confirm-text"),
    deleteOkBtn: document.getElementById("delete-confirm-ok"),
    deleteCancelBtn: document.getElementById("delete-confirm-cancel"),
  };
  if (!els.fixturesList) return; // Edit panel not present in this build of stage.html

  // View mode is look-only (operator spec): no selection, Properties,
  // gizmo, manipulation or context menu - only camera navigation. Shown
  // once (per page load) the first time the operator tries to click an
  // item or right-click while still in View mode.
  let viewModeHintShown = false;
  function showViewModeHintOnce() {
    if (viewModeHintShown) return;
    viewModeHintShown = true;
    app.showToast("View mode — switch to Edit to change the layout");
  }

  // ---- View mode inspect: a click outlines the item and shows its name tag (even with labels
  // off); no selection, Properties or gizmo. Cleared by an empty click or by leaving View mode.
  let inspectBox = null;
  let inspectTag = null;
  let inspectRoot = null;
  let inspectRaf = 0;
  const _inspectBox3 = new THREE.Box3();
  const _inspectTop = new THREE.Vector3();
  function itemName(root) {
    let name = null;
    root.traverse((o) => {
      if (!name && o.isCSS2DObject && o.element) name = o.element.textContent;
    });
    return name || (root.userData.key || "").replace(/^(fixture|object):/, "");
  }
  function clearInspect() {
    cancelAnimationFrame(inspectRaf);
    inspectRaf = 0;
    if (inspectBox) inspectBox.visible = false; // kept (not disposed) so its shader stays warm
    if (inspectTag) {
      inspectTag.remove();
      inspectTag = null;
    }
    inspectRoot = null;
  }
  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && inspectRoot) clearInspect();
  });
  function inspect(root) {
    clearInspect();
    inspectRoot = root;
    if (!inspectBox) {
      inspectBox = new THREE.BoxHelper(root, 0x5ec8ff);
      inspectBox.material.depthTest = false; // outline stays visible through other geometry
      inspectBox.material.transparent = true;
      inspectBox.renderOrder = 999;
      app.scene.scene.add(inspectBox);
    }
    inspectBox.setFromObject(root);
    inspectBox.visible = true;
    inspectTag = document.createElement("div");
    inspectTag.className = "stage-label";
    inspectTag.id = "inspect-tag";
    inspectTag.style.cssText = "position:fixed;z-index:60;pointer-events:none;transform:translate(-50%,-100%);" +
      "border:1px solid #5ec8ff;background:rgba(10,16,24,.85)";
    inspectTag.textContent = itemName(root);
    document.body.appendChild(inspectTag);
    const canvas = app.scene.renderer.domElement;
    (function follow() {
      if (!inspectRoot || app.mode !== "view") return clearInspect();
      inspectBox.update();
      _inspectBox3.setFromObject(inspectRoot);
      _inspectBox3.getCenter(_inspectTop);
      _inspectTop.y = _inspectBox3.max.y;
      _inspectTop.project(app.scene.camera);
      const r = canvas.getBoundingClientRect();
      const onScreen = _inspectTop.z < 1 && Math.abs(_inspectTop.x) <= 1.2 && Math.abs(_inspectTop.y) <= 1.2;
      inspectTag.style.display = onScreen ? "block" : "none";
      inspectTag.style.left = r.left + ((_inspectTop.x + 1) / 2) * r.width + "px";
      inspectTag.style.top = r.top + ((1 - _inspectTop.y) / 2) * r.height - 6 + "px";
      inspectRaf = requestAnimationFrame(follow);
    })();
  }

  // ---- snap (always follows the selector; Shift is a manipulation
  // modifier now, not a "disable snap while held" toggle) --------------

  SNAP_OPTIONS_IN.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = String(v);
    opt.textContent = formatLength(v);
    els.snapSelect.appendChild(opt);
  });
  els.snapSelect.value = "1";
  els.snapSelect.addEventListener("change", applySnap);

  function applySnap() {
    const snapIn = parseFloat(els.snapSelect.value);
    app.scene.transform.setTranslationSnap(isNaN(snapIn) ? null : snapIn * 0.0254);
    app.scene.transform.setRotationSnap(THREE.MathUtils.degToRad(5));
  }
  applySnap();
  // A bit bigger than the TransformControls default (1): its scale/rotate
  // handles sit closer to the object's pivot at size=1, which made it easy
  // to grab a handle very close to the screen-projected origin - the scale
  // handle's drag ratio uses that grab point's distance from the origin as
  // its denominator, so a near-zero starting distance could blow the result
  // up to an enormous factor (see clampScale below for the hard backstop).
  app.scene.transform.size = 1.4;

  // ---- selection -----------------------------------------------------
  // (gizmoTarget/qBeforeDrag/activeModifier* are declared here, ahead of
  // setGizmoMode's first call below, since updateGizmoAttachment() reads
  // them and a `let` is in its temporal dead zone until its own
  // declaration runs - this bit the page before, see .claude/memory)

  let gizmoTarget = null; // the single Object3D currently attached, if any
  let qBeforeDrag = null;
  let activeModifierKey = null; // 'Alt' | 'Shift' | 'Control'
  let activeModifier = null; // 'translate' | 'rotate' | 'scale'

  // ---- persistent gizmo mode (1/2/3 = move/rotate/scale; W/E now belong
  // to the WASD fly camera - see stage-scene.js) -----------------------

  function selectionIsFixturesOnly(keys) {
    return keys.length > 0 && keys.every((k) => k.indexOf("fixture:") === 0);
  }

  function setGizmoMode(mode) {
    if (mode === "scale" && selectionIsFixturesOnly(selectedKeys())) {
      app.showToast("fixtures keep their real size");
      mode = "translate";
    }
    app.editor = app.editor || {};
    app.editor.gizmoMode = mode;
    updateGizmoAttachment();
  }
  setGizmoMode("translate");

  function getEntryForKey(key) {
    const [type, idStr] = key.split(":");
    if (type === "fixture") {
      const id = parseInt(idStr, 10);
      return { type: "fixture", id: id, entry: app.stage.draft.fixtures[id], model: app.rigIndex && app.rigIndex.byId.get(id) };
    }
    const obj = (app.stage.draft.objects || []).find((o) => o.id === idStr);
    return { type: "object", id: idStr, entry: obj };
  }

  function selectedKeys() {
    return Array.from(app.selection);
  }

  // View mode is look-only: every selection path (canvas, left-panel lists, test hook) only
  // inspects there - outline + name tag, never Properties or the gizmo.
  function inspectKey(key) {
    const isFixture = key.indexOf("fixture:") === 0;
    const map = isFixture ? app.scene.fixtureEntries : app.scene.objectEntries;
    const rec = map && map.get(isFixture ? parseInt(key.slice(8), 10) : key.slice(7));
    const root = rec ? (rec.root || rec) : null;
    if (root && root !== inspectRoot) inspect(root);
    else clearInspect();
  }
  function selectOnly(key) {
    if (app.mode === "view") return inspectKey(key);
    app.selection.clear();
    app.selection.add(key);
    refreshAll();
  }
  function toggleSelect(key) {
    if (app.mode === "view") return inspectKey(key);
    if (app.selection.has(key)) app.selection.delete(key);
    else app.selection.add(key);
    refreshAll();
  }

  function setLocked(key, locked) {
    const info = getEntryForKey(key);
    if (!info || !info.entry) return false;
    app.commitDraftChange(() => {
      info.entry.locked = !!locked;
    });
    refreshAll();
    return true;
  }

  function updateGizmoAttachment() {
    if (app.mode === "view") {
      gizmoTarget = null;
      app.scene.transform.detach();
      return;
    }
    const keys = selectedKeys();
    if (keys.length === 1) {
      const key = keys[0];
      const lockedInfo = getEntryForKey(key);
      if (lockedInfo && lockedInfo.entry && lockedInfo.entry.locked) {
        gizmoTarget = null;
        app.scene.transform.detach();
        return;
      }
      const isFixture = key.indexOf("fixture:") === 0;
      const map = isFixture ? app.scene.fixtureEntries : app.scene.objectEntries;
      const id = isFixture ? parseInt(key.slice(8), 10) : key.slice(7);
      const rec = map.get(id);
      gizmoTarget = rec ? (rec.root || rec) : null;
      if (gizmoTarget) {
        app.scene.transform.attach(gizmoTarget);
        // item 5: while a modifier is actively held, ITS mode wins over the
        // sticky app.editor.gizmoMode preference - updateGizmoAttachment()
        // re-runs after every commit (refreshAll(), via commitDraftChange),
        // including mid-hold, and must not stomp the held modifier's mode
        // back to the sticky one or the NEXT drag in the same hold would
        // wrongly grab the sticky mode's handles instead.
        const effectiveMode = activeModifier || app.editor.gizmoMode;
        const mode = effectiveMode === "scale" && isFixture ? "translate" : effectiveMode;
        app.scene.transform.setMode(mode);
        if (mode === "rotate") {
          app.scene.transform.showX = false;
          app.scene.transform.showZ = false;
          app.scene.transform.showY = true;
        } else {
          app.scene.transform.showX = true;
          app.scene.transform.showY = true;
          app.scene.transform.showZ = true;
        }
      } else {
        app.scene.transform.detach();
      }
    } else {
      gizmoTarget = null;
      app.scene.transform.detach();
    }
  }

  app.scene.transform.addEventListener("mouseDown", () => {
    if (gizmoTarget) qBeforeDrag = gizmoTarget.quaternion.clone();
  });

  // Live-refreshes the Properties panel while the native gizmo is being
  // dragged (item 3) - objectChange fires on every drag tick.
  app.scene.transform.addEventListener("objectChange", () => {
    // Perf: object/prop/truss roots freeze matrixAutoUpdate once built (see
    // freezeStaticSubtree, stage-scene.js) - TransformControls mutates the
    // attached object's position/quaternion/scale directly while dragging,
    // so it needs the same explicit updateMatrix() poke as the Maya-modifier
    // path above or the mesh won't visually follow the gizmo. Harmless for a
    // fixture root (never frozen).
    if (gizmoTarget) gizmoTarget.updateMatrix();
    if (app.scene.transform.dragging) refreshPanel();
  });

  app.scene.transform.addEventListener("mouseUp", (ev) => {
    if (!gizmoTarget) return;
    const keys = selectedKeys();
    if (keys.length !== 1) return;
    const info = getEntryForKey(keys[0]);
    if (!info || !info.entry) return;
    // The mode THIS drag actually used - not app.editor.gizmoMode, which is
    // the app's "sticky" preference and can be stale while a modifier is
    // held (beginModifierHold below only changes the live gizmo mode, not
    // the sticky one). Using the sticky value here was the actual bug
    // behind scale/rotate/move snapping back on release (items 3/4/5).
    const mode = ev.mode;

    app.commitDraftChange(() => {
      if (mode === "translate") {
        const [x, y, z] = threeToUser(gizmoTarget.position);
        info.entry.pos = [round16th(x), round16th(y), round16th(z)];
      } else if (mode === "rotate" && qBeforeDrag) {
        const deltaYawDeg = yawDeltaDeg(qBeforeDrag, gizmoTarget.quaternion);
        const rot = getRot(info.entry);
        rot[2] = rot[2] + deltaYawDeg;
        info.entry.rot = rot;
        if (info.type === "object") info.entry.rz = rot[2];
        const hang = info.type === "fixture" ? info.entry.hang : "floor";
        gizmoTarget.quaternion.copy(mountQuaternion(rot, hang));
        gizmoTarget.updateMatrix(); // see the objectChange handler above
      } else if (mode === "scale" && info.type === "object") {
        const s = gizmoTarget.scale;
        info.entry.scale = [roundScale(clampScale(s.x)), roundScale(clampScale(s.z)), roundScale(clampScale(s.y))];
        // gizmoTarget itself is about to be discarded by the rebuild below,
        // but clamp it too so a mid-flight read (or a screenshot taken
        // before refreshAll() swaps in the rebuilt object) never shows the
        // degenerate value either.
        gizmoTarget.scale.set(info.entry.scale[0], info.entry.scale[2], info.entry.scale[1]);
        gizmoTarget.updateMatrix(); // see the objectChange handler above
      }
    });
    refreshAll();
  });

  // ---- Maya-style modifier hold: Alt=move, Shift=rotate, Ctrl=scale ---
  // While held: the TransformControls gizmo mode reflects it, OrbitControls
  // is fully disabled, and a left-drag on the selected item (or an item
  // under the pointer, selecting it first) manipulates it directly instead
  // of panning the camera. Released: previous gizmo mode + orbit restored.
  //
  // Shift is double-booked with the WASD/QE fly camera's "fast" modifier
  // (stage-scene.js): while a movement key is actually held, Shift means
  // fast, never rotate - see the isFlyActive() guard below.

  let manipulating = false;
  let manipMode = null; // the mode of the CURRENT drag gesture (translate/rotate/scale)
  let manipInfos = [];
  let manipStart = {};
  let manipPlaneHeight = 0;

  function beginModifierHold(key) {
    if (activeModifierKey || isTypingTarget()) return;
    if (key === "Shift" && app.isFlyActive && app.isFlyActive()) return; // fly camera (stage-camera.js) owns Shift right now
    activeModifierKey = key;
    activeModifier = MODIFIER_MODE[key];
    app.scene.transform.setMode(activeModifier);
    // ROOT CAUSE of the rotate/scale "doesn't work"/"snaps to a huge value"
    // bug: setMode() above leaves the REAL TransformControls gizmo visible
    // AND interactive at the selected item's pivot - exactly where a
    // modifier-hold drag naturally starts (the operator clicks the item
    // itself, not empty space). TransformControls' own pointerdown handler
    // is registered on the same canvas and runs first (see the
    // `downWasGizmo` comment below), so it would often steal the press -
    // e.g. its uniform-scale centre handle sits AT the pivot, and grabbing
    // it with a down-point essentially on top of the pivot makes its
    // distance-ratio scale factor blow up to enormous values; its rotate
    // ring, picked instead of dragged as a ring, can likewise yield ~0
    // rotation. The whole point of a modifier hold is OUR OWN pointer-delta
    // manipulation below (continueManipulation), never the native gizmo -
    // so disable its pointer handling for the duration (it stays visible,
    // just non-interactive: `enabled` only gates pointerHover/Down/Move/Up,
    // see three/examples/jsm/controls/TransformControls.js).
    app.scene.transform.enabled = false;
    app.scene.setOrbitSuppressed(true);
  }
  function endModifierHold() {
    if (!activeModifierKey) return;
    if (manipulating) finishManipulation();
    activeModifierKey = null;
    activeModifier = null;
    app.scene.transform.enabled = true;
    app.scene.setOrbitSuppressed(false);
    updateGizmoAttachment(); // restores gizmo mode/visibility for the real selection
  }

  // Safety net for a missed keyup (focus changes, OS-level shortcuts, etc):
  // re-derive the held modifier from the event's own flags before starting
  // a new gesture (item 5). Never touches an in-progress drag.
  function liveModifierKeyFromEvent(ev) {
    if (ev.ctrlKey) return "Control";
    if (ev.shiftKey) return "Shift";
    if (ev.altKey) return "Alt";
    return null;
  }
  function reconcileModifierFromEvent(ev) {
    if (manipulating) return;
    const wantKey = liveModifierKeyFromEvent(ev);
    if (wantKey === activeModifierKey) return;
    if (activeModifierKey) endModifierHold();
    if (wantKey) beginModifierHold(wantKey);
  }

  function startManipulation(ev, hit, mode) {
    const keys = selectedKeys();
    let targetKeys = keys;
    if (hit) {
      const hitKey = hit.userData.key;
      if (!app.selection.has(hitKey)) {
        selectOnly(hitKey);
        targetKeys = [hitKey];
      }
    } else if (!keys.length) {
      return false;
    }

    const infos = targetKeys.map(getEntryForKey).filter((i) => i && i.entry && !i.entry.locked);
    if (!infos.length) return false;

    if (mode === "scale" && !infos.every((i) => i.type === "object")) {
      app.showToast("fixtures keep their real size");
      return false;
    }

    manipInfos = infos
      .map((info) => {
        const map = info.type === "fixture" ? app.scene.fixtureEntries : app.scene.objectEntries;
        const rec = map.get(info.id);
        return { info: info, root: rec ? rec.root || rec : null };
      })
      .filter((m) => m.root);
    if (!manipInfos.length) return false;

    manipulating = true;
    manipMode = mode;

    if (mode === "translate") {
      manipPlaneHeight = manipInfos[0].root.position.y;
      const p0 = app.scene.pickAtHeight(ev.clientX, ev.clientY, manipPlaneHeight);
      manipStart = { userPoint: p0 };
      manipInfos.forEach((m) => {
        m.startPos = m.root.position.clone();
      });
    } else if (mode === "rotate") {
      manipStart = { mouseX: ev.clientX };
      manipInfos.forEach((m) => {
        m.startYaw = getRot(m.info.entry)[2];
      });
    } else if (mode === "scale") {
      manipStart = { mouseY: ev.clientY };
      manipInfos.forEach((m) => {
        m.startScale = (m.info.entry.scale || [1, 1, 1]).slice();
      });
    }
    return true;
  }

  function continueManipulation(ev) {
    if (manipMode === "translate") {
      if (!manipStart.userPoint) return;
      const p = app.scene.pickAtHeight(ev.clientX, ev.clientY, manipPlaneHeight);
      if (!p) return;
      const dxUser = p[0] - manipStart.userPoint[0];
      const dyUser = p[1] - manipStart.userPoint[1];
      const snapIn = parseFloat(els.snapSelect.value) || 0;
      manipInfos.forEach((m) => {
        const startUser = threeToUser(m.startPos);
        let nx = startUser[0] + dxUser;
        let ny = startUser[1] + dyUser;
        if (snapIn > 0) {
          nx = Math.round(nx / snapIn) * snapIn;
          ny = Math.round(ny / snapIn) * snapIn;
        }
        m.root.position.copy(userToThree(nx, ny, startUser[2]));
        // Perf: object/prop/truss roots have matrixAutoUpdate=false once
        // built (see freezeStaticSubtree in stage-scene.js) - a direct
        // position/quaternion/scale mutation during a live drag needs an
        // explicit updateMatrix() or it won't visually move until the next
        // full rebuild. Harmless (a redundant no-op) for fixture roots,
        // which are never frozen.
        m.root.updateMatrix();
      });
    } else if (manipMode === "rotate") {
      const deltaDeg = (ev.clientX - manipStart.mouseX) * 0.5;
      manipInfos.forEach((m) => {
        const rot = getRot(m.info.entry);
        rot[2] = m.startYaw + deltaDeg;
        const hang = m.info.type === "fixture" ? m.info.entry.hang : "floor";
        m.root.quaternion.copy(mountQuaternion(rot, hang));
        m.root.updateMatrix(); // see the translate branch above
        m._liveYaw = rot[2];
      });
    } else if (manipMode === "scale") {
      const deltaPx = manipStart.mouseY - ev.clientY; // drag up = bigger
      const factor = Math.min(Math.max(0.1, 1 + deltaPx / 200), 20);
      manipInfos.forEach((m) => {
        const s = m.startScale;
        m.root.scale.set(s[0] * factor, s[2] * factor, s[1] * factor);
        m.root.updateMatrix(); // see the translate branch above
        m._liveScale = [s[0] * factor, s[1] * factor, s[2] * factor];
      });
    }
  }

  function finishManipulation() {
    if (!manipulating) return;
    const mode = manipMode;
    manipulating = false;
    app.commitDraftChange(() => {
      manipInfos.forEach((m) => {
        if (mode === "translate") {
          const [x, y, z] = threeToUser(m.root.position);
          m.info.entry.pos = [round16th(x), round16th(y), round16th(z)];
        } else if (mode === "rotate" && m._liveYaw !== undefined) {
          setRotAxis(m.info.entry, 2, m._liveYaw, m.info.type === "object");
        } else if (mode === "scale" && m.info.type === "object" && m._liveScale) {
          m.info.entry.scale = m._liveScale.map(roundScale);
        }
      });
    });
    manipInfos = [];
    manipMode = null;
    refreshAll();
  }

  // Live value overrides used by the Properties panel while a drag is in
  // progress (items 3/4): during a manipulation the draft itself hasn't
  // changed yet (only the live Object3D has), so the panel must read from
  // the scene, not the entry, or it would show stale numbers until release.
  function liveManipValueFor(info) {
    if (manipulating) {
      const m = manipInfos.find((mi) => mi.info.type === info.type && mi.info.id === info.id);
      if (m) {
        if (manipMode === "translate") return { pos: threeToUser(m.root.position) };
        if (manipMode === "rotate" && m._liveYaw !== undefined) return { yaw: m._liveYaw };
        if (manipMode === "scale" && m._liveScale) return { scale: m._liveScale };
      }
      return null;
    }
    if (gizmoTarget && app.scene.transform.dragging) {
      const keys = selectedKeys();
      if (keys.length === 1) {
        const cur = getEntryForKey(keys[0]);
        if (cur && cur.type === info.type && cur.id === info.id) {
          const mode = app.scene.transform.mode;
          if (mode === "translate") return { pos: threeToUser(gizmoTarget.position) };
          if (mode === "rotate" && qBeforeDrag) {
            const deltaYawDeg = yawDeltaDeg(qBeforeDrag, gizmoTarget.quaternion);
            return { yaw: getRot(info.entry)[2] + deltaYawDeg };
          }
          if (mode === "scale" && info.type === "object") {
            const s = gizmoTarget.scale;
            return { scale: [roundScale(clampScale(s.x)), roundScale(clampScale(s.z)), roundScale(clampScale(s.y))] };
          }
        }
      }
    }
    return null;
  }

  window.addEventListener("keydown", (ev) => {
    if (isTypingTarget()) return;
    if (els.deleteOverlay && !els.deleteOverlay.classList.contains("hidden")) return; // the delete popup owns keys now
    // View mode is look-only: no gizmo-mode keys, modifier-drag manipulation,
    // delete or duplicate shortcuts - camera navigation (stage-camera.js)
    // listens on its own and is unaffected by this early return.
    if (app.mode !== "edit") return;
    if (ev.key === "Alt") ev.preventDefault();
    if ((ev.key === "Alt" || ev.key === "Shift" || ev.key === "Control") && !activeModifierKey) {
      beginModifierHold(ev.key);
      return;
    }
    if (activeModifierKey) return; // ignore other shortcuts while a modifier hold is active

    if (!ev.ctrlKey && !ev.metaKey && !ev.altKey) {
      // item 12: 1/2/3 = move/rotate/scale (W/E/R now belong to the WASD
      // fly camera in stage-scene.js)
      if (ev.key === "1") {
        setGizmoMode("translate");
        return;
      }
      if (ev.key === "2") {
        setGizmoMode("rotate");
        return;
      }
      if (ev.key === "3") {
        setGizmoMode("scale");
        return;
      }
      if (ev.key === "Escape") {
        if (hideContextMenu()) return;
        app.selection.clear();
        refreshAll();
        return;
      }
      if (ev.key === "Delete" || ev.key === "Backspace") {
        requestDeleteConfirmation(selectedKeys());
        return;
      }
    }
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === "d" || ev.key === "D")) {
      ev.preventDefault();
      selectedKeys()
        .filter((k) => k.indexOf("object:") === 0)
        .forEach((k) => duplicateObjectByKey(k));
    }
  });

  window.addEventListener("keyup", (ev) => {
    if (ev.key === "Alt") ev.preventDefault();
    if (ev.key === activeModifierKey) endModifierHold();
  });

  window.addEventListener("blur", () => {
    endModifierHold();
  });

  // Selecting/manipulating is driven by pointerdown/move/up with a small
  // movement threshold, so that a plain camera drag doesn't also fire a
  // selection change (item 2/5).
  let downX = 0;
  let downY = 0;
  let downButton = -1;
  let downWasGizmo = false;
  let pointerDownHit = null;
  let dragIsManipulate = false; // decided at pointerdown: manipulate vs let OrbitControls pan/orbit

  app.scene.renderer.domElement.addEventListener("pointerdown", (ev) => {
    ev.preventDefault(); // never start a native text/image drag-selection (item 1)
    if (app.mode === "edit") reconcileModifierFromEvent(ev);
    downX = ev.clientX;
    downY = ev.clientY;
    downButton = ev.button;
    // TransformControls' own pointerdown handler runs before ours (it was
    // registered on this element when the scene was created) and sets
    // `dragging` synchronously if the press hit a gizmo handle - by the
    // time OUR pointerdown handler runs, that flag already reflects it.
    downWasGizmo = app.scene.transform.dragging;
    pointerDownHit = ev.button === 0 ? app.scene.pick(ev.clientX, ev.clientY) : null;

    // item 2: a plain left-press (no modifier) that starts on the current,
    // unlocked selection manipulates it (move) exactly like an Alt-drag,
    // instead of panning; a press on empty space or an unselected item
    // still lets OrbitControls pan/orbit as usual.
    dragIsManipulate = false;
    if (app.mode === "edit" && ev.button === 0 && !downWasGizmo) {
      if (activeModifier) {
        dragIsManipulate = true;
      } else if (pointerDownHit && app.selection.has(pointerDownHit.userData.key)) {
        const info = getEntryForKey(pointerDownHit.userData.key);
        if (info && info.entry && !info.entry.locked) dragIsManipulate = true;
      }
      if (dragIsManipulate) app.scene.setOrbitSuppressed(true);
    }
  });

  window.addEventListener("pointermove", (ev) => {
    // dragIsManipulate is only ever set true in Edit mode (pointerdown
    // below), so this naturally no-ops in View mode without a mode gate.
    if (downButton !== 0 || downWasGizmo || !dragIsManipulate) return;
    const dx = ev.clientX - downX;
    const dy = ev.clientY - downY;
    if (!manipulating) {
      if (dx * dx + dy * dy <= 16) return;
      const mode = activeModifier || "translate";
      // Bug found live-testing rotate/scale in #demo (CDP-driven): passing
      // pointerDownHit here unconditionally let startManipulation() silently
      // RESELECT to whatever was directly under the down-point (e.g. a
      // fixture standing right behind/below the selected object on screen)
      // the instant the drag threshold was crossed - so a Ctrl/Shift-held
      // scale/rotate drag on an already-selected OBJECT could jump to a
      // FIXTURE mid-gesture, which can't scale, silently aborting the whole
      // manipulation (just a "fixtures keep their real size" toast) even
      // though the operator never intended to touch that fixture. A modifier
      // hold means "manipulate my current selection" - only the unmodified
      // plain-drag-to-grab case (item 2) should use the hit to pick a target.
      const hitForStart = activeModifier ? null : pointerDownHit;
      if (!startManipulation(ev, hitForStart, mode)) {
        dragIsManipulate = false;
        if (!activeModifierKey) app.scene.setOrbitSuppressed(false);
        return;
      }
    }
    continueManipulation(ev);
    refreshPanel();
  });

  window.addEventListener("pointerup", (ev) => {
    // Left-click selection works in ANY mode (item A); only drag
    // manipulation (below, via dragIsManipulate/manipulating, both of which
    // are only ever armed while app.mode === "edit") is Edit-mode-only.
    if (manipulating) {
      finishManipulation();
      downButton = -1;
      dragIsManipulate = false;
      if (!activeModifierKey) app.scene.setOrbitSuppressed(false);
      return;
    }
    if (dragIsManipulate && !activeModifierKey) app.scene.setOrbitSuppressed(false);
    dragIsManipulate = false;
    if (downButton !== 0 || ev.button !== 0) return;
    downButton = -1;
    if (downWasGizmo) return; // was a gizmo drag, not a selection click
    const dx = ev.clientX - downX;
    const dy = ev.clientY - downY;
    if (dx * dx + dy * dy > 16) return; // moved more than ~4px: a camera drag, not a click

    const hit = app.scene.pick(ev.clientX, ev.clientY);
    // View mode is look-only (operator spec): a click never selects
    // anything there - only camera navigation works. Show a one-time hint
    // if it looked like the operator meant to interact with something.
    if (app.mode !== "edit") {
      // clicking the same item again (or Esc) clears it - inside a closed room there may be no empty spot
      if (hit && hit !== inspectRoot) inspect(hit);
      else clearInspect();
      return;
    }
    if (!hit) {
      // item 9: an empty-space click deselects and hides the Properties panel
      if (!ev.shiftKey) {
        app.selection.clear();
        refreshAll();
      }
      return;
    }
    const key = hit.userData.key;
    if (ev.shiftKey) {
      if (app.selection.has(key)) app.selection.delete(key);
      else app.selection.add(key);
    } else {
      app.selection.clear();
      app.selection.add(key);
    }
    refreshAll();
  });

  // ---- right-click context menu ---------------------------------------

  function genObjectId() {
    return "o" + Date.now().toString(36) + Math.floor(Math.random() * 1000);
  }

  function duplicateObjectByKey(key) {
    if (key.indexOf("object:") !== 0) return null;
    const id = key.slice(7);
    const src = (app.stage.draft.objects || []).find((o) => o.id === id);
    if (!src || src.locked) return null;
    let newId = null;
    app.commitDraftChange(() => {
      const copy = JSON.parse(JSON.stringify(src));
      newId = genObjectId();
      copy.id = newId;
      copy.pos = [copy.pos[0] + 12, copy.pos[1], copy.pos[2]];
      copy.name = (src.name || src.prop || "Object") + " copy";
      app.stage.draft.objects.push(copy);
    });
    selectOnly("object:" + newId);
    return newId;
  }

  // The actual delete mutation - used both by the confirmation popup below
  // AND by the window.__stage.deleteObject test hook, which intentionally
  // bypasses the confirmation UI (a programmatic API call already IS the
  // confirmation).
  function deleteObjectsByKeys(keys) {
    const allObjIds = keys.filter((k) => k.indexOf("object:") === 0).map((k) => k.slice(7));
    const hadFixtures = keys.some((k) => k.indexOf("fixture:") === 0);
    const lockedById = new Map((app.stage.draft.objects || []).map((o) => [o.id, !!o.locked]));
    const objectIds = allObjIds.filter((id) => !lockedById.get(id));
    if (!objectIds.length) {
      if (hadFixtures) app.showToast("Fixtures come from the QLC+ show — add or remove them in QLC+");
      return false;
    }
    app.commitDraftChange(() => {
      app.stage.draft.objects = (app.stage.draft.objects || []).filter((o) => objectIds.indexOf(o.id) === -1);
    });
    objectIds.forEach((id) => app.selection.delete("object:" + id));
    refreshAll();
    return true;
  }

  // ---- delete confirmation popup (item 7) -----------------------------

  let pendingDeleteAction = null;
  function hideDeleteConfirm() {
    if (!els.deleteOverlay) return;
    els.deleteOverlay.classList.add("hidden");
    pendingDeleteAction = null;
  }
  function showDeleteConfirm(names, hadFixtures, onConfirm) {
    if (!els.deleteOverlay) {
      onConfirm();
      return;
    }
    const n = names.length;
    let msg = "Delete " + n + " object" + (n === 1 ? "" : "s") + "? " + names.join(", ");
    if (hadFixtures) msg += " (fixtures stay — they're part of the QLC+ show)";
    els.deleteText.textContent = msg;
    pendingDeleteAction = onConfirm;
    els.deleteOverlay.classList.remove("hidden");
  }
  if (els.deleteOkBtn)
    els.deleteOkBtn.addEventListener("click", () => {
      const action = pendingDeleteAction;
      hideDeleteConfirm();
      if (action) action();
    });
  if (els.deleteCancelBtn) els.deleteCancelBtn.addEventListener("click", hideDeleteConfirm);
  window.addEventListener("keydown", (ev) => {
    if (!els.deleteOverlay || els.deleteOverlay.classList.contains("hidden")) return;
    if (ev.key === "Enter") {
      ev.preventDefault();
      if (els.deleteOkBtn) els.deleteOkBtn.click();
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      hideDeleteConfirm();
    }
  });

  // Only room objects can be deleted this way; a fixtures-only selection is
  // refused with a toast, a mixed selection deletes just the objects and
  // says so in the popup.
  function requestDeleteConfirmation(keys) {
    const objIds = keys.filter((k) => k.indexOf("object:") === 0).map((k) => k.slice(7));
    const lockedById = new Map((app.stage.draft.objects || []).map((o) => [o.id, !!o.locked]));
    const deletableIds = objIds.filter((id) => !lockedById.get(id));
    const hadFixtures = keys.some((k) => k.indexOf("fixture:") === 0);
    if (!deletableIds.length) {
      if (hadFixtures) app.showToast("Fixtures come from the QLC+ show");
      return;
    }
    const names = deletableIds.map((id) => {
      const o = (app.stage.draft.objects || []).find((o2) => o2.id === id);
      return (o && (o.name || o.prop)) || id;
    });
    showDeleteConfirm(names, hadFixtures, () => deleteObjectsByKeys(keys));
  }

  function resetFixtureOrientation(key) {
    const info = getEntryForKey(key);
    if (!info || !info.entry) return;
    app.commitDraftChange(() => {
      info.entry.rot = [0, 0, 0];
    });
    refreshAll();
  }

  function setAnchorToKey(key) {
    const info = getEntryForKey(key);
    if (!info || !info.entry) return;
    app.commitDraftChange(() => {
      const pos = info.entry.pos;
      const isPointMarker = info.type === "object" && info.entry.prop === "point-marker";
      app.stage.draft.anchor = [pos[0], pos[1], isPointMarker ? pos[2] : 0];
    });
    refreshAnchorFields();
  }

  function saveObjectAsProp(objId) {
    const obj = (app.stage.draft.objects || []).find((o) => o.id === objId);
    if (!obj) return;
    const def = app.propDefLookup(obj);
    if (!def) return;
    const name = window.prompt("Save as prop named:", (def.name || "Prop") + " copy");
    if (!name) return;
    const newId = "prop-" + Date.now().toString(36);
    const copy = JSON.parse(JSON.stringify(def));
    copy.name = name;
    app.props.draft.props[newId] = copy;
    app.props.dirty = true;
    if (app.props.onChanged) app.props.onChanged();
    app.showToast("Saved as prop in your library draft — click Save prop library to keep it.");
  }

  function addObjectSubmenuItems(groundPt) {
    const pos = groundPt || [app.stage.draft.anchor[0], app.stage.draft.anchor[1], 0];
    const ids = Object.keys((app.props.draft && app.props.draft.props) || {}).sort((a, b) => {
      const na = app.props.draft.props[a].name || a;
      const nb = app.props.draft.props[b].name || b;
      return na.localeCompare(nb);
    });
    return ids.map((id) => ({
      label: app.props.draft.props[id].name || id,
      action: () => app.placePropInstance(id, pos),
    }));
  }

  function hideContextMenu() {
    if (!els.contextMenu || els.contextMenu.classList.contains("hidden")) return false;
    els.contextMenu.classList.add("hidden");
    els.contextMenu.classList.remove("flip-up");
    els.contextMenu.innerHTML = "";
    return true;
  }

  function buildContextMenuRow(item) {
    const row = document.createElement("div");
    row.className = "ctx-item" + (item.disabled ? " disabled" : "") + (item.submenu ? " has-submenu" : "");
    row.textContent = item.label;
    if (item.disabled && item.title) row.title = item.title;
    if (item.submenu) {
      const sub = document.createElement("div");
      sub.className = "ctx-submenu";
      sub.addEventListener("wheel", (ev) => ev.stopPropagation()); // item 14b
      item.submenu.forEach((s) => sub.appendChild(buildContextMenuRow(s)));
      row.appendChild(sub);
      // item 14b: flip a submenu that would open past the right/bottom edge
      row.addEventListener("mouseenter", () => {
        sub.classList.remove("flip-left", "flip-up");
        const r = row.getBoundingClientRect();
        const subRect = sub.getBoundingClientRect();
        if (r.right + subRect.width > window.innerWidth) sub.classList.add("flip-left");
        if (r.top + subRect.height > window.innerHeight) sub.classList.add("flip-up");
      });
    } else if (!item.disabled && item.action) {
      row.addEventListener("click", (ev) => {
        ev.stopPropagation();
        hideContextMenu();
        item.action();
      });
    }
    return row;
  }

  function showContextMenu(items, clientX, clientY) {
    if (!els.contextMenu) return;
    els.contextMenu.innerHTML = "";
    els.contextMenu.classList.remove("flip-up");
    items.forEach((item) => {
      if (item.separator) {
        const hr = document.createElement("div");
        hr.className = "ctx-sep";
        els.contextMenu.appendChild(hr);
        return;
      }
      els.contextMenu.appendChild(buildContextMenuRow(item));
    });
    els.contextMenu.classList.remove("hidden");
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // item 14b: clamp inside the viewport, and flip upward if it would run
    // off the bottom (the menu can be taller than the click point allows)
    const menuRect = els.contextMenu.getBoundingClientRect();
    let left = Math.min(clientX, vw - Math.min(menuRect.width || 220, vw - 8) - 4);
    left = Math.max(4, left);
    let top = clientY;
    if (top + menuRect.height > vh) {
      top = Math.max(4, clientY - menuRect.height);
      els.contextMenu.classList.add("flip-up");
    }
    els.contextMenu.style.left = left + "px";
    els.contextMenu.style.top = top + "px";
  }

  document.addEventListener("click", () => hideContextMenu());
  document.addEventListener("scroll", () => hideContextMenu(), true);
  if (els.contextMenu) els.contextMenu.addEventListener("wheel", (ev) => ev.stopPropagation()); // item 14b

  document.addEventListener("click", () => hideLookGallery());
  document.addEventListener("scroll", () => hideLookGallery(), true);
  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") hideLookGallery();
  });
  if (els.lookGallery) {
    els.lookGallery.addEventListener("click", (ev) => ev.stopPropagation());
    els.lookGallery.addEventListener("wheel", (ev) => ev.stopPropagation());
  }

  app.scene.renderer.domElement.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    if (app.mode !== "edit") {
      // View mode: right-click inspects like a left click (no menu, no editing)
      const hitV = app.scene.pick(ev.clientX, ev.clientY);
      if (hitV) inspect(hitV);
      else clearInspect();
      return;
    }
    hideContextMenu();
    const hit = app.scene.pick(ev.clientX, ev.clientY);
    const FIXTURE_TOOLTIP = "Fixtures come from the QLC+ show — add or remove them in QLC+";
    if (hit) {
      const key = hit.userData.key;
      if (!app.selection.has(key)) selectOnly(key);
      if (key.indexOf("fixture:") === 0) {
        showContextMenu(
          [
            { label: "Duplicate", disabled: true, title: FIXTURE_TOOLTIP },
            { label: "Delete", disabled: true, title: FIXTURE_TOOLTIP },
            { separator: true },
            { label: "Set anchor here", action: () => setAnchorToKey(key) },
            { label: "Reset orientation", action: () => resetFixtureOrientation(key) },
            { label: "Lock", action: () => setLocked(key, true) },
          ],
          ev.clientX,
          ev.clientY
        );
      } else {
        const objId = key.slice(7);
        showContextMenu(
          [
            { label: "Duplicate", action: () => duplicateObjectByKey(key) },
            { label: "Delete", action: () => requestDeleteConfirmation([key]) },
            { separator: true },
            { label: "Set anchor here", action: () => setAnchorToKey(key) },
            { label: "Save as prop…", action: () => saveObjectAsProp(objId) },
            { label: "Lock", action: () => setLocked(key, true) },
          ],
          ev.clientX,
          ev.clientY
        );
      }
    } else {
      const groundPt = app.scene.pickGroundPoint(ev.clientX, ev.clientY);
      showContextMenu([{ label: "Add object ▸", submenu: addObjectSubmenuItems(groundPt) }], ev.clientX, ev.clientY);
    }
  });

  // ---- lists -----------------------------------------------------------

  function listRow(text, key, extraClass) {
    const info = getEntryForKey(key);
    const locked = !!(info && info.entry && info.entry.locked);
    const row = document.createElement("div");
    row.className =
      "list-row" + (app.selection.has(key) ? " selected" : "") + (extraClass ? " " + extraClass : "") + (locked ? " locked" : "");

    const lockBtn = document.createElement("span");
    lockBtn.className = "lock-toggle";
    lockBtn.textContent = locked ? "\u{1F512}" : "\u{1F513}";
    lockBtn.title = locked ? "Unlock" : "Lock";
    lockBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      setLocked(key, !locked);
    });
    row.appendChild(lockBtn);

    const label = document.createElement("span");
    label.className = "list-row-label";
    label.textContent = text;
    row.appendChild(label);

    row.addEventListener("click", (ev) => {
      if (ev.shiftKey) toggleSelect(key);
      else selectOnly(key);
    });
    return row;
  }

  function addressLabel(model) {
    return "U" + (model.universe + 1) + "." + (model.address + 1);
  }

  function refreshLists() {
    els.fixturesList.innerHTML = "";
    els.unplacedList.innerHTML = "";
    els.objectsList.innerHTML = "";

    const draftIds = new Set(Object.keys(app.stage.draft.fixtures).map(Number));
    const rigFixtures = app.rigIndex ? app.rigIndex.list : [];
    const rigIds = new Set(rigFixtures.map((f) => f.id));

    rigFixtures.forEach((model) => {
      const key = "fixture:" + model.id;
      if (draftIds.has(model.id)) {
        const entry = app.stage.draft.fixtures[model.id];
        let cls = "";
        const expectedModel = model.manufacturer + "/" + model.model;
        if (entry.model && entry.model !== expectedModel) cls = "warn";
        els.fixturesList.appendChild(listRow(model.name + " (" + addressLabel(model) + ")", key, cls));
      } else {
        els.unplacedList.appendChild(listRow(model.name + " (" + addressLabel(model) + ")", key));
      }
    });

    // fixtures present in the stage draft but no longer in the rig
    draftIds.forEach((id) => {
      if (!rigIds.has(id)) {
        els.fixturesList.appendChild(listRow("#" + id + " (not in show)", "fixture:" + id, "warn"));
      }
    });

    (app.stage.draft.objects || []).forEach((obj) => {
      els.objectsList.appendChild(listRow(obj.name || obj.prop, "object:" + obj.id));
    });
  }

  // ---- unplaced tray: Place ------------------------------------------

  // double-clicking a row in the Unplaced tray places that fixture
  function wirePlaceOnDoubleClick() {
    els.unplacedList.querySelectorAll(".list-row").forEach((row) => {
      row.title = "Double-click to place";
    });
  }

  els.unplacedList.addEventListener("dblclick", (ev) => {
    const row = ev.target.closest(".list-row");
    if (!row) return;
    const idx = Array.prototype.indexOf.call(els.unplacedList.children, row);
    const unplacedModels = app.rigIndex.list.filter((m) => !app.stage.draft.fixtures[m.id]);
    const model = unplacedModels[idx];
    if (!model) return;
    if (app.mode === "view") return app.showToast("View mode — switch to Edit to place fixtures");
    placeFixture(model);
  });

  function placeFixture(model) {
    let x, y, z;
    const seed = model.monitorSeed;
    if (seed && (seed.x || seed.y)) {
      x = seed.x / 25.4;
      y = seed.y / 25.4;
    } else {
      x = app.stage.draft.room.width / 2;
      y = app.stage.draft.room.depth / 2;
    }
    z = 120; // 10' hung
    app.commitDraftChange(() => {
      app.stage.draft.fixtures[model.id] = {
        model: model.manufacturer + "/" + model.model,
        pos: [round16th(x), round16th(y), z],
        rot: [0, 0, 0],
        hang: "hung",
        invertPan: false,
        invertTilt: false,
        panOffset: 0,
        tiltOffset: 0,
        look: null,
      };
    });
    selectOnly("fixture:" + model.id);
  }

  // ---- anchor ----------------------------------------------------------

  function refreshAnchorFields() {
    const a = app.stage.draft.anchor;
    els.anchorX.value = formatLength(a[0]);
    els.anchorY.value = formatLength(a[1]);
    els.anchorZ.value = formatLength(a[2]);
  }

  function wireAnchorField(input, axis) {
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        refreshAnchorFields();
        input.blur();
      } else if (ev.key === "Enter") {
        input.blur();
      } else if (ev.key === "Tab") {
        input.blur();
      }
    });
    function commit() {
      const current = app.stage.draft.anchor[axis];
      const r = parseLength(input.value, current);
      if (!r.ok) {
        input.classList.add("invalid");
        input.title = r.error;
        return false;
      }
      input.classList.remove("invalid");
      app.commitDraftChange(() => {
        app.stage.draft.anchor[axis] = round16th(r.inches);
      });
      refreshAnchorFields();
      return true;
    }
    input.addEventListener("blur", commit);
  }
  wireAnchorField(els.anchorX, 0);
  wireAnchorField(els.anchorY, 1);
  wireAnchorField(els.anchorZ, 2);

  els.anchorSetBtn.addEventListener("click", () => {
    const keys = selectedKeys();
    if (keys.length !== 1) return;
    setAnchorToKey(keys[0]);
  });

  // ---- room size ---------------------------------------------------

  function refreshRoomFields() {
    const r = app.stage.draft.room;
    if (els.roomWidth) els.roomWidth.value = formatLength(r.width);
    if (els.roomDepth) els.roomDepth.value = formatLength(r.depth);
    if (els.roomHeight) els.roomHeight.value = formatLength(r.height);
  }

  function wireRoomField(input, key) {
    if (!input) return;
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        refreshRoomFields();
        input.blur();
      } else if (ev.key === "Enter" || ev.key === "Tab") input.blur();
    });
    input.addEventListener("blur", () => {
      const current = app.stage.draft.room[key];
      const r = parseLength(input.value, current);
      if (!r.ok) {
        input.classList.add("invalid");
        input.title = r.error;
        return;
      }
      input.classList.remove("invalid");
      app.commitDraftChange(() => {
        app.stage.draft.room[key] = Math.max(round16th(r.inches), 12);
      });
      refreshRoomFields();
    });
  }
  wireRoomField(els.roomWidth, "width");
  wireRoomField(els.roomDepth, "depth");
  wireRoomField(els.roomHeight, "height");

  // ---- properties panel --------------------------------------------

  // item 9: the right panel itself hides when nothing is selected and
  // reappears when something is, resizing the 3D view either way.
  function updateRightPanelVisibility(hasSelection) {
    if (!els.rightPanel) return;
    const shouldHide = !hasSelection;
    if (els.rightPanel.classList.contains("hidden") === shouldHide) return;
    els.rightPanel.classList.toggle("hidden", shouldHide);
    // Properties is a floating overlay now (see stage.css) - it never
    // changes the canvas size, so no scene.resize() here (operator
    // requirement: the view must not shift when it opens/closes).
  }

  function fieldRow(labelText, inputEl) {
    const wrap = document.createElement("label");
    wrap.className = "field-row";
    const span = document.createElement("span");
    span.textContent = labelText;
    wrap.appendChild(span);
    wrap.appendChild(inputEl);
    return wrap;
  }

  function makeNumberInput(getValue, setValue) {
    const input = document.createElement("input");
    input.type = "text";
    input.value = String(getValue());
    input.addEventListener("blur", () => {
      const v = parseFloat(input.value);
      if (isNaN(v)) {
        input.classList.add("invalid");
        return;
      }
      input.classList.remove("invalid");
      app.commitDraftChange(() => setValue(v));
      refreshPanel();
    });
    return input;
  }

  function makeCheckbox(getValue, setValue) {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = !!getValue();
    input.addEventListener("change", () => {
      app.commitDraftChange(() => setValue(input.checked));
    });
    return input;
  }

  function makeSelect(options, getValue, setValue) {
    const select = document.createElement("select");
    select.innerHTML = options.map((o) => `<option value="${o}">${o}</option>`).join("");
    select.value = getValue();
    select.addEventListener("change", () => {
      app.commitDraftChange(() => setValue(select.value));
      refreshPanel();
    });
    return select;
  }

  function makeTextInput(getValue, setValue) {
    const input = document.createElement("input");
    input.type = "text";
    input.value = getValue() || "";
    input.addEventListener("change", () => {
      app.commitDraftChange(() => setValue(input.value));
      refreshLists();
    });
    return input;
  }

  // Applies a length edit to every selected item of the same axis: an
  // absolute value (no leading operator) sets all items to that value; a
  // relative value (+2, -1', *2, /2) is re-applied per item against each
  // item's own current value.
  function applyMultiLength(infos, axis, getAxisValue, setAxisValue, rawText) {
    let anyOk = false;
    infos.forEach((info) => {
      const base = getAxisValue(info.entry);
      const r = parseLength(rawText, base);
      if (r.ok) {
        setAxisValue(info.entry, round16th(r.inches));
        anyOk = true;
      }
    });
    return anyOk;
  }

  // ---- Look picker gallery popover (M4) --------------------------------

  const lookThumbCache = new Map(); // look id -> Promise<dataUrl|null>
  let lookGalleryAnchor = null;

  function hideLookGallery() {
    if (!els.lookGallery || els.lookGallery.classList.contains("hidden")) return;
    els.lookGallery.classList.add("hidden");
    els.lookGallery.innerHTML = "";
    lookGalleryAnchor = null;
  }

  // Clamps the popover inside the viewport, opening below its anchor button
  // (flipping above if it would run off the bottom) - same idea as the
  // context menu's own clamping (item 14b).
  function positionLookGallery(anchorEl) {
    const el = els.lookGallery;
    const rect = anchorEl.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const elRect = el.getBoundingClientRect();
    let left = Math.min(rect.left, vw - elRect.width - 8);
    left = Math.max(8, left);
    let top = rect.bottom + 6;
    if (top + elRect.height > vh) top = Math.max(8, rect.top - elRect.height - 6);
    el.style.left = left + "px";
    el.style.top = top + "px";
  }

  function lookThumbFor(entry) {
    if (!lookThumbCache.has(entry.id)) {
      lookThumbCache.set(
        entry.id,
        renderLookThumbnail(entry).catch(() => null)
      );
    }
    return lookThumbCache.get(entry.id);
  }

  function buildLookGalleryHead(titleText) {
    const head = document.createElement("div");
    head.className = "look-gallery-head";
    const title = document.createElement("strong");
    title.textContent = titleText;
    head.appendChild(title);
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "icon-btn";
    closeBtn.title = "Close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      hideLookGallery();
    });
    head.appendChild(closeBtn);
    return head;
  }

  function buildLookCard(entry, isCurrent, fixtureInfo) {
    const card = document.createElement("div");
    card.className = "look-card" + (isCurrent ? " current" : "");
    const thumb = document.createElement("div");
    thumb.className = "look-thumb";
    const spinner = document.createElement("span");
    spinner.className = "look-thumb-spinner";
    spinner.textContent = "…";
    thumb.appendChild(spinner);
    card.appendChild(thumb);
    lookThumbFor(entry).then((url) => {
      if (!url || !thumb.isConnected) return;
      thumb.innerHTML = "";
      const img = document.createElement("img");
      img.src = url;
      img.alt = entry.name || entry.id;
      thumb.appendChild(img);
    });

    const name = document.createElement("div");
    name.className = "look-card-name";
    name.textContent = entry.name || entry.id;
    card.appendChild(name);

    const metaBits = [];
    if (entry.manufacturer) metaBits.push(entry.manufacturer);
    if (entry.beamAngleDeg != null) metaBits.push(Math.round(entry.beamAngleDeg) + "°");
    const meta = document.createElement("div");
    meta.className = "look-card-meta";
    meta.textContent = metaBits.join(" · ");
    card.appendChild(meta);

    if (lookEntryIsMovable(entry)) {
      const badge = document.createElement("span");
      badge.className = "look-movable-badge";
      badge.textContent = "movable";
      card.appendChild(badge);
    }

    card.addEventListener("click", (ev) => {
      ev.stopPropagation();
      showLookConfirm(entry, fixtureInfo);
    });
    return card;
  }

  function resetLookToDefault(fixtureInfo) {
    const modelKey = fixtureInfo.model && fixtureInfo.model.modelKey;
    app.commitDraftChange(() => {
      if (fixtureInfo.entry) fixtureInfo.entry.look = null;
      if (modelKey && app.stage.draft.models[modelKey]) app.stage.draft.models[modelKey].look = null;
    });
    refreshPanel();
  }

  function showLookConfirm(entry, fixtureInfo) {
    if (!els.lookGallery) return;
    els.lookGallery.innerHTML = "";
    els.lookGallery.appendChild(buildLookGalleryHead(entry.name || entry.id));

    const body = document.createElement("div");
    body.className = "look-gallery-body look-confirm";
    const p = document.createElement("p");
    p.textContent = "Apply this look to:";
    body.appendChild(p);

    const btnRow = document.createElement("div");
    btnRow.className = "btn-row";
    const modelKey = fixtureInfo.model && fixtureInfo.model.modelKey;
    if (modelKey) {
      const allBtn = document.createElement("button");
      allBtn.type = "button";
      allBtn.textContent = "Apply to all " + modelKey + " fixtures";
      allBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        app.commitDraftChange(() => {
          app.stage.draft.models[modelKey] = app.stage.draft.models[modelKey] || {};
          app.stage.draft.models[modelKey].look = entry.id;
        });
        hideLookGallery();
        refreshPanel();
      });
      btnRow.appendChild(allBtn);
    }
    const oneBtn = document.createElement("button");
    oneBtn.type = "button";
    oneBtn.textContent = "This fixture only";
    oneBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (!fixtureInfo.entry) return;
      app.commitDraftChange(() => {
        fixtureInfo.entry.look = entry.id;
      });
      hideLookGallery();
      refreshPanel();
    });
    btnRow.appendChild(oneBtn);
    const backBtn = document.createElement("button");
    backBtn.type = "button";
    backBtn.textContent = "‹ Back";
    backBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openLookGallery(fixtureInfo, lookGalleryAnchor);
    });
    btnRow.appendChild(backBtn);
    body.appendChild(btnRow);
    els.lookGallery.appendChild(body);

    if (lookGalleryAnchor) positionLookGallery(lookGalleryAnchor);
  }

  async function openLookGallery(fixtureInfo, anchorEl) {
    if (!els.lookGallery) return;
    hideContextMenu();
    lookGalleryAnchor = anchorEl || lookGalleryAnchor;
    els.lookGallery.innerHTML = "";
    els.lookGallery.classList.remove("hidden");
    els.lookGallery.appendChild(buildLookGalleryHead("Choose a look"));

    const body = document.createElement("div");
    body.className = "look-gallery-body";
    els.lookGallery.appendChild(body);
    if (lookGalleryAnchor) positionLookGallery(lookGalleryAnchor);

    let looks = [];
    try {
      const index = await loadLookIndex();
      looks = (index && index.looks) || [];
    } catch (e) {
      looks = [];
    }

    if (!looks.some((l) => l.path)) {
      const note = document.createElement("div");
      note.className = "look-gallery-note";
      note.textContent = "Real fixture models appear here after downloading from GDTF Share.";
      els.lookGallery.insertBefore(note, body);
    }

    const currentLookInfo = app.scene.getFixtureLookInfo && app.scene.getFixtureLookInfo(fixtureInfo.id);
    const currentId = currentLookInfo && currentLookInfo.id;

    const movable = looks.filter(lookEntryIsMovable);
    const builtinRest = looks.filter((l) => !lookEntryIsMovable(l) && String(l.id).indexOf("builtin/") === 0);
    const otherByCategory = new Map();
    looks.forEach((l) => {
      if (lookEntryIsMovable(l) || String(l.id).indexOf("builtin/") === 0) return;
      const cat = l.category || "Other";
      if (!otherByCategory.has(cat)) otherByCategory.set(cat, []);
      otherByCategory.get(cat).push(l);
    });

    function addGroup(titleText, list) {
      if (!list.length) return;
      const h = document.createElement("div");
      h.className = "look-group-title";
      h.textContent = titleText;
      body.appendChild(h);
      const grid = document.createElement("div");
      grid.className = "look-grid";
      body.appendChild(grid);
      list.forEach((entry) => grid.appendChild(buildLookCard(entry, entry.id === currentId, fixtureInfo)));
    }

    addGroup("Moving (spot/beam)", movable);
    addGroup("Built-in (QLC+)", builtinRest);
    Array.from(otherByCategory.keys())
      .sort()
      .forEach((cat) => addGroup(cat, otherByCategory.get(cat)));

    if (!looks.length) {
      const empty = document.createElement("div");
      empty.className = "hint";
      empty.textContent = "No looks available yet.";
      body.appendChild(empty);
    }

    const resetBtn = document.createElement("button");
    resetBtn.type = "button";
    resetBtn.textContent = "Reset to default";
    resetBtn.style.width = "100%";
    resetBtn.style.marginTop = "8px";
    resetBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      resetLookToDefault(fixtureInfo);
      hideLookGallery();
    });
    body.appendChild(resetBtn);

    if (lookGalleryAnchor) positionLookGallery(lookGalleryAnchor);
  }

  function refreshPanel() {
    const keys = app.mode === "view" ? [] : selectedKeys();
    updateRightPanelVisibility(keys.length > 0);
    els.propsPanel.innerHTML = "";
    if (keys.length === 0) {
      els.noSelection.style.display = "";
      els.propsPanel.style.display = "none";
      return;
    }
    els.noSelection.style.display = "none";
    els.propsPanel.style.display = "";

    const infos = keys.map(getEntryForKey).filter((i) => i && i.entry);
    if (infos.length === 0) return;
    const multi = infos.length > 1;
    const allFixtures = infos.every((i) => i.type === "fixture");
    const allObjects = infos.every((i) => i.type === "object");
    const anchor = app.stage.draft.anchor;

    function multiOrSingleLength(labelText, axis) {
      const first = infos[0].entry;
      const live = liveManipValueFor(infos[0]);
      const basePos = (live && live.pos) || first.pos;
      const shown = basePos[axis] - anchor[axis];
      const input = document.createElement("input");
      input.type = "text";
      input.value = formatLength(shown);
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") {
          input.value = formatLength(shown);
          input.blur();
        } else if (ev.key === "Enter" || ev.key === "Tab") input.blur();
      });
      input.addEventListener("blur", () => {
        const isRelative = /^[+\-*/]/.test(input.value.trim());
        let ok;
        app.commitDraftChange(() => {
          if (multi && !isRelative) {
            // absolute: sets all items to the same world position (anchor + value)
            const r = parseLength(input.value, first.pos[axis] - anchor[axis]);
            if (!r.ok) {
              ok = false;
            } else {
              infos.forEach((info) => (info.entry.pos[axis] = round16th(anchor[axis] + r.inches)));
              ok = true;
            }
          } else {
            ok = applyMultiLength(
              infos,
              axis,
              (entry) => entry.pos[axis] - anchor[axis],
              (entry, v) => (entry.pos[axis] = round16th(anchor[axis] + v)),
              input.value
            );
          }
        });
        if (!ok) {
          input.classList.add("invalid");
          return;
        }
        input.classList.remove("invalid");
        refreshPanel();
      });
      return fieldRow(labelText, input);
    }

    els.propsPanel.appendChild(multiOrSingleLength("X across", 0));
    els.propsPanel.appendChild(multiOrSingleLength("Y depth", 1));
    els.propsPanel.appendChild(multiOrSingleLength("Z height", 2));

    // item 11: full rotation. Fixtures show Yaw/Pitch/Roll (rot[2]/[1]/[0]);
    // objects (and mixed selections) show generic Rotate X/Y/Z, stored as
    // objects[i].rot=[rx,ry,rz] (legacy objects[i].rz read as [0,0,rz] and
    // kept in sync on write). Accepts relative math (+15, *2); in a
    // multi-select, a relative entry re-applies per item.
    function rotationField(labelText, axisIndex) {
      const firstEntry = infos[0].entry;
      const live = liveManipValueFor(infos[0]);
      const liveYaw = axisIndex === 2 && live && live.yaw !== undefined ? live.yaw : null;
      const shownDeg = liveYaw !== null ? liveYaw : getRot(firstEntry)[axisIndex];
      const input = document.createElement("input");
      input.type = "text";
      input.value = formatDeg(shownDeg);
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") {
          input.value = formatDeg(shownDeg);
          input.blur();
        } else if (ev.key === "Enter" || ev.key === "Tab") input.blur();
      });
      input.addEventListener("blur", () => {
        const raw = input.value.trim();
        const isRelative = /^[+\-*/]/.test(raw);
        let ok = true;
        app.commitDraftChange(() => {
          if (multi && !isRelative) {
            const r = parseLength(raw, getRot(firstEntry)[axisIndex]);
            if (!r.ok) {
              ok = false;
              return;
            }
            infos.forEach((info) => setRotAxis(info.entry, axisIndex, r.inches, info.type === "object"));
          } else {
            infos.forEach((info) => {
              const base = getRot(info.entry)[axisIndex];
              const r = parseLength(raw, base);
              if (r.ok) setRotAxis(info.entry, axisIndex, r.inches, info.type === "object");
              else ok = false;
            });
          }
        });
        if (!ok) {
          input.classList.add("invalid");
          return;
        }
        input.classList.remove("invalid");
        refreshPanel();
      });
      return fieldRow(labelText, input);
    }

    if (allFixtures) {
      els.propsPanel.appendChild(rotationField("Yaw (deg)", 2));
      els.propsPanel.appendChild(rotationField("Pitch (deg)", 1));
      els.propsPanel.appendChild(rotationField("Roll (deg)", 0));
    } else {
      els.propsPanel.appendChild(rotationField("Rotate X (deg)", 0));
      els.propsPanel.appendChild(rotationField("Rotate Y (deg)", 1));
      els.propsPanel.appendChild(rotationField("Rotate Z (deg)", 2));
    }

    if (allFixtures) {
      const first = infos[0].entry;
      const hangSelect = makeSelect(["floor", "hung", "wall"], () => first.hang, (v) => infos.forEach((i) => (i.entry.hang = v)));
      els.propsPanel.appendChild(fieldRow("Hang", hangSelect));

      const invertPan = makeCheckbox(() => first.invertPan, (v) => infos.forEach((i) => (i.entry.invertPan = v)));
      els.propsPanel.appendChild(fieldRow("Invert pan", invertPan));
      const invertTilt = makeCheckbox(() => first.invertTilt, (v) => infos.forEach((i) => (i.entry.invertTilt = v)));
      els.propsPanel.appendChild(fieldRow("Invert tilt", invertTilt));

      const panOffset = makeNumberInput(() => first.panOffset || 0, (v) => infos.forEach((i) => (i.entry.panOffset = v)));
      els.propsPanel.appendChild(fieldRow("Pan offset (deg)", panOffset));
      const tiltOffset = makeNumberInput(() => first.tiltOffset || 0, (v) => infos.forEach((i) => (i.entry.tiltOffset = v)));
      els.propsPanel.appendChild(fieldRow("Tilt offset (deg)", tiltOffset));

      // M4: the 3D "look" (real GDTF-derived or built-in QLC+ model) - only
      // shown/editable for a single selected fixture (the gallery's "apply
      // to all"/"this fixture only" choice needs one concrete fixture+model
      // to act on).
      if (!multi && els.lookGallery) {
        const fixtureInfo = infos[0];
        const lookInfo = (app.scene.getFixtureLookInfo && app.scene.getFixtureLookInfo(fixtureInfo.id)) || null;
        const row = document.createElement("div");
        row.className = "look-row-current";
        const nameSpan = document.createElement("span");
        nameSpan.className = "look-name";
        nameSpan.textContent = !lookInfo || !lookInfo.id
          ? "Procedural (no look)"
          : (lookInfo.name || lookInfo.id) + (lookInfo.loading ? " (loading…)" : "");
        row.appendChild(nameSpan);
        const changeBtn = document.createElement("button");
        changeBtn.type = "button";
        changeBtn.textContent = "Change…";
        changeBtn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          openLookGallery(fixtureInfo, changeBtn);
        });
        row.appendChild(changeBtn);
        els.propsPanel.appendChild(fieldRow("Look", row));
      }

      // Beam look is per MODEL (applies to every fixture sharing it), stored
      // in stage.draft.models[manufacturer/model] = {beamStart, beamSpread}.
      const modelKeys = Array.from(new Set(infos.map((i) => i.model && i.model.modelKey).filter(Boolean)));
      if (modelKeys.length) {
        const firstModel = infos[0].model;
        const startInput = makeLengthInputStandalone(firstModel.beamStart, (newInches) => {
          modelKeys.forEach((mk) => {
            app.stage.draft.models[mk] = app.stage.draft.models[mk] || {};
            app.stage.draft.models[mk].beamStart = newInches;
          });
        });
        els.propsPanel.appendChild(fieldRow("Beam start width", startInput));

        const spreadInput = makeNumberInput(
          () => firstModel.beamSpread,
          (v) => {
            modelKeys.forEach((mk) => {
              app.stage.draft.models[mk] = app.stage.draft.models[mk] || {};
              app.stage.draft.models[mk].beamSpread = v;
            });
          }
        );
        els.propsPanel.appendChild(fieldRow("Beam spread (deg)", spreadInput));

        // display size of the fixture body, in % (models[key].bodyScale)
        const sizeInput = makeNumberInput(
          () => {
            const info = app.scene.getFixtureLookInfo && app.scene.getFixtureLookInfo(firstModel.id);
            return Math.round(((info && info.bodyScale) || 1) * 100);
          },
          (v) => {
            const scale = Math.min(Math.max(v, 5), 400) / 100;
            modelKeys.forEach((mk) => {
              app.stage.draft.models[mk] = app.stage.draft.models[mk] || {};
              app.stage.draft.models[mk].bodyScale = scale;
            });
          }
        );
        els.propsPanel.appendChild(fieldRow("Fixture size (%)", sizeInput));

        const note = document.createElement("div");
        note.className = "hint";
        note.textContent = modelKeys.length > 1 ? "Applies to every fixture of each selected model." : "Applies to every fixture of this model.";
        els.propsPanel.appendChild(note);
      }
    }

    if (allObjects && !multi) {
      const entry = infos[0].entry;
      const nameInput = makeTextInput(() => entry.name, (v) => (entry.name = v));
      els.propsPanel.appendChild(fieldRow("Name", nameInput));
      const catInput = makeTextInput(() => entry.category, (v) => (entry.category = v));
      els.propsPanel.appendChild(fieldRow("Category", catInput));
      const aliasInput = makeTextInput(
        () => (entry.aliases || []).join(", "),
        (v) =>
          (entry.aliases = v
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean))
      );
      els.propsPanel.appendChild(fieldRow("Aliases", aliasInput));

      // W/D/H: edits scale relative to the prop definition's own bounding box
      const def = app.propDefLookup(entry);
      if (def) {
        const box = propBoundingBoxInches(def);
        const live = liveManipValueFor(infos[0]);
        const liveScale = live && live.scale;
        ["W", "D", "H"].forEach((axisLabel, i) => {
          const baseSize = [box.w, box.d, box.h][i];
          const scaleArr = liveScale || entry.scale || [1, 1, 1];
          const currentSize = baseSize * (scaleArr[i] || 1);
          const input = makeLengthInputStandalone(currentSize, (newInches) => {
            entry.scale = entry.scale || [1, 1, 1];
            entry.scale[i] = baseSize > 0 ? newInches / baseSize : 1;
          });
          els.propsPanel.appendChild(fieldRow(axisLabel, input));
        });
      }

      appendInstanceOverridesSection(entry, def);
      appendLookSection(entry, def);
    }

    // locked items can be inspected (read-only) but not edited from here
    if (infos.some((i) => i.entry.locked)) {
      els.propsPanel.querySelectorAll("input, select").forEach((el) => (el.disabled = true));
      const note = document.createElement("div");
      note.className = "hint";
      note.textContent = "Locked - unlock to edit.";
      els.propsPanel.appendChild(note);
    }
  }

  // ---- Instance overrides (goal 6, overnight build) ----
  // Contract (.claude/memory/stage-visualizer.md "Prop instance overrides"):
  // objects[i].overrides = {color?, partColors?, hiddenFaces?, visible?} -
  // written here, on the OBJECT INSTANCE only, never on the prop
  // definition itself. stage-props.js's buildPropGroup/applyOverrides (P)
  // and stage-scene.js's rebuildObjects (R) both read this shape.
  function appendInstanceOverridesSection(entry, def) {
    const header = document.createElement("div");
    header.className = "settings-row";
    header.innerHTML = "<strong>Instance</strong>";
    els.propsPanel.appendChild(header);

    function patchOverrides(fn) {
      app.commitDraftChange(() => {
        entry.overrides = entry.overrides || {};
        fn(entry.overrides);
        if (Object.keys(entry.overrides).length === 0) delete entry.overrides;
      });
      refreshPanel();
    }

    const ov = entry.overrides || {};

    const colorInput = document.createElement("input");
    colorInput.type = "color";
    colorInput.value = ov.color || entry.color || "#8b5a2b";
    colorInput.addEventListener("change", () => {
      patchOverrides((o) => {
        o.color = colorInput.value;
      });
    });
    els.propsPanel.appendChild(fieldRow("Colour override", colorInput));

    if (def && Array.isArray(def.parts) && def.parts.length > 1) {
      def.parts.forEach((part) => {
        const partInput = document.createElement("input");
        partInput.type = "color";
        partInput.value = (ov.partColors && ov.partColors[part.id]) || part.color || "#8b5a2b";
        partInput.title = "Colour override for this part only";
        partInput.addEventListener("change", () => {
          patchOverrides((o) => {
            o.partColors = o.partColors || {};
            o.partColors[part.id] = partInput.value;
          });
        });
        els.propsPanel.appendChild(fieldRow("  " + (part.id || part.shape), partInput));
      });
    }

    const hideInput = document.createElement("input");
    hideInput.type = "checkbox";
    hideInput.checked = ov.visible === false;
    hideInput.addEventListener("change", () => {
      patchOverrides((o) => {
        if (hideInput.checked) o.visible = false;
        else delete o.visible;
      });
    });
    els.propsPanel.appendChild(fieldRow("Hide", hideInput));

    const resetBtn = document.createElement("button");
    resetBtn.textContent = "Reset overrides";
    resetBtn.type = "button";
    resetBtn.addEventListener("click", () => {
      app.commitDraftChange(() => {
        delete entry.overrides;
        delete entry.color;
      });
      refreshPanel();
    });
    els.propsPanel.appendChild(resetBtn);

    const editPropBtn = document.createElement("button");
    editPropBtn.textContent = "Edit prop…";
    editPropBtn.type = "button";
    editPropBtn.title = "Changes every copy of this prop";
    editPropBtn.addEventListener("click", async () => {
      try {
        const mod = await import("./stage-propeditor.js");
        if (mod && mod.openPropEditor) mod.openPropEditor(app, entry.prop);
        else app.showToast("Prop editor not available yet");
      } catch (e) {
        app.showToast("Prop editor not available yet");
      }
    });
    els.propsPanel.appendChild(editPropBtn);
    const editNote = document.createElement("div");
    editNote.className = "hint";
    editNote.textContent = "Changes every copy of this prop.";
    els.propsPanel.appendChild(editNote);
  }

  // ---- "Look" (texture library / picker / filters pass) ----
  // Extends the overrides contract (see .claude/memory/stage-visualizer.md):
  //   objects[i].overrides.filters = FilterSet   (object-wide, composed OVER
  //     every part's own + per-part-look filters - "object-over-part")
  //   objects[i].overrides.partLooks = {partId: {texture?, tileIn?, rot?, filters?}}
  // Never touches the prop definition. Hover in the texture picker previews
  // LIVE on the real object by mutating app.stage.draft directly and calling
  // app.rebuildSceneFromDraft() (no history push, no dirty flag) - Cancel/Esc
  // restores the pre-open value the same way; OK restores it too and THEN
  // runs the real change through app.commitDraftChange, so undo sees one
  // clean transition instead of the hover scrubbing.
  function appendLookSection(entry, def) {
    const header = document.createElement("div");
    header.className = "settings-row";
    header.innerHTML = "<strong>Look</strong>";
    els.propsPanel.appendChild(header);

    function patchOverrides(fn) {
      app.commitDraftChange(() => {
        entry.overrides = entry.overrides || {};
        fn(entry.overrides);
        if (Object.keys(entry.overrides).length === 0) delete entry.overrides;
      });
      refreshPanel();
    }

    const ov = entry.overrides || {};

    const filtersHint = document.createElement("div");
    filtersHint.className = "hint";
    filtersHint.textContent = "Object filters (on top of each part's own):";
    els.propsPanel.appendChild(filtersHint);
    els.propsPanel.appendChild(
      buildFilterControls(ov.filters, (key, value) => {
        patchOverrides((o) => {
          if (key === "__reset__") {
            delete o.filters;
            return;
          }
          o.filters = o.filters || {};
          o.filters[key] = value;
        });
      })
    );

    const supportsTexture = (p) => p.shape !== "truss" && p.shape !== "ledstrip" && p.shape !== "model";
    const parts = def && Array.isArray(def.parts) ? def.parts.filter(supportsTexture) : [];
    if (!parts.length) return;

    const partsHint = document.createElement("div");
    partsHint.className = "hint";
    partsHint.textContent = "Per-part texture override:";
    els.propsPanel.appendChild(partsHint);

    parts.forEach((part) => {
      const partLooks = ov.partLooks || {};
      const partLook = partLooks[part.id] || null;
      const hasTextureOverride = partLook && Object.prototype.hasOwnProperty.call(partLook, "texture");
      const effectiveTexture = hasTextureOverride ? partLook.texture : part.texture;

      const row = document.createElement("div");
      row.className = "pe-texture-swatch";
      const thumbWrap = document.createElement("div");
      thumbWrap.className = "pe-texture-thumb";
      const nameSpan = document.createElement("div");
      nameSpan.className = "pe-texture-name";
      nameSpan.textContent = (part.id || part.shape) + ": " + (effectiveTexture || "No texture");
      row.appendChild(thumbWrap);
      row.appendChild(nameSpan);
      els.propsPanel.appendChild(row);
      if (effectiveTexture) {
        getTextureEntry(effectiveTexture).then((tentry) => {
          if (!tentry) return;
          nameSpan.textContent = (part.id || part.shape) + ": " + (tentry.name || effectiveTexture);
          thumbWrap.innerHTML = "";
          thumbWrap.appendChild(buildThumbElement(tentry, 34));
        });
      }

      const btnRow = document.createElement("div");
      btnRow.className = "pe-btn-row";
      const changeBtn = document.createElement("button");
      changeBtn.type = "button";
      changeBtn.className = "pe-btn";
      changeBtn.textContent = "Change…";
      btnRow.appendChild(changeBtn);
      if (hasTextureOverride) {
        const useDefBtn = document.createElement("button");
        useDefBtn.type = "button";
        useDefBtn.className = "pe-btn";
        useDefBtn.textContent = "Use prop's";
        useDefBtn.addEventListener("click", () => {
          patchOverrides((o) => {
            if (!o.partLooks || !o.partLooks[part.id]) return;
            delete o.partLooks[part.id].texture;
            if (Object.keys(o.partLooks[part.id]).length === 0) delete o.partLooks[part.id];
            if (o.partLooks && Object.keys(o.partLooks).length === 0) delete o.partLooks;
          });
        });
        btnRow.appendChild(useDefBtn);
      }
      els.propsPanel.appendChild(btnRow);

      changeBtn.addEventListener("click", () => {
        const original = effectiveTexture || null;
        const preview = (slug) => {
          entry.overrides = entry.overrides || {};
          entry.overrides.partLooks = entry.overrides.partLooks || {};
          entry.overrides.partLooks[part.id] = Object.assign({}, entry.overrides.partLooks[part.id], { texture: slug });
          app.rebuildSceneFromDraft();
        };
        const restore = () => {
          if (!entry.overrides || !entry.overrides.partLooks || !entry.overrides.partLooks[part.id]) return;
          if (hasTextureOverride) entry.overrides.partLooks[part.id].texture = original;
          else delete entry.overrides.partLooks[part.id].texture;
          if (Object.keys(entry.overrides.partLooks[part.id]).length === 0) delete entry.overrides.partLooks[part.id];
        };
        openTexturePicker({
          currentSlug: original,
          onHover: preview,
          onPick: (slug) => {
            restore();
            patchOverrides((o) => {
              o.partLooks = o.partLooks || {};
              o.partLooks[part.id] = Object.assign({}, o.partLooks[part.id], { texture: slug });
            });
          },
          onCancel: () => {
            restore();
            app.rebuildSceneFromDraft();
          },
        });
      });

      const details = document.createElement("details");
      details.className = "hint";
      const summary = document.createElement("summary");
      summary.textContent = "Part filters" + (partLook && partLook.filters ? " (overridden)" : "");
      details.appendChild(summary);
      const filtersHolder = document.createElement("div");
      details.appendChild(filtersHolder);
      filtersHolder.appendChild(
        buildFilterControls(partLook && partLook.filters, (key, value) => {
          patchOverrides((o) => {
            o.partLooks = o.partLooks || {};
            o.partLooks[part.id] = o.partLooks[part.id] || {};
            if (key === "__reset__") {
              delete o.partLooks[part.id].filters;
            } else {
              o.partLooks[part.id].filters = o.partLooks[part.id].filters || {};
              o.partLooks[part.id].filters[key] = value;
            }
            if (Object.keys(o.partLooks[part.id]).length === 0) delete o.partLooks[part.id];
            if (o.partLooks && Object.keys(o.partLooks).length === 0) delete o.partLooks;
          });
        })
      );
      els.propsPanel.appendChild(details);
    });
  }

  function makeLengthInputStandalone(initialInches, onCommit) {
    const input = document.createElement("input");
    input.type = "text";
    input.value = formatLength(initialInches);
    input.addEventListener("blur", () => {
      const r = parseLength(input.value, initialInches);
      if (!r.ok) {
        input.classList.add("invalid");
        return;
      }
      input.classList.remove("invalid");
      app.commitDraftChange(() => onCommit(r.inches));
      refreshPanel();
    });
    return input;
  }

  function propBoundingBoxInches(def) {
    let minX = 0, maxX = 0, minY = 0, maxY = 0, minZ = 0, maxZ = 0;
    (def.parts || []).forEach((p) => {
      const [w, d, h] = p.size || [0, 0, 0];
      const [x, y, z] = p.pos || [0, 0, 0];
      minX = Math.min(minX, x - w / 2);
      maxX = Math.max(maxX, x + w / 2);
      minY = Math.min(minY, y - d / 2);
      maxY = Math.max(maxY, y + d / 2);
      minZ = Math.min(minZ, z - h / 2);
      maxZ = Math.max(maxZ, z + h / 2);
    });
    return { w: maxX - minX || 1, d: maxY - minY || 1, h: maxZ - minZ || 1 };
  }

  // ---- distribute --------------------------------------------------

  els.distBtn.addEventListener("click", () => {
    const keys = selectedKeys();
    const infos = keys.map(getEntryForKey).filter((i) => i && i.entry && !i.entry.locked);
    if (infos.length < 2) return;
    const axis = els.distAxis.value === "y" ? 1 : 0;
    const r = parseLength(els.distSpacing.value, undefined);
    if (!r.ok) {
      els.distSpacing.classList.add("invalid");
      return;
    }
    els.distSpacing.classList.remove("invalid");
    app.commitDraftChange(() => {
      infos.sort((a, b) => a.entry.pos[axis] - b.entry.pos[axis]);
      const start = infos[0].entry.pos[axis];
      infos.forEach((info, i) => {
        info.entry.pos[axis] = round16th(start + i * r.inches);
      });
    });
    refreshPanel();
  });

  // ---- public refresh --------------------------------------------------

  function refreshAll() {
    refreshLists();
    wirePlaceOnDoubleClick();
    refreshAnchorFields();
    refreshRoomFields();
    refreshPanel();
    updateGizmoAttachment();
  }

  app.editor = {
    refreshAll: refreshAll,
    placeFixture: placeFixture,
    gizmoMode: "translate",
    duplicateObject: (id) => duplicateObjectByKey("object:" + id),
    deleteObject: (id) => deleteObjectsByKeys(["object:" + id]),
    setLocked: (kind, id, locked) => setLocked(kind + ":" + id, locked),
    // test hook (window.__stage.select): selects by kind+id like a real
    // click would, so CDP-driven tests can drive the gizmo/manipulation
    // paths without needing pixel-perfect canvas coordinates.
    select: (kind, id) => {
      selectOnly(kind + ":" + id);
      return true;
    },
    // stage-app.js's camera-controls glue asks this before letting WASD/
    // mouselook run, so a Maya-modifier drag or an in-progress gizmo drag
    // never fights the camera for the same pointer/keys.
    isManipulating: () => manipulating || !!activeModifierKey,
    _debug: () => ({ manipulating, manipMode, activeModifier, activeModifierKey, dragIsManipulate, downWasGizmo, transformDragging: app.scene.transform.dragging, transformEnabled: app.scene.transform.enabled, transformMode: app.scene.transform.mode }),
  };
  refreshAll();
}
