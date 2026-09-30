"""Tests for lightai.mine.formats3d: the MVR/GDTF decoders and the stage.json proposal converter.

Every MVR/GDTF archive here is a tiny, synthetic zip built in-memory (or, for the two on-disk
smoke-test files under tests/data/formats3d/, generated once as hand-authored XML) following the
XML shape documented in mvr.py/gdtf.py -- confirmed against the DIN SPEC 15801/15800 markdown and
the python-mvr reference parser's source, not guessed. No real MVR/GDTF files are downloaded or
stored in the repo.
"""

from __future__ import annotations

import math
import zipfile
from pathlib import Path

import pytest

import lightai.rig.stage as stage_mod
from lightai.mine.formats3d import (
    Formats3DError,
    MvrFixture,
    MvrScene,
    check_zip_safety,
    decode_gdtf,
    decode_mvr,
    mvr_to_stage,
)
from lightai.mine.formats3d.to_stage import guess_hang, mm_to_in, rotation_to_degrees

DATA_DIR = Path(__file__).with_name("data") / "formats3d"


# ------------------------------------------------------------------------------------- zip build
def make_zip(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content.encode("utf-8") if isinstance(content, str) else content)
    return path


def make_zip_raw_names(path: Path, entries: dict) -> Path:
    """Like make_zip, but writes each entry via a raw ZipInfo so an unsafe (zip-slip) name is
    written verbatim -- zipfile only sanitizes on *extract*, never on write."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(zipfile.ZipInfo(name), content.encode("utf-8") if isinstance(content, str) else content)
    return path


# ------------------------------------------------------------------------------------- MVR XML
def matrix_text(rows, translation) -> str:
    def g(vec):
        return ",".join(repr(float(x)) for x in vec)
    u, v, w = rows
    return f"{{{g(u)}}}{{{g(v)}}}{{{g(w)}}}{{{g(translation)}}}"


IDENTITY_ROWS = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
RX180_ROWS = [[1, 0, 0], [0, -1, 0], [0, 0, -1]]


def fixture_xml(name, matrix, *, fixture_id=None, gdtf_spec=None, gdtf_mode=None, address=None) -> str:
    parts = [f"<Matrix>{matrix}</Matrix>"]
    if gdtf_spec is not None:
        parts.append(f"<GDTFSpec>{gdtf_spec}</GDTFSpec>")
    if gdtf_mode is not None:
        parts.append(f"<GDTFMode>{gdtf_mode}</GDTFMode>")
    if fixture_id is not None:
        parts.append(f"<FixtureID>{fixture_id}</FixtureID>")
    if address is not None:
        parts.append(f'<Addresses><Address break="0">{address}</Address></Addresses>')
    return f'<Fixture name="{name}" uuid="f-{name}">' + "".join(parts) + "</Fixture>"


def group_xml(name, children: str) -> str:
    return f'<GroupObject name="{name}" uuid="g-{name}"><ChildList>{children}</ChildList></GroupObject>'


def truss_xml(name, matrix, geometry_file=None) -> str:
    geoms = f'<Geometries><Geometry3D fileName="{geometry_file}"/></Geometries>' if geometry_file else ""
    return f'<Truss name="{name}" uuid="t-{name}"><Matrix>{matrix}</Matrix>{geoms}</Truss>'


def layer_xml(name, children: str) -> str:
    return f'<Layer name="{name}" uuid="l-{name}"><ChildList>{children}</ChildList></Layer>'


def gsd(layers_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<GeneralSceneDescription verMajor="1" verMinor="6">'
        f"<Scene><Layers>{layers_xml}</Layers></Scene>"
        "</GeneralSceneDescription>"
    )


# ------------------------------------------------------------------------------------- GDTF XML
def gdtf_description_xml(*, short_name="B230") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<GDTF DataVersion="1.2">'
        f'<FixtureType Name="Beam230" ShortName="{short_name}" Manufacturer="Acme">'
        "<Geometries>"
        '<Geometry Name="Base" Model="BaseModel">'
        '<Axis Name="Yoke"><Axis Name="Head">'
        '<Beam Name="Beam1" BeamAngle="12.5" FieldAngle="14.0"/>'
        "</Axis></Axis>"
        "</Geometry>"
        "</Geometries>"
        '<Models><Model Name="BaseModel" File="base"/></Models>'
        "<DMXModes><DMXMode Name=\"16 Channel\"><DMXChannels>"
        '<DMXChannel DMXBreak="1" Offset="1" Geometry="Yoke">'
        '<LogicalChannel Attribute="Pan">'
        '<ChannelFunction Name="Pan" PhysicalFrom="-270" PhysicalTo="270"/>'
        "</LogicalChannel></DMXChannel>"
        '<DMXChannel DMXBreak="1" Offset="2" Geometry="Head">'
        '<LogicalChannel Attribute="Tilt">'
        '<ChannelFunction Name="Tilt" PhysicalFrom="-130" PhysicalTo="130"/>'
        "</LogicalChannel></DMXChannel>"
        '<DMXChannel DMXBreak="1" Offset="3,4" Geometry="Head">'
        '<LogicalChannel Attribute="Dim">'
        '<ChannelFunction Name="Dim" PhysicalFrom="0" PhysicalTo="1"/>'
        "</LogicalChannel></DMXChannel>"
        '<DMXChannel DMXBreak="1" Offset="None" Geometry="Head">'
        '<LogicalChannel Attribute="ColorMacro1"/>'
        "</DMXChannel>"
        "</DMXChannels></DMXMode></DMXModes>"
        "</FixtureType></GDTF>"
    )


# ------------------------------------------------------------------------------------- MVR: decode_mvr
def test_decode_mvr_basic_fixture_position_rotation_dmx(tmp_path):
    m = matrix_text(IDENTITY_ROWS, (1000.5, 2000.25, 300.0))
    fx = fixture_xml("Spot 1", m, fixture_id="1", gdtf_spec="Acme@Beam230",
                      gdtf_mode="16 Channel", address="513")
    path = make_zip(tmp_path / "scene.mvr", {"GeneralSceneDescription.xml": gsd(layer_xml("Front Truss", fx))})

    scene = decode_mvr(path)
    assert scene.layers == ["Front Truss"]
    assert len(scene.fixtures) == 1
    f = scene.fixtures[0]
    assert f.name == "Spot 1"
    assert f.fixture_id == "1"
    assert f.gdtf_spec == "Acme@Beam230.gdtf"  # ".gdtf" auto-appended when missing
    assert f.gdtf_mode == "16 Channel"
    assert f.layer == "Front Truss"
    assert f.position == pytest.approx((1000.5, 2000.25, 300.0))
    for got_row, want_row in zip(f.rotation, IDENTITY_ROWS):
        assert got_row == pytest.approx(want_row)
    # absolute address 513 -> 512 channels/universe, 1-based: universe 2, address 1
    assert (f.universe, f.address) == (2, 1)


def test_decode_mvr_dotted_dmx_address(tmp_path):
    fx = fixture_xml("Wash 1", matrix_text(IDENTITY_ROWS, (0, 0, 0)), address="2.10")
    path = make_zip(tmp_path / "scene.mvr", {"GeneralSceneDescription.xml": gsd(layer_xml("L1", fx))})

    f = decode_mvr(path).fixtures[0]
    assert (f.universe, f.address) == (2, 10)


def test_decode_mvr_fixture_without_addresses_has_none(tmp_path):
    fx = fixture_xml("No Patch", matrix_text(IDENTITY_ROWS, (0, 0, 0)))
    path = make_zip(tmp_path / "scene.mvr", {"GeneralSceneDescription.xml": gsd(layer_xml("L1", fx))})

    f = decode_mvr(path).fixtures[0]
    assert (f.universe, f.address) == (None, None)


def test_decode_mvr_nested_groups_and_layers(tmp_path):
    inner_fx = fixture_xml("Deep Fixture", matrix_text(IDENTITY_ROWS, (10, 20, 30)), fixture_id="7")
    nested = group_xml("Outer Group", group_xml("Inner Group", inner_fx))
    path = make_zip(tmp_path / "scene.mvr", {"GeneralSceneDescription.xml": gsd(layer_xml("Main Layer", nested))})

    scene = decode_mvr(path)
    assert scene.layers == ["Main Layer"]
    assert len(scene.fixtures) == 1
    f = scene.fixtures[0]
    assert f.name == "Deep Fixture"
    assert f.fixture_id == "7"
    assert f.layer == "Main Layer"  # attributed to the enclosing top-level Layer, not the group
    assert f.position == pytest.approx((10.0, 20.0, 30.0))


def test_decode_mvr_truss_geometry_file(tmp_path):
    truss = truss_xml("Truss A", matrix_text(IDENTITY_ROWS, (0, 0, 5000)), geometry_file="truss.glb")
    path = make_zip(tmp_path / "scene.mvr", {"GeneralSceneDescription.xml": gsd(layer_xml("Rig", truss))})

    scene = decode_mvr(path)
    assert len(scene.scene_objects) == 1
    obj = scene.scene_objects[0]
    assert (obj.name, obj.kind, obj.geometry_file, obj.layer) == ("Truss A", "Truss", "truss.glb", "Rig")
    assert obj.position == pytest.approx((0.0, 0.0, 5000.0))


def test_decode_mvr_truss_without_geometries_still_parses(tmp_path):
    bare = f'<Truss name="Bare" uuid="t-bare"><Matrix>{matrix_text(IDENTITY_ROWS, (1, 2, 3))}</Matrix></Truss>'
    path = make_zip(tmp_path / "scene.mvr", {"GeneralSceneDescription.xml": gsd(layer_xml("Rig", bare))})

    obj = decode_mvr(path).scene_objects[0]
    assert obj.geometry_file is None
    assert obj.position == pytest.approx((1.0, 2.0, 3.0))


def test_decode_mvr_lists_embedded_gdtf_files(tmp_path):
    path = make_zip(tmp_path / "scene.mvr", {
        "GeneralSceneDescription.xml": gsd(layer_xml("L1", "")),
        "Acme@Beam230.gdtf": b"fake-gdtf-bytes",
        "models/Other@Fixture.gdtf": b"more-bytes",
        "notes.txt": b"ignore me",
    })
    scene = decode_mvr(path)
    assert set(scene.gdtf_files) == {"Acme@Beam230.gdtf", "models/Other@Fixture.gdtf"}


def test_decode_mvr_missing_gsd_refused(tmp_path):
    path = make_zip(tmp_path / "bad.mvr", {"readme.txt": "nope"})
    with pytest.raises(Formats3DError):
        decode_mvr(path)


def test_decode_mvr_not_a_zip_refused(tmp_path):
    path = tmp_path / "bad.mvr"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(Formats3DError):
        decode_mvr(path)


def test_decode_mvr_zip_slip_refused(tmp_path):
    path = make_zip_raw_names(tmp_path / "evil.mvr", {
        "GeneralSceneDescription.xml": gsd(layer_xml("L1", "")),
        "../../evil.txt": "pwned",
    })
    with pytest.raises(Formats3DError):
        decode_mvr(path)


def test_decode_mvr_zip_slip_windows_absolute_refused(tmp_path):
    path = make_zip_raw_names(tmp_path / "evil2.mvr", {
        "GeneralSceneDescription.xml": gsd(layer_xml("L1", "")),
        "C:\\evil.txt": "pwned",
    })
    with pytest.raises(Formats3DError):
        decode_mvr(path)


# ------------------------------------------------------------------------------------- GDTF: decode_gdtf
def test_decode_gdtf_full(tmp_path):
    path = make_zip(tmp_path / "fixture.gdtf", {"description.xml": gdtf_description_xml()})
    fx = decode_gdtf(path)

    assert (fx.manufacturer, fx.name, fx.short_name) == ("Acme", "Beam230", "B230")

    assert len(fx.modes) == 1
    mode = fx.modes[0]
    assert mode.name == "16 Channel"
    assert [c.attribute for c in mode.channels] == ["Pan", "Tilt", "Dim", "ColorMacro1"]
    assert [c.offset for c in mode.channels] == [[1], [2], [3, 4], None]

    assert fx.physical.beam_angle == pytest.approx(12.5)
    assert fx.physical.field_angle == pytest.approx(14.0)
    assert fx.physical.pan_range == pytest.approx((-270.0, 270.0))
    assert fx.physical.tilt_range == pytest.approx((-130.0, 130.0))

    names_types = {(g["name"], g["type"]) for g in fx.geometries}
    assert {("Base", "Geometry"), ("Yoke", "Axis"), ("Head", "Axis"), ("Beam1", "Beam")} <= names_types

    assert fx.models == ["base"]


def test_decode_gdtf_missing_description_refused(tmp_path):
    path = make_zip(tmp_path / "bad.gdtf", {"readme.txt": "nope"})
    with pytest.raises(Formats3DError):
        decode_gdtf(path)


def test_decode_gdtf_no_fixture_type_refused(tmp_path):
    xml = '<?xml version="1.0"?><GDTF DataVersion="1.2"></GDTF>'
    path = make_zip(tmp_path / "bad.gdtf", {"description.xml": xml})
    with pytest.raises(Formats3DError):
        decode_gdtf(path)


def test_decode_gdtf_zip_slip_refused(tmp_path):
    path = make_zip_raw_names(tmp_path / "evil.gdtf", {
        "description.xml": gdtf_description_xml(),
        "../escape.txt": "pwned",
    })
    with pytest.raises(Formats3DError):
        decode_gdtf(path)


# ------------------------------------------------------------------------------------- safety helper
def test_check_zip_safety_oversize_refused(tmp_path):
    path = make_zip(tmp_path / "big.zip", {"a.txt": "x" * 1000, "b.txt": "y" * 1000})
    with zipfile.ZipFile(path) as zf:
        with pytest.raises(Formats3DError):
            check_zip_safety(zf, max_total_bytes=500)


def test_check_zip_safety_within_cap_ok(tmp_path):
    path = make_zip(tmp_path / "small.zip", {"a.txt": "x" * 10})
    with zipfile.ZipFile(path) as zf:
        infos = check_zip_safety(zf, max_total_bytes=500)
        assert len(infos) == 1


# ------------------------------------------------------------------------------------- on-disk smoke test
def test_decode_mvr_sample_file_on_disk():
    scene = decode_mvr(DATA_DIR / "sample.mvr")
    assert [f.name for f in scene.fixtures] == ["Spot 1"]
    assert scene.fixtures[0].gdtf_spec == "Acme@Beam230.gdtf"


def test_decode_gdtf_sample_file_on_disk():
    fx = decode_gdtf(DATA_DIR / "sample.gdtf")
    assert fx.manufacturer == "Acme"
    assert [c.attribute for c in fx.modes[0].channels] == ["Pan", "Tilt"]


# ------------------------------------------------------------------------------------- to_stage: rotation math
def test_mm_to_in():
    assert mm_to_in(25.4) == pytest.approx(1.0)
    assert mm_to_in(2540.0) == pytest.approx(100.0)


def _rows(m):  # MVR rows are the local axes in world: the transpose of the column-vector matrix
    return [[m[j][i] for j in range(3)] for i in range(3)]


def _euler(rx, ry, rz):
    return stage_mod._mul(stage_mod._rz(rz), stage_mod._mul(stage_mod._ry(ry), stage_mod._rx(rx)))


def test_identity_matrix_is_a_hung_fixture_pointing_down():
    """GDTF draws fixtures hanging with the beam along local -Z, so an unrotated MVR fixture hangs, beam down."""
    assert guess_hang(IDENTITY_ROWS) == "hung"
    assert rotation_to_degrees(IDENTITY_ROWS, "hung") == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
    entry = {"rot": [0, 0, 0], "hang": "hung"}
    assert stage_mod.beam_direction(entry, 0, 0) == pytest.approx((0.0, 0.0, -1.0), abs=1e-9)


def test_flipped_fixture_stands_on_the_floor_and_sideways_is_wall():
    assert guess_hang(RX180_ROWS) == "floor"
    assert rotation_to_degrees(RX180_ROWS, "floor") == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
    assert guess_hang(_rows(stage_mod._rx(90.0))) == "wall"


@pytest.mark.parametrize("rx,ry,rz", [(0, 0, 0), (180, 0, 0), (15, 20, 30), (10, -25, 200), (95, 10, -40), (-60, 80, 5),
                                      (170, -15, 90), (0, 90, 0)])
def test_converted_rotation_matches_the_stage_maths(rx, ry, rz):
    """Whatever the matrix, the stage's own orientation() of the converted entry is M . Rx(180), and the rest beam
    (pan = tilt = 0) points where the GDTF beam (-Z) points in the MVR world."""
    m = _euler(rx, ry, rz)
    rows = _rows(m)
    hang = guess_hang(rows)
    entry = {"rot": list(rotation_to_degrees(rows, hang)), "hang": hang}
    want = stage_mod._mul(m, stage_mod._rx(180.0))
    for got_row, want_row in zip(stage_mod.orientation(entry), want):
        assert got_row == pytest.approx(want_row, abs=1e-6)
    gdtf_beam = [-rows[2][0], -rows[2][1], -rows[2][2]]
    assert stage_mod.beam_direction(entry, 0, 0) == pytest.approx(gdtf_beam, abs=1e-6)


# ------------------------------------------------------------------------------------- to_stage: mvr_to_stage
class _FakeFx:
    def __init__(self, name):
        self.name = name


class _FakeRig:
    def __init__(self, fixtures):
        self.fixtures = fixtures


def test_mvr_to_stage_matches_by_id_and_name_reports_unmatched():
    scene = MvrScene(fixtures=[
        MvrFixture(name="Spot 1", fixture_id="1", gdtf_spec=None, gdtf_mode=None,
                   position=(2540.0, 5080.0, 1270.0), rotation=IDENTITY_ROWS,
                   universe=1, address=1, layer="L1"),
        MvrFixture(name="Wash 2", fixture_id=None, gdtf_spec=None, gdtf_mode=None,
                   position=(0.0, 0.0, 2000.0), rotation=RX180_ROWS,
                   universe=1, address=20, layer="L1"),
        MvrFixture(name="Unknown Thing", fixture_id="99", gdtf_spec=None, gdtf_mode=None,
                   position=(0.0, 0.0, 0.0), rotation=IDENTITY_ROWS,
                   universe=None, address=None, layer="L1"),
    ], layers=["L1"])
    rig = _FakeRig({1: _FakeFx("Spot 1"), 2: _FakeFx("wash 2")})  # case-insensitive name match

    proposal = mvr_to_stage(scene, rig)

    assert set(proposal["fixtures"].keys()) == {"1", "2"}
    assert proposal["fixtures"]["1"]["pos"] == pytest.approx([100.0, 200.0, 50.0])  # mm / 25.4
    assert proposal["fixtures"]["1"]["hang"] == "hung"  # unrotated = GDTF's hanging default, beam down
    assert proposal["fixtures"]["2"]["hang"] == "floor"  # flipped over: beam up
    assert len(proposal["unmatched"]) == 1
    assert proposal["unmatched"][0]["mvr_name"] == "Unknown Thing"
    assert proposal["unmatched"][0]["mvr_fixture_id"] == "99"


def test_mvr_to_stage_no_rig_everything_unmatched():
    scene = MvrScene(fixtures=[
        MvrFixture(name="A", fixture_id="1", gdtf_spec=None, gdtf_mode=None,
                   position=(0.0, 0.0, 0.0), rotation=IDENTITY_ROWS, universe=1, address=1),
        MvrFixture(name="B", fixture_id="2", gdtf_spec=None, gdtf_mode=None,
                   position=(0.0, 0.0, 0.0), rotation=IDENTITY_ROWS, universe=1, address=2),
    ])
    proposal = mvr_to_stage(scene, rig=None)
    assert proposal["fixtures"] == {}
    assert len(proposal["unmatched"]) == 2


def test_mvr_to_stage_never_writes_a_file(tmp_path, monkeypatch):
    """mvr_to_stage only returns a dict -- it must not touch the filesystem at all."""
    monkeypatch.chdir(tmp_path)
    scene = MvrScene(fixtures=[
        MvrFixture(name="A", fixture_id="1", gdtf_spec=None, gdtf_mode=None,
                   position=(0.0, 0.0, 0.0), rotation=IDENTITY_ROWS, universe=1, address=1),
    ])
    mvr_to_stage(scene, rig=_FakeRig({1: _FakeFx("A")}))
    assert list(tmp_path.iterdir()) == []


def test_decode_mvr_then_to_stage_end_to_end(tmp_path):
    m = matrix_text(IDENTITY_ROWS, (2540.0, 5080.0, 1270.0))
    fx = fixture_xml("Spot 1", m, fixture_id="3", address="1")
    path = make_zip(tmp_path / "scene.mvr", {"GeneralSceneDescription.xml": gsd(layer_xml("L1", fx))})

    scene = decode_mvr(path)
    proposal = mvr_to_stage(scene, _FakeRig({3: _FakeFx("Spot 1")}))

    assert "3" in proposal["fixtures"]
    assert proposal["fixtures"]["3"]["pos"] == pytest.approx([100.0, 200.0, 50.0])
    assert proposal["fixtures"]["3"]["gdtf_mode"] is None
    assert proposal["layers"] == ["L1"]
