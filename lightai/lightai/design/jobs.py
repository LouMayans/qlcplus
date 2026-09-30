"""Design jobs: a request goes to Claude in the background, the answer is validated against the real show, errors
go back for up to two repair rounds, and the result is composed into a preview the operator can apply, refine or
drop. Jobs are kept in data_dir/design_jobs.json so the console can show them after a restart."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from lightai.design.spec import DesignResult, design_schema
from lightai.design.validate import validate_design

MAX_REPAIRS = 2
KEEP_JOBS = 60
DESIGN_TOOLS = ("Read", "Glob", "Grep", "WebSearch", "WebFetch")
NO_WRITE_TOOLS = ("Bash", "Edit", "Write", "NotebookEdit")
WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def parse_request(text: str) -> tuple:
    """How many shows and how long each, from the words: '3 different shows', 'a 5-minute show'."""
    t = text.lower()
    count = 1
    m = re.search(r"\b(\d|one|two|three|four|five|six)\s+(?:different\s+|new\s+|distinct\s+|more\s+)*(?:light\s+)?shows?\b", t)
    if m:
        count = int(m.group(1)) if m.group(1).isdigit() else WORD_NUMBERS[m.group(1)]
    minutes = None
    m = re.search(r"\b(\d{1,2}(?:\.\d)?)\s*-?\s*(?:minute|min)s?\b", t)
    if m:
        minutes = float(m.group(1))
    return max(1, min(count, 6)), minutes


@dataclass
class DesignJob:
    id: str
    text: str
    count: int = 1
    minutes: Optional[float] = None
    deep: bool = False
    parent: Optional[str] = None
    status: str = "queued"  # queued | running | repairing | done | error
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    run_ids: list = field(default_factory=list)
    result: Optional[dict] = None  # the validated DesignResult
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    preview: list = field(default_factory=list)  # composed show summaries
    progress: list = field(default_factory=list)  # short live steps for the console
    applied: list = field(default_factory=list)
    note: str = ""


class JobStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "design_jobs.json"
        self.lock = threading.Lock()
        self.jobs: dict = {}
        try:
            for row in json.loads(self.path.read_text(encoding="utf-8")):
                self.jobs[row["id"]] = DesignJob(**row)
        except (OSError, ValueError, TypeError):
            self.jobs = {}

    def new(self, text: str, count: int, minutes: Optional[float], deep: bool = False, parent: Optional[str] = None) -> DesignJob:
        job = DesignJob(id=f"d{time.strftime('%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}", text=text, count=count, minutes=minutes,
                        deep=deep, parent=parent)
        with self.lock:
            self.jobs[job.id] = job
        self.save()
        return job

    def get(self, job_id: str) -> Optional[DesignJob]:
        return self.jobs.get(job_id)

    def list(self, limit: int = 20) -> list:
        return sorted(self.jobs.values(), key=lambda j: -j.created)[:limit]

    def save(self) -> None:
        with self.lock:
            rows = [asdict(j) for j in sorted(self.jobs.values(), key=lambda j: -j.created)[:KEEP_JOBS]]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
            tmp.replace(self.path)


def _step(job: DesignJob, text: str, store: Optional[JobStore] = None) -> None:
    job.progress.append({"t": time.time(), "text": text})
    job.progress = job.progress[-80:]
    job.updated = time.time()
    if store is not None:
        store.save()


def _event_step(event: dict) -> Optional[str]:
    """A short line for the live progress panel, from one stream event."""
    if event.get("type") != "assistant":
        return None
    for block in (event.get("message") or {}).get("content") or []:
        if block.get("type") == "tool_use":
            name, inp = block.get("name"), block.get("input") or {}
            if name == "WebSearch":
                return f"searching the web: {inp.get('query', '')}"
            if name == "WebFetch":
                return f"reading {inp.get('url', '')}"
            if name in ("Read", "Glob", "Grep"):
                return f"reading {inp.get('file_path') or inp.get('pattern') or inp.get('path') or ''}"
            return f"using {name}"
        if block.get("type") == "text" and block.get("text", "").strip():
            return "writing: " + block["text"].strip().splitlines()[0][:140]
    return None


async def run_design_job(ai, job: DesignJob, store: JobStore, backend) -> DesignJob:
    """Ask Claude, validate, repair (up to twice), compose a preview. Nothing is written to the show here."""
    from lightai.design.composer import compose_design
    from lightai.design.prompt import build_context, request_prompt, write_context

    cfg = ai.cfg
    rig = ai.get_rig()
    from lightai.mine.index import references_brief

    ctx = build_context(rig, getattr(ai, "prefs", None), count=job.count, minutes=job.minutes,
                        positions_final=bool(getattr(cfg, "positions_final", False)),
                        references=references_brief(cfg, job.text))
    ctx_file = write_context(Path(cfg.data_dir) / "design_ctx" / f"{job.id}.md", ctx)
    prompt = request_prompt(job.text, job.count, job.minutes)
    if job.parent:
        parent = store.get(job.parent)
        if parent and parent.result:
            prompt = (f"Revise this design as the operator asks: {job.text.strip()}\nKeep what they didn't ask to "
                      f"change. The current design:\n{json.dumps(parent.result)}")
    model = "opus" if job.deep else getattr(cfg, "claude_model_design", "sonnet")
    job.status = "running"
    _step(job, f"asking Claude ({model})", store)

    def on_event(event: dict) -> None:
        line = _event_step(event)
        if line:
            _step(job, line)

    for attempt in range(MAX_REPAIRS + 1):
        res = await backend.run(prompt, kind="design" if attempt == 0 else "repair", model=model, schema=design_schema(),
                                system_file=ctx_file, allowed_tools=DESIGN_TOOLS, disallowed_tools=NO_WRITE_TOOLS,
                                timeout_s=float(getattr(cfg, "design_timeout_s", 600.0)),
                                budget_usd=getattr(cfg, "claude_budget_design_usd", None), on_event=on_event)
        job.run_ids.append(res.run_id)
        if not res.ok:
            job.status, job.errors = "error", [res.error or "Claude run failed"]
            _step(job, f"failed: {job.errors[0]}", store)
            return job
        problems: list = []
        try:
            design = DesignResult.model_validate(res.data or {})
        except ValidationError as exc:
            design, problems = None, [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()][:20]
        if design is not None:
            problems, warnings, _ = validate_design(ai.get_rig(), design)
            job.warnings = warnings
            if not problems:
                try:
                    shows = compose_design(ai.get_rig(), design, bpm=getattr(ai.session, "bpm", None))
                except Exception as exc:  # a compile error is also a problem Claude can fix
                    problems = [f"lightai couldn't build it: {exc}"]
                else:
                    job.result = design.model_dump()
                    job.preview = [s.summary() for s in shows]
                    job.status, job.errors = "done", []
                    job.note = design.notes
                    _step(job, f"ready: {len(shows)} show(s), {sum(len(s.functions) for s in shows)} functions", store)
                    return job
        job.errors = problems
        if attempt == MAX_REPAIRS:
            break
        job.status = "repairing"
        _step(job, f"sending {len(problems)} problem(s) back to Claude (round {attempt + 1})", store)
        prompt = ("Your design can't be built as it is. Fix exactly these problems and return the complete, corrected "
                  "DesignResult:\n- " + "\n- ".join(problems) + "\n\nYour previous answer:\n"
                  + json.dumps(res.data or {}))
    job.status = "error"
    _step(job, "gave up after the repair rounds", store)
    return job
