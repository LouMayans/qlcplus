/*
  Free-fly + middle-drag mouselook camera controls layered on top of an
  existing OrbitControls instance (which keeps handling left-drag pan).
  All physical speeds are specified in feet/second by the caller's
  settings and converted to the three.js world unit, which is metres.
*/

import * as THREE from "three";

const FT = 0.3048; // 1 ft in metres (world unit)
const ACCEL_TIME = 0.15; // seconds to reach full speed when smooth=true
const LOOK_KEYS_DEG_PER_S = 60; // arrow-key look speed (Shift: twice as fast)
const GESTURE_IDLE_MS = 350; // gap after which a movement/wheel burst is considered done
const LOOK_K = 0.0025; // rad per pixel, before lookSensitivity multiplier

const DEFAULTS = {
  moveSpeed: 8,
  fastMult: 3,
  verticalSpeed: 6,
  lookSensitivity: 1,
  panSpeed: 1,
  smooth: false,
  keepLevel: false,
  orbitMiddle: false,
  wheelMode: "zoom",
};

function mergeSettings(s) {
  s = s || {};
  return {
    moveSpeed: num(s.moveSpeed, DEFAULTS.moveSpeed),
    fastMult: num(s.fastMult, DEFAULTS.fastMult),
    verticalSpeed: num(s.verticalSpeed, DEFAULTS.verticalSpeed),
    lookSensitivity: num(s.lookSensitivity, DEFAULTS.lookSensitivity),
    panSpeed: num(s.panSpeed, DEFAULTS.panSpeed),
    smooth: !!(s.smooth ?? DEFAULTS.smooth),
    keepLevel: !!(s.keepLevel ?? DEFAULTS.keepLevel),
    orbitMiddle: !!(s.orbitMiddle ?? DEFAULTS.orbitMiddle),
    wheelMode: s.wheelMode || DEFAULTS.wheelMode,
  };
}

function num(v, d) {
  v = Number(v);
  return Number.isFinite(v) ? v : d;
}

