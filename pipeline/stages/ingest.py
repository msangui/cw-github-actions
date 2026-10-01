"""Ingest: fetch RSS/Atom feeds and normalize stories. Sources come from config/sources.yaml.

Besides the stories, every fetch records a per-source health row (HTTP status, entries, kept) so a dead
or silent feed is visible in the job summary instead of being one warning line in a long log.
"""

from __future__ import annotations

import calendar
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

import feedparser
import httpx

from pipeline.config import Settings
from pipeline.log import get_logger

log = get_logger(stage="ingest")

_TAG_RE = re.compile(r"<[^>]+>")
USER_AGENT = "ContextWindowBot/1.0 (+podcast pipeline)"

# Health rows from the most recent fetch_stories() calls, keyed by group ("main" / "aisle").
# Module state rather than a return value so the stage contract (a JSON list of stories) and the
# checkpoint shape stay unchanged.
_HEALTH: dict[str, list[dict[str, Any]]] = {}
_HEALTH_LOCK = threading.Lock()


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


def _fetch_source(client: httpx.Client, source: dict[str, Any], cutoff_ts: float, max_entries: int, vertical: Optional[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Returns (stories, health_row). Never raises for HTTP-level problems; those become health rows."""
    stories: list[dict[str, Any]] = []
    health: dict[str, Any] = {"source": source.get("name", ""), "tier": str(source.get("tier", "1")), "url": source["url"], "status": 0, "entries": 0, "kept": 0, "error": ""}
    try:
        resp = client.get(source["url"])
    except Exception as e:
        health["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        log.warning("Failed to fetch RSS source", source=source.get("name", ""), error=health["error"])
        return stories, health
    health["status"] = resp.status_code
    if resp.status_code != 200:
        log.warning("Non-200 from RSS source", source=source["name"], status=resp.status_code)
        return stories, health
    feed = feedparser.parse(resp.content)
    health["entries"] = len(feed.entries)
    if not feed.entries:  # feedparser is lenient: an HTML error page parses "fine" with zero entries
        reason = str(getattr(feed, "bozo_exception", "") or "")[:80] if feed.bozo else f"content-type {resp.headers.get('content-type', '?')[:40]}"
        health["error"] = f"no entries ({reason})"
    limit = int(source.get("max_entries", max_entries))
    for entry in feed.entries[:limit]:
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
    health["kept"] = len(stories)
    log.info("Fetched source", source=source["name"], total_entries=len(feed.entries), kept=len(stories))
    return stories, health


def fetch_stories(
    sources: list[dict[str, Any]],
    window_hours: int = 48,
    max_entries: int = 50,
    vertical: Optional[str] = None,
    concurrency: int = 8,
    group: str = "main",
    transport: Optional[httpx.BaseTransport] = None,
) -> list[dict[str, Any]]:
    active = [s for s in sources if s.get("active", True)]
    cutoff_ts = datetime.now(timezone.utc).timestamp() - window_hours * 3600
    stories: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}, transport=transport) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_fetch_source, client, s, cutoff_ts, max_entries, vertical): s for s in active}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    got, row = fut.result()
                    stories.extend(got)
                    health.append(row)
                except Exception as e:  # defensive: _fetch_source already catches network errors
                    log.warning("Source fetch crashed", source=src.get("url", ""), error=str(e)[:200])
                    health.append({"source": src.get("name", ""), "tier": str(src.get("tier", "1")), "url": src.get("url", ""), "status": 0, "entries": 0, "kept": 0, "error": str(e)[:120]})
    health.sort(key=lambda h: (h["tier"], h["source"]))
    with _HEALTH_LOCK:
        _HEALTH[group] = health
    # Stable order: newest first, then by source name — makes runs reproducible
    stories.sort(key=lambda s: (s.get("published_at") or "", s.get("source_name", "")), reverse=True)
    return stories


def ingest_main(settings: Settings) -> list[dict[str, Any]]:
    cfg = settings.load_yaml("sources")
    stories = fetch_stories(cfg.get("main", []), int(cfg.get("window_hours", 48)), int(cfg.get("max_entries_per_feed", 50)), group="main")
    log.info("Main ingestion complete", total_stories=len(stories))
    return stories


def ingest_aisle(settings: Settings) -> list[dict[str, Any]]:
    cfg = settings.load_yaml("sources")
    aisle = cfg.get("aisle", {})
    if not aisle.get("enabled", True):
        return []
    stories = fetch_stories(aisle.get("sources", []), int(cfg.get("window_hours", 48)), int(cfg.get("max_entries_per_feed", 50)), vertical="cpg_retail", group="aisle")
    log.info("Aisle ingestion complete", total_stories=len(stories))
    return stories


# ── source health ──────────────────────────────────────────────────────────────


def health_verdict(row: dict[str, Any]) -> str:
    if row.get("status") != 200:
        return "DEAD" if row.get("status") in (0, 404, 410) else f"HTTP {row.get('status')}"
    if row.get("entries", 0) == 0:
        return "EMPTY"
    if row.get("kept", 0) == 0:
        return "STALE"  # feed is alive but nothing inside the ingest window
    return "OK"


def health_report() -> list[dict[str, Any]]:
    """Health rows from the fetches performed in this process (empty if ingest came from a checkpoint)."""
    with _HEALTH_LOCK:
        rows = [{**row, "group": group, "verdict": health_verdict(row)} for group, group_rows in _HEALTH.items() for row in group_rows]
    return rows


def health_markdown(rows: list[dict[str, Any]], window_hours: int) -> str:
    if not rows:
        return "_Ingest came from a checkpoint; no feeds were fetched in this run._\n"
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    bad = [r for r in rows if r["verdict"] != "OK"]
    lines = [f"**{len(rows)} feeds** — " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())), ""]
    if bad:
        lines += ["| group | tier | source | verdict | entries | kept | note |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {r['group']} | {r['tier']} | {r['source']} | {r['verdict']} | {r['entries']} | {r['kept']} | {r.get('error', '')} |" for r in sorted(bad, key=lambda r: (r["group"], r["tier"], r["source"]))]
        lines.append("")
    lines.append(f"_kept = entries inside the {window_hours} h window. Healthy feeds are omitted from the table._")
    return "\n".join(lines) + "\n"


def tier0_problems(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tier 0 sources that delivered nothing — these are the auto-include lab feeds, so silence matters."""
    return [r for r in rows if r.get("tier") == "0" and r["verdict"] != "OK"]
