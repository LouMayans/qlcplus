"""The show designer without Claude: validation, composition, applying and removing a design on a copy of the main
show (fake QLC+), a design job with a repair round, and the API flow. A stand-in backend replays canned answers."""

import asyncio
import copy
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.design.jobs import JobStore, parse_request, run_design_job
from lightai.design.spec import DesignResult
from lightai.design.validate import validate_design
from lightai.rig.qxw import Workspace

MAIN = load_config().project_path
MODEL = load_config().current_model_dir()

CANNED = {
    "shows": [
        {"title": "Velvet Pulse", "bpm": 124, "mood_tags": ["dreamy"], "sections": [
            {"name": "Float", "bars": 8, "transition_fade_ms": 2000, "looks": [
                {"layer": "base", "recipe": "color_wash", "targets": ["washes"], "colors": ["lavender"], "intensity": 0.6},
                {"layer": "movement", "recipe": "circle_wave", "targets": ["spots"], "colors": ["pink"], "rate": "slow",
                 "order": "center_out"}]},
            {"name": "Flicker", "bars": 4, "transition_fade_ms": 500, "looks": [
                {"layer": "strobe", "recipe": "strobe", "targets": ["pars"], "colors": ["white"], "rate": "fast"},
                {"layer": "position", "recipe": "position", "targets": ["spots"], "aim": "dance floor", "spread": "cross"}]}]},
        {"title": "Neon Drift", "bpm": 128, "sections": [
            {"name": "Drift", "bars": 16, "looks": [
                {"layer": "base", "recipe": "color_chase", "targets": ["pars", "washes"], "colors": ["cyan", "magenta"],
                 "rate": "slow"}]}]},
    ],
    "notes": "two shows",
}


