"""PostHog: product analytics, LLM analytics, error tracking and feature flags for the pipeline.

Best-effort by design — nothing in here may ever fail an episode. Without POSTHOG_API_KEY (or
with `enabled: false` in config/analytics.yaml) every method is a no-op and feature flags
return their defaults, which is what CI and local dry runs get.

What goes where in PostHog:
  * Product analytics  — `episode_*` / `pipeline_stage_completed` events, grouped by `episode`
                         (group key = episode date) so trends and funnels can be sliced per episode.
  * LLM analytics      — one `$ai_generation` per Claude call (from pipeline/llm.py), one `$ai_span`
                         per pipeline stage and one `$ai_trace` per run, all sharing `$ai_trace_id`.
  * Error tracking     — `capture_exception` for the exception that failed a run.
  * Feature flags      — operational switches evaluated once at run start (pause, aisle on/off,
                         force dry run) plus a JSON payload that overrides agent model/effort
                         without a commit. Flag keys live in config/analytics.yaml.
  * Web analytics      — an optional posthog-js snippet injected into newsletter.html
                         (page views + link clicks per episode).
"""

from __future__ import annotations

import json
import os
import platform
import uuid
from typing import Any, Optional

from pipeline.config import Settings
from pipeline.log import get_logger

log = get_logger(component="analytics")

DEFAULT_HOST = "https://us.i.posthog.com"
DEFAULT_DISTINCT_ID = "context-window-pipeline"
DEFAULT_GROUP_TYPE = "episode"

# Agent config keys a feature-flag payload may override. Prompts stay in git on purpose.
AGENT_OVERRIDE_KEYS = frozenset({"model", "effort", "max_tokens", "temperature"})


def _parse_payload(value: Any) -> Any:
    """Flag payloads arrive JSON-encoded as strings from the /flags endpoint."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def sanitize_agent_overrides(payload: Any) -> dict[str, dict[str, Any]]:
    """Keep only {agent: {model|effort|max_tokens|temperature: value}} from a flag payload."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for agent, cfg in payload.items():
        if not isinstance(cfg, dict):
            continue
        kept = {k: v for k, v in cfg.items() if k in AGENT_OVERRIDE_KEYS and v is not None}
        if kept:
            out[str(agent)] = kept
    return out


def web_snippet(settings: Settings, episode_date: str) -> str:
    """posthog-js loader for newsletter.html. Empty string when analytics is off.

    Uses the project API key, which PostHog designs to be public. Readers stay anonymous
    (`person_profiles: identified_only`); every event carries the episode date so page views
    and link clicks can be broken down per episode next to the pipeline's own events.
    """
    cfg = settings.load_yaml("analytics")
    if not settings.posthog_api_key or not cfg.get("enabled", True) or not cfg.get("newsletter", {}).get("web_snippet", True):
        return ""
    host = settings.posthog_host.rstrip("/") or DEFAULT_HOST
    assets = host.replace(".i.posthog.com", "-assets.i.posthog.com") if host.endswith(".i.posthog.com") else host
    init = {"api_host": host, "person_profiles": "identified_only", "capture_pageview": True, "capture_pageleave": True, "autocapture": True}
    reg = {"episode_date": episode_date, "surface": "newsletter"}
    return (
        f'<script src="{assets}/static/array.js" crossorigin="anonymous"></script>\n'
        f"<script>posthog.init({json.dumps(settings.posthog_api_key)}, {json.dumps(init)});"
        f"posthog.register({json.dumps(reg)});</script>"
    )


def inject_web_snippet(html: str, snippet: str) -> str:
    if not snippet:
        return html
    marker = "</body>"
    idx = html.rfind(marker)
    if idx == -1:
        return html + "\n" + snippet
    return html[:idx] + snippet + "\n" + html[idx:]


