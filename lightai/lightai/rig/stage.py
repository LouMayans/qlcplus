"""Where every fixture is: the 3D stage file (`<show>.stage.json`) that the /stage visualizer edits, read live.

The positions are provisional (the operator will measure the room later), so nothing is copied out of the file or
cached beyond its modified time: orders, mirror pairs, groups and aims are recomputed from the file every time it
changes. Looks store *what* they aim at ("the DJ") and are re-aimed after the layout changes.

Axes and maths are shared with webaccess/res/stage-rig.js (.claude/memory/stage-visualizer.md, "Axes and pan/tilt
maths"): user axes in inches, X across the room, Y depth (0 = the back wall with the DJ, growing towards the bar and
the entrance), Z up. Orientation R = Rz(rot[2]) . Ry(rot[1]) . Rx(rot[0]) . H with H = identity (floor), Rx(180)
(hung), Rx(90) (wall). Beam direction in fixture axes d = Rz(pan) . Rx(tilt) . (0, 0, 1).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

FALLBACK_PAN_MAX = 360
FALLBACK_TILT_MAX = 270
HANG_RX = {"floor": 0.0, "hung": 180.0, "wall": 90.0}
ORDERS = ("left_to_right", "right_to_left", "back_to_front", "front_to_back", "center_out", "outside_in",
          "circular", "counter_circular", "top_down", "bottom_up", "random")
ORDER_WORDS = {  # spoken forms -> order name
    "left to right": "left_to_right", "right to left": "right_to_left",
    "back to front": "back_to_front", "from the dj": "back_to_front", "front to back": "front_to_back",
    "towards the dj": "front_to_back", "center out": "center_out", "centre out": "center_out",
    "from the middle": "center_out", "inside out": "center_out", "outside in": "outside_in",
    "around the room": "circular", "clockwise": "circular", "counter clockwise": "counter_circular",
    "anticlockwise": "counter_circular", "top down": "top_down", "bottom up": "bottom_up", "random": "random",
}
_CACHE: dict = {}


def stage_path(show_path: Path) -> Path:
    p = Path(show_path)
    return p.with_name(p.stem + ".stage.json")


# ---------------------------------------------------------------- 3x3 maths (user axes)
def _rx(deg: float) -> list:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def _ry(deg: float) -> list:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def _rz(deg: float) -> list:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def _mul(a: list, b: list) -> list:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m: list, v) -> tuple:
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def _transpose(m: list) -> list:
    return [[m[j][i] for j in range(3)] for i in range(3)]


def _norm(v) -> tuple:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / n for x in v)


def orientation(entry: dict) -> list:
    """R for one stage fixture entry (rot in degrees, hang floor/hung/wall)."""
    rx, ry, rz = (list(entry.get("rot") or [0, 0, 0]) + [0, 0, 0])[:3]
    h = _rx(HANG_RX.get(entry.get("hang") or "floor", 0.0))
    return _mul(_rz(rz), _mul(_ry(ry), _mul(_rx(rx), h)))


def local_direction(pan_deg: float, tilt_deg: float) -> tuple:
    """d = Rz(pan) . Rx(tilt) . (0, 0, 1) = (sin p sin t, -cos p sin t, cos t)."""
    p, t = math.radians(pan_deg), math.radians(tilt_deg)
    return (math.sin(p) * math.sin(t), -math.cos(p) * math.sin(t), math.cos(t))


def beam_direction(entry: dict, pan_deg: float, tilt_deg: float) -> tuple:
    return _apply(orientation(entry), local_direction(pan_deg, tilt_deg))


# The 3D page's builtin/moving_head look (QLC+'s generic moving_head.dae) is fitted to 20 in, the club's BEAM230 as
# the operator measured it (stage-looks.js BUILTIN_HEIGHT_INCHES). Along the fixture's own axis from the mount face
# (the clamp / the legs, which is the fixture's position): pan pivot 10.40 in, tilt axis 13.91 in (the model's
# 22.09 / 29.54 of 42.49, scaled); the beam leaves at the tilt axis. models["<maker>/<model>"].bodyScale scales one
# model further (default 1, stage-scene.js bodyScaleFor).
MOVING_HEAD_HEIGHT_IN = 20.0
MOVING_HEAD_TILT_AXIS_IN = 29.545 * 20.0 / 42.494
MOVING_HEAD_DEFAULT_SCALE = 1.0


def dmx_to_degrees(pan16: int, tilt16: int, pan_max: float, tilt_max: float, entry: Optional[dict] = None) -> tuple:
    """Forward mapping, as the visualizer draws it."""
    e = entry or {}
    p = pan16 / 65535 * pan_max - pan_max / 2
    t = tilt16 / 65535 * tilt_max - tilt_max / 2
    if e.get("invertPan"):
        p = -p
    if e.get("invertTilt"):
        t = -t
    return p + float(e.get("panOffset") or 0), t + float(e.get("tiltOffset") or 0)


def _to_raw(deg: float, invert: bool, offset: float) -> float:
    raw = deg - offset
    return -raw if invert else raw


def _to16(raw_deg: float, span: float) -> int:
    return max(0, min(65535, round((raw_deg + span / 2) / span * 65535)))


def aim_degrees(entry: dict, target, pan_max: float, tilt_max: float, origin=None) -> Optional[tuple]:
    """(pan, tilt) in degrees that point the beam at `target` (inches), inside the fixture's ranges, closest to the
    centre of both ranges; None when the fixture can't reach it."""
    o = origin if origin is not None else entry.get("pos") or (0, 0, 0)
    w = _norm(tuple(float(target[i]) - float(o[i]) for i in range(3)))
    d = _apply(_transpose(orientation(entry)), w)
    t0 = math.degrees(math.acos(max(-1.0, min(1.0, d[2]))))
    st = math.sin(math.radians(t0))
    p0 = 0.0 if st < 1e-9 else math.degrees(math.atan2(d[0] / st, -d[1] / st))
    inv_p, inv_t = bool(entry.get("invertPan")), bool(entry.get("invertTilt"))
    off_p, off_t = float(entry.get("panOffset") or 0), float(entry.get("tiltOffset") or 0)
    best = None
    for p, t in ((p0, t0), (p0 + 180.0, -t0)):
        for k in (-2, -1, 0, 1, 2):
            pp = p + 360.0 * k
            rp, rt = _to_raw(pp, inv_p, off_p), _to_raw(t, inv_t, off_t)
            if abs(rp) <= pan_max / 2 + 1e-6 and abs(rt) <= tilt_max / 2 + 1e-6:
                key = (abs(rp), abs(rt))
                if best is None or key < best[0]:
                    best = (key, pp, t)
    return (best[1], best[2]) if best else None


