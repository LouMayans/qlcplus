"""Spatial words and moods in ordinary commands, offline: orders, aims, crossing and fanning beams, stage groups,
mood words filling what wasn't said. Planned against the main show and its 3D stage (read-only)."""

import pytest

from lightai.config import load_config

MODEL = load_config().current_model_dir()
pytestmark = pytest.mark.skipif(MODEL is None, reason="no promoted model")


@pytest.fixture(scope="module")
def ai():
    from lightai.app import LightAI

    a = LightAI(load_config(), model_dir=MODEL, use_embeddings=False)
    if a.rig.stage is None:
        pytest.skip("the main show has no 3D stage file")
    return a


@pytest.mark.parametrize("text,spatial,recipe", [
    ("aim the spots at the dj", {"aim": "dj"}, "position"),
    ("point the beams at the dance floor", {"aim": "dance floor"}, "position"),
    ("cross the beams over the dance floor", {"aim": "dance floor", "spread": "cross"}, "position"),
    ("spots straight down", {"aim": "down"}, "position"),
    ("spots circle left to right", {"order": "left_to_right"}, "circle"),
    ("washes pink center out", {"order": "center_out"}, None),
])
def test_spatial_words(ai, text, spatial, recipe):
    cmd, plan = ai.plan(text)
    assert cmd.spatial == spatial and plan.mode == "structural", (cmd.spatial, plan.summary)
    params = plan.look["params"]
    assert all(params[k] == v for k, v in spatial.items())
    if recipe:
        assert params["recipe"] == recipe


def test_place_words_are_not_fixtures(ai):
    cmd, _ = ai.plan("washes pink center out")
    assert set(cmd.first("target").value["fixture_ids"]) == set(ai.rig.zones["washes"])
    cmd, _ = ai.plan("cross the beams over the dance floor")
    assert set(cmd.first("target").value["fixture_ids"]) == set(ai.rig.zones["spots"])


def test_a_group_over_a_place_is_one_group(ai):
    cmd, plan = ai.plan("the beams over the dance floor at 50%")
    got = set(cmd.first("target").value["fixture_ids"])
    over = set(ai.rig.stage_groups()["over the dance floor"])
    assert cmd.intent == "set_level" and got == set(ai.rig.zones["spots"]) & over


def test_a_mood_fills_what_was_not_said(ai):
    cmd, plan = ai.plan("dreamy blue wash on the washes")
    p = plan.look["params"]
    assert p["colors"] == ["blue"] and p["fade_ms"] and p["intensity"] < 1.0
    assert any("mood 'dreamy'" in a["fact"] for a in plan.assumptions)


def test_a_mood_on_fixtures_proposes_a_look(ai):
    cmd, plan = ai.plan("something hypnotic on the spots")
    assert cmd.intent == "propose_look", (cmd.intent, plan.summary)


@pytest.mark.parametrize("text,intent", [("washes at 50%", "set_level"), ("fog on", "set_level"), ("blackout", "blackout")])
def test_plain_commands_unchanged(ai, text, intent):
    cmd, _ = ai.plan(text)
    assert cmd.intent == intent and not cmd.spatial


def test_filler_movement_and_a_size_inside_a_speed():
    """v8 tagged 'do' as a movement and 'big fast' as one speed: both are tidied without the model."""
    from lightai.nlu.pipeline import Parser
    from lightai.schema import LightCommand, SlotValue

    p = Parser.__new__(Parser)
    cmd = LightCommand(text="make the spots do red circles", intent="create_look", confidence=0.9,
                       slots={"movement": [SlotValue(raw="do"), SlotValue(raw="circles", value={"movement": "circle"})]})
    p.tidy_look_slots(cmd)
    assert [sv.raw for sv in cmd.slots["movement"]] == ["circles"]
    cmd = LightCommand(text="make the spots do red", intent="create_look", confidence=0.9, slots={"movement": [SlotValue(raw="do")]})
    p.tidy_look_slots(cmd)
    assert "movement" not in cmd.slots
    cmd = LightCommand(text="big fast white sweep on the beams", intent="create_look", confidence=0.9,
                       slots={"rate": [SlotValue(raw="big fast")]})
    p.tidy_look_slots(cmd)
    assert cmd.slots["size"][0].raw == "big" and cmd.slots["size"][0].value
    assert cmd.slots["rate"][0].raw == "fast" and cmd.slots["rate"][0].value.get("word")


@pytest.mark.parametrize("text,agree", [
    ("make the spots do red circles at 128", True), ("do a slow blue sweep on the beams", True),
    ("give me a rainbow chase on the washes", True), ("make the chase faster", False), ("put the wash look on", False),
    ("make the washes blue", False), ("stop the chase", False),
])
def test_clear_look_wording(text, agree):
    import re

    from lightai.nlu.policy import AGREE

    assert bool(re.search(AGREE["create_look"], text, re.I)) is agree
