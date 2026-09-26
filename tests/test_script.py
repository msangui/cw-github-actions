import pytest

from pipeline.llm import LLMError, extract_json
from pipeline.stages.editor import _restore_marker
from pipeline.stages.stitch import combine_for_extended, duck_expression
from pipeline.stages.tts import parse_script_lines
from pipeline.stages.writer import (
    SECTION_MARKER,
    ScriptValidationError,
    format_brief,
    stub_script,
    validate_script,
)

SCRIPT = "\n".join(["FLINT: Hey there.", "CLAIRE: Hello.", SECTION_MARKER, "FLINT: Read these.", "CLAIRE: Bye."])


def test_parse_script_lines_records_marker_index():
    lines, markers = parse_script_lines(SCRIPT)
    assert lines == [("FLINT", "Hey there."), ("CLAIRE", "Hello."), ("FLINT", "Read these."), ("CLAIRE", "Bye.")]
    assert markers == {"read_these_start": 2}


def test_parse_ignores_blank_and_garbage_lines():
    lines, _ = parse_script_lines("\n\nFLINT:   \nCLAIRE: ok\n[stage direction]\n")
    assert lines == [("CLAIRE", "ok")]


def test_validate_script_rejects_bad_prefix():
    with pytest.raises(ScriptValidationError):
        validate_script("FLINT: hi\nNARRATOR: no\n" + "CLAIRE: word " * 2000)


def test_validate_script_word_range():
    with pytest.raises(ScriptValidationError):
        validate_script("FLINT: too short")
    validate_script("FLINT: " + "word " * 2500)


def test_stub_script_is_valid_shape():
    stub = stub_script({"stories": [{"title": "t", "url": "u", "is_deep_dive": True}]})
    lines, markers = parse_script_lines(stub["script"])
    assert lines and "read_these_start" in markers
    assert stub["metadata"]["deep_dive_picks"][0]["title"] == "t"


def test_format_brief_includes_allocation_and_deep_dive_flag():
    text = format_brief([{"title": "X", "is_deep_dive": True, "time_allocation": 120, "summary": "s", "source_name": "src", "url": "u"}], "idea")
    assert "[DEEP DIVE]" in text and "(120s)" in text and "Cold open idea: idea" in text


def test_restore_marker_when_editor_drops_it():
    corrected = SCRIPT.replace(SECTION_MARKER + "\n", "")
    assert SECTION_MARKER not in corrected
    restored = _restore_marker(SCRIPT, corrected)
    lines = [ln for ln in restored.split("\n") if ln.strip()]
    assert lines[2] == SECTION_MARKER


def test_combine_for_extended_inserts_aisle_before_read_these():
    std = ["s0", "s1", "s2", "s3"]
    assert combine_for_extended(std, ["a0", "a1"], 2) == ["s0", "s1", "a0", "a1", "s2", "s3"]


def test_duck_expression_shape():
    expr = duck_expression(9, 15, 0.25)
    assert expr.startswith("if(lt(t,9)") and "0.25" in expr and "0.75" in expr


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('{"a": 1}') == '{"a": 1}'
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('prefix {"a": {"b": 2}} suffix') == '{"a": {"b": 2}}'
    with pytest.raises(LLMError):
        extract_json("no json here")
