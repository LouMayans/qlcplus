"""Check a design against the real show before anything is built: every phrase must resolve to real fixtures,
recipes come from the catalog, places exist in the 3D stage, colors are known. The errors go back to Claude for a
repair round, worded so it can fix them."""

from __future__ import annotations

from lightai.compiler.recipes import RECIPES
from lightai.design.spec import DesignResult, LayeredLook
from lightai.rig.stage import ORDERS

MAX_TOTAL_MINUTES = 90


def _label(si: int, show, ji: int, section, ki: int, look: LayeredLook) -> str:
    return f"show {si + 1} '{show.title}', section {ji + 1} '{section.name}', layer {ki + 1} ({look.layer} {look.recipe})"


def resolve_targets(rig, phrases: list) -> tuple:
    """Fixture IDs for target phrases, and the phrases that didn't resolve."""
    from lightai.nlu.normalize import normalize_slot

    ids, bad = [], []
    for ph in phrases:
        v = normalize_slot(rig, "target", ph)
        got = [i for i in v.get("fixture_ids") or [] if i in rig.fixtures]
        if got:
            ids.extend(i for i in got if i not in ids)
        else:
            bad.append(ph)
    return ids, bad


def validate_design(rig, result: DesignResult) -> tuple:
    """(errors, warnings, resolved) where resolved maps (show, section, layer) -> fixture IDs."""
    errors, warnings, resolved = [], [], {}
    stage = rig.stage
    zones = sorted(rig.zones)
    groups = sorted(rig.stage_groups())
    total_min = 0.0
    for si, show in enumerate(result.shows):
        for ji, section in enumerate(show.sections):
            bpm = show.bpm or 124
            ms = (section.seconds * 1000.0) if section.seconds else (section.bars or 8) * 4 * 60000.0 / bpm
            total_min += ms / 60000.0
            for ki, look in enumerate(section.looks):
                where = _label(si, show, ji, section, ki, look)
                cls = RECIPES.get(look.recipe)
                if cls is None:
                    errors.append(f"{where}: unknown recipe '{look.recipe}'; use one of {', '.join(sorted(RECIPES))}")
                    continue
                for c in look.colors:  # checked even when the targets fail, so one repair round fixes everything
                    if rig.colors.canonical(c) is None:
                        errors.append(f"{where}: unknown color '{c}'; use common color names (red, blue, pink, amber, lavender, ...)")
                ids, bad = resolve_targets(rig, look.targets)
                for ph in bad:
                    errors.append(f"{where}: '{ph}' is not a fixture group here; use zones ({', '.join(zones)}), "
                                  f"stage groups ({', '.join(groups) or 'none'}), models or fixture names")
                if not ids:
                    continue
                if cls.needs_movement and not any(rig.fixtures[i].can_move for i in ids):
                    errors.append(f"{where}: {look.recipe} needs moving heads, but {', '.join(look.targets)} can't move")
                    continue
                if look.order and look.order not in ORDERS:
                    errors.append(f"{where}: unknown order '{look.order}'; use one of {', '.join(ORDERS)}")
                if look.order and stage is None:
                    warnings.append(f"{where}: the show has no 3D stage file, so order '{look.order}' falls back to stage-map order")
                if look.aim and look.aim.lower() != "down":
                    if stage is None:
                        errors.append(f"{where}: can't aim at '{look.aim}': the show has no 3D stage file")
                    elif stage.place(look.aim) is None:
                        names = sorted({p.name for p in stage.places.values() if p.category not in ("decor", "seating")})
                        errors.append(f"{where}: '{look.aim}' is not a place in the stage; use one of {', '.join(names)} or 'down'")
                if look.recipe == "position" and not look.aim:
                    errors.append(f"{where}: position needs an aim (a place or 'down')")
                resolved[(si, ji, ki)] = ids
    if total_min > MAX_TOTAL_MINUTES:
        errors.append(f"the shows add up to {total_min:.0f} minutes of sections; keep them under {MAX_TOTAL_MINUTES}")
    return errors, warnings, resolved
