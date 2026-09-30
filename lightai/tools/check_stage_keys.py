"""Visible check of the 3D stage's arrow keys (look around without a middle mouse button), in a maximized Chrome.

An isolated copy of the show and its stage runs in a QLC+ build with the 3D stage on port 9994 (never 9999). Each arrow
key is held down like a finger would; the view must turn the right way (a fixture's spot on screen moves the opposite
way), Shift must turn faster, and the page must stay free of errors.

    python tools/check_stage_keys.py [--exe C:\\qlcplus\\qlcplus.exe]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

LIGHTAI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIGHTAI))
sys.path.insert(0, str(LIGHTAI / "tools"))

from check_stage_via_lightai import CDP_PORT, CHROME, attach, wait  # noqa: E402
from lightai.config import load_config  # noqa: E402
from lightai.devtools import QlcInstance, make_test_project  # noqa: E402
from lightai.rig.stage import stage_path  # noqa: E402

TMP = Path(r"C:/lightai-data/tmp/stage-keys")
QLC_PORT = 9994
KEYS = {"ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40}


async def hold(cdp, key: str, seconds: float, shift: bool = False) -> None:
    mods = 8 if shift else 0
    if shift:
        await cdp.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Shift", "code": "ShiftLeft", "windowsVirtualKeyCode": 16, "modifiers": 8})
    ev = {"key": key, "code": key, "windowsVirtualKeyCode": KEYS[key], "nativeVirtualKeyCode": KEYS[key], "modifiers": mods}
    await cdp.call("Input.dispatchKeyEvent", dict(ev, type="rawKeyDown"))
    await asyncio.sleep(seconds)
    await cdp.call("Input.dispatchKeyEvent", dict(ev, type="keyUp"))
    if shift:
        await cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Shift", "code": "ShiftLeft", "windowsVirtualKeyCode": 16})
    await asyncio.sleep(0.4)


async def run(results: list, shots: Path) -> None:
    st = await attach(lambda t: "/stage" in t["url"])
    await st.call("Runtime.enable")
    errors: list = []
    await wait(st, "!!(window.__stage && window.__stage.getDraft())", 60)
    await wait(st, "!/Preparing lights/.test(document.body.innerText)", 90)
    await st.ev("window.__stage.camera('foh'); document.activeElement && document.activeElement.blur(); true")
    await asyncio.sleep(2)
    fid = await st.ev("(function(){var d=window.__stage.getDraft();return Object.keys(d.fixtures||{})[0]})()")
    pos = f"window.__stage.screenPosOf('fixture', {int(fid)})"

    async def at():
        p = await st.ev(f"(function(){{var p={pos};return p?[p.x,p.y]:null}})()")
        return p

    p0 = await at()
    await hold(st, "ArrowLeft", 0.6)
    p1 = await at()
    results.append((f"Left arrow turns the view left (the fixture moves right on screen: {p0} -> {p1})", bool(p0 and p1 and p1[0] > p0[0] + 20)))
    await hold(st, "ArrowRight", 0.6)
    p2 = await at()
    results.append((f"Right arrow turns it back ({p1} -> {p2})", bool(p1 and p2 and p2[0] < p1[0] - 20)))
    await hold(st, "ArrowUp", 0.4)
    p3 = await at()
    results.append((f"Up arrow looks up (the fixture moves down on screen: {p2} -> {p3})", bool(p2 and p3 and p3[1] > p2[1] + 10)))
    await hold(st, "ArrowDown", 0.4)
    p4 = await at()
    results.append((f"Down arrow looks down again ({p3} -> {p4})", bool(p3 and p4 and p4[1] < p3[1] - 10)))
    await hold(st, "ArrowLeft", 0.3)
    slow = (await at())[0] - p4[0]
    await hold(st, "ArrowRight", 0.3)
    base = await at()
    await hold(st, "ArrowLeft", 0.3, shift=True)
    fast = (await at())[0] - base[0]
    results.append((f"Shift turns faster ({slow:.0f} px -> {fast:.0f} px in the same time)", fast > slow * 1.4))
    legend = await st.ev("(document.getElementById('hud-legend')||{}).textContent || ''")
    results.append(("the key help mentions the arrows", "Arrows look" in legend))
    exc = await st.ev("window.__lightaiErrors ? window.__lightaiErrors.length : 0")
    results.append(("no page errors", not exc and not errors))
    await st.shot(shots / "stage-after-arrows.png")
    await st.ws.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=r"C:\qlcplus-dev\qlcplus.exe")
    args = ap.parse_args()
    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    show = make_test_project(TMP / "show")
    shutil.copy(stage_path(load_config().project_path), stage_path(show))
    shots = TMP / "shots"
    shots.mkdir(parents=True)
    results: list = []
    qlc = QlcInstance(show, port=QLC_PORT, exe=Path(args.exe)).start()
    chrome = None
    try:
        time.sleep(1.5)
        chrome = subprocess.Popen([CHROME, "--start-maximized", f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={TMP / 'chrome'}",
                                   "--no-first-run", "--no-default-browser-check", f"http://127.0.0.1:{QLC_PORT}/stage"])
        asyncio.run(run(results, shots))
        time.sleep(1.5)
    finally:
        if chrome:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(chrome.pid)], capture_output=True)
        qlc.stop()
    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    return 0 if results and all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
