"""QLC+ fixture definitions (.qxf): parser and a library that mirrors QLC+'s lookup order."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lxml import etree


def local(el) -> str:
    return etree.QName(el).localname


def kids(el, name: Optional[str] = None) -> list:
    return [c for c in el if isinstance(c.tag, str) and (name is None or local(c) == name)]


def kid(el, name: str):
    for c in el:
        if isinstance(c.tag, str) and local(c) == name:
            return c
    return None


def text_of(el, name: str, default: str = "") -> str:
    c = kid(el, name)
    return (c.text or "").strip() if c is not None and c.text else default


@dataclass
class Capability:
    min: int
    max: int
    name: str = ""
    preset: Optional[str] = None
    res1: Optional[str] = None
    res2: Optional[str] = None


@dataclass
class ChannelDef:
    name: str
    preset: Optional[str] = None
    group: Optional[str] = None
    colour: Optional[str] = None
    byte: int = 0
    default: int = 0
    capabilities: list = field(default_factory=list)


@dataclass
class ModeDef:
    name: str
    channels: list = field(default_factory=list)
    heads: list = field(default_factory=list)
    pan_max: int = 0
    tilt_max: int = 0


@dataclass
class FixtureDef:
    manufacturer: str
    model: str
    type: str
    channels: dict = field(default_factory=dict)
    modes: dict = field(default_factory=dict)
    path: Optional[str] = None
    pan_max: int = 0
    tilt_max: int = 0
    synthetic: bool = False

    def mode(self, name: str) -> Optional[ModeDef]:
        if name in self.modes:
            return self.modes[name]
        lowered = {k.lower(): v for k, v in self.modes.items()}
        return lowered.get(name.lower())

    def mode_channels(self, mode_name: str) -> list:
        m = self.mode(mode_name)
        if m is None:
            return []
        return [self.channels.get(n) or ChannelDef(name=n) for n in m.channels]


def _int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _focus(parent) -> tuple:
    phys = kid(parent, "Physical") if parent is not None else None
    focus = kid(phys, "Focus") if phys is not None else None
    if focus is None:
        return 0, 0
    return _int(focus.get("PanMax")), _int(focus.get("TiltMax"))


def parse_qxf(path: Path) -> FixtureDef:
    root = etree.parse(str(path)).getroot()
    fd = FixtureDef(
        manufacturer=text_of(root, "Manufacturer"),
        model=text_of(root, "Model"),
        type=text_of(root, "Type", "Other"),
        path=str(path),
    )
    fd.pan_max, fd.tilt_max = _focus(root)
    for ch in kids(root, "Channel"):
        cd = ChannelDef(name=ch.get("Name", ""), preset=ch.get("Preset"), default=_int(ch.get("Default")))
        grp = kid(ch, "Group")
        if grp is not None:
            cd.group = (grp.text or "").strip()
            cd.byte = _int(grp.get("Byte"))
        cd.colour = text_of(ch, "Colour") or None
        for cap in kids(ch, "Capability"):
            cd.capabilities.append(
                Capability(
                    min=_int(cap.get("Min")),
                    max=_int(cap.get("Max")),
                    name=(cap.text or "").strip(),
                    preset=cap.get("Preset"),
                    res1=cap.get("Res1") or cap.get("Res") or cap.get("Color"),
                    res2=cap.get("Res2") or cap.get("Color2"),
                )
            )
        fd.channels[cd.name] = cd
    for m in kids(root, "Mode"):
        md = ModeDef(name=m.get("Name", ""))
        numbered = []
        for c in kids(m, "Channel"):
            numbered.append((_int(c.get("Number")), (c.text or "").strip()))
        md.channels = [n for _, n in sorted(numbered, key=lambda x: x[0])]
        for h in kids(m, "Head"):
            md.heads.append([_int(c.text) for c in kids(h, "Channel")])
        md.pan_max, md.tilt_max = _focus(m)
        if not md.pan_max:
            md.pan_max, md.tilt_max = fd.pan_max, fd.tilt_max
        fd.modes[md.name] = md
    return fd


def generic_dimmer(channels: int) -> FixtureDef:
    fd = FixtureDef(manufacturer="Generic", model="Generic", type="Dimmer", synthetic=True)
    mode = ModeDef(name=f"{channels} Channel")
    for i in range(channels):
        name = f"Dimmer #{i + 1}"
        fd.channels[name] = ChannelDef(name=name, group="Intensity", capabilities=[Capability(0, 255, "Intensity")])
        mode.channels.append(name)
        mode.heads.append([i])
    fd.modes[mode.name] = mode
    return fd


def generic_rgb_panel(mode_name: str, channels: int) -> FixtureDef:
    order = mode_name.split()[0].upper()
    fd = FixtureDef(manufacturer="Generic", model="RGBPanel", type="LED Bar (Pixels)", synthetic=True)
    presets = {"R": "IntensityRed", "G": "IntensityGreen", "B": "IntensityBlue", "W": "IntensityWhite"}
    names = {"R": "Red", "G": "Green", "B": "Blue", "W": "White"}
    per_head = len(order)
    mode = ModeDef(name=mode_name)
    heads = max(1, channels // max(1, per_head))
    for h in range(heads):
        head = []
        for letter in order:
            name = f"{names.get(letter, letter)} {h + 1}"
            fd.channels[name] = ChannelDef(name=name, preset=presets.get(letter))
            head.append(len(mode.channels))
            mode.channels.append(name)
        mode.heads.append(head)
    fd.modes[mode.name] = mode
    return fd


def _header(path: Path) -> tuple:
    manufacturer = model = None
    try:
        with open(path, "rb") as fh:
            for _, el in etree.iterparse(fh, events=("end",)):
                name = local(el)
                if name == "Manufacturer" and manufacturer is None:
                    manufacturer = (el.text or "").strip()
                elif name == "Model" and model is None:
                    model = (el.text or "").strip()
                if manufacturer is not None and model is not None:
                    break
    except (etree.XMLSyntaxError, OSError):
        pass
    return manufacturer, model


_SHARED_LIBS: dict = {}


class FixtureLibrary:
    """Finds .qxf definitions by (manufacturer, model), first directory wins (like QLC+)."""

    def __init__(self, dirs: list) -> None:
        self.dirs = [Path(d) for d in dirs if Path(d).exists()]
        self._index: Optional[dict] = None
        self._cache: dict = {}

    @staticmethod
    def _signature(dirs: list) -> tuple:
        sig = []
        for d in dirs:
            d = Path(d)
            fmap = d / "FixturesMap.xml"
            try:
                sig.append((str(d), d.stat().st_mtime_ns, fmap.stat().st_mtime_ns if fmap.exists() else 0))
            except OSError:
                sig.append((str(d), 0, 0))
        return tuple(sig)

    @classmethod
    def shared(cls, dirs: list) -> "FixtureLibrary":
        """Reuse the parsed index across rig reloads (walking the folders costs ~100 ms); a new or removed file re-indexes."""
        key = tuple(str(d) for d in dirs)
        sig = cls._signature(dirs)
        hit = _SHARED_LIBS.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
        lib = cls(dirs)
        _SHARED_LIBS[key] = (sig, lib)
        return lib

    def _build_index(self) -> dict:
        index: dict = {}
        for d in self.dirs:
            stems: dict = {}
            for dirpath, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(".qxf"):
                        stems.setdefault(f[:-4].lower(), Path(dirpath) / f)
            fmap = d / "FixturesMap.xml"
            if fmap.exists():
                root = etree.parse(str(fmap)).getroot()
                for m in kids(root, "M"):
                    for f in kids(m, "F"):
                        key = (m.get("n", "").lower(), f.get("m", "").lower())
                        path = stems.get(f.get("n", "").lower())
                        if path is not None and key not in index:
                            index[key] = path
            else:
                for path in stems.values():
                    manufacturer, model = _header(path)
                    if manufacturer and model:
                        index.setdefault((manufacturer.lower(), model.lower()), path)
        return index

    def find(self, manufacturer: str, model: str) -> Optional[Path]:
        if self._index is None:
            self._index = self._build_index()
        return self._index.get((manufacturer.lower(), model.lower()))

    def get(self, manufacturer: str, model: str, mode: str = "", channels: int = 0) -> Optional[FixtureDef]:
        if manufacturer == "Generic" and model == "Generic":
            return generic_dimmer(max(1, channels))
        if manufacturer == "Generic" and model == "RGBPanel":
            return generic_rgb_panel(mode or "RGB", channels)
        key = (manufacturer.lower(), model.lower())
        path = self.find(manufacturer, model)
        try:
            stamp = path.stat().st_mtime_ns if path else 0
        except OSError:
            stamp = 0
        hit = self._cache.get(key)
        if hit is not None and hit[0] == stamp:  # a definition edited in place is parsed again
            return hit[1]
        fd = parse_qxf(path) if path else None
        self._cache[key] = (stamp, fd)
        return fd

    def all_models(self) -> list:
        if self._index is None:
            self._index = self._build_index()
        return sorted(self._index.keys())
