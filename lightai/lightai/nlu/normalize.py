"""Deterministic slot normalizers: span text -> typed values, grounded in the rig."""

from __future__ import annotations

import re
from typing import Optional

from lightai.rig.model import Rig

UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
    "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
}
NUMWORDS = {w: v for w, v in UNITS.items() if w != "oh"} | TENS
_NUMWORD_RE = re.compile(r"\b(" + "|".join(sorted(NUMWORDS, key=len, reverse=True)) + r")\b")
_TENS_UNITS_RE = re.compile(r"\b(" + "|".join(TENS) + r")[\s-](" + "|".join(w for w, v in UNITS.items() if 0 < v < 10) + r")\b")


def digits_for_words(t: str) -> str:
    """'fixture five' -> 'fixture 5', 'wash twenty one' -> 'wash 21' (numbers below 100)."""
    t = _TENS_UNITS_RE.sub(lambda m: str(TENS[m.group(1)] + UNITS[m.group(2)]), t)
    return _NUMWORD_RE.sub(lambda m: str(NUMWORDS[m.group(1)]), t)


def _glue(t: str) -> str:
    """'2s' -> '2 s', '500ms' -> '500 ms', '10bpm' -> '10 bpm', 'half a second' -> '0.5 seconds'."""
    t = re.sub(r"(\d)(ms|s|sec|secs|bpm)\b", r"\1 \2", t)
    t = re.sub(r"\bhalf(?: a)? second\b", "0.5 seconds", t)
    return re.sub(r"\bevery other beat\b", "every 2 beats", t)


RATE_WORDS = [
    (r"\b(super|very|really|extra|ultra)\s+(slow|slowly|chill)\b|\bglacial\b|\bcrawl", "very_slow"),
    (r"\b(super|very|really|extra|ultra)\s+(fast|quick|quickly|rapid)\b|\binsane\b|\bcrazy fast\b|\bmax speed\b", "very_fast"),
    (r"\bhalf[\s-]?time\b|\bslow(ly|er)?\b|\bchill\b|\blazy\b|\bgentle\b|\brelaxed\b|\bmellow\b|\bdrift", "slow"),
    (r"\bdouble[\s-]?time\b|\bfast(er)?\b|\bquick(ly|er)?\b|\brapid(ly)?\b|\bspeedy\b|\bhyper\b|\bfrantic\b|\bpunchy\b|\bspeed (it )?up\b", "fast"),
    (r"\bmedium\b|\bmoderate\b|\bnormal\b|\bmid\b|\baverage\b|\bsteady\b", "medium"),
]
INTENSITY_WORDS = [
    (r"\b(full|max(imum)?|100|bright(est)?|blinding|all the way|full power|full blast)\b", 1.0),
    (r"\bthree quarters?\b", 0.75),
    (r"\bhalf\b", 0.5),
    (r"\bquarter\b", 0.25),
    (r"\b(dim|low|soft|subtle|faint|quiet|gentle|moody)\b", 0.3),
    (r"\b(barely|a little|a bit|tiny bit)\b", 0.15),
    (r"\b(off|zero|nothing|none|out|kill|cut)\b", 0.0),
    (r"\bon\b", 1.0),
]
SIZES = [
    (r"\b(tiny|minimal)\b", "tiny"),
    (r"\b(small|little|tight|narrow|close)\b", "small"),
    (r"\b(medium|normal|mid)\b", "medium"),
    (r"\b(huge|massive|giant|enormous|full room|whole room)\b", "huge"),
    (r"\b(big|large|wide|broad)\b", "big"),
]
PRIORITIES = [
    (r"\bp\s*(\d+)\b|\bpriority\s*(\d+)\b", None),
    (r"\b(kill|top priority|highest)\b", 100),
    (r"\bvip\b", 50),
    (r"\b(override|overrides|on top|high priority)\b", 20),
    (r"\b(base|background|low priority|default)\b", 0),
]
MOVEMENT_RECIPES = [
    (r"breath|pulse|puls|throb|heartbeat|swell", "breathe"),
    (r"ballyhoo|search|random sweep|crazy|all over|chaos", "ballyhoo"),
    (r"figure[\s-]*(8|eight)|infinity|eights?\b", "figure8"),
    (r"wave|ripple|phased|rolling", "circle_wave"),
    (r"circle|circular|spin|spinning|orbit|round|rotate|rotating|swirl", "circle"),
    (r"sweep|scan|pan across|side to side|left to right|back and forth|swing", "sweep"),
    (r"bounce|bouncing|knight rider|ping[\s-]?pong", "running_light"),
    (r"run|running|chase light|walk|marching|one by one|one at a time|sequence", "running_light"),
    (r"chase|chasing|cycle|cycling|alternat|switch|swap|flip between|rotate colou?rs", "color_chase"),
    (r"strob|flash|flicker|blink|lightning", "strobe"),
    (r"blackout|kill|dark", "blackout_kill"),
    (r"wash|static|solid|steady|still|flat|plain|fill|look", "color_wash"),
]
DIRECTIONS = [
    (r"\banti[\s-]?clockwise|counter[\s-]?clockwise|\bccw\b", "ccw"),
    (r"\bclockwise|\bcw\b", "cw"),
    (r"\bmirror|opposite|counter|symmetr", "mirror"),
    (r"\b(left|west)\b", "left"),
    (r"\b(right|east)\b", "right"),
    (r"\b(up|upstage|north|back|forward)\b", "up"),
    (r"\b(down|downstage|south|front|towards the crowd)\b", "down"),
]
DIST_UNITS = {
    "mm": 1.0, "millimeter": 1.0, "millimeters": 1.0, "millimetre": 1.0, "millimetres": 1.0,
    "cm": 10.0, "centimeter": 10.0, "centimeters": 10.0, "centimetre": 10.0, "centimetres": 10.0,
    "m": 1000.0, "meter": 1000.0, "meters": 1000.0, "metre": 1000.0, "metres": 1000.0,
    "ft": 304.8, "foot": 304.8, "feet": 304.8, "in": 25.4, "inch": 25.4, "inches": 25.4,
}


