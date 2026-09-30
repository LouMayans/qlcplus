"""Decode GDTF fixture files (DIN SPEC 15800): a zip holding description.xml plus 3D models and
thumbnails describing one fixture type's DMX modes, channels and physical optics.

description.xml's shape here was confirmed against the DIN SPEC 15800 markdown (mvrdevelopment/
spec) and against real fixtures downloaded from gdtf-share.com by tools/stagelib/fetch_gdtf.py
(production code in this repo; its geometry-tree/pan-tilt-range logic is reused, not duplicated
guesswork):

    FixtureType[Manufacturer,Name,ShortName]
        > DMXModes > DMXMode[Name] > DMXChannels > DMXChannel[Offset]
              > LogicalChannel[Attribute] > ChannelFunction[PhysicalFrom,PhysicalTo]
        > Geometries > Geometry/Axis/Beam[BeamAngle,FieldAngle] (nested)
        > Models > Model[File]

``Offset`` is a comma-separated list of DMX byte offsets (highest to least significant byte), or
the literal string "None" for a channel with no DMX footprint.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from . import Formats3DError, SAFE_XML_PARSER, check_zip_safety

_GEOMETRY_TAGS = ("Geometry", "Axis", "Beam", "FilterBeam", "FilterColor", "FilterGobo",
                  "Display", "Laser", "MediaServerLayer", "GeometryReference")


@dataclass
class GdtfChannel:
    attribute: str = ""
    offset: list[int] | None = None


@dataclass
class GdtfMode:
    name: str
    channels: list[GdtfChannel] = field(default_factory=list)


@dataclass
class GdtfPhysical:
    beam_angle: float | None = None
    field_angle: float | None = None
    pan_range: tuple[float, float] | None = None
    tilt_range: tuple[float, float] | None = None


@dataclass
class GdtfFixture:
    manufacturer: str
    name: str
    short_name: str
    modes: list[GdtfMode] = field(default_factory=list)
    physical: GdtfPhysical = field(default_factory=GdtfPhysical)
    geometries: list[dict] = field(default_factory=list)  # [{"name", "type", "parent"}]
    models: list[str] = field(default_factory=list)       # Model/@File (falls back to @Name)


def _local(el) -> str:
    return etree.QName(el).localname


def _ffloat(el, attr: str) -> float | None:
    v = el.get(attr)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_offset(raw: str | None) -> list[int] | None:
    if not raw or raw == "None":
        return None
    try:
        return [int(x) for x in raw.split(",")]
    except ValueError:
        return None


def _parse_modes(ft) -> list[GdtfMode]:
    modes: list[GdtfMode] = []
    modes_el = ft.find("DMXModes")
    if modes_el is None:
        return modes
    for mode_el in modes_el.findall("DMXMode"):
        channels: list[GdtfChannel] = []
        chans_el = mode_el.find("DMXChannels")
        if chans_el is not None:
            for chan_el in chans_el.findall("DMXChannel"):
                logical = chan_el.find("LogicalChannel")
                attribute = logical.get("Attribute", "") if logical is not None else ""
                channels.append(GdtfChannel(attribute=attribute, offset=_parse_offset(chan_el.get("Offset"))))
        modes.append(GdtfMode(name=mode_el.get("Name", ""), channels=channels))
    return modes


def _physical_range(ft) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Pan/tilt physical range across every mode's Pan*/Tilt* LogicalChannels (same approach as
    tools/stagelib/fetch_gdtf.py, which this mirrors against real fixtures)."""
    pan_lo = pan_hi = tilt_lo = tilt_hi = None
    modes_el = ft.find("DMXModes")
    if modes_el is None:
        return None, None
    for mode_el in modes_el.findall("DMXMode"):
        chans_el = mode_el.find("DMXChannels")
        if chans_el is None:
            continue
        for chan_el in chans_el.findall("DMXChannel"):
            for logical in chan_el.findall("LogicalChannel"):
                attr = (logical.get("Attribute") or "").lower()
                if not (attr.startswith("pan") or attr.startswith("tilt")):
                    continue
                los = [f for f in (_ffloat(cf, "PhysicalFrom") for cf in logical.findall("ChannelFunction")) if f is not None]
                his = [f for f in (_ffloat(cf, "PhysicalTo") for cf in logical.findall("ChannelFunction")) if f is not None]
                if not los and not his:
                    continue
                lo_v = min(los) if los else 0.0
                hi_v = max(his) if his else 0.0
                if attr.startswith("pan"):
                    pan_lo = lo_v if pan_lo is None else min(pan_lo, lo_v)
                    pan_hi = hi_v if pan_hi is None else max(pan_hi, hi_v)
                else:
                    tilt_lo = lo_v if tilt_lo is None else min(tilt_lo, lo_v)
                    tilt_hi = hi_v if tilt_hi is None else max(tilt_hi, hi_v)
    pan = (pan_lo, pan_hi) if pan_lo is not None else None
    tilt = (tilt_lo, tilt_hi) if tilt_lo is not None else None
    return pan, tilt


