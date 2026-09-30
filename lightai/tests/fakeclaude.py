"""A DesignBackend that replays canned Claude Code CLI streams (or a fixed dict) without
spawning a process. For tests of code that depends on DesignBackend (the designer and its
callers) as well as for lightai/tests/test_claude_backend.py itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from lightai.design.backend import ClaudeResult, build_result
from lightai.design.runlog import RunLog

Canned = Union[dict, str, Path]


class FakeClaudeBackend:
    """Constructed with a list of canned responses, consumed one per call to run():
    - a dict is treated as the structured output of a minimal synthetic stream;
    - a path (str or Path) to a .jsonl file replays that file's events verbatim.

    Either way, events are pushed through the same RunLog.event()/on_event path a real
    ClaudeCodeBackend uses, and the final ClaudeResult is built with the same build_result()
    helper, so the parsing logic is exercised the same way for canned and real streams.
    """

    def __init__(self, canned: Sequence[Canned], runlog: RunLog) -> None:
        self.canned = list(canned)
        self.runlog = runlog
        self.calls: list = []

    async def run(self, prompt: str, *, kind: str, model: str, schema: Optional[dict] = None,
                  system_file: Optional[Path] = None, replace_system_prompt: bool = False,
                  allowed_tools: Sequence[str] = (), disallowed_tools: Sequence[str] = (),
                  agents: Optional[dict] = None, timeout_s: float = 600.0, budget_usd: Optional[float] = None,
                  on_event: Optional[Callable[[dict], None]] = None) -> ClaudeResult:
        self.calls.append({
            "prompt": prompt, "kind": kind, "model": model, "schema": schema, "system_file": system_file,
            "replace_system_prompt": replace_system_prompt, "allowed_tools": tuple(allowed_tools),
            "disallowed_tools": tuple(disallowed_tools), "agents": agents, "timeout_s": timeout_s,
            "budget_usd": budget_usd,
        })
        if not self.canned:
            raise AssertionError("FakeClaudeBackend ran out of canned responses")
        run_id = self.runlog.start(kind, prompt, model)
        canned = self.canned.pop(0)
        events = _events_for(canned)
        for ev in events:
            self.runlog.event(run_id, ev)
            if on_event is not None:
                on_event(ev)
        result = build_result(run_id, events, schema)
        self.runlog.finish(run_id, result)
        return result


def _events_for(canned: Canned) -> list:
    if isinstance(canned, dict):
        return _synthetic_stream(canned)
    events = []
    with Path(canned).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def _synthetic_stream(data: dict) -> list:
    """A minimal, well-formed stream for a plain-dict canned response: one system/init, one
    assistant text block, and a result event carrying the dict as structured_output - matching
    the shape a real --json-schema run produces (see claude_stream_schema.jsonl)."""
    text = json.dumps(data)
    return [
        {"type": "system", "subtype": "init", "cwd": "", "model": "fake", "tools": []},
        {"type": "assistant", "message": {"model": "fake", "role": "assistant",
                                           "content": [{"type": "text", "text": text}]}},
        {"duration_api_ms": 1, "stop_reason": "end_turn", "total_cost_usd": 0.0,
         "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                    "output_tokens": 0, "output_tokens_details": {"thinking_tokens": 0},
                    "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}},
         "is_error": False, "num_turns": 1, "subtype": "success", "result": text,
         "structured_output": data, "type": "result", "duration_ms": 1},
    ]