def parse_number(text: str) -> Optional[float]:
    t = text.lower().replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if m:
        return float(m.group(0))
    words = re.findall(r"[a-z]+", t)
    nums = [w for w in words if w in UNITS or w in TENS or w in ("hundred", "a", "and")]
    if not nums or all(w in ("a", "and") for w in nums):
        return None
    if len(nums) >= 2 and nums[0] in UNITS and (nums[1] in TENS or nums[1] == "oh" or UNITS.get(nums[1], 0) >= 10):
        rest = sum(UNITS.get(w, 0) + TENS.get(w, 0) for w in nums[1:])
        return float(UNITS[nums[0]] * 100 + rest)
    total, cur = 0, 0
    for w in nums:
        if w in UNITS:
            cur += UNITS[w]
        elif w in TENS:
            cur += TENS[w]
        elif w == "hundred":
            cur = max(cur, 1) * 100
        elif w == "a":
            cur += 1 if cur == 0 else 0
    total += cur
    return float(total) if total or "zero" in nums else None


def norm_rate(raw: str) -> dict:
    t = _glue(raw.lower())
    out: dict = {}
    if re.fullmatch(r"\s*(p\s*\d+|priority\s*(of\s*)?\d+)\s*", t):
        return out  # 'at p100' is a priority, not 100 BPM
    num = parse_number(t)
    if re.search(r"\bbpm\b|\bbeats per minute\b|\btempo\b", t) and num:
        out["bpm"] = num
    elif re.search(r"\b(sec|secs|second|seconds|s)\b", t) and num is not None:
        out["period_ms"] = int(round(num * 1000))
    elif re.search(r"\b(ms|millisecond|milliseconds)\b", t) and num is not None:
        out["period_ms"] = int(round(num))
    elif re.search(r"\bbeats?\b", t):
        out["beats"] = num or 1.0
    elif re.search(r"\bbars?\b", t):
        out["beats"] = (num or 1.0) * 4
    elif num is not None and 30 <= num <= 250:
        out["bpm"] = num
    for pat, word in RATE_WORDS:
        if re.search(pat, t):
            out["word"] = word
            break
    return out


