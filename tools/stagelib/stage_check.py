#!/usr/bin/env python
"""End-to-end checks for the /stage 3D visualizer served by QLC+ web access.

Runs a SANITIZED COPY of a QLC+ project (never the original file) on an isolated dev QLC+
instance, drives the page with headless Edge/Chrome over the Chrome DevTools Protocol, and
exercises the WebSocket protocol documented in .claude/memory/stage-visualizer.md.

Run with the lightai venv's python (plain `python` is not on PATH):
    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/stage_check.py
    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/stage_check.py --project "SaveFile/Main Project.qxw"
    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/stage_check.py --only 1,2,3

HARD RULES enforced here:
  - never touches port 9999 (the live rig); refuses to run if --port 9999.
  - always operates on a sanitized copy of the project (devtools.sanitize_project /
    make_test_project), never the file the user passed in.
  - always stops QLC+ and the browser in a finally block, and restores the QLC+ registry
    settings snapshot devtools.QlcInstance took, unless --keep-open was explicitly given
    (in which case the process is left running for manual inspection, and only the registry
    settings are restored on a best-effort basis).

Assumptions about the page API (window.__stage), since the frontend (stage.html/app.js/...)
had not been built yet at the time this harness was written -- see the final report for the
full list:
  - window.__stage.selftest() -> {..., fail: <int>, ...}
  - window.__stage.status() -> {connected: bool, fixtures: <int>, fps: <number>, ...}
  - window.__stage.debug() -> some JSON-serializable per-fixture sample (dimmer/color/pan/...)
  - window.__stage.camera(name) -> switches to a named camera preset ("foh"/"top"/"side"/"stage")
  - window.__stage.getDraft() -> the in-memory (unsaved) stage draft, plus some setter to
    mutate it; if no such setter is found, that sub-check is skipped with WARN rather than
    guessed at destructively.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import http.client
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lightai"))

from lxml import etree  # noqa: E402

from lightai.devtools import (  # noqa: E402
    QlcInstance,
    make_test_project,
    port_open,
    sanitize_project,
)
from lightai import devtools  # noqa: E402
from lightai.rig.qxf import kid, kids  # noqa: E402
from lightai.rig.qxw import Workspace  # noqa: E402

import websockets  # noqa: E402
import club_scene  # noqa: E402  (same dir: tools/stagelib/club_scene.py)

EDGE_PATH = Path(r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
CHROME_PATH = Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe")
DEFAULT_DEV_EXE = Path(r"C:/qlcplus-dev/qlcplus.exe")
MINGW_BIN = r"C:\msys64\mingw64\bin"
QT_PLUGIN_PATH = r"C:\msys64\mingw64\share\qt5\plugins"


# --------------------------------------------------------------------------------------
# Result bookkeeping
# --------------------------------------------------------------------------------------

@dataclass
class Result:
    name: str
    status: str  # PASS / FAIL / WARN
    detail: str


RESULTS: list = []


def record(name: str, ok: bool, detail: str) -> None:
    status = "PASS" if ok else "FAIL"
    RESULTS.append(Result(name, status, detail))
    print(f"[{status}] {name}: {detail}")


def record_warn(name: str, detail: str) -> None:
    RESULTS.append(Result(name, "WARN", detail))
    print(f"[WARN] {name}: {detail}")


def record_exc(name: str, exc: Exception) -> None:
    record(name, False, f"exception: {exc}\n{traceback.format_exc(limit=4)}")


# --------------------------------------------------------------------------------------
# Small helpers shared across checks
# --------------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_get(host: str, port: int, path: str, timeout: float = 10.0):
    """A raw GET that never normalizes '..' or '%2F' in the path (unlike urllib)."""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.putrequest("GET", path, skip_host=False)
        conn.putheader("Host", f"{host}:{port}")
        conn.putheader("Connection", "close")
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
    finally:
        conn.close()


def stage_json_path(qxw_path: Path) -> Path:
    """Mirrors WebAccessStage::stageFilePath: <dir>/<basename-without-last-suffix>.stage.json."""
    return qxw_path.parent / (qxw_path.stem + ".stage.json")


def count_patched_fixtures(project_path: Path) -> int:
    """Real patched fixtures under Engine (Universe+Address children), not EFX axis references."""
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    tree = etree.parse(str(project_path), parser)
    engine = kid(tree.getroot(), "Engine")
    if engine is None:
        return 0
    n = 0
    for fx in kids(engine, "Fixture"):
        if kid(fx, "Universe") is not None and kid(fx, "Address") is not None:
            n += 1
    return n


def pick_free_readdress(project_path: Path):
    """First patched fixture's id, plus a universe/address that is free of any other fixture."""
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    tree = etree.parse(str(project_path), parser)
    engine = kid(tree.getroot(), "Engine")
    fixtures = []
    for fx in kids(engine, "Fixture"):
        uni_el, addr_el, ch_el, id_el = (kid(fx, "Universe"), kid(fx, "Address"),
                                          kid(fx, "Channels"), kid(fx, "ID"))
        if uni_el is None or addr_el is None or id_el is None:
            continue
        chans = int(ch_el.text) if ch_el is not None and ch_el.text else 1
        fixtures.append((int(id_el.text), int(uni_el.text), int(addr_el.text), chans))
    if not fixtures:
        raise RuntimeError("no patched fixtures found to readdress")

    target_id, target_uni, target_addr, target_chans = fixtures[0]
    by_uni: dict = {}
    for _, uni, addr, chans in fixtures:
        by_uni.setdefault(uni, []).append((addr, chans))
    universes = sorted(by_uni) or [target_uni]
    candidates = [target_uni] + [u for u in universes if u != target_uni] + [max(universes) + 1]
    for uni in candidates:
        used = by_uni.get(uni, [])
        max_end = max((a + c for a, c in used), default=0)
        if max_end + target_chans <= 512 and (uni != target_uni or max_end != target_addr):
            return target_id, uni, max_end
    raise RuntimeError("could not find a free DMX address to readdress into")


def parse_pairs(payload: str):
    """'<id>|<name>|<id2>|<name2>' (as returned by getFunctionsList) -> [(id, name), ...]."""
    if not payload:
        return []
    parts = payload.split("|")
    return list(zip(parts[0::2], parts[1::2]))


def values_differ(a, b) -> bool:
    try:
        return json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)
    except TypeError:
        return a != b


def restore_qlc_settings_only(qlc: QlcInstance) -> None:
    """Undo the registry drift QlcInstance.stop() would, without killing the process (--keep-open)."""
    settings = getattr(qlc, "settings", {})
    if settings:
        snap_no_recent = {k: {n: v for n, v in vals.items() if not n.startswith("recent")}
                           for k, vals in settings.items()}
        wk = settings.get(devtools.SETTINGS_KEY + r"\workspace", {})
        keep = [wk[f"recent{i}"] for i in range(10) if f"recent{i}" in wk]
        devtools.restore_settings_except_recent(snap_no_recent)
        devtools.clean_recent_files(keep=keep)
    try:
        qlc._unlock()
    except Exception:
        pass


# --------------------------------------------------------------------------------------
# A tiny QLC+ WebSocket client (ws://.../qlcplusWS) -- separate from CDP
# --------------------------------------------------------------------------------------

class WsConn:
    def __init__(self, ws):
        self.ws = ws
        self.received: list = []
        self._task = asyncio.create_task(self._reader())

    async def _reader(self):
        try:
            async for msg in self.ws:
                self.received.append(msg)
        except Exception:
            pass

    async def send(self, message: str) -> None:
        await self.ws.send(message)

    def mark(self) -> int:
        return len(self.received)

    async def wait_for_indexed(self, predicate: Callable[[str], bool], timeout: float = 10.0, start_index: int = 0):
        """Like wait_for, but also returns the index right after the match (for resuming a scan)."""
        deadline = time.time() + timeout
        idx = start_index
        while time.time() < deadline:
            while idx < len(self.received):
                msg = self.received[idx]
                if predicate(msg):
                    return msg, idx + 1
                idx += 1
            await asyncio.sleep(0.05)
        raise TimeoutError("timed out waiting for a matching WebSocket message")

    async def wait_for(self, predicate: Callable[[str], bool], timeout: float = 10.0, start_index: int = 0) -> str:
        msg, _next_idx = await self.wait_for_indexed(predicate, timeout=timeout, start_index=start_index)
        return msg

    async def call(self, cmd: str, *args, timeout: float = 10.0) -> str:
        """Send QLC+API|cmd|args and return everything after the fixed 'QLC+API|cmd|' prefix.

        getFunctionsList truncates one character too many when there are zero functions,
        turning 'QLC+API|getFunctionsList|' into 'QLC+API|getFunctionsList' (no trailing
        pipe); the bare-prefix fallback below handles that empty-list edge case.
        """
        start = self.mark()
        await self.send("|".join(["QLC+API", cmd, *[str(a) for a in args]]))
        prefix = f"QLC+API|{cmd}|"
        bare = f"QLC+API|{cmd}"
        reply = await self.wait_for(lambda m: m.startswith(prefix) or m == bare, timeout=timeout, start_index=start)
        return reply[len(prefix):] if reply.startswith(prefix) else ""

    async def close(self) -> None:
        self._task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass


async def connect_ws(uri: str, timeout: float = 10.0) -> WsConn:
    ws = await asyncio.wait_for(websockets.connect(uri, max_size=None), timeout=timeout)
    return WsConn(ws)


# --------------------------------------------------------------------------------------
# A tiny Chrome DevTools Protocol client, over `websockets`
# --------------------------------------------------------------------------------------

