/*
  stage-render.js

  The post-processing / quality pipeline for the 3D stage visualizer (R
  group - see .claude/memory/stage-visualizer.md "Overnight build
  contracts"). Owns the EffectComposer chain, the depth pre-pass used by
  stage-beams.js's soft-intersection, dynamic resolution, and the render
  settings schema/presets that stage-scene.js's setRenderSettings/
  getRenderSettings/getRenderStats surface to the UI (stage-editor.js/
  stage-app.js build a generic settings panel from RENDER_SETTINGS_SCHEMA).

  Pipeline: RenderPass -> [SSAOPass] -> UnrealBloomPass -> [SMAAPass|FXAA] ->
  OutputPass (ACES tone mapping + exposure, via renderer.toneMapping*).

  Depth pre-pass (used by stage-beams.js for soft volumetric intersection):
  before the main composer render, the scene is rendered depth-only (colour
  writes disabled - cheap) into a small WebGLRenderTarget carrying a real
  THREE.DepthTexture, with the camera's LAYER_BEAM bit temporarily disabled
  so beams themselves never occlude each other or write into it. This is a
  genuinely separate render target from whatever the composer is currently
  writing to, so beam fragments can safely sample it later in the same
  frame without a WebGL framebuffer feedback loop (see stage-beams.js's
  header for why that matters).
*/

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { SSAOPass } from "three/addons/postprocessing/SSAOPass.js";
import { SMAAPass } from "three/addons/postprocessing/SMAAPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { FXAAShader } from "three/addons/shaders/FXAAShader.js";
import { LAYER_BEAM } from "./stage-beams.js";

// ------------------------------------------------------------- schema ----

export const RENDER_SETTINGS_SCHEMA = [
  { key: "quality", label: "Quality preset", group: "Quality", type: "select", options: ["low", "medium", "high", "ultra"] },
  { key: "resolutionScale", label: "Resolution scale", group: "Quality", type: "range", min: 0.5, max: 1.5, step: 0.05 },
  { key: "dynamicResolution", label: "Dynamic resolution (hold target FPS)", group: "Quality", type: "bool" },
  { key: "targetFps", label: "Target FPS", group: "Quality", type: "select", options: [30, 60] },
  { key: "exposure", label: "Exposure", group: "Quality", type: "range", min: 0.2, max: 2.5, step: 0.05 },
  { key: "workLight", label: "Work light (house ambient)", group: "Quality", type: "range", min: 0, max: 1, step: 0.05 },

  { key: "bloom.enabled", label: "Bloom", group: "Bloom", type: "bool" },
  { key: "bloom.strength", label: "Bloom strength", group: "Bloom", type: "range", min: 0, max: 2, step: 0.05 },
  { key: "bloom.radius", label: "Bloom radius", group: "Bloom", type: "range", min: 0, max: 1.5, step: 0.05 },
  { key: "bloom.threshold", label: "Bloom threshold", group: "Bloom", type: "range", min: 0, max: 1, step: 0.02 },

  { key: "ao.enabled", label: "Ambient occlusion", group: "Ambient Occlusion", type: "bool" },
  { key: "ao.intensity", label: "AO intensity", group: "Ambient Occlusion", type: "range", min: 0, max: 1.5, step: 0.05 },

  { key: "antialias", label: "Antialiasing", group: "Antialiasing", type: "select", options: ["none", "fxaa", "smaa"] },

  { key: "shadows.enabled", label: "Shadows", group: "Shadows", type: "bool" },
  { key: "shadows.maxShadowLights", label: "Max shadow-casting lights", group: "Shadows", type: "range", min: 0, max: 8, step: 1 },
  { key: "shadows.mapSize", label: "Shadow map size", group: "Shadows", type: "select", options: [512, 1024, 2048] },

  { key: "spot.maxLights", label: "Max spotlights", group: "Spotlights", type: "range", min: 0, max: 32, step: 1 },

  { key: "beams.volumetric", label: "Volumetric beams", group: "Beams", type: "bool" },
  { key: "beams.quality", label: "Beam quality", group: "Beams", type: "range", min: 0, max: 3, step: 1 },
  { key: "beams.softness", label: "Beam edge softness", group: "Beams", type: "range", min: 0, max: 1, step: 0.05 },
  { key: "beams.haze", label: "Haze density", group: "Beams", type: "range", min: 0, max: 1, step: 0.05 },
  { key: "beams.noise", label: "Haze noise", group: "Beams", type: "range", min: 0, max: 1, step: 0.05 },
  { key: "beams.maxLengthFt", label: "Max beam length (ft)", group: "Beams", type: "range", min: 10, max: 100, step: 5 },

  { key: "bounce.count", label: "Bounce lights", group: "Bounce Light", type: "range", min: 0, max: 32, step: 1 },
  { key: "bounce.intensity", label: "Bounce intensity", group: "Bounce Light", type: "range", min: 0, max: 2, step: 0.05 },

  { key: "reflections.enabled", label: "Floor reflections", group: "Reflections", type: "bool" },
  { key: "reflections.resolution", label: "Reflection resolution", group: "Reflections", type: "select", options: [128, 256, 384, 512, 768, 1024] },
  // A small always-on-or-off CubeCamera (goal 4, materials pass) used as envMap for
  // mirror/metal/glossy-tile/gloss materials only - separate from the planar floor
  // Reflector above. 0 = off. Refreshed at <=4Hz in stage-scene.js, never per frame.
  { key: "reflections.cube", label: "Dynamic reflections (mirrors/metal)", group: "Reflections", type: "select", options: [0, 128, 256] },
];

