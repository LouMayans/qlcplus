"""The nightly teacher: Claude reads the conversations the local model struggled with and writes training examples.

History rows (history.jsonl) are grouped into sessions: the console tab, split after 10 idle minutes. A session is
worth teaching from when it holds a correction ("no, I meant ..."), a clarify question, a command the model didn't
understand or was unsure of (confidence < 0.8), or a change that failed. Claude (Haiku by default) gets those
sessions with the label vocabulary and returns bracket-markup labels for the misunderstood originals, using what
the operator said they meant, plus labels for the correction phrasings themselves and new words it noticed.

Clear cases (confidence >= 0.8) go into corrections.jsonl as {source: "claude", weight: 1, evidence}: the nightly
retrain uses them below the operator's own corrections (weight 3) and the promotion gates stay. Unclear cases and
new words wait in review_queue.jsonl. Every teaching run shows in the console's Claude tab."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

GAP_S = 600
BATCH_ROWS = 40
MIN_CONFIDENCE = 0.8


class TeachItem(BaseModel):
    text: str = Field(description="the operator's command exactly as typed")
    intent: str
    marked: str = Field(description="the same text with [slot:words] markup, e.g. 'slow [color:blue] [target:washes]'")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    evidence: list[str] = Field(default_factory=list, description="plan_ids of the history rows this is based on")


class NewTerm(BaseModel):
    word: str
    meaning: str
    kind: Literal["alias", "color", "place", "model", "mood", "other"] = "other"


class TeachResult(BaseModel):
    items: list[TeachItem] = Field(default_factory=list)
    new_terms: list[NewTerm] = Field(default_factory=list)


def _ts(row: dict) -> float:
    try:
        return datetime.fromisoformat(row["ts"]).timestamp()
    except (KeyError, ValueError, TypeError):
        return 0.0


def read_history(path: Path) -> list:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def sessions(rows: list, since: float = 0.0) -> list:
    """Command rows grouped into conversations (per console session, split after 10 idle minutes), each command with
    what happened when it ran; only rows newer than `since`."""
    done = {r["plan_id"]: r for r in rows if "executed" in r and r.get("plan_id")}
    cmds = [dict(r, **({"executed": done[r["plan_id"]]["executed"], "result": done[r["plan_id"]].get("note")} if r.get("plan_id") in done else {}))
            for r in rows if "text" in r and _ts(r) > since]
    out: list = []
    last: dict = {}
    for r in sorted(cmds, key=_ts):
        key = r.get("session") or "-"
        cur = last.get(key)
        if cur is None or _ts(r) - _ts(cur[-1]) > GAP_S:
            cur = []
            out.append(cur)
            last[key] = cur
        cur.append(r)
    return out


def worth_teaching(session: list) -> bool:
    return any(r.get("corrects") or r.get("mode") == "clarify" or r.get("intent") == "none"
               or (isinstance(r.get("confidence"), (int, float)) and r["confidence"] < MIN_CONFIDENCE)
               or r.get("executed") is False for r in session)


TASK = (
    "Label these lightai console conversations to train the local model. For EVERY command below that the model got "
    "wrong or was unsure of - it asked to clarify, said 'none', had confidence under 0.8, failed to run, or a later "
    "message corrects it - return one item: the command text exactly as typed, the right intent, the same text with "
    "[slot:words] markup, your confidence, a one-line reason, and the plan ids you used as evidence. Commands that were "
    "understood and ran fine need no item. Return an empty list only when nothing needs a label.\n\n")


def brief() -> str:
    """The label vocabulary and the markup rules, for Claude."""
    from lightai.schema import LABELS_PATH

    return (
        "You label nightclub lighting commands for a small local model (DistilBERT intent + BIO slots). For each command "
        "the model got wrong or was unsure of, give the intent and the same text with [slot:words] markup around the "
        "exact words (keep every word and its order; only add brackets). Use what the operator said they meant in a "
        "later correction ('no, I meant the spots' means the earlier command's target was the spots). Also label the "
        "correction phrasings themselves when they carry a new command. Give confidence < 0.8 whenever you are unsure "
        "(e.g. a retyped command that may be a new request). Report new words (nicknames for fixtures, colors, places, "
        "moods) in new_terms. Only these intents and slots exist:\n\n" + LABELS_PATH.read_text(encoding="utf-8")
    )


def valid_item(item: TeachItem) -> Optional[str]:
    """Why the label can't be used, or None."""
    from lightai.nlu.text import parse_markup, split_words
    from lightai.schema import labels

    L = labels()
    if item.intent not in L["intents"]:
        return f"unknown intent {item.intent}"
    try:
        plain, words, tags, _ = parse_markup(item.marked)
    except ValueError as exc:
        return f"bad markup: {exc}"
    if [w.lower() for w in words] != [w.lower() for w in split_words(item.text)]:
        return "the markup changes the words"
    bad = sorted({t[2:] for t in tags if t != "O" and t[2:] not in L["slots"]})
    return f"unknown slots {bad}" if bad else None


