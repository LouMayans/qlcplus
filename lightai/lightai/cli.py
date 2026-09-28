"""Terminal front ends: `lightai repl`, `lightai parse`, `lightai do`, `lightai facts`."""

from __future__ import annotations

import asyncio
import json
import sys

from lightai.app import LightAI
from lightai.schema import Issue, OutcomeFeedback

HELP = """commands: type a request, or
  :y / :apply        run the shown plan        :p / :preview   preview a create_look plan live
  :keep              save a previewed proposal :n / :discard   drop the plan
  :ok  :fast :slow :dim :bright :bigger :smaller :moved :dark  quick feedback on the last result
  :saw <color> [fixture]   color was wrong      :bpm <n> / :tap   tempo
  :json              show last JSON             :stop            stop preview/calibration
  :cal <answer>      answer a calibration step  :q              quit"""


def _print_plan(cmd, plan) -> None:
    print(f"\n  intent: {cmd.intent} ({cmd.confidence:.0%}, {cmd.latency_ms:.1f} ms)")
    if cmd.spans:
        print("  slots:  " + "  ".join(f"[{s.slot}: {s.text}]" for s in cmd.spans))
    print(f"  plan {plan.plan_id} ({plan.mode}): {plan.summary}")
    for a in plan.assumptions[:12]:
        print(f"    assume: {a['fact']}  ({a['source']})")
    for w in plan.warnings:
        print(f"    ! {w}")
    for a in plan.actions:
        print(f"    - {a.op}: {a.describe}")
    if plan.look and plan.look.get("results"):
        for r in plan.look["results"][:40]:
            print("    " + "  ".join(f"{k}={v}" for k, v in r.items() if k not in ("roles", "zones")))


def _print_result(res: dict) -> None:
    ok = res.get("ok")
    print(f"  => {'done' if ok else 'NOT done'} in {res.get('latency_ms', '?')} ms")
    for r in res.get("results", []):
        slim = {k: v for k, v in r.items() if k not in ("feedback",)}
        print("     " + json.dumps(slim)[:400])
    if res.get("needs_confirmation"):
        print("     (needs confirmation: type :y)")
    if res.get("clarify"):
        print(f"     {res['clarify']}")


