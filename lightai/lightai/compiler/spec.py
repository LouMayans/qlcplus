"""Data classes shared by recipes, the XML generator and the validator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class StepSpec:
    function_id: int
    fade_in: int = 0
    hold: int = 0
    fade_out: int = 0
    note: Optional[str] = None


@dataclass
class EfxFixtureSpec:
    id: int
    head: int = 0
    mode: int = 0
    direction: str = "Forward"
    start_offset: int = 0


@dataclass
class FunctionSpec:
    id: int
    type: str
    name: str
    path: str = ""
    priority: int = 0
    fade_in: int = 0
    fade_out: int = 0
    duration: int = 0
    values: dict = field(default_factory=dict)
    roles: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)
    direction: str = "Forward"
    run_order: str = "Loop"
    speed_modes: tuple = ("Common", "Common", "Common")
    efx: dict = field(default_factory=dict)
    efx_fixtures: list = field(default_factory=list)
    members: list = field(default_factory=list)

    def refs(self) -> list:
        if self.type == "Chaser":
            return [s.function_id for s in self.steps]
        if self.type == "Collection":
            return list(self.members)
        return []


@dataclass
class LookParams:
    recipe: str
    targets: list
    target_label: str = ""
    colors: list = field(default_factory=list)
    intensity: float = 1.0
    bpm: Optional[float] = None
    period_ms: Optional[int] = None
    rate_word: Optional[str] = None
    fade_ms: Optional[int] = None
    strobe_speed: Optional[float] = None
    size: Optional[str] = None
    priority: int = 0
    mirror: bool = False
    name: Optional[str] = None
    category: Optional[str] = None
    color_map: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Look:
    recipe: str
    main_id: int
    name: str
    functions: list
    params: LookParams
    assumptions: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    facts_used: list = field(default_factory=list)
    preview: list = field(default_factory=list)

    @property
    def ids(self) -> list:
        return [f.id for f in self.functions]

    def summary(self) -> dict:
        return {
            "recipe": self.recipe,
            "main_id": self.main_id,
            "name": self.name,
            "functions": [{"id": f.id, "type": f.type, "name": f.name} for f in self.functions],
            "assumptions": self.assumptions,
            "skipped": self.skipped,
            "params": self.params.to_dict(),
        }
