r"""Real-QLC+ check of reloads, previews, calibration, priority warnings and live latency (isolated: port 9994, fresh
test project, never port 9999). Usage: C:\lightai-env\venv\Scripts\python tools\check_live_fork.py"""
import asyncio
import copy
import json
import shutil
import statistics
import time
from pathlib import Path

from lightai.app import LightAI
from lightai.config import load_config
from lightai.devtools import QlcInstance, make_test_project

TMP = Path(r"C:/lightai-data/tmp/final-check")
EXE = Path(r"C:/lightai-data/qlcplus-fork/qlcplus.exe")
PORT = 9994
out: dict = {"checks": {}, "latency_ms": {}}


def check(name, ok, detail=""):
    out["checks"][name] = {"ok": bool(ok), "detail": detail}
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


async def overrides(c) -> int:
    n = 0
    for u in (1, 2):
        n += sum(1 for r in await c.channel_values(u, 1, 512) if r["override"])
    return n


async def run(ai: LightAI) -> None:
    ex = ai.executor

    async def do(text, **kw):
        cmd, plan = ai.plan(text)
        res = await ex.execute(plan, **kw)
        return cmd, plan, res

    c = await ex.ensure_client()
    t0 = time.perf_counter()
    _, plan, res = await do("slow blue wash breathing at 60 bpm", confirm=True, allow_running_reload=True)
    out["latency_ms"]["create_look_write_and_reload"] = round((time.perf_counter() - t0) * 1000, 1)
    check("create look", res["ok"], plan.look.get("name"))
    main = plan.look["main_id"]
    name = plan.look["name"]
    parts = ai.rig.functions[main].refs

    # N1: after a confirmed reload only the look itself is restarted, and stopping it by name stops every part
    _, _, res = await do(f"start {name}")
    await asyncio.sleep(0.4)
    _, _, res = await do("rename fixture 22 to Bar Wall", confirm=True, allow_running_reload=True)
    rl = next((r for r in res["results"] if r["op"] == "reload"), {})
    check("N1 reload restarts only the look", rl.get("restarted") == [main], f"restarted={rl.get('restarted')}")
    await asyncio.sleep(0.4)
    _, _, res = await do(f"stop {name}", confirm=True)
    await asyncio.sleep(0.5)
    still = [f for f in [main] + list(parts) if await c.function_status(f) == "Running"]
    check("N1 stop by name stops every part", not still, f"still running={still}")

    # N2: a live retime marks QLC+ modified; a blocked reload is loaded on the confirmed retry
    await do(f"start {name}")
    await asyncio.sleep(0.3)
    _, _, res = await do("set the bpm to 128")
    await do("stop everything", confirm=True)
    await asyncio.sleep(0.3)
    _, plan2, res1 = await do("red wash on the spots", confirm=True)
    blocked = any(r.get("blocked") for r in res1.get("results", []))
    _, plan3, res2 = await do("red wash on the spots", confirm=True, allow_running_reload=True)
    funcs = await c.functions()
    present = any(v == plan3.look["name"] for v in funcs.values())
    check("N2 look loaded after a blocked reload", res2["ok"] and present, f"first blocked={blocked}, in QLC+={present}")

    # N10: release everything with nothing overridden sends no whole-universe reset and is fast
    ts = []
    for _ in range(5):
        t0 = time.perf_counter()
        _, _, res = await do("release everything")
        ts.append((time.perf_counter() - t0) * 1000)
    out["latency_ms"]["release_everything_p50"] = round(statistics.median(ts), 1)
    check("N10 release everything is fast", statistics.median(ts) < 100, f"p50 {statistics.median(ts):.1f} ms")

    # previews release everything, including three at once
    _, plan, _ = (lambda r: r)(await do("make an assumption on fixture 5 and make it move"))  # starts a preview
    await ex.stop_preview()
    plans = [ai.plan(t)[1] for t in ("make an assumption on the washes", "make an assumption on fixture 5 and make it move",
                                     "make an assumption on the spots")]
    for p in plans:
        for a in p.actions:
            if a.op == "preview":
                a.args["seconds"] = 1.0
    await asyncio.gather(*(ex.execute(p) for p in plans))
    await asyncio.sleep(0.3)
    await ex.stop_preview()
    await asyncio.sleep(0.3)
    n = await overrides(c)
    check("three concurrent previews leave nothing overridden", n == 0, f"{n} overridden")

    # API D2: a second calibration ends the first
    await do("find blue on fixture 34", confirm=True)
    await do("find red on fixture 35", confirm=True)
    await ex.calibrate_end("final check")
    await asyncio.sleep(0.2)
    n = await overrides(c)
    check("second calibration replaces the first", n == 0, f"{n} overridden")

    # N8: a running priority look that holds the channels is named when setting levels
    _, kplan, res = await do("create a kill for the washes at p100", confirm=True, allow_running_reload=True)
    kill = (kplan.look or {}).get("main_id")
    if kill is not None and res.get("ok"):
        await c.set_function(kill, True)
        await asyncio.sleep(0.4)
        _, _, res = await do("washes at 40%", confirm=True)
        sc = next((r for r in res.get("results", []) if r.get("op") == "set_channels"), {})
        check("N8 priority look named on manual levels", bool(sc.get("held_by")), sc.get("warning", "")[:90])
        await c.set_function(kill, False)
        await do("release everything")
    else:
        check("N8 priority look named on manual levels", False, f"kill look not created: {kplan.summary}")

    # live latency (plan + execute), n=15 each
    for text in ("washes at 40%", "set channel 9 on fixture 34 to 128", "blackout", "lights back on", "release the washes", "what's running"):
        ts = []
        for _ in range(15):
            t0 = time.perf_counter()
            cmd, plan = ai.plan(text)
            await ex.execute(plan, confirm=True)
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        out["latency_ms"][text] = {"p50": round(statistics.median(ts), 1), "p95": round(ts[int(0.95 * len(ts)) - 1], 1)}
    await do("release everything")
    await c.close()


def main() -> None:
    if TMP.exists():
        shutil.rmtree(TMP)
    cfg = copy.copy(load_config())
    cfg.data_dir = TMP / "data"
    cfg.data_dir.mkdir(parents=True)
    cfg.learned_path = TMP / "learned.yaml"
    cfg.project_path = make_test_project(TMP / "proj")
    cfg.qlc_url = f"ws://127.0.0.1:{PORT}/qlcplusWS"
    cfg.reload_strategy = "auto"
    inst = QlcInstance(cfg.project_path, port=PORT, exe=EXE).start()
    try:
        time.sleep(1.5)
        ai = LightAI(cfg, model_dir=load_config().current_model_dir(), use_embeddings=False)
        asyncio.run(run(ai))
    finally:
        inst.stop()
    (TMP / "result.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out["latency_ms"], indent=1))
    print("passed", sum(1 for v in out["checks"].values() if v["ok"]), "of", len(out["checks"]))


if __name__ == "__main__":
    main()
