import pytest

from lightai.nlu.normalize import normalize_slot, parse_number
from lightai.nlu.text import parse_markup, to_markup


@pytest.mark.parametrize("text,value", [("60", 60), ("sixty", 60), ("one twenty eight", 128), ("a hundred and twenty", 120), ("2.5", 2.5)])
def test_numbers(text, value):
    assert parse_number(text) == value


@pytest.mark.parametrize("slot,raw,expect", [
    ("target", "fixture 3", {"fixture_ids": [3]}),
    ("target", "the washes", {"fixture_ids": [8, 9, 10, 11]}),
    ("target", "spot #4", {"fixture_ids": [3]}),
    ("target", "wash 1", {"fixture_ids": [8]}),
    ("target", "third spot from the left", {"fixture_ids": [13]}),
    ("target", "fixtures 1 to 4", {"fixture_ids": [1, 2, 3, 4]}),
    ("color", "hot pink", {"name": "pink"}),
    ("rate", "60 bpm", {"bpm": 60.0}),
    ("rate", "every 2 seconds", {"period_ms": 2000}),
    ("rate", "too fast", {"word": "fast"}),
    ("intensity", "40%", {"level": 0.4}),
    ("fade", "snap", {"ms": 0}),
    ("angle", "90 degrees", {"deg": 90, "relative": True}),
    ("distance", "50 cm", {"mm": 500}),
    ("channel", "channel 9", {"index": 8}),
    ("value", "50%", {"value": 128}),  # 50 % of 255 rounds to 128, same as the dimmer path
    ("fixture_model", "v3", {"model": "Mayans/BEAM230 V3"}),
])
def test_normalize(rig, slot, raw, expect):
    got = normalize_slot(rig, slot, raw)
    for k, v in expect.items():
        assert got.get(k) == v, (slot, raw, got)


def test_unresolved_target(rig):
    assert normalize_slot(rig, "target", "fixture x").get("unresolved")


def test_markup_roundtrip():
    text, words, tags, spans = parse_markup("[rate:slow] [color:blue] [target:wash] [movement:breathing] at [rate:60 bpm]")
    assert text == "slow blue wash breathing at 60 bpm"
    assert tags == ["B-rate", "B-color", "B-target", "B-movement", "O", "B-rate", "I-rate"]
    assert to_markup(words, tags) == "[rate:slow] [color:blue] [target:wash] [movement:breathing] at [rate:60 bpm]"
