# Agent configs

One YAML per agent. Replaces the `agent_configs` Postgres table from the original
Temporal implementation — edit here, commit, and the next run picks it up.

Fields:

| field | meaning |
|---|---|
| `model` | Claude model ID. Default `claude-opus-5`. |
| `max_tokens` | Output cap for the call. |
| `effort` | `low` / `medium` / `high` / `xhigh` / `max`. Controls adaptive thinking depth on Claude 4.6+ models. |
| `temperature` | Only sent for models that still accept sampling params (4.6 family and older). Ignored on Claude 5 models. |
| `system_prompt` | The system prompt, verbatim. |

The user message for each call is assembled in code (`pipeline/stages/*.py`) and
contains the run-specific data (story list, brief, script).
