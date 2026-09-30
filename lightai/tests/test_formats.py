"""Tests for lightai.mine.formats: the xLights .xsq, Light-O-Rama .lms, and Vixen 3 .tim
reference-show decoders. Every sample under tests/data/formats/ is a tiny, hand-built synthetic
file following the documented/reverse-engineered XML shape each decoder's module docstring
cites -- no real show files are used or stored (see lightai/knowledge/research/format-survey.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lightai.mine.formats import Cue, NotDecodable, ReferenceShow, decode_file, registered
from lightai.mine.formats.lor import LorDecoder, _lor_color
from lightai.mine.formats.vixen3 import Vixen3Decoder, _effect_name
from lightai.mine.formats.xlights import XLightsDecoder

DATA_DIR = Path(__file__).with_name("data") / "formats"


def _times(show: ReferenceShow) -> list:
    return [t for t, _ in show.energy_curve]


def _is_monotonic(show: ReferenceShow) -> bool:
    ts = _times(show)
    return bool(ts) and all(a < b for a, b in zip(ts, ts[1:]))


# ---------------------------------------------------------------------------------------- xLights

def test_xlights_decode_produces_a_sensible_show():
    show = decode_file(DATA_DIR / "sample.xsq")
    assert show.format == "xlights"
    assert show.title == "Neon Nights"
    assert show.song is not None
    assert show.song.artist == "DJ Synthetic" and show.song.title == "Neon Nights"
    assert show.duration_s == 30.0
    assert len(show.cues) == 5
    assert all(isinstance(c, Cue) and c.end_s > c.start_s for c in show.cues)
    assert show.effect_types == {"Bars": 2, "ColorWash": 1, "Shockwave": 1, "Strobe": 1}
    assert set(show.palette) == {"#FF0000", "#FFFFFF", "#0000FF"}
    assert show.fixture_kinds == ["model"]
    # Par 1/2/3 fire in ascending order at ascending times -> reads as a chase
    assert show.spatial_moves == ["left_to_right chase"]
    assert _is_monotonic(show)


def test_xlights_timing_track_becomes_named_sections():
    show = decode_file(DATA_DIR / "sample.xsq")
    assert [s.name for s in show.sections] == ["Intro", "Drop", None]
    assert show.sections[0].start_s == 0.0
    assert show.sections[-1].end_s == show.duration_s
    for s in show.sections:
        assert 0.0 <= s.energy <= 1.0


def test_xlights_sniff_is_extension_and_root_specific():
    dec = XLightsDecoder()
    assert dec.sniff(DATA_DIR / "sample.xsq") is True
    assert dec.sniff(DATA_DIR / "sample.lms") is False
    assert dec.sniff(DATA_DIR / "sample.tim") is False


# -------------------------------------------------------------------------------------------- LOR

def test_lor_decode_produces_a_sensible_show():
    show = decode_file(DATA_DIR / "sample.lms")
    assert show.format == "lor"
    assert show.title == "club_anthem"  # from musicFilename="club_anthem.wav"
    assert show.duration_s == 30.0
    assert len(show.cues) == 5
    assert show.effect_types == {"twinkle": 1, "off": 1, "on": 1, "fade_up": 1, "shimmer": 1}
    assert set(show.palette) == {"#FF0000", "#00FF00", "#0000FF"}
    assert show.fixture_kinds == ["channel"]
    assert _is_monotonic(show)


def test_lor_timing_grids_split_into_bpm_and_sections():
    show = decode_file(DATA_DIR / "sample.lms")
    # the "fixed" grid taps every 0.5s -> 120 BPM; the separate "freeform" grid gives sections
    assert show.bpm == 120.0
    assert len(show.sections) == 3
    assert show.sections[0].start_s == 0.0 and show.sections[-1].end_s == show.duration_s


def test_lor_intensity_effects_carry_a_normalized_level():
    show = decode_file(DATA_DIR / "sample.lms")
    on_cue = next(c for c in show.cues if c.effect_type == "on")
    off_cue = next(c for c in show.cues if c.effect_type == "off")
    fade_cue = next(c for c in show.cues if c.effect_type == "fade_up")
    assert on_cue.intensity == 1.0
    assert off_cue.intensity == 0.0
    assert fade_cue.intensity == 1.0


def test_lor_color_is_a_decimal_colorref_not_hex():
    assert _lor_color("255") == "#FF0000"  # red is the low byte
    assert _lor_color("65280") == "#00FF00"
    assert _lor_color("16711680") == "#0000FF"
    assert _lor_color("not a number") is None
    assert _lor_color(None) is None


def test_lor_sniff_is_extension_and_root_specific():
    dec = LorDecoder()
    assert dec.sniff(DATA_DIR / "sample.lms") is True
    assert dec.sniff(DATA_DIR / "sample.xsq") is False
    assert dec.sniff(DATA_DIR / "sample.tim") is False


# ---------------------------------------------------------------------------------------- Vixen 3

def test_vixen3_decode_produces_a_sensible_show():
    show = decode_file(DATA_DIR / "sample.tim")
    assert show.format == "vixen3"
    assert show.duration_s == 60.0
    assert len(show.cues) == 4
    assert show.effect_types["Set Level"] == 1
    assert show.effect_types["Chase"] == 1
    assert show.effect_types["Twinkle"] == 1
    # an unrecognized effect-module GUID degrades to a label instead of crashing
    assert any(k.startswith("effect-") for k in show.effect_types)
    assert set(show.palette) == {"#3366CC", "#FF9900"}
    assert show.fixture_kinds == []  # not in the .tim file at all; see module docstring
    assert show.spatial_moves == ["left_to_right chase"]
    assert len(show.sections) == 2
    assert _is_monotonic(show)


def test_vixen3_effect_guid_lookup_is_graceful():
    assert _effect_name("32cff8e0-5b10-4466-a093-0d232c55aac0") == "Set Level"
    assert _effect_name("{ffffffff-ffff-ffff-ffff-ffffffffffff}") == "effect-ffffffff"
    assert _effect_name(None) == "Unknown"
    assert _effect_name("") == "Unknown"


def test_vixen3_sniff_is_extension_specific():
    dec = Vixen3Decoder()
    assert dec.sniff(DATA_DIR / "sample.tim") is True
    assert dec.sniff(DATA_DIR / "sample.xsq") is False
    assert dec.sniff(DATA_DIR / "sample.lms") is False


# --------------------------------------------------------------------------- registry and errors

def test_decode_file_picks_the_right_decoder_by_sniffing():
    assert decode_file(DATA_DIR / "sample.xsq").format == "xlights"
    assert decode_file(DATA_DIR / "sample.lms").format == "lor"
    assert decode_file(DATA_DIR / "sample.tim").format == "vixen3"


def test_registry_has_all_three_in_survey_rank_order():
    assert [d.name for d in registered()] == ["xlights", "lor", "vixen3"]


def test_closed_format_raises_not_decodable_with_a_specific_reason():
    with pytest.raises(NotDecodable) as exc_info:
        decode_file(DATA_DIR / "unknown_show.ssproj")
    assert "SoundSwitch" in exc_info.value.reason


def test_unrecognized_extension_raises_not_decodable():
    with pytest.raises(NotDecodable):
        decode_file(DATA_DIR / "random.txt")


def test_missing_file_raises_not_decodable_not_a_crash():
    with pytest.raises(NotDecodable):
        decode_file(DATA_DIR / "does_not_exist.xsq")


def test_malformed_xml_raises_a_clear_error_not_a_crash():
    with pytest.raises(ValueError, match="malformed XML"):
        decode_file(DATA_DIR / "malformed.xsq")
