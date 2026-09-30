"""Tests for lightai.design.research. FakeClaudeBackend only - never the real CLI."""

from __future__ import annotations

import asyncio
import copy
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fakeclaude import FakeClaudeBackend
from lightai.config import load_config
from lightai.design import research as research_mod
from lightai.design.research import ResearchMood, merge_moods, render_report, research
from lightai.design.runlog import RunLog

DATA = Path(__file__).parent / "data"
REAL_MOODS = Path(__file__).parent.parent / "knowledge" / "moods.yaml"

CANNED = {
    "topic": "afrobeats club lighting",
    "summary": "Warm, saturated color dominates over harsh strobing, with chases timed to the clave.",
    "findings": [{"title": "Warm palette dominates", "detail": "Ambers and oranges are the base wash.",
                 "sources": ["https://example.com/a", "https://example.com/b"]}],
    "moods": [{"name": "afro_groove", "words": ["afrobeats", "amapiano"], "recipes": ["color_chase", "not_a_recipe"],
              "avoid": [], "rate": ["medium", "fast"], "fade_ms": [800, 2000], "palette": ["amber", "octarine"],
              "intensity": [0.6, 0.9], "movement_size": ["medium", "big"], "strobe": "accents",
              "orders": ["left_to_right", "not_an_order"], "notes": "Warm groove-locked chases."}],
    "terms": [{"word": "clave", "meaning": "a recurring rhythmic pattern used to time chases and hits"}],
    "references": [{"title": "Afrobeats Lighting Breakdown", "url": "https://example.com/a", "kind": "article"}],
}


@pytest.fixture()
def cfg(tmp_path):
    c = copy.copy(load_config())
    c.data_dir = tmp_path / "data"
    c.data_dir.mkdir()
    return c


@pytest.fixture()
def kdir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    shutil.copy(REAL_MOODS, d / "moods.yaml")
    return d


# ---------------------------------------------------------------------------
# research() end to end
# ---------------------------------------------------------------------------

def test_research_end_to_end(cfg, kdir):
    backend = FakeClaudeBackend([CANNED], RunLog(cfg.data_dir))
    events: list = []
    result = asyncio.run(research(SimpleNamespace(cfg=cfg), CANNED["topic"], backend, on_event=events.append,
                                  knowledge_dir=kdir))

    assert result["ok"] is True
    assert result["moods_added"] == ["afro_groove"]
    assert result["moods_extended"] == []
    assert any("not_a_recipe" in d for d in result["dropped"])
    assert any("octarine" in d for d in result["dropped"])
    assert any("not_an_order" in d for d in result["dropped"])
    assert result["run_id"]
    assert events, "on_event should fire for the replayed stream"

    report = Path(result["report_path"])
    assert report.exists() and report.parent == kdir / "research"
    text = report.read_text(encoding="utf-8")
    assert "Warm palette dominates" in text
    assert "<https://example.com/a>" in text and "<https://example.com/b>" in text
    assert "clave" in text and "Afrobeats Lighting Breakdown" in text
    assert "afro_groove" in text and "not_a_recipe" in text  # dropped names are reported, not hidden

    doc = yaml.safe_load((kdir / "moods.yaml").read_text(encoding="utf-8"))
    entry = doc["moods"]["afro_groove"]
    assert entry["recipes"] == ["color_chase"]  # the bogus one never made it in
    assert entry["palette"] == ["amber"]  # octarine never made it in
    assert entry["orders"] == ["left_to_right"]
    assert doc["moods"]["dreamy"]["words"] == ["dreamy", "dream", "ethereal", "floaty", "hazy", "soft", "floaty"]

    backups = list(kdir.glob("moods.yaml.bak-*"))
    assert len(backups) == 1

    call = backend.calls[0]
    assert call["kind"] == "research"
    assert set(("WebSearch", "WebFetch")) <= set(call["allowed_tools"])
    assert call["agents"] and "researcher" in call["agents"]
    assert call["agents"]["researcher"]["tools"] == ["WebSearch", "WebFetch"]


def test_research_reports_backend_failure_without_writing_anything(cfg, kdir):
    backend = FakeClaudeBackend([str(DATA / "claude_stream_bare_auth_error.jsonl")], RunLog(cfg.data_dir))
    result = asyncio.run(research(cfg, "some topic", backend, knowledge_dir=kdir))

    assert result["ok"] is False
    assert "Not logged in" in result["error"]
    assert result["run_id"]
    assert not (kdir / "research").exists()
    assert not list(kdir.glob("moods.yaml.bak-*"))


def test_research_reports_invalid_structured_output(cfg, kdir):
    backend = FakeClaudeBackend([{"summary": "no topic field at all"}], RunLog(cfg.data_dir))
    result = asyncio.run(research(cfg, "some topic", backend, knowledge_dir=kdir))

    assert result["ok"] is False
    assert "invalid ResearchResult" in result["error"]
    assert not (kdir / "research").exists()


def test_research_accepts_a_bare_config_and_writes_the_brief_under_its_data_dir(cfg, kdir):
    backend = FakeClaudeBackend([CANNED], RunLog(cfg.data_dir))
    asyncio.run(research(cfg, CANNED["topic"], backend, knowledge_dir=kdir))
    briefs = list((cfg.data_dir / "research_ctx").glob("*.md"))
    assert len(briefs) == 1
    text = briefs[0].read_text(encoding="utf-8")
    assert "README.md" in text and "color_wash" in text and "center_out" in text
    assert "## The rig" not in text  # a bare Config has no get_rig(): the section is just skipped


