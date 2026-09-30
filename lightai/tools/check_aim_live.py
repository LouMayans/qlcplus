"""Visible check of live aiming ('triangulating') through lightai, the way the operator does it (never port 9999):

An isolated copy of the show and its 3D stage runs in the installed QLC+ on port 9994 with a lightai server on 8766.
In a maximized Chrome (tabs visible) the console gets, one after another, the operator's own sentences (typed and run, like a
person would), then the 3D Stage tab shows the beams from QLC+'s live DMX. The check reads the real DMX back from
QLC+ and follows every beam down to the floor with the 3D stage's maths: they must all land on spot 2's floor spot.

    python tools/check_aim_live.py [--exe C:\\qlcplus-dev\\qlcplus.exe]
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LIGHTAI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIGHTAI))
sys.path.insert(0, str(LIGHTAI / "tools"))

from check_stage_via_lightai import CDP_PORT, CHROME, attach, wait  # noqa: E402
from lightai.config import load_config  # noqa: E402
from lightai.devtools import QlcInstance, make_test_project  # noqa: E402
from lightai.rig.stage import stage_path  # noqa: E402

TMP = Path(r"C:/lightai-data/tmp/aim-live")
QLC_PORT, AI_PORT = 9994, 8766
SENTENCES = ["place spot 2 white beam no flashing on straight down",
             "lets say all beams point to spot 2 beam that ends on the floor so they all overlap"]


async def say(con, text: str) -> str:
    """Type a command into the console, parse it, run it; returns the plan summary."""
    before = await con.ev("(document.getElementById('planid')||{}).textContent || ''")
    await con.ev("document.getElementById('result').innerHTML = ''; true")
    await con.ev(f"document.getElementById('cmd').value = {json.dumps(text)}; document.getElementById('go').click(); true")
    summary = await wait(con, "(function(){var p=document.getElementById('planid'), s=document.getElementById('summary'),"
                              "a=document.getElementById('apply'); return p && p.textContent !== " + json.dumps(before)
                              + " && a && !a.classList.contains('hidden') ? s.textContent : ''})()", 20)
    await con.ev("document.getElementById('apply').click(); true")
    await wait(con, "(function(){var r=document.getElementById('result');return r && /done|not done/.test(r.textContent) ? r.textContent : ''})()", 20)
    return summary or ""


async def floor_spots(show: Path) -> dict:
    """Every beam's floor spot, from the DMX QLC+ outputs now."""
    from lightai.exec.wsclient import QlcClient
    from lightai.rig import aim_live
    from lightai.rig.model import Rig

    cfg = copy.copy(load_config())
    cfg.project_path, cfg.main_project_path = show, None
    rig = Rig.load(cfg)
    out = {}
    client = QlcClient(url="auto", host="127.0.0.1", port=QLC_PORT)
    async with client:
        for fid, fx in rig.fixtures.items():
            if fx.has("pan") and fx.has("tilt") and rig.stage.fixtures.get(fid):
                p16, t16 = await aim_live.read_pan_tilt(client, fx)
                out[fid] = (fx.name, aim_live.beam_floor_point(rig.stage, fx, p16, t16))
        await client.close()
    return out


async def run(results: list, shots: Path, show: Path) -> None:
    con = await attach(lambda t: t["url"].rstrip("/").endswith(f":{AI_PORT}"))
    results.append(("console loaded", bool(await wait(con, "!!document.getElementById('stagelink')"))))
    for text in SENTENCES:
        summary = await say(con, text)
        print(f"  '{text}'\n    -> {summary}")
        results.append((f"'{text}' ran as a live aim", "(live)" in summary))
        await asyncio.sleep(1.0)
    await con.shot(shots / "1-console.png")
    spots = await floor_spots(show)
    ref = next((v[1] for v in spots.values() if v[0] == "BEAM230 #2"), None)
    lands = {n: p for n, p in spots.values() if p is not None}
    worst = max((((p[0] - ref[0]) ** 2 + (p[1] - ref[1]) ** 2) ** 0.5 for p in lands.values()), default=None) if ref else None
    results.append((f"every beam lands on spot 2's floor spot (worst {worst:.2f} in, {len(lands)} beams, from QLC+'s DMX)" if worst is not None
                    else "every beam lands on spot 2's floor spot", worst is not None and worst < 1.0 and len(lands) >= 10))
    await con.ev("document.querySelector('#stagelink button').click()")
    st = await attach(lambda t: "/stage" in t["url"])
    await wait(st, "!!(window.__stage && window.__stage.getDraft())", 40)
    await wait(st, "!/Preparing lights/.test(document.body.innerText)", 90)  # the page builds its lights first
    await st.ev("window.__stage.camera('foh')")
    await asyncio.sleep(5)
    await st.shot(shots / "2-stage-beams.png")
    await st.ev("window.__stage.camera('top')")
    await asyncio.sleep(3)
    await st.shot(shots / "3-stage-top.png")
    for c in (con, st):
        await c.ws.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=r"C:\qlcplus\qlcplus.exe")
    args = ap.parse_args()
    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    show = make_test_project(TMP / "show")
    shutil.copy(stage_path(load_config().project_path), stage_path(show))
    shots = TMP / "shots"
    shots.mkdir(parents=True)
    results: list = []
    qlc = QlcInstance(show, port=QLC_PORT, exe=Path(args.exe)).start()
    server = chrome = None
    try:
        main_cfg = load_config()
        data = TMP / "data"
        current = main_cfg.current_model_dir()
        shutil.copytree(current, data / "models" / current.name)
        (data / "models" / "current.txt").write_text(current.name, encoding="utf-8")
        env = dict(os.environ, LIGHTAI_PROJECT=str(show), LIGHTAI_DATA=str(data))
        server = subprocess.Popen([r"C:\lightai-env\venv\Scripts\python.exe", "-m", "lightai", "serve", "--port", str(AI_PORT),
                                   "--qlc-port", str(QLC_PORT), "--no-embeddings"], cwd=str(LIGHTAI), env=env,
                                  stdout=open(TMP / "server.log", "w"), stderr=subprocess.STDOUT)
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{AI_PORT}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        chrome = subprocess.Popen([CHROME, "--start-maximized", f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={TMP / 'chrome'}",
                                   "--no-first-run", "--no-default-browser-check", f"http://127.0.0.1:{AI_PORT}/"])
        asyncio.run(run(results, shots, show))
        time.sleep(2)
    finally:
        if chrome:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(chrome.pid)], capture_output=True)
        if server:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(server.pid)], capture_output=True)
        qlc.stop()
    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    print("screenshots:", shots)
    return 0 if results and all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