class Backend:
    """Replays canned answers like the Claude backend would, one per call."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def run(self, prompt, *, kind, model, schema=None, system_file=None, allowed_tools=(), disallowed_tools=(),
                  agents=None, timeout_s=600.0, budget_usd=None, on_event=None, **kw):
        self.calls.append({"prompt": prompt, "kind": kind, "model": model, "system_file": system_file,
                           "allowed": tuple(allowed_tools), "disallowed": tuple(disallowed_tools)})
        if on_event:
            on_event({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "WebSearch", "input": {"query": "dreamy club lighting"}}]}})
        data = self.answers.pop(0)
        return SimpleNamespace(ok=True, data=data, text="", run_id=f"r{len(self.calls)}", usage={}, cost_usd=0.0,
                               num_turns=1, duration_ms=1, error=None)


@pytest.fixture()
def env(tmp_path):
    from lightai.app import LightAI

    show = tmp_path / "show" / "Main Project.qxw"
    show.parent.mkdir()
    shutil.copy(MAIN, show)
    stage = MAIN.with_name(MAIN.stem + ".stage.json")
    if stage.exists():
        shutil.copy(stage, show.with_name(show.stem + ".stage.json"))
    cfg = copy.copy(load_config())
    cfg.project_path, cfg.main_project_path = show, None
    cfg.data_dir = tmp_path / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp_path / "learned.yaml"
    fake = FakeQlc(show, fork=True).start()
    cfg.qlc_url = fake.url
    if MODEL is None:
        pytest.skip("no promoted model")
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    loop = asyncio.new_event_loop()
    yield SimpleNamespace(ai=ai, cfg=cfg, fake=fake, run=loop.run_until_complete)
    loop.run_until_complete(ai.executor.client.close())
    loop.close()
    fake.stop()


@pytest.mark.parametrize("text,count,minutes", [
    ("Can you create 3 different shows that resemble fast strobing but changing in light a dreamy way", 3, None),
    ("a 5-minute show with slow washes", 1, 5.0), ("two new shows", 2, None), ("make a show", 1, None)])
def test_parse_request(text, count, minutes):
    assert parse_request(text) == (count, minutes)


def test_the_canned_design_is_valid(env):
    errors, warnings, resolved = validate_design(env.ai.get_rig(), DesignResult.model_validate(CANNED))
    assert errors == [] and len(resolved) == 5


def test_validation_errors_say_how_to_fix(env):
    bad = DesignResult.model_validate({"shows": [{"title": "Bad", "sections": [{"name": "x", "looks": [
        {"recipe": "laser_tunnel", "targets": ["spots"]},
        {"recipe": "color_wash", "targets": ["the pyros"], "colors": ["octarine"]},
        {"recipe": "position", "targets": ["spots"], "aim": "the moon"},
        {"recipe": "circle", "targets": ["pars"]},
        {"recipe": "color_wash", "targets": ["washes"], "order": "sideways"}]}]}]})
    errors, _, _ = validate_design(env.ai.get_rig(), bad)
    text = " | ".join(errors)
    for needle in ("unknown recipe 'laser_tunnel'", "'the pyros' is not a fixture group", "unknown color 'octarine'",
                   "'the moon' is not a place", "needs moving heads", "unknown order 'sideways'"):
        assert needle in text, (needle, errors)


def test_compose_loop_and_single(env):
    from lightai.design.composer import compose_design

    rig = env.ai.get_rig()
    shows = compose_design(rig, DesignResult.model_validate(CANNED))
    s = shows[0]
    main = next(f for f in s.functions if f.id == s.main_id)
    assert main.type == "Chaser" and main.run_order == "Loop" and main.speed_modes == ("PerStep", "PerStep", "PerStep")
    colls = [sec["collection_id"] for sec in s.sections]
    assert [st.function_id for st in main.steps] == colls and len(colls) == 2
    assert all(next(f for f in s.functions if f.id == c).type == "Collection" for c in colls)
    assert main.steps[0].fade_in + main.steps[0].hold == s.sections[0]["ms"] == round(8 * 4 * 60000 / 124)
    ids = [i for c in shows for i in c.ids]
    assert ids == list(range(ids[0], ids[0] + len(ids))) and ids[0] == rig.next_function_id()
    assert all(f.path.startswith("AI/Shows") for f in s.functions)
    timed = dict(CANNED, shows=[dict(CANNED["shows"][1], minutes=2)])
    t = compose_design(rig, DesignResult.model_validate(timed))[0]
    main = next(f for f in t.functions if f.id == t.main_id)
    assert t.kind == "timeline" and main.type == "Show" and main.time_division == ("BPM_4_4", 128)
    assert [tr.name for tr in main.tracks] == ["Base"] and not any(f.type == "Collection" and f.name.startswith("Neon Drift - ") for f in t.functions)
    items = [it for tr in main.tracks for it in tr.items]
    assert abs(max(it.start_ms + it.duration_ms for it in items) - 120000) < 50
    two = dict(CANNED, shows=[dict(CANNED["shows"][0], minutes=3)])
    v = compose_design(rig, DesignResult.model_validate(two))[0]
    vm = next(f for f in v.functions if f.id == v.main_id)
    assert [tr.name for tr in vm.tracks] == ["Base", "Movement", "Strobe", "Position"]
    starts = sorted({it.start_ms for tr in vm.tracks for it in tr.items})
    assert starts[0] == 0 and len(starts) == 2 and abs(max(it.start_ms + it.duration_ms for tr in vm.tracks for it in tr.items) - 180000) < 50


def test_pixel_layers_share_groups_across_a_design(env):
    """Pixel layers on the same fixtures share one new FixtureGroup across the shows of a design; it is written once,
    before every function (QLC+ loads in order), and never counted among the shows' function IDs."""
    from lightai.compiler import write_design
    from lightai.compiler.validate import validate_workspace
    from lightai.design.composer import compose_design
    from lightai.rig.model import Rig

    design = DesignResult.model_validate({"shows": [
        {"title": "Pixel A", "sections": [{"name": "a", "looks": [
            {"layer": "fx", "recipe": "pixel_wave", "targets": ["tetras"], "colors": ["blue", "pink"]}]}]},
        {"title": "Pixel B", "sections": [{"name": "b", "looks": [
            {"layer": "fx", "recipe": "pixel_chase", "targets": ["tetras"], "colors": ["red"]},
            {"layer": "accent", "recipe": "pixel_wave", "targets": ["led walls"], "colors": ["white"]}]}]}]})
    rig = Rig.load(env.cfg)
    errors, _, _ = validate_design(rig, design)
    assert not errors, errors
    before = len(rig.ws.fixture_groups())
    shows = compose_design(rig, design)
    assert [len(s.groups) for s in shows] == [1, 1]  # B reuses A's tetras group and adds one for the LED walls
    tetra_group = shows[0].groups[0].id
    assert next(f for f in shows[1].functions if f.type == "RGBMatrix").matrix["fixture_group"] == tetra_group
    assert all(f.type != "FixtureGroup" for s in shows for f in s.functions)
    write_design(rig, shows, text="pixels")
    ws = Workspace.load(env.cfg.project_path)
    tags = [c.tag.split("}")[-1] for c in ws.engine if isinstance(c.tag, str)]
    assert len(ws.fixture_groups()) == before + 2
    assert max(i for i, t in enumerate(tags) if t == "FixtureGroup") < tags.index("Function")
    assert not [e for e in validate_workspace(ws)[0] if "Monitor" not in e]


