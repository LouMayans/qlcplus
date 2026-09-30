"""Shared model, decoder protocol, and registry for reference-show decoders.

A ``ReferenceShow`` is our own small, format-agnostic distillation of someone else's exported
light show: enough to mine for ideas (palette, effect mix, energy over time, roughly how busy it
gets, whether movement reads as a chase) without ever storing or replaying their actual cue data
verbatim. See ``lightai/knowledge/research/format-survey.md`` for how these formats were picked.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from lxml import etree
from pydantic import BaseModel, Field

from lightai.rig.qxf import kid as _kid
from lightai.rig.qxf import kids as _kids
from lightai.rig.qxf import local
from lightai.rig.qxf import text_of as _text_of

__all__ = [
    "Song", "Section", "Cue", "ReferenceShow", "Decoder", "NotDecodable",
    "register", "registered", "decode_file",
    "kid", "kids", "text_of", "local", "load_root", "quick_root_tag",
    "clamp01", "dedupe", "norm_hex", "energy_curve", "windowed_sections", "spatial_moves_from_cues",
]


# --- XML helpers -----------------------------------------------------------------------------
# None-safe wrappers around lightai.rig.qxf's generic (QLC+-agnostic) lxml helpers: a missing
# optional section of a show file (`el is None`) should look like "no children" everywhere in
# these decoders, not raise.

def kid(el, name: str):
    return _kid(el, name) if el is not None else None


def kids(el, name: Optional[str] = None) -> list:
    return _kids(el, name) if el is not None else []


def text_of(el, name: str, default: str = "") -> str:
    return _text_of(el, name, default) if el is not None else default


MAX_BYTES = 256 * 1024 * 1024  # untrusted files: refuse anything bigger (or unpacking bigger) than this


def load_root(path: Path, inner_exts: tuple[str, ...] = ()) -> "etree._Element":
    """Parse `path` as XML, transparently unzipping it first if it turns out to be a zip archive
    (some show-editor formats bundle their XML alongside audio/media that way). Never resolves
    external entities or touches the network -- these files are untrusted input, and a decoder
    must never execute anything from them. Raises ValueError with a clear message on anything
    that isn't well-formed XML, instead of letting a raw lxml/zip exception escape.
    """
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError(f"{path.name} is over the {MAX_BYTES // 2**20} MB limit")
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"can't read {path.name}: {exc}") from exc
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(path) as zf:
                names = [n for n in zf.namelist() if inner_exts and n.lower().endswith(inner_exts)]
                if not names:
                    names = [n for n in zf.namelist() if not n.endswith("/")]
                if not names:
                    raise ValueError(f"{path.name} is a zip archive with no readable entry")
                if zf.getinfo(names[0]).file_size > MAX_BYTES:  # a zip bomb never gets read into memory
                    raise ValueError(f"{path.name}: {names[0]} unpacks to over {MAX_BYTES // 2**20} MB")
                data = zf.read(names[0])
        except zipfile.BadZipFile as exc:
            raise ValueError(f"{path.name} looks like a zip but won't open: {exc}") from exc
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        return etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"malformed XML in {path.name}: {exc}") from exc


def quick_root_tag(path: Path) -> Optional[str]:
    """Best-effort, cheap peek at the root element's local name, for sniff() to use. Never
    raises -- returns None on a zip, an unreadable file, or anything else that goes wrong, which
    callers should treat as "inconclusive", not "no"."""
    try:
        with open(path, "rb") as fh:
            if fh.read(4) == b"PK\x03\x04":
                return None
            fh.seek(0)
            for _, el in etree.iterparse(fh, events=("start",), resolve_entities=False, no_network=True):
                return local(el)
    except (etree.XMLSyntaxError, OSError):
        return None
    return None


# --- small pure-data helpers shared by the decoders -------------------------------------------

_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def dedupe(items) -> list:
    """First-seen-order de-duplication (`set()` would scramble the order)."""
    return list(dict.fromkeys(items))


def norm_hex(s: Optional[str]) -> Optional[str]:
    """"ff9900" / "#FF9900" -> "#FF9900"; anything else -> None (never raises)."""
    m = _HEX_RE.match((s or "").strip())
    return f"#{m.group(1).upper()}" if m else None


def energy_curve(intervals: list[tuple[float, float]], duration_s: float, track_count: int,
                  step_s: float = 2.0) -> list[tuple[float, float]]:
    """Sample "how much is happening" every `step_s` seconds: the fraction of the busiest
    possible moment (`track_count` things firing at once) that's actually firing at each instant.
    A cheap density proxy for "energy" -- none of these hobbyist formats expose a real computed
    loudness/brightness curve, so density of simultaneous effects is what's actually available.
    Always returns at least one sample, and timestamps are always strictly increasing.
    """
    if duration_s <= 0:
        return [(0.0, 0.0)]
    n = max(1, track_count)
    out = []
    t = 0.0
    while t <= duration_s + 1e-9:
        active = sum(1 for s, e in intervals if s <= t < e)
        out.append((round(t, 3), clamp01(active / n)))
        t += step_s
    return out


def windowed_sections(curve: list[tuple[float, float]], duration_s: float, window_s: float = 30.0) -> list[Section]:
    """Fallback ``sections`` for a show with no native phrase/label markers: bucket the energy
    curve into fixed windows and average each one. Used when a format-specific attempt (e.g. a
    timing/mark track) found nothing usable. `Section` is defined later in this module; by the
    time this function actually runs the module has finished loading, so the bare name below
    resolves fine."""
    if duration_s <= 0:
        return []
    step = min(window_s, duration_s)
    out, t = [], 0.0
    while t < duration_s - 1e-9:
        end = min(t + step, duration_s)
        vals = [e for ts, e in curve if t <= ts < end]
        energy = clamp01(sum(vals) / len(vals)) if vals else 0.0
        out.append(Section(start_s=round(t, 3), end_s=round(end, 3), energy=round(energy, 3)))
        t = end
    return out


_NUM_RE = re.compile(r"(\d+)")


def spatial_moves_from_cues(cues: list[Cue]) -> list[str]:
    """One conservative, testable heuristic: if 3+ distinct targets each start firing in a
    strictly increasing order of first-start time, and that firing order matches the targets'
    own ascending (or descending) numeric/alphabetic order, call it a chase. None of these
    sequence formats carry real fixture positions (that lives in a separate layout/preview file
    most of them have, which is out of scope here), so anything subtler than "the numbers and
    the timing agree" would just be guessing.
    """
    first_start: dict[str, float] = {}
    for c in cues:
        for t in c.targets:
            first_start[t] = min(first_start.get(t, c.start_s), c.start_s)
    if len(first_start) < 3:
        return []
    fired_order = [t for t, _ in sorted(first_start.items(), key=lambda kv: kv[1])]

    def sort_key(name: str):
        m = _NUM_RE.search(name)
        return (0, int(m.group(1))) if m else (1, name)

    ascending = sorted(first_start, key=sort_key)
    if fired_order == ascending:
        return ["left_to_right chase"]
    if fired_order == list(reversed(ascending)):
        return ["right_to_left chase"]
    return []


# --- the model ---------------------------------------------------------------------------------

class Song(BaseModel):
    """Track metadata, only when the show file itself embeds it."""

    artist: Optional[str] = None
    title: Optional[str] = None


class Section(BaseModel):
    """A coarse span of the show: a labeled phrase/mark-track interval when the format has one,
    else a fixed-size bucket (see `windowed_sections`)."""

    name: Optional[str] = None
    start_s: float
    end_s: float
    energy: float = Field(0.0, ge=0.0, le=1.0)


class Cue(BaseModel):
    """One effect firing, on one or more named targets, for one span of time."""

    start_s: float
    end_s: float
    effect_type: str
    colors: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    intensity: Optional[float] = Field(None, ge=0.0, le=1.0)
    speed: Optional[float] = None


class ReferenceShow(BaseModel):
    """The common, format-agnostic distillation every decoder in this package produces."""

    source_file: str
    format: str
    title: str
    song: Optional[Song] = None
    bpm: Optional[float] = Field(None, gt=0.0)
    duration_s: float = Field(ge=0.0)
    sections: list[Section] = Field(default_factory=list)
    cues: list[Cue] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    effect_types: dict[str, int] = Field(default_factory=dict)
    energy_curve: list[tuple[float, float]] = Field(default_factory=list)
    fixture_kinds: list[str] = Field(default_factory=list)
    spatial_moves: list[str] = Field(default_factory=list)


# --- decoder protocol, registry, and closed-format hints ---------------------------------------

@runtime_checkable
class Decoder(Protocol):
    name: str
    extensions: tuple[str, ...]

    def sniff(self, path: Path) -> bool:
        """Cheap, best-effort check. Must never raise -- return False on any doubt."""
        ...

    def decode(self, path: Path) -> ReferenceShow:
        """Full parse. May raise ValueError for malformed input that `sniff()` accepted."""
        ...


class NotDecodable(Exception):
    """No registered decoder recognizes this file -- either it's one of the closed/undocumented
    formats from the survey (see `_CLOSED_FORMATS`), or it's simply not one of the show formats
    this package knows about."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# A few closed/undocumented formats named in the survey, purely to make NotDecodable's message
