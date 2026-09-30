"""Runs Plans: live WebSocket actions, previews, calibration, safe file edits + reload, feedback."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Callable, Optional

from lightai.compiler import compile_look, write_look
from lightai.compiler.spec import LookParams
from lightai.compiler.writer import write_workspace
from lightai.config import Config
from lightai.exec.http import fork_version, load_project_file, open_project_file, project_file, reload_project, supports_fork_commands
from lightai.exec.wsclient import QlcClient, QlcError
from lightai.feedback import FeedbackRouter
from lightai.rig.facts import LearnedFacts
from lightai.rig.qxw import Workspace
from lightai.schema import OutcomeFeedback, Plan
from lightai.session import Calibration, Session

ROLE_WORDS = {
    "dimmer": "dimmer", "intensity": "dimmer", "brightness": "dimmer",
    "strobe": "shutter", "shutter": "shutter", "flash": "shutter",
    "color": "color_wheel", "colour": "color_wheel", "wheel": "color_wheel",
    "pan": "pan", "tilt": "tilt", "gobo": "gobo", "prism": "prism", "focus": "focus", "zoom": "zoom",
    "speed": "pt_speed", "frost": "frost", "red": "red", "green": "green", "blue": "blue", "white": "white", "amber": "amber",
    "macro": "color_macro", "mode": "mode", "reset": "maintenance", "nothing": "none", "lamp": "maintenance",
}


def color_in(rig, text: str):
    """First color named anywhere in a free-text answer ("no, that's hot pink" -> pink)."""
    words = re.findall(r"[a-z]+", text.lower())
    for n in (2, 1):
        for i in range(len(words) - n + 1):
            c = rig.colors.canonical(" ".join(words[i:i + n]))
            if c:
                return c
    return None


def _same_file(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


class Executor:
    def __init__(self, cfg: Config, session: Session, get_rig: Callable, on_rig_change: Callable, prefs, client: Optional[QlcClient] = None) -> None:
        self.cfg = cfg
        self.session = session
        self.get_rig = get_rig
        self.on_rig_change = on_rig_change
        self.prefs = prefs
        self.client = client or QlcClient.from_config(cfg, timeout=1.0)
        self.preview_task: Optional[asyncio.Task] = None
        self._struct_lock = asyncio.Lock()
        self.ramp_task: Optional[asyncio.Task] = None
        self.preview_channels: set = set()
        self.pending_release: set = set()  # overrides whose reset failed (QLC+ dropped); released after reconnecting
        self._cal_lock = asyncio.Lock()
        self.planner = None  # set by LightAI: feedback applies taste corrections to proposals when it runs
        self._preview_lock = asyncio.Lock()  # previews start/stop one at a time, or overrides get orphaned

    async def ensure_client(self) -> QlcClient:
        if not self.client.connected:
            await self.client.ensure_connected()
            if self.pending_release:
                await self._flush_pending_release()
        return self.client

    async def _flush_pending_release(self) -> None:
        pending, self.pending_release = sorted(self.pending_release), set()
        for u, ch in pending:
            try:
                await self.client.reset_channel(u, ch)
                self.session.overridden.discard((u, ch))
            except QlcError:
                self.pending_release.add((u, ch))

    async def execute(self, plan: Plan, confirm: bool = False, allow_running_reload: bool = False, wait_preview: bool = False,
                      allow_other_show: bool = False) -> dict:
        t0 = time.perf_counter()
        if plan.mode == "clarify":
            return {"ok": False, "clarify": plan.summary, "plan_id": plan.plan_id}
        if plan.show and not _same_file(plan.show, self.cfg.project_path):  # its IDs and addresses belong to that show
            return {"ok": False, "plan_id": plan.plan_id,
                    "clarify": f"That was planned for {Path(plan.show).name}, but lightai now edits "
                               f"{Path(self.cfg.project_path).name}. Type it again."}
        if plan.needs_confirmation and not confirm:
            return {"ok": False, "needs_confirmation": True, "plan_id": plan.plan_id, "summary": plan.summary}
        urgent = plan.intent in ("blackout",)
        if plan.intent != "feedback" and not urgent:
            await self.stop_preview()
        if urgent:
            try:
                return await self._execute(plan, t0, allow_running_reload, wait_preview, False)
            finally:
                await self.stop_preview()
        structural = plan.mode == "structural" and any(a.op == "reload" for a in plan.actions)
        if structural:
            await self._struct_lock.acquire()
        try:
            return await self._execute(plan, t0, allow_running_reload, wait_preview, structural, allow_other_show)
        finally:
            if structural:
                self._struct_lock.release()

    async def _execute(self, plan: Plan, t0: float, allow_running_reload: bool, wait_preview: bool, structural: bool,
                       allow_other_show: bool = False) -> dict:
        self._was_running = []
        self._switch_show = None
        if structural:
            other = None
            try:
                c = await self.ensure_client()
                other = await self._other_show(c)
                if other is None:  # functions running in another show say nothing about this one
                    rig = self.get_rig()
                    running = await c.running_functions(sorted(rig.functions))
                    # only top-level functions: a parent restarts its own parts. Restarting a part ourselves gives it a second
                    # start source, and stopping the look by name later leaves that part running.
                    parts = {r for f in running if f in rig.functions for r in rig.functions[f].refs}
                    self._was_running = [f for f in running if f not in parts]
            except QlcError:
                self._was_running = []
            if other is not None and not allow_other_show:  # checked before anything is written
                what = f"'{other['path']}'" if other["path"] else "a new, unsaved show"
                lost = " Its unsaved changes would be lost." if other["modified"] else ""
                name = Path(self.cfg.project_path).name
                return {"ok": False, "plan_id": plan.plan_id, "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                        "results": [{"op": "preflight", "blocked": True, "other_show": True, "open": other["path"],
                                     "modified": other["modified"], "target": name,
                                     "note": f"QLC+ has {what} open, but lightai edits {name}. Nothing was changed. "
                                             f"Applying changes {name} and opens it in QLC+ in place of that show.{lost}"}]}
            self._switch_show = other
            if self._was_running and not allow_running_reload:
                rig = self.get_rig()
                names = [rig.functions[i].name for i in self._was_running[:6] if i in rig.functions]
                return {"ok": False, "plan_id": plan.plan_id, "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                        "results": [{"op": "preflight", "blocked": True, "running": self._was_running,
                                     "note": f"{len(self._was_running)} function(s) running ({', '.join(names)}). Reloading restarts the show; "
                                             "they will be started again afterwards. Nothing was written yet. Confirm the reload to continue."}]}
        results = []
        ok = True
        wrote = None
        for action in plan.actions:
            handler = getattr(self, "_op_" + action.op, None)
            if handler is None:
                results.append({"op": action.op, "error": "unknown op"})
                ok = False
                break
            if action.op == "reload" and wrote is False:
                results.append({"op": "reload", "skipped": True, "note": "the show file did not change, so QLC+ was not reloaded"})
                continue
            try:
                res = await handler(action.args, plan, allow_running_reload=allow_running_reload, wait_preview=wait_preview)
            except (QlcError, OSError, ValueError, KeyError) as exc:
                res = {"error": f"{type(exc).__name__}: {exc}"}
                if action.op == "reload" and wrote:
                    res["note"] = ("the show file was saved (a backup was made) but QLC+ could not reload it; "
                                   "reload the show in QLC+ once it is reachable")
            except Exception as exc:
                import traceback

                traceback.print_exc()
                res = {"error": f"unexpected {type(exc).__name__}: {exc}"}
            res["op"] = action.op
            if action.op in ("write_look", "edit_fixture", "update_look", "delete_look", "add_widget", "add_fixture", "readdress"):
                wrote = bool(wrote) or not (res.get("skipped") or res.get("existing"))
                if action.op == "write_look" and res.get("skipped") and not wrote:
                    # identical to the FILE is not the same as loaded in QLC+: an earlier reload may have been blocked
                    try:
                        if res.get("main_id") not in await (await self.ensure_client()).functions():
                            wrote = True
                            res["note"] = "the look is in the show file but QLC+ has not loaded it yet; reloading"
                    except QlcError:
                        pass
            results.append(res)
            if res.get("error") or res.get("blocked"):
                ok = False
                break
        if ok:
            self.session.record_executed(plan, results)
        return {"ok": ok, "plan_id": plan.plan_id, "results": results, "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2)}

    async def _op_write_look(self, args: dict, plan: Plan, **_) -> dict:
        rig = self.get_rig(fresh=True)
        params = LookParams(**args["params"])
        look = compile_look(rig, params)
        note = None
        if args.get("expected_ids") and look.ids != args["expected_ids"]:
            note = f"IDs changed because the show file changed since planning: now {look.ids[0]}-{look.ids[-1]}"
        text = ((plan.followup or {}).get("command") or {}).get("text", "")
        res = write_look(rig, look, self.cfg.project_path, text=text, source=plan.plan_id)
        if plan.look is not None:
            plan.look["main_id"] = res.get("main_id", look.main_id)
        self.on_rig_change()
        if note:
            res["note"] = note
        return res

    async def _running(self, ids: list) -> list:
        c = await self.ensure_client()
        return await c.running_functions(ids)

    async def _other_show(self, c: QlcClient) -> Optional[dict]:
        """The show QLC+ has open when it is not lightai's file ('' = a new, unsaved show). Stock QLC+ can't tell: None."""
        if not await supports_fork_commands(c):
            return None
        info = await project_file(c)
        if not info:
            return None
        path = info.get("path") or ""
        if path and Path(path).resolve() == Path(self.cfg.project_path).resolve():
            return None
        return {"path": path, "modified": bool(info.get("modified"))}

    async def _open_main_show(self, c: QlcClient, other: dict) -> dict:
        """The operator confirmed: QLC+ swaps the show it has open for lightai's file."""
        was = other["path"] or "a new, unsaved show"
        if await fork_version(c) >= 2:
            r = await open_project_file(c, self.cfg.project_path, force=True)
            return {"strategy": "openProjectFile", "loaded": bool(r.get("loaded")), "switched_from": was,
                    **({"note": r["note"]} if r.get("note") else {})}
        if not other["path"]:  # fork v1: loadProjectFile opens the file over an untitled show
            return dict(await load_project_file(c, self.cfg.project_path, force=True), switched_from=was)
        return {"strategy": "loadProjectFile", "loaded": False, "blocked": True,
                "note": f"this QLC+ build can't switch shows; open {self.cfg.project_path} in QLC+ (File > Open)"}

    async def _op_reload(self, args: dict, plan: Plan, allow_running_reload: bool = False, **_) -> dict:
        strategy = args.get("strategy") or self.cfg.reload_strategy
        if strategy == "none":
            return {"strategy": "none", "note": f"file written; open {self.cfg.project_path} in QLC+ to see it"}
        try:
            c = await self.ensure_client()
        except QlcError as exc:
            return {"strategy": strategy, "loaded": False, "error": f"QLC+ not reachable ({exc})",
                    "note": "the show file was saved (a backup was made) but QLC+ could not reload it; reload the show in QLC+"}
        running = list(getattr(self, "_was_running", []) or [])
        switch = getattr(self, "_switch_show", None)
        if switch is not None:
            res = await self._open_main_show(c, switch)
        else:
            res = await reload_project(c, self.cfg.project_path, strategy, force=allow_running_reload)
        if res.get("blocked"):
            return res
        if not res.get("loaded"):
            res["error"] = "QLC+ did not confirm the reload (isProjectLoaded never returned true)"
            return res
        funcs = await c.functions()
        restarted = []
        for fid in running:
            if fid in funcs:
                await c.set_function(fid, True)
                restarted.append(fid)
        if restarted:
            await c.barrier()
        res["restarted"] = restarted
        res["functions_in_qlc"] = len(funcs)
        if restarted and self.session.bpm:
            try:
                rt = await self._op_retime_looks({"bpm": self.session.bpm}, plan)
                if rt.get("retimed"):
                    res["retimed"] = rt["retimed"]
            except QlcError:
                pass
        if plan.look and plan.look.get("name"):
            res["look_visible"] = plan.look["name"] in funcs.values()
        return res

    async def _op_edit_fixture(self, args: dict, plan: Plan, **_) -> dict:
        ws = Workspace.load(self.cfg.project_path)
        fid = int(args["fixture_id"])
        if args["kind"] == "rotate":
            change = ws.set_fxitem(fid, rotation=int(args["rotation"]))
        elif args["kind"] == "move":
            change = ws.set_fxitem(fid, x=float(args["x"]), y=float(args["y"]))
        elif args["kind"] == "rename":
            change = ws.rename_fixture(fid, str(args["name"]))
        else:
            raise ValueError(f"unknown fixture edit {args['kind']}")
        if args["kind"] in ("rotate", "move") and change.get("before") == change.get("after"):
            return {"fixture_id": fid, "change": change, "skipped": True, "note": "nothing changes; the show was not rewritten"}
        res = write_workspace(ws, self.cfg.project_path, self.cfg.backups_dir, self.cfg.backups_keep)
        self.on_rig_change()
        return {"fixture_id": fid, "change": change, "backup": res["backup"]}

    async def _op_update_look(self, args: dict, plan: Plan, **_) -> dict:
        from lightai.compiler.sidecar import Sidecar
        from lightai.compiler.xmlgen import to_element

        rig = self.get_rig(fresh=True)
        old = [i for i in args["old_ids"] if i in rig.functions]
        params = LookParams(**args["params"])
        if args.get("in_place"):
            look = compile_look(rig, params, start_id=min(args["old_ids"]), allow_ids=set(args["old_ids"]))
        else:
            look = compile_look(rig, params)
        ws = rig.ws
        for group in look.groups:  # a new pixel group goes before the functions (Workspace.add_fixture_group)
            ws.add_fixture_group(to_element(group))
        if args.get("in_place") and look.ids == args["old_ids"]:
            for spec in look.functions:
                ws.replace_function(spec.id, to_element(spec))
            rebound = 0
        else:
            for fid in old:
                ws.remove_function(fid)
            for spec in look.functions:
                ws.add_function(to_element(spec))
            rebound = ws.rebind_buttons(args["main_id"], look.main_id)
        res = write_workspace(ws, self.cfg.project_path, self.cfg.backups_dir, self.cfg.backups_keep)
        side = Sidecar(self.cfg.sidecar_path)
        prev = side.remove(args["main_id"]) or {}
        side.add(look, text=prev.get("text", "") + " | updated", source=plan.plan_id)
        side.save()
        self.on_rig_change()
        return {"main_id": look.main_id, "ids": look.ids, "name": look.name, "in_place": bool(args.get("in_place") and look.ids == args["old_ids"]),
                "buttons_rebound": rebound, "backup": res["backup"]}

    async def _op_write_design(self, args: dict, plan: Plan, **_) -> dict:
        """Compose the chosen shows of a design against the show as it is now, and write them in one go."""
        from lightai.compiler import write_design
        from lightai.design.composer import compose_design
        from lightai.design.spec import DesignResult

        rig = self.get_rig(fresh=True)
        design = DesignResult.model_validate(args["design"])
        shows = compose_design(rig, design, show_indexes=args.get("shows"), bpm=self.session.bpm)
        res = write_design(rig, shows, text=args.get("text", ""), source="design", job_id=args.get("job_id", ""))
        self.on_rig_change()
        return res

    async def _op_rewrite_design(self, args: dict, plan: Plan, **_) -> dict:
        """Rebuild one designed show from its stored design (after the 3D layout changed); buttons follow it."""
        from lightai.compiler import write_design
        from lightai.compiler.sidecar import Sidecar
        from lightai.design.composer import compose_design
        from lightai.design.spec import DesignResult

        rig = self.get_rig(fresh=True)
        ws = rig.ws
        for fid in [i for i in args["old_ids"] if ws.function_el(i) is not None]:
            ws.remove_function(fid)
            rig.functions.pop(fid, None)
        side = Sidecar(self.cfg.sidecar_path)
        side.remove(args["main_id"])
        side.save()
        shows = compose_design(rig, DesignResult(shows=[args["spec"]]), bpm=self.session.bpm)
        res = write_design(rig, shows, text=args.get("text", ""), source="reaim", job_id=args.get("job_id", ""))
        rebound = rig.ws.rebind_buttons(args["main_id"], shows[0].main_id)
        if rebound:
            from lightai.compiler.writer import write_workspace

            write_workspace(rig.ws, self.cfg.project_path, self.cfg.backups_dir, self.cfg.backups_keep)
        self.on_rig_change()
        return dict(res, buttons_rebound=rebound, old_main_id=args["main_id"], main_id=shows[0].main_id)

    async def _op_edit_stage(self, args: dict, plan: Plan, **_) -> dict:
        """Edit the room's 3D stage: re-read the file, make the planned changes (refused when the scene changed since
        planning), keep a backup, and save through QLC+ (saveStage: every open /stage page reloads) when QLC+ has this
        show open with the 3D stage; otherwise write the file."""
        from lightai.rig import scene as sc

        path = Path(args["stage_path"])
        new = sc.apply_changes(sc.read_doc(path), args["changes"])
        saved = sc.backup(path, self.cfg.data_dir)
        how, rev, note = "file", None, ""
        try:
            c = await self.ensure_client()
        except QlcError:
            c = None
        if c is not None:
            try:
                if await fork_version(c) >= 3 and await self._other_show(c) is None:
                    reply = await c.request("QLC+API|saveStage|" + json.dumps(new, ensure_ascii=False), reply_prefix="QLC+API|saveStage",
                                            timeout=10.0)
                    parts = reply.split("|")
                    if len(parts) >= 3 and parts[2] == "OK":
                        how, rev = "qlc", int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
                    else:
                        note = f"QLC+ didn't save it ({'|'.join(parts[2:])[:80]}); wrote the file instead"
            except QlcError as exc:
                note = f"QLC+ didn't answer ({exc}); wrote the file instead"
        if how == "file":
            sc.write_doc(path, new)
            note = note or "wrote the file (no QLC+ with the 3D stage has this show open): reload an open 3D page"
        self.on_rig_change()
        return {"saved": how, "rev": rev, "changes": len(args["changes"]), "backup": str(saved) if saved else None,
                **({"note": note} if note else {})}

    async def _op_aim_live(self, args: dict, plan: Plan, **_) -> dict:
        """Aim fixtures live (rig/aim_live.py): a beam reference is followed from the reference fixture's pan/tilt as
        QLC+ outputs them now, down to the floor; the values are live overrides until 'release'."""
        from lightai.rig import aim_live
        from lightai.rig.scene import fmt_pos

        rig = self.get_rig(fresh=True)
        st = rig.stage
        if st is None:
            raise QlcError("this show has no 3D stage file")
        c = await self.ensure_client()
        target = args["target"]
        point, note = None, ""
        if target.get("beam_of") is not None:
            ref = rig.fixtures[int(target["beam_of"])]
            pan16, tilt16 = await aim_live.read_pan_tilt(c, ref)
            point = aim_live.beam_floor_point(st, ref, pan16, tilt16)
            if point is None:
                raise QlcError(f"{ref.name}'s beam doesn't reach the floor right now (it points up or level)")
            note = f"{ref.name}'s beam lands at {fmt_pos(point, st.anchor)}"
        elif target.get("place"):
            point = st.target_point(target["place"])
            if point is None:
                raise QlcError(f"'{target['place']}' is not a place in the 3D stage")
        elif target.get("point"):
            point = tuple(float(v) for v in target["point"])[:3]
        aim = "down" if target.get("down") else "point:" + ",".join(f"{v:.3f}" for v in point)
        chans, aimed, skipped = aim_live.aim_channels(rig, [rig.fixtures[int(f)] for f in args["fixtures"]], aim,
                                                      args.get("color") or "white", float(args.get("intensity") or 1.0))
        for u, ch, v in chans:
            await c.set_channel(int(u), int(ch), int(v))
            self.session.overridden.add((int(u), int(ch)))
        confirmed = None
        if chans:  # a read-back after the writes: they are sent without replies, and QLC+ answers in order
            u, ch, v = chans[-1]
            got = await c.channel_values(int(u), int(ch), 1)
            confirmed = bool(got) and got[0]["value"] == int(v)
        self.session.last_aim = {k: v for k, v in args.items()}
        res = {"aimed": aimed, "channels": len(chans), "confirmed": confirmed, "target": [round(v, 2) for v in point] if point else "down",
               "target_shown": fmt_pos(point, st.anchor) if point else "straight down"}
        if skipped:
            res["skipped"] = skipped
        if note:
            res["note"] = note
        return res

    async def _op_start_rotation(self, args: dict, plan: Plan, **_) -> dict:
        """Rotate shows through the night on the QLC+ side (LOOP|SET/INTERVAL/START): it keeps going with no browser."""
        c = await self.ensure_client()
        fids = ",".join(str(int(f)) for f in args["main_ids"])
        await c.request(f"LOOP|SET|{fids}", reply_prefix="LOOP|STATE", timeout=3.0)
        await c.request(f"LOOP|INTERVAL|{max(10, int(round(float(args['minutes']) * 60)))}", reply_prefix="LOOP|STATE", timeout=3.0)
        return self._loop_state(await c.request("LOOP|START", reply_prefix="LOOP|STATE", timeout=3.0))

    async def _op_stop_rotation(self, args: dict, plan: Plan, **_) -> dict:
        c = await self.ensure_client()
        return self._loop_state(await c.request("LOOP|STOP", reply_prefix="LOOP|STATE", timeout=3.0))

    @staticmethod
    def _loop_state(reply: str) -> dict:
        p = reply.split("|")
        if len(p) < 6:
            raise QlcError(f"unexpected LOOP reply: {reply[:80]}")
        return {"running": p[2] == "1", "interval_s": int(p[3] or 0), "current": int(p[4] or 0),
                "main_ids": [int(x) for x in p[5].split(",") if x.strip()]}

    async def _op_delete_look(self, args: dict, plan: Plan, **_) -> dict:
        from lightai.compiler.sidecar import Sidecar

        rig = self.get_rig(fresh=True)
        ws = rig.ws
        ids = [i for i in args["ids"] if ws.function_el(i) is not None]
        buttons = ws.remove_buttons_for(set(ids))
        for fid in ids:
            ws.remove_function(fid)
        res = write_workspace(ws, self.cfg.project_path, self.cfg.backups_dir, self.cfg.backups_keep)
        side = Sidecar(self.cfg.sidecar_path)
        side.remove(args["main_id"])
        side.save()
        self.on_rig_change()
        return {"deleted_functions": ids, "deleted_buttons": buttons, "backup": res["backup"]}

    async def _op_add_widget(self, args: dict, plan: Plan, **_) -> dict:
        rig = self.get_rig(fresh=True)
        if int(args["function_id"]) not in rig.functions:
            raise ValueError(f"function {args['function_id']} is not in the show")
        info = rig.ws.add_vc_button(int(args["function_id"]), str(args["caption"]))
        if info.get("existing"):
            return {"note": f"a button for this function already exists (widget {info['widget_id']})", **info}
        res = write_workspace(rig.ws, self.cfg.project_path, self.cfg.backups_dir, self.cfg.backups_keep)
        self.on_rig_change()
        return {**info, "backup": res["backup"]}

    async def _op_add_fixture(self, args: dict, plan: Plan, **_) -> dict:
        from lightai.compiler.validate import validate_workspace

        rig = self.get_rig(fresh=True)
        fixtures = args.get("fixtures") or [args]
        for f in fixtures:
            if int(f["id"]) in rig.fixtures:
                raise ValueError(f"fixture ID {f['id']} is taken; plan again")
            rig.ws.add_fixture_element(int(f["id"]), f["name"], f["manufacturer"], f["model"], f["mode"],
                                       int(f["universe"]), int(f["address"]), int(f["channels"]))
        errors, _ = validate_workspace(rig.ws)
        errors = [e for e in errors if "overlaps" in e or "past channel 512" in e]
        if errors:
            raise ValueError(errors[0])
        res = write_workspace(rig.ws, self.cfg.project_path, self.cfg.backups_dir, self.cfg.backups_keep)
        self.on_rig_change()
        ids = [int(f["id"]) for f in fixtures]
        return {"fixture_id": ids[0], "fixture_ids": ids, "backup": res["backup"]}

    async def _op_readdress(self, args: dict, plan: Plan, **_) -> dict:
        from lightai.compiler.validate import validate_workspace

        ws = Workspace.load(self.cfg.project_path)
        changes = [ws.readdress_fixture(int(m["fixture_id"]), int(m["universe"]), int(m["address"])) for m in args["moves"]]
        errors, _ = validate_workspace(ws)
        errors = [e for e in errors if "overlaps" in e or "past channel 512" in e]
        if errors:
            raise ValueError(errors[0])
        res = write_workspace(ws, self.cfg.project_path, self.cfg.backups_dir, self.cfg.backups_keep)
        self.on_rig_change()
        return {"moved": changes, "backup": res["backup"]}

    async def _op_set_channels(self, args: dict, plan: Plan, **_) -> dict:
        c = await self.ensure_client()
        t0 = time.perf_counter()
        for u, ch, v in args["channels"]:
            await c.set_channel(int(u), int(ch), int(v))
            self.session.overridden.add((int(u), int(ch)))
        confirmed = None
        if args["channels"]:
            u, ch, v = args["channels"][0]
            got = await c.channel_values(int(u), int(ch), 1)
            confirmed = bool(got) and got[0]["value"] == int(v)
        res = {"channels": len(args["channels"]), "confirmed": confirmed, "send_ms": round((time.perf_counter() - t0) * 1000.0, 3)}
        held = await self._priority_holders({(int(u), int(ch)) for u, ch, _ in args["channels"]})
        if held:
            res["confirmed"] = False
            res["held_by"] = held
            res["warning"] = self._held_note(held)
        return res

    async def _op_reset_channels(self, args: dict, plan: Plan, **_) -> dict:
        c = await self.ensure_client()
        chans = [(int(u), int(ch)) for u, ch in args["channels"]]
        flagged = set()
        for uni in sorted({u for u, _ in chans}):
            chs = [ch for u, ch in chans if u == uni]
            lo, hi = min(chs), max(chs)
            for row in await c.channel_values(uni, lo, hi - lo + 1):
                if row["override"]:
                    flagged.add((uni, row["channel"]))
        n = 0
        for u, ch in chans:
            if (u, ch) in flagged:
                await c.reset_channel(u, ch)
                n += 1
            self.session.overridden.discard((u, ch))
        return {"released": n, "checked": len(chans)}

    async def _op_reset_universes(self, args: dict, plan: Plan, **_) -> dict:
        c = await self.ensure_client()
        done = []
        for u in args["universes"]:
            flagged = [r["channel"] for r in await c.channel_values(int(u), 1, 512) if r["override"]]
            if len(flagged) > 32:
                await c.reset_universe(int(u))
            else:
                for ch in flagged:
                    await c.reset_channel(int(u), int(ch))
            done.append({"universe": int(u), "released": len(flagged)})
        self.session.overridden.clear()
        return {"universes": args["universes"], "released": done}

    async def _op_grand_master(self, args: dict, plan: Plan, **_) -> dict:
        c = await self.ensure_client()
        await c.grand_master(int(args["value"]))
        await c.barrier()
        self.session.gm_changed = int(args["value"]) != 255
        return {"grand_master": int(args["value"])}

    async def _op_function(self, args: dict, plan: Plan, **_) -> dict:
        c = await self.ensure_client()
        t0 = time.perf_counter()
        await c.set_function(int(args["id"]), bool(args["on"]))
        status = None
        for _ in range(10):
            await asyncio.sleep(0.02)
            status = await c.function_status(int(args["id"]))
            if (status == "Running") == bool(args["on"]):
                break
        else:
            await c.set_function(int(args["id"]), bool(args["on"]))
            await asyncio.sleep(0.1)
            status = await c.function_status(int(args["id"]))
            if (status == "Running") != bool(args["on"]):
                return {"id": args["id"], "status": status, "error": f"QLC+ still reports '{status}' (it ignores a start while the function is stopping)"}
        return {"id": args["id"], "status": status, "confirmed_ms": round((time.perf_counter() - t0) * 1000.0, 2)}

    async def _op_stop_all(self, args: dict, plan: Plan, **_) -> dict:
        c = await self.ensure_client()
        rig = self.get_rig()
        running = await c.running_functions(sorted(rig.functions))
        for fid in running:
            await c.set_function(fid, False)
        await c.barrier()
        return {"stopped": running}

    async def _op_query_status(self, args: dict, plan: Plan, **_) -> dict:
        rig = self.get_rig()
        running = await self._running(args["ids"])
        rows = [{"id": i, "name": rig.functions[i].name if i in rig.functions else "?", "type": rig.functions[i].type if i in rig.functions else "?"} for i in running]
        return {"running": rows, "checked": len(args["ids"])}

    async def _op_set_bpm(self, args: dict, plan: Plan, **_) -> dict:
        self.session.set_bpm(float(args["bpm"]), "typed")
        return {"bpm": self.session.bpm}

    async def _op_retime_looks(self, args: dict, plan: Plan, **_) -> dict:
        """Recompile running AI looks at the new BPM and push only the speeds (fork command, no reload)."""
        from lightai.compiler.sidecar import Sidecar
        from lightai.exec.http import set_function_speed, supports_fork_commands

        try:
            c = await self.ensure_client()
        except QlcError as exc:
            return {"retimed": [], "note": f"QLC+ not reachable ({exc})"}
        if not await supports_fork_commands(c):
            return {"retimed": [], "note": "live retiming needs the lightai QLC+ fork build (setFunctionSpeed)"}
        rig = self.get_rig()
        side = Sidecar(self.cfg.sidecar_path)
        done = []
        for entry in side.looks.values():
            p = dict(entry.get("params") or {})
            main = entry.get("main_id")
            if p.get("period_ms") or main not in rig.functions or not entry.get("ids"):
                continue
            if p.get("recipe") in ("color_wash", "strobe", "blackout_kill"):
                continue
            if await c.function_status(main) != "Running":
                continue
            p["bpm"] = float(args["bpm"])
            try:
                look = compile_look(rig, LookParams(**p), start_id=min(entry["ids"]), allow_ids=set(entry["ids"]))
            except Exception as exc:
                done.append({"look": entry.get("name"), "error": str(exc)})
                continue
            if look.ids != entry["ids"]:
                done.append({"look": entry.get("name"), "error": "look structure changed; recreate it"})
                continue
            for spec in look.functions:
                if spec.type in ("Chaser", "EFX") and spec.id in rig.functions:
                    done.append(await set_function_speed(c, spec.id, spec.fade_in, spec.fade_out, spec.duration))
        return {"retimed": done, "bpm": args["bpm"]}

    async def _op_ask_outcome(self, args: dict, plan: Plan, **_) -> dict:
        return {"ask": args.get("questions", []), "plan_id": plan.plan_id}

    async def _op_feedback(self, args: dict, plan: Plan, **_) -> dict:
        rig = self.get_rig()
        target = self.session.plans.get(args.get("plan_id")) if args.get("plan_id") else None
        fb = OutcomeFeedback(**args["feedback"])
        router = FeedbackRouter(rig, self.prefs, self.cfg)
        res = router.apply(fb, target, source_text=args.get("text", ""))
        if "rig_facts" in res.get("routed_to", []):
            self.on_rig_change()
        if self.planner is not None and target is not None:
            tf = self.planner.taste_followup(target, [i.model_dump() for i in fb.issues], proposals_only=True)
            if tf.get("adjusted_plan_id"):
                res["adjusted_proposal"] = {"plan_id": tf["adjusted_plan_id"], "changes": tf["notes"]}
        return res

    async def apply_feedback(self, fb: OutcomeFeedback) -> dict:
        rig = self.get_rig()
        target = self.session.plans.get(fb.plan_id)
        res = FeedbackRouter(rig, self.prefs, self.cfg).apply(fb, target)
        if "rig_facts" in res.get("routed_to", []):
            self.on_rig_change()
        return res

    def _materialize_preview(self, args: dict) -> dict:
        """A lazy preview action carries look params; simulate its frames once, on first play."""
        if "deltas" in args or not args.get("lazy"):
            return args
        from lightai.compiler import compile_look
        from lightai.compiler.preview import compress, simulate
        from lightai.compiler.spec import LookParams

        rig = self.get_rig()
        look = compile_look(rig, LookParams(**args["params"]))
        args.update(compress(simulate(rig, look, seconds=float(args.get("seconds", 8.0)), fps=int(args.get("fps", 20)))))
        return args

    async def _priority_holders(self, chans: set) -> list:
        """Running looks with a priority above 0 that write some of these (universe, channel) pairs, 1-based.

        QLC+'s Simple Desk writes at priority 0, so a manual override on such a channel has no effect on stage."""
        rig = self.get_rig()
        prio = [f for f in rig.functions.values() if (f.priority or 0) > 0]
        if not prio or not chans:
            return []
        try:
            c = await self.ensure_client()
            running = set(await c.running_functions(sorted(f.id for f in prio)))
        except QlcError:
            return []
        out = []
        for f in prio:
            if f.id in running:
                held = self._function_channels(rig, f.id, set()) & set(chans)
                if held:
                    out.append({"function_id": f.id, "name": f.name, "priority": f.priority, "channels": len(held)})
        return out

    def _function_channels(self, rig, fid: int, seen: set) -> set:
        if fid in seen or fid not in rig.functions:
            return set()
        seen.add(fid)
        f = rig.functions[fid]
        out: set = set()
        if f.type == "Scene":
            for fx_id, pairs in (f.values or {}).items():
                fx = rig.fixtures.get(int(fx_id))
                if fx is not None:
                    out |= {fx.dmx(int(ch)) for ch, _ in pairs}
        elif f.type == "EFX":
            for fx_id in f.fixtures or []:
                fx = rig.fixtures.get(int(fx_id))
                if fx is not None:
                    out |= {fx.dmx(fx.roles[r]) for r in ("pan", "tilt", "pan_fine", "tilt_fine", "dimmer") if r in fx.roles}
        for r in f.refs or []:
            out |= self._function_channels(rig, r, seen)
        return out

    @staticmethod
    def _held_note(held: list) -> str:
        top = max(held, key=lambda h: h["priority"])
        more = f" and {len(held) - 1} more" if len(held) > 1 else ""
        return (f"'{top['name']}' (priority {top['priority']}){more} holds {sum(h['channels'] for h in held)} of these channels: "
                "a manual override has no effect there until it stops")

    async def _op_preview(self, args: dict, plan: Plan, wait_preview: bool = False, **_) -> dict:
        args = self._materialize_preview(args)
        c = await self.ensure_client()
        held = await self._priority_holders({(int(u), int(ch)) for u, ch in args["channels"]})
        if self.session.calibration is not None:
            await self.calibrate_end("interrupted by a preview")
        async with self._preview_lock:
            await self._cancel_tasks()
            self.preview_task = task = asyncio.create_task(self._play(c, args))
        if plan.intent == "propose_look" and plan.look:  # moved here from Planner._propose_look: planning must not write state
            self.prefs.remember(plan.look.get("recipe"), ((plan.look.get("params") or {}).get("colors") or ["white"])[0])
        if wait_preview:
            try:
                await task
            except asyncio.CancelledError:
                cur = asyncio.current_task()
                if cur is not None and cur.cancelling():
                    raise
                return {"started": True, "superseded": True, "seconds": args["seconds"], "channels": len(args["channels"]),
                        "frames": len(args["deltas"])}
        out = {"started": True, "seconds": args["seconds"], "channels": len(args["channels"]), "frames": len(args["deltas"])}
        if held:
            out["held_by"] = held
            out["warning"] = self._held_note(held) + "; the preview will not show on those channels"
        return out

    async def _play(self, c: QlcClient, pv: dict) -> None:
        period = 1.0 / max(1, int(pv["fps"]))
        chans = {(int(u), int(ch)) for u, ch in pv["channels"]}
        self.preview_channels |= chans
        t_start = time.perf_counter()
        try:
            for i, delta in enumerate(pv["deltas"]):
                for u, ch, v in delta:
                    await c.set_channel(int(u), int(ch), int(v))
                target = t_start + (i + 1) * period
                await asyncio.sleep(max(0.0, target - time.perf_counter()))
        finally:
            await asyncio.shield(self._release(c, chans))  # a second cancel must not abort the release

    async def _release(self, c: QlcClient, chans: set) -> None:
        failed = set()
        for u, ch in sorted(chans):
            try:
                await c.reset_channel(u, ch)
            except Exception:
                failed.add((u, ch))
        self.preview_channels -= chans
        if failed:  # QLC+ dropped mid-release: remember them and release after reconnecting
            self.pending_release |= failed
            self.session.overridden |= failed

    async def stop_preview(self) -> None:
        async with self._preview_lock:
            await self._cancel_tasks()

    async def _cancel_tasks(self) -> None:
        for task_name in ("preview_task", "ramp_task"):
            task = getattr(self, task_name)
            setattr(self, task_name, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _op_calibrate_start(self, args: dict, plan: Plan, **_) -> dict:
        async with self._cal_lock:
            if self.session.calibration is not None:
                await self._calibrate_end("replaced by a new calibration")
            return await self._calibrate_start(args, plan)

    async def _calibrate_start(self, args: dict, plan: Plan) -> dict:
        fx = self.get_rig().fixtures.get(int(args["fixture_id"]))
        if fx is not None:
            held = await self._priority_holders({fx.dmx(i) for i in range(fx.channels)})
            if held:  # answers given in the dark would teach wrong facts
                return {"calibration": False, "held_by": held,
                        "error": self._held_note(held) + f". Stop it first, then calibrate fixture {fx.id}."}
        self.session.calibration = Calibration(fixture_id=args["fixture_id"], mode=args["mode"], steps=args["steps"], color=args.get("color"), plan_id=plan.plan_id)
        self.session.calibration.question = args.get("question", "")
        return await self._cal_run_step()

    async def _cal_run_step(self) -> dict:
        cal = self.session.calibration
        if cal is None or cal.index >= len(cal.steps):
            return {"calibration": False, "result": "stopped"}
        c = await self.ensure_client()
        await self.stop_preview()
        step = cal.steps[cal.index]
        for u, ch, v in step.get("set", []):
            await c.set_channel(int(u), int(ch), int(v))
            self.session.overridden.add((int(u), int(ch)))
        if step.get("ramp"):
            u, ch = step["ramp"]
            self.ramp_task = asyncio.create_task(self._ramp(c, int(u), int(ch)))
        return {"calibration": True, "step": cal.index + 1, "of": len(cal.steps), "label": step.get("label"), "question": getattr(cal, "question", "")}

    async def _ramp(self, c: QlcClient, u: int, ch: int) -> None:
        self.session.overridden.add((u, ch))
        while True:
            for v in list(range(0, 256, 8)) + list(range(255, -1, -8)):
                await c.set_channel(u, ch, v)
                await asyncio.sleep(0.05)

    async def calibrate_answer(self, answer: str) -> dict:
        async with self._cal_lock:
            return await self._calibrate_answer(answer)

    async def calibrate_end(self, why: str, learned: Optional[str] = None) -> dict:
        async with self._cal_lock:
            return await self._calibrate_end(why, learned)

    async def _calibrate_answer(self, answer: str) -> dict:
        cal = self.session.calibration
        if cal is None:
            return {"error": "no calibration running"}
        rig = self.get_rig()
        fx = rig.fixtures[cal.fixture_id]
        a = answer.strip().lower()
        facts = LearnedFacts(self.cfg.learned_path)
        learned = None
        step = cal.steps[cal.index]
        if a in ("stop", "cancel", "quit", "done", "end"):
            return await self._calibrate_end("stopped")
        if cal.mode == "rgb":
            seen = color_in(rig, a)
            seen = seen if seen in ("red", "green", "blue", "white", "amber", "uv") else None
            if seen and seen != step.get("expect"):
                facts.set_role("model", fx.key, seen, int(step["channel"]), plan_id=cal.plan_id)
                facts.save()
                cal.dirty = True
                learned = f"{fx.key}: channel {int(step['channel']) + 1} is {seen} (was mapped as {step.get('expect')})"
            cal.answers.append({"channel": step.get("channel"), "answer": a, "expect": step.get("expect"), "seen": seen})
        elif cal.mode == "wheel":
            seen = color_in(rig, a)
            neg = re.search(r"\b(no|not|nope|nah|next|wrong)\b", a)
            said_yes = re.search(r"\b(yes|yeah|yep|this|that|it|there|got it|found|correct)\b", a)
            if not neg and (seen == cal.color or (seen is None and said_yes)):
                facts.set_wheel(fx.key, cal.color, int(step["value"]), plan_id=cal.plan_id)
                learned = f"{fx.key}: {cal.color} = wheel value {step['value']}"
                facts.save()
                cal.dirty = True
                return await self._calibrate_end("found", learned)
            if seen and seen != cal.color:
                facts.set_wheel(fx.key, seen, int(step["value"]), plan_id=cal.plan_id)
                facts.save()
                cal.dirty = True
                learned = f"{fx.key}: wheel value {step['value']} = {seen}"
        else:
            role = next((ROLE_WORDS[w] for w in re.findall(r"[a-z]+", a) if w in ROLE_WORDS), None)
            ch = int(step["channel"])
            clash = sorted(r for r, c in fx.roles.items() if c == ch and r != role and not r.endswith("_fine") and r not in ("none", "effect"))
            elsewhere = role in fx.roles and fx.roles[role] != ch
            if role and role != "none" and (clash or elsewhere) and not re.search(r"\b(sure|really|confirm|force)\b", a):
                learned = (f"NOT saved: channel {ch + 1} is already {'/'.join(clash) or 'unassigned'} and {role} is channel "
                           f"{fx.roles.get(role, -1) + 1} for every {fx.key}. Changing that would affect all {fx.key} fixtures; "
                           f"answer '{role}, sure' to confirm.")
            elif role and role != "none":
                facts.set_role("model", fx.key, role, ch, plan_id=cal.plan_id)
                facts.save()
                cal.dirty = True
                learned = f"{fx.key}: channel {ch + 1} = {role}"
            cal.answers.append({"channel": step.get("channel"), "answer": a, "role": role})
        await self.stop_preview()
        if step.get("ramp"):
            try:
                await self.client.reset_channel(*step["ramp"])
            except QlcError:
                pass
        cal.index += 1
        if cal.index >= len(cal.steps):
            return await self._calibrate_end("complete", learned)
        res = await self._cal_run_step()
        res["learned"] = learned
        return res

    async def _calibrate_end(self, why: str, learned: Optional[str] = None) -> dict:
        cal = self.session.calibration
        self.session.calibration = None
        await self.stop_preview()
        if cal is not None:
            chans = set()
            for st in cal.steps:
                for u, ch, _ in st.get("set", []):
                    chans.add((int(u), int(ch)))
                if st.get("ramp"):
                    chans.add(tuple(int(x) for x in st["ramp"]))
            left = set(chans)
            try:
                c = await self.ensure_client()
                for u, ch in sorted(chans):
                    await c.reset_channel(u, ch)
                    self.session.overridden.discard((u, ch))
                    left.discard((u, ch))
            except QlcError as exc:
                self.pending_release |= left  # released as soon as QLC+ is back
                why += f" (QLC+ not reachable, {len(left)} channel(s) will be released when it is back: {exc})"
            if getattr(cal, "dirty", False):
                self.on_rig_change()
        return {"calibration": False, "result": why, "learned": learned}
