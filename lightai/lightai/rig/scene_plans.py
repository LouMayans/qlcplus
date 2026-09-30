"""Plans for editing the room's 3D stage by voice (the Planner calls these when the show has a <show>.stage.json).

'move fixture 12 one foot to the left', 'move table 3 with its chairs a foot to the right from the stage', 'place
fixture 7 at 5 foot by 5 foot', 'put the high top next to the dj booth', 'rotate fixture 8 upside down', 'turn cocktail
table 1 90 degrees', 'mount fixture 4 on the wall', 'add a high top table and 4 stools next to the dj booth', 'remove
bar stool 4'. Each plan carries one edit_stage action: the changes with the values they replace (see rig/scene.py)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from lightai.rig.scene import (CATALOG, Box, Ref, Scene, SceneError, direction_vector, direction_words, fmt_len, fmt_pos,
                               hang_changes, kind_of, layout_group, move_changes, new_id, next_name, prop_for, read_doc,
                               remove_changes, rotate_changes, viewpoint)
from lightai.rig.stage import stage_path
from lightai.schema import Action

TWO_D = re.compile(r"\b(?:2d|2-d|two d|monitor|stage map|the map)\b", re.I)
WITH_KIDS = re.compile(r"\b(?:with|and|together with|plus) (?:its|the|their|all (?:its|the)) (?:chairs?|stools?|seats?|seating)\b", re.I)
OBJECT_WORD = re.compile(r"\b(?:tables?|high ?tops?|stools?|chairs?|seats?|booths?|bar|back bar|console|dj booth|speakers?|stacks?|"
                         r"plants?|sofas?|couch(?:es)?|decks?|stage|truss|lamps?|pendants?|risers?|podiums?|pillars?|columns?|screens?|tvs?|"
                         r"walls?|door|entrance)\b", re.I)


def scene_of(planner) -> Optional[tuple]:
    """(Scene, stage file) for the show lightai edits, or None when it has no 3D stage (then the 2D map is edited)."""
    path = stage_path(planner.cfg.project_path)
    if not path.exists():
        return None
    return Scene(read_doc(path), planner.rig), path


def wants_3d(planner, cmd) -> bool:
    return not TWO_D.search(cmd.text) and scene_of(planner) is not None


def _raw(cmd, slot: str) -> str:
    return " ".join(sv.raw for sv in cmd.slots.get(slot) or [])


def refs_of(scene: Scene, cmd, allow_fixtures: bool = True) -> tuple:
    """The things a command names: fixtures by the NLU's fixture ids, objects by name in the scene. (refs, missing)"""
    refs, missing = [], []
    for sv in cmd.slots.get("target") or []:
        v = sv.value or {}
        fids = [] if v.get("objects") is not None else [str(f) for f in v.get("fixture_ids") or []]
        objectish = bool(OBJECT_WORD.search(sv.raw))
        if fids and not v.get("unresolved") and not objectish:
            if not allow_fixtures:
                raise SceneError("fixtures are patched in QLC+, not the 3D stage: say which object to remove")
            refs += [Ref("fixture", f) for f in fids]
            missing += [f for f in fids if f not in scene.fixtures]
            continue
        ids, cands = (v["objects"], []) if v.get("objects") else scene.find(sv.raw)
        if cands:
            names = ", ".join((scene.obj(c) or {}).get("name", c) for c in cands[:4])
            raise SceneError(f"Which one: {names}?")
        if not ids:
            if fids and allow_fixtures:
                refs += [Ref("fixture", f) for f in fids]
                missing += [f for f in fids if f not in scene.fixtures]
                continue
            raise SceneError(f"I can't find '{sv.raw}' in the 3D stage")
        refs += [Ref("object", i) for i in ids]
    seen, out = set(), []
    for r in refs:
        if r.key not in seen:
            seen.add(r.key)
            out.append(r)
    return out, missing


def with_children(scene: Scene, refs: list, text: str) -> tuple:
    """'table 3 with its chairs': the table's seats come along. (refs, the added seat refs)"""
    if not WITH_KIDS.search(text):
        return refs, []
    kids = []
    for r in refs:
        if r.kind == "object":
            kids += [Ref("object", k) for k in scene.children(r.id) if Ref("object", k).key not in {x.key for x in refs + kids}]
    return refs + kids, kids


