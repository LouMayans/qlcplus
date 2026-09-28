"""Taste: what the operator accepts. Running defaults per recipe + a small acceptance classifier.

State lives in %LIGHTAI_DATA%\\prefs\\ (state.json, model.pkl); every outcome is appended to
%LIGHTAI_DATA%\\feedback.jsonl so the classifier can be retrained at any time.
"""

from __future__ import annotations

import json
import pickle
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lightai.config import Config

RATE_ORDER = ["very_slow", "slow", "medium", "fast", "very_fast"]
SIZE_ORDER = ["tiny", "small", "medium", "big", "huge"]
KIND_RECIPES = {
    "spot": ["circle_wave", "ballyhoo", "sweep", "figure8", "circle", "color_chase", "breathe", "strobe"],
    "wash": ["breathe", "circle", "color_chase", "sweep", "color_wash", "circle_wave"],
    "par": ["breathe", "color_chase", "color_wash", "strobe"],
    "bar": ["color_chase", "breathe", "strobe", "color_wash"],
    "rgb": ["breathe", "color_chase", "color_wash"],
    "dimmer": ["breathe", "color_wash"],
    "fx": ["strobe"],
    "fog": [],
    "other": ["color_wash"],
}
DEFAULT_PALETTE = ["blue", "pink", "white", "red", "purple"]


def _shift(order: list, cur: str, step: int) -> str:
    i = order.index(cur) if cur in order else order.index("medium")
    return order[max(0, min(len(order) - 1, i + step))]


