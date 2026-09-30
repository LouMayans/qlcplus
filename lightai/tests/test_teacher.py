"""The nightly teacher with a stand-in Claude: sessions worth teaching, clear labels to training (weight 1), unclear
ones and bad markup to review, the cursor, and the training weights (operator 3, Claude 1)."""

import asyncio
import json
from types import SimpleNamespace

from lightai.design.teacher import TeachResult, label_sessions, sessions, worth_teaching


class Backend:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def run(self, prompt, **kw):
        self.calls.append({"prompt": prompt, **kw})
        return SimpleNamespace(ok=True, data=self.answer, run_id=f"r{len(self.calls)}", error=None)


def history(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


ROWS = [
    {"ts": "2026-09-29T20:00:00+00:00", "plan_id": "p1", "text": "add some fog", "intent": "none", "confidence": 0.97,
     "mode": "clarify", "summary": "That doesn't sound like a lighting request.", "session": "s1"},
    {"ts": "2026-09-29T20:00:20+00:00", "plan_id": "p2", "text": "fog on", "intent": "set_level", "confidence": 0.99,
     "mode": "live", "summary": "fog to 100%", "session": "s1"},
    {"ts": "2026-09-29T20:00:21+00:00", "plan_id": "p2", "executed": True, "note": ""},
    {"ts": "2026-09-29T21:30:00+00:00", "plan_id": "p3", "text": "washes at 50%", "intent": "set_level", "confidence": 0.99,
     "mode": "live", "summary": "washes to 50%", "session": "s1"},
]
ANSWER = {
    "items": [
        {"text": "add some fog", "intent": "set_level", "marked": "add some [target:fog]", "confidence": 0.9,
         "reason": "the operator then said 'fog on'", "evidence": ["p1", "p2"]},
        {"text": "add some fog", "intent": "set_level", "marked": "add [intensity:some] [target:fog]", "confidence": 0.5},
        {"text": "add some fog", "intent": "set_level", "marked": "add more [target:fog]", "confidence": 0.95},
    ],
    "new_terms": [{"word": "some fog", "meaning": "fog on at a medium level", "kind": "mood"}],
}


def test_sessions_and_what_is_worth_teaching():
    s = sessions(ROWS)
    assert [len(x) for x in s] == [2, 1], "a 90-minute gap starts a new conversation"
    assert s[0][1]["executed"] is True
    assert worth_teaching(s[0]) and not worth_teaching(s[1])


def test_label_sessions(tmp_path):
    history(tmp_path / "history.jsonl", ROWS)
    TeachResult.model_validate(ANSWER)
    ai = SimpleNamespace(cfg=SimpleNamespace(data_dir=tmp_path, claude_model_label="haiku", label_timeout_s=60.0,
                                             claude_budget_label_usd=0.05), model=SimpleNamespace(version="v7"))
    backend = Backend(ANSWER)
    out = asyncio.run(label_sessions(ai, backend))
    assert out["sessions"] == 1 and out["labeled"] == 1 and out["queued"] == 1 and out["rejected"] == 1 and out["terms"] == 1, out
    call = backend.calls[0]
    assert call["kind"] == "label" and call["model"] == "haiku" and call["allowed_tools"] == ()
    assert '"add some fog"' in call["prompt"] and "fog on" in call["prompt"]
    corr = [json.loads(line) for line in (tmp_path / "corrections.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(corr) == 1 and corr[0]["source"] == "claude" and corr[0]["weight"] == 1 and corr[0]["accepted"]
    assert corr[0]["evidence"] == ["p1", "p2"] and corr[0]["corrected"]["marked"] == "add some [target:fog]"
    review = [json.loads(line) for line in (tmp_path / "review_queue.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r.get("kind") for r in review} == {"label", "term"}
    assert any(r.get("problem") == "the markup changes the words" for r in review)
    again = asyncio.run(label_sessions(ai, backend))
    assert again["sessions"] == 0 and len(backend.calls) == 1, "the cursor stops the same conversations being taught twice"


def test_training_weights(tmp_path):
    from lightai.schema import labels
    from lightai.train.generate import read_corrections

    v = labels()["version"]
    rows = [
        {"labels_version": v, "text": "fog on", "corrected": {"intent": "set_level", "marked": "[target:fog] [intensity:on]"},
         "accepted": True, "source": "console"},
        {"labels_version": v, "text": "add some fog", "corrected": {"intent": "set_level", "marked": "add some [target:fog]"},
         "accepted": True, "source": "claude", "weight": 1},
        {"labels_version": v, "text": "x", "corrected": {"intent": "set_level", "marked": "[target:fog]"}, "accepted": False,
         "source": "claude", "weight": 1},
    ]
    (tmp_path / "c.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    got = read_corrections(tmp_path / "c.jsonl", v)
    assert [(r["marked"], r["weight"]) for r in got] == [("[target:fog] [intensity:on]", None), ("add some [target:fog]", 1)]