def _fetch_json(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def launch_browser(debug_port: int, user_data_dir: Path):
    exe = EDGE_PATH if EDGE_PATH.exists() else CHROME_PATH
    if not exe.exists():
        raise RuntimeError("neither Edge nor Chrome found at the expected install paths")
    args = [
        str(exe),
        "--headless=new",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data_dir}",
        "--window-size=1600,900",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    last_err = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{exe.name} exited early with code {proc.returncode}")
        try:
            _fetch_json(f"http://127.0.0.1:{debug_port}/json/version", timeout=1.5)
            return proc, exe.name
        except Exception as e:
            last_err = e
            time.sleep(0.3)
    proc.kill()
    raise TimeoutError(f"browser devtools never came up on port {debug_port}: {last_err}")


class CDPClient:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self._pending: dict = {}
        self.console_errors: list = []
        self.exceptions: list = []
        self._task = asyncio.create_task(self._recv_loop())

    @classmethod
    async def connect(cls, debug_port: int, timeout: float = 15.0) -> "CDPClient":
        targets = _fetch_json(f"http://127.0.0.1:{debug_port}/json")
        page = next((t for t in targets if t.get("type") == "page"), None)
        if page is None:
            raise RuntimeError("no page target on the headless browser's devtools endpoint")
        ws = await asyncio.wait_for(websockets.connect(page["webSocketDebuggerUrl"], max_size=None), timeout=timeout)
        client = cls(ws)
        await client.send("Page.enable")
        await client.send("Runtime.enable")
        await client.send("Log.enable")
        return client

    async def _recv_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                    continue
                method = msg.get("method")
                params = msg.get("params", {})
                if method == "Runtime.consoleAPICalled" and params.get("type") == "error":
                    self.console_errors.append(_format_console_args(params))
                elif method == "Runtime.exceptionThrown":
                    self.exceptions.append(params.get("exceptionDetails", {}))
                elif method == "Log.entryAdded" and params.get("entry", {}).get("level") == "error":
                    self.console_errors.append(params["entry"].get("text", ""))
        except Exception:
            pass

    async def send(self, method: str, params: Optional[dict] = None, timeout: float = 40.0):
        self._id += 1
        mid = self._id
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        return await asyncio.wait_for(fut, timeout=timeout)

    async def navigate(self, url: str, timeout: float = 75.0) -> None:
        # timeout covers BOTH the Page.navigate round-trip and the readyState
        # poll below - a heavy scene (club_scene.py's 54 objects, several
        # with real downloaded glTF models) can leave the renderer's main
        # thread busy long enough to delay the CDP command ack itself, not
        # just page load, so the inner send() must get the same generous
        # budget instead of its own fixed 20s default.
        self.console_errors.clear()
        self.exceptions.clear()
        await self.send("Page.navigate", {"url": url}, timeout=timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self.evaluate("document.readyState", timeout=timeout) == "complete":
                return
            await asyncio.sleep(0.2)
        raise TimeoutError(f"page never finished loading: {url}")

    async def evaluate(self, expr: str, await_promise: bool = True, return_by_value: bool = True, timeout: float = 40.0):
        res = await self.send("Runtime.evaluate", {
            "expression": expr,
            "awaitPromise": await_promise,
            "returnByValue": return_by_value,
        }, timeout=timeout)
        result = res.get("result", {})
        if "exceptionDetails" in result:
            raise RuntimeError(f"JS error evaluating {expr!r}: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    async def screenshot(self, path: Path, timeout: float = 60.0) -> None:
        # a show running (haze/beams/bloom/SSAO all animating) under
        # software GL can make a single capture noticeably slower than the
        # generic 40s default - this is a one-time capture, not a tight
        # loop, so a bigger budget is cheap insurance.
        res = await self.send("Page.captureScreenshot", {"format": "png"}, timeout=timeout)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.b64decode(res["result"]["data"]))

    async def close(self) -> None:
        self._task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass


def _format_console_args(params: dict) -> str:
    parts = []
    for a in params.get("args", []):
        parts.append(str(a.get("value", a.get("description", a.get("type", "")))))
    return " ".join(parts)


async def wait_for_js(cdp: CDPClient, expr: str, timeout: float = 15.0, interval: float = 0.25) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if await cdp.evaluate(expr):
                return
        except Exception:
            pass
        await asyncio.sleep(interval)
    raise TimeoutError(f"condition never became true: {expr}")


async def find_scene_function(api_ws: WsConn) -> Optional[str]:
    """A Scene that sets at least one channel above 0, so starting it visibly changes the DMX."""
    funcs = parse_pairs(await api_ws.call("getFunctionsList"))
    first = None
    for fid, _name in funcs:
        if await api_ws.call("getFunctionType", fid) != "Scene":
            continue
        first = first or fid
        if fid in NONZERO_SCENES:
            return fid
    return first


# Scene IDs (as strings) whose FixtureVal lists hold a non-zero value; filled from the project file.
NONZERO_SCENES: set = set()


def scan_nonzero_scenes(project: Path) -> None:
    from lxml import etree
    tree = etree.parse(str(project))
    for fn in tree.iter("{*}Function"):
        if fn.get("Type") != "Scene":
            continue
        for fv in fn.iter("{*}FixtureVal"):
            vals = [v for v in (fv.text or "").split(",") if v.strip()]
            if any(int(v) > 0 for v in vals[1::2] if v.strip().isdigit()):
                NONZERO_SCENES.add(fn.get("ID"))
                break


# --------------------------------------------------------------------------------------
# Checks 1-9
# --------------------------------------------------------------------------------------

def check_static(host: str, port: int) -> None:
    name = "1 static files"
    problems = []
    try:
        status, headers, body = http_get(host, port, "/stage")
        if status != 200 or b"importmap" not in body:
            problems.append(f"GET /stage -> {status}, importmap present={b'importmap' in body}")

        status, headers, body = http_get(host, port, "/three/build/three.module.min.js")
        ctype = headers.get("content-type", "")
        if status != 200 or "javascript" not in ctype.lower():
            problems.append(f"GET /three/build/three.module.min.js -> {status}, content-type {ctype!r}")

        for path in ("/stage-lib/../qlcplus.exe", "/stage-lib/..%2F..%2Fqlcplus.exe", "/gobos/../../qlcplus.exe"):
            status, headers, body = http_get(host, port, path)
            leaked = status == 200 and body[:2] == b"MZ"
            if leaked:
                problems.append(f"GET {path} -> LEAKED the exe (status {status}, {len(body)} bytes)")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, "/stage and /three ok; the 3 traversal attempts did not leak qlcplus.exe")
    except Exception as e:
        record_exc(name, e)


async def check_selftest(cdp: CDPClient, base_url: str) -> None:
    name = "2 selftest"
    try:
        await cdp.navigate(f"{base_url}/stage#selftest")
        await wait_for_js(cdp, "typeof window.__stage !== 'undefined'", timeout=15)
        result = await cdp.evaluate("window.__stage.selftest()")
        fail = result.get("fail") if isinstance(result, dict) else None
        if fail == 0:
            record(name, True, f"selftest passed: {json.dumps(result)[:300]}")
        else:
            record(name, False, f"selftest reported failures: {json.dumps(result)[:2000]}")
    except Exception as e:
        record_exc(name, e)


async def check_ws_protocol(api_ws: WsConn, vis_ws: WsConn, project: Path):
    name = "3 ws protocol"
    problems = []
    rig = None
    try:
        ver = await api_ws.call("lightaiVersion")
        if ver != "3":
            problems.append(f"lightaiVersion -> {ver!r}, expected '3'")

        rig = json.loads(await api_ws.call("getStageRig"))
        expected = count_patched_fixtures(project)
        actual = len(rig.get("fixtures", []))
        if actual != expected:
            problems.append(f"getStageRig has {actual} fixtures, project has {expected} patched fixtures")
        for fx in rig.get("fixtures", []):
            missing = [k for k in ("id", "universe", "address", "ch") if k not in fx]
            if missing or not isinstance(fx.get("ch"), list):
                problems.append(f"fixture {fx.get('id', '?')} malformed (missing {missing})")
                break

        snap = await vis_ws.wait_for(lambda m: m.startswith("VIS|DMX|"), timeout=10)
        frame0 = base64.b64decode(snap.split("|", 3)[3])
        if len(frame0) != 512:
            problems.append(f"VIS|DMX frame decoded to {len(frame0)} bytes, expected 512")

        scene_id = await find_scene_function(api_ws)
        if scene_id is None:
            record_warn("3b start function", "no Scene function in the project; pass --project for a show "
                                              "with a Scene to exercise this sub-check")
        else:
            mark = vis_ws.mark()
            await api_ws.send(f"QLC+API|setFunctionStatus|{scene_id}|1")
            changed = False
            deadline = time.time() + 5
            idx = mark
            while time.time() < deadline and not changed:
                try:
                    msg, idx = await vis_ws.wait_for_indexed(lambda m: m.startswith("VIS|DMX|"), timeout=1, start_index=idx)
                    if base64.b64decode(msg.split("|", 3)[3]) != frame0:
                        changed = True
                except TimeoutError:
                    break
            await api_ws.send(f"QLC+API|setFunctionStatus|{scene_id}|0")
            if not changed:
                record_warn("3b start function", f"starting Scene {scene_id} changed no DMX (its LTP values may already be latched); the stream itself is checked with a Simple Desk override below")

        mark = vis_ws.mark()
        await api_ws.send("CH|1|123")
        try:
            await vis_ws.wait_for(lambda m: m.startswith("VIS|DMX|0|") and base64.b64decode(m.split("|", 3)[3])[0] == 123,
                                  timeout=5, start_index=mark)
        except TimeoutError:
            problems.append("a Simple Desk override (CH|1|123) never showed up in the VIS|DMX stream")
        await api_ws.send("QLC+API|sdResetChannel|1")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"version ok, {actual} fixtures match the project, DMX frame is 512 bytes")
    except Exception as e:
        record_exc(name, e)
    return rig


async def check_page_live(cdp: CDPClient, base_url: str, api_ws: WsConn, rig: Optional[dict]) -> None:
    name = "4 page live"
    problems = []
    try:
        if rig is None:
            rig = json.loads(await api_ws.call("getStageRig"))
        rig_count = len(rig.get("fixtures", []))

        await cdp.navigate(f"{base_url}/stage")
        await wait_for_js(cdp, "typeof window.__stage !== 'undefined'", timeout=15)

        deadline = time.time() + 20
        status = None
        ok = False
        while time.time() < deadline:
            status = await cdp.evaluate("window.__stage.status()")
            ok = isinstance(status, dict) and status.get("connected") and status.get("fixtures") == rig_count
            if ok:
                break
            await asyncio.sleep(0.5)
        if not ok:
            problems.append(f"status() never reported connected+fixtures=={rig_count}: last={status}")

        # the page guards unsaved changes on unload; headless Edge logs the blocked prompt as an error
        cdp.console_errors[:] = [e for e in cdp.console_errors if "beforeunload" not in e]
        if cdp.console_errors or cdp.exceptions:
            texts = list(cdp.console_errors) + [e.get("text", "") for e in cdp.exceptions]
            problems.append(f"{len(cdp.console_errors)} console error(s), {len(cdp.exceptions)} exception(s): {texts[:5]}")

        # Drive one moving head's pan through a Simple Desk override: deterministic, unlike a Scene
        # whose LTP channels may already hold the values it sets.
        mover = None
        for f in (rig or {}).get("fixtures", []):
            chans = f.get("ch") or []
            pan_idx = next((i for i, c in enumerate(chans) if c.get("group") == "Pan" and c.get("byte") == 0), None)
            if pan_idx is not None:
                mover = (f, pan_idx)
                break
        if mover is None:
            record_warn("4b debug() changes", "no fixture with a pan channel in the rig")
        else:
            f, pan_idx = mover
            abs_ch = int(f["universe"]) * 512 + int(f["address"]) + pan_idx + 1
            pick = lambda d: next((x for x in (d or []) if str(x.get("id")) == str(f["id"])), {})
            d1 = await cdp.evaluate("window.__stage.debug()")
            await api_ws.send(f"CH|{abs_ch}|200")
            await asyncio.sleep(1.2)
            d2 = await cdp.evaluate("window.__stage.debug()")
            await api_ws.send(f"QLC+API|sdResetChannel|{abs_ch}")
            p1, p2 = pick(d1).get("pan"), pick(d2).get("pan")
            pan_max = (f.get("physical") or {}).get("panMax") or 360
            expected = 200 * 256 / 65535 * pan_max - pan_max / 2
            if p2 is None or p1 == p2 or abs(p2 - expected) > 2:
                stats = await cdp.evaluate("window.__stage.dmxStats ? window.__stage.dmxStats() : null")
                problems.append(f"fixture {f['id']} pan did not follow CH|{abs_ch}|200: before={p1} after={p2} "
                                f"expected~{expected:.1f} dmxStats={json.dumps(stats)}")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"connected with {rig_count} fixtures, no console errors/exceptions")
    except Exception as e:
        record_exc(name, e)


