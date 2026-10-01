"""Curator: deterministic scoring + 3-layer dedup + LLM editorial brief."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pipeline.config import Settings
from pipeline.embeddings import embed_text
from pipeline.llm import LLM
from pipeline.log import get_logger
from pipeline.memory import HeadlineMemory, title_overlap, title_words

log = get_logger(stage="curator")

NAMESPACE = "headlines"

BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "time_allocation": {"type": "integer", "enum": [60, 90, 120]},
                    "comedy_angle": {"type": ["string", "null"]},
                },
                "required": ["title", "time_allocation", "comedy_angle"],
                "additionalProperties": False,
            },
        },
        "deep_dives": {"type": "array", "items": {"type": "string"}},
        "cold_open_idea": {"type": "string"},
        "story_count": {"type": "integer"},
    },
    "required": ["stories", "deep_dives", "cold_open_idea", "story_count"],
    "additionalProperties": False,
}


def _age_hours(story: dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    if not story.get("published_at"):
        return None
    try:
        pub = datetime.fromisoformat(str(story["published_at"]).replace("Z", "+00:00"))
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return ((now or datetime.now(timezone.utc)) - pub).total_seconds() / 3600
    except Exception:
        return None


def prescore(story: dict[str, Any], now: Optional[datetime] = None) -> int:
    """Recency + tier only (used before coverage is known)."""
    score = 0
    age = _age_hours(story, now)
    if age is not None:
        if age <= 12:
            score += 3
        elif age <= 24:
            score += 2
        elif age <= 36:
            score += 1
    tier = str(story.get("source_tier", "2"))
    if tier == "0":
        score += 3
    elif tier == "1":
        score += 1
    return score


def score_story(story: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    """Recency + tier + cross-coverage. Same rubric as the original `_score_story`."""
    score = prescore(story, now)
    coverage = int(story.get("coverage_count", 0) or 0)
    if coverage >= 3:
        score += 3
    elif coverage >= 2:
        score += 1
    return {**story, "score": score}


def cluster_coverage(stories: list[dict[str, Any]], threshold: float = 0.6) -> list[dict[str, Any]]:
    """Cross-coverage measured on our own feeds: how many distinct sources ran a story with an
    overlapping title. Free, and unlike a raw Google hit count it actually varies between stories.
    Sets coverage_count = max(existing coverage_count, cluster size)."""
    words = [title_words(s.get("title", "")) for s in stories]
    names = [s.get("source_name", "") for s in stories]
    out: list[dict[str, Any]] = []
    for i, s in enumerate(stories):
        wi = words[i]
        sources = {names[i]}
        if wi:
            for j, wj in enumerate(words):
                if j != i and wj and names[j] not in sources and len(wi & wj) / len(wi) > threshold:
                    sources.add(names[j])
        out.append({**s, "coverage_count": max(int(s.get("coverage_count", 0) or 0), len(sources))})
    return out


def cap_per_source(stories: list[dict[str, Any]], max_per_source: int) -> list[dict[str, Any]]:
    """Keep input order (already score-sorted) but let no outlet take more than max_per_source slots.
    Without this a single live Tier 0 feed with ten fresh posts fills most of the episode."""
    if max_per_source <= 0:
        return list(stories)
    taken: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for s in stories:
        name = s.get("source_name", "")
        if taken.get(name, 0) >= max_per_source:
            continue
        taken[name] = taken.get(name, 0) + 1
        kept.append(s)
    return kept


def simple_dedup(stories: list[dict[str, Any]], threshold: float = 0.6) -> list[dict[str, Any]]:
    """Within-run title-overlap dedup (fallback when memory/embeddings unavailable)."""
    kept: list[dict[str, Any]] = []
    for s in stories:
        if not any(title_overlap(s["title"], k["title"]) > threshold for k in kept):
            kept.append(s)
    return kept


def dedup_stories(settings: Settings, memory: Optional[HeadlineMemory], stories: list[dict[str, Any]], cfg: dict[str, Any], namespace: str = NAMESPACE) -> list[dict[str, Any]]:
    """Three layers, cheapest first:
    1. covered in a past episode (URL / title overlap, N-day window)
    2. semantic similarity against stored embeddings (if OPENAI_API_KEY)
    3. within-run title overlap
    """
    window = int(cfg.get("dedup_window_days", 21))
    threshold = float(cfg.get("title_overlap_threshold", 0.6))
    max_dist = float(cfg.get("embedding_distance_threshold", 0.08))

    kept: list[dict[str, Any]] = []
    for story in stories:
        if memory is not None:
            try:
                if memory.was_covered(story, namespace, window, threshold):
                    log.debug("Covered in previous episode", title=story["title"][:60])
                    continue
            except Exception as e:
                log.warning("Memory lookup failed, continuing without it", error=str(e)[:120])

        if any(title_overlap(story["title"], k["title"]) > threshold for k in kept):
            continue

        vec = embed_text(settings, story["title"]) if settings.openai_api_key else None
        if vec is not None and memory is not None:
            if memory.is_semantic_duplicate(vec, namespace, window, max_dist):
                log.debug("Semantic duplicate", title=story["title"][:60])
                continue
            story = {**story, "embedding": vec}
        kept.append(story)
    return kept


def _format_stories(stories: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"[{i + 1}] {s['title']} (score={s.get('score', 0)}, tier={s.get('source_tier', '?')}, coverage={s.get('coverage_count', 0)}, source={s.get('source_name', '')})\n  URL: {s.get('url', '')}\n  Summary: {(s.get('summary') or '')[:200]}"
        for i, s in enumerate(stories)
    )


def simple_brief(stories: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "stories": stories,
        "deep_dives": [s["title"] for s in stories if s.get("is_deep_dive")],
        "cold_open_idea": "",
        "story_count": len(stories),
        "token_usage": {},
        "model": None,
    }


def _merge_llm_brief(stories: list[dict[str, Any]], llm_brief: dict[str, Any]) -> dict[str, Any]:
    """Keep full story data but adopt the LLM's ordering, time allocations and comedy angles."""
    by_title = {s["title"]: s for s in stories}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in llm_brief.get("stories", []):
        title = item.get("title", "")
        s = by_title.get(title)
        if s is None:
            # tolerate light rewording by the model
            match = max(by_title.values(), key=lambda x: title_overlap(title, x["title"]), default=None)
            if match is None or title_overlap(title, match["title"]) < 0.8:
                continue
            s = match
        if s["title"] in seen:
            continue
        seen.add(s["title"])
        ordered.append({**s, "time_allocation": item.get("time_allocation", 90), "comedy_angle": item.get("comedy_angle")})
    for s in stories:  # anything the LLM dropped goes at the end
        if s["title"] not in seen:
            ordered.append({**s, "time_allocation": 90, "comedy_angle": None})

    deep_dive_titles = set(llm_brief.get("deep_dives") or [])
    if deep_dive_titles:
        for s in ordered:
            s["is_deep_dive"] = s["title"] in deep_dive_titles or any(title_overlap(t, s["title"]) >= 0.8 for t in deep_dive_titles)
    return {
        "stories": ordered,
        "deep_dives": [s["title"] for s in ordered if s.get("is_deep_dive")],
        "cold_open_idea": llm_brief.get("cold_open_idea", ""),
        "story_count": len(ordered),
    }