async def repl() -> int:
    ai = LightAI()
    print(f"lightai {ai.model.version} | {len(ai.rig.fixtures)} fixtures | {len(ai.rig.functions)} functions | project {ai.cfg.project_path}")
    print(HELP)
    last = {"cmd": None, "plan": None, "res": None}
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, lambda: input("\nlightai> "))
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if not line:
            continue
        if line in (":q", ":quit", "exit", "quit"):
            break
        try:
            if line.startswith(":"):
                word, _, rest = line[1:].partition(" ")
                plan = last["plan"]
                if word in ("y", "apply"):
                    if plan is None:
                        print("  nothing to apply")
                        continue
                    res = await ai.executor.execute(plan, confirm=True, wait_preview=True)
                    if any(r.get("blocked") for r in res.get("results", [])):
                        _print_result(res)
                        ans = input("  reload anyway? [y/N] ").strip().lower()
                        if ans == "y":
                            res = await ai.executor.execute(plan, confirm=True, allow_running_reload=True, wait_preview=True)
                    last["res"] = res
                    _print_result(res)
                elif word in ("p", "preview"):
                    pv = ai.planner.preview_for(plan) if plan else None
                    if not pv:
                        print("  this plan has no preview")
                        continue
                    from lightai.schema import Action, Plan

                    prev = Plan(plan_id=ai.session.new_plan_id(), intent="preview", mode="live", summary="preview", actions=[Action(op="preview", args=pv)])
                    ai.session.last_action_plan = plan
                    _print_result(await ai.executor.execute(prev, wait_preview=True))
                elif word == "keep":
                    if not plan or not (plan.followup or {}).get("apply"):
                        print("  last plan is not a proposal")
                        continue
                    newp = ai.planner.apply_proposal(plan)
                    last["plan"] = newp
                    _print_plan(last["cmd"], newp)
                    print("  type :y to write it to the show")
                elif word in ("n", "discard"):
                    last["plan"] = None
                elif word == "json":
                    print(json.dumps({"command": last["cmd"].model_dump() if last["cmd"] else None,
                                      "plan": last["plan"].model_dump() if last["plan"] else None, "result": last["res"]}, indent=2)[:8000])
                elif word == "bpm":
                    ai.session.set_bpm(float(rest), "typed")
                    print(f"  BPM {ai.session.bpm}")
                elif word == "tap":
                    print(f"  BPM {ai.session.tap()}")
                elif word == "stop":
                    await ai.executor.stop_preview()
                    if ai.session.calibration:
                        print(await ai.executor.calibrate_end("stopped"))
                elif word == "cal":
                    print("  " + json.dumps(await ai.executor.calibrate_answer(rest)))
                elif word in ("ok", "fast", "slow", "dim", "bright", "bigger", "smaller", "moved", "dark", "saw"):
                    target = ai.session.last_action_plan
                    if target is None:
                        print("  no previous action to rate")
                        continue
                    issues = []
                    m = {"fast": ("speed", "too_fast"), "slow": ("speed", "too_slow"), "dim": ("intensity", "too_dim"),
                         "bright": ("intensity", "too_bright"), "bigger": ("size", "too_small"), "smaller": ("size", "too_big")}
                    if word in m:
                        issues.append(Issue(kind=m[word][0], direction=m[word][1]))
                    elif word == "moved":
                        issues.append(Issue(kind="movement"))
                    elif word == "dark":
                        issues.append(Issue(kind="dark"))
                    elif word == "saw":
                        parts = rest.split()
                        exp = ((target.look or {}).get("params") or {}).get("colors", [None])[0]
                        issues.append(Issue(kind="color", observed=parts[0] if parts else None, expected=exp,
                                            fixture_id=int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None))
                    verdict = "ok" if word == "ok" else ("adjust" if word in m else "wrong")
                    res = await ai.executor.apply_feedback(OutcomeFeedback(plan_id=target.plan_id, verdict=verdict, issues=issues, free_text=line))
                    for c in res["changes"]:
                        print(f"  learned: {c}")
                    for f in res["followups"]:
                        print(f"  next: {f}")
                else:
                    print(HELP)
                continue
            cmd, plan = ai.plan(line)
            last.update(cmd=cmd, plan=plan, res=None)
            _print_plan(cmd, plan)
            if plan.intent in ("feedback", "set_bpm", "query.functions", "query.fixtures", "query.status") and plan.mode != "clarify":
                res = await ai.executor.execute(plan, confirm=True)
                last["res"] = res
                _print_result(res)
            elif plan.mode == "clarify":
                pass
            else:
                print("  type :y to run it" + (", :p to preview it on the rig" if (plan.followup or {}).get("preview") else ""))
        except Exception as exc:
            print(f"  error: {type(exc).__name__}: {exc}")
    await ai.executor.stop_preview()
    await ai.executor.client.close()
    return 0


def repl_main(args) -> int:
    return asyncio.run(repl())


def parse_main(args) -> int:
    ai = LightAI(use_embeddings=not args.no_embeddings)
    for text in args.text:
        cmd, plan = ai.plan(text)
        out = {"command": cmd.model_dump(), "plan": plan.model_dump()} if args.plan else cmd.model_dump()
        if args.full:
            print(json.dumps(out, indent=2))
        else:
            slim = {"text": cmd.text, "intent": cmd.intent, "confidence": cmd.confidence, "latency_ms": cmd.latency_ms,
                    "slots": {k: [v.value for v in vs] for k, vs in cmd.slots.items()}, "clarify": cmd.clarify}
            if args.plan:
                slim["plan"] = {"summary": plan.summary, "mode": plan.mode, "actions": [a.op for a in plan.actions]}
            print(json.dumps(slim, indent=2))
    return 0


def do_main(args) -> int:
    """One-shot: parse, plan and (with --yes) execute."""

    async def run() -> int:
        ai = LightAI(use_embeddings=not args.no_embeddings)
        cmd, plan = ai.plan(args.text)
        _print_plan(cmd, plan)
        if plan.mode == "clarify":
            return 2
        if not args.yes and plan.needs_confirmation:
            print("  (dry run: add --yes to execute)")
            return 0
        res = await ai.executor.execute(plan, confirm=args.yes, allow_running_reload=args.allow_running_reload, wait_preview=True)
        _print_result(res)
        await ai.executor.client.close()
        return 0 if res.get("ok") else 1

    return asyncio.run(run())
