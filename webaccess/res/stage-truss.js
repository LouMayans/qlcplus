/*
  stage-truss.js

  Realistic aluminium truss geometry for "truss" prop parts (see
  stage-props.js's createPartMesh). Builds chords, zig-zag diagonal lacing
  on every face, and end-plate/coupler rings at real segment spacing, then
  merges everything into ONE BufferGeometry (draw-call friendly) with a
  single brushed-metal PBR material.

  All numbers in this file are in *inches* until the final geometry
  construction, which converts with IN (matching stage-props.js's
  convention: three.js units = inches * 0.0254). A truss part's own local
  origin is its geometric centre (matching box/cylinder/etc parts), with
  its length running along local X - stage-props.js's part.pos/part.rot
  then place and orient the whole thing like any other part.
*/

import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

const IN = 0.0254;

// Cross-section corner offsets [y,z] (inches) around the truss centreline.
const PROFILES = {
  box12: { corners: [[-6, -6], [6, -6], [6, 6], [-6, 6]], plateSize: 12.5 },
  box16: { corners: [[-8, -8], [8, -8], [8, 8], [-8, 8]], plateSize: 16.5 },
  tri12: {
    corners: [
      [0, 6],
      [5.196, -3],
      [-5.196, -3],
    ],
    plateSize: 13,
  },
};
export const TRUSS_TYPES = Object.keys(PROFILES);

const CHORD_RADIUS_IN = 1; // 2" OD chords
const DIAG_RADIUS_IN = 0.5; // 1" OD diagonal lacing
const LACING_PITCH_IN = 24; // zig-zag node spacing
const PLATE_PITCH_IN = 98.4; // ~2.5m (8.2') between couplers/end plates
const PLATE_THICKNESS_IN = 1;

// A straight cylindrical strut geometry, in inches, from p0 to p1 (both
// [x,y,z] inches). Already scaled to three.js units (metres).
function strutGeometry(p0, p1, radiusIn, radialSegments) {
  const a = new THREE.Vector3(p0[0] * IN, p0[1] * IN, p0[2] * IN);
  const b = new THREE.Vector3(p1[0] * IN, p1[1] * IN, p1[2] * IN);
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length();
  if (len < 1e-6) return null;
  const geo = new THREE.CylinderGeometry(radiusIn * IN, radiusIn * IN, len, radialSegments || 8, 1, false);
  geo.translate(0, len / 2, 0); // base at local origin, tip at +Y
  const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  geo.applyQuaternion(quat);
  geo.translate(a.x, a.y, a.z);
  return geo;
}

function plateGeometry(xIn, sizeIn, thicknessIn) {
  const geo = new THREE.BoxGeometry(thicknessIn * IN, sizeIn * IN, sizeIn * IN);
  geo.translate(xIn * IN, 0, 0);
  return geo;
}

// Zig-zag lacing between two chord lines (cornerA/cornerB are [y,z]
// inches), spanning x from -halfLen to +halfLen along the truss.
function addZigzag(list, cornerA, cornerB, halfLen, pitch, radius) {
  const totalLen = halfLen * 2;
  const n = Math.max(2, Math.round(totalLen / pitch));
  const step = totalLen / n;
  for (let i = 0; i < n; i++) {
    const x0 = -halfLen + i * step;
    const x1 = -halfLen + (i + 1) * step;
    const from = i % 2 === 0 ? cornerA : cornerB;
    const to = i % 2 === 0 ? cornerB : cornerA;
    const g = strutGeometry([x0, from[0], from[1]], [x1, to[0], to[1]], radius, 6);
    if (g) list.push(g);
  }
}

/**
 * Builds a truss mesh for a part `{trussType:"box12"|"tri12"|"box16", length}`
 * (length in inches; defaults to 10'). Centred at local origin, length
 * along local X.
 */
export function buildTruss(part) {
  const type = PROFILES[part && part.trussType] ? part.trussType : "box12";
  const profile = PROFILES[type];
  const corners = profile.corners;
  const lengthIn = Math.max((part && part.length) || 120, 12);
  const half = lengthIn / 2;

  const geos = [];

  // chords: one continuous rod per corner, full length
  corners.forEach((c) => {
    const g = strutGeometry([-half, c[0], c[1]], [half, c[0], c[1]], CHORD_RADIUS_IN, 8);
    if (g) geos.push(g);
  });

  // diagonal lacing on every face (each pair of adjacent corners)
  const n = corners.length;
  for (let i = 0; i < n; i++) {
    addZigzag(geos, corners[i], corners[(i + 1) % n], half, LACING_PITCH_IN, DIAG_RADIUS_IN);
  }

  // end plates / couplers: both true ends plus every PLATE_PITCH_IN between
  const plateXs = [-half, half];
  for (let x = -half + PLATE_PITCH_IN; x < half - 1; x += PLATE_PITCH_IN) plateXs.push(x);
  plateXs.forEach((x) => geos.push(plateGeometry(x, profile.plateSize, PLATE_THICKNESS_IN)));

  const merged = mergeGeometries(geos.filter(Boolean), false) || new THREE.BoxGeometry(0.01, 0.01, 0.01);
  const material = new THREE.MeshStandardMaterial({
    color: 0xcfd2d6,
    roughness: 0.35,
    metalness: 0.9,
  });
  const mesh = new THREE.Mesh(merged, material);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData.trussType = type;
  return mesh;
}
