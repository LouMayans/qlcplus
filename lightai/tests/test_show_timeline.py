"""Tests for QLC+ Show (timeline) support in the look compiler.

Covers: FunctionSpec/TrackSpec/ShowItemSpec -> XML (matching Track::saveXML and
ShowFunction::saveXML exactly), validate_specs' Show rules, and a round trip through a
real workspace file (write, reload, re-validate).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from lightai.compiler.spec import FunctionSpec, ShowItemSpec, TrackSpec
from lightai.compiler.validate import validate_specs, validate_workspace
from lightai.compiler.writer import write_workspace
from lightai.compiler.xmlgen import to_element
from lightai.config import REPO_ROOT
from lightai.devtools import QlcInstance, make_test_project
from lightai.rig.qxf import kid, kids
from lightai.rig.qxw import Workspace


def _leaf_collections(base_id: int) -> tuple:
    """Two Collections that reference each other: a Show needs real targets to point at, and a
    mutual reference needs no rig-specific fixture/channel data. Only used for structural
    (never executed) tests — starting this pair in real QLC+ would recurse forever."""
    a = FunctionSpec(id=base_id, type="Collection", name="Collection A", path="Shows", members=[base_id + 1])
    b = FunctionSpec(id=base_id + 1, type="Collection", name="Collection B", path="Shows", members=[base_id])
    return a, b


def _two_track_show(show_id: int, leaf_a: int, leaf_b: int, name: str = "Test Show") -> FunctionSpec:
    """A Show with 2 tracks / 3 items, the shape the rebuild spec asks tests to cover."""
    return FunctionSpec(
        id=show_id,
        type="Show",
        name=name,
        path="Shows",
        tracks=[
            TrackSpec(
                id=0,
                name="Track 1",
                items=[
                    ShowItemSpec(function_id=leaf_a, start_ms=0, duration_ms=1000, color="#556b80"),
                    ShowItemSpec(function_id=leaf_b, start_ms=1000, duration_ms=500),
                ],
            ),
            TrackSpec(
                id=1,
                name="Track 2",
                items=[ShowItemSpec(function_id=leaf_a, start_ms=0, duration_ms=2000, color="#803c3c")],
            ),
        ],
    )


def test_show_to_element_matches_qlc_shape_and_validates(rig):
    base = rig.next_function_id()
    a, b = _leaf_collections(base)
    show_spec = _two_track_show(base + 2, a.id, b.id)
    specs = [a, b, show_spec]

    errors, _ = validate_specs(rig, specs, set(rig.functions))
    assert errors == []

    el = to_element(show_spec)
    assert list(el.attrib.keys()) == ["ID", "Type", "Name", "Path"]
    assert el.get("Type") == "Show"

    td = kid(el, "TimeDivision")
    assert list(td.attrib.keys()) == ["Type", "BPM"]
    assert (td.get("Type"), td.get("BPM")) == ("Time", "120")

    tracks = kids(el, "Track")
    assert [t.get("ID") for t in tracks] == ["0", "1"]
    assert [t.get("Name") for t in tracks] == ["Track 1", "Track 2"]

    items0 = kids(tracks[0], "ShowFunction")
    items1 = kids(tracks[1], "ShowFunction")
    assert len(items0) == 2 and len(items1) == 1
    assert list(items0[0].attrib.keys()) == ["ID", "StartTime", "Duration", "Color"]
    assert items0[0].get("ID") == str(a.id)
    assert items0[0].get("StartTime") == "0"
    assert items0[0].get("Duration") == "1000"
    assert items0[0].get("Color") == "#556b80"
    # QLC+ never writes UID/TrackID in practice: Track::saveXML always calls
    # ShowFunction::saveXML(doc) with the default trackId (UINT_MAX), so the "track context"
    # branch that would add them never fires on disk (verified: 0 occurrences in Main Project.qxw).
    for sf in items0 + items1:
        assert sf.get("UID") is None
        assert sf.get("TrackID") is None


def test_zero_duration_and_missing_color_are_omitted():
    spec = FunctionSpec(id=1, type="Show", name="S", tracks=[TrackSpec(id=0, name="T", items=[ShowItemSpec(function_id=5, start_ms=250)])])
    sf = kids(kids(to_element(spec), "Track")[0], "ShowFunction")[0]
    assert list(sf.attrib.keys()) == ["ID", "StartTime"]
    assert sf.get("StartTime") == "250"


def test_track_scene_id_mute_and_empty_track():
    spec = FunctionSpec(id=1, type="Show", name="S", tracks=[TrackSpec(id=7, name="Bound", items=[], scene_id=42, mute=True)])
    tr = kids(to_element(spec), "Track")[0]
    assert list(tr.attrib.keys()) == ["ID", "Name", "SceneID", "isMute"]
    assert (tr.get("ID"), tr.get("SceneID"), tr.get("isMute")) == ("7", "42", "1")
    assert kids(tr, "ShowFunction") == []


def test_validate_specs_show_needs_at_least_one_track(rig):
    show_spec = FunctionSpec(id=rig.next_function_id(), type="Show", name="Empty Show", tracks=[])
    errors, _ = validate_specs(rig, [show_spec], set(rig.functions))
    assert any("no tracks" in e for e in errors)


def test_validate_specs_show_rejects_missing_reference(rig):
    base = rig.next_function_id()
    missing = base + 10_000
    assert missing not in rig.functions
    show_spec = FunctionSpec(
        id=base, type="Show", name="Show", tracks=[TrackSpec(id=0, name="T", items=[ShowItemSpec(function_id=missing, start_ms=0)])]
    )
    errors, _ = validate_specs(rig, [show_spec], set(rig.functions))
    assert any("missing function" in e for e in errors)


def test_validate_specs_show_rejects_overlapping_items(rig):
    base = rig.next_function_id()
    a, b = _leaf_collections(base)
    show_spec = FunctionSpec(
        id=base + 2,
        type="Show",
        name="Show",
        tracks=[
            TrackSpec(
                id=0,
                name="T",
                items=[
                    ShowItemSpec(function_id=a.id, start_ms=0, duration_ms=1000),
                    ShowItemSpec(function_id=b.id, start_ms=500, duration_ms=500),  # starts before the first ends
                ],
            )
        ],
    )
    errors, _ = validate_specs(rig, [a, b, show_spec], set(rig.functions))
    assert any("overlap" in e for e in errors)


def test_validate_specs_show_rejects_duplicate_track_ids(rig):
    base = rig.next_function_id()
    a, b = _leaf_collections(base)
    show_spec = FunctionSpec(
        id=base + 2,
        type="Show",
        name="Show",
        tracks=[
            TrackSpec(id=0, name="T1", items=[ShowItemSpec(function_id=a.id, start_ms=0)]),
            TrackSpec(id=0, name="T2", items=[ShowItemSpec(function_id=b.id, start_ms=0)]),
        ],
    )
    errors, _ = validate_specs(rig, [a, b, show_spec], set(rig.functions))
    assert any("duplicate track" in e for e in errors)


def test_validate_specs_show_rejects_negative_start_and_duration(rig):
    base = rig.next_function_id()
    a, b = _leaf_collections(base)
    show_spec = FunctionSpec(
        id=base + 2,
        type="Show",
        name="Show",
        tracks=[
            TrackSpec(
                id=0,
                name="T",
                items=[
                    ShowItemSpec(function_id=a.id, start_ms=-1),
                    ShowItemSpec(function_id=b.id, start_ms=0, duration_ms=-5),
                ],
            )
        ],
    )
    errors, _ = validate_specs(rig, [a, b, show_spec], set(rig.functions))
    assert any("negative start" in e for e in errors)
    assert any("negative duration" in e for e in errors)


def test_show_round_trips_through_a_real_workspace(tmp_path):
    project = make_test_project(tmp_path / "test-project")
    ws = Workspace.load(project)
    assert ws.functions() == []  # the template ships with no functions; nothing to collide with
    base_errors, _ = validate_workspace(ws)

    a, b = _leaf_collections(1)
    show_spec = _two_track_show(3, a.id, b.id, name="Round Trip Show")
    for spec in (a, b, show_spec):
        ws.add_function(to_element(spec))

    write_workspace(ws, project, tmp_path / "backups")
    reloaded = Workspace.load(project)
    errors, _ = validate_workspace(reloaded)
    assert errors == base_errors  # the Show introduces no new errors

    functions = {f.id: f for f in reloaded.functions()}
    assert functions[3].type == "Show"
    assert sorted(functions[3].refs) == [1, 1, 2]  # Workspace's own Show parsing (qxw.py)

    show_el = reloaded.function_el(3)
    tracks = kids(show_el, "Track")
    assert [t.get("ID") for t in tracks] == ["0", "1"]
    t0_items = kids(tracks[0], "ShowFunction")
    assert [i.get("StartTime") for i in t0_items] == ["0", "1000"]
    assert t0_items[0].get("Duration") == "1000"
    assert t0_items[0].get("Color") == "#556b80"
    assert t0_items[1].get("Color") is None


def test_show_matches_a_real_show_attribute_by_attribute():
    """Byte-level shape check against a real Show ("Medium Spinning Flashing Fast White",
    function 21) in SaveFile/Main Project.qxw: same attribute names, in the same order."""
    real_ws = Workspace.load(REPO_ROOT / "SaveFile" / "Main Project.qxw")
    real_show = real_ws.function_el(21)
    assert real_show is not None and real_show.get("Type") == "Show"

    mine = to_element(FunctionSpec(id=21, type="Show", name=real_show.get("Name"), path=real_show.get("Path")))
    assert list(mine.attrib.keys()) == list(real_show.attrib.keys()) == ["ID", "Type", "Name", "Path"]

    real_td = kid(real_show, "TimeDivision")
    my_td = kid(mine, "TimeDivision")
    assert list(my_td.attrib.keys()) == list(real_td.attrib.keys()) == ["Type", "BPM"]

    real_tracks = kids(real_show, "Track")
    plain = next(t for t in real_tracks if t.get("SceneID") is None)
    bound = next(t for t in real_tracks if t.get("SceneID") is not None)
    my_tracks = kids(
        to_element(
            FunctionSpec(
                id=1,
                type="Show",
                name="x",
                tracks=[TrackSpec(id=1, name="n", items=[]), TrackSpec(id=2, name="n", items=[], scene_id=999)],
            )
        ),
        "Track",
    )
    assert list(my_tracks[0].attrib.keys()) == list(plain.attrib.keys()) == ["ID", "Name", "isMute"]
    assert list(my_tracks[1].attrib.keys()) == list(bound.attrib.keys()) == ["ID", "Name", "SceneID", "isMute"]

    real_items = [sf for t in real_tracks for sf in kids(t, "ShowFunction")]
    assert all(k in ("ID", "StartTime", "Duration", "Color", "Locked") for sf in real_items for k in sf.attrib.keys())
    assert all(sf.get("UID") is None and sf.get("TrackID") is None for sf in real_items)
    no_duration = next(sf for sf in real_items if sf.get("Duration") is None)
    with_duration = next(sf for sf in real_items if sf.get("Duration") is not None)

    my_show = FunctionSpec(
        id=1,
        type="Show",
        name="x",
        tracks=[
            TrackSpec(
                id=0,
                name="n",
                items=[
                    ShowItemSpec(function_id=1, start_ms=0, color="#803c3c"),
                    ShowItemSpec(function_id=1, start_ms=0, duration_ms=4000, color="#556b80"),
                ],
            )
        ],
    )
    my_items = kids(kids(to_element(my_show), "Track")[0], "ShowFunction")
    assert list(my_items[0].attrib.keys()) == list(no_duration.attrib.keys()) == ["ID", "StartTime", "Color"]
    assert list(my_items[1].attrib.keys()) == list(with_duration.attrib.keys()) == ["ID", "StartTime", "Duration", "Color"]


# Opt-in end-to-end: an isolated QLC+ dev build actually running the Show. Never port 9999
# (that belongs to a real/production instance); this uses 9993, and QlcInstance refuses to
# start on a port already in use.
LIGHTAI_DEV_EXE = Path(r"C:\qlcplus-dev\qlcplus.exe")


@pytest.mark.skipif(os.environ.get("LIGHTAI_E2E") != "1", reason="set LIGHTAI_E2E=1 to run against an isolated QLC+")
@pytest.mark.skipif(not LIGHTAI_DEV_EXE.exists(), reason=f"{LIGHTAI_DEV_EXE} not found")
def test_show_runs_in_a_live_qlc_instance(tmp_path):
    from lightai.exec.wsclient import QlcClient  # imported lazily: needs `websockets`, unlike the rest of this file

    project = make_test_project(tmp_path / "test-project")
    ws = Workspace.load(project)
    # Safe, inert leaves (empty Scenes): unlike the Collection-cycle helper above, these are
    # actually started for real, and a Collection cycle would make QLC+ recurse forever.
    scene_a = FunctionSpec(id=1, type="Scene", name="Empty Scene A")
    scene_b = FunctionSpec(id=2, type="Scene", name="Empty Scene B")
    show_spec = _two_track_show(3, scene_a.id, scene_b.id, name="Live Show")
    for spec in (scene_a, scene_b, show_spec):
        ws.add_function(to_element(spec))
    write_workspace(ws, project, tmp_path / "backups")

    async def run() -> None:
        with QlcInstance(project, port=9993, exe=LIGHTAI_DEV_EXE):
            client = QlcClient(url="ws://127.0.0.1:9993/qlcplusWS")
            async with client:
                await client.set_function(3, True)
                await asyncio.sleep(0.5)
                assert await client.function_status(3) == "Running"
                await client.set_function(3, False)

    asyncio.run(run())
