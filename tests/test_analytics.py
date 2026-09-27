"""PostHog integration: no-op without a key, event shapes, feature flags, LLM hook, workflow lifecycle."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from pipeline import workflow
from pipeline.analytics import Analytics, inject_web_snippet, sanitize_agent_overrides, web_snippet
from pipeline.config import Settings
from pipeline.llm import LLM

STORIES = [
    {"title": f"Story number {i} about agents", "url": f"https://example.com/{i}", "summary": "s", "published_at": "2026-09-26T04:00:00+00:00", "source_name": "Src", "source_tier": "0" if i < 2 else "1"}
    for i in range(6)
]


class FakeClient:
    """Stands in for posthog.Posthog: records everything, never touches the network."""

    def __init__(self, flags=None, payloads=None):
        self.events: list[tuple[str, str, dict, dict]] = []
        self.exceptions: list[tuple[BaseException, dict]] = []
        self.groups: list[tuple[str, str, dict]] = []
        self.flags_response = {"featureFlags": flags or {}, "featureFlagPayloads": payloads or {}}
        self.shutdown_calls = 0

    def capture(self, event, distinct_id=None, properties=None, groups=None):
        self.events.append((event, distinct_id, properties or {}, groups or {}))

    def capture_exception(self, exc, distinct_id=None, properties=None, groups=None):
        self.exceptions.append((exc, properties or {}))

    def group_identify(self, group_type, group_key, properties=None, distinct_id=None):
        self.groups.append((group_type, group_key, properties or {}))

    def get_all_flags_and_payloads(self, distinct_id, groups=None):
        return self.flags_response

    def shutdown(self):
        self.shutdown_calls += 1

    def named(self, event):
        return [e for e in self.events if e[0] == event]


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(Analytics, "_make_client", lambda self: client)
    return client


@pytest.fixture
def ph_settings(settings):
    settings.posthog_api_key = "phc_test"
    return settings


# ── wrapper ─────────────────────────────────────────────────────────────────


def test_disabled_without_key_is_a_noop(settings):
    a = Analytics(settings, "2026-09-26")
    assert not a.enabled and not a.llm_enabled
    a.capture("x", foo=1)
    a.generation(agent="writer", model="m", latency_s=1.0)
    a.exception(RuntimeError("boom"))
    assert a.load_flags() == {}
    assert a.flag("paused", False) is False
    assert a.payload("agent_overrides") is None
    a.shutdown()
    assert web_snippet(settings, "2026-09-26") == ""


def test_capture_adds_base_properties_and_episode_group(ph_settings, fake, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    a = Analytics(ph_settings, "2026-09-26")
    assert a.enabled
    a.capture("episode_published", cost_total_usd=1.5)
    event, distinct_id, props, groups = fake.events[0]
    assert event == "episode_published" and distinct_id == "context-window-pipeline"
    assert groups == {"episode": "2026-09-26"}
    assert props["episode_date"] == "2026-09-26" and props["cost_total_usd"] == 1.5
    assert props["runner"] == "github-actions" and props["github_run_id"] == "123" and props["trigger"] == "workflow_dispatch"
    assert props["run_trace_id"] == a.trace_id
    assert props["$process_person_profile"] is False  # anonymous events by default


def test_generation_span_and_trace_events(ph_settings, fake):
    a = Analytics(ph_settings, "2026-09-26")
    a.generation(agent="writer", model="claude-opus-5", latency_s=12.34, input_tokens=100, output_tokens=50, cache_read_tokens=0, stop_reason="end_turn", http_status=200, model_parameters={"max_tokens": 16000}, messages=[{"role": "user", "content": "hi"}], output_text="{}", attempt=1)
    (_, _, gen, _) = fake.named("$ai_generation")[0]
    assert gen["$ai_trace_id"] == a.trace_id and gen["$ai_span_name"] == "writer" and gen["agent"] == "writer"
    assert gen["$ai_provider"] == "anthropic" and gen["$ai_model"] == "claude-opus-5"
    assert gen["$ai_input_tokens"] == 100 and gen["$ai_output_tokens"] == 50 and gen["$ai_latency"] == 12.34
    assert gen["$ai_is_error"] is False and gen["$ai_stop_reason"] == "end_turn" and gen["$ai_http_status"] == 200
    assert "$ai_cache_read_input_tokens" not in gen  # zero is omitted
    assert "$ai_input" not in gen and "$ai_output_choices" not in gen  # send_prompts defaults to off

    a.cfg["llm_analytics"] = {**a.cfg["llm_analytics"], "send_prompts": True}
    a.generation(agent="editor", model="claude-opus-5", latency_s=1, messages=[{"role": "user", "content": "hi"}], output_text="ok", error="RateLimitError: slow down", http_status=429)
    (_, _, gen2, _) = fake.named("$ai_generation")[1]
    assert gen2["$ai_input"] == [{"role": "user", "content": "hi"}] and gen2["$ai_output_choices"] == [{"role": "assistant", "content": "ok"}]
    assert gen2["$ai_is_error"] is True and gen2["$ai_error"].startswith("RateLimitError") and gen2["$ai_http_status"] == 429

    a.span("curator", 3.2)
    (_, _, span, _) = fake.named("$ai_span")[0]
    assert span["$ai_parent_id"] == a.trace_id and span["$ai_span_name"] == "curator" and span["$ai_latency"] == 3.2

    a.trace("PUBLISHED", 100.0, output_state={"cost_usd": 3.0})
    (_, _, trace, _) = fake.named("$ai_trace")[0]
    assert trace["$ai_span_id"] == a.trace_id and trace["$ai_output_state"] == {"status": "PUBLISHED", "cost_usd": 3.0}

    a.exception(RuntimeError("boom"), stage="tts")
    exc, props = fake.exceptions[0]
    assert str(exc) == "boom" and props["stage"] == "tts" and props["episode_date"] == "2026-09-26"

    a.shutdown()
    assert fake.shutdown_calls == 1 and not a.enabled


def test_flags_payloads_and_agent_overrides(ph_settings, fake):
    fake.flags_response = {
        "featureFlags": {"cw-pipeline-paused": False, "cw-aisle-enabled": False, "cw-force-dry-run": "variant-a", "cw-agent-overrides": True},
        "featureFlagPayloads": {"cw-agent-overrides": json.dumps({"writer": {"model": "claude-sonnet-5", "effort": "high", "system_prompt": "HACKED"}, "editor": "not-a-dict", "curator": {"max_tokens": 4000}})},
    }
    a = Analytics(ph_settings, "2026-09-26")
    flags = a.load_flags()
    assert flags["cw-aisle-enabled"] is False
    assert a.flag("paused", True) is False and a.flag("aisle_enabled", True) is False and a.flag("force_dry_run") == "variant-a"
    assert a.flag("does_not_exist", "dflt") == "dflt"
    overrides = sanitize_agent_overrides(a.payload("agent_overrides"))
    assert overrides == {"writer": {"model": "claude-sonnet-5", "effort": "high"}, "curator": {"max_tokens": 4000}}  # prompt override dropped
    assert sanitize_agent_overrides("garbage") == {} and sanitize_agent_overrides(None) == {}

    original_model = Settings().agent("writer")["model"]
    ph_settings.agent_overrides = overrides
    cfg = ph_settings.agent("writer")
    assert cfg["model"] == "claude-sonnet-5" and cfg["effort"] == "high" and "HACKED" not in cfg["system_prompt"]
    assert ph_settings.agent("editor")["model"] == original_model
    assert Settings().agent("writer")["model"] == original_model, "cached YAML must not be mutated"

    a.capture("episode_run_started")
    props = fake.named("episode_run_started")[0][2]
    assert props["$feature/cw-aisle-enabled"] is False and props["$feature/cw-force-dry-run"] == "variant-a"


def test_flag_loading_failure_falls_back_to_defaults(ph_settings, fake):
    def explode(*_a, **_k):
        raise ConnectionError("posthog down")

    fake.get_all_flags_and_payloads = explode
    a = Analytics(ph_settings, "2026-09-26")
    assert a.load_flags() == {} and a.flag("paused", False) is False


# ── newsletter web snippet ──────────────────────────────────────────────────


def test_web_snippet_and_injection(ph_settings):
    snippet = web_snippet(ph_settings, "2026-09-26")
    assert 'src="https://us-assets.i.posthog.com/static/array.js"' in snippet
    assert 'posthog.init("phc_test", {"api_host": "https://us.i.posthog.com"' in snippet
    assert '"episode_date": "2026-09-26"' in snippet
    html = "<html><body><p>hi</p></body></html>"
    out = inject_web_snippet(html, snippet)
    assert out.index("<p>hi</p>") < out.index("array.js") < out.index("</body>")
    assert inject_web_snippet(html, "") == html
    assert inject_web_snippet("<p>no body tag</p>", snippet).endswith(snippet)

    ph_settings.posthog_host = "https://ph.example.internal"
    assert 'src="https://ph.example.internal/static/array.js"' in web_snippet(ph_settings, "2026-09-26")


def test_web_snippet_respects_config_switch(ph_settings, monkeypatch):
    monkeypatch.setattr(Settings, "load_yaml", lambda self, name: {"enabled": True, "newsletter": {"web_snippet": False}} if name == "analytics" else Settings.__dict__["load_yaml"](self, name))
    assert web_snippet(ph_settings, "2026-09-26") == ""


# ── LLM hook ────────────────────────────────────────────────────────────────


class _FakeStream:
    def __init__(self, message):
        self.message = message

    def get_final_message(self):
        return self.message


def _fake_anthropic(message):
    @contextmanager
    def stream(**kwargs):
        yield _FakeStream(message)

    return SimpleNamespace(messages=SimpleNamespace(stream=stream))


def test_llm_call_emits_ai_generation(ph_settings, fake, monkeypatch):
    ph_settings.anthropic_api_key = "sk-ant-test"
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"ok": true}')],
        usage=SimpleNamespace(input_tokens=1234, output_tokens=56, cache_read_input_tokens=100, cache_creation_input_tokens=0),
        stop_reason="end_turn",
    )
    monkeypatch.setattr(LLM, "client", property(lambda self: _fake_anthropic(message)))
    a = Analytics(ph_settings, "2026-09-26")
    llm = LLM(ph_settings, analytics=a)
    result = llm.call_json("editor", "review this", schema={"type": "object"})
    assert result.data == {"ok": True}

    gens = fake.named("$ai_generation")
    assert len(gens) == 1
    props = gens[0][2]
    assert props["$ai_span_name"] == "editor" and props["$ai_model"] == ph_settings.agent("editor")["model"]
    assert props["$ai_input_tokens"] == 1234 and props["$ai_output_tokens"] == 56 and props["$ai_cache_read_input_tokens"] == 100
    assert props["$ai_stop_reason"] == "end_turn" and props["$ai_http_status"] == 200 and props["attempt"] == 1
    assert props["$ai_model_parameters"]["structured_output"] is True and props["$ai_trace_id"] == a.trace_id
    assert "$ai_input" not in props


def test_llm_works_without_analytics(ph_settings, monkeypatch):
    ph_settings.anthropic_api_key = "sk-ant-test"
    message = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"a": 1}')], usage=SimpleNamespace(input_tokens=1, output_tokens=1), stop_reason="end_turn")
    monkeypatch.setattr(LLM, "client", property(lambda self: _fake_anthropic(message)))
    assert LLM(ph_settings).call_json("editor", "x").data == {"a": 1}


# ── workflow lifecycle ──────────────────────────────────────────────────────


def test_dry_run_emits_lifecycle_events_and_flushes(ph_settings, fake, monkeypatch):
    monkeypatch.setattr(workflow, "ingest_main", lambda s: STORIES)
    monkeypatch.setattr(workflow, "ingest_aisle", lambda s: [])
    ph_settings.dry_run = True
    result = workflow.run_episode(ph_settings, "2026-09-27")
    assert result["status"] == "DRY_RUN"

    names = [e[0] for e in fake.events]
    assert names[0] == "episode_run_started"
    assert names[-2:] == ["episode_run_finished", "$ai_trace"]
    statuses = [e[2]["status"] for e in fake.named("episode_status_changed")]
    assert statuses == ["INGESTING", "CURATING", "WRITING", "EDITING", "DRY_RUN_COMPLETE"]
    stages = {e[2]["stage"]: e[2] for e in fake.named("pipeline_stage_completed")}
    assert {"ingest_main", "coverage", "curator", "writer", "editor"} <= set(stages)
    assert stages["writer"]["from_checkpoint"] is False and "seconds" in stages["writer"]
    assert {e[2]["$ai_span_name"] for e in fake.named("$ai_span")} >= {"curator", "writer", "editor"}
    finished = fake.named("episode_run_finished")[0][2]
    assert finished["status"] == "DRY_RUN" and finished["cost_total_usd"] == 0 and finished["dry_run"] is True
    assert fake.named("$ai_trace")[0][2]["$ai_output_state"]["status"] == "DRY_RUN"
    assert fake.shutdown_calls == 1

    # Same date again: stages come back from checkpoints and say so.
    fake.events.clear()
    workflow.run_episode(ph_settings, "2026-09-27")
    assert all(e[2]["from_checkpoint"] for e in fake.named("pipeline_stage_completed"))


def test_paused_flag_stops_the_run_before_any_work(ph_settings, fake, monkeypatch):
    fake.flags_response = {"featureFlags": {"cw-pipeline-paused": True}, "featureFlagPayloads": {}}
    calls = []
    monkeypatch.setattr(workflow, "ingest_main", lambda s: calls.append(1) or STORIES)
    ph_settings.dry_run = True
    result = workflow.run_episode(ph_settings, "2026-09-27")
    assert result["status"] == "PAUSED" and "cw-pipeline-paused" in result["error"]
    assert calls == []
    paused = fake.named("episode_paused")[0][2]
    assert paused["reason"] == "feature_flag" and paused["$feature/cw-pipeline-paused"] is True
    assert fake.named("episode_run_finished")[0][2]["status"] == "PAUSED"


def test_flags_toggle_aisle_dry_run_and_agent_overrides(ph_settings, fake, monkeypatch):
    fake.flags_response = {
        "featureFlags": {"cw-aisle-enabled": False, "cw-force-dry-run": True, "cw-agent-overrides": True},
        "featureFlagPayloads": {"cw-agent-overrides": json.dumps({"writer": {"model": "claude-sonnet-5"}})},
    }
    monkeypatch.setattr(workflow, "ingest_main", lambda s: STORIES)
    aisle_calls = []
    monkeypatch.setattr(workflow, "ingest_aisle", lambda s: aisle_calls.append(1) or [])
    assert ph_settings.dry_run is False
    result = workflow.run_episode(ph_settings, "2026-09-27")
    assert result["status"] == "DRY_RUN", result  # forced by flag: no TTS attempted
    assert aisle_calls == [] and ph_settings.skip_aisle is True
    assert ph_settings.agent("writer")["model"] == "claude-sonnet-5"
    assert ph_settings.agent("editor")["model"] == Settings().agent("editor")["model"]


def test_stage_failure_is_reported_to_error_tracking(ph_settings, fake, monkeypatch):
    def boom(s):
        raise RuntimeError("feeds exploded")

    monkeypatch.setattr(workflow, "ingest_main", boom)
    monkeypatch.setattr(workflow, "ingest_aisle", lambda s: [])
    result = workflow.run_episode(ph_settings, "2026-09-27")
    assert result["status"] == "FAILED" and result["stage"] == "ingest_main"
    failed = fake.named("pipeline_stage_failed")[0][2]
    assert failed["stage"] == "ingest_main" and "feeds exploded" in failed["error"]
    exc, props = fake.exceptions[0]
    assert "feeds exploded" in str(exc) and props["stage"] == "ingest_main" and props["status"] == "INGESTING"
    assert [e[2]["status"] for e in fake.named("episode_status_changed")][-1] == "FAILED"
    assert fake.named("episode_run_finished")[0][2]["status"] == "FAILED"
    assert fake.shutdown_calls == 1
