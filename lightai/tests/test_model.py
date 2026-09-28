"""Model-level tests. They use the promoted model and are skipped until one exists."""

import pytest

from lightai.config import load_config

MODEL = load_config().current_model_dir()
pytestmark = pytest.mark.skipif(MODEL is None, reason="no promoted model yet (run lightai train --promote-if-better)")


def test_golden_and_abstain_suites():
    from lightai.train.evaluate import evaluate_model_dir

    rep = evaluate_model_dir(MODEL)
    assert rep["golden_pass"] == rep["golden_total"], rep["failures"]
    assert rep["abstain_pass"] == rep["abstain_total"], rep["failures"]
    assert rep["parse_p95_ms"] < 30


def test_flagship_sentences(cfg):
    from lightai.app import LightAI

    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    cmd, plan = ai.plan("slow blue wash breathing at 60 BPM")
    assert plan.intent == "create_look" and plan.look["recipe"] == "breathe"
    assert sorted(plan.look["params"]["targets"]) == [8, 9, 10, 11]
    cmd, plan = ai.plan("Hey fixture 3 needs to be rotated 90 degrees")
    assert plan.intent == "fixture_edit.rotate"
    assert plan.actions[0].args["rotation"] == 180
    cmd, plan = ai.plan("make an assumption on fixture 5 and make it move")
    assert plan.intent == "propose_look" and plan.actions[0].op == "preview"
    assert any("fixture 5" in a["fact"] for a in plan.assumptions)