async def check_draft_until_save(cdp: Optional[CDPClient], api_ws: WsConn, vis_ws: WsConn, rig: dict) -> None:
    name = "5 draft-until-save"
    problems = []
    try:
        if not rig or not rig.get("fixtures"):
            record(name, False, "no fixtures in the rig to test with")
            return
        first_id = rig["fixtures"][0]["id"]

        info = json.loads(await api_ws.call("getStage"))
        stage_path = Path(info["path"])
        mtime_before = stage_path.stat().st_mtime if stage_path.exists() else None

        has_draft_api = False
        if cdp is not None:
            has_draft_api = bool(await cdp.evaluate(
                "typeof window.__stage !== 'undefined' && typeof window.__stage.getDraft === 'function'"))

        if not has_draft_api:
            record_warn("5a draft mutation", "window.__stage.getDraft() not found; skipping in-page draft "
                                              "mutation (still checking the file is untouched by a no-op wait)")
        else:
            setter = await cdp.evaluate(
                "['setDraftFixture', 'setFixturePos', 'updateDraft', 'setDraft'].find("
                "n => typeof window.__stage[n] === 'function') || null")
            if not setter:
                record_warn("5a draft mutation", "getDraft() exists but no recognized setter "
                                                  "(setDraftFixture/setFixturePos/updateDraft/setDraft) was found")
            else:
                fid_js = json.dumps(str(first_id))
                await cdp.evaluate(
                    f"window.__stage.{setter}({fid_js}, Object.assign({{}}, "
                    f"(window.__stage.getDraft().fixtures || {{}})[{fid_js}] || {{}}, {{pos: [111, 111, 111]}}))")

        await asyncio.sleep(2.0)
        mtime_after = stage_path.stat().st_mtime if stage_path.exists() else None
        if mtime_before != mtime_after:
            problems.append(f"the stage file changed on disk from an unsaved draft edit ({mtime_before} -> {mtime_after})")

        payload = {
            "version": 1, "units": "in",
            "room": {"width": 600, "depth": 480, "height": 168},
            "anchor": [300, 240, 0],
            "fixtures": {str(first_id): {"model": "M/N", "pos": [100, 100, 120], "rot": [0, 0, 0], "hang": "hung"}},
            "objects": [],
        }
        vis_mark = vis_ws.mark()
        reply = await api_ws.call("saveStage", json.dumps(payload))
        if not reply.startswith("OK|"):
            problems.append(f"saveStage -> {reply!r}, expected OK|<rev>")
        elif not stage_path.exists():
            problems.append(f"saveStage said OK but {stage_path} does not exist")
        else:
            info2 = json.loads(await api_ws.call("getStage"))
            got_pos = ((info2.get("stage") or {}).get("fixtures", {}).get(str(first_id), {}) or {}).get("pos")
            if got_pos != [100, 100, 120]:
                problems.append(f"getStage after save: fixture {first_id} pos={got_pos}, expected [100, 100, 120]")

            try:
                await vis_ws.wait_for(lambda m: m.startswith("VIS|STAGE_SAVED|"), timeout=5, start_index=vis_mark)
            except TimeoutError:
                problems.append("no VIS|STAGE_SAVED push seen on the other socket after saveStage")

            reply2 = await api_ws.call("saveStage", json.dumps(payload))
            if not reply2.startswith("OK|"):
                problems.append(f"second saveStage -> {reply2!r}, expected OK|<rev>")
            bak = Path(str(stage_path) + ".bak")
            if not bak.exists():
                problems.append(f".bak file missing at {bak} after a second save")

        bad_reply = await api_ws.call("saveStage", "{not valid json")
        if not bad_reply.startswith("ERR"):
            problems.append(f"saveStage with invalid JSON -> {bad_reply!r}, expected ERR|...")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"saveStage/getStage/.bak/invalid-JSON/VIS push all ok for fixture {first_id}")
    except Exception as e:
        record_exc(name, e)


async def check_props(api_ws: WsConn, allow_write: bool) -> None:
    name = "6 props"
    try:
        info = json.loads(await api_ws.call("getProps"))
        problems = []
        if "path" not in info:
            problems.append("getProps reply is missing 'path'")

        if not allow_write:
            record_warn("6b saveProps", "skipped (pass --allow-props-write to also round-trip the shared, "
                                         "per-user prop library at %USERPROFILE%\\QLC+\\stage-props.json)")
        else:
            props_obj = info.get("props") or {"version": 1, "props": {}}
            reply = await api_ws.call("saveProps", json.dumps(props_obj))
            if not reply.startswith("OK|"):
                problems.append(f"saveProps -> {reply!r}, expected OK|<rev>")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"getProps path={info.get('path')}, exists={info.get('exists')}")
    except Exception as e:
        record_exc(name, e)


async def check_readdress(api_ws: WsConn, vis_ws: WsConn, project: Path, workdir: Path) -> None:
    name = "7 readdress"
    try:
        fid, new_uni, new_addr = pick_free_readdress(project)

        # a fresh, known stage.json entry for this fixture id (self-contained, whether or not
        # check 5 already ran and left its own saved position)
        known_pos = [77, 88, 99]
        payload = {
            "version": 1, "units": "in",
            "room": {"width": 600, "depth": 480, "height": 168},
            "anchor": [300, 240, 0],
            "fixtures": {str(fid): {"model": "M/N", "pos": known_pos, "rot": [0, 0, 0], "hang": "hung"}},
            "objects": [],
        }
        reply = await api_ws.call("saveStage", json.dumps(payload))
        if not reply.startswith("OK|"):
            record(name, False, f"could not seed a stage.json before readdressing: saveStage -> {reply!r}")
            return
        stage_path = Path(json.loads(await api_ws.call("getStage"))["path"])

        readdress_dir = workdir / "readdress"
        readdress_dir.mkdir(parents=True, exist_ok=True)
        project2 = sanitize_project(project, readdress_dir / project.name)

        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
        tree = etree.parse(str(project2), parser)
        engine = kid(tree.getroot(), "Engine")
        target = next((fx for fx in kids(engine, "Fixture")
                       if kid(fx, "ID") is not None and kid(fx, "ID").text == str(fid)), None)
        if target is None:
            record(name, False, f"fixture id {fid} missing from the second sanitized copy")
            return
        kid(target, "Universe").text = str(new_uni)
        kid(target, "Address").text = str(new_addr)
        project2.write_bytes(Workspace(tree, project2).serialize())

        # copy the .stage.json next to the new project copy BEFORE loading it: positions are
        # keyed by fixture id, so they should survive the readdress unchanged
        shutil.copy(stage_path, stage_json_path(project2))

        problems = []
        rig_mark = vis_ws.mark()
        reply = await api_ws.call("loadProjectFile", str(project2), "force", timeout=60)
        used_cmd = "loadProjectFile"
        if reply.startswith("ERR"):
            used_cmd = "openProjectFile"
            reply = await api_ws.call("openProjectFile", str(project2), "force", timeout=60)
        if reply != "OK":
            problems.append(f"{used_cmd} -> {reply!r}, expected OK")
        else:
            try:
                await vis_ws.wait_for(lambda m: m.startswith("VIS|RIG_CHANGED|"), timeout=10, start_index=rig_mark)
            except TimeoutError:
                problems.append("no VIS|RIG_CHANGED push seen after loading the readdressed copy")

            new_rig = json.loads(await api_ws.call("getStageRig"))
            fx = next((f for f in new_rig.get("fixtures", []) if str(f.get("id")) == str(fid)), None)
            if fx is None:
                problems.append(f"fixture {fid} missing from getStageRig after readdress")
            elif fx.get("universe") != new_uni or fx.get("address") != new_addr:
                problems.append(f"fixture {fid} is at {fx.get('universe')}/{fx.get('address')}, "
                                 f"expected {new_uni}/{new_addr}")

            stage_info = json.loads(await api_ws.call("getStage"))
            got_pos = ((stage_info.get("stage") or {}).get("fixtures", {}).get(str(fid), {}) or {}).get("pos")
            if got_pos != known_pos:
                problems.append(f"position did not survive the readdress: got {got_pos}, expected {known_pos}")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"used {used_cmd}; fixture {fid} moved to {new_uni}/{new_addr}, "
                                f"position {known_pos} survived")
    except Exception as e:
        record_exc(name, e)


async def check_screenshots(cdp: CDPClient, base_url: str, shots_dir: Path, project_name: str) -> None:
    name = "8 screenshots"
    try:
        await cdp.navigate(f"{base_url}/stage")
        await wait_for_js(cdp, "typeof window.__stage !== 'undefined'", timeout=15)
        has_camera = bool(await cdp.evaluate("typeof window.__stage.camera === 'function'"))
        saved = []
        for preset in ("foh", "top", "side", "stage"):
            if has_camera:
                await cdp.evaluate(f"window.__stage.camera({json.dumps(preset)})")
                await asyncio.sleep(0.3)
            path = shots_dir / f"{project_name}-{preset}.png"
            await cdp.screenshot(path)
            saved.append(path.name)
        if not has_camera:
            record_warn("8a camera presets", "window.__stage.camera(name) not found; screenshots use whatever "
                                              "camera the page opens with")

        # a hash-only change does not reload the page: leave it first
        await cdp.navigate("about:blank")
        await cdp.navigate(f"{base_url}/stage#demo")
        await wait_for_js(cdp, "typeof window.__stage !== 'undefined'", timeout=15)
        await asyncio.sleep(2.0)
        demo_path = shots_dir / f"{project_name}-demo.png"
        await cdp.screenshot(demo_path)
        saved.append(demo_path.name)

        record(name, True, f"saved {len(saved)} screenshot(s) to {shots_dir}: {', '.join(saved)}")
    except Exception as e:
        record_exc(name, e)


async def check_fps(cdp: CDPClient) -> None:
    name = "9 fps"
    try:
        status = await cdp.evaluate(
            "(window.__stage && window.__stage.status) ? window.__stage.status() : null")
        fps = status.get("fps") if isinstance(status, dict) else None
        record_warn(name, f"reported fps={fps} (headless software GL; informational only)")
    except Exception as e:
        record_warn(name, f"could not read fps: {e}")


# --------------------------------------------------------------------------------------
# Checks 10-18 (mouse/Maya modifiers, undo/redo, context menu, default layout,
# frozen labels, current-project-follow, Settings popup, beam frustum, locking)
# --------------------------------------------------------------------------------------

