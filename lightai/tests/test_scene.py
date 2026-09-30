"""The 3D stage by voice (iteration 10): move, place, rotate, flip, mount, add and remove fixtures and objects.

The engine is tested on a small made-up room; the operator's own sentences are planned against the main show and its
3D stage (read-only) with a stand-in model answer; saving and undo run on a copy with a fake QLC+ that speaks
saveStage like the real one (webaccessstage.cpp)."""

import asyncio
import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.rig import scene as sc
from lightai.rig.stage import stage_path

MAIN = load_config().project_path
MODEL = load_config().current_model_dir()
HAS_STAGE = stage_path(MAIN).exists()


def room() -> dict:
    """A 40 x 30 ft room: DJ console on a deck at the back, a bar at the front, two cocktail tables with stools."""
    cyl = lambda d, h, z: {"shape": "cylinder", "size": [d, d, h], "pos": [0, 0, z]}  # noqa: E731
    return {
        "version": 1, "units": "in", "room": {"width": 480, "depth": 360, "height": 168}, "anchor": [240, 180, 0],
        "fixtures": {"1": {"model": "A/B", "pos": [100, 100, 150], "rot": [0, 0, 0], "hang": "hung"},
                     "2": {"model": "A/B", "pos": [300, 100, 150], "rot": [0, 0, 0], "hang": "hung"}},
        "propDefs": {"deck": {"name": "Deck", "category": "stage", "parts": [{"shape": "box", "size": [144, 72, 24], "pos": [0, 0, 12]}]},
                     "console": {"name": "Console", "category": "dj", "parts": [{"shape": "box", "size": [100, 20, 40], "pos": [0, 0, 20]}]},
                     "bar": {"name": "Bar", "category": "bar", "parts": [{"shape": "box", "size": [240, 24, 42], "pos": [0, 0, 21]}]},
                     "ctable": {"name": "Cocktail table", "category": "table", "parts": [cyl(16, 30, 15)]},
                     "stool": {"name": "Stool", "category": "seating", "parts": [{"shape": "model", "size": [16, 16, 30], "pos": [0, 0, 0]}]},
                     "floor": {"name": "Floor", "category": "floor_area", "parts": [{"shape": "box", "size": [200, 120, 2], "pos": [0, 0, 1]}]}},
        "objects": [
            {"id": "o1", "prop": "deck", "name": "DJ stage deck", "category": "stage", "aliases": ["the stage"], "pos": [240, 50, 0]},
            {"id": "o2", "prop": "console", "name": "DJ console", "category": "dj", "aliases": ["dj", "dj booth", "the booth"], "pos": [240, 45, 24]},
            {"id": "o3", "prop": "bar", "name": "Bar", "category": "bar", "aliases": ["the bar"], "pos": [240, 330, 0]},
            {"id": "o4", "prop": "ctable", "name": "Cocktail table 1", "category": "table", "aliases": ["the tables"], "pos": [120, 280, 0], "rz": 0},
            {"id": "o5", "prop": "stool", "name": "Cocktail stool 1a", "category": "seating", "pos": [100, 280, 0], "parent": "Cocktail table 1"},
            {"id": "o6", "prop": "stool", "name": "Cocktail stool 1b", "category": "seating", "pos": [140, 280, 0], "parent": "Cocktail table 1"},
            {"id": "o7", "prop": "ctable", "name": "Cocktail table 2", "category": "table", "aliases": ["the tables"], "pos": [360, 280, 0], "rz": 0},
            {"id": "o8", "prop": "ctable", "name": "VIP table L 1", "category": "decor", "pos": [40, 150, 0]},
            {"id": "o9", "prop": "ctable", "name": "VIP table R 1", "category": "decor", "pos": [440, 150, 0]},
            {"id": "o10", "prop": "floor", "name": "Dance floor", "category": "floor_area", "aliases": ["the floor"], "pos": [240, 180, 0]},
        ],
    }


# ------------------------------------------------------------------------------------------------ engine

@pytest.mark.parametrize("inches,text", [(12, "1' 0\""), (6, '6"'), (-30, "-2' 6\""), (9.5, '9 1/2"'), (0, '0"'), (66, "5' 6\"")])
def test_lengths_read_like_the_editor(inches, text):
    assert sc.fmt_len(inches) == text


