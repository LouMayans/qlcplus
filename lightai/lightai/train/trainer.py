"""Fine-tune one encoder with two heads (intent + BIO slots), export ONNX, quantize INT8.

    python -m lightai train [--epochs 4] [--promote-if-better]
"""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from lightai.config import load_config
from lightai.schema import LABELS_PATH, labels
from lightai.train.generate import build_dataset, split


def _torch():
    import torch

    return torch


def make_model(encoder: str, n_intents: int, n_tags: int):
    torch = _torch()
    from transformers import AutoModel

    class JointModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = AutoModel.from_pretrained(encoder)
            h = self.encoder.config.hidden_size
            self.drop = torch.nn.Dropout(0.1)
            self.intent_head = torch.nn.Linear(h, n_intents)
            self.slot_head = torch.nn.Linear(h, n_tags)

        def forward(self, input_ids, attention_mask):
            out = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
            return self.intent_head(self.drop(out[:, 0])), self.slot_head(self.drop(out))

    return JointModel()


def encode(tokenizer, rows: list, intents: list, bio: list, max_len: int) -> list:
    i_idx = {n: i for i, n in enumerate(intents)}
    t_idx = {n: i for i, n in enumerate(bio)}
    out = []
    for r in rows:
        enc = tokenizer(r["words"], is_split_into_words=True, truncation=True, max_length=max_len)
        word_ids = enc.word_ids()
        labels_ = []
        prev = None
        for wid in word_ids:
            if wid is None or wid == prev:
                labels_.append(-100)
            else:
                labels_.append(t_idx[r["tags"][wid]])
            prev = wid
        out.append({"input_ids": enc["input_ids"], "labels": labels_, "intent": i_idx[r["intent"]]})
    return out


def batches(items: list, size: int, pad_id: int, shuffle: bool, rnd: random.Random):
    torch = _torch()
    order = list(range(len(items)))
    if shuffle:
        rnd.shuffle(order)
    for i in range(0, len(order), size):
        chunk = [items[j] for j in order[i : i + size]]
        L = max(len(c["input_ids"]) for c in chunk)
        ids = torch.full((len(chunk), L), pad_id, dtype=torch.long)
        mask = torch.zeros((len(chunk), L), dtype=torch.long)
        lab = torch.full((len(chunk), L), -100, dtype=torch.long)
        for k, c in enumerate(chunk):
            n = len(c["input_ids"])
            ids[k, :n] = torch.tensor(c["input_ids"])
            mask[k, :n] = 1
            lab[k, :n] = torch.tensor(c["labels"])
        yield ids, mask, lab, torch.tensor([c["intent"] for c in chunk])


def span_set(tags: list) -> set:
    spans, i = set(), 0
    while i < len(tags):
        t = tags[i]
        if t != "O":
            slot = t[2:]
            j = i + 1
            while j < len(tags) and tags[j] == f"I-{slot}":
                j += 1
            spans.add((slot, i, j))
            i = j
        else:
            i += 1
    return spans


def evaluate_torch(model, data: list, rows: list, bio: list, pad_id: int) -> dict:
    torch = _torch()
    model.eval()
    correct = total = 0
    tp = fp = fn = 0
    k = 0
    with torch.no_grad():
        for ids, mask, lab, intent in batches(data, 64, pad_id, False, random.Random(0)):
            il, sl = model(ids, mask)
            pred_i = il.argmax(-1)
            correct += int((pred_i == intent).sum())
            total += len(intent)
            pred_t = sl.argmax(-1)
            for b in range(ids.shape[0]):
                gold_tags = rows[k]["tags"]
                pt = [bio[int(pred_t[b, p])] for p in range(ids.shape[1]) if int(lab[b, p]) != -100]
                pt = pt[: len(gold_tags)]
                g, p = span_set(gold_tags), span_set(pt)
                tp += len(g & p)
                fp += len(p - g)
                fn += len(g - p)
                k += 1
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    model.train()
    return {"intent_acc": correct / max(1, total), "slot_f1": f1, "n": total}


def freeze(model, n_layers: int, embeddings: bool) -> int:
    """Freeze the embedding block and the lowest n transformer layers; returns frozen parameter count."""
    import re as _re

    count = 0
    for name, p in model.named_parameters():
        if not name.startswith("encoder."):
            continue
        m = _re.search(r"\.layers?\.(\d+)\.", name)
        hit = (embeddings and ".embeddings." in name) or (m is not None and int(m.group(1)) < n_layers)
        if hit:
            p.requires_grad_(False)
            count += p.numel()
    return count


def balance(rows: list, min_per_intent: int, rnd: random.Random) -> list:
    by: dict = {}
    for r in rows:
        by.setdefault(r["intent"], []).append(r)
    out = list(rows)
    for intent, rs in by.items():
        if len(rs) < min_per_intent:
            out += [rnd.choice(rs) for _ in range(min_per_intent - len(rs))]
    return out


def export_onnx(model, tokenizer, out_dir: Path, max_len: int) -> Path:
    torch = _torch()
    from lightai.nlu.onnx_export import quantize_int8, torch_onnx_export

    model.eval()
    enc = tokenizer(["slow", "blue", "wash", "breathing"], is_split_into_words=True, return_tensors="pt", padding="max_length", max_length=max_len)
    fp32 = out_dir / "model.fp32.onnx"
    with torch.no_grad():
        torch_onnx_export(
            model,
            (enc["input_ids"], enc["attention_mask"]),
            fp32,
            ["input_ids", "attention_mask"],
            ["intent_logits", "slot_logits"],
            {
                "input_ids": {0: "batch", 1: "seq"},
                "attention_mask": {0: "batch", 1: "seq"},
                "intent_logits": {0: "batch"},
                "slot_logits": {0: "batch", 1: "seq"},
            },
        )
    int8 = quantize_int8(fp32, out_dir / "model.int8.onnx")
    return int8


