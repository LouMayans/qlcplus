"""Text -> LightCommand: model prediction, span normalization, retrieval, policy."""

from __future__ import annotations

import re
import time
from typing import Optional

from lightai.nlu.infer import NluModel
from lightai.nlu.normalize import norm_direction, norm_fade, norm_movement, norm_priority, norm_rate, norm_size, normalize_slot  # noqa: F401
from lightai.nlu.policy import apply_policy
from lightai.nlu.retriever import Retriever
from lightai.nlu.text import spans_from_tags
from lightai.rig.model import Rig
from lightai.schema import LightCommand, SlotValue, Span, labels

FUNC_VERBS = (r"^(hey |ok |okay |yo |please |can you |could you |lightai |)*(start up|start|run|fire|play|go|turn on|trigger|kick off|launch|hit|"
              r"bring up|put on|engage|stop|turn off|end|cut|kill|switch off|drop|take down|shut off|fade out|is|are|check if|check|"
              r"delete|remove|get rid of|trash|add a button for|add a button to|make a button for|put a button for|give me a button for|"
              r"add|button for)\b\s*(the\s+)?")
SENTIMENT = r"(really |very |so |super )?(good|great|perfect|fine|nice|awesome|right|amazing|cool|sick|fire|dope|lit|wrong|off|bad|terrible|awful|ok|okay|correct|better|worse|weird|strange)"
STOPWORDS = {"the", "and", "all", "for", "with", "that", "this", "what", "whats", "from", "into", "over", "your", "you", "are", "is", "on", "off"}
BASE_VOCAB = {
    "light", "lights", "lighting", "look", "looks", "scene", "scenes", "chaser", "chasers", "show", "shows", "running", "playing",
    "status", "blackout", "black", "dark", "bpm", "tempo", "fixture", "fixtures", "channel", "channels", "dimmer", "strobe",
    "fog", "haze", "smoke", "rotate", "rotated", "move", "rename", "calibrate", "probe", "release", "override", "overrides",
    "master", "level", "live", "active", "patch", "patched", "universe", "rig", "function", "functions", "efx", "effect",
    "effects", "color", "colour", "colors", "kill", "stop", "everything", "stage", "desk", "state", "cue", "cues", "beam",
    "beams", "spot", "spots", "wash", "washes", "par", "pars", "dmx", "address", "lit", "bright", "brightness", "fade",
}
MERGE_SLOTS = {"value", "intensity", "rate", "angle", "distance", "channel", "size", "fade", "direction", "name"}
GENERIC = {"nice", "light", "lights", "thing", "things", "stuff", "movement", "something", "look", "one", "n", "&", "+", "-"}
NUMBER_ONLY = re.compile(r"(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
                         r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|a|and|\d+)(\s+(zero|one|two|three|four|"
                         r"five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|and|\d+))*")
MOVE_VERBS = {"strobe", "strobing", "pulse", "pulsing", "breathe", "breathing", "sweep", "sweeping", "circle", "circling", "spin", "spinning",
              "chase", "chasing", "flash", "flashing", "flicker", "flickering", "wave", "waving", "swap", "alternate", "bounce", "bouncing", "scan"}


def merge_spans(spans: list) -> list:
    """The tagger sometimes emits B-x B-x for one phrase ('two' 'hundred', 'minus' '90'); join adjacent pieces."""
    out: list = []
    for sp in spans:
        prev = out[-1] if out else None
        if prev and prev["end"] == sp["start"] and (
            (prev["slot"] == sp["slot"] and sp["slot"] in MERGE_SLOTS)
            or (sp["slot"] in ("intensity", "value") and NUMBER_ONLY.fullmatch(prev["text"].lower()))
        ):
            out[-1] = {"slot": sp["slot"], "start": prev["start"], "end": sp["end"], "text": prev["text"] + " " + sp["text"],
                       "confidence": min(prev["confidence"], sp["confidence"])}
        else:
            out.append(dict(sp))
    return out


MOVEMENT_SPECIFICITY = ["circle_wave", "figure8", "ballyhoo", "sweep", "running_light", "breathe", "strobe", "color_chase", "circle", "blackout_kill", "color_wash"]


COUNT_WORD = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a couple of|a pair of|couple of|pair of)"
PATCH_COUNT = re.compile(r"\b(?:add|patch|install|put|plug in|set up|hang|mount|rig up|need|want|got|have|bought|order)\s+(?:(?:in|up|another)\s+)?"
                         + COUNT_WORD + r"\b(?!\s*(?:%|percent|degrees?|cm|mm|m\b|bpm|seconds?))", re.I)
X_COUNT = re.compile(r"\b(\d{1,2})\s*x\b|\bx\s*(\d{1,2})\b|\b" + COUNT_WORD + r"\s+(?:more|new|extra)\b", re.I)
# '7 address type' is a mode; 'universe 2 address 150' is not (a number follows, or 'universe' comes before)
MODE_PHRASE = re.compile(r"(?<!universe )(?<!uni )\b(\d{1,3})\s*-?\s*(?:ch|chs|chan\w*|channel\w*|address\w*|addr|dmx|slot\w*)\b"
                         r"(?!\s*#?\s*\d)(?:\s+(?:mode|type|version))?", re.I)
MODEL_FILLER = re.compile(r"\b(?:fixtures?|units?|lights?|type|mode|more|new|extra|another|of|the|a|an|to|on|in|at|into|for)\b", re.I)
JUNK_NAMES = {"universe", "type", "fixtures", "fixture", "mode", "units", "address", "dmx"}


def address_from_text(text: str) -> tuple:
    """'to universe 1', 'universe 2 address 150', '2.300', 'starting at 200', 'dmx 97' -> ({universe, address} 0-based, phrase)."""
    from lightai.nlu.normalize import digits_for_words

    t = digits_for_words(text.lower())
    m = re.search(r"\b(\d+)\s*[./:]\s*(\d+)\b", t)
    if m:
        return {"universe": int(m.group(1)) - 1, "address": int(m.group(2)) - 1}, m.group(0)
    out, parts = {}, []
    u = re.search(r"\b(?:universe|uni)\s*#?\s*(\d+)\b", t)
    if u:
        out["universe"] = int(u.group(1)) - 1
        parts.append(u.group(0))
    a = re.search(r"\b(?:address|addr|dmx|start(?:ing)? at|starting from|from|at)\s*#?\s*(\d+)\b(?!\s*(?:%|percent|bpm))", t)
    if a:
        out["address"] = int(a.group(1)) - 1
        parts.append(a.group(0))
    return out, " ".join(parts)


