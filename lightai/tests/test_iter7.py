"""Iteration 7: a New show button; lightai edits the show QLC+ has open when it is a saved file; facts keyed by the
main show's IDs stay with the main show; looks are recorded per show; a plan made for another show is refused."""

import copy
import shutil

import pytest
from lxml import etree

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.devtools import make_test_project
from lightai.rig.model import Rig
from lightai.rig.newshow import create_empty_show, show_path
from lightai.rig.qxw import Workspace

MODEL = load_config().current_model_dir()


def test_an_empty_show_keeps_only_the_outputs(tmp_path):
    main = load_config().project_path
    dest = create_empty_show(main, tmp_path / "Empty.qxw")
    tree = etree.parse(str(dest), etree.XMLParser(remove_blank_text=True))
    root = tree.getroot()
    assert tree.docinfo.doctype == "<!DOCTYPE Workspace>" and root.get("CurrentWindow") == "FixtureManager"
    assert [etree.QName(c).localname for c in root] == ["Creator", "Engine"]
    assert [etree.QName(c).localname for c in root.find("{*}Engine")] == ["InputOutputMap"]
    io = lambda t: etree.tostring(t.getroot().find("{*}Engine/{*}InputOutputMap"), method="c14n")  # noqa: E731
    assert io(tree) == io(etree.parse(str(main), etree.XMLParser(remove_blank_text=True)))
    assert Workspace.load(dest).fixtures() == []
    with pytest.raises(FileExistsError):
        create_empty_show(main, dest)


@pytest.mark.parametrize("name,file", [("Friday Test", "Friday Test.qxw"), ("Friday Test.qxw", "Friday Test.qxw"), ("  Sat (v2) ", "Sat (v2).qxw")])
def test_show_names(tmp_path, name, file):
    assert show_path(tmp_path, name) == tmp_path / file


@pytest.mark.parametrize("name", ["../evil", "a/b", "a\\b", "C:x", "CON", "", "x" * 61, "name.", ".hidden"])
def test_bad_show_names(tmp_path, name):
    with pytest.raises(ValueError):
        show_path(tmp_path, name)


def test_another_show_gets_model_facts_but_not_main_show_ids(tmp_path):
    cfg = copy.copy(load_config())
    main_rig = Rig.load(cfg)
    other = tmp_path / "Other.qxw"
    shutil.copy(cfg.project_path, other)
    cfg.main_project_path, cfg.project_path = cfg.project_path, other
    rig = Rig.load(cfg)
    assert main_rig.is_main and not rig.is_main
    assert main_rig.stage_order and not rig.stage_order and rig.kill_function_id is None and not rig.protected_functions
    assert set(rig.zones) <= {"all"} and not rig.overrides.get("fixtures")
    assert rig.overrides.get("models") == main_rig.overrides.get("models") and rig.overrides.get("rules") == main_rig.overrides.get("rules")
    assert cfg.sidecar_path.name == "Other.lightai-looks.json"
    cfg.project_path = cfg.main_project_path
    assert cfg.sidecar_path.name == "lightai-looks.json"


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    from fastapi.testclient import TestClient

    from lightai.api.server import create_app
    from lightai.app import LightAI

    if MODEL is None:
        pytest.skip("no promoted model")
    tmp = tmp_path_factory.mktemp("newshow")
    cfg = copy.copy(load_config())
    cfg.data_dir = tmp / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp / "learned.yaml"
    cfg.project_path = make_test_project(tmp / "shows")
    other = cfg.project_path.parent / "Other Show.qxw"
    shutil.copy(cfg.project_path, other)
    fake = FakeQlc(cfg.project_path, fork=True).start()
    cfg.qlc_url = fake.url
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    main = cfg.project_path
    with TestClient(create_app(ai), base_url="http://127.0.0.1:8765") as client:
        yield {"c": client, "fake": fake, "cfg": cfg, "main": main, "other": other}
    fake.stop()


def _back_to_main(api):
    api["fake"].modified = False
    r = api["c"].post("/qlc/open-show", json={"force": True}).json()
    assert r["ok"], r
    assert api["c"].get("/qlc/show").json()["active_is_main"]


def test_new_show_is_created_opened_and_edited(api):
    c, fake, main = api["c"], api["fake"], api["main"]
    r = c.post("/qlc/new-show", json={"name": "Friday Test"}).json()
    new = main.parent / "Friday Test.qxw"
    assert r["ok"] and new.is_file() and fake.project.resolve() == new.resolve(), r
    s = c.get("/qlc/show").json()
    assert s["follows"] and not s["active_is_main"] and s["active"].endswith("Friday Test.qxw"), s
    p = c.post("/plan", json={"text": "add 2 vpar 7 channel fixtures to universe 1"}).json()["plan"]
    assert p["mode"] == "structural" and p["show"].endswith("Friday Test.qxw"), p["summary"]
    assert "Friday Test.qxw" in p["actions"][0]["describe"] and not any("QLC+ has" in w for w in p["warnings"])
    before = main.read_bytes()
    ex = c.post("/execute", json={"plan_id": p["plan_id"], "confirm": True, "allow_running_reload": True}).json()
    assert ex["ok"], ex
    assert len(Workspace.load(new).fixtures()) == 2 and main.read_bytes() == before, "only the new show may change"
    _back_to_main(api)
    assert fake.project.resolve() == main.resolve()


def test_new_show_refuses_bad_or_taken_names(api):
    c, main = api["c"], api["main"]
    assert c.post("/qlc/new-show", json={"name": "../evil"}).status_code == 422
    shutil.copy(main, main.parent / "Taken.qxw")
    assert c.post("/qlc/new-show", json={"name": "Taken"}).status_code == 409


def test_new_show_asks_before_discarding_unsaved_changes(api):
    c, fake, main = api["c"], api["fake"], api["main"]
    fake.modified = True
    r = c.post("/qlc/new-show", json={"name": "Sat Test"}).json()
    assert r.get("needs_confirmation") and not (main.parent / "Sat Test.qxw").exists(), r
    r = c.post("/qlc/new-show", json={"name": "Sat Test", "force": True}).json()
    assert r["ok"] and fake.project.name == "Sat Test.qxw" and not fake.modified, r
    _back_to_main(api)


def test_lightai_follows_saved_shows_but_not_unsaved_ones(api):
    c, fake = api["c"], api["fake"]
    fake.project = api["other"]
    s = c.get("/qlc/show").json()
    assert s["follows"] and s["active"].endswith("Other Show.qxw"), s
    fake.project = None  # QLC+ File > New: nothing to edit on disk
    s = c.get("/qlc/show").json()
    assert not s["follows"] and s["active"].endswith("Other Show.qxw"), s
    _back_to_main(api)


def test_a_plan_made_for_another_show_is_refused(api):
    c, fake = api["c"], api["fake"]
    p = c.post("/plan", json={"text": "add 2 vpar 7 channel fixtures to universe 1"}).json()["plan"]
    fake.project = api["other"]
    c.get("/qlc/show")  # lightai follows QLC+ to the other show
    ex = c.post("/execute", json={"plan_id": p["plan_id"], "confirm": True}).json()
    assert not ex["ok"] and "Type it again" in ex.get("clarify", ""), ex
    _back_to_main(api)