def train_main(args) -> int:
    torch = _torch()
    from transformers import AutoTokenizer, get_linear_schedule_with_warmup

    cfg = load_config()
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        free_gb = st.ullAvailPhys / 1e9
        if free_gb < args.min_free_gb:
            print(f"only {free_gb:.1f} GB RAM free (< {args.min_free_gb} GB); close other programs or pass --min-free-gb", file=sys.stderr)
            return 3
        if args.freeze_layers < 0:
            args.freeze_layers = 3 if free_gb < 2.5 else 0
            if args.freeze_layers:
                print(f"{free_gb:.1f} GB RAM free: low-memory mode (embeddings + {args.freeze_layers} lower layers frozen)", flush=True)
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        if args.freeze_layers < 0:
            args.freeze_layers = 0

    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    rnd = random.Random(args.seed)
    L = labels()
    rows = build_dataset(seed=args.seed, scale=args.scale)
    train_rows, dev_rows = split(rows)
    train_rows = balance(train_rows, args.min_per_intent, rnd)
    encoder = args.encoder or cfg.encoder
    tokenizer = AutoTokenizer.from_pretrained(encoder)
    train_data = encode(tokenizer, train_rows, L["intents"], L["bio"], cfg.max_len)
    dev_data = encode(tokenizer, dev_rows, L["intents"], L["bio"], cfg.max_len)
    model = make_model(encoder, len(L["intents"]), len(L["bio"]))
    frozen = freeze(model, args.freeze_layers, args.freeze_embeddings or args.freeze_layers > 0)
    if frozen:
        print(f"frozen parameters: {frozen:,}; trainable: {sum(p.numel() for p in model.parameters() if p.requires_grad):,}", flush=True)
    enc_params = [p for n, p in model.named_parameters() if n.startswith("encoder.") and p.requires_grad]
    head_params = [p for n, p in model.named_parameters() if not n.startswith("encoder.")]
    opt = torch.optim.AdamW([{"params": enc_params, "lr": args.lr}, {"params": head_params, "lr": args.head_lr}], weight_decay=0.01)
    steps = math.ceil(len(train_data) / args.batch) * args.epochs
    sched = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
    ce = torch.nn.CrossEntropyLoss(ignore_index=-100)
    pad = tokenizer.pad_token_id
    print(f"encoder={encoder} train={len(train_data)} dev={len(dev_data)} steps={steps} threads={args.threads}", flush=True)
    best, best_state, history = -1.0, None, []
    t0 = time.time()
    step = 0
    for epoch in range(args.epochs):
        model.train()
        tot = 0.0
        for ids, mask, lab, intent in batches(train_data, args.batch, pad, True, rnd):
            il, sl = model(ids, mask)
            loss = ce(il, intent) + ce(sl.reshape(-1, sl.shape[-1]), lab.reshape(-1))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad()
            tot += loss.item()
            step += 1
            if step % 25 == 0:
                print(f"  step {step}/{steps} loss {tot / 25:.4f} elapsed {time.time() - t0:.0f}s", flush=True)
                tot = 0.0
        m = evaluate_torch(model, dev_data, dev_rows, L["bio"], pad)
        m["epoch"] = epoch + 1
        history.append(m)
        print(f"epoch {epoch + 1}: dev intent_acc={m['intent_acc']:.4f} slot_f1={m['slot_f1']:.4f} ({time.time() - t0:.0f}s)", flush=True)
        score = m["intent_acc"] + m["slot_f1"]
        if score > best:
            best = score
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    existing = sorted(p.name for p in cfg.models_dir.glob("v*") if p.is_dir())
    version = f"v{len(existing) + 1}-{stamp}"
    out_dir = cfg.models_dir / version
    out_dir.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(out_dir)
    shutil.copy(LABELS_PATH, out_dir / "labels.yaml")
    export_onnx(model, tokenizer, out_dir, cfg.max_len)
    fp32 = out_dir / "model.fp32.onnx"
    if fp32.exists() and not args.keep_fp32:
        fp32.unlink()
    best_m = max(history, key=lambda h: h["intent_acc"] + h["slot_f1"])
    meta = {
        "version": version,
        "encoder": encoder,
        "labels_version": L["version"],
        "max_len": cfg.max_len,
        "created": stamp,
        "train_rows": len(train_rows),
        "dev_rows": len(dev_rows),
        "epochs": args.epochs,
        "frozen_layers": args.freeze_layers,
        "train_seconds": round(time.time() - t0, 1),
        "dev": best_m,
        "history": history,
        "sources": {s: sum(1 for r in rows if r["source"].split(":")[0] == s) for s in ("seed", "grammar", "correction")},
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"exported {out_dir} in {meta['train_seconds']}s total", flush=True)

    from lightai.train.evaluate import evaluate_model_dir, promote

    report = evaluate_model_dir(out_dir)
    (out_dir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}, indent=2))
    for f in report.get("failures", [])[:15]:
        print("  FAIL", f)
    if args.promote_if_better or args.promote:
        ok, why = promote(out_dir, report, force=args.promote)
        print(("PROMOTED " if ok else "NOT PROMOTED: ") + why)
        return 0 if ok else 4
    return 0
