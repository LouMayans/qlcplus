"""Download CC0 surface textures for the 3D stage view's material presets AND the
free per-part texture picker (stage-textures.js / stage-propeditor.js).

    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/fetch_textures.py

Files go to C:\\qlcplus-dev\\Web\\stage-lib\\textures\\<slug>\\ (served at
/stage-lib/textures/) with textures/index.json =
{"version":2,"textures":{"<slug>":{
    "name":..., "category":"Wood|Metal|Stone|Fabric|Leather|Tile|Brick|Other",
    "map":"<slug>/diffuse_1k.jpg", "thumb":"<slug>/thumb.jpg",
    "tileInchesX":.., "tileInchesY":.., "roughness":.., "metalness":..,
    "source":"https://polyhaven.com/a/<id>" | "https://ambientcg.com/a/<id>",
    "license":"CC0",
    "tint": {"color":"#hex","amount":0.0-1.0}   # only on synthesized colour variants
}}}

Two CC0 sources:
  - Poly Haven (https://api.polyhaven.com) - searched by keyword, since exact
    ids drift over time (see POLYHAVEN_WANTED).
  - ambientCG (https://ambientcg.com/api/v2) - used only where Poly Haven has
    no matching asset (see AMBIENTCG_WANTED); ids were looked up once via its
    search API and are pinned here for determinism. Downloads the 1K-JPG zip
    (stdlib zipfile, no Pillow available) and pulls out "<id>_1K-JPG_Color.jpg".

Colour/diffuse map only (no normal/roughness maps) - one shared shader variant.
A ~256px thumbnail is also fetched per texture (Poly Haven's own
`thumbnail_url` catalogue field; ambientCG's `previewImage` "256-JPG-FFFFFF")
so the in-page texture picker never has to load a full 1k map just to show a
grid of options.

Real-world tile size: Poly Haven's /info/<id> "dimensions" (mm) is used when
present (true photo scale - see stage-props.js's applyTiledTexture). ambientCG
material listings don't expose that, so those slugs get a sane per-category
guess instead (documented per entry below) - still stored in the same
tileInchesX/Y fields so callers never need two fallback paths.

No true black/red CC0 LEATHER photo turned out to be needed - ambientCG has a
real black leather set (Leather026) and Poly Haven has real red/brown/white
leather sets. Where a colour genuinely doesn't exist as a photo (red/black
VELVET - no CC0 velvet dye variants were found on either source), the closest
neutral photo (Poly Haven's "velour_velvet") is fetched ONCE and a `tint`
field records the colour/amount to bake on at runtime (stage-textures.js's
canvas filter pipeline - the same mechanism used for the prop editor's
per-part Tint/colour filters, see stage-visualizer.md). No separate image is
downloaded for the tinted variant; it reuses the neutral slug's map/thumb.

Poly Haven blocks Python's default User-Agent, so send our own.
Re-runnable: existing image files are skipped (metadata/index entries are
always recomputed so name/category/thumb fixes apply on a re-run).
"""
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

OUT = Path(r"C:\qlcplus-dev\Web\stage-lib\textures")
UA = {"User-Agent": "qlcplus-stage-visualizer/1.0 (local club previz)"}
PH_API = "https://api.polyhaven.com"
ACG_API = "https://ambientcg.com/api/v2"

