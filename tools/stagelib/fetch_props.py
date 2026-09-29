"""Download CC0 club props (glTF) from Poly Haven for the 3D stage view.

    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/fetch_props.py [--res 1k]

Files go to C:\\qlcplus-dev\\Web\\stage-lib\\props\\<slug>\\ (served at /stage-lib/props/) with
props/index.json = {"version":1,"models":[{id,name,category,url,sizeInches:[w,d,h],source,license,tris}]}.
Sizes are measured from the glTF itself (metres -> inches; w = X, d = Z, h = Y in glTF's Y-up frame).
Re-runnable: existing files are skipped. Poly Haven blocks Python's default User-Agent, so send our own.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import time
import urllib.request
from pathlib import Path

OUT = Path(r"C:\qlcplus-dev\Web\stage-lib\props")
UA = {"User-Agent": "qlcplus-stage-visualizer/1.0 (local club previz)"}
API = "https://api.polyhaven.com"

# Poly Haven id -> (display name, category). Categories match the stage prop categories.
WANTED = {
    "bar_chair_round_01": ("Bar stool (round)", "seating"),
    "metal_stool_01": ("Metal bar stool", "seating"),
    "metal_stool_02": ("Metal stool low", "seating"),
    "mid_century_lounge_chair": ("Lounge chair", "seating"),
    "modern_arm_chair_01": ("Modern armchair", "seating"),
    "ArmChair_01": ("Club armchair", "seating"),
    "Sofa_01": ("Sofa", "booth"),
    "sofa_02": ("Sofa 2", "booth"),
    "sofa_03": ("Sofa 3", "booth"),
    "dining_chair_02": ("Dining chair", "seating"),
    "coffee_table_round_01": ("Round coffee table", "table"),
    "modern_coffee_table_01": ("Modern coffee table", "table"),
    "modern_coffee_table_02": ("Modern coffee table 2", "table"),
    "round_wooden_table_01": ("Round table", "table"),
    "side_table_tall_01": ("Tall cocktail table", "table"),
    "industrial_coffee_table": ("Industrial coffee table", "table"),
    "modern_wooden_cabinet": ("Back-bar cabinet", "bar"),
    "wine_bottles_01": ("Wine bottles", "bar"),
    "wine_barrel_01": ("Wine barrel", "decor"),
    "potted_plant_01": ("Potted plant", "decor"),
    "potted_plant_02": ("Potted plant 2", "decor"),
    "potted_plant_04": ("Succulent", "decor"),
    "planter_box_01": ("Planter box", "decor"),
    "modern_ceiling_lamp_01": ("Ceiling pendant lamp", "decor"),
    "hanging_industrial_lamp": ("Industrial pendant lamp", "decor"),
    "industrial_wall_lamp": ("Wall lamp", "decor"),
    "wooden_crate_01": ("Wooden crate", "decor"),
}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def mat_mul(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def node_matrix(n) -> list:
    """Row-major 4x4 from a glTF node (matrix is column-major; TRS otherwise)."""
    if "matrix" in n:
        m = n["matrix"]
        return [m[c * 4 + r] for r in range(4) for c in range(4)]
    tx, ty, tz = n.get("translation", [0, 0, 0])
    qx, qy, qz, qw = n.get("rotation", [0, 0, 0, 1])
    sx, sy, sz = n.get("scale", [1, 1, 1])
    r = [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw),
         2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw),
         2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]
    return [r[0] * sx, r[1] * sy, r[2] * sz, tx, r[3] * sx, r[4] * sy, r[5] * sz, ty,
            r[6] * sx, r[7] * sy, r[8] * sz, tz, 0, 0, 0, 1]


def measure(gltf: dict) -> tuple:
    """(size in metres [x, y, z], triangle count) from accessor bounds under node transforms."""
    lo, hi, tris = [math.inf] * 3, [-math.inf] * 3, 0
    nodes, meshes, acc = gltf.get("nodes", []), gltf.get("meshes", []), gltf.get("accessors", [])

    def visit(i, parent):
        nonlocal tris
        n = nodes[i]
        m = mat_mul(parent, node_matrix(n))
        if "mesh" in n:
            for prim in meshes[n["mesh"]].get("primitives", []):
                a = acc[prim["attributes"]["POSITION"]]
                if "indices" in prim:
                    tris += acc[prim["indices"]]["count"] // 3
                mn, mx = a["min"], a["max"]
                for cx in (mn[0], mx[0]):
                    for cy in (mn[1], mx[1]):
                        for cz in (mn[2], mx[2]):
                            p = [m[r * 4] * cx + m[r * 4 + 1] * cy + m[r * 4 + 2] * cz + m[r * 4 + 3] for r in range(3)]
                            for k in range(3):
                                lo[k], hi[k] = min(lo[k], p[k]), max(hi[k], p[k])
        for c in n.get("children", []):
            visit(c, m)

    ident = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    scene = gltf.get("scenes", [{}])[gltf.get("scene", 0)]
    for root in scene.get("nodes", range(len(nodes))):
        visit(root, ident)
    return [hi[k] - lo[k] for k in range(3)], tris


def fetch(pid: str, res: str) -> dict | None:
    files = json.loads(get(f"{API}/files/{pid}"))
    entry = (files.get("gltf") or {}).get(res) or next(iter((files.get("gltf") or {}).values()), None)
    if not entry or "gltf" not in entry:
        print(f"  {pid}: no glTF download")
        return None
    g = entry["gltf"]
    folder = OUT / pid.lower()
    folder.mkdir(parents=True, exist_ok=True)
    main_name = Path(g["url"]).name
    main_path = folder / main_name
    if not main_path.exists():
        main_path.write_bytes(get(g["url"]))
    for rel, inc in (g.get("include") or {}).items():
        target = folder / rel
        if Path(rel).suffix.lower() not in (".bin", ".png", ".jpg", ".jpeg"):
            continue  # the web server only serves bin/png/jpg next to the .gltf
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(get(inc["url"]))
    size_m, tris = measure(json.loads(main_path.read_text(encoding="utf-8")))
    return {"file": main_name, "size_m": size_m, "tris": tris}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", default="1k")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    models = []
    for pid, (name, cat) in WANTED.items():
        try:
            got = fetch(pid, args.res)
        except Exception as e:  # keep going: one bad asset shouldn't stop the rest
            print(f"  {pid}: failed ({e})")
            continue
        if not got:
            continue
        w, h, d = (round(v / 0.0254, 1) for v in got["size_m"])
        models.append({"id": pid.lower(), "name": name, "category": cat,
                       "url": f"/stage-lib/props/{pid.lower()}/{got['file']}",
                       "sizeInches": [w, d, h], "source": f"https://polyhaven.com/a/{pid}",
                       "license": "CC0", "tris": got["tris"]})
        print(f"  {pid}: {w} x {d} x {h} in, {got['tris']} tris")
        time.sleep(0.3)
    (OUT / "index.json").write_text(json.dumps({"version": 1, "models": models}, indent=2), encoding="utf-8")
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"{len(models)} models, {total / 1e6:.1f} MB -> {OUT / 'index.json'}")


if __name__ == "__main__":
    main()
