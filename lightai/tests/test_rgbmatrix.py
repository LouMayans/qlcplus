"""Tests for QLC+ RGB Matrix (pixel effect) support: lightai.compiler.recipes_pixel plus the
spec/xmlgen/validate additions it relies on. Uses the real rig read-only (nothing is written to
disk) except where a test explicitly builds its own tmp_path project.
"""

from __future__ import annotations

import asyncio
import copy
import os
from pathlib import Path

import pytest

from lightai.compiler import compile_look, write_look
from lightai.compiler.recipes import RECIPES, unit_ms
from lightai.compiler.recipes_pixel import RECIPES_PIXEL, WORDS_PIXEL, pack_color, rgb_heads
from lightai.compiler.spec import FunctionSpec, GroupHeadSpec, LookParams
from lightai.compiler.validate import validate_specs, validate_workspace
from lightai.compiler.xmlgen import to_element
from lightai.config import load_config
from lightai.rig.model import Rig
from lightai.rig.qxf import kid, kids, text_of
from lightai.rig.qxw import Workspace


@pytest.fixture(scope="module")
def rig():
    return Rig.load(load_config())


def _by_type(look, type_):
    return [f for f in look.groups + look.functions if f.type == type_]


# ---------------------------------------------------------------- catalog

def test_both_registered_and_describe():
    for cls in RECIPES_PIXEL:
        assert RECIPES[cls.key] is cls
        info = cls.describe()
        assert info["recipe"] == cls.key
        assert info["what"]
        assert isinstance(info["options"], list) and info["options"]


def test_words_pixel_route_to_a_known_recipe():
    assert {"pixel_chase", "pixel_wave"} <= set(RECIPES)
    for word, key in WORDS_PIXEL.items():
        assert key in RECIPES, f"{word!r} routes to unknown recipe {key!r}"


# ---------------------------------------------------------------- fixture-level helpers

def test_rgb_heads_tetra_bar_is_one_implicit_head(rig):
    fx = rig.fixtures[rig.zones["tetras"][0]]  # patched 6-CH: R,G,B,Amber,Dimmer,Strobe, no <Head> blocks
    assert rgb_heads(fx) == [0]


def test_rgb_heads_generic_rgb_wall_is_one_implicit_head(rig):
    fx = rig.fixtures[rig.zones["led_walls"][0]]  # Generic/Generic RGB, mode "RGB": R,G,B, no <Head> blocks
    assert rgb_heads(fx) == [0]


def test_rgb_heads_empty_for_color_wheel_fixture(rig):
    fx = rig.fixtures[rig.zones["spots"][0]]
    assert rgb_heads(fx) == []


def test_pack_color_matches_qlc_packed_argb():
    assert pack_color((255, 0, 0)) == 4294901760  # 0xFFFF0000, the real value QLC+ writes for pure red
    assert pack_color((0, 0, 0)) == 0xFF000000
    assert pack_color((300, -10, 128)) == pack_color((255, 0, 128))  # clamped to 0..255


# ---------------------------------------------------------------- compiles on real zones

@pytest.mark.parametrize("recipe", ["pixel_chase", "pixel_wave"])
@pytest.mark.parametrize("zone", ["tetras", "led_walls"])
def test_recipe_compiles_on_real_zones(rig, recipe, zone):
    look = compile_look(rig, LookParams(recipe=recipe, targets=rig.zones[zone], target_label=zone, colors=["red", "blue"]))
    assert look.main_id in look.ids
    assert len(_by_type(look, "RGBMatrix")) == 1
    assert len(_by_type(look, "FixtureGroup")) == 1
    group = _by_type(look, "FixtureGroup")[0]
    matrix = _by_type(look, "RGBMatrix")[0]
    assert matrix.matrix["fixture_group"] == group.id
    assert look.main_id == matrix.id
    assert {h.fixture for h in group.group_heads} == set(rig.zones[zone])


def test_pixel_chase_uses_stripes_and_pixel_wave_uses_waves(rig):
    chase = compile_look(rig, LookParams(recipe="pixel_chase", targets=rig.zones["tetras"], colors=["red"]))
    wave = compile_look(rig, LookParams(recipe="pixel_wave", targets=rig.zones["tetras"], colors=["red"]))
    assert _by_type(chase, "RGBMatrix")[0].matrix["algorithm_name"] == "Stripes"
    assert _by_type(wave, "RGBMatrix")[0].matrix["algorithm_name"] == "Waves"


