"""Fetch real fixture 3D "looks" from GDTF Share for the browser stage visualizer.

    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/fetch_gdtf.py --dry-run
    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/fetch_gdtf.py --limit 80
    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/fetch_gdtf.py --category beam230 --limit 10

What it does
------------
1. Logs into https://gdtf-share.com with the credentials in
   C:\\lightai-data\\gdtf-share.json (never printed/logged).
2. Downloads the full revision list (getList.php) and matches "<manufacturer> <fixture>"
   against the regexes in tools/stagelib/wishlist.json, picking the newest revision per
   matching fixture and round-robining across categories up to --limit downloads.
3. Downloads each matched .gdtf (a zip) into the cache C:\\lightai-data\\gdtf-cache\\<rid>.gdtf
   (reused on reruns), skipping anything over 40 MB, with a short delay between requests.
4. Parses description.xml out of each cached zip (models, geometry tree + position matrices,
   DMX pan/tilt ranges, beam optics, gobo wheels) and writes
   C:\\qlcplus-dev\\Web\\stage-lib\\gdtf\\<slug>\\look.json plus the model/gobo files it
   references.
5. Rebuilds C:\\qlcplus-dev\\Web\\stage-lib\\index.json from every look.json on disk plus the
   built-in QLC+ .dae meshes in stage-lib/builtin/.

Only files under stage-lib/gdtf/ and stage-lib/index.json are written; stage-lib/builtin/ is
read-only here. Only json/glb/gltf/bin/3ds/dae/png/jpg/svg are ever written into stage-lib
(the dev web server only serves those extensions).
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from lxml import etree

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
CREDENTIALS_PATH = Path(r"C:\lightai-data\gdtf-share.json")
CACHE_DIR = Path(r"C:\lightai-data\gdtf-cache")
OUT_ROOT = Path(r"C:\qlcplus-dev\Web\stage-lib")
OUT_GDTF = OUT_ROOT / "gdtf"
BUILTIN_DIR = OUT_ROOT / "builtin"
WISHLIST_PATH = Path(__file__).with_name("wishlist.json")

API_BASE = "https://gdtf-share.com/apis/public"
LOGIN_URL = f"{API_BASE}/login.php"
LIST_URL = f"{API_BASE}/getList.php"
DOWNLOAD_URL = f"{API_BASE}/downloadFile.php"

MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
DOWNLOAD_DELAY_S = 1.2
DEFAULT_LIMIT = 80

# Operator priority: spot/beam moving heads get ~25 of the default 80 downloads, and we try a
# few revisions per fixture (newest first) looking for one with separate Yoke/Head Axis models
# so the page can animate pan/tilt ("movable": true).
PRIORITY_CATEGORY = "spot_beam_movers"
PRIORITY_SHARE = 25 / 80
PRIORITY_CANDIDATES_PER_FIXTURE = 3

MODEL_EXTS = (".glb", ".3ds")
IMAGE_KEEP_EXTS = (".png", ".jpg", ".jpeg", ".svg")


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Auth / HTTP
# ---------------------------------------------------------------------------


def make_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def login(opener: urllib.request.OpenerDirector) -> None:
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(f"Missing credentials file: {CREDENTIALS_PATH}")
    creds = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    body = json.dumps({"user": creds["user"], "password": creds["password"]}).encode("utf-8")
    req = urllib.request.Request(
        LOGIN_URL, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Never echo the request body (it contains the password).
        raise SystemExit(f"GDTF Share login failed: HTTP {e.code} {e.reason}") from None
    if not data.get("result"):
        raise SystemExit(f"GDTF Share login rejected: {data.get('error', 'unknown error')}")
    log(f"Logged in to GDTF Share ({data.get('notice', 'ok')}).")


def get_list(opener: urllib.request.OpenerDirector) -> list[dict]:
    with opener.open(LIST_URL, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("result"):
        raise SystemExit(f"getList.php failed: {data.get('error', 'unknown error')}")
    return data.get("list", [])


def download_file(opener: urllib.request.OpenerDirector, rid: int, dest: Path) -> tuple[bool, str]:
    """Download rid's .gdtf into dest (using the cache if already present)."""
    if dest.exists() and dest.stat().st_size > 0:
        return True, "cached"
    req = urllib.request.Request(f"{DOWNLOAD_URL}?rid={rid}")
    try:
        with opener.open(req, timeout=120) as resp:
            length = resp.headers.get("Content-Length")
            if length is not None and int(length) > MAX_DOWNLOAD_BYTES:
                return False, f"skipped (Content-Length {int(length)/1e6:.1f} MB > 40 MB)"
            tmp = dest.with_suffix(".part")
            total = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        f.close()
                        tmp.unlink(missing_ok=True)
                        return False, "skipped (exceeded 40 MB while streaming)"
                    f.write(chunk)
            tmp.replace(dest)
            return True, f"downloaded ({total/1e6:.1f} MB)"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"error {e}"


