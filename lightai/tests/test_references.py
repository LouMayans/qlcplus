"""The private reference library: decode once, rank for a request, brief the designer (iteration 10)."""

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import lightai.mine.formats as formats
from lightai.mine import index as refs

SAMPLES = Path(__file__).parent / "data" / "formats"


@pytest.fixture()
def cfg(tmp_path):
    root = tmp_path / "references"
    root.mkdir()
    for name in ("sample.xsq", "sample.lms", "sample.tim", "malformed.xsq", "random.txt"):
        shutil.copy(SAMPLES / name, root / name)
    return SimpleNamespace(data_dir=tmp_path, references_dir=None)


def test_index_decodes_each_file_once(cfg, monkeypatch):
    files = refs.build_index(cfg)["files"]
    assert set(files) == {"sample.xsq", "sample.lms", "sample.tim", "malformed.xsq"}  # random.txt isn't a show format
    assert "error" in files["malformed.xsq"] and all("summary" in files[k] for k in ("sample.xsq", "sample.lms", "sample.tim"))
    xsq = files["sample.xsq"]["summary"]
    assert xsq["title"] == "Neon Nights" and xsq["song"]["artist"] == "DJ Synthetic" and xsq["format"] == "xlights"
    assert {"red", "blue", "white"} <= set(xsq["palette"]) and len(xsq["energy_shape"]) == 8
    calls = []
    real = formats.decode_file
    monkeypatch.setattr(formats, "decode_file", lambda p: calls.append(Path(p).name) or real(p))
    refs.build_index(cfg)
    assert calls == []  # every file comes from index.json, failures too (`lightai refs index --force` retries them)
    lms = Path(cfg.data_dir) / "references" / "sample.lms"
    os.utime(lms, (lms.stat().st_atime, lms.stat().st_mtime + 60))
    calls.clear()
    refs.build_index(cfg)
    assert calls == ["sample.lms"]  # only the changed file is decoded again


def test_score_prefers_matching_pace_palette_and_effects():
    dreamy = {"kind": "show", "title": "Slow Bloom", "avg_cue_s": 8.0, "energy_mean": 0.2, "pastel_share": 0.8,
              "effects": [["ColorWash", 0.7], ["Twinkle", 0.3]]}
    banger = {"kind": "show", "title": "Hard Drop", "avg_cue_s": 0.5, "energy_mean": 0.9, "pastel_share": 0.0,
              "effects": [["Strobe", 0.6], ["Bars", 0.4]]}
    assert refs.score(dreamy, "a dreamy pastel wash") > 0 > refs.score(banger, "a dreamy pastel wash")
    assert refs.score(banger, "energetic strobe chase") > refs.score(dreamy, "energetic strobe chase")
    assert refs.score(banger, "hard drop") >= 4.0
    assert refs.score({"kind": "rig", "title": "Hard Drop"}, "hard drop") == 0.0


def test_query_and_brief_from_the_library(cfg):
    idx = refs.build_index(cfg)
    top = refs.query_references(cfg, "something like neon nights", k=3, index=idx)
    assert top[0]["title"] == "Neon Nights" and top[0]["file"] == "sample.xsq"
    assert refs.query_references(cfg, "zzz qqq", index=idx) == []
    brief = refs.references_brief(cfg, "something like neon nights")
    assert brief.startswith("Decoded from shows") and '"Neon Nights" - DJ Synthetic (xlights, 0:30)' in brief
    assert "Strobe" in brief and "palette" in brief
    assert refs.references_brief(cfg, "zzz qqq") == ""


def test_no_library_means_no_briefing(tmp_path):
    cfg = SimpleNamespace(data_dir=tmp_path, references_dir=None)
    assert refs.build_index(cfg)["files"] == {} and not (tmp_path / "references").exists()
    assert refs.references_brief(cfg, "dreamy strobe show") == ""


def test_color_families():
    assert refs.color_family("#FF0000") == ("red", False)
    assert refs.color_family("#FFFFFF") == ("white", False)
    assert refs.color_family("#0000FF")[0] == "blue"
    assert refs.color_family("#AFC8FF") == ("blue", True)  # pastel
    assert refs.color_family("#050505") == ("black", False)
