/*
  stage-rig.js

  Turns a rig fixture definition (from QLC+API|getStageRig) plus live DMX
  bytes into a renderable per-head state: pan/tilt in degrees, dimmer alpha,
  RGB colour, shutter state and zoom angle.

  Ported (deliberately simplified for M2 - full realism lands in M3) from:
    - engine/src/qlcfixturehead.cpp:165-229 (cacheChannels: role mapping)
    - qmlui/fixtureutils.cpp:318-375 (headColor: RGB/CMY + W/A/UV/Lime/Indigo blend)
    - ui/src/monitor/monitorfixtureitem.cpp:349-383 (computeColor: colour wheel first)

  Ambiguity resolved: the spec's "dimmer = head dimmer x master" refers to a
  per-fixture master dimmer distinct from the Grand Master (DMX frames from
  the server are already post-GM per the WS protocol spec). The rig JSON has
  no distinct "master channel" concept, so `masterAlpha` here is an optional
  external multiplier (defaults to 1) that callers may wire to a future UI
  control; it is not derived from a channel search.
*/

// QLCChannel::PrimaryColour values (engine/src/qlcchannel.h)
export const COLOUR = {
  NoColour: 0,
  Red: 0xff0000,
  Green: 0x00ff00,
  Blue: 0x0000ff,
  Cyan: 0x00ffff,
  Magenta: 0xff00ff,
  Yellow: 0xffff00,
  Amber: 0xff7e00,
  White: 0xffffff,
  UV: 0x9400d3,
  Lime: 0xadff2f,
  Indigo: 0x4b0082,
};

// Fallbacks used when the .qxf physical block is 0/missing and no
// stage `models` override exists (per the M2 spec: panMax 360, tiltMax 270,
// beam 15deg - the shared spec quotes QLC+5's own 10-30deg default range,
// we use its midpoint-ish 15deg as the single fallback number M2 asks for).
export const FALLBACKS = { panMax: 360, tiltMax: 270, beam: 15 };

// Per-kind beam defaults (start width in inches, spread angle in degrees),
// used when a fixture's stage `models` override has no beamStart/beamSpread
// and the qxf physical block has no lens angle (lensMin/lensMax).
export const BEAM_KIND_DEFAULTS = {
  movingHeadSpot: { start: 4, spread: 3 }, // colour/gobo/prism wheel (e.g. Mayans BEAM230)
  movingHeadWash: { start: 7, spread: 18 }, // RGB(W) mixing
  par: { start: 6, spread: 25 },
  bar: { start: 2, spread: 30 },
  panel: { start: 8, spread: 60 },
  strobe: { start: 8, spread: 60 },
  other: { start: 4, spread: 15 },
};

// A moving head with a Gobo/Prism/Colour(wheel) channel reads as a spot/beam
// fixture (narrow, hard-edged beam); one without reads as a wash (RGB mixing).
function isSpotBeamFixture(fx) {
  return (fx.ch || []).some((ch) => ch && (ch.group === "Gobo" || ch.group === "Prism" || ch.group === "Colour"));
}

function allChannelIndices(fx) {
  const n = fx.channels || (fx.ch ? fx.ch.length : 0);
  const arr = [];
  for (let i = 0; i < n; i++) arr.push(i);
  return arr;
}

function findRolesForChannelIndices(fx, indices) {
  const roles = {
    panMSB: null,
    panLSB: null,
    tiltMSB: null,
    tiltLSB: null,
    dimmer: null,
    colourChannels: {}, // colourInt -> channel index (MSB, first found wins)
    wheelChannels: [],
    shutterChannels: [],
    zoomChannel: null,
  };
  for (const i of indices) {
    const ch = fx.ch && fx.ch[i];
    if (!ch) continue;
    if (ch.group === "Pan") {
      if (ch.byte === 0 && roles.panMSB === null) roles.panMSB = i;
      else if (ch.byte === 1 && roles.panLSB === null) roles.panLSB = i;
    } else if (ch.group === "Tilt") {
      if (ch.byte === 0 && roles.tiltMSB === null) roles.tiltMSB = i;
      else if (ch.byte === 1 && roles.tiltLSB === null) roles.tiltLSB = i;
    } else if (ch.group === "Intensity") {
      const colour = ch.colour || 0;
      if (colour === 0) {
        if (roles.dimmer === null) roles.dimmer = i;
      } else if (roles.colourChannels[colour] === undefined) {
        roles.colourChannels[colour] = i;
      }
    } else if (ch.group === "Colour" && ch.byte === 0) {
      roles.wheelChannels.push(i);
    } else if (ch.group === "Shutter" && ch.byte === 0) {
      roles.shutterChannels.push(i);
    } else if (ch.group === "Beam" && /zoom/i.test(ch.name || "")) {
      if (roles.zoomChannel === null) roles.zoomChannel = i;
    }
  }
  return roles;
}

