"""Labels v4: 'design a show' hands off to Claude's Design tab, a correction the model recognises re-plans the previous
command, and typed commands reach the iteration-10 effects. Routing is tested with a stand-in model answer, so these
tests hold before and after a v4 model is promoted."""

import pytest

from lightai.config import load_config
from lightai.nlu.normalize import norm_movement
from lightai.schema import LightCommand, SlotValue

MODEL = load_config().current_model_dir()
needs_model = pytest.mark.skipif(MODEL is None, reason="no promoted model")


@pytest.fixture(scope="module")
def ai():
    from lightai.app import LightAI

    return LightAI(load_config(), model_dir=MODEL, use_embeddings=False)


def said(ai, text, intent, slots=None, confidence=0.95):
    """What a v4 model would answer for `text`."""
    real = ai.parse

    def parse(t):
        if t != text:
            return real(t)
        cmd = real(t)
        cmd.intent, cmd.confidence, cmd.clarify = intent, confidence, None
        cmd.slots = {k: [SlotValue(raw=v, value={})] for k, v in (slots or {}).items()}
        from lightai.nlu.normalize import normalize_slot

        for k, vals in cmd.slots.items():
            for sv in vals:
                sv.value = normalize_slot(ai.rig, k, sv.raw, intent=intent)
        return cmd

    return parse


@pytest.mark.parametrize("word,recipe", [("pixel wave", "pixel_wave"), ("matrix wave", "pixel_wave"), ("pixel chase", "pixel_chase"),
                                         ("color morph", "color_morph"), ("colour morph", "color_morph"), ("dimmer wave", "dimmer_wave"),
                                         ("strobe chase", "strobe_chase"), ("strobe bursts", "strobe_burst"), ("wave", "circle_wave"),
                                         ("chase", "color_chase"), ("strobe", "strobe")])
def test_typed_effect_words_reach_their_recipe(word, recipe):
    assert norm_movement(word)["recipe"] == recipe


@needs_model
def test_design_show_offers_the_design_tab(ai, monkeypatch):
    text = "create 3 different shows that resemble fast strobing but changing in light a dreamy way"
    monkeypatch.setattr(ai, "parse", said(ai, text, "design_show"))
    cmd, plan = ai.plan(text)
    assert plan.mode == "design" and not plan.actions and plan.intent == "design_show"
    assert plan.followup["design"] == {"text": text, "count": 3, "minutes": None}
    assert "3 shows" in plan.summary and "looping" in plan.summary
    monkeypatch.setattr(ai, "parse", said(ai, "design a 20 minute show", "design_show"))
    _, plan = ai.plan("design a 20 minute show")
    assert plan.followup["design"]["minutes"] == 20.0 and "20 minutes long" in plan.summary


@needs_model
def test_a_recognised_correction_replans_the_previous_command(ai, monkeypatch):
    _, first = ai.plan("washes at 50%")
    monkeypatch.setattr(ai, "parse", said(ai, "i said spots not washes", "correction", {"target": "spots"}))
    cmd, plan = ai.plan("i said spots not washes")
    assert cmd.intent == "set_level" and cmd.corrects == first.plan_id, (cmd.intent, plan.summary)
    assert "spots" in plan.summary and "50%" in plan.summary and plan.followup["corrects"] == first.plan_id


@needs_model
def test_a_bare_recognised_correction_asks_what_was_meant(ai, monkeypatch):
    _, first = ai.plan("washes at 50%")
    monkeypatch.setattr(ai, "parse", said(ai, "you misunderstood me", "correction"))
    cmd, plan = ai.plan("you misunderstood me")
    assert plan.mode == "clarify" and "washes at 50%" in plan.summary and cmd.corrects == first.plan_id


@needs_model
def test_a_correction_with_nothing_before_it_asks_for_the_whole_command(monkeypatch):
    from lightai.app import LightAI

    fresh = LightAI(load_config(), model_dir=MODEL, use_embeddings=False)
    monkeypatch.setattr(fresh, "parse", said(fresh, "i said the spots", "correction", {"target": "spots"}))
    _, plan = fresh.plan("i said the spots")
    assert plan.mode == "clarify" and "nothing to correct" in plan.summary


