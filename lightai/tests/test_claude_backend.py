"""Tests for lightai.design.backend and lightai.design.runlog. No real CLI is invoked."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from fakeclaude import FakeClaudeBackend
from lightai.config import Config, load_config
from lightai.design import runlog as runlog_module
from lightai.design.backend import (
    ClaudeCodeBackend,
    ClaudeResult,
    build_command_line,
    build_result,
    discover_claude_exe,
)
from lightai.design.runlog import RunLog

DATA = Path(__file__).parent / "data"


def _load_events(name: str) -> list:
    events = []
    with (DATA / name).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _usage(input_tokens=0, output_tokens=0, cache_read=0, cache_creation=0) -> dict:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": cache_creation}


# ---------------------------------------------------------------------------
# discover_claude_exe
# ---------------------------------------------------------------------------

def _make_extension(root: Path, version: str) -> Path:
    exe_dir = root / f"anthropic.claude-code-{version}-win32-x64" / "resources" / "native-binary"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "claude.exe"
    exe.write_text("stub", encoding="utf-8")
    return exe


def test_discover_claude_exe_newest_version_wins(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ext_root = home / ".vscode" / "extensions"
    ext_root.mkdir(parents=True)
    older = _make_extension(ext_root, "2.1.99")
    newer = _make_extension(ext_root, "2.1.283")
    monkeypatch.setenv("USERPROFILE", str(home))
    found = discover_claude_exe(Config())
    assert found == newer
    assert found != older


def test_discover_claude_exe_memo_invalidates_on_new_version(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ext_root = home / ".vscode" / "extensions"
    ext_root.mkdir(parents=True)
    older = _make_extension(ext_root, "2.1.99")
    monkeypatch.setenv("USERPROFILE", str(home))
    cfg = Config()
    assert discover_claude_exe(cfg) == older
    time.sleep(0.05)  # NTFS mtime resolution is fine-grained, but leave a safety margin
    newer = _make_extension(ext_root, "2.1.283")
    assert discover_claude_exe(cfg) == newer


def test_discover_claude_exe_config_override_wins(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ext_root = home / ".vscode" / "extensions"
    ext_root.mkdir(parents=True)
    _make_extension(ext_root, "2.1.283")
    monkeypatch.setenv("USERPROFILE", str(home))
    override = tmp_path / "custom" / "claude.exe"
    override.parent.mkdir(parents=True)
    override.write_text("stub", encoding="utf-8")
    cfg = Config()
    cfg.claude_exe_path = override
    assert discover_claude_exe(cfg) == override


def test_discover_claude_exe_missing_override_falls_back_to_extensions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ext_root = home / ".vscode" / "extensions"
    ext_root.mkdir(parents=True)
    newer = _make_extension(ext_root, "2.1.283")
    monkeypatch.setenv("USERPROFILE", str(home))
    cfg = Config()
    cfg.claude_exe_path = tmp_path / "does" / "not" / "exist.exe"
    assert discover_claude_exe(cfg) == newer


def test_discover_claude_exe_none_found(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".vscode" / "extensions").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    cfg = Config()
    assert discover_claude_exe(cfg) is None


def test_load_config_converts_claude_exe_path_from_yaml(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    exe = tmp_path / "custom-claude.exe"
    exe.write_text("stub", encoding="utf-8")
    cfg_path.write_text(f"claude_exe_path: {json.dumps(str(exe))}\n", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert isinstance(cfg.claude_exe_path, Path)
    assert cfg.claude_exe_path == exe


# ---------------------------------------------------------------------------
# build_command_line
# ---------------------------------------------------------------------------

def test_build_command_line_minimal():
    argv = build_command_line(Path("claude.exe"), model="sonnet")
    assert argv == ["claude.exe", "-p", "--output-format", "stream-json", "--verbose", "--model", "sonnet",
                     "--no-session-persistence", "--strict-mcp-config", "--tools", ""]


def test_build_command_line_loads_only_the_allowed_tools():
    argv = build_command_line(Path("claude.exe"), model="sonnet", allowed_tools=("Read", "WebSearch", "Bash"),
                              disallowed_tools=("Bash",), agents={"researcher": {"model": "haiku"}})
    assert argv[argv.index("--tools") + 1] == "Read,WebSearch,Agent"
    assert "--strict-mcp-config" in argv


def test_build_command_line_schema_is_compact():
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    argv = build_command_line(Path("claude.exe"), model="haiku", schema=schema)
    i = argv.index("--json-schema")
    assert argv[i + 1] == '{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"]}'
    assert " " not in argv[i + 1]


def test_build_command_line_system_file_append_by_default(tmp_path):
    sysfile = tmp_path / "system.txt"
    sysfile.write_text("be terse", encoding="utf-8")
    argv = build_command_line(Path("claude.exe"), model="haiku", system_file=sysfile)
    i = argv.index("--append-system-prompt-file")
    assert argv[i + 1] == str(sysfile)
    assert "--system-prompt-file" not in argv


def test_build_command_line_system_file_replace(tmp_path):
    sysfile = tmp_path / "system.txt"
    sysfile.write_text("be terse", encoding="utf-8")
    argv = build_command_line(Path("claude.exe"), model="haiku", system_file=sysfile, replace_system_prompt=True)
    i = argv.index("--system-prompt-file")
    assert argv[i + 1] == str(sysfile)
    assert "--append-system-prompt-file" not in argv


def test_build_command_line_tools_agents_budget_present(tmp_path):
    argv = build_command_line(Path("claude.exe"), model="sonnet", allowed_tools=["WebSearch", "Read"],
                               disallowed_tools=["Bash"], agents={"a": {"description": "d", "prompt": "p"}},
                               budget_usd=1.5)
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch,Read"
    assert argv[argv.index("--disallowedTools") + 1] == "Bash"
    assert argv[argv.index("--agents") + 1] == '{"a":{"description":"d","prompt":"p"}}'
    assert argv[argv.index("--max-budget-usd") + 1] == "1.5"


def test_build_command_line_optional_flags_absent_when_not_given():
    argv = build_command_line(Path("claude.exe"), model="sonnet")
    for flag in ("--json-schema", "--append-system-prompt-file", "--system-prompt-file", "--allowedTools",
                 "--disallowedTools", "--agents", "--max-budget-usd"):
        assert flag not in argv


def test_build_command_line_prompt_arg_appended_when_given():
    argv = build_command_line(Path("claude.exe"), model="sonnet", prompt_arg="hello there")
    assert argv[-1] == "hello there"


def test_build_command_line_prompt_arg_too_long_raises():
    with pytest.raises(ValueError):
        build_command_line(Path("claude.exe"), model="sonnet", prompt_arg="x" * 30001)


# ---------------------------------------------------------------------------
# build_result on the real captured streams (see the implementing agent's report for the
# smoke-test session that produced these under lightai/tests/data/)
# ---------------------------------------------------------------------------

def test_build_result_schema_stream():
    events = _load_events("claude_stream_schema.jsonl")
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    result = build_result("rtest-schema", events, schema)
    assert result.ok is True
    assert result.data == {"answer": "4"}
    assert result.num_turns == 2
    assert result.usage == _usage(9, 215, 0, 35591)
    assert result.thinking_tokens == 158
    assert result.cost_usd == pytest.approx(0.07323)
    assert result.error is None


def test_build_result_websearch_stream():
    events = _load_events("claude_stream_websearch.jsonl")
    result = build_result("rtest-search", events, None)
    assert result.ok is True
    assert result.data is None  # no schema was given for this run
    assert "mcallegari/qlcplus" in result.text
    assert result.usage["cache_read_input_tokens"] == 98057
    assert result.thinking_tokens == 293
    assert result.num_turns == 3
    assert result.server_tool_use == {"web_search_requests": 0, "web_fetch_requests": 0}


def test_build_result_basic_stream():
    events = _load_events("claude_stream_basic.jsonl")
    result = build_result("rtest-basic", events, None)
    assert result.ok is True
    assert result.text == "pineapple."
    assert result.usage["cache_creation_input_tokens"] == 29634
    assert result.cost_usd == pytest.approx(0.060721)


def test_build_result_bare_auth_error_stream():
    events = _load_events("claude_stream_bare_auth_error.jsonl")
    result = build_result("rtest-bare", events, None)
    assert result.ok is False
    assert "Not logged in" in result.error
    assert result.cost_usd == 0


def test_build_result_no_result_event():
    result = build_result("rtest-empty", [{"type": "system", "subtype": "init"}], None)
    assert result.ok is False
    assert "no result event" in result.error


def test_build_result_schema_requested_but_missing():
    events = [{"type": "result", "is_error": False, "result": "not json", "num_turns": 1, "duration_ms": 1,
               "total_cost_usd": 0.0, "usage": {}}]
    result = build_result("rtest-noschema", events, {"type": "object"})
    assert result.ok is False
    assert result.error == "no structured output"


# ---------------------------------------------------------------------------
# RunLog
# ---------------------------------------------------------------------------

def test_runlog_start_event_finish_and_runs(tmp_path):
    log = RunLog(tmp_path)
    events = _load_events("claude_stream_websearch.jsonl")
    run_id = log.start("research", "search for qlcplus", "haiku")
    assert run_id.startswith("r")
    for ev in events:
        log.event(run_id, ev)
    result = build_result(run_id, events, None)
    log.finish(run_id, result)

    raw_path = tmp_path / "claude_runs" / f"{run_id}.jsonl"
    assert raw_path.exists()
    assert len(raw_path.read_text(encoding="utf-8").splitlines()) == len(events)

    rows = log.runs()
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == run_id
    assert row["kind"] == "research"
    assert row["request"] == "search for qlcplus"
    assert row["status"] == "ok"
    assert row["cache_read_input_tokens"] == 98057
    assert row["thinking_tokens"] == 293
    assert row["server_tool_use"] == {"web_search_requests": 0, "web_fetch_requests": 0}
    assert row["limits"] is not None
    assert row["limits"]["status"] == "allowed"
    assert row["limits"]["five_hour"]["utilization"] == pytest.approx(0.09)
    assert row["limits"]["five_hour"]["resets_at"]  # non-empty ISO string


def test_runlog_runs_filters_by_kind_and_newest_first(tmp_path):
    log = RunLog(tmp_path)
    for kind, text in (("design", "a"), ("label", "b"), ("design", "c")):
        rid = log.start(kind, text, "haiku")
        result = ClaudeResult(ok=True, data=None, text=text, run_id=rid, usage=_usage(1, 1), cost_usd=0.001,
                               num_turns=1, duration_ms=1)
        log.finish(rid, result)
    design_rows = log.runs(kind="design")
    assert [r["request"] for r in design_rows] == ["c", "a"]
    assert [r["request"] for r in log.runs()] == ["c", "b", "a"]
    assert log.runs(limit=1) == log.runs()[:1]


def test_runlog_finish_status_timeout_and_error(tmp_path):
    log = RunLog(tmp_path)
    rid1 = log.start("design", "x", "sonnet")
    log.finish(rid1, ClaudeResult(ok=False, data=None, text="", run_id=rid1, usage=_usage(), cost_usd=None,
                                   num_turns=0, duration_ms=500, error="timeout after 5 s"))
    rid2 = log.start("design", "y", "sonnet")
    log.finish(rid2, ClaudeResult(ok=False, data=None, text="", run_id=rid2, usage=_usage(), cost_usd=None,
                                   num_turns=0, duration_ms=5, error="boom"))
    rows = {r["run_id"]: r for r in log.runs()}
    assert rows[rid1]["status"] == "timeout"
    assert rows[rid2]["status"] == "error"


def test_runlog_tolerates_malformed_lines(tmp_path):
    log = RunLog(tmp_path)
    rid = log.start("design", "x", "sonnet")
    log.event(rid, {"type": "system", "subtype": "init", "model": "haiku", "cwd": "C:/x"})
    with (tmp_path / "claude_runs" / f"{rid}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    steps = log.timeline(rid)
    assert steps and steps[0]["kind"] == "init"

    with (tmp_path / "claude_runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("garbage, not json at all\n")
    log.finish(rid, ClaudeResult(ok=True, data=None, text="ok", run_id=rid, usage=_usage(1, 1), cost_usd=0.0,
                                  num_turns=1, duration_ms=1))
    assert any(r["run_id"] == rid for r in log.runs())


def test_runlog_totals_buckets_by_local_date(tmp_path, monkeypatch):
    import datetime as dt_module

    class Frozen(dt_module.datetime):
        _now = None

        @classmethod
        def now(cls, tz=None):
            return cls._now

    monkeypatch.setattr(runlog_module, "datetime", Frozen)
    log = RunLog(tmp_path)

    def seed(when, text):
        Frozen._now = when
        rid = log.start("design", text, "haiku")
        log.finish(rid, ClaudeResult(ok=True, data=None, text=text, run_id=rid, usage=_usage(10, 5, 1, 2),
                                      cost_usd=0.01, num_turns=1, duration_ms=1))

    seed(dt_module.datetime(2026, 9, 29, 10, 0, 0), "today-run")
    seed(dt_module.datetime(2026, 9, 25, 10, 0, 0), "3-days-ago")
    seed(dt_module.datetime(2026, 9, 10, 10, 0, 0), "way-old")

    totals = log.totals(now=dt_module.datetime(2026, 9, 29, 12, 0, 0))
    assert totals["today"]["runs"] == 1
    assert totals["today"]["input_tokens"] == 10
    assert totals["last_7_days"]["runs"] == 2
    assert totals["last_7_days"]["input_tokens"] == 20
    assert totals["last_7_days"]["cost_usd"] == pytest.approx(0.02)


def test_runlog_totals_limits_is_most_recent(tmp_path, monkeypatch):
    import datetime as dt_module

    class Frozen(dt_module.datetime):
        _now = None

        @classmethod
        def now(cls, tz=None):
            return cls._now

    monkeypatch.setattr(runlog_module, "datetime", Frozen)
    log = RunLog(tmp_path)

    def seed(when, utilization):
        Frozen._now = when
        rid = log.start("design", "x", "haiku")
        log.event(rid, {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "unifiedWindows": {
                "five_hour": {"utilization": utilization, "resetsAt": 1790719200},
                "seven_day": {"utilization": 0.5, "resetsAt": 1790740800}}}})
        log.finish(rid, ClaudeResult(ok=True, data=None, text="x", run_id=rid, usage=_usage(1, 1), cost_usd=0.0,
                                      num_turns=1, duration_ms=1))

    seed(dt_module.datetime(2026, 9, 20, 8, 0, 0), 0.10)
    seed(dt_module.datetime(2026, 9, 29, 9, 0, 0), 0.42)  # the later run's limits should win

    totals = log.totals(now=dt_module.datetime(2026, 9, 29, 12, 0, 0))
    assert totals["limits"]["five_hour"]["utilization"] == pytest.approx(0.42)


def test_runlog_timeline_and_sources_all_kinds(tmp_path):
    log = RunLog(tmp_path)
    rid = log.start("research", "look things up", "sonnet")
    log.event(rid, {"type": "system", "subtype": "init", "model": "claude-haiku-4-5", "cwd": "C:/work"})
    log.event(rid, {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "let me look"}]}})
    log.event(rid, {"type": "assistant", "message": {"content": [{"type": "text", "text": "Searching now."}]}})
    log.event(rid, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "WebSearch", "input": {"query": "qlcplus github"}}]}})
    log.event(rid, {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
        "content": "Web search results for query: \"qlcplus github\"\n\nLinks: "
                   "[{\"title\":\"QLC+ on GitHub\",\"url\":\"https://github.com/mcallegari/qlcplus\"}]\n\nmore prose"}]}})
    log.event(rid, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t2", "name": "WebFetch",
         "input": {"url": "https://github.com/mcallegari/qlcplus/releases"}}]}})
    log.event(rid, {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2",
                                                              "content": "page contents..."}]}})
    log.event(rid, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t3", "name": "Read", "input": {"path": "/tmp/notes.txt"}}]}})
    log.event(rid, {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t3",
                                                              "content": "file contents"}]}})
    log.event(rid, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t4", "name": "SomeOtherTool", "input": {"x": 1}}]}})
    log.event(rid, {"type": "assistant", "error": "authentication_failed",
                    "message": {"content": [{"type": "text", "text": "Not logged in"}]}})
    log.event(rid, {"type": "result", "is_error": True, "result": "Not logged in", "num_turns": 1, "duration_ms": 5,
                    "total_cost_usd": 0.0, "usage": _usage()})

    steps = log.timeline(rid)
    kinds = [s["kind"] for s in steps]
    assert kinds == ["init", "thinking", "text", "search", "tool_result", "fetch", "tool_result", "read",
                      "tool_result", "tool", "error", "result"]
    search_step = steps[kinds.index("search")]
    assert "qlcplus github" in search_step["title"]
    fetch_step = steps[kinds.index("fetch")]
    assert fetch_step["url"] == "https://github.com/mcallegari/qlcplus/releases"
    read_step = steps[kinds.index("read")]
    assert read_step["title"] == "/tmp/notes.txt"
    error_step = steps[kinds.index("error")]
    assert error_step["title"] == "authentication_failed"

    sources = log.sources(rid)
    by_url = {s["url"]: s["title"] for s in sources}
    assert by_url == {
        "https://github.com/mcallegari/qlcplus": "QLC+ on GitHub",
        "https://github.com/mcallegari/qlcplus/releases": None,
    }


def test_runlog_timeline_on_real_websearch_stream(tmp_path):
    log = RunLog(tmp_path)
    rid = log.start("research", "qlc github", "haiku")
    for ev in _load_events("claude_stream_websearch.jsonl"):
        log.event(rid, ev)
    steps = log.timeline(rid)
    search_steps = [s for s in steps if s["kind"] == "search"]
    assert len(search_steps) == 1
    assert "QLC+ lighting software github repository" in search_steps[0]["title"]
    sources = log.sources(rid)
    assert any("github.com/mcallegari/qlcplus" in s["url"] for s in sources)


# ---------------------------------------------------------------------------
# Timeout path (a .bat stand-in "executable" that just sleeps; Windows can Popen a .bat
# directly without shell=True, unlike a bare .py file)
# ---------------------------------------------------------------------------

def _make_sleep_stub(tmp_path) -> Path:
    bat = tmp_path / "sleep_stub.bat"
    bat.write_text(f'@echo off\r\n"{sys.executable}" -c "import time; time.sleep(60)"\r\n', encoding="utf-8")
    return bat


def test_claude_code_backend_timeout_kills_process_tree(tmp_path):
    stub = _make_sleep_stub(tmp_path)
    log = RunLog(tmp_path / "data")
    backend = ClaudeCodeBackend(Config(), log, exe_path=stub)

    t0 = time.monotonic()
    result = asyncio.run(backend.run("hello", kind="design", model="haiku", timeout_s=1.0))
    elapsed = time.monotonic() - t0

    assert result.ok is False
    assert "timeout after" in result.error
    assert elapsed < 30  # generous: taskkill + wait should finish in a couple of seconds

    rows = log.runs()
    assert len(rows) == 1
    assert rows[0]["status"] == "timeout"
    assert rows[0]["kind"] == "design"


# ---------------------------------------------------------------------------
# FakeClaudeBackend
# ---------------------------------------------------------------------------

def test_fake_claude_backend_round_trip(tmp_path):
    log = RunLog(tmp_path)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    fake = FakeClaudeBackend([{"answer": "42"}, str(DATA / "claude_stream_schema.jsonl")], log)

    events_seen: list = []
    result1 = asyncio.run(fake.run("what is the answer?", kind="label", model="haiku", schema=schema,
                                    on_event=events_seen.append))
    assert result1.ok is True
    assert result1.data == {"answer": "42"}
    assert events_seen  # on_event fired for each replayed event

    result2 = asyncio.run(fake.run("what is 2+2?", kind="label", model="haiku", schema=schema))
    assert result2.ok is True
    assert result2.data == {"answer": "4"}  # from the real captured stream

    assert len(fake.calls) == 2
    assert fake.calls[0]["prompt"] == "what is the answer?"
    assert fake.calls[1]["kind"] == "label"

    with pytest.raises(AssertionError):
        asyncio.run(fake.run("one too many", kind="label", model="haiku"))

    rows = log.runs()
    assert len(rows) == 2
    assert {r["status"] for r in rows} == {"ok"}
