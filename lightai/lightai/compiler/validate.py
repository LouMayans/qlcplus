"""Validation of generated functions and of whole workspaces (what QLC+ would reject or mangle)."""

from __future__ import annotations

from lightai.compiler.spec import FunctionSpec
from lightai.rig.model import Rig
from lightai.rig.qxf import kid, kids, local
from lightai.rig.qxw import Workspace

# Script algorithm names QLC+ ships (resources/rgbscripts/*.js's `algo.name`). An RGBMatrix
# <Algorithm Type="Script"> whose name isn't in this list won't be found in the scripts cache.
KNOWN_RGB_SCRIPTS = {
    "Alternate", "Balls", "Blinder", "Circles", "Circular", "Even/Odd", "Fill", "Fill From Center",
    "Fill Unfill", "Fill Unfill From Center", "Fill Unfill Squares From Center", "Fireworks",
    "Flying Objects", "Gradient", "Lines", "Marquee", "Noise", "One By One", "Opposite", "Plasma",
    "Random Column", "Random Fill Column", "Random Fill Row", "Random Fill Single",
    "Random Pixel Per Row", "Random Pixel Per Row Multicolor", "Random Row", "Random Single",
    "Sine Wave", "Snow or Bubbles", "Squares", "Squares From Center", "3D Starfield", "Stripes",
    "Stripes From Center", "Strobe", "Vertical fall", "Waves",
}


def validate_specs(rig: Rig, specs: list, existing_ids: set) -> tuple:
    errors: list = []
    warnings: list = []
    # FixtureGroup IDs are a separate namespace from Function IDs in QLC+ (Doc keeps independent
    # counters), so they're bookkept separately here rather than folded into new_ids/known below.
    func_specs = [s for s in specs if s.type != "FixtureGroup"]
    group_specs = [s for s in specs if s.type == "FixtureGroup"]
    new_ids = [s.id for s in func_specs]
    if len(new_ids) != len(set(new_ids)):
        errors.append("duplicate function IDs inside the new look")
    clash = set(new_ids) & set(existing_ids)
    if clash:
        errors.append(f"function IDs already used in the show: {sorted(clash)}")
    known = set(existing_ids) | set(new_ids)
    existing_group_ids = {g.id for g in rig.ws.fixture_groups()}
    new_group_ids = [s.id for s in group_specs]
    if len(new_group_ids) != len(set(new_group_ids)):
        errors.append("duplicate fixture group IDs inside the new look")
    gclash = set(new_group_ids) & existing_group_ids
    if gclash:
        errors.append(f"fixture group IDs already used in the show: {sorted(gclash)}")
    known_groups = existing_group_ids | set(new_group_ids)
    for s in specs:
        if not s.name:
            errors.append(f"function {s.id} has no name")
        if s.type == "Scene":
            if not s.values:
                errors.append(f"scene {s.id} '{s.name}' sets no channels")
            for fid, pairs in s.values.items():
                fx = rig.fixtures.get(fid)
                if fx is None:
                    errors.append(f"scene {s.id} uses unknown fixture {fid}")
                    continue
                for ch, val in pairs:
                    if not 0 <= ch < fx.channels:
                        errors.append(f"scene {s.id}: fixture {fid} has no channel {ch}")
                    if not 0 <= val <= 255:
                        errors.append(f"scene {s.id}: value {val} out of range on fixture {fid} ch {ch}")
                    role = (s.roles.get(fid) or {}).get(ch)
                    if role is None:
                        errors.append(f"scene {s.id}: fixture {fid} ch {ch} written without a role")
                    elif fx.roles.get(role) != ch:
                        errors.append(
                            f"scene {s.id}: fixture {fid} ch {ch} was written as '{role}' but that role is ch {fx.roles.get(role)}"
                        )
        elif s.type in ("Chaser", "Collection"):
            refs = s.refs()
            if not refs:
                errors.append(f"{s.type.lower()} {s.id} has no steps")
            for r in refs:
                if r == s.id:
                    errors.append(f"{s.type.lower()} {s.id} references itself")
                elif r not in known:
                    errors.append(f"{s.type.lower()} {s.id} references missing function {r}")
            if s.type == "Chaser" and s.duration <= 0 and s.speed_modes[2] == "Common":
                errors.append(f"chaser {s.id} has zero step duration")
        elif s.type == "EFX":
            if not s.efx_fixtures:
                errors.append(f"EFX {s.id} has no fixtures")
            for ef in s.efx_fixtures:
                fx = rig.fixtures.get(ef.id)
                if fx is None:
                    errors.append(f"EFX {s.id} uses unknown fixture {ef.id}")
                elif not fx.can_move:
                    errors.append(f"EFX {s.id}: fixture {ef.id} has no pan/tilt")
                if not 0 <= ef.start_offset <= 359:
                    errors.append(f"EFX {s.id}: start offset {ef.start_offset} out of 0-359")
            e = s.efx
            for key in ("width", "height"):
                if not 0 <= int(e.get(key, 0)) <= 127:
                    errors.append(f"EFX {s.id}: {key} must be 0-127")
            if s.duration <= 0:
                errors.append(f"EFX {s.id} has zero duration")
            if len(s.efx_fixtures) > 2:
                offsets = sorted(ef.start_offset for ef in s.efx_fixtures)
                spread = offsets[-1] - offsets[0]
                limit = int(rig.rules.get("efx_max_phase_spread_deg", 180))
                if spread > limit:
                    warnings.append(f"EFX {s.id}: phase spread {spread} deg exceeds the rig rule ({limit})")
        elif s.type == "FixtureGroup":
            if not s.group_heads:
                errors.append(f"fixture group {s.id} '{s.name}' has no heads")
            gx, gy = s.group_size
            for h in s.group_heads:
                if rig.fixtures.get(h.fixture) is None:
                    errors.append(f"fixture group {s.id} '{s.name}' uses unknown fixture {h.fixture}")
                if not (0 <= h.x < gx and 0 <= h.y < gy):
                    errors.append(f"fixture group {s.id} '{s.name}': head at ({h.x},{h.y}) is outside its {gx}x{gy} grid")
        elif s.type == "RGBMatrix":
            m = s.matrix
            gid = m.get("fixture_group")
            if gid is None or gid not in known_groups:
                errors.append(f"RGBMatrix {s.id} '{s.name}' references missing fixture group {gid}")
            algo_type = m.get("algorithm_type", "Script")
            algo_name = m.get("algorithm_name", "")
            if algo_type == "Script":
                if not algo_name:
                    errors.append(f"RGBMatrix {s.id} '{s.name}' has no script algorithm name")
                elif algo_name not in KNOWN_RGB_SCRIPTS:
                    errors.append(f"RGBMatrix {s.id} '{s.name}': unknown RGB script '{algo_name}'")
            if not m.get("colors"):
                errors.append(f"RGBMatrix {s.id} '{s.name}' sets no colors")
            if m.get("control_mode", "RGB") not in ("RGB", "Amber", "White", "UV", "Dimmer", "Shutter"):
                errors.append(f"RGBMatrix {s.id} '{s.name}': unknown control mode {m.get('control_mode')!r}")
        elif s.type == "Show":
            if not s.tracks:
                errors.append(f"show {s.id} has no tracks")
            for r in s.refs():
                if r == s.id:
                    errors.append(f"show {s.id} references itself")
                elif r not in known:
                    errors.append(f"show {s.id} references missing function {r}")
            track_ids = [t.id for t in s.tracks]
            if len(track_ids) != len(set(track_ids)):
                errors.append(f"show {s.id} has duplicate track IDs")
            for t in s.tracks:
                for it in t.items:
                    if it.start_ms < 0:
                        errors.append(f"show {s.id} track {t.id}: item {it.function_id} has a negative start time")
                    if it.duration_ms < 0:
                        errors.append(f"show {s.id} track {t.id}: item {it.function_id} has a negative duration")
                items = sorted(t.items, key=lambda item: item.start_ms)
                for a, b in zip(items, items[1:]):
                    if a.start_ms + a.duration_ms > b.start_ms:
                        errors.append(
                            f"show {s.id} track {t.id}: item {a.function_id} ({a.start_ms}+{a.duration_ms}) overlaps "
                            f"item {b.function_id} (starts {b.start_ms})"
                        )
    return errors, warnings


