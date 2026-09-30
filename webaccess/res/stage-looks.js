/*
  stage-looks.js

  Turns a downloaded GDTF fixture "look" (see .claude/memory/stage-visualizer.md /
  the stage-lib index+look.json format produced by the fixture downloader) into a
  rigged, animatable three.js object for the 3D stage visualizer, and a matching
  fallback path for the built-in QLC+ Collada meshes.

  ---------------------------------------------------------------------------
  Coordinate conversion (GDTF -> our local rig frame)
  ---------------------------------------------------------------------------
  GDTF (see https://github.com/mvrdevelopment/spec/blob/main/gdtf-spec.md,
  "Model Collect > Model" and "Geometry Collect > Geometry Type Beam"):
    - World/local axes: right-handed, metres, Z-up ("Z - from bottom(-Z) to
      top(+Z)"). Geometry position matrices are 4x4, stored row-major, with
      translation in the 4th column of each row - i.e. a plain row-major
      affine matrix, the same argument order THREE.Matrix4.set() takes.
    - "The device shall be drawn in a hanging position displaying the front
      view. That results in the pan axis is Z aligned, and the tilt axis is
      X aligned." - so GDTF's own authoring convention is a HUNG fixture:
      the mounting/base geometry is nearest the top of the model's Z extent,
      the body hangs below it (-Z), and:
    - "The beam geometry emits its light into negative Z direction" - i.e.
      a Beam geometry's own local -Z axis, at rest (no pan/tilt applied).

  Our target local frame (see stage-scene.js's header, and the fixture mesh
  hierarchy `buildFixtureObject3D` builds there) is the opposite mount
  convention: a FLOOR-standing fixture, base at the local origin, body
  extending along local +Y, pan axis = local Y, tilt axis = local X, and at
  pan=0/tilt=0 the beam points local +Y ("up"). The page applies the
  hang/floor/wall flip itself, outside this module, per fixture instance.

  So every GDTF local matrix gets conjugated by one fixed rotation C, applied
  uniformly at every node of the geometry tree:
    M'_local = C * M_gdtf_local * C^-1     (translations rotate simply: t' = C*t)
  C is the composition of:
    - Flip = Rx(180 deg): un-hangs the GDTF default pose into a floor-mount
      default pose *before* relabelling axes (rotating 180 deg about GDTF's
      own X turns "hangs below, beam -Z" into "stands above, beam +Z", and
      leaves GDTF's X and Z *lines* fixed).
    - rho = Rx(-90 deg): relabels axes X->X (unchanged, tilt stays "X"),
      Z->Y (pan axis becomes local Y), Y->-Z (kept only for right-handedness).
  C = rho . Flip = Rx(-90) . Rx(180) = Rx(+90 deg) (same-axis rotations add).

  Because conjugation by a single fixed rotation preserves matrix products,
  applying C once per node (rather than once for the whole assembled scene)
  keeps every node's own *local* axes consistent with this same relabelling,
  which is what makes the simple, checkable results below hold:
    - C * (0,0,-1)  [GDTF beam -Z]     = (0, 1, 0)  = our +Y  (beam at rest)
    - C * (0,0, 1)  [GDTF pan/Z axis]  = (0,-1, 0)  -> the pan AXIS LINE is
      local Y (matches "pan rotates about local Y"); this module always
      builds the yoke's rest orientation from the *same* C, so a plain
      THREE `yoke.rotation.y` delta on top of that rest orientation sweeps
      the head the same way a `Rz` delta would in raw GDTF space - only the
      *sign* of "increasing panDeg" is a free choice at that point (real
      fixtures are wired either way in practice; stage-rig.js already has
      per-instance invertPan/invertTilt override flags for exactly this,
      so this module intentionally does NOT try to chase GDTF's own PanRange
      sign - it uses the same plain, direct `rotation.y = panDeg` convention
      the procedural fixtures in stage-scene.js use, for consistency).
    - C * (1,0,0)   [GDTF tilt/X axis] = (1, 0, 0)  = our +X (unchanged).

  3D model files referenced by a part (`file: "x.glb"|"x.3ds"`) are loaded in
  their own native axes and then wrapped in one more fixed rotation before
  being parented under the (already-converted) part group:
    - .glb/.gltf: glTF itself is always Y-up. Converting glTF Y-up to GDTF's
      own Z-up (G = Rx(+90 deg), since G*(0,1,0) = (0,0,1)) and then applying
      the same part-local C on top (C*G = Rx(180 deg)) gives the wrapper
      used below. The GDTF spec text available to this module does not spell
      out the glTF<->GDTF axis mapping explicitly beyond "glTF is Y-up per
      the glTF spec, GDTF defines how they map" - this is the standard
      Y-up<->Z-up convention (the same one Blender's glTF importer uses), and
      is the best-effort interpretation used here; flag for verification
      once real downloaded GLB assets exist.
    - .3ds: 3ds Studio models are authored in GDTF's own Z-up local axes
      (no differing convention is stated for 3ds in the fetched spec text),
      so only the plain per-part C is needed to bring them into our frame.
    - Built-in QLC+ .dae meshes (moving_head/par/scanner/...) declare
      `<up_axis>Y_UP</up_axis>` and meter units; three's ColladaLoader
      converts that automatically, and per the task spec they are already
      authored in *our* target convention (base at origin, arm/head local Y
      = pan axis) - no extra wrapper rotation is applied to them.

  Model dimension scaling (`sizeMetres: [length, width, height]`, matching
  GDTF's own Length(X)/Width(Y)/Height(Z)) is applied to the raw loaded mesh
  in its own native axes, using the same per-format axis correspondence as
  the wrapper rotations above (see applyFitScale()).

  ---------------------------------------------------------------------------
  API
  ---------------------------------------------------------------------------
  loadLookIndex(baseUrl) -> index.json, cached per baseUrl.
  buildLook(lookIdOrEntry, opts) -> { root, setPanTilt(panDeg,tiltDeg), lens,
    lensMeshes, lensRadiusInches, beamAngleDeg, heightInches, dims, movable,
    dispose() }. See exported function docs below for details.
  renderLookThumbnail(entry, size) -> dataURL, an offscreen 3/4-view render.
*/

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { TDSLoader } from "three/addons/loaders/TDSLoader.js";
import { ColladaLoader } from "three/addons/loaders/ColladaLoader.js";

