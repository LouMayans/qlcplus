"""Research runner: sends a lighting-design question to Claude (web search/fetch, plus a cheap
Haiku research subagent), then writes a readable report under lightai/knowledge/research/ and
merges any mood entries it proposes into lightai/knowledge/moods.yaml - additively only, and only
after checking every recipe/order/color name against what lightai can actually build.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import Annotated, Callable, Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError

from lightai.compiler.recipes import RECIPES
from lightai.config import Config, LIGHTAI_DIR, load_config
from lightai.rig.model import ColorBook
from lightai.rig.stage import ORDERS

KB_README = ".claude/memory/lighting/README.md"
KNOWLEDGE_DIR = LIGHTAI_DIR / "knowledge"
RESEARCH_TOOLS = ("Read", "Glob", "Grep", "WebSearch", "WebFetch")
ReferenceKind = Literal["article", "video", "show_file", "manual", "forum", "other"]
FadeMs = Annotated[int, Field(ge=0, le=20000)]
Intensity = Annotated[float, Field(ge=0.0, le=1.0)]


class Finding(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    detail: str = Field("", max_length=2000)
    sources: list[str] = Field(default_factory=list, max_length=8)


class ResearchMood(BaseModel):
    """One mood proposal, in the knowledge/moods.yaml shape (see that file), plus its name."""
    name: str = Field(min_length=1, max_length=40)
    words: list[str] = Field(default_factory=list, max_length=12)
    recipes: list[str] = Field(default_factory=list, max_length=8)
    avoid: list[str] = Field(default_factory=list, max_length=8)
    rate: list[str] = Field(default_factory=list, max_length=2)
    fade_ms: list[FadeMs] = Field(default_factory=list, max_length=2)
    palette: list[str] = Field(default_factory=list, max_length=8)
    intensity: list[Intensity] = Field(default_factory=list, max_length=2)
    movement_size: list[str] = Field(default_factory=list, max_length=2)
    strobe: Optional[str] = None
    orders: list[str] = Field(default_factory=list, max_length=4)
    notes: str = Field("", max_length=400)


class Term(BaseModel):
    word: str = Field(min_length=1, max_length=60)
    meaning: str = Field(min_length=1, max_length=400)


class Reference(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    url: str = Field("", max_length=500)
    kind: ReferenceKind = "other"


class ResearchResult(BaseModel):
    topic: str = Field(min_length=1, max_length=140)
    summary: str = Field("", max_length=3000)
    findings: list[Finding] = Field(default_factory=list, max_length=10)
    moods: list[ResearchMood] = Field(default_factory=list, max_length=6)
    terms: list[Term] = Field(default_factory=list, max_length=20)
    references: list[Reference] = Field(default_factory=list, max_length=20)


def research_schema() -> dict:
    """The JSON schema passed to claude.exe --json-schema."""
    return ResearchResult.model_json_schema()


EXAMPLE = {
    "topic": "afrobeats club lighting",
    "summary": "Afrobeats sets favor warm, saturated colour over harsh strobing, with chases timed to the clave.",
    "findings": [
        {"title": "Warm palette dominates", "detail": "Amber/orange/red pars are used as the base wash, with cooler "
         "accents reserved for breakdowns.", "sources": ["https://example.com/afrobeats-lighting"]}],
    "moods": [
        {"name": "afro_groove", "words": ["afrobeats", "afro", "amapiano"], "recipes": ["color_chase", "running_light"],
         "avoid": ["strobe"], "rate": ["medium", "fast"], "fade_ms": [800, 2000], "palette": ["amber", "orange", "lime"],
         "intensity": [0.6, 0.9], "movement_size": ["medium", "big"], "strobe": "accents",
         "orders": ["left_to_right", "center_out"], "notes": "Warm, groove-locked chases; save strobe for drops."}],
    "terms": [{"word": "clave", "meaning": "a recurring rhythmic pattern used to time chases and hits"}],
    "references": [{"title": "Afrobeats Lighting Design Breakdown", "url": "https://example.com/afrobeats-lighting",
                    "kind": "article"}],
}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "topic"


def researcher_agent() -> dict:
    """The --agents JSON: a cheap Haiku subagent the main run can dispatch web look-ups to. Keys
    are the ones claude.exe's --agents accepts (confirmed against `claude.exe --help` and the
    CLI's own agent-definition field list: description/prompt/tools/model, among others)."""
    return {
        "researcher": {
            "description": "Searches the web and fetches pages for one lighting-design fact or source",
            "prompt": ("You help research professional nightclub/EDM lighting design. Search the web and fetch "
                       "pages, then report back concrete facts with their exact source URLs. Be concise."),
            "tools": ["WebSearch", "WebFetch"],
            "model": "haiku",
        }
    }


