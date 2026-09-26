"""Cross-coverage: how many Google results does each headline have? (Serper). Optional."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from pipeline.config import Settings
from pipeline.log import get_logger
from pipeline.stages.curator import prescore

log = get_logger(stage="coverage")

SERPER_URL = "https://google.serper.dev/search"


def _search(client: httpx.Client, api_key: str, title: str) -> int:
    try:
        resp = client.post(SERPER_URL, headers={"X-API-KEY": api_key, "Content-Type": "application/json"}, json={"q": title, "num": 10})
        if resp.status_code != 200:
            log.warning("Serper non-200", status=resp.status_code, title=title[:60])
            return 1
        return len(resp.json().get("organic", []))
    except Exception as e:
        log.warning("Serper search failed", title=title[:60], error=str(e)[:120])
        return 1


def add_coverage(settings: Settings, stories: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Return (stories with coverage_count, number of Serper queries made)."""
    if not settings.serper_api_key:
        log.info("No SERPER_API_KEY — coverage_count defaults to 1")
        return [{**s, "coverage_count": 1} for s in stories], 0

    cfg = settings.load_yaml("curation").get("coverage", {})
    max_search = int(cfg.get("max_stories_to_search", 150))
    concurrency = int(cfg.get("concurrency", 8))

    # Only search the most promising stories (recency + tier), the rest get coverage 1
    ranked = sorted(stories, key=lambda s: prescore(s), reverse=True)
    to_search = ranked[:max_search]
    titles = {s["title"] for s in to_search}

    counts: dict[str, int] = {}
    with httpx.Client(timeout=10.0) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_search, client, settings.serper_api_key, t): t for t in titles}
            for fut in as_completed(futures):
                counts[futures[fut]] = fut.result()

    enriched = [{**s, "coverage_count": counts.get(s["title"], 1)} for s in stories]
    log.info("Coverage complete", searched=len(titles), total=len(stories))
    return enriched, len(titles)
