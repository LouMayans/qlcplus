"""Regression tests for the iteration 4 fixes (round-2 agents: QLC+ N1-N16, language D1-D17, API D1-D25)."""

import asyncio
import copy
import time

import pytest

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.devtools import make_test_project
from lightai.nlu.normalize import norm_intensity
from lightai.nlu.policy import apply_policy
from lightai.schema import Issue, LightCommand, OutcomeFeedback, SlotValue

MODEL = load_config().current_model_dir()


def _cmd(text, intent, conf=0.99, slots=None):
    return LightCommand(text=text, words=text.split(), intent=intent, confidence=conf, intent_top=[(intent, conf)], slots=slots or {})


@pytest.mark.parametrize("text,intent", [
    ("blackout at the drop", "blackout"),
    ("should we blackout?", "blackout"),
    ("we won't go dark tonight", "blackout"),
    ("lights back on later", "blackout_release"),
    ("don't stop everything", "stop_all"),
    ("stop all the music", "stop_all"),
    ("prep the blackout", "blackout"),
])
def test_qualified_high_impact_commands_ask(text, intent):
    cmd = apply_policy(_cmd(text, intent, 0.99))
    assert cmd.clarify, f"'{text}' must not act immediately"


@pytest.mark.parametrize("text,intent", [("blackout", "blackout"), ("lights back on", "blackout_release"), ("stop everything", "stop_all")])
def test_plain_high_impact_commands_act(text, intent):
    assert apply_policy(_cmd(text, intent, 0.99)).clarify is None


def test_partial_blackout_asks():
    cmd = _cmd("blackout only the washes", "blackout", slots={"target": [SlotValue(raw="washes", value={"fixture_ids": [8, 9], "via": "zone washes"})]})
    assert apply_policy(cmd).clarify


def test_chatter_percentage_is_not_the_grand_master():
    lvl = {"intensity": [SlotValue(raw="5%", value={"level": 0.05})]}
    assert apply_policy(_cmd("battery at 5%", "set_level", 0.95, dict(lvl))).clarify
    assert apply_policy(_cmd("grand master 40%", "set_level", 0.95, {"intensity": [SlotValue(raw="40%", value={"level": 0.4})]})).clarify is None


def test_negated_tempo_is_not_set():
    rate = {"rate": [SlotValue(raw="90", value={"bpm": 90.0})]}
    assert apply_policy(_cmd("do not set the tempo to 90", "set_bpm", 0.95, dict(rate))).clarify
    assert apply_policy(_cmd("bpm 128", "set_bpm", 0.95, {"rate": [SlotValue(raw="128", value={"bpm": 128.0})]})).clarify is None


def test_p_numbers_are_never_intensity():
    assert norm_intensity("p20") == {} and norm_intensity("priority 50") == {}
    assert norm_intensity("20%")["level"] == pytest.approx(0.2)


def test_names_carry_intensity_and_fade():
    from lightai.compiler.recipes import look_name
    from lightai.compiler.spec import LookParams

    assert look_name(LookParams(recipe="color_wash", targets=[1], target_label="spots", colors=["red"], intensity=0.5), "Wash") == "Red Spots Wash 50%"
    strobe = LookParams(recipe="strobe", targets=[1], target_label="pars", colors=["white"], bpm=200, rate_word="fast")
    assert look_name(strobe, "Strobe") == "White Pars Strobe fast", "a strobe does not follow the BPM, so its name must not claim it"


def test_same_color_complaint_teaches_nothing(rig, tmp_path):
    from lightai.feedback import FeedbackRouter
    from lightai.prefs import Prefs

    cfg = copy.copy(rig.cfg)
    cfg.learned_path = tmp_path / "learned.yaml"
    cfg.data_dir = tmp_path
    fb = OutcomeFeedback(plan_id="p1", verdict="wrong", issues=[Issue(kind="color", fixture_id=14, expected="yellow", observed="yellow")])
    res = FeedbackRouter(rig, Prefs(cfg, rig.overrides), cfg).apply(fb, None)
    from lightai.rig.facts import LearnedFacts

    assert "rig_facts" not in res["routed_to"] and not LearnedFacts(cfg.learned_path).data.get("facts")