// If a head has no pan/tilt of its own, look across the whole fixture mode
// for a shared pan/tilt pair (engine/src/qlcfixturehead.cpp:216-223).
function fillMissingPanTilt(fx, roles) {
  if (roles.panMSB !== null && roles.tiltMSB !== null && (roles.panLSB !== null || roles.tiltLSB !== null)) return;
  (fx.ch || []).forEach((ch, i) => {
    if (!ch) return;
    if (ch.group === "Pan") {
      if (ch.byte === 0 && roles.panMSB === null) roles.panMSB = i;
      if (ch.byte === 1 && roles.panLSB === null) roles.panLSB = i;
    } else if (ch.group === "Tilt") {
      if (ch.byte === 0 && roles.tiltMSB === null) roles.tiltMSB = i;
      if (ch.byte === 1 && roles.tiltLSB === null) roles.tiltLSB = i;
    }
  });
}

export function classifyKind(fx, heads) {
  const text = ((fx.type || "") + " " + (fx.model || "") + " " + (fx.name || "")).toLowerCase();
  const hasPanTilt = heads.some((h) => h.hasPanTilt);
  if (/fog|haz/.test(text)) return "fog";
  if (/laser/.test(text)) return "fx";
  if (hasPanTilt) return "movingHead";
  if (/strobe|blinder/.test(text)) return "strobe";
  if (heads.length > 1) return "bar";
  if (/panel|matrix|led\s*wall|screen/.test(text)) return "panel";
  if (/par\b/.test(text)) return "par";
  if (/derby|scanner|effect|fx\b/.test(text)) return "fx";
  return heads.length === 1 ? "par" : "other";
}

/**
 * Builds a renderable model for one rig fixture.
 * @param {object} fx a fixture entry from getStageRig's `fixtures` array
 * @param {object} [modelsOverride] the stage file's `models` map, keyed
 *        "<manufacturer>/<model>"
 */
