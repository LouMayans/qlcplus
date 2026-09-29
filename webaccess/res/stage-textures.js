/*
  stage-textures.js

  Free per-part texture library + picker + colour/gloss "filters", shared by
  stage-propeditor.js (prop-definition parts) and stage-editor.js (per-object
  instance "Look" overrides). See .claude/memory/stage-visualizer.md
  "Texture library / picker / filters" for the full contract.

  Library: /stage-lib/textures/index.json (tools/stagelib/fetch_textures.py) -
  {"version":2,"textures":{"<slug>":{name,category,map,thumb,tileInchesX,
  tileInchesY,roughness,metalness,source,license,tint?}}}. `tint` (only on a
  couple of synthesized colour variants, e.g. red/black velvet) is a baked-in
  default {color,amount} composed UNDER any part/instance filter (see
  composeFilters) - the same canvas-tint mechanism used for user filters.

  Filters (goal: dim/saturate/hue-shift/tint/gloss a part, textured or not,
  WITHOUT a new shader): {brightness,contrast,saturation,hue,tintColor,
  tintAmount,gloss}. For a flat colour, applyColorFilters() computes an
  adjusted hex colour. For a textured part, the filtered image is baked once
  on an offscreen 2D canvas (`ctx.filter` + a tint composite), cached by
  slug+filters, and CLONED per part instance (same pattern stage-props.js's
  old applyTiledTexture used for its shared base textures) so repeat/rotation
  stay per-instance while the decode+bake is shared. The clone's own
  `.dispose()` is wrapped to release the shared bake's refcount, so it's
  freed automatically whenever three.js's existing disposeObject3D()
  (stage-scene.js) disposes a mesh's material.map - no new lifecycle hooks
  needed in files this session doesn't own.
*/

import * as THREE from "three";

export const TEXTURE_ROOT = "/stage-lib/textures/";

// ------------------------------------------------------------- library ----

let indexPromise = null;
export function getLibraryIndex() {
  if (!indexPromise) {
    indexPromise = fetch(TEXTURE_ROOT + "index.json")
      .then((r) => (r && r.ok ? r.json() : { version: 2, textures: {} }))
      .catch(() => ({ version: 2, textures: {} }));
  }
  return indexPromise;
}

export function getTextureEntry(slug) {
  if (!slug) return Promise.resolve(null);
  return getLibraryIndex().then((idx) => (idx.textures && idx.textures[slug]) || null);
}

export function libraryCategories(idx) {
  const set = new Set();
  Object.keys((idx && idx.textures) || {}).forEach((slug) => set.add(idx.textures[slug].category || "Other"));
  return Array.from(set).sort();
}

// -------------------------------------------------------------- filters ----

export const DEFAULT_FILTERS = Object.freeze({
  brightness: 1,
  contrast: 1,
  saturation: 1,
  hue: 0,
  tintColor: null,
  tintAmount: 0,
  gloss: null, // null = inherit the material/texture's own roughness
});

export function normalizeFilters(f) {
  const src = f || {};
  return {
    brightness: src.brightness != null ? src.brightness : 1,
    contrast: src.contrast != null ? src.contrast : 1,
    saturation: src.saturation != null ? src.saturation : 1,
    hue: src.hue != null ? src.hue : 0,
    tintColor: src.tintColor || null,
    tintAmount: src.tintAmount != null ? src.tintAmount : 0,
    gloss: src.gloss != null ? src.gloss : null,
  };
}

/** `over` wins ties (object-over-part composition); numeric knobs multiply/add. */
export function composeFilters(base, over) {
  const b = normalizeFilters(base);
  const o = normalizeFilters(over);
  const out = {
    brightness: b.brightness * o.brightness,
    contrast: b.contrast * o.contrast,
    saturation: b.saturation * o.saturation,
    hue: b.hue + o.hue,
    tintColor: null,
    tintAmount: 0,
    gloss: o.gloss != null ? o.gloss : b.gloss,
  };
  if (o.tintColor && o.tintAmount > 0) {
    out.tintColor = o.tintColor;
    out.tintAmount = o.tintAmount;
  } else if (b.tintColor && b.tintAmount > 0) {
    out.tintColor = b.tintColor;
    out.tintAmount = b.tintAmount;
  }
  return out;
}

