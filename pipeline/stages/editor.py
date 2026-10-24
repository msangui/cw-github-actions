"""Editor: fact-check against source summaries, return line edits, approve or reject (-> safe mode).

The script is sent with numbered lines (L1, L2, ...) and the model returns only the lines that change.
Applying the edits locally keeps the ~7k output tokens of unchanged script off the bill.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from pipeline.config import Settings
from pipeline.llm import LLM
from pipeline.log import get_logger
from pipeline.stages.writer import SECTION_MARKER

log = get_logger(stage="editor")

MATCH_THRESHOLD = 0.8  # how close the model's "original" must be to the line it names

EDITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer"},
                    "original": {"type": "string"},
                    "replacement": {"type": "string"},
                },
                "required": ["line", "original", "replacement"],
                "additionalProperties": False,
            },
        },
        "changes": {"type": "array", "items": {"type": "string"}},
        "hallucinations": {"type": "array", "items": {"type": "string"}},
        "approved": {"type": "boolean"},
    },
    "required": ["edits", "changes", "hallucinations", "approved"],
    "additionalProperties": False,
}


def _script_lines(script: str) -> list[str]:
    return [ln for ln in script.split("\n") if ln.strip()]


def number_script(script: str) -> str:
    """The script as sent to the editor: one non-empty line per L-number, 1-based."""
    return "\n".join(f"L{i + 1}: {ln}" for i, ln in enumerate(_script_lines(script)))


def _similar(a: str, b: str) -> bool:
    a, b = a.strip(), b.strip()
    return a == b or (bool(b) and b in a) or SequenceMatcher(None, a, b).ratio() >= MATCH_THRESHOLD


def _locate(lines: list[str], line_no: Any, original: str) -> int | None:
    """Index of the line an edit refers to: the L-number if its text matches, else the best text match."""
    if isinstance(line_no, int) and 1 <= line_no <= len(lines) and (not original.strip() or _similar(lines[line_no - 1], original)):
        return line_no - 1
    if not original.strip():
        return None
    for i, ln in enumerate(lines):
        if ln.strip() == original.strip():
            return i
    best = max(range(len(lines)), key=lambda i: SequenceMatcher(None, lines[i].strip(), original.strip()).ratio(), default=None)
    if best is not None and SequenceMatcher(None, lines[best].strip(), original.strip()).ratio() >= MATCH_THRESHOLD:
        return best
    return None


def _valid_line(ln: str) -> bool:
    return ln.startswith("CLAIRE:") or ln.startswith("FLINT:")


def apply_edits(script: str, edits: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Apply the editor's line edits to the script. Returns (corrected script, reasons for skipped edits).

    An edit is skipped when its line cannot be located, when it targets the section marker, or when a
    replacement line lacks a CLAIRE:/FLINT: prefix. Several edits on one line: the last one wins.
    """
    lines = _script_lines(script)
    skipped: list[str] = []
    resolved: dict[int, list[str]] = {}
    for e in edits or []:
        original = str(e.get("original") or "")
        idx = _locate(lines, e.get("line"), original)
        if idx is None:
            skipped.append(f"L{e.get('line')}: no matching line for {original[:60]!r}")
            continue
        if lines[idx].strip() == SECTION_MARKER:
            skipped.append(f"L{idx + 1}: refused to edit the section marker")
            continue
        replacement = [ln.strip() for ln in str(e.get("replacement") or "").split("\n") if ln.strip()]
        if any(not _valid_line(ln) for ln in replacement):
            skipped.append(f"L{idx + 1}: replacement line without CLAIRE:/FLINT: prefix")
            continue
        resolved[idx] = replacement
    for idx in sorted(resolved, reverse=True):  # descending so earlier indexes stay valid
        lines[idx : idx + 1] = resolved[idx]
    return "\n".join(lines), skipped


def _restore_marker(original: str, corrected: str) -> str:
    """If the section marker went missing, put it back at the same relative position."""
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
    user = f"Source stories:\n{source_text}\n\nScript to review (refer to lines by their L-number):\n{number_script(script)}"
    result = llm.call_json(agent_key, user, schema=EDITOR_SCHEMA)
    data = result.data
    edits = data.get("edits") or []
    corrected, skipped = apply_edits(script, edits)
    corrected = _restore_marker(script, corrected)
    for reason in skipped:
        log.warning("Editor edit skipped", reason=reason)
    out = {
        "corrected_script": corrected,
        "changes": data.get("changes", []),
        "hallucinations": data.get("hallucinations", []),
        "approved": bool(data.get("approved", False)),
        "edits_applied": len(edits) - len(skipped),
        "edits_skipped": skipped,
        "token_usage": result.token_usage,
        "model": result.model,
    }
    log.info("Editing complete", edits=out["edits_applied"], skipped=len(skipped), hallucinations=len(out["hallucinations"]), approved=out["approved"])
    return out
