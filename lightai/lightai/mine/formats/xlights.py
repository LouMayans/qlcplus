"""xLights ``.xsq`` sequence decoder.

``.xsq`` is xLights' own plain-XML sequence format: a ``<head>`` with song/artist/duration, a
list of named ``<ColorPalette>`` swatches, a ``<DisplayElements>`` list of model/group/timing
tracks, and an ``<ElementEffects>`` tree that places actual effects (by name -- Bars, Butterfly,
ColorWash, Shockwave, Strobe, ...) on models with start/end times in milliseconds and a palette
index. A ``type="timing"`` track's "effects" aren't effects at all: they're labeled intervals
(the `label` attribute, often empty) used as a song-structure grid, which is what this decoder
uses for ``sections`` when one is present.

Field names here were confirmed against a real xLights export (teslamotors/light-show on GitHub,
GPL-3.0 licensed) rather than guessed from the manual alone.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

from .base import (
    Cue,
    ReferenceShow,
    Section,
    Song,
    clamp01,
    dedupe,
    energy_curve,
    kid,
    kids,
    load_root,
    local,
    norm_hex,
    quick_root_tag,
    register,
    spatial_moves_from_cues,
    text_of,
    windowed_sections,
)

_FIXTURE_TYPES = {"model", "modelgroup"}


def _ms(v: Optional[str]) -> float:
    try:
        return round(int(v) / 1000.0, 3)
    except (TypeError, ValueError):
        return 0.0


def _to_float(v: Optional[str]) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _read_palettes(root) -> list[list[str]]:
    """Each ``<ColorPalette>`` is a comma-separated KEY=VALUE string; the "enabled" swatches for
    that palette are the ``C_BUTTON_PaletteN`` colors whose matching ``C_CHECKBOX_PaletteN`` is
    "1" (xLights' own UI convention -- unchecked slots aren't part of the effective palette)."""
    out = []
    for cp in kids(kid(root, "ColorPalettes"), "ColorPalette"):
        kv = {}
        for part in (cp.text or "").split(","):
            if "=" in part:
                k, _, v = part.partition("=")
                kv[k.strip()] = v.strip()
        colors = []
        for i in range(1, 9):
            if kv.get(f"C_CHECKBOX_Palette{i}") == "1":
                hexval = norm_hex(kv.get(f"C_BUTTON_Palette{i}"))
                if hexval:
                    colors.append(hexval)
        out.append(colors)
    return out


def _timing_sections(timing_tracks: dict[str, list[tuple[float, float, Optional[str]]]],
                      curve: list[tuple[float, float]]) -> list[Section]:
    """Pick the timing track with the *fewest* intervals: a hand-tapped song-structure track
    (verse/chorus/drop) has far fewer marks than an auto-generated beat/bar grid. Best-effort --
    falls back to `windowed_sections` in the caller if there's no timing track at all."""
    candidates = [(name, ivals) for name, ivals in timing_tracks.items() if len(ivals) >= 2]
    if not candidates:
        return []
    _, ivals = min(candidates, key=lambda kv: len(kv[1]))
    sections = []
    for start_s, end_s, label in sorted(ivals):
        vals = [e for t, e in curve if start_s <= t < end_s]
        energy = clamp01(sum(vals) / len(vals)) if vals else 0.0
        sections.append(Section(name=label, start_s=start_s, end_s=end_s, energy=round(energy, 3)))
    return sections


class XLightsDecoder:
    name = "xlights"
    extensions = (".xsq",)

    def sniff(self, path: Path) -> bool:
        if path.suffix.lower() not in self.extensions:
            return False
        tag = quick_root_tag(path)
        return tag is None or tag.lower() == "xsequence"

    def decode(self, path: Path) -> ReferenceShow:
        root = load_root(path)
        if local(root).lower() != "xsequence":
            raise ValueError(f"{path.name} is not an xLights <xsequence> document (root is <{local(root)}>)")

        head = kid(root, "head")
        song_title = text_of(head, "song")
        artist = text_of(head, "artist")
        song = Song(title=song_title or None, artist=artist or None) if (song_title or artist) else None
        duration_s = _to_float(text_of(head, "sequenceDuration"))

        palettes = _read_palettes(root)

        elem_kind: dict[str, str] = {}
        for el in kids(kid(root, "DisplayElements"), "Element"):
            elem_kind[el.get("name", "")] = (el.get("type") or "").lower()

        cues: list[Cue] = []
        timing_tracks: dict[str, list[tuple[float, float, Optional[str]]]] = {}
        effect_types: Counter[str] = Counter()

        for el in kids(kid(root, "ElementEffects"), "Element"):
            ename = el.get("name", "")
            kind = elem_kind.get(ename, (el.get("type") or "").lower())
            for layer in kids(el, "EffectLayer"):
                for eff in kids(layer, "Effect"):
                    start_s, end_s = _ms(eff.get("startTime")), _ms(eff.get("endTime"))
                    if kind == "timing":
                        label = (eff.get("label") or "").strip() or None
                        timing_tracks.setdefault(ename, []).append((start_s, end_s, label))
                        continue
                    etype = eff.get("name") or "Unknown"
                    effect_types[etype] += 1
                    colors: list[str] = []
                    pidx = eff.get("palette")
                    if pidx is not None and pidx.isdigit() and int(pidx) < len(palettes):
                        colors = palettes[int(pidx)]
                    cues.append(Cue(start_s=start_s, end_s=end_s, effect_type=etype, colors=colors,
                                    targets=[ename] if ename else []))

        if duration_s <= 0:
            duration_s = max((c.end_s for c in cues), default=0.0)

        active_tracks = dedupe(t for c in cues for t in c.targets) or ["show"]
        curve = energy_curve([(c.start_s, c.end_s) for c in cues], duration_s, len(active_tracks))

        sections = _timing_sections(timing_tracks, curve) or windowed_sections(curve, duration_s)
        palette = dedupe(c for cue in cues for c in cue.colors)
        fixture_kinds = sorted({elem_kind[t] for t in active_tracks if elem_kind.get(t)} & _FIXTURE_TYPES) or ["model"]

        return ReferenceShow(
            source_file=path.name,
            format="xlights",
            title=song_title or path.stem,
            song=song,
            bpm=None,
            duration_s=round(duration_s, 3),
            sections=sections,
            cues=cues,
            palette=palette,
            effect_types=dict(effect_types),
            energy_curve=curve,
            fixture_kinds=fixture_kinds,
            spatial_moves=spatial_moves_from_cues(cues),
        )


register(XLightsDecoder())
