"""The fixtures you have, so "add a swarm", "add 3 american dj pars", "thin pars at 50%" or "add a betopper" mean your
fixtures, not one of 30 library models.

Sources, strongest first: the show lightai edits and the main show, the other shows in the same folder, then your own
fixture definitions (the repo's Fixtures folder and the QLC+ user folder), even when no show uses them yet. Autosaves
and backups are skipped; every file is parsed once per change (cached on its size and modified time).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

_FILE_CACHE: dict = {}
_QXF_CACHE: dict = {}

FILLER = {"a", "an", "the", "new", "more", "extra", "another", "of", "fixture", "fixtures", "light", "lights", "unit", "units",
          "lamp", "lamps", "type", "types", "mode", "one", "ones", "some", "my", "our", "same", "kind", "like", "those", "these"}
MAKER_ALIASES = {"adj": "american dj"}
# unambiguous words only: 'par', 'wash', 'spot' and 'beam' are matched by model and fixture names, never by type
TYPE_WORDS = {
    "movinghead": ("moving head",), "mover": ("moving head",), "head": ("moving head",), "scanner": ("scanner",),
    "strobe": ("strobe",), "laser": ("laser",), "fog": ("smoke", "hazer"), "smoke": ("smoke",), "haze": ("hazer",),
    "hazer": ("hazer",), "dimmer": ("dimmer",), "bar": ("led bar",),
}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def singular(w: str) -> str:
    if w.endswith(("us", "is", "ss")):  # 'plus', 'iris', 'bass'
        return w
    if len(w) > 3 and w.endswith("es") and w[:-2].endswith(("sh", "ch", "x", "ss")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    if len(w) == 3 and w.endswith("s") and w[1] not in "aeiouy":  # 'fxs' -> 'fx'; 'gas' stays
        return w[:-1]
    return w


def _tokens(s: str) -> list:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()


def other_shows(project_path: Path) -> list:
    """The other .qxw files next to the show lightai edits (no autosaves; backups live in a subfolder)."""
    main = Path(project_path).resolve()
    return [p for p in sorted(Path(project_path).parent.glob("*.qxw"))
            if ".autosave." not in p.name.lower() and p.resolve() != main]


def _cached(cache: dict, path: Path, read):
    try:
        st = path.stat()
    except OSError:
        return None
    key = (st.st_size, st.st_mtime_ns)
    hit = cache.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    try:
        value = read(path)
    except Exception:  # a broken or half-written file must never break parsing
        value = None
    cache[str(path)] = (key, value)
    return value


def _file_fixtures(path: Path) -> list:
    """(manufacturer, model, mode, name) of every fixture in a show file."""
    from lightai.rig.qxw import Workspace

    return _cached(_FILE_CACHE, path, lambda p: [(f.manufacturer, f.model, f.mode or "", f.name) for f in Workspace.load(p).fixtures()]) or []


def _qxf_names(path: Path):
    """(manufacturer, model, type) of a fixture definition file."""
    from lxml import etree

    def read(p):
        r = etree.parse(str(p)).getroot()
        names = tuple((r.findtext("{*}" + t) or "").strip() for t in ("Manufacturer", "Model", "Type"))
        return names if names[0] and names[1] else None
    return _cached(_QXF_CACHE, path, read)


def local_definitions(cfg) -> list:
    """(manufacturer, model, type, file name) of your own fixture definitions."""
    out, seen = [], set()
    for d in getattr(cfg, "local_fixture_dirs", None) or []:
        for p in sorted(Path(d).glob("*.qxf")):
            names = _qxf_names(p)
            if names and (norm(names[0]), norm(names[1])) not in seen:
                seen.add((norm(names[0]), norm(names[1])))
                out.append((*names, p.name))
    return out


def house_models(rig, only_rig: bool = False) -> list:
    """Your fixture models, most used first: the show lightai edits and the main show decide, the other shows break
    ties, your own unpatched definitions come last. only_rig: just the fixtures of the show lightai edits."""
    entries: dict = {}
    main_path = Path(rig.cfg.project_path)

    def entry(manufacturer: str, model: str) -> dict:
        return entries.setdefault((norm(manufacturer), norm(model)), {
            "manufacturer": manufacturer, "model": model, "main": 0, "other": 0, "shows": Counter(), "names": [],
            "modes": Counter(), "ids": [], "local": None, "type": ""})

    def add(show: str, manufacturer: str, model: str, mode: str, name: str, main: bool, fid=None) -> None:
        e = entry(manufacturer, model)
        e["main" if main else "other"] += 1
        e["shows"][show] += 1
        if name and name not in e["names"] and len(e["names"]) < 12:
            e["names"].append(name)
        if mode:
            e["modes"][mode] += 3 if main else 1
        if fid is not None:
            e["ids"].append(fid)

    for f in rig.fixtures.values():
        add(main_path.name, f.manufacturer, f.model, getattr(f, "mode", "") or "", f.name, True, f.id)
    if not only_rig:
        ref = Path(rig.cfg.main_show()) if hasattr(rig.cfg, "main_show") else main_path
        for p in other_shows(main_path):
            is_ref = p.resolve() == ref.resolve()  # editing another show: the main show still counts first
            for manufacturer, model, mode, name in _file_fixtures(p):
                add(p.name, manufacturer, model, mode, name, is_ref)
        for manufacturer, model, _type, fname in local_definitions(rig.cfg):
            entry(manufacturer, model)["local"] = fname
    out = []
    for e in entries.values():
        fd = rig.library.get(e["manufacturer"], e["model"])
        if (fd is None or not fd.modes) and not only_rig:  # no definition: it can't be patched from here
            continue
        e["type"] = ((fd.type if fd else "") or "").lower()
        out.append(e)
    out.sort(key=lambda e: (-e["main"], -e["other"], e["local"] is None, e["model"].lower()))
    return out


def phrase_words(raw: str) -> list:
    t = raw.lower()
    for k, v in MAKER_ALIASES.items():
        t = re.sub(r"\b" + re.escape(k) + r"\b", v, t)
    t = re.sub(r"\bmoving\s+heads?\b", "movinghead", t)
    return [singular(w) for w in _tokens(t) if w not in FILLER]


def match_house_models(rig, raw: str, house: list = None, need_model_word: bool = False, zones: bool = True) -> list:
    """Your models that explain every word of the phrase, best first: [(points, entry)].

    The maker's name explains a word (no points). The model name explains a word (3), or the whole phrase when its
    letters and digits run together ('l 1015', 'thin par', 'beam230 v2'; 3 more when it is the full model name). The names
    you gave the fixtures (2), a zone or alias that resolves to them (2) or an unambiguous fixture type (1) explain a
    word too. With need_model_word the phrase must name the maker or the model: 'add fog' (an alias) is fog on, not a
    new fog machine."""
    words = phrase_words(raw)
    if not words:
        return []
    house = house_models(rig) if house is None else house
    zone_ids: dict = {}
    if zones:
        from lightai.nlu.normalize import normalize_slot

        for w in set(words):
            if w.isalpha() and len(w) >= 3:  # never read a model number as a fixture ID
                try:
                    zone_ids[w] = set(normalize_slot(rig, "target", w).get("fixture_ids") or [])
                except Exception:
                    zone_ids[w] = set()
    out = []
    for e in house:
        maker = {f(t) for t in _tokens(e["manufacturer"]) for f in (str, singular)}  # 'mayans' is said 'mayans' or read 'mayan'
        model_tokens, model_joined = {f(t) for t in _tokens(e["model"]) for f in (str, singular)}, norm(e["model"])
        name_tokens = {singular(w) for n in e["names"] for w in _tokens(n)}
        names_joined = norm(" ".join(e["names"]))
        rest = [w for w in words if w not in maker]
        said = len(rest) < len(words)
        joined = "".join(rest)
        named = False
        if rest and (joined == model_joined or (len(joined) >= 3 and joined in model_joined)):
            pts, named = 3 * len(rest) + (3 if joined == model_joined else 0), True
        else:
            pts, ok = 0, True
            for w in rest:
                if w in model_tokens or (not w.isdigit() and len(w) >= 2 and w in model_joined):
                    pts += 3
                    named = True
                elif not w.isdigit() and (w in name_tokens or (len(w) >= 4 and w in names_joined)):  # '2' in 'BEAM230 #2' is a number
                    pts += 2
                elif zone_ids.get(w) and zone_ids[w] & set(e["ids"]):
                    pts += 2
                elif any(t in e["type"] for t in TYPE_WORDS.get(w, ())):
                    pts += 1
                else:
                    ok = False
                    break
            if not ok:
                continue
        if not (pts or said) or (need_model_word and not (named or said)):
            continue
        out.append((pts, e))
    out.sort(key=lambda x: (-x[0], -x[1]["main"], -x[1]["other"], x[1]["local"] is None))
    return out


def assume_house_model(rig, raw: str, val: dict) -> dict:
    """The model you mean: one of yours when your fixtures explain every word ('american dj par' -> your VPar, 'beam v2'
    -> your BEAM230V2 rather than a library 'Intimidator Beam 140SR V2'). A library model you name in full that isn't
    one of yours stays. When nothing tells two of yours apart ('betopper'), it asks and names all of them."""
    hits = match_house_models(rig, raw)
    if not hits:
        return val
    mine = {(norm(e["manufacturer"]), norm(e["model"])) for _, e in hits}
    if not (val.get("ambiguous") or val.get("unresolved")) and (val.get("score") or 0) >= 1.2 \
            and (norm(val.get("manufacturer", "")), norm(val.get("model", ""))) not in mine:
        return val
    best = hits[0][0]
    top = [e for p, e in hits if p == best]
    tied = [e for e in top if (e["main"], e["other"]) == (top[0]["main"], top[0]["other"])]
    if best == 0 and len(top) > 1:  # only the maker's name ('add a mayans'): ask which of its fixtures
        tied = top
    if len(tied) > 1:
        cands = [{"manufacturer": e["manufacturer"], "model": e["model"], "score": 1.0} for e in tied]
        return {"manufacturer": tied[0]["manufacturer"], "model": tied[0]["model"], "score": 1.0, "candidates": cands,
                "ambiguous": True, "yours": True}
    e = top[0]
    if e["shows"]:
        first = {Path(rig.cfg.project_path).name}
        if hasattr(rig.cfg, "main_show"):
            first.add(Path(rig.cfg.main_show()).name)
        where = ", ".join(f"{n} in {s}" for s, n in e["shows"].items() if s in first) \
            or ", ".join(f"{n} in {s}" for s, n in e["shows"].most_common(2))
        named = (": " + ", ".join(e["names"][:3]) + (", ..." if len(e["names"]) > 3 else "")) if e["names"] else ""
        note = f"the '{raw}' your shows use ({where}{named})"
    else:
        note = f"your own fixture definition {e['local']}, not patched in any show yet"
    return {"manufacturer": e["manufacturer"], "model": e["model"], "score": 1.0, "assumed": True, "note": note,
            "house_mode": e["modes"].most_common(1)[0][0] if e["modes"] else None,
            "alternatives": [{"manufacturer": x["manufacturer"], "model": x["model"], "main": x["main"], "other": x["other"]}
                             for _, x in hits if x is not e][:5]}
