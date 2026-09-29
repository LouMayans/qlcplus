/*
  stage-propeditor.js

  Full-screen prop editor: its own three.js renderer/scene (a neutral studio
  backdrop, not the club scene) for authoring one prop *definition* at a
  time - parts list, add/duplicate/mirror/delete, a TransformControls
  gizmo + numeric ft-in fields for move/rotate/scale, colour/material,
  hidden faces / visible-from, its own undo/redo, and Save/Cancel.

  Saving writes the prop straight into app.props.draft.props[id] and calls
  app.savePropsLibrary() (same round-trip the old in-panel builder used),
  then app.rebuildSceneFromDraft() so every placed instance updates
  immediately, and app.props.onChanged() so the library list refreshes.

  export function openPropEditor(app, propId|null) - propId null starts a
  blank prop (only added to the library on Save). U's "Edit prop..."
  button calls this directly; stage-props.js's own library-list rows call
  it too via a dynamic import (see initPropsMode in that file).
*/

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { parseLength, formatLength } from "./stage-units.js";
import {
  IN,
  ALL_PART_SHAPES,
  MATERIALS,
  SIDE_OPTIONS,
  CATEGORIES,
  FACE_LABELS,
  TRUSS_TYPES,
  newBlankProp,
  addPart,
  duplicatePart,
  mirrorPart,
  deletePart,
  createPartMesh,
  loadPropModelIndex,
  decomposeLocalQuaternion,
} from "./stage-props.js";
import { getTextureEntry, buildThumbElement, openTexturePicker, buildFilterControls } from "./stage-textures.js";

let dom = null;

const session = {
  app: null,
  originalId: null,
  propDef: null,
  selectedPartId: null,
  undoStack: [],
  redoStack: [],
  dirty: false,
  gizmoMode: "translate",
  justDragged: false,
  dragBeforeJSON: null,
  scene: null,
  camera: null,
  renderer: null,
  orbit: null,
  transformControls: null,
  raycaster: null,
  partsGroup: null,
  partObjects: new Map(),
  rotateInputs: null, // {x,y,z} <input> elements for the selected part's Rotate X/Y/Z fields
  viewportWrap: null,
  rafId: null,
};

// -------------------------------------------------------------- public --

export function openPropEditor(app, propId) {
  ensureDom();
  ensureThree();
  loadSession(app, propId);
  dom.overlay.classList.remove("hidden");
  document.addEventListener("keydown", onKeyDown);
  refreshTopFields();
  rebuildPartsGroup();
  frameCamera();
  refreshPartsList();
  refreshFieldsPanel();
  updateStatus("");
  if (!session.rafId) animate();
  requestAnimationFrame(resizeViewport);
}

// ---------------------------------------------------------------- dom ---

