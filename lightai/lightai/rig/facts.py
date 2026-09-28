"""Learned operator facts (lightai/rig/learned.yaml), merged on top of overrides.yaml.

overrides.yaml is hand-edited and keeps its comments; the feedback router only ever writes
learned.yaml. Every fact carries source, date and the plan it came from, and can be undone.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from lightai.config import PACKAGE_DIR

LEARNED_PATH = PACKAGE_DIR / "rig" / "learned.yaml"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif k == "facts" and isinstance(v, list):
            out[k] = list(out.get(k) or []) + v
        else:
            out[k] = copy.deepcopy(v)
    return out


class LearnedFacts:
    def __init__(self, path: Path = LEARNED_PATH) -> None:
        self.path = path
        self.data: dict = {"version": 1, "models": {}, "fixtures": {}, "facts": []}
        if path.exists():
            self.data.update(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        for key in ("models", "fixtures"):
            self.data[key] = self.data.get(key) or {}
        self.data["facts"] = self.data.get("facts") or []

    def overlay(self) -> dict:
        return {k: v for k, v in self.data.items() if k in ("models", "fixtures", "facts", "zones", "fixture_number_means", "channel_numbering")}

    def _log(self, kind: str, subject: str, detail: dict, source: str, plan_id: Optional[str]) -> dict:
        fact = {"id": len(self.data["facts"]) + 1, "kind": kind, "subject": subject, "detail": detail,
                "source": source, "date": now(), "plan_id": plan_id, "active": True}
        self.data["facts"].append(fact)
        return fact

    def set_wheel(self, model_key: str, color: str, value: int, source: str = "operator", plan_id: Optional[str] = None) -> dict:
        m = self.data["models"].setdefault(model_key, {})
        m.setdefault("wheel", {})[color] = {"value": int(value), "source": source, "date": now()}
        nots = (m.get("wheel_not") or {}).get(color) or []
        if int(value) in nots:
            nots.remove(int(value))
        return self._log("wheel", model_key, {"color": color, "value": int(value)}, source, plan_id)

    def mark_wheel_bad(self, model_key: str, value: int, observed: str, expected: str, source: str = "operator", plan_id: Optional[str] = None) -> dict:
        m = self.data["models"].setdefault(model_key, {})
        nots = m.setdefault("wheel_not", {}).setdefault(expected, [])
        if int(value) not in nots:
            nots.append(int(value))
        wheel = m.get("wheel") or {}
        for c, f in list(wheel.items()):
            if (f.get("value") if isinstance(f, dict) else f) == int(value) and c == expected:
                wheel.pop(c)
        if observed:
            m.setdefault("wheel_observed", {})[int(value)] = observed
        return self._log("wheel_bad", model_key, {"value": int(value), "observed": observed, "expected": expected}, source, plan_id)

    def set_role(self, scope: str, key, role: str, channel: int, source: str = "operator", plan_id: Optional[str] = None) -> dict:
        bucket = self.data["models" if scope == "model" else "fixtures"].setdefault(key, {})
        bucket.setdefault("roles", {})[role] = {"channel": int(channel), "source": source, "date": now()}
        return self._log("role", str(key), {"scope": scope, "role": role, "channel": int(channel)}, source, plan_id)

    def set_fixture_model_hint(self, fixture_id: int, model_key: str, plan_id: Optional[str] = None) -> dict:
        self.data["fixtures"].setdefault(fixture_id, {})["model_hint"] = {"model": model_key, "date": now()}
        return self._log("model_hint", str(fixture_id), {"model": model_key}, "operator", plan_id)

    def set_model_value(self, model_key: str, key: str, value, plan_id: Optional[str] = None) -> dict:
        self.data["models"].setdefault(model_key, {})[key] = {"value": value, "source": "operator", "date": now()}
        return self._log(key, model_key, {"value": value}, "operator", plan_id)

    def add_alias(self, phrase: str, fixture_ids: list, plan_id: Optional[str] = None) -> dict:
        zones = self.data.setdefault("zones", {})
        name = "alias_" + "".join(c if c.isalnum() else "_" for c in phrase.lower()).strip("_")
        zones[name] = {"ids": list(fixture_ids), "aliases": [phrase.lower()]}
        return self._log("alias", phrase, {"fixture_ids": list(fixture_ids)}, "operator", plan_id)

    def undo(self, fact_id: int) -> Optional[dict]:
        for f in self.data["facts"]:
            if f["id"] == fact_id and f.get("active"):
                f["active"] = False
                d = f["detail"]
                if f["kind"] == "wheel":
                    (self.data["models"].get(f["subject"], {}).get("wheel") or {}).pop(d["color"], None)
                elif f["kind"] == "wheel_bad":
                    nots = (self.data["models"].get(f["subject"], {}).get("wheel_not") or {}).get(d["expected"]) or []
                    if d["value"] in nots:
                        nots.remove(d["value"])
                elif f["kind"] == "role":
                    scope = "models" if d["scope"] == "model" else "fixtures"
                    key = f["subject"] if scope == "models" else int(f["subject"])
                    (self.data[scope].get(key, {}).get("roles") or {}).pop(d["role"], None)
                elif f["kind"] == "alias":
                    name = "alias_" + "".join(c if c.isalnum() else "_" for c in f["subject"].lower()).strip("_")
                    (self.data.get("zones") or {}).pop(name, None)
                return f
        return None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = "# Written by lightai's feedback router. Undo a fact with `lightai facts undo <id>`.\n"
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(header + yaml.safe_dump(self.data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        tmp.replace(self.path)
