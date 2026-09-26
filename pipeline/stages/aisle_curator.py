"""The Aisle curator: CPG/Retail AI relevance scoring + dedup + brief."""

from __future__ import annotations

from typing import Any, Optional

from pipeline.config import Settings
from pipeline.llm import LLM
from pipeline.log import get_logger
from pipeline.memory import HeadlineMemory, title_overlap
from pipeline.stages.curator import dedup_stories, prescore

log = get_logger(stage="aisle_curator")

NAMESPACE = "aisle_headlines"

_HIGH_RELEVANCE = {
    "supply chain", "inventory", "retail", "grocery", "cpg", "consumer goods", "merchandising", "planogram",
    "demand forecasting", "price optimization", "shelf", "checkout", "store operations", "fmcg", "supermarket",
    "shopper", "trade promotion", "replenishment", "assortment",
}
_MEDIUM_RELEVANCE = {
    "personalization", "recommendation", "e-commerce", "omnichannel", "logistics", "fulfillment", "warehouse",
    "food", "beverage", "consumer", "brand", "packaging", "customer journey", "loyalty",
}
_AI_MARKERS = ("ai ", " ai", "artificial intelligence", "machine learning", "ml ", "llm", "model", "algorithm", "automation", "generative")

AISLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "time_allocation": {"type": "integer", "enum": [60, 90, 120]}},
                "required": ["title", "time_allocation"],
                "additionalProperties": False,
            },
        },
        "story_count": {"type": "integer"},
        "aisle_brief": {"type": "string"},
    },
    "required": ["stories", "story_count", "aisle_brief"],
    "additionalProperties": False,
}


def score_aisle_story(story: dict[str, Any]) -> dict[str, Any]:
    score = prescore(story)
    relevance = 0
    text = (story.get("title", "") + " " + (story.get("summary") or "")).lower()
    if any(m in text for m in _AI_MARKERS):
        score += 2
        if any(kw in text for kw in _HIGH_RELEVANCE):
            score += 3
            relevance = 2
        elif any(kw in text for kw in _MEDIUM_RELEVANCE):
            score += 1
            relevance = 1
    return {**story, "score": score, "aisle_relevance": relevance}


def _empty() -> dict[str, Any]:
    return {"stories": [], "story_count": 0, "aisle_brief": "", "token_usage": {}, "model": None}


def curate_aisle(settings: Settings, llm: LLM, memory: Optional[HeadlineMemory], stories: list[dict[str, Any]]) -> dict[str, Any]:
    if not stories:
        return _empty()
    cfg = settings.load_yaml("curation").get("aisle", {})
    max_stories = int(cfg.get("max_stories", 5))

    scored = sorted((score_aisle_story(s) for s in stories), key=lambda s: s["score"], reverse=True)
    relevant = [s for s in scored if s.get("aisle_relevance", 0) > 0] or scored
    deduped = dedup_stories(settings, memory, relevant, cfg, namespace=NAMESPACE)
    selected = deduped[:max_stories]
    if not selected:
        log.info("No aisle stories after dedup")
        return _empty()
    for s in selected:
        s.setdefault("time_allocation", 90)

    if not llm.available:
        return {"stories": selected, "story_count": len(selected), "aisle_brief": f"The Aisle: {len(selected)} CPG/Retail AI stories", "token_usage": {}, "model": None}

    story_text = "\n".join(
        f"[{i + 1}] {s['title']} (score={s['score']}, source={s.get('source_name', '')})\n  URL: {s.get('url', '')}\n  Summary: {(s.get('summary') or '')[:200]}"
        for i, s in enumerate(selected)
    )
    try:
        result = llm.call_json("aisle_curator", f"Here are today's CPG/Retail AI stories:\n\n{story_text}\n\nProduce the JSON brief.", schema=AISLE_SCHEMA)
        by_title = {s["title"]: s for s in selected}
        ordered: list[dict[str, Any]] = []
        for item in result.data.get("stories", []):
            match = by_title.get(item.get("title")) or max(selected, key=lambda x: title_overlap(item.get("title", ""), x["title"]))
            if match and match not in ordered and title_overlap(item.get("title", ""), match["title"]) >= 0.8:
                ordered.append({**match, "time_allocation": item.get("time_allocation", 90)})
        if not ordered:
            ordered = selected
        brief = {"stories": ordered, "story_count": len(ordered), "aisle_brief": result.data.get("aisle_brief", ""), "token_usage": result.token_usage, "model": result.model}
    except Exception as e:
        log.warning("LLM aisle brief failed, using simple brief", error=str(e)[:200])
        brief = {"stories": selected, "story_count": len(selected), "aisle_brief": "", "token_usage": {}, "model": None}

    log.info("Aisle curation complete", selected=brief["story_count"])
    return brief
