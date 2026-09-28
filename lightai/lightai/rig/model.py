"""The canonical rig: fixtures with derived roles and capabilities, zones, colors, functions.

Everything here is data loaded from the show file, the .qxf definitions, the roles template
(Blank Rig Template channel groups), overrides.yaml (operator facts) and colors.yaml.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from lightai.config import Config, load_config
from lightai.rig.qxf import ChannelDef, FixtureDef, FixtureLibrary
from lightai.rig.qxw import Workspace
from lightai.rig.roles import channel_candidates, channel_group_role, pick_roles

COLOR_ROLES = ("red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow", "lime", "indigo")


@dataclass
class RigFixture:
    id: int
    name: str
    manufacturer: str
    model: str
    mode: str
    universe: int
    address: int
    channels: int
    definition: Optional[FixtureDef] = None
    channel_defs: list = field(default_factory=list)
    roles: dict = field(default_factory=dict)
    role_sources: dict = field(default_factory=dict)
    kind: str = "other"
    caps: dict = field(default_factory=dict)
    zones: list = field(default_factory=list)
    pos: Optional[dict] = None
    notes: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.manufacturer}/{self.model}"

    def has(self, role: str) -> bool:
        return role in self.roles

    def ch(self, role: str) -> Optional[int]:
        return self.roles.get(role)

    def dmx(self, ch: int) -> tuple:
        return self.universe + 1, self.address + ch + 1

    @property
    def can_move(self) -> bool:
        return self.has("pan") and self.has("tilt")

    @property
    def color_mode(self) -> str:
        if self.has("color_wheel") and self.caps.get("wheel"):
            return "wheel"
        if any(self.has(r) for r in ("red", "green", "blue")):
            return "rgb"
        return "none"

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.key,
            "mode": self.mode,
            "universe": self.universe + 1,
            "address": self.address + 1,
            "channels": self.channels,
            "kind": self.kind,
            "roles": self.roles,
            "role_sources": self.role_sources,
            "caps": self.caps,
            "zones": self.zones,
            "pos": self.pos,
            "notes": self.notes,
        }


class ColorBook:
    def __init__(self, doc: dict) -> None:
        self.confirmed = {k.lower(): tuple(v) for k, v in (doc.get("confirmed") or {}).items()}
        self.defaults = {k.lower(): tuple(v) for k, v in (doc.get("defaults") or {}).items()}
        self.aliases = {k.lower(): v.lower() for k, v in (doc.get("aliases") or {}).items()}
        self.mixes = {k.lower(): v for k, v in (doc.get("emitter_mixes") or {}).items()}

    def names(self) -> list:
        return sorted(set(self.confirmed) | set(self.defaults))

    def canonical(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        n = re.sub(r"\s+", " ", name.lower().strip())
        n = re.sub(r"^(the|a|an)\s+", "", n)
        for cand in (n, n.rstrip("s"), n.replace("-", " ")):
            if cand in self.confirmed or cand in self.defaults:
                return cand
            if cand in self.aliases:
                return self.aliases[cand]
        return None

    def rgb(self, name: str) -> tuple:
        c = self.canonical(name)
        if c in self.confirmed:
            return self.confirmed[c], "confirmed"
        if c in self.defaults:
            return self.defaults[c], "default"
        raise KeyError(f"unknown color {name!r}")

    def nearest(self, rgb: tuple, candidates: list) -> Optional[str]:
        best, best_d = None, math.inf
        for c in candidates:
            try:
                crgb, _ = self.rgb(c)
            except KeyError:
                continue
            d = sum((a - b) ** 2 for a, b in zip(rgb, crgb))
            if d < best_d:
                best, best_d = c, d
        return best


def _shutter_caps(cd: ChannelDef) -> dict:
    out: dict = {}
    opens, closes = [], []
    for cap in cd.capabilities:
        p = (cap.preset or "").lower()
        nm = cap.name.lower()
        if p == "shutteropen" or (not p and re.search(r"\b(open|no function|off|no strobe)\b", nm) and "strobe (" not in nm):
            opens.append(cap)
        elif p == "shutterclose" or (not p and re.search(r"\b(blackout|closed?)\b", nm)):
            closes.append(cap)
        elif p in ("strobeslowtofast", "shutterstrobeslowfast") or (not p and "slow" in nm and "fast" in nm and "strobe" in nm and nm.find("slow") < nm.find("fast")):
            out["strobe"] = [cap.min, cap.max]
        elif p in ("strobefasttoslow", "shutterstrobefastslow") or (not p and "strobe" in nm and "fast" in nm and "slow" in nm):
            out["strobe"] = [cap.max, cap.min]
        elif p == "stroberandom" or (not p and "random" in nm and "strobe" in nm):
            out["strobe_random"] = [cap.min, cap.max]
    if opens:
        top = max(opens, key=lambda c: c.max)
        out["open"] = top.max
        out["open_source"] = "qxf capability"
    if closes:
        out["closed"] = min(c.min for c in closes)
    if "strobe" not in out and cd.preset in ("ShutterStrobeSlowFast", "ShutterStrobeFastSlow"):
        out["strobe"] = [0, 255] if cd.preset.endswith("SlowFast") else [255, 0]
    if "open" not in out:
        out["open"] = 0
        out["open_source"] = "assumed (no open capability in .qxf)"
    return out


def _dimmer_ramp(cd: ChannelDef) -> int:
    for cap in cd.capabilities:
        if re.search(r"0\s*-\s*100\s*%", cap.name) and cap.max > 0:
            return cap.max
    return 255


def _neutral_value(cd: ChannelDef, words: tuple, default: int = 0) -> int:
    for cap in cd.capabilities:
        nm = cap.name.lower()
        if any(w in nm for w in words):
            return cap.min
    return default


_YAML_CACHE: dict = {}
_TEMPLATE_CACHE: dict = {}


def _cached_yaml(path: Path) -> dict:
    """yaml.safe_load with a cache keyed by mtime and size (a changed file is re-read); callers get their own copy."""
    path = Path(path)
    st = path.stat()
    key = (st.st_mtime_ns, st.st_size)
    hit = _YAML_CACHE.get(str(path))
    if hit is None or hit[0] != key:
        hit = (key, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        _YAML_CACHE[str(path)] = hit
    return copy.deepcopy(hit[1])


class Rig:
    def __init__(self, cfg: Config, workspace: Workspace, overrides: dict, colors: ColorBook, library: FixtureLibrary) -> None:
        self.cfg = cfg
        self.ws = workspace
        self.overrides = overrides
        self.colors = colors
        self.library = library
        self.fixtures: dict = {}
        self.zones: dict = {}
        self.zone_aliases: dict = {}
        self.fixture_aliases: dict = {}
        self.functions: dict = {f.id: f for f in workspace.functions()}
        self.widgets = workspace.widgets()
        self.function_widgets: dict = {}
        for w in self.widgets:
            if w.function_id is not None:
                self.function_widgets.setdefault(w.function_id, []).append(w.id)
        self.stage_order: list = list(overrides.get("stage_order") or [])
        self.kill_function_id: Optional[int] = overrides.get("kill_function_id")
        self.protected_functions = set(overrides.get("protected_functions") or [])
        self.exclude_from_all = set(overrides.get("exclude_from_all") or [])
        self.rules = overrides.get("rules") or {}
        self.template_roles = self._template_roles()
        self._build_fixtures()
        self._build_zones()

    @classmethod
    def load(cls, cfg: Optional[Config] = None, project: Optional[Path] = None) -> "Rig":
        cfg = cfg or load_config()
        from lightai.rig.facts import LearnedFacts, deep_merge

        ws = Workspace.load(project or cfg.project_path)
        overrides = _cached_yaml(cfg.overrides_path)
        overrides = deep_merge(overrides, LearnedFacts(cfg.learned_path).overlay())
        colors = ColorBook(_cached_yaml(cfg.colors_path))
        return cls(cfg, ws, overrides, colors, FixtureLibrary.shared(cfg.fixture_dirs))

    def reload_workspace(self, workspace: Workspace) -> None:
        self.__init__(self.cfg, workspace, self.overrides, self.colors, self.library)

    def _template_roles(self) -> dict:
        """{fixture_id: {role: channel}} from the roles template's ChannelsGroups."""
        out: dict = {}
        path = self.cfg.roles_template
        if not path or not Path(path).exists():
            return out
        key = (str(path), Path(path).stat().st_mtime_ns)
        if key in _TEMPLATE_CACHE:
            return copy.deepcopy(_TEMPLATE_CACHE[key])
        try:
            tmpl = Workspace.load(Path(path))
        except Exception:
            return out
        for g in tmpl.channels_groups():
            role = channel_group_role(g.name)
            if role is None:
                continue
            for fid, ch in g.pairs:
                out.setdefault(fid, {})[role] = (ch, f"roles template group '{g.name}'")
        _TEMPLATE_CACHE.clear()
        _TEMPLATE_CACHE[key] = copy.deepcopy(out)
        return out

    def _build_fixtures(self) -> None:
        models = self.overrides.get("models") or {}
        per_fixture = self.overrides.get("fixtures") or {}
        positions = self.ws.monitor_items()
        for p in self.ws.fixtures():
            fd = self.library.get(p.manufacturer, p.model, p.mode, p.channels)
            fx = RigFixture(
                id=p.id,
                name=p.name,
                manufacturer=p.manufacturer,
                model=p.model,
                mode=p.mode,
                universe=p.universe,
                address=p.address,
                channels=p.channels,
                definition=fd,
                pos=positions.get(p.id),
            )
            if fd is None:
                fx.notes.append(f"no fixture definition found for {fx.key}; QLC+ treats it as a generic dimmer")
                fd = self.library.get("Generic", "Generic", "", p.channels)
                fx.definition = fd
            cdefs = fd.mode_channels(p.mode) if fd else []
            if not cdefs and fd and fd.synthetic:
                cdefs = [fd.channels[n] for n in next(iter(fd.modes.values())).channels]
            if not cdefs:
                fx.notes.append(f"mode {p.mode!r} not found in {fx.key} definition")
            fx.channel_defs = cdefs[: p.channels] if cdefs else []

            candidates: dict = {}
            for idx, cd in enumerate(fx.channel_defs):
                candidates[idx] = channel_candidates(cd)
            for role, (ch, src) in (self.template_roles.get(p.id) or {}).items():
                candidates.setdefault(ch, []).append((role, 5.0, src))
            model_ov = models.get(fx.key) or {}
            fix_ov = per_fixture.get(p.id) or per_fixture.get(str(p.id)) or {}
            for ov, label in ((model_ov, "model"), (fix_ov, "fixture")):
                for role, fact in (ov.get("roles") or {}).items():
                    ch = fact["channel"] if isinstance(fact, dict) else int(fact)
                    src = fact.get("source", "operator") if isinstance(fact, dict) else "operator"
                    candidates.setdefault(ch, []).append((role, 10.0, f"{label} fact ({src})"))
            fx.roles, fx.role_sources, _ = pick_roles(candidates)
            fx.roles = {r: c for r, c in fx.roles.items() if 0 <= c < p.channels}

            self._derive_caps(fx, model_ov, fix_ov)
            fx.kind = fix_ov.get("kind") or model_ov.get("kind") or self._guess_kind(fx)
            self.fixtures[p.id] = fx

    def _guess_kind(self, fx: RigFixture) -> str:
        t = (fx.definition.type if fx.definition else "").lower()
        if fx.can_move:
            return "spot" if fx.color_mode == "wheel" else "wash"
        if "laser" in t or "effect" in t:
            return "fx"
        if "smoke" in t or "hazer" in t:
            return "fog"
        if "bar" in t:
            return "bar"
        if fx.color_mode == "rgb":
            return "par" if fx.has("dimmer") else "rgb"
        if fx.has("dimmer"):
            return "dimmer"
        return "other"

    def _derive_caps(self, fx: RigFixture, model_ov: dict, fix_ov: dict) -> None:
        caps: dict = {}
        cd = lambda role: fx.channel_defs[fx.roles[role]] if role in fx.roles and fx.roles[role] < len(fx.channel_defs) else None  # noqa: E731
        d = cd("dimmer")
        if d is not None:
            caps["dimmer_ramp_max"] = _dimmer_ramp(d)
        s = cd("shutter")
        if s is not None:
            caps["shutter"] = _shutter_caps(s)
        w = cd("color_wheel")
        if w is not None:
            slots = {}
            for cap in w.capabilities:
                c = self.colors.canonical(cap.name)
                if c and c not in slots:
                    slots[c] = cap.min
            caps["wheel"] = slots
        m = cd("color_macro")
        if m is not None:
            caps["macro_off"] = _neutral_value(m, ("off", "no function", "none", "manual"), 0)
        mo = cd("mode")
        if mo is not None:
            caps["mode_value"] = _neutral_value(mo, ("rgb mode", "manual", "dmx"), 0)
        sp = cd("pt_speed")
        if sp is not None:
            caps["pt_speed_fast"] = 0 if (sp.preset or "").endswith("FastSlow") else 255
            caps["pt_speed_fast_source"] = "qxf preset"
        for ov in (model_ov, fix_ov):
            if "pt_speed_fast" in ov and "pt_speed" in fx.roles:
                caps["pt_speed_fast"] = ov["pt_speed_fast"]["value"] if isinstance(ov["pt_speed_fast"], dict) else ov["pt_speed_fast"]
                caps["pt_speed_fast_source"] = "operator"
            if "shutter_open" in ov and "shutter" in caps:
                caps["shutter"]["open"] = ov["shutter_open"]["value"] if isinstance(ov["shutter_open"], dict) else ov["shutter_open"]
                caps["shutter"]["open_source"] = "operator"
            for color, fact in (ov.get("wheel") or {}).items():
                caps.setdefault("wheel", {})[color] = fact["value"] if isinstance(fact, dict) else int(fact)
            for color, values in (ov.get("wheel_not") or {}).items():
                caps.setdefault("wheel_not", {}).setdefault(color, [])
                caps["wheel_not"][color] = sorted(set(caps["wheel_not"][color]) | {int(v) for v in values})
        fx.caps = caps

    def _build_zones(self) -> None:
        for name, z in (self.overrides.get("zones") or {}).items():
            ids = [i for i in (z.get("ids") or []) if i in self.fixtures]
            if name == "all":
                ids = [i for i in self.fixtures if i not in self.exclude_from_all]
            self.zones[name] = ids
            for a in [name.replace("_", " ")] + list(z.get("aliases") or []):
                self.zone_aliases[a.lower()] = name
            for i in ids:
                self.fixtures[i].zones.append(name)
        for fx in self.fixtures.values():
            base = fx.name.lower()
            self.fixture_aliases.setdefault(base, fx.id)
            m = re.search(r"#\s*(\d+)", fx.name)
            if m:
                self.fixture_aliases.setdefault(f"{fx.kind} #{m.group(1)}", fx.id)
        for fid, fx in self.fixtures.items():
            if fid not in self.stage_order and fx.pos:
                pass

    def ordered(self, ids: list) -> list:
        """Stage order left to right; fixtures not in the stage map follow by monitor X position."""
        rank = {fid: i for i, fid in enumerate(self.stage_order)}
        def key(fid):
            if fid in rank:
                return (0, rank[fid], 0.0)
            pos = self.fixtures[fid].pos if fid in self.fixtures else None
            return (1, 0, pos["x"] if pos else float(fid))
        return sorted(ids, key=key)

    def zone_of_phrase(self, phrase: str) -> Optional[str]:
        p = re.sub(r"\s+", " ", phrase.lower().strip())
        p = re.sub(r"^(the|all the|all of the|all)\s+", "", p) if p not in self.zone_aliases else p
        if p in self.zone_aliases:
            return self.zone_aliases[p]
        if p.rstrip("s") in self.zone_aliases:
            return self.zone_aliases[p.rstrip("s")]
        return None

    def next_function_id(self) -> int:
        return max(self.functions.keys(), default=-1) + 1

    def wheel_value(self, fx: RigFixture, color: str) -> dict:
        """Pick the color-wheel DMX value for a color name on one fixture."""
        wheel = dict(fx.caps.get("wheel") or {})
        c = self.colors.canonical(color) or color
        bad = set((fx.caps.get("wheel_not") or {}).get(c) or [])
        usable = {name: v for name, v in wheel.items() if v not in bad}
        if c in usable:
            return {"value": usable[c], "slot": c, "exact": True}
        try:
            target, _ = self.colors.rgb(c)
        except KeyError:
            target = None
        if target is not None and usable:
            near = self.colors.nearest(target, list(usable))
            if near and bad:
                nrgb, _ = self.colors.rgb(near)
                if sum((a - b) ** 2 for a, b in zip(target, nrgb)) > 3 * 90 ** 2:
                    return {"value": None, "slot": None, "exact": False, "unknown": True}
            if near:
                return {"value": usable[near], "slot": near, "exact": False, "ruled_out": bool(bad) and c in wheel}
        if usable:
            first = next(iter(usable))
            return {"value": usable[first], "slot": first, "exact": False}
        return {"value": None, "slot": None, "exact": False}

    def emitter_values(self, fx: RigFixture, color: str, level01: float = 1.0) -> dict:
        """Channel values for RGB(W/A/UV) emitters that realize a color at a level (0..1)."""
        c = self.colors.canonical(color) or color
        rgb, _ = self.colors.rgb(c)
        emitters = {r: fx.ch(r) for r in COLOR_ROLES if fx.has(r)}
        out: dict = {}
        mix = self.colors.mixes.get(c)
        if mix and all(k in emitters for k in mix if mix[k]) and any(k not in ("red", "green", "blue") for k in mix if mix[k]):
            for role in emitters:
                out[role] = int(mix.get(role, 0))
        else:
            r, g, b = rgb
            if "white" in emitters:
                w = min(r, g, b)
                r, g, b = r - w, g - w, b - w
                out["white"] = w
            out.update({"red": r, "green": g, "blue": b})
            for role in emitters:
                out.setdefault(role, 0)
        scale = 1.0 if fx.has("dimmer") else max(0.0, min(1.0, level01))
        return {role: int(round(v * scale)) for role, v in out.items() if role in emitters}

    def dimmer_value(self, fx: RigFixture, level01: float) -> Optional[int]:
        if not fx.has("dimmer"):
            return None
        level01 = max(0.0, min(1.0, level01))
        if level01 >= 0.999:
            return 255
        return int(round(level01 * fx.caps.get("dimmer_ramp_max", 255)))

    def shutter_open(self, fx: RigFixture) -> Optional[int]:
        sh = fx.caps.get("shutter")
        return sh.get("open") if sh else None

    def strobe_value(self, fx: RigFixture, speed01: float) -> Optional[int]:
        sh = fx.caps.get("shutter")
        if not sh or "strobe" not in sh:
            return None
        lo, hi = sh["strobe"]
        speed01 = max(0.0, min(1.0, speed01))
        return int(round(lo + (hi - lo) * speed01))

    def summary(self) -> dict:
        return {
            "project": str(self.ws.path) if self.ws.path else None,
            "fixtures": [fx.summary() for fx in sorted(self.fixtures.values(), key=lambda f: f.id)],
            "zones": self.zones,
            "stage_order": self.stage_order,
            "kill_function_id": self.kill_function_id,
            "functions": len(self.functions),
            "next_function_id": self.next_function_id(),
        }