def curate(settings: Settings, llm: LLM, memory: Optional[HeadlineMemory], stories: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = settings.load_yaml("curation").get("main", {})
    min_score = int(cfg.get("min_score", 2))
    max_stories = int(cfg.get("max_stories", 14))
    deep_dive_count = int(cfg.get("deep_dive_count", 4))
    deep_dive_min_cov = int(cfg.get("deep_dive_min_coverage", 2))
    max_per_source = int(cfg.get("max_per_source", 3))
    threshold = float(cfg.get("title_overlap_threshold", 0.6))

    log.info("Starting curation", story_count=len(stories))
    covered = cluster_coverage(stories, threshold)
    scored = sorted((score_story(s) for s in covered), key=lambda s: s["score"], reverse=True)
    filtered = [s for s in scored if s["score"] >= min_score]
    tier0 = [s for s in filtered if str(s.get("source_tier")) == "0"]
    others = [s for s in filtered if str(s.get("source_tier")) != "0"]

    deduped = dedup_stories(settings, memory, tier0 + others, cfg)
    selected = cap_per_source(deduped, max_per_source)[:max_stories]
    multi = sum(1 for s in selected if int(s.get("coverage_count", 0) or 0) >= 2)
    log.info("Selection", candidates=len(deduped), selected=len(selected), multi_source=multi, sources=len({s.get("source_name") for s in selected}))

    deep = [s for s in selected if int(s.get("coverage_count", 0) or 0) >= deep_dive_min_cov][:deep_dive_count]
    if len(deep) < 3:  # without Serper every story has coverage 1; fall back to top scores
        deep = selected[:deep_dive_count]
    deep_titles = {s["title"] for s in deep}
    for s in selected:
        s["is_deep_dive"] = s["title"] in deep_titles

    if not selected:
        log.warning("No stories survived curation")
        return simple_brief([])

    if not llm.available:
        log.warning("No ANTHROPIC_API_KEY — using simple brief")
        return simple_brief(selected)

    user = f"Here are the ranked stories for today's episode:\n\n{_format_stories(selected)}\n\nProduce the editorial brief as JSON."
    try:
        result = llm.call_json("curator", user, schema=BRIEF_SCHEMA)
        brief = _merge_llm_brief(selected, result.data)
        brief["token_usage"] = result.token_usage
        brief["model"] = result.model
    except Exception as e:
        log.warning("LLM brief failed, using simple brief", error=str(e)[:200])
        brief = simple_brief(selected)

    log.info("Curation complete", selected=brief["story_count"], deep_dives=len(brief["deep_dives"]))
    return brief
