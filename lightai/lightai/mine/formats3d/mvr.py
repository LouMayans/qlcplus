"""Decode MVR scene files (DIN SPEC 15801): a zip of GeneralSceneDescription.xml plus embedded
GDTF fixture files and 3D models describing where every fixture, truss and prop sits in a venue.

The XML shape and the Matrix convention below were confirmed against the DIN SPEC 15801 markdown
(mvrdevelopment/spec) and the open-stage/python-mvr (MIT) reference parser's source, not guessed:

    GeneralSceneDescription > Scene > Layers > Layer > ChildList >
        {Fixture, GroupObject (nests its own ChildList), Truss, SceneObject, Support, ...}

A <Matrix> is 4 row vectors ``{u}{v}{w}{o}`` in millimetres, right-handed, Z-up: u/v/w are the
object's local X/Y/Z axes expressed in the parent coordinate space, o is the translation.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from . import Formats3DError, SAFE_XML_PARSER, check_zip_safety

_IDENTITY_ROWS = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


@dataclass
class MvrFixture:
    """One <Fixture>, flattened: rotation is the Matrix's 3 rotation rows (local X/Y/Z axes in
    the parent space); position is its translation row, in mm. universe/address are 1-based per
    the spec's own Address convention (None when the fixture has no <Addresses>/<Address>)."""

    name: str
    fixture_id: str | None
    gdtf_spec: str | None
    gdtf_mode: str | None
    position: tuple[float, float, float]
    rotation: list[list[float]]
    universe: int | None
    address: int | None
    dmx_break: int = 0
    layer: str = ""


@dataclass
class MvrSceneObject:
    """A Truss, SceneObject or Support: scenery/rigging with an optional 3D model file."""

    name: str
    kind: str
    position: tuple[float, float, float]
    rotation: list[list[float]]
    geometry_file: str | None = None
    layer: str = ""


@dataclass
class MvrScene:
    fixtures: list[MvrFixture] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    scene_objects: list[MvrSceneObject] = field(default_factory=list)
    gdtf_files: list[str] = field(default_factory=list)
    units: str = "mm"


def _local(el) -> str:
    return etree.QName(el).localname


def _parse_matrix(text: str | None) -> tuple[list[list[float]], tuple[float, float, float]]:
    """``{u1,u2,u3}{v1,v2,v3}{w1,w2,w3}{o1,o2,o3}`` -> (rotation rows [u, v, w], translation o).
    Identity/origin when absent or malformed -- a fixture with a bad Matrix is still worth
    reporting rather than aborting the whole scene."""
    if text:
        groups = re.findall(r"\{([^}]*)\}", text)
        if len(groups) == 4:
            try:
                nums = [[float(x) for x in g.split(",")] for g in groups]
            except ValueError:
                nums = []
            if len(nums) == 4 and all(len(n) == 3 for n in nums):
                tx, ty, tz = nums[3]
                return nums[:3], (tx, ty, tz)
    return [row[:] for row in _IDENTITY_ROWS], (0.0, 0.0, 0.0)


def _parse_address(addr_el) -> tuple[int, int, int]:
    """(dmx_break, universe, address), 1-based: either an absolute address (1..) split into
    512-channel universes, or an explicit "universe.address" -- same rule as DIN SPEC 15801."""
    dmx_break = int(addr_el.get("break", 0) or 0)
    raw = (addr_el.text or "1").strip() or "1"
    if raw == "0":
        raw = "1"
    if "." in raw:
        uni_s, addr_s = raw.split(".", 1)
        universe = int(uni_s) if int(uni_s) > 0 else 1
        address = int(addr_s) if int(addr_s) > 0 else 1
        return dmx_break, universe, address
    absolute = int(raw)
    universe = (absolute - 1) // 512 + 1
    address = (absolute - 1) % 512 + 1
    return dmx_break, universe, address


