"""Executor + planner journeys against the fake QLC+ (fork commands on), using the real model."""

import asyncio
import copy

import pytest

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.devtools import make_test_project
from lightai.rig.facts import LearnedFacts
from lightai.rig.qxw import Workspace

MODEL = load_config().current_model_dir()
pytestmark = pytest.mark.skipif(MODEL is None, reason="no promoted model")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from lightai.app import LightAI

    tmp = tmp_path_factory.mktemp("exec")
    cfg = copy.copy(load_config())
    cfg.data_dir = tmp / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp / "learned.yaml"
    cfg.project_path = make_test_project(tmp / "test-project")
    fake = FakeQlc(cfg.project_path, fork=True).start()
    cfg.qlc_url = fake.url
    cfg.reload_strategy = "auto"
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    loop = asyncio.new_event_loop()
    yield {"ai": ai, "fake": fake, "cfg": cfg, "run": loop.run_until_complete}
    loop.run_until_complete(ai.executor.client.close())
    loop.close()
    fake.stop()


def do(env, text, **kw):
    ai = env["ai"]
    cmd, plan = ai.plan(text)
    res = env["run"](ai.executor.execute(plan, **kw))
    return cmd, plan, res


def test_create_look_writes_and_reloads(env):
    cmd, plan, res = do(env, "slow blue wash breathing at 60 BPM")
    assert not res["ok"] and res["needs_confirmation"]
    res = env["run"](env["ai"].executor.execute(plan, confirm=True))
    assert res["ok"], res
    reload = next(r for r in res["results"] if r["op"] == "reload")
    assert reload["strategy"] == "loadProjectFile" and reload["loaded"] and reload["look_visible"]
    names = {f["name"] for f in env["fake"].functions.values()}
    assert plan.look["name"] in names
    side = (env["cfg"].project_path.parent / "lightai-looks.json").read_text()
    assert "breathe" in side


def test_reload_blocked_while_running_then_forced(env):
    fake = env["fake"]
    fid = next(iter(fake.functions))
    fake.functions[fid]["running"] = True
    try:
        before = env["cfg"].project_path.read_bytes()
        cmd, plan, res = do(env, "fast red circles on the spots", confirm=True)
        blocked = next(r for r in res["results"] if r.get("blocked"))
        assert blocked["op"] == "preflight" and not res["ok"]
        assert env["cfg"].project_path.read_bytes() == before, "nothing may be written before the reload is confirmed"
        res = env["run"](env["ai"].executor.execute(plan, confirm=True, allow_running_reload=True))
        assert res["ok"], res
        reload = next(r for r in res["results"] if r["op"] == "reload")
        assert fid in reload["restarted"] and fake.functions[fid]["running"], "functions that were running are started again"
    finally:
        fake.functions[fid]["running"] = False


def test_rotate_move_rename(env):
    cfg = env["cfg"]
    before = Workspace.load(cfg.project_path).monitor_items()[3]["rotation"]
    _, plan, res = do(env, "fixture 3 needs to be rotated 90 degrees", confirm=True)
    assert res["ok"], res
    assert Workspace.load(cfg.project_path).monitor_items()[3]["rotation"] == (before + 90) % 360
    x0 = Workspace.load(cfg.project_path).monitor_items()[8]["x"]
    _, plan, res = do(env, "move wash 1 left 50 cm", confirm=True)
    assert res["ok"], res
    assert abs(Workspace.load(cfg.project_path).monitor_items()[8]["x"] - max(0.0, x0 - 500)) < 0.01
    _, plan, res = do(env, "rename fixture 21 to DJ Wall", confirm=True)
    assert res["ok"], res
    names = {f.id: f.name for f in Workspace.load(cfg.project_path).fixtures()}
    assert names[21] == "DJ Wall"


def test_live_levels_channels_release(env):
    ai, fake = env["ai"], env["fake"]
    _, plan, res = do(env, "washes at 40%", confirm=True)
    assert res["ok"], res
    for fid in (8, 9, 10, 11):
        u, a = ai.rig.fixtures[fid].dmx(ai.rig.fixtures[fid].roles["dimmer"])
        addr = (u - 1) * 512 + a
        assert fake.values[addr] == 102 and addr in fake.override
    _, plan, res = do(env, "release the washes")
    assert res["ok"], res
    for fid in (8, 9, 10, 11):
        u, a = ai.rig.fixtures[fid].dmx(ai.rig.fixtures[fid].roles["dimmer"])
        assert (u - 1) * 512 + a not in fake.override
    _, plan, res = do(env, "set channel 9 on fixture 34 to 128")
    assert res["ok"], res
    u, a = ai.rig.fixtures[34].dmx(8)
    assert fake.values[(u - 1) * 512 + a] == 128
    _, plan, res = do(env, "release overrides")
    assert res["ok"] and not fake.override


