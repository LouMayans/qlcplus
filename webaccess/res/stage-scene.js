/*
  stage-scene.js

  The three.js scene: room, anchor marker, camera presets, procedural
  fixture meshes (by kind) with simple additive beams, and room objects
  built from props (stage-props.js).

  Axes (see .claude/memory/stage-visualizer.md): three world (metres, Y-up)
  = (X, Z, -Y) * 0.0254, where X/Y/Z are user inches (X across, Y depth,
  Z height).

  Orientation: R = Rz(yaw=rot[2]) . Ry(rot[1]) . Rx(rot[0]) . H (user axes),
  H = identity(floor) / Rx(180)(hung) / Rx(90)(wall).
  Beam direction (fixture-local user axes): d = Rz(pan) . Rx(tilt) . (0,0,1).
  World direction = R . d.

  Conjugating the whole formula by the axis map M (three = M*user, where
  M*(1,0,0)=(1,0,0), M*(0,1,0)=(0,0,-1), M*(0,0,1)=(0,1,0)) turns every
  user-axis rotation into a same-angle, same-handed three.js rotation:
    Rz_user -> rotate around three's +Y      (pan, yaw)
    Rx_user -> rotate around three's +X      (tilt, roll, hang)
    Ry_user -> rotate around three's +Z, negated (pitch)
  and the base vector (0,0,1)_user maps to three's +Y axis - which is
  exactly the default orientation of THREE.ConeGeometry/CylinderGeometry.
  So the mesh hierarchy below (mount -> hang(folded into mount's X term)
  -> yoke(Y) -> head(X) -> beam pointing local +Y) reproduces the spec's
  maths using plain per-group axis rotations, no shader/matrix tricks
  needed at update time.
*/

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { Reflector } from "three/addons/objects/Reflector.js";
import { buildPropGroup, setModelWarmHook, setEnvMapSource, setAssetReadyHook } from "./stage-props.js";
import { buildLook, isAdjVparModel } from "./stage-looks.js";
import {
  LAYER_BEAM,
  getUniformWorldScale,
  createVolumetricBeam,
  setBeamWorldShape,
  updateBeamLook,
  updateBeamDepthUniforms,
  createBeamSpotLight,
  fitSpotLightToBeam,
  createBounceLight,
  fitBounceLight,
  estimateSurfaceBounce,
  pickTopByBrightness,
} from "./stage-beams.js";
import { createRenderPipeline, DEFAULT_RENDER_SETTINGS } from "./stage-render.js";

export const IN = 0.0254;

export function userToThree(x, y, z) {
  return new THREE.Vector3(x * IN, z * IN, -y * IN);
}
export function threeToUser(v) {
  return [v.x / IN, -v.z / IN, v.y / IN];
}

// ---------------------------------------------------------- disposal ----

// CSS2DObject only removes its DOM element from the label overlay when
// *it* is removed via object.removeFromParent()/scene.remove(object) - not
// when an ancestor group is cleared/rebuilt. Every place that throws away a
// group that might contain labels (fixture rebuild, object rebuild, room
// rebuild, prop preview) must walk it with this first, or removed fixtures'
// and objects' labels are left stuck on screen forever.
export function disposeObject3D(obj) {
  obj.traverse((child) => {
    if (child.isCSS2DObject && child.element && child.element.parentNode) {
      child.element.parentNode.removeChild(child.element);
    }
    if (child.geometry) child.geometry.dispose();
    if (child.material) {
      const mats = Array.isArray(child.material) ? child.material : [child.material];
      mats.forEach((m) => {
        if (m.map) m.map.dispose();
        m.dispose();
      });
    }
  });
}

/** Disposes and removes every child of `group`, leaving it empty. */
export function clearGroup(group) {
  const children = group.children.slice();
  children.forEach((c) => {
    disposeObject3D(c);
    group.remove(c);
  });
}

// Perf: three.js recomputes every Object3D's local matrix (position/
// quaternion/scale -> matrix) on EVERY frame's scene.updateMatrixWorld()
// traversal, unconditionally, whenever matrixAutoUpdate is true (the
// default) - for a room's walls/floor/grid plus every prop/truss segment,
// that is pure wasted CPU once the scene is built, since none of it moves
// on its own. `root` (and everything under it) never changes shape/position
// except through the editor's own explicit gizmo/manipulation code, which
// calls updateMatrix() itself right after mutating position/quaternion/
// scale (see stage-editor.js continueManipulation / the TransformControls
// "objectChange" handler) - that call sets matrixWorldNeedsUpdate=true,
// which the next frame's traversal still honours even with
// matrixAutoUpdate=false, so live drags keep working correctly.
export function freezeStaticSubtree(root) {
  root.traverse((n) => {
    // bake the current position/rotation/scale first: with matrixAutoUpdate off, three never
    // recomputes the local matrix, so a position set just before freezing (setRoom) was lost and
    // the room box was drawn half a room off, cutting the club into four
    n.updateMatrix();
    n.matrixAutoUpdate = false;
  });
  root.updateMatrixWorld(true);
}

// A per-face-hideable part (box/cylinder/cone, see stage-props.js) uses a
// grouped material array where a hidden face's material has visible=false;
// rendering already skips those groups, but raycasting (picking, beam hit
// tests) needs an explicit check so hidden faces are neither pickable nor
// able to block a beam.
function isHitOnHiddenFace(hit) {
  const mat = hit.object.material;
  if (Array.isArray(mat) && hit.face && typeof hit.face.materialIndex === "number") {
    const m = mat[hit.face.materialIndex];
    return !!(m && m.visible === false);
  }
  return false;
}

function hangAngleDeg(hang) {
  return hang === "hung" ? 180 : hang === "wall" ? 90 : 0;
}

/** R (mount + hang) as a quaternion, per the header maths. */
export function mountQuaternion(rot, hang) {
  const rot0 = (rot && rot[0]) || 0;
  const rot1 = (rot && rot[1]) || 0;
  const rot2 = (rot && rot[2]) || 0;
  const yaw = THREE.MathUtils.degToRad(rot2);
  const pitch = THREE.MathUtils.degToRad(rot1);
  const roll = THREE.MathUtils.degToRad(rot0 + hangAngleDeg(hang));
  const my = new THREE.Matrix4().makeRotationY(yaw);
  const mz = new THREE.Matrix4().makeRotationZ(-pitch);
  const mx = new THREE.Matrix4().makeRotationX(roll);
  const m = new THREE.Matrix4().multiplyMatrices(my, mz).multiply(mx);
  return new THREE.Quaternion().setFromRotationMatrix(m);
}

const FT = 12; // inches per foot

// ------------------------------------------------------------- beams ----
// Real beam geometry/shader work (volumetric shell+core, world-scale-safe
// shape, depth-soft-intersection) lives in stage-beams.js now; this section
// is just the thin per-fixture adapter plus the non-beam room-hit maths.

const MAX_BEAM_LENGTH = 40 * FT * IN;

// Read by createBeam() below; kept as a plain module-level value (not a
// scene-manager closure variable) because buildProceduralContent() - which
// creates the first beam for every fixture kind - runs before any
// createSceneManager instance/settings exist. createSceneManager's
// setRenderSettings() keeps this in sync afterwards.
let moduleBeamQuality = DEFAULT_RENDER_SETTINGS.beams.quality;

/** Creates one beam object at the current beam-quality setting. */
function createBeam() {
  return createVolumetricBeam({ quality: moduleBeamQuality });
}

// Distance from o along unit direction d to the first room surface (floor, ceiling or a wall).
// The room spans x 0..W, y 0..H, z -D..0 in three.js metres (user origin is the room corner).
function roomHitLength(room, o, d, maxLength) {
  const w = (room.width || 480) * IN;
  const h = (room.height || 168) * IN;
  const dd = (room.depth || 480) * IN;
  let t = maxLength || MAX_BEAM_LENGTH;
  const hits = [[d.x, -o.x], [d.x, w - o.x], [d.y, -o.y], [d.y, h - o.y], [d.z, -o.z], [d.z, -dd - o.z]];
  for (const [dv, delta] of hits) {
    if (Math.abs(dv) > 1e-6) {
      const tt = delta / dv;
      if (tt > 0.01 && tt < t) t = tt;
    }
  }
  return t;
}

/**
 * Updates a beam's colour/opacity/frustum shape from a computed head state
 * and an already-resolved hit length (see computeBeamLength in the scene
 * manager, which knows whether the room box or the floor+objects are the
 * relevant hit surfaces). `parentWorldScale` compensates for a scaled-down
 * look root (e.g. bodyScale 0.35) so the beam always reaches its WORLD
 * length/width regardless of how the fixture body is scaled (bug fix - see
 * stage-beams.js's header).
 * @param {object} [beamOpts] {widthMul, brightnessMul, softness, haze, noise,
 *        cameraPos} - the Settings popup's global beam-look multipliers
 *        (view-only, not saved) plus the current render settings' beam
 *        look/camera position (read from the scene-manager closure by the
 *        caller - this function itself stays a plain, scene-instance-free
 *        helper).
 * @param {number} parentWorldScale beam's parent's accumulated world scale
 *        (see getUniformWorldScale) - compensates for a scaled-down look
 *        root so length/width always land in true WORLD units.
 */
function updateBeam(beam, state, length, startInches, beamOpts, parentWorldScale) {
  if (!beam) return;
  const visible = state.shutter !== "closed" && state.dimmer > 0.003;
  beam.visible = visible;
  if (!visible) return;

  const opts = beamOpts || {};
  const widthMul = opts.widthMul || 1;
  const brightnessMul = opts.brightnessMul === undefined ? 1 : opts.brightnessMul;

  const strobing = state.shutter === "strobe";
  const flicker = strobing ? (Math.sin(performance.now() * 0.03) > 0 ? 1 : 0.05) : 1;

  const len = Math.max(length || MAX_BEAM_LENGTH, 0.05);
  const spreadDeg = (state.beamSpreadDeg || 0) * widthMul;
  const nearRadius = Math.max(((startInches || 0) * IN * widthMul) / 2, 0.005);
  const farRadius = Math.max(nearRadius + len * Math.tan(THREE.MathUtils.degToRad(spreadDeg / 2)), nearRadius);
  setBeamWorldShape(beam, nearRadius, farRadius, len, parentWorldScale);

  updateBeamLook(beam, {
    color: state.color || "#ffffff",
    intensity: state.dimmer * 0.55 * flicker * brightnessMul,
    softness: opts.softness != null ? opts.softness : 0.6,
    hazeDensity: 0.02 + (opts.haze != null ? opts.haze : 0.35) * 0.5,
    noiseAmount: opts.noise != null ? opts.noise : 0.15,
    timeSec: performance.now() / 1000,
    cameraPos: opts.cameraPos,
  });
}

// -------------------------------------------------------------- room ----

