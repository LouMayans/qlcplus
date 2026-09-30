"""Headless Claude Code CLI connector: finds claude.exe, builds its argv, runs it as a
subprocess, and turns its stream-json output into a ClaudeResult - every run going through a
RunLog (start -> events -> finish, even on timeout/error).

Smoke-tested against claude.exe 2.1.284 (see the implementing agent's report for the raw
findings): -p --output-format stream-json requires --verbose or the CLI refuses to start; the
prompt can go on stdin (avoids the ~32K Windows command-line limit); --system-prompt-file and
--append-system-prompt-file both exist and take a path (so system_file is passed through as-is,
no size limit); --bare disables OAuth/keychain reads, so it cannot use the operator's
subscription login (confirmed: "Not logged in - Please run /login", zero cost).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

from lightai.config import Config, REPO_ROOT
from lightai.design.runlog import RunLog

# A CLI argument (not a file) over roughly this size risks the ~32K Windows command-line limit
# once combined with the rest of argv. Only the prompt-as-argument fallback needs this now that
# system prompts go through *-system-prompt-file (a path, not inlined content).
MAX_INLINE_ARG_CHARS = 30000

_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_EXT_NAME_RE = re.compile(r"claude-code-(\d+)\.(\d+)\.(\d+)-win32-x64")
_EXE_MEMO: dict = {}  # extensions dir -> (dir mtime, resolved exe or None); cheap, not cross-process


def _version_key(name: str) -> Optional[tuple]:
    m = _EXT_NAME_RE.search(name)
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def _newest_from_extensions(ext_dir: Path) -> Optional[Path]:
    if not ext_dir.is_dir():
        return None
    try:
        mtime = ext_dir.stat().st_mtime
    except OSError:
        return None
    cached = _EXE_MEMO.get(ext_dir)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    best_key: Optional[tuple] = None
    best_exe: Optional[Path] = None
    for child in ext_dir.iterdir():
        if not child.is_dir():
            continue
        key = _version_key(child.name)
        if key is None:
            continue
        exe = child / "resources" / "native-binary" / "claude.exe"
        if exe.is_file() and (best_key is None or key > best_key):
            best_key, best_exe = key, exe
    _EXE_MEMO[ext_dir] = (mtime, best_exe)
    return best_exe


def discover_claude_exe(cfg: Config) -> Optional[Path]:
    """cfg.claude_exe_path if set and existing; else the newest installed
    anthropic.claude-code-*-win32-x64 VS Code extension, comparing parsed (major, minor, patch)
    version tuples rather than sorting the folder names as strings; else ~/.local/bin/claude.exe;
    else whatever 'claude' resolves to on PATH."""
    if cfg.claude_exe_path:
        p = Path(cfg.claude_exe_path)
        if p.is_file():
            return p
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    found = _newest_from_extensions(home / ".vscode" / "extensions")
    if found is not None:
        return found
    local_bin = home / ".local" / "bin" / "claude.exe"
    if local_bin.is_file():
        return local_bin
    which = shutil.which("claude")
    if which:
        return Path(which)
    return None


@dataclass
class ClaudeResult:
    ok: bool
    data: Optional[dict]  # parsed structured output when a schema was given, else None
    text: str  # the final result event's text
    run_id: str
    usage: dict  # input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens (all ints)
    cost_usd: Optional[float]
    num_turns: int
    duration_ms: int
    error: Optional[str] = None
    thinking_tokens: int = 0
    server_tool_use: dict = field(default_factory=dict)  # {web_search_requests, web_fetch_requests}


def _zero_usage() -> dict:
    return {k: 0 for k in _USAGE_KEYS}


def extract_usage(usage: Optional[dict]) -> dict:
    usage = usage or {}
    return {k: int(usage.get(k) or 0) for k in _USAGE_KEYS}


def build_result(run_id: str, events: Sequence[dict], schema: Optional[dict]) -> ClaudeResult:
    """Turn a full stream-json event list into a ClaudeResult. Shared by ClaudeCodeBackend (the
    real CLI) and FakeClaudeBackend (canned streams) so both exercise the same parsing rules the
    smoke test discovered - in particular, where structured output and usage/cost live on the
    final 'result' event."""
    final = None
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "result":
            final = ev
    if final is None:
        return ClaudeResult(ok=False, data=None, text="", run_id=run_id, usage=_zero_usage(), cost_usd=None,
                             num_turns=0, duration_ms=0, error="no result event in the stream")
    raw_usage = final.get("usage") or {}
    usage = extract_usage(raw_usage)
    thinking_tokens = int(((raw_usage.get("output_tokens_details") or {}).get("thinking_tokens")) or 0)
    server_tool_use = raw_usage.get("server_tool_use")
    server_tool_use = dict(server_tool_use) if isinstance(server_tool_use, dict) else {}
    is_error = bool(final.get("is_error"))
    text = final.get("result") or ""
    data = None
    error = None
    if schema is not None:
        structured = final.get("structured_output")
        if isinstance(structured, dict):
            data = structured
        else:
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                data = None
        if data is None and not is_error:
            error = "no structured output"
    if is_error and not error:
        error = text or final.get("subtype") or "claude reported an error"
    ok = (not is_error) and (schema is None or data is not None)
    return ClaudeResult(ok=ok, data=data, text=text, run_id=run_id, usage=usage,
                         cost_usd=final.get("total_cost_usd"), num_turns=int(final.get("num_turns") or 0),
                         duration_ms=int(final.get("duration_ms") or 0), error=error,
                         thinking_tokens=thinking_tokens, server_tool_use=server_tool_use)


class DesignBackend(Protocol):
    async def run(self, prompt: str, *, kind: str, model: str, schema: Optional[dict] = None,
                  system_file: Optional[Path] = None, replace_system_prompt: bool = False,
                  allowed_tools: Sequence[str] = (), disallowed_tools: Sequence[str] = (),
                  agents: Optional[dict] = None, timeout_s: float = 600.0, budget_usd: Optional[float] = None,
                  on_event: Optional[Callable[[dict], None]] = None) -> ClaudeResult:
        """on_event, when given, may be invoked from a worker thread (not the caller's event loop
        thread) by a real backend - make it thread-safe."""
        ...


def build_command_line(
    exe: Path,
    *,
    model: str,
    schema: Optional[dict] = None,
    system_file: Optional[Path] = None,
    replace_system_prompt: bool = False,
    allowed_tools: Sequence[str] = (),
    disallowed_tools: Sequence[str] = (),
    agents: Optional[dict] = None,
    budget_usd: Optional[float] = None,
    prompt_arg: Optional[str] = None,
) -> list:
    """The claude.exe argv. The prompt goes on stdin (confirmed working in the smoke test) and is
    not part of this argv unless prompt_arg is given (a fallback ClaudeCodeBackend does not use by
    default, kept for the case stdin is ever unavailable); system_file is passed by path to
    --system-prompt-file (replace_system_prompt=True) or --append-system-prompt-file (default) -
    both flags exist on this CLI and take a path, so there is no inline-content size limit there."""
    argv = [str(exe), "-p", "--output-format", "stream-json", "--verbose", "--model", model, "--no-session-persistence",
            "--strict-mcp-config"]  # no MCP connectors: their tool definitions cost tokens on every run
    # only the built-in tools the job needs are loaded at all (most of a run's fixed ~30K tokens are tool schemas)
    tools = [t for t in allowed_tools if t not in disallowed_tools] + (["Agent"] if agents else [])
    argv += ["--tools", ",".join(tools)]
    if schema is not None:
        argv += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    if system_file is not None:
        flag = "--system-prompt-file" if replace_system_prompt else "--append-system-prompt-file"
        argv += [flag, str(system_file)]
    if allowed_tools:
        argv += ["--allowedTools", ",".join(allowed_tools)]
    if disallowed_tools:
        argv += ["--disallowedTools", ",".join(disallowed_tools)]
    if agents:
        argv += ["--agents", json.dumps(agents, separators=(",", ":"))]
    if budget_usd is not None:
        argv += ["--max-budget-usd", str(budget_usd)]
    if prompt_arg is not None:
        if len(prompt_arg) > MAX_INLINE_ARG_CHARS:
            raise ValueError(f"prompt is {len(prompt_arg)} chars, over the {MAX_INLINE_ARG_CHARS}-char safety limit "
                              "for a command-line argument; it should be sent on stdin instead")
        argv.append(prompt_arg)
    return argv


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
    except Exception:
        pass


class ClaudeCodeBackend:
    """Runs claude.exe headlessly. Never logs or stores the operator's credentials or environment -
    the subprocess inherits the parent environment (needed for its OAuth/keychain login to work),
    but nothing from it is read, printed, or written to the run log."""

    def __init__(self, cfg: Config, runlog: RunLog, *, exe_path: Optional[Path] = None) -> None:
        self.cfg = cfg
        self.runlog = runlog
        self._exe_override = exe_path  # tests point this at a stand-in executable

    async def run(self, prompt: str, *, kind: str, model: str, schema: Optional[dict] = None,
                  system_file: Optional[Path] = None, replace_system_prompt: bool = False,
                  allowed_tools: Sequence[str] = (), disallowed_tools: Sequence[str] = (),
                  agents: Optional[dict] = None, timeout_s: float = 600.0, budget_usd: Optional[float] = None,
                  on_event: Optional[Callable[[dict], None]] = None, thinking_tokens: Optional[int] = None) -> ClaudeResult:
        """Runs claude.exe via subprocess.Popen on a worker thread (asyncio.to_thread), not
        asyncio.create_subprocess_exec: under uvicorn on Windows the running event loop may be a
        SelectorEventLoop, which cannot spawn subprocesses (ProactorEventLoop only). Because of
        that thread, on_event may be called from a worker thread rather than the event loop
        thread - keep it thread-safe (e.g. hand off via a thread-safe queue rather than touching
        asyncio state directly)."""
        return await asyncio.to_thread(
            self._run_sync, prompt, kind, model, schema, system_file, replace_system_prompt,
            tuple(allowed_tools), tuple(disallowed_tools), agents, timeout_s, budget_usd, on_event, thinking_tokens,
        )

    def _fail(self, run_id: str, error: str, duration_ms: int = 0) -> ClaudeResult:
        result = ClaudeResult(ok=False, data=None, text="", run_id=run_id, usage=_zero_usage(), cost_usd=None,
                               num_turns=0, duration_ms=duration_ms, error=error)
        self.runlog.finish(run_id, result)
        return result

    def _run_sync(self, prompt: str, kind: str, model: str, schema: Optional[dict], system_file: Optional[Path],
                  replace_system_prompt: bool, allowed_tools: Sequence[str], disallowed_tools: Sequence[str],
                  agents: Optional[dict], timeout_s: float, budget_usd: Optional[float],
                  on_event: Optional[Callable[[dict], None]], thinking_tokens: Optional[int] = None) -> ClaudeResult:
        run_id = self.runlog.start(kind, prompt, model)
        exe = self._exe_override or discover_claude_exe(self.cfg)
        if exe is None:
            return self._fail(run_id, "claude executable not found (checked cfg.claude_exe_path, the VS Code "
                                       "extension folder, ~/.local/bin, and PATH)")
        try:
            argv = build_command_line(exe, model=model, schema=schema, system_file=system_file,
                                       replace_system_prompt=replace_system_prompt, allowed_tools=allowed_tools,
                                       disallowed_tools=disallowed_tools, agents=agents, budget_usd=budget_usd)
        except ValueError as exc:
            return self._fail(run_id, str(exc))

        t0 = time.monotonic()
        try:
            # MAX_THINKING_TOKENS caps Claude Code's thinking budget (labelling needs little; Haiku otherwise spends
            # minutes thinking over a short batch)
            env = dict(os.environ, MAX_THINKING_TOKENS=str(int(thinking_tokens))) if thinking_tokens is not None else None
            proc = subprocess.Popen(argv, cwd=str(REPO_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env)
        except OSError as exc:
            return self._fail(run_id, f"failed to start claude: {exc}")

        events: list = []
        stderr_chunks: list = []
        killed = threading.Event()

        def _drain_stderr() -> None:
            try:
                for chunk in iter(lambda: proc.stderr.read(4096), ""):
                    stderr_chunks.append(chunk)
            except Exception:
                pass

        def _on_timeout() -> None:
            killed.set()
            _kill_tree(proc.pid)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()
        timer = threading.Timer(timeout_s, _on_timeout)
        timer.daemon = True
        timer.start()
        try:
            try:
                proc.stdin.write(prompt)
            except Exception:
                pass  # a killed/crashed process breaks the pipe; the timeout/error path below reports it
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue  # tolerate a malformed line rather than aborting the run
                events.append(ev)
                self.runlog.event(run_id, ev)
                if on_event is not None:
                    try:
                        on_event(ev)
                    except Exception:
                        pass
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                killed.set()
                _kill_tree(proc.pid)
        finally:
            timer.cancel()
            stderr_thread.join(5)

        duration_ms = int((time.monotonic() - t0) * 1000)
        stderr_text = "".join(stderr_chunks)[-4096:]
        if killed.is_set():
            return self._fail(run_id, f"timeout after {timeout_s:g} s", duration_ms=duration_ms)
        if any(isinstance(e, dict) and e.get("type") == "result" for e in events):
            result = build_result(run_id, events, schema)
            if not result.ok and not result.error and stderr_text:
                result.error = stderr_text
            self.runlog.finish(run_id, result)
            return result
        msg = f"claude exited {proc.returncode} with no result event"
        if stderr_text:
            msg += f": {stderr_text}"
        return self._fail(run_id, msg, duration_ms=duration_ms)
