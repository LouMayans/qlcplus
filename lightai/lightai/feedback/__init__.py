"""Feedback router: turn "that was pink not blue" into the right kind of learning.

  color / model / didn't-move / dark  -> rig facts (lightai/rig/learned.yaml), instant
  speed / intensity / size / verdict  -> taste (Prefs), instant
  meaning ("that's not what I meant") -> language corrections queue (corrections.jsonl)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

from lightai.config import Config
from lightai.prefs import Prefs
from lightai.rig.facts import LearnedFacts
from lightai.rig.model import Rig
from lightai.schema import Issue, LightCommand, OutcomeFeedback, Plan

OK_RE = re.compile(r"\b(perfect|right|good|nailed|love|great|correct|yes|yeah|yep|awesome|spot on|works|keep it|nice|exactly|sick|fire|not bad)\b")
WRONG_RE = re.compile(r"\b(no|nope|wrong|not quite|off|bad|not it|not what|misunderstood|nah)\b")
PRIMARIES = ("red", "green", "blue")


def classify_text(text: str) -> dict:
    t = text.lower()
    out: dict = {}
    if re.search(r"too (fast|quick)|slow (it )?down|slower|calm it down|less (busy|frantic)", t):
        out["speed"] = "too_fast"
    elif re.search(r"too slow|speed (it )?up|faster|quicker|more energy|pick it up", t):
        out["speed"] = "too_slow"
    if re.search(r"too (dim|dark)|brighter|not bright enough|more light|can'?t see", t):
        out["intensity"] = "too_dim"
    elif re.search(r"too bright|dimmer|less light|tone it down|blinding", t):
        out["intensity"] = "too_bright"
    if re.search(r"too small|bigger|wider|larger|more movement", t):
        out["size"] = "too_small"
    elif re.search(r"too big|smaller|tighter|narrower|less movement", t):
        out["size"] = "too_big"
    if re.search(r"didn'?t move|not moving|no movement|nothing moved|isn'?t moving|stayed still", t):
        out["movement"] = True
    if re.search(r"nothing happened|stayed dark|no light|went dark|stayed (off|black)|nothing lit", t):
        out["dark"] = True
    if re.search(r"misunderstood|(not|isn'?t) what i (meant|asked|said)|that'?s not what|wrong command", t):
        out["meaning"] = True
    if re.search(r"wrong fixture|wrong light|moved instead|wrong one", t):
        out["fixture"] = True
    return out


class FeedbackRouter:
    def __init__(self, rig: Rig, prefs: Prefs, cfg: Config) -> None:
        self.rig = rig
        self.prefs = prefs
        self.cfg = cfg

    def from_command(self, cmd: LightCommand, plan: Optional[Plan]) -> OutcomeFeedback:
        t = cmd.text.lower()
        cls = classify_text(t)
        issues: list = []
        look = (plan.look if plan else None) or {}
        look_fixtures = (look.get("params") or {}).get("targets") or []
        tgt = [i for sv in cmd.slots.get("target") or [] for i in sv.value.get("fixture_ids", [])]
        observed = [sv.value for sv in cmd.slots.get("observed") or []]
        expected_colors = [sv.value.get("name") for sv in cmd.slots.get("color") or [] if sv.value.get("name")]
        obs_color = next((o.get("color") for o in observed if o.get("color")), None)
        if obs_color:
            exp = expected_colors[0] if expected_colors else ((look.get("params") or {}).get("colors") or [None])[0]
        if obs_color and exp == obs_color:
            obs_color = None  # 'yellow looked yellow' teaches nothing (and must never become an operator fact)
        if obs_color:
            fixtures = tgt or look_fixtures
            issues.append(Issue(kind="color", fixture_id=fixtures[0] if len(fixtures) == 1 else None, expected=exp, observed=obs_color))
        if cmd.slots.get("fixture_model") and tgt:
            model = cmd.slots["fixture_model"][0].value.get("model")
            issues.append(Issue(kind="model", fixture_id=tgt[0], observed=model))
        obs_fx = next((o.get("fixture_ids") for o in observed if o.get("fixture_ids")), None)
        if cls.get("fixture") or obs_fx:
            issues.append(Issue(kind="fixture", fixture_id=(obs_fx or [None])[0], expected=",".join(map(str, tgt)) if tgt else None,
                                observed=",".join(map(str, obs_fx)) if obs_fx else None))
        if cls.get("movement"):
            issues.append(Issue(kind="movement"))
        if cls.get("dark") or any(o.get("nothing") for o in observed):
            issues.append(Issue(kind="dark"))
        for k in ("speed", "intensity", "size"):
            if cls.get(k):
                issues.append(Issue(kind=k, direction=cls[k]))
        if cls.get("meaning"):
            issues.append(Issue(kind="meaning"))
        if issues:
            verdict = "wrong" if any(i.kind in ("color", "model", "fixture", "movement", "dark", "meaning") for i in issues) else "adjust"
        elif OK_RE.search(t) and not WRONG_RE.search(t.replace("not bad", "")):
            verdict = "ok"
        else:
            verdict = "wrong"
        return OutcomeFeedback(plan_id=plan.plan_id if plan else "", verdict=verdict, issues=issues, free_text=cmd.text)

    def apply(self, fb: OutcomeFeedback, plan: Optional[Plan], source_text: str = "") -> dict:
        facts = LearnedFacts(self.cfg.learned_path)
        changes: list = []
        routed: set = set()
        followups: list = []
        look = (plan.look if plan else None) or {}
        params = look.get("params") or {}
        look_fx = [self.rig.fixtures[i] for i in params.get("targets", []) if i in self.rig.fixtures]
        for iss in fb.issues:
            if iss.kind == "color":
                seen_c = self.rig.colors.canonical(iss.observed or "")
                if seen_c and seen_c == (self.rig.colors.canonical(iss.expected or "") or iss.expected):
                    changes.append(f"you saw {seen_c}, which is what was asked for; nothing to learn")  # never an operator fact
                    continue
                routed.add("rig_facts")
                fxs = [self.rig.fixtures[iss.fixture_id]] if iss.fixture_id in self.rig.fixtures else look_fx
                wheel_models = sorted({fx.key for fx in fxs if fx.color_mode == "wheel"})
                if iss.fixture_id not in self.rig.fixtures and len(wheel_models) > 1:
                    # one complaint cannot tell which model showed the wrong color; changing all of them teaches wrong facts
                    changes.append(f"this look mixes {len(wheel_models)} color-wheel models ({', '.join(wheel_models)}); "
                                   "no wheel fact was changed")
                    followups.append(f"say which fixture looked {iss.observed or 'wrong'}, e.g. 'fixture {fxs[0].id} was "
                                     f"{iss.observed or 'pink'}', or 'find {iss.expected or 'blue'} on fixture {fxs[0].id}'")
                    continue
                done_models = set()
                for fx in fxs:
                    if fx.key in done_models or not iss.expected:
                        continue
                    done_models.add(fx.key)
                    if fx.color_mode == "wheel":
                        w = self.rig.wheel_value(fx, iss.expected)
                        if w["value"] is None:
                            continue
                        facts.mark_wheel_bad(fx.key, w["value"], iss.observed or "", iss.expected, plan_id=fb.plan_id)
                        changes.append(f"{fx.key}: wheel value {w['value']} is not {iss.expected} (you saw {iss.observed})")
                        if iss.observed and self.rig.colors.canonical(iss.observed):
                            facts.set_wheel(fx.key, self.rig.colors.canonical(iss.observed), w["value"], plan_id=fb.plan_id)
                            changes.append(f"{fx.key}: wheel value {w['value']} = {iss.observed}")
                        followups.append(f"find {iss.expected} on fixture {fx.id}")
                        from lightai.compiler.sidecar import Sidecar

                        stale = Sidecar(self.cfg.sidecar_path).using_fact(f"wheel:{fx.key}:{iss.expected}")
                        if stale:
                            followups.append(f"{len(stale)} saved look(s) still use value {w['value']} for {iss.expected}: "
                                             + ", ".join(e["name"] for e in stale[:5]) + " (update them to apply the fix)")
                    elif fx.color_mode == "rgb" and iss.observed in PRIMARIES and iss.expected in PRIMARIES and iss.observed != iss.expected:
                        a, b = fx.roles.get(iss.expected), fx.roles.get(iss.observed)
                        if a is not None and b is not None:
                            facts.set_role("model", fx.key, iss.expected, b, source="feedback", plan_id=fb.plan_id)
                            facts.set_role("model", fx.key, iss.observed, a, source="feedback", plan_id=fb.plan_id)
                            changes.append(f"{fx.key}: swapped {iss.expected}/{iss.observed} channels ({a} <-> {b})")
                    else:
                        changes.append(f"{fx.key}: noted that {iss.expected} looked {iss.observed}; adjust data/colors.yaml or calibrate")
                        followups.append(f"calibrate fixture {fx.id}")
            elif iss.kind == "model" and iss.fixture_id is not None and iss.observed:
                routed.add("rig_facts")
                facts.set_fixture_model_hint(iss.fixture_id, iss.observed, plan_id=fb.plan_id)
                cur = self.rig.fixtures[iss.fixture_id].key if iss.fixture_id in self.rig.fixtures else "?"
                changes.append(f"fixture {iss.fixture_id} is really a {iss.observed} (patched as {cur})")
                if cur != iss.observed:
                    followups.append(f"re-patch fixture {iss.fixture_id} as {iss.observed} in QLC+ (Fixture Manager), then reload")
            elif iss.kind == "fixture":
                routed.add("rig_facts")
                changes.append(f"wrong fixture reported (expected {iss.expected or '?'}, saw {iss.observed or '?'}); logged for the audit")
                facts._log("fixture_mismatch", str(iss.expected or ""), {"observed": iss.observed, "text": fb.free_text}, "operator", fb.plan_id)
                if iss.observed:
                    followups.append(f"check the DMX address of fixture(s) {iss.expected or 'in the last plan'}; fixture(s) {iss.observed} lit instead")
            elif iss.kind in ("movement", "dark"):
                routed.add("rig_facts")
                facts._log(iss.kind, ",".join(str(f.id) for f in look_fx), {"text": fb.free_text}, "operator", fb.plan_id)
                for fx in look_fx[:1]:
                    followups.append(f"calibrate fixture {fx.id}")
                changes.append("logged: " + ("fixtures did not move" if iss.kind == "movement" else "fixtures stayed dark"))
            elif iss.kind == "meaning":
                routed.add("language")
                self.queue_correction(source_text or fb.free_text, plan)
                changes.append("queued the last command for relabeling in teach mode")
        taste_issues = [i.model_dump() for i in fb.issues if i.kind in ("speed", "intensity", "size")]
        rig_issue = any(i.kind in ("color", "model", "fixture", "movement", "dark") for i in fb.issues)
        if look and (taste_issues or fb.verdict == "ok" or (fb.verdict == "wrong" and not rig_issue)):
            kinds = [f.kind for f in look_fx]
            look_ctx = dict(look)
            look_ctx["kind"] = max(set(kinds), key=kinds.count) if kinds else "other"
            changes += self.prefs.learn(look_ctx, taste_issues, fb.verdict if fb.verdict != "adjust" else "adjust")
            routed.add("taste")
        if fb.verdict == "ok" and plan is not None:
            self.confirm_language(plan)
            routed.add("language")
        facts.save()
        return {"verdict": fb.verdict, "routed_to": sorted(routed), "changes": changes, "followups": followups,
                "feedback": fb.model_dump()}

    def queue_correction(self, text: str, plan: Optional[Plan]) -> None:
        from lightai.schema import labels

        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "labels_version": int(labels()["version"]), "text": text,
               "predicted": (plan.model_dump().get("look") if plan else None), "corrected": None, "accepted": False,
               "needs_label": True, "source": "feedback"}
        self._append(row)

    def confirm_language(self, plan: Plan) -> None:
        cmd = (plan.followup or {}).get("command")
        if not cmd:
            return
        from lightai.nlu.text import parse_markup, to_markup

        words = cmd.get("words") or []
        tags = ["O"] * len(words)
        for sp in cmd.get("spans", []):
            for i in range(sp["start"], sp["end"]):
                tags[i] = ("B-" if i == sp["start"] else "I-") + sp["slot"]
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "labels_version": cmd.get("labels_version", 1),
               "text": cmd.get("text"), "predicted": {"intent": cmd.get("intent"), "marked": to_markup(words, tags)},
               "corrected": {"intent": cmd.get("intent"), "marked": to_markup(words, tags)}, "accepted": True, "source": "confirmed_outcome"}
        try:  # text that already contains brackets ('[color:blue] wash') cannot round-trip; such a row would only be skipped later
            if parse_markup(row["corrected"]["marked"])[1] != words:
                return
        except ValueError:
            return
        self._append(row)

    def _append(self, row: dict) -> None:
        path = self.cfg.data_dir / "corrections.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
