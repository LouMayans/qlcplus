"""A stand-in for the language model: it answers with hand-written labels ('intent | [slot:words] markup'), so the whole
pipeline (normalizing, repairs, policy, planner) can be tested on sentences a model hasn't learned yet."""

from __future__ import annotations

from lightai.nlu.text import parse_markup


class StandInModel:
    labels_version = 4
    version = "stand-in"
    max_len = 128

    def __init__(self, answers: dict, fallback=None) -> None:
        self.answers = {k.lower(): v for k, v in answers.items()}  # text -> "intent | marked"
        self.fallback = fallback

    def predict(self, text: str) -> dict:
        line = self.answers.get(text.lower())
        if line is None:
            if self.fallback is None:
                raise KeyError(f"no stand-in answer for {text!r}")
            return self.fallback.predict(text)
        intent, marked = [x.strip() for x in line.split("|", 1)]
        plain, words, tags, _ = parse_markup(marked)
        offsets, pos = [], 0
        low = text.lower()
        for w in words:
            i = low.find(w.lower(), pos)
            if i < 0:
                raise ValueError(f"stand-in words don't match the text: {w!r} in {text!r}")
            offsets.append((i, i + len(w)))
            pos = i + len(w)
        return {"words": words, "tags": tags, "tag_probs": [1.0] * len(tags), "intent": intent, "confidence": 0.97,
                "intent_top": [(intent, 0.97)], "offsets": offsets}


def stand_in_parser(ai, answers: dict):
    """A Parser for the ai's rig that answers from `answers` (falling back to the real model for other sentences)."""
    from lightai.nlu.pipeline import Parser

    return Parser(ai.rig, StandInModel(answers, fallback=ai.parser.model), ai.parser.retriever)


def use_stand_in(ai, answers: dict) -> None:
    """Make the ai answer from `answers` for good: the app rebuilds its parser from ai.model after every change to the
    show, so the stand-in replaces the model itself (other sentences still go to the real model)."""
    from lightai.nlu.pipeline import Parser

    ai.model = StandInModel(answers, fallback=ai.model)
    ai.parser = Parser(ai.rig, ai.model, ai.retriever)
