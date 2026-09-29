/*
  stage-props.js

  Room object "props" - furniture/decor/truss/etc built from primitives,
  realistic truss (stage-truss.js), downloaded models (GLTFLoader) or LED
  strips (stage-ledstrip.js), saved by name to the shared prop library
  (%USERPROFILE%\QLC+\stage-props.json via QLC+API|saveProps), and placed
  as instances in the stage draft (webaccess/res/stage-editor.js handles
  moving/selecting instances; this file owns their *definition*: parts,
  the starter library, and wires the Props side panel's library list -
  the full parts editor lives in stage-propeditor.js).

  A prop's origin is its bottom centre. Part `pos` is the part's centre
  relative to that origin, in inches (EXCEPT "model" parts, where `pos` is
  the part's *base* point in X/Y and its floor contact height in Z - see
  createPartMesh). Cylinder/cone/sphere `size` is [diameter, diameter, height].
*/

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { parseLength, formatLength } from "./stage-units.js";
import { buildTruss, TRUSS_TYPES } from "./stage-truss.js";
import { acquireTexture, composeFilters, isDefaultFilters, applyColorFilters } from "./stage-textures.js";

export const IN = 0.0254;
export const PART_SHAPES = ["box", "cylinder", "sphere", "cone", "plane", "torus"];
// Shapes added by the overnight "realism" pass - listed separately so the
// old primitive dropdown (PART_SHAPES) stays stable for any code that still
// reads it, while the new prop editor offers all of them.
export const EXTRA_PART_SHAPES = ["model", "truss", "ledstrip"];
export const ALL_PART_SHAPES = PART_SHAPES.concat(EXTRA_PART_SHAPES);
export { TRUSS_TYPES };
export const MATERIALS = [
  "matte", "gloss", "metal", "glass", "led",
  // realistic surface presets (materials/bounce pass) - real-world-ish PBR values,
  // see makeMaterial() below. Textured ones (MATERIAL_TEXTURE_SLUG) tile by the part's
  // own size in inches; the rest stay flat PBR colour (correct look for paint/foam/mirror).
  "paint-matte", "paint-satin", "black-acoustic-foam", "concrete",
  "brick-red", "brick-dark", "brick-white",
  "wood-panel", "glossy-tile", "fabric", "mirror",
];

// "Visible from" (material side) - only meaningful for one-sided-ish shapes
// (planes); everything else defaults to "both" (double-sided, the old look).
export const SIDE_OPTIONS = ["front", "back", "both"];

// Per-shape hideable faces, in the exact order their geometry's material
// groups use them (see makeGeometry/createPartMesh below) - a face in a
// part's `hiddenFaces` gets a material with visible:false, so it renders
// as nothing and is skipped by picking/beam raycasts (stage-scene.js).
export const FACE_LABELS = {
  box: [
    { id: "right", label: "Right" },
    { id: "left", label: "Left" },
    { id: "top", label: "Top" },
    { id: "bottom", label: "Bottom" },
    { id: "front", label: "Front" },
    { id: "back", label: "Back" },
  ],
  cylinder: [
    { id: "side", label: "Side" },
    { id: "top", label: "Top cap" },
    { id: "bottom", label: "Bottom cap" },
  ],
  cone: [
    { id: "side", label: "Side" },
    { id: "bottom", label: "Bottom cap" },
  ],
};
// BoxGeometry/CylinderGeometry/ConeGeometry always add their material
// groups in this fixed order (three.js core geometries), regardless of
// which caps actually exist - see the three.js source for *Geometry.js.
const FACE_GROUP_INDEX = {
  box: { right: 0, left: 1, top: 2, bottom: 3, front: 4, back: 5 },
  cylinder: { side: 0, top: 1, bottom: 2 },
  cone: { side: 0, bottom: 2 },
};
export const CATEGORIES = [
  "table",
  "booth",
  "bar",
  "stage",
  "wall",
  "truss",
  "screen",
  "speaker",
  "seating",
  "led",
  "decor",
  "floor_area",
  "point",
];

// Same axis convention as stage-scene.js: three(x,y,z) = user(x,z,-y) * 0.0254.
// Duplicated here (rather than imported) to keep stage-props.js usable
// without pulling in the whole scene module.
export function userToThreeLocal(x, y, z) {
  return new THREE.Vector3(x * IN, z * IN, -y * IN);
}

// Composes the same "Rz(yaw)*Ry(pitch)*Rx(roll)" user-axis rotation used
// for fixtures (see stage-scene.js), without a hang term - used for both
// part rotations (local to a prop) and room-object yaw.
export function composeLocalQuaternion(rx, ry, rz) {
  const yaw = THREE.MathUtils.degToRad(rz || 0);
  const pitch = THREE.MathUtils.degToRad(ry || 0);
  const roll = THREE.MathUtils.degToRad(rx || 0);
  const my = new THREE.Matrix4().makeRotationY(yaw);
  const mz = new THREE.Matrix4().makeRotationZ(-pitch);
  const mx = new THREE.Matrix4().makeRotationX(roll);
  const m = new THREE.Matrix4().multiplyMatrices(my, mz).multiply(mx);
  return new THREE.Quaternion().setFromRotationMatrix(m);
}

// Inverse of composeLocalQuaternion (up to gimbal lock) - used by the prop
// editor to write a dragged TransformControls rotation back into [rx,ry,rz].
// Verified against composeLocalQuaternion with a node script: Euler order
// "YZX" gives ex=rx, ey=rz, ez=-ry exactly.
export function decomposeLocalQuaternion(quat) {
  const e = new THREE.Euler().setFromQuaternion(quat, "YZX");
  return [THREE.MathUtils.radToDeg(e.x), -THREE.MathUtils.radToDeg(e.z), THREE.MathUtils.radToDeg(e.y)];
}

function sideConstant(side) {
  if (side === "front") return THREE.FrontSide;
  if (side === "back") return THREE.BackSide;
  return THREE.DoubleSide;
}

