"""The writer prompt compiler: show.yaml dials -> language bands -> {{SHOW}} slot."""

import copy
from pathlib import Path

import yaml

from pipeline.config import REPO_ROOT, Settings
from pipeline.show import (
    DYNAMICS_DIALS,
    HOST_DIALS,
    SHOW_PLACEHOLDER,
    band_index,
    band_text,
    compile_agent_prompt,
    compile_system_prompt,
    render_show_block,
)
from pipeline.stages.writer import SECTION_MARKER

FIXTURES = Path(__file__).parent / "fixtures"
SIGN_OFFS = [
    'FLINT: "Links are in your Telegram. You know what to do. Sorry for my gibberish, it happens every now and then"',
    'CLAIRE: "We\'ll be here tomorrow."',
    'FLINT: "Unfortunately."',
    'FLINT: "I stand by it."',
    'CLAIRE: "You always do."',
]


def _show() -> dict:
    """Frozen copy of show.yaml (tests/fixtures/show.yaml) so panel commits to config/ never break CI."""
    return yaml.safe_load((FIXTURES / "show.yaml").read_text(encoding="utf-8"))


def _live_show() -> dict:
    return yaml.safe_load((REPO_ROOT / "config" / "show.yaml").read_text(encoding="utf-8"))


def _fixture_prompt(agent: str) -> str:
    return yaml.safe_load((FIXTURES / f"{agent}.yaml").read_text(encoding="utf-8"))["system_prompt"]


def test_band_index_boundaries():
    assert band_index(0) == 0 and band_index(19) == 0
    assert band_index(20) == 1 and band_index(39) == 1
    assert band_index(40) == 2 and band_index(59) == 2
    assert band_index(60) == 3 and band_index(79) == 3
    assert band_index(80) == 4 and band_index(100) == 4
    assert band_index(250) == 4 and band_index(-5) == 0 and band_index("garbage") == 2
    assert band_index(50, [10, 90]) == 1


def test_every_dial_has_five_bands():
    """Runs against the live config: this is the structural contract the panel must keep."""
    show = _live_show()
    for d in HOST_DIALS:
        assert len(show["bands"]["host"][d]) == 5, d
    for d in DYNAMICS_DIALS:
        assert len(show["bands"]["dynamics"][d]) == 5, d
    for key, host in show["hosts"].items():
        assert set(host["dials"]) == set(HOST_DIALS), key
    assert set(show["dynamics"]) == set(DYNAMICS_DIALS)


def test_moving_a_dial_changes_the_band_text():
    show = _show()
    low = band_text(show, "host", "skepticism", 5)
    high = band_text(show, "host", "skepticism", 95)
    assert low != high
    assert low == show["bands"]["host"]["skepticism"][0]
    assert high == show["bands"]["host"]["skepticism"][4]


def test_render_block_contains_hosts_and_dynamics():
    block = render_show_block(_show())
    assert block.startswith("SHOW:")
    assert "HOSTS:" in block and "DYNAMICS (how the hosts interact):" in block
    assert "FLINT — Main host. Texas drawl" in block
    assert "CLAIRE — Expert co-host. PhD ML researcher" in block
    assert "Em-dash (—) before precise rebukes" in block
    assert "Trails off with ellipsis (...)" in block
    assert "- Pace:" in block


def test_missing_bands_fall_back_to_numeric():
    show = _show()
    del show["bands"]["host"]["humor"]
    assert band_text(show, "host", "humor", 72) == "Humor: 72/100"


def test_compile_preserves_indentation_and_leaves_prompts_without_placeholder_alone():
    out = compile_system_prompt("  intro\n  {{SHOW}}\n  rules", {"hosts": {"X": {"role": "Host", "dials": {}}}})
    assert "  HOSTS:\n  X — Host.\n" in out
    assert SHOW_PLACEHOLDER not in out
    assert compile_system_prompt("no slot here", _show()) == "no slot here"
    assert compile_system_prompt("a\n{{SHOW}}\nb", None) == "a\n\nb"


def test_compiled_writer_prompt_keeps_sign_offs_and_marker():
    prompt = compile_agent_prompt(Settings(), "writer")
    assert SHOW_PLACEHOLDER not in prompt
    assert SECTION_MARKER in prompt
    for line in SIGN_OFFS:
        assert line in prompt, line
    assert "SCRIPT RULES:" in prompt and "Manner:" in prompt


def test_compiled_aisle_prompt_keeps_transition_lines():
    prompt = compile_agent_prompt(Settings(), "aisle_writer")
    assert SHOW_PLACEHOLDER not in prompt
    assert "let's drop into The Aisle" in prompt
    assert "That's The Aisle. Back to you, Flint" in prompt
    assert "HOSTS:" in prompt


def test_golden_fixtures_match_frozen_inputs():
    """Parity contract with admin/lib/compile-prompt.test.ts: both compilers must turn
    tests/fixtures/{show,writer,aisle_writer}.yaml into tests/fixtures/compiled_*_prompt.txt.
    Only regenerate when the *compiler* changes (not when config/ changes):
      python -c "import yaml; from pipeline.show import compile_system_prompt as c; s=yaml.safe_load(open('tests/fixtures/show.yaml'));
      [open(f'tests/fixtures/compiled_{a}_prompt.txt','w').write(c(yaml.safe_load(open(f'tests/fixtures/{a}.yaml'))['system_prompt'], s)) for a in ('writer','aisle_writer')]"
    """
    show = _show()
    assert compile_system_prompt(_fixture_prompt("writer"), show) == (FIXTURES / "compiled_writer_prompt.txt").read_text(encoding="utf-8")
    assert compile_system_prompt(_fixture_prompt("aisle_writer"), show) == (FIXTURES / "compiled_aisle_writer_prompt.txt").read_text(encoding="utf-8")


def test_dial_change_flows_into_compiled_prompt(tmp_path, monkeypatch):
    """End to end: edit show.yaml in a temp config dir, the writer prompt changes accordingly."""
    cfg_dir = tmp_path / "config"
    (cfg_dir / "agents").mkdir(parents=True)
    for p in (REPO_ROOT / "config" / "agents").glob("*.yaml"):
        (cfg_dir / "agents" / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    show = copy.deepcopy(_live_show())
    show["hosts"]["FLINT"]["dials"]["skepticism"] = 95
    show["dynamics"]["joke_density"] = 0
    (cfg_dir / "show.yaml").write_text(yaml.safe_dump(show, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("CW_CONFIG_DIR", str(cfg_dir))
    prompt = compile_agent_prompt(Settings(), "writer")
    assert show["bands"]["host"]["skepticism"][4] in prompt
    assert "Joke density: No jokes anywhere in the script." in prompt