class Analytics:
    """One instance per EpisodeRun. Thread-safe: the PostHog client queues events internally."""

    def __init__(self, settings: Settings, episode_date: str):
        self.settings = settings
        self.date = episode_date
        self.cfg: dict[str, Any] = dict(settings.load_yaml("analytics") or {})  # copy: the YAML loader caches
        self.distinct_id: str = str(self.cfg.get("distinct_id") or DEFAULT_DISTINCT_ID)
        self.group_type: str = str(self.cfg.get("group_type") or DEFAULT_GROUP_TYPE)
        self.trace_id: str = str(uuid.uuid4())
        self.flags: dict[str, Any] = {}
        self.payloads: dict[str, Any] = {}
        self._client: Any = None
        if settings.posthog_api_key and self.cfg.get("enabled", True):
            self._client = self._make_client()

    # ── setup ──────────────────────────────────────────────────────────────
    def _make_client(self) -> Any:
        try:
            from posthog import Posthog

            client = Posthog(
                project_api_key=self.settings.posthog_api_key,
                host=self.settings.posthog_host or DEFAULT_HOST,
                flush_at=20,
                flush_interval=2.0,
                max_retries=2,
                timeout=10,
                disable_geoip=True,
                feature_flags_request_timeout_seconds=5,
            )
            log.info("PostHog analytics enabled", host=self.settings.posthog_host or DEFAULT_HOST, distinct_id=self.distinct_id, trace_id=self.trace_id)
            return client
        except Exception as e:  # missing package, bad host, ...
            log.warning("PostHog disabled: could not create client", error=str(e)[:200])
            return None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _section(self, name: str, default: bool = True) -> bool:
        section = self.cfg.get(name, {})
        if isinstance(section, bool):
            return section
        return bool((section or {}).get("enabled", default))

    # ── properties shared by every event ───────────────────────────────────
    def base_properties(self) -> dict[str, Any]:
        s = self.settings
        env = os.environ
        props: dict[str, Any] = {
            "episode_date": self.date,
            "run_trace_id": self.trace_id,
            "runner": "github-actions" if env.get("GITHUB_ACTIONS") == "true" else "local",
            "trigger": env.get("GITHUB_EVENT_NAME", "local"),
            "storage": "s3" if s.uses_s3 else "local",
            "dry_run": bool(s.dry_run),
            "fresh": bool(s.fresh),
            "skip_aisle": bool(s.skip_aisle),
            "skip_publish": bool(s.skip_publish),
            "python_version": platform.python_version(),
        }
        for key, var in (("github_run_id", "GITHUB_RUN_ID"), ("github_run_attempt", "GITHUB_RUN_ATTEMPT"), ("github_ref", "GITHUB_REF_NAME"), ("github_repository", "GITHUB_REPOSITORY")):
            if env.get(var):
                props[key] = env[var]
        if env.get("GITHUB_SHA"):
            props["git_sha"] = env["GITHUB_SHA"][:12]
        for key, value in self.flags.items():
            props[f"$feature/{key}"] = value
        if not self.cfg.get("person_profiles", False):
            props["$process_person_profile"] = False
        return props

    def _groups(self) -> dict[str, str]:
        return {self.group_type: self.date}

    # ── product analytics ──────────────────────────────────────────────────
    def capture(self, event: str, **properties: Any) -> None:
        if not self._client:
            return
        try:
            self._client.capture(event=event, distinct_id=self.distinct_id, properties={**self.base_properties(), **properties}, groups=self._groups())
        except Exception as e:
            log.warning("PostHog capture failed", event=event, error=str(e)[:200])

    def identify_episode(self, **properties: Any) -> None:
        """Set properties on the `episode` group (title, status, cost) for group-level breakdowns."""
        if not self._client:
            return
        try:
            self._client.group_identify(group_type=self.group_type, group_key=self.date, properties={"date": self.date, **properties}, distinct_id=self.distinct_id)
        except Exception as e:
            log.warning("PostHog group_identify failed", error=str(e)[:200])

    # ── LLM analytics ──────────────────────────────────────────────────────
    @property
    def llm_enabled(self) -> bool:
        return self.enabled and self._section("llm_analytics")

    @property
    def send_prompts(self) -> bool:
        return bool((self.cfg.get("llm_analytics") or {}).get("send_prompts", False))

    def generation(
        self,
        agent: str,
        model: str,
        latency_s: float,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        cache_read_tokens: Optional[int] = None,
        cache_creation_tokens: Optional[int] = None,
        stop_reason: Optional[str] = None,
        http_status: Optional[int] = None,
        error: Optional[str] = None,
        model_parameters: Optional[dict[str, Any]] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        output_text: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """One `$ai_generation` event per Claude API round-trip (successful or not)."""
        if not self.llm_enabled:
            return
        props: dict[str, Any] = {
            "$ai_trace_id": self.trace_id,
            "$ai_span_id": str(uuid.uuid4()),
            "$ai_span_name": agent,
            "$ai_provider": "anthropic",
            "$ai_model": model,
            "$ai_latency": round(latency_s, 3),
            "$ai_base_url": "https://api.anthropic.com",
            "$ai_is_error": error is not None,
            "agent": agent,
            **extra,
        }
        if input_tokens is not None:
            props["$ai_input_tokens"] = input_tokens
        if output_tokens is not None:
            props["$ai_output_tokens"] = output_tokens
        if cache_read_tokens:
            props["$ai_cache_read_input_tokens"] = cache_read_tokens
        if cache_creation_tokens:
            props["$ai_cache_creation_input_tokens"] = cache_creation_tokens
        if stop_reason:
            props["$ai_stop_reason"] = stop_reason
        if http_status is not None:
            props["$ai_http_status"] = http_status
        if error is not None:
            props["$ai_error"] = error[:500]
        if model_parameters:
            props["$ai_model_parameters"] = model_parameters
        if self.send_prompts:
            if messages is not None:
                props["$ai_input"] = messages
            if output_text is not None:
                props["$ai_output_choices"] = [{"role": "assistant", "content": output_text}]
        self.capture("$ai_generation", **props)

    def span(self, name: str, latency_s: float, **properties: Any) -> None:
        """A non-LLM step (pipeline stage) inside the run's trace."""
        if not self.llm_enabled or not (self.cfg.get("llm_analytics") or {}).get("stage_spans", True):
            return
        self.capture("$ai_span", **{"$ai_trace_id": self.trace_id, "$ai_parent_id": self.trace_id, "$ai_span_id": str(uuid.uuid4()), "$ai_span_name": name, "$ai_latency": round(latency_s, 3), **properties})

    def trace(self, status: str, latency_s: float, output_state: Optional[dict[str, Any]] = None, **properties: Any) -> None:
        """The root `$ai_trace` event that ties every generation and span of this run together."""
        if not self.llm_enabled:
            return
        self.capture(
            "$ai_trace",
            **{
                "$ai_trace_id": self.trace_id,
                "$ai_span_id": self.trace_id,
                "$ai_span_name": f"episode {self.date}",
                "$ai_latency": round(latency_s, 3),
                "$ai_input_state": {"episode_date": self.date, "dry_run": bool(self.settings.dry_run), "skip_aisle": bool(self.settings.skip_aisle), "fresh": bool(self.settings.fresh)},
                "$ai_output_state": {"status": status, **(output_state or {})},
                "status": status,
                **properties,
            },
        )

    # ── error tracking ─────────────────────────────────────────────────────
    def exception(self, exc: BaseException, **properties: Any) -> None:
        if not self._client or not self._section("error_tracking"):
            return
        try:
            self._client.capture_exception(exc, distinct_id=self.distinct_id, properties={**self.base_properties(), **properties}, groups=self._groups())
        except Exception as e:
            log.warning("PostHog capture_exception failed", error=str(e)[:200])

    # ── feature flags ──────────────────────────────────────────────────────
    def load_flags(self) -> dict[str, Any]:
        """Evaluate every flag for this pipeline once (a single request). Returns {key: value}."""
        if not self._client or not self._section("feature_flags"):
            return {}
        try:
            res = self._client.get_all_flags_and_payloads(self.distinct_id, groups=self._groups())
            self.flags = dict(res.get("featureFlags") or {})
            self.payloads = {k: _parse_payload(v) for k, v in (res.get("featureFlagPayloads") or {}).items()}
            log.info("Feature flags loaded", flags=self.flags)
        except Exception as e:
            log.warning("Could not load feature flags; using defaults", error=str(e)[:200])
            self.flags, self.payloads = {}, {}
        return self.flags

    def flag_key(self, name: str) -> Optional[str]:
        """Resolve a logical flag name (e.g. `paused`) to its PostHog key from config."""
        key = (self.cfg.get("feature_flags") or {}).get(name)
        return str(key) if key else None

    def flag(self, name: str, default: Any = None) -> Any:
        key = self.flag_key(name)
        if not key or key not in self.flags:
            return default
        value = self.flags[key]
        return default if value is None else value

    def payload(self, name: str, default: Any = None) -> Any:
        key = self.flag_key(name)
        if not key or key not in self.payloads:
            return default
        return self.payloads[key]

    # ── lifecycle ──────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        """Flush the queue before the process exits (GitHub Actions kills stragglers)."""
        if not self._client:
            return
        try:
            self._client.shutdown()
        except Exception as e:
            log.warning("PostHog shutdown failed", error=str(e)[:200])
        self._client = None
