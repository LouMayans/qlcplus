"""Vixen 3 ``.tim`` sequence decoder.

Unlike xLights/LOR, Vixen 3 doesn't hand-write its sequence XML: it round-trips a `.NET
DataContractSerializer` object graph, so element names come straight from Vixen's own C# classes
and nest arbitrarily (``EffectNodeSurrogate`` -> ``StartTime``/``TimeSpan``/``TypeId`` children,
``TargetNodes`` -> ``ChannelNodeReferenceSurrogate`` -> ``Name``; confirmed against
VixenLights/Vixen's ``NodeSurrogate``/``EffectNodeSurrogate`` source, BSD-like license). There is
no separate written spec (per the format survey), so this decoder deliberately does a
wrapper-agnostic scan for those element names anywhere in the tree rather than assuming one
exact nesting -- Vixen versions differ, and getting the outer collection element's name wrong
would otherwise silently produce nothing.

``TypeId`` is a GUID identifying the effect *module* (e.g. Pulse, Chase, Twinkle), not a
human-readable name -- `_EFFECT_NAMES` below maps the common built-in modules' GUIDs (read
straight out of their ``*Descriptor.cs`` sources) to names; an unrecognized GUID degrades to
``"effect-<first 8 hex chars>"`` instead of failing.

Known, honest limitations (see the module docstring in ``formats/__init__.py`` and the task
report for the full list): an effect's actual color/level parameters live in a separate,
per-instance module-data blob correlated by ``InstanceId``, whose shape is per-effect-module and
undocumented, so ``colors``/``intensity``/``speed`` are essentially never populated here; a
sequence's own fixture "kind" labels live in Vixen's separate system-config file, not the
``.tim``, so ``fixture_kinds`` is always empty.
"""

from __future__ import annotations

import re
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
    norm_hex,
    register,
    spatial_moves_from_cues,
    text_of,
    windowed_sections,
)

# Built-in Vixen 3 effect module GUIDs -> display name (from VixenModules.Effect.*.*Descriptor.cs
# in the VixenLights/Vixen source). Not exhaustive -- there are 50+ built-in effect modules and
# an unknown number of third-party ones; unrecognized GUIDs fall back gracefully in _effect_name.
_EFFECT_NAMES = {
    "cbd76d3b-c924-40ff-bad6-d1437b3dbdc0": "Pulse",
    "83bdd6f7-19c7-4598-b8e3-7ce28c44e7db": "Twinkle",
    "affea852-85b1-418f-9cdf-0b9735154bb5": "Chase",
    "32cff8e0-5b10-4466-a093-0d232c55aac0": "Set Level",
    "24decea6-78ab-4f42-acee-79afe4ffc015": "Bars",
    "9dc93585-fff6-4216-bc72-fbf2617954c5": "Colorwash",
    "880280ea-c38b-442c-8f09-395db4eb698c": "Shockwave",
    "2bff1008-56e2-4bff-8ce3-a23189ea4684": "Strobe",
    "504582d2-43ac-472e-8885-ec4bcbf2b1f7": "Candle Flicker",
    "821a8540-ea34-401f-a8aa-416d7d9a196a": "Spin",
}

