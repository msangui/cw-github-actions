"""Editor: fact-check against source summaries, fix inline, approve or reject (-> safe mode)."""

from __future__ import annotations

from typing import Any

from pipeline.config import Settings
from pipeline.llm import LLM
from pipeline.log import get_logger
from pipeline.stages.writer import SECTION_MARKER

log = get_logger(stage="editor")

EDITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "corrected_script": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "string"}},
        "hallucinations": {"type": "array", "items": {"type": "string"}},
        "approved": {"type": "boolean"},
    },
    "required": ["corrected_script", "changes", "hallucinations", "approved"],
    "additionalProperties": False,
}


def _restore_marker(original: str, corrected: str) -> str:
    """If the editor dropped the section marker, put it back at the same relative position."""
    if SECTION_MARKER in corrected or SECTION_MARKER not in original:
        return corrected
    orig_lines = [ln for ln in original.split("\n") if ln.strip()]
    idx = orig_lines.index(SECTION_MARKER)
    if idx == 0:
        return corrected
    anchor = orig_lines[idx - 1].strip()
    corr_lines = corrected.split("\n")
    for i, ln in enumerate(corr_lines):
        if ln.strip() == anchor:
            corr_lines.insert(i + 1, SECTION_MARKER)
            return "\n".join(corr_lines)
    # Fallback: same line index (lines are rarely added/removed by the editor)
    corr_nonempty = [ln for ln in corr_lines if ln.strip()]
    corr_nonempty.insert(min(idx, len(corr_nonempty)), SECTION_MARKER)
    return "\n".join(corr_nonempty)


def edit_script(settings: Settings, llm: LLM, script_result: dict[str, Any], stories: list[dict[str, Any]], agent_key: str = "editor") -> dict[str, Any]:
    script = script_result.get("script", "") if isinstance(script_result, dict) else str(script_result)
    if not llm.available:
        log.warning("No ANTHROPIC_API_KEY — auto-approving")
        return {"corrected_script": script, "changes": [], "hallucinations": [], "approved": True, "token_usage": {}, "model": None}

    source_text = "\n".join(f"- {s['title']} ({s.get('source_name', '')}): {(s.get('summary') or '')[:300]}" for s in stories[:20])
    user = f"Source stories:\n{source_text}\n\nScript to review:\n{script}"
    result = llm.call_json(agent_key, user, schema=EDITOR_SCHEMA)
    data = result.data
    corrected = _restore_marker(script, data.get("corrected_script") or script)
    out = {
        "corrected_script": corrected,
        "changes": data.get("changes", []),
        "hallucinations": data.get("hallucinations", []),
        "approved": bool(data.get("approved", False)),
        "token_usage": result.token_usage,
        "model": result.model,
    }
    log.info("Editing complete", changes=len(out["changes"]), hallucinations=len(out["hallucinations"]), approved=out["approved"])
    return out