async def _goto_stage_ready(cdp: CDPClient, base_url: str, extra_ready_fn: str = "", wait_for_fixtures: bool = True) -> None:
    ready = "typeof window.__stage !== 'undefined'"
    if extra_ready_fn:
        ready += f" && typeof window.__stage.{extra_ready_fn} === 'function'"
    await cdp.navigate(f"{base_url}/stage")
    await wait_for_js(cdp, ready, timeout=15)
    if wait_for_fixtures:
        # window.__stage exists as soon as boot() finishes registering it, but
        # the actual getStageRig/getStage/getProps round-trip (and any
        # default-layout auto-placement) happens asynchronously after that -
        # wait for it to settle so a check doesn't mutate a draft that's
        # about to be wholesale-replaced when the load finishes.
        deadline = time.time() + 15
        while time.time() < deadline:
            status = await cdp.evaluate("window.__stage.status()")
            if isinstance(status, dict) and status.get("fixtures", 0) > 0:
                return
            await asyncio.sleep(0.3)


async def check_default_layout(cdp: CDPClient, base_url: str, api_ws: WsConn) -> None:
    name = "10 default layout"
    try:
        # Checks 5/7 may already have saved a stage.json and/or switched the
        # live instance onto a readdressed copy of the project; ask the
        # server what show is CURRENTLY loaded rather than assuming `project`.
        rig_now = json.loads(await api_ws.call("getStageRig"))
        show_path = rig_now.get("show")
        if show_path:
            sp = stage_json_path(Path(show_path))
            for p in (sp, Path(str(sp) + ".bak")):
                if p.exists():
                    p.unlink()
        else:
            record_warn(name, "current show has no file path (unsaved); testing the default layout without pre-clearing a stage.json")
        await _goto_stage_ready(cdp, base_url, "getDraft")

        deadline = time.time() + 15
        status = None
        while time.time() < deadline:
            status = await cdp.evaluate("window.__stage.status()")
            if isinstance(status, dict) and status.get("fixtures", 0) > 0:
                break
            await asyncio.sleep(0.3)

        problems = []
        if not isinstance(status, dict) or status.get("unplaced") != 0:
            problems.append(f"status()={status}, expected unplaced==0")

        draft = await cdp.evaluate("window.__stage.getDraft()")
        room = (draft or {}).get("room", {})
        w, d, h = room.get("width"), room.get("depth"), room.get("height")
        fixtures = (draft or {}).get("fixtures", {})
        out_of_bounds = [
            (fid, e.get("pos"))
            for fid, e in fixtures.items()
            if not e.get("pos") or not (0 <= e["pos"][0] <= w and 0 <= e["pos"][1] <= d and 0 <= e["pos"][2] <= h)
        ]
        if out_of_bounds:
            problems.append(f"{len(out_of_bounds)} fixture(s) outside the {w}x{d}x{h} room: {out_of_bounds[:5]}")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"{len(fixtures)} fixtures auto-placed inside a {w}x{d}x{h} room, unplaced=0")
    except Exception as e:
        record_exc(name, e)


async def check_labels(cdp: CDPClient, base_url: str) -> None:
    name = "11 labels"
    try:
        await _goto_stage_ready(cdp, base_url, "labelStats")
        await asyncio.sleep(1.0)
        stats0 = await cdp.evaluate("window.__stage.labelStats()")
        problems = []
        if not isinstance(stats0, dict) or stats0.get("dom") != stats0.get("expected"):
            problems.append(f"initial labelStats mismatch: {stats0}")

        draft = await cdp.evaluate("window.__stage.getDraft()")
        fids = list((draft or {}).get("fixtures", {}).keys())
        if not fids:
            record_warn(name, f"no placed fixtures to rebuild against; initial labelStats={stats0}")
            return

        for i in range(5):
            fid = fids[i % len(fids)]
            pos = [100 + i * 7, 100 + i * 5, 96]
            await cdp.evaluate(f"window.__stage.setFixturePos({json.dumps(fid)}, {json.dumps(pos)})")
            await asyncio.sleep(0.15)

        stats1 = await cdp.evaluate("window.__stage.labelStats()")
        if not isinstance(stats1, dict) or stats1.get("dom") != stats1.get("expected"):
            problems.append(f"after 5 rebuilds labelStats mismatch: {stats1}")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"labelStats dom==expected before ({stats0}) and after 5 rebuilds ({stats1})")
    except Exception as e:
        record_exc(name, e)


async def check_undo_redo(cdp: CDPClient, base_url: str) -> None:
    name = "12 undo/redo"
    try:
        await _goto_stage_ready(cdp, base_url, "undo")
        draft0 = await cdp.evaluate("window.__stage.getDraft()")
        fids = list((draft0 or {}).get("fixtures", {}).keys())
        if not fids:
            record(name, False, "no placed fixtures to test undo/redo with")
            return
        fid = fids[0]
        before_pos = draft0["fixtures"][fid]["pos"]
        was_dirty = (await cdp.evaluate("window.__stage.status()")).get("dirty")
        problems = []

        new_pos = [before_pos[0] + 37, before_pos[1] + 11, before_pos[2]]
        await cdp.evaluate(f"window.__stage.setFixturePos({json.dumps(fid)}, {json.dumps(new_pos)})")
        h1 = await cdp.evaluate("window.__stage.historyInfo()")
        if not isinstance(h1, dict) or h1.get("undo", 0) < 1:
            problems.append(f"historyInfo after edit = {h1}, expected undo>=1")

        await cdp.evaluate("window.__stage.undo()")
        d1 = await cdp.evaluate("window.__stage.getDraft()")
        pos_after_undo = (d1.get("fixtures", {}).get(fid) or {}).get("pos")
        if pos_after_undo != before_pos:
            problems.append(f"undo did not restore position: {pos_after_undo} != {before_pos}")
        dirty_after_undo = (await cdp.evaluate("window.__stage.status()")).get("dirty")
        if was_dirty is False and dirty_after_undo:
            problems.append(f"dirty flag wrong after undo back to saved: {dirty_after_undo}")

        await cdp.evaluate("window.__stage.redo()")
        d2 = await cdp.evaluate("window.__stage.getDraft()")
        pos_after_redo = (d2.get("fixtures", {}).get(fid) or {}).get("pos")
        if pos_after_redo != new_pos:
            problems.append(f"redo did not reapply position: {pos_after_redo} != {new_pos}")
        h2 = await cdp.evaluate("window.__stage.historyInfo()")
        if not isinstance(h2, dict) or h2.get("redo", 0) != 0:
            problems.append(f"historyInfo after redo = {h2}, expected redo==0")

        await cdp.evaluate("window.__stage.undo()")  # leave state as we found it

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"undo/redo round-trip ok for fixture {fid}; history after redo={h2}")
    except Exception as e:
        record_exc(name, e)


async def check_context_actions(cdp: CDPClient, base_url: str) -> None:
    name = "13 context actions"
    try:
        await _goto_stage_ready(cdp, base_url, "duplicateObject")
        draft0 = await cdp.evaluate("window.__stage.getDraft()")
        problems = []
        objects = (draft0 or {}).get("objects", [])
        added = False
        if objects:
            obj_id = objects[0]["id"]
        else:
            obj_id = await cdp.evaluate("window.__stage.addObject('point-marker', [100, 100, 0])")
            added = True
        if not obj_id:
            record(name, False, "could not find or create an object to test with")
            return

        count0 = len((await cdp.evaluate("window.__stage.getDraft()")).get("objects", []))
        new_id = await cdp.evaluate(f"window.__stage.duplicateObject({json.dumps(obj_id)})")
        count1 = len((await cdp.evaluate("window.__stage.getDraft()")).get("objects", []))
        if not new_id or count1 != count0 + 1:
            problems.append(f"duplicateObject -> {new_id!r}, count {count0} -> {count1}, expected +1")

        ok_del = await cdp.evaluate(f"window.__stage.deleteObject({json.dumps(new_id)})")
        count2 = len((await cdp.evaluate("window.__stage.getDraft()")).get("objects", []))
        if not ok_del or count2 != count1 - 1:
            problems.append(f"deleteObject -> {ok_del}, count {count1} -> {count2}, expected -1")

        draft = await cdp.evaluate("window.__stage.getDraft()")
        fids = list((draft or {}).get("fixtures", {}).keys())
        if fids:
            fid = fids[0]
            refused = await cdp.evaluate(f"window.__stage.deleteFixture({json.dumps(fid)})")
            still_there = fid in (await cdp.evaluate("window.__stage.getDraft()")).get("fixtures", {})
            if refused is not False or not still_there:
                problems.append(f"deleteFixture({fid}) -> {refused}, still present={still_there}; expected False/True")
        else:
            record_warn("13b deleteFixture", "no fixtures to test the refusal with")

        if added:
            await cdp.evaluate(f"window.__stage.deleteObject({json.dumps(obj_id)})")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"duplicate/delete object ok (id {obj_id}), deleteFixture refused and fixture kept")
    except Exception as e:
        record_exc(name, e)


async def check_beam_direction(cdp: CDPClient, base_url: str, api_ws: WsConn, rig: Optional[dict]) -> None:
    name = "14 beam direction"
    try:
        await _goto_stage_ready(cdp, base_url, "beamDir")
        draft = await cdp.evaluate("window.__stage.getDraft()")
        fixtures_entries = (draft or {}).get("fixtures", {})
        mover = None
        for f in (rig or {}).get("fixtures", []):
            entry = fixtures_entries.get(str(f["id"]))
            if not entry or entry.get("hang") != "hung":
                continue
            chans = f.get("ch") or []
            pan_idx = next((i for i, c in enumerate(chans) if c.get("group") == "Pan" and c.get("byte") == 0), None)
            pan_fine_idx = next((i for i, c in enumerate(chans) if c.get("group") == "Pan" and c.get("byte") == 1), None)
            tilt_idx = next((i for i, c in enumerate(chans) if c.get("group") == "Tilt" and c.get("byte") == 0), None)
            tilt_fine_idx = next((i for i, c in enumerate(chans) if c.get("group") == "Tilt" and c.get("byte") == 1), None)
            if pan_idx is not None and tilt_idx is not None:
                mover = (f, pan_idx, pan_fine_idx, tilt_idx, tilt_fine_idx)
                break
        if mover is None:
            record_warn(name, "no hung moving head with pan+tilt in the rig/draft; skipped")
            return

        f, pan_idx, pan_fine_idx, tilt_idx, tilt_fine_idx = mover
        base_addr = int(f["universe"]) * 512 + int(f["address"])
        pan_ch = base_addr + pan_idx + 1
        tilt_ch = base_addr + tilt_idx + 1
        reset_chs = [pan_ch, tilt_ch]
        pan_fine_ch = base_addr + pan_fine_idx + 1 if pan_fine_idx is not None else None
        fine_ch = base_addr + tilt_fine_idx + 1 if tilt_fine_idx is not None else None
        # an earlier check (e.g. 4's own pan override) may have left a Simple
        # Desk override on one of this fixture's channels that survived
        # check 7's project reload (Simple Desk state isn't tied to the
        # loaded show) - clear this fixture's pan/tilt channels first so the
        # test starts from a known baseline.
        for ch in [pan_ch, pan_fine_ch, tilt_ch, fine_ch]:
            if ch is not None:
                await api_ws.send(f"QLC+API|sdResetChannel|{ch}")
        await asyncio.sleep(0.2)

        await api_ws.send(f"CH|{pan_ch}|127")
        await api_ws.send(f"CH|{tilt_ch}|127")
        if fine_ch is not None:
            await api_ws.send(f"CH|{fine_ch}|255")
            reset_chs.append(fine_ch)
        await asyncio.sleep(1.0)
        # beamDir() reads the fixture's LAST RENDERED transform (driven by
        # requestAnimationFrame) - this scene's software-GL frame rate can
        # be slow/bursty enough right after a heavy page load that a single
        # fixed sleep sometimes still reads a stale pre-override frame, so
        # poll until 2 consecutive reads agree instead of reading once.
        d = await _wait_stable_numbers(cdp, f"window.__stage.beamDir({json.dumps(f['id'])})")
        for ch in reset_chs:
            await api_ws.send(f"QLC+API|sdResetChannel|{ch}")

        if not isinstance(d, list) or len(d) != 3 or d[1] > -0.9:
            record(name, False, f"beamDir({f['id']}) = {d}, expected [x, y<-0.9, z] (pointing down)")
        else:
            record(name, True, f"fixture {f['id']} beamDir={d}, points down with tilt centred")
    except Exception as e:
        record_exc(name, e)


