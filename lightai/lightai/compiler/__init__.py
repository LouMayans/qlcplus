"""Look compiler: LookParams -> validated QLC+ functions -> safely written project file."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lightai.compiler.recipes import RECIPES, build_look
from lightai.compiler.sidecar import Sidecar, look_hash
from lightai.compiler.spec import Look, LookParams
from lightai.compiler.validate import validate_specs, validate_workspace
from lightai.compiler.writer import write_workspace
from lightai.compiler.xmlgen import to_element
from lightai.rig.model import Rig

__all__ = ["RECIPES", "Look", "LookParams", "compile_look", "add_look_to_workspace", "write_look", "write_design", "CompileError"]


class CompileError(Exception):
    def __init__(self, message: str, errors: Optional[list] = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def compile_look(rig: Rig, params: LookParams, start_id: Optional[int] = None, allow_ids: Optional[set] = None) -> Look:
    """allow_ids: existing function IDs this look may reuse (recompiling a look in place)."""
    look = build_look(rig, params, start_id=start_id)
    errors, warnings = validate_specs(rig, look.groups + look.functions, set(rig.functions) - set(allow_ids or ()))
    if errors:
        raise CompileError(f"generated look failed validation: {errors[0]}", errors)
    for w in warnings:
        look.assumptions.append({"fact": w, "source": "validator warning"})
    return look


def add_look_to_workspace(rig: Rig, look: Look, replace_ids: Optional[list] = None) -> None:
    ws = rig.ws
    for fid in replace_ids or []:
        if ws.function_el(fid) is not None:
            ws.remove_function(fid)
    for group in look.groups:  # before the functions: QLC+ loads in order (see Workspace.add_fixture_group)
        ws.add_fixture_group(to_element(group))
    for spec in look.functions:
        ws.add_function(to_element(spec))
    errors, _ = validate_workspace(ws)
    new_errors = [e for e in errors if "Monitor" not in e]
    if new_errors:
        raise CompileError(f"workspace invalid after adding the look: {new_errors[0]}", new_errors)


def write_look(rig: Rig, look: Look, path: Optional[Path] = None, text: str = "", source: str = "") -> dict:
    """Add the look to the rig's workspace, back up + write the file, record it in the sidecar."""
    cfg = rig.cfg
    target = Path(path) if path else cfg.project_path
    sidecar = Sidecar(cfg.sidecar_path if target.resolve() == Path(cfg.project_path).resolve() else target.parent / "lightai-looks.json")
    existing = sidecar.find_hash(look_hash(look))
    if existing and existing.get("main_id") in rig.functions:
        return {"skipped": True, "reason": "identical look already exists", "main_id": existing["main_id"], "name": existing["name"]}
    add_look_to_workspace(rig, look)
    result = write_workspace(rig.ws, target, target.parent / "backups", cfg.backups_keep)
    sidecar.add(look, text=text, source=source)
    sidecar.save()
    rig.functions.update({f.id: f for f in rig.ws.functions() if f.id in set(look.ids)})
    result.update({"skipped": False, "main_id": look.main_id, "name": look.name, "ids": look.ids})
    return result


def write_design(rig: Rig, shows: list, text: str = "", source: str = "design", job_id: str = "") -> dict:
    """Add every function of the composed shows, then one backup + one write + one sidecar entry per show."""
    cfg = rig.cfg
    written: set = set()
    for group in (g for show in shows for g in show.groups):
        if group.id not in written:
            written.add(group.id)
            rig.ws.add_fixture_group(to_element(group))
    for show in shows:
        for spec in show.functions:
            rig.ws.add_function(to_element(spec))
    errors, _ = validate_workspace(rig.ws)
    new_errors = [e for e in errors if "Monitor" not in e]
    if new_errors:
        raise CompileError(f"workspace invalid after adding the design: {new_errors[0]}", new_errors)
    result = write_workspace(rig.ws, cfg.project_path, cfg.backups_dir, cfg.backups_keep)
    sidecar = Sidecar(cfg.sidecar_path)
    for show in shows:
        sidecar.add_design(show, text=text, source=source, job_id=job_id)
    sidecar.save()
    ids = [i for show in shows for i in show.ids]
    rig.functions.update({f.id: f for f in rig.ws.functions() if f.id in set(ids)})
    result.update({"skipped": False, "ids": ids,
                   "shows": [{"main_id": s.main_id, "title": s.title, "kind": s.kind, "functions": len(s.ids)} for s in shows]})
    return result
