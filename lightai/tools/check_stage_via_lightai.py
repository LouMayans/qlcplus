"""Visible check of the 3D stage THROUGH lightai, the way the operator uses it (never port 9999, never headless):

An isolated copy of the main show and its stage file runs in a QLC+ build (default: the installed C:\\qlcplus) on port
9994; a lightai server on port 8766 points at it; a maximized Chrome (tabs visible) opens the console, presses '3D Stage' (the
stage page comes through lightai's proxy and must load the show's own stage file), then types a scene command into the
console and applies it: the 3D tab must show the change by itself, with no refresh. Screenshots of both tabs.

    python tools/check_stage_via_lightai.py [--exe C:\\qlcplus-dev\\qlcplus.exe]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

LIGHTAI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIGHTAI))

from lightai.config import load_config  # noqa: E402
from lightai.devtools import QlcInstance, make_test_project  # noqa: E402
from lightai.rig.stage import stage_path  # noqa: E402

TMP = Path(r"C:/lightai-data/tmp/stage-via-lightai")
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
CDP_PORT, QLC_PORT, AI_PORT = 9226, 9994, 8766
COMMAND = "move table 3 with its chairs a foot to the right from the stage"


def pages() -> list:
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2) as r:
        return [t for t in json.loads(r.read()) if t.get("type") == "page"]


class CDP:
    def __init__(self, ws) -> None:
        self.ws, self.n = ws, 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result") or {}

    async def ev(self, expr: str):
        r = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True, "userGesture": True})
        return (r.get("result") or {}).get("value")

    async def shot(self, path: Path) -> None:
        path.write_bytes(base64.b64decode((await self.call("Page.captureScreenshot", {"format": "png"}))["data"]))


async def attach(pred, timeout: float = 30) -> CDP:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            for t in pages():
                if pred(t):
                    return CDP(await websockets.connect(t["webSocketDebuggerUrl"], max_size=None))
        except OSError:
            pass
        await asyncio.sleep(0.4)
    raise RuntimeError("tab not found")


async def wait(cdp: CDP, expr: str, timeout: float = 30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = await cdp.ev(expr)
        if v:
            return v
        await asyncio.sleep(0.4)
    return None


TABLE3 = ("(function(){var d=window.__stage&&window.__stage.getDraft();if(!d)return null;"
          "var o=(d.objects||[]).find(function(x){return x.name==='Cocktail table 3'});return o?o.pos[0]:null})()")


async def run(results: list, shots: Path) -> None:
    con = await attach(lambda t: t["url"].rstrip("/").endswith(f":{AI_PORT}"))
    results.append(("console loaded", bool(await wait(con, "!!document.getElementById('stagelink')"))))
    await con.ev("document.querySelector('#stagelink button').click()")
    st = await attach(lambda t: "/stage" in t["url"])
    x0 = await wait(st, TABLE3, 40)
    results.append(("3D Stage (through lightai) opened the show's own stage file", x0 is not None))
    await st.ev("window.__stage.camera('top')")
    await asyncio.sleep(3)
    await st.shot(shots / "1-stage-before.png")
    # the operator's command, typed into the console and applied
    await con.call("Page.bringToFront")
    await con.ev(f"document.getElementById('cmd').value = {json.dumps(COMMAND)}; document.getElementById('go').click(); true")
    summary = await wait(con, "(function(){var s=document.getElementById('summary');var a=document.getElementById('apply');"
                              "return s && a && !a.classList.contains('hidden') ? s.textContent : ''})()", 20)
    results.append((f"the console planned it: {summary!r}", bool(summary) and "3D stage" in (summary or "")))
    await con.ev("document.getElementById('apply').click(); true")
    done = await wait(con, "(function(){var r=document.getElementById('result');return r && /done/.test(r.textContent) ? r.textContent : ''})()", 20)
    results.append(("applied from the console", bool(done)))
    await con.shot(shots / "2-console-after.png")
    x1 = await wait(st, f"(function(){{var x={TABLE3};return x!==null && Math.abs(x-({x0}+12))<0.1 ? x : null}})()", 15)
    results.append(("the open 3D tab moved table 3 by itself (no refresh)", x1 is not None))
    await st.call("Page.bringToFront")
    await st.ev("window.__stage.select('object', (window.__stage.getDraft().objects.find(function(o){return o.name==='Cocktail table 3'})||{}).id)")
    await asyncio.sleep(2.5)
    await st.shot(shots / "3-stage-after.png")
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
        # the server edits the COPY (the show QLC+ has open, so saves go through QLC+ and the 3D tab hears of them) and
        # keeps its own data folder (the operator's history, which the teacher reads, stays clean); the model is copied in
        main = load_config()
        data = TMP / "data"
        current = main.current_model_dir()
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
        asyncio.run(run(results, shots))
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
