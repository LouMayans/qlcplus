"""A realistic, true-size NIGHTCLUB layout for the Mayans show's 36 fixtures.

`build_stage(project)` returns a stage dict (see .claude/memory/stage-visualizer.md for the
file format: inches, user axes X across / Y depth / Z height, room origin at [0,0,0]).
Used by `demo_show.py --club-layout`.

Room: 48' wide (X) x 40' deep (Y) x 16' high (Z) = 576 x 480 x 192 inches.
Back wall (Y=0) holds the DJ booth; front wall (Y=480) holds the bar and the entrance.
Side walls (X=0 / X=576) hold the VIP booths. The dance floor sits in the middle,
under a box12 truss rig at 14'6" (174") that carries the moving heads.

Every prop id here is prefixed "club-" and every propDef used by an object is embedded in
the returned stage's `propDefs`, so the scene is self-contained (stage-visualizer.md contract).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lightai"))

from lightai.rig.qxw import Workspace  # noqa: E402

# --------------------------------------------------------------------- room --
ROOM = {"width": 576, "depth": 480, "height": 192, "haze": 0.35}
FLOOR_CENTRE = [288, 222, 0]  # dance floor / truss-grid centre; also the stage anchor
TRUSS_Z = 174  # 14'6" trim height for the ceiling rig
DECK_Z = 24  # 2' DJ stage deck


def r16(v: float) -> float:
    return round(v * 16) / 16


def _r16_list(xs):
    return [r16(x) for x in xs]


# ------------------------------------------------------------------ parts ---

_part_seq = [0]


def _pid() -> str:
    _part_seq[0] += 1
    return f"cp{_part_seq[0]}"


def box(size, pos, color, material="matte"):
    return {"id": _pid(), "shape": "box", "size": _r16_list(size), "pos": _r16_list(pos),
            "rot": [0, 0, 0], "color": color, "material": material}


def model_part(model_id, size, pos=(0, 0, 0), rot=None):
    return {"id": _pid(), "shape": "model", "modelId": model_id, "size": _r16_list(size),
            "pos": _r16_list(pos), "rot": list(rot or [0, 0, 0])}


def truss_part(truss_type, length, pos, rot=None):
    return {"id": _pid(), "shape": "truss", "trussType": truss_type, "length": r16(length),
            "pos": _r16_list(pos), "rot": list(rot or [0, 0, 0])}


# LED strips are left out of the club for now (operator: "remove all led strips on that
# project for now") - led_part stays for when strip functionality is revisited.
def led_part(length, color, brightness=0, pos=(0, 0, 0), rot=None):
    return {"id": _pid(), "shape": "ledstrip", "length": r16(length), "ledsPerMeter": 60,
            "color": color, "brightness": brightness, "pos": _r16_list(pos), "rot": list(rot or [0, 0, 0])}


def wall_box(w, d, h, color, inner=None, material="matte"):
    """A single-box prop whose bottom-centre origin sits at the wall segment's floor point."""
    part = box([w, d, h], [0, 0, h / 2], color, material)
    if inner:
        # only the face toward the room, one-sided: seen from inside, see-through from outside
        # (box faces in stage-props.js: right +X, left -X, top up, bottom down, front -> low Y, back -> high Y)
        part["side"] = "front"
        part["hiddenFaces"] = [f for f in ("right", "left", "top", "bottom", "front", "back") if f != inner]
    return {"name": "Wall", "category": "decor", "aliases": [], "parts": [part]}


def defn(name, category, parts, aliases=None):
    return {"name": name, "category": category, "aliases": aliases or [], "parts": parts}


# --------------------------------------------------------------- propDefs ---
# Only real, currently-downloaded models from stage-lib/props/index.json are used
# (checked 2026-09-28): bar_chair_round_01, metal_stool_01, sofa_02, sofa_03,
# modern_coffee_table_02, side_table_tall_01, modern_wooden_cabinet, wine_bottles_01,
# potted_plant_02, hanging_industrial_lamp.