function ensureDom() {
  if (dom) return;

  const overlay = document.createElement("div");
  overlay.id = "prop-editor-overlay";
  overlay.className = "hidden";

  const topbar = document.createElement("div");
  topbar.className = "pe-topbar";
  const h2 = document.createElement("h2");
  h2.textContent = "Prop editor";
  topbar.appendChild(h2);

  const nameLabel = document.createElement("label");
  nameLabel.textContent = "Name";
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameLabel.appendChild(nameInput);
  topbar.appendChild(nameLabel);

  const catLabel = document.createElement("label");
  catLabel.textContent = "Category";
  const categorySelect = document.createElement("select");
  categorySelect.innerHTML = CATEGORIES.map((c) => `<option value="${c}">${c}</option>`).join("");
  catLabel.appendChild(categorySelect);
  topbar.appendChild(catLabel);

  const aliasLabel = document.createElement("label");
  aliasLabel.textContent = "Aliases";
  const aliasesInput = document.createElement("input");
  aliasesInput.type = "text";
  aliasesInput.placeholder = "comma, separated";
  aliasLabel.appendChild(aliasesInput);
  topbar.appendChild(aliasLabel);

  const spacer = document.createElement("div");
  spacer.className = "pe-spacer";
  topbar.appendChild(spacer);

  const status = document.createElement("div");
  status.className = "pe-status";
  topbar.appendChild(status);

  const cancelBtn = document.createElement("button");
  cancelBtn.textContent = "Cancel";
  topbar.appendChild(cancelBtn);

  const saveBtn = document.createElement("button");
  saveBtn.textContent = "Save";
  saveBtn.className = "primary";
  topbar.appendChild(saveBtn);

  const closeBtn = document.createElement("button");
  closeBtn.textContent = "✕";
  closeBtn.title = "Close (Esc)";
  topbar.appendChild(closeBtn);

  const body = document.createElement("div");
  body.className = "pe-body";

  const leftPanel = document.createElement("div");
  leftPanel.className = "pe-panel pe-left";

  const toolsTitle = document.createElement("div");
  toolsTitle.className = "pe-section-title";
  toolsTitle.textContent = "Tools";
  leftPanel.appendChild(toolsTitle);

  const toolRow = document.createElement("div");
  toolRow.className = "pe-btn-row";
  const moveBtn = document.createElement("button");
  moveBtn.className = "pe-btn primary";
  moveBtn.textContent = "Move (W)";
  const rotateBtn = document.createElement("button");
  rotateBtn.className = "pe-btn";
  rotateBtn.textContent = "Rotate (E)";
  const scaleBtn = document.createElement("button");
  scaleBtn.className = "pe-btn";
  scaleBtn.textContent = "Scale (R)";
  toolRow.appendChild(moveBtn);
  toolRow.appendChild(rotateBtn);
  toolRow.appendChild(scaleBtn);
  leftPanel.appendChild(toolRow);

  const snapLabel = document.createElement("label");
  snapLabel.textContent = "Snap";
  const snapSelect = document.createElement("select");
  snapSelect.innerHTML = ['<option value="0">Off</option>', '<option value="1">1"</option>', '<option value="3">3"</option>', '<option value="6">6"</option>', '<option value="12">12"</option>'].join(
    ""
  );
  snapLabel.appendChild(snapSelect);
  leftPanel.appendChild(snapLabel);

  const undoRow = document.createElement("div");
  undoRow.className = "pe-btn-row";
  const undoBtn = document.createElement("button");
  undoBtn.className = "pe-btn";
  undoBtn.textContent = "Undo (Ctrl+Z)";
  const redoBtn = document.createElement("button");
  redoBtn.className = "pe-btn";
  redoBtn.textContent = "Redo (Ctrl+Y)";
  undoRow.appendChild(undoBtn);
  undoRow.appendChild(redoBtn);
  leftPanel.appendChild(undoRow);

  const partsTitle = document.createElement("div");
  partsTitle.className = "pe-section-title";
  partsTitle.textContent = "Parts";
  leftPanel.appendChild(partsTitle);

  const addRow = document.createElement("div");
  addRow.className = "pe-add-row";
  const addPartSelect = document.createElement("select");
  addPartSelect.innerHTML = ALL_PART_SHAPES.map((s) => `<option value="${s}">${s}</option>`).join("");
  const addPartBtn = document.createElement("button");
  addPartBtn.className = "pe-btn";
  addPartBtn.textContent = "Add part";
  addRow.appendChild(addPartSelect);
  addRow.appendChild(addPartBtn);
  leftPanel.appendChild(addRow);

  const partsList = document.createElement("div");
  partsList.className = "pe-list";
  leftPanel.appendChild(partsList);

  const viewportWrapEl = document.createElement("div");
  viewportWrapEl.className = "pe-viewport-wrap";
  const hint = document.createElement("div");
  hint.className = "pe-viewport-hint";
  hint.textContent = "Left-drag orbit · right-drag pan · wheel zoom · click a part to select";
  viewportWrapEl.appendChild(hint);

  const rightPanel = document.createElement("div");
  rightPanel.className = "pe-panel pe-right";

  body.appendChild(leftPanel);
  body.appendChild(viewportWrapEl);
  body.appendChild(rightPanel);

  overlay.appendChild(topbar);
  overlay.appendChild(body);
  document.body.appendChild(overlay);

  dom = {
    overlay,
    nameInput,
    categorySelect,
    aliasesInput,
    status,
    cancelBtn,
    saveBtn,
    closeBtn,
    leftPanel,
    rightPanel,
    viewportWrap: viewportWrapEl,
    moveBtn,
    rotateBtn,
    scaleBtn,
    snapSelect,
    undoBtn,
    redoBtn,
    addPartSelect,
    addPartBtn,
    partsList,
  };

  nameInput.addEventListener("change", () => commitChange(() => (session.propDef.name = nameInput.value)));
  categorySelect.addEventListener("change", () => commitChange(() => (session.propDef.category = categorySelect.value)));
  aliasesInput.addEventListener("change", () =>
    commitChange(() => {
      session.propDef.aliases = aliasesInput.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    })
  );

  cancelBtn.addEventListener("click", requestClose);
  closeBtn.addEventListener("click", requestClose);
  saveBtn.addEventListener("click", saveAndClose);

  moveBtn.addEventListener("click", () => setGizmoMode("translate"));
  rotateBtn.addEventListener("click", () => setGizmoMode("rotate"));
  scaleBtn.addEventListener("click", () => setGizmoMode("scale"));
  undoBtn.addEventListener("click", undo);
  redoBtn.addEventListener("click", redo);

  snapSelect.addEventListener("change", () => {
    const v = parseFloat(snapSelect.value);
    if (!session.transformControls) return;
    session.transformControls.setTranslationSnap(v > 0 ? v * IN : null);
    session.transformControls.setRotationSnap(v > 0 ? THREE.MathUtils.degToRad(15) : null);
  });

  addPartBtn.addEventListener("click", () => {
    const shape = addPartSelect.value;
    let newId = null;
    commitChange(() => {
      const p = addPart(session.propDef, shape);
      newId = p.id;
    });
    if (newId) selectPart(newId);
  });
}

// -------------------------------------------------------------- three ---