# ---------------------------------------------------------------- Poly Haven --
# slug -> (category, display name, ordered candidate substrings to search the
# catalogue for, roughness, metalness). The first not-yet-claimed catalogue id
# matching an earlier-priority pattern wins (never reusing an id another slug
# already claimed).
POLYHAVEN_WANTED: dict[str, tuple[str, str, list[str], float, float]] = {
    # --- original realism-pass set (kept working, now also gets name/category/thumb) ---
    "brick-red": ("Brick", "Red brick", ["red_brick", "brick_red", "brick_wall_001", "brick_wall", "brick"], 0.88, 0.0),
    "brick-dark": ("Brick", "Dark painted brick", ["painted_brick", "black_brick", "dark_brick", "brick_wall_010", "brick"], 0.88, 0.0),
    "brick-white": ("Brick", "White brick", ["white_brick", "painted_brick_white", "brick_whitewash", "whitewash", "brick"], 0.88, 0.0),
    "concrete": ("Stone", "Concrete", ["concrete_wall", "concrete_floor", "polished_concrete", "concrete"], 0.85, 0.02),
    "wood-panel": ("Wood", "Oak wood planks", ["wood_planks", "wooden_planks", "planks", "plywood", "wood_floor"], 0.5, 0.0),
    "glossy-tile": ("Tile", "Glossy floor tile", ["tiles_glossy", "black_tiles", "floor_tiles", "marble_tiles", "tiles"], 0.18, 0.05),
    "fabric": ("Fabric", "Linen fabric pattern", ["fabric_pattern", "carpet", "fabric"], 0.85, 0.0),
    # --- new: Wood ---
    "wood-walnut": ("Wood", "Walnut veneer", ["walnut_veneer", "black_walnut_veneer_01", "american_walnut_veneer"], 0.45, 0.0),
    "wood-dark-stained": ("Wood", "Dark stained wood", ["dark_wooden_planks", "dark_paneled_wood", "dark_wood"], 0.4, 0.0),
    "wood-plywood": ("Wood", "Plywood", ["plywood"], 0.6, 0.0),
    "wood-weathered-planks": ("Wood", "Weathered wood planks", ["old_wood_floor", "wood_floor_worn", "brown_planks_09", "brown_planks_08"], 0.75, 0.0),
    "wood-parquet": ("Wood", "Parquet floor", ["herringbone_parquet", "diagonal_parquet", "rectangular_parquet"], 0.35, 0.0),
    # --- new: Metal ---
    "metal-rusty": ("Metal", "Rusty metal", ["rusty_metal", "rusted_shutter", "rust_coarse_01"], 0.8, 0.55),
    "metal-corrugated": ("Metal", "Corrugated metal", ["corrugated_iron", "rusty_corrugated_iron"], 0.55, 0.75),
    # --- new: Stone ---
    "stone-granite": ("Stone", "Granite", ["granite_tile"], 0.45, 0.0),
    "stone-slate": ("Stone", "Slate", ["slate_floor"], 0.7, 0.0),
    "stone-terrazzo": ("Stone", "Terrazzo", ["terrazzo_tiles"], 0.25, 0.0),
    "stone-plaster": ("Stone", "Plaster / stucco", ["grey_plaster", "white_stucco", "clay_plaster"], 0.9, 0.0),
    # --- new: Fabric ---
    "fabric-denim": ("Fabric", "Denim", ["denim_fabric"], 0.8, 0.0),
    # neutral base for the red/black VELVET colour variants (tinted at runtime - see module docstring)
    "fabric-velvet-red": ("Fabric", "Red velvet", ["velour_velvet"], 0.7, 0.0),
    # --- new: Leather (real CC0 colour photos - no tint needed) ---
    "leather-red": ("Leather", "Red leather", ["leather_red_02", "leather_red_03"], 0.4, 0.0),
    "leather-brown": ("Leather", "Brown leather", ["brown_leather"], 0.45, 0.0),
    "leather-white": ("Leather", "White leather", ["leather_white"], 0.4, 0.0),
    # --- new: Tile ---
    "tile-checker": ("Tile", "Checker ceramic tile", ["checkered_pavement_tiles"], 0.3, 0.05),
    # --- new: Other ---
    "other-asphalt": ("Other", "Asphalt", ["asphalt_01", "asphalt_02"], 0.95, 0.0),
    "other-rubber": ("Other", "Rubber floor", ["rubber_tiles"], 0.9, 0.0),
}

# A synthesized colour variant that reuses another slug's downloaded map/thumb
# (never downloaded itself) plus a canvas tint baked on at runtime.
# slug -> (category, name, source_slug, tint_color, tint_amount)
TINT_VARIANTS: dict[str, tuple[str, str, str, str, float]] = {
    "fabric-velvet-black": ("Fabric", "Black velvet", "fabric-velvet-red", "#0a0a0d", 0.88),
}
# fabric-velvet-red is itself ALSO a tint (Poly Haven's velour_velvet has no
# strong red dye) - applied on top of the real downloaded photo.
POLYHAVEN_TINT: dict[str, tuple[str, float]] = {
    "fabric-velvet-red": ("#7a1020", 0.75),
}

# ------------------------------------------------------------------ ambientCG --
# slug -> (category, name, assetId, roughness, metalness, fallbackTileInches).
# ambientCG material listings don't expose real-world physical size, so each
# gets a sane per-category guess instead of a true photo measurement.
AMBIENTCG_WANTED: dict[str, tuple[str, str, str, float, float, float]] = {
    "leather-black": ("Leather", "Black leather", "Leather026", 0.4, 0.0, 24),
    "metal-brushed-steel": ("Metal", "Brushed steel", "Metal009", 0.3, 0.9, 24),
    "metal-aluminium": ("Metal", "Brushed aluminium", "Metal051A", 0.35, 0.9, 24),
    "metal-diamond-plate": ("Metal", "Diamond plate", "DiamondPlate009", 0.4, 0.8, 24),
    "metal-brass-copper": ("Metal", "Brass / copper", "Metal057A", 0.3, 0.9, 24),
    "stone-marble-white": ("Stone", "White marble", "Marble021", 0.15, 0.0, 36),
    "stone-marble-black": ("Stone", "Black marble", "Marble016", 0.15, 0.0, 36),
    "tile-subway": ("Tile", "Subway tile", "Tiles036", 0.25, 0.0, 12),
    "other-cork": ("Other", "Cork", "Cork002", 0.85, 0.0, 24),
    "fabric-carpet": ("Fabric", "Carpet", "Carpet001", 0.9, 0.0, 36),
}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def get_json(url: str):
    return json.loads(get(url))