def _load_colorbook(cfg: Config) -> ColorBook:
    """The rig's color book, loaded directly from cfg.colors_path (no project/show file needed -
    this is the same lightweight load lightai.mine uses)."""
    try:
        doc = yaml.safe_load(Path(cfg.colors_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        doc = {}
    return ColorBook(doc)


def _known_colors(cfg: Config) -> list[str]:
    """Best-effort color vocabulary for the brief; an unreadable colors file just yields an empty list."""
    return _load_colorbook(cfg).names()


def _rig_summary(ai_or_cfg) -> str:
    """Optional rig context for the brief: research is useful even without a live show, so a
    missing or unloadable rig is silently skipped rather than failing the run."""
    get_rig = getattr(ai_or_cfg, "get_rig", None)
    if not callable(get_rig):
        return ""
    try:
        rig = get_rig()
    except Exception:
        return ""
    if rig is None:
        return ""
    from lightai.design.prompt import rig_summary

    try:
        return rig_summary(rig)
    except Exception:
        return ""


def build_brief(ai_or_cfg, cfg: Config, topic: str) -> str:
    """The append-system-prompt brief: role, rules, the allowed mood vocabulary, and an example -
    the same shape prompt.py uses for the show designer (see build_context there)."""
    colors = _known_colors(cfg)
    parts = [
        "# You research professional lighting design for a nightclub running QLC+.",
        "Your answer is a ResearchResult JSON (the schema is enforced); lightai writes a report from it and may "
        "merge any moods you propose into its mood vocabulary. You never write files or edit the show.",
        "",
        "## Rules",
        f"- Read the knowledge-base README first with the Read tool: {KB_README} (it indexes the rest; follow its "
        "links for whatever is relevant to this topic).",
        "- Use the web (WebSearch/WebFetch, and the 'researcher' subagent for extra look-ups) and cite sources: "
        "every finding needs real source URLs, not guesses.",
        "- Only propose entries in `moods` when the topic is itself a style/genre/vibe - not for a general or "
        "technical question. Each mood's `recipes`, `avoid` and `orders` must be recipe/order names from the list "
        "below, and `palette` must be color names from the list below; anything else is dropped before it reaches "
        "the mood file, so prefer leaving a field empty over guessing.",
        "- List useful `references` for later use: show files from other lighting programs, manuals, videos, forum "
        "threads, and 3D visualization files (MVR/GDTF) are all welcome when relevant.",
        "- Put any lighting jargon you had to look up yourself in `terms`.",
        "",
        "## Allowed vocabulary for moods",
        "Recipes (for `recipes`/`avoid`): " + ", ".join(sorted(RECIPES)),
        "Orders (for `orders`): " + ", ".join(ORDERS),
        "Colors (for `palette`): " + (", ".join(colors) if colors else "(none loaded)"),
        "Rate words: very_slow, slow, medium, fast, very_fast. Movement sizes: tiny, small, medium, big, huge. "
        "Strobe: none, accents, bursts, or constant.",
        "",
    ]
    rig_ctx = _rig_summary(ai_or_cfg)
    if rig_ctx:
        parts += ["## The rig (optional context; this research need not be rig-specific)", rig_ctx, ""]
    parts += ["## Example of a valid answer (shape only; research your own)", json.dumps(EXAMPLE, indent=1)]
    return "\n".join(parts)


def write_brief(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _dedupe_lower(words: list) -> list:
    out: list = []
    for w in words:
        w = str(w).strip()
        if w and w.lower() not in {x.lower() for x in out}:
            out.append(w.lower())
    return out


def _check_names(mood_name: str, names: list, known: set, label: str, dropped: list) -> list:
    keep, bad = [], []
    for n in names:
        if n in known:
            keep.append(n)
        else:
            bad.append(n)
    if bad:
        dropped.append(f"mood '{mood_name}': unknown {label} {', '.join(sorted(set(bad)))}")
    return keep


def _check_colors(mood_name: str, names: list, colors: ColorBook, dropped: list) -> list:
    keep, bad = [], []
    for n in names:
        c = colors.canonical(n)
        if c is None:
            bad.append(n)
        elif c not in keep:
            keep.append(c)
    if bad:
        dropped.append(f"mood '{mood_name}': unknown color(s) {', '.join(sorted(set(bad)))}")
    return keep


def _clean_mood(mood: ResearchMood, colors: ColorBook, dropped: list) -> dict:
    """A new mood's fields, with every recipe/order/color name checked against what lightai can
    actually build; invalid names are dropped (and noted in `dropped`) rather than failing the run."""
    recipes = set(RECIPES)
    orders = set(ORDERS)
    return {
        "words": _dedupe_lower(mood.words),
        "recipes": _check_names(mood.name, mood.recipes, recipes, "recipe(s)", dropped),
        "avoid": _check_names(mood.name, mood.avoid, recipes, "recipe(s) in avoid", dropped),
        "rate": list(mood.rate),
        "fade_ms": list(mood.fade_ms),
        "palette": _check_colors(mood.name, mood.palette, colors, dropped),
        "intensity": list(mood.intensity),
        "movement_size": list(mood.movement_size),
        "strobe": mood.strobe or "none",
        "orders": _check_names(mood.name, mood.orders, orders, "order(s)", dropped),
        "notes": mood.notes,
    }


def backup_moods(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    return backup


def merge_moods(moods: list, moods_path: Path, cfg: Config) -> tuple:
    """Add new moods and, for existing ones, only ADD new words/recipes/colors - never delete or
    overwrite anything already there. Returns (added, extended, dropped); moods_path is backed up
    first, and only when something actually changes."""
    colors = _load_colorbook(cfg)
    doc: dict = {"version": 1, "moods": {}}
    if moods_path.exists():
        loaded = yaml.safe_load(moods_path.read_text(encoding="utf-8")) or {}
        doc = loaded if isinstance(loaded, dict) else doc
        doc.setdefault("moods", {})

    added, extended, dropped, changed = [], [], [], False
    for mood in moods:
        name = re.sub(r"\s+", "_", mood.name.strip().lower())
        if not name:
            continue
        clean = _clean_mood(mood, colors, dropped)
        if name not in doc["moods"]:
            doc["moods"][name] = clean
            added.append(name)
            changed = True
            continue
        existing = doc["moods"][name]
        grew = False
        for field in ("words", "recipes", "palette"):
            had = list(existing.get(field) or [])
            fresh = [v for v in clean.get(field, []) if v not in had]
            if fresh:
                existing[field] = had + fresh
                grew = True
        if grew:
            extended.append(name)
            changed = True

    if changed:
        backup_moods(moods_path)
        moods_path.parent.mkdir(parents=True, exist_ok=True)
        moods_path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return added, extended, dropped


def render_report(result: ResearchResult, moods_added: list, moods_extended: list, dropped: list) -> str:
    lines = [f"# Research: {result.topic}", "", (result.summary or "").strip() or "*(no summary given)*", ""]
    if result.findings:
        lines.append("## Findings")
        for i, f in enumerate(result.findings, 1):
            lines.append(f"### {i}. {f.title}")
            if f.detail:
                lines.append(f.detail.strip())
            if f.sources:
                lines += ["", "Sources:"] + [f"{j}. <{u}>" for j, u in enumerate(f.sources, 1)]
            lines.append("")
    if moods_added or moods_extended or dropped:
        lines.append("## Moods")
        lines += [f"- added: {n}" for n in moods_added]
        lines += [f"- extended: {n}" for n in moods_extended]
        lines += [f"- dropped: {d}" for d in dropped]
        lines.append("")
    if result.terms:
        lines.append("## Terms")
        lines += [f"- **{t.word}** -- {t.meaning}" for t in result.terms]
        lines.append("")
    if result.references:
        lines.append("## References")
        for r in result.references:
            link = f"[{r.title}]({r.url})" if r.url else r.title
            lines.append(f"- {link} ({r.kind})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(result: ResearchResult, out_dir: Path, moods_added: list, moods_extended: list, dropped: list,
                 slug: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{time.strftime('%Y-%m-%d')}-{slug}.md"
    path.write_text(render_report(result, moods_added, moods_extended, dropped), encoding="utf-8")
    return path


async def research(ai_or_cfg, topic: str, backend, *, deep: bool = False,
                   on_event: Optional[Callable[[dict], None]] = None, knowledge_dir: Optional[Path] = None) -> dict:
    """Ask Claude to research a lighting-design topic, then write a report and merge any moods it
    proposes into moods.yaml. ai_or_cfg is a LightAI instance (used for an optional rig summary
    and its .cfg) or a bare Config; knowledge_dir (or cfg.knowledge_dir) overrides lightai/knowledge, for tests."""
    cfg: Config = getattr(ai_or_cfg, "cfg", ai_or_cfg)
    kdir = Path(knowledge_dir) if knowledge_dir is not None else Path(getattr(cfg, "knowledge_dir", None) or KNOWLEDGE_DIR)
    topic = topic.strip()
    slug = _slug(topic)
    brief = build_brief(ai_or_cfg, cfg, topic)
    brief_path = write_brief(Path(cfg.data_dir) / "research_ctx" / f"{slug}.md", brief)
    model = "opus" if deep else getattr(cfg, "claude_model_research", "sonnet")
    prompt = f"Research this lighting-design topic for the operator: {topic}"

    res = await backend.run(prompt, kind="research", model=model, schema=research_schema(), system_file=brief_path,
                            allowed_tools=RESEARCH_TOOLS, agents=researcher_agent(),
                            timeout_s=float(getattr(cfg, "research_timeout_s", 900.0)),
                            budget_usd=getattr(cfg, "claude_budget_research_usd", None), on_event=on_event)
    if not res.ok:
        return {"ok": False, "error": res.error or "Claude run failed", "run_id": res.run_id}
    try:
        result = ResearchResult.model_validate(res.data or {})
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:10])
        return {"ok": False, "error": f"invalid ResearchResult: {errs}", "run_id": res.run_id}

    moods_added, moods_extended, dropped = merge_moods(result.moods, kdir / "moods.yaml", cfg)
    report_path = write_report(result, kdir / "research", moods_added, moods_extended, dropped, slug)
    return {"ok": True, "report_path": str(report_path), "moods_added": moods_added,
            "moods_extended": moods_extended, "dropped": dropped, "run_id": res.run_id}


# ---------------------------------------------------------------------------
# CLI: `python -m lightai research "<topic>" [--deep]`
# ---------------------------------------------------------------------------

def _event_line(event: dict) -> Optional[str]:
    """A short progress line for one stream event (searches, pages read) - the CLI counterpart of
    design/jobs.py's _event_step, but printed directly rather than kept on a job."""
    if event.get("type") != "assistant":
        return None
    for block in (event.get("message") or {}).get("content") or []:
        if block.get("type") != "tool_use":
            continue
        name, inp = block.get("name"), block.get("input") or {}
        if name == "WebSearch":
            return f"searching: {inp.get('query', '')}"
        if name == "WebFetch":
            return f"reading: {inp.get('url', '')}"
        if name in ("Read", "Glob", "Grep"):
            return f"reading: {inp.get('file_path') or inp.get('pattern') or inp.get('path') or ''}"
        return f"using {name}"
    return None


def research_main(args) -> int:
    from lightai.design.backend import ClaudeCodeBackend
    from lightai.design.runlog import RunLog

    cfg = load_config()
    backend = ClaudeCodeBackend(cfg, RunLog(cfg.data_dir))

    def on_event(event: dict) -> None:
        line = _event_line(event)
        if line:
            print(line)

    result = asyncio.run(research(cfg, args.topic, backend, deep=args.deep, on_event=on_event))
    if not result["ok"]:
        print(f"research failed: {result['error']}")
        return 1
    print(f"report: {result['report_path']}")
    if result["moods_added"]:
        print("moods added: " + ", ".join(result["moods_added"]))
    if result["moods_extended"]:
        print("moods extended: " + ", ".join(result["moods_extended"]))
    if result["dropped"]:
        print("dropped (not in lightai's vocabulary):")
        for d in result["dropped"]:
            print(f"  - {d}")
    print(f"run_id: {result['run_id']}")
    return 0
