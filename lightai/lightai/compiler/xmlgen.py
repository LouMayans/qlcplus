"""FunctionSpec -> <Function> element, in the exact order QLC+'s saveXML writes it."""

from __future__ import annotations

from lightai.compiler.spec import FunctionSpec
from lightai.rig.qxw import new_element, sub


def _common(spec: FunctionSpec, extra: dict | None = None):
    attrs = {"ID": spec.id, "Type": spec.type, "Name": spec.name}
    if extra:
        attrs.update(extra)
    if spec.path:
        attrs["Path"] = spec.path
    if spec.priority:
        attrs["Priority"] = spec.priority
    return new_element("Function", attrs)


def _speed(el, spec: FunctionSpec) -> None:
    sub(el, "Speed", {"FadeIn": int(spec.fade_in), "FadeOut": int(spec.fade_out), "Duration": int(spec.duration)})


def scene(spec: FunctionSpec):
    el = _common(spec)
    _speed(el, spec)
    for fid in sorted(spec.values):
        pairs = sorted(spec.values[fid])
        if not pairs:
            continue
        flat = ",".join(f"{ch},{max(0, min(255, int(v)))}" for ch, v in pairs)
        sub(el, "FixtureVal", {"ID": fid}, flat)
    return el


def chaser(spec: FunctionSpec):
    el = _common(spec)
    _speed(el, spec)
    sub(el, "Direction", text=spec.direction)
    sub(el, "RunOrder", text=spec.run_order)
    fi, fo, du = spec.speed_modes
    sub(el, "SpeedModes", {"FadeIn": fi, "FadeOut": fo, "Duration": du})
    for i, st in enumerate(spec.steps):
        attrs = {"Number": i, "FadeIn": int(st.fade_in), "Hold": int(st.hold), "FadeOut": int(st.fade_out)}
        if st.note:
            attrs["Note"] = st.note
        sub(el, "Step", attrs, st.function_id)
    return el


def collection(spec: FunctionSpec):
    el = _common(spec)
    for i, fid in enumerate(spec.members):
        sub(el, "Step", {"Number": i}, fid)
    return el


def efx(spec: FunctionSpec):
    el = _common(spec)
    for fx in spec.efx_fixtures:
        f = sub(el, "Fixture")
        sub(f, "ID", text=fx.id)
        sub(f, "Head", text=fx.head)
        sub(f, "Mode", text=fx.mode)
        sub(f, "Direction", text=fx.direction)
        sub(f, "StartOffset", text=fx.start_offset)
    e = spec.efx
    sub(el, "PropagationMode", text=e.get("propagation", "Parallel"))
    _speed(el, spec)
    sub(el, "Direction", text=spec.direction)
    sub(el, "RunOrder", text=spec.run_order)
    sub(el, "Algorithm", text=e.get("algorithm", "Circle"))
    sub(el, "Width", text=int(e.get("width", 60)))
    sub(el, "Height", text=int(e.get("height", 60)))
    sub(el, "Rotation", text=int(e.get("rotation", 0)))
    sub(el, "StartOffset", text=int(e.get("start_offset", 0)))
    sub(el, "IsRelative", text=1 if e.get("relative") else 0)
    for axis in ("X", "Y"):
        a = e.get(axis.lower(), {})
        ax = sub(el, "Axis", {"Name": axis})
        sub(ax, "Offset", text=int(a.get("offset", 127)))
        sub(ax, "Frequency", text=int(a.get("frequency", 2 if axis == "X" else 3)))
        sub(ax, "Phase", text=int(a.get("phase", 90 if axis == "X" else 0)))
    return el


BUILDERS = {"Scene": scene, "Chaser": chaser, "Collection": collection, "EFX": efx}


def to_element(spec: FunctionSpec):
    try:
        return BUILDERS[spec.type](spec)
    except KeyError as exc:
        raise ValueError(f"no XML builder for function type {spec.type!r}") from exc