function ensureThree() {
  if (session.renderer) return;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x2a2c33); // neutral studio grey, not the club
  const grid = new THREE.GridHelper(6, 24, 0x5a6070, 0x3a3d46);
  scene.add(grid);
  scene.add(new THREE.HemisphereLight(0xdfe6ee, 0x22242a, 1.0));
  const key = new THREE.DirectionalLight(0xffffff, 1.5);
  key.position.set(2.5, 4, 2);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xaecbff, 0.45);
  fill.position.set(-3, 2, -2);
  scene.add(fill);

  const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 200);
  camera.position.set(2, 1.6, 2.4);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  dom.viewportWrap.appendChild(renderer.domElement);

  const orbit = new OrbitControls(camera, renderer.domElement);
  orbit.enableDamping = true;
  orbit.target.set(0, 0.5, 0);

  const transformControls = new TransformControls(camera, renderer.domElement);
  transformControls.setSize(0.9);
  scene.add(transformControls.getHelper());
  transformControls.addEventListener("dragging-changed", (ev) => {
    orbit.enabled = !ev.value;
    if (ev.value) {
      session.dragBeforeJSON = JSON.stringify(session.propDef);
    } else {
      commitGizmoDrag();
      session.justDragged = true;
    }
  });
  // Live-update the Rotate X/Y/Z fields while the rotate gizmo is dragged
  // (the underlying part.rot itself is only committed at drag end, in
  // commitGizmoDrag - this just keeps the visible field text in sync).
  transformControls.addEventListener("objectChange", () => {
    if (session.gizmoMode !== "rotate" || !session.rotateInputs) return;
    const part = getSelectedPart();
    const obj = part && session.partObjects.get(part.id);
    if (!obj) return;
    const rot = decomposeLocalQuaternion(obj.quaternion);
    const inputs = session.rotateInputs;
    if (inputs.x && document.activeElement !== inputs.x) inputs.x.value = formatDegrees(rot[0]);
    if (inputs.y && document.activeElement !== inputs.y) inputs.y.value = formatDegrees(rot[1]);
    if (inputs.z && document.activeElement !== inputs.z) inputs.z.value = formatDegrees(rot[2]);
  });

  renderer.domElement.addEventListener("click", onViewportClick);

  session.scene = scene;
  session.camera = camera;
  session.renderer = renderer;
  session.orbit = orbit;
  session.transformControls = transformControls;
  session.raycaster = new THREE.Raycaster();
  session.partsGroup = new THREE.Group();
  scene.add(session.partsGroup);

  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => resizeViewport()).observe(dom.viewportWrap);
  } else {
    window.addEventListener("resize", resizeViewport);
  }
}

function resizeViewport() {
  if (!session.renderer) return;
  const wrap = dom.viewportWrap;
  const w = wrap.clientWidth || 1;
  const h = wrap.clientHeight || 1;
  session.camera.aspect = w / h;
  session.camera.updateProjectionMatrix();
  session.renderer.setSize(w, h, false);
}

function animate() {
  session.rafId = requestAnimationFrame(animate);
  session.orbit.update();
  session.renderer.render(session.scene, session.camera);
}

function frameCamera() {
  const box = new THREE.Box3();
  let any = false;
  session.partsGroup.children.forEach((obj) => {
    obj.updateMatrixWorld(true);
    const b = new THREE.Box3().setFromObject(obj);
    if (!b.isEmpty()) {
      box.union(b);
      any = true;
    }
  });
  if (!any) box.set(new THREE.Vector3(-0.3, 0, -0.3), new THREE.Vector3(0.3, 1, 0.3));
  const size = new THREE.Vector3();
  box.getSize(size);
  const center = new THREE.Vector3();
  box.getCenter(center);
  const radius = Math.max(size.length() * 0.7, 0.6);
  session.orbit.target.copy(center);
  session.camera.position.set(center.x + radius, center.y + radius * 0.7, center.z + radius);
  session.camera.near = Math.max(radius / 100, 0.01);
  session.camera.far = radius * 60;
  session.camera.updateProjectionMatrix();
  session.orbit.update();
}

// -------------------------------------------------------------- session --

function loadSession(app, propId) {
  session.app = app;
  session.originalId = propId || null;
  const lib = (app.props.draft && app.props.draft.props) || {};
  session.propDef = propId && lib[propId] ? JSON.parse(JSON.stringify(lib[propId])) : newBlankProp();
  session.selectedPartId = null;
  session.undoStack = [];
  session.redoStack = [];
  session.dirty = false;
  session.gizmoMode = "translate";
  if (session.transformControls) {
    session.transformControls.setMode("translate");
    session.transformControls.detach();
  }
}

function requestClose() {
  if (session.dirty) {
    const ok = window.confirm("Discard changes to this prop?");
    if (!ok) return;
  }
  closeEditor();
}

function closeEditor() {
  document.removeEventListener("keydown", onKeyDown);
  if (session.rafId) {
    cancelAnimationFrame(session.rafId);
    session.rafId = null;
  }
  dom.overlay.classList.add("hidden");
}

async function saveAndClose() {
  const app = session.app;
  const lib = app.props.draft.props;
  let id = session.originalId;
  if (!id) id = "prop-" + Date.now().toString(36) + Math.floor(Math.random() * 1000);
  const instanceCount = ((app.stage.draft && app.stage.draft.objects) || []).filter((o) => o.prop === id).length;
  if (instanceCount > 0) {
    const ok = window.confirm('Update all ' + instanceCount + ' placed instance(s) of "' + (session.propDef.name || id) + '"?');
    if (!ok) return;
  }
  lib[id] = JSON.parse(JSON.stringify(session.propDef));
  updateStatus("Saving…", "");
  try {
    await app.savePropsLibrary();
    app.props.editingId = id;
    if (app.rebuildSceneFromDraft) app.rebuildSceneFromDraft();
    if (app.props.onChanged) app.props.onChanged();
    updateStatus("Saved.", "ok");
    session.dirty = false;
    closeEditor();
  } catch (e) {
    updateStatus("Save failed: " + (e && e.message ? e.message : e), "error");
  }
}