// ---------------------------------------------- material-aware bounce/env --
// Materials tagged .userData.reflective=true are the ones stage-scene.js's
// optional CubeCamera (goal 4, "reflections.cube") applies an envMap to -
// mirror/metal/glossy-tile/gloss only, per the spec ("mirror/glossy/metal/
// glossy-tile materials only"). stage-scene.js registers the live source via
// setEnvMapSource() once; makeMaterial() reads it at creation time so a
// material built after the cube camera exists gets the map immediately, and
// stage-scene.js re-applies it to already-built materials when the cube
// camera setting itself changes (one warm-up recompile, never per frame).
const REFLECTIVE_MATERIALS = new Set(["gloss", "metal", "mirror", "glossy-tile"]);
let envMapGetter = null;
export function setEnvMapSource(fn) {
  envMapGetter = fn;
}
// Called (stage-scene.js -> requestWarmup) whenever a texture finishes
// loading and gets applied to an already-built material, so the "with map"
// shader variant gets compiled in the background instead of on first draw.
let assetReadyHook = null;
export function setAssetReadyHook(fn) {
  assetReadyHook = fn;
}

// Colour-texture presets (goal 1): CC0 sets fetched by
// tools/stagelib/fetch_textures.py into stage-lib/textures/<slug>/ +
// index.json - the SAME library the free per-part texture picker
// (stage-textures.js/stage-propeditor.js "Texture" field) reads from, so a
// preset material and a hand-picked library texture share one cache/one
// loader (see applyTexture below). Presets NOT listed here
// (paint-matte/paint-satin/black-acoustic-foam/mirror) stay flat PBR colour
// on purpose - a photo texture doesn't help a flat-paint or foam-wedge look,
// and mirror needs envMap, not a diffuse photo.
const MATERIAL_TEXTURE_SLUG = {
  concrete: "concrete",
  "brick-red": "brick-red",
  "brick-dark": "brick-dark",
  "brick-white": "brick-white",
  "wood-panel": "wood-panel",
  "glossy-tile": "glossy-tile",
  fabric: "fabric",
};

// Every textured preset/free-picked texture uses the SAME material type
// (MeshStandardMaterial) with just `.map` set - no normal/roughness maps on
// Low/Medium (goal 1: "keep shader variants few") - so this only ever
// adds/replaces one texture slot. `filters` (stage-textures.js) are baked
// into the shared cached texture BEFORE it's cloned here (never a new
// shader/uniform) - see acquireTexture's refcounted bake cache.
function applyTexture(material, slug, part, filters, tileIn, rotDeg) {
  if (!slug) return;
  trackPending(
    acquireTexture(slug, filters).then((base) => {
      if (!base) return;
      const size = (part && part.size) || [base.tileX, base.tileY, base.tileX];
      const tileX = (tileIn && tileIn[0]) || base.tileX;
      const tileY = (tileIn && tileIn[1]) || base.tileY;
      const w = size[0] || tileX;
      const h = (size[2] !== undefined ? size[2] : size[1]) || tileY;
      const tex = base.tex.clone();
      tex.needsUpdate = true;
      if (rotDeg === 90) {
        tex.center.set(0.5, 0.5);
        tex.rotation = Math.PI / 2;
        tex.repeat.set(Math.max(0.25, h / tileX), Math.max(0.25, w / tileY));
      } else {
        tex.repeat.set(Math.max(0.25, w / tileX), Math.max(0.25, h / tileY));
      }
      // Releasing this clone (three.js's own disposeObject3D, stage-scene.js,
      // calls `material.map.dispose()`) also drops the shared bake's refcount
      // - see stage-textures.js's acquireTexture doc comment.
      const origDispose = tex.dispose.bind(tex);
      tex.dispose = () => {
        origDispose();
        base.release();
      };
      material.map = tex;
      material.needsUpdate = true;
      if (assetReadyHook) assetReadyHook();
    })
  );
}

// Resolves the effective texture/tile-size/rotation/filters a part should
// render with, composing (in order, later wins - "object-over-part"):
// the part definition's own fields < an instance's per-part override
// (`overrides.partLooks[partId]`, already merged with the instance's
// object-wide `overrides.filters` by buildPropGroup below) < nothing further.
// Always returns a non-null object (filters is always fully normalized).
function resolveEffectiveLook(part, instanceLook) {
  const il = instanceLook || {};
  const texture = Object.prototype.hasOwnProperty.call(il, "texture") ? il.texture : part.texture;
  const tileIn = il.tileIn || part.tileIn || null;
  const rot = il.rot !== undefined ? il.rot : part.textureRot || 0;
  const filters = composeFilters(part.filters, il.filters);
  return { texture: texture || null, tileIn: tileIn, rot: rot, filters: filters };
}

