"""Approximate playback of a compiled look as DMX frames, for live previews through Simple Desk.

It mirrors QLC+ closely enough to judge a look by eye: Collections start all members, Chasers
step with their fade/duration, EFX move pan/tilt along the algorithm with per-fixture offsets,
intensity roles merge highest-takes-precedence and everything else latest-takes-precedence.
"""

from __future__ import annotations

from lightai.compiler.recipes import efx_point
from lightai.compiler.spec import FunctionSpec, Look
from lightai.rig.model import Rig

HTP_ROLES = {"dimmer", "red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow", "lime", "indigo"}


def _expand(fid: int, specs: dict, seen: set) -> list:
    if fid in seen or fid not in specs:
        return []
    seen.add(fid)
    s = specs[fid]
    if s.type == "Collection":
        out = []
        for m in s.members:
            out.extend(_expand(m, specs, seen))
        return out
    return [s]


def _apply(vals: dict, scene: FunctionSpec, weight: float) -> None:
    for fid, pairs in scene.values.items():
        roles = scene.roles.get(fid, {})
        for ch, v in pairs:
            key = (fid, ch)
            if roles.get(ch) in HTP_ROLES:
                vals[key] = max(vals.get(key, 0), int(round(v * weight)))
            elif weight >= 0.5 or key not in vals:
                vals[key] = v


def _step_index(pos: int, n: int, run_order: str) -> int:
    if run_order == "PingPong" and n > 1:
        cycle = 2 * n - 2
        k = pos % cycle
        return k if k < n else cycle - k
    return pos % n


def frame_at(t_ms: float, active: list, specs: dict, rig: Rig) -> dict:
    vals: dict = {}
    for s in active:
        if s.type == "Scene":
            _apply(vals, s, 1.0)
        elif s.type == "Chaser" and s.steps:
            n = len(s.steps)
            dur = max(20, s.duration or 1000)
            pos = int(t_ms // dur)
            frac_ms = t_ms - pos * dur
            cur = specs.get(s.steps[_step_index(pos, n, s.run_order)].function_id)
            prev = specs.get(s.steps[_step_index(max(0, pos - 1), n, s.run_order)].function_id) if pos > 0 else None
            w_in = min(1.0, frac_ms / s.fade_in) if s.fade_in > 0 else 1.0
            w_out = max(0.0, 1.0 - frac_ms / s.fade_out) if (s.fade_out > 0 and prev is not None) else 0.0
            if prev is not None and w_out > 0:
                _apply(vals, prev, w_out)
            if cur is not None:
                _apply(vals, cur, w_in)
        elif s.type == "EFX":
            e = s.efx
            dur = max(20, s.duration or 2000)
            phase = (t_ms / dur) % 1.0
            for ef in s.efx_fixtures:
                fx = rig.fixtures.get(ef.id)
                if fx is None or not fx.can_move:
                    continue
                ph = (phase + ef.start_offset / 360.0) % 1.0
                if ef.direction == "Backward":
                    ph = 1.0 - ph
                pan, tilt = efx_point(
                    e.get("algorithm", "Circle"), ph, int(e.get("width", 60)), int(e.get("height", 60)),
                    int(e.get("x", {}).get("frequency", 2)), int(e.get("y", {}).get("frequency", 3)),
                    int(e.get("x", {}).get("phase", 90)), int(e.get("y", {}).get("phase", 0)),
                )
                vals[(fx.id, fx.roles["pan"])] = max(0, min(255, pan))
                vals[(fx.id, fx.roles["tilt"])] = max(0, min(255, tilt))
    return vals


def simulate(rig: Rig, look: Look, seconds: float = 8.0, fps: int = 20) -> dict:
    specs = {f.id: f for f in look.functions}
    active = _expand(look.main_id, specs, set())
    frames = []
    touched: set = set()
    n = max(1, int(seconds * fps))
    for i in range(n):
        t = i * 1000.0 / fps
        vals = frame_at(t, active, specs, rig)
        dmx = {}
        for (fid, ch), v in vals.items():
            uni, addr = rig.fixtures[fid].dmx(ch)
            dmx[(uni, addr)] = v
            touched.add((uni, addr))
        frames.append(dmx)
    return {"fps": fps, "seconds": seconds, "frames": frames, "channels": sorted(touched)}


def compress(preview: dict) -> dict:
    """JSON-friendly form: first frame in full, then only changed channels per frame."""
    out, prev = [], {}
    for fr in preview["frames"]:
        delta = [[u, c, v] for (u, c), v in fr.items() if prev.get((u, c)) != v]
        out.append(delta)
        prev = fr
    return {"fps": preview["fps"], "seconds": preview["seconds"], "deltas": out, "channels": [list(x) for x in preview["channels"]]}