const X_AXIS = new THREE.Vector3(1, 0, 0);
const Y_AXIS = new THREE.Vector3(0, 1, 0);
const IN = 0.0254; // metres per inch (matches stage-scene.js's IN)

// The single fixed GDTF-local -> our-local conjugating rotation, see header.
const C4 = new THREE.Matrix4().makeRotationX(Math.PI / 2);
const C4inv = new THREE.Matrix4().makeRotationX(-Math.PI / 2);

const gltfLoader = new GLTFLoader();
const tdsLoader = new TDSLoader();
const colladaLoader = new ColladaLoader();

// url -> Promise<{ wrap: THREE.Object3D, kind: "glb"|"3ds"|"dae", rawSize: THREE.Vector3|null }>
const modelTemplateCache = new Map();
// baseUrl -> Promise<index.json>
const indexCache = new Map();
let lastBaseUrl = null;

// ------------------------------------------------------------------ utils --

function joinUrl(baseUrl, rel) {
  if (/^https?:\/\//i.test(rel) || rel.startsWith("/")) return rel;
  const base = baseUrl.endsWith("/") ? baseUrl : baseUrl + "/";
  return base + rel;
}

function dirnameUrl(url) {
  const i = url.lastIndexOf("/");
  return i >= 0 ? url.slice(0, i + 1) : "";
}

function basenameUrl(url) {
  const i = url.lastIndexOf("/");
  return i >= 0 ? url.slice(i + 1) : url;
}

function extOf(url) {
  const m = /\.([a-z0-9]+)$/i.exec(url);
  return m ? m[1].toLowerCase() : "";
}

/** Converts one GDTF local position matrix (16 numbers, row-major, metres)
 * into our local-frame THREE.Matrix4, per the header derivation. */
function convertLocalMatrix(m16) {
  if (!Array.isArray(m16) || m16.length !== 16) return new THREE.Matrix4();
  const M = new THREE.Matrix4().set(
    m16[0], m16[1], m16[2], m16[3],
    m16[4], m16[5], m16[6], m16[7],
    m16[8], m16[9], m16[10], m16[11],
    m16[12], m16[13], m16[14], m16[15]
  );
  return new THREE.Matrix4().multiplyMatrices(C4, M).multiply(C4inv);
}

function setCastReceiveShadow(root) {
  root.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
    }
  });
}

function collectLensMeshes(root) {
  const out = [];
  const re = /lens|emitter|beam/i;
  root.traverse((o) => {
    if (o.isMesh && re.test(o.name || "")) out.push(o);
  });
  return out;
}