def norm_intensity(raw: str) -> dict:
    t = raw.lower()
    if re.fullmatch(r"\s*(p\s*\d+|priority\s*(of\s*)?\d+)\s*", t):
        return {}  # 'at p20' is a priority, never 20 %
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent|per cent|pct)", t)
    if m:
        pct = float(m.group(1))
        return {"level": max(0.0, min(1.0, pct / 100.0))} | ({"out_of_range": pct} if pct > 100 else {})
    if re.search(r"\b(brighter|dimmer|darker|up|down|raise|lower|more|less|higher)\b", t) and not re.search(r"\d|\b(full|max|half|quarter|zero|off)\b", t):
        step = 0.1 if re.search(r"\b(little|bit|touch|tad|slightly|notch)\b", t) else 0.2
        up = re.search(r"\b(brighter|up|raise|more|higher)\b", t)
        return {"relative": step if up else -step}
    num = parse_number(t)
    if num is not None and re.search(r"percent|%", t):
        return {"level": max(0.0, min(1.0, num / 100.0))}
    for pat, lvl in INTENSITY_WORDS:
        if re.search(pat, t):
            return {"level": lvl}
    if num is not None:
        if num <= 1.0:
            return {"level": num}
        if num <= 100:
            return {"level": num / 100.0}
        return {"level": min(1.0, num / 255.0)}
    return {}


def norm_fade(raw: str) -> dict:
    t = _glue(raw.lower())
    num = parse_number(t)
    if re.search(r"\b(snap|snappy|hard|cut|instant|instantly|no fade|straight|sharp|bump)\b", t):
        return {"ms": 0, "snap": True}
    if num is not None and re.search(r"\b(ms|millisecond|milliseconds)\b", t):
        return {"ms": int(num), "snap": False}
    if num is not None and re.search(r"\b(sec|secs|second|seconds|s)\b", t):
        return {"ms": int(round(num * 1000)), "snap": False}
    if re.search(r"\bslow\b|\blong\b|\blazy\b", t):
        return {"ms": 3000, "snap": False}
    if re.search(r"\bquick\b|\bfast\b|\bshort\b", t):
        return {"ms": 300, "snap": False}
    if re.search(r"\b(smooth(ly)?|soft(ly)?|gentle|gently|fades?|faded|fading|blend|crossfade|melt|ease)\b", t):
        return {"ms": 1000, "snap": False}
    return {}


SIZE_REL = [(r"\b(bigger|larger|wider|broader)\b", 1), (r"\b(smaller|tighter|narrower)\b", -1)]


def norm_size(raw: str) -> dict:
    t = raw.lower()
    for pat, step in SIZE_REL:  # 'bigger' is a change, not a size (update_look shifts the look's own size)
        if re.search(pat, t):
            return {"relative": step * (2 if re.search(r"\b(a lot|much|way)\b", t) else 1)}
    for pat, size in SIZES:
        if re.search(pat, t):
            return {"size": size}
    return {}


def norm_priority(raw: str) -> dict:
    t = raw.lower()
    m = re.search(r"\bp\s*(\d+)\b|\bpriority\s*(?:of\s*)?(\d+)\b", t)
    if m:
        return {"priority": int(m.group(1) or m.group(2))}
    for pat, pr in PRIORITIES[1:]:
        if re.search(pat, t):
            return {"priority": pr}
    return {}


def norm_angle(raw: str, context_before: str = "") -> dict:
    t = raw.lower()
    if re.search(r"three[\s-]quarters?", t):
        deg = 270.0
    elif re.search(r"\bhalf(?: a)?[\s-]turn\b|upside down|\bflip", t):
        deg = 180.0
    elif re.search(r"\bquarter\b", t) and not re.search(r"\d", t):
        deg = 90.0
    else:
        deg = parse_number(t)
    if deg is None:
        return {}
    if re.search(r"\b(minus|negative)\b", t):
        deg = -abs(deg)
    if re.search(r"anti[\s-]?clockwise|counter[\s-]?clockwise|\bccw\b|\bleft\b", t):
        deg = -deg
    relative = not re.search(r"\b(to|at|be|is)\s*$", context_before.lower())
    return {"deg": int(round(deg)), "relative": relative}


