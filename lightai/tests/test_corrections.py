"""Corrections: 'no, I meant the spots', 'what I meant was swarms', "that's wrong", and undoing what the wrong command
changed. Planning runs against the main show (read-only); the undo test writes to a copy with a fake QLC+."""

import asyncio
import copy
import shutil
from types import SimpleNamespace

import pytest

from fakeqlc import FakeQlc
from lightai.config import load_config
from lightai.rig.qxw import Workspace

MODEL = load_config().current_model_dir()
pytestmark = pytest.mark.skipif(MODEL is None, reason="no promoted model")


@pytest.fixture()
def ai():
    from lightai.app import LightAI

    return LightAI(load_config(), model_dir=MODEL, use_embeddings=False)


@pytest.mark.parametrize("first,second,intent,check", [
    ("washes at 50%", "no, I meant the spots", "set_level", lambda c, p: "spots" in p.summary and "50%" in p.summary),
    ("spots at 30%", "no, 80%", "set_level", lambda c, p: "80%" in p.summary),
    ("add 2 pars", "what I meant was swarms", "add_fixture", lambda c, p: "Swarm 5 FX" in p.summary and "Patch 2" in p.summary),
    ("washes blue", "not that, pink", "create_look", lambda c, p: "Pink" in p.summary),
    ("washes at 50%", "actually, stop everything", "stop_all", lambda c, p: True),
])
def test_corrections_replan_the_previous_command(ai, first, second, intent, check):
    _, p1 = ai.plan(first)
    c2, p2 = ai.plan(second)
    assert c2.intent == intent and c2.corrects == p1.plan_id and check(c2, p2), (c2.intent, p2.summary)
    assert p2.followup["corrects"] == p1.plan_id and p2.followup["previous_text"] == first


def test_a_bare_correction_asks(ai):
    _, p1 = ai.plan("washes at 50%")
    c2, p2 = ai.plan("that's wrong")
    assert p2.mode == "clarify" and "washes at 50%" in p2.summary and c2.corrects == p1.plan_id


def test_no_previous_command_means_no_correction(ai):
    c, _ = ai.plan("no, the washes")
    assert c.corrects is None


def test_correcting_an_earlier_history_item(ai):
    _, p1 = ai.plan("washes at 50%")
    ai.plan("spots at 20%")
    c, p = ai.correct_plan(p1.plan_id, "the pars")
    assert c.intent == "set_level" and "pars" in p.summary and "50%" in p.summary and c.corrects == p1.plan_id


def test_undo_what_the_wrong_command_wrote(tmp_path):
    from lightai.app import LightAI
    from lightai.devtools import make_test_project

    cfg = copy.copy(load_config())
    cfg.project_path = make_test_project(tmp_path / "show")
    cfg.data_dir = tmp_path / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp_path / "learned.yaml"
    fake = FakeQlc(cfg.project_path, fork=True).start()
    cfg.qlc_url = fake.url
    ai = LightAI(cfg, model_dir=MODEL, use_embeddings=False)
    loop = asyncio.new_event_loop()
    try:
        before = {f.id for f in Workspace.load(cfg.project_path).functions()}
        _, p1 = ai.plan("slow blue wash on the washes")
        assert p1.mode == "structural", p1.summary
        assert loop.run_until_complete(ai.executor.execute(p1, confirm=True, allow_running_reload=True))["ok"]
        added = {f.id for f in Workspace.load(cfg.project_path).functions()} - before
        assert added
        _, p2 = ai.plan("no, I meant pink")
        undo_id = p2.followup.get("undo_plan_id")
        assert undo_id and "remove the" in p2.followup["undo_summary"], p2.followup
        res = loop.run_until_complete(ai.executor.execute(ai.session.get(undo_id), confirm=True, allow_running_reload=True))
        assert res["ok"], res
        assert {f.id for f in Workspace.load(cfg.project_path).functions()} == before
    finally:
        loop.run_until_complete(ai.executor.client.close())
        loop.close()
        fake.stop()
