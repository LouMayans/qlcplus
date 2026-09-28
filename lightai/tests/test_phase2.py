"""update_look / delete_look / add_widget / add_fixture and feedback -> look correction (fake QLC+).

Commands are built from slot text through the real normalizers and retriever, so these tests don't
depend on which labels the promoted model knows.
"""

import asyncio
import copy
import json

import pytest

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.devtools import make_test_project
from lightai.nlu.normalize import normalize_slot
from lightai.nlu.policy import apply_policy
from lightai.rig.qxw import Workspace
from lightai.schema import Issue, LightCommand, OutcomeFeedback, SlotValue

MODEL = load_config().current_model_dir()
pytestmark = pytest.mark.skipif(MODEL is None, reason="no promoted model")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from lightai.app import LightAI

    tmp = tmp_path_factory.mktemp("phase2")
    cfg = copy.copy(load_config())
    cfg.data_dir = tmp / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp / "learned.yaml"
    cfg.project_path = make_test_project(tmp / "test-project")
    fake = FakeQlc(cfg.project_path, fork=True).start()
    cfg.qlc_url = fake.url
    cfg.reload_strategy = "auto"
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    loop = asyncio.new_event_loop()
    yield {"ai": ai, "fake": fake, "cfg": cfg, "run": loop.run_until_complete}
    loop.run_until_complete(ai.executor.client.close())
    loop.close()
    fake.stop()


def command(ai, intent, text, **slots):
    cmd = LightCommand(text=text, intent=intent, confidence=0.99)
    for slot, raws in slots.items():
        for raw in (raws if isinstance(raws, list) else [raws]):
            value = ai.retriever.resolve(raw) if slot == "function_ref" else normalize_slot(ai.rig, slot, raw, "", intent)
            cmd.slots.setdefault(slot, []).append(SlotValue(raw=raw, value=value))
    return apply_policy(cmd)


def run(env, cmd, **kw):
    ai = env["ai"]
    plan = ai.planner.plan(cmd)
    kw.setdefault("confirm", True)
    kw.setdefault("allow_running_reload", True)
    return plan, env["run"](ai.executor.execute(plan, **kw))


def sidecar(env):
    return json.loads((env["cfg"].project_path.parent / "lightai-looks.json").read_text())["looks"]


def test_update_look_in_place(env):
    ai = env["ai"]
    cmd, plan = ai.plan("slow blue wash breathing at 60 BPM")
    res = env["run"](ai.executor.execute(plan, confirm=True))
    assert res["ok"], res
    name, ids = plan.look["name"], plan.look["functions"]
    plan, res = run(env, command(ai, "update_look", f"make {name} faster", function_ref=name.lower(), rate="faster"))
    assert plan.intent == "update_look" and res["ok"], (plan.summary, res)
    up = next(r for r in res["results"] if r["op"] == "update_look")
    assert up["in_place"] and up["ids"] == [f["id"] for f in ids]
    entry = next(e for e in sidecar(env).values() if e["main_id"] == up["main_id"])
    assert entry["params"]["rate_word"] == "medium", "slow -> one step faster"
    plan, res = run(env, command(ai, "update_look", f"change {name} to red", function_ref=name.lower(), color="red"))
    assert res["ok"], res
    entry = next(e for e in sidecar(env).values() if e["main_id"] == up["main_id"])
    assert entry["params"]["colors"] == ["red"]


def test_update_refuses_non_ai_functions(env):
    ai = env["ai"]
    cmd = command(ai, "update_look", "make blah faster", function_ref="blah blah nothing", rate="faster")
    plan = ai.planner.plan(cmd)
    assert plan.mode == "clarify"


def test_add_widget_then_delete_look(env):
    ai = env["ai"]
    entry = next(iter(sidecar(env).values()))
    plan, res = run(env, command(ai, "add_widget", f"add a button for {entry['name']}", function_ref=entry["name"].lower()))
    assert res["ok"], res
    w = next(r for r in res["results"] if r["op"] == "add_widget")
    ws = Workspace.load(env["cfg"].project_path)
    btn = next(x for x in ws.widgets() if x.id == w["widget_id"])
    assert btn.type == "Button" and btn.function_id == entry["main_id"]
    assert any(x.type == "Frame" and x.caption == "lightai" for x in ws.widgets())
    plan, res = run(env, command(ai, "add_widget", "again", function_ref=entry["name"].lower()))
    assert next(r for r in res["results"] if r["op"] == "add_widget")["existing"]
    plan, res = run(env, command(ai, "delete_look", f"delete {entry['name']}", function_ref=entry["name"].lower()))
    assert res["ok"], res
    d = next(r for r in res["results"] if r["op"] == "delete_look")
    assert set(d["deleted_functions"]) == set(entry["ids"]) and w["widget_id"] in d["deleted_buttons"]
    ws = Workspace.load(env["cfg"].project_path)
    assert not ({f.id for f in ws.functions()} & set(entry["ids"]))
    assert str(entry["main_id"]) not in sidecar(env)


def test_add_fixture_from_library(env):
    ai = env["ai"]
    plan, res = run(env, command(ai, "add_fixture", "add a chauvet intimidator spot 260 at universe 2 address 150",
                                 fixture_model="chauvet intimidator spot 260", address="universe 2 address 150"))
    assert res["ok"], (plan.summary, res)
    fid = plan.actions[0].args["id"]
    fx = next(f for f in Workspace.load(env["cfg"].project_path).fixtures() if f.id == fid)
    assert fx.manufacturer == "Chauvet" and "Intimidator" in fx.model and fx.universe == 1 and fx.address == 149
    assert ai.rig.fixtures[fid].can_move, "the new mover's roles come from its .qxf"
    clash = ai.planner.plan(command(ai, "add_fixture", "overlap", fixture_model="chauvet intimidator spot 260", address="universe 2 address 150"))
    assert clash.mode == "clarify" and "overlaps" in clash.summary


def test_feedback_prepares_look_update(env):
    ai = env["ai"]
    cmd, plan = ai.plan("green circles on the spots at 128")
    res = env["run"](ai.executor.execute(plan, confirm=True, allow_running_reload=True))
    assert res["ok"], res
    ai.session.last_action_plan = plan
    fb = ai.planner.plan(LightCommand(text="too fast", intent="feedback", confidence=0.99))
    assert fb.followup.get("update_plan_id"), fb.summary
    upd = ai.session.plans[fb.followup["update_plan_id"]]
    res = env["run"](ai.executor.execute(upd, confirm=True, allow_running_reload=True))
    assert res["ok"], res
    entry = next(e for e in sidecar(env).values() if e["main_id"] == plan.look["main_id"])
    assert entry["params"]["rate_word"] in ("slow", "very_slow")


def test_feedback_adjusts_proposal(env):
    ai = env["ai"]
    cmd, plan = ai.plan("make an assumption on fixture 5 and make it move")
    ai.session.last_action_plan = plan
    before = dict(plan.followup["apply"])
    res = env["run"](ai.executor.apply_feedback(OutcomeFeedback(plan_id=plan.plan_id, verdict="adjust", issues=[Issue(kind="size", direction="too_small")])))
    tf = ai.planner.taste_followup(plan, [{"kind": "size", "direction": "too_small"}])
    assert tf.get("adjusted_plan_id") == plan.plan_id
    assert plan.followup["apply"]["size"] != before.get("size")