# ---------------------------------------------------------------- Poly Haven --

def pick_ids(catalogue: dict) -> dict[str, str]:
    """Greedy match: for each wanted slug (in dict order), walk its candidate
    patterns in priority order and take the first not-yet-claimed catalogue id
    containing that pattern."""
    ids = sorted(catalogue.keys())
    claimed: set[str] = set()
    chosen: dict[str, str] = {}
    for slug, (_cat, _name, patterns, _r, _m) in POLYHAVEN_WANTED.items():
        pick = None
        for pat in patterns:
            for aid in ids:
                if aid in claimed:
                    continue
                if pat in aid.lower():
                    pick = aid
                    break
            if pick:
                break
        if pick:
            claimed.add(pick)
            chosen[slug] = pick
        else:
            print(f"  MISS {slug}: no Poly Haven catalogue match for any of {patterns}")
    return chosen


def _best_map(files: dict, key_substrings: list[str], prefer_res=("1k", "2k", "4k")) -> str | None:
    """Finds the best available jpg URL among files[<key matching one of key_substrings>]
    at the first preferred resolution that exists. Poly Haven's /files/<id> response keys
    texture maps by name (e.g. "Diffuse", "diff", "Rough", "nor_gl", ...) - match loosely."""
    for key, by_res in files.items():
        if not any(s in key.lower() for s in key_substrings):
            continue
        if not isinstance(by_res, dict):
            continue
        for res in prefer_res:
            entry = by_res.get(res)
            if entry and isinstance(entry, dict) and "jpg" in entry:
                return entry["jpg"]["url"]
        for entry in by_res.values():
            if isinstance(entry, dict) and "jpg" in entry:
                return entry["jpg"]["url"]
    return None


def real_size_inches(asset_id: str) -> tuple[float, float] | None:
    """Poly Haven's /info/<id> exposes the texture's real-world physical size
    ("dimensions" field, mm) for most seamless/tileable sets - used to size a
    material's UV repeat at TRUE scale instead of a guessed generic tile size."""
    try:
        info = get_json(f"{PH_API}/info/{asset_id}")
        dims = info.get("dimensions")
        if dims and len(dims) >= 2 and dims[0] and dims[1]:
            return dims[0] / 25.4, dims[1] / 25.4  # mm -> inches
    except Exception as e:
        print(f"  {asset_id}: /info failed ({e})")
    return None


def fetch_thumb_polyhaven(catalogue_entry: dict, folder: Path) -> str | None:
    url = catalogue_entry.get("thumbnail_url")
    if not url:
        return None
    thumb_path = folder / "thumb.jpg"
    try:
        if not thumb_path.exists():
            thumb_path.write_bytes(get(url))
        return "thumb.jpg"
    except Exception as e:
        print(f"  thumbnail fetch failed ({e})")
        return None


def fetch_one_polyhaven(slug: str, asset_id: str, catalogue_entry: dict) -> dict | None:
    cat, name, _patterns, roughness, metalness = POLYHAVEN_WANTED[slug]
    try:
        files = get_json(f"{PH_API}/files/{asset_id}")
    except Exception as e:
        print(f"  {slug} ({asset_id}): /files failed ({e})")
        return None
    # Poly Haven key names vary by asset: "Diffuse"/"diff" classically, but
    # multi-colourway sets (e.g. fabric_pattern_05) use "col_01"/"col_02"/... instead.
    diffuse_url = _best_map(files, ["diff", "albedo", "color", "col_01", "col_1", "col"])
    if not diffuse_url:
        print(f"  {slug} ({asset_id}): no diffuse/albedo map")
        return None

    folder = OUT / slug
    folder.mkdir(parents=True, exist_ok=True)
    diffuse_path = folder / "diffuse_1k.jpg"
    if not diffuse_path.exists():
        diffuse_path.write_bytes(get(diffuse_url))

    thumb = fetch_thumb_polyhaven(catalogue_entry, folder)

    entry = {
        "name": name,
        "category": cat,
        "map": f"{slug}/diffuse_1k.jpg",
        "roughness": roughness,
        "metalness": metalness,
        "source": f"https://polyhaven.com/a/{asset_id}",
        "license": "CC0",
        "polyhavenId": asset_id,
    }
    if thumb:
        entry["thumb"] = f"{slug}/{thumb}"
    size = real_size_inches(asset_id)
    fallback = 36
    entry["tileInchesX"] = round(size[0], 2) if size else fallback
    entry["tileInchesY"] = round(size[1], 2) if size else fallback
    if slug in POLYHAVEN_TINT:
        color, amount = POLYHAVEN_TINT[slug]
        entry["tint"] = {"color": color, "amount": amount}
    return entry


