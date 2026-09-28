"""Phase-0 latency benchmark: real numbers for this laptop, not estimates.

    python -m lightai bench [--out DIR] [--iters N] [--threads T] [--seq-len L] [--skip-ws]

Measures
  1. distilbert-base-uncased encoder, FP32 and INT8 (dynamic quantization)
  2. all-MiniLM-L6-v2 retriever encoder, INT8
  3. tokenizer time
  4. QLC+ WebSocket round trip (read-only query)
Writes <out>/bench.json and prints PASS/FAIL against the gates in the plan.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
import time
from pathlib import Path

from lightai.config import load_config

SAMPLE_TEXT = "slow blue wash breathing at 60 bpm on the washes please"

GATES = {
    "nlu_int8_p95_ms": 30.0,
    "retriever_int8_p95_ms": 10.0,
    "ws_rtt_p95_ms": 50.0,
}


def _percentile(xs: list, p: float) -> float:
    ordered = sorted(xs)
    return ordered[min(len(ordered) - 1, int(round(p / 100.0 * (len(ordered) - 1))))]


def _stats(xs: list) -> dict:
    return {
        "n": len(xs),
        "p50_ms": round(_percentile(xs, 50), 3),
        "p95_ms": round(_percentile(xs, 95), 3),
        "mean_ms": round(sum(xs) / len(xs), 3),
    }


def time_encoder(onnx_path: Path, tok_dir: Path, seq_len: int, iters: int, threads: int) -> dict:
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    t0 = time.perf_counter()
    sess = ort.InferenceSession(str(onnx_path), so, providers=["CPUExecutionProvider"])
    load_ms = (time.perf_counter() - t0) * 1000.0
    input_names = {i.name for i in sess.get_inputs()}

    tok = Tokenizer.from_file(str(tok_dir / "tokenizer.json"))
    tok.enable_padding(length=seq_len)
    tok.enable_truncation(seq_len)

    def feed() -> dict:
        enc = tok.encode(SAMPLE_TEXT)
        d = {
            "input_ids": np.array([enc.ids], dtype=np.int64),
            "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
            "token_type_ids": np.array([enc.type_ids], dtype=np.int64),
        }
        return {k: v for k, v in d.items() if k in input_names}

    tok_times = []
    for _ in range(iters):
        t0 = time.perf_counter()
        tok.encode(SAMPLE_TEXT)
        tok_times.append((time.perf_counter() - t0) * 1000.0)

    for _ in range(20):
        sess.run(None, feed())
    run_times = []
    for _ in range(iters):
        inputs = feed()
        t0 = time.perf_counter()
        sess.run(None, inputs)
        run_times.append((time.perf_counter() - t0) * 1000.0)

    return {
        "onnx": str(onnx_path),
        "size_mb": round(onnx_path.stat().st_size / 1e6, 1),
        "session_load_ms": round(load_ms, 1),
        "tokenizer": _stats(tok_times),
        "encoder": _stats(run_times),
    }


def ws_rtt(iters: int = 200) -> dict:
    try:
        from lightai.exec.wsclient import QlcClient

        async def run() -> dict:
            client = QlcClient.from_config(load_config())
            async with client:
                for _ in range(iters):
                    await client.functions_number()
                out = client.latency.summary()
                out["url"] = client.connected_url
                return out

        return asyncio.run(run())
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def bench_main(args) -> int:
    from lightai.nlu.onnx_export import export_encoder, quantize_int8

    cfg = load_config()
    out = Path(args.out) if args.out else cfg.data_dir / "bench"
    out.mkdir(parents=True, exist_ok=True)
    import onnxruntime as ort

    report: dict = {
        "machine": {
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "python": sys.version.split()[0],
            "onnxruntime": ort.__version__,
        },
        "settings": {"seq_len": args.seq_len, "iters": args.iters, "threads": args.threads},
        "results": {},
    }

    for label, model_id in (("nlu", cfg.encoder), ("retriever", cfg.retriever)):
        model_dir = out / label
        try:
            t0 = time.perf_counter()
            fp32 = export_encoder(model_id, model_dir)
            int8 = quantize_int8(fp32)
            res = {"model": model_id, "export_s": round(time.perf_counter() - t0, 1)}
            if label == "nlu":
                res["fp32"] = time_encoder(fp32, model_dir, args.seq_len, args.iters, args.threads)
            res["int8"] = time_encoder(int8, model_dir, args.seq_len, args.iters, args.threads)
            report["results"][label] = res
        except Exception as exc:
            report["results"][label] = {"model": model_id, "error": f"{type(exc).__name__}: {exc}"}
            print(f"[bench] {label} failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    if not args.skip_ws:
        report["results"]["ws"] = ws_rtt()

    checks = {}
    nlu = report["results"].get("nlu", {}).get("int8")
    if nlu:
        checks["nlu_int8_p95_ms"] = nlu["encoder"]["p95_ms"]
    ret = report["results"].get("retriever", {}).get("int8")
    if ret:
        checks["retriever_int8_p95_ms"] = ret["encoder"]["p95_ms"]
    ws = report["results"].get("ws", {})
    if ws.get("p95_ms") is not None:
        checks["ws_rtt_p95_ms"] = ws["p95_ms"]
    report["gates"] = {k: {"value_ms": v, "limit_ms": GATES[k], "pass": v < GATES[k]} for k, v in checks.items()}

    (out / "bench.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    m = report["machine"]
    print(f"\n{m['processor']} | {m['cpu_count']} logical CPUs | onnxruntime {m['onnxruntime']}")
    print(f"seq_len={args.seq_len} iters={args.iters} threads={args.threads}\n")
    print(f"{'measurement':<32}{'p50 ms':>10}{'p95 ms':>10}{'mean ms':>10}{'size MB':>10}")
    for label in ("nlu", "retriever"):
        res = report["results"].get(label, {})
        for kind in ("fp32", "int8"):
            if kind in res:
                enc = res[kind]["encoder"]
                print(f"{label + ' ' + kind + ' encoder':<32}{enc['p50_ms']:>10}{enc['p95_ms']:>10}{enc['mean_ms']:>10}{res[kind]['size_mb']:>10}")
        if "int8" in res:
            tk = res["int8"]["tokenizer"]
            print(f"{label + ' tokenizer':<32}{tk['p50_ms']:>10}{tk['p95_ms']:>10}{tk['mean_ms']:>10}")
        if "error" in res:
            print(f"{label:<32}ERROR {res['error']}")
    if ws.get("p95_ms") is not None:
        print(f"{'QLC+ ws round trip':<32}{ws['p50_ms']:>10}{ws['p95_ms']:>10}")
    elif ws:
        print(f"{'QLC+ ws round trip':<32}{ws.get('error', 'skipped')}")
    print()
    all_ok = True
    for name, g in report["gates"].items():
        all_ok &= g["pass"]
        print(f"[{'PASS' if g['pass'] else 'FAIL'}] {name}: {g['value_ms']} ms (limit {g['limit_ms']} ms)")
    print(f"\nreport: {out / 'bench.json'}")
    return 0 if all_ok else 1
