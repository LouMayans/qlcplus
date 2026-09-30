"""Visible-Chrome check of the lightai booth console: Command / Design / Claude / History tabs.

Runs the real API server (lightai.api.server.create_app) against an isolated copy of the main
show (a fake QLC+ from tests/fakeqlc.py) and a fake Claude backend that streams a canned,
pre-validated two-show design over the DesignBackend protocol - so the whole pipeline (job
store, validator, composer, run log) runs for real, only the two external processes (QLC+ and
claude.exe) are faked. Drives a real, visible, maximized Chrome (tabs visible) window over the Chrome
DevTools Protocol so a human can watch it work.

Never touches port 9999 or the real show/data dir: everything lives under
C:\\lightai-data\\tmp\\ui-check, wiped clean at the start of each run.

Run with the project venv (plain Python, no pytest):
    C:\\lightai-env\\venv\\Scripts\\python.exe lightai\\tools\\check_console_ui.py
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

try:  # the timeline/history text we print can legitimately contain Unicode (icons, curly quotes,
    # arrows) that the Windows console's cp1252 codepage can't encode; replace rather than crash.
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

TOOLS_DIR = Path(__file__).resolve().parent
LIGHTAI_ROOT = TOOLS_DIR.parent  # .../qlcplus/lightai  (contains the `lightai` package and `tests`)
REPO_ROOT = LIGHTAI_ROOT.parent  # .../qlcplus  (contains SaveFile/)
sys.path.insert(0, str(LIGHTAI_ROOT))
sys.path.insert(0, str(LIGHTAI_ROOT / "tests"))

import websockets  # noqa: E402

from lightai.config import load_config  # noqa: E402
from lightai.design.backend import build_result  # noqa: E402
from lightai.design.runlog import RunLog  # noqa: E402
from lightai.design.spec import DesignResult  # noqa: E402
from lightai.design.validate import validate_design  # noqa: E402
from fakeqlc import FakeQlc  # noqa: E402

HOST, PORT = "127.0.0.1", 8766
BASE = f"http://{HOST}:{PORT}"
CDP_PORT = 9223
WORK = Path(r"C:\lightai-data\tmp\ui-check")
SHOW_DIR = WORK / "show"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
FAIL_SHOT = WORK / "fail.png"
SAVEFILE = REPO_ROOT / "SaveFile"
DESIGN_REQUEST = "Create 3 different shows that resemble fast strobing but changing in a dreamy way"

RESULTS: list = []


def report(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    line = ("PASS " if ok else "FAIL ") + name
    if detail:
        line += f" -- {detail}"
    print(line, flush=True)
    return ok


# A design that validate_design() accepts for the real "Main Project" rig: recipes color_wash /
# circle_wave / strobe / position, on the zones washes (8,9,10,11) / spots (14 moving heads) /
# pars, aimed at the "dance floor" place from Main Project.stage.json. Checked again at startup
# with validate_design() itself before the browser is ever launched.
CANNED_DESIGN = {
    "shows": [
        {
            "title": "Dreamy Strobe Bloom",
            "description": "slow lavender/blue bloom broken by soft strobe hits, spots crossing the floor",
            "mood_tags": ["dreamy", "strobe"],
            "bpm": 124,
            "sections": [
                {"name": "Bloom", "seconds": 20, "transition_fade_ms": 1500, "looks": [
                    {"layer": "base", "recipe": "color_wash", "targets": ["washes"], "colors": ["lavender", "blue"],
                     "intensity": 0.8, "rate": "slow", "fade_ms": 2000, "order": "left_to_right"},
                    {"layer": "movement", "recipe": "circle_wave", "targets": ["spots"], "colors": ["blue"],
                     "intensity": 0.7, "rate": "slow", "size": "medium", "order": "center_out"},
                ]},
                {"name": "Strobe Hit", "seconds": 12, "transition_fade_ms": 200, "looks": [
                    {"layer": "strobe", "recipe": "strobe", "targets": ["pars"], "colors": ["white"],
                     "intensity": 1.0, "strobe_speed": 0.8},
                    {"layer": "position", "recipe": "position", "targets": ["spots"], "aim": "dance floor",
                     "spread": "cross", "intensity": 0.9},
                ]},
            ],
        },
        {
            "title": "Euphoric Dream Wave",
            "description": "brighter pink/white wave with center-out movement and a fast strobe peak",
            "mood_tags": ["euphoric", "dreamy"],
            "bpm": 128,
            "sections": [
                {"name": "Wave", "seconds": 18, "transition_fade_ms": 1200, "looks": [
                    {"layer": "base", "recipe": "color_wash", "targets": ["pars"], "colors": ["pink", "white"],
                     "intensity": 0.75, "rate": "medium", "fade_ms": 1500, "order": "center_out"},
                    {"layer": "movement", "recipe": "circle_wave", "targets": ["spots"], "colors": ["pink"],
                     "intensity": 0.8, "rate": "medium", "size": "big", "order": "outside_in"},
                ]},
                {"name": "Peak", "seconds": 10, "transition_fade_ms": 150, "looks": [
                    {"layer": "strobe", "recipe": "strobe", "targets": ["washes"], "colors": ["white"],
                     "intensity": 1.0, "strobe_speed": 0.95},
                    {"layer": "position", "recipe": "position", "targets": ["spots"], "aim": "dance floor",
                     "spread": "fan", "intensity": 1.0},
                ]},
            ],
        },
    ],
    "notes": "two contrasting shows: a slower lavender/blue bloom-and-strobe, and a brighter pink euphoric wave "
             "with a fast strobe peak.",
    "new_terms": [],
}

# A minimal, schema-valid lightai.design.research.ResearchResult, for the Claude tab's Research box.
CANNED_RESEARCH = {
    "topic": "dreamy nightclub lighting",
    "summary": "Dreamy sets favor slow pastel colour washes and soft strobe accents over hard, fast strobing.",
    "findings": [
        {"title": "Pastel washes dominate", "detail": "Lavender/blue/pink washes with slow crossfades build a "
         "dreamy base layer.", "sources": ["https://example.com/dreamy-lighting"]},
    ],
    "moods": [
        {"name": "dreamy_test", "words": ["dreamy", "ethereal"], "recipes": ["color_wash", "circle_wave"],
         "avoid": ["strobe"], "rate": ["slow"], "fade_ms": [2000], "palette": ["lavender", "blue"],
         "intensity": [0.6, 0.8], "movement_size": ["medium"], "strobe": "accents", "orders": ["center_out"],
         "notes": "slow pastel base with soft accents"},
    ],
    "terms": [{"word": "bloom", "meaning": "a slow build in intensity/colour saturation"}],
    "references": [{"title": "Dreamy nightclub lighting ideas", "url": "https://example.com/dreamy-lighting",
                    "kind": "article"}],
}

# A minimal, schema-valid lightai.design.teacher.TeachResult, for POST /teach/run (label_sessions()
# calls backend.run(kind="label", ...) for any session worth teaching from).
CANNED_TEACH = {
    "items": [
        {"text": "washes at 50%", "intent": "set_level", "marked": "[target:washes] at [intensity:50%]",
         "confidence": 0.95, "reason": "clear correction target", "evidence": []},
    ],
    "new_terms": [],
}

CANNED_BY_KIND = {"design": CANNED_DESIGN, "repair": CANNED_DESIGN, "research": CANNED_RESEARCH, "label": CANNED_TEACH}


class FakeBackend:
    """DesignBackend that streams a few realistic stream-json events (the shape of a real
    claude.exe --output-format stream-json run, see tests/data/claude_stream_schema.jsonl) over
    ~3 seconds, then returns the same ClaudeResult shape lightai.design.backend.build_result
    builds for a real run - so the job pipeline, the run log, and the console's Claude tab all
    see a normal, well-formed run. Every run is also logged through the given RunLog exactly
    like ClaudeCodeBackend does (start/event/finish)."""

    def __init__(self, runlog: RunLog) -> None:
        self.runlog = runlog
        self.calls: list = []

    async def run(self, prompt, *, kind, model, schema=None, system_file=None, replace_system_prompt=False,
                  allowed_tools=(), disallowed_tools=(), agents=None, timeout_s=600.0, budget_usd=None,
                  on_event=None, **kw):
        self.calls.append({"kind": kind, "model": model})
        run_id = self.runlog.start(kind, prompt, model)
        data = CANNED_BY_KIND.get(kind, CANNED_DESIGN)
        text = json.dumps(data)
        events = [
            {"type": "system", "subtype": "init", "cwd": str(LIGHTAI_ROOT), "model": model, "tools": []},
            {"type": "assistant", "message": {"model": model, "role": "assistant", "content": [
                {"type": "text", "text": f"Working on this {kind} request."}]}},
            {"type": "assistant", "message": {"model": model, "role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_fake1", "name": "WebSearch",
                 "input": {"query": "dreamy nightclub lighting"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_fake1",
                 "content": "Dreamy club lighting often uses slow pastel colour washes under soft strobe hits. "
                            "Links: [{\"url\": \"https://example.com/dreamy-lighting\", "
                            "\"title\": \"Dreamy nightclub lighting ideas\"}]"}]}},
            {"type": "assistant", "message": {"model": model, "role": "assistant", "content": [
                {"type": "text", "text": f"Finishing the {kind} request."}]}},
            {"duration_api_ms": 1200, "stop_reason": "end_turn", "total_cost_usd": 0.1,
             "usage": {"input_tokens": 4200, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 500,
                       "output_tokens": 900, "output_tokens_details": {"thinking_tokens": 0},
                       "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0}},
             "is_error": False, "num_turns": 3, "subtype": "success", "result": text,
             "structured_output": data, "type": "result", "duration_ms": 3000},
        ]
        delays = [0.3, 0.5, 0.5, 0.5, 0.5, 0.4]  # one per event above, ~2.7s total: "over ~3 seconds"
        assert len(delays) == len(events)
        for ev, delay in zip(events, delays):
            await asyncio.sleep(delay)
            self.runlog.event(run_id, ev)
            if on_event is not None:
                try:
                    on_event(ev)
                except Exception:
                    pass
        result = build_result(run_id, events, schema)
        self.runlog.finish(run_id, result)
        return result


# ------------------------------------------------------------------ setup helpers
def reset_workdir() -> None:
    for _ in range(3):
        try:
            if WORK.exists():
                shutil.rmtree(WORK, ignore_errors=True)
            break
        except OSError:
            time.sleep(1.0)
    SHOW_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAVEFILE / "Main Project.qxw", SHOW_DIR / "Main Project.qxw")
    stage = SAVEFILE / "Main Project.stage.json"
    if stage.exists():
        shutil.copy(stage, SHOW_DIR / "Main Project.stage.json")


def build_config():
    cfg = copy.copy(load_config())
    cfg.project_path = SHOW_DIR / "Main Project.qxw"
    cfg.main_project_path = None
    cfg.data_dir = WORK / "data"
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.learned_path = WORK / "learned.yaml"
    cfg.reload_strategy = "auto"
    return cfg


def start_uvicorn(app):
    import uvicorn

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    t0 = time.time()
    while time.time() - t0 < 20:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=1) as resp:
                if resp.status == 200:
                    return server, thread
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("lightai API server did not start within 20s")


def stop_uvicorn(server, thread) -> None:
    server.should_exit = True
    thread.join(timeout=10)


def launch_chrome():
    user_data = WORK / "chrome"
    user_data.mkdir(parents=True, exist_ok=True)
    args = [str(CHROME), "--start-maximized", f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={user_data}", "--no-first-run", "--no-default-browser-check", BASE + "/"]
    return subprocess.Popen(args)


def wait_chrome_ready(timeout: float = 20.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def find_page_target() -> Optional[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=5) as resp:
        targets = json.loads(resp.read().decode("utf-8"))
    for t in targets:
        if t.get("type") == "page":
            return t
    return targets[0] if targets else None


# ------------------------------------------------------------------ a tiny CDP client
class CDP:
    def __init__(self) -> None:
        self.ws = None
        self._id = 0
        self._pending: dict = {}
        self._reader_task = None
        self.console_errors: list = []
        self.exceptions: list = []

    async def connect(self, ws_url: str) -> None:
        self.ws = await websockets.connect(ws_url, max_size=None, ping_interval=None)
        self._reader_task = asyncio.create_task(self._reader())
        await self.send("Page.enable")
        await self.send("Runtime.enable")

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                else:
                    self._on_event(msg)
        except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
            pass

    def _on_event(self, msg: dict) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "Page.javascriptDialogOpening":
            # Belt-and-suspenders alongside overriding window.confirm in the page itself: a native
            # dialog (confirm/alert/prompt) that slips through freezes the renderer's main thread,
            # which then makes every later Runtime.evaluate hang until our own send() timeout fires.
            asyncio.create_task(self._accept_dialog())
            return
        if method == "Runtime.exceptionThrown":
            d = params.get("exceptionDetails") or {}
            exc = d.get("exception") or {}
            text = exc.get("description") or exc.get("value") or d.get("text") or json.dumps(d)[:500]
            self.exceptions.append(str(text))
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            args = params.get("args") or []
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in args)
            self.console_errors.append(text)

    async def _accept_dialog(self) -> None:
        try:
            await self.send("Page.handleJavaScriptDialog", accept=True)
        except Exception:
            pass

    async def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        return await asyncio.wait_for(fut, timeout=30)

    async def eval(self, expression: str, await_promise: bool = False):
        msg = await self.send("Runtime.evaluate", expression=expression, returnByValue=True,
                              awaitPromise=await_promise)
        payload = msg.get("result", {})
        if "exceptionDetails" in payload:
            raise RuntimeError(json.dumps(payload["exceptionDetails"])[:800])
        return (payload.get("result") or {}).get("value")

    async def screenshot(self, path: Path) -> None:
        msg = await self.send("Page.captureScreenshot", format="png")
        data = (msg.get("result") or {}).get("data")
        if data:
            path.write_bytes(base64.b64decode(data))

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass


async def click(cdp: CDP, selector: str) -> bool:
    return bool(await cdp.eval(
        "(() => { const el = document.querySelector(%s); if (!el) return false; el.click(); return true; })()"
        % json.dumps(selector)))


async def set_value(cdp: CDP, selector: str, value: str) -> bool:
    return bool(await cdp.eval(
        "(() => { const el = document.querySelector(%s); if (!el) return false; el.value = %s; return true; })()"
        % (json.dumps(selector), json.dumps(value))))


async def exists(cdp: CDP, selector: str) -> bool:
    return bool(await cdp.eval("!!document.querySelector(%s)" % json.dumps(selector)))


async def count_of(cdp: CDP, selector: str) -> int:
    return int(await cdp.eval("document.querySelectorAll(%s).length" % json.dumps(selector)) or 0)


async def wait_for(cdp: CDP, expr: str, timeout: float = 20.0, interval: float = 0.3):
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout:
        try:
            last = await cdp.eval(expr)
        except (RuntimeError, TimeoutError):
            last = None
        if last:
            return last
        await asyncio.sleep(interval)
    return last


# ------------------------------------------------------------------ the browser-driven checks
async def browser_checks(fake: FakeQlc) -> bool:
    chrome_proc = launch_chrome()
    cdp = CDP()
    try:
        if not wait_chrome_ready():
            report("Chrome DevTools port ready", False, f"timed out waiting for :{CDP_PORT}/json/version")
            return False
        target = find_page_target()
        if not target:
            report("Chrome page target found", False)
            return False
        await cdp.connect(target["webSocketDebuggerUrl"])
        await cdp.eval("window.confirm = () => true; true;")
        report("attached to Chrome over CDP; window.confirm overridden", True)

        loaded = bool(await wait_for(cdp, "!!document.querySelector('.tabbtn')", timeout=15))
        report("console page loaded (tab bar present)", loaded)
        if not loaded:
            return False
        await asyncio.sleep(1.0)

        # ---- Design tab: start a job ----
        await click(cdp, '.tabbtn[data-tab="design"]')
        opened = bool(await wait_for(cdp, "!document.getElementById('tab-design').classList.contains('hidden')", timeout=5))
        report("Design tab opened", opened)
        await asyncio.sleep(1.0)

        await set_value(cdp, "#designText", DESIGN_REQUEST)
        await click(cdp, "#designGo")
        report("typed a design request and clicked 'Design with Claude'", True)
        await asyncio.sleep(1.0)

        status = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            status = await cdp.eval(
                "(function(){var e=document.getElementById('designJobStatus'); return e ? e.textContent : '';})()")
            if status in ("done", "error"):
                break
            await asyncio.sleep(0.5)
        report("design job reached status 'done'", status == "done", f"status={status!r}")
        await asyncio.sleep(1.0)

        n_cards = await count_of(cdp, "#designPreviews .card")
        report("preview shows 2 shows", n_cards == 2, f"found {n_cards} card(s)")
        n_rows = await count_of(cdp, '#designPreviews .card[data-idx="0"] table tr')
        report("show 1's preview card has a sections table", n_rows > 1, f"{n_rows} row(s) incl. header")
        await asyncio.sleep(1.0)

        # ---- Apply show 1 ----
        await click(cdp, '#designPreviews .card[data-idx="0"] [data-act="apply"]')
        applied = bool(await wait_for(
            cdp, "!!document.querySelector('#designPreviews .card[data-idx=\"0\"] .applied-badge')", timeout=15))
        report("Apply on show 1 succeeded (applied badge shown)", applied)
        await asyncio.sleep(1.0)

        shows, main_id = [], None
        try:
            with urllib.request.urlopen(BASE + "/design/shows", timeout=5) as resp:
                shows = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            report("/design/shows lists the applied show", False, repr(exc))
        else:
            titles = [s.get("title") for s in shows]
            report("/design/shows lists the applied show", "Dreamy Strobe Bloom" in titles, f"titles={titles}")
            main_id = next((s["main_id"] for s in shows if s.get("title") == "Dreamy Strobe Bloom"), None)
        await asyncio.sleep(1.0)

        # ---- Run / Stop ----
        if main_id is not None:
            await click(cdp, '#designPreviews .card[data-idx="0"] [data-act="run"]')
            running, t0 = False, time.monotonic()
            while time.monotonic() - t0 < 5:
                running = bool((fake.functions.get(main_id) or {}).get("running"))
                if running:
                    break
                await asyncio.sleep(0.2)
            report("Run started the show in QLC+ (fake)", running, f"main_id={main_id}")
            await asyncio.sleep(1.0)

            await click(cdp, '#designPreviews .card[data-idx="0"] [data-act="stop"]')
            stopped, t0 = False, time.monotonic()
            while time.monotonic() - t0 < 5:
                stopped = not bool((fake.functions.get(main_id) or {}).get("running"))
                if stopped:
                    break
                await asyncio.sleep(0.2)
            report("Stop stopped the show in QLC+ (fake)", stopped, f"main_id={main_id}")
        else:
            report("Run started the show in QLC+ (fake)", False, "no main_id resolved")
            report("Stop stopped the show in QLC+ (fake)", False, "no main_id resolved")
        await asyncio.sleep(1.0)

        # ---- Preview in 3D (sandbox): endpoints may not exist yet (added concurrently by another
        # agent) - non-blocking per the coordinator's instruction: just check the console handles a
        # missing/erroring endpoint gracefully (no uncaught JS exception / console.error), and don't
        # fail the run if the feature itself isn't wired server-side yet.
        before_js_errs = len(cdp.exceptions) + len(cdp.console_errors)
        have_btn = await exists(cdp, '#designPreviews .card[data-idx="0"] [data-act="preview3d"]')
        if have_btn:
            await click(cdp, '#designPreviews .card[data-idx="0"] [data-act="preview3d"]')
            await asyncio.sleep(2.0)
        after_js_errs = len(cdp.exceptions) + len(cdp.console_errors)
        sandbox_ok = have_btn and after_js_errs == before_js_errs
        sandbox_detail = "" if sandbox_ok else ("button missing" if not have_btn else "a JS error/exception was raised")
        report("'Preview in 3D (sandbox)' button present and handled without a JS error", sandbox_ok, sandbox_detail)
        await asyncio.sleep(1.0)

        # ---- Claude tab ----
        await click(cdp, '.tabbtn[data-tab="claude"]')
        await asyncio.sleep(0.5)
        n_runs = await wait_for(cdp, "document.querySelectorAll('#claudeRunsBody tr[data-i]').length", timeout=10)
        report("Claude tab shows at least one run row", bool(n_runs) and n_runs >= 1, f"rows={n_runs}")
        await asyncio.sleep(1.0)

        await click(cdp, '#claudeRunsBody tr[data-i="0"]')
        timeline_text = await wait_for(
            cdp,
            "(function(){var d=document.getElementById('claudeRunDetail'); "
            "if (!d || d.classList.contains('hidden')) return ''; "
            "var t=document.getElementById('claudeTimeline'); return t ? t.textContent : ''; })()",
            timeout=10)
        low = (timeline_text or "").lower()
        has_search = "search" in low and "dreamy nightclub lighting" in low
        report("run timeline opened and shows a search step", has_search, (timeline_text or "")[:200])
        await asyncio.sleep(1.0)

        # ---- Research box ----
        await set_value(cdp, "#researchTopic", "dreamy nightclub lighting")
        await click(cdp, "#researchGo")
        research_status = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20:
            research_status = await cdp.eval(
                "(function(){var e=document.getElementById('researchStatus'); return e ? e.textContent : '';})()")
            if research_status in ("done", "error"):
                break
            await asyncio.sleep(0.5)
        report("research job reached status 'done'", research_status == "done", f"status={research_status!r}")
        research_body = await cdp.eval(
            "(function(){var e=document.getElementById('researchBody'); return e ? e.textContent : '';})()")
        has_report = bool(research_body and "report:" in research_body.lower())
        report("research result shows a report path", has_report, (research_body or "")[:200])
        await asyncio.sleep(1.0)

        # ---- Command tab: plan "washes at 50%" ----
        await click(cdp, '.tabbtn[data-tab="command"]')
        await asyncio.sleep(0.5)
        await set_value(cdp, "#cmd", "washes at 50%")
        await click(cdp, "#go")
        plan_json = await wait_for(
            cdp,
            "(function(){var p=document.getElementById('plan'); if (!p || p.classList.contains('hidden')) return ''; "
            "var j=document.getElementById('json'); return j ? j.textContent : ''; })()",
            timeout=10)
        plan_id = None
        if plan_json:
            try:
                plan_id = (json.loads(plan_json).get("plan") or {}).get("plan_id")
            except (ValueError, TypeError):
                pass
        report("planned 'washes at 50%' in the Command tab", bool(plan_id), f"plan_id={plan_id}")
        await asyncio.sleep(1.0)

        # ---- History tab: "What I meant..." -> "the spots" ----
        await click(cdp, '.tabbtn[data-tab="history"]')
        await asyncio.sleep(0.5)
        found_row = False
        if plan_id:
            found_row = bool(await wait_for(
                cdp, "!!document.querySelector('.hitem[data-planid=\"%s\"]')" % plan_id, timeout=10))
        report("the planned command appears as a row in History", found_row, f"plan_id={plan_id}")
        if found_row:
            await click(cdp, '[data-meant="%s"]' % plan_id)
            await asyncio.sleep(0.3)
            await set_value(cdp, "#meantinput-%s" % plan_id, "the spots")
            await click(cdp, '[data-meantgo="%s"]' % plan_id)
            # /correct returns a NEW plan (its own plan_id, distinct from the row being corrected) and
            # console.html's renderHistory() re-fetches /history and renders the result into that NEW
            # row's own correctout-<id> div - so find the newest history row first, then read its div.
            new_plan_id = await wait_for(
                cdp,
                "(function(){var els=document.querySelectorAll('.hitem[data-planid]'); if (!els.length) return ''; "
                "var pid=els[els.length-1].getAttribute('data-planid'); return pid === '%s' ? '' : pid;})()" % plan_id,
                timeout=10)
            correct_text = ""
            if new_plan_id:
                correct_text = await wait_for(
                    cdp,
                    "(function(){var e=document.getElementById('correctout-%s'); return e ? e.textContent : '';})()"
                    % new_plan_id,
                    timeout=10)
            mentions_spots = bool(correct_text and "spot" in correct_text.lower())
            report("'What I meant... the spots' produced a new plan mentioning spots", mentions_spots,
                   (correct_text or "")[:200])
        else:
            report("'What I meant... the spots' produced a new plan mentioning spots", False, "history row not found")
        await asyncio.sleep(1.0)

        # ---- Teaching: "Teach now" on the Claude tab, using the correction just made above ----
        await click(cdp, '.tabbtn[data-tab="claude"]')
        await asyncio.sleep(0.5)
        await click(cdp, "#teachRunBtn")
        teach_msg = await wait_for(cdp, "(function(){var e=document.getElementById('teachRunMsg'); "
                                         "return e ? e.textContent : '';})()", timeout=15)
        report("'Teach now' reports session/labeled counts", bool(teach_msg) and "sessions" in (teach_msg or "").lower(),
               teach_msg or "")
        await asyncio.sleep(1.0)
        corrections_html = await wait_for(
            cdp, "(function(){var e=document.getElementById('correctionsList'); return e ? e.innerHTML : '';})()",
            timeout=10)
        has_correction_row = bool(corrections_html and "washes" in corrections_html.lower())
        report("Recent corrections (Claude) lists the labeled example", has_correction_row, (corrections_html or "")[:200])
        await asyncio.sleep(1.0)

        # ---- Rotate designed shows through the night (FakeQlc speaks the fork's LOOP|SET/INTERVAL/START/STOP) ----
        await click(cdp, "#tabbar .tabbtn[data-tab=\"design\"]")
        await asyncio.sleep(0.5)
        before_js_errs2 = len(cdp.exceptions) + len(cdp.console_errors)
        await click(cdp, "#rotateStart")
        rotate_msg = await wait_for(cdp, "(function(){var e=document.getElementById('rotateMsg'); "
                                          "return e && /rotating|not started/.test(e.textContent) ? e.textContent : '';})()", timeout=10)
        report("Start rotation rotates the designed shows", bool(rotate_msg) and "rotating" in rotate_msg, f"msg={rotate_msg!r}")
        await asyncio.sleep(1.5)
        await click(cdp, "#rotateStop")
        rotate_stop_msg = await wait_for(cdp, "(function(){var e=document.getElementById('rotateMsg'); "
                                               "return e && /stopped/.test(e.textContent) ? e.textContent : '';})()", timeout=10)
        after_js_errs2 = len(cdp.exceptions) + len(cdp.console_errors)
        report("Stop rotation stops it, without a JS error", bool(rotate_stop_msg) and after_js_errs2 == before_js_errs2,
               f"stop_msg={rotate_stop_msg!r}")
        try:  # the rotation's history row once made every later /history call fail
            with urllib.request.urlopen(BASE + "/history", timeout=5) as resp:
                hist_ok = resp.status == 200 and any("rotate shows" in (r.get("text") or "") for r in json.loads(resp.read()))
        except Exception as exc:  # noqa: BLE001
            hist_ok, rotate_stop_msg = False, f"{rotate_stop_msg!r}; /history: {exc}"
        report("History still loads after a rotation and lists it", hist_ok)
        await asyncio.sleep(1.0)

    except Exception as exc:  # keep going to teardown; record the failure
        report("browser checks completed without a fatal exception", False, repr(exc))
    finally:
        if cdp.exceptions:
            report("no uncaught JS exceptions during the run", False, "; ".join(cdp.exceptions[:3]))
        else:
            report("no uncaught JS exceptions during the run", True)
        if cdp.console_errors:
            report("no console.error output during the run", False, "; ".join(cdp.console_errors[:3]))
        else:
            report("no console.error output during the run", True)

        overall_ok = bool(RESULTS) and all(ok for _, ok, _ in RESULTS)
        if not overall_ok and cdp.ws is not None:
            try:
                WORK.mkdir(parents=True, exist_ok=True)
                await cdp.screenshot(FAIL_SHOT)
                print(f"Saved failure screenshot to {FAIL_SHOT}", flush=True)
            except Exception as exc:
                print(f"(could not save failure screenshot: {exc!r})", flush=True)

        print("Pausing 5s before closing Chrome...", flush=True)
        await asyncio.sleep(5.0)
        await cdp.close()
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(chrome_proc.pid)], capture_output=True, timeout=10)
        except Exception:
            pass

    return bool(RESULTS) and all(ok for _, ok, _ in RESULTS)


# ------------------------------------------------------------------ orchestration
def setup_and_run() -> bool:
    if not CHROME.is_file():
        return report("Chrome executable found", False, str(CHROME))

    print("Resetting the isolated work directory...", flush=True)
    reset_workdir()

    real_model = load_config().current_model_dir()
    if real_model is None:
        return report("a trained/promoted lightai model exists", False,
                      "run `lightai train --promote-if-better` first")

    from lightai.rig.model import Rig

    cfg = build_config()
    fake = FakeQlc(cfg.project_path, fork=True)
    fake.start()
    cfg.qlc_url = fake.url

    server = thread = None
    try:
        rig = Rig.load(cfg)
        errors, _warnings, _resolved = validate_design(rig, DesignResult.model_validate(CANNED_DESIGN))
        if not report("canned design validates against the copied main show", not errors, "; ".join(errors)):
            return False

        from lightai.api.server import create_app
        from lightai.app import LightAI

        # the Research box's canned run writes a report and merges moods.yaml: keep it in the work dir, never the repo
        cfg.knowledge_dir = WORK / "knowledge"

        ai = LightAI(cfg, model_dir=real_model, use_embeddings=False)
        runlog = RunLog(cfg.data_dir)
        backend = FakeBackend(runlog)
        app = create_app(ai, design_backend=backend)

        server, thread = start_uvicorn(app)
        report("lightai API server started", True, BASE)

        return asyncio.run(browser_checks(fake))
    finally:
        if server is not None:
            try:
                stop_uvicorn(server, thread)
            except Exception:
                pass
        try:
            fake.stop()
        except Exception:
            pass


def main() -> int:
    try:
        ok = setup_and_run()
    except Exception as exc:
        report("harness completed without a fatal exception", False, repr(exc))
        ok = False
    passed = sum(1 for _, ok2, _ in RESULTS if ok2)
    print(f"\n{passed}/{len(RESULTS)} checks passed.", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