function preset(p) {
  return JSON.parse(JSON.stringify(p));
}

export const QUALITY_PRESETS = {
  low: {
    quality: "low",
    resolutionScale: 0.6,
    dynamicResolution: true,
    targetFps: 30,
    exposure: 1.0,
    workLight: 0.42,
    bloom: { enabled: false, strength: 0.5, radius: 0.35, threshold: 0.85 },
    ao: { enabled: false, intensity: 0.5 },
    antialias: "none",
    shadows: { enabled: true, maxShadowLights: 2, mapSize: 512 },
    spot: { maxLights: 8 },
    beams: { volumetric: false, quality: 0, softness: 0.5, haze: 0.3, noise: 0, maxLengthFt: 40 },
    bounce: { count: 0, intensity: 0 },
    reflections: { enabled: false, resolution: 256, cube: 0 },
  },
  medium: {
    quality: "medium",
    resolutionScale: 1.0,
    dynamicResolution: true,
    targetFps: 60,
    exposure: 1.05,
    workLight: 0.36,
    bloom: { enabled: true, strength: 0.5, radius: 0.5, threshold: 0.9 },
    ao: { enabled: false, intensity: 0.6 },
    antialias: "fxaa",
    shadows: { enabled: true, maxShadowLights: 2, mapSize: 1024 },
    spot: { maxLights: 8 },
    beams: { volumetric: true, quality: 1, softness: 0.6, haze: 0.35, noise: 0.12, maxLengthFt: 40 },
    bounce: { count: 6, intensity: 0.5 },
    // Confirmed live on the operator's Intel UHD laptop (live_drive.py): turning
    // the cube camera on applies envMap to every reflective material in the
    // club scene at once (floor, bar top, mirror, brass trim, DJ gloss panel,
    // ...) - a single big warm-up batch that measured as a 1s+ freeze and
    // dropped Medium well under the 30fps gate. Kept off by default on Medium
    // (same as Low); the operator can still opt in via Settings > Graphics.
    reflections: { enabled: false, resolution: 384, cube: 0 },
  },
  high: {
    quality: "high",
    resolutionScale: 1.0,
    dynamicResolution: true,
    targetFps: 60,
    exposure: 1.1,
    workLight: 0.32,
    bloom: { enabled: true, strength: 0.8, radius: 0.6, threshold: 0.75 },
    ao: { enabled: true, intensity: 0.7 },
    antialias: "smaa",
    shadows: { enabled: true, maxShadowLights: 4, mapSize: 2048 },
    spot: { maxLights: 16 },
    beams: { volumetric: true, quality: 2, softness: 0.65, haze: 0.4, noise: 0.18, maxLengthFt: 50 },
    bounce: { count: 16, intensity: 0.7 },
    reflections: { enabled: true, resolution: 512, cube: 256 },
  },
  ultra: {
    quality: "ultra",
    resolutionScale: 1.25,
    dynamicResolution: false,
    targetFps: 60,
    exposure: 1.15,
    workLight: 0.28,
    bloom: { enabled: true, strength: 0.9, radius: 0.7, threshold: 0.7 },
    ao: { enabled: true, intensity: 0.85 },
    antialias: "smaa",
    shadows: { enabled: true, maxShadowLights: 8, mapSize: 2048 },
    spot: { maxLights: 32 },
    beams: { volumetric: true, quality: 3, softness: 0.7, haze: 0.45, noise: 0.25, maxLengthFt: 60 },
    bounce: { count: 32, intensity: 0.9 },
    reflections: { enabled: true, resolution: 1024, cube: 256 },
  },
};