@needs_model
def test_aiming_words_never_turn_a_show_request_into_a_look(ai):
    if ai.rig.stage is None:
        pytest.skip("the main show has no 3D stage file")
    cmd = LightCommand(text="make a show that aims the spots at the dj", words=[], intent="design_show", confidence=0.95,
                       slots={"target": [SlotValue(raw="spots", value={})]})
    ai.parser.spatial_words(cmd)
    assert cmd.intent == "design_show"


# ---------------------------------------------------------------- v9's tagging slips, guarded for any model

def _cmd(text, intent, **slots):
    from lightai.nlu.normalize import normalize_slot

    rig = _rig()
    cmd = LightCommand(text=text, words=[], intent=intent, confidence=0.99)
    for slot, raws in slots.items():
        cmd.slots[slot] = [SlotValue(raw=r, value=normalize_slot(rig, slot, r, intent=intent)) for r in raws]
    return cmd


def _rig():
    from lightai.rig.model import Rig

    global _RIG
    try:
        return _RIG
    except NameError:
        _RIG = Rig.load(load_config())
        return _RIG


def _parser():
    from lightai.nlu.pipeline import Parser

    p = Parser.__new__(Parser)
    p.rig = _rig()
    return p


def test_a_mood_word_is_no_color_and_fixture_is_no_model():
    p = _parser()
    cmd = _cmd("dreamy blue wash on the washes", "create_look", color=["dreamy", "blue"])
    p.tidy_models_and_moods(cmd)
    assert [sv.raw for sv in cmd.slots["color"]] == ["blue"]
    cmd = _cmd("add par fixture to project", "add_fixture", fixture_model=["par", "fixture"])
    p.tidy_models_and_moods(cmd)
    assert [sv.raw for sv in cmd.slots["fixture_model"]] == ["par"]


@pytest.mark.parametrize("text,patch", [("add fog", False), ("add strobe", False), ("add a strobe", True), ("add swarms", True),
                                        ("add swarm fixture to universe 1", True), ("add 2 pars", True)])
def test_a_patch_needs_a_sign_of_patching(text, patch):
    p = _parser()
    word = text.split()[-1] if not text.endswith("universe 1") else "swarm"
    cmd = _cmd(text, "add_fixture", fixture_model=[word])
    p.tidy_models_and_moods(cmd)
    assert (cmd.intent == "add_fixture") is patch and (cmd.clarify is None) is patch


@pytest.mark.parametrize("text,targets,coords", [
    ("place fixture 7 on coordinate 5 foot by 5 foot", ["fixture 7", "coordinate"], {"x": 60.0, "y": 60.0}),
    ("put fixture 7 at position 5 by 5 feet", ["fixture 7", "5"], {"x": 60.0, "y": 60.0}),
    ("place table 1 at the spot 4 by 6 feet", ["table 1", "spot 4"], {"x": 48.0, "y": 72.0}),
])
def test_coordinates_after_a_lead_in_word(text, targets, coords):
    p = _parser()
    cmd = _cmd(text, "fixture_edit.move", target=targets)
    p.scene_coordinates(cmd)
    assert [sv.raw for sv in cmd.slots["target"]] == targets[:1]
    assert {k: v for k, v in cmd.slots["coordinates"][0].value.items() if k in "xyz"} == coords


def test_a_distance_after_by_is_no_coordinates_and_a_trailing_word_is_trimmed():
    p = _parser()
    cmd = _cmd("move fixture 5 by 2 feet to the left", "fixture_edit.move", target=["fixture 5 by"], distance=["2 feet"])
    p.trim_targets(cmd)
    p.scene_coordinates(cmd)
    assert [sv.raw for sv in cmd.slots["target"]] == ["fixture 5"] and "coordinates" not in cmd.slots
