import pytest

from lightai.compiler import CompileError, compile_look, write_look
from lightai.compiler.preview import simulate
from lightai.compiler.recipes import RECIPES
from lightai.compiler.spec import FunctionSpec, LookParams
from lightai.compiler.validate import validate_specs, validate_workspace
from lightai.rig.model import Rig
from lightai.rig.qxw import Workspace

CASES = [
    ("breathe", "washes", ["blue"], {"bpm": 60, "rate_word": "slow"}),
    ("color_wash", "spots", ["red", "blue"], {"fade_ms": 2000}),
    ("color_chase", "pars", ["pink", "blue", "white"], {"bpm": 128}),
    ("running_light", "spots", ["white"], {"rate_word": "fast", "mirror": True}),
    ("circle", "spots", ["green"], {}),
    ("circle_wave", "spots", ["cyan"], {"size": "small"}),
    ("ballyhoo", "movers", ["white"], {}),
    ("figure8", "spots", ["yellow"], {}),
    ("sweep", "spots", ["white"], {}),
    ("strobe", "all", ["white"], {"rate_word": "fast", "priority": 20}),
    ("blackout_kill", "washes", [], {}),
]


@pytest.mark.parametrize("recipe,zone,colors,extra", CASES)
def test_every_recipe_compiles_and_loads(blank_cfg, recipe, zone, colors, extra):
    rig = Rig.load(blank_cfg)
    look = compile_look(rig, LookParams(recipe=recipe, targets=rig.zones[zone], target_label=zone, colors=colors, **extra))
    assert look.main_id in look.ids
    res = write_look(rig, look, blank_cfg.project_path, text=recipe)
    assert not res["skipped"]
    ws = Workspace.load(blank_cfg.project_path)
    errors, _ = validate_workspace(ws)
    assert errors == []
    names = {f.name for f in ws.functions()}
    assert look.name in names
    for el in ws.function_els():
        assert el.get("Hidden") is None, "generated functions must never be Hidden (QLC+ zeroes hidden scenes)"


def test_no_scene_writes_v3_focus_channel(rig):
    look = compile_look(rig, LookParams(recipe="color_wash", targets=rig.zones["spots"], colors=["blue"]))
    for spec in look.functions:
        for fid in (34, 35):
            chans = [ch for ch, _ in spec.values.get(fid, [])]
            assert 5 not in chans
            if chans:
                assert 8 in chans and dict(spec.values[fid])[8] == 24


def test_validator_rejects_wrong_role(rig):
    bad = FunctionSpec(id=9999, type="Scene", name="bad", values={34: [(5, 24)]}, roles={34: {5: "color_wheel"}})
    errors, _ = validate_specs(rig, [bad], set(rig.functions))
    assert any("color_wheel" in e for e in errors)


def test_efx_phase_spread_rule(rig):
    look = compile_look(rig, LookParams(recipe="circle_wave", targets=rig.zones["spots"], colors=["blue"]))
    efx = next(f for f in look.functions if f.type == "EFX")
    offs = [e.start_offset for e in efx.efx_fixtures]
    assert max(offs) - min(offs) <= 180


def test_strobe_stays_in_each_models_range(rig):
    look = compile_look(rig, LookParams(recipe="strobe", targets=rig.zones["spots"], colors=["white"], rate_word="fast"))
    scene = look.functions[0]
    for fid, pairs in scene.values.items():
        fx = rig.fixtures[fid]
        v = dict(pairs)[fx.roles["shutter"]]
        lo, hi = sorted(fx.caps["shutter"]["strobe"])
        assert lo <= v <= hi


def test_idempotent_write(cfg):
    rig = Rig.load(cfg)
    p = LookParams(recipe="breathe", targets=rig.zones["washes"], colors=["blue"], bpm=60)
    assert not write_look(rig, compile_look(rig, p), cfg.project_path)["skipped"]
    rig2 = Rig.load(cfg)
    assert write_look(rig2, compile_look(rig2, p), cfg.project_path)["skipped"]


def test_backup_created(cfg):
    rig = Rig.load(cfg)
    write_look(rig, compile_look(rig, LookParams(recipe="color_wash", targets=[8], colors=["red"])), cfg.project_path)
    assert list((cfg.project_path.parent / "backups").glob("*.qxw"))


def test_preview_moves_pan_and_tilt(rig):
    look = compile_look(rig, LookParams(recipe="circle", targets=[1], colors=["blue"], bpm=120))
    pv = simulate(rig, look, seconds=2.0, fps=10)
    pan = [fr[rig.fixtures[1].dmx(0)] for fr in pv["frames"]]
    assert len(set(pan)) > 3


def test_unknown_recipe(rig):
    with pytest.raises(ValueError):
        compile_look(rig, LookParams(recipe="laser_show", targets=[1]))


def test_movement_needs_movers(rig):
    with pytest.raises(ValueError):
        compile_look(rig, LookParams(recipe="circle", targets=rig.zones["pars"], colors=["red"]))