PROP_DEFS = {
    # --- structure ---------------------------------------------------------
    # Real club walls are exposed brick (operator update, overriding the earlier
    # charcoal-paint plan) - neutral near-white part colour so the Poly Haven
    # brick photo (fetch_textures.py) shows its own real colouring unmultiplied.
    "club-wall-back": wall_box(576, 6, 192, "#f2efe9", "back", "brick-red"),
    "club-wall-front": wall_box(576, 6, 192, "#f2efe9", "front", "brick-red"),
    "club-wall-left": wall_box(6, 480, 192, "#f2efe9", "right", "brick-red"),
    "club-wall-right": wall_box(6, 480, 192, "#f2efe9", "left", "brick-red"),
    "club-ceiling": wall_box(576, 480, 6, "#141416", "bottom", "black-acoustic-foam"),
    # Acoustic foam treatment behind the DJ booth, mounted just proud of the
    # (now-brick) back wall - keeps the "foam behind the DJ booth" ask even
    # though the walls themselves are brick, not painted satin.
    "club-dj-foam-panel": defn("DJ acoustic foam", "decor",
                               [box([220, 2, 96], [0, 0, 48], "#101012", "black-acoustic-foam")]),
    "club-entrance-doors": defn("Entrance doors", "decor",
                                [box([72, 3, 84], [0, 0, 42], "#3a3a3a", "metal")]),

    # --- bar -----------------------------------------------------------------
    # category "decor" (not "bar"): seats must come ONLY from the bar counter,
    # one per stool - the cabinet behind it isn't where anyone sits.
    "club-back-bar-cabinet": defn("Back-bar cabinet", "decor", [
        model_part("modern_wooden_cabinet", [96.1, 20.5, 26.8], [0, 0, 0]),
        model_part("wine_bottles_01", [26.6, 3.2, 13.0], [0, -6, 26.8]),
        # a mirror strip behind the back bar (goal 5) - mounted on the wall
        # side (+Y, opposite the crowd-facing bottles at -6) above the shelf.
        box([90, 1, 30], [0, 9, 40], "#f0f0f0", "mirror"),
    ]),
    "club-bar-counter": defn("Bar counter", "bar", [
        box([360, 24, 42], [0, 0, 21], "#3a2a20", "gloss"),
        box([364, 3, 3], [0, -12, 42], "#c9a227", "metal"),  # brass lip trim
        # crowd-facing wood-panel fascia (goal 5: "bar front: wood-panel")
        box([360, 2, 38], [0, -13, 19], "#8a6440", "wood-panel"),
    ]),
    "club-bar-stool": defn("Bar stool", "seating", [model_part("bar_chair_round_01", [19.0, 19.1, 29.5])]),

    # --- DJ booth --------------------------------------------------------------
    "club-dj-stage-deck": defn("DJ stage deck", "stage", [box([144, 72, 24], [0, 0, 12], "#2a2a30")]),
    "club-dj-console": defn("DJ console", "dj", [
        box([100, 20, 38], [0, 0, 19], "#2c2c32"),
        box([90, 14, 4], [0, 6, 40], "#222222", "gloss"),
    ]),
    "club-speaker-stack": defn("Speaker stack", "decor", [
        box([30, 26, 40], [0, 0, 20], "#111111"),
        box([26, 22, 30], [0, 0, 55], "#111111"),
    ]),

    # --- dance floor -------------------------------------------------------
    "club-dance-floor": defn("Dance floor", "floor_area", [box([240, 192, 2], [0, 0, 1], "#15151f", "glossy-tile")]),
    "club-point-marker": defn("Point marker", "point", [box([4, 4, 1], [0, 0, 0.5], "#ff00ff")]),

    # --- VIP booths ----------------------------------------------------------
    "club-vip-sofa-3": defn("VIP sofa", "booth", [model_part("sofa_03", [107.5, 36.4, 44.0])]),
    "club-vip-sofa-2": defn("VIP sofa", "booth", [model_part("sofa_02", [71.1, 32.2, 27.9])]),
    # modern_coffee_table_01 (4504 tris) instead of _02 (13645 tris) - same
    # footprint box, ~3x fewer triangles, x4 instances (perf pass).
    "club-vip-table": defn("VIP coffee table", "decor", [model_part("modern_coffee_table_01", [47.2, 47.2, 14.5])]),

    # --- cocktail tables -----------------------------------------------------
    "club-cocktail-table": defn("Cocktail table", "table", [model_part("side_table_tall_01", [15.1, 15.1, 30.0])]),
    # metal_stool_02 (6532 tris) instead of _01 (8334 tris), x8 instances (perf pass).
    "club-cocktail-stool": defn("Cocktail stool", "seating", [model_part("metal_stool_02", [13.9, 14.0, 34.8])]),

    # --- decor -----------------------------------------------------------
    # potted_plant_04 (8929 tris) instead of _02 (69806 tris) - a decor plant
    # was the single heaviest model in the whole scene at ~40% of its total
    # triangle budget across 4 instances; ~8x lighter here (perf pass).
    "club-potted-plant": defn("Potted plant", "decor", [model_part("potted_plant_04", [27.6, 25.9, 33.1])]),
    "club-pendant-lamp": defn("Pendant lamp", "decor", [model_part("hanging_industrial_lamp", [21.6, 21.6, 53.4])]),

    # --- truss (box12, real lengths so the lacing pattern stays correct) ---
    "club-truss-288": defn("Truss 24'", "decor", [truss_part("box12", 288, [0, 0, 0])]),
    "club-truss-192": defn("Truss 16'", "decor", [truss_part("box12", 192, [0, 0, 0])]),
}

