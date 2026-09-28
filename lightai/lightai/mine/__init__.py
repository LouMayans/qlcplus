"""Learn from other people's QLC+ shows and from the fixture library.

  lightai mine local <folders...>   copy .qxw files into %LIGHTAI_DATA%\\mined\\raw (drop folder)
  lightai mine github --token T     download public .qxw files found by GitHub code search
  lightai mine stats                parse everything in mined\\raw -> lightai/knowledge/priors.yaml
  lightai mine taxonomy             summarize the fixture library -> %LIGHTAI_DATA%\\fixture-taxonomy.json
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import yaml

from lightai.config import PACKAGE_DIR, Config, load_config
from lightai.rig.model import ColorBook, Rig
from lightai.rig.qxf import FixtureLibrary, parse_qxf
from lightai.rig.qxw import Workspace

PRIORS_PATH = PACKAGE_DIR / "knowledge" / "priors.yaml"


def raw_dir(cfg: Config) -> Path:
    d = cfg.data_dir / "mined" / "raw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def foreign_rig(path: Path, cfg: Optional[Config] = None, library: Optional[FixtureLibrary] = None) -> Rig:
    cfg = copy.copy(cfg or load_config())
    cfg.roles_template = None
    colors = ColorBook(yaml.safe_load(cfg.colors_path.read_text(encoding="utf-8")) or {})
    return Rig(cfg, Workspace.load(path), {"zones": {}}, colors, library or FixtureLibrary(cfg.fixture_dirs))


def mine_local(cfg: Config, paths: list) -> int:
    dst = raw_dir(cfg)
    n = 0
    for p in paths:
        root = Path(p)
        files = [root] if root.is_file() else list(root.rglob("*.qxw"))
        for f in files:
            if f.suffix.lower() != ".qxw" or ".autosave" in f.name:
                continue
            data = f.read_bytes()
            h = hashlib.sha1(data).hexdigest()[:12]
            out = dst / f"local__{f.stem[:40].replace(' ', '_')}__{h}.qxw"
            if not out.exists():
                out.write_bytes(data)
                n += 1
                with open(dst.parent / "manifest.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"file": out.name, "source": str(f), "kind": "local"}) + "\n")
    print(f"added {n} show file(s) to {dst}")
    return 0


def mine_github(cfg: Config, token: Optional[str], limit: int) -> int:
    import httpx

    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GitHub code search needs a token: create a fine-grained token with no extra permissions at\n"
              "https://github.com/settings/tokens and run: lightai mine github --token <token>  (or set GITHUB_TOKEN)")
        return 2
    dst = raw_dir(cfg)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    got, page = 0, 1
    with httpx.Client(headers=headers, timeout=30) as http:
        while got < limit and page <= 10:
            r = http.get("https://api.github.com/search/code", params={"q": "Workspace extension:qxw", "per_page": 50, "page": page})
            if r.status_code == 403:
                wait = int(r.headers.get("retry-after", "60"))
                print(f"rate limited; waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                break
            for it in items:
                if got >= limit:
                    break
                name = f"gh__{it['repository']['full_name'].replace('/', '_')}__{hashlib.sha1(it['path'].encode()).hexdigest()[:10]}.qxw"
                out = dst / name
                if out.exists():
                    continue
                meta = http.get(it["url"]).json()
                dl = meta.get("download_url")
                if not dl:
                    continue
                body = http.get(dl).content
                if b"<Workspace" not in body[:4000]:
                    continue
                out.write_bytes(body)
                got += 1
                with open(dst.parent / "manifest.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"file": name, "source": it["html_url"], "repo": it["repository"]["full_name"], "kind": "github"}) + "\n")
                time.sleep(1.0)
            page += 1
            time.sleep(6.5)
    print(f"downloaded {got} show file(s) into {dst} (used only locally for statistics; not redistributed)")
    return 0


def _q(values: list) -> dict:
    if not values:
        return {}
    v = sorted(values)
    return {"n": len(v), "p25": v[len(v) // 4], "median": statistics.median(v), "p75": v[(3 * len(v)) // 4]}


def show_stats(rig: Rig) -> dict:
    st: dict = {"fixtures": len(rig.fixtures), "models": Counter(fx.key for fx in rig.fixtures.values()),
                "kinds": Counter(fx.kind for fx in rig.fixtures.values()), "types": Counter(f.type for f in rig.functions.values()),
                "efx": [], "chasers": [], "colors": Counter(), "name_words": Counter()}
    for f in rig.functions.values():
        for w in f.name.lower().replace("-", " ").split():
            if w.isalpha() and len(w) > 2:
                st["name_words"][w] += 1
    for el in rig.ws.function_els():
        t = el.get("Type")
        fid = int(el.get("ID", "-1"))
        f = rig.functions.get(fid)
        if t == "EFX":
            from lightai.rig.qxf import kid, kids, text_of

            offs = [int(text_of(fx, "StartOffset", "0") or 0) for fx in kids(el, "Fixture")]
            sp = kid(el, "Speed")
            st["efx"].append({"algorithm": text_of(el, "Algorithm"), "width": int(text_of(el, "Width", "0") or 0),
                              "height": int(text_of(el, "Height", "0") or 0),
                              "duration": int(sp.get("Duration", "0")) if sp is not None else 0,
                              "fixtures": len(offs), "spread": (max(offs) - min(offs)) if offs else 0})
        elif t == "Chaser":
            from lightai.rig.qxf import kid, kids

            sp = kid(el, "Speed")
            modes = kid(el, "SpeedModes")
            steps = kids(el, "Step")
            dur = int(sp.get("Duration", "0")) if sp is not None else 0
            if modes is not None and modes.get("Duration") == "PerStep" and steps:
                dur = int(statistics.median([int(s.get("Hold", "0")) + int(s.get("FadeIn", "0")) for s in steps]))
            st["chasers"].append({"steps": len(steps), "duration": dur, "fade_in": int(sp.get("FadeIn", "0")) if sp is not None else 0})
        elif t == "Scene" and f is not None:
            for fxid, pairs in f.values.items():
                fx = rig.fixtures.get(fxid)
                if fx is None:
                    continue
                vals = dict(pairs)
                if fx.color_mode == "rgb" and all(r in fx.roles for r in ("red", "green", "blue")):
                    rgb = tuple(vals.get(fx.roles[r], 0) for r in ("red", "green", "blue"))
                    if max(rgb) > 30:
                        name = rig.colors.nearest(rgb, rig.colors.names())
                        if name:
                            st["colors"][name] += 1
                elif fx.color_mode == "wheel" and "color_wheel" in fx.roles and fx.roles["color_wheel"] in vals:
                    inv = {v: c for c, v in (fx.caps.get("wheel") or {}).items()}
                    c = inv.get(vals[fx.roles["color_wheel"]])
                    if c:
                        st["colors"][c] += 1
    return st


def mine_stats(cfg: Config) -> int:
    files = sorted(raw_dir(cfg).glob("*.qxw"))
    if not files:
        print("no shows yet: run `lightai mine local <folder>` or `lightai mine github --token ...` first")
        return 2
    lib = FixtureLibrary(cfg.fixture_dirs)
    agg = {"shows": 0, "failed": 0, "models": Counter(), "kinds": Counter(), "types": Counter(), "colors": Counter(), "name_words": Counter()}
    efx, chasers = [], []
    for f in files:
        try:
            st = show_stats(foreign_rig(f, cfg, lib))
        except Exception as exc:
            agg["failed"] += 1
            print(f"  skip {f.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        agg["shows"] += 1
        for k in ("models", "kinds", "types", "colors", "name_words"):
            agg[k].update(st[k])
        efx += st["efx"]
        chasers += st["chasers"]
    by_alg = defaultdict(list)
    for e in efx:
        by_alg[e["algorithm"]].append(e)
    mayans = Rig.load(cfg)
    our_models = {fx.key for fx in mayans.fixtures.values()}
    our_kinds = Counter(fx.kind for fx in mayans.fixtures.values())
    priors = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "shows_parsed": agg["shows"],
        "shows_failed": agg["failed"],
        "efx": {alg: {"count": len(v), "width": _q([e["width"] for e in v]), "height": _q([e["height"] for e in v]),
                      "duration_ms": _q([e["duration"] for e in v if e["duration"]]), "phase_spread": _q([e["spread"] for e in v])}
                for alg, v in sorted(by_alg.items(), key=lambda kv: -len(kv[1]))},
        "chaser": {"count": len(chasers), "steps": _q([c["steps"] for c in chasers]), "step_ms": _q([c["duration"] for c in chasers if c["duration"]]),
                   "fade_in_ms": _q([c["fade_in"] for c in chasers])},
        "function_types": dict(agg["types"].most_common()),
        "palette": dict(agg["colors"].most_common(15)),
        "popular_models": dict(agg["models"].most_common(25)),
        "fixture_kinds": dict(agg["kinds"].most_common()),
        "gear_gap": {
            "popular_models_we_do_not_have": [m for m, _ in agg["models"].most_common(40) if m not in our_models and not m.startswith("Generic/")][:15],
            "kinds_share_elsewhere_vs_here": {k: {"elsewhere": round(v / max(1, sum(agg["kinds"].values())), 3),
                                                  "here": round(our_kinds.get(k, 0) / max(1, len(mayans.fixtures)), 3)}
                                              for k, v in agg["kinds"].most_common()},
        },
        "common_name_words": [w for w, _ in agg["name_words"].most_common(40)],
    }
    PRIORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRIORS_PATH.write_text("# Generated by `lightai mine stats` from shows in %LIGHTAI_DATA%\\mined\\raw\n" + yaml.safe_dump(priors, sort_keys=False), encoding="utf-8")
    print(f"parsed {agg['shows']} show(s) ({agg['failed']} failed) -> {PRIORS_PATH}")
    return 0


def mine_taxonomy(cfg: Config) -> int:
    root = Path(cfg.fixture_dirs[-1])
    by_type: dict = defaultdict(lambda: {"count": 0, "channels": [], "roles": Counter(), "manufacturers": Counter()})
    from lightai.rig.roles import channel_candidates, pick_roles

    n = 0
    for f in root.rglob("*.qxf"):
        try:
            fd = parse_qxf(f)
        except Exception:
            continue
        n += 1
        t = by_type[fd.type]
        t["count"] += 1
        t["manufacturers"][fd.manufacturer] += 1
        for m in list(fd.modes.values())[:1]:
            cds = fd.mode_channels(m.name)
            t["channels"].append(len(cds))
            roles, _, _ = pick_roles({i: channel_candidates(cd) for i, cd in enumerate(cds)})
            t["roles"].update(roles.keys())
    out = {typ: {"count": v["count"], "channels": _q(v["channels"]),
                 "typical_roles": [r for r, c in v["roles"].most_common(12) if c >= 0.3 * v["count"]],
                 "top_manufacturers": [m for m, _ in v["manufacturers"].most_common(8)]}
           for typ, v in sorted(by_type.items(), key=lambda kv: -kv[1]["count"])}
    path = cfg.data_dir / "fixture-taxonomy.json"
    path.write_text(json.dumps({"definitions": n, "types": out}, indent=2), encoding="utf-8")
    print(f"{n} fixture definitions summarized -> {path}")
    for typ, v in list(out.items())[:13]:
        print(f"  {typ:<20} {v['count']:>5}  median {v['channels'].get('median')} ch  roles: {', '.join(v['typical_roles'][:8])}")
    return 0


def mine_main(args) -> int:
    cfg = load_config()
    if args.source == "local":
        return mine_local(cfg, args.paths or [str(cfg.project_path.parent)])
    if args.source == "github":
        return mine_github(cfg, args.token, args.max)
    if args.source == "stats":
        return mine_stats(cfg)
    if args.source == "taxonomy":
        return mine_taxonomy(cfg)
    return 2