export const DEFAULT_RENDER_SETTINGS = preset(QUALITY_PRESETS.low);

function deepMerge(base, patch) {
  const out = Object.assign({}, base);
  if (!patch) return out;
  for (const k of Object.keys(patch)) {
    const v = patch[k];
    if (v && typeof v === "object" && !Array.isArray(v) && base[k] && typeof base[k] === "object") {
      out[k] = deepMerge(base[k], v);
    } else {
      out[k] = v;
    }
  }
  return out;
}

// --------------------------------------------------------------- pipeline --

/**
 * @param {THREE.WebGLRenderer} renderer
 * @param {THREE.Scene} scene
 * @param {THREE.PerspectiveCamera} camera
 */
export function createRenderPipeline(renderer, scene, camera) {
  let settings = preset(DEFAULT_RENDER_SETTINGS);
  let mode = "lit"; // "lit" | "beams" | "unlit" | "wireframe"
  let width = 1;
  let height = 1;
  let dynScale = 1; // internal dynamic-resolution multiplier, 0.5..1

  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = settings.exposure;
  renderer.shadowMap.enabled = settings.shadows.enabled;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  // EffectComposer makes several internal renderer.render() calls per frame
  // (RenderPass, SSAOPass's own normal/depth passes, bloom's blur/composite
  // blits, SMAA's 3 passes, the final OutputPass blit) - each one resets
  // renderer.info.render by default, so reading it after composer.render()
  // would only ever show the LAST internal blit (typically "1 draw call, a
  // handful of triangles"), not the frame's real total. Managing the reset
  // ourselves (once per OUR frame, not per internal pass) makes
  // getStats().drawCalls/triangles the true per-frame totals.
  renderer.info.autoReset = false;

  // camera always "sees" the beam layer during normal rendering; the depth
  // pre-pass toggles it off for one render call each frame.
  camera.layers.enable(LAYER_BEAM);

  // ---- depth pre-pass (solid geometry only, colour writes disabled) -----
  const depthColorOverride = new THREE.MeshBasicMaterial({ colorWrite: false });
  let depthTarget = null;
  function buildDepthTarget(w, h) {
    if (depthTarget) depthTarget.dispose();
    // Same DepthStencilFormat/UnsignedInt248Type combo three's own SSAOPass
    // uses for its depth render target - the broadly-supported way to get a
    // sampleable depth texture attachment.
    const dt = new THREE.DepthTexture(w, h);
    dt.format = THREE.DepthStencilFormat;
    dt.type = THREE.UnsignedInt248Type;
    depthTarget = new THREE.WebGLRenderTarget(w, h, { depthTexture: dt, depthBuffer: true });
    return depthTarget;
  }

  function renderDepthPrepass() {
    const prevOverride = scene.overrideMaterial;
    const prevBg = scene.background;
    scene.overrideMaterial = depthColorOverride;
    scene.background = null;
    camera.layers.disable(LAYER_BEAM); // solid geometry only, never the beams themselves
    const prevTarget = renderer.getRenderTarget();
    renderer.setRenderTarget(depthTarget);
    renderer.clear(true, true, false);
    renderer.render(scene, camera);
    renderer.setRenderTarget(prevTarget);
    camera.layers.enable(LAYER_BEAM);
    scene.overrideMaterial = prevOverride;
    scene.background = prevBg;
  }

  // ------------------------------------------------------------ composer --
  let composer = null;
  let renderPass = null;
  let ssaoPass = null;
  let bloomPass = null;
  let aaPass = null;
  let outputPass = null;

  // EffectComposer.dispose() frees only its own two buffers; each pass (bloom mips, SMAA
  // textures, SSAO targets) must be disposed too, or every rebuild leaked GPU memory.
  function disposeComposer() {
    if (!composer) return;
    composer.passes.forEach((p) => p.dispose && p.dispose());
    composer.dispose();
    composer = null;
  }
  let composerKey = "";
  function buildComposer() {
    disposeComposer();
    composer = new EffectComposer(renderer);
    renderPass = new RenderPass(scene, camera);
    composer.addPass(renderPass);

    if (settings.ao.enabled && mode === "lit") {
      ssaoPass = new SSAOPass(scene, camera, width, height);
      ssaoPass.kernelRadius = 0.25;
      ssaoPass.minDistance = 0.0005;
      ssaoPass.maxDistance = 0.15;
      ssaoPass.output = SSAOPass.OUTPUT ? SSAOPass.OUTPUT.Default : 0;
      composer.addPass(ssaoPass);
    } else {
      ssaoPass = null;
    }

    if (settings.bloom.enabled) {
      bloomPass = new UnrealBloomPass(new THREE.Vector2(width, height), settings.bloom.strength, settings.bloom.radius, settings.bloom.threshold);
      composer.addPass(bloomPass);
    } else {
      bloomPass = null;
    }

    if (settings.antialias === "smaa") {
      aaPass = new SMAAPass(width * renderer.getPixelRatio(), height * renderer.getPixelRatio());
      composer.addPass(aaPass);
    } else if (settings.antialias === "fxaa") {
      aaPass = new ShaderPass(FXAAShader);
      aaPass.material.uniforms["resolution"].value.set(1 / (width * renderer.getPixelRatio()), 1 / (height * renderer.getPixelRatio()));
      composer.addPass(aaPass);
    } else {
      aaPass = null;
    }

    outputPass = new OutputPass();
    composer.addPass(outputPass);
  }

  function applyAoIntensity() {
    if (ssaoPass) {
      ssaoPass.kernelRadius = 0.15 + settings.ao.intensity * 0.2;
    }
  }
  function applyBloomParams() {
    if (bloomPass) {
      bloomPass.strength = settings.bloom.strength;
      bloomPass.radius = settings.bloom.radius;
      bloomPass.threshold = settings.bloom.threshold;
    }
  }

  function effectiveResolutionScale() {
    return Math.max(0.35, Math.min(1.5, settings.resolutionScale * dynScale));
  }

  function setSize(w, h) {
    width = Math.max(1, w);
    height = Math.max(1, h);
    renderer.setPixelRatio(settings.quality === "low" ? 1 : Math.min(window.devicePixelRatio || 1, 2));
    const scale = effectiveResolutionScale();
    const rw = Math.max(1, Math.round(width * scale));
    const rh = Math.max(1, Math.round(height * scale));
    renderer.setSize(rw, rh, false);
    renderer.domElement.style.width = width + "px";
    renderer.domElement.style.height = height + "px";
    if (composer) composer.setSize(rw, rh);
    if (bloomPass) bloomPass.setSize(rw, rh);
    if (aaPass && aaPass.setSize) aaPass.setSize(rw * renderer.getPixelRatio(), rh * renderer.getPixelRatio());
    if (aaPass && aaPass.material && aaPass.material.uniforms && aaPass.material.uniforms["resolution"]) {
      aaPass.material.uniforms["resolution"].value.set(1 / (rw * renderer.getPixelRatio()), 1 / (rh * renderer.getPixelRatio()));
    }
    // depth pre-pass runs at a lower internal resolution at low beam quality
    // (its only job is a soft fade, not pixel-perfect edges).
    const depthScale = settings.beams.quality <= 0 ? 0.4 : settings.beams.quality === 1 ? 0.55 : 0.75;
    buildDepthTarget(Math.max(1, Math.round(rw * depthScale)), Math.max(1, Math.round(rh * depthScale)));
  }

  buildComposer();

  // ---------------------------------------------------------- settings ----
  function applySettings() {
    renderer.toneMappingExposure = settings.exposure;
    renderer.shadowMap.enabled = settings.shadows.enabled && mode !== "unlit" && mode !== "wireframe";
    // rebuild the post chain only when its passes change (not for e.g. a spotlight budget)
    const key = JSON.stringify([settings.ao.enabled, settings.bloom.enabled, settings.antialias, mode]);
    if (!composer || key !== composerKey) {
      composerKey = key;
      buildComposer();
    }
    applyAoIntensity();
    applyBloomParams();
    setSize(width, height);
  }

  function setSettings(patch) {
    settings = deepMerge(settings, patch);
    applySettings();
  }
  function getSettings() {
    return preset(settings);
  }
  function setQualityPreset(name) {
    if (QUALITY_PRESETS[name]) setSettings(QUALITY_PRESETS[name]);
  }

  function setMode(m) {
    mode = m;
    applySettings();
  }
  function getMode() {
    return mode;
  }

  // ----------------------------------------------------- dynamic res/stats --
  let frameTimes = [];
  let lastAdjust = performance.now();
  let statsFps = 60;
  let statsFrameMs = 16.7;

  // Bug (2026-09-29, live-GPU perf pass): this used to react to the CPU-side
  // `ms` measured around composer.render() inside render() below - but WebGL
  // command submission is asynchronous, so that number stays near the JS-only
  // cost (a few ms) even while the browser's real frame delivery is throttled
  // to a much slower GPU-bound rate (confirmed live: a steady 51ms/frame
  // Medium preset measured internal `ms` well under 10ms) - dynamic
  // resolution never saw the real cost and never engaged. It now reacts to
  // the caller's real rAF-to-rAF interval instead (stage-scene.js loop(),
  // passed as render()'s 2nd argument), which the browser genuinely throttles
  // to match actual GPU throughput.
  //
  // That real interval is vsync-quantized and can never read below ~1000/refreshRate
  // (e.g. 16.7ms at 60Hz) no matter how much GPU headroom exists - so once vsync-capped
  // there's no timing signal left to prove "we could afford more resolution now". The
  // scheme below: drop hard and immediately the moment we're over budget (and remember
  // that failure as a ceiling), but only ever creep back up in small steps after a long
  // unbroken run of good frames plus a cooldown - so a bad probe corrects itself in well
  // under a second instead of oscillating, and a resize (the only visible side effect)
  // never happens more than a couple of times per adjustment window.
  let scaleCeiling = 1; // highest scale considered safe recently; relaxes slowly, drops immediately
  let lastDrop = -Infinity;
  let goodStreak = 0;

  function updateDynamicResolution(frameIntervalMs) {
    frameTimes.push(frameIntervalMs);
    if (frameTimes.length > 30) frameTimes.shift();
    if (!settings.dynamicResolution) return;
    const now = performance.now();
    if (now - lastAdjust < 500 || frameTimes.length < 10) return;
    lastAdjust = now;
    const avg = frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length;
    const targetMs = 1000 / settings.targetFps;
    let changed = false;
    if (avg > targetMs * 1.15 && dynScale > 0.5) {
      // drop quickly: the real frame cadence is over budget right now
      dynScale = Math.max(0.5, dynScale - 0.15);
      scaleCeiling = dynScale;
      lastDrop = now;
      goodStreak = 0;
      changed = true;
    } else if (avg <= targetMs * 1.05) {
      goodStreak++;
      // ~3s of consecutive good 500ms windows, well clear of the last drop
      if (goodStreak >= 6 && now - lastDrop > 4000 && scaleCeiling < 1) {
        goodStreak = 0;
        scaleCeiling = Math.min(1, scaleCeiling + 0.05);
        if (dynScale < scaleCeiling) {
          dynScale = Math.min(scaleCeiling, dynScale + 0.05);
          changed = true;
        }
      }
    } else {
      goodStreak = 0; // grey zone (1.05x-1.15x target): hold steady, don't probe up
    }
    if (changed) setSize(width, height);
  }

  /** Renders one frame: depth pre-pass (if any beam wants it) then the main
   * composer chain (or a bare renderer.render for "unlit"/"wireframe", which
   * skip post entirely per the render-mode contract).
   * @param {number} dt seconds, passed through to the composer's time-based passes.
   * @param {number} [frameIntervalMs] the caller's real rAF-to-rAF interval
   *   (stage-scene.js loop()'s clamped `dt*1000`) - used to drive dynamic
   *   resolution instead of this function's own CPU-side timing, which is
   *   decoupled from real GPU cost (see updateDynamicResolution's comment).
   *   Falls back to the CPU-side measurement if the caller doesn't pass one. */
  function render(dt, frameIntervalMs) {
    const t0 = performance.now();
    renderer.info.reset(); // see the autoReset=false comment above
    const usesBeams = mode === "lit" || mode === "beams";
    if (usesBeams && settings.beams.volumetric && depthTarget) {
      renderDepthPrepass();
    }
    if (mode === "unlit" || mode === "wireframe") {
      renderer.render(scene, camera);
    } else {
      composer.render(dt);
    }
    const ms = performance.now() - t0;
    statsFrameMs = ms;
    statsFps = ms > 0 ? Math.min(1000 / ms, settings.targetFps * 2) : statsFps;
    updateDynamicResolution(frameIntervalMs != null ? frameIntervalMs : ms);
  }

  function getDepthTexture() {
    return depthTarget ? depthTarget.depthTexture : null;
  }
  function getResolution() {
    const scale = effectiveResolutionScale();
    return { x: Math.max(1, Math.round(width * scale)), y: Math.max(1, Math.round(height * scale)) };
  }

  function getStats() {
    return {
      fps: Math.round(statsFps * 10) / 10,
      frameMs: Math.round(statsFrameMs * 100) / 100,
      drawCalls: renderer.info.render.calls,
      triangles: renderer.info.render.triangles,
      resolutionScale: Math.round(effectiveResolutionScale() * 100) / 100,
    };
  }

  function dispose() {
    disposeComposer();
    if (depthTarget) depthTarget.dispose();
    depthColorOverride.dispose();
  }

  // The render target the scene is drawn into (the composer's buffer in lit/beams modes, else the
  // screen). Shader variants differ between the two (tone mapping, output colour space), so a
  // warm-up must compile with this bound to produce the programs actually used.
  function getSceneTarget() {
    return composer && mode !== "unlit" && mode !== "wireframe" ? composer.readBuffer : null;
  }
  return {
    LAYER_BEAM,
    setSize,
    render,
    getSceneTarget,
    setSettings,
    getSettings,
    setQualityPreset,
    setMode,
    getMode,
    getStats,
    getDepthTexture,
    getResolution,
    dispose,
  };
}
