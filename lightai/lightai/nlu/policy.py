"""Abstain policy: ask a precise question instead of guessing."""

from __future__ import annotations

import re

from lightai.schema import LightCommand

MIN_CONFIDENCE = 0.80
AGREE_FLOOR = 0.45
# Unmistakable wording for a few simple, non-destructive intents. Between AGREE_FLOOR and MIN_CONFIDENCE the model's
# top intent is accepted when this wording agrees with it ("black it out" at 0.77, "gimme a blackout" at 0.62).
AGREE = {
    "blackout": r"\b(black ?(it )?out|blackout|lights? out|kill (all )?the lights|cut (all )?the lights|go dark|all dark|darkness)\b",
    "blackout_release": r"\b(lights? (back )?on|lights? back( up)?|bring (them|it|the lights|everything) back|un-?blackout|lift the (kill|blackout)|end the blackout)\b",
    "stop_all": r"\b(stop (everything|all)|clear (everything|all( the)? looks)|kill (everything|all (the )?(looks|functions|chasers|efx)))\b",
    "release": r"\b(release|overrides?|manual levels?|hand .{0,20} back|take over again)\b",
    "query.functions": r"\b(how many|which|what|list|search|show me|find)\b.{0,40}\b(functions?|efx|chasers?|scenes?|looks?|collections?|sequences?|shows?)\b",
    "query.status": r"\b(what'?s (running|on|playing|live)|what is (running|on|playing|live)|anything (running|on|playing)|status|is .{1,40} (running|on|playing|active))\b",
    "query.fixtures": r"\b(how many|which|what|list|describe)\b.{0,40}\b(fixtures?|lights?|patch(ed)?|rig)\b",
}
NEGATION = r"\b(don'?t|dont|do not|does not|doesn'?t|never|not|no|without|won'?t|wont|shouldn'?t|can'?t|cannot|nah|nope|hold off|skip|forget)\b"
LATER = (r"\b(later|tomorrow|tonight|after|afterwards|when|whenever|once|if|until|till|before|next|soon|eventually|maybe|gonna|going to|"
         r"will|about to|at (the )?(drop|break|breakdown|chorus|end|close)|on (the |my )?(drop|cue|count|signal|go|mark)|"
         r"in (a|an|\d+|one|two|three|four|five|ten|fifteen|twenty|thirty|a few|a couple of) (sec|secs|second|seconds|min|mins|minute|minutes|bit|moment)|"
         r"at \d{1,2}(:\d{2})?\s*(am|pm|o'?clock)?|at (midnight|noon))\b")  # lightai can't schedule: a qualified command is never done now
QUESTION = r"\?\s*$|^\s*(should|shall|can we|could we|would|did|do we|does|is|are|was|were|who|why|when|what)\b"
REPORTED = r"\b(went|was|were|came|did|had|has been|have been)\b"
# things that are not the rig: bar / DJ / venue talk
OUT_OF_SCOPE = (r"\b(dj|songs?|tracks?|music|playlist|album|record|bass|volume|mic|speakers?|drinks?|bottles?|bottle service|bar tab|bartenders?|"
                r"shots?|ice|tables?|guest ?list|tickets?|phone|battery|instagram|social media|capacity|sales|tip|air ?con|aircon|heater|"
                r"coffee|bouncer|security|headliner|crowd|vibe|hounds|storage|kitchen|office|menu|doors?|uber|wifi|chatter|talking|noise|"
                r"windows?|curtains|blinds)\b")
CONTRA = {  # wording that means the opposite, not now, or only part of the rig (the kill scene is all or nothing)
    "blackout": r"\b(stop|cancel|undo|end|lift|release|over|done|finished|prep|ready|arm|program|save|only|except|but keep|apart from)\b",
    "blackout_release": r"\b(prep|ready|arm|program|save|only|except|but keep|apart from)\b",
    "stop_all": r"\b(except|but keep|apart from|only)\b",
    "release": r"\b(except|but keep|apart from)\b",
}
HIGH_IMPACT = ("blackout", "blackout_release", "stop_all", "release")
LIVE_ONE = ("blackout", "blackout_release", "stop_all", "release", "set_level", "set_channel", "run_function", "stop_function")
GRAND_MASTER = r"\b(grand ?master|master( dimmer)?|gm)\b"
LIVE_CONFIRM_FIXTURES = 8

