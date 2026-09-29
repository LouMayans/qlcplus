/*
  stage-beams.js

  Volumetric fixture beams for the 3D stage visualizer (R/render group - see
  .claude/memory/stage-visualizer.md "Overnight build contracts").

  Replaces the old solid additive cone with a soft, view-angle-dependent
  "fake volumetric" shaft, the same technique used by most real-time
  volumetric-spotlight demos (an open-ended, double-sided cone, additive
  blending, no depth write):
    - Radial "gaussian" softness comes from a Fresnel-style term: a cone
      fragment facing the camera almost head-on (viewDot near 1) reads as
      the visible CENTRE of the shaft on screen; a fragment at the cone's
      silhouette (viewDot near 0, grazing/edge-on) reads as the shaft's
      OUTER EDGE. Intensity ~ pow(viewDot, power) therefore fades smoothly
      from a bright centre to nothing at the silhouette - a soft edge with
      no hard rim, and (bonus) it is inherently view-angle dependent, which
      is also one of the goals on its own.
    - An optional inner "core" layer (a second, narrower copy of the same
      cone, quality >= 1) adds a denser hot centre on top, which combined
      with the outer shell's soft fresnel falloff reads as a genuine
      radial gaussian profile without the cost of a real volume render.
    - Distance fade uses a haze extinction term (exp(-density * distance)),
      not a hard length cutoff - the mesh itself already stops wherever the
      caller (stage-scene.js's computeBeamLength) says it hits something;
      the shader just makes that stop feel soft rather than a lit rod that
      switches off.
    - Animated 3D simplex noise (quality >= 2) modulates the haze density
      for a subtle "moving smoke" look.
    - Soft intersection with solid geometry: a small depth pre-pass (see
      stage-render.js, which renders scene depth - EXCLUDING beams, via the
      LAYER_BEAM camera-layer toggle exported below - into its own
      WebGLRenderTarget/DepthTexture before the main composer pass runs)
      lets each beam fragment compare its own view-space depth against the
      real scene's, fading to 0 just before it would poke through a floor
      or wall instead of hard-clipping (soft particles).

  World-scale independence (bug fix, see stage-scene.js swapInLook): a
  beam mesh is parented under a look's `lens` node, which sits somewhere
  under the look's `root`, and `root` may carry a uniform bodyScale (e.g.
  0.35 for the generic QLC+ moving-head model). Writing WORLD-space length/
  radius numbers straight into the beam's own local-space geometry would
  then get silently shrunk again by that ancestor scale when the scene
  renders it. setBeamWorldShape() takes an explicit `parentWorldScale`
  (the beam's parent's accumulated world scale - see
  getUniformWorldScale()) and divides by it before writing local geometry,
  so the beam always reaches its intended WORLD length/width regardless of
  how the fixture body is scaled.

  Exports:
    LAYER_BEAM                              - camera layer bit used to
                                               exclude beams from the depth
                                               pre-pass (see stage-render.js).
    getUniformWorldScale(object3D)          - helper, see above.
    createVolumetricBeam(opts)              -> { mesh, dispose() }
    setBeamWorldShape(beam, nearM, farM, lengthM, parentScale)
    updateBeamLook(beam, params)            - colour/intensity/quality/etc.
    updateBeamDepthUniforms(beam, camera, depthTexture, resolution)
    createBeamSpotLight()                   -> THREE.SpotLight, pre-configured
    fitSpotLightToBeam(light, beam, state)  - colour/angle/penumbra/decay/pos
    createBounceLight()                     -> THREE.PointLight, pre-configured
    estimateSurfaceBounce(material)         -> {color, albedo} - material-aware bounce tint/strength
    fitBounceLight(light, pt, color, dimmer, surface, bounceIntensity)
    pickTopByBrightness(list, n)            - small budgeting helper
*/

import * as THREE from "three";

export const LAYER_BEAM = 7; // an otherwise-unused bit; see stage-render.js depth pre-pass