def _names(scene: Scene, refs: list) -> str:
    names = [scene.name(r) for r in refs]
    if len(set(names)) < len(names):  # four 'Potted plant's: say 'the 4 potted plants'
        counts = {}
        for n in names:
            counts[n] = counts.get(n, 0) + 1
        names = [f"the {c} {n.lower()}s" if c > 1 else n for n, c in counts.items()]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if len(names) < 5 else f"{len(refs)} things"


def _said(cmd, refs: list) -> str:
    """'the beams (14 fixtures)': many fixtures named by one phrase are called what the operator called them."""
    targets = cmd.slots.get("target") or []
    if len(refs) >= 5 and len(targets) == 1 and all(r.kind == "fixture" for r in refs):
        return f"the {re.sub(r'^the ', '', targets[0].raw.strip(), flags=re.I)} ({len(refs)} fixtures)"
    return ""


def _group_label(scene: Scene, refs: list, kids: list) -> str:
    main = [r for r in refs if r.key not in {k.key for k in kids}]
    label = _names(scene, main)
    if kids:
        kinds = {re.sub(r"\s*\w*\d+\w*$", "", scene.name(k)).strip().lower() or "seat" for k in kids}
        label += f" and its {len(kids)} {'/'.join(sorted(kinds))}{'s' if len(kids) > 1 and len(kinds) == 1 else ''}"
    return label


def _stage_plan(planner, intent: str, scene: Scene, path: Path, changes: list, summary: str, assumptions: list, warnings: list,
                changed: list):
    if not changes:
        return planner._plan(intent, "info", summary + " (nothing to change)")
    return planner._plan(intent, "structural", summary + " in the 3D stage",
                         actions=[Action(op="edit_stage", args={"stage_path": str(path), "changes": changes},
                                         describe=f"{len(changes)} change(s) to {path.name}")],
                         assumptions=assumptions, warnings=warnings, needs_confirmation=True,
                         followup={"stage": {"changed": [r.key for r in changed], "file": str(path)}})


# ------------------------------------------------------------------------------------------------ move / place

