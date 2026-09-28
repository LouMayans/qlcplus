"""Parametric look recipes. Each turns LookParams + the rig into QLC+ FunctionSpecs.

Nothing here knows channel numbers: every value goes through a fixture role
(dimmer, shutter, color_wheel, red, ...) derived by the rig model.
"""

from __future__ import annotations

import math
from typing import Optional

from lightai.compiler.spec import EfxFixtureSpec, FunctionSpec, Look, LookParams, StepSpec
from lightai.rig.model import Rig, RigFixture

DEFAULT_BPM = 124.0
RATE_BEATS = {
    "breathe": {"very_slow": 4, "slow": 2, "medium": 1, "fast": 0.5, "very_fast": 0.25},
    "chase": {"very_slow": 4, "slow": 2, "medium": 1, "fast": 0.5, "very_fast": 0.25},
    "run": {"very_slow": 2, "slow": 1, "medium": 0.5, "fast": 0.25, "very_fast": 0.125},
    "efx": {"very_slow": 16, "slow": 8, "medium": 4, "fast": 2, "very_fast": 1},
    "ballyhoo": {"very_slow": 32, "slow": 16, "medium": 8, "fast": 4, "very_fast": 2},
}
STROBE_SPEED = {"very_slow": 0.05, "slow": 0.2, "medium": 0.55, "fast": 0.85, "very_fast": 1.0}
EFX_SIZE = {"tiny": 20, "small": 35, "medium": 60, "big": 85, "huge": 110}
CATEGORY_BY_ZONE = [
    ("spots", "Spots"),
    ("washes", "Washes"),
    ("pars", "Pars"),
    ("tetras", "Tetras"),
    ("led_walls", "LED Walls"),
    ("white_panels", "Panels"),
    ("swarms", "FX"),
]


def noncontiguous_fine(fx: RigFixture) -> bool:
    for r in ("pan", "tilt"):
        if r + "_fine" in fx.roles and r in fx.roles and fx.roles[r + "_fine"] != fx.roles[r] + 1:
            return True
    return False


class IdAlloc:
    def __init__(self, start: int, reserved: Optional[set] = None) -> None:
        self.cur = start
        self.reserved = reserved or set()

    def next(self) -> int:
        while self.cur in self.reserved:
            self.cur += 1
        v = self.cur
        self.cur += 1
        return v


def unit_ms(p: LookParams, family: str) -> int:
    """Length of the recipe's time unit (fade, step or cycle) in ms."""
    word = p.rate_word or "medium"
    beats = RATE_BEATS[family].get(word, RATE_BEATS[family]["medium"])
    if p.period_ms:
        return max(20, int(p.period_ms))
    bpm = p.bpm or DEFAULT_BPM
    return max(20, int(round(beats * 60000.0 / bpm)))


def rate_label(p: LookParams) -> str:
    if p.period_ms:
        s = p.period_ms / 1000.0
        return f"{s:g}s"
    if p.bpm:
        return f"{p.bpm:g}bpm"
    return (p.rate_word or "").replace("_", " ")


def category(rig: Rig, p: LookParams) -> str:
    if p.category:
        return p.category
    targets = set(p.targets)
    for zone, label in CATEGORY_BY_ZONE:
        ids = set(rig.zones.get(zone) or [])
        if ids and targets <= ids:
            return label
    return "Whole Rig"


def look_name(p: LookParams, label: str) -> str:
    if p.name:
        return p.name
    parts = []
    if p.colors:
        parts.append("/".join(c.title() for c in p.colors))
    if p.target_label:
        parts.append(p.target_label.title())
    parts.append(label)
    if p.recipe in ("color_wash", "blackout_kill"):
        rl = ""
    elif p.recipe == "strobe":  # a strobe runs at a speed word; fixture strobe channels have no tempo sync
        rl = (p.rate_word or "").replace("_", " ")
    else:
        rl = rate_label(p)
    if rl:
        parts.append(rl)
    if p.intensity < 0.999 and p.recipe != "blackout_kill":  # distinct looks need distinct names
        parts.append(f"{int(round(p.intensity * 100))}%")
    if p.fade_ms:
        parts.append(f"Fade {p.fade_ms / 1000:g}s")
    if p.mirror:
        parts.append("Mirrored")
    if p.priority:
        parts.append(f"P{p.priority}")
    return " ".join(parts)


