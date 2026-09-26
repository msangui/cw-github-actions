"""Ingest: fetch RSS feeds and normalize stories. Sources come from config/sources.yaml."""

from __future__ import annotations

import calendar
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

import feedparser
import httpx

from pipeline.config import Settings
from pipeline.log import get_logger

log = get_logger(stage="ingest")

_TAG_RE = re.compile(r"<[^>]+>")


def _parse_date(entry: Any) -> Optional[datetime]:
    for field in ("published_parsed", "updated_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                # feedparser returns UTC struct_time; timegm converts correctly (mktime would assume local time)
                return datetime.fromtimestamp(calendar.timegm(val), tz=timezone.utc)
            except Exception:
                pass
    return None


def clean_summary(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()[:500]


def _fetch_source(client: httpx.Client, source: dict[str, Any], cutoff_ts: float, max_entries: int, vertical: Optional[str]) -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    resp = client.get(source["url"])
    if resp.status_code != 200:
        log.warning("Non-200 from RSS source", source=source["name"], status=resp.status_code)
        return stories
    feed = feedparser.parse(resp.content)
    for entry in feed.entries[:max_entries]:
        published = _parse_date(entry)
        if published and published.timestamp() < cutoff_ts:
            continue
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        story = {
            "title": title,
            "url": entry.get("link", ""),
            "summary": clean_summary(entry.get("summary", entry.get("description", ""))),
            "published_at": published.isoformat() if published else None,
            "source_name": source.get("name", ""),
            "source_tier": str(source.get("tier", "1")),
        }
        if vertical:
            story["vertical"] = vertical
        stories.append(story)
    log.info("Fetched source", source=source["name"], total_entries=len(feed.entries), kept=len(stories))
    return stories


def fetch_stories(sources: list[dict[str, Any]], window_hours: int = 48, max_entries: int = 50, vertical: Optional[str] = None, concurrency: int = 8) -> list[dict[str, Any]]:
    active = [s for s in sources if s.get("active", True)]
    cutoff_ts = datetime.now(timezone.utc).timestamp() - window_hours * 3600
    stories: list[dict[str, Any]] = []
    headers = {"User-Agent": "ContextWindowBot/1.0 (+podcast pipeline)"}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_fetch_source, client, s, cutoff_ts, max_entries, vertical): s for s in active}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    stories.extend(fut.result())
                except Exception as e:
                    log.warning("Failed to fetch RSS source", source=src.get("url", ""), error=str(e)[:200])
    # Stable order: newest first, then by source name — makes runs reproducible
    stories.sort(key=lambda s: (s.get("published_at") or "", s.get("source_name", "")), reverse=True)
    return stories


def ingest_main(settings: Settings) -> list[dict[str, Any]]:
    cfg = settings.load_yaml("sources")
    stories = fetch_stories(cfg.get("main", []), int(cfg.get("window_hours", 48)), int(cfg.get("max_entries_per_feed", 50)))
    log.info("Main ingestion complete", total_stories=len(stories))
    return stories


def ingest_aisle(settings: Settings) -> list[dict[str, Any]]:
    cfg = settings.load_yaml("sources")
    aisle = cfg.get("aisle", {})
    if not aisle.get("enabled", True):
        return []
    stories = fetch_stories(aisle.get("sources", []), int(cfg.get("window_hours", 48)), int(cfg.get("max_entries_per_feed", 50)), vertical="cpg_retail")
    log.info("Aisle ingestion complete", total_stories=len(stories))
    return stories
