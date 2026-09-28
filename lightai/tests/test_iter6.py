"""Iteration 6: a vague fixture model means the one your shows use, several groups in one add, and a structural change
never touches the main show while QLC+ has a different show open (unless the operator confirms)."""

import asyncio
import copy
import shutil

import pytest

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.devtools import make_test_project
from lightai.nlu.normalize import prefer_rig_model, resolve_library_model
from lightai.nlu.pipeline import patch_groups
from lightai.rig.house import assume_house_model, singular
from lightai.rig.qxw import Workspace

MODEL = load_config().current_model_dir()
needs_model = pytest.mark.skipif(MODEL is None, reason="no promoted model")


def _assume(rig, raw):
    return assume_house_model(rig, raw, prefer_rig_model(rig, resolve_library_model(rig.library, raw)))


@pytest.mark.parametrize("word,want", [("pars", "par"), ("washes", "wash"), ("heads", "head"), ("plus", "plus"), ("bass", "bass"), ("vpar", "vpar")])
def test_singular(word, want):
    assert singular(word) == want


def test_a_vague_par_is_the_par_your_shows_use(rig):
    v = _assume(rig, "american dj par")
    assert v.get("assumed") and v["model"].lower() == "vpar" and "VPAR" in v["note"], v
    v = _assume(rig, "par")
    assert v["model"].lower() == "vpar" and any(a["model"] == "ThinPAR 38" for a in v["alternatives"]), v


@pytest.mark.parametrize("raw,model", [("moving head", "BEAM230"), ("beams", "BEAM230"), ("spots", "BEAM230"), ("tetra", "Tetra Bar")])
def test_house_models_by_type_name_and_zone(rig, raw, model):
    assert _assume(rig, raw)["model"].lower() == model.lower()


def test_a_maker_you_dont_own_still_asks(rig):
    v = _assume(rig, "chauvet par")
    assert v.get("ambiguous") and not v.get("assumed") and all("chauvet" in c["manufacturer"].lower() for c in v["candidates"])


def test_an_exact_model_is_never_replaced(rig):
    v = _assume(rig, "american dj mega hex par")
    assert not v.get("assumed") and v["model"].lower() == "mega hex par"


def test_groups_from_the_words():
    g = patch_groups("Add 3 american dj par lights 7 address to universe 1 and 4 to universe 2")
    assert [(n, a) for n, a, _ in g] == [(None, {"universe": 0}), (4, {"universe": 1})]
    g = patch_groups("add 2 pars to universe 1 address 400 and four more to universe 2 at 300")
    assert [(n, a) for n, a, _ in g] == [(None, {"universe": 0, "address": 399}), (4, {"universe": 1, "address": 299})]
    assert patch_groups("add 3 pars to universe 1") == []


@pytest.fixture(scope="module")
def main_ai():
    from lightai.app import LightAI

    if MODEL is None:
        pytest.skip("no promoted model")
    return LightAI(load_config(), model_dir=MODEL, use_embeddings=False)  # plans only: nothing is written


@needs_model
def test_the_logged_sentence_patches_two_groups_of_vpars(main_ai):
    cmd, plan = main_ai.plan("Add 3 american dj par lights 7 address to universe 1 and 4 to universe 2")
    assert plan.mode == "structural", plan.summary
    fx = plan.actions[0].args["fixtures"]
    assert len(fx) == 7 and all(f["model"] == "VPar" and f["channels"] == 7 for f in fx)
    assert [f["universe"] for f in fx] == [0, 0, 0, 1, 1, 1, 1]
    assert [f["id"] for f in fx] == list(range(fx[0]["id"], fx[0]["id"] + 7))
    taken = [(f.universe, f.address, f.address + f.channels) for f in main_ai.rig.fixtures.values()]
    taken += [(f["universe"], f["address"], f["address"] + 7) for f in fx]
    assert all(not (u1 == u2 and a1 < b2 and a2 < b1) for i, (u1, a1, b1) in enumerate(taken) for (u2, a2, b2) in taken[i + 1:])
    assert any("your shows use" in a["fact"] for a in plan.assumptions)