const FT = 12;
const IN = 0.0254;

// ---------------------------------------------------------------- utils --

const _scaleV = new THREE.Vector3();
/** The object's accumulated WORLD scale, collapsed to one number (our
 * scales are always uniform - bodyScale is set via .setScalar()). */
export function getUniformWorldScale(object3D) {
  if (!object3D) return 1;
  object3D.getWorldScale(_scaleV);
  const s = (_scaleV.x + _scaleV.y + _scaleV.z) / 3;
  return s > 1e-6 ? s : 1;
}

// ---------------------------------------------------- shared geometry ----

// Same unit-template trick as the old createBeam(): a unit-radius open
// cylinder spanning local y 0..1 (near ring = lens, at y=0). Perf: this
// geometry is now completely static (never rewritten per frame) - the
// vertex shader (BEAM_VERT) reshapes it into the true frustum every frame
// from uNearRadius/uFarRadius/uLocalLength uniforms, so one shared copy per
// segment count serves every beam instance (see sharedGeometry below).
function makeUnitConeGeometry(segments) {
  const geo = new THREE.CylinderGeometry(1, 1, 1, segments, 1, true);
  geo.translate(0, 0.5, 0);
  return geo;
}

const geomCache = new Map(); // segments -> geometry (shared, never disposed per-instance)
function sharedGeometry(segments) {
  let g = geomCache.get(segments);
  if (!g) {
    g = makeUnitConeGeometry(segments);
    geomCache.set(segments, g);
  }
  return g;
}

// -------------------------------------------------------------- shader ---

// Compact 3D simplex noise (public-domain form, Ashima Arts / Ian McEwan;
// this exact GLSL is the version ubiquitously reused across three.js/webgl
// noise demos). Only compiled in when uNoiseAmount-driven animation is on
// (beams.quality >= 2) - see BEAM_VERT/BEAM_FRAG below.
const SIMPLEX3 = `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

// Perf: the unit-cylinder template geometry (position.xz on a unit circle,
// position.y in {0 (near/lens ring), 1 (far ring)} - see makeUnitConeGeometry)
// is now GPU-shaped every frame via uNearRadius/uFarRadius/uLocalLength
// uniforms instead of a CPU rewrite of the position buffer (setMeshWorldShape
// used to touch every vertex, then call geo.computeVertexNormals() and
// geo.computeBoundingSphere() - all CPU, all every frame, per beam per
// shell/core layer). The frustum's slanted normal is derived analytically
// here (surface-of-revolution normal for a cone whose radius changes
// linearly with y) rather than read from a (now-static, generic-cylinder)
// normal attribute, so the view-angle Fresnel falloff in the fragment
// shader still looks correct even at a steep spread angle.
const BEAM_VERT = `
uniform float uNearRadius;  // local units (already divided by parent world scale - see JS)
uniform float uFarRadius;   // local units
uniform float uLocalLength; // local units - scales the template's y in [0,1]
varying vec3 vWorldPos;
varying vec3 vViewPos;
varying vec3 vNormalW;
varying float vLocalY; // 0 at the lens, 1 at the far end
void main(){
  vLocalY = position.y; // template spans local y 0..1
  float r = mix(uNearRadius, uFarRadius, step(0.5, position.y));
  vec3 localPos = vec3(position.x * r, position.y * uLocalLength, position.z * r);
  // Analytic cone-frustum normal: position.xz already lies on the unit
  // circle (cos/sin of the radial angle), so the outward slant normal is
  // proportional to (uLocalLength*cos, -(farR-nearR), uLocalLength*sin),
  // normalized. Degenerates gracefully to the plain radial cylinder normal
  // when nearR==farR (uLocalLength term dominates -> normal ~ (cos,0,sin)).
  vec3 localNormal = normalize(vec3(position.x * uLocalLength, -(uFarRadius - uNearRadius), position.z * uLocalLength));
  vec4 worldPos = modelMatrix * vec4(localPos, 1.0);
  vWorldPos = worldPos.xyz;
  vNormalW = normalize(mat3(modelMatrix) * localNormal);
  vec4 mv = modelViewMatrix * vec4(localPos, 1.0);
  vViewPos = mv.xyz;
  gl_Position = projectionMatrix * mv;
}`;

const BEAM_FRAG = (withNoise, withDepth) => `
precision mediump float;
varying vec3 vWorldPos;
varying vec3 vViewPos;
varying vec3 vNormalW;
varying float vLocalY;