def plan_move(planner, cmd):
    scene, path = scene_of(planner)
    refs, missing = refs_of(scene, cmd)
    if not refs:
        raise SceneError("Which fixture or object? (e.g. 'fixture 12', 'table 3', 'the dj console')")
    refs, kids = with_children(scene, refs, cmd.text)
    label = _said(cmd, refs) or _group_label(scene, refs, kids)
    assumptions, warnings = [], []
    coords = cmd.first("coordinates")
    place = cmd.first("place")
    dist = cmd.first("distance")
    dirs = _raw(cmd, "direction")
    view = viewpoint(cmd.text)
    lead = refs[0]
    if missing and not coords:
        raise SceneError(f"fixture(s) {', '.join(missing)} aren't placed in the 3D stage yet: say where, e.g. 'place fixture "
                         f"{missing[0]} at 5 by 5 feet'")
    if coords and coords.value.get("x") is not None:
        c = coords.value
        cur = scene.pos(lead) if lead.kind != "fixture" or lead.id in scene.fixtures else None
        x, y = scene.anchor[0] + c["x"], scene.anchor[1] + c["y"]
        changes = []
        if cur is None:  # a fixture the stage doesn't have yet: add it hung at the height of the others
            changes.append(_new_fixture_entry(planner, scene, lead.id, x, y, c.get("z")))
            assumptions.append({"fact": f"fixture {lead.id} wasn't in the 3D stage: added hung at {fmt_len(changes[0]['value']['pos'][2])}",
                                "source": "3D stage"})
            refs = refs[1:]
            if not refs:
                return _stage_plan(planner, cmd.intent, scene, path, changes, f"Place {label} at {fmt_pos([x, y, 0], scene.anchor)}",
                                   assumptions, warnings, [lead])
            lead = refs[0]
            cur = scene.pos(lead)
        z = c.get("z")
        dz = (z - cur[2]) if z is not None else 0.0
        if lead.kind == "object" and z is None and cur[2] < 60:
            dz = scene.support_z(x, y, {r.id for r in refs if r.kind == "object"}) - cur[2]
        delta = (x - cur[0], y - cur[1], dz)
        if c.get("assumed"):
            assumptions.append({"fact": c["assumed"], "source": "normalizer"})
        assumptions.append({"fact": "coordinates are read like the 3D editor shows them: from the dance-floor centre (the anchor), "
                                    "x to the DJ's right, y towards the bar", "source": "3D stage"})
        changes += move_changes(scene, refs, delta)
        return _stage_plan(planner, cmd.intent, scene, path, changes,
                           f"Place {label} at {fmt_pos([x, y, 0], scene.anchor)}", assumptions, warnings, refs)
    if place and place.value.get("relation"):
        rel, ref_word = place.value["relation"], place.value.get("ref", "")
        excl = {r.id for r in refs if r.kind == "object"}
        if rel in ("toward", "away_from"):
            box, where = scene.ref_box(ref_word)
            cur = scene.pos(lead)
            vx, vy = box.cx - cur[0], box.cy - cur[1]
            n = (vx * vx + vy * vy) ** 0.5 or 1.0
            step = float(dist.value.get("in") or 12.0) if dist and dist.value else min(24.0, n / 2)
            if not (dist and dist.value):
                assumptions.append({"fact": f"no distance given: {fmt_len(step)}", "source": "default"})
            sgn = 1.0 if rel == "toward" else -1.0
            delta = (sgn * vx / n * step, sgn * vy / n * step, 0.0)
            changes = move_changes(scene, refs, delta)
            return _stage_plan(planner, cmd.intent, scene, path, changes,
                               f"Move {label} {fmt_len(step)} {'towards' if sgn > 0 else 'away from'} {where}", assumptions, warnings, refs)
        box, where = scene.ref_box(ref_word)
        mains = [r for r in refs if r.key not in {k.key for k in kids}]
        if len(mains) > 1 and not kids and rel not in ("between",):  # 'the plants by the bar': each gets its own spot
            import copy as _copy

            from lightai.rig.scene import apply_changes

            work = Scene(_copy.deepcopy(scene.doc), scene.rig)
            changes = []
            for r in mains:
                b = work.box(r)
                if rel in ("on", "in", "center_of"):
                    x, y = work.spot_at(box.cx, box.cy, b.w / 2, b.d / 2, {r.id})
                else:
                    x, y, _ = work.spot(box, rel, b.w / 2, b.d / 2, {r.id})
                cur = work.pos(r)
                dz = (work.support_z(x, y, {r.id}) - cur[2]) if r.kind == "object" and cur[2] < 60 else 0.0
                ch = move_changes(work, [r], (x - b.cx, y - b.cy, dz))
                work.doc = apply_changes(work.doc, ch)
                changes += ch
            return _stage_plan(planner, cmd.intent, scene, path, changes, f"Move {label} next to {where}", assumptions, warnings, refs)
        group = [scene.box(r) for r in refs]
        gx0, gx1 = min(b.x0 for b in group), max(b.x1 for b in group)
        gy0, gy1 = min(b.y0 for b in group), max(b.y1 for b in group)
        cur = scene.pos(lead)
        if rel in ("on", "in", "center_of"):
            x, y = scene.spot_at(box.cx, box.cy, (gx1 - gx0) / 2, (gy1 - gy0) / 2, excl)
            side = "on" if rel == "on" else "in"
        elif rel == "between" and place.value.get("refs"):
            b2, _ = scene.ref_box(place.value["refs"][1])
            x, y = scene.spot_at((box.cx + b2.cx) / 2, (box.cy + b2.cy) / 2, (gx1 - gx0) / 2, (gy1 - gy0) / 2, excl, avoid_floor=True)
            side = "between"
        else:
            x, y, side = scene.spot(box, rel, (gx1 - gx0) / 2, (gy1 - gy0) / 2, excl)
        gcx, gcy = (gx0 + gx1) / 2, (gy0 + gy1) / 2
        dz = 0.0
        if lead.kind == "object" and cur[2] < 60:
            dz = scene.support_z(x, y, excl) - cur[2]
        changes = move_changes(scene, refs, (x - gcx, y - gcy, dz))
        rel_words = {"right": "to the right of", "left": "to the left of", "front": "in front of", "back": "behind",
                     "on": "on", "in": "in", "between": "between"}.get(side, "next to")
        what = f"{where} and {place.value['refs'][1]}" if side == "between" else where
        return _stage_plan(planner, cmd.intent, scene, path, changes, f"Move {label} {rel_words} {what}", assumptions, warnings, refs)
    vec = direction_vector(dirs, cmd.text)
    if vec is None:
        raise SceneError("Which way? e.g. 'a foot to the left', 'up 6 inches', 'at 5 by 5 feet', 'next to the dj booth'")
    if dist and dist.value.get("in") is not None:
        inches = float(dist.value["in"])
        if (dist.value.get("assumed") or "").startswith("no unit"):
            inches = float(dist.value["mm"]) / 10.0 * 12.0  # a bare number in the room is feet: '2' = 2 feet
            assumptions.append({"fact": f"no unit given: {dist.raw} read as feet", "source": "3D stage"})
        elif dist.value.get("assumed"):
            assumptions.append({"fact": dist.value["assumed"], "source": "normalizer"})
    else:
        inches = 12.0
        assumptions.append({"fact": "no distance given: one foot", "source": "default"})
    delta = tuple(v * inches for v in vec)
    if abs(vec[0]) > 0:
        assumptions.append({"fact": "left and right as seen from the DJ booth, like the 3D view's default camera; say 'from the dance "
                                    "floor' for the other way round" if view == "stage" else "left and right as seen from the dance floor",
                            "source": "3D stage"})
    changes = move_changes(scene, refs, delta)
    for r, ch in zip(refs, changes):
        if not scene.inside(Box(ch["value"][0] - 1, ch["value"][0] + 1, ch["value"][1] - 1, ch["value"][1] + 1), margin=0):
            warnings.append(f"{scene.name(r)} would end up outside the room")
    return _stage_plan(planner, cmd.intent, scene, path, changes,
                       f"Move {label} {fmt_len(inches)} {direction_words(vec, view)}", assumptions, warnings, refs)


