"""Every fixture the operator has, said the ways an operator says it: the models patched in the shows in SaveFile and
the own fixture definitions (the repo's Fixtures folder, the QLC+ user folder). Planned against the main show; nothing
is written."""

import re

import pytest

from lightai.config import load_config
from lightai.nlu.text import split_words

MODEL = load_config().current_model_dir()
pytestmark = pytest.mark.skipif(MODEL is None, reason="no promoted model")

NAMES = {
    ("Mayans", "BEAM230"): ["beam230", "beam 230", "beam", "mayans beam230"],
    ("Mayans", "BEAM230 V3"): ["beam230 v3", "beam v3"],
    ("Mayans", "BEAM230V2"): ["beam230v2", "beam230 v2", "beam v2"],
    ("Mayans", "WASH"): ["wash", "mayans wash"],
    ("Mayans", "Revolver Wash"): ["revolver", "revolver wash"],
    ("American DJ", "VPar"): ["vpar", "par", "american dj par", "adj vpar"],
    ("Venue", "ThinPAR 38"): ["thinpar", "thin par", "thinpar 38", "venue thinpar"],
    ("Venue", "Tetra Bar"): ["tetra", "tetra bar", "venue tetra"],
    ("Chauvet", "Swarm 5 FX"): ["swarm", "swarm 5", "swarm 5 fx", "chauvet swarm"],
    ("Generic", "Generic RGB"): ["generic rgb", "rgb"],
    ("Betopper", "L1015"): ["l1015", "betopper l1015", "l 1015"],  # own definitions, not patched in any show
    ("Betopper", "LF2405"): ["lf2405", "betopper lf2405", "lf 2405"],
}
ADD = [(model, n, text) for (_, model), names in NAMES.items() for n in names
       for text in (f"add a {n}", f"add 2 {n} fixtures to universe 1", f"add {n} fixture to project")]
# groups by model; words that are also zone aliases (beams, beam230, pars) keep meaning the zone
LEVEL = [("thin pars", "ThinPAR 38"), ("thinpar 38", "ThinPAR 38"), ("venue tetras", "Tetra Bar"), ("chauvet swarms", "Swarm 5 FX"),
         ("swarm 5 fxs", "Swarm 5 FX"), ("rgbs", "Generic RGB"), ("generic rgbs", "Generic RGB"), ("beam230 v3", "BEAM230 V3"),
         ("beam v3", "BEAM230 V3"), ("beam230 v2", "BEAM230V2"), ("mayans washes", "WASH"), ("revolver washes", "Revolver Wash"),
         ("american dj pars", "VPar"), ("adj vpars", "VPar"), ("vpars", "VPar"), ("mayans beam230", "BEAM230")]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


@pytest.fixture(scope="module")
def ai():
    from lightai.app import LightAI

    return LightAI(load_config(), model_dir=MODEL, use_embeddings=False)


def test_model_numbers_stay_one_word():
    assert split_words("add 2 beam230v2 fixtures") == ["add", "2", "beam230v2", "fixtures"]
    assert split_words("3x vpar at 50%") == ["3", "x", "vpar", "at", "50", "%"]


@pytest.mark.parametrize("model,name,text", ADD)
def test_adding_by_name(ai, model, name, text):
    cmd, plan = ai.plan(text)
    assert plan.mode == "structural", (cmd.intent, plan.summary)
    args = plan.actions[0].args
    assert norm((args.get("fixtures") or [args])[0]["model"]) == norm(model), plan.summary


@pytest.mark.parametrize("phrase,model", LEVEL)
def test_levels_by_model_name(ai, phrase, model):
    want = {f.id for f in ai.rig.fixtures.values() if norm(f.model) == norm(model)}
    cmd, plan = ai.plan(f"{phrase} at 50%")
    got = set(cmd.first("target").value.get("fixture_ids") or [])
    assert cmd.intent == "set_level" and got == want, (cmd.intent, sorted(got), sorted(want), plan.summary)


@pytest.mark.parametrize("text,names", [("add a betopper", ["L1015", "LF2405"]), ("add a mayans", ["BEAM230", "WASH"])])
def test_a_maker_alone_asks_which(ai, text, names):
    _, plan = ai.plan(text)
    assert plan.mode == "clarify" and all(n in plan.summary for n in names), plan.summary


def test_real_movements_stay_movements(ai):
    cmd, _ = ai.plan("washes breathe slowly")
    assert cmd.first("movement") is not None and cmd.first("target").raw == "washes"