uniform vec3 uColor;
uniform float uIntensity;      // dimmer * brightnessMul * layer weight
uniform float uSoftness;       // 0 (hard) .. 1 (very soft)
uniform float uHazeDensity;    // per-metre extinction
uniform float uLength;         // world-space beam length, metres
uniform vec3 uCameraPos;
uniform float uTime;
${withNoise ? "uniform float uNoiseAmount;\nuniform float uNoiseFreq;\nuniform float uNoiseSpeed;" : ""}
${withDepth ? "uniform float uHasDepth;\nuniform sampler2D uDepthTexture;\nuniform vec2 uResolution;\nuniform float uCameraNear;\nuniform float uCameraFar;\nuniform float uSoftIntersect;" : ""}

${withNoise ? SIMPLEX3 : ""}

${withDepth ? `
float perspectiveDepthToDist(float z, float near, float far){
  float zN = z * 2.0 - 1.0;
  return (2.0 * near * far) / (far + near - zN * (far - near));
}` : ""}

void main(){
  vec3 viewDir = normalize(uCameraPos - vWorldPos);
  float viewDot = clamp(abs(dot(normalize(vNormalW), viewDir)), 0.0, 1.0);
  // soft "radial gaussian": bright where the surface faces the camera
  // (screen-space centre of the shaft), fading to 0 at the silhouette
  // (screen-space edge) - no hard rim, see file header.
  float power = mix(3.2, 0.7, clamp(uSoftness, 0.0, 1.0));
  float radial = pow(viewDot, power);

  float dist = clamp(vLocalY, 0.0, 1.0) * uLength;
  float extinction = exp(-uHazeDensity * dist);

  float haze = 1.0;
  ${withNoise ? `
  float n = snoise(vWorldPos * uNoiseFreq + vec3(0.0, 0.0, uTime * uNoiseSpeed));
  haze = clamp(1.0 + uNoiseAmount * (n * 0.6), 0.0, 1.6);
  ` : ""}

  float alpha = radial * extinction * haze * uIntensity;

  ${withDepth ? `
  vec2 screenUv = gl_FragCoord.xy / uResolution;
  float sceneDepth = texture2D(uDepthTexture, screenUv).x;
  // an unbound sampler reads 0 ("everything is in front of the beam"), which hid every beam on Low
  if (uHasDepth > 0.5 && sceneDepth < 1.0) {
    float sceneDist = perspectiveDepthToDist(sceneDepth, uCameraNear, uCameraFar);
    float fragDist = -vViewPos.z;
    float diff = sceneDist - fragDist;
    float fade = clamp(diff / max(uSoftIntersect, 0.001), 0.0, 1.0);
    alpha *= fade;
  }
  ` : ""}

  if (alpha < 0.003) discard;
  gl_FragColor = vec4(uColor * alpha, alpha);
}`;

function makeBeamMaterial(quality) {
  const withNoise = quality >= 2;
  const withDepth = true; // cheap; only samples if a depthTexture uniform is actually bound
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      uColor: { value: new THREE.Color(0xffffff) },
      uIntensity: { value: 0 },
      uSoftness: { value: 0.6 },
      uHazeDensity: { value: 0.08 },
      uLength: { value: 1 },
      uNearRadius: { value: 0.01 },
      uFarRadius: { value: 0.1 },
      uLocalLength: { value: 1 },
      uCameraPos: { value: new THREE.Vector3() },
      uTime: { value: 0 },
      uNoiseAmount: { value: 0.15 },
      uNoiseFreq: { value: 2.0 },
      uNoiseSpeed: { value: 0.15 },
      uHasDepth: { value: 0 },
      uDepthTexture: { value: null },
      uResolution: { value: new THREE.Vector2(1, 1) },
      uCameraNear: { value: 0.05 },
      uCameraFar: { value: 500 },
      uSoftIntersect: { value: 0.35 },
    },
    vertexShader: BEAM_VERT,
    fragmentShader: BEAM_FRAG(withNoise, withDepth),
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    depthTest: true,
    side: THREE.DoubleSide,
  });
  mat.userData.withNoise = withNoise;
  return mat;
}

// ----------------------------------------------------------- public API --

/**
 * Builds one volumetric beam object (a THREE.Group so an optional inner
 * "core" layer can ride alongside the outer shell without callers caring).
 * @param {{quality?:0|1|2|3, segments?:number}} [opts]
 */
export function createVolumetricBeam(opts) {
  const o = opts || {};
  const quality = o.quality != null ? o.quality : 2;
  const segments = o.segments != null ? o.segments : quality <= 0 ? 10 : quality === 1 ? 14 : 22;

  const group = new THREE.Group();
  group.name = "volumetricBeam";
  group.layers.set(LAYER_BEAM);
  group.renderOrder = 10;

  // Perf: geometry is now the shared, never-mutated unit template (no
  // per-instance clone, no per-frame position/normal rewrite - see
  // BEAM_VERT). Every beam instance of this segment count reuses the same
  // GPU buffer; shaping happens entirely through this mesh's own material
  // uniforms (uNearRadius/uFarRadius/uLocalLength), which uniquely
  // parameterize the frustum per instance.
  const geo = sharedGeometry(segments);
  const shellMat = makeBeamMaterial(quality);
  const shell = new THREE.Mesh(geo, shellMat);
  shell.layers.set(LAYER_BEAM);
  shell.frustumCulled = false; // shape lives in the shader; the static template's bounds don't reflect it
  group.add(shell);

  let core = null;
  if (quality >= 1) {
    const coreGeo = sharedGeometry(Math.max(8, Math.round(segments * 0.7)));
    const coreMat = makeBeamMaterial(quality);
    coreMat.uniforms.uSoftness.value = 0.85;
    core = new THREE.Mesh(coreGeo, coreMat);
    core.layers.set(LAYER_BEAM);
    core.frustumCulled = false;
    group.add(core);
  }

  group.userData.shell = shell;
  group.userData.core = core;
  group.userData.quality = quality;

  group.dispose = function () {
    // geometry is the shared cache entry (see sharedGeometry) - never
    // disposed per-instance, only the per-instance material.
    [shell, core].forEach((m) => {
      if (!m) return;
      m.material.dispose();
    });
  };
  return group;
}

/** Re-derives a beam's frustum shape by updating its material's shaping
 * uniforms (uNearRadius/uFarRadius/uLocalLength - see BEAM_VERT), compensating
 * for the beam's ancestor world scale so the result is correct in WORLD
 * units regardless of a scaled-down fixture body. No geometry touched:
 * the GPU reshapes the shared unit-cylinder template per-vertex. */
function setMeshWorldShape(mesh, nearRadiusM, farRadiusM, lengthM, parentWorldScale) {
  const scale = parentWorldScale > 1e-6 ? parentWorldScale : 1;
  const u = mesh.material.uniforms;
  u.uNearRadius.value = nearRadiusM / scale;
  u.uFarRadius.value = farRadiusM / scale;
  u.uLocalLength.value = lengthM / scale;
  u.uLength.value = lengthM; // shader fades in WORLD metres, unaffected by parent scale
}

export function setBeamWorldShape(beamGroup, nearRadiusM, farRadiusM, lengthM, parentWorldScale) {
  const shell = beamGroup.userData.shell;
  const core = beamGroup.userData.core;
  setMeshWorldShape(shell, nearRadiusM, farRadiusM, lengthM, parentWorldScale);
  if (core) setMeshWorldShape(core, nearRadiusM * 0.42, farRadiusM * 0.42, lengthM, parentWorldScale);
}

/**
 * Updates a beam's visual "look" (colour/brightness/haze/noise/softness).
 * @param {object} params {color, intensity, softness, haze, noiseAmount,
 *   quality (0-3, informational only - set at creation), timeSec, cameraPos}
 */
export function updateBeamLook(beamGroup, params) {
  const p = params || {};
  const color = new THREE.Color(p.color || "#ffffff");
  [beamGroup.userData.shell, beamGroup.userData.core].forEach((m, idx) => {
    if (!m) return;
    const u = m.material.uniforms;
    u.uColor.value.copy(color);
    // haze shafts are translucent: at full strength the additive shell+core read as solid pillars
    u.uIntensity.value = Math.max(0, p.intensity || 0) * 0.45 * (idx === 1 ? 1.3 : 1); // core slightly hotter
    u.uSoftness.value = p.softness != null ? p.softness : 0.6;
    u.uHazeDensity.value = p.hazeDensity != null ? p.hazeDensity : 0.08;
    u.uTime.value = p.timeSec || 0;
    if (u.uNoiseAmount) u.uNoiseAmount.value = p.noiseAmount != null ? p.noiseAmount : 0.15;
    if (p.cameraPos) u.uCameraPos.value.copy(p.cameraPos);
  });
}

/** Binds the shared depth pre-pass texture/resolution/near-far to a beam's
 * materials (see stage-render.js). Safe to call with depthTexture=null
 * (disables soft-intersection cheaply: sceneDepth reads 1.0 => no fade). */
export function updateBeamDepthUniforms(beamGroup, camera, depthTexture, resolution, softIntersectM) {
  [beamGroup.userData.shell, beamGroup.userData.core].forEach((m) => {
    if (!m) return;
    const u = m.material.uniforms;
    u.uDepthTexture.value = depthTexture;
    u.uHasDepth.value = depthTexture ? 1 : 0;
    u.uResolution.value.set(resolution.x, resolution.y);
    u.uCameraNear.value = camera.near;
    u.uCameraFar.value = camera.far;
    if (softIntersectM != null) u.uSoftIntersect.value = softIntersectM;
  });
}

// ------------------------------------------------------------ real light --

/** A SpotLight pre-configured for "light coming out of a beam": physically
 * based decay=2, a penumbra so the lit disc on a surface has a soft edge. */
export function createBeamSpotLight() {
  const light = new THREE.SpotLight(0xffffff, 0, 30, Math.PI / 6, 0.4, 2);
  light.visible = false;
  return light;
}

/** Positions/aims/colours a SpotLight to match one beam's current head
 * transform and DMX state. `state` = {color, dimmer, beamSpreadDeg}. */
const _sp = new THREE.Vector3();
const _sq = new THREE.Quaternion();
const _sd = new THREE.Vector3();
export function fitSpotLightToBeam(light, headObject3D, state, lengthM, opts) {
  const o = opts || {};
  headObject3D.getWorldPosition(_sp);
  headObject3D.getWorldQuaternion(_sq);
  _sd.set(0, 1, 0).applyQuaternion(_sq);
  light.position.copy(_sp);
  // SpotLight.target normally needs to be added to the scene graph so its
  // matrixWorld updates every frame; we instead set its (parent-less, so
  // local==world) position directly and refresh its matrix by hand, which
  // is cheaper than adding another node to the graph per beam.
  light.target.position.copy(_sp).add(_sd.clone().multiplyScalar(Math.max(lengthM || 1, 0.1)));
  light.target.updateMatrixWorld(true);
  light.color.set(state.color || "#ffffff");
  const dimmer = Math.max(0, Math.min(1, state.dimmer || 0));
  // three.js r170 lights are physically-based: SpotLight.intensity is
  // candela (lm/sr), decay=2 is real inverse-square falloff. This room's
  // trusses sit ~4.4m (174") above the floor, so with the old default of 8cd
  // the floor-pool irradiance was ~8/4.4^2 =~ 0.4 - invisibly dim next to the
  // hemi(2.2)/directional(1.0) house light. ~hundreds of candela reads as a
  // visible bright pool without blowing out (verified live via screenshots).
  light.intensity = dimmer * (o.intensityMul != null ? o.intensityMul : 220);
  light.distance = Math.max(lengthM * 1.15 || 5, 1);
  light.angle = Math.max(THREE.MathUtils.degToRad((state.beamSpreadDeg || 15) / 2 + 2), 0.03);
  if (!(dimmer > 0.004 && state.shutter !== "closed")) light.intensity = 0;
}

/** A dim PointLight used to approximate one bounce of GI at a beam's floor
 * hit point. */
export function createBounceLight() {
  const light = new THREE.PointLight(0xffffff, 0, 4, 2);
  light.visible = false;
  return light;
}

// -------------------------------------------- material-aware bounce (goal 2) --
// Generic, preset-name-agnostic estimate of how much of a beam's light a hit
// surface's REAL material sends back, from its own colour/roughness/metalness
// (whatever preset stage-props.js's makeMaterial built it with) - not a fixed
// 0.5 grey guess. `color` tints the bounce (a white satin wall bounces the
// beam's own colour; a red wall reddens it too); `albedo` scales it (near 0
// for very dark/rough surfaces like black-acoustic-foam, boosted for glossy/
// metal/mirror hits, which reflect specularly rather than absorbing).
const _white = new THREE.Color(0xffffff);
export function estimateSurfaceBounce(material) {
  if (!material || material.color === undefined) return { color: _white, albedo: 0.5 };
  const c = material.color;
  const lum = (c.r + c.g + c.b) / 3;
  const rough = material.roughness != null ? material.roughness : 0.8;
  const metal = material.metalness != null ? material.metalness : 0;
  let scale;
  if (metal > 0.6 && rough < 0.35) scale = 1.3; // polished metal/mirror: strong tinted reflection
  else if (rough < 0.3) scale = 1.05; // glossy tile / gloss lacquer: bright, tight highlight
  else scale = 0.55 + 0.35 * (1 - rough); // matte/satin/foam/concrete/brick/fabric/wood: diffuse
  return { color: c, albedo: THREE.MathUtils.clamp(lum * scale, 0, 1.6) };
}

const _bounceTint = new THREE.Color();
/** `surface` = {color: THREE.Color, albedo: number} from estimateSurfaceBounce
 * (or a hit material passed directly - either works, see stage-scene.js). */
export function fitBounceLight(light, worldHitPoint, color, dimmer, surface, bounceIntensity) {
  light.position.copy(worldHitPoint).setY(worldHitPoint.y + 0.05);
  const surfaceColor = (surface && surface.color) || _white;
  const albedo = surface && surface.albedo != null ? surface.albedo : 0.5;
  _bounceTint.set(color || "#ffffff").multiply(surfaceColor);
  light.color.copy(_bounceTint);
  const strength = Math.max(0, dimmer || 0) * Math.max(0, albedo) * Math.max(0, bounceIntensity || 0);
  light.intensity = strength * 6;
  light.distance = 2.5 + strength * 3;
  if (!(strength > 0.003)) light.intensity = 0;
}

/** Returns up to `n` entries of `list` with the highest `.brightness`
 * (a small budgeting helper for "shadows only for the top-N brightest" /
 * "SpotLight per visible beam up to a budget"). Non-mutating. */
export function pickTopByBrightness(list, n) {
  if (!list || list.length <= n) return list ? list.slice() : [];
  return list
    .slice()
    .sort((a, b) => b.brightness - a.brightness)
    .slice(0, n);
}

export const BEAM_CONST = { FT, IN };
