"""Live aiming, for fitting the 3D stage to the real room by eye ('triangulating').

    place spot 2 white beam, no flashing, straight down
    point spot 3 right where the beam ends and hits the floor
    all beams to where spot 2's beam lands
    do it again                                   (after moving a fixture in the 3D stage)

A live aim writes pan/tilt, plus an open shutter, a color (white unless said) and full dimmer, as live overrides that
stay until 'release': nothing is saved in the show. A beam reference ('where spot 2's beam lands') is worked out when
the command runs: the reference fixture's pan/tilt as QLC+ outputs them right now, through the 3D stage's own maths
(the same as the visualizer draws), down to the floor or the top of a stage deck. When the real beams don't meet, a
position in the 3D stage is off: move that fixture in the 3D stage and say 'do it again' until they do. No tape measure.
"""

from __future__ import annotations

from typing import Optional

from lightai.rig.stage import beam_direction, dmx_to_degrees, stage_path


def floor_hit(entry: dict, pan_deg: float, tilt_deg: float, floor_z: float = 0.0, origin=None) -> Optional[tuple]:
    """Where a beam starting at origin (default: the fixture's position) meets a level floor at height floor_z (inches);
    None when it points up or level."""
    o = [float(v) for v in (list(origin if origin is not None else entry.get("pos") or [0, 0, 0]) + [0, 0, 0])[:3]]
    d = beam_direction(entry, pan_deg, tilt_deg)
    if d[2] >= -1e-6 or o[2] <= floor_z:
        return None
    s = (floor_z - o[2]) / d[2]
    return (o[0] + d[0] * s, o[1] + d[1] * s, floor_z)


def beam_floor_point(stage, fx, pan16: int, tilt16: int) -> Optional[tuple]:
    """Where fixture fx's beam lands at these 16-bit pan/tilt values: on the floor, or on top of a stage deck or riser
    when it lands on one."""
    entry = stage.fixtures.get(int(fx.id))
    if not entry or not entry.get("pos"):
        return None
    pan_max, tilt_max = stage.pan_tilt_range(fx)
    p, t = dmx_to_degrees(pan16, tilt16, pan_max, tilt_max, entry)
    origin = stage.beam_origin(fx)
    hit = floor_hit(entry, p, t, origin=origin)
    if hit is None:
        return None
    try:  # a deck or riser under that spot: the beam stops on its top
        from lightai.rig.scene import Scene, read_doc

        scene = Scene(read_doc(stage.path), None)
        top = scene.support_z(hit[0], hit[1], set())
        if top > 0:
            on_top = floor_hit(entry, p, t, top, origin=origin)
            if on_top is not None and scene.support_z(on_top[0], on_top[1], set()) >= top - 0.5:
                return on_top
    except (OSError, ValueError, AttributeError):
        pass
    return hit


async def read_pan_tilt(client, fx) -> tuple:
    """The fixture's pan/tilt as QLC+ outputs them now, as 16-bit values (an 8-bit channel counts as its coarse byte)."""
    roles = [r for r in ("pan", "pan_fine", "tilt", "tilt_fine") if fx.has(r)]
    if "pan" not in roles or "tilt" not in roles:
        raise ValueError(f"{fx.name} has no pan/tilt: its beam can't be followed")
    chans = {r: fx.dmx(fx.ch(r)) for r in roles}
    uni = chans["pan"][0]
    lo = min(a for _, a in chans.values())
    hi = max(a for _, a in chans.values())
    rows = {row["channel"]: row["value"] for row in await client.channel_values(uni, lo, hi - lo + 1)}

    def val(role: str) -> int:
        return int(rows.get(chans[role][1], 0)) if role in chans else 0

    return (val("pan") << 8) | val("pan_fine"), (val("tilt") << 8) | val("tilt_fine")


def aim_channels(rig, fxs: list, aim: str, color: str = "white", intensity: float = 1.0) -> tuple:
    """Live channel values that aim these fixtures (the position recipe: pan/tilt, open shutter, color, dimmer).
    aim: 'down' or 'point:x,y,z' (inches). -> ([(universe, channel, value)], aimed ids, [skipped])"""
    from lightai.compiler.recipes import build_look
    from lightai.compiler.spec import LookParams

    movers = [fx for fx in fxs if fx.has("pan") and fx.has("tilt")]
    skipped = [{"fixture": fx.id, "name": fx.name, "reason": "no pan/tilt"} for fx in fxs if fx not in movers]
    if not movers:
        return [], [], skipped
    look = build_look(rig, LookParams(recipe="position", targets=[fx.id for fx in movers], colors=[color or "white"],
                                      intensity=intensity, aim=aim, name="live aim"), start_id=900000)
    scene = look.functions[0]
    out, aimed = [], []
    for fid, pairs in (scene.values or {}).items():
        fx = rig.fixtures[int(fid)]
        aimed.append(int(fid))
        for ch, value in pairs:
            u, a = fx.dmx(int(ch))
            out.append((u, a, int(value)))
    return out, aimed, skipped + list(look.skipped)


def stage_file(cfg):
    return stage_path(cfg.project_path)
