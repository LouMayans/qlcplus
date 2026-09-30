"""Live check of editing the 3D stage by voice, in a visible maximized Chrome (tabs visible) (never headless, never port 9999).

An isolated copy of the main show and its 3D stage runs in the dev QLC+ (the build with the 3D stage) on port 9994.
Chrome shows its /stage page from above; each spoken command goes through lightai (the language model, the planner
and the executor, which saves through QLC+'s saveStage), and the check waits until the OPEN PAGE shows the change
(window.__stage.getDraft()), highlights what changed and takes a screenshot.

    python tools/check_scene_live.py                    # the promoted model
    python tools/check_scene_live.py --model <dir>      # another model directory
    python tools/check_scene_live.py --standin          # hand-written model answers (tests the rest of the chain)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from lightai.config import load_config  # noqa: E402
from lightai.devtools import QlcInstance, make_test_project  # noqa: E402
from lightai.rig.stage import stage_path  # noqa: E402

DEV_EXE = Path(r"C:/qlcplus-dev/qlcplus.exe")
CHROME = Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe")
TMP = Path(r"C:/lightai-data/tmp/scene-live")
PORT = 9994
CDP_PORT = 9224

COMMANDS = [
    ("move fixture 12 one foot to the left", "fixture_edit.move | move [target:fixture 12] [distance:one foot] to the [direction:left]"),
    ("Move table 3 with its chairs a foot to the right from the stage",
     "fixture_edit.move | move [target:table 3] with its chairs [distance:a foot] to the [direction:right] from the stage"),
    ("place fixture 7 on coordinate 5 foot by 5 foot", "fixture_edit.move | place [target:fixture 7] on coordinate [coordinates:5 foot by 5 foot]"),
    ("rotate fixture 8 upside down", "fixture_edit.rotate | rotate [target:fixture 8] [angle:upside down]"),
    ("rotate cocktail table 1 90 degrees", "fixture_edit.rotate | rotate [target:cocktail table 1] [angle:90 degrees]"),
    ("add a high top table and 4 stools next to the DJ booth",
     "scene.add | add a [object:high top table] and [count:4] [object:stools] [place:next to the dj booth]"),
    ("remove bar stool 4", "scene.remove | remove [target:bar stool 4]"),
    ("add 2 speakers on either side of the stage", "scene.add | add [count:2] [object:speakers] [place:on either side of the stage]"),
    ("move the dj booth 6 inches back", "fixture_edit.move | move the [target:dj booth] [distance:6 inches] [direction:back]"),
]


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

    async def eval(self, expr: str):
        r = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return (r.get("result") or {}).get("value")


async def attach(url_part: str, timeout: float = 20.0) -> CDP:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2) as resp:
                pages = [p for p in json.loads(resp.read()) if p.get("type") == "page" and url_part in p.get("url", "")]
            if pages:
                ws = await websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=None)
                return CDP(ws)
        except OSError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("Chrome's debugging port never showed the /stage page")


def expected(changes: list) -> list:
    """What the open page must show once it reloaded: (kind, id, field, value) checks."""
    out = []
    for ch in changes:
        if ch["op"] == "set" and ch["field"] in ("pos", "hang"):
            out.append((ch["kind"], str(ch["id"]), ch["field"], ch["value"]))
        elif ch["op"] == "set" and ch["field"] == "rot":
            out.append((ch["kind"], str(ch["id"]), "rot", ch["value"]))
        elif ch["op"] == "add":
            out.append(("object", ch["object"]["id"], "exists", True))
        elif ch["op"] == "remove":
            out.append(("object", ch["id"], "exists", False))
    return out


def shown(draft: dict, kind: str, oid: str, field: str):
    if kind == "fixture":
        e = (draft.get("fixtures") or {}).get(oid)
        return None if e is None else e.get(field)
    o = next((x for x in draft.get("objects") or [] if str(x.get("id")) == oid), None)
    if field == "exists":
        return o is not None
    if o is None:
        return None
    if field == "rot":
        return o.get("rot") or [0, 0, o.get("rz") or 0]
    return o.get(field)


def close(a, b) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.05
    return a == b


async def run(ai, cdp: CDP, standin: bool, results: list, shots: Path, pause: float) -> None:
    for n, (text, _) in enumerate(COMMANDS, 1):
        cmd, plan = ai.plan(text)
        print(f"\n[{n}] {text}\n    {cmd.intent} ({cmd.confidence:.2f}) -> {plan.mode}: {plan.summary}")
        if plan.mode != "structural":
            results.append((f"'{text}' planned as a 3D-stage edit", False))
            continue
        res = await ai.executor.execute(plan, confirm=True)
        edit = next((r for r in res.get("results") or [] if r.get("op") == "edit_stage"), {})
        results.append((f"'{text}' saved through QLC+", bool(res.get("ok")) and edit.get("saved") == "qlc"))
        if not res.get("ok"):
            print("    ", json.dumps(res, default=str)[:600])
            continue
        checks = expected([ch for a in plan.actions if a.op == "edit_stage" for ch in a.args["changes"]])
        ok, t0 = False, time.time()
        while time.time() - t0 < 8:
            draft = await cdp.eval("window.__stage && window.__stage.getDraft()")
            if draft and all(close(shown(draft, k, i, f), v) for k, i, f, v in checks):
                ok = True
                break
            await asyncio.sleep(0.4)
        results.append((f"the open 3D page shows '{text}'", ok))
        first = next(((k, i) for k, i, f, v in checks if not (f == "exists" and v is False)), None)
        if first:
            await cdp.eval(f"window.__stage.select({json.dumps(first[0])}, {json.dumps(first[1] if first[0] == 'object' else int(first[1]))})")
        await asyncio.sleep(pause)
        shot = await cdp.call("Page.captureScreenshot", {"format": "png"})
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
        (shots / f"{n:02d}-{slug}.png").write_bytes(base64.b64decode(shot.get("data", "")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--standin", action="store_true", help="hand-written model answers instead of the model")
    ap.add_argument("--pause", type=float, default=2.5, help="seconds to look at each change")
    args = ap.parse_args()
    from lightai.app import LightAI

    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    show = make_test_project(TMP / "show")
    main_cfg = load_config()
    shutil.copy(stage_path(main_cfg.project_path), stage_path(show))
    cfg = copy.copy(main_cfg)
    cfg.project_path, cfg.main_project_path = show, None
    cfg.data_dir = TMP / "data"
    cfg.data_dir.mkdir(parents=True)
    cfg.learned_path = TMP / "learned.yaml"
    cfg.qlc_url = f"ws://127.0.0.1:{PORT}/qlcplusWS"
    shots = TMP / "shots"
    shots.mkdir(parents=True)
    results: list = []
    inst = QlcInstance(show, port=PORT, exe=DEV_EXE).start()
    chrome = None
    try:
        time.sleep(1.5)
        ai = LightAI(cfg, model_dir=Path(args.model) if args.model else main_cfg.current_model_dir(), use_embeddings=False)
        if args.standin:
            from standin import use_stand_in

            use_stand_in(ai, dict(COMMANDS))
        print("model:", ai.parser.model.version)
        chrome = subprocess.Popen([str(CHROME), "--start-maximized", f"--remote-debugging-port={CDP_PORT}",
                                   f"--user-data-dir={TMP / 'chrome'}", "--no-first-run", "--no-default-browser-check",
                                   f"http://127.0.0.1:{PORT}/stage"])
        loop = asyncio.new_event_loop()
        try:
            cdp = loop.run_until_complete(attach("/stage"))
            t0 = time.time()
            while time.time() - t0 < 30 and not loop.run_until_complete(cdp.eval("!!(window.__stage && window.__stage.getDraft())")):
                time.sleep(0.5)
            loop.run_until_complete(cdp.eval("window.__stage.camera('top')"))
            time.sleep(2.0)
            loop.run_until_complete(run(ai, cdp, args.standin, results, shots, args.pause))
            time.sleep(3.0)
            loop.run_until_complete(cdp.ws.close())
            loop.run_until_complete(ai.executor.client.close())
        finally:
            loop.close()
    finally:
        if chrome is not None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(chrome.pid)], capture_output=True)
        inst.stop()
    print()
    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    print(f"screenshots: {shots}")
    return 0 if results and all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