PATCH_WHAT = re.compile(r"\b(?:add|patch|install|plug in|set up|hang|mount|bought|got|have)\s+(.+?)"
                        r"(?=\s+(?:to|on|in|at|into|called|named|as)\b|[,.!?]|$)", re.I)
# 'add 2 pars', 'add swarm fixture to project', 'add a swarm to universe 1': no number means one
PATCH_START = re.compile(r"^\s*(?:please\s+)?(?:add|patch|install|hang|mount)\s+"
                         r"(?:" + COUNT_WORD + r"\s*x?\s+(?:more\s+)?|(?:a|an|another|one more)\s+)?"
                         r"(?!(?:to|on|at|into|percent|points?|bpm|seconds?|degrees?|the|this|that|it|them|some)\b)(?P<what>[a-z][a-z0-9 \-]*?)"
                         r"(?=\s+(?:to|on|in|at|into)\s+(?:universe|uni|address|addr|dmx)\b"
                         r"|\s+(?:to|in|into)\s+(?:the\s+|this\s+|my\s+|our\s+|a\s+)?(?:project|show|patch|rig|workspace|file)\b"
                         r"|\s+(?:called|named|as)\b|\s*[.!]?\s*$)", re.I)
DEST = re.compile(r"\b(?:to|on|in|into|onto|at)\s+(?:universe|uni)\s*#?\s*(\d+)"
                  r"(?:\s*,?\s*(?:address|addr|dmx|start(?:ing)? at|starting from|from|at)\s*#?\s*(\d+))?", re.I)


def patch_groups(text: str) -> list:
    """'add 3 pars to universe 1 and 4 to universe 2' -> [(None, {universe: 0}, raw), (4, {universe: 1}, raw)].
    The first group takes the sentence's count; each later group takes the number said just before its universe."""
    from lightai.nlu.normalize import digits_for_words, norm_count

    t = digits_for_words(text.lower())
    dests = list(DEST.finditer(t))
    if len(dests) < 2:
        return []
    out = []
    for i, m in enumerate(dests):
        addr = {"universe": int(m.group(1)) - 1}
        if m.group(2):
            addr["address"] = int(m.group(2)) - 1
        n = None
        if i:
            c = re.search(r"\b" + COUNT_WORD + r"\s*x?\b", t[dests[i - 1].end():m.start()], re.I)
            n = norm_count(c.group(1)).get("count") if c else None
        out.append((n, addr, m.group(0).strip()))
    return out


