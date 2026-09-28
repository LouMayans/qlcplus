"""SaveFile/lightai-looks.json: what lightai generated, from which sentence and which rig facts.

It makes re-running the same request a no-op, lets update_look replace a look in place,
and lets a corrected rig fact find every look that has to be recompiled.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lightai.compiler.spec import Look


def look_hash(look: Look) -> str:
    p = look.params.to_dict()
    p.pop("name", None)
    compiled = [(f.type, sorted((str(k), v) for k, v in f.values.items()), f.efx,
                 [(e.id, e.start_offset, e.direction) for e in f.efx_fixtures]) for f in look.functions]
    blob = json.dumps({"recipe": look.recipe, "params": p, "compiled": compiled}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


class Sidecar:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = {"version": 1, "looks": {}}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.data = {"version": 1, "looks": {}}
        self.data.setdefault("looks", {})

    @property
    def looks(self) -> dict:
        return self.data["looks"]

    def find_hash(self, h: str) -> Optional[dict]:
        for entry in self.looks.values():
            if entry.get("hash") == h:
                return entry
        return None

    def add(self, look: Look, text: str = "", source: str = "") -> dict:
        entry = {
            "main_id": look.main_id,
            "name": look.name,
            "recipe": look.recipe,
            "ids": look.ids,
            "params": look.params.to_dict(),
            "hash": look_hash(look),
            "text": text,
            "source": source,
            "facts_used": look.facts_used,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.looks[str(look.main_id)] = entry
        return entry

    def remove(self, main_id: int) -> Optional[dict]:
        return self.looks.pop(str(main_id), None)

    def using_fact(self, fact_prefix: str) -> list:
        return [e for e in self.looks.values() if any(f.startswith(fact_prefix) for f in e.get("facts_used", []))]

    def prune(self, existing_ids: set) -> list:
        gone = [k for k, e in self.looks.items() if e.get("main_id") not in existing_ids]
        for k in gone:
            self.looks.pop(k, None)
        return gone

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)
