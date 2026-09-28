"""Iteration 5: add several fixtures at free addresses, and move fixtures to other DMX addresses."""

import asyncio
import copy

import pytest

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.devtools import make_test_project
from lightai.nlu.normalize import norm_address, norm_count, norm_mode, prefer_rig_model, resolve_library_model
from lightai.rig.qxw import Workspace
from lightai.schema import LightCommand, SlotValue

MODEL = load_config().current_model_dir()


@pytest.mark.parametrize("raw,channels", [("7address type", 7), ("7 channel mode", 7), ("7-ch", 7), ("14 dmx channels", 14), ("16ch", 16)])
def test_mode_phrases(raw, channels):
    assert norm_mode(raw)["channels"] == channels


@pytest.mark.parametrize("raw,count", [("3", 3), ("three", 3), ("a couple of", 2), ("3x", 3), ("twelve", 12)])
def test_counts(raw, count):
    assert norm_count(raw) == {"count": count}


def test_a_few_is_not_a_number():
    assert norm_count("a few") == {}


@pytest.mark.parametrize("raw,want", [("universe 1", {"universe": 0}), ("universe 2 address 150", {"universe": 1, "address": 149}),
                                      ("starting at 200", {"address": 199}), ("2.300", {"universe": 1, "address": 299})])
def test_addresses(raw, want):
    assert norm_address(raw) == want


def test_library_spellings_are_one_model(rig):
    v = prefer_rig_model(rig, resolve_library_model(rig.library, "vpar"))
    assert v["model"].lower() == "vpar" and not v.get("ambiguous")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from lightai.app import LightAI

    if MODEL is None:
        pytest.skip("no promoted model")
    tmp = tmp_path_factory.mktemp("iter5")
    cfg = copy.copy(load_config())
    cfg.data_dir = tmp / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp / "learned.yaml"
    cfg.project_path = make_test_project(tmp / "test-project")
    fake = FakeQlc(cfg.project_path, fork=True).start()
    cfg.qlc_url = fake.url
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    loop = asyncio.new_event_loop()
    yield {"ai": ai, "fake": fake, "cfg": cfg, "run": loop.run_until_complete}
    loop.run_until_complete(ai.executor.client.close())
    loop.close()
    fake.stop()


def _no_overlaps(ws: Workspace) -> bool:
    fx = [(f.universe, f.address, f.address + f.channels) for f in ws.fixtures()]
    return all(not (u1 == u2 and a1 < b2 and a2 < b1) for i, (u1, a1, b1) in enumerate(fx) for (u2, a2, b2) in fx[i + 1:])


def test_add_three_fixtures_at_free_addresses(env):
    ai, run, cfg = env["ai"], env["run"], env["cfg"]
    before = {f.id for f in ai.rig.fixtures.values()}
    cmd, plan = ai.plan("Add 3 vpar 7address type fixtures to universe 1")
    assert cmd.intent == "add_fixture" and cmd.first("count").value["count"] == 3, cmd.slots
    assert plan.mode == "structural", plan.summary
    res = run(ai.executor.execute(plan, confirm=True, allow_running_reload=True))
    assert res["ok"], res
    ws = Workspace.load(cfg.project_path)
    new = [f for f in ws.fixtures() if f.id not in before]
    assert len(new) == 3 and all(f.universe == 0 and f.channels == 7 and f.model.lower() == "vpar" for f in new)
    assert [f.address for f in sorted(new, key=lambda f: f.address)] == [new[0].address + 7 * k for k in range(3)] or _no_overlaps(ws)
    assert _no_overlaps(ws), "new fixtures must not overlap anything"


def _readdress(ai, ids, addr):
    return ai.planner.plan(LightCommand(text="move", words=["move"], intent="fixture_edit.readdress", confidence=0.99,
                                        slots={"target": [SlotValue(raw="x", value={"fixture_ids": ids})],
                                               "address": [SlotValue(raw="y", value=addr)]}))


def test_readdress_moves_and_keeps_the_patch_valid(env):
    ai, run, cfg = env["ai"], env["run"], env["cfg"]
    fx = sorted(ai.rig.fixtures.values(), key=lambda f: f.id)[-1]  # one of the fixtures added above
    plan = _readdress(ai, [fx.id], {"universe": 0, "address": 449})
    assert plan.mode == "structural" and "450" in plan.summary, plan.summary
    res = run(ai.executor.execute(plan, confirm=True, allow_running_reload=True))
    assert res["ok"], res
    moved = next(f for f in Workspace.load(cfg.project_path).fixtures() if f.id == fx.id)
    assert (moved.universe, moved.address) == (0, 449)
    assert _no_overlaps(Workspace.load(cfg.project_path))


def test_readdress_refuses_an_overlap(env):
    ai = env["ai"]
    a, b = sorted(ai.rig.fixtures.values(), key=lambda f: (f.universe, f.address))[:2]
    plan = _readdress(ai, [b.id], {"universe": a.universe, "address": a.address})
    assert plan.mode == "clarify" and "overlaps" in plan.summary


def test_readdress_to_a_universe_finds_a_free_block(env):
    ai = env["ai"]
    fx = sorted(ai.rig.fixtures.values(), key=lambda f: f.id)[-2]
    plan = _readdress(ai, [fx.id], {"universe": 1})
    assert plan.mode == "structural", plan.summary
    assert "universe 2" in plan.summary


def test_readdress_on_its_own_universe_without_an_address_asks(env):
    ai = env["ai"]
    fx = sorted(ai.rig.fixtures.values(), key=lambda f: f.id)[0]
    plan = _readdress(ai, [fx.id], {"universe": fx.universe})
    assert plan.mode == "info" and "Already on universe" in plan.summary, plan.summary


def test_automatic_placement_never_picks_the_current_spot(env):
    ai = env["ai"]
    fx = sorted(ai.rig.fixtures.values(), key=lambda f: f.id)[0]
    here = ((fx.address, fx.address + fx.channels),)
    a = ai.planner._place(fx.universe, [fx.channels], exclude=(fx.id,), avoid=here)[0]
    assert a + fx.channels <= fx.address or fx.address + fx.channels <= a


def test_an_explicit_address_may_overlap_the_old_spot(env):
    ai = env["ai"]
    fx = sorted(ai.rig.fixtures.values(), key=lambda f: f.id)[0]
    here = ((fx.address, fx.address + fx.channels),)
    assert ai.planner._place(fx.universe, [fx.channels], start=fx.address, exclude=(fx.id,), avoid=here) == [fx.address]