// -------------------------------------------------------- parts / gizmo --

function disposeObject3D(obj) {
  obj.traverse((node) => {
    if (!node.isMesh) return;
    if (node.geometry) node.geometry.dispose();
    // Only a PRIMITIVE part's own mesh carries `userData.partId` directly
    // (createPartMesh sets it on the mesh itself) - a "model"/"truss"/
    // "ledstrip" part's partId lives on its wrapper GROUP instead (never a
    // Mesh, so never reaches here), and its inner meshes' materials/maps are
    // SHARED BY REFERENCE across every instance of that same glTF/template
    // (stage-props.js: `template.clone(true)`) - disposing those here would
    // corrupt every other placed copy of the same model. Primitive parts'
    // materials are never shared this way (makeMaterial builds a fresh one
    // per mesh; a textured one gets its OWN cloned map - stage-props.js
    // applyTexture), so disposing `.map` here is what actually releases
    // stage-textures.js's shared, refcounted bake - rebuildPartsGroup()
    // throws away the whole group on every single field edit, so skipping
    // this would leak one bake per edit while a textured part is open.
    const isOwnPrimitivePart = !!(node.userData && node.userData.partId);
    const mats = Array.isArray(node.material) ? node.material : [node.material];
    mats.forEach((m) => {
      if (!m) return;
      if (isOwnPrimitivePart && m.map && m.map.dispose) m.map.dispose();
      if (m.dispose) m.dispose();
    });
  });
}

function getSelectedPart() {
  return (session.propDef.parts || []).find((p) => p.id === session.selectedPartId) || null;
}

function rebuildPartsGroup() {
  if (session.partsGroup) {
    session.scene.remove(session.partsGroup);
    disposeObject3D(session.partsGroup);
  }
  const group = new THREE.Group();
  const newObjects = new Map();
  (session.propDef.parts || []).forEach((part) => {
    const obj = createPartMesh(part, null);
    if (part.shape === "plane") {
      // item 2: a faint outline on every plane, both faces, so a one-sided
      // plane viewed from its invisible side is never mistaken for "gone".
      const w = (part.size ? part.size[0] : 24) * IN;
      const h = (part.size ? part.size[2] : 24) * IN;
      obj.add(
        new THREE.LineSegments(
          new THREE.EdgesGeometry(new THREE.PlaneGeometry(w, h)),
          new THREE.LineBasicMaterial({ color: 0x9099aa, transparent: true, opacity: 0.55 })
        )
      );
    }
    newObjects.set(part.id, obj);
    group.add(obj);
  });
  session.partsGroup = group;
  session.partObjects = newObjects;
  session.scene.add(group);

  if (session.selectedPartId && newObjects.has(session.selectedPartId)) {
    const part = getSelectedPart();
    if (session.gizmoMode === "scale" && (!part || !part.size)) {
      session.gizmoMode = "translate";
      session.transformControls.setMode("translate");
      refreshModeButtons();
    }
    session.transformControls.attach(newObjects.get(session.selectedPartId));
  } else {
    session.transformControls.detach();
    session.selectedPartId = null;
  }
}

function selectPart(id) {
  session.selectedPartId = id;
  const obj = id ? session.partObjects.get(id) : null;
  if (obj) {
    const part = getSelectedPart();
    if (session.gizmoMode === "scale" && (!part || !part.size)) {
      session.gizmoMode = "translate";
      session.transformControls.setMode("translate");
      refreshModeButtons();
    }
    session.transformControls.attach(obj);
  } else {
    session.transformControls.detach();
  }
  refreshPartsList();
  refreshFieldsPanel();
}

function pickPartAt(clientX, clientY) {
  const rect = session.renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
  session.raycaster.setFromCamera(ndc, session.camera);
  const hits = session.raycaster.intersectObjects(session.partsGroup.children, true);
  for (let i = 0; i < hits.length; i++) {
    let o = hits[i].object;
    while (o && o !== session.partsGroup) {
      if (o.userData && o.userData.partId) return o.userData.partId;
      o = o.parent;
    }
  }
  return null;
}

function onViewportClick(ev) {
  if (session.justDragged) {
    session.justDragged = false;
    return;
  }
  selectPart(pickPartAt(ev.clientX, ev.clientY));
}

