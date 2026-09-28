"""HTTP API + console against the fake QLC+ (fork on), with the real model."""

import copy
import time

import pytest

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.devtools import make_test_project

MODEL = load_config().current_model_dir()
pytestmark = pytest.mark.skipif(MODEL is None, reason="no promoted model")


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    from fastapi.testclient import TestClient

    from lightai.api.server import create_app
    from lightai.app import LightAI

    tmp = tmp_path_factory.mktemp("api")
    cfg = copy.copy(load_config())
    cfg.data_dir = tmp / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp / "learned.yaml"
    cfg.project_path = make_test_project(tmp / "test-project")
    fake = FakeQlc(cfg.project_path, fork=True).start()
    cfg.qlc_url = fake.url
    cfg.reload_strategy = "auto"
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    with TestClient(create_app(ai), base_url="http://127.0.0.1:8765") as client:
        yield {"c": client, "fake": fake, "ai": ai, "cfg": cfg}
    fake.stop()


def test_static_and_meta(api):
    c = api["c"]
    r = c.get("/")
    assert r.status_code == 200 and "lightai" in r.text and "<script>" in r.text
    h = c.get("/health").json()
    assert h["model_version"] == MODEL.name and h["fixtures"] == 36
    L = c.get("/labels").json()
    from lightai.schema import labels

    assert len(L["intents"]) == len(labels()["intents"]) and "target" in L["slots"]
    assert "blue" in c.get("/colors").json()["colors"]
    assert len(c.get("/rig").json()["fixtures"]) == 36


def test_parse_plan_execute_confirm(api):
    c = api["c"]
    cmd = c.post("/parse", json={"text": "washes at 40%"}).json()
    assert cmd["intent"] == "set_level"
    r = c.post("/plan", json={"text": "slow blue wash breathing at 60 BPM"}).json()
    pid = r["plan"]["plan_id"]
    assert r["plan"]["needs_confirmation"] and r["plan"]["followup"]["preview"] is True
    assert c.get(f"/plan/{pid}").status_code == 200
    ex = c.post("/execute", json={"plan_id": pid}).json()
    assert ex["needs_confirmation"] and not ex["ok"]
    ex = c.post("/execute", json={"plan_id": pid, "confirm": True}).json()
    assert ex["ok"], ex
    pv = c.post("/preview", json={"plan_id": pid}).json()
    assert pv["ok"] and pv["results"][0]["started"]
    time.sleep(0.3)
    assert c.post("/stop", json={}).json()["stopped"]
    for _ in range(20):
        if not api["fake"].override:
            break
        time.sleep(0.05)
    assert not api["fake"].override, "stopping a preview must release its channels"
    fr = c.get("/functions", params={"q": "blue wash breathe"}).json()["results"]
    assert fr and "Breathe" in fr[0]["name"]


def test_proposal_feedback_apply(api):
    c = api["c"]
    r = c.post("/plan", json={"text": "make an assumption on fixture 5 and make it move"}).json()
    pid = r["plan"]["plan_id"]
    assert r["plan"]["intent"] == "propose_look"
    fb = c.post("/feedback", json={"plan_id": pid, "verdict": "wrong", "issues": [{"kind": "color", "fixture_id": 5,
                                   "expected": r["plan"]["look"]["params"]["colors"][0], "observed": "pink"}]}).json()
    assert "rig_facts" in fb["routed_to"] or "taste" in fb["routed_to"]
    ap = c.post("/apply_proposal", json={"plan_id": pid}).json()
    new = ap["plan"]
    assert new["intent"] == "create_look" and new["needs_confirmation"]
    ex = c.post("/execute", json={"plan_id": new["plan_id"], "confirm": True, "allow_running_reload": True}).json()
    assert ex["ok"], ex
    bad = c.post("/apply_proposal", json={"plan_id": new["plan_id"]})
    assert bad.status_code == 400