class Values:
    """Accumulates channel values per fixture plus the role each value was meant for."""

    def __init__(self) -> None:
        self.values: dict = {}
        self.roles: dict = {}

    def set(self, fx: RigFixture, role: str, value: Optional[int]) -> None:
        if value is None or role not in fx.roles:
            return
        ch = fx.roles[role]
        self.values.setdefault(fx.id, {})[ch] = max(0, min(255, int(value)))
        self.roles.setdefault(fx.id, {})[ch] = role

    def spec_values(self) -> dict:
        return {fid: sorted(chs.items()) for fid, chs in self.values.items()}

    def empty(self) -> bool:
        return not any(self.values.values())


class Recipe:
    key = ""
    label = ""
    family = "chase"
    needs_color = True
    needs_movement = False

    def __init__(self, rig: Rig, p: LookParams, ids: IdAlloc) -> None:
        self.rig = rig
        self.p = p
        self.ids = ids
        self.assumptions: list = []
        self.skipped: list = []
        self.facts: set = set()
        self.name = look_name(p, self.label)
        self.path = f"AI/{category(rig, p)}"

    def fixtures(self) -> list:
        out = []
        for fid in self.rig.ordered(self.p.targets):
            fx = self.rig.fixtures.get(fid)
            if fx is None:
                self.skipped.append({"fixture": fid, "reason": "not in the show file"})
                continue
            if self.needs_movement and not fx.can_move:
                self.skipped.append({"fixture": fid, "name": fx.name, "reason": "has no pan/tilt"})
                continue
            if self.needs_color and fx.color_mode == "none" and not fx.has("dimmer"):
                self.skipped.append({"fixture": fid, "name": fx.name, "reason": "no color or intensity channels"})
                continue
            if fx.kind in ("fx", "fog") and len(self.p.targets) > 1 and not self.explicit(fid):
                self.skipped.append({"fixture": fid, "name": fx.name, "reason": f"{fx.kind} fixture only used when named explicitly"})
                continue
            out.append(fx)
        if not out:
            raise ValueError(f"{self.key}: none of the target fixtures can do this look")
        return out

    def explicit(self, fid: int) -> bool:
        return len(self.p.targets) == 1 and self.p.targets[0] == fid

    def spec(self, type_: str, suffix: str = "", **kw) -> FunctionSpec:
        name = self.name if not suffix else f"{self.name} - {suffix}"
        return FunctionSpec(id=self.ids.next(), type=type_, name=name, path=self.path, **kw)

    def color_for(self, index: int, fx: Optional[RigFixture] = None) -> str:
        if fx is not None and (self.p.color_map or {}).get(str(fx.id)):
            return self.p.color_map[str(fx.id)]
        colors = self.p.colors or ["white"]
        return colors[index % len(colors)]

    def put_color(self, v: Values, fx: RigFixture, color: str, level: float) -> None:
        if fx.color_mode == "wheel":
            w = self.rig.wheel_value(fx, color)
            if w["value"] is None:
                reason = (f"'{color}' is unknown on its color wheel (you ruled out the listed slot); say 'find {color} on fixture {fx.id}'"
                          if w.get("unknown") else "color wheel has no usable slots")
                self.skipped.append({"fixture": fx.id, "name": fx.name, "reason": reason})
                return
            if not w["exact"]:
                why = (f"you ruled out its listed '{color}' slot" if w.get("ruled_out") else f"{fx.key} has no '{color}' on its color wheel")
                self.assumptions.append({"fact": f"{why}; using nearest slot '{w['slot']}' ({w['value']})", "source": "qxf color wheel + your feedback"})
            v.set(fx, "color_wheel", w["value"])
            self.facts.add(f"wheel:{fx.key}:{w['slot']}")
        elif fx.color_mode == "rgb":
            for role, val in self.rig.emitter_values(fx, color, level).items():
                v.set(fx, role, val)
            self.facts.add(f"rgb:{fx.key}")

    def put_intensity(self, v: Values, fx: RigFixture, level: float) -> None:
        v.set(fx, "dimmer", self.rig.dimmer_value(fx, level))

    def put_open(self, v: Values, fx: RigFixture) -> None:
        sh = fx.caps.get("shutter")
        if sh:
            v.set(fx, "shutter", sh.get("open"))
            if str(sh.get("open_source", "")).startswith("assumed"):
                self.assumptions.append({"fact": f"{fx.key} shutter open = {sh.get('open')}", "source": sh.get("open_source")})
            self.facts.add(f"shutter_open:{fx.key}")
        if "macro_off" in fx.caps:
            v.set(fx, "color_macro", fx.caps["macro_off"])
        if "mode_value" in fx.caps:
            v.set(fx, "mode", fx.caps["mode_value"])

    def put_fast_motors(self, v: Values, fx: RigFixture) -> None:
        if "pt_speed_fast" in fx.caps:
            v.set(fx, "pt_speed", fx.caps["pt_speed_fast"])

    def scene(self, v: Values, suffix: str = "", fade_in: int = 0, fade_out: int = 0, priority: int = 0) -> FunctionSpec:
        return self.spec(
            "Scene", suffix, values=v.spec_values(), roles=v.roles, fade_in=fade_in, fade_out=fade_out, priority=priority
        )

    def finish(self, functions: list, main: FunctionSpec) -> Look:
        if self.p.priority:
            for f in functions:
                f.priority = self.p.priority
        return Look(
            recipe=self.key,
            main_id=main.id,
            name=main.name,
            functions=functions,
            params=self.p,
            assumptions=self.assumptions,
            skipped=self.skipped,
            facts_used=sorted(self.facts),
        )

    def build(self) -> Look:
        raise NotImplementedError


