"""Per-operator session state: tapped BPM, last command/plan, active overrides, calibration."""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Optional

from lightai.schema import LightCommand, Plan


@dataclass
class Calibration:
    fixture_id: int
    mode: str
    steps: list
    index: int = 0
    answers: list = field(default_factory=list)
    color: Optional[str] = None
    plan_id: str = ""
    dirty: bool = False  # a fact was saved during this calibration


class Session:
    def __init__(self, max_history: int = 200) -> None:
        self.bpm: Optional[float] = None
        self.bpm_source: Optional[str] = None
        self.taps: deque = deque(maxlen=8)
        self.last_command: Optional[LightCommand] = None
        self.last_plan: Optional[Plan] = None
        self.last_action_plan: Optional[Plan] = None
        self.plans: OrderedDict = OrderedDict()  # least recently used plans are dropped first
        self.history: deque = deque(maxlen=max_history)
        self.overridden: set = set()
        self.gm_changed = False
        self.calibration: Optional[Calibration] = None
        self.rated: dict = {}  # feedback fingerprints already applied (double-click guard)
        self._n = 0

    def new_plan_id(self) -> str:
        self._n += 1
        return f"p{int(time.time()) % 100000:05d}-{self._n:03d}"

    def store(self, plan: Plan) -> None:
        self.plans[plan.plan_id] = plan
        self.plans.move_to_end(plan.plan_id)
        while len(self.plans) > 300:
            self.plans.popitem(last=False)

    def get(self, plan_id: str) -> Optional[Plan]:
        p = self.plans.get(plan_id)
        if p is not None:
            self.plans.move_to_end(plan_id)
        return p

    def remember(self, cmd: Optional[LightCommand], plan: Plan) -> None:
        self.store(plan)
        if cmd is not None:
            self.last_command = cmd
        self.last_plan = plan
        if plan.intent not in ("feedback", "query.functions", "query.fixtures", "query.status", "none", "set_bpm") and plan.mode != "clarify":
            self.last_action_plan = plan
        self.history.append({"t": time.time(), "text": cmd.text if cmd else "", "intent": plan.intent, "plan_id": plan.plan_id, "summary": plan.summary})

    def tap(self, t: Optional[float] = None) -> Optional[float]:
        t = time.monotonic() if t is None else t
        if self.taps and t - self.taps[-1] > 2.0:
            self.taps.clear()
        self.taps.append(t)
        if len(self.taps) >= 3:
            gaps = [b - a for a, b in zip(list(self.taps)[:-1], list(self.taps)[1:])]
            avg = sum(gaps) / len(gaps)
            if avg > 0:
                bpm = 60.0 / avg
                while bpm < 40.0:  # tapping every other beat (or every beat of a fast track) is still the same tempo
                    bpm *= 2.0
                while bpm > 220.0:
                    bpm /= 2.0
                self.set_bpm(round(bpm, 1), "tap")
        return self.bpm

    def set_bpm(self, bpm: float, source: str = "typed") -> None:
        self.bpm = max(40.0, min(220.0, float(bpm)))
        self.bpm_source = source