# ---------------------------------------------------------------------------
# Wishlist matching
# ---------------------------------------------------------------------------


def load_wishlist() -> dict:
    return json.loads(WISHLIST_PATH.read_text(encoding="utf-8"))["categories"]


def ts(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def build_matches(
    revisions: list[dict], wishlist: dict, only_categories: set[str] | None
) -> tuple[dict[str, list[dict]], dict[tuple[str, str], list[dict]]]:
    """Returns (matches, priority_pool).

    matches: category -> deduped (newest revision per manufacturer+fixture) list, sorted
    newest-first. Used for reporting and for non-priority-category downloads.

    priority_pool: for PRIORITY_CATEGORY only, identity -> up to PRIORITY_CANDIDATES_PER_FIXTURE
    revisions (newest first), so the caller can fall back to an older revision if the newest
    one lacks separate pan/tilt axis models.
    """
    out: dict[str, list[dict]] = {}
    priority_pool: dict[tuple[str, str], list[dict]] = {}
    for category, patterns in wishlist.items():
        if only_categories and category not in only_categories:
            continue
        compiled = [(re.compile(p["match"], re.IGNORECASE), p.get("note", "")) for p in patterns]
        groups: dict[tuple[str, str], list[dict]] = {}
        for rev in revisions:
            name = f"{rev.get('manufacturer', '')} {rev.get('fixture', '')}".strip()
            note = None
            for regex, n in compiled:
                if regex.search(name):
                    note = n
                    break
            if note is None:
                continue
            key = (rev.get("manufacturer", "").lower(), rev.get("fixture", "").lower())
            candidate = dict(rev)
            candidate["_category"] = category
            candidate["_note"] = note
            groups.setdefault(key, []).append(candidate)
        for key, revs in groups.items():
            revs.sort(key=lambda r: ts(r.get("lastModified")), reverse=True)
        if category == PRIORITY_CATEGORY:
            priority_pool = {k: v[:PRIORITY_CANDIDATES_PER_FIXTURE] for k, v in groups.items()}
        out[category] = sorted((v[0] for v in groups.values()), key=lambda r: ts(r.get("lastModified")), reverse=True)
    return out, priority_pool


def select_for_download(matches: dict[str, list[dict]], limit: int) -> list[dict]:
    """Round-robin across categories so coverage is balanced within the global cap."""
    queues = {cat: list(revs) for cat, revs in matches.items()}
    selected: list[dict] = []
    while len(selected) < limit and any(queues.values()):
        for cat in list(queues.keys()):
            if not queues[cat]:
                continue
            selected.append(queues[cat].pop(0))
            if len(selected) >= limit:
                break
    return selected


# ---------------------------------------------------------------------------
# GDTF (description.xml) parsing
# ---------------------------------------------------------------------------


def slugify(*parts: str) -> str:
    s = "-".join(parts).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s or "fixture"


def parse_matrix(s: str | None) -> list[float]:
    identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    if not s:
        return identity
    groups = re.findall(r"\{([^}]*)\}", s)
    if len(groups) != 4:
        return identity
    out: list[float] = []
    try:
        for g in groups:
            nums = [float(x) for x in g.split(",")]
            if len(nums) != 4:
                return identity
            out.extend(nums)
    except ValueError:
        return identity
    return out


def cie_xyy_to_hex(color: str | None) -> str | None:
    if not color:
        return None
    try:
        parts = [float(x) for x in color.split(",")]
    except ValueError:
        return None
    if len(parts) < 2:
        return None
    x, y = parts[0], parts[1]
    Y = parts[2] if len(parts) > 2 else 1.0
    if y == 0:
        return None
    X = (x / y) * Y
    Z = ((1 - x - y) / y) * Y
    # XYZ (D65) -> linear sRGB
    r = 3.2406 * X - 1.5372 * Y - 0.4986 * Z
    g = -0.9689 * X + 1.8758 * Y + 0.0415 * Z
    b = 0.0557 * X - 0.2040 * Y + 1.0570 * Z

    def gamma(c: float) -> float:
        c = max(0.0, min(1.0, c))
        return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055

    r, g, b = (max(0.0, min(1.0, gamma(c))) for c in (r, g, b))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def ffloat(el, attr, default=None):
    v = el.get(attr)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


class ParseFailure(Exception):
    pass


def find_description_xml(zf) -> bytes:
    names = zf.namelist()
    candidates = [n for n in names if n.lower().endswith("description.xml")]
    if not candidates:
        raise ParseFailure("no description.xml in archive")
    candidates.sort(key=lambda n: n.count("/"))
    return zf.read(candidates[0])


def find_ci(names_lower: dict[str, str], *want_prefixes_and_stem) -> str | None:
    """names_lower: lowercased-path -> original path. Look for <prefix>/<stem>.<ext>."""
    prefix, stem, exts = want_prefixes_and_stem
    for ext in exts:
        target = f"{prefix}/{stem}{ext}".lower()
        if target in names_lower:
            return names_lower[target]
    # fall back: any file under the prefix whose stem matches
    for lower, orig in names_lower.items():
        if lower.startswith(prefix + "/") and Path(lower).stem == stem.lower() and Path(lower).suffix in exts:
            return orig
    return None


def parse_gdtf(zf, rid: int, revision_meta: dict) -> dict:
    xml_bytes = find_description_xml(zf)
    root = etree.fromstring(xml_bytes)
    ft = root.find("FixtureType")
    if ft is None:
        raise ParseFailure("no FixtureType element")

    manufacturer = ft.get("Manufacturer", "").strip() or "Unknown"
    name = ft.get("Name", "").strip() or ft.get("LongName", "").strip() or "Unknown"

    names_lower = {n.lower(): n for n in zf.namelist()}

    # --- Models ---------------------------------------------------------
    models: dict[str, dict] = {}
    models_el = ft.find("Models")
    if models_el is not None:
        for m in models_el.findall("Model"):
            mname = m.get("Name", "")
            file_attr = m.get("File", mname)
            models[mname] = {
                "length": ffloat(m, "Length", 0.0),
                "width": ffloat(m, "Width", 0.0),
                "height": ffloat(m, "Height", 0.0),
                "primitive": m.get("PrimitiveType", "Undefined"),
                "file_attr": file_attr,
            }

    # --- DMX modes: pan/tilt physical ranges + which geometries they drive
    pan_range = [None, None]
    tilt_range = [None, None]
    pan_geoms: set[str] = set()
    tilt_geoms: set[str] = set()
    modes_el = ft.find("DMXModes")
    if modes_el is not None:
        for mode in modes_el.findall("DMXMode"):
            chans_el = mode.find("DMXChannels")
            if chans_el is None:
                continue
            for chan in chans_el.findall("DMXChannel"):
                geom = chan.get("Geometry", "")
                for logical in chan.findall("LogicalChannel"):
                    attr = (logical.get("Attribute") or "").lower()
                    if not (attr.startswith("pan") or attr.startswith("tilt")):
                        continue
                    los, his = [], []
                    for cf in logical.findall("ChannelFunction"):
                        lo = ffloat(cf, "PhysicalFrom")
                        hi = ffloat(cf, "PhysicalTo")
                        if lo is not None:
                            los.append(lo)
                        if hi is not None:
                            his.append(hi)
                    if not los and not his:
                        continue
                    lo = min(los) if los else 0.0
                    hi = max(his) if his else 0.0
                    if attr.startswith("pan"):
                        pan_geoms.add(geom)
                        pan_range[0] = lo if pan_range[0] is None else min(pan_range[0], lo)
                        pan_range[1] = hi if pan_range[1] is None else max(pan_range[1], hi)
                    else:
                        tilt_geoms.add(geom)
                        tilt_range[0] = lo if tilt_range[0] is None else min(tilt_range[0], lo)
                        tilt_range[1] = hi if tilt_range[1] is None else max(tilt_range[1], hi)
    pan_range = [pan_range[0] or 0.0, pan_range[1] or 0.0]
    tilt_range = [tilt_range[0] or 0.0, tilt_range[1] or 0.0]

    # --- Geometry tree ----------------------------------------------------
    parts: list[dict] = []
    geoms_el = ft.find("Geometries")
    beam_info = {}

    def classify(tag: str, gname: str) -> str:
        low = gname.lower()
        if tag == "Beam":
            return "beam"
        if gname in pan_geoms or "yoke" in low:
            return "yoke"
        if gname in tilt_geoms or "head" in low:
            return "head"
        if "lens" in low or "beam" in low:
            return "beam"
        return "other"

    def walk(el, parent_name: str | None, is_root: bool):
        tag = etree.QName(el).localname
        gname = el.get("Name", "")
        model_ref = el.get("Model")
        role = "base" if is_root else classify(tag, gname)
        size = [0.0, 0.0, 0.0]
        file_name = None
        primitive = "Undefined"
        if model_ref and model_ref in models:
            mdl = models[model_ref]
            size = [mdl["length"], mdl["width"], mdl["height"]]
            primitive = mdl["primitive"]
            found = find_ci(names_lower, "models/gltf", mdl["file_attr"], (".glb",))
            if not found:
                found = find_ci(names_lower, "models/3ds", mdl["file_attr"], (".3ds",))
            if found:
                ext = Path(found).suffix.lower()
                file_name = slugify(model_ref) + ext
                file_name = (file_name, found)  # placeholder, resolved by caller
        if tag == "Beam":
            beam_info["angleDeg"] = ffloat(el, "BeamAngle")
            beam_info["fieldAngleDeg"] = ffloat(el, "FieldAngle")
            radius_m = ffloat(el, "BeamRadius")
            beam_info["radiusInches"] = radius_m * 39.3701 if radius_m is not None else None
            beam_info["type"] = el.get("BeamType")
            beam_info["lumens"] = ffloat(el, "LuminousFlux")
            beam_info["kelvin"] = ffloat(el, "ColorTemperature")

        parts.append({
            "name": gname,
            "role": role,
            "parent": parent_name,
            "_tag": tag,
            "_file": file_name,
            "primitive": primitive,
            "matrix": parse_matrix(el.get("Position")),
            "sizeMetres": size,
        })
        for child in el:
            ctag = etree.QName(child).localname
            if ctag in ("Geometry", "Axis", "Beam", "FilterBeam", "FilterColor", "FilterGobo", "Display", "Laser", "MediaServerLayer"):
                walk(child, gname, False)

    if geoms_el is not None:
        roots = [c for c in geoms_el if etree.QName(c).localname in ("Geometry", "Axis")]
        for i, r in enumerate(roots):
            walk(r, None, True if i == 0 else False)

    # --- Wheels / gobos -----------------------------------------------
    gobos = []
    wheels_el = ft.find("Wheels")
    if wheels_el is not None:
        for wheel in wheels_el.findall("Wheel"):
            wname = wheel.get("Name", "")
            for slot in wheel.findall("Slot"):
                media = slot.get("MediaFileName") or None
                found_img = None
                if media:
                    stem = Path(media).stem
                    found_img = find_ci(names_lower, "wheels", stem, (".png", ".jpg", ".jpeg", ".svg", ".bmp", ".tif", ".tiff"))
                gobos.append({
                    "wheel": wname,
                    "slot": slot.get("Name", ""),
                    "name": slot.get("Name", ""),
                    "_srcfile": found_img,
                    "color": cie_xyy_to_hex(slot.get("Color")),
                })

    prism_facets = None
    if geoms_el is not None:
        for el in geoms_el.iter():
            if "prism" in etree.QName(el).localname.lower():
                facets = el.get("NumberOfFacets") or el.get("Facets")
                if facets:
                    try:
                        prism_facets = int(float(facets))
                    except ValueError:
                        pass

    base_size = parts[0]["sizeMetres"] if parts else [0.0, 0.0, 0.0]
    dims_inches = [round(v * 39.3701, 2) for v in (base_size[1], base_size[0], base_size[2])]

    yoke_file = next((p["_file"] for p in parts if p["_tag"] == "Axis" and p["role"] == "yoke" and p["_file"]), None)
    head_file = next((p["_file"] for p in parts if p["_tag"] == "Axis" and p["role"] == "head" and p["_file"]), None)
    movable = bool(yoke_file and head_file and yoke_file[1] != head_file[1])

    return {
        "id": None,  # filled by caller once slug is known
        "name": name,
        "manufacturer": manufacturer,
        "category": None,  # filled by caller
        "source": {
            "site": "gdtf-share.com",
            "rid": rid,
            "revision": revision_meta.get("revision"),
            "lastModified": revision_meta.get("lastModified"),
        },
        "dimensionsInches": dims_inches,
        "panRange": pan_range,
        "tiltRange": tilt_range,
        "beam": {
            "angleDeg": beam_info.get("angleDeg"),
            "fieldAngleDeg": beam_info.get("fieldAngleDeg"),
            "radiusInches": beam_info.get("radiusInches"),
            "type": beam_info.get("type"),
            "lumens": beam_info.get("lumens"),
            "kelvin": beam_info.get("kelvin"),
        },
        "parts": parts,
        "gobos": gobos,
        "prismFacets": prism_facets,
        "movable": movable,
    }


def materialize_assets(zf, look: dict, out_dir: Path) -> None:
    """Copy model/gobo files referenced by _file/_srcfile into out_dir, then strip the temp keys."""
    out_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()

    def unique(name: str) -> str:
        if name not in used_names:
            used_names.add(name)
            return name
        stem, ext = Path(name).stem, Path(name).suffix
        i = 2
        while f"{stem}-{i}{ext}" in used_names:
            i += 1
        chosen = f"{stem}-{i}{ext}"
        used_names.add(chosen)
        return chosen

    for part in look["parts"]:
        part.pop("_tag", None)
        fentry = part.pop("_file", None)
        if not fentry:
            part["file"] = None
            continue
        wanted_name, src_path = fentry
        ext = Path(src_path).suffix.lower()
        if ext not in MODEL_EXTS:
            part["file"] = None
            continue
        out_name = unique(wanted_name.lower())
        (out_dir / out_name).write_bytes(zf.read(src_path))
        part["file"] = out_name

    for gobo in look["gobos"]:
        src = gobo.pop("_srcfile", None)
        if not src:
            gobo["file"] = None
            continue
        ext = Path(src).suffix.lower()
        if ext not in IMAGE_KEEP_EXTS:
            # No image conversion library (PIL) is available in this environment;
            # per spec, skip formats that aren't already png/jpg/svg.
            gobo["file"] = None
            continue
        base = slugify(gobo["wheel"], gobo["slot"]) + ext
        out_name = unique(base)
        (out_dir / out_name).write_bytes(zf.read(src))
        gobo["file"] = out_name


# ---------------------------------------------------------------------------
# index.json
# ---------------------------------------------------------------------------


def rebuild_index() -> tuple[int, int]:
    looks = []
    for look_path in sorted(OUT_GDTF.glob("*/look.json")):
        try:
            data = json.loads(look_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log(f"  [index] skipping unreadable {look_path}: {e}")
            continue
        has_model = any(p.get("file") for p in data.get("parts", []))
        looks.append({
            "id": data["id"],
            "name": data["name"],
            "manufacturer": data.get("manufacturer"),
            "category": data.get("category"),
            "thumb": None,
            "path": f"gdtf/{look_path.parent.name}/look.json",
            "beamAngleDeg": (data.get("beam") or {}).get("angleDeg"),
            "hasModel": has_model,
            "movable": bool(data.get("movable")),
        })
    builtin_count = 0
    if BUILTIN_DIR.exists():
        for dae in sorted(BUILTIN_DIR.glob("*.dae")):
            builtin_count += 1
            pretty = dae.stem.replace("_", " ").replace("-", " ").title()
            looks.append({
                "id": f"builtin/{dae.stem}",
                "name": f"{pretty} (QLC+ generic)",
                "manufacturer": None,
                "category": "builtin",
                "thumb": None,
                "path": None,
                "model": f"builtin/{dae.stem}.dae",
                "hasModel": True,
                # QLC+'s moving_head.dae and scanner.dae have separate arm/head nodes that pan and tilt
                "movable": dae.stem in ("moving_head", "scanner"),
            })
    # Movable spot/beam moving heads (animatable pan+tilt) surface first.
    looks.sort(key=lambda l: (0 if l.get("movable") else 1, 0 if l.get("category") == PRIORITY_CATEGORY else 1))
    index = {"version": 1, "looks": looks}
    (OUT_ROOT / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return len(looks) - builtin_count, builtin_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def download_and_parse_one(opener, rev: dict, cat: str, tag: str) -> tuple[bool, dict | None, str]:
    """Download+parse a single revision candidate. Returns (ok, look_or_None, message)."""
    import zipfile

    rid = rev["rid"]
    cache_path = CACHE_DIR / f"{rid}.gdtf"
    ok, msg = download_file(opener, rid, cache_path)
    if not ok:
        return False, None, f"download {msg}"
    if msg != "cached":
        time.sleep(DOWNLOAD_DELAY_S)
    try:
        with zipfile.ZipFile(cache_path) as zf:
            look = parse_gdtf(zf, rid, rev)
            slug = slugify(look["manufacturer"], look["name"])
            out_dir = OUT_GDTF / slug
            base_slug, n = slug, 2
            while out_dir.exists() and not (out_dir / "look.json").exists():
                slug = f"{base_slug}-{n}"
                out_dir = OUT_GDTF / slug
                n += 1
            look["id"] = f"gdtf/{slug}"
            look["category"] = cat
            materialize_assets(zf, look, out_dir)
            (out_dir / "look.json").write_text(json.dumps(look, indent=2), encoding="utf-8")
        return True, look, f"parsed ok ({tag})"
    except (ParseFailure, zipfile.BadZipFile, etree.XMLSyntaxError, KeyError, OSError) as e:
        return False, None, f"parse FAILED: {e}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"max number of fixtures to download (default {DEFAULT_LIMIT})")
    ap.add_argument("--category", action="append", help="restrict to this wishlist category (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="list matches per category without downloading anything")
    args = ap.parse_args()

    wishlist = load_wishlist()
    only_categories = set(args.category) if args.category else None
    if only_categories:
        unknown = only_categories - set(wishlist.keys())
        if unknown:
            raise SystemExit(f"Unknown --category value(s): {sorted(unknown)}. Known: {sorted(wishlist.keys())}")

    opener = make_opener()
    login(opener)
    log("Fetching revision list...")
    revisions = get_list(opener)
    log(f"Fetched {len(revisions)} revisions from GDTF Share.")

    matches, priority_pool = build_matches(revisions, wishlist, only_categories)

    for cat, revs in matches.items():
        log(f"\n[{cat}] {len(revs)} matching fixture(s)")
        for r in revs:
            n_cands = len(priority_pool.get((r.get("manufacturer", "").lower(), r.get("fixture", "").lower()), [])) if cat == PRIORITY_CATEGORY else 1
            extra = f" ({n_cands} revisions on file)" if cat == PRIORITY_CATEGORY and n_cands > 1 else ""
            log(f"  rid={r['rid']:<7} {r.get('manufacturer','')} {r.get('fixture','')!r} rev={r.get('revision','?')} "
                f"modified={r.get('lastModified','?')} -- {r['_note']}{extra}")

    empty_categories = [c for c, revs in matches.items() if not revs]

    if args.dry_run:
        total = sum(len(v) for v in matches.values())
        log(f"\n[dry-run] {total} total matches across {len(matches)} categories. No downloads performed.")
        if empty_categories:
            log(f"[dry-run] categories with zero matches: {empty_categories}")
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_GDTF.mkdir(parents=True, exist_ok=True)

    # Priority budget: spot/beam moving heads get ~25 of the default 80 downloads (scaled to
    # --limit), or the whole budget if --category was narrowed to just that category.
    priority_identities = sorted(priority_pool.values(), key=lambda lst: ts(lst[0].get("lastModified")), reverse=True)
    if priority_identities:
        if only_categories == {PRIORITY_CATEGORY}:
            priority_target = min(len(priority_identities), args.limit)
        else:
            priority_target = min(len(priority_identities), max(1, round(args.limit * PRIORITY_SHARE)))
        chosen_priority = priority_identities[:priority_target]
    else:
        chosen_priority = []
    remaining_budget = max(0, args.limit - len(chosen_priority))

    other_matches = {c: v for c, v in matches.items() if c != PRIORITY_CATEGORY}
    chosen_other = select_for_download(other_matches, remaining_budget)

    tasks: list[tuple[str, list[dict]]] = [(PRIORITY_CATEGORY, cands) for cands in chosen_priority]
    tasks += [(r["_category"], [r]) for r in chosen_other]

    log(f"\nSelected {len(tasks)} fixtures to download (limit={args.limit}, "
        f"{len(chosen_priority)} reserved for {PRIORITY_CATEGORY}).")

    downloaded = 0
    failed = 0
    parsed = 0
    parse_failed = 0
    per_category_counts: dict[str, int] = {}
    movable_report: list[tuple[str, bool]] = []  # (fixture label, movable) for spot_beam_movers

    for i, (cat, candidates) in enumerate(tasks):
        chosen_look = None
        chosen_rev = None
        last_msg = "no candidates"
        for c_idx, rev in enumerate(candidates):
            label = f"{rev.get('manufacturer','')} {rev.get('fixture','')}".strip()
            tag = f"candidate {c_idx+1}/{len(candidates)}"
            ok, look, msg = download_and_parse_one(opener, rev, cat, tag)
            log(f"[{i+1}/{len(tasks)}] [{cat}] rid={rev['rid']} {label!r} {tag}: {msg}")
            last_msg = msg
            if not ok:
                failed += 1
                continue
            downloaded += 1
            if cat != PRIORITY_CATEGORY:
                chosen_look, chosen_rev = look, rev
                break
            # Priority category: keep trying candidates until one is movable, or fall back
            # to the best (first successfully parsed) one once candidates run out.
            if chosen_look is None:
                chosen_look, chosen_rev = look, rev
            if look.get("movable"):
                chosen_look, chosen_rev = look, rev
                break
            if c_idx < len(candidates) - 1:
                log(f"    not movable (missing separate Yoke/Head Axis models) - trying an older revision")

        if chosen_look is None:
            continue
        parsed += 1
        per_category_counts[cat] = per_category_counts.get(cat, 0) + 1
        if cat == PRIORITY_CATEGORY:
            label = f"{chosen_look['manufacturer']} {chosen_look['name']}"
            movable_report.append((label, bool(chosen_look.get("movable"))))

    n_looks, n_builtin = rebuild_index()

    log("\n=== Summary ===")
    log(f"Downloaded: {downloaded}, failed downloads: {failed}, parsed OK: {parsed}, parse failures: {parse_failed}")
    for cat in wishlist:
        log(f"  {cat}: {per_category_counts.get(cat, 0)} written")
    if empty_categories:
        log(f"Categories with zero GDTF-Share matches: {empty_categories}")
    if movable_report:
        n_movable = sum(1 for _, m in movable_report if m)
        log(f"\nSpot/beam movers: {n_movable}/{len(movable_report)} have separate Yoke+Head axis models (movable=true):")
        for label, m in movable_report:
            log(f"  [{'movable' if m else 'static'}] {label}")
    log(f"index.json rebuilt: {n_looks} gdtf looks + {n_builtin} builtin meshes -> {OUT_ROOT / 'index.json'}")


if __name__ == "__main__":
    main()