# ------------------------------------------------------------------ objects --
# Truss grid over the dance floor: X 144-432 (24' wide), Y 126-318 (16' deep), Z 174".
TRUSS_X0, TRUSS_X1 = 144, 432
TRUSS_Y0, TRUSS_Y1 = 126, 318
TRUSS_CX, TRUSS_CY = (TRUSS_X0 + TRUSS_X1) / 2, (TRUSS_Y0 + TRUSS_Y1) / 2
FRONT_TRUSS_Y = 350  # a second truss line toward the crowd

OBJECTS = [
    # id-less tuples: (prop, name, category, aliases, pos, rz) - ids assigned in build_stage()
    # --- structure ---
    ("club-wall-back", "Back wall", "decor", [], [288, 3, 0], 0),
    ("club-wall-front", "Front wall", "decor", [], [288, 477, 0], 0),
    ("club-wall-left", "Left wall", "decor", [], [3, 240, 0], 0),
    ("club-wall-right", "Right wall", "decor", [], [573, 240, 0], 0),
    ("club-ceiling", "Ceiling", "decor", [], [288, 240, 186], 0),
    ("club-entrance-doors", "Entrance", "decor", ["the entrance", "the door"], [500, 477, 0], 0),

    # --- bar (front wall) ---
    ("club-back-bar-cabinet", "Back bar", "decor", ["the back bar"], [220, 468, 0], 0),
    ("club-bar-counter", "Bar", "bar", ["the bar", "bar counter"], [220, 420, 0], 0),

    # --- DJ booth (back wall) ---
    ("club-dj-foam-panel", "DJ acoustic foam", "decor", ["the foam"], [288, 8, 0], 0),
    ("club-dj-stage-deck", "DJ stage deck", "stage", ["the stage"], [288, 54, 0], 0),
    ("club-dj-console", "DJ console", "dj", ["the booth", "dj", "dj booth"], [288, 45, DECK_Z], 0),
    ("club-speaker-stack", "Speaker stack L", "decor", [], [190, 45, 0], 0),
    ("club-speaker-stack", "Speaker stack R", "decor", [], [386, 45, 0], 0),

    # --- dance floor ---
    ("club-dance-floor", "Dance floor", "floor_area", ["the floor"], [FLOOR_CENTRE[0], FLOOR_CENTRE[1], 0], 0),
    ("club-point-marker", "Dance floor centre", "point",
     ["centre of the dance floor", "middle of the floor"], FLOOR_CENTRE, 0),

    # --- truss rig: 4 edges of the main grid + a centre cross + a front line ---
    ("club-truss-288", "Truss - back edge", "decor", ["the truss"], [TRUSS_CX, TRUSS_Y0, TRUSS_Z], 0),
    ("club-truss-288", "Truss - front edge", "decor", ["the truss"], [TRUSS_CX, TRUSS_Y1, TRUSS_Z], 0),
    ("club-truss-192", "Truss - left edge", "decor", ["the truss"], [TRUSS_X0, TRUSS_CY, TRUSS_Z], 90),
    ("club-truss-192", "Truss - right edge", "decor", ["the truss"], [TRUSS_X1, TRUSS_CY, TRUSS_Z], 90),
    ("club-truss-288", "Truss - centre cross X", "decor", ["the truss"], [TRUSS_CX, TRUSS_CY, TRUSS_Z], 0),
    ("club-truss-192", "Truss - centre cross Y", "decor", ["the truss"], [TRUSS_CX, TRUSS_CY, TRUSS_Z], 90),
    ("club-truss-288", "Truss - front line", "decor", ["the truss"], [TRUSS_CX, FRONT_TRUSS_Y, TRUSS_Z], 0),
]

