"""Regression tests for the iteration 3 fixes (validation agents' findings H1, M2, M3, D17-D19, retriever)."""

import asyncio
import copy
from types import SimpleNamespace as N

import pytest

from fakeqlc import FakeQlc
from lightai.compiler.recipes import look_name
from lightai.compiler.spec import LookParams
from lightai.config import load_config
from lightai.devtools import make_test_project
from lightai.nlu.normalize import norm_priority, norm_rate, norm_size
from lightai.nlu.retriever import Retriever
from lightai.schema import LightCommand, SlotValue, Span

MODEL = load_config().current_model_dir()


def test_retriever_exact_parts_duplicates_and_spoken_tempo():
    names = ["Blue Wash Breathe 60bpm - Color", "Blue Wash Breathe 60bpm - Breath", "Blue Wash Breathe 60bpm",
             "Red Spots Wash", "Red Spots Wash", "Auto Chaser Show 3", "Auto Chaser Show 5"]
    r = Retriever({i: N(id=i, name=n, path="AI", type="Scene") for i, n in enumerate(names)})
    exact = r.resolve("blue wash breathe 60bpm")
    assert exact["function_id"] == 2 and not exact.get("ambiguous"), "the look itself, not its '- Color' part"
    assert r.resolve("blue wash breathe sixty bpm")["function_id"] == 2
    part = r.resolve("blue wash breathe")
    assert part["function_id"] == 2 and not part.get("ambiguous"), "a look's own parts never make it ambiguous"
    dup = r.resolve("red spots wash")
    assert dup.get("ambiguous") and dup.get("same_name") == 2
    assert r.resolve("auto chaser show").get("ambiguous")
    assert r.resolve("auto chaser show 3")["function_id"] == 5


def test_priority_is_never_a_tempo_and_wide_is_a_size():
    assert norm_rate("p100") == {} and norm_rate("priority 5") == {}
    assert norm_priority("p100") == {"priority": 100}
    assert norm_rate("128 bpm")["bpm"] == 128
    assert norm_size("wide") and not norm_rate("wide")


def test_look_names_static_and_mirrored():
    static = LookParams(recipe="color_wash", targets=[1], target_label="spots", colors=["red"], rate_word="medium")
    assert look_name(static, "Wash") == "Red Spots Wash"
    moving = LookParams(recipe="circle", targets=[1], target_label="spots", colors=["red"], rate_word="medium", mirror=True)
    assert look_name(moving, "Circles") == "Red Spots Circles medium Mirrored"


def _cmd(text, intent, slots, spans=()):
    words = text.split()
    return LightCommand(text=text, words=words, intent=intent, confidence=0.99, slots=slots, spans=list(spans))


def test_repairs_without_model(rig):
    from lightai.nlu.normalize import normalize_slot
    from lightai.nlu.pipeline import Parser

    p = Parser(rig, model=None, retriever=Retriever(rig.functions))
    # 'wash 1 left' tagged as one target: the trailing direction is split off
    c = _cmd("move wash 1 left 50 cm", "fixture_edit.move",
             {"target": [SlotValue(raw="wash 1 left", value=normalize_slot(rig, "target", "wash 1 left"))],
              "distance": [SlotValue(raw="50 cm", value={"mm": 500})]},
             [Span(slot="target", start=1, end=4, text="wash 1 left"), Span(slot="distance", start=4, end=6, text="50 cm")])
    p.repair(c)
    assert c.slots["direction"][0].value["dir"] == "left" and c.slots["target"][0].value.get("fixture_ids")
    # 'up' left untagged
    c = _cmd("move fixture 3 up 30 cm", "fixture_edit.move",
             {"target": [SlotValue(raw="fixture 3", value=normalize_slot(rig, "target", "fixture 3"))]},
             [Span(slot="target", start=1, end=3, text="fixture 3")])
    p.repair(c)
    assert c.slots["direction"][0].value["dir"] == "up"
    # 'p100' untagged on a new look
    c = _cmd("create a kill for the spots at p100", "create_look",
             {"target": [SlotValue(raw="spots", value=normalize_slot(rig, "target", "spots"))]},
             [Span(slot="target", start=5, end=6, text="spots")])
    p.repair(c)
    assert c.slots["priority"][0].value == {"priority": 100}
    # 'wide' tagged as a rate becomes a size
    c = _cmd("purple circles on the spots slow wide", "create_look",
             {"rate": [SlotValue(raw="wide", value={})]}, [Span(slot="rate", start=6, end=7, text="wide")])
    p.repair(c)
    assert c.slots.get("size") and not c.slots.get("rate")


