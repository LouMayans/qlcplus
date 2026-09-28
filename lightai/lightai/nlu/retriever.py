"""Find existing functions from free text ("the red ballyhoo", "auto chaser show 3").

Hybrid score: number-aware lexical match (numbers must agree: "show 3" never matches "show 5")
plus, when the MiniLM ONNX encoder is installed, cosine similarity of sentence embeddings.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Optional

import numpy as np

STOP = {"the", "a", "an", "of", "on", "for", "and", "with", "look", "function", "one", "all"}


def _norm(s: str) -> str:
    t = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9#% ]", " ", s.lower())).strip()
    return re.sub(r"(\d) (bpm|ms|s|%)(?= |$)", r"\1\2", t)  # "60 bpm" == "60bpm"


def _numbers(s: str) -> list:
    return re.findall(r"\d+", s)


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split() if t not in STOP}


class Embedder:
    def __init__(self, model_dir: Path, threads: int = 2) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        path = model_dir / "model.int8.onnx"
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.no_padding()
        self.tok.enable_truncation(32)

    def embed(self, texts: list) -> np.ndarray:
        out = []
        for t in texts:
            enc = self.tok.encode(t)
            feed = {
                "input_ids": np.array([enc.ids], dtype=np.int64),
                "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
                "token_type_ids": np.array([enc.type_ids], dtype=np.int64),
            }
            h = self.sess.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0][0]
            m = np.array(enc.attention_mask, dtype=np.float32)[:, None]
            v = (h * m).sum(0) / max(1.0, m.sum())
            out.append(v / (np.linalg.norm(v) + 1e-9))
        return np.stack(out) if out else np.zeros((0, 384), dtype=np.float32)


_EMBEDDERS: dict = {}
_VEC_CACHE: dict = {}


class Retriever:
    def __init__(self, functions: dict, embedder: Optional[Embedder] = None, recent: Optional[dict] = None) -> None:
        self.items = [(f.id, f.name, f.path, f.type) for f in functions.values() if f.name]
        self.embedder = embedder
        self.recent = recent or {}
        self.vecs = None
        if embedder is not None and self.items:
            texts = [f"{n} ({t})" for _, n, _, t in self.items]
            cache = _VEC_CACHE.setdefault(id(embedder), {})
            todo = [t for t in dict.fromkeys(texts) if t not in cache]
            if todo:
                for t, v in zip(todo, embedder.embed(todo)):
                    cache[t] = v
            self.vecs = np.stack([cache[t] for t in texts])

    @classmethod
    def for_rig(cls, rig, models_dir: Optional[Path] = None, use_embeddings: bool = True) -> "Retriever":
        emb = None
        if use_embeddings and models_dir is not None:
            d = models_dir / "retriever"
            if (d / "model.int8.onnx").exists():
                key = (str(d), (d / "model.int8.onnx").stat().st_mtime_ns)
                emb = _EMBEDDERS.get(key)
                if emb is None:
                    try:
                        emb = Embedder(d)
                        _EMBEDDERS.clear()
                        _VEC_CACHE.clear()
                        _EMBEDDERS[key] = emb
                    except Exception:
                        emb = None
        return cls(rig.functions, emb)

    def lexical(self, query: str, name: str) -> float:
        q, n = _norm(query), _norm(name)
        if not q:
            return 0.0
        if q == n:
            return 1.0
        qn, nn = _numbers(q), _numbers(n)
        if qn and not all(x in nn for x in qn):
            return 0.0
        qt, nt = _tokens(q), _tokens(n)
        overlap = len(qt & nt) / max(1, len(qt)) if qt else 0.0
        cover = len(qt & nt) / max(1, len(nt)) if nt else 0.0
        ratio = difflib.SequenceMatcher(None, q, n).ratio()
        score = 0.55 * overlap + 0.2 * cover + 0.25 * ratio
        if qn and nn and set(qn) == set(nn):
            score += 0.1
        if nn and not qn and overlap > 0:
            score -= 0.05
        if n.startswith(q + " "):
            score -= 0.05
        return max(0.0, min(0.98, score))

    def search(self, query: str, k: int = 5) -> list:
        if not self.items:
            return []
        from lightai.nlu.normalize import digits_for_words

        query = digits_for_words(query.lower())
        lex = np.array([self.lexical(query, name) for _, name, _, _ in self.items], dtype=np.float32)
        if self.vecs is not None:
            qv = self.embedder.embed([query])[0]
            sem = self.vecs @ qv
            qn = _numbers(query)
            if qn:
                ok = np.array([all(x in _numbers(name) for x in qn) for _, name, _, _ in self.items])
                sem = np.where(ok, sem, sem - 0.5)
            score = 0.65 * lex + 0.35 * np.clip(sem, 0, 1)
        else:
            score = lex
        for i, (fid, _, _, _) in enumerate(self.items):
            if fid in self.recent:
                score[i] += min(0.03, 0.005 * self.recent[fid])
        qn_norm = _norm(query)
        for i, (_, name, _, _) in enumerate(self.items):
            if _norm(name) == qn_norm:
                score[i] += 0.05  # the exact name beats a longer name that merely contains it
        order = np.argsort(-score, kind="stable")[:k]
        return [
            {"function_id": int(self.items[i][0]), "name": self.items[i][1], "type": self.items[i][3], "score": round(float(score[i]), 4)}
            for i in order
            if score[i] > 0.05
        ]

    def resolve(self, query: str, k: int = 5) -> dict:
        cands = self.search(query, k=k)
        if not cands:
            return {"unresolved": query, "candidates": []}
        top = cands[0]
        # a look's own parts ("<look> - Color", "<look> - Breath") never make its name ambiguous
        others = [c for c in cands[1:] if not _norm(c["name"]).startswith(_norm(top["name"]) + " ")]
        gap = top["score"] - (others[0]["score"] if others else 0.0)
        out = {"function_id": top["function_id"], "name": top["name"], "type": top["type"], "score": top["score"], "gap": round(gap, 4), "candidates": cands}
        if top["score"] < 0.45:
            out["weak"] = True
        from lightai.nlu.normalize import digits_for_words

        q = _norm(digits_for_words(query.lower()))
        n_exact = sum(1 for c in cands if _norm(c["name"]) == q)
        exact = n_exact == 1 and _norm(top["name"]) == q
        if others and (n_exact > 1 or (gap < 0.1 and not exact)):
            out["ambiguous"] = True  # several functions share this exact name, or the top two are too close
            if n_exact > 1:
                out["same_name"] = n_exact
        return out
