"""The room's 3D stage (<show>.stage.json) edited by voice: move, place, rotate, add and remove fixtures and objects.

Conventions are the 3D stage's own (.claude/memory/stage-visualizer.md): inches; the origin is a floor corner; +X is the
DJ's right hand looking into the room (the default camera's screen-right); +Y runs from the DJ's back wall towards the
bar and the entrance; +Z is up. The editor shows lengths in feet-inches and positions relative to the anchor (the
dance-floor centre), so spoken coordinates are read that way and summaries are written that way. Objects are drawn
from the file's own `propDefs`: a new kind of object brings its prop definition along. A stool or chair names its
table in `parent` (the table's name).

Everything here works on a copy of the document and returns changes, each with the value it replaces: the executor
refuses them when the scene moved on since planning (an edit in the browser), and undo is exact. The executor saves
through QLC+ (saveStage: the file is backed up and every open /stage page reloads) or straight to the file."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class SceneError(ValueError):
    """A scene edit that can't be planned or applied (unknown thing, stale scene, off the room)."""


# ----------------------------------------------------------------------------------------------------- file

def read_doc(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_doc(path: Path, doc: dict) -> None:
    """Atomic write: never a half-written stage file."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def backup(path: Path, data_dir: Path, keep: int = 30) -> Optional[Path]:
    """lightai's own copies (QLC+ keeps only one .bak): data_dir/stage-backups/<stem>.<time>.json."""
    path = Path(path)
    if not path.exists():
        return None
    d = Path(data_dir) / "stage-backups"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{path.stem}.{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.json"
    dest.write_bytes(path.read_bytes())
    old = sorted(d.glob(f"{path.stem}.*.json"))
    for f in old[:-keep]:
        f.unlink(missing_ok=True)
    return dest


# ----------------------------------------------------------------------------------------------------- units

def fmt_len(inches: float) -> str:
    """Feet-inches like the editor shows them, to the half inch: 4' 6", -2' 0", 9 1/2"."""
    sign = "-" if inches < -0.24 else ""
    half = round(abs(inches) * 2) / 2
    ft, rest = divmod(half, 12)
    whole, frac = int(rest), rest - int(rest)
    inch = f"{whole}{' 1/2' if frac else ''}\""
    return f"{sign}{int(ft)}' {inch}" if ft else f"{sign}{inch}"


def fmt_pos(pos: list, anchor: list, z: bool = False) -> str:
    """A position the way the editor shows it: relative to the anchor, feet-inches."""
    s = f"x {fmt_len(pos[0] - anchor[0])}, y {fmt_len(pos[1] - anchor[1])}"
    return s + (f", height {fmt_len(pos[2])}" if z else "")


# ----------------------------------------------------------------------------------------------------- geometry

@dataclass
class Box:
    x0: float
    x1: float
    y0: float
    y1: float
    z0: float = 0.0
    z1: float = 0.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def d(self) -> float:
        return self.y1 - self.y0

    def moved(self, dx: float, dy: float) -> "Box":
        return Box(self.x0 + dx, self.x1 + dx, self.y0 + dy, self.y1 + dy, self.z0, self.z1)

    def overlaps(self, o: "Box", gap: float = 0.0) -> bool:
        return self.x0 < o.x1 + gap and o.x0 < self.x1 + gap and self.y0 < o.y1 + gap and o.y0 < self.y1 + gap

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


def _part_extent(part: dict) -> tuple:
    """(x0, x1, y0, y1, z0, z1) of one prop part in the prop's frame. Primitives are centred on `pos`; a model part
    stands on it (stage-props.js fitModelAndBaseAlign). Missing sizes fall back like the renderer does."""
    shape = part.get("shape") or "box"
    size = list(part.get("size") or ([24, 24, 36] if shape == "model" else [12, 12, 12]))
    w, d, h = (list(size) + [12, 12, 12])[:3]
    px, py, pz = (list(part.get("pos") or [0, 0, 0]) + [0, 0, 0])[:3]
    z0, z1 = (pz, pz + h) if shape == "model" else (pz - h / 2, pz + h / 2)
    return px - w / 2, px + w / 2, py - d / 2, py + d / 2, z0, z1


def prop_extent(doc: dict, prop: str) -> tuple:
    parts = ((doc.get("propDefs") or {}).get(prop) or {}).get("parts") or []
    if not parts:
        return -12.0, 12.0, -12.0, 12.0, 0.0, 36.0
    ex = [_part_extent(p) for p in parts]
    return (min(e[0] for e in ex), max(e[1] for e in ex), min(e[2] for e in ex), max(e[3] for e in ex),
            min(e[4] for e in ex), max(e[5] for e in ex))


def yaw_of(obj: dict) -> float:
    rot = obj.get("rot")
    if isinstance(rot, list) and len(rot) >= 3:
        return float(rot[2] or 0.0)
    return float(obj.get("rz") or 0.0)


def footprint(doc: dict, obj: dict) -> Box:
    """The object's box in the room (its prop's parts, scaled, turned by its yaw, moved to its position)."""
    x0, x1, y0, y1, z0, z1 = prop_extent(doc, obj.get("prop") or "")
    sx, sy, sz = (list(obj.get("scale") or [1, 1, 1]) + [1, 1, 1])[:3]
    x0, x1, y0, y1, z0, z1 = x0 * sx, x1 * sx, y0 * sy, y1 * sy, z0 * sz, z1 * sz
    a = math.radians(yaw_of(obj))
    c, s = math.cos(a), math.sin(a)
    xs, ys = [], []
    for x in (x0, x1):
        for y in (y0, y1):
            xs.append(x * c - y * s)
            ys.append(x * s + y * c)
    px, py, pz = (list(obj.get("pos") or [0, 0, 0]) + [0, 0, 0])[:3]
    return Box(px + min(xs), px + max(xs), py + min(ys), py + max(ys), pz + z0, pz + z1)


def fixture_box(entry: dict) -> Box:
    px, py, pz = (list(entry.get("pos") or [0, 0, 0]) + [0, 0, 0])[:3]
    return Box(px - 8, px + 8, py - 8, py + 8, pz - 8, pz + 8)


# ----------------------------------------------------------------------------------------------------- words

def _norm(text: str) -> list:
    """Lowercase tokens, 'the/a/its' dropped, letter-digit runs split ('1a' -> '1 a', 'l1' -> 'l 1'), plurals singular."""
    t = re.sub(r"(\d)([a-z])", r"\1 \2", re.sub(r"([a-z])(\d)", r"\1 \2", (text or "").lower()))
    out = []
    for w in re.findall(r"[a-z]+|\d+", t):
        if w in ("the", "a", "an", "its", "their", "his", "her", "of", "my", "our", "that", "this", "those", "these"):
            continue
        out.append(_sing(w))
    return out


def _sing(w: str) -> str:
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and re.search(r"(ch|sh|x|ss)es$", w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _plural(text: str) -> bool:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return any(w != _sing(w) for w in words) or bool(re.search(r"\b(all|every|both)\b", (text or "").lower()))


SYNONYMS = {"couch": "sofa", "settee": "sofa", "lounge": "sofa", "seat": "chair", "sub": "speaker", "subwoofer": "speaker",
            "monitor": "speaker", "column": "pillar", "post": "pillar", "desk": "console", "hightop": "high"}
SEATING = re.compile(r"stool|chair|seat|sofa|bench|couch")


# ----------------------------------------------------------------------------------------------------- directions

AUDIENCE_VIEW = re.compile(r"\b(?:from|seen from|looking from|facing) (?:the )?(?:dance ?floor|floor|bar|entrance|door|crowd|audience|"
                           r"room|vip|tables?)\b|\bhouse (?:left|right)\b")


def viewpoint(text: str) -> str:
    """'stage' (the DJ looking into the room: the 3D view's default camera) unless the words say the other side."""
    return "audience" if AUDIENCE_VIEW.search((text or "").lower()) else "stage"


def direction_vector(words: str, text: str = "") -> Optional[tuple]:
    """A unit step in room axes for spoken direction words. Left/right follow the viewpoint (stage by default); back is
    towards the DJ's back wall, front towards the bar and entrance; up/raise is higher."""
    w = (words or "").lower()
    if re.search(r"\bstage right\b", w):
        return (1.0, 0.0, 0.0)
    if re.search(r"\bstage left\b", w):
        return (-1.0, 0.0, 0.0)
    if re.search(r"\bhouse right\b", w):
        return (-1.0, 0.0, 0.0)
    if re.search(r"\bhouse left\b", w):
        return (1.0, 0.0, 0.0)
    sign = -1.0 if viewpoint(text or w) == "audience" else 1.0
    if re.search(r"\b(?:right|east)\b", w):
        return (sign, 0.0, 0.0)
    if re.search(r"\b(?:left|west)\b", w):
        return (-sign, 0.0, 0.0)
    if re.search(r"\b(?:raise|raised|higher|lift|up|upwards?)\b", w):
        return (0.0, 0.0, 1.0)
    if re.search(r"\b(?:lower|lowered|down|downwards?|drop)\b", w):
        return (0.0, 0.0, -1.0)
    if re.search(r"\b(?:back|backwards?|upstage|behind)\b", w):
        return (0.0, -1.0, 0.0)
    if re.search(r"\b(?:forwards?|front|downstage|ahead)\b", w):
        return (0.0, 1.0, 0.0)
    return None


def direction_words(vec: tuple, view: str) -> str:
    x, y, z = vec
    if abs(z) > max(abs(x), abs(y)):
        return "up" if z > 0 else "down"
    if abs(x) >= abs(y):
        right = x > 0 if view == "stage" else x < 0
        return ("right" if right else "left") + (" (seen from the DJ booth)" if view == "stage" else " (seen from the dance floor)")
    return "towards the bar and entrance" if y > 0 else "back towards the DJ"


# ----------------------------------------------------------------------------------------------------- the scene

@dataclass
class Ref:
    kind: str  # "object" | "fixture"
    id: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"


class Scene:
    """One stage document with lookups; changes are planned against it and applied by apply_changes."""

    def __init__(self, doc: dict, rig=None) -> None:
        self.doc = doc
        self.rig = rig
        self.room = doc.get("room") or {"width": 480, "depth": 432, "height": 168}
        self.anchor = (list(doc.get("anchor") or [0, 0, 0]) + [0, 0, 0])[:3]

    # ---- lookups
    @property
    def objects(self) -> list:
        return self.doc.setdefault("objects", [])

    @property
    def fixtures(self) -> dict:
        return self.doc.setdefault("fixtures", {})

    def obj(self, oid: str) -> Optional[dict]:
        return next((o for o in self.objects if str(o.get("id")) == str(oid)), None)

    def name(self, ref: Ref) -> str:
        if ref.kind == "object":
            o = self.obj(ref.id) or {}
            return o.get("name") or o.get("prop") or ref.id
        fx = getattr(self.rig, "fixtures", {}).get(int(ref.id)) if self.rig is not None else None
        return f"fixture {ref.id}" + (f" ({fx.name})" if fx is not None and getattr(fx, "name", "") else "")

    def pos(self, ref: Ref) -> list:
        e = self.obj(ref.id) if ref.kind == "object" else self.fixtures.get(ref.id)
        return [float(v) for v in (list((e or {}).get("pos") or [0, 0, 0]) + [0, 0, 0])[:3]]

    def box(self, ref: Ref) -> Box:
        if ref.kind == "object":
            return footprint(self.doc, self.obj(ref.id) or {})
        return fixture_box(self.fixtures.get(ref.id) or {})

    def _words(self, o: dict) -> set:
        text = " ".join([o.get("name") or "", *(o.get("aliases") or []), (o.get("prop") or "").replace("-", " ").replace("club ", ""),
                         o.get("category") or ""])
        ws = set(_norm(text))
        return ws | {SYNONYMS[w] for w in ws if w in SYNONYMS}

    def find(self, phrase: str) -> tuple:
        """Objects a phrase names: ([ids], [candidates when it could be several different things]).
        'table 3', 'cocktail stool 1a', 'the bar', 'the stools', 'vip booth l1'. Numbers must match exactly; a plural
        (or 'all') takes every match; a singular with several equally good matches is ambiguous."""
        p = (phrase or "").lower().strip()
        plain = " ".join(_norm(p))
        if not plain:
            return [], []
        exact = [o for o in self.objects if plain in {" ".join(_norm(o.get("name") or "")), *(" ".join(_norm(a)) for a in o.get("aliases") or [])}]
        if exact:
            if len(exact) == 1 or _plural(p):
                return [str(o["id"]) for o in exact], []
        want = [SYNONYMS.get(w, w) for w in _norm(p)]
        nums = [w for w in want if w.isdigit() or len(w) == 1]
        scored = []
        for o in self.objects:
            ws = self._words(o)
            if not all(w in ws for w in want):
                continue
            own = _norm(o.get("name") or "")
            if [w for w in own if w.isdigit() or len(w) == 1] and nums and not all(n in own for n in nums):
                continue
            extra = len([w for w in own if w not in want])
            scored.append((extra, str(o["id"])))
        if not scored:
            return [], []
        if _plural(p) and not nums:
            return [oid for _, oid in scored], []
        best = min(e for e, _ in scored)
        top = [oid for e, oid in scored if e == best]
        if len(top) == 1:
            return top, []
        return [], top

    def children(self, oid: str) -> list:
        """Stools and chairs that belong to a table: their `parent` names it, else unparented seats right next to it."""
        o = self.obj(oid)
        if o is None:
            return []
        name = (o.get("name") or "").lower()
        kids = [str(x["id"]) for x in self.objects if str(x.get("id")) != str(oid) and (x.get("parent") or "").lower() == name and name]
        if kids:
            return kids
        b = footprint(self.doc, o)
        reach = max(b.w, b.d) / 2 + 30
        return [str(x["id"]) for x in self.objects
                if str(x.get("id")) != str(oid) and not x.get("parent") and SEATING.search(f"{x.get('name', '')} {x.get('prop', '')}".lower())
                and math.hypot(footprint(self.doc, x).cx - b.cx, footprint(self.doc, x).cy - b.cy) <= reach]

    def ref_box(self, phrase: str) -> tuple:
        """Where a place phrase points: (Box, label) from the scene's objects, the 3D stage's named places, or fixtures."""
        ids, cands = self.find(phrase)
        if not ids and cands:
            ids = cands  # 'next to the vip booths': any of them will do as a reference
        if ids:
            boxes = [footprint(self.doc, self.obj(i)) for i in ids]
            box = Box(min(b.x0 for b in boxes), max(b.x1 for b in boxes), min(b.y0 for b in boxes), max(b.y1 for b in boxes),
                      min(b.z0 for b in boxes), max(b.z1 for b in boxes))
            label = (self.obj(ids[0]) or {}).get("name") or phrase
            return box, label if len(ids) == 1 else f"the {phrase.strip()}"
        stage = getattr(self.rig, "stage", None) if self.rig is not None else None
        if stage is not None:
            try:
                pt = stage.place(phrase)
            except Exception:  # noqa: BLE001 - an unknown place is just no answer here
                pt = None
            if pt is not None:
                x, y = float(pt[0]), float(pt[1])
                return Box(x - 12, x + 12, y - 12, y + 12, 0, 0), phrase.strip()
        if self.rig is not None:
            from lightai.nlu.normalize import normalize_slot

            v = normalize_slot(self.rig, "target", phrase)
            fids = [str(f) for f in v.get("fixture_ids") or [] if str(f) in self.fixtures]
            if fids and not v.get("unresolved"):
                boxes = [fixture_box(self.fixtures[f]) for f in fids]
                return Box(min(b.x0 for b in boxes), max(b.x1 for b in boxes), min(b.y0 for b in boxes), max(b.y1 for b in boxes),
                           0, 0), phrase.strip()
        raise SceneError(f"I can't find '{phrase}' in the 3D stage")

    # ---- space
    def obstacles(self, exclude: set) -> list:
        """Boxes a new or moved thing on the floor must not overlap: furniture and fixtures standing on the floor
        (walls are the room's bounds; overhead things, areas and markers don't block)."""
        out = []
        for o in self.objects:
            if str(o.get("id")) in exclude:
                continue
            cat = (o.get("category") or "").lower()
            nm = f"{o.get('name', '')} {o.get('prop', '')}".lower()
            if cat in ("floor_area", "point") or re.search(r"wall|ceiling|foam|truss|pendant|marker", nm):
                continue
            b = footprint(self.doc, o)
            if b.z0 > 60:  # hanging things
                continue
            out.append(b)
        return out

    def support_z(self, x: float, y: float, exclude: set) -> float:
        """The top of a platform (stage deck, riser) under a spot, else the floor."""
        top = 0.0
        for o in self.objects:
            if str(o.get("id")) in exclude:
                continue
            cat = (o.get("category") or "").lower()
            nm = f"{o.get('name', '')} {o.get('prop', '')}".lower()
            if cat == "stage" or re.search(r"\bdeck\b|riser|platform", nm):
                b = footprint(self.doc, o)
                if b.contains(x, y) and b.z1 < 60:
                    top = max(top, b.z1)
        return top

    def inside(self, b: Box, margin: float = 6.0) -> bool:
        return (b.x0 >= margin and b.y0 >= margin and b.x1 <= float(self.room.get("width", 480)) - margin
                and b.y1 <= float(self.room.get("depth", 432)) - margin)

    def free(self, b: Box, exclude: set, avoid_floor: bool = True, gap: float = 3.0) -> bool:
        if not self.inside(b):
            return False
        if any(b.overlaps(o, gap) for o in self.obstacles(exclude)):
            return False
        if avoid_floor:
            for o in self.objects:
                if (o.get("category") or "").lower() == "floor_area" and str(o.get("id")) not in exclude and b.overlaps(footprint(self.doc, o)):
                    return False
        return True

    def spot(self, ref: Box, relation: str, half_w: float, half_d: float, exclude: set, avoid_floor: bool = True) -> tuple:
        """A free centre (x, y) for something half_w x half_d big, placed `relation` to the ref box; (x, y, side)."""
        gap = 12.0
        sides = {"right_of": ["right"], "left_of": ["left"], "in_front_of": ["front"], "behind": ["back"]}.get(
            relation, ["right", "left", "front", "back"])
        view_right = 1.0  # stage view: the room's +X is 'right'
        for extra in range(0, 97, 12):
            for side in sides:
                for slide in (0, 12, -12, 24, -24, 36, -36, 48, -48):
                    if side in ("right", "left"):
                        sx = 1.0 if (side == "right") == (view_right > 0) else -1.0
                        x = (ref.x1 + gap + extra + half_w) if sx > 0 else (ref.x0 - gap - extra - half_w)
                        y = ref.cy + slide
                    else:
                        sy = 1.0 if side == "front" else -1.0
                        y = (ref.y1 + gap + extra + half_d) if sy > 0 else (ref.y0 - gap - extra - half_d)
                        x = ref.cx + slide
                    b = Box(x - half_w, x + half_w, y - half_d, y + half_d)
                    if self.free(b, exclude, avoid_floor):
                        return x, y, side
        raise SceneError("there's no free room there; say where exactly (e.g. 'at 5 by 5 feet')")

    def spot_at(self, x: float, y: float, half_w: float, half_d: float, exclude: set, avoid_floor: bool = False) -> tuple:
        """The free centre nearest to (x, y), spiralling out."""
        for r in range(0, 121, 6):
            for k in range(max(1, r // 3)):
                a = 2 * math.pi * k / max(1, r // 3)
                cx, cy = x + r * math.cos(a), y + r * math.sin(a)
                if self.free(Box(cx - half_w, cx + half_w, cy - half_d, cy + half_d), exclude, avoid_floor):
                    return cx, cy
        raise SceneError("there's no free room near there")


# ----------------------------------------------------------------------------------------------------- changes

def ch_set(ref: Ref, field: str, value, before) -> dict:
    return {"op": "set", "kind": ref.kind, "id": ref.id, "field": field, "value": value, "before": before}


def _r(v) -> list:
    return [round(float(x), 3) for x in v]


def move_changes(scene: Scene, refs: list, delta: tuple) -> list:
    out = []
    for ref in refs:
        cur = scene.pos(ref)
        new = [cur[0] + delta[0], cur[1] + delta[1], max(0.0, cur[2] + delta[2])]
        out.append(ch_set(ref, "pos", _r(new), _r(cur)))
    return out


def rotate_changes(scene: Scene, refs: list, yaw: float, pivot: Optional[tuple] = None) -> list:
    """Turn by `yaw` degrees (positive = counter-clockwise seen from above, the file's sense); with a pivot, the things
    also swing around it (a table turning with its stools)."""
    out = []
    a = math.radians(yaw)
    for ref in refs:
        if ref.kind == "object":
            o = scene.obj(ref.id) or {}
            rot = list(o.get("rot") or [0, 0, o.get("rz") or 0])
            rot = (rot + [0, 0, 0])[:3]
            before = list(o.get("rot")) if isinstance(o.get("rot"), list) else None
            new = [rot[0], rot[1], round(((float(rot[2]) + yaw + 180) % 360) - 180, 3)]
            out.append(ch_set(ref, "rot", new, before if before is not None else {"rz": o.get("rz", 0)}))
        else:
            e = scene.fixtures.get(ref.id) or {}
            rot = (list(e.get("rot") or [0, 0, 0]) + [0, 0, 0])[:3]
            new = [rot[0], rot[1], round(((float(rot[2]) + yaw + 180) % 360) - 180, 3)]
            out.append(ch_set(ref, "rot", new, list(e.get("rot") or [0, 0, 0])))
        if pivot is not None:
            p = scene.pos(ref)
            dx, dy = p[0] - pivot[0], p[1] - pivot[1]
            nx, ny = pivot[0] + dx * math.cos(a) - dy * math.sin(a), pivot[1] + dx * math.sin(a) + dy * math.cos(a)
            if abs(nx - p[0]) > 1e-6 or abs(ny - p[1]) > 1e-6:
                out.append(ch_set(ref, "pos", _r([nx, ny, p[2]]), _r(p)))
    return out


def hang_changes(scene: Scene, refs: list, hang: str) -> list:
    return [ch_set(r, "hang", hang, (scene.fixtures.get(r.id) or {}).get("hang", "floor"))
            for r in refs if r.kind == "fixture" and (scene.fixtures.get(r.id) or {}).get("hang", "floor") != hang]


def remove_changes(scene: Scene, ids: list) -> list:
    return [{"op": "remove", "id": str(i), "before": copy.deepcopy(scene.obj(i))} for i in ids if scene.obj(i) is not None]


def _close(a, b) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 0.01
    return a == b


def apply_changes(doc: dict, changes: list) -> dict:
    """A new document with the changes made; SceneError when something no longer is what the plan saw."""
    new = copy.deepcopy(doc)
    objects = new.setdefault("objects", [])
    fixtures = new.setdefault("fixtures", {})
    props = new.setdefault("propDefs", {})

    def find(oid):
        return next((o for o in objects if str(o.get("id")) == str(oid)), None)

    for ch in changes:
        op = ch["op"]
        if op == "set":
            ent = find(ch["id"]) if ch["kind"] == "object" else fixtures.get(str(ch["id"]))
            if ent is None:
                if ch["kind"] == "fixture" and ch["field"] == "entry":
                    fixtures[str(ch["id"])] = copy.deepcopy(ch["value"])
                    continue
                raise SceneError(f"{ch['kind']} {ch['id']} is no longer in the 3D stage")
            field, before = ch["field"], ch.get("before")
            if field == "entry":  # a whole fixture entry (placing a fixture the stage didn't have yet, and its undo)
                if ch["value"] is None:
                    fixtures.pop(str(ch["id"]), None)
                else:
                    fixtures[str(ch["id"])] = copy.deepcopy(ch["value"])
                continue
            if isinstance(before, dict) and "rz" in before:  # an object that only had the legacy yaw
                if not _close(float(ent.get("rz") or 0), float(before["rz"] or 0)) or isinstance(ent.get("rot"), list):
                    raise SceneError(f"{ent.get('name') or ch['id']} was turned since this was planned")
            elif before is not None and not _close(ent.get(field, "floor" if field == "hang" else None), before):
                raise SceneError(f"{ent.get('name') or ch['kind'] + ' ' + str(ch['id'])} changed since this was planned; say it again")
            if isinstance(ch["value"], dict) and "rz" in ch["value"]:  # undo back to the legacy yaw only
                ent.pop("rot", None)
                ent["rz"] = ch["value"]["rz"]
            else:
                ent[field] = copy.deepcopy(ch["value"])
                if ch["kind"] == "object" and field == "rot":
                    ent["rz"] = ch["value"][2]  # the editor keeps both in step
        elif op == "add":
            if find(ch["object"]["id"]) is not None:
                raise SceneError(f"an object {ch['object']['id']} already exists")
            objects.append(copy.deepcopy(ch["object"]))
        elif op == "remove":
            ent = find(ch["id"])
            if ent is None:
                raise SceneError(f"{(ch.get('before') or {}).get('name') or ch['id']} is no longer in the 3D stage")
            if ch.get("before") and not _close(ent.get("pos"), ch["before"].get("pos")):
                raise SceneError(f"{ent.get('name') or ch['id']} moved since this was planned; say it again")
            objects.remove(ent)
        elif op == "add_prop":
            props.setdefault(ch["prop"], copy.deepcopy(ch["def"]))
        elif op == "remove_prop":
            if not any(o.get("prop") == ch["prop"] for o in objects):
                props.pop(ch["prop"], None)
        else:
            raise SceneError(f"unknown scene change {op}")
    return new


def invert(changes: list) -> list:
    """The changes that take these back (in reverse order)."""
    out = []
    for ch in reversed(changes):
        op = ch["op"]
        if op == "set":
            out.append(dict(ch, value=ch.get("before"), before=ch.get("value")))
        elif op == "add":
            out.append({"op": "remove", "id": ch["object"]["id"], "before": ch["object"]})
        elif op == "remove":
            out.append({"op": "add", "object": ch["before"]})
        elif op == "add_prop":
            out.append({"op": "remove_prop", "prop": ch["prop"]})
    return out


# ----------------------------------------------------------------------------------------------------- new objects

def _prim(name: str, category: str, parts: list) -> dict:
    return {"name": name, "category": category, "aliases": [], "parts": parts}


def _box(w, d, h, z, color="#8899aa", material="matte", x=0.0, y=0.0) -> dict:
    return {"shape": "box", "size": [w, d, h], "pos": [x, y, z], "color": color, "material": material}


def _cyl(dia, h, z, color="#333333", material="matte") -> dict:
    return {"shape": "cylinder", "size": [dia, dia, h], "pos": [0, 0, z], "color": color, "material": material}


# kind -> words, the file props to reuse (by prop key / name words), lightai's own primitive prop, seats
CATALOG = {
    "high_top": {"label": "High-top table", "words": r"high[\s-]?tops?|high[\s-]?top tables?|bar tables?|poseur tables?|standing tables?",
                 "reuse": ["high-top", "high top"], "category": "table",
                 "prop": ("lightai-high-top-table", _prim("High-top table", "table", [_cyl(24, 1.5, 41.25, "#5a3a20"), _cyl(3, 40.5, 20.25)]))},
    "cocktail_table": {"label": "Cocktail table", "words": r"cocktail tables?", "reuse": ["cocktail-table", "cocktail table"], "category": "table",
                       "prop": ("lightai-table", _prim("Table", "table", [_box(30, 30, 1.5, 29.25, "#5a3a20"), _cyl(3, 28.5, 14.25)]))},
    "table": {"label": "Table", "words": r"(?:round |square |dining |small |big |)tables?", "reuse": ["lightai-table"], "category": "table",
              "prop": ("lightai-table", _prim("Table", "table", [_box(30, 30, 1.5, 29.25, "#5a3a20"), _cyl(3, 28.5, 14.25)]))},
    "stool": {"label": "Stool", "words": r"(?:bar |cocktail |high |)stools?", "reuse": ["bar-stool", "cocktail-stool", "stool"], "category": "seating",
              "prop": ("lightai-stool", _prim("Stool", "seating", [_cyl(14, 2, 29, "#333333"), _cyl(2, 28, 14, "#555555", "metal")]))},
    "chair": {"label": "Chair", "words": r"chairs?|seats?", "reuse": ["chair"], "category": "seating",
              "prop": ("lightai-chair", _prim("Chair", "seating", [_box(16, 16, 2, 18, "#333333"), _box(16, 2, 20, 29, "#333333", y=7)]))},
    "sofa": {"label": "Sofa", "words": r"couch(?:es)?|sofas?|settees?|lounges?|booths?|benches?", "reuse": ["vip-sofa-2", "sofa"], "category": "booth",
             "prop": ("lightai-sofa", _prim("Sofa", "booth", [_box(72, 32, 16, 8, "#402030"), _box(72, 8, 30, 15, "#402030", y=12)]))},
    "speaker": {"label": "Speaker", "words": r"speakers?|subs?|subwoofers?|monitors?", "reuse": ["speaker"], "category": "speaker",
                "prop": ("lightai-speaker", _prim("Speaker", "speaker", [_box(24, 24, 48, 24, "#0a0a0a")]))},
    "plant": {"label": "Plant", "words": r"(?:potted |)plants?|trees?|palms?", "reuse": ["potted-plant", "plant"], "category": "decor",
              "prop": ("lightai-plant", _prim("Plant", "decor", [_cyl(18, 16, 8, "#6b4b2a"), {"shape": "sphere", "size": [30, 30, 30], "pos": [0, 0, 36], "color": "#2e6b30", "material": "matte"}]))},
    "pillar": {"label": "Pillar", "words": r"pillars?|columns?|posts?", "reuse": ["pillar", "column"], "category": "decor",
               "prop": ("lightai-pillar", _prim("Pillar", "decor", [_cyl(18, 150, 75, "#bbbbbb")]))},
    "podium": {"label": "Podium", "words": r"podiums?|lecterns?", "reuse": ["podium"], "category": "decor",
               "prop": ("lightai-podium", _prim("Podium", "decor", [_box(24, 18, 42, 21, "#222222")]))},
    "riser": {"label": "Riser", "words": r"risers?|platforms?|stage decks?|decks?", "reuse": ["riser", "stage-deck-4x8"], "category": "stage",
              "prop": ("lightai-riser", _prim("Riser", "stage", [_box(96, 48, 24, 12, "#1a1a1a")]))},
    "screen": {"label": "Screen", "words": r"screens?|tvs?|televisions?|led screens?|displays?", "reuse": ["led-screen", "screen"], "category": "decor",
               "prop": ("lightai-screen", _prim("Screen", "decor", [_box(60, 4, 36, 66, "#050505"), _box(4, 4, 48, 24, "#333333")]))},
}
SEAT_KINDS = ("stool", "chair")


def kind_of(word: str) -> Optional[str]:
    w = (word or "").lower().strip()
    for kind, c in CATALOG.items():
        if re.fullmatch(rf"(?:a |an |one |some |)(?:{c['words']})", w):
            return kind
    return None


def prop_for(doc: dict, kind: str) -> tuple:
    """(prop key, change adding its definition or None): the show's own prop when it has one of this kind."""
    c = CATALOG[kind]
    defs = doc.get("propDefs") or {}
    for want in c["reuse"]:
        for key, d in defs.items():
            if want in key.lower() or want in (d.get("name") or "").lower():
                return key, None
    key, d = c["prop"]
    if key in defs:
        return key, None
    return key, {"op": "add_prop", "prop": key, "def": copy.deepcopy(d)}


def next_name(scene: Scene, label: str, taken: set) -> str:
    nums = [int(m.group(1)) for o in scene.objects for m in [re.fullmatch(rf"{re.escape(label)} (\d+)", o.get("name") or "", re.I)] if m]
    n = max(nums, default=0) + 1
    while f"{label} {n}" in taken:
        n += 1
    return f"{label} {n}"


def new_id(scene: Scene, taken: set) -> str:
    base = int(time.time() * 1000)
    i = 0
    while True:
        oid = "o" + _b36(base + i)
        if scene.obj(oid) is None and oid not in taken:
            return oid
        i += 1


def _b36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out or "0"


def kind_half(scene: Scene, kind: str) -> float:
    """Half the width of one thing of this kind (its show prop, or lightai's own shape)."""
    key, add = prop_for(scene.doc, kind)
    ext = prop_extent(scene.doc if add is None else {"propDefs": {key: add["def"]}}, key)
    return max(ext[1] - ext[0], ext[3] - ext[2]) / 2


def layout_group(scene: Scene, items: list) -> tuple:
    """Relative spots for one group of new things, centred on (0, 0): seats in a ring around the first table, the
    other tables in a row beside it; without a table, a row. items: [(kind, count)] -> ([(kind, dx, dy, yaw)], half_w, half_d)"""
    kinds = [k for k, n in items for _ in range(max(1, n))]
    tables = [k for k in kinds if k not in SEAT_KINDS]
    seats = [k for k in kinds if k in SEAT_KINDS]
    placed = []  # (kind, x, y, yaw, half)
    if tables and seats:
        main = tables[0]
        r_main, r_seat = kind_half(scene, main), max(kind_half(scene, k) for k in seats)
        r = r_main + r_seat + 3
        placed.append((main, 0.0, 0.0, 0.0, r_main))
        n = len(seats)
        start = {1: 90.0, 2: 0.0, 3: 90.0}.get(n, 45.0)
        for i, k in enumerate(seats):
            a = math.radians(start + 360.0 * i / n)
            placed.append((k, r * math.cos(a), r * math.sin(a), round((math.degrees(a) + 90 + 180) % 360 - 180, 1), r_seat))
        x = r + r_seat
        for k in tables[1:]:
            h = kind_half(scene, k)
            placed.append((k, x + 12 + h, 0.0, 0.0, h))
            x += 12 + 2 * h
    else:
        x = 0.0
        for k in kinds:
            h = kind_half(scene, k)
            placed.append((k, x + h, 0.0, 0.0, h))
            x += 2 * h + (6 if k in SEAT_KINDS else 12)
    x0 = min(px - h for _, px, _, _, h in placed)
    x1 = max(px + h for _, px, _, _, h in placed)
    y0 = min(py - h for _, _, py, _, h in placed)
    y1 = max(py + h for _, _, py, _, h in placed)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return [(k, px - cx, py - cy, yaw) for k, px, py, yaw, _ in placed], (x1 - x0) / 2 + 2, (y1 - y0) / 2 + 2