class Prefs:
    def __init__(self, cfg: Config, overrides: Optional[dict] = None) -> None:
        self.cfg = cfg
        self.dir = cfg.data_dir / "prefs"
        self.state_path = self.dir / "state.json"
        self.model_path = self.dir / "model.pkl"
        self.feedback_path = cfg.data_dir / "feedback.jsonl"
        self.state = {"recipe_defaults": {}, "recipe_scores": {}, "palette_scores": {}, "recent": []}
        if self.state_path.exists():
            self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
        self.palette = list((overrides or {}).get("house_palette") or DEFAULT_PALETTE)
        self.model = None
        if self.model_path.exists():
            try:
                self.model = pickle.loads(self.model_path.read_bytes())
            except Exception:
                self.model = None

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def defaults_for(self, recipe: str) -> dict:
        d = {"rate_word": "medium", "size": None, "intensity": 1.0}
        d.update(self.state["recipe_defaults"].get(recipe, {}))
        return d

    def house_palette(self) -> list:
        scores = self.state["palette_scores"]
        return sorted(self.palette, key=lambda c: -scores.get(c, 0.0))

    def features(self, recipe: str, kind: str, color: str, rate_word: str, size: Optional[str], hour: Optional[int] = None) -> dict:
        hour = datetime.now().hour if hour is None else hour
        slot = "early" if 18 <= hour <= 22 else ("peak" if hour >= 23 or hour <= 2 else "late" if hour <= 6 else "day")
        return {f"recipe={recipe}": 1, f"kind={kind}": 1, f"color={color}": 1, f"rate={rate_word}": 1,
                f"size={size or 'na'}": 1, f"when={slot}": 1, f"kind_recipe={kind}:{recipe}": 1}

    def accept_prob(self, feats: dict) -> Optional[float]:
        if self.model is None:
            return None
        vec, clf = self.model
        return float(clf.predict_proba(vec.transform([feats]))[0][1])

    def rank_recipes(self, kind: str) -> list:
        base = KIND_RECIPES.get(kind, ["color_wash"])
        scores = self.state["recipe_scores"].get(kind, {})
        return sorted(base, key=lambda r: -(scores.get(r, 0.0) + (len(base) - base.index(r)) * 0.3))

    def propose(self, kind: str, avoid_recent: int = 3, explore: float = 0.15, rnd: Optional[random.Random] = None) -> dict:
        rnd = rnd or random.Random()
        recent = [r["recipe"] for r in self.state["recent"][-avoid_recent:]]
        recent_colors = [r["color"] for r in self.state["recent"][-avoid_recent:]]
        ranked = self.rank_recipes(kind) or ["color_wash"]
        fresh = [r for r in ranked if r not in recent] or ranked
        recipe = rnd.choice(fresh[:3]) if rnd.random() < explore else fresh[0]
        palette = self.house_palette()
        colors = [c for c in palette if c not in recent_colors] or palette
        color = colors[0]
        d = self.defaults_for(recipe)
        reason = [f"'{recipe}' ranks highest for {kind} fixtures you have not seen in the last {avoid_recent} proposals"]
        feats = self.features(recipe, kind, color, d["rate_word"], d["size"])
        p = self.accept_prob(feats)
        if p is not None:
            reason.append(f"acceptance model predicts {p:.0%}")
        return {"recipe": recipe, "color": color, "rate_word": d["rate_word"], "size": d["size"], "intensity": d["intensity"], "reason": "; ".join(reason)}

    def remember(self, recipe: str, color: str) -> None:
        self.state["recent"].append({"recipe": recipe, "color": color, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self.state["recent"] = self.state["recent"][-50:]
        self.save()

    def learn(self, look: dict, issues: list, verdict: str) -> list:
        """Update defaults and scores from one outcome; returns human-readable changes."""
        changes = []
        recipe = look.get("recipe")
        params = look.get("params", {})
        kind = look.get("kind", "other")
        color = (params.get("colors") or ["white"])[0]
        d = self.state["recipe_defaults"].setdefault(recipe, {}) if recipe else {}
        scores = self.state["recipe_scores"].setdefault(kind, {})
        if verdict == "ok" and recipe:
            scores[recipe] = scores.get(recipe, 0.0) + 1.0
            self.state["palette_scores"][color] = self.state["palette_scores"].get(color, 0.0) + 0.5
            changes.append(f"'{recipe}' on {kind} fixtures +1 (accepted)")
        elif verdict == "wrong" and recipe and not issues:
            scores[recipe] = scores.get(recipe, 0.0) - 0.5
            changes.append(f"'{recipe}' on {kind} fixtures -0.5 (rejected)")
        for iss in issues:
            k, dirn = iss.get("kind"), iss.get("direction")
            if not recipe:
                continue
            if k == "speed":
                if dirn not in ("too_fast", "too_slow"):
                    continue  # no direction = nothing to learn (never guess the opposite)
                cur = params.get("rate_word") or d.get("rate_word") or "medium"
                new = _shift(RATE_ORDER, cur, -1 if dirn == "too_fast" else 1)
                d["rate_word"] = new
                if new != cur:
                    changes.append(f"default speed for '{recipe}': {cur} -> {new}")
            elif k == "intensity":
                if dirn not in ("too_dim", "too_bright"):
                    continue
                cur = float(params.get("intensity") or d.get("intensity") or 1.0)
                new = round(max(0.1, min(1.0, cur + (0.2 if dirn == "too_dim" else -0.2))), 2)
                d["intensity"] = new
                if new != cur:
                    changes.append(f"default intensity for '{recipe}': {cur:.0%} -> {new:.0%}")
            elif k == "size":
                if dirn not in ("too_small", "too_big"):
                    continue
                cur = params.get("size") or d.get("size") or "medium"
                new = _shift(SIZE_ORDER, cur, 1 if dirn == "too_small" else -1)
                d["size"] = new
                if new != cur:
                    changes.append(f"default size for '{recipe}': {cur} -> {new}")
        self.save()
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "verdict": verdict, "issues": issues,
               "recipe": recipe, "kind": kind, "color": color, "rate_word": params.get("rate_word"), "size": params.get("size")}
        self.feedback_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.feedback_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return changes

    def train(self) -> dict:
        """Fit the acceptance classifier on feedback.jsonl (needs both outcomes present)."""
        if not self.feedback_path.exists():
            return {"trained": False, "reason": "no feedback yet"}
        rows = [json.loads(l) for l in self.feedback_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows = [r for r in rows if r.get("recipe") and r.get("verdict") in ("ok", "wrong")]
        ys = [1 if r["verdict"] == "ok" else 0 for r in rows]
        if len(rows) < 8 or len(set(ys)) < 2:
            return {"trained": False, "reason": f"need 8+ outcomes with both accepts and rejects (have {len(rows)})"}
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.linear_model import LogisticRegression

        feats = [self.features(r["recipe"], r.get("kind", "other"), r.get("color") or "white", r.get("rate_word") or "medium", r.get("size"), 0) for r in rows]
        vec = DictVectorizer()
        X = vec.fit_transform(feats)
        clf = LogisticRegression(max_iter=500, C=1.0).fit(X, ys)
        self.model = (vec, clf)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.model_path.write_bytes(pickle.dumps(self.model))
        return {"trained": True, "rows": len(rows), "accept_rate": round(sum(ys) / len(ys), 3)}