# VIP booths: 2 per side wall, sofa + coffee table.
for _i, (_x_wall, _rz, _sofa) in enumerate([(60, 90, "club-vip-sofa-3"), (60, 90, "club-vip-sofa-2"),
                                             (516, -90, "club-vip-sofa-3"), (516, -90, "club-vip-sofa-2")]):
    _y = 140 if _i % 2 == 0 else 330
    _side = "L" if _x_wall < 288 else "R"
    OBJECTS.append((_sofa, f"VIP booth {_side} {_i % 2 + 1}", "booth", ["vip", "the vip booths"],
                     [_x_wall, _y, 0], _rz))
    _table_x = _x_wall + 50 if _x_wall < 288 else _x_wall - 50
    OBJECTS.append(("club-vip-table", f"VIP table {_side} {_i % 2 + 1}", "decor", [],
                     [_table_x, _y, 0], 0))

# Cocktail tables between the bar and the dance floor, 2 stools each. Each
# stool carries an explicit 7th tuple field (parent anchor NAME) - the bar's
# front row of stools sits only ~6-14in from these, close enough that
# nearest-object-by-distance alone mismatches a stool to the wrong piece of
# furniture (confirmed live); an explicit link removes the ambiguity.
for _i, _tx in enumerate([140, 230, 326, 416]):
    _ty = 380
    _table_name = f"Cocktail table {_i + 1}"
    OBJECTS.append(("club-cocktail-table", _table_name, "table", ["the tables"], [_tx, _ty, 0], 0))
    OBJECTS.append(("club-cocktail-stool", f"Cocktail stool {_i + 1}a", "seating", [], [_tx, _ty - 22, 0], 0, _table_name))
    OBJECTS.append(("club-cocktail-stool", f"Cocktail stool {_i + 1}b", "seating", [], [_tx, _ty + 22, 0], 0, _table_name))

# Bar stools in front of the counter (parent-linked to "Bar", same reason as above).
for _i, _sx in enumerate([60, 124, 188, 252, 316, 380]):
    OBJECTS.append(("club-bar-stool", f"Bar stool {_i + 1}", "seating", [], [_sx, 396, 0], 0, "Bar"))

# Decor: potted plants in the 4 corners, pendant lamps over the bar.
for _cx, _cy in [(30, 30), (546, 30), (30, 450), (546, 450)]:
    OBJECTS.append(("club-potted-plant", "Potted plant", "decor", [], [_cx, _cy, 0], 0))
for _lx in [100, 220, 340]:
    OBJECTS.append(("club-pendant-lamp", "Pendant lamp", "decor", [], [_lx, 440, 132.6], 0))

