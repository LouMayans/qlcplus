"""Nightly learning job and its Windows Task Scheduler registration."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from lightai.config import LIGHTAI_DIR, load_config

TASK_NAME = "lightai nightly learning"


def new_examples_since(cfg, stamp: str) -> int:
    path = cfg.data_dir / "corrections.jsonl"
    if not path.exists():
        return 0
    try:
        since = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("accepted") or not (row.get("corrected") or {}).get("marked"):
            continue
        try:
            ts = datetime.fromisoformat(row["ts"])
        except (KeyError, ValueError):
            continue
        if ts > since:
            n += 1
    return n


def nightly_main(args) -> int:
    cfg = load_config()
    print(f"=== lightai nightly {datetime.now().isoformat(timespec='seconds')} ===", flush=True)
    from lightai.prefs import Prefs

    print("taste model:", Prefs(cfg).train(), flush=True)
    if getattr(cfg, "teacher_enabled", True):  # Claude labels what the local model missed; gated by the retrain below
        try:
            import asyncio
            from types import SimpleNamespace

            from lightai.design.backend import ClaudeCodeBackend
            from lightai.design.runlog import RunLog
            from lightai.design.teacher import label_sessions

            summary = asyncio.run(label_sessions(SimpleNamespace(cfg=cfg, model=None), ClaudeCodeBackend(cfg, RunLog(cfg.data_dir))))
            print("teacher:", {k: v for k, v in summary.items() if k != "runs"}, flush=True)
        except Exception as exc:  # never let the teacher stop the nightly job
            print(f"teacher skipped: {exc}", flush=True)
    current = cfg.current_model_dir()
    stamp = json.loads((current / "meta.json").read_text(encoding="utf-8"))["created"] if current else "19700101T000000Z"
    n_new = new_examples_since(cfg, stamp)
    print(f"new confirmed/corrected examples since {stamp}: {n_new}", flush=True)
    rc = 0
    if n_new >= args.min_new or args.force or current is None:
        from lightai.train.trainer import train_main

        targs = argparse.Namespace(epochs=5, batch=32, lr=5e-5, head_lr=1e-4, threads=3, seed=13, scale=1.0, min_per_intent=120,
                                   encoder=None, min_free_gb=0.8, freeze_layers=-1, freeze_embeddings=False, keep_fp32=False,
                                   promote_if_better=True, promote=False)
        rc = train_main(targs)
        print(f"training exit code {rc} (4 = trained but not promoted because a gate failed)", flush=True)
    else:
        print("language model unchanged (nothing new to learn)", flush=True)
    from lightai.rig.audit import audit_main

    audit_main(argparse.Namespace(project=None, out=None))
    return 0 if rc in (0, 4) else rc


def install_task(args) -> int:
    cfg = load_config()
    if args.remove:
        r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)
        print(r.stdout or r.stderr)
        return r.returncode
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "logs").mkdir(exist_ok=True)
    script = cfg.data_dir / "nightly.cmd"
    script.write_text(
        "@echo off\r\n"
        f'cd /d "{LIGHTAI_DIR}"\r\n'
        f'"{sys.executable}" -m lightai nightly >> "{cfg.data_dir / "logs" / "nightly.log"}" 2>&1\r\n',
        encoding="utf-8",
    )
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", str(script), "/SC", "DAILY", "/ST", args.time, "/F"],
                       capture_output=True, text=True)
    print((r.stdout or r.stderr).strip())
    if r.returncode == 0:
        print(f"runs {script} every day at {args.time}; log: {cfg.data_dir / 'logs' / 'nightly.log'}")
        print(f"remove with: lightai install-task --remove")
    return r.returncode
