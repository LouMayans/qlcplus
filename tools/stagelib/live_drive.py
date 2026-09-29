"""Drive the 3D stage page in a VISIBLE Chrome window on the real GPU and measure freezes.

    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/live_drive.py [--url http://127.0.0.1:9998/stage] [--keep]

Opens Chrome with a throw-away profile, waits for the club
to load, then moves the camera on every axis for a few seconds each (WASD, Q/E, middle-drag
yaw/pitch, left-drag pan), switches seats and render settings, and after each step prints the
FPS, the longest frame and the number of frames over 250 ms. Closes the window at the end.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_check import CDPClient  # noqa: E402

BROWSER = r"C:\Program Files\Google\Chrome\Application\chrome.exe"  # Chrome: no Edge sync/sign-in popups on fresh profiles
X0, Y0 = 820, 430  # a point over the 3D view (the left panel is ~300 px wide)

FRAME_PROBE = """(() => { window.__lf = {max: 0, slow: 0, n: 0};
  if (window.__lfOn) return true; window.__lfOn = true;
  let last = performance.now();
  (function f(t) { const d = t - last; last = t; const s = window.__lf; s.n++;
    if (d > s.max) s.max = d; if (d > 250) s.slow++; requestAnimationFrame(f); })(last);
  return true; })()"""


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def step(cdp: CDPClient, name: str, coro) -> dict:
    await cdp.evaluate("window.__lf = {max: 0, slow: 0, n: 0}")
    t0 = time.time()
    await coro
    took = time.time() - t0
    t1 = time.time()
    try:
        await cdp.evaluate("1", timeout=30)
        lag = (time.time() - t1) * 1000
    except Exception:
        lag = -1
    lf = await cdp.evaluate("JSON.stringify(window.__lf)")
    st = await cdp.evaluate("JSON.stringify(window.__stage.getRenderStats ? window.__stage.getRenderStats() : {})")
    lf, st = json.loads(lf), json.loads(st)
    fps = lf["n"] / took if took > 0 else 0
    row = {"step": name, "fps": round(fps, 1), "maxFrameMs": round(lf["max"]), "slowFrames": lf["slow"],
           "evalLagMs": round(lag), "tris": st.get("triangles"), "calls": st.get("drawCalls"), "progs": st.get("programs"), "tex": st.get("textures")}
    flag = "  <-- FREEZE" if lf["max"] > 1000 or lag < 0 else ("  <-- hitch" if lf["max"] > 250 else "")
    print(f"{name:28s} fps {row['fps']:5.1f}  worst frame {row['maxFrameMs']:5d} ms  slow {row['slowFrames']:3d}  "
          f"lag {row['evalLagMs']:5d} ms  progs {row['progs']} tex {row['tex']}{flag}", flush=True)
    return row


async def hold_key(cdp, code, key, vk, seconds):
    await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "code": code, "key": key, "windowsVirtualKeyCode": vk})
    await asyncio.sleep(seconds)
    await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "code": code, "key": key, "windowsVirtualKeyCode": vk})


async def drag(cdp, button, dx, dy, seconds, buttons_mask):
    steps = max(10, int(seconds * 30))
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": X0, "y": Y0})
    await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": X0, "y": Y0, "button": button, "buttons": buttons_mask, "clickCount": 1})
    for i in range(1, steps + 1):
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": X0 + dx * i / steps, "y": Y0 + dy * i / steps,
                                                    "button": button, "buttons": buttons_mask})
        await asyncio.sleep(seconds / steps)
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": X0 + dx, "y": Y0 + dy, "button": button, "buttons": 0, "clickCount": 1})


async def seat_then_wait(cdp, i):
    await cdp.evaluate(f"void window.__stage.goSeat({i})", await_promise=False, timeout=60)
    await asyncio.sleep(2.5)


async def settings_then_wait(cdp, patch):
    await cdp.evaluate(f"void window.__stage.setRenderSettings({json.dumps(patch)})", await_promise=False, timeout=60)
    await asyncio.sleep(2.5)


async def run(url: str, keep: bool) -> None:
    port = free_port()
    profile = tempfile.mkdtemp(prefix="stage-live-")
    proc = subprocess.Popen([BROWSER, f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "--no-first-run",
                             "--no-default-browser-check", "--start-maximized",
                             # keep drawing even if another window covers this one (Windows occlusion
                             # throttling otherwise stops frames and looks like a freeze)
                             "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                             "--disable-background-timer-throttling",
                             "--disable-features=CalculateNativeWinOcclusion", url])
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        cdp = await CDPClient.connect(port)
        await cdp.send("Runtime.enable")
        await cdp.send("Page.bringToFront")
        for _ in range(90):
            ok = await cdp.evaluate("!!(window.__stage && window.__stage.assetsPending && window.__stage.assetsPending() === 0)")
            if ok:
                break
            await asyncio.sleep(1)
        gpu = await cdp.evaluate("(() => { const c = document.querySelector('canvas'); const g = c && c.getContext('webgl2');"
                                 " const e = g && g.getExtension('WEBGL_debug_renderer_info');"
                                 " return e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'unknown'; })()")
        print(f"GPU: {gpu}")
        print(f"settings: {await cdp.evaluate('JSON.stringify(window.__stage.getRenderSettings().spot)')} quality="
              f"{await cdp.evaluate('window.__stage.getRenderSettings().quality')}")
        await cdp.evaluate(FRAME_PROBE)
        rows = []
        for quality in ("low", "medium"):
            await cdp.evaluate(f"void window.__stage.applyQualityPreset('{quality}')", await_promise=False, timeout=60)
            print(f"--- {quality} ---", flush=True)
            rows.append(await step(cdp, f"{quality}: settle", asyncio.sleep(4)))
            for code, key, vk, name in (("KeyW", "w", 87, "W forward"), ("KeyS", "s", 83, "S back"), ("KeyA", "a", 65, "A left"),
                                        ("KeyD", "d", 68, "D right"), ("KeyE", "e", 69, "E up"), ("KeyQ", "q", 81, "Q down")):
                rows.append(await step(cdp, f"{quality}: {name}", hold_key(cdp, code, key, vk, 2.5)))
            rows.append(await step(cdp, f"{quality}: look yaw", drag(cdp, "middle", 400, 0, 2.5, 4)))
            rows.append(await step(cdp, f"{quality}: look pitch", drag(cdp, "middle", 0, 180, 2.5, 4)))
            rows.append(await step(cdp, f"{quality}: pan", drag(cdp, "left", -300, 120, 2.5, 1)))
            for i in (0, 5, 12):
                rows.append(await step(cdp, f"{quality}: seat {i}", seat_then_wait(cdp, i)))
        for n in (4, 8, 2):
            rows.append(await step(cdp, f"spot.maxLights -> {n}", settings_then_wait(cdp, {"spot": {"maxLights": n}})))
        rows.append(await step(cdp, "idle end", asyncio.sleep(3)))
        errs = cdp.console_errors[:5] + [str(e.get("exception", {}).get("description", e.get("text")))[:200] for e in cdp.exceptions][:3]
        print("errors:", errs or "none")
        out = Path(__file__).resolve().parents[2] / "lightai" / "reports" / "stage" / "live_drive.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"gpu": gpu, "rows": rows, "errors": errs}, indent=2), encoding="utf-8")
        await cdp.close()
    finally:
        if not keep:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:9998/stage")
    ap.add_argument("--keep", action="store_true", help="leave the window open")
    a = ap.parse_args()
    asyncio.run(run(a.url, a.keep))
