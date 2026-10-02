"""Writer: curator brief -> full CLAIRE/FLINT script + episode metadata."""

from __future__ import annotations

from typing import Any

from pipeline.config import Settings
from pipeline.llm import LLM
from pipeline.log import get_logger
from pipeline.show import compile_agent_prompt

log = get_logger(stage="writer")

SECTION_MARKER = "# SECTION:READ_THESE"
WORD_MIN, WORD_MAX = 2000, 5000

SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "script": {"type": "string"},
        "metadata": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "story_count": {"type": "integer"},
                "deep_dive_picks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}, "url": {"type": "string"}, "tease": {"type": "string"}},
                        "required": ["title", "url", "tease"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "description", "story_count", "deep_dive_picks"],
            "additionalProperties": False,
        },
    },
    "required": ["script", "metadata"],
    "additionalProperties": False,
}


class ScriptValidationError(ValueError):
    pass


def validate_script(script: str, word_min: int = WORD_MIN, word_max: int = WORD_MAX, require_marker: bool = False) -> None:
    lines = [ln.strip() for ln in script.split("\n") if ln.strip()]
    bad = [ln for ln in lines if not (ln.startswith("CLAIRE:") or ln.startswith("FLINT:") or ln.startswith("# SECTION:"))]
    if bad:
        raise ScriptValidationError(f"Script has {len(bad)} lines not starting with CLAIRE: or FLINT: (first: {bad[0][:80]!r})")
    words = len(script.split())
    if not (word_min <= words <= word_max):
        raise ScriptValidationError(f"Script word count {words} outside range {word_min}-{word_max}")
    if require_marker and SECTION_MARKER not in script:
        raise ScriptValidationError(f"Script is missing the {SECTION_MARKER} marker")


def format_brief(stories: list[dict[str, Any]], cold_open_idea: str, max_stories: int = 14) -> str:
    lines = [f"Cold open idea: {cold_open_idea or '(writer’s choice)'}", "", "Stories to cover (in order):"]
    for i, s in enumerate(stories[:max_stories]):
        dd = " [DEEP DIVE]" if s.get("is_deep_dive") else ""
        alloc = s.get("time_allocation", 90)
        angle = f"\n   Comedy angle: {s['comedy_angle']}" if s.get("comedy_angle") else ""
        lines.append(
            f"{i + 1}. {s['title']}{dd} ({alloc}s)\n   Source: {s.get('source_name', '')}\n   URL: {s.get('url', '')}\n   Summary: {(s.get('summary') or '')[:300]}{angle}"
        )
    return "\n".join(lines)


def stub_script(brief: dict[str, Any]) -> dict[str, Any]:
    """Used when no ANTHROPIC_API_KEY is configured (dry runs / tests)."""
    stories = brief.get("stories", [])
    lines = [
        "FLINT: Hey Claire, welcome back to Context Window.",
        "CLAIRE: Good to be here.",
        f"FLINT: Alright, alright — we've got {len(stories)} stories today, let's get into it.",
    ]
    for s in stories[:3]:
        lines += [f"FLINT: So {s['title']}...", "CLAIRE: That is notable."]
    lines += [
        SECTION_MARKER,
        "FLINT: Links are in your Telegram. You know what to do. Sorry for my gibberish, it happens every now and then",
        "CLAIRE: We'll be here tomorrow.",
        "FLINT: Unfortunately.",
        "FLINT: My prediction: agents will replace most SaaS workflows within the year.",
        "CLAIRE: I give that a two out of ten on scientific credibility.",
        "FLINT: I stand by it.",
        "CLAIRE: You always do.",
    ]
    return {
        "script": "\n".join(lines),
        "metadata": {
            "title": f"Context Window — {len(stories)} Stories",
            "description": "Daily AI and agentic engineering news.",
            "story_count": len(stories),
            "deep_dive_picks": [{"title": s["title"], "url": s.get("url", ""), "tease": ""} for s in stories if s.get("is_deep_dive")],
        },
        "token_usage": {},
        "model": None,
        "stub": True,
    }


def write_script(settings: Settings, llm: LLM, brief: dict[str, Any], max_attempts: int = 3) -> dict[str, Any]:
    if not llm.available:
        log.warning("No ANTHROPIC_API_KEY — returning stub script")
        return stub_script(brief)

    cfg = settings.agent("writer")
    word_min = int(cfg.get("word_count_min_hard", WORD_MIN))
    word_max = int(cfg.get("word_count_max_hard", WORD_MAX))
    brief_text = format_brief(brief.get("stories", []), brief.get("cold_open_idea", ""))
    user = f"Write today's episode script.\n\n{brief_text}\n\nReturn only valid JSON."
    # Hosts + dynamics come from config/show.yaml, rendered into the {{SHOW}} slot of writer.yaml.
    system_prompt = compile_agent_prompt(settings, "writer")

    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        result = llm.call_json("writer", user, schema=SCRIPT_SCHEMA, system_override=system_prompt)
        script = result.data.get("script", "")
        try:
            validate_script(script, word_min, word_max)
        except ScriptValidationError as e:
            last_err = e
            log.warning("Script failed validation, regenerating", attempt=attempt, error=str(e))
            user = f"{user}\n\nYour previous attempt was rejected: {e}. Fix that and return the complete script again."
            continue
        if SECTION_MARKER not in script:
            log.warning("Script missing section marker — extended edition will be unavailable")
        out = {**result.data, "token_usage": result.token_usage, "model": result.model}
        log.info("Script generated", word_count=len(script.split()), title=out.get("metadata", {}).get("title", ""))
        return out
    raise ScriptValidationError(f"Writer failed validation after {max_attempts} attempts: {last_err}")
