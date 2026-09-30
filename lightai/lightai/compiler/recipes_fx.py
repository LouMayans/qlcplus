"""Extra look recipes, loaded automatically by recipes.py's `_load_extra()`.

Same rules as recipes.py: nothing here knows channel numbers, everything goes through the rig's
fixture roles, and every look is built from self.spec()/self.scene() so IDs and naming stay
consistent with the rest of the compiler.
"""

from __future__ import annotations

import math

from lightai.compiler.recipes import Recipe, STROBE_SPEED, Values, unit_ms
from lightai.compiler.spec import Look, StepSpec

PASTEL_COLORS = ("lavender", "pink", "sky blue", "mint")
BURST_BEATS = 1
CALM_BEATS = 3


def _bump(centre: float, pos: float, half_width: float = 0.15) -> float:
    """cos^2 falloff: 1.0 at pos == centre, 0.0 at |pos - centre| >= half_width (floor 0)."""
    d = abs(pos - centre)
    if d >= half_width:
        return 0.0
    return math.cos((d / half_width) * (math.pi / 2)) ** 2


class DimmerWave(Recipe):
    key = "dimmer_wave"
    label = "Wave"
    family = "run"

    def build(self) -> Look:
        fxs = self.fixtures()
        n = len(fxs)
        steps_n = max(n, 4)
        step_ms = unit_ms(self.p, self.family)

        def frac_of(i: int) -> float:
            return i / (n - 1) if n > 1 else 0.5

        def centre_of(k: int) -> float:
            return k / (steps_n - 1) if steps_n > 1 else 0.5

        base = Values()
        for i, fx in enumerate(fxs):
            self.put_open(base, fx)
            if fx.has("dimmer"):
                self.put_color(base, fx, self.color_for(i, fx), 1.0)
                self.put_intensity(base, fx, 0.0)
        s_base = self.scene(base, "Color") if not base.empty() else None  # RGB-only fixtures carry the wave in color level, not a preset

        scenes = []
        for k in range(steps_n):
            v = Values()
            centre = centre_of(k)
            for i, fx in enumerate(fxs):
                level = self.p.intensity * _bump(centre, frac_of(i))
                if fx.has("dimmer"):
                    self.put_intensity(v, fx, level)
                else:
                    self.put_color(v, fx, self.color_for(i, fx), level)
            scenes.append(self.scene(v, f"{k + 1:02d}"))

        ch = self.spec(
            "Chaser",
            "Steps",
            steps=[StepSpec(s.id) for s in scenes],
            fade_in=step_ms,
            fade_out=0,
            duration=step_ms,
            run_order="PingPong" if self.p.mirror else "Loop",
            speed_modes=("Common", "Common", "Common"),
        )
        main = self.spec("Collection", members=([s_base.id] if s_base else []) + [ch.id])
        main.name = self.name
        self.assumptions.append({
            "fact": f"{steps_n} steps, bump width ~30% of the fixtures, cos^2 falloff, floor 0",
            "source": "recipe",
        })
        return self.finish(([s_base] if s_base else []) + scenes + [ch, main], main)


class ColorMorph(Recipe):
    key = "color_morph"
    label = "Morph"
    family = "ballyhoo"  # RATE_BEATS' slowest default: long, dreamy crossfades

    def build(self) -> Look:
        fxs = self.fixtures()
        colors = list(self.p.colors) if len(self.p.colors) >= 2 else list(PASTEL_COLORS)
        if len(self.p.colors) < 2:
            self.assumptions.append({
                "fact": f"fewer than 2 colors given; using a default pastel set: {', '.join(c.title() for c in colors)}",
                "source": "recipe default",
            })
        m = len(colors)
        ids = [fx.id for fx in fxs]
        st = self.rig.stage
        order = self.p.order or "left_to_right"
        if st is not None:
            frac_map = st.position_fraction(ids, order)
            fractions = [frac_map.get(fx.id, 0.0) for fx in fxs]
            self.facts.add(f"stage:{st.hash}")
        else:
            n = len(fxs)
            fractions = [(i / (n - 1) if n > 1 else 0.0) for i in range(n)]
            if self.p.order:
                self.assumptions.append({"fact": f"no 3D stage file for this show: '{self.p.order}' not applied, fixture order used for the gradient", "source": "stage"})

        has_wheel = any(fx.color_mode == "wheel" for fx in fxs)
        if has_wheel:
            self.assumptions.append({
                "fact": f"color-wheel fixtures can't crossfade colors; kept fixed on '{colors[0]}'",
                "source": "rig rule",
            })

        fade = int(self.p.fade_ms) if self.p.fade_ms else unit_ms(self.p, self.family)
        scenes = []
        for k in range(m):
            v = Values()
            for i, fx in enumerate(fxs):
                self.put_open(v, fx)
                if fx.color_mode == "wheel":
                    color = colors[0]
                else:
                    offset = round(fractions[i] * (m - 1)) if m > 1 else 0
                    color = colors[(k + offset) % m]
                self.put_color(v, fx, color, self.p.intensity)
                self.put_intensity(v, fx, self.p.intensity)
            scenes.append(self.scene(v, f"{k + 1:02d} {colors[k].title()}"))

        main = self.spec(
            "Chaser",
            steps=[StepSpec(s.id) for s in scenes],
            fade_in=fade,
            fade_out=0,
            duration=fade,
            run_order="Loop",
            speed_modes=("Common", "Common", "Common"),
        )
        main.name = self.name
        how = order.replace("_", " ")
        self.assumptions.append({"fact": f"gradient shifts along {how}, one whole crossfade {fade} ms",
                                 "source": "3D stage" if st is not None else "fixture order"})
        return self.finish(scenes + [main], main)