function buildRoom(room) {
  const group = new THREE.Group();
  const w = (room.width || 600) * IN;
  const d = (room.depth || 480) * IN;
  const h = (room.height || 168) * IN;

  const floorGeo = new THREE.PlaneGeometry(w, d);
  floorGeo.rotateX(-Math.PI / 2);
  // Glossy PBR dance floor (goal 3): low roughness/slight metalness so beam
  // pools and bounce light read as reflective highlights, not flat paint.
  // An optional coplanar Reflector (see applyReflectionSettings()) rides
  // just above this for real planar reflections at reduced resolution.
  const FloorMatCtor = THREE.MeshPhysicalMaterial || THREE.MeshStandardMaterial;
  const floorMat = new FloorMatCtor({ color: 0x1a1c24, roughness: 0.28, metalness: 0.15, clearcoat: 0.3, clearcoatRoughness: 0.25 });
  const floor = new THREE.Mesh(floorGeo, floorMat);
  floor.receiveShadow = true;
  group.add(floor);

  floor.name = "roomFloor";

  const grid = new THREE.GridHelper(Math.max(w, d), Math.round(Math.max(w, d) / IN / 24), 0x30384a, 0x20242e);
  grid.position.y = 0.002;
  grid.name = "roomGrid";
  group.add(grid);

  const wallMat = new THREE.LineBasicMaterial({ color: 0x3a4258 });
  const wallGeo = new THREE.BoxGeometry(w, h, d);
  const wallEdges = new THREE.EdgesGeometry(wallGeo);
  const walls = new THREE.LineSegments(wallEdges, wallMat);
  walls.position.y = h / 2;
  walls.name = "roomWalls";
  group.add(walls);

  // an invisible box used only for beam hit-testing when the visible
  // wireframe ("Show room box") is switched off in Settings
  const hitBoxGeo = new THREE.BoxGeometry(w, h, d);
  const hitBox = new THREE.Mesh(hitBoxGeo, new THREE.MeshBasicMaterial({ visible: false }));
  hitBox.position.y = h / 2;
  hitBox.name = "roomHitBox";
  group.add(hitBox);

  return group;
}

function buildAnchorMarker() {
  const group = new THREE.Group();
  const mat = new THREE.LineBasicMaterial({ color: 0xffcc00 });
  const len = 0.4;
  const pts = [
    new THREE.Vector3(-len, 0, 0),
    new THREE.Vector3(len, 0, 0),
    new THREE.Vector3(0, 0, -len),
    new THREE.Vector3(0, 0, len),
  ];
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  group.add(new THREE.LineSegments(geo, mat));
  const dot = new THREE.Mesh(new THREE.SphereGeometry(0.05, 12, 8), new THREE.MeshBasicMaterial({ color: 0xffcc00 }));
  group.add(dot);
  return group;
}

// --------------------------------------------------------- fixtures -----

function boxMesh(w, h, d, color) {
  return new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshStandardMaterial({ color: color, roughness: 0.6 }));
}
function cylMesh(r, h, color) {
  return new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 16), new THREE.MeshStandardMaterial({ color: color, roughness: 0.5, metalness: 0.3 }));
}

/** Builds the procedural placeholder mesh for one fixture model - used
 *  immediately (synchronously) for every fixture, then replaced in place by
 *  a real "look" (stage-looks.js) once one loads, for the kinds that have a
 *  default/assigned look (see resolveLookId/defaultLookIdForKind below).
 *  Returns { group, beams:[{beam,yoke,head,cell,panel,glow,headIndex}] } -
 *  `group` is a plain content node with no userData of its own; the caller
 *  (rebuildFixtures) parents it under the fixture's real keyed root so a
 *  later look-swap can remove/replace just this node without touching the
 *  root (and its label, position, userData.key, etc). */
function buildProceduralContent(fxModel) {
  const group = new THREE.Group();
  const beams = [];
  const color = 0x8a8f9a;

  if (fxModel.kind === "movingHead") {
    const base = boxMesh(0.22, 0.16, 0.22, color);
    base.position.y = 0.08;
    group.add(base);

    const yoke = new THREE.Group();
    yoke.position.y = 0.16;
    group.add(yoke);
    const yokeArm = cylMesh(0.03, 0.22, 0x555a66);
    yokeArm.rotation.z = Math.PI / 2;
    yoke.add(yokeArm);

    const head = new THREE.Group();
    yoke.add(head);
    const headMesh = cylMesh(0.09, 0.16, 0x2b2e36);
    head.add(headMesh);

    const beam = createBeam(fxModel.beamDeg);
    head.add(beam);

    beams.push({ beam: beam, yoke: yoke, head: head, headIndex: 0 });
  } else if (fxModel.kind === "par") {
    const can = cylMesh(0.1, 0.22, color);
    can.rotation.x = 0;
    can.position.y = 0.11;
    group.add(can);
    const beam = createBeam(fxModel.beamDeg);
    beam.position.y = 0.22;
    group.add(beam);
    beams.push({ beam: beam, yoke: null, head: group, headIndex: 0 });
  } else if (fxModel.kind === "bar") {
    const n = Math.max(fxModel.heads.length, 1);
    const length = 1.0;
    const bar = boxMesh(length, 0.08, 0.08, color);
    bar.position.y = 0.04;
    group.add(bar);
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 0.5 : i / (n - 1);
      const x = (t - 0.5) * length;
      const cell = new THREE.Mesh(new THREE.SphereGeometry(0.03, 10, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));
      cell.position.set(x, 0.09, 0);
      group.add(cell);
      const beam = createBeam(fxModel.beamDeg);
      beam.position.set(x, 0.09, 0);
      // no local .scale here - the beam's shape is always written in WORLD
      // units by setBeamWorldShape() (see updateBeam), so leave this node
      // at scale 1 or that world-space size gets silently shrunk again.
      group.add(beam);
      beams.push({ beam: beam, cell: cell, yoke: null, head: group, headIndex: i });
    }
  } else if (fxModel.kind === "panel") {
    const panel = new THREE.Mesh(
      new THREE.PlaneGeometry(0.6, 0.6),
      new THREE.MeshStandardMaterial({ color: 0x222222, emissive: 0x111111, side: THREE.DoubleSide })
    );
    panel.position.y = 0.3;
    group.add(panel);
    beams.push({ beam: null, panel: panel, yoke: null, head: group, headIndex: 0 });
  } else if (fxModel.kind === "fog") {
    const box = boxMesh(0.3, 0.2, 0.2, 0x333333);
    box.position.y = 0.1;
    group.add(box);
  } else if (fxModel.kind === "strobe") {
    const box = boxMesh(0.25, 0.15, 0.15, 0xdddddd);
    box.position.y = 0.08;
    group.add(box);
    beams.push({ beam: null, glow: box, yoke: null, head: group, headIndex: 0 });
  } else {
    const box = boxMesh(0.2, 0.2, 0.2, 0x555555);
    box.position.y = 0.1;
    group.add(box);
  }

  return { group: group, beams: beams };
}

// -------------------------------------------------------- looks (M4) -----
// Fixture 3D "looks" (stage-looks.js): real GDTF-derived or built-in QLC+
// models, swapped in over the procedural placeholder once loaded. See
// .claude/memory/stage-visualizer.md and stage-looks.js's own header for the
// coordinate conventions this relies on (a look's `lens` node's local +Y is
// always the beam axis, in any pan/tilt pose).

const LOOK_BASE_URL = "/stage-lib/";

// A fixture's own `look` (per-instance) wins over its model's `look` (every
// fixture sharing that manufacturer/model), which wins over a plain
// kind-based default; `null`/absent at every level means "keep the
// procedural placeholder" for kinds with no sensible default look.
function defaultLookIdForKind(fxModel) {
  switch (fxModel.kind) {
    case "movingHead":
      return "builtin/moving_head";
    case "par":
      return "builtin/par";
    case "strobe":
      return "builtin/strobe";
    case "fog":
      return "builtin/hazer";
    case "fx": {
      // classifyKind() (stage-rig.js) folds scanner/derby/laser/effect
      // fixtures into "fx"; only the scanner-like ones get a default look -
      // derby/laser/effect keep the procedural mesh (task spec: "others ...
      // keep the procedural mesh (bars/pixel, panels, fx)").
      const text = ((fxModel.type || "") + " " + (fxModel.model || "") + " " + (fxModel.name || "")).toLowerCase();
      return /scanner/.test(text) ? "builtin/scanner" : null;
    }
    default:
      return null; // bar/panel/other: procedural only
  }
}

// Built-in looks come in at their real size (stage-looks.js fits QLC+'s generic moving head to 20 in, the club's
// BEAM230), so a fixture's display size is 1. models["<Manufacturer>/<Model>"].bodyScale still scales one model.
export function bodyScaleFor(fxModel, lookId, modelsOverride) {
  const ov = modelsOverride && modelsOverride[fxModel.modelKey];
  if (ov && typeof ov.bodyScale === "number" && ov.bodyScale > 0) return ov.bodyScale;
  return 1;
}

export function resolveLookId(fxModel, stageFixtureEntry, modelsOverride) {
  if (stageFixtureEntry && stageFixtureEntry.look) return stageFixtureEntry.look;
  const mo = modelsOverride && modelsOverride[fxModel.modelKey];
  if (mo && mo.look) return mo.look;
  // American DJ VPar: a procedural built-in look (stage-looks.js), default
  // for any fixture whose manufacturer/model matches it, ahead of the
  // generic kind-based default (a VPar is usually classified "par").
  if (isAdjVparModel(fxModel.manufacturer, fxModel.model)) return "builtin/adj-vpar";
  return defaultLookIdForKind(fxModel);
}

// console.warn a failed look load exactly once per look id (not once per
// fixture instance - several fixtures commonly share one look/model).
const warnedLookIds = new Set();

// ------------------------------------------------------- scene manager --

