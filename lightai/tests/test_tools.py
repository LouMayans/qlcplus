"""Audit/fix, mining, transplant, nightly job, CLI and WebSocket client basics."""

import argparse
import asyncio
import json

import pytest

from conftest import MAIN
from fakeqlc import FakeQlc
from lightai.config import REPO_ROOT
from lightai.exec.wsclient import Latency, QlcClient, QlcError, validate_message
from lightai.rig.audit import fix_v3_color, run_audit
from lightai.rig.model import Rig
from lightai.rig.qxw import Workspace


def test_audit_finds_v3_problems_and_fix_clears_them(cfg):
    rig = Rig.load(cfg)
    rep = run_audit(rig)
    assert rep["errors"] == []
    assert len(rep["scene_color_channel_problems"]) == 22
    assert len(rep["channel_group_problems"]) == 6
    changes = fix_v3_color(rig)
    skipped = {(c["function_id"], c["fixture_id"]) for c in changes if "skipped" in c}
    assert len(changes) - len(skipped) >= 20
    from lightai.compiler.writer import write_workspace

    write_workspace(rig.ws, cfg.project_path, cfg.project_path.parent / "backups")
    after = run_audit(Rig.load(cfg))
    left = {(p["function_id"], p["fixture_id"]) for p in after["scene_color_channel_problems"]}
    assert left == skipped, "only scenes whose color the V3 wheel doesn't have may remain"
    assert after["channel_group_problems"] == []
    assert any(d["value"] == 72 and d["color"] == "cyan" for d in rep["wheel_slots_from_function_names"])
    assert not any(d["value"] == 0 for d in rep["wheel_slots_from_function_names"]), "hidden scenes are saved as zeros; never evidence"
    ws = Workspace.load(cfg.project_path)
    red = next(f for f in ws.functions() if f.id == 1)
    assert dict(red.values[34])[8] == 8, "V3 red goes to ch8 with the V3 wheel value"


def test_mining_and_transplant(cfg, tmp_path, monkeypatch):
    import lightai.mine as mine
    from lightai.mine.transplant import abstract_function

    monkeypatch.setattr(mine, "PRIORS_PATH", tmp_path / "priors.yaml")
    assert mine.mine_local(cfg, [str(REPO_ROOT / "SaveFile")]) == 0
    assert mine.mine_stats(cfg) == 0
    text = (tmp_path / "priors.yaml").read_text()
    assert "efx:" in text and "gear_gap" in text
    src = mine.foreign_rig(REPO_ROOT / "SaveFile" / "Preview Shows - Spots and Washes.qxw", cfg)
    idea = abstract_function(src, 117)
    assert idea["recipe"] == "circle_wave" and len(idea["colors"]) >= 3


def test_taxonomy_small(cfg, monkeypatch):
    import lightai.mine as mine

    cfg.fixture_dirs = [REPO_ROOT / "Fixtures"]
    assert mine.mine_taxonomy(cfg) == 0
    tax = json.loads((cfg.data_dir / "fixture-taxonomy.json").read_text())
    assert tax["definitions"] >= 5 and "Moving Head" in tax["types"]


def test_nightly_skips_training_without_new_examples(cfg, monkeypatch):
    import lightai.tasks as tasks

    monkeypatch.setattr(tasks, "load_config", lambda: cfg)
    called = {}
    import lightai.train.trainer as trainer

    monkeypatch.setattr(trainer, "train_main", lambda a: called.setdefault("train", 0))
    import lightai.rig.audit as audit

    monkeypatch.setattr(audit, "audit_main", lambda a: 0)
    assert tasks.new_examples_since(cfg, "20000101T000000Z") == 0
    (cfg.data_dir / "corrections.jsonl").write_text(json.dumps({"ts": "2030-01-01T00:00:00+00:00", "accepted": True,
                                                                "corrected": {"intent": "blackout", "marked": "lights out"}}) + "\n")
    assert tasks.new_examples_since(cfg, "20260101T000000Z") == 1


def test_validate_message_guards():
    with pytest.raises(QlcError):
        validate_message("QLC+CMD|opMode")
    with pytest.raises(QlcError):
        validate_message("QLC+API|getWidgetSubIdList|3")
    with pytest.raises(QlcError):
        validate_message("QLC+API|setFunctionStatus|3")
    with pytest.raises(QlcError):
        validate_message("CH|12")
    validate_message("QLC+API|setFunctionStatus|3|1")
    lat = Latency()
    assert lat.summary()["p50_ms"] is None
    for v in range(1, 101):
        lat.add(v)
    assert lat.summary()["p95_ms"] >= 94
    with pytest.raises(QlcError):
        QlcClient.abs_address(0, 1)
    assert QlcClient.abs_address(2, 500) == 1012


def test_client_against_fake():
    fake = FakeQlc(MAIN, fork=False).start()
    try:
        async def run():
            c = QlcClient(url="auto", host="127.0.0.1", port=fake.port, timeout=1.0)
            await c.connect()
            try:
                assert c.connected_url.startswith("ws://"), "auto falls back from wss to ws"
                assert await c.functions_number() == 223
                funcs = await c.functions()
                assert funcs[300] == "Default Scene Club 1"
                assert await c.function_status(300) == "Stopped"
                pushes = []
                c.subscribe(pushes.append)
                await c.set_function(300, True)
                await c.barrier()
                assert await c.function_status(300) == "Running"
                await asyncio.sleep(0.05)
                assert "FUNCTION|300|Running" in pushes
                await c.set_channel(2, 10, 77)
                vals = await c.channel_values(2, 10, 1)
                assert vals[0]["value"] == 77 and vals[0]["override"]
                await c.reset_channel(2, 10)
                assert not (await c.channel_values(2, 10, 1))[0]["override"]
                with pytest.raises(QlcError):
                    await c.request("QLC+API|getFunctionType|1", reply_prefix="NEVER|", timeout=0.2)
            finally:
                await c.close()

        asyncio.run(run())
    finally:
        fake.stop()


def test_connect_failure_is_clean():
    async def run():
        c = QlcClient(url="auto", host="127.0.0.1", port=1, timeout=0.5)
        with pytest.raises(QlcError):
            await c.connect()

    asyncio.run(run())


def test_cli_parse_and_facts(cfg, capsys, monkeypatch):
    from lightai import __main__ as cli

    monkeypatch.setattr("lightai.config.load_config", lambda path=None: cfg)
    assert cli.main(["facts", "list"]) == 0
    model = __import__("lightai.config", fromlist=["x"]).Config().current_model_dir()
    if model is None:
        pytest.skip("no model")
    assert cli.main(["parse", "washes at 40%", "--no-embeddings"]) == 0
    out = capsys.readouterr().out
    assert '"intent": "set_level"' in out