def test_group_heads_follow_spatial_order(rig):
    ids = rig.zones["led_walls"]
    expected = rig.stage.order(ids, "left_to_right")
    assert expected != list(ids), "the test is only meaningful if the spatial order differs from zone order"
    look = compile_look(rig, LookParams(recipe="pixel_wave", targets=ids, target_label="led_walls", colors=["white"], order="left_to_right"))
    group = _by_type(look, "FixtureGroup")[0]
    ordered_heads = sorted(group.group_heads, key=lambda h: h.x)
    assert [h.fixture for h in ordered_heads] == expected
    assert [h.x for h in ordered_heads] == list(range(len(expected)))
    assert group.group_size == (len(expected), 1)


def test_matrix_xml_has_colors_speed_algorithm_and_group_ref(rig):
    p = LookParams(recipe="pixel_chase", targets=rig.zones["tetras"], target_label="tetras", colors=["red", "blue"], rate_word="fast")
    look = compile_look(rig, p)
    matrix = _by_type(look, "RGBMatrix")[0]
    group = _by_type(look, "FixtureGroup")[0]
    el = to_element(matrix)
    algo = kid(el, "Algorithm")
    assert algo.get("Type") == "Script"
    assert algo.text == "Stripes"
    colors = kids(el, "Color")
    assert [c.get("Index") for c in colors] == ["0", "1"]
    assert int(colors[0].text) == pack_color(rig.colors.rgb(rig.colors.canonical("red"))[0])
    assert int(colors[1].text) == pack_color(rig.colors.rgb(rig.colors.canonical("blue"))[0])
    assert text_of(el, "ControlMode") == "RGB"
    assert text_of(el, "FixtureGroup") == str(group.id)
    speed = kid(el, "Speed")
    assert int(speed.get("Duration")) == unit_ms(p, "run")


def test_skips_fixtures_with_no_rgb_head(rig):
    spot_id = rig.zones["spots"][0]
    targets = list(rig.zones["led_walls"]) + [spot_id]
    look = compile_look(rig, LookParams(recipe="pixel_chase", targets=targets, colors=["white"]))
    assert any(s["fixture"] == spot_id and "RGB head" in s["reason"] for s in look.skipped)
    group = _by_type(look, "FixtureGroup")[0]
    assert len(group.group_heads) == len(rig.zones["led_walls"])


def test_raises_when_no_target_has_an_rgb_head(rig):
    with pytest.raises(ValueError):
        compile_look(rig, LookParams(recipe="pixel_chase", targets=rig.zones["spots"], colors=["white"]))
    with pytest.raises(ValueError):
        compile_look(rig, LookParams(recipe="pixel_wave", targets=rig.zones["spots"], colors=["white"]))


# ---------------------------------------------------------------- FixtureGroup ID namespace + reuse

def test_group_id_is_its_own_namespace_from_function_ids(rig):
    """A FixtureGroup ID may equal an existing *function* ID: QLC+ (Doc) keeps separate counters."""
    existing_group_ids = {g.id for g in rig.ws.fixture_groups()}
    existing_function_id = next(fid for fid in rig.functions if fid not in existing_group_ids)
    group = FunctionSpec(id=existing_function_id, type="FixtureGroup", name="g", group_size=(1, 1),
                          group_heads=[GroupHeadSpec(x=0, y=0, fixture=rig.zones["tetras"][0])])
    errors, _ = validate_specs(rig, [group], set(rig.functions))
    assert errors == []


def test_group_reused_across_separate_compiles(cfg):
    rig1 = Rig.load(cfg)
    look1 = compile_look(rig1, LookParams(recipe="pixel_chase", targets=rig1.zones["tetras"], target_label="tetras", colors=["red"]))
    group1 = _by_type(look1, "FixtureGroup")[0]
    write_look(rig1, look1, cfg.project_path, text="pixel_chase")

    rig2 = Rig.load(cfg)
    look2 = compile_look(rig2, LookParams(recipe="pixel_wave", targets=rig2.zones["tetras"], target_label="tetras", colors=["white"]))
    assert _by_type(look2, "FixtureGroup") == []  # reused: no second group emitted
    assert look2.functions[0].matrix["fixture_group"] == group1.id
    assert any("reusing fixture group" in a["fact"] for a in look2.assumptions)
    write_look(rig2, look2, cfg.project_path, text="pixel_wave")

    ws = Workspace.load(cfg.project_path)
    # Main Project.qxw already ships 2 fixture groups (SPOT Group, VIP SPOT 1); only 1 more should exist.
    assert len(ws.fixture_groups()) == 3


# ---------------------------------------------------------------- validation

def test_validate_rejects_missing_group_reference(rig):
    bad = FunctionSpec(id=900001, type="RGBMatrix", name="bad", matrix={
        "algorithm_type": "Script", "algorithm_name": "Stripes", "colors": [4294901760],
        "control_mode": "RGB", "fixture_group": 999999,
    })
    errors, _ = validate_specs(rig, [bad], set(rig.functions))
    assert any("missing fixture group" in e for e in errors)


