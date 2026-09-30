"""The private reference library: shows made in other programs, decoded into facts the designer can learn from.

Files the operator collects (xLights .xsq, Light-O-Rama .lms/.loredit, Vixen 3 .tim, and MVR rigs) live in
<data_dir>/references/ (never in the repo, never redistributed). `build_index` decodes each file once (cached by size
and modification time) into a compact summary in references/index.json: pacing (cues per minute, average cue length),
energy shape, effect mix, palette families, sections and spatial moves. `query_references` ranks them for a request
by its words, moods and effects, and `references_brief` turns the best few into a short section of the designer's
briefing: structure and pacing to borrow, never content to copy.
"""

from __future__ import annotations

import colorsys
import json
import re
import time
from pathlib import Path
from typing import Optional

INDEX_VERSION = 1
RIG_EXTS = (".mvr",)
STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "some", "make", "create", "show", "shows", "light",
        "lights", "lighting", "like", "can", "you", "different", "resemble", "way", "more", "less", "but", "changing"}
# mood words -> (pace: "slow" | "mid" | "fast", wants pastel colors)
MOODS = {
    **dict.fromkeys(("dreamy", "dream", "ethereal", "chill", "ambient", "calm", "soft", "floaty", "floating", "romantic",
                     "lounge", "mellow", "gentle", "serene", "slow"), ("slow", True)),
    **dict.fromkeys(("hypnotic", "groovy", "funky", "house", "disco", "deep", "steady", "sultry", "moody"), ("mid", False)),
    **dict.fromkeys(("energetic", "energy", "hype", "intense", "peak", "drop", "party", "techno", "edm", "rave", "fast",
                     "aggressive", "strobing", "banger", "euphoric", "build", "buildup", "wild"), ("fast", False)),
}
# lightai effect words -> substrings of effect names in other programs
EFFECTS = {
    "strobe": ("strobe", "shimmer", "flash", "twinkle", "sparkle", "lightning"),
    "chase": ("chase", "bars", "marquee", "single strand", "running", "wipe", "curtain"),
    "wave": ("wave", "ripple", "shockwave", "butterfly", "wipe"),
    "morph": ("morph", "colorwash", "color wash", "fade", "gradient", "set level", "pulse"),
    "circle": ("circle", "spiral", "pinwheel", "fan", "galaxy", "swirl"),
    "sparkle": ("twinkle", "sparkle", "snowflakes", "meteors", "fireworks"),
    "rainbow": ("rainbow", "color wash", "colorwash", "plasma"),
    "pulse": ("pulse", "on", "fade_up", "fade up", "fade_down", "set level"),
}
EFFECT_WORDS = {"strobe": "strobe", "strobes": "strobe", "strobing": "strobe", "flash": "strobe", "chase": "chase",
                "chases": "chase", "wave": "wave", "waves": "wave", "morph": "morph", "crossfade": "morph", "wash": "morph",
                "circle": "circle", "circles": "circle", "spiral": "circle", "sparkle": "sparkle", "twinkle": "sparkle",
                "rainbow": "rainbow", "pulse": "pulse", "pulsing": "pulse", "breathing": "pulse"}
HUES = ((12, "red"), (38, "orange"), (52, "amber"), (68, "yellow"), (165, "green"), (200, "cyan"), (255, "blue"),
        (285, "purple"), (320, "magenta"), (345, "pink"), (361, "red"))
MAX_FILE_BYTES = 256 * 1024 * 1024


def references_dir(cfg) -> Path:
    return Path(getattr(cfg, "references_dir", None) or Path(cfg.data_dir) / "references")


def color_family(hexstr: str) -> tuple:
    """('blue', pastel?) for '#RRGGBB'; ('white', False) for greys, ('black', False) for very dark colors."""
    h = hexstr.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
    if val < 0.15:
        return "black", False
    if sat < 0.18:
        return "white", False
    deg = hue * 360.0
    return next(name for limit, name in HUES if deg < limit), sat < 0.55 and val > 0.75


def _shape(curve: list, duration_s: float, buckets: int = 8) -> str:
    """The energy curve as `buckets` digits 0-9 (mean energy per slice of the show)."""
    if not curve or duration_s <= 0:
        return ""
    out = []
    for b in range(buckets):
        lo, hi = duration_s * b / buckets, duration_s * (b + 1) / buckets
        vals = [e for t, e in curve if lo <= t < hi] or [min(curve, key=lambda te: abs(te[0] - (lo + hi) / 2))[1]]
        out.append(str(min(9, int(round(9 * sum(vals) / len(vals))))))
    return "".join(out)


