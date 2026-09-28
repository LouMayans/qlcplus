"""Derive channel roles (dimmer, pan, color_wheel, red, ...) from evidence, never hardcoded.

Evidence and scores (highest wins per role, ties go to the lowest channel index):
  10  operator facts in overrides.yaml
   5  ChannelsGroups of the roles template (e.g. "Spot Dimmer" -> dimmer)
   3  .qxf channel Preset
   2  .qxf Group (+Colour) / channel name when no preset
 1.5  channel name that contradicts the preset (kept as a weaker candidate)
"""

from __future__ import annotations

import re
from typing import Optional

from lightai.rig.qxf import ChannelDef

COLOURS = ("red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow", "lime", "indigo")

PRESET_ROLES = {
    "IntensityMasterDimmer": "dimmer",
    "IntensityMasterDimmerFine": "dimmer_fine",
    "IntensityDimmer": "dimmer",
    "IntensityDimmerFine": "dimmer_fine",
    "IntensityHue": "hue",
    "IntensitySaturation": "saturation",
    "IntensityLightness": "lightness",
    "IntensityValue": "value",
    "PositionPan": "pan",
    "PositionPanFine": "pan_fine",
    "PositionTilt": "tilt",
    "PositionTiltFine": "tilt_fine",
    "PositionXAxis": "x_axis",
    "PositionYAxis": "y_axis",
    "SpeedPanSlowFast": "pan_speed",
    "SpeedPanFastSlow": "pan_speed",
    "SpeedTiltSlowFast": "tilt_speed",
    "SpeedTiltFastSlow": "tilt_speed",
    "SpeedPanTiltSlowFast": "pt_speed",
    "SpeedPanTiltFastSlow": "pt_speed",
    "ColorMacro": "color_macro",
    "ColorWheel": "color_wheel",
    "ColorWheelFine": "color_wheel_fine",
    "ColorRGBMixer": "rgb_mixer",
    "ColorCTOMixer": "cto",
    "ColorCTCMixer": "ctc",
    "ColorCTBMixer": "ctb",
    "GoboWheel": "gobo",
    "GoboWheelFine": "gobo_fine",
    "GoboIndex": "gobo_index",
    "GoboIndexFine": "gobo_index_fine",
    "ShutterStrobeSlowFast": "shutter",
    "ShutterStrobeFastSlow": "shutter",
    "ShutterIrisMinToMax": "iris",
    "ShutterIrisMaxToMin": "iris",
    "ShutterIrisFine": "iris_fine",
    "BeamFocusNearFar": "focus",
    "BeamFocusFarNear": "focus",
    "BeamFocusFine": "focus_fine",
    "BeamZoomSmallBig": "zoom",
    "BeamZoomBigSmall": "zoom",
    "BeamZoomFine": "zoom_fine",
    "PrismRotationSlowFast": "prism_rotation",
    "PrismRotationFastSlow": "prism_rotation",
    "NoFunction": "none",
}
for _c in COLOURS:
    _t = "UV" if _c == "uv" else _c.capitalize()
    PRESET_ROLES[f"Intensity{_t}"] = _c
    PRESET_ROLES[f"Intensity{_t}Fine"] = f"{_c}_fine"

GROUP_ROLES = {
    "Shutter": "shutter",
    "Gobo": "gobo",
    "Prism": "prism",
    "Effect": "effect",
    "Maintenance": "maintenance",
    "Nothing": "none",
}

CHANNEL_GROUP_KEYWORDS = [
    ("dimmer", "dimmer"),
    ("intensity", "dimmer"),
    ("colour", "color_wheel"),
    ("color", "color_wheel"),
    ("strobe", "shutter"),
    ("shutter", "shutter"),
    ("pan", "pan"),
    ("tilt", "tilt"),
    ("red", "red"),
    ("green", "green"),
    ("blue", "blue"),
    ("white", "white"),
    ("amber", "amber"),
    ("uv", "uv"),
]