function commitGizmoDrag() {
  const part = getSelectedPart();
  const obj = part && session.partObjects.get(part.id);
  const beforeJSON = session.dragBeforeJSON;
  session.dragBeforeJSON = null;
  if (!part || !obj || !beforeJSON) return;

  if (session.gizmoMode === "scale" && part.size) {
    const isRound = part.shape === "cylinder" || part.shape === "cone" || part.shape === "sphere";
    if (part.shape === "plane") {
      part.size[0] = Math.max(1, part.size[0] * Math.abs(obj.scale.x));
      part.size[2] = Math.max(1, part.size[2] * Math.abs(obj.scale.y));
    } else if (isRound) {
      const dxz = (Math.abs(obj.scale.x) + Math.abs(obj.scale.z)) / 2;
      part.size[0] = Math.max(0.5, part.size[0] * dxz);
      part.size[1] = part.size[0];
      part.size[2] = Math.max(0.5, (part.size[2] === undefined ? part.size[0] : part.size[2]) * Math.abs(obj.scale.y));
    } else {
      part.size[0] = Math.max(0.5, part.size[0] * Math.abs(obj.scale.x));
      part.size[1] = Math.max(0.5, (part.size[1] === undefined ? part.size[0] : part.size[1]) * Math.abs(obj.scale.z));
      part.size[2] = Math.max(0.5, (part.size[2] === undefined ? part.size[0] : part.size[2]) * Math.abs(obj.scale.y));
    }
  } else if (session.gizmoMode !== "scale") {
    const p = obj.position;
    part.pos = [p.x / IN, -(p.z / IN), p.y / IN];
    part.rot = decomposeLocalQuaternion(obj.quaternion);
  }

  const afterJSON = JSON.stringify(session.propDef);
  if (afterJSON === beforeJSON) return;
  session.undoStack.push(beforeJSON);
  if (session.undoStack.length > 100) session.undoStack.shift();
  session.redoStack.length = 0;
  session.dirty = true;
  rebuildPartsGroup();
  refreshPartsList();
  refreshFieldsPanel();
}

// ------------------------------------------------------- change/history --

function commitChange(mutateFn) {
  const before = JSON.stringify(session.propDef);
  mutateFn();
  const after = JSON.stringify(session.propDef);
  if (before === after) return;
  session.undoStack.push(before);
  if (session.undoStack.length > 100) session.undoStack.shift();
  session.redoStack.length = 0;
  session.dirty = true;
  rebuildPartsGroup();
  refreshTopFields();
  refreshPartsList();
  refreshFieldsPanel();
}

function undo() {
  if (!session.undoStack.length) return;
  session.redoStack.push(JSON.stringify(session.propDef));
  session.propDef = JSON.parse(session.undoStack.pop());
  session.dirty = true;
  rebuildPartsGroup();
  refreshTopFields();
  refreshPartsList();
  refreshFieldsPanel();
}

function redo() {
  if (!session.redoStack.length) return;
  session.undoStack.push(JSON.stringify(session.propDef));
  session.propDef = JSON.parse(session.redoStack.pop());
  session.dirty = true;
  rebuildPartsGroup();
  refreshTopFields();
  refreshPartsList();
  refreshFieldsPanel();
}

function setGizmoMode(mode) {
  const part = getSelectedPart();
  if (mode === "scale" && part && !part.size) return; // truss/ledstrip have no `size` to scale into
  session.gizmoMode = mode;
  session.transformControls.setMode(mode);
  refreshModeButtons();
}

// ------------------------------------------------------------ keyboard --

function onKeyDown(ev) {
  if (ev.key === "Escape") {
    ev.preventDefault();
    requestClose();
    return;
  }
  const tag = (document.activeElement && document.activeElement.tagName) || "";
  const typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
  if ((ev.ctrlKey || ev.metaKey) && !typing) {
    const key = ev.key.toLowerCase();
    if (key === "z") {
      ev.preventDefault();
      if (ev.shiftKey) redo();
      else undo();
      return;
    }
    if (key === "y") {
      ev.preventDefault();
      redo();
      return;
    }
  }
  if (typing) return;
  const key = ev.key.toLowerCase();
  if (key === "w") setGizmoMode("translate");
  else if (key === "e") setGizmoMode("rotate");
  else if (key === "r") setGizmoMode("scale");
  else if (key === "delete" || key === "backspace") {
    const part = getSelectedPart();
    if (part) {
      commitChange(() => deletePart(session.propDef, part.id));
      selectPart(null);
    }
  }
}

// ----------------------------------------------------------------- ui ---

function updateStatus(text, cls) {
  dom.status.textContent = text || "";
  dom.status.className = "pe-status" + (cls ? " " + cls : "");
}

function refreshTopFields() {
  dom.nameInput.value = session.propDef.name || "";
  dom.categorySelect.value = session.propDef.category || CATEGORIES[0];
  dom.aliasesInput.value = (session.propDef.aliases || []).join(", ");
}

function refreshModeButtons() {
  dom.moveBtn.classList.toggle("primary", session.gizmoMode === "translate");
  dom.rotateBtn.classList.toggle("primary", session.gizmoMode === "rotate");
  dom.scaleBtn.classList.toggle("primary", session.gizmoMode === "scale");
}

function refreshPartsList() {
  dom.partsList.innerHTML = "";
  (session.propDef.parts || []).forEach((p) => {
    const row = document.createElement("div");
    row.className = "pe-row" + (p.id === session.selectedPartId ? " selected" : "");
    let label = p.shape;
    if (p.shape === "model") label = p.modelId || "model (no asset chosen)";
    else if (p.shape === "truss") label = (p.trussType || "box12") + " truss";
    row.innerHTML = '<span class="pe-row-label"></span>';
    row.querySelector(".pe-row-label").textContent = label;
    row.addEventListener("click", () => selectPart(p.id));
    dom.partsList.appendChild(row);
  });
}

// ------------------------------------------------------------- fields --

