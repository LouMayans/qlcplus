"""The promotion gate compares a candidate with the current model on the same data (iteration 10)."""

import json
from pathlib import Path

from lightai.train import evaluate as ev


class Cfg:
    def __init__(self, models: Path):
        self.models_dir = models

    def current_model_dir(self):
        return self.models_dir / "v7-x"


def make(models: Path, name: str, metrics: dict) -> Path:
    d = models / name
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return d


def report(**kw) -> dict:
    return dict({"dev_intent_acc": 0.996, "dev_slot_f1": 0.972, "golden_pass": 58, "pass": True, "gates": {}}, **kw)


def scored(slot_f1: float):
    return lambda d, rig=None: {"model": d.name, "dev_intent_acc": 0.996, "dev_slot_f1": slot_f1, "golden_pass": 58}


def test_candidate_is_compared_on_the_same_data(tmp_path, monkeypatch):
    models = tmp_path / "models"
    make(models, "v7-x", {"model": "v7-x", "dev_intent_acc": 0.997, "dev_slot_f1": 0.99, "golden_pass": 58})  # an older split
    cand = make(models, "v8-x", {})
    monkeypatch.setattr(ev, "load_config", lambda: Cfg(models))
    monkeypatch.setattr(ev, "evaluate_model_dir", scored(0.968))  # v7 on today's data
    ok, why = ev.promote(cand, report())
    assert ok, why
    assert (models / "current.txt").read_text(encoding="utf-8") == "v8-x"


def test_a_real_regression_still_blocks(tmp_path, monkeypatch):
    models = tmp_path / "models"
    make(models, "v7-x", {})
    cand = make(models, "v8-x", {})
    monkeypatch.setattr(ev, "load_config", lambda: Cfg(models))
    monkeypatch.setattr(ev, "evaluate_model_dir", scored(0.99))
    ok, why = ev.promote(cand, report(dev_slot_f1=0.97))
    assert not ok and "dev_slot_f1 regressed" in why and "same data" in why
    assert not (models / "current.txt").exists()


def test_saved_metrics_are_the_fallback(tmp_path, monkeypatch):
    models = tmp_path / "models"
    make(models, "v7-x", {"model": "v7-x", "dev_intent_acc": 0.997, "dev_slot_f1": 0.99, "golden_pass": 58})
    cand = make(models, "v8-x", {})
    monkeypatch.setattr(ev, "load_config", lambda: Cfg(models))

    def broken(d, rig=None):
        raise OSError("no model")

    monkeypatch.setattr(ev, "evaluate_model_dir", broken)
    ok, why = ev.promote(cand, report(dev_slot_f1=0.97))
    assert not ok and "saved metrics" in why
