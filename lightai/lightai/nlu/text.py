"""Word splitting and the bracket span markup used by the training data.

Markup: "slow [color:blue] [target:washes] [movement:breathing] at [rate:60 bpm]".
The same word splitter runs at training and inference time, so spans line up exactly.
"""

from __future__ import annotations

import re

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?(?:\d+[A-Za-z]*)*|\d+(?:\.\d+)?|[^\sA-Za-z\d]")  # 'beam230v2' is one word
MARK_RE = re.compile(r"\[([a-z_.]+):([^\]]+)\]")


def words_with_offsets(text: str) -> list:
    return [(m.group(0), m.start(), m.end()) for m in WORD_RE.finditer(text)]


def split_words(text: str) -> list:
    return [w for w, _, _ in words_with_offsets(text)]


def parse_markup(marked: str) -> tuple:
    """Return (plain_text, words, bio_tags, spans) for one marked-up sentence."""
    plain = []
    char_spans = []
    pos = 0
    out_len = 0
    for m in MARK_RE.finditer(marked):
        before = marked[pos : m.start()]
        plain.append(before)
        out_len += len(before)
        inner = m.group(2)
        char_spans.append((m.group(1), out_len, out_len + len(inner)))
        plain.append(inner)
        out_len += len(inner)
        pos = m.end()
    plain.append(marked[pos:])
    text = "".join(plain)
    if "[" in text or "]" in text:
        raise ValueError(f"unbalanced markup: {marked!r}")
    toks = words_with_offsets(text)
    tags = ["O"] * len(toks)
    spans = []
    for slot, s, e in char_spans:
        idx = [i for i, (_, ts, te) in enumerate(toks) if ts >= s and te <= e]
        if not idx:
            raise ValueError(f"empty span {slot!r} in {marked!r}")
        for j, i in enumerate(idx):
            tags[i] = ("B-" if j == 0 else "I-") + slot
        spans.append({"slot": slot, "start": idx[0], "end": idx[-1] + 1, "text": text[s:e]})
    return text, [w for w, _, _ in toks], tags, spans


def to_markup(words: list, tags: list) -> str:
    """Inverse of parse_markup for word lists (used when saving corrections)."""
    out = []
    cur_slot, cur_words = None, []

    def flush():
        nonlocal cur_slot, cur_words
        if cur_slot:
            out.append(f"[{cur_slot}:{' '.join(cur_words)}]")
        cur_slot, cur_words = None, []

    for w, t in zip(words, tags):
        if t.startswith("B-"):
            flush()
            cur_slot, cur_words = t[2:], [w]
        elif t.startswith("I-") and cur_slot == t[2:]:
            cur_words.append(w)
        else:
            flush()
            out.append(w)
    flush()
    return " ".join(out)


def spans_from_tags(words: list, tags: list, probs: list | None = None) -> list:
    spans = []
    i = 0
    while i < len(tags):
        t = tags[i]
        if t.startswith("B-") or (t.startswith("I-")):
            slot = t[2:]
            j = i + 1
            while j < len(tags) and tags[j] == f"I-{slot}":
                j += 1
            conf = min(probs[i:j]) if probs else 1.0
            spans.append({"slot": slot, "start": i, "end": j, "text": " ".join(words[i:j]), "confidence": float(conf)})
            i = j
        else:
            i += 1
    return spans