function lengthField(labelText, getVal, setVal) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "text";
  input.value = formatLength(getVal());
  input.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Escape") {
      input.value = formatLength(getVal());
      input.blur();
    } else if (ev.key === "Enter") {
      input.blur();
    }
  });
  input.addEventListener("blur", () => {
    const r = parseLength(input.value, getVal());
    if (r.ok) {
      input.classList.remove("invalid");
      commitChange(() => setVal(r.inches));
    } else {
      input.classList.add("invalid");
      input.title = r.error;
    }
  });
  label.appendChild(input);
  return label;
}

// Degrees are stored/used unitless (part.rot), so the length-expression
// math engine (parseLength) is reused as a plain calculator: bare numbers
// have no unit suffix, so "+15", "*2", "90" all parse exactly as they do
// for length fields - just without a feet/inches meaning attached.
function formatDegrees(deg) {
  if (!isFinite(deg)) return "0";
  let r = Math.round(deg * 1000) / 1000;
  if (Object.is(r, -0)) r = 0;
  return String(r);
}

function rotateField(labelText, getVal, setVal, onInput) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "text";
  input.value = formatDegrees(getVal());
  input.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Escape") {
      input.value = formatDegrees(getVal());
      input.blur();
    } else if (ev.key === "Enter") {
      input.blur();
    }
  });
  input.addEventListener("blur", () => {
    const r = parseLength(input.value, getVal());
    if (r.ok) {
      input.classList.remove("invalid");
      commitChange(() => setVal(r.inches));
    } else {
      input.classList.add("invalid");
      input.title = r.error;
    }
  });
  if (onInput) onInput(input);
  label.appendChild(input);
  return label;
}

function colorField(labelText, getVal, setVal) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "color";
  input.value = getVal() || "#888888";
  input.addEventListener("input", () => commitChange(() => setVal(input.value)));
  label.appendChild(input);
  return label;
}

function selectField(labelText, options, getVal, setVal) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const select = document.createElement("select");
  select.innerHTML = options.map((o) => `<option value="${o}">${o}</option>`).join("");
  select.value = getVal();
  select.addEventListener("change", () => commitChange(() => setVal(select.value)));
  label.appendChild(select);
  return label;
}

function numberField(labelText, getVal, setVal, step) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "number";
  if (step) input.step = String(step);
  input.value = String(getVal());
  input.addEventListener("keydown", (ev) => ev.stopPropagation());
  input.addEventListener("change", () => {
    const v = parseFloat(input.value);
    if (!isNaN(v)) commitChange(() => setVal(v));
  });
  label.appendChild(input);
  return label;
}

function textField(labelText, getVal, setVal) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "text";
  input.value = getVal() || "";
  input.addEventListener("keydown", (ev) => ev.stopPropagation());
  input.addEventListener("change", () => commitChange(() => setVal(input.value)));
  label.appendChild(input);
  return label;
}

function appendModelPicker(panel, part) {
  const title = document.createElement("div");
  title.className = "pe-section-title";
  title.textContent = "Model asset";
  panel.appendChild(title);

  const current = document.createElement("div");
  current.className = "hint";
  current.style.marginBottom = "4px";
  current.textContent = part.modelId ? "Using: " + part.modelId : part.url ? "Custom URL" : "No model chosen - showing a placeholder box";
  panel.appendChild(current);

  const grid = document.createElement("div");
  grid.className = "pe-field-grid";
  grid.appendChild(textField("Custom URL", () => part.url, (v) => { part.url = v; if (v) part.modelId = ""; }));
  panel.appendChild(grid);

  const listWrap = document.createElement("div");
  listWrap.className = "pe-model-picker hint";
  listWrap.textContent = "Loading model library…";
  panel.appendChild(listWrap);

  loadPropModelIndex().then((idx) => {
    listWrap.innerHTML = "";
    listWrap.classList.remove("hint");
    const models = (idx && idx.models) || [];
    if (!models.length) {
      listWrap.classList.add("hint");
      listWrap.textContent = "No downloaded models yet (agent A's library is still being filled) - using a placeholder box.";
      return;
    }
    models.forEach((m) => {
      const row = document.createElement("div");
      row.className = "pe-row" + (part.modelId === m.id ? " selected" : "");
      row.title = m.source || "";
      row.innerHTML = '<span class="pe-row-label"></span>';
      row.querySelector(".pe-row-label").textContent = m.name + " (" + m.category + ")";
      row.addEventListener("click", () => {
        commitChange(() => {
          if (!part.modelId) part.size = m.sizeInches.slice();
          part.modelId = m.id;
          part.url = "";
        });
      });
      listWrap.appendChild(row);
    });
  });
}