class ColorWash(Recipe):
    key = "color_wash"
    label = "Wash"

    def build(self) -> Look:
        fxs = self.fixtures()
        fade = int(self.p.fade_ms or 0)
        level = self.p.intensity
        has_wheel = any(fx.color_mode == "wheel" for fx in fxs)
        if fade > 0 and has_wheel:
            snap, soft = Values(), Values()
            for i, fx in enumerate(fxs):
                color = self.color_for(i, fx)
                self.put_open(snap, fx)
                if fx.color_mode == "wheel":
                    self.put_color(snap, fx, color, level)
                else:
                    self.put_color(soft, fx, color, level)
                self.put_intensity(soft, fx, level)
            a = self.scene(snap, "Color", priority=self.p.priority)
            b = self.scene(soft, "Level", fade_in=fade, fade_out=fade, priority=self.p.priority)
            main = self.spec("Collection", members=[a.id, b.id])
            main.name = self.name
            self.assumptions.append({"fact": "color wheel snaps while intensity fades (a fading wheel scrolls through colors)", "source": "rig rule"})
            return self.finish([a, b, main], main)
        v = Values()
        for i, fx in enumerate(fxs):
            self.put_open(v, fx)
            self.put_color(v, fx, self.color_for(i, fx), level)
            self.put_intensity(v, fx, level)
        main = self.spec("Scene", values=v.spec_values(), roles=v.roles, fade_in=fade, fade_out=fade, priority=self.p.priority)
        return self.finish([main], main)