export function buildFixtureModel(fx, modelsOverride) {
  const key = (fx.manufacturer || "?") + "/" + (fx.model || "?");
  const override = (modelsOverride && modelsOverride[key]) || null;
  const phys = fx.physical || {};

  const panMax = (override && override.panMax) || phys.panMax || FALLBACKS.panMax;
  const tiltMax = (override && override.tiltMax) || phys.tiltMax || FALLBACKS.tiltMax;

  const headDefs = Array.isArray(fx.heads) && fx.heads.length ? fx.heads : [allChannelIndices(fx)];
  // Channels outside every head are master channels for the whole fixture (QLC+ semantics): e.g. the
  // Mayans BEAM230 V1's only head is pan/tilt/speed, and its dimmer, strobe and colour wheel sit
  // outside it. Without this those heads had no dimmer/shutter/colour and showed "full on, white".
  const inHead = new Set([].concat(...headDefs));
  const master = findRolesForChannelIndices(fx, allChannelIndices(fx).filter((i) => !inHead.has(i)));
  const heads = headDefs.map((indices) => {
    const roles = findRolesForChannelIndices(fx, indices);
    fillMissingPanTilt(fx, roles);
    if (master.dimmer !== null) {
      if (roles.dimmer === null) roles.dimmer = master.dimmer;
      else roles.masterDimmer = master.dimmer; // head dimmer x master dimmer
    }
    if (!roles.shutterChannels.length) roles.shutterChannels = master.shutterChannels.slice();
    if (!roles.wheelChannels.length && !Object.keys(roles.colourChannels).length) {
      roles.wheelChannels = master.wheelChannels.slice();
      roles.colourChannels = Object.assign({}, master.colourChannels);
    }
    if (roles.zoomChannel === null) roles.zoomChannel = master.zoomChannel;
    roles.hasPanTilt =
      (roles.panMSB !== null || roles.panLSB !== null) && (roles.tiltMSB !== null || roles.tiltLSB !== null);
    return roles;
  });

  const kind = classifyKind(fx, heads);
  const kindDefaults =
    kind === "movingHead"
      ? isSpotBeamFixture(fx)
        ? BEAM_KIND_DEFAULTS.movingHeadSpot
        : BEAM_KIND_DEFAULTS.movingHeadWash
      : BEAM_KIND_DEFAULTS[kind] || BEAM_KIND_DEFAULTS.other;
  // qxf lens angle (lensMin/lensMax), when non-zero, is used as the default
  // spread ahead of the kind-based default; a stage `models` override wins
  // over both. beamDeg is kept as a legacy alias of beamSpread.
  const qxfLensAngle = phys.lensMax || phys.lensMin || 0;
  // note: `!== undefined` (not `||`) so an explicit override of 0 (e.g. a
  // "make it a cylinder" spread override) isn't mistaken for "no override".
  const beamStart = override && override.beamStart !== undefined ? override.beamStart : kindDefaults.start;
  const beamSpread = override && override.beamSpread !== undefined ? override.beamSpread : qxfLensAngle || kindDefaults.spread;

  const model = {
    id: fx.id,
    name: fx.name,
    manufacturer: fx.manufacturer,
    model: fx.model,
    modelKey: key,
    type: fx.type,
    mode: fx.mode,
    universe: fx.universe,
    address: fx.address,
    channels: fx.channels,
    physical: phys,
    panMax: panMax,
    tiltMax: tiltMax,
    beamStart: beamStart,
    beamSpread: beamSpread,
    beamDeg: beamSpread, // legacy alias, some callers still read this
    ch: fx.ch || [],
    heads: heads,
    monitorSeed: fx.monitor || null,
  };
  model.kind = kind;
  return model;
}

/** Indexes a whole rig JSON by fixture id. */
export function buildRigIndex(rigJson, modelsOverride) {
  const byId = new Map();
  const list = [];
  (rigJson.fixtures || []).forEach((fx) => {
    const model = buildFixtureModel(fx, modelsOverride);
    byId.set(fx.id, model);
    list.push(model);
  });
  return {
    serial: rigJson.serial,
    show: rigJson.show,
    universes: rigJson.universes || [],
    monitor: rigJson.monitor || null,
    byId: byId,
    list: list,
  };
}

function findCapability(ch, value) {
  if (!ch || !Array.isArray(ch.caps)) return null;
  for (const cap of ch.caps) {
    if (value >= cap.min && value <= cap.max) return cap;
  }
  return null;
}

function hexToRgbTriplet(hexStr) {
  if (typeof hexStr !== "string") return null;
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hexStr.trim());
  if (!m) return null;
  const v = parseInt(m[1], 16);
  return [(v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff];
}

function toHexColor(rgb) {
  const c = rgb.map((v) => Math.max(0, Math.min(255, Math.round(v))));
  return (
    "#" +
    c
      .map((v) => v.toString(16).padStart(2, "0"))
      .join("")
  );
}

function blend(base, overlayHex, mix) {
  const or_ = (overlayHex >> 16) & 0xff;
  const og = (overlayHex >> 8) & 0xff;
  const ob = overlayHex & 0xff;
  return [or_ * mix + base[0] * (1 - mix), og * mix + base[1] * (1 - mix), ob * mix + base[2] * (1 - mix)];
}

// Colour-wheel slots that carry only a name (no colour resource), e.g. the Mayans BEAM230.
// The BEAM230 values are the ones the operator confirmed on the real rig
// (.claude/memory/mayans-beam-color-rgb.md); the rest are common wheel colours.
const WHEEL_NAME_COLOURS = [
  ["light blue", "#6fb7ff"], ["dark blue", "#1020c0"], ["congo", "#3a0ca3"], ["deep red", "#c00000"],
  ["light green", "#80ff80"], ["dark green", "#008040"], ["lavender", "#b57edc"],
  ["magenta", "#ff00ff"], ["purple", "#8000ff"], ["violet", "#8a2be2"], ["cyan", "#00ffff"],
  ["aqua", "#00ffff"], ["turquoise", "#40e0d0"], ["amber", "#ff7e00"], ["orange", "#ff6e00"],
  ["yellow", "#ffff00"], ["pink", "#ff058c"], ["rose", "#ff3b8b"], ["red", "#ff0000"],
  ["green", "#00ff00"], ["lime", "#adff2f"], ["blue", "#0f0fff"], ["uv", "#9400d3"],
  ["ultraviolet", "#9400d3"], ["cto", "#ffc58f"], ["ctb", "#cfe2ff"], ["warm", "#ffd9a0"],
  ["white", "#ffffff"], ["open", "#ffffff"],
];

