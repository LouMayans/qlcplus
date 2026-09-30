"""Live check of the show designer on an isolated QLC+ (the dev build with the 3D stage, port 9994, a sanitized copy
of the rig without DMX outputs; never port 9999):

1. aim every spot at the DJ and check, from the real DMX output, that each beam passes within 2 ft of the DJ;
2. apply a designed show, play it, and show it in the 3D stage in a visible, maximized Chrome (tabs visible) window.

    python tools/check_design_live.py                     # canned design, no Claude usage
    python tools/check_design_live.py --real "Can you create 3 different shows ..."   # a real Claude design run
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

from lightai.config import load_config
from lightai.devtools import QlcInstance, make_test_project

DEV_EXE = Path(r"C:/qlcplus-dev/qlcplus.exe")
CHROME = Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe")
TMP = Path(r"C:/lightai-data/tmp/design-live")
PORT = 9994

CANNED = {"shows": [{"title": "Dream Strobe", "bpm": 126, "sections": [
    {"name": "Morph", "bars": 8, "transition_fade_ms": 3000, "looks": [
        {"layer": "base", "recipe": "color_morph", "targets": ["washes", "pars", "led walls"],
         "colors": ["lavender", "pink", "cyan"], "rate": "slow", "order": "left_to_right"},
        {"layer": "movement", "recipe": "circle_wave", "targets": ["spots"], "colors": ["lavender"], "rate": "slow",
         "size": "small", "order": "center_out"}]},
    {"name": "Flutter", "bars": 8, "transition_fade_ms": 500, "looks": [
        {"layer": "strobe", "recipe": "strobe_burst", "targets": ["spots"], "colors": ["white", "lavender"], "rate": "fast"},
        {"layer": "fx", "recipe": "strobe_chase", "targets": ["pars"], "colors": ["pink"], "order": "circular"},
        {"layer": "position", "recipe": "position", "targets": ["spots"], "aim": "dance floor", "spread": "fan"}]}]}],
    "notes": "canned check design"}


def channel_values(reply: str) -> list:
    """QLC+API|getChannelsValues|<n>|<value>|<type>|<override>|... -> [values] (four fields per channel)."""
    parts = reply.split("|")[2:]
    return [int(parts[i + 1]) for i in range(0, len(parts) - 1, 4) if parts[i + 1].lstrip("-").isdigit()]


async def aim_check(ai, results: list) -> None:
    from lightai.compiler import compile_look
    from lightai.compiler.spec import LookParams
    from lightai.rig.stage import dmx_to_degrees, hit_point
    from lightai.schema import Action, Plan

    rig, cfg = ai.get_rig(), ai.cfg
    spots = rig.zones.get("spots") or []
    params = LookParams(recipe="position", targets=spots, aim="dj", colors=["white"], name="Check - spots on the DJ")
    look = compile_look(rig, params)
    plan = Plan(plan_id="aim-check", intent="create_look", mode="structural", summary="aim", show=str(cfg.project_path),
                actions=[Action(op="write_look", args={"params": params.to_dict(), "expected_ids": look.ids}),
                         Action(op="reload", args={"strategy": "auto"})])
    res = await ai.executor.execute(plan, confirm=True, allow_running_reload=True)
    results.append(("aim look written and loaded", bool(res.get("ok"))))
    c = await ai.executor.ensure_client()
    rig = ai.get_rig()
    main = next(e for e in __import__("lightai.compiler.sidecar", fromlist=["Sidecar"]).Sidecar(cfg.sidecar_path).looks.values()
                if e["name"].startswith("Check - spots on the DJ"))
    await c.set_function(main["main_id"], True)
    await asyncio.sleep(1.5)
    st = rig.stage
    target = st.target_point("dj")
    misses = []
    for fid in spots:
        fx = rig.fixtures[fid]
        vals = channel_values(await c.request(f"QLC+API|getChannelsValues|{fx.universe + 1}|{fx.address + 1}|{fx.channels}", timeout=3.0))
        if len(vals) < fx.channels:
            misses.append((fx.name, "no values"))
            continue
        pan16 = vals[fx.ch("pan")] * 256 + (vals[fx.ch("pan_fine")] if fx.has("pan_fine") else vals[fx.ch("pan")])
        tilt16 = vals[fx.ch("tilt")] * 256 + (vals[fx.ch("tilt_fine")] if fx.has("tilt_fine") else vals[fx.ch("tilt")])
        pan_max, tilt_max = st.pan_tilt_range(fx)
        e = st.fixtures[fid]
        p, t = dmx_to_degrees(pan16, tilt16, pan_max, tilt_max, e)
        hp = hit_point(e, p, t, target)
        miss = math.dist(hp, target) if hp else float("inf")
        misses.append((fx.name, round(miss, 1)))
    worst = max((m for _, m in misses if isinstance(m, float)), default=float("inf"))
    print("aim misses (inches):", misses)
    results.append((f"every spot's beam passes within 2 ft of the DJ (worst {worst:.1f} in)", worst <= 24.0))
    await c.set_function(main["main_id"], False)


async def design_check(ai, design: dict, results: list, watch_s: float) -> None:
    from lightai.design.spec import DesignResult
    from lightai.design.validate import validate_design
    from lightai.schema import Action, Plan

    cfg = ai.cfg
    errors, _, _ = validate_design(ai.get_rig(), DesignResult.model_validate(design))
    results.append(("design valid for the rig", not errors))
    if errors:
        print("design errors:", errors)
        return
    plan = Plan(plan_id="design-check", intent="design_show", mode="structural", summary="design", show=str(cfg.project_path),
                actions=[Action(op="write_design", args={"design": design, "job_id": "live-check"}),
                         Action(op="reload", args={"strategy": "auto"})])
    res = await ai.executor.execute(plan, confirm=True, allow_running_reload=True)
    results.append(("design written and loaded into QLC+", bool(res.get("ok"))))
    if not res.get("ok"):
        print(json.dumps(res, indent=1, default=str)[:2000])
        return
    shows = next(r for r in res["results"] if r["op"] == "write_design")["shows"]
    c = await ai.executor.ensure_client()
    chrome = subprocess.Popen([str(CHROME), "--start-maximized", f"--user-data-dir={TMP / 'chrome'}", "--no-first-run",
                               "--no-default-browser-check", "--new-window", f"http://127.0.0.1:{PORT}/stage"])
    try:
        for s in shows:
            await c.set_function(s["main_id"], True)
            await asyncio.sleep(2.0)
            status = await c.function_status(s["main_id"])
            results.append((f"show '{s['title']}' runs in QLC+", status == "Running"))
            print(f"playing '{s['title']}' for {watch_s:.0f} s - watch it in the 3D stage")
            await asyncio.sleep(watch_s)
            await c.set_function(s["main_id"], False)
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(chrome.pid)], capture_output=True)


async def real_design(ai, text: str, results: list) -> dict:
    from lightai.design.backend import ClaudeCodeBackend
    from lightai.design.jobs import JobStore, parse_request, run_design_job
    from lightai.design.runlog import RunLog

    runlog = RunLog(Path(ai.cfg.data_dir))
    store = JobStore(Path(ai.cfg.data_dir))
    count, minutes = parse_request(text)
    job = store.new(text, count, minutes)
    print(f"asking Claude for {count} show(s)...")
    await run_design_job(ai, job, store, ClaudeCodeBackend(ai.cfg, runlog))
    for p in job.progress:
        print("  -", p["text"])
    results.append((f"real Claude design: {job.status}", job.status == "done"))
    for r in runlog.runs(limit=5):
        print("run:", {k: r.get(k) for k in ("kind", "model", "status", "duration_ms", "num_turns", "cost_usd", "usage", "limits")})
    if job.status != "done":
        print("errors:", job.errors)
        return {}
    print(json.dumps(job.result, indent=1)[:4000])
    return job.result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", help="design request to send to Claude (uses the subscription)")
    ap.add_argument("--watch", type=float, default=25.0, help="seconds to play each show")
    ap.add_argument("--skip-aim", action="store_true")
    args = ap.parse_args()
    from lightai.app import LightAI

    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    show = make_test_project(TMP / "show")
    main_cfg = load_config()
    stage = main_cfg.project_path.with_name(main_cfg.project_path.stem + ".stage.json")
    shutil.copy(stage, show.with_name(show.stem + ".stage.json"))
    cfg = copy.copy(main_cfg)
    cfg.project_path, cfg.main_project_path = show, None
    cfg.data_dir = TMP / "data"
    cfg.data_dir.mkdir(parents=True)
    cfg.learned_path = TMP / "learned.yaml"
    cfg.qlc_url = f"ws://127.0.0.1:{PORT}/qlcplusWS"
    cfg.reload_strategy = "auto"
    results: list = []
    inst = QlcInstance(show, port=PORT, exe=DEV_EXE).start()
    try:
        time.sleep(1.5)
        ai = LightAI(cfg, model_dir=main_cfg.current_model_dir(), use_embeddings=False)
        loop = asyncio.new_event_loop()
        try:
            if not args.skip_aim:
                loop.run_until_complete(aim_check(ai, results))
            design = loop.run_until_complete(real_design(ai, args.real, results)) if args.real else CANNED
            if design:
                loop.run_until_complete(design_check(ai, design, results, args.watch))
            loop.run_until_complete(ai.executor.client.close())
        finally:
            loop.close()
    finally:
        inst.stop()
    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    return 0 if results and all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