def test_mixed_models_complaint_asks_which_fixture(rig, tmp_path):
    from lightai.feedback import FeedbackRouter
    from lightai.prefs import Prefs
    from lightai.schema import Plan

    cfg = copy.copy(rig.cfg)
    cfg.learned_path = tmp_path / "learned.yaml"
    cfg.data_dir = tmp_path
    spots = sorted(rig.zones["spots"])
    assert len({rig.fixtures[i].key for i in spots}) > 1, "the test rig mixes BEAM230 models"
    plan = Plan(plan_id="p2", intent="create_look", mode="structural", summary="x", look={"params": {"targets": spots, "colors": ["blue"]}})
    fb = OutcomeFeedback(plan_id="p2", verdict="wrong", issues=[Issue(kind="color", expected="blue", observed="pink")])
    res = FeedbackRouter(rig, Prefs(cfg, rig.overrides), cfg).apply(fb, plan)
    assert any("no wheel fact was changed" in c for c in res["changes"]) and res["followups"]


def test_tap_tempo_folds_into_range():
    from lightai.session import Session

    s = Session()
    for t in (0.0, 1.9, 3.8):
        s.tap(t)
    assert 40 <= s.bpm <= 220 and s.bpm == pytest.approx(63.2, abs=0.1), "taps 1.9 s apart are 31.6 BPM, i.e. 63.2 at double time"


def test_plans_are_least_recently_used():
    from lightai.schema import Plan
    from lightai.session import Session

    s = Session()
    first = Plan(plan_id="keep", intent="none", mode="info", summary="")
    s.store(first)
    for i in range(299):
        s.store(Plan(plan_id=f"p{i}", intent="none", mode="info", summary=""))
    assert s.get("keep") is not None  # touching it keeps it
    s.store(Plan(plan_id="new", intent="none", mode="info", summary=""))
    assert s.get("keep") is not None and s.get("p0") is None


def test_client_fails_fast_while_qlc_is_down():
    from lightai.exec.wsclient import QlcClient, QlcError
    from fakeqlc import free_port

    async def go():
        c = QlcClient(f"ws://127.0.0.1:{free_port()}/qlcplusWS", timeout=0.5)
        with pytest.raises(QlcError):
            await c.ensure_connected()
        t0 = time.perf_counter()
        with pytest.raises(QlcError):
            await c.ensure_connected()
        return time.perf_counter() - t0

    assert asyncio.run(go()) < 0.1, "a second command within 3 s must not wait for another connect attempt"


def test_subscribe_is_idempotent():
    from lightai.exec.wsclient import QlcClient

    c = QlcClient("ws://127.0.0.1:1/qlcplusWS")
    cb = lambda m: None  # noqa: E731
    c.subscribe(cb)
    c.subscribe(cb)
    assert c._subs.count(cb) == 1


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from lightai.app import LightAI

    if MODEL is None:
        pytest.skip("no promoted model")
    tmp = tmp_path_factory.mktemp("iter4")
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


def test_reload_restarts_only_top_level_functions(live):
    """N1: parts restarted by us get a second start source and keep running when the look is stopped by name."""
    ai, fake, run = live["ai"], live["fake"], live["run"]
    cmd, plan = ai.plan("slow blue wash breathing at 60 bpm")
    assert run(ai.executor.execute(plan, confirm=True, allow_running_reload=True))["ok"]
    main = plan.look["main_id"]
    parts = ai.rig.functions[main].refs
    for fid in [main] + list(parts):
        fake.functions[fid]["running"] = True
    cmd, plan = ai.plan("rename fixture 22 to Bar Wall")
    res = run(ai.executor.execute(plan, confirm=True, allow_running_reload=True))
    reload = next(r for r in res["results"] if r["op"] == "reload")
    assert reload["restarted"] == [main], reload
    for fid in fake.functions:
        fake.functions[fid]["running"] = False