function wheelSlotColour(cap) {
  if (!cap) return null;
  if (Array.isArray(cap.res) && typeof cap.res[0] === "string" && cap.res[0][0] === "#") return hexToRgbTriplet(cap.res[0]);
  const name = String(cap.name || "").toLowerCase();
  for (const [word, hex] of WHEEL_NAME_COLOURS) {
    if (new RegExp("(^|[^a-z])" + word + "([^a-z]|$)").test(name)) return hexToRgbTriplet(hex);
  }
  return null;
}

// The capability holding the value, or the nearest one when the wheel only lists single slot values.
function wheelCapability(ch, value) {
  const hit = findCapability(ch, value);
  if (hit || !ch || !Array.isArray(ch.caps) || !ch.caps.length) return hit;
  let best = null;
  let bestDist = Infinity;
  for (const c of ch.caps) {
    const d = value < c.min ? c.min - value : value > c.max ? value - c.max : 0;
    if (d < bestDist) { bestDist = d; best = c; }
  }
  return bestDist <= 8 ? best : null;
}

function computeHeadColor(fxModel, roles, getVal) {
  // 1. colour wheel: first wheel channel whose current slot has a non-black
  //    colour wins (ui/src/monitor/monitorfixtureitem.cpp:351-357)
  let wheelBlackout = false;
  for (const wi of roles.wheelChannels) {
    const cap = wheelCapability(fxModel.ch[wi], getVal(wi));
    // a slot like "Blackout" / "Off" (e.g. Chauvet Swarm's LED Operation at 0) means no light
    if (cap && /blackout|\boff\b|no light/i.test(cap.name || "")) {
      wheelBlackout = true;
      continue;
    }
    const rgb = wheelSlotColour(cap);
    if (rgb && !(rgb[0] === 0 && rgb[1] === 0 && rgb[2] === 0)) return toHexColor(rgb);
  }
  const hasMixing = Object.keys(roles.colourChannels).length > 0;
  if (wheelBlackout && !hasMixing) return "#000000";

  let base = [0, 0, 0];
  let colourFound = false;

  const rC = roles.colourChannels[COLOUR.Red];
  const gC = roles.colourChannels[COLOUR.Green];
  const bC = roles.colourChannels[COLOUR.Blue];
  if (rC !== undefined && gC !== undefined && bC !== undefined) {
    base = [getVal(rC), getVal(gC), getVal(bC)];
    colourFound = true;
  } else {
    const cC = roles.colourChannels[COLOUR.Cyan];
    const mC = roles.colourChannels[COLOUR.Magenta];
    const yC = roles.colourChannels[COLOUR.Yellow];
    if (cC !== undefined && mC !== undefined && yC !== undefined) {
      const c = getVal(cC) / 255;
      const m = getVal(mC) / 255;
      const y = getVal(yC) / 255;
      base = [255 * (1 - c), 255 * (1 - m), 255 * (1 - y)];
      colourFound = true;
    }
  }

  // white/amber/UV/lime/indigo blend on top, in this order
  // (qmlui/fixtureutils.cpp:344-361)
  const blends = [
    [COLOUR.White, 0xffffff],
    [COLOUR.Amber, 0xff7e00],
    [COLOUR.UV, 0x9400d3],
    [COLOUR.Lime, 0xadff2f],
    [COLOUR.Indigo, 0x4b0082],
  ];
  for (const pair of blends) {
    const ci = roles.colourChannels[pair[0]];
    if (ci !== undefined) {
      colourFound = true;
      const v = getVal(ci);
      if (v > 0) base = blend(base, pair[1], v / 255);
    }
  }

  if (!colourFound) return "#ffffff";
  return toHexColor(base);
}

