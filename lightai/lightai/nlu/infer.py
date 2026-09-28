"""ONNX Runtime inference for the joint intent + slot model (no torch at runtime)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import yaml

from lightai.nlu.text import words_with_offsets


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


class NluModel:
    def __init__(self, model_dir: Path, threads: int = 2) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.dir = Path(model_dir)
        meta = json.loads((self.dir / "meta.json").read_text(encoding="utf-8"))
        lab = yaml.safe_load((self.dir / "labels.yaml").read_text(encoding="utf-8"))
        self.intents = list(lab["intents"].keys())
        slots = list(lab["slots"].keys())
        self.bio = ["O"] + [f"{p}-{s}" for s in slots for p in ("B", "I")]
        self.version = meta["version"]
        self.labels_version = int(lab["labels_version"])
        self.max_len = int(meta.get("max_len", 32))
        self.meta = meta
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(str(self.dir / "model.int8.onnx"), so, providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(str(self.dir / "tokenizer.json"))
        self.tok.no_padding()
        self.tok.enable_truncation(self.max_len)
        self.predict("warm up the model")

    def predict(self, text: str) -> dict:
        t0 = time.perf_counter()
        toks = words_with_offsets(text)
        words = [w for w, _, _ in toks]
        if not words:
            return {"words": [], "offsets": [], "tags": [], "tag_probs": [], "intent": "none", "confidence": 1.0,
                    "intent_top": [("none", 1.0)], "latency_ms": 0.0}
        enc = self.tok.encode(words, is_pretokenized=True)
        ids = np.array([enc.ids], dtype=np.int64)
        mask = np.array([enc.attention_mask], dtype=np.int64)
        il, sl = self.sess.run(None, {"input_ids": ids, "attention_mask": mask})
        ip = softmax(il[0])
        order = np.argsort(-ip)
        sp = softmax(sl[0], axis=-1)
        tags = ["O"] * len(words)
        probs = [1.0] * len(words)
        seen = set()
        for ti, wid in enumerate(enc.word_ids):
            if wid is None or wid in seen or wid >= len(words):
                continue
            seen.add(wid)
            k = int(sp[ti].argmax())
            tags[wid] = self.bio[k]
            probs[wid] = float(sp[ti][k])
        for i, t in enumerate(tags):
            if t.startswith("I-") and (i == 0 or tags[i - 1][2:] != t[2:] or tags[i - 1] == "O"):
                tags[i] = "B-" + t[2:]
        return {
            "words": words,
            "offsets": [(s, e) for _, s, e in toks],
            "tags": tags,
            "tag_probs": probs,
            "intent": self.intents[int(order[0])],
            "confidence": float(ip[order[0]]),
            "intent_top": [(self.intents[int(i)], round(float(ip[i]), 4)) for i in order[:3]],
            "latency_ms": (time.perf_counter() - t0) * 1000.0,
            "truncated": len(seen) < len(words),
        }