def test_teach_validation(api):
    c = api["c"]
    ok = c.post("/teach", json={"text": "make the bars pulse red", "intent": "create_look",
                                "marked": "make the [target:bars] [movement:pulse] [color:red]"}).json()
    assert ok["saved"]
    rows = (api["cfg"].data_dir / "corrections.jsonl").read_text().strip().splitlines()
    assert rows and "bars" in rows[-1]
    assert c.post("/teach", json={"text": "x", "intent": "dance", "marked": "x"}).status_code == 400
    assert c.post("/teach", json={"text": "x", "intent": "create_look", "marked": "[color:red"}).status_code == 400
    assert c.post("/teach", json={"text": "x", "intent": "create_look", "marked": "[flavour:red]"}).status_code == 400
    from lightai.train.generate import read_corrections

    from lightai.schema import labels

    assert any(r["marked"].startswith("make the [target:bars]") for r in read_corrections(api["cfg"].data_dir / "corrections.jsonl", labels()["version"]))


def test_bpm_tap_and_value(api):
    c = api["c"]
    for _ in range(4):
        r = c.post("/bpm", json={"tap": True}).json()
        time.sleep(0.5)
    assert r["bpm"] and 100 <= r["bpm"] <= 130
    r = c.post("/bpm", json={"bpm": 126}).json()
    assert r["bpm"] == 126
    plan = c.post("/plan", json={"text": "red chase on the pars"}).json()["plan"]
    assert plan["look"]["params"]["bpm"] == 126


def test_calibration_and_errors(api):
    c = api["c"]
    assert c.post("/calibrate", json={"answer": "next"}).status_code == 409
    r = c.post("/plan", json={"text": "find blue on fixture 34"}).json()["plan"]
    ex = c.post("/execute", json={"plan_id": r["plan_id"], "confirm": True}).json()
    assert ex["results"][0]["calibration"]
    step = c.post("/calibrate", json={"answer": "next"}).json()
    assert step["step"] == 2
    done = c.post("/calibrate", json={"answer": "stop"}).json()
    assert done["calibration"] is False
    assert not api["fake"].override
    assert c.post("/execute", json={"plan_id": "nope"}).status_code == 404
    assert c.get("/plan/nope").status_code == 404
    assert c.post("/plan", json={}).status_code == 422
    empty = c.post("/plan", json={"text": "   "}).json()
    assert empty["plan"]["mode"] == "clarify"


def test_guards_and_validation(api):
    """API agent findings: DNS rebinding, cross-site POSTs, bad feedback, double clicks, bad numbers, bad teach rows."""
    c = api["c"]
    assert c.get("/health", headers={"host": "evil.example:8765"}).status_code == 400
    assert c.post("/stop", headers={"origin": "http://evil.example"}).status_code == 403
    assert c.post("/stop", json={}, headers={"origin": "http://127.0.0.1:8765"}).status_code == 200
    pid = c.post("/plan", json={"text": "slow blue wash breathing at 60 BPM"}).json()["plan"]["plan_id"]
    assert c.post("/feedback", json={"plan_id": "nope", "verdict": "ok"}).status_code == 404
    assert c.post("/feedback", json={"plan_id": pid, "verdict": "banana"}).status_code == 422
    assert c.post("/feedback", json={"plan_id": pid, "verdict": "adjust", "issues": [{"kind": "banana"}]}).status_code == 422
    assert c.post("/feedback", json={"plan_id": pid, "verdict": "adjust", "issues": [{"kind": "speed", "direction": "sideways"}]}).status_code == 422
    first = c.post("/feedback", json={"plan_id": pid, "verdict": "ok"}).json()
    again = c.post("/feedback", json={"plan_id": pid, "verdict": "ok"}).json()
    assert not first.get("duplicate") and again.get("duplicate") and again["changes"] == []
    nodir = c.post("/feedback", json={"plan_id": pid, "verdict": "adjust", "issues": [{"kind": "speed"}]}).json()
    assert not any("default speed" in ch for ch in nodir["changes"]), nodir
    for bad in ({"bpm": 0}, {"bpm": 999}, {"bpm": -5}):
        assert c.post("/bpm", json=bad).status_code == 422
    assert c.post("/plan", json={"text": "x" * 501}).status_code == 422
    base = {"text": "make the bars pulse red", "intent": "create_look"}
    assert c.post("/teach", json={**base, "marked": ""}).status_code == 400
    assert c.post("/teach", json={**base, "marked": "make the [target:pars] [movement:pulse] [color:red]"}).status_code == 400
    assert c.post("/teach", json={**base, "marked": "make the [target:bars] [movement:pulse] [color:red]"}).status_code == 200
