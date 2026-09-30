"""Live aiming by beam references ('triangulating' the 3D stage against the real room by eye): spot 2 straight down,
spot 3 (or every beam) where spot 2's beam lands, 'do it again'. Runs on a copy of the main show and its 3D stage with a
fake QLC+ that keeps the channel values it is sent; the check follows every beam, from the values actually written,
down to the floor."""

import asyncio
import copy
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from fakeqlc import FakeQlc
from lightai.app import AGAIN
from lightai.config import load_config
from lightai.rig import aim_live
from lightai.rig.stage import stage_path

MAIN = load_config().project_path
MODEL = load_config().current_model_dir()
HAS_STAGE = stage_path(MAIN).exists()
needs_show = pytest.mark.skipif(MODEL is None or not HAS_STAGE, reason="needs the promoted model and the main show's 3D stage")

ANSWERS = {
    "place spot 2 white beam no flashing on straight down": "create_look | place [target:spot 2] [color:white] beam no flashing on straight down",
    "point spot 3 right where the beam ends and collides with the floor":
        "create_look | point [target:spot 3] right where the beam ends and collides with the floor",
    "all beams point to spot 2 beam that ends on the floor": "create_look | all [target:beams] point to spot 2 beam that ends on the floor",
}


def test_a_beam_meets_the_floor_below_a_hung_fixture():
    hung = {"pos": [100, 200, 150], "rot": [0, 0, 0], "hang": "hung"}
    assert aim_live.floor_hit(hung, 0, 0) == pytest.approx((100, 200, 0))
    tilted = aim_live.floor_hit(hung, 0, 45)  # 45 degrees off vertical: 150 in down, 150 in across
    assert tilted[2] == 0 and ((tilted[0] - 100) ** 2 + (tilted[1] - 200) ** 2) ** 0.5 == pytest.approx(150, abs=0.01)
    assert aim_live.floor_hit({"pos": [0, 0, 50], "rot": [0, 0, 0], "hang": "floor"}, 0, 0) is None  # pointing up


@pytest.mark.parametrize("text,again", [("do it again", True), ("again", True), ("re-aim", True), ("aim them again", True),
                                        ("one more time", True), ("redo", True), ("do it again with red", False),
                                        ("again the spots at 50%", False)])
def test_do_it_again_is_only_the_bare_request(text, again):
    assert bool(AGAIN.match(text)) is again


@pytest.fixture()
def env(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from standin import use_stand_in

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
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    use_stand_in(ai, ANSWERS)
    loop = asyncio.new_event_loop()
    yield SimpleNamespace(ai=ai, fake=fake, run=loop.run_until_complete)
    loop.run_until_complete(ai.executor.client.close())
    loop.close()
    fake.stop()


def _lands(env, fid):
    fx = env.ai.rig.fixtures[fid]
    v = {}
    for r in ("pan", "pan_fine", "tilt", "tilt_fine"):
        if fx.has(r):
            u, a = fx.dmx(fx.ch(r))
            v[r] = env.fake.values.get((u - 1) * 512 + a, 0)
    return aim_live.beam_floor_point(env.ai.rig.stage, fx, (v.get("pan", 0) << 8) | v.get("pan_fine", 0),
                                     (v.get("tilt", 0) << 8) | v.get("tilt_fine", 0))


def _run(env, text):
    cmd, plan = env.ai.plan(text)
    assert plan.mode == "live" and plan.actions[0].op == "aim_live", (text, plan.mode, plan.summary)
    res = env.run(env.ai.executor.execute(plan, confirm=True))
    assert res["ok"], res
    return next(r for r in res["results"] if r["op"] == "aim_live")


def test_beams_are_sent_where_another_beam_lands(env):
    first = _run(env, "place spot 2 white beam no flashing on straight down")
    ref = first["aimed"][0]
    below = env.ai.rig.stage.pos(ref)
    down = _lands(env, ref)
    assert down[0] == pytest.approx(below[0], abs=0.2) and down[1] == pytest.approx(below[1], abs=0.2)  # straight down
    second = _run(env, "point spot 3 right where the beam ends and collides with the floor")  # 'the beam': the last one aimed
    assert "beam lands at" in second["note"]
    spot3 = second["aimed"][0]
    hit = _lands(env, spot3)
    assert ((hit[0] - down[0]) ** 2 + (hit[1] - down[1]) ** 2) ** 0.5 < 0.5
    third = _run(env, "all beams point to spot 2 beam that ends on the floor")
    assert ref not in third["aimed"] and len(third["aimed"]) >= 10  # the reference keeps its aim
    worst = max(((h[0] - down[0]) ** 2 + (h[1] - down[1]) ** 2) ** 0.5 for h in (_lands(env, f) for f in third["aimed"]))
    assert worst < 0.5, worst
    again = _run(env, "do it again")
    assert again["aimed"] == third["aimed"]


def test_live_aims_are_released(env):
    _run(env, "place spot 2 white beam no flashing on straight down")
    assert env.ai.session.overridden
    cmd, plan = env.ai.plan("release")
    assert env.run(env.ai.executor.execute(plan, confirm=True))["ok"]
    assert not env.ai.session.overridden


@needs_show
@pytest.mark.parametrize("text,live", [
    ("place spot 2 white beam no flashing on straight down", True),
    ("all beams point to spot 2 beam that ends on the floor", True),
    ("lets say all beams point to spot 2 beam that ends on the floor so they all overlap", True),
    ("point spot 4 at the same spot as spot 2", True),
    ("aim the spots at the dj", False),  # a zone aimed at a place stays a saved look
])
def test_the_operators_sentences_with_the_real_model(text, live):
    from lightai.app import LightAI

    ai = LightAI(load_config(), model_dir=MODEL, use_embeddings=False)
    cmd, plan = ai.plan(text)
    assert (plan.mode == "live" and plan.actions[0].op == "aim_live") is live, (plan.mode, plan.summary)


@needs_show
def test_the_beam_starts_at_the_tilt_axis_like_the_3d_page():
    """The 3D page fits QLC+'s moving head to 20 in (the club's BEAM230): the beam leaves at the tilt axis, 13.91 in
    from the clamp along the fixture's axis; for a hung spot that is straight below its position."""
    from lightai.rig.model import Rig
    from lightai.rig.stage import MOVING_HEAD_TILT_AXIS_IN

    rig = Rig.load(load_config())
    fx = next(f for f in rig.fixtures.values() if f.has("pan") and f.has("tilt") and rig.stage.fixtures.get(f.id, {}).get("hang") == "hung")
    pos = rig.stage.pos(fx.id)
    origin = rig.stage.beam_origin(fx)
    assert MOVING_HEAD_TILT_AXIS_IN == pytest.approx(13.906, abs=0.01)
    assert origin[0] == pytest.approx(pos[0]) and origin[1] == pytest.approx(pos[1])
    assert origin[2] == pytest.approx(pos[2] - 13.906, abs=0.01)
    down = rig.stage.aim(fx, (pos[0], pos[1], 0.0))  # straight down still lands right below it
    lands = aim_live.floor_hit(rig.stage.fixtures[fx.id], down["pan_deg"], down["tilt_deg"], origin=origin)
    assert lands[0] == pytest.approx(pos[0], abs=0.1) and lands[1] == pytest.approx(pos[1], abs=0.1)
