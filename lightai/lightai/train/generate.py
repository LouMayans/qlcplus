"""Build the training set: hand-written seeds + grammar templates over the real rig vocabulary
+ accepted operator corrections (weighted). Output rows: {intent, marked, text, words, tags, source}.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Optional

import yaml

from lightai.config import LIGHTAI_DIR, PACKAGE_DIR, load_config
from lightai.nlu.text import parse_markup
from lightai.rig.model import Rig
from lightai.schema import labels

GRAMMAR = PACKAGE_DIR / "train" / "grammar.yaml"
SEED = LIGHTAI_DIR / "data" / "seed.txt"

MOVEMENT_WORDS = {
    "breathe": ["breathing", "breathe", "pulse", "pulsing", "breath", "slow pulse", "heartbeat", "swell"],
    "circle": ["circles", "circle", "spinning circles", "spin", "orbit", "rotating circles", "swirl"],
    "circle_wave": ["wave", "ripple", "wave of circles", "rolling wave", "phased circles"],
    "ballyhoo": ["ballyhoo", "search", "crazy sweep", "all over the place movement", "chaos"],
    "figure8": ["figure 8", "figure eight", "figure eights", "infinity loop"],
    "sweep": ["sweep", "sweeps", "sweeping", "scan", "side to side sweep", "swing"],
    "running_light": ["running light", "run", "chase light", "one by one", "bounce", "knight rider", "ping pong"],
    "color_chase": ["chase", "color chase", "colour chase", "alternating", "color cycle", "cycling"],
    "strobe": ["strobe", "strobing", "flash", "flicker", "lightning"],
    "color_wash": ["wash", "static", "solid", "fill", "flat wash"],
    "blackout_kill": ["blackout", "kill"],
}
RATES = [
    "slow", "slowly", "fast", "quick", "medium", "super fast", "very slow", "half time", "double time", "chill",
    "moderate", "rapid", "every 2 seconds", "every beat", "every bar", "every 4 seconds", "every half second",
    "60 bpm", "90 bpm", "120 bpm", "124 bpm", "126 bpm", "128 bpm", "130 bpm", "140 bpm", "174 bpm",
    "one twenty eight", "insane", "lazy", "steady", "super slow", "really fast",
]
BPMS = ["128", "124", "126", "130", "122", "140", "100", "95 bpm", "128 bpm", "one twenty eight", "one twenty four", "174", "86", "110 bpm", "120"]
INTENSITIES = ["50%", "30 percent", "full", "half", "dim", "low", "bright", "three quarters", "10%", "80%", "25%", "full power", "60 percent", "a quarter", "zero", "max", "40%", "70%", "90%", "15%"]
FADES = ["snap", "hard cut", "smooth fade", "2 second fade", "slow fade", "no fade", "quick fade", "5 second fade", "soft fade", "crossfade", "instant"]
SIZES = ["small", "tiny", "big", "wide", "huge", "tight", "large", "narrow", "massive"]
PRIORITIES = ["override", "vip", "p20", "p50", "priority 20", "high priority", "p100", "priority 50"]
ANGLES = ["90 degrees", "45 degrees", "180", "270 degrees", "a quarter turn", "90°", "30 degrees", "15 degrees", "180 degrees", "90", "half a turn", "60 degrees", "120 degrees", "10 degrees"]
DISTANCES = ["50 cm", "1 meter", "2 feet", "300 mm", "a bit", "half a meter", "20 centimeters", "1.5 m", "10 cm", "3 meters", "a little", "1 foot", "25 cm"]
DIRECTIONS = ["left", "right", "up", "down"]
ROT_DIRECTIONS = ["clockwise", "counterclockwise", "anticlockwise", "counter clockwise", "counter-clockwise", "anti clockwise",
                  "anti-clockwise"]
NAMES = [
    "Stage Left Spot", "DJ Booth Wall", "Roof Wash West", "Center Beam", "VIP Pinspot", "Bar Wash", "Front Truss 1",
    "Hazer", "Ladies Room RGB", "Left Column", "Dance Floor Strip", "Main Beam", "Back Wall", "Corner NE",
    "Truss Spot 3", "Booth Light", "Sky Beam", "Entrance Wash", "Stage Right Spot", "Floor Wash 2",
]
CHANNELS = [f"channel {i}" for i in range(1, 17)] + ["ch 5", "ch 8", "channel number 3", "ch 1", "ch 12"]
VALUES = ["0", "64", "128", "200", "255", "full", "50%", "99", "120", "10", "180", "88", "240"]
RATE_FB = ["too fast", "too slow", "slower", "faster", "slow it down", "speed it up", "a little faster", "way too fast", "too quick"]
INTENSITY_FB = ["too dim", "too bright", "brighter", "dimmer", "too dark", "not bright enough", "way too bright"]
SIZE_FB = ["bigger", "smaller", "too small", "too big", "wider", "tighter"]
OBSERVED_EXTRA = ["nothing", "dark", "the one next to it", "off", "white", "no light", "the wrong one"]
MODELS = ["v3", "v2", "beam230 v2", "beam230", "wash", "vpar", "base model", "beam230 v3", "revolver wash", "tetra bar"]
ONOFF = ["on", "off", "50%", "full", "half"]


def ai_look_names(rig: Rig, n: int, seed: int = 5) -> list:
    """Names in the style lightai gives its own looks, so they can be started/stopped by name."""
    from lightai.compiler.recipes import RECIPES
    from lightai.compiler.spec import LookParams
    from lightai.compiler.recipes import look_name

    rnd = random.Random(seed)
    colors = rig.colors.names()
    zones = [z.replace("_", " ") for z in rig.zones if not z.startswith("alias_")]
    out = set()
    while len(out) < n:
        recipe = rnd.choice(sorted(RECIPES))
        cls = RECIPES[recipe]
        p = LookParams(recipe=recipe, targets=[], target_label=rnd.choice(zones), colors=rnd.sample(colors, rnd.choice([1, 1, 2])),
                       bpm=rnd.choice([None, 60, 90, 120, 124, 126, 128, 130]), rate_word=rnd.choice([None, "slow", "fast", "medium"]),
                       priority=rnd.choice([0, 0, 0, 20, 50]))
        out.add(look_name(p, cls.label).lower())
    return sorted(out)


def library_models(rig: Rig, n: int, seed: int = 9) -> list:
    """Spoken-style names of fixtures from the QLC+ library (manufacturer + model, or model only)."""
    rnd = random.Random(seed)
    models = rig.library.all_models()
    out = set()
    for manufacturer, model in rnd.sample(models, min(n, len(models))):
        man = manufacturer.replace("_", " ")
        out.add(f"{man} {model}" if rnd.random() < 0.7 else model)
    return sorted(out)


def fillers(rig: Rig) -> dict:
    zones = []
    for name, z in (rig.overrides.get("zones") or {}).items():
        zones.extend([a for a in (z.get("aliases") or []) if a not in ("the beams", "the washes", "the right", "the left")])
    fixtures = []
    for fx in rig.fixtures.values():
        fixtures += [f"fixture {fx.id}", f"fixture #{fx.id}"]
        fixtures.append(fx.name.lower())
        m = re.search(r"#\s*(\d+)", fx.name)
        if m and fx.kind == "spot":
            n = m.group(1)
            fixtures += [f"spot #{n}", f"beam {n}", f"spot {n}", f"beam #{n}"]
        if m and fx.kind == "wash" and fx.name.lower().startswith("wash"):
            fixtures += [f"wash {m.group(1)}", f"wash #{m.group(1)}"]
    fixtures += [
        "third spot from the left", "first wash", "last spot", "second spot from the right", "middle spot",
        "left spots", "right spots", "spots on the left", "spots on the right", "spots and washes", "washes and pars",
        "fixtures 1 to 4", "fixture 3 and 5", "fixtures 8 through 11", "v3 beams", "fixture x", "the v2",
    ]
    targets = zones * 3 + fixtures
    colors = rig.colors.names() + list(rig.colors.aliases.keys())
    moves = [w for words in MOVEMENT_WORDS.values() for w in words]
    funcs = sorted({f.name.lower() for f in rig.functions.values() if f.name and "[" not in f.name and "]" not in f.name})
    funcs += ai_look_names(rig, 120)
    short = []
    for f in funcs:
        words = f.split()
        if len(words) > 3:
            short.append(" ".join(words[:3]))
    observed = colors + OBSERVED_EXTRA + [f"fixture {i}" for i in list(rig.fixtures)[:12]]
    fog = [a for a in (rig.overrides.get("zones", {}).get("fog", {}).get("aliases") or ["fog"])]
    return {
        "target": ("target", targets),
        "color": ("color", colors),
        "color2": ("color", colors),
        "movement": ("movement", moves),
        "rate": ("rate", RATES),
        "rate2": ("rate", [r for r in RATES if "bpm" in r or r[0].isdigit()] or RATES),
        "bpm": ("rate", BPMS),
        "intensity": ("intensity", INTENSITIES),
        "fade": ("fade", FADES),
        "size": ("size", SIZES),
        "priority": ("priority", PRIORITIES),
        "angle": ("angle", ANGLES),
        "distance": ("distance", DISTANCES),
        "direction": ("direction", DIRECTIONS + ["mirrored", "mirror image", "opposite directions"]),
        "direction_rot": ("direction", ROT_DIRECTIONS),
        "name": ("name", NAMES),
        "channel": ("channel", CHANNELS),
        "value": ("value", VALUES),
        "observed": ("observed", observed),
        "fixture_model": ("fixture_model", MODELS),
        "function_ref": ("function_ref", funcs + short),
        "rate_fb": ("rate", RATE_FB),
        "intensity_fb": ("intensity", INTENSITY_FB),
        "size_fb": ("size", SIZE_FB),
        "fog": ("target", fog),
        "function_ref_ai": ("function_ref", ai_look_names(rig, 200, seed=11)),
        "function_ref_any": ("function_ref", funcs + ai_look_names(rig, 60, seed=12)),
        "rate_upd": ("rate", ["faster", "slower", "quicker", "slow", "fast", "at 128 bpm", "at 90 bpm", "twice as fast", "half speed",
                              "a lot slower", "a bit faster", "at 124", "super slow", "medium speed"]),
        "size_upd": ("size", ["bigger", "smaller", "wider", "tighter", "huge", "small", "a lot bigger", "narrower"]),
        "intensity_upd": ("intensity", ["brighter", "dimmer", "at 50%", "at full", "at 30 percent", "half", "down to 40%", "up to full"]),
        "lib_model": ("fixture_model", library_models(rig, 180)),
        "address": ("address", ["universe 2 address 150", "dmx 289", "2.150", "address 300", "u1 address 289", "universe 1 dmx 400",
                                "universe 2 address 12", "dmx 70", "1.289", "address 450 on universe 2", "universe 2 dmx 501"]),
        "mode": ("mode", ["14 channel mode", "16 channel", "extended mode", "basic mode", "8ch mode", "standard mode", "11 channel", "3 channel mode"]),
        "washes": ("target", ["wash", "washes", "wash lights", "washers", "moving washes"]),
        "movement_nw": ("movement", [w for k, words in MOVEMENT_WORDS.items() if k not in ("color_wash", "blackout_kill") for w in words]),
        "direction_m": ("direction", ["mirrored", "mirror image", "mirror", "counter rotating", "opposite", "symmetrical"]),
        "fade1": ("fade", ["smooth", "soft", "snappy", "gentle", "slow fade", "hard cut", "soft fade", "crisp", "instant"]),
        "place": ("name", []),
        "info": ("name", []),
        "onoff": ("intensity", ONOFF),
        # iteration 4 (round-2 language agent)
        "movement_verb": ("movement", ["strobe", "flash", "pulse", "sweep", "chase", "circle", "bounce", "breathe", "flicker", "swirl", "ripple"]),
        "rate_verb": ("rate", ["speed up", "slow down", "speed up a bit", "slow down a little"]),
        "direction_rot2": ("direction", ["counter clockwise", "counter-clockwise", "anti clockwise", "anti-clockwise", "clockwise",
                                         "to the left", "to the right", "left", "right", "ccw", "cw"]),
        "target2": ("target", targets),
        # iteration 5: several fixtures at once, re-addressing
        "count": ("count", ["2", "3", "4", "5", "6", "8", "10", "12", "two", "three", "four", "five", "six", "a couple of", "a pair of", "3x"]),
        "mode2": ("mode", ["7address type", "7 address", "7 channel type", "7-ch", "14ch", "16 channel", "3 address", "4 channel type",
                           "6-channel", "8 dmx channel", "7 address type"]),
        "address_u": ("address", ["universe 1", "universe 2", "universe one", "universe two"]),
        "address_any": ("address", ["universe 1", "universe 2", "address 100", "250", "dmx 97", "universe 1 address 300",
                                    "universe 2 starting at 200", "2.150", "dmx 1", "address 450", "universe 1 from 320", "300"]),
    }


LITERALS = {
    "pct": ["10 percent", "35%", "half", "full", "50 percent", "90%", "2 percent", "a quarter"],
    "place": ["kitchen", "bar", "door", "bathroom", "coat check", "vip room", "patio", "terrace", "front door", "parking lot",
              "ice machine", "wifi", "card reader", "elevator", "dance floor bar", "box office", "restaurant", "rooftop"],
    "info": ["capacity", "wifi password", "cover charge", "dress code", "address", "phone number", "drink special", "guest count",
             "weather", "forecast", "schedule", "lineup", "set time", "door policy", "bar tab", "age limit"],
}

ALT_RE = re.compile(r"<([^<>]*)>")
SLOT_RE = re.compile(r"\{([a-z_0-9]+)\}")


def expand(template: str, fill: dict, rnd: random.Random) -> str:
    s = ALT_RE.sub(lambda m: rnd.choice(m.group(1).split("|")), template)

    def slot(m):
        key = m.group(1)
        if key in LITERALS:
            return rnd.choice(LITERALS[key])
        if key not in fill:
            raise KeyError(f"unknown placeholder {{{key}}} in grammar")
        name, values = fill[key]
        return f"[{name}:{rnd.choice(values)}]"

    s = SLOT_RE.sub(slot, s)
    return re.sub(r"\s+", " ", s).strip()


def read_seed(path: Path = SEED) -> list:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        intent, marked = [x.strip() for x in line.split("|", 1)]
        rows.append({"intent": intent, "marked": marked, "source": "seed"})
    return rows


def read_corrections(path: Path, labels_version: int) -> list:
    """Accepted corrections whose intent and slots exist in the current label set (older label
    versions are fine as long as they only use labels that still exist; additions are compatible)."""
    rows = []
    if not path.exists():
        return rows
    L = labels()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if int(r.get("labels_version") or 0) > labels_version or not r.get("accepted", True):
            continue
        c = r.get("corrected") or {}
        if not (c.get("intent") in L["intents"] and c.get("marked")):
            continue
        try:
            _, _, tags, _ = parse_markup(c["marked"])
        except ValueError:
            continue
        if any(t != "O" and t[2:] not in L["slots"] for t in tags):
            continue
        rows.append({"intent": c["intent"], "marked": c["marked"], "source": "correction"})
    return rows


def build_dataset(rig: Optional[Rig] = None, seed: int = 13, scale: float = 1.0, corrections_weight: int = 3) -> list:
    rig = rig or Rig.load()
    cfg = load_config()
    L = labels()
    rnd = random.Random(seed)
    fill = fillers(rig)
    grammar = yaml.safe_load(GRAMMAR.read_text(encoding="utf-8"))
    rows = []
    for intent, templates in grammar.items():
        if intent not in L["intents"]:
            raise ValueError(f"grammar intent {intent!r} is not in labels.yaml")
        for ti, t in enumerate(templates):
            n = max(1, int(round(t["n"] * scale)))
            for _ in range(n):
                rows.append({"intent": intent, "marked": expand(t["t"], fill, rnd), "source": f"grammar:{intent}:{ti}"})
    rows += read_seed()
    corr = read_corrections(cfg.data_dir / "corrections.jsonl", L["version"])
    rows += corr * corrections_weight
    out, seen = [], set()
    for r in rows:
        key = (r["intent"], r["marked"].lower())
        if key in seen and r["source"] != "correction":
            continue
        seen.add(key)
        text, words, tags, _ = parse_markup(r["marked"])
        if not words:
            continue
        for t in tags:
            if t != "O" and t[2:] not in L["slots"]:
                raise ValueError(f"unknown slot in {r['marked']!r}")
        out.append({**r, "text": text, "words": words, "tags": tags})
    return out


def split(rows: list, dev_frac: float = 0.1, seed: int = 7) -> tuple:
    rnd = random.Random(seed)
    idx = list(range(len(rows)))
    rnd.shuffle(idx)
    n_dev = int(len(rows) * dev_frac)
    dev_idx = set(idx[:n_dev])
    train = [r for i, r in enumerate(rows) if i not in dev_idx or r["source"] == "correction"]
    dev = [r for i, r in enumerate(rows) if i in dev_idx and r["source"] != "correction"]
    return train, dev