// Real-world-ish PBR presets (goal 1). `part` is optional (only used for
// texture repeat sizing - see applyTexture) so existing call sites that
// don't have a part handy (e.g. LED strip placeholders) keep working.
// `look` (texture library / picker pass, optional) is the result of
// resolveEffectiveLook() - a free-picked texture overrides the preset's own
// tiled texture (if any); its `filters` always apply. On a flat colour (no
// texture at all) filters adjust the colour itself (stage-textures.js
// applyColorFilters); on a textured part they're baked into the shared,
// cached, cloned texture (applyTexture) - never a new shader/material type.
function makeMaterial(matName, colorHex, side, part, look) {
  const presetSlug = MATERIAL_TEXTURE_SLUG[matName] || null;
  const textureSlug = (look && look.texture) || presetSlug;
  let colorInput = colorHex || "#888888";
  if (look && look.filters && textureSlug == null && !isDefaultFilters(look.filters)) {
    // No photo at all (flat preset colour) - filters act directly on the colour.
    colorInput = applyColorFilters(colorInput, look.filters);
  }
  const color = new THREE.Color(colorInput);
  // THREE.FrontSide is 0, so `side || DoubleSide` turned "visible from front" into double-sided
  const s = side === undefined || side === null ? THREE.DoubleSide : side;
  let mat;
  switch (matName) {
    case "gloss":
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.32, metalness: 0.05, side: s }); // satin, not a mirror
      break;
    case "metal":
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.35, metalness: 0.9, side: s });
      break;
    case "glass": {
      const Ctor = THREE.MeshPhysicalMaterial || THREE.MeshStandardMaterial;
      mat = new Ctor({ color: color, roughness: 0.05, metalness: 0, transparent: true, opacity: 0.35, side: s });
      break;
    }
    case "led":
      mat = new THREE.MeshStandardMaterial({ color: color, emissive: color, emissiveIntensity: 1.4, roughness: 0.6, side: s });
      break;
    case "paint-matte":
      // flat interior wall paint - real matte reflectance, no specular pop
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.92, metalness: 0.0, side: s });
      break;
    case "paint-satin":
      // satin/eggshell wall paint - common club wall finish, a soft broad highlight
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.55, metalness: 0.0, side: s });
      break;
    case "black-acoustic-foam":
      // wedge foam behind the DJ booth: very high roughness, near-zero reflectance by
      // design (should read as "almost no bounce" - see stage-beams.js estimateSurfaceBounce)
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.98, metalness: 0.0, side: s });
      break;
    case "concrete":
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.85, metalness: 0.02, side: s });
      break;
    case "brick-red":
    case "brick-dark":
    case "brick-white":
      // exposed/painted brick wall - shared PBR values, different texture
      // slug per variant (see MATERIAL_TEXTURE_SLUG); `color` tints the photo,
      // so leave it near-white (part.color) unless overridden per instance.
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.88, metalness: 0.0, side: s });
      break;
    case "wood-panel":
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.5, metalness: 0.0, side: s });
      break;
    case "glossy-tile":
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.18, metalness: 0.05, side: s });
      break;
    case "fabric":
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.85, metalness: 0.0, side: s });
      break;
    case "mirror":
      // polished mirror strip: near-zero roughness, full metalness - reads bright and
      // colour-tinted under coloured beams (see fitBounceLight/estimateSurfaceBounce)
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.05, metalness: 1.0, side: s });
      break;
    case "matte":
    default:
      mat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.9, metalness: 0.0, side: s });
      break;
  }
  mat.userData.materialPreset = matName;
  if (REFLECTIVE_MATERIALS.has(matName)) {
    mat.userData.reflective = true;
    const env = envMapGetter && envMapGetter();
    if (env) mat.envMap = env;
  }
  applyTexture(mat, textureSlug, part, look && look.filters, look && look.tileIn, look && look.rot);
  if (look && look.filters && look.filters.gloss != null) {
    mat.roughness = THREE.MathUtils.clamp(1 - look.filters.gloss, 0.02, 1);
  }
  return mat;
}

// A hidden face still exists as geometry (so we don't have to rebuild the
// mesh's vertex/index buffers) but renders nothing and, per
// isHitOnHiddenFace() in stage-scene.js, is excluded from picking and beam
// hit-testing.
function hiddenFaceMaterial() {
  return new THREE.MeshBasicMaterial({ visible: false });
}

function makeGeometry(part) {
  const size = part.size || [12, 12, 12];
  const w = size[0] * IN;
  const d = (size[1] === undefined ? size[0] : size[1]) * IN;
  const h = (size[2] === undefined ? size[0] : size[2]) * IN;
  let geo;
  switch (part.shape) {
    case "cylinder":
      geo = new THREE.CylinderGeometry(w / 2, w / 2, h, 24);
      break;
    case "sphere":
      geo = new THREE.SphereGeometry(w / 2, 20, 16);
      break;
    case "cone":
      geo = new THREE.ConeGeometry(w / 2, h, 24);
      break;
    case "plane":
      geo = new THREE.PlaneGeometry(w, h);
      break;
    case "torus": {
      const R = w / 2;
      const r = Math.max(h / 2, R * 0.12);
      geo = new THREE.TorusGeometry(Math.max(R - r, 0.01), r, 12, 24);
      break;
    }
    case "box":
    default:
      geo = new THREE.BoxGeometry(w, h, d);
      break;
  }
  return geo;
}

// --------------------------------------------------------- model parts --
// "model" parts load a downloaded glTF (agent A's /stage-lib/props/*)
// looked up from index.json by `part.modelId` at load time (so a starter
// prop keeps working whether or not that particular asset has been
// downloaded yet - it just shows a placeholder box instead). `part.url`
// is accepted directly too, for hand-authored parts that don't go through
// the index.
// Tracks in-flight async part loads (model glTFs, dynamic-imported LED strip
// module) so a test harness can wait for everything to settle after a scene
// load (window.__stage.assetsPending(), stage-app.js) instead of guessing
// with a fixed sleep. Truss parts are built synchronously (stage-truss.js)
// so they never touch this counter.
let pendingAssetCount = 0;
export function pendingAssets() {
  return pendingAssetCount;
}
function trackPending(promise) {
  pendingAssetCount++;
  const done = () => {
    pendingAssetCount = Math.max(0, pendingAssetCount - 1);
  };
  promise.then(done, done);
  return promise;
}

let modelIndexPromise = null;
export function loadPropModelIndex() {
  if (!modelIndexPromise) {
    modelIndexPromise = fetch("/stage-lib/props/index.json")
      .then((r) => (r && r.ok ? r.json() : { version: 1, models: [] }))
      .catch(() => ({ version: 1, models: [] }));
  }
  return modelIndexPromise;
}

function resolveModelUrl(part) {
  if (part.url) return Promise.resolve(part.url);
  if (!part.modelId) return Promise.resolve(null);
  return loadPropModelIndex().then((idx) => {
    const entry = (idx.models || []).find((m) => m.id === part.modelId);
    return entry ? entry.url : null;
  });
}

const modelTemplateCache = new Map(); // url -> Promise<THREE.Object3D> (unscaled template scene)

// The scene registers a hook that compiles a freshly loaded model's shaders in the background
// (renderer.compileAsync) before any copy of it is shown. Otherwise the first frame that draws
// it compiles them synchronously, which froze integrated GPUs for seconds.
let modelWarmHook = null;

