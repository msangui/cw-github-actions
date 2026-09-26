"""The Aisle segment writer."""

from __future__ import annotations

from typing import Any

from pipeline.config import Settings
from pipeline.llm import LLM
from pipeline.log import get_logger
from pipeline.stages.writer import ScriptValidationError

log = get_logger(stage="aisle_writer")

AISLE_SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"script": {"type": "string"}, "story_count": {"type": "integer"}},
    "required": ["script", "story_count"],
    "additionalProperties": False,
}


def validate_aisle_script(script: str, min_words: int = 50) -> None:
    lines = [ln.strip() for ln in script.split("\n") if ln.strip()]
    bad = [ln for ln in lines if not (ln.startswith("CLAIRE:") or ln.startswith("FLINT:"))]
    if bad:
        raise ScriptValidationError(f"Aisle script has {len(bad)} lines not starting with CLAIRE: or FLINT:")
    if len(script.split()) < min_words:
        raise ScriptValidationError(f"Aisle script too short ({len(script.split())} words)")


def _format_brief(stories: list[dict[str, Any]], theme: str) -> str:
    lines = [f"Segment theme: {theme}", "", "CPG/Retail AI stories to cover:"]
    for i, s in enumerate(stories[:5]):
        lines.append(f"{i + 1}. {s['title']} ({s.get('time_allocation', 90)}s)\n   Source: {s.get('source_name', '')}\n   URL: {s.get('url', '')}\n   Summary: {(s.get('summary') or '')[:300]}")
    return "\n".join(lines)


def stub_aisle_script(stories: list[dict[str, Any]]) -> dict[str, Any]:
    lines = ["FLINT: Alright — before we get to today's reads, let's drop into The Aisle. What's moving on the shelf this week, Claire?"]
    for s in stories[:3]:
        lines += [f"CLAIRE: {s['title']}... that's worth noting.", "FLINT: CPG getting the AI treatment, I see..."]
    lines.append("CLAIRE: That's The Aisle. Back to you, Flint — what's on the reading list?")
    return {"script": "\n".join(lines), "story_count": len(stories), "token_usage": {}, "model": None, "stub": True}


def write_aisle(settings: Settings, llm: LLM, brief: dict[str, Any], max_attempts: int = 2) -> dict[str, Any]:
    stories = brief.get("stories", [])
    if not stories:
        return {"script": "", "story_count": 0, "token_usage": {}, "model": None}
    if not llm.available:
        return stub_aisle_script(stories)

    user = f"Write The Aisle segment.\n\n{_format_brief(stories, brief.get('aisle_brief', ''))}\n\nReturn only valid JSON."
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        result = llm.call_json("aisle_writer", user, schema=AISLE_SCRIPT_SCHEMA)
        try:
            validate_aisle_script(result.data.get("script", ""))
        except ScriptValidationError as e:
            last_err = e
            log.warning("Aisle script failed validation", attempt=attempt, error=str(e))
            user = f"{user}\n\nYour previous attempt was rejected: {e}. Fix that and return the segment again."
            continue
        out = {**result.data, "token_usage": result.token_usage, "model": result.model}
        log.info("Aisle script generated", word_count=len(out["script"].split()))
        return out
    raise ScriptValidationError(f"Aisle writer failed validation: {last_err}")
