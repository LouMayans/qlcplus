"""The 3D sandbox preview against a real QLC+ (opt in with LIGHTAI_E2E=1): a design is played on a sanitized copy of
a copy of the main show in a separate QLC+ on the sandbox port; the real show file is never written."""

import asyncio
import copy
import os
import shutil
from pathlib import Path

import pytest

from lightai.config import load_config
from lightai.devtools import port_open

pytestmark = pytest.mark.skipif(os.environ.get("LIGHTAI_E2E") != "1", reason="set LIGHTAI_E2E=1 to run against an isolated QLC+")

DESIGN = {"shows": [{"title": "Sandbox Check", "bpm": 124, "sections": [
    {"name": "A", "bars": 4, "looks": [{"layer": "base", "recipe": "color_wash", "targets": ["washes"], "colors": ["blue"]}]},
    {"name": "B", "bars": 4, "looks": [{"layer": "base", "recipe": "color_chase", "targets": ["pars"], "colors": ["pink", "cyan"]}]}]}]}


def test_sandbox_plays_a_design_without_touching_the_show(tmp_path):
    from lightai.app import LightAI
    from lightai.design.sandbox import Sandbox, sandbox_exe
    from lightai.exec.wsclient import QlcClient

    base = load_config()
    show = tmp_path / "show" / base.project_path.name
    show.parent.mkdir()
    shutil.copy(base.project_path, show)
    stage = base.project_path.with_name(base.project_path.stem + ".stage.json")
    if stage.exists():
        shutil.copy(stage, show.with_name(show.stem + ".stage.json"))
    cfg = copy.copy(base)
    cfg.project_path, cfg.main_project_path = show, None
    cfg.data_dir = tmp_path / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp_path / "learned.yaml"
    cfg.sandbox_port = 9996
    if not sandbox_exe(cfg).exists():
        pytest.skip("no QLC+ build for the sandbox")
    before = show.read_bytes()
    ai = LightAI(cfg, model_dir=base.current_model_dir(), use_embeddings=False)
    sb = Sandbox(cfg)

    async def run():
        res = await sb.preview(ai, DESIGN)
        assert res["ok"] and res["url"].endswith(":9996/stage") and res["shows"][0]["title"] == "Sandbox Check"
        c = QlcClient("ws://127.0.0.1:9996/qlcplusWS", timeout=5.0)
        await c.connect()
        try:
            await asyncio.sleep(1.0)
            assert await c.function_status(res["shows"][0]["main_id"]) == "Running"
        finally:
            await c.close()
        await sb.stop()

    asyncio.run(run())
    assert not port_open(9996), "the sandbox QLC+ must be gone"
    assert show.read_bytes() == before, "the show itself is never written"
    assert (cfg.data_dir / "sandbox" / show.name).exists()
