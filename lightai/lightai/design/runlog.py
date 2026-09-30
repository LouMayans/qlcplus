"""Append-only log of Claude CLI runs: one JSONL file of raw stream events per run, plus a
shared claude_runs.jsonl index of per-run summaries used for history/cost UI and totals.

Writers only ever append; readers tolerate a concurrent writer's in-flight last line (or any
other malformed line) by skipping it rather than raising, since a reader may run while a run
is still in progress.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from lightai.design.backend import ClaudeResult

TEXT_TRUNC = 2000
SHORT_TRUNC = 500
REQUEST_TRUNC = 300


def _short_json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)[:SHORT_TRUNC]
    except (TypeError, ValueError):
        return str(value)[:SHORT_TRUNC]


def _iso_from_epoch(ts) -> Optional[str]:
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


def parse_rate_limit(event: dict) -> Optional[dict]:
    """rate_limit_event -> {"status", "five_hour": {"utilization", "resets_at"}, "seven_day": {...}}."""
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    windows = info.get("unifiedWindows") or {}
    out: dict = {"status": info.get("status")}
    for key in ("five_hour", "seven_day"):
        w = windows.get(key)
        if isinstance(w, dict):
            out[key] = {"utilization": w.get("utilization"), "resets_at": _iso_from_epoch(w.get("resetsAt"))}
    return out


def extract_search_links(text: str) -> list:
    """WebSearch tool_result content is prose with an embedded 'Links: [...]' JSON array (plus
    more prose after it) - not a clean JSON blob, so pull just the array out with raw_decode."""
    marker = "Links: ["
    idx = text.find(marker)
    if idx == -1:
        return []
    start = idx + len("Links: ")
    try:
        arr, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return []
    if not isinstance(arr, list):
        return []
    return [{"url": item["url"], "title": item.get("title")} for item in arr if isinstance(item, dict) and item.get("url")]


class RunLog:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.runs_dir = self.data_dir / "claude_runs"
        self.index_path = self.data_dir / "claude_runs.jsonl"
        self._lock = threading.Lock()
        self._starts: dict = {}  # run_id -> {kind, request, model, started, limits}

    def start(self, kind: str, request: str, model: str) -> str:
        now = datetime.now()
        run_id = f"r{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        with self._lock:
            self._starts[run_id] = {"kind": kind, "request": request, "model": model, "started": now, "limits": None}
        return run_id

    def event(self, run_id: str, event: dict) -> None:
        """Append the raw event, and - for a rate_limit_event - remember it as this run's latest
        usage-window snapshot (surfaced in finish()'s row and totals()'s 'limits')."""
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        path = self.runs_dir / f"{run_id}.jsonl"
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
            if isinstance(event, dict) and event.get("type") == "rate_limit_event":
                meta = self._starts.get(run_id)
                if meta is not None:
                    limits = parse_rate_limit(event)
                    if limits:
                        meta["limits"] = limits

    def finish(self, run_id: str, result: "ClaudeResult", extra: Optional[dict] = None) -> None:
        with self._lock:
            meta = self._starts.pop(run_id, None)
        started = meta["started"] if meta else datetime.now()
        finished = datetime.now()
        if result.ok:
            status = "ok"
        elif result.error and result.error.startswith("timeout after"):
            status = "timeout"
        else:
            status = "error"
        usage = result.usage or {}
        row = {
            "run_id": run_id,
            "kind": meta["kind"] if meta else "",
            "request": (meta["request"] if meta else "")[:REQUEST_TRUNC],
            "model": meta["model"] if meta else "",
            "started": started.isoformat(timespec="seconds"),
            "finished": finished.isoformat(timespec="seconds"),
            "duration_ms": result.duration_ms,
            "status": status,
            "error": result.error,
            "num_turns": result.num_turns,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
            "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
            "thinking_tokens": int(getattr(result, "thinking_tokens", 0) or 0),
            "server_tool_use": dict(getattr(result, "server_tool_use", None) or {}),
            "cost_usd": result.cost_usd,
            "limits": meta.get("limits") if meta else None,
            "extra": extra or {},
        }
        line = json.dumps(row, ensure_ascii=False)
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with self.index_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")

    def _read_jsonl(self, path: Path):
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # tolerate a partial line from a writer still in flight
                if isinstance(obj, dict):
                    yield obj

    def _read_index(self):
        return self._read_jsonl(self.index_path)

    def runs(self, kind: Optional[str] = None, limit: int = 50) -> list:
        rows = list(self._read_index())
        if kind is not None:
            rows = [r for r in rows if r.get("kind") == kind]
        rows.reverse()  # the file is oldest-first (append-only); callers want newest first
        return rows[:limit]

    def totals(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now()
        today = now.date()
        buckets = {
            "today": {"runs": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0, "cost_usd": 0.0},
            "last_7_days": {"runs": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                             "cache_creation_input_tokens": 0, "cost_usd": 0.0},
        }
        latest_limits = None
        latest_ts = None
        for row in self._read_index():
            started_raw = row.get("started")
            try:
                d = datetime.fromisoformat(started_raw).date()
            except (TypeError, ValueError):
                d = None
            if d is not None:
                age = (today - d).days
                if age == 0:
                    self._accumulate(buckets["today"], row)
                if 0 <= age < 7:
                    self._accumulate(buckets["last_7_days"], row)
            limits = row.get("limits")
            if limits:
                ts = row.get("finished") or row.get("started") or ""
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts
                    latest_limits = limits
        buckets["limits"] = latest_limits
        return buckets

    @staticmethod
    def _accumulate(bucket: dict, row: dict) -> None:
        bucket["runs"] += 1
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            bucket[k] += int(row.get(k) or 0)
        bucket["cost_usd"] += float(row.get("cost_usd") or 0.0)

    def timeline(self, run_id: str) -> list:
        """A simplified, human-scannable step list from the raw event file for this run."""
        steps: list = []
        tool_names: dict = {}  # tool_use id -> tool name, so a later tool_result can be labeled

        def add(kind: str, title: str, detail: str = "", url: Optional[str] = None) -> None:
            steps.append({"i": len(steps), "kind": kind, "title": title, "detail": detail, "url": url})

        for ev in self._read_jsonl(self.runs_dir / f"{run_id}.jsonl"):
            t = ev.get("type")
            if t == "system":
                if ev.get("subtype") == "init":
                    add("init", str(ev.get("model") or "init"), f"cwd={ev.get('cwd', '')}")
                continue  # thinking_tokens pings etc: not a content step
            if t == "assistant":
                message = ev.get("message") or {}
                content = message.get("content") or []
                err = ev.get("error")
                if err:
                    text = next((b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"), "")
                    add("error", str(err), text[:TEXT_TRUNC])
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type")
                    if bt == "text":
                        add("text", "text", (block.get("text") or "")[:TEXT_TRUNC])
                    elif bt == "thinking":
                        add("thinking", "thinking", (block.get("thinking") or "")[:TEXT_TRUNC])
                    elif bt == "tool_use":
                        name = block.get("name") or ""
                        inp = block.get("input") or {}
                        tid = block.get("id")
                        if tid:
                            tool_names[tid] = name
                        if name == "WebSearch":
                            q = str(inp.get("query", ""))
                            add("search", q, q)
                        elif name == "WebFetch":
                            u = str(inp.get("url", ""))
                            add("fetch", u, u, url=u)
                        elif name in ("Read", "Glob", "Grep"):
                            p = str(inp.get("path") or inp.get("pattern") or "")
                            add("read", p, p)
                        else:
                            add("tool", name, _short_json(inp))
                continue
            if t == "user":
                message = ev.get("message") or {}
                for block in message.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tid = block.get("tool_use_id")
                    name = tool_names.get(tid, "")
                    content = block.get("content")
                    text = content if isinstance(content, str) else _short_json(content)
                    url = None
                    if name == "WebSearch" and isinstance(content, str):
                        links = extract_search_links(content)
                        if links:
                            url = links[0]["url"]
                    add("tool_result", name or "tool_result", text[:SHORT_TRUNC], url=url)
                continue
            if t == "result":
                from lightai.design.backend import extract_usage  # local import: avoids a hard cycle at module load

                usage = extract_usage(ev.get("usage"))
                detail = (f"is_error={bool(ev.get('is_error'))} cost_usd={ev.get('total_cost_usd')} "
                          f"turns={ev.get('num_turns')} in={usage['input_tokens']} out={usage['output_tokens']} "
                          f"cache_read={usage['cache_read_input_tokens']} cache_creation={usage['cache_creation_input_tokens']}")
                add("result", "result", detail)
                continue
            # rate_limit_event and any other/future event types: not part of the simplified view
        return steps

    def sources(self, run_id: str) -> list:
        """De-duplicated {url, title} list gathered from this run's WebSearch/WebFetch steps."""
        tool_names: dict = {}
        tool_inputs: dict = {}
        seen: dict = {}
        order: list = []

        def remember(url: Optional[str], title: Optional[str]) -> None:
            if not url or url in seen:
                return
            seen[url] = title
            order.append(url)

        for ev in self._read_jsonl(self.runs_dir / f"{run_id}.jsonl"):
            t = ev.get("type")
            if t == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tid = block.get("id")
                        if tid:
                            tool_names[tid] = block.get("name") or ""
                            tool_inputs[tid] = block.get("input") or {}
            elif t == "user":
                for block in (ev.get("message") or {}).get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tid = block.get("tool_use_id")
                    name = tool_names.get(tid, "")
                    content = block.get("content")
                    if name == "WebSearch" and isinstance(content, str):
                        for link in extract_search_links(content):
                            remember(link["url"], link.get("title"))
                    elif name == "WebFetch":
                        remember(tool_inputs.get(tid, {}).get("url"), None)
        return [{"url": u, "title": seen[u]} for u in order]
