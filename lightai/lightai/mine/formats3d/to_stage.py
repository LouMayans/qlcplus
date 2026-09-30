"""Convert a decoded MVR scene into a proposal shaped like the club's stage.json (see
.claude/memory/stage-visualizer.md, lines 54-68): inches, X across, Y depth, Z up, fixtures keyed
by QLC+ fixture ID. This only ever RETURNS a dict -- it never writes the stage file -- so the
operator reviews/approves an import before anything real changes (lightai.rig.stage.Stage is
what actually loads the approved file later).

Axis assumption: MVR's world is right-handed and Z-up per DIN SPEC 15801, the same handedness and
up-axis the club's room uses, so a scene's positions/rotations are taken as already aligned with
the room (only a unit conversion, mm -> inches, is applied). A scene authored against a
differently-rotated room will still decode fine; that is exactly why this returns a proposal for
a human to check rather than writing the stage file directly.
"""

from __future__ import annotations

import math

from .mvr import MvrFixture, MvrScene

MM_PER_INCH = 25.4


def mm_to_in(v: float) -> float:
    return v / MM_PER_INCH


def _rx(deg: float) -> list[list[float]]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _transpose(m: list[list[float]]) -> list[list[float]]:
    return [[m[j][i] for j in range(3)] for i in range(3)]


# GDTF draws every fixture HANGING, and its Beam geometry emits along local -Z (DIN SPEC 15800: "Model" - "the device
# shall be drawn in a hanging position"; "Geometry Type Beam" - "emits its light into negative Z direction"). The stage
# frame (lightai.rig.stage / stage-rig.js) has the beam along +Z at pan = tilt = 0 and 'hung' = Rx(180) of it. So an
# MVR fixture's rotation in the stage frame is R = M . Rx(180): an identity Matrix is a hung fixture with rot 0.
_GDTF_TO_STAGE = _rx(180.0)
_HANG_H = {"floor": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], "hung": _rx(180.0), "wall": _rx(90.0)}


def stage_rotation(rotation_rows: list[list[float]]) -> list[list[float]]:
    """The fixture's rotation in the stage frame (column-vector matrix): M . Rx(180). `rotation_rows` are the Matrix's
    3 rotation rows as parsed (local X/Y/Z axes in world), so M is their transpose."""
    return _matmul(_transpose(rotation_rows), _GDTF_TO_STAGE)


def guess_hang(rotation_rows: list[list[float]]) -> str:
    """Where the beam points at rest: GDTF's beam is local -Z, so in world it is minus the Matrix's third row.
    Down -> 'hung', up -> 'floor', roughly sideways -> 'wall'."""
    beam_z = -rotation_rows[2][2]
    if beam_z < -0.5:
        return "hung"
    if beam_z > 0.5:
        return "floor"
    return "wall"


def rotation_to_degrees(rotation_rows: list[list[float]], hang: str) -> tuple[float, float, float]:
    """(rx, ry, rz) degrees with Rz(rz) . Ry(ry) . Rx(rx) . H(hang) == stage_rotation(rows), the composition
    lightai.rig.stage.orientation() uses. Exact for any hang: rot carries whatever H doesn't."""
    h = _HANG_H.get(hang, _HANG_H["floor"])
    m = _matmul(stage_rotation(rotation_rows), _transpose(h))  # R . H^-1, and H^-1 == H^T for a rotation
    ry = math.atan2(-m[2][0], math.hypot(m[0][0], m[1][0]))
    cy = math.cos(ry)
    if abs(cy) < 1e-9:
        # Gimbal lock (ry ~= +-90 deg): rx/rz trade off freely, so fold it all into rz.
        rz = math.atan2(-m[0][1], m[1][1])
        rx = 0.0
    else:
        rz = math.atan2(m[1][0], m[0][0])
        rx = math.atan2(m[2][1], m[2][2])
    return math.degrees(rx), math.degrees(ry), math.degrees(rz)


def _match_fixture_id(mvr_fx: MvrFixture, rig) -> int | None:
    """The QLC+ fixture ID this MVR fixture corresponds to: its FixtureID when the rig has that
    numeric ID, else an exact case-insensitive name match; None otherwise (or when rig is None)."""
    fixtures = getattr(rig, "fixtures", None) or {}
    if mvr_fx.fixture_id is not None:
        try:
            fid = int(mvr_fx.fixture_id)
        except ValueError:
            fid = None
        if fid is not None and fid in fixtures:
            return fid
    want = (mvr_fx.name or "").strip().lower()
    if want:
        for fid, fx in fixtures.items():
            if (getattr(fx, "name", "") or "").strip().lower() == want:
                return fid
    return None


def mvr_to_stage(scene: MvrScene, rig=None) -> dict:
    """A stage.json-shaped proposal dict for the operator to review (never written to disk).

    `fixtures` uses the real stage file's shape, keyed by QLC+ fixture ID (string) for every MVR
    fixture matched against `rig` (by FixtureID, else by name). Anything unmatched -- including
    every fixture when `rig` is None -- is listed in `unmatched` with its raw MVR data instead.
    """
    fixtures: dict[str, dict] = {}
    unmatched: list[dict] = []
    for mvr_fx in scene.fixtures:
        hang = guess_hang(mvr_fx.rotation)
        rx, ry, rz = rotation_to_degrees(mvr_fx.rotation, hang)
        entry = {
            "pos": [round(mm_to_in(v), 4) for v in mvr_fx.position],
            "rot": [round(rx, 2), round(ry, 2), round(rz, 2)],
            "hang": hang,
            "gdtf_spec": mvr_fx.gdtf_spec,
            "gdtf_mode": mvr_fx.gdtf_mode,
            "universe": mvr_fx.universe,
            "address": mvr_fx.address,
            "mvr_name": mvr_fx.name,
            "mvr_fixture_id": mvr_fx.fixture_id,
        }
        fid = _match_fixture_id(mvr_fx, rig)
        if fid is None:
            unmatched.append(entry)
        else:
            fixtures[str(fid)] = entry

    return {
        "version": 1,
        "units": "in",
        "fixtures": fixtures,
        "unmatched": unmatched,
        "layers": list(scene.layers),
        "scene_objects": [
            {
                "name": o.name,
                "kind": o.kind,
                "pos": [round(mm_to_in(v), 4) for v in o.position],
                "geometry_file": o.geometry_file,
                "layer": o.layer,
            }
            for o in scene.scene_objects
        ],
    }