async def check_mouse_config(cdp: CDPClient, base_url: str) -> None:
    name = "15 mouse config"
    try:
        await _goto_stage_ready(cdp, base_url, "mouseConfig")
        cfg = await cdp.evaluate("window.__stage.mouseConfig()")
        # middle-drag is now stage-camera.js's mouselook by default
        # (viewSettings.orbitMiddle defaults to false) - see
        # .claude/memory/stage-visualizer.md "end of session 1" status.
        expected = {"left": "pan", "middle": "look", "right": "none"}
        if cfg != expected:
            record(name, False, f"mouseConfig() = {cfg}, expected {expected}")
        else:
            record(name, True, f"mouseConfig() = {cfg}")
    except Exception as e:
        record_exc(name, e)


async def check_settings_view(cdp: CDPClient, base_url: str) -> None:
    name = "16 settings view"
    try:
        await _goto_stage_ready(cdp, base_url, "setViewSettings")
        draft = await cdp.evaluate("window.__stage.getDraft()")
        fids = list((draft or {}).get("fixtures", {}).keys())
        if not fids:
            record(name, False, "no placed fixtures to test picking against")
            return
        fid = fids[0]
        # isolate from the default layout's other rows (see check 18's
        # identical note) so the screenPos->pickAt roundtrip is unambiguous
        room = (draft or {}).get("room", {})
        isolated_pos = [(room.get("width") or 480) / 2, (room.get("depth") or 432) / 2, 96]
        await cdp.evaluate(f"window.__stage.setFixturePos({json.dumps(fid)}, {json.dumps(isolated_pos)})")
        problems = []

        vs = await cdp.evaluate("window.__stage.setViewSettings({fov: 110, sx: 1.5})")
        if not isinstance(vs, dict) or abs(vs.get("fov", 0) - 110) > 0.01:
            problems.append(f"setViewSettings -> {vs}, expected fov==110")
        await asyncio.sleep(0.3)

        pos = await cdp.evaluate(f"window.__stage.screenPos({json.dumps(fid)})")
        if not isinstance(pos, dict):
            problems.append(f"screenPos({fid}) = {pos}")
        else:
            hit = await cdp.evaluate(f"window.__stage.pickAt({pos['x']}, {pos['y']})")
            if not isinstance(hit, dict) or hit.get("kind") != "fixture" or str(hit.get("id")) != str(fid):
                problems.append(f"pickAt(screenPos({fid})) = {hit}, expected fixture {fid}")

        await cdp.evaluate("window.__stage.setViewSettings({fov: 55, sx: 1, sy: 1})")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"fov=110/sx=1.5 applied; picking at fixture {fid}'s projected screen position still worked")
    except Exception as e:
        record_exc(name, e)


async def check_beam_frustum(cdp: CDPClient, base_url: str, rig: Optional[dict]) -> None:
    name = "17 beam frustum"
    try:
        await _goto_stage_ready(cdp, base_url, "beamInfo")
        target = None
        for f in (rig or {}).get("fixtures", []):
            if (f.get("manufacturer") or "").lower() == "mayans" and "beam230" in (f.get("model") or "").lower():
                target = f
                break
        if target is None:
            record_warn(name, "no Mayans/BEAM230 fixture in the rig; pass --project for a show with one")
            return

        fid = target["id"]
        draft = await cdp.evaluate("window.__stage.getDraft()")
        if str(fid) not in (draft or {}).get("fixtures", {}):
            await cdp.evaluate(f"window.__stage.setFixturePos({fid}, [100, 100, 156])")

        info = await cdp.evaluate(f"window.__stage.beamInfo({fid})")
        problems = []
        if not isinstance(info, dict):
            problems.append(f"beamInfo({fid}) = {info}")
        else:
            if abs(info.get("spreadDeg", 0) - 3) > 1.5:
                problems.append(f"spreadDeg={info.get('spreadDeg')}, expected ~3")
            if not (info.get("nearRadius", 0) > 0):
                problems.append(f"nearRadius={info.get('nearRadius')}, expected > 0 (lens width, not a point)")
            length = info.get("length", 0)
            near = info.get("nearRadius", 0)
            far = info.get("farRadius", 0)
            expected_far = near + length * math.tan(math.radians(1.5))
            if expected_far > 0 and abs(far - expected_far) / expected_far > 0.05:
                problems.append(f"farRadius={far}, expected ~{expected_far:.4f} (nearRadius + length*tan(1.5deg))")

        model_key = f"{target['manufacturer']}/{target['model']}"
        await cdp.evaluate(f"window.__stage.setModelOverride({json.dumps(model_key)}, {{beamSpread: 0}})")
        info2 = await cdp.evaluate(f"window.__stage.beamInfo({fid})")
        if isinstance(info2, dict):
            near2, far2 = info2.get("nearRadius", 0), info2.get("farRadius", 0)
            if near2 <= 0 or abs(far2 - near2) / max(near2, 1e-6) > 0.05:
                problems.append(f"after spread=0 override, beamInfo={info2}, expected a cylinder (farRadius~=nearRadius)")
        else:
            problems.append(f"beamInfo after override = {info2}")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"BEAM230 beam is a narrow frustum ({info}); spread=0 override -> cylinder ({info2})")
    except Exception as e:
        record_exc(name, e)


async def check_lock(cdp: CDPClient, base_url: str) -> None:
    name = "18 lock"
    try:
        await _goto_stage_ready(cdp, base_url, "setLocked")
        draft = await cdp.evaluate("window.__stage.getDraft()")
        fids = list((draft or {}).get("fixtures", {}).keys())
        if not fids:
            record(name, False, "no placed fixtures to test locking with")
            return
        fid = fids[0]
        # the default layout packs many fixtures into rows that can overlap
        # on screen from the FOH camera; move this one to an isolated spot
        # (the room's floor centre, where defaultLayoutAll never places
        # anything) so picking its screen position is unambiguous.
        room = (draft or {}).get("room", {})
        isolated_pos = [(room.get("width") or 480) / 2, (room.get("depth") or 432) / 2, 96]
        await cdp.evaluate(f"window.__stage.setFixturePos({json.dumps(fid)}, {json.dumps(isolated_pos)})")
        pos = await cdp.evaluate(f"window.__stage.screenPos({json.dumps(fid)})")
        if not isinstance(pos, dict):
            record(name, False, f"screenPos({fid}) = {pos}")
            return
        problems = []

        await cdp.evaluate(f"window.__stage.setLocked('fixture', {json.dumps(fid)}, true)")
        hit_locked = await cdp.evaluate(f"window.__stage.pickAt({pos['x']}, {pos['y']})")
        if isinstance(hit_locked, dict) and hit_locked.get("kind") == "fixture" and str(hit_locked.get("id")) == str(fid):
            problems.append(f"pickAt still returned the locked fixture: {hit_locked}")

        await cdp.evaluate(f"window.__stage.setLocked('fixture', {json.dumps(fid)}, false)")
        hit_unlocked = await cdp.evaluate(f"window.__stage.pickAt({pos['x']}, {pos['y']})")
        if not (isinstance(hit_unlocked, dict) and hit_unlocked.get("kind") == "fixture" and str(hit_unlocked.get("id")) == str(fid)):
            problems.append(f"pickAt after unlock = {hit_unlocked}, expected fixture {fid}")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"fixture {fid} not pickable while locked, pickable again after unlock")
    except Exception as e:
        record_exc(name, e)


# --------------------------------------------------------------------------------------
# Checks 19-26 (overnight wave 2: club_scene.py layout, seats, render settings,
# beam-to-floor, ADJ VPar, prop overrides, LED strips, performance).
#
# These run against whatever project is CURRENTLY loaded on the server - when
# invoked with --stage-club, that's the original --project with club_scene.py's
# layout already written to its .stage.json before QLC+ was started (see run()),
# so no re-seeding is needed here. They are placed BEFORE check 7 (readdress)
# so check 22's Simple Desk channel override still works (see the "Known QLC+/
# webaccess quirk" note in .claude/memory/stage-visualizer.md - Simple Desk
# overrides stop taking effect after check 7's project reload) and BEFORE
# check 10 (default layout), which deletes the current show's .stage.json to
# test auto-placement.
# --------------------------------------------------------------------------------------

async def _wait_stable_numbers(cdp: CDPClient, expr: str, timeout: float = 20.0, interval: float = 0.4, tol: float = 1e-3):
    """Polls a JS expression that returns a flat list of numbers until two
    consecutive reads agree (or timeout). Used for beamDir()/beamInfo()
    right after a Simple Desk channel override: those read the fixture's
    LAST RENDERED pan/tilt transform (stage-scene.js's updateFixtureStates,
    driven by requestAnimationFrame), and this scene's software-GL frame
    rate can be slow/bursty enough right after a heavy page load that a
    single fixed sleep sometimes reads a stale pre-override frame."""
    last = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = await cdp.evaluate(expr)
        nums = list(val.values()) if isinstance(val, dict) else (val if isinstance(val, list) else None)
        if nums is not None and last is not None and len(nums) == len(last) and all(abs(a - b) < tol for a, b in zip(nums, last)):
            return val
        last = nums
        await asyncio.sleep(interval)
    return val