class Parser:
    def __init__(self, rig: Rig, model: NluModel, retriever: Optional[Retriever] = None) -> None:
        self.rig = rig
        self.model = model
        self.retriever = retriever or Retriever(rig.functions)

    def parse(self, text: str, defaults: Optional[dict] = None) -> LightCommand:
        t0 = time.perf_counter()
        pred = self.model.predict(text)
        spans = merge_spans(spans_from_tags(pred["words"], pred["tags"], pred["tag_probs"]))
        cmd = LightCommand(
            labels_version=self.model.labels_version,
            model_version=self.model.version,
            text=text,
            words=pred["words"],
            intent=pred["intent"],
            confidence=round(pred["confidence"], 4),
            intent_top=pred["intent_top"],
        )
        offsets = pred["offsets"]
        for sp in spans:
            s_char = offsets[sp["start"]][0]
            e_char = offsets[sp["end"] - 1][1]
            raw = text[s_char:e_char]
            cmd.spans.append(Span(slot=sp["slot"], start=sp["start"], end=sp["end"], text=raw, confidence=round(sp["confidence"], 4)))
            if sp["slot"] == "function_ref":
                value = self.retriever.resolve(raw, k=50 if cmd.intent == "query.functions" else 5)
            else:
                value = normalize_slot(self.rig, sp["slot"], raw, text[:s_char], cmd.intent)
            cmd.slots.setdefault(sp["slot"], []).append(SlotValue(raw=raw, value=value))
        self.repair(cmd)
        self.guard(cmd)
        if pred.get("truncated"):
            cmd.ambiguities.append(f"sentence cut off after the first {self.model.max_len - 2} word pieces; the rest was ignored")
        apply_policy(cmd, defaults)
        if pred.get("truncated") and not cmd.clarify and cmd.intent != "none":
            cmd.clarify = "That was a long one and I only read the start. Say just the lighting part, e.g. 'fast white strobe on the spots'."
        cmd.latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        return cmd

    def _relabel(self, cmd: LightCommand, sv: SlotValue, old: str, new: str, value: dict) -> None:
        cmd.slots[old].remove(sv)
        if not cmd.slots[old]:
            cmd.slots.pop(old)
        cmd.slots.setdefault(new, []).append(SlotValue(raw=sv.raw, value=value))
        for sp in cmd.spans:
            if sp.slot == old and sp.text == sv.raw:
                sp.slot = new
                break

    def function_fallback(self, cmd: LightCommand, text: str) -> None:
        """run/stop/status with no function tagged: the words after the verb are the function name.

        Names lightai generates ("Blue Washes Breathe 60bpm") are made of color/zone/effect/tempo
        words, so the tagger can label them as those slots instead of one function name.
        """
        if cmd.intent not in ("run_function", "stop_function", "query.status", "delete_look", "add_widget"):
            return
        tagged = cmd.first("function_ref")
        rest = re.sub(FUNC_VERBS, "", text.strip(), count=1, flags=re.I).strip(" .!?")
        rest = re.sub(r"\s+(please|now|running|playing|on|active|look|scene|function)$", "", rest, flags=re.I).strip()
        if tagged and not any(tagged.value.get(k) for k in ("ambiguous", "weak", "unresolved")):
            # the tagger can cut an exact name short ('... medium' + 'mirrored'); the whole remainder wins if it is itself
            # the exact name of a different function (several functions with that same name still count: the policy asks)
            if len(rest) < 3 or _norm_name(rest) == _norm_name(tagged.raw):
                return
            whole = self.retriever.resolve(rest)
            if (whole.get("function_id") in (None, tagged.value.get("function_id"))
                    or (whole.get("ambiguous") and not whole.get("same_name"))
                    or _norm_name(whole.get("name", "")) != _norm_name(rest)):
                return
        if len(rest) < 3:
            return
        found = self.retriever.resolve(rest)
        if found.get("unresolved") or found.get("weak"):
            return
        exact = _norm_name(found["name"]) == _norm_name(rest)
        if tagged and found.get("ambiguous") and not exact and not tagged.value.get("unresolved"):
            return
        for slot in ("color", "target", "movement", "rate", "intensity", "fade", "size", "priority", "name", "observed"):
            cmd.slots.pop(slot, None)
        cmd.spans = [sp for sp in cmd.spans if sp.slot == "function_ref"]
        cmd.slots["function_ref"] = [SlotValue(raw=rest, value=found)]
        cmd.ambiguities.append(f"read '{rest}' as a function name")

    def color_refs(self, cmd: LightCommand) -> None:
        """'turn the spots green' read as run_function 'SPOT 1 Green': a function_ref made only of colors, next to a target, is a color."""
        refs = cmd.slots.get("function_ref") or []
        if cmd.intent == "update_look" and not refs and cmd.slots.get("target") and cmd.slots.get("color"):
            cmd.ambiguities.append("no look was named, only fixtures and a color: read it as a new look")
            cmd.intent = "create_look"
            return
        if not refs or not cmd.slots.get("target") or cmd.intent not in ("create_look", "propose_look", "run_function", "update_look"):
            return
        if cmd.intent in ("run_function", "update_look"):  # 'run All Lights Red' names an existing function exactly
            rest = re.sub(FUNC_VERBS, "", cmd.text.strip(), count=1, flags=re.I).strip(" .!?")
            found = self.retriever.resolve(rest) if len(rest) > 2 else {}
            if found.get("name") and _norm_name(found["name"]) == _norm_name(rest):
                return
        for sv in list(refs):
            parts = [p for p in re.split(r"\s*(?:,|\band\b|&)\s*|\s+", sv.raw.lower()) if p]
            cols = [self.rig.colors.canonical(p) for p in parts]
            if not cols or not all(cols):
                return
            for part in [p for p in re.split(r"\s*(?:,|\band\b|&)\s*", sv.raw) if p.strip()]:
                cmd.slots.setdefault("color", []).append(SlotValue(raw=part, value=normalize_slot(self.rig, "color", part)))
            low = cmd.text.lower()  # keep the sentence order: zones and colors are paired by position
            cmd.slots["color"].sort(key=lambda c: low.find(c.raw.lower()))
            refs.remove(sv)
            for sp in cmd.spans:
                if sp.slot == "function_ref" and sp.text == sv.raw:
                    sp.slot = "color"
        if not refs:
            cmd.slots.pop("function_ref", None)
            if cmd.intent in ("run_function", "update_look"):
                cmd.ambiguities.append("read the color words as a new look, not as a function name")
                cmd.intent = "create_look"

    def movement_fallback(self, cmd: LightCommand) -> None:
        """'strobe the spots', 'pulse the washes red': an untagged movement verb still picks the recipe."""
        if cmd.intent not in ("create_look", "propose_look") or cmd.slots.get("movement"):
            return
        bad_color = {i for sp in cmd.spans if sp.slot == "color" and not self.rig.colors.canonical(sp.text) for i in range(sp.start, sp.end)}
        bad_color |= {i for sp in cmd.spans if sp.slot in ("fade", "size", "rate", "intensity") and not normalize_slot(self.rig, sp.slot, sp.text)
                      for i in range(sp.start, sp.end)}
        covered = {i for sp in cmd.spans for i in range(sp.start, sp.end)} - bad_color
        for i, w in enumerate(cmd.words):
            if i in covered or w.lower() not in MOVE_VERBS:
                continue
            mv = norm_movement(w)
            if not mv.get("recipe"):
                continue
            cmd.slots["movement"] = [SlotValue(raw=w, value=mv)]
            cmd.ambiguities.append(f"read '{w}' as the movement")
            if i in bad_color:  # 'the washes should breathe blue': 'breathe' had been tagged as a color (or a fade)
                for slot in ("color", "fade", "size", "rate", "intensity"):
                    if slot in cmd.slots:
                        cmd.slots[slot] = [sv for sv in cmd.slots[slot] if sv.raw.lower() != w.lower()]
                        if not cmd.slots[slot]:
                            cmd.slots.pop(slot)
                cmd.spans = [sp for sp in cmd.spans if not (sp.slot != "movement" and sp.text.lower() == w.lower())]
            return

    def split_target_direction(self, cmd: LightCommand) -> None:
        """'move wash 1 left 50 cm' tagged 'wash 1 left' as the target: a trailing direction word is the direction."""
        if cmd.intent not in ("fixture_edit.move", "fixture_edit.rotate") or cmd.slots.get("direction"):
            return
        for sv in cmd.slots.get("target") or []:
            words = sv.raw.split()
            if len(words) < 2:
                continue
            d = norm_direction(words[-1])
            if d.get("dir") not in ("left", "right", "up", "down"):
                continue
            head = " ".join(words[:-1])
            tv = normalize_slot(self.rig, "target", head)
            if not tv.get("fixture_ids"):
                continue
            old = sv.raw
            sv.raw, sv.value = head, tv
            cmd.slots["direction"] = [SlotValue(raw=words[-1], value=d)]
            for sp in cmd.spans:
                if sp.slot == "target" and sp.text == old:
                    sp.text, sp.end = head, sp.end - 1
                    cmd.spans.append(Span(slot="direction", start=sp.end, end=sp.end + 1, text=words[-1], confidence=sp.confidence))
                    break
            cmd.ambiguities.append(f"split '{old}' into target '{head}' + direction '{words[-1]}'")
            return
        covered = {i for sp in cmd.spans for i in range(sp.start, sp.end)}
        for i, w in enumerate(cmd.words):  # 'move fixture 3 up 30 cm' with 'up' left untagged
            d = norm_direction(w) if i not in covered else {}
            if d.get("dir") in ("left", "right", "up", "down"):
                cmd.slots["direction"] = [SlotValue(raw=w, value=d)]
                cmd.spans.append(Span(slot="direction", start=i, end=i + 1, text=w, confidence=0.5))
                cmd.ambiguities.append(f"read '{w}' as the direction")
                return

    def priority_fallback(self, cmd: LightCommand) -> None:
        """'create a kill for the spots at p100': 'p100' / 'priority 5' sets the priority, whatever else it was tagged as."""
        if cmd.intent not in ("create_look", "propose_look", "update_look"):
            return
        m = re.search(r"\b(p\s?\d{1,3}|priority (of )?\d{1,3})\b", cmd.text, re.I)
        if not m:
            return
        tok = m.group(0).lower()
        for slot in ("intensity", "function_ref", "fixture_model", "target", "rate", "value", "name"):  # same words, wrong label
            for sv in list(cmd.slots.get(slot) or []):
                raw = sv.raw.lower().strip()
                if raw == tok or (slot in ("target", "rate", "value") and raw in tok.split()):
                    cmd.slots[slot].remove(sv)
                    cmd.spans = [sp for sp in cmd.spans if not (sp.slot == slot and sp.text.lower().strip() == raw)]
                    cmd.ambiguities.append(f"'{sv.raw}' is part of the priority, not a {slot}")
            if slot in cmd.slots and not cmd.slots[slot]:
                cmd.slots.pop(slot)
        if not cmd.slots.get("priority"):
            val = norm_priority(m.group(0))
            if val:
                cmd.slots["priority"] = [SlotValue(raw=m.group(0), value=val)]
                cmd.ambiguities.append(f"read '{m.group(0)}' as the priority")

    def split_movement_targets(self, cmd: LightCommand) -> None:
        """'color chase spots and washes': the movement span swallowed a zone; split it back into movement + target."""
        if cmd.intent not in ("create_look", "propose_look", "update_look"):
            return
        for sv in list(cmd.slots.get("movement") or []):
            words = sv.raw.split()
            for i in range(1, len(words)):
                head, tail = " ".join(words[:i]), " ".join(words[i:])
                mv = norm_movement(head)
                if not mv.get("recipe"):
                    continue
                tv = normalize_slot(self.rig, "target", tail)
                if not tv.get("fixture_ids") or tv.get("unresolved"):
                    continue
                sv.raw, sv.value = head, mv
                cmd.slots.setdefault("target", []).append(SlotValue(raw=tail, value=tv))
                for sp in cmd.spans:
                    if sp.slot == "movement" and sp.text == " ".join(words):
                        sp.text, sp.end = head, sp.start + i
                        cmd.spans.append(Span(slot="target", start=sp.start + i, end=sp.start + len(words), text=tail, confidence=sp.confidence))
                        break
                cmd.ambiguities.append(f"split '{' '.join(words)}' into movement '{head}' + target '{tail}'")
                break

    def relabel_empty(self, cmd: LightCommand) -> None:
        """A span whose words mean nothing for its slot ('vip' as a movement, 'tight' as a fade) gets the slot that reads it;
        a generic word ('nice', 'light', 'thing') that reads as nothing is dropped instead of asked about."""
        if cmd.intent not in ("create_look", "propose_look", "update_look"):
            return
        tries = (("priority", norm_priority), ("size", norm_size), ("rate", norm_rate), ("fade", norm_fade),
                 ("movement", lambda r: norm_movement(r) if norm_movement(r).get("recipe") else {}), ("direction", norm_direction))
        for slot in ("movement", "fade", "size", "rate", "color"):
            for sv in list(cmd.slots.get(slot) or []):
                v = sv.value
                empty = (slot == "movement" and not v.get("recipe")) or (slot == "color" and v.get("unresolved")) or (slot not in ("movement", "color") and not v)
                if not empty:
                    continue
                for new, fn in tries:
                    if new == slot:
                        continue
                    val = fn(sv.raw)
                    if val:
                        self._relabel(cmd, sv, slot, new, val)
                        cmd.ambiguities.append(f"read '{sv.raw}' as {new}, not {slot}")
                        break
                else:
                    if sv.raw.lower().strip() in GENERIC:
                        cmd.slots[slot].remove(sv)
                        if not cmd.slots[slot]:
                            cmd.slots.pop(slot)
                        cmd.spans = [sp for sp in cmd.spans if not (sp.slot == slot and sp.text == sv.raw)]
                        cmd.ambiguities.append(f"ignored '{sv.raw}'")

    def look_words_ref(self, cmd: LightCommand) -> None:
        """'SPOTS RED CIRCLES 128' tagged as a function name on a new look: its words are color/movement/tempo."""
        if cmd.intent not in ("create_look", "propose_look"):
            return
        for sv in list(cmd.slots.get("function_ref") or []):
            parts = sv.raw.split()
            vals = []
            for w in parts:
                if self.rig.colors.canonical(w):
                    vals.append(("color", w, normalize_slot(self.rig, "color", w)))
                elif norm_movement(w).get("recipe"):
                    vals.append(("movement", w, norm_movement(w)))
                elif norm_rate(w):
                    vals.append(("rate", w, norm_rate(w)))
                elif not normalize_slot(self.rig, "target", w).get("unresolved"):
                    vals.append(("target", w, normalize_slot(self.rig, "target", w)))
                else:
                    vals = []
                    break
            if not vals:
                continue
            cmd.slots["function_ref"].remove(sv)
            if not cmd.slots["function_ref"]:
                cmd.slots.pop("function_ref")
            for slot, w, v in vals:
                cmd.slots.setdefault(slot, []).append(SlotValue(raw=w, value=v))
            cmd.ambiguities.append(f"read '{sv.raw}' as look words, not a function name")

    def feedback_colors(self, cmd: LightCommand) -> None:
        """'i said yellow but they're white': the color after said/wanted/should be is the expected one; 'X not Y',
        'X instead of Y', 'more X than Y' put the seen color first; otherwise the first color is the expected one."""
        if cmd.intent != "feedback":
            return
        text = cmd.text.lower()
        mentions = []
        for slot in ("color", "observed"):
            for sv in cmd.slots.get(slot) or []:
                c = self.rig.colors.canonical(sv.raw)
                if c:
                    mentions.append((text.find(sv.raw.lower()), c, sv))
        mentions.sort(key=lambda m: m[0])
        if not mentions:
            return
        want = re.compile(r"(said|asked for|asked|wanted|want|should be|supposed to be|meant|expected)\W+(?:\w+\W+){0,2}$")
        seen = re.compile(r"(seeing|see|shows?|showing|it'?s|its|that'?s|thats|looks?( like)?|looked( like)?|came out|coming out|got|is|are|was|were)\W+(?:\w+\W+){0,1}$")
        roles = {}
        if len(mentions) >= 2:
            (p1, c1, _), (p2, c2, _) = mentions[0], mentions[1]
            between = text[p1:p2]
            if any(want.search(text[:p]) for p, _, _ in mentions[1:2]):
                roles = {"expected": c2, "observed": c1}
            elif want.search(text[:p1]):
                roles = {"expected": c1, "observed": c2}
            elif re.search(r"\b(not|instead of|rather than|than)\b", between):
                roles = {"expected": c2, "observed": c1}
            else:
                roles = {"expected": c1, "observed": c2}
        else:
            p, c, _ = mentions[0]
            roles = {"expected": c} if want.search(text[:p]) or not seen.search(text[:p]) else {"observed": c}
            if "observed" in roles and cmd.slots.get("observed") and self.rig.colors.canonical(cmd.slots["observed"][0].raw) == c:
                return
            if "expected" in roles and cmd.slots.get("color") and not cmd.slots.get("observed"):
                return
        if roles.get("expected") == roles.get("observed"):
            roles.pop("observed")
        keep_obs = [sv for sv in cmd.slots.get("observed") or [] if not self.rig.colors.canonical(sv.raw)]  # 'nothing', 'fixture 7'
        cmd.slots.pop("color", None)
        cmd.slots.pop("observed", None)
        if roles.get("expected"):
            cmd.slots["color"] = [SlotValue(raw=roles["expected"], value=normalize_slot(self.rig, "color", roles["expected"]))]
        if roles.get("observed"):
            keep_obs = [SlotValue(raw=roles["observed"], value={"color": roles["observed"]})] + keep_obs
        if keep_obs:
            cmd.slots["observed"] = keep_obs
        cmd.ambiguities.append(f"feedback colors: expected {roles.get('expected')}, seen {roles.get('observed')}")

    def speed_change(self, cmd: LightCommand) -> None:
        """'speed up the X' / 'slow down the X' / 'X faster' changes a look's speed; it neither starts nor stops it."""
        if cmd.intent not in ("run_function", "stop_function") or not cmd.slots.get("function_ref"):
            return
        if re.match(r"\s*(please\s+)?(speed (it )?up|slow (it )?down)\b", cmd.text, re.I) or re.search(r"\b(faster|slower|quicker)\W*$", cmd.text, re.I):
            cmd.ambiguities.append(f"read '{cmd.text}' as a speed change of the look, not {cmd.intent.split('_')[0]}")
            cmd.intent = "update_look"

    def fixture_model_retry(self, cmd: LightCommand) -> None:
        """'add another beam230 v3' tagged only 'v3': retry the library match on the words after the verb."""
        from lightai.nlu.normalize import resolve_library_model

        sv = cmd.first("fixture_model")
        if cmd.intent != "add_fixture" or sv is None or not (sv.value.get("ambiguous") or sv.value.get("unresolved")):
            return
        rest = re.sub(r"^.*?\b(add|patch|plug in|install|set up|bought|got|have)\b\s+((a|an|another|new|the)\s+)*", "", cmd.text, count=1, flags=re.I)
        rest = re.split(r"\s+(?:at|on|in|called|named|as)\s+|,", rest, maxsplit=1)[0]
        if not rest or rest.lower() == sv.raw.lower():
            return
        v = resolve_library_model(self.rig.library, rest)
        if not v.get("unresolved") and not v.get("ambiguous"):
            cmd.slots["fixture_model"] = [SlotValue(raw=rest, value=v)]
            cmd.ambiguities.append(f"read the fixture model as '{rest}'")

    def level_only(self, cmd: LightCommand) -> None:
        """'mayans beam230 at 50%' read as 'create a look' (0.94): a sentence that is only fixtures and a percentage, with
        fixtures that resolve, is a level change."""
        if cmd.intent == "set_level":
            return
        m = re.fullmatch(r"\s*(?:set\s+|put\s+|bring\s+)?(?:the\s+|all\s+(?:the\s+)?)?(.+?)\s+(?:at|to)\s+(\d{1,3})\s*(?:%|percent)\s*[.!]?\s*",
                         cmd.text, re.I)
        if not m or int(m.group(2)) > 100 or any(cmd.slots.get(s) for s in ("color", "movement", "rate", "strobe", "fade", "function_ref")
                                                 if cmd.first(s) is not None and cmd.first(s).raw.lower() not in m.group(1).lower()):
            return
        val = normalize_slot(self.rig, "target", m.group(1))
        if not val.get("fixture_ids"):
            return
        cmd.ambiguities.append(f"'{cmd.text.strip()}' sets a level (model said {cmd.intent} at {cmd.confidence:.2f})")
        cmd.intent_top = [("set_level", 1.0)] + [t for t in cmd.intent_top if t[0] != "set_level"]
        cmd.intent, cmd.confidence = "set_level", max(cmd.confidence, 0.9)
        for s in ("color", "movement", "rate", "strobe", "fade", "function_ref", "name"):
            cmd.slots.pop(s, None)
        cmd.slots["target"] = [SlotValue(raw=m.group(1), value=val)]
        pct = f"{m.group(2)}%"
        cmd.slots["intensity"] = [SlotValue(raw=pct, value=normalize_slot(self.rig, "intensity", pct))]

    def join_model_spans(self, cmd: LightCommand) -> None:
        """'beam230 v3' tagged as target 'beam230' + movement 'v3', 'mayans wash' as model 'mayans' + movement 'wash': when
        the next tag's words make a more exact name of one of your fixture models, they belong to the same name."""
        from lightai.rig.house import house_models, match_house_models

        spans = sorted(cmd.spans, key=lambda s: s.start)
        for a, b in zip(spans, spans[1:]):
            if a.slot not in ("target", "fixture_model") or b.start != a.end or b not in cmd.spans \
                    or b.slot not in ("movement", "function_ref", "name", "direction", "observed", "target", "fixture_model"):
                continue
            house = house_models(self.rig, only_rig=a.slot == "target")
            joined = f"{a.text} {b.text}"
            one = match_house_models(self.rig, a.text, house=house, need_model_word=True, zones=False)
            both = match_house_models(self.rig, joined, house=house, need_model_word=True, zones=False)
            if not both or (one and both[0][0] <= one[0][0]):
                continue
            first = next((sv for sv in cmd.slots.get(a.slot) or [] if sv.raw == a.text), None)
            second = next((sv for sv in cmd.slots.get(b.slot) or [] if sv.raw == b.text), None)
            if first is None:
                continue
            first.raw, first.value = joined, normalize_slot(self.rig, a.slot, joined, intent=cmd.intent)
            if second is not None:
                cmd.slots[b.slot].remove(second)
                if not cmd.slots[b.slot]:
                    cmd.slots.pop(b.slot)
            a.end, a.text = b.end, joined
            cmd.spans.remove(b)
            cmd.ambiguities.append(f"'{joined}' is one fixture name")

    def maker_targets(self, cmd: LightCommand) -> None:
        """'mayans washes at 50%' tagged as two targets, 'mayans' and 'washes': the maker's name narrows the other target
        to that maker's fixtures instead of standing alone as every Mayans fixture."""
        tv = cmd.slots.get("target") or []
        if len(tv) < 2:
            return
        from lightai.rig.house import house_models, match_house_models

        house = house_models(self.rig, only_rig=True)
        makers = []
        for sv in tv:
            hits = match_house_models(self.rig, sv.raw, house=house, need_model_word=True, zones=False)
            if hits and all(pts == 0 for pts, _ in hits):
                makers.append((sv, {i for _, e in hits for i in e["ids"]}))
        if not makers or len(makers) == len(tv):
            return
        for sv, ids in makers:
            tv.remove(sv)
            for other in tv:
                keep = [i for i in other.value.get("fixture_ids") or [] if i in ids]
                if keep:
                    other.value = dict(other.value, fixture_ids=keep)
            cmd.ambiguities.append(f"'{sv.raw}' is the maker: only its fixtures")

    def patch_intent(self, cmd: LightCommand) -> None:
        """'add 2 pars': too short for the model to be sure (0.30). A count plus words that name a fixture model you use
        (or exactly one library model) is a patch request."""
        from lightai.nlu.normalize import resolve_library_model
        from lightai.rig.house import match_house_models

        m = PATCH_START.match(cmd.text)
        if not m or (cmd.intent == "add_fixture" and cmd.confidence >= 0.8):
            return
        strong = bool(re.search(r"\b(?:fixtures?|units?)\b", m.group("what"), re.I)) or bool(re.search(
            r"^\s+(?:to|in|into|on)\s+(?:the\s+|this\s+|my\s+)?(?:project|show|patch|universe|uni)\b", cmd.text[m.end("what"):], re.I))
        # a move never says 'add/hang'; 'none' is no reading at all; 'fixture' or 'to project' is patch wording
        if cmd.confidence >= 0.8 and cmd.intent not in ("fixture_edit.readdress", "none") and not strong:
            return
        if re.search(r"\b(?:to|on|in|at|into|onto|for|with|from)\b", m.group("what"), re.I):  # 'add 2 washes to the look'
            return
        what = re.sub(r"\s+", " ", MODEL_FILLER.sub(" ", MODE_PHRASE.sub(" ", m.group("what")))).strip()
        if not what:
            return
        lib = resolve_library_model(self.rig.library, what)
        counted = bool(m.group(1))  # without a number only a model you name counts ('add swarm'), never an alias ('add fog')
        if not match_house_models(self.rig, what, need_model_word=not counted) and (lib.get("unresolved") or lib.get("ambiguous")):
            return
        cmd.ambiguities.append(f"'{m.group(0).strip()}' patches new fixtures (model said {cmd.intent} at {cmd.confidence:.2f})")
        cmd.intent_top = [("add_fixture", 1.0)] + [t for t in cmd.intent_top if t[0] != "add_fixture"]
        cmd.intent, cmd.confidence = "add_fixture", max(cmd.confidence, 0.9)
        for sv in cmd.slots.get("fixture_model") or []:  # normalized for another intent: redo it as a library model
            sv.value = normalize_slot(self.rig, "fixture_model", sv.raw, intent="add_fixture")

    def patch_words(self, cmd: LightCommand) -> None:
        """add_fixture / fixture_edit.readdress: count, universe and address come from the words; the model phrase is cleaned."""
        if cmd.intent not in ("add_fixture", "fixture_edit.readdress"):
            return
        if cmd.slots.pop("function_ref", None):  # 'add 1 par': 'par' also tagged as a function; patching uses none
            cmd.ambiguities.append("ignored a function name: patching doesn't use functions")
        from lightai.nlu.normalize import norm_count, norm_mode, prefer_rig_model, resolve_library_model
        from lightai.rig.house import assume_house_model

        addr, phrase = address_from_text(cmd.text)
        if addr:  # 'to universe 1' is universe 1, never address 1
            old = cmd.first("address")
            if old is None or old.value != addr:
                cmd.slots["address"] = [SlotValue(raw=phrase, value=addr)]
                cmd.ambiguities.append(f"address read from the words: '{phrase}'")
        if cmd.intent == "fixture_edit.readdress":
            tv = cmd.first("target")
            if tv is None or not tv.value.get("fixture_ids"):  # 'move the vpars to universe 1' tagged 'the' as the target
                m = re.search(r"\b(?:move|readdress|re-address|repatch|re-patch|shift|put|swap|give|assign|address of|set)\s+(.+?)"
                              r"(?=\s+(?:to|on|at|over to|onto|into|the address|address)\b)", cmd.text, re.I)
                if m:
                    words = re.sub(r"^(?:the|all the|all)\s+", "", m.group(1).strip(), flags=re.I)
                    val = normalize_slot(self.rig, "target", words)
                    if val.get("fixture_ids"):
                        cmd.slots["target"] = [SlotValue(raw=words, value=val)]
                        cmd.ambiguities.append(f"fixtures read as '{words}'")
            return
        for sv in list(cmd.slots.get("name") or []):  # 'universe', 'type' tagged as a name
            if sv.raw.lower().strip() in JUNK_NAMES:
                cmd.slots["name"].remove(sv)
        for sv in list(cmd.slots.get("target") or []):  # 'fixtures' tagged as a target
            if not sv.value.get("fixture_ids"):
                cmd.slots["target"].remove(sv)
        for slot in ("name", "target"):
            if slot in cmd.slots and not cmd.slots[slot]:
                cmd.slots.pop(slot)
        if not cmd.slots.get("count"):
            m = PATCH_COUNT.search(cmd.text) or X_COUNT.search(cmd.text)
            if m:
                raw = next(g for g in m.groups() if g)
                val = norm_count(raw)
                if val:
                    cmd.slots["count"] = [SlotValue(raw=raw, value=val)]
                    cmd.ambiguities.append(f"read '{raw}' as how many fixtures")
        groups = patch_groups(cmd.text)
        if groups:  # '3 ... to universe 1 and 4 to universe 2': one count and one address per group, in order
            first = cmd.first("count")
            counts = [first if first is not None else SlotValue(raw="?", value={})]
            counts += [SlotValue(raw=str(n) if n else "?", value={"count": n} if n else {}) for n, _, _ in groups[1:]]
            cmd.slots["count"] = counts
            cmd.slots["address"] = [SlotValue(raw=raw, value=addr) for _, addr, raw in groups]
            cmd.ambiguities.append("groups read from the words: " + "; ".join(
                f"{c.value.get('count', '?')} {raw}" for c, (_, _, raw) in zip(counts, groups)))
        sv = cmd.first("fixture_model")
        if sv is not None and re.fullmatch(r"\s*\d+\s*", sv.raw):  # a lone number names no model
            sv.value = {"unresolved": sv.raw}
        if sv is None:  # nothing tagged: the words after the verb, up to where/what they are called
            m = PATCH_WHAT.search(cmd.text)
            if not m:
                return
            sv = SlotValue(raw=m.group(1), value={})
            cmd.slots["fixture_model"] = [sv]
        raw = sv.raw
        rest = cmd.text.replace(phrase, " ") if phrase else cmd.text  # never read the address as a mode
        mm = MODE_PHRASE.search(raw) or (MODE_PHRASE.search(rest) if not cmd.slots.get("mode") else None)
        if mm and not cmd.slots.get("mode"):
            cmd.slots["mode"] = [SlotValue(raw=mm.group(0), value=norm_mode(mm.group(0)))]
        cnt = cmd.first("count")

        def cleaned(s: str) -> str:
            s = MODE_PHRASE.sub(" ", s)
            if cnt is not None and cnt.raw != "?":
                s = re.sub(r"^\s*" + re.escape(cnt.raw) + r"\b", " ", s, flags=re.I)
            s = re.sub(r"^\s*" + COUNT_WORD + r"\s*x?\b", " ", s, flags=re.I)
            s = MODEL_FILLER.sub(" ", s)
            return re.sub(r"\s+", " ", s).strip()

        def resolve(s: str) -> dict:
            return assume_house_model(self.rig, s, prefer_rig_model(self.rig, resolve_library_model(self.rig.library, s)))

        clean = cleaned(raw)
        if clean and (clean.lower() != raw.lower().strip() or sv.value.get("ambiguous") or sv.value.get("unresolved") or not sv.value):
            val = resolve(clean)
            if not val.get("unresolved"):
                sv.raw, sv.value = clean, val
                cmd.ambiguities.append(f"fixture model read as '{clean}'")
        if sv.value.get("ambiguous") or sv.value.get("unresolved") or not sv.value:  # the tagger caught part of a name ('moving' of 'moving head')
            m = PATCH_WHAT.search(cmd.text)
            wide = cleaned(m.group(1)) if m else ""
            if wide and wide.lower() != sv.raw.lower().strip():
                val = resolve(wide)
                if not val.get("unresolved") and not val.get("ambiguous"):
                    sv.raw, sv.value = wide, val
                    cmd.ambiguities.append(f"fixture model read as '{wide}'")

    def repair(self, cmd: LightCommand) -> None:
        """Deterministic fixes for known tagger confusions; each one is recorded in ambiguities."""
        self.join_model_spans(cmd)
        self.level_only(cmd)
        self.patch_intent(cmd)
        self.maker_targets(cmd)
        if re.fullmatch(r"\s*release( (all|everything|overrides|the overrides|all overrides|all channels))?\s*[.!]?\s*", cmd.text, re.I) \
                and cmd.intent != "release":
            cmd.ambiguities.append(f"'{cmd.text.strip()}' hands every override back to the looks (model said {cmd.intent})")
            cmd.intent_top = [("release", 1.0)] + cmd.intent_top
            cmd.intent, cmd.confidence = "release", max(cmd.confidence, 0.9)
        elif re.fullmatch(r"\s*(full |total )?(black ?out|blackout)( now| please| everything)?\s*[.!]*\s*", cmd.text, re.I) and cmd.intent != "blackout":
            cmd.ambiguities.append(f"'{cmd.text.strip()}' is the blackout (model said {cmd.intent})")
            cmd.intent_top = [("blackout", 1.0)] + cmd.intent_top
            cmd.intent, cmd.confidence = "blackout", max(cmd.confidence, 0.9)
        elif re.fullmatch(r"\s*(turn )?(all )?(the )?lights? (back )?(on|up)( please| now)?\s*[.!]*\s*", cmd.text, re.I) and cmd.intent != "blackout_release":
            cmd.ambiguities.append(f"'{cmd.text.strip()}' ends the blackout (model said {cmd.intent})")
            cmd.intent_top = [("blackout_release", 1.0)] + cmd.intent_top
            cmd.intent, cmd.confidence = "blackout_release", max(cmd.confidence, 0.9)
            cmd.slots.pop("intensity", None)
            cmd.spans = [sp for sp in cmd.spans if sp.slot != "intensity"]
        if cmd.intent == "stop_all" and re.search(r"\b(release|overrides?|simple desk|manual levels?|hand (control )?back|give back)\b", cmd.text, re.I):
            cmd.intent_top = [("release", cmd.confidence)] + cmd.intent_top
            cmd.intent = "release"
            cmd.ambiguities.append("'release ...' hands overrides back to the looks; it does not stop them")
        self.color_refs(cmd)
        self.look_words_ref(cmd)
        self.relabel_empty(cmd)
        self.movement_fallback(cmd)
        self.function_fallback(cmd, cmd.text)
        self.feedback_colors(cmd)
        self.fixture_model_retry(cmd)
        self.patch_words(cmd)
        self.speed_change(cmd)
        for sv in list(cmd.slots.get("observed") or []):
            if set(sv.value) == {"text"} and re.fullmatch(SENTIMENT, sv.raw.lower().strip()):
                cmd.slots["observed"].remove(sv)
                if not cmd.slots["observed"]:
                    cmd.slots.pop("observed")
                cmd.spans = [sp for sp in cmd.spans if not (sp.slot == "observed" and sp.text == sv.raw)]
                cmd.ambiguities.append(f"'{sv.raw}' is a verdict, not something seen")
        moves = list(cmd.slots.get("movement") or [])
        if moves and not cmd.slots.get("target") and cmd.intent in ("create_look", "propose_look"):
            wash_like = [sv for sv in moves if re.fullmatch(r"(the )?wash(es| lights)?", sv.raw.lower().strip())]
            if wash_like and len(wash_like) < len(moves):
                for sv in wash_like:
                    self._relabel(cmd, sv, "movement", "target", normalize_slot(self.rig, "target", sv.raw))
                cmd.ambiguities.append("read 'wash' as the wash fixtures (no other target was given)")
        for sv in list(cmd.slots.get("rate") or []):
            if norm_rate(sv.raw):
                continue
            for slot, fn, what in (("fade", norm_fade, "a fade"), ("size", norm_size, "a size"), ("priority", norm_priority, "a priority")):
                val = fn(sv.raw)
                if val:
                    self._relabel(cmd, sv, "rate", slot, val)
                    cmd.ambiguities.append(f"read '{sv.raw}' as {what}, not a speed")
                    break
        self.split_movement_targets(cmd)
        self.split_target_direction(cmd)
        self.priority_fallback(cmd)
        for sv in list(cmd.slots.get("color") or []):
            if not sv.value.get("unresolved"):
                continue
            words = sv.raw.split()
            for i in range(1, len(words)):
                head, tail = " ".join(words[:i]), " ".join(words[i:])
                c = self.rig.colors.canonical(tail)
                if not c:
                    continue
                sv.raw = tail
                sv.value = normalize_slot(self.rig, "color", tail)
                for sp in cmd.spans:
                    if sp.slot == "color" and sp.text.endswith(tail) and sp.text != tail:
                        sp.text = tail
                        sp.start += i
                for slot, fn in (("direction", norm_direction), ("size", norm_size), ("rate", norm_rate), ("fade", norm_fade)):
                    val = fn(head)
                    if val:
                        cmd.slots.setdefault(slot, []).append(SlotValue(raw=head, value=val))
                        cmd.ambiguities.append(f"split '{head} {tail}' into {slot} '{head}' + color '{tail}'")
                        break
                break

    def guard(self, cmd: LightCommand) -> None:
        """Unslotted, not-quite-certain commands with no lighting word at all are treated as out of scope."""
        from lightai.nlu.policy import OUT_OF_SCOPE

        if cmd.intent == "feedback" and re.search(OUT_OF_SCOPE, cmd.text, re.I) \
                and not any(cmd.slots.get(s) for s in ("color", "observed", "fixture_model")) \
                and not any(sv.value.get("fixture_ids") for sv in cmd.slots.get("target") or []):
            cmd.ambiguities.append(f"about the venue, not the last look; model said feedback ({cmd.confidence:.2f})")
            cmd.intent_top = [("none", 1.0)] + cmd.intent_top
            cmd.intent = "none"
            return
        if cmd.intent in ("none", "feedback") or cmd.spans or cmd.confidence >= 0.85:
            return
        words = {w.lower() for w in cmd.words}
        if not words & self.vocab():
            cmd.ambiguities.append(f"no lighting words; model said {cmd.intent} ({cmd.confidence:.2f})")
            cmd.intent_top = [("none", 1.0)] + cmd.intent_top
            cmd.intent = "none"

    def vocab(self) -> set:
        if getattr(self, "_vocab", None) is None:
            v = set(BASE_VOCAB)
            for alias in self.rig.zone_aliases:
                v.update(w for w in alias.split() if len(w) > 2)
            for fx in self.rig.fixtures.values():
                v.update(w.lower() for w in re.findall(r"[A-Za-z]{3,}", fx.name))
            for f in self.rig.functions.values():
                v.update(w.lower() for w in re.findall(r"[A-Za-z]{4,}", f.name))
            v.update(w for c in self.rig.colors.names() for w in c.split())
            self._vocab = v - STOPWORDS
        return self._vocab


