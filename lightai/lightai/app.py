"""One object that wires config, rig, model, parser, prefs, session, planner and executor.

Shared by the CLI REPL and the HTTP API so both behave identically.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lightai.config import Config, load_config
from lightai.exec.executor import Executor
from lightai.nlu.infer import NluModel
from lightai.nlu.pipeline import Parser
from lightai.nlu.retriever import Retriever
from lightai.planner import Planner
from lightai.prefs import Prefs
from lightai.rig.model import Rig
from lightai.schema import LightCommand, Plan
from lightai.session import Session


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
        cmd = self.parse(text)
        return cmd, self.planner.plan(cmd)

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

    def health(self) -> dict:
        lat = sorted(self.parse_ms)
        c = self.executor.client
        return {
            "model_version": self.model.version,
            "labels_version": self.model.labels_version,
            "project": str(self.cfg.project_path),
            "main_project": str(self.cfg.main_show()),
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
