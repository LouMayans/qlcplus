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


def rgbmatrix(spec: FunctionSpec):
    """An RGB Matrix: write order matches RGBMatrix::saveXML exactly (Speed, Direction, RunOrder,
    Algorithm, Color(s), ControlMode, then the plain-text FixtureGroup reference, then Properties)."""
    el = _common(spec)
    _speed(el, spec)
    sub(el, "Direction", text=spec.direction)
    sub(el, "RunOrder", text=spec.run_order)
    m = spec.matrix
    algo_type = m.get("algorithm_type", "Script")
    algo_name = m.get("algorithm_name", "")
    sub(el, "Algorithm", {"Type": algo_type}, algo_name if algo_type == "Script" else None)
    for i, packed in enumerate(m.get("colors", [])):
        sub(el, "Color", {"Index": i}, int(packed) & 0xFFFFFFFF)
    sub(el, "ControlMode", text=m.get("control_mode", "RGB"))
    sub(el, "FixtureGroup", text=int(m.get("fixture_group", -1)))
    for name, value in (m.get("properties") or {}).items():
        sub(el, "Property", {"Name": name, "Value": value})
    return el


def fixture_group(spec: FunctionSpec):
    """An engine-level <FixtureGroup>: matches FixtureGroup::saveXML (ID attribute, then Name,
    Size, and one <Head> per grid cell). Not a <Function>, so it skips _common() entirely."""
    el = new_element("FixtureGroup", {"ID": spec.id})
    sub(el, "Name", text=spec.name)
    gx, gy = spec.group_size
    sub(el, "Size", {"X": int(gx), "Y": int(gy)})
    for h in spec.group_heads:
        sub(el, "Head", {"X": h.x, "Y": h.y, "Fixture": h.fixture}, h.head)
    return el


def show(spec: FunctionSpec):
    """A Show's <TimeDivision> plus its <Track>s, each holding its <ShowFunction> items.

    Matches Track::saveXML/ShowFunction::saveXML exactly: the only real-world caller of
    ShowFunction::saveXML never passes a trackId, so on-disk .qxw files never carry UID or
    TrackID attributes (verified against SaveFile/Main Project.qxw) — only ID/StartTime/
    Duration/Color, so that's what we emit too.
    """
    el = _common(spec)
    time_type, bpm = spec.time_division
    sub(el, "TimeDivision", {"Type": time_type, "BPM": int(bpm)})
    for tr in spec.tracks:
        attrs = {"ID": tr.id, "Name": tr.name}
        if tr.scene_id is not None:
            attrs["SceneID"] = tr.scene_id
        attrs["isMute"] = 1 if tr.mute else 0
        t = sub(el, "Track", attrs)
        for it in tr.items:
            item_attrs = {"ID": it.function_id, "StartTime": int(it.start_ms)}
            if it.duration_ms:
                item_attrs["Duration"] = int(it.duration_ms)
            if it.color:
                item_attrs["Color"] = it.color
            sub(t, "ShowFunction", item_attrs)
    return el


BUILDERS = {
    "Scene": scene,
    "Chaser": chaser,
    "Collection": collection,
    "EFX": efx,
    "Show": show,
    "RGBMatrix": rgbmatrix,
    "FixtureGroup": fixture_group,
}


def to_element(spec: FunctionSpec):
    try:
        return BUILDERS[spec.type](spec)
    except KeyError as exc:
        raise ValueError(f"no XML builder for function type {spec.type!r}") from exc
