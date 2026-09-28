"""LightCommand (+ session) -> Plan: the exact actions an executor will run, with assumptions."""

from __future__ import annotations

import re
from typing import Optional

from lightai.compiler import CompileError, compile_look
from lightai.compiler.preview import compress, simulate
from lightai.compiler.recipes import DEFAULT_BPM, RECIPES
from lightai.compiler.spec import LookParams
from lightai.config import Config
from lightai.feedback import FeedbackRouter
from lightai.nlu.pipeline import merged_rate, primary_movement, target_ids
from lightai.prefs import Prefs
from lightai.rig.model import Rig
from lightai.schema import Action, LightCommand, Plan
from lightai.session import Calibration, Session

MOVE_RECIPES = {"circle", "circle_wave", "ballyhoo", "figure8", "sweep"}
ROLE_LABELS = ["dimmer", "shutter", "color_wheel", "pan", "tilt", "pt_speed", "red", "green", "blue", "white", "amber"]


class Planner:
    def __init__(self, rig: Rig, prefs: Prefs, session: Session, cfg: Config) -> None:
        self.rig = rig
        self.prefs = prefs
        self.session = session
        self.cfg = cfg
        self.router = FeedbackRouter(rig, prefs, cfg)

    def wire(self, a: Action) -> list:
        """The exact QLC+ Web Access messages an external program can send for this action."""
        ab = lambda u, ch: (int(u) - 1) * 512 + int(ch)  # noqa: E731
        if a.op == "set_channels":
            return [f"CH|{ab(u, ch)}|{int(v)}" for u, ch, v in a.args["channels"]]
        if a.op == "reset_channels":
            return [f"QLC+API|sdResetChannel|{ab(u, ch)}" for u, ch in a.args["channels"]]
        if a.op == "reset_universes":
            return [f"QLC+API|sdResetUniverse|{int(u)}" for u in a.args["universes"]]
        if a.op == "grand_master":
            return [f"GM_VALUE|{int(a.args['value'])}"]
        if a.op == "function":
            return [f"QLC+API|setFunctionStatus|{int(a.args['id'])}|{1 if a.args['on'] else 0}"]
        if a.op == "reload":
            return [f"QLC+API|loadProjectFile|{self.cfg.project_path}", "(stock QLC+: POST the file to /loadProject)"]
        return []

    def _plan(self, intent: str, mode: str, summary: str, **kw) -> Plan:
        return Plan(plan_id=self.session.new_plan_id(), intent=intent, mode=mode, summary=summary, **kw)

    def plan(self, cmd: LightCommand) -> Plan:
        if cmd.clarify:
            p = self._plan(cmd.intent, "clarify", cmd.clarify, followup={"command": cmd.model_dump()})
            self.session.remember(cmd, p)
            return p
        handler = getattr(self, "_" + cmd.intent.replace(".", "_"), None)
        if handler is None:
            p = self._plan(cmd.intent, "clarify", f"I can't do '{cmd.intent}' yet.")
        else:
            try:
                p = handler(cmd)
            except (CompileError, ValueError, KeyError) as exc:
                p = self._plan(cmd.intent, "clarify", f"Can't build that: {exc}", warnings=getattr(exc, "errors", [])[:5])
        p.followup = dict(p.followup or {})
        p.followup.setdefault("command", cmd.model_dump())
        for a in p.actions:
            a.wire = self.wire(a)
        if cmd.ambiguities:
            p.warnings = list(p.warnings) + cmd.ambiguities
        if cmd.needs_confirmation:
            p.needs_confirmation = True
        self.session.remember(cmd, p)
        return p

    def look_params(self, cmd: LightCommand, recipe: Optional[str] = None) -> tuple:
        assumptions: list = []
        targets = target_ids(cmd)
        tsv_sorted = sorted(cmd.slots.get("target") or [], key=lambda sv: (cmd.text.lower().find(sv.raw.lower()) % 10000))
        label = " + ".join(sv.value.get("label") or sv.raw for sv in tsv_sorted)
        move = primary_movement(cmd)
        recipe = recipe or (move["recipe"] if move else "color_wash")
        if recipe not in RECIPES:
            recipe = "color_wash"
        d = self.prefs.defaults_for(recipe)
        rate = merged_rate(cmd)
        bpm = rate.get("bpm")
        if bpm is not None and not 40 <= bpm <= 220:
            raise ValueError(f"{bpm:g} BPM is outside 40-220")
        if bpm is None and self.session.bpm:
            bpm = self.session.bpm
            assumptions.append({"fact": f"tempo {bpm:g} BPM", "source": f"session ({self.session.bpm_source})"})
        period = rate.get("period_ms")
        if rate.get("beats"):
            period = int(rate["beats"] * 60000.0 / (bpm or DEFAULT_BPM))
        if bpm is None and period is None and recipe not in ("color_wash", "strobe", "blackout_kill"):
            assumptions.append({"fact": f"no tempo given; using {DEFAULT_BPM:g} BPM (tap the BPM button to set it)", "source": "default"})
        rate_word = rate.get("word")
        if rate_word is None:
            rate_word = d["rate_word"]
            if recipe not in ("color_wash", "blackout_kill"):
                assumptions.append({"fact": f"speed '{rate_word}'", "source": "your learned default" if (self.prefs.state["recipe_defaults"].get(recipe) or {}).get("rate_word") else "default"})
        colors = [sv.value["name"] for sv in cmd.slots.get("color") or [] if sv.value.get("name")]
        palette = self.prefs.house_palette()
        if not colors and recipe != "blackout_kill":
            colors = [palette[0]]
            assumptions.append({"fact": f"no color given; using house color '{colors[0]}'", "source": "house palette"})
        if recipe == "color_chase" and len(colors) < 2:
            extra = [c for c in palette if c not in colors][: 2 - len(colors)]
            colors = colors + extra
            assumptions.append({"fact": f"chase needs 2+ colors; added {', '.join(extra)}", "source": "house palette"})
        iv = cmd.first("intensity")
        intensity = iv.value.get("level", d["intensity"]) if iv else d["intensity"]
        fv = cmd.first("fade")
        fade = fv.value.get("ms") if fv else None
        sv = cmd.first("size")
        size = sv.value.get("size") if sv else d.get("size")
        pv = cmd.first("priority")
        priority = int(pv.value.get("priority", 0)) if pv else 0
        dv = cmd.first("direction")
        mirror = bool((dv and dv.value.get("dir") == "mirror") or (move and move.get("mirror")))
        color_map: dict = {}
        low = cmd.text.lower()  # pair zones and colors in the order they were said, whatever order the slots were filled in
        tsv = sorted(cmd.slots.get("target") or [], key=lambda sv: low.find(sv.raw.lower()))
        csv_ = sorted([sv for sv in cmd.slots.get("color") or [] if sv.value.get("name")], key=lambda sv: low.find(sv.raw.lower()))
        if len(tsv) >= 2 and len(tsv) == len(csv_):
            for t, cl in zip(tsv, csv_):
                for f in t.value.get("fixture_ids", []):
                    color_map[str(f)] = cl.value["name"]
            assumptions.append({"fact": "colors paired with zones in the order you said them: " +
                                ", ".join(f"{t.raw} {cl.value['name']}" for t, cl in zip(tsv, csv_)), "source": "planner"})
        params = LookParams(recipe=recipe, targets=targets, target_label=label, colors=colors, intensity=float(intensity),
                            bpm=bpm, period_ms=period, rate_word=rate_word, fade_ms=fade, size=size, priority=priority, mirror=mirror,
                            color_map=color_map)
        return params, assumptions

    def _fixture_facts(self, ids: list, limit: int = 3) -> list:
        out = []
        for fid in ids[:limit]:
            fx = self.rig.fixtures[fid]
            roles = ", ".join(f"{r} ch{fx.roles[r]}" for r in ROLE_LABELS if r in fx.roles)
            srcs = sorted({fx.role_sources[r].split(" ")[0] for r in ROLE_LABELS if r in fx.roles})
            out.append({"fact": f"fixture {fid} '{fx.name}' = {fx.key}: {roles}", "source": "/".join(srcs)})
        if len(ids) > limit:
            out.append({"fact": f"... and {len(ids) - limit} more fixtures", "source": "rig"})
        return out

    def _structural_warnings(self) -> list:
        return ["Reloading replaces the show in QLC+: running functions restart and unsaved QLC+ edits are lost. Save in QLC+ first."]

    def _create_look(self, cmd: LightCommand) -> Plan:
        params, assumptions = self.look_params(cmd)
        look = compile_look(self.rig, params)
        kinds = [self.rig.fixtures[i].kind for i in params.targets if i in self.rig.fixtures]
        summary = f"Create '{look.name}': {len(look.functions)} functions ({', '.join(sorted({f.type for f in look.functions}))}) on {len(params.targets)} fixtures"
        actions = [
            Action(op="write_look", args={"params": params.to_dict(), "expected_ids": look.ids}, describe=f"add functions {look.ids[0]}-{look.ids[-1]} to {self.cfg.project_path.name}"),
            Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+"),
        ]
        warnings = [f"skipped {s.get('name', s['fixture'])}: {s['reason']}" for s in look.skipped] + self._structural_warnings()
        lk = look.summary()
        lk["kind"] = max(set(kinds), key=kinds.count) if kinds else "other"
        return self._plan("create_look", "structural", summary, actions=actions, assumptions=assumptions + look.assumptions,
                          warnings=warnings, needs_confirmation=True, look=lk,
                          followup={"preview": True, "preview_params": params.to_dict()})

    def preview_for(self, plan: Plan, seconds: float = 6.0, fps: int = 20) -> Optional[dict]:
        """Preview frames for a plan, simulated only when asked for (keeps planning fast)."""
        fu = plan.followup or {}
        if isinstance(fu.get("preview"), dict):
            return fu["preview"]
        params = fu.get("preview_params") or fu.get("apply")
        if not params:
            return None
        look = compile_look(self.rig, LookParams(**params))
        return compress(simulate(self.rig, look, seconds=seconds, fps=fps))

    def _propose_look(self, cmd: LightCommand) -> Plan:
        targets = target_ids(cmd)
        fxs = [self.rig.fixtures[i] for i in targets]
        kinds = [f.kind for f in fxs]
        kind = max(set(kinds), key=kinds.count) if kinds else "other"
        prop = self.prefs.propose(kind)
        move = primary_movement(cmd)
        recipe = move["recipe"] if move else prop["recipe"]
        if recipe in MOVE_RECIPES and not any(f.can_move for f in fxs):
            recipe = next((r for r in self.prefs.rank_recipes(kind) if r not in MOVE_RECIPES), "breathe")
        params, assumptions = self.look_params(cmd, recipe=recipe)
        if not cmd.slots.get("color"):
            params.colors = [prop["color"]] if recipe != "color_chase" else [prop["color"]] + [c for c in self.prefs.house_palette() if c != prop["color"]][:1]
            assumptions = [a for a in assumptions if "no color given" not in a["fact"] and "chase needs" not in a["fact"]]
            assumptions.append({"fact": f"color '{', '.join(params.colors)}'", "source": "house palette, least recently used"})
        if not cmd.slots.get("rate") and prop.get("rate_word"):
            params.rate_word = prop["rate_word"]
        if not cmd.slots.get("size") and prop.get("size"):
            params.size = prop["size"]
        look = compile_look(self.rig, params)
        assumptions = self._fixture_facts(targets) + assumptions + look.assumptions + [{"fact": f"chose '{recipe}': {prop['reason']}", "source": "preference model"}]
        for fx in fxs[:2]:
            for c in params.colors[:2]:
                if fx.color_mode == "wheel":
                    w = self.rig.wheel_value(fx, c)
                    if w["value"] is None:
                        assumptions.append({"fact": f"no usable wheel slot for {c} on {fx.key} (you ruled out its listed slot); say 'find {c} on fixture {fx.id}'", "source": "your feedback"})
                    else:
                        assumptions.append({"fact": f"{c} on {fx.key} = wheel value {w['value']} ('{w['slot']}')", "source": "operator fact" if c in (fx.caps.get('wheel') or {}) else "qxf color wheel"})
                elif fx.color_mode == "rgb":
                    assumptions.append({"fact": f"{c} on {fx.key} = {self.rig.emitter_values(fx, c, params.intensity)}", "source": "data/colors.yaml"})
        kinds_lk = look.summary()
        kinds_lk["kind"] = kind
        summary = f"Preview '{look.name}' live on {len(targets)} fixture(s) for 8s, then tell me if it looked right"
        actions = [  # frames are simulated when the preview is played (keeps planning fast and the plan small)
            Action(op="preview", args={"lazy": True, "params": params.to_dict(), "seconds": 8.0, "fps": 20},
                   describe="play 8 s of the look through Simple Desk (20 frames/s), then release the channels"),
            Action(op="ask_outcome", args={"questions": ["color", "movement", "speed", "intensity", "fixture"]}, describe="ask how it looked"),
        ]
        return self._plan("propose_look", "live", summary, actions=actions, assumptions=assumptions,
                          warnings=[f"skipped {s.get('name', s['fixture'])}: {s['reason']}" for s in look.skipped],
                          look=kinds_lk, followup={"apply": params.to_dict()})

    def apply_proposal(self, plan: Plan) -> Plan:
        params = LookParams(**plan.followup["apply"])
        look = compile_look(self.rig, params)
        actions = [
            Action(op="write_look", args={"params": params.to_dict(), "expected_ids": look.ids}, describe=f"add functions {look.ids[0]}-{look.ids[-1]}"),
            Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+"),
        ]
        lk = look.summary()
        lk["kind"] = (plan.look or {}).get("kind", "other")
        skipped = [f"skipped {s.get('name', s['fixture'])}: {s['reason']}" for s in look.skipped]  # e.g. a color ruled out since the preview
        p = self._plan("create_look", "structural", f"Save '{look.name}' to the show ({len(look.functions)} functions)", actions=actions,
                       assumptions=look.assumptions, warnings=skipped + self._structural_warnings(), needs_confirmation=True, look=lk,
                       followup={"from_proposal": plan.plan_id, "command": (plan.followup or {}).get("command")})
        self.session.remember(None, p)
        return p

    def _probe_fixture(self, cmd: LightCommand) -> Plan:
        targets = target_ids(cmd)
        fx = self.rig.fixtures[targets[0]]
        base = []
        if fx.has("dimmer"):
            base.append([*fx.dmx(fx.roles["dimmer"]), 255])
        if fx.caps.get("shutter"):
            base.append([*fx.dmx(fx.roles["shutter"]), fx.caps["shutter"].get("open", 0)])
        color = cmd.first("color")
        chan = cmd.first("channel")
        if color and fx.color_mode == "wheel":
            cname = color.value.get("name") or color.raw
            slots = sorted(set((fx.caps.get("wheel") or {}).values()) | set(range(0, 128, 8)))
            steps = [{"set": base + [[*fx.dmx(fx.roles["color_wheel"]), v]], "label": f"wheel value {v}", "value": v} for v in slots]
            mode, question = "wheel", f"Tell me when fixture {fx.id} shows {cname}"
        elif color and fx.color_mode == "rgb":
            emitters = [r for r in ("red", "green", "blue", "white", "amber", "uv") if fx.has(r)]
            dark = [[*fx.dmx(fx.roles[r]), 0] for r in emitters]
            steps = []
            for r in emitters:
                lit = [x for x in dark if x[:2] != list(fx.dmx(fx.roles[r]))] + [[*fx.dmx(fx.roles[r]), 255]]
                steps.append({"set": base + lit, "label": f"channel {fx.roles[r] + 1} alone at full (should be {r})",
                              "channel": fx.roles[r], "expect": r})
            mode, question = "rgb", "Which color do you see? (red, green, blue, white, amber, uv; or 'yes' if it matches)"
            cname = color.value.get("name") or color.raw
        elif chan:
            idx = chan.value.get("index")
            if idx is None or not 0 <= idx < fx.channels:
                raise ValueError(f"fixture {fx.id} has channels 1-{fx.channels}")
            steps = [{"set": base, "ramp": [*fx.dmx(idx)], "label": f"channel {idx + 1} ramp 0-255", "channel": idx}]
            mode, question = "channel", f"What did channel {idx + 1} do? (dimmer, strobe, color, pan, tilt, gobo, prism, focus, speed, nothing)"
            cname = None
        else:
            steps = [{"set": base if i not in (fx.roles.get("dimmer"), fx.roles.get("shutter")) else [], "ramp": [*fx.dmx(i)], "label": f"channel {i + 1} ramp 0-255", "channel": i}
                     for i in range(fx.channels)]
            mode, question = "channels", "For each channel, tell me what it did"
            cname = None
        cal = {"fixture_id": fx.id, "mode": mode, "steps": steps, "color": cname, "question": question}
        summary = f"Calibrate fixture {fx.id} '{fx.name}' ({fx.key}): {len(steps)} step(s). {question}."
        return self._plan("probe_fixture", "live", summary, actions=[Action(op="calibrate_start", args=cal, describe="step through values live, one at a time")],
                          assumptions=self._fixture_facts([fx.id], 1), warnings=["Uses Simple Desk overrides; they are released when calibration ends."])

    def adjusted_params(self, params: dict, issues: list) -> tuple:
        """Apply speed/size/intensity feedback to a look's parameters."""
        from lightai.prefs import RATE_ORDER, SIZE_ORDER, _shift

        p = dict(params)
        notes = []
        for iss in issues:
            k, d = iss.get("kind"), iss.get("direction")
            if k == "speed" and d in ("too_fast", "too_slow"):
                if p.get("bpm") and not p.get("rate_word"):
                    p["rate_word"] = "medium"
                old = p.get("rate_word") or "medium"
                p["rate_word"] = _shift(RATE_ORDER, old, -1 if d == "too_fast" else 1)
                if p.get("period_ms"):
                    p["period_ms"] = int(p["period_ms"] * (1.5 if d == "too_fast" else 0.67))
                notes.append(f"speed {old} -> {p['rate_word']}")
            elif k == "size" and d in ("too_small", "too_big"):
                old = p.get("size") or "medium"
                p["size"] = _shift(SIZE_ORDER, old, 1 if d == "too_small" else -1)
                notes.append(f"size {old} -> {p['size']}")
            elif k == "intensity" and d in ("too_dim", "too_bright"):
                old = float(p.get("intensity") or 1.0)
                p["intensity"] = round(max(0.1, min(1.0, old + (0.2 if d == "too_dim" else -0.2))), 2)
                notes.append(f"intensity {old:.0%} -> {p['intensity']:.0%}")
        return p, notes

    def taste_followup(self, last: Optional[Plan], issues: list, dry_run: bool = False, proposals_only: bool = False) -> dict:
        """Speed/size/intensity feedback also corrects the look it was about.

        A proposal gets its parameters adjusted (Keep it saves the corrected version); a saved look gets
        a ready update_look plan (needs confirmation). Returns {"notes", "adjusted_plan_id"|"update_plan"}.
        """
        taste = [i for i in issues if i.get("kind") in ("speed", "size", "intensity")]
        if last is None or not taste:
            return {}
        if (last.followup or {}).get("apply"):
            new, notes = self.adjusted_params(last.followup["apply"], taste)
            if not notes:
                return {}
            if dry_run:  # planning only describes the change; executing the feedback makes it
                return {"notes": notes, "adjusted_plan_id": last.plan_id}
            try:
                look = compile_look(self.rig, LookParams(**new))
            except (CompileError, ValueError) as exc:
                return {"notes": notes, "error": str(exc)}
            last.followup["apply"] = new
            for a in last.actions:  # the next "Play preview" shows the corrected look
                if a.op == "preview":
                    a.args = {"lazy": True, "params": new, "seconds": 8.0, "fps": 20}
            last.look = {**look.summary(), "kind": (last.look or {}).get("kind", "other")}
            return {"notes": notes, "adjusted_plan_id": last.plan_id}
        if proposals_only or last.intent not in ("create_look", "update_look") or not last.look or last.look.get("main_id") is None:
            return {}
        from lightai.compiler.sidecar import Sidecar

        entry = Sidecar(self.cfg.sidecar_path).looks.get(str(last.look["main_id"]))
        if not entry:
            return {}
        new, notes = self.adjusted_params(entry["params"], taste)
        if not notes:
            return {}
        new["name"] = None
        try:
            look = compile_look(self.rig, LookParams(**new), start_id=min(entry["ids"]), allow_ids=set(entry["ids"]))
        except (CompileError, ValueError) as exc:
            return {"notes": notes, "error": str(exc)}
        upd = self._plan("update_look", "structural", f"Update '{entry['name']}': {'; '.join(notes)}",
                         actions=[Action(op="update_look", args={"main_id": entry["main_id"], "old_ids": entry["ids"],
                                                                 "params": look.params.to_dict(), "in_place": look.ids == entry["ids"]},
                                         describe="rewrite the look with your correction"),
                                  Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+")],
                         warnings=self._structural_warnings(), needs_confirmation=True, look=look.summary())
        self.session.plans[upd.plan_id] = upd
        return {"notes": notes, "update_plan": upd}

    def _feedback(self, cmd: LightCommand) -> Plan:
        last = self.session.last_action_plan
        fb = self.router.from_command(cmd, last)
        what = ", ".join(i.kind + (f" ({i.expected}->{i.observed})" if i.expected or i.observed else "") + (f" {i.direction}" if i.direction else "") for i in fb.issues) or fb.verdict
        summary = f"Feedback on {last.plan_id if last else 'nothing yet'}: {what}"
        tf = self.taste_followup(last, [i.model_dump() for i in fb.issues], dry_run=True)
        followup: dict = {}
        if tf.get("adjusted_plan_id"):
            followup["adjusted_plan_id"] = tf["adjusted_plan_id"]
            summary += f". Proposal adjusted ({'; '.join(tf['notes'])}); preview again or press Keep it"
        elif tf.get("update_plan"):
            followup["update_plan_id"] = tf["update_plan"].plan_id
            summary += f". Ready to apply to the look: {'; '.join(tf['notes'])} (plan {tf['update_plan'].plan_id})"
        return self._plan("feedback", "learn", summary, followup=followup,
                          actions=[Action(op="feedback", args={"feedback": fb.model_dump(), "plan_id": last.plan_id if last else None, "text": cmd.text}, describe="record and learn")])

    def _resolve_function(self, cmd: LightCommand) -> dict:
        fr = cmd.first("function_ref")
        return fr.value if fr else {}

    def _run_function(self, cmd: LightCommand) -> Plan:
        v = self._resolve_function(cmd)
        fid, name = v["function_id"], v["name"]
        warnings = []
        if fid in self.rig.protected_functions:
            warnings.append(f"'{name}' is a protected function")
        return self._plan("run_function", "live", f"Start '{name}' (ID {fid})", actions=[Action(op="function", args={"id": fid, "on": True}, describe=f"start {fid}")],
                          warnings=warnings, needs_confirmation=bool(v.get("ambiguous") or v.get("weak")))

    def _stop_function(self, cmd: LightCommand) -> Plan:
        v = self._resolve_function(cmd)
        fid, name = v["function_id"], v["name"]
        return self._plan("stop_function", "live", f"Stop '{name}' (ID {fid})", actions=[Action(op="function", args={"id": fid, "on": False}, describe=f"stop {fid}")],
                          needs_confirmation=bool(v.get("ambiguous") or v.get("weak")))

    def _stop_all(self, cmd: LightCommand) -> Plan:
        return self._plan("stop_all", "live", "Stop every running function", actions=[Action(op="stop_all", describe="query each function and stop the running ones")])

    def _blackout(self, cmd: LightCommand) -> Plan:
        kid = self.rig.kill_function_id
        if kid is not None and kid in self.rig.functions:
            covered = set(self.rig.functions[kid].fixtures)
            lit = [fx.name for fx in self.rig.fixtures.values()
                   if fx.id not in covered and fx.id not in self.rig.exclude_from_all and (fx.has("dimmer") or fx.color_mode == "rgb")]
            warnings = [f"kill scene {kid} does not cover {len(lit)} fixtures, they stay lit: {', '.join(lit[:6])}{' ...' if len(lit) > 6 else ''}"] if lit else []
            return self._plan("blackout", "live", f"Blackout: start kill scene '{self.rig.functions[kid].name}' (ID {kid})",
                              actions=[Action(op="function", args={"id": kid, "on": True}, describe=f"start {kid}")], warnings=warnings)
        return self._plan("blackout", "live", "Blackout via grand master 0 (no kill scene configured)", actions=[Action(op="grand_master", args={"value": 0})],
                          warnings=["set kill_function_id in overrides.yaml to use your P100 kill scene"])

    def _blackout_release(self, cmd: LightCommand) -> Plan:
        acts = []
        kid = self.rig.kill_function_id
        if kid is not None and kid in self.rig.functions:
            acts.append(Action(op="function", args={"id": kid, "on": False}, describe=f"stop {kid}"))
        if self.session.gm_changed or not acts:
            acts.append(Action(op="grand_master", args={"value": 255}, describe="grand master to full"))
        return self._plan("blackout_release", "live", "Release the blackout", actions=acts)

    def _set_level(self, cmd: LightCommand) -> Plan:
        iv = cmd.first("intensity")
        if "level" not in iv.value:
            raise ValueError(f"I couldn't read the level '{iv.raw}' (say e.g. 40%, half, full or off)")
        level = float(iv.value["level"])
        targets = target_ids(cmd)
        if not targets:
            return self._plan("set_level", "live", f"Grand master to {level:.0%}", actions=[Action(op="grand_master", args={"value": int(round(level * 255))})])
        chans, skipped = [], []
        for fid in targets:
            fx = self.rig.fixtures[fid]
            v = self.rig.dimmer_value(fx, level)
            if v is None:
                skipped.append(fx.name)
                continue
            chans.append([*fx.dmx(fx.roles["dimmer"]), v])
        if not chans:
            raise ValueError(f"{', '.join(skipped)} ha{'ve' if len(skipped) > 1 else 's'} no dimmer channel; set their brightness with a look instead")
        warnings = ["Live override: stays until you say 'release'. Shutter and color still come from the running looks."]
        if skipped:
            warnings.append(f"no dimmer channel on: {', '.join(skipped)}")
        label = cmd.first("target").raw
        return self._plan("set_level", "live", f"{label} to {level:.0%} ({len(chans)} dimmer channels)", actions=[Action(op="set_channels", args={"channels": chans})], warnings=warnings)

    def _set_channel(self, cmd: LightCommand) -> Plan:
        fx = self.rig.fixtures[target_ids(cmd)[0]]
        ch = cmd.first("channel").value
        idx = ch.get("index")
        if idx is None or not 0 <= idx < fx.channels:
            raise ValueError(f"fixture {fx.id} has channels 1-{fx.channels} ({ch.get('numbering')} numbering)")
        vv = cmd.first("value")
        if "value" not in vv.value:
            raise ValueError(f"I couldn't read the value '{vv.raw}' (0-255 or a percentage)")
        val = int(vv.value["value"])
        role = next((r for r, c in fx.roles.items() if c == idx), "unknown")
        return self._plan("set_channel", "live", f"Fixture {fx.id} channel {ch.get('number')} ({role}) = {val}",
                          actions=[Action(op="set_channels", args={"channels": [[*fx.dmx(idx), val]]})],
                          assumptions=[{"fact": f"'channel {ch.get('number')}' = channel index {idx} ({ch.get('numbering')})", "source": "overrides.yaml channel_numbering"}],
                          warnings=["Live override: stays until you say 'release'."])

    def _release(self, cmd: LightCommand) -> Plan:
        targets = target_ids(cmd)
        if targets:
            chans = [[*self.rig.fixtures[f].dmx(c)] for f in targets for c in range(self.rig.fixtures[f].channels)]
            return self._plan("release", "live", f"Release overrides on {len(targets)} fixture(s)", actions=[Action(op="reset_channels", args={"channels": chans})])
        unis = sorted({fx.universe + 1 for fx in self.rig.fixtures.values()})
        return self._plan("release", "live", "Release all Simple Desk overrides", actions=[Action(op="reset_universes", args={"universes": unis})])

    def _fixture_edit_rotate(self, cmd: LightCommand) -> Plan:
        # 'counter clockwise' can leave a second angle span ('clockwise') with no degrees: use the one with a number
        ang = next((dict(sv.value) for sv in cmd.slots.get("angle") or [] if "deg" in sv.value), dict(cmd.first("angle").value))
        dirs = {sv.value.get("dir") for sv in cmd.slots.get("direction") or []}
        text = re.sub(r"\b(anti|counter)[\s-]*clock[\s-]?wise\b", " ccw ", cmd.text.lower())  # 'counter clockwise' is often split by the tagger
        if (re.search(r"\bccw\b", text) or dirs & {"ccw", "left"}) and ang["deg"] > 0:
            ang["deg"] = -ang["deg"]
        items = self.rig.ws.monitor_items()
        acts = []
        assumptions = []
        if abs(ang["deg"]) >= 360:
            assumptions.append({"fact": f"{ang['deg']} degrees is {abs(ang['deg']) // 360} full turn(s) plus {ang['deg'] % 360} degrees", "source": "normalizer"})
        for fid in target_ids(cmd):
            cur = (items.get(fid) or {}).get("rotation", 0)
            new = (cur + ang["deg"]) % 360 if ang.get("relative", True) else ang["deg"] % 360
            acts.append(Action(op="edit_fixture", args={"kind": "rotate", "fixture_id": fid, "rotation": new, "before": cur},
                               describe=f"fixture {fid}: rotation {cur} -> {new} deg"))
        acts.append(Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+"))
        missing = [fid for fid in target_ids(cmd) if fid not in items]
        extra = [f"fixture(s) {missing} are not on the 2D stage map yet; the edit adds them at 0,0"] if missing else []
        return self._plan("fixture_edit.rotate", "structural", f"Rotate {len(acts) - 1} fixture(s) on the 2D stage map", actions=acts,
                          assumptions=assumptions, warnings=extra + self._structural_warnings(), needs_confirmation=True)

    def _fixture_edit_move(self, cmd: LightCommand) -> Plan:
        d = cmd.first("direction").value.get("dir")
        dist = cmd.first("distance")
        if dist and "mm" not in dist.value:
            raise ValueError(f"I couldn't read the distance '{dist.raw}' (say e.g. 50 cm, 1 m, 2 ft)")
        mm = dist.value.get("mm", 250) if dist else 250
        if abs(mm) > 20000:
            raise ValueError(f"{mm / 1000:g} m is bigger than the room; did you mean cm?")
        assumptions = [] if dist and not dist.value.get("assumed") else [{"fact": (dist.value.get("assumed") if dist else "no distance given; moving 25 cm"), "source": "default"}]
        dx, dy = {"left": (-mm, 0), "right": (mm, 0), "up": (0, -mm), "down": (0, mm)}.get(d, (0, 0))
        if (dx, dy) == (0, 0):
            raise ValueError(f"can't move in direction '{d}'")
        items = self.rig.ws.monitor_items()
        acts = []
        clamped = []
        for fid in target_ids(cmd):
            cur = items.get(fid) or {"x": 0.0, "y": 0.0}
            nx, ny = max(0.0, cur["x"] + dx), max(0.0, cur["y"] + dy)
            if (nx, ny) != (cur["x"] + dx, cur["y"] + dy):
                clamped.append(fid)
                assumptions.append({"fact": f"fixture {fid} stops at the stage-map edge ({nx:.0f}, {ny:.0f}) mm, "
                                            f"{abs(nx - cur['x']) / 10 + abs(ny - cur['y']) / 10:g} cm instead of {mm / 10:g} cm",
                                    "source": "2D map bounds"})
            acts.append(Action(op="edit_fixture", args={"kind": "move", "fixture_id": fid, "x": nx, "y": ny, "before": [cur["x"], cur["y"]]},
                               describe=f"fixture {fid}: ({cur['x']:.0f}, {cur['y']:.0f}) -> ({nx:.0f}, {ny:.0f}) mm"))
        acts.append(Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+"))
        missing = [fid for fid in target_ids(cmd) if fid not in items]
        extra = [f"fixture(s) {missing} are not on the 2D stage map yet; moving from 0,0"] if missing else []
        edge = f"; fixture(s) {clamped} stop at the map edge" if clamped else ""
        return self._plan("fixture_edit.move", "structural", f"Move {len(acts) - 1} fixture(s) {d} {mm / 10:g} cm on the 2D stage map{edge}",
                          actions=acts, assumptions=assumptions, warnings=extra + self._structural_warnings(), needs_confirmation=True)

    def _fixture_edit_rename(self, cmd: LightCommand) -> Plan:
        fid = target_ids(cmd)[0]
        name = cmd.first("name").value["name"]
        before = self.rig.fixtures[fid].name
        return self._plan("fixture_edit.rename", "structural", f"Rename fixture {fid} '{before}' -> '{name}'",
                          actions=[Action(op="edit_fixture", args={"kind": "rename", "fixture_id": fid, "name": name, "before": before}),
                                   Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+")],
                          warnings=self._structural_warnings(), needs_confirmation=True)

    def _query_functions(self, cmd: LightCommand) -> Plan:
        funcs = list(self.rig.functions.values())
        colors = [sv.value.get("name") for sv in cmd.slots.get("color") or [] if sv.value.get("name")]
        tgt = set(target_ids(cmd))
        move = primary_movement(cmd)
        t = cmd.text.lower()
        if colors:
            funcs = [f for f in funcs if any(c in f.name.lower() for c in colors)]
        if tgt:
            funcs = [f for f in funcs if tgt & set(f.fixtures)]
        if move:
            words = {"circle": "circ", "circle_wave": "wave", "ballyhoo": "bally", "strobe": "strob", "breathe": "breath", "sweep": "sweep", "color_chase": "chase", "running_light": "run"}
            key = words.get(move["recipe"], move["recipe"])
            funcs = [f for f in funcs if key in f.name.lower() or (move["recipe"] in ("circle", "circle_wave", "ballyhoo", "sweep", "figure8") and f.type == "EFX")]
        t_types = re.sub(r"^\s*(please\s+)?(show|list|find|give|tell)(\s+me)?\b", " ", t)  # 'show me the chasers': show is the verb
        types = {typ for typ in ("chaser", "scene", "efx", "show", "collection", "sequence") if re.search(rf"\b{typ}s?\b", t_types)}
        if len(types) > 1 and re.search(r"\b(in|from|of) the show\b", t_types):
            types.discard("show")  # 'which EFX are in the show'
        if types:
            funcs = [f for f in funcs if f.type.lower() in types]
        if "today" in t or " ai " in f" {t} " or re.search(r"\byou (create|created|make|made|built|build)\b|\blightai (made|created)\b", t):
            funcs = [f for f in funcs if f.path.startswith("AI")]
        if cmd.first("function_ref"):
            cands = cmd.first("function_ref").value.get("candidates", [])
            ids = {c["function_id"] for c in cands if c["score"] >= cands[0]["score"] - 0.15}
            funcs = [f for f in funcs if f.id in ids] or funcs
        rows = [{"id": f.id, "type": f.type, "name": f.name, "path": f.path, "priority": f.priority} for f in sorted(funcs, key=lambda f: f.id)]
        return self._plan("query.functions", "info", f"{len(rows)} function(s)", look={"results": rows[:200]})

    def _query_fixtures(self, cmd: LightCommand) -> Plan:
        from lightai.nlu.normalize import digits_for_words

        ids = target_ids(cmd) or sorted(self.rig.fixtures)
        m = re.search(r"universe\s*(\d+)", digits_for_words(cmd.text.lower()))
        if m:
            ids = [i for i in ids if self.rig.fixtures[i].universe + 1 == int(m.group(1))]
        rows = [{k: v for k, v in self.rig.fixtures[i].summary().items() if k in ("id", "name", "model", "mode", "universe", "address", "channels", "kind", "zones", "roles")} for i in ids]
        return self._plan("query.fixtures", "info", f"{len(rows)} fixture(s)", look={"results": rows})

    def _query_status(self, cmd: LightCommand) -> Plan:
        fr = cmd.first("function_ref")
        ids = [fr.value["function_id"]] if fr and fr.value.get("function_id") is not None else sorted(self.rig.functions)
        return self._plan("query.status", "info", "Check which functions are running", actions=[Action(op="query_status", args={"ids": ids})])

    def _set_bpm(self, cmd: LightCommand) -> Plan:
        rate = merged_rate(cmd)
        bpm = rate.get("bpm") or (round(60000.0 / rate["period_ms"], 1) if rate.get("period_ms") else None)
        if not bpm:
            raise ValueError("no BPM number found")
        if not 40 <= bpm <= 220:
            raise ValueError(f"{bpm:g} BPM is outside 40-220; did you mean something else?")
        return self._plan("set_bpm", "session", f"Tempo set to {bpm:g} BPM for new looks and running AI looks",
                          actions=[Action(op="set_bpm", args={"bpm": bpm}, describe="use this tempo for new looks"),
                                   Action(op="retime_looks", args={"bpm": bpm}, describe="retime running AI looks live (fork build)")])

    def _ai_look_for(self, cmd: LightCommand) -> tuple:
        from lightai.compiler.sidecar import Sidecar

        fr = cmd.first("function_ref")
        fid = fr.value.get("function_id") if fr else None
        side = Sidecar(self.cfg.sidecar_path)
        if fr and fr.value.get("same_name"):  # two of my looks share this name: never pick one silently
            same = {c["function_id"] for c in fr.value.get("candidates", []) if c["name"].lower() == fr.value["name"].lower()}
            owners = [e for e in side.looks.values() if same & ({e.get("main_id")} | set(e.get("ids", [])))]
            if len(owners) > 1:
                raise ValueError(f"there are {len(owners)} looks called '{fr.value['name']}' (IDs {', '.join(str(e['main_id']) for e in owners)}); say which ID")
        for entry in side.looks.values():
            if fid is not None and (fid == entry.get("main_id") or fid in entry.get("ids", [])):
                return entry, fr
        return None, fr

    def _update_look(self, cmd: LightCommand) -> Plan:
        entry, fr = self._ai_look_for(cmd)
        if entry is None:
            raise ValueError(f"'{fr.raw if fr else '?'}' isn't a look lightai made; I only change my own looks. Say 'create ...' for a new one.")
        params = dict(entry["params"])
        changes = []
        colors = [sv.value["name"] for sv in cmd.slots.get("color") or [] if sv.value.get("name")]
        if colors:
            params["colors"] = colors
            changes.append(f"colors -> {', '.join(colors)}")
        rate = merged_rate(cmd)
        if rate.get("bpm"):
            params["bpm"], params["period_ms"] = rate["bpm"], None
            changes.append(f"tempo -> {rate['bpm']:g} BPM")
        if rate.get("period_ms"):
            params["period_ms"] = rate["period_ms"]
            changes.append(f"period -> {rate['period_ms']} ms")
        if not rate.get("word") and re.search(r"\b(faster|quicker|speed (it )?up|slower|slow (it )?down)\b", cmd.text.lower()):
            rate["word"] = "medium"  # 'speed up the X' with no speed word tagged
        if rate.get("word"):
            from lightai.prefs import RATE_ORDER, _shift

            word = rate["word"]
            if re.search(r"\b(faster|quicker|speed (it )?up)\b", cmd.text.lower()):
                word = _shift(RATE_ORDER, params.get("rate_word") or "medium", 1)
            elif re.search(r"\b(slower|slow (it )?down)\b", cmd.text.lower()):
                word = _shift(RATE_ORDER, params.get("rate_word") or "medium", -1)
            params["rate_word"] = word
            changes.append(f"speed -> {word}")
        iv = cmd.first("intensity")
        if iv and "level" in iv.value:
            params["intensity"] = iv.value["level"]
            changes.append(f"intensity -> {iv.value['level']:.0%}")
        elif iv and "relative" in iv.value:  # 'dimmer', 'a bit brighter'
            new = round(max(0.1, min(1.0, float(params.get("intensity") or 1.0) + iv.value["relative"])), 2)
            if new != params.get("intensity"):
                params["intensity"] = new
                changes.append(f"intensity -> {new:.0%}")
        sv = cmd.first("size")
        if sv and sv.value.get("size"):
            params["size"] = sv.value["size"]
            changes.append(f"size -> {sv.value['size']}")
        elif sv and sv.value.get("relative"):  # 'bigger', 'tighter'
            from lightai.prefs import SIZE_ORDER, _shift

            params["size"] = _shift(SIZE_ORDER, params.get("size") or "medium", int(sv.value["relative"]))
            changes.append(f"size -> {params['size']}")
        fv = cmd.first("fade")
        if fv and "ms" in fv.value:
            params["fade_ms"] = fv.value["ms"]
            changes.append(f"fade -> {fv.value['ms']} ms")
        pv = cmd.first("priority")
        if pv and "priority" in pv.value:
            params["priority"] = pv.value["priority"]
            changes.append(f"priority -> {pv.value['priority']}")
        dv = cmd.first("direction")
        if dv and dv.value.get("dir") == "mirror":
            params["mirror"] = True
            changes.append("mirrored")
        if not changes:
            raise ValueError("What should change? (color, speed, size, intensity, fade or priority)")
        params["name"] = None
        look = compile_look(self.rig, LookParams(**params), start_id=min(entry["ids"]), allow_ids=set(entry["ids"]))
        same = look.ids == entry["ids"]
        summary = f"Update '{entry['name']}': {'; '.join(changes)}"
        actions = [Action(op="update_look", args={"main_id": entry["main_id"], "old_ids": entry["ids"], "params": look.params.to_dict(), "in_place": same},
                          describe=("rewrite the look's functions in place" if same else "replace the look (new function IDs, buttons rebound)")),
                   Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+")]
        lk = look.summary()
        return self._plan("update_look", "structural", summary, actions=actions, assumptions=look.assumptions,
                          warnings=self._structural_warnings(), needs_confirmation=True, look=lk)

    def _delete_look(self, cmd: LightCommand) -> Plan:
        entry, fr = self._ai_look_for(cmd)
        if entry is None:
            raise ValueError(f"'{fr.raw if fr else '?'}' isn't a look lightai made; delete your own functions in QLC+.")
        ids = set(entry["ids"])
        users = [f for f in self.rig.functions.values() if f.id not in ids and ids & set(f.refs)]
        warnings = self._structural_warnings()
        if users:
            warnings.insert(0, "Used by your own functions, which will lose these steps: " + ", ".join(f"'{f.name}' ({f.type} {f.id})" for f in users[:8]))
        return self._plan("delete_look", "structural", f"Delete '{entry['name']}' ({len(entry['ids'])} functions and their lightai buttons)",
                          actions=[Action(op="delete_look", args={"main_id": entry["main_id"], "ids": entry["ids"]}, describe="remove the functions"),
                                   Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+")],
                          warnings=warnings, needs_confirmation=True)

    def _add_widget(self, cmd: LightCommand) -> Plan:
        fr = cmd.first("function_ref")
        fid, name = fr.value["function_id"], fr.value["name"]
        entry, _ = self._ai_look_for(cmd)
        if entry is None and fr.value.get("same_name"):  # 'add a button for all lights red': which of the four?
            same = [c for c in fr.value.get("candidates", []) if c["name"].lower() == name.lower()]
            which = ", ".join(str(c["type"]) + " " + str(c["function_id"]) for c in same)
            raise ValueError(f"there are {len(same)} functions called '{name}' ({which}); say which ID")
        if entry is not None:
            fid, name = entry["main_id"], entry["name"]
        return self._plan("add_widget", "structural", f"Add a Virtual Console button for '{name}' (function {fid}) in the 'lightai' frame",
                          actions=[Action(op="add_widget", args={"function_id": fid, "caption": name}, describe="add a Toggle button"),
                                   Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+")],
                          warnings=self._structural_warnings(), needs_confirmation=True)

    def _add_fixture(self, cmd: LightCommand) -> Plan:
        mv = cmd.first("fixture_model").value
        top = [c for c in mv.get("candidates", []) if c["score"] >= (mv.get("score") or 0) - 1e-9]
        if mv.get("ambiguous") and len({(c["manufacturer"], c["model"]) for c in top}) > 1:
            names = sorted({f"{c['manufacturer']} {c['model']}" for c in top})
            raise ValueError("which fixture: " + ", ".join(names[:5]) + (" ..." if len(names) > 5 else "") + "? Say the full model name.")
        fd = self.rig.library.get(mv["manufacturer"], mv["model"])
        if fd is None or not fd.modes:
            raise ValueError(f"no usable definition for {mv['manufacturer']} {mv['model']}")
        assumptions = []
        mode_slot = cmd.first("mode")
        modes = list(fd.modes.values())
        mode = None
        if mode_slot:
            want = mode_slot.value
            for m in modes:
                if want.get("channels") and len(m.channels) == want["channels"]:
                    mode = m
                    break
                if want["text"].lower() in m.name.lower():
                    mode = m
                    break
        if mode is None:
            mode = max(modes, key=lambda m: len(m.channels))
            assumptions.append({"fact": f"mode '{mode.name}' ({len(mode.channels)} ch): the mode with the most channels", "source": "default"})
        nch = len(mode.channels)
        addr = (cmd.first("address").value if cmd.first("address") else {})
        is_mover = fd.type.lower() in ("moving head", "scanner")
        universe = addr.get("universe", 0 if is_mover else 1)
        used = sorted((f.address, f.address + f.channels) for f in self.rig.fixtures.values() if f.universe == universe)
        if "address" in addr:
            start = addr["address"]
        else:
            start = 0
            for a, b in used:
                if start + nch <= a:
                    break
                start = max(start, b)
            assumptions.append({"fact": f"first free address in universe {universe + 1}: {start + 1}", "source": "patch"})
        if "universe" not in addr:
            assumptions.append({"fact": f"universe {universe + 1} ({'movers go on universe 1' if is_mover else 'LED/effects go on universe 2'})", "source": "rig convention"})
        if start < 0 or start + nch > 512:
            raise ValueError(f"{nch} channels don't fit at address {start + 1}")
        clash = [f for f in self.rig.fixtures.values() if f.universe == universe and not (start + nch <= f.address or f.address + f.channels <= start)]
        if clash:
            raise ValueError(f"address {start + 1}-{start + nch} overlaps {', '.join(f'{f.name} ({f.address + 1}-{f.address + f.channels})' for f in clash)}")
        nv = cmd.first("name")
        fid = max(self.rig.fixtures, default=-1) + 1
        name = nv.value["name"] if nv else f"{fd.model} #{fid}"
        return self._plan("add_fixture", "structural",
                          f"Patch '{name}' = {fd.manufacturer} {fd.model} ({mode.name}, {nch} ch) at universe {universe + 1} address {start + 1}, fixture ID {fid}",
                          actions=[Action(op="add_fixture", args={"id": fid, "name": name, "manufacturer": fd.manufacturer, "model": fd.model,
                                                                  "mode": mode.name, "universe": universe, "address": start, "channels": nch},
                                          describe="add the fixture to the patch"),
                                   Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+")],
                          assumptions=assumptions, warnings=self._structural_warnings(), needs_confirmation=True)

    def _none(self, cmd: LightCommand) -> Plan:
        return self._plan("none", "clarify", "That doesn't sound like a lighting request.")
