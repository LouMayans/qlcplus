"""Console / API: open lightai's main show in QLC+ (fork v2 openProjectFile), against the fake QLC+."""

import copy
import shutil

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

    tmp = tmp_path_factory.mktemp("openshow")
    cfg = copy.copy(load_config())
    cfg.data_dir = tmp / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp / "learned.yaml"
    cfg.project_path = make_test_project(tmp / "test-project")
    other = tmp / "other" / "Other Show.qxw"
    other.parent.mkdir()
    shutil.copy(cfg.project_path, other)
    fake = FakeQlc(cfg.project_path, fork=True).start()
    cfg.qlc_url = fake.url
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    with TestClient(create_app(ai), base_url="http://127.0.0.1:8765") as client:
        yield {"c": client, "fake": fake, "cfg": cfg, "other": other}
    fake.stop()


def test_show_status_reports_the_main_show(api):
    s = api["c"].get("/qlc/show").json()
    assert s["running"] and s["is_main"] and s["can_open"], s


def test_open_main_show_asks_before_discarding_unsaved_changes(api):
    c, fake, cfg = api["c"], api["fake"], api["cfg"]
    fake.project, fake.modified = api["other"], True  # QLC+ has another show open, with unsaved edits
    s = c.get("/qlc/show").json()
    assert not s["is_main"] and s["modified"]
    r = c.post("/qlc/open-show", json={}).json()
    assert r.get("needs_confirmation") and "unsaved" in r["note"]
    assert fake.project == api["other"], "nothing may change before the operator confirms"
    r = c.post("/qlc/open-show", json={"force": True}).json()
    assert r["ok"], r
    assert fake.project.resolve() == cfg.project_path.resolve() and not fake.modified
    assert c.get("/qlc/show").json()["is_main"]


def test_open_main_show_when_it_is_already_open(api):
    r = api["c"].post("/qlc/open-show", json={}).json()
    assert r["ok"] and r.get("already")


def test_plan_warns_and_execute_blocks_when_qlc_has_another_show_open(api):
    c, fake = api["c"], api["fake"]
    fake.project, fake.modified = api["other"], False
    try:
        r = c.post("/plan", json={"text": "add 2 vpar 7 channel fixtures to universe 1"}).json()
        assert r["plan"]["mode"] == "structural", r["plan"]["summary"]
        assert "Other Show.qxw" in r["plan"]["warnings"][0], r["plan"]["warnings"]
        ex = c.post("/execute", json={"plan_id": r["plan"]["plan_id"], "confirm": True}).json()
        blocked = next(x for x in ex["results"] if x.get("blocked"))
        assert not ex["ok"] and blocked["other_show"], ex
    finally:
        fake.project = api["cfg"].project_path


def test_history_keeps_what_was_typed_and_what_came_of_it(api):
    c = api["c"]
    p = c.post("/plan", json={"text": "add 2 vpar 7 channel fixtures to universe 1"}).json()["plan"]
    c.post("/execute", json={"plan_id": p["plan_id"], "confirm": False})
    h = c.get("/history", params={"limit": 5}).json()
    row = next(r for r in h if r.get("plan_id") == p["plan_id"])
    assert row["text"] == "add 2 vpar 7 channel fixtures to universe 1" and row["intent"] == "add_fixture"
    assert row["executed"] is False and "confirmation" in row["result"], row
