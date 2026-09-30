"""Tests for the extra recipes in lightai.compiler.recipes_fx: dimmer_wave, color_morph,
strobe_chase, strobe_burst. Uses the real rig read-only (nothing is written to disk)."""

from __future__ import annotations

import pytest

from lightai.compiler import compile_look
from lightai.compiler.recipes import RECIPES, unit_ms
from lightai.compiler.recipes_fx import RECIPES_FX, WORDS_FX
from lightai.compiler.spec import LookParams
from lightai.config import load_config
from lightai.rig.model import Rig


@pytest.fixture(scope="module")
def rig():
    return Rig.load(load_config())


def _by_type(look, type_):
    return [f for f in look.functions if f.type == type_]


def _assert_steps_reference_scenes(look):
    scene_ids = {f.id for f in _by_type(look, "Scene")}
    for chaser in _by_type(look, "Chaser"):
        for step in chaser.steps:
            assert step.function_id in scene_ids


# ---------------------------------------------------------------- catalog

def test_all_four_registered_and_describe():
    for cls in RECIPES_FX:
        assert RECIPES[cls.key] is cls
        info = cls.describe()
        assert info["recipe"] == cls.key
        assert info["what"]
        assert isinstance(info["options"], list) and info["options"]


def test_words_fx_route_to_a_known_recipe():
    assert {"dimmer_wave", "color_morph", "strobe_chase", "strobe_burst"} <= set(RECIPES)
    for word, key in WORDS_FX.items():
        assert key in RECIPES, f"{word!r} routes to unknown recipe {key!r}"


# ---------------------------------------------------------------- compiles on real zones

@pytest.mark.parametrize("recipe,zone,colors,extra", [
    ("dimmer_wave", "spots", ["blue"], {}),
    ("dimmer_wave", "washes", ["white"], {"mirror": True}),
    ("color_morph", "washes", ["red", "blue", "green"], {"order": "left_to_right"}),
    ("color_morph", "spots", [], {}),
    ("strobe_chase", "spots", ["white"], {}),
    ("strobe_chase", "pars", ["white"], {}),
    ("strobe_burst", "washes", ["red", "blue"], {}),
    ("strobe_burst", "pars", [], {}),
])
def test_recipe_compiles_on_real_zones(rig, recipe, zone, colors, extra):
    look = compile_look(rig, LookParams(recipe=recipe, targets=rig.zones[zone], target_label=zone, colors=colors, **extra))
    assert look.main_id in look.ids
    _assert_steps_reference_scenes(look)


# ---------------------------------------------------------------- dimmer_wave

def test_dimmer_wave_function_counts(rig):
    n = len(rig.zones["washes"])
    look = compile_look(rig, LookParams(recipe="dimmer_wave", targets=rig.zones["washes"], colors=["white"]))
    steps_n = max(n, 4)
    assert len(_by_type(look, "Scene")) == steps_n + 1  # N steps + one base "Color" scene (washes all have a dimmer)
    assert len(_by_type(look, "Chaser")) == 1
    assert len(_by_type(look, "Collection")) == 1


def test_dimmer_wave_bump_travels(rig):
    ids = list(rig.zones["washes"])
    ordered_ids = rig.ordered(ids)
    look = compile_look(rig, LookParams(recipe="dimmer_wave", targets=ids, colors=["white"]))
    chaser = _by_type(look, "Chaser")[0]
    scenes_by_id = {f.id: f for f in _by_type(look, "Scene")}
    step_scenes = [scenes_by_id[s.function_id] for s in chaser.steps]
    assert len(step_scenes) == len(ordered_ids)
    for k, scene in enumerate(step_scenes):
        levels = [dict(scene.values[fid])[rig.fixtures[fid].roles["dimmer"]] for fid in ordered_ids]
        brightest = levels.index(max(levels))
        assert brightest == k, f"step {k}: brightest fixture index is {brightest}"


def test_dimmer_wave_no_dimmer_uses_color_level(rig):
    # led_walls fixtures are RGB-only with no dimmer channel: the wave must ride the color level instead,
    # and there is nothing to preset, so the Collection has no base scene.
    look = compile_look(rig, LookParams(recipe="dimmer_wave", targets=rig.zones["led_walls"], colors=["white"]))
    assert len(_by_type(look, "Collection")) == 1
    main = next(f for f in look.functions if f.id == look.main_id)
    assert len(main.members) == 1
    assert len(_by_type(look, "Scene")) == 4  # max(3 fixtures, 4) steps, no base


# ---------------------------------------------------------------- color_morph

def test_color_morph_defaults_to_pastel_and_counts(rig):
    look = compile_look(rig, LookParams(recipe="color_morph", targets=rig.zones["washes"], colors=[]))
    assert any("pastel" in a["fact"] for a in look.assumptions)
    assert len(_by_type(look, "Scene")) == 4
    assert len(_by_type(look, "Chaser")) == 1