// --------------------------------------------------------- model loading --

/** Loads (and caches) the raw model file at `url`, wrapped in the fixed
 * corrective rotation for its file type (see header). The returned template
 * is never added to a scene directly - clone it per fixture instance. */
function loadModelTemplate(url) {
  if (modelTemplateCache.has(url)) return modelTemplateCache.get(url);
  const ext = extOf(url);
  const dir = dirnameUrl(url);
  const file = basenameUrl(url);
  let p;

  if (ext === "glb" || ext === "gltf") {
    gltfLoader.setPath(dir);
    p = gltfLoader.loadAsync(file).then((gltf) => {
      setCastReceiveShadow(gltf.scene);
      const rawSize = new THREE.Box3().setFromObject(gltf.scene).getSize(new THREE.Vector3());
      const wrap = new THREE.Group();
      wrap.name = "modelWrap:" + file;
      wrap.quaternion.setFromAxisAngle(X_AXIS, Math.PI); // C.G, see header
      wrap.add(gltf.scene);
      return { wrap, kind: "glb", rawSize };
    });
  } else if (ext === "3ds") {
    tdsLoader.setPath(dir);
    p = tdsLoader.loadAsync(file).then((obj) => {
      setCastReceiveShadow(obj);
      const rawSize = new THREE.Box3().setFromObject(obj).getSize(new THREE.Vector3());
      const wrap = new THREE.Group();
      wrap.name = "modelWrap:" + file;
      wrap.quaternion.setFromAxisAngle(X_AXIS, Math.PI / 2); // C, see header
      wrap.add(obj);
      return { wrap, kind: "3ds", rawSize };
    });
  } else if (ext === "dae") {
    colladaLoader.setPath(dir);
    p = colladaLoader.loadAsync(file).then((collada) => {
      setCastReceiveShadow(collada.scene);
      // Already authored in our target convention (Y_UP + our axis roles) -
      // no extra wrapper rotation, and no dimension-fit scaling (dae assets
      // are used as-is, matching the procedural meshes they replace).
      const wrap = new THREE.Group();
      wrap.name = "modelWrap:" + file;
      wrap.add(collada.scene);
      return { wrap, kind: "dae", rawSize: null };
    });
  } else {
    p = Promise.reject(new Error(`stage-looks: unsupported model file type "${ext}" (${url})`));
  }

  p = p.catch((err) => {
    modelTemplateCache.delete(url);
    throw new Error(`stage-looks: failed to load model "${url}": ${(err && err.message) || err}`);
  });
  modelTemplateCache.set(url, p);
  return p;
}

/** Scales the raw loaded mesh (child 0 of a loaded wrap) to the GDTF part's
 * sizeMetres = [length(X), width(Y), height(Z)], using the same per-format
 * native-axis correspondence as the wrapper rotations (see header). */
function applyFitScale(raw, kind, rawSize, sizeMetres) {
  if (!raw || !rawSize || !Array.isArray(sizeMetres) || sizeMetres.length !== 3) return;
  const [lengthM, widthM, heightM] = sizeMetres;
  const eps = 1e-6;
  if (kind === "glb") {
    raw.scale.set(
      rawSize.x > eps ? lengthM / rawSize.x : 1,
      rawSize.y > eps ? heightM / rawSize.y : 1,
      rawSize.z > eps ? widthM / rawSize.z : 1
    );
  } else if (kind === "3ds") {
    raw.scale.set(
      rawSize.x > eps ? lengthM / rawSize.x : 1,
      rawSize.y > eps ? widthM / rawSize.y : 1,
      rawSize.z > eps ? heightM / rawSize.z : 1
    );
  }
}

// ----------------------------------------------------------- primitives --

/** Builds a simple placeholder mesh for a part with no model file. Sized
 * directly in our local axes (X=length,Y=height,Z=width matches the part
 * group's own local convention after conversion - see header), so no extra
 * rotation is needed: THREE's Cylinder/Cone already default to a Y axis. */