function computeShutter(fxModel, roles, getVal) {
  let result = "open";
  for (const si of roles.shutterChannels) {
    const ch = fxModel.ch[si];
    const cap = findCapability(ch, getVal(si));
    const text = (cap ? (cap.preset || "") + " " + (cap.name || "") : "").toLowerCase();
    if (/close|closed|blackout/.test(text)) return "closed";
    if (/strobe/.test(text)) result = "strobe";
  }
  return result;
}

// Current beam spread (degrees) for a head, from its zoom channel if any.
// With qxf lens angle data (lensMin/lensMax, both non-zero) the zoom scales
// between them; otherwise it scales the model's base spread 50%-150%.
function computeSpreadDeg(fxModel, roles, getVal) {
  if (roles.zoomChannel === null) return fxModel.beamSpread;
  const phys = fxModel.physical || {};
  const t = getVal(roles.zoomChannel) / 255;
  if (phys.lensMin && phys.lensMax) {
    return phys.lensMin + (phys.lensMax - phys.lensMin) * t;
  }
  return fxModel.beamSpread * (0.5 + t);
}

function combine16(getVal, msbIdx, lsbIdx) {
  if (msbIdx === null && lsbIdx === null) return 0;
  if (msbIdx !== null && lsbIdx !== null) return getVal(msbIdx) * 256 + getVal(lsbIdx);
  if (msbIdx !== null) return getVal(msbIdx) * 257;
  return getVal(lsbIdx) * 257;
}

/**
 * Computes the renderable state for one head.
 * @param {object} fxModel from buildFixtureModel()
 * @param {object} head one entry of fxModel.heads
 * @param {object} dmxByUniverse map of universeId -> Uint8Array(512)
 * @param {number} [masterAlpha] optional external dimmer multiplier (0..1)
 * @param {object} [overrideFlags] {invertPan, invertTilt, panOffset, tiltOffset}
 */
export function computeHeadState(fxModel, head, dmxByUniverse, masterAlpha, overrideFlags) {
  const bytes = dmxByUniverse && dmxByUniverse[fxModel.universe];
  const getVal = (chIndex) => {
    if (!bytes) return 0;
    const addr = fxModel.address + chIndex;
    return addr >= 0 && addr < bytes.length ? bytes[addr] : 0;
  };

  let pan = null;
  let tilt = null;
  if (head.hasPanTilt) {
    const pan16 = combine16(getVal, head.panMSB, head.panLSB);
    const tilt16 = combine16(getVal, head.tiltMSB, head.tiltLSB);
    pan = (pan16 / 65535) * fxModel.panMax - fxModel.panMax / 2;
    tilt = (tilt16 / 65535) * fxModel.tiltMax - fxModel.tiltMax / 2;
    if (overrideFlags && overrideFlags.invertPan) pan = -pan;
    if (overrideFlags && overrideFlags.invertTilt) tilt = -tilt;
    pan += (overrideFlags && overrideFlags.panOffset) || 0;
    tilt += (overrideFlags && overrideFlags.tiltOffset) || 0;
  }

  let dimmerRaw = head.dimmer !== null ? getVal(head.dimmer) / 255 : 1;
  if (head.masterDimmer !== undefined && head.masterDimmer !== null) dimmerRaw *= getVal(head.masterDimmer) / 255;
  const master = masterAlpha === undefined || masterAlpha === null ? 1 : masterAlpha;
  const dimmer = dimmerRaw * master;

  return {
    pan: pan,
    tilt: tilt,
    dimmer: dimmer,
    color: computeHeadColor(fxModel, head, getVal),
    shutter: computeShutter(fxModel, head, getVal),
    beamSpreadDeg: computeSpreadDeg(fxModel, head, getVal),
    hasPanTilt: head.hasPanTilt,
  };
}

/** Computes the state of every head of a fixture. */
export function computeFixtureState(fxModel, dmxByUniverse, masterAlpha, stageFixtureEntry) {
  const flags = stageFixtureEntry
    ? {
        invertPan: !!stageFixtureEntry.invertPan,
        invertTilt: !!stageFixtureEntry.invertTilt,
        panOffset: stageFixtureEntry.panOffset || 0,
        tiltOffset: stageFixtureEntry.tiltOffset || 0,
      }
    : null;
  return fxModel.heads.map((h) => computeHeadState(fxModel, h, dmxByUniverse, masterAlpha, flags));
}

