"""CFO: per-episode cost estimate, monthly aggregation in <storage>/state/costs.json,
budget thresholds. Replaces the cfo activity + episodes.cost_usd column."""

from __future__ import annotations

from datetime import date
from typing import Any

from pipeline.config import Settings
from pipeline.log import get_logger
from pipeline.storage import Storage

log = get_logger(stage="cfo")

COSTS_KEY = "state/costs.json"


class BudgetPaused(RuntimeError):
    pass


def _llm_rate(pricing: dict[str, Any], model: str | None) -> dict[str, float]:
    rates = pricing.get("llm", {})
    if model:
        for prefix, rate in rates.items():
            if prefix != "default" and model.startswith(prefix):
                return rate
    return rates.get("default", {"input": 5.0, "output": 25.0})


def estimate_cost(settings: Settings, llm_calls: list[tuple[str, str | None, dict[str, int]]], tts_chars: int, serper_queries: int) -> dict[str, Any]:
    """llm_calls: [(agent, model, {input_tokens, output_tokens}), ...]."""
    pricing = settings.load_yaml("budget").get("pricing", {})
    llm_total = 0.0
    per_agent: dict[str, float] = {}
    for agent, model, usage in llm_calls:
        rate = _llm_rate(pricing, model)
        cost = usage.get("input_tokens", 0) / 1e6 * float(rate["input"]) + usage.get("output_tokens", 0) / 1e6 * float(rate["output"])
        per_agent[agent] = round(per_agent.get(agent, 0.0) + cost, 4)
        llm_total += cost
    tts_cost = tts_chars / 1000.0 * float(pricing.get("elevenlabs_per_1k_chars", 0.33))
    serper_cost = serper_queries * float(pricing.get("serper_per_query", 0.001))
    total = llm_total + tts_cost + serper_cost
    return {
        "llm_usd": round(llm_total, 4),
        "llm_by_agent": per_agent,
        "tts_usd": round(tts_cost, 4),
        "tts_chars": tts_chars,
        "serper_usd": round(serper_cost, 4),
        "serper_queries": serper_queries,
        "total_usd": round(total, 4),
    }


def _load(storage: Storage) -> dict[str, Any]:
    return storage.get_json(COSTS_KEY) or {"episodes": {}}


def month_total(costs: dict[str, Any], month: str) -> float:
    return round(sum(float(v.get("total_usd", 0)) for d, v in costs.get("episodes", {}).items() if d.startswith(month)), 4)


def check_budget_before_run(settings: Settings, storage: Storage, today: date | None = None) -> float:
    """Raise BudgetPaused if this month's spend already exceeds the hard-pause threshold."""
    thresholds = settings.load_yaml("budget").get("thresholds", {})
    hard = float(thresholds.get("monthly_hard_pause", 145.0))
    month = (today or date.today()).strftime("%Y-%m")
    spent = month_total(_load(storage), month)
    if spent >= hard:
        msg = f"Monthly spend ${spent:.2f} >= hard pause ${hard:.2f}"
        if settings.ignore_budget:
            log.warning("Budget hard-pause overridden by CW_IGNORE_BUDGET", spent=spent)
        else:
            raise BudgetPaused(msg)
    return spent


def record_episode_cost(settings: Settings, storage: Storage, episode_date: str, cost: dict[str, Any]) -> list[str]:
    """Persist the episode cost and return human-readable alerts (may be empty)."""
    thresholds = settings.load_yaml("budget").get("thresholds", {})
    costs = _load(storage)
    costs["episodes"][episode_date] = cost
    storage.put_json(COSTS_KEY, costs, cache_control="private, no-store")

    month = episode_date[:7]
    spent = month_total(costs, month)
    alerts: list[str] = []
    daily = float(cost.get("total_usd", 0))
    if daily >= float(thresholds.get("daily_alert_breakdown", 6.0)):
        alerts.append(f"Daily spend ${daily:.2f} — LLM ${cost['llm_usd']:.2f}, TTS ${cost['tts_usd']:.2f}, Serper ${cost['serper_usd']:.2f}")
    elif daily >= float(thresholds.get("daily_alert", 5.0)):
        alerts.append(f"Daily spend ${daily:.2f} above alert threshold")

    hard = float(thresholds.get("monthly_hard_pause", 145.0))
    warn = float(thresholds.get("monthly_warn", 120.0))
    if spent >= hard:
        alerts.append(f"Monthly spend ${spent:.2f} hit hard pause (${hard:.2f}) — next run will refuse to start")
    elif spent >= warn:
        d = date.fromisoformat(episode_date)
        days_in_month = 31 if d.month in (1, 3, 5, 7, 8, 10, 12) else (30 if d.month != 2 else 29 if d.year % 4 == 0 else 28)
        projection = spent / max(d.day, 1) * days_in_month
        alerts.append(f"Monthly spend ${spent:.2f} above warn threshold; month-end projection ${projection:.2f}")
    log.info("Cost recorded", episode=episode_date, episode_usd=daily, month_usd=spent, alerts=len(alerts))
    return alerts