# --------------------------------------------------------------- fixtures ---
# Fixtures not on the 2D Monitor map: (x, y, z, hang, rot) in inches/degrees, by fixture ID.
EXTRA_PLACES = {
    16: (20, 216, 0, "floor", [0, 0, 90]),     # VPAR Left Column (uplight, left wall)
    17: (556, 216, 0, "floor", [0, 0, -90]),   # VPAR Right Column (uplight, right wall)
    28: (250, 20, DECK_Z, "floor", [0, 0, 0]),  # VPAR stage large truss floor (on the DJ deck)
    29: (326, 20, DECK_Z, "floor", [0, 0, 0]),  # VPAR stage small truss floor
    18: (250, 222, TRUSS_Z, "hung", [0, 0, 0]),   # Center Truss Washes
    19: (218, TRUSS_Y1, 0, "floor", [0, 0, 0]),   # Tetra bar #1 (dance-floor front edge)
    20: (358, TRUSS_Y1, 0, "floor", [0, 0, 0]),   # Tetra bar #2
    21: (566, 410, 96, "wall", [0, 0, -90]),      # South East LED wall (right wall, bar side)
    22: (566, 60, 96, "wall", [0, 0, -90]),       # North East LED wall (right wall, DJ side)
    32: (10, 60, 96, "wall", [0, 0, 90]),         # North West LED wall (left wall, DJ side)
    23: (288, 160, TRUSS_Z, "hung", [0, 0, 0]),   # Middle truss white panel #1
    24: (240, 222, TRUSS_Z, "hung", [0, 0, 0]),   # Middle truss white panel #2
    25: (336, 222, TRUSS_Z, "hung", [0, 0, 0]),   # Middle truss white panel #3
    26: (TRUSS_X0, TRUSS_Y0, TRUSS_Z, "hung", [0, 0, 0]),  # Ceiling truss swarms (truss corner)
    30: (288, 60, 150, "hung", [0, 0, 0]),        # Stage large truss swarms (over the DJ booth)
    27: (10, 410, 96, "wall", [0, 0, 90]),        # Female bathroom RGB (left wall, bar side)
    31: (288, 222, 182, "hung", [0, 0, 0]),       # Revolver wash (roof, highest point)
    33: (200, 45, 0, "floor", [0, 0, 0]),         # FOG (DJ booth side)
}


def _evenly_spaced(lo, hi, n):
    step = (hi - lo) / n
    return [lo + (i + 0.5) * step for i in range(n)]


def _truss_fixture_positions(ws: Workspace):
    """Assigns the 18 monitor-mapped moving heads to the truss grid, ordered left-to-right
    by their 2D Monitor X (per the club-layout spec), spots split across the back/front
    edges and washes on the left/right edges."""
    monitor = ws.monitor_items()
    beam_ids, wash_ids = [], []
    for p in ws.fixtures():
        if p.id not in monitor:
            continue
        if "BEAM230" in p.model:
            beam_ids.append(p.id)
        elif p.model == "WASH":
            wash_ids.append(p.id)
    beam_ids.sort(key=lambda i: monitor[i]["x"])
    wash_ids.sort(key=lambda i: monitor[i]["x"])

    placed = {}
    back, front = beam_ids[:7], beam_ids[7:]
    xs = _evenly_spaced(TRUSS_X0, TRUSS_X1, 7)
    for fid, x in zip(back, xs):
        placed[fid] = ([x, TRUSS_Y0, TRUSS_Z], "hung")
    for fid, x in zip(front, xs):
        placed[fid] = ([x, TRUSS_Y1, TRUSS_Z], "hung")

    left, right = wash_ids[:2], wash_ids[2:]
    ys = _evenly_spaced(TRUSS_Y0, TRUSS_Y1, 2)
    for fid, y in zip(left, ys):
        placed[fid] = ([TRUSS_X0, y, TRUSS_Z], "hung")
    for fid, y in zip(right, ys):
        placed[fid] = ([TRUSS_X1, y, TRUSS_Z], "hung")
    return placed


def build_stage(project: Path) -> dict:
    ws = Workspace.load(project)
    truss_places = _truss_fixture_positions(ws)

    fixtures = {}
    for p in ws.fixtures():
        key = str(p.id)
        entry = {"model": f"{p.manufacturer}/{p.model}", "rot": [0, 0, 0], "invertPan": False,
                  "invertTilt": False, "panOffset": 0, "tiltOffset": 0, "look": None}
        if p.id in truss_places:
            pos, hang = truss_places[p.id]
            entry.update(pos=_r16_list(pos), hang=hang)
        elif p.id in EXTRA_PLACES:
            x, y, z, hang, rot = EXTRA_PLACES[p.id]
            entry.update(pos=_r16_list([x, y, z]), hang=hang, rot=list(rot))
        else:
            continue  # left in the Unplaced tray
        fixtures[key] = entry

    objects = []
    for i, entry in enumerate(OBJECTS):
        prop_id, name, category, aliases, pos, rz = entry[:6]
        parent = entry[6] if len(entry) > 6 else None
        obj = {"id": f"o{i + 1}", "prop": prop_id, "name": name, "category": category,
               "aliases": aliases, "pos": _r16_list(pos), "rz": rz, "scale": [1, 1, 1], "color": None}
        if parent:
            obj["parent"] = parent
        objects.append(obj)

    used_props = {o["prop"] for o in objects}
    prop_defs = {pid: PROP_DEFS[pid] for pid in used_props}

    return {"version": 1, "units": "in", "room": ROOM, "anchor": _r16_list(FLOOR_CENTRE),
            "fixtures": fixtures, "models": {}, "objects": objects, "propDefs": prop_defs}