async def _wait_camera_settled(cdp: CDPClient, before: Optional[dict] = None, timeout: float = 30.0, interval: float = 0.3):
    """Polls window.__stage.getCamera() until the position stops moving
    (2 consecutive near-identical reads) instead of guessing a fixed sleep
    for an animateTo() transition.

    If `before` (the camera state captured BEFORE triggering the move) is
    given, a reading identical to it does not count as "settled" - this
    scene's software-GL frame rate can drop low enough right after a heavy
    page load that requestAnimationFrame may not fire again for several
    seconds, so the very first poll can otherwise look falsely "stable"
    simply because the animation has not started yet."""
    def moved_from_before(cam):
        if before is None:
            return True
        bp = before.get("position") if isinstance(before, dict) else None
        cp = cam.get("position") if isinstance(cam, dict) else None
        if not (isinstance(bp, list) and isinstance(cp, list)):
            return True
        return max(abs(a - b) for a, b in zip(cp, bp)) > 1e-4

    last = None
    stable = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        cam = await cdp.evaluate("window.__stage.getCamera()")
        if (
            isinstance(cam, dict)
            and isinstance(cam.get("position"), list)
            and isinstance(last, dict)
            and isinstance(last.get("position"), list)
            and moved_from_before(cam)
        ):
            if max(abs(a - b) for a, b in zip(cam["position"], last["position"])) < 1e-4:
                stable += 1
                if stable >= 2:
                    return cam
            else:
                stable = 0
        last = cam
        await asyncio.sleep(interval)
    return last


async def check_club_scene_load(cdp: CDPClient, base_url: str) -> None:
    name = "19 club scene load"
    try:
        await cdp.navigate(f"{base_url}/stage")
        await wait_for_js(cdp, "typeof window.__stage !== 'undefined'", timeout=15)
        problems = []

        deadline = time.time() + 30
        draft = None
        while time.time() < deadline:
            draft = await cdp.evaluate("window.__stage.getDraft()")
            if isinstance(draft, dict) and len(draft.get("objects", [])) >= 54:
                break
            await asyncio.sleep(0.5)
        n_objects = len((draft or {}).get("objects", []))
        if n_objects != 54:
            problems.append(f"{n_objects} objects rendered, expected 54 (club_scene.py layout)")

        has_pending = bool(await cdp.evaluate("typeof window.__stage.assetsPending === 'function'"))
        pending = None
        if has_pending:
            deadline2 = time.time() + 30
            while time.time() < deadline2:
                pending = await cdp.evaluate("window.__stage.assetsPending()")
                if pending == 0:
                    break
                await asyncio.sleep(0.5)
            if pending != 0:
                problems.append(f"assetsPending()={pending} after 30s, expected 0 (models/truss/ledstrip not settled)")
        else:
            record_warn("19b assetsPending", "window.__stage.assetsPending() not found; skipped the asset-settle wait")

        await asyncio.sleep(1.0)
        errs = [e for e in cdp.console_errors if "beforeunload" not in e]
        if errs or cdp.exceptions:
            texts = list(errs) + [e.get("text", "") for e in cdp.exceptions]
            problems.append(f"{len(errs)} console error(s), {len(cdp.exceptions)} exception(s): {texts[:5]}")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"{n_objects} objects, assets settled (pending={pending}), no console errors within 30s")
    except Exception as e:
        record_exc(name, e)


async def check_seats(cdp: CDPClient, base_url: str) -> None:
    name = "20 seats"
    try:
        await _goto_stage_ready(cdp, base_url, "seatList")
        seats = await cdp.evaluate("window.__stage.seatList()")
        problems = []
        if not isinstance(seats, list) or len(seats) < 10:
            problems.append(f"seatList() has {len(seats) if isinstance(seats, list) else seats} seat(s), expected >=10")
            seats = seats if isinstance(seats, list) else []

        cats = {s.get("category") for s in seats}
        for want in ("table", "booth", "bar", "dj"):
            if want not in cats:
                problems.append(f"no seat with category {want!r} (categories present: {sorted(c for c in cats if c)})")

        if seats:
            idx = next((i for i, s in enumerate(seats) if s.get("category") == "dj"), 0)
            cam_before = await cdp.evaluate("window.__stage.getCamera()")
            went = await cdp.evaluate(f"window.__stage.goSeat({idx})")
            seats_len_now = await cdp.evaluate("window.__stage.seatList().length")
            # the seat move animates over ~0.8s via requestAnimationFrame, but
            # this scene's software-GL frame rate can drop low enough right
            # after a heavy page load (54 objects, real lights/shadows/bloom)
            # that requestAnimationFrame may not fire again for many seconds -
            # poll until the camera actually MOVES from its pre-goSeat
            # position and then stops changing, instead of guessing a fixed
            # sleep or treating "hasn't moved yet" as "arrived".
            cam = await _wait_camera_settled(cdp, before=cam_before)
            expected_in = (seats[idx]["pos"][2] or 0) + seats[idx]["eyeHeightIn"]
            got_in = cam["position"][1] / 0.0254 if isinstance(cam, dict) and isinstance(cam.get("position"), list) else None
            if got_in is None or abs(got_in - expected_in) > 2:
                problems.append(f"goSeat({idx}) returned {went}, seatList().length now {seats_len_now} (was {len(seats)} "
                                 f"at read time), seats[{idx}]={seats[idx]}, eye height {got_in} != expected {expected_in} "
                                 f"(+-2in): cam={cam}")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"{len(seats)} seats, categories {sorted(cats)}, goSeat eye height within 2in")
    except Exception as e:
        record_exc(name, e)


async def check_render_settings(cdp: CDPClient, base_url: str) -> None:
    name = "21 render settings"
    try:
        await _goto_stage_ready(cdp, base_url, "getRenderStats")
        problems = []
        names = await cdp.evaluate("window.__stage.qualityPresetNames ? window.__stage.qualityPresetNames() : []")
        if not names:
            problems.append("qualityPresetNames() returned nothing")

        # test the 4 render modes under "low" first (cheap) - switching modes
        # while "high"/"ultra" is active would queue each CDP round-trip
        # behind that preset's own multi-second software-GL frames (see
        # .claude/memory/stage-visualizer.md "ultra is impractically slow on
        # software GL"), which can cascade into a timeout well before we
        # even get to testing the presets themselves.
        if names:
            await cdp.evaluate(f"window.__stage.applyQualityPreset({json.dumps(names[0])})")
            await asyncio.sleep(0.5)
        for mode in ("lit", "beams", "unlit", "wireframe"):
            try:
                await cdp.evaluate(f"window.__stage.setRenderMode({json.dumps(mode)})")
                await asyncio.sleep(0.2)
            except Exception as e:
                problems.append(f"setRenderMode({mode!r}) raised: {e}")
        got_mode = await cdp.evaluate("window.__stage.getRenderMode ? window.__stage.getRenderMode() : null")
        if got_mode != "wireframe":
            problems.append(f"getRenderMode() after setRenderMode('wireframe') = {got_mode!r}")
        await cdp.evaluate("window.__stage.setRenderMode('lit')")

        stats = await cdp.evaluate("window.__stage.getRenderStats()")
        if not isinstance(stats, dict) or not isinstance(stats.get("fps"), (int, float)) or stats["fps"] < 0 or "drawCalls" not in stats:
            problems.append(f"getRenderStats() looks wrong: {stats}")

        # now the presets themselves - each just needs to apply without
        # throwing (check 26 covers actual FPS per preset).
        soft_warnings = []
        for preset in names or []:
            try:
                ok = await cdp.evaluate(f"window.__stage.applyQualityPreset({json.dumps(preset)})", timeout=90.0)
                await asyncio.sleep(1.0)
                if not ok:
                    problems.append(f"applyQualityPreset({preset!r}) returned falsy")
            except Exception as e:
                # "ultra" (8 shadow-casting lights + SSAO + reflections, all
                # CPU-rasterized) is documented as impractically slow on
                # SwiftShader software GL for a scene this size - a CDP
                # round-trip timeout applying it is this environment's known
                # limitation, not a page bug, so it's a WARN not a FAIL.
                if preset == "ultra":
                    soft_warnings.append(f"applyQualityPreset('ultra') raised (known slow on software GL): {e}")
                else:
                    problems.append(f"applyQualityPreset({preset!r}) raised: {e}")

        # clear the persisted Graphics setting so it doesn't leak "ultra"/
        # wireframe into every later check's fresh navigate() - the settings
        # popup saves to localStorage, which (unlike JS state) survives a
        # same-origin page navigation within this one browser profile.
        try:
            await cdp.evaluate("localStorage.removeItem('qlcplus-stage-graphics')")
        except Exception:
            pass

        errs = [e for e in cdp.console_errors if "beforeunload" not in e]
        if errs:
            problems.append(f"console errors during render settings sweep: {errs[:5]}")

        for w in soft_warnings:
            record_warn(name + "b", w)
        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"presets {names} applied; 4 render modes set; stats sane: {stats}")
    except Exception as e:
        record_exc(name, e)


async def check_beam_to_floor(cdp: CDPClient, base_url: str, api_ws: WsConn, rig: Optional[dict]) -> None:
    name = "22 beam to floor"
    try:
        await _goto_stage_ready(cdp, base_url, "beamInfo")
        draft = await cdp.evaluate("window.__stage.getDraft()")
        fixtures_entries = (draft or {}).get("fixtures", {})
        mover = None
        for f in (rig or {}).get("fixtures", []):
            if "beam230" not in (f.get("model") or "").lower():
                continue
            entry = fixtures_entries.get(str(f["id"]))
            if not entry or entry.get("hang") != "hung":
                continue
            chans = f.get("ch") or []
            pan_idx = next((i for i, c in enumerate(chans) if c.get("group") == "Pan" and c.get("byte") == 0), None)
            tilt_idx = next((i for i, c in enumerate(chans) if c.get("group") == "Tilt" and c.get("byte") == 0), None)
            if pan_idx is not None and tilt_idx is not None:
                mover = (f, entry, pan_idx, tilt_idx)
                break
        if mover is None:
            record_warn(name, "no hung Mayans/BEAM230 in the rig/draft; pass --stage-club or a project with one")
            return

        f, entry, pan_idx, tilt_idx = mover
        base_addr = int(f["universe"]) * 512 + int(f["address"])
        pan_ch = base_addr + pan_idx + 1
        tilt_ch = base_addr + tilt_idx + 1
        for ch in (pan_ch, tilt_ch):
            await api_ws.send(f"QLC+API|sdResetChannel|{ch}")
        await asyncio.sleep(0.2)
        await api_ws.send(f"CH|{pan_ch}|127")
        await api_ws.send(f"CH|{tilt_ch}|127")
        await asyncio.sleep(1.0)
        # same rationale as check 14: poll until the reading stabilizes
        # instead of trusting a single read after a fixed sleep.
        info = await _wait_stable_numbers(cdp, f"window.__stage.beamInfo({f['id']})")
        for ch in (pan_ch, tilt_ch):
            await api_ws.send(f"QLC+API|sdResetChannel|{ch}")

        problems = []
        height = entry["pos"][2] or 0
        if not isinstance(info, dict):
            problems.append(f"beamInfo({f['id']}) = {info}")
        elif height <= 0:
            problems.append(f"fixture {f['id']} entry has no positive height above the floor: {entry.get('pos')}")
        else:
            length = info.get("length", 0)
            if abs(length - height) / height > 0.10:
                problems.append(f"beam length {length}in vs fixture height above floor {height}in (fixture {f['id']}), expected within 10%")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"fixture {f['id']} at height {height}in, beam length {info.get('length')}in (within 10%, bodyScale accounted for)")
    except Exception as e:
        record_exc(name, e)