@needs_model
@pytest.mark.parametrize("text,model", [("add 2 pars", "VPar"), ("add a moving head to universe 1", "BEAM230"),
                                        ("patch 2 tetra bars", "Tetra Bar")])
def test_short_patch_requests(main_ai, text, model):
    cmd, plan = main_ai.plan(text)
    assert cmd.intent == "add_fixture" and plan.mode == "structural", (cmd.intent, plan.summary)
    fx = plan.actions[0].args.get("fixtures") or [plan.actions[0].args]
    assert all(f["model"] == model for f in fx), fx


@needs_model
def test_levels_are_still_levels(main_ai):
    cmd, _ = main_ai.plan("add 20% to the pars")
    assert cmd.intent != "add_fixture"


@pytest.fixture()
def env(tmp_path):
    from lightai.app import LightAI

    if MODEL is None:
        pytest.skip("no promoted model")
    cfg = copy.copy(load_config())
    cfg.data_dir = tmp_path / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp_path / "learned.yaml"
    cfg.project_path = make_test_project(tmp_path / "test-project")
    other = tmp_path / "other" / "Other Show.qxw"
    other.parent.mkdir()
    shutil.copy(cfg.project_path, other)
    fake = FakeQlc(cfg.project_path, fork=True).start()
    cfg.qlc_url = fake.url
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    loop = asyncio.new_event_loop()
    yield {"ai": ai, "fake": fake, "cfg": cfg, "other": other, "run": loop.run_until_complete}
    loop.run_until_complete(ai.executor.client.close())
    loop.close()
    fake.stop()


@pytest.mark.parametrize("open_show", ["other", "untitled"])
def test_another_show_open_blocks_before_writing(env, open_show):
    ai, fake, cfg, run = env["ai"], env["fake"], env["cfg"], env["run"]
    fake.project = env["other"] if open_show == "other" else None
    before = cfg.project_path.read_bytes()
    n = len(Workspace.load(cfg.project_path).fixtures())
    _, plan = ai.plan("add 2 vpar 7 channel fixtures to universe 1")
    assert plan.mode == "structural", plan.summary
    res = run(ai.executor.execute(plan, confirm=True, allow_running_reload=True))
    blocked = next(r for r in res["results"] if r.get("blocked"))
    assert not res["ok"] and blocked["other_show"], res
    assert ("Other Show.qxw" in blocked["note"]) if open_show == "other" else ("new, unsaved show" in blocked["note"])
    assert cfg.project_path.read_bytes() == before, "nothing may be written before the operator confirms"
    res = run(ai.executor.execute(plan, confirm=True, allow_running_reload=True, allow_other_show=True))
    assert res["ok"], res
    reload = next(r for r in res["results"] if r["op"] == "reload")
    assert reload["loaded"] and reload["strategy"] == "openProjectFile", reload
    assert fake.project.resolve() == cfg.project_path.resolve()
    assert len(Workspace.load(cfg.project_path).fixtures()) == n + 2


def test_the_main_show_open_needs_no_extra_confirmation(env):
    ai, run = env["ai"], env["run"]
    _, plan = ai.plan("add 2 vpar 7 channel fixtures to universe 1")
    res = run(ai.executor.execute(plan, confirm=True, allow_running_reload=True))
    assert res["ok"], res


@needs_model
@pytest.mark.parametrize("text,universe", [("add swarm fixture to project", 1), ("add swarm fixture to universe 1", 0),
                                           ("add a swarm to universe 2", 1), ("add one more swarm", 1)])
def test_patch_requests_without_a_number(main_ai, text, universe):
    cmd, plan = main_ai.plan(text)
    assert cmd.intent == "add_fixture" and plan.mode == "structural", (cmd.intent, plan.summary)
    f = plan.actions[0].args
    assert f["model"] == "Swarm 5 FX" and f["universe"] == universe, f


@needs_model
@pytest.mark.parametrize("text", ["add fog", "add the swarm chase to the look", "add blue to the washes", "add strobe"])
def test_not_patch_requests(main_ai, text):
    cmd, _ = main_ai.plan(text)
    assert cmd.intent != "add_fixture", text
