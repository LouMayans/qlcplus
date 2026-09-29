"""Check left/right clicks in View and Edit mode on the 3D stage page, in a VISIBLE Chrome window.

    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/click_check.py [--url http://127.0.0.1:9998/stage] [--keep]

View mode: a click (left or right) on a fixture or object outlines it and shows its name tag;
no Properties, gizmo or context menu, and a drag never moves anything. An empty click clears
it, and so does switching to Edit. Edit mode: a left click selects (Properties shown), a
right click on an object opens the context menu. Screenshots go to lightai/reports/stage/clicks-*.png.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_check import CDPClient  # noqa: E402
from live_drive import BROWSER, free_port  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "lightai" / "reports" / "stage"

# grid scan: the nearest-to-centre canvas point (not covered by UI) that picks a fixture, a
# non-wall object, a wall/ceiling object, and nothing at all
SCAN_JS = """(() => {
  const S = window.__stage, c = document.querySelector('canvas'), r = c.getBoundingClientRect();
  const objs = {}; for (const o of (S.getDraft().objects || [])) objs[o.id] = o;
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2, best = {};
  const shell = (o) => /wall|ceiling|floor|room/i.test((o && (o.prop + ' ' + (o.name || ''))) || '');
  for (let gy = 0; gy < 36; gy++) for (let gx = 0; gx < 60; gx++) {
    const x = r.left + (gx + 0.5) / 60 * r.width, y = r.top + (gy + 0.5) / 36 * r.height;
    if (document.elementFromPoint(x, y) !== c) continue;
    const h = S.pickAt(x, y);
    let k = !h ? 'empty' : h.kind === 'fixture' ? 'fixture' : shell(objs[h.id]) ? 'shell' : 'object';
    const d = Math.hypot(x - cx, y - cy);
    if (!best[k] || d < best[k].d) best[k] = {x: Math.round(x), y: Math.round(y), d: d, id: h ? h.id : null,
      name: h && h.kind !== 'fixture' && objs[h.id] ? (objs[h.id].name || objs[h.id].prop) : null};
  }
  return JSON.stringify(best); })()"""

STATE_JS = """(() => {
  const tag = document.getElementById('inspect-tag'), sf = document.getElementById('selection-fields');
  const menu = document.getElementById('context-menu'), rp = document.getElementById('right-panel');
  return JSON.stringify({mode: window.__stage.status().mode, dirty: window.__stage.status().dirty,
    tag: tag ? tag.textContent : null, tagShown: !!tag && tag.style.display !== 'none',
    props: !!sf && sf.style.display !== 'none' && !!rp && rp.offsetWidth > 0,
    menu: !!menu && !menu.classList.contains('hidden')}); })()"""


async def click(cdp, x, y, button="left"):
    mask = 1 if button == "left" else 2
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": button, "buttons": mask, "clickCount": 1})
    await asyncio.sleep(0.05)
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": button, "buttons": 0, "clickCount": 1})
    await asyncio.sleep(0.6)


async def drag(cdp, x, y, dx, dy):
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1})
    for i in range(1, 16):
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x + dx * i / 15, "y": y + dy * i / 15, "button": "left", "buttons": 1})
        await asyncio.sleep(0.03)
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x + dx, "y": y + dy, "button": "left", "buttons": 0, "clickCount": 1})
    await asyncio.sleep(0.6)


async def run(url: str, keep: bool) -> int:
    port = free_port()
    profile = tempfile.mkdtemp(prefix="stage-click-")
    proc = subprocess.Popen([BROWSER, f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "--no-first-run",
                             "--no-default-browser-check", "--start-maximized", "--disable-backgrounding-occluded-windows",
                             "--disable-renderer-backgrounding", "--disable-background-timer-throttling",
                             "--disable-features=CalculateNativeWinOcclusion", url])
    fails = []
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
            if await cdp.evaluate("!!(window.__stage && window.__stage.assetsPending && window.__stage.assetsPending() === 0)"):
                break
            await asyncio.sleep(1)
        await asyncio.sleep(3)
        await cdp.evaluate("window.__stage.setLabelsVisible(false); window.__stage.camera('foh'); true")
        await asyncio.sleep(2)
        pts = json.loads(await cdp.evaluate(SCAN_JS, timeout=60))
        print("targets:", json.dumps({k: {kk: v[kk] for kk in ("x", "y", "id", "name")} for k, v in pts.items()}), flush=True)

        n = [0]

        async def check(name, expect):
            n[0] += 1
            st = json.loads(await cdp.evaluate(STATE_JS))
            await cdp.screenshot(OUT / f"clicks-{n[0]}-{name}.png")
            bad = [f"{k}={st[k]!r} (want {v!r})" for k, v in expect.items()
                   if (st[k] != v if not callable(v) else not v(st[k]))]
            print(f"{'FAIL' if bad else 'ok  '} {n[0]}-{name}: {st}" + (f"  <-- {'; '.join(bad)}" if bad else ""), flush=True)
            if bad:
                fails.append(name)
            return st

        has = lambda v: bool(v)  # noqa: E731
        fx, ob, empty = pts.get("fixture"), pts.get("object") or pts.get("shell"), pts.get("empty")
        await cdp.evaluate("document.getElementById('mode-view-btn').click(); true")
        await asyncio.sleep(0.5)
        if fx:
            await click(cdp, fx["x"], fx["y"])
            await check("view-left-fixture", {"mode": "view", "tag": has, "tagShown": True, "props": False, "menu": False})
        if ob:
            await click(cdp, ob["x"], ob["y"], "right")
            await check("view-right-object", {"tag": has, "props": False, "menu": False})
            before = json.dumps(json.loads(await cdp.evaluate("JSON.stringify(window.__stage.getDraft().objects)")))
            await drag(cdp, ob["x"], ob["y"], 120, 40)
            after = json.dumps(json.loads(await cdp.evaluate("JSON.stringify(window.__stage.getDraft().objects)")))
            st = await check("view-drag-object", {"props": False, "menu": False, "dirty": False})
            if before != after:
                print("FAIL view drag changed the layout", flush=True)
                fails.append("view-drag-moved")
            await cdp.evaluate("window.__stage.camera('foh'); true")
            await asyncio.sleep(1.5)
        if fx:
            await click(cdp, fx["x"], fx["y"], "right")
            await check("view-right-fixture", {"tag": has, "props": False, "menu": False})
        # left-panel list rows are another selection path - in View mode they must only inspect too
        for lst, want in (("fixtures-list", None), ("objects-list", "Bar")):
            pos = json.loads(await cdp.evaluate(f"""(() => {{
              const rows = Array.from(document.querySelectorAll('#{lst} .list-row .list-row-label'));
              const r = (rows.find(e => e.textContent.trim() === {json.dumps(want)}) || rows[0]);
              if (!r) return 'null'; r.scrollIntoView({{block: 'center'}}); const b = r.getBoundingClientRect();
              return JSON.stringify({{x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2), t: r.textContent.trim()}}); }})()"""))
            if not pos:
                continue
            await click(cdp, pos["x"], pos["y"])
            await check(f"view-list-{lst}", {"mode": "view", "tag": has, "props": False, "menu": False})
            await click(cdp, pos["x"], pos["y"])
            await check(f"view-list-{lst}-again-clears", {"tag": None, "props": False})
        if empty:
            await click(cdp, empty["x"], empty["y"])
            await check("view-empty-click", {"tag": None, "props": False, "menu": False})
        else:
            print("note: no empty spot in view (room shell covers the canvas)", flush=True)
        if fx:
            await click(cdp, fx["x"], fx["y"])
        await cdp.evaluate("document.getElementById('mode-edit-btn').click(); true")
        await asyncio.sleep(1)
        await check("edit-switch-clears", {"mode": "edit", "tag": None, "menu": False})
        if ob:
            await click(cdp, ob["x"], ob["y"])
            await check("edit-left-object", {"props": True, "menu": False})
            await click(cdp, ob["x"], ob["y"], "right")
            await check("edit-right-object", {"menu": True})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "code": "Escape", "key": "Escape", "windowsVirtualKeyCode": 27})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "code": "Escape", "key": "Escape", "windowsVirtualKeyCode": 27})
            await asyncio.sleep(0.4)
        if fx:
            await click(cdp, fx["x"], fx["y"])
            await check("edit-left-fixture", {"props": True, "menu": False})
            await click(cdp, fx["x"], fx["y"], "right")
            await check("edit-right-fixture", {})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "code": "Escape", "key": "Escape", "windowsVirtualKeyCode": 27})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "code": "Escape", "key": "Escape", "windowsVirtualKeyCode": 27})
        await cdp.evaluate("document.getElementById('mode-view-btn').click(); true")
        await asyncio.sleep(1)
        await check("back-to-view", {"mode": "view", "props": False, "menu": False, "tag": None})
        errs = await cdp.evaluate("JSON.stringify((window.__errors || []).slice(-5))")
        print("page errors:", errs, flush=True)
        await cdp.close()
    finally:
        if not keep:
            proc.terminate()
    print("RESULT:", "PASS" if not fails else f"FAIL {fails}", flush=True)
    return 1 if fails else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:9998/stage")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.url, a.keep)))


if __name__ == "__main__":
    main()
