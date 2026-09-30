"""One object that wires config, rig, model, parser, prefs, session, planner and executor.

Shared by the CLI REPL and the HTTP API so both behave identically.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lightai.config import Config, load_config
from lightai.nlu.normalize import normalize_slot
from lightai.exec.executor import Executor
from lightai.nlu.infer import NluModel
from lightai.nlu.pipeline import Parser
from lightai.nlu.retriever import Retriever
from lightai.planner import Planner
from lightai.prefs import Prefs
from lightai.rig.model import Rig
from lightai.schema import LightCommand, Plan
from lightai.session import Session


# a fragment's slot can stand for a related slot of the command it corrects
COMPATIBLE = {"target": {"fixture_model", "function_ref"}, "fixture_model": {"target"}, "function_ref": {"target"}}
# what a fragment means for the command it corrects: fixtures said while patching name the model
PREFERRED = {"add_fixture": {"target": "fixture_model"}}
# 'no, I meant the spots', 'what I meant was swarms', 'not that, blue', "that's wrong"
CORRECTION = re.compile(r"^\s*(?:no+|nope|not that|wrong|that'?s wrong|that was wrong|that's not it|i meant|what i meant(?: was)?|"
                        r"actually|instead|sorry,? i meant|no,? what i meant(?: was)?|no,? i meant)\b[\s,.:;!-]*(?P<rest>.*)$", re.I)

AGAIN = re.compile(r"^\s*(?:(?:do|try|run|aim|point) (?:it |that |this |them )?(?:again|one more time)|again|redo(?: it| that)?|"
                   r"re-?aim(?: (?:it|them|that))?|one more time|repeat(?: that| it)?|same again)\s*[.!]*\s*$", re.I)


class LightAI:
    def __init__(self, cfg: Optional[Config] = None, model_dir: Optional[Path] = None, use_embeddings: bool = True) -> None:
        self.cfg = cfg or load_config()
        self.session = Session()
        self.rig = Rig.load(self.cfg)
        self.prefs = Prefs(self.cfg, self.rig.overrides)
        md = model_dir or self.cfg.current_model_dir()
        if md is None:
            raise RuntimeError("no trained model yet: run `lightai train --promote-if-better` first")
        self.model = NluModel(md, threads=self.cfg.ort_threads)
        self.use_embeddings = use_embeddings
        self.retriever = Retriever.for_rig(self.rig, self.cfg.models_dir, use_embeddings)
        self.parser = Parser(self.rig, self.model, self.retriever)
        self.planner = Planner(self.rig, self.prefs, self.session, self.cfg)
        self.executor = Executor(self.cfg, self.session, self.get_rig, self.reload_rig, self.prefs)
        self.executor.planner = self.planner
        self._bad_models: set = set()
        self.parse_ms: list = []
        self.model_loaded_at = time.time()

    def get_rig(self, fresh: bool = False) -> Rig:
        if fresh:
            self.reload_rig()
        return self.rig

    def switch_show(self, path: Path) -> bool:
        """Edit another show file from now on (the one QLC+ has open). Facts keyed by the main show's fixture IDs stay
        with the main show. False when lightai already edits that file."""
        path = Path(path)
        old = Path(self.cfg.project_path)
        try:
            if path.resolve() == old.resolve():
                return False
        except OSError:
            pass
        if self.cfg.main_project_path is None:
            self.cfg.main_project_path = old
        self.cfg.project_path = path
        try:
            self.reload_rig()
        except Exception:
            self.cfg.project_path = old  # a show lightai can't read: keep editing the previous one
            self.reload_rig()
            raise
        s = self.session
        s.last_command = s.last_plan = s.last_action_plan = None
        s.overridden.clear()
        s.gm_changed = False
        s.rated.clear()
        return True

    def reload_rig(self) -> None:
        self.rig = Rig.load(self.cfg)
        self.prefs.palette = list(self.rig.overrides.get("house_palette") or self.prefs.palette)
        # the rig can be updated in place by a write, so compare with what the retriever indexed
        now = [(f.id, f.name, f.path, f.type) for f in self.rig.functions.values() if f.name]
        if now != list(self.retriever.items):
            self.retriever = Retriever.for_rig(self.rig, self.cfg.models_dir, self.use_embeddings)
        self.parser = Parser(self.rig, self.model, self.retriever)
        self.planner.rig = self.rig
        self.planner.router.rig = self.rig

    def maybe_swap_model(self) -> Optional[str]:
        md = self.cfg.current_model_dir()
        if md is None or md.name == self.model.version or md.name in self._bad_models:
            return None
        try:
            model = NluModel(md, threads=self.cfg.ort_threads)
        except Exception:
            self._bad_models.add(md.name)  # a broken promotion is tried once, then the current model stays
            raise
        self.model = model
        self.parser = Parser(self.rig, self.model, self.retriever)
        return md.name

    def parse(self, text: str) -> LightCommand:
        cmd = self.parser.parse(text)
        self.parse_ms.append(cmd.latency_ms)
        self.parse_ms = self.parse_ms[-2000:]
        return cmd

    def plan(self, text: str) -> tuple:
        if AGAIN.match(text):  # 'do it again' after moving a fixture in the 3D stage: the last live aim, worked out again
            p = self.planner.again_aim(text)
            if p is not None:
                from lightai.schema import LightCommand

                cmd = LightCommand(text=text, words=text.split(), intent="create_look", confidence=1.0)
                cmd.ambiguities.append("repeating the last live aim")
                self.session.remember(cmd, p)
                return cmd, p
        m = CORRECTION.match(text)
        prev = self.session.last_command
        has_prev = prev is not None and self.session.last_plan is not None
        if m and has_prev:
            return self.correct(prev, self.session.last_plan, m.group("rest").strip(), text)
        cmd = self.parse(text)
        if cmd.intent == "correction" and has_prev and not cmd.clarify:  # 'i said spots not washes': the model knows it
            return self.correct(prev, self.session.last_plan, "", text, frag=cmd)
        return cmd, self.planner.plan(cmd)

    def correct(self, prev, prev_plan, rest: str, text: str, frag=None) -> tuple:
        """'no, I meant the spots': a fragment replaces the matching part of the previous command; a full command
        replaces it; nothing after the correction asks what was meant. Offers to undo what the previous one changed."""
        from lightai.schema import SlotValue

        if frag is None and rest:
            frag = self.parse(rest)
        if frag is None or (frag.intent == "correction" and not frag.slots):  # 'that's wrong', 'you misunderstood me'
            cmd = self.parse(prev.text)
            cmd.text = text
            cmd.clarify = f"What did you mean instead of '{prev.text}'? Say it again the way you meant it."
            cmd.corrects = prev_plan.plan_id
            plan = self.planner.plan(cmd)
        else:
            short = len((rest or text).split()) <= 4
            fits = bool(frag.slots) and all(s in prev.slots or COMPATIBLE.get(s, set()) & set(prev.slots) for s in frag.slots)
            complete = (frag.intent not in ("none", "feedback", "correction") and frag.confidence >= 0.8 and not frag.clarify
                        and not (short and fits))
            if complete:
                cmd = frag
            else:  # a fragment: keep the previous command, swap in the parts the fragment names
                cmd = self.parse(prev.text)
                replaced = []
                for slot, values in frag.slots.items():
                    target_slot = PREFERRED.get(cmd.intent, {}).get(slot) or (
                        slot if slot in cmd.slots else next(iter(COMPATIBLE.get(slot, set()) & set(cmd.slots)), None))
                    if target_slot is None:
                        continue
                    if target_slot == slot:
                        cmd.slots[slot] = [SlotValue(raw=v.raw, value=v.value) for v in values]
                    else:  # 'swarms' said as fixtures, meant as the model the previous command named
                        cmd.slots[target_slot] = [SlotValue(raw=v.raw, value=normalize_slot(self.rig, target_slot, v.raw, intent=cmd.intent))
                                                  for v in values]
                    replaced.append(target_slot)
                if not replaced:
                    cmd.clarify = f"I don't know which part of '{prev.text}' '{rest}' replaces. Say the whole command."
                cmd.ambiguities.append("replaced " + ", ".join(replaced) if replaced else "nothing replaced")
            cmd.text = text
            cmd.corrects = prev_plan.plan_id
            cmd.ambiguities.append(f"correcting: '{prev.text}'")
            plan = self.planner.plan(cmd)
        undo = self.undo_plan(prev_plan.plan_id)
        followup = dict(plan.followup or {})
        followup.update({"corrects": prev_plan.plan_id, "previous_text": prev.text})
        if undo is not None:
            followup.update({"undo_plan_id": undo.plan_id, "undo_summary": undo.summary})
        plan.followup = followup
        return cmd, plan

    def correct_plan(self, plan_id: str, rest: str) -> tuple:
        """Correct an earlier command from the console's history ('Wrong' / 'What I meant...')."""
        prev_plan = self.session.get(plan_id)
        row = next((h for h in reversed(self.session.history) if h.get("plan_id") == plan_id), None)
        if prev_plan is None or row is None or not row.get("text"):
            raise KeyError(f"unknown or expired plan {plan_id}")
        prev = self.parse(row["text"])
        text = f"what I meant was {rest}" if rest else "that's wrong"
        return self.correct(prev, prev_plan, rest.strip(), text)

    def undo_plan(self, plan_id: str):
        """The plan that takes back what an executed plan created: its looks/shows removed, live changes released."""
        from lightai.schema import Action, Plan

        done = self.session.executed.get(plan_id)
        if not done:
            return None
        edits = [a for a in (done.get("plan_actions") or []) if a.get("op") == "edit_stage"]
        if edits:  # a 3D-stage edit: the exact inverse, planned against the scene as it is now
            from lightai.rig.scene import invert

            return Plan(plan_id=self.session.new_plan_id(), intent="fixture_edit.move", mode="structural",
                        summary="Undo: put the 3D stage back the way it was",
                        actions=[Action(op="edit_stage", args={"stage_path": a["args"]["stage_path"], "changes": invert(a["args"]["changes"])},
                                        describe="take the changes back") for a in reversed(edits)],
                        needs_confirmation=True, show=str(self.cfg.project_path))
        ids, main = [], None
        for r in done.get("results") or []:
            if r.get("op") in ("write_look", "write_design") and r.get("ids") and not r.get("skipped"):
                ids += [i for i in r["ids"] if i not in ids]
                main = main if main is not None else r.get("main_id") or (r.get("shows") or [{}])[0].get("main_id")
        if ids:
            undo = Plan(plan_id=self.session.new_plan_id(), intent="delete_look", mode="structural",
                        summary=f"Undo: remove the {len(ids)} functions the previous command added",
                        actions=[Action(op="delete_look", args={"ids": ids, "main_id": main}, describe="remove them"),
                                 Action(op="reload", args={"strategy": self.cfg.reload_strategy}, describe="reload the show into QLC+")],
                        needs_confirmation=True, show=str(self.cfg.project_path))
        elif done.get("mode") == "live":
            chans = sorted(self.session.overridden)
            if not chans:
                return None
            undo = Plan(plan_id=self.session.new_plan_id(), intent="release", mode="live",
                        summary=f"Undo: hand {len(chans)} overridden channel(s) back to the looks",
                        actions=[Action(op="reset_channels", args={"channels": [list(c) for c in chans]})],
                        show=str(self.cfg.project_path))
        else:
            return None
        self.session.store(undo)
        return undo

    def teach(self, text: str, predicted: dict, corrected: dict, source: str = "console") -> dict:
        from lightai.schema import labels

        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "labels_version": labels()["version"],
               "model_version": self.model.version, "text": text, "predicted": predicted, "corrected": corrected,
               "accepted": True, "source": source}
        path = self.cfg.data_dir / "corrections.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return {"saved": True, "path": str(path)}

    def _claude_exe(self) -> Optional[str]:
        from lightai.design.backend import discover_claude_exe

        try:
            exe = discover_claude_exe(self.cfg)
        except Exception:
            return None
        return str(exe) if exe else None

    def _stage_info(self) -> Optional[dict]:
        st = self.rig.stage
        if st is None:
            return None
        return {"file": st.path.name, "hash": st.hash, "fixtures": len(st.fixtures), "positions_final": bool(self.cfg.positions_final)}

    def health(self) -> dict:
        lat = sorted(self.parse_ms)
        c = self.executor.client
        return {
            "model_version": self.model.version,
            "labels_version": self.model.labels_version,
            "project": str(self.cfg.project_path),
            "main_project": str(self.cfg.main_show()),
            "claude_exe": self._claude_exe(),
            "stage": self._stage_info(),
            "functions": len(self.rig.functions),
            "fixtures": len(self.rig.fixtures),
            "bpm": self.session.bpm,
            "bpm_source": self.session.bpm_source,
            "parse_p50_ms": lat[len(lat) // 2] if lat else None,
            "parse_p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
            "qlc_connected": c.connected,
            "qlc_url": c.connected_url,
            "ws_rtt": c.latency.summary(),
            "overridden_channels": len(self.session.overridden),
            "release_pending": len(self.executor.pending_release),
            "preview_running": bool(self.executor.preview_task and not self.executor.preview_task.done()),
            "calibration": bool(self.session.calibration),
        }
