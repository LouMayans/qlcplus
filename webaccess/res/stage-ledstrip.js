/*
  stage-ledstrip.js

  Builds an LED strip prop part for the 3D stage visualizer (R group - see
  .claude/memory/stage-visualizer.md "Overnight build contracts": P's
  stage-props.js createPartMesh() calls buildLedStrip(part) for part shape
  "ledstrip" = {length, ledsPerMeter:60, color, brightness, fixtureId?}).

  Efficient by construction: one InstancedMesh for every LED dot (a tiny
  emissive capsule) rather than one mesh per LED, plus a single translucent
  diffuser box over the top (catches bloom as a soft continuous glow line)
  and a handful of RectAreaLights/PointLights along the strip's length for
  a cheap actual light contribution (capped, since these are real lights -
  see LED_LIGHT_STEP below).

  Coordinate convention matches stage-props.js part conventions: the
  strip's own local origin is its centre, extending along local +X by
  `length` (inches) - same axis role BoxGeometry([length,...]) parts use
  elsewhere in stage-props.js (`part(shape,size,pos,rot,...)` with
  size=[w,d,h] and the box's long axis conventionally X unless rotated by
  the caller), height (Y) and depth (Z) are fixed, small physical-strip
  dimensions.

  Exports:
    buildLedStrip(part) -> THREE.Object3D with:
      .update({color, dimmer}) - drives LED emissive colour/brightness and
        the cheap light contribution; dimmer 0..1, color "#rrggbb".
      .setLightBudget(maxLights) - re-run if a Settings change lowers the
        global light budget after the strip was built (see stage-scene.js).
      .dispose()
*/

import * as THREE from "three";
import { RectAreaLightUniformsLib } from "three/addons/lights/RectAreaLightUniformsLib.js";

// Required once for THREE.RectAreaLight to actually light anything (LTC
// look-up textures) - idempotent, safe even if called again elsewhere.
RectAreaLightUniformsLib.init();

const IN = 0.0254;
const STRIP_HEIGHT_IN = 0.35; // physical LED PCB+diffuser strip cross-section
const STRIP_DEPTH_IN = 0.5;
const LED_DOT_DIAMETER_IN = 0.16;

// One real light per this many LEDs, at minimum this many mm apart - keeps
// a 10ft/60-per-m strip (≈180 LEDs) from ever wanting 180 RectAreaLights.
const LEDS_PER_LIGHT = 12;
const MAX_LIGHTS_PER_STRIP = 8;

function ledDotGeometry() {
  const r = (LED_DOT_DIAMETER_IN * IN) / 2;
  return new THREE.SphereGeometry(r, 8, 6);
}

/**
 * @param {{length:number, ledsPerMeter?:number, color?:string,
 *   brightness?:number, fixtureId?:number|string}} part  lengths in inches
 */
