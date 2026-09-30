"""What Claude returns for a design request: shows made of sections made of layered looks.

Everything that names fixtures or places is a phrase ("the washes", "front row", "dj") that lightai resolves itself
against the show and its 3D stage; Claude never gives fixture or function IDs, so it can't invent fixtures.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Layer = Literal["base", "movement", "position", "fx", "strobe", "accent"]
Rate = Literal["very_slow", "slow", "medium", "fast", "very_fast"]


class LayeredLook(BaseModel):
    """One layer of a section: a recipe on some fixtures. Layers of a section run together."""
    layer: Layer = "base"
    recipe: str = Field(description="a recipe from the catalog, e.g. color_wash, color_morph, strobe_burst, circle_wave, position")
    targets: list[str] = Field(min_length=1, max_length=6, description="fixture phrases: zones, models, names, stage groups")
    colors: list[str] = Field(default_factory=list, max_length=8)
    intensity: float = Field(1.0, ge=0.0, le=1.0)
    rate: Optional[Rate] = None
    fade_ms: Optional[int] = Field(None, ge=0, le=20000)
    strobe_speed: Optional[float] = Field(None, ge=0.0, le=1.0)
    size: Optional[Literal["tiny", "small", "medium", "big", "huge"]] = None
    mirror: bool = False
    order: Optional[str] = Field(None, description="spatial order: left_to_right, right_to_left, back_to_front, front_to_back, center_out, outside_in, circular, counter_circular, top_down, bottom_up, random")
    aim: Optional[str] = Field(None, description="a place from the stage (dj, dance floor, bar, ...) or 'down'")
    spread: Optional[Literal["together", "fan", "cross"]] = None
    priority: int = Field(0, ge=0, le=100)


class Section(BaseModel):
    """A part of a show; the show steps from section to section."""
    name: str = Field(min_length=1, max_length=40)
    bars: Optional[int] = Field(None, ge=1, le=64, description="length in 4/4 bars at the show's BPM")
    seconds: Optional[float] = Field(None, ge=2, le=600)
    transition_fade_ms: int = Field(0, ge=0, le=10000)
    looks: list[LayeredLook] = Field(min_length=1, max_length=6)


class ShowSpec(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    description: str = ""
    mood_tags: list[str] = Field(default_factory=list)
    bpm: Optional[float] = Field(None, ge=60, le=200)
    minutes: Optional[float] = Field(None, gt=0, le=60, description="only when the request gives a length")
    sections: list[Section] = Field(min_length=1, max_length=12)


class NewTerm(BaseModel):
    word: str
    meaning: str


class DesignResult(BaseModel):
    shows: list[ShowSpec] = Field(min_length=1, max_length=6)
    notes: str = ""
    new_terms: list[NewTerm] = Field(default_factory=list)


def design_schema() -> dict:
    """The JSON schema passed to claude.exe --json-schema."""
    return DesignResult.model_json_schema()
