"""Mood words in an ordinary command ('dreamy blue wash', 'something hypnotic on the spots'): the ranges in
knowledge/moods.yaml fill in only what the operator didn't say. Offline and deterministic; the same file briefs the
Claude designer, and research runs extend it."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

MOODS_FILE = Path(__file__).resolve().parents[2] / "knowledge" / "moods.yaml"
_CACHE: dict = {}


def load_moods(path: Path = MOODS_FILE) -> dict:
    try:
        st = path.stat()
    except OSError:
        return {}
    key = (st.st_size, st.st_mtime_ns)
    hit = _CACHE.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    try:
        moods = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("moods") or {}
    except (OSError, yaml.YAMLError):
        moods = {}
    _CACHE[str(path)] = (key, moods)
    return moods


def find_mood(text: str, moods: Optional[dict] = None) -> Optional[tuple]:
    """(name, entry, word) for the longest mood word in the text, e.g. 'hypnotic' or 'build up'."""
    moods = load_moods() if moods is None else moods
    low = text.lower()
    best = None
    for name, entry in moods.items():
        for word in [name] + list(entry.get("words") or []):
            w = str(word).lower().strip()
            if w and re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low) and (best is None or len(w) > len(best[2])):
                best = (name, entry, w)
    return best


def _mid(r) -> Optional[float]:
    if isinstance(r, (list, tuple)) and r:
        return (float(r[0]) + float(r[-1])) / 2
    return float(r) if isinstance(r, (int, float)) else None


def apply_mood(params, mood: tuple, explicit: dict, rig=None) -> list:
    """Fill the parts of a look the operator didn't say from the mood; returns the assumptions to show."""
    from lightai.compiler.recipes import RECIPES

    name, entry, word = mood
    said = []
    if not explicit.get("recipe"):
        movers = rig is not None and any(rig.fixtures[i].can_move for i in params.targets if i in rig.fixtures)
        for r in entry.get("recipes") or []:
            cls = RECIPES.get(r)
            if cls is not None and (movers or not cls.needs_movement):
                if r != params.recipe:
                    params.recipe = r
                    said.append(f"effect {r}")
                break
    if not explicit.get("rate") and entry.get("rate"):
        params.rate_word = entry["rate"][0]
        said.append(f"speed {params.rate_word}")
    if not explicit.get("fade") and entry.get("fade_ms"):
        params.fade_ms = int(_mid(entry["fade_ms"]))
        said.append(f"{params.fade_ms / 1000:g} s fades")
    if not explicit.get("colors") and entry.get("palette"):
        known = [c for c in entry["palette"] if rig is None or rig.colors.canonical(c)]
        if known:
            params.colors = known[:3]
            said.append("colors " + ", ".join(params.colors))
    if not explicit.get("intensity") and entry.get("intensity") is not None:
        params.intensity = round(_mid(entry["intensity"]), 2)
        said.append(f"{params.intensity:.0%} intensity")
    if not explicit.get("size") and entry.get("movement_size"):
        params.size = entry["movement_size"][0]
    if not params.order and entry.get("orders"):
        params.order = entry["orders"][0]
        said.append(params.order.replace("_", " "))
    return [{"fact": f"mood '{name}' (you said '{word}'): " + ", ".join(said), "source": "knowledge/moods.yaml"}] if said else []