function buildPrimitiveMesh(part) {
  const size = Array.isArray(part.sizeMetres) && part.sizeMetres.length === 3 ? part.sizeMetres : [0.1, 0.1, 0.1];
  const [l, w, h] = size;
  const prim = String(part.primitive || "box").toLowerCase();
  let geo;
  if (prim === "cylinder") geo = new THREE.CylinderGeometry(Math.max(l, w) / 2, Math.max(l, w) / 2, Math.max(h, 0.001), 20);
  else if (prim === "cone") geo = new THREE.ConeGeometry(Math.max(l, w) / 2, Math.max(h, 0.001), 20);
  else if (prim === "sphere") geo = new THREE.SphereGeometry(Math.max(l, w, h) / 2, 16, 12);
  else geo = new THREE.BoxGeometry(Math.max(l, 0.001), Math.max(h, 0.001), Math.max(w, 0.001));
  const mat = new THREE.MeshStandardMaterial({ color: 0x888892, metalness: 0.35, roughness: 0.55 });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.name = part.name || "part";
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

// ------------------------------------------------------------ rig build --

async function attachPartMesh(partGroup, part, lookDir) {
  if (part.file) {
    const url = joinUrl(lookDir, part.file);
    const tpl = await loadModelTemplate(url);
    const inst = tpl.wrap.clone(true);
    const raw = inst.children[0];
    applyFitScale(raw, tpl.kind, tpl.rawSize, part.sizeMetres);
    inst.name = "model:" + (part.name || "");
    partGroup.add(inst);
  } else {
    partGroup.add(buildPrimitiveMesh(part));
  }
}

/** Builds the rig for a GDTF-sourced look (look.json's `parts` array). */
async function buildFromGdtfLook(look, lookDir, entry) {
  const root = new THREE.Group();
  root.name = "look:" + (look.id || entry.id || "gdtf");

  const parts = Array.isArray(look.parts) ? look.parts : [];
  const byName = new Map();
  for (const part of parts) {
    const group = new THREE.Group();
    group.name = part.name || "part";
    const localMatrix = convertLocalMatrix(part.matrix);
    const pos = new THREE.Vector3();
    const quat = new THREE.Quaternion();
    const scale = new THREE.Vector3();
    localMatrix.decompose(pos, quat, scale);
    group.position.copy(pos);
    group.quaternion.copy(quat);
    if (Math.abs(scale.x - 1) > 1e-4 || Math.abs(scale.y - 1) > 1e-4 || Math.abs(scale.z - 1) > 1e-4) {
      group.scale.copy(scale);
    }
    group.userData.restQuat = quat.clone();
    group.userData.role = part.role || "other";
    byName.set(part.name, { def: part, group });
  }
  // Attach hierarchy (two passes so parent-before-child ordering in the
  // source array doesn't matter).
  for (const { def, group } of byName.values()) {
    const parentEntry = def.parent ? byName.get(def.parent) : null;
    (parentEntry ? parentEntry.group : root).add(group);
  }
  // Attach meshes/primitives (can run in parallel).
  await Promise.all(Array.from(byName.values()).map(({ def, group }) => attachPartMesh(group, def, lookDir)));

  const roleGroups = { base: [], yoke: [], head: [], beam: [], other: [] };
  for (const { def, group } of byName.values()) {
    (roleGroups[def.role] || roleGroups.other).push(group);
  }

  const yokeGroups = roleGroups.yoke;
  const headGroups = roleGroups.head;
  const movable = entry.movable !== false && (yokeGroups.length > 0 || headGroups.length > 0);

  const setPanTilt = movable
    ? (panDeg, tiltDeg) => {
        const panQ = new THREE.Quaternion().setFromAxisAngle(Y_AXIS, THREE.MathUtils.degToRad(panDeg || 0));
        const tiltQ = new THREE.Quaternion().setFromAxisAngle(X_AXIS, THREE.MathUtils.degToRad(tiltDeg || 0));
        for (const y of yokeGroups) y.quaternion.copy(y.userData.restQuat).multiply(panQ);
        for (const h of headGroups) h.quaternion.copy(h.userData.restQuat).multiply(tiltQ);
      }
    : () => {};

  // Beam anchor: the "beam" role part's own local +Y is the beam direction
  // by construction (see header) - identity offset is enough. Fall back to
  // the head, then the root, for looks with no explicit beam geometry.
  const beamAnchor = roleGroups.beam[0] || headGroups[0] || root;
  const lens = new THREE.Object3D();
  lens.name = "lensAxis";
  beamAnchor.add(lens);

  const beam = look.beam || {};
  const dims = Array.isArray(look.dimensionsInches) ? look.dimensionsInches : null;

  return {
    root,
    setPanTilt,
    lens,
    lensMeshes: collectLensMeshes(root),
    lensRadiusInches: (beam.radiusInches != null ? beam.radiusInches : null),
    beamAngleDeg: beam.angleDeg != null ? beam.angleDeg : entry.beamAngleDeg != null ? entry.beamAngleDeg : 15,
    heightInches: dims ? dims[2] : null,
    dims: dims,
    movable,
    id: look.id != null ? look.id : entry.id,
    name: look.name || entry.name,
    category: look.category || entry.category,
    gobos: Array.isArray(look.gobos) ? look.gobos : [],
    prismFacets: look.prismFacets != null ? look.prismFacets : null,
    dispose() {
      if (root.parent) root.parent.remove(root);
      // Geometries/materials are cached/shared per model URL - not disposed
      // here (see header/cache note); only this instance's graph is dropped.
    },
  };
}

// QLC+'s moving_head.dae and par.dae are modelled HANGING (base/handle on top, head below,
// lens facing -Y); the other built-ins stand on the floor.
const HUNG_AUTHORED = new Set(["builtin/moving_head", "builtin/par"]);

// Real-world size of a built-in model at scale 1, in inches from the mount face (the legs) to the far end with the
// head straight. QLC+'s moving_head.dae is authored about 42.5 in tall; the club's moving heads (Mayans BEAM230,
// measured by the operator) are 20 in, so the model is fitted to that and a fixture's bodyScale stays 1.
const BUILTIN_HEIGHT_INCHES = { "builtin/moving_head": 20 };

/** Builds the rig for a built-in QLC+ Collada mesh (moving_head/par/...). */
async function buildFromBuiltinModel(modelUrl, entry) {
  const tpl = await loadModelTemplate(modelUrl);
  const model = tpl.wrap.clone(true).children[0]; // unwrap the identity wrap
  model.name = "look:" + (entry.id || "builtin");

  // Bring hanging models into this module's floor frame (base at the origin, beam +Y at rest):
  // flip about X, then lift so the mount face (top of the base) sits on the origin.
  const hungAuthored = HUNG_AUTHORED.has(entry.id);
  let root = model;
  if (hungAuthored) {
    const flip = new THREE.Group();
    flip.rotation.x = Math.PI;
    flip.add(model);
    root = new THREE.Group();
    root.name = model.name;
    root.add(flip);
    root.updateMatrixWorld(true);
    flip.position.y = -new THREE.Box3().setFromObject(flip).min.y;
  }

  const yokeNode = model.getObjectByName("arm") || null;
  const headNode = model.getObjectByName("head") || null;
  // the X flip mirrors rotations about Y: pan in the model's own frame is the negated pan
  const panSign = hungAuthored ? -1 : 1;
  for (const n of [yokeNode, headNode]) {
    if (n) n.userData.restQuat = n.quaternion.clone();
  }

  // Movability is read from the actual loaded model (does it have arm/head
  // nodes?), not from the index entry's "movable" flag - the generator
  // (tools/stagelib/fetch_gdtf.py) currently hardcodes movable:false for
  // every builtin .dae, which would otherwise wrongly disable pan/tilt on
  // builtin/moving_head and builtin/scanner (both DO have arm+head nodes).
  const movable = !!(yokeNode || headNode);
  const setPanTilt = movable
    ? (panDeg, tiltDeg) => {
        if (yokeNode) {
          const panQ = new THREE.Quaternion().setFromAxisAngle(Y_AXIS, THREE.MathUtils.degToRad(panSign * (panDeg || 0)));
          yokeNode.quaternion.copy(yokeNode.userData.restQuat).multiply(panQ);
        }
        if (headNode) {
          const tiltQ = new THREE.Quaternion().setFromAxisAngle(X_AXIS, THREE.MathUtils.degToRad(tiltDeg || 0));
          headNode.quaternion.copy(headNode.userData.restQuat).multiply(tiltQ);
        }
      }
    : () => {};

  const beamAnchor = headNode || model;
  const lens = new THREE.Object3D();
  lens.name = "lensAxis";
  if (hungAuthored) lens.rotation.x = Math.PI; // the model's lens faces its own -Y
  beamAnchor.add(lens);

  const nativeHeight = BUILTIN_HEIGHT_INCHES[entry.id];
  if (nativeHeight) {
    // fit the model to its real size on an inner node, so the fixture's own bodyScale (set on the returned root by
    // the scene) multiplies it instead of replacing it
    root.updateMatrixWorld(true);
    const raw = new THREE.Box3().setFromObject(root).getSize(new THREE.Vector3());
    if (raw.y > 0) {
      root.scale.setScalar((nativeHeight * IN) / raw.y);
      const holder = new THREE.Group();
      holder.name = root.name;
      holder.add(root);
      root = holder;
    }
  }

  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  // the lens is about half the head's width (not the whole model's footprint)
  const headSize = headNode ? new THREE.Box3().setFromObject(headNode).getSize(new THREE.Vector3()) : null;
  const size = box.getSize(new THREE.Vector3());
  const dimsInches = [size.x / IN, size.z / IN, size.y / IN]; // [w, d, h] (h = our local Y)

  return {
    root,
    setPanTilt,
    lens,
    lensMeshes: collectLensMeshes(root),
    lensRadiusInches: headSize ? Math.max(headSize.x, headSize.z) / IN / 4 : Math.max(size.x, size.z) / IN / 4 || 1,
    beamAngleDeg: entry.beamAngleDeg != null ? entry.beamAngleDeg : 15,
    heightInches: dimsInches[2],
    dims: dimsInches,
    movable,
    id: entry.id,
    name: entry.name,
    category: entry.category,
    gobos: [],
    prismFacets: null,
    dispose() {
      if (root.parent) root.parent.remove(root);
    },
  };
}

// ------------------------------------------------------ builtin/adj-vpar --
// American DJ VPar: a round, thin "puck" can (~8" diameter, ~3.5" deep) on
// a short fixed yoke bracket, with 5 LED emitters spread across its front
// face - one centre + 4 around at ~2.4" radius - each throwing its own
// narrow soft beam in the head's colour. Not a moving-head (no pan/tilt
// motor) - the short yoke is a fixed manual-tilt bracket, so `movable` is
// false, same as the procedural "par" kind. Procedural (no GDTF/model file
// download needed), built directly here rather than through
// buildFromGdtfLook/buildFromBuiltinModel.
//
// Extends the normal single-`lens` buildLook() contract with an
// `emitters: THREE.Object3D[]` array (5 lens-axis nodes, local +Y = each
// emitter's own beam direction) - stage-scene.js's swapInLook() creates one
// beam per emitter when this is present, instead of the usual single beam
// under `lens`. `lens` itself still points at the centre emitter so any
// caller that only ever looks at the single-beam fields keeps working.
const VPAR_DIAMETER_IN = 8;
const VPAR_DEPTH_IN = 3.5;
const VPAR_RING_RADIUS_IN = 2.4;
const VPAR_EMITTER_LENS_IN = 0.55;
const VPAR_YOKE_HEIGHT_IN = 1.5;
const VPAR_EMITTER_BEAM_DEG = 12;

export function isAdjVparModel(manufacturer, model) {
  const m = String(manufacturer || "").toLowerCase();
  const md = String(model || "").toLowerCase();
  const isAdj = /american\s*dj|^adj$/.test(m) || /american\s*dj/.test(md);
  return isAdj && /v\s*-?\s*par/.test(md);
}

async function buildAdjVpar(entry) {
  const root = new THREE.Group();
  root.name = "look:builtin/adj-vpar";

  const yokeH = VPAR_YOKE_HEIGHT_IN * IN;
  const canR = (VPAR_DIAMETER_IN * IN) / 2;
  const canH = VPAR_DEPTH_IN * IN;
  const bodyMat = new THREE.MeshStandardMaterial({ color: 0x1c1d20, roughness: 0.5, metalness: 0.4 });

  // fixed yoke bracket (two short posts + a base foot) holding the can up
  // off the floor/mount point
  const yoke = new THREE.Group();
  yoke.name = "yoke";
  root.add(yoke);
  const footGeo = new THREE.CylinderGeometry(canR * 0.5, canR * 0.55, yokeH * 0.25, 16);
  const foot = new THREE.Mesh(footGeo, bodyMat);
  foot.position.y = yokeH * 0.125;
  yoke.add(foot);
  const postGeo = new THREE.CylinderGeometry(canR * 0.12, canR * 0.12, yokeH, 10);
  [-1, 1].forEach((s) => {
    const post = new THREE.Mesh(postGeo, bodyMat);
    post.position.set(s * canR * 0.55, yokeH * 0.5 + yokeH * 0.25, 0);
    yoke.add(post);
  });

  // the can itself - a short "puck" cylinder, flat round face up (+Y),
  // matching the procedural "par" kind's beam-points-up convention
  const head = new THREE.Group();
  head.name = "head";
  head.position.y = yokeH + canH / 2;
  root.add(head);
  const canGeo = new THREE.CylinderGeometry(canR, canR, canH, 28);
  const can = new THREE.Mesh(canGeo, bodyMat);
  head.add(can);
  const faceGeo = new THREE.CylinderGeometry(canR * 0.96, canR * 0.96, canH * 0.06, 28);
  const faceMat = new THREE.MeshStandardMaterial({ color: 0x08090a, roughness: 0.3, metalness: 0.1 });
  const face = new THREE.Mesh(faceGeo, faceMat);
  face.position.y = canH / 2;
  head.add(face);

  // 5 emitters: 1 centre + 4 around at VPAR_RING_RADIUS_IN
  const emitters = [];
  const lensMeshes = [];
  const emitterMat = new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0x222222, roughness: 0.25 });
  const positions = [[0, 0]];
  for (let i = 0; i < 4; i++) {
    const ang = (i / 4) * Math.PI * 2;
    positions.push([Math.cos(ang) * VPAR_RING_RADIUS_IN * IN, Math.sin(ang) * VPAR_RING_RADIUS_IN * IN]);
  }
  positions.forEach((p, i) => {
    const lensGeo = new THREE.CylinderGeometry(VPAR_EMITTER_LENS_IN * IN * 0.5, VPAR_EMITTER_LENS_IN * IN * 0.5, canH * 0.08, 12);
    const lensMesh = new THREE.Mesh(lensGeo, emitterMat.clone());
    lensMesh.name = "emitter" + i;
    lensMesh.position.set(p[0], canH / 2 + canH * 0.04, p[1]);
    head.add(lensMesh);
    lensMeshes.push(lensMesh);

    const emitter = new THREE.Object3D();
    emitter.name = "emitterLens" + i;
    emitter.position.copy(lensMesh.position);
    head.add(emitter);
    emitters.push(emitter);
  });

  const lens = emitters[0]; // centre emitter doubles as the single-beam fallback anchor

  return {
    root,
    setPanTilt: () => {}, // fixed manual-tilt bracket, no DMX pan/tilt motor
    lens,
    emitters,
    lensMeshes,
    lensRadiusInches: VPAR_EMITTER_LENS_IN / 2,
    beamAngleDeg: VPAR_EMITTER_BEAM_DEG,
    heightInches: VPAR_YOKE_HEIGHT_IN + VPAR_DEPTH_IN,
    dims: [VPAR_DIAMETER_IN, VPAR_DIAMETER_IN, VPAR_YOKE_HEIGHT_IN + VPAR_DEPTH_IN],
    movable: false,
    id: "builtin/adj-vpar",
    name: (entry && entry.name) || "American DJ VPar",
    category: (entry && entry.category) || "par",
    gobos: [],
    prismFacets: null,
    dispose() {
      if (root.parent) root.parent.remove(root);
      root.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) o.material.dispose();
      });
    },
  };
}

