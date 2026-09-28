from lightai.compiler import compile_look
from lightai.compiler.spec import LookParams
from lightai.feedback import FeedbackRouter
from lightai.prefs import Prefs
from lightai.rig.facts import LearnedFacts
from lightai.rig.model import Rig
from lightai.schema import Issue, OutcomeFeedback, Plan


def _plan(rig, recipe="circle", targets=None, colors=("blue",), rate_word="fast"):
    targets = targets or [5]
    look = compile_look(rig, LookParams(recipe=recipe, targets=targets, colors=list(colors), rate_word=rate_word))
    lk = look.summary()
    lk["kind"] = rig.fixtures[targets[0]].kind
    return Plan(plan_id="p-test", intent="propose_look", mode="live", summary="t", look=lk)


def test_color_feedback_learns_wheel_fact(cfg):
    rig = Rig.load(cfg)
    plan = _plan(rig)
    router = FeedbackRouter(rig, Prefs(cfg), cfg)
    res = router.apply(OutcomeFeedback(plan_id="p-test", verdict="wrong", issues=[Issue(kind="color", fixture_id=5, expected="blue", observed="pink")]), plan)
    assert "rig_facts" in res["routed_to"]
    facts = LearnedFacts(cfg.learned_path)
    m = facts.data["models"]["Mayans/BEAM230"]
    assert 88 in m["wheel_not"]["blue"] and m["wheel"]["pink"]["value"] == 88
    rig2 = Rig.load(cfg)
    assert rig2.wheel_value(rig2.fixtures[5], "blue")["value"] != 88
    assert rig2.fixtures[34].caps["wheel"]["blue"] == 24, "other models are untouched"
    assert any("find blue" in f for f in res["followups"])


def test_rgb_channel_swap(cfg):
    rig = Rig.load(cfg)
    plan = _plan(rig, recipe="color_wash", targets=[21], colors=("red",))
    FeedbackRouter(rig, Prefs(cfg), cfg).apply(OutcomeFeedback(plan_id="p", verdict="wrong", issues=[Issue(kind="color", fixture_id=21, expected="red", observed="green")]), plan)
    rig2 = Rig.load(cfg)
    assert rig2.fixtures[21].roles["red"] == 1 and rig2.fixtures[21].roles["green"] == 0


def test_undo_fact(cfg):
    facts = LearnedFacts(cfg.learned_path)
    f = facts.set_wheel("Mayans/BEAM230", "purple", 60)
    facts.save()
    assert Rig.load(cfg).fixtures[1].caps["wheel"]["purple"] == 60
    facts = LearnedFacts(cfg.learned_path)
    facts.undo(f["id"])
    facts.save()
    assert "purple" not in Rig.load(cfg).fixtures[1].caps["wheel"]


def test_speed_feedback_changes_default(cfg):
    rig = Rig.load(cfg)
    prefs = Prefs(cfg)
    router = FeedbackRouter(rig, prefs, cfg)
    plan = _plan(rig, rate_word="fast")
    router.apply(OutcomeFeedback(plan_id="p", verdict="adjust", issues=[Issue(kind="speed", direction="too_fast")]), plan)
    assert prefs.defaults_for("circle")["rate_word"] == "medium"


def test_taste_model_trains(cfg):
    rig = Rig.load(cfg)
    prefs = Prefs(cfg)
    router = FeedbackRouter(rig, prefs, cfg)
    for i in range(6):
        router.apply(OutcomeFeedback(plan_id="p", verdict="ok"), _plan(rig, recipe="breathe", targets=[8], colors=("blue",)))
        router.apply(OutcomeFeedback(plan_id="p", verdict="wrong"), _plan(rig, recipe="strobe", targets=[8], colors=("red",)))
    res = prefs.train()
    assert res["trained"]
    good = prefs.accept_prob(prefs.features("breathe", "wash", "blue", "medium", None, 0))
    bad = prefs.accept_prob(prefs.features("strobe", "wash", "red", "medium", None, 0))
    assert good > bad
    assert prefs.rank_recipes("wash")[0] == "breathe"
