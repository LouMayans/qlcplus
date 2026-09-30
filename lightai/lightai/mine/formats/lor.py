"""Light-O-Rama ``.lms`` / ``.loredit`` sequence decoder.

Both extensions share the same plain-XML core this decoder reads (S5's ``.loredit`` is a newer,
larger schema, but the pieces below are unchanged): a ``<sequence>`` with a ``<channels>`` list
of per-prop ``<channel>``/``<rgbChannel>`` elements, each holding ``<effect type=".."
startCentisecond=".." endCentisecond=".." .../>`` spans in centiseconds, plus a
``<timingGrids>`` list of named ``<timingGrid><timing centisecond=".."/></timingGrid>`` tap
grids -- a ``type="fixed"`` grid is an evenly-tapped metronome grid (a bpm hint), anything else
is hand-tapped song structure (-> sections).

Structure confirmed against two independent, working open-source readers rather than guessed:
Cryptkeeper's ``libreorama`` (MIT, an actual LOR-protocol sequence player) and xLights' own
``LORMusic.cpp`` LOR importer (GPL-3.0) -- including the non-obvious detail that a channel's
``color="12345"`` is a decimal COLORREF-style int (red = i & 0xff, green = (i>>8) & 0xff,
blue = (i>>16) & 0xff), not hex RGB text.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

from .base import (
    Cue,
    ReferenceShow,
    Section,
    clamp01,
    dedupe,
    energy_curve,
    kid,
    kids,
    load_root,
    local,
    quick_root_tag,
    register,
    spatial_moves_from_cues,
    windowed_sections,
)

# LOR's plain "intensity" channels are 0-100; DMX-addressed channels use "DMX intensity" and are
# 0-255 (confirmed by xLights' LORMusic.cpp, which rescales exactly this way on import).
_INTENSITY_SCALE = {"intensity": 100.0, "dmx intensity": 255.0}


def _cs(v: Optional[str]) -> float:
    try:
        return round(int(v) / 100.0, 3)
    except (TypeError, ValueError):
        return 0.0


def _lor_color(raw: Optional[str]) -> Optional[str]:
    try:
        i = int(raw)
    except (TypeError, ValueError):
        return None
    r, g, b = i & 0xFF, (i >> 8) & 0xFF, (i >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def _effect_type(attrs: dict[str, str]) -> tuple[str, Optional[float]]:
    """-> (effect_type label, intensity 0-1 or None). `attrs` keys are already lower-cased."""
    kind = (attrs.get("type") or "").lower()
    scale = _INTENSITY_SCALE.get(kind)
    if scale is None:
        return (kind or "unknown"), None
    if "intensity" in attrs:
        try:
            level = clamp01(float(attrs["intensity"]) / scale)
        except ValueError:
            return kind, None
        if level >= 0.995:
            return "on", level
        if level <= 0.005:
            return "off", level
        return "intensity", level
    if "startintensity" in attrs and "endintensity" in attrs:
        try:
            start, end = float(attrs["startintensity"]), float(attrs["endintensity"])
        except ValueError:
            return kind, None
        level = clamp01(end / scale)
        return ("fade_up" if end > start else "fade_down" if end < start else "intensity"), level
    return kind, None


class LorDecoder:
    name = "lor"
    extensions = (".lms", ".loredit")

    def sniff(self, path: Path) -> bool:
        if path.suffix.lower() not in self.extensions:
            return False
        tag = quick_root_tag(path)
        return tag is None or tag.lower() == "sequence"

    def decode(self, path: Path) -> ReferenceShow:
        root = load_root(path)
        if local(root).lower() != "sequence":
            raise ValueError(f"{path.name} is not a LOR <sequence> document (root is <{local(root)}>)")
        channels_el = kid(root, "channels")
        if channels_el is None:
            raise ValueError(f"{path.name} has no <channels> section; not a recognizable LOR sequence")

        cues: list[Cue] = []
        effect_types: Counter[str] = Counter()
        channel_names: set[str] = set()

        def walk(container) -> None:
            for chan in kids(container):
                tag = local(chan).lower()
                if tag not in ("channel", "rgbchannel"):
                    continue
                nested = kid(chan, "channels")
                if nested is not None:
                    # an rgbChannel wraps sub-channel references instead of holding effects itself
                    walk(nested)
                    continue
                name = chan.get("name") or f"unit{chan.get('unit', '?')}.circuit{chan.get('circuit', '?')}"
                channel_names.add(name)
                color = _lor_color(chan.get("color"))
                for eff in kids(chan, "effect"):
                    attrs = {k.lower(): v for k, v in eff.attrib.items()}
                    etype, level = _effect_type(attrs)
                    effect_types[etype] += 1
                    start_s, end_s = _cs(eff.get("startCentisecond")), _cs(eff.get("endCentisecond"))
                    cues.append(Cue(start_s=start_s, end_s=end_s, effect_type=etype,
                                    colors=[color] if color else [], targets=[name], intensity=level))

        walk(channels_el)

        duration_s = max((_cs(t.get("totalCentiseconds")) for t in kids(kid(root, "tracks"), "track")), default=0.0)
        if duration_s <= 0:
            duration_s = max((c.end_s for c in cues), default=0.0)

        curve = energy_curve([(c.start_s, c.end_s) for c in cues], duration_s, len(channel_names) or 1)

        bpm: Optional[float] = None
        sections: list[Section] = []
        for grid in kids(kid(root, "timingGrids"), "timingGrid"):
            marks = sorted({_cs(m.get("centisecond")) for m in kids(grid, "timing")})
            if len(marks) < 2:
                continue
            gaps = sorted(b - a for a, b in zip(marks, marks[1:]) if b > a)
            if not gaps:
                continue
            gtype = (grid.get("type") or "").lower()
            median_gap = gaps[len(gaps) // 2]
            if gtype == "fixed":
                if bpm is None and median_gap > 0 and 40.0 <= 60.0 / median_gap <= 220.0:
                    bpm = round(60.0 / median_gap, 1)
            elif not sections:
                name = grid.get("name") or None
                bounds = marks if marks[-1] >= duration_s - 0.01 else marks + [duration_s]
                for start_s, end_s in zip(bounds, bounds[1:]):
                    vals = [e for t, e in curve if start_s <= t < end_s]
                    energy = clamp01(sum(vals) / len(vals)) if vals else 0.0
                    sections.append(Section(name=name, start_s=start_s, end_s=end_s, energy=round(energy, 3)))

        title = Path(root.get("musicFilename") or "").stem or path.stem

        return ReferenceShow(
            source_file=path.name,
            format="lor",
            title=title,
            song=None,
            bpm=bpm,
            duration_s=round(duration_s, 3),
            sections=sections or windowed_sections(curve, duration_s),
            cues=cues,
            palette=dedupe(c for cue in cues for c in cue.colors),
            effect_types=dict(effect_types),
            energy_curve=curve,
            fixture_kinds=["channel"] if channel_names else [],
            spatial_moves=spatial_moves_from_cues(cues),
        )


register(LorDecoder())