def test_validate_rejects_unknown_algorithm(rig):
    bad = FunctionSpec(id=900002, type="RGBMatrix", name="bad", matrix={
        "algorithm_type": "Script", "algorithm_name": "Not A Real Script", "colors": [1],
        "control_mode": "RGB", "fixture_group": 0,
    })
    errors, _ = validate_specs(rig, [bad], set(rig.functions))
    assert any("unknown RGB script" in e for e in errors)


def test_validate_rejects_no_colors_and_bad_control_mode(rig):
    no_colors = FunctionSpec(id=900003, type="RGBMatrix", name="bad", matrix={
        "algorithm_type": "Script", "algorithm_name": "Stripes", "colors": [], "fixture_group": 0,
    })
    errors, _ = validate_specs(rig, [no_colors], set(rig.functions))
    assert any("sets no colors" in e for e in errors)

    bad_mode = FunctionSpec(id=900004, type="RGBMatrix", name="bad", matrix={
        "algorithm_type": "Script", "algorithm_name": "Stripes", "colors": [1], "control_mode": "Rainbow", "fixture_group": 0,
    })
    errors, _ = validate_specs(rig, [bad_mode], set(rig.functions))
    assert any("unknown control mode" in e for e in errors)


def test_validate_accepts_matrix_referencing_a_new_group_in_the_same_batch(rig):
    group = FunctionSpec(id=900005, type="FixtureGroup", name="g", group_size=(1, 1),
                          group_heads=[GroupHeadSpec(x=0, y=0, fixture=rig.zones["tetras"][0])])
    matrix = FunctionSpec(id=900006, type="RGBMatrix", name="m", matrix={
        "algorithm_type": "Script", "algorithm_name": "Stripes", "colors": [1], "control_mode": "RGB", "fixture_group": 900005,
    })
    errors, _ = validate_specs(rig, [group, matrix], set(rig.functions))
    assert errors == []


def test_validate_rejects_group_with_no_heads(rig):
    bad = FunctionSpec(id=900007, type="FixtureGroup", name="g", group_size=(1, 1), group_heads=[])
    errors, _ = validate_specs(rig, [bad], set(rig.functions))
    assert any("has no heads" in e for e in errors)


def test_validate_rejects_group_head_outside_its_grid(rig):
    bad = FunctionSpec(id=900008, type="FixtureGroup", name="g", group_size=(1, 1),
                        group_heads=[GroupHeadSpec(x=5, y=0, fixture=rig.zones["tetras"][0])])
    errors, _ = validate_specs(rig, [bad], set(rig.functions))
    assert any("outside its" in e for e in errors)


def test_validate_rejects_group_with_unknown_fixture(rig):
    bad = FunctionSpec(id=900009, type="FixtureGroup", name="g", group_size=(1, 1),
                        group_heads=[GroupHeadSpec(x=0, y=0, fixture=9999999)])
    errors, _ = validate_specs(rig, [bad], set(rig.functions))
    assert any("unknown fixture" in e for e in errors)


# ---------------------------------------------------------------- round trip + whole-workspace checks

def test_round_trip_and_workspace_consistency_checks(tmp_path):
    from lightai.devtools import make_test_project

    cfg = copy.copy(load_config())
    cfg.data_dir = tmp_path / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp_path / "learned.yaml"
    cfg.project_path = make_test_project(tmp_path / "proj")

    rig = Rig.load(cfg)
    look = compile_look(rig, LookParams(recipe="pixel_chase", targets=rig.zones["tetras"], target_label="tetras", colors=["red", "blue"]))
    write_look(rig, look, cfg.project_path, text="pixel_chase")

    ws = Workspace.load(cfg.project_path)
    errors, _ = validate_workspace(ws)
    assert errors == []

    groups = ws.fixture_groups()
    assert len(groups) == 1
    assert {h["fixture"] for h in groups[0].heads} == set(rig.zones["tetras"])

    rgbm = [f for f in ws.functions() if f.type == "RGBMatrix"]
    assert len(rgbm) == 1
    assert rgbm[0].extra["fixture_group"] == groups[0].id
    assert rgbm[0].extra["algorithm"] == "Stripes"

    # corrupt the matrix's group reference and confirm the whole-workspace check catches it
    matrix_el = ws.function_el(rgbm[0].id)
    kid(matrix_el, "FixtureGroup").text = "123456"
    errors, _ = validate_workspace(ws)
    assert any("references missing fixture group" in e for e in errors)
    kid(matrix_el, "FixtureGroup").text = str(groups[0].id)  # restore

    # corrupt a head's fixture reference and confirm that is caught too
    group_el = None
    for g in kids(ws.engine, "FixtureGroup"):
        if int(g.get("ID")) == groups[0].id:
            group_el = g
    head_el = kids(group_el, "Head")[0]
    head_el.set("Fixture", "7654321")
    errors, _ = validate_workspace(ws)
    assert any("references missing fixture 7654321" in e for e in errors)