def _parse_fixture(el, layer_name: str) -> MvrFixture:
    rows, pos = _parse_matrix(el.findtext("Matrix"))
    gdtf_spec = el.findtext("GDTFSpec")
    if gdtf_spec and gdtf_spec[-5:].lower() != ".gdtf":
        gdtf_spec = f"{gdtf_spec}.gdtf"
    dmx_break = universe = address = None
    addresses_el = el.find("Addresses")
    if addresses_el is not None:
        addr_el = addresses_el.find("Address")
        if addr_el is not None:
            dmx_break, universe, address = _parse_address(addr_el)
    return MvrFixture(
        name=el.get("name") or "",
        fixture_id=el.findtext("FixtureID"),
        gdtf_spec=gdtf_spec,
        gdtf_mode=el.findtext("GDTFMode"),
        position=pos,
        rotation=rows,
        universe=universe,
        address=address,
        dmx_break=dmx_break or 0,
        layer=layer_name,
    )


def _parse_scene_object(el, kind: str, layer_name: str) -> MvrSceneObject:
    rows, pos = _parse_matrix(el.findtext("Matrix"))
    geometry_file = None
    geoms_el = el.find("Geometries")
    if geoms_el is not None:
        g3d = geoms_el.find("Geometry3D")
        if g3d is not None:
            geometry_file = g3d.get("fileName") or None
    return MvrSceneObject(name=el.get("name") or "", kind=kind, position=pos, rotation=rows,
                           geometry_file=geometry_file, layer=layer_name)


def _walk_child_list(child_list_el, layer_name: str, fixtures: list[MvrFixture],
                      objects: list[MvrSceneObject]) -> None:
    """Recurse through ChildList/GroupObject/ChildList/... to any depth (MVR nests groups this
    way); every fixture/object found is attributed to the enclosing top-level Layer's name."""
    if child_list_el is None:
        return
    for el in child_list_el:
        tag = _local(el)
        if tag == "Fixture":
            fixtures.append(_parse_fixture(el, layer_name))
        elif tag in ("Truss", "SceneObject", "Support"):
            objects.append(_parse_scene_object(el, tag, layer_name))
        elif tag == "GroupObject":
            _walk_child_list(el.find("ChildList"), layer_name, fixtures, objects)
        # FocusPoint/VideoScreen/Projector carry no fixture/geometry data we need: skipped.


def decode_mvr(path: str | Path) -> MvrScene:
    """Parse an MVR zip into an MvrScene.

    Raises Formats3DError for an unsafe (zip-slip) or oversized archive, a missing or malformed
    GeneralSceneDescription.xml, or a file that isn't a zip at all.
    """
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise Formats3DError(f"not a zip file: {path}") from exc
    with zf:
        check_zip_safety(zf)
        names = zf.namelist()
        gsd_name = next((n for n in names if _local_path(n) == "generalscenedescription.xml"), None)
        if gsd_name is None:
            raise Formats3DError("no GeneralSceneDescription.xml in the MVR archive")
        try:
            root = etree.fromstring(zf.read(gsd_name), parser=SAFE_XML_PARSER)
        except etree.XMLSyntaxError as exc:
            raise Formats3DError(f"malformed GeneralSceneDescription.xml: {exc}") from exc

        gdtf_files = sorted(n for n in names if n.lower().endswith(".gdtf"))
        fixtures: list[MvrFixture] = []
        objects: list[MvrSceneObject] = []
        layer_names: list[str] = []

        scene_el = root.find("Scene")
        layers_el = scene_el.find("Layers") if scene_el is not None else None
        if layers_el is not None:
            for layer_el in layers_el.findall("Layer"):
                name = layer_el.get("name") or ""
                layer_names.append(name)
                _walk_child_list(layer_el.find("ChildList"), name, fixtures, objects)

        return MvrScene(fixtures=fixtures, layers=layer_names, scene_objects=objects,
                         gdtf_files=gdtf_files, units="mm")


def _local_path(name: str) -> str:
    return name.rsplit("/", 1)[-1].lower()