class BreathingWash(Recipe):
    key = "breathe"
    label = "Breathe"
    family = "breathe"

    def build(self) -> Look:
        fxs = self.fixtures()
        # 'breathe every 2 s' = one whole breath (up + down); a BPM or speed word sets one fade
        half = max(20, int(self.p.period_ms) // 2) if self.p.period_ms else unit_ms(self.p, self.family)
        level = self.p.intensity
        floor = level * float(self.rig.rules.get("dim_floor_pct", 15)) / 100.0
        base, high, low = Values(), Values(), Values()
        for i, fx in enumerate(fxs):
            color = self.color_for(i, fx)
            self.put_open(base, fx)
            if fx.has("dimmer"):
                self.put_color(base, fx, color, 1.0)
                self.put_intensity(high, fx, level)
                self.put_intensity(low, fx, floor)
            else:
                self.put_color(high, fx, color, level)
                self.put_color(low, fx, color, floor)
        s_base = self.scene(base, "Color") if not base.empty() else None  # RGB-only fixtures (LED walls, panels) have nothing to preset
        s_high = self.scene(high, "High")
        s_low = self.scene(low, "Low")
        ch = self.spec(
            "Chaser",
            "Breath",
            steps=[StepSpec(s_high.id), StepSpec(s_low.id)],
            fade_in=half,
            fade_out=half,
            duration=half,
            speed_modes=("Common", "Common", "Common"),
        )
        main = self.spec("Collection", members=([s_base.id] if s_base else []) + [ch.id])
        main.name = self.name
        self.assumptions.append({"fact": f"one breath = {2 * half} ms (fade up {half} ms, fade down {half} ms), dim floor {self.rig.rules.get('dim_floor_pct', 15)}%", "source": "recipe"})
        return self.finish(([s_base] if s_base else []) + [s_high, s_low, ch, main], main)


class ColorChase(Recipe):
    key = "color_chase"
    label = "Color Chase"
    family = "chase"

    def build(self) -> Look:
        fxs = self.fixtures()
        colors = self.p.colors if len(self.p.colors) >= 2 else (self.p.colors + ["white"])[:2]
        step = unit_ms(self.p, self.family)
        has_wheel = any(fx.color_mode == "wheel" for fx in fxs)
        fade = 0 if has_wheel else int(self.p.fade_ms or 0)
        scenes = []
        for c in colors:
            v = Values()
            for fx in fxs:
                self.put_open(v, fx)
                self.put_color(v, fx, c, self.p.intensity)
                self.put_intensity(v, fx, self.p.intensity)
            scenes.append(self.scene(v, c.title()))
        main = self.spec(
            "Chaser",
            steps=[StepSpec(s.id) for s in scenes],
            fade_in=fade,
            fade_out=0,
            duration=step,
            speed_modes=("Common", "Common", "Common"),
        )
        main.name = self.name
        if has_wheel and self.p.fade_ms:
            self.assumptions.append({"fact": "fade ignored: color-wheel fixtures must snap between colors", "source": "rig rule"})
        return self.finish(scenes + [main], main)


class RunningLight(Recipe):
    key = "running_light"
    label = "Run"
    family = "run"

    def build(self) -> Look:
        fxs = self.fixtures()
        if len(fxs) < 2:
            raise ValueError("running_light needs at least two fixtures")
        step = unit_ms(self.p, self.family)
        base = Values()
        for i, fx in enumerate(fxs):
            self.put_open(base, fx)
            if fx.has("dimmer"):
                self.put_color(base, fx, self.color_for(i, fx), 1.0)
                self.put_intensity(base, fx, 0.0)
        s_base = self.scene(base, "Color") if not base.empty() else None  # RGB-only fixtures have nothing to preset
        scenes = []
        for i, fx in enumerate(fxs):
            v = Values()
            if fx.has("dimmer"):
                self.put_intensity(v, fx, self.p.intensity)
            else:
                self.put_color(v, fx, self.color_for(i, fx), self.p.intensity)
            scenes.append(self.scene(v, f"{i + 1:02d} {fx.name}"))
        ch = self.spec(
            "Chaser",
            "Steps",
            steps=[StepSpec(s.id) for s in scenes],
            fade_in=0,
            fade_out=int(self.p.fade_ms or 0),
            duration=step,
            run_order="PingPong" if self.p.mirror else "Loop",
            speed_modes=("Common", "Common", "Common"),
        )
        main = self.spec("Collection", members=([s_base.id] if s_base else []) + [ch.id])
        main.name = self.name
        self.assumptions.append({"fact": "runs left to right in stage-map order" + (" and bounces back" if self.p.mirror else ""), "source": "overrides.yaml stage_order"})
        return self.finish(([s_base] if s_base else []) + scenes + [ch, main], main)


class Movement(Recipe):
    family = "efx"
    needs_movement = True
    algorithm = "Circle"
    phased = False
    default_size = "medium"
    x_freq, y_freq, x_phase, y_phase = 2, 3, 90, 0
    run_order = "Loop"

    def size(self) -> tuple:
        s = EFX_SIZE.get(self.p.size or self.default_size, EFX_SIZE["medium"])
        return s, s

    def build(self) -> Look:
        fxs = self.fixtures()
        cycle = unit_ms(self.p, self.family)
        base = Values()
        for i, fx in enumerate(fxs):
            self.put_open(base, fx)
            self.put_color(base, fx, self.color_for(i, fx), 1.0)
            self.put_intensity(base, fx, self.p.intensity)
            self.put_fast_motors(base, fx)
        s_base = self.scene(base, "Color")
        max_spread = int(self.rig.rules.get("efx_max_phase_spread_deg", 180))
        n = len(fxs)
        efx_fixtures = []
        for i, fx in enumerate(fxs):
            offset = int(round(i * max_spread / (n - 1))) if (self.phased and n > 1) else 0
            direction = "Forward"
            if self.p.mirror and fx.id in set(self.rig.zones.get("stage_right") or []):
                direction = "Backward"
            efx_fixtures.append(EfxFixtureSpec(id=fx.id, direction=direction, start_offset=offset % 360))
        # QLC+ shares one fader per universe for all fixtures of an EFX and switches it to 8-bit when it
        # starts a fixture whose fine channels are not next to the coarse ones (BEAM230 V3, WASH). Fixtures
        # started before that switch output coarse pan/tilt 0. Starting those fixtures first avoids it;
        # offsets and directions were already computed in stage order, so the look is unchanged.
        first = {fx.id: 0 if noncontiguous_fine(fx) else 1 for fx in fxs}
        efx_fixtures.sort(key=lambda e: first[e.id])
        w, h = self.size()
        efx = self.spec(
            "EFX",
            "Move",
            duration=cycle,
            run_order=self.run_order,
            efx={
                "algorithm": self.algorithm,
                "width": min(127, w),
                "height": min(127, h),
                "x": {"offset": 127, "frequency": self.x_freq, "phase": self.x_phase},
                "y": {"offset": 127, "frequency": self.y_freq, "phase": self.y_phase},
            },
            efx_fixtures=efx_fixtures,
        )
        main = self.spec("Collection", members=[s_base.id, efx.id])
        main.name = self.name
        self.assumptions.append(
            {"fact": f"{self.algorithm} centred at pan/tilt 127/127, size {w}x{h}, one cycle {cycle} ms" + (f", phase spread {max_spread} deg" if self.phased else ""), "source": "recipe + club EFX defaults"}
        )
        return self.finish([s_base, efx, main], main)


class Circle(Movement):
    key = "circle"
    label = "Circles"


class CircleWave(Movement):
    key = "circle_wave"
    label = "Circle Wave"
    phased = True


class Ballyhoo(Movement):
    key = "ballyhoo"
    label = "Ballyhoo"
    family = "ballyhoo"
    algorithm = "Lissajous"
    phased = True
    default_size = "big"

    def size(self) -> tuple:
        s = EFX_SIZE.get(self.p.size or "big", EFX_SIZE["big"])
        return s, max(10, int(s * 0.8))


class Figure8(Movement):
    key = "figure8"
    label = "Figure 8"
    algorithm = "Eight"
    phased = True


class Sweep(Movement):
    key = "sweep"
    label = "Sweep"
    algorithm = "Line"
    default_size = "big"

    def size(self) -> tuple:
        s = EFX_SIZE.get(self.p.size or "big", EFX_SIZE["big"])
        return s, 0


class Strobe(Recipe):
    key = "strobe"
    label = "Strobe"

    def build(self) -> Look:
        fxs = self.fixtures()
        speed = self.p.strobe_speed if self.p.strobe_speed is not None else STROBE_SPEED.get(self.p.rate_word or "medium", 0.55)
        v = Values()
        count = 0
        for i, fx in enumerate(fxs):
            val = self.rig.strobe_value(fx, speed)
            if val is None:
                self.skipped.append({"fixture": fx.id, "name": fx.name, "reason": "no strobe channel"})
                continue
            v.set(fx, "shutter", val)
            self.put_color(v, fx, self.color_for(i, fx), 1.0)
            self.put_intensity(v, fx, self.p.intensity)
            if "macro_off" in fx.caps:
                v.set(fx, "color_macro", fx.caps["macro_off"])
            if "mode_value" in fx.caps:
                v.set(fx, "mode", fx.caps["mode_value"])
            count += 1
        if not count:
            raise ValueError("strobe: none of the target fixtures has a strobe channel")
        main = self.spec("Scene", values=v.spec_values(), roles=v.roles, priority=self.p.priority)
        self.assumptions.append({"fact": f"strobe speed {speed:.2f} of each model's slow-to-fast range", "source": "qxf strobe capability"})
        return self.finish([main], main)


class BlackoutKill(Recipe):
    key = "blackout_kill"
    label = "Kill"
    needs_color = False

    def build(self) -> Look:
        fxs = self.fixtures()
        v = Values()
        for fx in fxs:
            v.set(fx, "dimmer", 0)
            sh = fx.caps.get("shutter") or {}
            if "closed" in sh:
                v.set(fx, "shutter", sh["closed"])
            if not fx.has("dimmer"):
                for role in ("red", "green", "blue", "white", "amber", "uv"):
                    v.set(fx, role, 0)
        pr = self.p.priority or 100
        main = self.spec("Scene", values=v.spec_values(), roles=v.roles, priority=pr)
        return self.finish([main], main)


RECIPES = {
    r.key: r
    for r in (ColorWash, BreathingWash, ColorChase, RunningLight, Circle, CircleWave, Ballyhoo, Figure8, Sweep, Strobe, BlackoutKill)
}

MOVEMENT_TO_RECIPE = {
    "wash": "color_wash",
    "static": "color_wash",
    "solid": "color_wash",
    "breathe": "breathe",
    "pulse": "breathe",
    "chase": "color_chase",
    "color chase": "color_chase",
    "run": "running_light",
    "bounce": "running_light",
    "circle": "circle",
    "wave": "circle_wave",
    "ripple": "circle_wave",
    "ballyhoo": "ballyhoo",
    "search": "ballyhoo",
    "figure8": "figure8",
    "sweep": "sweep",
    "strobe": "strobe",
    "blackout": "blackout_kill",
}


def build_look(rig: Rig, params: LookParams, start_id: Optional[int] = None, reserved: Optional[set] = None) -> Look:
    cls = RECIPES.get(params.recipe)
    if cls is None:
        raise ValueError(f"unknown recipe {params.recipe!r}; known: {sorted(RECIPES)}")
    ids = IdAlloc(start_id if start_id is not None else rig.next_function_id(), reserved)
    return cls(rig, params, ids).build()


def efx_point(algorithm: str, t01: float, width: int, height: int, xf: int = 2, yf: int = 3, xp: int = 90, yp: int = 0) -> tuple:
    """Approximate pan/tilt (0-255) of a QLC+ EFX at phase t (0..1), for live previews."""
    a = t01 * 2 * math.pi
    if algorithm == "Circle":
        x, y = math.cos(a + math.pi / 2), math.cos(a)
    elif algorithm == "Eight":
        x, y = math.cos(3 * math.pi / 2 + 2 * a), math.cos(a)
    elif algorithm in ("Line", "Line2"):
        x, y = math.cos(a), 0.0
    elif algorithm == "Lissajous":
        x, y = math.cos(xf * a - math.radians(xp)), math.cos(yf * a - math.radians(yp))
    else:
        x, y = math.cos(a + math.pi / 2), math.cos(a)
    return int(round(127 + x * width)), int(round(127 + y * height))