// Every glTF comes with its own mix of normal/roughness/metalness/occlusion maps, and each mix is a
// separate PBR shader: ~20 variants that took integrated GPUs tens of seconds to compile. Keep the
// colour texture and the material's base values, drop the detail maps (barely visible in a dark
// club), so all models share one or two shader variants and use far less GPU memory.
function simplifyModelMaterials(root) {
  const done = new Map();
  root.traverse((o) => {
    if (!o.isMesh || !o.material) return;
    const simplify = (m) => {
      if (done.has(m)) return done.get(m);
      const out = new THREE.MeshStandardMaterial({
        name: m.name,
        color: m.color ? m.color.clone() : 0xffffff,
        map: m.map || null,
        roughness: m.roughness != null ? m.roughness : 0.8,
        metalness: m.metalness != null ? m.metalness : 0,
        transparent: !!m.transparent,
        opacity: m.opacity != null ? m.opacity : 1,
        alphaTest: m.alphaTest || 0,
        side: m.side,
      });
      done.set(m, out);
      m.dispose();
      [m.normalMap, m.roughnessMap, m.metalnessMap, m.aoMap, m.emissiveMap].forEach((t) => t && t !== m.map && t.dispose());
      return out;
    };
    o.material = Array.isArray(o.material) ? o.material.map(simplify) : simplify(o.material);
  });
}
export function setModelWarmHook(fn) {
  modelWarmHook = fn;
}
function loadModelTemplate(url) {
  if (!modelTemplateCache.has(url)) {
    const slash = url.lastIndexOf("/");
    const base = slash === -1 ? "" : url.slice(0, slash + 1);
    const file = slash === -1 ? url : url.slice(slash + 1);
    modelTemplateCache.set(
      url,
      new Promise((resolve, reject) => {
        const loader = new GLTFLoader();
        // .gltf assets carry sibling .bin/texture files - resolve them
        // relative to the model's own folder, not the page.
        if (base) loader.setPath(base);
        loader.load(
          file,
          (gltf) => {
            const root = gltf.scene || (gltf.scenes && gltf.scenes[0]);
            if (root) simplifyModelMaterials(root);
            if (!modelWarmHook || !root) return resolve(root);
            Promise.resolve(modelWarmHook(root)).then(() => resolve(root), () => resolve(root));
          },
          undefined,
          reject
        );
      })
    );
  }
  return modelTemplateCache.get(url);
}

// Uniform-scales `obj` so its bounding box fits within [w,h,d] (inches-in-
// three-units, i.e. already *IN) without distorting proportions, then
// centres it horizontally and drops its bottom to local y=0.
function fitModelAndBaseAlign(obj, w, h, d) {
  const box = new THREE.Box3().setFromObject(obj);
  const size = new THREE.Vector3();
  box.getSize(size);
  const ratio = Math.min(
    size.x > 1e-6 ? w / size.x : Infinity,
    size.y > 1e-6 ? h / size.y : Infinity,
    size.z > 1e-6 ? d / size.z : Infinity
  );
  const scale = isFinite(ratio) && ratio > 0 ? ratio : 1;
  obj.scale.setScalar(scale);
  obj.updateMatrixWorld(true);
  const box2 = new THREE.Box3().setFromObject(obj);
  const center = new THREE.Vector3();
  box2.getCenter(center);
  obj.position.x -= center.x;
  obj.position.z -= center.z;
  obj.position.y -= box2.min.y;
}

function placeholderModelMesh(w, h, d, failed) {
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(Math.max(w, 0.01), Math.max(h, 0.01), Math.max(d, 0.01)),
    new THREE.MeshStandardMaterial({
      color: failed ? 0x884433 : 0x556070,
      roughness: 0.85,
      transparent: true,
      opacity: 0.55,
    })
  );
  mesh.position.set(0, h / 2, 0);
  return mesh;
}

function createModelPartMesh(part, pos, rot) {
  const wrapper = new THREE.Group();
  wrapper.userData.partId = part.id;
  wrapper.position.copy(userToThreeLocal(pos[0], pos[1], pos[2]));
  wrapper.quaternion.copy(composeLocalQuaternion(rot[0], rot[1], rot[2]));

  const size = part.size || [24, 24, 36];
  const w = size[0] * IN;
  const d = (size[1] === undefined ? size[0] : size[1]) * IN;
  const h = (size[2] === undefined ? size[0] : size[2]) * IN;
  let placeholder = placeholderModelMesh(w, h, d, false);
  wrapper.add(placeholder);

  trackPending(
    resolveModelUrl(part)
      .then((url) => (url ? loadModelTemplate(url) : null))
      .then((template) => {
        if (!template || wrapper.userData.disposed) return;
        const inst = template.clone(true);
        fitModelAndBaseAlign(inst, w, h, d);
        wrapper.remove(placeholder);
        wrapper.add(inst);
      })
      .catch((err) => {
        console.warn("stage-props: model load failed for part", part.id, part.modelId || part.url, err);
        if (wrapper.userData.disposed) return;
        wrapper.remove(placeholder);
        placeholder = placeholderModelMesh(w, h, d, true);
        wrapper.add(placeholder);
      })
  );

  return wrapper;
}

// --------------------------------------------------------- truss parts --

function createTrussPartMesh(part, pos, rot, colorOverride) {
  const wrapper = new THREE.Group();
  wrapper.userData.partId = part.id;
  wrapper.position.copy(userToThreeLocal(pos[0], pos[1], pos[2]));
  wrapper.quaternion.copy(composeLocalQuaternion(rot[0], rot[1], rot[2]));
  const mesh = buildTruss(part);
  if (colorOverride) mesh.material.color.set(colorOverride);
  wrapper.add(mesh);
  return wrapper;
}

// -------------------------------------------------------- ledstrip parts --
// Rendering realism (emitter shape, per-LED glow) belongs to agent R's
// stage-ledstrip.js; this is a dynamic import so a build without that file
// yet still shows a plain emissive bar instead of failing to load.

function createLedstripPartMesh(part, pos, rot, colorOverride) {
  const wrapper = new THREE.Group();
  wrapper.userData.partId = part.id;
  wrapper.position.copy(userToThreeLocal(pos[0], pos[1], pos[2]));
  wrapper.quaternion.copy(composeLocalQuaternion(rot[0], rot[1], rot[2]));

  const lengthIn = part.length || 39.37; // ~1m
  const color = colorOverride || part.color || "#ffffff";
  let placeholder = new THREE.Mesh(
    new THREE.BoxGeometry(lengthIn * IN, 1 * IN, 1 * IN),
    new THREE.MeshStandardMaterial({ color: color, emissive: color, emissiveIntensity: 1.3, roughness: 0.5 })
  );
  wrapper.add(placeholder);

  trackPending(
    import("./stage-ledstrip.js")
      .then((mod) => (mod && mod.buildLedStrip ? mod.buildLedStrip(part) : null))
      .then((built) => {
        if (!built || wrapper.userData.disposed) return;
        wrapper.remove(placeholder);
        // an instance colour override (stage-editor.js Properties panel) was
        // only ever applied to the placeholder bar, not the real built strip
        // once it finished loading - apply it here too, via the same
        // .update() the fixture-driven path uses.
        if (colorOverride && typeof built.update === "function") built.update({ color: colorOverride, dimmer: part.brightness });
        wrapper.add(built);
      })
      .catch(() => {
        /* stage-ledstrip.js not present/failed yet - keep the plain bar */
      })
  );

  return wrapper;
}