// ---------------------------------------------------------------- public --

/** Fetches (and caches, per baseUrl) /stage-lib/index.json. */
export async function loadLookIndex(baseUrl = "/stage-lib/") {
  lastBaseUrl = baseUrl;
  if (indexCache.has(baseUrl)) return indexCache.get(baseUrl);
  const p = fetch(joinUrl(baseUrl, "index.json"))
    .then((res) => {
      if (!res.ok) throw new Error(`stage-looks: failed to fetch look index "${baseUrl}" (HTTP ${res.status})`);
      return res.json();
    })
    .catch((err) => {
      indexCache.delete(baseUrl);
      throw err;
    });
  indexCache.set(baseUrl, p);
  return p;
}

/**
 * Builds a rigged, animatable three.js object for one fixture "look".
 * @param {string|number|object} lookIdOrEntry a look id (looked up via
 *   loadLookIndex) or an index/look entry object directly.
 * @param {{baseUrl?: string}} [opts]
 * @returns {Promise<{
 *   root: THREE.Group,
 *   setPanTilt: (panDeg:number, tiltDeg:number) => void,
 *   lens: THREE.Object3D,
 *   lensMeshes: THREE.Mesh[],
 *   lensRadiusInches: number|null,
 *   beamAngleDeg: number,
 *   heightInches: number|null,
 *   dims: number[]|null,
 *   movable: boolean,
 *   dispose: () => void,
 * }>}
 */
