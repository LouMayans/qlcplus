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
from lightai.api.stage_proxy import add_stage_routes
from lightai.exec.wsclient import QlcError
from lightai.nlu.text import parse_markup, split_words
from lightai.schema import OutcomeFeedback, labels

LOOPBACK_HOSTS = ["127.0.0.1", "localhost"]
MAX_BODY = 256 * 1024  # no request needs more; a 50 MB body used to stall live commands

CONSOLE = Path(__file__).with_name("console.html")


class TextIn(BaseModel):
    text: str = Field(max_length=500)
    session: Optional[str] = Field(None, max_length=64)  # the console tab, so history can be read as a conversation


class CorrectIn(BaseModel):
    plan_id: str = Field(max_length=64)
    text: str = Field("", max_length=500)  # what was meant; empty = "that's wrong"
    session: Optional[str] = Field(None, max_length=64)


class DesignIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    count: Optional[int] = Field(None, ge=1, le=6)
    minutes: Optional[float] = Field(None, gt=0, le=60)
    deep: bool = False  # Opus instead of the default design model
    session: Optional[str] = Field(None, max_length=64)


class RefineIn(BaseModel):
    text: str = Field(min_length=2, max_length=1000)
    deep: bool = False


class DesignApplyIn(BaseModel):
    shows: Optional[list[int]] = None  # indexes into the design's shows; None = all of them


class Preview3dIn(BaseModel):
    shows: Optional[list[int]] = None


class SandboxPlayIn(BaseModel):
    main_id: int


class RowRef(BaseModel):
    ts: str = Field(max_length=40)
    text: str = Field(max_length=500)


