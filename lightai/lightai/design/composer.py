"""Turn a validated design into QLC+ functions with the existing compiler.

Each layer is a look built by the look recipes; each section is a Collection of its layers (they run together);
each show is one top-level Chaser that steps through its sections, holding each for the section's length with the
section's crossfade. A show loops until stopped, unless it was asked for with a length ("a 5-minute show"): then it
plays once, stretched to that length. Phrases are resolved against the current show every time, so IDs are fresh.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from lightai.compiler.recipes import DEFAULT_BPM, build_look
from lightai.compiler.spec import FunctionSpec, LookParams, ShowItemSpec, StepSpec, TrackSpec
from lightai.design.spec import DesignResult, Section, ShowSpec
from lightai.design.validate import resolve_targets


LAYER_COLORS = {"base": "#4a5a8c", "movement": "#6c4a8c", "position": "#4a8c7a", "fx": "#8c6c4a", "strobe": "#c0c0c0",
                "accent": "#8c4a5a"}


@dataclass
class ComposedShow:
    title: str
    main_id: int
    kind: str  # "loop" (a Chaser of section Collections) | "timeline" (a QLC+ Show)
    functions: list
    sections: list
    spec: dict
    assumptions: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    facts_used: list = field(default_factory=list)
    groups: list = field(default_factory=list)  # new <FixtureGroup>s for pixel layers (own ID namespace, not in ids)

    @property
    def ids(self) -> list:
        return [f.id for f in self.functions]

    def summary(self) -> dict:
        return {"title": self.title, "main_id": self.main_id, "kind": self.kind, "functions": len(self.functions),
                "ids": [self.ids[0], self.ids[-1]] if self.ids else [], "sections": self.sections,
                "assumptions": self.assumptions[:20], "skipped": self.skipped[:20]}


def section_ms(section: Section, bpm: float) -> int:
    if section.seconds:
        return int(round(section.seconds * 1000))
    return int(round((section.bars or 8) * 4 * 60000.0 / bpm))


def _path(title: str) -> str:
    return "AI/Shows/" + (re.sub(r"[/\\]+", " ", title).strip() or "Show")


def compose_show(rig, show: ShowSpec, cursor: int, used: set, bpm: Optional[float] = None,
                 groups: Optional[list] = None) -> ComposedShow:
    sbpm = float(show.bpm or bpm or DEFAULT_BPM)
    path = _path(show.title)
    functions, sections, assumptions, skipped, facts = [], [], [], [], set()
    groups = groups if groups is not None else []
    new_groups: list = []
    for section in show.sections:
        members, layers = [], []
        for look in section.looks:
            ids, bad = resolve_targets(rig, look.targets)
            if not ids:
                raise ValueError(f"'{show.title}' / {section.name}: {', '.join(bad)} matched no fixtures")
            p = LookParams(recipe=look.recipe, targets=ids, target_label=", ".join(look.targets), colors=list(look.colors),
                           intensity=look.intensity, bpm=sbpm, rate_word=look.rate, fade_ms=look.fade_ms,
                           strobe_speed=look.strobe_speed, size=look.size, priority=look.priority, mirror=look.mirror,
                           name=f"{show.title} - {section.name} - {look.layer}", order=look.order, aim=look.aim,
                           spread=look.spread, layer=look.layer)
            built = build_look(rig, p, start_id=cursor, reserved=used, groups=groups)
            new_groups += built.groups
            for f in built.functions:
                f.path = path
                used.add(f.id)
            cursor = max(used) + 1
            functions.extend(built.functions)
            members.append(built.main_id)
            assumptions += [dict(a, where=f"{section.name} / {look.layer}") for a in built.assumptions]
            skipped += [dict(s, where=f"{section.name} / {look.layer}") for s in built.skipped]
            facts |= set(built.facts_used)
            layers.append({"layer": look.layer, "recipe": look.recipe, "targets": look.targets, "fixtures": len(ids),
                           "look_id": built.main_id, "functions": len(built.functions),
                           **{k: v for k, v in (("order", look.order), ("aim", look.aim), ("colors", look.colors)) if v}})
        coll_id = None
        if not show.minutes:  # a loop steps through section Collections; a timeline places the layers itself
            coll = FunctionSpec(id=cursor, type="Collection", name=f"{show.title} - {section.name}", path=path, members=members)
            used.add(cursor)
            cursor += 1
            functions.append(coll)
            coll_id = coll.id
        sections.append({"name": section.name, "ms": section_ms(section, sbpm), "fade_ms": section.transition_fade_ms,
                         "collection_id": coll_id, "layers": layers})
    kind = "timeline" if show.minutes else "loop"
    if show.minutes:  # asked for a length: a QLC+ timeline Show, one track per layer, sections stretched to fill it
        total = sum(s["ms"] for s in sections) or 1
        k = show.minutes * 60000.0 / total
        for s in sections:
            s["ms"] = int(round(s["ms"] * k))
        tracks: dict = {}
        start = 0
        for s in sections:
            for layer in s["layers"]:
                tracks.setdefault(layer["layer"], []).append(ShowItemSpec(layer["look_id"], start_ms=start, duration_ms=s["ms"],
                                                                          color=LAYER_COLORS.get(layer["layer"])))
            s["start_ms"] = start
            start += s["ms"]
        main = FunctionSpec(id=cursor, type="Show", name=show.title, path="AI/Shows", time_division=("BPM_4_4", int(round(sbpm))),
                            tracks=[TrackSpec(id=i, name=name.title(), items=items) for i, (name, items) in enumerate(tracks.items())])
        assumptions.append({"fact": f"a {show.minutes:g}-minute timeline, one track per layer (sections scaled x{k:.2f})", "source": "design length"})
    else:
        main = FunctionSpec(id=cursor, type="Chaser", name=show.title, path="AI/Shows",
                            steps=[StepSpec(s["collection_id"], fade_in=s["fade_ms"], hold=max(0, s["ms"] - s["fade_ms"]), fade_out=0)
                                   for s in sections],
                            run_order="Loop", speed_modes=("PerStep", "PerStep", "PerStep"))
    used.add(cursor)
    functions.append(main)
    return ComposedShow(title=show.title, main_id=main.id, kind=kind, functions=functions, sections=sections,
                        spec=show.model_dump(), assumptions=assumptions, skipped=skipped, facts_used=sorted(facts),
                        groups=new_groups)


def compose_design(rig, result: DesignResult, show_indexes: Optional[list] = None, bpm: Optional[float] = None) -> list:
    """All (or the chosen) shows of a design, with one contiguous block of new function IDs, validated together."""
    from lightai.compiler import CompileError
    from lightai.compiler.validate import validate_specs

    cursor, used, out, groups = rig.next_function_id(), set(), [], []
    for si, show in enumerate(result.shows):
        if show_indexes is not None and si not in set(show_indexes):
            continue
        composed = compose_show(rig, show, cursor, used, bpm, groups=groups)
        cursor = max(used) + 1
        out.append(composed)
    specs = [g for c in out for g in c.groups] + [f for c in out for f in c.functions]
    errors, warnings = validate_specs(rig, specs, set(rig.functions))
    if errors:
        raise CompileError(f"the design failed validation: {errors[0]}", errors)
    for c in out:
        c.assumptions += [{"fact": w, "source": "validator warning"} for w in warnings]
    return out
