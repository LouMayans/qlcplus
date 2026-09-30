"""Re-aim after the 3D layout changes: looks and designed shows record the stage version they were aimed with
("stage:<hash>" in facts_used). When the stage file changes, they are rebuilt from what they aim at (places and
orders, never coordinates) in one confirmed plan."""

from __future__ import annotations

from lightai.compiler.sidecar import Sidecar


def _stage_hashes(entry: dict) -> set:
    return {f.split(":", 1)[1] for f in entry.get("facts_used") or [] if f.startswith("stage:")}


def stale_entries(rig) -> dict:
    """Looks and designed shows aimed with an older version of the stage file."""
    st = rig.stage
    side = Sidecar(rig.cfg.sidecar_path)
    if st is None:
        return {"hash": None, "looks": [], "designs": []}
    looks = [e for e in side.looks.values() if (h := _stage_hashes(e)) and st.hash not in h and e.get("main_id") in rig.functions]
    designs = [e for e in side.designs.values() if (h := _stage_hashes(e)) and st.hash not in h and e.get("main_id") in rig.functions]
    return {"hash": st.hash, "looks": looks, "designs": designs}


def reaim_plan(ai):
    """One structural plan that rebuilds every stale look and show, then reloads QLC+ once."""
    from lightai.schema import Action, Plan

    rig, cfg = ai.get_rig(), ai.cfg
    stale = stale_entries(rig)
    actions = [Action(op="update_look", args={"main_id": e["main_id"], "old_ids": e["ids"], "params": e["params"], "in_place": True},
                      describe=f"re-aim look '{e['name']}'") for e in stale["looks"]]
    actions += [Action(op="rewrite_design", args={"main_id": e["main_id"], "old_ids": e["ids"], "spec": e["spec"],
                                                  "text": e.get("text", ""), "job_id": e.get("job_id", "")},
                       describe=f"re-aim show '{e['title']}'") for e in stale["designs"]]
    pid = ai.session.new_plan_id()
    if not actions:
        plan = Plan(plan_id=pid, intent="reaim", mode="info", show=str(cfg.project_path),
                    summary="Nothing to re-aim: every look and show already uses the current 3D layout.")
    else:
        actions.append(Action(op="reload", args={"strategy": cfg.reload_strategy}, describe="reload the show into QLC+"))
        plan = Plan(plan_id=pid, intent="reaim", mode="structural", needs_confirmation=True, show=str(cfg.project_path),
                    summary=f"Re-aim {len(stale['looks'])} look(s) and {len(stale['designs'])} show(s) for the updated 3D layout",
                    actions=actions)
    ai.session.store(plan)
    return plan