async def check_adj_vpar(cdp: CDPClient, base_url: str, rig: Optional[dict]) -> None:
    name = "23 adj vpar beams"
    try:
        await _goto_stage_ready(cdp, base_url, "beamCount")
        target = None
        for f in (rig or {}).get("fixtures", []):
            mfr = (f.get("manufacturer") or "").lower()
            model = (f.get("model") or "").lower().replace("-", "").replace(" ", "")
            if "american dj" in mfr and "vpar" in model:
                target = f
                break
        if target is None:
            record_warn(name, "no American DJ VPar fixture in the rig")
            return

        fid = target["id"]
        deadline = time.time() + 15
        count = None
        while time.time() < deadline:
            count = await cdp.evaluate(f"window.__stage.beamCount({fid})")
            if count:
                break
            await asyncio.sleep(0.3)

        if count != 5:
            record(name, False, f"beamCount({fid}) = {count}, expected 5 (fixture {target.get('name')!r})")
        else:
            record(name, True, f"fixture {fid} ({target.get('name')!r}) has 5 beams/emitters")
    except Exception as e:
        record_exc(name, e)


async def check_prop_overrides(cdp: CDPClient, base_url: str) -> None:
    name = "24 prop overrides"
    try:
        await _goto_stage_ready(cdp, base_url, "setObjectOverride")
        draft = await cdp.evaluate("window.__stage.getDraft()")
        objs = (draft or {}).get("objects", [])
        by_prop: dict = {}
        for o in objs:
            by_prop.setdefault(o.get("prop"), []).append(o.get("id"))
        prop_id, members = None, []
        for pid, ids in by_prop.items():
            if pid and len(ids) >= 2:
                prop_id, members = pid, ids
                break
        if not prop_id:
            record(name, False, "no prop used by >=2 objects to test definition-sharing with (need --stage-club)")
            return

        before = await cdp.evaluate(f"window.__stage.getPropDef({json.dumps(prop_id)})")
        problems = []
        obj_id = members[0]

        ok = await cdp.evaluate(f"window.__stage.setObjectOverride({json.dumps(obj_id)}, {json.dumps({'color': '#ff00aa'})})")
        if not ok:
            problems.append(f"setObjectOverride({obj_id!r}) returned falsy")
        after_override = await cdp.evaluate(f"window.__stage.getPropDef({json.dumps(prop_id)})")
        if json.dumps(after_override, sort_keys=True) != json.dumps(before, sort_keys=True):
            problems.append(f"setting an instance override changed the shared propDef {prop_id!r}: {before} -> {after_override}")

        patch = {"_wave2test": "edited"}
        ok2 = await cdp.evaluate(f"window.__stage.editPropDef({json.dumps(prop_id)}, {json.dumps(patch)})")
        if not ok2:
            problems.append(f"editPropDef({prop_id!r}) returned falsy")
        after_edit = await cdp.evaluate(f"window.__stage.getPropDef({json.dumps(prop_id)})")
        if not isinstance(after_edit, dict) or after_edit.get("_wave2test") != "edited":
            problems.append(f"editPropDef did not apply to the shared def: {after_edit}")

        # cleanup (this is a throwaway sanitized project, but tidy anyway)
        await cdp.evaluate(f"window.__stage.editPropDef({json.dumps(prop_id)}, {json.dumps({'_wave2test': None})})")
        await cdp.evaluate(f"window.__stage.setObjectOverride({json.dumps(obj_id)}, {json.dumps({'color': None})})")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"prop {prop_id!r} used by {len(members)} objects: instance override left the shared def "
                                f"untouched; editPropDef updated it (visible to every instance via propDefLookup)")
    except Exception as e:
        record_exc(name, e)


async def check_led_strips(cdp: CDPClient, base_url: str) -> None:
    name = "25 led strips"
    try:
        await _goto_stage_ready(cdp, base_url, "ledStrips")
        # ledstrip parts build a plain placeholder bar synchronously, then
        # swap in the real named "ledStrip" group once the dynamic import of
        # stage-ledstrip.js + buildLedStrip() resolves (tracked by
        # assetsPending(), same as check 19) - wait for that instead of
        # racing it, or ledStrips() legitimately finds nothing yet.
        deadline = time.time() + 20
        strips = []
        while time.time() < deadline:
            strips = await cdp.evaluate("window.__stage.ledStrips()")
            if isinstance(strips, list) and len(strips) > 0:
                break
            await asyncio.sleep(0.3)
        problems = []
        before = after = obj_id = None
        if not isinstance(strips, list) or len(strips) == 0:
            problems.append(f"ledStrips() = {strips}, expected at least 1 (club_scene.py embeds several)")
        else:
            obj_id = strips[0]["objectId"]
            before = await cdp.evaluate(f"window.__stage.ledStripState({json.dumps(obj_id)})")
            ok = await cdp.evaluate(f"window.__stage.setLedStripColor({json.dumps(obj_id)}, '#00ff00', 1)")
            after = await cdp.evaluate(f"window.__stage.ledStripState({json.dumps(obj_id)})")
            if not ok:
                problems.append(f"setLedStripColor({obj_id!r}) returned falsy")
            if not isinstance(after, dict) or (after.get("color") or "").lower() != "#00ff00":
                problems.append(f"ledStripState after setLedStripColor = {after}, expected color #00ff00")

        if problems:
            record(name, False, "; ".join(problems))
        else:
            record(name, True, f"{len(strips)} LED strip(s) present; colour update verified on {obj_id} ({before} -> {after})")
    except Exception as e:
        record_exc(name, e)


async def check_perf_presets(cdp: CDPClient, base_url: str) -> None:
    name = "26 performance"
    try:
        await _goto_stage_ready(cdp, base_url, "applyQualityPreset")
        names = await cdp.evaluate("window.__stage.qualityPresetNames ? window.__stage.qualityPresetNames() : []")
        results: dict = {}
        threw = []
        for preset in names or []:
            try:
                await cdp.evaluate(f"window.__stage.applyQualityPreset({json.dumps(preset)})", timeout=90.0)
                await asyncio.sleep(1.5)  # let a few frames render at the new settings
                stats = await cdp.evaluate("window.__stage.getRenderStats()")
                results[preset] = stats.get("fps") if isinstance(stats, dict) else None
            except Exception as e:
                # "ultra" is documented as impractically slow on software GL
                # for this scene size (see check 21's identical note) - a
                # timeout applying it is this environment's known limitation,
                # not a page bug.
                results[preset] = None
                if preset == "ultra":
                    record_warn(name + "b", f"applyQualityPreset('ultra') raised (known slow on software GL): {e}")
                else:
                    threw.append(f"{preset}: {e}")

        # clear the persisted Graphics setting so "ultra" doesn't leak into
        # every later check's fresh navigate() via localStorage (see check
        # 21's identical note - localStorage survives a same-origin
        # navigation within this one browser profile).
        try:
            await cdp.evaluate("localStorage.removeItem('qlcplus-stage-graphics')")
        except Exception:
            pass

        if threw:
            record(name, False, "; ".join(threw))
        else:
            record_warn(name, f"fps by preset (headless software GL via SwiftShader - informational, not representative "
                               f"of a real GPU): {results}")
    except Exception as e:
        record_exc(name, e)