REQUIRED = {
    "create_look": ["target"],
    "propose_look": ["target"],
    "probe_fixture": ["target"],
    "run_function": ["function_ref"],
    "stop_function": ["function_ref"],
    "set_channel": ["target", "channel", "value"],
    "fixture_edit.rotate": ["target", "angle"],
    "fixture_edit.move": ["target", "direction"],
    "fixture_edit.rename": ["target", "name"],
    "set_bpm": ["rate"],
    "update_look": ["function_ref"],
    "delete_look": ["function_ref"],
    "add_widget": ["function_ref"],
    "add_fixture": ["fixture_model"],
    "set_level": ["intensity"],
}
QUESTIONS = {
    "target": "Which fixtures? (e.g. 'the washes', 'fixture 5', 'stage left')",
    "function_ref": "Which function? Say its name as it appears in QLC+.",
    "channel": "Which channel number?",
    "value": "What value (0-255 or a percentage)?",
    "angle": "By how many degrees?",
    "direction": "Which direction: left, right, up or down?",
    "name": "What should the new name be?",
    "rate": "What BPM?",
    "intensity": "What level (e.g. 50%, full, off)?",
    "fixture_model": "Which fixture model? (manufacturer and model, e.g. 'Chauvet Intimidator Spot 260')",
}
STRUCTURAL = {"create_look", "fixture_edit.rotate", "fixture_edit.move", "fixture_edit.rename",
              "update_look", "delete_look", "add_widget", "add_fixture"}


def qualifier(text: str, intent: str) -> str | None:
    """Why a high-impact live command should not run now (None = fine to run)."""
    text = re.sub(r"\b(after|from) (the |that |this )?(black ?out|kill( scene)?)\b", " ", text, flags=re.I)  # 'lights on after the blackout'
    for pat, why in ((NEGATION, "negated"), (LATER, "future or conditional"), (QUESTION, "a question"), (REPORTED, "reported, not asked"),
                     (CONTRA.get(intent, r"(?!)"), "contradicting words"), (OUT_OF_SCOPE, "not about the rig")):
        m = re.search(pat, text, re.I)
        if m:
            return f"{why}: '{m.group(0).strip()}'"
    return None