def _norm_name(s: str) -> str:
    from lightai.nlu.normalize import digits_for_words
    from lightai.nlu.retriever import _norm

    return _norm(digits_for_words(s.lower()))


def merged_rate(cmd: LightCommand) -> dict:
    out: dict = {}
    for sv in cmd.slots.get("rate") or []:
        for k, v in sv.value.items():
            out.setdefault(k, v)
    return out


def primary_movement(cmd: LightCommand) -> Optional[dict]:
    moves = [sv.value for sv in cmd.slots.get("movement") or [] if sv.value.get("recipe")]
    if not moves:
        return None
    recipes = {m["recipe"] for m in moves}
    if "circle" in recipes and any(m["recipe"] == "circle_wave" for m in moves):
        return next(m for m in moves if m["recipe"] == "circle_wave")
    for r in MOVEMENT_SPECIFICITY:
        for m in moves:
            if m["recipe"] == r:
                return m
    return moves[0]


def target_ids(cmd: LightCommand) -> list:
    ids: list = []
    for sv in cmd.slots.get("target") or []:
        ids.extend(sv.value.get("fixture_ids", []))
    return list(dict.fromkeys(ids))


def projection(cmd: LightCommand) -> dict:
    """Canonical comparable view of a command (used by the golden suite)."""
    out: dict = {"intent": cmd.intent}
    if cmd.slots.get("target"):
        out["target"] = sorted(target_ids(cmd))
    if cmd.slots.get("color"):
        out["color"] = [sv.value.get("name", sv.raw.lower()) for sv in cmd.slots["color"]]
    m = primary_movement(cmd)
    if m:
        out["movement"] = m["recipe"]
    if cmd.slots.get("rate"):
        out["rate"] = merged_rate(cmd)
    for slot, key in (("intensity", "level"), ("fade", "ms"), ("size", "size"), ("priority", "priority"),
                      ("distance", "mm"), ("direction", "dir"), ("name", "name"), ("channel", "number"), ("value", "value")):
        if cmd.slots.get(slot):
            out[slot] = cmd.slots[slot][0].value.get(key)
    if cmd.slots.get("angle"):
        out["angle"] = {k: cmd.slots["angle"][0].value.get(k) for k in ("deg", "relative")}
    if cmd.slots.get("observed"):
        out["observed"] = cmd.slots["observed"][0].value
    if cmd.slots.get("fixture_model"):
        out["fixture_model"] = cmd.slots["fixture_model"][0].value.get("model")
    if cmd.slots.get("address"):
        out["address"] = {k: v for k, v in cmd.slots["address"][0].value.items() if k in ("universe", "address")}
    if cmd.slots.get("mode"):
        out["mode"] = cmd.slots["mode"][0].value.get("text")
    if cmd.slots.get("count"):
        out["count"] = cmd.slots["count"][0].value.get("count")
    if cmd.slots.get("function_ref"):
        out["function_ref"] = cmd.slots["function_ref"][0].value.get("name")
    out["clarify"] = bool(cmd.clarify)
    return out
