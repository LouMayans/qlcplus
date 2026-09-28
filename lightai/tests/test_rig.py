from lightai.rig.qxw import Workspace
from lightai.rig.roles import channel_group_role

from conftest import BLANK, MAIN


def test_roundtrip_is_byte_identical():
    for path in (MAIN, BLANK):
        assert Workspace.load(path).serialize() == path.read_bytes()


def test_roles_match_blank_rig_channel_groups(blank_rig):
    ws = Workspace.load(BLANK)
    checked = 0
    for g in ws.channels_groups():
        role = channel_group_role(g.name)
        assert role, g.name
        for fid, ch in g.pairs:
            assert blank_rig.fixtures[fid].roles[role] == ch, (g.name, fid, ch)
            checked += 1
    assert checked >= 60


def test_spot_models_have_their_own_channels(rig):
    base, v2, v3 = rig.fixtures[1], rig.fixtures[0], rig.fixtures[34]
    assert (base.roles["pan"], base.roles["tilt"], base.roles["dimmer"], base.roles["shutter"], base.roles["color_wheel"]) == (0, 2, 5, 6, 8)
    assert (v2.roles["pan"], v2.roles["tilt"], v2.roles["dimmer"], v2.roles["shutter"], v2.roles["color_wheel"]) == (9, 11, 2, 1, 0)
    assert (v3.roles["pan"], v3.roles["tilt"], v3.roles["dimmer"], v3.roles["shutter"], v3.roles["color_wheel"]) == (0, 1, 7, 6, 8)


def test_capabilities(rig):
    assert rig.fixtures[1].caps["shutter"]["open"] == 255
    assert rig.fixtures[8].caps["shutter"]["open"] == 0
    assert rig.fixtures[1].caps["pt_speed_fast"] == 0
    assert rig.fixtures[34].caps["wheel"]["blue"] == 24
    assert rig.fixtures[1].caps["wheel"]["blue"] == 88
    assert rig.fixtures[1].caps["dimmer_ramp_max"] == 112
    assert rig.dimmer_value(rig.fixtures[1], 0.5) == 56
    assert rig.dimmer_value(rig.fixtures[1], 1.0) == 255


def test_zones_and_stage_order(rig):
    assert rig.zones["washes"] == [8, 9, 10, 11]
    assert 33 not in rig.zones["all"] and 27 not in rig.zones["all"]
    assert rig.ordered([15, 14, 1]) == [14, 1, 15]


def test_wheel_nearest_color(rig):
    w = rig.wheel_value(rig.fixtures[1], "purple")
    assert not w["exact"] and w["slot"] in ("blue", "pink")
    assert rig.wheel_value(rig.fixtures[34], "white") == {"value": 99, "slot": "white", "exact": True}


def test_rgbw_mix(rig):
    wash = rig.fixtures[8]
    vals = rig.emitter_values(wash, "blue", 1.0)
    assert vals == {"red": 0, "green": 0, "blue": 240, "white": 15}
    assert rig.emitter_values(wash, "white", 1.0)["white"] == 255