export async function buildLook(lookIdOrEntry, opts = {}) {
  if (lookIdOrEntry === "builtin/adj-vpar" || (lookIdOrEntry && lookIdOrEntry.id === "builtin/adj-vpar")) {
    return buildAdjVpar(typeof lookIdOrEntry === "object" ? lookIdOrEntry : null);
  }
  const baseUrl = opts.baseUrl || lastBaseUrl || "/stage-lib/";
  let entry = lookIdOrEntry;
  if (typeof lookIdOrEntry === "string" || typeof lookIdOrEntry === "number") {
    const idx = await loadLookIndex(baseUrl);
    entry = (idx.looks || []).find((l) => l.id === lookIdOrEntry);
    if (!entry) throw new Error(`stage-looks: unknown look id "${lookIdOrEntry}"`);
  }
  if (!entry) throw new Error("stage-looks: buildLook requires a look id or an index entry");

  if (entry.path) {
    const lookUrl = joinUrl(baseUrl, entry.path);
    const lookDir = dirnameUrl(lookUrl);
    let look;
    try {
      const res = await fetch(lookUrl);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      look = await res.json();
    } catch (err) {
      throw new Error(`stage-looks: failed to fetch look "${lookUrl}": ${(err && err.message) || err}`);
    }
    return buildFromGdtfLook(look, lookDir, entry);
  }
  if (entry.model) {
    const modelUrl = joinUrl(baseUrl, entry.model);
    return buildFromBuiltinModel(modelUrl, entry);
  }
  throw new Error(`stage-looks: look "${entry.id}" has neither "path" nor "model"`);
}