class StrobeChase(Recipe):
    key = "strobe_chase"
    label = "Strobe Chase"
    family = "run"

    def build(self) -> Look:
        fxs = self.fixtures()
        speed = self.p.strobe_speed if self.p.strobe_speed is not None else STROBE_SPEED.get(self.p.rate_word or "fast", STROBE_SPEED["fast"])
        step_ms = unit_ms(self.p, self.family)
        usable = []
        for i, fx in enumerate(fxs):
            val = self.rig.strobe_value(fx, speed)
            if val is None:
                self.skipped.append({"fixture": fx.id, "name": fx.name, "reason": "no strobe channel"})
                continue
            usable.append((i, fx, val))
        if not usable:
            raise ValueError("strobe_chase: none of the target fixtures has a strobe channel")

        group = 2 if len(usable) > 8 else 1
        scenes = []
        for gi in range(0, len(usable), group):
            chunk = usable[gi:gi + group]
            v = Values()
            for i, fx, val in chunk:
                v.set(fx, "shutter", val)
                self.put_color(v, fx, self.color_for(i, fx), self.p.intensity)
                self.put_intensity(v, fx, self.p.intensity)
                if "macro_off" in fx.caps:
                    v.set(fx, "color_macro", fx.caps["macro_off"])
                if "mode_value" in fx.caps:
                    v.set(fx, "mode", fx.caps["mode_value"])
            names = "+".join(fx.name for _, fx, _ in chunk)
            scenes.append(self.scene(v, f"{gi // group + 1:02d} {names}"))

        main = self.spec(
            "Chaser",
            steps=[StepSpec(s.id) for s in scenes],
            duration=step_ms,
            run_order="PingPong" if self.p.mirror else "Loop",
            speed_modes=("Common", "Common", "Common"),
        )
        main.name = self.name
        self.assumptions.append({
            "fact": f"strobe speed {speed:.2f}, {len(usable)} strobing fixture(s) in groups of {group}, step {step_ms} ms",
            "source": "recipe",
        })
        return self.finish(scenes + [main], main)


class StrobeBurst(Recipe):
    key = "strobe_burst"
    label = "Burst"
    family = "chase"

    def build(self) -> Look:
        fxs = self.fixtures()
        speed = self.p.strobe_speed if self.p.strobe_speed is not None else STROBE_SPEED.get(self.p.rate_word or "fast", STROBE_SPEED["fast"])
        if not any(self.rig.strobe_value(fx, speed) is not None for fx in fxs):
            for fx in fxs:
                self.skipped.append({"fixture": fx.id, "name": fx.name, "reason": "no strobe channel"})
            raise ValueError("strobe_burst: none of the target fixtures has a strobe channel")

        beat = unit_ms(self.p, self.family)
        burst_hold, calm_hold = beat * BURST_BEATS, beat * CALM_BEATS
        colors = self.p.colors or ["white"]

        functions = []
        steps = []
        for color in colors:
            burst = Values()
            for i, fx in enumerate(fxs):
                val = self.rig.strobe_value(fx, speed)
                if val is not None:
                    burst.set(fx, "shutter", val)
                self.put_color(burst, fx, color, self.p.intensity)
                self.put_intensity(burst, fx, self.p.intensity)
                if "macro_off" in fx.caps:
                    burst.set(fx, "color_macro", fx.caps["macro_off"])
                if "mode_value" in fx.caps:
                    burst.set(fx, "mode", fx.caps["mode_value"])
            s_burst = self.scene(burst, f"Burst {color.title()}")

            calm = Values()
            for i, fx in enumerate(fxs):
                self.put_open(calm, fx)
                self.put_color(calm, fx, color, self.p.intensity)
                self.put_intensity(calm, fx, self.p.intensity)
            s_calm = self.scene(calm, f"Calm {color.title()}")

            functions.extend([s_burst, s_calm])
            steps.append(StepSpec(s_burst.id, fade_in=0, hold=burst_hold, fade_out=0))
            steps.append(StepSpec(s_calm.id, fade_in=0, hold=calm_hold, fade_out=0))

        main = self.spec(
            "Chaser",
            steps=steps,
            duration=burst_hold,
            run_order="Loop",
            speed_modes=("PerStep", "PerStep", "PerStep"),
        )
        main.name = self.name
        self.assumptions.append({
            "fact": f"burst {burst_hold} ms ({BURST_BEATS} beat), calm {calm_hold} ms ({CALM_BEATS} beats), strobe speed {speed:.2f}, no fades on bursts",
            "source": "recipe",
        })
        return self.finish(functions + [main], main)


DimmerWave.about = "an intensity wave travels through the fixtures in order (bounces with mirror)"
ColorMorph.about = "long dreamy crossfades between colors; a moving gradient when the stage/order is known"
StrobeChase.about = "strobe hits move through the fixtures in order (bounces with mirror)"
StrobeBurst.about = "rhythmic strobe bursts alternating with a calm color, cycling through colors"

DimmerWave.options = ("colors", "intensity", "rate", "order", "mirror", "priority")
ColorMorph.options = ("colors", "intensity", "rate", "fade", "order", "priority")
StrobeChase.options = ("colors", "intensity", "strobe_speed", "rate", "order", "mirror", "priority")
StrobeBurst.options = ("colors", "intensity", "strobe_speed", "rate", "priority")

RECIPES_FX = (DimmerWave, ColorMorph, StrobeChase, StrobeBurst)

WORDS_FX = {
    "dimmer wave": "dimmer_wave",
    "intensity wave": "dimmer_wave",
    "morph": "color_morph",
    "color morph": "color_morph",
    "fade through colors": "color_morph",
    "strobe chase": "strobe_chase",
    "strobe run": "strobe_chase",
    "burst": "strobe_burst",
    "strobe burst": "strobe_burst",
    "strobe bursts": "strobe_burst",
}