// Texture [thumbnail + name + "Change…" + "None"], tile size (real-world
// size, defaults to the texture's own real size), rotate 0/90 - see
// .claude/memory/stage-visualizer.md "Texture library / picker / filters".
// Hover in the picker previews live on the actual part mesh by mutating
// `part.texture` directly (bypassing history) and rebuilding just the
// viewport; Cancel/Esc restores the pre-open value; OK restores it too and
// THEN runs the real change through commitChange, so undo sees one clean
// before/after transition instead of the hover scrubbing.
function appendTextureField(panel, part) {
  const title = document.createElement("div");
  title.className = "pe-section-title";
  title.textContent = "Texture";
  panel.appendChild(title);

  const row = document.createElement("div");
  row.className = "pe-texture-swatch";
  const thumbWrap = document.createElement("div");
  thumbWrap.className = "pe-texture-thumb";
  const nameSpan = document.createElement("div");
  nameSpan.className = "pe-texture-name";
  nameSpan.textContent = part.texture || "No texture";
  row.appendChild(thumbWrap);
  row.appendChild(nameSpan);
  panel.appendChild(row);

  if (part.texture) {
    getTextureEntry(part.texture).then((entry) => {
      if (!entry) return;
      nameSpan.textContent = entry.name || part.texture;
      thumbWrap.innerHTML = "";
      thumbWrap.appendChild(buildThumbElement(entry, 40));
    });
  }

  const btnRow = document.createElement("div");
  btnRow.className = "pe-btn-row";
  const changeBtn = document.createElement("button");
  changeBtn.className = "pe-btn";
  changeBtn.type = "button";
  changeBtn.textContent = "Change…";
  const noneBtn = document.createElement("button");
  noneBtn.className = "pe-btn";
  noneBtn.type = "button";
  noneBtn.textContent = "None";
  btnRow.appendChild(changeBtn);
  btnRow.appendChild(noneBtn);
  panel.appendChild(btnRow);

  noneBtn.addEventListener("click", () => {
    if (!part.texture) return;
    commitChange(() => {
      part.texture = null;
    });
  });

  changeBtn.addEventListener("click", () => {
    const original = part.texture || null;
    openTexturePicker({
      currentSlug: original,
      onHover: (slug) => {
        part.texture = slug;
        rebuildPartsGroup();
      },
      onPick: (slug) => {
        part.texture = original;
        commitChange(() => {
          part.texture = slug;
        });
      },
      onCancel: () => {
        part.texture = original;
        rebuildPartsGroup();
      },
    });
  });

  if (part.texture) {
    getTextureEntry(part.texture).then((entry) => {
      if (!entry) return;
      const grid = document.createElement("div");
      grid.className = "pe-field-grid";
      grid.appendChild(
        lengthField(
          "Tile width",
          () => (part.tileIn ? part.tileIn[0] : entry.tileInchesX) || entry.tileInchesX,
          (v) => {
            part.tileIn = part.tileIn || [entry.tileInchesX, entry.tileInchesY];
            part.tileIn[0] = v;
          }
        )
      );
      grid.appendChild(
        lengthField(
          "Tile height",
          () => (part.tileIn ? part.tileIn[1] : entry.tileInchesY) || entry.tileInchesY,
          (v) => {
            part.tileIn = part.tileIn || [entry.tileInchesX, entry.tileInchesY];
            part.tileIn[1] = v;
          }
        )
      );
      grid.appendChild(selectField("Rotate", ["0", "90"], () => String(part.textureRot || 0), (v) => (part.textureRot = parseInt(v, 10) || 0)));
      panel.appendChild(grid);
    });
  }
}