def validate_workspace(ws: Workspace) -> tuple:
    """Structural checks QLC+ cares about. Returns (errors, warnings)."""
    errors: list = []
    warnings: list = []
    if "Workspace" not in (ws.tree.docinfo.doctype or ""):
        errors.append("missing <!DOCTYPE Workspace>")
    mon_top = kid(ws.root, "Monitor")
    if mon_top is not None:
        errors.append("<Monitor> sits directly under <Workspace>; QLC+ ignores it (it must be inside <Engine>)")
    mon = ws.monitor()
    if mon is not None and mon.get("DisplayMode") is None:
        errors.append("<Monitor> inside <Engine> lacks DisplayMode; QLC+ stops loading the rest of the file")
    ids = [f.id for f in ws.functions()]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        errors.append(f"duplicate function IDs: {dup}")
    known = set(ids)
    for f in ws.functions():
        for r in f.refs:
            if r not in known and r >= 0:
                warnings.append(f"function {f.id} '{f.name}' references missing function {r} (QLC+ drops it on load)")
    used: dict = {}
    for p in ws.fixtures():
        if p.address + p.channels > 512:
            errors.append(f"fixture {p.id} '{p.name}' runs past channel 512 (QLC+ resets it to address 0)")
        for ch in range(p.address, p.address + p.channels):
            key = (p.universe, ch)
            if key in used and used[key] != p.id:
                errors.append(f"fixture {p.id} '{p.name}' overlaps fixture {used[key]} at U{p.universe + 1} ch{ch + 1} (QLC+ drops it)")
                break
            used[key] = p.id
    bound = {int(f.get("BoundScene")) for f in kids(ws.engine, "Function") if f.get("BoundScene", "").isdigit()}
    for f in kids(ws.engine, "Function"):
        if f.get("Hidden") is not None and f.get("Type") == "Scene" and int(f.get("ID", "-1")) not in bound:
            warnings.append(f"scene {f.get('ID')} '{f.get('Name')}' is Hidden: QLC+ saves hidden scenes with all values 0")
    for el in ws.engine:
        if isinstance(el.tag, str) and local(el) == "Function" and not el.get("Name"):
            warnings.append(f"function {el.get('ID')} has an empty name")
    groups = ws.fixture_groups()
    group_ids = {g.id for g in groups}
    fixture_ids = {p.id for p in ws.fixtures()}
    for g in groups:
        for h in g.heads:
            if h["fixture"] not in fixture_ids:
                errors.append(f"fixture group {g.id} '{g.name}' references missing fixture {h['fixture']}")
    for f in ws.functions():
        if f.type == "RGBMatrix":
            gid = f.extra.get("fixture_group", -1)
            if gid not in group_ids:
                errors.append(f"RGBMatrix {f.id} '{f.name}' references missing fixture group {gid}")
    return errors, warnings