def _new_fixture_entry(planner, scene: Scene, fid: str, x: float, y: float, z: Optional[float]) -> dict:
    fx = planner.rig.fixtures.get(int(fid))
    model = f"{getattr(fx, 'manufacturer', '')}/{getattr(fx, 'model', '')}" if fx is not None else ""
    hung = [float((e.get("pos") or [0, 0, 0])[2]) for e in scene.fixtures.values() if e.get("hang") == "hung"]
    height = z if z is not None else (max(set(hung), key=hung.count) if hung else min(150.0, float(scene.room.get("height", 168)) - 18))
    entry = {"model": model, "pos": [round(x, 3), round(y, 3), round(height, 3)], "rot": [0, 0, 0], "hang": "hung",
             "invertPan": False, "invertTilt": False, "panOffset": 0, "tiltOffset": 0, "look": None}
    return {"op": "set", "kind": "fixture", "id": str(fid), "field": "entry", "value": entry, "before": None}


# ------------------------------------------------------------------------------------------------ rotate / flip / mount

UPSIDE = re.compile(r"\bupside[\s-]*down\b|\bflip(?:ped)?\b|\binvert(?:ed)?\b|\bturn (?:it |them )?over\b", re.I)
WALL = re.compile(r"\b(?:on|onto|to|against) the wall\b|\bwall[\s-]*mount", re.I)
FLOOR = re.compile(r"\b(?:on|onto) the floor\b|\bfloor[\s-]*mount|\bstand(?:ing)?\b|\bright way up\b", re.I)
HANG = re.compile(r"\bhang\b|\bhung\b|\bfrom the (?:truss|ceiling)\b", re.I)
CCW = re.compile(r"\b(?:anti|counter)[\s-]*clock[\s-]*wise\b|\bccw\b|\bto the left\b|\bleft\b", re.I)