# more useful than "unknown extension" when one of them shows up. Not an exhaustive list.
_CLOSED_FORMATS: dict[str, str] = {
    ".ssproj": "SoundSwitch (.ssproj) content lives behind SoundSwitch's cloud service, not as a decodable file",
    ".shw": "ChamSys MagicQ (.shw) has no public spec or parser",
    ".xhw": "ChamSys MagicQ (.xhw) has no public spec or parser",
    ".onyxshow": "Obsidian ONYX (.ONYXShow) has no public spec",
    ".hog": "Hog 4 / Road Hog (.hog) has no public spec",
    ".h3": "Hog 4 / Road Hog (.h3) has no public spec",
    ".c2p": "Capture (.c2p) is an undocumented binary format",
    ".c2s": "Capture (.c2s) is an undocumented binary format",
    ".vwx": "Vectorworks (.vwx) is a closed container -- use its MVR export instead",
    ".wyg": "WYSIWYG (.wyg) is a dongle-locked proprietary format",
    ".esf": "ETC Eos native (.esf) is closed -- use its USITT ASCII export instead",
    ".chb": "Freestyler (.chb) is a documented but strictly binary format; no decoder in this package (see formats/__init__.py)",
}

_REGISTRY: list[Decoder] = []


def register(decoder: Decoder) -> None:
    _REGISTRY.append(decoder)


def registered() -> list[Decoder]:
    return list(_REGISTRY)


def decode_file(path: Path) -> ReferenceShow:
    """Pick a registered decoder by sniffing `path` and decode it, or raise NotDecodable."""
    path = Path(path)
    if not path.exists():
        raise NotDecodable(f"no such file: {path}")
    for decoder in _REGISTRY:
        try:
            matched = decoder.sniff(path)
        except Exception:
            matched = False
        if matched:
            return decoder.decode(path)
    hint = _CLOSED_FORMATS.get(path.suffix.lower())
    raise NotDecodable(hint or f"no decoder recognizes {path.name!r} (extension {path.suffix!r})")
