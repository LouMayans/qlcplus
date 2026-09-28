"""End-to-end against a separate, sanitized QLC+ instance (never the club's live one).

Opt in with:  set LIGHTAI_E2E=1  (it starts C:\\qlcplus\\qlcplus.exe on port 9998 with a copy of the
dedicated test project (lightai-test-rig.qxw: the Blank Rig Template with its DMX inputs/outputs
removed), so nothing reaches the rig and the real show is never loaded. QLC+'s registry settings
(recent files) are snapshotted and restored around the instance.
"""

import asyncio
import copy
import os

from pathlib import Path

import pytest

from lightai.config import load_config
from lightai.devtools import QlcInstance, make_test_project
from lightai.exec.http import fork_version

pytestmark = pytest.mark.skipif(os.environ.get("LIGHTAI_E2E") != "1", reason="set LIGHTAI_E2E=1 to run against an isolated QLC+")


FORK_EXE = Path(r"C:\lightai-data\qlcplus-fork\qlcplus.exe")
BUILDS = [("installed", Path(r"C:\qlcplus\qlcplus.exe"))] + ([("fork", FORK_EXE)] if FORK_EXE.exists() else [])


@pytest.mark.parametrize("build,exe", BUILDS)
def test_create_rotate_propose_live(tmp_path, build, exe):
    from lightai.app import LightAI

    base = load_config()
    cfg = copy.copy(base)
    cfg.data_dir = tmp_path / "data"
    cfg.data_dir.mkdir()
    cfg.learned_path = tmp_path / "learned.yaml"
    cfg.project_path = make_test_project(tmp_path / "test-project")
    cfg.qlc_url = "ws://127.0.0.1:9998/qlcplusWS"
    cfg.reload_strategy = "auto"

    async def run():
        with QlcInstance(cfg.project_path, port=9998, exe=exe):
            ai = LightAI(cfg, model_dir=base.current_model_dir(), use_embeddings=False)
            cmd, plan = ai.plan("slow blue wash breathing at 60 BPM")
            res = await ai.executor.execute(plan, confirm=True)
            assert res["ok"], res
            reload = next(r for r in res["results"] if r["op"] == "reload")
            assert reload["loaded"] and reload["look_visible"]
            main_id = plan.look["main_id"]
            c = ai.executor.client
            is_fork = await fork_version(c) > 0  # C:\qlcplus holds the fork build once install-fork-build.ps1 ran
            assert reload["strategy"] == ("loadProjectFile" if is_fork else "post_loadProject")
            await c.set_function(main_id, True)
            await asyncio.sleep(0.5)
            assert await c.function_status(main_id) == "Running"
            await c.set_function(main_id, False)

            cmd, plan = ai.plan("fixture 3 needs to be rotated 90 degrees")
            res = await ai.executor.execute(plan, confirm=True, allow_running_reload=True)
            assert res["ok"], res
            assert ai.rig.ws.monitor_items()[3]["rotation"] == 180

            cmd, plan = ai.plan("make an assumption on fixture 5 and make it move")
            plan.actions[0].args["seconds"] = 1.0  # frames are simulated when the preview is played
            res = await ai.executor.execute(plan, wait_preview=True)
            assert res["ok"], res
            vals = await c.channel_values(1, 1, 300)
            assert not any(v["override"] for v in vals), "preview must release every Simple Desk override"
            await c.close()

    asyncio.run(run())