def test_skipped_write_still_reloads_when_qlc_lacks_the_look(live):
    """N2: identical to the file is not the same as loaded in QLC+."""
    ai, fake, run = live["ai"], live["fake"], live["run"]
    cmd, plan = ai.plan("red wash on the spots")
    assert run(ai.executor.execute(plan, confirm=True, allow_running_reload=True))["ok"]
    main = plan.look["main_id"]
    fake.functions.pop(main)  # as if the reload had been blocked
    cmd, plan = ai.plan("red wash on the spots")
    res = run(ai.executor.execute(plan, confirm=True, allow_running_reload=True))
    ops = {r["op"]: r for r in res["results"]}
    assert ops["write_look"].get("skipped") and not ops["reload"].get("skipped") and ops["reload"].get("loaded"), res
    assert main in fake.functions


def test_release_everything_skips_untouched_universes(live):
    """N10: a whole-universe reset costs ~160 ms in QLC+; with nothing overridden none is sent."""
    ai, fake, run = live["ai"], live["fake"], live["run"]
    fake.log.clear()
    cmd, plan = ai.plan("release everything")
    res = run(ai.executor.execute(plan, confirm=True))
    assert res["ok"] and not any("sdResetUniverse" in m for m in fake.log), fake.log[-5:]


def test_second_calibration_replaces_the_first(live):
    """API D2: the first calibration's channels are released when a second one starts."""
    ai, fake, run = live["ai"], live["fake"], live["run"]
    for text in ("find blue on fixture 34", "find red on fixture 35"):
        cmd, plan = ai.plan(text)
        assert run(ai.executor.execute(plan, confirm=True))["ok"]
    run(ai.executor.calibrate_end("test"))
    assert not fake.override, f"{len(fake.override)} channels left overridden"


def test_failed_release_is_retried_after_reconnect(live):
    """API D3: overrides that could not be released while QLC+ was gone are released once it is back."""
    ai, fake, run = live["ai"], live["fake"], live["run"]
    from lightai.exec.wsclient import QlcError

    c = run(ai.executor.ensure_client())
    run(c.set_channel(1, 100, 200))

    class Broken:
        async def reset_channel(self, u, ch):
            raise QlcError("gone")

    run(ai.executor._release(Broken(), {(1, 100)}))
    assert (1, 100) in ai.executor.pending_release
    run(ai.executor._flush_pending_release())
    assert not ai.executor.pending_release and 100 not in fake.override


def test_release_word_alone(live):
    cmd, plan = live["ai"].plan("release")
    assert cmd.intent == "release" and plan.mode != "clarify"


def test_feedback_is_applied_to_the_proposal_only_when_it_runs(live):
    """API D11: planning 'too fast' must not already change the proposal."""
    ai, run = live["ai"], live["run"]
    cmd, prop = ai.plan("make an assumption on fixture 5 and make it move")
    before = dict(prop.followup["apply"])
    cmd, fbplan = ai.plan("that was too fast")
    assert prop.followup["apply"] == before, "planning feedback changed the proposal"
    res = run(ai.executor.execute(fbplan))
    assert res["ok"]


@pytest.mark.parametrize("text,intent", [("lights on", "blackout_release"), ("release", "release")])
def test_short_phrases(live, text, intent):
    cmd, plan = live["ai"].plan(text)
    assert cmd.intent == intent and plan.mode != "clarify"


@pytest.mark.parametrize("text,intent,target", [
    ("stop all the chatter", "stop_all", None),
    ("black out the windows", "blackout", None),
    ("stop everything on the washes", "stop_all", "washes"),
])
def test_off_topic_or_partial_high_impact_asks(text, intent, target):
    slots = {"target": [SlotValue(raw=target, value={"fixture_ids": [8, 9], "via": "zone washes"})]} if target else {}
    assert apply_policy(_cmd(text, intent, 0.99, slots)).clarify