export function buildLedStrip(part) {
  const lengthIn = Math.max(part.length || 12, 1);
  const lengthM = lengthIn * IN;
  const ledsPerMeter = part.ledsPerMeter || 60;
  const count = Math.max(1, Math.round(lengthM * ledsPerMeter));
  const baseColor = new THREE.Color(part.color || "#ffffff");
  const baseBrightness = part.brightness != null ? part.brightness : 1;

  const group = new THREE.Object3D();
  group.name = "ledStrip";

  // ---- diffuser body (cheap box, catches/diffuses the dot glow) --------
  const bodyMat = new THREE.MeshStandardMaterial({
    color: 0x141414,
    roughness: 0.4,
    metalness: 0.1,
  });
  const body = new THREE.Mesh(new THREE.BoxGeometry(lengthM, STRIP_HEIGHT_IN * IN, STRIP_DEPTH_IN * IN), bodyMat);
  body.castShadow = false;
  body.receiveShadow = true;
  group.add(body);

  const diffuserMat = new THREE.MeshPhysicalMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.35,
    roughness: 0.3,
    metalness: 0,
    emissive: baseColor.clone(),
    emissiveIntensity: baseBrightness * 0.8,
  });
  const diffuser = new THREE.Mesh(
    new THREE.BoxGeometry(lengthM, STRIP_HEIGHT_IN * IN * 0.7, STRIP_DEPTH_IN * IN * 0.7),
    diffuserMat
  );
  diffuser.position.y = STRIP_HEIGHT_IN * IN * 0.16;
  group.add(diffuser);

  // ---- individual LED dots (InstancedMesh - one draw call for all of them) --
  const dotGeo = ledDotGeometry();
  const dotMat = new THREE.MeshStandardMaterial({
    color: baseColor.clone(),
    emissive: baseColor.clone(),
    emissiveIntensity: baseBrightness * 1.5,
    roughness: 0.4,
    metalness: 0,
  });
  const dots = new THREE.InstancedMesh(dotGeo, dotMat, count);
  dots.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  const m4 = new THREE.Matrix4();
  const half = lengthM / 2;
  for (let i = 0; i < count; i++) {
    const t = count === 1 ? 0.5 : i / (count - 1);
    const x = -half + t * lengthM;
    m4.makeTranslation(x, STRIP_HEIGHT_IN * IN * 0.35, 0);
    dots.setMatrixAt(i, m4);
  }
  dots.instanceMatrix.needsUpdate = true;
  group.add(dots);

  // ---- cheap "real" light contribution, capped, OFF BY DEFAULT ----------
  // Perf incident (2026-09-29): every strip used to create up to
  // MAX_LIGHTS_PER_STRIP RectAreaLights, always .visible regardless of
  // quality/dimmer. RectAreaLight uses an LTC evaluation that's one of the
  // most expensive per-fragment light types three.js has, and with 3 decor
  // strips in the club scene that was 24 always-on area lights evaluated on
  // every lit fragment of every material at full resolution - confirmed (by
  // another agent's scene audit, this session) as the dominant contributor to
  // Medium dropping from 57-120fps to ~24fps on the operator's Intel UHD
  // laptop. The strips are also currently unlit by operator request (no
  // demo/show data creates them right now). So: a strip creates ZERO real
  // lights unless a caller explicitly opts in via `part.areaLights: true` or
  // a positive `part.lightBudget` - the diffuser/dot meshes still glow via
  // emissive materials either way (the "plain mesh, no real light" look).
  // Even when opted in, the count is still fixed at build time (never
  // changed per-frame/per-dimmer - see the "fixed slot" rule in
  // .claude/memory/stage-visualizer.md) and capped at MAX_LIGHTS_PER_STRIP.
  const wantAreaLights = part.areaLights === true || (typeof part.lightBudget === "number" && part.lightBudget > 0);
  const desiredLights = wantAreaLights ? Math.min(MAX_LIGHTS_PER_STRIP, Math.max(1, Math.round(count / LEDS_PER_LIGHT))) : 0;
  const lights = [];
  let lightBudget = desiredLights;

  const RectAreaLightCtor = THREE.RectAreaLight; // core three; uniforms lib is inited by stage-scene.js once
  for (let i = 0; i < desiredLights; i++) {
    const t = desiredLights === 1 ? 0.5 : i / (desiredLights - 1);
    const x = -half + t * lengthM;
    let light;
    if (RectAreaLightCtor) {
      light = new RectAreaLightCtor(baseColor.getHex(), baseBrightness * 0.6, lengthM / desiredLights, STRIP_DEPTH_IN * IN);
      light.rotation.x = -Math.PI / 2; // shine downward/outward from the strip face
    } else {
      light = new THREE.PointLight(baseColor.getHex(), baseBrightness * 0.4, 1.2, 2);
    }
    light.position.set(x, STRIP_HEIGHT_IN * IN * 0.4, 0);
    light.visible = i < lightBudget;
    group.add(light);
    lights.push(light);
  }

  function setLightBudget(maxLights) {
    lightBudget = Math.max(0, Math.min(desiredLights, maxLights == null ? desiredLights : maxLights));
    lights.forEach((l, i) => {
      l.visible = i < lightBudget;
    });
  }

  function update(state) {
    const s = state || {};
    const color = new THREE.Color(s.color != null ? s.color : part.color || "#ffffff");
    const dimmer = s.dimmer != null ? Math.max(0, Math.min(1, s.dimmer)) : baseBrightness;
    dotMat.color.copy(color);
    dotMat.emissive.copy(color);
    // No baseline glow floor (operator ask, materials/bounce pass): a static
    // decor strip at brightness/dimmer 0 must read as fully unlit, not a dim
    // "always-on" LED - same max (2.0) at dimmer 1 as the old 0.3+dimmer*1.7.
    dotMat.emissiveIntensity = dimmer * 2.0;
    diffuserMat.emissive.copy(color);
    diffuserMat.emissiveIntensity = dimmer * 0.9;
    diffuserMat.opacity = 0.2 + dimmer * 0.3;
    lights.forEach((l) => {
      l.color.copy(color);
      l.intensity = (l.isRectAreaLight ? 0.6 : 0.4) * dimmer;
    });
  }

  // apply the part's own static defaults once up front
  update({ color: part.color, dimmer: baseBrightness });

  group.update = update;
  group.setLightBudget = setLightBudget;
  group.dispose = function () {
    dotGeo.dispose();
    dotMat.dispose();
    body.geometry.dispose();
    bodyMat.dispose();
    diffuser.geometry.dispose();
    diffuserMat.dispose();
  };
  group.userData.ledCount = count;
  group.userData.lightCount = lights.length;
  return group;
}