def test_grand_master_and_blackout_fallback(env):
    fake = env["fake"]
    _, _, res = do(env, "grand master 75%")
    assert res["ok"] and fake.gm == 191
    _, plan, res = do(env, "blackout")
    assert res["ok"] and fake.gm == 0, "test project has no kill scene, so blackout uses the grand master"
    _, plan, res = do(env, "lights back on")
    assert res["ok"] and fake.gm == 255


def test_run_stop_query_stop_all(env):
    ai, fake = env["ai"], env["fake"]
    name = next(f.name for f in ai.rig.functions.values() if f.name.endswith("Breathe 60bpm"))
    _, plan, res = do(env, f"start {name.lower()}", confirm=True)
    assert res["ok"], res
    fid = plan.actions[0].args["id"]
    assert fake.functions[fid]["running"]
    _, plan, res = do(env, "what's running")
    running = next(r for r in res["results"] if r["op"] == "query_status")["running"]
    assert any(r["id"] == fid for r in running)
    _, plan, res = do(env, "stop everything")
    assert res["ok"] and not any(f["running"] for f in fake.functions.values())


def test_bpm_retimes_running_ai_look(env):
    ai, fake = env["ai"], env["fake"]
    side = __import__("json").loads((env["cfg"].project_path.parent / "lightai-looks.json").read_text())
    breathe = next(e for e in side["looks"].values() if e["recipe"] == "breathe")
    fake.functions[breathe["main_id"]]["running"] = True
    _, plan, res = do(env, "bpm 120")
    assert res["ok"], res
    retimed = next(r for r in res["results"] if r["op"] == "retime_looks")["retimed"]
    assert retimed and all("duration" in r for r in retimed)
    chaser = next(r for r in retimed)
    assert fake.functions[chaser["id"]]["speed"][2] == chaser["duration"] == 1000
    fake.functions[breathe["main_id"]]["running"] = False
    assert ai.session.bpm == 120


def test_propose_preview_releases_everything(env):
    ai, fake = env["ai"], env["fake"]
    _, plan, _ = (lambda c: (c[0], c[1], None))(ai.plan("make an assumption on fixture 5 and make it move"))
    assert plan.intent == "propose_look" and plan.actions[0].op == "preview"
    assert plan.actions[0].args.get("lazy") and "deltas" not in plan.actions[0].args, "frames are simulated on play, not at plan time"
    plan.actions[0].args["seconds"] = 0.5
    res = env["run"](ai.executor.execute(plan, wait_preview=True))
    assert res["ok"], res
    assert not fake.override, "preview must release every Simple Desk channel"
    _, fbplan, res = do(env, "that was pink not blue")
    assert res["ok"], res
    learned = LearnedFacts(env["cfg"].learned_path).data
    assert learned["facts"], "color feedback must be learned"


def test_calibration_wheel(env):
    ai, fake = env["ai"], env["fake"]
    _, plan, res = do(env, "find blue on fixture 34")
    assert res["ok"] and res["results"][0]["calibration"]
    r = env["run"](ai.executor.calibrate_answer("next"))
    assert r["step"] == 2
    r = env["run"](ai.executor.calibrate_answer("yes"))
    assert r["calibration"] is False and "blue" in r["learned"]
    assert not fake.override
    assert ai.rig.fixtures[34].caps["wheel"]["blue"] == plan.actions[0].args["steps"][1]["value"]


def test_clarify_is_not_executed(env):
    _, plan, res = do(env, "hey fixture x needs to be rotated 90 degrees")
    assert plan.mode == "clarify" and not res["ok"] and res["clarify"]


def test_project_mismatch_is_blocked(env, tmp_path):
    from lightai.exec.http import reload_project

    other = make_test_project(tmp_path / "other")
    res = env["run"](reload_project(env["ai"].executor.client, other, "auto"))
    assert res.get("blocked") and "open" in res["note"]


def test_stock_qlc_has_no_fork_commands():
    from lightai.exec.http import supports_fork_commands
    from lightai.exec.wsclient import QlcClient

    fake = FakeQlc(fork=False).start()
    try:
        async def run():
            c = QlcClient(url=fake.url, timeout=1.0)
            await c.connect()
            try:
                return await supports_fork_commands(c)
            finally:
                await c.close()

        assert asyncio.run(run()) is False
    finally:
        fake.stop()


def test_calibration_rgb_swaps_channels(env):
    ai = env["ai"]
    _, plan, res = do(env, "find blue on fixture 21")
    assert res["ok"] and plan.actions[0].args["mode"] == "rgb"
    r = env["run"](ai.executor.calibrate_answer("that one is green"))
    r = env["run"](ai.executor.calibrate_answer("red"))
    r = env["run"](ai.executor.calibrate_answer("yes"))
    assert r["calibration"] is False
    fx = ai.rig.fixtures[21]
    assert fx.roles["green"] == 0 and fx.roles["red"] == 1 and fx.roles["blue"] == 2
    assert not env["fake"].override