function tintAsFilters(tint) {
  if (!tint || !tint.color) return null;
  return { tintColor: tint.color, tintAmount: tint.amount != null ? tint.amount : 0.8 };
}

const EPS = 0.01;
export function isDefaultFilters(f) {
  if (!f) return true;
  const n = normalizeFilters(f);
  return (
    Math.abs(n.brightness - 1) < EPS &&
    Math.abs(n.contrast - 1) < EPS &&
    Math.abs(n.saturation - 1) < EPS &&
    Math.abs(n.hue) < 0.5 &&
    !(n.tintColor && n.tintAmount > 0.001)
  );
}

function filterCacheKey(f) {
  const n = normalizeFilters(f);
  const r2 = (v) => Math.round(v * 100) / 100;
  return [r2(n.brightness), r2(n.contrast), r2(n.saturation), Math.round(n.hue), n.tintColor || "-", r2(n.tintAmount)].join(",");
}

export function cssFilterString(f) {
  const n = normalizeFilters(f);
  return `brightness(${n.brightness}) contrast(${n.contrast}) saturate(${n.saturation}) hue-rotate(${n.hue}deg)`;
}

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

function hexToRgb(hex) {
  const h = (hex || "#888888").replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16) || 0;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgbToHex(r, g, b) {
  const c = (v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, "0");
  return "#" + c(r) + c(g) + c(b);
}

// Simple HSL-based hue rotation (an approximation of the CSS hue-rotate()
// matrix - good enough for a flat "tint/dim a colour swatch" control, not
// meant to pixel-match the canvas filter used for photos).
function hueRotateRgb(r, g, b, degrees) {
  const rr = r / 255, gg = g / 255, bb = b / 255;
  const max = Math.max(rr, gg, bb), min = Math.min(rr, gg, bb);
  let h, s;
  const l = (max + min) / 2;
  if (max === min) {
    h = s = 0;
  } else {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === rr) h = (gg - bb) / d + (gg < bb ? 6 : 0);
    else if (max === gg) h = (bb - rr) / d + 2;
    else h = (rr - gg) / d + 4;
    h /= 6;
  }
  h = (h + degrees / 360) % 1;
  if (h < 0) h += 1;
  if (s === 0) return [l * 255, l * 255, l * 255];
  const hue2rgb = (p, q, t) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [hue2rgb(p, q, h + 1 / 3) * 255, hue2rgb(p, q, h) * 255, hue2rgb(p, q, h - 1 / 3) * 255];
}

/** Adjusts a flat hex colour by `filters` - used for untextured parts, and
 * as the object-instance override colour path. Mirrors (approximately) the
 * canvas `ctx.filter` pipeline used for photos, plus the same tint composite. */
export function applyColorFilters(hex, filters) {
  if (isDefaultFilters(filters)) return hex;
  const n = normalizeFilters(filters);
  let [r, g, b] = hexToRgb(hex);
  r *= n.brightness;
  g *= n.brightness;
  b *= n.brightness;
  const c = n.contrast;
  r = (r - 127.5) * c + 127.5;
  g = (g - 127.5) * c + 127.5;
  b = (b - 127.5) * c + 127.5;
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  r = lum + (r - lum) * n.saturation;
  g = lum + (g - lum) * n.saturation;
  b = lum + (b - lum) * n.saturation;
  if (Math.abs(n.hue) > 0.5) [r, g, b] = hueRotateRgb(clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255), n.hue);
  r = clamp(r, 0, 255);
  g = clamp(g, 0, 255);
  b = clamp(b, 0, 255);
  if (n.tintColor && n.tintAmount > 0) {
    const [tr, tg, tb] = hexToRgb(n.tintColor);
    const a = clamp(n.tintAmount, 0, 1);
    r = r * (1 - a) + tr * a;
    g = g * (1 - a) + tg * a;
    b = b * (1 - a) + tb * a;
  }
  return rgbToHex(r, g, b);
}

// --------------------------------------------------- texture bake cache ----
// One shared, refcounted, canvas-filtered THREE.Texture per (slug, filters)
// combo. Callers (stage-props.js) `.clone()` the returned `tex` for their own
// per-part `.repeat`/`.rotation` (a clone gets its own GPU texture object but
// shares the decoded/filtered pixel source - same pattern the old shared
// base-texture cache used), then wrap the clone's `.dispose()` so releasing
// it also drops this cache's refcount - freed for real once nothing uses it.
const bakeCache = new Map(); // key -> {refs, promise, tex}

