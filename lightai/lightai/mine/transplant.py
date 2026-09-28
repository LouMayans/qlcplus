"""Transplant: abstract a function from someone else's show into recipe parameters, then compile
it on this rig. Nothing is copied channel-for-channel; the idea (movement, colors, timing) moves over.
"""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path
from typing import Optional

from lightai.compiler import compile_look, write_look
from lightai.compiler.recipes import EFX_SIZE
from lightai.compiler.spec import LookParams
from lightai.config import load_config
from lightai.mine import foreign_rig
from lightai.nlu.normalize import TargetResolver
from lightai.rig.model import Rig
from lightai.rig.qxf import kid, kids, text_of

ALG_RECIPE = {"Circle": "circle", "Eight": "figure8", "Lissajous": "ballyhoo", "Line": "sweep", "Line2": "sweep",
              "Diamond": "circle", "Square": "circle", "SquareChoppy": "circle", "SquareTrue": "circle", "Leaf": "circle"}
KIND_ZONE = {"spot": "spots", "wash": "washes", "par": "pars", "rgb": "led_walls", "bar": "tetras", "dimmer": "white_panels"}


def _nearest_size(width: int) -> str:
    return min(EFX_SIZE, key=lambda k: abs(EFX_SIZE[k] - width))


def _scene_colors(rig: Rig, fid: int) -> tuple:
    f = rig.functions.get(fid)
    colors, levels, kinds = Counter(), [], Counter()
    if f is None:
        return colors, levels, kinds
    for fxid, pairs in f.values.items():
        fx = rig.fixtures.get(fxid)
        if fx is None:
            continue
        vals = dict(pairs)
        kinds[fx.kind] += 1
        if fx.has("dimmer") and fx.roles["dimmer"] in vals:
            levels.append(vals[fx.roles["dimmer"]] / 255.0)
        if fx.color_mode == "rgb" and all(r in fx.roles for r in ("red", "green", "blue")):
            rgb = tuple(vals.get(fx.roles[r], 0) for r in ("red", "green", "blue"))
            if max(rgb) > 30:
                colors[rig.colors.nearest(rgb, rig.colors.names())] += 1
        elif fx.color_mode == "wheel" and fx.roles.get("color_wheel") in vals:
            inv = {v: c for c, v in (fx.caps.get("wheel") or {}).items()}
            c = inv.get(vals[fx.roles["color_wheel"]])
            if c:
                colors[c] += 1
    return colors, levels, kinds


