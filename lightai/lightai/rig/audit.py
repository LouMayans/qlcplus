"""`lightai audit`: regenerate rig facts from the files and report problems.
`lightai fix v3-color`: repair scenes/channel groups that drive a spot model on the wrong channel.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lightai.compiler.validate import validate_workspace
from lightai.compiler.writer import write_workspace
from lightai.config import load_config
from lightai.rig.model import Rig
from lightai.rig.qxf import kids
from lightai.rig.roles import channel_candidates, channel_group_role, pick_roles

CONTROL_ROLES = {"dimmer", "shutter", "color_wheel", "pan", "tilt", "pt_speed", "red", "green", "blue", "white", "amber", "uv",
                 "color_macro", "mode", "pan_fine", "tilt_fine", "gobo", "prism", "prism_rotation", "frost", "zoom", "iris", "focus"}


def role_of(fx, ch: int) -> Optional[str]:
    for r, c in fx.roles.items():
        if c == ch:
            return r
    return None


def scene_color_findings(rig: Rig) -> list:
    """Scenes where some spots get their color wheel but a model is driven on another channel instead."""
    out = []
    for f in rig.functions.values():
        if f.type != "Scene" or not f.values:
            continue
        spots = {fid: pairs for fid, pairs in f.values.items() if fid in rig.fixtures and rig.fixtures[fid].color_mode == "wheel"}
        if len(spots) < 2:
            continue
        with_color = {fid for fid, pairs in spots.items() if any(role_of(rig.fixtures[fid], ch) == "color_wheel" for ch, _ in pairs)}
        if not with_color:
            continue
        for fid, pairs in spots.items():
            if fid in with_color:
                continue
            fx = rig.fixtures[fid]
            odd = [(ch, v, role_of(fx, ch)) for ch, v in pairs if role_of(fx, ch) not in ("dimmer", "shutter", "pan", "tilt", "pt_speed")]
            if odd:
                out.append({"function_id": f.id, "name": f.name, "fixture_id": fid, "model": fx.key,
                            "writes": [{"channel": ch, "value": v, "role": r or "unknown"} for ch, v, r in odd],
                            "color_channel": fx.roles.get("color_wheel")})
    return out


def name_colors(rig: Rig, text: str) -> list:
    words = [w for w in "".join(ch if ch.isalpha() else " " for ch in text.lower()).split() if w not in ("color", "colour", "all", "spot", "spotlight", "light", "lights")]
    found = []
    for n in (2, 1):
        for i in range(len(words) - n + 1):
            c = rig.colors.canonical(" ".join(words[i:i + n]))
            if c and c not in found:
                found.append(c)
    return found


def wheel_discoveries(rig: Rig) -> list:
    """Wheel values the show uses under a color name that the fixture definition doesn't list."""
    seen: dict = {}
    for f in rig.functions.values():
        if f.type != "Scene" or not f.values or f.hidden:
            continue
        colors = name_colors(rig, f.name)
        if len(colors) != 1:
            continue
        by_model: dict = {}
        for fid, pairs in f.values.items():
            fx = rig.fixtures.get(fid)
            if fx is None or fx.color_mode != "wheel":
                continue
            v = dict(pairs).get(fx.roles["color_wheel"])
            if v is not None:
                by_model.setdefault(fx.key, set()).add(v)
        for model, vals in by_model.items():
            if len(vals) != 1:
                continue
            v = vals.pop()
            known = {val: c for c, val in (next(fx for fx in rig.fixtures.values() if fx.key == model).caps.get("wheel") or {}).items()}
            if known.get(v) == colors[0]:
                continue
            key = (model, v, colors[0])
            seen.setdefault(key, []).append(f.name)
    return [{"model": m, "value": v, "color": c, "qxf_says": next((cc for cc, vv in (next(fx for fx in rig.fixtures.values() if fx.key == m).caps.get("wheel") or {}).items() if vv == v), None),
             "evidence": names[:3], "confirm_with": f"lightai facts wheel \"{m}\" \"{c}\" {v}"} for (m, v, c), names in sorted(seen.items())]


def channel_group_findings(rig: Rig) -> list:
    out = []
    for g in rig.ws.channels_groups():
        role = channel_group_role(g.name)
        if not role:
            continue
        for fid, ch in g.pairs:
            fx = rig.fixtures.get(fid)
            if fx is None or role not in fx.roles:
                continue
            if fx.roles[role] != ch:
                out.append({"group_id": g.id, "group": g.name, "fixture_id": fid, "model": fx.key, "channel": ch,
                            "channel_role": role_of(fx, ch) or "unknown", "expected_channel": fx.roles[role], "role": role})
    return out