function releaseBake(key) {
  const entry = bakeCache.get(key);
  if (!entry) return;
  entry.refs--;
  if (entry.refs <= 0) {
    bakeCache.delete(key);
    if (entry.tex) entry.tex.dispose();
  }
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  });
}

function finalizeTex(tex) {
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  if ("colorSpace" in tex) tex.colorSpace = THREE.SRGBColorSpace;
  tex.needsUpdate = true;
  return tex;
}

function bakeTexture(mapUrl, filters) {
  return loadImage(mapUrl).then((img) => {
    if (isDefaultFilters(filters)) return finalizeTex(new THREE.Texture(img));
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth || img.width || 1024;
    canvas.height = img.naturalHeight || img.height || 1024;
    const ctx = canvas.getContext("2d");
    ctx.filter = cssFilterString(filters);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const n = normalizeFilters(filters);
    if (n.tintColor && n.tintAmount > 0) {
      ctx.filter = "none";
      ctx.globalAlpha = clamp(n.tintAmount, 0, 1);
      ctx.globalCompositeOperation = "source-atop";
      ctx.fillStyle = n.tintColor;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
    }
    return finalizeTex(new THREE.CanvasTexture(canvas));
  });
}

/**
 * Resolves slug -> a shared, refcounted, already-filtered base texture.
 * Returns {tex, tileX, tileY, roughness, metalness, release()} or null (slug
 * not in the library / failed to load). `filters` is composed UNDER the
 * library entry's own baked-in `tint` (if any - see module docstring).
 * Callers must `.clone()` `tex` before using it as a material.map, and
 * arrange for that clone to eventually `.dispose()` (normal three.js
 * lifecycle) - wrap the clone's dispose to also call `release()`.
 */
export function acquireTexture(slug, filters) {
  if (!slug) return Promise.resolve(null);
  return getTextureEntry(slug).then((libEntry) => {
    if (!libEntry || !libEntry.map) return null;
    const finalFilters = composeFilters(tintAsFilters(libEntry.tint), filters);
    const key = slug + "|" + filterCacheKey(finalFilters);
    let entry = bakeCache.get(key);
    if (!entry) {
      entry = { refs: 0, tex: null };
      entry.promise = bakeTexture(TEXTURE_ROOT + libEntry.map, finalFilters).then(
        (tex) => {
          entry.tex = tex;
          return tex;
        },
        () => null
      );
      bakeCache.set(key, entry);
    }
    entry.refs++;
    return entry.promise.then((tex) => {
      if (!tex) {
        releaseBake(key);
        return null;
      }
      return {
        tex,
        tileX: libEntry.tileInchesX || 36,
        tileY: libEntry.tileInchesY || 36,
        roughness: libEntry.roughness,
        metalness: libEntry.metalness,
        release: () => releaseBake(key),
      };
    });
  });
}

// ----------------------------------------------------------- thumbnails ----

/** A lazy <img> (or a small tinted <canvas> for a synthesized colour variant
 * like the velvet slugs) for the picker grid / part panel swatch. */
export function buildThumbElement(entry, sizePx) {
  const size = sizePx || 72;
  const url = TEXTURE_ROOT + (entry.thumb || entry.map);
  if (!entry.tint) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = entry.name || "";
    img.src = url;
    return img;
  }
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const img = new Image();
  img.onload = () => {
    ctx.drawImage(img, 0, 0, size, size);
    ctx.globalAlpha = clamp(entry.tint.amount != null ? entry.tint.amount : 0.8, 0, 1);
    ctx.globalCompositeOperation = "source-atop";
    ctx.fillStyle = entry.tint.color;
    ctx.fillRect(0, 0, size, size);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
  };
  img.src = url;
  return canvas;
}

// -------------------------------------------------------- picker popover ---

let pickerEl = null;