def name_role(name: str) -> Optional[str]:
    n = name.lower().strip()
    words = set(re.findall(r"[a-z]+", n))
    if words & {"reset", "lamp", "maintenance"}:
        return "maintenance"
    if "speed" in words and (words & {"pan", "tilt", "motor"} or "p/t" in n):
        return "pt_speed"
    if "pan" in words and "fine" in words:
        return "pan_fine"
    if "tilt" in words and "fine" in words:
        return "tilt_fine"
    if n == "pan" or n.startswith("pan "):
        return "pan"
    if n == "tilt" or n.startswith("tilt "):
        return "tilt"
    if words & {"strobe", "strobing", "shutter"}:
        return "shutter"
    if "macro" in n or "macros" in n:
        return "color_macro"
    if words & {"color", "colour"} and not (words & {"effect", "temperature"}):
        return "color_wheel"
    if "gobo" in words:
        return "gobo"
    if "prism" in words:
        return "prism_rotation" if "rotation" in words else "prism"
    if "focus" in words:
        return "focus"
    if "zoom" in words:
        return "zoom"
    if "frost" in words:
        return "frost"
    if "iris" in words:
        return "iris"
    if "dimmer" in words or n == "intensity" or "master" in words:
        return "dimmer"
    if n == "mode" or n.endswith(" mode"):
        return "mode"
    for c in COLOURS:
        if n == c or re.fullmatch(rf"{c}\s*\d*", n):
            return c
    return None


def channel_candidates(ch: ChannelDef) -> list:
    """Candidate (role, score, source) tuples for one channel of a fixture definition."""
    out = []
    if ch.preset:
        role = PRESET_ROLES.get(ch.preset)
        if role:
            out.append((role, 3.0, f"qxf preset {ch.preset}"))
    if ch.group and not ch.preset:
        g = ch.group
        role = None
        if g == "Intensity":
            role = ch.colour.lower() if ch.colour else None
            if role is None:
                nr = name_role(ch.name)
                role = nr if nr in ("dimmer",) + COLOURS else None
        elif g in ("Pan", "Tilt"):
            role = g.lower() + ("_fine" if ch.byte == 1 else "")
        elif g == "Colour":
            if any((c.preset or "").startswith("ColorMacro") for c in ch.capabilities) or "macro" in ch.name.lower():
                role = "color_macro"
            else:
                role = "color_wheel"
        elif g == "Speed":
            role = name_role(ch.name) if name_role(ch.name) == "pt_speed" else "speed"
        elif g == "Beam":
            role = name_role(ch.name) or "beam"
        else:
            role = GROUP_ROLES.get(g)
            nr = name_role(ch.name)
            if g == "Maintenance" and nr == "mode":
                role = "mode"
        if role:
            out.append((role, 2.0, f"qxf group {g}" + (f"/{ch.colour}" if ch.colour else "")))
    nr = name_role(ch.name)
    if nr and not any(r == nr for r, _, _ in out):
        score = 1.5 if out else 2.0
        out.append((nr, score, f"channel name '{ch.name}'"))
    return out


def channel_group_role(name: str) -> Optional[str]:
    n = name.lower()
    for key, role in CHANNEL_GROUP_KEYWORDS:
        if re.search(rf"\b{key}s?\b", n):
            return role
    return None


def pick_roles(candidates: dict) -> tuple:
    """candidates: {channel_index: [(role, score, source), ...]} -> (roles, sources, scores)."""
    best: dict = {}
    for idx in sorted(candidates):
        for role, score, source in candidates[idx]:
            cur = best.get(role)
            if cur is None or score > cur[1]:
                best[role] = (idx, score, source)
    roles = {r: v[0] for r, v in best.items() if r != "none"}
    sources = {r: v[2] for r, v in best.items() if r != "none"}
    scores = {r: v[1] for r, v in best.items() if r != "none"}
    return roles, sources, scores
