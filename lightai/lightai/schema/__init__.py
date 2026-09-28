"""The JSON contracts: labels, LightCommand (model output), Plan (executor input), OutcomeFeedback."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field

SCHEMA_VERSION = 1
LABELS_PATH = Path(__file__).with_name("labels.yaml")


@lru_cache(maxsize=1)
def labels() -> dict:
    doc = yaml.safe_load(LABELS_PATH.read_text(encoding="utf-8"))
    intents = list(doc["intents"].keys())
    slots = list(doc["slots"].keys())
    bio = ["O"] + [f"{p}-{s}" for s in slots for p in ("B", "I")]
    return {"version": int(doc["labels_version"]), "intents": intents, "slots": slots, "bio": bio}


class Span(BaseModel):
    slot: str
    start: int
    end: int
    text: str
    confidence: float = 1.0


class SlotValue(BaseModel):
    raw: str
    value: dict[str, Any] = Field(default_factory=dict)


class LightCommand(BaseModel):
    schema_version: int = SCHEMA_VERSION
    labels_version: int = 1
    model_version: str = ""
    text: str
    words: list[str] = Field(default_factory=list)
    intent: str
    confidence: float
    intent_top: list[tuple[str, float]] = Field(default_factory=list)
    spans: list[Span] = Field(default_factory=list)
    slots: dict[str, list[SlotValue]] = Field(default_factory=dict)
    ambiguities: list[str] = Field(default_factory=list)
    needs_confirmation: bool = False
    clarify: Optional[str] = None
    latency_ms: float = 0.0

    def first(self, slot: str) -> Optional[SlotValue]:
        vals = self.slots.get(slot) or []
        return vals[0] if vals else None


class Action(BaseModel):
    op: str
    args: dict[str, Any] = Field(default_factory=dict)
    describe: str = ""
    wire: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    schema_version: int = SCHEMA_VERSION
    plan_id: str
    intent: str
    mode: str
    summary: str
    actions: list[Action] = Field(default_factory=list)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_confirmation: bool = False
    look: Optional[dict[str, Any]] = None
    followup: Optional[dict[str, Any]] = None


IssueKind = Literal["color", "fixture", "movement", "dark", "speed", "intensity", "size", "model", "meaning"]
Direction = Literal["too_fast", "too_slow", "too_dim", "too_bright", "too_small", "too_big"]


class Issue(BaseModel):
    kind: IssueKind
    fixture_id: Optional[int] = None
    expected: Optional[str] = Field(None, max_length=64)
    observed: Optional[str] = Field(None, max_length=64)
    direction: Optional[Direction] = None


class OutcomeFeedback(BaseModel):
    schema_version: int = SCHEMA_VERSION
    plan_id: str = Field(max_length=64)
    verdict: Literal["ok", "wrong", "adjust"]
    issues: list[Issue] = Field(default_factory=list, max_length=20)
    free_text: str = Field("", max_length=500)