class ResearchIn(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    deep: bool = False


class RotateIn(BaseModel):
    main_ids: Optional[list[int]] = None  # default: every designed show in the show
    minutes: float = Field(20.0, ge=0.5, le=240)


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
               token: Optional[str] = None, design_backend=None) -> FastAPI:
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
            sb = design_state.get("sandbox")
            if sb is not None and sb.running:  # never leave a sandbox QLC+ behind
                await sb.stop()
            await ex.client.close()
            await app.state.stage.aclose()

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
                     "mode": p.mode, "summary": p.summary[:300], "show": Path(p.show).name if p.show else None,
                     "session": body.session, "corrects": getattr(cmd, "corrects", None)})
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

    @app.post("/correct")
    async def correct(body: CorrectIn) -> dict:
        """'Wrong' / 'What I meant...' on a history item: re-plan it with the correction; offers undo when it ran."""
        try:
            cmd, p = ai().correct_plan(body.plan_id, body.text)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        history_log({"plan_id": p.plan_id, "text": cmd.text, "intent": cmd.intent, "confidence": round(cmd.confidence, 3),
                     "mode": p.mode, "summary": p.summary[:300], "show": Path(p.show).name if p.show else None,
                     "session": body.session, "corrects": body.plan_id})
        return {"command": cmd.model_dump(), "plan": p.model_dump()}

    design_state: dict = {}
    design_tasks: dict = {}

    def design_env() -> dict:
        """The job store, the run log and the Claude backend, made on first use."""
        if not design_state:
            from lightai.design.jobs import JobStore
            from lightai.design.runlog import RunLog

            runlog = RunLog(Path(ai().cfg.data_dir))
            if design_backend is not None:
                backend = design_backend
            else:
                from lightai.design.backend import ClaudeCodeBackend

                backend = ClaudeCodeBackend(ai().cfg, runlog)
            design_state.update(jobs=JobStore(Path(ai().cfg.data_dir)), runlog=runlog, backend=backend)
        return design_state

    def job_view(job) -> dict:
        from dataclasses import asdict

        from lightai.compiler.sidecar import Sidecar

        row = asdict(job)
        side = Sidecar(ai().cfg.sidecar_path)
        row["applied"] = [{"main_id": e["main_id"], "title": e["title"]} for e in side.designs.values() if e.get("job_id") == job.id]
        row["running"] = job.id in design_tasks and not design_tasks[job.id].done()
        return row

    def start_job(job) -> None:
        from lightai.design.jobs import run_design_job

        env = design_env()
        design_tasks[job.id] = asyncio.create_task(run_design_job(ai(), job, env["jobs"], env["backend"]))

    @app.post("/design")
    async def design(body: DesignIn) -> dict:
        """Start a design job: Claude designs, lightai validates and composes a preview; nothing is written yet."""
        from lightai.design.jobs import parse_request

        count, minutes = parse_request(body.text)
        job = design_env()["jobs"].new(body.text, body.count or count, body.minutes or minutes, body.deep)
        start_job(job)
        history_log({"design_job": job.id, "text": body.text, "intent": "design_show", "mode": "design",
                     "summary": f"design {job.count} show(s)" + (f", {job.minutes:g} min" if job.minutes else ""),
                     "session": body.session})
        return {"job": job_view(job)}

    @app.get("/design")
    async def design_list(limit: int = Query(20, ge=1, le=60)) -> list:
        return [{"id": j.id, "text": j.text, "status": j.status, "count": j.count, "created": j.created,
                 "shows": [p.get("title") for p in j.preview]} for j in design_env()["jobs"].list(limit)]

    @app.get("/design/shows")
    async def design_shows() -> list:
        """The designed shows in the show lightai edits (from its sidecar)."""
        from lightai.compiler.sidecar import Sidecar

        side = Sidecar(ai().cfg.sidecar_path)
        return [{k: e.get(k) for k in ("main_id", "title", "kind", "created", "job_id", "text")}
                | {"functions": len(e.get("ids") or []), "sections": [s.get("name") for s in e.get("sections") or []]}
                for e in side.designs.values() if e.get("main_id") in ai().rig.functions]

    @app.get("/design/{jid}")
    async def design_get(jid: str) -> dict:
        job = design_env()["jobs"].get(jid)
        if job is None:
            raise HTTPException(404, f"unknown design job {jid}")
        return job_view(job)

    @app.post("/design/{jid}/refine")
    async def design_refine(jid: str, body: RefineIn) -> dict:
        jobs = design_env()["jobs"]
        parent = jobs.get(jid)
        if parent is None or not parent.result:
            raise HTTPException(409, "that design isn't ready to refine")
        job = jobs.new(body.text, parent.count, parent.minutes, body.deep, parent=jid)
        start_job(job)
        history_log({"design_job": job.id, "text": body.text, "intent": "design_show", "mode": "design",
                     "summary": f"refine {jid}", "refines": jid})
        return {"job": job_view(job)}

    @app.post("/design/{jid}/apply")
    async def design_apply(jid: str, body: DesignApplyIn) -> dict:
        """A structural plan that writes the chosen shows; run it with /execute (confirmation, other-show guard)."""
        from lightai.schema import Action, Plan

        job = design_env()["jobs"].get(jid)
        if job is None or not job.result:
            raise HTTPException(409, "that design isn't ready")
        idx = body.shows if body.shows is not None else list(range(len(job.result["shows"])))
        titles = [job.result["shows"][i]["title"] for i in idx if 0 <= i < len(job.result["shows"])]
        if not titles:
            raise HTTPException(422, "no such show in that design")
        cfg = ai().cfg
        warnings = ["Reloading replaces the show in QLC+: running functions restart and unsaved QLC+ edits are lost."]
        if not getattr(cfg, "positions_final", False):
            warnings.append("aims use the provisional 3D stage positions; re-aim after the layout is measured")
        plan = Plan(plan_id=ai().session.new_plan_id(), intent="design_show", mode="structural",
                    summary=f"Add {len(titles)} designed show(s) to {Path(cfg.project_path).name}: " + ", ".join(titles),
                    actions=[Action(op="write_design", args={"design": job.result, "shows": idx, "text": job.text, "job_id": job.id},
                                    describe=f"write {len(titles)} show(s) under AI/Shows"),
                             Action(op="reload", args={"strategy": cfg.reload_strategy}, describe="reload the show into QLC+")],
                    warnings=warnings, needs_confirmation=True, show=str(cfg.project_path))
        ai().session.store(plan)
        return {"plan": plan.model_dump()}

    def sandbox():
        if "sandbox" not in design_state:
            from lightai.design.sandbox import Sandbox

            design_state["sandbox"] = Sandbox(ai().cfg)
        return design_state["sandbox"]

    @app.post("/design/{jid}/preview3d")
    async def design_preview3d(jid: str, body: Preview3dIn) -> dict:
        """Play the design on a private, output-less copy of the show in a separate QLC+ with the 3D stage."""
        job = design_env()["jobs"].get(jid)
        if job is None or not job.result:
            raise HTTPException(409, "that design isn't ready")
        try:
            return await sandbox().preview(ai(), job.result, body.shows)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            raise HTTPException(500, f"sandbox preview failed: {exc}")

    @app.get("/sandbox")
    async def sandbox_info() -> dict:
        sb = design_state.get("sandbox")
        return {"running": bool(sb and sb.running), "port": sb.port if sb else None,
                "url": f"http://127.0.0.1:{sb.port}/stage" if sb and sb.running else None, "shows": sb.shows if sb else []}

    @app.post("/sandbox/play")
    async def sandbox_play(body: SandboxPlayIn) -> dict:
        try:
            return await sandbox().play(body.main_id)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/sandbox/stop")
    async def sandbox_stop() -> dict:
        return await sandbox().stop()

    @app.post("/design/rotate")
    async def design_rotate(body: RotateIn) -> dict:
        """Rotate designed shows through the night on the QLC+ side, one every `minutes`."""
        from lightai.compiler.sidecar import Sidecar
        from lightai.schema import Action, Plan

        ids = body.main_ids or [e["main_id"] for e in Sidecar(ai().cfg.sidecar_path).designs.values()
                                if e.get("main_id") in ai().rig.functions]
        if not ids:
            raise HTTPException(409, "no designed shows to rotate; apply a design first")
        plan = Plan(plan_id=ai().session.new_plan_id(), intent="run_function", mode="live", show=str(ai().cfg.project_path),
                    summary=f"Rotate {len(ids)} show(s), {body.minutes:g} minutes each",
                    actions=[Action(op="start_rotation", args={"main_ids": ids, "minutes": body.minutes})])
        ai().session.store(plan)
        res = await ai().executor.execute(plan, confirm=True)
        history_log({"plan_id": plan.plan_id, "text": f"rotate shows every {body.minutes:g} min", "intent": "run_function",
                     "mode": "live", "summary": plan.summary, "executed": bool(res.get("ok")),
                     "note": "" if res.get("ok") else str(res.get("error") or res.get("clarify") or "")[:200]})
        return res

    @app.post("/design/rotate/stop")
    async def design_rotate_stop() -> dict:
        from lightai.schema import Action, Plan

        plan = Plan(plan_id=ai().session.new_plan_id(), intent="stop_function", mode="live", summary="Stop the show rotation",
                    show=str(ai().cfg.project_path), actions=[Action(op="stop_rotation", args={})])
        ai().session.store(plan)
        return await ai().executor.execute(plan, confirm=True)

    @app.post("/design/show/{main_id}/run")
    async def design_run(main_id: int) -> dict:
        c = await ai().executor.ensure_client()
        await c.set_function(main_id, True)
        return {"ok": True, "running": main_id}

    @app.post("/design/show/{main_id}/stop")
    async def design_stop(main_id: int) -> dict:
        c = await ai().executor.ensure_client()
        await c.set_function(main_id, False)
        return {"ok": True, "stopped": main_id}

    @app.post("/design/show/{main_id}/remove")
    async def design_remove(main_id: int) -> dict:
        """A structural plan that deletes every function of a designed show; run it with /execute."""
        from lightai.compiler.sidecar import Sidecar
        from lightai.schema import Action, Plan

        cfg = ai().cfg
        entry = Sidecar(cfg.sidecar_path).designs.get(str(main_id))
        if entry is None:
            raise HTTPException(404, f"no designed show with main function {main_id}")
        plan = Plan(plan_id=ai().session.new_plan_id(), intent="delete_look", mode="structural",
                    summary=f"Remove the designed show '{entry['title']}' ({len(entry['ids'])} functions)",
                    actions=[Action(op="delete_look", args={"ids": entry["ids"], "main_id": main_id},
                                    describe=f"delete {len(entry['ids'])} functions"),
                             Action(op="reload", args={"strategy": cfg.reload_strategy}, describe="reload the show into QLC+")],
                    needs_confirmation=True, show=str(cfg.project_path))
        ai().session.store(plan)
        return {"plan": plan.model_dump()}

    @app.get("/stage/stale")
    async def stage_stale() -> dict:
        """Looks and designed shows aimed with an older 3D layout (the console offers 'Re-aim')."""
        from lightai.design.reaim import stale_entries

        s = stale_entries(ai().get_rig())
        return {"hash": s["hash"], "looks": [{"main_id": e["main_id"], "name": e.get("name")} for e in s["looks"]],
                "designs": [{"main_id": e["main_id"], "title": e.get("title")} for e in s["designs"]]}

    @app.post("/reaim")
    async def reaim() -> dict:
        """A structural plan that rebuilds every stale look and show; run it with /execute."""
        from lightai.design.reaim import reaim_plan

        return {"plan": reaim_plan(ai()).model_dump()}

    def _rows(path: Path) -> list:
        out = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def _rewrite(path: Path, rows: list) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(path)

    @app.get("/corrections")
    async def corrections(source: Optional[str] = None, limit: int = Query(100, ge=1, le=1000)) -> list:
        """Training examples: the operator's own ('console') and the nightly teacher's ('claude'), newest last."""
        rows = [r for r in _rows(Path(ai().cfg.data_dir) / "corrections.jsonl") if source is None or r.get("source") == source]
        return rows[-limit:]

    @app.post("/corrections/undo")
    async def corrections_undo(body: RowRef) -> dict:
        """Take a training example back out (it stays in the file, marked not accepted)."""
        path = Path(ai().cfg.data_dir) / "corrections.jsonl"
        rows, hit = _rows(path), 0
        for r in rows:
            if r.get("ts") == body.ts and r.get("text") == body.text and r.get("accepted", True):
                r["accepted"], r["undone"] = False, datetime.now(timezone.utc).isoformat(timespec="seconds")
                hit += 1
        if not hit:
            raise HTTPException(404, "no such training example")
        _rewrite(path, rows)
        return {"ok": True, "undone": hit}

    @app.get("/review")
    async def review(limit: int = Query(100, ge=1, le=1000)) -> list:
        """What the teacher wasn't sure of (labels) and new words it noticed (terms), newest last."""
        return [r for r in _rows(Path(ai().cfg.data_dir) / "review_queue.jsonl") if not r.get("resolved")][-limit:]

    @app.post("/review/accept")
    async def review_accept(body: RowRef) -> dict:
        """Accept a queued label: it becomes a training example (weight 1, like the teacher's clear cases)."""
        data = Path(ai().cfg.data_dir)
        rows = _rows(data / "review_queue.jsonl")
        row = next((r for r in rows if r.get("ts") == body.ts and r.get("text") == body.text and r.get("kind") == "label"
                    and not r.get("resolved") and not r.get("problem")), None)
        if row is None:
            raise HTTPException(404, "no such reviewable label")
        with open(data / "corrections.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({k: v for k, v in row.items() if k not in ("kind", "problem")} | {"accepted": True, "reviewed": True}) + "\n")
        row["resolved"] = "accepted"
        _rewrite(data / "review_queue.jsonl", rows)
        return {"ok": True}

    @app.post("/review/dismiss")
    async def review_dismiss(body: RowRef) -> dict:
        data = Path(ai().cfg.data_dir)
        rows = _rows(data / "review_queue.jsonl")
        hit = [r for r in rows if r.get("ts") == body.ts and (r.get("text") == body.text or r.get("word") == body.text) and not r.get("resolved")]
        if not hit:
            raise HTTPException(404, "no such review item")
        for r in hit:
            r["resolved"] = "dismissed"
        _rewrite(data / "review_queue.jsonl", rows)
        return {"ok": True}

    @app.post("/teach/run")
    async def teach_run() -> dict:
        """Run the teacher now instead of waiting for the night (it only looks at sessions since its last run)."""
        from lightai.design.teacher import label_sessions

        env = design_env()
        return await label_sessions(ai(), env["backend"])

    research_jobs: dict = {}

    @app.post("/research")
    async def research_start(body: ResearchIn) -> dict:
        """Claude researches a lighting topic on the web; findings go to lightai/knowledge/research, new mood words
        to moods.yaml. Runs in the background; the steps show in the Claude tab."""
        import time as _time
        import uuid

        from lightai.design.research import research

        rid = f"q{_time.strftime('%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        job = {"id": rid, "topic": body.topic, "deep": body.deep, "status": "running", "progress": [], "result": None,
               "started": _time.time()}
        research_jobs[rid] = job
        for old_id in sorted(research_jobs, key=lambda k: research_jobs[k]["started"])[:-20]:
            research_jobs.pop(old_id, None)

        def on_event(event: dict) -> None:
            from lightai.design.jobs import _event_step

            line = _event_step(event)
            if line:
                job["progress"] = (job["progress"] + [{"t": _time.time(), "text": line}])[-80:]

        async def run() -> None:
            try:
                job["result"] = await research(ai(), body.topic, design_env()["backend"], deep=body.deep, on_event=on_event)
                job["status"] = "done" if job["result"].get("ok") else "error"
            except Exception as exc:
                job["status"], job["result"] = "error", {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        job["task"] = asyncio.create_task(run())
        history_log({"research_job": rid, "text": body.topic, "intent": "research", "mode": "research", "summary": "research"})
        return {k: v for k, v in job.items() if k != "task"}

    @app.get("/research")
    async def research_list() -> list:
        return [{k: v for k, v in j.items() if k not in ("task", "progress")} for j in
                sorted(research_jobs.values(), key=lambda j: -j["started"])]

    @app.get("/research/{rid}")
    async def research_get(rid: str) -> dict:
        job = research_jobs.get(rid)
        if job is None:
            raise HTTPException(404, f"unknown research job {rid}")
        return {k: v for k, v in job.items() if k != "task"}

    @app.get("/claude/runs")
    async def claude_runs(kind: Optional[str] = None, limit: int = Query(50, ge=1, le=500)) -> dict:
        rl = design_env()["runlog"]
        return {"runs": rl.runs(kind=kind, limit=limit), "totals": rl.totals()}

    @app.get("/claude/runs/{rid}")
    async def claude_run(rid: str) -> dict:
        rl = design_env()["runlog"]
        row = next((r for r in rl.runs(limit=500) if r.get("run_id") == rid), None)
        if row is None:
            raise HTTPException(404, f"unknown Claude run {rid}")
        return {"summary": row, "timeline": rl.timeline(rid), "sources": rl.sources(rid)}

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
        out = [dict(r, executed=done[r["plan_id"]]["executed"], result=done[r["plan_id"]].get("note")) if r.get("plan_id") in done else r
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

    # The 3D stage view, proxied from QLC+ at a different path on this same port (see
    # stage_proxy.py). Registered LAST: its catch-all route only ever sees requests that missed
    # every route above. Nothing here connects to QLC+ until a browser asks for /stage or opens
    # the WebSocket bridge.
    app.state.stage = add_stage_routes(app, ai, token)

    return app


def serve_main(args) -> int:
    import uvicorn

    from lightai.config import load_config

    cfg = load_config()
    host = args.host or cfg.api_host
    port = args.port or cfg.api_port
    if getattr(args, "qlc_port", None):
        cfg.qlc_port = args.qlc_port
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