def plan_rotate(planner, cmd):
    scene, path = scene_of(planner)
    refs, missing = refs_of(scene, cmd)
    if missing:
        raise SceneError(f"fixture(s) {', '.join(missing)} aren't placed in the 3D stage yet: place them first")
    if not refs:
        raise SceneError("Which fixture or object?")
    text = cmd.text
    fixtures = [r for r in refs if r.kind == "fixture"]
    objects = [r for r in refs if r.kind == "object"]
    assumptions = []
    if fixtures and (WALL.search(text) or FLOOR.search(text) or HANG.search(text) or UPSIDE.search(text)):
        if WALL.search(text):
            hang = "wall"
        elif FLOOR.search(text):
            hang = "floor"
        elif UPSIDE.search(text) and not HANG.search(text):
            now = {(scene.fixtures.get(r.id) or {}).get("hang", "floor") for r in fixtures}
            hang = "floor" if now == {"hung"} else "hung"
        else:
            hang = "hung"
        changes = hang_changes(scene, fixtures, hang)
        what = {"hung": "hung upside down", "floor": "standing on the floor", "wall": "mounted on the wall"}[hang]
        was = sorted({(scene.fixtures.get(r.id) or {}).get("hang", "floor") for r in fixtures})
        verb = "Flip" if UPSIDE.search(text) and not WALL.search(text) else "Set"
        return _stage_plan(planner, cmd.intent, scene, path, changes,
                           f"{verb} {_names(scene, fixtures)} {'over: ' if verb == 'Flip' else ''}{'/'.join(was)} -> {what}",
                           assumptions, [], fixtures)
    ang = next((sv.value for sv in cmd.slots.get("angle") or [] if "deg" in sv.value), None)
    if ang is None and UPSIDE.search(text):
        ang = {"deg": 180, "relative": True}
    if ang is None:
        raise SceneError("By how many degrees? e.g. '90 degrees', 'a quarter turn'; or 'upside down', 'on the wall'")
    deg = float(ang["deg"])
    ccw = bool(CCW.search(text)) or any((sv.value or {}).get("dir") in ("ccw", "left") for sv in cmd.slots.get("direction") or [])
    yaw = deg if ccw else -deg
    if not ccw and not re.search(r"\bclock[\s-]*wise\b|\bcw\b|\bright\b", text, re.I):
        assumptions.append({"fact": "turning clockwise seen from above; say counter-clockwise for the other way", "source": "default"})
    refs2, kids = with_children(scene, refs, text)
    pivot = None
    if kids and objects:
        b = scene.box(objects[0])
        pivot = (b.cx, b.cy)
    changes = rotate_changes(scene, refs2, yaw, pivot)
    return _stage_plan(planner, cmd.intent, scene, path, changes,
                       f"Turn {_said(cmd, refs2) or _group_label(scene, refs2, kids)} {abs(deg):g} degrees {'counter-clockwise' if ccw else 'clockwise'} (seen from above)",
                       assumptions, [], refs2)


# ------------------------------------------------------------------------------------------------ add / remove

def _items(cmd) -> list:
    """[(kind, count, word)] from the object slots, each with the count said just before it."""
    spans = sorted([s for s in cmd.spans if s.slot in ("object", "count")], key=lambda s: s.start)
    out, pending = [], None
    obj_vals = iter(cmd.slots.get("object") or [])
    cnt_vals = iter(cmd.slots.get("count") or [])
    for s in spans:
        if s.slot == "count":
            v = next(cnt_vals, None)
            pending = int((v.value or {}).get("count") or 1) if v is not None else 1
        else:
            v = next(obj_vals, None)
            word = v.raw if v is not None else s.text
            kind = kind_of(word)
            n = pending or (2 if re.search(r"\b(?:pair of|both|two)\b", word, re.I) else 1)
            if kind is None:
                raise SceneError(f"I don't know how to draw '{word}' yet. I can add: " + ", ".join(c["label"].lower() for c in CATALOG.values()))
            out.append((kind, n, word))
            pending = None
    if not out:
        for v in cmd.slots.get("object") or []:
            kind = kind_of(v.raw)
            if kind is None:
                raise SceneError(f"I don't know how to draw '{v.raw}' yet")
            out.append((kind, 1, v.raw))
    return out