function ensurePickerDom() {
  if (pickerEl) return pickerEl;
  const overlay = document.createElement("div");
  overlay.className = "tex-picker-overlay hidden";
  overlay.innerHTML = `
    <div class="tex-picker-modal">
      <div class="tex-picker-head">
        <div class="tex-picker-tabs"></div>
        <input class="tex-picker-search" type="text" placeholder="Search textures…">
        <button class="tex-picker-close" type="button" title="Cancel (Esc)">✕</button>
      </div>
      <div class="tex-picker-grid"></div>
      <div class="tex-picker-foot">
        <span class="tex-picker-selname hint"></span>
        <span class="pe-spacer"></span>
        <button class="tex-picker-cancel" type="button">Cancel</button>
        <button class="tex-picker-ok primary" type="button">OK</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  pickerEl = {
    overlay,
    tabs: overlay.querySelector(".tex-picker-tabs"),
    search: overlay.querySelector(".tex-picker-search"),
    grid: overlay.querySelector(".tex-picker-grid"),
    selname: overlay.querySelector(".tex-picker-selname"),
    closeBtn: overlay.querySelector(".tex-picker-close"),
    cancelBtn: overlay.querySelector(".tex-picker-cancel"),
    okBtn: overlay.querySelector(".tex-picker-ok"),
  };
  return pickerEl;
}

/**
 * Opens the texture picker popover. `opts`:
 *   currentSlug: string|null - initially selected/highlighted
 *   onHover(slug|null): called on hover/click for a live preview
 *   onPick(slug|null): called once, on OK (null = the "None" tile)
 *   onCancel(): called once, on Cancel/Esc/outside click (caller should
 *     restore whatever onHover(currentSlug) looked like before opening)
 */
export function openTexturePicker(opts) {
  const o = opts || {};
  const dom = ensurePickerDom();
  let selected = o.currentSlug || null;
  let activeCategory = "All";
  let search = "";
  let closed = false;
  let textures = {};
  let renderGrid = () => {};

  function close(cb) {
    if (closed) return;
    closed = true;
    dom.overlay.classList.add("hidden");
    document.removeEventListener("keydown", onKey);
    if (cb) cb();
  }
  function onKey(ev) {
    if (ev.key === "Escape") {
      ev.preventDefault();
      close(o.onCancel);
    }
  }
  function pick(slug, name) {
    selected = slug;
    dom.selname.textContent = name;
    renderGrid();
    o.onHover && o.onHover(slug);
  }

  getLibraryIndex().then((idx) => {
    if (closed) return;
    textures = idx.textures || {};
    const cats = ["All"].concat(libraryCategories(idx));
    dom.tabs.innerHTML = "";
    cats.forEach((c) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "tex-picker-tab" + (c === activeCategory ? " active" : "");
      b.textContent = c;
      b.addEventListener("click", () => {
        activeCategory = c;
        dom.tabs.querySelectorAll(".tex-picker-tab").forEach((el) => el.classList.toggle("active", el === b));
        renderGrid();
      });
      dom.tabs.appendChild(b);
    });

    renderGrid = function () {
      dom.grid.innerHTML = "";
      const q = search.trim().toLowerCase();
      // "None" tile - always first, clears the part's texture.
      if (!q) {
        const noneTile = document.createElement("div");
        noneTile.className = "tex-picker-tile" + (selected == null ? " selected" : "");
        noneTile.innerHTML = '<div class="tex-picker-thumb tex-picker-none">None</div><div class="tex-picker-name">No texture</div>';
        noneTile.addEventListener("mouseenter", () => o.onHover && o.onHover(null));
        noneTile.addEventListener("click", () => pick(null, "No texture"));
        dom.grid.appendChild(noneTile);
      }

      Object.keys(textures)
        .filter((slug) => activeCategory === "All" || textures[slug].category === activeCategory)
        .filter((slug) => !q || slug.toLowerCase().includes(q) || (textures[slug].name || "").toLowerCase().includes(q))
        .sort((a, b) => (textures[a].name || a).localeCompare(textures[b].name || b))
        .forEach((slug) => {
          const entry = textures[slug];
          const tile = document.createElement("div");
          tile.className = "tex-picker-tile" + (selected === slug ? " selected" : "");
          const thumbWrap = document.createElement("div");
          thumbWrap.className = "tex-picker-thumb";
          thumbWrap.appendChild(buildThumbElement(entry, 96));
          tile.appendChild(thumbWrap);
          const name = document.createElement("div");
          name.className = "tex-picker-name";
          name.textContent = entry.name || slug;
          tile.appendChild(name);
          tile.addEventListener("mouseenter", () => o.onHover && o.onHover(slug));
          tile.addEventListener("click", () => pick(slug, entry.name || slug));
          dom.grid.appendChild(tile);
        });
    };
    renderGrid();
    dom.selname.textContent = selected ? (textures[selected] && textures[selected].name) || selected : "No texture";
  });

  dom.search.value = "";
  dom.search.oninput = () => {
    search = dom.search.value;
    renderGrid();
  };

  dom.closeBtn.onclick = () => close(o.onCancel);
  dom.cancelBtn.onclick = () => close(o.onCancel);
  dom.okBtn.onclick = () => close(() => o.onPick && o.onPick(selected));
  document.addEventListener("keydown", onKey);
  dom.overlay.classList.remove("hidden");
}

// ---------------------------------------------------- filter controls UI ---

/**
 * Builds a small filters editor (Brightness/Contrast/Saturation/Hue/Tint/
 * Gloss + Reset), shared by the prop editor and the Properties panel.
 * `filters` is read once for initial values (callers rebuild their panel on
 * every change, same convention as the rest of this codebase).
 * `onSet(key, value)` fires on every input change; `key` is one of
 * brightness/contrast/saturation/hue/tintColor/tintAmount/gloss/gloss-off,
 * or "__reset__" (value is ignored) from the Reset button.
 */
export function buildFilterControls(filters, onSet) {
  const f = normalizeFilters(filters);
  const wrap = document.createElement("div");
  wrap.className = "tex-filters";

  function row(labelText, input) {
    const label = document.createElement("label");
    label.className = "tex-filter-row";
    const span = document.createElement("span");
    span.textContent = labelText;
    label.appendChild(span);
    label.appendChild(input);
    wrap.appendChild(label);
    return label;
  }
  function range(key, min, max, step, val) {
    const input = document.createElement("input");
    input.type = "range";
    input.min = String(min);
    input.max = String(max);
    input.step = String(step);
    input.value = String(val);
    input.addEventListener("input", () => onSet(key, parseFloat(input.value)));
    return input;
  }

  row("Brightness", range("brightness", 0.3, 2, 0.02, f.brightness));
  row("Contrast", range("contrast", 0.3, 2, 0.02, f.contrast));
  row("Saturation", range("saturation", 0, 2, 0.02, f.saturation));
  row("Hue shift", range("hue", -180, 180, 1, f.hue));

  const tintRow = document.createElement("label");
  tintRow.className = "tex-filter-row";
  const tintSpan = document.createElement("span");
  tintSpan.textContent = "Tint";
  const tintColor = document.createElement("input");
  tintColor.type = "color";
  tintColor.value = f.tintColor || "#ff8800";
  tintColor.addEventListener("input", () => onSet("tintColor", tintColor.value));
  const tintAmount = document.createElement("input");
  tintAmount.type = "range";
  tintAmount.min = "0";
  tintAmount.max = "1";
  tintAmount.step = "0.02";
  tintAmount.value = String(f.tintAmount);
  tintAmount.addEventListener("input", () => onSet("tintAmount", parseFloat(tintAmount.value)));
  tintRow.appendChild(tintSpan);
  tintRow.appendChild(tintColor);
  tintRow.appendChild(tintAmount);
  wrap.appendChild(tintRow);

  const glossRow = document.createElement("label");
  glossRow.className = "tex-filter-row";
  const glossSpan = document.createElement("span");
  glossSpan.textContent = "Gloss";
  const glossOn = document.createElement("input");
  glossOn.type = "checkbox";
  glossOn.checked = f.gloss != null;
  glossOn.title = "Override this part's roughness";
  const glossSlider = document.createElement("input");
  glossSlider.type = "range";
  glossSlider.min = "0";
  glossSlider.max = "1";
  glossSlider.step = "0.02";
  glossSlider.value = String(f.gloss != null ? f.gloss : 0.5);
  glossSlider.disabled = f.gloss == null;
  glossOn.addEventListener("change", () => {
    glossSlider.disabled = !glossOn.checked;
    onSet("gloss", glossOn.checked ? parseFloat(glossSlider.value) : null);
  });
  glossSlider.addEventListener("input", () => onSet("gloss", parseFloat(glossSlider.value)));
  glossRow.appendChild(glossSpan);
  glossRow.appendChild(glossOn);
  glossRow.appendChild(glossSlider);
  wrap.appendChild(glossRow);

  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "pe-btn";
  resetBtn.textContent = "Reset filters";
  resetBtn.addEventListener("click", () => onSet("__reset__", null));
  wrap.appendChild(resetBtn);

  return wrap;
}
