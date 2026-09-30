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
class GroupHeadSpec:
    """One <Head> of a <FixtureGroup>: a grid cell (x, y) -> (fixture ID, head index)."""

    x: int
    y: int
    fixture: int
    head: int = 0


@dataclass
class ShowItemSpec:
    """One QLC+ <ShowFunction>: a function placed on a Show track's timeline."""

    function_id: int
    start_ms: int
    duration_ms: int = 0
    color: Optional[str] = None


@dataclass
class TrackSpec:
    """One QLC+ <Track> inside a Show: an ordered lane of ShowItemSpecs."""

    id: int
    name: str
    items: list = field(default_factory=list)
    scene_id: Optional[int] = None
    mute: bool = False


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
    tracks: list = field(default_factory=list)  # TrackSpecs; only meaningful for type == "Show"
    time_division: tuple = ("Time", 120)  # (Type, BPM); Type is Time/BPM_4_4/BPM_3_4/BPM_2_4
    group_size: tuple = (0, 0)  # (X, Y) grid size; only meaningful for type == "FixtureGroup"
    group_heads: list = field(default_factory=list)  # GroupHeadSpecs; only meaningful for type == "FixtureGroup"
    matrix: dict = field(default_factory=dict)  # algorithm/colors/control mode/fixture group ref; only meaningful for type == "RGBMatrix"

    def refs(self) -> list:
        if self.type == "Chaser":
            return [s.function_id for s in self.steps]
        if self.type == "Collection":
            return list(self.members)
        if self.type == "Show":
            return [it.function_id for tr in self.tracks for it in tr.items]
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
    order: Optional[str] = None  # spatial order from the 3D stage: left_to_right, center_out, circular, ...
    aim: Optional[str] = None  # a place in the stage file: "dj", "dance floor", "bar", ... ("down" = straight down)
    spread: Optional[str] = None  # how aimed beams share the place: together, fan, cross
    layer: Optional[str] = None  # inside a designed show: base, movement, fx, strobe, accent

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
    # <FixtureGroup> specs this look adds (pixel looks). QLC+ numbers groups apart from functions, so they are never in
    # `ids`: a group ID can equal an unrelated function's ID. Written before the functions; left in place on removal
    # (other looks may reuse the same group).
    groups: list = field(default_factory=list)

    @property
    def ids(self) -> list:
        return [f.id for f in self.functions]

    def summary(self) -> dict:
        return {
            "recipe": self.recipe,
            "main_id": self.main_id,
            "name": self.name,
            "functions": [{"id": f.id, "type": f.type, "name": f.name} for f in self.functions],
            **({"fixture_groups": [{"id": g.id, "name": g.name} for g in self.groups]} if self.groups else {}),
            "assumptions": self.assumptions,
            "skipped": self.skipped,
            "params": self.params.to_dict(),
        }