def qxf_conflicts(rig: Rig) -> list:
    out = []
    for fx in rig.fixtures.values():
        cands = {i: channel_candidates(cd) for i, cd in enumerate(fx.channel_defs)}
        qroles, _, _ = pick_roles(cands)
        for role, ch in fx.roles.items():
            if role in qroles and qroles[role] != ch and role in CONTROL_ROLES:
                out.append({"fixture_id": fx.id, "model": fx.key, "role": role, "qxf_channel": qroles[role], "used_channel": ch,
                            "source": fx.role_sources.get(role)})
    uniq, seen = [], set()
    for c in out:
        key = (c["model"], c["role"], c["qxf_channel"], c["used_channel"])
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def next_free(rig: Rig) -> dict:
    used: dict = {}
    for fx in rig.fixtures.values():
        used[fx.universe] = max(used.get(fx.universe, 0), fx.address + fx.channels)
    return {f"universe {u + 1}": f"address {a + 1} (0-based {a})" for u, a in sorted(used.items())}


def run_audit(rig: Rig) -> dict:
    errors, warnings = validate_workspace(rig.ws)
    missing = [{"fixture_id": fx.id, "name": fx.name, "notes": fx.notes} for fx in rig.fixtures.values() if fx.notes]
    gaps = []
    for fx in rig.fixtures.values():
        need = {"spot": ["pan", "tilt", "dimmer", "shutter", "color_wheel"], "wash": ["pan", "tilt", "dimmer", "red", "green", "blue"],
                "par": ["red", "green", "blue"], "rgb": ["red", "green", "blue"], "bar": ["red", "green", "blue"]}.get(fx.kind, [])
        miss = [r for r in need if r not in fx.roles]
        if miss:
            gaps.append({"fixture_id": fx.id, "name": fx.name, "model": fx.key, "missing_roles": miss})
    assumed = sorted({fx.key for fx in rig.fixtures.values() if str((fx.caps.get("shutter") or {}).get("open_source", "")).startswith("assumed")})
    types = Counter(f.type for f in rig.functions.values())
    prios = Counter(f.priority for f in rig.functions.values())
    ai = [f for f in rig.functions.values() if f.path.startswith("AI")]
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": str(rig.ws.path),
        "counts": {"fixtures": len(rig.fixtures), "functions": len(rig.functions), "widgets": len(rig.widgets),
                   "functions_by_type": dict(types), "priorities": {str(k): v for k, v in sorted(prios.items())}, "ai_functions": len(ai)},
        "next_free_function_id": rig.next_function_id(),
        "next_free_address": next_free(rig),
        "errors": errors,
        "warnings": warnings,
        "missing_definitions": missing,
        "role_gaps": gaps,
        "shutter_open_assumed": assumed,
        "scene_color_channel_problems": scene_color_findings(rig),
        "channel_group_problems": channel_group_findings(rig),
        "role_overrides_vs_qxf": qxf_conflicts(rig),
        "wheel_slots_from_function_names": wheel_discoveries(rig),
    }


def to_markdown(rep: dict, rig: Rig) -> str:
    L = [f"# Rig audit - {rep['generated']}", "", f"Project: `{rep['project']}`", ""]
    c = rep["counts"]
    L += [f"- Fixtures: {c['fixtures']}; functions: {c['functions']} {c['functions_by_type']}; VC widgets: {c['widgets']}; AI-made: {c['ai_functions']}",
          f"- Next free function ID: {rep['next_free_function_id']}; next free addresses: {rep['next_free_address']}", ""]
    L += ["## Fixtures and derived roles", "", "| ID | Name | Model | Kind | U.Addr | Roles |", "|---|---|---|---|---|---|"]
    for fx in sorted(rig.fixtures.values(), key=lambda f: f.id):
        roles = ", ".join(f"{r} {c}" for r, c in fx.roles.items() if r in CONTROL_ROLES)
        L.append(f"| {fx.id} | {fx.name} | {fx.key} | {fx.kind} | {fx.universe + 1}.{fx.address + 1} | {roles} |")
    L.append("")
    sections = [
        ("Errors (QLC+ would reject or mangle these)", rep["errors"]),
        ("Scenes driving a spot model on a non-color channel while other spots get color", rep["scene_color_channel_problems"]),
        ("Channel groups pointing at the wrong channel for their role", rep["channel_group_problems"]),
        ("Roles where an operator fact/roles template beats the .qxf", rep["role_overrides_vs_qxf"]),
        ("Color-wheel slots your show uses that the .qxf doesn't list (confirm, then run the command)", rep["wheel_slots_from_function_names"]),
        ("Fixtures missing expected roles", rep["role_gaps"]),
        ("Missing fixture definitions", rep["missing_definitions"]),
        ("Warnings", rep["warnings"]),
    ]
    for title, items in sections:
        L += [f"## {title} ({len(items)})", ""]
        for it in items[:60]:
            L.append(f"- {json.dumps(it) if isinstance(it, dict) else it}")
        if len(items) > 60:
            L.append(f"- ... {len(items) - 60} more (see rig-audit.json)")
        L.append("")
    if rep["shutter_open_assumed"]:
        L += ["## Shutter 'open' value assumed (calibrate to confirm)", "", *[f"- {m}" for m in rep["shutter_open_assumed"]], ""]
    return "\n".join(L)


