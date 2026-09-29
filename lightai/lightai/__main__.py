"""lightai command line. Phase 0 provides `smoke` and `bench`; later phases add the rest."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lightai",
        description="Personal, trainable lighting AI for the Mayans QLC+ rig",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    smoke = sub.add_parser(
        "smoke",
        help="WebSocket smoke test against the running QLC+ (read-only unless --probe)",
    )
    smoke.add_argument("--url", default=None, help="ws://host:port/qlcplusWS (default: env LIGHTAI_QLC_URL or localhost)")
    smoke.add_argument("--probe", action="store_true", help="set and reset one unpatched Simple Desk channel")
    smoke.add_argument("--iters", type=int, default=50, help="round trips to time")
    smoke.add_argument("--json", action="store_true", help="print the report as JSON")

    bench = sub.add_parser("bench", help="phase-0 latency benchmark (needs the train packages)")
    bench.add_argument("--out", default=None, help="output folder (default: %%LIGHTAI_DATA%%\bench)")
    bench.add_argument("--iters", type=int, default=200)
    bench.add_argument("--threads", type=int, default=2, help="onnxruntime intra-op threads")
    bench.add_argument("--seq-len", type=int, default=32)
    bench.add_argument("--skip-ws", action="store_true", help="do not measure the QLC+ round trip")
    tr = sub.add_parser("train", help="build the dataset, fine-tune, export INT8 ONNX, evaluate, optionally promote")
    tr.add_argument("--epochs", type=int, default=4)
    tr.add_argument("--batch", type=int, default=32)
    tr.add_argument("--lr", type=float, default=5e-5)
    tr.add_argument("--head-lr", type=float, default=1e-4)
    tr.add_argument("--threads", type=int, default=4)
    tr.add_argument("--seed", type=int, default=13)
    tr.add_argument("--scale", type=float, default=1.0, help="multiply grammar template counts")
    tr.add_argument("--min-per-intent", type=int, default=120)
    tr.add_argument("--encoder", default=None)
    tr.add_argument("--min-free-gb", type=float, default=0.8)
    tr.add_argument("--freeze-layers", type=int, default=-1, help="freeze the lowest N encoder layers (-1 = auto by free RAM)")
    tr.add_argument("--freeze-embeddings", action="store_true")
    tr.add_argument("--keep-fp32", action="store_true")
    tr.add_argument("--promote-if-better", action="store_true", help="promote only if all gates pass and nothing regressed")
    tr.add_argument("--promote", action="store_true", help="promote even if gates fail (not recommended)")

    sv = sub.add_parser("serve", help="run the local API + booth console")
    sv.add_argument("--host", default=None)
    sv.add_argument("--port", type=int, default=None)
    sv.add_argument("--cors", nargs="*", default=None, help="allowed origins for a website/LWC caller")
    sv.add_argument("--no-embeddings", action="store_true", help="lexical function matching only (saves ~60 MB)")
    sv.add_argument("--qlc-port", type=int, default=None, help="override the QLC+ web access port (default: config/env, then 9999)")

    sub.add_parser("repl", help="interactive terminal console")

    pa = sub.add_parser("parse", help="print the JSON for one or more sentences (no side effects)")
    pa.add_argument("text", nargs="+")
    pa.add_argument("--plan", action="store_true", help="also build the plan")
    pa.add_argument("--full", action="store_true", help="full JSON")
    pa.add_argument("--no-embeddings", action="store_true")

    do = sub.add_parser("do", help="parse + plan + execute one sentence (dry run unless --yes)")
    do.add_argument("text")
    do.add_argument("--yes", action="store_true")
    do.add_argument("--allow-running-reload", action="store_true")
    do.add_argument("--no-embeddings", action="store_true")

    au = sub.add_parser("audit", help="regenerate rig facts from the files and report problems")
    au.add_argument("--project", default=None)
    au.add_argument("--out", default=None)

    fx = sub.add_parser("fix", help="one-shot repairs of the show file (backup first)")
    fx.add_argument("what", choices=["v3-color"])
    fx.add_argument("--project", default=None)
    fx.add_argument("--yes", action="store_true", help="write the change (default: dry run)")

    fa = sub.add_parser("facts", help="list or undo learned rig facts")
    fa.add_argument("action", choices=["list", "undo", "wheel"])
    fa.add_argument("args", nargs="*", help="undo: <id>; wheel: <Manufacturer/Model> <color> <value>")

    sub.add_parser("prefs-train", help="retrain the taste (acceptance) model from feedback.jsonl")

    ni = sub.add_parser("nightly", help="the nightly learning job: taste model, language model (if new examples), audit")
    ni.add_argument("--min-new", type=int, default=1, help="retrain the language model after this many new examples")
    ni.add_argument("--force", action="store_true")

    tp = sub.add_parser("transplant", help="turn a function from another QLC+ show into a look on this rig")
    tp.add_argument("qxw")
    tp.add_argument("function", help="function ID or name in that show")
    tp.add_argument("--targets", default=None, help="zone or fixture phrase on this rig (default: matched by fixture type)")
    tp.add_argument("--yes", action="store_true", help="write it into the show (default: dry run)")

    it = sub.add_parser("install-task", help="register the nightly retrain in Windows Task Scheduler")
    it.add_argument("--time", default="05:30")
    it.add_argument("--remove", action="store_true")

    mi = sub.add_parser("mine", help="collect and analyze QLC+ shows (local folders, GitHub)")
    mi.add_argument("source", choices=["local", "github", "stats", "taxonomy"])
    mi.add_argument("paths", nargs="*")
    mi.add_argument("--token", default=None, help="GitHub token (or set GITHUB_TOKEN)")
    mi.add_argument("--max", type=int, default=100)

    ev = sub.add_parser("evaluate", help="evaluate a model directory (default: current)")
    ev.add_argument("model_dir", nargs="?", default=None)
    ev.add_argument("--promote-if-better", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "smoke":
        from lightai.exec.wsclient import smoke_main

        return smoke_main(args)
    if args.cmd == "bench":
        from lightai.bench import bench_main

        return bench_main(args)
    if args.cmd == "serve":
        from lightai.api.server import serve_main

        return serve_main(args)
    if args.cmd == "repl":
        from lightai.cli import repl_main

        return repl_main(args)
    if args.cmd == "parse":
        from lightai.cli import parse_main

        return parse_main(args)
    if args.cmd == "do":
        from lightai.cli import do_main

        return do_main(args)
    if args.cmd == "audit":
        from lightai.rig.audit import audit_main

        return audit_main(args)
    if args.cmd == "fix":
        from lightai.rig.audit import fix_main

        return fix_main(args)
    if args.cmd == "facts":
        from lightai.config import load_config
        from lightai.rig.facts import LearnedFacts

        lf = LearnedFacts(load_config().learned_path)
        if args.action == "undo":
            fid = int(args.args[0]) if args.args else -1
            f = lf.undo(fid)
            lf.save()
            print(f"undone: {f}" if f else f"no active fact {fid}")
            return 0 if f else 1
        if args.action == "wheel":
            if len(args.args) != 3:
                print("usage: lightai facts wheel <Manufacturer/Model> <color> <value>")
                return 2
            f = lf.set_wheel(args.args[0], args.args[1].lower(), int(args.args[2]), source="operator (cli)")
            lf.save()
            print(f"learned: {f}")
            return 0
        for f in lf.data["facts"]:
            print(f"{f['id']:>4} {'   ' if f.get('active') else 'off'} {f['date']} {f['kind']:<16} {f['subject']:<24} {f['detail']}")
        return 0
    if args.cmd == "prefs-train":
        from lightai.config import load_config
        from lightai.prefs import Prefs

        print(Prefs(load_config()).train())
        return 0
    if args.cmd == "install-task":
        from lightai.tasks import install_task

        return install_task(args)
    if args.cmd == "nightly":
        from lightai.tasks import nightly_main

        return nightly_main(args)
    if args.cmd == "transplant":
        from lightai.mine.transplant import transplant_main

        return transplant_main(args)
    if args.cmd == "mine":
        from lightai.mine import mine_main

        return mine_main(args)
    if args.cmd == "train":
        from lightai.train.trainer import train_main

        return train_main(args)
    if args.cmd == "evaluate":
        import json
        from pathlib import Path

        from lightai.config import load_config
        from lightai.train.evaluate import evaluate_model_dir, promote

        cfg = load_config()
        d = Path(args.model_dir) if args.model_dir else cfg.current_model_dir()
        if d is None:
            print("no model; run `lightai train` first")
            return 2
        rep = evaluate_model_dir(d)
        (d / "metrics.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in rep.items() if k != "failures"}, indent=2))
        for f in rep["failures"]:
            print("  FAIL", f)
        if args.promote_if_better:
            ok, why = promote(d, rep)
            print(("PROMOTED " if ok else "NOT PROMOTED: ") + why)
        return 0 if rep["pass"] else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