def test_apply_and_remove_a_design(env):
    from lightai.schema import Action, Plan

    ex, cfg = env.ai.executor, env.cfg
    before = {f.id for f in Workspace.load(cfg.project_path).functions()}
    plan = Plan(plan_id="p-design", intent="design_show", mode="structural", summary="add", needs_confirmation=True,
                show=str(cfg.project_path),
                actions=[Action(op="write_design", args={"design": CANNED, "shows": [0], "job_id": "j1"}),
                         Action(op="reload", args={"strategy": cfg.reload_strategy})])
    res = env.run(ex.execute(plan, confirm=True, allow_running_reload=True))
    assert res["ok"], res
    wrote = next(r for r in res["results"] if r["op"] == "write_design")
    after = {f.id for f in Workspace.load(cfg.project_path).functions()}
    assert set(wrote["ids"]) == after - before and len(wrote["shows"]) == 1
    from lightai.compiler.sidecar import Sidecar

    entry = Sidecar(cfg.sidecar_path).designs[str(wrote["shows"][0]["main_id"])]
    assert entry["title"] == "Velvet Pulse" and entry["job_id"] == "j1" and len(entry["ids"]) == len(wrote["ids"])
    rm = Plan(plan_id="p-rm", intent="delete_look", mode="structural", summary="remove", needs_confirmation=True,
              show=str(cfg.project_path),
              actions=[Action(op="delete_look", args={"ids": entry["ids"], "main_id": entry["main_id"]}),
                       Action(op="reload", args={"strategy": cfg.reload_strategy})])
    assert env.run(ex.execute(rm, confirm=True, allow_running_reload=True))["ok"]
    assert {f.id for f in Workspace.load(cfg.project_path).functions()} == before
    assert str(entry["main_id"]) not in Sidecar(cfg.sidecar_path).designs


def test_a_design_job_repairs_then_succeeds(env):
    broken = {"shows": [{"title": "Oops", "sections": [{"name": "a", "looks": [{"recipe": "laser_tunnel", "targets": ["spots"]}]}]}]}
    backend = Backend([broken, CANNED])
    store = JobStore(env.cfg.data_dir)
    job = store.new("3 dreamy strobe shows", 2, None)
    env.run(run_design_job(env.ai, job, store, backend))
    assert job.status == "done" and len(job.preview) == 2 and job.run_ids == ["r1", "r2"], (job.status, job.errors)
    assert backend.calls[1]["kind"] == "repair" and "laser_tunnel" in backend.calls[1]["prompt"]
    assert "Write" in backend.calls[0]["disallowed"] and "WebSearch" in backend.calls[0]["allowed"]
    ctx = Path(backend.calls[0]["system_file"]).read_text(encoding="utf-8")
    assert "## The rig" in ctx and "## Recipes" in ctx and "Make exactly 2 show(s)" in ctx
    assert any("searching the web" in p["text"] for p in job.progress)
    assert JobStore(env.cfg.data_dir).get(job.id).status == "done", "jobs survive a restart"


def test_the_briefing_carries_matching_reference_shows(env):
    """A decoded show from the private library that fits the request goes into Claude's briefing."""
    import shutil

    root = Path(env.cfg.data_dir) / "references"
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy(Path(__file__).parent / "data" / "formats" / "sample.xsq", root / "neon.xsq")
    backend = Backend([CANNED])
    store = JobStore(env.cfg.data_dir)
    job = store.new("2 shows like neon nights with strobes", 2, None)
    env.run(run_design_job(env.ai, job, store, backend))
    ctx = Path(backend.calls[0]["system_file"]).read_text(encoding="utf-8")
    assert "## Reference shows" in ctx and '"Neon Nights"' in ctx, ctx[-600:]


def test_a_design_job_gives_up_after_the_repairs(env):
    broken = {"shows": [{"title": "Oops", "sections": [{"name": "a", "looks": [{"recipe": "nope", "targets": ["spots"]}]}]}]}
    store = JobStore(env.cfg.data_dir)
    job = store.new("x", 1, None)
    env.run(run_design_job(env.ai, job, store, Backend([broken, broken, broken])))
    assert job.status == "error" and job.errors and len(job.run_ids) == 3