def norm_distance(raw: str) -> dict:
    t = raw.lower()
    t = re.sub(r"\bhalf an? (meter|metre|foot)\b", r"0.5 \1", t)
    t = re.sub(r"\b(a|an|one) (meter|metre|foot|inch|centimeter|centimetre)\b", r"1 \2", t)
    t = re.sub(r"\ba couple (of )?", "2 ", t)
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([a-z]+)?", t)
    num = float(m.group(1)) if m else parse_number(t)
    unit = m.group(2) if m and m.group(2) in DIST_UNITS else None
    if unit is None:
        for u in sorted(DIST_UNITS, key=len, reverse=True):
            if re.search(rf"\b{u}\b", t):
                unit = u
                break
    if num is None:
        if re.search(r"\b(a bit|a little|slightly|nudge|tad)\b", t):
            return {"mm": 250, "assumed": "a bit = 25 cm"}
        return {}
    if unit is None:
        return {"mm": int(round(num * 10)), "assumed": "no unit given; read as centimeters"}
    return {"mm": int(round(num * DIST_UNITS[unit]))}


def norm_direction(raw: str) -> dict:
    t = raw.lower()
    for pat, d in DIRECTIONS:
        if re.search(pat, t):
            return {"dir": d}
    return {}


def norm_channel(raw: str, zero_based: bool = False) -> dict:
    num = parse_number(raw)
    if num is None:
        return {}
    n = int(num)
    return {"number": n, "index": n if zero_based else n - 1, "numbering": "0-based" if zero_based else "1-based"}


def norm_value(raw: str) -> dict:
    t = raw.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent)", t)
    if m:
        return {"value": int(round(min(100.0, float(m.group(1))) * 255 / 100))}
    if re.search(r"\b(full|max)\b", t):
        return {"value": 255}
    if re.search(r"\b(zero|off|min)\b", t):
        return {"value": 0}
    if re.search(r"\bhalf\b", t):
        return {"value": 128}
    num = parse_number(t)
    if num is None:
        return {}
    if re.search(r"percent", t):
        return {"value": int(round(min(100.0, num) * 255 / 100))}
    if not 0 <= num <= 255:
        return {"value": max(0, min(255, int(num))), "out_of_range": num}
    return {"value": int(num)}


def norm_address(raw: str) -> dict:
    """'universe 2 address 150', 'dmx 150', '2.150', '2/150' -> 0-based universe/address."""
    t = digits_for_words(raw.lower())  # 'universe two' is universe 2, not address 2
    m = re.search(r"\b(\d+)\s*[./:]\s*(\d+)\b", t)
    if m:
        return {"universe": int(m.group(1)) - 1, "address": int(m.group(2)) - 1}
    out: dict = {}
    u = re.search(r"\buniverse\s*(\d+)|\bu\s*(\d+)\b", t)
    if u:
        out["universe"] = int(u.group(1) or u.group(2)) - 1
    a = re.search(r"\b(?:address|addr|dmx|channel|start(?:ing)? at|at)\s*#?\s*(\d+)", t)
    if a:
        out["address"] = int(a.group(1)) - 1
    elif not u:
        n = parse_number(t)
        if n is not None:
            out["address"] = int(n) - 1
    return out


def norm_mode(raw: str) -> dict:
    num = parse_number(raw)
    return {"text": raw.strip(), "channels": int(num) if num is not None else None}