def plan_add(planner, cmd):
    scene, path = scene_of(planner)
    items = _items(cmd)
    if sum(n for _, n, _ in items) > 24:
        raise SceneError("that's more than 24 things at once; add them in smaller groups")
    coords, place = cmd.first("coordinates"), cmd.first("place")
    assumptions = []
    both = place is not None and place.value.get("relation") == "both_sides_of"
    along = place is not None and place.value.get("relation") == "along"
    side_items = [(k, max(1, n // 2)) for k, n, _ in items] if both else [(k, n) for k, n, _ in items]
    layout, half_w, half_d = layout_group(scene, side_items)
    centres = []
    if coords and coords.value.get("x") is not None:
        x, y = scene.anchor[0] + coords.value["x"], scene.anchor[1] + coords.value["y"]
        x, y = scene.spot_at(x, y, half_w, half_d, set())
        centres = [(x, y, "at")]
        where = f"at {fmt_pos([x, y, 0], scene.anchor)}"
    elif place and place.value.get("ref"):
        box, ref_name = scene.ref_box(place.value["ref"])
        rel = place.value["relation"]
        if both:  # one on each side
            xl, yl, _ = scene.spot(box, "left_of", half_w, half_d, set())
            xr, yr, _ = scene.spot(box, "right_of", half_w, half_d, set())
            centres = [(xl, yl, "left"), (xr, yr, "right")]
            where = f"on either side of {ref_name}"
        elif along:
            where = f"along {ref_name}"
            centres = [None]
        elif rel in ("on", "in", "center_of"):
            x, y = scene.spot_at(box.cx, box.cy, half_w, half_d, set())
            centres = [(x, y, rel)]
            where = f"{'on' if rel == 'on' else 'in'} {ref_name}"
        elif rel == "between" and place.value.get("refs"):
            b2, _ = scene.ref_box(place.value["refs"][1])
            x, y = scene.spot_at((box.cx + b2.cx) / 2, (box.cy + b2.cy) / 2, half_w, half_d, set(), avoid_floor=True)
            centres = [(x, y, "between")]
            where = f"between {ref_name} and {place.value['refs'][1]}"
        else:
            x, y, side = scene.spot(box, rel, half_w, half_d, set())
            centres = [(x, y, side)]
            where = {"right": f"to the right of {ref_name}", "left": f"to the left of {ref_name}", "front": f"in front of {ref_name}",
                     "back": f"behind {ref_name}"}.get(side, f"next to {ref_name}")
            if rel in ("next_to", "near"):
                assumptions.append({"fact": f"the nearest free spot {where.split(' of ')[0] if ' of ' in where else where}; say left of / "
                                            "in front of / behind to choose", "source": "3D stage"})
    else:
        raise SceneError("Where should it go? e.g. 'next to the dj booth', 'at 5 by 5 feet'")
    changes, taken_ids, taken_names, made = [], set(), set(), []
    props_added = set()

    def add_one(kind: str, x: float, y: float, yaw: float, parent: Optional[str]) -> str:
        prop, add = prop_for(scene.doc, kind)
        if add is not None and prop not in props_added:
            changes.append(add)
            props_added.add(prop)
            scene.doc.setdefault("propDefs", {})[prop] = add["def"]  # the working scene knows it from here on
        name = next_name(scene, CATALOG[kind]["label"], taken_names)
        taken_names.add(name)
        oid = new_id(scene, taken_ids)
        taken_ids.add(oid)
        z = scene.support_z(x, y, set())
        obj = {"id": oid, "prop": prop, "name": name, "category": CATALOG[kind]["category"], "aliases": [],
               "pos": [round(x, 3), round(y, 3), round(z, 3)], "rot": [0, 0, round(yaw, 3)], "rz": round(yaw, 3), "scale": [1, 1, 1],
               "color": None, "addedBy": "lightai"}
        if parent:
            obj["parent"] = parent
        changes.append({"op": "add", "object": obj})
        scene.objects.append(dict(obj))  # later things in this command keep clear of it
        made.append(name)
        return name

    if along:
        box, ref_name = scene.ref_box(place.value["ref"])
        room_cy = float(scene.room.get("depth", 432)) / 2
        front = box.cy < room_cy  # the side of the ref that faces the middle of the room
        y = (box.y1 + half_d + 12) if front else (box.y0 - half_d - 12)
        n_groups = max(1, max((n for k, n, _ in items if k not in ("stool", "chair")), default=1))
        span = box.w - 2 * half_w
        xs = [box.cx] if n_groups == 1 else [box.x0 + half_w + span * i / (n_groups - 1) for i in range(n_groups)]
        single = [(k, 1) for k, n, _ in items if k not in ("stool", "chair")][:1] or [(items[0][0], 1)]
        seats = [(k, n) for k, n, _ in items if k in ("stool", "chair")]
        per = [(single[0][0], 1)] + [(k, max(1, n // n_groups)) for k, n in seats]
        glayout, gw, gd = layout_group(scene, per)
        y = (box.y1 + gd + 12) if front else (box.y0 - gd - 12)
        span = box.w - 2 * gw
        xs = [box.cx] if n_groups == 1 else [box.x0 + gw + span * i / (n_groups - 1) for i in range(n_groups)]
        for gx in xs:
            parent = None
            gx, gy = scene.spot_at(gx, y, gw, gd, set(), avoid_floor=True)  # clear of the stools and tables already there
            for kind, dx, dy, yaw in glayout:
                nm = add_one(kind, gx + dx, gy + dy, yaw, parent if kind in ("stool", "chair") else None)
                if parent is None and kind not in ("stool", "chair"):
                    parent = nm
    else:
        per_centre = [layout] if not both else [layout] * 2
        for (cx, cy, _), lay in zip(centres, per_centre):
            parent = None
            for kind, dx, dy, yaw in lay:
                nm = add_one(kind, cx + dx, cy + dy, yaw, parent if kind in ("stool", "chair") else None)
                if parent is None and kind not in ("stool", "chair"):
                    parent = nm
    counts = {}
    for k, n, _ in items:
        counts[k] = counts.get(k, 0) + n
    what = " and ".join(f"{'a' if n == 1 else n} {CATALOG[k]['label'].lower()}{'s' if n > 1 else ''}" for k, n in counts.items())
    new_props = [c["prop"] for c in changes if c["op"] == "add_prop"]
    if new_props:
        assumptions.append({"fact": "drawn with simple shapes (new to this show): " + ", ".join(new_props), "source": "3D stage"})
    return _stage_plan(planner, "scene.add", scene, path, changes, f"Add {what} {where} ({', '.join(made)})", assumptions, [],
                       [Ref("object", c["object"]["id"]) for c in changes if c["op"] == "add"])


def plan_remove(planner, cmd):
    scene, path = scene_of(planner)
    refs, _ = refs_of(scene, cmd, allow_fixtures=False)
    if not refs:
        raise SceneError("Which object? e.g. 'table 3', 'bar stool 4', 'the high tops by the bar'")
    place = cmd.first("place")
    if place and place.value.get("ref") and len(refs) > 1:  # 'the stools by the bar': only those near it
        box, where = scene.ref_box(place.value["ref"])
        near = Box(box.x0 - 72, box.x1 + 72, box.y0 - 72, box.y1 + 72)
        parent_name = where.lower()
        refs = [r for r in refs if (scene.obj(r.id) or {}).get("parent", "").lower() == parent_name
                or near.contains(scene.box(r).cx, scene.box(r).cy)]
        if not refs:
            raise SceneError(f"none of those are {place.raw}")
    refs, kids = with_children(scene, refs, cmd.text)
    changes = remove_changes(scene, [r.id for r in refs])
    return _stage_plan(planner, "scene.remove", scene, path, changes, f"Remove {_group_label(scene, refs, kids)}", [], [], refs)