@pytest.mark.skipif(MODEL is None, reason="no promoted model")
def test_chase_padding_adds_exactly_one_color(rig):
    from lightai.planner import Planner
    from lightai.prefs import Prefs
    from lightai.session import Session

    cfg = rig.cfg
    planner = Planner(rig, Prefs(cfg, rig.overrides), Session(), cfg)
    c = _cmd("red chase on the tetras", "create_look",
             {"color": [SlotValue(raw="red", value={"name": "red"})],
              "movement": [SlotValue(raw="chase", value={"recipe": "color_chase"})],
              "target": [SlotValue(raw="tetras", value={"fixture_ids": sorted(rig.zones.get("tetras") or [])[:2] or [16, 17]})]})
    params, _ = planner.look_params(c)
    assert len(params.colors) == 2 and params.colors[0] == "red"


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from lightai.app import LightAI

    if MODEL is None:
        pytest.skip("no promoted model")
    tmp = tmp_path_factory.mktemp("iter3")
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


def test_concurrent_previews_leave_nothing_stuck(live):
    """H1: three previews started at the same moment, then /stop: no Simple Desk channel may stay overridden."""
    ai, fake = live["ai"], live["fake"]
    plans = [ai.plan(t)[1] for t in ("make an assumption on the washes", "make an assumption on fixture 5 and make it move",
                                     "make an assumption on the spots")]
    for p in plans:
        for a in p.actions:
            if a.op == "preview":
                a.args["seconds"] = 1.0

    async def go():
        await asyncio.gather(*(ai.executor.execute(p) for p in plans))
        await asyncio.sleep(0.2)
        await ai.executor.stop_preview()
        await asyncio.sleep(0.2)

    live["run"](go())
    assert not fake.override, f"{len(fake.override)} channels left overridden"
    assert ai.executor.preview_task is None and not ai.executor.preview_channels


def test_wheel_calibration_named_color_is_not_yes(live):
    """M2: 'that's pink' during 'find blue' records pink and moves on; it must not confirm blue."""
    ai = live["ai"]
    cmd, plan = ai.plan("find blue on fixture 34")
    res = live["run"](ai.executor.execute(plan, confirm=True))
    assert res["ok"] and res["results"][0]["calibration"]
    r = live["run"](ai.executor.calibrate_answer("that's pink"))
    assert r.get("calibration") is not False or r.get("result") != "found", r
    assert "pink" in (r.get("learned") or ""), r
    r = live["run"](ai.executor.calibrate_answer("not blue"))
    assert r.get("result") != "found", "'not blue' is never a yes"
    live["run"](ai.executor.calibrate_end("test done"))
    assert ai.session.calibration is None and not live["fake"].override


def test_rig_reload_is_fast_and_isolated(live):
    import time

    ai = live["ai"]
    ai.reload_rig()
    t0 = time.perf_counter()
    for _ in range(3):
        ai.reload_rig()
    assert (time.perf_counter() - t0) / 3 < 0.3, "rig reload must stay cached"
    ai.rig.overrides.setdefault("rules", {})["dim_floor_pct"] = 99
    ai.reload_rig()
    assert ai.rig.rules.get("dim_floor_pct") != 99, "cached YAML must be copied, never shared"


@pytest.mark.parametrize("intent,conf,text,accepted", [
    ("blackout", 0.77, "black it out", True),
    ("blackout", 0.62, "we need darkness right now", True),
    ("blackout", 0.62, "the dj wants darkness later", False),
    ("blackout", 0.70, "don't black it out", False),
    ("blackout", 0.30, "black it out", False),
    ("stop_all", 0.70, "stop everything when the song ends", False),
    ("set_level", 0.70, "washes at 40%", False),
])
def test_policy_accepts_clear_wording_only(intent, conf, text, accepted):
    from lightai.nlu.policy import apply_policy

    cmd = LightCommand(text=text, words=text.split(), intent=intent, confidence=conf, intent_top=[(intent, conf), ("none", 0.1)])
    apply_policy(cmd)
    assert (cmd.clarify is None or "not sure" not in cmd.clarify) == accepted, cmd.clarify