def test_the_design_api_end_to_end(env):
    from fastapi.testclient import TestClient

    from lightai.api.server import create_app

    backend = Backend([CANNED])
    with TestClient(create_app(env.ai, design_backend=backend), base_url="http://127.0.0.1:8765") as c:
        job = c.post("/design", json={"text": "two dreamy shows", "session": "t1"}).json()["job"]
        for _ in range(100):
            job = c.get(f"/design/{job['id']}").json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert job["status"] == "done" and [p["title"] for p in job["preview"]] == ["Velvet Pulse", "Neon Drift"], job
        plan = c.post(f"/design/{job['id']}/apply", json={"shows": [1]}).json()["plan"]
        assert plan["needs_confirmation"] and "Neon Drift" in plan["summary"]
        ex = c.post("/execute", json={"plan_id": plan["plan_id"], "confirm": True, "allow_running_reload": True}).json()
        assert ex["ok"], ex
        shows = c.get("/design/shows").json()
        assert [s["title"] for s in shows] == ["Neon Drift"]
        assert c.get(f"/design/{job['id']}").json()["applied"][0]["title"] == "Neon Drift"
        rm = c.post(f"/design/show/{shows[0]['main_id']}/remove").json()["plan"]
        assert c.post("/execute", json={"plan_id": rm["plan_id"], "confirm": True, "allow_running_reload": True}).json()["ok"]
        assert c.get("/design/shows").json() == []
        hist = c.get("/history", params={"limit": 20}).json()
        assert any(r.get("design_job") == job["id"] and r.get("session") == "t1" for r in hist)


def test_reaim_after_the_layout_changes(env):
    import json

    from lightai.compiler.sidecar import Sidecar
    from lightai.design.reaim import reaim_plan, stale_entries
    from lightai.rig.stage import stage_path
    from lightai.schema import Action, Plan

    ex, cfg = env.ai.executor, env.cfg
    if env.ai.get_rig().stage is None:
        pytest.skip("the main show has no stage file")
    plan = Plan(plan_id="p-d", intent="design_show", mode="structural", summary="add", needs_confirmation=True,
                show=str(cfg.project_path), actions=[Action(op="write_design", args={"design": CANNED, "shows": [0]}),
                                                     Action(op="reload", args={"strategy": cfg.reload_strategy})])
    assert env.run(ex.execute(plan, confirm=True, allow_running_reload=True))["ok"]
    assert stale_entries(env.ai.get_rig())["designs"] == []
    sp = stage_path(cfg.project_path)
    data = json.loads(sp.read_text(encoding="utf-8"))
    fid = next(iter(data["fixtures"]))
    data["fixtures"][fid]["pos"][0] += 36  # the operator moves a fixture 3 ft in the 3D editor
    sp.write_text(json.dumps(data), encoding="utf-8")
    stale = stale_entries(env.ai.get_rig())
    assert [e["title"] for e in stale["designs"]] == ["Velvet Pulse"]
    rp = reaim_plan(env.ai)
    assert rp.mode == "structural" and [a.op for a in rp.actions] == ["rewrite_design", "reload"]
    res = env.run(ex.execute(rp, confirm=True, allow_running_reload=True))
    assert res["ok"], res
    assert stale_entries(env.ai.get_rig())["designs"] == []
    designs = Sidecar(cfg.sidecar_path).designs
    assert [e["title"] for e in designs.values()] == ["Velvet Pulse"]
    assert reaim_plan(env.ai).mode == "info"


def test_rotate_designed_shows_through_the_night(env):
    from fastapi.testclient import TestClient

    from lightai.api.server import create_app
    from lightai.schema import Action, Plan

    ex, cfg = env.ai.executor, env.cfg
    plan = Plan(plan_id="p-rot", intent="design_show", mode="structural", summary="add", needs_confirmation=True,
                show=str(cfg.project_path), actions=[Action(op="write_design", args={"design": CANNED}),
                                                     Action(op="reload", args={"strategy": cfg.reload_strategy})])
    assert env.run(ex.execute(plan, confirm=True, allow_running_reload=True))["ok"]
    env.run(ex.client.close())  # the test client runs its own event loop: reconnect there
    with TestClient(create_app(env.ai, design_backend=object()), base_url="http://127.0.0.1:8765") as c:
        res = c.post("/design/rotate", json={"minutes": 20}).json()
        state = next(r for r in res["results"] if r["op"] == "start_rotation")
        assert res["ok"] and state["running"] and state["interval_s"] == 1200 and len(state["main_ids"]) == 2, res
        stop = c.post("/design/rotate/stop").json()
        assert stop["ok"] and not next(r for r in stop["results"] if r["op"] == "stop_rotation")["running"]
        hist = c.get("/history")  # the rotation's history row once broke /history (no 'note')
        assert hist.status_code == 200 and any("rotate shows" in (r.get("text") or "") for r in hist.json()), hist.text[:200]