def resolve_library_model(library, raw: str) -> dict:
    """Fuzzy-match a spoken fixture model against the whole QLC+ fixture library."""
    t = re.sub(r"[^a-z0-9 ]", " ", raw.lower())
    words = [w for w in t.split() if w not in ("a", "an", "the", "new", "fixture", "light", "one")]
    if not words:
        return {"unresolved": raw}
    nums = [w for w in words if w.isdigit()]
    best = []
    for manufacturer, model in library.all_models():
        hay = re.sub(r"[^a-z0-9 ]", " ", f"{manufacturer} {model}")
        tokens = set(hay.split())
        joined = hay.replace(" ", "")
        if nums and not all(n in tokens or n in joined for n in nums):
            continue
        hits = sum(1 for w in words if w in tokens or (len(w) > 3 and w in joined))
        if hits == 0:
            continue
        score = hits / len(words) + (0.2 if model in " ".join(words) else 0.0)
        best.append((score, manufacturer, model))
    best.sort(key=lambda x: (-x[0], len(x[2])))
    if not best:
        return {"unresolved": raw}
    cands = [{"manufacturer": b[1], "model": b[2], "score": round(b[0], 3)} for b in best[:5]]
    top = best[0]
    out = {"manufacturer": top[1], "model": top[2], "score": round(top[0], 3), "candidates": cands}
    if top[0] < 0.6 or (len(best) > 1 and best[1][0] >= top[0] - 1e-9):
        out["ambiguous"] = True
    return out


def norm_name(raw: str) -> dict:
    return {"name": raw.strip().strip("\"'").strip()}


def norm_movement(raw: str) -> dict:
    t = raw.lower()
    for pat, recipe in MOVEMENT_RECIPES:
        if re.search(pat, t):
            out = {"recipe": recipe, "word": raw}
            if recipe == "running_light" and re.search(r"bounce|knight rider|ping[\s-]?pong", t):
                out["mirror"] = True
            return out
    return {"word": raw}