function refreshFieldsPanel() {
  const panel = dom.rightPanel;
  panel.innerHTML = "";
  const part = getSelectedPart();
  session.rotateInputs = null;
  if (!part) {
    const hint = document.createElement("div");
    hint.className = "pe-section-title";
    hint.textContent = "No part selected";
    panel.appendChild(hint);
    return;
  }

  const title = document.createElement("div");
  title.className = "pe-section-title";
  title.textContent = "Part: " + part.shape;
  panel.appendChild(title);

  const grid = document.createElement("div");
  grid.className = "pe-field-grid";
  panel.appendChild(grid);

  const isRound = part.shape === "cylinder" || part.shape === "cone" || part.shape === "sphere";
  const isTruss = part.shape === "truss";
  const isLed = part.shape === "ledstrip";
  const isModel = part.shape === "model";

  if (isTruss) {
    grid.appendChild(selectField("Truss type", TRUSS_TYPES, () => part.trussType || "box12", (v) => (part.trussType = v)));
    grid.appendChild(lengthField("Length", () => part.length || 120, (v) => (part.length = v)));
    grid.appendChild(colorField("Tint", () => part.color, (v) => (part.color = v)));
  } else if (isLed) {
    grid.appendChild(lengthField("Length", () => part.length || 39.37, (v) => (part.length = v)));
    grid.appendChild(numberField("LEDs / metre", () => part.ledsPerMeter || 60, (v) => (part.ledsPerMeter = v), 1));
    grid.appendChild(numberField("Brightness", () => (part.brightness === undefined ? 1 : part.brightness), (v) => (part.brightness = v), 0.1));
    grid.appendChild(colorField("Colour", () => part.color, (v) => (part.color = v)));
  } else if (isModel) {
    grid.appendChild(lengthField("Width", () => part.size[0], (v) => (part.size[0] = v)));
    grid.appendChild(lengthField("Depth", () => (part.size[1] === undefined ? part.size[0] : part.size[1]), (v) => (part.size[1] = v)));
    grid.appendChild(lengthField("Height", () => (part.size[2] === undefined ? part.size[0] : part.size[2]), (v) => (part.size[2] = v)));
  } else {
    grid.appendChild(
      lengthField(
        isRound ? "Diameter" : "Width",
        () => part.size[0],
        (v) => {
          part.size[0] = v;
          if (isRound) part.size[1] = v;
        }
      )
    );
    if (!isRound) grid.appendChild(lengthField("Depth", () => (part.size[1] === undefined ? part.size[0] : part.size[1]), (v) => (part.size[1] = v)));
    grid.appendChild(lengthField("Height", () => (part.size[2] === undefined ? part.size[0] : part.size[2]), (v) => (part.size[2] = v)));
  }

  grid.appendChild(lengthField("X (across)", () => part.pos[0], (v) => (part.pos[0] = v)));
  grid.appendChild(lengthField("Y (depth)", () => part.pos[1], (v) => (part.pos[1] = v)));
  grid.appendChild(lengthField(isModel ? "Z (base height)" : "Z (height)", () => part.pos[2], (v) => (part.pos[2] = v)));

  const rotateInputs = {};
  grid.appendChild(
    rotateField(
      "Rotate X (deg)",
      () => (part.rot ? part.rot[0] : 0),
      (v) => {
        part.rot = part.rot || [0, 0, 0];
        part.rot[0] = v;
      },
      (input) => (rotateInputs.x = input)
    )
  );
  grid.appendChild(
    rotateField(
      "Rotate Y (deg)",
      () => (part.rot ? part.rot[1] : 0),
      (v) => {
        part.rot = part.rot || [0, 0, 0];
        part.rot[1] = v;
      },
      (input) => (rotateInputs.y = input)
    )
  );
  grid.appendChild(
    rotateField(
      "Rotate Z (deg)",
      () => (part.rot ? part.rot[2] : 0),
      (v) => {
        part.rot = part.rot || [0, 0, 0];
        part.rot[2] = v;
      },
      (input) => (rotateInputs.z = input)
    )
  );
  session.rotateInputs = rotateInputs;

  if (!isTruss && !isLed && !isModel) {
    grid.appendChild(colorField("Colour (tint)", () => part.color, (v) => (part.color = v)));
    grid.appendChild(selectField("Material", MATERIALS, () => part.material || "matte", (v) => (part.material = v)));
  }

  if (!isTruss && !isLed && !isModel) {
    // Closed solids (box/cylinder/sphere/cone/torus) default to FrontSide
    // (perf audit fix) - this lets the operator switch a specific part back
    // to "both" if it's genuinely meant to be seen from inside/behind.
    grid.appendChild(selectField("Visible from", SIDE_OPTIONS, () => part.side || "front", (v) => (part.side = v)));
  }

  if (isModel) appendModelPicker(panel, part);

  if (!isTruss && !isLed && !isModel) {
    appendTextureField(panel, part);

    const filtersTitle = document.createElement("div");
    filtersTitle.className = "pe-section-title";
    filtersTitle.textContent = "Filters";
    panel.appendChild(filtersTitle);
    panel.appendChild(
      buildFilterControls(part.filters, (key, value) => {
        commitChange(() => {
          if (key === "__reset__") {
            part.filters = null;
            return;
          }
          part.filters = part.filters || {};
          part.filters[key] = value;
        });
      })
    );
  }

  if (FACE_LABELS[part.shape]) {
    const heading = document.createElement("div");
    heading.className = "pe-section-title";
    heading.textContent = "Visible faces";
    panel.appendChild(heading);
    const facesWrap = document.createElement("div");
    facesWrap.className = "pe-face-toggles";
    FACE_LABELS[part.shape].forEach((f) => {
      const label = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = (part.hiddenFaces || []).indexOf(f.id) === -1;
      cb.addEventListener("change", () => {
        commitChange(() => {
          part.hiddenFaces = part.hiddenFaces || [];
          const i = part.hiddenFaces.indexOf(f.id);
          if (cb.checked && i !== -1) part.hiddenFaces.splice(i, 1);
          else if (!cb.checked && i === -1) part.hiddenFaces.push(f.id);
        });
      });
      label.appendChild(cb);
      label.appendChild(document.createTextNode(" " + f.label));
      facesWrap.appendChild(label);
    });
    panel.appendChild(facesWrap);
  }

  const btnRow = document.createElement("div");
  btnRow.className = "pe-btn-row";
  function mkBtn(text, cls, fn) {
    const b = document.createElement("button");
    b.className = "pe-btn" + (cls ? " " + cls : "");
    b.textContent = text;
    b.onclick = fn;
    return b;
  }
  btnRow.appendChild(
    mkBtn("Duplicate", "", () => {
      let newId = null;
      commitChange(() => {
        const c = duplicatePart(session.propDef, part.id);
        newId = c && c.id;
      });
      if (newId) selectPart(newId);
    })
  );
  btnRow.appendChild(
    mkBtn("Mirror X", "", () => {
      let newId = null;
      commitChange(() => {
        const c = mirrorPart(session.propDef, part.id, "x");
        newId = c && c.id;
      });
      if (newId) selectPart(newId);
    })
  );
  btnRow.appendChild(
    mkBtn("Mirror Y", "", () => {
      let newId = null;
      commitChange(() => {
        const c = mirrorPart(session.propDef, part.id, "y");
        newId = c && c.id;
      });
      if (newId) selectPart(newId);
    })
  );
  btnRow.appendChild(
    mkBtn("Delete", "danger", () => {
      commitChange(() => deletePart(session.propDef, part.id));
      selectPart(null);
    })
  );
  panel.appendChild(btnRow);
}