def test_color_morph_gradient_shifts_across_fixtures(rig):
    ids = list(rig.zones["washes"])  # rgb fixtures, no color wheel
    colors = ["red", "blue", "green"]
    look = compile_look(rig, LookParams(recipe="color_morph", targets=ids, colors=colors, order="left_to_right"))
    chaser = _by_type(look, "Chaser")[0]
    scenes_by_id = {f.id: f for f in _by_type(look, "Scene")}
    step_scenes = [scenes_by_id[s.function_id] for s in chaser.steps]
    assert len(step_scenes) == len(colors)

    m = len(colors)
    frac = rig.stage.position_fraction(ids, "left_to_right")
    offsets = {fid: round(frac[fid] * (m - 1)) for fid in ids}

    for k, scene in enumerate(step_scenes):
        for fid in ids:
            fx = rig.fixtures[fid]
            expected_color = colors[(k + offsets[fid]) % m]
            expected = rig.emitter_values(fx, expected_color, 1.0)
            actual = dict(scene.values[fid])
            for role, val in expected.items():
                assert actual[fx.roles[role]] == val, f"step {k} fixture {fid} role {role}"
    # not every fixture shows the same color at the same step: the gradient really moves
    first_step_colors = {dict(step_scenes[0].values[fid]).get(rig.fixtures[fid].roles.get("red")) for fid in ids}
    assert len(first_step_colors) > 1


def test_color_morph_keeps_wheel_fixtures_fixed(rig):
    ids = list(rig.zones["spots"])  # every fixture here is color-wheel
    look = compile_look(rig, LookParams(recipe="color_morph", targets=ids, colors=["red", "blue", "green"], order="left_to_right"))
    assert any("wheel" in a["fact"] and "fixed" in a["fact"] for a in look.assumptions)
    scenes_by_id = {f.id: f for f in _by_type(look, "Scene")}
    chaser = _by_type(look, "Chaser")[0]
    step_scenes = [scenes_by_id[s.function_id] for s in chaser.steps]

    fx = rig.fixtures[ids[0]]
    ch = fx.roles["color_wheel"]
    values = {dict(scene.values[fx.id])[ch] for scene in step_scenes}
    assert len(values) == 1
    assert values.pop() == rig.wheel_value(fx, "red")["value"]


# ---------------------------------------------------------------- strobe_chase

def test_strobe_chase_groups_of_two_over_eight(rig):
    zone = "spots"  # 14 fixtures, all strobe-capable -> groups of 2
    n = len(rig.zones[zone])
    look = compile_look(rig, LookParams(recipe="strobe_chase", targets=rig.zones[zone], colors=["white"]))
    assert len(_by_type(look, "Scene")) == -(-n // 2)  # ceil(n / 2)
    assert len(_by_type(look, "Chaser")) == 1
    assert not look.skipped


def test_strobe_chase_single_fixture_per_step_under_eight(rig):
    zone = "washes"  # 4 fixtures, all strobe-capable -> one scene each
    n = len(rig.zones[zone])
    look = compile_look(rig, LookParams(recipe="strobe_chase", targets=rig.zones[zone], colors=["white"], mirror=True))
    assert len(_by_type(look, "Scene")) == n
    assert _by_type(look, "Chaser")[0].run_order == "PingPong"


def test_strobe_chase_skips_non_strobe_fixtures(rig):
    targets = list(rig.zones["led_walls"]) + list(rig.zones["pars"][:1])  # 3 non-strobe + 1 strobe-capable
    look = compile_look(rig, LookParams(recipe="strobe_chase", targets=targets, colors=["white"]))
    assert len(look.skipped) == len(rig.zones["led_walls"])
    assert all("strobe" in s["reason"] for s in look.skipped)
    assert len(_by_type(look, "Scene")) == 1


def test_strobe_chase_raises_when_none_can_strobe(rig):
    with pytest.raises(ValueError):
        compile_look(rig, LookParams(recipe="strobe_chase", targets=rig.zones["led_walls"], colors=["white"]))


# ---------------------------------------------------------------- strobe_burst

def test_strobe_burst_alternates_holds_and_cycles_colors(rig):
    zone = "washes"
    colors = ["red", "blue"]
    p = LookParams(recipe="strobe_burst", targets=rig.zones[zone], colors=colors)
    look = compile_look(rig, p)
    beat = unit_ms(p, "chase")

    chaser = _by_type(look, "Chaser")[0]
    assert chaser.speed_modes == ("PerStep", "PerStep", "PerStep")
    assert chaser.run_order == "Loop"
    assert len(chaser.steps) == 2 * len(colors)

    scenes_by_id = {f.id: f for f in _by_type(look, "Scene")}
    fx = rig.fixtures[rig.zones[zone][0]]
    shutter_ch = fx.roles["shutter"]
    open_val = rig.shutter_open(fx)

    for pair, color in enumerate(colors):
        burst_step, calm_step = chaser.steps[pair * 2], chaser.steps[pair * 2 + 1]
        assert burst_step.fade_in == 0 and burst_step.fade_out == 0  # no fades on bursts
        assert burst_step.hold == beat
        assert calm_step.hold == beat * 3
        burst_scene = scenes_by_id[burst_step.function_id]
        calm_scene = scenes_by_id[calm_step.function_id]
        assert dict(burst_scene.values[fx.id])[shutter_ch] != open_val
        assert dict(calm_scene.values[fx.id])[shutter_ch] == open_val
        expected = rig.emitter_values(fx, color, 1.0)
        for role, val in expected.items():
            ch = fx.roles[role]
            assert dict(burst_scene.values[fx.id])[ch] == val
            assert dict(calm_scene.values[fx.id])[ch] == val


def test_strobe_burst_needs_at_least_one_strobing_fixture(rig):
    with pytest.raises(ValueError):
        compile_look(rig, LookParams(recipe="strobe_burst", targets=rig.zones["led_walls"], colors=["white"]))
