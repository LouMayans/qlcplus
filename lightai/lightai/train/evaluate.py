"""Evaluate an exported model (INT8 ONNX, the one that will actually run) and gate promotion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml

from lightai.config import LIGHTAI_DIR, load_config
from lightai.nlu.infer import NluModel
from lightai.nlu.pipeline import Parser, projection
from lightai.nlu.retriever import Retriever
from lightai.rig.model import Rig
from lightai.train.generate import build_dataset, split
from lightai.train.trainer import span_set

GOLDEN = LIGHTAI_DIR / "data" / "golden" / "golden.yaml"
ABSTAIN = LIGHTAI_DIR / "data" / "golden" / "abstain.txt"
GATES = {"dev_intent_acc": 0.97, "dev_slot_f1": 0.93, "golden": 1.0, "abstain": 1.0}


def expected_projection(item: dict, rig: Rig) -> dict:
    exp = dict(item.get("expect") or {})
    want: dict = {"intent": item["intent"]}
    if "target_zone" in exp:
        want["target"] = sorted(rig.zones[exp.pop("target_zone")])
    if "target" in exp:
        want["target"] = sorted(exp.pop("target"))
    if exp.pop("target_unresolved", False):
        want["target"] = []
    want.update(exp)
    want.setdefault("clarify", False)
    return want


def compare(want: dict, got: dict) -> list:
    diffs = []
    for k in sorted(set(want) | set(got)):
        w, g = want.get(k, "<absent>"), got.get(k, "<absent>")
        if isinstance(w, float) and isinstance(g, (int, float)):
            if abs(w - g) > 0.011:
                diffs.append(f"{k}: want {w} got {g}")
        elif k == "function_ref" and isinstance(w, str) and isinstance(g, str):
            if w.lower() != g.lower():
                diffs.append(f"{k}: want {w!r} got {g!r}")
        elif k == "name" and isinstance(w, str) and isinstance(g, str):
            if w.lower() != g.lower():
                diffs.append(f"{k}: want {w!r} got {g!r}")
        elif w != g:
            diffs.append(f"{k}: want {w} got {g}")
    return diffs


def evaluate_model_dir(model_dir: Path, rig: Optional[Rig] = None) -> dict:
    cfg = load_config()
    rig = rig or Rig.load(cfg)
    model = NluModel(model_dir, threads=cfg.ort_threads)
    parser = Parser(rig, model, Retriever(rig.functions))
    rows = build_dataset(rig=rig)
    _, dev = split(rows)
    correct = tp = fp = fn = 0
    for r in dev:
        pred = model.predict(r["text"])
        correct += int(pred["intent"] == r["intent"])
        if len(pred["words"]) == len(r["words"]):
            g, p = span_set(r["tags"]), span_set(pred["tags"])
        else:
            g, p = span_set(r["tags"]), set()
        tp += len(g & p)
        fp += len(p - g)
        fn += len(g - p)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0

    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    from lightai.planner import Planner
    from lightai.prefs import Prefs
    from lightai.session import Session

    planner = Planner(rig, Prefs(cfg, rig.overrides), Session(), cfg)
    failures = []
    passed = 0
    lat = []
    for item in golden:
        cmd = parser.parse(item["text"])
        lat.append(cmd.latency_ms)
        want = expected_projection(item, rig)
        got = projection(cmd)
        diffs = compare(want, got)
        if not diffs and not want.get("clarify") and cmd.intent != "none":
            try:  # the gate also proves each golden sentence turns into a runnable plan
                plan = planner.plan(cmd)
                if plan.mode == "clarify" and item.get("plan") != "refuse":
                    diffs = [f"plan: asks '{plan.summary}' instead of planning"]
                elif plan.mode != "clarify" and item.get("plan") == "refuse":
                    diffs = [f"plan: should refuse, planned '{plan.summary}'"]
            except Exception as exc:
                diffs = [f"plan: {type(exc).__name__}: {exc}"]
        if diffs:
            failures.append({"set": "golden", "text": item["text"], "diffs": diffs, "spans": [(s.slot, s.text) for s in cmd.spans], "intent": cmd.intent, "conf": cmd.confidence})
        else:
            passed += 1
    abstain_lines = [l.strip() for l in ABSTAIN.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    a_pass = 0
    for text in abstain_lines:
        cmd = parser.parse(text)
        if cmd.intent == "none" or cmd.clarify:
            a_pass += 1
        else:
            failures.append({"set": "abstain", "text": text, "intent": cmd.intent, "conf": cmd.confidence})
    lat.sort()
    report = {
        "model": model.version,
        "dev_intent_acc": round(correct / max(1, len(dev)), 4),
        "dev_slot_f1": round(f1, 4),
        "dev_n": len(dev),
        "golden_pass": passed,
        "golden_total": len(golden),
        "abstain_pass": a_pass,
        "abstain_total": len(abstain_lines),
        "parse_p50_ms": round(lat[len(lat) // 2], 2) if lat else None,
        "parse_p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2) if lat else None,
        "failures": failures,
    }
    report["gates"] = {
        "dev_intent_acc": report["dev_intent_acc"] >= GATES["dev_intent_acc"],
        "dev_slot_f1": report["dev_slot_f1"] >= GATES["dev_slot_f1"],
        "golden": passed == len(golden),
        "abstain": a_pass == len(abstain_lines),
    }
    report["pass"] = all(report["gates"].values())
    return report


def promote(model_dir: Path, report: dict, force: bool = False) -> tuple:
    cfg = load_config()
    if not force and not report.get("pass"):
        failed = [k for k, v in report.get("gates", {}).items() if not v]
        return False, f"gates failed: {failed}"
    current = cfg.current_model_dir()
    if current and not force:
        mfile = current / "metrics.json"
        if mfile.exists():
            old = json.loads(mfile.read_text(encoding="utf-8"))
            for key in ("dev_intent_acc", "dev_slot_f1"):
                if report[key] + 0.005 < old.get(key, 0):
                    return False, f"{key} regressed: {report[key]} < {old.get(key)}"
            if report["golden_pass"] < old.get("golden_pass", 0):
                return False, "golden suite regressed"
    cfg.models_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.models_dir / "current.txt.tmp"  # the server polls current.txt: never let it read a half-written name
    tmp.write_text(model_dir.name, encoding="utf-8")
    tmp.replace(cfg.models_dir / "current.txt")
    return True, f"current model is now {model_dir.name}"
