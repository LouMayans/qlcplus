"""RGB Matrix (pixel effect) recipes, loaded automatically by recipes.py's `_load_extra()`.

An RGB Matrix drives a <FixtureGroup> pixel grid with a QLC+ script algorithm (Stripes, Waves,
...). Each recipe here builds (or reuses) a one-row FixtureGroup whose X axis follows the look's
spatial order (Stage.order / the rig's stage-map order), with one grid cell per RGB-capable head
of each target fixture -- a single-head RGB wall/panel contributes one cell, a multi-head pixel
bar (e.g. a Tetra Bar patched in a 12+ channel mode) contributes one cell per pixel segment -- so
the script chases or waves across the room left to right.

Same rules as recipes.py/recipes_fx.py: nothing here knows raw DMX channel numbers for the matrix
itself (QLC+ resolves each head's own R/G/B channels from the fixture definition at run time); the
only per-channel lookups here are to *detect* which heads are RGB-capable, via the same evidence
based lightai.rig.roles.channel_candidates() the rest of the rig model uses.
"""

from __future__ import annotations

from lightai.compiler.recipes import Recipe, unit_ms
from lightai.compiler.spec import FunctionSpec, GroupHeadSpec, Look
from lightai.rig.model import RigFixture
from lightai.rig.roles import channel_candidates

RGB_ROLES = ("red", "green", "blue")


def _head_channel_lists(fx: RigFixture) -> list:
    """Per-head 0-based channel-index lists for this fixture's patched mode; one implicit head
    spanning every patched channel when the mode has no explicit <Head> blocks (QLC+'s own
    fallback for such modes -- see Fixture::setFixtureDefinition)."""
    fd = fx.definition
    mode = fd.mode(fx.mode) if fd else None
    if mode and mode.heads:
        return mode.heads
    return [list(range(len(fx.channel_defs)))]


def _head_has_rgb(fx: RigFixture, channel_idxs: list) -> bool:
    for idx in channel_idxs:
        if idx < 0 or idx >= len(fx.channel_defs):
            continue
        roles = {role for role, _, _ in channel_candidates(fx.channel_defs[idx])}
        if roles & set(RGB_ROLES):
            return True
    return False


def rgb_heads(fx: RigFixture) -> list:
    """0-based indices of this fixture's heads that have their own Red/Green/Blue channels, in
    head order (e.g. the 4 pixel segments of a multi-head bar, left to right along the fixture)."""
    return [i for i, chs in enumerate(_head_channel_lists(fx)) if _head_has_rgb(fx, chs)]


def pack_color(rgb: tuple) -> int:
    """0xFFRRGGBB as the decimal int RGBMatrix::saveXML stores for QColor::rgb() (alpha opaque)."""
    r, g, b = (max(0, min(255, int(v))) for v in rgb)
    return 0xFF000000 | (r << 16) | (g << 8) | b


class PixelMatrix(Recipe):
    """Shared machinery: build/reuse a one-row FixtureGroup over the look's targets, then one
    RGBMatrix function running a script algorithm over it."""

    family = "run"
    algorithm_name = "Stripes"
    options = ("colors", "intensity", "rate", "order", "mirror", "fade", "priority")

    def build(self) -> Look:
        fxs = self.fixtures()
        cells = []  # (fixture_id, head_index), left to right along the look's spatial order
        for fx in fxs:
            heads = rgb_heads(fx)
            if not heads:
                self.skipped.append({"fixture": fx.id, "name": fx.name, "reason": "no RGB head in its patched mode"})
                continue
            cells.extend((fx.id, h) for h in heads)
        if not cells:
            raise ValueError(f"{self.key}: none of the target fixtures has an RGB head")

        functions = []
        group_id, group_spec, reused = self.find_or_make_group(cells)

        colors = list(self.p.colors) or ["white"]
        packed = [pack_color(self.rgb_of(c)) for c in colors[:2]]  # Script algorithms use at most a start+end color
        step = unit_ms(self.p, self.family)
        fade = int(self.p.fade_ms or 0)
        matrix = self.spec(
            "RGBMatrix",
            direction="Backward" if self.p.mirror else "Forward",
            fade_in=fade,
            fade_out=fade,
            duration=step,
            matrix={
                "algorithm_type": "Script",
                "algorithm_name": self.algorithm_name,
                "colors": packed,
                "control_mode": "RGB",
                "fixture_group": group_id,
            },
        )
        functions.append(matrix)

        how = (self.p.order or "left_to_right").replace("_", " ")
        group_note = f"reusing fixture group {group_id}" if reused else f"new fixture group {group_id} ({len(cells)} pixel(s))"
        self.assumptions.append({
            "fact": f"{self.algorithm_name} script, {group_note}, grid follows {how}, one step {step} ms",
            "source": "3D stage" if self.p.order else "overrides.yaml stage_order",
        })
        look = self.finish(functions, matrix)
        look.groups = [group_spec] if group_spec is not None else []  # never in look.ids: groups have their own IDs
        return look

    def rgb_of(self, color: str) -> tuple:
        c = self.rig.colors.canonical(color) or color
        rgb, _ = self.rig.colors.rgb(c)
        return rgb

    def find_or_make_group(self, cells: list):
        """Reuse an existing <FixtureGroup> whose heads exactly match this order; otherwise mint
        a new single-row one with the next free group ID (its own namespace in QLC+, distinct
        from function IDs -- see engine/src/doc.cpp's separate FixtureGroup/Function counters)."""
        existing = [(g.id, [(h["x"], h["y"], h["fixture"], h["head"]) for h in sorted(g.heads, key=lambda h: (h["y"], h["x"]))])
                    for g in self.rig.ws.fixture_groups()]
        pending = self.ids.groups  # minted by earlier looks of the same design, not in the show yet
        existing += [(s.id, [(h.x, h.y, h.fixture, h.head) for h in sorted(s.group_heads, key=lambda h: (h.y, h.x))]) for s in pending]
        wanted = [(i, 0, fid, head) for i, (fid, head) in enumerate(cells)]
        for gid, got in existing:
            if got == wanted:
                return gid, None, True
        next_id = max((gid for gid, _ in existing), default=-1) + 1
        heads = [GroupHeadSpec(x=i, y=0, fixture=fid, head=head) for i, (fid, head) in enumerate(cells)]
        name = f"{self.p.target_label or self.name} Pixels"
        spec = FunctionSpec(id=next_id, type="FixtureGroup", name=name, group_size=(len(cells), 1), group_heads=heads)
        pending.append(spec)
        return next_id, spec, False


class PixelChase(PixelMatrix):
    key = "pixel_chase"
    label = "Pixel Chase"
    algorithm_name = "Stripes"  # one lit pixel/segment steps across the grid (hard edge)


class PixelWave(PixelMatrix):
    key = "pixel_wave"
    label = "Pixel Wave"
    algorithm_name = "Waves"  # a soft fading tail sweeps across the grid


PixelChase.about = "one lit pixel/segment runs across the fixtures in order (RGB Matrix, Stripes script)"
PixelWave.about = "a soft color wave with a fading tail sweeps across the fixtures in order (RGB Matrix, Waves script)"

RECIPES_PIXEL = (PixelChase, PixelWave)

WORDS_PIXEL = {
    "pixel chase": "pixel_chase",
    "pixel run": "pixel_chase",
    "pixel wave": "pixel_wave",
    "matrix wave": "pixel_wave",
}
