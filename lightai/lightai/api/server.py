"""Local HTTP API + booth console (127.0.0.1:8765 by default).

  GET  /                  booth console page
  POST /parse             {text} -> LightCommand
  POST /plan              {text} -> {command, plan}
  POST /execute           {plan_id, confirm, allow_running_reload} -> results
  POST /preview           {plan_id} -> play a create_look plan's preview live
  POST /apply_proposal    {plan_id} -> save a previewed proposal as a real look (returns a new plan)
  POST /feedback          OutcomeFeedback -> what was learned
  POST /teach             {text, intent, marked} -> save a corrected example
  POST /bpm               {bpm} or {tap: true}
  POST /calibrate         {answer}
  POST /stop              stop previews/calibration
  GET  /rig /functions?q= /health /labels /plan/{id}
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from lightai.app import LightAI
from lightai.exec.wsclient import QlcError
from lightai.nlu.text import parse_markup, split_words
from lightai.schema import OutcomeFeedback, labels

LOOPBACK_HOSTS = ["127.0.0.1", "localhost"]
MAX_BODY = 256 * 1024  # no request needs more; a 50 MB body used to stall live commands

CONSOLE = Path(__file__).with_name("console.html")


class TextIn(BaseModel):
    text: str = Field(max_length=500)


class ExecIn(BaseModel):
    plan_id: str = Field(max_length=64)
    confirm: bool = False
    allow_running_reload: bool = False
    allow_other_show: bool = False


class PlanRef(BaseModel):
    plan_id: str = Field(max_length=64)


class TeachIn(BaseModel):
    text: str = Field(max_length=500)
    intent: str = Field(max_length=64)
    marked: str = Field(max_length=2000)
    predicted: Optional[dict] = None


class BpmIn(BaseModel):
    bpm: Optional[float] = Field(None, ge=40, le=220, allow_inf_nan=False)
    tap: bool = False


class OpenShowIn(BaseModel):
    force: bool = False  # discard unsaved changes in QLC+
    start_if_closed: bool = True


class NewShowIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    force: bool = False  # discard unsaved changes in QLC+


class AnswerIn(BaseModel):
    answer: str = Field(max_length=200)


def create_app(state: Optional[LightAI] = None, cors_origins: Optional[list] = None, allowed_hosts: Optional[list] = None,
               token: Optional[str] = None) -> FastAPI:
    """allowed_hosts: Host header values accepted (DNS-rebinding guard); token: required X-LightAI-Token on every change."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ai()
        app.state.bg = asyncio.create_task(usage_logger())
        try:
            yield
        finally:
            app.state.bg.cancel()
            ex = app.state.ai.executor
            await ex.stop_preview()
            if app.state.ai.session.calibration is not None:
                try:
                    await ex.calibrate_end("server shutting down")
                except Exception:
                    pass
            await ex.client.close()

    app = FastAPI(title="lightai", version="0.1", lifespan=lifespan)
    hosts = list(allowed_hosts or LOOPBACK_HOSTS)
    origins = set(cors_origins or [])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        try:
            size = int(request.headers.get("content-length") or 0)
        except ValueError:
            size = 0
        if size > MAX_BODY:
            return JSONResponse(status_code=413, content={"detail": f"request body over {MAX_BODY // 1024} KB"})
        if request.headers.get("sec-fetch-site") == "cross-site" and request.url.path != "/":
            return JSONResponse(status_code=403, content={"detail": "cross-site requests are not allowed"})
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")  # browsers always send it on cross-site POSTs
            if origin is not None and origin not in origins and "*" not in hosts and urlsplit(origin).hostname not in hosts:
                return JSONResponse(status_code=403, content={"detail": f"requests from {origin} are not allowed"})
            if token and not hmac.compare_digest(request.headers.get("x-lightai-token") or "", token):
                return JSONResponse(status_code=401, content={"detail": "missing or wrong X-LightAI-Token header"})
        return await call_next(request)

    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.state.ai = state

    @app.exception_handler(RequestValidationError)
    async def bad_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [{k: v for k, v in e.items() if k not in ("input", "ctx", "url")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errs})

    @app.exception_handler(QlcError)
    async def qlc_down(request: Request, exc: QlcError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": f"QLC+ not reachable: {exc}"})

    def ai() -> LightAI:
        if app.state.ai is None:
            app.state.ai = LightAI()
        return app.state.ai

    async def usage_logger() -> None:
        path = ai().cfg.data_dir / "usage.jsonl"

        def on_push(msg: str) -> None:
            if not msg.startswith("FUNCTION|"):
                return
            parts = msg.split("|")
            if len(parts) < 3:
                return
            try:
                fid = int(parts[1])
            except ValueError:
                return
            f = ai().rig.functions.get(fid)
            row = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "fid": fid, "state": parts[2],
                   "name": f.name if f else None, "path": f.path if f else None, "priority": f.priority if f else None,
                   "bpm": ai().session.bpm}
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")

        ai().executor.client.subscribe(on_push)  # the client keeps its subscribers across reconnects
        while True:
            try:
                await ai().executor.ensure_client()
            except Exception:
                pass
            try:
                swapped = await asyncio.to_thread(ai().maybe_swap_model)
                if swapped:
                    print(f"[lightai] switched to model {swapped}")
            except Exception as exc:
                print(f"[lightai] model swap failed, keeping {ai().model.version}: {exc}")
            await asyncio.sleep(30)

    @app.get("/", response_class=HTMLResponse)
    async def console() -> str:
        return CONSOLE.read_text(encoding="utf-8")

    @app.post("/parse")
    async def parse(body: TextIn) -> dict:
        return ai().parse(body.text).model_dump()

    @app.post("/plan")
    async def plan(body: TextIn) -> dict:
        await follow_qlc()
        cmd, p = ai().plan(body.text)
        if p.mode == "structural" and any(a.op == "reload" for a in p.actions):
            try:
                s = await show_info()
            except Exception:  # the warning is a courtesy; the executor checks again before writing
                s = {}
            if s.get("running") and s.get("fork_version") and not s.get("follows"):  # QLC+ shows another show than lightai edits
                what = f"'{Path(s['open']).name}'" if s.get("open") else "a new, unsaved show"
                name = Path(ai().cfg.project_path).name
                p.warnings.insert(0, f"QLC+ has {what} open, but this changes {name}. Applying it opens {name} in QLC+ in place of that show.")
        history_log({"plan_id": p.plan_id, "text": body.text, "intent": cmd.intent, "confidence": round(cmd.confidence, 3),
                     "mode": p.mode, "summary": p.summary[:300], "show": Path(p.show).name if p.show else None})
        return {"command": cmd.model_dump(), "plan": p.model_dump()}

    def get_plan(pid: str):
        p = ai().session.get(pid)
        if p is None:
            raise HTTPException(404, f"unknown plan {pid}")
        return p

    @app.get("/plan/{pid}")
    async def plan_get(pid: str) -> dict:
        return get_plan(pid).model_dump()

    @app.post("/execute")
    async def execute(body: ExecIn) -> dict:
        res = await ai().executor.execute(get_plan(body.plan_id), confirm=body.confirm, allow_running_reload=body.allow_running_reload,
                                            allow_other_show=body.allow_other_show)
        why = next((r.get("note") or r.get("error") for r in res.get("results") or [] if r.get("note") or r.get("error")), None)
        history_log({"plan_id": body.plan_id, "executed": bool(res.get("ok")),
                     "note": str(res.get("clarify") or ("needs confirmation" if res.get("needs_confirmation") else "") or why or "")[:200]})
        return res

    def history_log(row: dict) -> None:
        """Every command typed and what came of it: C:\\lightai-data\\history.jsonl (5 MB, then history.1.jsonl)."""
        path = ai().cfg.data_dir / "history.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 5_000_000:
                path.replace(path.with_name("history.1.jsonl"))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @app.get("/history")
    async def history(limit: int = Query(50, ge=1, le=500)) -> list:
        """The last commands typed (newest last), each with its plan and, once run, whether it worked."""
        path = ai().cfg.data_dir / "history.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines()[-limit * 3:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        done = {r["plan_id"]: r for r in rows if "executed" in r}
        out = [dict(r, executed=done[r["plan_id"]]["executed"], result=done[r["plan_id"]]["note"]) if r.get("plan_id") in done else r
               for r in rows if "text" in r]
        return out[-limit:]

    @app.post("/preview")
    async def preview(body: PlanRef) -> dict:
        p = get_plan(body.plan_id)
        pv = ai().planner.preview_for(p)
        if not pv:
            raise HTTPException(400, "this plan has no preview")
        from lightai.schema import Action, Plan

        prev = Plan(plan_id=ai().session.new_plan_id(), intent="preview", mode="live", summary=f"preview of {p.plan_id}",
                    actions=[Action(op="preview", args=pv)], look=p.look, followup={"of": p.plan_id})
        ai().session.store(prev)
        ai().session.last_action_plan = p
        res = await ai().executor.execute(prev)
        res["preview_of"] = p.plan_id
        return res

    @app.post("/apply_proposal")
    async def apply_proposal(body: PlanRef) -> dict:
        p = get_plan(body.plan_id)
        if not (p.followup or {}).get("apply"):
            raise HTTPException(400, "not a proposal")
        return {"plan": ai().planner.apply_proposal(p).model_dump()}

    @app.post("/feedback")
    async def feedback(body: OutcomeFeedback) -> dict:
        get_plan(body.plan_id)  # 404 for unknown or expired plans
        bad = [i.fixture_id for i in body.issues if i.fixture_id is not None and i.fixture_id not in ai().rig.fixtures]
        if bad:
            raise HTTPException(422, f"no fixture {bad[0]} in this show")
        rated = ai().session.rated
        fp = json.dumps([body.plan_id, body.verdict, [i.model_dump() for i in body.issues], body.free_text], sort_keys=True)
        if time.time() - rated.get(fp, 0.0) < 3.0:  # a double click counts once; a deliberate repeat later counts again
            return {"verdict": body.verdict, "duplicate": True, "routed_to": [], "changes": [], "followups": [],
                    "note": "this feedback was just recorded for this plan"}
        rated[fp] = time.time()
        if len(rated) > 1000:
            for k in list(rated)[:200]:
                rated.pop(k, None)
        try:
            res = await ai().executor.apply_feedback(body)
        except Exception:
            rated.pop(fp, None)  # nothing was learned: a retry must not be called a duplicate
            raise
        tf = ai().planner.taste_followup(ai().session.get(body.plan_id), [i.model_dump() for i in body.issues])
        if tf.get("adjusted_plan_id"):
            res["adjusted_proposal"] = {"plan_id": tf["adjusted_plan_id"], "changes": tf["notes"]}
        if tf.get("update_plan"):
            res["update_plan"] = tf["update_plan"].model_dump()
        return res

    @app.post("/teach")
    async def teach(body: TeachIn) -> dict:
        L = labels()
        if body.intent not in L["intents"]:
            raise HTTPException(400, f"unknown intent {body.intent}")
        try:
            text, words, tags, spans = parse_markup(body.marked)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        bad = [s["slot"] for s in spans if s["slot"] not in L["slots"]]
        if bad:
            raise HTTPException(400, f"unknown slots {bad}")
        if not words:
            raise HTTPException(400, "the marked sentence is empty")
        if [w.lower() for w in words] != [w.lower() for w in split_words(body.text)]:
            raise HTTPException(400, "the marked sentence does not have the same words as the text")
        return ai().teach(body.text, body.predicted or {}, {"intent": body.intent, "marked": body.marked})

    @app.post("/bpm")
    async def bpm(body: BpmIn) -> dict:
        s = ai().session
        if body.tap:
            s.tap()
        elif body.bpm:
            s.set_bpm(body.bpm, "typed")
        return {"bpm": s.bpm, "source": s.bpm_source, "taps": len(s.taps)}

    @app.post("/calibrate")
    async def calibrate(body: AnswerIn) -> dict:
        if ai().session.calibration is None:
            raise HTTPException(409, "no calibration running")
        return await ai().executor.calibrate_answer(body.answer)

    @app.post("/stop")
    async def stop() -> dict:
        ex = ai().executor
        await ex.stop_preview()
        if ai().session.calibration:
            return await ex.calibrate_end("stopped")
        return {"stopped": True}

    async def show_info() -> dict:
        from lightai.exec.http import fork_version, project_file

        main = ai().cfg.main_show()
        active = Path(ai().cfg.project_path)
        launcher = Path(ai().cfg.qlc_launcher)
        try:
            c = await ai().executor.ensure_client()
        except QlcError:
            return {"running": False, "main": str(main), "active": str(active), "active_is_main": ai().cfg.is_main_show(),
                    "launcher": str(launcher) if launcher.exists() else None}
        ver = await fork_version(c)
        info = await project_file(c) if ver else None
        open_path = (info or {}).get("path") or None
        is_main = bool(open_path) and Path(open_path).resolve() == main.resolve()
        follows = bool(open_path) and Path(open_path).resolve() == active.resolve()
        return {"running": True, "open": open_path, "modified": (info or {}).get("modified"), "main": str(main),
                "is_main": is_main, "active": str(active), "active_is_main": ai().cfg.is_main_show(), "follows": follows,
                "fork_version": ver, "can_open": ver >= 2}

    follow_state = {"t": 0.0}

    async def follow_qlc(force: bool = False) -> None:
        """lightai edits the show QLC+ has open when that show is a saved .qxw file (a new, unsaved show has no file)."""
        from lightai.exec.http import project_file, supports_fork_commands

        now = time.monotonic()
        if not force and now - follow_state["t"] < 2.0:
            return
        follow_state["t"] = now
        ex = ai().executor
        if ex._struct_lock.locked():  # never swap the rig under a running change
            return
        try:
            c = await ex.ensure_client()
            if not await supports_fork_commands(c):
                return
            info = await project_file(c)
        except QlcError:
            return
        path = (info or {}).get("path")
        if not (path and path.lower().endswith(".qxw") and Path(path).is_file()):
            return
        if Path(path).resolve() == Path(ai().cfg.project_path).resolve():
            return
        await ex.stop_preview()
        if ai().session.calibration is not None:
            await ex.calibrate_end("QLC+ switched shows")
        try:
            ai().switch_show(Path(path))
        except Exception as exc:  # a show lightai can't read: keep editing the current one
            print(f"[lightai] can't edit {path}: {exc}", flush=True)

    @app.get("/qlc/show")
    async def qlc_show() -> dict:
        await follow_qlc(force=True)
        return await show_info()

    async def open_in_qlc(target: Path, force: bool = False, start_if_closed: bool = True) -> dict:
        """Open a show file in QLC+ (starting QLC+ with the usual launcher when it is closed); lightai edits it after."""
        from lightai.exec.http import open_project_file

        ex = ai().executor
        info = await show_info()
        started = False
        if not info["running"]:
            launcher = Path(ai().cfg.qlc_launcher)
            if not start_if_closed or not launcher.exists():
                raise HTTPException(503, "QLC+ is not running" + ("" if launcher.exists() else f" and there is no launcher at {launcher}"))
            subprocess.Popen(["cmd.exe", "/c", str(launcher)], cwd=str(launcher.parent), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started = True
            for _ in range(60):
                await asyncio.sleep(0.5)
                ex.client._down_until = 0.0  # retry now instead of after the fail-fast pause
                info = await show_info()
                if info["running"]:
                    break
            else:
                raise HTTPException(504, "QLC+ was started but its web access did not answer within 30 s")
        if info.get("open") and Path(info["open"]).resolve() == Path(target).resolve():
            if not ai().switch_show(target):
                ai().reload_rig()
            return {"ok": True, "started": started, "already": not started, "open": info["open"],
                    "note": f"QLC+ started with {Path(target).name}" if started else f"{Path(target).name} is already open in QLC+"}
        if not info.get("can_open"):
            raise HTTPException(409, "this QLC+ build can't switch shows from lightai; install the current fork build "
                                     "(lightai\\tools\\install-fork-build.ps1 while QLC+ is closed)")
        if info.get("modified") and not force:
            shown = Path(info["open"]).name if info.get("open") else "a new, unsaved show"
            return {"ok": False, "needs_confirmation": True, "open": info.get("open"),
                    "note": f"QLC+ has unsaved changes in '{shown}'. Open {Path(target).name} anyway? Those changes would be lost."}
        await ex.stop_preview()
        if ai().session.calibration is not None:
            await ex.calibrate_end("QLC+ is switching shows")
        async with ex._struct_lock:
            res = await open_project_file(ex.client, Path(target), force=force or bool(info.get("modified")))
        ai().session.overridden.clear()
        ok = bool(res.get("opened") and res.get("loaded"))
        if ok and not ai().switch_show(target):
            ai().reload_rig()
        return {"ok": ok, "started": started, "open": str(target), **res,
                "note": f"opened {Path(target).name} in QLC+" if ok else res.get("note", "QLC+ did not confirm the load")}

    @app.post("/qlc/new-show")
    async def qlc_new_show(body: NewShowIn) -> dict:
        """An empty show next to the main show (its DMX outputs, no fixtures), opened in QLC+; lightai edits it after."""
        from lightai.rig.newshow import create_empty_show, show_path

        main = ai().cfg.main_show()
        try:
            dest = show_path(main.parent, body.name)
        except ValueError as exc:
            raise HTTPException(422, f"show name: {exc}")
        if dest.exists():
            raise HTTPException(409, f"a show called '{dest.stem}' already exists; pick another name")
        info = await show_info()
        if info["running"] and not info.get("can_open"):
            raise HTTPException(409, "this QLC+ build can't switch shows from lightai; install the current fork build")
        if info["running"] and info.get("modified") and not body.force:
            shown = Path(info["open"]).name if info.get("open") else "a new, unsaved show"
            return {"ok": False, "needs_confirmation": True, "open": info.get("open"),
                    "note": f"QLC+ has unsaved changes in '{shown}'. Open the new show anyway? Those changes would be lost."}
        create_empty_show(main, dest)
        try:
            res = await open_in_qlc(dest, force=body.force, start_if_closed=True)
        except HTTPException:
            dest.unlink(missing_ok=True)  # nothing opened it: the name stays free
            raise
        if res.get("ok"):
            res["note"] = f"created {dest.name} (no fixtures; the main show's DMX outputs) and opened it in QLC+. lightai edits it now."
        return {**res, "created": str(dest)}

    @app.post("/qlc/open-show")
    async def qlc_open_show(body: OpenShowIn) -> dict:
        """Open the main show in QLC+ (starting QLC+ when it is closed); lightai edits the main show after."""
        res = await open_in_qlc(ai().cfg.main_show(), force=body.force, start_if_closed=body.start_if_closed)
        if res.get("ok") and res.get("already"):
            res["note"] = "the main show is already open in QLC+"
        return res

    @app.get("/rig")
    async def rig() -> dict:
        return ai().rig.summary()

    @app.get("/functions")
    async def functions(q: str = Query("", max_length=200), k: int = Query(10, ge=1, le=50)) -> dict:
        if not q:
            return {"results": [{"id": f.id, "type": f.type, "name": f.name, "path": f.path} for f in sorted(ai().rig.functions.values(), key=lambda f: f.id)]}
        return {"results": ai().retriever.search(q, k=k)}

    @app.get("/health")
    async def health() -> dict:
        return ai().health()

    @app.get("/labels")
    async def get_labels() -> dict:
        L = labels()
        return {"intents": L["intents"], "slots": L["slots"], "version": L["version"]}

    @app.get("/colors")
    async def colors() -> dict:
        return {"colors": ai().rig.colors.names(), "models": sorted({fx.key for fx in ai().rig.fixtures.values()})}

    return app


def serve_main(args) -> int:
    import uvicorn

    from lightai.config import load_config

    cfg = load_config()
    host = args.host or cfg.api_host
    port = args.port or cfg.api_port
    token = os.environ.get("LIGHTAI_TOKEN") or None
    loopback = host in LOOPBACK_HOSTS
    if not loopback and not token:
        print(f"[lightai] refusing to listen on {host}: anyone on that network could run the lights. "
              "Set LIGHTAI_TOKEN and open the console with ?token=<value>.")
        return 2
    t0 = time.time()
    state = LightAI(cfg, use_embeddings=not args.no_embeddings)
    print(f"[lightai] model {state.model.version} | {len(state.rig.fixtures)} fixtures | {len(state.rig.functions)} functions | ready in {time.time() - t0:.1f}s")
    print(f"[lightai] console: http://{host}:{port}/")
    app = create_app(state, cors_origins=args.cors or None, allowed_hosts=None if loopback else ["*"], token=token)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