def apply_policy(cmd: LightCommand, defaults: dict | None = None) -> LightCommand:
    defaults = defaults or {}
    if cmd.intent == "none":
        cmd.clarify = "That doesn't sound like a lighting request."
        return cmd
    if cmd.intent in HIGH_IMPACT:  # at any confidence: a qualified, negated, reported or off-topic sentence never fires now
        why = qualifier(cmd.text, cmd.intent)
        part = [sv for sv in cmd.slots.get("target") or []
                if sv.value.get("via") != "zone all" and not re.search(r"\b(room|rig|everything|all|every|whole)\b", sv.raw, re.I)]
        if not why and cmd.intent in ("blackout", "blackout_release") and part:
            why = f"the blackout is the whole rig, not '{part[0].raw}'; for part of the rig say e.g. '{part[0].raw} off'"
        if not why and cmd.intent == "stop_all" and part:  # 'stop everything on the bathroom lights' would stop every look
            why = f"'stop everything' stops every look, not just '{part[0].raw}'; say which look to stop, or '{part[0].raw} off'"
        if why:
            cmd.clarify = f"I'm not sure you want '{cmd.intent.replace('_', ' ')}' right now ({why}). I only act immediately: say it when you want it."
            cmd.ambiguities.append(f"not acting on {cmd.intent}: {why}")
            return cmd
    if cmd.intent == "set_bpm":  # set_bpm runs on Enter and retimes running looks: 'do not set the tempo to 90' must not
        m = (re.search(NEGATION, cmd.text, re.I) or re.search(QUESTION, cmd.text, re.I)
             or re.search(r"\b(later|tomorrow|tonight|after|afterwards|when|once|if|until|next|soon)\b", cmd.text, re.I))
        if m:
            cmd.clarify = f"Change the tempo now? ('{m.group(0).strip()}') Say it plainly, e.g. 'bpm 128'."
            cmd.ambiguities.append(f"not acting on set_bpm: '{m.group(0).strip()}'")
            return cmd
    if AGREE_FLOOR <= cmd.confidence < MIN_CONFIDENCE and cmd.intent in AGREE and not re.search(NEGATION, cmd.text, re.I) \
            and re.search(AGREE[cmd.intent], cmd.text, re.I) and not re.search(LATER, cmd.text, re.I) \
            and not re.search(OUT_OF_SCOPE, cmd.text, re.I):
        cmd.ambiguities.append(f"accepted {cmd.intent} at {cmd.confidence:.2f}: the wording says so")
    elif cmd.confidence < MIN_CONFIDENCE:
        alts = " or ".join(f"'{i}'" for i, _ in cmd.intent_top[:2])
        cmd.clarify = f"I'm not sure what you want ({alts}). Can you rephrase?"
        cmd.ambiguities.append(f"low intent confidence {cmd.confidence:.2f}")
        return cmd
    for slot in REQUIRED.get(cmd.intent, []):
        vals = cmd.slots.get(slot) or []
        if not vals:
            if slot == "target" and cmd.intent == "create_look" and defaults.get("target"):
                continue
            if slot == "intensity" and cmd.intent == "set_level":
                pass
            cmd.clarify = QUESTIONS.get(slot, f"Missing {slot}.")
            return cmd
    must_read = {"value": "value", "angle": "deg", "direction": "dir", "channel": "number", "rate": None, "intensity": None}
    for slot in REQUIRED.get(cmd.intent, []):
        vals = cmd.slots.get(slot) or []
        if slot in must_read and vals:
            key = must_read[slot]
            if not any((sv.value.get(key) is not None) if key else sv.value for sv in vals):
                cmd.clarify = f"I couldn't read '{vals[0].raw}'. " + QUESTIONS.get(slot, f"Missing {slot}.")
                return cmd
    if cmd.intent in LIVE_ONE and re.search(r"\b(and then|then|after that|and also)\b", cmd.text, re.I):
        cmd.clarify = "One command at a time, please: say the first part, then the next."  # 'blackout and then washes to 50%'
        return cmd
    if cmd.intent == "set_level":
        m = re.search(r"\b(kill|blackout|black ?out)\b", cmd.text, re.I)
        if m and any((sv.value.get("level") or 0) > 0 for sv in cmd.slots.get("intensity") or []):
            cmd.clarify = f"Full or off? You also said '{m.group(0)}'. Say e.g. 'rig off' or 'rig at full'."  # 'full kill on the rig'
            return cmd
    if cmd.intent == "set_level" and not cmd.slots.get("target") and not re.search(GRAND_MASTER, cmd.text, re.I):
        cmd.clarify = "Which lights? Name a zone or fixture, or say 'grand master 40%'."  # 'battery at 5%' must never dim the room
        return cmd
    if cmd.intent in ("set_level", "create_look"):
        for sv in cmd.slots.get("intensity") or []:
            if "relative" in sv.value and "level" not in sv.value:
                cmd.clarify = f"'{sv.raw}' is a change, not a level. What level should it go to (e.g. 40%)?"
                return cmd
            if sv.value.get("out_of_range"):
                cmd.clarify = f"{sv.value['out_of_range']:g}% is more than full. Use 0-100%."
                return cmd
    for sv in cmd.slots.get("value") or []:
        if "out_of_range" in sv.value:
            cmd.clarify = f"DMX values go from 0 to 255 (you said {sv.value['out_of_range']:g})."
            return cmd
    if cmd.intent in ("create_look", "propose_look"):
        for sv in cmd.slots.get("movement") or []:
            if not sv.value.get("recipe") and re.search(r"[a-z]{3}", sv.raw.lower()):  # ignore emoji/punctuation
                cmd.clarify = f"I don't know the effect '{sv.raw}'. Try breathe, chase, strobe, circles, sweep, wave or ballyhoo."
                return cmd
    if cmd.intent == "feedback":  # a vague 'wrong fixture' should not turn feedback into a question
        kept = [sv for sv in cmd.slots.get("target") or [] if sv.value.get("fixture_ids")]
        if kept:
            cmd.slots["target"] = kept
        else:
            cmd.slots.pop("target", None)
    for sv in cmd.slots.get("target") or []:
        if sv.value.get("unresolved") or not sv.value.get("fixture_ids"):
            cmd.clarify = f"I don't know which fixture '{sv.raw}' is. Which one? (a fixture number, name or zone)"
            return cmd
    for sv in cmd.slots.get("color") or []:
        if sv.value.get("unresolved"):
            cmd.clarify = f"I don't know the color '{sv.raw}'. Add it to data/colors.yaml or pick another."
            return cmd
    for sv in cmd.slots.get("fixture_model") or []:
        if cmd.intent == "add_fixture" and sv.value.get("unresolved"):
            cmd.clarify = f"I can't find '{sv.raw}' in the QLC+ fixture library. Say the manufacturer and model."
            return cmd
        if cmd.intent == "add_fixture" and sv.value.get("ambiguous"):
            names = ", ".join(f"'{c['manufacturer']} {c['model']}'" for c in sv.value.get("candidates", [])[:3])
            cmd.clarify = f"Which fixture is '{sv.raw}'? {names}?"  # patching the wrong model is worse than asking
            return cmd
    for sv in cmd.slots.get("function_ref") or []:
        v = sv.value
        if v.get("unresolved"):
            cmd.clarify = f"I can't find a function called '{sv.raw}'."
            return cmd
        names = ", ".join(f"'{c['name']}'" for c in v.get("candidates", [])[:3])
        if v.get("same_name"):  # identical names: say which is which (type + ID); the planner asks when it matters
            same = [c for c in v.get("candidates", []) if c["name"].lower() == v["name"].lower()]
            names = ", ".join(f"{c['type']} {c['function_id']}" for c in same)
        risky = cmd.intent in ("update_look", "delete_look", "add_widget")
        if v.get("weak") and (risky or v.get("score", 0) < 0.3):
            cmd.clarify = f"I'm not sure which function '{sv.raw}' is. Did you mean {names}?"
            return cmd
        if v.get("weak") or v.get("ambiguous"):
            cmd.ambiguities.append(f"function_ref '{sv.raw}': {names}")
            cmd.needs_confirmation = True
    if cmd.intent == "fixture_edit.rename":
        tv = cmd.slots.get("target") or []
        if tv and len(tv[0].value.get("fixture_ids", [])) != 1:
            cmd.clarify = "Rename works on one fixture at a time. Which one?"
            return cmd
    if cmd.intent == "set_channel":
        tv = cmd.slots.get("target") or []
        if tv and len(tv[0].value.get("fixture_ids", [])) != 1:
            cmd.clarify = "Raw channel overrides work on one fixture at a time. Which one?"
            return cmd
    n_fx = len({i for sv in cmd.slots.get("target") or [] for i in sv.value.get("fixture_ids", [])})
    if cmd.intent in STRUCTURAL or (cmd.intent in ("set_level", "set_channel") and n_fx > LIVE_CONFIRM_FIXTURES):
        cmd.needs_confirmation = True
    return cmd