class TargetResolver:
    """Fixture/zone phrases -> fixture IDs, using the rig's names, zones and stage order."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.number_means = (rig.overrides.get("fixture_number_means") or "id").lower()

    def by_name_number(self, n: int, kind: Optional[str] = None) -> list:
        out = []
        for fx in self.rig.fixtures.values():
            m = re.search(r"#\s*(\d+)\s*$", fx.name)
            if m and int(m.group(1)) == n and (kind is None or fx.kind == kind):
                out.append(fx.id)
        return out

    def resolve(self, raw: str) -> dict:
        t = re.sub(r"\s+", " ", raw.lower().strip())
        t = re.sub(r"(\u2019|')s?$|[.?!,\u00b0]+$", "", t).strip()
        if t not in self.rig.zone_aliases and f"the {t}" in self.rig.zone_aliases:
            t = f"the {t}"  # 'rig', 'room', 'left', 'right' are zones, not fragments of a fixture name
        t = re.sub(r"^(the|a|an|all the|all of the)\s+", "", t) if t not in self.rig.zone_aliases else t
        t = digits_for_words(t) if t not in self.rig.zone_aliases else t
        t = re.sub(r"^(?:(left|right)[\s-]?most|far[\s-](left|right))\s+(.+)$", lambda m: f"first {m.group(3)} from the {m.group(1) or m.group(2)}", t)
        ids: list = []
        via = None

        m = re.fullmatch(r"(fixtures?|ids?|fixture numbers?|number|#)\s*#?\s*(\d+)((?:\s*(?:,|and|&)\s*#?\s*\d+)*)", t)
        rng = re.fullmatch(r"(fixtures?|ids?)\s*#?\s*(\d+)\s*(?:to|through|thru|-)\s*#?\s*(\d+)", t)
        if rng:
            a, b = int(rng.group(2)), int(rng.group(3))
            nums = list(range(min(a, b), max(a, b) + 1))
            ids, via = self._numbers(nums)
        elif m:
            nums = [int(m.group(2))] + [int(x) for x in re.findall(r"\d+", m.group(3) or "")]
            ids, via = self._numbers(nums)
        if not ids:
            km = re.fullmatch(r"(spot|spotlight|beam|beam230|moving head|head|wash|washer|par|vpar|tetra|bar)s?\s*(?:number\s*)?#?\s*(\d+)"
                              r"((?:\s*(?:,|and|&|to|through|thru|-)\s*(?:(?:spot|beam|wash|par|tetra|bar)s?\s*)?#?\s*\d+)*)", t)
            if km:
                kind = {"spot": "spot", "spotlight": "spot", "beam": "spot", "beam230": "spot", "moving head": "spot", "head": "spot",
                        "wash": "wash", "washer": "wash", "par": "par", "vpar": "par", "tetra": "bar", "bar": "bar"}[km.group(1)]
                first, rest = int(km.group(2)), km.group(3) or ""
                if re.search(r"\b(to|through|thru)\b|-", rest):
                    last = int(re.findall(r"\d+", rest)[-1])
                    nums = list(range(min(first, last), max(first, last) + 1))
                else:
                    nums = [first] + [int(x) for x in re.findall(r"\d+", rest)]
                zone = {"spot": "spots", "wash": "washes", "par": "pars", "bar": "tetras"}.get(kind)
                members = self.rig.ordered(self.rig.zones.get(zone, []))
                found, vias = [], []
                for n in nums:
                    got = self.by_name_number(n, kind)  # never cross kinds: 'par 3' must not match 'BEAM230 #3'
                    if not got and 1 <= n <= len(members):
                        got = [members[n - 1]]
                        vias.append(f"position {n} in {zone} (stage order)")
                    elif got:
                        vias.append(f"name number #{n}")
                    if not got:
                        found = []
                        break
                    found += got
                if found:
                    ids, via = list(dict.fromkeys(found)), ", ".join(vias)
        if not ids:
            om = re.fullmatch(r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|last|middle|\d+\s*(?:st|nd|rd|th))\s+(.+?)(?:\s+(?:from|on|at)\s+(?:the\s+)?(left|right)(?:\s+side)?)?", t)
            if om:
                zone = self.rig.zone_of_phrase(om.group(2))
                if zone:
                    members = self.rig.ordered(self.rig.zones[zone])
                    if om.group(3) == "right":
                        members = list(reversed(members))
                    word = om.group(1)
                    if word == "last":
                        idx = len(members) - 1
                    elif word == "middle":
                        idx = len(members) // 2
                    elif word in ORDINALS:
                        idx = ORDINALS[word] - 1
                    else:
                        idx = int(re.match(r"\d+", word).group(0)) - 1
                    if 0 <= idx < len(members):
                        ids, via = [members[idx]], f"{word} of {zone} by stage order"
        if not ids:
            parts = re.split(r"\s*(?:,|\band\b|&|\bplus\b)\s*", t)
            acc: list = []
            vias = []
            for part in [p for p in parts if p]:
                got, v = self._single(part)
                if not got and part != t:
                    sub = self.resolve(part)  # 'fixture 3 and the washes', 'wash 1 and wash 3'
                    got, v = sub.get("fixture_ids", []), sub.get("via")
                if not got:
                    acc = []
                    break
                acc.extend(got)
                vias.append(v)
            if acc:
                ids, via = list(dict.fromkeys(acc)), " + ".join(vias)
        if not ids:
            return {"fixture_ids": [], "unresolved": raw}
        ids = [i for i in ids if i in self.rig.fixtures]
        return {"fixture_ids": ids, "via": via, "label": self.label(raw)}

    def _numbers(self, nums: list) -> tuple:
        if self.number_means == "name_number":
            ids = [i for n in nums for i in self.by_name_number(n)]
            return ids, "name number"
        ids = [n for n in nums if n in self.rig.fixtures]
        return ids, "fixture ID"

    def _single(self, part: str) -> tuple:
        p = re.sub(r"^(the|all the|all)\s+", "", part.strip())
        zone = self.rig.zone_of_phrase(part) or self.rig.zone_of_phrase(p)
        side = None
        p = re.sub(r"\s+(fixtures|units|lights)$", "", p) if self.rig.zone_of_phrase(re.sub(r"\s+(fixtures|units|lights)$", "", p)) else p
        zone = zone or self.rig.zone_of_phrase(p)
        sm = re.fullmatch(r"(?:stage\s+)?(left|right)(?: side| half)?\s+(.+)|(.+?)\s+on (?:the )?(?:stage )?(left|right)(?: side)?", p)
        if sm:
            side = sm.group(1) or sm.group(4)
            rest = sm.group(2) or sm.group(3)
            z = self.rig.zone_of_phrase(rest)
            if z:
                half = set(self.rig.zones.get(f"stage_{side}", []))
                return [i for i in self.rig.zones[z] if i in half], f"{side} half of {z}"
        if zone:
            return list(self.rig.zones[zone]), f"zone {zone}"
        for name, fid in self.rig.fixture_aliases.items():
            if p == name or p == name.replace("#", "").replace("  ", " ").strip():
                return [fid], "fixture name"
        cands = [fx for fx in self.rig.fixtures.values()
                 if p and not p.isdigit() and re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", fx.name.lower())]
        if len(cands) == 1:
            return [cands[0].id], "fixture name (partial)"
        return [], None

    def label(self, raw: str) -> str:
        t = re.sub(r"^((and|or|plus|also|on|to|for|at|with|the|a|an|all)\s+)+", "", raw.strip(" ,&"), flags=re.I)
        return t or raw.strip()


def resolve_model(rig: Rig, raw: str) -> dict:
    t = re.sub(r"[^a-z0-9]", "", raw.lower())
    keys = sorted({fx.key for fx in rig.fixtures.values()})
    if not t:
        return {}
    exact = [k for k in keys if re.sub(r"[^a-z0-9]", "", k.lower().split("/", 1)[1]) == t]
    if exact:
        return {"model": exact[0]}
    if t in ("v3", "beamv3", "beam230v3"):
        t = "beam230v3"
    elif t in ("v2", "beamv2", "beam230v2"):
        t = "beam230v2"
    elif t in ("v1", "base", "original", "beamv1", "beam230v1", "beam"):
        t = "beam230"
    hits = [k for k in keys if re.sub(r"[^a-z0-9]", "", k.lower().split("/", 1)[1]) == t]
    if not hits:
        hits = [k for k in keys if t in re.sub(r"[^a-z0-9]", "", k.lower())]
    return {"model": hits[0], "candidates": hits} if hits else {"unresolved": raw}


def norm_observed(rig: Rig, raw: str) -> dict:
    c = rig.colors.canonical(raw)
    if c:
        return {"color": c}
    t = raw.lower()
    if re.search(r"\b(nothing|none|dark|no light|off|black)\b", t):
        return {"nothing": True}
    if re.search(r"next to|beside|neighbou?r|left of|right of", t):
        return {"relative": raw}
    res = TargetResolver(rig).resolve(raw)
    if res.get("fixture_ids"):
        return {"fixture_ids": res["fixture_ids"]}
    return {"text": raw}


def normalize_slot(rig: Rig, slot: str, raw: str, context_before: str = "", intent: str = "") -> dict:
    if slot == "target":
        return TargetResolver(rig).resolve(raw)
    if slot == "color":
        c = rig.colors.canonical(raw)
        if not c:
            return {"unresolved": raw}
        rgb, src = rig.colors.rgb(c)
        return {"name": c, "rgb": list(rgb), "source": src}
    if slot == "movement":
        out = norm_movement(raw)
        if out.get("recipe") and re.search(r"\bmirror", raw.lower()):
            out["mirror"] = True  # 'mirrored circles' in one span
        return out
    if slot == "rate":
        out = norm_rate(raw)
        if intent == "set_bpm" and "bpm" not in out:
            n = parse_number(raw)
            if n:
                out["bpm"] = n
        return out
    if slot == "intensity":
        return norm_intensity(raw)
    if slot == "fade":
        return norm_fade(raw)
    if slot == "size":
        return norm_size(raw)
    if slot == "priority":
        return norm_priority(raw)
    if slot == "angle":
        return norm_angle(raw, context_before)
    if slot == "distance":
        return norm_distance(raw)
    if slot == "direction":
        return norm_direction(raw)
    if slot == "name":
        return norm_name(raw)
    if slot == "channel":
        return norm_channel(raw, (rig.overrides.get("channel_numbering") or "one_based") == "zero_based")
    if slot == "value":
        return norm_value(raw)
    if slot == "observed":
        return norm_observed(rig, raw)
    if slot == "fixture_model":
        if intent == "add_fixture":
            return resolve_library_model(rig.library, raw)
        return resolve_model(rig, raw)
    if slot == "address":
        return norm_address(raw)
    if slot == "mode":
        return norm_mode(raw)
    return {"text": raw}