async def take_club_screenshots(cdp: CDPClient, base_url: str, api_ws: Optional[WsConn], shots_dir: Path) -> list:
    """4 seat views (table/booth/bar/dj) + FOH lit/beams, saved as club-*.png (task item 1)."""
    saved = []
    started_scene_id = None
    try:
        await _goto_stage_ready(cdp, base_url, "seatList")
        # wait for models/truss/ledstrips to finish loading (same as check
        # 19) so screenshots don't catch placeholder boxes mid-load.
        if await cdp.evaluate("typeof window.__stage.assetsPending === 'function'"):
            deadline = time.time() + 30
            while time.time() < deadline:
                if await cdp.evaluate("window.__stage.assetsPending()") == 0:
                    break
                await asyncio.sleep(0.3)

        # run an actual show (lead's spec: function 45, "Auto Chaser Show 1",
        # same default as demo_show.py) so beams/pools of light are visible
        # in the screenshots, not just dark fixtures - fall back to any
        # Scene function if 45 doesn't exist/isn't a function in this project.
        if api_ws is not None:
            func_type = await api_ws.call("getFunctionType", "45")
            started_scene_id = "45" if func_type and not func_type.startswith("ERR") else None
            if started_scene_id is None:
                started_scene_id = await find_scene_function(api_ws)
            if started_scene_id is not None:
                await api_ws.send(f"QLC+API|setFunctionStatus|{started_scene_id}|1")
                await asyncio.sleep(2.0)

        seats = await cdp.evaluate("window.__stage.seatList()")
        wanted = {"table": None, "booth": None, "bar": None, "dj": None}
        if isinstance(seats, list):
            for i, s in enumerate(seats):
                c = s.get("category")
                if c in wanted and wanted[c] is None:
                    wanted[c] = i
        for cat, idx in wanted.items():
            if idx is None:
                continue
            cam_before = await cdp.evaluate("window.__stage.getCamera()")
            await cdp.evaluate(f"window.__stage.goSeat({idx})")
            # a fixed sleep isn't reliable here - this scene's software-GL
            # frame rate can be slow enough right after load that the
            # animateTo() transition hasn't even started within 1.2s (see
            # check 20's identical note); poll until the camera actually
            # arrives instead of screenshotting mid-transition or pre-move.
            await _wait_camera_settled(cdp, before=cam_before)
            await cdp.evaluate("window.__stage.setLabelsVisible(false)")  # belt-and-suspenders; goSeat already does this
            path = shots_dir / f"club-seat-{cat}.png"
            await cdp.screenshot(path)
            saved.append(path.name)

        if await cdp.evaluate("typeof window.__stage.camera === 'function'"):
            await cdp.evaluate("window.__stage.camera('foh')")
        await cdp.evaluate("window.__stage.setLabelsVisible(true)")
        await asyncio.sleep(0.5)
        await cdp.evaluate("window.__stage.setRenderMode('lit')")
        await asyncio.sleep(1.0)
        await cdp.screenshot(shots_dir / "club-foh-lit.png")
        saved.append("club-foh-lit.png")
        await cdp.evaluate("window.__stage.setRenderMode('beams')")
        await asyncio.sleep(1.0)
        await cdp.screenshot(shots_dir / "club-foh-beams.png")
        saved.append("club-foh-beams.png")
        await cdp.evaluate("window.__stage.setRenderMode('lit')")
        print(f"[stage_check] club screenshots saved to {shots_dir}: {saved}")
    except Exception as e:
        print(f"[stage_check] club screenshots failed ({type(e).__name__}): {e}\n{traceback.format_exc(limit=6)}")
    finally:
        if api_ws is not None and started_scene_id is not None:
            try:
                await api_ws.send(f"QLC+API|setFunctionStatus|{started_scene_id}|0")
            except Exception:
                pass
    return saved


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    shots_dir = args.shots_dir if args.shots_dir.is_absolute() else (REPO_ROOT / args.shots_dir)
    shots_dir.mkdir(parents=True, exist_ok=True)

    workdir = Path(tempfile.mkdtemp(prefix="stage_check_"))
    if args.project:
        src = Path(args.project).resolve()
        project = sanitize_project(src, workdir / src.name)
        print(f"[stage_check] using a sanitized copy of {src} -> {project}")
    else:
        project = make_test_project(workdir / "project")
        print(f"[stage_check] no --project given; using a fresh test project -> {project}")
    project_name = project.stem
    scan_nonzero_scenes(project)

    if args.stage_club:
        stage = club_scene.build_stage(project)
        stage_json_path(project).write_text(json.dumps(stage, indent=2), encoding="utf-8")
        print(f"[stage_check] wrote club layout: {len(stage['fixtures'])} fixtures, "
              f"{len(stage['objects'])} objects -> {stage_json_path(project)}")

    env = dict(os.environ)
    env["PATH"] = MINGW_BIN + os.pathsep + env.get("PATH", "")
    env["QT_PLUGIN_PATH"] = QT_PLUGIN_PATH

    qlc = QlcInstance(project, port=args.port, exe=args.exe, env=env)
    browser_proc = None
    browser_name = None
    cdp: Optional[CDPClient] = None
    api_ws: Optional[WsConn] = None
    vis_ws: Optional[WsConn] = None

    only = None
    if args.only:
        only = {int(x) for x in args.only.split(",") if x.strip()}

    def want(n: int) -> bool:
        return only is None or n in only

    try:
        print(f"[stage_check] starting {args.exe} on port {args.port} with {project} ...")
        qlc.start()
        host = "127.0.0.1"
        base_url = f"http://{host}:{args.port}"
        ws_url = f"ws://{host}:{args.port}/qlcplusWS"

        if want(1):
            check_static(host, args.port)

        need_browser = any(want(n) for n in (2, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
                                              19, 20, 21, 22, 23, 24, 25, 26))
        if need_browser:
            debug_port = free_port()
            user_data_dir = Path(tempfile.mkdtemp(prefix="stage_check_browser_"))
            browser_proc, browser_name = launch_browser(debug_port, user_data_dir)
            cdp = await CDPClient.connect(debug_port)
            print(f"[stage_check] launched headless {browser_name} (devtools port {debug_port})")

        if want(2):
            await check_selftest(cdp, base_url)

        need_ws = any(want(n) for n in (3, 4, 5, 6, 7, 10, 14, 22))
        if need_ws:
            api_ws = await connect_ws(ws_url)
            vis_ws = await connect_ws(ws_url)
            await vis_ws.send("VIS|SUBSCRIBE")
            await asyncio.sleep(0.2)

        rig = None
        if want(3):
            rig = await check_ws_protocol(api_ws, vis_ws, project)
        if want(4):
            await check_page_live(cdp, base_url, api_ws, rig)

        # 14 (beam direction) and 19-26 (overnight wave 2: club_scene.py
        # layout, seats, render settings, beam-to-floor, ADJ VPar, prop
        # overrides, LED strips, performance) all run here, BEFORE check 5
        # (which overwrites the whole stage.json with a single-fixture,
        # zero-object payload as part of its own save/load round-trip test)
        # and check 7 (project reload). 14/22 also need a working Simple
        # Desk channel override, which - per repeated manual testing this
        # session - stops taking effect on the live DMX output after check
        # 7's openProjectFile "force" reload (a QLC+/webaccess-side quirk
        # outside webaccess/res/, not something stage-scene.js/stage-rig.js
        # can work around: the raw VIS|DMX bytes for an overridden channel
        # stay 0 post-reload no matter what channel/universe is targeted).
        # See .claude/memory/stage-visualizer.md.
        need_early = any(want(n) for n in (14, 19, 20, 21, 22, 23, 24, 25, 26))
        if need_early:
            if api_ws is None:
                api_ws = await connect_ws(ws_url)
            if rig is None:
                rig = json.loads(await api_ws.call("getStageRig"))
        if want(19):
            await check_club_scene_load(cdp, base_url)
        # 14 and 22 both read a fixture's pan/tilt shortly after a Simple
        # Desk channel override, which only shows up once the page's own
        # render loop has processed at least one more frame - run them
        # BEFORE 21/26 (which deliberately leave the "ultra" quality preset
        # active - see its own note - and can drop this scene to
        # multi-second frames under software GL), so that 1s settle sleep
        # is actually enough for a fresh frame to land.
        if want(14):
            await check_beam_direction(cdp, base_url, api_ws, rig)
        if want(22):
            await check_beam_to_floor(cdp, base_url, api_ws, rig)
        if want(20):
            await check_seats(cdp, base_url)
        if want(23):
            await check_adj_vpar(cdp, base_url, rig)
        if want(24):
            await check_prop_overrides(cdp, base_url)
        if want(25):
            await check_led_strips(cdp, base_url)
        if want(21):
            await check_render_settings(cdp, base_url)
        if want(26):
            await check_perf_presets(cdp, base_url)
        if args.stage_club and cdp is not None:
            # take_club_screenshots() does its own fresh navigate() first,
            # which reloads the page at the default (medium) quality preset -
            # any "ultra" left over from 21/26 doesn't carry over.
            await take_club_screenshots(cdp, base_url, api_ws, shots_dir)

        if want(5):
            if rig is None and api_ws is not None:
                rig = json.loads(await api_ws.call("getStageRig"))
            await check_draft_until_save(cdp, api_ws, vis_ws, rig or {})
        if want(6):
            await check_props(api_ws, args.allow_props_write)

        if want(7):
            await check_readdress(api_ws, vis_ws, project, workdir)
        if want(8):
            await check_screenshots(cdp, base_url, shots_dir, project_name)
        if want(9):
            await check_fps(cdp)

        if want(10):
            if api_ws is None:
                api_ws = await connect_ws(ws_url)
            await check_default_layout(cdp, base_url, api_ws)
        if want(11):
            await check_labels(cdp, base_url)
        if want(12):
            await check_undo_redo(cdp, base_url)
        if want(13):
            await check_context_actions(cdp, base_url)
        if want(15):
            await check_mouse_config(cdp, base_url)
        if want(16):
            await check_settings_view(cdp, base_url)
        if want(17):
            if rig is None and api_ws is not None:
                rig = json.loads(await api_ws.call("getStageRig"))
            await check_beam_frustum(cdp, base_url, rig)
        if want(18):
            await check_lock(cdp, base_url)

        if cdp is not None:
            try:
                await cdp.navigate(f"{base_url}/stage")
                await wait_for_js(cdp, "typeof window.__stage !== 'undefined'", timeout=15)
                if await cdp.evaluate("typeof window.__stage.camera === 'function'"):
                    await cdp.evaluate("window.__stage.camera('foh')")
                await asyncio.sleep(0.3)
                await cdp.screenshot(shots_dir / f"{project_name}-foh-after.png")
                print(f"[stage_check] saved {project_name}-foh-after.png")
            except Exception as e:
                print(f"[stage_check] could not save the foh-after screenshot: {e}")

    finally:
        print("[stage_check] cleaning up ...")
        for conn in (api_ws, vis_ws):
            if conn is not None:
                await conn.close()
        if cdp is not None:
            await cdp.close()
        if browser_proc is not None:
            if args.keep_open:
                print(f"[stage_check] --keep-open: leaving {browser_name} running (pid {browser_proc.pid})")
            else:
                try:
                    browser_proc.terminate()
                    try:
                        browser_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        browser_proc.kill()
                except Exception:
                    pass
        if qlc.proc is not None:
            if args.keep_open:
                print(f"[stage_check] --keep-open: leaving QLC+ running on port {args.port}; "
                      f"restoring registry settings only (recent-files may be re-dirtied when you close it)")
                try:
                    restore_qlc_settings_only(qlc)
                except Exception as e:
                    print(f"[stage_check] warning: could not restore QLC+ settings: {e}")
            else:
                qlc.stop()
        else:
            # start() may have acquired the machine-wide lock (see QlcInstance._lock) before
            # failing later (e.g. the exe was missing); don't strand it for the next run.
            try:
                qlc._unlock()
            except Exception:
                pass
        print(f"[stage_check] working files kept at {workdir}")

    summary = {
        "project": str(project),
        "port": args.port,
        "results": [{"name": r.name, "status": r.status, "detail": r.detail} for r in RESULTS],
    }
    summary_path = shots_dir / "stage_check.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[stage_check] wrote summary to {summary_path}")

    print("\n===== stage_check summary =====")
    for r in RESULTS:
        print(f"  [{r.status}] {r.name}")
    fails = [r for r in RESULTS if r.status == "FAIL"]
    print(f"{len(RESULTS)} checks: {len(RESULTS) - len(fails)} ok, {len(fails)} failed")
    return 1 if fails else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=Path, default=None,
                         help="Show file to sanitize and test with (default: a fresh Blank Rig Template copy)")
    parser.add_argument("--port", type=int, default=9998, help="Test QLC+ instance port (never 9999)")
    parser.add_argument("--exe", type=Path, default=DEFAULT_DEV_EXE, help="Path to the dev qlcplus.exe")
    parser.add_argument("--keep-open", action="store_true",
                         help="Leave QLC+ and the browser running afterwards for manual inspection")
    parser.add_argument("--shots-dir", type=Path, default=Path("lightai/reports/stage"),
                         help="Where to write screenshots and stage_check.json")
    parser.add_argument("--allow-props-write", action="store_true",
                         help="Also round-trip the shared per-user prop library (check 6b); off by default")
    parser.add_argument("--stage-club", action="store_true",
                         help="Write the club_scene.py layout beside the sanitized project copy before "
                              "starting QLC+, and run checks 19-26 (club scene / seats / render settings / "
                              "beam-to-floor / ADJ VPar / prop overrides / LED strips / performance)")
    parser.add_argument("--only", type=str, default=None,
                         help="Comma-separated check numbers to run, e.g. --only 1,3,5")
    args = parser.parse_args()

    if args.port == 9999:
        print("[stage_check] refusing to run: port 9999 is the live rig", file=sys.stderr)
        return 2
    if port_open(args.port):
        print(f"[stage_check] refusing to run: port {args.port} is already in use", file=sys.stderr)
        return 2

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