# ------------------------------------------------------------------ ambientCG --

def fetch_one_ambientcg(slug: str) -> dict | None:
    cat, name, asset_id, roughness, metalness, fallback_tile_in = AMBIENTCG_WANTED[slug]
    try:
        info = get_json(f"{ACG_API}/full_json?id={asset_id}&include=downloadData")
        assets = info.get("foundAssets") or []
        if not assets:
            print(f"  {slug} ({asset_id}): ambientCG asset not found")
            return None
        asset = assets[0]
    except Exception as e:
        print(f"  {slug} ({asset_id}): ambientCG lookup failed ({e})")
        return None

    folder = OUT / slug
    folder.mkdir(parents=True, exist_ok=True)
    diffuse_path = folder / "diffuse_1k.jpg"
    if not diffuse_path.exists():
        zip_url = f"https://ambientcg.com/get?file={asset_id}_1K-JPG.zip"
        try:
            zdata = get(zip_url)
            with zipfile.ZipFile(io.BytesIO(zdata)) as z:
                color_name = next((n for n in z.namelist() if n.lower().endswith("_color.jpg")), None)
                if not color_name:
                    print(f"  {slug} ({asset_id}): no *_Color.jpg in zip ({z.namelist()})")
                    return None
                diffuse_path.write_bytes(z.read(color_name))
        except Exception as e:
            print(f"  {slug} ({asset_id}): ambientCG zip failed ({e})")
            return None

    thumb_path = folder / "thumb.jpg"
    thumb = None
    try:
        preview = asset.get("previewImage") or {}
        thumb_url = preview.get("256-JPG-FFFFFF") or preview.get("256-PNG")
        if thumb_url and not thumb_path.exists():
            ext = ".png" if thumb_url.lower().endswith(".png") else ".jpg"
            thumb_path = folder / f"thumb{ext}"
            thumb_path.write_bytes(get(thumb_url))
        if thumb_path.exists():
            thumb = thumb_path.name
    except Exception as e:
        print(f"  {slug}: thumbnail fetch failed ({e})")

    entry = {
        "name": name,
        "category": cat,
        "map": f"{slug}/diffuse_1k.jpg",
        "roughness": roughness,
        "metalness": metalness,
        "tileInchesX": fallback_tile_in,
        "tileInchesY": fallback_tile_in,
        "source": f"https://ambientcg.com/a/{asset_id}",
        "license": "CC0",
        "ambientcgId": asset_id,
    }
    if thumb:
        entry["thumb"] = f"{slug}/{thumb}"
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    catalogue = get_json(f"{PH_API}/assets?t=textures")
    chosen = pick_ids(catalogue)

    textures = {}
    index_path = OUT / "index.json"
    if index_path.exists():
        try:
            textures.update(json.loads(index_path.read_text(encoding="utf-8")).get("textures", {}))
        except Exception:
            pass

    for slug, asset_id in chosen.items():
        got = fetch_one_polyhaven(slug, asset_id, catalogue.get(asset_id, {}))
        if not got:
            continue
        textures[slug] = got
        print(f"  OK {slug}: polyhaven/{asset_id} -> {got['map']}")
        time.sleep(0.2)

    for slug in AMBIENTCG_WANTED:
        got = fetch_one_ambientcg(slug)
        if not got:
            continue
        textures[slug] = got
        print(f"  OK {slug}: ambientcg/{AMBIENTCG_WANTED[slug][2]} -> {got['map']}")
        time.sleep(0.2)

    for slug, (cat, name, source_slug, color, amount) in TINT_VARIANTS.items():
        src = textures.get(source_slug)
        if not src:
            print(f"  MISS {slug}: source slug {source_slug} wasn't fetched")
            continue
        entry = dict(src)
        entry["name"] = name
        entry["category"] = cat
        entry["tint"] = {"color": color, "amount": amount}
        entry.pop("polyhavenId", None)
        entry.pop("ambientcgId", None)
        textures[slug] = entry
        print(f"  OK {slug}: tint of {source_slug} ({color} @ {amount})")

    index_path.write_text(json.dumps({"version": 2, "textures": textures}, indent=2), encoding="utf-8")
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"{len(textures)} textures, {total / 1e6:.1f} MB -> {index_path}")


if __name__ == "__main__":
    main()