def test_deep_flag_selects_opus(cfg, kdir):
    backend = FakeClaudeBackend([CANNED], RunLog(cfg.data_dir))
    asyncio.run(research(cfg, CANNED["topic"], backend, deep=True, knowledge_dir=kdir))
    assert backend.calls[0]["model"] == "opus"


# ---------------------------------------------------------------------------
# merge_moods
# ---------------------------------------------------------------------------

def test_merge_moods_adds_a_new_mood_and_drops_invalid_vocabulary(cfg, kdir):
    moods_path = kdir / "moods.yaml"
    proposal = [ResearchMood(name="Afro Groove", words=["afrobeats"], recipes=["color_chase", "not_a_recipe"],
                             avoid=["strobe", "also_bogus"], palette=["amber", "octarine"],
                             orders=["left_to_right", "not_an_order"])]
    added, extended, dropped = merge_moods(proposal, moods_path, cfg)

    assert added == ["afro_groove"] and extended == []
    assert len(dropped) == 4  # one message per field with a bad name: recipes, avoid, palette, orders
    assert any("not_a_recipe" in d for d in dropped)
    assert any("also_bogus" in d for d in dropped)
    assert any("octarine" in d for d in dropped)
    assert any("not_an_order" in d for d in dropped)

    entry = yaml.safe_load(moods_path.read_text(encoding="utf-8"))["moods"]["afro_groove"]
    assert entry["recipes"] == ["color_chase"]
    assert entry["avoid"] == ["strobe"]
    assert entry["palette"] == ["amber"]
    assert entry["orders"] == ["left_to_right"]


def test_merge_moods_extends_an_existing_mood_without_losing_anything(cfg, kdir):
    moods_path = kdir / "moods.yaml"
    before = yaml.safe_load(moods_path.read_text(encoding="utf-8"))["moods"]["dreamy"]

    proposal = [ResearchMood(name="dreamy", words=["gauzy"], recipes=["color_morph", "circle_wave"],
                             palette=["lavender", "teal"], rate=["fast"], notes="overwritten?")]
    added, extended, dropped = merge_moods(proposal, moods_path, cfg)

    assert added == [] and extended == ["dreamy"] and dropped == []
    after = yaml.safe_load(moods_path.read_text(encoding="utf-8"))["moods"]["dreamy"]

    assert after["words"] == before["words"] + ["gauzy"]
    assert after["recipes"] == before["recipes"]  # color_morph/circle_wave were already there: nothing to add
    assert after["palette"] == before["palette"] + ["teal"]
    # only words/recipes/palette are ever touched for an existing mood
    assert after["rate"] == before["rate"]
    assert after["notes"] == before["notes"]
    assert after["avoid"] == before["avoid"]
    assert after["fade_ms"] == before["fade_ms"]


def test_merge_moods_backs_up_only_when_something_changes(cfg, kdir):
    moods_path = kdir / "moods.yaml"

    no_op = [ResearchMood(name="dreamy", words=["floaty"], recipes=["color_morph"], palette=["lavender"])]
    added, extended, dropped = merge_moods(no_op, moods_path, cfg)
    assert added == [] and extended == [] and dropped == []
    assert not list(kdir.glob("moods.yaml.bak-*"))

    real_change = [ResearchMood(name="dreamy", words=["brand-new-word"])]
    added, extended, dropped = merge_moods(real_change, moods_path, cfg)
    assert extended == ["dreamy"]
    backups = list(kdir.glob("moods.yaml.bak-*"))
    assert len(backups) == 1
    # the backup holds the pre-change content
    assert "brand-new-word" not in backups[0].read_text(encoding="utf-8")


def test_merge_moods_normalizes_the_mood_name(cfg, kdir):
    moods_path = kdir / "moods.yaml"
    added, _, _ = merge_moods([ResearchMood(name="  Neon Rush  ")], moods_path, cfg)
    assert added == ["neon_rush"]
    assert "neon_rush" in yaml.safe_load(moods_path.read_text(encoding="utf-8"))["moods"]


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------

def test_render_report_numbers_sources_per_finding():
    from lightai.design.research import ResearchResult

    result = ResearchResult.model_validate(CANNED)
    text = render_report(result, ["afro_groove"], [], ["mood 'afro_groove': unknown recipe(s) not_a_recipe"])
    assert "### 1. Warm palette dominates" in text
    assert "1. <https://example.com/a>" in text and "2. <https://example.com/b>" in text
    assert "- added: afro_groove" in text
    assert "- dropped: mood 'afro_groove': unknown recipe(s) not_a_recipe" in text


# ---------------------------------------------------------------------------
# _rig_summary duck-typing (no real Rig needed)
# ---------------------------------------------------------------------------

def test_rig_summary_empty_without_get_rig():
    assert research_mod._rig_summary(SimpleNamespace()) == ""


def test_rig_summary_empty_when_get_rig_raises():
    bad = SimpleNamespace(get_rig=lambda: (_ for _ in ()).throw(RuntimeError("no rig loaded")))
    assert research_mod._rig_summary(bad) == ""


def test_rig_summary_delegates_to_prompt_module(monkeypatch):
    monkeypatch.setattr("lightai.design.prompt.rig_summary", lambda rig: f"summary-for-{rig}")
    ai = SimpleNamespace(get_rig=lambda: "RIG42")
    assert research_mod._rig_summary(ai) == "summary-for-RIG42"


# ---------------------------------------------------------------------------
# CLI wiring (argparse only; research_main itself needs the real backend)
# ---------------------------------------------------------------------------

def test_cli_parser_has_research_subcommand():
    from lightai.__main__ import build_parser

    args = build_parser().parse_args(["research", "some topic", "--deep"])
    assert args.cmd == "research" and args.topic == "some topic" and args.deep is True

    args = build_parser().parse_args(["research", "other topic"])
    assert args.deep is False