def abstract_function(rig: Rig, fid: int) -> dict:
    el = next((e for e in rig.ws.function_els() if int(e.get("ID", "-1")) == fid), None)
    if el is None:
        raise KeyError(f"function {fid} not found in that show")
    t = el.get("Type")
    out: dict = {"source_function": {"id": fid, "type": t, "name": el.get("Name")}, "kinds": Counter(), "colors": []}
    sp = kid(el, "Speed")
    dur = int(sp.get("Duration", "0")) if sp is not None else 0
    if t == "EFX":
        alg = text_of(el, "Algorithm", "Circle")
        offs = [int(text_of(fx, "StartOffset", "0") or 0) for fx in kids(el, "Fixture")]
        recipe = ALG_RECIPE.get(alg, "circle")
        if recipe == "circle" and offs and max(offs) - min(offs) > 0:
            recipe = "circle_wave"
        out.update(recipe=recipe, period_ms=dur or None, size=_nearest_size(int(text_of(el, "Width", "60") or 60)),
                   mirror=any(text_of(fx, "Direction") == "Backward" for fx in kids(el, "Fixture")))
        for fx in kids(el, "Fixture"):
            f = rig.fixtures.get(int(text_of(fx, "ID", "-1")))
            if f:
                out["kinds"][f.kind] += 1
    elif t == "Chaser":
        steps = [int(s.text) for s in kids(el, "Step") if (s.text or "").strip().isdigit()]
        step_colors, lit_sets = [], []
        for s in steps:
            c, _, k = _scene_colors(rig, s)
            out["kinds"].update(k)
            if c:
                step_colors.append(c.most_common(1)[0][0])
            f = rig.functions.get(s)
            if f is not None:
                lit_sets.append(frozenset(f.values))
        modes = kid(el, "SpeedModes")
        if modes is not None and modes.get("Duration") == "PerStep":
            holds = [int(s.get("Hold", "0")) + int(s.get("FadeIn", "0")) for s in kids(el, "Step")]
            dur = int(statistics.median(holds)) if holds else dur
        if len(set(lit_sets)) > 2 and all(len(x) <= 2 for x in lit_sets):
            out.update(recipe="running_light", period_ms=dur or None)
        else:
            out.update(recipe="color_chase", period_ms=dur or None)
        out["colors"] = list(dict.fromkeys(step_colors))
    elif t == "Scene":
        c, levels, k = _scene_colors(rig, fid)
        out["kinds"].update(k)
        out.update(recipe="color_wash", colors=[x for x, _ in c.most_common(3)], intensity=round(statistics.median(levels), 2) if levels else 1.0,
                   fade_ms=int(sp.get("FadeIn", "0")) if sp is not None else 0)
    elif t == "Collection":
        members = [int(s.text) for s in kids(el, "Step") if (s.text or "").strip().isdigit()]
        best, rank = None, 99
        order = ["circle_wave", "ballyhoo", "figure8", "sweep", "circle", "running_light", "color_chase"]
        for m in members:
            try:
                sub = abstract_function(rig, m)
            except (KeyError, ValueError):
                continue
            r = order.index(sub["recipe"]) if sub.get("recipe") in order else 99
            if r < rank:
                best, rank = sub, r
            out["colors"] += sub.get("colors", [])
            out["kinds"].update(sub.get("kinds", {}))
        if best:
            out.update({k: v for k, v in best.items() if k not in ("colors", "kinds", "source_function")})
        else:
            out["recipe"] = "color_wash"
        out["colors"] = list(dict.fromkeys(out["colors"]))
    else:
        raise ValueError(f"{t} functions can't be transplanted yet (Scene, Chaser, EFX, Collection only)")
    return out


def transplant_main(args) -> int:
    cfg = load_config()
    src = foreign_rig(Path(args.qxw), cfg)
    fid = int(args.function) if str(args.function).isdigit() else next(
        (f.id for f in src.functions.values() if f.name.lower() == str(args.function).lower()), None)
    if fid is None:
        print(f"no function named {args.function!r} in {args.qxw}")
        return 2
    idea = abstract_function(src, fid)
    rig = Rig.load(cfg)
    if args.targets:
        res = TargetResolver(rig).resolve(args.targets)
        targets, label = res.get("fixture_ids", []), args.targets
    else:
        kind = idea["kinds"].most_common(1)[0][0] if idea["kinds"] else "spot"
        zone = KIND_ZONE.get(kind, "all")
        targets, label = rig.zones.get(zone, []), zone.replace("_", " ")
    if not targets:
        print("no target fixtures on this rig; pass --targets")
        return 2
    params = LookParams(recipe=idea["recipe"], targets=targets, target_label=label, colors=idea.get("colors") or ["white"],
                        intensity=float(idea.get("intensity", 1.0)), period_ms=idea.get("period_ms"), fade_ms=idea.get("fade_ms"),
                        size=idea.get("size"), mirror=bool(idea.get("mirror")),
                        name=f"{idea['source_function']['name']} (transplant)")
    look = compile_look(rig, params)
    print(f"source: {idea['source_function']}  kinds={dict(idea['kinds'])}")
    print(f"idea:   recipe={idea['recipe']} colors={params.colors} period_ms={params.period_ms} size={params.size} mirror={params.mirror}")
    print(f"look:   '{look.name}' -> {len(look.functions)} functions {look.ids} on {len(targets)} fixtures ({label})")
    for a in look.assumptions:
        print(f"        assume: {a['fact']}")
    if args.yes:
        res = write_look(rig, look, cfg.project_path, text=f"transplant {args.qxw}#{fid}", source="transplant")
        print(f"written: {res}")
    else:
        print("dry run: add --yes to write it into the show (backup first)")
    return 0