def aim_dmx(entry: dict, target, pan_max: float, tilt_max: float, origin=None) -> Optional[dict]:
    """16-bit pan/tilt values (and their 8-bit halves) that aim at `target`."""
    got = aim_degrees(entry, target, pan_max, tilt_max, origin)
    if got is None:
        return None
    p, t = got
    pan16 = _to16(_to_raw(p, bool(entry.get("invertPan")), float(entry.get("panOffset") or 0)), pan_max)
    tilt16 = _to16(_to_raw(t, bool(entry.get("invertTilt")), float(entry.get("tiltOffset") or 0)), tilt_max)
    return {"pan_deg": p, "tilt_deg": t, "pan16": pan16, "tilt16": tilt16,
            "pan": pan16 >> 8, "pan_fine": pan16 & 0xFF, "tilt": tilt16 >> 8, "tilt_fine": tilt16 & 0xFF,
            "pan8": round(pan16 / 257), "tilt8": round(tilt16 / 257)}


def hit_point(entry: dict, pan_deg: float, tilt_deg: float, target, origin=None) -> Optional[tuple]:
    """Where a beam passes closest to `target`: the point on the beam ray nearest to it (for checks)."""
    o = origin if origin is not None else entry.get("pos") or (0, 0, 0)
    d = beam_direction(entry, pan_deg, tilt_deg)
    v = tuple(float(target[i]) - float(o[i]) for i in range(3))
    s = sum(v[i] * d[i] for i in range(3))
    if s <= 0:
        return None
    return tuple(float(o[i]) + d[i] * s for i in range(3))


# ---------------------------------------------------------------- places and the stage file
@dataclass
class Place:
    name: str
    pos: tuple
    category: str = ""
    aliases: list = field(default_factory=list)
    size: Optional[tuple] = None  # footprint in inches (x, y) when known