def test_footprints_follow_the_parts_and_the_turn():
    doc = room()
    s = sc.Scene(doc)
    bar = s.box(sc.Ref("object", "o3"))
    assert (bar.x0, bar.x1, bar.y0, bar.y1, bar.z1) == (120, 360, 318, 342, 42)
    doc["objects"][2]["rot"] = [0, 0, 90]
    turned = sc.Scene(doc).box(sc.Ref("object", "o3"))
    assert round(turned.w) == 24 and round(turned.d) == 240
    stool = s.box(sc.Ref("object", "o5"))
    assert (stool.z0, stool.z1) == (0, 30)  # a model part stands on its position


@pytest.mark.parametrize("phrase,ids,ambiguous", [
    ("table 1", ["o4"], []), ("cocktail table 2", ["o7"], []), ("the bar", ["o3"], []), ("dj booth", ["o2"], []),
    ("vip table 1", [], ["o8", "o9"]), ("vip table l1", ["o8"], []), ("the stools", ["o5", "o6"], []), ("table 12", [], []),
    ("the tables", ["o4", "o7"], []), ("stool 1a", ["o5"], []),
])
def test_things_are_found_by_name(phrase, ids, ambiguous):
    found, cands = sc.Scene(room()).find(phrase)
    assert sorted(found) == sorted(ids) and sorted(cands) == sorted(ambiguous)


def test_a_tables_stools_come_along_by_parent():
    assert sorted(sc.Scene(room()).children("o4")) == ["o5", "o6"]


@pytest.mark.parametrize("words,text,vec", [
    ("left", "move it left", (-1, 0, 0)), ("right", "a foot to the right from the stage", (1, 0, 0)),
    ("right", "a foot to the right as seen from the dance floor", (-1, 0, 0)), ("stage left", "", (-1, 0, 0)),
    ("house left", "", (1, 0, 0)), ("raise", "", (0, 0, 1)), ("lower", "", (0, 0, -1)), ("back", "", (0, -1, 0)),
    ("front", "", (0, 1, 0)), ("sideways", "", None),
])
def test_directions_follow_the_viewpoint(words, text, vec):
    got = sc.direction_vector(words, text)
    assert (got is None and vec is None) or tuple(round(v) for v in got) == vec


def test_changes_apply_undo_and_refuse_a_stale_scene():
    doc = room()
    s = sc.Scene(doc)
    refs = [sc.Ref("object", "o4"), sc.Ref("object", "o5"), sc.Ref("fixture", "1")]
    changes = sc.move_changes(s, refs, (12, 0, 0)) + sc.rotate_changes(s, [sc.Ref("object", "o4")], 90) \
        + sc.hang_changes(s, [sc.Ref("fixture", "2")], "floor") + sc.remove_changes(s, ["o7"])
    new = sc.apply_changes(doc, changes)
    t1 = next(o for o in new["objects"] if o["id"] == "o4")
    assert t1["pos"] == [132, 280, 0] and t1["rot"] == [0, 0, 90] and t1["rz"] == 90
    assert new["fixtures"]["1"]["pos"] == [112, 100, 150] and new["fixtures"]["2"]["hang"] == "floor"
    assert all(o["id"] != "o7" for o in new["objects"])
    back = sc.apply_changes(new, sc.invert(changes))
    assert json.dumps(back, sort_keys=True) == json.dumps(doc, sort_keys=True) or \
        [o for o in back["objects"] if o["id"] == "o4"][0].get("rot") is None  # the legacy yaw comes back as it was
    assert back["fixtures"] == doc["fixtures"] and sorted(o["id"] for o in back["objects"]) == sorted(o["id"] for o in doc["objects"])
    with pytest.raises(sc.SceneError):  # someone moved the table in the browser meanwhile
        sc.apply_changes(new, changes)


def test_free_spots_keep_clear_of_furniture_and_the_dance_floor():
    s = sc.Scene(room())
    ref, _ = s.ref_box("the bar")
    x, y, side = s.spot(ref, "next_to", 15, 15, set())
    b = sc.Box(x - 15, x + 15, y - 15, y + 15)
    assert s.free(b, set()) and side in ("right", "left", "front", "back")
    assert not b.overlaps(s.box(sc.Ref("object", "o10")))