async def label_sessions(ai, backend, *, max_sessions: int = 30) -> dict:
    """Teach from the sessions since the last run; advances the cursor in teacher_state.json."""
    from lightai.schema import labels

    cfg = ai.cfg
    data = Path(cfg.data_dir)
    state_path = data / "teacher_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    since = float(state.get("cursor") or 0.0)
    all_sessions = sessions(read_history(data / "history.jsonl"), since)
    todo = [s for s in all_sessions if worth_teaching(s)][:max_sessions]
    summary = {"sessions": len(todo), "labeled": 0, "queued": 0, "rejected": 0, "terms": 0, "runs": []}
    if not todo:
        state.update(cursor=max((_ts(r) for s in all_sessions for r in s), default=since), last_run=time.time())
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return summary
    ctx = data / "teacher_ctx.md"
    ctx.write_text(brief(), encoding="utf-8")
    batches, cur = [], []
    for s in todo:
        if cur and sum(len(x) for x in cur) + len(s) > BATCH_ROWS:
            batches.append(cur)
            cur = []
        cur.append(s)
    if cur:
        batches.append(cur)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    version = labels()["version"]
    for batch in batches:
        lines = []
        for k, s in enumerate(batch, 1):
            lines.append(f"## Conversation {k}")
            for r in s:
                what = f"{r.get('intent')} ({r.get('confidence')})" if r.get("confidence") is not None else r.get("intent")
                ran = "" if "executed" not in r else (" -> ran" if r["executed"] else f" -> failed: {r.get('result') or ''}")
                corr = f" [corrects {r['corrects']}]" if r.get("corrects") else ""
                lines.append(f"- plan {r.get('plan_id') or r.get('design_job')}: \"{r['text']}\" -> {what}: {str(r.get('summary') or '')[:160]}{ran}{corr}")
        # the task leads the message and the brief REPLACES Claude Code's own system prompt: with only an appended
        # brief, Haiku read the conversation as a chat ("what would you like help with?") and returned no labels
        res = await backend.run(TASK + "\n".join(lines), kind="label", model=getattr(cfg, "claude_model_label", "haiku"),
                                schema=TeachResult.model_json_schema(), system_file=ctx, replace_system_prompt=True,
                                allowed_tools=(), thinking_tokens=2048,
                                timeout_s=float(getattr(cfg, "label_timeout_s", 60.0)),
                                budget_usd=getattr(cfg, "claude_budget_label_usd", None))
        summary["runs"].append(res.run_id)
        if not res.ok:
            summary.setdefault("errors", []).append(res.error)
            continue
        try:
            result = TeachResult.model_validate(res.data or {})
        except ValidationError as exc:
            summary.setdefault("errors", []).append(f"bad answer: {exc.errors()[0]['msg']}")
            continue
        with open(data / "corrections.jsonl", "a", encoding="utf-8") as corr, open(data / "review_queue.jsonl", "a", encoding="utf-8") as review:
            for item in result.items:
                why = valid_item(item)
                row = {"ts": now, "labels_version": version, "model_version": getattr(getattr(ai, "model", None), "version", None),
                       "text": item.text, "predicted": {}, "corrected": {"intent": item.intent, "marked": item.marked},
                       "source": "claude", "weight": 1, "evidence": item.evidence, "reason": item.reason,
                       "confidence": item.confidence, "run_id": res.run_id}
                if why:
                    summary["rejected"] += 1
                    review.write(json.dumps(dict(row, kind="label", accepted=False, problem=why)) + "\n")
                elif item.confidence >= MIN_CONFIDENCE:
                    summary["labeled"] += 1
                    corr.write(json.dumps(dict(row, accepted=True)) + "\n")
                else:
                    summary["queued"] += 1
                    review.write(json.dumps(dict(row, kind="label", accepted=False)) + "\n")
            for term in result.new_terms:
                summary["terms"] += 1
                review.write(json.dumps({"ts": now, "kind": "term", "term_kind": term.kind, "word": term.word,
                                         "meaning": term.meaning, "run_id": res.run_id}) + "\n")
    state.update(cursor=max((_ts(r) for s in all_sessions for r in s), default=since), last_run=time.time(),
                 last_summary={k: v for k, v in summary.items() if k != "runs"})
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return summary