export function initCameraControls({ camera, orbit, domElement, getSettings, isBlocked, onHistory, toast }) {
  let settings = mergeSettings(getSettings ? getSettings() : null);

  // ---- keyboard free-fly state ----
  const keys = { w: false, s: false, a: false, d: false, q: false, e: false, shift: false };
  let curVel = new THREE.Vector3(); // current smoothed velocity (world units/s)
  let anyKeyDown = false;
  let moveGestureBefore = null;
  let moveGestureTimer = null;

  // ---- middle-drag mouselook / orbit state ----
  let dragging = false;
  let dragButton = -1;
  let lastX = 0;
  let lastY = 0;
  let lookYaw = 0;
  let lookPitch = 0;
  let lookDist = 1;
  let orbitRadius = 1;
  let orbitTheta = 0; // azimuth
  let orbitPhi = Math.PI / 2; // polar, 0 = up
  let dragBefore = null;

  // ---- wheel speed override ----
  let speedOverride = 1; // multiplier applied to settings.moveSpeed for "speed" wheel mode
  let wheelGestureBefore = null;
  let wheelGestureTimer = null;

  function snapshot() {
    return {
      position: [camera.position.x, camera.position.y, camera.position.z],
      target: [orbit.target.x, orbit.target.y, orbit.target.z],
    };
  }

  function fireHistory(before) {
    if (!before || typeof onHistory !== "function") return;
    const after = snapshot();
    onHistory(before, after);
  }

  // ---------------- keyboard ----------------

  function keyFromEvent(e) {
    switch (e.code) {
      case "KeyW": return "w";
      case "KeyS": return "s";
      case "KeyA": return "a";
      case "KeyD": return "d";
      case "KeyQ": return "q";
      case "KeyE": return "e";
      // arrows look around, like middle-drag (for a trackpad or a mouse without a middle button)
      case "ArrowLeft": return "left";
      case "ArrowRight": return "right";
      case "ArrowUp": return "up";
      case "ArrowDown": return "down";
      case "ShiftLeft":
      case "ShiftRight": return "shift";
      default: return null;
    }
  }

  function onKeyDown(e) {
    keys.shift = e.shiftKey;
    if (e.ctrlKey || e.metaKey) return;
    const k = keyFromEvent(e);
    if (!k) return;
    if (isBlocked && isBlocked()) return;
    if (k !== "shift") {
      if (!anyKeyDown) moveGestureBefore = snapshot();
      anyKeyDown = true;
      if (moveGestureTimer) {
        clearTimeout(moveGestureTimer);
        moveGestureTimer = null;
      }
    }
    if (!keys[k]) e.preventDefault();
    keys[k] = true;
  }

  function onKeyUp(e) {
    keys.shift = e.shiftKey;
    const k = keyFromEvent(e);
    if (!k) return;
    keys[k] = false;
    scheduleMoveGestureEnd();
  }

  function scheduleMoveGestureEnd() {
    if (keys.w || keys.s || keys.a || keys.d || keys.q || keys.e || keys.left || keys.right || keys.up || keys.down) return;
    if (!anyKeyDown) return;
    if (moveGestureTimer) clearTimeout(moveGestureTimer);
    moveGestureTimer = setTimeout(() => {
      moveGestureTimer = null;
      anyKeyDown = false;
      fireHistory(moveGestureBefore);
      moveGestureBefore = null;
    }, GESTURE_IDLE_MS);
  }

  function releaseAllKeys() {
    keys.w = keys.s = keys.a = keys.d = keys.q = keys.e = keys.shift = false;
    keys.left = keys.right = keys.up = keys.down = false;
  }

  function onBlur() {
    releaseAllKeys();
  }

  // ---------------- middle-drag mouselook / orbit ----------------

  function onMouseDown(e) {
    if (e.button === 1) {
      e.preventDefault(); // suppress autoscroll
    }
  }

  function onAuxClick(e) {
    if (e.button === 1) e.preventDefault();
  }

  function onPointerDown(e) {
    if (e.button !== 1) return;
    if (isBlocked && isBlocked()) return;
    e.preventDefault();
    dragging = true;
    dragButton = e.pointerId;
    lastX = e.clientX;
    lastY = e.clientY;
    dragBefore = snapshot();
    try {
      domElement.setPointerCapture(e.pointerId);
    } catch (err) {
      /* ignore */
    }

    const toTarget = new THREE.Vector3().subVectors(orbit.target, camera.position);
    lookDist = Math.max(toTarget.length(), 1);

    // Extract yaw/pitch from current camera orientation (YXZ, no roll).
    const euler = new THREE.Euler().setFromQuaternion(camera.quaternion, "YXZ");
    lookYaw = euler.y;
    lookPitch = euler.x;

    if (settings.orbitMiddle) {
      const rel = new THREE.Vector3().subVectors(camera.position, orbit.target);
      orbitRadius = Math.max(rel.length(), 0.01);
      orbitPhi = Math.acos(THREE.MathUtils.clamp(rel.y / orbitRadius, -1, 1));
      orbitTheta = Math.atan2(rel.x, rel.z);
    }
  }

  function onPointerMove(e) {
    if (!dragging || e.pointerId !== dragButton) return;
    if (isBlocked && isBlocked()) return;
    const dx = e.clientX - lastX;
    const dy = e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;
    const k = LOOK_K * settings.lookSensitivity;

    if (settings.orbitMiddle) {
      orbitTheta -= dx * k;
      orbitPhi = THREE.MathUtils.clamp(
        orbitPhi - dy * k,
        THREE.MathUtils.degToRad(1),
        THREE.MathUtils.degToRad(179)
      );
      const sinPhi = Math.sin(orbitPhi);
      const pos = new THREE.Vector3(
        orbit.target.x + orbitRadius * sinPhi * Math.sin(orbitTheta),
        orbit.target.y + orbitRadius * Math.cos(orbitPhi),
        orbit.target.z + orbitRadius * sinPhi * Math.cos(orbitTheta)
      );
      camera.position.copy(pos);
      camera.lookAt(orbit.target);
    } else {
      lookYaw -= dx * k;
      const maxPitch = THREE.MathUtils.degToRad(89);
      lookPitch = THREE.MathUtils.clamp(lookPitch - dy * k, -maxPitch, maxPitch);
      const euler = new THREE.Euler(lookPitch, lookYaw, 0, "YXZ");
      camera.quaternion.setFromEuler(euler);

      const dir = new THREE.Vector3();
      camera.getWorldDirection(dir);
      orbit.target.copy(camera.position).addScaledVector(dir, lookDist);
    }
    orbit.update();
  }

  function endDrag(e) {
    if (!dragging || (e && e.pointerId !== dragButton)) return;
    dragging = false;
    try {
      domElement.releasePointerCapture(dragButton);
    } catch (err) {
      /* ignore */
    }
    dragButton = -1;
    fireHistory(dragBefore);
    dragBefore = null;
  }

  function onPointerUp(e) {
    if (e.button !== 1) return;
    endDrag(e);
  }

  function onPointerCancel(e) {
    endDrag(e);
  }

  // ---------------- wheel ----------------

  function scheduleWheelGestureEnd() {
    if (wheelGestureTimer) clearTimeout(wheelGestureTimer);
    wheelGestureTimer = setTimeout(() => {
      wheelGestureTimer = null;
      fireHistory(wheelGestureBefore);
      wheelGestureBefore = null;
    }, GESTURE_IDLE_MS);
  }

  function onWheel(e) {
    if (settings.wheelMode === "off") return;
    if (isBlocked && isBlocked()) return;
    e.preventDefault();

    if (!wheelGestureBefore) wheelGestureBefore = snapshot();
    if (wheelGestureTimer) {
      clearTimeout(wheelGestureTimer);
      wheelGestureTimer = null;
    }

    if (settings.wheelMode === "zoom") {
      const toTarget = new THREE.Vector3().subVectors(orbit.target, camera.position);
      const dist = Math.max(toTarget.length(), 0.5);
      const factor = e.deltaY > 0 ? 1.1 : 0.9;
      const newDist = Math.max(dist * factor, 0.5);
      const dir = toTarget.normalize();
      camera.position.copy(orbit.target).addScaledVector(dir, -newDist);
      orbit.update();
    } else if (settings.wheelMode === "speed") {
      speedOverride *= e.deltaY > 0 ? 1 / 1.15 : 1.15;
      const applied = THREE.MathUtils.clamp(settings.moveSpeed * speedOverride, 1, 60);
      speedOverride = applied / settings.moveSpeed;
      if (typeof toast === "function") toast(`Move speed ${applied.toFixed(0)} ft/s`);
    }

    scheduleWheelGestureEnd();
  }

  domElement.addEventListener("mousedown", onMouseDown);
  domElement.addEventListener("auxclick", onAuxClick);
  domElement.addEventListener("pointerdown", onPointerDown);
  domElement.addEventListener("pointermove", onPointerMove);
  domElement.addEventListener("pointerup", onPointerUp);
  domElement.addEventListener("pointercancel", onPointerCancel);
  domElement.addEventListener("wheel", onWheel, { passive: false });
  // capture phase: the camera sees keys before the editor's handlers can consume them
  window.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("keyup", onKeyUp, true);
  window.addEventListener("blur", onBlur);

  // ---------------- per-frame update ----------------

  function update(dtSeconds) {
    settings = mergeSettings(getSettings ? getSettings() : settings);

    if (isBlocked && isBlocked()) {
      // Decay any residual velocity so movement doesn't resume unexpectedly.
      curVel.set(0, 0, 0);
      return;
    }

    const turning = keys.left || keys.right || keys.up || keys.down;
    if (turning) lookWithKeys(dtSeconds);
    const anyDown = keys.w || keys.s || keys.a || keys.d || keys.q || keys.e;
    let wish = new THREE.Vector3();

    if (anyDown) {
      const fwd = new THREE.Vector3();
      camera.getWorldDirection(fwd);
      const right = new THREE.Vector3().crossVectors(fwd, camera.up).normalize();

      if (settings.keepLevel) {
        fwd.y = 0;
        if (fwd.lengthSq() > 1e-8) fwd.normalize();
        right.y = 0;
        if (right.lengthSq() > 1e-8) right.normalize();
      }

      if (keys.w) wish.add(fwd);
      if (keys.s) wish.sub(fwd);
      if (keys.d) wish.add(right);
      if (keys.a) wish.sub(right);
      if (wish.lengthSq() > 1e-8) wish.normalize();

      const mult = keys.shift ? settings.fastMult : 1;
      const speed = settings.moveSpeed * speedOverride * mult * FT;
      wish.multiplyScalar(speed);

      if (keys.q) wish.y -= settings.verticalSpeed * FT;
      if (keys.e) wish.y += settings.verticalSpeed * FT;
    } else if (!turning) {
      scheduleMoveGestureEnd();
    }

    if (settings.smooth) {
      const t = ACCEL_TIME > 0 ? Math.min(dtSeconds / ACCEL_TIME, 1) : 1;
      curVel.lerp(wish, t);
    } else {
      curVel.copy(wish);
    }

    if (curVel.lengthSq() > 1e-10) {
      const offset = curVel.clone().multiplyScalar(dtSeconds);
      camera.position.add(offset);
      orbit.target.add(offset);
      orbit.update();
    }
  }

  // Arrow keys: the same look (or orbit, with the orbit-middle setting) as middle-drag, at a steady turn rate.
  function lookWithKeys(dtSeconds) {
    const rate = THREE.MathUtils.degToRad(LOOK_KEYS_DEG_PER_S) * (keys.shift ? 2 : 1) * dtSeconds;
    const yaw = (keys.left ? 1 : 0) - (keys.right ? 1 : 0); // left turns left, like dragging left
    const pitch = (keys.up ? 1 : 0) - (keys.down ? 1 : 0); // up looks up, like dragging up
    if (settings.orbitMiddle) {
      const rel = new THREE.Vector3().subVectors(camera.position, orbit.target);
      const r = Math.max(rel.length(), 0.01);
      let phi = Math.acos(THREE.MathUtils.clamp(rel.y / r, -1, 1));
      let theta = Math.atan2(rel.x, rel.z);
      theta += yaw * rate;
      phi = THREE.MathUtils.clamp(phi + pitch * rate, THREE.MathUtils.degToRad(1), THREE.MathUtils.degToRad(179));
      const sinPhi = Math.sin(phi);
      camera.position.set(
        orbit.target.x + r * sinPhi * Math.sin(theta),
        orbit.target.y + r * Math.cos(phi),
        orbit.target.z + r * sinPhi * Math.cos(theta)
      );
      camera.lookAt(orbit.target);
    } else {
      const dist = Math.max(new THREE.Vector3().subVectors(orbit.target, camera.position).length(), 1);
      const euler = new THREE.Euler().setFromQuaternion(camera.quaternion, "YXZ");
      const maxPitch = THREE.MathUtils.degToRad(89);
      euler.set(THREE.MathUtils.clamp(euler.x + pitch * rate, -maxPitch, maxPitch), euler.y + yaw * rate, 0, "YXZ");
      camera.quaternion.setFromEuler(euler);
      const dir = new THREE.Vector3();
      camera.getWorldDirection(dir);
      orbit.target.copy(camera.position).addScaledVector(dir, dist);
    }
    orbit.update();
  }

  function setSettings(s) {
    settings = mergeSettings(s);
  }

  function getSpeedOverride() {
    return speedOverride;
  }

  function dispose() {
    domElement.removeEventListener("mousedown", onMouseDown);
    domElement.removeEventListener("auxclick", onAuxClick);
    domElement.removeEventListener("pointerdown", onPointerDown);
    domElement.removeEventListener("pointermove", onPointerMove);
    domElement.removeEventListener("pointerup", onPointerUp);
    domElement.removeEventListener("pointercancel", onPointerCancel);
    domElement.removeEventListener("wheel", onWheel);
    window.removeEventListener("keydown", onKeyDown, true);
    window.removeEventListener("keyup", onKeyUp, true);
    window.removeEventListener("blur", onBlur);
    if (moveGestureTimer) clearTimeout(moveGestureTimer);
    if (wheelGestureTimer) clearTimeout(wheelGestureTimer);
  }

  // true while a movement key is held: the editor then leaves Shift to the camera (fast)
  function isFlyActive() {
    return !!(keys.w || keys.s || keys.a || keys.d || keys.q || keys.e || keys.left || keys.right || keys.up || keys.down);
  }

  // true while the middle-drag mouselook gesture is active (game-UI polish:
  // the crosshair only makes sense while actually looking around).
  function isMouselooking() {
    return dragging;
  }

  return { update, setSettings, dispose, getSpeedOverride, isFlyActive, isMouselooking };
}