# ------------------------------------------------------------------ validate --
# Approximate unrotated (width, depth, height) footprints in inches for the overlap check,
# keyed by prop id. Height matters: the DJ console sits ON TOP of the stage deck, so a
# real 3D check (not just XY) is needed to avoid flagging that as a bad overlap.
_FOOTPRINT = {
    "club-back-bar-cabinet": (96.1, 20.5, 26.8), "club-bar-counter": (360, 24, 42),
    "club-dj-stage-deck": (144, 72, 24), "club-dj-console": (100, 20, 40),
    "club-dance-floor": (240, 192, 2),
    "club-vip-sofa-3": (107.5, 36.4, 44.0), "club-vip-sofa-2": (71.1, 32.2, 27.9),
    "club-cocktail-table": (15.1, 15.1, 30.0),
}
_AABB_CATEGORIES = {"table", "booth", "bar", "dj", "stage", "floor_area"}


def _aabb(obj):
    fw, fd, fh = _FOOTPRINT.get(obj["prop"], (24, 24, 24))
    if abs(obj["rz"]) in (90, 270):
        fw, fd = fd, fw
    x, y, z = obj["pos"]
    return (x - fw / 2, x + fw / 2, y - fd / 2, y + fd / 2, z, z + fh)


def validate(stage: dict, ws: Workspace) -> list:
    """Returns a list of problem strings (empty = clean)."""
    problems = []
    room = stage["room"]

    for o in stage["objects"]:
        if o["prop"] not in stage["propDefs"]:
            problems.append(f"object {o['id']} ({o['name']}) uses undefined prop {o['prop']}")
        x, y, z = o["pos"]
        if not (0 <= x <= room["width"] and 0 <= y <= room["depth"] and 0 <= z <= room["height"]):
            problems.append(f"object {o['id']} ({o['name']}) at {o['pos']} is outside the room")

    all_ids = {str(p.id) for p in ws.fixtures()}
    placed_ids = set(stage["fixtures"].keys())
    missing = all_ids - placed_ids
    if missing:
        problems.append(f"{len(missing)} fixture(s) not placed: {sorted(missing, key=int)}")
    for fid, fx in stage["fixtures"].items():
        x, y, z = fx["pos"]
        if not (0 <= x <= room["width"] and 0 <= y <= room["depth"] and 0 <= z <= room["height"]):
            problems.append(f"fixture {fid} at {fx['pos']} is outside the room")

    aabb_objs = [o for o in stage["objects"] if o["category"] in _AABB_CATEGORIES]
    for i in range(len(aabb_objs)):
        for j in range(i + 1, len(aabb_objs)):
            a, b = _aabb(aabb_objs[i]), _aabb(aabb_objs[j])
            ox = min(a[1], b[1]) - max(a[0], b[0])
            oy = min(a[3], b[3]) - max(a[2], b[2])
            oz = min(a[5], b[5]) - max(a[4], b[4])
            if ox > 6 and oy > 6 and oz > 6:  # allow a few inches of touching/rounding slack
                problems.append(f"overlap: {aabb_objs[i]['name']} <-> {aabb_objs[j]['name']} "
                                 f"(overlap {ox:.0f}x{oy:.0f}x{oz:.0f}in)")
    return problems


if __name__ == "__main__":
    src = REPO / "SaveFile" / "Main Project.qxw"
    stage = build_stage(src)
    ws = Workspace.load(src)
    problems = validate(stage, ws)
    counts = {}
    for o in stage["objects"]:
        counts[o["category"]] = counts.get(o["category"], 0) + 1
    print("objects by category:", counts)
    print("fixtures placed:", len(stage["fixtures"]), "/", len(list(ws.fixtures())))
    print("propDefs embedded:", len(stage["propDefs"]))
    if problems:
        print(f"VALIDATION: {len(problems)} problem(s):")
        for p in problems:
            print(" -", p)
    else:
        print("VALIDATION: clean")