def summarize(ref) -> dict:
    """The facts the designer can use from one decoded show (a lightai.mine.formats ReferenceShow)."""
    dur = float(ref.duration_s or 0.0)
    lengths = [max(0.0, c.end_s - c.start_s) for c in ref.cues]
    total = sum(ref.effect_types.values()) or 1
    effects = sorted(ref.effect_types.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
    fams: dict = {}
    pastel = 0
    for hx in ref.palette:
        try:
            name, soft = color_family(hx)
        except (ValueError, IndexError):
            continue
        fams[name] = fams.get(name, 0) + 1
        pastel += int(soft)
    energies = [e for _, e in ref.energy_curve]
    song = ref.song.model_dump(exclude_none=True) if ref.song else {}
    return {
        "kind": "show", "format": ref.format, "title": ref.title, "song": song, "bpm": ref.bpm, "duration_s": round(dur, 1),
        "cues": len(ref.cues), "cues_per_min": round(len(ref.cues) / (dur / 60.0), 1) if dur > 0 else None,
        "avg_cue_s": round(sum(lengths) / len(lengths), 2) if lengths else None,
        "effects": [[name, round(n / total, 2)] for name, n in effects],
        "palette": sorted(fams, key=lambda k: -fams[k]), "pastel_share": round(pastel / len(ref.palette), 2) if ref.palette else 0.0,
        "energy_mean": round(sum(energies) / len(energies), 2) if energies else None,
        "energy_peak": round(max(energies), 2) if energies else None, "energy_shape": _shape(ref.energy_curve, dur),
        "sections": [{"name": s.name, "start_s": round(s.start_s, 1), "end_s": round(s.end_s, 1), "energy": round(s.energy, 2)}
                     for s in ref.sections[:24]],
        "moves": list(ref.spatial_moves[:6]), "fixture_kinds": list(ref.fixture_kinds[:8]),
    }


def summarize_rig(scene, name: str) -> dict:
    """A 3D rig (MVR scene) from another venue: size and what hangs where, in inches."""
    pts = [f.position for f in scene.fixtures]
    extent = [round((max(p[i] for p in pts) - min(p[i] for p in pts)) / 25.4, 1) for i in range(3)] if pts else [0, 0, 0]
    specs: dict = {}
    for f in scene.fixtures:
        key = (f.gdtf_spec or "unknown").rsplit(".", 1)[0]
        specs[key] = specs.get(key, 0) + 1
    return {"kind": "rig", "format": "mvr", "title": name, "fixtures": len(scene.fixtures), "extent_in": extent,
            "fixture_types": sorted(specs.items(), key=lambda kv: -kv[1])[:10], "layers": list(scene.layers[:12])}


def _decodable_exts() -> tuple:
    from lightai.mine.formats import registered

    return tuple(e for d in registered() for e in d.extensions) + RIG_EXTS


def build_index(cfg, *, force: bool = False) -> dict:
    """Decode what's new or changed in the reference folder; returns the index (also saved as index.json)."""
    from lightai.mine.formats import decode_file

    root = references_dir(cfg)
    path = root / "index.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() and not force else {}
    except ValueError:
        old = {}
    known = old.get("files", {}) if old.get("version") == INDEX_VERSION else {}
    files: dict = {}
    exts = _decodable_exts()
    for f in sorted(root.rglob("*")) if root.exists() else []:
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        rel = f.relative_to(root).as_posix()
        st = f.stat()
        prev = known.get(rel)
        if prev and prev.get("size") == st.st_size and prev.get("mtime") == int(st.st_mtime):
            files[rel] = prev
            continue
        entry: dict = {"size": st.st_size, "mtime": int(st.st_mtime)}
        try:
            if st.st_size > MAX_FILE_BYTES:
                raise ValueError(f"over the {MAX_FILE_BYTES // 2**20} MB limit")
            if f.suffix.lower() in RIG_EXTS:
                from lightai.mine.formats3d import decode_mvr

                entry["summary"] = summarize_rig(decode_mvr(f), f.stem)
            else:
                entry["summary"] = summarize(decode_file(f))
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the index
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        files[rel] = entry
    index = {"version": INDEX_VERSION, "built": time.time(), "files": files}
    if root.exists():
        path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return index


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def _pace(s: dict) -> Optional[str]:
    avg, energy = s.get("avg_cue_s"), s.get("energy_mean")
    if avg is None and energy is None:
        return None
    if (avg is not None and avg >= 4.0) or (energy is not None and energy <= 0.3):
        return "slow"
    if (avg is not None and avg <= 1.5) or (energy is not None and energy >= 0.65):
        return "fast"
    return "mid"


def score(summary: dict, text: str, file_name: str = "") -> float:
    """How useful a reference is for a request: shared words (title, song, file name, effect names), mood pace and
    palette fit, and the share of effects that match the effect words asked for."""
    if summary.get("kind") != "show":
        return 0.0
    words = _words(text)
    song = summary.get("song") or {}
    own = _words(" ".join([summary.get("title") or "", song.get("artist") or "", song.get("title") or "", Path(file_name).stem]
                          + [e for e, _ in summary.get("effects") or []]))
    total = 2.0 * len(words & own)
    moods = [MOODS[w] for w in words if w in MOODS]
    pace = _pace(summary)
    for want_pace, wants_pastel in moods:
        if pace == want_pace:
            total += 1.5
        elif pace and {pace, want_pace} == {"slow", "fast"}:
            total -= 1.0
        if wants_pastel:
            total += summary.get("pastel_share") or 0.0
    effect_names = [(e.lower(), share) for e, share in summary.get("effects") or []]
    for key in {EFFECT_WORDS[w] for w in words if w in EFFECT_WORDS}:
        total += 2.0 * sum(share for e, share in effect_names if any(sub in e for sub in EFFECTS[key]))
    return round(total, 3)


def query_references(cfg, text: str, k: int = 3, *, index: Optional[dict] = None) -> list:
    """The k most useful decoded shows for a request (only those with a positive score)."""
    idx = index if index is not None else build_index(cfg)
    ranked = []
    for rel, entry in (idx.get("files") or {}).items():
        s = entry.get("summary")
        if not s:
            continue
        sc = score(s, text, rel)
        if sc > 0:
            ranked.append(dict(s, file=rel, score=sc))
    ranked.sort(key=lambda r: (-r["score"], r["file"]))
    return ranked[:k]


def _mmss(seconds: Optional[float]) -> str:
    if not seconds:
        return "?"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def describe(r: dict) -> str:
    """One line of facts for the designer."""
    song = r.get("song") or {}
    who = f" - {song['artist']}" if song.get("artist") else ""
    head = f'"{r["title"]}"{who} ({r["format"]}, {str(int(r["bpm"])) + " BPM, " if r.get("bpm") else ""}{_mmss(r.get("duration_s"))})'
    bits = []
    named = [s for s in r.get("sections") or [] if s.get("name")]
    if named:
        bits.append("sections " + " > ".join(f"{s['name']}({s['energy']:.1f})" for s in named[:8]))
    if r.get("energy_shape"):
        bits.append(f"energy across the show {r['energy_shape']} (0-9)")
    if r.get("cues_per_min") is not None:
        bits.append(f"{r['cues_per_min']:g} cues/min, avg cue {r.get('avg_cue_s') or 0:g} s")
    if r.get("effects"):
        bits.append("effects " + ", ".join(f"{e} {int(round(100 * sh))}%" for e, sh in r["effects"][:5]))
    if r.get("palette"):
        bits.append("palette " + "/".join(r["palette"][:5]) + (f" ({int(round(100 * r['pastel_share']))}% pastel)" if r.get("pastel_share") else ""))
    if r.get("moves"):
        bits.append("moves " + ", ".join(r["moves"][:3]))
    return f"- {head}: " + "; ".join(bits)


def references_brief(cfg, text: str, k: int = 3) -> str:
    """The designer-briefing section for a request, or '' when nothing in the library fits (or there is none)."""
    try:
        refs = query_references(cfg, text, k)
    except Exception:  # noqa: BLE001 - the library is an extra; a design never fails because of it
        return ""
    if not refs:
        return ""
    lines = ["Decoded from shows other designers made in other programs (the operator's private library). Borrow "
             "their structure, pacing and energy arc; don't copy them, and use only this rig's fixtures and recipes."]
    return "\n".join(lines + [describe(r) for r in refs])


def refs_main(args) -> int:
    """`lightai refs index | list | query <text>`"""
    from lightai.config import load_config

    cfg = load_config()
    root = references_dir(cfg)
    if args.action == "index":
        idx = build_index(cfg, force=getattr(args, "force", False))
        ok = sum(1 for e in idx["files"].values() if "summary" in e)
        bad = {k: e["error"] for k, e in idx["files"].items() if "error" in e}
        print(f"{root}: {ok} decoded, {len(bad)} failed")
        for k, e in bad.items():
            print(f"  FAIL {k}: {e}")
        return 0
    if args.action == "list":
        idx = build_index(cfg)
        for rel, e in idx["files"].items():
            s = e.get("summary")
            print(f"{rel}: " + (describe(s)[2:] if s and s.get("kind") == "show" else json.dumps(s or {"error": e.get("error")})[:200]))
        if not idx["files"]:
            print(f"no references yet: put decodable files ({', '.join(_decodable_exts())}) in {root}")
        return 0
    refs = query_references(cfg, " ".join(args.text), k=args.k)
    for r in refs:
        print(f"{r['score']:6.2f}  {describe(r)[2:]}")
    if not refs:
        print("no matching references")
    return 0