def _parse_beam(ft) -> tuple[float | None, float | None]:
    geoms_el = ft.find("Geometries")
    if geoms_el is None:
        return None, None
    for el in geoms_el.iter():
        if _local(el) == "Beam":
            return _ffloat(el, "BeamAngle"), _ffloat(el, "FieldAngle")
    return None, None


def _parse_geometries(ft) -> list[dict]:
    out: list[dict] = []
    geoms_el = ft.find("Geometries")
    if geoms_el is None:
        return out

    def walk(el, parent: str | None) -> None:
        name = el.get("Name", "")
        out.append({"name": name, "type": _local(el), "parent": parent})
        for child in el:
            if _local(child) in _GEOMETRY_TAGS:
                walk(child, name)

    for root_geo in geoms_el:
        if _local(root_geo) in ("Geometry", "Axis"):
            walk(root_geo, None)
    return out


def _parse_models(ft) -> list[str]:
    names: list[str] = []
    models_el = ft.find("Models")
    if models_el is None:
        return names
    for m in models_el.findall("Model"):
        file_attr = m.get("File") or m.get("Name") or ""
        if file_attr and file_attr not in names:
            names.append(file_attr)
    return names


def decode_gdtf(path: str | Path) -> GdtfFixture:
    """Parse a GDTF zip into a GdtfFixture.

    Raises Formats3DError for an unsafe (zip-slip) or oversized archive, a missing or malformed
    description.xml, a description.xml without a FixtureType element, or a file that isn't a zip.
    """
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise Formats3DError(f"not a zip file: {path}") from exc
    with zf:
        check_zip_safety(zf)
        desc_name = next((n for n in zf.namelist() if n.lower().endswith("description.xml")), None)
        if desc_name is None:
            raise Formats3DError("no description.xml in the GDTF archive")
        try:
            root = etree.fromstring(zf.read(desc_name), parser=SAFE_XML_PARSER)
        except etree.XMLSyntaxError as exc:
            raise Formats3DError(f"malformed description.xml: {exc}") from exc
        ft = root.find("FixtureType")
        if ft is None:
            raise Formats3DError("no FixtureType element in description.xml")

        pan_range, tilt_range = _physical_range(ft)
        beam_angle, field_angle = _parse_beam(ft)
        return GdtfFixture(
            manufacturer=ft.get("Manufacturer", ""),
            name=ft.get("Name", ""),
            short_name=ft.get("ShortName", ""),
            modes=_parse_modes(ft),
            physical=GdtfPhysical(beam_angle=beam_angle, field_angle=field_angle,
                                   pan_range=pan_range, tilt_range=tilt_range),
            geometries=_parse_geometries(ft),
            models=_parse_models(ft),
        )