/**
 * Builds one part's mesh, positioned/rotated relative to the prop origin.
 * `instanceLook` (optional) is a per-instance override for THIS part only -
 * {texture?, tileIn?, rot?, filters?} - see resolveEffectiveLook. Model/
 * truss/ledstrip parts don't support the free texture picker (they have
 * their own colour tint only - see "Not done" in the memory file).
 */
export function createPartMesh(part, colorOverride, instanceLook) {
  const pos = part.pos || [0, 0, 0];
  const rot = part.rot || [0, 0, 0];

  if (part.shape === "model") return createModelPartMesh(part, pos, rot);
  if (part.shape === "truss") return createTrussPartMesh(part, pos, rot, colorOverride);
  if (part.shape === "ledstrip") return createLedstripPartMesh(part, pos, rot, colorOverride);

  const geo = makeGeometry(part);
  const color = colorOverride || part.color;
  const look = resolveEffectiveLook(part, instanceLook);
  let material;
  const faceIndex = FACE_GROUP_INDEX[part.shape];
  if (part.shape === "plane") {
    material = makeMaterial(part.material, color, sideConstant(part.side || "front"), part, look);
  } else if (faceIndex) {
    const hidden = part.hiddenFaces || [];
    const maxIndex = Math.max.apply(null, Object.keys(faceIndex).map((k) => faceIndex[k]));
    const arr = new Array(maxIndex + 1);
    // BoxGeometry's own default UVs pair up (depth,height) for right/left,
    // (width,depth) for top/bottom and (width,height) for front/back (three.js
    // core convention) - a textured material (applyTexture) needs THAT
    // in-plane size, not just part.size[0]/[2], or a thin wall's side faces
    // (e.g. a 6in-thick, 480in-long left/right wall) tiled wildly wrong.
    const size0 = part.size || [12, 12, 12];
    const bw = size0[0], bd = size0[1] === undefined ? size0[0] : size0[1], bh = size0[2] === undefined ? size0[0] : size0[2];
    const FACE_PLANE_SIZE_BOX = {
      right: [bd, bh], left: [bd, bh],
      top: [bw, bd], bottom: [bw, bd],
      front: [bw, bh], back: [bw, bh],
    };
    // "front" = each face only from outside the shape (e.g. a wall seen from the room only).
    // Hidden faces skip makeMaterial entirely (not just discarded after) - they're
    // real texture-tiling work (a network-cached texture clone + a pending-asset
    // promise) that a wall's 5 always-hidden faces don't need to pay for.
    // Perf audit fix: box/cylinder/cone parts with NO explicit `side` (the
    // vast majority of furniture - club_scene.py's `box()` helper never sets
    // one) used to fall back to "both"/DoubleSide, doubling fill-rate/fragment
    // work for zero visual gain on a closed, always-viewed-from-outside solid.
    // Now defaults to FrontSide (single layer, correct winding, cheaper).
    // Walls are unaffected - they always set `part.side="front"` EXPLICITLY
    // already (see wall_box() in club_scene.py), so this fallback never
    // applies to them; their one un-hidden ("inner") face keeps rendering
    // exactly as before, seen from inside the room.
    Object.keys(faceIndex).forEach((faceId) => {
      const idx = faceIndex[faceId];
      if (hidden.indexOf(faceId) !== -1) {
        arr[idx] = hiddenFaceMaterial();
        return;
      }
      const facePart = part.shape === "box" && FACE_PLANE_SIZE_BOX[faceId] ? Object.assign({}, part, { size: FACE_PLANE_SIZE_BOX[faceId] }) : part;
      arr[idx] = makeMaterial(part.material, color, sideConstant(part.side || "front"), facePart, look);
    });
    material = arr;
  } else {
    // sphere/torus (no per-face material groups) - same FrontSide-by-default
    // fix, but this branch previously ignored `part.side` ENTIRELY (always
    // hardcoded DoubleSide), so an explicit "front"/"back" never worked here.
    material = makeMaterial(part.material, color, sideConstant(part.side || "front"), part, look);
  }
  const mesh = new THREE.Mesh(geo, material);
  mesh.position.copy(userToThreeLocal(pos[0], pos[1], pos[2]));
  mesh.quaternion.copy(composeLocalQuaternion(rot[0], rot[1], rot[2]));
  mesh.userData.partId = part.id;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

/**
 * Builds a THREE.Group for one prop definition (all its parts).
 * `opts.overrides` is an *instance* override object (never written back to
 * the definition - see applyOverrides below):
 *   {color?, partColors?: {partId: hex}, hiddenFaces?: {partId: [faceId,...]},
 *    visible?, filters?: FilterSet, partLooks?: {partId: {texture?, tileIn?, rot?, filters?}}}
 * `filters` is object-wide (composed OVER every part's own + per-part-look
 * filters - "object-over-part"); `partLooks` lets one instance swap a part's
 * texture/tile/rotation/filters without touching the shared prop definition.
 * `opts.color` (legacy) is treated as `overrides.color` for back-compat
 * with the pre-overrides call sites.
 */
export function buildPropGroup(propDef, opts) {
  const group = new THREE.Group();
  group.userData.propKind = "prop";
  const o = opts || {};
  const overrides = o.overrides || (o.color ? { color: o.color } : null);
  group.visible = !overrides || overrides.visible !== false;
  const partColors = (overrides && overrides.partColors) || {};
  const hiddenOv = (overrides && overrides.hiddenFaces) || {};
  const wholeColor = overrides && overrides.color;
  const objectFilters = overrides && overrides.filters;
  const partLooks = (overrides && overrides.partLooks) || {};
  (propDef.parts || []).forEach((part) => {
    const colorOverride = partColors[part.id] || wholeColor || null;
    const usedPart = hiddenOv[part.id] ? Object.assign({}, part, { hiddenFaces: hiddenOv[part.id] }) : part;
    const partLook = partLooks[part.id] || null;
    let instanceLook = null;
    if (partLook || objectFilters) {
      instanceLook = Object.assign({}, partLook);
      instanceLook.filters = composeFilters(partLook && partLook.filters, objectFilters);
    }
    group.add(createPartMesh(usedPart, colorOverride, instanceLook));
  });
  return group;
}

/**
 * Lightweight live-preview update (no rebuild): applies colour/visibility
 * overrides to an already-built group in place. Structural overrides
 * (hiddenFaces) still need a full buildPropGroup rebuild - callers that
 * mutate the stage draft already get that for free via
 * app.commitDraftChange -> rebuildSceneFromDraft.
 */
export function applyOverrides(group, propDef, overrides) {
  if (!group) return;
  const ov = overrides || {};
  group.visible = ov.visible !== false;
  const partColors = ov.partColors || {};
  group.children.forEach((child) => {
    const partId = child.userData && child.userData.partId;
    if (!partId) return;
    const hex = partColors[partId] || ov.color;
    if (!hex) return;
    const c = new THREE.Color(hex);
    child.traverse((node) => {
      if (!node.isMesh) return;
      const mats = Array.isArray(node.material) ? node.material : [node.material];
      mats.forEach((m) => {
        if (m && m.color) m.color.copy(c);
      });
    });
  });
}

// ------------------------------------------------------------- starters --

let partSeq = 1;
export function newPartId() {
  return "p" + partSeq++;
}

function part(shape, size, pos, rot, color, material) {
  return {
    id: newPartId(),
    shape: shape,
    size: size,
    pos: pos,
    rot: rot || [0, 0, 0],
    color: color,
    material: material || "matte",
    // Closed solids (box/cylinder/sphere/cone/torus) default to FrontSide -
    // single-layer, correct winding, half the fragment work of DoubleSide for
    // a shape that's only ever seen from outside (perf audit fix). Planes
    // keep their own "front" default (unrelated - a plane has no "inside").
    side: "front",
    hiddenFaces: [],
  };
}

function modelPart(modelId, size, pos, rot) {
  return { id: newPartId(), shape: "model", modelId: modelId, size: size, pos: pos, rot: rot || [0, 0, 0] };
}

function trussPart(trussType, length, pos, rot) {
  return {
    id: newPartId(),
    shape: "truss",
    trussType: trussType,
    length: length,
    pos: pos || [0, 0, 6],
    rot: rot || [0, 90, 0],
    color: "#c9ccd1",
    material: "metal",
  };
}

function ledstripPart(length, color, pos, rot) {
  return {
    id: newPartId(),
    shape: "ledstrip",
    length: length,
    ledsPerMeter: 60,
    // Static prop LED strips default OFF (operator ask): they're decor, not
    // DMX-driven, so a nonzero default made "all fixtures dark" look lit.
    brightness: 0,
    color: color || "#ffffff",
    pos: pos || [0, 0, 0.5],
    rot: rot || [0, 0, 0],
  };
}

export const STARTER_PROPS = {
  "cocktail-table": {
    name: "Cocktail table",
    category: "table",
    aliases: ["cocktail tables", "the tables"],
    parts: [modelPart("side_table_tall_01", [15.1, 15.1, 30], [0, 0, 0])],
  },
  "bar-stool": {
    name: "Bar stool",
    category: "seating",
    aliases: ["stool", "bar stools"],
    parts: [modelPart("bar_chair_round_01", [19, 19.1, 29.5], [0, 0, 0])],
  },
  "bar-counter": {
    name: "Bar counter",
    category: "bar",
    aliases: ["the bar"],
    parts: [
      part("box", [96, 24, 42], [0, 0, 21], [0, 0, 0], "#3b2a1a", "matte"),
      // lip along the front top edge
      part("box", [96, 3, 3], [0, -10.5, 43.5], [0, 0, 0], "#2a1d10", "gloss"),
      modelPart("modern_wooden_cabinet", [96.1, 20.5, 26.8], [0, 20, 0]),
    ],
  },
  "dj-booth": {
    name: "DJ booth",
    category: "booth",
    aliases: ["the booth", "dj"],
    parts: [
      part("box", [72, 30, 42], [0, 0, 21], [0, 0, 0], "#1a1a1a", "matte"),
      part("box", [78, 33, 2], [0, 0, 43], [0, 0, 0], "#111111", "gloss"),
      part("cylinder", [12, 12, 2], [-16, 0, 44], [0, 0, 0], "#222222", "gloss"),
      part("cylinder", [12, 12, 2], [16, 0, 44], [0, 0, 0], "#222222", "gloss"),
      ledstripPart(72, "#00d0ff", [0, 16.6, 22], [90, 0, 0]),
    ],
  },
  "lounge-booth": {
    name: "Club lounge booth",
    category: "booth",
    aliases: ["lounge", "booth seating"],
    parts: [
      modelPart("sofa_03", [107.5, 36.4, 44], [0, 18, 0]),
      modelPart("sofa_01", [61.9, 25.9, 31.4], [-65, -5, 0], [0, 0, 90]),
      modelPart("sofa_01", [61.9, 25.9, 31.4], [65, -5, 0], [0, 0, -90]),
      modelPart("side_table_tall_01", [15.1, 15.1, 30], [0, -8, 0]),
    ],
  },
  "vip-booth": {
    name: "VIP booth (sofa)",
    category: "booth",
    aliases: ["vip", "the booths"],
    parts: [
      part("box", [84, 20, 18], [0, 22, 9], [0, 0, 0], "#4b1f1f", "matte"),
      part("box", [20, 64, 18], [-32, -10, 9], [0, 0, 0], "#4b1f1f", "matte"),
      part("box", [20, 64, 18], [32, -10, 9], [0, 0, 0], "#4b1f1f", "matte"),
    ],
  },
  "stage-deck-4x8": {
    name: "Stage deck 4'x8'",
    category: "stage",
    aliases: ["deck", "the stage"],
    parts: [part("box", [96, 48, 24], [0, 0, 12], [0, 0, 0], "#222222", "matte")],
  },
  "truss-5": {
    name: "Box truss 5'",
    category: "truss",
    aliases: ["truss 5"],
    parts: [trussPart("box12", 60)],
  },
  "truss-8": {
    name: "Box truss 8'",
    category: "truss",
    aliases: ["truss 8"],
    parts: [trussPart("box12", 96)],
  },
  "truss-10": {
    name: "Box truss 10'",
    category: "truss",
    aliases: ["truss 10", "truss"],
    parts: [trussPart("box12", 120)],
  },
  "box-truss-10": {
    name: "Box truss 10' (legacy)",
    category: "truss",
    aliases: [],
    parts: [trussPart("box12", 120)],
  },
  "triangle-truss-10": {
    name: "Triangle truss 10'",
    category: "truss",
    aliases: ["triangle truss"],
    parts: [trussPart("tri12", 120)],
  },
  "led-strip-1m": {
    name: "LED strip 1m / 60 LED",
    category: "led",
    aliases: ["led strip", "pixel strip"],
    parts: [ledstripPart(39.37, "#ffffff", [0, 0, 0.5], [0, 0, 0])],
  },
  "led-screen": {
    name: "LED screen",
    category: "screen",
    aliases: ["screen", "led wall"],
    parts: [part("box", [120, 6, 80], [0, 0, 40], [0, 0, 0], "#334455", "led")],
  },
  "speaker": {
    name: "Speaker",
    category: "speaker",
    aliases: ["speakers", "pa", "speaker-stack"],
    parts: [
      part("box", [24, 24, 48], [0, 0, 24], [0, 0, 0], "#0a0a0a", "matte"),
      part("box", [20, 18, 30], [0, 0, 63], [0, 0, 0], "#0a0a0a", "matte"),
    ],
  },
  "crowd-barrier": {
    name: "Crowd barrier",
    category: "decor",
    aliases: ["barrier", "barricade"],
    parts: [part("box", [96, 4, 40], [0, 0, 20], [0, 0, 0], "#9aa0a6", "metal")],
  },
  "foh-riser": {
    name: "FOH riser",
    category: "stage",
    aliases: ["foh", "riser"],
    parts: [part("box", [96, 96, 12], [0, 0, 6], [0, 0, 0], "#2a2a2a", "matte")],
  },
  "dance-floor": {
    name: "Dance floor area",
    category: "floor_area",
    aliases: ["dance floor", "the floor"],
    parts: [part("box", [120, 120, 1], [0, 0, 0.5], [0, 0, 0], "#141414", "gloss")],
  },
  "point-marker": {
    name: "Point marker",
    category: "point",
    aliases: ["marker", "point"],
    parts: [part("sphere", [3, 3, 3], [0, 0, 2], [0, 0, 0], "#ffcc00", "matte")],
  },
  wall: {
    name: "Wall",
    category: "wall",
    aliases: ["the wall"],
    parts: [part("box", [120, 6, 120], [0, 0, 60], [0, 0, 0], "#555555", "matte")],
  },
  "wall-section-10x12": {
    name: "Wall section 10'x12'",
    category: "wall",
    aliases: ["wall section"],
    // One-sided plane (item 2's fix: identity-rotation planes already stand
    // upright spanning width x height, normal along the depth axis - the
    // "invisible plane" bug was side defaulting to a face the room's front
    // usually isn't on; here side is explicit and correct for a wall you
    // view from the room side). `thickness` is stored for a future
    // extruded-wall option; the plane itself has no depth yet.
    parts: [Object.assign(part("plane", [120, 120, 144], [0, 0, 72], [0, 0, 0], "#555555", "matte"), { side: "front", thickness: 4 })],
  },
};

// ------------------------------------------------------ builder helpers --

export function newBlankProp() {
  return { name: "New prop", category: "decor", aliases: [], parts: [] };
}

/** Shape-aware defaults for a freshly-added part (used by the prop editor). */
export function defaultPartFor(shape) {
  const base = { id: newPartId(), shape: shape, rot: [0, 0, 0], hiddenFaces: [] };
  switch (shape) {
    case "plane":
      // 2'x2' (item 2), side "both" so a fresh plane is never invisible
      // from the "wrong" side before the user picks an explicit facing.
      return Object.assign(base, { size: [24, 24, 24], pos: [0, 0, 12], color: "#8899aa", material: "matte", side: "both" });
    case "truss":
      return Object.assign(base, { trussType: "box12", length: 120, pos: [0, 0, 6], color: "#c9ccd1", material: "metal" });
    case "model":
      return Object.assign(base, { modelId: "", url: "", size: [24, 24, 36], pos: [0, 0, 0] });
    case "ledstrip":
      return Object.assign(base, {
        length: 39.37,
        ledsPerMeter: 60,
        // default OFF (operator ask) - a static decor strip isn't DMX-bound,
        // so a nonzero default broke "completely dark room at DMX 0".
        brightness: 0,
        color: "#ffffff",
        pos: [0, 0, 0.5],
        material: "led",
      });
    default:
      // Closed solids default to FrontSide (perf audit fix - see part() above);
      // the prop editor's own "Visible from" field lets the operator switch a
      // specific part back to "both" if it's ever genuinely seen from inside.
      return Object.assign(base, { size: [12, 12, 12], pos: [0, 0, 6], color: "#8899aa", material: "matte", side: "front" });
  }
}

export function addPart(propDef, shape, extra) {
  const p = defaultPartFor(shape);
  if (extra) Object.assign(p, extra);
  propDef.parts.push(p);
  return p;
}

export function duplicatePart(propDef, partId) {
  const src = propDef.parts.find((p) => p.id === partId);
  if (!src) return null;
  const copy = JSON.parse(JSON.stringify(src));
  copy.id = newPartId();
  if (copy.pos) copy.pos = [copy.pos[0] + 4, copy.pos[1] + 4, copy.pos[2]];
  propDef.parts.push(copy);
  return copy;
}

export function mirrorPart(propDef, partId, axis) {
  const src = propDef.parts.find((p) => p.id === partId);
  if (!src) return null;
  const copy = JSON.parse(JSON.stringify(src));
  copy.id = newPartId();
  if (copy.pos) {
    if (axis === "x") copy.pos[0] = -copy.pos[0];
    else copy.pos[1] = -copy.pos[1];
  }
  propDef.parts.push(copy);
  return copy;
}

export function deletePart(propDef, partId) {
  const i = propDef.parts.findIndex((p) => p.id === partId);
  if (i !== -1) propDef.parts.splice(i, 1);
}

/** Aligns `partId`'s bottom to the top of `ontoPartId` (same X/Y). */
export function snapPartOnTop(propDef, partId, ontoPartId) {
  const p = propDef.parts.find((x) => x.id === partId);
  const onto = propDef.parts.find((x) => x.id === ontoPartId);
  if (!p || !onto || !p.pos || !onto.pos) return;
  const ontoTop = onto.pos[2] + (onto.size ? onto.size[2] || 0 : 0);
  const half = p.size ? (p.size[2] || 0) / 2 : 0;
  p.pos = [onto.pos[0], onto.pos[1], ontoTop + half];
}

// ---------------------------------------------------------- Props mode --

/**
 * Wires the Props-mode DOM: the prop library list ("New prop" button,
 * click a row to open it in the full-screen prop editor, drag a row into
 * the 3D view to place an instance). The old in-panel parts builder is
 * hidden here - editing now happens in stage-propeditor.js's
 * openPropEditor(), which U's "Edit prop..." button also calls.
 */
export function initPropsMode(app) {
  const els = {
    list: document.getElementById("props-library-list"),
    newBtn: document.getElementById("props-new-btn"),
    nameField: document.getElementById("prop-name"),
    categoryField: document.getElementById("prop-category"),
    aliasesField: document.getElementById("prop-aliases"),
    partsList: document.getElementById("prop-parts-list"),
    addPartSelect: document.getElementById("prop-add-part-shape"),
    addPartBtn: document.getElementById("prop-add-part-btn"),
    partFields: document.getElementById("prop-part-fields"),
    saveBtn: document.getElementById("props-save-btn"),
    placeBtn: document.getElementById("props-place-btn"),
    status: document.getElementById("props-status"),
  };
  if (!els.list) return; // Props panel not present in this build of stage.html

  // Old in-panel builder DOM is superseded by stage-propeditor.js's
  // full-screen editor - hide every block except the library list/New
  // button (goal 1: "Remove or hide the old in-panel prop builder").
  const hidden = new Set();
  [
    els.nameField,
    els.categoryField,
    els.aliasesField,
    els.partsList,
    els.addPartSelect,
    els.addPartBtn,
    els.partFields,
    els.saveBtn,
    els.placeBtn,
    els.status,
  ].forEach((el) => {
    if (!el) return;
    const block = el.closest(".panel-block") || el;
    if (hidden.has(block)) return;
    hidden.add(block);
    block.style.display = "none";
  });

  function openEditor(id) {
    import("./stage-propeditor.js")
      .then((mod) => mod.openPropEditor(app, id))
      .catch((err) => console.error("stage-props: failed to load prop editor", err));
  }

  function currentPropId() {
    return app.props.editingId;
  }

  // "Edit" button: sits next to "New prop", enabled only while a row is
  // selected. Created here (not in stage.html, which is owned elsewhere).
  const editBtn = document.createElement("button");
  editBtn.id = "props-edit-btn";
  editBtn.type = "button";
  editBtn.textContent = "Edit";
  editBtn.disabled = true;
  editBtn.addEventListener("click", () => {
    const id = currentPropId();
    if (id) openEditor(id);
  });
  els.newBtn.insertAdjacentElement("afterend", editBtn);

  function selectProp(id) {
    app.props.editingId = id;
    refreshLibraryList();
  }

  function wireDraggableRow(row, id) {
    row.addEventListener("pointerdown", (downEv) => {
      if (downEv.button !== 0) return;
      const downX = downEv.clientX;
      // View mode is look-only: a prop can be opened in the editor but not dropped into the room
      const canDrop = app.mode !== "view";
      const downY = downEv.clientY;
      let dragging = false;
      let ghost = null;

      function onMove(mv) {
        const dx = mv.clientX - downX;
        const dy = mv.clientY - downY;
        if (!dragging && canDrop && dx * dx + dy * dy > 16) {
          dragging = true;
          ghost = document.createElement("div");
          ghost.className = "prop-drag-ghost";
          const def = app.props.draft.props[id];
          ghost.textContent = (def && def.name) || id;
          document.body.appendChild(ghost);
        }
        if (dragging && ghost) {
          ghost.style.left = mv.clientX + "px";
          ghost.style.top = mv.clientY + "px";
        }
      }
      function onUp(upEv) {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        if (ghost) {
          ghost.remove();
          ghost = null;
        }
        if (!dragging) {
          selectProp(id); // single click: select/highlight only, don't open the editor
          return;
        }
        const viewport = document.getElementById("viewport");
        const rect = viewport && viewport.getBoundingClientRect();
        if (!rect || upEv.clientX < rect.left || upEv.clientX > rect.right || upEv.clientY < rect.top || upEv.clientY > rect.bottom) {
          return; // dropped outside the 3D view - no-op
        }
        if (!app.scene || !app.scene.pickGroundPoint) return;
        if (app.mode === "view") return app.showToast("View mode — switch to Edit to place props");
        const pt = app.scene.pickGroundPoint(upEv.clientX, upEv.clientY);
        if (!pt) return;
        const snapSelect = document.getElementById("snap-select");
        const snapIn = snapSelect ? parseFloat(snapSelect.value) : 0;
        let x = pt[0];
        let y = pt[1];
        if (snapIn > 0) {
          x = Math.round(x / snapIn) * snapIn;
          y = Math.round(y / snapIn) * snapIn;
        }
        const newId = app.placePropInstance(id, [x, y, 0]);
        if (newId) {
          app.selection.clear();
          app.selection.add("object:" + newId);
          if (app.editor && app.editor.refreshAll) app.editor.refreshAll();
        }
      }
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    });
  }

  function refreshLibraryList() {
    const ids = Object.keys(app.props.draft.props).sort();
    els.list.innerHTML = "";
    ids.forEach((id) => {
      const def = app.props.draft.props[id];
      const row = document.createElement("div");
      row.className = "list-row" + (id === currentPropId() ? " selected" : "");
      row.textContent = def.name + " (" + def.category + ")";
      row.addEventListener("dblclick", () => openEditor(id));
      wireDraggableRow(row, id);
      els.list.appendChild(row);
    });
    editBtn.disabled = !currentPropId();
  }

  els.newBtn.addEventListener("click", () => {
    app.props.editingId = null;
    openEditor(null);
  });

  app.props.onChanged = refreshLibraryList;
  refreshLibraryList();
}

// Re-exported so stage-propeditor.js (and anything else) can format/parse
// lengths the same way without importing stage-units.js twice in a bundle
// that doesn't dedupe - harmless either way since ES modules are singletons.
export { parseLength, formatLength };