_ISO_TS = re.compile(
    r"^(?P<sign>-)?P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)
_CLOCK_TS = re.compile(r"^(?P<sign>-)?(?:(?P<d>\d+)\.)?(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{1,2}(?:\.\d+)?)$")


def _timespan_s(text: Optional[str]) -> Optional[float]:
    """Parse a .NET TimeSpan as either XmlConvert/DataContractSerializer writes it (xs:duration,
    e.g. "PT1M30S") or as TimeSpan.ToString() writes it ("00:01:30" / "1.02:03:04.5000000") --
    which one shows up isn't nailed down by any spec, so both are accepted. Returns None (not
    0.0) when unparseable, so callers can tell "absent/garbled" apart from "genuinely zero"."""
    if not text:
        return None
    text = text.strip()
    m = _ISO_TS.match(text)
    if m and any(m.group(g) for g in ("d", "h", "m", "s")):
        days, hours, mins, secs = (float(m.group(g) or 0) for g in ("d", "h", "m", "s"))
        total = days * 86400 + hours * 3600 + mins * 60 + secs
        return -total if m.group("sign") else total
    m = _CLOCK_TS.match(text)
    if m:
        days = float(m.group("d") or 0)
        total = days * 86400 + int(m.group("h")) * 3600 + int(m.group("m")) * 60 + float(m.group("s"))
        return -total if m.group("sign") else total
    return None


def _effect_name(type_id: Optional[str]) -> str:
    key = (type_id or "").strip().strip("{}").lower()
    if not key:
        return "Unknown"
    return _EFFECT_NAMES.get(key, f"effect-{key[:8]}")


def _iter_named(root, name: str):
    for node in root.iter():
        if isinstance(node.tag, str) and local(node) == name:
            yield node


def _scavenge_colors(root) -> list[str]:
    """Best-effort only, see module docstring: picks up the simple case of a bare
    <Color>/<StaticColor> element with plain hex text, and skips everything else."""
    return [c for c in (norm_hex(node.text) for node in root.iter()
                        if isinstance(node.tag, str) and local(node) in ("Color", "StaticColor")) if c]


class Vixen3Decoder:
    name = "vixen3"
    extensions = (".tim",)

    def sniff(self, path: Path) -> bool:
        # ".tim" isn't shared with the other decoders here, and the DataContractSerializer root
        # element name isn't reliably known across Vixen versions, so extension is the signal.
        return path.suffix.lower() in self.extensions

    def decode(self, path: Path) -> ReferenceShow:
        root = load_root(path)

        cues: list[Cue] = []
        effect_types: Counter[str] = Counter()
        targets_seen: set[str] = set()

        for node in _iter_named(root, "EffectNodeSurrogate"):
            start_s = _timespan_s(text_of(node, "StartTime")) or 0.0
            span_s = _timespan_s(text_of(node, "TimeSpan")) or 0.0
            etype = _effect_name(text_of(node, "TypeId"))
            effect_types[etype] += 1
            targets: list[str] = []
            for ref in _iter_named(node, "ChannelNodeReferenceSurrogate"):
                nm = text_of(ref, "Name")
                if nm:
                    targets.append(nm)
                    targets_seen.add(nm)
            cues.append(Cue(start_s=round(start_s, 3), end_s=round(start_s + span_s, 3),
                            effect_type=etype, targets=targets))

        length_s = next((_timespan_s(node.text) for node in _iter_named(root, "Length")), None)
        duration_s = length_s if length_s else max((c.end_s for c in cues), default=0.0)

        curve = energy_curve([(c.start_s, c.end_s) for c in cues], duration_s, len(targets_seen) or 1)

        bpm: Optional[float] = None
        sections: list[Section] = []
        for mc in _iter_named(root, "MarkCollection"):
            mname = text_of(mc, "Name") or None
            marks = sorted({v for v in (_timespan_s(c.text) for c in kids(kid(mc, "Marks"))) if v is not None})
            if len(marks) < 2:
                continue
            gaps = sorted(b - a for a, b in zip(marks, marks[1:]) if b > a)
            if not gaps:
                continue
            median_gap = gaps[len(gaps) // 2]
            if len(marks) >= 8:
                if bpm is None and median_gap > 0 and 40.0 <= 60.0 / median_gap <= 220.0:
                    bpm = round(60.0 / median_gap, 1)
            elif not sections:
                bounds = marks if marks[-1] >= duration_s - 0.01 else marks + [duration_s]
                for start_s, end_s in zip(bounds, bounds[1:]):
                    vals = [e for t, e in curve if start_s <= t < end_s]
                    energy = clamp01(sum(vals) / len(vals)) if vals else 0.0
                    sections.append(Section(name=mname, start_s=round(start_s, 3), end_s=round(end_s, 3),
                                            energy=round(energy, 3)))

        return ReferenceShow(
            source_file=path.name,
            format="vixen3",
            title=path.stem,
            song=None,
            bpm=bpm,
            duration_s=round(duration_s, 3),
            sections=sections or windowed_sections(curve, duration_s),
            cues=cues,
            palette=dedupe(_scavenge_colors(root)),
            effect_types=dict(effect_types),
            energy_curve=curve,
            fixture_kinds=[],
            spatial_moves=spatial_moves_from_cues(cues),
        )


register(Vixen3Decoder())
