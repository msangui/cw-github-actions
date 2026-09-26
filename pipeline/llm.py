"""Claude API wrapper shared by every agent.

One structured-output call per agent (no tool loop), streamed so long scripts never hit
HTTP timeouts. Prompts/models/effort come from config/agents/<key>.yaml.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import anthropic

from pipeline.config import Settings
from pipeline.log import get_logger

log = get_logger(component="llm")

# Models that reject sampling params (temperature/top_p/top_k). Newer than these only
# take `output_config.effort`. Older 4.6-and-below models still accept temperature.
_NO_SAMPLING_PREFIXES = ("claude-opus-5", "claude-sonnet-5", "claude-fable", "claude-mythos", "claude-opus-4-7", "claude-opus-4-8")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: dict[str, Any]
    raw_text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str

    @property
    def token_usage(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


def extract_json(raw_text: str) -> str:
    """Return the JSON object substring in raw_text (tolerates ```json fences and prose)."""
    stripped = raw_text.strip()
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        return match.group(0)
    raise LLMError(f"No JSON object found in LLM response. Raw text: {raw_text[:300]!r}")


def _supports_sampling(model: str) -> bool:
    return not model.startswith(_NO_SAMPLING_PREFIXES)


class LLM:
    def __init__(self, settings: Settings, log_dir: Optional[Path] = None):
        self.settings = settings
        self.log_dir = log_dir
        self._client: Optional[anthropic.Anthropic] = None

    @property
    def available(self) -> bool:
        return bool(self.settings.anthropic_api_key)

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key, max_retries=3)
        return self._client

    def call_json(
        self,
        agent_key: str,
        user_content: str,
        schema: Optional[dict[str, Any]] = None,
        system_override: Optional[str] = None,
        max_attempts: int = 3,
    ) -> LLMResult:
        """Run one agent call and return parsed JSON. Retries on transient API errors and bad JSON."""
        cfg = self.settings.agent(agent_key)
        if not cfg:
            raise LLMError(f"Missing agent config: config/agents/{agent_key}.yaml")

        model = cfg.get("model", "claude-opus-5")
        max_tokens = int(cfg.get("max_tokens", 16000))
        system_prompt = system_override or cfg.get("system_prompt", "")

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }
        output_config: dict[str, Any] = {}
        if cfg.get("effort"):
            output_config["effort"] = cfg["effort"]
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            kwargs["output_config"] = output_config
        if cfg.get("temperature") is not None and _supports_sampling(model):
            # The 1.x SDK dropped the temperature kwarg; older models (4.6 and below) still accept it on the wire.
            kwargs["extra_body"] = {"temperature": float(cfg["temperature"])}

        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            started = time.time()
            try:
                with self.client.messages.stream(**kwargs) as stream:
                    message = stream.get_final_message()
            except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
                last_error = e
                wait = min(60, 5 * 2 ** (attempt - 1))
                log.warning("Transient API error, retrying", agent=agent_key, attempt=attempt, wait_s=wait, error=str(e)[:200])
                time.sleep(wait)
                continue
            except anthropic.BadRequestError as e:
                # Structured outputs / effort may not be supported on a custom model — retry once without them.
                if output_config and attempt == 1:
                    log.warning("Bad request; retrying without output_config", agent=agent_key, error=str(e)[:200])
                    kwargs.pop("output_config", None)
                    output_config = {}
                    continue
                raise LLMError(f"{agent_key}: bad request: {e}") from e

            raw_text = "".join(block.text for block in message.content if block.type == "text")
            usage = message.usage
            duration = round(time.time() - started, 1)
            log.info(
                "LLM call complete",
                agent=agent_key,
                model=model,
                attempt=attempt,
                stop_reason=message.stop_reason,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                seconds=duration,
            )
            self._save_log(agent_key, model, user_content, raw_text, usage.input_tokens, usage.output_tokens, message.stop_reason)

            if message.stop_reason == "refusal":
                raise LLMError(f"{agent_key}: model refused the request (stop_reason=refusal)")
            if message.stop_reason == "max_tokens":
                last_error = LLMError(f"{agent_key}: output truncated at max_tokens={max_tokens}")
                log.warning("Output truncated, retrying", agent=agent_key, attempt=attempt)
                continue

            try:
                data = json.loads(extract_json(raw_text))
            except (LLMError, json.JSONDecodeError) as e:
                last_error = e
                log.warning("Unparseable JSON from model, retrying", agent=agent_key, attempt=attempt, error=str(e)[:200])
                continue

            return LLMResult(
                data=data,
                raw_text=raw_text,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                stop_reason=message.stop_reason or "",
            )

        raise LLMError(f"{agent_key}: failed after {max_attempts} attempts: {last_error}")

    def _save_log(self, agent_key: str, model: str, prompt: str, response: str, in_tok: int, out_tok: int, stop_reason: str | None) -> None:
        """Replaces the llm_logs table: one JSON file per call in the episode output dir."""
        if not self.log_dir:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            n = len(list(self.log_dir.glob(f"{agent_key}_*.json")))
            path = self.log_dir / f"{agent_key}_{n:02d}.json"
            path.write_text(
                json.dumps(
                    {
                        "agent": agent_key,
                        "model": model,
                        "stop_reason": stop_reason,
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "user_prompt": prompt,
                        "response_text": response,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as e:  # never fail the pipeline over a log file
            log.warning("Could not write LLM log", error=str(e))