def audit_main(args) -> int:
    cfg = load_config()
    project = Path(args.project) if args.project else cfg.project_path
    rig = Rig.load(cfg, project)
    rep = run_audit(rig)
    out = Path(args.out) if args.out else cfg.data_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "rig-audit.json").write_text(json.dumps({"audit": rep, "rig": rig.summary()}, indent=2, default=str), encoding="utf-8")
    (out / "rig-audit.md").write_text(to_markdown(rep, rig), encoding="utf-8")
    print(f"audit of {project}")
    print(f"  errors: {len(rep['errors'])}  warnings: {len(rep['warnings'])}")
    print(f"  scene color-channel problems: {len(rep['scene_color_channel_problems'])}")
    print(f"  channel group problems: {len(rep['channel_group_problems'])}")
    print(f"  role gaps: {len(rep['role_gaps'])}  qxf overrides: {len(rep['role_overrides_vs_qxf'])}")
    print(f"  wheel slots found in function names: {len(rep['wheel_slots_from_function_names'])}")
    print(f"  next free function ID {rep['next_free_function_id']}; {rep['next_free_address']}")
    print(f"  wrote {out / 'rig-audit.md'} and rig-audit.json")
    return 1 if rep["errors"] else 0


def fix_v3_color(rig: Rig) -> list:
    """Move misplaced spot color values to the model's real color channel, translating by color name."""
    changes = []
    ws = rig.ws
    by_id = {int(f.get("ID")): f for f in ws.function_els()}
    for finding in scene_color_findings(rig):
        el = by_id.get(finding["function_id"])
        fx = rig.fixtures[finding["fixture_id"]]
        f = rig.functions[finding["function_id"]]
        names = Counter()
        for fid, pairs in f.values.items():
            peer = rig.fixtures.get(fid)
            if peer is None or peer.color_mode != "wheel" or peer.key == fx.key:
                continue
            for ch, v in pairs:
                if role_of(peer, ch) == "color_wheel":
                    inv = {val: c for c, val in (peer.caps.get("wheel") or {}).items()}
                    if v in inv:
                        names[inv[v]] += 1
        if not names:
            for word in name_colors(rig, f.name):
                names[word] += 1
        if not names:
            changes.append({"function_id": f.id, "fixture_id": fx.id, "skipped": "no peer spot with a known color to copy"})
            continue
        color = names.most_common(1)[0][0]
        target = rig.wheel_value(fx, color)
        if not target["exact"]:
            changes.append({"function_id": f.id, "name": f.name, "fixture_id": fx.id,
                            "skipped": f"{fx.key} has no known '{color}' slot; run `find {color} on fixture {fx.id}` to teach it"})
            continue
        if target["value"] is None or fx.roles.get("color_wheel") is None:
            continue
        for fv in kids(el, "FixtureVal"):
            if int(fv.get("ID")) != fx.id:
                continue
            nums = [int(x) for x in (fv.text or "").split(",") if x.strip()]
            pairs = dict(zip(nums[0::2], nums[1::2]))
            removed = {ch: pairs.pop(ch) for ch in [w["channel"] for w in finding["writes"] if w["role"] in ("focus", "unknown", "effect", "maintenance", "none", "frost", "prism", "prism_rotation", "gobo")] if ch in pairs}
            pairs[fx.roles["color_wheel"]] = target["value"]
            fv.text = ",".join(f"{ch},{v}" for ch, v in sorted(pairs.items()))
            changes.append({"function_id": f.id, "name": f.name, "fixture_id": fx.id, "color": color,
                            "removed": removed, "set": {fx.roles["color_wheel"]: target["value"]}})
    for g in channel_group_findings(rig):
        for el in kids(ws.engine, "ChannelsGroup"):
            if int(el.get("ID")) != g["group_id"]:
                continue
            nums = [int(x) for x in (el.text or "").split(",") if x.strip()]
            pairs = list(zip(nums[0::2], nums[1::2]))
            pairs = [(fid, g["expected_channel"] if (fid == g["fixture_id"] and ch == g["channel"]) else ch) for fid, ch in pairs]
            el.text = ",".join(f"{a},{b}" for a, b in pairs)
            changes.append({"channels_group": g["group"], "fixture_id": g["fixture_id"], "channel": g["channel"], "now": g["expected_channel"]})
    return changes


def fix_main(args) -> int:
    cfg = load_config()
    project = Path(args.project) if args.project else cfg.project_path
    rig = Rig.load(cfg, project)
    changes = fix_v3_color(rig)
    for c in changes:
        print("  " + json.dumps(c))
    print(f"{len(changes)} change(s) {'to write' if args.yes else 'found (dry run; add --yes to write with a backup)'}")
    if args.yes and changes:
        res = write_workspace(rig.ws, project, project.parent / "backups", cfg.backups_keep)
        print(f"written {res['path']} (backup {res['backup']}); reload it in QLC+")
    return 0