/** Offscreen 3/4-view render of a look for a future look-picker gallery.
 * The beam is never lit (buildLook never creates a beam cone itself). */
export async function renderLookThumbnail(entry, size = 160) {
  const built = await buildLook(entry);
  const scene = new THREE.Scene();
  scene.add(built.root);
  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
  dirLight.position.set(2, 3, 2);
  scene.add(dirLight);
  const rim = new THREE.DirectionalLight(0xffffff, 0.3);
  rim.position.set(-2, 1, -1.5);
  scene.add(rim);

  const box = new THREE.Box3().setFromObject(built.root);
  const size3 = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size3.length() * 0.5, 0.15);

  const camera = new THREE.PerspectiveCamera(35, 1, radius / 50, radius * 30);
  camera.position.copy(center).add(new THREE.Vector3(radius * 1.6, radius * 1.3, radius * 1.6));
  camera.lookAt(center);

  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, alpha: true });
  renderer.setSize(size, size, false);
  renderer.setClearColor(0x000000, 0);
  renderer.render(scene, camera);
  const dataUrl = canvas.toDataURL("image/png");
  renderer.dispose();
  built.dispose();
  return dataUrl;
}

// ------------------------------------------------------------- selftest --

/** Pure-maths self-test (no network) of the GDTF->local conjugation. */
export function selftest() {
  const results = [];
  let pass = 0;
  let fail = 0;
  function check(name, ok, detail) {
    results.push({ name, ok, detail });
    if (ok) pass++;
    else fail++;
  }
  function vClose(a, b, eps = 1e-6) {
    return Math.abs(a.x - b.x) < eps && Math.abs(a.y - b.y) < eps && Math.abs(a.z - b.z) < eps;
  }

  // C itself (the fixed per-node conjugating rotation) should send GDTF's
  // beam -Z to our local +Y, and leave the tilt/X line unchanged.
  const beamDir = new THREE.Vector3(0, 0, -1).applyMatrix4(C4);
  check("beam -Z -> local +Y", vClose(beamDir, new THREE.Vector3(0, 1, 0)), "got " + JSON.stringify(beamDir));
  const tiltDir = new THREE.Vector3(1, 0, 0).applyMatrix4(C4);
  check("tilt/X axis unchanged", vClose(tiltDir, new THREE.Vector3(1, 0, 0)), "got " + JSON.stringify(tiltDir));

  // A part translated "down the hang" in GDTF (-Z, i.e. body extends away
  // from the mount toward -Z; translation lives in the 4th column of row 3,
  // index 11, per GDTF's row-major affine layout) should land above the
  // origin (+Y) in ours, once run through convertLocalMatrix() (used on
  // real per-part local matrices).
  const down16 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -0.3, 0, 0, 0, 1];
  const Md = convertLocalMatrix(down16);
  const pos = new THREE.Vector3().setFromMatrixPosition(Md);
  check("GDTF -Z translation -> local +Y", pos.y > 0 && Math.abs(pos.x) < 1e-6 && Math.abs(pos.z) < 1e-6, "got " + JSON.stringify(pos));

  return { pass, fail, results };
}