# ---------------------------------------------------------------- opt-in live E2E

pytestmark_e2e = pytest.mark.skipif(os.environ.get("LIGHTAI_E2E") != "1", reason="set LIGHTAI_E2E=1 to run against an isolated QLC+")


@pytestmark_e2e
@pytest.mark.parametrize("project", ["fresh", "main_show_copy"])
def test_pixel_chase_runs_and_animates_live(tmp_path, project):
    from lightai.devtools import QlcInstance, make_test_project, sanitize_project
    from lightai.exec.wsclient import QlcClient

    cfg = copy.copy(load_config())
    main = cfg.main_show()
    cfg.data_dir = tmp_path / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp_path / "learned.yaml"
    if project == "fresh":
        cfg.project_path = make_test_project(tmp_path / "proj")
    else:  # hundreds of functions already: the new group must still load before its matrix (it froze when written after)
        cfg.project_path = sanitize_project(main, tmp_path / "proj" / main.name)

    rig = Rig.load(cfg)
    fx = rig.fixtures[rig.zones["tetras"][0]]
    look = compile_look(rig, LookParams(
        recipe="pixel_chase", targets=rig.zones["tetras"], target_label="tetras",
        colors=["red", "blue"], rate_word="fast",
    ))
    write_look(rig, look, cfg.project_path, text="pixel_chase")

    async def run():
        with QlcInstance(cfg.project_path, port=9993, exe=Path(r"C:\qlcplus-dev\qlcplus.exe")):
            client = QlcClient(url="auto", host="127.0.0.1", port=9993)
            async with client:
                await client.set_function(look.main_id, True)
                await asyncio.sleep(0.3)
                assert await client.function_status(look.main_id) == "Running"

                samples = []
                for _ in range(6):
                    vals = await client.channel_values(fx.universe + 1, fx.address + 1, fx.channels)
                    samples.append(tuple(v["value"] for v in vals))
                    await asyncio.sleep(0.2)

                await client.set_function(look.main_id, False)
                await client.close()

        assert len(set(samples)) > 1, f"pixel channels on {fx.name} never changed over ~1.2s: {samples}"

    asyncio.run(run())


# ---------------------------------------------------------------- groups: own list, written first, never removed by ID

def test_group_is_never_one_of_the_looks_function_ids(cfg):
    """A new group's ID can also be an unrelated function's ID in the show (QLC+ numbers them apart): deleting or
    replacing the look by its ids must never touch that function."""
    rig = Rig.load(cfg)
    look = compile_look(rig, LookParams(recipe="pixel_chase", targets=rig.zones["tetras"], target_label="tetras", colors=["red"]))
    group = look.groups[0]
    assert group.type == "FixtureGroup" and all(f.type != "FixtureGroup" for f in look.functions)
    assert look.ids == [f.id for f in look.functions]
    assert group.id not in look.ids or group.id not in rig.functions


def test_new_group_is_written_before_the_functions(cfg):
    """QLC+ loads the file in order and a matrix loaded before its group freezes; the main show has functions."""
    rig = Rig.load(cfg)
    assert rig.functions
    look = compile_look(rig, LookParams(recipe="pixel_wave", targets=rig.zones["led_walls"], target_label="led_walls", colors=["blue"]))
    write_look(rig, look, cfg.project_path, text="pixel_wave")
    ws = Workspace.load(cfg.project_path)
    tags = [c.tag.split("}")[-1] for c in ws.engine if isinstance(c.tag, str)]
    groups = [i for i, t in enumerate(tags) if t == "FixtureGroup"]
    assert len(groups) == 3 and max(groups) < tags.index("Function")
    assert max(i for i, t in enumerate(tags) if t == "Fixture") < min(groups)


def test_design_layers_share_or_split_groups(rig):
    """Pixel layers of one design: the same fixtures share one new group, other fixtures get another ID."""
    from lightai.compiler.recipes import build_look

    def params(zone):
        return LookParams(recipe="pixel_chase", targets=rig.zones[zone], target_label=zone, colors=["red"])

    pending: list = []
    a = build_look(rig, params("tetras"), groups=pending)
    b = build_look(rig, params("tetras"), start_id=max(a.ids) + 1, groups=pending)
    c = build_look(rig, params("led_walls"), start_id=max(b.ids) + 1, groups=pending)
    assert len(a.groups) == 1 and b.groups == [] and len(c.groups) == 1
    assert b.functions[0].matrix["fixture_group"] == a.groups[0].id
    assert c.groups[0].id != a.groups[0].id and len(pending) == 2
