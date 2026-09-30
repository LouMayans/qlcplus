"""The briefing Claude gets for a design request, written to a file and passed with --append-system-prompt-file
(Windows caps a command line at ~32,000 characters). It holds the rules, the rig and its capabilities, the room
map from the 3D stage, the recipe catalog, the operator's taste, the mood vocabulary and the knowledge-base files
to read, so Claude can only use what lightai can build."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from lightai.compiler.recipes import RECIPES
from lightai.rig.stage import ORDERS

KB_FILES = [
    ".claude/memory/lighting/lightshow-design-principles.md",
    ".claude/memory/lighting/effect-recipes-cookbook.md",
    ".claude/memory/lighting/fixture-types-and-roles.md",
    ".claude/memory/lighting/club-rig-mayans.md",
]
REPO_ROOT = Path(__file__).resolve().parents[3]
MOODS = REPO_ROOT / "lightai" / "knowledge" / "moods.yaml"

EXAMPLE = {
    "shows": [{
        "title": "Velvet Pulse", "description": "slow pastel morph under soft strobe bursts", "mood_tags": ["dreamy"],
        "bpm": 124,
        "sections": [
            {"name": "Float", "bars": 16, "transition_fade_ms": 3000, "looks": [
                {"layer": "base", "recipe": "color_morph", "targets": ["washes", "pars"], "colors": ["lavender", "pink"],
                 "intensity": 0.6, "rate": "slow", "order": "left_to_right"},
                {"layer": "movement", "recipe": "circle_wave", "targets": ["spots"], "colors": ["cyan"],
                 "rate": "slow", "size": "small", "order": "center_out"}]},
            {"name": "Flicker", "bars": 8, "transition_fade_ms": 500, "looks": [
                {"layer": "strobe", "recipe": "strobe_burst", "targets": ["spots"], "colors": ["white", "lavender"], "rate": "fast"},
                {"layer": "position", "recipe": "position", "targets": ["spots"], "aim": "dance floor", "spread": "cross"}]},
        ]}],
    "notes": "two sections; spots cross over the floor during the bursts",
    "new_terms": [],
}


def rig_summary(rig) -> str:
    lines = ["## The rig (only these fixtures exist)"]
    by_model: dict = {}
    for fx in rig.fixtures.values():
        by_model.setdefault(fx.key, []).append(fx)
    for key, fxs in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        fx = fxs[0]
        can = [c for c, ok in (("dimmer", fx.has("dimmer")), ("strobe", bool(fx.caps.get("shutter")) or fx.has("shutter")),
                               ("colour wheel", fx.color_mode == "wheel"), ("RGB colour", fx.color_mode == "rgb"),
                               ("pan/tilt", fx.can_move)) if ok]
        lines.append(f"- {key} x{len(fxs)} ({fx.kind}): {', '.join(can) or 'no dimmer or colour'}; e.g. {fxs[0].name}")
    lines.append("")
    lines.append("## Fixture phrases you may use in `targets`")
    lines.append("Zones: " + "; ".join(f"{z.replace('_', ' ')} ({len(ids)})" for z, ids in sorted(rig.zones.items()) if ids))
    groups = rig.stage_groups()
    if groups:
        lines.append("Stage groups: " + "; ".join(f"{g} ({len(ids)})" for g, ids in groups.items()))
    lines.append("Also: a model name (e.g. 'vpars', 'swarms'), a maker, a fixture name, or '<zone> in the front row' / "
                 "'<zone> near the dj' / '<zone> over the dance floor'.")
    lines.append("")
    lines.append("## Colour names (use only these, exactly)")
    lines.append(", ".join(rig.colors.names()))
    return "\n".join(lines)


def recipe_catalog() -> str:
    lines = ["## Recipes (the only effects lightai can build)"]
    for key in sorted(RECIPES):
        d = RECIPES[key].describe()
        lines.append(f"- {key}: {d['what'] or d['family']} [needs {d['needs']}; options {', '.join(d['options'])}]")
    lines += [
        "",
        "Options: rate = very_slow | slow | medium | fast | very_fast (tied to the BPM); fade_ms 0-20000; "
        "size (movement) = tiny | small | medium | big | huge; intensity 0-1; strobe_speed 0-1; mirror true/false; "
        f"order = {' | '.join(ORDERS)} (real positions from the 3D stage); aim = a place or 'down'; "
        "spread (position) = together | fan | cross.",
        "Layers of one section run together: base (colour wash / morph), movement (EFX), position (aim), "
        "fx, strobe, accent. Colour-wheel fixtures snap between colours (they can't crossfade).",
    ]
    return "\n".join(lines)


def taste_summary(prefs) -> str:
    if prefs is None:
        return ""
    palette = list(getattr(prefs, "palette", None) or [])
    lines = ["## The operator's taste"]
    if palette:
        lines.append("House palette: " + ", ".join(palette))
    state = getattr(prefs, "state", None) or {}
    scores = state.get("recipe_scores") or {}
    if scores:
        liked = [k for k, v in sorted(scores.items(), key=lambda kv: -kv[1]) if v > 0][:5]
        disliked = [k for k, v in sorted(scores.items(), key=lambda kv: kv[1]) if v < 0][:5]
        if liked:
            lines.append("Liked recipes: " + ", ".join(liked))
        if disliked:
            lines.append("Disliked recipes: " + ", ".join(disliked))
    return "\n".join(lines) if len(lines) > 1 else ""


def build_context(rig, prefs, *, count: int = 1, minutes: Optional[float] = None, positions_final: bool = False,
                  references: str = "") -> str:
    parts = [
        "# You are the show designer for lightai, the lighting assistant of a nightclub running QLC+.",
        "Design creative, long-running light shows for THIS rig. Your answer is a DesignResult JSON (the schema is "
        "enforced). lightai validates it, builds every look with its own compiler and writes it to QLC+; you never "
        "write XML or give fixture/function IDs.",
        "",
        "## Rules",
        f"- Make exactly {count} show(s), clearly different from each other (palette, recipes, spatial orders, pacing).",
        "- Use only the recipes, fixture phrases, places and colour names described below. Anything else is "
        "rejected and sent back to you.",
        "- Build each section from 2-4 layers following the 5-layer look stack (base / movement / fx / strobe / "
        "accent); vary energy across sections (energy arcs, contrast, restraint), and use the room: spatial orders "
        "(left_to_right, center_out, circular...), mirror, aims at places.",
        ("- The operator asked for a length: set `minutes` on each show; sections are scaled to fill it."
         if minutes else "- Shows loop until stopped: don't set `minutes`. Sections are 4-32 bars."),
        "- Before designing, read the knowledge-base files listed below with the Read tool. Research on the web only "
        "when the request uses a style you don't know; put any new word in `new_terms`.",
        "",
        rig_summary(rig),
        "",
    ]
    stage = rig.stage
    if stage is not None:
        parts += ["## The room (3D stage)", stage.summary(rig, positions_final), ""]
    parts += [recipe_catalog(), ""]
    taste = taste_summary(prefs)
    if taste:
        parts += [taste, ""]
    if MOODS.exists():
        parts += ["## Mood vocabulary (lightai/knowledge/moods.yaml)", MOODS.read_text(encoding="utf-8").strip(), ""]
    if references:
        parts += ["## Reference shows", references, ""]
    parts += ["## Knowledge base to read first (paths from the repo root)"] + [f"- {p}" for p in KB_FILES] + [""]
    parts += ["## Example of a valid answer (shape only; design your own)", json.dumps(EXAMPLE, indent=1)]
    return "\n".join(parts)


def request_prompt(text: str, count: int, minutes: Optional[float]) -> str:
    extra = f" Make {count} different show(s)." if count > 1 else ""
    length = f" Each show lasts {minutes:g} minutes." if minutes else ""
    return f"Design request from the operator: {text.strip()}{extra}{length}"


def write_context(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