export function createSceneManager(container, opts) {
  const options = opts || {};
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x06070a);

  const camera = new THREE.PerspectiveCamera(55, 1, 0.05, 500);
  camera.position.set(6, 5, 9);

  // Perf: no native MSAA here - stage-render.js's composer adds FXAA/SMAA as
  // a post pass only when the quality preset enables it, so an
  // unconditional antialias:true here would otherwise pay for MSAA AND a
  // post AA pass at once. stencil:false and high-performance power
  // preference are cheap wins on integrated GPUs (Intel UHD): no stencil
  // buffer allocation, and an explicit hint against a driver silently
  // picking the low-power/iGPU-throttled path on hybrid-graphics laptops.
  const renderer = new THREE.WebGLRenderer({ antialias: false, stencil: false, powerPreference: "high-performance" });
  // Checking each shader's compile status makes the page wait for every compile in turn (seconds on
  // integrated GPUs); three.js recommends turning it off outside development.
  renderer.debug.checkShaderErrors = false;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  // Item 8: the real (unmasked) GPU/driver string, so the perf HUD can show
  // the operator whether WebGL is actually running on the hardware GPU
  // (e.g. "ANGLE (Intel(R) UHD Graphics ...)") or a software rasterizer
  // ("SwiftShader"/"llvmpipe"/"Software") - the two look identical in FPS
  // alone under a light scene, but not under real show load.
  let gpuRendererString = "unknown";
  try {
    const gl = renderer.getContext();
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    if (dbg) gpuRendererString = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) || gpuRendererString;
    else gpuRendererString = gl.getParameter(gl.RENDERER) || gpuRendererString;
  } catch (e) {
    /* ignore - HUD just shows "unknown" */
  }
  container.appendChild(renderer.domElement);

  const labelRenderer = new CSS2DRenderer();
  labelRenderer.domElement.style.position = "absolute";
  labelRenderer.domElement.style.top = "0";
  labelRenderer.domElement.style.left = "0";
  labelRenderer.domElement.style.pointerEvents = "none";
  container.appendChild(labelRenderer.domElement);

  // A dim hemisphere fill so unlit surfaces (walls, floor, furniture) still
  // read at a seat/FOH view with no show running - ground colour lightened
  // from near-black so it actually contributes visible bounce-like fill.
  const hemi = new THREE.HemisphereLight(0x9aa8c8, 0x33364a, 2.2);
  scene.add(hemi);
  // gentle fill for shape only: at 1.0 it acted like a sun and glared white off the glossy dance floor
  const dir = new THREE.DirectionalLight(0xffffff, 0.35);
  dir.position.set(5, 10, 2);
  scene.add(dir);
  // House lights: the room's work light (ambient + hemisphere + directional). Off = a truly dark
  // club where only the fixtures light things (View mode). Brightness only, never .visible, so
  // switching doesn't recompile shaders.
  const HEMI_BASE = hemi.intensity;
  const DIR_BASE = dir.intensity;
  let houseLightsOn = true;
  const workLightAmbient = new THREE.AmbientLight(0xffffff, 0);
  scene.add(workLightAmbient);

  // Cheap global-illumination approximation: a single always-present ambient
  // light whose colour/intensity follow the lit beams in the room (sum of
  // colour*dimmer, weighted by the room's average surface albedo), smoothed
  // over ~0.3s - see updateBeamLighting(). Fixed light count (allocated once,
  // never added/removed), intensity/colour-only changes every tick (no
  // recompile). Entirely separate from houseLightsOn/workLightAmbient above -
  // it must go to zero when every fixture is dark regardless of house
  // lights, and never affects the house-lights toggle in the other
  // direction either.
  const roomGlow = new THREE.AmbientLight(0xffffff, 0);
  scene.add(roomGlow);
  let avgRoomAlbedo = 0.45; // recomputed from real object materials in rebuildObjects()
  const _glowColorTmp = new THREE.Color();
  const _glowTargetColor = new THREE.Color(1, 1, 1);
  let glowTargetIntensity = 0;
  let lastGlowTime = performance.now();

  // ------------------------------------------------ render pipeline (R) ---
  // stage-render.js owns the EffectComposer/post chain + depth pre-pass;
  // this module owns everything settings-driven that touches the *scene*
  // itself (beam quality/look, spot/bounce light budgets, shadows, fog/
  // haze, work light, floor reflections, render mode).
  const pipeline = createRenderPipeline(renderer, scene, camera);
  let currentRenderSettings = JSON.parse(JSON.stringify(DEFAULT_RENDER_SETTINGS));
  let currentRenderMode = "lit";

  // Pooled SpotLights/PointLights (goal 3): allocated once at the largest
  // budget any quality preset asks for, reused every frame - never created/
  // destroyed per fixture, so raising/lowering the budget in Settings is
  // just showing/hiding pool members.
  const MAX_SPOT_LIGHTS = 32;
  const MAX_BOUNCE_LIGHTS = 32;
  const spotLightPool = [];
  for (let i = 0; i < MAX_SPOT_LIGHTS; i++) {
    const light = createBeamSpotLight();
    scene.add(light);
    scene.add(light.target);
    spotLightPool.push(light);
  }
  const bounceLightPool = [];
  for (let i = 0; i < MAX_BOUNCE_LIGHTS; i++) {
    const light = createBounceLight();
    scene.add(light);
    bounceLightPool.push(light);
  }
  let fogFixtureDimmer = 0; // max dimmer seen this frame among "fog"/hazer fixtures

  function applyWorkLight() {
    // "unlit" render mode always shows a flat work-light regardless of the
    // workLight setting (goal 5: "flat work light, no post").
    const base = currentRenderMode === "unlit" ? 0.9 : currentRenderSettings.workLight;
    const k = houseLightsOn || currentRenderMode === "unlit" ? 1 : 0;
    workLightAmbient.intensity = base * k;
    hemi.intensity = HEMI_BASE * k;
    dir.intensity = DIR_BASE * k;
    hemi.visible = currentRenderMode !== "beams";
    dir.visible = currentRenderMode !== "beams";
    // room glow (cheap GI): only meaningful in the normal lit look - "beams"
    // mode wants a black room but the beams/pools, "unlit"/"wireframe" are
    // flat/diagnostic views. Toggled only here (mode changes), never per frame.
    roomGlow.visible = currentRenderMode === "lit";
  }

  function applyFog() {
    if (currentRenderMode === "unlit" || currentRenderMode === "wireframe") {
      scene.fog = null;
      return;
    }
    const haze = currentRenderSettings.beams.haze || 0;
    const density = 0.002 + haze * 0.012 * (0.4 + fogFixtureDimmer * 0.6);
    if (!scene.fog) scene.fog = new THREE.FogExp2(0x06070a, density);
    else scene.fog.density = density;
  }

  let reflector = null;
  function applyReflectionSettings() {
    const floor = roomGroup.getObjectByName("roomFloor");
    if (!floor) return;
    const wantReflection = !!currentRenderSettings.reflections.enabled && currentRenderMode === "lit";
    if (!wantReflection) {
      if (reflector) {
        roomGroup.remove(reflector);
        reflector.geometry.dispose();
        reflector.material.dispose();
        reflector = null;
      }
      return;
    }
    const res = currentRenderSettings.reflections.resolution || 384;
    if (reflector) {
      roomGroup.remove(reflector);
      reflector.geometry.dispose();
      reflector.material.dispose();
      reflector = null;
    }
    reflector = new Reflector(floor.geometry.clone(), {
      textureWidth: res,
      textureHeight: res,
      color: 0x303038,
    });
    reflector.position.copy(floor.position);
    reflector.rotation.copy(floor.rotation);
    reflector.position.y += 0.002; // avoid z-fighting with the floor itself
    roomGroup.add(reflector);
    reflector.matrixAutoUpdate = false; // static once placed - see freezeStaticSubtree
    reflector.updateMatrixWorld(true);
  }

  // ---- optional dynamic cubemap reflections (goal 4, materials pass) -----
  // A single low-res CubeCamera near the room centre, refreshed at most
  // ~4Hz (updateCubeCamera(), called from the render loop below), used as
  // envMap for mirror/metal/glossy-tile/gloss materials ONLY (tagged
  // .userData.reflective by stage-props.js's makeMaterial). Separate from the
  // planar floor Reflector above. Size only changes when the
  // reflections.cube setting changes (0=off/128/256) - that's the one
  // allowed "warm-up recompile" moment; never rebuilt per frame.
  let cubeCamera = null;
  let cubeRenderTarget = null;
  let cubeCameraSize = 0;
  let lastCubeUpdate = 0;
  function roomCenterWorld() {
    const w = currentRoom.width || 480;
    const d = currentRoom.depth || 480;
    const h = currentRoom.height || 168;
    return userToThree(w / 2, d / 2, Math.min(h * 0.45, Math.max(h - 12, 12)));
  }
  function disposeCubeCamera() {
    if (cubeCamera) {
      scene.remove(cubeCamera);
      cubeCamera = null;
    }
    if (cubeRenderTarget) {
      cubeRenderTarget.dispose();
      cubeRenderTarget = null;
    }
  }
  function applyEnvMapToReflectiveMaterials() {
    const env = cubeCamera && cubeRenderTarget ? cubeRenderTarget.texture : null;
    [objectsGroup, fixturesGroup].forEach((g) =>
      g.traverse((o) => {
        if (!o.isMesh) return;
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        mats.forEach((m) => {
          if (m && m.userData && m.userData.reflective) m.envMap = env;
        });
      })
    );
  }
  function applyCubeReflectionSettings() {
    const want = (currentRenderSettings.reflections && currentRenderSettings.reflections.cube) || 0;
    if (want === cubeCameraSize) return;
    cubeCameraSize = want;
    disposeCubeCamera();
    if (want > 0) {
      cubeRenderTarget = new THREE.WebGLCubeRenderTarget(want, { generateMipmaps: true, minFilter: THREE.LinearMipmapLinearFilter });
      cubeCamera = new THREE.CubeCamera(0.15, 60, cubeRenderTarget);
      cubeCamera.position.copy(roomCenterWorld());
      scene.add(cubeCamera);
      lastCubeUpdate = -Infinity; // refresh on the very next opportunity
    }
    applyEnvMapToReflectiveMaterials();
    requestWarmup(); // one recompile for the new envMap - see header comment
  }
  function updateCubeCamera() {
    if (!cubeCamera || currentRenderMode !== "lit") return;
    const now = performance.now();
    if (now - lastCubeUpdate < 250) return; // <= 4Hz
    lastCubeUpdate = now;
    cubeCamera.update(renderer, scene);
  }
  setEnvMapSource(() => (cubeCamera && cubeRenderTarget ? cubeRenderTarget.texture : null));
  setAssetReadyHook(() => requestWarmup());

  function applyShadowBudget() {
    const s = currentRenderSettings.shadows;
    renderer.shadowMap.enabled = s.enabled && currentRenderMode === "lit";
    spotLightPool.forEach((light) => {
      if (light.shadow.mapSize.x !== s.mapSize) {
        light.shadow.mapSize.set(s.mapSize, s.mapSize);
        if (light.shadow.map) {
          light.shadow.map.dispose();
          light.shadow.map = null;
        }
      }
    });
  }

  // ------------------------------------------------------- render API (U) --
  // See .claude/memory/stage-visualizer.md "Render API": U calls these to
  // drive the generic Graphics settings panel built from
  // stage-render.js's RENDER_SETTINGS_SCHEMA/QUALITY_PRESETS.
  function deepMergeSettings(base, patch) {
    const out = Object.assign({}, base);
    if (!patch) return out;
    for (const k of Object.keys(patch)) {
      const v = patch[k];
      if (v && typeof v === "object" && !Array.isArray(v) && base[k] && typeof base[k] === "object") {
        out[k] = deepMergeSettings(base[k], v);
      } else {
        out[k] = v;
      }
    }
    return out;
  }

  function setRenderSettings(patch) {
    currentRenderSettings = deepMergeSettings(currentRenderSettings, patch || {});
    moduleBeamQuality = currentRenderSettings.beams.quality; // new beams only - see its own comment
    pipeline.setSettings(currentRenderSettings);
    applyWorkLight();
    applyFog();
    applyReflectionSettings();
    applyCubeReflectionSettings();
    applyShadowBudget();
    // light budgets/shadows/fog change the shader variants: recompile in the background
    updateBeamLighting([]);
    requestWarmup();
  }
  function getRenderSettings() {
    return JSON.parse(JSON.stringify(currentRenderSettings));
  }
  function getRenderStats() {
    const s = pipeline.getStats();
    let lights = 0;
    scene.traverse((o) => {
      if (o.isLight && o.visible) lights++;
    });
    let beams = 0;
    fixtureEntries.forEach((fe) => fe.beams.forEach((b) => { if (b.beam && b.beam.visible) beams++; }));
    const bounceLights = bounceLightPool.filter((l) => l.visible && l.intensity > 0).length;
    // jsMs (item 8): the per-frame update work (orbit/camera, DMX->beam
    // state, beam depth-uniform binding) measured separately from s.frameMs
    // (stage-render.js's pipeline.render() call, i.e. the actual GPU submit)
    // - lets the operator tell a CPU-JS bottleneck apart from a slow
    // renderer.render() (e.g. software GL).
    return Object.assign({}, s, { lights, beams, bounceLights, jsMs: Math.round(statsJsMs * 100) / 100, gpuRenderer: gpuRendererString,
      programs: (renderer.info.programs || []).length, textures: renderer.info.memory.textures, geometries: renderer.info.memory.geometries });
  }

  // A Group's own .visible=false stops the renderer from even descending
  // into its children (three's WebGLRenderer.projectObject returns early),
  // so "beams" mode can't just hide the whole fixturesGroup - beams live
  // NESTED inside each fixture's body/look group (e.g. procedural
  // `head.add(beam)`, or a look's lens node under `built.root`). Instead
  // this hides only the individual body MESH leaves that are NOT on
  // LAYER_BEAM, leaving every ancestor Group (and the beam meshes
  // themselves) at their normal .visible so beams keep rendering/updating.
  const _beamLayerMask = new THREE.Layers();
  _beamLayerMask.set(LAYER_BEAM);
  function setBodyMeshesHidden(root, hidden) {
    root.traverse((o) => {
      if (!o.isMesh) return;
      if (o.layers.test(_beamLayerMask)) return; // part of a beam - never touched here
      if (hidden) {
        if (o.userData._hiddenByRenderMode === undefined) o.userData._hiddenByRenderMode = o.visible;
        o.visible = false;
      } else if (o.userData._hiddenByRenderMode !== undefined) {
        o.visible = o.userData._hiddenByRenderMode;
        delete o.userData._hiddenByRenderMode;
      }
    });
  }

  function applyRenderModeToSceneGraph() {
    // "beams": black room, only the beams themselves and the light pools
    // they cast on the floor are visible (goal 5) - fixture/prop BODIES
    // hide, but the floor (which the light pools land on) and the beams
    // stay. "wireframe": every material in the graph drawn as wireframe,
    // post disabled (handled in stage-render.js).
    const beamsOnly = currentRenderMode === "beams";
    setBodyMeshesHidden(fixturesGroup, beamsOnly);
    objectsGroup.visible = !beamsOnly;
    anchor.visible = !beamsOnly;
    applyRoomVisibility();
    scene.background = beamsOnly || currentRenderMode === "wireframe" ? new THREE.Color(0x000000) : new THREE.Color(0x06070a);

    const wireframe = currentRenderMode === "wireframe";
    [roomGroup, fixturesGroup, objectsGroup].forEach((g) => {
      g.traverse((o) => {
        if (!o.material) return;
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        mats.forEach((m) => {
          if ("wireframe" in m) m.wireframe = wireframe;
        });
      });
    });
  }

  function setRenderMode(mode) {
    currentRenderMode = mode === "beams" || mode === "unlit" || mode === "wireframe" ? mode : "lit";
    pipeline.setMode(currentRenderMode);
    applyWorkLight();
    applyFog();
    applyReflectionSettings();
    applyShadowBudget();
    applyRenderModeToSceneGraph();
  }
  function getRenderMode() {
    return currentRenderMode;
  }

  // Controls listen on the WebGL canvas, not the CSS2D label overlay: the
  // label div has pointer-events:none precisely so clicks pass through to
  // the canvas underneath.
  // Camera navigation is split: OrbitControls only ever does left-drag PAN
  // here (wheel zoom and middle-drag are disabled on it entirely) - WASD/QE
  // fly, middle-drag mouselook and wheel handling now live in the dedicated
  // stage-camera.js module (owned by stage-app.js), which reads/writes
  // camera/orbit.target directly every frame via its own update(dt).
  const orbit = new OrbitControls(camera, renderer.domElement);
  orbit.target.set(0, 1, 0);
  orbit.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: null, RIGHT: null };
  orbit.enableZoom = false;
  orbit.enableRotate = false;
  orbit.update();

  // Right-click is reserved for the custom context menu (stage-editor.js);
  // never let the browser's own menu pop up over the canvas.
  renderer.domElement.addEventListener("contextmenu", (ev) => ev.preventDefault());

  const transform = new TransformControls(camera, renderer.domElement);
  transform.addEventListener("dragging-changed", (ev) => {
    orbit.enabled = !ev.value && !modifierOrbitLock;
  });
  scene.add(transform.getHelper ? transform.getHelper() : transform);

  // A Maya-style modifier hold (Alt/Shift/Ctrl - see stage-editor.js) takes
  // over left-drag for direct manipulation and must fully own the camera
  // while held, regardless of TransformControls' own dragging state.
  let modifierOrbitLock = false;
  function setOrbitSuppressed(suppressed) {
    modifierOrbitLock = suppressed;
    orbit.enabled = !suppressed && !transform.dragging;
  }

  const roomGroup = new THREE.Group();
  scene.add(roomGroup);
  const anchor = buildAnchorMarker();
  scene.add(anchor);

  const fixturesGroup = new THREE.Group();
  scene.add(fixturesGroup);
  const objectsGroup = new THREE.Group();
  scene.add(objectsGroup);

  const fixtureEntries = new Map(); // id -> {root, beams}
  const objectEntries = new Map(); // id -> group
  let labelsVisible = true;
  let currentRoom = { width: 600, depth: 480, height: 168 };

  // ---- view settings (per-browser, not part of the stage file/history) ---
  const DEFAULT_VIEW_SETTINGS = {
    fov: 55,
    sx: 1,
    sy: 1,
    beamWidthMul: 1,
    beamBrightnessMul: 1,
    beamLengthFt: 40,
    showRoomBox: true,
    showFloorGrid: true,
    // WASD/QE fly + middle-drag mouselook + mouse feel, per-browser only -
    // read by stage-camera.js (stage-app.js's getSettings()), not used
    // inside this module beyond storage/persistence and the Settings UI.
    moveSpeedFtS: 8,
    fastMul: 3,
    vertSpeedFtS: 6,
    orbitSensitivity: 1, // "mouse look sensitivity" (yaw/pitch rate)
    panSensitivity: 1,
    smoothMove: true,
    keepLevel: false, // "keep level while walking": floor-project W/S/A/D
    orbitMiddle: false, // "middle-drag orbits around the view centre" (old behaviour)
    wheelMode: "off", // "off" | "zoom" | "speed"
  };
  let viewSettings = Object.assign({}, DEFAULT_VIEW_SETTINGS);

  function applyRoomVisibility() {
    // "beams" render mode (goal 5: "black room, only beams and pools")
    // always wins over the Settings-popup "show room box/grid" toggles -
    // see applyRenderModeToSceneGraph, which calls this same combined logic
    // whenever the mode itself changes.
    const beamsOnly = currentRenderMode === "beams";
    const walls = roomGroup.getObjectByName("roomWalls");
    const grid = roomGroup.getObjectByName("roomGrid");
    if (walls) walls.visible = !beamsOnly && !!viewSettings.showRoomBox;
    if (grid) grid.visible = !beamsOnly && !!viewSettings.showFloorGrid;
  }

  function applyCameraSettings() {
    camera.fov = viewSettings.fov || 55;
    camera.updateProjectionMatrix();
    const sx = viewSettings.sx || 1;
    const sy = viewSettings.sy || 1;
    camera.projectionMatrix.elements[0] /= sx;
    camera.projectionMatrix.elements[5] /= sy;
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
  }

  function applyControlSettings() {
    // rotateSpeed is irrelevant now (orbit.enableRotate is permanently
    // false - stage-camera.js owns rotation/look), kept harmlessly for any
    // stale caller. panSpeed still drives OrbitControls' own left-drag pan.
    orbit.panSpeed = viewSettings.panSensitivity || 1;
  }

  function setViewSettings(patch) {
    viewSettings = Object.assign({}, viewSettings, patch || {});
    applyCameraSettings();
    applyRoomVisibility();
    applyControlSettings();
  }
  function getViewSettings() {
    return Object.assign({}, viewSettings);
  }
  function resetViewSettings() {
    setViewSettings(DEFAULT_VIEW_SETTINGS);
  }

  function setRoom(room) {
    currentRoom = room;
    clearGroup(roomGroup); // also disposes/removes any previous reflector (a roomGroup child)
    reflector = null;
    roomGroup.add(buildRoom(room));
    // buildRoom is centred on the origin; the user origin is the room corner
    roomGroup.position.set(((room.width || 600) * IN) / 2, 0, -((room.depth || 480) * IN) / 2);
    applyRoomVisibility();
    applyReflectionSettings();
    if (cubeCamera) cubeCamera.position.copy(roomCenterWorld()); // room size changed - re-centre it
    // Perf: walls/floor/grid never move on their own - freeze their matrices
    // (see freezeStaticSubtree). roomGroup itself is excluded (its own
    // position is set once above, matrixAutoUpdate stays default so a
    // future setRoom() call keeps working the same way either way).
    freezeStaticSubtree(roomGroup);
  }

  function setAnchor(anchorInches) {
    const p = userToThree(anchorInches[0], anchorInches[1], anchorInches[2]);
    anchor.position.copy(p);
  }

  function resize() {
    const w = container.clientWidth || 1;
    const h = container.clientHeight || 1;
    camera.aspect = w / h;
    applyCameraSettings();
    pipeline.setSize(w, h);
    labelRenderer.setSize(w, h);
  }

  function makeLabel(text) {
    const div = document.createElement("div");
    div.className = "stage-label";
    div.textContent = text;
    const obj = new CSS2DObject(div);
    obj.position.set(0, 0.3, 0);
    return obj;
  }

  // Swaps a resolved look into a fixture entry's content node in place: the
  // fixture's outer `root` (keyed, positioned, labelled) never changes
  // identity, so selection/gizmo-attach/picking are unaffected. Re-parents
  // fresh beam(s) under the look's own lens anchor(s) (identity local
  // transform - the module guarantees local +Y there is each anchor's beam
  // axis) and replaces `fe.beams` with one look-based descriptor per
  // anchor. Most looks have a single `lens`; a multi-emitter look (e.g.
  // builtin/adj-vpar) provides `built.emitters` (see stage-looks.js) and
  // gets one beam per emitter, all sharing headIndex 0 (one DMX head drives
  // every emitter's visible colour/dimmer the same way).
  function swapInLook(fe, built, beamStartOverrideInches) {
    if (fe.lookBuilt) {
      // A previous look is being replaced (e.g. the operator picked a new
      // one): its geometries/materials are shared from stage-looks.js's own
      // template cache, so use ITS dispose() (parent removal only), not the
      // generic disposeObject3D - only the beam(s) we added ourselves are
      // actually owned by this fixture instance.
      fe.beams.forEach((b) => {
        if (b.beam) disposeObject3D(b.beam);
      });
      fe.lookBuilt.dispose();
    } else {
      disposeObject3D(fe.contentNode);
      fe.root.remove(fe.contentNode);
    }
    // display size of the fixture body (models[key].bodyScale, default per look)
    const bodyScale = fe.bodyScale || 1;
    built.root.scale.setScalar(bodyScale);
    fe.root.add(built.root);
    fe.contentNode = built.root;
    requestWarmup();
    fe.lookBuilt = built;
    fe.lookId = built.id;
    fe.lookName = built.name;

    const anchors = built.emitters && built.emitters.length ? built.emitters : [built.lens];
    fe.beams = anchors.map((anchor) => {
      const beam = createBeam();
      anchor.add(beam);
      return {
        beam: beam,
        yoke: null,
        head: anchor, // beam-anchor convention shared with the procedural path (see below)
        headIndex: 0,
        setPanTilt: built.movable ? built.setPanTilt : null,
        lensMeshes: built.lensMeshes || [],
      };
    });
    fe.beamStartOverride =
      beamStartOverrideInches != null
        ? beamStartOverrideInches
        : built.lensRadiusInches != null
        ? built.lensRadiusInches * 2 * bodyScale
        : fe.model.beamStart;
  }

  function rebuildFixtures(rigIndex, stageFixtures, modelsOverride) {
    requestWarmup();
    clearGroup(fixturesGroup);
    fixtureEntries.clear();
    if (!rigIndex) return;
    rigIndex.list.forEach((fxModel) => {
      const entry = stageFixtures[fxModel.id];
      if (!entry) return; // unplaced fixtures aren't in the room

      const root = new THREE.Group();
      root.userData.key = "fixture:" + fxModel.id;
      root.userData.kind = "fixture";
      root.userData.fixtureId = fxModel.id;
      root.position.copy(userToThree(entry.pos[0], entry.pos[1], entry.pos[2]));
      root.quaternion.copy(mountQuaternion(entry.rot, entry.hang));
      root.userData.locked = !!entry.locked;

      const proc = buildProceduralContent(fxModel);
      root.add(proc.group);

      const label = makeLabel(fxModel.name);
      root.add(label);
      label.visible = labelsVisible;

      fixturesGroup.add(root);
      const fe = {
        model: fxModel,
        entry: entry,
        root: root,
        beams: proc.beams,
        label: label,
        contentNode: proc.group,
        lookBuilt: null,
        lookId: null,
        lookName: null,
        lookLoading: false,
      };
      fixtureEntries.set(fxModel.id, fe);

      const lookId = resolveLookId(fxModel, entry, modelsOverride);
      fe.bodyScale = bodyScaleFor(fxModel, lookId, modelsOverride);
      if (lookId) {
        fe.lookLoading = true;
        fe.lookId = lookId; // known immediately; fe.lookName fills in once buildLook resolves
        const mk = fxModel.modelKey;
        const explicitBeamStart =
          modelsOverride && modelsOverride[mk] && modelsOverride[mk].beamStart !== undefined ? fxModel.beamStart : null;
        buildLook(lookId, { baseUrl: LOOK_BASE_URL })
          .then((built) => {
            // Stale if a newer rebuildFixtures pass already replaced this
            // fixture's entry (e.g. the operator kept editing while this
            // was in flight) - drop the now-unwanted result instead of
            // mounting it onto a fixture that has moved on.
            if (fixtureEntries.get(fxModel.id) !== fe) {
              built.dispose();
              return;
            }
            fe.lookLoading = false;
            swapInLook(fe, built, explicitBeamStart);
          })
          .catch((err) => {
            fe.lookLoading = false;
            if (!warnedLookIds.has(lookId)) {
              warnedLookIds.add(lookId);
              console.warn('stage: failed to load look "' + lookId + '" - keeping the procedural mesh:', err);
            }
          });
      }
    });
  }

  // How many fixture "looks" (stage-looks.js buildLook()) are still
  // in-flight - window.__stage.assetsPending() (stage-app.js) adds this to
  // stage-props.js's own pendingAssets() so a test harness can wait for a
  // full scene (fixture bodies + prop models/ledstrips) to finish loading.
  function pendingLooks() {
    let n = 0;
    fixtureEntries.forEach((fe) => {
      if (fe.lookLoading) n++;
    });
    return n;
  }

  // window.__stage.lookOf() / the Properties panel "Look" row.
  function getFixtureLookInfo(fixtureId) {
    const fe = fixtureEntries.get(fixtureId);
    if (!fe) return null;
    return {
      id: fe.lookId || null,
      name: fe.lookName || null,
      loaded: !!fe.lookBuilt,
      loading: !!fe.lookLoading,
      movable: fe.lookBuilt ? !!fe.lookBuilt.movable : null,
      bodyScale: fe.bodyScale || 1,
    };
  }

  const _worldPos = new THREE.Vector3();
  const _worldQuat = new THREE.Quaternion();
  const _dir = new THREE.Vector3();
  const _lensGlowColor = new THREE.Color();
  const _raycaster2 = new THREE.Raycaster();
  const _beamHitCache = new WeakMap();

  // Resolves how far a beam travels before it hits something: the room box
  // (when "Show room box" is on) or just the floor plane (when it's off, so
  // beams don't stop at now-invisible walls); either way, nearby room
  // objects can shorten it further. Throttled per-beam (cheap raycast reuse) -
  // ~10Hz normally, ~5Hz on Low (perf item 5: a hit-test result a beam can
  // visibly lag by is a fine trade for CPU on weak/integrated GPUs).
  function beamHitThrottleMs() {
    return currentRenderSettings.quality === "low" ? 200 : 100;
  }
  // Same throttled tick also grabs the actual hit surface's REAL material
  // (goal 2, material-aware bounce), instead of a fixed 0.5 grey guess - a
  // club's walls/floor/bar-front are ordinary objects (club_scene.py), so
  // the objectsGroup raycast already needed to shorten the beam gives us
  // this for free; the default demo room's plain floor mesh ("roomFloor")
  // is added as one extra raycast target so an out-of-club-scene floor hit
  // still resolves to its own real PBR material rather than nothing.
  function lastHitMaterial(beam) {
    const cached = _beamHitCache.get(beam);
    return cached ? cached.material : null;
  }
  function computeBeamLength(beam, worldOrigin, worldDir, maxLength) {
    const now = performance.now();
    const cached = _beamHitCache.get(beam);
    if (cached && now - cached.t < beamHitThrottleMs()) return cached.length;

    let length;
    if (viewSettings.showRoomBox) {
      length = roomHitLength(currentRoom, worldOrigin, worldDir, maxLength);
    } else {
      length = maxLength;
      if (worldDir.y < -1e-6) {
        const tFloor = -worldOrigin.y / worldDir.y;
        if (tFloor > 0.01 && tFloor < length) length = tFloor;
      }
    }
    let hitMaterial = null;
    const floorMesh = roomGroup.getObjectByName("roomFloor");
    const rayTargets = floorMesh ? objectsGroup.children.concat([floorMesh]) : objectsGroup.children;
    if (rayTargets.length) {
      _raycaster2.set(worldOrigin, worldDir);
      _raycaster2.far = length + 0.05; // small epsilon so a hit exactly at the room-bound length still resolves
      _raycaster2.near = 0.01;
      const hits = _raycaster2.intersectObjects(rayTargets, true);
      const firstSolid = hits.find((h) => !isHitOnHiddenFace(h)); // beams pass through hidden faces
      if (firstSolid) {
        if (firstSolid.distance < length) length = firstSolid.distance;
        hitMaterial = Array.isArray(firstSolid.object.material)
          ? firstSolid.object.material[(firstSolid.face && firstSolid.face.materialIndex) || 0]
          : firstSolid.object.material;
      }
    }
    _beamHitCache.set(beam, { t: now, length: length, material: hitMaterial });
    return length;
  }

  // Reused every frame by the spot/bounce light budgeting pass below -
  // avoids an allocation per fixture per frame.
  let beamLightCandidates = [];

  function updateFixtureStates(statesById) {
    beamLightCandidates.length = 0;
    fogFixtureDimmer = 0;
    fixtureEntries.forEach((fe, id) => {
      const states = statesById.get(id);
      if (!states) return;
      if (fe.model.kind === "fog") {
        const s0 = states[0];
        if (s0 && s0.dimmer > fogFixtureDimmer) fogFixtureDimmer = s0.dimmer;
      }
      let needsMatrixUpdate = false;
      fe.beams.forEach((b) => {
        const state = states[b.headIndex] || states[0];
        if (!state) return;
        if (b.setPanTilt) {
          // Look-based fixture (stage-looks.js): same pan/tilt degrees
          // (invert/offset already applied upstream in stage-rig.js) drive
          // the look's own rig instead of a plain yoke/head rotation.
          if (state.hasPanTilt) {
            b.setPanTilt(state.pan || 0, state.tilt || 0);
            needsMatrixUpdate = true;
          }
        } else if (b.yoke && state.hasPanTilt) {
          b.yoke.rotation.y = THREE.MathUtils.degToRad(state.pan || 0);
          b.head.rotation.x = THREE.MathUtils.degToRad(state.tilt || 0);
          needsMatrixUpdate = true;
        }
      });
      // pan/tilt just changed above; refresh world matrices from the
      // fixture root down (a per-node updateMatrixWorld would use a still
      // stale parent matrix) before reading world position/direction below
      if (needsMatrixUpdate) fe.root.updateMatrixWorld(true);

      fe.beams.forEach((b) => {
        const state = states[b.headIndex] || states[0];
        if (!state) return;
        if (b.beam) {
          b.head.getWorldPosition(_worldPos);
          b.head.getWorldQuaternion(_worldQuat);
          _dir.set(0, 1, 0).applyQuaternion(_worldQuat);
          const maxLen = Math.min(viewSettings.beamLengthFt || 40, currentRenderSettings.beams.maxLengthFt || 40) * FT * IN;
          const length = computeBeamLength(b.beam, _worldPos, _dir, maxLen);
          const startInches = fe.beamStartOverride != null ? fe.beamStartOverride : fe.model.beamStart;
          const parentScale = getUniformWorldScale(b.beam.parent);
          updateBeam(
            b.beam,
            state,
            length,
            startInches,
            {
              widthMul: viewSettings.beamWidthMul,
              brightnessMul: viewSettings.beamBrightnessMul,
              softness: currentRenderSettings.beams.softness,
              haze: currentRenderSettings.beams.haze,
              noise: currentRenderSettings.beams.noise,
              cameraPos: camera.position,
            },
            parentScale
          );
          const visible = state.shutter !== "closed" && state.dimmer > 0.003;
          if (visible && currentRenderMode !== "unlit" && currentRenderMode !== "wireframe") {
            beamLightCandidates.push({
              head: b.head,
              state: state,
              length: length,
              worldPos: _worldPos.clone(),
              worldDir: _dir.clone(),
              brightness: state.dimmer,
              hitMaterial: lastHitMaterial(b.beam),
            });
          }
        }
        if (b.cell) b.cell.material.color.set(state.color || "#ffffff");
        if (b.panel) {
          b.panel.material.color.set(state.color || "#222222");
          b.panel.material.emissive.set(state.color || "#111111");
          b.panel.material.emissiveIntensity = Math.max(0.05, state.dimmer);
        }
        if (b.glow) b.glow.material.emissive && b.glow.material.emissive.set(state.color || "#ffffff");
        // A look's lens meshes (stage-looks.js collectLensMeshes: anything
        // named lens/emitter/beam) glow with head colour x dimmer, same
        // spirit as the procedural beam cone's own colour/opacity.
        if (b.lensMeshes && b.lensMeshes.length) {
          _lensGlowColor.set(state.color || "#ffffff");
          const inten = Math.max(0, Math.min(2, (state.dimmer || 0) * 1.2));
          b.lensMeshes.forEach((m) => {
            const mats = Array.isArray(m.material) ? m.material : [m.material];
            mats.forEach((mat) => {
              if (mat && mat.emissive) {
                mat.emissive.copy(_lensGlowColor);
                mat.emissiveIntensity = inten;
              }
            });
          });
        }
      });
    });
    applyFog();
    updateBeamLighting(beamLightCandidates);
  }

  // ------------------------------------------------- spot/bounce lights ---
  // Real-light approximation for goal 3: a SpotLight per visible beam up to
  // spot.maxLights (brightest first), with shadows only for the top
  // shadows.maxShadowLights of THOSE; a dim bounce PointLight at each of
  // the brightest beams' hit points (bounce.count of them), refreshed at
  // ~10Hz (bounce doesn't need to be per-frame-smooth) rather than every
  // frame.
  let lastBounceUpdate = 0;
  const _hitPoint = new THREE.Vector3();
  // Every material's shader depends on how many lights and shadow casters are visible, so a change
  // there recompiles everything (seconds of freeze on integrated GPUs). Lights therefore live in fixed
  // slot counts (8/16/32 spots, 6/16/32 bounce, 2/4/8 shadow casters): budgets inside a bucket only
  // change brightness, and Low and Medium share the same buckets.
  const spotSlotsFor = (n) => Math.min(n <= 8 ? 8 : n <= 16 ? 16 : 32, MAX_SPOT_LIGHTS);
  const bounceSlotsFor = (n) => Math.min(n <= 6 ? 6 : n <= 16 ? 16 : 32, MAX_BOUNCE_LIGHTS);
  const shadowSlotsFor = (n) => (n <= 2 ? 2 : n <= 4 ? 4 : 8);
  // Which beam owns each spot slot: kept while that beam stays among the brightest, so pools
  // don't jump between fixtures every frame; intensities fade instead of popping (the "flashes").
  const spotOwner = new Array(MAX_SPOT_LIGHTS).fill(null);
  let lastLightingTime = performance.now();
  function updateBeamLighting(candidates) {
    const nowL = performance.now();
    const fade = Math.min(1, ((nowL - lastLightingTime) / 1000) * 12); // ~80 ms to settle
    lastLightingTime = nowL;
    const spotSlots = spotSlotsFor(currentRenderSettings.spot.maxLights);
    const spotBudget = Math.min(currentRenderSettings.spot.maxLights, spotSlots);
    const shadowSlots = currentRenderSettings.shadows.enabled ? Math.min(shadowSlotsFor(currentRenderSettings.shadows.maxShadowLights), spotSlots) : 0;
    const shadowBudget = Math.min(currentRenderSettings.shadows.maxShadowLights, shadowSlots);
    const top = pickTopByBrightness(candidates, spotBudget);
    const byHead = new Map(top.map((c) => [c.head.uuid, c]));
    for (let i = 0; i < spotOwner.length; i++) {
      if (spotOwner[i] && (i >= spotBudget || !byHead.has(spotOwner[i]))) spotOwner[i] = null;
    }
    const owned = new Set(spotOwner.filter(Boolean));
    top.forEach((c) => {
      if (owned.has(c.head.uuid)) return;
      const free = spotOwner.findIndex((o, i) => !o && i < spotBudget);
      if (free >= 0) {
        spotOwner[free] = c.head.uuid;
        owned.add(c.head.uuid);
      }
    });

    spotLightPool.forEach((light, i) => {
      const slot = i < spotSlots;
      if (light.visible !== slot) light.visible = slot;
      const casts = i < shadowSlots;
      if (light.castShadow !== casts) light.castShadow = casts;
      if (light.shadow && "intensity" in light.shadow) light.shadow.intensity = i < shadowBudget ? 1 : 0;
      const c = slot && spotOwner[i] ? byHead.get(spotOwner[i]) : null;
      const cur = light.userData.cur || 0;
      if (!slot || !c) {
        // fade out where it was
        light.userData.cur = cur * (1 - fade);
        light.intensity = light.userData.cur < 0.05 ? 0 : light.userData.cur;
        return;
      }
      // ~70 cd reads as a clear pool from a 14' truss without blowing nearby furniture out to white
      fitSpotLightToBeam(light, c.head, c.state, c.length, { intensityMul: 70 });
      light.userData.cur = cur + (light.intensity - cur) * fade;
      light.intensity = light.userData.cur;
    });

    const now = performance.now();
    if (now - lastBounceUpdate > beamHitThrottleMs()) { // ~10Hz, ~5Hz on Low - see beamHitThrottleMs
      lastBounceUpdate = now;
      const bounceSlots = bounceSlotsFor(currentRenderSettings.bounce.count);
      const bounceBudget = Math.min(currentRenderSettings.bounce.count, bounceSlots);
      const bounceTop = pickTopByBrightness(candidates, bounceBudget);
      bounceLightPool.forEach((light, i) => {
        const slot = i < bounceSlots;
        if (light.visible !== slot) light.visible = slot;
        const c = i < bounceBudget ? bounceTop[i] : null;
        if (!slot || !c) {
          light.intensity = 0;
          return;
        }
        _hitPoint.copy(c.worldPos).addScaledVector(c.worldDir, c.length);
        // Material-aware bounce (goal 2): tint/scale from the REAL hit surface
        // (its own colour x roughness/metalness-derived albedo), not a fixed
        // 0.5 grey guess - white satin bounces the beam's own colour strongly,
        // black-acoustic-foam almost none, mirror/gloss brightly.
        fitBounceLight(light, _hitPoint, c.state.color, c.state.dimmer, estimateSurfaceBounce(c.hitMaterial), currentRenderSettings.bounce.intensity);
      });
    }

    // ---- room glow: cheap GI approximation (goal 3) ------------------------
    // Sum of lit beams' colour*dimmer, weighted by the room's average surface
    // albedo (computed once per object rebuild - see rebuildObjects). Only the
    // TARGET is computed here (whenever fresh DMX state arrives); the actual
    // smoothing toward it happens every rendered FRAME in updateRoomGlow()
    // (called from the main loop() below), not here - this function only runs
    // when new DMX data arrives, which stops entirely once values go stable
    // (e.g. a function is stopped and every channel holds at 0), which would
    // otherwise freeze the smoothing partway through a fade instead of
    // finishing the decay to zero over wall-clock time.
    let gR = 0, gG = 0, gB = 0, gW = 0;
    candidates.forEach((c) => {
      const w = Math.max(0, c.state.dimmer || 0);
      if (w <= 0.003) return;
      _glowColorTmp.set(c.state.color || "#ffffff");
      gR += _glowColorTmp.r * w;
      gG += _glowColorTmp.g * w;
      gB += _glowColorTmp.b * w;
      gW += w;
    });
    if (gW > 0.0005) {
      _glowTargetColor.setRGB(gR / gW, gG / gW, gB / gW);
      // A fixed scale, NOT currentRenderSettings.bounce.intensity: that setting
      // is 0 at the Low preset (it gates the real bounce POINT LIGHTS, an
      // actual per-quality expense) which would make this ambient-only glow -
      // effectively free regardless of quality - always zero at Low too, the
      // exact opposite of "a bright look fills the club" at every quality tier.
      glowTargetIntensity = Math.min(1.1, gW * avgRoomAlbedo * 0.5);
    } else {
      glowTargetIntensity = 0;
    }
  }

  // Smooths roomGlow toward glowTargetIntensity/_glowTargetColor over ~0.3s of
  // real wall-clock time - called once per rendered frame (see loop() below),
  // NOT from updateBeamLighting, precisely so it keeps decaying to zero even
  // after DMX stops changing (a stopped function holds every channel at a
  // constant 0, so updateFixtureStates/updateBeamLighting may never run
  // again - the room must still finish fading to black on its own).
  function updateRoomGlow() {
    const nowG = performance.now();
    const dtG = Math.min(0.25, (nowG - lastGlowTime) / 1000);
    lastGlowTime = nowG;
    if (dtG <= 0) return;
    const kG = 1 - Math.exp(-dtG / 0.3); // ~0.3s smoothing time constant
    roomGlow.intensity += (glowTargetIntensity - roomGlow.intensity) * kG;
    if (roomGlow.intensity < 0.003) roomGlow.intensity = 0;
    if (roomGlow.intensity > 0) roomGlow.color.lerp(_glowTargetColor, kG);
  }

  function propDefLookupDefault(objInstance, propDefs) {
    return (propDefs && propDefs[objInstance.prop]) || null;
  }

  function rebuildObjects(objects, propDefLookup) {
    requestWarmup();
    clearGroup(objectsGroup);
    objectEntries.clear();
    (objects || []).forEach((obj) => {
      const def = propDefLookup(obj);
      if (!def) return;
      // Per-instance overrides (P's contract: objects[i].overrides =
      // {color?, partColors?, hiddenFaces?, visible?}) - fall back to the
      // legacy plain obj.color for any instance that predates overrides.
      const group = buildPropGroup(def, { overrides: obj.overrides || (obj.color ? { color: obj.color } : undefined) });
      group.userData.key = "object:" + obj.id;
      group.userData.kind = "object";
      group.userData.objectId = obj.id;
      group.userData.locked = !!obj.locked;
      group.position.copy(userToThree(obj.pos[0], obj.pos[1], obj.pos[2]));
      const scale = obj.scale || [1, 1, 1];
      group.scale.set(scale[0], scale[2], scale[1]);
      // item 11: objects now carry a full rot=[rx,ry,rz]; a legacy entry
      // with only `rz` (no `rot` array) is read as [0,0,rz].
      const rot = Array.isArray(obj.rot) ? obj.rot : [0, 0, obj.rz || 0];
      group.quaternion.copy(mountQuaternion(rot, "floor"));
      const label = makeLabel(obj.name || def.name);
      label.visible = labelsVisible;
      group.add(label);
      objectsGroup.add(group);
      objectEntries.set(obj.id, group);
    });
    // Perf: props/truss never move on their own between rebuilds - freeze
    // (see freezeStaticSubtree). A live gizmo/manipulation drag on one of
    // these groups mutates position/quaternion/scale directly and calls
    // updateMatrix() itself afterward (stage-editor.js), which still works
    // correctly with matrixAutoUpdate=false; the eventual commitDraftChange
    // triggers a full rebuildObjects() that re-freezes everything anyway.
    freezeStaticSubtree(objectsGroup);
    // Room glow (goal 3): the average surface albedo of the actual room -
    // computed once here (object rebuild is rare), not per frame.
    avgRoomAlbedo = computeAvgRoomAlbedo();
  }

  // Cheap, one-shot (rebuild-time only) average of every real object
  // material's estimateSurfaceBounce().albedo - used to weight the room glow
  // so a bright-material club (white satin/glossy tile) fills more than a
  // dark one (foam/matte black) under the same beam output.
  function computeAvgRoomAlbedo() {
    let sum = 0, n = 0;
    objectsGroup.traverse((o) => {
      if (!o.isMesh || !o.material) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      mats.forEach((m) => {
        if (!m || m.visible === false) return;
        sum += estimateSurfaceBounce(m).albedo;
        n++;
      });
    });
    return n ? sum / n : 0.45;
  }

  function showPropPreview() {
    /* hook for the props-mode live preview; stage-app.js wires the actual
       swap of the main viewport into "preview" mode when needed. */
  }

  function setLabelsVisible(visible) {
    labelsVisible = visible;
    fixtureEntries.forEach((fe) => (fe.label.visible = visible));
    objectEntries.forEach((g) => {
      g.children.forEach((c) => {
        if (c.isCSS2DObject) c.visible = visible;
      });
    });
    // The main loop now skips labelRenderer.render() entirely while
    // labelsVisible is false (item 4/perf) - CSS2DRenderer only applies a
    // CSS2DObject's .visible flag (show/hide its DOM element) from inside
    // that render() call, so without this one explicit call here, flipping
    // labelsVisible off would leave the last-rendered labels stuck on
    // screen instead of actually disappearing.
    labelRenderer.render(scene, camera);
  }

  // presets frame the room around its centre (the user origin is the room corner)
  const CAMERA_PRESETS = {
    foh: () => {
      const cx = (currentRoom.width * IN) / 2, cz = -(currentRoom.depth * IN) / 2, d = currentRoom.depth * IN;
      // just inside the front wall at standing eye height, looking across the room
      camera.position.set(cx, Math.min(1.8, currentRoom.height * IN * 0.8), cz + d * 0.47);
      orbit.target.set(cx, (currentRoom.height * IN) * 0.3, cz - d * 0.2);
    },
    top: () => {
      const cx = (currentRoom.width * IN) / 2, cz = -(currentRoom.depth * IN) / 2;
      const span = Math.max(currentRoom.width, currentRoom.depth) * IN;
      camera.position.set(cx, span * 1.25, cz + 0.001);
      orbit.target.set(cx, 0, cz);
    },
    side: () => {
      const cx = (currentRoom.width * IN) / 2, cz = -(currentRoom.depth * IN) / 2;
      const span = Math.max(currentRoom.width, currentRoom.depth) * IN;
      // just inside the right wall, raised, looking across
      camera.position.set(cx + (currentRoom.width * IN) * 0.46, Math.min(2.6, currentRoom.height * IN * 0.8), cz);
      orbit.target.set(cx - (currentRoom.width * IN) * 0.2, (currentRoom.height * IN) * 0.3, cz);
    },
    stage: () => {
      const cx = (currentRoom.width * IN) / 2, cz = -(currentRoom.depth * IN) / 2, d = currentRoom.depth * IN;
      // just inside the back wall, raised, looking toward the front
      camera.position.set(cx, Math.min(2.6, currentRoom.height * IN * 0.8), cz - d * 0.46);
      orbit.target.set(cx, (currentRoom.height * IN) * 0.3, cz + d * 0.2);
    },
  };

  function cameraPreset(name) {
    const fn = CAMERA_PRESETS[name];
    if (fn) {
      fn();
      orbit.update();
    }
  }

  function findKeyedAncestor(obj) {
    let o = obj;
    while (o) {
      if (o.userData && o.userData.key) return o;
      o = o.parent;
    }
    return null;
  }

  const raycaster = new THREE.Raycaster();
  function pick(clientX, clientY) {
    const rect = renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(ndc, camera);
    // Raycaster.intersectObjects trusts each object's matrixWorld as-is; it
    // is normally kept current by the render loop, but a pick can land
    // between a synchronous scene rebuild (e.g. right after
    // rebuildFixtures()/commitDraftChange()) and the next rendered frame -
    // force it current so picking is correct immediately, not "eventually".
    fixturesGroup.updateMatrixWorld(true);
    objectsGroup.updateMatrixWorld(true);
    const hits = raycaster.intersectObjects([fixturesGroup, objectsGroup], true);
    for (const h of hits) {
      if (isHitOnHiddenFace(h)) continue;
      const keyed = findKeyedAncestor(h.object);
      // locked items are not pickable in the viewport: clicks pass through
      // to whatever is behind them (or to empty space)
      if (keyed && !keyed.userData.locked) return keyed;
    }
    return null;
  }

  // The floor-plane (y=0) world point under the pointer, in user inches -
  // used for the empty-space "Add object" context menu and for the
  // Alt-drag horizontal-plane move (at an item's own height, see pickAtHeight).
  function pickGroundPoint(clientX, clientY) {
    return pickAtHeight(clientX, clientY, 0);
  }
  function pickAtHeight(clientX, clientY, heightThreeY) {
    const rect = renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(ndc, camera);
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -(heightThreeY || 0));
    const pt = new THREE.Vector3();
    const hit = raycaster.ray.intersectPlane(plane, pt);
    return hit ? threeToUser(pt) : null;
  }

  function mouseButtonName(v) {
    if (v === THREE.MOUSE.PAN) return "pan";
    if (v === THREE.MOUSE.ROTATE) return "rotate";
    if (v === THREE.MOUSE.DOLLY) return "dolly";
    return "none";
  }
  function getMouseConfig() {
    // Middle button is no longer an OrbitControls mode (it's owned by
    // stage-camera.js's mouselook, unless the operator's "middle-drag
    // orbits around the view centre" toggle asks for the old behaviour).
    return {
      left: mouseButtonName(orbit.mouseButtons.LEFT),
      middle: viewSettings.orbitMiddle ? "rotate" : "look",
      right: mouseButtonName(orbit.mouseButtons.RIGHT),
    };
  }

  function getBeamWorldDir(fixtureId, headIndex) {
    const fe = fixtureEntries.get(fixtureId);
    if (!fe) return null;
    const b = fe.beams[headIndex || 0];
    if (!b || !b.head) return null;
    fe.root.updateMatrixWorld(true);
    const q = new THREE.Quaternion();
    b.head.getWorldQuaternion(q);
    const dir = new THREE.Vector3(0, 1, 0).applyQuaternion(q);
    return [dir.x, dir.y, dir.z];
  }

  // On-demand beam frustum numbers for window.__stage.beamInfo(id) - not
  // tied to the per-frame render loop, so it reflects the current DMX state
  // whenever it's called.
  function getBeamGeometryInfo(fixtureId, headIndex, startInches, spreadDeg) {
    const fe = fixtureEntries.get(fixtureId);
    if (!fe) return null;
    const b = fe.beams[headIndex || 0];
    if (!b || !b.head) return null;
    fe.root.updateMatrixWorld(true);
    const pos = new THREE.Vector3();
    const quat = new THREE.Quaternion();
    b.head.getWorldPosition(pos);
    b.head.getWorldQuaternion(quat);
    const dir = new THREE.Vector3(0, 1, 0).applyQuaternion(quat);
    const maxLen = (viewSettings.beamLengthFt || 40) * FT * IN;
    const length = computeBeamLength(b.beam || {}, pos, dir, maxLen);
    const mul = viewSettings.beamWidthMul || 1;
    const nearRadiusThree = Math.max(((startInches || 0) * IN * mul) / 2, 0.005);
    const farRadiusThree = Math.max(
      nearRadiusThree + length * Math.tan(THREE.MathUtils.degToRad(((spreadDeg || 0) * mul) / 2)),
      nearRadiusThree
    );
    return { nearRadius: nearRadiusThree / IN, farRadius: farRadiusThree / IN, length: length / IN };
  }

  function getFixtureScreenPos(fixtureId) {
    const fe = fixtureEntries.get(fixtureId);
    if (!fe) return null;
    const v = new THREE.Vector3();
    fe.root.getWorldPosition(v);
    v.project(camera);
    const rect = renderer.domElement.getBoundingClientRect();
    return { x: rect.left + (v.x * 0.5 + 0.5) * rect.width, y: rect.top + (-v.y * 0.5 + 0.5) * rect.height };
  }

  // ---- camera history (undo/redo of orbit/pan/zoom gestures) -----------
  function snapshotCamera() {
    return { position: [camera.position.x, camera.position.y, camera.position.z], target: [orbit.target.x, orbit.target.y, orbit.target.z] };
  }
  function getCameraState() {
    return snapshotCamera();
  }
  function setCameraState(state) {
    if (!state) return;
    if (state.position) camera.position.set(state.position[0], state.position[1], state.position[2]);
    if (state.target) orbit.target.set(state.target[0], state.target[1], state.target[2]);
    orbit.update();
  }

  let onCameraHistory = null;
  function setOnCameraHistory(fn) {
    onCameraHistory = fn;
  }
  let cameraHistoryBefore = null;
  let cameraHistoryTimer = null;
  orbit.addEventListener("start", () => {
    if (!cameraHistoryBefore) cameraHistoryBefore = snapshotCamera();
  });
  orbit.addEventListener("end", () => {
    // OrbitControls fires a start+end pair per wheel tick too, so a rapid
    // burst of ticks (or a drag followed by ticks) collapses into one
    // history entry by debouncing the actual commit.
    clearTimeout(cameraHistoryTimer);
    cameraHistoryTimer = setTimeout(() => {
      if (cameraHistoryBefore && onCameraHistory) {
        onCameraHistory(cameraHistoryBefore, snapshotCamera());
      }
      cameraHistoryBefore = null;
      cameraHistoryTimer = null;
    }, 350);
  });

  // WASD/QE fly, middle-drag mouselook and wheel handling used to live here;
  // they now live entirely in stage-camera.js (stage-app.js wires it up,
  // passing its own toast()/isBlocked()/onHistory() callbacks directly, and
  // drives it every frame via app.cameraControls.update(dt)). Nothing in
  // this module needs a speed-toast hook or its own isFlyActive() any more -
  // stage-editor.js's Shift-for-rotate modifier now asks app.isFlyActive()
  // (stage-app.js), which defers to app.cameraControls instead of this file.

  let animHandle = null;
  let lastFrameTime = performance.now();
  let frameCount = 0;
  let fps = 0;
  let fpsTimer = performance.now();
  let onFrame = null;
  let statsJsMs = 0; // item 8 - see getRenderStats()
  let lastLabelRenderTime = 0;
  const LABEL_RENDER_INTERVAL_MS = 100; // idle refresh while labels are visible (item 4)
  const lastLabelCamera = new Float32Array(32);
  function cameraChangedSinceLabels() {
    const m = camera.matrixWorld.elements;
    const p = camera.projectionMatrix.elements;
    let changed = false;
    for (let i = 0; i < 16; i++) {
      if (lastLabelCamera[i] !== m[i] || lastLabelCamera[16 + i] !== p[i]) {
        changed = true;
        lastLabelCamera[i] = m[i];
        lastLabelCamera[16 + i] = p[i];
      }
    }
    return changed;
  }

  function setOnFrame(fn) {
    onFrame = fn;
  }

  // ---- shader warm-up ----
  // three.js compiles a material's shader the first time it is drawn, and again whenever the
  // light setup changes. On integrated GPUs (ANGLE/D3D11) that blocked the page for seconds:
  // turning to face new furniture, a seat change, or a graphics setting change froze it.
  // Instead, compile everything in the background (compileAsync) and skip drawing until done.
  // `var` + lazy note: requestWarmup() may run (from setRenderSettings) before this point executes
  var warming = false;
  var warmAgain = false;
  var warmTimer = null;
  var warmPending = false; // a warm-up was requested: don't draw new materials before it compiles them
  var warmNote = null;
  function getWarmNote() {
    if (!warmNote) {
      warmNote = document.createElement("div");
      warmNote.textContent = "Preparing lights and materials…";
      warmNote.style.cssText = "position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);padding:10px 16px;" +
        "border-radius:8px;background:rgba(12,14,20,.85);color:#cfd6e4;font:13px system-ui,sans-serif;pointer-events:none;display:none;z-index:5";
      container.appendChild(warmNote);
    }
    return warmNote;
  }
  // Issue every shader compile, then wait (without drawing) until the GPU reports them all done.
  // three's compileAsync crashed in its own polling ("reading 'isReady'") and never settled, and
  // drawing before the compiles finish blocks the page on the first use of each program.
  // KHR_parallel_shader_compile lets us poll completion without blocking; without it,
  // renderer.compile() simply blocks once.
  const parallelCompile = renderer.getContext().getExtension("KHR_parallel_shader_compile");
  function programsPending() {
    if (!parallelCompile) return 0;
    const gl = renderer.getContext();
    let n = 0;
    (renderer.info.programs || []).forEach((p) => {
      if (p && p.program && !gl.getProgramParameter(p.program, parallelCompile.COMPLETION_STATUS_KHR)) n++;
    });
    return n;
  }
  // renderer.compile() only visits visible objects, and textures upload on first draw: both then
  // happened mid-show (a beam lighting up, a seat facing new furniture) and froze the page. So the
  // warm-up briefly unhides hidden meshes (never lights: that would change the light layout the
  // shaders are compiled for) and uploads every texture while drawing is paused.
  function prepareForCompile(root) {
    const unhidden = [];
    root.traverse((o) => {
      if (!o.visible && !o.isLight && (o.isMesh || o.isPoints || o.isLine || o.isSprite ||
          (o.isGroup && o.getObjectsByProperty("isLight", true).length === 0))) {
        unhidden.push(o);
        o.visible = true;
      }
      const mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
      mats.forEach((m) => {
        for (const k in m) {
          const v = m[k];
          if (v && v.isTexture && !v.isRenderTargetTexture && v.image) {
            try {
              renderer.initTexture(v);
            } catch (e) {
              /* a texture that can't upload yet will upload on first draw */
            }
          }
        }
      });
    });
    return () => unhidden.forEach((o) => (o.visible = false));
  }
  function compileWithin(target, sceneForLights, ms) {
    const restore = prepareForCompile(target);
    const prevTarget = renderer.getRenderTarget();
    renderer.setRenderTarget(pipeline.getSceneTarget ? pipeline.getSceneTarget() : null);
    try {
      renderer.compile(target, camera, sceneForLights || null);
    } catch (e) {
      console.warn("stage-scene: shader warm-up", e);
    }
    renderer.setRenderTarget(prevTarget);
    restore();
    const t0 = performance.now();
    return new Promise((resolve) => {
      (function poll() {
        const left = programsPending();
        if (warmNote && warming) warmNote.textContent = "Preparing lights and materials…" + (left ? " (" + left + " left)" : "");
        if (left === 0 || performance.now() - t0 > ms) return resolve();
        setTimeout(poll, 60);
      })();
    });
  }
  function requestWarmup() {
    warmPending = true;
    clearTimeout(warmTimer);
    warmTimer = setTimeout(runWarmup, 200);
  }
  async function runWarmup() {
    if (warming) {
      warmAgain = true;
      return;
    }
    warming = true;
    warmPending = false;
    const shown = setTimeout(() => (getWarmNote().style.display = "block"), 300); // only for slow compiles
    await compileWithin(scene, null, 90000);
    clearTimeout(shown);
    getWarmNote().style.display = "none";
    warming = false;
    if (warmAgain) {
      warmAgain = false;
      requestWarmup();
    }
  }
  setModelWarmHook((root) => compileWithin(root, scene, 60000));

  var depthWasBound = false;
  function loop() {
    animHandle = requestAnimationFrame(loop);
    if (document.hidden) return; // also fully paused - see start()/dispose()
    if (warming || warmPending) return; // drawing now would compile shaders synchronously - wait for the warm-up
    const now = performance.now();
    // Frame limiter (item 6): honour the quality preset's targetFps (Low =
    // 30) by skipping whole rAF ticks rather than rendering every one the
    // browser offers - on a 60Hz+ display this halves both CPU (everything
    // below, including the per-frame DMX/beam/orbit work) and GPU work on
    // Low without changing anything about how a rendered frame looks.
    const targetFps = (currentRenderSettings && currentRenderSettings.targetFps) || 60;
    const minIntervalMs = 1000 / targetFps;
    if (now - lastFrameTime < minIntervalMs - 1) return;
    // Real rAF-to-rAF interval, clamped so a multi-second warm-up pause never
    // reads as one giant frame - see the note above pipeline.render() below
    // for why dynamic resolution needs THIS (not a CPU-side render-call time).
    const dt = Math.min((now - lastFrameTime) / 1000, 0.1);
    lastFrameTime = now;
    frameCount++;
    if (now - fpsTimer > 500) {
      fps = (frameCount * 1000) / (now - fpsTimer);
      frameCount = 0;
      fpsTimer = now;
    }
    const jsStart = performance.now(); // item 8: "JS ms/frame" - update work only, not the render call below
    orbit.update();
    if (onFrame) onFrame(dt);

    // Bind this frame's depth pre-pass texture (stage-render.js) to every
    // active beam so its soft-intersection fade reads real scene depth -
    // skipped entirely (uDepthTexture stays null => no fade) once volumetric
    // beams are off or the mode hides beams anyway.
    const useDepth = currentRenderSettings.beams.volumetric && (currentRenderMode === "lit" || currentRenderMode === "beams");
    if (!useDepth && depthWasBound) {
      // volumetric turned off: stop fading against a stale depth texture
      depthWasBound = false;
      fixtureEntries.forEach((fe) => fe.beams.forEach((b) => b.beam && updateBeamDepthUniforms(b.beam, camera, null, pipeline.getResolution(), null)));
    }
    if (useDepth) {
      depthWasBound = true;
      const depthTex = pipeline.getDepthTexture();
      const res = pipeline.getResolution();
      fixtureEntries.forEach((fe) => {
        fe.beams.forEach((b) => {
          if (b.beam) updateBeamDepthUniforms(b.beam, camera, depthTex, res, 0.15 + currentRenderSettings.beams.softness * 0.5);
        });
      });
    }
    statsJsMs = performance.now() - jsStart;

    updateRoomGlow(); // real per-frame decay, independent of DMX push cadence - see its own comment
    updateCubeCamera(); // no-op unless reflections.cube is on; throttled to <=4Hz internally
    // dt*1000 = the real rAF-to-rAF interval (clamped, see above) - this is
    // what dynamic resolution must react to (see stage-render.js render()).
    pipeline.render(dt, dt * 1000);

    // CSS2DRenderer.render() repositions every label's DOM element. Skip it when labels are off;
    // when on, follow the camera on EVERY frame it moves (a 10 Hz cap made labels stutter behind
    // the view) and otherwise refresh at 10 Hz to pick up edits.
    if (labelsVisible) {
      const cameraMoved = cameraChangedSinceLabels();
      if (cameraMoved || now - lastLabelRenderTime >= LABEL_RENDER_INTERVAL_MS) {
        lastLabelRenderTime = now;
        labelRenderer.render(scene, camera);
      }
    }
  }

  function start() {
    resize();
    lastFrameTime = performance.now();
    requestWarmup();
    if (!animHandle) loop();
  }

  function dispose() {
    if (animHandle) cancelAnimationFrame(animHandle);
    pipeline.dispose();
    renderer.dispose();
  }

  window.addEventListener("resize", resize);

  setRenderSettings({}); // apply DEFAULT_RENDER_SETTINGS to the pipeline/lights up front (roomGroup etc. now exist)

  return {
    scene,
    camera,
    renderer,
    labelRenderer,
    orbit,
    transform,
    setRoom,
    setAnchor,
    rebuildFixtures,
    updateFixtureStates,
    rebuildObjects,
    propDefLookupDefault,
    showPropPreview,
    setLabelsVisible,
    setHouseLights(on) {
      houseLightsOn = !!on;
      applyWorkLight();
    },
    getHouseLights() {
      return houseLightsOn;
    },
    cameraPreset,
    pick,
    pickGroundPoint,
    pickAtHeight,
    getMouseConfig,
    setOrbitSuppressed,
    getBeamWorldDir,
    getBeamGeometryInfo,
    pendingLooks,
    getFixtureLookInfo,
    getFixtureScreenPos,
    getCameraState,
    setCameraState,
    setOnCameraHistory,
    setViewSettings,
    getViewSettings,
    resetViewSettings,
    resize,
    start,
    dispose,
    setOnFrame,
    getFps: () => fps,
    fixtureEntries,
    objectEntries,
    // ---- render API (R group - see .claude/memory/stage-visualizer.md) ---
    setRenderSettings,
    getRenderSettings,
    getRenderStats,
    setRenderMode,
    getRenderMode,
  };
}
