"""The 3D stage model: orders, mirror pairs, groups, places and aiming, on a small sample stage (never the club's
provisional coordinates), plus the resolver and recipes reading a stage next to a test project."""

import copy
import json
import math
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from lightai.rig.stage import Stage, aim_dmx, dmx_to_degrees, hit_point, stage_path

SAMPLE = Path(__file__).parent / "data" / "stage_sample.json"


def fx(fid, model="Mover", pan=540, tilt=270):
    return SimpleNamespace(id=fid, manufacturer="Test", model=model, mode="m", key=f"Test/{model}",
                           definition=SimpleNamespace(pan_max=pan, tilt_max=tilt, modes={}))


@pytest.fixture()
def stage(tmp_path):
    show = tmp_path / "Sample.qxw"
    show.write_text("<Workspace/>", encoding="utf-8")
    shutil.copy(SAMPLE, stage_path(show))
    return Stage.load(show), show


def test_load_and_places(stage):
    st, _ = stage
    assert st.anchor == (240.0, 180.0, 0.0) and len(st.fixtures) == 7
    assert st.place("the dj").name == "DJ console" and st.place("DJ booth").name == "DJ console"
    assert st.place("middle of the floor").category == "point" and st.place("bar").name == "Bar"
    assert st.place("the moon") is None


def test_orders(stage):
    st, _ = stage
    ids = [1, 2, 3, 4, 5, 6, 7]
    assert st.order(ids, "left_to_right")[:2] == [6, 1] and st.order(ids, "left_to_right")[-1] == 7
    assert st.order(ids, "back_to_front")[0] == 5  # the DJ side first
    assert st.order(ids, "center_out")[0] == 5 and set(st.order(ids, "center_out")[-2:]) == {6, 7}  # the wall pars are farthest
    xs = [st.pos(i)[0] for i in st.order(ids, "right_to_left")]
    assert xs == sorted(xs, reverse=True)
    assert st.order(ids, "random") == st.order(ids, "random")  # stable, so looks don't change on recompile
    frac = st.position_fraction([6, 1, 7], "left_to_right")
    assert frac[6] == 0.0 and frac[7] == 1.0
    with pytest.raises(ValueError):
        st.order(ids, "sideways")


def test_mirror_pairs_and_groups(stage):
    st, _ = stage
    models = {i: ("Par" if i in (6, 7) else "Mover") for i in range(1, 8)}
    pairs, centre, rest = st.mirror_pairs(list(range(1, 8)), models)
    assert set(pairs) == {(1, 2), (3, 4), (6, 7)} and centre == [5] and rest == []
    g = st.groups(list(range(1, 8)))
    assert set(g["back row"]) == {1, 2, 5} and set(g["front row"]) == {3, 4}
    assert "left side" not in g, "left/right wait until the positions are final"
    assert set(st.groups(list(range(1, 8)), positions_final=True)["left side"]) == {1, 3, 6}
    assert set(g["near the dj"]) >= {5} and set(g["near the bar"]) >= {3, 4}


@pytest.mark.parametrize("fid", [1, 2, 3, 4, 5, 6, 7])
@pytest.mark.parametrize("target", ["dj", "bar", "middle of the floor"])
def test_aim_round_trip(stage, fid, target):
    st, _ = stage
    a = st.aim(fx(fid), target)
    if a is None:  # unreachable is allowed, but then the maths must agree
        return
    e = st.fixtures[fid]
    p, t = dmx_to_degrees(a["pan16"], a["tilt16"], a["pan_max"], a["tilt_max"], e)
    hp = hit_point(e, p, t, a["target"])
    assert hp is not None and math.dist(hp, a["target"]) < 1.0
    assert a["pan"] == a["pan16"] >> 8 and a["pan_fine"] == a["pan16"] & 0xFF


def test_unreachable_straight_up_from_a_hung_head(stage):
    st, _ = stage
    e = st.fixtures[1]
    assert aim_dmx(e, (120, 60, 400), 540, 270) is None  # hung head can't point at the ceiling above it


def test_edits_are_read_live(stage):
    st, show = stage
    before = st.aim(fx(1), "dj")["pan16"]
    data = json.loads(stage_path(show).read_text(encoding="utf-8"))
    data["fixtures"]["1"]["pos"] = [20, 60, 170]
    stage_path(show).write_text(json.dumps(data), encoding="utf-8")
    st2 = Stage.load(show)
    assert st2 is not st and st2.hash != st.hash and st2.aim(fx(1), "dj")["pan16"] != before
    assert st2.order([1, 6], "left_to_right") == [1, 6]


# ---------------------------------------------------------------- with a real rig (test project + generated stage)
@pytest.fixture()
def rig_with_stage(tmp_path):
    from lightai.config import load_config
    from lightai.devtools import make_test_project
    from lightai.rig.model import Rig

    cfg = copy.copy(load_config())
    cfg.project_path = make_test_project(tmp_path / "show")
    cfg.learned_path = tmp_path / "learned.yaml"
    rig = Rig.load(cfg)
    ids = sorted(rig.fixtures)
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["fixtures"] = {}
    for k, i in enumerate(ids):  # a grid: back row near the DJ, front row near the bar
        row = 0 if k % 2 == 0 else 1
        data["fixtures"][str(i)] = {"model": rig.fixtures[i].key, "pos": [40 + (k // 2) * 25, 60 if row == 0 else 300, 170],
                                    "rot": [0, 0, 0], "hang": "hung"}
    stage_path(cfg.project_path).write_text(json.dumps(data), encoding="utf-8")
    return Rig.load(cfg), ids


def test_resolver_understands_stage_phrases(rig_with_stage):
    from lightai.nlu.normalize import normalize_slot

    rig, ids = rig_with_stage
    back = set(normalize_slot(rig, "target", "back row").get("fixture_ids") or [])
    front = set(normalize_slot(rig, "target", "the fixtures near the bar").get("fixture_ids") or [])
    assert back and front and not back & front and back | front <= set(ids)
    spots = set(rig.zones.get("spots") or [])
    if spots:
        got = set(normalize_slot(rig, "target", "spots in the front row").get("fixture_ids") or [])
        assert got <= spots and got <= set(normalize_slot(rig, "target", "front row").get("fixture_ids") or [])


def test_recipes_use_the_stage(rig_with_stage):
    from lightai.compiler import compile_look
    from lightai.compiler.spec import LookParams

    rig, ids = rig_with_stage
    movers = [i for i in ids if rig.fixtures[i].can_move]
    if len(movers) < 2:
        pytest.skip("test project has no moving heads")
    look = compile_look(rig, LookParams(recipe="position", targets=movers, aim="dj", colors=["white"]))
    scene = look.functions[0]
    assert scene.type == "Scene" and any("stage" in a["source"] for a in look.assumptions)
    run = compile_look(rig, LookParams(recipe="running_light", targets=movers[:4], order="right_to_left", colors=["red"]))
    steps = [f for f in run.functions if f.type == "Scene" and " - 0" in f.name]
    xs = [rig.stage.pos(i)[0] for i in rig.stage.order(movers[:4], "right_to_left")]
    assert xs == sorted(xs, reverse=True) and steps