def test_new_things_bring_their_shapes_and_sit_around_their_table():
    s = sc.Scene(room())
    layout, hw, hd = sc.layout_group(s, [("high_top", 1), ("stool", 4)])
    assert layout[0][0] == "high_top" and len(layout) == 5
    rings = {round((dx * dx + dy * dy) ** 0.5) for k, dx, dy, _ in layout if k == "stool"}
    assert len(rings) == 1 and hw == hd
    key, add = sc.prop_for(s.doc, "high_top")
    assert key == "lightai-high-top-table" and add["op"] == "add_prop"
    assert sc.prop_for(s.doc, "stool") == ("stool", None)  # the show's own stool
    assert sc.kind_of("high top table") == "high_top" and sc.kind_of("bar stools") == "stool" and sc.kind_of("couch") == "sofa"
    assert sc.kind_of("spaceship") is None


# ------------------------------------------------------------------------------------------------ the operator's sentences

needs_show = pytest.mark.skipif(MODEL is None or not HAS_STAGE, reason="needs the promoted model and the main show's 3D stage")

SENTENCES = {
    "move fixture 12 one foot to the left": "fixture_edit.move | move [target:fixture 12] [distance:one foot] to the [direction:left]",
    "Move table 3 with its chairs a foot to the right from the stage":
        "fixture_edit.move | move [target:table 3] with its chairs [distance:a foot] to the [direction:right] from the stage",
    "place fixture 7 on coordinate 5 foot by 5 foot": "fixture_edit.move | place [target:fixture 7] on coordinate [coordinates:5 foot by 5 foot]",
    "rotate fixture 8 upside down": "fixture_edit.rotate | rotate [target:fixture 8] [angle:upside down]",
    "rotate cocktail table 1 90 degrees": "fixture_edit.rotate | rotate [target:cocktail table 1] [angle:90 degrees]",
    "add a high top table and 4 stools next to the DJ booth":
        "scene.add | add a [object:high top table] and [count:4] [object:stools] [place:next to the dj booth]",
    "remove bar stool 4": "scene.remove | remove [target:bar stool 4]",
    "move vip booth 1 a foot left": "fixture_edit.move | move [target:vip booth 1] [distance:a foot] [direction:left]",
    "mount fixture 4 on the wall": "fixture_edit.rotate | mount [target:fixture 4] on the wall",
    "add 2 speakers on either side of the stage": "scene.add | add [count:2] [object:speakers] [place:on either side of the stage]",
}


@pytest.fixture(scope="module")
def ai():
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from standin import stand_in_parser

    from lightai.app import LightAI

    a = LightAI(load_config(), model_dir=MODEL, use_embeddings=False)
    a.parser = stand_in_parser(a, SENTENCES)
    return a


def _changes(plan) -> list:
    return [ch for a in plan.actions if a.op == "edit_stage" for ch in a.args["changes"]]


@needs_show
def test_move_a_table_with_its_chairs_seen_from_the_stage(ai):
    doc = sc.read_doc(stage_path(MAIN))
    _, plan = ai.plan("Move table 3 with its chairs a foot to the right from the stage")
    ch = _changes(plan)
    moved = {c["id"] for c in ch}
    table = next(o for o in doc["objects"] if o["name"] == "Cocktail table 3")
    stools = {o["id"] for o in doc["objects"] if o.get("parent") == "Cocktail table 3"}
    assert plan.mode == "structural" and moved == {table["id"]} | stools and len(stools) == 2
    assert all(round(c["value"][0] - c["before"][0]) == 12 and c["value"][1] == c["before"][1] for c in ch)


@needs_show
def test_fixture_moves_places_flips_and_mounts(ai):
    doc = sc.read_doc(stage_path(MAIN))
    _, plan = ai.plan("move fixture 12 one foot to the left")
    (c,) = _changes(plan)
    assert c["kind"] == "fixture" and c["id"] == "12" and round(c["before"][0] - c["value"][0]) == 12
    _, plan = ai.plan("place fixture 7 on coordinate 5 foot by 5 foot")
    (c,) = _changes(plan)
    assert c["value"][:2] == [doc["anchor"][0] + 60, doc["anchor"][1] + 60]
    _, plan = ai.plan("rotate fixture 8 upside down")
    (c,) = _changes(plan)
    assert c["field"] == "hang" and {c["before"], c["value"]} <= {"hung", "floor"} and c["before"] != c["value"]
    _, plan = ai.plan("mount fixture 4 on the wall")
    assert _changes(plan)[0]["value"] == "wall"