def _slug(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


class Stage:
    """One show's stage file, with derived orders, pairs, groups, places and aims."""

    def __init__(self, path: Path, data: dict) -> None:
        self.path = Path(path)
        self.data = data
        self.hash = hashlib.sha1(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        room = data.get("room") or {}
        self.room = {k: float(room.get(k) or 0) for k in ("width", "depth", "height")}
        anchor = data.get("anchor") or [self.room["width"] / 2, self.room["depth"] / 2, 0]
        self.anchor = tuple(float(x) for x in anchor)
        self.fixtures = {int(k): v for k, v in (data.get("fixtures") or {}).items() if str(k).lstrip("-").isdigit()}
        self.models = data.get("models") or {}
        self.places: dict = {}
        for obj in data.get("objects") or []:
            if not isinstance(obj, dict) or not obj.get("pos"):
                continue
            place = Place(obj.get("name") or obj.get("id") or "?", tuple(float(x) for x in obj["pos"]),
                          obj.get("category") or "", list(obj.get("aliases") or []), self._footprint(obj))
            for alias in [place.name] + place.aliases:
                key = _slug(alias)
                if key and key not in self.places:
                    self.places[key] = place

    @classmethod
    def load(cls, show_path: Path) -> Optional["Stage"]:
        p = stage_path(show_path)
        try:
            st = p.stat()
        except OSError:
            return None
        key = (st.st_size, st.st_mtime_ns)
        hit = _CACHE.get(str(p))
        if hit and hit[0] == key:
            return hit[1]
        try:
            stage = cls(p, json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            stage = None
        _CACHE[str(p)] = (key, stage)
        return stage

    def _footprint(self, obj: dict) -> Optional[tuple]:
        pd = (self.data.get("propDefs") or {}).get(obj.get("prop") or "") or {}
        parts = pd.get("parts") or []
        if not parts:
            return None
        sc = list(obj.get("scale") or [1, 1, 1]) + [1, 1]
        w = max(float((pt.get("size") or [0, 0])[0]) for pt in parts) * float(sc[0])
        d = max(float((pt.get("size") or [0, 0])[1]) for pt in parts) * float(sc[1])
        return (w, d) if w and d else None

    # ---- fixtures
    def pos(self, fid: int) -> Optional[tuple]:
        e = self.fixtures.get(int(fid))
        return tuple(float(x) for x in e["pos"]) if e and e.get("pos") else None

    def check(self, rig) -> dict:
        placed, patched = set(self.fixtures), set(rig.fixtures)
        return {"missing": sorted(patched - placed), "extra": sorted(placed - patched)}

    def pan_tilt_range(self, fx) -> tuple:
        """panMax/tiltMax: the stage file's model override, then the fixture mode, then the definition, then 360/270."""
        ov = self.models.get(f"{fx.manufacturer}/{fx.model}") or {}
        pan, tilt = ov.get("panMax"), ov.get("tiltMax")
        fd = getattr(fx, "definition", None)
        if fd is not None and (not pan or not tilt):
            md = (getattr(fd, "modes", None) or {}).get(getattr(fx, "mode", ""))
            pan = pan or (getattr(md, "pan_max", 0) if md else 0) or getattr(fd, "pan_max", 0)
            tilt = tilt or (getattr(md, "tilt_max", 0) if md else 0) or getattr(fd, "tilt_max", 0)
        return float(pan or FALLBACK_PAN_MAX), float(tilt or FALLBACK_TILT_MAX)

    # ---- orders
    def order(self, ids, how: str) -> list:
        """Fixture IDs in a spatial order; fixtures without a position keep their order at the end."""
        placed = [i for i in ids if self.pos(i) is not None]
        rest = [i for i in ids if self.pos(i) is None]
        ax, ay, _ = self.anchor

        def xyz(i):
            return self.pos(i)
        keys = {
            "left_to_right": lambda i: (xyz(i)[0], xyz(i)[1], i),
            "right_to_left": lambda i: (-xyz(i)[0], xyz(i)[1], i),
            "back_to_front": lambda i: (xyz(i)[1], xyz(i)[0], i),
            "front_to_back": lambda i: (-xyz(i)[1], xyz(i)[0], i),
            "center_out": lambda i: (math.hypot(xyz(i)[0] - ax, xyz(i)[1] - ay), xyz(i)[0], i),
            "outside_in": lambda i: (-math.hypot(xyz(i)[0] - ax, xyz(i)[1] - ay), xyz(i)[0], i),
            "circular": lambda i: (math.atan2(xyz(i)[1] - ay, xyz(i)[0] - ax), i),
            "counter_circular": lambda i: (-math.atan2(xyz(i)[1] - ay, xyz(i)[0] - ax), i),
            "top_down": lambda i: (-xyz(i)[2], xyz(i)[0], i),
            "bottom_up": lambda i: (xyz(i)[2], xyz(i)[0], i),
        }
        if how == "random":
            seed = int(hashlib.sha1(",".join(map(str, sorted(placed))).encode()).hexdigest()[:8], 16)
            out = sorted(placed)
            random.Random(seed).shuffle(out)
            return out + rest
        if how not in keys:
            raise ValueError(f"unknown order '{how}' (use one of {', '.join(ORDERS)})")
        return sorted(placed, key=keys[how]) + rest

    def position_fraction(self, ids, how: str) -> dict:
        """0..1 per fixture along an order (for phase offsets and gradients); equal places share a value."""
        ordered = self.order(ids, how)
        if len(ordered) < 2:
            return {i: 0.0 for i in ordered}
        return {i: k / (len(ordered) - 1) for k, i in enumerate(ordered)}

    def angle(self, fid: int) -> Optional[float]:
        p = self.pos(fid)
        return None if p is None else math.degrees(math.atan2(p[1] - self.anchor[1], p[0] - self.anchor[0])) % 360

    # ---- symmetry and groups
    def mirror_pairs(self, ids, models: Optional[dict] = None, tol: Optional[float] = None) -> tuple:
        """([(left_id, right_id), ...], [centre ids], [unpaired ids]) mirrored across the room's centre line
        (x = anchor x), same model when `models` ({id: model key}) is given."""
        ax = self.anchor[0]
        tol = tol if tol is not None else max(24.0, 0.08 * (self.room["width"] or 300))
        placed = [i for i in ids if self.pos(i) is not None]
        centre = [i for i in placed if abs(self.pos(i)[0] - ax) <= tol / 2]
        left = [i for i in placed if self.pos(i)[0] < ax - tol / 2]
        right = [i for i in placed if self.pos(i)[0] > ax + tol / 2]
        pairs, used = [], set()
        for a in sorted(left, key=lambda i: (self.pos(i)[1], self.pos(i)[0])):  # nearest mirrored partner, same model
            pa = self.pos(a)
            want = (2 * ax - pa[0], pa[1], pa[2])
            cands = [b for b in right if b not in used and (models is None or models.get(a) == models.get(b))]
            if not cands:
                continue
            b = min(cands, key=lambda j: math.dist(self.pos(j), want))
            if math.dist(self.pos(b), want) <= tol * 2:
                pairs.append((a, b))
                used.add(b)
        paired = {i for p in pairs for i in p}
        return pairs, centre, [i for i in placed if i not in paired and i not in centre]

    def groups(self, ids, positions_final: bool = False) -> dict:
        """Location groups by real position. Left/right/centre only once the operator marks the positions final:
        until then 'left side' keeps meaning the hand-made stage_left/stage_right zones."""
        ax, ay, _ = self.anchor
        tol = max(24.0, 0.08 * (self.room["width"] or 300))
        out: dict = {}
        placed = [i for i in ids if self.pos(i) is not None]
        if positions_final:
            out["left side"] = [i for i in placed if self.pos(i)[0] < ax - tol / 2]
            out["right side"] = [i for i in placed if self.pos(i)[0] > ax + tol / 2]
            out["centre"] = [i for i in placed if abs(self.pos(i)[0] - ax) <= tol / 2]
        ytol = max(24.0, 0.08 * (self.room["depth"] or 300))
        out["back row"] = [i for i in placed if self.pos(i)[1] < ay - ytol / 2]
        out["front row"] = [i for i in placed if self.pos(i)[1] > ay + ytol / 2]
        h = self.room["height"] or 150
        out["up high"] = [i for i in placed if self.pos(i)[2] >= h * 0.6]
        out["floor level"] = [i for i in placed if self.pos(i)[2] <= 60]
        floor = self.place("dance floor")
        if floor:
            half = (floor.size[0] / 2, floor.size[1] / 2) if floor.size else (120.0, 120.0)
            out["over the dance floor"] = [i for i in placed if abs(self.pos(i)[0] - floor.pos[0]) <= half[0]
                                           and abs(self.pos(i)[1] - floor.pos[1]) <= half[1]]
        for name, key in (("near the dj", "dj"), ("near the bar", "bar"), ("near the entrance", "entrance")):
            pl = self.place(key)
            if pl:
                out[name] = [i for i in placed if math.hypot(self.pos(i)[0] - pl.pos[0], self.pos(i)[1] - pl.pos[1]) <= 168]
        return {k: v for k, v in out.items() if v}

    # ---- places
    def place(self, phrase: str) -> Optional[Place]:
        """A named place: 'the dj', 'dj booth', 'middle of the floor', 'the bar', 'dance floor'."""
        t = _slug(phrase)
        t = re.sub(r"^(the|a|an)\s+", "", t)
        for key in (t, f"the {t}", t.replace("centre", "center"), t.replace("center", "centre")):
            if key in self.places:
                return self.places[key]
        # a word of a place name ('dance floor' -> 'Dance floor', 'bar' -> 'Bar')
        for key, pl in self.places.items():
            if re.sub(r"^the\s+", "", key) == t:
                return pl
        return None

    def target_point(self, phrase: str) -> Optional[tuple]:
        """Where to aim for a place: people and surfaces at head height (60 in) unless it is a point on the floor."""
        pl = self.place(phrase)
        if pl is None:
            return None
        z = pl.pos[2] if pl.category in ("point",) else max(pl.pos[2], 48.0)
        return (pl.pos[0], pl.pos[1], z)

    # ---- aiming a fixture
    def body_scale(self, fx) -> float:
        """How much the 3D page scales this moving head (models[key].bodyScale; 1 by default: the look is real size)."""
        ov = self.models.get(f"{getattr(fx, 'manufacturer', '')}/{getattr(fx, 'model', '')}") or {}
        s = ov.get("bodyScale")
        return float(s) if isinstance(s, (int, float)) and s > 0 else MOVING_HEAD_DEFAULT_SCALE

    def beam_origin(self, fx) -> Optional[tuple]:
        """Where the beam starts, like the 3D page draws it: a moving head's tilt axis (on its pan axis, so it doesn't
        move with pan/tilt), which hangs below the clamp point of a hung fixture; the fixture's position otherwise."""
        entry = self.fixtures.get(int(fx.id))
        if entry is None or not entry.get("pos"):
            return None
        pos = tuple(float(v) for v in (list(entry["pos"]) + [0, 0, 0])[:3])
        ov = self.models.get(f"{getattr(fx, 'manufacturer', '')}/{getattr(fx, 'model', '')}") or {}
        look = entry.get("look") or ov.get("look")
        has = getattr(fx, "has", None)
        mover = callable(has) and has("pan") and has("tilt")
        if not mover or look not in (None, "builtin/moving_head"):
            return pos
        d = MOVING_HEAD_TILT_AXIS_IN * self.body_scale(fx)
        off = _apply(orientation(entry), (0.0, 0.0, d))
        return (pos[0] + off[0], pos[1] + off[1], pos[2] + off[2])

    def aim(self, fx, target) -> Optional[dict]:
        """DMX pan/tilt for one fixture (rig fixture) at a place name or a point in inches, the beam starting where
        the 3D page draws it (beam_origin)."""
        entry = self.fixtures.get(int(fx.id))
        if entry is None or not entry.get("pos"):
            return None
        point = self.target_point(target) if isinstance(target, str) else tuple(target)
        if point is None:
            return None
        pan_max, tilt_max = self.pan_tilt_range(fx)
        got = aim_dmx(entry, point, pan_max, tilt_max, origin=self.beam_origin(fx))
        if got is not None:
            got.update({"target": point, "pan_max": pan_max, "tilt_max": tilt_max})
        return got

    # ---- summary for people and for Claude
    def summary(self, rig, positions_final: bool = False) -> str:
        """A compact text map of the room: fixtures by group with positions in feet, pairs, places."""
        ids = sorted(rig.fixtures)
        lines = [f"Room {self.room['width'] / 12:.0f} x {self.room['depth'] / 12:.0f} ft, {self.room['height'] / 12:.0f} ft high. "
                 f"X runs left to right, Y from the back wall (the DJ) towards the bar and entrance, Z up. "
                 f"Dance-floor centre at ({self.anchor[0] / 12:.0f}, {self.anchor[1] / 12:.0f}) ft."]
        if not positions_final:
            lines.append("Positions are provisional: prefer relative moves (left/right, front/back, centre-out) over precise aims.")
        for i in ids:
            fx, p = rig.fixtures[i], self.pos(i)
            if p is None:
                continue
            e = self.fixtures[i]
            lines.append(f"- {fx.name} ({fx.model}, {fx.kind}): ({p[0] / 12:.1f}, {p[1] / 12:.1f}, {p[2] / 12:.1f}) ft, {e.get('hang') or 'floor'}")
        pairs, centre, _ = self.mirror_pairs(ids, {i: rig.fixtures[i].key for i in ids})
        if pairs:
            lines.append("Mirror pairs: " + ", ".join(f"{rig.fixtures[a].name} <-> {rig.fixtures[b].name}" for a, b in pairs))
        g = self.groups(ids, positions_final)
        if g:
            lines.append("Groups: " + "; ".join(f"{k} ({len(v)})" for k, v in g.items()))
        names = sorted({pl.name for pl in self.places.values() if pl.category not in ("decor", "seating")})
        if names:
            lines.append("Places to aim at: " + ", ".join(names))
        return "\n".join(lines)