// ------------------------------------------------------------- selftest --

export function selftest() {
  const results = [];
  let pass = 0;
  let fail = 0;
  function check(name, ok, detail) {
    results.push({ name: name, ok: ok, detail: detail });
    if (ok) pass++;
    else fail++;
  }

  const fx = {
    id: 1,
    name: "Test MH",
    manufacturer: "Test",
    model: "MH1",
    type: "Moving Head",
    universe: 0,
    address: 0,
    channels: 8,
    physical: { panMax: 540, tiltMax: 270 },
    heads: [[0, 1, 2, 3, 4, 5, 6, 7]],
    ch: [
      { name: "Pan", group: "Pan", byte: 0, colour: 0 },
      { name: "Pan fine", group: "Pan", byte: 1, colour: 0 },
      { name: "Tilt", group: "Tilt", byte: 0, colour: 0 },
      { name: "Tilt fine", group: "Tilt", byte: 1, colour: 0 },
      { name: "Dimmer", group: "Intensity", byte: 0, colour: 0 },
      { name: "Red", group: "Intensity", byte: 0, colour: COLOUR.Red },
      { name: "Green", group: "Intensity", byte: 0, colour: COLOUR.Green },
      { name: "Blue", group: "Intensity", byte: 0, colour: COLOUR.Blue },
    ],
  };
  const model = buildFixtureModel(fx, null);
  check("classify moving head", model.kind === "movingHead", "kind=" + model.kind);

  const dmx = { 0: new Uint8Array(8) };
  dmx[0][0] = 128;
  dmx[0][1] = 0;
  dmx[0][2] = 128;
  dmx[0][3] = 0;
  dmx[0][4] = 255;
  dmx[0][5] = 255;
  dmx[0][6] = 0;
  dmx[0][7] = 0;

  const st = computeFixtureState(model, dmx, 1, null)[0];
  const expectedPan = (128 * 256 / 65535) * 540 - 270;
  check("pan formula", Math.abs(st.pan - expectedPan) < 0.01, "pan=" + st.pan + " expected=" + expectedPan);
  check("dimmer full", Math.abs(st.dimmer - 1) < 0.001, "dimmer=" + st.dimmer);
  check("color red", st.color.toLowerCase() === "#ff0000", "color=" + st.color);

  const st2 = computeFixtureState(model, dmx, 1, { invertPan: true })[0];
  check("invert pan negates", Math.abs(st2.pan + st.pan) < 0.01, "pan=" + st2.pan + " orig=" + st.pan);

  const st3 = computeFixtureState(model, dmx, 1, { panOffset: 10 })[0];
  check("pan offset adds", Math.abs(st3.pan - (st.pan + 10)) < 0.01, "pan=" + st3.pan);

  // no fine channel -> *257 full-range scaling
  const fx2 = {
    id: 2,
    name: "Test MH no-fine",
    manufacturer: "Test",
    model: "MH2",
    type: "Moving Head",
    universe: 0,
    address: 0,
    channels: 6,
    physical: { panMax: 540, tiltMax: 270 },
    heads: [[0, 1, 2, 3, 4, 5]],
    ch: [
      { name: "Pan", group: "Pan", byte: 0, colour: 0 },
      { name: "Tilt", group: "Tilt", byte: 0, colour: 0 },
      { name: "Dimmer", group: "Intensity", byte: 0, colour: 0 },
      { name: "Red", group: "Intensity", byte: 0, colour: COLOUR.Red },
      { name: "Green", group: "Intensity", byte: 0, colour: COLOUR.Green },
      { name: "Blue", group: "Intensity", byte: 0, colour: COLOUR.Blue },
    ],
  };
  const model2 = buildFixtureModel(fx2, null);
  const dmx2 = { 0: new Uint8Array(6) };
  dmx2[0][0] = 255;
  const st4 = computeFixtureState(model2, dmx2, 1, null)[0];
  check("no-fine pan uses *257 scaling", Math.abs(st4.pan - 270) < 0.01, "pan=" + st4.pan);

  return { pass: pass, fail: fail, results: results };
}
