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
        cmd, p = ai().plan(body.text)
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
        return await ai().executor.execute(get_plan(body.plan_id), confirm=body.confirm, allow_running_reload=body.allow_running_reload)

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