@needs_show
def test_turn_add_remove_and_ask(ai):
    _, plan = ai.plan("rotate cocktail table 1 90 degrees")
    (c,) = _changes(plan)
    assert c["field"] == "rot" and c["value"][2] == -90.0  # clockwise seen from above
    _, plan = ai.plan("add a high top table and 4 stools next to the DJ booth")
    adds = [c["object"] for c in _changes(plan) if c["op"] == "add"]
    table = adds[0]
    assert table["name"].startswith("High-top table") and len(adds) == 5
    assert all(o.get("parent") == table["name"] for o in adds[1:])
    assert any(c["op"] == "add_prop" for c in _changes(plan))
    _, plan = ai.plan("remove bar stool 4")
    (c,) = _changes(plan)
    assert c["op"] == "remove" and c["before"]["name"] == "Bar stool 4"
    _, plan = ai.plan("move vip booth 1 a foot left")
    assert plan.mode == "clarify" and "VIP booth L 1" in plan.summary and "VIP booth R 1" in plan.summary
    _, plan = ai.plan("add 2 speakers on either side of the stage")
    xs = sorted(c["object"]["pos"][0] for c in _changes(plan) if c["op"] == "add")
    assert len(xs) == 2 and xs[0] < 216 < 360 < xs[1]  # either side of the DJ stage deck


# ------------------------------------------------------------------------------------------------ saving and undo

@pytest.fixture()
def env(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from standin import stand_in_parser

    from lightai.app import LightAI

    if MODEL is None or not HAS_STAGE:
        pytest.skip("needs the promoted model and the main show's 3D stage")
    show = tmp_path / "show" / "Main Project.qxw"
    show.parent.mkdir()
    shutil.copy(MAIN, show)
    shutil.copy(stage_path(MAIN), stage_path(show))
    cfg = copy.copy(load_config())
    cfg.project_path, cfg.main_project_path = show, None
    cfg.data_dir = tmp_path / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp_path / "learned.yaml"
    fake = FakeQlc(show, fork=True, stage=True).start()
    cfg.qlc_url = fake.url
    a = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    a.parser = stand_in_parser(a, SENTENCES)
    loop = asyncio.new_event_loop()
    yield SimpleNamespace(ai=a, cfg=cfg, fake=fake, run=loop.run_until_complete, stage=stage_path(show))
    loop.run_until_complete(a.executor.client.close())
    loop.close()
    fake.stop()


def test_saved_through_qlc_then_undone(env):
    before = sc.read_doc(env.stage)
    _, plan = env.ai.plan("Move table 3 with its chairs a foot to the right from the stage")
    res = env.run(env.ai.executor.execute(plan, confirm=True))
    edit = next(r for r in res["results"] if r["op"] == "edit_stage")
    assert res["ok"] and edit["saved"] == "qlc" and edit["rev"] == 1 and Path(edit["backup"]).exists()
    after = sc.read_doc(env.stage)
    t3 = next(o for o in after["objects"] if o["name"] == "Cocktail table 3")
    t3_was = next(o for o in before["objects"] if o["name"] == "Cocktail table 3")
    assert t3["pos"][0] == t3_was["pos"][0] + 12
    undo = env.ai.undo_plan(plan.plan_id)
    assert undo is not None and undo.actions[0].op == "edit_stage"
    assert env.run(env.ai.executor.execute(undo, confirm=True))["ok"]
    again = next(o for o in sc.read_doc(env.stage)["objects"] if o["name"] == "Cocktail table 3")
    assert again["pos"] == t3_was["pos"]


def test_a_scene_changed_in_the_browser_is_not_overwritten(env):
    _, plan = env.ai.plan("remove bar stool 4")
    doc = sc.read_doc(env.stage)
    for o in doc["objects"]:
        if o["name"] == "Bar stool 4":
            o["pos"][0] += 30  # the operator dragged it meanwhile
    sc.write_doc(env.stage, doc)
    res = env.run(env.ai.executor.execute(plan, confirm=True))
    assert not res["ok"] and "moved since this was planned" in json.dumps(res)
    assert any(o["name"] == "Bar stool 4" for o in sc.read_doc(env.stage)["objects"])


def test_without_the_3d_stage_build_the_file_is_written(env):
    env.fake.stage = False  # an older QLC+ build: no saveStage
    _, plan = env.ai.plan("remove bar stool 4")
    res = env.run(env.ai.executor.execute(plan, confirm=True))
    edit = next(r for r in res["results"] if r["op"] == "edit_stage")
    assert res["ok"] and edit["saved"] == "file"
    assert not any(o["name"] == "Bar stool 4" for o in sc.read_doc(env.stage)["objects"])
